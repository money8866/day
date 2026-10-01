# -*- coding: utf-8 -*-
"""复检 20260917 加入的 V5.2 质量过滤（w7_quality_gate 三条规则）

规则（w7_second_wave_engine.py L82-83 / L1443-1463）：
  ① state ∈ {BREAKOUT_CONFIRM, SECOND_WAVE, RE_EXPANSION} → 排除
  ② type == CORE（涨幅<80%） → 排除
  ③ 选股日量比 volr > 0.66 → 排除

样本（当日引擎产出的「今日可操作榜」，非跟踪库）：
  A 旧引擎(4态)  : report_daily/w7_second_wave_*.md 解析（0820~0911）+ w7_today_action json（0909~0911）
  B 新引擎-门前  : w7_today_action json（0914~0917，当时落库未过滤）
  C 新引擎-门后  : w7_today_action json（0918~0930）
口径：前复权收盘，等权；超额 = 个股 − 全市场等权（daily_cache AVG(pct_chg)）
"""
import json
import os
import re
import sqlite3
from collections import defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
RD = os.path.join(BASE, "report_daily")
MKT = r"D:\mystock\cache_daily\stock_data.db"

STATES = {"DOWNTREND", "BASE", "IMPULSE", "EXTREME_CHURN", "ABSORPTION", "DRYUP", "RE_EXPANSION",
          "BREAKOUT_CONFIRM", "SECOND_WAVE", "T0_CONFIRM", "DISTRIBUTION", "FAILED",
          "BREAKOUT_RETEST", "MIDLINE_HOLD"}
EXCLUDE_STATES = ("BREAKOUT_CONFIRM", "SECOND_WAVE", "RE_EXPANSION")
VOLR_MAX = 0.66
HORIZONS = (1, 3, 5, 10)
PRICE_FROM, PRICE_TO = "20260801", "20261016"


def pick_json(dates):
    out = defaultdict(list)
    for fn in os.listdir(RD):
        if not (fn.startswith("w7_today_action_") and fn.endswith(".json")):
            continue
        d = fn[len("w7_today_action_"):-len(".json")]
        if d not in dates:
            continue
        try:
            j = json.load(open(os.path.join(RD, fn), encoding="utf-8"))
        except Exception:
            continue
        for s in j.get("signals", []):
            out[d].append({"code": s.get("code"), "name": s.get("name"), "state": s.get("state"),
                           "type": s.get("type"), "volr": s.get("volr")})
    return out


def pick_md(dates):
    """解析 md 里「## 今日可操作榜」表格行（版本差异大，用正则抽 code/type/volr/state）"""
    out = defaultdict(list)
    for d in dates:
        p = os.path.join(RD, f"w7_second_wave_{d}.md")
        if not os.path.exists(p):
            continue
        txt = open(p, encoding="utf-8", errors="ignore").read()
        seg = ""
        for head in ("## 今日可操作榜", "## 已突破标的完整名单"):
            if head in txt:
                seg = txt.split(head)[-1].split("\n## ")[0]
                break
        for line in seg.splitlines():
            m = re.search(r"(\d{6}\.(?:SZ|SH))", line)
            if not m:
                continue
            code = m.group(1)
            tm = re.search(r"\b(CORE|MID|EXT)\b", line)
            vm = re.search(r"[×x](\d+(?:\.\d+)?)", line)
            sm = [s for s in STATES if re.search(r"\b" + s + r"\b", line)]
            nm = re.search(r"\d{6}\.(?:SZ|SH)\s*\|\s*([^|]+?)\s*\|", line)
            out[d].append({"code": code, "name": nm.group(1) if nm else "", "state": sm[0] if sm else "",
                           "type": tm.group(1) if tm else "", "volr": float(vm.group(1)) if vm else None})
    return out


OLD_MD = ["20260820", "20260821", "20260831", "20260901", "20260902", "20260903",
          "20260904", "20260907", "20260908", "20260909", "20260910", "20260911"]
OLD_JS = ["20260909", "20260910", "20260911"]
NEW_PRE = ["20260914", "20260915", "20260916", "20260917"]
NEW_POST = ["20260918", "20260921", "20260922", "20260923", "20260924", "20260928", "20260929", "20260930"]

samples = {}
md_all = pick_md(OLD_MD)
old_js = pick_json(OLD_JS)
old = defaultdict(list)
for d in OLD_MD:
    if d in old_js:          # JSON 更精确（含 type/volr 原值），优先
        old[d] = old_js[d]
    elif d in md_all:
        old[d] = md_all[d]
samples["A 旧引擎(4态)"] = old
samples["B 新引擎-过滤前"] = pick_json(NEW_PRE)
samples["C 新引擎-过滤后"] = pick_json(NEW_POST)

# ── 收益 ──
codes = set()
for grp in samples.values():
    for lst in grp.values():
        codes |= {x["code"] for x in lst if x["code"]}
conn = sqlite3.connect(MKT)
ph = ",".join(["?"] * len(codes))
pr = __import__("pandas").read_sql_query(
    f"SELECT d.ts_code, d.trade_date, d.close, a.adj_factor FROM daily_cache d "
    f"LEFT JOIN adj_factor_cache a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date "
    f"WHERE d.ts_code IN ({ph}) AND d.trade_date>=? AND d.trade_date<=? ORDER BY d.ts_code, d.trade_date",
    conn, params=list(codes) + [PRICE_FROM, PRICE_TO])
mk = __import__("pandas").read_sql_query(
    "SELECT trade_date, AVG(pct_chg) m FROM daily_cache WHERE trade_date>=? AND trade_date<=? "
    "AND pct_chg IS NOT NULL GROUP BY trade_date ORDER BY trade_date", conn, params=[PRICE_FROM, PRICE_TO])
cal = [str(x) for x in mk.trade_date]
mkmap = dict(zip(cal, mk.m.astype(float) / 100.0))
px = {}
for c, g in pr.groupby("ts_code"):
    g = g.reset_index(drop=True)
    f = g.adj_factor.ffill()
    last = f.iloc[-1]
    p = g.close.astype(float) * f / last if last == last else g.close.astype(float)
    px[c] = dict(zip(g.trade_date.astype(str), p))


def mkt_ret(d, h):
    i = cal.index(d)
    acc = 1.0
    for k in range(1, h + 1):
        acc *= 1.0 + mkmap.get(cal[i + k], 0.0)
    return acc - 1.0


def rets(d, c):
    s = px.get(c)
    if not s or d not in s:
        return {}
    i = cal.index(d)
    out = {}
    for h in HORIZONS:
        if i + h < len(cal):
            t = cal[i + h]
            if t in s:
                out[h] = s[t] / s[d] - 1.0 - mkt_ret(d, h)
    return out


def gate(x):
    if x.get("state") in EXCLUDE_STATES:
        return False
    if x.get("type") == "CORE":
        return False
    v = x.get("volr")
    if isinstance(v, (int, float)) and v > VOLR_MAX:
        return False
    return True


def agg(items):
    """items: [(d, x)] → 各 horizon 超额均值 / 胜率 / n"""
    acc = {h: [] for h in HORIZONS}
    for d, x in items:
        r = rets(d, x["code"])
        for h, v in r.items():
            acc[h].append(v)
    return acc


def line(tag, items):
    a = agg(items)
    cells = []
    for h in HORIZONS:
        v = a[h]
        cells.append(f"T+{h} {sum(v)/len(v)*100:+.2f}%/胜{sum(1 for z in v if z > 0)/len(v)*100:.0f}%(n{len(v)})"
                     if v else f"T+{h} --")
    print(f"  {tag:<26}条数={len(items):<4}" + " | ".join(cells))


print("=" * 118)
print("一、按样本批次：全部信号 vs 通过 V5.2 过滤（超额收益，前复权收盘 − 全市场等权）")
print("=" * 118)
for era, grp in samples.items():
    allit = [(d, x) for d in sorted(grp) for x in grp[d]]
    passit = [(d, x) for d, x in allit if gate(x)]
    print(f"\n【{era}】{len(grp)} 个交易日，信号 {len(allit)} 条 → 过滤后 {len(passit)} 条"
          f"（保留 {len(passit)/max(len(allit),1)*100:.0f}%）")
    line("全部信号", allit)
    line("通过过滤", passit)
    line("被过滤掉", [(d, x) for d, x in allit if not gate(x)])

print()
print("=" * 118)
print("二、三条规则逐条复检（全部样本合并 A+B+C；超额收益）")
print("=" * 118)
ALL = [(d, x) for grp in samples.values() for d in sorted(grp) for x in grp[d]]
print("\n① 按状态（V5.2 排除 CONFIRM/SECOND_WAVE/RE_EXPANSION）")
for st in ["BREAKOUT_RETEST", "T0_CONFIRM", "MIDLINE_HOLD", "BREAKOUT_CONFIRM", "SECOND_WAVE", "RE_EXPANSION"]:
    items = [(d, x) for d, x in ALL if x.get("state") == st]
    if items:
        line(st + ("  ← 被排除" if st in EXCLUDE_STATES else ""), items)

print("\n② 按类型（V5.2 排除 CORE）")
for ty in ["CORE", "MID", "EXT"]:
    items = [(d, x) for d, x in ALL if x.get("type") == ty]
    if items:
        line(ty + ("  ← 被排除" if ty == "CORE" else ""), items)
line("类型缺失", [(d, x) for d, x in ALL if not x.get("type")])

print("\n③ 按选股日量比分档（V5.2 只留 ≤0.66）")
BUCKETS = [("<0.66  ← 保留", 0.0, 0.66), ("0.66~1.0", 0.66, 1.0), ("1.0~1.43", 1.0, 1.43),
           (">1.43", 1.43, 99.0)]
for name, lo, hi in BUCKETS:
    items = [(d, x) for d, x in ALL if isinstance(x.get("volr"), (int, float)) and lo <= x["volr"] < hi]
    if items:
        line(name, items)
line("量比缺失", [(d, x) for d, x in ALL if not isinstance(x.get("volr"), (int, float))])

print()
print("=" * 118)
print("三、样本口径校验：A 批（旧引擎 md）状态构成 + 仅新引擎(B+C)规则复检")
print("=" * 118)
Acomp = defaultdict(int)
for d in samples["A 旧引擎(4态)"]:
    for x in samples["A 旧引擎(4态)"][d]:
        Acomp[x.get("state") or "(未识别)"] += 1
print("  A 批状态构成：", dict(sorted(Acomp.items(), key=lambda kv: -kv[1])))

NEW = [(d, x) for era in ("B 新引擎-过滤前", "C 新引擎-过滤后")
       for d in sorted(samples[era]) for x in samples[era][d]]
print(f"\n  仅新引擎样本 {len(NEW)} 条（不含旧引擎 md 段落）")
print("\n  ① 按状态")
for st in ["BREAKOUT_RETEST", "T0_CONFIRM", "MIDLINE_HOLD", "BREAKOUT_CONFIRM", "SECOND_WAVE", "RE_EXPANSION"]:
    items = [(d, x) for d, x in NEW if x.get("state") == st]
    if items:
        line(st + ("  ← 被排除" if st in EXCLUDE_STATES else ""), items)
print("\n  ② 按类型")
for ty in ["CORE", "MID", "EXT"]:
    items = [(d, x) for d, x in NEW if x.get("type") == ty]
    if items:
        line(ty + ("  ← 被排除" if ty == "CORE" else ""), items)
print("\n  ③ 按选股日量比分档")
for name, lo, hi in BUCKETS:
    items = [(d, x) for d, x in NEW if isinstance(x.get("volr"), (int, float)) and lo <= x["volr"] < hi]
    if items:
        line(name, items)
print("\n  ④ 三条规则各自单独作用（仅新引擎）")
line("基准：全部新引擎样本", NEW)
line("只排状态(①)", [(d, x) for d, x in NEW if x.get("state") not in EXCLUDE_STATES])
line("只排CORE(②)", [(d, x) for d, x in NEW if x.get("type") != "CORE"])
line("只留量比≤0.66(③)", [(d, x) for d, x in NEW
                     if not isinstance(x.get("volr"), (int, float)) or x["volr"] <= VOLR_MAX])
line("三条全开(现行)", [(d, x) for d, x in NEW if gate(x)])

print("\n  ⑤ 在 BREAKOUT_RETEST 内部看 ②③（①之后真正生效的边际筛选）")
BR = [(d, x) for d, x in NEW if x.get("state") == "BREAKOUT_RETEST"]
line("基准：BREAKOUT_RETEST", BR)
line("  CORE（被②排）", [(d, x) for d, x in BR if x.get("type") == "CORE"])
line("  非CORE（②放行）", [(d, x) for d, x in BR if x.get("type") != "CORE"])
line("  volr≤0.66（③留）", [(d, x) for d, x in BR
                        if isinstance(x.get("volr"), (int, float)) and x["volr"] <= VOLR_MAX])
line("  volr>0.66（③排）", [(d, x) for d, x in BR
                        if isinstance(x.get("volr"), (int, float)) and x["volr"] > VOLR_MAX])
line("  ②③全开后的结果", [(d, x) for d, x in BR if gate(x)])

print("=" * 118)
