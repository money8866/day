# -*- coding: utf-8 -*-
"""w7_hvt 落库全量普查：样本量 / 日期跨度 / 实时 vs 回填 / 状态·类型·量比结构（只读）"""
import json
import os
import sqlite3
from collections import Counter, defaultdict
from datetime import date as _d

BASE = os.path.dirname(os.path.abspath(__file__))
conn = sqlite3.connect(os.path.join(BASE, "picks_db", "stock_picks.db"))
conn.row_factory = sqlite3.Row

rows = conn.execute(
    "SELECT pick_date, ts_code, stock_name, signal, score, created_at, indicators "
    "FROM stock_pick WHERE strategy_id='w7_hvt' ORDER BY pick_date").fetchall()

print(f"w7_hvt 总记录 {len(rows)} 条，跨度 {rows[0]['pick_date']} ~ {rows[-1]['pick_date']}")

by_date = defaultdict(list)
for r in rows:
    by_date[r["pick_date"]].append(r)


def gap(r):
    try:
        return (_d(int(r["created_at"][:4]), int(r["created_at"][5:7]), int(r["created_at"][8:10]))
                - _d(int(r["pick_date"][:4]), int(r["pick_date"][4:6]), int(r["pick_date"][6:8]))).days
    except Exception:
        return 999


print()
print(f"{'日期':<10}{'条数':>4}{'落库时间':<21}{'滞后':>5}  状态构成")
print("-" * 100)
live_n = back_n = 0
for d in sorted(by_date):
    rs = by_date[d]
    g = gap(rs[0])
    c = Counter(r["signal"] for r in rs)
    tag = "实时" if g == 0 else ("次日" if g == 1 else f"回填{g}d")
    if g <= 1:
        live_n += len(rs)
    else:
        back_n += len(rs)
    print(f"{d:<10}{len(rs):>4}{rs[0]['created_at']:<21}{tag:>7}  " +
          "、".join(f"{k}×{v}" for k, v in c.most_common()))
print("-" * 100)
print(f"实时/次日样本 {live_n} 条；回填样本 {back_n} 条")
