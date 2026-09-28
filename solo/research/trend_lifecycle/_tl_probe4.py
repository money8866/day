# -*- coding: utf-8 -*-
"""TL-01 探针 4：名称变更（时点 ST）/ 停牌 / 涨跌停 / 指数替代方案"""
import os
import glob
import numpy as np
import pandas as pd

FS = r'd:\mystock\solo\research\fundamental_surprise_alpha\data'
CD = r'D:\mystock\cache_daily'
PD = os.path.join(CD, 'parquet')

print('[1] treasure_namechg', flush=True)
f = glob.glob(os.path.join(CD, 'treasure_namechg_*.parquet')) + \
    glob.glob(os.path.join(PD, 'treasure_namechg_*.parquet'))
print('  文件数=%d 例:%s' % (len(f), [os.path.basename(x) for x in f[:3]]), flush=True)
if f:
    d = pd.read_parquet(f[0])
    print('  shape=%s cols=%s' % (d.shape, list(d.columns)), flush=True)
    print(d.head(3).to_string(), flush=True)

print('[2] price_panel 停牌/涨跌停统计（2018-2026 全量）', flush=True)
px = pd.read_parquet(os.path.join(FS, 'price_panel.parquet'),
                     columns=['ts_code', 'trade_date', 'open', 'close', 'pre_close',
                              'vol', 'qfq_close'])
px['trade_date'] = px['trade_date'].astype(str)
print('  行=%d' % len(px), flush=True)
sus = (px['vol'].isna() | (px['vol'] <= 0))
print('  停牌(vol<=0) 比例 %.5f' % sus.mean(), flush=True)
lu = px['open'] >= px['pre_close'] * 1.095
ld = px['open'] <= px['pre_close'] * 0.905
print('  开盘涨停 比例 %.5f ; 开盘跌停 比例 %.5f' % (lu.mean(), ld.mean()), flush=True)
print('  qfq_close 缺失比例 %.6f' % px['qfq_close'].isna().mean(), flush=True)

print('[3] 自建全A等权指数（替代 000001.SH 全历史缺失）', flush=True)
px['r'] = px['qfq_close'].groupby(px['ts_code']).pct_change()
px.loc[px['r'].abs() > 0.5, 'r'] = np.nan
d = px.dropna(subset=['r']).groupby('trade_date')['r'].mean()
print('  天数=%d  %s ~ %s' % (len(d), d.index.min(), d.index.max()), flush=True)
cum = (1 + d).cumprod()
print('  自建等权指数累计 %.3f ' % cum.iloc[-1], flush=True)
for y in ('2019', '2020', '2021', '2022', '2023', '2024', '2025'):
    s = d[d.index.str[:4] == y]
    print('   %s 交易日=%d 年化收益=%.4f' % (y, len(s), (1 + s).prod() - 1), flush=True)

print('[4] 沪深300 与自建等权相关性', flush=True)
idx = pd.read_parquet(os.path.join(FS, 'index_panel.parquet'))
idx['trade_date'] = idx['trade_date'].astype(str)
idx = idx[idx['ts_code'] == '000300.SH'].sort_values('trade_date')
ir = idx.set_index('trade_date')['close'].pct_change()
m = pd.concat([ir.rename('hs300'), d.rename('ewa')], axis=1).dropna()
print('  corr=%.4f  n=%d' % (m['hs300'].corr(m['ewa']), len(m)), flush=True)
print('  000300 年化(全期)=%.4f' % ((1 + m['hs300']).prod() ** (252 / len(m)) - 1), flush=True)
