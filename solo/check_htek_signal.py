"""
华天科技(002185.SZ) 历史信号检测
检测每一天的波浪结构和信号类型（低吸/突破）
"""
import sys
sys.path.insert(0, r'd:\mystock\solo')
import tdx_backtest_mixed as tb

ts_code = '002185.SZ'
START_DATE = '20250101'
END_DATE = '20260708'

trade_dates = tb.get_trade_dates(START_DATE, END_DATE)

full_df = tb.get_tdx_kline(ts_code, END_DATE, n_days=400)
print(f'华天科技 K线条数: {len(full_df)}')
date0 = full_df["trade_date"].iloc[0]
dateN = full_df["trade_date"].iloc[-1]
print(f'日期范围: {date0} ~ {dateN}')
print('=' * 100)

results = []

for date_idx, date_str in enumerate(trade_dates):
    sliced_df = full_df[full_df['trade_date'] <= date_str].copy()
    if len(sliced_df) < 60:
        continue

    pivots = tb.find_pivots(sliced_df)
    wave = tb.find_simple_wave(pivots, sliced_df)
    if not wave:
        continue

    current_price = sliced_df['close'].values[-1]
    w2_pct = wave.w2_retrace * 100
    w1_pct = wave.w1_gain * 100

    if w2_pct >= 70:
        sig_type = '低吸'
        score, details = tb.detect_rebound_signal(wave, sliced_df)
        if score is not None and score >= 60:
            results.append({
                'date': date_str,
                'sig_type': sig_type,
                'score': score,
                'price': round(current_price, 2),
                'w1_gain': round(w1_pct, 1),
                'w2_retrace': round(w2_pct, 1),
                'rebound_pct': round(details['rebound_pct'], 1),
                'dist_H1': round(details['dist_to_H1_pct'], 1),
                'days_L2': details['days_since_L2'],
            })
    else:
        prev_close = sliced_df['close'].values[-2] if len(sliced_df) >= 2 else 0
        if current_price > wave.H1.price and prev_close <= wave.H1.price:
            results.append({
                'date': date_str,
                'sig_type': '突破',
                'score': 0,
                'price': round(current_price, 2),
                'w1_gain': round(w1_pct, 1),
                'w2_retrace': round(w2_pct, 1),
                'rebound_pct': 0,
                'dist_H1': 0,
                'days_L2': 0,
            })

print(f'华天科技({ts_code}) {START_DATE}~{END_DATE} 信号检测:')
print(f'共{len(results)}笔信号')
print('-' * 100)
header = f"{'日期':12} {'类型':>4} {'信号分':>6} {'现价':>8} {'W1涨幅':>8} {'W2回调':>7} {'回升':>6} {'距H1':>7} {'L2后':>5}"
print(header)
print('-' * 100)
for r in results:
    line = f"{r['date']:12} {r['sig_type']:>4} {r['score']:>6.1f} {r['price']:>8.2f} {r['w1_gain']:>7.1f}% {r['w2_retrace']:>6.1f}% {r['rebound_pct']:>5.1f}% {r['dist_H1']:>+6.1f}% {r['days_L2']:>5}天"
    print(line)

n_dx = len([r for r in results if r['sig_type'] == '低吸'])
n_tp = len([r for r in results if r['sig_type'] == '突破'])
print('-' * 100)
print(f'低吸信号: {n_dx}笔  突破信号: {n_tp}笔')

print()
print('=' * 100)
print('所有出现过L0->H1->L2波浪结构的日期及W2回调深度（含未触发信号的）:')
print('=' * 100)
wave_dates = []
for date_idx, date_str in enumerate(trade_dates):
    sliced_df = full_df[full_df['trade_date'] <= date_str].copy()
    if len(sliced_df) < 60:
        continue
    pivots = tb.find_pivots(sliced_df)
    wave = tb.find_simple_wave(pivots, sliced_df)
    if not wave:
        continue
    current_price = sliced_df['close'].values[-1]
    w2_pct = wave.w2_retrace * 100
    w1_pct = wave.w1_gain * 100
    l0_date = wave.L0.date
    h1_date = wave.H1.date
    l2_date = wave.L2.date
    wave_dates.append({
        'date': date_str,
        'price': round(current_price, 2),
        'w1_gain': round(w1_pct, 1),
        'w2_retrace': round(w2_pct, 1),
        'l0_date': l0_date,
        'h1_date': h1_date,
        'l2_date': l2_date,
        'h1_price': round(wave.H1.price, 2),
        'l2_price': round(wave.L2.price, 2),
    })

header2 = f"{'日期':12} {'现价':>8} {'W1涨幅':>8} {'W2回调':>7} {'L0日期':>10} {'H1日期':>10} {'L2日期':>10} {'H1价':>8} {'L2价':>8}"
print(header2)
print('-' * 105)
for w in wave_dates:
    line = f"{w['date']:12} {w['price']:>8.2f} {w['w1_gain']:>7.1f}% {w['w2_retrace']:>6.1f}% {w['l0_date']:>10} {w['h1_date']:>10} {w['l2_date']:>10} {w['h1_price']:>8.2f} {w['l2_price']:>8.2f}"
    print(line)
