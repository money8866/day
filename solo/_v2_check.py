# -*- coding: utf-8 -*-
import json, pandas as pd

out = []
csv = pd.read_csv(r'd:\mystock\solo\report_daily\theme_scores_v2_20260806.csv', encoding='utf-8-sig')
out.append('=== V2 CSV 列 ===')
out.append('|'.join(str(c) for c in csv.columns))
out.append(f'\n=== V2 CSV 主题数: {len(csv)} ===')
for _, r in csv.iterrows():
    out.append(f"{r['rank']}: {r['theme']} | trend:{r['trend_score']} | senti:{r['sentiment_score']} | comp:{r['composite_score']} | lifecycle:{r.get('lifecycle','')} | strength:{r.get('strength_score','')} | fund:{r.get('fund_score','')} | heat:{r.get('heat_v3','')} | leader:{r.get('leader_v3_score','')} | mti:{r.get('mti','')} | mti_lv:{r.get('mti_level','')} | trade_rank:{r.get('trade_rank','')} | final_trade:{r.get('final_trade_score','')}")

with open(r'd:\mystock\cache_daily\theme_stock_map_v2_20260806.json', encoding='utf-8') as f:
    m = json.load(f)
themes = m.get('themes', {})
stocks = m.get('stocks', {})
out.append(f'\n=== V2 映射 主题数: {len(themes)}, 个股数: {len(stocks)} ===')
out.append('主题名: ' + '|'.join(themes.keys()))
out.append('\n=== 主题样例 ===')
for i, (tn, sl) in enumerate(themes.items()):
    if i >= 3:
        break
    out.append(f'{tn}: {len(sl)}只, 前5只: ' + '|'.join(s['code'] + s['name'] for s in sl[:5]))
out.append('\n=== stocks 样例 ===')
for i, (code, info) in enumerate(stocks.items()):
    if i >= 5:
        break
    out.append(f'{code}: {info}')

with open(r'd:\mystock\solo\_v2_check.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(out))
print('ok')
