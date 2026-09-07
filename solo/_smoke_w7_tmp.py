# -*- coding: utf-8 -*-
"""确认宽表 ma_bfq_* 基于哪一价格序列（close/close_qfq/close_hfq）满窗滚动"""
import sqlite3

import pandas as pd

DB = r'D:\mystock\cache_daily\stock_data.db'


def read_old(code):
    c = sqlite3.connect(DB, timeout=60)
    df = pd.read_sql_query(
        'SELECT trade_date, close, close_qfq, close_hfq, adj_factor, '
        'ma_bfq_10, ma_bfq_20, ma_bfq_60, ma_qfq_10, ma_qfq_20, ma_qfq_60 '
        'FROM stk_factor_pro WHERE ts_code=? ORDER BY trade_date',
        c, params=(code,))
    c.close()
    return df


for code in ['300308.SZ', '000001.SZ']:
    df = read_old(code)
    print(f'--- {code} 行数={len(df)} 区间 {df.trade_date.iloc[0]}~{df.trade_date.iloc[-1]} ---', flush=True)
    s20 = df['close'].astype(float).rolling(20, min_periods=20).mean()
    d = (df['ma_bfq_20'].astype(float) - s20).abs()
    k = d.nlargest(5).index
    print(df.loc[k, ['trade_date', 'close', 'close_qfq', 'adj_factor', 'ma_bfq_20', 'ma_qfq_20']].to_string(), flush=True)
    for base, label in (('close', 'bfq'), ('close_qfq', 'qfq'), ('close_hfq', 'hfq')):
        s = df[base].astype(float)
        for w in (10, 20, 60):
            col_ma = f'ma_{label}_{w}'
            if col_ma not in df.columns:
                continue
            ref = df[col_ma].astype(float)
            calc = s.rolling(w, min_periods=w).mean()
            start = max(int(len(ref) * 0.2), w)
            tail_ref = ref.iloc[start:]
            tail_calc = calc.iloc[start:]
            m = tail_ref.notna() & tail_calc.notna()
            if m.any():
                dd = (tail_ref[m] - tail_calc[m]).abs()
                print(f'  {base:9s}->{col_ma:10s} 尾段 maxdiff={dd.max():.6g} 样本={int(m.sum())}', flush=True)
    # 样例行
    row = df.iloc[-1]
    print('  末行 close=%.3f close_qfq=%.3f close_hfq=%.3f ma_bfq_20=%.4f ma_qfq_20=%.4f' % (
        row.close, row.close_qfq, row.close_hfq, row.ma_bfq_20, row.ma_qfq_20), flush=True)
print('DONE', flush=True)
