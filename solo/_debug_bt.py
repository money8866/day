import trend_feature_backtest as tb
for code in tb.DEFAULT_STOCKS:
    df = tb.get_stock_data(code, 120)
    if df is not None:
        print(f"{code}: {len(df)}行, {df.iloc[0]['trade_date']} ~ {df.iloc[-1]['trade_date']}")
