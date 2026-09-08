import stock_cache as sc

df = sc.cached_stk_factor_compat('000001.SZ', '20250601', '20250630', silent=False)
if df is None:
    print('FAIL: df is None')
else:
    print('shape:', df.shape)
    print('date range:', df['trade_date'].iloc[0], '->', df['trade_date'].iloc[-1])
    cols = list(df.columns)
    key_cols = [c for c in ['close', 'close_qfq', 'close_hfq', 'ma_bfq_5', 'ma_qfq_20',
                            'macd_bfq', 'macd_dif_bfq', 'kdj_k_bfq', 'rsi_qfq_6',
                            'atr_bfq_14', 'turnover_rate', 'total_mv', 'adj_factor',
                            'updays'] if c in cols]
    missing_key = [c for c in ['close_qfq', 'ma_bfq_5', 'macd_bfq', 'rsi_qfq_6', 'turnover_rate', 'adj_factor'] if c not in cols]
    print('key cols present:', key_cols)
    print('key cols MISSING:', missing_key)
    print(df[['trade_date'] + [c for c in ['close', 'close_qfq', 'ma_bfq_5', 'macd_bfq', 'turnover_rate'] if c in cols]].tail(3).to_string())
