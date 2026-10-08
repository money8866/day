# -*- coding: utf-8 -*-
"""腾讯日线 K 线数据源（用于量能验证 + 缺口形态识别）。

三个入口同一后端，各自限流独立；单入口约 600 次后返回空 —— 属限流非封 IP。
仅支持沪深；北交所腾讯只返回 1 根日线，直接抛错。
参考实现：a-stock-data 技能 §1.3 / §1.4
"""
from __future__ import annotations

import time
from typing import Any

import requests

TENCENT_KLINE_HOSTS = [
    "https://web.ifzq.gtimg.cn",
    "https://proxy.finance.qq.com/ifzqgtimg",
    "https://ifzq.gtimg.cn",
]
_COOLDOWN = 120.0
_host_down_until: dict[str, float] = {}
_last_call: dict[str, float] = {}
_MIN_INTERVAL = 0.15

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Referer": "https://gu.qq.com/",
}


class KlineError(RuntimeError):
    pass


def get_prefix(code: str) -> str:
    """返回 sh / sz / bj。6 开头→sh，0/3 开头→sz，4/8/9→bj。"""
    c = str(code).strip().lower()
    if c[:2] in ("sh", "sz", "bj"):
        return c[:2]
    c = c.zfill(6)
    if c[0] == "6":
        return "sh"
    if c[0] in ("0", "3"):
        return "sz"
    if c[0] in ("4", "8", "9"):
        return "bj"
    return "sz"


def norm_ticker(code: str) -> str:
    c = str(code).strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            return c[2:].zfill(6)
    return c.zfill(6)


def _norm_date(d: str | None) -> str | None:
    """'YYYYMMDD' / 'YYYY-MM-DD' → 'YYYY-MM-DD'（腾讯要求带横杠）。"""
    if not d:
        return None
    s = str(d).strip().replace("/", "-")
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s


def tencent_kline(
    code: str,
    period: str = "day",
    adjust: str | None = "qfq",
    count: int = 320,
    end: str | None = None,
) -> list[dict[str, Any]]:
    """日线（默认前复权）。返回按时间升序的 dict 列表。

    每条：{date, open, close, high, low, volume(手)}
    end: 截止日 'YYYY-MM-DD' 或 'YYYYMMDD'，回溯历史时必传，否则拿到的是最新数据。
    注意：腾讯该接口没有成交额，只有成交量（手）。
    """
    if get_prefix(code) == "bj":
        raise KlineError("腾讯 K 线不支持北交所；请改用通达信盘后包")
    symbol = get_prefix(code) + norm_ticker(code)
    if adjust is None:
        adjust = "qfq"
    end_d = _norm_date(end)
    path = "/appstock/app/fqkline/get"
    param = f"{symbol},day,,,{count},{adjust}" if not end_d else \
            f"{symbol},day,,{end_d},{count},{adjust}"
    if adjust == "":
        path = "/appstock/app/kline/kline"
        param = f"{symbol},day,,,{count}" if not end_d else \
                f"{symbol},day,,{end_d},{count}"

    errors: list[str] = []
    for host in TENCENT_KLINE_HOSTS:
        if _host_down_until.get(host, 0) > time.time():
            continue
        last = _last_call.get(host)
        if last is not None:
            wait = _MIN_INTERVAL - (time.time() - last)
            if wait > 0:
                time.sleep(wait)
        _last_call[host] = time.time()
        try:
            r = requests.get(
                host + path, params={"param": param}, headers=_HEADERS, timeout=(8, 20)
            )
            if not r.text.strip():
                raise KlineError("空响应")
            payload = r.json()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{host}: {type(exc).__name__}")
            _host_down_until[host] = time.time() + _COOLDOWN
            continue
        if not isinstance(payload, dict):
            errors.append(f"{host}: 顶层非对象")
            _host_down_until[host] = time.time() + _COOLDOWN
            continue
        if payload.get("msg") == "param error":
            raise KlineError(f"参数错误: {param}")
        data = payload.get("data")
        if isinstance(data, dict) and data:
            node = data.get(symbol) or {}
            rows = node.get("qfqday") or node.get("day") or []
            bars = []
            for row in rows:
                # [date, open, close, high, low, volume, ...]
                if len(row) < 6:
                    continue
                bars.append(
                    {
                        "date": row[0],
                        "open": float(row[1]),
                        "close": float(row[2]),
                        "high": float(row[3]),
                        "low": float(row[4]),
                        "volume": float(row[5]),
                    }
                )
            if bars:
                return bars
        errors.append(f"{host}: 无数据")
        _host_down_until[host] = time.time() + _COOLDOWN

    raise KlineError("腾讯 K 线三入口均不可用（限流，稍后重试）: " + "; ".join(errors))


def prev_trading_day(date: str) -> str:
    """从东财日 K 拿最近交易日（date 格式 YYYYMMDD，返回 YYYYMMDD）。

    用于「盘后选股但当日停牌/非交易日」时回退。
    """
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": "1.000001",
        "klt": 101,
        "fqt": 1,
        "beg": "20250101",
        "end": "20500101",
        "fields1": "f1,f2,f3",
        "fields2": "f51",
    }
    try:
        r = requests.get(
            url, params=params, headers={"User-Agent": _HEADERS["User-Agent"]}, timeout=10
        )
        kl = r.json().get("data", {}).get("klines", [])
        days = [k.split(",")[0] for k in kl]
        prev = [d for d in days if d.replace("-", "") <= date]
        return prev[-1].replace("-", "") if prev else date
    except Exception:  # noqa: BLE001
        return date
