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
