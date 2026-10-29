#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
RB 底部抬高中线价值筛选器 V1.0
════════════════════════════════════════════════════════════

回答三个互相独立的问题（三个评分不互相覆盖）：

  ① 股票质量（0~100）这家公司是否值得做未来 1~4 个月的中线研究？
  ② 形态质量（0~100）底部抬高形态是否真实有效、有趋势延续可能？
  ③ 买点质量（0~100）现在是否适合介入，还是应该等更好的位置？

核心原则（写死在门槛里，不靠人工判断）：
  - 公司好 ≠ 现在可以买：买点不足只能 WAIT。
  - 形态好 ≠ 基本面好：两个评分分池计算，互不加权补偿。
  - 题材热 ≠ 中线价值高：主题不进股票质量分，只做独立的催化剂展示。
  - 综合分（40/35/25）不能覆盖风险门槛：风险门槛单独判定，优先级最高。
  - 数据缺失不给中性分/满分：缺失项 0 分并计入 data_completeness，
    完整度不足直接落 FUNDAMENTAL_VERIFY。

输入（只读，不修改任何既有策略产出）：
  - RB 技术池：import rising_bottom_select 后调用其 scan()，取当日命中池
  - 中线放宽池：仅在本进程内临时替换 RB 的「不追高」阈值（距 MA60 / 距链末低点）
    再调一次 scan()，差集标记 pool_source=RB_EXT。不改 rising_bottom_select.py，
    run_all.bat 里 RB 自身的选股/邮件/落库结果完全不变。
  - 行情：cache_daily/stock_data.db → daily_cache + adj_factor_cache（前复权做形态）
  - 估值：daily_basic_cache（pe_ttm/pb/ps/dv_ttm）
  - 财务：fina_indicator_cache + cache_daily/parquet 三表（income/cashflow/balance/fina_indicator）
  - 基础信息：cache_daily/stock_basic.csv

防未来数据泄漏：
  - 财务数据只使用 ann_date <= 判断日 的报告；缺 ann_date 且映射不到的一律丢弃。
  - 摆动低点必须「已确认」（低点右侧 k 根 + 反弹确认窗口全部走完）才计入，
    确认时刻 = 低点索引 + k + confirm_lag；本模块只使用确认时刻 <= 当日 的低点。

用法：
  python rb_value_screen.py                 # 缓存里最新交易日
  python rb_value_screen.py 20261027        # 指定交易日
  python rb_value_screen.py 20261027 --top 15
  python rb_value_screen.py 20261027 --config my_weights.json   # 覆盖任意可配置项

输出：
  report_daily/rb_value_{date}.csv   全量排序表（机器可读）
  report_daily/rb_value_{date}.md    四件套报告（全量表 / 逐只分析 / A-B-C 名单 / A 组风险收益）

模型验证状态：**UNPROVEN**（本版本只做规则化打分与人工可核验的结构计算，
未完成严格样本外验证，权重为工程默认值，不构成任何预测能力声明）。
"""
import os
import sys
import json
import glob
import sqlite3
from datetime import datetime

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.environ.get("MSTOCK_CACHE", r"D:\mystock\cache_daily")
DB_PATH = os.path.join(CACHE_DIR, "stock_data.db")
BASIC_PATH = os.path.join(CACHE_DIR, "stock_basic.csv")
PARQUET_DIR = os.path.join(CACHE_DIR, "parquet")
REPORT_DIR = os.path.join(BASE_DIR, "report_daily")

VERSION = "rb_value_v1.0"
MODEL_STATUS = "UNPROVEN"

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════
# 配置区（全部可配置，可用 --config xxx.json 覆盖任意键）
# ═══════════════════════════════════════════════════════════════════════

# 股票质量权重（合计 100）
W_STOCK = {
    "revenue": 15,      # A1 营收质量
    "profit": 20,       # A2 利润质量
    "cashflow": 15,     # A3 现金流质量
    "safety": 10,       # A4 财务安全性
    "industry": 20,     # A5 行业与商业模式
    "valuation": 20,    # A6 估值与预期
}
# 形态质量权重（合计 100）
W_PATTERN = {
    "bottom": 25,       # B2 有效底部抬高质量
    "volume": 20,       # B3 回踩及成交量质量
    "ma": 20,           # B4 MA20/MA60 趋势
    "structure": 20,    # B5 前高及平台结构
    "liquidity": 15,    # 流动性与波动风险
}
# 买点质量权重（合计 100）
W_ENTRY = {
    "position": 20,     # C1 当前价格位置
    "support": 20,      # C2 支撑有效性
    "confirm": 25,      # C3 回踩/突破确认
    "rr": 25,           # C4 风险收益比
    "liquidity": 10,    # 交易流动性
}
# 综合分权重（不得覆盖风险门槛）
W_TOTAL = {"stock": 0.40, "pattern": 0.35, "entry": 0.25}

# 底部识别参数（B1）：全部配置化，不散落在代码里
P_PATTERN = {
    "pivot_k": 5,             # 摆动低点：前后各 k 根的最低
    "confirm_lag": 5,         # 低点确认滞后（未来 k+confirm_lag 根走完才算确认）
    "min_gap": 10,            # 相邻有效底部的最小间隔（交易日），过滤重复低点
    "min_rebound": 0.06,      # 低点后最低反弹幅度（6%），排除无有效反弹的微小波动
    "chain_tol": 0.97,        # 递升链容差：新低点 >= 前低 ×0.97 视为抬高
    "max_bottoms": 5,         # 只取最近 5 个有效底部做抬高质量评估
    "window": 180,            # 形态观察窗口（交易日）
    "platform_lookback": 60,  # 平台上沿的观察窗口
    "platform_skip": 10,      # 平台计算时剔除最近 N 根（避免把当日算进平台）
    "min_platform_days": 20,  # 有效平台的最少整理天数
    "breakout_pct": 0.5,      # 有效突破幅度（%）
    "breakout_volr": 1.20,    # 突破量能倍数（当日量 / 20 日均量）
    "retest_tol": 0.03,       # 突破后回踩到平台上沿的容差
    "extreme_move": 15.0,     # 单日涨跌超过该值视为极端波动（结构 D）
    "flat_range": 0.06,       # 最近底部价差 < 6% 视为同一震荡区间（结构 E）
    "atr_bars": 20,           # ATR 窗口
    "invalidation_atr": 1.0,  # 失效价 = 最近有效底部 - 1.0×ATR（按真实结构，非固定百分比）
    "invalidation_floor": 0.93,  # 失效价最低不超过底部 ×0.93
    "stop_buffer_low": 0.02,  # 结构止损至少留 2%（避免贴脸止损）
    "min_bars": 250,          # 上市满一年才参与
}

# 硬门槛（优先级高于综合分）
GATES = {
    "min_bars_listed": 250,        # 上市满 250 个交易日
    "min_amt20": 3.0e4,            # 20 日均成交额下限（千元）：3000 万
    "completeness_min": 0.60,      # 基本面数据完整度低于此值 → FUNDAMENTAL_VERIFY
    "completeness_buy": 0.75,      # 允许 BUY 的最低完整度
    "pe_pct_danger": 0.90,         # PE 历史分位高于 90% → VALUATION_RISK
    "pb_pct_danger": 0.85,         # 亏损股 PB 分位高于 85% → VALUATION_RISK
    "peg_danger": 3.0,             # PEG > 3 → VALUATION_RISK
    "loss_streak_years": 2,        # 连续亏损年数达到 → 风险标签
    "debt_danger": 80.0,           # 资产负债率高于 → 风险标签
    "pattern_weak": 55.0,          # 形态质量低于 → PATTERN_WEAK
    "quality_min": 60.0,           # 股票质量低于 → FUNDAMENTAL_VERIFY（不构成中线候选）
    "buy_stock": 65.0,             # BUY 的股票质量下限
    "buy_pattern": 65.0,           # BUY 的形态质量下限
    "buy_entry": 65.0,             # BUY 的买点质量下限
    "buy_rr": 2.0,                 # BUY 的最低风险收益比
    "a_group_stock": 60.0,         # A 组股票质量下限
    "a_group_total": 60.0,         # A 组综合分下限
    "c_group_quality": 45.0,       # 股票质量低于此值直接落 C 组（暂不优先）
}
# 中线放宽口径（只在本模块进程内临时替换 rising_bottom_select 的模块常量，
# 不改其源码、不写库，因此 run_all.bat 里 RB 自身的选股/邮件/落库结果完全不变）
# 理由：RB 的「不追高」是短线口径（距 MA60 <=15%、距链末低点 <=20%），
# 中线视角能接受更高的延伸，但必须把「已加速」作为风险写进报告。
RB_RELAX = {
    "enabled": True,
    "max_dist_ma60_pct": 30.0,    # RB 默认 15.0
    "max_ext_from_low_pct": 40.0,  # RB 默认 20.0
    "last_low_age_max": 60,        # RB 默认 40
}
# 估值分位样本要求
VAL_MIN_SAMPLES = 60
VAL_LOOKBACK_DAYS = 1000

# 主题相关度（只做展示，不进股票质量分）
THEME_REL = ("A_主营直接受益", "B_产业链间接受益", "C_仅概念关联", "D_缺少可靠证据")


# ═══════════════════════════════════════════════════════════════════════
# 小工具
# ═══════════════════════════════════════════════════════════════════════

def load_config(path):
    """可选 JSON 覆盖：任意键（W_STOCK/W_PATTERN/W_ENTRY/W_TOTAL/P_PATTERN/GATES）深层合并。"""
    if not path:
        return
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    for key, target in (("W_STOCK", W_STOCK), ("W_PATTERN", W_PATTERN), ("W_ENTRY", W_ENTRY),
                        ("W_TOTAL", W_TOTAL), ("P_PATTERN", P_PATTERN), ("GATES", GATES),
                        ("RB_RELAX", RB_RELAX)):
        if isinstance(cfg.get(key), dict):
            target.update(cfg[key])
    print("[CFG] 已加载覆盖配置：%s" % path)


def clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def fnum(v, d=1, sign=False):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "—"
    return ("%+.*f" if sign else "%.*f") % (d, v)


def scale(v, lo, hi, pts):
    """把 v 从 [lo, hi] 线性映射到 [0, pts]，超界截断。方向：v 越大分越高。"""
    if v is None:
        return 0.0
    if hi == lo:
        return 0.0
    return clamp((v - lo) / (hi - lo)) * pts


def rev_scale(v, lo, hi, pts):
    """v 越小分越高（lo 得满分，hi 得 0 分）。"""
    if v is None:
        return 0.0
    return clamp((hi - v) / (hi - lo)) * pts


def safe_div(a, b):
    try:
        if b is None or b == 0 or not np.isfinite(b):
            return None
        return a / b
    except (TypeError, ZeroDivisionError):
        return None


# ═══════════════════════════════════════════════════════════════════════
# 数据读取
# ═══════════════════════════════════════════════════════════════════════

def resolve_trade_date(conn, arg=None):
    if arg:
        return str(arg)
    row = conn.execute("SELECT MAX(trade_date) FROM daily_cache").fetchone()
    return str(row[0]) if row and row[0] else datetime.now().strftime("%Y%m%d")


def load_stock_basic():
    out = {}
    if not os.path.exists(BASIC_PATH):
        return out
    b = pd.read_csv(BASIC_PATH, dtype={"ts_code": str})
    for _, r in b.iterrows():
        out[r["ts_code"]] = (str(r.get("name") or ""), str(r.get("industry") or ""),
                             str(r.get("list_date") or ""))
    return out


def load_bars_for(conn, codes, end_date, days=420):
    """只取候选池的日线（含复权因子），返回 {ts_code: DataFrame}（前复权价格）。

    前复权口径与 rising_bottom_select._qfq 一致：按每股序列末端因子统一缩放，
    保证最新一根的复权价 == 原始价，支撑/目标价可直接当交易价使用。
    """
    if not codes:
        return {}
    start = (pd.to_datetime(end_date) - pd.Timedelta(days=int(days * 1.6))).strftime("%Y%m%d")
    marks = ",".join("?" * len(codes))
    q = ("SELECT d.ts_code, d.trade_date, d.open, d.high, d.low, d.close, d.pct_chg, "
         "d.vol, d.amount, a.adj_factor "
         "FROM daily_cache AS d LEFT JOIN adj_factor_cache AS a "
         "ON a.ts_code = d.ts_code AND a.trade_date = d.trade_date "
         "WHERE d.ts_code IN (%s) AND d.trade_date >= ? AND d.trade_date <= ? "
         "ORDER BY d.ts_code, d.trade_date" % marks)
    df = pd.read_sql_query(q, conn, params=list(codes) + [start, end_date])
    if df.empty:
        return {}
    for c in ("open", "high", "low", "close", "pct_chg", "vol", "amount", "adj_factor"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["close", "high", "low", "vol"])

    out = {}
    for code, g in df.groupby("ts_code", sort=False):
        g = g.sort_values("trade_date").reset_index(drop=True)
        if "adj_factor" in g.columns and g["adj_factor"].notna().any():
            adj = g["adj_factor"].ffill().bfill()
            last = adj.iloc[-1]
            if last and last > 0:
                for c in ("open", "high", "low", "close"):
                    g[c] = g[c].astype(float) * adj / last
        g.attrs["raw_close"] = float(g["close"].iloc[-1])
        out[code] = g
    return out


def load_fina_indicator(conn, codes, trade_date):
    """财务指标（只取 ann_date <= 判断日），按 end_date 升序。"""
    if not codes:
        return {}
    marks = ",".join("?" * len(codes))
    q = ("SELECT ts_code, ann_date, end_date, grossprofit_margin, netprofit_yoy, or_yoy, "
         "ocf_to_or, roe, debt_to_assets FROM fina_indicator_cache "
         "WHERE ts_code IN (%s) AND ann_date <= ?" % marks)
    df = pd.read_sql_query(q, conn, params=list(codes) + [trade_date])
    if df.empty:
        return {}
    for c in ("grossprofit_margin", "netprofit_yoy", "or_yoy", "ocf_to_or", "roe", "debt_to_assets"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.drop_duplicates(subset=["ts_code", "end_date"], keep="last")
    return {code: g.sort_values("end_date").reset_index(drop=True)
            for code, g in df.groupby("ts_code", sort=False)}


def _read_pq(paths, keep):
    frames = []
    for p in paths:
        try:
            d = pd.read_parquet(p)
        except Exception:
            continue
        cols = [c for c in keep if c in d.columns]
        if "end_date" not in cols:
            continue
        frames.append(d[cols])
    if not frames:
        return None
    d = pd.concat(frames, ignore_index=True)
    d = d.drop_duplicates(subset=[c for c in ("end_date", "report_type") if c in d.columns],
                          keep="first")
    return d


def _pq_paths(prefix, code):
    pats = [os.path.join(PARQUET_DIR, "%s_%s_*.parquet" % (prefix, code)),
            os.path.join(PARQUET_DIR, "%s_%s_*.parquet" % (prefix, code.replace(".", "_")))]
    out = []
    for p in pats:
        out += glob.glob(p)
    return sorted(set(out))


def load_financials(code, trade_date, ann_map):
    """读单只股票三表，做两件事：
    ① 丢弃没有 ann_date 或 ann_date > 判断日 的报告（防未来数据泄漏）；
    ② 统一成「累计值」口径的季报序列（income 的 revenue/n_income_attr_p 为累计）。
    ann_map: {end_date: ann_date}（来自 fina_indicator / balance / cashflow，用于补 income 缺公告日）
    """
    res = {"income": None, "cashflow": None, "balance": None, "deduct": None, "gaps": []}

    inc = _read_pq(_pq_paths("income", code),
                   ["ts_code", "ann_date", "end_date", "report_type", "revenue",
                    "n_income_attr_p", "n_income", "rd_exp", "total_revenue"])
    if inc is not None and len(inc):
        inc["end_date"] = inc["end_date"].astype(str)
        if "ann_date" not in inc.columns:
            inc["ann_date"] = np.nan
        inc["ann_date"] = inc["ann_date"].astype("object").where(inc["ann_date"].notna(),
                                                                inc["end_date"].map(ann_map))
        inc = inc[inc["ann_date"].notna()]
        inc["ann_date"] = inc["ann_date"].astype(str)
        inc = inc[inc["ann_date"] <= str(trade_date)]
        if "report_type" in inc.columns:
            inc = inc[pd.to_numeric(inc["report_type"], errors="coerce").fillna(1) == 1]
        inc = inc.drop_duplicates(subset=["end_date"], keep="last").sort_values("end_date")
        if len(inc):
            res["income"] = inc.reset_index(drop=True)
        else:
            res["gaps"].append("利润表：无可用公告日的报告")
    else:
        res["gaps"].append("利润表缺失")

    cf = _read_pq(_pq_paths("cashflow", code),
                  ["ts_code", "ann_date", "end_date", "n_cashflow_act"])
    if cf is not None and len(cf):
        cf["end_date"] = cf["end_date"].astype(str)
        cf = cf[cf["ann_date"].astype(str) <= str(trade_date)]
        cf = cf.drop_duplicates(subset=["end_date"], keep="last").sort_values("end_date")
        if len(cf):
            res["cashflow"] = cf.reset_index(drop=True)
        else:
            res["gaps"].append("现金流量表：无可用公告日的报告")
    else:
        res["gaps"].append("现金流量表缺失")

    bs = _read_pq(_pq_paths("balance_v14", code),
                  ["ts_code", "ann_date", "end_date", "total_assets", "inventories",
                   "accounts_receiv", "goodwill", "total_hldr_eqy_exc_min_int"])
    if bs is not None and len(bs):
        bs["end_date"] = bs["end_date"].astype(str)
        if "ann_date" in bs.columns:
            bs = bs[bs["ann_date"].astype(str) <= str(trade_date)]
        bs = bs.drop_duplicates(subset=["end_date"], keep="last").sort_values("end_date")
        if len(bs):
            res["balance"] = bs.reset_index(drop=True)
        else:
            res["gaps"].append("资产负债表：无可用公告日的报告")
    else:
        res["gaps"].append("资产负债表缺失")

    dq = _read_pq(_pq_paths("fina_indicator", code), ["ts_code", "end_date", "ann_date", "profit_dedt"])
    # 部分 fina_indicator 文件缺 profit_dedt / ann_date 列 → 视为无扣非数据（不猜测补齐）
    if dq is not None and len(dq) and "profit_dedt" in dq.columns:
        dq["end_date"] = dq["end_date"].astype(str)
        if "ann_date" in dq.columns:
            dq = dq[dq["ann_date"].astype(str) <= str(trade_date)]
        dq = dq.dropna(subset=["profit_dedt"])
        dq = dq.drop_duplicates(subset=["end_date"], keep="last")
        if len(dq):
            res["deduct"] = dq[["end_date", "profit_dedt"]]
    return res


def load_valuation(conn, code, trade_date):
    """当日估值快照 + 历史分位（PE/PB/股息率）。"""
    out = {"pe_ttm": None, "pb": None, "ps": None, "dv_ttm": None, "total_mv": None,
           "pe_pct": None, "pb_pct": None, "dv_pct": None, "val_gap": None}
    try:
        r = conn.execute(
            "SELECT pe_ttm, pb, ps, dv_ttm, total_mv FROM daily_basic_cache "
            "WHERE ts_code=? AND trade_date=?", (code, trade_date)).fetchone()
    except Exception:
        r = None
    if r:
        out.update(pe_ttm=r[0], pb=r[1], ps=r[2], dv_ttm=r[3], total_mv=r[4])

    start = (pd.to_datetime(trade_date) - pd.Timedelta(days=VAL_LOOKBACK_DAYS)).strftime("%Y%m%d")
    try:
        h = pd.read_sql_query(
            "SELECT pe_ttm, pb, dv_ttm FROM daily_basic_cache "
            "WHERE ts_code=? AND trade_date>=? AND trade_date<=?",
            conn, params=(code, start, trade_date))
    except Exception:
        h = None
    if h is not None and len(h):
        for col, key in (("pe_ttm", "pe_pct"), ("pb", "pb_pct"), ("dv_ttm", "dv_pct")):
            s = pd.to_numeric(h[col], errors="coerce").dropna()
            s = s[s > 0]
            cur = out.get(col)
            if len(s) >= VAL_MIN_SAMPLES and cur is not None and cur > 0:
                out[key] = float((s < cur).mean())
    if out["pe_ttm"] is None and out["pb"] is None:
        out["val_gap"] = "估值数据缺失（估值待核验）"
    return out


# ═══════════════════════════════════════════════════════════════════════
# 财务序列加工（累计 → 单季）
# ═══════════════════════════════════════════════════════════════════════

def _single_quarter(df, cols):
    """累计值 → 单季值：Q1 直接取，Q2/Q3/Q4 减去上一期；同财年内做差，跨年重置。"""
    if df is None or df.empty:
        return df
    d = df.copy()
    d["_y"] = d["end_date"].str[:4]
    d["_m"] = d["end_date"].str[4:6]
    for c in cols:
        if c not in d.columns:
            continue
        d[c] = pd.to_numeric(d[c], errors="coerce")
    vals = {c: [] for c in cols if c in d.columns}
    prev = {}
    for _, row in d.iterrows():
        for c in vals:
            v = row[c]
            pv = prev.get((c, row["_y"]))
            if pd.isna(v):
                vals[c].append(np.nan)
            elif row["_m"] == "0331" or pv is None or pd.isna(pv):
                vals[c].append(v)
            else:
                vals[c].append(v - pv)
            if not pd.isna(v):
                prev[(c, row["_y"])] = v
    for c in vals:
        d[c + "_q"] = vals[c]
    return d


def _yoy(cur, prev):
    if cur is None or prev is None or not np.isfinite(cur) or not np.isfinite(prev) or prev == 0:
        return None
    return (cur / abs(prev) - 1) * 100


def build_fundamentals(fin, fi, val, info):
    """把原始表整理成基本面分析所需的字段 + 可用性标记。"""
    d = {"gaps": list(fin.get("gaps") or []), "avail": {}}
    inc = fin.get("income")
    cf = fin.get("cashflow")
    bs = fin.get("balance")
    dq = fin.get("deduct")

    # ── 合并扣非 ──
    if inc is not None and dq is not None and len(dq):
        inc = inc.merge(dq, on="end_date", how="left")
        fin["income"] = inc

    # ── 单季序列 ──
    incq = None
    if inc is not None and len(inc):
        incq = _single_quarter(inc, ["revenue", "n_income_attr_p"])
        d["avail"]["income"] = True
        d["last_report"] = str(inc["end_date"].iloc[-1])
        d["last_ann"] = str(inc["ann_date"].iloc[-1])
    else:
        d["avail"]["income"] = False
        d["last_report"] = None
        d["last_ann"] = None

    # ── A1 营收 ──
    d.update(rev_fy_yoy=None, rev_last_yoy=None, rev_q=[], rev_trend=None, rev_yoy_seq=[])
    if incq is not None and len(incq):
        q = incq.dropna(subset=["revenue_q"])
        if len(q):
            d["rev_q"] = [float(x) for x in q["revenue_q"].iloc[-6:]]
            # 最近一期同比（单季）
            last = q.iloc[-1]
            same = q[(q["_m"] == last["_m"]) & (q["_y"] == str(int(last["_y"]) - 1))]
            if len(same):
                d["rev_last_yoy"] = _yoy(last["revenue_q"], same["revenue_q"].iloc[-1])
            # 最近完整财年（1231）同比
            fy = inc[inc["end_date"].str.endswith("1231")]
            if len(fy) >= 2:
                a = pd.to_numeric(fy["revenue"], errors="coerce").iloc[-1]
                b = pd.to_numeric(fy["revenue"], errors="coerce").iloc[-2]
                d["rev_fy_yoy"] = _yoy(a, b)
            # 近三个可比报告期同比序列（同一季度口径）
            seq = []
            for m in ("0331", "0630", "0930", "1231"):
                sub = q[q["_m"] == m]
                if len(sub) >= 2:
                    seq.append(_yoy(sub["revenue_q"].iloc[-1], sub["revenue_q"].iloc[-2]))
            d["rev_yoy_seq"] = [x for x in seq if x is not None]
            # 近四季营收的线性斜率（相对均值），判断趋势方向
            if len(d["rev_q"]) >= 4:
                y = np.array(d["rev_q"][-4:], dtype=float)
                if y.mean() > 0:
                    x = np.arange(len(y))
                    k = np.polyfit(x, y / y.mean(), 1)[0]
                    d["rev_trend"] = float(k)

    # ── A2 利润 ──
    d.update(np_yoy=None, np_q=[], deduct_yoy=None, gm_now=None, gm_prev=None,
             gm_chg=None, nm_now=None, nm_prev=None, nm_chg=None, loss_years=0, np_flags=[])
    if incq is not None and len(incq):
        q = incq.dropna(subset=["n_income_attr_p"])
        if len(q):
            d["np_q"] = [float(x) for x in q["n_income_attr_p"].iloc[-6:]]
            last = q.iloc[-1]
            same = q[(q["_m"] == last["_m"]) & (q["_y"] == str(int(last["_y"]) - 1))]
            if len(same):
                d["np_yoy"] = _yoy(last["n_income_attr_p"], same["n_income_attr_p"].iloc[-1])
            # 扣非同比
            if "profit_dedt" in incq.columns:
                dd = incq.dropna(subset=["profit_dedt"])
                if len(dd):
                    l2 = dd.iloc[-1]
                    s2 = dd[(dd["_m"] == l2["_m"]) & (dd["_y"] == str(int(l2["_y"]) - 1))]
                    if len(s2):
                        d["deduct_yoy"] = _yoy(l2["profit_dedt"], s2["profit_dedt"].iloc[-1])
            # 连续亏损年数（按年度 1231 归母）
            fy = inc[inc["end_date"].str.endswith("1231")]
            npv = pd.to_numeric(fy["n_income_attr_p"], errors="coerce").dropna()
            streak = 0
            for v in reversed(list(npv.values)):
                if v < 0:
                    streak += 1
                else:
                    break
            d["loss_years"] = streak
        # 净利率（归母 / 营收，累计口径最新一期）
        lastrow = inc.iloc[-1]
        rev = pd.to_numeric(pd.Series([lastrow.get("revenue")]), errors="coerce").iloc[0]
        npa = pd.to_numeric(pd.Series([lastrow.get("n_income_attr_p")]), errors="coerce").iloc[0]
        if rev and rev > 0 and np.isfinite(npa):
            d["nm_now"] = float(npa / rev * 100)
        prevrow = inc.iloc[:-1]
        if len(prevrow):
            r2 = pd.to_numeric(pd.Series([prevrow.iloc[-1].get("revenue")]), errors="coerce").iloc[0]
            n2 = pd.to_numeric(pd.Series([prevrow.iloc[-1].get("n_income_attr_p")]), errors="coerce").iloc[0]
            if r2 and r2 > 0 and np.isfinite(n2):
                d["nm_prev"] = float(n2 / r2 * 100)
        if d["nm_now"] is not None and d["nm_prev"] is not None:
            d["nm_chg"] = d["nm_now"] - d["nm_prev"]

    if fi is not None and len(fi):
        gm = pd.to_numeric(fi["grossprofit_margin"], errors="coerce").dropna()
        if len(gm):
            d["gm_now"] = float(gm.iloc[-1])
            if len(gm) >= 2:
                d["gm_prev"] = float(gm.iloc[-2])
                d["gm_chg"] = d["gm_now"] - d["gm_prev"]
        m = pd.to_numeric(fi["netprofit_yoy"], errors="coerce").dropna()
        if len(m) and d["np_yoy"] is None:
            d["np_yoy"] = float(m.iloc[-1])

    # 利润质量标记
    if d["np_yoy"] is not None and d["rev_last_yoy"] is not None:
        if d["rev_last_yoy"] > 0 and d["np_yoy"] < 0:
            d["np_flags"].append("收入增利润降")
    if d["np_yoy"] is not None and d["deduct_yoy"] is not None and d["np_yoy"] > 0 and d["deduct_yoy"] < 0:
        d["np_flags"].append("利润增长主要来自非经常性损益")
    if d["np_q"] and len(d["np_q"]) >= 2 and d["np_q"][-2] is not None and d["np_q"][-1] is not None:
        if d["np_q"][-2] < 0 <= d["np_q"][-1]:
            d["np_flags"].append("扭亏为盈")
    if d["loss_years"] >= GATES["loss_streak_years"]:
        d["np_flags"].append("连续%d年亏损" % d["loss_years"])
    # 最近报告期是否亏损（不因底部抬高自动视为基本面反转）
    d["cur_loss"] = bool(d["np_q"] and d["np_q"][-1] is not None and d["np_q"][-1] < 0)
    if d["cur_loss"]:
        d["np_flags"].append("最近报告期亏损")
    if d["np_yoy"] is not None and d["np_yoy"] <= -50:
        d["np_flags"].append("利润大幅下滑")
    if d["np_q"] and len([x for x in d["np_q"] if x is not None]) >= 4:
        arr = np.array([x for x in d["np_q"] if x is not None], dtype=float)
        if np.mean(np.abs(arr)) > 0:
            cv = float(np.std(arr) / max(np.mean(np.abs(arr)), 1e-9))
            if cv > 1.0:
                d["np_flags"].append("利润大幅波动")

    # ── A3 现金流 ──
    d.update(ocf_fy=None, ocf_np_ratio=None, ocf_yoy=None, ocf_neg=False,
             ar_yoy=None, inv_chg=None)
    if cf is not None and len(cf):
        cfq = _single_quarter(cf, ["n_cashflow_act"])
        fy = cf[cf["end_date"].str.endswith("1231")]
        ocf = pd.to_numeric(fy["n_cashflow_act"], errors="coerce").dropna()
        if len(ocf):
            d["ocf_fy"] = float(ocf.iloc[-1])
            d["ocf_neg"] = bool(ocf.iloc[-1] < 0)
            if len(ocf) >= 2:
                d["ocf_yoy"] = _yoy(ocf.iloc[-1], ocf.iloc[-2])
        if inc is not None and len(inc):
            nf = inc[inc["end_date"].str.endswith("1231")]
            if len(nf) and len(ocf):
                n1 = pd.to_numeric(nf["n_income_attr_p"], errors="coerce").dropna()
                if len(n1):
                    d["ocf_np_ratio"] = safe_div(float(ocf.iloc[-1]), float(n1.iloc[-1]))
        # 单季 OCF 连续为负判定
        qc = cfq.dropna(subset=["n_cashflow_act_q"]) if cfq is not None else None
        if qc is not None and len(qc) >= 2:
            d["ocf_neg_streak"] = bool(qc["n_cashflow_act_q"].iloc[-1] < 0 and qc["n_cashflow_act_q"].iloc[-2] < 0)
    if bs is not None and len(bs):
        ar = pd.to_numeric(bs["accounts_receiv"], errors="coerce").dropna()
        if len(ar) >= 5:
            d["ar_yoy"] = _yoy(ar.iloc[-1], ar.iloc[-5])
        iv = pd.to_numeric(bs["inventories"], errors="coerce").dropna()
        if len(iv) >= 5 and iv.iloc[-5]:
            d["inv_chg"] = (iv.iloc[-1] / iv.iloc[-5] - 1) * 100

    # ── A4 财务安全 ──
    d.update(debt_ratio=None, goodwill_ratio=None, roe=None)
    if fi is not None and len(fi):
        db = pd.to_numeric(fi["debt_to_assets"], errors="coerce").dropna()
        if len(db):
            d["debt_ratio"] = float(db.iloc[-1])
        ro = pd.to_numeric(fi["roe"], errors="coerce").dropna()
        if len(ro):
            d["roe"] = float(ro.iloc[-1])
    if bs is not None and len(bs):
        ta = pd.to_numeric(bs["total_assets"], errors="coerce").dropna()
        gw = pd.to_numeric(bs["goodwill"], errors="coerce").dropna()
        if len(ta) and len(gw) and ta.iloc[-1]:
            d["goodwill_ratio"] = float(gw.iloc[-1] / ta.iloc[-1] * 100)

    # ── A5 需要的研发强度 ──
    d["rd_ratio"] = None
    if inc is not None and len(inc) and "rd_exp" in inc.columns:
        last = inc.iloc[-1]
        rd = pd.to_numeric(pd.Series([last.get("rd_exp")]), errors="coerce").iloc[0]
        rv = pd.to_numeric(pd.Series([last.get("revenue")]), errors="coerce").iloc[0]
        if np.isfinite(rd) and rv and rv > 0:
            d["rd_ratio"] = float(rd / rv * 100)

    # ── A6 估值 ──
    d.update(pe=val.get("pe_ttm"), pb=val.get("pb"), ps=val.get("ps"),
             dv=val.get("dv_ttm"), pe_pct=val.get("pe_pct"), pb_pct=val.get("pb_pct"),
             dv_pct=val.get("dv_pct"), total_mv=val.get("total_mv"),
             val_gap=val.get("val_gap"))

    d["industry"] = info[1] if info else ""
    d["name"] = info[0] if info else ""
    d["list_date"] = info[2] if info else ""
    return d


# ═══════════════════════════════════════════════════════════════════════
# 行业分类（A5）：9 类，各自用不同的判断逻辑
# ═══════════════════════════════════════════════════════════════════════

_INDUSTRY_RULES = [
    ("高成长科技", r"半导体|芯片|软件|计算机|通信|电子|元件|光学|IT|互联网|云|人工智能|"
                 r"消费电子|集成电路|信息安全|数据中心|算力"),
    ("专业设备及工业制造", r"机械|设备|专用|通用|电气|自动化|仪器|仪表|工程机械|工业|"
                          r"汽车|零部件|军工|航空|航天|船舶|金属制品|电机"),
    ("医疗器械及医药", r"医疗|医药|生物|制药|器械|中药|化学制药|原料药|疫苗|CXO"),
    ("消费品牌", r"食品|饮料|酒|家电|家居|纺织|服装|商贸|零售|旅游|酒店|餐饮|"
                 r"医美|化妆品|农牧|养殖|种植|农业"),
    ("周期资源", r"煤炭|石油|化工|钢铁|有色|铜|铝|黄金|稀土|水泥|建材|玻璃|"
                 r"化纤|橡胶|塑料|造纸|矿业|采掘|能源"),
    ("金融及高股息", r"银行|保险|证券|多元金融|信托|公用事业|电力|水务|燃气|"
                     r"高速公路|港口|铁路|机场"),
    ("房地产及相关产业链", r"房地产|地产|建筑|装饰|装修|物业|园区开发|房屋建设"),
    ("传媒游戏及主题驱动", r"传媒|影视|游戏|广告|出版|教育|文化|娱乐|数字媒体"),
]


def classify_industry(industry):
    s = str(industry or "")
    for name, pat in _INDUSTRY_RULES:
        import re as _re
        if _re.search(pat, s):
            return name
    return "其他"


# ═══════════════════════════════════════════════════════════════════════
# A. 股票质量评分
# ═══════════════════════════════════════════════════════════════════════

def score_revenue(d):
    """A1 营收质量（15）。缺失项 0 分并计入完整度。"""
    pts, avail, notes = 0.0, 0, []
    if d["rev_fy_yoy"] is not None:
        avail += 1
        pts += scale(d["rev_fy_yoy"], -20, 30, 5)
        notes.append("上年营收%+.1f%%" % d["rev_fy_yoy"])
    if d["rev_last_yoy"] is not None:
        avail += 1
        pts += scale(d["rev_last_yoy"], -20, 30, 5)
        notes.append("最新单季营收%+.1f%%" % d["rev_last_yoy"])
    if d["rev_trend"] is not None:
        avail += 1
        pts += scale(d["rev_trend"], -0.05, 0.08, 3)
        notes.append("近四季斜率%+.3f" % d["rev_trend"])
    if d["rev_yoy_seq"]:
        avail += 1
        seq = d["rev_yoy_seq"][-3:]
        accel = (seq[-1] - seq[0]) if len(seq) >= 2 else 0.0
        pts += scale(accel, -20, 25, 2)
    if avail == 0:
        return 0.0, 0, "营收：无可用报告", 0.0
    # 趋势定性（不用单季同比定生死）
    if d["rev_trend"] is not None and d["rev_trend"] > 0.02 and (d["rev_last_yoy"] or 0) > 0:
        trend = "持续增长"
    elif d["rev_yoy_seq"] and len(d["rev_yoy_seq"]) >= 2 and d["rev_yoy_seq"][-1] > d["rev_yoy_seq"][-2]:
        trend = "增长改善"
    elif d["rev_trend"] is not None and d["rev_trend"] < -0.02:
        trend = "增长放缓"
    elif d["rev_last_yoy"] is not None and d["rev_last_yoy"] < -10:
        trend = "持续下滑"
    else:
        trend = "基本稳定"
    return min(pts, float(W_STOCK["revenue"])), avail, "%s（%s）" % (trend, "；".join(notes[:3])), \
        avail / 4.0


def score_profit(d):
    """A2 利润质量（20）。亏损公司不因底部抬高自动视为反转。"""
    pts, avail, notes = 0.0, 0, []
    if d["np_yoy"] is not None:
        avail += 1
        pts += scale(d["np_yoy"], -50, 60, 6)
        notes.append("归母%+.1f%%" % d["np_yoy"])
    if d["deduct_yoy"] is not None:
        avail += 1
        pts += scale(d["deduct_yoy"], -50, 60, 5)
        notes.append("扣非%+.1f%%" % d["deduct_yoy"])
    if d["gm_chg"] is not None:
        avail += 1
        pts += scale(d["gm_chg"], -5, 5, 3)
        notes.append("毛利率%.1f%%(%+.1f)" % (d["gm_now"], d["gm_chg"]))
    if d["nm_chg"] is not None:
        avail += 1
        pts += scale(d["nm_chg"], -5, 5, 3)
        notes.append("净利率%.1f%%(%+.1f)" % (d["nm_now"], d["nm_chg"]))
    if avail == 0:
        return 0.0, 0, "利润：无可用报告", 0.0

    penalty = 0.0
    if "收入增利润降" in d["np_flags"]:
        penalty += 4
    if "利润增长主要来自非经常性损益" in d["np_flags"]:
        penalty += 3
    if "扭亏为盈" in d["np_flags"]:
        pts += 2
    if "利润大幅波动" in d["np_flags"]:
        penalty += 3
    if d["loss_years"] >= GATES["loss_streak_years"]:
        pts, penalty = min(pts, 4.0), 0.0
    elif d.get("cur_loss"):
        # 最近报告期仍亏损：增长/周转再好也不给高利润分，
        # 亏损不会因为股价底部抬高就被视为基本面反转（需求 A2）
        pts, penalty = min(pts, 8.0), 0.0
    if d["np_yoy"] is not None and d["np_yoy"] <= -50:
        pts = min(pts, 6.0)
    pts = max(pts - penalty, 0.0)
    tag = "；".join(d["np_flags"]) if d["np_flags"] else "利润结构正常"
    return min(pts, float(W_STOCK["profit"])), avail, "%s（%s）" % (tag, "；".join(notes[:3])), \
        avail / 4.0


def score_cashflow(d):
    """A3 现金流质量（15）。OCF/净利润匹配度只作辅助，结合行业解释。"""
    pts, avail, notes = 0.0, 0, []
    r = d["ocf_np_ratio"]
    if r is not None and r > -5 and r < 10:
        avail += 1
        if r >= 0.8:
            pts += 6 if r <= 1.6 else 4.5
        elif r >= 0.5:
            pts += 4
        elif r >= 0:
            pts += 2
        else:
            pts += 0.5
        notes.append("OCF/净利%.2f" % r)
    elif d["ocf_fy"] is not None:
        avail += 1
        pts += 4 if d["ocf_fy"] > 0 else 0
        notes.append("年度OCF%s" % ("为正" if d["ocf_fy"] > 0 else "为负"))
    if d["ocf_yoy"] is not None:
        avail += 1
        pts += scale(d["ocf_yoy"], -50, 50, 4)
        notes.append("OCF同比%+.0f%%" % d["ocf_yoy"])
    if d["ar_yoy"] is not None and d["rev_last_yoy"] is not None:
        avail += 1
        gap = d["ar_yoy"] - d["rev_last_yoy"]
        pts += rev_scale(gap, -5, 40, 3)
        if gap > 25:
            notes.append("应收增速显著快于营收")
    if d["inv_chg"] is not None:
        avail += 1
        pts += rev_scale(d["inv_chg"], -10, 60, 2)
    if avail == 0:
        return 0.0, 0, "现金流：无可用报告", 0.0
    if d.get("ocf_neg_streak"):
        pts = max(pts - 3, 0.0)
        notes.append("近两季经营现金流均为负")
    return min(pts, float(W_STOCK["cashflow"])), avail, "；".join(notes[:3]) or "现金流数据不足", \
        avail / 5.0


def score_safety(d):
    """A4 财务安全性（10）。货币资金/有息负债未缓存 → 记为缺口，不给分。"""
    pts, avail, notes = 0.0, 0, []
    if d["debt_ratio"] is not None:
        avail += 1
        pts += rev_scale(d["debt_ratio"], 25, 85, 5)
        notes.append("资产负债率%.1f%%" % d["debt_ratio"])
    if d["goodwill_ratio"] is not None:
        avail += 1
        pts += rev_scale(d["goodwill_ratio"], 1, 30, 3)
        notes.append("商誉/总资产%.1f%%" % d["goodwill_ratio"])
    # 短期偿债（货币资金 / 有息负债）：数据未缓存 → 0 分 + 缺口
    notes.append("货币资金/有息负债未缓存")
    if avail == 0:
        return 0.0, 0, "财务安全：无可用数据", 0.0
    return min(pts, float(W_STOCK["safety"])), avail, "；".join(notes), avail / 3.0


def score_industry(d):
    """A5 行业与商业模式（20）。9 类不同逻辑，不统一套成长股标准。"""
    cat = classify_industry(d["industry"])
    rev = d["rev_last_yoy"] or d["rev_fy_yoy"]
    gm, gmc = d["gm_now"], d["gm_chg"]
    roe = d["roe"]
    rd = d["rd_ratio"]
    ocf_ok = (d["ocf_np_ratio"] is not None and d["ocf_np_ratio"] >= 0.7) or (d["ocf_fy"] or 0) > 0
    pts, avail, notes = 0.0, 0, []

    def add(v, a=1):
        nonlocal pts, avail
        if v is None:
            return
        pts += v
        avail += a

    if cat == "高成长科技":
        add(scale(rev, -20, 40, 5) if rev is not None else None)
        add(scale(rd, 1, 12, 5) if rd is not None else None)
        add(scale(gmc, -4, 5, 5) if gmc is not None else None)
        add(4.0 if ocf_ok else 1.0)
        notes.append("科技：看收入兑现+研发强度+毛利")
    elif cat == "专业设备及工业制造":
        add(scale(rev, -20, 40, 5) if rev is not None else None)
        add(scale(gm, 8, 40, 5) if gm is not None else None)
        add(scale(roe, 0, 20, 5) if roe is not None else None)
        add(4.0 if ocf_ok else 1.0)
        notes.append("设备制造：看收入+毛利水平+ROE+回款")
    elif cat == "医疗器械及医药":
        add(scale(gm, 20, 80, 6) if gm is not None else None)
        add(scale(rev, -20, 35, 6) if rev is not None else None)
        add(scale(d["np_yoy"], -40, 50, 4) if d["np_yoy"] is not None else None)
        add(3.0 if ocf_ok else 1.0)
        notes.append("医药：看产品毛利+销售增长+利润+现金流")
    elif cat == "消费品牌":
        add(scale(gm, 10, 60, 6) if gm is not None else None)
        add(scale(rev, -15, 30, 5) if rev is not None else None)
        add(scale(roe, 3, 25, 4) if roe is not None else None)
        add(3.0 if ocf_ok else 1.0)
        notes.append("消费：看品牌毛利+渠道增长+现金流")
    elif cat == "周期资源":
        # 周期股：毛利率从低位回升、ROE 由负转正才是关键，不追求高增速
        add(scale(gmc, -2, 8, 7) if gmc is not None else None)
        add(scale(roe, -5, 20, 5) if roe is not None else None)
        add(4.0 if ocf_ok else 1.0)
        add(scale(rev, -30, 30, 3) if rev is not None else None)
        notes.append("周期：看毛利率回升+ROE 位置，不看高增速")
    elif cat == "金融及高股息":
        add(scale(roe, 5, 18, 8) if roe is not None else None)
        add(scale(d["dv"] or 0, 0.5, 5, 5))
        add(scale(d["debt_ratio"], 30, 95, 4) if d["debt_ratio"] is not None else None)
        add(scale(rev, -10, 20, 3) if rev is not None else None)
        notes.append("金融/公用：看 ROE+股息+资本结构")
    elif cat == "房地产及相关产业链":
        add(rev_scale(d["debt_ratio"], 40, 90, 8) if d["debt_ratio"] is not None else None)
        add(6.0 if ocf_ok else 0.5)
        add(scale(rev, -40, 20, 3) if rev is not None else None)
        add(rev_scale(d["goodwill_ratio"], 1, 20, 3) if d["goodwill_ratio"] is not None else None)
        notes.append("地产链：先看负债与现金流，再看收入")
    elif cat == "传媒游戏及主题驱动":
        add(scale(rev, -25, 40, 6) if rev is not None else None)
        add(rev_scale(d["goodwill_ratio"], 2, 35, 6) if d["goodwill_ratio"] is not None else None)
        add(scale(d["np_yoy"], -50, 60, 4) if d["np_yoy"] is not None else None)
        add(3.0 if ocf_ok else 1.0)
        notes.append("传媒：高商誉+业绩波动，重点看减值风险")
    else:
        add(scale(rev, -20, 35, 5) if rev is not None else None)
        add(scale(d["np_yoy"], -40, 50, 5) if d["np_yoy"] is not None else None)
        add(scale(roe, 0, 20, 5) if roe is not None else None)
        add(4.0 if ocf_ok else 1.0)
        notes.append("其他：营收+利润+ROE+现金流综合")

    if avail == 0:
        return 0.0, cat, "行业：指标全缺", 0.0
    return min(pts, float(W_STOCK["industry"])), cat, "；".join(notes), avail / 4.0


def score_valuation(d):
    """A6 估值与预期（20）。亏损股不用 PE；缺失标「估值待核验」，不编目标价。"""
    pts, avail, notes = 0.0, 0, []
    loss = (d["np_yoy"] is not None and (d["np_q"] or [1])[-1] is not None
            and (d["np_q"] or [1])[-1] < 0) or d["loss_years"] > 0
    pe, pb, ps, dv = d["pe"], d["pb"], d["ps"], d["dv"]
    pe_pct, pb_pct = d["pe_pct"], d["pb_pct"]

    if pe and pe > 0 and not loss:
        if pe_pct is not None:
            avail += 1
            pts += rev_scale(pe_pct, 0.05, 0.95, 8)
            notes.append("PE %.1f（历史分位%.0f%%）" % (pe, pe_pct * 100))
        else:
            avail += 1
            pts += 4.0
            notes.append("PE %.1f（历史分位不足）" % pe)
    elif loss:
        notes.append("亏损，PE 不适用")
    if pb and pb > 0:
        avail += 1
        pts += rev_scale(pb_pct, 0.05, 0.95, 5) if pb_pct is not None else 2.5
        notes.append("PB %.2f%s" % (pb, "（分位%.0f%%）" % (pb_pct * 100) if pb_pct is not None else ""))
    if ps and ps > 0:
        avail += 1
        pts += rev_scale(min(ps, 20), 1, 20, 2)
    # PEG：只对盈利且增速为正的公司有意义
    if pe and pe > 0 and d["np_yoy"] and d["np_yoy"] > 5 and not loss:
        avail += 1
        peg = pe / d["np_yoy"]
        pts += rev_scale(peg, 0.5, 3.0, 5)
        notes.append("PEG %.2f" % peg)
    if dv and dv > 0:
        avail += 1
        pts += scale(dv, 0.5, 5, 2)
        notes.append("股息率%.2f%%" % dv)
    if avail == 0:
        return 0.0, "估值待核验", 0.0
    return min(pts, float(W_STOCK["valuation"])), "；".join(notes[:4]), avail / 5.0


# ═══════════════════════════════════════════════════════════════════════
# B. 形态质量
# ═══════════════════════════════════════════════════════════════════════

def find_swing_lows(g, p=None):
    """B1 有效底部识别（可重复、无未来函数）。

    条件：low[i] 为前后各 k 根最低；低点之后 confirm_lag 根内最高价相对低点
    反弹 >= min_rebound；低点须在 i+k+confirm_lag <= 最后一根 时才算「已确认」。
    相邻有效低点间隔 < min_gap 的视为重复低点，保留更低的那个。
    """
    p = p or P_PATTERN
    k, lag, gap = p["pivot_k"], p["confirm_lag"], p["min_gap"]
    low = g["low"].to_numpy(dtype=float)
    high = g["high"].to_numpy(dtype=float)
    n = len(low)
    cands = []
    for i in range(k, n - k):
        if low[i] > low[i - k:i + k + 1].min() + 1e-9:
            continue
        confirm = i + k + lag
        if confirm > n - 1:          # 尚未确认，不能用（防未来函数）
            continue
        seg = high[i + 1:min(n, confirm + 1)]
        reb = (seg.max() / low[i] - 1) if len(seg) and low[i] > 0 else 0.0
        if reb < p["min_rebound"]:
            continue
        cands.append({"idx": i, "price": float(low[i]), "rebound": float(reb),
                      "confirm_idx": confirm, "date": str(g["trade_date"].iloc[i])})
    out = []
    for c in cands:
        if out and c["idx"] - out[-1]["idx"] < gap:
            if c["price"] < out[-1]["price"]:
                out[-1] = c
            continue
        out.append(c)
    return out


def rising_chain(lows, tol):
    """递升链：新低点 >= 前低 ×tol 视为抬高，破位则重新起算。"""
    chain = []
    for c in lows:
        if not chain or c["price"] >= chain[-1]["price"] * tol:
            chain.append(c)
        else:
            chain = [c]
    return chain


def analyze_pattern(g, p=None):
    """B2~B6：底部抬高、成交量、均线、前高平台、失效位。"""
    p = p or P_PATTERN
    n = len(g)
    m = {"ok": False, "gaps": []}
    if n < p["min_bars"]:
        m["gaps"].append("上市不足 %d 根" % p["min_bars"])
        return m

    w = g.iloc[-p["window"]:].reset_index(drop=True)
    close = w["close"].to_numpy(dtype=float)
    high = w["high"].to_numpy(dtype=float)
    low = w["low"].to_numpy(dtype=float)
    vol = w["vol"].to_numpy(dtype=float)
    amt = w["amount"].to_numpy(dtype=float)
    px = float(close[-1])
    m["ok"] = True
    m["close"] = px
    m["n"] = n

    # ── 均线 ──
    c_all = g["close"].to_numpy(dtype=float)
    ma20 = float(np.mean(c_all[-20:]))
    ma60 = float(np.mean(c_all[-60:])) if n >= 60 else None
    ma20_20 = float(np.mean(c_all[-40:-20])) if n >= 40 else None
    ma60_20 = float(np.mean(c_all[-80:-20])) if n >= 80 else None
    m["ma20"], m["ma60"] = ma20, ma60
    m["ma20_slope"] = (ma20 / ma20_20 - 1) * 100 if ma20_20 else None
    m["ma60_slope"] = (ma60 / ma60_20 - 1) * 100 if (ma60 and ma60_20) else None
    m["ma_gap"] = (ma20 / ma60 - 1) * 100 if ma60 else None
    m["bias20"] = (px / ma20 - 1) * 100 if ma20 else None
    m["dist_ma60"] = (px / ma60 - 1) * 100 if ma60 else None

    # ── ATR ──
    tr = np.maximum(high[1:] - low[1:],
                    np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])))
    atr = float(np.mean(tr[-p["atr_bars"]:])) if len(tr) >= 5 else float(np.mean(high - low))
    m["atr"], m["atr_pct"] = atr, (atr / px * 100 if px else None)

    # ── B1/B2 有效底部与抬高 ──
    lows = find_swing_lows(w, p)
    chain = rising_chain(lows, p["chain_tol"])[-p["max_bottoms"]:]
    m["bottom_count"] = len(chain)
    m["bottom_rise_pct"] = ((chain[-1]["price"] / chain[0]["price"] - 1) * 100) if len(chain) >= 2 else 0.0
    m["bottoms"] = [{"date": c["date"], "price": round(c["price"], 2),
                     "rebound": round(c["rebound"] * 100, 1)} for c in chain]
    rises, intervals, rebounds = [], [], []
    for a, b in zip(chain, chain[1:]):
        rises.append((b["price"] / a["price"] - 1) * 100)
        intervals.append(b["idx"] - a["idx"])
        seg = high[a["idx"]:b["idx"] + 1]
        rebounds.append((seg.max() / a["price"] - 1) * 100 if len(seg) else 0.0)
    m["rises"], m["intervals"], m["rebounds"] = rises, intervals, rebounds
    m["last_low"] = chain[-1]["price"] if chain else None
    m["last_low_date"] = chain[-1]["date"] if chain else None
    m["last_low_age"] = (n - 1 - (len(g) - len(w) + chain[-1]["idx"])) if chain else None

    # 平台 / 前高（B5）
    pl = min(p["platform_lookback"], len(w))
    seg_hi = high[-pl:-p["platform_skip"]] if len(w) > p["platform_skip"] + 10 else high[:-p["platform_skip"]]
    m["platform_high"] = float(np.max(seg_hi)) if len(seg_hi) else None
    m["swing_high"] = float(np.max(high[-p["platform_lookback"]:])) if len(w) else None
    m["platform_days"] = int(pl - p["platform_skip"])
    m["dist_platform"] = ((px / m["platform_high"] - 1) * 100) if m["platform_high"] else None

    # 结构分类（A/B/C/D/E）
    m["structure"], m["structure_desc"] = classify_structure(m, w, p)

    # ── B3 成交量 ──
    v5 = float(np.mean(vol[-5:]))
    v20 = float(np.mean(vol[-20:])) if len(vol) >= 20 else v5
    v_pre = float(np.mean(vol[-25:-5])) if len(vol) >= 25 else v20
    m["vol5"], m["vol20"] = v5, v20
    m["shrink_ratio"] = v5 / v20 if v20 else None
    m["pullback_shrink"] = v5 / v_pre if v_pre else None
    up_mask = np.diff(close) > 0
    ret = np.diff(close)
    volp = vol[1:]
    upv = float(np.mean(volp[up_mask])) if up_mask.any() else None
    dnv = float(np.mean(volp[~up_mask])) if (~up_mask).any() else None
    m["up_dn_vol"] = (upv / dnv) if (upv and dnv) else None
    # 异常放量后回落
    bad = 0
    for i in range(max(1, len(vol) - 10), len(vol)):
        base = float(np.mean(vol[max(0, i - 20):i])) if i >= 5 else None
        if base and vol[i] >= 2.0 * base and (ret[i - 1] * 100 if i - 1 < len(ret) else 0) <= -3:
            bad += 1
    m["vol_spike_bad"] = bad
    m["amt20"] = float(np.mean(amt[-20:])) if len(amt) >= 20 else float(np.mean(amt))
    m["amt5"] = float(np.mean(amt[-5:]))

    # ── B5 突破状态 ──
    # 突破必须「真的发生过」：最近 20 根里出现过收盘站在平台上沿之上，
    # 不能只因为最近 15 日的高点高于「剔除最近 10 根后的平台」就认定突破过 ——
    # 那是正常的上冲回落，不是突破失败。
    ph = m["platform_high"]
    brk_win = min(20, len(w))
    broke_idx = None
    if ph:
        for j in range(len(w) - brk_win, len(w)):
            if close[j] > ph * (1 + p["breakout_pct"] / 100):
                broke_idx = j
                break
    m["broke_out"] = broke_idx is not None
    m["breakout"] = bool(broke_idx is not None and px > ph * (1 + p["breakout_pct"] / 100)
                         and (m["shrink_ratio"] or 0) >= p["breakout_volr"])
    m["retest"] = False
    if ph and broke_idx is not None and px >= ph:
        recent = w.iloc[max(0, broke_idx - 3):]
        if (recent["low"] <= ph * (1 + p["retest_tol"])).any():
            m["retest"] = True
    m["failed_breakout"] = bool(ph and broke_idx is not None and px < ph * 0.97)

    # ── B6 支撑与失效 ──
    support = m["last_low"] if m["last_low"] else (ma60 if ma60 else px * 0.9)
    inv = max(support - p["invalidation_atr"] * atr, support * p["invalidation_floor"])
    inv = min(inv, support * (1 - p["stop_buffer_low"]))
    m["support_price"] = round(float(support), 2)
    m["invalidation_price"] = round(float(inv), 2)
    m["invalidated"] = px < m["invalidation_price"]

    # ── 形态阶段 ──
    m["state"] = classify_state(m, p)
    return m


def classify_structure(m, w, p):
    """B2 结构分类：A 连续抬高回撤有序 / B 反弹越来越弱 / C 无法突破前高 / D 极端波动 / E 同一震荡区间。"""
    chain, rises, rebounds = m["bottoms"], m["rises"], m["rebounds"]
    if len(chain) < 3:
        return "E", "有效底部不足 3 个，尚不构成抬高结构"
    prices = [c["price"] for c in chain]
    spread = (max(prices) - min(prices)) / min(prices)
    if spread < p["flat_range"] and m["bottom_rise_pct"] < 8:
        return "E", "最近 %d 个低点价差仅 %.1f%%，实为同一震荡区间" % (len(prices), spread * 100)
    # 极端波动：单日振幅 > extreme_move，或底部附近出现暴涨暴跌
    rng = ((w["high"] - w["low"]) / w["low"].shift(1) * 100).dropna()
    if len(rng) and float((rng > p["extreme_move"]).sum()) >= 3:
        return "D", "近半年出现 %d 次单日振幅 >%.0f%%，低点抬高可能由极端波动造成" \
            % (int((rng > p["extreme_move"]).sum()), p["extreme_move"])
    hi = w["high"].to_numpy(dtype=float)
    no_hh = True
    for i in range(1, len(chain)):
        a, b = chain[i - 1], chain[i]
        ia = [j for j, d in enumerate(w["trade_date"].astype(str)) if d == a["date"]]
        ib = [j for j, d in enumerate(w["trade_date"].astype(str)) if d == b["date"]]
        if ia and ib and hi[ib[0]:].max() > hi[ia[0]:ib[0] + 1].max() * 1.005:
            no_hh = False
            break
    if no_hh:
        return "C", "底部抬高但每次反弹都未突破前高，仍在上方套牢区下方"
    if len(rebounds) >= 2 and rebounds[-1] < rebounds[0] * 0.6:
        return "B", "底部仍在抬高，但反弹高度由 %.1f%% 衰减到 %.1f%%" % (rebounds[0], rebounds[-1])
    if rises and all(r > 0 for r in rises) and len(m["intervals"]) >= 2:
        iv = np.array(m["intervals"], dtype=float)
        cv = float(np.std(iv) / max(np.mean(iv), 1e-9))
        if cv < 0.6:
            return "A", "连续 %d 次抬高、回撤有序（间隔 %s）" % (
                len(chain), "/".join(str(x) for x in m["intervals"]))
    return "A", "底部抬高，节奏一般"


def classify_state(m, p):
    ph = m.get("platform_high")
    if m.get("invalidated"):
        return "跌破失效位"
    if m.get("failed_breakout"):
        return "突破失败"
    if m.get("retest"):
        return "突破后回踩确认"
    if m.get("breakout"):
        return "已经有效突破"
    if ph and m.get("dist_platform") is not None and 0 <= m["dist_platform"] <= 3 \
            and m.get("platform_days", 0) >= p["min_platform_days"]:
        return "平台突破临界点"
    if m.get("bottom_count", 0) >= 3 and m.get("bottom_rise_pct", 0) > 3:
        return "底部抬高但未突破"
    return "尚在底部构筑"


def score_pattern(m):
    """B 形态质量（100）：5 项加权，底部数量不做线性映射。"""
    p = P_PATTERN
    det = {}
    if not m.get("ok") or m["state"] == "跌破失效位":
        return 0.0, {"bottom": 0, "volume": 0, "ma": 0, "structure": 0, "liquidity": 0}, "形态失效"

    # 1) 底部抬高（25）
    s = 0.0
    if m["bottom_count"] >= 2:
        s += scale(m["bottom_rise_pct"], 3, 60, 10)              # 抬高幅度
        pos = sum(1 for r in m["rises"] if r > 0)
        s += (pos / max(len(m["rises"]), 1)) * 6                  # 抬高的持续性（比例，不按次数）
        if len(m["rebounds"]) >= 2:
            s += 4 if m["rebounds"][-1] >= m["rebounds"][0] * 0.8 else 1.5   # 反弹未明显衰减
        s += {"A": 5, "B": 2.5, "C": 1, "D": 0, "E": 0}.get(m["structure"], 0)
    det["bottom"] = round(min(s, float(W_PATTERN["bottom"])), 1)

    # 2) 成交量（20）
    s = 0.0
    ud = m["up_dn_vol"]
    if ud is not None:
        s += scale(ud, 0.85, 1.30, 8)
    sh = m["shrink_ratio"]
    if sh is not None:
        s += 5 if 0.5 <= sh <= 1.5 else 2      # 近期量能不过度放大/衰减
    if m.get("breakout") or m.get("retest"):
        s += 4
    if m["vol_spike_bad"]:
        s -= 3
    det["volume"] = round(max(0.0, min(s, float(W_PATTERN["volume"]))), 1)

    # 3) 均线（20）
    s = 0.0
    s += scale(m["ma20_slope"], 0, 8, 7) if m["ma20_slope"] is not None else 0
    s += scale(m["ma60_slope"], -2, 5, 5) if m["ma60_slope"] is not None else 0
    s += 4 if (m["ma_gap"] or 0) > 0 else 1
    s += rev_scale(abs(m["bias20"] or 0), 0, 12, 4)     # 乖离不宜过大
    det["ma"] = round(min(s, float(W_PATTERN["ma"])), 1)

    # 4) 前高与平台（20）
    s = 0.0
    dp = m.get("dist_platform")
    if dp is not None:
        if dp >= 0:
            s += scale(dp, 0, 8, 8)                   # 突破幅度越大越好（但不过度）
        else:
            s += rev_scale(abs(dp), 0, 25, 6)         # 距前高越近越好
    s += {"突破后回踩确认": 8, "已经有效突破": 7, "平台突破临界点": 6,
          "底部抬高但未突破": 3, "尚在底部构筑": 1, "突破失败": 0}.get(m["state"], 0)
    if m.get("platform_days", 0) < p["min_platform_days"]:
        s = s * 0.85
    det["structure"] = round(min(s, float(W_PATTERN["structure"])), 1)

    # 5) 流动性与波动（15）
    s = 0.0
    s += scale(m["amt20"], 3e4, 5e5, 8)               # 日均成交额（千元）
    atr_pct = m["atr_pct"] or 5
    s += rev_scale(atr_pct, 2, 10, 7)                 # 波动越小越稳
    det["liquidity"] = round(min(s, float(W_PATTERN["liquidity"])), 1)

    total = sum(det.values())
    return round(total, 1), det, m["state"]


# ═══════════════════════════════════════════════════════════════════════
# C. 买点质量
# ═══════════════════════════════════════════════════════════════════════

def analyze_entry(m):
    """C1/C2/C3 三类买点 + C4 风险收益比。所有价格都按真实结构算。"""
    px = m["close"]
    sup = m["support_price"]
    inv = m["invalidation_price"]
    ph = m.get("platform_high")
    atr = m.get("atr") or px * 0.03
    e = {"setup": "NONE", "setup_desc": "无明确买点", "entry_low": None, "entry_high": None,
         "target": None, "rr": None, "confirm": 0.0}

    # C3 突破后回踩 → 二次启动
    if m["state"] == "突破后回踩确认":
        e.update(setup="REBREAKOUT", setup_desc="等待二次启动",
                 entry_low=round(max(ph, px - 0.5 * atr), 2), entry_high=round(px + 0.5 * atr, 2),
                 confirm=23.0)
        # 目标只取结构量度：平台上沿 + 平台高度（平台下沿用最近有效底部近似）。
        # 不用「现价 + N×风险距离」反推目标——那会让 RR 恒等于 N，等于自证。
        if ph and sup and ph > sup:
            e["target"] = round(ph + (ph - sup), 2)
    # C1 缩量回踩
    elif (m["state"] in ("底部抬高但未突破", "尚在底部构筑", "平台突破临界点")
          and m["ma20_slope"] is not None and m["ma20_slope"] > 0
          and abs(m["bias20"] or 99) <= 5
          and (m["shrink_ratio"] or 9) <= 1.3
          and px > sup):
        e.update(setup="PULLBACK", setup_desc="等待回踩确认",
                 entry_low=round(max(sup * 1.00, m["ma20"] - 0.5 * atr), 2),
                 entry_high=round(max(sup * 1.02, m["ma20"] + 0.3 * atr), 2),
                 confirm=18.0 if (m["shrink_ratio"] or 9) <= 1.0 else 13.0)
        e["target"] = round(ph, 2) if ph and ph > px else None
    # C2 平台突破
    if e["setup"] == "NONE" and ph and m.get("dist_platform") is not None \
            and -3 <= m["dist_platform"] <= 3 \
            and m.get("platform_days", 0) >= P_PATTERN["min_platform_days"]:
        e.update(setup="BREAKOUT", setup_desc="等待放量突破",
                 entry_low=round(ph * 1.001, 2), entry_high=round(ph * 1.02, 2), confirm=12.0)
        plat_h = (ph - sup) if ph > sup else atr * 4
        e["target"] = round(ph + plat_h, 2)
    if e["setup"] == "NONE" and m["state"] == "已经有效突破":
        e.update(setup="BREAKOUT_DONE", setup_desc="已突破，等回踩不追",
                 entry_low=round(max(ph, px - atr), 2), entry_high=round(px, 2), confirm=6.0)
        # 同上：只用量度升幅，不反推
        if ph and sup and ph > sup:
            e["target"] = round(ph + (ph - sup), 2)

    # C4 风险收益比
    if e["entry_low"] and e["target"] and e["target"] > e["entry_low"]:
        entry = (e["entry_low"] + e["entry_high"]) / 2
        risk = entry - inv
        e["risk"] = round(risk, 2)
        e["reward"] = round(e["target"] - entry, 2)
        e["rr"] = round((e["target"] - entry) / risk, 2) if risk > 0 else None
        if e["rr"] is None or e["rr"] < 1.0:
            e["target"] = None
            e["rr"] = None
            e["target_gap"] = "目标位待确认（潜在收益不足以覆盖风险）"
    else:
        e["target"] = None
        e["target_gap"] = "目标位待确认"
    return e


def score_entry(m, e):
    """C 买点质量（100）。没有明确买点时不得给高分。"""
    det = {}
    px, sup, inv = m["close"], m["support_price"], m["invalidation_price"]
    atr = m.get("atr") or px * 0.03

    # 当前位置（20）：距最近底部与 MA60 的距离，越贴近支撑越好
    ext = (px / sup - 1) * 100 if sup else 0
    s = rev_scale(abs(ext), 0, 25, 12)
    s += rev_scale(abs(m["dist_ma60"] if "dist_ma60" in m else (px / m["ma60"] - 1) * 100 if m["ma60"] else 10),
                   0, 20, 8)
    det["position"] = round(min(s, float(W_ENTRY["position"])), 1)

    # 支撑有效性（20）：价格距失效位越远越安全，但也不能离支撑太远
    dist_sup = (px - sup) / atr if atr else 0
    s = rev_scale(abs(dist_sup - 1.0), 0, 3, 20)
    det["support"] = round(s, 1)

    # 回踩/突破确认（25）
    det["confirm"] = round(min(e["confirm"], float(W_ENTRY["confirm"])), 1)

    # 风险收益比（25）
    rr = e.get("rr")
    if rr is None:
        det["rr"] = 4.0
    elif rr >= 3:
        det["rr"] = 25.0
    elif rr >= 2:
        det["rr"] = 18.0
    elif rr >= 1.5:
        det["rr"] = 11.0
    else:
        det["rr"] = 3.0

    # 交易流动性（10）
    det["liquidity"] = round(scale(m["amt20"], 3e4, 3e5, 10), 1)

    total = sum(det.values())
    return round(total, 1), det


# ═══════════════════════════════════════════════════════════════════════
# 主题（只展示，不进股票质量分）
# ═══════════════════════════════════════════════════════════════════════

def theme_relation(h, d):
    """主题与主营相关度（A/B/C/D）。仅作展示；C/D 不得提高基本面评分。"""
    theme = h.get("theme") or ""
    ind = d.get("industry") or ""
    if not theme:
        return "D_缺少可靠证据", 0.0
    if ind and (ind in theme or theme in ind):
        return "A_主营直接受益", 1.0
    if h.get("theme_cnt", 0) >= 3:
        return "B_产业链间接受益", 0.7
    return "C_仅概念关联", 0.3


# ═══════════════════════════════════════════════════════════════════════
# 决策：主标签 + 交易状态（风险门槛优先于综合分）
# ═══════════════════════════════════════════════════════════════════════

def decide(row):
    gates = []
    # ① 排除类风险
    if row["_is_st"]:
        gates.append("ST/退市风险")
    if row["_bars"] < GATES["min_bars_listed"]:
        gates.append("上市不足 %d 个交易日" % GATES["min_bars_listed"])
    if row["_amt20"] < GATES["min_amt20"]:
        gates.append("流动性不足（20日均成交额 %.0f 万）" % (row["_amt20"] / 10))
    if row["_loss_years"] >= GATES["loss_streak_years"] and row["_ocf_neg"]:
        gates.append("连续亏损且经营现金流为负")
    if row["_debt"] is not None and row["_debt"] > GATES["debt_danger"] and row["_ocf_neg"]:
        gates.append("高负债叠加经营现金流为负")
    # 硬剔除 = 不可交易 / 重大财务风险（连续亏损且现金流为负、高负债叠加现金流为负）
    hard_exclude = [g for g in gates
                    if g in ("ST/退市风险",)
                    or g.startswith(("上市不足", "流动性不足", "连续亏损", "高负债叠加"))]

    val_risk = False
    if row["_pe"] and row["_pe"] > 0 and row["_pe_pct"] is not None \
            and row["_pe_pct"] > GATES["pe_pct_danger"]:
        val_risk = True
    if (row["_pe"] is None or row["_pe"] <= 0) and row["_pb_pct"] is not None \
            and row["_pb_pct"] > GATES["pb_pct_danger"]:
        val_risk = True
    if row["_pe"] and row["_pe"] > 0 and row["_np_yoy"] and row["_np_yoy"] > 5 \
            and row["_pe"] / row["_np_yoy"] > GATES["peg_danger"]:
        val_risk = True

    completeness = row["data_completeness"]
    quality = row["stock_quality"]
    pattern_fail = (row["pattern_quality"] < GATES["pattern_weak"]
                    or row["pattern_state"] in ("跌破失效位", "突破失败"))

    # 主标签（只能一个）。优先级 = 硬风险 > 基本面未达中线门槛 > 形态失效 >
    # 估值透支 > 具体买点等待 > 核心观察。综合分不得越过任何一道门槛。
    if hard_exclude:
        label = "RISK_EXCLUDE"
    elif completeness < GATES["completeness_min"]:
        label = "FUNDAMENTAL_VERIFY"
    elif quality < GATES["quality_min"]:
        # 基本面不足以支撑 1~4 个月中线持有：形态再好也只能先核验基本面
        label = "FUNDAMENTAL_VERIFY"
    elif pattern_fail:
        label = "PATTERN_WEAK"
    elif val_risk:
        label = "VALUATION_RISK"
    else:
        setup = row["_setup"]
        if setup == "REBREAKOUT" and row["entry_quality"] >= 55:
            label = "REBREAKOUT_WATCH"
        elif setup in ("BREAKOUT", "BREAKOUT_DONE") and row["entry_quality"] >= 50:
            label = "BREAKOUT_WATCH"
        elif setup == "PULLBACK" and row["entry_quality"] >= 50:
            label = "PULLBACK_WATCH"
        else:
            # 基本面 + 形态均过关但没有合格买点：核心观察，绝不等于 BUY
            label = "CORE_WATCH"

    # 交易状态
    buy_ok = (label in ("CORE_WATCH", "PULLBACK_WATCH", "BREAKOUT_WATCH", "REBREAKOUT_WATCH")
              and row["stock_quality"] >= GATES["buy_stock"]
              and row["pattern_quality"] >= GATES["buy_pattern"]
              and row["entry_quality"] >= GATES["buy_entry"]
              and completeness >= GATES["completeness_buy"]
              and (row["risk_reward"] is not None and row["risk_reward"] >= GATES["buy_rr"])
              and row["_setup"] in ("PULLBACK", "BREAKOUT", "REBREAKOUT")
              and row["_setup_confirm"] >= 0.70)
    if label == "RISK_EXCLUDE" or (row["pattern_state"] == "跌破失效位"):
        status = "NO_TRADE"
    elif buy_ok:
        status = "BUY"
    else:
        status = "WAIT"
    return label, status, gates


# ═══════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════

def build_row(h, g, conn, trade_date, basic, ann_map, fi_map):
    code = h["ts_code"]
    name, industry, _ = basic.get(code, (h.get("stock_name", ""), h.get("industry", ""), ""))
    row = {"ts_code": code, "name": name, "industry": industry}
    row["pool_source"] = h.get("pool_source", "RB_TIGHT")
    row["_is_st"] = ("ST" in str(name)) or ("退" in str(name))
    row["_bars"] = len(g) if g is not None else 0

    # ── 形态 ──
    m = analyze_pattern(g) if g is not None else {"ok": False, "gaps": ["无行情"]}
    row["_m"] = m
    if not m.get("ok"):
        row["pattern_quality"] = 0.0
        row["pattern_detail"] = {"bottom": 0, "volume": 0, "ma": 0, "structure": 0, "liquidity": 0}
        row["pattern_state"] = "数据不足"
        row["bottom_count"], row["bottom_rise_pct"] = 0, 0.0
        row["ma20_slope"] = row["ma60_slope"] = row["volume_quality"] = None
        row["support_price"] = row["invalidation_price"] = row["close"] = None
        m = {"ok": False, "state": "数据不足", "close": None, "atr": None, "amt20": 0,
             "support_price": None, "invalidation_price": None, "dist_ma60": None,
             "ma60": None, "ma20": None, "bias20": None, "setup": "NONE"}
        row["_m"] = m
        e = {"setup": "NONE", "setup_desc": "无明确买点", "entry_low": None, "entry_high": None,
             "target": None, "rr": None, "confirm": 0.0, "target_gap": "目标位待确认"}
    else:
        pq, pdet, pstate = score_pattern(m)
        row["pattern_quality"] = pq
        row["pattern_detail"] = pdet
        row["pattern_state"] = pstate
        row["bottom_count"] = m["bottom_count"]
        row["bottom_rise_pct"] = round(m["bottom_rise_pct"], 1)
        row["ma20_slope"] = m["ma20_slope"]
        row["ma60_slope"] = m["ma60_slope"]
        row["volume_quality"] = pdet["volume"]
        row["support_price"] = m["support_price"]
        row["invalidation_price"] = m["invalidation_price"]
        row["close"] = m["close"]
        e = analyze_entry(m)

    # ── 财务 ──
    fin = load_financials(code, trade_date, ann_map.get(code, {}))
    fi = fi_map.get(code)
    val = load_valuation(conn, code, trade_date)
    d = build_fundamentals(fin, fi, val, (name, industry, basic.get(code, ("", "", ""))[2]))

    r_pts, r_av, r_note, r_cov = score_revenue(d)
    p_pts, p_av, p_note, p_cov = score_profit(d)
    c_pts, c_av, c_note, c_cov = score_cashflow(d)
    s_pts, s_av, s_note, s_cov = score_safety(d)
    i_pts, cat, i_note, i_cov = score_industry(d)
    v_pts, v_note, v_cov = score_valuation(d)

    detail = {"revenue": round(r_pts, 1), "profit": round(p_pts, 1),
              "cashflow": round(c_pts, 1), "safety": round(s_pts, 1),
              "industry": round(i_pts, 1), "valuation": round(v_pts, 1)}
    row["stock_detail"] = detail
    row["stock_quality"] = round(sum(detail.values()), 1)
    # 数据完整度：按权重加权的可用比例（缺失项不补中性分）
    wsum = sum(W_STOCK.values())
    row["data_completeness"] = round(sum(W_STOCK[k] * cov for k, cov in
                                         (("revenue", r_cov), ("profit", p_cov),
                                          ("cashflow", c_cov), ("safety", s_cov),
                                          ("industry", i_cov), ("valuation", v_cov))) / wsum, 2)
    row["fundamental_summary"] = "%s；%s；%s；%s" % (r_note, p_note, c_note, i_note)
    row["valuation_summary"] = v_note
    row["industry_type"] = cat
    row["_report_period"] = d.get("last_report")
    row["_last_ann"] = d.get("last_ann")

    # ── 买点 ──
    if m.get("ok"):
        eq, edet = score_entry(m, e)
    else:
        eq, edet = 0.0, {"position": 0, "support": 0, "confirm": 0, "rr": 0, "liquidity": 0}
    row["entry_quality"] = eq
    row["entry_detail"] = edet
    row["_setup"] = e["setup"]
    row["_setup_desc"] = e["setup_desc"]
    row["_setup_confirm"] = e["confirm"] / 25.0 if e["confirm"] else 0.0
    row["entry_price"] = (round((e["entry_low"] + e["entry_high"]) / 2, 2)
                          if e["entry_low"] and e["entry_high"] else None)
    row["entry_low"], row["entry_high"] = e["entry_low"], e["entry_high"]
    row["target_price"] = e["target"]
    row["risk_reward"] = e["rr"]
    row["_target_gap"] = e.get("target_gap")

    # ── 汇总 ──
    row["total_score"] = round(row["stock_quality"] * W_TOTAL["stock"]
                               + row["pattern_quality"] * W_TOTAL["pattern"]
                               + row["entry_quality"] * W_TOTAL["entry"], 1)

    # ── 风险 / 催化剂 / 主题 ──
    risks = []
    for f in d["np_flags"]:
        risks.append(f)
    if d["ocf_neg"]:
        risks.append("年度经营现金流为负")
    if d.get("ocf_neg_streak"):
        risks.append("近两季经营现金流为负")
    if d["debt_ratio"] is not None and d["debt_ratio"] > GATES["debt_danger"]:
        risks.append("资产负债率 %.0f%%" % d["debt_ratio"])
    if d["goodwill_ratio"] is not None and d["goodwill_ratio"] > 20:
        risks.append("商誉占总资产 %.0f%%" % d["goodwill_ratio"])
    if d["ar_yoy"] is not None and d["rev_last_yoy"] is not None and d["ar_yoy"] - d["rev_last_yoy"] > 30:
        risks.append("应收账款增速远超营收")
    if m.get("ok") and m["structure"] in ("D", "E"):
        risks.append("底部结构%s：%s" % (m["structure"], m["structure_desc"]))
    if m.get("ok") and (m["bias20"] or 0) > 12:
        risks.append("价格偏离 MA20 %.0f%%，短期过热" % m["bias20"])
    if row["pool_source"] == "RB_EXT":
        # 因放宽「不追高」才进入候选：把延伸幅度显式写成风险，避免被当成低位买点
        dm = int(round(float(h.get("dist_ma60") or 0)))
        el = int(round(float(h.get("ext_low") or 0)))
        risks.insert(0, "中线放宽纳入（距MA60 %+d%%、距底部 %+d%%），超出 RB 严格口径" % (dm, el))
    if d["val_gap"]:
        risks.append(d["val_gap"])
    for gp in fin.get("gaps", []):
        risks.append(gp)
    row["key_risks"] = "；".join(dict.fromkeys(risks)) or "未发现显著风险"
    row["_loss_years"] = d["loss_years"]
    row["_ocf_neg"] = bool(d["ocf_neg"])
    row["_debt"] = d["debt_ratio"]
    row["_pe"] = d["pe"]
    row["_pe_pct"] = d["pe_pct"]
    row["_pb_pct"] = d["pb_pct"]
    row["_np_yoy"] = d["np_yoy"]
    row["_amt20"] = m.get("amt20") or 0

    rel, rel_w = theme_relation(h, d)
    row["theme"] = h.get("theme") or ""
    row["theme_heat"] = h.get("theme_cnt", 0)
    row["theme_relation"] = rel
    row["catalyst"] = ("题材共振 %s（%d 家）：%s" % (row["theme"], row["theme_heat"], rel)
                       if row["theme"] else "")

    label, status, gates = decide(row)
    row["main_label"], row["trade_status"] = label, status
    row["_gates"] = gates
    row["reason"] = build_reason(row, m, e, d)
    row["_d"] = d
    row["_e"] = e
    return row


def build_reason(row, m, e, d):
    parts = []
    parts.append("股票质量 %.0f（完整度 %.0f%%）" % (row["stock_quality"], row["data_completeness"] * 100))
    if m.get("ok"):
        parts.append("底部%d次抬高 %+.1f%%、结构%s" % (m["bottom_count"], m["bottom_rise_pct"], m["structure"]))
        parts.append("阶段：%s" % m["state"])
    else:
        parts.append("形态数据不足")
    parts.append("买点：%s" % e["setup_desc"])
    if row["risk_reward"] is not None:
        parts.append("RR %.2f" % row["risk_reward"])
    if row["_gates"]:
        parts.append("门槛：" + "、".join(row["_gates"][:2]))
    return "；".join(parts)


def write_outputs(trade_date, rows, pool_n, top_n):
    os.makedirs(REPORT_DIR, exist_ok=True)
    rows = sorted(rows, key=lambda r: (-r["total_score"], -r["pattern_quality"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i

    cols = ["rank", "ts_code", "name", "industry", "stock_quality", "pattern_quality",
            "entry_quality", "total_score", "data_completeness", "bottom_count",
            "bottom_rise_pct", "ma20_slope", "ma60_slope", "volume_quality", "pattern_state",
            "support_price", "invalidation_price", "entry_price", "target_price",
            "risk_reward", "fundamental_summary", "valuation_summary", "key_risks",
            "catalyst", "main_label", "trade_status", "reason", "close",
            "industry_type", "theme", "theme_relation", "report_period", "pool_source"]
    csv_rows = []
    for r in rows:
        csv_rows.append({
            "rank": r["rank"], "ts_code": r["ts_code"], "name": r["name"], "industry": r["industry"],
            "stock_quality": r["stock_quality"], "pattern_quality": r["pattern_quality"],
            "entry_quality": r["entry_quality"], "total_score": r["total_score"],
            "data_completeness": r["data_completeness"], "bottom_count": r["bottom_count"],
            "bottom_rise_pct": r["bottom_rise_pct"], "ma20_slope": r["ma20_slope"],
            "ma60_slope": r["ma60_slope"], "volume_quality": r["volume_quality"],
            "pattern_state": r["pattern_state"], "support_price": r["support_price"],
            "invalidation_price": r["invalidation_price"], "entry_price": r["entry_price"],
            "target_price": r["target_price"], "risk_reward": r["risk_reward"],
            "fundamental_summary": r["fundamental_summary"],
            "valuation_summary": r["valuation_summary"], "key_risks": r["key_risks"],
            "catalyst": r["catalyst"], "main_label": r["main_label"],
            "trade_status": r["trade_status"], "reason": r["reason"], "close": r["close"],
            "industry_type": r["industry_type"], "theme": r["theme"],
            "theme_relation": r["theme_relation"], "report_period": r["_report_period"],
            "pool_source": r.get("pool_source", "RB_TIGHT"),
        })
    csv_path = os.path.join(REPORT_DIR, "rb_value_%s.csv" % trade_date)
    pd.DataFrame(csv_rows, columns=cols).to_csv(csv_path, index=False, encoding="utf-8-sig")

    md = build_markdown(trade_date, rows, pool_n, top_n)
    md_path = os.path.join(REPORT_DIR, "rb_value_%s.md" % trade_date)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    return md_path, csv_path


def _group(rows):
    """A/B/C 三组：不凑数，宁缺毋滥。

    A = 三评分均过关且标签属于可操作观察（优先研究）；
    B = 有明确待解决项（基本面待核验 / 形态不足 / 估值偏高 / 买点未到）；
    C = 硬风险剔除或质量明显不足，暂不投入研究时间。
    """
    A, B, C = [], [], []
    for r in rows:
        if r["main_label"] == "RISK_EXCLUDE" or r["stock_quality"] < GATES["c_group_quality"]:
            C.append(r)
        elif (r["main_label"] in ("CORE_WATCH", "PULLBACK_WATCH", "BREAKOUT_WATCH", "REBREAKOUT_WATCH")
              and r["stock_quality"] >= GATES["a_group_stock"]
              and r["total_score"] >= GATES["a_group_total"]):
            A.append(r)
        else:
            B.append(r)
    return A, B, C


def rbs_tight_pct():
    """RB 严格口径的「不追高」阈值（只用于报告说明；取不到时回退默认值）。"""
    try:
        import rising_bottom_select as _r
        return float(_r.MAX_DIST_MA60_PCT), float(_r.MAX_EXT_FROM_LOW_PCT)
    except Exception:
        return 15.0, 20.0


def build_markdown(trade_date, rows, pool_n, top_n):
    A, B, C = _group(rows)
    ext_n = sum(1 for r in rows if r.get("pool_source") == "RB_EXT")
    tight_n = max(pool_n - ext_n, 0)
    counts = {}
    for r in rows:
        counts[r["main_label"]] = counts.get(r["main_label"], 0) + 1
    st = {}
    for r in rows:
        st[r["trade_status"]] = st.get(r["trade_status"], 0) + 1

    L = []
    L.append("# RB 底部抬高中线价值筛选 · %s" % trade_date)
    L.append("")
    L.append("模型版本 **%s**｜数据截止 **%s**｜模型状态 **%s**" % (VERSION, trade_date, MODEL_STATUS))
    L.append("")
    L.append("RB 严格池 %d 只 + 中线放宽新增 %d 只 → 本筛选器全量评估 %d 只。"
             "三个评分互相独立：股票质量（公司）、形态质量（结构）、买点质量（位置），"
             "综合分 = 股票质量×%.0f%% + 形态质量×%.0f%% + 买点质量×%.0f%%，"
             "但**风险门槛优先于综合分**，综合分高不等于可以买。"
             % (tight_n, ext_n, len(rows), W_TOTAL["stock"] * 100,
                W_TOTAL["pattern"] * 100, W_TOTAL["entry"] * 100))
    L.append("")
    if ext_n:
        L.append("> 表中名称带 `*` 的 %d 只为**中线放宽纳入**（形态链成立，但距 MA60 或距最近底部已超出 "
                 "RB 短线口径 %.0f%%/%.0f%%，按中线口径放宽到 %.0f%%/%.0f%%）：它们出现是为了不被漏掉，"
                 "风险栏已注明「已非回踩位」，买点分通常偏低，多数应为 WAIT。"
                 % (ext_n, rbs_tight_pct()[0], rbs_tight_pct()[1],
                    RB_RELAX["max_dist_ma60_pct"], RB_RELAX["max_ext_from_low_pct"]))
        L.append("")
    L.append("标签分布：%s｜交易状态：%s"
             % ("、".join("%s %d" % (k, v) for k, v in sorted(counts.items(), key=lambda x: -x[1])),
                "、".join("%s %d" % (k, v) for k, v in sorted(st.items()))))
    L.append("")
    L.append("> 本报告只回答「值不值得研究 / 形态是否有效 / 现在该不该买」，"
             "不构成投资建议；权重为工程默认值，未完成样本外验证（%s）。" % MODEL_STATUS)
    L.append("")

    # ── 输出一：全量排序表 ──
    L.append("## 输出一 · 全量排序表")
    L.append("")
    L.append("（完整 32 字段见同目录 `rb_value_%s.csv`；下表为排序与决策主字段）" % trade_date)
    L.append("")
    L.append("| # | 代码 | 名称 | 行业 | 质量 | 形态 | 买点 | 综合 | 完整度 | 底部 | 抬高 | "
             "阶段 | 支撑 | 失效 | 入场 | 目标 | RR | 标签 | 状态 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    show = rows[:max(top_n, 40)]
    for r in show:
        L.append("| %d | %s | %s | %s | %.0f | %.0f | %.0f | %.1f | %.0f%% | %d | %+.1f%% | %s | %s | %s | %s | %s | %s | %s | %s |"
                 % (r["rank"], r["ts_code"],
                    r["name"] + ("*" if r.get("pool_source") == "RB_EXT" else ""),
                    (r["industry"] or "")[:6],
                    r["stock_quality"], r["pattern_quality"], r["entry_quality"], r["total_score"],
                    r["data_completeness"] * 100, r["bottom_count"], r["bottom_rise_pct"],
                    r["pattern_state"], fnum(r["support_price"], 2), fnum(r["invalidation_price"], 2),
                    fnum(r["entry_price"], 2), fnum(r["target_price"], 2),
                    fnum(r["risk_reward"], 2), r["main_label"], r["trade_status"]))
    if len(rows) > len(show):
        L.append("")
        L.append("（其余 %d 只见 CSV）" % (len(rows) - len(show)))
    L.append("")

    # ── 输出二：逐只分析 ──
    L.append("## 输出二 · 逐只分析（%d 只）" % len(rows))
    L.append("")
    for r in rows:
        L += per_stock_lines(r)
        L.append("")

    # ── 输出三：候选名单 ──
    L.append("## 输出三 · 最终候选名单")
    L.append("")
    L.append("### A 组 · 优先研究的中线候选（%d 只）" % len(A[:10]))
    L.append("")
    if A:
        L.append("| 代码 | 名称 | 质量 | 形态 | 买点 | 综合 | 标签 | 状态 | 一句话 |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        for r in A[:10]:
            L.append("| %s | %s | %.0f | %.0f | %.0f | %.1f | %s | %s | %s |"
                     % (r["ts_code"], r["name"], r["stock_quality"], r["pattern_quality"],
                        r["entry_quality"], r["total_score"], r["main_label"],
                        r["trade_status"], one_liner(r)))
    else:
        L.append("**空名单**：当日无同时满足股票质量 ≥ %.0f、综合分 ≥ %.0f 且无风险门槛的标的。"
                 % (GATES["a_group_stock"], GATES["a_group_total"]))
    L.append("")
    L.append("### B 组 · 等待基本面验证或技术确认（共 %d 只，列前 %d）" % (len(B), len(B[:10])))
    L.append("")
    if B:
        L.append("| 代码 | 名称 | 质量 | 形态 | 买点 | 标签 | 待解决 |")
        L.append("|---|---|---|---|---|---|---|")
        for r in B[:10]:
            L.append("| %s | %s | %.0f | %.0f | %.0f | %s | %s |"
                     % (r["ts_code"], r["name"], r["stock_quality"], r["pattern_quality"],
                        r["entry_quality"], r["main_label"], _b_reason(r)))
    else:
        L.append("**空名单**")
    L.append("")
    L.append("### C 组 · 暂不优先（共 %d 只，列前 %d）" % (len(C), len(C[:10])))
    L.append("")
    if C:
        L.append("| 代码 | 名称 | 质量 | 形态 | 买点 | 标签 | 主因 |")
        L.append("|---|---|---|---|---|---|---|")
        for r in C[:10]:
            why = "股票质量 %.0f 低于 %.0f" % (r["stock_quality"], GATES["c_group_quality"])
            if r["main_label"] == "RISK_EXCLUDE":
                why = (r["reason"] or "").split("门槛：")[-1] or r["main_label"]
            elif r["key_risks"] and r["key_risks"] != "未发现显著风险":
                why += "；" + r["key_risks"]
            # 按分号取前 2 项，避免把一句话切断（原为 why[:52]）
            why = "；".join([x for x in why.split("；") if x][:2])
            L.append("| %s | %s | %.0f | %.0f | %.0f | %s | %s |"
                     % (r["ts_code"], r["name"], r["stock_quality"], r["pattern_quality"],
                        r["entry_quality"], r["main_label"], why))
    else:
        L.append("**空名单**")
    L.append("")

    # ── 输出四：A 组风险收益比较 ──
    L.append("## 输出四 · A 组风险收益比较")
    L.append("")
    if A:
        L.append("| 代码 | 名称 | 现价 | 计划入场 | 关键支撑 | 失效价 | 第一目标 | 潜在收益 | 潜在亏损 | RR | 现在值得买 |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for r in A[:10]:
            entry = r["entry_price"]
            tgt = r["target_price"]
            inv = r["invalidation_price"]
            up = (tgt / entry - 1) * 100 if (tgt and entry) else None
            dn = (inv / entry - 1) * 100 if (entry and inv) else None
            L.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |"
                     % (r["ts_code"], r["name"], fnum(r["close"], 2),
                        ("%s~%s" % (fnum(r["entry_low"], 2), fnum(r["entry_high"], 2)))
                        if r["entry_low"] else "待确认",
                        fnum(r["support_price"], 2), fnum(inv, 2),
                        fnum(tgt, 2) if tgt else "目标位待确认",
                        fnum(up, 1, True) + "%" if up is not None else "—",
                        fnum(dn, 1, True) + "%" if dn is not None else "—",
                        fnum(r["risk_reward"], 2),
                        "是" if r["trade_status"] == "BUY" else "否（%s）" % r["trade_status"]))
    else:
        L.append("**A 组为空，无风险收益比较。**")
    L.append("")
    L.append("> 目标价只取自历史结构（平台上沿 / 前高 / 平台高度投影），不使用历史最高价；"
             "无可靠目标时输出「目标位待确认」。")
    L.append("")

    # ── 数据缺口与限制 ──
    L.append("## 数据缺口与已知限制")
    L.append("")
    L.append("- 财务口径：只使用 `ann_date <= %s` 的报告，缺失公告日的报告一律丢弃（防未来数据泄漏）。"
             % trade_date)
    L.append("- 货币资金、有息负债、总负债科目未在本地缓存 → A4「短期偿债/有息负债」两项记 0 分并计入缺口，"
             "不参与打分补偿。")
    L.append("- 扣非净利润（`profit_dedt`）parquet 覆盖不全，缺失时该项 0 分并降低 `data_completeness`。")
    L.append("- 估值分位需 ≥%d 个样本（近 %.0f 个自然日）；样本不足输出「估值待核验」。"
             % (VAL_MIN_SAMPLES, VAL_LOOKBACK_DAYS))
    L.append("- 形态分析用前复权价；最新一根复权价等于原始价，故支撑/失效/目标价可直接作为交易价参考。")
    L.append("- 摆动低点均已按「右侧 k 根 + 反弹确认窗口」确认后才计入，不存在用未来 K 线回填低点的情况。")
    L.append("- 模型状态：**%s** —— 未完成严格样本外验证（T+20/T+60、最大回撤、扣费、分市场阶段、"
             "相对原始 RB 策略增量、随机池对比），不得据此宣称有效。" % MODEL_STATUS)
    L.append("")
    return "\n".join(L)


def one_liner(r):
    if r["trade_status"] == "BUY":
        return "三项评分均达标，可用 %s 附近分批" % fnum(r["entry_price"], 2)
    if r["_setup"] == "PULLBACK":
        return "等缩量回踩 %s~%s 确认" % (fnum(r["entry_low"], 2), fnum(r["entry_high"], 2))
    if r["_setup"] in ("BREAKOUT",):
        return "等放量突破 %s 确认" % fnum(r["_m"].get("platform_high"), 2)
    if r["_setup"] == "REBREAKOUT":
        return "等二次启动确认"
    return "质量过关，暂无明确买点，观察"


def _b_reason(r):
    d = r["_d"]
    if r["main_label"] == "FUNDAMENTAL_VERIFY":
        if r["data_completeness"] < GATES["completeness_min"]:
            return "数据完整度 %.0f%% 不足 %.0f%%" % (r["data_completeness"] * 100,
                                                  GATES["completeness_min"] * 100)
        if r["stock_quality"] < GATES["quality_min"]:
            return "股票质量 %.0f 未达中线门槛 %.0f" % (r["stock_quality"], GATES["quality_min"])
        miss = []
        if not d.get("avail", {}).get("income"):
            miss.append("利润表")
        if d.get("deduct_yoy") is None:
            miss.append("扣非")
        if d.get("debt_ratio") is None:
            miss.append("负债率")
        return "待核验：%s" % ("、".join(miss) or "关键财务项")
    if r["main_label"] == "VALUATION_RISK":
        return r["valuation_summary"][:40]
    if r["main_label"] == "PATTERN_WEAK":
        return "%s（形态分 %.0f）" % (r["pattern_state"], r["pattern_quality"])
    if r["main_label"] in ("CORE_WATCH", "PULLBACK_WATCH", "BREAKOUT_WATCH", "REBREAKOUT_WATCH"):
        miss = []
        if r["total_score"] < GATES["a_group_total"]:
            miss.append("综合 %.1f < %.0f" % (r["total_score"], GATES["a_group_total"]))
        if r["entry_quality"] < GATES["buy_entry"]:
            miss.append("买点 %d 未到 %.0f" % (r["entry_quality"], GATES["buy_entry"]))
        if r["pattern_quality"] < GATES["buy_pattern"]:
            miss.append("形态 %d 偏弱" % r["pattern_quality"])
        if r["risk_reward"] is None:
            miss.append("目标位待确认")
        elif r["risk_reward"] < GATES["buy_rr"]:
            miss.append("RR %.1f < %.1f" % (r["risk_reward"], GATES["buy_rr"]))
        return "、".join(miss) or "等买点确认"
    return r["main_label"]


def per_stock_lines(r):
    """输出二：每只 8 项、100~200 字。"""
    m, e, d = r["_m"], r["_e"], r["_d"]
    L = ["### %s %s（%s｜%s）" % (r["ts_code"], r["name"], r["industry"] or "—", r["industry_type"]),
         ""]
    L.append("1. **结论**：%s / %s —— %s。"
             % (r["main_label"], r["trade_status"], one_liner(r)))
    adv = []
    if d.get("rev_last_yoy") is not None and d["rev_last_yoy"] > 0:
        adv.append("营收同比 %+.1f%%" % d["rev_last_yoy"])
    if d.get("np_yoy") is not None and d["np_yoy"] > 0:
        adv.append("归母同比 %+.1f%%" % d["np_yoy"])
    if d.get("gm_now") is not None:
        adv.append("毛利率 %.1f%%" % d["gm_now"])
    if d.get("ocf_np_ratio") is not None:
        adv.append("OCF/净利 %.2f" % d["ocf_np_ratio"])
    if d.get("roe") is not None:
        adv.append("ROE %.1f%%" % d["roe"])
    L.append("2. **基本面优势**：%s。" % ("，".join(adv[:4]) if adv else "无明显优势（%s）" % r["fundamental_summary"][:50]))
    # 按分号取前 3 项，避免按字符硬切断在半句话上
    risks_txt = "；".join([x for x in r["key_risks"].split("；") if x][:3])
    L.append("3. **主要经营风险**：%s。" % (risks_txt or "—"))
    if m.get("ok"):
        L.append("4. **形态质量**：%.0f 分，%d 个有效底部累计抬高 %+.1f%%，结构 %s（%s），"
                 "涨跌量比 %s，MA20 斜率 %s。"
                 % (r["pattern_quality"], m["bottom_count"], m["bottom_rise_pct"], m["structure"],
                    m["structure_desc"], fnum(m["up_dn_vol"], 2),
                    (fnum(m["ma20_slope"], 1, True) + "%") if m["ma20_slope"] is not None else "—"))
        L.append("5. **当前阶段**：%s；现价 %.2f，平台上沿 %s，距前高 %s。"
                 % (m["state"], m["close"], fnum(m.get("platform_high"), 2),
                    (fnum(m.get("dist_platform"), 1, True) + "%")
                    if m.get("dist_platform") is not None else "—"))
        L.append("6. **观察买点**：%s（%s~%s）。"
                 % (e["setup_desc"], fnum(r["entry_low"], 2), fnum(r["entry_high"], 2)))
        L.append("7. **失效条件**：收盘跌破 %.2f（最近有效底部 %.2f − %.1f×ATR），"
                 "跌破后需重新构筑底部、出现新的抬高链再评估。"
                 % (m["invalidation_price"], m["support_price"], P_PATTERN["invalidation_atr"]))
    else:
        L.append("4. **形态质量**：数据不足（%s）。" % "、".join(m.get("gaps") or ["无行情"]))
        L.append("5. **当前阶段**：无法判定。")
        L.append("6. **观察买点**：无。")
        L.append("7. **失效条件**：无结构支撑可算。")
    L.append("8. **最终决策**：%s / %s —— %s" % (r["main_label"], r["trade_status"], r["reason"]))
    return L


def main():
    args = [a for a in sys.argv[1:] if a.strip()]
    top_n, cfg = 30, None
    if "--top" in args:
        i = args.index("--top")
        if i + 1 < len(args):
            top_n = int(args[i + 1])
        del args[i:i + 2]
    if "--config" in args:
        i = args.index("--config")
        if i + 1 < len(args):
            cfg = args[i + 1]
        del args[i:i + 2]
    load_config(cfg)
    date_arg = args[0].strip() if args else None

    # ── 复用 RB 技术池（只调用其 scan，不写库、不改文件）──
    sys.path.insert(0, BASE_DIR)
    import rising_bottom_select as rbs
    trade_date, hits_tight = rbs.scan(date_arg, verbose=False)
    if not hits_tight:
        print("[RBV] %s RB 技术池为空，无法筛选" % trade_date)
        return 2

    # 中线放宽池：仅在本进程内临时替换 RB 的「不追高」常量再跑一遍，
    # 差集 = 形态仍成立、但因距 MA60/距底部过远被 RB 严格口径剔除的票。
    # 不修改 rising_bottom_select.py，run_all.bat 的 RB 结果不受影响。
    hits = list(hits_tight)
    ext_n = 0
    if RB_RELAX.get("enabled"):
        saved = (rbs.MAX_DIST_MA60_PCT, rbs.MAX_EXT_FROM_LOW_PCT, rbs.LAST_LOW_AGE_MAX)
        hits_wide = []
        try:
            rbs.MAX_DIST_MA60_PCT = float(RB_RELAX["max_dist_ma60_pct"])
            rbs.MAX_EXT_FROM_LOW_PCT = float(RB_RELAX["max_ext_from_low_pct"])
            rbs.LAST_LOW_AGE_MAX = int(RB_RELAX["last_low_age_max"])
            _, hits_wide = rbs.scan(trade_date, verbose=False)
        except Exception as ex:
            print("[RBV][WARN] 中线放宽池构建失败，只用严格池：%s" % ex)
        finally:
            (rbs.MAX_DIST_MA60_PCT, rbs.MAX_EXT_FROM_LOW_PCT,
             rbs.LAST_LOW_AGE_MAX) = saved
        seen = {h["ts_code"] for h in hits_tight}
        for h in hits_wide:
            if h["ts_code"] in seen:
                continue
            h["pool_source"] = "RB_EXT"
            hits.append(h)
            ext_n += 1
    for h in hits:
        h.setdefault("pool_source", "RB_TIGHT")

    print("[RBV] %s RB 严格池 %d 只 + 中线放宽新增 %d 只 = 评估 %d 只，开始三评分评估…"
          % (trade_date, len(hits_tight), ext_n, len(hits)))

    codes = [h["ts_code"] for h in hits]
    basic = load_stock_basic()
    conn = sqlite3.connect(DB_PATH)
    try:
        bars = load_bars_for(conn, codes, trade_date)
        FI = load_fina_indicator(conn, codes, trade_date)
        # income 缺 ann_date 时用其他表补公告日
        ann_map = {}
        for code in codes:
            mp = {}
            if code in FI:
                for _, r in FI[code].iterrows():
                    mp[str(r["end_date"])] = str(r["ann_date"])
            for prefix in ("balance_v14", "cashflow"):
                for p in _pq_paths(prefix, code):
                    try:
                        d = pd.read_parquet(p, columns=["end_date", "ann_date"])
                    except Exception:
                        continue
                    for _, r in d.iterrows():
                        if r.get("ann_date"):
                            mp.setdefault(str(r["end_date"]), str(r["ann_date"]))
            ann_map[code] = mp

        rows = []
        for h in hits:
            try:
                rows.append(build_row(h, bars.get(h["ts_code"]), conn, trade_date, basic, ann_map, FI))
            except Exception as ex:
                print("[RBV][WARN] %s 评估失败：%s" % (h["ts_code"], ex))
    finally:
        conn.close()

    if not rows:
        print("[RBV] 无有效评估结果")
        return 2
    md_path, csv_path = write_outputs(trade_date, rows, len(hits), top_n)
    A, B, C = _group(sorted(rows, key=lambda r: -r["total_score"]))
    print("[RBV] 完成｜评估 %d 只｜A %d / B %d / C %d" % (len(rows), len(A), len(B), len(C)))
    print("[RBV] 报告 %s" % md_path)
    print("[RBV] 全量表 %s" % csv_path)
    buy = [r for r in rows if r["trade_status"] == "BUY"]
    print("[RBV] BUY %d 只%s" % (len(buy), ("：" + "、".join(r["ts_code"] for r in buy)) if buy else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
