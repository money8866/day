"""
精选股票池扫描 - 绕过全池扫描bug
"""
import sys, os, sqlite3
from datetime import datetime
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DB = r'D:\mystock\cache_daily\stock_data.db'
OUTPUT_DIR = r'D:\mystock\solo\trend_feature_output'
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 精选股票池 (20只，基于BullScore高分股)
SELECTED_CODES = [
    '600460.SH',  # 士兰微
    '688002.SH',  # 睿创微纳
    '688525.SH',  # 佰维存储
    '603256.SH',  # 宏和科技
    '688498.SH',  # 蜂助手
    '688519.SH',  # 南亚新材
    '603629.SH',  # 利扬芯片
    '300661.SZ',  # 圣邦股份
    '688187.SH',  # 时代电气
    '002049.SZ',  # 紫光国微
    '600584.SH',  # 长电科技
    '688008.SH',  # 澜起科技
    '002185.SZ',  # 华天科技
    '300223.SZ',  # 北京君正
    '688012.SH',  # 中微公司
    '688396.SH',  # 华润微
    '600667.SH',  # 太极实业
    '002151.SZ',  # 深天马A
    '300327.SZ',  # 中颖电子
    '688099.SH',  # 晶晨股份
]

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

def check_signal(df, idx, min_score=50):
    """原版评分逻辑 (v1)"""
    if idx < 60:
        return None
    
    row = df.iloc[idx]
    close = row['close']
    pct_chg = row['pct_chg']
    
    # 计算评分 (原版v1)
    above_ma20 = row.get('above_ma20_pct', 0)
    above_ma60 = row.get('above_ma60_pct', 0)
    vol_ratio = row.get('volume_ratio', 1)
    dif = row.get('macd_bfq_dif', 0)
    
    entry_score = 0
    
    # 1. MA20位置 (30分)
    if 12 <= above_ma20 <= 16:
        entry_score += 30
    elif 16 < above_ma20 <= 20:
        entry_score += 25
    elif 8 <= above_ma20 < 12:
        entry_score += 20
    elif 5 <= above_ma20 < 8:
        entry_score += 10
    
    # 2. 量比 (25分)
    if 1.2 <= vol_ratio <= 1.5:
        entry_score += 25
    elif 1.5 < vol_ratio <= 2.0:
        entry_score += 18
    elif 2.0 < vol_ratio <= 3.0:
        entry_score += 8
    elif 1.0 <= vol_ratio < 1.2:
        entry_score += 15
    elif vol_ratio > 3.0:
        entry_score -= 10
    else:
        entry_score -= 5
    
    # 3. MA60位置 (20分)
    if above_ma60 >= 20:
        entry_score += 20
    elif 15 <= above_ma60 < 20:
        entry_score += 15
    elif 10 <= above_ma60 < 15:
        entry_score += 10
    elif 5 <= above_ma60 < 10:
        entry_score += 5
    
    # 4. MACD DIF (15分)
    if dif >= 2:
        entry_score += 15
    elif 1 <= dif < 2:
        entry_score += 10
    elif 0 <= dif < 1:
        entry_score += 5
    
    # 5. 涨幅 (10分)
    if 7 <= pct_chg <= 10:
        entry_score += 10
    elif 10 < pct_chg:
        entry_score += 5
    elif 5 <= pct_chg < 7:
        entry_score += 5
    
    # 6. 波段位置 (±20分)
    consecutive_up = 1
    for i in range(idx - 1, max(0, idx - 10), -1):
        if df.iloc[i].get('pct_chg', 0) > 0:
            consecutive_up += 1
        else:
            break
    
    if consecutive_up == 1:
        entry_score += 5
    elif consecutive_up == 2:
        entry_score += 0
    elif consecutive_up == 3:
        entry_score -= 10
    else:
        entry_score -= 20
    
    if entry_score < min_score:
        return None
    
    return {
        'signal_date': str(row['trade_date']),
        'signal_close': round(close, 2),
        'entry_score': entry_score,
        'consecutive_up': consecutive_up,
        'pct_chg': round(pct_chg, 2),
        'vol_ratio': round(row.get('volume_ratio', 0), 2),
        'above_ma20_pct': round(above_ma20, 2),
        'above_ma60_pct': round(above_ma60, 2),
        'rsi6': round(row.get('rsi_bfq_6', 50), 2)
    }

def main():
    print('=' * 60)
    print('精选股票池扫描')
    print('=' * 60)
    print()
    print(f'股票池: {len(SELECTED_CODES)}只')
    print(f'评分阈值: 50')
    print()
    
    all_signals = []
    
    for code in SELECTED_CODES:
        df = get_data(code)
        if df is None:
            print(f'{code}: ❌ 无数据')
            continue
        
        # 检查最后30天
        start_idx = max(0, len(df) - 30)
        for idx in range(start_idx, len(df)):
            sig = check_signal(df, idx, min_score=50)
            if sig:
                sig['ts_code'] = code
                all_signals.append(sig)
        
        if len(all_signals) > 0:
            print(f'{code}: ✅ 找到{sum(1 for s in all_signals if s["ts_code"] == code)}个信号')
    
    print()
    print('=' * 60)
    print(f'总信号数: {len(all_signals)}')
    print('=' * 60)
    
    if len(all_signals) > 0:
        # 按评分排序
        all_signals.sort(key=lambda x: x['entry_score'], reverse=True)
        
        # 保存CSV
        df_signals = pd.DataFrame(all_signals)
        csv_path = os.path.join(OUTPUT_DIR, f'selected_scan_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv')
        df_signals.to_csv(csv_path, index=False, encoding='utf-8-sig')
        print(f'✅ CSV已保存: {csv_path}')
        
        # 显示TOP10
        print()
        print('TOP10信号:')
        for i, sig in enumerate(all_signals[:10], 1):
            print(f'{i:2}. {sig["signal_date"]} {sig["ts_code"]:12} 评分={sig["entry_score"]:2} RSI={sig.get("rsi6", 0):5.1f}')
    else:
        print('❌ 未发现任何信号')
    
    print()
    print('=' * 60)

if __name__ == '__main__':
    main()
