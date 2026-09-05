import sqlite3
from hvt_bull.data_loader import DB_PATH

conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=10)
rows = conn.execute(
    "SELECT trade_date, COUNT(*) FROM stk_factor_pro "
    "WHERE trade_date >= '20260601' AND ts_code='000001.SZ' "
    "GROUP BY trade_date ORDER BY trade_date"
).fetchall()
conn.close()
print(f'days since 0601: {len(rows)}')
if rows:
    print('first:', rows[0], ' last:', rows[-1])
