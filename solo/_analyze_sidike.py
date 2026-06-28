# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, r'D:\mystock\solo')
sys.path.insert(0, r'D:\mystock')

import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime

DB = r'D:\mystock\cache_daily\stock_data.db'

code = '300806.SZ'
name = '斯迪克'

def get_data(ts_code):
    conn = sqlite3.connect(DB)
    try:
        sql = """SELECT trade_date, open, high, low, close, pct_chg, vol, amount, volume_ratio,
                        ma_bfq_5, ma_bfq_10, ma_bfq_20, ma_bfq_30, ma_bfq_60, ma_bfq_90,
                        macd_dif_bfq, macd_dea_bfq, macd_bfq,
                        rsi_bfq_6, rsi_bfq_12, rsi_bfq_24,
                        kdj_bfq, kdj_k_bfq, kdj_d_bfq,
                        atr_bfq
                 FROM stk_factor_pro WHERE ts_code=? ORDER BY trade_date"""
        df = pd.read_sql(sql, conn, params=(ts_code,))
        if df.empty:
            return None
        df['trade_date'] = df['trade_date'].astype(str)
        df = df.fillna(0)
        # 计算KDJ-J值: J = 3K - 2D
        df['kdj_j_bfq'] = 3 * df['kdj_k_bfq'] - 2 * df['kdj_d_bfq']
        return df
    finally:
        conn.close()

df = get_data(code)
if df is None:
    print(f"未找到 {name} 的数据")
    sys.exit(0)

print(f"=== {name} ({code}) 数据分析 ===")
print(f"数据范围: {df['trade_date'].iloc[0]} ~ {df['trade_date'].iloc[-1]}")
print(f"共 {len(df)} 个交易日")
print(f"最新收盘价: {df['close'].iloc[-1]:.2f} 元")
print()

# ============================================================
# 1. 趋势确认信号（站上MA20+放量+大阳线+KDJ多头+前涨≥25%+回撤≥10%）
# ============================================================
print("=" * 70)
print("【一】趋势确认信号（TREND_BREAK）")
print("=" * 70)
print("条件: 站上MA20 + 大阳线≥4% + 量比≥1.3 + KDJ多头 + 前一波涨≥25% + 回撤≥10%")
print()

def calc_prior_rally_gain(df, idx):
    if idx < 60:
        return 0, 0, 0
    close = df.iloc[idx]['close']
    ma20 = df.iloc[idx]['ma_bfq_20']
    
    # 往前找跌破MA20的起点（回撤起点）
    pullback_start = idx
    for i in range(idx - 1, max(0, idx - 120), -1):
        if df.iloc[i]['close'] < df.iloc[i]['ma_bfq_20'] * 0.98:
            pullback_start = i
            break
    
    if pullback_start == idx:
        return 0, 0, 0
    
    # 在回撤起点之前找一波上涨的起点
    wave_high_idx = pullback_start
    wave_high = df.iloc[pullback_start]['high']
    for i in range(pullback_start, max(0, pullback_start - 120), -1):
        if df.iloc[i]['high'] > wave_high:
            wave_high = df.iloc[i]['high']
            wave_high_idx = i
    
    # 找一波上涨的起点（低点）
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
    for idx in range(60, len(df)):
        row = df.iloc[idx]
        close = row['close']
        ma20 = row['ma_bfq_20']
        pct_chg = row['pct_chg']
        vol_ratio = row.get('volume_ratio', 0)
        kdj_j = row.get('kdj_j_bfq', 0)
        kdj_k = row.get('kdj_k_bfq', 0)
        
        if ma20 <= 0:
            continue
        
        # 条件1: 站上MA20
        if close < ma20 * 1.01:
            continue
        
        # 条件2: 大阳线涨幅≥4%
        if pct_chg < 4.0:
            continue
        
        # 条件3: 量比≥1.3
        if vol_ratio < 1.3:
            continue
        
        # 条件4: KDJ多头（J>K且J>50）
        if not (kdj_j > kdj_k and kdj_j > 50):
            continue
        
        # 条件5: 前一波上涨≥25%
        wave1_gain, pullback_pct, wave_high = calc_prior_rally_gain(df, idx)
        if wave1_gain < 25:
            continue
        
        # 条件6: 回撤深度≥10%
        if pullback_pct < 10:
            continue
        
        # 去重：同一波只取第一个信号
        if signals:
            last_idx = signals[-1]['idx']
            if idx - last_idx < 20:
                continue
        
        signals.append({
            'idx': idx,
            'date': row['trade_date'],
            'close': round(close, 2),
            'pct_chg': round(pct_chg, 2),
            'vol_ratio': round(vol_ratio, 2),
            'kdj_j': round(kdj_j, 1),
            'wave1_gain': round(wave1_gain, 1),
            'pullback_pct': round(pullback_pct, 1),
            'wave_high': round(wave_high, 2),
            'above_ma20': round((close / ma20 - 1) * 100, 1),
        })
    return signals

trend_signals = find_trend_confirm_signals(df)
if trend_signals:
    print(f"共发现 {len(trend_signals)} 个趋势确认信号:")
    print()
    for i, s in enumerate(trend_signals):
        print(f"  信号{i+1}: {s['date']}  收盘价:{s['close']:.2f}  涨幅:{s['pct_chg']}%  量比:{s['vol_ratio']}")
        print(f"         前一波涨:{s['wave1_gain']}%  回撤:{s['pullback_pct']}%  前高:{s['wave_high']:.2f}  距MA20:+{s['above_ma20']}%")
        print(f"         KDJ-J: {s['kdj_j']}")
        # 计算后续表现
        s_idx = s['idx']
        for days in [5, 10, 20]:
            if s_idx + days < len(df):
                future_close = df.iloc[s_idx + days]['close']
                ret = (future_close / s['close'] - 1) * 100
                print(f"         +{days}日: {future_close:.2f} ({ret:+.2f}%)")
        print()
else:
    print("  未发现趋势确认信号")
print()

# ============================================================
# 2. 精准入场信号（v3）
# ============================================================
print("=" * 70)
print("【二】精准入场信号（Precision Entry v3）")
print("=" * 70)
print("条件: 距MA20 5%~20% + 当日量/前20日最高量>0.7 + 距MA60<30%")
print()

def find_precision_entries(df):
    signals = []
    for idx in range(61, len(df)):
        row = df.iloc[idx]
        close = row['close']
        ma20 = row['ma_bfq_20']
        ma60 = row['ma_bfq_60']
        
        if ma20 <= 0 or ma60 <= 0:
            continue
        
        # 基础趋势条件: MA20走平或上拐
        ma20_5ago = df.iloc[idx - 5]['ma_bfq_20']
        if ma20_5ago <= 0:
            continue
        ma20_trend = (ma20 / ma20_5ago - 1) * 100
        if ma20_trend < -2.0:
            continue
        
        # 突破60日线或前60日高点
        seg_high = df.iloc[max(0, idx - 60):idx]['high'].max()
        break_ma60 = close > ma60 * 1.01
        break_60d_high = close > seg_high * 1.01
        if not (break_ma60 or break_60d_high):
            continue
        
        # MACD条件
        dif = row.get('macd_dif_bfq', 0)
        dea = row.get('macd_dea_bfq', 0)
        macd_near_zero = abs(dif) < 1.0
        golden_cross = dif > dea
        if not (macd_near_zero or (golden_cross and dif > -0.5)):
            continue
        
        # 涨幅条件
        pct_chg = row.get('pct_chg', 0)
        if not (pct_chg >= 2.0 or (pct_chg >= 0.5 and close > row['open'] and break_60d_high)):
            continue
        
        # B1: 距MA20 5%~20%
        above_ma20 = (close / ma20 - 1) * 100
        if above_ma20 < 5.0 or above_ma20 > 20.0:
            continue
        
        # B2: 当日量/前20日最高量 > 0.7
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
        
        # B3: 距MA60 < 30%
        above_ma60 = (close / ma60 - 1) * 100
        if above_ma60 > 30.0:
            continue
        
        # 评分
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
        
        # 去重：60天内只取第一个
        if signals:
            last_idx = signals[-1]['idx']
            if idx - last_idx < 60:
                if entry_score > signals[-1]['score']:
                    signals[-1] = {
                        'idx': idx,
                        'date': row['trade_date'],
                        'close': round(close, 2),
                        'score': entry_score,
                        'pct_chg': round(pct_chg, 2),
                        'vol_ratio': round(row.get('volume_ratio', 0), 2),
                        'vol_surge': round(vol_ratio_vs_max, 2),
                        'above_ma20': round(above_ma20, 1),
                        'above_ma60': round(above_ma60, 1),
                        'ma20_trend': round(ma20_trend, 2),
                        'rsi6': round(row.get('rsi_bfq_6', 0), 1),
                    }
                continue
        
        signals.append({
            'idx': idx,
            'date': row['trade_date'],
            'close': round(close, 2),
            'score': entry_score,
            'pct_chg': round(pct_chg, 2),
            'vol_ratio': round(row.get('volume_ratio', 0), 2),
            'vol_surge': round(vol_ratio_vs_max, 2),
            'above_ma20': round(above_ma20, 1),
            'above_ma60': round(above_ma60, 1),
            'ma20_trend': round(ma20_trend, 2),
            'rsi6': round(row.get('rsi_bfq_6', 0), 1),
        })
    return signals

precision_signals = find_precision_entries(df)
if precision_signals:
    print(f"共发现 {len(precision_signals)} 个精准入场信号:")
    print()
    for i, s in enumerate(precision_signals):
        print(f"  信号{i+1}: {s['date']}  收盘价:{s['close']:.2f}  评分:{s['score']}分")
        print(f"         涨幅:{s['pct_chg']}%  量比:{s['vol_ratio']}  放量倍数:{s['vol_surge']}")
        print(f"         距MA20:+{s['above_ma20']}%  距MA60:+{s['above_ma60']}%  MA20趋势:{s['ma20_trend']:+.2f}%")
        print(f"         RSI6: {s['rsi6']}")
        # 计算后续表现
        s_idx = s['idx']
        for days in [5, 10, 20]:
            if s_idx + days < len(df):
                future_close = df.iloc[s_idx + days]['close']
                ret = (future_close / s['close'] - 1) * 100
                print(f"         +{days}日: {future_close:.2f} ({ret:+.2f}%)")
        print()
else:
    print("  未发现精准入场信号")
print()

# ============================================================
# 3. 最近60天走势概览
# ============================================================
print("=" * 70)
print("【三】最近60天走势概览")
print("=" * 70)

recent = df.tail(60).copy()
print(f"时间范围: {recent['trade_date'].iloc[0]} ~ {recent['trade_date'].iloc[-1]}")
print(f"区间最高: {recent['high'].max():.2f}  ({recent.loc[recent['high'].idxmax(), 'trade_date']})")
print(f"区间最低: {recent['low'].min():.2f}  ({recent.loc[recent['low'].idxmin(), 'trade_date']})")
print(f"区间涨幅: {(recent['close'].iloc[-1] / recent['close'].iloc[0] - 1) * 100:+.2f}%")
print()

# 当前状态
last = df.iloc[-1]
print(f"当前状态 ({last['trade_date']}):")
print(f"  收盘价: {last['close']:.2f}  涨跌幅: {last['pct_chg']:+.2f}%")
print(f"  MA5: {last['ma_bfq_5']:.2f}  MA10: {last['ma_bfq_10']:.2f}  MA20: {last['ma_bfq_20']:.2f}  MA60: {last['ma_bfq_60']:.2f}")
print(f"  距MA20: {(last['close'] / last['ma_bfq_20'] - 1) * 100:+.2f}%  距MA60: {(last['close'] / last['ma_bfq_60'] - 1) * 100:+.2f}%")
print(f"  量比: {last.get('volume_ratio', 0):.2f}  RSI6: {last.get('rsi_bfq_6', 0):.1f}")
print(f"  MACD DIF: {last.get('macd_dif_bfq', 0):.2f}  DEA: {last.get('macd_dea_bfq', 0):.2f}")
print(f"  KDJ-J: {last.get('kdj_j_bfq', 0):.1f}  K: {last.get('kdj_k_bfq', 0):.1f}")
print()

# ============================================================
# 4. 综合分析
# ============================================================
print("=" * 70)
print("【四】综合分析与建议")
print("=" * 70)
print()

# 找出最近一次波段低点
last_60 = df.tail(120)
low_idx = last_60['low'].idxmin()
low_date = df.loc[low_idx, 'trade_date']
low_price = df.loc[low_idx, 'low']
print(f"最近120日最低点: {low_price:.2f}  ({low_date})")
print(f"从最低点至今涨幅: {(last['close'] / low_price - 1) * 100:+.2f}%")
print()

# 判断当前位置
if last['close'] > last['ma_bfq_20']:
    ma20_dist = (last['close'] / last['ma_bfq_20'] - 1) * 100
    if ma20_dist < 5:
        print("当前位置: 贴近MA20，处于蓄势阶段")
    elif ma20_dist < 15:
        print("当前位置: MA20上方合理区间，趋势健康")
    else:
        print("当前位置: 距MA20较远，短期有回调风险")
else:
    print("当前位置: 跌破MA20，处于调整期")

print()
print("=" * 70)
