"""检查东财行业标准名称"""
import tushare as ts
from dotenv import load_dotenv
import os

load_dotenv('d:/mystock/config/.env')
pro = ts.pro_api(os.getenv('TUSHARE_TOKEN'))

# 获取最近的交易日
cal = pro.trade_cal(exchange='', start_date='20250501', end_date='20250602')
cal = cal[cal['is_open'] == 1]
dates = cal['cal_date'].tolist()
print(f"最近交易日: {dates[-3:]}")

df = pro.dc_index(trade_date=dates[-1], idx_type='行业板块')
print(f"\n行业板块 ({len(df)} 个):")
for n in df['name'].tolist():
    print(f"  [{n}]")

# 找含"煤炭"的
for n in df['name'].tolist():
    if '煤炭' in n or '煤' in n:
        print(f"\n  含煤: [{n}]")

# 找含"钢铁"、"银行"、"保险"、"证券"
for keyword in ['钢铁', '银行', '保险', '证券', '电力', '建材', '食品', '饮料', '酒']:
    matches = [n for n in df['name'].tolist() if keyword in n]
    if matches:
        print(f"  含'{keyword}': {matches}")
