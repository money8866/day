# -*- coding: utf-8 -*-
"""腾讯财经获取中证2000实时/收盘行情"""
import urllib.request
import re

def fetch_tx(codes):
    """codes: list e.g. ['sh932000', 'sh000001']"""
    url = "https://qt.gtimg.cn/q=" + ",".join(codes)
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://stockapp.finance.qq.com/'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read().decode('gbk')
    result = {}
    for line in raw.strip().split('\n'):
        m = re.match(r'v_(\w+)="(.*)";', line)
        if m:
            code = m.group(1)
            parts = m.group(2).split('~')
            result[code] = parts
    return result

codes = ['sh932000', 'sh000001', 'sz399006', 'sh000300', 'sh000905', 'sh000852']
data = fetch_tx(codes)

for code in codes:
    if code not in data:
        print(f"{code}: 无数据")
        continue
    p = data[code]
    if len(p) < 40:
        print(f"{code}: 数据不完整 {len(p)}字段")
        continue
    name = p[1]
    now = float(p[3]) if p[3] else 0
    prev_close = float(p[4]) if p[4] else 0
    open_p = float(p[5]) if p[5] else 0
    high = float(p[33]) if p[33] else 0
    low = float(p[34]) if p[34] else 0
    vol = float(p[6]) if p[6] else 0  # 手
    amount = float(p[37]) if p[37] else 0  # 元
    time_str = p[30]
    
    if prev_close:
        chg = (now - prev_close) / prev_close * 100
        reb = (now - low) / low * 100 if low else 0
        amp = (high - low) / prev_close * 100 if prev_close else 0
        print(f"{name} ({code})  时间:{time_str}")
        print(f"  现价: {now:.2f}  涨跌: {chg:+.2f}%")
        print(f"  今开: {open_p:.2f}  最高: {high:.2f}  最低: {low:.2f}")
        print(f"  振幅: {amp:.2f}%  从最低反弹: {reb:+.2f}%")
        print(f"  成交额: {amount/1e8:.1f}亿")
        print()

# 中证2000分析
if 'sh932000' in data and len(data['sh932000']) >= 40:
    p = data['sh932000']
    now = float(p[3])
    prev_close = float(p[4])
    low = float(p[34])
    high = float(p[33])
    open_p = float(p[5])
    chg = (now - prev_close) / prev_close * 100
    reb = (now - low) / low * 100
    pullback = (high - now) / high * 100
    
    print("="*60)
    print("中证2000反弹力度评估:")
    print(f"  较昨收: {chg:+.2f}%")
    print(f"  较开盘: {(now-open_p)/open_p*100:+.2f}%")
    print(f"  从最低点反弹: {reb:+.2f}%")
    print(f"  从最高点回落: {pullback:.2f}%")
    if now > prev_close:
        print(f"  ✅ 已收复昨日失地，反弹有效")
    else:
        print(f"  ⚠️ 仍低于昨收 {chg:.2f}%，弱反弹")
    if reb > 3:
        print(f"  💪 反弹力度强（从最低回升{reb:.1f}%）")
    elif reb > 1:
        print(f"  ✅ 反弹力度中等（从最低回升{reb:.1f}%）")
