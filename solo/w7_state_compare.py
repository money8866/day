# -*- coding: utf-8 -*-
"""对比吸收态 vs 趋势确认态的买入表现（基于 w7_backtest_v41 冒烟信号 CSV）"""
import pandas as pd
import numpy as np

CSV = r"D:\mystock\solo\report_daily\w7_backtest_v41_signals.csv"
S = pd.read_csv(CSV)

# 吸收类：天量后回踩缩量锁筹，买点是低吸
absorb = S[S.state.isin(["ABSORPTION", "DRYUP"])]
# 趋势确认类：放量突破/二次启动，买点是右侧追突破
confirm = S[S.state.isin(["BREAKOUT_CONFIRM", "SECOND_WAVE", "RE_EXPANSION"])]


def stat(g, label):
    if g.empty:
        print(f"\n== {label}: 无样本 ==")
        return
    print(f"\n== {label}  样本={len(g)} ==")
    for col, tag in (("fwd20", "T+20"), ("fwd60", "T+60"), ("fwd120", "T+120")):
        d = g[col].dropna()
        if d.empty:
            continue
        print(f"  {tag}: 均值={d.mean()*100:+.2f}% 中位={d.median()*100:+.2f}% P90={d.quantile(0.9)*100:+.1f}% "
              f"胜率={(d>0).mean()*100:.1f}% ≥20%={(d>=0.2).mean()*100:.1f}% ≥30%={(d>=0.3).mean()*100:.1f}% ≥50%={(d>=0.5).mean()*100:.1f}% (n={len(d)})")


stat(absorb, "吸收态 ABSORPTION+DRYUP")
stat(confirm, "趋势确认态 BREAKOUT+SECOND_WAVE+RE_EXPANSION")
print("\n== 细分 ==")
for s_ in ["ABSORPTION", "DRYUP", "BREAKOUT_CONFIRM", "SECOND_WAVE", "RE_EXPANSION"]:
    g = S[S.state == s_]
    stat(g, s_)

# 右尾捕获：把两类中 fwd120 排名前 10% 的样例拎出来看特征
print("\n== 右尾赢家画像（fwd120 Top10%）==")
d = S.dropna(subset=["fwd120"])
if len(d) >= 20:
    top = d[d.fwd120 >= d.fwd120.quantile(0.9)]
    print(f"Top10%样本={len(top)}")
    print("状态分布:", top.state.value_counts().to_dict())
    num_cols = ["fwd20", "fwd120", "alpha_hvt", "trend_d", "rs_d", "t120", "entry"]
    print("均值:", top[num_cols].mean().round(1).to_dict())
    print("Top10%中来自趋势确认类占比:", round(top.state.isin(["BREAKOUT_CONFIRM", "SECOND_WAVE", "RE_EXPANSION"]).mean() * 100, 1))
    print("Top10%中来自吸收类占比:", round(top.state.isin(["ABSORPTION", "DRYUP"]).mean() * 100, 1))
    # 基准占比（用于比较密度）
    base_c = d.state.isin(["BREAKOUT_CONFIRM", "SECOND_WAVE", "RE_EXPANSION"]).mean() * 100
    print(f"全样本中趋势确认类占比: {base_c:.1f}%  → Top10%占比与之对比可看密度差异")
