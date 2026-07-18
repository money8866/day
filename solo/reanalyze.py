"""重新分析未匹配股票分布"""
import sys
sys.path.insert(0, '.')
import theme_trend_sentiment_score as theme_ts
import json
import pandas as pd
from collections import Counter
import os
import tushare as ts
import glob

themes = theme_ts.load_theme_json()
print(f'当前主题数: {len(themes)}')

dc_df = theme_ts.get_dc_members()
stock_basic = theme_ts.get_stock_basic()

result = theme_ts.match_theme_stocks(themes, dc_df, stock_basic)
matched_dict = result[0] if isinstance(result, tuple) else result

matched_codes = set()
for tname, stocks in matched_dict.items():
    if isinstance(stocks, dict):
        matched_codes.update(stocks.keys())

print(f'已匹配股票总数: {len(matched_codes)}')

# 获取市值
cache_files = sorted(glob.glob('cache/daily_basic_*.csv'), reverse=True)
if cache_files:
    daily_basic = pd.read_csv(cache_files[0])
else:
    token = os.environ.get('TUSHARE_TOKEN', '')
    pro = ts.pro_api(token)
    daily_basic = pro.daily_basic(trade_date='20260717')

big = daily_basic[daily_basic['total_mv'] >= 100e4].copy()
print(f'100亿以上股票数: {len(big)}')

unmatched = big[~big['ts_code'].isin(matched_codes)].copy()
print(f'100亿以上未匹配股票数: {len(unmatched)}')

# 构建股票 -> 东财行业映射
stock_industries = {}
for _, row in dc_df.iterrows():
    if row['is_industry']:
        code = row['con_code']
        if code not in stock_industries:
            stock_industries[code] = []
        stock_industries[code].append(row['concept_name'])

# 统计未匹配股票的东财行业分布
ind_counter = Counter()
for code in unmatched['ts_code']:
    if code in stock_industries:
        for ind in stock_industries[code]:
            ind_counter[ind] += 1

print('\n=== TOP30 未匹配100亿+股票的东财行业分布 ===')
for ind, cnt in ind_counter.most_common(30):
    print(f'{ind}: {cnt}')

# 输出未匹配股票列表（前40只）
print('\n=== 未匹配100亿+股票示例（按市值降序，前50只）===')
unmatched_sorted = unmatched.sort_values('total_mv', ascending=False)
unmatched_with_name = unmatched_sorted.merge(stock_basic[['ts_code', 'name']], on='ts_code', how='left')
for _, row in unmatched_with_name.head(50).iterrows():
    inds = stock_industries.get(row['ts_code'], [])
    print(f'{row["ts_code"]} {row["name"]} | 市值{row["total_mv"]/1e4:.0f}亿 | 东财行业: {", ".join(inds)}')
