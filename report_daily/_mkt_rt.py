# -*- coding: utf-8 -*-
import sys, datetime
from pytdx.hq import TdxHq_API

sys.stdout.reconfigure(encoding='utf-8')
api = TdxHq_API(heartbeat=False, auto_retry=True)

connected = False
for ip, port in [('123.125.108.14', 7709), ('218.108.47.77', 7709),
                  ('180.153.18.170', 7709), ('180.153.18.172', 80),
                  ('202.108.253.139', 80)]:
    try:
        if api.connect(ip, int(port)):
            connected = True
            print("Connected: %s:%s" % (ip, port))
            break
    except:
        continue

if not connected:
    print("All servers failed")
    sys.exit(1)

now = datetime.datetime.now()
print("Time: %s\n" % now.strftime('%Y-%m-%d %H:%M:%S'))

# 四大指数 + 中证2000
# 上证=1,000001 / 深证=0,399001 / 创业板=0,399006 / 沪深300=0,399300 / 中证2000=1,932000
codes = [
    (1, '1A0001', '上证指数'),
    (0, '399001', '深证成指'),
    (0, '399006', '创业板指'),
    (0, '399300', '沪深300'),
    (1, '932000', '中证2000'),
    (1, '931025', '中证1000'),
]

print("=== REAL-TIME INDEX QUOTES ===")
results = []
for market, code, name in codes:
    try:
        data = api.get_security_bars(4, market, code, 0, 5)
        if data and len(data) >= 2:
            today_bar = data[-1]
            yesterday_bar = data[-2]
            
            today_close = float(today_bar['close'])
            today_high = float(today_bar['high'])
            today_low = float(today_bar['low'])
            today_open = float(today_bar['open'])
            y_close = float(yesterday_bar['close'])
            
            pct = (today_close - y_close) / y_close * 100
            
            results.append({
                'name': name, 'code': code,
                'close': today_close, 'high': today_high, 'low': today_low,
                'open': today_open, 'pct': pct, 'y_close': y_close
            })
            
            pct_str = '+%.2f' % pct if pct >= 0 else '%.2f' % pct
            print("%s: close=%.2f  %s%%  open=%.2f  H=%.2f  L=%.2f  (yesterday: %.2f)" % (
                name, today_close, pct_str, today_open, today_high, today_low, y_close))
    except Exception as e:
        print("%s: error - %s" % (name, e))

# 涨跌停统计用指数内成分近似
print("\n=== MA TREND ===")
for market, code, name in codes:
    try:
        data = api.get_security_bars(4, market, code, 0, 20)
        if data and len(data) >= 5:
            closes = [float(d['close']) for d in data]
            ma5 = sum(closes[-5:]) / 5
            ma10 = sum(closes[-10:]) / 10
            ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else sum(closes) / len(closes)
            last = closes[-1]
            pct5d = (closes[-1] - closes[-6]) / closes[-6] * 100 if len(closes) >= 6 else 0
            pct10d = (closes[-1] - closes[-11]) / closes[-11] * 100 if len(closes) >= 11 else 0
            
            if last > ma5 > ma10 > ma20:
                trend = 'UPTREND'
            elif last < ma5 < ma10 < ma20:
                trend = 'DOWNTREND'
            else:
                trend = 'FLAT'
            
            print("%s: close=%.2f  MA5=%.2f MA10=%.2f MA20=%.2f  5d=%+.1f%% 10d=%+.1f%% [%s]" % (
                name, last, ma5, ma10, ma20, pct5d, pct10d, trend))
    except Exception as e:
        print("%s MA: error" % name)

# 综合情绪评分
print("\n=== SENTIMENT SCORE ===")
if results:
    sh = next((r for r in results if '上证' in r['name']), None)
    csi2k = next((r for r in results if '2000' in r['name']), None)
    cyb = next((r for r in results if '创业' in r['name']), None)
    hs300 = next((r for r in results if '沪深' in r['name']), None)
    
    score = 50
    parts = []
    
    if sh:
        if sh['pct'] > 0: score += 5
        if sh['pct'] < 0: score -= 5
        parts.append('上证%+d' % sh['pct'])
    
    if csi2k:
        if csi2k['pct'] > 0: score += 5
        if csi2k['pct'] < 0: score -= 8
        if csi2k['pct'] < -1.5: score -= 10
        parts.append('中证2000%+d' % csi2k['pct'])
    
    if cyb:
        if cyb['pct'] > 1: score += 5
        if cyb['pct'] < 0: score -= 5
        parts.append('创业板%+d' % cyb['pct'])
    
    # 指数内小盘股强弱对比
    if csi2k and sh and sh['pct'] != 0:
        rel = csi2k['pct'] - sh['pct']
        if rel > 1: score += 5
        if rel < -1: score -= 5
        parts.append('小盘-大盘差%+d' % rel)
    
    # 趋势因子
    if csi2k:
        ma20_data = api.get_security_bars(4, 1 if '932000' in csi2k['code'] else 0, csi2k['code'], 0, 20)
        if ma20_data and len(ma20_data) >= 5:
            ma5s = sum([float(d['close']) for d in ma20_data[-5:]]) / 5
            ma20s = sum([float(d['close']) for d in ma20_data[-20:]]) / 20 if len(ma20_data) >= 20 else ma5s
            if csi2k['close'] < ma20s: score -= 10  # 均线空头
            if csi2k['close'] < ma5s * 0.95: score -= 5  # 跌破MA5
    
    score = max(0, min(100, score))
    
    if score >= 70: label = 'STRONG_BULL'
    elif score >= 58: label = 'BULLISH'
    elif score >= 45: label = 'NEUTRAL'
    elif score >= 32: label = 'BEARISH'
    else: label = 'STRONG_BEAR'
    
    print("Score: %d/100  [%s]" % (score, label))
    print("Parts: %s" % '  |  '.join(parts))

api.disconnect()
print("\nDone")
