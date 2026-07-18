"""分析100亿以上未匹配主题的股票行业分布（修正版）"""
import sys
sys.path.insert(0, '.')
import theme_trend_sentiment_score as theme_ts
import json
import pandas as pd
from collections import Counter
import os
import tushare as ts
import glob

# 加载theme
themes = theme_ts.load_theme_json()
print(f'当前主题数: {len(themes)}')

# 获取东财板块成员
dc_df = theme_ts.get_dc_members()

# 获取股票基本信息
stock_basic = theme_ts.get_stock_basic()

# 匹配主题
result = theme_ts.match_theme_stocks(themes, dc_df, stock_basic)
if isinstance(result, tuple):
    matched_dict = result[0]
else:
    matched_dict = result

# 收集所有匹配到的股票
matched_codes = set()
for tname, stocks in matched_dict.items():
    if isinstance(stocks, dict):
        matched_codes.update(stocks.keys())
    elif isinstance(stocks, list):
        matched_codes.update(stocks)

print(f'已匹配股票总数: {len(matched_codes)}')

# 获取市值数据 - 优先从缓存读取
cache_files = sorted(glob.glob('cache/daily_basic_*.csv'), reverse=True)
if cache_files:
    daily_basic = pd.read_csv(cache_files[0])
    print(f'从缓存读取市值数据: {cache_files[0]}, {len(daily_basic)}行')
else:
    token = os.environ.get('TUSHARE_TOKEN', '')
    pro = ts.pro_api(token)
    daily_basic = pro.daily_basic(trade_date='20260717')
    print(f'从tushare获取市值数据: {len(daily_basic)}行')

# 100亿以上 = 100e4 万元
big = daily_basic[daily_basic['total_mv'] >= 100e4].copy()
print(f'100亿以上股票数: {len(big)}')

# 未匹配的100亿以上股票
unmatched = big[~big['ts_code'].isin(matched_codes)].copy()
print(f'100亿以上未匹配股票数: {len(unmatched)}')

# 构建股票 -> 东财行业映射（使用con_code）
stock_industries = {}
for _, row in dc_df.iterrows():
    if row['is_industry']:
        code = row['con_code']  # 关键：用con_code
        if code not in stock_industries:
            stock_industries[code] = []
        stock_industries[code].append(row['concept_name'])

print(f'\n有东财行业信息的股票数: {len(stock_industries)}')

# 统计未匹配股票的东财行业分布
ind_counter = Counter()
unmatched_with_ind_count = 0
for code in unmatched['ts_code']:
    if code in stock_industries:
        unmatched_with_ind_count += 1
        for ind in stock_industries[code]:
            ind_counter[ind] += 1

print(f'未匹配股票中有东财行业信息的: {unmatched_with_ind_count}/{len(unmatched)}')

print('\n=== TOP40 未匹配100亿+股票的东财行业分布 ===')
for ind, cnt in ind_counter.most_common(40):
    print(f'{ind}: {cnt}')

# 输出未匹配股票列表
print('\n=== 部分未匹配股票示例（前60只，按市值降序）===')
unmatched_sorted = unmatched.sort_values('total_mv', ascending=False)
unmatched_with_name = unmatched_sorted.merge(stock_basic[['ts_code', 'name']], on='ts_code', how='left')
for _, row in unmatched_with_name.head(60).iterrows():
    inds = stock_industries.get(row['ts_code'], [])
    print(f'{row["ts_code"]} {row["name"]} | 市值{row["total_mv"]/1e4:.0f}亿 | 东财行业: {", ".join(inds)}')
