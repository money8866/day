# -*- coding: utf-8 -*-
"""下蹲「持续性缩量」探针（一次性）：
   评估近3/5日均量相对20日基准的萎缩程度，判断能否剔除光电股份(600184.SH 20260923)
   这类「缩量过快」标的。
   口径: 事件池=_tmp_vsw_cohort.csv; ret=T+1开盘买/持有T+5收盘/盘中-7%止损/含0.25%成本
"""
import sqlite3
import numpy as np
import pandas as pd

DB = r'd:\mystock\cache_daily\stock_data.db'
EV = r'd:\mystock\solo\_tmp_vsw_cohort.csv'


def main():
    ev = pd.read_csv(EV, encoding='utf-8-sig')
    ev['date'] = ev['date'].astype(str)
    print(f'事件池 {len(ev):,} 条, 下蹲 {int(ev["squat"].sum())} 条')

    con = sqlite3.connect(DB)
    d = pd.read_sql("SELECT ts_code,trade_date,vol FROM daily_cache "
                    "WHERE trade_date>='20240101' AND ts_code NOT LIKE '%.BJ'", con)
    con.close()
    d['trade_date'] = d['trade_date'].astype(str)
    d = d.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)
    g = d.groupby('ts_code')['vol']
    d['ma3'] = g.transform(lambda s: s.rolling(3).mean())
    d['ma5'] = g.transform(lambda s: s.rolling(5).mean())
    d['ma10'] = g.transform(lambda s: s.rolling(10).mean())
    d['ma20'] = g.transform(lambda s: s.rolling(20).mean())

    def _streak(s):
        v = s.values.astype(float)
        out = np.zeros(len(v), dtype=int)
        c = 0
        for i in range(1, len(v)):
            c = c + 1 if v[i] < v[i - 1] else 0
            out[i] = c
        return pd.Series(out, index=s.index)

    d['shrink'] = g.transform(_streak)
    d['peak20'] = g.transform(lambda s: s.rolling(20).max())
    d['pvr'] = d['peak20'] / d['vol']
    d['v5v20'] = d['ma5'] / d['ma20']
    d['v3v20'] = d['ma3'] / d['ma20']
    d['v3v10'] = d['ma3'] / d['ma10']
    d['d1v20'] = d['vol'] / d['ma20']
    d['dvol5'] = d['vol'] / g.shift(5) - 1   # 5日量能变化率（缩量速度）

    m = ev.merge(d[['ts_code', 'trade_date', 'v5v20', 'v3v20', 'v3v10', 'd1v20', 'shrink', 'pvr',
                    'dvol5']],
                 left_on=['ts_code', 'date'], right_on=['ts_code', 'trade_date'], how='left')
    print(f'merge 命中 {m["v5v20"].notna().sum():,}/{len(m):,}')
    m['squat'] = m['squat'].astype(str).str.lower().isin(['true', '1'])

    def stat(df, name):
        if len(df) == 0:
            print(f'{name}: n=0')
            return
        r = df['ret']
        print(f'{name}: n={len(df):5d} 胜率={(r > 0).mean() * 100:5.1f}% '
              f'均={r.mean():+6.2f}% 大赢={((r >= 5).mean() * 100):4.1f}% '
              f'止损={((r <= -6.75).mean() * 100):4.1f}%')

    def bucket(df, col, bins, labels, title):
        print(f'--- {title} ---')
        cut = pd.cut(df[col], bins, labels=labels)
        for lb in labels:
            stat(df[cut == lb], f'{lb:>12s}')

    sq = m[m['squat']].dropna(subset=['v5v20'])
    print(f'\n下蹲样本 {len(sq)}')
    stat(sq, '下蹲基准    ')
    bucket(sq, 'v5v20', [0, 0.6, 0.8, 1.0, 99], ['<0.6', '0.6~0.8', '0.8~1.0', '>1.0'],
           '下蹲·近5日均量/20日均量')
    bucket(sq, 'v3v20', [0, 0.6, 0.8, 1.0, 99], ['<0.6', '0.6~0.8', '0.8~1.0', '>1.0'],
           '下蹲·近3日均量/20日均量')
    bucket(sq, 'v3v10', [0, 0.7, 0.9, 1.1, 99], ['<0.7', '0.7~0.9', '0.9~1.1', '>1.1'],
           '下蹲·近3日均量/10日均量')
    bucket(sq, 'd1v20', [0, 0.5, 0.7, 1.0, 99], ['<0.5', '0.5~0.7', '0.7~1.0', '>1.0'],
           '下蹲·当日量/20日均量')
    bucket(sq, 'shrink', [-1, 1, 2, 3, 99], ['0~1', '2', '3', '>=4'], '下蹲·连续缩量天数')
    bucket(sq, 'shrink', [-1, 3, 4, 5, 6, 99], ['0~3', '4', '5', '6', '>=7'],
           '下蹲·连续缩量天数(细化)')
    bucket(sq, 'v3v10', [0, 0.6, 0.7, 0.8, 0.9, 99],
           ['<0.6', '0.6~0.7', '0.7~0.8', '0.8~0.9', '>0.9'], '下蹲·近3日/10日量能(细化)')
    bucket(sq, 'pvr', [0, 1.5, 2.0, 2.5, 3.0, 99],
           ['<1.5', '1.5~2.0', '2.0~2.5', '2.5~3.0', '>3.0'], '下蹲·近20日最高量/当日量(退潮落差)')
    bucket(sq, 'dvol5', [-1, -0.6, -0.45, -0.3, 0, 99],
           ['<-60%', '-60~-45%', '-45~-30%', '-30~0%', '>0%'], '下蹲·5日量能变化率(缩量速度)')
    print('  0923 四只 5日量能变化率: ' + ' | '.join(
        f"{c}={(d[(d['ts_code'] == c) & (d['trade_date'] == '20260923')]['dvol5'].values[0] * 100):+.1f}%"
        for c in ['300300.SZ', '002300.SZ', '600184.SH', '002902.SZ']))

    print('\n--- 0923 下蹲 4 只原始序列（末10日）---')
    for c in ['300300.SZ', '002300.SZ', '600184.SH', '002902.SZ']:
        t = d[(d['ts_code'] == c) & (d['trade_date'] <= '20260923')].tail(10)
        print(f'== {c}')
        print(t[['trade_date', 'vol', 'd1v20', 'v3v20', 'v5v20', 'v3v10', 'shrink', 'pvr']]
              .to_string(index=False))

    print('\n--- 4 只 0924 起实际走势 ---')
    con = sqlite3.connect(DB)
    q = pd.read_sql("SELECT ts_code,trade_date,open,high,low,close,pct_chg FROM daily_cache "
                    "WHERE ts_code IN ('300300.SZ','002300.SZ','600184.SH','002902.SZ') "
                    "AND trade_date>='20260922'", con)
    con.close()
    print(q.sort_values(['ts_code', 'trade_date']).to_string(index=False))

    print('\n--- 交叉：连续缩量天数 × 近3日/20日量能 ---')
    _c = pd.cut(sq['v3v20'], [0, 0.7, 1.0, 99], labels=['<0.7', '0.7~1.0', '>1.0'])
    _s = pd.cut(sq['shrink'], [-1, 1, 3, 99], labels=['0~1', '2~3', '>=4'])
    for _sl in ['0~1', '2~3', '>=4']:
        for _cl in ['<0.7', '0.7~1.0', '>1.0']:
            stat(sq[(_s == _sl) & (_c == _cl)], f'缩量{_sl:>4s} × 量能{_cl:>5s}')

    print('\n--- 全池对照 ---')
    pool = m.dropna(subset=['v5v20'])
    bucket(pool, 'v5v20', [0, 0.6, 0.8, 1.0, 99], ['<0.6', '0.6~0.8', '0.8~1.0', '>1.0'],
           '全池·近5日均量/20日均量')


if __name__ == '__main__':
    main()
