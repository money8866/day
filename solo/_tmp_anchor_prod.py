# -*- coding: utf-8 -*-
"""生产口径预览：0923 / 0924 下蹲标的的「起量台阶」标记（口径一 KEEP_MIN / 口径二 STEP）。"""
import sqlite3
import numpy as np
import pandas as pd

DB = r'd:\mystock\cache_daily\stock_data.db'
RATIO_JUMP, VOL20_MIN, MAX_D = 1.5, 2.0, 15

CASES = [('300300.SZ', '20260923'), ('002300.SZ', '20260923'),
         ('600184.SH', '20260923'), ('002902.SZ', '20260923'),
         ('600237.SH', '20260924'), ('603980.SH', '20260924'),
         ('688209.SH', '20260924')]


def find(vol, close, up_to):
    v = pd.Series(vol[-200:])
    cl = close[-200:]
    ma5 = v.rolling(5).mean().values
    ratio = (v.rolling(5).mean() / v.rolling(5).mean().shift(5)).values
    v20r = (v / v.rolling(20, min_periods=1).mean()).values
    k = len(ratio) - 1 - up_to
    hits = []
    for i in range(k - 1, max(-1, k - 1 - MAX_D), -1):
        if not (ratio[i] >= RATIO_JUMP and not np.isnan(ratio[i - 1]) and ratio[i - 1] < RATIO_JUMP):
            continue
        if v20r[i] < VOL20_MIN or i - 1 < 0 or not (ma5[i - 1] > 0):
            continue
        hits.append(i)
    return ma5, ratio, cl, k, hits


def main():
    con = sqlite3.connect(DB)
    print(f'{"代码":<11}{"日期":<10}{"口径一(KEEP>=1.0)":<22}{"口径二(STEP>=1.2)":<24}{"minSTEP":<10}')
    for code, dt in CASES:
        df = pd.read_sql("SELECT trade_date,close,vol FROM daily_cache WHERE ts_code=? AND trade_date<=? "
                         "ORDER BY trade_date", con, params=(code, dt))
        if len(df) < 60:
            print(f'{code} {dt}: 数据不足')
            continue
        close = df['close'].values.astype(float)
        vol = df['vol'].values.astype(float)
        ma5, ratio, cl, k, hits = find(vol, close, 0)
        c1, c2, sv = '不在', '不在', '-'
        for i in hits:
            if not np.isnan(ratio[i + 1:k + 1]).any() and ratio[i + 1:k + 1].min() >= 1.0:
                c1 = f'在 d={k - i} px={cl[k] / cl[i] - 1:+.1%}'
                break
        for i in hits:
            seg = ma5[i:k + 1] / ma5[i - 1]
            if not np.isnan(seg).any() and seg.min() >= 1.2:
                c2 = f'在 d={k - i} px={cl[k] / cl[i] - 1:+.1%}'
                sv = f'{seg.min():.2f}'
                break
        print(f'{code:<11}{dt:<10}{c1:<22}{c2:<24}{sv:<10}')
    con.close()


if __name__ == '__main__':
    main()
