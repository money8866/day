# -*- coding: utf-8 -*-
"""V10.7 全年回放验证

用途：
  1. 检验新增的量价口径 / S4确认 / S5确认 / 结构性机会 在 2026 全年的表现
  2. 检验状态门控对仓位分布的影响（与 V10.1 基线对比）
  3. 检查是否有未来数据泄漏（每一项都只能用当日及历史数据）

用法：
  python _bt_v107_replay.py 20260101 20261103
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
        rows = conn.execute(
            "SELECT DISTINCT trade_date FROM daily_cache "
            "WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",
            (start, end)).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def hist_pack_from_micro(dates):
    """用市场微结构面板构造 V10.0 的 hist_pack（口径完全一致）。"""
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


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else '20260101'
    end = sys.argv[2] if len(sys.argv) > 2 else '20261103'

    cfg = v107.load_config()
    # 回放时只分析前 3 个候选行业（提速，不改变判定逻辑）
    cfg['structural_alpha']['max_analyze_candidates'] = 3

    dates = get_dates(start, end)
    print(f"回放区间：{dates[0]} ~ {dates[-1]}，共 {len(dates)} 个交易日")

    print("预计算微结构序列…", flush=True)
    micro.build(start, end)
    pack = hist_pack_from_micro(dates)
    print(f"  ✓ hist_pack: breadth {len(pack['breadth'])} 天")

    _orig_collect = v10.collect_market_snapshot

    def _collect_fast(trade_date, c, prev_snapshot=None, hist_pack=None):
        return _orig_collect(trade_date, c, prev_snapshot, hist_pack or pack)

    v10.collect_market_snapshot = _collect_fast

    chain = {"state": None, "consecutive_days": 0, "pending_target": "",
             "pending_days": 0, "vote_history": []}

    rows = []
    t0 = datetime.datetime.now()
    for i, d in enumerate(dates, 1):
        def _load_prev(td, _c=chain):
            return dict(_c) if _c.get("state") else None
        orig_load = v10.load_prev_state
        v10.load_prev_state = _load_prev
        try:
            r = v107.analyze_trade_date_v107(d, cfg, None, persist=False)
        except Exception as e:
            print(f"  {d} 运行异常: {e}")
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

        rows.append({
            "trade_date": d,
            "base_state": r.gate.base_state,
            "eff_state": r.state,
            "blocked": len(r.gate.blocked),
            "amount_pattern": r.amount.pattern,
            "volume_up": r.amount.is_volume_up,
            "easing": r.easing.verdict,
            "rebound": r.rebound.verdict,
            "structural": r.structural.verdict,
            "structural_ind": r.structural.industry,
            "base_target": r.base.target.suggested if r.base.target else None,
            "eff_target": r.target.suggested if r.target else None,
            "hard_cap": r.plan.hard_cap if r.plan else None,
        })
        if i % 20 == 0:
            print(f"  {i}/{len(dates)} … {(datetime.datetime.now()-t0).seconds}s", flush=True)

    out_path = os.path.join(BASE_DIR, "_bt_v107_replay.csv")
    df = pd.DataFrame(rows)
    df.to_csv(out_path, index=False, encoding="utf-8-sig")

    print("\n" + "=" * 70)
    print("V10.7 全年回放结果")
    print("=" * 70)
    print(f"耗时 {(datetime.datetime.now()-t0).seconds}s，共 {len(rows)} 天\n")

    print("【状态分布】V10.1 基线 vs V10.7 门控后")
    print("  基线：", dict(Counter(df['base_state'])))
    print("  门控：", dict(Counter(df['eff_state'])))
    changed = df[df['base_state'] != df['eff_state']]
    print(f"  门控改变状态的天数：{len(changed)}")
    if len(changed):
        print(changed[['trade_date', 'base_state', 'eff_state']].head(20).to_string(index=False))

    print("\n【量价形态分布】")
    print(dict(Counter(df['amount_pattern'])))
    print(f"  判定为「放量上涨」的天数：{int(df['volume_up'].sum())}")

    print("\n【三项确认】")
    print("  卖压衰减确认：", dict(Counter(df['easing'])))
    print("  反弹确认：  ", dict(Counter(df['rebound'])))
    print("  结构性机会：", dict(Counter(df['structural'])))

    st = df[df['structural'] == 'PASS']
    print(f"\n  结构性机会成立 {len(st)} 天，行业分布："
          f"{dict(Counter(st['structural_ind']))}")

    print("\n【仓位分布】")
    print(f"  V10.1 基线建议仓位均值：{df['base_target'].mean():.1f}%"
          f"　≥60% 天数：{int((df['base_target'] >= 60).sum())}")
    print(f"  V10.7 门控后均值：      {df['eff_target'].mean():.1f}%"
          f"　≥60% 天数：{int((df['eff_target'] >= 60).sum())}")

    print("\n【交叉验证】确认结果与状态的一致性")
    ct = pd.crosstab(df['base_state'], df['easing'])
    print("  状态 × 卖压衰减确认：")
    print(ct.to_string())
    ct2 = pd.crosstab(df['base_state'], df['rebound'])
    print("  状态 × 反弹确认：")
    print(ct2.to_string())

    print(f"\n明细已保存：{out_path}")


if __name__ == '__main__':
    main()
