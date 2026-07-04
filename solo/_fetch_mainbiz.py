# -*- coding: utf-8 -*-
"""获取全市场主营业务数据并缓存"""
import tushare as ts
import json
import time

pro = ts.pro_api()

result = {}
for exchange in ['SSE', 'SZSE']:
    try:
        df = pro.stock_company(exchange=exchange, fields='ts_code,main_business')
        print(f"{exchange}: {len(df)} 只")
        for _, r in df.iterrows():
            mb = r['main_business']
            result[r['ts_code']] = mb if mb and str(mb) != 'nan' else ''
        time.sleep(0.15)
    except Exception as e:
        print(f"{exchange} 获取失败: {e}")

print(f"总计: {len(result)} 只")
out_path = r'd:\mystock\cache_daily\stock_company_mainbiz.json'
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False)
print(f"已保存到 {out_path}")

# 打印几个示例
samples = ['002028.SZ', '300459.SZ', '600556.SH', '603018.SH', '002714.SZ', '002821.SZ']
for code in samples:
    if code in result:
        print(f"  {code}: {result[code][:80]}")
