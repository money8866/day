# -*- coding: utf-8 -*-
"""股票研究站点总入口：登录 + 门户。

- 登录：账号（手机号或用户名）+ 密码，PBKDF2-HMAC-SHA256 校验
- 会话：HMAC 签名的 HttpOnly Cookie（默认 30 天）
- 门户：入口卡片由 entries.json 驱动，新增入口只改配置、不改代码
- 运维：操作员管理（增删 / 启停 / 重置密码）、修改自己的密码
- 单点登录：/api/auth 供 nginx auth_request 使用，保护 /picks/ 与 /news/

用法：
    python app.py                                  # 127.0.0.1:8000
    python app.py --host 0.0.0.0 --port 8030       # 本地联调
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("PORTAL_DB", os.path.join(BASE_DIR, "portal.db"))
ENTRIES_PATH = os.environ.get("PORTAL_ENTRIES", os.path.join(BASE_DIR, "entries.json"))
SECRET_PATH = os.environ.get("PORTAL_SECRET", os.path.join(BASE_DIR, "secret.key"))

CN_TZ = timezone(timedelta(hours=8))
COOKIE = "sd_sess"
SESSION_DAYS = 30
PBKDF2_ITERS = 120000
ADMIN_DEFAULT = ("admin", "123456")

_lock = threading.Lock()


# ══════════════════════════════════════════════════════════════════════
# 基础工具
# ══════════════════════════════════════════════════════════════════════
def cn_now() -> str:
    return datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")


async def read_json(req: Request) -> dict:
    """读请求体并解析 JSON。请求体非法时返回空字典，由调用方按缺参处理，
    避免客户端发来坏数据时抛出 500。"""
    try:
        body = await req.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def db() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS accounts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            account    TEXT    NOT NULL UNIQUE,
            name       TEXT    NOT NULL DEFAULT '',
            phone      TEXT    NOT NULL DEFAULT '',
            role       TEXT    NOT NULL DEFAULT 'operator',
            pwd        TEXT    NOT NULL,
            active     INTEGER NOT NULL DEFAULT 1,
            created_at TEXT    NOT NULL DEFAULT '',
            last_login TEXT    NOT NULL DEFAULT '')""")
        n = c.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        if n == 0:
            acc, pwd = ADMIN_DEFAULT
            c.execute("""INSERT INTO accounts
                         (account, name, phone, role, pwd, active, created_at)
                         VALUES (?, ?, ?, 'admin', ?, 1, ?)""",
                      (acc, "管理员", "", hash_pwd(pwd), cn_now()))
            print("[portal] 已创建初始管理员 %s / %s，请尽快修改密码" % (acc, pwd), flush=True)


# ---------------------------------------------------------------- 密码
def hash_pwd(pwd: str, salt: str | None = None, iters: int = PBKDF2_ITERS) -> str:
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pwd.encode(), salt.encode(), iters).hex()
    return "pbkdf2_sha256$%d$%s$%s" % (iters, salt, h)


def check_pwd(pwd: str, stored: str) -> bool:
    try:
        algo, iters, salt, h = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        return hmac.compare_digest(hash_pwd(pwd, salt, int(iters)), stored)
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------- 会话
def _secret() -> bytes:
    if not os.path.exists(SECRET_PATH):
        with open(SECRET_PATH, "w") as f:
            f.write(secrets.token_hex(32))
        os.chmod(SECRET_PATH, 0o600)
    with open(SECRET_PATH) as f:
        return f.read().strip().encode()


def make_token(uid: int) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(
        {"uid": uid, "exp": int(time.time()) + SESSION_DAYS * 86400}
    ).encode()).decode().rstrip("=")
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return payload + "." + sig


def read_token(token: str | None) -> int | None:
    if not token or "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    want = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, want):
        return None
    try:
        pad = "=" * (-len(payload) % 4)
        d = json.loads(base64.urlsafe_b64decode(payload + pad))
    except Exception:
        return None
    return d.get("uid") if d.get("exp", 0) > time.time() else None


def current_user(req: Request) -> sqlite3.Row | None:
    uid = read_token(req.cookies.get(COOKIE))
    if uid is None:
        return None
    with db() as c:
        row = c.execute("SELECT * FROM accounts WHERE id=? AND active=1", (uid,)).fetchone()
    return row


def public_user(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "account": row["account"], "name": row["name"],
            "phone": row["phone"], "role": row["role"],
            "last_login": row["last_login"]}


# ---------------------------------------------------------------- 入口清单
_entries_cache: dict = {"mtime": 0, "data": []}


def load_entries(role: str) -> list[dict]:
    """入口卡片来自 entries.json（按 mtime 自动热加载，改完不用重启）。"""
    try:
        mt = os.path.getmtime(ENTRIES_PATH)
    except OSError:
        return []
    if _entries_cache["mtime"] != mt:
        try:
            with open(ENTRIES_PATH, encoding="utf-8") as f:
                _entries_cache["data"] = json.load(f)
            _entries_cache["mtime"] = mt
        except (OSError, ValueError) as e:
            print("[portal] entries.json 解析失败: %s" % e, flush=True)
            return _entries_cache["data"]
    return [e for e in _entries_cache["data"]
            if not e.get("roles") or role in e["roles"]]


# ---------------------------------------------------------------- 登录限速
_fails: dict = {}


def throttle_hit(ip: str) -> int:
    now = time.time()
    rec = _fails.get(ip)
    if not rec or now - rec[1] > 300:
        _fails[ip] = [0, now]
        return 0
    rec[0] += 1
    rec[1] = now
    return rec[0]


def throttle_clear(ip: str):
    _fails.pop(ip, None)


def throttle_left(ip: str) -> int:
    rec = _fails.get(ip)
    if not rec or time.time() - rec[1] > 300:
        return 0
    return max(0, 8 - rec[0])


# ══════════════════════════════════════════════════════════════════════
# Web 层
# ══════════════════════════════════════════════════════════════════════
def build_app() -> FastAPI:
    app = FastAPI(title="登录与门户", docs_url=None, redoc_url=None)

    @app.get("/")
    def index():
        page = os.path.join(BASE_DIR, "index.html")
        if not os.path.exists(page):
            return JSONResponse({"ok": False, "error": "index.html 缺失"}, status_code=500)
        return FileResponse(page)

    # ---------------- 鉴权 ----------------
    @app.post("/api/login")
    async def api_login(req: Request, res: Response):
        ip = (req.headers.get("X-Real-IP") or (req.client.host if req.client else "-"))
        if throttle_hit(ip) >= 8:
            return JSONResponse({"ok": False, "error": "失败次数过多，请 5 分钟后再试"},
                                status_code=429)
        body = await read_json(req)
        acc = str(body.get("account") or "").strip()
        pwd = str(body.get("password") or "")
        if not acc or not pwd:
            return JSONResponse({"ok": False, "error": "请输入账号和密码"}, status_code=400)
        with db() as c:
            row = c.execute("SELECT * FROM accounts WHERE account=?", (acc,)).fetchone()
            if row is None or not check_pwd(pwd, row["pwd"]):
                return JSONResponse({"ok": False, "error": "账号或密码错误"}, status_code=401)
            if not row["active"]:
                return JSONResponse({"ok": False, "error": "该账号已停用"}, status_code=403)
            c.execute("UPDATE accounts SET last_login=? WHERE id=?", (cn_now(), row["id"]))
        throttle_clear(ip)
        res = JSONResponse({"ok": True, "user": public_user(row)})
        res.set_cookie(COOKIE, make_token(row["id"]), max_age=SESSION_DAYS * 86400,
                       httponly=True, samesite="lax", path="/")
        return res

    @app.post("/api/logout")
    def api_logout(req: Request):
        res = JSONResponse({"ok": True})
        res.delete_cookie(COOKIE, path="/")
        return res

    @app.api_route("/api/auth", methods=["GET", "POST"])
    def api_auth(req: Request):
        """供 nginx auth_request 调用：有效会话返回 204，否则 401。

        同时接受 GET / POST：不同 nginx 版本转发 auth 子请求的方式不一致。
        """
        return Response(status_code=204 if current_user(req) else 401)

    @app.get("/api/me")
    def api_me(req: Request):
        u = current_user(req)
        if u is None:
            return JSONResponse({"ok": False, "authed": False}, status_code=401)
        return {"ok": True, "authed": True, "user": public_user(u),
                "entries": load_entries(u["role"])}

    @app.post("/api/password")
    async def api_password(req: Request):
        u = current_user(req)
        if u is None:
            return JSONResponse({"ok": False, "error": "未登录"}, status_code=401)
        body = await read_json(req)
        old, new = str(body.get("old") or ""), str(body.get("new") or "")
        if not check_pwd(old, u["pwd"]):
            return JSONResponse({"ok": False, "error": "原密码不正确"}, status_code=400)
        if len(new) < 6:
            return JSONResponse({"ok": False, "error": "新密码至少 6 位"}, status_code=400)
        with db() as c:
            c.execute("UPDATE accounts SET pwd=? WHERE id=?", (hash_pwd(new), u["id"]))
        return {"ok": True}

    # ---------------- 操作员管理（仅管理员）----------------
    def require_admin(req: Request):
        u = current_user(req)
        if u is None:
            return None, JSONResponse({"ok": False, "error": "未登录"}, status_code=401)
        if u["role"] != "admin":
            return None, JSONResponse({"ok": False, "error": "需要管理员权限"}, status_code=403)
        return u, None

    @app.get("/api/users")
    def api_users(req: Request):
        u, err = require_admin(req)
        if err:
            return err
        with db() as c:
            rows = c.execute("SELECT * FROM accounts ORDER BY id").fetchall()
        return {"ok": True, "items": [public_user(r) | {"active": r["active"],
                                                        "created_at": r["created_at"]}
                                      for r in rows]}

    @app.post("/api/users")
    async def api_user_add(req: Request):
        u, err = require_admin(req)
        if err:
            return err
        b = await read_json(req)
        acc = str(b.get("account") or "").strip()
        pwd = str(b.get("password") or "")
        if not acc or len(pwd) < 6:
            return JSONResponse({"ok": False, "error": "账号必填，密码至少 6 位"}, status_code=400)
        role = "admin" if b.get("role") == "admin" else "operator"
        try:
            with db() as c:
                c.execute("""INSERT INTO accounts
                             (account, name, phone, role, pwd, active, created_at)
                             VALUES (?, ?, ?, ?, ?, 1, ?)""",
                          (acc, str(b.get("name") or "").strip(),
                           str(b.get("phone") or "").strip(), role, hash_pwd(pwd), cn_now()))
        except sqlite3.IntegrityError:
            return JSONResponse({"ok": False, "error": "该账号已存在"}, status_code=409)
        return {"ok": True}

    @app.post("/api/users/{uid}/update")
    async def api_user_update(uid: int, req: Request):
        u, err = require_admin(req)
        if err:
            return err
        b = await read_json(req)
        with db() as c:
            t = c.execute("SELECT * FROM accounts WHERE id=?", (uid,)).fetchone()
            if t is None:
                return JSONResponse({"ok": False, "error": "账号不存在"}, status_code=404)
            role = "admin" if b.get("role") == "admin" else "operator"
            active = 1 if b.get("active", 1) else 0
            if t["role"] == "admin" and (role != "admin" or not active):
                admins = c.execute("SELECT COUNT(*) FROM accounts "
                                   "WHERE role='admin' AND active=1").fetchone()[0]
                if admins <= 1:
                    return JSONResponse({"ok": False,
                                         "error": "至少要保留一个启用中的管理员"}, status_code=400)
            c.execute("""UPDATE accounts SET name=?, phone=?, role=?, active=? WHERE id=?""",
                      (str(b.get("name") or "").strip(), str(b.get("phone") or "").strip(),
                       role, active, uid))
        return {"ok": True}

    @app.post("/api/users/{uid}/password")
    async def api_user_reset(uid: int, req: Request):
        u, err = require_admin(req)
        if err:
            return err
        b = await read_json(req)
        pwd = str(b.get("password") or "")
        if len(pwd) < 6:
            return JSONResponse({"ok": False, "error": "密码至少 6 位"}, status_code=400)
        with db() as c:
            if not c.execute("SELECT 1 FROM accounts WHERE id=?", (uid,)).fetchone():
                return JSONResponse({"ok": False, "error": "账号不存在"}, status_code=404)
            c.execute("UPDATE accounts SET pwd=? WHERE id=?", (hash_pwd(pwd), uid))
        return {"ok": True}

    @app.delete("/api/users/{uid}")
    def api_user_del(uid: int, req: Request):
        u, err = require_admin(req)
        if err:
            return err
        if uid == u["id"]:
            return JSONResponse({"ok": False, "error": "不能删除自己"}, status_code=400)
        with db() as c:
            t = c.execute("SELECT * FROM accounts WHERE id=?", (uid,)).fetchone()
            if t is None:
                return JSONResponse({"ok": False, "error": "账号不存在"}, status_code=404)
            if t["role"] == "admin":
                admins = c.execute("SELECT COUNT(*) FROM accounts "
                                   "WHERE role='admin'").fetchone()[0]
                if admins <= 1:
                    return JSONResponse({"ok": False, "error": "至少要保留一个管理员"},
                                        status_code=400)
            c.execute("DELETE FROM accounts WHERE id=?", (uid,))
        return {"ok": True}

    return app


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="登录与门户服务")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args(argv)

    init_db()
    print("[portal] http://%s:%d  db=%s" % (args.host, args.port, DB_PATH), flush=True)
    import uvicorn
    uvicorn.run(build_app(), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
