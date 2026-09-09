import w7_second_wave_engine as w7

reader = w7.CacheReader()
reader.load_fina()
full = reader.bars_sql("000560.SZ", "20260909")
mdates, mvals = reader.market_curve("20260909")
mkt = w7.MarketCtx(mdates, mvals)
dates = [d for d in full.trade_date.astype(str) if "20260819" <= d <= "20260908"]

print("date        state            buy        event    close   pressure  score")
for asof in dates:
    sub = full[full.trade_date <= asof].reset_index(drop=True)
    res = w7.analyze("000560.SZ", "我爱我家", "房地产服务", sub, {}, reader=reader, mkt=mkt,
                     sector_strength={}, sector_growth={})
    if res is None:
        print(f"{asof}  None")
        continue
    print(f"{asof}  {res['state']:<15s} {res['buy']:<10s} {res['event_date']}  "
          f"{res['close']:.2f}     {res['pressure']:.2f}   {res['score']:.0f}")
