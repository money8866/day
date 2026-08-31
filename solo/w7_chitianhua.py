# -*- coding: utf-8 -*-
"""分析赤天化 600227.SH：天量触发日、后续走势、突破细节"""
import sys
sys.path.insert(0, r"D:\mystock\solo")
import numpy as np
from w7_second_wave_engine import CacheReader, extreme_event, state_and_features, pp_score, finite, MAX_EVENT_AGE, MIN_BARS

reader = CacheReader()
df = reader.bars_sql("600227.SH", "20260828")
dts = df.trade_date.astype(str).tolist()
c = df.close.to_numpy(dtype=float)
h = df.high.to_numpy(dtype=float)
l = df.low.to_numpy(dtype=float)
o = df.open.to_numpy(dtype=float)
v = df.vol.to_numpy(dtype=float)
tr = df.turnover_rate_f.fillna(0).to_numpy(dtype=float)
pc = df.pct_chg.fillna(0).to_numpy(dtype=float)

print(f"赤天化 600227.SH 总K线={len(df)} 最新={dts[-1]} close={c[-1]:.2f}\n")

# 1. 最近60日所有天量候选
cands = []
for i in range(max(MIN_BARS, len(df) - MAX_EVENT_AGE - 1), len(df) - 2):
    ok, ep = extreme_event(df, i)
    if ok:
        cands.append((i, ep))
print("== 最近60日天量事件 ==")
for i, ep in cands:
    print(f"  {dts[i]}  close={c[i]:.2f} vol={v[i]:.0f} 换手={tr[i]:.2f}% 涨跌={pc[i]:+.2f}%  P{ep:.1f}")

# 2. 天量后走势
if cands:
    ei = cands[-1][0]
    print(f"\n== 天量日 {dts[ei]} 之后走势（每3日）==")
    print(f"  天量日 {dts[ei]} close={c[ei]:.2f} vol={v[ei]:.0f} 换手={tr[ei]:.2f}%")
    for k in range(ei + 1, len(df)):
        if (k - ei - 1) % 3 == 0 or k == len(df) - 1:
            vol20 = np.mean(v[max(0, k - 20):k]) if k >= 20 else np.mean(v[:k])
            print(f"  {dts[k]} close={c[k]:6.2f} 较天量={c[k]/c[ei]-1:+6.1%} vol={v[k]:.0f} 量比={v[k]/vol20:.2f} 换手={tr[k]:.2f}% 涨跌={pc[k]:+.2f}%")

    # 3. 突破检测：用 state_and_features 确认
    base, state, pp, pp_ok, reexp, breakout, major_risk, dd, pressure = state_and_features(df, ei, cands[-1][1])
    print(f"\n== state_and_features ==")
    print(f"  状态={state} breakout={breakout} reexp={reexp} pp_ok={pp_ok} pp={pp:.0f}")
    print(f"  CQ={base['cq']:.1f} Ac={base['acceptance']:.1f} SDS={base['sds']:.1f} Lock={base['lock']:.1f}")
    print(f"  pressure(平台压力)={pressure:.2f} 最新close={c[-1]:.2f}")

    # 4. 找突破日：收盘站上 pressure 的那天
    print("\n== 平台压力位突破日扫描 ==")
    for k in range(ei + 1, len(df)):
        vol20 = np.mean(v[max(0, k - 20):k]) if k >= 20 else np.mean(v[:k])
        pos = (c[k] - min(o[k], l[k])) / max(h[k] - l[k], 0.01)
        upper = (h[k] - max(o[k], c[k])) / max(h[k] - l[k], 0.01)
        is_bk = c[k] > pressure and v[k] >= vol20 * 1.2 and pos >= 70 and upper < 0.35
        if is_bk:
            print(f"  {dts[k]} close={c[k]:.2f} 突破pressure={pressure:.2f} vol={v[k]:.0f} 量比={v[k]/vol20:.2f} pos={pos:.0f} 上影={upper:.2f}")
reader.close()
