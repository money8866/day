"""调试：检查ETF成份股API返回的列名"""
import os
import tushare as ts

# Read token from .env
with open('.env') as f:
    for line in f:
        line = line.strip()
        if line.startswith('TUSHARE_TOKEN='):
            token = line.split('=', 1)[1].strip()
            break

os.environ['TUSHARE_TOKEN'] = token
pro = ts.pro_api(token)

# 沪市ETF成份股
print("=== 588000.SH (etf_sh_cons) ===")
d = pro.etf_sh_cons(ts_code='588000.SH')
print(f'columns: {d.columns.tolist()}')
print(f'shape: {d.shape}')
print(d.head(5).to_string())

print()

# 深市ETF成份股
print("=== 159915.SZ (etf_sz_cons) ===")
d2 = pro.etf_sz_cons(ts_code='159915.SZ')
print(f'columns: {d2.columns.tolist()}')
print(f'shape: {d2.shape}')
print(d2.head(5).to_string())
