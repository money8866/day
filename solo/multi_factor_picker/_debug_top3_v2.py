# -*- coding: utf-8 -*-
"""模拟精选逻辑，找出主板强势横盘TOP3"""
import os, sys
sys.path.insert(0, r'D:\mystock')
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'

# 读取扫描日志中所有主板强势横盘信号
sideways_main = [
    ('600183.SH', 32, '生益科技'),
    ('600301.SH', 26, '华锡有色'),
    ('603929.SH', 25, '亚翔集成'),
    ('600601.SH', 23, '方正科技'),
    ('603678.SH', 23, ''),
    ('688519.SH', 21, ''),
    ('301217.SH', 21, ''),
    ('301128.SH', 22, ''),
]

# 过滤主板
main_codes = [x for x in sideways_main if x[0].startswith(('600', '000', '002'))]
main_codes.sort(key=lambda x: -x[1])
print('主板强势横盘排序:')
for i, (code, score, name) in enumerate(main_codes, 1):
    print(f'  {i}. {code} {name} {score}分')
print()
print('TOP3应该是:')
for i, (code, score, name) in enumerate(main_codes[:3], 1):
    print(f'  {i}. {code} {name} {score}分')
