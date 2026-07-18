"""验证主题匹配效果"""
import sys
sys.path.insert(0, '.')
import theme_trend_sentiment_score as theme_ts
import json
import pandas as pd
from collections import Counter
import os
import glob

print("=== 验证主题匹配效果 ===")

themes = theme_ts.load_theme_json()
print(f'当前主题数: {len(themes)}')

dc_df = theme_ts.get_dc_members()
stock_basic = theme_ts.get_stock_basic()

result = theme_ts.match_theme_stocks(themes, dc_df, stock_basic)
if isinstance(result, tuple):
    matched_dict = result[0]
else:
    matched_dict = result

matched_codes = set()
theme_counts = {}

for theme_name, stocks in matched_dict.items():
    if isinstance(stocks, dict):
        count = len(stocks)
        theme_counts[theme_name] = count
        matched_codes.update(stocks.keys())
    elif isinstance(stocks, list):
        count = len(stocks)
        theme_counts[theme_name] = count
        matched_codes.update(stocks)

print(f"\n已匹配股票总数: {len(matched_codes)}")

# 检查关键股票是否匹配
key_stocks = {
    "601766.SH": "中国中车",
    "300999.SZ": "金龙鱼",
    "000408.SZ": "藏格矿业",
    "300866.SZ": "安克创新",
    "001872.SZ": "招商港口",
    "601825.SH": "沪农商行",
    "600436.SH": "片仔癀",
    "000538.SZ": "云南白药",
    "002008.SZ": "大族激光",
}

print("\n=== 关键股票匹配验证 ===")
for code, name in key_stocks.items():
    matched = code in matched_codes
    if matched:
        matched_themes = [t for t, stocks in matched_dict.items() if (isinstance(stocks, dict) and code in stocks) or (isinstance(stocks, list) and code in stocks)]
        print(f"✓ {code} {name}: 匹配到 {matched_themes[:3]}")
    else:
        print(f"✗ {code} {name}: 未匹配")

# 统计各主题匹配数量(取前30)
print("\n=== 主题匹配数量排行(前30) ===")
sorted_themes = sorted(theme_counts.items(), key=lambda x: x[1], reverse=True)
for theme, count in sorted_themes[:30]:
    print(f"{theme}: {count}只")

# 分析未匹配股票
print("\n=== 分析未匹配股票 ===")
cache_files = sorted(glob.glob('cache/daily_basic_*.csv'), reverse=True)
if cache_files:
    daily_basic = pd.read_csv(cache_files[0])
    print(f'从缓存读取市值数据: {cache_files[0]}')
else:
    import tushare as ts
    token = os.environ.get('TUSHARE_TOKEN', '')
    pro = ts.pro_api(token)
    daily_basic = pro.daily_basic(trade_date='20260717')

big = daily_basic[daily_basic['total_mv'] >= 100e4].copy()
unmatched = big[~big['ts_code'].isin(matched_codes)].copy()
print(f"100亿以上未匹配股票: {len(unmatched)}只")

stock_industries = {}
for _, row in dc_df.iterrows():
    if row['is_industry']:
        code = row['con_code']
        if code not in stock_industries:
            stock_industries[code] = []
        stock_industries[code].append(row['concept_name'])

ind_counter = Counter()
for code in unmatched['ts_code']:
    if code in stock_industries:
        for ind in stock_industries[code]:
            ind_counter[ind] += 1

print("\n未匹配股票行业分布(TOP15):")
for ind, count in ind_counter.most_common(15):
    print(f"  {ind}: {count}只")

# 列出TOP20未匹配大市值股票
print("\nTOP20未匹配大市值股票:")
unmatched_sorted = unmatched.sort_values('total_mv', ascending=False)
unmatched_with_name = unmatched_sorted.merge(stock_basic[['ts_code', 'name']], on='ts_code', how='left')
for _, row in unmatched_with_name.head(20).iterrows():
    inds = stock_industries.get(row['ts_code'], [])
    print(f"  {row['ts_code']} {row['name']}: {row['total_mv']/1e4:.0f}亿 ({', '.join(inds)})")