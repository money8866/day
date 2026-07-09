import theme_trend_sentiment_score as ts
import json
from collections import defaultdict

dc_df = ts.get_dc_members()
stock_basic = ts.get_stock_basic()

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

ths_stock, ths_ind = ts.get_ths_members()
ths_ss = ths_ind.get('固态电池', set())

d = json.load(open('d:/mystock/cache_daily/theme_stock_map_latest.json', encoding='utf-8'))
in_result = set(s['code'] for s in d['themes'].get('固态电池', []))

all_candidates = set(ss_codes) | set(ths_ss)
missing = all_candidates - in_result

print(f"东财固态电池概念: {len(ss_codes)} 只")
print(f"同花顺固态电池概念: {len(ths_ss)} 只")
print(f"并集候选: {len(all_candidates)} 只")
print(f"已入选: {len(in_result)} 只")
print(f"遗漏: {len(missing)} 只")
print()

themes = json.load(open('theme.json', encoding='utf-8'))['HOT_THEMES']
ss_cfg = themes.get('固态电池', {})
print("theme.json 固态电池配置:")
print(f"  industry: {ss_cfg.get('industry', '(无)')}")
print(f"  concept: {ss_cfg.get('concept', '(无)')}")
print(f"  keywords: {ss_cfg.get('keywords', '(无)')}")
print(f"  exclude_keywords: {ss_cfg.get('exclude_keywords', '(无)')}")
print()

excluded_by_st = 0
excluded_by_bj = 0
excluded_by_exclude_kw = 0
excluded_by_industry = 0
excluded_by_irs = 0

for code in sorted(missing):
    name = name_map.get(code, code)
    if name.startswith('ST') or name.startswith('*ST'):
        excluded_by_st += 1
        continue
    if code.startswith('8') or code.startswith('920') or code.startswith('4'):
        excluded_by_bj += 1
        continue
    ind = sbi.get(code, '')
    dc_con = stock_concepts.get(code, [])
    ths = ths_stock.get(code, [])
    mainbiz = json.load(open('d:/mystock/cache_daily/stock_company_mainbiz.json', encoding='utf-8')).get(code, '')
    keywords = ['固态电池', '全固态', '半固态', '硫化物', '固态电解质', '锂金属负极']
    hit_kw = [k for k in keywords if k in mainbiz]
    exclude_kw = ss_cfg.get('exclude_keywords', [])
    hit_excl = [k for k in exclude_kw if k in mainbiz]
    if hit_excl:
        excluded_by_exclude_kw += 1
        continue
    excluded_by_irs += 1

print("遗漏原因分类:")
print(f"  ST股被过滤: {excluded_by_st}")
print(f"  北交所被过滤: {excluded_by_bj}")
print(f"  exclude_keywords命中: {excluded_by_exclude_kw}")
print(f"  IRS评分<50被过滤: {excluded_by_irs}")
print()

print("IRS<50被过滤的股票(主板/创业板/科创板):")
for code in sorted(missing):
    name = name_map.get(code, code)
    if name.startswith('ST') or name.startswith('*ST'):
        continue
    if code.startswith('8') or code.startswith('920') or code.startswith('4'):
        continue
    ind = sbi.get(code, '')
    dc_con = stock_concepts.get(code, [])
    ths = ths_stock.get(code, [])
    mainbiz = json.load(open('d:/mystock/cache_daily/stock_company_mainbiz.json', encoding='utf-8')).get(code, '')
    keywords = ['固态电池', '全固态', '半固态', '硫化物', '固态电解质', '锂金属负极']
    hit_kw = [k for k in keywords if k in mainbiz]
    exclude_kw = ss_cfg.get('exclude_keywords', [])
    hit_excl = [k for k in exclude_kw if k in mainbiz]
    if hit_excl:
        continue
    print(f"  {code} {name} 行业={ind}")
    print(f"    东财概念含固态电池={'固态电池' in dc_con} 同花顺含={'固态电池' in ths}")
    print(f"    主营命中关键词={hit_kw}")
    print(f"    主营={mainbiz[:80]}")
