# -*- coding: utf-8 -*-
"""V10.7.4 主升段锁存落地验收：两年 443 日连续回放 vs 激进策略实验口径。

校验四件事
----------
1. **公式一致性**：诊断档（授权链全通过、base=实验的 default_no_limit、
   上限 100%）逐日有效仓位应与 ``_bt_bull_aggressive`` 的
   ``s_trend | trail | cap1.0 | h10 | 50pp`` 组合逐日相同（0.0pp 误差）。
2. **信号一致性**：生产 ``bull_latch.build_signals`` 的入场判定与实验
   ``build_features.s_trend`` 逐日相同。
3. **A/B 一致性**：``enabled=false`` 时逐日有效目标 == 输入基准，0 差异，
   行为与 V10.7.3 完全一致。
4. **落地折扣**：打开完整授权链 + 生产闸门后（base=effective、逐日真实
   hard_cap、P0 风险预算按当日状态重算）的收益 / 授权通过天数 / UNKNOWN
   天数 / 限速触发天数。

★ 回放 CSV 不含每日 ``authorization.result``，生产链路列的满仓授权按
  FULL_ALLOWED 模拟，是**突破天数与收益的上界**；真实落地的突破只会更少。
  该假设只影响授权列，不影响公式一致性列。

输出：_bt_bull_latch_replay_metrics.json / _bt_bull_latch_replay.csv
"""
import os
import sys
import json
import copy
import types

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import market_regime_v101 as v101
import market_regime_v107 as v107
import bull_latch as bl
import _bt_bull_entry as be
import _bt_bull_aggressive as ag

FEE_BP = 5.0
BENCH = "000300.SH"
SD_PROXY = 0.0306       # P0 实测代理止损距离 3.06%


# ---------------------------------------------------------------- 数据
def load_experiment():
    bench = be.load_bench()
    micro = be.load_micro()
    plan = be.load_plan()
    d = be.build_features(bench, micro, plan)
    lock = ag.aggressive_lock(d, "s_trend", exit_mode="trail", hold=10,
                              cooldown=0, trail_pct=0.04)
    w_exp, _ = ag.aggressive_weight(
        d, lock, target_cap=1.0, up_fast=50.0, up_slow=10.0,
        base_col="default_no_limit")
    return bench, micro, d, lock, w_exp


def aligned_rows(bench, micro):
    """按指数交易日对齐的全历史特征序列（含 2024 年，供 MA/广度预热）。

    回放循环只取 443 个决策日，但每次切片必须带上此前全部交易日，
    否则生产 MA20 / MA20(5日前) 会与实验的预热窗口不一致。
    """
    umap = {}
    for t, u in zip(micro.trade_date, micro.up_ratio):
        try:
            umap[str(t)] = float(u) if not pd.isna(u) else None
        except (TypeError, ValueError):
            umap[str(t)] = None
    out = []
    for t, c in zip(bench.trade_date, bench.close):
        try:
            cv = float(c) if not pd.isna(c) else None
        except (TypeError, ValueError):
            cv = None
        out.append({"trade_date": str(t), "close": cv,
                    "up_ratio": umap.get(str(t))})
    return out


def make_stub(td, state, cfg, authorized=True):
    """最小 V10.7 结果桩（回放不重跑全链路，只复算锁存层）。"""
    pf = v101.PortfolioRisk(
        total_risk_pct=3.26, risk_budget_pct=3.5, budget_usage=0.93,
        ok=True, used_proxy=True, proxy_stop_distance=SD_PROXY,
        cash_floor_max=90.0)
    return types.SimpleNamespace(
        trade_date=str(td), state=str(state),
        authority=v101.position_authority(str(state), cfg),
        authorization=types.SimpleNamespace(
            result=(v101.FULL_ALLOWED if authorized else v101.FULL_DENIED)),
        portfolio=pf,
        p0=types.SimpleNamespace(risk_cap=100),
        plan=types.SimpleNamespace(target_position=0, hard_cap=0))


# ---------------------------------------------------------------- 回放
def run_latch(d, rows_all, cfg, mode):
    """mode: diag(纯公式) / live(完整授权链) / off(A/B 关闭)。"""
    hist, rec = {}, []
    date_pos = {x["trade_date"]: i for i, x in enumerate(rows_all)}
    for row in d.itertuples():
        td = str(row.trade_date)
        i = date_pos.get(td)
        if i is None:
            continue
        series = rows_all[:i + 1]          # 严格只含 <= 当日
        if mode == "diag":
            base = float(row.default_no_limit)
            cap = 100
        else:
            base = float(row.effective)
            cap = int(row.hard_cap)
        state = (str(row.state)
                 if hasattr(row, "state") and not pd.isna(row.state) else "S1")
        stub = make_stub(td, state, cfg)
        a = bl.compute(stub, cfg, base_target=base, hard_cap=cap,
                       series=series, history=hist)
        rec.append({
            "trade_date": td,
            "state": stub.state,
            "base": round(base, 4), "hard_cap": cap,
            "effective_exact": a.effective_target_exact,
            "signal": a.audit.signals.get("signal"),
            "locked": bool((a.audit.latch or {}).get("locked")),
            "auth_mode": (a.audit.authorization or {}).get("mode"),
            "action": (a.audit.execution or {}).get("action"),
            "missing": "|".join(a.audit.missing or []),
            "authorized_cap": a.authorized_hard_cap,
        })
        if a.history_record:
            hist = dict(a.history_record)
    return pd.DataFrame(rec)


# ---------------------------------------------------------------- 评估
def pnl(df, bench, w_col="effective_exact"):
    dates = bench.trade_date.tolist()
    close = bench.close.to_numpy(float)
    pos = {str(x): i for i, x in enumerate(dates)}
    wmap = dict(zip(df.trade_date, df[w_col] / 100.0))
    prev, out = 0.0, []
    for td in df.trade_date:
        i = pos.get(str(td))
        if i is None or i + 1 >= len(dates):
            continue
        w = min(float(wmap[str(td)]), 1.0)
        r_idx = close[i + 1] / close[i] - 1.0
        ret = w * r_idx - abs(w - prev) * FEE_BP / 10000
        out.append((str(td), ret, w, r_idx))
        prev = w
    return pd.DataFrame(out, columns=["decision_date", "ret", "w", "idx_ret"])


def metrics(f, with_years=True):
    if f.empty:
        return {}
    nav = (1 + f.ret).cumprod()
    dd = (nav / nav.cummax() - 1).min()
    out = {
        "总收益%": round((nav.iloc[-1] - 1) * 100, 2),
        "最大回撤%": round(dd * 100, 2),
        "平均仓位%": round(f.w.mean() * 100, 2),
        "累计换手(倍)": round(float(f.w.diff().abs().sum() + f.w.iloc[0]), 2),
    }
    if with_years:
        for y, lo, hi in (("2025", "20250101", "20251231"),
                          ("2026", "20260101", "20261104")):
            sub = f[(f.decision_date >= lo) & (f.decision_date <= hi)]
            out[f"_{y}收益%"] = (metrics(sub, with_years=False).get("总收益%")
                               if not sub.empty else None)
    return out


def main():
    bench, micro, d, lock_exp, w_exp = load_experiment()
    rows_all = aligned_rows(bench, micro)
    print(f"回放 {len(d)} 日 ({d.trade_date.min()}~{d.trade_date.max()})"
          f" | 指数 {len(bench)} 日", flush=True)

    # ---- A) 诊断档：授权链放开、base 用实验口径 → 逐日公式严格对比 ----
    cfg_diag = v107.load_config()
    cfg_diag["bull_latch"]["enabled"] = True
    cfg_diag["bull_latch"]["authorization"] = {
        "require_full_allowed": False,
        "require_p0_recompute": False,
        "require_portfolio_ok": False,
    }
    d_diag = run_latch(d, rows_all, cfg_diag, "diag")

    # ---- B) 生产链路：完整授权链 + 生产闸门后有效目标 ----
    cfg_live = v107.load_config()
    cfg_live["bull_latch"]["enabled"] = True
    d_live = run_latch(d, rows_all, cfg_live, "live")

    # ---- C) A/B 关闭 ----
    cfg_off = v107.load_config()
    cfg_off["bull_latch"]["enabled"] = False
    d_off = run_latch(d, rows_all, cfg_off, "off")

    # ---- 公式一致性 ----
    exp_w = pd.Series(w_exp[:len(d)], index=[str(x) for x in d.trade_date])
    got = d_diag.set_index("trade_date")["effective_exact"] / 100.0
    common = got.index.intersection(exp_w.index)
    diff_pp = (got.loc[common] - exp_w.loc[common]).abs() * 100
    sig_prod = d_diag.set_index("trade_date")["signal"]
    exp_sig = pd.Series(np.asarray(d.s_trend, dtype=bool),
                        index=[str(x) for x in d.trade_date])
    # 生产 UNKNOWN 与实验 NaN→False 都是「不入场」，按 False 对齐比较
    sig_cmp = sig_prod.loc[common].map(lambda x: bool(x) if x is not None
                                       else False)
    sig_diff = int((sig_cmp != exp_sig.loc[common]).sum())

    # run_latch(off) 的 base 也是 d.effective，直接比 base 列最准确
    ab_diff2 = (d_off["effective_exact"] - d_off["base"]).abs()

    # ---- 收益对比 ----
    series = {
        "实验脚本(s_trend|trail|cap1.0|h10)": exp_w.loc[common] * 100,
        "落地诊断档(纯公式)": d_diag.set_index("trade_date")["effective_exact"],
        "落地生产链路(完整授权链)": d_live.set_index("trade_date")["effective_exact"],
        "A/B关闭(V10.7.3基准)": d_off.set_index("trade_date")["effective_exact"],
    }
    res = {
        "区间": f"{d.trade_date.min()}~{d.trade_date.max()}（{len(d)} 个决策日）",
        "口径": ("5bp 单边换手；T 日盘后决策/T 日收盘成交/承担 T→T+1 收益；"
                 "现金零收益；信号严格只用 <=T 数据；基准 000300.SH"),
        "授权列假设": ("回放 CSV 不含每日 authorization.result，生产链路列按 "
                    "FULL_ALLOWED 模拟，是突破天数与收益的上界；真实落地只会更少。"
                    "该假设不影响公式一致性列"),
        "落地列说明": (
            "生产链路列以 7d 之后含 10pp 限速的 effective 为基准（实验诊断列用无限速"
            " default_no_limit），且授权被拒时仍把仓位补到当日 hard_cap（hard_cap 已含"
            " P0 risk_cap，仍在风险预算内），故其收益/换手与实验不可直接比较。"
            "7e 的 plan 重建只重算硬上限与建议区间、不改变有效仓位，故仓位与收益可比。"
            "「有效目标超过输入 hard_cap 天数」全部是授权通过后申请的突破，生产由 7e"
            "按 P0 重算后的上限重建，不是绕过上限。"),
        "公式一致性": {
            "对比天数": int(len(common)),
            "最大绝对误差(pp)": round(float(diff_pp.max()), 4),
            "平均绝对误差(pp)": round(float(diff_pp.mean()), 4),
            "误差>0.5pp天数": int((diff_pp > 0.5).sum()),
            "信号判定不一致天数": sig_diff,
            "锁存天数_实验": int(lock_exp.sum()),
            "锁存天数_落地诊断档": int(d_diag["locked"].sum()),
        },
        "A_B一致性": {
            "关闭档逐日=基准_最大误差(pp)": round(float(ab_diff2.max()), 6),
            "关闭档逐日=基准_不一致天数": int((ab_diff2 > 1e-9).sum()),
            "关闭档授权申请次数": int(d_off["authorized_cap"].notna().sum()),
        },
        "收益对比": {},
        "落地统计": {},
    }
    for name, w in series.items():
        tmp = pd.DataFrame({"trade_date": w.index,
                            "effective_exact": w.to_numpy(float)})
        res["收益对比"][name] = metrics(pnl(tmp, bench))

    res["落地统计"] = {
        "生产链路锁存天数": int(d_live["locked"].sum()),
        "授权通过(BREAK_ALLOWED)天数": int((d_live["auth_mode"] ==
                                        bl.BREAK_ALLOWED).sum()),
        "授权被风险预算拒绝天数": int((d_live["auth_mode"] ==
                                  bl.BLOCKED_RISK).sum()),
        "授权被满仓授权拒绝天数": int((d_live["auth_mode"] ==
                                  bl.BLOCKED_AUTH).sum()),
        "UNKNOWN天数": int((d_live["signal"].isna()).sum()),
        "升仓限速触发天数": int((d_live["action"] == "UP_LIMITED").sum()),
        "降仓立即天数": int((d_live["action"] == "DOWN_IMMEDIATE").sum()),
        "有效目标超过输入hard_cap天数(均为授权后)": int(
            (d_live["effective_exact"] > d_live["hard_cap"]).sum()),
        "有效目标超过100%(杠杆)天数": int(
            (d_live["effective_exact"] > 100).sum()),
    }
    try:
        em = json.load(open(os.path.join(
            BASE_DIR, "_bt_bull_aggressive_metrics.json"), encoding="utf-8"))
        res["实验脚本对照"] = {
            "网格1_s_trend_trail_两年收益%": em["网格1_信号x退出"][
                "s_trend|trail"]["总收益%"],
            "网格2_s_trend_trail_cap1.0_h10_两年收益%": em["网格2_仓位x锁存"][
                "s_trend|trail|cap1.0|h10"]["总收益%"],
            "本脚本实验复算%": res["收益对比"][
                "实验脚本(s_trend|trail|cap1.0|h10)"]["总收益%"],
        }
    except Exception as e:
        res["实验脚本对照"] = {"读取失败": str(e)}

    out = d_live.merge(
        d_diag[["trade_date", "effective_exact", "signal"]].rename(
            columns={"effective_exact": "diag_exact", "signal": "diag_signal"}),
        on="trade_date", how="left").merge(
        d_off[["trade_date", "effective_exact"]].rename(
            columns={"effective_exact": "off_exact"}),
        on="trade_date", how="left")
    out["exp_w"] = out.trade_date.map(exp_w) * 100
    out["exp_lock"] = [bool(x) for x in lock_exp[:len(out)]]
    out.to_csv(os.path.join(BASE_DIR, "_bt_bull_latch_replay.csv"),
               index=False, encoding="utf-8-sig")
    with open(os.path.join(BASE_DIR, "_bt_bull_latch_replay_metrics.json"),
              "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    print(json.dumps({"公式一致性": res["公式一致性"],
                      "A_B一致性": res["A_B一致性"],
                      "收益对比": res["收益对比"],
                      "落地统计": res["落地统计"]},
                     ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
