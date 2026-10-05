# -*- coding: utf-8 -*-
"""探测：Tushare 是否提供 932000.CSI / 000985.CSI 的 2021 年前历史（用于 MA 预热）"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stock_cache as sc

pro = sc._get_pro()
for code in ('932000.CSI', '000985.CSI', '000852.SH', '000905.SH', '000300.SH'):
    try:
        df = pro.index_daily(ts_code=code, start_date='20180101', end_date='20210103')
        if df is None or df.empty:
            print(f'{code}: 无 2018~2020 数据')
        else:
            print(f'{code}: {len(df)} 行  {df["trade_date"].min()} ~ {df["trade_date"].max()}')
    except Exception as e:
        print(f'{code}: 异常 {e}')
