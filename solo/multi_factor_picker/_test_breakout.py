# -*- coding: utf-8 -*-
"""快速测试60日新高突破检测"""
import os, sys, time
sys.path.insert(0, r'D:\mystock')
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'

import tushare as ts
ts.set_token(os.environ['TUSHARE_TOKEN'])
pro = ts.pro_api()

# 测试5只典型股
codes = ['688629.SH', '688787.SH', '301128.SZ', '688041.SH', '603163.SH']
for code in codes:
    df = pro.stk_factor_pro(ts_code=code, start_date='20260101', end_date='20260623')
    if df is None or len(df) < 60:
        print(code, '数据不足')
        continue
    df = df.sort_values('trade_date').reset_index(drop=True)

    # 找60日新高突破
    for i in range(60, len(df)):
        high_60 = df.iloc[i-60:i]['close'].max()
        if df.iloc[i]['close'] > high_60:
            vol_ratio = df.iloc[i].get('volume_ratio', 1.0)
            rsi = df.iloc[i].get('rsi_qfq_6', 50)
            macd_dif = df.iloc[i].get('macd_dif_qfq', 0)
            macd_dea = df.iloc[i].get('macd_dea_qfq', 0)
            adx = df.iloc[i].get('dmi_adx_qfq', 0)
            print(f'{code} {df.iloc[i]["trade_date"]} 突破60日新高 close={df.iloc[i]["close"]:.2f} high60={high_60:.2f} 量比={vol_ratio:.2f} RSI={rsi:.1f} MACD={"金叉" if macd_dif>macd_dea else "死叉"} ADX={adx:.0f}')
            break
    time.sleep(0.12)
