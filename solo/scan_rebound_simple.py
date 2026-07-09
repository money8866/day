
import sys
sys.path.insert(0, r'd:\mystock\solo')
import tushare_quant as tq
import pandas as pd
import os
from etf_resonance.wave3_detector import find_pivots
from etf_resonance.rebound_detector import find_simple_wave, detect_rebound_signal, print_rebound_signal

print('='*70)
print('  今日回升买点检测 (检查昨日wave3信号)')
print('='*70)

# 先读取昨日的wave3信号
csv_path = r'd:\mystock\solo\etf_resonance\output\wave3_signals.csv'
if os.path.exists(csv_path):
    df = pd.read_csv(csv_path)
    print(f'加载到 {len(df)} 个wave3信号')
    codes = df['code'].tolist()
else:
    codes = ['002371.SZ', '300083.SZ', '002185.SZ']

found_signals = []

for i, code in enumerate(codes):
    df_data = tq.get_hist_data(code)
    if df_data is None or len(df_data) < 100:
        continue
    
    try:
        pivots = find_pivots(df_data)
        wave = find_simple_wave(pivots, df_data)
        if not wave:
            continue
        
        # 从dataframe读取股票名称行业，或者从缓存读取
        name = ''
        industry = ''
        try:
            csv_info = pd.read_csv(csv_path)
            info_row = csv_info[csv_info['code'] == code]
            if not info_row.empty:
                name = str(info_row['name'].values[0])
                industry = str(info_row['industry'].values[0])
        except:
            pass
        
        sig = detect_rebound_signal(wave, df_data, name, industry)
        if sig:
            sig.ts_code = code
            found_signals.append(sig)
    except Exception as e:
        print(f'Error with {code}: {e}')
        continue

print(f'\n[结果] 发现 {len(found_signals)} 个回升买点信号:')
print('  代码          名称        行业        现价        信号分      距H1空间')
print('-'*70)

for sig in sorted(found_signals, key=lambda s: -s.rebound_score):
    print(f'  {sig.ts_code:10s}  {sig.name:8s}  {sig.industry:10s}  {sig.current_price:8.2f}  {sig.rebound_score:5.1f}  {sig.dist_to_H1_pct:10.1f}%')

if len(found_signals) >0:
    print('\n详细结果(前5个):')
    for i, sig in enumerate(sorted(found_signals, key=lambda s: -s.rebound_score)[:5]):
        print(f'\n[{i+1}]')
        print_rebound_signal(sig)
