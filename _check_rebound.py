# -*- coding: utf-8 -*-
"""获取今日实时行情（新浪财经）"""
import urllib.request
import re
import time

def fetch_sina(codes):
    """codes: list of sina format e.g. ['sh932000','sh000001']"""
    url = f"https://hq.sinajs.cn/list=" + ",".join(codes)
    req = urllib.request.Request(url, headers={
        'Referer': 'https://finance.sina.com.cn',
        'User-Agent': 'Mozilla/5.0'
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read().decode('gbk')
    result = {}
    for line in raw.strip().split('\n'):
        m = re.match(r'var hq_str_(\w+)="(.*)";', line)
        if m:
            code = m.group(1)
            parts = m.group(2).split(',')
            if len(parts) >= 6:
                result[code] = parts
    return result

# 中证2000 + 对比指数
codes = ['sh932000', 'sh000001', 'sz399006', 'sh000300', 'sh000905', 'sh000852']
data = fetch_sina(codes)

print(f"数据时间: {data.get('sh932000', [''])[30] if len(data.get('sh932000', [])) > 30 else 'N/A'}")
print("="*70)

for code in codes:
    if code not in data:
        print(f"{code}: 无数据")
        continue
    p = data[code]
    name = p[0]
    open_p = float(p[1])
    prev_close = float(p[2])
    now = float(p[3])
    high = float(p[4])
    low = float(p[5])
    chg = (now - prev_close) / prev_close * 100
    chg_open = (now - open_p) / open_p * 100
    amp = (high - low) / prev_close * 100
    
    print(f"{name} ({code})")
    print(f"  现价: {now:.2f}  涨跌幅: {chg:+.2f}%")
    print(f"  今开: {open_p:.2f}  最高: {high:.2f}  最低: {low:.2f}")
    print(f"  较开盘: {chg_open:+.2f}%  振幅: {amp:.2f}%")
    print()

# 中证2000特殊分析
if 'sh932000' in data:
    p = data['sh932000']
    now = float(p[3])
    prev_close = float(p[2])
    open_p = float(p[1])
    high = float(p[4])
    low = float(p[5])
    
    # 反弹力度：从早盘低点回升幅度
    # 需要早盘最低点 - 新浪没有盘中最低，用low近似
    print("="*70)
    print("中证2000反弹力度分析:")
    print(f"  昨日收盘: {prev_close:.2f}")
    print(f"  今日最低: {low:.2f} (盘中最低)")
    print(f"  当前回升: 从最低 {(now-low)/low*100:+.2f}%")
    print(f"  开盘跳空: {(open_p-prev_close)/prev_close*100:+.2f}%")
    print(f"  当前涨幅: {(now-prev_close)/prev_close*100:+.2f}%")
    # 收复失地：相对昨收
    if now > prev_close:
        print(f"  ✅ 已收复昨日失地")
    else:
        print(f"  ⚠️ 仍低于昨日收盘 {(now-prev_close)/prev_close*100:.2f}%")
