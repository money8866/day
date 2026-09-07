# -*- coding: utf-8 -*-
"""一次性对照运行：同一 run_daily 代码 + 两种 loader（任务5）

usage:
    python _hvt_run_loader.py old   -> 旧 loader(stk_factor_pro) 跑 20260904
    python _hvt_run_loader.py new   -> 新 loader(三窄表 JOIN) 跑 20260904
输出写到 report_daily/hvt_bull_20260904.{json,md}，由调用方另存对比。
"""
import os
import sys
import shutil
import argparse

BASE_DIR = r'd:\mystock\solo'
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import pandas as pd
print('[DRIVER] pandas 导入完成', flush=True)
import hvt_bull.daily as D
print('[DRIVER] daily 模块导入完成', flush=True)
from hvt_bull.data_loader import DB_PATH, HvtDataLoader, _TS_COLS, _NUM_COLS


class HvtDataLoaderOld(HvtDataLoader):
    """旧实现：直读 stk_factor_pro（SQL 与 numeric 强转均复刻 git HEAD）"""

    def load(self, ts_code, start_date, end_date):
        key = (ts_code, start_date, end_date)
        if key in self._cache:
            return self._cache[key]
        sql = ("SELECT {cols} FROM stk_factor_pro WHERE ts_code=? "
               "AND trade_date>=? AND trade_date<=? ORDER BY trade_date"
               ).format(cols=','.join(_TS_COLS))
        df = self._read(sql, (ts_code, start_date, end_date))
        if df is None or df.empty:
            self._cache[key] = None
            return None
        df = df.dropna(subset=['close', 'vol'])
        for c in _NUM_COLS:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors='coerce')
        df = df.reset_index(drop=True)
        self._cache[key] = df
        return df

    def query_cross_section(self, trade_date, fields=('ts_code', 'turnover_rate', 'amount', 'total_mv')):
        cols = ','.join(fields)
        sql = f"SELECT {cols} FROM stk_factor_pro WHERE trade_date=?"
        df = self._read(sql, (trade_date,))
        return df if df is not None else pd.DataFrame(columns=list(fields))

    def trade_dates(self, start_date, end_date):
        sql = ("SELECT DISTINCT trade_date FROM stk_factor_pro "
               "WHERE trade_date>=? AND trade_date<=? AND ts_code='000001.SZ' "
               "ORDER BY trade_date")
        df = self._read(sql, (start_date or '', end_date))
        return df['trade_date'].tolist() if df is not None and not df.empty else []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('loader', choices=('old', 'new'))
    ap.add_argument('--date', default='20260904')
    args = ap.parse_args()

    if args.loader == 'old':
        D.HvtDataLoader = HvtDataLoaderOld
        print('[DRIVER] 使用旧 loader（stk_factor_pro）')
    else:
        D.HvtDataLoader = HvtDataLoader
        print('[DRIVER] 使用新 loader（daily_cache+daily_basic_cache+adj_factor_cache）')

    # 清掉模块级导入缓存的旧类引用（engine 等只消费 df，不受影响）
    try:
        res = D.run_daily(trade_date=args.date)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[DRIVER] 运行异常: {e}", flush=True)
        raise
    print(f"[DRIVER] 完成 loader={args.loader}: universe={res.get('universe')} n_events={res.get('n_events')}")


if __name__ == '__main__':
    main()
