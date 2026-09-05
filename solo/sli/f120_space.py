# -*- coding: utf-8 -*-
"""F120 space — 信号→未来空间三层量化: IC / 历史类比 / 期望值.

复用 f120.py 生产信号代码, 对 2022-06 以来每月末截面重放全部信号,
计算各信号对 T+60/T+120 前瞻收益的预测力(IC), 并对当前执行卡做
历史类比匹配 + 规则化路径模拟(stop/target EV).
"""
import glob
import os

import numpy as np
import pandas as pd

import f120 as F

H1, H2 = 60, 120
CLIP = 0.21
WIN = F.LOOKBACK
OUT = F.OUT
SIGS = ["F", "P", "E", "T", "V", "F120", "rr", "r20", "r120", "shock"]
SETUPS = ["BREAKOUT_RETEST", "BASE_BREAKOUT", "FIRST_PULLBACK", "DEEP_PULLBACK", ""]


def _norm(s):
    s = str(s).strip()
    if "-" in s:
        s = s.split(" ")[0].replace("-", "")
    return s


def rank_ic(x, y):
    d = pd.concat([x, y], axis=1).dropna()
    if len(d) < 100:
        return np.nan
    rx, ry = d.iloc[:, 0].rank(), d.iloc[:, 1].rank()
    rx, ry = rx - rx.mean(), ry - ry.mean()
    den = float(np.sqrt((rx ** 2).sum() * (ry ** 2).sum()))
    return float((rx * ry).sum() / den) if den > 0 else np.nan


def fwd_stats(pcts, i0, h):
    seg = pcts[i0:i0 + h]
    if len(seg) < h or np.isnan(seg).any():
        return None
    pa = np.cumprod(1 + np.clip(seg / 100.0, -CLIP, CLIP)) - 1
    return float(pa[-1]), float(pa.max()), float(pa.min())


def build_samples():
    full = F.load_full().drop_duplicates("ts_code").reset_index(drop=True)
    full["roe_pool_med"] = full.groupby("subsector")["roe_dt"].transform("median")
    for c in ("rank5", "purity_t5", "dominance"):
        if c not in full.columns:
            full[c] = np.nan
    frows = full.set_index("ts_code")
    codes = full["ts_code"].tolist()

    panel = F.load_panel(codes, days=9999)
    panel["trade_date"] = panel["trade_date"].map(_norm)
    panel = panel.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)

    mkey = {}
    for d in sorted(panel["trade_date"].unique()):
        mkey.setdefault(d[:6], d)
    sample_days = sorted(d for d in mkey.values() if d >= "20220630")

    gmap = dict(tuple(panel.groupby("ts_code")))
    recs = []
    for i, code in enumerate(codes, 1):
        g = gmap.get(code)
        if g is None or code not in frows.index or len(g) < 40:
            continue
        row = frows.loc[code]
        tds = g["trade_date"].values
        pcts = g["pct_chg"].astype(float).values
        for d in sample_days:
            i0 = int(np.searchsorted(tds, d, side="right"))
            sub = g.iloc[max(0, i0 - WIN):i0]
            if len(sub) < 30:
                continue
            s = F.build_series(sub)
            if s is None:
                continue
            tr = F.trend_of(s["px"].values)
            sd = F.detect_setup(s, tr)
            vo = F.volume_of(s, tr)
            cur = tr["c"]
            p, ptag = F.score_p(cur, tr, sd)
            e, etag = F.score_e(row, tr["r20"], tr["r60"], tr["r120"])
            f, ftag = F.score_f(row)
            f120v = f * 0.20 + p * 0.20 + e * 0.25 + tr["score"] * 0.20 + vo["score"] * 0.15
            setup = sd.get("setup") or ""
            rr = np.nan
            if setup:
                rr = F.levels_of(setup, sd, tr, cur).get("rr")
            f60 = fwd_stats(pcts, i0, H1)
            f120 = fwd_stats(pcts, i0, H2)
            recs.append({
                "ts_code": code, "date": d,
                "F": f, "P": p, "E": e, "T": tr["score"], "V": vo["score"], "F120": f120v,
                "setup": setup, "stage": sd.get("stage") or "",
                "p_tag": ptag, "e_tag": etag, "f_tag": ftag,
                "rr": rr, "r20": tr["r20"], "r120": tr["r120"],
                "shock": vo.get("shock"), "shrink": vo.get("shrink"),
                "fwd60": f60[0] if f60 else np.nan,
                "mfe60": f60[1] if f60 else np.nan,
                "mae60": f60[2] if f60 else np.nan,
                "fwd120": f120[0] if f120 else np.nan,
                "mfe120": f120[1] if f120 else np.nan,
                "mae120": f120[2] if f120 else np.nan,
            })
        if i % 200 == 0:
            print(f"[samples] {i}/{len(codes)} rows={len(recs)}", flush=True)
    return pd.DataFrame(recs), panel


def ic_table(df):
    rows = []
    for h in (H1, H2):
        col = f"fwd{h}"
        sub = df.dropna(subset=[col])
        for c in SIGS:
            ics = []
            for _, g in sub.groupby("date"):
                if len(g) >= 200:
                    v = rank_ic(g[c], g[col])
                    if v == v:
                        ics.append(v)
            if not ics:
                continue
            a = np.array(ics)
            sd = float(a.std(ddof=1)) if len(a) > 1 else np.nan
            rows.append({"signal": c, "horizon": h, "n_cross": len(a),
                         "ic_mean": float(a.mean()), "ic_std": sd,
                         "icir": float(a.mean()) / sd if sd and sd > 0 else np.nan,
                         "ic_pos": float((a > 0).mean())})
    return pd.DataFrame(rows)


def group_table(df):
    sub = df.dropna(subset=["fwd120"])
    base = float(sub["fwd120"].mean())
    out = []
    for col, order in (("setup", SETUPS), ("stage", ["ready", "wait", ""])):
        g = sub.groupby(col)["fwd120"].agg(n="count", mean="mean", med="median",
                                           win=lambda x: float((x > 0).mean()))
        for k in order:
            if k in g.index:
                r = g.loc[k]
                out.append({"feat": col, "value": k or "(none)", "n": int(r["n"]),
                            "fwd120_mean": float(r["mean"]), "fwd120_med": float(r["med"]),
                            "win": float(r["win"]), "excess": float(r["mean"]) - base})
    return pd.DataFrame(out), base


def sim_path(path, stop_ret, target_ret):
    hs = np.where(path <= stop_ret)[0]
    ht = np.where(path >= target_ret)[0]
    s0 = int(hs[0]) if len(hs) else -1
    t0 = int(ht[0]) if len(ht) else -1
    if s0 >= 0 and (t0 < 0 or s0 <= t0):
        return stop_ret, s0 + 1, "stop"
    if t0 >= 0:
        return target_ret, t0 + 1, "target"
    return float(path[-1]), len(path), "timeout"


def card_space(cards, df, codes):
    panel = F.load_panel(sorted(set(codes)), days=9999)
    need = panel[["ts_code", "trade_date", "pct_chg"]].copy()
    need["trade_date"] = need["trade_date"].map(_norm)
    pth = {}
    for code, g in need.groupby("ts_code"):
        g = g.sort_values("trade_date")
        pth[code] = (g["trade_date"].values, g["pct_chg"].astype(float).values)

    pool = df.dropna(subset=["fwd120"]).copy()
    out = []
    for _, c in cards.iterrows():
        setup = str(c.get("setup") or "")
        f0 = float(c["F120"])
        t0 = float(c["T"])
        cand = pool[pool["setup"] == setup] if setup else pool
        m = cand[(cand["stage"] == "ready") & (cand["F120"].between(f0 - 5, f0 + 5))
                 & (cand["T"].between(t0 - 5, t0 + 5))]
        rule = "setup+F5+T5+ready"
        if len(m) < 30:
            m = cand[(cand["F120"].between(f0 - 10, f0 + 10)) & (cand["T"].between(t0 - 10, t0 + 10))]
            rule = "setup+F10+T10"
        if len(m) < 30:
            m = cand
            rule = "setup only"
        if len(m) < 30:
            m = pool
            rule = "ALL(no-setup)"

        ideal = float(c["ideal"])
        stop_ret = float(c["stop"]) / ideal - 1.0
        target_ret = float(c["target"]) / ideal - 1.0
        exits, days, kinds = [], [], []
        for _, r in m.iterrows():
            tp = pth.get(r["ts_code"])
            if tp is None:
                continue
            tds, pcts = tp
            i0 = int(np.searchsorted(tds, r["date"], side="right"))
            seg = pcts[i0:i0 + H2]
            if len(seg) < H2 or np.isnan(seg).any():
                continue
            path = np.cumprod(1 + np.clip(seg / 100.0, -CLIP, CLIP)) - 1
            ev, nd, kind = sim_path(path, stop_ret, target_ret)
            exits.append(ev)
            days.append(nd)
            kinds.append(kind)
        ex = np.array(exits) if exits else np.array([np.nan])
        dy = np.array(days) if days else np.array([np.nan])
        kd = pd.Series(kinds).value_counts() if kinds else pd.Series(dtype=int)
        q = m["fwd120"].quantile([0.10, 0.25, 0.5, 0.75, 0.90])
        n = len(exits)
        out.append({
            "ts_code": c["ts_code"], "name": c.get("name"), "subsector": c.get("subsector"),
            "verdict": c["verdict"], "setup": setup,
            "F120": round(f0, 1), "T": round(t0, 1),
            "cur": round(float(c["cur"]), 2), "ideal": round(ideal, 2),
            "stop_ret": round(stop_ret, 4), "target_ret": round(target_ret, 4),
            "entry_premium": round(float(c["cur"]) / ideal - 1.0, 4),
            "analog_n": n, "rule": rule,
            "raw_p10": round(float(q[0.10]), 4), "raw_p25": round(float(q[0.25]), 4),
            "raw_p50": round(float(q[0.5]), 4), "raw_p75": round(float(q[0.75]), 4),
            "raw_p90": round(float(q[0.90]), 4),
            "raw_win": round(float((m["fwd120"] > 0).mean()), 4),
            "mfe_p50": round(float(m["mfe120"].median()), 4),
            "mae_p50": round(float(m["mae120"].median()), 4),
            "p_target": round(float(kd.get("target", 0)) / n, 4) if n else np.nan,
            "p_stop": round(float(kd.get("stop", 0)) / n, 4) if n else np.nan,
            "p_timeout": round(float(kd.get("timeout", 0)) / n, 4) if n else np.nan,
            "ev": round(float(np.nanmean(ex)), 4),
            "ev_p10": round(float(np.nanquantile(ex, 0.10)), 4),
            "ev_p90": round(float(np.nanquantile(ex, 0.90)), 4),
            "exp_days": round(float(np.nanmean(dy)), 1),
        })
    out_df = pd.DataFrame(out)
    out_df["pri"] = out_df["verdict"].astype(str).str.startswith("PRIMARY").astype(int)
    out_df = out_df.sort_values(["pri", "F120"], ascending=[False, False]).drop(columns=["pri"])
    return out_df


def main():
    spath = os.path.join(OUT, "f120_space_samples.csv")
    if os.path.exists(spath):
        print("[1/4] 读取已有重放样本 ...", flush=True)
        df = pd.read_csv(spath, dtype={"ts_code": str, "date": str, "setup": str,
                                       "stage": str, "p_tag": str, "e_tag": str, "f_tag": str})
        for c in ("setup", "stage", "p_tag", "e_tag", "f_tag"):
            df[c] = df[c].fillna("")
    else:
        print("[1/4] 加载面板 + 历史月度截面重放(复用 f120 生产信号) ...", flush=True)
        df, _ = build_samples()
        df.to_csv(spath, index=False)
    print(f"    samples={len(df)}  cross_sections={df['date'].nunique()}", flush=True)

    print("[2/4] 信号 IC + 分类组均值差 ...", flush=True)
    ic = ic_table(df)
    ic.to_csv(os.path.join(OUT, "f120_space_ic.csv"), index=False)
    gt, base = group_table(df)
    gt.to_csv(os.path.join(OUT, "f120_space_groups.csv"), index=False)

    print("[3/4] 执行卡历史类比 + 规则化路径 EV 模拟 ...", flush=True)
    rfiles = sorted(glob.glob(os.path.join(OUT, "f120_result_????????.csv")))
    cards = pd.read_csv(rfiles[-1])
    print(f"    result file: {os.path.basename(rfiles[-1])}", flush=True)
    cards = cards[cards["verdict"].astype(str).str.startswith(("PRIMARY", "CONDITIONAL"))]
    cards = cards[cards["stage"] == "ready"].reset_index(drop=True)
    cs = card_space(cards, df, df["ts_code"].unique())
    cs.to_csv(os.path.join(OUT, "f120_space_cards.csv"), index=False)

    print("[4/4] 汇总输出\n", flush=True)

    def icv(sig, h, col):
        r = ic[(ic["signal"] == sig) & (ic["horizon"] == h)]
        return float(r[col].iloc[0]) if len(r) else np.nan

    print("## 一、信号预测力（月度截面内 Spearman IC，vs 前瞻收益）\n")
    print("| 信号 | 截面数 | IC@T+60 | ICIR@60 | IC@T+120 | ICIR@120 | 正IC占比(120) |")
    print("|---|---|---|---|---|---|---|")
    for s in SIGS:
        n0 = ic[ic["signal"] == s]["n_cross"]
        n = int(n0.iloc[0]) if len(n0) else 0
        print(f"| {s} | {n} | {icv(s, H1, 'ic_mean'):+.3f} | {icv(s, H1, 'icir'):+.2f} "
              f"| {icv(s, H2, 'ic_mean'):+.3f} | {icv(s, H2, 'icir'):+.2f} "
              f"| {icv(s, H2, 'ic_pos'):.0%} |")

    print("\n## 二、分类信号组表现（fwd120，相对全样本均值 %.1f%%）\n" % (base * 100))
    print("| 特征 | 取值 | n | fwd120均值 | 中位 | 胜率 | 超额 |")
    print("|---|---|---|---|---|---|---|")
    for _, r in gt.iterrows():
        print(f"| {r['feat']} | {r['value']} | {r['n']} | {r['fwd120_mean']:+.1%} "
              f"| {r['fwd120_med']:+.1%} | {r['win']:.0%} | {r['excess']:+.1%} |")

    print("\n## 三、执行卡未来空间（历史类比 + 规则化 stop/target 模拟，入场=ideal）\n")
    print("| 卡片 | verdict | setup | F120 | 类比N/规则 | 裸持有fwd120 P10/P50/P90 | 胜率 "
          "| P到目标 | P止损 | EV | EV P10/P90 | 期望持有日 | 入场溢价 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for _, r in cs.iterrows():
        print(f"| {r['name']} {r['ts_code']} | {str(r['verdict'])[:11]} | {r['setup']} "
              f"| {r['F120']:.1f} | {r['analog_n']}/{r['rule']} "
              f"| {r['raw_p10']:+.0%}/{r['raw_p50']:+.0%}/{r['raw_p90']:+.0%} "
              f"| {r['raw_win']:.0%} | {r['p_target']:.0%} | {r['p_stop']:.0%} "
              f"| {r['ev']:+.1%} | {r['ev_p10']:+.0%}/{r['ev_p90']:+.0%} "
              f"| {r['exp_days']:.0f} | {r['entry_premium']:+.1%} |")

    print("""
> 口径与局限：
> 1. 历史重放复用 f120 生产信号代码（build_series/trend_of/detect_setup/levels_of/score_*），技术面零偏差；
> 2. F/E 基本面字段为当前快照（sli_full_20260901），历史截面存在轻微前视，IC 解读以技术面信号为主；
> 3. 历史样本无 TOP5 的 purity/dominance（rank5 取中性 3.0），F 信号含系统性小幅偏移，方向性结论不受影响；
> 4. 前瞻路径按收盘价、日涨跌 clip ±21%，未建模滑点/一字板不可成交，EV 为信号空间上界；
> 5. EV 模拟假设以 ideal 价入场、卡自带 stop/target 触发即退出（同日双触发保守取止损），未触发持有满 120 日。""")


if __name__ == "__main__":
    main()
