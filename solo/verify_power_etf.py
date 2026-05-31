
#!/usr/bin/env python
# -*- coding: utf-8 -*-
import akshare as ak
import pandas as pd

print("="*80)
print("Verify Power ETF Holdings")
print("="*80)

# 电力ETF代码
etf_code = '159611'

print(f"\nFetching holdings for {etf_code} (Power ETF)...")
df = ak.fund_portfolio_hold_em(symbol=etf_code, date='2024')

if df is not None and len(df) > 0:
    print(f"\nTotal holdings: {len(df)}")
    
    print(f"\nAll stocks (with weight):")
    for idx, row in df.iterrows():
        print(f"  {idx+1:3d}. {row['股票代码']} {row['股票名称']:<15} 权重: {row['占净值比例']}%")
    
    # 检查思泰克
    sitai = df[df['股票代码'].astype(str).str.contains('301568', na=False)]
    if len(sitai) > 0:
        print(f"\n⚠️ Found 思泰克 (301568):")
        print(sitai.to_string())
    else:
        print(f"\n✅ 思泰克 (301568) NOT found in raw data")
    
    # 应用过滤条件
    if '占净值比例' in df.columns:
        df_filtered = df.sort_values('占净值比例', ascending=False).drop_duplicates(subset=['股票代码'])
        df_filtered = df_filtered[df_filtered['占净值比例'] >= 0.1]
        
        print(f"\n\nAfter filtering (weight >= 0.1%):")
        print(f"Filtered holdings: {len(df_filtered)}")
        
        for idx, row in df_filtered.iterrows():
            print(f"  {idx+1:3d}. {row['股票代码']} {row['股票名称']:<15} 权重: {row['占净值比例']}%")
        
        sitai_filtered = df_filtered[df_filtered['股票代码'].astype(str).str.contains('301568', na=False)]
        if len(sitai_filtered) > 0:
            print(f"\n⚠️ Found 思泰克 (301568) in filtered list!")
        else:
            print(f"\n✅ 思泰克 (301568) successfully filtered out!")

print("\n" + "="*80)
print("Verification complete")
print("="*80)
