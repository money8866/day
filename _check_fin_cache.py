# -*- coding: utf-8 -*-
import os, pickle, sys
sys.stdout.reconfigure(encoding='utf-8')
FIN_CACHE = r'C:\Users\kongx\mystock\fin_cache_v4.pkl'
if os.path.exists(FIN_CACHE):
    cache = pickle.load(open(FIN_CACHE, 'rb'))
    print(f"fin_cache 条目数: {len(cache)}")
    # Show first 3 entries
    for k, v in list(cache.items())[:3]:
        print(f"  {k}: pe={v.get('pe')}, gm={v.get('grossprofit_margin')}, nm={v.get('netprofit_margin')}")
    # Check how many have valid pe
    valid_pe = sum(1 for v in cache.values() if v.get('pe') and 0 < v.get('pe') <= 100)
    print(f"有效PE(0-100)条目: {valid_pe}")
else:
    print("fin_cache 不存在")
