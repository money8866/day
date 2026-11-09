# -*- coding: utf-8 -*-
"""验证 enddate + ann_date 区间组合过滤是否可靠（临时脚本）"""
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

# A) 只按 ann_date 区间，看 ann_date 是否都被限制在区间内
d = pro.stk_holdernumber(start_date="20260701", end_date="20260710")
print("按 ann_date 20260701~20260710 -> %d 行" % len(d))
print("  ann_date min/max: %s / %s" % (d["ann_date"].min(), d["ann_date"].max()))
print("  end_date 唯一值数: %d" % d["end_date"].nunique())

# B) 组合：enddate + ann_date 区间
for a, b in (("20260701", "20260710"), ("20260711", "20260720")):
    d2 = pro.stk_holdernumber(enddate="20260630", start_date=a, end_date=b)
    print("组合 enddate=20260630 ann %s~%s -> %3d 行  ann范围 %s~%s  end唯一 %d"
          % (a, b, len(d2), d2["ann_date"].min() if len(d2) else "-",
             d2["ann_date"].max() if len(d2) else "-",
             d2["end_date"].nunique() if len(d2) else 0))
    if len(d2):
        print("   end_date 值: %s" % list(d2["end_date"].unique()[:5]))

# C) 组合后是否仍有 (ts_code,end_date) 重复
d3 = pro.stk_holdernumber(enddate="20260630", start_date="20260630", end_date="20260705")
print("\n组合 enddate=20260630 ann 20260630~20260705 -> %d 行, 重复(ts,end)=%d"
      % (len(d3), d3.duplicated(subset=["ts_code", "end_date"]).sum()))
