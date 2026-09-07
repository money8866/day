# -*- coding: utf-8 -*-
"""归因验证：old-vs-new 事件差异股票是否全部存在 stk_factor_pro 数据洞"""
import json, os, sys
sys.stdout.reconfigure(encoding='utf-8')
import sqlite3
db = sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db')

def ev_codes(path):
    d = json.load(open(path, encoding='utf-8'))
    return {e['ts_code'] for e in d.get('events', [])}

old_codes = ev_codes(r'd:\mystock\solo\_hvt_compare\old_20260904.json')
new_codes = ev_codes(r'd:\mystock\solo\_hvt_compare\new_20260904.json')

only_old = sorted(old_codes - new_codes)
only_new = sorted(new_codes - old_codes)
common = sorted(old_codes & new_codes)

HIST, T0 = '20240801', '20260904'

def hole_info(code):
    n_sp = db.execute("SELECT COUNT(*) FROM stk_factor_pro WHERE ts_code=? AND trade_date BETWEEN ? AND ?",
                      (code, HIST, T0)).fetchone()[0]
    n_dc = db.execute("SELECT COUNT(*) FROM daily_cache WHERE ts_code=? AND trade_date BETWEEN ? AND ?",
                      (code, HIST, T0)).fetchone()[0]
    return n_sp, n_dc

print(f'仅A(old)事件 {len(only_old)} 只；仅B(new)事件 {len(only_new)} 只；共同 {len(common)} 只')
print()
print('=== 仅 A(old) 事件股：stk_factor_pro vs daily 窗口行数 ===')
bad = []
for c in only_old:
    ns, nd = hole_info(c)
    mark = '<== 无行差(不可归因!)' if ns == nd else ''
    if ns == nd:
        bad.append(c)
    print(f'  {c}: stk={ns} daily={nd} {mark}')
print()
print('=== 仅 B(new) 事件股 ===')
for c in only_new:
    ns, nd = hole_info(c)
    mark = '<== 无行差(不可归因!)' if ns == nd else ''
    if ns == nd:
        bad.append(c)
    print(f'  {c}: stk={ns} daily={nd} {mark}')
print()
print(f'无行差的事件集合差异股票: {bad}')
db.close()
