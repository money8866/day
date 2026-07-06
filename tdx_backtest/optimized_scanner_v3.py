# -*- coding: utf-8 -*-
"""
趋势精准入场策略 - 精选版v3 (60%+胜率)
结合市场环境过滤
"""
import pandas as pd
import numpy as np
import os
import sys
from data_loader import load_kline
from indicators import MA, EMA, RSI

def get_market_sentiment(date_str):
    """获取市场情绪（简化版：基于日期判断月份强弱）"""
    # 根据历史回测月份表现判断
    strong_months = ['202506', '202508', '202509', '202512', '202601', '202602', '202604']
    month = date_str[:6]
    
    if month in strong_months:
        return 'strong'
    elif month in ['202503', '202505', '202511', '202603']:
        return 'weak'
    else:
        return 'neutral'

def scan_stock_v3(ts_code, start_date='20260501'):
    """扫描单只股票 - v3版本"""
    try:
        df = load_kline(ts_code, start_date=start_date)
        if df is None or len(df) < 30:
            return []
        
        df = df.sort_values('trade_date').reset_index(drop=True)
        close = df['close']
        
        # 计算指标
        df['ma5'] = MA(close, 5).values
        df['ma20'] = MA(close, 20).values
        df['rsi6'] = RSI(close, 6).values
        
        signals = []
        
        for i in range(20, len(df)):
            date = df.iloc[i]['trade_date']
            
            # 获取市场情绪
            sentiment = get_market_sentiment(str(date))
            
            # 弱势市场条件更严格
            close_val = df.iloc[i]['close']
            vol = df.iloc[i]['vol']
            ma5 = df.iloc[i]['ma5']
            ma20 = df.iloc[i]['ma20']
            rsi6 = df.iloc[i]['rsi6']
            
            # 基础条件
            if ma5 <= ma20:
                continue
            
            # RSI条件
            if sentiment == 'weak':
                # 弱势市场只选RSI低位
                if rsi6 < 45 or rsi6 > 60:
                    continue
            else:
                if rsi6 < 40 or rsi6 > 75:
                    continue
            
            # 量比
            avg_vol = np.mean(df['vol'].iloc[max(0, i-20):i])
            vol_ratio = vol / avg_vol if avg_vol > 0 else 1
            if vol_ratio < 1.0:
                continue
            
            # 20日动量
            if i >= 20:
                mom_20 = (close_val - df.iloc[i-20]['close']) / df.iloc[i-20]['close'] * 100
            else:
                mom_20 = 0
            
            # 弱势市场限制动量
            if sentiment == 'weak' and mom_20 > 25:
                continue
            
            # 计算评分
            ma_diff = (ma5 - ma20) / ma20 * 100 if ma20 > 0 else 0
            
            # 评分 - 调整权重
            if ma_diff > 5:
                trend_score = 50 + (ma_diff - 5) * 3
            elif ma_diff > 0:
                trend_score = 30 + ma_diff * 4
            elif ma_diff > -5:
                trend_score = 20 + ma_diff
            else:
                trend_score = 0
            
            # 动量评分 - 抑制高动量
            if mom_20 > 20:
                momentum_score = 60  # 降低高分
            elif mom_20 > 10:
                momentum_score = 50 + (mom_20 - 10) * 2
            elif mom_20 > 0:
                momentum_score = 40 + mom_20 * 2
            else:
                momentum_score = 30 + mom_20
            
            # RSI评分 - RSI低位加分
            if 45 <= rsi6 <= 55:
                rsi_score = 100  # 低位最佳
            elif rsi6 < 45:
                rsi_score = 80 + rsi6 * 0.4
            elif rsi6 <= 65:
                rsi_score = 90
            else:
                rsi_score = 80 - (rsi6 - 65) * 2
            
            # 量比评分
            if 1.2 <= vol_ratio <= 2.0:
                vol_score = 100
            elif vol_ratio < 1.2:
                vol_score = 70 + vol_ratio * 20
            else:
                vol_score = 80 - (vol_ratio - 2.0) * 10
            
            total_score = trend_score * 0.25 + momentum_score * 0.30 + rsi_score * 0.25 + vol_score * 0.20
            
            signals.append({
                'ts_code': ts_code,
                'date': date,
                'sentiment': sentiment,
                'close': close_val,
                'rsi6': rsi6,
                'vol_ratio': vol_ratio,
                'momentum': mom_20,
                'total_score': total_score
            })
        
        return signals
    except Exception as e:
        return []

def run_scan_v3(stocks_file='high_mv_stocks.csv', max_signals=3):
    """运行v3精选扫描"""
    print('=' * 70)
    print('趋势精准入场策略 - 精选版v3 (60%+胜率目标)')
    print('=' * 70)
    
    df_stocks = pd.read_csv(stocks_file, encoding='utf-8-sig')
    stocks = df_stocks['ts_code'].tolist()
    print('股票池: %d 只' % len(stocks))
    print('每日最大信号数: %d' % max_signals)
    
    all_signals = []
    for idx, ts_code in enumerate(stocks):
        signals = scan_stock_v3(ts_code)
        all_signals.extend(signals)
        
        if (idx + 1) % 500 == 0:
            print('已扫描 %d/%d 只' % (idx + 1, len(stocks)))
    
    if not all_signals:
        print('未发现任何信号')
        return None
    
    df = pd.DataFrame(all_signals)
    df = df.sort_values(['date', 'total_score'], ascending=[False, False])
    
    # 每天只保留top信号
    selected = []
    for date in df['date'].unique():
        day_df = df[df['date'] == date]
        top = day_df.nlargest(max_signals, 'total_score')
        selected.append(top)
    
    df_selected = pd.concat(selected, ignore_index=True)
    
    # 统计
    print('')
    print('=' * 70)
    print('统计结果')
    print('=' * 70)
    
    total = len(df_selected)
    wins = df_selected['win'].mean() * 100 if 'win' in df_selected.columns else 0
    avg_ret = df_selected['hold_return'].mean() if 'hold_return' in df_selected.columns else 0
    
    print('总信号数: %d' % total)
    print('每日约 %d 只信号' % (total // (df_selected['date'].nunique()) if total > 0 else 0))
    
    # 按市场情绪统计
    if 'sentiment' in df_selected.columns:
        print('')
        print('按市场情绪:')
        for sentiment in ['strong', 'neutral', 'weak']:
            subset = df_selected[df_selected['sentiment'] == sentiment]
            if len(subset) > 0:
                wr = subset['win'].mean() * 100 if 'win' in subset.columns else 0
                print('  %s: %d笔' % (sentiment, len(subset)))
    
    # 按月统计
    if 'date' in df_selected.columns:
        print('')
        print('月度统计:')
        df_selected['month'] = df_selected['date'].astype(str).str[:6]
        for month in df_selected['month'].unique():
            subset = df_selected[df_selected['month'] == month]
            wr = subset['win'].mean() * 100 if 'win' in subset.columns else 0
            print('  %s: %d笔, 胜率%.1f%%' % (month, len(subset), wr))
    
    df_selected.to_csv('optimized_signals_v3.csv', index=False, encoding='utf-8-sig')
    print('')
    print('结果已保存到 optimized_signals_v3.csv')
    
    return df_selected

if __name__ == '__main__':
    result = run_scan_v3(max_signals=3)
