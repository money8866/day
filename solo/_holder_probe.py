# -*- coding: utf-8 -*-
"""探测 stk_holdernumber 在本地 tushare 下的可用参数与返回行数（临时脚本）"""
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

for kw in ({"enddate": "20260630"}, {"end_date": "20260630"}):
    try:
        df = pro.stk_holdernumber(**kw)
        print("参数 %s -> %d 行, 列 %s" % (kw, len(df), list(df.columns)))
        print(df.head(3).to_string())
        print("唯一 end_date:", df["end_date"].unique()[:5])
    except Exception as e:
        print("参数 %s 失败: %s" % (kw, e))

# 区间查询：看是否能覆盖不定期公布
for kw in ({"start_date": "20260701", "end_date": "20260930"},):
    try:
        df = pro.stk_holdernumber(**kw)
        print("\n参数 %s -> %d 行" % (kw, len(df)))
        print("end_date 分布:")
        print(df["end_date"].value_counts().head(10).to_string())
    except Exception as e:
        print("\n参数 %s 失败: %s" % (kw, e))
