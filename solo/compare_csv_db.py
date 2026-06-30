"""Debug: Compare CSV vs DB data"""
import sqlite3
import pandas as pd

DB = r'D:\mystock\cache_daily\stock_data.db'

# Load CSV
csv_path = r'D:\mystock\solo\trend_feature_output\entry_precision_20260630_204617_qualified.csv'
df_csv = pd.read_csv(csv_path, encoding='utf-8-sig')
print(f"CSV signals: {len(df_csv)}")
print()

# Check DB for first few codes
conn = sqlite3.connect(DB)
cursor = conn.cursor()

for _, row in df_csv.head(3).iterrows():
    code = row['ts_code']
    sql = """SELECT trade_date, close, pct_chg, volume_ratio,
                    ma_bfq_5, ma_bfq_10, ma_bfq_20, ma_bfq_60,
                    rsi_bfq_6, macd_dif_bfq, macd_dea_bfq
             FROM stk_factor_pro 
             WHERE ts_code = ?
             ORDER BY trade_date DESC LIMIT 3"""
    cursor.execute(sql, (code,))
    rows = cursor.fetchall()
    
    print(f"{code}:")
    for r in rows:
        trade_date, close, pct_chg, vol_ratio = r[0], r[1], r[2], r[3]
        ma5, ma10, ma20, ma60 = r[4], r[5], r[6], r[7]
        rsi6, dif, dea = r[8], r[9], r[10]
        
        if ma20 and ma20 > 0:
            above_ma20 = (close - ma20) / ma20 * 100
        else:
            above_ma20 = 0
        
        print(f"  {trade_date} close={close:.2f} pct={pct_chg} vol={vol_ratio}")
        print(f"    MA20={ma20} above_MA20={above_ma20:.1f}% RSI6={rsi6} DIF={dif}")
        
        # Check v8 filters
        ma20_ok = 5 <= above_ma20 <= 18
        vol_ok = 0.8 <= vol_ratio <= 3.0
        pct_ok = 2 <= pct_chg <= 20 if pct_chg else False
        print(f"    [v8] MA20 OK: {ma20_ok}, Vol OK: {vol_ok}, Pct OK: {pct_ok}")
    print()

conn.close()
