# -*- coding: utf-8 -*-
"""W7「T0 后不对量能进行约束」全市场配对回测（同锚同日期双口径对比）。

目的
----
20261028 用户口径：T0 锚定日之后不再用量能约束形态。本脚本用「配对回测」量化这次改造：
同一批 T0 锚、同一批交易日下，同时跑两套口径，对比信号数量与事后表现。

口径定义
--------
A. old（改造前基准）：六个开关全 True。
   该组合逐条等价于改造前的引擎（备份 w7_second_wave_engine.py.bak_v7_20261028）：
     · 四个探测器量能硬门槛全部生效；
     · w7_priority 的量能因子按原方向判定；
     · w7_exec_status 类型B 走通用量比阀门；
     · hvt_type 类型B 走通用分支（dist_risk≥60 可否决）；
     · persistent_sell 含 T0 后量能 conjunct 且阻断 T0 探测器。
   → 用 _w7_novol_selftest.py 与备份引擎逐条对拍验证（要求 100% 一致）。
B. new（取消 T0 后量能约束）：六个开关全 False。
   量能彻底退出「信号生死」判定，只保留记录（pull_vol_ratio / break_volr / stall_volr）与排序参考。

口径一致性保证
--------------
· 锚点每 (股票, 交易日) 只算一次，两口径共用 → 锚点一致率天然 100%（脚本另做独立校验）。
· 同一份截断序列（as-of）+ 同一份 MA/前复权 + 同一份财务(as_of) + 同一份 IGE(严格 as-of)
  + 同一市场环境判定 + 同一份锚点参照特征 → 两口径唯一差异就是那六个开关。
· 探针/锚点用 numpy 快版（_w7_novol_fast.py），与引擎原生逐条对拍 100% 一致
  （_w7_novol_check.py：6948 条判定零差异，MA 最大差 1.4e-14）。

性能架构（20261029 实测优化，见 _w7_novol_prof5.py）
--------------------------------------------------
  原生路径每 (股,日) 成本：锚点 36.95ms + 双探针 10.31ms + analyze 27.17ms
  现路径：
    · extreme_event 全历史向量化预算（196x，逐条等价）
    · 锚点/双探针 numpy 快版 + preshift 按 event_idx 缓存
    · MA/前复权全历史预计算 + 逐日缩放（4.4x，逐条等价）
    · **探针预筛**：先用 numpy 探针判「可能命中」，不命中直接跳过 analyze
      （new 口径探针命中率仅 5.0% → analyze 调用量降约 95%）
    · 只在探针命中时调用引擎原生 analyze，保证输出字段与生产完全一致

产出
----
_w7_novol_out/eval_signals.csv   每个 (日期, 代码, 口径) 的过门信号 + 事后表现
_w7_novol_out/eval_summary.json  汇总统计
report_daily/w7_novol_eval_*.md  人读报告
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

# 六道开关 → 口径映射
GATE_KEYS = ("W7_GATE_A_PULL_SHRINK", "W7_GATE_A_STALL_VOLR", "W7_GATE_B_PULL_SHRINK",
             "W7_GATE_B_BREAK_VOLR", "W7_GATE_B_DIST_RISK", "W7_GATE_PERSIST_SELL")
CALIBERS = {"old": True, "new": False}

_MOD = None
_FAST = None
_READER = None
_CTX = {}
_DATES = []
_UNI = {}
_IGE = {}


def _stable_hash(s):
    """跨进程稳定的字符串哈希（Python 内置 hash 对 str 有随机盐，worker 间不一致）。"""
    h = 0
    for ch in s:
        h = (h * 131 + ord(ch)) & 0xFFFFFFFF
    return h


def load_engine(path=ENGINE):
    """按「真实模块名」加载引擎。

    关键：Windows spawn 语义下，initargs 在子进程 initializer 之前就要反序列化。
    ctx 里含 MarketCtx 实例 → pickle 时记的是类所在的模块名。若用
    spec_from_file_location('w7eng_eval', ...) 这种虚拟模块名，子进程 import 不到
    'w7eng_eval' → ModuleNotFoundError（20261028 实测）。
    故把 SOLO 加入 sys.path 并用真实模块名导入，子进程继承 sys.path 即可解析。
    """
    if SOLO not in sys.path:
        sys.path.insert(0, SOLO)
    modname = os.path.splitext(os.path.basename(path))[0]
    if modname in sys.modules:
        return sys.modules[modname]
    return importlib.import_module(modname)


def set_caliber(mod, caliber):
    """切换六道开关到指定口径（True=old 基准 / False=new 取消量能约束）。"""
    v = CALIBERS[caliber]
    for k in GATE_KEYS:
        setattr(mod, k, v)


# ---------------------------------------------------------------- worker 侧
def _worker_init(engine_path, ctx, dates):
    global _MOD, _FAST, _READER, _CTX, _DATES
    _MOD = load_engine(engine_path)
    import _w7_novol_fast
    _FAST = _w7_novol_fast
    _READER = _MOD.CacheReader()
    _READER.load_fina()
    _CTX = ctx
    _DATES = dates


def _load_chunk(mod, reader, codes, start, end):
    """批量加载一个代码块的日线（一次 SQL IN 查询，比逐票 bars_sql 快 3 倍以上）。"""
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
    out = {}
    for code, g in df.groupby("ts_code", sort=False):
        out[str(code)] = g.reset_index(drop=True)
    return out


def _asof_ige(mod, asof):
    """严格 as-of 版 IGE 快照：只取快照日期 <= asof 的最近一次（生产口径会回退到 files[-1]，属前视）。"""
    import re
    d = mod.IGE_OUT_DIR
    if not os.path.isdir(d):
        return {}
    files = sorted(f for f in os.listdir(d)
                   if re.fullmatch(r"ige_full_\d{8}\.csv", f))
    cands = [f for f in files if f[9:17] <= str(asof)]
    if not cands:
        return {}
    df = pd.read_csv(os.path.join(d, cands[-1]), encoding="utf-8-sig", dtype={"code": str})
    if "code" not in df.columns or "ige_adj" not in df.columns:
        return {}
    out = {}
    for rec in df[["code", "ige_adj"]].to_dict("records"):
        code = str(rec.get("code") or "").strip()
        if not code:
            continue
        v = rec.get("ige_adj")
        if isinstance(v, (int, float)) and not pd.isna(v):
            out[code] = float(v)
    return out


def _get_ige(mod, asof):
    """IGE 快照按 as-of 缓存（同一交易日多个 worker 共用文件读）。"""
    key = str(asof)
    if key not in _IGE:
        _IGE[key] = _asof_ige(mod, asof)
    return _IGE[key]


def _uni_of(mod, reader, D):
    """当日股池截面（生产同口径）：reader.universe(D)，按需缓存。"""
    if D not in _UNI:
        uni_df = reader.universe(D)
        uni = {}
        for rec in uni_df[["ts_code", "name", "industry"]].to_dict("records"):
            code = str(rec.get("ts_code") or "")
            if not code:
                continue
            uni[code] = (str(rec.get("name") or code), str(rec.get("industry") or ""))
        _UNI[D] = uni
    return _UNI[D]


def _process_code(code, df_full=None):
    """单票逐日 as-of 回放，双口径并行出信号。返回 (rows, diag)。"""
    mod, reader, fast = _MOD, _READER, _FAST
    diag = {"code": code, "anchor_mismatch": 0, "anchor_checked": 0, "states": {},
            "probe_hit": 0, "combo": 0}
    rows = []
    basic = reader.basic.loc[code] if code in reader.basic.index else {}
    list_date = str(basic.get("list_date", "")) if hasattr(basic, "get") else ""

    if df_full is None:
        df_full = reader.bars_sql(code, _DATES[-1] if _DATES else "")
    if df_full is None or df_full.empty or len(df_full) < mod.MIN_BARS:
        return rows, diag
    # 与生产 load_all(min_date=DATA_START) 对齐：只保留 DATA_START 之后的 K 线。
    df_full = df_full[df_full.trade_date.astype(str) >= mod.DATA_START].reset_index(drop=True)
    if len(df_full) < mod.MIN_BARS:
        return rows, diag

    # 全历史预处理（numpy 快版）：flags 预算 + MA 原料 + preshift 标志
    sp = fast.StockPrep(mod, df_full, code)
    ps_cache = {}

    # 前复权收盘（用于事后收益，避免除权跳空污染）
    price = mod._qfq_price(df_full)
    dates_all = df_full.trade_date.astype(str).to_numpy()
    price_map = dict(zip(dates_all, price.astype(float)))
    row_of_date = {d: i for i, d in enumerate(dates_all)}
    lows_all = df_full.low.astype(float).to_numpy()
    # 每个交易日在本股序列中的行号（as-of 截断长度）
    row_idx = {d: i for i, d in enumerate(dates_all)}

    for D in _DATES:
        ctx = _CTX.get(D)
        if ctx is None:
            continue
        uni = _uni_of(mod, reader, D)
        if code not in uni:
            continue
        name, industry = uni[code]
        # ST/退市/次新过滤（与生产 _analyze_row 同口径）
        if "ST" in name.upper() or "退" in name:
            continue
        if list_date and list_date.isdigit() and int(list_date) > int(D) - 365:
            continue
        k = row_idx.get(D)
        if k is None or k + 1 < mod.MIN_BARS:
            continue
        diag["combo"] += 1

        # ---------- 锚点（numpy 快版；两口径共用） ----------
        p99_anchor = fast.find_event_anchor_np(mod, sp, k, ps_cache)
        anchor = p99_anchor
        fallback_anchor = False
        if anchor is None:
            anchor = fast.find_activation_anchor_np(mod, sp, k)
            if anchor is None:
                continue
            fallback_anchor = True
        event_idx, ep = anchor

        # 独立校验：与引擎原生 find_event_anchor / find_activation_anchor 分别逐条对拍
        # （抽样 + 末日全量）。两段锚分别比，避免兜底锚与 P99 锚混比造成假不一致。
        if D == _DATES[-1] or _stable_hash(code) % 23 == 0:
            sub_chk = df_full.iloc[:k + 1].copy()
            sub_chk = mod._fill_ma_columns(sub_chk, price=mod._qfq_price(sub_chk))
            ref_p99 = mod.find_event_anchor(sub_chk)
            ref_fb = mod.find_activation_anchor(sub_chk)
            diag["anchor_checked"] += 1
            got_p99 = None if p99_anchor is None else (p99_anchor[0], p99_anchor[1])
            exp_p99 = None if ref_p99 is None else (ref_p99[0], ref_p99[1])
            got_fb = (None if fast.find_activation_anchor_np(mod, sp, k) is None
                      else fast.find_activation_anchor_np(mod, sp, k))
            exp_fb = None if ref_fb is None else (ref_fb[0], ref_fb[1])
            if got_p99 != exp_p99 or got_fb != exp_fb:
                diag["anchor_mismatch"] += 1
                if len(diag.setdefault("mismatch_sample", [])) < 5:
                    diag["mismatch_sample"].append(
                        {"date": D, "code": code, "fast_p99": str(got_p99),
                         "ref_p99": str(exp_p99), "fast_fb": str(got_fb), "ref_fb": str(exp_fb)})

        # ---------- 探针预筛（new 口径；new ⊇ old，命中即需跑 analyze） ----------
        t0_open = float(sp.o[event_idx])
        t0_close = float(sp.c[event_idx])
        t0_vol = float(sp.vol[event_idx])
        adjf_D = sp.adjf_at(k)
        ma60_end = float(sp.ma_raw[60][k] / adjf_D) if sp.ma_raw[60][k] == sp.ma_raw[60][k] else 0.0
        ph_hit, _ = fast.t0_pullback_hold_np(mod, sp, event_idx, k, False, False,
                                             ma60_end, t0_open, t0_close, t0_vol)
        rb_hit, _ = fast.t0_rebreak_np(mod, sp, event_idx, k, False, False,
                                       ma60_end, t0_open, t0_close, t0_vol)
        if not (ph_hit or rb_hit):
            continue
        diag["probe_hit"] += 1

        # ---------- 命中 → 走引擎原生 analyze（输出字段与生产一致） ----------
        sub = df_full.iloc[:k + 1].copy()
        sub = mod._fill_ma_columns(sub, price=mod._qfq_price(sub))
        ige = _get_ige(mod, D).get(code)
        for caliber in ("old", "new"):
            set_caliber(mod, caliber)
            res = mod.analyze(code, name, industry, sub, ctx["anchors"], reader=reader,
                              mkt=ctx["mkt"], sector_strength={}, sector_growth={},
                              ige_adj=ige, event_hint=(event_idx, ep))
            if not res:
                continue
            res["ige_adj"] = ige
            res["market_regime"] = ctx["regime"]
            if fallback_anchor:
                res["event_is_fallback"] = True
            st, fail_line, fail_name = mod.w7_exec_status(res)
            res["w7_status"] = st
            res["w7_fail_line"] = fail_line
            res["w7_fail_name"] = fail_name
            ok, why = mod.w7_quality_gate(res)
            diag["states"][(caliber, res["state"])] = diag["states"].get((caliber, res["state"]), 0) + 1
            if not ok or res["state"] not in mod.ACTION_BUY_STATES:
                continue
            rows.append({
                "caliber": caliber, "date": D, "code": code, "name": name,
                "state": res["state"], "type": res["type"],
                "close": res["close"], "volr": round(res["volr"], 4),
                "amount_wan": round(res["amount"] / 10.0, 1),
                "t0_date": res.get("t0_date", ""), "t0_open": res.get("t0_open"),
                "t0_close": res.get("t0_close"), "pull_low": res.get("pull_low"),
                "pull_vol_ratio": round(res.get("pull_vol_ratio", 0.0), 4),
                "stall": res.get("stall", ""), "break_volr": round(res.get("break_volr", 0.0), 4),
                "buy": res["buy"], "exec": st, "gate": "PASS",
                "base_score": round(res["base_score"], 2), "score": round(res["score"], 2),
                "t120": round(res["t120"], 2), "ige_adj": ige, "regime": ctx["regime"],
                "level": res["level"], "extension": round(res["extension"], 4),
                "fail_line": fail_line, "fail_name": fail_name,
                "anchor_idx": int(event_idx), "anchor_ep": round(float(ep), 2),
            })
        set_caliber(mod, "new")  # 复位

    # 事后表现（T+1/3/5/10，用前复权价；基准用上证指数）
    idx = _CTX["_index"]
    idx_dates = list(idx.keys())
    idx_row = {d: i for i, d in enumerate(idx_dates)}
    for r in rows:
        d0 = r["date"]
        i0 = row_of_date.get(d0)
        if i0 is not None and d0 in price_map:
            p0 = price_map[d0]
            for h in (1, 3, 5, 10):
                j = i0 + h
                r["ret%d" % h] = (round((price_map[dates_all[j]] / p0 - 1) * 100, 3)
                                  if j < len(dates_all) and p0 > 0 else None)
        i0i = idx_row.get(d0)
        if i0i is not None and idx[d0] > 0:
            for h in (1, 3, 5, 10):
                j = i0i + h
                r["mkt%d" % h] = (round((idx[idx_dates[j]] / idx[d0] - 1) * 100, 3)
                                  if j < len(idx_dates) else None)
        # 失效位保护：T+10 内是否触及
        hit = None
        if i0 is not None:
            fl = r.get("fail_line") or 0
            if fl:
                for j in range(i0 + 1, min(i0 + 11, len(dates_all))):
                    if lows_all[j] <= fl:
                        hit = str(dates_all[j])
                        break
        r["stop_hit_date"] = hit
    return rows, diag


def _process_chunk(codes):
    """worker 入口：批量加载一个代码块 → 逐票双口径回放。"""
    mod, reader = _MOD, _READER
    start = mod.DATA_START
    end = _DATES[-1] if _DATES else ""
    frames = _load_chunk(mod, reader, codes, start, end)
    all_rows = []
    diags = []
    for code in codes:
        rows, diag = _process_code(code, frames.get(code))
        all_rows.extend(rows)
        diags.append(diag)
    return all_rows, diags


def _worker_map(chunk):
    return _process_chunk(chunk)


# ---------------------------------------------------------------- parent 侧
def build_ctx(reader, mod, dates, end_date):
    """构建逐日共享上下文：锚点参照特征 / 市场曲线 / 市场环境。

    股池截面（reader.universe(D)）改为 worker 内按需缓存——它逐日变化且体积大，
    放进 ctx 会让 pickle 体积膨胀 12MB+ 且各 worker 都要反序列化。
    市场曲线用 reader.market_curve(D)（index_daily_cache 自 DATA_START 起、归一化到 1.0），
    与生产 main() 完全同口径——不得用自定义归一化（起点不同会让 alpha_rs 的 120 日基准错位）。
    """
    ctx = {}
    for D in dates:
        anchors = {}
        for label, (acode, adate) in mod.ANCHORS.items():
            adf = reader.bars_sql(acode, D)
            anchors[label] = mod.anchor_features(adf, adate)
        mdates, mvals = reader.market_curve(D)
        mkt = mod.MarketCtx(mdates, mvals)
        ctx[D] = {
            "anchors": anchors, "mkt": mkt,
            "regime": mod.market_regime(reader.conn, D),
        }
    ctx["_index"] = {str(d): float(c) for d, c in reader.conn.execute(
        "SELECT trade_date, close FROM index_daily_cache WHERE ts_code=? AND trade_date<=? ORDER BY trade_date",
        (mod.MARKET_INDEX_CODE, end_date)).fetchall()}
    return ctx


def summarize(df, ctx):
    """汇总：数量 / 事后表现 / 增量（new 独有 vs old 独有）/ 分月稳定性。"""
    out = {}
    if df.empty:
        return out
    for caliber in ("old", "new"):
        sub = df[df.caliber == caliber]
        st = {}
        for s, g in sub.groupby("state"):
            st[s] = {
                "n": int(len(g)),
                "ret1_mean": _m(g, "ret1"), "ret3_mean": _m(g, "ret3"),
                "ret5_mean": _m(g, "ret5"), "ret10_mean": _m(g, "ret10"),
                "ex3_mean": _m(g.assign(e3=g.ret3 - g.mkt3), "e3"),
                "ex5_mean": _m(g.assign(e5=g.ret5 - g.mkt5), "e5"),
                "win3": _win(g, "ret3"), "win5": _win(g, "ret5"), "win10": _win(g, "ret10"),
            }
        out[caliber] = {"n": int(len(sub)), "by_state": st}

    key = ["date", "code"]
    o = df[df.caliber == "old"].set_index(key)
    n = df[df.caliber == "new"].set_index(key)
    both = o.index.intersection(n.index)
    only_new = n.index.difference(o.index)
    only_old = o.index.difference(n.index)
    out["paired"] = {
        "both": int(len(both)), "only_new": int(len(only_new)), "only_old": int(len(only_old)),
        "state_changed_both": int((o.loc[both, "state"] != n.loc[both, "state"]).sum()) if len(both) else 0,
    }
    for label, idxs in (("only_new", only_new), ("only_old", only_old)):
        if len(idxs) == 0:
            out[label] = {"n": 0}
            continue
        g = n.loc[idxs] if label == "only_new" else o.loc[idxs]
        out[label] = {
            "n": int(len(g)),
            "state": g.state.value_counts().to_dict(),
            "ret1_mean": _m(g, "ret1"), "ret3_mean": _m(g, "ret3"),
            "ret5_mean": _m(g, "ret5"), "ret10_mean": _m(g, "ret10"),
            "ex3_mean": _m(g.assign(e3=g.ret3 - g.mkt3), "e3"),
            "ex5_mean": _m(g.assign(e5=g.ret5 - g.mkt5), "e5"),
            "win3": _win(g, "ret3"), "win5": _win(g, "ret5"), "win10": _win(g, "ret10"),
            "stop_hit_rate": _m(g.assign(h=g.stop_hit_date.notna()), "h"),
        }
    # 分月稳定性
    mm = {}
    for caliber in ("old", "new"):
        sub = df[df.caliber == caliber].copy()
        sub["month"] = sub.date.str[:6]
        mm[caliber] = {}
        for mo, g in sub.groupby("month"):
            mm[caliber][mo] = {
                "n": int(len(g)),
                "ret3_mean": _m(g, "ret3"), "ret5_mean": _m(g, "ret5"), "ret10_mean": _m(g, "ret10"),
                "ex3_mean": _m(g.assign(e3=g.ret3 - g.mkt3), "e3"),
                "win3": _win(g, "ret3"), "win5": _win(g, "ret5"),
            }
    out["monthly"] = mm
    return out


def _m(g, col):
    if col not in g.columns or g[col].isna().all() or len(g) == 0:
        return None
    return round(float(g[col].mean()), 3)


def _win(g, col):
    if col not in g.columns or g[col].isna().all() or len(g) == 0:
        return None
    v = g[col].dropna()
    if len(v) == 0:
        return None
    return round(float((v > 0).mean() * 100), 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20260803")
    ap.add_argument("--end", default="20261028")
    ap.add_argument("--jobs", type=int, default=4, help="并行进程数（内存受限，默认 4）")
    ap.add_argument("--limit", type=int, default=0, help="限制股数（调试）")
    ap.add_argument("--block", type=int, default=60, help="每块的股票数")
    ap.add_argument("--outdir", default=OUT_DIR)
    args = ap.parse_args()

    mod = load_engine()
    reader = mod.CacheReader()
    reader.load_fina()

    dates = [str(r[0]) for r in reader.conn.execute(
        "SELECT DISTINCT trade_date FROM index_daily_cache WHERE ts_code=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
        (mod.MARKET_INDEX_CODE, args.start, args.end)).fetchall()]
    if not dates:
        print("[eval] 无交易日")
        return
    print("[eval] 交易日 %d 个：%s ~ %s" % (len(dates), dates[0], dates[-1]), flush=True)

    t0 = time.time()
    ctx = build_ctx(reader, mod, dates, args.end)
    print("[eval] 上下文构建完成 %.1fs" % (time.time() - t0), flush=True)

    codes = sorted(reader.universe(dates[-1]).ts_code.tolist())
    if args.limit:
        codes = codes[:args.limit]
    print("[eval] 待回放 %d 只 × %d 日 = %d 次" % (len(codes), len(dates), len(codes) * len(dates)), flush=True)

    block = max(1, args.block)
    chunks = [codes[i:i + block] for i in range(0, len(codes), block)]
    print("[eval] 分为 %d 块（每块 %d 只），并行 %d 进程" % (len(chunks), block, args.jobs), flush=True)

    os.makedirs(args.outdir, exist_ok=True)
    all_rows = []
    diags = []
    t0 = time.time()
    n_combo = 0
    n_probe = 0
    with Pool(args.jobs, initializer=_worker_init, initargs=(ENGINE, ctx, dates)) as pool:
        for n, (rows, dgs) in enumerate(pool.imap_unordered(_worker_map, chunks, chunksize=1), 1):
            all_rows.extend(rows)
            diags.extend(dgs)
            n_combo += sum(d.get("combo", 0) for d in dgs)
            n_probe += sum(d.get("probe_hit", 0) for d in dgs)
            if n % 10 == 0 or n == len(chunks):
                el = time.time() - t0
                done = min(len(codes), n * block)
                print("[eval] %d/%d 块（%d/%d 只）用时=%.0fs 预计剩余=%.0fs "
                      "组合=%d 探针命中=%d 信号=%d" %
                      (n, len(chunks), done, len(codes), el,
                       el / n * (len(chunks) - n), n_combo, n_probe, len(all_rows)), flush=True)

    df = pd.DataFrame(all_rows)
    p_sig = os.path.join(args.outdir, "eval_signals.csv")
    if not df.empty:
        cols = ["caliber", "date", "code", "name", "state", "type", "close", "volr", "amount_wan",
                "t0_date", "t0_open", "t0_close", "pull_low", "pull_vol_ratio", "stall", "break_volr",
                "buy", "exec", "gate", "base_score", "score", "t120", "ige_adj", "regime", "level",
                "extension", "fail_line", "fail_name", "anchor_idx", "anchor_ep",
                "ret1", "ret3", "ret5", "ret10", "mkt1", "mkt3", "mkt5", "mkt10", "stop_hit_date"]
        df[[c for c in cols if c in df.columns]].to_csv(p_sig, index=False, encoding="utf-8-sig")

    checked = sum(d["anchor_checked"] for d in diags)
    mism = sum(d["anchor_mismatch"] for d in diags)
    summary = summarize(df, ctx) if not df.empty else {}
    summary["_meta"] = {
        "start": dates[0], "end": dates[-1], "days": len(dates), "codes": len(codes),
        "combos": n_combo, "probe_hits": n_probe,
        "probe_hit_rate_pct": round(100.0 * n_probe / max(1, n_combo), 3),
        "anchor_crosscheck_n": checked, "anchor_mismatch": mism,
        "anchor_consistency_pct": (100.0 if mism == 0 else round(100.0 * (1 - mism / max(1, checked)), 3)),
        "elapsed_s": round(time.time() - t0, 1),
    }
    p_sum = os.path.join(args.outdir, "eval_summary.json")
    with open(p_sum, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("[eval] 输出：%s / %s" % (p_sig, p_sum))


if __name__ == "__main__":
    main()
