import theme_trend_sentiment_score as ts
import json
from collections import defaultdict

dc_df = ts.get_dc_members()
stock_basic = ts.get_stock_basic()
mainbiz = json.load(open('d:/mystock/cache_daily/stock_company_mainbiz.json', encoding='utf-8'))

stock_concepts = defaultdict(list)
stock_dc_industries = defaultdict(list)
for _, r in dc_df.iterrows():
    cc = r['con_code']
    bn = r['concept_name']
    if cc and bn:
        if r.get('is_industry', False):
            stock_dc_industries[cc].append(bn)
        else:
            stock_concepts[cc].append(bn)

name_map = dict(zip(stock_basic['ts_code'], stock_basic['name']))
sbi = dict(zip(stock_basic['ts_code'], stock_basic['industry']))

ss_codes = []
for code, cons in stock_concepts.items():
    if '固态电池' in cons:
        ss_codes.append(code)
print(f"东财概念含固态电池的股票: {len(ss_codes)} 只")
for code in sorted(ss_codes):
    print(f"  {code} {name_map.get(code, code)} 行业={sbi.get(code, '')}")

print()

ths_stock, ths_ind = ts.get_ths_members()
if '固态电池' in ths_ind:
    ths_ss = ths_ind['固态电池']
    print(f"同花顺固态电池概念: {len(ths_ss)} 只")
    for code in sorted(ths_ss):
        print(f"  {code} {name_map.get(code, code)} 行业={sbi.get(code, '')}")
else:
    print("同花顺无固态电池概念")

print()

in_result = set()
d = json.load(open('d:/mystock/cache_daily/theme_stock_map_latest.json', encoding='utf-8'))
for s in d['themes'].get('固态电池', []):
    in_result.add(s['code'])

all_candidates = set(ss_codes) | set(ths_ind.get('固态电池', set()))
missing = all_candidates - in_result
print(f"\n所有候选({len(all_candidates)}) - 已入选({len(in_result)}) = 遗漏({len(missing)})")
for code in sorted(missing):
    name = name_map.get(code, code)
    ind = sbi.get(code, '')
    dc_con = stock_concepts.get(code, [])
    sw = []
    ths = ths_stock.get(code, [])
    mb = mainbiz.get(code, '')
    keywords = ['固态电池', '全固态', '半固态', '硫化物', '氧化物', '固态电解质', '锂金属负极', '锂硫电池']
    hit_kw = [k for k in keywords if k in mb]
    print(f"  {code} {name} 行业={ind}")
    print(f"    东财概念={dc_con[:5]}")
    print(f"    同花顺概念={ths[:5]}")
    print(f"    主营命中关键词={hit_kw}")
    print(f"    主营={mb[:120]}")
    print()
