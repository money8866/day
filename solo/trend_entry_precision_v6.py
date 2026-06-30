"""
趋势精准入场 - 强化版 (v6)
目的：大幅提高信号胜率，减少失败率

强化过滤条件：
1. MA20偏离度: 12-18%（不过高不过低）
2. 量比: 1.2-2.0（温和放量）
3. RSI6: 45-70（中等偏强，不过热）
4. 涨幅: 5-12%（稳健上涨）
5. 均线多头: MA5 > MA10 > MA20
6. 趋势确认: 连续上涨≤3天（避免追高）
"""
import sys, os, sqlite3, argparse
from datetime import datetime, timedelta
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

def check_signal_enhanced(df, idx, min_score=50):
    """强化版信号检测"""
    if idx < 60:
        return None
    
    row = df.iloc[idx]
    close = row['close']
    pct_chg = row['pct_chg']
    vol_ratio = row.get('volume_ratio', 1)
    
    # ========== 强化过滤条件 ==========
    
    # 1. MA20偏离度: 12-18%（最佳区间）
    above_ma20 = row.get('above_ma20_pct', 0)
    if not (12 <= above_ma20 <= 18):
        return None
    
    # 2. 量比: 1.2-2.0（温和放量）
    if not (1.2 <= vol_ratio <= 2.5):
        return None
    
    # 3. RSI6: 45-70（中等偏强）
    rsi6 = row.get('rsi_bfq_6', 50)
    if rsi6 < 45 or rsi6 > 70:
        return None
    
    # 4. 涨幅: 5-12%（稳健上涨，不过热）
    if pct_chg < 5 or pct_chg > 12:
        return None
    
    # 5. 均线多头排列
    ma5 = row.get('ma_bfq_5', 0)
    ma10 = row.get('ma_bfq_10', 0)
    ma20 = row.get('ma_bfq_20', 0)
    if not (ma5 > ma10 > ma20):
        return None
    
    # 6. 连续上涨天数（避免追高）
    consecutive_up = 0
    for i in range(idx - 1, max(0, idx - 5), -1):
        if df.iloc[i].get('pct_chg', 0) > 0:
            consecutive_up += 1
        else:
            break
    if consecutive_up > 3:  # 连续上涨超过3天，拒绝
        return None
    
    # 7. MACD金叉或在水上
    dif = row.get('macd_bfq_dif', 0)
    dea = row.get('macd_bfq_dea', 0)
    if dif < dea and dif < 0:  # 在水下且死叉，拒绝
        return None
    
    # ========== 评分 ==========
    entry_score = 0
    
    # MA20位置 (30分)
    if 14 <= above_ma20 <= 16:
        entry_score += 30
    elif 12 <= above_ma20 < 14:
        entry_score += 25
    elif 16 < above_ma20 <= 18:
        entry_score += 25
    
    # 量比 (25分)
    if 1.5 <= vol_ratio <= 2.0:
        entry_score += 25
    elif 1.2 <= vol_ratio < 1.5:
        entry_score += 20
    elif 2.0 < vol_ratio <= 2.5:
        entry_score += 15
    
    # RSI (15分)
    if 50 <= rsi6 <= 60:
        entry_score += 15
    elif 45 <= rsi6 < 50 or 60 < rsi6 <= 70:
        entry_score += 10
    
    # 涨幅 (10分)
    if 7 <= pct_chg <= 10:
        entry_score += 10
    elif 5 <= pct_chg < 7 or 10 < pct_chg <= 12:
        entry_score += 7
    
    # 均线健康度 (10分)
    if ma5 > ma10 > ma20:
        entry_score += 10
    
    # MACD (10分)
    if dif > dea and dif > 0:
        entry_score += 10
    elif dif > dea:
        entry_score += 5
    
    # 连续上涨天数惩罚
    if consecutive_up == 0:
        entry_score += 5
    elif consecutive_up == 1:
        entry_score += 3
    elif consecutive_up == 2:
        entry_score -= 5
    elif consecutive_up == 3:
        entry_score -= 10
    
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
        'ma5_above_ma10': 1 if ma5 > ma10 else 0,
        'ma10_above_ma20': 1 if ma10 > ma20 else 0,
        'macd_golden': 1 if dif > dea else 0,
    }

def load_pool(pool_name):
    if pool_name == 'qualified':
        csv_path = r'D:\mystock\solo\multi_factor_picker\output\bull_stocks.csv'
        if os.path.exists(csv_path):
            df = pd.read_csv(csv_path, encoding='utf-8-sig')
            return df['ts_code'].tolist()
    
    # fallback: 全部A股
    conn = sqlite3.connect(DB)
    codes = pd.read_sql_query('SELECT DISTINCT ts_code FROM stk_factor_pro', conn)['ts_code'].tolist()
    conn.close()
    return codes

def main():
    parser = argparse.ArgumentParser(description='趋势精准入场 - 强化版')
    parser.add_argument('--pool', default='qualified', help='股票池')
    parser.add_argument('--today', action='store_true', help='只看今日信号')
    parser.add_argument('--recent', type=int, default=0, help='最近N天')
    parser.add_argument('--min-score', type=int, default=50, help='最低评分')
    args = parser.parse_args()
    
    print('=' * 70)
    print('趋势精准入场 - 强化版 v6')
    print('=' * 70)
    print()
    print(f'股票池: {args.pool}')
    print(f'最低评分: {args.min_score}')
    print()
    print('强化过滤条件:')
    print('  1. MA20偏离度: 12-18%')
    print('  2. 量比: 1.2-2.5')
    print('  3. RSI6: 45-70')
    print('  4. 涨幅: 5-12%')
    print('  5. 均线多头: MA5 > MA10 > MA20')
    print('  6. 连续上涨≤3天')
    print('  7. MACD金叉或在水面以上')
    print()
    
    codes = load_pool(args.pool)
    print(f'股票总数: {len(codes)}')
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
            sig = check_signal_enhanced(df, idx, min_score=args.min_score)
            if sig:
                sig['ts_code'] = code
                all_signals.append(sig)
        
        if (i + 1) % 100 == 0:
            print(f'[{i+1}/{len(codes)}] 已发现{len(all_signals)}个信号')
    
    print()
    print('=' * 70)
    print(f'扫描完成，共 {len(all_signals)} 个信号')
    print('=' * 70)
    
    if len(all_signals) > 0:
        df_signals = pd.DataFrame(all_signals)
        
        # 只看今日
        if args.today:
            df_signals = df_signals[df_signals['signal_date'] == today]
        
        df_signals = df_signals.sort_values('entry_score', ascending=False)
        
        # 保存CSV
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        csv_path = os.path.join(OUTPUT_DIR, f'entry_precision_v6_{timestamp}_{args.pool}.csv')
        df_signals.to_csv(csv_path, index=False, encoding='utf-8-sig')
        
        print(f'CSV: {csv_path}')
        print()
        
        # 显示TOP20
        print('TOP20信号:')
        for i, row in df_signals.head(20).iterrows():
            print(f"  {row['ts_code']:12} {row['signal_date']} 评分={row['entry_score']:3} "
                  f"涨={row['pct_chg']:5.1f}% 量比={row['vol_ratio']:.2f} RSI={row['rsi_bfq_6']:.0f}")
        
        print()
        print(f'总信号数: {len(df_signals)}')
        if args.today:
            print(f'今日信号: {len(df_signals[df_signals["signal_date"] == today])}')
    else:
        print('无信号')

if __name__ == '__main__':
    main()
