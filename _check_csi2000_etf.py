# -*- coding: utf-8 -*-
"""通过中证2000 ETF获取走势（间接）"""
import tushare as ts
ts.set_token('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')

# 常见中证2000 ETF
etfs = ['sh563300', 'sz159531', 'sh560010', 'sz159535', 'sh563200']
try:
    df = ts.get_realtime_quotes(etfs)
    if df is not None and not df.empty:
        print(f"找到 {len(df)} 只ETF实时数据:")
        for _, r in df.iterrows():
            if r['price'] and float(r['price']) > 0:
                now = float(r['price'])
                prev = float(r['pre_close'])
                chg = (now - prev) / prev * 100
                print(f"  {r['name']}({r['code']}): {now:.3f} {chg:+.2f}%  昨收{prev} 今开{r['open']} 高{r['high']} 低{r['low']}")
    else:
        print("ETF实时数据为空")
except Exception as e:
    print(f"失败: {e}")
