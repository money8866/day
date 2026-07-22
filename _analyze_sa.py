# -*- coding: utf-8 -*-
import pandas as pd
df = pd.read_csv('D:/mystock/solo/report_daily/enhanced_timing_bull_all_20260722_084454.csv', encoding='utf-8-sig')
sa = df[df['修正后胜率分级'].isin(['S','A'])].sort_values('量化择时分', ascending=False)
print('S+A级共', len(sa), '只')
print()
for _, r in sa.iterrows():
    code = str(r['代码']).replace('.SH','').replace('.SZ','')
    print(f"{r['名称']}({code}) [{r['修正后胜率分级']}] 择时分:{r['量化择时分']:.1f} 修正分:{r['修正后评分']:.1f}")
    print(f"   行业:{r['行业']} 中报:{r['中报业绩亮点']} 真突破:{r['真突破判定']} 回踩:{r['回踩确认']}")
    print(f"   现价:{r['现价']} VWAP:{r['VWAP']} MA20:{r['MA20']} 筹码峰:{r['筹码峰顶']} 集中度:{r['筹码集中度%']}%")
    print(f"   买点:{r['推荐买点类型']} ATR止损:{r['ATR动态止损价']} ATR止盈:{r['ATR跟踪止盈价']} 决策:{r['交易决策']}")
    print()
