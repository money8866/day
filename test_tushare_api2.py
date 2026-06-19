import sys
sys.path.insert(0, r'C:\Users\kongx\AppData\Local\Programs\Python\Python313\Lib\site-packages')
import tushare as ts
ts.set_token('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')
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
for dt in ['20260618','20260617','20260616','20260613','20260612']:
    try:
        df = pro.daily_basic(ts_code='000960.SZ', trade_date=dt, fields='ts_code,trade_date,pe,pb,ps')
        if len(df) > 0:
            print(f"  日期{dt}: PE={df.iloc[0]['pe']} PB={df.iloc[0]['pb']}")
            break
    except Exception as e:
        print(f"  日期{dt}失败: {e}")
