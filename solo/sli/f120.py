# -*- coding: utf-8 -*-
"""F120 V1.1 — A股中报TOP5 × T+1择时 × T+60~120中长线趋势引擎.

输入: output/sli_v2_subsector_top5_{DATE}.csv
数据: output/sli_full_{DATE}.csv + cache/daily_*.parquet + index_daily(沪深300)
输出: output/f120_result_{DATE}.csv + output/f120_report_{DATE}.md

评分: F20% P20% E25% T20% V15%，HARD GATE + BUY SETUP + T+1 TRIGGER 决定买卖。
"""
from __future__ import annotations

import glob
import math
import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from sli.config import CACHE_DIR  # noqa: E402

DATE = "20260901"
OUT = os.path.join(_HERE, "output")
LOOKBACK = 165  # 交易日回看窗口（覆盖MA120+平台）
NAN = float("nan")
_RESOLVED = False


def resolve_date(override: str | None = None) -> str:
    """基本面快照日期回退: 每日运行时自动取最近一期快照(同时存在 top5+full); 显式 override 优先."""
    global DATE, _RESOLVED
    if override:
        DATE = override
        _RESOLVED = True
        return DATE
    if _RESOLVED:
        return DATE
    tops = {os.path.basename(p)[len("sli_v2_subsector_top5_"):-4]
            for p in glob.glob(os.path.join(OUT, "sli_v2_subsector_top5_????????.csv"))}
    fulls = {os.path.basename(p)[len("sli_full_"):-4]
             for p in glob.glob(os.path.join(OUT, "sli_full_????????.csv"))}
    both = sorted(tops & fulls)
    if both:
        DATE = both[-1]
    _RESOLVED = True
    return DATE


def latest_mkt_date() -> str:
    """最新市场交易日 = cache 中最新一根 daily parquet 的日期(无则回退快照 DATE)."""
    fs = sorted(glob.glob(os.path.join(CACHE_DIR, "daily_????????.parquet")))
    return os.path.basename(fs[-1])[len("daily_"):-len(".parquet")] if fs else DATE


def _f(x, default=NAN):
    try:
        x = float(x)
        return x
    except (TypeError, ValueError):
        return default


def _nan(x):
    return x is None or (isinstance(x, float) and math.isnan(x))


# ---------------------------------------------------------------- 数据加载
def load_top5() -> pd.DataFrame:
    resolve_date()
    df = pd.read_csv(os.path.join(OUT, f"sli_v2_subsector_top5_{DATE}.csv"), low_memory=False)
    df = df.rename(columns={"三级行业": "l3", "细分赛道": "sub_t5", "排名": "rank5",
                            "代码": "ts_code", "名称": "name5", "SLI_V2": "sli_v2_t5",
                            "Product": "product", "Purity": "purity_t5",
                            "龙头类型": "leader_type", "Dominance": "dominance",
                            "生命周期": "lifecycle_t5"})
    return df


FULL_COLS = ["ts_code", "name", "close", "ret20", "ret60", "ret120", "pe_ttm", "total_mv",
             "or_yoy", "netprofit_yoy", "dt_netprofit_yoy", "q_profit_yoy",
             "g1", "g2", "g3", "roe_dt", "grossprofit_margin", "netprofit_margin",
             "ocf_to_profit", "lifecycle", "EARNINGS_ACCELERATION", "EARNINGS_TURN",
             "subsector", "sli_v2"]


def load_full() -> pd.DataFrame:
    resolve_date()
    df = pd.read_csv(os.path.join(OUT, f"sli_full_{DATE}.csv"), low_memory=False)
    cols = [c for c in FULL_COLS if c in df.columns]
    return df[cols].copy()


def load_panel(codes, days=LOOKBACK) -> pd.DataFrame:
    files = sorted(glob.glob(os.path.join(CACHE_DIR, "daily_????????.parquet")))[-days:]
    frames = []
    for fp in files:
        d = pd.read_parquet(fp, columns=["ts_code", "trade_date", "close", "pre_close",
                                         "pct_chg", "vol", "amount", "high", "low"])
        d = d[d["ts_code"].isin(codes)]
        if len(d):
            frames.append(d)
    panel = pd.concat(frames, ignore_index=True)
    panel = panel.dropna(subset=["close"])
    panel = panel.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    return panel


def ensure_index() -> pd.DataFrame:
    fp = os.path.join(CACHE_DIR, "index_daily_000300.SH.parquet")
    upto = latest_mkt_date()
    try:
        idx = pd.read_parquet(fp).sort_values("trade_date")
        idx = idx[idx["trade_date"] <= upto]
        if idx["trade_date"].iloc[-1] >= upto:
            return idx
        from sli.cache import SliCache
        from sli.datasource import DataSource
        from sli.utils import load_token
        ds = DataSource(load_token(), SliCache(CACHE_DIR))
        # 注意: ds.get_index_daily 的缓存键无日期界, 会返回≤72h的陈旧副本截短指数;
        # 每日运行必须拿全量至 upto, 故直接走原始 call 并本地覆盖引擎 parquet.
        new = ds.call("index_daily", ts_code="000300.SH", start_date="20250101",
                      end_date=upto, fields="ts_code,trade_date,close")
        if new is not None and len(new) > 100:
            new = new.drop_duplicates("trade_date").sort_values("trade_date")
            new.to_parquet(fp, index=False)
            return new[new["trade_date"] <= upto]
    except Exception as e:  # noqa: BLE001
        print(f"[warn] index refresh failed: {e}")
    return idx[idx["trade_date"] <= upto]


# ---------------------------------------------------------------- 复权序列
def build_series(g: pd.DataFrame):
    """逐日锚定的复权价格序列: 末日=真实收盘, 历史按 pct_chg 链复算(消除除权跳空)."""
    g = g.drop_duplicates("trade_date").sort_values("trade_date").reset_index(drop=True)
    n = len(g)
    if n < 30:
        return None
    adjr = (g["pct_chg"].fillna(0.0) / 100.0).clip(-0.21, 0.21)
    cum = (1.0 + adjr).cumprod()
    px_close = g["close"].astype(float) * (cum / cum.iloc[-1])
    px_high = g["high"].astype(float) * px_close / g["close"].astype(float)
    px_low = g["low"].astype(float) * px_close / g["close"].astype(float)
    return pd.DataFrame({
        "trade_date": g["trade_date"].values,
        "px": px_close.values, "hi": px_high.values, "lo": px_low.values,
        "vol": g["vol"].astype(float).values, "amt": g["amount"].astype(float).values,
        "pct": g["pct_chg"].astype(float).values, "close_raw": g["close"].astype(float).values,
    })


# ---------------------------------------------------------------- 评分: F (V1.1: ROE质量25 + 现金流25 + 主业纯度20 + 竞争地位30)
_DOM_MAP = {"DOMINANT": 95.0, "STRONG_LEADER": 85.0, "COMPETITIVE": 68.0, "FRAGMENTED": 48.0}


def _roe_tier(x: float) -> float:
    if _nan(x):
        return 50.0
    if x >= 8:
        return 88.0
    if x >= 5:
        return 78.0
    if x >= 3:
        return 66.0
    if x >= 1.5:
        return 56.0
    if x >= 0.5:
        return 46.0
    if x >= 0:
        return 38.0
    return 15.0


def _ocf_tier(x) -> float:
    if _nan(x):
        return 48.0
    if x >= 150:
        return 90.0
    if x >= 100:
        return 86.0
    if x >= 70:
        return 80.0
    if x >= 40:
        return 70.0
    if x >= 15:
        return 56.0
    if x >= 0:
        return 42.0
    return 15.0


def _purity_tier(x) -> float:
    if _nan(x):
        return 55.0
    if x >= 90:
        return 88.0
    if x >= 82.5:
        return 74.0
    if x >= 70:
        return 62.0
    if x >= 50:
        return 48.0
    return 32.0


def score_f(r) -> tuple[float, str]:
    roe = _f(r.get("roe_dt"))
    ocf = _f(r.get("ocf_to_profit"))
    pur = _f(r.get("purity_t5"))
    dom = str(r.get("dominance") or "").upper().strip()
    rk = _f(r.get("rank5"), 3.0)
    dt = _f(r.get("dt_netprofit_yoy"), _f(r.get("netprofit_yoy"), 0.0))

    s1 = _roe_tier(roe)
    med = _f(r.get("roe_pool_med"))
    if not _nan(med) and not _nan(roe):
        rel = roe - med
        if rel >= 2:
            s1 = min(95.0, s1 + 8)
        elif rel >= 0:
            s1 = min(92.0, s1 + 3)
        elif rel >= -2:
            s1 = max(20.0, s1 - 3)
        else:
            s1 = max(15.0, s1 - 8)
    if not _nan(roe) and roe >= 5 and not _nan(ocf) and ocf < 20:
        s1 = min(s1, 62.0)

    s2 = _ocf_tier(ocf)
    if dt > 40 and not _nan(ocf):
        if ocf < 0:
            s2 = max(10.0, s2 - 14)
        elif ocf < 20:
            s2 = max(15.0, s2 - 8)

    s3 = _purity_tier(pur)

    s4 = _DOM_MAP.get(dom, 62.0)
    if not _nan(rk):
        s4 += {1.0: 4.0, 2.0: 2.0, 3.0: 0.0, 4.0: -2.0}.get(rk, -4.0)
    s4 = float(np.clip(s4, 0, 100))

    f = s1 * 0.25 + s2 * 0.25 + s3 * 0.20 + s4 * 0.30
    lvl = "非常强" if f >= 85 else "较强" if f >= 75 else "有亮点" if f >= 65 else "弱"
    return f, lvl


# ---------------------------------------------------------------- 评分: P (V1.1: 长期位置40 + 中期位置35 + 回撤/买点距离25)
def score_p(cur: float, tr: dict, sd: dict) -> tuple[float, str]:
    r120 = tr.get("r120")
    m120, m20, m60 = tr["ma120"], tr["ma20"], tr["ma60"]
    dd60 = sd.get("dd60", NAN)
    setup = sd.get("setup")

    if _nan(r120):
        s_r = 55.0
    elif r120 > 1.0:
        s_r = 76.0
    elif r120 >= 0.5:
        s_r = 88.0
    elif r120 >= 0.25:
        s_r = 78.0
    elif r120 >= 0.10:
        s_r = 70.0
    elif r120 >= 0:
        s_r = 62.0
    elif r120 >= -0.15:
        s_r = 48.0
    else:
        s_r = 35.0
    if not _nan(m120) and not _nan(cur):
        if cur >= m120 * 1.05:
            s_ma = 90.0
        elif cur >= m120:
            s_ma = 80.0
        elif cur >= m120 * 0.93:
            s_ma = 62.0
        else:
            s_ma = 40.0
    else:
        s_ma = 60.0
    p1 = s_r * 0.50 + s_ma * 0.50

    if not _nan(m20) and not _nan(m60) and not _nan(cur):
        if m20 > m60 and cur > m20:
            base = 88.0
        elif cur > m20 and cur > m60:
            base = 80.0
        elif cur > m60:
            base = 65.0
        elif cur > m20:
            base = 60.0
        else:
            base = 42.0
        dist20 = cur / m20 - 1
        if dist20 > 0.20:
            base -= 10.0
        elif -0.03 <= dist20 <= 0.06:
            base += 5.0
    else:
        base = 60.0
    p2 = float(np.clip(base, 0, 100))

    if _nan(dd60):
        s_dd = 60.0
    elif dd60 >= -0.04:
        s_dd = 80.0
    elif dd60 >= -0.10:
        s_dd = 90.0
    elif dd60 >= -0.18:
        s_dd = 75.0
    elif dd60 >= -0.28:
        s_dd = 58.0
    else:
        s_dd = 42.0
    if setup:
        s_dd = min(100.0, s_dd + 6.0)
    p3 = s_dd

    p = p1 * 0.40 + p2 * 0.35 + p3 * 0.25
    dist20v = (cur / m20 - 1) if not _nan(m20) and not _nan(cur) else NAN
    if not _nan(dist20v) and dist20v > 0.20:
        tag = "偏高-追高风险"
    elif not _nan(dd60) and dd60 >= -0.04:
        tag = "近高点/突破区"
    elif not _nan(dd60) and dd60 >= -0.12:
        tag = "健康回调区"
    elif not _nan(dd60) and dd60 >= -0.25:
        tag = "深度回调区"
    else:
        tag = "深度回撤/低位"
    if not _nan(m120) and not _nan(cur) and cur < m120:
        tag = "长期趋势下方; " + tag
    return p, tag


# ---------------------------------------------------------------- 评分: E (V1.1: 营收20 + 净利40 + 加速/预期差40, 极端增长边际递减)
_DT_X = [-100, -50, -20, 0, 20, 40, 80, 150, 300]
_DT_Y = [8, 25, 38, 50, 70, 82, 92, 97, 100]


def _growth_level(x: float) -> float:
    if _nan(x):
        return 50.0
    return float(np.interp(max(min(x, 300.0), -100.0), _DT_X, _DT_Y))


def score_e(r, r20: float, r60: float, r120: float) -> tuple[float, str]:
    orr = _f(r.get("or_yoy"), 0.0)
    dt = _f(r.get("dt_netprofit_yoy"))
    if _nan(dt):
        dt = _f(r.get("netprofit_yoy"), 0.0)
    g2 = _f(r.get("g2"))
    q2 = _f(r.get("g3"))
    if _nan(q2):
        q2 = _f(r.get("q_profit_yoy"))
    pe = _f(r.get("pe_ttm"))

    if orr >= 40:
        s_e1 = 92.0
    elif orr >= 25:
        s_e1 = 84.0
    elif orr >= 15:
        s_e1 = 74.0
    elif orr >= 8:
        s_e1 = 64.0
    elif orr >= 0:
        s_e1 = 52.0
    else:
        s_e1 = 28.0

    s_e2 = 0.60 * _growth_level(dt) + 0.40 * _growth_level(q2)

    if _nan(g2) and _nan(q2):
        acc = 60.0
    elif _nan(g2) or _nan(q2):
        acc = 62.0
    else:
        d = q2 - g2
        if d >= 40:
            acc = 95.0
        elif d >= 15:
            acc = 85.0
        elif d >= 0:
            acc = 72.0
        elif d >= -15:
            acc = 58.0
        else:
            acc = 40.0
        if q2 < 10 and g2 < 10:
            acc = min(acc, 68.0)

    growth = 0.62 * min(max(dt, 0), 150) / 150 + 0.38 * min(max(orr, 0), 60) / 60
    price = 0.55 * min(max(r120, 0), 130) / 130 + 0.45 * min(max(r60, 0), 90) / 90
    raw = (growth - price) * 100
    if raw >= 35:
        gap_adj, tag = 12.0, "HIGH GAP"
    elif raw >= 12:
        gap_adj, tag = 4.0, "NORMAL GAP"
    elif raw >= -10:
        gap_adj, tag = 0.0, "LOW GAP"
    else:
        gap_adj, tag = -12.0, "OVERPRICED"
    if not _nan(pe) and pe > 0:
        peg = pe / max(dt, 5.0)
        if peg < 0.35:
            peg_adj = 6.0
        elif peg < 0.7:
            peg_adj = 3.0
        elif peg < 1.6:
            peg_adj = 0.0
        elif peg < 2.6:
            peg_adj = -6.0
        else:
            peg_adj = -10.0
    else:
        peg_adj = -4.0

    s_e3 = float(np.clip(0.55 * acc + 0.45 * (50.0 + gap_adj + peg_adj), 0, 100))
    if not _nan(g2) and not _nan(q2):
        if q2 - g2 >= 40:
            tag += "|Q2大幅加速"
        elif q2 - g2 <= -15:
            tag += "|Q2减速"

    e = s_e1 * 0.20 + s_e2 * 0.40 + s_e3 * 0.40
    return float(np.clip(e, 0, 100)), tag


# ---------------------------------------------------------------- 评分: T
def trend_of(px: np.ndarray) -> dict:
    s = pd.Series(px)
    n = len(s)
    ma20 = s.rolling(20, min_periods=20).mean()
    ma60 = s.rolling(60, min_periods=60).mean()
    ma120 = s.rolling(120, min_periods=100).mean()
    c = s.iloc[-1]
    m20 = ma20.iloc[-1] if not _nan(ma20.iloc[-1]) else NAN
    m60 = ma60.iloc[-1] if n >= 60 and not _nan(ma60.iloc[-1]) else NAN
    m120 = ma120.iloc[-1] if not _nan(ma120.iloc[-1]) else NAN
    s20 = (ma20.iloc[-1] / ma20.iloc[-6] - 1) if n >= 26 and not _nan(ma20.iloc[-6]) else NAN
    s60 = (ma60.iloc[-1] / ma60.iloc[-21] - 1) if n >= 81 and not _nan(ma60.iloc[-21]) else NAN
    r20 = c / s.iloc[-21] - 1 if n >= 21 else NAN
    r60 = c / s.iloc[-61] - 1 if n >= 61 else NAN
    r120 = c / s.iloc[-121] - 1 if n >= 121 else NAN

    if not _nan(m60) and not _nan(s60):
        if c > m20 > m60 and s60 > 0.008:
            t, lbl = 95.0, "T4 STRONG UP"
        elif c > m20 and (not _nan(s20) and s20 > 0.002) and s60 >= -0.005:
            t, lbl = 82.0, "T3 UP"
        elif c > m60 and s60 > 0.003:
            t, lbl = 66.0, "T2+ 回升"
        elif c > m20 and m20 < m60:
            t, lbl = 56.0, "T2 结构混合"
        elif abs(_nan_guard(s20)) < 0.006 and abs(s60) < 0.004 and (not _nan(m120) and c > m120 * 0.97):
            t, lbl = 62.0, "T2 平台"
        else:
            t, lbl = 28.0, "T1 DOWN"
    else:
        if c > m20:
            t, lbl = 60.0, "T2 次新(数据不足)"
        else:
            t, lbl = 40.0, "T2- 次新走弱"
    # 过热修正
    if not _nan(m20) and c / m20 > 1.16:
        t -= 8
    if not _nan(r20) and r20 > 0.38:
        t -= 5
    if not _nan(m120) and not _nan(s60) and c < m120 and s60 < 0:
        t = min(t, 45)
    return {"score": float(np.clip(t, 0, 100)), "label": lbl, "ma20": m20, "ma60": m60,
            "ma120": m120, "s20": s20, "s60": s60, "r20": r20, "r60": r60, "r120": r120,
            "c": c}


def _nan_guard(x):
    return NAN if _nan(x) else x


# ---------------------------------------------------------------- 评分: V
def volume_of(df: pd.DataFrame, tr: dict) -> dict:
    n = len(df)
    v = df["vol"].values
    pct = df["pct"].values
    v20 = pd.Series(v).rolling(20, min_periods=10).mean().values

    seg = slice(max(0, n - 20), n)
    up_v = np.mean([v[i] for i in range(n - 20, n) if pct[i] > 1.2]) if any(pct[i] > 1.2 for i in range(max(0, n - 20), n)) else 0
    dn_v = np.mean([v[i] for i in range(n - 20, n) if pct[i] < -1.2]) if any(pct[i] < -1.2 for i in range(max(0, n - 20), n)) else 0
    if up_v > 0 and dn_v > 0:
        ud = up_v / dn_v
        s1 = 100 if ud >= 1.5 else 88 if ud >= 1.2 else 75 if ud >= 1.0 else 60 if ud >= 0.85 else 42
        ud_note = "上涨放量>回调" if ud >= 1.2 else ("量能均衡" if ud >= 0.95 else "下跌量大于上涨量")
    else:
        ud, s1, ud_note = NAN, 65, "近期单边量能"

    shrink = np.mean(v[n - 5:]) / (np.mean(v[n - 20:]) or 1)
    s2 = 90 if 0.55 <= shrink <= 0.9 else 78 if shrink <= 1.1 else 65 if shrink <= 1.4 else 50

    shock = 0
    for i in range(max(0, n - 10), n):
        if pct[i] < -4.5 and v20[i - 1] > 0 and v[i] > 2.3 * v20[i - 1]:
            shock += 1
    s3 = max(30.0, 100 - 25 * shock)

    score = s1 * 0.45 + s2 * 0.30 + s3 * 0.25
    notes = [ud_note]
    r20 = tr.get("r20") or 0
    if r20 > 0 and shrink > 1.45 and n >= 6 and (df["px"].iloc[-1] / df["px"].iloc[-6] - 1) < 0.02:
        score -= 12
        notes.append("滞涨+放量 DISTRIBUTION RISK")
    big_vol_flat = sum(1 for i in range(max(0, n - 5), n)
                       if v20[i - 1] > 0 and v[i] > 3.5 * v20[i - 1] and abs(pct[i]) < 2)
    if big_vol_flat >= 2:
        score -= 6
        notes.append("突破后持续巨量且价格滞涨(警惕兑现)")
    if shock:
        notes.append(f"近10日放量下跌x{shock}")
    return {"score": float(np.clip(score, 0, 100)), "shock": shock, "shrink": float(shrink),
            "ud": ud, "notes": "; ".join(notes)}


# ---------------------------------------------------------------- 买点识别
def detect_setup(df: pd.DataFrame, tr: dict) -> dict:
    px = df["px"].values
    hi = df["hi"].values
    lo = df["lo"].values
    vol = df["vol"].values
    pct = df["pct"].values
    n = len(px)
    c = px[-1]
    v20 = pd.Series(vol).rolling(20, min_periods=10).mean().values

    hi20 = px[n - 20:].max()
    hi60 = px[n - min(60, n):].max()
    hi120 = px[n - min(120, n):].max()
    dd120 = c / hi120 - 1
    dd60 = c / hi60 - 1
    m20, m60 = tr["ma20"], tr["ma60"]

    seg = px[max(0, n - 120):max(0, n - 25)]
    if len(seg) >= 40:
        plat_top, plat_bot = float(seg.max()), float(seg.min())
        plat_rng = (plat_top - plat_bot) / plat_bot if plat_bot > 0 else NAN
    else:
        plat_top = plat_bot = plat_rng = NAN

    # ---- 突破日扫描(近30日): 收盘创前120日新高+放量1.8x
    bidx = blevel = None
    for i in range(max(0, n - 30), n - 1):
        prior = px[max(0, i - 120):i]
        if len(prior) >= 40 and px[i] > prior.max() * 1.015 and v20[i - 1] > 0 and vol[i] > 1.8 * v20[i - 1]:
            bidx, blevel = i, float(prior.max())
            break

    setup, stage = None, None
    detail = {}
    if bidx is not None:
        post = px[bidx + 1:]
        postv = vol[bidx + 1:]
        post_lo = float(post.min()) if len(post) else c
        pull_days = n - 1 - bidx
        has_retest = bool(np.any((px[bidx + 1:] <= blevel * 1.035) & (vol[bidx + 1:] < vol[bidx] * 0.65)))
        no_break = post_lo > blevel * 0.97
        renewed = c >= max(post_lo * 1.015, blevel * 0.995)
        detail.update(bl=blevel, bdate=df["trade_date"].iloc[bidx], bvol_x=float(vol[bidx] / v20[bidx - 1]),
                      post_lo=post_lo, pull_days=pull_days)
        if 4 <= pull_days <= 30 and no_break and has_retest and renewed:
            setup, stage = "BREAKOUT_RETEST", "ready"
        elif 1 <= pull_days <= 5 and c > blevel * 1.01:
            setup, stage = "BASE_BREAKOUT", "wait"

    if setup is None and not _nan(m60) and not _nan(tr["s60"]) and tr["s60"] > 0.005:
        # FIRST_PULLBACK: 主升(60日内任一20日段涨幅>18%) 后首次回调至MA20
        win = px[n - min(60, n):]
        if len(win) >= 40:
            roll = pd.Series(win)
            max_leg = float((roll.rolling(20).max() / roll.rolling(20).min() - 1).max()) if len(roll) >= 20 else 0
        else:
            max_leg = 0
        near_m20 = not _nan(m20) and m20 * 0.94 <= c <= m20 * 1.05
        shrinking = np.mean(vol[n - 5:]) < np.mean(vol[n - min(60, n):]) * 0.92
        stabil = px[-1] >= px[n - 8:].min() * 1.005 and pct[-1] > -1.5
        if -0.16 <= dd60 <= -0.03 and max_leg >= 0.18 and near_m20 and shrinking:
            if stabil and c > m20 * 0.96:
                setup, stage = "FIRST_PULLBACK", "ready"
            else:
                setup, stage = "FIRST_PULLBACK", "wait"

    if setup is None and dd120 <= -0.14 and dd120 >= -0.33:
        # DEEP_PULLBACK: 接近MA60/平台+量能收缩+止跌确认
        sup = m60 if not _nan(m60) else plat_top
        near_sup = not _nan(sup) and sup * 0.93 <= c <= sup * 1.06
        vol_shrink = np.mean(vol[n - 5:]) < np.mean(vol[n - min(60, n):]) * 0.72
        if near_sup and vol_shrink:
            confirm = (px[-3:].min() > px[n - 12:].min() * 0.995) and (pct[-1] > 0.3 or pct[-2] > 1.5)
            setup, stage = "DEEP_PULLBACK", ("ready" if confirm else "wait")

    return {"setup": setup, "stage": stage, "hi20": hi20, "hi60": hi60, "hi120": hi120,
            "dd120": dd120, "dd60": dd60, "plat_top": plat_top, "plat_bot": plat_bot,
            "plat_rng": plat_rng, **detail}


# ---------------------------------------------------------------- 关键位
def levels_of(setup: str, sd: dict, tr: dict, cur: float) -> dict:
    m20, m60 = tr["ma20"], tr["ma60"]
    if setup == "BREAKOUT_RETEST":
        bl = sd.get("bl")
        ideal = bl
        zone_lo, zone_hi = bl * 0.98, bl * 1.03
        stop = min(sd.get("post_lo", bl) * 0.97, bl * 0.96)
        h = (sd.get("plat_top", NAN) - sd.get("plat_bot", NAN))
        target = bl + max(min(h if not _nan(h) else bl * 0.15, bl * 0.35), bl * 0.15)
        trigger = f"回踩突破位{bl:.2f}缩量不破后转强(已确认)"
    elif setup == "BASE_BREAKOUT":
        bl = sd.get("bl")
        ideal = bl
        zone_lo, zone_hi = bl * 0.99, bl * 1.04
        stop = bl * 0.96
        h = (sd.get("plat_top", NAN) - sd.get("plat_bot", NAN))
        target = bl + max(min(h if not _nan(h) else bl * 0.15, bl * 0.35), bl * 0.15)
        trigger = f"等待回踩{bl:.2f}(±2%)缩量企稳; 有效跌破=放弃"
    elif setup == "FIRST_PULLBACK":
        ideal = m20 * 0.99
        zone_lo, zone_hi = m20 * 0.955, m20 * 1.03
        stop = m20 * 0.94
        target = max(sd["hi60"] * 1.03, ideal * 1.22)
        trigger = "MA20附近缩量企稳(不有效跌破MA20*0.96)后买入"
    else:  # DEEP_PULLBACK
        sup = m60 if not _nan(m60) else sd.get("plat_top", cur)
        ideal = sup
        zone_lo, zone_hi = sup * 0.96, sup * 1.05
        stop = sup * 0.945
        target = max(ideal * 1.25, sd.get("hi120", ideal) * 0.98)
        trigger = "支撑区止跌确认(3日不创新低+放量阳线站回支撑)"
    stop = min(stop, zone_lo * 0.985)
    target = min(target, ideal * 1.80)
    rr = (target - ideal) / (ideal - stop) if ideal > stop else 0
    return {"ideal": ideal, "zone_lo": zone_lo, "zone_hi": zone_hi, "stop": stop,
            "ceiling": zone_hi * 1.03, "target": target, "rr": float(rr) if rr > 0 else 0.0,
            "trigger": trigger}

# ---------------------------------------------------------------- 分类
def classify(f, p, e, t, v, setup, stage, rr, cur, ceiling, shock, tr) -> tuple[str, str]:
    r20 = tr.get("r20") or 0
    overpriced = (e < 35 and r120_g(tr) > 0.6)
    if f < 65 or p < 60 or t <= 35 or shock >= 2 or overpriced:
        return "AVOID", avoid_reason(f, p, t, shock, overpriced)
    g1_ok = f >= 75 and p >= 70
    g2_ok = t >= 60
    g3_ok = v >= 60
    if not (g1_ok and g2_ok and g3_ok):
        miss = [nm for nm, ok in (("G1基本面", g1_ok), ("G2趋势", g2_ok), ("G3量价", g3_ok)) if not ok]
        return ("WATCH", "HARD GATE未过: " + ",".join(miss)) if f >= 75 and p >= 70 else ("WATCH", "基本面/趋势未达可交易门槛")

    if setup in ("BREAKOUT_RETEST", "FIRST_PULLBACK") and stage == "ready":
        if f >= 80 and p >= 75 and e >= 65 and t >= 70 and v >= 65 and rr >= 2.0 and cur <= ceiling:
            return "PRIMARY BUY", "GATE全过+买点确认+R:R达标"
        if f >= 80 and p >= 75 and e >= 60:
            why = "价格已高于Chase Ceiling" if cur > ceiling else ("R:R不足2.0" if rr < 2.0 else "评分未满PRIMARY线")
            return "CONDITIONAL BUY", f"买点已确认但{why}; Trigger={setup}"
    if setup is not None and f >= 80 and p >= 75 and e >= 60:
        if stage == "ready":
            return "CONDITIONAL BUY", f"{setup}结构已确认(该类型评级上限CONDITIONAL); 按BUY ZONE执行"
        return "CONDITIONAL BUY", f"等待{setup}触发"
    if f >= 75 and p >= 70:
        if setup is not None:
            if stage == "ready":
                return "CONDITIONAL BUY", f"{setup}买点已确认但评分未满线(未达80/75/60); 按BUY ZONE执行"
            return "CONDITIONAL BUY", "GATE过但评分线不足, 等待更强结构"
        return "WATCH", "基本面达标但无T+1买点(等回调/等突破/等止跌)"
    return "WATCH", "基本面未达WATCH线但未触AVOID"


def r120_g(tr):
    x = tr.get("r120")
    return 0 if _nan(x) else x


def avoid_reason(f, p, t, shock, overpriced) -> str:
    rs = []
    if f < 65:
        rs.append("中报质量弱(F<65)")
    if p < 60:
        rs.append("价格位置不利(P<60)")
    if t <= 35:
        rs.append("明确下降趋势(T1)")
    if shock >= 2:
        rs.append("近期放量破位")
    if overpriced:
        rs.append("股价严重透支基本面")
    return "; ".join(rs)


# ---------------------------------------------------------------- 市场状态
def market_state(idx: pd.DataFrame) -> dict:
    c = idx["close"].astype(float).reset_index(drop=True)
    ma20 = c.rolling(20).mean()
    ma60 = c.rolling(60).mean()
    ma120 = c.rolling(120, min_periods=100).mean()
    cc = c.iloc[-1]
    m20, m60, m120 = ma20.iloc[-1], ma60.iloc[-1], ma120.iloc[-1]
    s60 = ma60.iloc[-1] / ma60.iloc[-21] - 1
    r20 = cc / c.iloc[-21] - 1
    r60 = cc / c.iloc[-61] - 1
    if cc < m20 and m20 < m60 and (not _nan(m120) and cc < m120):
        st, pos = "BEAR", "CASH优先"
    elif cc < m20 and m20 < m60:
        st, pos = "WEAK", "只做最强基本面+最强技术结构"
    elif cc > m20 > m60 and s60 > 0.008:
        st, pos = "STRONG", "正常执行"
    elif cc > m60 and s60 > 0:
        st, pos = "RECOVERY", "正常执行但仓位降低"
    else:
        st, pos = "RANGE", "重点做PULLBACK, 减少追突破"
    cap = {"STRONG": 1.0, "RECOVERY": 0.75, "RANGE": 0.6, "WEAK": 0.4, "BEAR": 0.15}[st]
    risk = {"STRONG": "中", "RECOVERY": "中", "RANGE": "中低", "WEAK": "低", "BEAR": "极低(防守)"}[st]
    return {"state": st, "cap": cap, "strategy": pos, "risk": risk,
            "close": cc, "ma20": m20, "ma60": m60, "ma120": m120,
            "r20": r20, "r60": r60, "last_date": idx["trade_date"].iloc[-1]}


# ---------------------------------------------------------------- 主流程
def run():
    top5 = load_top5()
    full = load_full()
    codes = top5["ts_code"].unique().tolist()
    print(f"[1/5] TOP5={len(top5)} rows, codes={len(codes)}")
    m = top5.merge(full, on="ts_code", how="left")
    m["subsector"] = m["sub_t5"].fillna(m.get("subsector"))
    m["roe_dt"] = pd.to_numeric(m["roe_dt"], errors="coerce")
    m["roe_pool_med"] = m.groupby("subsector")["roe_dt"].transform("median")
    print("[2/5] loading daily panel ...")
    panel = load_panel(codes)
    print(f"      panel rows={len(panel)}")
    idx = ensure_index()
    last_td = str(panel["trade_date"].max())
    idx = idx[idx["trade_date"] <= last_td]
    mk = market_state(idx)
    print(f"[3/5] market state: {mk['state']} (000300 @ {mk['last_date']})")

    series = {}
    for ts, g in panel.groupby("ts_code"):
        s = build_series(g)
        if s is not None:
            series[ts] = s

    rows = []
    print("[4/5] scoring ...")
    for r in m.to_dict("records"):
        ts = r["ts_code"]
        df = series.get(ts)
        base = {"ts_code": ts, "name": r.get("name") or r.get("name5"),
                "subsector": r.get("subsector"), "l3": r.get("l3"), "rank5": r.get("rank5"),
                "sli_v2": r.get("sli_v2"), "lifecycle": r.get("lifecycle"),
                "leader_type": r.get("leader_type")}
        if df is None:
            base.update(F=0, P=0, E=0, T=0, V=0, F120=0, verdict="AVOID",
                        reason="日线数据不足", setup="", cur=_f(r.get("close")))
            rows.append(base)
            continue
        tr = trend_of(df["px"].values)
        cur = float(df["close_raw"].iloc[-1])
        sd = detect_setup(df, tr)
        vo = volume_of(df, tr)
        f, fl = score_f(r)
        p, ptag = score_p(cur, tr, sd)
        if _nan(tr["r120"]) and _nan(tr["r60"]):
            e, etag = 50.0, "HISTORY_SHORT(上市不足120日,预期差不可评估)"
        elif _nan(tr["r120"]):
            e, etag = score_e(r, _g(tr["r20"]) * 100, _g(tr["r60"]) * 100, 0.0)
            e = min(e, 70.0)
            etag += "|次新股(无120日历史,预期差降级)"
        else:
            e, etag = score_e(r, _g(tr["r20"]) * 100, _g(tr["r60"]) * 100, _g(tr["r120"]) * 100)
        lv = levels_of(sd["setup"], sd, tr, cur) if sd["setup"] else {}
        rr = lv.get("rr", 0.0)
        verdict, reason = classify(f, p, e, tr["score"], vo["score"], sd["setup"], sd["stage"],
                                   rr, cur, lv.get("ceiling", 9e9), vo["shock"], tr)
        f120 = f * 0.20 + p * 0.20 + e * 0.25 + tr["score"] * 0.20 + vo["score"] * 0.15
        base.update({
            "F": round(f, 1), "F_lvl": fl, "P": round(p, 1), "P_tag": ptag, "E": round(e, 1),
            "E_tag": etag, "T": round(tr["score"], 1), "T_tag": tr["label"], "V": round(vo["score"], 1),
            "V_tag": vo["notes"], "F120": round(f120, 1), "verdict": verdict, "reason": reason,
            "setup": sd["setup"] or "", "stage": sd["stage"] or "",
            "cur": cur, "r20": round(_g(tr["r20"]) * 100, 1), "r60": round(_g(tr["r60"]) * 100, 1),
            "r120": round(_g(tr["r120"]) * 100, 1), "dd120": round(sd["dd120"] * 100, 1),
            "pe_ttm": _f(r.get("pe_ttm")), "or_yoy": _f(r.get("or_yoy")),
            "dt_yoy": _f(r.get("dt_netprofit_yoy"), _f(r.get("netprofit_yoy"))),
            "q2_yoy": _f(r.get("g3"), _f(r.get("q_profit_yoy"))), "q1_yoy": _f(r.get("g2")),
            "ocf": _f(r.get("ocf_to_profit")), "roe": _f(r.get("roe_dt")),
            "npm": _f(r.get("netprofit_margin")), "product": r.get("product"),
            "ideal": lv.get("ideal"), "zone_lo": lv.get("zone_lo"), "zone_hi": lv.get("zone_hi"),
            "stop": lv.get("stop"), "ceiling": lv.get("ceiling"), "target": lv.get("target"),
            "rr": round(rr, 2) if rr else NAN, "trigger": lv.get("trigger", ""),
            "ma20": tr["ma20"], "ma60": tr["ma60"],
        })
        rows.append(base)

    res = pd.DataFrame(rows)
    res.to_csv(os.path.join(OUT, f"f120_result_{last_td}.csv"), index=False, encoding="utf-8-sig")
    print(f"[5/5] result saved: {len(res)} rows @ f120_result_{last_td}.csv")
    return res, mk


def _g(x):
    return 0.0 if _nan(x) else x


# ---------------------------------------------------------------- 报告
def _n(x, d=2):
    if x is None or (isinstance(x, float) and (_nan(x) or x != x)):
        return "--"
    return f"{x:,.{d}f}"


_E_GAP = {"HIGH GAP": "业绩改善显著领先股价，未定价空间大",
          "NORMAL GAP": "业绩改善与股价大体同步，仍有跟随空间",
          "LOW GAP": "业绩改善已被股价大部分定价",
          "OVERPRICED": "股价已透支业绩改善，原则上不追"}


def _why_watch(row) -> tuple[str, str]:
    reason = str(row.get("reason", ""))
    if "HARD GATE未过" in reason:
        why = f"门槛未过: {reason.split(':')[-1].strip()}"
        can = "需先修复对应维度(F≥75/P≥70/T≥60/V≥60)后重新评估"
    elif "未达可交易门槛" in reason or "未达WATCH线" in reason:
        why = "基本面未达WATCH线(F≥75且P≥70)，不满足可交易质量标准"
        can = f"待中报质量分修复(F≥75/P≥70)后再评估；当前回踩MA20≈{_n(row.get('ma20'))}缩量企稳仅作观察信号"
    else:
        why = "基本面达标但当前无T+1买点(位置/涨幅结构不合适)"
        can = f"回踩MA20≈{_n(row.get('ma20'))}缩量企稳，或平台突破放量确认，或回踩突破位缩量不破后转强"
    return why, can


def _concl(row) -> str:
    etag = str(row.get("E_tag", "")).split("|")[0]
    gap = _E_GAP.get(etag, etag)
    lt = str(row.get("leader_type") or "--")
    return f"{lt}｜{gap}｜位置:{row.get('P_tag')}｜趋势:{row.get('T_tag')}｜量价:{row.get('V_tag')}"


def _risk(row) -> str:
    rs = []
    cur, ceil = row.get("cur"), row.get("ceiling")
    if not _nan(cur) and not _nan(ceil) and cur > ceil:
        rs.append(f"现价高于追涨上限{_n(ceil)}")
    pe = _f(row.get("pe_ttm"))
    if not _nan(pe) and pe > 60:
        rs.append(f"PE {_n(pe,0)}倍对业绩兑现节奏敏感")
    vtag = str(row.get("V_tag", ""))
    if "DISTRIBUTION" in vtag or "放量下跌" in vtag:
        rs.append("量价出现分配迹象")
    if "减速" in str(row.get("E_tag", "")):
        rs.append("Q2增速较Q1减速，持续性待验证")
    rs.append(f"有效跌破结构止损{_n(row.get('stop'))}=逻辑失效")
    return "；".join(rs)


def _card(row, pos_txt: str) -> str:
    orr, dt = row.get("or_yoy", 0), row.get("dt_yoy", 0)
    q1, q2 = row.get("q1_yoy", 0), row.get("q2_yoy", 0)
    cur, ideal = row.get("cur"), row.get("ideal")
    zlo, zhi, stop, ceil = row.get("zone_lo"), row.get("zone_hi"), row.get("stop"), row.get("ceiling")
    m20 = row.get("ma20")
    prod = row.get("product")
    try:
        prod = f"{float(prod):.1f}分"
    except (TypeError, ValueError):
        prod = str(prod) if prod and str(prod) != "nan" else "--"
    gap_txt = _E_GAP.get(str(row.get("E_tag", "")).split("|")[0], str(row.get("E_tag", "")))
    if not _nan(cur) and not _nan(zlo) and not _nan(zhi) and zlo <= cur <= zhi:
        act = f"现价已在BUY ZONE内；T+1若高开≤3%且结构未破坏 → 分2笔执行(首笔1/2仓)；高开>3% → WAIT"
    elif not _nan(zhi) and not _nan(cur) and cur > zhi:
        act = f"现价高于BUY ZONE上限 → WAIT，等待回踩 {_n(ideal)} 一带"
    else:
        act = f"现价低于BUY ZONE → 观察是否结构破坏；不破止损位且止跌企稳后方可接回"
    return f"""【{row.get('verdict','')} · T+1 EXECUTION】
股票：{row['name']}（{row['ts_code']}）
细分行业：{row['subsector']}（{row.get('l3','')} / 细分TOP{row.get('rank5','--')}）
F120={_n(row.get('F120'),1)}（F{_n(row.get('F'),0)}/P{_n(row.get('P'),0)}/E{_n(row.get('E'),0)}/T{_n(row.get('T'),0)}/V{_n(row.get('V'),0)}）｜Lifecycle={row.get('lifecycle')}｜Leader Type={row.get('leader_type')}
中报核心：营收 YoY {_n(orr,0)}%｜归母净利 YoY {_n(dt,0)}%｜Q1 {_n(q1,0)}% → Q2 {_n(q2,0)}%｜ROE {_n(row.get('roe'),1)}%｜净利率 {_n(row.get('npm'),1)}%｜PE {_n(row.get('pe_ttm'),1)}
增长逻辑：{row['subsector']}·主业纯度{prod}；预期差：{gap_txt}
当前趋势：{row.get('T_tag')}（现价较MA20 {_n((cur / m20 - 1) * 100 if m20 else float('nan'), 1)}%）
当前量价：{row.get('V_tag')}
BUY TYPE：{row['setup']}（{row.get('stage')}）
Current：{_n(cur)}
Ideal Entry：{_n(ideal)}
BUY ZONE：{_n(zlo)} ～ {_n(zhi)}
TRIGGER：{row.get('trigger')}
CHASE CEILING：{_n(ceil)}（超过不追）
STOP：{_n(stop)}（结构止损，有效跌破=逻辑失效）
TARGET：{_n(row.get('target'))}
RISK/REWARD：{_n(row.get('rr'))}
仓位：{pos_txt}
T+1操作：{act}
T+20验证：THESIS CHECK——①盈利预期(业绩会/上修/下修) ②行业景气 ③趋势(MA20/MA60未破坏) ④量价(无分配)。基本面+趋势完好 → HOLD
T+60管理：盈利逻辑延续+景气延续+MA20/MA60趋势完整+无明显Distribution → 不机械止盈，HOLD
T+120管理：重估盈利兑现/未来预期/估值/趋势——基本面继续改善+趋势完整 → CONTINUE HOLD；兑现+估值极端+趋势破坏 → EXIT
最大风险：{_risk(row)}
评级原因：{_concl(row)}"""


def _con_block(row, i: int, ready: bool) -> str:
    rs = str(row.get("reason", ""))
    if ready:
        if "Chase Ceiling" in rs:
            act = "现价高于追涨上限，回踩其下方缩量企稳后升PRIMARY候选"
        elif "R:R" in rs:
            act = "买点已确认但R:R<2.0；仅小仓试错或等待更优位置，R:R修复后升PRIMARY候选"
        elif "评分未满" in rs:
            act = "按BUY ZONE执行；评分补足(E≥65/V≥65/T≥70)后升PRIMARY候选"
        elif row.get("setup") in ("DEEP_PULLBACK", "BASE_BREAKOUT"):
            act = "结构已确认（该买点类型评级上限CONDITIONAL）；按BUY ZONE执行，>CEILING不追"
        else:
            act = "按BUY ZONE执行；T+1高开>3%一律WAIT"
        tail = f"✅ 买点已出现，{act}"
    else:
        if "Chase Ceiling" in rs:
            wait = f"回踩追涨上限 {_n(row.get('ceiling'))} 下方并缩量企稳"
        elif "R:R" in rs:
            wait = "R:R修复至≥2.0"
        else:
            tg = str(row.get("trigger") or "关键位确认（见TRIGGER）")
            wait = tg[2:] if tg.startswith("等待") else tg
            if "评分线不足" in rs:
                wait += "（同时评分补足至PRIMARY线）"
        tail = f"⛔ 当前不能买，等待：{wait}"
    return f"""{i}. **{row['name']}（{row['ts_code']}）** {row['subsector']} · 细分TOP{_n(row.get('rank5'),0)} · {row.get('setup')}（{'READY' if ready else 'WAIT'}）
   - F120 **{_n(row.get('F120'),1)}**｜F {_n(row.get('F'),0)} / P {_n(row.get('P'),0)} / E {_n(row.get('E'),0)} / T {_n(row.get('T'),0)} / V {_n(row.get('V'),0)}
   - Lifecycle {row.get('lifecycle') or '--'}｜Leader Type {row.get('leader_type') or '--'}｜PE {_n(row.get('pe_ttm'),1)}
   - 现价 {_n(row.get('cur'))}｜BUY ZONE {_n(row.get('zone_lo'))}～{_n(row.get('zone_hi'))}｜IDEAL ENTRY {_n(row.get('ideal'))}｜CEILING {_n(row.get('ceiling'))}
   - TRIGGER {row.get('trigger') or '--'}
   - STOP {_n(row.get('stop'))}｜TARGET {_n(row.get('target'))}｜R:R {_n(row.get('rr'))}
   - 营收同比 {_n(row.get('or_yoy'),0)}%｜归母净利同比 {_n(row.get('dt_yoy'),0)}%｜Q2同比 {_n(row.get('q2_yoy'),0)}%（Q1 {_n(row.get('q1_yoy'),0)}%）
   - ROE {_n(row.get('roe'),1)}%｜净利率 {_n(row.get('npm'),1)}%
   - 评级原因：{row.get('reason', '')}
   - {tail}"""


def report(res: pd.DataFrame, mk: dict):
    tier = {"PRIMARY BUY": 0, "CONDITIONAL BUY": 1, "WATCH": 2, "AVOID": 3}
    res = res.copy()
    res["_tier"] = res["verdict"].map(tier)
    # FINAL RANKING (V1.1 spec 十一/二十九): 等级门控在前，同级内 F120 合成分定序（多因子一致 > 单因子极强）
    res = res.sort_values(["_tier", "F120"], ascending=[True, False]).reset_index(drop=True)

    cap = mk["cap"]
    n_pri = int((res["verdict"] == "PRIMARY BUY").sum())
    pri_pos = min(0.12, max(0.04, cap / max(1, n_pri))) if n_pri else 0.0
    con_pos = round(pri_pos * 0.5, 3)
    pos_txt = f"{pri_pos * 100:.1f}%~{min(pri_pos * 1.4, 0.12) * 100:.1f}%"

    L = []
    A = L.append
    A(f"# 潜龙五维 · F120 V1.1 T+1 执行卡（市场日 {mk.get('last_date', DATE)}｜基本面快照 {resolve_date()}）\n")
    A(f"> 市场状态：{mk['state']}｜F120最大仓位：{cap * 100:.0f}%｜禁追涨：T+1高开>3%一律WAIT\n")

    pri = res[res["verdict"] == "PRIMARY BUY"]
    con = res[res["verdict"] == "CONDITIONAL BUY"]
    conr = con[con["stage"] == "ready"]

    A("## PRIMARY BUY（全仓口径）\n")
    if pri.empty:
        A("（无——宁缺毋滥）\n")
    for _, r in pri.iterrows():
        A(_card(r, pos_txt))
        A("")

    A("## CONDITIONAL-READY（试仓口径）\n")
    con_pos_txt = f"≤{con_pos * 100:.0f}%（试仓，不与PRIMARY叠加超限）"
    if conr.empty:
        A("（无）\n")
    for _, r in conr.iterrows():
        A(_card(r, con_pos_txt))
        A("")

    txt = "\n".join(L)
    path = os.path.join(OUT, f"f120_report_{mk.get('last_date', DATE)}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(txt)
    print(f"report saved: {path}")
    print(txt[:1600] + "\n... (完整报告见文件)")


if __name__ == "__main__":
    _res, _mk = run()
    report(_res, _mk)
