# -*- coding: utf-8 -*-
"""全市场行情扫描 —— 「向上跳空缺口」扫描层。

信号源与涨停无关：任何股票只要当日最低价高于昨日最高价，即构成向上突破缺口。

数据源
    新浪 Market_Center  —— 全市场分页排序，一次 80 只，含 high/low/settlement(昨收)
    腾讯 qt.gtimg.cn     —— 批量快照（≤60 只/次），用于对候选做二次精确核验

注：东财 push2.eastmoney.com 在部分网络环境被拦，本模块不依赖它。
"""
from __future__ import annotations

import time
from typing import Any, Iterator

import requests

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

_SINA_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData"
)
_TENCENT_URL = "https://qt.gtimg.cn/q="

_last: dict[str, float] = {}


def _throttle(key: str, gap: float) -> None:
    last = _last.get(key, 0.0)
    w = gap - (time.time() - last)
    if w > 0:
        time.sleep(w)
    _last[key] = time.time()


def _f(v: Any) -> float:
    try:
        f = float(v)
        return f if f == f else 0.0
    except (TypeError, ValueError):
        return 0.0


def iter_sina(node: str = "hs_a", sort: str = "changepercent",
              total: int = 6000) -> Iterator[dict[str, Any]]:
    """流式遍历全市场 A 股。node: hs_a 沪深A股 / hs_bjs 京城不重要。"""
    num = 80
    pages = (min(total, 6000) + num - 1) // num
    for page in range(1, pages + 1):
        _throttle("sina", 0.35)
        try:
            r = requests.get(
                _SINA_URL,
                params={"page": page, "num": num, "sort": sort,
                        "asc": 0, "node": node, "_s_r_a": "page"},
                headers={"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"},
                timeout=15,
            )
            rows = r.json()
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] 新浪第{page}页失败: {type(exc).__name__}")
            return
        if not rows:
            return
        for x in rows:
            name = x.get("name") or ""
            if any(t in name for t in ("ST", "退", "*")):
                continue
            high, low, prev = _f(x.get("high")), _f(x.get("low")), _f(x.get("settlement"))
            close, open_ = _f(x.get("trade")), _f(x.get("open"))
            if not (high and low and prev and close):
                continue
            yield {
                "code": x.get("code"),
                "symbol": x.get("symbol"),
                "name": name,
                "close": close,
                "open": open_,
                "high": high,
                "low": low,
                "prev_close": prev,
                "pct": _f(x.get("changepercent")),
                "volume": _f(x.get("volume")),          # 股
                "amount": _f(x.get("amount")),          # 元（实测确认）
                "turnover": _f(x.get("turnoverratio")),
                "float_cap": _f(x.get("nmc")) * 1e4,   # 流通市值（元，nmc单位是万元）
            }


def tencent_snapshot(codes: list[str]) -> dict[str, dict[str, Any]]:
    """腾讯批量快照（≤60 只/次）。codes 传 'sh600592' 形式。

    字段：现价/昨收/今开/最高/最低/量比/换手/成交额(万)
    """
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(codes), 60):
        chunk = codes[i:i + 60]
        _throttle("tencent", 0.4)
        try:
            r = requests.get(_TENCENT_URL + ",".join(chunk),
                             headers={"User-Agent": UA}, timeout=15)
            r.encoding = "gbk"
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] 腾讯快照失败: {type(exc).__name__}")
            continue
        for line in r.text.split(";"):
            if "=" not in line:
                continue
            body = line.split("=", 1)[1].strip().strip('"')
            f = body.split("~")
            if len(f) < 35:
                continue
            try:
                out[f[2]] = {
                    "code": f[2],
                    "name": f[1],
                    "close": _f(f[3]),
                    "prev_close": _f(f[4]),
                    "open": _f(f[5]),
                    "volume": _f(f[6]),               # 手
                    "high": _f(f[33]),
                    "low": _f(f[34]),
                    "turnover": _f(f[38]),
                    "vol_ratio": _f(f[49]) if len(f) > 49 else 0.0,
                    "amount_yi": round(_f(f[37]) / 1e4, 2) if len(f) > 37 else 0.0,
                }
            except (IndexError, ValueError):
                continue
    return out


def scan_up_gaps(min_gap_pct: float = 1.0, min_amount_yi: float = 1.0,
                 max_candidates: int = 200) -> list[dict[str, Any]]:
    """全市场粗筛：找出可能有向上突破缺口的候选。

    为什么不直接在这里判定缺口：
        新浪快照只有「昨收 settlement」和当日 high/low，拿不到**昨日最高价**。
        而昨日最高价才是缺口的正确基准（昨收 ≤ 昨高，用昨收会系统性低估缺口，
        导致当天明明跳空的股票被漏掉）。所以本层只做粗筛，
        真正的缺口判定与量化在 gap_trade.verify_gap() 用日K线完成。

    粗筛条件（尽量宽松，宁可多留）：
        当日最低价 > 昨收            → 整天运行在昨收之上，具备缺口雏形
        或 当日开盘 > 昨收 1%        → 开盘跳空
    """
    out: list[dict[str, Any]] = []
    for q in iter_sina():
        if q["amount"] < min_amount_yi * 1e8:
            continue
        if q["float_cap"] and q["float_cap"] < 20e8:   # 流通市值 < 20 亿，太小易操纵
            continue
        above_prev = q["low"] > q["prev_close"]
        gap_open = (q["open"] - q["prev_close"]) / q["prev_close"] * 100
        if not (above_prev or gap_open >= 1.0):
            continue
        item = dict(q)
        item["amount_yi"] = round(q["amount"] / 1e8, 2)
        item["float_cap_yi"] = round(q["float_cap"] / 1e8, 2)
        item["open_gap_pct"] = round(gap_open, 2)
        out.append(item)
        if len(out) >= max_candidates:
            break
    return out
