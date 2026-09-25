# -*- coding: utf-8 -*-
"""探查 HVT 日输出结构，供正交验证复用（只读，不修改 HVT）"""
import os, json, glob

RD = r'd:\mystock\solo\report_daily'
fs = sorted(glob.glob(os.path.join(RD, 'hvt_bull_2*.json')))
print('文件数', len(fs), '| 首', os.path.basename(fs[0]), '| 末', os.path.basename(fs[-1]))
d = json.load(open(fs[-1], encoding='utf-8'))
print('顶层 keys:', list(d.keys())[:40])
for k, v in d.items():
    if isinstance(v, list):
        print('  list:', k, 'len', len(v), '| row0 keys:', (list(v[0].keys())[:40] if v and isinstance(v[0], dict) else type(v[0]).__name__ if v else '-'))
    elif isinstance(v, dict):
        print('  dict:', k, 'keys', list(v.keys())[:20])
    else:
        print('  scalar:', k, '=', str(v)[:80])
