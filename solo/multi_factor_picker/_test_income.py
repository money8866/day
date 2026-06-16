import sys
sys.path.insert(0, '.')
import pandas as pd
from data_fetcher import DataFetcher
from main import load_config, get_token

config = load_config()
token = get_token(config)
fetcher = DataFetcher(token, config)

# 检查年度数据
inc = fetcher.get_income('603256.SH', start_year='2023')
print(f"603256.SH income 字段: {list(inc.columns)}")
print(f"共 {len(inc)} 行")
print()
inc_sorted = inc.sort_values('end_date', ascending=False)
print("前6期数据:")
for _, row in inc_sorted.head(6).iterrows():
    rev = row.get('revenue')
    ni = row.get('n_income')
    ed = row.get('end_date')
    p = row.get('report_type')
    print(f"  end_date={ed}, report_type={p}, revenue={rev}, n_income={ni}")

# 验证年报
print("\n年报（report_type=1）:")
annual = inc_sorted[inc_sorted['report_type'] == 1]
for _, row in annual.head(4).iterrows():
    print(f"  end_date={row['end_date']}, revenue={row['revenue']}, n_income={row['n_income']}")
