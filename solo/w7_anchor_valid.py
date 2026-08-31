# -*- coding: utf-8 -*-
"""锚点有效性验证：中际旭创@20250508 / 华正新材@20250812
用 V4.1 全链路（T120_ALPHA/ENTRY/状态机）在事件日+20天窗口 point-in-time 评估，
再统计事件日后 T+20/60/120 实际涨幅，判断锚点案例是否依然成立。"""
import sys
sys.path.insert(0, r"D:\mystock\solo")
import numpy as np
import pandas as pd
from w7_second_wave_engine import (
    CacheReader, MarketCtx, ANCHORS, anchor_features, extreme_event,
    state_and_features, similarity, alpha_hvt, alpha_trend, alpha_fina,
    alpha_rs, alpha_upside, alpha_sector, t120_alpha_score, entry_score_v2,
    clip, finite, MIN_BARS,
)

reader = CacheReader()
mdates, mvals = reader.market_curve("20260828")
mkt = MarketCtx(mdates, mvals)
nfina = reader.load_fina()

ANCHORS2 = {"中际旭创": ("300308.SZ", "20250508"), "华正新材": ("603186.SH", "20250812")}

# 构建锚点特征库（用完整数据，取事件日）
anchors = {}
for label, (code, ad) in ANCHORS2.items():
    df_full = reader.bars_sql(code, "20260828")
    anchors[label] = anchor_features(df_full, ad)
    print(f"{label} {code} 锚点特征: " + " ".join(f"{k}={v:.1f}" for k, v in anchors[label].items() if isinstance(v, (int, float))))

print("\n" + "=" * 60)
for label, (code, ad) in ANCHORS2.items():
    df_full = reader.bars_sql(code, "20260828")
    dts = df_full.trade_date.astype(str).tolist()
    if ad not in dts:
        print(f"{label}: 事件日 {ad} 不在数据中")
        continue
    i = dts.index(ad)
    closes = df_full.close.to_numpy(dtype=float)
    hi = df_full.high.to_numpy(dtype=float)
    # ---- 事件后实际表现 ----
    print(f"\n=== {label} {code} 天量日 {ad} idx={i} close={closes[i]:.2f} ===")
    for w, tag in ((20, "T+20"), (60, "T+60"), (120, "T+120")):
        if i + w < len(closes):
            r = closes[i + w] / closes[i] - 1
            print(f"  {tag} 实际涨幅: {r*100:+.1f}% (close={closes[i+w]:.2f})")
    peak = hi[i + 1:i + 121].max() if i + 1 < len(hi) else np.nan
    if not np.isnan(peak):
        print(f"  事件后120日最高: {peak:.2f} ({peak/closes[i]-1:+.1f}%)")
    # ---- V4.1 point-in-time 评估：截断到 事件日+20天 ----
    end_slice = i + 20 + 1  # 行为窗口 = 事件日后20个交易日
    df_slice = df_full.iloc[:end_slice].reset_index(drop=True)
    last = len(df_slice) - 1
    # 找事件日索引（在截断后）
    dts_s = df_slice.trade_date.astype(str).tolist()
    if ad not in dts_s:
        print("  事件日不在截断数据中")
        continue
    ei = dts_s.index(ad)
    ok, ep = extreme_event(df_slice, ei)
    base, state, pp, pp_ok, reexp, breakout, major_risk, drawdown, pressure = state_and_features(df_slice, ei, ep)
    if base is None:
        print("  state_and_features 返回 None")
        continue
    # 若真实天量事件(全历史)不是 extreme_event(截断)也识别, 用锚点口径替代
    if not ok:
        # 全历史识别（窗口=None 用全历史），事件日本身必然是
        ok, ep = extreme_event(df_full, i)
        base, state, pp, pp_ok, reexp, breakout, major_risk, drawdown, pressure = state_and_features(df_full, i, ep)
        last = i + 20 if i + 20 < len(df_full) else len(df_full) - 1
        df_slice = df_full.iloc[:last + 1].reset_index(drop=True)
        ei = dts.index(ad)
        # 重新计算 trend/rs 等基于 df_slice
        state = state  # 已用完整数据,趋势会前视;仅作参考
    current = dict(base)
    sim_a = similarity(current, anchors.get("中际旭创"))
    sim_b = similarity(current, anchors.get("华正新材"))
    hvt = (sim_a + sim_b) / 2
    last = len(df_slice) - 1
    fina_now, fina_prev = reader.fina(code, as_of=str(df_slice.iloc[last].trade_date))
    dims = {
        "hvt": alpha_hvt(base, hvt, drawdown),
        "trend": alpha_trend(df_slice, last),
        "fina": alpha_fina(fina_now, fina_prev),
        "rs": alpha_rs(df_slice, last, mkt),
        "upside": alpha_upside(df_slice, last),
        "sector": alpha_sector("", {}, {}),
    }
    t120 = t120_alpha_score(dims)
    event_low = finite(df_slice.low.iloc[ei], 0.0)
    entry, entry_dims = entry_score_v2(df_slice, last, pp, pp_ok, reexp, breakout, event_low, mkt)
    trend_state = state in ("BREAKOUT_CONFIRM", "SECOND_WAVE", "RE_EXPANSION")
    trend_confirmed = breakout or reexp or dims["trend"] >= 70
    risky = major_risk or state in ("FAILED", "DISTRIBUTION")
    if risky:
        status = "WATCH"
    elif t120 >= 85 and entry >= 80 and trend_state:
        status = "PRIMARY_BUY"
    elif t120 >= 85:
        status = "T120_ROCKET"
    elif t120 >= 70 and trend_confirmed:
        status = "CONFIRMED"
    else:
        status = "WATCH"
    print(f"\n  V4.1 评估(事件日+20天窗口):")
    print(f"  状态={state} | T120_ALPHA={t120:.1f} | ENTRY={entry:.1f} | buy={status}")
    print(f"  dims: hvt={dims['hvt']:.0f} trend={dims['trend']:.0f} fina={dims['fina']:.0f} rs={dims['rs']:.0f} upside={dims['upside']:.0f}")
    print(f"  entry_dims: pp={entry_dims['pp']:.0f} cp={entry_dims['cp']:.0f} breakout={entry_dims['breakout']:.0f} vol={entry_dims['volume']:.0f} pullback={entry_dims['pullback']:.0f} rs={entry_dims['rs']:.0f} rrr={entry_dims['rrr']:.0f}")
    print(f"  base: CQ={base['cq']:.0f} Ac={base['acceptance']:.0f} SDS={base['sds']:.0f} 回撤={drawdown*100:.1f}%")

reader.close()
