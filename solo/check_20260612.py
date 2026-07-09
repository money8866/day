
import sys
sys.path.insert(0, r'd:\mystock\solo')
import tushare_quant as tq
from etf_resonance.wave3_detector import (
    find_pivots, detect_waves, score_wave3_signal, Wave3Signal
)

print('=== 20260612 时的 wave3_detector 分析 ===')

df = tq.get_hist_data('002371.SZ')
df = df[df['trade_date'] <= '20260612'].copy()
df = df.sort_values('trade_date').reset_index(drop=True)

print(f'数据范围: {df.iloc[0]['trade_date']} ~ {df.iloc[-1]['trade_date']}')
print(f'当前(20260612)收盘价: {df.iloc[-1]['close']:.2f}\n')

print('--- 检测波浪 ---')
pivots = find_pivots(df)
print(f'找到 {len(pivots)} 个枢轴点')

wave = detect_waves(pivots, df)

if wave:
    print('\n--- 波浪结构 ---')
    print(f'L0: {wave.L0.date} / {wave.L0.price:.2f}')
    print(f'H1: {wave.H1.date} / {wave.H1.price:.2f}')
    print(f'L2: {wave.L2.date} / {wave.L2.price:.2f}')
    if wave.H3:
        print(f'H3: {wave.H3.date} / {wave.H3.price:.2f}')
    print(f'W1涨幅: {wave.w1_gain*100:.1f}%')
    print(f'W2回调: {wave.w2_retrace*100:.1f}%')
    print(f'W2回调天数: {wave.w2_days}天')
    
    print('\n--- 信号评估 ---')
    score, reasons = score_wave3_signal(wave, df, '北方华创')
    
    # 计算当前价
    current_price = float(df['close'].values[-1])
    
    # 构建信号
    dist_to_w3_target = (wave.w3_target_price / current_price - 1) * 100
    if wave.H3 is not None:
        w3_progress = (current_price - wave.L2.price) / max(wave.H3.price - wave.L2.price, 0.01) * 100
    else:
        w3_progress = 0
    
    print(f'当前价: {current_price:.2f}')
    print(f'信号分: {score:.1f}')
    print(f'距目标价: {dist_to_w3_target:.1f}%')
    print(f'理由: {'; '.join(reasons)}')
    print(f'当前距离H1(721.60): {(current_price / wave.H1.price -1)*100:.1f}%')
    
else:
    print('未找到有效的波浪结构')

