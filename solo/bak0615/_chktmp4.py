import os, sys, json, sqlite3, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
from theme_trend_sentiment_score import get_dc_members, get_stock_basic, cache_get

# 1) 获取板块-成份股映射
dc_df = get_dc_members()
print(f"dc_df: {len(dc_df)} rows")
print(f"columns: {list(dc_df.columns)}")

# 2) 获取股票基本信息 -> 查 code
stock_basic = get_stock_basic()
print(f"stock_basic columns: {list(stock_basic.columns)}")

# 3) 查 宗申动力 / 万丰奥威 的 ts_code
target_names = ["宗申动力", "万丰奥威"]
name_to_code = dict(zip(stock_basic["name"], stock_basic["ts_code"]))
for n in target_names:
    print(f"  {n} -> {name_to_code.get(n, 'NOT FOUND')}")

# 4) 查这两只股票在哪些东财板块
for name in target_names:
    code = name_to_code.get(name)
    if code is None:
        continue
    rows = dc_df[dc_df["con_code"] == code]
    if rows.empty:
        print(f"\n{name}({code}): NOT IN ANY DC PLATE")
        continue
    industries = rows[rows["is_industry"]]["concept_name"].tolist()
    concepts = rows[~rows["is_industry"]]["concept_name"].tolist()
    print(f"\n=== {name} ({code}) ===")
    print(f"  行业板块: {industries}")
    print(f"  概念板块: {concepts[:20]}")
    if len(concepts) > 20:
        print(f"            (+{len(concepts)-20} more)")

# 5) 查 "光学光电子" 概念下有哪些股票
print(f"\n=== '光学光电子' 相关板块搜索 ===")
plate_names = dc_df["concept_name"].unique().tolist()
related = [n for n in plate_names if "光学" in str(n) or "光电子" in str(n) or "3D" in str(n) or "激光" in str(n)]
print(f"相关板块: {related}")
for plate in related:
    members = dc_df[dc_df["concept_name"] == plate]
    is_industry = bool(members.iloc[0]["is_industry"]) if not members.empty else False
    print(f"\n  [{plate}] ({'行业' if is_industry else '概念'}) -> {len(members)} 只")
    for _, r in members.head(10).iterrows():
        print(f"    {r['con_code']}")
