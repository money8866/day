"""
华天科技 20260707前后 量能爆发细节诊断
分析为什么0707当天没命中，以及如何在0707发现它
"""
import sys
sys.path.insert(0, r'd:\mystock\solo')
import _backtest_vol_surge as bvs
import pandas as pd
import numpy as np

df = bvs.load_stock_df('002185.SZ')
print(f'K线条数: {len(df)}')

for test_date in ['20260706','20260707','20260708']:
    d = df[df['trade_date'] <= test_date].copy()
    if len(d) < 180:
        continue
    recent = d.tail(60)
    vol_arr = recent['vol'].values.astype(float)
    high_arr = recent['high'].values.astype(float)
    low_arr = recent['low'].values.astype(float)
    close_arr = recent['close'].values.astype(float)
    pre_close_arr = recent['pre_close'].values.astype(float)

    vol_ma20 = pd.Series(vol_arr).rolling(20, min_periods=1).mean().values
    vol_ratio = vol_arr / np.maximum(vol_ma20, 1)
    max_vol_ratio = float(np.max(vol_ratio))
    vol_ratio_gt2 = int(np.sum(vol_ratio > 2.0))
    vol_ratio_gt3 = int(np.sum(vol_ratio > 3.0))

    amplitude = (high_arr - low_arr) / np.maximum(pre_close_arr, 0.01) * 100
    avg_amplitude = float(np.mean(amplitude))
    amp_gt8_count = int(np.sum(amplitude > 8))

    range_high = float(np.max(high_arr))
    range_low = float(np.min(low_arr))
    range_swing = (range_high / range_low - 1) * 100 if range_low > 0 else 0
    price_change = (close_arr[-1] / close_arr[0] - 1) * 100 if close_arr[0] > 0 else 0

    hist_vol_max = float(np.max(d['vol'].values.astype(float)))
    recent_vol_max = float(np.max(vol_arr))
    vol_vs_hist_pct = (recent_vol_max / hist_vol_max * 100) if hist_vol_max > 0 else 0

    close_full = d['close'].values.astype(float)
    ema12 = pd.Series(close_full).ewm(span=12, adjust=False).mean().values
    ema26 = pd.Series(close_full).ewm(span=26, adjust=False).mean().values
    macd_dif = ema12 - ema26
    macd_dea = pd.Series(macd_dif).ewm(span=9, adjust=False).mean().values
    macd_bar = 2 * (macd_dif - macd_dea)
    cur_bar = float(macd_bar[-1])
    prev_bar = float(macd_bar[-2])
    prev2_bar = float(macd_bar[-3])

    ma20_full = pd.Series(close_full).rolling(20, min_periods=20).mean().values
    ma20_now = float(ma20_full[-1])
    ma20_10ago = float(ma20_full[-11])
    ma20_20ago = float(ma20_full[-21])
    ma20_chg_10d = (ma20_now / ma20_10ago - 1) * 100
    ma20_chg_20d = (ma20_now / ma20_20ago - 1) * 100

    vol_score = min(max_vol_ratio / 5.0, 1) * 30
    freq_score = min(vol_ratio_gt2 / 7, 1) * 20
    amp_score = min(avg_amplitude / 7, 1) * 20
    big_amp_score = min(amp_gt8_count / 15, 1) * 15
    swing_score = min(range_swing / 60, 1) * 15
    total_score = vol_score + freq_score + amp_score + big_amp_score + swing_score

    today_vol_ratio = float(vol_ratio[-1])
    ma20_latest = pd.Series(close_arr).rolling(20).mean().values[-1]
    pos_ma20 = (close_arr[-1] / ma20_latest - 1) * 100

    _df200 = d.tail(200) if len(d) >= 200 else d
    _vol200 = _df200['vol'].values.astype(float)
    _peak_vol_idx = int(np.argmax(_vol200))
    _high200 = _df200['high'].values.astype(float)
    _low200 = _df200['low'].values.astype(float)
    _peak_vol_price = float(_high200[_peak_vol_idx])
    _pre_peak_start = max(0, _peak_vol_idx - 20)
    _pre_peak_end = max(0, _peak_vol_idx - 3)
    _base_vol = float(np.mean(_vol200[_pre_peak_start:_pre_peak_end])) if _pre_peak_end > _pre_peak_start else float(np.mean(_vol200[:_peak_vol_idx]))
    _base_vol = max(_base_vol, 1)
    _recent_vol = float(np.mean(_vol200[-20:]))
    _vol_vs_base = _recent_vol / _base_vol
    _peak_vol_start = max(0, _peak_vol_idx - 5)
    _peak_vol_end = min(len(_vol200), _peak_vol_idx + 6)
    _peak_5d_vol = float(np.mean(_vol200[_peak_vol_start:_peak_vol_end])) if _peak_vol_end > _peak_vol_start else _recent_vol
    _peak_5d_vol = max(_peak_5d_vol, 1)
    _vol_vs_peak = _recent_vol / _peak_5d_vol

    _a_low = float(np.min(_low200[:_peak_vol_idx+1]))
    _a_gain = (_peak_vol_price / _a_low - 1) * 100 if _a_low > 0 else 0
    if _peak_vol_idx < len(_low200) - 3:
        _b_low = float(np.min(_low200[_peak_vol_idx:]))
        _b_drop = (1 - _b_low / _peak_vol_price) * 100
        _retrace_ratio = _b_drop / _a_gain * 100 if _a_gain > 0 else 0
    else:
        _b_low = close_arr[-1]
        _b_drop = 0
        _retrace_ratio = 0
    _fib_786 = _peak_vol_price - (_peak_vol_price - _a_low) * 0.786

    print(f'\n===== {test_date} =====')
    print(f'  收盘={close_arr[-1]:.2f}  今日量比={today_vol_ratio:.2f}  距MA20={pos_ma20:+.1f}%')
    print(f'  --- 量能爆发硬条件 ---')
    print(f'  max_vol_ratio={max_vol_ratio:.2f} (要求>=2.6) {"PASS" if max_vol_ratio>=2.6 else "FAIL"}')
    print(f'  vol_ratio_gt2={vol_ratio_gt2} (要求>=3) {"PASS" if vol_ratio_gt2>=3 else "FAIL"}')
    print(f'  avg_amplitude={avg_amplitude:.2f} (要求>=4.5) {"PASS" if avg_amplitude>=4.5 else "FAIL"}')
    print(f'  range_swing={range_swing:.1f} (要求>=35) {"PASS" if range_swing>=35 else "FAIL"}')
    print(f'  price_change={price_change:.1f} (要求-10~100) {"PASS" if -10<=price_change<=100 else "FAIL"}')
    print(f'  vol_vs_hist_pct={vol_vs_hist_pct:.0f} (要求>=50) {"PASS" if vol_vs_hist_pct>=50 else "FAIL"}')
    print(f'  ma20_chg_10d={ma20_chg_10d:.2f}% ma20_chg_20d={ma20_chg_20d:.2f}% (要求10d>=-0.3 且 20d>=-1) {"PASS" if ma20_chg_10d>=-0.3 and ma20_chg_20d>=-1 else "FAIL"}')
    print(f'  vol_vs_base={_vol_vs_base:.2f} (要求>=1.3) {"PASS" if _vol_vs_base>=1.3 else "FAIL"}')
    print(f'  vol_vs_peak={_vol_vs_peak:.2f} (要求>=0.5) {"PASS" if _vol_vs_peak>=0.5 else "FAIL"}')
    print(f'  a_gain={_a_gain:.1f}% (要求>=15) {"PASS" if _a_gain>=15 else "FAIL"}')
    print(f'  retrace_ratio={_retrace_ratio:.1f}% (要求<=50) {"PASS" if _retrace_ratio<=50 else "FAIL"}')
    print(f'  b_low={_b_low:.2f} vs fib786*0.92={_fib_786*0.92:.2f} (要求b_low>=fib786*0.92) {"PASS" if _b_low>=_fib_786*0.92 else "FAIL"}')
    print(f'  total_score={total_score:.1f} (要求>=65) {"PASS" if total_score>=65 else "FAIL"}')
    print(f'  --- MACD ---')
    print(f'  cur_bar={cur_bar:.4f} prev_bar={prev_bar:.4f} prev2_bar={prev2_bar:.4f}')
    if prev_bar < 0 < cur_bar:
        print(f'  MACD: 刚刚红柱 PASS')
    elif cur_bar < 0 and cur_bar > prev_bar > prev2_bar:
        print(f'  MACD: 即将红柱 PASS')
    else:
        print(f'  MACD: 无信号 FAIL (prev<0且cur>0才刚红柱, 或cur<0且连续缩短才即将红柱)')
