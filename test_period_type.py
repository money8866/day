import json, sys, time
sys.path.insert(0, r'C:\Users\kongx\AppData\Local\Programs\Python\Python313\Lib\site-packages')
import tushare as ts

ts.set_token('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')
pro = ts.pro_api()

# 验证: 001389 广合科技
code = '001389.SZ'
print("=== {} ===".format(code))

# period_type=1 (累计)
df1 = pro.income(ts_code=code, fields='ts_code,end_date,total_revenue', period_type='1')
df1 = df1.sort_values('end_date', ascending=False)
print("period_type=1 (累计):")
for _, r in df1.head(6).iterrows():
    print("  {} : {:.2f}亿".format(r['end_date'], r['total_revenue']/1e8 if r['total_revenue'] else 0))

# period_type=2 (单季度)
df2 = pro.income(ts_code=code, fields='ts_code,end_date,total_revenue', period_type='2')
df2 = df2.sort_values('end_date', ascending=False)
print("\nperiod_type=2 (单季度):")
for _, r in df2.head(6).iterrows():
    print("  {} : {:.2f}亿".format(r['end_date'], r['total_revenue']/1e8 if r['total_revenue'] else 0))
