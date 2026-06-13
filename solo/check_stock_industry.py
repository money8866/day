import sys
sys.path.insert(0, '.')
import tushare as ts
pro = ts.pro_api()
df = pro.stock_basic(ts_code='300570.SZ')
print(f'太辰光行业: {df.iloc[0]["industry"]}')