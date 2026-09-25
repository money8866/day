# -*- coding: utf-8 -*-
"""三花聚顶研究 · 与 HVT-BULL 正交验证（第七阶段）

原则: 只读 HVT 产物，不修改 HVT 任何逻辑与代码。
HVT 产物来源（只读）:
  1) report_daily/hvt_bull_backtest_events_20250101_20260828.csv  —— HVT 全历史事件（4439 条）
  2) report_daily/hvt_bull_*.json                                  —— HVT 日快照（Top20 事件 + 执行池）

问题: 三花是否为 HVT 已捕捉的同一 Alpha?
口径:
  - 三花锚点 = 三花确认日（首板日 T0 后第 k_A 个交易日）
  - 对齐 A「同日同股」: HVT t0_date == 三花确认日
  - 对齐 B「近邻同股」: 同股票 HVT t0_date 与三花确认日相隔 <= 3 个交易日
  - 对齐 C「同一首板」: HVT t0_date == 三花事件的首板日 T0
输出: out/tf_hvt_orth.csv / out/tf_hvt_events.csv
"""
import os
import glob
import json
import sqlite3
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, 'out')
RD = r'd:\mystock\solo\report_daily'
DB = r'D:\mystock\cache_daily\stock_data.db'
HVT_EV = os.path.join(RD, 'hvt_bull_backtest_events_20250101_20260828.csv')
CORE_STATES = {'HVT_STRONG', 'PRIMARY_BUY', 'BREAKOUT_READY', 'LOCKED'}


def codes_of(x):
    out = set()
    if isinstance(x, list):
        for it in x:
            if isinstance(it, dict):
                if it.get('ts_code'):
                    out.add(it['ts_code'])
            elif isinstance(it, str):
                out.add(it)
    elif isinstance(x, dict):
        out.update(x.keys())
    return out


def load_snap():
    """日快照: 只含 Top20 events + 执行池（HVT 高优先级名单）"""
    fs = sorted(glob.glob(os.path.join(RD, 'hvt_bull_2*.json')))
    wide, core, dates = {}, {}, []
    for f in fs:
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        dt = str(d.get('trade_date'))
        dates.append(dt)
        w = codes_of(d.get('events')) | codes_of(d.get('te_buy_pool'))
        st = d.get('all_states')
        if isinstance(st, dict):
            for k, v in st.items():
                if k in CORE_STATES:
                    w |= codes_of(v)
        wide[dt], core[dt] = w, codes_of(d.get('te_buy_pool'))
    return wide, core, sorted(set(dates))


def main():
    # ── 交易日历
    conn = sqlite3.connect(DB, timeout=30.0)
    cal = pd.read_sql_query(
        "SELECT trade_date FROM index_daily_cache WHERE ts_code='000001.SH' ORDER BY trade_date",
        conn)
    conn.close()
    cal = cal['trade_date'].astype(np.int64).tolist()
    pos = {d: i for i, d in enumerate(cal)}

    # ── 三花事件
    df = pd.read_parquet(os.path.join(OUT, 'tf_base.parquet')).reset_index(drop=True)
    h1 = df['d_H1'].values.copy()
    kA = df['k_A'].fillna(-1).astype(int).values
    t0 = df['trade_date'].astype(np.int64).values
    cd = np.array([(cal[pos[t] + k] if (t in pos and k >= 0 and pos[t] + k < len(cal)) else -1)
                   for t, k in zip(t0, kA)], dtype=np.int64)
    df['t0n'], df['cdn'] = t0, cd
    df['cidx'] = [pos.get(int(c), -1) for c in cd]
    df['t0idx'] = [pos.get(int(t), -1) for t in t0]
    print('H1 事件 %d | 确认日可解析 %d' % (int(h1.sum()), int((h1 & (cd > 0)).sum())))

    # ── HVT 全历史事件
    he = pd.read_csv(HVT_EV, usecols=lambda c: c in [
        'ts_code', 'name', 't0_date', 'state', 'signal_tier', 'hvt_grade', 'breakout_date',
        'entry_score', 'score', 'r5', 'r10', 'r20', 'max_gain', 'max_dd'])
    he['t0n'] = he['t0_date'].astype(np.int64)
    he = he[he['t0n'].isin(pos)].copy()
    he['tidx'] = he['t0n'].map(pos)
    print('HVT 事件 %d | 期间 %s ~ %s' % (len(he), he['t0n'].min(), he['t0n'].max()))

    ev_lo, ev_hi = int(he['t0n'].min()), int(he['t0n'].max())
    by_code = {}
    for c, ix in zip(he['ts_code'].values, he['tidx'].values):
        by_code.setdefault(c, []).append(int(ix))

    # ── 对齐（仅在三花确认日落在 HVT 事件期内时有意义）
    idx_h1 = np.where(h1)[0]
    in_period = np.array([ev_lo <= cd[i] <= ev_hi for i in idx_h1], dtype=bool)
    same_day, near3, same_t0 = [], [], []
    for i in idx_h1:
        c, t = int(cd[i]), int(t0[i])
        lst = by_code.get(df['ts_code'].values[i], [])
        ci = pos.get(c, -1)
        same_day.append(any(x == ci for x in lst))
        near3.append(any(abs(x - ci) <= 3 for x in lst))
        same_t0.append(any(x == df['t0idx'].values[i] for x in lst))
    same_day = np.asarray(same_day); near3 = np.asarray(near3); same_t0 = np.asarray(same_t0)

    # ── HVT 覆盖率（随机基准）：同一期间内任意事件与 HVT 重合的概率
    all_in_period = np.array([ev_lo <= c <= ev_hi for c in cd])
    print('\n三花确认日落在 HVT 事件期(2025-01-01~2026-08-28)内的 H1 事件: %d / %d'
          % (int(in_period.sum()), len(idx_h1)))
    print('对齐A 同日同股 命中 %d (%.1f%%) | 对齐B 近邻±3日 命中 %d (%.1f%%) | 对齐C 同一首板日 命中 %d (%.1f%%)'
          % (same_day.sum(), 100 * same_day.mean(), near3.sum(), 100 * near3.mean(),
             same_t0.sum(), 100 * same_t0.mean()))

    # HVT 自身密度基准：三花确认日当天，任取一只股票被 HVT 收录的概率
    hvt_days = he.groupby('t0n')['ts_code'].nunique()
    univ = 5000.0
    base_rate = float((hvt_days / univ).mean())
    print('HVT 平均每日收录股票数 %.1f（按 5000 只 A 股近似）→ 随机命中率基准 ≈ %.2f%%'
          % (hvt_days.mean(), 100 * base_rate))

    rows = []
    pc = lambda x: ('%+.2f%%' % (100 * x)) if x is not None and pd.notna(x) else ' n/a'

    for nm, flag in (('A_同日同股', same_day), ('B_近邻±3日', near3), ('C_同一首板日', same_t0)):
        mm = np.zeros(len(df), bool); mm[idx_h1[flag]] = True
        om = np.zeros(len(df), bool); om[idx_h1[in_period & ~flag]] = True
        r = {'align': nm, 'n_hit': int(mm.sum()), 'n_h1_in_period': int(in_period.sum()),
             'hit_rate': round(float(mm.sum() / max(in_period.sum(), 1)), 4),
             'random_baseline': round(base_rate, 4)}
        for N in (5, 10, 20):
            a = df.loc[mm, 'A_r%d' % N].astype(float).values
            b = df.loc[om, 'A_r%d' % N].astype(float).values
            a, b = a[np.isfinite(a)], b[np.isfinite(b)]
            r['hit_n_%d' % N], r['hit_med_%d' % N] = len(a), (round(float(np.median(a)), 5) if len(a) else None)
            r['rest_n_%d' % N], r['rest_med_%d' % N] = len(b), (round(float(np.median(b)), 5) if len(b) else None)
            r['diff_%d' % N] = (round(float(np.median(a) - np.median(b)), 5) if len(a) and len(b) else None)
        rows.append(r)
        print('-- %s | 命中 %d 只 (命中率 %.1f%%, 随机基准 %.2f%%)' % (nm, r['n_hit'],
              100 * r['hit_rate'], 100 * base_rate))
        for N in (5, 10, 20):
            print('   T+%-2d 交集 n=%-4s med %s || 三花-非HVT n=%-4s med %s || 差 %s'
                  % (N, r['hit_n_%d' % N], pc(r['hit_med_%d' % N]),
                     r['rest_n_%d' % N], pc(r['rest_med_%d' % N]), pc(r['diff_%d' % N])))

    # ── 日快照口径（HVT 高优先级名单 Top20 + 执行池）
    wide, core, sdates = load_snap()
    sd = set(sdates)
    m_snap_w = np.array([(str(int(c)) in sd and ts in wide[str(int(c))])
                         for c, ts in zip(cd, df['ts_code'].values)], dtype=bool)
    m_snap_c = np.array([(str(int(c)) in sd and ts in core[str(int(c))])
                         for c, ts in zip(cd, df['ts_code'].values)], dtype=bool)
    print('\n【日快照口径】窗口 %s~%s | H1 确认日落在窗口内 %d'
          % (sdates[0], sdates[-1], int((h1 & np.array([str(int(c)) in sd for c in cd])).sum())))
    for nm, fl in (('快照_WIDE(事件Top20+执行池)', m_snap_w), ('快照_CORE(执行池)', m_snap_c)):
        mm = h1 & fl
        r = {'align': nm, 'n_hit': int(mm.sum()),
             'n_h1_in_period': int((h1 & np.array([str(int(c)) in sd for c in cd])).sum()),
             'hit_rate': round(float(mm.sum() / max(int((h1 & np.array([str(int(c)) in sd for c in cd])).sum()), 1)), 4)}
        for N in (5, 10, 20):
            a = df.loc[mm, 'A_r%d' % N].astype(float).values
            a = a[np.isfinite(a)]
            r['hit_n_%d' % N], r['hit_med_%d' % N] = len(a), (round(float(np.median(a)), 5) if len(a) else None)
        rows.append(r)
        print('-- %s | 命中 %d 只' % (nm, r['n_hit']))

    pd.DataFrame(rows).to_csv(os.path.join(OUT, 'tf_hvt_orth.csv'), index=False, encoding='utf-8-sig')

    # ── 明细
    keep = ['ts_code', 'name', 'trade_date', 'cdn', 'k_A', 'A_r5', 'A_r10', 'A_r20',
            'volume_decay', 'price_progression', 'struct_dd', 'ma_conv', 'n_flower']
    sel = df.loc[h1.tolist() if isinstance(h1, np.ndarray) else h1, keep].copy()
    sel['alignA_same_day'] = same_day.astype(int)
    sel['alignB_near3'] = near3.astype(int)
    sel['alignC_same_t0'] = same_t0.astype(int)
    sel['in_hvt_period'] = ((sel['cdn'] >= ev_lo) & (sel['cdn'] <= ev_hi)).astype(int)
    sel.to_csv(os.path.join(OUT, 'tf_hvt_events.csv'), index=False, encoding='utf-8-sig')
    print('\n已写 tf_hvt_orth.csv / tf_hvt_events.csv')


if __name__ == '__main__':
    main()
