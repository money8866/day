# -*- coding: utf-8 -*-
"""基于已保存样本的快速诊断：涨停基因/强主升/回调末端门槛"""
import pandas as pd
from scipy.stats import spearmanr

res = pd.read_csv(r"d:\mystock\solo\_pullback_samples.csv")
sub = res[res['rally_gain'] >= 25].copy()
sub['chg2d'] = sub['chg1'] + sub['chg_prev']
print("主升结构子集", len(sub))


def v(name, m):
    seg = sub[m]
    if len(seg) < 200:
        print("[%s] n=%d 样本不足" % (name, len(seg)))
        return
    r20, _ = spearmanr(seg['score'], seg['f20'])
    r10, _ = spearmanr(seg['score'], seg['f10'])
    r5, _ = spearmanr(seg['score'], seg['f5'])
    print("[%s] n=%-6d 20日胜率=%.1f%%  5日=%+.2f%% 10日=%+.2f%% 20日=%+.2f%%  rho(5/10/20)=%+.3f/%+.3f/%+.3f"
          % (name, len(seg), 100 * (seg['f20'] > 0).mean(),
             seg['f5'].mean(), seg['f10'].mean(), seg['f20'].mean(), r5, r10, r20))


print()
v("强主升(涨幅>=50)", sub['rally_gain'] >= 50)
v("强主升+经典回撤", (sub['rally_gain'] >= 50) & (sub['pullback'] >= 8) & (sub['pullback'] <= 25))
v("强主升+阳线+缩量", (sub['rally_gain'] >= 50) & sub['yang'] & (sub['vol5'] < 1.0))
v("回调末端(近2日转跌但今日阳)", (sub['chg2d'] < 0) & sub['yang'])
v("回调末端+缩量", (sub['chg2d'] < 0) & sub['yang'] & (sub['vol5'] < 1.0))
v("回调末端+未创新低", (sub['chg2d'] < 0) & sub['yang'] & (sub['low_today'] >= sub['low_prev']))
v("深度回撤25~40+阳线+缩量", (sub['pullback'] > 25) & (sub['pullback'] <= 40) & sub['yang'] & (sub['vol5'] < 1.0))
v("近2日跌超10%后企稳", (sub['chg2d'] <= -10) & sub['yang'] & (sub['vol5'] < 1.0))
