# -*- coding: utf-8 -*-
"""回放前的前置检查：确认旧引擎所需的外部输入在 0825~0916 区间是否齐备。"""
import os
import glob
import sqlite3

CACHE = r"D:\mystock\cache_daily"
DB = os.path.join(CACHE, "stock_data.db")
SOLO = r"d:\mystock\solo"

print("=" * 60)
print("1) 交易日序列 20260825~20260916")
con = sqlite3.connect(DB)
ds = [r[0] for r in con.execute(
    "SELECT DISTINCT trade_date FROM daily_cache WHERE trade_date>=? AND trade_date<=? ORDER BY trade_date",
    ("20260825", "20260916"))]
sd = [r[0] for r in con.execute(
    "SELECT DISTINCT trade_date FROM daily_cache WHERE trade_date>? ORDER BY trade_date LIMIT 12", ("20260916",))]
print("  区间内交易日 %d 天: %s" % (len(ds), " ".join(ds)))
print("  0916 之后: %s" % " ".join(sd))
print("  daily_basic_cache 覆盖检查:")
for d in ds:
    n = con.execute("SELECT COUNT(*) FROM daily_basic_cache WHERE trade_date=?", (d,)).fetchone()[0]
    print("    %s  daily_basic=%d" % (d, n))

print("=" * 60)
print("2) market_curve.csv（旧引擎 RS/市场环境基准）")
p = os.path.join(CACHE, "market_curve.csv")
if os.path.exists(p):
    import pandas as pd
    mc = pd.read_csv(p, dtype={"trade_date": str})
    print("  存在 行数=%d  首=%s 末=%s" % (len(mc), mc.trade_date.iloc[0], mc.trade_date.iloc[-1]))
else:
    print("  不存在（旧引擎会自动全表聚合重建，首次较慢）")

print("=" * 60)
print("3) market_{date}.csv 快照")
for d in ds:
    f = os.path.join(CACHE, "market_%s.csv" % d)
    print("    %s  %s" % (d, "有" if os.path.exists(f) else "缺"))

print("=" * 60)
print("4) SLI 龙头快照（load_sli_codes asof）")
for pat in ("sli/**/*.csv", "sli/**/*.json", "sli/output/*"):
    hits = sorted(glob.glob(os.path.join(SOLO, pat), recursive=True))[:8]
    if hits:
        print("  %s -> %d 个（示例）" % (pat, len(hits)))
        for h in hits:
            print("      %s" % os.path.relpath(h, SOLO))

print("=" * 60)
print("5) IGE 输出文件（load_ige_adj asof）")
hits = sorted(glob.glob(os.path.join(SOLO, "ige", "output", "ige_full_*.csv")))
print("  共 %d 个；最早 5 个 / 最晚 5 个:" % len(hits))
for h in hits[:5] + (["..."] if len(hits) > 10 else []) + hits[-5:]:
    print("      %s" % os.path.basename(h) if h != "..." else "      ...")

print("=" * 60)
print("6) report_daily 目录")
for d in (os.path.join(SOLO, "report_daily"), r"d:\mystock\report_daily"):
    print("  %s  %s" % (d, "存在" if os.path.isdir(d) else "不存在"))
    if os.path.isdir(d):
        fs = sorted(glob.glob(os.path.join(d, "w7_second_wave_*.md")))
        print("      w7 报告 %d 个；最早 %s 最晚 %s"
              % (len(fs), os.path.basename(fs[0]) if fs else "-", os.path.basename(fs[-1]) if fs else "-"))
con.close()
