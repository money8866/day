# -*- coding: utf-8 -*-
import sys, os, sqlite3
sys.path.insert(0, r'D:\mystock\solo')
import pandas as pd
import numpy as np

DB = r'D:\mystock\cache_daily\stock_data.db'

def get_data(ts_code):
    conn = sqlite3.connect(DB)
    try:
        sql = """SELECT trade_date, open, high, low, close, pct_chg, vol, volume_ratio,
                        ma_bfq_5, ma_bfq_10, ma_bfq_20, ma_bfq_30, ma_bfq_60,
                        macd_dif_bfq, macd_dea_bfq, macd_bfq,
                        rsi_bfq_6, kdj_k_bfq, kdj_d_bfq
                 FROM stk_factor_pro WHERE ts_code=? ORDER BY trade_date"""
        df = pd.read_sql(sql, conn, params=(ts_code,))
        if df.empty:
            return None
        df['trade_date'] = df['trade_date'].astype(str)
        df = df.fillna(0)
        df['kdj_j_bfq'] = 3 * df['kdj_k_bfq'] - 2 * df['kdj_d_bfq']
        return df
    finally:
        conn.close()

def calc_prior_rally_gain(df, idx):
    if idx < 60:
        return 0, 0, 0
    close = df.iloc[idx]['close']
    ma20 = df.iloc[idx]['ma_bfq_20']
    
    pullback_start = idx
    for i in range(idx - 1, max(0, idx - 120), -1):
        if df.iloc[i]['close'] < df.iloc[i]['ma_bfq_20'] * 0.98:
            pullback_start = i
            break
    
    if pullback_start == idx:
        return 0, 0, 0
    
    wave_high_idx = pullback_start
    wave_high = df.iloc[pullback_start]['high']
    for i in range(pullback_start, max(0, pullback_start - 120), -1):
        if df.iloc[i]['high'] > wave_high:
            wave_high = df.iloc[i]['high']
            wave_high_idx = i
    
    wave_low = wave_high
    wave_low_idx = wave_high_idx
    for i in range(wave_high_idx, max(0, wave_high_idx - 120), -1):
        if df.iloc[i]['low'] < wave_low:
            wave_low = df.iloc[i]['low']
            wave_low_idx = i
    
    if wave_low <= 0:
        return 0, 0, 0
    
    wave1_gain = (wave_high / wave_low - 1) * 100
    pullback_pct = (1 - close / wave_high) * 100
    return wave1_gain, pullback_pct, wave_high

def find_trend_confirm_signals(df):
    signals = []
    for idx in range(60, len(df) - 25):
        row = df.iloc[idx]
        close = row['close']
        ma20 = row['ma_bfq_20']
        pct_chg = row['pct_chg']
        vol_ratio = row.get('volume_ratio', 0)
        kdj_j = row.get('kdj_j_bfq', 0)
        kdj_k = row.get('kdj_k_bfq', 0)
        
        if ma20 <= 0:
            continue
        
        if close < ma20 * 1.01:
            continue
        if pct_chg < 4.0:
            continue
        if vol_ratio < 1.3:
            continue
        if not (kdj_j > kdj_k and kdj_j > 50):
            continue
        
        wave1_gain, pullback_pct, wave_high = calc_prior_rally_gain(df, idx)
        if wave1_gain < 25:
            continue
        if pullback_pct < 10:
            continue
        
        if signals and idx - signals[-1]['idx'] < 20:
            continue
        
        ret5 = ret10 = ret20 = None
        if idx + 5 < len(df):
            ret5 = (df.iloc[idx + 5]['close'] / close - 1) * 100
        if idx + 10 < len(df):
            ret10 = (df.iloc[idx + 10]['close'] / close - 1) * 100
        if idx + 20 < len(df):
            ret20 = (df.iloc[idx + 20]['close'] / close - 1) * 100
        
        signals.append({
            'idx': idx,
            'date': row['trade_date'],
            'close': round(close, 2),
            'wave1_gain': round(wave1_gain, 1),
            'pullback_pct': round(pullback_pct, 1),
            'above_ma20': round((close / ma20 - 1) * 100, 1),
            'ret_5d': round(ret5, 2) if ret5 is not None else None,
            'ret_10d': round(ret10, 2) if ret10 is not None else None,
            'ret_20d': round(ret20, 2) if ret20 is not None else None,
        })
    return signals

def find_precision_entries(df):
    signals = []
    for idx in range(61, len(df) - 25):
        row = df.iloc[idx]
        close = row['close']
        ma20 = row['ma_bfq_20']
        ma60 = row['ma_bfq_60']
        
        if ma20 <= 0 or ma60 <= 0:
            continue
        
        ma20_5ago = df.iloc[idx - 5]['ma_bfq_20']
        if ma20_5ago <= 0:
            continue
        ma20_trend = (ma20 / ma20_5ago - 1) * 100
        if ma20_trend < -2.0:
            continue
        
        seg_high = df.iloc[max(0, idx - 60):idx]['high'].max()
        break_ma60 = close > ma60 * 1.01
        break_60d_high = close > seg_high * 1.01
        if not (break_ma60 or break_60d_high):
            continue
        
        dif = row.get('macd_dif_bfq', 0)
        dea = row.get('macd_dea_bfq', 0)
        macd_near_zero = abs(dif) < 1.0
        golden_cross = dif > dea
        if not (macd_near_zero or (golden_cross and dif > -0.5)):
            continue
        
        pct_chg = row.get('pct_chg', 0)
        if not (pct_chg >= 2.0 or (pct_chg >= 0.5 and close > row['open'] and break_60d_high)):
            continue
        
        above_ma20 = (close / ma20 - 1) * 100
        if above_ma20 < 5.0 or above_ma20 > 20.0:
            continue
        
        vol_20d = df.iloc[max(0, idx - 21):idx]['vol']
        if len(vol_20d) < 10:
            continue
        max_vol_20d = vol_20d.max()
        current_vol = row['vol']
        if max_vol_20d <= 0:
            continue
        vol_ratio_vs_max = current_vol / max_vol_20d
        if vol_ratio_vs_max < 0.7:
            continue
        
        above_ma60 = (close / ma60 - 1) * 100
        if above_ma60 > 30.0:
            continue
        
        entry_score = 50
        if vol_ratio_vs_max >= 1.0:
            entry_score += 15
        elif vol_ratio_vs_max >= 0.8:
            entry_score += 8
        
        if 8 <= above_ma20 <= 15:
            entry_score += 10
        elif 5 <= above_ma20 < 8 or 15 < above_ma20 <= 20:
            entry_score += 5
        
        if dif > dea and dif > 0:
            entry_score += 10
        
        ma5 = row['ma_bfq_5']
        ma10 = row['ma_bfq_10']
        if all(x > 0 for x in [ma5, ma10, ma20, ma60]):
            if ma5 > ma10 > ma20 > ma60:
                entry_score += 10
            elif ma10 > ma20 > ma60:
                entry_score += 5
        
        if signals and idx - signals[-1]['idx'] < 60:
            if entry_score > signals[-1]['score']:
                signals[-1] = {
                    'idx': idx, 'date': row['trade_date'], 'close': round(close, 2),
                    'score': entry_score, 'above_ma20': round(above_ma20, 1),
                    'ret_5d': None, 'ret_10d': None, 'ret_20d': None,
                }
            continue
        
        ret5 = ret10 = ret20 = None
        if idx + 5 < len(df):
            ret5 = (df.iloc[idx + 5]['close'] / close - 1) * 100
        if idx + 10 < len(df):
            ret10 = (df.iloc[idx + 10]['close'] / close - 1) * 100
        if idx + 20 < len(df):
            ret20 = (df.iloc[idx + 20]['close'] / close - 1) * 100
        
        signals.append({
            'idx': idx, 'date': row['trade_date'], 'close': round(close, 2),
            'score': entry_score, 'above_ma20': round(above_ma20, 1),
            'ret_5d': round(ret5, 2) if ret5 is not None else None,
            'ret_10d': round(ret10, 2) if ret10 is not None else None,
            'ret_20d': round(ret20, 2) if ret20 is not None else None,
        })
    return signals

# 加载合格池
bull_csv = r'D:\mystock\solo\report_daily\bull_stocks_qualified.csv'
df_bull = pd.read_csv(bull_csv, dtype={'code': str})
codes = []
for _, row in df_bull.iterrows():
    code = row['code']
    if code.startswith(('6', '9')):
        ts_code = code + '.SH'
    else:
        ts_code = code + '.SZ'
    codes.append((ts_code, row['name']))

print(f"合格池共 {len(codes)} 只股票")
print()

trend_all = []
precision_all = []
success_count = 0
fail_count = 0

for ts_code, name in codes:
    df = get_data(ts_code)
    if df is None or len(df) < 120:
        fail_count += 1
        continue
    
    success_count += 1
    tsigs = find_trend_confirm_signals(df)
    for s in tsigs:
        s['code'] = ts_code
        s['name'] = name
        trend_all.append(s)
    
    psigs = find_precision_entries(df)
    for s in psigs:
        s['code'] = ts_code
        s['name'] = name
        precision_all.append(s)

print(f"有效数据: {success_count} 只  (无数据: {fail_count} 只)")
print()

print("=" * 70)
print("【趋势确认信号】回测统计（TREND_BREAK）")
print("=" * 70)
print(f"总信号数: {len(trend_all)}")
trend_df = pd.DataFrame(trend_all)
trend_df = trend_df.dropna(subset=['ret_5d', 'ret_10d', 'ret_20d'])
print(f"有完整20日数据: {len(trend_df)} 个信号")
print()

if len(trend_df) > 0:
    print(f"+5日:  平均 {trend_df['ret_5d'].mean():+.2f}%  中位数 {trend_df['ret_5d'].median():+.2f}%  胜率 {(trend_df['ret_5d']>0).mean()*100:.1f}%")
    print(f"+10日: 平均 {trend_df['ret_10d'].mean():+.2f}%  中位数 {trend_df['ret_10d'].median():+.2f}%  胜率 {(trend_df['ret_10d']>0).mean()*100:.1f}%")
    print(f"+20日: 平均 {trend_df['ret_20d'].mean():+.2f}%  中位数 {trend_df['ret_20d'].median():+.2f}%  胜率 {(trend_df['ret_20d']>0).mean()*100:.1f}%")
    print()
    
    print("按前一波涨幅分组 (+20日):")
    for thresh in [25, 50, 80, 120]:
        sub = trend_df[trend_df['wave1_gain'] >= thresh]
        if len(sub) >= 5:
            print(f"  前涨≥{thresh}%: {len(sub):3d}个  +20d {sub['ret_20d'].mean():+.2f}%  胜率 {(sub['ret_20d']>0).mean()*100:.1f}%")
    print()
    
    print("按距MA20位置分组 (+20日):")
    bins = [(0, 5), (5, 10), (10, 15), (15, 25), (25, 100)]
    for lo, hi in bins:
        sub = trend_df[(trend_df['above_ma20'] >= lo) & (trend_df['above_ma20'] < hi)]
        if len(sub) >= 5:
            print(f"  距MA20 +{lo}%~+{hi}%: {len(sub):3d}个  +20d {sub['ret_20d'].mean():+.2f}%  胜率 {(sub['ret_20d']>0).mean()*100:.1f}%")

print()
print("=" * 70)
print("【精准入场信号】回测统计（Precision Entry v3）")
print("=" * 70)
print(f"总信号数: {len(precision_all)}")
prec_df = pd.DataFrame(precision_all)
prec_df = prec_df.dropna(subset=['ret_5d', 'ret_10d', 'ret_20d'])
print(f"有完整20日数据: {len(prec_df)} 个信号")
print()

if len(prec_df) > 0:
    print(f"+5日:  平均 {prec_df['ret_5d'].mean():+.2f}%  中位数 {prec_df['ret_5d'].median():+.2f}%  胜率 {(prec_df['ret_5d']>0).mean()*100:.1f}%")
    print(f"+10日: 平均 {prec_df['ret_10d'].mean():+.2f}%  中位数 {prec_df['ret_10d'].median():+.2f}%  胜率 {(prec_df['ret_10d']>0).mean()*100:.1f}%")
    print(f"+20日: 平均 {prec_df['ret_20d'].mean():+.2f}%  中位数 {prec_df['ret_20d'].median():+.2f}%  胜率 {(prec_df['ret_20d']>0).mean()*100:.1f}%")
    print()
    
    print("按评分分组 (+20日):")
    for s in [60, 70, 75, 80, 85, 90]:
        sub = prec_df[prec_df['score'] >= s]
        if len(sub) >= 5:
            print(f"  ≥{s}分: {len(sub):3d}个信号  +10d {sub['ret_10d'].mean():+.2f}%  胜率 {(sub['ret_10d']>0).mean()*100:.1f}%  +20d {sub['ret_20d'].mean():+.2f}%")
    print()
    
    print("按距MA20位置分组 (+20日):")
    bins = [(5, 8), (8, 12), (12, 15), (15, 20)]
    for lo, hi in bins:
        sub = prec_df[(prec_df['above_ma20'] >= lo) & (prec_df['above_ma20'] < hi)]
        if len(sub) >= 5:
            print(f"  距MA20 +{lo}%~+{hi}%: {len(sub):3d}个  +20d {sub['ret_20d'].mean():+.2f}%  胜率 {(sub['ret_20d']>0).mean()*100:.1f}%")

print()
print("=" * 70)
