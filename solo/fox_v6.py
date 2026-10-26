# -*- coding: utf-8 -*-
"""猎狐 V6 形态引擎：上升趋势中「缩量回踩不破位 → 尾盘重新走强」的趋势延续买点。

与 V4（任意位置的「缩量回调 → 首根放量中阳」反转第一根）不同，V6 硬性要求：
前期存在有效上涨趋势 + 缩量回踩 + 关键价格结构保持 + 当日重新走强，且未过度上涨。
目标不是筛当天涨幅最大的股票，而是找「回踩结束、买盘重新占优、尚未透支」的标的。

形态定义（信号日 j，日线口径；盘中用「实时价虚拟当日K线」近似）：
  ① 前期有效上涨趋势：信号日前 trend_win 日累计涨幅 ≥ trend_min（默认 20 日 ≥ 20%）
  ② 缩量回踩：从近 pullback_days 日高点（不含当日）回撤 ∈ [pullback_min, pullback_max]，
     且近期量能 < 前期量能（回踩缩量）
  ③ 关键价格结构保持：回踩最低价不破 MA20（low ≥ ma20_keep × MA20）
  ④ 尾盘重新走强：当日收中阳 ∈ [signal_min, signal_max]，收盘在中上位置，量能温和放大
  ⑤ 未过度上涨：前 trend_win 日涨幅 < trend_max（不追高，默认 25%）、收盘/MA20 ≤ ma20_ratio_max、
     当日量能不过大

评分 0~100（各子项甜区由「口径内全样本」分档实测标定，见 score_row）：
  趋势强度 20 + 回踩充分度 15 + 结构保持 20 + 重新走强 25 + 缩量 10 + 位置 10
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_PARAMS = {
    # ── ① 前期有效上涨趋势 ──
    "trend_win": 20,        # 趋势窗口（信号日前 N 日累计涨幅）
    "trend_min": 20.0,      # 前 N 日涨幅下限 %（20 日的 20% 线，用户拍板）
    "trend_max": 25.0,      # 前 N 日涨幅上限 %（「未过度上涨」核心杠杆：
                            #   回放标定 [20,25) 档 f10 +2.32%/64% f20 +0.23%；
                            #   [25,35) 档 f20 -6.96%/[35,60) f20 -11.28%，追高即分布派发）
    # ── ② 缩量回踩 ──
    "pullback_days": 20,    # 回踩观察窗口（近 N 日高点，不含当日）
    "pullback_min": 5.0,    # 最小回撤 %（从近 20 日高点回落的深度）
    "pullback_max": 15.0,   # 最大回撤 %（更深则可能已破位转弱）
    "shrink_win": 5,        # 回踩段量能窗口
    "shrink_base": 20,      # 前期量能基准窗口
    "shrink_min": 0.4,      # 过缩 = 流动性枯竭
    "shrink_max": 0.9,      # 回踩量能未明显收缩 → 剔除
    # ── ③ 关键价格结构保持 ──
    "ma20_keep": 0.97,      # 最低价 ≥ 该倍数×MA20（不破位）
    # ── ④ 尾盘重新走强 ──
    "signal_min": 3.0,      # 中阳下限 %（用户拍板 3%）
    "signal_max": 6.0,      # 中阳上限 %（用户拍板 6%）
    "gap_max": 1.5,         # 开盘涨幅上限 %（防高开追高）
    "close_pos_min": 0.6,   # 收盘位置下限 (close-low)/(high-low)：收盘在中上位置
    "volr_min": 1.2,        # 温和放量下限（买盘重新占优需量能配合）
    "volr_max": 3.0,        # 爆量上限（派发风险）
    # ── ⑤ 未过度上涨 ──
    "vol_win": 5,
    "vol20_base": 20,
    "vol20r_max": 1.5,      # 当日量 ÷ 前 20 日均量上限（防爆量）
    "ma20_ratio_max": 1.08,  # 收盘/MA20 上限（不追高）
    # ── 第一根 ──
    "first_lookback": 10,   # 窗口内不得有更早中阳；0 = 关闭
    # ── 其他 ──
    "min_bars": 60,
    "tier_a": 72.0,         # ≥ → 核心买点
    "tier_b": 60.0,         # ≥ → 买点池；其余 → 观察池
}

# 回测落盘时保留的特征列（供离线参数网格重筛，无需重跑扫描）
FEATURE_COLS = [
    "v6_ret20", "v6_ret10", "v6_pb", "v6_shrink", "v6_low_ma20", "v6_ma20_ratio",
    "v6_ma20_slope", "v6_pct", "v6_gap", "v6_close_pos", "v6_volr", "v6_vol20r",
    "v6_shadow", "v6_prior_cnt", "v6_sig",
]

CAPTION = "调仓：趋势延续买点（缩量回踩不破位 → 尾盘重新走强）"


def merged(params=None):
    p = dict(DEFAULT_PARAMS)
    if params:
        p.update({k: v for k, v in params.items() if v is not None})
    return p


def _clip(v, lo=0.0, hi=1.0):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return lo
    if not np.isfinite(v):
        return lo
    return max(lo, min(hi, v))


def _adj_series(df):
    """调整因子序列（缺失退化为 1.0）；adjc = close×adj 用于跨日比值，免疫除权跳空。"""
    if "adj_factor" in df.columns:
        adj = pd.to_numeric(df["adj_factor"], errors="coerce").ffill().bfill()
        if adj.notna().any():
            return adj.fillna(1.0).to_numpy(dtype=float)
    return np.ones(len(df), dtype=float)


def build_features(df, params=None):
    """在 df 上追加 v6_* 形态列（不改动原列）；返回 reset_index 后的新表。"""
    p = merged(params)
    out = df.reset_index(drop=True).copy()
    n = len(out)
    if n == 0:
        return out

    close = pd.to_numeric(out["close"], errors="coerce").astype(float)
    high = pd.to_numeric(out["high"], errors="coerce").astype(float)
    low = pd.to_numeric(out["low"], errors="coerce").astype(float)
    open_ = pd.to_numeric(out["open"], errors="coerce").astype(float)
    vol = pd.to_numeric(out["vol"], errors="coerce").astype(float).fillna(0.0)
    pct = pd.to_numeric(out.get("pct_chg"), errors="coerce").astype(float)
    adj = pd.Series(_adj_series(out))
    adjc, adjh = close * adj, high * adj

    pre_adjc = adjc.shift(1)

    # ① 前期有效上涨趋势：信号日前 trend_win 日累计涨幅
    tw = int(p["trend_win"])
    out["v6_ret20"] = (pre_adjc / adjc.shift(tw + 1).replace(0.0, np.nan) - 1.0) * 100.0
    out["v6_ret10"] = (pre_adjc / adjc.shift(11).replace(0.0, np.nan) - 1.0) * 100.0

    # ② 缩量回踩：近 pullback_days 日高点（不含当日）→ 昨收 的回撤
    peak = adjh.shift(1).rolling(int(p["pullback_days"]), min_periods=5).max()
    out["v6_pb"] = (1.0 - pre_adjc / peak.replace(0.0, np.nan)) * 100.0
    # 回踩段量能 / 前期量能
    v_win = vol.shift(1).rolling(int(p["shrink_win"]), min_periods=int(p["shrink_win"])).mean()
    v_base = vol.shift(1 + int(p["shrink_win"])).rolling(
        int(p["shrink_base"]), min_periods=int(p["shrink_base"])).mean()
    out["v6_shrink"] = v_win / v_base.replace(0.0, np.nan)

    # ③ 结构保持：最低价 / MA20 与 收盘 / MA20（复权均线还原到当前价口径）
    ma20 = adjc.rolling(20, min_periods=20).mean()
    ma20_px = ma20 / adj.replace(0.0, np.nan)
    out["v6_low_ma20"] = low / ma20_px.replace(0.0, np.nan)
    out["v6_ma20_ratio"] = close / ma20_px.replace(0.0, np.nan)
    out["v6_ma20_slope"] = (ma20 / ma20.shift(10).replace(0.0, np.nan) - 1.0) * 100.0

    # ④ 重新走强：日内收盘位置 / 量比 / 下影 / 跳空
    span = (high - low).replace(0.0, np.nan)
    out["v6_close_pos"] = ((close - low) / span).fillna(0.5)
    v_ref = vol.shift(1).rolling(int(p["vol_win"]), min_periods=int(p["vol_win"])).mean()
    out["v6_volr"] = vol / v_ref.replace(0.0, np.nan)
    v_base20 = vol.shift(1 + int(p["vol_win"])).rolling(
        int(p["vol20_base"]), min_periods=int(p["vol20_base"])).mean()
    out["v6_vol20r"] = vol / v_base20.replace(0.0, np.nan)
    body_low = np.minimum(open_, close)
    out["v6_shadow"] = ((body_low - low) / close.shift(1).replace(0.0, np.nan)) * 100.0
    out["v6_gap"] = ((open_ / close.shift(1).replace(0.0, np.nan)) - 1.0) * 100.0

    # 第一根：窗口内不得存在更早的中阳
    lb = int(p["first_lookback"])
    if lb > 0:
        qual = (pct >= float(p["signal_min"])).astype(float)
        out["v6_prior_cnt"] = qual.shift(1).rolling(lb, min_periods=1).sum()
    else:
        out["v6_prior_cnt"] = 0.0
    out["v6_pct"] = pct

    sig = (
        (out["v6_ret20"] >= float(p["trend_min"]))
        & (pct >= float(p["signal_min"])) & (pct <= float(p["signal_max"]))
        & (close > open_) & (high > low)
        & (out["v6_pb"] >= float(p["pullback_min"])) & (out["v6_pb"] <= float(p["pullback_max"]))
        & (out["v6_shrink"] <= float(p["shrink_max"]))
        & (out["v6_low_ma20"] >= float(p["ma20_keep"]))
        & (out["v6_close_pos"] >= float(p["close_pos_min"]))
        & (out["v6_volr"] >= float(p["volr_min"]))
        & (out["v6_gap"] <= float(p["gap_max"]))
    )
    if float(p.get("shrink_min") or 0.0) > 0:
        sig &= (out["v6_shrink"] >= float(p["shrink_min"]))
    if float(p.get("trend_max") or 0.0) > 0:
        sig &= (out["v6_ret20"] < float(p["trend_max"]))
    if float(p.get("volr_max") or 0.0) > 0:
        sig &= (out["v6_volr"] <= float(p["volr_max"]))
    if float(p.get("vol20r_max") or 0.0) > 0:
        sig &= (out["v6_vol20r"] <= float(p["vol20r_max"]))
    if float(p.get("ma20_ratio_max") or 0.0) > 0:
        sig &= (out["v6_ma20_ratio"] <= float(p["ma20_ratio_max"]))
    if lb > 0:
        sig &= (out["v6_prior_cnt"] <= 0)
    out["v6_sig"] = sig.fillna(False)
    return out


def score_row(row, p=None):
    """0~100 综合分（趋势20 + 回踩15 + 结构20 + 走强25 + 缩量10 + 位置10）

    分档线由全历史回放标定（trend_max=25 口径，n=77；短周期 f10 +2.32%/64% 为最优判定区间）：
      前20日涨幅 [20,25) 最优，>25 逐档转负 → 趋势上限收紧到 trend_max
      收盘位     [0.7,0.85] 最优（f3 +2.70%），>0.85 = 尾盘拉到最高追高退货
      量比       [1.5,2.0] 最优，>2.0 派发
    """
    p = merged(p)

    # 趋势强度：甜区 [trend_min, trend_max] 满分；<trend_min 递减；>trend_max 追高递减
    #   （回放标定 [20,25) 最优，>25 逐档转负，故上限收紧到 trend_max）
    r20 = float(row.get("v6_ret20") or 0.0)
    lo = float(p["trend_min"])
    hi = float(p.get("trend_max") or 0.0) or 25.0
    if r20 < lo:
        f_tr = max(0.0, (r20 - 5.0) / max(lo - 5.0, 1e-6))
    elif r20 <= hi:
        f_tr = 1.0
    else:
        f_tr = max(0.0, 1.0 - (r20 - hi) / 40.0)

    # 回踩充分度：8% 最优
    pb = float(row.get("v6_pb") or 0.0)
    f_pb = max(0.0, 1.0 - abs(pb - 8.0) / 8.0)

    # 结构保持：最低价/MA20 ≥ 1.0 满分，0.97→0
    lm = float(row.get("v6_low_ma20") or 0.0)
    f_st = _clip((lm - float(p["ma20_keep"])) / max(1.0 - float(p["ma20_keep"]), 1e-6))

    # 重新走强：收盘位置甜区 [0.7, 0.85]（0.7 以下未真正走强；0.85 以上=尾盘拉到最高，追高退货，分档转弱）
    cp = float(row.get("v6_close_pos") or 0.0)
    if cp < 0.7:
        f_cp = _clip(cp / 0.7)
    elif cp <= 0.85:
        f_cp = 1.0
    else:
        f_cp = max(0.0, 1.0 - (cp - 0.85) / 0.15)
    vr = float(row.get("v6_volr") or 0.0)
    if vr < 1.5:
        f_vr = max(0.0, (vr - 1.0) / 0.5)
    elif vr <= 2.5:
        f_vr = 1.0
    else:
        f_vr = max(0.0, 1.0 - (vr - 2.5) / 2.5)

    # 缩量：甜区 [0.6, 0.8]
    sr = float(row.get("v6_shrink") or 0.0)
    sr_lo = max(float(p.get("shrink_min") or 0.0), 0.4)
    sr_max = float(p["shrink_max"])
    if sr < 0.6:
        f_sr = max(0.0, (sr - sr_lo) / max(0.6 - sr_lo, 1e-6))
    elif sr <= 0.8:
        f_sr = 1.0
    else:
        f_sr = max(0.0, (sr_max - sr) / max(sr_max - 0.8, 1e-6))

    # 位置：收盘/MA20 ≤1.02 满分，>1.08 递减
    mr = float(row.get("v6_ma20_ratio") or 0.0)
    if mr <= 0:
        f_pos = 0.0
    elif mr <= 1.02:
        f_pos = 1.0
    else:
        f_pos = max(0.0, 1.0 - (mr - 1.02) / 0.10)

    return round(_clip(f_tr) * 20.0 + _clip(f_pb) * 15.0 + _clip(f_st) * 20.0
                 + (_clip(f_cp) * 15.0 + _clip(f_vr) * 10.0)
                 + _clip(f_sr) * 10.0 + _clip(f_pos) * 10.0, 1)


def tier_of(score, p=None):
    p = merged(p)
    if score >= float(p["tier_a"]):
        return "核心买点"
    if score >= float(p["tier_b"]):
        return "买点池"
    return "观察池"


def _row_signal(feat, j, p):
    """把 feature 表第 j 行转成信号 dict（含回调/结构/评分/止损止盈/理由）"""
    r = feat.iloc[j]
    close = float(r["close"])
    low = float(r["low"])
    pct = float(r.get("v6_pct") or 0.0)
    ret20 = float(r.get("v6_ret20") or 0.0)
    pb = float(r.get("v6_pb") or 0.0)
    shrink = float(r.get("v6_shrink") or 0.0)
    low_ma20 = float(r.get("v6_low_ma20") or 0.0)
    ma20_ratio = float(r.get("v6_ma20_ratio") or 0.0)
    close_pos = float(r.get("v6_close_pos") or 0.0)
    volr = float(r.get("v6_volr") or 0.0)
    vol20r = float(r.get("v6_vol20r") or 0.0)
    shadow = float(r.get("v6_shadow") or 0.0)
    gap = float(r.get("v6_gap") or 0.0)
    sc = score_row(r, p)

    # 回调窗口内最高价所在日
    win = int(p["pullback_days"])
    lo = max(0, j - win)
    peak_date, peak_close = "", np.nan
    if j > lo and "high" in feat.columns:
        seg = feat["high"].iloc[lo:j].to_numpy(dtype=float)
        if len(seg) and np.isfinite(seg).any():
            k = int(np.nanargmax(seg)) + lo
            peak_date = str(feat.iloc[k].get("trade_date", ""))
            peak_close = round(float(seg[k - lo]), 2)

    return {
        "idx": int(j),
        "code": str(r.get("ts_code", "")),
        "name": str(r.get("name", "") or ""),
        "signal_date": str(r.get("trade_date", "")),
        "close": round(close, 2),
        "pct_chg": round(pct, 2),
        "ret20": round(ret20, 1),
        "pb_depth": round(pb, 2),
        "shrink": round(shrink, 3),
        "low_ma20": round(low_ma20, 3),
        "ma20_ratio": round(ma20_ratio, 3),
        "close_pos": round(close_pos, 2),
        "volr": round(volr, 2),
        "vol20r": round(vol20r, 2),
        "shadow": round(shadow, 2),
        "gap": round(gap, 2),
        "prior_cnt": int(r.get("v6_prior_cnt") or 0),
        "peak_date": peak_date,
        "peak_close": peak_close,
        "score": sc,
        "tier": tier_of(sc, p),
        "stop": round(low, 2),
        "tp1": round(close * 1.05, 2),
        "tp2": round(close * 1.10, 2),
        "reason": (f"前{int(p['trend_win'])}日{crd(ret20)} → 缩量回踩{pb:.1f}%(量能×{shrink:.2f})"
                   f" 低点/MA20={low_ma20:.3f} 均线斜率20={float(r.get('v6_ma20_slope') or 0.0):+.1f}%"
                   f" → 中阳{pct:+.1f}% 收盘位{close_pos:.2f} 量×{volr:.2f}(中量×{vol20r:.2f})"
                   f" 收盘/MA20={ma20_ratio:.2f}"),
    }


def crd(v):
    return f"{v:+.1f}%" if np.isfinite(v) else "-"


def scan_fox_v6(df, params=None, signal_only=True):
    """全历史扫描：返回 DataFrame（signal_only=True 时只含命中行）。"""
    p = merged(params)
    if df is None or len(df) < int(p["min_bars"]):
        return pd.DataFrame()
    feat = build_features(df, p)
    idx = np.flatnonzero(feat["v6_sig"].to_numpy()) if signal_only else np.arange(len(feat))
    rows = [_row_signal(feat, int(j), p) for j in idx]
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    for c in FEATURE_COLS:
        out[c] = feat[c].reindex(idx).to_numpy()
    return out


def compute_fox_v6(df, params=None):
    """对 df 最后一根 K 线做形态判定（线上实时用）；命中返回 dict，否则 None。"""
    p = merged(params)
    need = max(int(p["min_bars"]),
               int(p["trend_win"]) + 1 + int(p["pullback_days"]) + int(p["shrink_base"])
               + int(p["shrink_win"]) + 30)
    if df is None or len(df) < int(p["min_bars"]):
        return None
    feat = build_features(df.tail(need).reset_index(drop=True), p)
    if feat.empty or not bool(feat["v6_sig"].iloc[-1]):
        return None
    return _row_signal(feat, len(feat) - 1, p)


# ─────────────────────────────────────────────
# 输出格式（与控制台/微信解耦，供 scan_fox_v6 直接调用）
# ─────────────────────────────────────────────
HEADER = (f"{'排名':<3} {'代码':<11} {'名称':<9} {'主题':<10} {'今收':>7} {'涨幅':>6} "
          f"{'前20日':>7} {'回踩':>6} {'低/MA20':>8} {'收盘位':>6} {'量比':>5} {'评分':>5} "
          f"{'分档':<6} {'止损':>7} {'目标1':>7}")


def format_console(signals, ts="", pre=False):
    if not signals:
        return ""
    tag = "盘中预检" if pre else "定稿"
    lines = ["", "=" * 128,
             f"🦊 「猎狐V6」趋势延续·缩量回踩不破位→尾盘重新走强·{tag} [{ts}] 命中{len(signals)}只"
             + ("（实时价近似收盘，尾盘回落可能作废，以 14:50 定稿为准）" if pre else ""),
             HEADER, "-" * 128]
    for i, s in enumerate(signals[:15], 1):
        lines.append(
            f"{i:<3} {s['code']:<11} {s['name'][:8]:<9} {(s.get('theme') or '')[:9]:<10} "
            f"{s['close']:>7.2f} {s['pct_chg']:>+5.1f}% {s['ret20']:>+6.1f}% "
            f"{s['pb_depth']:>5.1f}% {s['low_ma20']:>8.3f} {s['close_pos']:>6.2f} "
            f"{s['volr']:>5.2f} {s['score']:>5.1f} {s['tier']:<6} {s['stop']:>7.2f} {s['tp1']:>7.2f}")
    lines.append("=" * 128)
    return "\n".join(lines)


def format_wechat_lines(signals, pre=False):
    if not signals:
        return []
    head = ("盘中首现「趋势延续·缩量回踩不破位→尾盘重新走强」条件（实时价判定，尾盘回落可能作废；以 14:50 定稿为准）:"
            if pre else f"共{len(signals)}只命中「趋势延续·缩量回踩不破位→尾盘重新走强」形态:")
    out = [head]
    for s in signals[:5]:
        out.append(f"● [{s['tier']}]{s['name']}({s['code']}) [{s.get('theme', '')}] 评分{s['score']:.0f}")
        out.append(f"  今收{s['close']:.2f}({s['pct_chg']:+.1f}%) 前20日{s['ret20']:+.1f}% "
                   f"回踩{s['pb_depth']:.1f}% 低点/MA20={s['low_ma20']:.3f}")
        out.append(f"  收盘位{s['close_pos']:.2f} 量×{s['volr']:.2f} 止损{s['stop']:.2f} "
                   f"目标1 {s['tp1']:.2f} 目标2 {s['tp2']:.2f}")
    return out