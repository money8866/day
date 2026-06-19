import sys
sys.path.insert(0, r'C:\Users\kongx\AppData\Local\Programs\Python\Python313\Lib\site-packages')
import tushare as ts
ts.set_token('bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d')
pro = ts.pro_api()

# 测试1: income
print("=== 测试 income ===")
try:
    df = pro.income(ts_code='000960.SZ', fields='ts_code,end_date,total_revenue,n_income_attr_p', period_type='1')
    print(df)
    print(f"行数: {len(df)}")
except Exception as e:
    print(f"失败: {e}")

# 测试2: fina_indicator
print("\n=== 测试 fina_indicator ===")
try:
    df = pro.fina_indicator(ts_code='000960.SZ', fields='ts_code,end_date,roe,roe_waa,grossprofit_margin')
    print(df.head(4))
except Exception as e:
    print(f"失败: {e}")

# 测试3: daily_basic
print("\n=== 测试 daily_basic ===")
try:
    df = pro.daily_basic(ts_code='000960.SZ', trade_date='20260618', fields='ts_code,pe,pb,ps')
    print(df)
except Exception as e:
    print(f"失败: {e}")

# 测试4: 用最新交易日期
print("\n=== 测试 daily_basic 最后交易日 ===")
try:
    df = pro.daily_basic(ts_code='000960.SZ', trade_date='20260613', fields='ts_code,pe,pb,ps')
    print(df)
except Exception as e:
    print(f"失败: {e}")
