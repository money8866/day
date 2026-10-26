# -*- coding: utf-8 -*-
"""极米科技 20260929 逐条对照 SQUAT 各档口径（临时脚本，用完删除）"""
import os
import sqlite3
import sys

sys.path.insert(0, r"d:\mystock\solo")
import _tmp_squat as S

CODE = "688696.SH"
conn = sqlite3.connect(S.DB)
frames = S.load_bars(conn, "20240101")
conn.close()
f = frames[CODE]
S.prep(f)
i = f["date"].index("20260929")

o, h, l, c, v = (f["o"][i], f["h"][i], f["l"][i], f["c"][i], f["v"][i])
vol20, ma20, ma60 = f["vol20"][i], f["ma20"][i], f["ma60"][i]
# 最近 T0
t0_idx = None
for j in range(i, max(0, i - 61), -1):
    if f["t0"][j]:
        t0_idx = j
        break
span = h - l
body = abs(c - o) / span
up_sh = (h - max(o, c)) / span
dn_sh = (min(o, c) - l) / span
amp = span / f["c"][i - 1]
pk = max(f["h"][t0_idx:i + 1])
dd = (pk - c) / pk

print(f"极米科技 688696.SH 20260929")
print(f"  开{o:.2f} 高{h:.2f} 低{l:.2f} 收{c:.2f} 涨幅{f['pc'][i]:+.2f}%")
print(f"  量={v:.0f}  前20日均量={vol20:.0f}  量比={v/vol20:.3f}   [要求 ≤0.30 / ≤0.35 / ≤0.45]")
print(f"  最近T0={f['date'][t0_idx]}  T0量={f['v'][t0_idx]:.0f}  T0量/当日量={f['v'][t0_idx]/v:.2f}  [要求 ≥8 / ≥12]")
print(f"  实体占比={body*100:.1f}% [≤35%]   上影={up_sh*100:.1f}% 下影={dn_sh*100:.1f}% [各 ≥15%]")
print(f"  振幅={amp*100:.2f}% [≤4%]")
print(f"  收盘{c:.2f} vs MA20 {ma20:.2f}（须低于）  vs MA60 {ma60:.2f}（须高于）")
print(f"  T0后最高={pk:.2f}  回撤={dd*100:.2f}% [≥5%/8%/12%]")
print()
print("各档位判定：")
for volr in (0.30, 0.35, 0.45):
    for t0r in (0.0, 8.0, 12.0):
        for ddz in (0.05, 0.08, 0.12):
            ok = (v / vol20 <= volr and amp <= 0.04 and body <= 0.35
                  and up_sh >= 0.15 and dn_sh >= 0.15
                  and c < ma20 and c >= ma60 and dd >= ddz
                  and (t0r == 0 or f["v"][t0_idx] / v >= t0r))
            if ok:
                print(f"  通过  volr={volr} t0_ratio={t0r:g} dd={ddz:g}")
print()
hl = [volr for volr in (0.30, 0.35, 0.45)
      if v / vol20 <= volr and amp <= 0.04 and body <= 0.35 and up_sh >= 0.15
      and dn_sh >= 0.15 and c < ma20 and c >= ma60 and dd >= 0.08]
print(f"若只卡 volr（不卡 t0_ratio）可通过的档位: {hl}")