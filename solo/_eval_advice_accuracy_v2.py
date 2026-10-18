#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
盘后仓位建议准确度评估 V2：多持有期口径
- 次日 / 未来3日 / 未来5日 三种持有期
- 分市场环境（按未来5日指数涨跌划分上行/下行/震荡段）
- 验证「IC≈0 是信号无效还是评估口径错配」
"""
import sqlite3
import pandas as pd
import numpy as np
from io import StringIO

CACHE_DB = 'D:/mystock/solo/cache_backbone_tushare/cache.db'
MA_DB = 'D:/mystock/solo/cache_backbone_tushare/market_analysis.db'
INDEX_MAP = {'上证指数': '000001.SH', '沪深300': '000300.SH', '中证2000': '932000.CSI'}

def load_index_series():
    conn = sqlite3.connect(CACHE_DB)
    cur = conn.cursor()
    cur.execute("SELECT key, data FROM cache_data WHERE key LIKE 'tsc_index_kline%'")
    rows = cur.fetchall()
    conn.close()
    out = {}
    for key, data in rows:
        code = key.split('ts_code_')[1].split('_')[0]
        df = pd.read_csv(StringIO(data))
        if df.empty or 'trade_date' not in df.columns:
            continue
        df['trade_date'] = df['trade_date'].astype(str)
        last = df.iloc[-1]
        out.setdefault(code, {})[last['trade_date']] = float(last['close'])
    return {code: pd.Series(d).sort_index() for code, d in out.items()}

def load_advice():
    conn = sqlite3.connect(MA_DB)
    df = pd.read_sql_query(
        "SELECT trade_date, total_position, trend_score, market_status "
        "FROM overall_analysis ORDER BY trade_date", conn)
    conn.close()
    df['trade_date'] = df['trade_date'].astype(str)
    return df

def main():
    series = load_index_series()
    advice = load_advice()
    advice_dates = set(advice['trade_date'])
    pos_map = dict(zip(advice['trade_date'], advice['total_position']))
    ts_map = dict(zip(advice['trade_date'], advice['trend_score']))

    recs = []
    for bm_name, code in INDEX_MAP.items():
        closes = series.get(code)
        if closes is None:
            continue
        dates = list(closes.index)
        for i, d in enumerate(dates):
            if d not in advice_dates:
                continue
            rec = {'bm': bm_name, 'd': d, 'pos': float(pos_map[d]),
                   'ts': float(ts_map[d]) if pd.notna(ts_map[d]) else np.nan}
            # 未来N日收益（T日收盘 -> T+N收盘）
            for h in (1, 3, 5):
                j = i + h
                rec[f'r{h}'] = (closes.iloc[j] / closes.iloc[i] - 1) * 100 if j < len(dates) else np.nan
            recs.append(rec)
    r = pd.DataFrame(recs)

    print("=" * 84)
    print("持有期口径对比：建议仓位/趋势分 与 未来N日收益 的 RankIC")
    print("=" * 84)
    print(f"{'持有期':<10}{'position IC':>14}{'trend_score IC':>16}{'样本':>8}{'显著性(2xSE)':>14}")
    for h in (1, 3, 5):
        sub = r.dropna(subset=[f'r{h}', 'pos'])
        ic_p = sub['pos'].corr(sub[f'r{h}'], method='spearman')
        sub2 = sub.dropna(subset=['ts'])
        ic_t = sub2['ts'].corr(sub2[f'r{h}'], method='spearman')
        se = 1 / np.sqrt(len(sub) - 3)
        sig_p = "显著" if abs(ic_p) > 2 * se else "不显著"
        print(f"未来{h}日    {ic_p:>+13.4f}{ic_t:>+16.4f}{len(sub):>8}{sig_p:>12}")

    print("\n" + "=" * 84)
    print("未来5日收益分层（3基准平均）")
    print("=" * 84)
    def tier(p):
        if p <= 10: return '0-10% 空仓/试探'
        if p <= 30: return '15-30% 轻仓'
        if p < 50:  return '35-45% 中仓'
        return '>=50% 重仓'
    r5 = r.dropna(subset=['r5']).copy()
    r5['tier'] = r5['pos'].apply(tier)
    print(f"{'仓位档':<16}{'样本':>6}{'未来5日均收益':>14}{'胜率':>8}")
    for t in ['0-10% 空仓/试探', '15-30% 轻仓', '35-45% 中仓', '>=50% 重仓']:
        sub = r5[r5['tier'] == t]
        if len(sub) == 0: continue
        print(f"{t:<16}{len(sub):>6}{sub['r5'].mean():>13.3f}%{(sub['r5'] > 0).mean() * 100:>7.1f}%")

    print("\n" + "=" * 84)
    print("分市场环境：按未来5日中证2000涨跌划分，看各仓位档表现")
    print("=" * 84)
    zz = r[r['bm'] == '中证2000'].dropna(subset=['r5']).copy()
    zz['env'] = pd.cut(zz['r5'], [-100, -3, 3, 100], labels=['下行段(r5<-3%)', '震荡段', '上行段(r5>+3%)'])
    print(f"{'环境':<18}{'天数':>6}{'均仓位':>8}{'未来5日均收益':>14}{'高仓位档收益':>14}{'低仓位档收益':>14}")
    for env, sub in zz.groupby('env', observed=True):
        hi = sub[sub['pos'] >= 50]['r5']
        lo = sub[sub['pos'] <= 10]['r5']
        print(f"{str(env):<18}{len(sub):>6}{sub['pos'].mean():>8.1f}{sub['r5'].mean():>13.3f}%"
              f"{(hi.mean() if len(hi) else float('nan')):>13.3f}%{(lo.mean() if len(lo) else float('nan')):>13.3f}%")

    print("\n各环境下高仓位 vs 低仓位差值（>0 说明进攻档选对了环境）:")
    for env, sub in zz.groupby('env', observed=True):
        hi = sub[sub['pos'] >= 50]['r5']; lo = sub[sub['pos'] <= 10]['r5']
        if len(hi) and len(lo):
            print(f"  {env}: 高仓位 {hi.mean():+.3f}% vs 低仓位 {lo.mean():+.3f}%  差 {hi.mean() - lo.mean():+.3f}%")

    print("\n" + "=" * 84)
    print("上行段细节：如果市场进入上行，建议仓位爬坡速度是否跟得上？")
    print("=" * 84)
    up = zz[zz['r5'] > 3]
    if len(up):
        print(f"上行段共 {len(up)} 天，平均建议仓位 {up['pos'].mean():.1f}%，"
              f"其中 >=50% 的天数 {len(up[up['pos'] >= 50])} 天，<=10% 的天数 {len(up[up['pos'] <= 10])} 天")

if __name__ == '__main__':
    main()
