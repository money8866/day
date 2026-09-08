"""Debug: Compare CSV vs DB data"""
import pandas as pd

import stock_cache as sc

# Load CSV
csv_path = r'D:\mystock\solo\trend_feature_output\entry_precision_20260630_204617_qualified.csv'
df_csv = pd.read_csv(csv_path, encoding='utf-8-sig')
print(f"CSV signals: {len(df_csv)}")
print()

for _, row in df_csv.head(3).iterrows():
    code = row['ts_code']
    cols = ('close', 'pct_chg', 'volume_ratio',
            'ma_bfq_5', 'ma_bfq_10', 'ma_bfq_20', 'ma_bfq_60',
            'rsi_bfq_6', 'macd_dif_bfq', 'macd_dea_bfq')
    df = sc.fetch_hist_range(None, None, ts_codes=[code], cols=cols)
    if df.empty:
        continue
    df = df.sort_values('trade_date', ascending=False).head(3)

    print(f"{code}:")
    for _, r in df.iterrows():
        trade_date = r['trade_date']
        close, pct_chg, vol_ratio = r['close'], r['pct_chg'], r['volume_ratio']
        ma5, ma10, ma20, ma60 = r['ma_bfq_5'], r['ma_bfq_10'], r['ma_bfq_20'], r['ma_bfq_60']
        rsi6, dif, dea = r['rsi_bfq_6'], r['macd_dif_bfq'], r['macd_dea_bfq']

        if ma20 and ma20 > 0:
            above_ma20 = (close - ma20) / ma20 * 100
        else:
            above_ma20 = 0

        print(f"  {trade_date} close={close:.2f} pct={pct_chg} vol={vol_ratio}")
        print(f"    MA20={ma20} above_MA20={above_ma20:.1f}% RSI6={rsi6} DIF={dif}")

        # Check v8 filters
        ma20_ok = 5 <= above_ma20 <= 18
        vol_ok = 0.8 <= vol_ratio <= 3.0
        pct_ok = 2 <= pct_chg <= 20 if pct_chg else False
        print(f"    [v8] MA20 OK: {ma20_ok}, Vol OK: {vol_ok}, Pct OK: {pct_ok}")
    print()
