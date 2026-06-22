# -*- coding: utf-8 -*-
"""对比新旧算法下巨化 vs 三美股份的分值变化"""
import pandas as pd, sys
sys.path.insert(0, '.')

# 新算法结果
new_df = pd.read_csv('output/bullscore_20260622_142716.csv')
# 旧算法结果
old_df = pd.read_csv('output/bullscore_20260622_140347.csv')

stocks = ['巨化股份', '三美股份']
print('='*100)
print(f'{"":<30}  {"旧Bull":>8} {"新Bull":>8} {"差值":>7}  {"旧Final":>8} {"新Final":>8} {"差值":>7}')
print('='*100)
for name in stocks:
    o = old_df[old_df['name'] == name].iloc[0]
    n = new_df[new_df['name'] == name].iloc[0]
    delta_bull = n.bull_score - o.bull_score
    delta_final = n.final_score - o.final_score
    print(f'{name:<30}  {o.bull_score:>8.1f} {n.bull_score:>8.1f} {delta_bull:>+7.1f}  {o.final_score:>8.1f} {n.final_score:>8.1f} {delta_final:>+7.1f}')
print()
print('='*100)
print('子因子分差对比（新 - 旧）')
print('='*100)
sub_cols = ['industry_demand_score','tech_barrier_score','order_explosion_score',
            'earnings_quality_score','leader_score','expectation_score',
            'institution_score','marketcap_score']
labels = ['产业景气','技术壁垒','订单爆发','盈利质量','龙头地位','预期差','机构持仓','市值弹性']
print(f'{"子因子":<30} {"旧_巨化":>9} {"新_巨化":>9} {"差":>7}  {"旧_三美":>9} {"新_三美":>9} {"差":>7}')
print('-'*100)
for col, label in zip(sub_cols, labels):
    o_j = float(old_df[old_df['name']=='巨化股份'].iloc[0][col])
    n_j = float(new_df[new_df['name']=='巨化股份'].iloc[0][col])
    o_s = float(old_df[old_df['name']=='三美股份'].iloc[0][col])
    n_s = float(new_df[new_df['name']=='三美股份'].iloc[0][col])
    dj = n_j - o_j
    ds = n_s - o_s
    print(f'{label:<30}  {o_j:>8.1f} {n_j:>8.1f} {dj:>+7.1f}  {o_s:>8.1f} {n_s:>8.1f} {ds:>+7.1f}')

print()
print('='*100)
print('市值与机构持仓分项对比')
print('='*100)
jh_new = new_df[new_df['name']=='巨化股份'].iloc[0]
sm_new = new_df[new_df['name']=='三美股份'].iloc[0]
print(f'巨化股份: 市值={float(jh_new.market_cap)/1e8:.0f}亿 (区间={jh_new.get("cap_range","?")}) 机构覆盖={jh_new.analyst_count}家 预期分={jh_new.analyst_expectation_score}')
print(f'三美股份: 市值={float(sm_new.market_cap)/1e8:.0f}亿 (区间={sm_new.get("cap_range","?")}) 机构覆盖={sm_new.analyst_count}家 预期分={sm_new.analyst_expectation_score}')
print()
print('='*100)
print('新算法 BullScore TOP15')
print('='*100)
top15 = new_df.sort_values('bull_score', ascending=False).head(15)
for i, (_, r) in enumerate(top15.iterrows(), 1):
    jh_marker = ' ★' if r['name'] in stocks else '  '
    print(f'{i:>2}.{jh_marker} {r["name"]:<8} Bull={r["bull_score"]:>6.1f} Final={r["final_score"]:>6.1f}  订单={r["order_explosion_score"]:>5.1f} 盈利={r["earnings_quality_score"]:>5.1f} 龙头={r["leader_score"]:>5.1f} 机构={r["institution_score"]:>5.1f} 市值={r["marketcap_score"]:>5.1f}')
print()
print('='*100)
print('新算法 FinalScore TOP15')
print('='*100)
top_final = new_df.sort_values('final_score', ascending=False).head(15)
for i, (_, r) in enumerate(top_final.iterrows(), 1):
    jh_marker = ' ★' if r['name'] in stocks else '  '
    print(f'{i:>2}.{jh_marker} {r["name"]:<8} chain={r["theme"]:<14} Bull={r["bull_score"]:>6.1f} Final={r["final_score"]:>6.1f} lv={r["bull_level"]}')
