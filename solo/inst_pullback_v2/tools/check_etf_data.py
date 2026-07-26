import sqlite3
import sys
sys.path.insert(0, r'D:\mystock\solo')
import stock_cache as sc

conn = sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db')
cur = conn.cursor()
cur.execute("""
    SELECT DISTINCT ts_code FROM stk_factor_pro 
    WHERE ts_code LIKE '159%' OR ts_code LIKE '510%' 
    OR ts_code LIKE '512%' OR ts_code LIKE '515%' 
    OR ts_code LIKE '516%' OR ts_code LIKE '561%' 
    OR ts_code LIKE '562%'
    ORDER BY ts_code LIMIT 50
""")
rows = cur.fetchall()
print("ETF codes in DB:")
for r in rows:
    print(r[0])
conn.close()

print("\n--- Checking specific ETF codes ---")
etf_map = {
    '510300': 'SH', '510170': 'SH', '512480': 'SH', '512660': 'SH',
    '512690': 'SH', '512720': 'SH', '512880': 'SH', '515050': 'SH',
    '515210': 'SH', '515980': 'SH', '516160': 'SH', '516510': 'SH',
    '516520': 'SH', '516970': 'SH', '562500': 'SH', '561910': 'SH',
    '159801': 'SZ', '159611': 'SZ', '159638': 'SZ', '159647': 'SZ',
    '159707': 'SZ', '159732': 'SZ', '159825': 'SZ', '159828': 'SZ',
    '159869': 'SZ',
}
for code, suffix in etf_map.items():
    full = f"{code}.{suffix}"
    df = sc.cached_stk_factor_pro(full, '20260624', '20260724', silent=True)
    status = 'OK' if df is not None and not df.empty else 'FAIL'
    if df is not None and not df.empty:
        print(f"  {full}: {status} ({len(df)} rows, close={df['close'].iloc[-1]:.2f})")
    else:
        print(f"  {full}: {status}")