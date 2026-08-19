# -*- coding: utf-8 -*-
import os, time
tok = ''
for l in open(r'D:\mystock\config\.env', encoding='utf-8'):
    l = l.strip()
    if l.startswith('TUSHARE_TOKEN='):
        tok = l.split('=', 1)[1].strip()
import tushare as ts
ts.set_token(tok); pro = ts.pro_api()
for code, name in [('002284.SZ', '亚太股份'), ('603071.SH', '物产环能'), ('688112.SH', '鼎阳科技'), ('301129.SZ', '瑞纳智能')]:
    d = pro.daily(ts_code=code, start_date='20260801', end_date='20260819')
    if d is None or len(d) == 0:
        print(f'{name} 无数据'); continue
    d = d.sort_values('trade_date')
    db = pro.daily_basic(ts_code=code, start_date='20260801', end_date='20260819',
                         fields='trade_date,turnover_rate,volume_ratio,total_mv')
    db = db.sort_values('trade_date')
    m = db.set_index('trade_date')
    print(f'== {name} ({code}) ==')
    for _, r in d.iterrows():
        b = m.loc[r['trade_date']]
        print(f"  {r['trade_date']} 开{r['open']:.2f} 收{r['close']:.2f} 高{r['high']:.2f} 低{r['low']:.2f} "
              f"涨{r['pct_chg']:+.1f}% 量比{b['volume_ratio']:.1f} 换手{b['turnover_rate']:.1f}% "
              f"市值{b['total_mv']/10000:.0f}亿")
    time.sleep(0.15)
