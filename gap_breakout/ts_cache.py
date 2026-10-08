# -*- coding: utf-8 -*-
"""solo/cache_daily 本地日线缓存读取层。

数据规格（实测）
    路径    D:/mystock/solo/cache_daily/{code}.{SZ|SH|BJ}.csv
    覆盖    5541 只（沪 2327 / 深 2897 / 北 317），2025-01-02 ~ 2026-06-05
    字段    ts_code, trade_date, open, high, low, close, pre_close,
            change, pct_chg, vol, amount
    行数    约 344 个交易日/股

相对腾讯K线的优势：
    1. 含 `pre_close` 与 `amount`（腾讯K线无成交额）
    2. 含北交所（腾讯对北交所只返回 1 根日线）
    3. 一次读盘得到完整历史，无需多次网络请求 → 回测可直接用

相对不足：
    数据截止 2026-06-05，6 月之后的行情需另行补齐。
"""
from __future__ import annotations

import csv
import os
from typing import Any

CACHE_DIR = r"D:\mystock\solo\cache_daily"

# 列索引（避免每行都建 dict，344 行 × 5541 股回测时能省不少开销）
_COLS = ("ts_code", "trade_date", "open", "high", "low", "close",
         "pre_close", "change", "pct_chg", "vol", "amount")
_I_DATE, _I_OPEN, _I_HIGH, _I_LOW, _I_CLOSE = 1, 2, 3, 4, 5
_I_PREV, _I_VOL, _I_AMT, _I_PCT = 6, 9, 10, 8


def list_codes(cache_dir: str = CACHE_DIR) -> list[str]:
    """列出缓存中所有股票代码（不含交易所后缀）。"""
    if not os.path.isdir(cache_dir):
        return []
    out = []
    for fn in os.listdir(cache_dir):
        if not fn.endswith(".csv"):
            continue
        stem = fn[:-4]
        if "." not in stem:
            continue
        code = stem.split(".")[0]
        if code.isdigit() and len(code) == 6:
            out.append(code)
    return sorted(out)


def _f(s: str) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def load_bars(code: str, cache_dir: str = CACHE_DIR) -> list[list]:
    """读取单只股票日线，返回按日期升序的列表。

    每行格式（tuple 风格，索引同上）：
        [0]=ts_code [1]=date(YYYYMMDD int) [2]=open [3]=high [4]=low
        [5]=close [6]=pre_close [7]=change [8]=pct_chg [9]=vol [10]=amount
    """
    stem = str(code).split(".")[0].zfill(6)
    for suffix in ("SZ", "SH", "BJ"):
        path = os.path.join(cache_dir, f"{stem}.{suffix}.csv")
        if os.path.exists(path):
            break
    else:
        return []

    bars: list[list] = []
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if not header or "trade_date" not in header:
                return []
            for row in reader:
                if len(row) < 11:
                    continue
                try:
                    bars.append([
                        row[0], int(row[1]), _f(row[2]), _f(row[3]), _f(row[4]),
                        _f(row[5]), _f(row[6]), _f(row[7]), _f(row[8]),
                        _f(row[9]), _f(row[10]),
                    ])
                except (ValueError, TypeError):
                    continue
    except (OSError, UnicodeDecodeError):
        return []
    bars.sort(key=lambda r: r[_I_DATE])
    return bars


def load_dict(code: str, cache_dir: str = CACHE_DIR) -> list[dict[str, Any]]:
    """同 load_bars，但返回 dict 列表（便于阅读，批量回测建议用 load_bars）。"""
    keys = ("ts_code", "date", "open", "high", "low", "close",
            "pre_close", "change", "pct_chg", "vol", "amount")
    return [dict(zip(keys, r)) for r in load_bars(code, cache_dir)]


def trading_dates(cache_dir: str = CACHE_DIR,
                  sample: str = "000001") -> list[int]:
    """全库交易日历（用一只样本股的日期序列）。sample 传纯 6 位代码。"""
    bars = load_bars(sample, cache_dir)
    return [r[_I_DATE] for r in bars]


def coverage(cache_dir: str = CACHE_DIR) -> dict[str, Any]:
    """缓存覆盖概况。"""
    codes = list_codes(cache_dir)
    dates = trading_dates(cache_dir)
    return {
        "dir": cache_dir,
        "exists": os.path.isdir(cache_dir),
        "stock_count": len(codes),
        "trade_days": len(dates),
        "date_from": dates[0] if dates else None,
        "date_to": dates[-1] if dates else None,
    }


if __name__ == "__main__":
    import json

    print(json.dumps(coverage(), ensure_ascii=False, indent=2))
    b = load_bars("600519")
    if b:
        print(f"\n600519 最后 3 根：")
        for r in b[-3:]:
            print(f"  {r[1]} 开{r[2]:.2f} 高{r[3]:.2f} 低{r[4]:.2f} "
                  f"收{r[5]:.2f} 昨收{r[6]:.2f} 额{r[10]/1e4:.2f}万")
