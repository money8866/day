"""核对 prev_theme_data 数据源：cache_backbone_tushare/theme_trend_sentiment.db 的日期与字段"""
import sqlite3, os

for p in [r'd:/mystock/solo/cache_backbone_tushare/theme_trend_sentiment.db',
          r'd:/mystock/cache_backbone_tushare/theme_trend_sentiment.db']:
    if not os.path.exists(p):
        print('不存在:', p); continue
    print('== ', p)
    c = sqlite3.connect(p); cur = c.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tabs = [r[0] for r in cur.fetchall()]
    print('  表:', tabs)
    for t in tabs:
        cur.execute(f"PRAGMA table_info({t})")
        cols = [r[1] for r in cur.fetchall()]
        print(f'  [{t}] 字段:', cols)
        dcol = 'trade_date' if 'trade_date' in cols else ('date' if 'date' in cols else None)
        if dcol:
            cur.execute(f"SELECT DISTINCT {dcol} FROM {t} ORDER BY {dcol}")
            ds = [r[0] for r in cur.fetchall()]
            print(f'  [{t}] 日期数={len(ds)}:', ds[:10], '...', ds[-5:] if len(ds) > 10 else '')
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            print(f'  [{t}] 行数:', cur.fetchone()[0])
    c.close()
