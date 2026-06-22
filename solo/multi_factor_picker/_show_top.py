# -*- coding: utf-8 -*-
import pandas as pd
df = pd.read_csv('output/bullscore_20260622_140347.csv')

# === BullScore TOP20（纯基本面质量）===
print('='*90)
print('BullScore  TOP20（按 bull_score 降序，8子因子加权的纯基本面质量）')
print('='*90)
top_bull = df.sort_values('bull_score', ascending=False).head(20)
for i, (_, r) in enumerate(top_bull.iterrows(), 1):
    print(f"{i:>2}. {r['name']:<8} chain={r['theme']:<14} Bull={r['bull_score']:>6.1f} Final={r['final_score']:>6.1f} lv={r['bull_level']}")
    print(f"     产业景气={r['industry_demand_score']:>5.1f} 技术壁垒={r['tech_barrier_score']:>5.1f} 订单爆发={r['order_explosion_score']:>5.1f} 盈利质量={r['earnings_quality_score']:>5.1f} 龙头={r['leader_score']:>5.1f} 预期差={r['expectation_score']:>5.1f} 机构={r['institution_score']:>5.1f} 市值弹性={r['marketcap_score']:>5.1f}")

print()
print('='*90)
print('FinalScore TOP15（80%基本面 + 20%主题情绪加成的最终决策分）')
print('='*90)
top_final = df.sort_values('final_score', ascending=False).head(15)
for i, (_, r) in enumerate(top_final.iterrows(), 1):
    print(f"{i:>2}. {r['name']:<8} chain={r['theme']:<14} Bull={r['bull_score']:>6.1f} 主题={r['theme_score']:>5.1f} Final={r['final_score']:>6.1f} lv={r['bull_level']}")
