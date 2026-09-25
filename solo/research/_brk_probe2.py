# -*- coding: utf-8 -*-
"""突破前 Alpha 研究 · 候选池勘探（只读）
建立 resistance(N) 与 distance(N)，报告候选池规模与分布，用于确定研究口径。
"""
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')

COLS = ['ts_code', 'trade_date', 'seq', 'board', 'pct_chg', 'vol', 'amount',
        'a_open', 'a_high', 'a_low', 'a_close', 'limit_price', 'limit_pct', 'is_limit_up']
p = pd.read_parquet(os.path.join(OUT, 'panel.parquet'), columns=COLS)
print('panel:', p.shape, '股票数', p['ts_code'].nunique(),
      '交易日', p['trade_date'].nunique(), p['trade_date'].min(), p['trade_date'].max())

n = len(p)
h = p['a_high'].values.astype(np.float64)
l = p['a_low'].values.astype(np.float64)
c = p['a_close'].values.astype(np.float64)
seq = p['seq'].values
codes = p['ts_code'].values

# 全局 rolling（依赖数据按 ts_code/trade_date 排序；seq>=N 时窗口必落在同股票内）
hs = pd.Series(h)
print('\n=== 压力位与距离 ===')
for N in (10, 20, 40, 60):
    R = hs.rolling(N, min_periods=N).max().shift(1).values
    d = c / R - 1.0
    p['_d%d' % N] = d
    ok = np.isfinite(d)
    print('R%-3d 有效=%d  距离分位: p1=%.3f p5=%.3f p10=%.3f p25=%.3f p50=%.3f p75=%.3f p90=%.3f max=%.3f'
          % (N, ok.sum(), *np.nanpercentile(d, [1, 5, 10, 25, 50, 75, 90]), np.nanmax(d)))

# 基础过滤
basic = (p['seq'].values >= 60) & np.isfinite(p['a_close'].values) & (p['vol'].values > 0)
print('\n基础过滤(seq>=60 & 有效价量):', int(basic.sum()))

# ST / 退市过滤
sb = pd.read_csv(r"D:\mystock\cache_daily\stock_basic.csv", dtype=str)
bad = set(sb.loc[sb['name'].fillna('').str.contains('ST|退', regex=True), 'ts_code'])
st_mask = pd.Series(codes).isin(bad).values
print('ST/退市剔除:', int((basic & st_mask).sum()))
uni = basic & (~st_mask)
print('Universe U =', int(uni.sum()), '  日均 %.0f 只' % (uni.sum() / p['trade_date'].nunique()))

# 每日横截面排名（距离越小 = 越接近压力位）
print('\n=== 候选池规模（按距离分位阈值，越小越近）===')
td, tdidx = np.unique(p['trade_date'].values, return_inverse=True)
print('交易日数', len(td))
for N in (10, 20, 40, 60):
    d = p['_d%d' % N].values
    dd = pd.DataFrame({'td': tdidx, 'd': d, 'u': uni})
    dd = dd[dd['u'] & np.isfinite(dd['d'])]
    # 每个交易日内按距离升序的百分位
    rk = dd.groupby('td')['d'].rank(pct=True)
    line = 'R%-3d ' % N
    for q in (0.05, 0.10, 0.20, 0.30):
        m = int((rk <= q).sum())
        line += ' top%02d%%=%7d(%5.0f/日)' % (q * 100, m, m / len(td))
    print(line)

# union 规模
print('\n=== 多压力位并集规模（各取 top20%%）===')
for q in (0.10, 0.20, 0.30):
    U = np.zeros(n, dtype=bool)
    for N in (10, 20, 40, 60):
        d = p['_d%d' % N].values
        dd = pd.DataFrame({'td': tdidx, 'd': d, 'u': uni, 'i': np.arange(n)})
        dd = dd[dd['u'] & np.isfinite(dd['d'])]
        rk = dd.groupby('td')['d'].rank(pct=True)
        U[dd.loc[rk <= q, 'i'].values] = True
    print('top%02d%% union = %d  (占 U 的 %.1f%%)  日均 %.0f 只'
          % (q * 100, int(U.sum()), 100.0 * U.sum() / uni.sum(), U.sum() / len(td)))

# 流动性 / 市值覆盖
print('\n=== 流动性 ===')
amt = p['amount'].values
for th in (1e4, 2e4, 5e4, 1e5):
    print('amount >= %-8.0f 千元(=%.2f亿): %d' % (th, th / 1e4, int((uni & (amt >= th)).sum())))

print('\n=== daily_basic 覆盖（换手/市值）===')
import sqlite3
conn = sqlite3.connect(r"D:\mystock\cache_daily\stock_data.db", timeout=120)
db = pd.read_sql_query("SELECT ts_code, trade_date, turnover_rate, volume_ratio, total_mv, circ_mv "
                       "FROM daily_basic_cache", conn)
conn.close()
print(db.shape, db['trade_date'].min(), db['trade_date'].max())
k = set(zip(db['ts_code'], db['trade_date'].astype(int)))
print('U 中能被 daily_basic 覆盖的比例: %.1f%%'
      % (100.0 * np.mean([(a, b) in k for a, b in zip(codes[uni], p['trade_date'].values[uni])])))

print('\n=== 是否为涨停日（信号日）占比 ===')
print('U 中 is_limit_up 占比 %.2f%%' % (100.0 * p['is_limit_up'].values[uni].mean()))
