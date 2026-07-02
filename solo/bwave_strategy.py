"""
B浪低点识别策略 — 寻找A浪主升→B浪回调→二波启动的中线机会
========================================================
检测最新交易日的信号，对每只股票扫描最近250个交易日。

用法:
  python bwave_strategy.py --pool qualified          # 盘后全量扫描
  python bwave_strategy.py 600460.SH 002409.SZ       # 个股检测
  python bwave_strategy.py --debug 688170.SH          # 调试模式看细节

评分权重:
  A浪质量(30%) + B浪健康度(35%) + 趋势保持(20%) + 启动信号(15%)
  仅输出 BWaveScore ≥ 85 的股票
"""

import os, sys, argparse, sqlite3
from datetime import datetime
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DB = r'D:\mystock\cache_daily\stock_data.db'
OUTPUT_DIR = r'D:\mystock\solo\trend_feature_output'
os.makedirs(OUTPUT_DIR, exist_ok=True)


def get_data(ts_code: str) -> pd.DataFrame | None:
    conn = sqlite3.connect(DB)
    try:
        sql = """SELECT trade_date, open, high, low, close, pct_chg, vol, volume_ratio,
                        ma_bfq_5, ma_bfq_10, ma_bfq_20, ma_bfq_30, ma_bfq_60, ma_bfq_90,
                        macd_dif_bfq, macd_dea_bfq, macd_bfq,
                        rsi_bfq_6
                 FROM stk_factor_pro WHERE ts_code=? ORDER BY trade_date"""
        df = pd.read_sql(sql, conn, params=(ts_code,))
        if df.empty:
            return None
        df['trade_date'] = df['trade_date'].astype(str)
        df = df.fillna(0)

        # 计算缺失字段
        # MA120 / MA250
        df['ma_120'] = df['close'].rolling(120).mean().fillna(0)
        df['ma_250'] = df['close'].rolling(250).mean().fillna(0)

        # ATR(14)
        df['prev_close'] = df['close'].shift(1).fillna(0)
        df['tr'] = df.apply(
            lambda r: max(r['high'] - r['low'],
                          abs(r['high'] - r['prev_close']) if r['prev_close'] > 0 else 0,
                          abs(r['low'] - r['prev_close']) if r['prev_close'] > 0 else 0),
            axis=1
        )
        df['atr'] = df['tr'].rolling(14).mean().fillna(0)
        df = df.drop(columns=['prev_close', 'tr'])

        return df
    except Exception as e:
        return None
    finally:
        conn.close()


def detect_awave(df: pd.DataFrame, lookback: int = 120) -> dict | None:
    """
    识别A浪（主升浪）— 优化版本，基于波峰波谷检测
    条件：
    - 在最近lookback个交易日内（默认120）
    - 涨幅 ≥60%（优先≥80%）
    - 持续20-60个交易日
    - MA20上行，价格多数在MA20之上
    - 量比≥1.3
    """
    if len(df) < 130:
        return None

    lookback = min(lookback, len(df) - 10)
    start_idx = len(df) - lookback

    # 找局部低点和局部高点
    lows = []
    highs = []
    for i in range(start_idx, len(df) - 1):
        if df.iloc[i]['close'] <= df.iloc[i - 1]['close'] and df.iloc[i]['close'] <= df.iloc[i + 1]['close']:
            lows.append(i)
        if df.iloc[i]['close'] >= df.iloc[i - 1]['close'] and df.iloc[i]['close'] >= df.iloc[i + 1]['close']:
            highs.append(i)

    best = None

    # 对每个低点→高点对，检查A浪特征
    for a_start in lows:
        if a_start < start_idx:
            continue
        for a_end in highs:
            if a_end <= a_start + 20 or a_end > a_start + 60:
                continue
            if a_end >= len(df) - 5:
                continue

            start_price = df.iloc[a_start]['close']
            end_price = df.iloc[a_end]['close']
            if start_price <= 0:
                continue

            gain = (end_price / start_price - 1) * 100
            if gain < 60:
                continue

            duration = a_end - a_start

            ma20_slice = df.iloc[a_start:a_end + 1]['ma_bfq_20'].values
            ma20_up_count = sum(
                1 for i in range(1, len(ma20_slice))
                if ma20_slice[i] > ma20_slice[i - 1] and ma20_slice[i] > 0
            )
            ma20_up_ratio = ma20_up_count / max(len(ma20_slice) - 1, 1)
            if ma20_up_ratio < 0.6:
                continue

            above_ma20 = sum(
                1 for i in range(a_start, a_end + 1)
                if df.iloc[i]['close'] > df.iloc[i]['ma_bfq_20'] > 0
            )
            above_ratio = above_ma20 / max(duration, 1)

            a_vol = df.iloc[a_start:a_end + 1]['vol'].mean()
            vol_40 = df.iloc[max(0, a_start - 40):a_start]['vol'].mean()
            vol_ratio_a = a_vol / vol_40 if vol_40 > 0 else 0

            if above_ratio < 0.6 or vol_ratio_a < 1.3:
                continue

            score = 0
            if gain >= 80:
                score += 40
            elif gain >= 60:
                score += 25
            score += min(20, int(ma20_up_ratio * 20))
            score += min(20, int(above_ratio * 20))
            score += min(20, int(min(vol_ratio_a / 2, 1) * 20))

            if best is None or score > best['score']:
                best = {
                    'start_idx': a_start,
                    'end_idx': a_end,
                    'start_date': str(df.iloc[a_start]['trade_date']),
                    'end_date': str(df.iloc[a_end]['trade_date']),
                    'start_price': round(start_price, 2),
                    'end_price': round(end_price, 2),
                    'gain': round(gain, 1),
                    'duration': duration,
                    'ma20_up_ratio': round(ma20_up_ratio, 2),
                    'above_ma20_ratio': round(above_ratio, 2),
                    'vol_ratio': round(vol_ratio_a, 2),
                    'score': score,
                    'avg_vol': a_vol,
                }

    return best


def detect_all_awaves(df: pd.DataFrame, lookback: int = 0) -> list:
    """
    找数据中所有历史A浪（用于回测）
    返回按时间排序的A浪列表
    """
    if len(df) < 130:
        return []

    if lookback <= 0:
        lookback = len(df) - 10
    lookback = min(lookback, len(df) - 10)
    start_idx = len(df) - lookback

    lows = []
    highs = []
    for i in range(start_idx, len(df) - 1):
        if df.iloc[i]['close'] <= df.iloc[i - 1]['close'] and df.iloc[i]['close'] <= df.iloc[i + 1]['close']:
            lows.append(i)
        if df.iloc[i]['close'] >= df.iloc[i - 1]['close'] and df.iloc[i]['close'] >= df.iloc[i + 1]['close']:
            highs.append(i)

    awaves = []
    for a_start in lows:
        if a_start < start_idx:
            continue
        for a_end in highs:
            if a_end <= a_start + 20 or a_end > a_start + 60:
                continue
            if a_end >= len(df) - 5:
                continue

            start_price = df.iloc[a_start]['close']
            end_price = df.iloc[a_end]['close']
            if start_price <= 0:
                continue

            gain = (end_price / start_price - 1) * 100
            if gain < 60:
                continue

            duration = a_end - a_start

            ma20_slice = df.iloc[a_start:a_end + 1]['ma_bfq_20'].values
            ma20_up_count = sum(
                1 for i in range(1, len(ma20_slice))
                if ma20_slice[i] > ma20_slice[i - 1] and ma20_slice[i] > 0
            )
            ma20_up_ratio = ma20_up_count / max(len(ma20_slice) - 1, 1)
            if ma20_up_ratio < 0.6:
                continue

            above_ma20 = sum(
                1 for i in range(a_start, a_end + 1)
                if df.iloc[i]['close'] > df.iloc[i]['ma_bfq_20'] > 0
            )
            above_ratio = above_ma20 / max(duration, 1)

            a_vol = df.iloc[a_start:a_end + 1]['vol'].mean()
            vol_40 = df.iloc[max(0, a_start - 40):a_start]['vol'].mean()
            vol_ratio_a = a_vol / vol_40 if vol_40 > 0 else 0

            if above_ratio < 0.6 or vol_ratio_a < 1.3:
                continue

            score = 0
            if gain >= 80:
                score += 40
            elif gain >= 60:
                score += 25
            score += min(20, int(ma20_up_ratio * 20))
            score += min(20, int(above_ratio * 20))
            score += min(20, int(min(vol_ratio_a / 2, 1) * 20))

            awaves.append({
                'start_idx': a_start,
                'end_idx': a_end,
                'start_date': str(df.iloc[a_start]['trade_date']),
                'end_date': str(df.iloc[a_end]['trade_date']),
                'start_price': round(start_price, 2),
                'end_price': round(end_price, 2),
                'gain': round(gain, 1),
                'duration': duration,
                'ma20_up_ratio': round(ma20_up_ratio, 2),
                'above_ma20_ratio': round(above_ratio, 2),
                'vol_ratio': round(vol_ratio_a, 2),
                'score': score,
                'avg_vol': a_vol,
            })

    # 去重：同一end_idx只保留score最高的
    seen = {}
    for aw in awaves:
        key = aw['end_idx']
        if key not in seen or aw['score'] > seen[key]['score']:
            seen[key] = aw

    # 去重2：同一波上涨只保留最优A浪（start_idx接近的视为同一波）
    deduped = sorted(seen.values(), key=lambda x: (x['start_idx'], -x['score']))
    final = []
    for aw in deduped:
        if final and aw['start_idx'] - final[-1]['start_idx'] < 10:
            # 同一波上涨，保留score更高的
            if aw['score'] > final[-1]['score']:
                final[-1] = aw
        else:
            final.append(aw)

    return sorted(final, key=lambda x: x['end_idx'])


def detect_bwave_relaxed(df: pd.DataFrame, awave: dict) -> dict | None:
    """
    放宽版B浪检测 — 用于底背离候选
    降低门槛以获得更多候选:
    - 跌幅15%-45%（原20%-45%）
    - 时间≥A浪×0.6（原×0.8）
    - 缩量阈值根据A浪量能动态调整
    - 其他条件不变
    """
    a_end = awave['end_idx']
    a_high = awave['end_price']
    a_duration = awave['duration']
    a_avg_vol = awave['avg_vol']
    a_vol_ratio = awave.get('vol_ratio', 1.0)
    
    best = None
    search_end = min(a_end + a_duration * 2 + 10, len(df) - 5)
    
    # 根据A浪量能特征动态调整缩量阈值
    if a_vol_ratio > 2.0:
        vol_shrink_limit = 1.5  # 放量型A浪，B浪量能可以高达A浪的1.5倍
    elif a_vol_ratio > 1.5:
        vol_shrink_limit = 1.3  # 中等放量，B浪量能可以高达A浪的1.3倍
    else:
        vol_shrink_limit = 0.8  # 正常A浪，B浪量能需≤80%

    for b_low in range(a_end + int(a_duration * 0.6), search_end + 1):
        if b_low >= len(df):
            break

        seg = df.iloc[a_end:b_low + 1]
        real_low_idx = seg['close'].idxmin()
        low_price = df.loc[real_low_idx, 'close']

        drop = (a_high - low_price) / a_high * 100
        if drop < 15 or drop > 40:
            continue

        b_duration = real_low_idx - a_end
        if b_duration < a_duration * 0.6:
            continue

        recent_10_vol = df.iloc[max(real_low_idx - 9, a_end):real_low_idx + 1]['vol'].mean()
        vol_shrink = recent_10_vol / a_avg_vol if a_avg_vol > 0 else 0
        if vol_shrink > vol_shrink_limit:
            continue

        atr_start = df.iloc[a_end]['atr'] if df.iloc[a_end]['atr'] > 0 else 0
        atr_end = df.iloc[real_low_idx]['atr'] if df.iloc[real_low_idx]['atr'] > 0 else 0
        atr_drop = (atr_start - atr_end) / atr_start * 100 if atr_start > 0 else 0

        ma60 = df.iloc[real_low_idx]['ma_bfq_60']
        ma120 = df.iloc[real_low_idx]['ma_120']
        low_price_val = df.loc[real_low_idx, 'close']

        if ma120 > 0 and low_price_val < ma120 * 0.95:
            continue

        ma60_30ago = df.iloc[max(0, real_low_idx - 30)]['ma_bfq_60']
        ma60_up = ma60 > ma60_30ago if ma60_30ago > 0 else False

        time_ratio = b_duration / a_duration if a_duration > 0 else 0

        score = 0
        if 25 <= drop <= 35:
            score += 30
        elif 20 <= drop < 25 or 35 < drop <= 40:
            score += 20
        else:
            score += 10

        if 1.0 <= time_ratio <= 1.5:
            score += 25
        elif 0.6 <= time_ratio < 1.0 or 1.5 < time_ratio <= 2.0:
            score += 15
        else:
            score += 5

        if vol_shrink <= 0.5:
            score += 20
        elif vol_shrink <= 0.7:
            score += 15
        else:
            score += 5

        if atr_drop >= 30:
            score += 15
        elif atr_drop >= 15:
            score += 10
        else:
            score += 5

        ma60_dist = (low_price_val / ma60 - 1) * 100 if ma60 > 0 else 0
        if ma60_dist > 0:
            score += 10

        if best is None or score > best['score']:
            best = {
                'start_idx': a_end,
                'low_idx': real_low_idx,
                'start_date': str(df.iloc[a_end]['trade_date']),
                'low_date': str(df.loc[real_low_idx, 'trade_date']),
                'high_price': round(a_high, 2),
                'low_price': round(low_price, 2),
                'drop': round(drop, 1),
                'duration': b_duration,
                'time_ratio': round(time_ratio, 2),
                'vol_shrink_ratio': round(vol_shrink, 2),
                'atr_drop': round(atr_drop, 1),
                'ma60_dist': round(ma60_dist, 1),
                'ma60_up': ma60_up,
                'score': score,
            }

    return best


def detect_bwave(df: pd.DataFrame, awave: dict) -> dict | None:
    a_end = awave['end_idx']
    a_high = awave['end_price']
    a_duration = awave['duration']
    a_avg_vol = awave['avg_vol']
    a_vol_ratio = awave.get('vol_ratio', 1.0)
    
    best = None
    search_end = min(a_end + a_duration * 2 + 10, len(df) - 5)
    
    # 根据A浪量能特征动态调整缩量阈值
    # 放量型A浪（量比>2.0）：B浪量能可以更大，因为资金还在活跃
    if a_vol_ratio > 2.0:
        vol_shrink_limit = 1.5  # 放量型A浪，B浪量能可以高达A浪的1.5倍
    elif a_vol_ratio > 1.5:
        vol_shrink_limit = 1.2  # 中等放量，B浪量能可以高达A浪的1.2倍
    else:
        vol_shrink_limit = 0.7  # 正常A浪，B浪量能需≤70%

    for b_low in range(a_end + int(a_duration * 0.8), search_end + 1):
        if b_low >= len(df):
            break

        seg = df.iloc[a_end:b_low + 1]
        real_low_idx = seg['close'].idxmin()
        low_price = df.loc[real_low_idx, 'close']

        drop = (a_high - low_price) / a_high * 100
        if drop < 20 or drop > 45:
            continue

        b_duration = real_low_idx - a_end
        if b_duration < a_duration * 0.8:
            continue

        recent_10_vol = df.iloc[max(real_low_idx - 9, a_end):real_low_idx + 1]['vol'].mean()
        vol_shrink = recent_10_vol / a_avg_vol if a_avg_vol > 0 else 0
        if vol_shrink > vol_shrink_limit:
            continue

        atr_start = df.iloc[a_end]['atr'] if df.iloc[a_end]['atr'] > 0 else 0
        atr_end = df.iloc[real_low_idx]['atr'] if df.iloc[real_low_idx]['atr'] > 0 else 0
        atr_drop = (atr_start - atr_end) / atr_start * 100 if atr_start > 0 else 0

        ma60 = df.iloc[real_low_idx]['ma_bfq_60']
        ma120 = df.iloc[real_low_idx]['ma_120']
        low_price_val = df.loc[real_low_idx, 'close']

        if ma120 > 0 and low_price_val < ma120 * 0.97:
            continue

        ma60_30ago = df.iloc[max(0, real_low_idx - 30)]['ma_bfq_60']
        ma60_up = ma60 > ma60_30ago if ma60_30ago > 0 else False
        if not ma60_up:
            continue

        # B浪评分
        score = 0
        if 25 <= drop <= 35:
            score += 30
        elif 20 <= drop < 25 or 35 < drop <= 40:
            score += 20
        else:
            score += 10

        time_ratio = b_duration / a_duration if a_duration > 0 else 0
        if 1.0 <= time_ratio <= 1.5:
            score += 25
        elif 0.8 <= time_ratio < 1.0 or 1.5 < time_ratio <= 2.0:
            score += 15
        else:
            score += 5

        if vol_shrink <= 0.5:
            score += 20
        elif vol_shrink <= 0.6:
            score += 15
        else:
            score += 10

        if atr_drop >= 30:
            score += 15
        elif atr_drop >= 20:
            score += 10
        else:
            score += 5

        ma60_dist = (low_price_val / ma60 - 1) * 100 if ma60 > 0 else 0
        if ma60_dist > 0:
            score += 10

        if best is None or score > best['score']:
            best = {
                'start_idx': a_end,
                'low_idx': real_low_idx,
                'start_date': str(df.iloc[a_end]['trade_date']),
                'low_date': str(df.loc[real_low_idx, 'trade_date']),
                'high_price': round(a_high, 2),
                'low_price': round(low_price, 2),
                'drop': round(drop, 1),
                'duration': b_duration,
                'time_ratio': round(time_ratio, 2),
                'vol_shrink_ratio': round(vol_shrink, 2),
                'atr_drop': round(atr_drop, 1),
                'ma60_dist': round(ma60_dist, 1),
                'score': score,
            }

    return best


def check_launch_signal(df: pd.DataFrame, awave: dict, bwave: dict) -> dict | None:
    """
    检测B浪末端启动信号
    条件:
    - 价格已从B浪低点反弹至50%黄金分割位以上（确认B浪结束）
    - 价格未显著突破A浪高点（≤105% of A浪高点）— B浪末端特征
    - 从B浪低点反弹幅度≤35%（仍在初期阶段）
    - 放量 + (见底信号或RSI金叉或MACD改善) + (突破B浪平台或MA5金叉或MA10金叉)
    
    独立信号类型（分别提示，显示各自日期）:
    - 见底信号: 长下影小阳十字星（实体小，下影长）
    - RSI金叉: RSI从低于50穿越至50以上
    - MACD金叉: DIF上穿DEA
    """
    low_idx = bwave['low_idx']
    b_high = bwave['high_price']
    b_low = bwave['low_price']
    a_high = awave['end_price']
    recovery_mid = b_low + (b_high - b_low) * 0.382

    rsi_golden_date = None
    macd_golden_date = None
    bottom_signal_date = None
    
    scan_end = min(low_idx + 41, len(df))
    for idx in range(low_idx, scan_end):
        row = df.iloc[idx]
        close = row['close']
        
        dif = row.get('macd_dif_bfq', 0)
        dea = row.get('macd_dea_bfq', 0)
        prev_dif = df.iloc[idx - 1].get('macd_dif_bfq', 0) if idx > 0 else 0
        prev_dea = df.iloc[idx - 1].get('macd_dea_bfq', 0) if idx > 0 else 0
        
        if dif > dea and prev_dif <= prev_dea:
            macd_golden_date = str(row['trade_date'])
        
        rsi6 = row.get('rsi_bfq_6', 0)
        prev_rsi6 = df.iloc[idx - 1].get('rsi_bfq_6', 0) if idx > 0 else 0
        
        if rsi6 >= 50 and prev_rsi6 < 50:
            rsi_golden_date = str(row['trade_date'])
        
        open_p = row['open']
        high = row['high']
        low_p = row['low']
        range_p = high - low_p if high > low_p else 0.01
        body = abs(close - open_p)
        lower_shadow = min(close, open_p) - low_p
        
        if lower_shadow > range_p * 0.5 and body < range_p * 0.3 and close >= open_p:
            bottom_signal_date = str(row['trade_date'])

    for launch in range(scan_end - 1, low_idx - 1, -1):
        row = df.iloc[launch]
        close = row['close']
        open_p = row['open']
        high = row['high']
        low_p = row['low']
        vol = row['vol']

        if close < recovery_mid:
            continue

        if close > a_high * 1.00:
            continue

        b_recovery = (close / b_low - 1) * 100 if b_low > 0 else 0
        if b_recovery > 35:
            continue

        dist_to_a_high = (a_high / close - 1) * 100 if close > 0 else 0

        dif = row.get('macd_dif_bfq', 0)
        dea = row.get('macd_dea_bfq', 0)
        macd = row.get('macd_bfq', 0)
        prev_macd = df.iloc[launch - 1].get('macd_bfq', 0) if launch > 0 else 0

        macd_improved = (dif > dea) or (macd > prev_macd)

        ma5 = row.get('ma_bfq_5', 0)
        ma10 = row.get('ma_bfq_10', 0)
        ma20 = row.get('ma_bfq_20', 0)
        prev_ma5 = df.iloc[launch - 1].get('ma_bfq_5', 0) if launch > 0 else 0
        prev_ma10 = df.iloc[launch - 1].get('ma_bfq_10', 0) if launch > 0 else 0
        prev_ma20 = df.iloc[launch - 1].get('ma_bfq_20', 0) if launch > 0 else 0

        ma5_above_ma20 = ma5 > ma20 if (ma5 > 0 and ma20 > 0) else False
        ma5_crossing = (ma5 > ma20 and prev_ma5 <= prev_ma20) if (ma5 > 0 and ma20 > 0) else False
        ma10_crossing = (ma10 > ma20 and prev_ma10 <= prev_ma20) if (ma10 > 0 and ma20 > 0) else False

        avg_vol_20 = df.iloc[max(0, launch - 20):launch]['vol'].mean()
        vol_surge = vol > avg_vol_20 * 1.1 if avg_vol_20 > 0 else False

        seg = df.iloc[bwave['start_idx']:low_idx + 1]
        platform_high = seg['high'].max()
        break_platform = close > platform_high * 1.01

        rsi6 = row.get('rsi_bfq_6', 0)

        momentum_ok = macd_improved or (rsi_golden_date is not None) or (bottom_signal_date is not None)
        trend_ok = break_platform or ma5_crossing or ma10_crossing
        
        if not (vol_surge and momentum_ok and trend_ok):
            continue

        launch_score = 0
        if break_platform:
            launch_score += 30
        if ma5_crossing or ma5_above_ma20:
            launch_score += 25
        if ma10_crossing:
            launch_score += 20
        if dif > dea:
            launch_score += 20
        elif macd > prev_macd:
            launch_score += 10
        if rsi_golden_date is not None:
            launch_score += 15
        if bottom_signal_date is not None:
            launch_score += 10
        if vol_surge:
            launch_score += 15
        if 50 <= rsi6 <= 80:
            launch_score += 10

        pct_chg = row.get('pct_chg', 0)
        if pct_chg >= 5:
            launch_score += 5

        return {
            'launch_idx': launch,
            'launch_date': str(row['trade_date']),
            'launch_price': round(close, 2),
            'macd_improved': 1 if macd_improved else 0,
            'macd_golden': 1 if dif > dea else 0,
            'ma5_above_ma20': 1 if ma5_above_ma20 else 0,
            'ma5_crossing': 1 if ma5_crossing else 0,
            'ma10_crossing': 1 if ma10_crossing else 0,
            'break_platform': 1 if break_platform else 0,
            'vol_surge': 1 if vol_surge else 0,
            'vol_ratio': round(vol / avg_vol_20, 2) if avg_vol_20 > 0 else 0,
            'rsi6': round(rsi6, 1),
            'rsi_golden': 1 if rsi_golden_date is not None else 0,
            'bottom_signal': 1 if bottom_signal_date is not None else 0,
            'rsi_golden_date': rsi_golden_date,
            'macd_golden_date': macd_golden_date,
            'bottom_signal_date': bottom_signal_date,
            'pct_chg': round(pct_chg, 2),
            'b_recovery': round(b_recovery, 1),
            'dist_to_a_high': round(dist_to_a_high, 1),
            'score': launch_score,
        }

    return None


def detect_bwave_divergence(df: pd.DataFrame, awave: dict, bwave: dict) -> dict | None:
    """
    检测B浪中MACD底背离信号
    MACD底背离: B浪中价格创新低（或接近前低），但DIF线不再创新低
    这是左侧入场信号，意味着下跌动能衰竭、反转在即。
    条件:
    - 仍在B浪中（未突破A浪高点）
    - B浪内至少有两个可比的局部低点
    - 第二个低点价格 ≤ 第一个低点，但 DIF > 第一个低点
    - RSI辅助确认：第二个低点RSI ≥ 第一个低点
    - 信号时效：最近15个交易日内
    """
    a_high = awave['end_price']
    a_end = bwave['start_idx']
    b_low_idx = bwave['low_idx']
    b_low_price = bwave['low_price']

    # 当前价格不能突破A浪高点（还在B浪中）
    current_close = df.iloc[-1]['close']
    if current_close > a_high * 1.02:
        return None

    # 在B浪区间找局部低点
    seg = df.iloc[a_end:]
    if len(seg) < 20:
        return None

    low_indices = []
    for i in range(1, len(seg) - 1):
        if (seg.iloc[i]['close'] <= seg.iloc[i - 1]['close'] and
                seg.iloc[i]['close'] <= seg.iloc[i + 1]['close']):
            low_indices.append(a_end + i)

    # === 策略A: 标准底背离（两个局部低点）===
    if len(low_indices) >= 2:
        # 最近的两个局部低点
        p2 = low_indices[-1]
        p1 = low_indices[-2]

        p1_close = df.iloc[p1]['close']
        p2_close = df.iloc[p2]['close']
        p1_dif = df.iloc[p1]['macd_dif_bfq']
        p2_dif = df.iloc[p2]['macd_dif_bfq']

        # 底背离核心：价格持平或更低，DIF抬高（优化：要求抬高>10%）
        price_down = p2_close <= p1_close * 1.005
        dif_up = p2_dif > p1_dif * 1.10  # 优化：DIF抬高>10%
        
        # 优化：计算DIF抬高幅度
        dif_up_pct = (p2_dif - p1_dif) / abs(p1_dif) * 100 if p1_dif != 0 else 0
        if dif_up_pct <= 10:
            return None  # DIF抬高不足10%，过滤

        if price_down and dif_up:
            # 信号时效：最近的低点不能太久远（15个交易日以内）
            if len(df) - p2 <= 15:
                # 背离低点不能远低于B浪低点（>5%说明B浪结构已被破坏）
                if p2_close >= b_low_price * 0.95:
                    # RSI确认
                    p1_rsi = df.iloc[p1]['rsi_bfq_6']
                    p2_rsi = df.iloc[p2]['rsi_bfq_6']

                    # 计算MACD绿柱状态
                    last_macd = df.iloc[-1]['macd_bfq']
                    last_dif = df.iloc[-1]['macd_dif_bfq']
                    last_dea = df.iloc[-1]['macd_dea_bfq']

                    # 状态标记 — 使用背离低点(p2)的价格而非最新价
                    b_recovery_p2 = (p2_close / b_low_price - 1) * 100 if b_low_price > 0 else 0
                    dist_to_a_high = (a_high / p2_close - 1) * 100 if p2_close > 0 else 0

                    dif_recovery = (p2_dif / p1_dif - 1) * 100 if p1_dif > 0 else 0
                    rsi_higher = 1 if p2_rsi > p1_rsi else 0
                    macd_shrinking = 1 if last_macd < 0 and last_macd > df.iloc[-2]['macd_bfq'] else 0

                    vol = df.iloc[-1]['vol']
                    avg_vol_20 = df.iloc[max(0, len(df) - 21):len(df) - 1]['vol'].mean()
                    vol_shrink_now = vol / avg_vol_20 if avg_vol_20 > 0 else 1

                    return {
                        'launch_idx': p2,
                        'launch_date': str(df.iloc[p2]['trade_date']),
                        'launch_price': round(p2_close, 2),
                        'macd_improved': 1,
                        'macd_golden': 1 if last_dif > last_dea else 0,
                        'ma5_above_ma20': 1 if df.iloc[-1]['ma_bfq_5'] > df.iloc[-1]['ma_bfq_20'] > 0 else 0,
                        'ma5_crossing': 0,
                        'break_platform': 0,
                        'vol_surge': 1 if vol_shrink_now < 0.8 else 0,
                        'vol_ratio': round(1 / vol_shrink_now, 2) if vol_shrink_now > 0 else 0,
                        'rsi6': round(p2_rsi, 1),
                        'pct_chg': round(df.iloc[-1]['pct_chg'], 2),
                        'b_recovery': round(b_recovery_p2, 1),
                        'dist_to_a_high': round(dist_to_a_high, 1),
                        'signal_type': 'divergence',
                        'score': int(
                            30 +                      # 底背离基础分
                            min(20, int(dif_recovery)) +  # DIF抬高幅度
                            (10 if rsi_higher else 0) +  # RSI确认
                            (10 if macd_shrinking else 0) +  # 绿柱缩短
                            (10 if last_dif > last_dea else 0) +  # DIF上穿DEA
                            (5 if vol_shrink_now < 0.8 else 0) +  # 缩量
                            (5 if dist_to_a_high < 10 else 0),  # 距A浪高点较近
                        ),
                    }

    # === 策略B: 当日MACD底背离（当天数据即可判断，无需次日确认）===
    # 适用于今日价格在B浪低点附近，MACD已率先反转的情形
    today = df.iloc[-1]
    today_close = today['close']
    today_dif = today['macd_dif_bfq']

    # 条件1：今日价格在B浪低点附近（5%以内）
    near_b_low = (today_close / b_low_price - 1) * 100 <= 5 if b_low_price > 0 else False
    if not near_b_low:
        return None

    # 条件2：最近的低点中（倒推找严格局部低点），有一个与今日形成底背离
    # 找B浪内最近的一个严格局部低点（p1）做对比
    p1 = None
    for idx in reversed(low_indices):
        if idx >= a_end:
            p1 = idx
            break

    # 如果找不到严格局部低点，使用B浪最低点作为参考
    if p1 is None:
        p1 = b_low_idx

    # 用p1的价格/DIF 和今日对比
    p1_close = df.iloc[p1]['close']
    p1_dif = df.iloc[p1]['macd_dif_bfq']

    # 底背离：价格持平或更低，DIF抬高
    # 今日价格不高于p1价格的2%（基本相同或更低）
    price_not_higher = today_close <= p1_close * 1.02
    # DIF明显高于p1时的DIF
    dif_recovery = (today_dif / p1_dif - 1) * 100 if p1_dif != 0 else 0
    dif_higher = today_dif > p1_dif * 1.01

    if not (price_not_higher and dif_higher):
        return None

    # 信号时效：p1距今不超过15个交易日
    if len(df) - p1 > 15:
        return None

    # 当日RSI
    today_rsi = today['rsi_bfq_6']
    p1_rsi = df.iloc[p1]['rsi_bfq_6']
    rsi_higher = 1 if today_rsi > p1_rsi else 0

    # MACD状态
    today_macd = today['macd_bfq']
    prev_macd = df.iloc[-2]['macd_bfq'] if len(df) >= 2 else today_macd
    today_dea = today['macd_dea_bfq']
    macd_shrinking = 1 if today_macd < 0 and today_macd > prev_macd else 0

    # 量能
    today_vol = today['vol']
    avg_vol_20 = df.iloc[max(0, len(df) - 21):len(df) - 1]['vol'].mean()
    vol_shrink = today_vol / avg_vol_20 if avg_vol_20 > 0 else 1

    b_recovery = (today_close / b_low_price - 1) * 100 if b_low_price > 0 else 0
    dist_to_a_high = (a_high / today_close - 1) * 100 if today_close > 0 else 0

    return {
        'launch_idx': len(df) - 1,
        'launch_date': str(today['trade_date']),
        'launch_price': round(today_close, 2),
        'macd_improved': 1,
        'macd_golden': 1 if today_dif > today_dea else 0,
        'ma5_above_ma20': 1 if today['ma_bfq_5'] > today['ma_bfq_20'] > 0 else 0,
        'ma5_crossing': 0,
        'break_platform': 0,
        'vol_surge': 1 if vol_shrink < 0.8 else 0,
        'vol_ratio': round(1 / vol_shrink, 2) if vol_shrink > 0 else 0,
        'rsi6': round(today_rsi, 1),
        'pct_chg': round(today['pct_chg'], 2),
        'b_recovery': round(b_recovery, 1),
        'dist_to_a_high': round(dist_to_a_high, 1),
        'signal_type': 'divergence',
        'score': int(
            25 +                      # 当日底背离基础分（略低于标准底背离）
            min(25, int(dif_recovery)) +  # DIF抬高幅度
            (10 if rsi_higher else 0) +  # RSI确认
            (10 if macd_shrinking else 0) +  # 绿柱缩短
            (10 if today_dif > today_dea else 0) +  # DIF上穿DEA
            (5 if vol_shrink < 0.8 else 0) +  # 缩量
            (5 if dist_to_a_high < 10 else 0),  # 距A浪高点较近
        ),
    }


def calc_bwave_score(awave: dict, bwave: dict, launch: dict) -> dict:
    # A浪质量（满分100 → 权重30%）
    a_score = min(100, int(
        min(max(awave['gain'] - 40, 0) / 60 * 50, 50) +
        awave['ma20_up_ratio'] * 20 +
        awave['above_ma20_ratio'] * 15 +
        min(awave['vol_ratio'] / 2, 1) * 15
    ))

    # B浪健康度（满分100 → 权重35%）
    b_score = min(100, int(
        (25 if 25 <= bwave['drop'] <= 35 else
         20 if (20 <= bwave['drop'] < 25 or 35 < bwave['drop'] <= 40) else 10) * 1.5 +
        (25 if 1.0 <= bwave['time_ratio'] <= 1.5 else
         15 if 0.8 <= bwave['time_ratio'] < 1.0 else 5) * 1.2 +
        (20 if bwave['vol_shrink_ratio'] <= 0.5 else
         15 if bwave['vol_shrink_ratio'] <= 0.6 else 10) * 1.0 +
        (15 if bwave['atr_drop'] >= 30 else
         10 if bwave['atr_drop'] >= 20 else 5) * 0.8 +
        (10 if bwave['ma60_dist'] > 0 else 0) * 0.5
    ))

    # 趋势保持（满分100 → 权重20%）
    t_score = 0
    if bwave['ma60_dist'] > 5:
        t_score += 40
    elif bwave['ma60_dist'] > 0:
        t_score += 25
    if bwave['atr_drop'] >= 20:
        t_score += 30
    if bwave['vol_shrink_ratio'] <= 0.6:
        t_score += 30
    t_score = min(100, t_score)

    # 启动信号（满分100 → 权重15%）
    l_score = 0
    if launch['macd_golden']:
        l_score += 25
    elif launch['macd_improved']:
        l_score += 10
    if launch['ma5_crossing'] or launch['ma5_above_ma20']:
        l_score += 20
    if launch['break_platform']:
        l_score += 15
    if launch['vol_surge']:
        l_score += 10
    l_score += 5 if 50 <= launch['rsi6'] <= 80 else 0

    # B浪末端位置加分：距A浪高点越近（未突破），越说明是B浪末端
    dist = launch.get('dist_to_a_high', 5)
    if 0 < dist <= 3:
        l_score += 15   # 距A浪高点3%以内
    elif 3 < dist <= 8:
        l_score += 10   # 距A浪高点3-8%
    elif dist > 8:
        l_score += 5    # 距A浪高点较远但仍在B浪末端

    l_score = min(100, l_score)

    total = round(a_score * 0.30 + b_score * 0.35 + t_score * 0.20 + l_score * 0.15, 1)

    # 优化1：A浪涨幅 > 100% → 评分降权-10分（可能主力已出货）
    if awave.get('gain', 0) > 100:
        total -= 10
        a_score -= 5  # A浪质量也降权

    return {
        'total': total,
        'a_score': a_score,
        'b_score': b_score,
        't_score': t_score,
        'l_score': l_score,
    }


def calc_divergence_score(awave: dict, bwave: dict, div_signal: dict) -> dict:
    """
    底背离信号专用评分
    权重: A浪质量30% + B浪健康度25% + 底背离强度30% + 趋势保持15%
    """
    a_score = min(100, int(
        min(max(awave['gain'] - 40, 0) / 60 * 50, 50) +
        awave['ma20_up_ratio'] * 20 +
        awave['above_ma20_ratio'] * 15 +
        min(awave['vol_ratio'] / 2, 1) * 15
    ))

    # B浪健康度 25%
    b_score = min(100, int(
        (25 if 25 <= bwave['drop'] <= 35 else
         20 if (20 <= bwave['drop'] < 25 or 35 < bwave['drop'] <= 40) else 10) * 1.2 +
        (25 if 1.0 <= bwave['time_ratio'] <= 1.5 else
         15 if 0.6 <= bwave['time_ratio'] < 1.0 else 5) * 1.0 +
        (20 if bwave['vol_shrink_ratio'] <= 0.5 else
         15 if bwave['vol_shrink_ratio'] <= 0.7 else 5) * 1.0 +
        (15 if bwave.get('atr_drop', 0) >= 30 else
         10 if bwave.get('atr_drop', 0) >= 15 else 5) * 0.8 +
        (10 if bwave.get('ma60_up', False) else 5) * 0.5
    ))

    # 趋势保持 15%
    t_score = 0
    if bwave.get('ma60_dist', -10) > 0:
        t_score += 40
    elif bwave.get('ma60_dist', -10) > -5:
        t_score += 25
    if bwave.get('atr_drop', 0) >= 15:
        t_score += 30
    if bwave.get('vol_shrink_ratio', 1) <= 0.7:
        t_score += 30
    t_score = min(100, t_score)

    # 底背离强度 30%
    d_score = div_signal.get('score', 50)
    d_score = min(100, max(0, int(d_score)))

    total = round(a_score * 0.30 + b_score * 0.25 + d_score * 0.30 + t_score * 0.15, 1)

    return {
        'total': total,
        'a_score': a_score,
        'b_score': b_score,
        't_score': t_score,
        'l_score': d_score,
    }


def detect_bwave_full(ts_code: str, backtest_idx: int = -1) -> dict | None:
    df = get_data(ts_code)
    if df is None or len(df) < 250:
        return None

    if backtest_idx > 0:
        df = df.iloc[:backtest_idx + 1].reset_index(drop=True)

    latest_date = df.iloc[-1]['trade_date']

    awave = detect_awave(df)
    if awave is None:
        return None

    # 1) 严格B浪 → 启动信号
    bwave = detect_bwave(df, awave)
    if bwave:
        launch = check_launch_signal(df, awave, bwave)
        if launch:
            launch_idx = launch['launch_idx']
            if len(df) - launch_idx <= 10:
                score = calc_bwave_score(awave, bwave, launch)
                if score['total'] >= 65:
                    entry_price = df.iloc[launch_idx]['close']
                    rets = {}
                    for w in [1, 5, 10, 20]:
                        fi = min(launch_idx + w, len(df) - 1)
                        rets[w] = round((df.iloc[fi]['close'] / entry_price - 1) * 100, 2) if entry_price > 0 else 0
                    return {**make_result_base(ts_code, latest_date, awave, bwave, launch, score, rets),
                            'signal_type': 'launch'}

    # 2) 严格B浪 → 底背离
    if bwave:
        div = detect_bwave_divergence(df, awave, bwave)
        if div:
            score = calc_divergence_score(awave, bwave, div)
            if score['total'] >= 60:
                rets = {}
                div_idx = div['launch_idx']
                entry_price = df.iloc[div_idx]['close']
                for w in [1, 5, 10, 20]:
                    fi = min(div_idx + w, len(df) - 1)
                    rets[w] = round((df.iloc[fi]['close'] / entry_price - 1) * 100, 2) if entry_price > 0 else 0
                return {**make_result_base(ts_code, latest_date, awave, bwave, div, score, rets),
                        'signal_type': 'divergence'}

    # 3) 放宽B浪 → 启动信号
    bwave_r = detect_bwave_relaxed(df, awave)
    if bwave_r:
        launch = check_launch_signal(df, awave, bwave_r)
        if launch:
            launch_idx = launch['launch_idx']
            if len(df) - launch_idx <= 10:
                score = calc_bwave_score(awave, bwave_r, launch)
                if score['total'] >= 60:
                    entry_price = df.iloc[launch_idx]['close']
                    rets = {}
                    for w in [1, 5, 10, 20]:
                        fi = min(launch_idx + w, len(df) - 1)
                        rets[w] = round((df.iloc[fi]['close'] / entry_price - 1) * 100, 2) if entry_price > 0 else 0
                    return {**make_result_base(ts_code, latest_date, awave, bwave_r, launch, score, rets),
                            'signal_type': 'launch'}

    # 4) 放宽B浪 → 底背离
    if bwave_r:
        div = detect_bwave_divergence(df, awave, bwave_r)
        if div:
            score = calc_divergence_score(awave, bwave_r, div)
            if score['total'] >= 60:
                rets = {}
                div_idx = div['launch_idx']
                entry_price = df.iloc[div_idx]['close']
                for w in [1, 5, 10, 20]:
                    fi = min(div_idx + w, len(df) - 1)
                    rets[w] = round((df.iloc[fi]['close'] / entry_price - 1) * 100, 2) if entry_price > 0 else 0
                return {**make_result_base(ts_code, latest_date, awave, bwave_r, div, score, rets),
                        'signal_type': 'divergence'}

    return None


def make_result_base(ts_code, latest_date, awave, bwave, sig, score, rets):
    return {
        'ts_code': ts_code,
        'today': latest_date,
        'launch_date': sig['launch_date'],
        'a_start_date': awave['start_date'],
        'a_end_date': awave['end_date'],
        'a_gain': awave['gain'],
        'a_duration': awave['duration'],
        'a_vol_ratio': awave['vol_ratio'],
        'b_start_date': bwave['start_date'],
        'b_low_date': bwave['low_date'],
        'b_drop': bwave['drop'],
        'b_duration': bwave['duration'],
        'b_time_ratio': bwave['time_ratio'],
        'b_vol_shrink': bwave['vol_shrink_ratio'],
        'b_atr_drop': bwave['atr_drop'],
        'b_ma60_dist': bwave['ma60_dist'],
        'launch_price': sig['launch_price'],
        'launch_pct_chg': sig['pct_chg'],
        'launch_vol_ratio': sig['vol_ratio'],
        'launch_macd_golden': sig['macd_golden'],
        'launch_ma5_crossing': sig['ma5_crossing'],
        'launch_break_platform': sig['break_platform'],
        'launch_rsi6': sig['rsi6'],
        'launch_b_recovery': sig['b_recovery'],
        'launch_dist_to_a_high': sig['dist_to_a_high'],
        'launch_rsi_golden': sig.get('rsi_golden', 0),
        'launch_bottom_signal': sig.get('bottom_signal', 0),
        'bottom_signal_date': sig.get('bottom_signal_date', ''),
        'rsi_golden_date': sig.get('rsi_golden_date', ''),
        'macd_golden_date': sig.get('macd_golden_date', ''),
        'signal_type': '',
        'bwave_score': score['total'],
        'a_score': score['a_score'],
        'b_score': score['b_score'],
        't_score': score['t_score'],
        'l_score': score['l_score'],
        'return_1d': rets[1],
        'return_5d': rets[5],
        'return_10d': rets[10],
        'return_20d': rets[20],
    }


def normalize_ts_code(code: str) -> str:
    code = code.strip().upper()
    if '.' in code:
        return code
    code = code.zfill(6)
    if code.startswith(('6', '9')):
        return code + '.SH'
    return code + '.SZ'


def load_qualified_pool(pool_type: str = 'qualified') -> list:
    candidates = [
        r"D:\mystock\solo\multi_factor_picker\output",
        r"D:\mystock\report_daily",
    ]
    csv_path = None
    prefix = 'bull_all_' if pool_type == 'all' else 'bull_stocks_'
    for base_dir in candidates:
        if not os.path.isdir(base_dir):
            continue
        files = sorted([f for f in os.listdir(base_dir)
                        if f.startswith(prefix) and f.endswith('.csv')],
                       reverse=True)
        if files:
            csv_path = os.path.join(base_dir, files[0])
            break
        if pool_type == 'qualified':
            fixed = os.path.join(base_dir, "bull_stocks_qualified.csv")
            if os.path.exists(fixed):
                csv_path = fixed
                break
    if csv_path is None:
        return []
    df = pd.read_csv(csv_path, encoding='utf-8-sig')
    return [normalize_ts_code(str(c)) for c in df['code'].tolist()]


def log(msg: str):
    print(f"  {msg}", flush=True)


def main():
    parser = argparse.ArgumentParser(description='B浪低点识别策略')
    parser.add_argument('codes', nargs='*')
    parser.add_argument('--pool', choices=['default', 'qualified', 'all'], default='qualified')
    parser.add_argument('--min-score', type=int, default=65)
    parser.add_argument('--debug', type=str, default='')
    parser.add_argument('--backtest', action='store_true',
                        help='历史回测模式：扫描每个交易日，统计历史信号表现')
    args = parser.parse_args()

    if args.debug:
        ts_code = normalize_ts_code(args.debug)
        df = get_data(ts_code)
        if df is None or len(df) < 130:
            log(f"无法获取 {ts_code} 数据")
            return
        awave = detect_awave(df)
        if awave:
            log(f"A浪: {awave['start_date']}~{awave['end_date']} 涨幅={awave['gain']}% 持续{awave['duration']}天")
            bwave = detect_bwave(df, awave)
            bwave_r = detect_bwave_relaxed(df, awave)
            if bwave:
                log(f"B浪(严格): {bwave['start_date']}~{bwave['low_date']} 回调{bwave['drop']}% "
                        f"持续{bwave['duration']}天 缩量{bwave['vol_shrink_ratio']} ATR降{bwave['atr_drop']}%")
                launch = check_launch_signal(df, awave, bwave)
                if launch:
                    score = calc_bwave_score(awave, bwave, launch)
                    log(f"启动信号: {launch['launch_date']} 评分={score['total']} "
                        f"(A={score['a_score']} B={score['b_score']} T={score['t_score']} L={score['l_score']})")
                    sig_types = []
                    if launch.get('bottom_signal_date'):
                        sig_types.append(f"见底({launch['bottom_signal_date']})")
                    if launch.get('rsi_golden_date'):
                        sig_types.append(f"RSI金叉({launch['rsi_golden_date']})")
                    if launch.get('macd_golden_date'):
                        sig_types.append(f"MACD金叉({launch['macd_golden_date']})")
                    log(f"  信号类型: {','.join(sig_types) if sig_types else '其他'}")
                    log(f"  距A高={launch['dist_to_a_high']}% 反弹={launch['b_recovery']}% "
                        f"突破平台={launch['break_platform']} MA5金叉={launch['ma5_crossing']} "
                        f"MACD金叉={launch['macd_golden']} RSI金叉={launch.get('rsi_golden',0)} "
                        f"见底={launch.get('bottom_signal',0)} 放量={launch['vol_surge']}")
                div = detect_bwave_divergence(df, awave, bwave)
                if div:
                    s = calc_divergence_score(awave, bwave, div)
                    log(f"底背离信号: 评分={s['total']} (DIF抬高+{div.get('score',0)}分) "
                        f"RSI={div['rsi6']} 距A高={div['dist_to_a_high']}%")
                if not launch and not div:
                    log("未检测到启动信号或底背离")
            if bwave_r:
                log(f"B浪(放宽): {bwave_r['start_date']}~{bwave_r['low_date']} 回调{bwave_r['drop']}% "
                        f"持续{bwave_r['duration']}天 缩量{bwave_r['vol_shrink_ratio']}")
                launch = check_launch_signal(df, awave, bwave_r)
                if launch:
                    score = calc_bwave_score(awave, bwave_r, launch)
                    log(f"启动信号(放宽): {launch['launch_date']} 评分={score['total']}")
                    sig_types = []
                    if launch.get('bottom_signal_date'):
                        sig_types.append(f"见底({launch['bottom_signal_date']})")
                    if launch.get('rsi_golden_date'):
                        sig_types.append(f"RSI金叉({launch['rsi_golden_date']})")
                    if launch.get('macd_golden_date'):
                        sig_types.append(f"MACD金叉({launch['macd_golden_date']})")
                    log(f"  信号类型: {','.join(sig_types) if sig_types else '其他'}")
                    log(f"  距A高={launch['dist_to_a_high']}% 反弹={launch['b_recovery']}% "
                        f"突破平台={launch['break_platform']} MA5金叉={launch['ma5_crossing']} "
                        f"MACD金叉={launch['macd_golden']} RSI金叉={launch.get('rsi_golden',0)} "
                        f"见底={launch.get('bottom_signal',0)} 放量={launch['vol_surge']}")
                div = detect_bwave_divergence(df, awave, bwave_r)
                if div:
                    s = calc_divergence_score(awave, bwave_r, div)
                    log(f"底背离信号(放宽): 评分={s['total']}")
            else:
                log("未检测到B浪")
        else:
            log("未检测到A浪")
        return

    if args.codes:
        stock_codes = [normalize_ts_code(c) for c in args.codes]
    elif args.pool in ['qualified', 'all']:
        stock_codes = load_qualified_pool(args.pool)
        if not stock_codes:
            log("[错误] 股票池为空")
            return
    else:
        stock_codes = []

    mode = "历史回测(Backtest)" if args.backtest else "盘后扫描"
    log(f"B浪低点识别策略 — {mode}")
    log(f"股票池: {args.pool} ({len(stock_codes)}只)")
    log(f"最低评分: {args.min_score}")
    log("")

    if args.backtest:
        # === 真正的历史回测：逐日切片检测 ===
        # 对每只股票，找所有历史A浪，对每个A浪逐日切片检测信号
        # 信号只在首次出现时记录，避免重复；收益用真实未来数据计算
        all_results = []
        total = len(stock_codes)
        diag = {'total': 0, 'a_wave': 0, 'scanned': 0, 'signals': 0,
                'launch': 0, 'divergence': 0}

        for i, ts_code in enumerate(stock_codes):
            df_full = get_data(ts_code)
            if df_full is None or len(df_full) < 250:
                continue
            diag['total'] += 1

            # 找所有历史A浪
            awaves = detect_all_awaves(df_full)
            if not awaves:
                continue
            diag['a_wave'] += 1

            for awave_full in awaves:
                a_end_idx = awave_full['end_idx']
                a_duration = awave_full['duration']

                # 回测区间：B浪低点可能形成后 ~ A浪结束后100天（或数据末尾前20天）
                bt_start = a_end_idx + int(a_duration * 0.6)
                bt_end = min(a_end_idx + a_duration * 3 + 10, len(df_full) - 20)
                if bt_start >= bt_end:
                    continue
                diag['scanned'] += 1

                # 记录已触发的信号，避免同一A浪周期内重复记录
                triggered_signals = set()

                # 逐日切片检测
                for day_idx in range(bt_start, bt_end):
                    df_slice = df_full.iloc[:day_idx + 1].reset_index(drop=True)
                    # A浪是历史事实，直接用全量数据中检测到的A浪
                    # 切片数据包含了A浪完整区间，指标值一致
                    awave = awave_full

                    sig = None
                    score = None
                    bwave_used = None
                    signal_type = None

                    # 1) 严格B浪 → 启动信号
                    bwave = detect_bwave(df_slice, awave)
                    if bwave:
                        launch = check_launch_signal(df_slice, awave, bwave)
                        if launch:
                            s = calc_bwave_score(awave, bwave, launch)
                            if s['total'] >= args.min_score:
                                sig = launch
                                score = s
                                bwave_used = bwave
                                signal_type = '启动'
                                diag['launch'] += 1

                    # 2) 放宽B浪 → 启动信号
                    if not sig:
                        bwave_r = detect_bwave_relaxed(df_slice, awave) if not bwave else None
                        if bwave_r:
                            launch = check_launch_signal(df_slice, awave, bwave_r)
                            if launch:
                                s = calc_bwave_score(awave, bwave_r, launch)
                                if s['total'] >= max(args.min_score - 5, 55):
                                    sig = launch
                                    score = s
                                    bwave_used = bwave_r
                                    signal_type = '启动'
                                    diag['launch'] += 1

                    # 3) 严格B浪 → 底背离
                    if not sig and bwave:
                        div = detect_bwave_divergence(df_slice, awave, bwave)
                        if div:
                            s = calc_divergence_score(awave, bwave, div)
                            if s['total'] >= max(args.min_score - 5, 55):
                                sig = div
                                score = s
                                bwave_used = bwave
                                signal_type = '底背离'
                                diag['divergence'] += 1

                    # 4) 放宽B浪 → 底背离
                    if not sig:
                        if not bwave:
                            bwave_r = detect_bwave_relaxed(df_slice, awave)
                        else:
                            bwave_r = bwave
                        if bwave_r:
                            div = detect_bwave_divergence(df_slice, awave, bwave_r)
                            if div:
                                s = calc_divergence_score(awave, bwave_r, div)
                                if s['total'] >= max(args.min_score - 5, 55):
                                    sig = div
                                    score = s
                                    bwave_used = bwave_r
                                    signal_type = '底背离'
                                    diag['divergence'] += 1

                    if not sig:
                        continue

                    # 去重
                    sig_key = (signal_type, sig['launch_date'])
                    if sig_key in triggered_signals:
                        continue
                    triggered_signals.add(sig_key)

                    # 用全量数据计算真实未来收益
                    sig_idx = sig['launch_idx']
                    entry_price = df_full.iloc[sig_idx]['close']
                    rets = {}
                    for w in [1, 5, 10, 20]:
                        fi = min(sig_idx + w, len(df_full) - 1)
                        rets[w] = round((df_full.iloc[fi]['close'] / entry_price - 1) * 100, 2) if entry_price > 0 else 0

                    tags = []
                    if sig.get('bottom_signal_date'):
                        tags.append(f"见底{sig['bottom_signal_date']}")
                    if sig.get('rsi_golden_date'):
                        tags.append(f"RSI金叉{sig['rsi_golden_date']}")
                    if sig.get('macd_golden_date'):
                        tags.append(f"MACD金叉{sig['macd_golden_date']}")

                    all_results.append({
                        **make_result_base(ts_code, df_full.iloc[-1]['trade_date'],
                                           awave, bwave_used, sig, score, rets),
                        'signal_type': signal_type,
                        'signal_tags': ','.join(tags) if tags else '',
                        'backtest_date': str(df_full.iloc[day_idx]['trade_date']),
                    })
                    diag['signals'] += 1

            if (i + 1) % 50 == 0:
                log(f"[{i+1}/{total}] 扫描中...已发现{len(all_results)}个历史信号")
                # 每50只保存中间CSV，避免进程中断丢失数据
                if all_results:
                    _tmp_df = pd.DataFrame(all_results)
                    _tmp_path = os.path.join(OUTPUT_DIR, f"bwave_backtest_{total}_{i+1}_partial.csv")
                    _tmp_df.to_csv(_tmp_path, index=False, encoding='utf-8-sig')

        # === 回测统计输出 ===
        print(f"\n{'='*60}")
        print(f"  历史回测统计 (共{diag['total']}只股票)")
        print(f"{'='*60}")
        print(f"  A浪检测通过:       {diag['a_wave']:>4}")
        print(f"  进入回测扫描:      {diag['scanned']:>4}")
        print(f"  启动信号:          {diag['launch']:>4}")
        print(f"  底背离信号:        {diag['divergence']:>4}")
        print(f"  去重后有效信号:    {diag['signals']:>4}")

        if all_results:
            df_bt = pd.DataFrame(all_results)
            # 按信号类型分别统计
            for sig_type in ['启动', '底背离']:
                sub = df_bt[df_bt['signal_type'] == sig_type]
                if sub.empty:
                    continue
                print(f"\n  --- {sig_type}信号统计 ({len(sub)}个) ---")
                for w in [1, 5, 10, 20]:
                    col = f'return_{w}d'
                    if col in sub.columns:
                        r = sub[col].dropna()
                        if len(r) > 0:
                            wins = r[r > 0]
                            print(f"    +{w:>2}d: 均={r.mean():>6.2f}%  胜率={len(wins)/len(r)*100:>4.0f}%  "
                                  f"中位={r.median():>6.2f}%  亏>15%={(r<-15).sum():>3}  赚>15%={(r>15).sum():>3}")

            # 信号子类型统计（见底/RSI金叉/MACD金叉）
            print(f"\n  --- 子信号统计 ---")
            for tag_name in ['见底', 'RSI金叉', 'MACD金叉']:
                sub = df_bt[df_bt['signal_tags'].str.contains(tag_name, na=False)]
                if sub.empty:
                    continue
                for w in [5, 10]:
                    col = f'return_{w}d'
                    if col in sub.columns:
                        r = sub[col].dropna()
                        if len(r) > 0:
                            wins = r[r > 0]
                            print(f"    {tag_name} +{w:>2}d: 均={r.mean():>6.2f}%  胜率={len(wins)/len(r)*100:>4.0f}%  样本={len(r)}")
    else:
        # 当前模式：只检测最新信号
        all_results = []
        total = len(stock_codes)
        diag = {'checked': 0, 'a_wave': 0, 'b_wave': 0, 'launch': 0, 'b_divergence': 0, 'score_pass': 0}

        for i, ts_code in enumerate(stock_codes):
            if (i + 1) % 100 == 0:
                log(f"[{i+1}/{total}] 扫描中...已发现{len(all_results)}个B浪信号")

            try:
                df = get_data(ts_code)
                if df is None or len(df) < 250:
                    continue
                diag['checked'] += 1

                awave = detect_awave(df)
                if awave is None:
                    continue
                diag['a_wave'] += 1

                bwave = detect_bwave(df, awave)
                bwave_r = detect_bwave_relaxed(df, awave)
                if not bwave and not bwave_r:
                    continue
                diag['b_wave'] += 1

                signal_type = None
                sig = None
                score = None

                if bwave:
                    launch = check_launch_signal(df, awave, bwave)
                    if launch and len(df) - launch['launch_idx'] <= 10:
                        s = calc_bwave_score(awave, bwave, launch)
                        if s['total'] >= args.min_score:
                            signal_type = '启动'
                            sig = launch
                            score = s
                            bwave_used = bwave
                            diag['launch'] += 1

                if not sig and bwave_r:
                    launch = check_launch_signal(df, awave, bwave_r)
                    if launch and len(df) - launch['launch_idx'] <= 10:
                        s = calc_bwave_score(awave, bwave_r, launch)
                        if s['total'] >= max(args.min_score - 5, 55):
                            signal_type = '启动'
                            sig = launch
                            score = s
                            bwave_used = bwave_r
                            diag['launch'] += 1

                if not sig and bwave:
                    div = detect_bwave_divergence(df, awave, bwave)
                    if div:
                        s = calc_divergence_score(awave, bwave, div)
                        if s['total'] >= max(args.min_score - 5, 55):
                            signal_type = '底背离'
                            sig = div
                            score = s
                            bwave_used = bwave
                            diag['b_divergence'] += 1

                if not sig and bwave_r:
                    div = detect_bwave_divergence(df, awave, bwave_r)
                    if div:
                        s = calc_divergence_score(awave, bwave_r, div)
                        if s['total'] >= max(args.min_score - 5, 55):
                            signal_type = '底背离'
                            sig = div
                            score = s
                            bwave_used = bwave_r
                            diag['b_divergence'] += 1

                if not sig:
                    continue
                diag['score_pass'] += 1

                idx = sig['launch_idx']
                entry_price = df.iloc[idx]['close']
                rets = {}
                for w in [1, 5, 10, 20]:
                    fi = min(idx + w, len(df) - 1)
                    rets[w] = round((df.iloc[fi]['close'] / entry_price - 1) * 100, 2) if entry_price > 0 else 0

                tags = []
                if sig.get('bottom_signal_date'):
                    tags.append(f"见底{sig['bottom_signal_date']}")
                if sig.get('rsi_golden_date'):
                    tags.append(f"RSI金叉{sig['rsi_golden_date']}")
                if sig.get('macd_golden_date'):
                    tags.append(f"MACD金叉{sig['macd_golden_date']}")
                
                sub_signals = []
                if sig.get('bottom_signal_date'):
                    sub_signals.append(('见底', sig['bottom_signal_date']))
                if sig.get('rsi_golden_date'):
                    sub_signals.append(('RSI金叉', sig['rsi_golden_date']))
                if sig.get('macd_golden_date'):
                    sub_signals.append(('MACD金叉', sig['macd_golden_date']))
                
                if sub_signals:
                    for sub_type, sub_date in sub_signals:
                        row = make_result_base(ts_code, df.iloc[-1]['trade_date'],
                                              awave, bwave_used, sig, score, rets)
                        row['signal_type'] = sub_type
                        row['signal_tags'] = f"{sub_type}{sub_date}"
                        row['launch_date'] = sub_date
                        all_results.append(row)
                else:
                    row = make_result_base(ts_code, df.iloc[-1]['trade_date'],
                                          awave, bwave_used, sig, score, rets)
                    row['signal_type'] = signal_type
                    row['signal_tags'] = ''
                    all_results.append(row)
            except Exception:
                continue

        # 诊断输出
        print(f"\n{'='*55}")
        print(f"  诊断分析 (共{diag['checked']}只股票)")
        print(f"{'='*55}")
        print(f"  A浪检测通过:       {diag['a_wave']:>4} ({diag['a_wave']/max(diag['checked'],1)*100:.0f}%)")
        print(f"  B浪检测通过:       {diag['b_wave']:>4} ({diag['b_wave']/max(diag['a_wave'],1)*100:.0f}%)")
        print(f"  启动信号:          {diag['launch']:>4}")
        print(f"  底背离信号:        {diag['b_divergence']:>4}")
        print(f"  评分超过{args.min_score}分: {len(all_results)}")
        print()

    log(f"\n扫描完成！BWaveScore≥{args.min_score}: {len(all_results)} 个")

    if not all_results:
        log("  无符合条件的B浪信号")
        return

    df_out = pd.DataFrame(all_results)
    df_out = df_out.sort_values('bwave_score', ascending=False).reset_index(drop=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    csv_path = os.path.join(OUTPUT_DIR, f"bwave_{timestamp}_{args.pool}.csv")
    df_out.to_csv(csv_path, index=False, encoding='utf-8-sig')
    log(f"  CSV: {csv_path}")

    print(f"\n{'='*85}")
    print(f"  B浪低点识别 — 按 BWaveScore 降序")
    print(f"{'='*85}")

    display_cols = ['bwave_score', 'signal_type', 'signal_tags', 'ts_code',
                    'a_start_date', 'a_gain', 'a_duration',
                    'b_drop', 'b_duration', 'b_vol_shrink',
                    'launch_date', 'launch_pct_chg', 'launch_dist_to_a_high',
                    'return_5d', 'return_10d']
    display_cols = [c for c in display_cols if c in df_out.columns]

    headers = {'bwave_score': '评分', 'signal_type': '类型', 'signal_tags': '信号', 'ts_code': '代码',
               'a_start_date': 'A起点', 'a_gain': 'A涨%', 'a_duration': 'A天',
               'b_drop': 'B跌%', 'b_duration': 'B天', 'b_vol_shrink': '缩量',
               'launch_date': '启动日', 'launch_pct_chg': '涨幅',
               'launch_dist_to_a_high': '距A高%',
               'return_5d': '+5日', 'return_10d': '+10日'}
    hdr_str = '  '.join([f"{headers.get(c, c):>10}" for c in display_cols])
    print(f"  {hdr_str}")
    print(f"  {'-'*(len(display_cols)*13 - 2)}")

    for _, r in df_out.head(20).iterrows():
        vals = []
        for c in display_cols:
            v = r[c]
            if c == 'bwave_score':
                vals.append(f"{v:>10.0f}")
            elif c == 'signal_type':
                label = '启动' if v == 'launch' else '底背离' if v == 'divergence' else str(v)
                vals.append(f"{label:>10}")
            elif c == 'signal_tags':
                vals.append(f"{str(v):>10}")
            elif c in ('a_gain', 'b_drop', 'return_5d', 'return_10d', 'launch_pct_chg', 'b_vol_shrink', 'launch_dist_to_a_high'):
                vals.append(f"{v:>9.1f}%")
            elif c in ('a_duration', 'b_duration'):
                vals.append(f"{v:>10}")
            else:
                vals.append(f"{v:>10}")
        print(f"  {'  '.join(vals)}")

    print(f"\n  统计:")
    for w in [5, 10]:
        r = df_out.drop_duplicates(subset=['ts_code'])[f'return_{w}d'].dropna()
        wins = r[r > 0]
        if len(r) > 0:
            print(f"    +{w}d: 均={r.mean():>6.2f}%  胜率={len(wins)/len(r)*100:>4.0f}%  亏>15%={(r<-15).sum()}")


if __name__ == '__main__':
    main()
