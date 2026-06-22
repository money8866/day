import sys, os, pandas as pd
os.chdir(r'd:\mystock\solo')
sys.path.insert(0, r'd:\mystock\solo')
from tushare_quant import pro, calculate_short_term_win_score

stocks = {
    '京东方A': '000725.SZ',
    '阿石创': '300842.SZ',
    '埃斯顿': '002747.SZ',
}

for name, code in stocks.items():
    print(f'\n========== {name} ({code}) ==========')
    
    # 查6月每日数据（用2025年6月历史数据演示算法，2026年6月数据待补充）
    df = pro.daily(ts_code=code, start_date='20250601', end_date='20250630')
    if df is not None and not df.empty:
        df = df.sort_values('trade_date')
        print(f'6月交易日: {len(df)}天')
        for _, row in df.iterrows():
            date = row['trade_date']
            close = row['close']
            pct_chg = row['pct_chg']
            vol = row['vol']
            print(f"  {date} 收={close:.2f} 涨幅={pct_chg:+.2f}% 量={vol:.0f}万手")
    else:
        print('无6月数据')
