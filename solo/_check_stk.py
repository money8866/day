import sys, os
os.chdir(r'd:\mystock\solo')
sys.path.insert(0, r'd:\mystock\solo')
from tushare_quant import pro

# 测试多只股票的 stk_factor 数据
codes = ['688809.SH', '000970.SZ', '600378.SH', '300666.SZ']
for c in codes:
    df = pro.stk_factor(ts_code=c)
    if df is None or df.empty:
        print(f'{c}: 无数据')
        continue
    l = df.iloc[0]
    print(f'{c}: MA5={l.get("ma5",0):.2f} MA10={l.get("ma10",0):.2f} MA20={l.get("ma20",0):.2f} MA60={l.get("ma60",0):.2f} close={l.get("close",0):.2f} MACD={l.get("macd",0):.4f} DIF={l.get("dif",0):.4f}')
