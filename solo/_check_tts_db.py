import sqlite3, os
from theme_trend_sentiment_score import OUTPUT_DB as TTS_DB
T2 = 'report_daily/theme_scores.db'
print('TTS_DB:', TTS_DB)
print('TTS_DB 存在:', os.path.exists(TTS_DB))
if os.path.exists(TTS_DB):
    c = sqlite3.connect(TTS_DB)
    tabs = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    print('TTS tables:', tabs)
    if 'theme_scores' in tabs:
        cols = [r[1] for r in c.execute('PRAGMA table_info(theme_scores)')]
        print('TTS theme_scores cols 含 lifecycle/gate:', 'lifecycle' in cols, 'gate_tier' in cols)
        print('TTS 日期范围:', c.execute('SELECT MIN(trade_date),MAX(trade_date) FROM theme_scores').fetchone())
        print('TTS 行数:', c.execute('SELECT COUNT(*) FROM theme_scores').fetchone())
    c.close()
