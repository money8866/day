# -*- coding: utf-8 -*-
"""
PBP 市场过滤器 + 行业/主题共振（第十三、十四节）

数据源（全部本地缓存，无网络依赖）：
  1. TDX 指数 .day 文件：上证指数(sh000001)、沪深300(sh000300)、中证1000(sh000852)
  2. SQLite daily_cache 全市场快照：涨跌比、成交额、涨停/跌停家数

输出：
  MARKET_FILTER（bull/neutral/weak/bear）
  ThemeStrength（行业5日强度排名、上涨占比、成交额变化）
"""
import os
import struct
from typing import Optional

import numpy as np
import pandas as pd

from .config import TDX_PATH, CACHE_DB_PATH, PBP_CONFIG


# ═════════════════════════════════════════════
# TDX 指数读取（复用 bts.data.parse_tdx_day_file 逻辑）
# ═════════════════════════════════════════════

def _parse_tdx_day(filepath: str) -> Optional[pd.DataFrame]:
    if not os.path.exists(filepath):
        return None
    records = []
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(32)
            if not chunk or len(chunk) < 32:
                break
            date_int = struct.unpack("<i", chunk[0:4])[0]
            open_p = struct.unpack("<i", chunk[4:8])[0] / 100.0
            high_p = struct.unpack("<i", chunk[8:12])[0] / 100.0
            low_p = struct.unpack("<i", chunk[12:16])[0] / 100.0
            close_p = struct.unpack("<i", chunk[16:20])[0] / 100.0
            records.append({
                "trade_date": str(date_int),
                "open": open_p, "high": high_p, "low": low_p, "close": close_p,
            })
    if not records:
        return None
    return pd.DataFrame(records).sort_values("trade_date").reset_index(drop=True)


_INDEX_FILES = {
    "sh": "sh000001.day",     # 上证指数
    "hs300": "sh000300.day",  # 沪深300
    "zz1000": "sh000852.day", # 中证1000（小盘代表，替代中证2000）
}

_idx_cache: dict = {}


def _load_index(key: str) -> Optional[pd.DataFrame]:
    if key not in _idx_cache:
        _idx_cache[key] = _parse_tdx_day(os.path.join(TDX_PATH, "vipdoc", "sh", "lday", _INDEX_FILES[key]))
    return _idx_cache[key]


# ═════════════════════════════════════════════
# 市场状态判定（第十三节）
# ═════════════════════════════════════════════

def _index_state(idx: pd.DataFrame, date_str: str) -> Optional[dict]:
    """单指数状态：MA20/MA60 位置 + 20日涨幅（只用 <=date 的数据）"""
    idx = idx[idx["trade_date"] <= str(date_str)].reset_index(drop=True)
    if len(idx) < 70:
        return None
    close = idx["close"]
    now = float(close.iloc[-1])
    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma60 = float(close.rolling(60).mean().iloc[-1])
    ret20 = now / float(close.iloc[-21]) - 1.0
    return {"close": now, "ma20": ma20, "ma60": ma60, "ret20": ret20}


def market_breadth(date_str: str) -> Optional[dict]:
    """全市场涨跌比/成交额/涨停跌停（SQLite daily_cache 快照，pct_chg 含义为百分比涨跌幅）"""
    import sqlite3
    try:
        conn = sqlite3.connect(CACHE_DB_PATH, timeout=10.0)
        df = pd.read_sql_query(
            "SELECT pct_chg, amount FROM daily_cache WHERE trade_date=?",
            conn, params=(str(date_str),),
        )
        conn.close()
    except Exception:
        return None
    if df.empty:
        return None
    pct = pd.to_numeric(df["pct_chg"], errors="coerce").dropna()
    if pct.empty:
        return None
    up = int((pct > 0).sum())
    down = int((pct < 0).sum())
    limit_up = int((pct >= 9.8).sum())
    limit_down = int((pct <= -9.8).sum())
    amount = float(pd.to_numeric(df["amount"], errors="coerce").sum())  # 千元
    return {
        "up": up, "down": down, "flat": int((pct == 0).sum()),
        "up_down_ratio": up / down if down > 0 else 99.0,
        "limit_up": limit_up, "limit_down": limit_down,
        "amount_yi": amount / 1e5,  # 千元 -> 亿元
        "n": int(len(pct)),
    }


def market_regime(date_str: str) -> str:
    """市场环境：bull/neutral/weak/bear

    判定规则（多指数 + 广度 + 赚钱效应综合）：
      bear: 上证跌破MA60且20日跌幅>5%，或跌停家数异常
      weak: 上证跌破MA20，或市场涨跌比<0.8
      bull: 上证/沪深300/中证1000 全部多头排列（close>MA20>MA60）且20日涨幅>3%
      neutral: 其余
    """
    sh = _index_state(_load_index("sh"), date_str) if _load_index("sh") is not None else None
    hs300 = _index_state(_load_index("hs300"), date_str) if _load_index("hs300") is not None else None
    zz = _index_state(_load_index("zz1000"), date_str) if _load_index("zz1000") is not None else None
    breadth = market_breadth(date_str)

    states = [s for s in (sh, hs300, zz) if s is not None]
    if not states:
        return "neutral"

    # bear：上证破MA60 + 深跌，或跌停家数占比异常
    if sh is not None:
        if (sh["close"] < sh["ma60"] and sh["ret20"] < -0.05) or (
            breadth and breadth["limit_down"] > 30 and breadth["limit_down"] > breadth["limit_up"] * 2
        ):
            return "bear"
    # weak：上证在MA20下方，或涨跌比恶化
    if sh is not None:
        if sh["close"] < sh["ma20"] or (breadth and breadth["up_down_ratio"] < 0.8):
            return "weak"
    # bull：三大指数全部多头排列 + 20日涨幅达标
    if len(states) == 3 and all(s["close"] > s["ma20"] > s["ma60"] for s in states) \
            and any(s["ret20"] > 0.03 for s in states):
        return "bull"
    # neutral：多数指数在MA20上方
    above = sum(1 for s in states if s["close"] > s["ma20"])
    if above >= 2:
        return "neutral"
    return "weak"


# ═════════════════════════════════════════════
# 行业/主题共振（第十四节）
# ═════════════════════════════════════════════

_theme_cache: dict = {}


def theme_strength(date_str: str, industry_map: Optional[dict] = None) -> dict:
    """行业主题强度：{industry: {rank_pct, ret5, up_ratio, amount_chg}}

    - ret5: 行业等权近5日涨幅
    - up_ratio: 当日行业上涨家数占比
    - rank_pct: 行业 ret5 在全市场行业中的分位（0=最强）
    全部使用 <=date 的缓存数据，无未来函数。
    """
    key = str(date_str)
    if key in _theme_cache:
        return _theme_cache[key]
    import sqlite3
    result = {}
    try:
        conn = sqlite3.connect(CACHE_DB_PATH, timeout=10.0)
        # 最近6个交易日全市场快照（日期降序取6行，再反转）
        dates_df = pd.read_sql_query(
            "SELECT DISTINCT trade_date FROM daily_cache WHERE trade_date<=? "
            "ORDER BY trade_date DESC LIMIT 6",
            conn, params=(key,),
        )
        dates = dates_df["trade_date"].astype(str).tolist()
        if dates:
            dates = sorted(dates)
            ph = ",".join(["?"] * len(dates))
            df = pd.read_sql_query(
                f"SELECT ts_code, trade_date, pct_chg, amount FROM daily_cache "
                f"WHERE trade_date IN ({ph})",
                conn, params=dates,
            )
            conn.close()
            if not df.empty and industry_map:
                df["industry"] = df["ts_code"].map(industry_map)
                df = df.dropna(subset=["industry"])
                df["ret1"] = pd.to_numeric(df["pct_chg"], errors="coerce") / 100.0
                df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
                df["is_up"] = df["ret1"] > 0
                today = dates[-1]
                prev_day = dates[-2] if len(dates) >= 2 else None
                # 向量化：行业×日期 透视聚合（替换逐组逐日循环，提速约 50 倍）
                pivot_ret = df.pivot_table(index="industry", columns="trade_date",
                                           values="ret1", aggfunc="mean", sort=False)
                pivot_up = df.pivot_table(index="industry", columns="trade_date",
                                          values="is_up", aggfunc="mean", sort=False)
                pivot_amt = df.pivot_table(index="industry", columns="trade_date",
                                           values="amount", aggfunc="sum", sort=False)
                pivot_n = df.pivot_table(index="industry", columns="trade_date",
                                         values="ts_code", aggfunc="size", sort=False)
                ret5_series = (1.0 + pivot_ret).prod(axis=1) - 1.0
                for ind in pivot_ret.index:
                    today_n = int(pivot_n.loc[ind, today]) if today in pivot_n.columns else 0
                    result[ind] = {
                        "ret5": float(ret5_series.loc[ind]) if ind in ret5_series.index else 0.0,
                        "up_ratio": float(pivot_up.loc[ind, today]) if today in pivot_up.columns else 0.0,
                        "amount_yi": float(pivot_amt.loc[ind, today]) / 1e5 if today in pivot_amt.columns else 0.0,
                        "amount_chg": 0.0,
                        "n": today_n,
                    }
                    if prev_day and prev_day in pivot_amt.columns:
                        amt_p = float(pivot_amt.loc[ind, prev_day])
                        amt_t = result[ind]["amount_yi"] * 1e5
                        if amt_p > 0:
                            result[ind]["amount_chg"] = amt_t / amt_p - 1.0
                # 行业5日强度分位排名
                if result:
                    inds = sorted(result, key=lambda k: -result[k]["ret5"])
                    n_ind = len(inds)
                    for i, ind in enumerate(inds):
                        result[ind]["rank"] = i + 1
                        result[ind]["rank_pct"] = i / max(1, n_ind - 1) if n_ind > 1 else 0.0
        else:
            conn.close()
    except Exception:
        pass
    _theme_cache[key] = result
    return result


def theme_score(industry: str, date_str: str, industry_map: Optional[dict] = None) -> dict:
    """个股所属行业的共振得分（0~5，对应 BREAKOUT_SCORE 的市场/行业共振 5 分）

    - 行业近5日强度 > 市场中位数：+2
    - 行业上涨家数占比 >50%：+1.5
    - 行业成交额放大：+1
    - 行业强度前10%：+0.5
    """
    ts = theme_strength(date_str, industry_map)
    if not ts or industry not in ts:
        return {"score": 0.0, "ret5": None, "up_ratio": None, "rank_pct": None, "amount_chg": None}
    t = ts[industry]
    ret5_all = [v["ret5"] for v in ts.values()]
    med = float(np.median(ret5_all)) if ret5_all else 0.0
    s = 0.0
    if t["ret5"] > med:
        s += 2.0
    if t["up_ratio"] > PBP_CONFIG["theme_up_ratio_min"]:
        s += 1.5
    if t["amount_chg"] > 0:
        s += 1.0
    if t.get("rank_pct") is not None and t["rank_pct"] <= PBP_CONFIG["extreme_theme_rank"]:
        s += 0.5
    return {
        "score": min(5.0, s),
        "ret5": t["ret5"], "up_ratio": t["up_ratio"],
        "rank_pct": t.get("rank_pct"), "amount_chg": t["amount_chg"],
        "rank": t.get("rank"),
    }
