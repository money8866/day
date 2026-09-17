import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')
conn = sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db')
print("fina ann_date max:", conn.execute("SELECT MAX(ann_date) FROM fina_indicator_cache").fetchone()[0])
print("fina distinct ts_code:", conn.execute("SELECT COUNT(DISTINCT ts_code) FROM fina_indicator_cache").fetchone()[0])
for r in conn.execute("SELECT end_date, COUNT(*) n FROM fina_indicator_cache GROUP BY end_date ORDER BY end_date DESC LIMIT 10"):
    print("  end_date", r)
print("ann_date 2026 sample:", conn.execute(
    "SELECT end_date, COUNT(*) FROM fina_indicator_cache WHERE ann_date>='20260101' GROUP BY end_date ORDER BY end_date").fetchall())
print("index codes:", conn.execute("SELECT DISTINCT ts_code FROM index_daily_cache").fetchall())
print("seos_daily phases:", )
conn.close()
import pandas as pd
d = pd.read_csv(r'd:\mystock\solo\sector_0915\data\sector_seos_daily.csv', dtype={'trade_date': str}, encoding='utf-8-sig')
print("seos_daily shape:", d.shape, "| dates:", d.trade_date.min(), "->", d.trade_date.max(), "| n_dates:", d.trade_date.nunique())
print("theme_phase values:", d.theme_phase.value_counts().to_dict())
print("rotation_signal values:", d.rotation_signal.value_counts().head(8).to_dict())
last = d[d.trade_date == d.trade_date.max()]
print("last date:", d.trade_date.max(), "| non-DORMANT:", last[last.theme_phase != 'DORMANT'][['sector_id','sector_name','theme_phase','seos_score','theme_health']].to_dict('records'))
h = pd.read_csv(r'd:\mystock\solo\sector_0915\data\sector_state_history.csv', dtype={'trade_date': str}, encoding='utf-8-sig')
print("state_history current_state values:", h.current_state.value_counts().to_dict())
