# -*- coding: utf-8 -*-
"""SLI V2 合成数据冒烟测试（不调 API，验证产品层+八维评分+分类+报告全链路）。

覆盖：
- subsector_match 细分赛道归属（含 PCB刀具 产品级赛道）
- build_panel_v2 八维评分与 SLI_V2 合成
- lifecycle(col="sli_v2") + v2_pipeline（六类龙头/Dominance/NEXT_LEADER/拐点）
- report 全部 V2 输出方法
"""
import os
import sys
import tempfile

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sli.classify import lifecycle, v2_pipeline
from sli.features import (PriceFeatures, annual_moat, build_universe, compute_purity,
                          financial_snapshot, growth_acceleration, prev_period_snapshot)
from sli.reason import build_reasons_v2
from sli.report import SliReport
from sli.scoring import build_panel, build_panel_v2
from sli.subsector import (load_subsector_map, primary_subsector,
                           product_rev_growth, subsector_match)
from sli.utils import shift_trade_date

np.random.seed(7)

# ── 合成行业与成分 ────────────────────────────────────
classify_l3 = pd.DataFrame([
    {"index_code": "850326.SI", "industry_code": "L3001", "industry_name": "钛白粉",
     "parent_code": "L2001", "is_pub": 1},
    {"index_code": "850327.SI", "industry_code": "L3002", "industry_name": "锂电池",
     "parent_code": "L2002", "is_pub": 1},
    {"index_code": "850328.SI", "industry_code": "L3003", "industry_name": "印制电路板",
     "parent_code": "L2003", "is_pub": 1},
])
members = pd.DataFrame([
    # 钛白粉：龙佰/中核钛白/安纳达
    {"index_code": "850326.SI", "con_code": "002601.SZ", "in_date": "20180101", "out_date": np.nan},
    {"index_code": "850326.SI", "con_code": "000545.SZ", "in_date": "20180101", "out_date": np.nan},
    {"index_code": "850326.SI", "con_code": "600727.SH", "in_date": "20180101", "out_date": np.nan},
    # 锂电池：宁德/国轩/亿纬
    {"index_code": "850327.SI", "con_code": "300750.SZ", "in_date": "20180101", "out_date": np.nan},
    {"index_code": "850327.SI", "con_code": "002074.SZ", "in_date": "20180101", "out_date": np.nan},
    {"index_code": "850327.SI", "con_code": "300014.SZ", "in_date": "20180101", "out_date": np.nan},
    # 印制电路板：鹏鼎(PCB制造)/鼎泰高科(微型钻针)/沪电(PCB制造)
    {"index_code": "850328.SI", "con_code": "002938.SZ", "in_date": "20180101", "out_date": np.nan},
    {"index_code": "850328.SI", "con_code": "301377.SZ", "in_date": "20180101", "out_date": np.nan},
    {"index_code": "850328.SI", "con_code": "002463.SZ", "in_date": "20180101", "out_date": np.nan},
])
basic = pd.DataFrame([
    {"ts_code": "002601.SZ", "name": "龙佰集团", "market": "主板", "list_date": "20110715"},
    {"ts_code": "000545.SZ", "name": "中核钛白", "market": "主板", "list_date": "19931210"},
    {"ts_code": "600727.SH", "name": "安纳达", "market": "主板", "list_date": "19961128"},
    {"ts_code": "300750.SZ", "name": "宁德时代", "market": "创业板", "list_date": "20180611"},
    {"ts_code": "002074.SZ", "name": "国轩高科", "market": "主板", "list_date": "20070516"},
    {"ts_code": "300014.SZ", "name": "亿纬锂能", "market": "创业板", "list_date": "20091030"},
    {"ts_code": "002938.SZ", "name": "鹏鼎控股", "market": "主板", "list_date": "20180918"},
    {"ts_code": "301377.SZ", "name": "鼎泰高科", "market": "创业板", "list_date": "20221116"},
    {"ts_code": "002463.SZ", "name": "沪电股份", "market": "主板", "list_date": "20100818"},
])

uni = build_universe(classify_l3, members, basic, "20260828")
assert len(uni) == 9, "universe should be 9"

# ── 合成日行情（160 个交易日） ─────────────────────────
n_days = 160
dates = pd.bdate_range("2026-01-02", periods=n_days).strftime("%Y%m%d").tolist()
codes = uni["ts_code"].tolist()
base = {"002601.SZ": 20.0, "000545.SZ": 6.0, "600727.SH": 12.0,
        "300750.SZ": 180.0, "002074.SZ": 15.0, "300014.SZ": 30.0,
        "002938.SZ": 28.0, "301377.SZ": 22.0, "002463.SZ": 40.0}
momentum = {"002601.SZ": 0.004, "000545.SZ": 0.001, "600727.SH": 0.0,
            "300750.SZ": 0.005, "002074.SZ": 0.002, "300014.SZ": 0.003,
            "002938.SZ": 0.001, "301377.SZ": 0.004, "002463.SZ": 0.003}
rows = []
for i, d in enumerate(dates):
    for c in codes:
        p = base[c] * (1 + momentum[c]) ** i * (1 + np.random.normal(0, 0.01))
        rows.append({"trade_date": d, "ts_code": c, "close": round(p, 2),
                     "high": round(p * 1.02, 2), "low": round(p * 0.98, 2),
                     "vol": int(1e6 * (1 + i * 0.01)),
                     "amount": float(p * 1e6 * (1 + i * 0.01)),
                     "pre_close": round(p / 1.004, 2), "pct_chg": 0.0})
daily = pd.DataFrame(rows)
pf = PriceFeatures(daily)
pf.prepare()
t = pf.nearest_date("20260828")
assert t is not None

db_rows = []
for i, d in enumerate(dates):
    for c in codes:
        db_rows.append({"trade_date": d, "ts_code": c,
                        "total_mv": base[c] * 10 * 1e4 * (1 + momentum[c]) ** i,
                        "circ_mv": base[c] * 8 * 1e4, "pe_ttm": 30.0, "pb": 3.0,
                        "turnover_rate": 2.0 + i * 0.01, "volume_ratio": 1.2})
daily_basic = pd.DataFrame(db_rows)

def db_at(d):
    return daily_basic[daily_basic["trade_date"] == d][
        ["ts_code", "total_mv", "circ_mv", "pe_ttm", "pb", "turnover_rate"]]

# ── 合成财务（5 期） ───────────────────────────────────
periods = ["20251231", "20250930", "20250630", "20250331", "20241231"]
ann_dates = {"20251231": "20260420", "20250930": "20251030", "20250630": "20250828",
             "20250331": "20250429", "20241231": "20250425"}
prof = {
    "002601.SZ": {"roe": 16.0, "gm": 32.0, "nm": 14.0, "or_yoy": 12.0, "pd_yoy": 15.0, "q_yoy": 13.0, "roic": 10.0, "cf": 80.0, "rd": 3.0},
    "000545.SZ": {"roe": 8.0, "gm": 22.0, "nm": 6.0, "or_yoy": 5.0, "pd_yoy": 4.0, "q_yoy": 3.0, "roic": 5.0, "cf": 50.0, "rd": 1.0},
    "600727.SH": {"roe": 5.0, "gm": 15.0, "nm": 4.0, "or_yoy": -5.0, "pd_yoy": -8.0, "q_yoy": -6.0, "roic": 3.0, "cf": 30.0, "rd": 1.0},
    "300750.SZ": {"roe": 22.0, "gm": 25.0, "nm": 12.0, "or_yoy": 35.0, "pd_yoy": 45.0, "q_yoy": 30.0, "roic": 15.0, "cf": 90.0, "rd": 6.0},
    "002074.SZ": {"roe": 10.0, "gm": 18.0, "nm": 7.0, "or_yoy": 18.0, "pd_yoy": 22.0, "q_yoy": 20.0, "roic": 7.0, "cf": 40.0, "rd": 3.0},
    "300014.SZ": {"roe": 14.0, "gm": 20.0, "nm": 9.0, "or_yoy": 25.0, "pd_yoy": 30.0, "q_yoy": 28.0, "roic": 10.0, "cf": 60.0, "rd": 4.0},
    # 鹏鼎：规模大但成长平；鼎泰：高毛利+高成长（产品龙头候选）；沪电：中规中矩
    "002938.SZ": {"roe": 15.0, "gm": 24.0, "nm": 11.0, "or_yoy": 10.0, "pd_yoy": 12.0, "q_yoy": 11.0, "roic": 9.0, "cf": 70.0, "rd": 4.0},
    "301377.SZ": {"roe": 18.0, "gm": 45.0, "nm": 20.0, "or_yoy": 30.0, "pd_yoy": 40.0, "q_yoy": 35.0, "roic": 14.0, "cf": 85.0, "rd": 7.0},
    "002463.SZ": {"roe": 17.0, "gm": 30.0, "nm": 15.0, "or_yoy": 20.0, "pd_yoy": 25.0, "q_yoy": 24.0, "roic": 12.0, "cf": 75.0, "rd": 5.0},
}
fina_rows = []
for p, ann in ann_dates.items():
    for c in codes:
        d = prof[c]
        q = d["q_yoy"] + (8 * (periods.index(p) - 4) if c == "300750.SZ" else 0)
        fina_rows.append({"ts_code": c, "end_date": p, "ann_date": ann, "update_flag": "1",
                          "roe": d["roe"], "roic": d["roic"], "grossprofit_margin": d["gm"],
                          "netprofit_margin": d["nm"], "or_yoy": d["or_yoy"],
                          "netprofit_yoy": d["pd_yoy"], "ocf_to_profit": d["cf"],
                          "rd_exp": d["rd"], "q_profit_yoy": q,
                          "dt_netprofit_yoy": d["pd_yoy"], "roe_dt": d["roe"]})
fina = pd.DataFrame(fina_rows)

income_rows, balance_rows = [], []
for p, ann in ann_dates.items():
    for c in codes:
        rev = base[c] * 1e3 * (1.1 ** periods.index(p))
        income_rows.append({"ts_code": c, "end_date": p, "ann_date": ann, "update_flag": "1",
                            "revenue": rev, "operate_cost": rev * 0.7,
                            "total_cogs": rev * 0.7, "n_income_attr_p": rev * 0.1})
        balance_rows.append({"ts_code": c, "end_date": p, "ann_date": ann, "update_flag": "1",
                             "total_assets": base[c] * 1e4})
income, balance = pd.DataFrame(income_rows), pd.DataFrame(balance_rows)

# ── 合成主营构成（含 PCB刀具 产品级赛道） ───────────────
mainbz_rows = [
    {"ts_code": "002601.SZ", "end_date": "20251231", "bz_item": "钛白粉", "bz_sales": 90e6, "bz_profit": 20e6, "bz_cost": 70e6},
    {"ts_code": "002601.SZ", "end_date": "20251231", "bz_item": "海绵钛", "bz_sales": 10e6, "bz_profit": 2e6, "bz_cost": 8e6},
    {"ts_code": "002601.SZ", "end_date": "20241231", "bz_item": "钛白粉", "bz_sales": 80e6, "bz_profit": 17e6, "bz_cost": 63e6},
    {"ts_code": "000545.SZ", "end_date": "20251231", "bz_item": "钛白粉", "bz_sales": 40e6, "bz_profit": 5e6, "bz_cost": 35e6},
    {"ts_code": "600727.SH", "end_date": "20251231", "bz_item": "金红石型钛白粉", "bz_sales": 25e6, "bz_profit": 3e6, "bz_cost": 22e6},
    {"ts_code": "300750.SZ", "end_date": "20251231", "bz_item": "动力电池", "bz_sales": 300e6, "bz_profit": 40e6, "bz_cost": 260e6},
    {"ts_code": "300750.SZ", "end_date": "20251231", "bz_item": "储能电池", "bz_sales": 100e6, "bz_profit": 12e6, "bz_cost": 88e6},
    {"ts_code": "300750.SZ", "end_date": "20241231", "bz_item": "动力电池", "bz_sales": 250e6, "bz_profit": 33e6, "bz_cost": 217e6},
    {"ts_code": "002074.SZ", "end_date": "20251231", "bz_item": "动力电池", "bz_sales": 80e6, "bz_profit": 8e6, "bz_cost": 72e6},
    {"ts_code": "300014.SZ", "end_date": "20251231", "bz_item": "锂原电池", "bz_sales": 50e6, "bz_profit": 6e6, "bz_cost": 44e6},
    {"ts_code": "002938.SZ", "end_date": "20251231", "bz_item": "印制电路板", "bz_sales": 200e6, "bz_profit": 30e6, "bz_cost": 170e6},
    {"ts_code": "002938.SZ", "end_date": "20251231", "bz_item": "软板", "bz_sales": 120e6, "bz_profit": 16e6, "bz_cost": 104e6},
    {"ts_code": "301377.SZ", "end_date": "20251231", "bz_item": "微型钻针", "bz_sales": 40e6, "bz_profit": 15e6, "bz_cost": 25e6},
    {"ts_code": "301377.SZ", "end_date": "20251231", "bz_item": "铣刀", "bz_sales": 10e6, "bz_profit": 3e6, "bz_cost": 7e6},
    {"ts_code": "301377.SZ", "end_date": "20241231", "bz_item": "微型钻针", "bz_sales": 28e6, "bz_profit": 10e6, "bz_cost": 18e6},
    {"ts_code": "002463.SZ", "end_date": "20251231", "bz_item": "印制电路板", "bz_sales": 150e6, "bz_profit": 25e6, "bz_cost": 125e6},
    {"ts_code": "002463.SZ", "end_date": "20251231", "bz_item": "PCB钻孔", "bz_sales": 30e6, "bz_profit": 6e6, "bz_cost": 24e6},
]
mainbz = pd.DataFrame(mainbz_rows)

# ── 时点特征 + V2 产品层 ──────────────────────────────
date_T = t
snap = financial_snapshot(fina, income, balance, date_T)
prev = prev_period_snapshot(fina, date_T)
accel = growth_acceleration(fina, date_T)
purity = compute_purity(mainbz, uni, snap)
moat = annual_moat(uni, fina, date_T)

subsector_map = load_subsector_map()
subsector_df = subsector_match(uni, mainbz, subsector_map)
prod_growth = product_rev_growth(mainbz, uni, subsector_map)
print("细分赛道归属:", len(subsector_df), "行 /", subsector_df["subsector"].nunique(), "赛道")
print("赛道分布:", subsector_df.groupby("subsector")["ts_code"].nunique().to_dict())
print("产品增速代理:", len(prod_growth), "只")

assert "PCB刀具" in set(subsector_df["subsector"]), "PCB刀具赛道应被识别"
dth = subsector_df[subsector_df["ts_code"] == "301377.SZ"].sort_values("is_primary", ascending=False).iloc[0]
assert dth["subsector"] == "PCB刀具", f"鼎泰高科主赛道应为 PCB刀具，实际 {dth['subsector']}"
print("鼎泰高科主赛道:", dth["subsector"], "| 纯度 %.0f%%" % dth["subsector_purity"])

# ── 四时点面板 + build_panel_v2 ───────────────────────
panels = {}
for label, back in (("T", 0), ("T20", 20), ("T60", 60), ("T120", 120)):
    d = shift_trade_date(pf.dates, date_T, back)
    p = build_panel(uni, pf.eval_at(d), db_at(d), snap.merge(prev, on="ts_code", how="left"),
                    accel, purity, moat)
    panels[label] = build_panel_v2(p, subsector_df, prod_growth)

panel = panels["T"]
v2_cols = ["industry_score", "product_position", "profit_quality", "growth_v2",
           "purity_v2", "moat_v2", "market_v2", "trend_v2", "sli_v2"]
print("\n八维评分覆盖:", panel[v2_cols].notna().mean().round(2).to_dict())

# ── V2 生命周期 + 分类流水线 ───────────────────────────
lc = lifecycle(panels, col="sli_v2")
panel = panel.merge(lc, on="ts_code", how="left")
panel = v2_pipeline(panel, subsector_df, prod_growth)

print("\n=== V2 龙头类型 ===")
print(panel["leader_type_v2"].value_counts().to_dict())
print("\n=== V2 生命周期 ===")
print(panel["lifecycle"].value_counts().to_dict())

show = ["name", "l3_name", "subsector", "sli_v2", "product_position",
        "product_purity", "growth_v2", "leader_type_v2", "dominance",
        "NEXT_LEADER", "LEADER_CHALLENGER", "LEADER_EARNINGS_TURN", "SUPER_LEADER"]
print("\n=== 面板（按 sli_v2）===")
print(panel.sort_values("sli_v2", ascending=False)[show].round(1).to_string(index=False))

# 关键断言
lb = panel[panel["ts_code"] == "002601.SZ"].iloc[0]      # 龙佰：钛白粉第一
assert lb["subsector"] == "钛白粉"
assert lb["sub_rank"] == 1, "龙佰应为钛白粉赛道第一"
assert lb["is_ABSOLUTE_LEADER"] or lb["is_PRODUCT_LEADER"], "龙佰应为绝对/产品龙头"
dth2 = panel[panel["ts_code"] == "301377.SZ"].iloc[0]    # 鼎泰：PCB刀具产品龙头
assert dth2["subsector"] == "PCB刀具"
assert dth2["sub_rank"] == 1, "鼎泰应为 PCB刀具 赛道第一"
assert dth2["product_purity"] >= 70, "鼎泰主营纯度应高（微型钻针+铣刀 ~100%）"
nd = panel[panel["ts_code"] == "300750.SZ"].iloc[0]      # 宁德：动力电池赛道
assert nd["subsector"] == "电池电芯", f"宁德主赛道应为电池电芯，实际 {nd['subsector']}"
print("\n断言通过：龙佰(钛白粉#1) / 鼎泰高科(PCB刀具#1, 纯度%.0f%%) / 宁德(电池电芯)" % dth2["product_purity"])

# ── report 全链路 ─────────────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    rep = SliReport(tmp, os.path.join(tmp, "sli.db"))
    date = "20260828"
    rep.leaderboard_v2(panel, date, 100)
    rep.subsector_top5(panel, date)
    rep.next_leader_v2_report(panel, date)
    rep.earnings_turn_report(panel, date)
    rep.radar(panel, date)
    reasons_v2 = build_reasons_v2(panel)
    rep.reasons_v2(reasons_v2, date)
    files = sorted(os.listdir(tmp))
    print("\n报告输出:", files)
    assert len([f for f in files if f.startswith("sli_v2_")]) >= 6

r = reasons_v2[reasons_v2["ts_code"] == "002601.SZ"].iloc[0]
print("\n=== 龙佰 LEADER_REASON(V2) ===")
print(r["reason"])
r2 = reasons_v2[reasons_v2["ts_code"] == "301377.SZ"].iloc[0]
print("\n=== 鼎泰高科 LEADER_REASON(V2) ===")
print(r2["reason"])

print("\nSMOKE V2 OK")
