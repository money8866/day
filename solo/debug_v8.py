"""Debug v8 - check actual data from DB"""
import sqlite3
import pandas as pd

DB = r'D:\mystock\cache_daily\stock_data.db'

# Check today's data for some codes
codes = ['688629.SH', '603379.SH', '688187.SH', '688135.SH']

conn = sqlite3.connect(DB)
cursor = conn.cursor()

for code in codes:
    sql = """SELECT trade_date, close, pct_chg, volume_ratio,
                    ma_bfq_5, ma_bfq_10, ma_bfq_20, ma_bfq_60,
                    rsi_bfq_6, macd_bfq_dif
             FROM stk_factor_pro 
             WHERE ts_code = ?
             ORDER BY trade_date DESC LIMIT 3"""
    cursor.execute(sql, (code,))
    rows = cursor.fetchall()
    
    print(f'{code}:')
    for row in rows:
        trade_date, close, pct_chg, vol_ratio = row[0], row[1], row[2], row[3]
        ma5, ma10, ma20, ma60 = row[4], row[5], row[6], row[7]
        rsi6, dif = row[8], row[9]
        
        if ma20 and ma20 > 0:
            above_ma20 = (close - ma20) / ma20 * 100
        else:
            above_ma20 = 0
        
        print(f'  {trade_date} close={close:.2f} pct={pct_chg:.1f}% vol={vol_ratio:.2f}')
        print(f'    MA5={ma5:.2f} MA10={ma10:.2f} MA20={ma20:.2f} MA60={ma60:.2f}')
        print(f'    above_MA20={above_ma20:.1f}% RSI6={rsi6:.1f} DIF={dif:.2f}')
        
        # Check filter conditions
        ma20_ok = 5 <= above_ma20 <= 18
        vol_ok = 0.8 <= vol_ratio <= 3.0
        pct_ok = 2 <= pct_chg <= 20
        
        print(f'    MA20 OK: {ma20_ok}, Vol OK: {vol_ok}, Pct OK: {pct_ok}')
    print()

conn.close()
