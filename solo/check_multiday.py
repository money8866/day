
import sys
sys.path.insert(0, r'd:\mystock\solo')
import tushare_quant as tq
from etf_resonance.wave3_detector import (
    find_pivots, detect_waves, score_wave3_signal
)

for end_date in ['20260615','20260617','20260620','20260624','20260630','20260708']:
    print(f'\n====== 截止到 {end_date} ======')
    
    df = tq.get_hist_data('002371.SZ')
    df = df[df['trade_date'] <= end_date].copy().sort_values('trade_date').reset_index(drop=True)
    
    pivots = find_pivots(df)
    wave = detect_waves(pivots, df)
    
    if wave:
        print(f'找到波浪结构!')
        print(f'  L0: {wave.L0.date} / {wave.L0.price:.2f}')
        print(f'  H1: {wave.H1.date} / {wave.H1.price:.2f}')
        print(f'  L2: {wave.L2.date} / {wave.L2.price:.2f}')
        print(f'  W1涨幅: {wave.w1_gain*100:.1f}%')
        print(f'  W2回调: {wave.w2_retrace*100:.1f}%')
        print(f'  W2回调天数: {wave.w2_days}天')
        
        # 检查L2到end_date的距离
        current_price = df.iloc[-1]['close']
        print(f'  当前价: {current_price:.2f}')
        print(f'  当前价 vs H1: {(current_price / wave.H1.price -1)*100:.1f}%')
        
        score, reasons = score_wave3_signal(wave, df, '北方华创')
        print(f'  信号分: {score:.1f}')
        
        if score >= 90:
            print('  >>>> 符合买入条件!')
    else:
        print('未找到有效波浪结构')

