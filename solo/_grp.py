# -*- coding: utf-8 -*-
import pandas as pd

df = pd.read_csv(r"d:\mystock\solo\ige\output\ige_v12_tech_timing_20260904.csv")
df["s"] = df.apply(
    lambda r: f"{r['code'][:6]} {r['name']} 收{r['close']:.2f} "
              f"MA5/10/20/60={r['ma5']:.1f}/{r['ma10']:.1f}/{r['ma20']:.1f}/{r['ma60']:.1f} "
              f"MA20斜率{r['ma20_slope5']:+.2f}% 距60日高{r['dd_h60']:.0f}% "
              f"5日{r['gain5']:+.1f}% 量比{r['volume_ratio']:.2f}",
    axis=1)

order = ["破位-急跌", "下跌中-勿接刀", "破MA20-调整中", "贴MA60-防破位",
         "MA60下-下行整理", "贴MA60-弱势企稳",
         "MA60下-箱体/反弹", "贴MA60-待突破", "回踩近MA60",
         "回调企稳观察", "缩量回踩低吸区", "冲高回踩中", "强势整理",
         "强势-乖离过大", "主升/强势"]
grp = df.groupby("phase", sort=False)
for ph in order:
    sub = grp.get_group(ph) if ph in df["phase"].values else None
    if sub is None or len(sub) == 0:
        continue
    print(f"■ {ph}  x{len(sub)}")
    for _, r in sub.iterrows():
        print("   ", r["s"])
    print()
