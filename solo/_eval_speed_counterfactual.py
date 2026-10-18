#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
反事实模拟：加仓限速从 +8/日 改为 +12/+15/日 的效果
- 识别「撞限速」的加仓日（position_T - position_{T-1} == +8）
- 反事实提升这些日的仓位，重算组合表现
- 硬约束：下行段回撤不得恶化
"""
import sqlite3
import pandas as pd
import numpy as np
from io import StringIO

CACHE_DB = 'D:/mystock/solo/cache_backbone_tushare/cache.db'
MA_DB = 'D:/mystock/solo/cache_backbone_tushare/market_analysis.db'

def load_series():
    conn = sqlite3.connect(CACHE_DB)
    cur = conn.cursor()
    cur.execute("SELECT key, data FROM cache_data WHERE key LIKE 'tsc_index_kline_ts_code_932000.CSI%'")
    rows = cur.fetchall()
    conn.close()
    out = {}
    for key, data in rows:
        df = pd.read_csv(StringIO(data))
        last = df.iloc[-1]
        out[str(last['trade_date'])] = float(last['close'])
    return pd.Series(out).sort_index()

def load_advice():
    conn = sqlite3.connect(MA_DB)
    df = pd.read_sql_query("SELECT trade_date, total_position FROM overall_analysis ORDER BY trade_date", conn)
    conn.close()
    df['trade_date'] = df['trade_date'].astype(str)
    return df.set_index('trade_date')['total_position']

def simulate(closes, pos, cap_speed):
    """按给定限速模拟仓位路径：position_t = min(target_t, prev + cap_speed)（减仓不限）"""
    dates = list(closes.index)
    sim = []
    prev = None
    for i, d in enumerate(dates):
        if d not in pos.index:
            prev = prev  # 无建议日保持
            continue
        tgt = float(pos.loc[d])
        if prev is None:
            p = tgt
        else:
            diff = tgt - prev
            if diff > 0:
                p = prev + min(diff, cap_speed)
            else:
                p = tgt
        sim.append((d, p, tgt))
        prev = p
    return pd.DataFrame(sim, columns=['d', 'pos', 'tgt']).set_index('d')

def perf(closes, sim):
    dates = list(closes.index)
    rets, poss = [], []
    idx = {d: i for i, d in enumerate(dates)}
    for d, row in sim.iterrows():
        if d not in idx: continue
        i = idx[d]
        if i + 1 >= len(dates): continue
        rets.append(closes.iloc[i + 1] / closes.iloc[i] - 1)
        poss.append(row['pos'] / 100)
    rets = pd.Series(rets); poss = pd.Series(poss)
    strat = poss * rets
    cum = (1 + strat).cumprod()
    dd = (cum / cum.cummax() - 1).min()
    return {'ret': cum.iloc[-1] - 1, 'dd': dd, 'avg_pos': poss.mean() * 100}

def env_stats(closes, sim):
    """分环境统计仓位"""
    dates = list(closes.index)
    idx = {d: i for i, d in enumerate(dates)}
    recs = []
    for d, row in sim.iterrows():
        if d not in idx: continue
        i = idx[d]
        if i + 5 >= len(dates): continue
        r5 = closes.iloc[i + 5] / closes.iloc[i] - 1
        recs.append({'r5': r5 * 100, 'pos': row['pos']})
    df = pd.DataFrame(recs)
    if df.empty: return {}
    df['env'] = pd.cut(df['r5'], [-100, -3, 3, 100], labels=['下行', '震荡', '上行'])
    return {env: {'days': len(s), 'avg_pos': s['pos'].mean(), 'hi_days': len(s[s['pos'] >= 50]),
                  'r5': s['r5'].mean()} for env, s in df.groupby('env', observed=True)}

def main():
    closes = load_series()
    pos = load_advice()
    print(f"中证2000行情 {len(closes)} 天，建议 {len(pos)} 天\n")

    print("=" * 88)
    print("反事实模拟：加仓单日限速（现状 +8%/日）")
    print("=" * 88)
    print(f"{'限速':>6}{'累计收益':>10}{'最大回撤':>10}{'平均仓位':>10}   "
          f"{'上行段均仓':>10}{'上行段>=50%天数':>14}{'下行段均仓':>10}{'下行段回撤':>10}")
    results = {}
    for speed in (8, 10, 12, 15, 20, 999):
        sim = simulate(closes, pos, speed)
        p = perf(closes, sim)
        e = env_stats(closes, sim)
        up = e.get('上行', {}); dn = e.get('下行', {})
        results[speed] = (p, e)
        print(f"{speed if speed < 999 else '不限':>6}{p['ret'] * 100:>9.2f}%{p['dd'] * 100:>9.2f}%"
              f"{p['avg_pos']:>9.1f}%   {up.get('avg_pos', 0):>9.1f}%{up.get('hi_days', 0):>12}天"
              f"{dn.get('avg_pos', 0):>9.1f}%{dn.get('r5', 0) * 0:>8}")

    # 分环境明细（最优与现状对比）
    print("\n" + "=" * 88)
    print("现状(+8) vs 提速(+15) 分环境明细")
    print("=" * 88)
    p8, e8 = results[8]
    p15, e15 = results[15]
    print(f"{'环境':<8}{'天数':>6}{'+8均仓':>10}{'+15均仓':>10}{'+8的5日收益':>12}{'+15的5日收益':>12}")
    for env in ('上行', '震荡', '下行'):
        a = e8.get(env, {}); b = e15.get(env, {})
        print(f"{env:<8}{a.get('days', 0):>6}{a.get('avg_pos', 0):>9.1f}%{b.get('avg_pos', 0):>9.1f}%"
              f"{a.get('r5', 0):>11.2f}%{b.get('r5', 0):>11.2f}%")

    # 撞限速日统计
    sim8 = simulate(closes, pos, 8)
    hit = (sim8['pos'] - sim8['tgt'].shift(1)).abs()
    capped = ((sim8['tgt'] - sim8['tgt'].shift(1)) > 8).sum()
    print(f"\n撞 +8 限速的天数: {capped} / {len(sim8)} （这些天系统想加更多但被限速夹住）")

if __name__ == '__main__':
    main()
