# -*- coding: utf-8 -*-
"""起量台阶锚定池扫描（一次性研究）：
   验证「起量日 T0（5日均量跃升）→ 台阶维持 → 价格未透支」这一结构是否具备独立 alpha。
   买入口径: T+1 开盘买 / 持有 T+5 收盘 / 盘中 -7% 止损 / 含 0.25% 成本
   无未来函数: 判定第 j 日事件时只使用 <= j 的数据
"""
import sqlite3
import numpy as np
import pandas as pd

DB = r'd:\mystock\cache_daily\stock_data.db'
START, END = '20250101', '20260924'
HOLD, STOP, COST = 5, -7.0, 0.25
RATIO_JUMP = 1.5      # 起量: 5日均量/前5日均量
VOL20_MIN = 2.0       # 起量: 当日量/20日均量
KEEP_MIN = 1.0        # 台阶维持: 起量后 ratio 下限
LO, HI = -0.15, 0.15  # 价格未透支区间(相对起量日收盘)
MAX_D = 15


def ret_of(o, c, l, i):
    if i + 1 >= len(c):
        return None
    buy = float(o[i + 1])
    if buy <= 0:
        return None
    end = i + 1 + HOLD
    if end >= len(c):
        return None
    for j in range(i + 2, end + 1):
        if l[j] / buy - 1 <= STOP / 100.0:
            return STOP - COST
    return (c[end] / buy - 1) * 100 - COST


def stat(rows, name):
    if not rows:
        print(f'{name}: n=0')
        return
    r = np.array([x['ret'] for x in rows])
    print(f'{name}: n={len(r):6d} 胜率={(r > 0).mean() * 100:5.1f}% 均={r.mean():+6.2f}% '
          f'大赢={((r >= 5).mean() * 100):4.1f}% 止损={((r <= -6.75).mean() * 100):4.1f}%')


def main():
    con = sqlite3.connect(DB)
    d = pd.read_sql("SELECT ts_code,trade_date,open,high,low,close,vol FROM daily_cache "
                    "WHERE trade_date>='20240401' AND ts_code NOT LIKE '%.BJ'", con)
    con.close()
    d = d[~d['ts_code'].str.startswith(('8', '4', '9'))]
    d['trade_date'] = d['trade_date'].astype(str)
    d = d.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)
    g = d.groupby('ts_code')['vol']
    d['ma5'] = g.transform(lambda s: s.rolling(5).mean())
    d['ma5p'] = g.transform(lambda s: s.rolling(5).mean().shift(5))
    d['ma20'] = g.transform(lambda s: s.rolling(20).mean())
    d['ratio'] = d['ma5'] / d['ma5p']
    d['v20r'] = d['vol'] / d['ma20']

    base, keep_rows, keep_px_rows = [], [], []
    n_anchor = 0
    for code, gg in d.groupby('ts_code', sort=False):
        if len(gg) < 120:
            continue
        ratio = gg['ratio'].values
        v20r = gg['v20r'].values
        o = gg['open'].values.astype(float)
        c = gg['close'].values.astype(float)
        h = gg['high'].values.astype(float)
        l = gg['low'].values.astype(float)
        dates = gg['trade_date'].values
        prev = np.concatenate([[np.nan], ratio[:-1]])
        anc = np.where((ratio >= RATIO_JUMP) & (prev < RATIO_JUMP) & (v20r >= VOL20_MIN))[0]
        for i in anc:
            n_anchor += 1
            for dd in range(1, MAX_D + 1):
                j = i + dd
                if j + 1 + HOLD >= len(c):
                    break
                if dates[j] < START or dates[j] > END:
                    continue
                seg = ratio[i + 1:j + 1]
                if np.isnan(seg).any():
                    continue
                r = ret_of(o, c, l, j)
                if r is None:
                    continue
                rec = {'code': code, 'date': dates[j], 'ret': r, 'd': dd}
                base.append(rec)
                if seg.min() < KEEP_MIN:
                    continue
                keep_rows.append(rec)
                px = c[j] / c[i] - 1
                rec2 = dict(rec, px=px, keep_days=int((seg >= KEEP_MIN).sum()))
                if LO <= px <= HI:
                    keep_px_rows.append(rec2)

    print(f'锚点数 {n_anchor:,}')
    stat(base, 'A 起量后任意日(无条件)   ')
    stat(keep_rows, 'B +台阶维持(ratio>=1.0)  ')
    stat(keep_px_rows, 'C +价格未透支[-15%,+15%] ')

    print('\n--- C 按起量后第 d 日 ---')
    for dd in range(1, MAX_D + 1):
        stat([x for x in keep_px_rows if x['d'] == dd], f'd={dd:2d}')

    print('\n--- C 按价格区间 ---')
    for lo, hi, nm in [(-0.15, -0.05, '  -15~-5%'), (-0.05, 0.05, '   -5~+5%'),
                       (0.05, 0.15, '   +5~+15%')]:
        stat([x for x in keep_px_rows if lo <= x['px'] < hi], nm)

    print('\n--- C 按台阶维持天数 ---')
    for lo, hi, nm in [(1, 5, '  1~4天'), (5, 10, '  5~9天'), (10, 999, '  >=10天')]:
        stat([x for x in keep_px_rows if lo <= x['keep_days'] < hi], nm)

    print('\n--- C 海峡创新(300300.SZ) 命中明细 ---')
    for x in sorted([x for x in keep_px_rows if x['code'] == '300300.SZ'], key=lambda z: z['date']):
        print(f"  {x['date']} d={x['d']:2d} px={x['px'] * 100:+6.1f}% 维持{x['keep_days']:2d}天 ret={x['ret']:+.2f}%")


if __name__ == '__main__':
    main()
