# -*- coding: utf-8 -*-
"""
HVE-Research V1  Step 1b
重建市场基准：只使用「可投资样本池」，剔除
  - 北交所、ST/退市
  - 上市/样本内前 60 个交易日（含 IPO 首日爆涨）
  - 停牌与数据缺失
输出 ew_ret(等权) / med_ret / amtw_ret(成交额加权) + 指数 + 宽度
"""
import os
import numpy as np
import pandas as pd
import hve_lib as L

DATA = L.DATA


def eligible_mask(p):
    m = (
        (p['board'].fillna('BJ') != 'BJ') &
        (p['is_st'].fillna(0) == 0) & (p['is_delist'].fillna(0) == 0) &
        (p['obs_days'] >= 60) &
        p['ret'].notna() & p['ma20'].notna() & p['amount'].notna()
    )
    return m


def main():
    p = pd.read_parquet(os.path.join(DATA, 'panel.parquet'))
    m = eligible_mask(p)
    d = p[m].copy()
    print('eligible rows', len(d), 'of', len(p))

    g = d.groupby('td_idx')
    mk = g.agg(n_stocks=('ret', 'size'),
               ew_ret=('ret', 'mean'),
               med_ret=('ret', 'median'),
               tot_amount=('amount', 'sum')).reset_index()
    # 注：成交额加权收益在 A 股存在严重同日偏差（上涨日成交更大），不作为基准使用

    ok = d.dropna(subset=['ma20'])
    ok = ok.assign(ab20=(ok['close'] > ok['ma20']).astype(np.float32),
                   ab60=(ok['close'] > ok['ma60']).astype(np.float32))
    br = ok.groupby('td_idx').agg(breadth20=('ab20', 'mean'), breadth60=('ab60', 'mean')).reset_index()
    mk = mk.merge(br, on='td_idx', how='left')
    mk = mk.merge(g['ret'].std().rename('xsec_std').reset_index(), on='td_idx', how='left')
    # 上涨家数占比
    up = d.assign(u=(d['ret'] > 0).astype(np.float32)).groupby('td_idx')['u'].mean().rename('up_ratio').reset_index()
    mk = mk.merge(up, on='td_idx', how='left')
    # 涨停/跌停家数
    lu = d.groupby('td_idx')['up_limit'].sum().rename('n_limitup').reset_index()
    mk = mk.merge(lu, on='td_idx', how='left')

    old = pd.read_parquet(os.path.join(DATA, 'market.parquet'))
    idx_cols = [c for c in old.columns if c.startswith(('close_', 'pct_chg_', 'amount_'))]
    mk = mk.merge(old[['td_idx'] + idx_cols], on='td_idx', how='left')
    cal = pd.read_parquet(os.path.join(DATA, 'calendar.parquet'))
    mk = mk.merge(cal, on='td_idx', how='left')

    print('mean daily ew_ret %.5f  ann %.3f  cum %.3f' % (
        mk['ew_ret'].mean(), (1 + mk['ew_ret'].mean()) ** 243 - 1,
        np.exp(np.log1p(mk['ew_ret'].fillna(0)).sum()) - 1))
    print('mean daily med_ret %.5f  cum %.3f' % (
        mk['med_ret'].mean(), np.exp(np.log1p(mk['med_ret'].fillna(0)).sum()) - 1))
    mk.to_parquet(os.path.join(DATA, 'market.parquet'), index=False)
    print('saved', mk.shape)


if __name__ == '__main__':
    main()
