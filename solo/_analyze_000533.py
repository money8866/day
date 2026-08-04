# -*- coding: utf-8 -*-
"""分析 000533(顺钠股份) 为何未被现有择时算法在 7/31 回调中找出"""
import pandas as pd
import numpy as np
import sys
sys.path.insert(0, r"d:\mystock\solo")
import treasure_hunter as th
np.seterr(all='ignore')


def build_row(slice_df):
    closes = slice_df['close'].astype(float).values
    current_close = float(closes[-1])
    high_120 = float(slice_df.tail(120)['high'].max())
    pct_from_high = (high_120 - current_close) / high_120 * 100 if high_120 > 0 else 999
    low_60 = float(slice_df.tail(60)['low'].min())
    pct_from_60d_low = (current_close - low_60) / low_60 * 100 if low_60 > 0 else 999
    ma20 = slice_df['close'].rolling(20).mean()
    ma60 = slice_df['close'].rolling(60).mean()
    ma20_val = float(ma20.dropna().iloc[-1])
    ma60_val = float(ma60.dropna().iloc[-1])
    pct_below_ma20 = (current_close - ma20_val) / ma20_val * 100
    pct_below_ma60 = (current_close - ma60_val) / ma60_val * 100
    m20 = ma20.dropna()
    ma20_prev = float(m20.iloc[-6]) if len(m20) >= 6 else ma20_val
    ma20_slope = (ma20_val - ma20_prev) / ma20_prev * 100 if ma20_prev > 0 else 0
    volumes = slice_df['vol'].astype(float).values
    vol_5d = np.mean(volumes[-5:])
    vol_20d = np.mean(volumes[-20:])
    volume_ratio = vol_5d / vol_20d if vol_20d > 0 else 1.0
    cs = pd.Series(closes)
    dif = cs.ewm(span=12, adjust=False).mean() - cs.ewm(span=26, adjust=False).mean()
    dea = dif.ewm(span=9, adjust=False).mean()
    hist = (dif - dea) * 2
    golden = bool(dif.iloc[-1] > dea.iloc[-1] and dif.iloc[-2] <= dea.iloc[-2])
    rsi = 50.0
    if len(closes) >= 15:
        deltas = np.diff(closes[-15:])
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        ag = gains.mean()
        al = losses.mean()
        if al > 0:
            rsi = 100 - 100 / (1 + ag / al)
        elif ag > 0:
            rsi = 100.0
    rc1 = (closes[-1] / closes[-2] - 1) * 100 if closes[-2] > 0 else 0
    rc2 = (closes[-1] / closes[-3] - 1) * 100 if closes[-3] > 0 else 0
    isneg = bool(slice_df.iloc[-1]['close'] < slice_df.iloc[-1]['open'])
    above = 0
    for j in range(len(closes) - 1, -1, -1):
        if np.isnan(ma20.values[j]) or closes[j] <= ma20.values[j]:
            break
        above += 1
    # 主升后回调指标
    recent_60d = slice_df.tail(60)
    low_60d = float(recent_60d['low'].min())
    low_pos = int(recent_60d['low'].idxmin())
    seg = recent_60d.loc[low_pos:]
    rally_high = float(seg['high'].max()) if len(seg) > 0 else current_close
    rally_gain = (rally_high - low_60d) / low_60d * 100 if low_60d > 0 else 0
    pullback_from_high = (rally_high - current_close) / rally_high * 100 if rally_high > 0 else 0
    if len(volumes) >= 6:
        prev_5d = np.mean(volumes[-6:-1])
        vol_today_vs_5d = volumes[-1] / prev_5d if prev_5d > 0 else 1.0
    else:
        vol_today_vs_5d = 1.0
    return {
        'current_close': current_close, 'pct_from_120d_high': pct_from_high,
        'ma20_slope': ma20_slope, 'rsi_14': rsi,
        'pct_below_ma20': pct_below_ma20, 'pct_below_ma60': pct_below_ma60,
        'volume_ratio': volume_ratio, 'pct_from_60d_low': pct_from_60d_low,
        'ma20': ma20_val, 'ma60': ma60_val,
        'macd_dif': float(dif.iloc[-1]), 'macd_dea': float(dea.iloc[-1]),
        'macd_hist': float(hist.iloc[-1]), 'macd_golden_cross': golden,
        'days_since_60d_low': 5, 'recent_chg_1d': rc1, 'recent_chg_2d': rc2,
        'is_negative_day': isneg, 'days_above_ma20': above,
        'rally_gain': rally_gain, 'pullback_from_high': pullback_from_high,
        'vol_today_vs_5d': vol_today_vs_5d,
    }


df = pd.read_parquet(r"D:\mystock\cache_daily\treasure_daily_000533_SZ_20260105_20260803.parquet")
df['trade_date'] = df['trade_date'].astype(str)
df = df.sort_values('trade_date').reset_index(drop=True)

for d in ['20260728', '20260729', '20260730', '20260731', '20260803']:
    hit = df.index[df['trade_date'] == d]
    if len(hit) == 0:
        print(d, '无数据')
        continue
    i = hit[0]
    row = build_row(df.iloc[:i + 1])
    os_ = th._compute_oversold_score(row)['超跌总分']
    mt_ = th._compute_midterm_buy_score(row)
    rs_ = th._compute_rightside_strength(row)['右侧强度总分']
    pb_ = th._compute_pullback_score(row)
    print("\n== %s 收%.2f ==" % (d, row['current_close']))
    print("  超跌%.1f  中线买点%.1f(形态罚%.1f)  右侧强度%.1f  主升后回调%.1f" % (
        os_, mt_['中线买点总分'], mt_['形态惩罚'], rs_, pb_['主升后回调总分']))
    print("  RSI%.1f 距MA20%+.1f%% 距MA60%+.1f%% 量比%.2f 距120日高%.1f%% 站上MA20 %d日"
          % (row['rsi_14'], row['pct_below_ma20'], row['pct_below_ma60'],
             row['volume_ratio'], row['pct_from_120d_high'], row['days_above_ma20']))
    print("  近1日%+.1f%% 近2日%+.1f%% 阴线%s" % (row['recent_chg_1d'], row['recent_chg_2d'], row['is_negative_day']))
    print("  主升%.1f%%(60日高低差) 回撤%.1f%% 当日量比%.2f → 回调子分: 主升%d 回调%d 缩量%d 趋势%d 空间%d 罚%d" % (
        row['rally_gain'], row['pullback_from_high'], row['vol_today_vs_5d'],
        pb_['主升确认'], pb_['回调到位'], pb_['缩量回调'], pb_['趋势未破'], pb_['二波空间'], pb_['回调惩罚']))
