import pandas as pd
import numpy as np


def calculate_ma(klines, period=5):
    if not klines or len(klines) < period:
        return None
    
    df = pd.DataFrame(klines)
    if 'close' not in df.columns:
        return None
    
    ma = df['close'].rolling(window=period).mean().iloc[-1]
    return ma


def calculate_ma_list(klines, period=5):
    if not klines or len(klines) < period:
        return []
    
    df = pd.DataFrame(klines)
    if 'close' not in df.columns:
        return []
    
    ma_list = df['close'].rolling(window=period).mean().tolist()
    return ma_list


def check_concept_movement(klines_dict, days=3):
    if not klines_dict:
        return False
    
    up_count = 0
    total_count = 0
    
    for code, klines in klines_dict.items():
        if klines and len(klines) >= days + 1:
            df = pd.DataFrame(klines)
            recent_changes = df['close'].pct_change().tail(days)
            if (recent_changes > 0).sum() >= days - 1:
                up_count += 1
            total_count += 1
    
    if total_count == 0:
        return False
    
    return (up_count / total_count) >= 0.6


def calculate_change_percent(quote):
    if not quote or 'price' not in quote or 'last_close' not in quote:
        return 0
    
    price = quote['price']
    last_close = quote['last_close']
    if last_close == 0:
        return 0
    
    return (price - last_close) / last_close * 100


def analyze_concept_intraday(quotes, concept_name):
    if not quotes:
        return None
    
    stock_info = []
    for quote in quotes:
        change_pct = calculate_change_percent(quote)
        stock_info.append({
            'code': quote.get('code', ''),
            'name': quote.get('name', ''),
            'change_pct': change_pct,
            'price': quote.get('price', 0),
            'volume': quote.get('vol', 0)
        })
    
    df = pd.DataFrame(stock_info)
    
    if len(df) == 0:
        return None
    
    avg_change = df['change_pct'].mean()
    up_count = len(df[df['change_pct'] > 0])
    limit_up_count = len(df[df['change_pct'] >= 9.9])
    strong_stock_count = len(df[df['change_pct'] >= 5])
    total_count = len(df)
    
    df_sorted = df.sort_values('change_pct', ascending=False)
    leader = df_sorted.iloc[0].to_dict() if len(df_sorted) > 0 else None
    
    return {
        'concept_name': concept_name,
        'avg_change': avg_change,
        'up_ratio': up_count / total_count if total_count > 0 else 0,
        'limit_up_count': limit_up_count,
        'strong_stock_count': strong_stock_count,
        'total_count': total_count,
        'leader': leader,
        'top_5': df_sorted.head(5).to_dict('records')
    }


def detect_concept_breakout(analysis, prev_analysis=None):
    if not analysis:
        return False, None
    
    signals = []
    
    if analysis['avg_change'] >= 3:
        signals.append(f"板块平均涨幅 {analysis['avg_change']:.2f}%")
    
    if analysis['limit_up_count'] >= 3:
        signals.append(f"涨停家数 {analysis['limit_up_count']}")
    
    if analysis['up_ratio'] >= 0.7:
        signals.append(f"上涨比例 {analysis['up_ratio']*100:.0f}%")
    
    if analysis['strong_stock_count'] >= 5:
        signals.append(f"5%+ 家数 {analysis['strong_stock_count']}")
    
    is_breakout = len(signals) >= 2
    return is_breakout, signals


def detect_morning_star(klines):
    if not klines or len(klines) < 3:
        return False, None
    
    df = pd.DataFrame(klines)
    if 'open' not in df.columns or 'close' not in df.columns or 'high' not in df.columns or 'low' not in df.columns:
        return False, None
    
    df = df.tail(3)
    if len(df) < 3:
        return False, None
    
    day1 = df.iloc[0]
    day2 = df.iloc[1]
    day3 = df.iloc[2]
    
    day1_is_red = day1['close'] > day1['open']
    day2_is_red = day2['close'] > day2['open']
    day3_is_red = day3['close'] > day3['open']
    
    if day1_is_red and not day2_is_red and not day3_is_red:
        if day3['close'] <= day2['low'] and day3['open'] >= day2['close']:
            if day2['high'] - day2['low'] >= 2 * (day1['high'] - day1['low']):
                return True, {
                    'type': 'morning_star',
                    'day1_range': day1['high'] - day1['low'],
                    'day2_range': day2['high'] - day2['low'],
                    'day3_range': day3['high'] - day3['low']
                }
    
    return False, None


def detect_bullish_engulfing(klines):
    if not klines or len(klines) < 2:
        return False, None
    
    df = pd.DataFrame(klines)
    if 'open' not in df.columns or 'close' not in df.columns:
        return False, None
    
    df = df.tail(2)
    if len(df) < 2:
        return False, None
    
    prev_day = df.iloc[0]
    curr_day = df.iloc[1]
    
    prev_is_green = prev_day['close'] < prev_day['open']
    curr_is_red = curr_day['close'] > curr_day['open']
    
    if prev_is_green and curr_is_red:
        prev_body = abs(prev_day['close'] - prev_day['open'])
        curr_body = curr_day['close'] - curr_day['open']
        
        if curr_day['open'] <= prev_day['close'] and curr_day['close'] >= prev_day['open']:
            if curr_body >= prev_body * 1.0:
                return True, {
                    'type': 'bullish_engulfing',
                    'prev_open': prev_day['open'],
                    'prev_close': prev_day['close'],
                    'curr_open': curr_day['open'],
                    'curr_close': curr_day['close'],
                    'prev_body': prev_body,
                    'curr_body': curr_body
                }
    
    return False, None


def detect_new_high(klines, days=60):
    if not klines or len(klines) < days:
        return False, None
    
    df = pd.DataFrame(klines)
    if 'high' not in df.columns:
        return False, None
    
    recent_high = df['high'].tail(days)
    latest_high = df['high'].iloc[-1]
    
    if latest_high >= recent_high.max():
        return True, {
            'type': 'new_high',
            'high': latest_high,
            'period': days,
            'max_high_in_period': recent_high.max()
        }
    
    return False, None
