
import sys
sys.path.insert(0, r'd:\mystock\solo')
import tushare_quant as tq
from etf_resonance.wave3_detector import find_pivots
from etf_resonance.rebound_detector import find_simple_wave, detect_rebound_signal, print_rebound_signal

for date in ['20260615', '20260617', '20260620', '20260624', '20260627']:
    print(f'\n{'='*70}')
    print(f'=== {date} 检测 ===')
    print(f'{'='*70}')
    
    df = tq.get_hist_data('002371.SZ')
    df = df[df['trade_date'] <= date].copy().sort_values('trade_date').reset_index(drop=True)
    
    pivots = find_pivots(df)
    print(f'找到 {len(pivots)} 个枢轴点')
    # 打印最后几个看看
    print(f'最后几个枢轴点:')
    for p in pivots[-5:]:
        print(f'  {p.date} {p.kind} {p.price:.2f}')
    
    wave = find_simple_wave(pivots, df)
    
    if wave:
        print(f'\n找到简单波浪结构!')
        print(f'  L0: {wave.L0.date} / {wave.L0.price:.2f}')
        print(f'  H1: {wave.H1.date} / {wave.H1.price:.2f}')
        print(f'  L2: {wave.L2.date} / {wave.L2.price:.2f}')
        
        print(f'\n当前价: {df.iloc[-1]['close']:.2f}')
        
        rebound_sig = detect_rebound_signal(wave, df, '北方华创', '半导体')
        
        if rebound_sig:
            print_rebound_signal(rebound_sig)
        else:
            print('未检测到回升买点')
    else:
        print('未找到有效的简单波浪结构')

