# -*- coding: utf-8 -*-
"""东财API获取中证2000实时/收盘行情"""
import urllib.request
import json
import time

def fetch_em(secid):
    """secid: '1.932000' (1=上海, 0=深圳)"""
    fields = 'f43,f44,f45,f46,f47,f48,f49,f50,f51,f52,f55,f57,f58,f60,f168,f169,f170'
    url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields={fields}&fltt=2&invt=2"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    return data.get('data', {})

# 中证2000 secid=1.932000
print("=== 东财API 中证2000 ===")
d = fetch_em('1.932000')

if d:
    name = d.get('f58', '中证2000')
    code = d.get('f57', '932000')
    now = d.get('f43')  # 现价
    prev_close = d.get('f60')  # 昨收
    open_p = d.get('f46')  # 今开
    high = d.get('f44')  # 最高
    low = d.get('f45')  # 最低
    vol = d.get('f47', 0)  # 成交量(手)
    amount = d.get('f48', 0)  # 成交额(元)
    change = d.get('f169')  # 涨跌
    pct = d.get('f170')  # 涨跌幅(%)
    
    print(f"名称: {name} ({code})")
    print(f"现价: {now}  昨收: {prev_close}")
    print(f"今开: {open_p}  最高: {high}  最低: {low}")
    print(f"涨跌: {change:+.2f}  涨跌幅: {pct:+.2f}%")
    print(f"成交量: {vol/10000:.0f}万手  成交额: {amount/1e8:.1f}亿")
    
    if prev_close and low and high and now:
        chg_vs_prev = (now - prev_close) / prev_close * 100
        reb = (now - low) / low * 100
        pullback = (high - now) / high * 100
        amp = (high - low) / prev_close * 100
        print()
        print("="*60)
        print("中证2000反弹力度评估:")
        print(f"  较昨收: {chg_vs_prev:+.2f}%")
        print(f"  较开盘: {(now-open_p)/open_p*100:+.2f}%")
        print(f"  振幅: {amp:.2f}%")
        print(f"  从最低点反弹: {reb:+.2f}%")
        print(f"  从最高点回落: {pullback:.2f}%")
        if now > prev_close:
            print(f"  ✅ 已收复昨日失地，反弹有效")
        else:
            print(f"  ⚠️ 仍低于昨收 {chg_vs_prev:.2f}%，弱反弹")
        if reb > 3:
            print(f"  💪 反弹力度强（从最低回升{reb:.1f}%）")
        elif reb > 1:
            print(f"  ✅ 反弹力度中等（从最低回升{reb:.1f}%）")
else:
    print("东财无数据")
