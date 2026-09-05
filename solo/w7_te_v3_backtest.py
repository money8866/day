# -*- coding: utf-8 -*-
"""
W7 Trade Execution V3.1 条件历史回测（毛收益，无未来函数）
================================================================
目标：回测 trade_execution_engine V3.1 的"次日可执行条件"：
  - Trigger(=W7 pressure) / Buy Zone(Trigger±1.5%) / 双止损(预警线0.97×Trigger / 结构位0.93×Trigger,4ATR)
  - V3.1 门控：G1 EXTREME_CHURN 剔除；G2 P1 无条件 + P2&Exec≥85 + 其余不给 BUY；
    G3 volr≤2.2；G4 市场 regime(全市场等权曲线)≥1；均与引擎 v31_decision 同源
  - 分级：PRIMARY BUY(Alpha≥60 & DRisk≤20 & 现价在买区) / CONDITIONAL BUY / WAIT / WATCH / AVOID
  - Gap Filter：高开>5% → NO CHASE（gap_no_chase 标记，主统计剔除）
执行撮合口径：决策日收盘出信号 → T+1 开盘成交（ActualEntry=open[j+1]，与 hvt_bull te_backtest 一致）；
另输出决策日收盘基准 r{h} 与成交基准 er{h}、MFE/MAE、双止损触发率。
候选池重建：全市场事件扫描（V5 口径：120日分位量能+换手双≥P99，事件后60日内逐日重放
state_and_features + V5维度 → 与 w7 引擎 analyze 同口径；sector=50，fina 用 point-in-time）。
注：实盘 W7 日更另有 SLI 龙头池过滤，本回测为全市场研究口径，结论见报告内说明。
V3.0 基线对照文件：report_daily/te3_backtest_20240101_20260828.md（旧文件名已被本版让出）。

用法：
    python w7_te_v3_backtest.py [--limit N] [--workers 8] [--end 20260828]
"""
import argparse
import os
import sys
import time
from multiprocessing import Pool

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from w7_second_wave_engine import (  # noqa: E402
    CacheReader, state_and_features, anchor_features, ANCHORS,
    similarity, alpha_hvt, alpha_trend, alpha_fina, alpha_rs, alpha_upside,
    t120_alpha_score, MarketCtx, finite, MAIN_EVENT_PCT,
    lifecycle, lifecycle_score, hvt_future_space, hvt_acceleration,
    hvt_platform, hvt_distribution_risk, hvt_v3_score, hvt_type, WATCH_MIN_SCORE,
    entry_score_v2, STATES,
)
from trade_execution_engine import (  # noqa: E402
    classify, sub_scores, retest_score, position_for, clip,
    v31_decision, market_regime_series,
    BUY_BAND_LO, BUY_BAND_HI, EXT_HI_COND, EXT_NOCHASE, WEIGHTS,
)

# ===== 回测区间（信号日 <= SIGNAL_END；数据加载至 LOAD_END 以保留 T+20 前瞻） =====
EVENT_MIN_DATE = "20240101"       # 事件最早日
SIGNAL_END = "20260828"           # 信号最晚日（决策日）
LOAD_END = "20260904"             # K 线加载截止（未来收益用，不参与决策特征）
TRACK_DAYS = 60                   # 事件后最大跟踪日（与引擎 MAX_EVENT_AGE 一致）
REPEAT_GAP = 3                    # 同一事件两次采样最小间隔（交易日）；Action 变化时不受限
MIN_HIST = 320                    # 最少历史 K 线
HORIZONS = (1, 3, 5, 10, 20)      # 前瞻日（决策日收盘 / T+1 开盘成交两套基准）
MFE_H = (5, 10, 20)

_MKT_DATES = None
_MKT_VALS = None
_ANCHORS = {}


def _init_worker(mdates, mvals, anchors):
    global _MKT_DATES, _MKT_VALS, _ANCHORS
    _MKT_DATES, _MKT_VALS, _ANCHORS = mdates, mvals, anchors


def find_events(df):
    """V5 事件口径：事件日前120日分位，量能+换手 双≥P99（numpy 实现，同引擎 extreme_event）"""
    dates = df["trade_date"].astype(str).to_numpy()
    tr = pd.to_numeric(df.turnover_rate_f, errors="coerce").fillna(0).to_numpy(dtype=float)
    vol = pd.to_numeric(df.vol, errors="coerce").fillna(0).to_numpy(dtype=float)
    n = len(df)
    if n < MIN_HIST:
        return []
    out = []
    for i in range(121, n - 2):
        if dates[i] < EVENT_MIN_DATE:
            continue
        start = i - 120
        p_t = float(np.mean(tr[start:i] <= tr[i]) * 100.0)
        p_v = float(np.mean(vol[start:i] <= vol[i]) * 100.0)
        if min(p_t, p_v) >= MAIN_EVENT_PCT:
            out.append((int(i), float(max(p_t, p_v))))
    return out


def v5_status(score, entry, trend_confirmed, tp):
    if tp == "DISTRIBUTION":
        return "WATCH"
    if score >= 85 and entry >= 80:
        return "PRIMARY_BUY"
    if score >= 80:
        return "T120_ROCKET"
    if score >= 70 and (trend_confirmed or tp in ("MID", "EXT")):
        return "CONFIRMED"
    return "WATCH"


def tech(df):
    """日线结构特征（镜像 trade_execution_engine.tech_features，输入截至决策日）"""
    if df is None or len(df) < 30:
        return None
    cur, prev = df.iloc[-1], df.iloc[-2]
    close, high, low = float(cur.close), float(cur.high), float(cur.low)
    opn = float(cur.open)
    prev_close = df.close.shift(1)
    tr = pd.concat([(df.high - df.low), (df.high - prev_close).abs(),
                    (df.low - prev_close).abs()], axis=1).max(axis=1)
    atr = float(tr.tail(14).mean())
    ma60 = float(df.ma_bfq_60.iloc[-1]) if pd.notna(df.ma_bfq_60.iloc[-1]) else float(df.close.tail(60).mean())
    ma60_slope = (ma60 / float(df.ma_bfq_60.iloc[-21]) - 1) if (len(df) >= 81 and pd.notna(df.ma_bfq_60.iloc[-21])) else 0.0
    high20 = float(df.high.tail(21).iloc[:-1].max()) if len(df) >= 21 else high
    high60 = float(df.high.tail(61).iloc[:-1].max()) if len(df) >= 61 else high
    low10 = float(df.low.tail(10).min())
    low20 = float(df.low.tail(20).min())
    ma20 = float(df.ma_bfq_20.iloc[-1]) if pd.notna(df.ma_bfq_20.iloc[-1]) else float(df.close.tail(20).mean())
    ma10 = float(df.ma_bfq_10.iloc[-1]) if ("ma_bfq_10" in df.columns and pd.notna(df.ma_bfq_10.iloc[-1])) \
        else float(df.close.tail(10).mean())
    upper_shadow = high - max(opn, close)
    is_yang = close >= opn
    long_shadow = upper_shadow > 0.5 * atr if atr > 0 else False
    ret20 = close / float(df.close.iloc[-21]) - 1 if len(df) >= 21 else 0.0
    return dict(atr=atr, ma60=ma60, ma60_slope=ma60_slope, ma20=ma20, ma10=ma10,
                high20=high20, high60=high60, low10=low10, low20=low20,
                is_yang=is_yang, long_shadow=long_shadow, ret20=ret20,
                close=close, high=high, low=low)


def exec_action(c, t, regime=1):
    """V3.1 决策（镜像引擎 main 的 v31_decision，单一事实源）。
    返回 dict：entry_type/eq/action/reason/gate/exec/stop/stop_struct/buy_lo/buy_hi。"""
    return v31_decision(c, t, regime)


def scan_batch(codes_names):
    reader = CacheReader()
    reader.load_all(LOAD_END, codes=[c for c, _ in codes_names], min_date="20230101",
                    chunk=500, verbose=False)
    reader.load_fina()
    mkt = MarketCtx(_MKT_DATES, _MKT_VALS)
    # G4 市场 regime（决策日取值）：只依赖当日及以前的市场等权曲线，无未来函数
    _r_arr = market_regime_series(_MKT_DATES, _MKT_VALS)
    regime_map = dict(zip(_MKT_DATES, _r_arr.tolist()))
    samples = []
    logf = open(f"te3_w{os.getpid()}.log", "a", encoding="utf-8", buffering=1)
    for idx, (code, name) in enumerate(codes_names):
        t0 = time.time()
        df = reader.bars(code, LOAD_END)
        if len(df) < MIN_HIST:
            continue
        df = df.reset_index(drop=True)
        events = find_events(df)
        if not events:
            continue
        n = len(df)
        closes = df.close.to_numpy(dtype=float)
        highs = df.high.to_numpy(dtype=float)
        lows = df.low.to_numpy(dtype=float)
        opn = df.open.to_numpy(dtype=float)
        dates = df.trade_date.astype(str).to_numpy()

        for ei, (i, ep) in enumerate(events):
            end_i = events[ei + 1][0] if ei + 1 < len(events) else min(i + TRACK_DAYS, n - 2)
            end_i = min(end_i, i + TRACK_DAYS, n - 2)
            last_sig, last_action = -10 ** 9, ""
            event_low = finite(df.low.iloc[i])
            for j in range(i + 1, end_i + 1):
                if dates[j] > SIGNAL_END:
                    break
                res = state_and_features(df, i, ep, end=j)
                if not res:
                    continue
                base, state, pp, pp_ok, reexp, breakout, major_risk, dd, pressure = res
                if pressure <= 0 or finite(df.iloc[j].close, 0) <= 0:
                    continue
                # 先粗判"是否可能被采样"，明显无变化且未到间隔 → 跳过重维度计算
                if (state == last_action and j - last_sig < REPEAT_GAP
                        and not (state in ("BREAKOUT_CONFIRM", "SECOND_WAVE", "RE_EXPANSION"))):
                    continue
                d = dates[j]
                lc = lifecycle(df, j)
                sim_a = similarity(base, _ANCHORS.get("中际旭创"))
                sim_b = similarity(base, _ANCHORS.get("华正新材"))
                hvt = (sim_a + sim_b) / 2.0
                dims = {
                    "hvt": alpha_hvt(base, hvt, dd),
                    "trend": alpha_trend(df, j),
                    "fina": alpha_fina(*reader.fina(code, as_of=d)),
                    "rs": alpha_rs(df, j, mkt),
                    "upside": alpha_upside(df, j),
                    "sector": 50.0,
                }
                fs = hvt_future_space(df, j, lc)
                acc = hvt_acceleration(df, j, mkt)
                plat = hvt_platform(df, i, j)
                dist_risk = hvt_distribution_risk(df, j, base, lc)
                score, base_score, absorption, penalty = hvt_v3_score(
                    base, lc, dims["hvt"], fs, acc, dims["rs"], dims["fina"], plat, dist_risk)
                tp = hvt_type(state, lc, dist_risk)
                trend_confirmed = breakout or reexp or dims["trend"] >= 70
                entry = entry_score_v2(df, j, pp, pp_ok, reexp, breakout, event_low, mkt)[0] if score >= 85 else 0.0
                status = v5_status(score, entry, trend_confirmed, tp)
                if status == "WATCH" and base_score < WATCH_MIN_SCORE:
                    continue  # 引擎 analyze 同样丢弃低分 WATCH，不进入候选池
                t = tech(df.iloc[:j + 1].reset_index(drop=True))
                if t is None:
                    continue
                vol20 = float(np.mean(df.vol.iloc[max(0, j - 19):j])) if j >= 20 \
                    else float(np.mean(df.vol.iloc[:max(1, j)]))
                volr = finite(df.iloc[j].vol, 0.0) / vol20 if vol20 > 0 else 0.0
                c = dict(code=code, name=name, score=score, type=tp, state=state,
                         close=finite(closes[j]), pressure=pressure, ma20=finite(df.iloc[j].ma_bfq_20),
                         volr=volr, absorption=absorption, life=lifecycle_score(lc),
                         space=fs, accel=acc, rs=dims["rs"], fina=dims["fina"],
                         drisk=dist_risk, section="bt")
                regime = regime_map.get(d, 1)
                dec = exec_action(c, t, regime)
                action, entry_type, eq = dec["action"], dec["entry_type"], dec["eq"]
                exec_score, gate = dec["exec"], dec["gate"]
                stop, stop_struct = dec["stop"], dec["stop_struct"]
                buy_lo, buy_hi = dec["buy_lo"], dec["buy_hi"]
                # 采样去重：同 Action 连续且未达间隔 → 跳过（防止自相关重复计数）
                if action == last_action and j - last_sig < REPEAT_GAP:
                    continue
                last_sig, last_action = j, action
                rec = {
                    "code": code, "name": name, "event_date": str(df.iloc[i].trade_date),
                    "signal_date": d, "state": state, "type": tp, "status": status,
                    "score": round(score, 1), "drisk": dist_risk, "life_level": lc["level"],
                    "close": round(float(closes[j]), 2), "trigger": round(float(pressure), 2),
                    "buy_lo": buy_lo, "buy_hi": buy_hi, "stop": round(stop, 2),
                    "stop_struct": round(stop_struct, 2),
                    "volr": round(volr, 2), "entry_type": entry_type, "eq": round(eq, 0),
                    "action": action, "exec": round(exec_score, 1), "regime": int(regime),
                    "gate": gate,
                }
                # 前瞻收益：决策日收盘基准 r{h} / T+1 开盘成交基准 er{h}
                if j + 1 < n and opn[j + 1] > 0:
                    entry_px = float(opn[j + 1])
                    rec["entry_px"] = round(entry_px, 2)
                    rec["gap_pct"] = round((entry_px / closes[j] - 1.0) * 100.0, 2)
                    rec["gap_ok"] = entry_px / closes[j] - 1.0 <= 0.05
                else:
                    rec["entry_px"] = None
                    rec["gap_pct"] = None
                    rec["gap_ok"] = False
                for h in HORIZONS:
                    if j + h < n and closes[j] > 0:
                        rec[f"r{h}"] = round((closes[j + h] / closes[j] - 1.0) * 100.0, 2)
                    if rec.get("entry_px") and j + h < n:
                        rec[f"er{h}"] = round((closes[j + h] / rec["entry_px"] - 1.0) * 100.0, 2)
                for h in MFE_H:
                    if rec.get("entry_px") and j + h < n and j + 1 < n:
                        hi = highs[j + 1:j + h + 1]
                        lo = lows[j + 1:j + h + 1]
                        if len(hi) and np.isfinite(hi).all() and np.isfinite(lo).all():
                            rec[f"mfe{h}"] = round(float(np.max(hi) / rec["entry_px"] - 1.0) * 100.0, 2)
                            rec[f"mae{h}"] = round(float(np.min(lo) / rec["entry_px"] - 1.0) * 100.0, 2)
                # 双止损触发（决策日之后 20 日内最低价 <= 止损线）
                win = lows[j + 1:min(n, j + 21)]
                if len(win):
                    hit = float(np.min(win)) <= stop
                    rec["stop_hit20"] = bool(hit)                       # 预警线（V3.0 口径，对照）
                    rec["days_to_stop"] = int(np.argmax(win <= stop) + 1) if hit else None
                    hit_s = float(np.min(win)) <= stop_struct           # V3.1 结构失效位
                    rec["stop_s_hit20"] = bool(hit_s)
                    rec["days_to_stop_s"] = int(np.argmax(win <= stop_struct) + 1) if hit_s else None
                samples.append(rec)
        logf.write(f"{code} n={n} ev={len(events)} t={time.time() - t0:.1f}s\n")
    logf.close()
    reader.close()
    return samples


# ===== 统计 =====
def pool_stat(d, col, label, src="er"):
    """胜率/均值/中位/盈亏比/右尾（col 为已存百分数的收益列）"""
    x = pd.to_numeric(d[col], errors="coerce").dropna() if col in d else pd.Series(dtype=float)
    if x.empty:
        return f"{label:<14} n={0}"
    wins = x[x > 0]
    losses = x[x < 0]
    pf = wins.mean() / abs(losses.mean()) if len(wins) and len(losses) else float("nan")
    return (f"{label:<14} n={len(x):>4} 胜率={100 * (x > 0).mean():5.1f}% 均值={x.mean():+6.2f}% "
            f"中位={x.median():+6.2f}% 盈亏比={pf:5.2f} 右尾≥10%={100 * (x >= 10.0).mean():4.1f}% "
            f"最差={x.min():+7.2f}%")


def _year_pool(d):
    """d: 含 year/signal_date 与 er10/er20 的表；返回 {year: {tag: 摘要}} 文本供报告。"""
    rows = []
    if d.empty:
        return rows
    d = d.assign(year=d.signal_date.astype(str).str[:4])
    for y, g in d.groupby("year"):
        for col, tag in (("er10", "er10"), ("er20", "er20")):
            x = pd.to_numeric(g[col], errors="coerce").dropna()
            if len(x):
                rows.append(f"  {y} {tag}: n={len(x):>3} 均值={x.mean():+6.2f}% 胜率={100 * (x > 0).mean():5.1f}%")
    return rows


def dump_stats(S, out_lines):
    out_lines.append("═" * 78)
    out_lines.append(f"信号样本总数={len(S)}（全市场 V5 口径重建，毛收益，不计成本；V3.1 门控 action）")
    out_lines.append("═" * 78)
    if S.empty:
        return
    # 0) V3.0 基线（旧全量 CSV，仅作对照读取）
    old_csv = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_daily",
                           f"te3_events_{EVENT_MIN_DATE}_{SIGNAL_END}.csv")
    old_ex = None
    if os.path.exists(old_csv):
        old = pd.read_csv(old_csv, dtype={"signal_date": str})
        old_ex = old[(old.action.isin(["PRIMARY BUY", "CONDITIONAL BUY"])) & old.entry_px.notna() & old.gap_ok].copy()
    # 1) Action 分布
    out_lines.append("\n[A] Action 分级分布（V3.1 决策：原 BUY 被门控降级后计入 WAIT/WATCH）")
    out_lines.append(f"  {dict(S.action.value_counts())}")
    if "gate" in S and S.gate.notna().any():
        gv = S[S.gate.notna() & (S.gate.astype(str).str.strip().str.len() > 0)]
        out_lines.append(f"  门控降级样本={len(gv)}（{100 * len(gv) / len(S):.1f}%）：")
        out_lines.append(f"  {dict(gv.gate.astype(str).str[:2].value_counts())}")
    # 2) Action → 决策日收盘基准 r10 / r20
    out_lines.append("\n[B] Action 分层表现（决策日收盘基准 r10/r20，全样本含 Gap 不可成交）")
    for act in ["PRIMARY BUY", "CONDITIONAL BUY", "WAIT", "WATCH", "AVOID"]:
        g = S[S.action == act]
        if not g.empty:
            out_lines.append("  " + pool_stat(g, "r10", f"{act}"))
            out_lines.append("  " + pool_stat(g, "r20", " " * len(act) + " r20"))
    # 3) 成交基准（T+1 开盘 er，剔除高开>5% NO CHASE 与无开盘样本）
    out_lines.append("\n[C] BUY 池成交基准 er10/er20（V3.1 门控后 BUY；ActualEntry=T+1开盘，剔除 gap>5%）")
    buy = S[S.action.isin(["PRIMARY BUY", "CONDITIONAL BUY"])]
    ex = buy[(buy.entry_px.notna()) & buy.gap_ok]
    for grp, lab in ((ex[ex.action == "PRIMARY BUY"], "PRIMARY(可执行)"),
                     (ex[ex.action == "CONDITIONAL BUY"], "COND(可执行)"),
                     (ex, "BUY 合计(可执行)")):
        if not grp.empty:
            for col, tag in (("er5", "er5"), ("er10", "er10"), ("er20", "er20")):
                out_lines.append("  " + pool_stat(grp, col, f"{lab} {tag}"))
    # 4) 双止损纪律：预警线(V3.0口径 0.97×Trigger) vs 结构位(V3.1 0.93×Trigger/4ATR)
    out_lines.append("\n[D] 双止损 20日内触发频率（成交池；预警线=0.97×Trigger 对照，结构位=V3.1 退出基准）")
    for grp, lab in ((ex[ex.action == "PRIMARY BUY"], "PRIMARY"),
                     (ex[ex.action == "CONDITIONAL BUY"], "COND"),
                     (ex, "BUY 合计")):
        if not grp.empty:
            hit = grp.stop_hit20.fillna(False).mean() * 100 if "stop_hit20" in grp else float("nan")
            hit_s = grp.stop_s_hit20.fillna(False).mean() * 100 if "stop_s_hit20" in grp else float("nan")
            out_lines.append(f"  {lab:<10} 预警线触发={hit:5.1f}%  结构位触发={hit_s:5.1f}%   n={len(grp)}")
    # 5) Execution Score 分层
    out_lines.append("\n[E] Execution Score 分层（可执行成交池 er20 均值，验证评分单调性）")
    e = ex.copy()
    if not e.empty and "exec" in e:
        for lo, hi in ((85, 101), (75, 85), (65, 75), (50, 65)):
            g = e[(e.exec >= lo) & (e.exec < hi)]
            if not g.empty:
                out_lines.append("  " + pool_stat(g, "er20", f"Exec∈[{lo},{hi})"))
    # 6) 状态分层（成交池）
    out_lines.append("\n[F] 状态分层（V3.1 可执行成交池 er20；与 V3.0 差异=门控后的幸存结构）")
    for st in ["SECOND_WAVE", "DRYUP", "BREAKOUT_CONFIRM", "RE_EXPANSION", "ABSORPTION"]:
        g = ex[ex.state == st]
        if not g.empty:
            out_lines.append("  " + pool_stat(g, "er20", st))
    # 7) 市场 regime 分层（成交池）
    out_lines.append("\n[G] 市场 regime 分层（V3.1 可执行成交池 er20；regime0 已被 G4 排除，0 档仅剩历史对照）")
    if "regime" in ex:
        for rg in sorted(ex.regime.dropna().unique()):
            g = ex[ex.regime == rg]
            if not g.empty:
                out_lines.append("  " + pool_stat(g, "er20", f"regime={int(rg)}"))
    # 8) 年度稳定性 + V3.0 对照
    out_lines.append("\n[H] 年度稳定性（可执行成交池 er10/er20 均值 | 胜率）")
    if not ex.empty:
        out_lines.extend(_year_pool(ex))
    if old_ex is not None and len(old_ex):
        out_lines.append("  -- V3.0 全量基线对照（te3_events CSV，BUY 可执行池，未加 V3.1 门控）--")
        out_lines.extend(_year_pool(old_ex))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", default="")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    reader = CacheReader()
    universe = reader.universe(LOAD_END)
    stock_list = []
    for r in universe.to_dict("records"):
        name = str(r.get("name") or r["ts_code"])
        if "ST" in name.upper() or "退" in name:
            continue
        basic = reader.basic.loc[r["ts_code"]] if r["ts_code"] in reader.basic.index else {}
        ld = str(basic.get("list_date", "")) if hasattr(basic, "get") else ""
        if ld and ld.isdigit() and int(ld) > int(LOAD_END) - 365:
            continue
        stock_list.append((str(r["ts_code"]), name))
    if args.limit:
        stock_list = stock_list[:args.limit]
    print(f"[te3] 股池={len(stock_list)} workers={args.workers} 信号截止={SIGNAL_END} "
          f"数据加载={LOAD_END} 事件口径={EVENT_MIN_DATE}起/120日分位双≥P99", flush=True)
    mdates, mvals = reader.market_curve(LOAD_END)
    anchors = {}
    for label, (code, adate) in ANCHORS.items():
        anchors[label] = anchor_features(reader.bars_sql(code, LOAD_END), adate)
    t0 = time.time()
    batches = [stock_list[i::args.workers] for i in range(args.workers)]
    batches = [b for b in batches if b]
    with Pool(args.workers, initializer=_init_worker, initargs=(mdates, mvals, anchors)) as pool:
        results = pool.map_async(scan_batch, batches)
        # 主进程也等结果；简单起见直接 get
        res_batches = results.get()
    reader.close()
    samples = [s for b in res_batches for s in b]
    print(f"[te3] 扫描完成 样本={len(samples)} 耗时={time.time() - t0:.0f}s", flush=True)
    S = pd.DataFrame(samples)

    out = args.out or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "report_daily", f"te3_v31_backtest_{EVENT_MIN_DATE}_{SIGNAL_END}.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    L = [f"# Trade Execution V3.1 条件回测（{EVENT_MIN_DATE}~{SIGNAL_END}）\n",
         "口径：全市场 V5 事件扫描重建候选（120日分位量能+换手双≥P99，事件后60日内逐日重放）；"
         "sector=50、fina 用 point-in-time；毛收益不计成本；无未来函数（决策特征截至决策日）。",
         "门控：G1 EXTREME_CHURN 剔除 / G2 P1(SECOND_WAVE/DRYUP)无条件 + P2 需 Exec≥85 + 其余不给 BUY / "
         "G3 volr≤2.2 / G4 市场 regime≥1；止损=预警线(0.97×Trigger) + 结构位(0.93×Trigger,4ATR)。",
         "撮合：决策日收盘出信号 → ActualEntry=T+1开盘；高开>5% 标记 NO CHASE 并从可执行池剔除。",
         "注意：实盘 W7 另有 SLI 龙头池过滤与每日榜单截断，本回测样本为该口径的近似超集。"]
    dump_stats(S, L)
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_daily",
                            f"te3_v31_events_{EVENT_MIN_DATE}_{SIGNAL_END}.csv")
    if not S.empty:
        S.to_csv(csv_path, index=False, encoding="utf-8-sig")
        print(f"[te3] 明细已保存 {csv_path}")
    print("\n".join(L))
    print(f"\n[te3] 总耗时={time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
