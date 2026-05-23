# -*- coding: utf-8 -*-
import tushare as ts
import os
os.environ['PYTHONIOENCODING'] = 'utf-8'

ts.set_token('bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d')
pro = ts.pro_api()

stocks = [
    ('688551.SH', 'KEWEIER'),
    ('301183.SZ', 'DONGTIANWEI'),
    ('002222.SZ', 'FUJING'),
    ('002428.SZ', 'YUNNANGE'),
    ('002975.SZ', 'BOJIE'),
]

for ts_code, name in stocks:
    print(f'\n=== {name} ({ts_code}) ===')

    # get last 60 days daily data for technical
    df_daily = pro.daily(ts_code=ts_code, start_date='20260401', end_date='20260522')
    if len(df_daily) > 0:
        df_daily = df_daily.sort_values('trade_date')
        closes = df_daily['close'].values
        highs = df_daily['high'].values
        lows = df_daily['low'].values
        vols = df_daily['vol'].values

        ma5 = closes[-5:] if len(closes) >= 5 else closes
        ma10 = closes[-10:] if len(closes) >= 10 else closes
        ma20 = closes[-20:] if len(closes) >= 20 else closes

        price_now = closes[-1]
        vol_now = vols[-1]
        vol_avg5 = vols[-5:].mean() if len(vols) >= 5 else vols.mean()

        print(f'CLOSE:{price_now:.2f}')
        print(f'MA5:{ma5.mean():.2f}')
        print(f'MA10:{ma10.mean():.2f}')
        print(f'MA20:{ma20.mean():.2f}')
        print(f'VOL:{vol_now:.0f}')
        print(f'VOL_AVG5:{vol_avg5:.0f}')
        print(f'VOL_RATIO:{vol_now/vol_avg5:.2f}')

        # 52w high/low
        df_52w = pro.daily(ts_code=ts_code, start_date='20250501', end_date='20260522')
        if len(df_52w) > 0:
            high52 = df_52w['high'].max()
            low52 = df_52w['low'].min()
            print(f'52W_HIGH:{high52:.2f}')
            print(f'52W_LOW:{low52:.2f}')

        # turnover rate
        df_basic = pro.daily_basic(ts_code=ts_code, start_date='20260501', end_date='20260522')
        if len(df_basic) > 0:
            for _, row in df_basic.iterrows():
                print(f'TRADE_DATE:{row["trade_date"]} CLOSE:{row["close"]:.2f} TURNOVER:{row["turnover_rate"]:.2f}')

        # recent news count via concept
        df_hs = pro.hs_history(ts_code=ts_code, start_date='20260501', end_date='20260522')
        if len(df_hs) > 0:
            print(f'HIGH52W_FROM_52W:{high52}')

    # fundamentals - quarterly
    df_q = pro.fina_indicator(ts_code=ts_code, start_date='20240101')
    if len(df_q) > 0:
        for i, row in df_q.head(4).iterrows():
            print(f'Q_END:{row["end_date"]} EPS:{row["eps"]:.4f} ROE:{row["roe"]:.2f} GROSS:{row["grossprofit_margin"]:.2f} NET:{row["netprofit_margin"]:.2f} DEBT:{row["debt_to_assets"]:.2f} NP_YOY:{row["netprofit_yoy"]:.2f}')

    # valuation today
    df_qt = pro.daily_basic(ts_code=ts_code, trade_date='20260522')
    if len(df_qt) > 0:
        r = df_qt.iloc[0]
        print(f'QT_CLOSE:{r["close"]:.2f} PE:{r["pe_ttm"]:.2f} PB:{r["pb"]:.2f} PS:{r["ps_ttm"]:.2f} MKT_CAP:{r["total_mv"]/10000:.2f} CIRC_MV:{r["circ_mv"]/10000:.2f}')