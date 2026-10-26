# -*- coding: utf-8 -*-
"""
EOD-RB 每日信号输出（规范 11）：生成当日候选表，字段齐全，决策 BUY/WATCH/NO TRADE。
分钟数据仅覆盖到 2026-05-22，故「每日示例」用该日生成；实时当日信号需当天分钟数据到位。
"""
import numpy as np

from . import backtest as B
from . import engine as E
from .config import REGIME_CN
import bts.data as bd


def generate_daily_signals(trade_date, cfg, max_rows=None):
    uni = B.build_universe(cfg, trade_date)
    snap = B.snapshot_by_date(trade_date)
    if snap is None or snap.empty:
        return [], "no_data"
    min_amount_qian = cfg["universe"]["min_amount_yi"] * 1e5
    cand = snap[snap["ts_code"].isin(uni.code_set)].copy()
    cand = cand[cand["amount"] >= min_amount_qian]

    regime = bd.market_regime(trade_date)
    ctx = B._build_sector(snap, uni)
    caches = B._Caches(trade_date)
    signals = []

    for _, r in cand.iterrows():
        ts = r["ts_code"]
        df = caches.daily(ts)
        if df is None:
            continue
        sub = df[df["trade_date"] <= str(trade_date)]
        if len(sub) < 40:
            continue
        base = E.evaluate_daily(ts, sub, cfg)
        if not (base["trend_ok"] and base["pullback_ok"]):
            continue
        anchor = caches.anchor(ts, str(trade_date))
        bench = caches.bench(ts, str(trade_date), cfg["tail"]["vol_ratio_lookback"])
        sector = B._sector(ts, r.get("pct_chg"), uni, ctx)
        sig = E.evaluate_full(ts, sub, anchor, bench, regime,
                              uni.name.get(ts, ts), uni.mv.get(ts), sector, cfg)
        if sig["decision"] in ("BUY", "WATCH"):
            signals.append(sig)

    order = {"BUY": 0, "WATCH": 1}

    def _key(s):
        vol = s.get("tail_vol_ratio") or 0.0
        rs = s.get("rel_strength") if s.get("rel_strength") is not None else -999.0
        return (order.get(s["decision"], 2),
                0 if s.get("tag") == "RB_BREAKOUT" else 1,
                -vol, -rs)

    signals.sort(key=_key)
    if max_rows:
        signals = signals[:max_rows]
    return signals, regime


def build_table(signals):
    """生成 Markdown 候选表"""
    header = ("排名｜股票代码｜股票名称｜当前价格｜当日涨幅%｜前期涨幅%｜回踩幅度%｜缩量比率｜"
              "尾盘收益%｜尾盘量比｜分时均价线｜突破状态｜板块相对强度%｜支撑位｜失效位｜信号类型｜决策｜主要风险")
    lines = [header, "---" ]
    for i, s in enumerate(signals, 1):
        tb = s.get("pullback_info", {})
        ti = s.get("tail_info", {})
        support = s.get("support")
        lines.append(
            f"{i}｜{s['ts_code']}｜{s.get('name','')}｜{round(s['close'],2)}｜"
            f"{round(s['pct_chg'] or 0,2)}｜{s.get('prior_wave')}｜{tb.get('depth')}｜{tb.get('shrink')}｜"
            f"{ti.get('tail_return')}｜{ti.get('vol_ratio')}｜{s.get('vwap_state')}｜"
            f"{s.get('breakout_state')}｜{s.get('rel_strength')}｜{support}｜{support}｜"
            f"{s.get('tag_cn') or '无'}｜{s['decision']}｜{s['main_risk']}"
        )
    return "\n".join(lines)


def reasons(signal, max_n=3):
    """不超过三条入选理由"""
    rs = []
    tb = signal.get("pullback_info", {})
    ti = signal.get("tail_info", {})
    if signal.get("trend_ok"):
        rs.append(f"前期强势(20日{signal.get('prior_wave')}%)")
    if signal.get("pullback_ok"):
        rs.append(f"缩量回踩(回踩{tb.get('depth')}%/缩量{tb.get('shrink')})")
    if signal.get("tail_ok"):
        rs.append(f"尾盘再启动({signal.get('tag_cn')}/量比{ti.get('vol_ratio')})")
    return rs[:max_n]