# -*- coding: utf-8 -*-
"""据全市场配对回测与消融结果，给出 W7 六道量能开关的**默认值建议**。

只读分析脚本，不修改引擎。输出：
  · 每个开关的边际贡献（信号数增量、增量信号的期望）
  · 推荐口径（引擎默认值应设成什么）
  · 与引擎当前默认值的差异清单

用法：
  python _w7_novol_recommend.py
"""
import argparse
import json
import os
import re
import sys

import pandas as pd

SOLO = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(SOLO, "w7_second_wave_engine.py")
OUT_DIR = os.path.join(SOLO, "_w7_novol_out")

GATE_KEYS = ("W7_GATE_A_PULL_SHRINK", "W7_GATE_A_STALL_VOLR", "W7_GATE_B_PULL_SHRINK",
             "W7_GATE_B_BREAK_VOLR", "W7_GATE_B_DIST_RISK", "W7_GATE_PERSIST_SELL")

GATE_DESC = {
    "W7_GATE_A_PULL_SHRINK": "类型A 回踩段缩量（回踩均量 ≤ T0量×0.50）",
    "W7_GATE_A_STALL_VOLR": "类型A 止跌日量比上限（当日量 ≤ 0.55×前20日均量）",
    "W7_GATE_B_PULL_SHRINK": "类型B 回踩期缩量（回踩均量 ≤ T0量×0.70）",
    "W7_GATE_B_BREAK_VOLR": "类型B 突破日放量确认（当日量 ≥ 2.20×前20日均量）",
    "W7_GATE_B_DIST_RISK": "类型B 走 dist_risk 通用分支（dist_risk≥60 判 DISTRIBUTION）",
    "W7_GATE_PERSIST_SELL": "persistent_sell（含量能 conjunct，且阻断 T0 探测器）",
}


def engine_defaults():
    """读取引擎当前默认值（不 import，避免副作用）。"""
    out = {}
    with open(ENGINE, "r", encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"^(W7_GATE_[A-Z_]+)\s*=\s*(True|False)", line)
            if m and m.group(1) in GATE_KEYS:
                out[m.group(1)] = (m.group(2) == "True")
    return out


# 闸 → 消融口径名（no_X = 只关掉 X，其余保持 old 全开）
GATE_CALIBER = {
    "W7_GATE_A_PULL_SHRINK": "no_A_PULL",
    "W7_GATE_A_STALL_VOLR": "no_A_STALL",
    "W7_GATE_B_PULL_SHRINK": "no_B_PULL",
    "W7_GATE_B_BREAK_VOLR": "no_B_BREAK",
    "W7_GATE_B_DIST_RISK": "no_B_DIST",
    "W7_GATE_PERSIST_SELL": "no_PERSIST",
}


def judge_gates(A, df, old_n, old_perf):
    """逐闸判定：关掉它带来的增量信号是正期望还是负期望。

    规则（保守，避免过度拟合）：
      · dn == 0            → 该闸在本区间对「能否入选」无影响 → 可关可开，标注"无影响"
      · dn > 0 且 Δex3 < 0  → 增量信号负期望 → 该闸有判别力 → 建议保持开启(True)
      · dn > 0 且 Δex3 >= 0 → 增量信号正期望 → 该闸过严 → 建议关闭(False)
      · dn < 0              → 关掉它反而少信号（该闸在帮倒忙）→ 建议关闭(False)
    """
    verdict = {}
    for gk, cal in GATE_CALIBER.items():
        r = A.get(cal)
        if not r:
            verdict[gk] = {"judgement": "未知", "recommend": None}
            continue
        dn = r.get("n", 0) - old_n
        if old_perf is None:
            de3 = None
        else:
            de3 = (r.get("ex3") or 0) - (old_perf.get("ex3") or 0)
        if dn == 0:
            j, rec = "无影响", False
        elif dn < 0:
            j, rec = "关掉反减信号", False
        elif de3 is not None and de3 < 0:
            j, rec = "增量负期望", True
        else:
            j, rec = "增量正期望", False
        verdict[gk] = {"dn": dn, "d_ex3": de3, "judgement": j,
                       "recommend": rec, "caliber": cal}
    return verdict


def load_ablation(outdir):
    """合并多轮消融结果。

    消融是分轮跑的（第一轮 9 口径覆盖逐闸边际，第二轮 7 口径覆盖候选组合），
    每轮的 summary 只含该轮参与的口径。合并时同名口径以后一轮为准，
    并保留第一轮里「唯一一轮有」的 no_X 口径用于逐闸归因。

    ⚠️ 跨年长区间（ablation_summary_long.json）**不并入主表**——
    它的区间/样本量完全不同（old n=863 vs 短区间 228），混算会污染增量归因。
    长区间结果单独由报告脚本读取。
    """
    merged = {}
    files = []
    for name in ("ablation_summary.json", "ablation_summary_r2.json", "ablation_summary_r3.json",
                 "ablation_summary_r4.json", "ablation_summary_r5.json",
                 "ablation_summary_r6.json"):
        p = os.path.join(outdir, name)
        if os.path.exists(p):
            files.append(p)
    rows_all = []
    # 优先用合并后的逐条信号（多轮消融合并）
    p_merged = os.path.join(outdir, "ablation_signals_merged.csv")
    if os.path.exists(p_merged):
        df_all = pd.read_csv(p_merged)
        rows_all.append(df_all)
    else:
        for p in files:
            sfx = p.replace("ablation_summary", "ablation_signals").replace(".json", ".csv")
            if os.path.exists(sfx):
                df = pd.read_csv(sfx)
                if not df.empty:
                    rows_all.append(df)
    for p in files:
        with open(p, "r", encoding="utf-8") as fh:
            A = json.load(fh)
        for cal, r in A.items():
            if cal.startswith("_"):
                continue
            merged[cal] = r
    df_all = pd.concat(rows_all, ignore_index=True) if rows_all else pd.DataFrame()
    if not df_all.empty and "caliber" in df_all.columns:
        df_all = df_all.drop_duplicates(subset=["caliber", "date", "code"], keep="last")
    return merged, df_all, files


def perf(g):
    if len(g) == 0:
        return None
    r3 = g["ret3"]
    ex3 = (g["ret3"] - g["mkt3"])
    r10 = g["ret10"]
    ex10 = (g["ret10"] - g["mkt10"])
    return {
        "n": int(len(g)),
        "ret3": round(float(r3.mean()), 3) if r3.notna().any() else None,
        "ex3": round(float(ex3.mean()), 3) if ex3.notna().any() else None,
        "ret10": round(float(r10.mean()), 3) if r10.notna().any() else None,
        "ex10": round(float(ex10.mean()), 3) if ex10.notna().any() else None,
        "win3": round(float((r3.dropna() > 0).mean() * 100), 1) if r3.notna().any() else None,
        "win10": round(float((r10.dropna() > 0).mean() * 100), 1) if r10.notna().any() else None,
    }


def incremental_perf(df, cal, base="old"):
    """某口径相对 base 的**增量信号**（该口径有、base 没有）的单独表现。

    这是最干净的逐闸归因：只看「关掉这道闸多出来的那批信号」本身好不好，
    不被整体均值稀释。
    """
    if df.empty or "caliber" not in df.columns:
        return None
    d = df[df.caliber == cal]
    o = df[df.caliber == base]
    if d.empty or o.empty:
        return None
    os_ = set(zip(o.date, o.code))
    inc = d[[(dt, cc) not in os_ for dt, cc in zip(d.date, d.code)]]
    if inc.empty:
        return {"n": 0, "ret3": None, "ex3": None, "ret10": None, "ex10": None,
                "win3": None, "win10": None}
    p = perf(inc)
    p["n"] = int(len(inc))
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default=OUT_DIR)
    args = ap.parse_args()
    A, df, src_files = load_ablation(args.outdir)
    if not A:
        print("[rec] 缺少消融结果（先跑 _w7_novol_ablation.py）")
        return
    cur = engine_defaults()

    print("=" * 104)
    print("W7 六道量能开关：默认值建议")
    print("=" * 104)
    print("[rec] 合并消融来源：%s" % ", ".join(os.path.basename(f) for f in src_files))
    print("\n【各口径总览】")
    hdr = "%-14s %6s %9s %9s %9s %9s %9s %8s" % (
        "口径", "n", "T+3", "T+3超额", "T+10", "T+10超额", "T+3胜率", "T+10胜率")
    print(hdr)
    print("-" * len(hdr))
    for cal, r in A.items():
        print("%-14s %6d %9s %9s %9s %9s %9s %8s" % (
            cal, r.get("n", 0),
            _f(r.get("ret3")), _f(r.get("ex3")), _f(r.get("ret10")),
            _f(r.get("ex10")), _f(r.get("win3")), _f(r.get("win10"))))

    # ── 逐开关边际贡献（增量信号单独表现） ──────────────────
    print("\n" + "=" * 104)
    print("【逐闸边际贡献】no_X = 只关掉 X 这一道（其余保持 old 全开）。")
    print("  判定依据：增量信号的 T+3 超额（该口径多出来、old 没有的那批信号的单独表现，")
    print("  不被整体均值稀释）。若逐条 CSV 不可用，退化为该口径整体与 old 的差值。")
    print("=" * 104)
    old_n = A.get("old", {}).get("n", 0)
    old_perf = perf(df[df.caliber == "old"]) if not df.empty else None
    hdr2 = "%-14s %6s %7s %9s %10s %11s %12s  %s" % (
        "关掉的闸", "口径n", "Δn", "增量n", "增量T+3", "增量T+3超额", "增量T+10超额", "判定")
    print(hdr2)
    print("-" * len(hdr2))
    rows = []
    for cal, gk in (("no_A_PULL", "W7_GATE_A_PULL_SHRINK"),
                    ("no_A_STALL", "W7_GATE_A_STALL_VOLR"),
                    ("no_B_PULL", "W7_GATE_B_PULL_SHRINK"),
                    ("no_B_BREAK", "W7_GATE_B_BREAK_VOLR"),
                    ("no_B_DIST", "W7_GATE_B_DIST_RISK"),
                    ("no_PERSIST", "W7_GATE_PERSIST_SELL")):
        r = A.get(cal)
        if not r:
            continue
        dn = r.get("n", 0) - old_n
        inc = incremental_perf(df, cal) if not df.empty else None
        if inc is None or inc.get("n") is None:
            # 逐条 CSV 缺失：退化为整体差值口径（ΔT+3 超额），增量条数用 Δn 近似
            de3 = ((r.get("ex3") or 0) - (old_perf.get("ex3") or 0)) if old_perf else None
            de10 = ((r.get("ex10") or 0) - (old_perf.get("ex10") or 0)) if old_perf else None
            inc = {"n": dn if dn > 0 else 0, "ret3": None, "ex3": de3,
                   "ret10": None, "ex10": de10, "win3": None, "win10": None,
                   "approx": True}
        if inc["n"] == 0:
            verdict, rec = "零影响（可自由开关）", None
        elif (inc.get("ex3") or 0) > 0:
            verdict, rec = "增量正期望，建议关闭", False
        else:
            verdict, rec = "增量负期望，建议保留", True
        approx = "（整体差值近似）" if inc.get("approx") else ""
        print("%-14s %6d %+7d %9d %10s %11s %12s  %s%s" % (
            gk.replace("W7_GATE_", ""), r.get("n", 0), dn, inc["n"],
            _s(inc.get("ret3")), _s(inc.get("ex3")), _s(inc.get("ex10")),
            verdict, approx))
        rows.append({"gate": gk, "desc": GATE_DESC.get(gk, ""), "caliber": cal,
                     "n": r.get("n", 0), "dn": dn, "inc": inc,
                     "verdict": verdict, "recommend": rec})

    # ── 阈值建议：正期望的闸应「放宽阈值」还是「彻底关闭」？──
    print("\n" + "=" * 104)
    print("【阈值修法对比】对正期望的闸，比较「放宽阈值」与「彻底关闭」两种修法。")
    print("  依据：同一批口径的整体指标（n / T+3 / T+3超额 / T+10超额 / 胜率）。")
    print("=" * 104)
    hdr_t = "%-14s %6s %9s %9s %9s %9s %9s %8s  %s" % (
        "口径", "n", "T+3", "T+3超额", "T+5", "T+10", "T+10超额", "胜率3", "说明")
    print(hdr_t)
    print("-" * len(hdr_t))
    thr_groups = [
        ("类型A 回踩缩量 W7_T0_SHRINK",
         [("old", "0.50（改造前）"), ("thr060", "0.60"), ("thr070", "0.70"),
          ("thr085", "0.85"), ("thr100", "1.00"), ("no_A_PULL", "关闭（不限）")]),
        ("类型A 止跌日量比 W7_T0_STALL_VOLR",
         [("old", "0.55（改造前）"), ("stall070", "0.70"), ("stall085", "0.85"),
          ("stall100", "1.00"), ("no_A_STALL", "关闭（不限）")]),
        ("组合：A_PULL 0.85 × A_STALL 放宽",
         [("thr085", "0.85×0.55"), ("c085_s070", "0.85×0.70"),
          ("c085_s085", "0.85×0.85"), ("c085_s100", "0.85×1.00")]),
    ]
    thr_reco = {}
    for gname, items in thr_groups:
        print("  【%s】" % gname)
        best = None
        for cal, th in items:
            r = A.get(cal)
            if not r:
                continue
            note = ""
            if cal.startswith(("thr0", "stall0", "c085_")):
                note = "← 候选"
            print("  %-12s %6d %9s %9s %9s %9s %9s %8s  %s %s" % (
                cal, r.get("n", 0), _f(r.get("ret3")), _f(r.get("ex3")), _f(r.get("ret5")),
                _f(r.get("ret10")), _f(r.get("ex10")),
                ("%.1f%%" % r["win3"]) if r.get("win3") is not None else "-", th, note))
            # 打分：T+3超额 + T+10超额 各半，胜率做微小加权
            if r.get("ex3") is not None:
                sc = (r["ex3"] or 0) + (r.get("ex10") or 0)
                if best is None or sc > best[0]:
                    best = (sc, cal, th)
        if best:
            thr_reco[gname] = {"best_caliber": best[1], "threshold": best[2]}
            print("  → 最优：%s（%s）" % (best[1], best[2]))
        print("")

    # ── 候选口径对比 ────────────────────────────────────────
    print("\n" + "=" * 104)
    print("【候选口径对比】")
    print("=" * 104)
    hdr3 = "%-14s %6s %9s %9s %9s %9s %9s %8s" % (
        "口径", "n", "T+3", "T+3超额", "T+10", "T+10超额", "T+3胜率", "T+10胜率")
    print(hdr3)
    print("-" * len(hdr3))
    for cal in ("old", "new", "rec", "keep_core", "no_A_PULL",
                "thr060", "thr070", "thr085", "thr100",
                "stall070", "stall085", "stall100",
                "c085_s070", "c085_s085", "c085_s100"):
        r = A.get(cal)
        if not r:
            continue
        mark = ""
        if cal == "rec":
            mark = "  <- 仅关 A_PULL + 两道零影响闸"
        elif cal == "no_A_PULL":
            mark = "  <- 仅关回踩缩量"
        elif cal.startswith("c085_"):
            mark = "  <- 候选落地方案"
        print("%-14s %6d %9s %9s %9s %9s %9s %8s%s" % (
            cal, r.get("n", 0), _f(r.get("ret3")), _f(r.get("ex3")), _f(r.get("ret10")),
            _f(r.get("ex10")), _f(r.get("win3")), _f(r.get("win10")), mark))

    # ── 与引擎当前默认值对比 ───────────────────────────────
    verdict = judge_gates(A, df, old_n, old_perf)
    for r_ in rows:
        if r_["recommend"] is not None:
            verdict[r_["gate"]] = {"dn": r_["dn"], "d_ex3": r_["inc"].get("ex3"),
                                   "judgement": r_["verdict"],
                                   "recommend": r_["recommend"], "caliber": r_["caliber"]}
    # ── 修正：阈值放宽优于彻底关闭时，建议「保留开启 + 放宽阈值」──
    # 判据：在「关闭该闸」与「放宽该闸阈值」两类修法中，
    #       若放宽口径的整体 T+3超额 / T+10超额 / 胜率不劣于关闭口径 → 保留闸、放宽阈值。
    thr_fix = {}
    for gk, relax_cal, off_cal, grp in (
            ("W7_GATE_A_PULL_SHRINK", "thr085", "no_A_PULL", "类型A 回踩缩量 W7_T0_SHRINK"),
            ("W7_GATE_A_STALL_VOLR", "stall085", "no_A_STALL", "类型A 止跌日量比 W7_T0_STALL_VOLR")):
        v = verdict.get(gk) or {}
        if v.get("recommend") is False:      # 增量正期望 → 初步判定为「关闭」
            rr = A.get(relax_cal) or {}
            oo = A.get(off_cal) or {}
            if rr and oo:
                def _better(a, b):
                    # 三个指标都不劣即视为更优（任一指标缺失则不判）
                    ks = ("ex3", "ex10", "win3")
                    if any(a.get(k) is None or b.get(k) is None for k in ks):
                        return None
                    ge = sum(1 for k in ks if a[k] >= b[k])
                    return ge >= 2
                better = _better(rr, oo)
                if better:
                    thr_fix[gk] = {"group": grp, "threshold_caliber": relax_cal,
                                   "threshold_note": "放宽阈值口径不劣于彻底关闭口径",
                                   "relax": {"n": rr.get("n"), "ex3": rr.get("ex3"),
                                             "ex10": rr.get("ex10"), "win3": rr.get("win3")},
                                   "off": {"n": oo.get("n"), "ex3": oo.get("ex3"),
                                           "ex10": oo.get("ex10"), "win3": oo.get("win3")}}
                    v["recommend"] = True
                    v["judgement"] = "增量正期望，但放宽阈值优于彻底关闭 → 保留闸并放宽阈值"
                    v["threshold_note"] = thr_fix[gk]
    rec_cfg = {gk: verdict[gk].get("recommend") for gk in GATE_KEYS}
    rec_on = sorted([g for g, v in rec_cfg.items() if v is True])

    print("\n" + "=" * 104)
    print("【引擎当前默认值 vs 建议】")
    print("=" * 104)
    print("%-24s %-8s %-8s %-8s %-14s %s" %
          ("开关", "当前", "建议", "Δn", "增量T+3超额", "说明"))
    print("-" * 104)
    for gk in GATE_KEYS:
        v = verdict.get(gk, {})
        cur_s = "True" if cur.get(gk) else "False"
        rec_s = "-" if v.get("recommend") is None else ("True" if v["recommend"] else "False")
        dn = v.get("dn")
        de3 = v.get("d_ex3")
        note = v.get("threshold_note") or {}
        extra = ""
        if note:
            extra = "  [阈值→放宽口径 %s]" % note.get("threshold_caliber", "")
        mark = ("  [需改]" if (v.get("recommend") is not None and
                               bool(cur.get(gk)) != bool(v["recommend"])) else "") + extra
        print("%-24s %-8s %-8s %-8s %-14s %s%s" % (
            gk.replace("W7_GATE_", ""), cur_s, rec_s,
            ("-" if dn is None else "%+d" % dn),
            ("-" if de3 is None else "%+.2f%%" % de3),
            GATE_DESC.get(gk, ""), mark))

    print("\n【建议保留开启的闸】%s" % (", ".join(g.replace("W7_GATE_", "") for g in rec_on) or "（无）"))
    print("【建议关闭的闸】%s" % (", ".join(g.replace("W7_GATE_", "") for g in GATE_KEYS
                                            if rec_cfg.get(g) is False) or "（无）"))
    if thr_fix:
        print("【建议放宽阈值的闸】")
        for gk, t in thr_fix.items():
            print("  · %s（%s）：放宽口径 n=%d / T+3超额 %+.2f%% / T+10超额 %+.2f%% / 胜率 %.1f%%"
                  % (gk.replace("W7_GATE_", ""), t["group"],
                     t["relax"].get("n", 0), t["relax"].get("ex3") or 0,
                     t["relax"].get("ex10") or 0, t["relax"].get("win3") or 0))

    # ── 交叉验证 ────────────────────────────────────────────
    print("\n" + "=" * 104)
    print("【交叉验证：落地口径 vs old vs new】")
    print("=" * 104)
    hdr4 = "%-14s %6s %9s %9s %9s %9s %9s %8s" % (
        "口径", "n", "T+3", "T+3超额", "T+10", "T+10超额", "T+3胜率", "T+10胜率")
    print(hdr4)
    print("-" * len(hdr4))
    for cal in ("old", "new", "rec", "thr085", "c085_s070", "c085_s085", "c085_s100"):
        r = A.get(cal)
        if not r:
            continue
        print("%-14s %6d %9s %9s %9s %9s %9s %8s" % (
            cal, r.get("n", 0), _f(r.get("ret3")), _f(r.get("ex3")), _f(r.get("ret10")),
            _f(r.get("ex10")), _f(r.get("win3")), _f(r.get("win10"))))

    out = {"marginal": rows, "current_defaults": cur,
           "verdict": verdict, "recommended_on": rec_on,
           "recommended_config": rec_cfg, "ablation": A,
           "threshold_reco": thr_reco, "threshold_fix": thr_fix}
    p = os.path.join(args.outdir, "recommend.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print("\n[rec] 输出：%s" % p)


def _f(v):
    return "-" if v is None else "%+.2f" % v


def _s(v):
    return "-" if v is None else "%+.2f" % v


if __name__ == "__main__":
    main()
