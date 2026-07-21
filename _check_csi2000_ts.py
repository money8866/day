# -*- coding: utf-8 -*-
"""Tushare旧版实时行情获取中证2000"""
import tushare as ts
ts.set_token('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')

codes = ['sh932000', 'sh000001', 'sz399006', 'sh000300', 'sh000905', 'sh000852']
print("获取实时行情...")
df = ts.get_realtime_quotes(codes)
print()

for _, r in df.iterrows():
    name = r['name']
    code = r['code']
    now = float(r['price'])
    prev_close = float(r['pre_close'])
    open_p = float(r['open'])
    high = float(r['high'])
    low = float(r['low'])
    chg = (now - prev_close) / prev_close * 100
    chg_open = (now - open_p) / open_p * 100
    amp = (high - low) / prev_close * 100
    reb = (now - low) / low * 100
    vol = float(r['volume']) / 10000  # 手转万手
    amount = float(r['amount']) / 1e8  # 元转亿
    date = r['date']
    time_str = r['time']
    
    print(f"{name} ({code})  {date} {time_str}")
    print(f"  现价: {now:.2f}  涨跌: {chg:+.2f}%")
    print(f"  今开: {open_p:.2f}  最高: {high:.2f}  最低: {low:.2f}")
    print(f"  较开盘: {chg_open:+.2f}%  振幅: {amp:.2f}%  从最低反弹: {reb:+.2f}%")
    print(f"  成交量: {vol:.0f}万手  成交额: {amount:.1f}亿")
    print()

# 中证2000分析
csi = df[df['code'] == 'sh932000']
if not csi.empty:
    r = csi.iloc[0]
    now = float(r['price'])
    prev_close = float(r['pre_close'])
    low = float(r['low'])
    high = float(r['high'])
    open_p = float(r['open'])
    chg = (now - prev_close) / prev_close * 100
    reb = (now - low) / low * 100
    pullback = (high - now) / high * 100  # 从高点回落
    
    print("=" * 60)
    print("中证2000反弹力度评估:")
    print(f"  昨日收盘: {prev_close:.2f}")
    print(f"  今日最低: {low:.2f}  -> 跌幅最深 {(low-prev_close)/prev_close*100:+.2f}%")
    print(f"  当前价格: {now:.2f}")
    print(f"  反弹力度: 从最低 {reb:+.2f}%")
    print(f"  收复失地: {(now-prev_close)/prev_close*100:+.2f}% (相对昨收)")
    if now > prev_close:
        print(f"  ✅ 已收复昨日失地，反弹有效")
    else:
        print(f"  ⚠️ 仍低于昨收 {(now-prev_close)/prev_close*100:.2f}%，弱反弹")
    print(f"  从高点回落: {pullback:.2f}% (若>2%说明冲高回落)")
