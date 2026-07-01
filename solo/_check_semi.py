# -*- coding: utf-8 -*-
import pandas as pd
import os

CACHE_DIR = r'D:\mystock\cache_daily'
TRADE_DATE = '20260630'

etf_codes = {'半导体': '512480', '半导体设备': '159516'}

for name, code in etf_codes.items():
    suffix = '.SZ' if code.startswith('1') or code.startswith('15') else '.SH'
    full_code = code + suffix
    
    cache_file = os.path.join(CACHE_DIR, f'etf_{code}.csv')
    if os.path.exists(cache_file):
        df = pd.read_csv(cache_file)
        df['trade_date'] = df['trade_date'].astype(str)
        df = df.sort_values('trade_date').tail(120)
        
        for ma in [5, 10, 20, 60]:
            df[f'ma{ma}'] = df['close'].rolling(ma).mean()
        
        df['pct20'] = (df['close'] / df['close'].shift(20) - 1) * 100
        df['pct60'] = (df['close'] / df['close'].shift(60) - 1) * 100
        df['ma60_slope'] = (df['ma60'] / df['ma60'].shift(10) - 1) * 100
        df['deviation60'] = (df['close'] / df['ma60'] - 1) * 100
        df['vol5'] = df['vol'].rolling(5).mean()
        df['vol20'] = df['vol'].rolling(20).mean()
        df['vol_ratio'] = df['vol5'] / df['vol20']
        
        latest = df.iloc[-1]
        
        print(f'=== {name}({full_code}) ===')
        print(f'  收盘价: {latest["close"]:.3f}')
        print(f'  MA5: {latest["ma5"]:.3f}')
        print(f'  MA10: {latest["ma10"]:.3f}')
        print(f'  MA20: {latest["ma20"]:.3f}')
        print(f'  MA60: {latest["ma60"]:.3f}')
        print(f'  ---')
        print(f'  20日涨幅: {latest["pct20"]:+.1f}%  (要求10%~50%)')
        print(f'  60日涨幅: {latest["pct60"]:+.1f}%  (要求20%~150%)')
        print(f'  偏离MA60: {latest["deviation60"]:+.1f}%  (要求<40%)')
        print(f'  MA60斜率: {latest["ma60_slope"]:+.2f}%  (要求>0)')
        print(f'  量比: {latest["vol_ratio"]:.2f}  (要求>0.8)')
        print(f'  ---')
        
        checks = [
            ('均线多头', latest['ma20'] > latest['ma60'], f'MA20({latest["ma20"]:.2f}) > MA60({latest["ma60"]:.2f})'),
            ('20日涨幅', 10 <= latest['pct20'] <= 50, f'{latest["pct20"]:+.1f}%'),
            ('60日涨幅', 20 <= latest['pct60'] <= 150, f'{latest["pct60"]:+.1f}%'),
            ('偏离度', abs(latest['deviation60']) < 40, f'{latest["deviation60"]:+.1f}%'),
            ('MA60向上', latest['ma60_slope'] > 0, f'{latest["ma60_slope"]:+.2f}%'),
            ('量比', latest['vol_ratio'] > 0.8, f'{latest["vol_ratio"]:.2f}'),
        ]
        
        for cond_name, passed, detail in checks:
            status = 'PASS' if passed else 'FAIL'
            print(f'  [{status}] {cond_name}: {detail}')
        print()
