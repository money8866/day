# -*- coding: utf-8 -*-
import pandas as pd
df = pd.read_csv('output/bullscore_20260622_140347.csv')
jh = df[df['name'] == '巨化股份'].iloc[0]
sm = df[df['name'] == '三美股份'].iloc[0]

print(f'巨化股份: market_cap={float(jh["market_cap"])/1e8:.1f}亿')
print(f'三美股份: market_cap={float(sm["market_cap"])/1e8:.1f}亿')
print()
print(f'巨化: 营收同比={jh["revenue_yoy"]}, 净利同比={jh["profit_yoy"]}, 合同负债={jh["contract_liability_yoy"]}, ROE={jh["roe"]}, GM={jh["gross_margin"]}')
print(f'三美: 营收同比={sm["revenue_yoy"]}, 净利同比={sm["profit_yoy"]}, 合同负债={sm["contract_liability_yoy"]}, ROE={sm["roe"]}, GM={sm["gross_margin"]}')
print()
print(f'巨化: analyst={jh["analyst_count"]}, np_growth={jh["analyst_np_growth_%"]}, buy_ratio={jh["analyst_buy_ratio_%"]}, exp_score={jh["analyst_expectation_score"]}')
print(f'三美: analyst={sm["analyst_count"]}, np_growth={sm["analyst_np_growth_%"]}, buy_ratio={sm["analyst_buy_ratio_%"]}, exp_score={sm["analyst_expectation_score"]}')
print()
print(f'巨化: 龙头子维度 rev={jh["leader_rev_share_pct"]}%, pricing={jh["leader_pricing_power_pct"]}%, rd={jh["leader_rd_barrier_pct"]}%, cf={jh["leader_cash_quality_pct"]}%')
print(f'三美: 龙头子维度 rev={sm["leader_rev_share_pct"]}%, pricing={sm["leader_pricing_power_pct"]}%, rd={sm["leader_rd_barrier_pct"]}%, cf={sm["leader_cash_quality_pct"]}%')
