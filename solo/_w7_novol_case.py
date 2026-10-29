# -*- coding: utf-8 -*-
"""针对用户原始痛点的定向复核：某只票在某口径下能否形成 W7 信号、卡在哪一条。

背景
----
20261028 用户反馈：301550.SZ 从 20260801 到 20261028 共 57 个交易日，
类型A/类型B 全程 0 信号，卡点实测为「回踩未缩量」（= W7_GATE_A_PULL_SHRINK）。
第一轮全市场消融结论：**只关掉 A_PULL 这一道闸**，
信号 228 → 362（+134），T+3 收益从 -0.04% 翻到 +0.08%，
T+3 超额 +0.05% → +0.15%，T+10 超额 -0.04% → +0.61%，胜率 46.7% → 48.1%
—— 也就是说「回踩缩量」这道闸既误杀形态、又确实是有效的判别力来源。

本脚本对指定代码逐日跑指定口径，输出：
  · 每日锚点
  · 类型A / 类型B 探针在「各闸开启组合」下的命中情况
  · 最终是否形成信号、被哪道质量门拦下

用法
----
  python _w7_novol_case.py --codes 301550.SZ --start 20260801 --end 20261028
  python _w7_novol_case.py --codes 301550.SZ --calibers old,rec,new
"""
import argparse
import importlib
import os
import sys

import pandas as pd

SOLO = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(SOLO, "w7_second_wave_engine.py")
if SOLO not in sys.path:
    sys.path.insert(0, SOLO)

GATE_KEYS = ("W7_GATE_A_PULL_SHRINK", "W7_GATE_A_STALL_VOLR", "W7_GATE_B_PULL_SHRINK",
             "W7_GATE_B_BREAK_VOLR", "W7_GATE_B_DIST_RISK", "W7_GATE_PERSIST_SELL")

CALIBERS = {
    "old": dict.fromkeys(GATE_KEYS, True),
    "new": dict.fromkeys(GATE_KEYS, False),
    "rec": {"W7_GATE_A_PULL_SHRINK": False, "W7_GATE_A_STALL_VOLR": True,
            "W7_GATE_B_PULL_SHRINK": True, "W7_GATE_B_BREAK_VOLR": True,
            "W7_GATE_B_DIST_RISK": False, "W7_GATE_PERSIST_SELL": False},
    "no_A_PULL": {**dict.fromkeys(GATE_KEYS, True), "W7_GATE_A_PULL_SHRINK": False},
    "keep_core": {"W7_GATE_A_PULL_SHRINK": False, "W7_GATE_A_STALL_VOLR": True,
                  "W7_GATE_B_PULL_SHRINK": False, "W7_GATE_B_BREAK_VOLR": True,
                  "W7_GATE_B_DIST_RISK": False, "W7_GATE_PERSIST_SELL": False},
}

# 消融阶段用的名字（与 _w7_novol_ablation.py 保持一致）
CALIBERS.update({
    "keep_A_STALL": {"W7_GATE_A_PULL_SHRINK": False, "W7_GATE_A_STALL_VOLR": True,
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
    "rec_noBpull": {"W7_GATE_A_PULL_SHRINK": False, "W7_GATE_A_STALL_VOLR": True,
                    "W7_GATE_B_PULL_SHRINK": False, "W7_GATE_B_BREAK_VOLR": True,
                    "W7_GATE_B_DIST_RISK": False, "W7_GATE_PERSIST_SELL": False},
    "rec_dist_on": {"W7_GATE_A_PULL_SHRINK": False, "W7_GATE_A_STALL_VOLR": True,
                    "W7_GATE_B_PULL_SHRINK": True, "W7_GATE_B_BREAK_VOLR": True,
                    "W7_GATE_B_DIST_RISK": True, "W7_GATE_PERSIST_SELL": False},
    "rec_persist_on": {"W7_GATE_A_PULL_SHRINK": False, "W7_GATE_A_STALL_VOLR": True,
                       "W7_GATE_B_PULL_SHRINK": True, "W7_GATE_B_BREAK_VOLR": True,
                       "W7_GATE_B_DIST_RISK": False, "W7_GATE_PERSIST_SELL": True},
    "rec_all_off_but_A": {"W7_GATE_A_PULL_SHRINK": False, "W7_GATE_A_STALL_VOLR": True,
                           "W7_GATE_B_PULL_SHRINK": False, "W7_GATE_B_BREAK_VOLR": False,
                           "W7_GATE_B_DIST_RISK": False, "W7_GATE_PERSIST_SELL": False},
    # ── final = 20261029 落地到引擎的默认口径（thr085 方案）─────────
    # A_PULL 闸保留、阈值 W7_T0_SHRINK=0.85（引擎常量已是该值）
    "final": {"W7_GATE_A_PULL_SHRINK": True, "W7_GATE_A_STALL_VOLR": True,
              "W7_GATE_B_PULL_SHRINK": True, "W7_GATE_B_BREAK_VOLR": True,
              "W7_GATE_B_DIST_RISK": False, "W7_GATE_PERSIST_SELL": False},
    # ── 候选落地：final 基础上再放宽 A_STALL 阈值 ──────────────
    "f_s070": {"W7_GATE_A_PULL_SHRINK": True, "W7_GATE_A_STALL_VOLR": True,
               "W7_GATE_B_PULL_SHRINK": True, "W7_GATE_B_BREAK_VOLR": True,
               "W7_GATE_B_DIST_RISK": False, "W7_GATE_PERSIST_SELL": False},
    "f_s085": {"W7_GATE_A_PULL_SHRINK": True, "W7_GATE_A_STALL_VOLR": True,
               "W7_GATE_B_PULL_SHRINK": True, "W7_GATE_B_BREAK_VOLR": True,
               "W7_GATE_B_DIST_RISK": False, "W7_GATE_PERSIST_SELL": False},
    "f_s100": {"W7_GATE_A_PULL_SHRINK": True, "W7_GATE_A_STALL_VOLR": True,
               "W7_GATE_B_PULL_SHRINK": True, "W7_GATE_B_BREAK_VOLR": True,
               "W7_GATE_B_DIST_RISK": False, "W7_GATE_PERSIST_SELL": False},
})

# 口径 → 阈值覆盖（与 _w7_novol_ablation.py 的 THRESH_OVERRIDE 保持一致）
CASE_THRESH = {
    "final": {"W7_T0_SHRINK": 0.85},
    "f_s070": {"W7_T0_SHRINK": 0.85, "W7_T0_STALL_VOLR": 0.70},
    "f_s085": {"W7_T0_SHRINK": 0.85, "W7_T0_STALL_VOLR": 0.85},
    "f_s100": {"W7_T0_SHRINK": 0.85, "W7_T0_STALL_VOLR": 1.00},
}

# 引擎阈值的基准值（重置用）
CASE_BASE_THRESH = {
    "W7_T0_SHRINK": 0.50, "W7_T0_SHRINK_B": 0.70,
    "W7_T0_STALL_VOLR": 0.55, "W7_T0_BREAK_VOLR": 2.20,
}


def load_engine(path=ENGINE):
    if SOLO not in sys.path:
        sys.path.insert(0, SOLO)
    modname = os.path.splitext(os.path.basename(path))[0]
    if modname in sys.modules:
        return sys.modules[modname]
    return importlib.import_module(modname)


def set_caliber(mod, name):
    for k, v in CALIBERS[name].items():
        setattr(mod, k, v)
    for k, v in CASE_BASE_THRESH.items():
        setattr(mod, k, v)
    for k, v in CASE_THRESH.get(name, {}).items():
        setattr(mod, k, v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", default="301550.SZ", help="逗号分隔的代码")
    ap.add_argument("--start", default="20260801")
    ap.add_argument("--end", default="20261028")
    ap.add_argument("--calibers", default="old,rec,new")
    ap.add_argument("--verbose", action="store_true", help="逐日打印锚点与命中细节")
    args = ap.parse_args()

    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    cals = [c.strip() for c in args.calibers.split(",") if c.strip()]
    for c in cals:
        if c not in CALIBERS:
            print("[case] 未知口径 %s，可用：%s" % (c, ", ".join(CALIBERS)))
            return

    mod = load_engine()
    reader = mod.CacheReader()
    reader.load_fina()

    dates = [str(r[0]) for r in reader.conn.execute(
        "SELECT DISTINCT trade_date FROM index_daily_cache WHERE ts_code=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
        (mod.MARKET_INDEX_CODE, args.start, args.end)).fetchall()]
    if not dates:
        print("[case] 区间内无交易日")
        return

    # 锚点参照特征 / 市场环境
    ctx = {}
    for D in dates:
        anchors = {}
        for label, (acode, adate) in mod.ANCHORS.items():
            anchors[label] = mod.anchor_features(reader.bars_sql(acode, D), adate)
        mdates, mvals = reader.market_curve(D)
        ctx[D] = {"anchors": anchors, "mkt": mod.MarketCtx(mdates, mvals),
                  "regime": mod.market_regime(reader.conn, D)}

    for code in codes:
        print("=" * 100)
        print("【%s】%s ~ %s（%d 个交易日）" % (code, args.start, args.end, len(dates)))
        print("=" * 100)
        b = reader.basic.loc[code] if code in reader.basic.index else None
        name = str(b.get("name", code)) if b is not None else code
        industry = str(b.get("industry", "") or "") if b is not None else ""
        if "ST" in name.upper() or "退" in name:
            print("[case] ST/退市股，跳过")
            continue

        hist = reader.bars_sql(code, args.end)
        if hist is None or hist.empty:
            print("[case] 无行情数据")
            continue

        rows = []
        for D in dates:
            sub = hist[hist.trade_date.astype(str) <= D].reset_index(drop=True)
            if len(sub) < mod.MIN_BARS:
                continue
            sub = mod._fill_ma_columns(sub.copy(), price=mod._qfq_price(sub.copy()))
            anchor = mod.find_event_anchor(sub)
            if anchor is None:
                continue
            ev_idx, ep = anchor
            rec = {"date": D, "anchor_date": str(sub.trade_date.iloc[ev_idx]),
                   "ep": round(float(ep), 1)}
            for cal in cals:
                set_caliber(mod, cal)
                r = mod.analyze(code, name, industry, sub, ctx[D]["anchors"], reader=reader,
                                mkt=ctx[D]["mkt"], sector_strength={}, sector_growth={},
                                ige_adj=None, event_hint=anchor)
                if not r:
                    rec[cal] = "-"
                    continue
                st, fail_line, fail_name = mod.w7_exec_status(r)
                ok, why = mod.w7_quality_gate(r)
                if r["state"] in mod.ACTION_BUY_STATES and ok:
                    rec[cal] = "★%s/%s" % (r["state"], r.get("buy"))
                else:
                    rec[cal] = "%s%s" % (r["state"], "" if ok else " [门:%s]" % str(why)[:26])
            rows.append(rec)
            if args.verbose:
                print("  %s 锚=%s ep=%s | %s" % (
                    D, rec["anchor_date"], rec["ep"],
                    " | ".join("%s=%s" % (c, rec[c]) for c in cals)))

        if not rows:
            print("[case] 全区间无有效锚点（可能上市不足 %d 根或无放量激活日）" % mod.MIN_BARS)
            continue

        df = pd.DataFrame(rows)
        print("\n%-12s %-12s %6s %s" % ("选股日", "锚点日", "ep", " | ".join("%-22s" % c for c in cals)))
        print("-" * (34 + 24 * len(cals)))
        for _, r in df.iterrows():
            print("%-12s %-12s %6s %s" % (
                r["date"], r["anchor_date"], r["ep"],
                " | ".join("%-22s" % str(r[c]) for c in cals)))
        print()
        for c in cals:
            hit = df[df[c].astype(str).str.startswith("★")]
            print("[case] %-14s 形成信号 %d 天 / %d 天" % (c, len(hit), len(df)))
        print()


if __name__ == "__main__":
    main()
