#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查源杰科技全部预告数据"""
import sys, json, time
sys.path.insert(0, r'C:\Users\kongx\AppData\Local\Programs\Python\Python313\Lib\site-packages')
import tushare as ts
ts.set_token('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')
pro = ts.pro_api()

# 查全部
df = pro.forecast(ts_code='688498.SH')
print("=== 源杰科技 全部forecast记录 ===")
if df is not None and len(df) > 0:
    for i, r in df.iterrows():
        d = r.where(r.notna(), None).to_dict()
        line = "  end_date=%s | ann_date=%s | type=%s" % (d.get('end_date'), d.get('ann_date'), d.get('type'))
        line += " | p_min=%s%% p_max=%s%%" % (d.get('p_change_min','?'), d.get('p_change_max','?'))
        line += " | net=%s~%s万" % (d.get('net_profit_min','?'), d.get('net_profit_max','?'))
        line += " | update=%s" % d.get('update_flag','?')
        print(line)
else:
    print("  无数据")
time.sleep(0.06)

# 查2026中报
print()
print("=== end_date=20260630 ===")
df2 = pro.forecast(end_date='20260630')
if df2 is not None and len(df2) > 0:
    print("共%d条记录" % len(df2))
    for i, r in df2.iterrows():
        print("  %s | %s | %s | p=%s~%s%%" % (r['ts_code'], r.get('ann_date',''), r.get('type',''), r.get('p_change_min',0), r.get('p_change_max',0)))
else:
    print("  无2026H1预告数据")

# 查源杰科技H1 2026
time.sleep(0.06)
df3 = pro.forecast(ts_code='688498.SH', end_date='20260630')
print()
print("=== 源杰科技 end_date=20260630 ===")
if df3 is not None and len(df3) > 0:
    r = df3.iloc[0]
    print(json.dumps(r.where(r.notna(), None).to_dict(), ensure_ascii=False, indent=2))
else:
    print("  无单独H1预告数据")
