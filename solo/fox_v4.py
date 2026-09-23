# -*- coding: utf-8 -*-
"""猎狐 V4 形态引擎：缩量回调后的第一根放量中阳线（3%~5%），长下影加分。

替代原「T0 天量确认」链路（W7 state_and_features → T0_CONFIRM → t0_confirm_v21）。
旧模块 w7_second_wave_engine / t0_confirm_v21 保留在仓库中，但新链路不再调用。

形态定义（信号日 j，日线口径；盘中用「实时价虚拟当日K线」近似）：
  ① 回调：近 pb_days 日内最高价（不含当日）→ 昨收 的回落幅度 ∈ [pb_min, pb_max]，默认 3%~15%
  ② 缩量：近 shrink_win 日均量 ÷ 前 shrink_base 日均量 ≤ shrink_max，默认 5日/20日 ≤ 0.8
  ③ 放量：当日量 ÷ 前 vol_win 日均量 ≥ volr_min，默认 ×5日 ≥ 1.5（首根放量）
  ④ 中阳：涨幅 ∈ [signal_min, signal_max]（默认 3%~5%）、收>开、有振幅（非一字）
  ⑤ 长下影：min(开,收) − 最低，相对昨收 的占比；≥1% 起加分、≥2% 更强（非硬条件）
  ⑥ 第一根：回调窗口内不得存在更早的中阳线（first_lookback）

评分 0~100（各子项甜区均由「口径内全样本」分档实测标定，详见 score_row）：
  放量 30（1.5~2.0× 最优，2.0~3.0× 递减） + 长下影 25（单调递增，1.5% 起满分）
  + 回调充分度 20（6.5% 最优） + 缩量 15（甜区 0.6~0.8，过缩=流动性枯竭同样扣分）
  + 位置 10（收盘 ≤0.98×MA20 最优）
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_PARAMS = {
    # ── ④ 中阳线 ──
    "signal_min": 3.0,      # 涨幅下限 %
    "signal_max": 5.0,      # 涨幅上限 %
    "gap_max": 1.5,         # 开盘涨幅上限 %（放宽到 3% 后 fwd20 由 +2.74% 降到 +2.68%，不追高开）
    # ── ① 回调 ──
    "pb_days": 10,          # 回调观察窗口（近 N 日，不含当日）
    "pb_min": 5.0,          # 最小回调幅度 %（放开到 3% 样本多 42%，fwd20/fwd60 双双下降）
    "pb_max": 20.0,         # 最大回调幅度 %
    # ── ② 缩量 ──
    "shrink_win": 5,
    "shrink_base": 20,
    "shrink_min": 0.4,      # 过缩（<0.4）= 流动性枯竭；0.4~0.6 档 fwd20 仅 +1.93%，评分另作递减
    "shrink_max": 1.0,      # 收到 0.8 后表现持平但样本少 27%，故放到 1.0
    # ── ③ 放量 ──
    "vol_win": 5,
    "volr_min": 1.5,        # 1.5~2.0 档 fwd20 +2.85% 最优；降到 1.2 掉到 +2.38%
    "volr_max": 3.0,        # 去掉该上限后 fwd20 由 +2.74% 降到 +2.66%（爆量派发）
    # ── ⑤ 长下影（加分项，非硬门槛；评分单调递增，1.5% 起满分）──
    # ── ⑥ 第一根 ──
    "first_lookback": 10,   # 窗口内不得有更早中阳；0 = 关闭该条件
    # ── 位置 / 其他 ──
    "ma20_ratio_max": 1.05,  # 去掉该上限后 fwd20 +2.72%（略降）；>1.03 档 fwd20 仅 +1.73%
    "min_bars": 60,
    "require_ma20": False,  # 是否硬要求 收盘 ≥ MA20×ma20_k
    "ma20_k": 0.97,
    "tier_a": 72.0,         # ≥ → 核心买点
    "tier_b": 60.0,         # ≥ → 买点池；其余 → 观察池
}

# 回测落盘时保留的特征列（供离线参数网格重筛，无需重跑扫描）
FEATURE_COLS = [
    "fox_pct", "fox_gap", "fox_pb", "fox_shrink", "fox_volr", "fox_shadow",
    "fox_pos", "fox_ma20_ratio", "fox_prior_cnt", "fox_sig",
]


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
    """在 df 上追加 fox_* 形态列（不改动原列）；返回 reset_index 后的新表。"""
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

    # ① 回调：近 pb_days 日内最高价（不含当日）→ 昨收
    peak = adjh.shift(1).rolling(int(p["pb_days"]), min_periods=3).max()
    pre_adjc = adjc.shift(1)
    out["fox_pb"] = (1.0 - pre_adjc / peak.replace(0.0, np.nan)) * 100.0

    # ② 缩量：近 shrink_win 日均量 ÷ 前 shrink_base 日均量
    v_win = vol.shift(1).rolling(int(p["shrink_win"]), min_periods=int(p["shrink_win"])).mean()
    v_base = vol.shift(1 + int(p["shrink_win"])).rolling(
        int(p["shrink_base"]), min_periods=int(p["shrink_base"])).mean()
    out["fox_shrink"] = v_win / v_base.replace(0.0, np.nan)

    # ③ 放量：当日量 ÷ 前 vol_win 日均量
    v_ref = vol.shift(1).rolling(int(p["vol_win"]), min_periods=int(p["vol_win"])).mean()
    out["fox_volr"] = vol / v_ref.replace(0.0, np.nan)

    # ⑤ 长下影：min(开,收) − 最低，相对昨收
    body_low = np.minimum(open_, close)
    out["fox_shadow"] = ((body_low - low) / close.shift(1).replace(0.0, np.nan)) * 100.0

    # 日内位置 + 开盘跳空
    span = (high - low).replace(0.0, np.nan)
    out["fox_pos"] = ((close - low) / span).fillna(0.5)
    out["fox_gap"] = ((open_ / close.shift(1).replace(0.0, np.nan)) - 1.0) * 100.0

    # 位置：收盘 / MA20
    ma20 = adjc.rolling(20, min_periods=20).mean()
    out["fox_ma20"] = ma20
    out["fox_ma20_ratio"] = close / (ma20 / adj).replace(0.0, np.nan)

    # ⑥ 第一根：窗口内不得存在更早的中阳
    lb = int(p["first_lookback"])
    if lb > 0:
        qual = (pct >= float(p["signal_min"])).astype(float)
        out["fox_prior_cnt"] = qual.shift(1).rolling(lb, min_periods=1).sum()
    else:
        out["fox_prior_cnt"] = 0.0
    out["fox_pct"] = pct

    sig = (
        (pct >= float(p["signal_min"])) & (pct <= float(p["signal_max"]))
        & (close > open_) & (high > low)
        & (out["fox_pb"] >= float(p["pb_min"])) & (out["fox_pb"] <= float(p["pb_max"]))
        & (out["fox_shrink"] <= float(p["shrink_max"]))
        & (out["fox_volr"] >= float(p["volr_min"]))
        & (out["fox_gap"] <= float(p["gap_max"]))
    )
    if float(p.get("shrink_min") or 0.0) > 0:
        sig &= (out["fox_shrink"] >= float(p["shrink_min"]))
    if float(p.get("volr_max") or 0.0) > 0:
        sig &= (out["fox_volr"] <= float(p["volr_max"]))
    if float(p.get("ma20_ratio_max") or 0.0) > 0:
        sig &= (out["fox_ma20_ratio"] <= float(p["ma20_ratio_max"]))
    if lb > 0:
        sig &= (out["fox_prior_cnt"] <= 0)
    if p.get("require_ma20"):
        sig &= (out["fox_ma20_ratio"] >= float(p["ma20_k"]))
    out["fox_sig"] = sig.fillna(False)
    return out


def score_row(row, p=None):
    """0~100 综合分（放量30 + 下影25 + 回调20 + 缩量15 + 位置10）

    各子项方向由「口径内全样本」分档实测标定（见 fox_v4_backtest.py 第三节）：
      放量  1.5~2.0× 最优(fwd20 +2.85%)，2.0~3.0× 递减(+2.52%)
      下影  单调递增(0~0.5% +2.56% → ≥2% +3.16%)，1.5% 起给满分
      回调  5~8% 最优(fwd60 +7.08%)，回调越深 fwd60 越差(+5.25%)
      缩量  0.6~0.8 最优(fwd20 +3.22%)，<0.6 流动性枯竭(+1.93%)
      位置  收盘/MA20 ≤0.98 最优(fwd20 +3.63%)，>1.03 明显转弱(+1.73%)
    """
    p = merged(p)

    # 放量：甜区 [volr_min, 2.0] 满分，低于下限线性衰减，高于 2.0 按派发风险递减
    v = float(row.get("fox_volr") or 0.0)
    lo = float(p["volr_min"])
    hi = min(float(p["volr_max"]) or 2.0, 2.0)
    if v < lo:
        f_vol = max(0.0, (v - 1.0) / max(lo - 1.0, 1e-6))
    elif v <= hi:
        f_vol = 1.0
    else:
        f_vol = max(0.0, 1.0 - (v - hi) / max((float(p["volr_max"]) or 5.0) - hi, 1e-6))

    # 长下影：单调递增，1.5% 起满分
    sh = float(row.get("fox_shadow") or 0.0)
    f_sh = min(1.0, max(0.0, sh) / 1.5)

    # 回调充分度：6.5% 最优（洗盘到位且趋势未破）
    pb = float(row.get("fox_pb") or 0.0)
    f_pb = max(0.0, 1.0 - abs(pb - 6.5) / 6.5)

    # 缩量：甜区 [0.6, 0.8]；<0.6 流动性枯竭、>0.8 未真正缩量
    sr = float(row.get("fox_shrink") or 0.0)
    sr_lo = max(float(p.get("shrink_min") or 0.0), 0.4)
    sr_max = float(p["shrink_max"])
    if sr < 0.6:
        f_sr = max(0.0, (sr - sr_lo) / max(0.6 - sr_lo, 1e-6))
    elif sr <= 0.8:
        f_sr = 1.0
    else:
        f_sr = max(0.0, (sr_max - sr) / max(sr_max - 0.8, 1e-6))

    # 位置：收盘 ≤0.98×MA20 最优，越高于 MA20 越差
    mr = float(row.get("fox_ma20_ratio") or 0.0)
    if mr <= 0:
        f_pos = 0.0
    elif mr <= 0.98:
        f_pos = 1.0
    else:
        f_pos = max(0.0, 1.0 - (mr - 0.98) / 0.12)

    return round(_clip(f_vol) * 30.0 + _clip(f_sh) * 25.0 + _clip(f_pb) * 20.0
                 + _clip(f_sr) * 15.0 + _clip(f_pos) * 10.0, 1)


def tier_of(score, p=None):
    p = merged(p)
    if score >= float(p["tier_a"]):
        return "核心买点"
    if score >= float(p["tier_b"]):
        return "买点池"
    return "观察池"


def _row_signal(feat, j, p):
    """把 feature 表第 j 行转成信号 dict（含峰位/评分/止损止盈/理由）"""
    r = feat.iloc[j]
    close = float(r["close"])
    low = float(r["low"])
    pct = float(r.get("fox_pct") or 0.0)
    pb = float(r.get("fox_pb") or 0.0)
    shrink = float(r.get("fox_shrink") or 0.0)
    volr = float(r.get("fox_volr") or 0.0)
    shadow = float(r.get("fox_shadow") or 0.0)
    gap = float(r.get("fox_gap") or 0.0)
    sc = score_row(r, p)

    # 峰位（回调窗口内最高价所在日）
    win = int(p["pb_days"])
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
        "volr": round(volr, 2),
        "pb_depth": round(pb, 2),
        "shrink": round(shrink, 3),
        "shadow": round(shadow, 2),
        "pos": round(float(r.get("fox_pos") or 0.0), 2),
        "ma20": round(float(r.get("fox_ma20") or 0.0), 2),
        "ma20_ratio": round(float(r.get("fox_ma20_ratio") or 0.0), 3),
        "gap": round(gap, 2),
        "prior_cnt": int(r.get("fox_prior_cnt") or 0),
        "peak_date": peak_date,
        "peak_close": peak_close,
        "score": sc,
        "tier": tier_of(sc, p),
        "stop": round(low, 2),
        "tp1": round(close * 1.05, 2),
        "tp2": round(close * 1.10, 2),
        "reason": (f"缩量回调{pb:.1f}%(量能×{shrink:.2f}) → 首根放量中阳{pct:+.1f}%"
                   f"(量×{volr:.2f}) 下影{shadow:.1f}% 收盘/MA20={float(r.get('fox_ma20_ratio') or 0.0):.2f}"),
    }


def scan_fox_v4(df, params=None, signal_only=True):
    """全历史扫描：返回 DataFrame（signal_only=True 时只含命中行）。

    特征列一并保留（fox_*），便于离线重筛阈值而不必重跑扫描。
    """
    p = merged(params)
    if df is None or len(df) < int(p["min_bars"]):
        return pd.DataFrame()
    feat = build_features(df, p)
    idx = np.flatnonzero(feat["fox_sig"].to_numpy()) if signal_only else np.arange(len(feat))
    rows = [_row_signal(feat, int(j), p) for j in idx]
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    for c in FEATURE_COLS:
        out[c] = feat[c].reindex(idx).to_numpy()
    return out


def compute_fox_v4(df, params=None):
    """对 df 最后一根 K 线做形态判定（线上实时用）；命中返回 dict，否则 None。

    只需最近约 90 根 K 线即可完成判定（窗口 10 + 缩量基准 25 + MA20 20）。
    """
    p = merged(params)
    need = max(int(p["min_bars"]), int(p["pb_days"]) + int(p["shrink_base"])
               + int(p["shrink_win"]) + 25)
    if df is None or len(df) < int(p["min_bars"]):
        return None
    feat = build_features(df.tail(need).reset_index(drop=True), p)
    if feat.empty or not bool(feat["fox_sig"].iloc[-1]):
        return None
    return _row_signal(feat, len(feat) - 1, p)


# ─────────────────────────────────────────────
# 输出格式（与控制台/微信解耦，供 scan_fox_v4_live 直接调用）
# ─────────────────────────────────────────────
HEADER = (f"{'排名':<3} {'代码':<11} {'名称':<9} {'主题':<10} {'今收':>7} {'涨幅':>6} "
          f"{'量比':>5} {'回调':>6} {'缩量':>5} {'下影':>5} {'评分':>5} {'分档':<6} "
          f"{'止损':>7} {'目标1':>7}")


def format_console(signals, ts="", pre=False):
    if not signals:
        return ""
    tag = "盘中预检" if pre else "定稿"
    lines = ["", "=" * 118,
             f"🦊 「猎狐V4」缩量回调→首根放量中阳·{tag} [{ts}] 命中{len(signals)}只"
             + ("（实时价近似收盘，尾盘回落可能作废，以 14:50 定稿为准）" if pre else ""),
             HEADER, "-" * 118]
    for i, s in enumerate(signals[:15], 1):
        lines.append(
            f"{i:<3} {s['code']:<11} {s['name'][:8]:<9} {(s.get('theme') or '')[:9]:<10} "
            f"{s['close']:>7.2f} {s['pct_chg']:>+5.1f}% {s['volr']:>5.2f} "
            f"{s['pb_depth']:>5.1f}% {s['shrink']:>5.2f} {s['shadow']:>4.1f}% "
            f"{s['score']:>5.1f} {s['tier']:<6} {s['stop']:>7.2f} {s['tp1']:>7.2f}")
    lines.append("=" * 118)
    return "\n".join(lines)


def format_wechat_lines(signals, pre=False):
    if not signals:
        return []
    head = ("盘中首现「缩量回调→首根放量中阳」条件（实时价判定，尾盘回落可能作废；以 14:50 定稿为准）:"
            if pre else f"共{len(signals)}只命中「缩量回调→首根放量中阳」形态:")
    out = [head]
    for s in signals[:5]:
        out.append(f"● [{s['tier']}]{s['name']}({s['code']}) [{s.get('theme', '')}] 评分{s['score']:.0f}")
        out.append(f"  今收{s['close']:.2f}({s['pct_chg']:+.1f}%) 量×{s['volr']:.2f} "
                   f"回调{s['pb_depth']:.1f}% 缩量×{s['shrink']:.2f} 下影{s['shadow']:.1f}%")
        out.append(f"  止损{s['stop']:.2f} 目标1 {s['tp1']:.2f} 目标2 {s['tp2']:.2f}")
    return out
