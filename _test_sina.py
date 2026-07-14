# -*- coding: utf-8 -*-
import time, os, subprocess, json, re
import requests as _requests

SINA_HEADERS = {
    'Referer': 'https://finance.sina.com.cn',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
}

def fetch_sina_quotes(codes):
    if not codes:
        return {}
    sina_list = []
    for code in codes:
        if code.endswith('.SH'):
            sina_list.append('sh' + code.replace('.SH', ''))
        elif code.endswith('.SZ'):
            sina_list.append('sz' + code.replace('.SZ', ''))
    result_map = {}
    for offset in range(0, len(sina_list), 200):
        batch = sina_list[offset:offset+200]
        url = 'https://hq.sinajs.cn/list=' + ','.join(batch)
        try:
            resp = _requests.get(url, headers=SINA_HEADERS, timeout=5)
            resp.encoding = 'gbk'
            lines = resp.text.strip().split('\n')
            for line in lines:
                line = line.strip()
                if not line or '=' not in line:
                    continue
                try:
                    var_part = line.split('=', 1)[1]
                    if var_part.count('"') < 2:
                        continue
                    data_str = var_part.split('"')[1]
                    fields = data_str.split(',')
                    if len(fields) < 32:
                        continue
                    var_name = line.split('hq_str_')[1].split('=')[0]
                    if var_name.startswith('sz'):
                        ts_c = var_name[2:].zfill(6) + '.SZ'
                    elif var_name.startswith('sh'):
                        ts_c = var_name[2:].zfill(6) + '.SH'
                    else:
                        continue
                    prev_close = float(fields[2])
                    price = float(fields[3])
                    high = float(fields[4])
                    low = float(fields[5])
                    volume = int(fields[8])
                    amount = float(fields[9])
                    pct = (price - prev_close) / prev_close * 100 if prev_close > 0 else 0
                    result_map[ts_c] = {
                        'price': price, 'pct_chg': round(pct, 2),
                        'high': high, 'low': low, 'vol': volume,
                        'amount': amount, 'prev_close': prev_close,
                        'name': fields[0],
                    }
                except (IndexError, ValueError):
                    continue
        except Exception as e:
            print('Sina error:', e)
    return result_map

# 测试
test_codes = [
    '000001.SZ', '159516.SZ', '600519.SH', '601318.SH',
    '000002.SZ', '000300.SH', '399006.SZ', '600036.SH',
]
print('=== 测试新浪批量行情 (%d只) ===' % len(test_codes))
start = time.time()
quotes = fetch_sina_quotes(test_codes)
elapsed = time.time() - start
print('耗时: %.3fs' % elapsed)
for code in test_codes:
    q = quotes.get(code, {})
    print('  %s: %s %.2f%%' % (code, q.get('name','?'), q.get('pct_chg', 0)))

print()
print('=== 性能: 100只 ===')
codes100 = ['%06d.SZ' % i for i in range(1, 101)]
start = time.time()
q100 = fetch_sina_quotes(codes100)
elapsed = time.time() - start
print('获取 %d 只, 耗时 %.3fs' % (len(q100), elapsed))
