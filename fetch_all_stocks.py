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

    # 1. daily data last 60 days
    df = pro.daily(ts_code=ts_code, start_date='20260401', end_date='20260522')
    if len(df) > 0:
        df = df.sort_values('trade_date')
        closes = df['close'].values
        vols = df['vol'].values
        highs = df['high'].values
        lows = df['low'].values

        ma5 = closes[-5:].mean() if len(closes) >= 5 else closes.mean()
        ma10 = closes[-10:].mean() if len(closes) >= 10 else closes.mean()
        ma20 = closes[-20:].mean() if len(closes) >= 20 else closes.mean()
        ma60 = closes[-60:].mean() if len(closes) >= 60 else closes.mean()

        price = closes[-1]
        vol_now = vols[-1]
        vol_avg5 = vols[-5:].mean()

        # 52w high/low
        df52 = pro.daily(ts_code=ts_code, start_date='20250501', end_date='20260522')
        h52 = df52['high'].max() if len(df52) > 0 else price
        l52 = df52['low'].min() if len(df52) > 0 else price

        print(f'CLOSE:{price:.2f}')
        print(f'MA5:{ma5:.2f} MA10:{ma10:.2f} MA20:{ma20:.2f} MA60:{ma60:.2f}')
        print(f'VOL_NOW:{vol_now:.0f} VOL_AVG5:{vol_avg5:.0f} VOL_RATIO:{vol_now/vol_avg5:.2f}')
        print(f'52W_HIGH:{h52:.2f} 52W_LOW:{l52:.2f}')
        print(f'52W_POSITION:{(price-l52)/(h52-l52)*100:.1f}%')

    # 2. turnover rate last 5 days
    df_basic = pro.daily_basic(ts_code=ts_code, start_date='20260516', end_date='20260522')
    for _, row in df_basic.iterrows():
        print(f'TD:{row["trade_date"]} CLOSE:{row["close"]:.2f} TR:{row["turnover_rate"]:.2f} PE:{row["pe_ttm"]:.2f} PB:{row["pb"]:.2f} MKT:{row["total_mv"]/10000:.2f}')

    # 3. quarterly financials last 4 quarters
    df_q = pro.fina_indicator(ts_code=ts_code, start_date='20240101')
    if len(df_q) > 0:
        for _, row in df_q.head(4).iterrows():
            print(f'Q:{row["end_date"]} EPS:{row["eps"]:.4f} ROE:{row["roe"]:.2f} GROSS:{row["grossprofit_margin"]:.2f} NET:{row["netprofit_margin"]:.2f} DEBT:{row["debt_to_assets"]:.2f} NP_YOY:{row["netprofit_yoy"]:.2f} OR_YOY:{row["or_yoy"]:.2f}')