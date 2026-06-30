"""
趋势精准入场 - 回调买入版 (v7)
核心策略：只买回调到均线的信号，不追高

关键改变：
1. MA20偏离度：5-15%（回调到均线附近，而非远离均线）
2. 评分门槛：≥70（避开65-79死区）
3. 信号日涨幅：2-15%（不强求涨停）
"""
import sys, os, sqlite3, argparse
from datetime import datetime
import pandas as pd
import numpy as np

DB = r'D:\mystock\cache_daily\stock_data.db'
OUTPUT_DIR = r'D:\mystock\solo\trend_feature_output'
os.makedirs(OUTPUT_DIR, exist_ok=True)

def get_data(ts_code):
    conn = sqlite3.connect(DB)
    try:
        sql = """SELECT trade_date, open, high, low, close, pct_chg, vol,
                        ma_bfq_5, ma_bfq_10, ma_bfq_20, ma_bfq_60,
                        rsi_bfq_6, rsi_bfq_12, macd_bfq_dif, macd_bfq_dea,
                        volume_ratio
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
    except:
        conn.close()
        return None

def check_signal_pullback(df, idx, min_score=70):
    """
    回调买入信号检测
    
    核心逻辑：
    - 信号日股价接近MA20（5-15%），而非远离均线
    - 评分≥70（避开65-79死区）
    - 量比适中（1.0-2.5）
    - 连续上涨≤2天（不追高）
    """
    if idx < 60:
        return None
    
    row = df.iloc[idx]
    close = row['close']
    pct_chg = row['pct_chg']
    vol_ratio = row.get('volume_ratio', 1)
    
    # ========== 核心过滤条件 ==========
    
    # 1. MA20偏离度: 5-15%（回调到均线附近买入）
    above_ma20 = row.get('above_ma20_pct', 0)
    if not (5 <= above_ma20 <= 15):
        return None
    
    # 2. 量比: 1.0-2.5（温和放量，不要太缩量也不要爆量）
    if not (1.0 <= vol_ratio <= 2.5):
        return None
    
    # 3. 涨幅: 2-15%（稳健上涨）
    if pct_chg < 2 or pct_chg > 15:
        return None
    
    # 4. 评分门槛: ≥70
    entry_score = 0
    
    # MA20位置评分 (30分)
    if 8 <= above_ma20 <= 12:
        entry_score += 30
    elif 5 <= above_ma20 < 8:
        entry_score += 25
    elif 12 < above_ma20 <= 15:
        entry_score += 25
    
    # 量比评分 (25分)
    if 1.2 <= vol_ratio <= 2.0:
        entry_score += 25
    elif 1.0 <= vol_ratio < 1.2:
        entry_score += 18
    elif 2.0 < vol_ratio <= 2.5:
        entry_score += 15
    
    # 涨幅评分 (15分)
    if 5 <= pct_chg <= 10:
        entry_score += 15
    elif 10 < pct_chg <= 15:
        entry_score += 12
    elif 2 <= pct_chg < 5:
        entry_score += 10
    
    # RSI6评分 (15分)
    rsi6 = row.get('rsi_bfq_6', 50)
    if 45 <= rsi6 <= 60:
        entry_score += 15
    elif 40 <= rsi6 < 45 or 60 < rsi6 <= 70:
        entry_score += 10
    
    # 均线健康度 (10分)
    ma5 = row.get('ma_bfq_5', 0)
    ma10 = row.get('ma_bfq_10', 0)
    ma20 = row.get('ma_bfq_20', 0)
    if ma5 > ma10 > ma20:
        entry_score += 10
    elif ma5 > ma20 and ma10 > ma20:
        entry_score += 5
    
    # MACD评分 (5分)
    dif = row.get('macd_bfq_dif', 0)
    dea = row.get('macd_bfq_dea', 0)
    if dif > dea:
        entry_score += 5
    
    # 连续上涨天数惩罚（追高惩罚）
    consecutive_up = 0
    for i in range(idx - 1, max(0, idx - 5), -1):
        if df.iloc[i].get('pct_chg', 0) > 0:
            consecutive_up += 1
        else:
            break
    if consecutive_up > 2:
        entry_score -= 10
    elif consecutive_up > 3:
        entry_score -= 20
    
    if entry_score < min_score:
        return None
    
    return {
        'signal_date': str(row['trade_date']),
        'signal_close': round(close, 2),
        'entry_score': entry_score,
        'pct_chg': round(pct_chg, 2),
        'vol_ratio': round(vol_ratio, 2),
        'above_ma20_pct': round(above_ma20, 2),
        'rsi_bfq_6': round(rsi6, 2),
        'consecutive_up': consecutive_up,
        'ma5_above_ma20': 1 if ma5 > ma20 else 0,
        'macd_golden': 1 if dif > dea else 0,
    }

def load_pool(pool_name):
    if pool_name == 'qualified':
        csv_path = r'D:\mystock\solo\multi_factor_picker\output\bull_stocks.csv'
        if os.path.exists(csv_path):
            df = pd.read_csv(csv_path, encoding='utf-8-sig')
            return df['ts_code'].tolist()
    
    # fallback
    conn = sqlite3.connect(DB)
    codes = pd.read_sql_query('SELECT DISTINCT ts_code FROM stk_factor_pro', conn)['ts_code'].tolist()
    conn.close()
    return codes

def main():
    parser = argparse.ArgumentParser(description='Trend Entry - Pullback Version')
    parser.add_argument('--pool', default='qualified', help='Stock pool')
    parser.add_argument('--today', action='store_true', help='Today only')
    parser.add_argument('--recent', type=int, default=0, help='Recent N days')
    parser.add_argument('--min-score', type=int, default=70, help='Min score (default 70)')
    args = parser.parse_args()
    
    print('=' * 70)
    print('Trend Entry - Pullback Version v7')
    print('=' * 70)
    print()
    print(f'Stock Pool: {args.pool}')
    print(f'Min Score: {args.min_score}')
    print()
    print('Core Filters:')
    print('  1. MA20 Offset: 5-15% (near MA20, not far)')
    print('  2. Volume Ratio: 1.0-2.5')
    print('  3. Return: 2-15%')
    print('  4. Score >= 70 (avoid 65-79 dead zone)')
    print()
    
    codes = load_pool(args.pool)
    print(f'Total Stocks: {len(codes)}')
    print()
    
    all_signals = []
    today = datetime.now().strftime('%Y%m%d')
    
    for i, code in enumerate(codes):
        df = get_data(code)
        if df is None:
            continue
        
        # 确定扫描范围
        if args.today:
            start_idx = len(df) - 1
        elif args.recent > 0:
            start_idx = max(0, len(df) - args.recent)
        else:
            start_idx = max(0, len(df) - 60)
        
        for idx in range(start_idx, len(df)):
            sig = check_signal_pullback(df, idx, min_score=args.min_score)
            if sig:
                sig['ts_code'] = code
                all_signals.append(sig)
        
        if (i + 1) % 100 == 0:
            print(f'[{i+1}/{len(codes)}] Found {len(all_signals)} signals')
    
    print()
    print('=' * 70)
    print(f'Complete: {len(all_signals)} signals')
    print('=' * 70)
    
    if len(all_signals) > 0:
        df_signals = pd.DataFrame(all_signals)
        
        # 只看今日
        if args.today:
            df_signals = df_signals[df_signals['signal_date'] == today]
        
        df_signals = df_signals.sort_values('entry_score', ascending=False)
        
        # 保存CSV
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        csv_path = os.path.join(OUTPUT_DIR, f'entry_precision_v7_{timestamp}_{args.pool}.csv')
        df_signals.to_csv(csv_path, index=False, encoding='utf-8-sig')
        
        print(f'CSV: {csv_path}')
        print()
        
        # 显示全部
        print('All Signals:')
        for _, row in df_signals.iterrows():
            print(f"  {row['ts_code']:12} {row['signal_date']} Score={row['entry_score']:3} "
                  f"Ret={row['pct_chg']:5.1f}% Vol={row['vol_ratio']:.2f} MA20={row['above_ma20_pct']:.1f}%")
        
        print()
        print(f'Total: {len(df_signals)}')
        if args.today:
            today_signals = df_signals[df_signals['signal_date'] == today]
            print(f'Today: {len(today_signals)}')
    else:
        print('No signals')

if __name__ == '__main__':
    main()
