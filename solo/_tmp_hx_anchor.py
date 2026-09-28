# -*- coding: utf-8 -*-
"""海峡创新 300300.SZ 起量结构核对（一次性）：
   核对「20260904 起量 → 量能台阶维持 → 价格未透支 → 0923/0924 出信号」这一描述。
"""
import sqlite3
import pandas as pd

DB = r'd:\mystock\cache_daily\stock_data.db'


def main():
    con = sqlite3.connect(DB)
    d = pd.read_sql("SELECT ts_code,trade_date,open,high,low,close,pct_chg,vol FROM daily_cache "
                    "WHERE ts_code='300300.SZ' AND trade_date>='20260715' ORDER BY trade_date", con)
    con.close()
    d = d.reset_index(drop=True)
    d['ma5'] = d['vol'].rolling(5).mean()
    d['ma5_prev'] = d['ma5'].shift(5)
    d['ratio'] = d['ma5'] / d['ma5_prev'].replace(0, float('nan'))
    d['ma20'] = d['vol'].rolling(20).mean()
    d['v20r'] = d['vol'] / d['ma20']
    d['ma5_c'] = d['close'].rolling(5).mean()
    d['ma5p'] = d['ma5_c'].shift(5)
    d['cr5'] = d['ma5_c'] / d['ma5p'] - 1

    print('== 300300.SZ 原始序列 ==')
    print(d[['trade_date', 'close', 'pct_chg', 'vol', 'ma5', 'ma5_prev', 'ratio', 'v20r', 'cr5']]
          .to_string(index=False, float_format=lambda v: f'{v:,.3f}'))

    t0 = d[d['trade_date'] == '20260904']
    if len(t0):
        c0 = float(t0['close'].values[0])
        print(f'\n起量日 20260904 收盘={c0}')
        for dt, c in [('20260916', None), ('20260922', None), ('20260923', None), ('20260924', None)]:
            r = d[d['trade_date'] == dt]
            if len(r):
                cc = float(r['close'].values[0])
                print(f'  {dt}: close={cc:.2f}  相对0904={cc / c0 - 1:+.1%}')


if __name__ == '__main__':
    main()
