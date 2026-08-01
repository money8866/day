import io, sys, json, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import tushare as ts
pro = ts.pro_api('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')

def rsi14(closes):
    if len(closes) < 15: return None
    ds = [closes[i]-closes[i-1] for i in range(1,len(closes))]
    ag = sum(max(d,0) for d in ds[:14])/14
    al = sum(max(-d,0) for d in ds[:14])/14
    for i in range(14, len(ds)):
        ag = (ag*13+max(ds[i],0))/14
        al = (al*13+max(-ds[i],0))/14
    if al == 0: return 100.0
    return round(100-100/(1+ag/al), 1)

# === 1. 指数7-31收盘 + 近10日结构 ===
print("=== [1] 主要指数 7-31收盘 & 近10日结构 ===", flush=True)
idx_map = {
    '000001.SH': '上证指数', '399001.SZ': '深证成指', '399006.SZ': '创业板指',
    '000300.SH': '沪深300', '000852.SH': '中证1000', '000688.SH': '科创50',
}
for ts_code, name in idx_map.items():
    df = pro.index_daily(ts_code=ts_code, start_date='20260715', end_date='20260731')
    if df is None or len(df)==0: 
        print(f"{name}: 无数据"); continue
    df = df.sort_values('trade_date').reset_index(drop=True)
    last = df.iloc[-1]
    closes = df['close'].tolist()
    # 计算盘中振幅（上下引线）
    body = (last['close'] - last['open'])/last['open']*100
    upper = (last['high'] - max(last['open'], last['close']))/last['open']*100
    lower = (min(last['open'], last['close']) - last['low'])/last['open']*100
    amt = last['amount']/1e8
    # 量能对比（vs前5日均量）
    avg_amt5 = df['amount'].iloc[-6:-1].mean()/1e8
    vol_ratio = amt/avg_amt5 if avg_amt5 else 0
    rsi = rsi14(closes)
    # 近5日累计
    c5 = closes[-1]/closes[-6]-1 if len(closes)>=6 else 0
    print(f"\n{name}: 收盘{last['close']:.2f} 涨{last['pct_chg']:+.2f}%", flush=True)
    print(f"  开{last['open']:.2f} 高{last['high']:.2f} 低{last['low']:.2f}", flush=True)
    print(f"  实体{body:+.2f}% 上影{upper:.2f}% 下影{lower:.2f}%", flush=True)
    print(f"  成交额{amt:.0f}亿 (5日均量{avg_amt5:.0f}亿, 量比{vol_ratio:.2f}x)", flush=True)
    print(f"  RSI14={rsi}  近5日{ c5*100:+.1f}%", flush=True)

# === 2. 半导体ETF 159516 + 512480 7-31K线 ===
print("\n=== [2] 半导体ETF 7-31 K线 ===", flush=True)
for ts_code, nm in [('159516.SZ','半导体材料设备ETF'),('512480.SH','半导体ETF'),('588000.SH','科创50ETF'),('159915.SZ','创业板ETF')]:
    df = pro.fund_daily(ts_code=ts_code, start_date='20260715', end_date='20260731')
    if df is None or len(df)==0:
        print(f"{nm}: 无数据"); continue
    df = df.sort_values('trade_date').reset_index(drop=True)
    last = df.iloc[-1]
    body = (last['close'] - last['open'])/last['open']*100
    upper = (last['high'] - max(last['open'], last['close']))/last['open']*100
    lower = (min(last['open'], last['close']) - last['low'])/last['open']*100
    amt = last['amount']/1e8
    avg_amt5 = df['amount'].iloc[-6:-1].mean()/1e8
    vol_ratio = amt/avg_amt5 if avg_amt5 else 0
    print(f"{nm}: 收{last['close']:.3f} 涨{last['pct_chg']:+.2f}% 量比{vol_ratio:.2f}x", flush=True)
    print(f"  开{last['open']:.3f} 高{last['high']:.3f} 低{last['low']:.3f} 实体{body:+.2f}% 上影{upper:.2f}% 下影{lower:.2f}%", flush=True)

# === 3. 159516成分股 7-31收盘 + RSI ===
print("\n=== [3] 159516关键成分股 7-31收盘 & RSI ===", flush=True)
stocks = [
    ('688012','SH','中微公司'),('002371','SZ','北方华创'),('688981','SH','中芯国际'),
    ('688234','SH','天岳先进'),('605358','SH','立昂微'),('688072','SH','拓荆科技'),
    ('688037','SH','芯源微'),('688082','SH','盛美上海'),('688432','SH','有研硅'),
    ('688535','SH','华岭股份'),('603690','SH','至纯科技'),('688126','SH','沪硅产业'),
    ('688361','SH','中科飞测'),('688200','SH','华峰测控'),('300604','SZ','长川科技'),
]
for code, mkt, name in stocks:
    ts_code = f"{code}.{mkt}"
    df = pro.daily(ts_code=ts_code, start_date='20260615', end_date='20260731')
    if df is None or len(df) < 15:
        print(f"{name}: 数据不足"); continue
    df = df.sort_values('trade_date').reset_index(drop=True)
    last = df.iloc[-1]
    closes = df['close'].tolist()
    rsi = rsi14(closes)
    # 距最高
    hi20 = max(df['high'].iloc[-20:]) if len(df)>=20 else last['high']
    from_hi = (last['close']-hi20)/hi20*100
    body = (last['close']-last['open'])/last['open']*100
    print(f"{name:<8} 收{last['close']:>8.2f} 涨{last['pct_chg']:>+6.2f}% 实体{body:>+5.1f}% RSI={rsi:<5} 距20高{from_hi:>+6.1f}%", flush=True)

# === 4. 下跌结构判断 ===
print("\n=== [4] 下跌结构判断（单波 vs 反弹再跌）===")
# 取科创50日线 7月
df = pro.index_daily(ts_code='000688.SH', start_date='20260701', end_date='20260731')
df = df.sort_values('trade_date').reset_index(drop=True)
print("科创50 7月每日收盘:", flush=True)
for _, r in df.iterrows():
    print(f"  {r['trade_date']} 收{r['close']:.2f} 涨{r['pct_chg']:+.2f}%", flush=True)
# 判断是否有中间反弹
# 找最大单日反弹
max_up = df.loc[df['pct_chg'].idxmax()]
print(f"\n最大单日反弹: {max_up['trade_date']} {max_up['pct_chg']:+.2f}%", flush=True)
print(f"7月累计: {df.iloc[-1]['close']/df.iloc[0]['close']-1:+.1%}", flush=True)
