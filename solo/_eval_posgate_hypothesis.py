#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
P0 位置门假设检验（独立样本：2024-01 ~ 2026-10，约 700 个交易日）
假设：距 60 日高点回撤 > 12% 时，反弹是「下跌中继反抽」而非「趋势启动」，不值得追。
检验：位置门触发日 vs 未触发日的未来 5 日收益/胜率差异。
附带检验：双周期动量同向（mom5>0 且 mom20>0）是否有区分度。
"""
import sqlite3
import pandas as pd
import numpy as np
from io import StringIO

CACHE_DB = 'D:/mystock/solo/cache_backbone_tushare/cache.db'
CODES = {'上证指数': '000001.SH', '沪深300': '000300.SH', '中证2000': '932000.CSI'}

def load_full_series():
    """合并 hist_* 表（2024-2025）与 tsc_index_kline 缓存（2026）为完整日线"""
    conn = sqlite3.connect(CACHE_DB)
    cur = conn.cursor()
    cur.execute("SELECT key, data FROM cache_data WHERE key LIKE 'tsc_index_kline%'")
    kline_rows = cur.fetchall()
    hist_tables = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'hist_%'").fetchall()]
    hist_data = {}
    for t in hist_tables:
        code = t.replace('hist_', '').replace('_', '.', 1)
        # hist_932000_CSI -> 932000.CSI；hist_000001_SH -> 000001.SH
        if code.endswith('_SH') or code.endswith('_CSI'):
            base, suffix = code.rsplit('_', 1)
            code = base + '.' + suffix
        df = pd.read_sql_query(f"SELECT trade_date, close, high, low FROM [{t}]", conn)
        hist_data[code] = df
    conn.close()

    series = {}
    for code in ['000001.SH', '000300.SH', '932000.CSI']:
        d = {}
        if code in hist_data:
            h = hist_data[code]
            for _, r in h.iterrows():
                d[str(r['trade_date'])] = (float(r['close']), float(r['high']))
        for key, data in kline_rows:
            if f'ts_code_{code}_' not in key:
                continue
            df = pd.read_csv(StringIO(data))
            last = df.iloc[-1]
            d[str(last['trade_date'])] = (float(last['close']), float(last['high']))
        if d:
            s = pd.DataFrame(
                [(v[0], v[1]) for v in d.values()],
                index=pd.Index(sorted(d.keys()), name='trade_date'),
                columns=['close', 'high'])
            series[code] = s.sort_index()
    return series

def analyze(code_name, s, gate_dd=0.12):
    close = s['close']; high = s['high']
    dates = list(s.index)
    recs = []
    for i in range(60, len(dates) - 5):
        dd = 1 - close.iloc[i] / high.iloc[i - 59:i + 1].max()   # 距60日高点回撤
        mom5 = close.iloc[i] / close.iloc[i - 5] - 1
        mom20 = close.iloc[i] / close.iloc[i - 20] - 1
        r5 = close.iloc[i + 5] / close.iloc[i] - 1
        recs.append({'date': dates[i], 'dd': dd, 'gate': dd > gate_dd,
                     'dual_mom': (mom5 > 0) and (mom20 > 0), 'r5': r5 * 100})
    return pd.DataFrame(recs)

def main():
    series = load_full_series()
    all_recs = []
    for name, code in CODES.items():
        s = series.get(code)
        if s is None:
            print(f"{name}: 无数据")
            continue
        print(f"{name}: {len(s)} 天  {s.index.min()} ~ {s.index.max()}")
        df = analyze(name, s)
        df['bm'] = name
        all_recs.append(df)
    r = pd.concat(all_recs, ignore_index=True)

    print("\n" + "=" * 82)
    print("位置门假设检验（距60日高点回撤>12% → 禁入强趋势）")
    print("=" * 82)
    g = r.groupby('gate')['r5'].agg(['count', 'mean', lambda x: (x > 0).mean() * 100])
    g.columns = ['样本', '未来5日均收益%', '胜率%']
    for idx, row in g.iterrows():
        label = '门触发(回撤>12%)' if idx else '门未触发(回撤<=12%)'
        print(f"{label:<22}{int(row['样本']):>6}{row['未来5日均收益%']:>13.3f}{row['胜率%']:>9.1f}")

    print("\n分年度看（门触发日 vs 未触发日，未来5日均收益%）:")
    r['year'] = r['date'].str[:4]
    for year, sub in r.groupby('year'):
        a = sub[sub['gate']]['r5']; b = sub[~sub['gate']]['r5']
        print(f"  {year}: 触发 {a.mean():+.3f}% (n={len(a)})  vs  未触发 {b.mean():+.3f}% (n={len(b)})  "
              f"差 {a.mean() - b.mean():+.3f}%")

    print("\n" + "=" * 82)
    print("附加检验：双周期动量（mom5>0 且 mom20>0）")
    print("=" * 82)
    g2 = r.groupby('dual_mom')['r5'].agg(['count', 'mean', lambda x: (x > 0).mean() * 100])
    g2.columns = ['样本', '未来5日均收益%', '胜率%']
    for idx, row in g2.iterrows():
        label = '双周期同向' if idx else '非同向'
        print(f"{label:<22}{int(row['样本']):>6}{row['未来5日均收益%']:13.3f}{row['胜率%']:9.1f}")

    print("\n组合检验（位置门 + 双周期动量交叉）:")
    for gate in (True, False):
        for dm in (True, False):
            sub = r[(r['gate'] == gate) & (r['dual_mom'] == dm)]
            if len(sub):
                print(f"  门{'触发' if gate else '未触发'} + 动量{'同向' if dm else '非同向'}: "
                      f"n={len(sub):>4}  未来5日 {sub['r5'].mean():+.3f}%  胜率 {(sub['r5'] > 0).mean() * 100:.1f}%")

    # 统计显著性
    from scipy import stats as st
    a = r[r['gate']]['r5']; b = r[~r['gate']]['r5']
    t, p = st.ttest_ind(a, b, equal_var=False)
    print(f"\n门触发 vs 未触发 t检验: t={t:.3f}, p={p:.4f} "
          f"({'显著' if p < 0.05 else '不显著'})")

if __name__ == '__main__':
    main()
