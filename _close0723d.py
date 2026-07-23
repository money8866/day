# -*- coding: utf-8 -*-
import tushare as ts
ts.set_token('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')

# ETF间接获取指数
etf_map = {
    '510300': '沪深300',  # 华泰柏瑞沪深300ETF
    '159915': '创业板指',  # 易方达创业板ETF
    '512880': '证券ETF',  # 券商
    '512660': '军工ETF',  # 军工
    '515050': '通信ETF',
    '512760': '芯片ETF',
    '512480': '半导体ETF',
    '159611': '电力ETF',
    '159516': '半导设备ETF',
    '159865': '养殖ETF',
    '563300': '中证2000ETF',
}

print('=== 实时ETF（间接反映指数）===')
for code, name in etf_map.items():
    df = ts.get_realtime_quotes(code)
    if df is not None and not df.empty:
        price = float(df.iloc[0]['price'])
        pre = float(df.iloc[0]['pre_close'])
        pct = (price - pre) / pre * 100
        print(f'{name}({code}): {price:.3f}  {pct:+.2f}%')

# 尝试巨潮/东财
print()
print('=== 东财指数 ===')
try:
    import requests
    # 东财指数实时
    url = 'https://push2.eastmoney.com/api/qt/stock/get'
    params = {
        'secid': '1.000001,0.399001,0.399006,1.000300,1.000905,1.000852',
        'fields': 'f43,f44,f45,f46,f47,f48,f57,f58',
        'ut': 'fa5fd1943c7b386f172d6893dbbd5d2d',
    }
    r = requests.get(url, params=params, timeout=5)
    data = r.json()
    for item in data.get('data', {}).get('data', []):
        name = item.get('f58', '')
        close = item.get('f43', 0) / 100
        pct = item.get('f43', 0)  # already pct
        pct_chg = item.get('f4', 0) / 100 if item.get('f4') else 0
        print(f'{name}: {close}  {pct_chg:+.2f}%')
except Exception as e:
    print(f'东财: {e}')

# 用pytdx
print()
print('=== pytdx指数 ===')
try:
    from pytdx.hq import TdxHq_API
    api = TdxHq_API(heartbeat=False)
    api.connect('123.125.108.14', 7709)
    # 上证(1,000001) 深证(0,399001) 创业板(0,399006) 沪深300(0,399300)
    for market, code, name in [(1,'000001','上证指数'), (0,'399001','深证成指'), (0,'399006','创业板指'), (0,'399300','沪深300'), (0,'399905','中证500'), (0,'399852','中证1000')]:
        try:
            df = api.get_security_bars(9, market, code, 0, 1)  # 日线
            if df is not None and len(df) > 0:
                row = df.iloc[-1]
                close = row['close']
                open_ = row['open']
                high = row['high']
                low = row['low']
                pct = (close - row.get('pre_close', close)) / row.get('pre_close', close) * 100 if row.get('pre_close') else 0
                print(f'{name}: {close:.2f}  {pct:+.2f}%  高:{high:.2f} 低:{low:.2f}')
        except Exception as e2:
            print(f'{name}: pytdx error - {e2}')
    api.disconnect()
except Exception as e:
    print(f'pytdx: {e}')
