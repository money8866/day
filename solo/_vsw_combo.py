# -*- coding: utf-8 -*-
"""临时：VSW 组合过滤效果验证（离线，基于已跟踪记录）"""
import sys
sys.path.insert(0, r'd:\mystock\solo')
sys.stdout.reconfigure(encoding='utf-8')
from _vsw_class_analysis import load  # noqa: E402

recs = load()


def agg(sub, label):
    n = len(sub)
    if not n:
        print(f"  {label:<44} n=0")
        return
    def s(k):
        v = [x[k] for x in sub if x[k] is not None]
        if not v:
            return '--'
        return f"{sum(v)/len(v):+.2f}%(胜{sum(1 for a in v if a>0)/len(v)*100:.0f}%,n={len(v)})"
    stopn = sum(1 for x in sub if x['status'] == 'STOP_HIT')
    dd = [x['mdd'] for x in sub if x['mdd'] is not None]
    print(f"  {label:<44} n={n:<4} 1d:{s('r1'):<22} 3d:{s('r3'):<22} 5d:{s('r5'):<22} "
          f"止损{stopn/n*100:.0f}%" + (f" maxDD{sum(dd)/len(dd):.1f}%" if dd else ''))


print('=' * 130)
print('VSW 组合过滤效果验证（基准：全部 %d 条）' % len(recs))
print('=' * 130)
agg(recs, '【基准】全部')

print('\n── 单因子（回忆用） ──')
agg([x for x in recs if 0 <= (x['dma20'] if x['dma20'] is not None else 999) < 3], '距MA20 0~3%（甜蜜区）')
agg([x for x in recs if 'F2优先' not in x['sig']], '非F2优先')
agg([x for x in recs if x['risk'] == 'Medium'], 'T1Risk=Medium')
agg([x for x in recs if x['action'].endswith('下蹲买点')], '仅下蹲买点（剔扩大）')
agg([x for x in recs if '主题效应' in x['sig']], '主题效应')

print('\n── 组合 ──')
base = [x for x in recs if x['action'].endswith('下蹲买点') and 'F2优先' not in x['sig']]
agg(base, '① 下蹲买点 ∩ 非F2优先')
agg([x for x in base if x['risk'] == 'Medium'], '① ∩ T1Risk=Medium')
agg([x for x in base if 0 <= (x['dma20'] if x['dma20'] is not None else 999) < 3],
    '① ∩ 距MA20 0~3%')
agg([x for x in base if x['risk'] == 'Medium'
     and 0 <= (x['dma20'] if x['dma20'] is not None else 999) < 3],
    '① ∩ T1Risk=Medium ∩ 距MA20 0~3%')
agg([x for x in base if x['risk'] == 'Medium'
     and 0 <= (x['dma20'] if x['dma20'] is not None else 999) < 3
     and x['chg5'] is not None and x['chg5'] < 0],
    '① ∩ Medium ∩ 距MA20 0~3% ∩ 5日涨幅<0')

print('\n── 剔除口径（保留 vs 剔除） ──')


def keep(x):
    """建议剔除：扩大档(=待确认半仓)、F2优先、T1Risk High/Extreme、距MA20>=6、量能分<85"""
    if x['action'].endswith('待确认(半仓)'):
        return False
    if 'F2优先' in x['sig']:
        return False
    if x['risk'] in ('High', 'Extreme'):
        return False
    if x['dma20'] is not None and x['dma20'] >= 6:
        return False
    return True


agg([x for x in recs if keep(x)], '【建议剔除后保留】')
agg([x for x in recs if not keep(x)], '【建议剔除掉的部分】')
