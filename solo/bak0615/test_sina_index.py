import requests

codes = [
    'sh000901', 'sh000852', 'sh000905', 'sh000001',
    'sz399001', 'sz399106', 'sz399303', 'sz399004',
    'sz399673', 'sh000016', 'sh000688',
    'sh932001', 'sh932000', 'sz932000'
]

for c in codes:
    url = 'https://hq.sinajs.cn/list=' + c
    try:
        r = requests.get(url, headers={
            'Referer': 'https://finance.sina.com.cn',
            'User-Agent': 'Mozilla/5.0'
        }, timeout=5)
        r.encoding = 'gbk'
        text = r.text.strip()
        if text.endswith('=""') or '=""' in text.split('=')[-1]:
            print(f'{c}: 无数据')
        else:
            parts = text.split('"')
            if len(parts) > 1:
                name = parts[1].split(',')[0]
                pct = parts[1].split(',')[3] if len(parts[1].split(',')) > 3 else '?'
                last = parts[1].split(',')[2] if len(parts[1].split(',')) > 2 else '?'
                print(f'{c}: {name} 昨收={last} 现价={pct}')
            else:
                print(f'{c}: 解析失败: {text[:80]}')
    except Exception as e:
        print(f'{c}: ERROR - {e}')
