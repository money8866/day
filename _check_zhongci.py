# -*- coding: utf-8 -*-
import pandas as pd
df = pd.read_csv(r'C:\Users\kongx\mystock\backtest_v4_local_halfyear.csv', encoding='utf-8-sig')
rows = df[df['name']=='中瓷电子'].sort_values('date')
for _, r in rows.iterrows():
    print(f"{r['date']} 入买点={r['actual_buy']} 离卖点={r['sell']} 收益={r['return_pct']:+.2f}% 等回档={r['pullback_wait']}天 基本面={r['fin_score']} PE={r['pe']:.0f} repeat={r['repeat']} entry={r['entry_type']}")

# Also check how many times appeared (not just traded)
print()
print(f"总共出现 {len(rows)} 次交易信号")