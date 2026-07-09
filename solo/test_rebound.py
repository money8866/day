
import sys
sys.path.insert(0, r'd:\mystock\solo')
import tushare_quant as tq
from etf_resonance.wave3_detector import find_pivots
from etf_resonance.rebound_detector import find_simple_wave, detect_rebound_signal, print_rebound_signal

print('=== 20260612 回升买点检测 ===')

df = tq.get_hist_data('002371.SZ')
df = df[df['trade_date'] <= '20260612'].copy().sort_values('trade_date').reset_index(drop=True)

print(f'数据范围: {df.iloc[0]['trade_date']} ~ {df.iloc[-1]['trade_date']}')
print(f'当前(20260612)收盘价: {df.iloc[-1]['close']:.2f}\n')

print('--- 检测简单波浪(L0→H1→L2) ---')
pivots = find_pivots(df)
print(f'找到 {len(pivots)} 个枢轴点')

wave = find_simple_wave(pivots, df)

if wave:
    print('找到简单波浪结构!')
    print(f'  L0: {wave.L0.date} / {wave.L0.price:.2f}')
    print(f'  H1: {wave.H1.date} / {wave.H1.price:.2f}')
    print(f'  L2: {wave.L2.date} / {wave.L2.price:.2f}')
    print(f'  W1涨幅: {wave.w1_gain*100:.1f}%, W2回调: {wave.w2_retrace*100:.1f}%\n')
    
    # 检测回升买点
    print('--- 检测回升买点 ---')
    rebound_sig = detect_rebound_signal(wave, df, '北方华创', '半导体')
    
    if rebound_sig:
        print_rebound_signal(rebound_sig)
    else:
        print('未检测到回升买点')
else:
    print('未找到有效的简单波浪结构')

