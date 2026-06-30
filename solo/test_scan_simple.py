"""
测试简化版扫描 - 绕过可能的主循环bug
"""
import sys, os, sqlite3
import numpy as np
import pandas as pd
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DB = r'D:\mystock\cache_daily\stock_data.db'
OUTPUT_DIR = r'D:\mystock\solo\trend_feature_output'
os.makedirs(OUTPUT_DIR, exist_ok=True)

def get_data(ts_code: str) -> pd.DataFrame | None:
    conn = sqlite3.connect(DB)
    try:
        sql = """SELECT trade_date, open, high, low, close, pct_chg, vol, volume_ratio,
                        ma_bfq_5, ma_bfq_10, ma_bfq_20, ma_bfq_60,
                        rsi_bfq_6, rsi_bfq_12, macd_bfq_dif, macd_bfq_dea
                 FROM stk_factor_pro 
                 WHERE ts_code = ? 
                 ORDER BY trade_date"""
        df = pd.read_sql_query(sql, conn, params=(ts_code,))
        conn.close()
        if len(df) < 60:
            return None
        # 计算衍生指标
        df['return_1d'] = df['close'].pct_change(1) * 100
        df['above_ma20_pct'] = (df['close'] - df['ma_bfq_20']) / df['ma_bfq_20'] * 100
        df['above_ma60_pct'] = (df['close'] - df['ma_bfq_60']) / df['ma_bfq_60'] * 100
        return df
    except Exception as e:
        conn.close()
        return None

def check_signal_simple(df, idx, min_score=50, filter_return1d=False, filter_rsi=None, filter_macd_golden=False):
    """简化版信号检测 - 直接复制原版逻辑但独立实现"""
    if idx < 60:  # 需要足够历史数据
        return None
    
    row = df.iloc[idx]
    close = row['close']
    pct_chg = row['pct_chg']
    
    # 基本过滤
    if filter_return1d and row.get('return_1d', 0) <= 0:
        return None
    
    if filter_rsi and (row.get('rsi_bfq_6', 50) < filter_rsi[0] or row.get('rsi_bfq_6', 50) > filter_rsi[1]):
        return None
    
    # 计算评分 (使用优化后的v2逻辑)
    above_ma20 = row.get('above_ma20_pct', 0)
    above_ma60 = row.get('above_ma60_pct', 0)
    vol_ratio = row.get('volume_ratio', 1)
    dif = row.get('macd_bfq_dif', 0)
    dea = row.get('macd_bfq_dea', 0)
    rsi6 = row.get('rsi_bfq_6', 50)
    
    score = 0
    # MA20 (35分)
    if 12 <= above_ma20 <= 16:
        score += 35
    elif 10 <= above_ma20 < 12:
        score += 30
    elif 16 < above_ma20 <= 18:
        score += 28
    elif 8 <= above_ma20 < 10:
        score += 20
    elif 18 < above_ma20 <= 20:
        score += 15
    else:
        score += 5
    
    # 量比 (20分)
    if 1.2 <= vol_ratio <= 2.0:
        score += 20
    elif 1.0 <= vol_ratio < 1.2:
        score += 15
    elif 2.0 < vol_ratio <= 3.0:
        score += 10
    elif vol_ratio > 3.0:
        score += 0
    else:
        score += 5
    
    # MA60 (15分)
    if 15 <= above_ma60 <= 25:
        score += 15
    elif 10 <= above_ma60 < 15:
        score += 12
    elif 25 < above_ma60 <= 30:
        score += 8
    elif 5 <= above_ma60 < 10:
        score += 8
    else:
        score += 3
    
    # MACD (15分)
    if dif > 0 and dea > 0 and dif > dea:
        score += 15
    elif dif > 0 and dif > dea:
        score += 12
    elif dif > 0 and abs(dif - dea) < 0.1:
        score += 8
    elif dif > -0.5:
        score += 5
    
    # RSI (10分)
    if 45 <= rsi6 <= 65:
        score += 10
    elif 40 <= rsi6 < 45 or 65 < rsi6 <= 70:
        score += 7
    elif 35 <= rsi6 < 40 or 70 < rsi6 <= 75:
        score += 4
    
    # 确保评分在0-100
    score = max(0, min(100, score))
    
    if score < min_score:
        return None
    
    return {
        'ts_code': row['ts_code'] if 'ts_code' in df.columns else 'unknown',
        'signal_date': str(row['trade_date']),
        'signal_close': round(close, 2),
        'entry_score': score,
        'pct_chg': round(pct_chg, 2),
        'vol_ratio': round(vol_ratio, 2),
        'above_ma20_pct': round(above_ma20, 2),
        'above_ma60_pct': round(above_ma60, 2),
        'rsi6': round(rsi6, 2),
        'macd_dif': round(dif, 2)
    }

def main():
    print('=' * 60)
    print('简化版扫描测试')
    print('=' * 60)
    print()
    
    # 测试股票池 (已知有数据的)
    test_codes = ['600460.SH', '603256.SH', '688525.SH', '688002.SH', '688498.SH']
    
    print(f'测试股票数: {len(test_codes)}')
    print('过滤条件: return_1d > 0')
    print()
    
    all_signals = []
    
    for code in test_codes:
        df = get_data(code)
        if df is None:
            print(f'{code}: ❌ 无数据')
            continue
        
        print(f'{code}: ✅ {len(df)}天数据，检查信号...')
        
        # 检查最后30天
        start_idx = max(0, len(df) - 30)
        for idx in range(start_idx, len(df)):
            sig = check_signal_simple(df, idx, min_score=50, filter_return1d=True, filter_rsi=[40, 75])
            if sig:
                sig['ts_code'] = code
                all_signals.append(sig)
                print(f'  ✅ 发现信号: {sig["signal_date"]} 评分={sig["entry_score"]}')
    
    print()
    print('=' * 60)
    print(f'总信号数: {len(all_signals)}')
    print('=' * 60)
    
    if len(all_signals) > 0:
        # 保存CSV
        df_signals = pd.DataFrame(all_signals)
        csv_path = os.path.join(OUTPUT_DIR, f'test_scan_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv')
        df_signals.to_csv(csv_path, index=False, encoding='utf-8-sig')
        print(f'CSV已保存: {csv_path}')
        
        # 显示前5个信号
        print()
        print('前5个信号:')
        for i, sig in enumerate(all_signals[:5]):
            print(f'{i+1}. {sig["signal_date"]} {sig["ts_code"]:12} 评分={sig["entry_score"]} RSI={sig.get("rsi6", 0):5.1f}')
    else:
        print('❌ 未发现任何信号')
    
    print()
    print('=' * 60)

if __name__ == '__main__':
    main()
