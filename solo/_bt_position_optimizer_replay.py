# -*- coding: utf-8 -*-
"""P1/P2 仓位优化全年 A/B 回放与验收

用法：
  python _bt_position_optimizer_replay.py 20260101 20261103

一次运行同时产出 enabled=false（10.7.1 基线）与 enabled=true（10.7.2）两份明细，
并计算诊断时给出的四项验收指标：
  · 单日目标变化 ≥20pp 天数：49 → <10
  · 3 日方向往返次数：14 → <5
  · hard_cap == target 天数：200/200 → 不再全部重合
  · ≥60% 仓位天数：47 → 维持（不过度收紧）
"""
import os
import sys
import json
import sqlite3
import datetime
from collections import Counter

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import pandas as pd

import market_regime_v10 as v10
import market_regime_v101 as v101
import market_regime_v107 as v107
import market_micro as micro


def get_dates(start, end):
    db = os.path.join(os.path.dirname(BASE_DIR), 'cache_daily', 'stock_data.db')
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return [r[0] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM daily_cache "
            "WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",
            (start, end)).fetchall()]
    finally:
        conn.close()


def hist_pack(dates):
    pack = {"breadth": {}, "amount": {}, "dt": {}}
    for d in dates:
        r = micro.get_row(d)
        if not r:
            continue
        if r['up_ratio'] is not None:
            pack["breadth"][d] = r['up_ratio'] * 100.0
        if r['amount_yi'] is not None:
            pack["amount"][d] = r['amount_yi']
        if r['dt_count'] is not None:
            pack["dt"][d] = float(r['dt_count'])
    return pack


def run(dates, pack, enabled, tag, overrides=None):
    cfg = v107.load_config()
    cfg['position_optimizer']['enabled'] = bool(enabled)
    for sec, kv in (overrides or {}).items():
        cfg['position_optimizer'].setdefault(sec, {}).update(kv)
    cfg['structural_alpha']['max_analyze_candidates'] = 3

    _orig_collect = v10.collect_market_snapshot

    def _collect_fast(trade_date, c, prev_snapshot=None, hist_pack=None):
        return _orig_collect(trade_date, c, prev_snapshot, hist_pack or pack)

    v10.collect_market_snapshot = _collect_fast
    chain = {"state": None, "consecutive_days": 0, "pending_target": "",
             "pending_days": 0, "vote_history": []}
    opt_hist = {}
    rows = []
    t0 = datetime.datetime.now()
    try:
        for i, d in enumerate(dates, 1):
            def _load_prev(td, _c=chain):
                return dict(_c) if _c.get("state") else None
            orig_load = v10.load_prev_state
            v10.load_prev_state = _load_prev
            try:
                r = v107.analyze_trade_date_v107(
                    d, cfg, None, persist=False, optimizer_history=opt_hist)
            except Exception as e:
                print(f"  [{tag}] {d} 运行异常: {e}", flush=True)
                continue
            finally:
                v10.load_prev_state = orig_load

            dec = r.base.v10_decision
            chain = {
                "state": r.base.state,
                "consecutive_days": r.base.state_days,
                "pending_target": getattr(dec, "pending_target", "") or "",
                "pending_days": getattr(dec, "pending_days", 0) or 0,
                "vote_history": list(getattr(dec, "vote_history", []) or []),
            }
            o = r.opt.as_dict() if r.opt is not None else {}
            sm = o.get("smoother") or {}
            vol = o.get("volatility") or {}
            cg = o.get("confirm_gates") or {}
            rows.append({
                "trade_date": d,
                "base_state": r.gate.base_state,
                "eff_state": r.state,
                "easing": r.easing.verdict,
                "rebound": r.rebound.verdict,
                "raw_target": o.get("target_before", r.plan.target_position),
                "target": r.plan.target_position,
                "hard_cap": r.plan.hard_cap,
                "suggested_low": r.plan.suggested_low,
                "active_add": bool(r.plan.allow_active_add),
                "smoother_action": sm.get("action"),
                "vol_status": vol.get("status"),
                "vol_factor": vol.get("factor"),
                "extra_cap": cg.get("extra_cap"),
                "deny_active": cg.get("deny_active_add"),
            })
            if i % 40 == 0:
                print(f"  [{tag}] {i}/{len(dates)} … "
                      f"{(datetime.datetime.now()-t0).seconds}s", flush=True)
    finally:
        v10.collect_market_snapshot = _orig_collect

    path = os.path.join(BASE_DIR, f"_bt_opt_replay_{tag}.csv")
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    print(f"  [{tag}] 完成 {len(rows)} 天 / "
          f"{(datetime.datetime.now()-t0).seconds}s → {path}", flush=True)
    return pd.DataFrame(rows)


def metrics(df):
    t = df['target'].astype(float)
    diff = t.diff().abs().fillna(0)
    # 3 日方向往返：连续两段变化方向相反且两段都非零
    d = t.diff().fillna(0)
    round_trip = int(((d.shift(1) > 0) & (d < 0)).sum()
                     + ((d.shift(1) < 0) & (d > 0)).sum())
    return {
        "days": len(df),
        "mean_target": round(float(t.mean()), 2),
        "ge20pp_days": int((diff >= 20).sum()),
        "max_jump_pp": round(float(diff.max()), 1),
        "three_day_roundtrip": round_trip,
        "cap_eq_target_days": int((df['target'] == df['hard_cap']).sum()),
        "ge60_days": int((t >= 60).sum()),
        "mean_hard_cap": round(float(df['hard_cap'].mean()), 2),
        "active_add_days": int(df['active_add'].sum()),
        "smoother": dict(Counter(df['smoother_action'].dropna())),
        "vol_status": dict(Counter(df['vol_status'].dropna())),
        "extra_cap_days": int(df['extra_cap'].notna().sum()),
        "deny_active_days": int(df['deny_active'].fillna(False).sum()),
    }


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else '20260101'
    end = sys.argv[2] if len(sys.argv) > 2 else '20261103'
    dates = get_dates(start, end)
    print(f"回放区间 {dates[0]} ~ {dates[-1]}，共 {len(dates)} 个交易日", flush=True)
    micro.build(start, end)
    pack = hist_pack(dates)
    print(f"hist_pack: breadth {len(pack['breadth'])} 天", flush=True)

    off = run(dates, pack, False, "off")
    on = run(dates, pack, True, "on")

    m_off, m_on = metrics(off), metrics(on)
    print("\n" + "=" * 72)
    print("P1/P2 A/B 验收（off=10.7.1 基线，on=10.7.2）")
    print("=" * 72)
    keys = ("days", "mean_target", "ge20pp_days", "max_jump_pp",
            "three_day_roundtrip", "cap_eq_target_days", "ge60_days",
            "mean_hard_cap", "active_add_days", "extra_cap_days",
            "deny_active_days")
    label = {"days": "交易日数", "mean_target": "目标仓位均值(%)",
             "ge20pp_days": "单日跳变≥20pp天数", "max_jump_pp": "最大单日跳变(pp)",
             "three_day_roundtrip": "3日方向往返次数",
             "cap_eq_target_days": "硬上限==目标天数", "ge60_days": "≥60%仓位天数",
             "mean_hard_cap": "硬上限均值(%)", "active_add_days": "主动加仓允许天数",
             "extra_cap_days": "确认门控回落硬上限天数",
             "deny_active_days": "关闭主动加仓天数"}
    print(f"{'指标':<26}{'off':>14}{'on':>14}")
    for k in keys:
        print(f"{label[k]:<26}{str(m_off[k]):>14}{str(m_on[k]):>14}")
    print("\n平滑器动作分布(on)：", m_on['smoother'])
    print("波动率状态分布(on)：", m_on['vol_status'])

    out = {"off": m_off, "on": m_on, "range": [start, end]}
    with open(os.path.join(BASE_DIR, "_bt_opt_replay_metrics.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n指标已保存：_bt_opt_replay_metrics.json")


if __name__ == '__main__':
    main()
