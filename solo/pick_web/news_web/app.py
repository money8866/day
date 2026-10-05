# -*- coding: utf-8 -*-
"""A股「资讯流 + 市场热度」Web 服务（FastAPI，零本地数据依赖）。

数据源均为公开零鉴权 HTTP 接口，直连、不用 akshare：
  资讯流 ① 财联社电报     https://www.cls.cn/v1/roll/get_roll_list   （本地签名，无 key）
        ② 东财 7×24 快讯  https://np-weblist.eastmoney.com/...
        ③ 华尔街见闻      https://api-one-wscn.awtmt.com/apiv1/content/lives
        ④ 央视新闻联播    https://tv.cctv.com/lm/xwlb/day/{ymd}.shtml
  热度   ⑤ 同花顺热榜      https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock
        ⑥ 东财人气榜      https://emappdata.eastmoney.com/stockrank/getAllCurrentList
        ⑦ 东财个股概念    https://emappdata.eastmoney.com/stockrank/getHotStockRankList

缓存：进程内 TTL 缓存（默认 300s）；后台线程按同一周期预热，实现「盘中每 5 分钟」更新。
落盘：抓取结果同步归档到 SQLite（data/news.db）。资讯只追加新条目（按来源+链接去重）；
      热榜按周期（小时榜按小时、日榜按交易日）按股票去重，已上榜的更新为最新排名/人气，
      只有新上榜的才新增行，不会每 5 分钟堆一份重复快照。落盘失败不影响接口返回。
所有接口都返回 HTTP 200 + {"ok": bool}，源级失败不抛给前端，便于页面降级展示。

用法：
    python app.py                                  # 127.0.0.1:8002
    python app.py --host 0.0.0.0 --port 8032       # 本地联调
"""
from __future__ import annotations

import argparse
import hashlib
import html as _html
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import requests

CN_TZ = timezone(timedelta(hours=8))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

SESSION = requests.Session()
EM_MIN_INTERVAL = 1.0                 # 东财统一节流：串行 + 间隔（与 a-stock-data 一致）
_em_last = [0.0]
_em_lock = threading.Lock()


# ══════════════════════════════════════════════════════════════════════
# HTTP 基础（东财节流）
# ══════════════════════════════════════════════════════════════════════
def _throttle_em(url: str):
    """东财统一节流：串行 + 最小间隔，避免触发 IP 级风控。"""
    if "eastmoney.com" not in url:
        return
    with _em_lock:
        wait = EM_MIN_INTERVAL - (time.time() - _em_last[0])
        if wait > 0:
            time.sleep(wait)
        _em_last[0] = time.time()


def _request(method: str, url: str, headers=None, retries: int = 3, timeout: int = 12, **kw):
    """带重试的 HTTP 请求。数据源偶发 RemoteDisconnected / 超时，重试可自愈。"""
    hdr = {"User-Agent": UA}
    if headers:
        hdr.update(headers)
    last = None
    for attempt in range(retries):
        _throttle_em(url)
        try:
            r = SESSION.request(method, url, headers=hdr, timeout=timeout, **kw)
            r.raise_for_status()
            return r
        except (requests.ConnectionError, requests.Timeout) as e:
            last = e
            if attempt < retries - 1:
                time.sleep(1.0 + attempt)
    raise last


def http_get(url: str, params=None, headers=None, timeout: int = 12):
    return _request("GET", url, headers=headers, params=params, timeout=timeout)


def http_post(url: str, params=None, data=None, json=None, headers=None, timeout: int = 12):
    return _request("POST", url, headers=headers, params=params, data=data,
                    json=json, timeout=timeout)


def cn_now() -> datetime:
    return datetime.now(CN_TZ)


def prefix_of(code: str) -> str:
    """6 位代码 → SH / SZ / BJ（emappdata 要求大写前缀）。"""
    c = str(code).strip()
    if c.startswith(("60", "68", "90", "11", "13")):
        return "SH"
    if c.startswith(("43", "83", "87", "88", "92")):
        return "BJ"
    return "SZ"


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ══════════════════════════════════════════════════════════════════════
# 数据源实现
# ══════════════════════════════════════════════════════════════════════
def fetch_cls(page_size: int = 50) -> list[dict]:
    """财联社电报（v1 API + 本地签名，零 key）。"""
    page_size = max(1, min(int(page_size), 100))
    params = {"appName": "CailianpressWeb", "os": "web", "sv": "7.7.5",
              "last_time": "", "refresh_type": "1", "rn": str(page_size)}
    qs = "&".join(f"{k}={params[k]}" for k in sorted(params))
    sign = hashlib.md5(hashlib.sha1(qs.encode()).hexdigest().encode()).hexdigest()
    r = http_get(f"https://www.cls.cn/v1/roll/get_roll_list?{qs}&sign={sign}",
                 headers={"Referer": "https://www.cls.cn/"}, timeout=10)
    data = (r.json() or {}).get("data") or {}
    items = data.get("roll_data") or []
    out = []
    for it in items:
        ts = it.get("ctime")
        t = datetime.fromtimestamp(ts, CN_TZ).strftime("%Y-%m-%d %H:%M:%S") if ts else ""
        out.append({
            "time": t,
            "title": it.get("title") or it.get("brief") or "",
            "text": (it.get("content") or it.get("brief") or "").strip(),
            "url": it.get("shareurl") or "",
            "tags": [x.get("name") for x in (it.get("subjects") or []) if isinstance(x, dict)][:3],
        })
    return out


def fetch_em724(page_size: int = 50) -> list[dict]:
    """东方财富 7×24 全球财经快讯。"""
    page_size = max(1, min(int(page_size), 100))
    r = http_get("https://np-weblist.eastmoney.com/comm/web/getFastNewsList",
                 params={"client": "web", "biz": "web_724", "fastColumn": "102",
                         "sortEnd": "", "pageSize": str(page_size),
                         "req_trace": str(uuid.uuid4())},
                 headers={"Referer": "https://kuaixun.eastmoney.com/"}, timeout=10)
    items = ((r.json() or {}).get("data") or {}).get("fastNewsList") or []
    out = []
    for it in items:
        out.append({
            "time": it.get("showTime") or "",
            "title": it.get("title") or "",
            "text": (it.get("summary") or "").strip(),
            "url": it.get("url") or "",
            "tags": [it.get("stockList")] if it.get("stockList") else [],
        })
    return out


def fetch_wscn(channel: str = "a-stock-channel", limit: int = 50, cursor=None) -> dict:
    """华尔街见闻 7×24 快讯。返回 {items, next_cursor}。"""
    if not re.fullmatch(r"[a-z0-9-]+-channel", str(channel)):
        raise ValueError("channel 形如 'global-channel' / 'a-stock-channel'")
    limit = max(1, min(int(limit), 100))
    params = {"channel": channel, "limit": limit}
    if cursor:
        params["cursor"] = cursor
    r = http_get("https://api-one-wscn.awtmt.com/apiv1/content/lives",
                 params=params, timeout=12)
    payload = r.json()
    if not isinstance(payload, dict) or payload.get("code") != 20000:
        raise RuntimeError("华尔街见闻返回结构异常: %s" % str(payload)[:150])
    data = payload.get("data") or {}
    out = []
    for it in (data.get("items") or []):
        stamp = it.get("display_time")
        t = datetime.fromtimestamp(stamp, CN_TZ).strftime("%Y-%m-%d %H:%M:%S") \
            if isinstance(stamp, (int, float)) else ""
        out.append({
            "time": t,
            "title": it.get("title") or "",
            "text": (it.get("content_text") or "").strip(),
            "url": it.get("uri") or "",
            "importance": it.get("score"),
        })
    if not out:
        raise RuntimeError("华尔街见闻 %s 返回 0 条（频道名可能不存在）" % channel)
    return {"items": out, "next_cursor": data.get("next_cursor")}


def _cctv_body(url: str):
    r = http_get(url, timeout=12)
    text = r.content.decode("utf-8", "replace")
    if len(text) < 1000 and "error.html" in text:
        return None
    m = (re.search(r'<div class="content_area"[^>]*>(.*?)</div>', text, re.S)
         or re.search(r'<div class="cnt_bd"[^>]*>(.*?)</div>', text, re.S))
    if not m:
        raise RuntimeError("新闻联播正文页结构改变: %s" % url)
    body = re.sub(r"</p>|<br\s*/?>", "\n", m.group(1))
    body = _html.unescape(re.sub(r"<[^>]+>", "", body))
    body = "\n".join(x.strip() for x in body.splitlines() if x.strip())
    return re.sub(r"^央视网消息\s*[（(]新闻联播[)）]\s*[：:]", "", body)


def fetch_cctv(date: str | None = None, with_content: bool = False) -> dict:
    """央视《新闻联播》当日条目。date 为空时从今天往前找最近一期（最多 6 天）。"""
    if date:
        cands = [str(date).replace("-", "")]
    else:
        today = cn_now().date()
        cands = [(today - timedelta(days=i)).strftime("%Y%m%d") for i in range(7)]

    last_err = None
    for ymd in cands:
        if not re.fullmatch(r"\d{8}", ymd):
            raise ValueError("date 需为 YYYY-MM-DD 或 YYYYMMDD")
        url = f"https://tv.cctv.com/lm/xwlb/day/{ymd}.shtml"
        r = SESSION.get(url, headers={"User-Agent": UA}, timeout=12)
        if r.status_code == 404:
            last_err = ValueError("%s 没有新闻联播页面（日期过早或当晚约 20:00 后才更新）" % ymd)
            continue
        text = r.content.decode("utf-8", "replace")
        rows = []
        for chunk in text.split("<li")[1:]:
            link = re.search(r'href="([^"]*/VIDE[^"]+)"', chunk)
            if not link:
                continue
            href = link.group(1)
            t = (re.search(r'title="([^"]+)"', chunk)
                 or re.search(r'class="title">(.*?)</div>', chunk, re.S)
                 or re.search(r"<a[^>]*>(.*?)</a>", chunk, re.S))
            title = _html.unescape(re.sub(r"<[^>]+>", "", t.group(1))).strip() if t else ""
            if not title or re.match(r"《新闻联播》\s*\d{8}|新闻联播完整版", title):
                continue
            title = re.sub(r"^\[视频\]", "", title).strip()
            rows.append({
                "time": "%s-%s-%s" % (ymd[:4], ymd[4:6], ymd[6:]),
                "title": title,
                "text": "",
                "url": ("https:" + href) if href.startswith("//") else href,
            })
        if not rows:
            last_err = RuntimeError("新闻联播 %s 页面没有解析出条目，结构可能已变" % ymd)
            continue
        if with_content:
            for row in rows:
                row["text"] = _cctv_body(row["url"])
                time.sleep(0.2)
        return {"date": "%s-%s-%s" % (ymd[:4], ymd[4:6], ymd[6:]), "items": rows}
    raise last_err or RuntimeError("新闻联播暂无可解析期数")


def fetch_ths_hot(period: str = "hour") -> list[dict]:
    """同花顺热榜（人气值 + 概念标签 + 排名变化）。period: hour/day。"""
    if period not in ("hour", "day"):
        raise ValueError("period 只能是 'hour' 或 'day'")
    r = http_get("https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock",
                 params={"stock_type": "a", "type": period, "list_type": "normal"}, timeout=10)
    lst = ((r.json() or {}).get("data") or {}).get("stock_list") or []
    out = []
    for it in lst:
        tag = it.get("tag") or {}
        out.append({
            "rank": it.get("order"), "code": it.get("code"), "name": it.get("name"),
            "heat": it.get("rate"), "pct": it.get("rise_and_fall"),
            "rank_chg": it.get("hot_rank_chg"),
            "concepts": (tag.get("concept_tag") or [])[:4],
            "tag": tag.get("popularity_tag") or "",
        })
    return out


EM_HOT_BODY = {"appId": "appId01", "globalId": "786e4c21-70dc-435a-93bb-38"}


def _qt_quote(secids: list[str]) -> dict:
    """腾讯行情批量取「名称/现价/涨跌幅」，secids 形如 ['sz002594', ...]。

    东财 push2 对机房 IP 有间歇性拒连（RemoteDisconnected，时好时坏），
    改用腾讯 qt.gtimg.cn 这路独立的零鉴权行情源，避免整栏失败。
    返回 {6 位代码: (name, price, pct)}。
    """
    out: dict = {}
    for i in range(0, len(secids), 50):
        r = http_get("https://qt.gtimg.cn/q=" + ",".join(secids[i:i + 50]),
                     headers={"Referer": "https://gu.qq.com/"}, timeout=10)
        for ln in r.content.decode("gbk", "replace").split(";"):
            ln = ln.strip()
            if "=" not in ln:
                continue
            key, val = ln.split("=", 1)
            f = val.strip().strip('"').split("~")
            if len(f) < 33 or not f[1]:
                continue
            out[key.strip()[2:][-6:]] = (f[1], _num(f[3]), _num(f[32]))
    return out


def fetch_em_hot(top: int = 50) -> list[dict]:
    """东财人气榜（排名 + 排名变化 + 名称/价格/涨跌幅）。

    名单取自东财 emappdata，行情明细取自腾讯行情。
    """
    top = max(1, min(int(top), 200))
    r = http_post("https://emappdata.eastmoney.com/stockrank/getAllCurrentList",
                  json={**EM_HOT_BODY, "marketType": "", "pageNo": 1, "pageSize": top},
                  timeout=10)
    data = (r.json() or {}).get("data") or []
    if not data:
        return []
    mkt = {"SZ": "sz", "SH": "sh", "BJ": "bj"}
    qt = _qt_quote([mkt.get(it["sc"][:2], "sz") + it["sc"][2:]
                    for it in data if it.get("sc")])
    out = []
    for it in data:
        code = it["sc"][2:]
        name, price, pct = qt.get(code, ("", None, None))
        out.append({"rank": it.get("rk"), "code": code, "name": name,
                    "price": price, "pct": pct, "rank_chg": it.get("hisRc")})
    return out


def fetch_em_concept(code: str) -> list[dict]:
    """东财个股热门概念命中（这只票当下被市场归到哪些概念在炒）。"""
    c = re.sub(r"\D", "", str(code))[:6]
    if len(c) != 6:
        raise ValueError("code 需为 6 位股票代码")
    r = http_post("https://emappdata.eastmoney.com/stockrank/getHotStockRankList",
                  json={**EM_HOT_BODY, "srcSecurityCode": prefix_of(c) + c}, timeout=10)
    data = (r.json() or {}).get("data") or []
    return [{"concept": x.get("conceptName"), "bk": x.get("conceptId"),
             "hit": x.get("hitCount")} for x in data]


# ══════════════════════════════════════════════════════════════════════
# TTL 缓存 + 后台预热
# ══════════════════════════════════════════════════════════════════════
class Cache:
    def __init__(self, ttl: int):
        self.ttl = ttl
        self._d: dict = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            v = self._d.get(key)
        if not v:
            return None
        ts, data = v
        return data if time.time() - ts < self.ttl else None

    def put(self, key, data):
        with self._lock:
            self._d[key] = (time.time(), data)

    def age(self, key):
        with self._lock:
            v = self._d.get(key)
        return None if not v else round(time.time() - v[0], 1)


CACHE: Cache | None = None
WARM_KEYS = [("news", "cls"), ("news", "em"), ("news", "wscn"), ("news", "cctv"),
             ("hot", "ths_hour"), ("hot", "ths_day"), ("hot", "em")]


def resolve(column: str, source: str, **kw) -> dict:
    """取一栏数据（带缓存）。返回 {name, items, extra}。"""
    key = (column, source)
    cached = CACHE.get(key)
    if cached is not None:
        return cached
    t0 = time.time()
    name, items, extra = "", [], {}

    if column == "news":
        if source == "cls":
            name, items = "财联社电报", fetch_cls(kw.get("limit", 50))
        elif source == "em":
            name, items = "东财 7×24", fetch_em724(kw.get("limit", 50))
        elif source == "wscn":
            d = fetch_wscn(kw.get("channel") or "a-stock-channel", kw.get("limit", 50))
            name, items, extra = "华尔街见闻", d["items"], {"next_cursor": d["next_cursor"]}
        elif source == "cctv":
            d = fetch_cctv(kw.get("date"))
            name, items, extra = "新闻联播（%s）" % d["date"], d["items"], {"date": d["date"]}
        else:
            raise ValueError("资讯流不支持的来源: %s" % source)
    elif column == "hot":
        if source == "ths_hour":
            name, items = "同花顺热榜·小时", fetch_ths_hot("hour")
        elif source == "ths_day":
            name, items = "同花顺热榜·日", fetch_ths_hot("day")
        elif source == "em":
            name, items = "东财人气榜", fetch_em_hot(kw.get("top", 50))
        else:
            raise ValueError("市场热度不支持的来源: %s" % source)
    else:
        raise ValueError("未知栏目: %s" % column)

    data = {"name": name, "items": items, "extra": extra,
            "elapsed": round(time.time() - t0, 2)}
    persist(column, source, items)
    CACHE.put(key, data)
    return data


def warmer(stop_evt: threading.Event | None = None):
    """后台预热：每 ttl 秒刷新一次默认栏目，保证「盘中每 5 分钟」。"""
    while not (stop_evt and stop_evt.is_set()):
        for column, source in WARM_KEYS:
            try:
                resolve(column, source)
            except Exception as e:                    # 预热失败不影响服务
                print("[warm] %s/%s 失败: %s: %s" % (column, source, type(e).__name__, e),
                      flush=True)
        stop_evt and stop_evt.wait(CACHE.ttl) or time.sleep(CACHE.ttl)


# ══════════════════════════════════════════════════════════════════════
# Web 层
# ══════════════════════════════════════════════════════════════════════
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# ══════════════════════════════════════════════════════════════════════
# 持久化：SQLite 归档
# ══════════════════════════════════════════════════════════════════════
DB_PATH = os.environ.get("NEWS_DB") or os.path.join(BASE_DIR, "data", "news.db")

_DDL = """
CREATE TABLE IF NOT EXISTS news_item(
  source TEXT NOT NULL, uid TEXT NOT NULL, ts TEXT, title TEXT, text TEXT,
  url TEXT, tags TEXT, importance INTEGER, first_seen TEXT NOT NULL,
  PRIMARY KEY(source, uid));
CREATE INDEX IF NOT EXISTS ix_news_ts ON news_item(ts);
CREATE TABLE IF NOT EXISTS hot_snapshot(
  source TEXT NOT NULL, bucket TEXT NOT NULL, code TEXT NOT NULL,
  rank INTEGER, name TEXT, heat TEXT, pct REAL, rank_chg INTEGER,
  price REAL, concepts TEXT, tag TEXT, updated_at TEXT NOT NULL,
  PRIMARY KEY(source, bucket, code));
CREATE INDEX IF NOT EXISTS ix_hot_bucket ON hot_snapshot(source, bucket);
"""

_db_ready = False
_db_lock = threading.Lock()


def db_init() -> None:
    """建库建表（幂等）。"""
    global _db_ready
    with _db_lock:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        con = sqlite3.connect(DB_PATH, timeout=10)
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(_DDL)
            con.commit()
        finally:
            con.close()
        _db_ready = True


def _uid(source: str, it: dict) -> str:
    """资讯去重键：优先原始链接，缺链接时用 时间+标题+正文 的指纹。"""
    url = (it.get("url") or "").strip()
    if url:
        return url
    raw = "|".join([str(it.get("time") or ""), str(it.get("title") or ""),
                    str(it.get("text") or "")[:200]])
    return "h:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _hot_bucket(source: str) -> str:
    """热榜周期键：日榜按自然日，小时榜/人气榜按小时。"""
    now = cn_now()
    return now.strftime("%Y-%m-%d") if source == "ths_day" else now.strftime("%Y-%m-%dT%H")


def persist(column: str, source: str, items: list) -> None:
    """把一次抓取结果落盘。资讯只增不覆盖；热榜同周期同股票覆盖为最新值。"""
    if not items or not _db_ready:
        return
    now = cn_now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        con = sqlite3.connect(DB_PATH, timeout=10)
        try:
            if column == "news":
                con.executemany(
                    "INSERT OR IGNORE INTO news_item"
                    "(source,uid,ts,title,text,url,tags,importance,first_seen)"
                    " VALUES(?,?,?,?,?,?,?,?,?)",
                    [(source, _uid(source, it), it.get("time") or "",
                      it.get("title") or "", it.get("text") or "", it.get("url") or "",
                      json.dumps(it.get("tags") or [], ensure_ascii=False),
                      it.get("importance"), now) for it in items])
            elif column == "hot":
                bucket = _hot_bucket(source)
                con.executemany(
                    "INSERT INTO hot_snapshot"
                    "(source,bucket,code,rank,name,heat,pct,rank_chg,price,concepts,tag,updated_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(source,bucket,code) DO UPDATE SET"
                    " rank=excluded.rank,name=excluded.name,heat=excluded.heat,"
                    " pct=excluded.pct,rank_chg=excluded.rank_chg,price=excluded.price,"
                    " concepts=excluded.concepts,tag=excluded.tag,updated_at=excluded.updated_at",
                    [(source, bucket, str(it.get("code") or ""), it.get("rank"),
                      it.get("name"),
                      None if it.get("heat") is None else str(it.get("heat")),
                      _num(it.get("pct")), it.get("rank_chg"), _num(it.get("price")),
                      json.dumps(it.get("concepts") or [], ensure_ascii=False),
                      it.get("tag") or "", now)
                     for it in items if it.get("code")])
            else:
                return
            con.commit()
        finally:
            con.close()
    except Exception as e:                    # 落盘失败不影响接口返回
        print("[db] 落盘失败 %s/%s: %s: %s" % (column, source, type(e).__name__, e),
              flush=True)


def build_app(ttl: int, warm: bool = True):
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.responses import FileResponse, JSONResponse

    global CACHE
    CACHE = Cache(ttl)
    db_init()
    app = FastAPI(title="A股资讯与热度", docs_url="/api/docs", redoc_url=None)

    if warm:
        threading.Thread(target=warmer, daemon=True).start()

    @app.get("/")
    def index():
        page = os.path.join(BASE_DIR, "index.html")
        if not os.path.exists(page):
            raise HTTPException(500, "index.html 缺失")
        return FileResponse(page)

    @app.get("/api/feed")
    def api_feed(column: str = Query("news"), source: str = Query("cls"),
                 limit: int = Query(50, ge=1, le=100), channel: str | None = Query(None),
                 date: str | None = Query(None), top: int = Query(50, ge=1, le=200)):
        try:
            d = resolve(column, source, limit=limit, channel=channel, date=date, top=top)
        except Exception as e:
            return JSONResponse({"ok": False, "column": column, "source": source,
                                 "error": "%s: %s" % (type(e).__name__, e),
                                 "fetched_at": cn_now().strftime("%Y-%m-%d %H:%M:%S")})
        return {"ok": True, "column": column, "source": source, "name": d["name"],
                "count": len(d["items"]), "items": d["items"], "extra": d["extra"],
                "cached_age": CACHE.age((column, source)),
                "elapsed": d["elapsed"],
                "fetched_at": cn_now().strftime("%Y-%m-%d %H:%M:%S")}

    @app.get("/api/cctv_content")
    def api_cctv_content(url: str = Query(...)):
        if not url.startswith("https://tv.cctv.com/"):
            raise HTTPException(400, "只允许 tv.cctv.com 域名")
        try:
            return {"ok": True, "text": _cctv_body(url) or ""}
        except Exception as e:
            return JSONResponse({"ok": False, "error": "%s: %s" % (type(e).__name__, e)})

    @app.get("/api/concept")
    def api_concept(code: str = Query(...)):
        try:
            return {"ok": True, "code": code, "items": fetch_em_concept(code)}
        except Exception as e:
            return JSONResponse({"ok": False, "error": "%s: %s" % (type(e).__name__, e)})

    @app.get("/api/health")
    def api_health():
        keys = ["%s/%s" % (c, s) for c, s in CACHE._d.keys()]
        return {"ok": True, "ttl": CACHE.ttl,
                "cached": {k: CACHE.age(tuple(k.split("/"))) for k in keys},
                "now": cn_now().strftime("%Y-%m-%d %H:%M:%S")}

    return app


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="A股资讯流 + 市场热度 Web 服务")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8002)
    ap.add_argument("--ttl", type=int, default=int(os.environ.get("NEWS_TTL", "300")),
                    help="缓存秒数，默认 300（盘中每 5 分钟）")
    ap.add_argument("--no-warm", action="store_true", help="关闭后台预热")
    args = ap.parse_args(argv)

    print("[news] ttl=%ss  http://%s:%d" % (args.ttl, args.host, args.port), flush=True)
    import uvicorn
    uvicorn.run(build_app(args.ttl, warm=not args.no_warm),
                host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
