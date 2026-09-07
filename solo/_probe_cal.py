# -*- coding: utf-8 -*-
"""核对旧(stk_factor_pro) vs 新(daily_cache) 的交易日历差异（000001.SZ 锚点）"""
import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')
db = sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db')

def dates(tbl):
    return [r[0] for r in db.execute(
        f"SELECT DISTINCT trade_date FROM {tbl} WHERE trade_date>='20240101' "
        f"AND ts_code='000001.SZ' ORDER BY trade_date").fetchall()]

sp = dates('stk_factor_pro')
dc = dates('daily_cache')
print(f'stk_factor_pro(000001.SZ): {len(sp)} 日  {sp[0]}..{sp[-1]}')
print(f'daily_cache(000001.SZ)   : {len(dc)} 日  {dc[0]}..{dc[-1]}')
ss, ds = set(sp), set(dc)
print(f'仅旧有: {len(ss - ds)} -> {sorted(ss - ds)[:10]}')
print(f'仅新有: {len(ds - ss)} -> {sorted(ds - ss)[:10]}')
# 尾部 30 个日历日对比
print(f'旧尾部15: {sp[-15:]}')
print(f'新尾部15: {dc[-15:]}')

# 关键：20260801-20260904 区间双方交易日是否一一对应
seg_sp = [d for d in sp if '20260801' <= d <= '20260904']
seg_dc = [d for d in dc if '20260801' <= d <= '20260904']
print(f'\n20260801-20260904: 旧={len(seg_sp)}日 新={len(seg_dc)}日')
print(f'旧多: {sorted(set(seg_sp) - set(seg_dc))}')
print(f'新多: {sorted(set(seg_dc) - set(seg_sp))}')
db.close()
