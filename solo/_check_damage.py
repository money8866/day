import sqlite3
c = sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db')

# 检查 stk_factor_pro 表中 20260731 的数据有多少技术指标为 NULL
total_731 = c.execute("SELECT COUNT(*) FROM stk_factor_pro WHERE trade_date='20260731'").fetchone()[0]
damaged_731 = c.execute("SELECT COUNT(*) FROM stk_factor_pro WHERE trade_date='20260731' AND ma_bfq_5 IS NULL").fetchone()[0]
print(f'stk_factor_pro 20260731: 总 {total_731} 行, 技术指标被破坏(NULL) {damaged_731} 行')

# 检查其他日期是否也有破坏
total_all = c.execute("SELECT COUNT(*) FROM stk_factor_pro").fetchone()[0]
damaged_all = c.execute("SELECT COUNT(*) FROM stk_factor_pro WHERE ma_bfq_5 IS NULL").fetchone()[0]
print(f'stk_factor_pro 全部: 总 {total_all} 行, 技术指标为NULL {damaged_all} 行')

# 检查哪些日期有破坏
damaged_dates = c.execute("SELECT trade_date, COUNT(*) as cnt FROM stk_factor_pro WHERE ma_bfq_5 IS NULL GROUP BY trade_date ORDER BY trade_date DESC LIMIT 10").fetchall()
print(f'\n技术指标为NULL的日期(前10):')
for d, cnt in damaged_dates:
    print(f'  {d}: {cnt} 行')

c.close()
