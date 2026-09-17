import sqlite3, pandas as pd, sys
sys.stdout.reconfigure(encoding='utf-8')

DB = r'D:\mystock\cache_daily\stock_data.db'
conn = sqlite3.connect(DB)
tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
print("TABLES:", tables)
for t in tables:
    n = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
    print(f"  {t}: {n} rows")

for t in ['daily_cache', 'daily_basic_cache', 'adj_factor_cache', 'index_daily_cache']:
    if t in tables:
        r = conn.execute(f'SELECT MIN(trade_date), MAX(trade_date), COUNT(DISTINCT ts_code) FROM "{t}"').fetchone()
        print(f"  {t} range: {r}")

for t in tables:
    if any(k in t for k in ('fina', 'indicator', 'basic', 'stock_basic', 'income', 'balance', 'cashflow')):
        cols = [c[1] for c in conn.execute(f'PRAGMA table_info("{t}")').fetchall()]
        print(f"  COLS {t}: {cols}")

conn.close()

mb = pd.read_csv(r'd:\mystock\solo\sector_0915\data\sector_membership.csv',
                 dtype={'ts_code': str, 'effective_date': str}, encoding='utf-8-sig')
print("\nMEMBERSHIP rows:", len(mb), "unique stocks:", mb.ts_code.nunique())
print("membership_type:", mb.membership_type.value_counts().to_dict())
print("per-sector size (top10):", mb.groupby('sector_id').size().sort_values(ascending=False).head(10).to_dict())
print("per-sector size (min5):", mb.groupby('sector_id').size().sort_values().head(5).to_dict())
print("effective_date values:", mb.effective_date.value_counts().to_dict())
print("mapping_method:", mb.mapping_method.value_counts().to_dict())
print("board_type:", mb.board_type.value_counts().to_dict())

conn = sqlite3.connect(DB)
have = set(pd.read_sql("SELECT ts_code FROM daily_cache WHERE trade_date='20260915'", conn).ts_code)
hb = pd.read_sql("SELECT COUNT(DISTINCT ts_code) n FROM daily_basic_cache WHERE trade_date='20260915'", conn).n[0]
lastd = pd.read_sql("SELECT MAX(trade_date) d FROM daily_cache", conn).d[0]
conn.close()
codes = set(mb.ts_code.unique())
print("\ndaily 20260915 stocks:", len(have), "| last daily date:", lastd)
print("membership covered by daily:", len(codes & have), "/", len(codes))
print("daily_basic 20260915 distinct stocks:", hb)
missing = sorted(codes - have)[:20]
print("missing sample:", missing)
