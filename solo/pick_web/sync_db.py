# -*- coding: utf-8 -*-
"""把本地 stock_picks.db 同步到云服务器（供 pick_web/app.py 只读查询）。

为什么不能直接 scp 主库：本地库是 WAL 模式，已提交的数据可能还在
stock_picks.db-wal 里，单独拷主库会拷到一个偏旧的快照。所以这里先用
SQLite 的 backup API 生成「一致性快照」，校验通过后再上传。

上传用「先传 .tmp 再原子 mv」两步：服务器上的 app.py 每次请求都会新开
连接读库，替换文件后自动生效、无需重启；而直接覆盖目标文件时，若恰好
有请求落在写入中途，会读到损坏的库。

只读取本地库、只往服务器写数据文件，不改动任何选股脚本。

同步的产物文件：
  - 复盘报告 Final_Self_<date>.html      → 远端 reports/（网页「每日复盘」），按大小比对
  - 三级行业共振 l3_resonance_<date>.json → 远端 l3res/（网页「三级行业共振」），按 MD5 比对
  - 中证2000 仓位 Gate 每日输出           → 远端 etf/（网页「ETF 每日复盘」），按 MD5 比对
       csi2000_position_gate_daily.csv / csi2000_position_gate_events.csv
       csi2000_etf_trade_plan.json

用法：
    python sync_db.py --check          # 只测连通性，不上传
    python sync_db.py --dry-run        # 只生成并校验快照，不上传
    python sync_db.py                  # 快照 + 上传 + 原子替换
    python sync_db.py --reports-only    # 只同步复盘报告 HTML
    python sync_db.py --l3res-only      # 只同步三级行业共振 JSON
    python sync_db.py --etf-only        # 只同步 ETF 每日复盘数据
    python sync_db.py --local-db <path> --remote-dir /opt/stockweb/data
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "sync_config.json")
LOG_DIR = os.path.join(BASE_DIR, "logs")
LOG_PATH = os.path.join(LOG_DIR, "sync.log")
DEFAULT_LOCAL_DB = os.path.join(os.path.dirname(BASE_DIR), "picks_db", "stock_picks.db")
DEFAULT_REPORTS_SRC = r"d:\mystock\report_daily"
DEFAULT_L3RES_SRC = os.path.join(os.path.dirname(BASE_DIR), "report_daily")
DEFAULT_ETF_SRC = os.path.join(os.path.dirname(BASE_DIR), "position_gate", "output")

SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3"]


def log(msg: str) -> None:
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def load_config(args) -> dict:
    cfg = {
        "host": "", "port": 22, "user": "", "remote_dir": "/opt/stockweb/data",
        "remote_name": "stock_picks.db", "identity_file": "",
        "strict_host_key": False, "local_db": DEFAULT_LOCAL_DB,
        "reports_src": DEFAULT_REPORTS_SRC, "reports_remote_dir": "",
        "l3res_src": DEFAULT_L3RES_SRC, "l3res_remote_dir": "",
        "etf_src": DEFAULT_ETF_SRC, "etf_remote_dir": "",
    }
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            cfg.update(json.load(fh))
    if args.local_db:
        cfg["local_db"] = args.local_db
    if args.remote_dir:
        cfg["remote_dir"] = args.remote_dir
    if getattr(args, "reports_src", None):
        cfg["reports_src"] = args.reports_src
    if getattr(args, "l3res_src", None):
        cfg["l3res_src"] = args.l3res_src
    if getattr(args, "etf_src", None):
        cfg["etf_src"] = args.etf_src
    return cfg


def ssh_base(cfg: dict) -> list:
    cmd = ["ssh"] + SSH_OPTS
    if cfg.get("strict_host_key"):
        cmd += ["-o", "StrictHostKeyChecking=yes"]
    else:
        cmd += ["-o", "StrictHostKeyChecking=accept-new"]
    if cfg.get("identity_file"):
        cmd += ["-i", cfg["identity_file"]]
    cmd += ["-p", str(cfg.get("port", 22))]
    return cmd


def scp_base(cfg: dict) -> list:
    cmd = ["scp"] + SSH_OPTS
    if cfg.get("strict_host_key"):
        cmd += ["-o", "StrictHostKeyChecking=yes"]
    else:
        cmd += ["-o", "StrictHostKeyChecking=accept-new"]
    if cfg.get("identity_file"):
        cmd += ["-i", cfg["identity_file"]]
    cmd += ["-P", str(cfg.get("port", 22))]      # scp 用大写 -P
    return cmd


def target(cfg: dict) -> str:
    return "%s@%s" % (cfg["user"], cfg["host"])


def run(cmd: list, timeout: int = 120, cwd: str | None = None,
        retries: int = 3) -> tuple[int, str]:
    """执行 ssh/scp。服务器 sshd 的 MaxStartups 会丢弃短时间内的密集新连接，
    这类报错是瞬时的，统一退避重试，避免把「报告没同步上」误当成成功。"""
    transient = ("connection closed", "connection reset", "maxstartups",
                 "broken pipe", "kex_exchange_identification",
                 "connection timed out", "connection refused")
    last = (1, "")
    for attempt in range(retries):
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd,
                               encoding="utf-8", errors="replace", timeout=timeout)
        except FileNotFoundError:
            return 127, "找不到 %s（Windows 需启用「OpenSSH 客户端」）" % cmd[0]
        except subprocess.TimeoutExpired:
            last = (124, "命令超时（%ss）" % timeout)
        else:
            out = ((p.stdout or "") + (p.stderr or "")).strip()
            if p.returncode == 0 or not any(k in out.lower() for k in transient):
                return p.returncode, out
            last = (p.returncode, out)
        if attempt < retries - 1:
            time.sleep(2 * (attempt + 1))
    return last


REPORT_PREFIX = "Final_Self_"
REPORT_SUFFIX = ".html"
REPORT_BATCH = 60


def local_reports(src_dir: str) -> list[str]:
    """本地复盘报告文件名（Final_Self_<8位日期>.html）。"""
    if not src_dir or not os.path.isdir(src_dir):
        return []
    out = []
    for name in os.listdir(src_dir):
        if not (name.startswith(REPORT_PREFIX) and name.endswith(REPORT_SUFFIX)):
            continue
        date = name[len(REPORT_PREFIX):-len(REPORT_SUFFIX)]
        if len(date) == 8 and date.isdigit():
            out.append(name)
    return sorted(out)


def sync_reports(cfg: dict, remote_dir: str) -> bool:
    """把本地复盘报告 HTML 增量同步到服务器（供网页第三个 tab 展示）。"""
    src = cfg.get("reports_src") or ""
    files = local_reports(src)
    if not src or not os.path.isdir(src):
        log("报告目录不存在，跳过报告同步：%s" % (src or "(未配置)"))
        return True
    if not files:
        log("本地无复盘报告，跳过报告同步：%s" % src)
        return True

    rdir = cfg.get("reports_remote_dir") or \
        os.path.dirname(remote_dir.rstrip("/")) + "/reports"
    tgt = target(cfg)
    ssh, scp = ssh_base(cfg), scp_base(cfg)

    rc, out = run(ssh + [tgt, "mkdir -p '%s' && cd '%s' && ls -l" % (rdir, rdir)])
    if rc != 0:
        log("报告目录创建失败（exit %s）：%s" % (rc, out[:300]))
        return False

    # 同一次连接里顺带取回远端已有文件大小，只传新增/变更的
    sizes = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[4].isdigit():
            sizes[parts[-1]] = int(parts[4])

    todo = [f for f in files
            if sizes.get(f) != os.path.getsize(os.path.join(src, f))]
    log("复盘报告：本地 %d 期，远端 %d 期，待上传 %d 期" % (len(files), len(sizes), len(todo)))

    if todo:
        for i in range(0, len(todo), REPORT_BATCH):
            batch = todo[i:i + REPORT_BATCH]
            rc, out = run(scp + batch + ["%s:%s/" % (tgt, rdir)], timeout=600, cwd=src)
            if rc != 0:
                log("报告上传失败（exit %s）：%s" % (rc, out[:300]))
                return False
        rc, out = run(ssh + [tgt, "ls '%s' | wc -l" % rdir])
        log("报告上传完成，远端现有 %s 期（%s）"
            % ((out or "?").strip().splitlines()[-1] if out else "?", rdir))
    else:
        log("报告已是最新，无需上传。")
    return True


L3RES_PREFIX = "l3_resonance_"
L3RES_SUFFIX = ".json"
L3RES_BATCH = 60


def local_l3res(src_dir: str) -> list[str]:
    """本地三级行业共振文件名（l3_resonance_<8位日期>.json）。"""
    if not src_dir or not os.path.isdir(src_dir):
        return []
    out = []
    for name in os.listdir(src_dir):
        if not (name.startswith(L3RES_PREFIX) and name.endswith(L3RES_SUFFIX)):
            continue
        date = name[len(L3RES_PREFIX):-len(L3RES_SUFFIX)]
        if len(date) == 8 and date.isdigit():
            out.append(name)
    return sorted(out)


def _md5_file(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sync_l3res(cfg: dict, remote_dir: str) -> bool:
    """把三级行业共振 JSON 增量同步到服务器（供「三级行业共振」页面展示）。"""
    src = cfg.get("l3res_src") or ""
    files = local_l3res(src)
    if not src or not os.path.isdir(src):
        log("共振目录不存在，跳过同步：%s" % (src or "(未配置)"))
        return True
    if not files:
        log("本地无共振 JSON，跳过同步：%s" % src)
        return True

    rdir = cfg.get("l3res_remote_dir") or \
        os.path.dirname(remote_dir.rstrip("/")) + "/l3res"
    tgt = target(cfg)
    ssh, scp = ssh_base(cfg), scp_base(cfg)

    rc, out = run(ssh + [tgt,
                         "mkdir -p '%s' && cd '%s' && (md5sum *.json 2>/dev/null || true)"
                         % (rdir, rdir)])
    if rc != 0:
        log("共振目录创建失败（exit %s）：%s" % (rc, out[:300]))
        return False

    # 按内容（MD5）比对：共振 JSON 每日整体重写，同尺寸不代表同内容
    remote_md5 = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and len(parts[0]) == 32:
            remote_md5[parts[1]] = parts[0]

    todo = [f for f in files
            if remote_md5.get(f) != _md5_file(os.path.join(src, f))]
    log("三级行业共振：本地 %d 期，远端 %d 期，待上传 %d 期"
        % (len(files), len(remote_md5), len(todo)))

    if todo:
        for i in range(0, len(todo), L3RES_BATCH):
            batch = todo[i:i + L3RES_BATCH]
            rc, out = run(scp + batch + ["%s:%s/" % (tgt, rdir)], timeout=600, cwd=src)
            if rc != 0:
                log("共振 JSON 上传失败（exit %s）：%s" % (rc, out[:300]))
                return False
        rc, out = run(ssh + [tgt, "ls '%s' | wc -l" % rdir])
        log("共振 JSON 上传完成，远端现有 %s 期（%s）"
            % ((out or "?").strip().splitlines()[-1] if out else "?", rdir))
    else:
        log("共振 JSON 已是最新，无需上传。")
    return True


# 中证2000 仓位 Gate 每日输出（固定文件名，每日整体重写，故按 MD5 比对）
ETF_FILES = (
    "csi2000_position_gate_daily.csv",
    "csi2000_position_gate_events.csv",
    "csi2000_etf_trade_plan.json",
)


def sync_etf(cfg: dict, remote_dir: str) -> bool:
    """把中证2000 仓位 Gate 的每日输出同步到服务器（供「ETF 每日复盘」页面展示）。

    三个文件都是固定文件名、每日整体重写，尺寸相同不代表内容相同，
    因此按内容（MD5）比对，只传真正变更的文件。
    """
    src = cfg.get("etf_src") or ""
    if not src or not os.path.isdir(src):
        log("ETF 数据目录不存在，跳过同步：%s" % (src or "(未配置)"))
        return True
    files = [f for f in ETF_FILES if os.path.exists(os.path.join(src, f))]
    if not files:
        log("本地无 ETF 数据文件，跳过同步：%s" % src)
        return True

    rdir = cfg.get("etf_remote_dir") or \
        os.path.dirname(remote_dir.rstrip("/")) + "/etf"
    tgt = target(cfg)
    ssh, scp = ssh_base(cfg), scp_base(cfg)

    rc, out = run(ssh + [tgt,
                         "mkdir -p '%s' && cd '%s' && (md5sum %s 2>/dev/null || true)"
                         % (rdir, rdir, " ".join(files))])
    if rc != 0:
        log("ETF 数据目录创建失败（exit %s）：%s" % (rc, out[:300]))
        return False

    remote_md5 = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and len(parts[0]) == 32:
            remote_md5[parts[1]] = parts[0]

    todo = [f for f in files
            if remote_md5.get(f) != _md5_file(os.path.join(src, f))]
    log("ETF 每日复盘：本地 %d 个，远端 %d 个，待上传 %d 个"
        % (len(files), len(remote_md5), len(todo)))

    if todo:
        rc, out = run(scp + todo + ["%s:%s/" % (tgt, rdir)], timeout=300, cwd=src)
        if rc != 0:
            log("ETF 数据上传失败（exit %s）：%s" % (rc, out[:300]))
            return False
        log("ETF 数据上传完成（%s）：%s" % (rdir, "、".join(todo)))
    else:
        log("ETF 数据已是最新，无需上传。")
    return True


def make_snapshot(src_db: str, dst: str) -> dict:
    """用 SQLite backup API 生成一致性快照，并校验。"""
    if not os.path.exists(src_db):
        raise SystemExit("找不到本地库：%s" % src_db)
    src = sqlite3.connect(src_db, timeout=30)
    try:
        dst_con = sqlite3.connect(dst)
        try:
            with dst_con:
                src.backup(dst_con)          # WAL 下也能拿到完整数据
        finally:
            dst_con.close()
    finally:
        src.close()

    con = sqlite3.connect(dst)
    con.isolation_level = None            # 自动提交，否则 journal_mode 切不动
    try:
        # 快照转回滚日志模式：服务端只读打开时不再生成 -wal/-shm，
        # 否则每次 mv 覆盖主库都会留下陈旧 WAL，有读到脏数据的风险。
        con.execute("pragma journal_mode=delete")
        ok = con.execute("pragma integrity_check").fetchone()[0]
        if ok != "ok":
            raise SystemExit("快照完整性校验失败：%s" % ok)
        pick = con.execute("select count(*) from stock_pick").fetchone()[0]
        track = con.execute("select count(*) from pick_tracking").fetchone()[0]
        span = con.execute("select min(pick_date), max(pick_date) from stock_pick").fetchone()
    finally:
        con.close()
    return {"pick_rows": pick, "track_rows": track,
            "date_min": span[0], "date_max": span[1],
            "size_mb": round(os.path.getsize(dst) / 1048576, 3)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="同步 stock_picks.db 到云服务器（WAL 安全）")
    ap.add_argument("--local-db", default=None, help="本地库路径（覆盖配置）")
    ap.add_argument("--remote-dir", default=None, help="服务器目录（覆盖配置）")
    ap.add_argument("--config", default=None, help="配置文件路径")
    ap.add_argument("--check", action="store_true", help="只测 SSH 连通性")
    ap.add_argument("--dry-run", action="store_true", help="只生成并校验快照，不上传")
    ap.add_argument("--keep", action="store_true", help="保留快照文件供排查")
    ap.add_argument("--reports-src", default=None, help="本地复盘报告目录（覆盖配置）")
    ap.add_argument("--no-reports", action="store_true", help="跳过复盘报告同步")
    ap.add_argument("--reports-only", action="store_true",
                    help="只同步复盘报告 HTML，不传数据库（供 tushare_quant.py 报告生成后调用）")
    ap.add_argument("--l3res-src", default=None, help="本地三级行业共振 JSON 目录（覆盖配置）")
    ap.add_argument("--no-l3res", action="store_true", help="跳过三级行业共振同步")
    ap.add_argument("--l3res-only", action="store_true", help="只同步三级行业共振 JSON，不传数据库")
    ap.add_argument("--etf-src", default=None, help="本地中证2000仓位 Gate 输出目录（覆盖配置）")
    ap.add_argument("--no-etf", action="store_true", help="跳过 ETF 每日复盘数据同步")
    ap.add_argument("--etf-only", action="store_true", help="只同步 ETF 每日复盘数据，不传数据库")
    args = ap.parse_args(argv)

    global CONFIG_PATH
    if args.config:
        CONFIG_PATH = args.config
    cfg = load_config(args)

    if not cfg.get("host") or not cfg.get("user"):
        if args.check:
            log("未配置服务器（%s 里的 host/user 为空），无法检查连通性。" % CONFIG_PATH)
            return 1
        if not args.dry_run:
            log("未配置服务器（%s 里的 host/user 为空），跳过同步。" % CONFIG_PATH)
            return 0                                   # 不阻塞主流程
        log("未配置服务器，仅执行本地快照校验（--dry-run）。")

    remote_dir = cfg["remote_dir"].rstrip("/")
    remote_name = cfg["remote_name"]
    remote_final = "%s/%s" % (remote_dir, remote_name)
    remote_tmp = "%s/.%s.tmp" % (remote_dir, remote_name)

    conn = "%s:%s" % (target(cfg), remote_dir)
    log("目标 %s  目录 %s" % (conn, remote_dir))

    if args.reports_only:
        ok = sync_reports(cfg, remote_dir)
        log("报告同步完成。" if ok else "[WARN] 报告同步未成功，请检查上方日志。")
        return 0 if ok else 1

    if args.l3res_only:
        ok = sync_l3res(cfg, remote_dir)
        log("共振同步完成。" if ok else "[WARN] 共振同步未成功，请检查上方日志。")
        return 0 if ok else 1

    if args.etf_only:
        ok = sync_etf(cfg, remote_dir)
        log("ETF 数据同步完成。" if ok else "[WARN] ETF 数据同步未成功，请检查上方日志。")
        return 0 if ok else 1

    if args.check:
        rc, out = run(ssh_base(cfg) + [target(cfg), "echo __OK__ && pwd"])
        if rc != 0:
            log("连通性检查失败（exit %s）：%s" % (rc, out[:400]))
            return 1
        log("连通性正常：%s" % out.replace("__OK__", "").strip())
        rc, out = run(ssh_base(cfg) + [target(cfg), "mkdir -p '%s' && ls -la '%s'" % (remote_dir, remote_final)])
        log("远端目录状态（exit %s）：%s" % (rc, out[:400] or "（文件尚不存在）"))
        return 0 if rc == 0 else 1

    tmp = tempfile.NamedTemporaryFile(prefix="stock_picks_", suffix=".db", delete=False)
    tmp.close()
    try:
        log("生成本地快照（backup API，WAL 安全）…")
        info = make_snapshot(cfg["local_db"], tmp.name)
        log("快照 OK：%.3f MB  信号 %d 条  跟踪 %d 条  %s~%s"
            % (info["size_mb"], info["pick_rows"], info["track_rows"],
               info["date_min"], info["date_max"]))

        if args.dry_run:
            log("--dry-run：未上传。快照保留在 %s" % tmp.name)
            args.keep = True
            return 0

        rc, out = run(ssh_base(cfg) + [target(cfg), "mkdir -p '%s'" % remote_dir])
        if rc != 0:
            log("远端目录创建失败（exit %s）：%s" % (rc, out[:400]))
            return 1

        log("上传到 %s …" % remote_tmp)
        rc, out = run(scp_base(cfg) + [tmp.name, "%s:%s" % (target(cfg), remote_tmp)])
        if rc != 0:
            log("上传失败（exit %s）：%s" % (rc, out[:400]))
            return 1
        log("上传完成，原子替换为 %s" % remote_final)
        rc, out = run(ssh_base(cfg) + [target(cfg),
                     "mv -f '%s' '%s' && ls -l '%s'"
                     % (remote_tmp, remote_final, remote_final)])
        if rc != 0:
            log("远端替换失败（exit %s）：%s" % (rc, out[:400]))
            return 1
        log("服务器已就绪：%s" % (out.split()[-1] if out else remote_final))

        # 复盘报告（tushare_quant.py 每日生成的 Final_Self_<date>.html）
        ok = True
        if args.no_reports:
            log("--no-reports：跳过复盘报告同步。")
        elif not sync_reports(cfg, remote_dir):
            ok = False
            log("[WARN] 选股库已同步，但复盘报告未同步成功，网页「复盘报告」页签可能缺最新一期。")

        # 三级行业共振 JSON（l3_resonance_scanner.py 每日生成）
        if args.no_l3res:
            log("--no-l3res：跳过三级行业共振同步。")
        elif not sync_l3res(cfg, remote_dir):
            ok = False
            log("[WARN] 三级行业共振 JSON 未同步成功，网页「三级行业共振」页签可能缺最新一期。")

        # 中证2000 仓位 Gate 每日输出（position_gate/daily.py 生成）
        if args.no_etf:
            log("--no-etf：跳过 ETF 每日复盘数据同步。")
        elif not sync_etf(cfg, remote_dir):
            ok = False
            log("[WARN] ETF 每日复盘数据未同步成功，网页「ETF 每日复盘」页可能缺最新一期。")

        log("同步完成。服务端读新连接即生效，无需重启 app.py。")
        return 0 if ok else 1
    finally:
        if os.path.exists(tmp.name):
            if args.keep:
                log("快照保留在 %s" % tmp.name)
            else:
                os.unlink(tmp.name)


if __name__ == "__main__":
    sys.exit(main())
