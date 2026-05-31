"""初始化持仓数据 - 电力ETF"""
import sqlite3

DB_PATH = r'D:\mystock\cache_daily\etf_result.db'

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# 创建持仓表（如果不存在）
cursor.execute("""
    CREATE TABLE IF NOT EXISTS portfolio (
        ts_code TEXT PRIMARY KEY,
        industry TEXT,
        buy_date TEXT,
        buy_price REAL,
        current_price REAL,
        shares INTEGER DEFAULT 0,
        stop_loss REAL DEFAULT 0,
        take_profit REAL DEFAULT 0,
        status TEXT DEFAULT 'holding'
    )
""")

cursor.execute("""
    CREATE TABLE IF NOT EXISTS trade_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date TEXT,
        ts_code TEXT,
        industry TEXT,
        action TEXT,
        price REAL,
        shares INTEGER,
        pnl REAL DEFAULT 0,
        reason TEXT
    )
""")

cursor.execute("""
    CREATE TABLE IF NOT EXISTS daily_snapshot (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date TEXT,
        ts_code TEXT,
        industry TEXT,
        score REAL,
        signal TEXT,
        stage TEXT,
        pct_chg REAL,
        position_pct REAL,
        emotion REAL
    )
""")

# 检查是否已有持仓
cursor.execute("SELECT COUNT(*) FROM portfolio WHERE status='holding'")
count = cursor.fetchone()[0]

if count == 0:
    # 插入电力ETF持仓
    cursor.execute("""
        INSERT INTO portfolio (ts_code, industry, buy_date, buy_price, current_price, shares, stop_loss, take_profit, status)
        VALUES ('159611.SZ', '电力', '20260514', 1.20, 1.20, 1000, 1.14, 1.44, 'holding')
    """)
    print("OK - added: power ETF(159611.SZ), buy_price=1.20, buy_date=20260514")
else:
    cursor.execute("SELECT ts_code, industry, buy_price, buy_date FROM portfolio WHERE status='holding'")
    rows = cursor.fetchall()
    print(f"Current holdings: {count}")
    for r in rows:
        print(f"  - {r[1]}({r[0]}): 买入价{r[2]}, 买入日{r[3]}")

conn.commit()
conn.close()
