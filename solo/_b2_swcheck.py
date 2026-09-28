# -*- coding: utf-8 -*-
"""B2：651 条兜底类 EXCLUDE 的审计侧口径复核（stock_basic 行业 vs 申万行业）"""
import csv, json, collections, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import theme_trend_sentiment_score as tts
import theme_mapping_v3_audit as aud

sw_stock, _ = tts.get_sw_members()
cfg = json.load(open(r'theme_kg_v3\theme_kg_v3\config\theme_config.json', encoding='utf-8'))
by_name = {v.get('name_cn'): (k, v) for k, v in cfg.items()
           if isinstance(v, dict) and not k.startswith('_')}

rows = list(csv.DictReader(open('report_daily/theme_mapping_v3_20260924.csv',
                               encoding='utf-8-sig', newline='')))
prod = {(r['主题'], r['股票代码']) for r in
        csv.DictReader(open('report_daily/theme_stock_map_v2_20260924.csv',
                            encoding='utf-8-sig', newline=''))}
res = [r for r in rows if r['classification'] == 'EXCLUDE'
       and (r['theme'], r['ts_code']) in prod]

cnt, tot = collections.Counter(), collections.Counter()
for r in res:
    tot[r['theme']] += 1
    key, c = by_name.get(r['theme'], (None, None))
    if not c:
        continue
    sws = sw_stock.get(r['ts_code'], [])
    if any(aud.match_theme_industry(c, s, key) for s in sws):
        cnt[r['theme']] += 1

print(f'兜底类 EXCLUDE（在产）合计 {len(res)} 条')
print(f'按申万口径复核可命中主题行业的：{sum(cnt.values())} 条 '
      f'（{sum(cnt.values())/max(len(res),1)*100:.1f}%）→ 审计侧行业口径错配，非真污染')
print('\n按主题（命中/总数）:')
for t, n in tot.most_common():
    if n >= 5:
        print(f'  {t}: {cnt[t]}/{n}')
