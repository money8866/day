# -*- coding: utf-8 -*-
"""
天量翻倍 · 样本外前向跟踪（P0）：冻结阈值台账 + 90/180日对账 + 样本外报告
========================================================================
背景：TIER 画像（TIER1≈35.5% / TIER2≈24.0%）来自样本内事件库
（double_sli_pool_events.csv，D250 全观察），存在 R1/R2 过拟合风险。
本脚本建立 append-only 样本外台账，自 20260611（画像冻结后）起登记实盘触发
事件，按 P0 入场规则做可实现口径对账，与样本内基准对比，衰减>50% 亮红旗。

P0 入场规则（对账口径B，可实现收益）：
  ① 事件日涨停/一字 → 顺延次日开盘价入场；否则事件日收盘入场
  ② 止损线 = max(事件日最低价, 入场价×0.80)，先触即出；跳空低开按开盘价成交
  ③ 入场满 250 交易日未触发 → 收盘强制出场
对账口径A（与样本内可比）：事件日收盘持有，r90/r180/r250、收盘/盘中翻倍首达天数

台账：report_daily/double_forward_ledger.csv（append-only，主键 code+date）
用法：
  python double_forward_track.py --backfill [N]   # 回放重建台账（可带最近N日，默认自20260611全部）
  python double_forward_track.py                  # 每日链路：登记 + 对账 + 报告
  python double_forward_track.py --report         # 仅重新输出报告

冻结约定：TIER 阈值一律调用 _daily_double_scan.tier（本文件不复刻阈值）。
画像一旦修订须新开台账并归档旧台账，禁止用样本外结果反调阈值。
"""
import argparse
import os
import sys
import time
from datetime import datetime
from multiprocessing import Pool

import numpy as np
import pandas as pd

SOLO_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SOLO_DIR)
import _daily_double_scan as S
from w7_second_wave_engine import CacheReader
import bts.data as D

RD = os.path.join(SOLO_DIR, "report_daily")
LEDGER_CSV = os.path.join(RD, "double_forward_ledger.csv")
TRIGGER_CSV = os.path.join(RD, "double_trigger.csv")
REPORT_MD = os.path.join(RD, "double_forward_report.md")

BACKFILL_FROM = 20260611  # 画像冻结（double_analysis.md）后的样本外起点
BASE_T1, BASE_T2 = 0.355, 0.240  # 样本内基准（double_analysis.md，D250 全观察14797事件）
TIER1, TIER2 = "TIER1_核心", "TIER2_基础"
WINDOWS = (90, 180, 250)

REG_COLS = ["code", "name", "subsector", "date", "tier", "close", "pct", "mv_yi",
            "turn_today", "turn_20m", "vol_ratio", "p_vol", "p_turn", "rel_hi250",
            "ma20_x", "ma60_x", "ret60", "streak", "is_limitup", "is_yizi",
            "first_of_cluster", "registered_at"]
RECON_COLS = ["entry_price", "entry_date", "entry_gap", "stop_price", "exit_price",
              "exit_date", "exit_reason", "net_ret", "cur_ret", "mfe_e", "mae_e",
              "hold_days", "status", "r90", "r180", "r250", "r_now",
              "dbl_c_days", "dbl_h_days", "days_since", "recon_date"]
LEDGER_COLS = REG_COLS + RECON_COLS


def _read_csv_safe(path):
    if not os.path.exists(path):
        return pd.DataFrame()
    for enc in ("utf-8-sig", "gbk", "utf-8"):
        try:
            return pd.read_csv(path, dtype={"code": str, "date": str, "entry_date": str,
                                            "exit_date": str, "registered_at": str,
                                            "recon_date": str}, encoding=enc)
        except Exception:
            continue
    return pd.DataFrame()


STR_COLS = ("name", "subsector", "tier", "registered_at", "entry_date",
            "exit_date", "exit_reason", "status", "recon_date")


def _load_ledger():
    led = _read_csv_safe(LEDGER_CSV)
    if led.empty:
        return pd.DataFrame(columns=LEDGER_COLS)
    for col in ("is_limitup", "is_yizi", "first_of_cluster"):
        if col in led.columns:
            led[col] = led[col].map(lambda v: str(v).strip().lower() in ("true", "1", "1.0"))
    led = led.reindex(columns=LEDGER_COLS)
    for col in STR_COLS:  # 空台账列默认 float64，须转 object 才能写入字符串
        led[col] = led[col].astype(object)
    return led


def _save_ledger(led):
    led = led.reindex(columns=LEDGER_COLS)
    led.to_csv(LEDGER_CSV, index=False, encoding="utf-8-sig")


def _latest_day():
    probe = CacheReader()
    latest = str(probe.conn.execute("SELECT MAX(trade_date) FROM daily_cache").fetchone()[0])
    probe.close()
    return latest


def _merge(led, df_new):
    """append-only 合并，主键 code+date，现有台账优先"""
    if df_new.empty:
        return led
    df_new = df_new.reindex(columns=LEDGER_COLS)
    led = led.reindex(columns=LEDGER_COLS)
    led["_k"] = led["code"].astype(str) + "|" + led["date"].astype(str)
    df_new["_k"] = df_new["code"].astype(str) + "|" + df_new["date"].astype(str)
    out = pd.concat([led, df_new[~df_new["_k"].isin(set(led["_k"]))]], ignore_index=True)
    out = out.drop_duplicates("_k", keep="first").drop(columns="_k")
    return out.sort_values(["date", "code"]).reset_index(drop=True)


# ---------------------------------------------------------------- 回填（回放重建）

def _init_pool(sub, name):
    S._SUB.clear()
    S._SUB.update(sub)
    S._NAME.clear()
    S._NAME.update(name)


def _replay_worker(job):
    chunk, days, latest = job
    reader = CacheReader()
    reader.load_all(latest, codes=chunk, min_date="20230101")
    out = []
    for code in chunk:
        df = reader.frames.get(code)
        if df is None or df.empty:
            continue
        arr = df.trade_date.astype(str).to_numpy()
        for d in days:
            k = int(np.searchsorted(arr, d, side="right"))
            if k == 0 or arr[k - 1] != d:
                continue
            rec = S._last_event_rec(code, df.iloc[:k], d)
            if rec:
                out.append(rec)
    reader.close()
    return out


def cmd_backfill(last_n=None):
    probe = CacheReader()
    latest = str(probe.conn.execute("SELECT MAX(trade_date) FROM daily_cache").fetchone()[0])
    days = [str(r[0]) for r in probe.conn.execute(
        "SELECT DISTINCT trade_date FROM daily_cache WHERE trade_date > ? AND trade_date <= ? ORDER BY trade_date",
        (BACKFILL_FROM, latest)).fetchall()]
    probe.close()
    if last_n:
        days = days[-int(last_n):]
    print(f"回放窗口: {days[0]} ~ {days[-1]} 共{len(days)}个交易日", flush=True)

    panel = S.get_subsector_top5()
    sub = panel[["ts_code", "subsector", "name"]].drop_duplicates("ts_code")
    sub["ts_code"] = sub["ts_code"].astype(str).str.strip()
    submap = dict(zip(sub.ts_code, sub.subsector))
    namemap = dict(zip(sub.ts_code, sub.name))
    codes = sorted(submap.keys())
    nw = 8
    chunks = [list(c) for c in np.array_split(np.asarray(codes, dtype=object), nw) if len(c)]
    t0 = time.time()
    with Pool(nw, initializer=_init_pool, initargs=(submap, namemap)) as pool:
        batches = pool.map(_replay_worker, [(c, days, latest) for c in chunks])
    recs = [r for b in batches for r in b]
    print(f"回放事件总数={len(recs)} 耗时={time.time() - t0:.0f}s", flush=True)
    if not recs:
        return False
    df = pd.DataFrame(recs)
    df["tier"] = df.apply(S.tier, axis=1)
    df["registered_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    d0 = df[df.date == latest]
    print(f"[校验锚点] {latest}: 回放事件={len(d0)} "
          f"TIER1={int((d0.tier == TIER1).sum())} TIER2={int((d0.tier == TIER2).sum())} "
          f"（应与当日扫描 double_trigger.csv 一致）", flush=True)

    led = _load_ledger()
    before = len(led)
    merged = _merge(led, df)
    _save_ledger(merged)
    print(f"台账: {before} → {len(merged)}（新增 {len(merged) - before}），{LEDGER_CSV}", flush=True)
    return True


# ---------------------------------------------------------------- 每日登记

def _fill_yizi(df):
    """trigger.csv 不含 is_yizi，用 TDX 当日 K 线补算（一字 = OHLC 四价相等且当日上涨）"""
    if df.empty:
        return
    if "is_yizi" not in df.columns:
        df["is_yizi"] = np.nan
    miss = df[df["is_yizi"].isna()]
    for idx, r in miss.iterrows():
        try:
            d = D.tdx_daily(str(r["code"]))
            if d is None or d.empty:
                continue
            arr = d.trade_date.astype(str).to_numpy()
            k = int(np.searchsorted(arr, str(r["date"]), side="right")) - 1
            if k < 0 or arr[k] != str(r["date"]):
                continue
            row = d.iloc[k]
            get = lambda x: pd.to_numeric(row[x], errors="coerce") if x in d.columns else np.nan
            o, h, l, c = get("open"), get("high"), get("low"), get("close")
            if all(np.isfinite(v) for v in (o, h, l, c)) and o == h == l == c and float(r.get("pct", 0) or 0) > 5:
                df.at[idx, "is_yizi"] = True
        except Exception as e:
            print(f"  is_yizi 补算失败 {r['code']}: {e}", flush=True)


def cmd_register():
    trg = _read_csv_safe(TRIGGER_CSV)
    if trg.empty:
        print("未找到 double_trigger.csv（先跑 _daily_double_scan.py），跳过登记", flush=True)
        return False
    tier_s = trg["tier"].astype(str).str.strip()
    hits = trg[(tier_s == TIER1) | (tier_s == TIER2)].copy()
    if hits.empty:
        print("今日 trigger 无 TIER 事件，无需登记", flush=True)
        return True
    _fill_yizi(hits)
    hits["registered_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    led = _load_ledger()
    before = len(led)
    merged = _merge(led, hits)
    _save_ledger(merged)
    print(f"登记 TIER 事件 {len(merged) - before} 个（trigger 全事件 {len(trg)}，台账 {before}→{len(merged)}）", flush=True)
    return True


# ---------------------------------------------------------------- 对账

def _simulate(rec, o, h, l, c, arr, i0, n):
    f = {k: np.nan for k in RECON_COLS if k not in ("entry_date", "exit_date", "exit_reason", "recon_date")}
    f.update({"entry_date": "", "exit_date": "", "exit_reason": "", "status": "open"})
    if i0 < 0 or i0 >= n:
        f["status"] = "no_data"
        return f
    c0 = float(c[i0])
    if not np.isfinite(c0) or c0 <= 0:
        f["status"] = "no_data"
        return f
    f["days_since"] = n - 1 - i0
    f["r_now"] = round(float(c[-1] / c0 - 1), 4)
    lu = bool(rec.get("is_limitup")) or bool(rec.get("is_yizi"))
    if lu and i0 + 1 >= n:
        f["status"] = "pending_entry"  # 涨停/一字且事件日即最新日，次日开盘再入场
        return f
    if lu:
        ei, entry = i0 + 1, float(o[i0 + 1])  # 规则①顺延次日开盘
    else:
        ei, entry = i0, c0
    if not np.isfinite(entry) or entry <= 0:
        f["status"] = "pending_entry"
        return f
    f["entry_price"] = round(entry, 3)
    f["entry_date"] = str(arr[ei])
    f["entry_gap"] = round(float(entry / c0 - 1), 4)
    stop = max(float(l[i0]), entry * 0.80)  # 规则②：跌破事件日最低 或 -20%，先触即出
    f["stop_price"] = round(stop, 3)

    ex = None
    for j in range(ei + 1, n):
        if np.isfinite(o[j]) and o[j] <= stop:
            ex = (j, float(o[j]), "stop_gap")
            break
        if np.isfinite(l[j]) and l[j] <= stop:
            ex = (j, stop, "stop_hit")
            break
        if j - ei >= 250:  # 规则③强制出场
            ex = (j, float(c[j]), "force_250")
            break
    hh, ll = h[ei:], l[ei:]
    if len(hh) and np.isfinite(hh).any():
        f["mfe_e"] = round(float(np.nanmax(hh) / entry - 1), 4)
    if len(ll) and np.isfinite(ll).any():
        f["mae_e"] = round(float(np.nanmin(ll) / entry - 1), 4)
    if ex:
        j, px, rs = ex
        f["exit_date"], f["exit_price"], f["exit_reason"] = str(arr[j]), round(px, 3), rs
        f["net_ret"], f["hold_days"], f["status"] = round(px / entry - 1, 4), j - ei, rs
    else:
        f["hold_days"] = n - 1 - ei
        f["cur_ret"] = round(float(c[-1] / entry - 1), 4)

    for N in WINDOWS:  # 口径A：事件日收盘持有，与样本内可比
        if n - 1 - i0 >= N and np.isfinite(c[i0 + N]):
            f[f"r{N}"] = round(float(c[i0 + N] / c0 - 1), 4)
    cc, chh = c[i0 + 1:], h[i0 + 1:]
    p1 = np.flatnonzero(np.isfinite(cc) & (cc >= 2 * c0))
    p2 = np.flatnonzero(np.isfinite(chh) & (chh >= 2 * c0))
    if len(p1):
        f["dbl_c_days"] = int(p1[0]) + 1
    if len(p2):
        f["dbl_h_days"] = int(p2[0]) + 1
    return f


def _recon_worker(job):
    items, latest = job
    out = []
    for code, rows in items:
        try:
            df = D.tdx_daily(code)
            if df is None or df.empty:
                df = D.load_daily(code, latest, lookback_bars=2000)
            if df is None or df.empty:
                continue
            arr = df.trade_date.astype(str).to_numpy()
            col_o = df["open"] if "open" in df.columns else df["close"]
            o = pd.to_numeric(col_o, errors="coerce").to_numpy(float)
            h = pd.to_numeric(df["high"], errors="coerce").to_numpy(float)
            l = pd.to_numeric(df["low"], errors="coerce").to_numpy(float)
            c = pd.to_numeric(df["close"], errors="coerce").to_numpy(float)
            for rec in rows:
                i0 = int(np.searchsorted(arr, str(rec["date"]), side="right")) - 1
                if i0 < 0 or arr[i0] != str(rec["date"]):
                    continue
                out.append((code, str(rec["date"]), _simulate(rec, o, h, l, c, arr, i0, len(arr))))
        except Exception as e:
            print(f"  对账失败 {code}: {e}", flush=True)
    return out


def cmd_reconcile():
    led = _load_ledger()
    if led.empty:
        print("台账为空，先 --backfill 回填", flush=True)
        return False
    latest = _latest_day()
    done = {"stop_gap", "stop_hit", "force_250", "no_data"}
    mask = ~led["status"].isin(done)
    todo = led[mask]
    if todo.empty:
        print("台账事件均已出场/失效，无需对账", flush=True)
        return True
    groups = {}
    for _, r in todo.iterrows():
        groups.setdefault(str(r["code"]), []).append(r.to_dict())
    items = sorted(groups.items())
    nw = 8
    chunks = [list(c) for c in np.array_split(np.asarray(items, dtype=object), nw) if len(c)]
    t0 = time.time()
    with Pool(nw) as pool:
        batches = pool.map(_recon_worker, [(c, latest) for c in chunks])
    upd = {}
    for b in batches:
        for code, date, fields in b:
            upd[(code, date)] = fields
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    for i in led.index:
        k = (str(led.at[i, "code"]), str(led.at[i, "date"]))
        if k in upd:
            for kk, vv in upd[k].items():
                if vv is not None and not (isinstance(vv, float) and np.isnan(vv)):
                    led.at[i, kk] = vv
            led.at[i, "recon_date"] = now
    _save_ledger(led)
    print(f"对账 {len(upd)}/{len(todo)} 事件（最新 {latest}）耗时={time.time() - t0:.0f}s", flush=True)
    return True


# ---------------------------------------------------------------- 报告

def _pct(v, signed=True):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "-"
    return f"{v * 100:+.1f}%" if signed else f"{v * 100:.1f}%"


def _flag(oos, base, n):
    if n < 20:
        return f"跟踪中（可比样本 {n}/20）"
    if oos is None:
        return "样本不足"
    ratio = oos / base if base else np.nan
    if ratio < 0.5:
        return f"🚩 红旗：样本外仅为样本内的 {ratio * 100:.0f}%（衰减>50%）"
    if ratio < 0.8:
        return f"🟡 衰减警惕：样本外为样本内的 {ratio * 100:.0f}%"
    return f"🟢 正常区间：样本外为样本内的 {ratio * 100:.0f}%"


def cmd_report(latest=None):
    led = _load_ledger()
    if led.empty:
        print("台账为空", flush=True)
        return False
    latest = latest or _latest_day()
    L = []
    A = L.append
    A("# 天量翻倍 · 样本外前向跟踪报告（P0）\n")
    A(f"- 最新交易日：{latest} ｜ 台账：`double_forward_ledger.csv`（append-only，主键 code+date）")
    A(f"- TIER 阈值冻结于 `_daily_double_scan.tier`（画像出处 double_analysis.md），样本外起点 {BACKFILL_FROM}")
    A(f"- 样本内基准（D250 全观察14797事件）：TIER1≈35.5%（2025年≈55.2%）、TIER2≈24.0%，口径=事件日后250日内收盘≥2×事件收盘")
    A(f"- 报告生成：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n")

    A("## 1. 台账总览\n")
    n1, n2 = int((led.tier == TIER1).sum()), int((led.tier == TIER2).sum())
    A(f"| 指标 | 值 |\n|---|---|")
    A(f"| 样本外事件总数 | {len(led)} |")
    A(f"| TIER1（核心） | {n1} |")
    A(f"| TIER2（基础） | {n2} |")
    A(f"| 登记区间 | {led['date'].min()} ~ {led['date'].max()} |\n")
    A("### 月度事件分布\n")
    A("| 月份 | 事件 | TIER1 | TIER2 |")
    A("|---|---|---|---|")
    for ym, g in led.groupby(led["date"].astype(str).str[:6]):
        A(f"| {ym} | {len(g)} | {int((g.tier == TIER1).sum())} | {int((g.tier == TIER2).sum())} |")
    A("")

    A("## 2. P0 可实现口径（规则：涨停/一字顺延次日开盘；跌破事件日最低或-20%止损；250日强制）\n")
    st = led["status"].fillna("open").value_counts()
    A("| 状态 | 数量 | 说明 |")
    A("|---|---|---|")
    st_desc = {"stop_gap": "跳空低开触止损", "stop_hit": "盘中触止损", "force_250": "250日强制出场",
               "open": "持有中", "pending_entry": "待入场（涨停顺延）", "no_data": "数据缺失"}
    for k, v in st.items():
        A(f"| {k} | {v} | {st_desc.get(k, '')} |")
    A("")
    exited = led[led["status"].isin(("stop_gap", "stop_hit", "force_250"))]
    if len(exited):
        A(f"### 已出场 {len(exited)} 笔\n")
        win = float((exited["net_ret"] > 0).mean())
        A(f"- 净收益中位 {_pct(exited['net_ret'].median())} ｜ 胜率 {_pct(win, False)} ｜ "
          f"平均持有 {exited['hold_days'].mean():.0f} 交易日")
        for t_, tag in ((TIER1, "TIER1"), (TIER2, "TIER2")):
            g = exited[exited["tier"] == t_]
            if len(g):
                A(f"- {tag}：{len(g)} 笔，净收益中位 {_pct(g['net_ret'].median())}，止损率 "
                  f"{_pct((g['status'].isin(('stop_gap', 'stop_hit'))).mean(), False)}")
        A("")
        A("| 日期 | 代码 | 名称 | 层 | 入场价 | 止损线 | 出场日 | 出场价 | 原因 | 净收益 | 最大浮盈 | 最大浮亏 |")
        A("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for _, r in exited.sort_values("net_ret").iterrows():
            A(f"| {r['date']} | {r['code']} | {r['name']} | {'T1' if r['tier'] == TIER1 else 'T2'} "
              f"| {r['entry_price']} | {r['stop_price']} | {r['exit_date']} | {r['exit_price']} "
              f"| {st_desc.get(r['status'], r['status'])} | {_pct(r['net_ret'])} "
              f"| {_pct(r['mfe_e'])} | {_pct(r['mae_e'])} |")
        A("")
    holding = led[led["status"] == "open"]
    if len(holding):
        A(f"### 持有中 {len(holding)} 笔\n")
        A("| 日期 | 代码 | 名称 | 层 | 入场价 | 浮动收益 | 距事件收盘 | 最大浮盈 | 最大浮亏 | 持有日 |")
        A("|---|---|---|---|---|---|---|---|---|---|")
        for _, r in holding.sort_values("cur_ret").iterrows():
            A(f"| {r['date']} | {r['code']} | {r['name']} | {'T1' if r['tier'] == TIER1 else 'T2'} "
              f"| {r['entry_price']} | {_pct(r['cur_ret'])} | {_pct(r['r_now'])} "
              f"| {_pct(r['mfe_e'])} | {_pct(r['mae_e'])} | {int(r['hold_days']) if pd.notna(r['hold_days']) else '-'} |")
        A("")

    A("## 3. 口径A对账（事件日收盘持有，与样本内可比）\n")
    A("| 层 | n | r250已到期 | r250中位 | r180已到期 | r180中位 | r90已到期 | r90中位 | 事件后已收盘翻倍 | 事件后已盘中翻倍 |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    for t_, tag, base in ((TIER1, "TIER1", BASE_T1), (TIER2, "TIER2", BASE_T2), (None, "全部", None)):
        g = led if t_ is None else led[led["tier"] == t_]
        if not len(g):
            continue
        r250 = g["r250"].dropna()
        r180 = g["r180"].dropna()
        r90 = g["r90"].dropna()
        dc = int(g["dbl_c_days"].notna().sum())
        dh = int(g["dbl_h_days"].notna().sum())
        A(f"| {tag} | {len(g)} | {len(r250)} | {_pct(r250.median()) if len(r250) else '-'} "
          f"| {len(r180)} | {_pct(r180.median()) if len(r180) else '-'} "
          f"| {len(r90)} | {_pct(r90.median()) if len(r90) else '-'} | {dc}（{_pct(dc / len(g), False)}） | {dh}（{_pct(dh / len(g), False)}） |")
    A("")
    A("> 注：事件后翻倍比例为任意窗口（登记以来）的累计观察，与样本内 250 日口径不可直接对比，仅作早期信号。"
      "正式衰减判定需 r250 到期样本 ≥20。\n")

    A("## 4. 样本内 vs 样本外（红旗判定）\n")
    A("| 层 | 样本内基准 | 样本外早期观察 | 判定 |")
    A("|---|---|---|---|")
    for t_, tag, base in ((TIER1, "TIER1", BASE_T1), (TIER2, "TIER2", BASE_T2)):
        g = led[led["tier"] == t_]
        dc = int(g["dbl_c_days"].notna().sum())
        early = dc / len(g) if len(g) else None
        n250 = int(g["r250"].notna().sum())
        oos = None
        if n250 >= 20:
            oos = float((g["r250"] >= 1.0).mean())
        note = _flag(oos, base, n250)
        A(f"| {tag} | {_pct(base, False)} | {(_pct(oos, False) if oos is not None else '窗口未满')}"
          f"｜早期已翻倍 {_pct(early, False) if early is not None else '-'} | {note} |")
    A("")

    A("## 5. TIER1 明细\n")
    t1 = led[led["tier"] == TIER1].sort_values("date")
    if len(t1):
        A("| 日期 | 代码 | 名称 | 行业 | 流通亿 | 换手% | 回撤% | 状态 | 净收益/浮动 | 最大浮盈 | 翻倍天数 |")
        A("|---|---|---|---|---|---|---|---|---|---|---|")
        for _, r in t1.iterrows():
            ret = r["net_ret"] if r["status"] in ("stop_gap", "stop_hit", "force_250") else r["cur_ret"]
            A(f"| {r['date']} | {r['code']} | {r['name']} | {r['subsector']} | {r['mv_yi']} "
              f"| {r['turn_today']} | {r['rel_hi250'] * 100:.0f}% | {st_desc.get(r['status'], r['status'])} "
              f"| {_pct(ret)} | {_pct(r['mfe_e'])} "
              f"| {int(r['dbl_c_days']) if pd.notna(r['dbl_c_days']) else '-'} |")
    else:
        A("（暂无 TIER1 事件）")
    L_text = "\n".join(L) + "\n"
    with open(REPORT_MD, "w", encoding="utf-8") as f:
        f.write(L_text)
    print(f"报告已写入 {REPORT_MD}", flush=True)
    print(L_text, flush=True)
    return True


# ---------------------------------------------------------------- 入口

def main():
    ap = argparse.ArgumentParser(description="天量翻倍样本外前向跟踪")
    ap.add_argument("--backfill", nargs="?", const=0, default=None, type=int,
                    help="回放重建台账（可带最近N日，默认自20260611全部）")
    ap.add_argument("--report", action="store_true", help="仅输出报告")
    args = ap.parse_args()
    if args.backfill is not None:
        cmd_backfill(args.backfill or None)
        return
    if args.report:
        cmd_report()
        return
    cmd_register()
    cmd_reconcile()
    cmd_report()


if __name__ == "__main__":
    main()
