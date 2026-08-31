# -*- coding: utf-8 -*-
"""SLI 合成数据冒烟测试（不调 API，验证全链路逻辑）。"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sli.features import (PriceFeatures, annual_moat, build_universe, compute_purity,
                          financial_snapshot, growth_acceleration, prev_period_snapshot)
from sli.scoring import build_panel
from sli.classify import (accelerator, classify_leader, industry_rank, leader_gap,
                          lifecycle, special_tags, trade_alpha)
from sli.reason import build_reasons

np.random.seed(42)

# ── 合成行业与成分 ────────────────────────────────────
classify_l3 = pd.DataFrame([
    {"index_code": "850326.SI", "industry_code": "L3001", "industry_name": "钛白粉",
     "parent_code": "L2001", "is_pub": 1},
    {"index_code": "850327.SI", "industry_code": "L3002", "industry_name": "锂电池",
     "parent_code": "L2002", "is_pub": 1},
])
# 每个行业 3 只股票
members = pd.DataFrame([
    # 钛白粉
    {"index_code": "850326.SI", "con_code": "002601.SZ", "in_date": "20180101", "out_date": np.nan},
    {"index_code": "850326.SI", "con_code": "000545.SZ", "in_date": "20180101", "out_date": np.nan},
    {"index_code": "850326.SI", "con_code": "600727.SH", "in_date": "20180101", "out_date": np.nan},
    # 锂电池
    {"index_code": "850327.SI", "con_code": "300750.SZ", "in_date": "20180101", "out_date": np.nan},
    {"index_code": "850327.SI", "con_code": "002074.SZ", "in_date": "20180101", "out_date": np.nan},
    {"index_code": "850327.SI", "con_code": "300014.SZ", "in_date": "20180101", "out_date": np.nan},
])
basic = pd.DataFrame([
    {"ts_code": "002601.SZ", "name": "龙佰集团", "market": "主板", "list_date": "20110715"},
    {"ts_code": "000545.SZ", "name": "中核钛白", "market": "主板", "list_date": "19931210"},
    {"ts_code": "600727.SH", "name": "安纳达", "market": "主板", "list_date": "19961128"},
    {"ts_code": "300750.SZ", "name": "宁德时代", "market": "创业板", "list_date": "20180611"},
    {"ts_code": "002074.SZ", "name": "国轩高科", "market": "主板", "list_date": "20070516"},
    {"ts_code": "300014.SZ", "name": "亿纬锂能", "market": "创业板", "list_date": "20091030"},
])

uni = build_universe(classify_l3, members, basic, "20260828")
print("universe OK:", len(uni), "rows")
assert len(uni) == 6, "universe should be 6"

# ── 合成日行情（160 个交易日） ─────────────────────────
n_days = 160
dates = pd.bdate_range("2026-01-02", periods=n_days).strftime("%Y%m%d").tolist()
codes = uni["ts_code"].tolist()
# 股价：龙佰高位强势，宁德成长强势，其他一般
base = {"002601.SZ": 20.0, "000545.SZ": 6.0, "600727.SH": 12.0,
        "300750.SZ": 180.0, "002074.SZ": 15.0, "300014.SZ": 30.0}
momentum = {"002601.SZ": 0.004, "000545.SZ": 0.001, "600727.SH": 0.0,
            "300750.SZ": 0.005, "002074.SZ": 0.002, "300014.SZ": 0.003}
rows = []
for i, d in enumerate(dates):
    for c in codes:
        p = base[c] * (1 + momentum[c]) ** i * (1 + np.random.normal(0, 0.01))
        rows.append({"trade_date": d, "ts_code": c, "close": round(p, 2),
                     "high": round(p * 1.02, 2), "low": round(p * 0.98, 2),
                     "vol": int(1e6 * (1 + i * 0.01)), "amount": float(p * 1e6 * (1 + i * 0.01)),
                     "pre_close": round(p / 1.004, 2), "pct_chg": 0.0})
daily = pd.DataFrame(rows)

pf = PriceFeatures(daily)
pf.prepare()
t = pf.nearest_date("20260828")
at = pf.eval_at(t)
print("PriceFeatures OK @", t, "rows:", len(at))
assert len(at) == 6

# ── 合成 daily_basic ──────────────────────────────────
db_rows = []
for i, d in enumerate(dates):
    for c in codes:
        db_rows.append({"trade_date": d, "ts_code": c,
                        "total_mv": base[c] * 10 * 1e4 * (1 + momentum[c]) ** i,
                        "circ_mv": base[c] * 8 * 1e4, "pe_ttm": 30.0, "pb": 3.0,
                        "turnover_rate": 2.0 + i * 0.01, "volume_ratio": 1.2})
daily_basic = pd.DataFrame(db_rows)

# ── 合成财务指标（5 期） ───────────────────────────────
periods = ["20251231", "20250930", "20250630", "20250331", "20241231"]
ann_dates = {"20251231": "20260420", "20250930": "20251030", "20250630": "20250828",
             "20250331": "20250429", "20241231": "20250425"}
# 龙佰：盈利稳、高毛利、成长中；宁德：高成长、加速；安纳达：低盈利
prof = {
    "002601.SZ": {"roe": 16.0, "gm": 32.0, "nm": 14.0, "or_yoy": 12.0, "pd_yoy": 15.0, "q_yoy": 13.0, "roic": 10.0, "cf": 80.0, "rd": 3.0},
    "000545.SZ": {"roe": 8.0, "gm": 22.0, "nm": 6.0, "or_yoy": 5.0, "pd_yoy": 4.0, "q_yoy": 3.0, "roic": 5.0, "cf": 50.0, "rd": 1.0},
    "600727.SH": {"roe": 5.0, "gm": 15.0, "nm": 4.0, "or_yoy": -5.0, "pd_yoy": -8.0, "q_yoy": -6.0, "roic": 3.0, "cf": 30.0, "rd": 1.0},
    "300750.SZ": {"roe": 22.0, "gm": 25.0, "nm": 12.0, "or_yoy": 35.0, "pd_yoy": 45.0, "q_yoy": 30.0, "roic": 15.0, "cf": 90.0, "rd": 6.0},
    "002074.SZ": {"roe": 10.0, "gm": 18.0, "nm": 7.0, "or_yoy": 18.0, "pd_yoy": 22.0, "q_yoy": 20.0, "roic": 7.0, "cf": 40.0, "rd": 3.0},
    "300014.SZ": {"roe": 14.0, "gm": 20.0, "nm": 9.0, "or_yoy": 25.0, "pd_yoy": 30.0, "q_yoy": 28.0, "roic": 10.0, "cf": 60.0, "rd": 4.0},
}
fina_rows = []
for p, ann in ann_dates.items():
    for c in codes:
        d = prof[c]
        # 加速度：宁德的 q_yoy 逐期上升
        if c == "300750.SZ":
            q = d["q_yoy"] + 8 * (periods.index(p) - 4)
        else:
            q = d["q_yoy"]
        fina_rows.append({"ts_code": c, "end_date": p, "ann_date": ann, "update_flag": "1",
                          "roe": d["roe"], "roic": d["roic"], "grossprofit_margin": d["gm"],
                          "netprofit_margin": d["nm"], "or_yoy": d["or_yoy"],
                          "netprofit_yoy": d["pd_yoy"], "ocf_to_profit": d["cf"],
                          "rd_exp": d["rd"], "q_profit_yoy": q,
                          "dt_netprofit_yoy": d["pd_yoy"], "roe_dt": d["roe"]})
fina = pd.DataFrame(fina_rows)

income_rows = []
for p, ann in ann_dates.items():
    for c in codes:
        rev = base[c] * 1e3 * (1.1 ** periods.index(p))
        income_rows.append({"ts_code": c, "end_date": p, "ann_date": ann, "update_flag": "1",
                            "revenue": rev, "operate_cost": rev * 0.7,
                            "total_cogs": rev * 0.7, "n_income_attr_p": rev * 0.1})
income = pd.DataFrame(income_rows)

balance_rows = []
for p, ann in ann_dates.items():
    for c in codes:
        balance_rows.append({"ts_code": c, "end_date": p, "ann_date": ann, "update_flag": "1",
                             "total_assets": base[c] * 1e4})
balance = pd.DataFrame(balance_rows)

# ── 合成主营构成 ──────────────────────────────────────
mainbz_rows = [
    {"ts_code": "002601.SZ", "end_date": "20251231", "bz_item": "钛白粉", "bz_sales": 90e6, "bz_profit": 20e6, "bz_cost": 70e6},
    {"ts_code": "002601.SZ", "end_date": "20251231", "bz_item": "海绵钛", "bz_sales": 10e6, "bz_profit": 2e6, "bz_cost": 8e6},
    {"ts_code": "000545.SZ", "end_date": "20251231", "bz_item": "钛白粉", "bz_sales": 40e6, "bz_profit": 5e6, "bz_cost": 35e6},
    {"ts_code": "600727.SH", "end_date": "20251231", "bz_item": "金红石型钛白粉", "bz_sales": 25e6, "bz_profit": 3e6, "bz_cost": 22e6},
    {"ts_code": "300750.SZ", "end_date": "20251231", "bz_item": "动力电池", "bz_sales": 300e6, "bz_profit": 40e6, "bz_cost": 260e6},
    {"ts_code": "300750.SZ", "end_date": "20251231", "bz_item": "储能电池", "bz_sales": 100e6, "bz_profit": 12e6, "bz_cost": 88e6},
    {"ts_code": "002074.SZ", "end_date": "20251231", "bz_item": "动力电池", "bz_sales": 80e6, "bz_profit": 8e6, "bz_cost": 72e6},
    {"ts_code": "300014.SZ", "end_date": "20251231", "bz_item": "锂原电池", "bz_sales": 50e6, "bz_profit": 6e6, "bz_cost": 44e6},
]
mainbz = pd.DataFrame(mainbz_rows)

# ── 时点特征 ──────────────────────────────────────────
date_T = t
snap = financial_snapshot(fina, income, balance, date_T)
prev = prev_period_snapshot(fina, date_T)
accel = growth_acceleration(fina, date_T)
purity = compute_purity(mainbz, uni, snap)
moat = annual_moat(uni, fina, date_T)
print("snapshot:", len(snap), "| purity:", len(purity), "| moat:", len(moat))

db_at = daily_basic[daily_basic["trade_date"] == date_T][["ts_code", "total_mv", "circ_mv", "pe_ttm", "pb", "turnover_rate"]]
panel = build_panel(uni, pf.eval_at(date_T), db_at, snap.merge(prev, on="ts_code", how="left"),
                    accel, purity, moat)
print("panel rows:", len(panel), "| sli non-null:", int(panel["sli"].notna().sum()))

# 历史面板（复用同一套财务，仅价格不同）
panels = {"T": panel}
for label, back in (("T20", 20), ("T60", 60), ("T120", 120)):
    from sli.utils import shift_trade_date
    d = shift_trade_date(pf.dates, date_T, back)
    panels[label] = build_panel(uni, pf.eval_at(d),
                                daily_basic[daily_basic["trade_date"] == d][["ts_code", "total_mv", "circ_mv", "pe_ttm", "pb", "turnover_rate"]],
                                snap.merge(prev, on="ts_code", how="left"), accel, purity, moat)

lc = lifecycle(panels)
panel = panel.merge(lc, on="ts_code", how="left")
panel = industry_rank(panel)
panel, gap = leader_gap(panel)
panel = classify_leader(panel)
panel = accelerator(panel)
panel = special_tags(panel)
panel = trade_alpha(panel, None)

print("\n=== 面板抽样（按SLI排序）===")
cols = ["ts_code", "name", "l3_name", "ind_rank", "sli", "scale_score", "profit_score",
        "growth_score", "purity_score", "moat_score", "market_score", "trend_score",
        "leader_type", "lifecycle", "gap_band", "LEADER_ACCELERATION", "NEXT_LEADER",
        "LEADER_EARNINGS_TURN", "trade_alpha"]
print(panel.sort_values("sli", ascending=False)[cols].round(1).to_string(index=False))

print("\n=== 行业Top1 ===")
print(gap.to_string(index=False))

reasons = build_reasons(panel)
print("\n=== LEADER_REASON（示例）===")
for _, r in reasons.head(2).iterrows():
    print(f"\n[{r['name']}] ({r['leader_type']}) SLI={r['sli']:.1f}")
    print(r["reason"])

print("\nSMOKE OK")
