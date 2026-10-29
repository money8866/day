# -*- coding: utf-8 -*-
"""W7 六道量能开关的逐项消融（决定引擎默认值的关键证据）。

问题
----
配对回测已给出 old/new 两端结论：
  · 共有信号 228 条：old 与 new **表现逐条完全相同** → 改造对已入选信号零影响；
  · 仅 new 信号 2076 条：T+3 -0.68%、T+3 超额 -0.47%、胜率 40.6%、失效位触达 62%
    → 明确负期望；
  · 反事实显示仅 new 中「止跌日量比≤0.55」这 163 条是正期望（T+10 超额 +1.67%）。
但这仍不足以回答「引擎默认值该定成什么」——因为 old 一端同时开着六道闸，
无法归因到具体某一道。

方法
----
在**同一批**探针命中的 (股票, 交易日) 上，一次性跑多个口径的完整
analyze + exec_status + quality_gate，得到每个口径的：
    信号数 / 状态分布 / T+N 收益 / 超额 / 胜率 / 失效位触达率

口径枚举（9 组）
----------------
  old            六闸全开（改造前基准）
  new            六闸全关（用户口径：T0 后完全不对量能约束）
  no_A_PULL      仅关 W7_GATE_A_PULL_SHRINK
  no_A_STALL     仅关 W7_GATE_A_STALL_VOLR
  no_B_PULL      仅关 W7_GATE_B_PULL_SHRINK
  no_B_BREAK     仅关 W7_GATE_B_BREAK_VOLR
  no_B_DIST      仅关 W7_GATE_B_DIST_RISK
  no_PERSIST     仅关 W7_GATE_PERSIST_SELL
  keep_core      只保留 A_STALL + B_BREAK（两道「生死门」），其余四闸关闭

正确性保证
----------
· 预筛用 **new 口径探针**（最宽松）。已实测 old 探针命中集 ⊆ new 探针命中集
  （违反 0 次），故任何口径的 T0 信号都落在预筛集合内 → 预筛不漏。
· 预筛命中后，各口径均走引擎原生 analyze + w7_exec_status + w7_quality_gate，
  输出字段与生产完全一致。
· 锚点由两口径共用（锚点与开关无关，已实测锚点不一致 0 次）。

用法
----
  python _w7_novol_ablation.py --jobs 4
"""
import argparse
import importlib
import json
import os
import sys
import time
from multiprocessing import Pool

import numpy as np
import pandas as pd

SOLO = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(SOLO, "w7_second_wave_engine.py")
OUT_DIR = os.path.join(SOLO, "_w7_novol_out")
if SOLO not in sys.path:
    sys.path.insert(0, SOLO)

GATE_KEYS = ("W7_GATE_A_PULL_SHRINK", "W7_GATE_A_STALL_VOLR", "W7_GATE_B_PULL_SHRINK",
             "W7_GATE_B_BREAK_VOLR", "W7_GATE_B_DIST_RISK", "W7_GATE_PERSIST_SELL")

# 口径名 → 六闸取值（True=开）
CALIBERS = {
    "old":        dict.fromkeys(GATE_KEYS, True),
    "new":        dict.fromkeys(GATE_KEYS, False),
    "no_A_PULL":  {**dict.fromkeys(GATE_KEYS, True), "W7_GATE_A_PULL_SHRINK": False},
    "no_A_STALL": {**dict.fromkeys(GATE_KEYS, True), "W7_GATE_A_STALL_VOLR": False},
    "no_B_PULL":  {**dict.fromkeys(GATE_KEYS, True), "W7_GATE_B_PULL_SHRINK": False},
    "no_B_BREAK": {**dict.fromkeys(GATE_KEYS, True), "W7_GATE_B_BREAK_VOLR": False},
    "no_B_DIST":  {**dict.fromkeys(GATE_KEYS, True), "W7_GATE_B_DIST_RISK": False},
    "no_PERSIST": {**dict.fromkeys(GATE_KEYS, True), "W7_GATE_PERSIST_SELL": False},
    "keep_core":  {"W7_GATE_A_PULL_SHRINK": False, "W7_GATE_A_STALL_VOLR": True,
                    "W7_GATE_B_PULL_SHRINK": False, "W7_GATE_B_BREAK_VOLR": True,
                    "W7_GATE_B_DIST_RISK": False, "W7_GATE_PERSIST_SELL": False},
    # ── 二轮验证口径（20261029 追加）────────────────────────────
    # 依据配对回测证据链构造的混合口径：保留有判别力的闸、关掉其余。
    "keep_A_STALL":  {"W7_GATE_A_PULL_SHRINK": False, "W7_GATE_A_STALL_VOLR": True,
                       "W7_GATE_B_PULL_SHRINK": False, "W7_GATE_B_BREAK_VOLR": False,
                       "W7_GATE_B_DIST_RISK": False, "W7_GATE_PERSIST_SELL": False},
    "keep_A_STALL_B_BREAK": {"W7_GATE_A_PULL_SHRINK": False, "W7_GATE_A_STALL_VOLR": True,
                              "W7_GATE_B_PULL_SHRINK": False, "W7_GATE_B_BREAK_VOLR": True,
                              "W7_GATE_B_DIST_RISK": False, "W7_GATE_PERSIST_SELL": False},
    "keep_A_STALL_PERSIST": {"W7_GATE_A_PULL_SHRINK": False, "W7_GATE_A_STALL_VOLR": True,
                              "W7_GATE_B_PULL_SHRINK": False, "W7_GATE_B_BREAK_VOLR": False,
                              "W7_GATE_B_DIST_RISK": False, "W7_GATE_PERSIST_SELL": True},
    "keep_A_STALL_DIST": {"W7_GATE_A_PULL_SHRINK": False, "W7_GATE_A_STALL_VOLR": True,
                          "W7_GATE_B_PULL_SHRINK": False, "W7_GATE_B_BREAK_VOLR": False,
                          "W7_GATE_B_DIST_RISK": True, "W7_GATE_PERSIST_SELL": False},
    "keep_A_STALL_B_PULL": {"W7_GATE_A_PULL_SHRINK": False, "W7_GATE_A_STALL_VOLR": True,
                            "W7_GATE_B_PULL_SHRINK": True, "W7_GATE_B_BREAK_VOLR": False,
                            "W7_GATE_B_DIST_RISK": False, "W7_GATE_PERSIST_SELL": False},
    # ── 第二轮定向验证口径（20261029）──────────────────────────
    # 第一轮消融结论：A_PULL 关掉边际为正（+134 条且全面变好）；
    #                B_DIST / PERSIST 关掉零影响；A_STALL / B_PULL / B_BREAK 关掉均为负。
    # rec = 唯一保留全部三道有效闸、且关掉 A_PULL + 两道零影响闸的最优候选。
    "rec":            {"W7_GATE_A_PULL_SHRINK": False, "W7_GATE_A_STALL_VOLR": True,
                       "W7_GATE_B_PULL_SHRINK": True, "W7_GATE_B_BREAK_VOLR": True,
                       "W7_GATE_B_DIST_RISK": False, "W7_GATE_PERSIST_SELL": False},
    "rec_noBpull":    {"W7_GATE_A_PULL_SHRINK": False, "W7_GATE_A_STALL_VOLR": True,
                       "W7_GATE_B_PULL_SHRINK": False, "W7_GATE_B_BREAK_VOLR": True,
                       "W7_GATE_B_DIST_RISK": False, "W7_GATE_PERSIST_SELL": False},
    "rec_dist_on":    {"W7_GATE_A_PULL_SHRINK": False, "W7_GATE_A_STALL_VOLR": True,
                       "W7_GATE_B_PULL_SHRINK": True, "W7_GATE_B_BREAK_VOLR": True,
                       "W7_GATE_B_DIST_RISK": True, "W7_GATE_PERSIST_SELL": False},
    "rec_persist_on": {"W7_GATE_A_PULL_SHRINK": False, "W7_GATE_A_STALL_VOLR": True,
                       "W7_GATE_B_PULL_SHRINK": True, "W7_GATE_B_BREAK_VOLR": True,
                       "W7_GATE_B_DIST_RISK": False, "W7_GATE_PERSIST_SELL": True},
    "rec_all_off_but_A": {"W7_GATE_A_PULL_SHRINK": False, "W7_GATE_A_STALL_VOLR": True,
                          "W7_GATE_B_PULL_SHRINK": False, "W7_GATE_B_BREAK_VOLR": False,
                          "W7_GATE_B_DIST_RISK": False, "W7_GATE_PERSIST_SELL": False},
    # ── 第三轮：阈值放宽口径（闸仍开启，只把 W7_T0_SHRINK 从 0.50 放宽）──
    "thr060": dict.fromkeys(GATE_KEYS, True),
    "thr070": dict.fromkeys(GATE_KEYS, True),
    "thr085": dict.fromkeys(GATE_KEYS, True),
    "thr100": dict.fromkeys(GATE_KEYS, True),
    "thr070_all": dict.fromkeys(GATE_KEYS, True),
    # ── 第五轮：类型A 止跌日量比阈值放宽（A_STALL 闸保留，阈值 0.55 → 0.70/0.85/1.00）──
    "stall070": dict.fromkeys(GATE_KEYS, True),
    "stall085": dict.fromkeys(GATE_KEYS, True),
    "stall100": dict.fromkeys(GATE_KEYS, True),
    # ── 第六轮：候选落地方案（A_PULL 阈值 0.85 × A_STALL 阈值放宽 的组合）──
    "c085_s070": dict.fromkeys(GATE_KEYS, True),
    "c085_s085": dict.fromkeys(GATE_KEYS, True),
    "c085_s100": dict.fromkeys(GATE_KEYS, True),
}

_MOD = None
_FAST = None
_READER = None
_CTX = {}
_DATES = []
_UNI = {}
_IGE = {}
# 实际参与本轮的口径子集（默认 = CALIBERS 全量；--calibers 可裁剪）
_ACTIVE = dict(CALIBERS)

# 阈值覆盖：口径名 → {引擎常量名: 值}。与开关组合使用（开关全 False 时阈值无效）。
# 用途：消融显示「类型A 回踩缩量闸」是唯一正期望的闸，但其增量收益集中在
#       pull_vol_ratio ∈ (0.50, 0.70] 区间（+0.81%/胜率60%），>1.0 才转负 →
#       需要区分「完全关闭」与「把阈值从 0.50 放宽到 0.60/0.70/0.85」两种修法。
THRESH_OVERRIDE = {
    "old": {},
    "new": {},
    "no_A_PULL": {},
    "keep_core": {},
    "keep_A_STALL": {},
    "keep_A_STALL_B_BREAK": {},
    "keep_A_STALL_PERSIST": {},
    "keep_A_STALL_DIST": {},
    "keep_A_STALL_B_PULL": {},
    "rec": {},
    "rec_noBpull": {},
    "rec_dist_on": {},
    "rec_persist_on": {},
    "rec_all_off_but_A": {},
    # ── 第三轮：阈值放宽口径（保留 A_PULL 闸开启，只改阈值）──
    "thr060": {"W7_T0_SHRINK": 0.60},
    "thr070": {"W7_T0_SHRINK": 0.70},
    "thr085": {"W7_T0_SHRINK": 0.85},
    "thr100": {"W7_T0_SHRINK": 1.00},
    "thr070_all": {"W7_T0_SHRINK": 0.70, "W7_T0_SHRINK_B": 0.85},
    "stall070": {"W7_T0_STALL_VOLR": 0.70},
    "stall085": {"W7_T0_STALL_VOLR": 0.85},
    "stall100": {"W7_T0_STALL_VOLR": 1.00},
    # ── 第六轮：组合口径（A_PULL 阈值 0.85 + A_STALL 阈值放宽）──
    "c085_s070": {"W7_T0_SHRINK": 0.85, "W7_T0_STALL_VOLR": 0.70},
    "c085_s085": {"W7_T0_SHRINK": 0.85, "W7_T0_STALL_VOLR": 0.85},
    "c085_s100": {"W7_T0_SHRINK": 0.85, "W7_T0_STALL_VOLR": 1.00},
}

# 引擎里可被覆盖的阈值常量及其原始默认值（用于口径间完整重置）
_BASE_THRESH = {
    "W7_T0_SHRINK": 0.50,
    "W7_T0_SHRINK_B": 0.70,
    "W7_T0_STALL_VOLR": 0.55,
    "W7_T0_BREAK_VOLR": 2.20,
}


def load_engine(path=ENGINE):
    if SOLO not in sys.path:
        sys.path.insert(0, SOLO)
    modname = os.path.splitext(os.path.basename(path))[0]
    if modname in sys.modules:
        return sys.modules[modname]
    return importlib.import_module(modname)


def set_caliber(mod, name):
    """设置六道闸 + 阈值覆盖。

    阈值覆盖必须**每次完整重置**（先还原到引擎默认值，再叠加该口径的覆盖），
    否则口径之间会互相污染。
    """
    for k, v in CALIBERS[name].items():
        setattr(mod, k, v)
    for k, v in _BASE_THRESH.items():
        setattr(mod, k, v)
    for k, v in THRESH_OVERRIDE.get(name, {}).items():
        setattr(mod, k, v)


def _worker_init(engine_path, ctx, dates, active=None):
    global _MOD, _FAST, _READER, _CTX, _DATES, _ACTIVE
    _MOD = load_engine(engine_path)
    import _w7_novol_fast
    _FAST = _w7_novol_fast
    _READER = _MOD.CacheReader()
    _READER.load_fina()
    _CTX = ctx
    _DATES = dates
    if active:
        _ACTIVE = dict(active)


def _load_chunk(mod, reader, codes, start, end):
    if not codes:
        return {}
    ph = ",".join(["?"] * len(codes))
    q = (mod._BARS_SELECT +
         " WHERE d.ts_code IN (%s) AND d.trade_date>=? AND d.trade_date<=? ORDER BY d.ts_code,d.trade_date" % ph)
    df = pd.read_sql_query(q, reader.conn, params=list(codes) + [start, end])
    if df.empty:
        return {}
    df = df.dropna(subset=["close", "high", "low", "vol"]).copy()
    numeric = [c for c in df.columns if c not in ("ts_code", "trade_date")]
    for col in numeric:
        if not pd.api.types.is_numeric_dtype(df[col]):
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return {str(c): g.reset_index(drop=True) for c, g in df.groupby("ts_code", sort=False)}


def _asof_ige(mod, asof):
    import re
    d = mod.IGE_OUT_DIR
    if not os.path.isdir(d):
        return {}
    files = sorted(f for f in os.listdir(d) if re.fullmatch(r"ige_full_\d{8}\.csv", f))
    cands = [f for f in files if f[9:17] <= str(asof)]
    if not cands:
        return {}
    df = pd.read_csv(os.path.join(d, cands[-1]), encoding="utf-8-sig", dtype={"code": str})
    if "code" not in df.columns or "ige_adj" not in df.columns:
        return {}
    out = {}
    for rec in df[["code", "ige_adj"]].to_dict("records"):
        code = str(rec.get("code") or "").strip()
        v = rec.get("ige_adj")
        if code and isinstance(v, (int, float)) and not pd.isna(v):
            out[code] = float(v)
    return out


def _uni_of(mod, reader, D):
    if D not in _UNI:
        uni_df = reader.universe(D)
        uni = {}
        for rec in uni_df[["ts_code", "name", "industry"]].to_dict("records"):
            code = str(rec.get("ts_code") or "")
            if code:
                uni[code] = (str(rec.get("name") or code), str(rec.get("industry") or ""))
        _UNI[D] = uni
    return _UNI[D]


def _process_code(code, df_full):
    mod, reader, fast = _MOD, _READER, _FAST
    rows = []
    if df_full is None or df_full.empty or len(df_full) < mod.MIN_BARS:
        return rows
    df_full = df_full[df_full.trade_date.astype(str) >= mod.DATA_START].reset_index(drop=True)
    if len(df_full) < mod.MIN_BARS:
        return rows
    basic = reader.basic.loc[code] if code in reader.basic.index else {}
    list_date = str(basic.get("list_date", "")) if hasattr(basic, "get") else ""

    sp = fast.StockPrep(mod, df_full, code)
    ps_cache = {}
    dates_all = df_full.trade_date.astype(str).to_numpy()
    row_idx = {d: i for i, d in enumerate(dates_all)}
    price = mod._qfq_price(df_full).astype(float)
    price_map = dict(zip(dates_all, price))
    lows_all = df_full.low.astype(float).to_numpy()

    for D in _DATES:
        ctx = _CTX.get(D)
        if ctx is None:
            continue
        uni = _uni_of(mod, reader, D)
        if code not in uni:
            continue
        name, industry = uni[code]
        if "ST" in name.upper() or "退" in name:
            continue
        if list_date and list_date.isdigit() and int(list_date) > int(D) - 365:
            continue
        k = row_idx.get(D)
        if k is None or k + 1 < mod.MIN_BARS:
            continue

        anchor = fast.find_event_anchor_np(mod, sp, k, ps_cache)
        if anchor is None:
            anchor = fast.find_activation_anchor_np(mod, sp, k)
            if anchor is None:
                continue
        event_idx, ep = anchor

        # 预筛：new 口径探针（最宽松）→ old ⊆ new 已验证，不会漏
        set_caliber(mod, "new")
        t0_open, t0_close, t0_vol = (float(sp.o[event_idx]), float(sp.c[event_idx]),
                                     float(sp.vol[event_idx]))
        adjf_D = sp.adjf_at(k)
        raw60 = sp.ma_raw[60][k]
        ma60_end = float(raw60 / adjf_D) if raw60 == raw60 else 0.0
        ph_hit, _ = fast.t0_pullback_hold_np(mod, sp, event_idx, k, False, False,
                                             ma60_end, t0_open, t0_close, t0_vol)
        rb_hit, _ = fast.t0_rebreak_np(mod, sp, event_idx, k, False, False,
                                       ma60_end, t0_open, t0_close, t0_vol)
        if not (ph_hit or rb_hit):
            continue

        sub = df_full.iloc[:k + 1].copy()
        sub = mod._fill_ma_columns(sub, price=mod._qfq_price(sub))
        ige = _IGE.get(D)
        if ige is None:
            ige = _asof_ige(mod, D)
            _IGE[D] = ige
        ige_v = ige.get(code)
        for cal in _ACTIVE:
            set_caliber(mod, cal)
            res = mod.analyze(code, name, industry, sub, ctx["anchors"], reader=reader,
                              mkt=ctx["mkt"], sector_strength={}, sector_growth={},
                              ige_adj=ige_v, event_hint=(event_idx, ep))
            if not res:
                continue
            res["market_regime"] = ctx["regime"]
            st, fail_line, fail_name = mod.w7_exec_status(res)
            ok, _why = mod.w7_quality_gate(res)
            if not ok or res["state"] not in mod.ACTION_BUY_STATES:
                continue
            rows.append({
                "caliber": cal, "date": D, "code": code, "name": name,
                "state": res["state"], "type": res["type"],
                "close": res["close"], "volr": round(res["volr"], 4),
                "amount_wan": round(res["amount"] / 10.0, 1),
                "t0_open": res.get("t0_open"), "t0_close": res.get("t0_close"),
                "pull_low": res.get("pull_low"),
                "pull_vol_ratio": round(res.get("pull_vol_ratio", 0.0), 4),
                "break_volr": round(res.get("break_volr", 0.0), 4),
                "buy": res["buy"], "exec": st,
                "base_score": round(res["base_score"], 2), "t120": round(res["t120"], 2),
                "regime": ctx["regime"], "level": res["level"],
                "fail_line": fail_line, "fail_name": fail_name,
            })
    # 事后表现
    idx = _CTX["_index"]
    idx_dates = list(idx.keys())
    idx_row = {d: i for i, d in enumerate(idx_dates)}
    for r in rows:
        d0 = r["date"]
        i0 = row_idx.get(d0)
        if i0 is not None and d0 in price_map and price_map[d0] > 0:
            p0 = price_map[d0]
            for h in (1, 3, 5, 10):
                j = i0 + h
                r["ret%d" % h] = (round((price_map[dates_all[j]] / p0 - 1) * 100, 3)
                                  if j < len(dates_all) else None)
        i0i = idx_row.get(d0)
        if i0i is not None and idx[d0] > 0:
            for h in (1, 3, 5, 10):
                j = i0i + h
                r["mkt%d" % h] = (round((idx[idx_dates[j]] / idx[d0] - 1) * 100, 3)
                                  if j < len(idx_dates) else None)
        hit = None
        if i0 is not None:
            fl = r.get("fail_line") or 0
            if fl:
                for j in range(i0 + 1, min(i0 + 11, len(dates_all))):
                    if lows_all[j] <= fl:
                        hit = str(dates_all[j])
                        break
        r["stop_hit_date"] = hit
    return rows


def _process_chunk(codes):
    mod, reader = _MOD, _READER
    frames = _load_chunk(mod, reader, codes, mod.DATA_START, _DATES[-1] if _DATES else "")
    out = []
    for code in codes:
        out.extend(_process_code(code, frames.get(code)))
    return out


def _worker_map(chunk):
    return _process_chunk(chunk)


def build_ctx(reader, mod, dates, end_date):
    ctx = {}
    for D in dates:
        anchors = {}
        for label, (acode, adate) in mod.ANCHORS.items():
            adf = reader.bars_sql(acode, D)
            anchors[label] = mod.anchor_features(adf, adate)
        mdates, mvals = reader.market_curve(D)
        ctx[D] = {"anchors": anchors, "mkt": mod.MarketCtx(mdates, mvals),
                  "regime": mod.market_regime(reader.conn, D)}
    ctx["_index"] = {str(d): float(c) for d, c in reader.conn.execute(
        "SELECT trade_date, close FROM index_daily_cache WHERE ts_code=? AND trade_date<=? ORDER BY trade_date",
        (mod.MARKET_INDEX_CODE, end_date)).fetchall()}
    return ctx


def summarize(df):
    out = {}
    for cal in _ACTIVE:
        g0 = df[df.caliber == cal]
        rec = {"n": int(len(g0)), "by_state": {}}
        for st, g in g0.groupby("state"):
            rec["by_state"][st] = int(len(g))
        for h in (1, 3, 5, 10):
            r = g0["ret%d" % h]
            m = g0["mkt%d" % h]
            if len(g0) and r.notna().any():
                rec["ret%d" % h] = round(float(r.mean()), 3)
                rec["ex%d" % h] = round(float((r - m).mean()), 3)
                rec["win%d" % h] = round(float((r.dropna() > 0).mean() * 100), 1)
        if len(g0) and "stop_hit_date" in g0.columns:
            rec["stop_rate"] = round(float(g0["stop_hit_date"].notna().mean() * 100), 1)
        if len(g0) and "regime" in g0.columns:
            rec["by_regime"] = {k: int(v) for k, v in g0.regime.value_counts().items()}
        out[cal] = rec
    # 相对 old 的增量
    if "old" in out:
        base = out["old"]["n"]
        for cal in _ACTIVE:
            out[cal]["delta_vs_old"] = out[cal]["n"] - base
    elif "new" in out:
        base = out["new"]["n"]
        for cal in _ACTIVE:
            out[cal]["delta_vs_new"] = out[cal]["n"] - base
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20260803")
    ap.add_argument("--end", default="20261028")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--block", type=int, default=60)
    ap.add_argument("--outdir", default=OUT_DIR)
    ap.add_argument("--calibers", default="", help="逗号分隔的口径子集（默认全部）")
    ap.add_argument("--tag", default="", help="输出文件后缀（避免覆盖首轮结果）")
    ap.add_argument("--step", type=int, default=1,
                    help="交易日抽样步长（每 step 个交易日取 1 个）。"
                         "逐日 as-of 回放的因果性不受抽样影响（每个被选中的日期仍只用 <= 该日的数据），"
                         "但样本量下降 → 用于在更长区间上验证结论的稳健性")
    args = ap.parse_args()

    global _ACTIVE
    if args.calibers:
        want = [c.strip() for c in args.calibers.split(",") if c.strip()]
        bad = [c for c in want if c not in CALIBERS]
        if bad:
            print("[abl] 未知口径：%s" % bad)
            print("[abl] 可用：%s" % ", ".join(CALIBERS))
            return
        _ACTIVE = {c: CALIBERS[c] for c in want}
    print("[abl] 参与口径：%s" % ", ".join(_ACTIVE))

    mod = load_engine()
    reader = mod.CacheReader()
    reader.load_fina()
    dates = [str(r[0]) for r in reader.conn.execute(
        "SELECT DISTINCT trade_date FROM index_daily_cache WHERE ts_code=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
        (mod.MARKET_INDEX_CODE, args.start, args.end)).fetchall()]
    if args.step > 1:
        dates = dates[::args.step]
    print("[abl] 交易日 %d 个：%s ~ %s" % (len(dates), dates[0], dates[-1]), flush=True)
    t0 = time.time()
    ctx = build_ctx(reader, mod, dates, args.end)
    print("[abl] 上下文构建 %.1fs" % (time.time() - t0), flush=True)
    codes = sorted(reader.universe(dates[-1]).ts_code.tolist())
    if args.limit:
        codes = codes[:args.limit]
    chunks = [codes[i:i + args.block] for i in range(0, len(codes), args.block)]
    print("[abl] %d 只 × %d 日，%d 块，%d 进程" %
          (len(codes), len(dates), len(chunks), args.jobs), flush=True)

    all_rows = []
    t0 = time.time()
    with Pool(args.jobs, initializer=_worker_init, initargs=(ENGINE, ctx, dates, _ACTIVE)) as pool:
        for n, rows in enumerate(pool.imap_unordered(_worker_map, chunks, chunksize=1), 1):
            all_rows.extend(rows)
            if n % 10 == 0 or n == len(chunks):
                el = time.time() - t0
                print("[abl] %d/%d 块 用时=%.0fs 预计剩余=%.0fs 行=%d" %
                      (n, len(chunks), el, el / n * (len(chunks) - n), len(all_rows)), flush=True)

    df = pd.DataFrame(all_rows)
    os.makedirs(args.outdir, exist_ok=True)
    sfx = ("_" + args.tag) if args.tag else ""
    if not df.empty:
        df.to_csv(os.path.join(args.outdir, "ablation_signals%s.csv" % sfx),
                  index=False, encoding="utf-8-sig")
    summary = summarize(df) if not df.empty else {}
    summary["_meta"] = {"start": dates[0], "end": dates[-1], "days": len(dates),
                        "codes": len(codes), "calibers": list(_ACTIVE),
                        "elapsed_s": round(time.time() - t0, 1)}
    p = os.path.join(args.outdir, "ablation_summary%s.json" % sfx)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)

    # 打印表
    print("\n%-12s %6s %8s %9s %8s %8s %9s %8s %8s" %
          ("口径", "n", "Δold", "T+3", "T+3超额", "T+5", "T+10", "T+10超额", "T+3胜率"))
    print("-" * 92)
    for cal in _ACTIVE:
        r = summary.get(cal, {})
        print("%-12s %6d %+8d %9s %8s %8s %9s %8s %8s" % (
            cal, r.get("n", 0), r.get("delta_vs_old", 0),
            _f(r.get("ret3")), _f(r.get("ex3")), _f(r.get("ret5")),
            _f(r.get("ret10")), _f(r.get("ex10")), _f(r.get("win3"))))
    print("\n[abl] 输出：%s" % p)


def _f(v):
    return "—" if v is None else "%+.2f" % v


if __name__ == "__main__":
    main()
