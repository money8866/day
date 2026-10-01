# -*- coding: utf-8 -*-
"""W7「回填偏差」量化：库内记录 vs 当日存档 JSON（只读）

两个问题：
  A. 库内 w7_hvt 记录 与 当日实时存档 JSON 是否一致？（判定实时 / 回填）
  B. 对存在旧引擎实时存档的日期，比较「旧引擎当时真会推送」与「库内现有记录」的 T+N 实盘表现。

数据源：picks_db/stock_picks.db、solo/report_daily/w7_today_action_*.json、
       cache_daily/stock_data.db（daily_cache + adj_factor_cache，前复权）
"""
import json
import os
import sqlite3
from collections import defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "picks_db", "stock_picks.db")
RD = os.path.join(BASE, "report_daily")
MKT = r"D:\mystock\cache_daily\stock_data.db"

START, END = "20260820", "20260930"
HORIZONS = (1, 3, 5, 10)
PRICE_FROM, PRICE_TO = "20260901", "20261016"

# ── 库内记录 ──
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT pick_date, ts_code, stock_name, signal, created_at FROM stock_pick "
    "WHERE strategy_id='w7_hvt' AND pick_date>=? AND pick_date<=? ORDER BY pick_date, ts_code",
    (START, END)).fetchall()
db = defaultdict(list)
for r in rows:
    db[r["pick_date"]].append((r["ts_code"], r["stock_name"], r["signal"] or "", r["created_at"]))

# ── 存档 JSON ──
arch = {}
for fn in os.listdir(RD):
    if fn.startswith("w7_today_action_") and fn.endswith(".json"):
        d = fn[len("w7_today_action_"):-len(".json")]
        if not (START <= d <= END) or not d.isdigit():
            continue
        try:
            j = json.load(open(os.path.join(RD, fn), encoding="utf-8"))
        except Exception:
            continue
        arch[d] = [(s.get("code", ""), s.get("name", ""), s.get("state", "")) for s in j.get("signals", [])]


def eng(tags):
    t = set(tags)
    if not t:
        return "-"
    return "新5态" if "BREAKOUT_RETEST" in t else "旧4态"


print("=" * 96)
print("A. 库内记录 vs 当日实时存档 JSON")
print("=" * 96)
print(f"{'日期':<10}{'库内':>4}{'存档':>4}  {'存档引擎':<8}{'库内引擎':<8}{'落库时间':<20}{'交集':>6}{'判定'}")
print("-" * 96)
dates_old_live = []
for d in sorted(set(db) | set(arch)):
    a, b = arch.get(d, []), db.get(d, [])
    sa, sb = set(x[0] for x in a), set(x[0] for x in b)
    created = min((x[3] for x in b), default="-")
    gap = ""
    if created != "-":
        from datetime import date as _d
        try:
            gap = (_d(int(created[:4]), int(created[5:7]), int(created[8:10]))
                   - _d(int(d[:4]), int(d[4:6]), int(d[6:8]))).days
        except Exception:
            gap = "?"
    judge = "实时当日" if gap == 0 else (f"回填T+{gap}d" if isinstance(gap, int) else "-")
    inter = len(sa & sb)
    print(f"{d:<10}{len(b):>4}{len(a):>4}  {eng([x[2] for x in a]):<8}{eng([x[2] for x in b]):<8}"
          f"{created:<20}{inter:>6}  {judge}")
    if a and eng([x[2] for x in a]) == "旧4态" and gap != 0:
        dates_old_live.append(d)
print("-" * 96)
print(f"存在旧引擎实时存档且库内为回填的日期：{'、'.join(dates_old_live) if dates_old_live else '无'}")

# ── B. 表现对比 ──
if dates_old_live:
    mc = sqlite3.connect(MKT)
    all_codes = set()
    for d in dates_old_live:
        all_codes |= set(x[0] for x in arch[d]) | set(x[0] for x in db[d])
    ph = ",".join(["?"] * len(all_codes))
    q = (f"SELECT d.ts_code, d.trade_date, d.close, a.adj_factor "
         f"FROM daily_cache d LEFT JOIN adj_factor_cache a "
         f"ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date "
         f"WHERE d.ts_code IN ({ph}) AND d.trade_date>=? AND d.trade_date<=? "
         f"ORDER BY d.ts_code, d.trade_date")
    pr = __import__("pandas").read_sql_query(q, mc, params=list(all_codes) + [PRICE_FROM, PRICE_TO])
    mk = __import__("pandas").read_sql_query(
        "SELECT trade_date, AVG(pct_chg) m FROM daily_cache WHERE trade_date>=? AND trade_date<=? "
        "AND pct_chg IS NOT NULL GROUP BY trade_date ORDER BY trade_date", mc, params=[PRICE_FROM, PRICE_TO])
    cal = [str(x) for x in mk.trade_date]
    mkmap = dict(zip(cal, mk.m.astype(float) / 100.0))

    px = {}
    for code, g in pr.groupby("ts_code"):
        g = g.reset_index(drop=True)
        last = g.adj_factor.ffill().iloc[-1]
        adj = g.adj_factor.ffill().fillna(1.0)
        g["p"] = g.close.astype(float) * (adj if last and last == last else 1.0) / (last if last and last == last else 1.0)
        px[code] = dict(zip(g.trade_date.astype(str), g.p))

    def path(d, h):
        try:
            i = cal.index(d)
        except ValueError:
            return None
        if i + h >= len(cal):
            return None
        return cal[i + h]

    def stats(d, codes):
        out = {h: [] for h in HORIZONS}
        mkt = {h: [] for h in HORIZONS}
        for c in codes:
            s = px.get(c)
            if not s or d not in s:
                continue
            base = s[d]
            for h in HORIZONS:
                t = path(d, h)
                if t is None or t not in s:
                    continue
                out[h].append(s[t] / base - 1.0)
                acc = 1.0
                for k in range(1, h + 1):
                    acc *= 1.0 + mkmap.get(cal[cal.index(d) + k], 0.0)
                mkt[h].append(acc - 1.0)
        return out, mkt

    print()
    print("=" * 96)
    print("B. 旧引擎实时信号 vs 库内(回填)信号 的 T+N 表现（等权，前复权收盘；超额=个股-全市场等权）")
    print("=" * 96)
    for d in dates_old_live:
        old = [x[0] for x in arch[d]]
        new = [x[0] for x in db[d]]
        print(f"\n【{d}】旧引擎实时 {len(old)} 只 vs 库内回填 {len(new)} 只")
        for tag, codes in (("旧引擎实时", old), ("库内回填", new)):
            so, sm = stats(d, codes)
            cells = []
            for h in HORIZONS:
                v = so[h]
                m = sm[h]
                if not v:
                    cells.append(f"T+{h}: --")
                    continue
                ex = sum(a - b for a, b in zip(v, m)) / len(v)
                win = sum(1 for a, b in zip(v, m) if a > b) / len(v)
                cells.append(f"T+{h}: 均{sum(v)/len(v)*100:+.2f}% 超{ex*100:+.2f}% 胜{win*100:.0f}%")
            print(f"  {tag:<10}n={len(codes):<3} " + " | ".join(cells))
