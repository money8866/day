# -*- coding: utf-8 -*-
"""V10.8 回测：强股上涨比「连续性分级」的未来收益，用于决定是否接入仓位。

事实标签（沿用 _bt_six_diag.py / V10.4 调参的权威口径）：
    fwd_benchN = 未来 N 日「等权行业 ETF 基准」收益（%）
    label      = fwd_bench20 > 3%（与 _bt_six_diag 的「事后强市」定义一致）
辅助基准：
    fwd_mkt20  = 未来 20 日全市场等权收益（日涨跌幅截尾 ±21% 后复合），与 ETF 基准互证

防前视：分级只用 T 日及之前的 strong_up；收益只用 T 日之后。
统计 caveat：未来 20 日窗口重叠 → 样本非独立，pp 只作方向参考，不做 t 检验。

用法：
    python _bt_strongup_grade.py                       # 默认 2026 全年
    python _bt_strongup_grade.py --start 20240101     # 扩展样本
"""
from __future__ import annotations
import argparse
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import etf_mainline as em
import market_regime_v101 as v101
import strong_up_history as suh

DB = HERE.parent / "cache_daily" / "stock_data.db"
OUT_CSV = HERE / "_bt_out"
OUT_REPORT = HERE.parent / "report_daily"

ap = argparse.ArgumentParser()
ap.add_argument("--start", default="20260101")
ap.add_argument("--end", default="20261103")
ap.add_argument("--tag", default="2026")
args = ap.parse_args()

cfg = v101.load_config()
gcfg = cfg["strong_uptrend"]["money_effect"]["strong_up_continuity"]
LABEL_THR = 3.0

# ---------------------------------------------------------------- 1) 日线面板
# 多取 1 个交易日作为 T-1 的前置窗口（分级统计仍从 args.start 起）
with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as conn:
    all_dates = [r[0] for r in conn.execute(
        "select distinct trade_date from daily_cache where trade_date<=? "
        "order by trade_date", (args.end,))]
    start_pos = max(0, next((i for i, d in enumerate(all_dates)
                             if d >= args.start), 1) - 1)
    lo = all_dates[start_pos]
    df = pd.read_sql(
        "select ts_code, trade_date, pct_chg from daily_cache "
        "where trade_date between ? and ?", conn, params=(lo, args.end))
df["pct_chg"] = pd.to_numeric(df["pct_chg"], errors="coerce")
# 长窗口回测时 ts_code 转 category，显著压低内存（map 前再转回 str）
df["ts_code"] = df["ts_code"].astype("category")
frames = {d: g for d, g in df.groupby("trade_date")}
dates = sorted(frames)
print(f"日线面板：{len(dates)} 天  {dates[0]} ~ {dates[-1]}", flush=True)


def calc_strong_up() -> dict:
    """T-1 涨停名单在 T 日的上涨比例；口径与 _intraday_monitor / V10.0 完全一致。"""
    out = {}
    for i in range(1, len(dates)):
        a, b = frames.get(dates[i - 1]), frames.get(dates[i])
        if a is None or b is None:
            continue
        t = a["ts_code"].astype(str).map(suh._thr)
        p = a["pct_chg"]
        zt = set(a.loc[(p >= t * 0.99) & (p < t + 0.1), "ts_code"].astype(str))
        if not zt:
            continue
        sub = b[b["ts_code"].isin(zt)]["pct_chg"].dropna()
        if len(sub):
            out[dates[i]] = round(float((sub > 0).mean() * 100), 2)
    return out


su = calc_strong_up()

# 全市场等权日收益（截尾 ±21% 后再复合，避免新股/复权跳空污染）
mkt_daily = {d: float(g["pct_chg"].clip(-21, 21).mean() / 100.0)
             for d, g in frames.items()}

# ---------------------------------------------------------------- 2) 事实标签
panel = em.load_etf_panel()
series = em.compute_industry_series(panel, em.load_config())
idx_all = sorted(panel["trade_date"].unique())
nav_by_ind = {ind: s.set_index("trade_date")["nav"].reindex(idx_all).values
              for ind, s in series.items()}
bench = np.mean(list(nav_by_ind.values()), axis=0)
pos = {d: i for i, d in enumerate(idx_all)}


def fwd_bench(d, n):
    i = pos.get(d)
    return (bench[i + n] / bench[i] - 1) * 100 if (i is not None and i + n < len(bench) and bench[i]) else np.nan


def fwd_mkt(d, n=20):
    i = dates.index(d)
    if i + n >= len(dates):
        return np.nan
    w = dates[i + 1:i + 1 + n]
    r = 1.0
    for x in w:
        r *= (1 + mkt_daily.get(x, 0.0))
    return (r - 1) * 100


# ---------------------------------------------------------------- 3) 逐日分级
rows = []
seq_all = [(d, su.get(d)) for d in dates]
for i, d in enumerate(dates):
    if d < args.start or su.get(d) is None:
        continue
    seq = seq_all[max(0, i - 4):i + 1]          # 最近 5 个交易日，含当日，无前视
    gr = suh.classify(d, su[d], cfg, seq=seq)
    rows.append({
        "trade_date": d, "strong_up": su[d], "grade": gr["grade"],
        "streak_repair": gr["streak_repair"], "streak_boost": gr["streak_boost"],
        "fwd5": fwd_bench(d, 5), "fwd10": fwd_bench(d, 10),
        "fwd20": fwd_bench(d, 20), "fwd_mkt20": fwd_mkt(d),
    })
R = pd.DataFrame(rows)
R["label"] = (R["fwd20"] > LABEL_THR).where(R["fwd20"].notna())
V = R.dropna(subset=["fwd20"]).copy()          # 主评估集：有完整未来 20 日
print(f"分级样本 {len(R)} 天，其中有完整 fwd20 标签 {len(V)} 天", flush=True)

# ---------------------------------------------------------------- 4) 分档统计
BASE = 100 * V["label"].mean()
ORDER = ["ALERT", "BOOST", "REPAIR", "PASS", "FAIL"]
NAME = {"ALERT": "强势股负反馈警报", "BOOST": "确认短线赚钱效应增强",
        "REPAIR": "确认修复", "PASS": "通过", "FAIL": "未过"}


def stat(mask, name):
    s = V[mask]
    n = len(s)
    if n == 0:
        return dict(档位=name, n=0)
    return {
        "档位": name, "n": n,
        "占比%": round(100 * n / len(V), 1),
        "fwd5%": round(s.fwd5.mean(), 2),
        "fwd10%": round(s.fwd10.mean(), 2),
        "fwd20%": round(s.fwd20.mean(), 2),
        "fwd20中位%": round(s.fwd20.median(), 2),
        "胜率%": round(100 * (s.fwd20 > 0).mean(), 1),
        "强市率%": round(100 * s.label.mean(), 1),
        "强市提升pp": round(100 * s.label.mean() - BASE, 1),
        "mkt20%": round(s.fwd_mkt20.mean(), 2),
    }


stats = [stat(V.grade == k, f"{k} {NAME[k]}") for k in ORDER]
stats.append(stat(V.strong_up >= 50, "【对照】现行单一 ≥50% 通过"))
stats.append(stat(V.strong_up < 50, "【对照】现行单一 <50% 未过"))
stats.append(stat(pd.Series(True, index=V.index), "【基准】全体"))
S = pd.DataFrame(stats)

# ---- 分年稳健性：不同市况下信号是否同向 ----
V2 = V.copy()
V2["year"] = V2["trade_date"].astype(str).str[:4]
yr = []
for y, s in V2.groupby("year"):
    b_y = 100 * s.label.mean()
    row = {"年": y, "n": len(s), "基准强市%": round(b_y, 1),
           "全体fwd20%": round(s.fwd20.mean(), 2)}
    for k in ("ALERT", "BOOST"):
        t = s[s.grade == k]
        row[f"{k}_n"] = len(t)
        row[f"{k}_fwd20%"] = round(t.fwd20.mean(), 2) if len(t) else None
        row[f"{k}_pp"] = round(100 * t.label.mean() - b_y, 1) if len(t) else None
    yr.append(row)
Y = pd.DataFrame(yr)

# ---- ALERT 事件聚类：连续多日触发只算一次独立事件，避免重复计数夸大样本 ----
al_dates = sorted(R.loc[R.grade == "ALERT", "trade_date"].astype(str))
di_of = {d: i for i, d in enumerate(dates)}
clusters = []
if al_dates:
    cur = [di_of[al_dates[0]]]
    for d in al_dates[1:]:
        i = di_of[d]
        if i - cur[-1] <= 1:
            cur.append(i)
        else:
            clusters.append(cur)
            cur = [i]
    clusters.append(cur)
n_clusters = len(clusters)
cluster_sizes = [len(c) for c in clusters]

# ---------------------------------------------------------------- 5) 输出
OUT_CSV.mkdir(exist_ok=True)
csv_path = OUT_CSV / f"strongup_grade_backtest_{args.tag}.csv"
R.to_csv(csv_path, index=False, encoding="utf-8-sig")
S.to_csv(OUT_CSV / f"strongup_grade_summary_{args.tag}.csv",
         index=False, encoding="utf-8-sig")

print("\n" + "=" * 108)
print(f"  强股上涨比连续性分级 · 未来收益回测  [{dates[0]} ~ {dates[-1]} | 标签 {args.tag}]")
print("=" * 108)
print(f"事实标签：未来20日等权行业ETF基准 >{LABEL_THR}% 为「事后强市」，全体基准 {BASE:.1f}%")
print(f"主评估集：{len(V)} 天（未来20日窗口完整）\n")
with pd.option_context("display.width", 200, "display.max_columns", 20):
    print(S.to_string(index=False))
print("\n--- 分年稳健性（pp = 该档强市率 - 当年基准）---")
with pd.option_context("display.width", 200, "display.max_columns", 20):
    print(Y.to_string(index=False))
print(f"\nALERT 触发 {len(al_dates)} 天 → 聚为 {n_clusters} 个独立事件簇，"
      f"簇长度中位数 {int(np.median(cluster_sizes)) if cluster_sizes else 0} 天")

# 接入仓位的自动判据（写进报告，避免口头结论）
def verdict(grade):
    s = V[V.grade == grade]
    if len(s) < 15:
        return f"样本仅 {len(s)} 天，不足以支持仓位决策 → **不接入**"
    lift = 100 * s.label.mean() - BASE
    mean20 = s.fwd20.mean()
    if lift >= 8 and mean20 > 0:
        return f"强市率 {lift:+.1f}pp、fwd20 {mean20:+.2f}% → **正向，可考虑接入**"
    if lift <= -8 and mean20 < 0:
        return f"强市率 {lift:+.1f}pp、fwd20 {mean20:+.2f}% → **负向，可考虑反向约束**"
    return f"强市率 {lift:+.1f}pp、fwd20 {mean20:+.2f}% → **无显著区分度，不接入**"

L = []
L.append(f"# 强股上涨比连续性分级 · 未来收益回测（{args.tag}）\n")
L.append(f"- 样本区间：`{dates[0]} ~ {dates[-1]}`，分级 {len(R)} 天，完整 fwd20 标签 {len(V)} 天")
L.append(f"- 事实标签：未来 20 日**等权行业 ETF 基准**收益 > {LABEL_THR}%（沿用 `_bt_six_diag.py` / V10.4 调参口径），全体基准 **{BASE:.1f}%**")
L.append(f"- 辅助基准：`mkt20` = 未来 20 日全市场等权收益（日涨跌幅截尾 ±21% 后复合）")
L.append("- 防前视：分级只用 T 日及之前的 `strong_up`；收益只用 T 日之后")
L.append("- **统计 caveat**：未来 20 日窗口互相重叠，样本非独立；pp 只作方向参考，未做显著性检验\n")
L.append("## 分档结果\n")
L.append("| 档位 | n | 占比% | fwd5% | fwd10% | fwd20% | fwd20中位% | 胜率% | 强市率% | 相对基准pp | mkt20% |")
L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
for _, r in S.iterrows():
    if r.get("n", 0) == 0:
        L.append(f"| {r['档位']} | 0 | — | — | — | — | — | — | — | — | — |")
        continue
    L.append(f"| {r['档位']} | {int(r['n'])} | {r['占比%']} | {r['fwd5%']} | {r['fwd10%']} | "
             f"{r['fwd20%']} | {r['fwd20中位%']} | {r['胜率%']} | {r['强市率%']} | "
             f"{float(r['强市提升pp']):+.1f} | {r['mkt20%']} |")
L.append("\n## 分年稳健性\n")
L.append("| 年 | n | 基准强市% | 全体fwd20% | ALERT_n | ALERT_fwd20% | ALERT_pp | BOOST_n | BOOST_fwd20% | BOOST_pp |")
L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
for _, r in Y.iterrows():
    L.append(f"| {r['年']} | {int(r['n'])} | {r['基准强市%']} | {r['全体fwd20%']} | "
             f"{r['ALERT_n']} | {r['ALERT_fwd20%']} | {r['ALERT_pp']} | "
             f"{r['BOOST_n']} | {r['BOOST_fwd20%']} | {r['BOOST_pp']} |")
L.append(f"\n- ALERT 共触发 {len(al_dates)} 天，聚为 **{n_clusters} 个独立事件簇**"
         f"（簇长度中位数 {int(np.median(cluster_sizes)) if cluster_sizes else 0} 天）；"
         f"连续触发 + 20 日重叠窗口使独立样本远小于表观 n。")
L.append("\n## 是否接入仓位的判据\n")
for k in ("ALERT", "BOOST", "REPAIR"):
    L.append(f"- **{k} {NAME[k]}**：{verdict(k)}")
L.append("\n> 判据：样本 ≥15 天且强市率相对基准 ≥+8pp（或 ≤-8pp）且 fwd20 方向一致，才考虑接入；否则维持信号层。")
L.append("\n## 明细\n")
L.append(f"- 逐日面板：`{csv_path}`")
L.append(f"- 汇总表：`_bt_out/strongup_grade_summary_{args.tag}.csv`\n")

OUT_REPORT.mkdir(exist_ok=True)
rep = OUT_REPORT / f"strongup_grade_backtest_{args.tag}.md"
rep.write_text("\n".join(L), encoding="utf-8")
print(f"\n报告：{rep}")
print(f"明细：{csv_path}")
