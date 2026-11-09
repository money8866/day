# -*- coding: utf-8 -*-
"""探测 stk_holdernumber 分页能力与单季总量（临时脚本）"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if "TUSHARE_TOKEN" not in os.environ:
    for _l in open(r"D:\mystock\config\.env"):
        if _l.strip().startswith("TUSHARE_TOKEN="):
            os.environ["TUSHARE_TOKEN"] = _l.strip().split("=", 1)[1].strip().strip('"')
            break

import tushare as ts

pro = ts.pro_api(os.environ["TUSHARE_TOKEN"])

# 1) 分页探测
for off in (0, 5000, 5500):
    try:
        df = pro.stk_holdernumber(enddate="20260630", offset=off, limit=5000)
        print("offset=%-5d -> %4d 行  首条 %s  末条 %s"
              % (off, len(df), df["ts_code"].iloc[0] if len(df) else "-",
                 df["ts_code"].iloc[-1] if len(df) else "-"))
    except Exception as e:
        print("offset=%s 失败: %s" % (off, e))

# 2) 单季总量（用 offset 翻页累计）
tot, off = 0, 0
while True:
    df = pro.stk_holdernumber(enddate="20260630", offset=off, limit=5000)
    tot += len(df)
    if len(df) < 5000:
        break
    off += 5000
    if off > 20000:
        break
print("\n20260630 全量行数: %d" % tot)

# 3) 看同一 ts_code 同 end_date 是否有多条 ann_date（需去重）
d = pro.stk_holdernumber(enddate="20260630", offset=0, limit=5000)
dup = d.duplicated(subset=["ts_code", "end_date"]).sum()
print("前5000行中 (ts_code,end_date) 重复条数: %d" % dup)
