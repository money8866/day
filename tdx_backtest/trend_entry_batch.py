"""
趋势精准入场策略 - 分批回测脚本
"""
import pandas as pd
import numpy as np
import os
import sys
import logging
from datetime import datetime, timedelta

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('trend_entry_batch.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# 添加父目录到路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from data_loader import load_kline
from indicators import add_indicators, MA, EMA, RSI, MACD, KDJ, BOLL, OBV, CROSS, golden_cross, death_cross

def find_trend_entry_signals(df, hold_days=5):
    """寻找趋势精准入场信号"""
    if df is None or len(df) < 60:
        return []
    
    signals = []
    df = df.sort_values('trade_date').reset_index(drop=True)
    
    # 重命名列
    if 'trade_date' in df.columns:
        df = df.rename(columns={'trade_date': 'date'})
    
    # 计算技术指标（add_indicators使用'vol'列）
    df = add_indicators(df)
    
    # 确保必要的列存在
    if 'ma5' not in df.columns:
        df['ma5'] = MA(df['close'], 5)
    if 'ma20' not in df.columns:
        df['ma20'] = MA(df['close'], 20)
    if 'rsi6' not in df.columns:
        df['rsi6'] = RSI(df['close'], 6)
    
    # 趋势条件
    ma5 = df['ma5'].values
    ma20 = df['ma20'].values
    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    vol = df['vol'].values
    rsi6 = df.get('rsi6', np.full(len(df), 50))
    if rsi6 is None:
        rsi6 = np.full(len(df), 50)
    
    for i in range(20, len(df) - hold_days):
        signal_date = df.iloc[i]['date']
        current_close = close[i]
        
        # 趋势条件：MA5 > MA20（均线多头）
        trend_up = ma5[i] > ma20[i] if not np.isnan(ma5[i]) and not np.isnan(ma20[i]) else False
        
        # RSI条件
        current_rsi = rsi6[i] if not np.isnan(rsi6[i]) else 50
        
        # 成交量条件
        vol_ratio = vol[i] / np.mean(vol[max(0, i-20):i]) if np.mean(vol[max(0, i-20):i]) > 0 else 1
        
        # 买入条件：趋势确认 + RSI在合理区间 + 放量
        if trend_up and current_rsi >= 40 and current_rsi <= 75 and vol_ratio >= 1.0:
            # 计算持有hold_days天的收益
            future_returns = []
            for d in range(1, hold_days + 1):
                if i + d < len(df):
                    ret = (close[i + d] - close[i]) / close[i] * 100
                    future_returns.append(ret)
            
            if len(future_returns) == hold_days:
                avg_return = np.mean(future_returns)
                max_return = max(future_returns)
                min_return = min(future_returns)
                
                signals.append({
                    'date': signal_date,
                    'close': current_close,
                    'rsi6': current_rsi,
                    'vol_ratio': vol_ratio,
                    'hold_return': avg_return,
                    'max_return': max_return,
                    'min_return': min_return,
                    'win': avg_return > 0
                })
    
    return signals

def run_batch_backtest(batch_file, start_date='20250101', hold_days=5, batch_num=1):
    """运行单批回测"""
    logger.info(f"=== 开始批次 {batch_num} 回测 ===")
    
    # 读取股票列表
    df_stocks = pd.read_csv(batch_file, encoding='utf-8-sig')
    stocks = df_stocks['ts_code'].tolist()
    logger.info(f"批次 {batch_num}: {len(stocks)} 只股票")
    
    all_signals = []
    trade_count = 0
    
    for idx, ts_code in enumerate(stocks):
        try:
            # 加载数据
            df = load_kline(ts_code, start_date=start_date)
            
            if df is None or len(df) < 60:
                continue
            
            # 寻找信号
            signals = find_trend_entry_signals(df, hold_days=hold_days)
            
            for sig in signals:
                sig['ts_code'] = ts_code
                all_signals.append(sig)
                trade_count += 1
            
            if (idx + 1) % 20 == 0:
                logger.info(f"批次 {batch_num}: 已处理 {idx + 1}/{len(stocks)} 只股票，{trade_count} 个信号")
                
        except Exception as e:
            logger.warning(f"处理 {ts_code} 失败: {e}")
            continue
    
    # 统计结果
    if len(all_signals) > 0:
        df_result = pd.DataFrame(all_signals)
        
        # 计算胜率
        total_trades = len(df_result)
        win_trades = df_result['win'].sum()
        win_rate = win_trades / total_trades * 100 if total_trades > 0 else 0
        
        # 计算平均收益
        avg_return = df_result['hold_return'].mean()
        avg_win = df_result[df_result['win']]['hold_return'].mean() if win_trades > 0 else 0
        avg_loss = df_result[~df_result['win']]['hold_return'].mean() if (total_trades - win_trades) > 0 else 0
        profit_loss_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 0
        
        logger.info(f"=== 批次 {batch_num} 结果 ===")
        logger.info(f"总交易数: {total_trades}")
        logger.info(f"胜率: {win_rate:.1f}%")
        logger.info(f"平均收益: {avg_return:.2f}%")
        logger.info(f"平均盈利: {avg_win:.2f}%")
        logger.info(f"平均亏损: {avg_loss:.2f}%")
        logger.info(f"盈亏比: {profit_loss_ratio:.2f}")
        
        # 保存结果
        output_file = f'trend_entry_trades_batch_{batch_num}.csv'
        df_result.to_csv(output_file, index=False, encoding='utf-8-sig')
        logger.info(f"结果已保存到 {output_file}")
        
        return df_result
    else:
        logger.info(f"批次 {batch_num}: 未发现任何信号")
        return None

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='趋势精准入场策略分批回测')
    parser.add_argument('--batch', type=int, default=1, help='批次号')
    parser.add_argument('--hold', type=int, default=5, help='持有天数')
    parser.add_argument('--start', type=str, default='20250101', help='开始日期')
    args = parser.parse_args()
    
    batch_file = f'high_mv_stocks_batch_{args.batch}.csv'
    if os.path.exists(batch_file):
        run_batch_backtest(batch_file, args.start, args.hold, args.batch)
    else:
        logger.error(f"批次文件不存在: {batch_file}")
