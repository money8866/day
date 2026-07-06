# -*- coding: utf-8 -*-
"""
趋势精准入场策略 - 最终优化版
目标: 60%+胜率，每天1-3只
核心条件: RSI低位 + 温和放量 + 市场强势期
"""
import pandas as pd
import numpy as np
from data_loader import load_kline
from indicators import MA, RSI

def scan_stock_final(ts_code, start_date='20260501'):
    """最终版扫描"""
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
            date_str = str(date)
            
            close_val = df.iloc[i]['close']
            vol = df.iloc[i]['vol']
            ma5 = df.iloc[i]['ma5']
            ma20 = df.iloc[i]['ma20']
            rsi6 = df.iloc[i]['rsi6']
            
            # 条件1: 趋势确认
            if ma5 <= ma20:
                continue
            
            # 条件2: RSI低位 (40-52最佳)
            if rsi6 < 40 or rsi6 > 52:
                continue
            
            # 条件3: 量比适中 (1.5-2.5)
            avg_vol = np.mean(df['vol'].iloc[max(0, i-20):i])
            vol_ratio = vol / avg_vol if avg_vol > 0 else 1
            if vol_ratio < 1.5 or vol_ratio > 2.5:
                continue
            
            # 20日动量
            if i >= 20:
                mom_20 = (close_val - df.iloc[i-20]['close']) / df.iloc[i-20]['close'] * 100
            else:
                mom_20 = 0
            
            # 条件4: 动量适中 (5-25%)
            if mom_20 < 5 or mom_20 > 25:
                continue
            
            # 评分
            ma_diff = (ma5 - ma20) / ma20 * 100 if ma20 > 0 else 0
            
            # RSI低位加分 (越低越高)
            rsi_score = 100 - (rsi6 - 40) * 2  # 40分=100, 52分=76
            
            # 量比评分
            if 1.8 <= vol_ratio <= 2.2:
                vol_score = 100
            else:
                vol_score = 90
            
            # 动量评分
            if 10 <= mom_20 <= 20:
                momentum_score = 100
            else:
                momentum_score = 80
            
            # 趋势评分
            trend_score = min(100, 50 + ma_diff * 5)
            
            total_score = trend_score * 0.25 + momentum_score * 0.30 + rsi_score * 0.25 + vol_score * 0.20
            
            signals.append({
                'ts_code': ts_code,
                'date': date_str,
                'close': close_val,
                'rsi6': rsi6,
                'vol_ratio': vol_ratio,
                'momentum': mom_20,
                'ma_diff': ma_diff,
                'total_score': total_score
            })
        
        return signals
    except:
        return []

def run_final_scan():
    """运行最终扫描"""
    print('=' * 70)
    print('趋势精准入场策略 - 最终优化版')
    print('60%+胜率目标 | RSI低位40-52 | 量比1.5-2.5 | 动量5-25%')
    print('=' * 70)
    
    df_stocks = pd.read_csv('high_mv_stocks.csv', encoding='utf-8-sig')
    stocks = df_stocks['ts_code'].tolist()
    print('股票池: %d 只' % len(stocks))
    
    all_signals = []
    for idx, ts_code in enumerate(stocks):
        signals = scan_stock_final(ts_code)
        all_signals.extend(signals)
        
        if (idx + 1) % 500 == 0:
            print('已扫描 %d/%d 只' % (idx + 1, len(stocks)))
    
    if not all_signals:
        print('未发现任何信号')
        return
    
    df = pd.DataFrame(all_signals)
    df = df.sort_values(['date', 'total_score'], ascending=[False, False])
    
    # 每天只保留top3
    selected = []
    for date in df['date'].unique():
        day_df = df[df['date'] == date]
        top = day_df.nlargest(3, 'total_score')
        selected.append(top)
    
    df_selected = pd.concat(selected, ignore_index=True)
    
    # 按月统计
    df_selected['month'] = df_selected['date'].astype(str).str[:6]
    
    print('')
    print('=' * 70)
    print('月度胜率统计')
    print('=' * 70)
    
    for month in sorted(df_selected['month'].unique()):
        subset = df_selected[df_selected['month'] == month]
        print('%s: %d只信号' % (month, len(subset)))
    
    print('')
    print('=' * 70)
    print('最近5天信号')
    print('=' * 70)
    
    recent_dates = sorted(df_selected['date'].unique(), reverse=True)[:5]
    for date in recent_dates:
        day_df = df_selected[df_selected['date'] == date].sort_values('total_score', ascending=False)
        print('')
        print('日期: %s' % date)
        for _, row in day_df.iterrows():
            print('  %s  评分%.1f  RSI%.1f  量比%.2f  动量%.1f%%' % (
                row['ts_code'], row['total_score'], row['rsi6'], row['vol_ratio'], row['momentum']))
    
    df_selected.to_csv('final_signals.csv', index=False, encoding='utf-8-sig')
    print('')
    print('结果已保存到 final_signals.csv')

if __name__ == '__main__':
    run_final_scan()
