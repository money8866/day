import sqlite3
conn = sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db')
rows = conn.execute("""
    SELECT trade_date, close, pct_chg, vol, volume_ratio,
           ma_bfq_20, ma_bfq_10, ma_bfq_5, macd_bfq, rsi_bfq_6
    FROM stk_factor_pro
    WHERE ts_code='002409.SZ'
    ORDER BY trade_date
""").fetchall()
for r in rows:
    dt = r[0]
    if dt >= '20260501' and dt <= '20260625':
        print(dt, "close=%.2f" % r[1], "pct=%.2f%%" % r[2], "vol=%.0f" % r[3],
              "vr=%.2f" % r[4], "ma20=%.2f" % r[5], "ma10=%.2f" % r[6],
              "ma5=%.2f" % r[7], "macd=%.4f" % r[8], "rsi6=%.1f" % r[9])
conn.close()