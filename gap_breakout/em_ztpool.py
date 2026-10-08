# -*- coding: utf-8 -*-
"""东财涨停池 / 炸板池数据源。

免登录、零鉴权。基于 push2ex.eastmoney.com 涨停板行情中心四池。

关键字段（选股直接要用）：
    code/name        标的
    price            最新价
    pct              涨跌幅 %
    amount           成交额（元）
    float_cap        流通市值（元）
    turnover         换手率 %
    limit_days       连板数（1=首板，2=二板 ...）
    first_seal/last_seal  首次/最后封板时间 HH:MM:SS
    seal_fund        封板资金（元）→ 用于算封单量/成交额比
    break_times      炸板次数
    industry         所属行业/概念（板块共振判别）
    zt_stat          N天M板

参考实现：a-stock-data 技能 §8.1/§8.2/§8.3
"""
from __future__ import annotations

import time
from typing import Any

import requests

ZTB_UT = "7eea3edcaed734bea9cbfc24409ed989"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

BASE = "https://push2ex.eastmoney.com"

# 单域名约 600 次后返回空 JSON —— 限流保护
_HOST_DOWN_UNTIL: dict[str, float] = {}
_COOLDOWN = 60.0
_last_call: dict[str, float] = {}
_MIN_INTERVAL = 0.12


class FetchError(RuntimeError):
    pass


def _sleep(url: str) -> None:
    """同 host 最小间隔 + 冷却跳过。"""
    host = url.split("/")[2]
    if _HOST_DOWN_UNTIL.get(host, 0) > time.time():
        raise FetchError(f"{host} 处于限流冷却中")
    last = _last_call.get(host)
    if last is not None:
        wait = _MIN_INTERVAL - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
    _last_call[host] = time.time()


def _em_get(url: str, params: dict[str, Any], timeout: int = 10) -> dict:
    host = url.split("/")[2]
    for attempt in range(2):
        _sleep(url)
        try:
            r = requests.get(
                url,
                params=params,
                headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"},
                timeout=timeout,
            )
            if r.status_code != 200:
                raise FetchError(f"HTTP {r.status_code}")
            return r.json()
        except Exception as exc:  # noqa: BLE001
            if attempt == 0:
                _HOST_DOWN_UNTIL[host] = time.time() + _COOLDOWN
                time.sleep(1.0)
                continue
            raise FetchError(f"{type(exc).__name__}: {exc}") from exc
    raise FetchError("unreachable")


def _fmt_zt_time(t: Any) -> str:
    """涨停时间整数 → HH:MM:SS。

    东财 fbt/lbt 实测存在 4~6 位不定长（92500 → 09:25:00；
    极端情形下会出现 2500 这类 4 位值）。按「补足 6 位后取末 6 位」处理，
    避免 zfill 把 4 位值补成 00:25:00 这类错误时间。
    """
    try:
        n = int(t)
    except (TypeError, ValueError):
        return "00:00:00"
    if n <= 0:
        return "00:00:00"
    s = str(n)[-6:].zfill(6)
    return f"{s[0:2]}:{s[2:4]}:{s[4:6]}"


def _pool(endpoint: str, sort: str, date: str) -> list[dict]:
    url = f"{BASE}/{endpoint}"
    params = {
        "ut": ZTB_UT,
        "dpt": "wz.ztzt",
        "Pageindex": 0,
        "pagesize": 10000,
        "sort": sort,
        "date": date,
    }
    payload = _em_get(url, params)
    data = (payload or {}).get("data") or {}
    return data.get("pool") or []


def em_zt_pool(date: str) -> list[dict]:
    """涨停池。date=YYYYMMDD（交易日）。data 为 null 即非交易日。"""
    out = []
    for p in _pool("getTopicZTPool", "fbt:asc", date):
        zttj = p.get("zttj") or {}
        out.append(
            {
                "code": p["c"],
                "name": p["n"],
                "price": p["p"] / 1000,
                "pct": round(p["zdp"], 2),
                "amount": p.get("amount", 0),
                "float_cap": p.get("ltsz", 0),
                "turnover": round(p.get("hs", 0), 2),
                "limit_days": p.get("lbc", 1),
                "first_seal": _fmt_zt_time(p.get("fbt", 0)),
                "last_seal": _fmt_zt_time(p.get("lbt", 0)),
                "seal_fund": p.get("fund", 0),
                "break_times": p.get("zbc", 0),
                "industry": p.get("hybk", ""),
                "zt_stat": f"{zttj.get('days', '?')}天{zttj.get('ct', '?')}板",
            }
        )
    return out


def em_zb_pool(date: str) -> list[dict]:
    """炸板池（涨停后开板）。"""
    out = []
    for p in _pool("getTopicZBPool", "fbt:asc", date):
        zttj = p.get("zttj") or {}
        out.append(
            {
                "code": p["c"],
                "name": p["n"],
                "price": p["p"] / 1000,
                "limit_price": p.get("ztp", 0) / 1000,
                "pct": round(p.get("zdp", 0), 2),
                "turnover": round(p.get("hs", 0), 2),
                "first_seal": _fmt_zt_time(p.get("fbt", 0)),
                "break_times": p.get("zbc", 0),
                "amplitude": round(p.get("zf", 0), 2),
                "industry": p.get("hybk", ""),
                "zt_stat": f"{zttj.get('days', '?')}天{zttj.get('ct', '?')}板",
            }
        )
    return out


def em_dt_pool(date: str) -> list[dict]:
    """跌停池。"""
    out = []
    for p in _pool("getTopicDTPool", "fund:asc", date):
        out.append(
            {
                "code": p["c"],
                "name": p["n"],
                "price": p["p"] / 1000,
                "pct": round(p.get("zdp", 0), 2),
                "seal_fund": p.get("fund", 0),
                "dt_days": p.get("days", 0),
                "open_times": p.get("op", 0),
                "industry": p.get("hybk", ""),
            }
        )
    return out


def em_ztj_pool(date: str) -> list[dict]:
    """昨涨停池（昨日涨停股今日表现 → 晋级率 / 赚钱效应）。"""
    out = []
    for p in _pool("getYesterdayZTPool", "fbt:asc", date):
        out.append(
            {
                "code": p["c"],
                "name": p["n"],
                "price": p["p"] / 1000,
                "pct": round(p.get("zdp", 0), 2),
                "y_limit_days": p.get("lbc", 0),
                "zt_stat": (p.get("zttj") or {}).get("ct", 0),
                "industry": p.get("hybk", ""),
            }
        )
    return out


def limit_up_sentiment(date: str) -> dict[str, Any]:
    """打板情绪温度计：炸板率 / 连板高度 / 连板梯队 / 晋级率。

    选股的总开关 —— 决定当天是否允许出手。
    """
    zt = em_zt_pool(date)
    zb = em_zb_pool(date)
    dt = em_dt_pool(date)
    ztj = em_ztj_pool(date)

    seal_cnt = len(zt)
    break_cnt = len(zb)
    denom = seal_cnt + break_cnt
    break_rate = round(break_cnt / denom * 100, 1) if denom else 0.0

    ladder: dict[int, int] = {}
    for s in zt:
        k = s["limit_days"]
        ladder[k] = ladder.get(k, 0) + 1
    max_height = max(ladder) if ladder else 0

    # 昨日涨停股今日表现 → 赚钱效应
    up = sum(1 for s in ztj if s["pct"] > 0)
    nxt_rate = round(up / len(ztj) * 100, 1) if ztj else 0.0
    avg_pct = round(sum(s["pct"] for s in ztj) / len(ztj), 2) if ztj else 0.0
    nuke = sum(1 for s in ztj if s["pct"] <= -9.5)  # 核按钮

    return {
        "date": date,
        "seal_cnt": seal_cnt,
        "break_cnt": break_cnt,
        "dt_count": len(dt),
        "break_rate": break_rate,
        "max_height": max_height,
        "ladder": dict(sorted(ladder.items())),
        "y_zt_cnt": len(ztj),
        "y_zt_up_rate": nxt_rate,
        "y_zt_avg_pct": avg_pct,
        "nuke_cnt": nuke,
        "is_trading_day": bool(seal_cnt or break_cnt or len(dt)),
    }
