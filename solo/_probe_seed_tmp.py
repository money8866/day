# -*- coding: utf-8 -*-
"""临时探查：stk_factor_pro 20230103 后按日覆盖率 → 决定 daily_basic/adj_factor 种子灌库范围"""
import sqlite3
con = sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db')
# 每日行数分布（>3500 视为完整全市场）
rows = con.execute("""
    SELECT trade_date, COUNT(*) AS cnt FROM stk_factor_pro
    WHERE trade_date>='20221201' GROUP BY trade_date ORDER BY trade_date
""").fetchall()
complete = [(d, c) for d, c in rows if c >= 4000]
print(f"总日数={len(rows)} 完整日(>=4000)={len(complete)}")
print("首/末完整日:", complete[0][0] if complete else None, complete[-1][0] if complete else None)
# 非完整尾部
print("最后6天(原始):")
for d, c in rows[-6:]:
    print(" ", d, c)
# 2023 开头看最早已有完整日
print("最早5个完整日:")
for d, c in complete[:5]:
    print(" ", d, c)
# 检查 daily_basic_cache 各列
print("\ndaily_basic_cache schema:", con.execute("PRAGMA table_info(daily_basic_cache)").fetchall())
# 检查 turnover_rate_f / volume_ratio 在宽表与 basic 重叠值是否一致
r = con.execute("""
    SELECT b.trade_date, b.ts_code, b.turnover_rate, s.turnover_rate, s.turnover_rate_f,
           b.volume_ratio, s.volume_ratio, s.total_mv, s.circ_mv
    FROM daily_basic_cache b JOIN stk_factor_pro s
      ON b.ts_code=s.ts_code AND b.trade_date=s.trade_date
    WHERE b.trade_date='20260803' LIMIT 5
""").fetchall()
for x in r:
    print(x)
con.close()
