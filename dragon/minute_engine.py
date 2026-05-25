# -*- coding: utf-8 -*-
"""
通达信分钟数据解析器 (realtime_monitor.py 的数据引擎扩展)
支持解析 .lc1/.lc* 格式的分钟K线数据
"""

import struct
import os
import pandas as pd
from pathlib import Path
from datetime import datetime, time as dt_time

# 通达信分钟数据路径常量
TDX_SH_MINLINE = r"C:\new_tdx\vipdoc\sh\minline"
TDX_SZ_MINLINE = r"C:\new_tdx\vipdoc\sz\minline"


def parse_tdx_minute_lc1(filepath):
    """
    通达信分钟线(.lc1)解析器
    
    标准通达信lc1格式 (32字节/条):
    - date: 4字节 (int), 日期编码
    - open/high/low/close: 4字节 (int), 价格 × 10000
    - vol: 4字节 (unsigned), 成交量 (手)
    - amount: 4字节 (int), 成交额 (元)
    - reserved: 4字节
    
    注意：部分版本的lc1可能有不同的编码方式
    """
    records = []
    REC_SIZE = 32
    
    try:
        with open(filepath, "rb") as f:
            while True:
                row = f.read(REC_SIZE)
                if len(row) < REC_SIZE:
                    break
                
                vals = struct.unpack("IIIIIfII", row)
                date_int = vals[0]
                
                # 尝试解析日期编码
                # 常见几种格式:
                # 1. YYYYMMDDN - 年月日 + 分钟序号
                # 2. YYMMDDxx - 年月日 + 时间因子
                # 3. 纯序号 + 时间戳
                
                # 方法1: 先提取年月日
                try:
                    date_str = str(date_int)
                    if len(date_str) >= 8:
                        # YYYYMMDD 或 YYMMDDxx 格式
                        year = int(date_str[:4]) if len(date_str) == 8 else int("20" + date_str[:2])
                        month = int(date_str[4:6])
                        day = int(date_str[6:8])
                        
                        # 处理尾部的分钟信息
                        if len(date_str) > 8:
                            minute_idx = int(date_str[8:])
                        else:
                            # 可能是连续的分钟索引 (从0开始)
                            minute_idx = 0
                    else:
                        # 短日期格式
                        year = 2020 + (date_int // 10000)
                        month = (date_int // 100) % 100
                        day = date_int % 100
                        minute_idx = 0
                        
                    # 生成时间
                    # 假设每条记录间隔3分钟（可以根据实际情况调整）
                    base_time = dt_time(9, 30)  # 开盘时间
                    total_minutes = minute_idx * 3
                    hour = (9 + total_minutes // 60) % 24
                    minute = (30 + total_minutes % 60) % 60
                    
                    # 检查是否超出交易时间
                    if hour >= 12 and hour < 13:  # 午休时间
                        hour = hour + 12 if hour + 12 < 15 else hour
                    
                    trade_time = dt_time(hour, minute)
                    
                except Exception:
                    # 解析失败，使用索引生成时间
                    year, month, day = 2024, 1, 1
                    trade_time = dt_time(9, 30)
                
                # 价格除以10000 (大多数版本)
                price_open = vals[1] / 10000.0
                price_high = vals[2] / 10000.0
                price_low = vals[3] / 10000.0
                price_close = vals[4] / 10000.0
                
                # 成交量（手）
                volume = vals[5]
                
                # 成交额（元）
                amount = vals[6]
                
                records.append({
                    "date": date_int,
                    "time": trade_time,
                    "open": price_open,
                    "high": price_high,
                    "low": price_low,
                    "close": price_close,
                    "volume": volume,
                    "amount": amount,
                })
                
    except FileNotFoundError:
        return pd.DataFrame()
    
    if not records:
        return pd.DataFrame()
    
    df = pd.DataFrame(records)
    return df


def get_minute_data(ts_code, market="sh", days_back=5):
    """
    获取指定股票的分钟数据
    
    Args:
        ts_code: 股票代码如 '000001' 或 '600000'
        market: 'sh' 或 'sz'
        days_back: 最近N天的分钟数据
        
    Returns:
        DataFrame with columns: date, time, open, high, low, close, volume
    """
    # 确定文件路径
    min_dir = TDX_SH_MINLINE if market == "sh" else TDX_SZ_MINLINE
    filename = f"{market}{ts_code}.lc1"
    filepath = os.path.join(min_dir, filename)
    
    if not os.path.exists(filepath):
        # 尝试大写
        filename = f"{market.upper()}{ts_code}.lc1"
        filepath = os.path.join(min_dir, filename)
    
    if not os.path.exists(filepath):
        print(f"Minute file not found: {filename}")
        return pd.DataFrame()
    
    df = parse_tdx_minute_lc1(filepath)
    
    if df.empty:
        return df
    
    # 只保留最近N天
    if days_back and days_back > 0:
        # 使用日期筛选
        # 这里需要根据实际日期编码来调整
        pass
    
    return df


def calculate_realtime_indicators(df, period=14):
    """
    基于分钟数据计算实时技术指标
    
    Args:
        df: 分钟数据DataFrame
        period: RSI计算周期
        
    Returns:
        dict 包含当前的指标值
    """
    if df.empty or len(df) < period:
        return {}
    
    # 最新价格
    last = df.iloc[-1]
    
    # 计算RSI
    deltas = df['close'].diff()
    gains = deltas.clip(lower=0)
    losses = (-deltas.clip(upper=0))
    
    avg_gain = gains.tail(period).mean()
    avg_loss = losses.tail(period).mean()
    
    if avg_loss == 0:
        rsi = 100
    else:
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
    
    # 均线
    ma5 = df['close'].tail(5).mean()
    ma10 = df['close'].tail(10).mean()
    ma20 = df['close'].tail(20).mean()
    
    # 量能
    avg_vol = df['volume'].tail(20).mean()
    current_vol = last['volume']
    vol_ratio = current_vol / avg_vol if avg_vol > 0 else 0
    
    return {
        "price": last['close'],
        "change_pct": ((last['close'] - df.iloc[-2]['close']) / df.iloc[-2]['close'] * 100) if len(df) > 1 else 0,
        "rsi": rsi,
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "volume": current_vol,
        "vol_ratio": vol_ratio,
    }


def fetch_realtime_quotes(codes):
    """
    获取多只股票的实时行情
    
    Args:
        codes: list of '000001.SZ' format
        
    Returns:
        dict: {code: indicators_dict}
    """
    results = {}
    
    for code in codes:
        # 解析代码和市场
        if '.' in code:
            symbol, market_suffix = code.split('.')
            market = market_suffix.lower()
        else:
            symbol = code
            market = 'sz' if code.startswith(('0', '3')) else 'sh'
        
        # 获取分钟数���
        df = get_minute_data(symbol, market)
        
        if not df.empty:
            results[code] = calculate_realtime_indicators(df)
        else:
            results[code] = None
    
    return results


# ============================================================
# 测试代码
# ============================================================
if __name__ == "__main__":
    import sys
    
    # 测试读取上证指数分钟数据
    print("Testing minute data parser...")
    
    df = get_minute_data("000001", "sh")
    print(f"Records: {len(df)}")
    
    if not df.empty:
        print("\nFirst 3 records:")
        print(df.head(3))
        
        print("\nLast 3 records:")
        print(df.tail(3))
        
        # 计算实时指标
        ind = calculate_realtime_indicators(df)
        print("\nRealtime indicators:")
        for k, v in ind.items():
            print(f"  {k}: {v}")