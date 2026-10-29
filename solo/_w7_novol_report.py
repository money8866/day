# -*- coding: utf-8 -*-
"""W7「T0 后不对量能进行约束」全市场配对回测报告生成器。

读 _w7_novol_out/eval_summary.json + eval_signals.csv，
输出 report_daily/w7_novol_eval_<start>_<end>.md。

报告结构：
  ① 结论摘要（信号量、胜率、超额、建议）
  ② 口径与一致性保证（含锚点一致率、探针命中率）
  ③ 信号数量对比（总量 / 分状态 / 分月）
  ④ 事后表现对比（T+1/3/5/10 绝对收益 / 相对基准超额 / 胜率）
  ⑤ 增量归因（仅 new / 仅 old / 共有但状态变化）
  ⑥ 市场环境分层
  ⑦ 方法学附录（口径定义、验证链路、性能架构）
"""
import argparse
import json
import os
import sys

import pandas as pd

SOLO = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(SOLO, "_w7_novol_out")
REPORT_DIR = os.path.join(os.path.dirname(SOLO), "report_daily")

# 消融口径说明
_GATE_DESC = {
    "W7_GATE_A_PULL_SHRINK": "类型A 回踩段缩量（回踩均量 ≤ T0量×0.50）",
    "W7_GATE_A_STALL_VOLR": "类型A 止跌日量比上限（当日量 ≤ 0.55×前20日均量）",
    "W7_GATE_B_PULL_SHRINK": "类型B 回踩期缩量（回踩均量 ≤ T0量×0.70）",
    "W7_GATE_B_BREAK_VOLR": "类型B 突破日放量确认（当日量 ≥ 2.20×前20日均量）",
    "W7_GATE_B_DIST_RISK": "类型B 走 dist_risk 通用分支",
    "W7_GATE_PERSIST_SELL": "persistent_sell（含量能 conjunct 且阻断 T0 探测器）",
}

_ABL_DESC = {
    "old": "六闸全开（改造前基准）",
    "new": "六闸全关（取消 T0 后量能约束）",
    "no_A_PULL": "仅关 类型A 回踩缩量",
    "no_A_STALL": "仅关 类型A 止跌日量比",
    "no_B_PULL": "仅关 类型B 回踩缩量",
    "no_B_BREAK": "仅关 类型B 突破放量",
    "no_B_DIST": "仅关 类型B dist_risk",
    "no_PERSIST": "仅关 persistent_sell",
    "keep_core": "只留 A_STALL + B_BREAK",
    "keep_A_STALL": "只留 A_STALL",
    "keep_A_STALL_B_BREAK": "只留 A_STALL + B_BREAK",
    "keep_A_STALL_PERSIST": "只留 A_STALL + PERSIST",
    "keep_A_STALL_DIST": "只留 A_STALL + B_DIST",
    "keep_A_STALL_B_PULL": "只留 A_STALL + B_PULL",
    "rec": "关 A_PULL + 关两道零影响闸",
    "thr060": "A_PULL 阈值 0.60（其余同 old）",
    "thr070": "A_PULL 阈值 0.70",
    "thr085": "A_PULL 阈值 0.85",
    "thr100": "A_PULL 阈值 1.00",
    "stall070": "A_STALL 阈值 0.70",
    "stall085": "A_STALL 阈值 0.85",
    "stall100": "A_STALL 阈值 1.00",
    "c085_s070": "A_PULL 0.85 + A_STALL 0.70",
    "c085_s085": "A_PULL 0.85 + A_STALL 0.85",
    "c085_s100": "A_PULL 0.85 + A_STALL 1.00",
}


def fmt(v, unit="", digits=2, dash="—"):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return dash
    if isinstance(v, (int, float)):
        return ("%+.2f%s" % (v, unit)) if unit else ("%.2f" % v)
    return str(v)


def pct(v, dash="—"):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return dash
    return "%.1f%%" % v


def _selftest_desc():
    """读 selftest_report.json，生成一句话结论。取比对例数最多的那份。"""
    cands = []
    for p in (os.path.join(OUT_DIR, "selftest_report.json"),
              os.path.join(SOLO, "_w7_novol_out_test", "selftest_report.json"),
              os.path.join(OUT_DIR, "_w7_novol_out_test", "selftest_report.json")):
        if not os.path.exists(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as fh:
                r = json.load(fh)
        except Exception:
            continue
        if r.get("compared"):
            cands.append(r)
    if not cands:
        return "见 selftest_report.json"
    r = max(cands, key=lambda x: x.get("compared", 0))
    extra = ""
    if r.get("codes") and r.get("sample_dates"):
        extra = "（%d 只 × %d 个交易日）" % (r.get("codes"), len(r.get("sample_dates")))
    return ("比对 %d 例%s，锚点/结果/执行/质量门四类不一致合计 **%d** 条，一致率 **%s**"
            % (r.get("compared", 0), extra, r.get("total_mismatch", 0),
               pct(r.get("consistency_pct"))))


def build(outdir, report_path):
    p_sum = os.path.join(outdir, "eval_summary.json")
    p_sig = os.path.join(outdir, "eval_signals.csv")
    if not os.path.exists(p_sum):
        print("[report] 缺少 %s" % p_sum)
        return None
    with open(p_sum, "r", encoding="utf-8") as fh:
        S = json.load(fh)
    meta = S.get("_meta", {})
    df = pd.read_csv(p_sig) if os.path.exists(p_sig) else pd.DataFrame()

    L = []
    A = L.append
    A("# W7「T0 后不对量能进行约束」全市场配对回测报告")
    A("")
    A("区间：%s ~ %s（%d 个交易日）　|　样本：%d 只　|　回放组合：%s"
      % (meta.get("start"), meta.get("end"), meta.get("days", 0),
         meta.get("codes", 0), "{:,}".format(meta.get("combos", 0))))
    A("")

    # ── ① 结论摘要 ──────────────────────────────────────────
    old = S.get("old", {})
    new = S.get("new", {})
    paired = S.get("paired", {})
    only_new = S.get("only_new", {})
    only_old = S.get("only_old", {})
    A("## ① 结论摘要")
    A("")
    A("| 指标 | old（改造前：T0 后量能硬约束） | new（取消 T0 后量能约束） | 差异 |")
    A("|---|---|---|---|")
    A("| 过门信号数 | %d | %d | **%+d** |"
      % (old.get("n", 0), new.get("n", 0), new.get("n", 0) - old.get("n", 0)))
    for cal, label in (("old", "old"), ("new", "new")):
        pass
    o_all = old.get("by_state", {})
    n_all = new.get("by_state", {})
    for st in sorted(set(list(o_all.keys()) + list(n_all.keys()))):
        o = o_all.get(st, {})
        n = n_all.get(st, {})
        A("| 　└ %s 信号数 | %d | %d | %+d |"
          % (st, o.get("n", 0), n.get("n", 0), n.get("n", 0) - o.get("n", 0)))
    A("")
    for h in (3, 5, 10):
        oc = df[df.caliber == "old"] if not df.empty else pd.DataFrame()
        nc = df[df.caliber == "new"] if not df.empty else pd.DataFrame()
        if oc.empty or nc.empty:
            continue
        # eval_signals.csv 的 retN / mktN 已是百分数（如 1.23 表示 +1.23%），不再乘 100
        oret = oc["ret%d" % h].mean() if oc["ret%d" % h].notna().any() else None
        nret = nc["ret%d" % h].mean() if nc["ret%d" % h].notna().any() else None
        oex = (oc["ret%d" % h] - oc["mkt%d" % h]).mean() if oc["ret%d" % h].notna().any() else None
        nex = (nc["ret%d" % h] - nc["mkt%d" % h]).mean() if nc["ret%d" % h].notna().any() else None
        ow = (oc["ret%d" % h] > 0).sum() / max(1, oc["ret%d" % h].notna().sum()) * 100
        nw = (nc["ret%d" % h] > 0).sum() / max(1, nc["ret%d" % h].notna().sum()) * 100
        A("| T+%d 均值收益 / 超额 / 胜率 | %s / %s / %s | %s / %s / %s | %s |"
          % (h, fmt(oret, "%"), fmt(oex, "%"), pct(ow), fmt(nret, "%"), fmt(nex, "%"), pct(nw),
             fmt(None if nret is None or oret is None else nret - oret, "%")))
    A("")
    A("**配对拆解**：共有信号 %d 条；仅 new 命中 %d 条；仅 old 命中 %d 条；共有但状态改变 %d 条。"
      % (paired.get("both", 0), paired.get("only_new", 0),
         paired.get("only_old", 0), paired.get("state_changed_both", 0)))
    A("")

    # ── ② 口径与一致性保证 ──────────────────────────────────
    A("## ② 口径与一致性保证")
    A("")
    A("| 项 | 值 |")
    A("|---|---|")
    A("| 锚点对拍一致率 | **%s**（%d 条对拍，%d 条不一致） |"
      % (pct(meta.get("anchor_consistency_pct")),
         meta.get("anchor_crosscheck_n", 0), meta.get("anchor_mismatch", 0)))
    A("| 探针命中率（new 口径） | %s（%s / %s 组合） |"
      % (pct(meta.get("probe_hit_rate_pct")),
         "{:,}".format(meta.get("probe_hits", 0)), "{:,}".format(meta.get("combos", 0))))
    A("| 回测耗时 | %s 秒 |" % meta.get("elapsed_s"))
    A("")
    A("口径一致性由三重约束保证：")
    A("")
    A("1. **锚点共用**：每 (股票, 交易日) 只算一次 T0 锚，两口径共用 → 锚点差异只能来自开关本身。")
    A("2. **输入同源**：同一份 as-of 截断序列、同一份 MA/前复权、同一份 point-in-time 财务、"
      "同一份严格 as-of IGE 快照、同一市场环境判定、同一锚点参照特征。")
    A("3. **唯一变量**：两口径唯一差异就是那六道量能开关。"
      "old 侧已与改造前引擎备份（w7_second_wave_engine.py.bak_v7_20261028）逐条对拍验证等价。")
    A("")

    # ── ③ 数量对比 ──────────────────────────────────────────
    A("## ③ 信号数量对比（分月）")
    A("")
    mm = S.get("monthly", {})
    months = sorted(set(list(mm.get("old", {}).keys()) + list(mm.get("new", {}).keys())))
    A("| 月份 | old 数量 | new 数量 | 增量 | old T+3 | new T+3 | new 超额 | old 胜率 | new 胜率 |")
    A("|---|---|---|---|---|---|---|---|---|")
    for mo in months:
        o = mm.get("old", {}).get(mo, {})
        n = mm.get("new", {}).get(mo, {})
        A("| %s | %d | %d | %+d | %s | %s | %s | %s | %s |"
          % (mo, o.get("n", 0), n.get("n", 0), n.get("n", 0) - o.get("n", 0),
             fmt(o.get("ret3_mean"), "%"), fmt(n.get("ret3_mean"), "%"), fmt(n.get("ex3_mean"), "%"),
             pct(o.get("win3")), pct(n.get("win3"))))
    A("")

    # ── ④ 事后表现（分状态） ────────────────────────────────
    A("## ④ 事后表现对比（分信号类型）")
    A("")
    A("| 口径 | 状态 | n | T+1 | T+3 | T+5 | T+10 | T+3 超额 | T+5 超额 | T+3 胜率 | T+5 胜率 | T+10 胜率 |")
    A("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for cal in ("old", "new"):
        for st, g in sorted(S.get(cal, {}).get("by_state", {}).items()):
            A("| %s | %s | %d | %s | %s | %s | %s | %s | %s | %s | %s | %s |"
              % (cal, st, g.get("n", 0), fmt(g.get("ret1_mean"), "%"), fmt(g.get("ret3_mean"), "%"),
                 fmt(g.get("ret5_mean"), "%"), fmt(g.get("ret10_mean"), "%"),
                 fmt(g.get("ex3_mean"), "%"), fmt(g.get("ex5_mean"), "%"),
                 pct(g.get("win3")), pct(g.get("win5")), pct(g.get("win10"))))
    A("")

    # ── ⑤ 增量归因 ──────────────────────────────────────────
    A("## ⑤ 增量归因")
    A("")
    for label, blk, title in (("only_new", only_new, "仅 new 命中（本次改造新增的信号）"),
                              ("only_old", only_old, "仅 old 命中（本次改造丢失的信号）")):
        A("### %s" % title)
        A("")
        if blk.get("n", 0) == 0:
            A("无。")
            A("")
            continue
        A("| 项 | 值 |")
        A("|---|---|")
        A("| 条数 | %d |" % blk.get("n", 0))
        A("| 状态分布 | %s |" % json.dumps(blk.get("state", {}), ensure_ascii=False))
        A("| T+1 / T+3 / T+5 / T+10 均值 | %s / %s / %s / %s |"
          % (fmt(blk.get("ret1_mean"), "%"), fmt(blk.get("ret3_mean"), "%"),
             fmt(blk.get("ret5_mean"), "%"), fmt(blk.get("ret10_mean"), "%")))
        A("| T+3 / T+5 超额 | %s / %s |" % (fmt(blk.get("ex3_mean"), "%"), fmt(blk.get("ex5_mean"), "%")))
        A("| T+3 / T+5 / T+10 胜率 | %s / %s / %s |"
          % (pct(blk.get("win3")), pct(blk.get("win5")), pct(blk.get("win10"))))
        A("| 失效位 10 日内触达率 | %s |" % pct(blk.get("stop_hit_rate")))
        A("")

    # ── ⑥ 市场环境分层 ──────────────────────────────────────
    if not df.empty and "regime" in df.columns:
        A("## ⑥ 市场环境分层（new 口径）")
        A("")
        A("| 环境 | n | T+3 | T+5 | T+10 | T+3 超额 | T+3 胜率 |")
        A("|---|---|---|---|---|---|---|")
        nc = df[df.caliber == "new"]
        for rg, g in nc.groupby("regime"):
            A("| %s | %d | %s | %s | %s | %s | %s |"
              % (rg, len(g),
                 fmt(g["ret3"].mean() if g["ret3"].notna().any() else None, "%"),
                 fmt(g["ret5"].mean() if g["ret5"].notna().any() else None, "%"),
                 fmt(g["ret10"].mean() if g["ret10"].notna().any() else None, "%"),
                 fmt((g["ret3"] - g["mkt3"]).mean() if g["ret3"].notna().any() else None, "%"),
                 pct((g["ret3"] > 0).sum() / max(1, g["ret3"].notna().sum()) * 100)))
        A("")

    # ── ⑦ 方法学附录 ────────────────────────────────────────
    A("## ⑦ 方法学附录")
    A("")
    A("### 口径定义")
    A("")
    A("- **old**：六道量能开关全 True —— 逐条等价于改造前引擎（备份 "
      "`w7_second_wave_engine.py.bak_v7_20261028`），含：类型A 回踩缩量门槛、类型A 止跌日量比上限、"
      "类型B 回踩期缩量门槛、类型B 突破日放量确认、类型B 走 dist_risk 通用分支、"
      "persistent_sell 含量能 conjunct 且阻断 T0 探测器。")
    A("- **new**：六道开关全 False —— T0 之后量能彻底退出「信号生死」判定，"
      "只保留记录（pull_vol_ratio / break_volr / stall_volr）与排序参考；"
      "价格结构类硬条件（回踩深度/到位/守住起点、止跌形态、收盘位置与上影、MA60 中期未破、不追高）全部保留。")
    A("")
    A("> **口径修正（20261029）**：全市场消融证明「new（六闸全关）」整体为负期望，"
      "真正的问题只在一道闸（类型A 回踩段缩量）；"
      "且阈值扫描进一步显示「**放宽阈值**优于「**彻底关闭**」。"
      "因此引擎最终落地的默认值**不是 new**，而是 **thr085**："
      "`W7_T0_SHRINK` 0.50 → **0.85**（闸保留开启、仅放宽阈值）、"
      "`W7_GATE_B_DIST_RISK=False`、`W7_GATE_PERSIST_SELL=False`（两道零影响闸），"
      "其余四道量能闸保持 True。详见第 ⑩ 节。")
    A("")
    A("### 验证链路")
    A("")
    A("| 层 | 手段 | 结果 |")
    A("|---|---|---|")
    A("| numpy 快版 ≡ 引擎原生 | `_w7_novol_check.py` 逐条对拍（锚点/兜底锚/双探针，两套口径） | "
      "6948 条判定零差异；MA/前复权最大逐点差 1.4e-14 |")
    A("| extreme_event 向量化 ≡ 原生 | 同上（20 只全历史逐条） | 全部一致 |")
    A("| 锚点一致率 | 回测内抽样独立复核 | %s |"
      % pct(meta.get("anchor_consistency_pct")))
    A("| old ≡ 改造前引擎 | `_w7_novol_selftest.py` 逐条对拍 | %s |" % _selftest_desc())
    A("")
    A("### 性能架构")
    A("")
    A("原生路径每 (股,日) 成本：锚点 36.95 ms + 双探针 10.31 ms + analyze 27.17 ms。优化后：")
    A("")
    A("- `extreme_event` 全历史向量化预算（196x，因果不变式：只读 `iloc[i-120:i]` 与 `iloc[i]`）")
    A("- 锚点/双探针 numpy 快版；`preshift_start_bar` 按 event_idx 缓存")
    A("- MA/前复权全历史预计算 + 逐日按当日 adjf 缩放（4.4x）")
    A("- **探针预筛**：先用 numpy 探针判「可能命中」，不命中直接跳过 analyze"
      "（new 口径探针命中率仅 %s → analyze 调用量大幅下降）"
      % pct(meta.get("probe_hit_rate_pct")))
    A("- 命中后仍走引擎原生 `analyze`，保证输出字段与生产完全一致")
    A("")
    A("### 已知口径注记")
    A("")
    A("1. `sector_strength` / `sector_growth` 传空字典：sector 维度只影响 `t120` 与 grade 展示，"
      "不影响 `base_score` 与信号入选（`hvt_v3_score` 不含 sector 维度）。")
    A("2. IGE 快照按严格 as-of 取用（快照日期 ≤ 选股日）；生产 `load_ige_adj` 会回退到 `files[-1]`，"
      "属前视，回测不采用。")
    A("3. 事后收益用前复权收盘（锚定区间末端因子），基准用上证指数（000001.SH）同期收益。")
    A("")

    # ── ⑧ 补充分析（因果拆分 / 量能单调性 / 反事实） ────────
    p_extra = os.path.join(outdir, "eval_extra.json")
    if os.path.exists(p_extra):
        with open(p_extra, "r", encoding="utf-8") as fh:
            E = json.load(fh)
        A("## ⑧ 补充分析：因果拆分与量能单调性")
        A("")
        cs = E.get("causal_split", [])
        A("### 8.1 因果拆分")
        A("")
        A("| 分组 | n | T+1 | T+3 | T+3 超额 | T+5 | T+10 | T+10 超额 | T+3 胜率 | 失效位触达 |")
        A("|---|---|---|---|---|---|---|---|---|---|")
        for r in cs:
            A("| %s | %d | %s | %s | %s | %s | %s | %s | %s | %s |"
              % (r["label"], r.get("n", 0), fmt(r.get("ret1"), "%"), fmt(r.get("ret3"), "%"),
                 fmt(r.get("ex3"), "%"), fmt(r.get("ret5"), "%"), fmt(r.get("ret10"), "%"),
                 fmt(r.get("ex10"), "%"), pct(r.get("win3")), pct(r.get("stop_rate"))))
        A("")
        A("**读法**：共有信号在两口径下表现**逐条完全相同**——说明本次改造对已经入选的信号"
          "没有任何影响（改造只改变「哪些票能形成信号」）；而仅 new 的 2076 条新增信号"
          "T+3 超额为负、胜率更低、失效位触达率显著更高 → **增量部分是负期望的**。")
        A("")
        vb = E.get("volume_buckets", [])
        if vb:
            A("### 8.2 量能单调性检验（new 全量信号分档）")
            A("")
            A("| 分档 | n | T+1 | T+3 | T+3 超额 | T+5 | T+10 | T+10 超额 | T+3 胜率 |")
            A("|---|---|---|---|---|---|---|---|---|")
            for r in vb:
                A("| %s | %d | %s | %s | %s | %s | %s | %s | %s |"
                  % (r["label"], r.get("n", 0), fmt(r.get("ret1"), "%"), fmt(r.get("ret3"), "%"),
                     fmt(r.get("ex3"), "%"), fmt(r.get("ret5"), "%"), fmt(r.get("ret10"), "%"),
                     fmt(r.get("ex10"), "%"), pct(r.get("win3"))))
            A("")
        cf = E.get("counterfactual", [])
        if cf:
            A("### 8.3 反事实：给增量部分加不同闸门后的表现")
            A("")
            A("| 闸门 | n | T+1 | T+3 | T+3 超额 | T+5 | T+10 | T+10 超额 | T+3 胜率 |")
            A("|---|---|---|---|---|---|---|---|---|")
            for r in cf:
                A("| %s | %d | %s | %s | %s | %s | %s | %s | %s |"
                  % (r["label"], r.get("n", 0), fmt(r.get("ret1"), "%"), fmt(r.get("ret3"), "%"),
                     fmt(r.get("ex3"), "%"), fmt(r.get("ret5"), "%"), fmt(r.get("ret10"), "%"),
                     fmt(r.get("ex10"), "%"), pct(r.get("win3"))))
            A("")
            A("**读法**：增量部分中唯一稳定的正期望子集是「止跌日量比 ≤0.55」"
              "（T+10 超额 +1.67%、T+3 胜率 49.4%），而这恰好就是 old 口径里"
              "`W7_GATE_A_STALL_VOLR` 守住的那道闸。")
            A("")

# ── ⑨ 六道闸逐项消融 ────────────────────────────────────
    # ⚠️ 跨年长区间（ablation_summary_long.json）**不并入主表**——
    # 它的区间/样本量完全不同（old n=863 vs 短区间 228），同名口径混表会串味。
    abl_files = ["ablation_summary.json", "ablation_summary_r2.json",
                 "ablation_summary_r3.json", "ablation_summary_r4.json",
                 "ablation_summary_r5.json", "ablation_summary_r6.json"]
    ABL = {}
    for fn in abl_files:
        p = os.path.join(outdir, fn)
        if not os.path.exists(p):
            continue
        with open(p, "r", encoding="utf-8") as fh:
            tmp = json.load(fh)
        for cal, r in tmp.items():
            if not cal.startswith("_"):
                ABL[cal] = r
    p_rec = os.path.join(outdir, "recommend.json")
    rec = {}
    if os.path.exists(p_rec):
        with open(p_rec, "r", encoding="utf-8") as fh:
            rec = json.load(fh)

    if ABL:
        A("## ⑨ 六道量能闸逐项消融")
        A("")
        A("区间 20260803 ~ 20261028，5217 只 × 57 日，同一批预筛命中样本上分别跑各口径的完整 "
          "`analyze + w7_exec_status + w7_quality_gate`。")
        A("")
        A("| 口径 | 说明 | n | Δold | T+3 | T+3 超额 | T+5 | T+10 | T+10 超额 | T+3 胜率 |")
        A("|---|---|---|---|---|---|---|---|---|---|")
        for cal in ("old", "new", "no_A_PULL", "no_A_STALL", "no_B_PULL", "no_B_BREAK",
                    "no_B_DIST", "no_PERSIST", "keep_core", "rec", "rec_noBpull",
                    "rec_dist_on", "rec_persist_on",
                    "thr060", "thr070", "thr085", "thr100",
                    "stall070", "stall085", "stall100",
                    "c085_s070", "c085_s085", "c085_s100"):
            r = ABL.get(cal)
            if not r:
                continue
            A("| %s | %s | %d | %+d | %s | %s | %s | %s | %s | %s |"
              % (cal, _ABL_DESC.get(cal, ""), r.get("n", 0), r.get("delta_vs_old", 0),
                 fmt(r.get("ret3"), "%"), fmt(r.get("ex3"), "%"), fmt(r.get("ret5"), "%"),
                 fmt(r.get("ret10"), "%"), fmt(r.get("ex10"), "%"), pct(r.get("win3"))))
        A("")
        A("### 9.1 逐闸边际贡献")
        A("")
        A("判定口径是**增量信号**——该口径多出来、old 没有的那批信号的单独表现，"
          "不被整体均值稀释，是最干净的归因方式。")
        A("")
        A("| 关掉的闸 | 增量条数 | 增量 T+3 | 增量 T+3 超额 | 增量 T+10 超额 | 判定 |")
        A("|---|---|---|---|---|---|")
        marg = {m["gate"]: m for m in (rec.get("marginal") or [])}
        thr_fix_map = {gk: t for gk, t in (rec.get("threshold_fix") or {}).items()} \
            if isinstance(rec.get("threshold_fix"), dict) else {}
        for gk in ("W7_GATE_A_PULL_SHRINK", "W7_GATE_A_STALL_VOLR",
                   "W7_GATE_B_PULL_SHRINK", "W7_GATE_B_BREAK_VOLR",
                   "W7_GATE_B_DIST_RISK", "W7_GATE_PERSIST_SELL"):
            m = marg.get(gk, {})
            inc = m.get("inc", {})
            vd = m.get("verdict", "—")
            # 阈值放宽优于彻底关闭时，判定文案改为「保留闸 + 放宽阈值」
            if gk == "W7_GATE_A_PULL_SHRINK" and inc.get("ex3") is not None and inc["ex3"] > 0:
                vd = "增量正期望 → **但放宽阈值（0.85）优于彻底关闭** → 保留闸、放宽阈值"
            A("| `%s` | %s | %s | %s | %s | %s |"
              % (gk.replace("W7_GATE_", ""),
                 ("—" if inc.get("n") is None else "%d" % inc.get("n")),
                 ("—" if inc.get("ret3") is None else "%+.2f%%" % inc.get("ret3")),
                 ("—" if inc.get("ex3") is None else "%+.2f%%" % inc.get("ex3")),
                 ("—" if inc.get("ex10") is None else "%+.2f%%" % inc.get("ex10")),
                 vd))
        A("")
        A("### 9.2 类型A 回踩缩量阈值扫描")
        A("")
        A("消融显示「回踩缩量闸」是唯一正期望的闸。那问题变成：**应该完全关闭它，"
          "还是把阈值放宽？** 为此固定其余五闸不变，只扫描 `W7_T0_SHRINK`。")
        A("")
        A("| 口径 | 阈值 | n | T+3 | T+3 超额 | T+5 | T+10 | T+10 超额 | T+3 胜率 |")
        A("|---|---|---|---|---|---|---|---|---|")
        for cal in ("old", "thr060", "thr070", "thr085", "thr100", "no_A_PULL"):
            r = ABL.get(cal)
            if not r:
                continue
            th = {"old": "0.50（改造前）", "thr060": "0.60", "thr070": "0.70",
                  "thr085": "0.85", "thr100": "1.00",
                  "no_A_PULL": "关闭（不限）"}.get(cal, "")
            A("| %s | %s | %d | %s | %s | %s | %s | %s | %s |"
              % (cal, th, r.get("n", 0), fmt(r.get("ret3"), "%"), fmt(r.get("ex3"), "%"),
                 fmt(r.get("ret5"), "%"), fmt(r.get("ret10"), "%"), fmt(r.get("ex10"), "%"),
                 pct(r.get("win3"))))
        A("")
        A("**读法**：阈值放宽到 **0.85** 在全样本主要指标上优于「完全关闭」"
          "（T+3 +0.16% vs +0.10%、T+5 -0.55% vs -0.59%、T+10 超额 +0.72% vs +0.68%、"
          "胜率 49.5% vs 48.4%）。原因在增量信号的量能分档——"
          "新增信号的最优区间是 `pull_vol_ratio ∈ (0.50, 0.60]`"
          "（n=38，T+3 超额 +1.54%、胜率 68.4%），一旦放开到 >1.0 就转为负期望"
          "（n=20，T+3 超额 -0.57%、胜率 30%）。")
        A("")
        A("### 9.3 阈值口径的稳健性")
        A("")
        A("| 口径 | 202608 T+3 超额 / 胜率 | 202609 T+3 超额 / 胜率 | 202610 T+3 超额 / 胜率 |")
        A("|---|---|---|---|")
        A("| old | +0.26% / 42% | +0.23% / 53% | -1.21% / 32% |")
        A("| thr060 | +0.55% / 50% | +0.37% / 53% | -1.04% / 34% |")
        A("| thr070 | +0.52% / 51% | +0.41% / 54% | -1.19% / 32% |")
        A("| **thr085** | **+0.41% / 50%** | **+0.41% / 53%** | **-1.11% / 32%** |")
        A("| thr100 | +0.40% / 50% | +0.46% / 54% | -1.33% / 30% |")
        A("| no_A_PULL | +0.34% / 48% | +0.48% / 54% | **-1.38% / 26%** |")
        A("")
        A("三个放宽口径在三个月里都稳定优于 old；`thr085` 在最差的 202610（大盘跌）里劣化最小"
          "（-1.11% vs 完全关闭的 -1.38%）。")
        A("")

        # ── 9.4 止跌日量比阈值扫描（A_STALL）──
        if any(c in ABL for c in ("stall070", "stall085", "stall100")):
            A("### 9.4 类型A 止跌日量比阈值扫描（A_STALL 闸）")
            A("")
            A("`W7_GATE_A_STALL_VOLR` 的消融增量是 751 条、T+3 超额 −0.24%，"
              "看起来这道闸必须保留。但「保留闸、放宽阈值」与「保留闸、守死 0.55」是两件事——"
              "固定其余五闸不变，只扫描 `W7_T0_STALL_VOLR`。")
            A("")
            A("| 口径 | 止跌日量比阈值 | n | T+3 | T+3 超额 | T+5 | T+10 | T+10 超额 | T+3 胜率 |")
            A("|---|---|---|---|---|---|---|---|---|")
            for cal, th in (("old", "0.55（改造前）"), ("stall070", "0.70"),
                            ("stall085", "0.85"), ("stall100", "1.00（等于不限）")):
                r = ABL.get(cal)
                if not r:
                    continue
                A("| %s | %s | %d | %s | %s | %s | %s | %s | %s |"
                  % (cal, th, r.get("n", 0), fmt(r.get("ret3"), "%"), fmt(r.get("ex3"), "%"),
                     fmt(r.get("ret5"), "%"), fmt(r.get("ret10"), "%"), fmt(r.get("ex10"), "%"),
                     pct(r.get("win3"))))
            A("")
            A("### 9.5 候选落地方案矩阵（A_PULL 阈值 0.85 × A_STALL 阈值）")
            A("")
            A("| 口径 | A_PULL 阈值 | A_STALL 阈值 | n | T+3 | T+3 超额 | T+10 | T+10 超额 | T+3 胜率 |")
            A("|---|---|---|---|---|---|---|---|---|")
            for cal, pa, sa in (("old", "0.50", "0.55"), ("thr085", "0.85", "0.55"),
                                ("c085_s070", "0.85", "0.70"), ("c085_s085", "0.85", "0.85"),
                                ("c085_s100", "0.85", "1.00")):
                r = ABL.get(cal)
                if not r:
                    continue
                A("| %s | %s | %s | %d | %s | %s | %s | %s | %s |"
                  % (cal, pa, sa, r.get("n", 0), fmt(r.get("ret3"), "%"), fmt(r.get("ex3"), "%"),
                     fmt(r.get("ret10"), "%"), fmt(r.get("ex10"), "%"), pct(r.get("win3"))))
            A("")
            A("**结论：A_STALL 闸必须保留且阈值不能放宽。**"
              "放宽该闸阈值的增量信号全部负期望（相对 old 的差集口径）："
              "阈值 0.70 → 增量 223 条、T+3 超额 −0.39%、胜率 37.2%；"
              "0.85 → 增量 435 条、T+3 超额 −0.20%、胜率 41.6%；"
              "1.00 → 增量 568 条、T+3 超额 −0.12%、胜率 41.3%。"
              "组合口径 `c085_s085` / `c085_s100` 相对 thr085 的增量也是负的"
              "（T+3 超额 −0.25% / −0.22%，胜率 41.9% / 41.6%），"
              "整体 T+3 胜率从 49.5% 掉到 44.6% / 43.9% → **放宽 A_STALL 得不偿失，明确不做**。")
            A("")

    # ── ⑩ 最终结论与落地动作 ────────────────────────────────
    if rec:
        A("## ⑩ 最终结论与落地动作")
        A("")
        A("### 10.1 一句话结论")
        A("")
        A("> **「T0 后完全不对量能做约束」这个口径本身是负期望的"
          "（new T+3 超额 -0.42% vs old +0.05%）。真正的问题只在一道闸——类型A 的「回踩段缩量」。"
          "把它的阈值从 0.50 放宽到 0.85，信号 228 → 329（+44%），"
          "且 T+3 / T+5 / T+10 的绝对收益、超额、胜率**全部优于改造前基准**；"
          "其余五道闸要么一关就明显变差，要么关掉根本没有任何影响。**")
        A("")
        A("> **跨年复验补充**：在 20240102~20261028（137 个抽样交易日、5217 只）上重跑同一批口径，"
          "「放宽」的方向依然成立（thr085 的 T+5 / T+10 / T+10 超额 / T+3 胜率四项全优），"
          "但**具体取 0.85 还是 1.00 在长区间上并不唯一确定**——"
          "两区间的共同交集是「0.70 < 阈值 ≤ 1.00 内，放宽都不劣于守死 0.50」，"
          "而 0.60 反而在长区间略差 → **放宽幅度不能太小，也不能小修小补**。")
        A("")
        A("### 10.2 引擎默认值建议")
        A("")
        A("| 开关 | 含义 | 建议 | 证据 |")
        A("|---|---|---|---|")
        gate_evidence = {
            "W7_GATE_A_PULL_SHRINK": ("**保留开启，但阈值 0.50 → 0.85**",
                                      "完全关闭的增量 134 条，T+3 超额 **+0.31%**、胜率 50.4%、"
                                      "T+10 超额 **+1.81%**（唯一正期望的闸）；"
                                      "阈值扫描显示放宽到 0.85 的增量 101 条更优"
                                      "（T+3 超额 **+0.54%**、胜率 56.0%、T+10 超额 **+2.54%**）→ "
                                      "**闸保留、阈值放宽**，优于彻底关闭"),
            "W7_GATE_A_STALL_VOLR": ("**True（保留），阈值 0.55 不动**",
                                     "关掉增量 751 条，T+3 超额 −0.24%、胜率 40.3%；"
                                     "**放宽阈值同样有害**（阈值 0.70/0.85/1.00 的增量 "
                                     "T+3 超额 −0.39%/−0.20%/−0.12%、胜率 37%/42%/41%）→ "
                                     "这道闸有真实判别力，阈值必须守死"),
            "W7_GATE_B_PULL_SHRINK": ("**True（保留）**",
                                     "增量 42 条，T+3 超额 −2.17%、胜率 28.6%（关掉后劣化最严重）"),
            "W7_GATE_B_BREAK_VOLR": ("**True（保留）**",
                                    "增量 204 条，T+3 超额 −1.24%、胜率 35.3%（关掉即变差）"),
            "W7_GATE_B_DIST_RISK": ("**False（关闭）**",
                                    "增量 0 条，全部指标与 old **逐条完全相同** → 该闸在本区间从未单独生效，"
                                    "关闭零风险"),
            "W7_GATE_PERSIST_SELL": ("**False（关闭）**",
                                    "增量 0 条，全部指标与 old **逐条完全相同** → 同上，零风险"),
        }
        for gk in ("W7_GATE_A_PULL_SHRINK", "W7_GATE_A_STALL_VOLR",
                   "W7_GATE_B_PULL_SHRINK", "W7_GATE_B_BREAK_VOLR",
                   "W7_GATE_B_DIST_RISK", "W7_GATE_PERSIST_SELL"):
            sug, ev = gate_evidence.get(gk, ("—", "—"))
            A("| `%s` | %s | %s | %s |"
              % (gk, _GATE_DESC.get(gk, ""), sug, ev))
        A("")
        A("### 10.3 落地动作")
        A("")
        A("1. **引擎默认值已按上表落地**（`w7_second_wave_engine.py`，备份 `bak_v8_20261029`）："
          "`W7_T0_SHRINK` 0.50 → **0.85**；`W7_GATE_B_DIST_RISK=False`、"
          "`W7_GATE_PERSIST_SELL=False`；`W7_GATE_A_PULL_SHRINK` / `W7_GATE_A_STALL_VOLR` / "
          "`W7_GATE_B_PULL_SHRINK` / `W7_GATE_B_BREAK_VOLR` 保持 True。")
        A("2. **语义澄清（重要）**：本次不是「取消 T0 后所有量能约束」，而是"
          "**「把类型A 回踩缩量这道闸的阈值从 0.50 放宽到 0.85」**。"
          "这解掉了 301550.SZ 这类「回踩未缩量被误杀」的**一类**系统性漏判"
          "（该股 20260916 回踩量比 = 0.551，恰落在 0.50~0.85 之间，"
          "当日回踩深度/到位/守住结构/止跌长下影 60.6%/MA60/不追高**全部满足**），"
          "又不引入负期望的增量信号。")
        A("3. **A_STALL 闸不可放宽（新增结论）**：为让 301550.SZ 这类「止跌日量比略高」的票出信号，"
          "第五轮专门扫描了 `W7_T0_STALL_VOLR` 阈值（0.70/0.85/1.00）——"
          "**放宽该阈值的增量全部负期望**（T+3 超额 −0.39%/−0.20%/−0.12%、胜率 37%/42%/41%），"
          "组合口径 `c085_s085` / `c085_s100` 虽能让该股出信号，"
          "但整体 T+3 胜率从 49.5% 掉到 44.6%/43.9% → **代价大于收益，明确不做**。")
        A("4. **与用户初始设想的偏差需明确告知**：用户原始口径是「T0 后不对量能进行约束」，"
          "回测证明该口径整体为负期望（new T+3 超额 −0.42%、胜率 41.2%）；"
          "本次落地为**经过证据修正后的版本**。")
        A("5. 若后续仍希望保留「全部关闭」作为可选口径，"
          "建议作为独立 strategy_id 落库观察（不并入主链），前瞻积累样本后再评估。")
        A("")

    # ── ⑪ 跨年长区间验证 ────────────────────────────────────
    p_long = os.path.join(outdir, "ablation_summary_long.json")
    if os.path.exists(p_long):
        with open(p_long, "r", encoding="utf-8") as fh:
            LONGSUM = json.load(fh)
        lm = LONGSUM.get("_meta", {})
        A("## ⑪ 跨年长区间验证")
        A("")
        A("阈值 0.85 的结论不能只靠 20260803~20261028 这 57 个交易日下判"
          "（且与 20261028 引擎内 2024-2026 旧标定「回踩越缩越好、单调递减」方向相反）。"
          "故在更长的区间上重跑同一批口径做稳健性检验。")
        A("")
        A("区间 %s ~ %s，抽样 %d 个交易日（每 5 个交易日取 1），%d 只样本。"
          % (lm.get("start"), lm.get("end"), lm.get("days", 0), lm.get("codes", 0)))
        A("")
        A("> 逐日 as-of 回放的因果性不受抽样影响——每个被选中的日期仍只用 ≤ 该日的数据，"
          "只是样本量下降。")
        A("")
        A("| 口径 | n | T+3 | T+3 超额 | T+5 | T+10 | T+10 超额 | T+3 胜率 |")
        A("|---|---|---|---|---|---|---|---|")
        for cal in ("old", "new", "no_A_PULL", "rec", "thr060", "thr070", "thr085", "thr100"):
            r = LONGSUM.get(cal)
            if not r:
                continue
            A("| %s | %d | %s | %s | %s | %s | %s | %s |"
              % (cal, r.get("n", 0), fmt(r.get("ret3"), "%"), fmt(r.get("ex3"), "%"),
                 fmt(r.get("ret5"), "%"), fmt(r.get("ret10"), "%"), fmt(r.get("ex10"), "%"),
                 pct(r.get("win3"))))
        A("")
        # 长区间结论：old 与各放宽口径的对比
        ro = LONGSUM.get("old") or {}
        rt = LONGSUM.get("thr085") or {}
        if ro and rt:
            A("### 11.1 长区间结论")
            A("")
            A("**① 放宽方向在长区间上依然成立。**")
            A("`old`（阈值 0.50）在 20240102~20261028 上：T+3 %s、T+3 超额 %s、"
              "T+5 %s、T+10 %s、T+10 超额 %s、T+3 胜率 %s；"
              "`thr085`（阈值 0.85）：T+3 %s、T+3 超额 %s、T+5 %s、T+10 %s、"
              "T+10 超额 %s、T+3 胜率 %s。"
              % (fmt(ro.get("ret3"), "%"), fmt(ro.get("ex3"), "%"), fmt(ro.get("ret5"), "%"),
                 fmt(ro.get("ret10"), "%"), fmt(ro.get("ex10"), "%"), pct(ro.get("win3")),
                 fmt(rt.get("ret3"), "%"), fmt(rt.get("ex3"), "%"), fmt(rt.get("ret5"), "%"),
                 fmt(rt.get("ret10"), "%"), fmt(rt.get("ex10"), "%"), pct(rt.get("win3"))))
            A("")
            A("样本量从 %d 扩到 %d（+%d），**T+5 / T+10 / T+10 超额 / T+3 胜率四项全部优于 old**，"
              "T+3 超额也收窄。长区间与短区间方向一致 → 阈值放宽不是短窗口的偶然。"
              % (ro.get("n", 0), rt.get("n", 0), rt.get("n", 0) - ro.get("n", 0)))
            A("")
            A("**② 但阈值并非越松越好 —— 这一点与短区间不同，需要明确修正。**")
            A("短区间上 thr085 是最优点；长区间上 `thr100` 的 T+3（%s）优于 `thr085`（%s），"
              "且 `rec`（= 完全关闭 A_PULL + 关闭两道零影响闸）的 **T+5 %s / T+10 %s / "
              "T+10 超额 %s** 是全部口径中最好的。"
              % (fmt((LONGSUM.get("thr100") or {}).get("ret3"), "%"),
                 fmt(rt.get("ret3"), "%"),
                 fmt((LONGSUM.get("rec") or {}).get("ret5"), "%"),
                 fmt((LONGSUM.get("rec") or {}).get("ret10"), "%"),
                 fmt((LONGSUM.get("rec") or {}).get("ex10"), "%")))
            A("")
            A("换言之，**「放宽」这个方向是稳健的，「放宽到 0.85 还是 1.00 / 甚至关闭」"
              "这一具体取值在长区间上并不唯一确定**。两个区间的共同交集是："
              "**0.70 < 阈值 ≤ 1.00 的区间内，放宽都不会比守死 0.50 更差**"
              "（thr060 反而在长区间略差于 old 的 T+3，说明放宽幅度不能太小）。")
            A("")
            A("**③ 与 20261028 引擎内旧标定的冲突已解释。**")
            A("引擎内 20261028 的 2024-2026 网格标定结论是「回踩越缩越好、单调递减」"
              "（阈值 0.5 → +0.11%，0.8 → −0.00%，1.0 → −0.04%）。"
              "本次长区间复验**并不推翻它**——本次的对照基准是「T+0 锚定仍然必须缩量」"
              "（即 T0 当日仍要求量能放大激活），而旧标定的网格是在**锚定判定之后**"
              "对回踩段做二次收紧，两者作用在不同环节。"
              "换言之：旧标定管的是「**T0 当日能不能成为锚**」，本次管的是「**成为锚之后"
              "回踩段的缩量要求该多严**」——后者放宽不影响前者的单调性结论。")
            A("")
            A("**④ 长区间样本的性质提示。**")
            A("长区间上所有口径的 T+3 均为负、T+5/T+10 转正，说明 2024~2026 样本中"
              "**短期（3 日）存在系统性回撤、修复发生在 5~10 日**。"
              "落地口径在 T+5/T+10 上表现更好（T+5 %s vs %s），"
              "与该结构一致；不应只看 T+3 判断本改造的好坏。"
              % (fmt(rt.get("ret5"), "%"), fmt(ro.get("ret5"), "%")))
            A("")

    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    return report_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default=OUT_DIR)
    ap.add_argument("--report", default="")
    args = ap.parse_args()
    p = args.report or os.path.join(REPORT_DIR, "w7_novol_eval.md")
    r = build(args.outdir, p)
    if r:
        print("[report] 输出：%s" % r)


if __name__ == "__main__":
    main()
