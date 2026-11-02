#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
V10.0 历史验证
一次性预计算历史序列 → 逐日回放节点识别 + 状态机 → 统计检验

检验项（对应任务第十节）：
  1. 恐慌加速 / 卖压衰减 / 修复确认 后 1、3、5 个交易日的市场表现
  2. 不同状态下涨停股次日表现与市场广度变化
  3. 状态切换频率（是否在相邻状态间频繁跳动）
  4. 与简单基准规则（广度<35% 一律空仓）对比，状态机是否真正改善风险控制
  5. 数据质量检查：未来函数 / UNKNOWN 处理 / 是否把极端下跌当成底部
  6. 时间顺序切分：前半段调参、后半段样本外验证
"""
import os
import sys
import json
import time
import sqlite3
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import market_regime_v10 as V10

OUT = os.path.join(BASE, '_bt_out')
os.makedirs(OUT, exist_ok=True)
CACHE = os.path.join(BASE, 'cache_backbone_tushare')
HIST_CACHE = os.path.join(CACHE, 'v10_daily_history.csv')
IDX_CACHE = os.path.join(CACHE, 'v10_index_hist.csv')


# ============================================================
# 1) 预计算历史序列
# ============================================================
def build_history(days=320):
    """一次性计算历史：广度 / 涨跌停 / 成交额 / 昨日涨停股今日表现 / 晋级率"""
    if os.path.exists(HIST_CACHE):
        df = pd.read_csv(HIST_CACHE, dtype={'trade_date': str})
        print(f'  复用历史缓存 {HIST_CACHE} ({len(df)} 天)')
        return df

    conn = sqlite3.connect(V10.STOCK_DB, timeout=30.0)
    conn.execute("PRAGMA busy_timeout = 30000")
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT trade_date FROM daily_cache WHERE trade_date <= ? "
        "ORDER BY trade_date DESC LIMIT ?", ('20261030', days))]
    dates = sorted(dates)
    print(f'  计算 {len(dates)} 个交易日的基础序列（daily_cache 已缓存，不重复下载）...')

    frames = {}
    for d in dates:
        df = pd.read_sql_query(
            "SELECT ts_code, pct_chg, amount FROM daily_cache WHERE trade_date = ?",
            conn, params=(d,))
        frames[d] = df if not df.empty else None

    rows = []
    for i, d in enumerate(dates):
        df = frames[d]
        if df is None or df.empty:
            continue
        up = int((df['pct_chg'] > 0).sum())
        dn = int((df['pct_chg'] < 0).sum())
        fl = int((df['pct_chg'] == 0).sum())
        lc = V10._count_limit(df)
        row = {
            'trade_date': d,
            'up': up, 'down': dn, 'flat': fl, 'total': len(df),
            'breadth_ratio': up / (up + dn) * 100 if (up + dn) else np.nan,
            'zt': lc.get('zt_count'), 'dt': lc.get('dt_count'),
            'amount_yi': float(df['amount'].sum()) / 100000.0,
            'strong_mean_pct': np.nan, 'strong_up_ratio': np.nan,
            'strong_count': np.nan, 'promotion_rate': np.nan,
        }
        # 昨日涨停股的今日表现 + 连板晋级率
        if i > 0 and frames.get(dates[i-1]) is not None:
            ydf = frames[dates[i-1]]
            ythr = ydf['ts_code'].map(V10._limit_thresholds)
            ymask = (ydf['pct_chg'] >= ythr * 0.99) & (ydf['pct_chg'] < ythr + 0.1)
            y_zt = set(ydf[ymask]['ts_code'].astype(str))
            if y_zt:
                today = df[df['ts_code'].astype(str).isin(y_zt)]
                if not today.empty:
                    pcts = today['pct_chg'].astype(float)
                    row['strong_mean_pct'] = float(pcts.mean())
                    row['strong_up_ratio'] = float((pcts > 0).mean()) * 100
                    row['strong_count'] = int(len(pcts))
                    tthr = today['ts_code'].map(V10._limit_thresholds)
                    cont = int(((pcts >= tthr * 0.99) & (pcts < tthr + 0.1)).sum())
                    row['promotion_rate'] = cont / len(y_zt) * 100
        rows.append(row)
    conn.close()
    df = pd.DataFrame(rows)
    df.to_csv(HIST_CACHE, index=False)
    print(f'  → {HIST_CACHE} ({len(df)} 天)')
    return df


def build_index_hist():
    """指数历史（各指数独立序列）"""
    if os.path.exists(IDX_CACHE):
        df = pd.read_csv(IDX_CACHE, dtype={'trade_date': str})
        print(f'  复用指数缓存 {IDX_CACHE}')
        return df
    conn = sqlite3.connect(V10.STOCK_DB, timeout=30.0)
    frames = []
    for nm, code in V10.INDEX_LIST:
        d = pd.read_sql_query(
            "SELECT ts_code, trade_date, close, pct_chg, vol, amount FROM index_daily_cache "
            "WHERE ts_code = ? AND trade_date >= '20240101' ORDER BY trade_date",
            conn, params=(code,))
        if not d.empty:
            d['name'] = nm
            frames.append(d)
    conn.close()
    df = pd.concat(frames, ignore_index=True)
    df.to_csv(IDX_CACHE, index=False)
    print(f'  → {IDX_CACHE} ({len(df)} 行）')
    return df


# ============================================================
# 2) 逐日回放
# ============================================================
def replay(hist_df, idx_df, cfg, start_date='20260101'):
    """逐日构造快照 → 节点识别 → 状态机。严格时间顺序，无未来数据。"""
    hist = hist_df.copy()
    hist['trade_date'] = hist['trade_date'].astype(str)
    hist = hist[hist['trade_date'] >= start_date].sort_values('trade_date').reset_index(drop=True)

    # 历史序列包（供分位与均值计算；切片只含 <= 当前日 的数据 → 无未来函数）
    pack = {
        'breadth': {r['trade_date']: r['breadth_ratio'] for _, r in hist.iterrows()},
        'amount': {r['trade_date']: r['amount_yi'] for _, r in hist.iterrows()},
        'dt': {r['trade_date']: r['dt'] for _, r in hist.iterrows()},
    }
    # 分位需要更长历史，用全量
    full = hist_df.copy()
    full['trade_date'] = full['trade_date'].astype(str)
    pack['breadth'] = {r['trade_date']: r['breadth_ratio'] for _, r in full.iterrows()}
    pack['amount'] = {r['trade_date']: r['amount_yi'] for _, r in full.iterrows()}
    pack['dt'] = {r['trade_date']: r['dt'] for _, r in full.iterrows()}

    # 指数历史字典
    idx_map = {}
    for nm, _ in V10.INDEX_LIST:
        d = idx_df[idx_df['name'] == nm].copy()
        d['trade_date'] = d['trade_date'].astype(str)
        idx_map[nm] = d.sort_values('trade_date').reset_index(drop=True)

    # 炸板率（tushare limit_list_d，缓存）
    zb_cache = os.path.join(CACHE, 'v10_broken_rate.csv')
    if os.path.exists(zb_cache):
        zbd = pd.read_csv(zb_cache, dtype={'trade_date': str})
        # 炸板率 = 炸板 / (封住 + 炸板)，需结合当日涨停家数
        zhaban = dict(zip(zbd['trade_date'], zbd['zhaban']))
    else:
        zhaban = {}

    out = []
    prev_snap = None
    prev_state = None
    prev_days = 0
    prev_pending_target = None
    prev_pending_days = 0
    t0 = time.time()
    for _, row in hist.iterrows():
        td = row['trade_date']
        snap = V10.MarketSnapshot(trade_date=td)

        b = snap.breadth
        b.up_count, b.down_count, b.flat_count, b.total = int(row['up']), int(row['down']), int(row['flat']), int(row['total'])
        b.breadth_ratio = float(row['breadth_ratio']) if row['breadth_ratio'] == row['breadth_ratio'] else None
        b._hist = V10._hist_slice(pack, 'breadth', td, 250)
        if b.breadth_ratio is not None and b._hist:
            b.breadth_percentile = V10._percentile_rank(b._hist, b.breadth_ratio * 100)

        lim = snap.limit
        lim.zt_count = int(row['zt']) if row['zt'] == row['zt'] else None
        lim.dt_count = int(row['dt']) if row['dt'] == row['dt'] else None
        lim._dt_hist = V10._hist_slice(pack, 'dt', td, 250)
        if lim.dt_count is not None and lim._dt_hist:
            lim.dt_percentile = V10._percentile_rank(lim._dt_hist, float(lim.dt_count))
        _zb = zhaban.get(td)
        if _zb is not None and lim.zt_count is not None:
            _tot = lim.zt_count + int(_zb)
            lim.broken_rate = (_zb / _tot * 100) if _tot > 0 else None
        lim.promotion_rate = float(row['promotion_rate']) if row['promotion_rate'] == row['promotion_rate'] else None
        lim.strong_stock_mean_pct = float(row['strong_mean_pct']) if row['strong_mean_pct'] == row['strong_mean_pct'] else None
        lim.strong_stock_up_ratio = (float(row['strong_up_ratio']) / 100) if row['strong_up_ratio'] == row['strong_up_ratio'] else None
        lim.strong_stock_count = int(row['strong_count']) if row['strong_count'] == row['strong_count'] else None

        # 前一日对比
        i = hist.index[hist['trade_date'] == td][0]
        if i > 0:
            p = hist.iloc[i - 1]
            lim.prev_dt_count = int(p['dt']) if p['dt'] == p['dt'] else None
            lim.prev_zt_count = int(p['zt']) if p['zt'] == p['zt'] else None
            if lim.dt_count is not None and lim.prev_dt_count:
                lim.dt_change_ratio = lim.dt_count / lim.prev_dt_count
            b.prev_breadth_ratio = float(p['breadth_ratio']) if p['breadth_ratio'] == p['breadth_ratio'] else None
            if b.breadth_ratio is not None and b.prev_breadth_ratio is not None:
                b.delta_pp = (b.breadth_ratio - b.prev_breadth_ratio) * 100

        am = snap.amount
        am._hist = V10._hist_slice(pack, 'amount', td, 60)
        am.total_amount = float(row['amount_yi'])
        if len(am._hist) >= 2:
            am.prev_amount = am._hist[-2]
            if am.prev_amount:
                am.chg_vs_prev = (am.total_amount / am.prev_amount - 1) * 100
            if len(am._hist) >= 6:
                m5 = float(np.mean(am._hist[-6:-1]))
                am.chg_vs_ma5 = (am.total_amount / m5 - 1) * 100
            if len(am._hist) >= 21:
                m20 = float(np.mean(am._hist[-21:-1]))
                am.chg_vs_ma20 = (am.total_amount / m20 - 1) * 100

        for nm, _ in V10.INDEX_LIST:
            idf = idx_map.get(nm)
            if idf is None:
                continue
            sub = idf[idf['trade_date'] <= td]
            snap.indexes[nm] = V10._compute_index_metrics(sub, nm, '', cfg)

        core = snap.indexes.get(V10.CORE_INDEX)
        bench = snap.indexes.get(V10.BENCH_INDEX)
        if core and bench and core.pct_chg is not None and bench.pct_chg is not None:
            snap.small_vs_large_gap = core.pct_chg - bench.pct_chg
        if am.total_amount is not None and core and core.pct_chg is not None:
            am.index_chg = core.pct_chg
            if am.chg_vs_ma5 is not None:
                expand = am.chg_vs_ma5 > 0
                am.pattern = ("放量上涨" if expand and core.pct_chg > 0 else
                              "放量下跌" if expand and core.pct_chg < 0 else
                              "缩量上涨" if core.pct_chg > 0 else "缩量下跌")

        snap.prev_snapshot = prev_snap
        node = V10.detect_market_nodes(snap, cfg)
        prev = ({"state": prev_state, "consecutive_days": prev_days,
                 "pending_target": prev_pending_target,
                 "pending_days": prev_pending_days} if prev_state else None)
        dec = V10.decide_state(snap, node, prev, cfg)
        prev_pending_target = dec.pending_target
        prev_pending_days = dec.pending_days
        pa = V10.suggest_position(dec, snap, cfg)

        out.append({
            'trade_date': td,
            'node': node.node_type, 'node_hits': len(node.evidence),
            'state': dec.state, 'state_cn': dec.state_cn, 'consec': dec.consecutive_days,
            'changed': dec.changed, 'confidence': dec.confidence,
            'target': pa.target_position,
            'breadth': b.breadth_ratio, 'breadth_pct': b.breadth_percentile,
            'zt': lim.zt_count, 'dt': lim.dt_count, 'zb': lim.broken_rate,
            'promo': lim.promotion_rate, 'strong_mean': lim.strong_stock_mean_pct,
            'strong_up': lim.strong_stock_up_ratio,
            'amount': am.total_amount, 'vs_ma5': am.chg_vs_ma5, 'pattern': am.pattern,
            'gap': snap.small_vs_large_gap,
            'idx_chg': core.pct_chg if core else None,
            'pos_pct': core.close_percentile_250d if core else None,
        })
        prev_snap = snap
        prev_state = dec.state
        prev_days = dec.consecutive_days

    print(f'  回放 {len(out)} 天，耗时 {time.time()-t0:.1f}s')
    return pd.DataFrame(out)


# ============================================================
# 3) 检验
# ============================================================
def node_forward_stats(df, node_type, label):
    """节点后 1/3/5 日的市场表现"""
    idx = df[df['node'] == node_type]
    if len(idx) == 0:
        print(f'\n  【{label}】无触发样本')
        return
    df = df.reset_index(drop=True)
    pos = {d: i for i, d in enumerate(df['trade_date'])}
    print(f'\n  【{label}】触发 {len(idx)} 天')
    print(f'    {"horizon":>8} {"N":>4} {"中证2000均":>11} {"中证2000中位":>13} {"胜率":>8} {"广度变化":>10}')
    for h in (1, 3, 5):
        rets, dbr = [], []
        for _, r in idx.iterrows():
            i = pos[r['trade_date']]
            if i + h < len(df):
                nxt = df.iloc[i + h]
                if nxt['idx_chg'] is not None:
                    rets.append(nxt['idx_chg'])
                if r['breadth'] is not None and nxt['breadth'] is not None:
                    dbr.append(nxt['breadth'] - r['breadth'])
        if not rets:
            continue
        r_ = np.array(rets)
        print(f'    {"T+"+str(h):>8} {len(rets):>4} {r_.mean():>11.2f} {np.median(r_):>13.2f} '
              f'{(r_>0).mean()*100:>7.1f}% {(np.mean(dbr) if dbr else float("nan")):>10.1f}')


def main():
    print('=' * 88)
    print('V10.0 历史验证')
    print('=' * 88)
    cfg = V10.load_config()

    print('\n[1] 预计算历史序列')
    hist_df = build_history()
    idx_df = build_index_hist()

    print('\n[2] 逐日回放（严格时间顺序）')
    df = replay(hist_df, idx_df, cfg, start_date='20260101')
    df.to_csv(os.path.join(OUT, 'v10_replay.csv'), index=False)
    print(f'  → _bt_out/v10_replay.csv ({len(df)} 天)')

    print('\n[3] 节点后续表现')
    node_forward_stats(df, V10.NODE_PANIC, '恐慌加速')
    node_forward_stats(df, V10.NODE_EASING, '卖压衰减')
    node_forward_stats(df, V10.NODE_REPAIR, '修复确认')
    node_forward_stats(df, V10.NODE_EXTREME, '极端情绪')

    print('\n[4] 状态分布与切换频率')
    print(f'  {"状态":<16} {"天数":>5} {"占比":>8} {"平均连续":>9}')
    for st in [V10.STATE_NORMAL, V10.STATE_DRAWDOWN, V10.STATE_PANIC, V10.STATE_EASING, V10.STATE_REPAIR]:
        g = df[df['state'] == st]
        if len(g) == 0:
            continue
        print(f'  {st} {V10.STATE_CN[st]:<12} {len(g):>5} {len(g)/len(df)*100:>7.1f}% {g["consec"].mean():>9.1f}')
    changes = df['changed'].sum()
    print(f'\n  状态切换次数：{changes} / {len(df)} 天 = {changes/len(df)*100:.1f}%（越低越稳）')
    print('  切换明细（前 25 次）:')
    for _, r in df[df['changed']].head(25).iterrows():
        print(f'    {r["trade_date"]}  {r["prev"] if "prev" in r else ""} → {r["state"]} {r["state_cn"]}')

    print('\n[5] 各状态下的短线情绪对比')
    print(f'  {"状态":<16} {"N":>4} {"广度%":>8} {"涨停":>6} {"跌停":>6} {"炸板%":>8} {"晋级%":>8} {"昨涨停今日%":>12}')
    for st in [V10.STATE_NORMAL, V10.STATE_DRAWDOWN, V10.STATE_PANIC, V10.STATE_EASING, V10.STATE_REPAIR]:
        g = df[df['state'] == st]
        if len(g) == 0:
            continue
        def m(c):
            v = g[c].dropna()
            return f'{v.mean():>8.1f}' if len(v) else f'{"-":>8}'
        print(f'  {st} {V10.STATE_CN[st]:<12} {len(g):>4} {m("breadth")} {g["zt"].mean():>6.0f} '
              f'{g["dt"].mean():>6.0f} {m("zb")} {m("promo")} {m("strong_mean")}')

    print('\n[6] 与简单基准规则对比（广度<35% 一律空仓）')
    df['fwd1'] = df['idx_chg'].shift(-1)
    df['fwd3'] = df['idx_chg'].shift(-3)
    base = df.copy()
    base['ret'] = np.where(base['breadth'] < 35, 0.0, 1.0) * base['fwd1'].fillna(0)
    sm = df.copy()
    tgt = df['target'].fillna(50) / 100.0
    sm['ret'] = tgt * sm['fwd1'].fillna(0)
    for name, r in [('基准(广度<35%空仓)', base['ret']), ('V10状态机', sm['ret'])]:
        r = r.dropna()
        cum = (1 + r / 100).prod() - 1
        print(f'  {name:<22} 累计{cum*100:>8.2f}%  年化波动{r.std()*np.sqrt(252):>6.2f}%  '
              f'Sharpe{(r.mean()/r.std()*np.sqrt(252) if r.std() else 0):>6.2f}  胜率{(r>0).mean()*100:>5.1f}%')

    print('\n[7] 时间顺序切分（前半调参 / 后半样本外）')
    n = len(df)
    split = n // 2
    tr, te = df.iloc[:split], df.iloc[split:]
    print(f'  训练段 {tr["trade_date"].min()}~{tr["trade_date"].max()} ({len(tr)} 天)')
    print(f'  样本外 {te["trade_date"].min()}~{te["trade_date"].max()} ({len(te)} 天)')
    for name, seg in [('训练段', tr), ('样本外', te)]:
        node_forward_stats(seg, V10.NODE_PANIC, f'恐慌加速（{name}）')
        node_forward_stats(seg, V10.NODE_REPAIR, f'修复确认（{name}）')

    print('\n[8] 数据质量检查')
    print(f'  位置分位 UNKNOWN 天数（pos_pct 为空）：{df["pos_pct"].isna().sum()} / {len(df)}')
    print(f'  炸板率 UNKNOWN 天数：{df["zb"].isna().sum()} / {len(df)}')
    print(f'  昨日涨停股表现 UNKNOWN 天数：{df["strong_mean"].isna().sum()} / {len(df)}')
    # 状态为 S1 但广度极低 → 是否有「UNKNOWN 被当正常」的情况
    bad = df[(df['state'] == V10.STATE_NORMAL) & (df['breadth'] < 20)]
    print(f'  ⚠ 状态为 S1 正常但广度<20% 的天数：{len(bad)}（应尽量少）')
    if len(bad):
        print(bad[['trade_date', 'breadth', 'node', 'state']].head(10).to_string(index=False))
    # 恐慌日被误判为修复
    bad2 = df[(df['node'] == V10.NODE_PANIC) & (df['state'] == V10.STATE_REPAIR)]
    print(f'  ⚠ 恐慌节点但状态=修复的天数：{len(bad2)}（应为 0）')
    # 极端下跌当天是否给了高仓位
    bad3 = df[(df['idx_chg'] < -2) & (df['target'].fillna(0) > 30)]
    print(f'  ⚠ 指数跌超2% 但目标仓位>30% 的天数：{len(bad3)}（应尽量少）')

    print(f'\n  → _bt_out/v10_replay.csv')


if __name__ == '__main__':
    main()
