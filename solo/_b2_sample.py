# -*- coding: utf-8 -*-
"""B2 抽样：兜底类 EXCLUDE 残留（生产映射中）Top5 主题各抽 20 条，供人工核验误杀率"""
import csv, os, random, collections
RD = 'report_daily'
AUD = os.path.join(RD, 'theme_mapping_v3_20260924.csv')
PROD = os.path.join(RD, 'theme_stock_map_v2_20260924.csv')

def load(p):
    with open(p, 'r', encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))

aud = load(AUD)
prod = {(r['主题'], r['股票代码']) for r in load(PROD)}

res = [r for r in aud
       if r['classification'] == 'EXCLUDE' and (r['theme'], r['ts_code']) in prod]
print(f'兜底类 EXCLUDE 残留（在产）合计 {len(res)} 条')
print('evidence_type 分布:', dict(collections.Counter(r['evidence_type'] for r in res)))
print('confidence 分布:', dict(collections.Counter(r['confidence'] for r in res)))
print('via 分布:', dict(collections.Counter(r['via'] for r in res).most_common(8)))
print('gate_status 分布:', dict(collections.Counter(r['gate_status'] for r in res)))
top = collections.Counter(r['theme'] for r in res).most_common(5)
print('Top5 主题:', top)
print('reason 样例:', dict(collections.Counter(r['reason'][:60] for r in res).most_common(5)))

random.seed(20260927)
for theme, _n in top:
    rows = [r for r in res if r['theme'] == theme]
    samp = random.sample(rows, min(20, len(rows)))
    print(f'\n═══ {theme}（残留 {len(rows)} 条，抽 {len(samp)}）═══')
    for r in samp:
        print(f"{r['ts_code']}\t{r['name']}\tvia={r['via']}\t行业={r['industry']}")
        print(f"   evidence={r['evidence']}")
        print(f"   reason={r['reason']}")
        print(f"   主营={r['mainbiz'][:150]}")
