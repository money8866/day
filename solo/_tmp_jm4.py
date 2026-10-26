# -*- coding: utf-8 -*-
"""极米科技 20260929 在 W7 引擎各分支下的逐条件诊断（临时脚本，用完删除）"""
import sys

import numpy as np

sys.path.insert(0, r"d:\mystock\solo")
import w7_second_wave_engine as W

CODE = "688696.SH"
DATE = "20260929"

reader = W.CacheReader()
df = reader.bars_sql(CODE, DATE)
df = df.reset_index(drop=True)
end = len(df) - 1
print(f"{CODE} bars={len(df)} last_date={df.iloc[end].trade_date}")

# 找 T0（与引擎同口径）
ev = W.find_event_anchor(df)
print("find_event_anchor ->", ev)
event_idx = ev[0] if isinstance(ev, tuple) else None
print("event_idx =", event_idx,
      "date =", df.iloc[event_idx].trade_date if event_idx is not None else None)

# ---- RIGHT_BOTTOM 逐条 ----
lows = df.low.to_numpy(dtype=float)
highs = df.high.to_numpy(dtype=float)
w0 = max(0, end - W.RIGHT_BOTTOM_WINDOW)
h0 = w0 + int(np.argmax(highs[w0:end]))
l1 = h0 + int(np.argmin(lows[h0:end + 1]))
m = l1 + int(np.argmax(highs[l1:end + 1]))
print("\n[R-B] 几何：")
print(f"  H0={df.iloc[h0].trade_date} high={highs[h0]:.2f}  (end-h0={end-h0})")
print(f"  L1={df.iloc[l1].trade_date} low ={lows[l1]:.2f}  (end-l1={end-l1})")
print(f"  M ={df.iloc[m].trade_date} high={highs[m]:.2f}  (m-l1={m-l1})")
base_lo = lows[max(0, h0 - W.RIGHT_BOTTOM_LOOKBACK):h0 + 1].min()
print(f"  ① 前波涨幅={highs[h0]/base_lo-1:+.2%}  需 ≥{W.RIGHT_BOTTOM_PRIOR_RISE:.0%}")
print(f"  ② H0→L1 回落={1-lows[l1]/highs[h0]:.2%}  需 ≥{W.RIGHT_BOTTOM_PULLBACK:.0%}")
print(f"  ③ L1→M 反弹={highs[m]/lows[l1]-1:+.2%}  需 ≥{W.RIGHT_BOTTOM_REBOUND:.0%}")
v20 = W.safe_mean(df.vol.iloc[max(0, end - 20):end])
seg = df.vol.iloc[l1:m + 1].to_numpy(dtype=float)
print(f"  ③b 段内最大量={np.nanmax(seg):.0f} vs v20×{W.RIGHT_BOTTOM_REBOUND_VOLR}="
      f"{v20*W.RIGHT_BOTTOM_REBOUND_VOLR:.0f}  最大量比={np.nanmax(seg)/v20:.2f}")
near_lo = lows[max(0, end - W.RIGHT_BOTTOM_NEAR + 1):end + 1].min()
print(f"  ④ 当日 low={lows[end]:.2f} 近{W.RIGHT_BOTTOM_NEAR}根最低={near_lo:.2f}")
print(f"  ⑤ low/L1={lows[end]/lows[l1]:.4f}  需 ∈[{1-W.RIGHT_BOTTOM_TOL_LO:.2f},"
      f"{1+W.RIGHT_BOTTOM_TOL_HI:.2f}]")
print(f"  ⑥ 自 M 回撤={1-lows[end]/highs[m]:.2%}  需 ≥{W.RIGHT_BOTTOM_RETRACE:.0%}")
print(f"  ⑦ 当日量比={W.finite(df.iloc[end].vol)/v20:.3f}  需 ≤{W.RIGHT_BOTTOM_VOL_DRYUP}")
ma10 = W.finite(df.iloc[end].ma_bfq_10, 0.0)
ma60 = W.finite(df.iloc[end].ma_bfq_60, 0.0)
ma60p = W.finite(df.iloc[max(0, end - 5)].ma_bfq_60, 0.0)
close = W.finite(df.iloc[end].close, 0.0)
print(f"  ⑧ close={close:.2f} vs MA60={ma60:.2f}  MA60近5根变动={ma60/ma60p-1:+.3%}"
      f"  需 ≥{W.RIGHT_BOTTOM_MA60_FLAT:.1%}")
print(f"  ⑨ close={close:.2f} vs MA10={ma10:.2f}  需 ≤MA10")
start = event_idx if (event_idx is not None and 0 <= event_idx < end) else h0
run = best = 0
for i in range(start + 1, end + 1):
    m20 = W.safe_mean(df.vol.iloc[max(0, i - 20):i])
    if m20 > 0 and W.finite(df.iloc[i].vol) >= m20 * W.RIGHT_BOTTOM_CHURN_VOLR:
        run += 1
        best = max(best, run)
    else:
        run = 0
print(f"  ⑩ 自T0({df.iloc[start].trade_date})最长连续放量={best}  需 ≤{W.RIGHT_BOTTOM_MAX_CHURN_RUN}")

ok, info = W.right_bottom(df, end, event_idx)
print(f"\n  >>> right_bottom = {ok}")

# ---- 补充特征：供 SQUAT 阈值取档 ----
v = df.vol.to_numpy(dtype=float)
base_c_point = df.close.to_numpy(dtype=float)[max(0, event_idx - 20)]
lo60 = v[max(0, end - 60):end + 1].min()
ma20 = W.finite(df.iloc[end].ma_bfq_20, 0.0)
ma20p = W.finite(df.iloc[end - 5].ma_bfq_20, 0.0)
print("\n[SQ] 补充特征：")
print(f"  T0前20根累计涨幅 = {df.close.to_numpy()[event_idx]/base_c_point-1:+.2%}")
print(f"  当日量/近60日地量 = {v[end]/lo60:.2f}（地量 {lo60:.0f}）")
print(f"  close/MA20-1 = {close/ma20-1:+.2%}   MA20近5根变动 = {ma20/ma20p-1:+.2%}")
print(f"  当日振幅 = {(df.iloc[end].high-df.iloc[end].low)/df.iloc[end-1].close:.2%}   "
      f"实体 = {abs(df.iloc[end].close-df.iloc[end].open)/(df.iloc[end].high-df.iloc[end].low):.1%}")

ok2, info2 = W.shakeout_hold(df, end)
print(f"  >>> shakeout_hold = {ok2}")
ok3, info3 = W.midline_hold(df, end)
print(f"  >>> midline_hold = {ok3}")
ok4, info4 = W.squat_shrink(df, event_idx, end)
print(f"  >>> squat_shrink = {ok4}")
if ok4:
    for k, v in info4.items():
        print(f"      {k} = {v}")
res = W.state_and_features(df, event_idx, ev[1])
if res:
    base, state = res[0], res[1]
    print(f"\n  state_and_features -> state = {state}  pressure = {res[8]:.2f}")
    x = {"state": state, "close": W.finite(df.iloc[end].close), "pressure": res[8],
         "ma20": W.finite(df.iloc[end].ma_bfq_20), "volr": W.finite(df.iloc[end].vol)/v20}
    x.update({k: base.get(k) for k in ("squat_low", "squat_high", "squat_dd", "mid_line", "shake_line",
                                       "rb_left_low", "retest_low", "event_open")})
    print(f"  w7_fail_line -> {W.w7_fail_line(x)}")
    print(f"  w7_exec_status -> {W.w7_exec_status(x)}")
