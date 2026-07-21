# -*- coding: utf-8 -*-
import pandas as pd
df = pd.read_csv('D:/mystock/solo/report_daily/valuation_ge100_v2.csv', encoding='utf-8-sig')
high = df[df['realistic_upside_%'] >= 100].sort_values('realistic_upside_%', ascending=False)
print(f"空间>=100%: {len(high)} 只")
print()
for _, r in high.iterrows():
    code_s = str(r['code']).strip()
    # 补齐代码
    if code_s.isdigit() and len(code_s) < 6:
        code_s = code_s.zfill(6)
    name = str(r['name'])[:6]
    theme = str(r['theme'])[:10] if pd.notna(r['theme']) else '-'
    pe = r['pe_ttm']
    peg = r['peg']
    npy = r['net_profit_yoy']
    ups = r['realistic_upside_%']
    score = r['composite_score']
    print(f"{code_s:>10s} | {name:<6s} | {theme:<10s} | PE={pe:>5.1f} | PEG={peg:>5.2f} | 净利YoY={npy:>7.1f}% | 空间={ups:>6.1f}% | 分={score:.0f}")
