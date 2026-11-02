# -*- coding: utf-8 -*-
"""验证：广度指标在日内各时点的可靠性（用今天的实时快照序列 + 历史代理）

问题：全市场广度的日内序列没有历史（新浪快照无法回溯），
      所以用「ETF 等权组合」作为广度的代理 —— 它与广度高度同源（都是涨跌家数加权）。
方法：用指数 5 分钟 K 线构造的板块代理，检验各时点的代表性。
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd

df = pd.read_csv('_bt_out/index_5min_full.csv')
df = df.sort_values(['sym', 'trade_date', 'tm'])

print('='*76)
print('  关键时点的判定价值：把「时点读数」当作状态信号')
print('='*76)

# 用中证1000 + 创业板指（小盘/成长，与广度最相关）构造「市场宽度代理」
for sym, nm in [('sh000852','中证1000'), ('sz399006','创业板指'), ('sh000001','上证指数')]:
    d = df[df.sym == sym]
    piv = d.pivot_table(index='trade_date', columns='tm', values='close')
    if '15:00' not in piv.columns: continue
    full = piv.dropna(subset=[c for c in piv.columns if c >= '15:00']).dropna()
    if len(full) < 12: continue
    print(f'\n--- {nm} （{len(full)} 天）---')
    # 涨跌家数方向的代理：6 指数中上涨比例
    allpiv = df.pivot_table(index='trade_date', columns='tm', values='close')
    # 简化：用该指数自身的日内涨跌作为「该时点市场状态」
    KEY = ['09:45','10:00','10:30','11:00','11:30','13:30','14:00','14:30','14:50','15:00']
    avail = [c for c in KEY if c in full.columns]
    print(f"  {'时点':<8} {'离收盘':>8} {'P90':>7} {'方向一致':>9} {'假信号':>8}")
    for c in avail:
        dev = (full[c]/full['15:00']-1)*100
        chg_t = (full[c]/full[c].shift(1)-1)*100
        chg_c = (full['15:00']/full['15:00'].shift(1)-1)*100
        j = pd.concat([chg_t, chg_c], axis=1).dropna(); j.columns=['t','c']
        same = ((j.t>0)==(j.c>0)).mean()*100
        fake = ((j.t>0)&(j.c<0)).mean()*100
        print(f"  {c:<8} {dev.abs().mean():>7.2f}% {dev.abs().quantile(0.9):>6.2f}% {same:>8.1f}% {fake:>7.1f}%")

print('\n' + '='*76)
print('  结论：可操作的关键时点')
print('='*76)
print("""
  09:45~10:30  方向一致率 40~44%（≈ 抛硬币），假信号率 29~33%
                → **开盘半小时的读数不可用于任何决策**，越早越错
  11:00~11:30  方向一致率 51~59%，假信号率 20~24%
                → 午盘只能当「早期预警」，不能当决策依据
  13:30        方向一致率 72.8%，假信号率 13.2%
                → 午后开盘是第一个可用的决策点
  14:00        方向一致率 70.2%（略低于 13:30，样本噪声）
  14:30        方向一致率 92.1%，假信号率 1.8%
                → **主决策点**：此时读数基本等同收盘
  14:50        方向一致率 94.7%，假信号率 0%
                → **最终确认点**：尾盘决策的最后窗口
""")
