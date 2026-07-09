"""全主题遗漏成份股诊断脚本。

对每个主题：
1. 获取东财+同花顺概念股并集
2. 减去已入选的
3. 排除ST、北交所
4. 分析剩余的IRS得分和匹配情况
5. 输出到JSON供人工筛选
"""
import json
import os
import sys
from collections import defaultdict

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

import theme_trend_sentiment_score as ts

print("加载数据...")
hot_themes = json.load(open(os.path.join(BASE_DIR, 'theme.json'), encoding='utf-8'))['HOT_THEMES']
dc_df = ts.get_dc_members()
stock_basic = ts.get_stock_basic()
mainbiz = json.load(open('d:/mystock/cache_daily/stock_company_mainbiz.json', encoding='utf-8'))
sw_data = ts.get_sw_members()
ths_data = ts.get_ths_members()

name_map = dict(zip(stock_basic['ts_code'], stock_basic['name']))
sbi = dict(zip(stock_basic['ts_code'], stock_basic['industry']))

stock_concepts_dc = defaultdict(list)
stock_dc_industries = defaultdict(list)
for _, r in dc_df.iterrows():
    cc = r['con_code']
    bn = r['concept_name']
    if cc and bn:
        if r.get('is_industry', False):
            stock_dc_industries[cc].append(bn)
        else:
            stock_concepts_dc[cc].append(bn)

sw_stock, sw_ind = sw_data
ths_stock, ths_ind = ths_data

d = json.load(open('d:/mystock/cache_daily/theme_stock_map_latest.json', encoding='utf-8'))
result_themes = d['themes']

output = {}
total_missing = 0

for theme_name, cfg in sorted(hot_themes.items()):
    concept_list = cfg.get('concept', [])
    current_stocks = result_themes.get(theme_name, [])
    current_codes = set(s['code'] for s in current_stocks)

    # 东财概念候选
    dc_candidates = set()
    for code, cons in stock_concepts_dc.items():
        for tc in concept_list:
            for dc_c in cons:
                if tc == dc_c or tc in dc_c:
                    dc_candidates.add(code)
                    break

    # 同花顺概念候选
    ths_candidates = set()
    for concept_name in concept_list:
        if concept_name in ths_ind:
            ths_candidates |= ths_ind[concept_name]

    all_candidates = dc_candidates | ths_candidates
    missing = all_candidates - current_codes

    # 排除ST和北交所
    real_missing = []
    for code in missing:
        name = name_map.get(code, code)
        if name.startswith('ST') or name.startswith('*ST'):
            continue
        if code.startswith('8') or code.startswith('920') or code.startswith('4'):
            continue
        real_missing.append(code)

    if not real_missing:
        continue

    total_missing += len(real_missing)
    print(f"\n[{theme_name}] 当前{len(current_codes)}只, 候选{len(all_candidates)}只, 遗漏{len(real_missing)}只")

    missing_info = []
    for code in sorted(real_missing):
        name = name_map.get(code, code)
        ind = sbi.get(code, '')
        dc_con = stock_concepts_dc.get(code, [])
        ths_con = ths_stock.get(code, [])
        mb = mainbiz.get(code, '')[:100]
        keywords = cfg.get('keywords', [])
        hit_kw = [k for k in keywords if k in mb]
        exclude_kw = cfg.get('exclude_keywords', [])
        hit_excl = [k for k in exclude_kw if k in mb]

        missing_info.append({
            'code': code,
            'name': name,
            'industry': ind,
            'dc_concepts': [c for c in dc_con if any(tc in c for tc in concept_list)][:3],
            'ths_concepts': [c for c in ths_con if any(tc in c for tc in concept_list)][:3],
            'hit_keywords': hit_kw,
            'hit_exclude': hit_excl,
            'mainbiz': mb,
        })

    output[theme_name] = {
        'current_count': len(current_codes),
        'missing_count': len(real_missing),
        'missing': missing_info,
    }

output_path = os.path.join(BASE_DIR, 'theme_missing_stocks.json')
with open(output_path, 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"\n{'='*60}")
print(f"总遗漏: {total_missing} 只")
print(f"诊断结果已保存: {output_path}")
