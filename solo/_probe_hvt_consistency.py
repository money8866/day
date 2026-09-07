# -*- coding: utf-8 -*-
"""一次性验证：新 loader（三窄表 JOIN）逐值与旧 stk_factor_pro 口径一致（任务4）

随机抽取 stk_factor_pro 与 daily_basic_cache 都完整的日期，每个日期抽样若干
(ts_code, trade_date)，用新 loader.load() 单日取数与 stk_factor_pro 同 (ts_code,trade_date)
行做 16 列逐值比对，报告不一致的行。
"""
import os
import sys
import sqlite3
import random
import numpy as np
import pandas as pd

DB = r'D:\mystock\cache_daily\stock_data.db'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hvt_bull.data_loader import HvtDataLoader, _TS_COLS

random.seed(20260907)


def conn():
    c = sqlite3.connect(DB, timeout=60.0)
    c.execute('PRAGMA query_only=ON')
    return c


def overlap_dates(limit=8):
    """两个数据源都 >=4000 条的日期（保证口径可比）"""
    with conn() as c:
        cur = c.execute('''
            SELECT b.trade_date, COUNT(*) AS n
            FROM daily_basic_cache b
            JOIN stk_factor_pro s ON s.ts_code=b.ts_code AND s.trade_date=b.trade_date
            GROUP BY b.trade_date HAVING COUNT(*) >= 4000
            ORDER BY b.trade_date DESC LIMIT ?''', (limit,))
        return [r[0] for r in cur.fetchall()]


def stk_row(c, ts_code, trade_date):
    cols = ','.join('"%s"' % x for x in _TS_COLS)
    try:
        df = pd.read_sql(f'SELECT {cols} FROM stk_factor_pro WHERE ts_code=? AND trade_date=?',
                         c, params=(ts_code, trade_date))
    except Exception as e:
        print('  stk cols err', e)
        return None
    return df.iloc[0] if not df.empty else None


def vals_equal(a, b, tol=1e-6):
    """两标量是否一致：双方 NaN/None 视为相等；数值按绝对+相对容差"""
    na = (a is None) or (isinstance(a, float) and np.isnan(a))
    nb = (b is None) or (isinstance(b, float) and np.isnan(b))
    if na and nb:
        return True, 0.0
    if na != nb:
        return False, float('nan')
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return str(a) == str(b), 0.0
    if np.isclose(a, b, rtol=tol, atol=tol, equal_nan=True):
        return True, abs(a - b)
    return False, abs(a - b)


def main():
    loader = HvtDataLoader()
    dates = overlap_dates(12)
    print(f'可比日期 {len(dates)} 个: {dates[:5]} ...')
    if not dates:
        print('!! 无可比日期（回填未到该区间），跳过')
        return

    total, mismatch = 0, []
    for d in dates[:6]:
        with conn() as c:
            codes = pd.read_sql('SELECT ts_code FROM stk_factor_pro WHERE trade_date=?',
                                c, params=(d,))['ts_code'].tolist()
        sample = random.sample(sorted(codes), min(25, len(codes)))
        for code in sample:
            df = loader.load(code, d, d)
            if df is None or df.empty:
                mismatch.append((d, code, 'new_loader 空'))
                continue
            with conn() as c:
                s = stk_row(c, code, d)
            if s is None:
                continue
            nr = df.iloc[0]
            for col in _TS_COLS:
                if col not in s.index:
                    continue
                total += 1
                ok, diff = vals_equal(nr[col], s[col])
                if not ok:
                    mismatch.append((d, code, col, nr[col], s[col]))
    print(f'共比对 {total} 个数值，不一致 {len(mismatch)} 处')
    for m in mismatch[:20]:
        print('  MISMATCH:', m[0], m[1], 'col=', m[2], '| new=', repr(m[3]), '| old=', repr(m[4]))
    # 交叉截面冒烟：query_cross_section 是否可跑、行数合理
    if dates:
        d0 = dates[0]
        xs = loader.query_cross_section(d0)
        print(f'query_cross_section({d0}) -> {0 if xs is None else len(xs)} 行 '
              f'(columns={list(xs.columns) if xs is not None else None})')
    print('DONE')


if __name__ == '__main__':
    main()
