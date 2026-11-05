# -*- coding: utf-8 -*-
"""V10.7.3 波段闸门落地验收：两年 443 日连续回放 vs 实验脚本口径。

校验三件事
----------
1. **公式一致性**：关闭升仓限速后，swing_gate 逐日有效仓位应与
   _bt_swing_optimizer.factor_for 的 state_s2s5 / combo_ma20_s2s5_dd
   完全相同（仅允许整数取整的 ±0.5pp 误差）。
2. **收益一致性**：用同一 5bp 换手成本与 T→T+1 收益公式，默认档 / 三层档
   的两年收益应复现实验脚本的对应策略。
3. **落地折扣**：默认开启 10pp 升仓限速后的收益变化（与实验的 P1 限速列对比）。

不重跑 V10.7 全链路（单日可达数分钟），复用 443 日连续回放的 P1/P2 目标
序列 _bt_opt_replay_on.csv 作为闸门输入；闸门本身是纯函数，可逐日精确复算。

输出：_bt_swing_gate_replay_metrics.json / _bt_swing_gate_replay.csv
"""
import os
import sys
import json
import copy
import sqlite3
import types
import logging

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import market_regime_v107 as v107
import swing_gate as sg
import _bt_swing_optimizer as exp

DB = os.path.join(os.path.dirname(BASE_DIR), "cache_daily", "stock_data.db")
BENCH = "000300.SH"
FEE_BP = 5.0
TRADING_DAYS = 244

logging.basicConfig(level=logging.WARNING)


def load_bench():
    con = sqlite3.connect("file:" + DB.replace("\\", "/") + "?mode=ro", uri=True)
    try:
        df = pd.read_sql(
            "SELECT trade_date, close FROM index_daily_cache "
            "WHERE ts_code=? AND trade_date>=? AND trade_date<=? ORDER BY trade_date",
            con, params=(BENCH, "20240101", "20261104"))
    finally:
        con.close()
    df["trade_date"] = df.trade_date.astype(str)
    return df.dropna().drop_duplicates("trade_date").reset_index(drop=True)


def load_plan():
    d = pd.read_csv(os.path.join(BASE_DIR, "_bt_opt_replay_on.csv"),
                    encoding="utf-8-sig")
    d["trade_date"] = d.trade_date.astype(str)
    return d


def run_gate(plan, bench, profile, up_step=None):
    """逐日跑落地闸门，返回 DataFrame 与每日审计。"""
    cfg = v107.load_config()
    cfg["swing_gate"]["enabled"] = True
    cfg["swing_gate"]["profile"] = profile
    if up_step is not None:
        cfg["swing_gate"]["execution"]["up_step_pct"] = up_step

    rows = [{"trade_date": r.trade_date, "close": float(r.close)}
            for r in bench.itertuples()]
    date_pos = {x["trade_date"]: i for i, x in enumerate(rows)}
    hist, rec = {}, []
    for r in plan.itertuples():
        td = str(r.trade_date)
        i = date_pos.get(td)
        if i is None:
            continue
        series = rows[max(0, i - 79):i + 1]
        stub = types.SimpleNamespace(trade_date=td, state=str(r.eff_state))
        a = sg.compute(stub, cfg, base_target=int(r.target),
                       hard_cap=int(r.hard_cap), series=series,
                       history=(hist or {}))
        rec.append({
            "trade_date": td, "state": str(r.eff_state),
            "base_target": int(r.target), "hard_cap": int(r.hard_cap),
            "effective_exact": a.audit.effective_target_exact,
            "effective": a.effective_target,
            "action": a.audit.execution.get("action"),
            "layers": "|".join(a.audit.layers.values()),
        })
        if a.history_record:
            hist = dict(a.history_record)
    return pd.DataFrame(rec), cfg


def pnl(plan, df_gate, bench, w_col="effective_exact"):
    """与 _bt_swing_optimizer.evaluate 完全同口径的收益计算（5bp 单边）。"""
    dates = bench.trade_date.tolist()
    close = bench.close.to_numpy(float)
    pos = {d: i for i, d in enumerate(dates)}
    wmap = dict(zip(df_gate.trade_date, df_gate[w_col] / 100.0))
    prev_w, out = 0.0, []
    for d in plan.trade_date:
        i = pos.get(str(d))
        if i is None or i + 1 >= len(dates) or str(d) not in wmap:
            continue
        w = min(float(wmap[str(d)]), 1.0)
        r_idx = close[i + 1] / close[i] - 1.0
        ret = w * r_idx - abs(w - prev_w) * FEE_BP / 10000
        out.append((str(d), dates[i + 1], ret, w, r_idx))
        prev_w = w
    return pd.DataFrame(out, columns=["decision_date", "ret_date",
                                      "ret", "w", "idx_ret"])


def metrics(df):
    if df.empty:
        return {}
    nav = (1 + df.ret).cumprod()
    dd = (nav / nav.cummax() - 1).min()
    return {
        "总收益%": round((nav.iloc[-1] - 1) * 100, 2),
        "最大回撤%": round(dd * 100, 2),
        "平均仓位%": round(df.w.mean() * 100, 2),
        "累计换手(倍)": round(float(df.w.diff().abs().sum() + df.w.iloc[0]), 2),
        "收益天数": int(len(df)),
    }


def compare_to_experiment(plan, bench, df_gate, sigs, strategy):
    """逐日与实验脚本 factor_for 对比（实验无升仓限速）。"""
    diffs, n = [], 0
    for r in plan.itertuples():
        td = str(r.trade_date)
        if td not in sigs:
            continue
        row = df_gate[df_gate.trade_date == td]
        if row.empty:
            continue
        pd_ = {"w": float(r.target) / 100.0,
               "cap": float(r.hard_cap) / 100.0,
               "state": str(r.eff_state),
               "easing": str(r.easing), "rebound": str(r.rebound)}
        exp_w = min(exp.factor_for(strategy, pd_, sigs[td], None),
                    pd_["cap"])
        got = float(row.effective_exact.iloc[0]) / 100.0
        diffs.append(abs(got - exp_w) * 100)
        n += 1
    return {"对比天数": n,
            "最大绝对误差(pp)": round(max(diffs), 4) if diffs else None,
            "误差>0.5pp天数": int(sum(1 for x in diffs if x > 0.5)),
            "平均绝对误差(pp)": round(float(np.mean(diffs)), 4) if diffs else None}


def main():
    plan = load_plan()
    bench = load_bench()
    sigs = exp.build_signals(bench)
    print(f"回放 {len(plan)} 日 ({plan.trade_date.min()}~{plan.trade_date.max()})"
          f" | 指数 {len(bench)} 日", flush=True)

    # 关闭升仓限速 → 与实验脚本逐日公式严格对比
    d_default, _ = run_gate(plan, bench, "default", up_step=0)
    d_swing3, _ = run_gate(plan, bench, "swing3", up_step=0)
    # 默认落地：10pp 升仓限速
    d_live, cfg_live = run_gate(plan, bench, "default", up_step=None)

    res = {
        "区间": f"{plan.trade_date.min()}~{plan.trade_date.max()}（{len(plan)} 个决策日）",
        "口径": "5bp 单边换手成本；T 日盘后决策、T 日收盘成交、承担 T→T+1 收益；现金零收益；无未来数据",
        "公式一致性": {
            "default_vs_state_s2s5": compare_to_experiment(
                plan, bench, d_default, sigs, "state_s2s5"),
            "swing3_vs_combo_ma20_s2s5_dd": compare_to_experiment(
                plan, bench, d_swing3, sigs, "combo_ma20_s2s5_dd"),
        },
    }

    # 收益对照（含 baseline = 无闸门）
    d_base = d_default.copy()
    d_base["effective_exact"] = d_base["base_target"].astype(float)
    series = {
        "无闸门(P1/P2原状)": d_base,
        "默认档(S2/S5,无限速)": d_default,
        "默认档(S2/S5,10pp限速·实盘)": d_live,
        "三层档(无限速)": d_swing3,
    }
    pnl_frames = {}
    res["收益对比"] = {}
    for name, d in series.items():
        f = pnl(plan, d, bench)
        pnl_frames[name] = f
        m = metrics(f)
        m["分年"] = {}
        for y, lo, hi in (("2025", "20250101", "20251231"),
                          ("2026", "20260101", "20261104")):
            sub = f[(f.decision_date >= lo) & (f.decision_date <= hi)]
            m["分年"][y] = metrics(sub)
        res["收益对比"][name] = m

    # 与实验脚本指标对照
    try:
        em = json.load(open(os.path.join(BASE_DIR,
                        "_bt_swing_optimizer_metrics.json"), encoding="utf-8"))
        cmp_ = em.get("策略对比", {})
        res["实验脚本对照"] = {
            "baseline_两年收益%": cmp_.get("baseline", {}).get("总收益%"),
            "state_s2s5_两年收益%": cmp_.get("state_s2s5", {}).get("总收益%"),
            "state_s2s5_含P1限速%": cmp_.get("state_s2s5", {}).get(
                "_含P1升仓限速10pp后收益%"),
            "combo_ma20_s2s5_dd_两年收益%": cmp_.get(
                "combo_ma20_s2s5_dd", {}).get("总收益%"),
            "落地默认档_两年收益%": res["收益对比"][
                "默认档(S2/S5,无限速)"]["总收益%"],
            "落地默认档_10pp限速%": res["收益对比"][
                "默认档(S2/S5,10pp限速·实盘)"]["总收益%"],
        }
    except Exception as e:
        res["实验脚本对照"] = {"读取失败": str(e)}

    res["落地统计"] = {
        "默认档实际收紧天数": int((d_live.effective_exact <
                                d_live.base_target).sum()),
        "升仓限速触发天数": int((d_live.action == "UP_LIMITED").sum()),
        "降仓立即天数": int((d_live.action == "DOWN_IMMEDIATE").sum()),
        "默认档平均有效仓位%": round(d_live.effective_exact.mean(), 2),
        "三层档平均有效仓位%": round(d_swing3.effective_exact.mean(), 2),
        "硬上限被突破天数": int((d_live.effective_exact >
                              d_live.hard_cap).sum()),
        "有效目标高于基准天数": int((d_live.effective_exact >
                                 d_live.base_target).sum()),
    }

    out_csv = d_live.merge(
        d_default[["trade_date", "effective_exact"]].rename(
            columns={"effective_exact": "default_no_limit"}),
        on="trade_date", how="left").merge(
        d_swing3[["trade_date", "effective_exact"]].rename(
            columns={"effective_exact": "swing3_no_limit"}),
        on="trade_date", how="left")
    out_csv.to_csv(os.path.join(BASE_DIR, "_bt_swing_gate_replay.csv"),
                   index=False, encoding="utf-8-sig")
    with open(os.path.join(BASE_DIR, "_bt_swing_gate_replay_metrics.json"),
              "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    print(json.dumps({"公式一致性": res["公式一致性"],
                      "收益对比": {k: {"总收益%": v["总收益%"],
                                    "最大回撤%": v["最大回撤%"],
                                    "平均仓位%": v["平均仓位%"]}
                                 for k, v in res["收益对比"].items()},
                      "实验脚本对照": res["实验脚本对照"],
                      "落地统计": res["落地统计"]},
                     ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
