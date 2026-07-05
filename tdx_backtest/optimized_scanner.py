# -*- coding: utf-8 -*-
"""
趋势精准入场策略 - 精选版
目标：每天1-5只信号
优化方向：加入多维度评分，精选高质量信号
"""
import pandas as pd
import numpy as np
import os
import sys
import logging
from datetime import datetime

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('optimized_scanner.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from data_loader import load_kline
from indicators import add_indicators, MA, EMA, RSI, MACD, KDJ

def calculate_trend_strength(df, i):
    """计算趋势强度 (0-100)"""
    close = df['close'].values
    ma5 = df['ma5'].values if 'ma5' in df.columns else MA(df['close'], 5).values
    ma20 = df['ma20'].values if 'ma20' in df.columns else MA(df['close'], 20).values
    
    # 均线多头强度
    ma_diff = (ma5[i] - ma20[i]) / ma20[i] * 100 if ma20[i] > 0 else 0
    
    # 趋势强度评分
    if ma_diff > 5:
        return min(100, 60 + ma_diff * 4)  # 强趋势
    elif ma_diff > 2:
        return 40 + ma_diff * 8  # 中等趋势
    else:
        return max(0, ma_diff * 10)  # 弱趋势

def calculate_momentum_score(df, i, lookback=20):
    """计算动量评分 (0-100)"""
    close = df['close'].values
    if i < lookback:
        return 50
    
    # 近期涨幅
    recent_return = (close[i] - close[i-lookback]) / close[i-lookback] * 100
    
    # 动量评分
    if recent_return > 15:
        return 80 + min(20, (recent_return - 15) * 2)
    elif recent_return > 8:
        return 60 + (recent_return - 8) * 2.5
    elif recent_return > 3:
        return 40 + (recent_return - 3) * 5
    else:
        return max(0, 20 + recent_return * 3)

def scan_stock(ts_code, start_date='20250101', hold_days=5):
    """扫描单只股票，返回买入信号"""
    try:
        df = load_kline(ts_code, start_date=start_date)
        if df is None or len(df) < 60:
            return None
        
        df = df.sort_values('trade_date').reset_index(drop=True)
        df = add_indicators(df)
        
        # 确保必要指标
        if 'ma5' not in df.columns:
            df['ma5'] = MA(df['close'], 5)
        if 'ma20' not in df.columns:
            df['ma20'] = MA(df['close'], 20)
        if 'rsi6' not in df.columns:
            df['rsi6'] = RSI(df['close'], 6)
        
        signals = []
        
        for i in range(20, len(df) - hold_days):
            # 基础条件：趋势确认
            ma5 = df['ma5'].values
            ma20 = df['ma20'].values
            close = df['close'].values
            vol = df['vol'].values
            rsi6 = df['rsi6'].values
            
            # 1. 趋势条件：MA5 > MA20
            if ma5[i] <= ma20[i]:
                continue
            
            # 2. RSI条件：40-75
            current_rsi = rsi6[i]
            if current_rsi < 40 or current_rsi > 75:
                continue
            
            # 3. 成交量条件：放量1.5-3倍
            avg_vol_20 = np.mean(vol[max(0, i-20):i])
            vol_ratio = vol[i] / avg_vol_20 if avg_vol_20 > 0 else 1
            if vol_ratio < 1.5 or vol_ratio > 4.0:
                continue
            
            # 4. MACD金叉条件（可选加强）
            if 'DIF' in df.columns and 'DEA' in df.columns:
                dif = df['DIF'].values
                dea = df['DEA'].values
                # MACD在0轴上方金叉
                if dif[i] <= dea[i] or dif[i] < 0:
                    continue
            
            # 计算综合评分
            signal_date = df.iloc[i]['trade_date']
            current_close = close[i]
            
            # 趋势强度
            trend_score = calculate_trend_strength(df, i)
            
            # 动量评分
            momentum_score = calculate_momentum_score(df, i)
            
            # RSI健康度 (50-70最佳)
            if 50 <= current_rsi <= 70:
                rsi_score = 100
            elif current_rsi < 50:
                rsi_score = 60 + current_rsi
            else:
                rsi_score = 80 - (current_rsi - 70) * 2
            
            # 量比健康度
            if 1.5 <= vol_ratio <= 2.5:
                vol_score = 100
            elif vol_ratio < 1.5:
                vol_score = 60 + vol_ratio * 20
            else:
                vol_score = max(0, 80 - (vol_ratio - 2.5) * 15)
            
            # 综合评分 (满分100)
            total_score = (
                trend_score * 0.25 +      # 趋势强度 25%
                momentum_score * 0.30 +    # 动量 30%
                rsi_score * 0.25 +         # RSI健康度 25%
                vol_score * 0.20           # 量比健康度 20%
            )
            
            # 计算持有收益
            future_returns = []
            for d in range(1, hold_days + 1):
                if i + d < len(df):
                    ret = (close[i + d] - close[i]) / close[i] * 100
                    future_returns.append(ret)
            
            if len(future_returns) == hold_days:
                avg_return = np.mean(future_returns)
                max_return = max(future_returns)
                
                signals.append({
                    'date': signal_date,
                    'close': current_close,
                    'rsi6': current_rsi,
                    'vol_ratio': vol_ratio,
                    'trend_score': trend_score,
                    'momentum_score': momentum_score,
                    'rsi_score': rsi_score,
                    'vol_score': vol_score,
                    'total_score': total_score,
                    'hold_return': avg_return,
                    'max_return': max_return,
                    'win': avg_return > 0
                })
        
        return signals
        
    except Exception as e:
        logger.warning(f"扫描 {ts_code} 失败: {e}")
        return None

def run_scan(stocks_file='high_mv_stocks.csv', start_date='20250101', hold_days=5, min_score=70, max_signals=5):
    """运行精选扫描"""
    logger.info("=" * 60)
    logger.info("趋势精准入场策略 - 精选版")
    logger.info("=" * 60)
    
    # 读取股票池
    df_stocks = pd.read_csv(stocks_file, encoding='utf-8-sig')
    stocks = df_stocks['ts_code'].tolist()
    logger.info(f"股票池: {len(stocks)} 只")
    logger.info(f"最低评分阈值: {min_score}")
    logger.info(f"每日最大信号数: {max_signals}")
    
    all_signals = []
    
    for idx, ts_code in enumerate(stocks):
        signals = scan_stock(ts_code, start_date, hold_days)
        if signals:
            for sig in signals:
                sig['ts_code'] = ts_code
                all_signals.append(sig)
        
        if (idx + 1) % 100 == 0:
            logger.info(f"已扫描 {idx + 1}/{len(stocks)} 只股票, {len(all_signals)} 个候选信号")
    
    if not all_signals:
        logger.info("未发现符合条件的信号")
        return None
    
    df_all = pd.DataFrame(all_signals)
    
    # 按日期分组，每天只保留评分最高的max_signals只
    df_all = df_all.sort_values(['date', 'total_score'], ascending=[False, False])
    
    selected_signals = []
    for date in df_all['date'].unique():
        day_signals = df_all[df_all['date'] == date]
        day_signals = day_signals[day_signals['total_score'] >= min_score]
        top_signals = day_signals.nlargest(max_signals, 'total_score')
        selected_signals.append(top_signals)
    
    df_selected = pd.concat(selected_signals, ignore_index=True)
    
    # 按日期统计
    logger.info("")
    logger.info("=" * 60)
    logger.info("每日信号统计")
    logger.info("=" * 60)
    
    for date in df_selected['date'].unique():
        day_signals = df_selected[df_selected['date'] == date]
        wins = day_signals['win'].sum()
        total = len(day_signals)
        avg_ret = day_signals['hold_return'].mean()
        wr = wins / total * 100 if total > 0 else 0
        logger.info(f"{date}: {total}只, 胜率{wr:.0f}%, 均收益{avg_ret:.2f}%")
    
    # 总体统计
    total = len(df_selected)
    wins = df_selected['win'].sum()
    wr = wins / total * 100 if total > 0 else 0
    avg_ret = df_selected['hold_return'].mean()
    
    logger.info("")
    logger.info("=" * 60)
    logger.info("总体统计")
    logger.info("=" * 60)
    logger.info(f"总信号数: {total}")
    logger.info(f"胜率: {wr:.1f}%")
    logger.info(f"均收益: {avg_ret:.2f}%")
    
    # 保存结果
    output_file = 'optimized_signals.csv'
    df_selected.to_csv(output_file, index=False, encoding='utf-8-sig')
    logger.info(f"结果已保存到 {output_file}")
    
    return df_selected

if __name__ == '__main__':
    # 运行精选扫描
    result = run_scan(
        stocks_file='high_mv_stocks.csv',
        start_date='20250101',
        hold_days=5,
        min_score=70,
        max_signals=5
    )
