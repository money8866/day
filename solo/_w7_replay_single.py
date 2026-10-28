# -*- coding: utf-8 -*-
"""单只股票 W7 信号 as-of 历史回放（逐日重放）。

用途：把 w7_second_wave_engine 的状态机 + 质量门在「单票、逐日 as-of」口径下复现，
回答「这只票从某月起到今天，W7 每天给出什么状态、哪天给出可操作买点、事后表现如何」。

与生产跑批（engine.main()）的对齐要点：
  1) 每个交易日 D 只用 trade_date<=D 的 K 线；MA/前复权在「截断后的序列」上重算
     ——这与引擎 load_all(end_date=D) → _fill_ma_columns 完全一致（前复权以序列末端为锚，
     截断会改变缩放基准，故必须逐日重算，不能一次算完再切片）。
  2) T0 锚由 find_event_anchor/preshift_start_bar 在截断序列内重算 → 无前视。
  3) 财务走 reader.fina(as_of=D) 的 point-in-time；IGE 走「快照日期<=D 的最近一次」
     （生产 load_ige_adj 会回退到 files[-1]，属前视，回放中改为严格 as-of）。
  4) 市场环境 market_regime(conn, D) 按 D 计算。
  5) 板块强度/景气（sector_strength/sector_growth）需全市场截面，生产才算得；
     本脚本取中性 50。影响范围已核：sector 维度只进入 t120_alpha_score（展示分/定级文案），
     不进入 hvt_v3_score 的 base_score（≥40 硬底线）、不进入 w7_priority 三因子、
     不进入状态判定与质量门 → 不影响可操作买点结论。
  6) 不写 stock_pick_db / 不写 w7_today_action_*.json / 不覆盖 report_daily 报告。
"""
import argparse
import importlib.util
import json
import os
import sys
import time

import numpy as np
import pandas as pd

SOLO = r"d:\mystock\solo"
ENGINE = os.path.join(SOLO, "w7_second_wave_engine.py")
OUT_DIR = os.path.join(SOLO, "_w7_replay_out")


def load_engine():
    spec = importlib.util.spec_from_file_location("w7eng", ENGINE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["w7eng"] = mod
    spec.loader.exec_module(mod)
    return mod


def asof_ige(mod, asof):
    """严格 as-of 版 load_ige_adj：只取快照日期 <= asof 的最近一次；无则不注入 IGE。"""
    d = mod.IGE_OUT_DIR
    if not os.path.isdir(d):
        return {}, ""
    files = sorted(f for f in os.listdir(d) if f.startswith("ige_full_") and f.endswith(".csv"))
    cands = [f for f in files if f[9:17] <= str(asof)]
    if not cands:
        return {}, ""
    chosen = cands[-1]
    snap = chosen[9:17]
    df = pd.read_csv(os.path.join(d, chosen), encoding="utf-8-sig", dtype={"code": str})
    need = [c for c in ("code", "ige_adj", "ige_mix", "sw_l1", "sw_l3") if c in df.columns]
    info = {}
    for rec in df[need].to_dict("records"):
        code = str(rec.get("code") or "").strip()
        if not code:
            continue
        info[code] = {"ige_adj": mod.finite(rec.get("ige_adj"), None),
                      "ige_mix": mod.finite(rec.get("ige_mix"), None),
                      "sw_l1": "" if pd.isna(rec.get("sw_l1")) else str(rec["sw_l1"]),
                      "sw_l3": "" if pd.isna(rec.get("sw_l3")) else str(rec["sw_l3"])}
    return info, snap


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="301550.SZ")
    ap.add_argument("--start", default="20260801")
    ap.add_argument("--end", default="")
    ap.add_argument("--label", default="")
    args = ap.parse_args()

    mod = load_engine()
    reader = mod.CacheReader()
    reader.load_fina()

    code = args.code
    start = args.start
    end = args.end or reader.latest_date()

    # 个股基础信息
    basic = reader.basic.loc[code] if code in reader.basic.index else {}
    name = str(basic.get("name", code))
    industry = str(basic.get("industry", "") or "")

    # 全部交易日（个股自身）
    q = ("SELECT DISTINCT trade_date FROM daily_cache WHERE ts_code=? "
         "AND trade_date>=? AND trade_date<=? ORDER BY trade_date")
    dates = [r[0] for r in reader.conn.execute(q, (code, start, end))]
    if not dates:
        print("[replay] %s 在 %s~%s 无交易日" % (code, start, end))
        return

    # 指数序列（算 RS / 市场环境用）
    idx_df = pd.read_sql_query(
        "SELECT trade_date, close FROM index_daily_cache WHERE ts_code=? AND trade_date<=? ORDER BY trade_date",
        reader.conn, params=(mod.MARKET_INDEX_CODE, end))
    idx_df["close"] = pd.to_numeric(idx_df["close"], errors="coerce")

    rows, sig_rows = [], []
    t0 = time.time()
    for i, d in enumerate(dates, 1):
        # 个股 K 线（as-of：仅 <= d，且在截断序列上重算 MA/前复权）
        df = reader.bars_sql(code, d)
        if len(df) < mod.MIN_BARS:
            rows.append({"date": d, "state": "NO_DATA", "close": mod.finite(df.iloc[-1].close) if len(df) else None})
            continue
        # 锚点特征（锚点日固定，与 D 无关；此处按生产口径在 <=d 序列上取）
        anchors = {}
        for label, (acode, adate) in mod.ANCHORS.items():
            adf = reader.bars_sql(acode, d)
            anchors[label] = mod.anchor_features(adf, adate)
        # 市场曲线（截断到 d）
        sub = idx_df[idx_df.trade_date <= d]
        mkt = mod.MarketCtx(sub.trade_date.astype(str).to_numpy(),
                             sub.close.to_numpy(dtype=float))
        regime = mod.market_regime(reader.conn, d)
        ige_info, ige_snap = asof_ige(mod, d)
        ig = ige_info.get(code)

        result = mod.analyze(code, name, industry, df, anchors, reader=reader, mkt=mkt,
                             sector_strength={}, sector_growth={},
                             ige_adj=ig["ige_adj"] if ig else None)
        if not result:
            rows.append({"date": d, "state": "NO_ANCHOR", "close": mod.finite(df.iloc[-1].close)})
            continue
        result["ige_adj"] = ig["ige_adj"] if ig else None
        result["market_regime"] = regime
        result["ige_snap"] = ige_snap

        # 执行状态 + 质量门（与生产一致：报告可操作榜与落库共用此门）
        exec_st, fail_line, fail_name = mod.w7_exec_status(result)
        result["w7_status"] = exec_st
        result["w7_fail_line"] = fail_line
        result["w7_fail_name"] = fail_name
        ok, why = mod.w7_quality_gate(result)

        last = df.iloc[-1]
        rows.append({
            "date": d,
            "state": result["state"],
            "type": result["type"],
            "close": result["close"],
            "pct_chg": mod.finite(last.pct_chg),
            "volr": round(result["volr"], 3),
            "amount_wan": round(result["amount"] / 10.0, 1),
            "t0_date": result.get("t0_date", ""),
            "buy": result["buy"],
            "exec": exec_st,
            "gate": "PASS" if ok else "BLOCK",
            "gate_why": why,
            "base_score": round(result["base_score"], 1),
            "score": round(result["score"], 1),
            "t120": round(result["t120"], 1),
            "ige_adj": result.get("ige_adj"),
            "regime": regime,
            "level": result["level"],
            "extension": round(result["extension"], 3),
        })

        if result["state"] in mod.ACTION_BUY_STATES:
            sig_rows.append({
                "date": d, "state": result["state"], "type": result["type"],
                "close": result["close"], "volr": round(result["volr"], 3),
                "amount_wan": round(result["amount"] / 10.0, 1),
                "t0_date": result.get("t0_date", ""),
                "t0_open": result.get("t0_open"), "t0_close": result.get("t0_close"),
                "pull_low": result.get("pull_low"),
                "pull_low_date": result.get("pull_low_date", ""),
                "pull_bars": result.get("pull_bars"),
                "pull_vol_ratio": round(result.get("pull_vol_ratio", 0.0), 3),
                "stall": result.get("stall", ""),
                "break_volr": round(result.get("break_volr", 0.0), 3),
                "t0_ext": round(result.get("t0_ext", 0.0), 3),
                "fail_line": fail_line, "fail_name": fail_name,
                "buy": result["buy"], "exec": exec_st,
                "gate": "PASS" if ok else "BLOCK", "gate_why": why,
                "base_score": round(result["base_score"], 1), "score": round(result["score"], 1),
                "t120": round(result["t120"], 1), "ige_adj": result.get("ige_adj"),
                "regime": regime,
                "reason": result["reason"], "explanation": result["explanation"],
            })
        if i % 10 == 0:
            print("[replay] %s %d/%d 耗时=%.1fs" % (code, i, len(dates), time.time() - t0), flush=True)

    df_rows = pd.DataFrame(rows)
    df_sig = pd.DataFrame(sig_rows)
    os.makedirs(OUT_DIR, exist_ok=True)
    tag = args.label or ("%s_%s" % (code, start))
    p_daily = os.path.join(OUT_DIR, "daily_%s.csv" % tag)
    p_sig = os.path.join(OUT_DIR, "signals_%s.csv" % tag)
    df_rows.to_csv(p_daily, index=False, encoding="utf-8-sig")
    df_sig.to_csv(p_sig, index=False, encoding="utf-8-sig")

    # ---- 事后表现（仅对可操作信号：类型A/类型B 且过质量门）----
    perf = []
    full = reader.bars_sql(code, end)
    dates_all = list(full.trade_date.astype(str))
    close_map = dict(zip(dates_all, full.close.astype(float)))
    for s in sig_rows:
        if s["gate"] != "PASS":
            continue
        d0 = s["date"]
        if d0 not in dates_all:
            continue
        i0 = dates_all.index(d0)
        entry = close_map[d0]
        rec = {"date": d0, "state": s["state"], "close": entry,
               "fail_line": s["fail_line"], "fail_name": s["fail_name"]}
        for h in (1, 3, 5, 10):
            j = i0 + h
            rec["ret%d" % h] = round((close_map[dates_all[j]] / entry - 1) * 100, 2) if j < len(dates_all) else None
        # 指数同期
        sub = idx_df.set_index(idx_df.trade_date.astype(str))["close"]
        if d0 in sub.index:
            i0i = list(sub.index).index(d0)
            for h in (1, 3, 5, 10):
                j = i0i + h
                rec["mkt%d" % h] = round((float(sub.iloc[j]) / float(sub.iloc[i0i]) - 1) * 100, 2) if j < len(sub) else None
        # 失效位保护：T+10 内是否触及
        hit = None
        for j in range(i0 + 1, min(i0 + 11, len(dates_all))):
            if float(full.low.iloc[j]) <= float(s["fail_line"] or 0):
                hit = dates_all[j]
                break
        rec["stop_hit_date"] = hit
        perf.append(rec)
    df_perf = pd.DataFrame(perf)
    p_perf = os.path.join(OUT_DIR, "perf_%s.csv" % tag)
    if not df_perf.empty:
        df_perf.to_csv(p_perf, index=False, encoding="utf-8-sig")

    # ---- 控制台摘要 ----
    print("\n=== %s %s %s~%s 共 %d 个交易日 ===" % (code, name, start, end, len(dates)))
    if not df_rows.empty:
        print("\n-- 每日状态分布 --")
        print(df_rows["state"].value_counts().to_string())
    print("\n-- 类型A/B 信号日 --")
    if df_sig.empty:
        print("（无）")
    else:
        cols = ["date", "state", "close", "t0_date", "t0_open", "t0_close", "pull_low",
                "stall", "volr", "buy", "exec", "gate", "gate_why"]
        print(df_sig[[c for c in cols if c in df_sig.columns]].to_string(index=False))
    print("\n-- 过门信号的事后表现 --")
    if df_perf.empty:
        print("（无过门信号）")
    else:
        print(df_perf.to_string(index=False))
    print("\n输出：\n  %s\n  %s\n  %s" % (p_daily, p_sig, p_perf))
    reader.close()


if __name__ == "__main__":
    main()
