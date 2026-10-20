# -*- coding: utf-8 -*-
"""补充审计：列出 vsw 落库明细 + 信号标签统计 + 每日落库条数"""
import sqlite3
from collections import Counter, defaultdict

DB = r'd:\mystock\solo\picks_db\stock_picks.db'
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

rows = con.execute("""
    SELECT pick_date, ts_code, stock_name, close, signal, score, stop_price, reason
    FROM stock_pick
    WHERE strategy_id='vsw'
    ORDER BY pick_date, rank_no""").fetchall()
con.close()

print(f"=== vsw 落库明细 n={len(rows)} ===")
print()

KEYS = ('F2', '·主线共振', '·L3共振')
cnt_f2 = 0
cnt_main = 0
cnt_l3 = 0
by_date = Counter()

for r in rows:
    sig = r['signal'] or ''
    reason = r['reason'] or ''
    hits = [k for k in KEYS if k in sig]
    if 'F2' in sig:
        cnt_f2 += 1
    if '·主线共振' in sig:
        cnt_main += 1
    if '·L3共振' in sig:
        cnt_l3 += 1
    by_date[r['pick_date']] += 1
    mark = ('  <<< ' + '|'.join(hits)) if hits else ''
    print(f"{r['pick_date']}  {r['ts_code']}  {r['stock_name']}  close={r['close']}  "
          f"score={r['score']}  stop_price={r['stop_price']}{mark}")
    print(f"      signal : {sig}")
    print(f"      reason : {reason}")

print()
print("--- 信号标签统计 ---")
print(f"  含 'F2' 的记录数        : {cnt_f2}")
print(f"  含 '·主线共振' 的记录数 : {cnt_main}")
print(f"  含 '·L3共振' 的记录数   : {cnt_l3}")

print()
print("--- 按 pick_date 统计每天落库条数 ---")
for d in sorted(by_date):
    print(f"  {d}: {by_date[d]}")
print(f"  合计: {sum(by_date.values())} 条, {len(by_date)} 个交易日")
