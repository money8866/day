import os, sqlite3, pandas as pd
from io import StringIO

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "cache_backbone_tushare", "cache.db")
conn = sqlite3.connect(DB)
cur = conn.cursor()

def load_csv_key(key):
    cur.execute("SELECT data FROM cache_data WHERE key=?", (key,))
    row = cur.fetchone()
    if not row:
        print(f"key '{key}' not found")
        return None
    return pd.read_csv(StringIO(row[0]))

dc_df = load_csv_key("tsc_dc_all_members_20260612")
sb_df = load_csv_key("tsc_stock_basic_20260612")
print(f"dc_df: {len(dc_df)} rows, columns={list(dc_df.columns)}")
print(f"sb_df: {len(sb_df)} rows, columns={list(sb_df.columns)}")

# sb_df 的 name/ts_code 列名？
# 假设列名包含 name, ts_code
name_col = None
code_col = None
for c in sb_df.columns:
    if c.lower() == "name":
        name_col = c
    if c.lower() in ("ts_code", "code"):
        code_col = c

print(f"sb_df name_col={name_col}, code_col={code_col}")
if name_col and code_col:
    name_to_code = dict(zip(sb_df[name_col], sb_df[code_col]))

    # 查目标股
    for target_name in ["宗申动力", "万丰奥威"]:
        target_code = name_to_code.get(target_name)
        if not target_code:
            # 模糊搜索
            matches = sb_df[sb_df[name_col].astype(str).str.contains(target_name, na=False)]
            if not matches.empty:
                target_code = matches.iloc[0][code_col]
                print(f"  模糊匹配: {target_name} -> {target_code}")

        if target_code:
            rows = dc_df[dc_df["con_code"] == target_code]
            if rows.empty:
                print(f"\n{target_name}({target_code}): 无东财板块记录")
                continue
            is_industry_series = rows.get("is_industry", pd.Series([False] * len(rows)))
            industries = rows[is_industry_series]["concept_name"].tolist() if is_industry_series.any() else []
            concepts = rows[~is_industry_series]["concept_name"].tolist() if (~is_industry_series).any() else rows["concept_name"].tolist()
            # 如果没有 is_industry 字段，就把所有都当作概念
            if "is_industry" not in dc_df.columns:
                industries = []
                concepts = rows["concept_name"].tolist()
            print(f"\n=== {target_name} ({target_code}) ===")
            print(f"  行业板块 ({len(industries)}): {industries}")
            print(f"  概念板块 ({len(concepts)}): {concepts[:15]}")
            if len(concepts) > 15:
                print(f"            (+{len(concepts)-15} more)")

# 查光学光电子相关板块
print(f"\n=== '光学光电子' 相关板块 ===")
plate_names = dc_df["concept_name"].unique().tolist()
for kw in ["光学", "光电子", "激光", "3D", "机器视觉", "传感器", "半导体", "AI手机", "折叠屏", "柔性屏"]:
    related = sorted([n for n in plate_names if isinstance(n, str) and kw in n])
    if related:
        for plate in related[:10]:
            members = dc_df[dc_df["concept_name"] == plate]
            is_ind = bool(members.iloc[0].get("is_industry", False)) if "is_industry" in dc_df.columns else False
            codes = members["con_code"].tolist()[:8]
            names = []
            if name_col and code_col:
                code_to_name = dict(zip(sb_df[code_col], sb_df[name_col]))
                names = [f"{code_to_name.get(c, c)}({c})" for c in codes]
            else:
                names = codes
            print(f"  [{plate}] ({'行业' if is_ind else '概念'}) {len(members)}只: {', '.join(names)}")

conn.close()
