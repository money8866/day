import sys, time
sys.path.insert(0, '.')
import pandas as pd
from data_fetcher import DataFetcher
from main import load_config, get_token

config = load_config()
token = get_token(config)
fetcher = DataFetcher(token, config)

# 原来通过筛选的股票
codes = [
    '603256.SH','688127.SH','600176.SH','688008.SH','688300.SH',
    '603893.SH','300476.SZ','301377.SZ','001389.SZ','301338.SZ'
]

for code in codes:
    fin = fetcher.get_stock_financial_batch([code], start_year='2023')
    inc = fin[code]['income']
    cf = fin[code]['cashflow']
    bal = fin[code]['balance']

    if len(inc) >= 2:
        inc = inc.sort_values('end_date', ascending=False)
        curr = inc.iloc[0]
        prev = inc.iloc[1]
        curr_rev = float(curr.get('revenue', 0)) if pd.notna(curr.get('revenue')) else 0.0
        prev_rev = float(prev.get('revenue', 0)) if pd.notna(prev.get('revenue')) else 0.0
        curr_profit = float(curr.get('n_income', 0)) if pd.notna(curr.get('n_income')) else 0.0
        prev_profit = float(prev.get('n_income', 0)) if pd.notna(prev.get('n_income')) else 0.0

        rev_yoy = (curr_rev - prev_rev) / prev_rev if prev_rev > 0 else 0.0
        profit_yoy = (curr_profit - prev_profit) / prev_profit if prev_profit > 0 else 0.0

        # Capex
        capex = 0.0
        if len(cf) >= 2:
            cf_s = cf.sort_values('end_date', ascending=False)
            curr_cf = float(cf_s.iloc[0].get('cap_expend_ra', 0)) if pd.notna(cf_s.iloc[0].get('cap_expend_ra')) else 0.0
            prev_cf = float(cf_s.iloc[1].get('cap_expend_ra', 0)) if pd.notna(cf_s.iloc[1].get('cap_expend_ra')) else 0.0
            capex = (curr_cf - prev_cf) / prev_cf if prev_cf > 0 else 0.0

        print(f"{code}: 收入YoY={rev_yoy*100:.1f}%, 利润YoY={profit_yoy*100:.1f}%, Capex={capex*100:.1f}%, 年度营收={curr_rev/1e8:.1f}亿, 净利润={curr_profit/1e8:.2f}亿")
    else:
        print(f"{code}: 仅{len(inc)}期年报数据")
