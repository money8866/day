import tushare as ts
ts.set_token('bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d')
pro = ts.pro_api()
codes = ['512880','512800','512660','512010','512480','512400','512580','512700','512690','512200','512560','510300','510050','159825','159915','159996','159813','159901','159949','159919']
for c in codes:
    market = 'SH' if c.startswith('5') else 'SZ'
    ts_code = c + '.' + market
    df = pro.fund_basic(ts_code=ts_code, market='E')
    if len(df) > 0:
        name = df.iloc[0]['name']
        print(f'{ts_code}  {name}')
    else:
        print(f'{ts_code}  NOT FOUND')
