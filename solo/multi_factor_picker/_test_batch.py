import tushare as ts
import time
import sys
sys.path.insert(0, '.')
from main import load_config, get_token

config = load_config()
token = get_token(config)
pro = ts.pro_api(token=token)

# Test 1: Query income WITHOUT ts_code (all stocks for a specific ann_date)
print("Test 1: 按日期查询利润表(不限股票)...")
start = time.time()
try:
    df = pro.income(start_date='20250101', end_date='20251231',
                   fields='ts_code,ann_date,end_date,report_type,n_income,total_revenue,rd_exp,total_cogs,total_profit')
    print(f"  行数: {len(df) if df is not None else 0}, 唯一股票数: {df['ts_code'].nunique() if df is not None else 0}")
    print(f"  用时: {time.time()-start:.1f}秒")
    if df is not None and len(df) > 0:
        print(f"  样例:\n{df.head(3)}")
except Exception as e:
    print(f"  失败: {e}")

print()

# Test 2: Query income with COMMA-SEPARATED batch of ts_codes
print("Test 2: 逗号分隔批量ts_code查询...")
codes = ['000001.SZ','000002.SZ','600000.SH','600519.SH','601318.SH']
start = time.time()
try:
    df2 = pro.income(ts_code=','.join(codes), start_year='2023',
                    fields='ts_code,ann_date,end_date,report_type,n_income,total_revenue,rd_exp,total_cogs')
    print(f"  行数: {len(df2) if df2 is not None else 0}, 唯一股票数: {df2['ts_code'].nunique() if df2 is not None else 0}")
    print(f"  用时: {time.time()-start:.1f}秒")
    if df2 is not None and len(df2) > 0:
        print(f"  股票分布: {df2['ts_code'].value_counts().to_dict()}")
except Exception as e:
    print(f"  失败: {e}")

print()

# Test 3: balancesheet without ts_code
print("Test 3: 按日期查询资产负债表...")
start = time.time()
try:
    df3 = pro.balancesheet(start_date='20250101', end_date='20251231',
                          fields='ts_code,ann_date,end_date,report_type,total_assets,inventories,total_hldr_eqy_exc_min_int,total_liability')
    print(f"  行数: {len(df3) if df3 is not None else 0}, 唯一股票数: {df3['ts_code'].nunique() if df3 is not None else 0}")
    print(f"  用时: {time.time()-start:.1f}秒")
except Exception as e:
    print(f"  失败: {e}")

print()

# Test 4: forecast with date range (no ts_code)
print("Test 4: 按日期查询业绩预告...")
start = time.time()
try:
    df4 = pro.forecast(start_date='20250101', end_date='20260630',
                      fields='ts_code,ann_date,end_date,type,profit_change,profit_ratio')
    print(f"  行数: {len(df4) if df4 is not None else 0}, 唯一股票数: {df4['ts_code'].nunique() if df4 is not None else 0}")
    print(f"  用时: {time.time()-start:.1f}秒")
except Exception as e:
    print(f"  失败: {e}")
