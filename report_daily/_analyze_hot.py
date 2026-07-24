# -*- coding: utf-8 -*-
import json
print('=== HOT STOCKS (07-24) ===')
hot = json.load(open('D:/mystock/report_daily/q_hot_0724.json', encoding='utf-8'))
print('keys:', list(hot.keys()), 'code:', hot.get('code'))
data = hot.get('data')
if isinstance(data, dict):
    print('data keys:', list(data.keys()))
    for k, v in data.items():
        if isinstance(v, list):
            print('  %s: %d items' % (k, len(v)))
            if v:
                print('   sample:', json.dumps(v[0], ensure_ascii=False)[:300])
        else:
            print('  %s:', str(v)[:120])
elif isinstance(data, list):
    print('data list len:', len(data))
    if data:
        print('sample:', json.dumps(data[0], ensure_ascii=False)[:300])
else:
    print('data:', str(data)[:200])
