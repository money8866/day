# -*- coding: utf-8 -*-
"""
2026 全年每日仓位回放（用于生成动态柱状图）
=========================================
输出：_bt_out/position_2026.csv
  trade_date, state, state_cn, authority_max(仓位权限上限), suggested(建议仓位),
  qualified(合格机会数), breadth, zt, dt, broken_rate, m_index/m_breadth/m_money/m_mainline,
  strong_passed, switch(是否状态切换), exit_check

★ 严格按时间顺序串行推进状态链（否则 confirm_days 确认机制失效）
★ 预计算广度/成交额/涨跌停历史序列，注入 V10.0 避免逐日扫全表
★ 数据缺失如实标注：个股信号只覆盖 20260601 起、主线只覆盖 20260724 起
"""
import os
import sys
import json
import sqlite3
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import numpy as np

import market_regime_v101 as V101
import market_regime_v10 as v10

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, '_bt_out')
os.makedirs(OUT_DIR, exist_ok=True)

PACK = None


def precompute_hist(dates):
    """预计算广度/成交额/涨跌停序列（口径与 V10.0 一致）。"""
    db = os.path.join(os.path.dirname(BASE_DIR), 'cache_daily', 'stock_data.db')
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.execute("PRAGMA busy_timeout = 30000")
    breadth, amount, dt = {}, {}, {}
    for d in dates:
        df = pd.read_sql_query(
            "SELECT ts_code, pct_chg, amount FROM daily_cache WHERE trade_date = ?",
            conn, params=(d,))
        if df.empty:
            continue
        up = int((df['pct_chg'] > 0).sum())
        dn = int((df['pct_chg'] < 0).sum())
        breadth[d] = up / (up + dn) * 100.0 if (up + dn) else 0.0
        amount[d] = float(df['amount'].sum()) / 100000.0
        thr = df['ts_code'].map(
            lambda c: 20.0 if str(c)[:3] in ('300', '301', '688') else 10.0)
        dt[d] = int((df['pct_chg'] <= -thr * 0.99).sum())
    conn.close()
    return {"breadth": breadth, "amount": amount, "dt": dt}


def main():
    cfg = V101.load_config()
    v10cfg = v10.load_config()

    # ---- 全年交易日 ----
    db = os.path.join(os.path.dirname(BASE_DIR), 'cache_daily', 'stock_data.db')
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT trade_date FROM daily_cache "
        "WHERE trade_date >= '20260101' ORDER BY trade_date")]
    conn.close()
    print(f"回放区间：{dates[0]} ~ {dates[-1]}，共 {len(dates)} 个交易日")

    print("预计算历史序列…")
    t0 = datetime.datetime.now()
    globals()['PACK'] = precompute_hist(dates)
    print(f"  ✓ 完成 {(datetime.datetime.now()-t0).seconds}s\n")

    # ---- 注入 hist_pack，避免逐日重复扫库 ----
    _orig = v10.collect_market_snapshot

    def _fast(trade_date, c, prev_snapshot=None, hist_pack=None):
        return _orig(trade_date, c, prev_snapshot, hist_pack or PACK)
    v10.collect_market_snapshot = _fast
    # ★ V10.2：V10.1 的 _get_snapshot 也需要拿到 hist_pack，
    #   否则会回落到逐日扫全表（单日 562 次 SQL → 全年回放超时）
    V101.set_hist_pack(PACK)

    # ---- 串行状态链 ----
    chain = {"state": None, "consecutive_days": 0,
             "pending_target": "", "pending_days": 0}
    rows = []
    for i, d in enumerate(dates):
        _orig_load = v10.load_prev_state

        def _load(td, _c=chain):
            return dict(_c) if _c.get("state") else None
        v10.load_prev_state = _load
        try:
            r = V101.analyze_trade_date_v101(d, cfg, None, persist=False)
        except Exception as e:
            print(f"  {d} 失败: {e}")
            v10.load_prev_state = _orig_load
            continue
        finally:
            v10.load_prev_state = _orig_load

        s = r.snapshot
        rows.append({
            'trade_date': d,
            'state': r.state,
            'state_cn': r.state_cn,
            'state_days': r.state_days,
            'authority_max': r.authority.max_position,
            'suggested': r.target.suggested,
            'qualified': r.target.qualified_count,
            'observe': r.target.observe_count,
            'auth': r.authorization.result,
            'breadth': round((s.breadth.breadth_ratio or 0) * 100, 2),
            'zt': s.limit.zt_count,
            'dt': s.limit.dt_count,
            'broken_rate': s.limit.broken_rate,
            'm_index': int(r.strong.modules['指数趋势'].passed),
            'm_breadth': int(r.strong.modules['市场广度'].passed),
            'm_money': int(r.strong.modules['短线赚钱效应'].passed),
            'm_mainline': int(r.strong.modules['主线持续性'].passed),
            'strong_passed': r.strong.passed_count,
            'mainline_missing': int(bool(r.strong.modules['主线持续性'].missing)),
            'exit_check': r.exit_check.result if r.exit_check else '',
            'switched': int(r.state != chain.get('state')),
        })
        chain = {
            "state": r.state,
            "consecutive_days": r.state_days,
            "pending_target": getattr(r.v10_decision, "pending_target", "") or "",
            "pending_days": getattr(r.v10_decision, "pending_days", 0) or 0,
            "vote_history": getattr(r.v10_decision, "vote_history", []) or [],
        }
        if (i + 1) % 25 == 0:
            print(f"  已回放 {i+1}/{len(dates)}")

    df = pd.DataFrame(rows)
    out_csv = os.path.join(OUT_DIR, 'position_2026.csv')
    df.to_csv(out_csv, index=False)
    print(f"\n✓ 已保存 {out_csv}（{len(df)} 行）")

    # ---- 摘要 ----
    print("\n" + "=" * 60)
    print("全年仓位摘要")
    print("=" * 60)
    vc = df['state'].value_counts()
    for st in ['S1', 'S2', 'S3', 'S4', 'S5', 'S6']:
        n = int(vc.get(st, 0))
        print(f"  {st}: {n:>3} 天 ({n/len(df)*100:>5.1f}%)")
    print(f"\n  仓位权限上限：均值 {df['authority_max'].mean():.1f}%  最大 {df['authority_max'].max()}%")
    print(f"  建议仓位：均值 {df['suggested'].mean():.1f}%  最大 {df['suggested'].max()}%")
    sw = int(df['switched'].sum())
    print(f"  状态切换：{sw} 次 / {len(df)} 天 = {sw/len(df)*100:.1f}%")
    print(f"  满仓授权：{df['auth'].value_counts().to_dict()}")
    # 数据覆盖
    q = df[df['qualified'] > 0]
    print(f"\n  有选股信号的日子：{len(q)} 天（{q['trade_date'].min()} ~ {q['trade_date'].max()}）")
    print(f"  主线模块 UNKNOWN 的日子：{int(df['mainline_missing'].sum())} 天")


if __name__ == '__main__':
    main()
