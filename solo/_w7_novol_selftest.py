# -*- coding: utf-8 -*-
"""W7「T0 后不对量能进行约束」改造的 old 口径等价性自检。

目的
----
证明「六道量能开关全部置 True」= 改造前引擎行为（基准备份 w7_second_wave_engine.py.bak_v7_20261028），
从而让后续 old vs new 双口径配对回测的 old 侧可以放心当作「改造前基准」。

对拍方式
--------
两个引擎模块在同一进程内并存：
  · NEW = 现引擎 w7_second_wave_engine.py（六开关全 True）
  · OLD = 备份 w7_second_wave_engine.py.bak_v7_20261028（开关未接线，行为=改造前）
对每个 (股票, 交易日)：
  1. 各自按 as-of 截断序列 → 各自重算前复权与 MA（口径一致才可比）；
  2. 各自算 T0 锚 → 比较锚点是否一致（event_idx / event_percentile）；
  3. 各自跑 analyze() → 比较状态、分型、评分、买点档、关键结构字段、reason 文本；
  4. 各自跑 w7_exec_status / w7_quality_gate → 比较执行状态与过门结论。

差异白名单
----------
  · stall_volr：现引擎新增的「止跌日量比」记录字段（old 口径下也写入），OLD 侧无此键 → 允许；
  · 其余任何字段差异都视为不一致并计入 mismatch。

输出
----
_w7_novol_out/selftest_report.json
"""
import argparse
import importlib.machinery
import importlib.util
import json
import os
import sys
import time
from multiprocessing import Pool

import numpy as np
import pandas as pd

SOLO = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(SOLO, "w7_second_wave_engine.py")
ENGINE_BAK = os.path.join(SOLO, "w7_second_wave_engine.py.bak_v7_20261028")
OUT_DIR = os.path.join(SOLO, "_w7_novol_out")

GATE_KEYS = ("W7_GATE_A_PULL_SHRINK", "W7_GATE_A_STALL_VOLR", "W7_GATE_B_PULL_SHRINK",
             "W7_GATE_B_BREAK_VOLR", "W7_GATE_B_DIST_RISK", "W7_GATE_PERSIST_SELL")

# 允许 NEW 侧多出的键（纯记录字段，不参与任何判定）
EXTRA_KEYS_OK = {"stall_volr"}

COMPARE_KEYS = [
    "state", "type", "buy", "level", "close", "volr", "amount", "ma20",
    "score", "base_score", "rank", "t120", "entry", "cq", "acceptance", "sds", "lock",
    "fs", "acc", "plat", "dist_risk", "absorption", "penalty",
    "t0_open", "t0_close", "pull_low", "t0_date", "pull_low_date", "pull_bars",
    "pull_vol_ratio", "stall", "break_volr", "t0_ext", "t0_vol",
    "major_risk", "hard_fail", "event_date", "event_percentile", "reexpansion", "breakout",
    "hvt", "sim_zjxc", "sim_hzxc", "grade", "reason", "explanation",
]
FLOAT_KEYS = {"close", "volr", "amount", "ma20", "score", "base_score", "rank", "t120", "entry",
              "cq", "acceptance", "sds", "lock", "fs", "acc", "plat", "dist_risk", "absorption",
              "penalty", "t0_open", "t0_close", "pull_low", "pull_vol_ratio", "break_volr",
              "t0_ext", "t0_vol", "hvt", "sim_zjxc", "sim_hzxc", "event_percentile"}

# 报告文案归一化：现引擎在 20261028 把「缩量回踩」口径改为「回踩」（因为 T0 后不再用量能约束），
# 旧备份引擎仍是改造前文案。对 reason / explanation 这类纯展示文本做等价替换后再比，
# 以免把「文案改名」误判成「行为不一致」。
TEXT_NORMALIZE = [
    ("类型A·缩量回踩止跌低吸", "类型A·回踩止跌低吸"),
    ("类型A 缩量回踩止跌", "类型A 回踩止跌"),
    ("缩量回踩至T0开盘价上方", "回踩至T0开盘价上方"),
    ("后放量突破，", "后突破，"),
]


def _norm_text(s):
    if not isinstance(s, str):
        return s
    for a, b in TEXT_NORMALIZE:
        s = s.replace(a, b)
    return s


def load_engine(path, name):
    """按源码加载引擎模块。备份文件名以 .bak_ 结尾，importlib 不识别后缀，
    故显式用 SourceFileLoader。"""
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


def set_gates(mod, value):
    """把六道开关统一设为 value；OLD 模块没有的开关直接跳过。"""
    for k in GATE_KEYS:
        if hasattr(mod, k):
            setattr(mod, k, value)


def _num_eq(a, b, tol=1e-6):
    fa = a if isinstance(a, (int, float)) and not isinstance(a, bool) else None
    fb = b if isinstance(b, (int, float)) and not isinstance(b, bool) else None
    if fa is None or fb is None:
        return a == b
    if not np.isfinite(fa) and not np.isfinite(fb):
        return True
    return abs(fa - fb) <= tol * max(1.0, abs(fa), abs(fb))


def _asof_ige(mod, asof):
    """严格 as-of 版 IGE 快照。

    20261029 修正：原实现用 ``len(f) == 19`` 过滤文件名，但实际文件名形如
    ``ige_full_20260901.csv`` 长度是 21 → 过滤恒假 → IGE 恒为空字典
    （实测「ige 条目 0」）→ w7_priority 的 IGE 因子恒不命中 → 定级降档。
    改为与引擎 load_ige_adj 同款正则。
    """
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


def _load_batch(reader, mod, codes, end_date, min_date):
    """批量载入多只股票的日线（一次 SQL IN 查询），返回 {code: DataFrame}。

    20261029：原实现逐票 bars_sql（每票一次 SQL + 一次 MA 重算），400 只 × 8 天
    要跑几千次查询。改为批量 IN 查询 + 逐日截断，采样量可放大一个数量级。
    """
    if not codes:
        return {}
    ph = ",".join(["?"] * len(codes))
    q = (mod._BARS_SELECT +
         " WHERE d.ts_code IN (%s) AND d.trade_date>=? AND d.trade_date<=? "
         "ORDER BY d.ts_code,d.trade_date" % ph)
    df = pd.read_sql_query(q, reader.conn, params=list(codes) + [min_date, end_date])
    if df.empty:
        return {}
    df = df.dropna(subset=["close", "high", "low", "vol"]).copy()
    numeric = [c for c in df.columns if c not in ("ts_code", "trade_date")]
    for col in numeric:
        if not pd.api.types.is_numeric_dtype(df[col]):
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return {str(c): g.reset_index(drop=True) for c, g in df.groupby("ts_code", sort=False)}


# ===== 并行化（20261029）=====================================
# 单进程跑 600 只 × 12 天 ≈ 55 分钟，太慢。改为按代码分块并行：
#   · 每个 worker 独立加载 NEW/OLD 引擎与 reader；
#   · 每个 worker 对自己那份代码只做一次全历史 SQL（不是每只一次）；
#   · 逐日只在内存里截断 + 重算 MA/前复权。
_W_NEW = None
_W_OLD = None
_W_READER = None
_W_DATES = []
_W_CTX = {}
_W_IGE = {}
_W_UNI = {}


def _w_init(dates, end_date):
    global _W_NEW, _W_OLD, _W_READER, _W_CTX, _W_DATES
    _W_NEW = load_engine(ENGINE, "w7eng_selftest_new")
    _W_OLD = load_engine(ENGINE_BAK, "w7eng_selftest_old")
    set_gates(_W_NEW, True)
    _W_READER = _W_NEW.CacheReader()
    _W_READER.load_fina()
    _W_DATES = dates
    ctx = {}
    for D in dates:
        anchors_new, anchors_old = {}, {}
        for label, (acode, adate) in _W_NEW.ANCHORS.items():
            anchors_new[label] = _W_NEW.anchor_features(_W_READER.bars_sql(acode, D), adate)
            anchors_old[label] = _W_OLD.anchor_features(_W_READER.bars_sql(acode, D), adate)
        mdates, mvals = _W_READER.market_curve(D)
        ctx[D] = {"anchors_new": anchors_new, "anchors_old": anchors_old,
                  "mkt_new": _W_NEW.MarketCtx(mdates, mvals),
                  "mkt_old": _W_OLD.MarketCtx(mdates, mvals),
                  "regime": _W_NEW.market_regime(_W_READER.conn, D)}
    _W_CTX = ctx


def _w_uni(D):
    if D not in _W_UNI:
        u = {}
        for rec in _W_READER.universe(D)[["ts_code", "name", "industry"]].to_dict("records"):
            c = str(rec.get("ts_code") or "")
            if c:
                u[c] = (str(rec.get("name") or c), str(rec.get("industry") or ""))
        _W_UNI[D] = u
    return _W_UNI[D]


def _w_chunk(codes):
    """worker 入口：对 codes × dates 做逐条对拍，返回 (计数, 不一致样例)。"""
    NEW, OLD, reader = _W_NEW, _W_OLD, _W_READER
    n_cmp = n_anchor = n_res = n_exec = n_gate = 0
    samples = []

    # 一次 SQL 载入本块全部代码的全历史
    frames = _load_batch(reader, NEW, codes, _W_DATES[-1] if _W_DATES else "", NEW.DATA_START)

    for D in _W_DATES:
        c = _W_CTX.get(D)
        if c is None:
            continue
        uni = _w_uni(D)
        ige = _W_IGE.get(D)
        if ige is None:
            ige = _asof_ige(NEW, D)
            _W_IGE[D] = ige
        for code in codes:
            name, industry = uni.get(code, (code, ""))
            if "ST" in name.upper() or "退" in name:
                continue
            df_new = frames.get(code)
            if df_new is None or df_new.empty or len(df_new) < NEW.MIN_BARS:
                continue
            sub_new = df_new[df_new.trade_date.astype(str) <= D].reset_index(drop=True)
            if len(sub_new) < NEW.MIN_BARS:
                continue
            sub_old = sub_new.copy()
            sub_new = NEW._fill_ma_columns(sub_new.copy(), price=NEW._qfq_price(sub_new.copy()))
            sub_old = OLD._fill_ma_columns(sub_old.copy(), price=OLD._qfq_price(sub_old.copy()))

            a_new = NEW.find_event_anchor(sub_new)
            a_old = OLD.find_event_anchor(sub_old)
            if a_new != a_old:
                n_anchor += 1
                if len(samples) < 40:
                    samples.append({"kind": "anchor", "date": D, "code": code,
                                    "new": str(a_new), "old": str(a_old)})
                continue

            r_new = NEW.analyze(code, name, industry, sub_new, c["anchors_new"], reader=reader,
                                mkt=c["mkt_new"], sector_strength={}, sector_growth={},
                                ige_adj=ige.get(code), event_hint=a_new)
            r_old = OLD.analyze(code, name, industry, sub_old, c["anchors_old"], reader=reader,
                                mkt=c["mkt_old"], sector_strength={}, sector_growth={},
                                ige_adj=ige.get(code), event_hint=a_old)
            n_cmp += 1
            if (r_new is None) != (r_old is None):
                n_res += 1
                if len(samples) < 40:
                    samples.append({"kind": "res_none", "date": D, "code": code,
                                    "new_none": r_new is None, "old_none": r_old is None})
                continue
            if r_new is None:
                continue

            diffs = []
            for k in COMPARE_KEYS:
                if k not in r_new and k not in r_old:
                    continue
                if k in EXTRA_KEYS_OK:
                    continue
                a, b2 = r_new.get(k), r_old.get(k)
                if k in FLOAT_KEYS:
                    if not _num_eq(a, b2):
                        diffs.append({"key": k, "new": a, "old": b2})
                else:
                    if k in ("reason", "explanation"):
                        if _norm_text(a) != _norm_text(b2):
                            diffs.append({"key": k, "new": str(a)[:120], "old": str(b2)[:120]})
                    elif a != b2:
                        diffs.append({"key": k, "new": str(a)[:120], "old": str(b2)[:120]})
            if diffs:
                n_res += 1
                if len(samples) < 40:
                    samples.append({"kind": "result", "date": D, "code": code, "diffs": diffs[:8]})
                continue

            st_new = NEW.w7_exec_status(r_new)
            st_old = OLD.w7_exec_status(r_old)
            if st_new[0] != st_old[0]:
                n_exec += 1
                if len(samples) < 40:
                    samples.append({"kind": "exec", "date": D, "code": code,
                                    "new": st_new[0], "old": st_old[0]})
                continue
            g_new = NEW.w7_quality_gate(r_new)
            g_old = OLD.w7_quality_gate(r_old)
            if g_new[0] != g_old[0]:
                n_gate += 1
                if len(samples) < 40:
                    samples.append({"kind": "gate", "date": D, "code": code,
                                    "new": g_new[1][:80], "old": g_old[1][:80]})
    return (n_cmp, n_anchor, n_res, n_exec, n_gate, samples)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20260803")
    ap.add_argument("--end", default="20261028")
    ap.add_argument("--codes", type=int, default=400, help="抽样股票数")
    ap.add_argument("--days", type=int, default=8, help="抽样交易日数")
    ap.add_argument("--outdir", default=OUT_DIR)
    ap.add_argument("--jobs", type=int, default=4, help="并行进程数")
    ap.add_argument("--block", type=int, default=60, help="每块代码数")
    args = ap.parse_args()

    NEW = load_engine(ENGINE, "w7eng_selftest_new")
    OLD = load_engine(ENGINE_BAK, "w7eng_selftest_old")
    set_gates(NEW, True)
    print("[selftest] NEW 六开关=%s / OLD 模块存在开关=%s" %
          ([getattr(NEW, k) for k in GATE_KEYS], [k for k in GATE_KEYS if hasattr(OLD, k)]), flush=True)

    reader = NEW.CacheReader()
    reader.load_fina()

    dates = [str(r[0]) for r in reader.conn.execute(
        "SELECT DISTINCT trade_date FROM index_daily_cache WHERE ts_code=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
        (NEW.MARKET_INDEX_CODE, args.start, args.end)).fetchall()]
    if not dates:
        print("[selftest] 无交易日")
        return
    step = max(1, len(dates) // max(1, args.days))
    sample_dates = dates[::step][:args.days]
    print("[selftest] 全区间 %d 个交易日，抽样 %d 天：%s" % (len(dates), len(sample_dates), sample_dates), flush=True)

    # 抽样股票：取区间末日股池，均匀抽样
    universe = sorted({str(r[0]) for r in reader.conn.execute(
        "SELECT ts_code FROM daily_basic_cache WHERE trade_date=?", (args.end,)).fetchall()})
    universe = [c for c in universe if c.endswith((".SZ", ".SH"))]
    if args.codes and args.codes < len(universe):
        pick = np.linspace(0, len(universe) - 1, args.codes).astype(int)
        universe = [universe[i] for i in sorted(set(pick.tolist()))]
    print("[selftest] 抽样股票 %d 只" % len(universe), flush=True)

    # 上下文（锚点/市场曲线/环境）由 worker 各自构建（口径与串行版一致）
    chunks = [universe[i:i + args.block] for i in range(0, len(universe), args.block)]
    print("[selftest] %d 块 × %d 进程" % (len(chunks), args.jobs), flush=True)

    n_cmp = n_anchor_mismatch = n_res_mismatch = n_exec_mismatch = n_gate_mismatch = 0
    mismatches = []
    t0 = time.time()

    if args.jobs > 1:
        with Pool(args.jobs, initializer=_w_init, initargs=(sample_dates, args.end)) as pool:
            for n, res in enumerate(pool.imap_unordered(_w_chunk, chunks, chunksize=1), 1):
                c, a, r_, e, g, ss = res
                n_cmp += c
                n_anchor_mismatch += a
                n_res_mismatch += r_
                n_exec_mismatch += e
                n_gate_mismatch += g
                for s in ss:
                    if len(mismatches) < 40:
                        mismatches.append(s)
                el = time.time() - t0
                print("[selftest] %d/%d 块 用时=%.0fs 比对=%d 锚点差=%d 结果差=%d 执行差=%d 门差=%d"
                      % (n, len(chunks), el, n_cmp, n_anchor_mismatch, n_res_mismatch,
                         n_exec_mismatch, n_gate_mismatch), flush=True)
    else:
        _w_init(sample_dates, args.end)
        for n, ch in enumerate(chunks, 1):
            c, a, r_, e, g, ss = _w_chunk(ch)
            n_cmp += c
            n_anchor_mismatch += a
            n_res_mismatch += r_
            n_exec_mismatch += e
            n_gate_mismatch += g
            for s in ss:
                if len(mismatches) < 40:
                    mismatches.append(s)
            print("[selftest] %d/%d 块 用时=%.0fs 比对=%d" %
                  (n, len(chunks), time.time() - t0, n_cmp), flush=True)

    total_diff = n_anchor_mismatch + n_res_mismatch + n_exec_mismatch + n_gate_mismatch
    report = {
        "compared": n_cmp,
        "anchor_mismatch": n_anchor_mismatch,
        "result_mismatch": n_res_mismatch,
        "exec_mismatch": n_exec_mismatch,
        "gate_mismatch": n_gate_mismatch,
        "total_mismatch": total_diff,
        "consistency_pct": 100.0 if total_diff == 0 else round(100.0 * (1 - total_diff / max(1, n_cmp)), 3),
        "sample_dates": sample_dates,
        "codes": len(universe),
        "elapsed_s": round(time.time() - t0, 1),
        "mismatch_samples": mismatches,
        "note": "OLD 基准 = w7_second_wave_engine.py.bak_v7_20261028；NEW = 现引擎六开关全 True",
    }
    os.makedirs(args.outdir, exist_ok=True)
    p = os.path.join(args.outdir, "selftest_report.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in report.items() if k != "mismatch_samples"},
                     ensure_ascii=False, indent=2))
    print("[selftest] 报告：%s" % p)
    if mismatches:
        print("[selftest] 不一致样例（前 5）：")
        for m in mismatches[:5]:
            print("   ", m)


if __name__ == "__main__":
    main()
