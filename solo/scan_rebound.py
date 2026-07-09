
import sys
sys.path.insert(0, r'd:\mystock\solo')
import tushare_quant as tq
from etf_resonance.wave3_detector import find_pivots
from etf_resonance.rebound_detector import find_simple_wave, detect_rebound_signal, print_rebound_signal
import pandas as pd

print('='*70)
print('  今日回升买点扫描')
print('='*70)

# 先读取ETF成份股池
from etf_resonance.run_real import _load_etf_constituents_cached
constituents = _load_etf_constituents_cached()

print(f'加载到 {len(constituents)} 只成份股')

# 读取股票列表
codes = list(constituents.keys())
if not codes:
    codes = ['002371.SZ', '300083.SZ', '002185.SZ']

found_signals = []

for i, code in enumerate(codes):
    if (i+1) %50 ==0:
        print(f'进度: {i+1}/{len(codes)} 已找到 {len(found_signals)} 个信号')
    
    df = tq.get_hist_data(code)
    if df is None or len(df) < 100:
        continue
    
    try:
        pivots = find_pivots(df)
        wave = find_simple_wave(pivots, df)
        if not wave:
            continue
        
        name = constituents.get(code, {}).get('name', code)
        industry = constituents.get(code, {}).get('industry', '')
        
        sig = detect_rebound_signal(wave, df, name, industry)
        if sig:
            found_signals.append(sig)
    except Exception as e:
        continue

print(f'\n[结果] 发现 {len(found_signals)} 个回升买点信号:')
print('  代码          名称        行业        现价        信号分      距H1空间')
print('-'*70)

for sig in sorted(found_signals, key=lambda s: -s.rebound_score):
    print(f'  {sig.ts_code:10s}  {sig.name:8s}  {sig.industry:10s}  {sig.current_price:8.2f}  {sig.rebound_score:5.1f}  {sig.dist_to_H1_pct:10.1f}%')

if len(found_signals) >0:
    # 打印第一个详细结果
    print('\n第一个回升买点详情:')
    print_rebound_signal(found_signals[0])
