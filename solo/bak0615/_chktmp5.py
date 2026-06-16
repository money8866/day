import os, sqlite3, json, pickle, pandas as pd, io

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "cache_backbone_tushare", "cache.db")
conn = sqlite3.connect(DB)
cur = conn.cursor()

# 1) 查所有 keys
cur.execute("SELECT key FROM cache_data")
keys = [r[0] for r in cur.fetchall()]
print(f"总缓存 key 数: {len(keys)}")
print(f"keys (前20): {keys[:20]}")

# 2) 查 dc_all_members
cur.execute("SELECT data FROM cache_data WHERE key='dc_all_members'")
row = cur.fetchone()
if row:
    try:
        dc_df = pickle.loads(row[0])
        print(f"\ndc_df loaded: {len(dc_df)} rows, columns={list(dc_df.columns)}")
    except Exception as e1:
        print(f"pickle failed: {e1}")
        # fallback: try pandas
        try:
            dc_df = pd.read_pickle(io.BytesIO(row[0]))
            print(f"pd.read_pickle: {len(dc_df)} rows")
        except Exception as e2:
            print(f"also failed: {e2}")

# 3) 查 stock_basic
cur.execute("SELECT data FROM cache_data WHERE key='stock_basic'")
row = cur.fetchone()
if row:
    try:
        sb_df = pickle.loads(row[0])
        print(f"\nstock_basic loaded: {len(sb_df)} rows, columns={list(sb_df.columns)}")
    except Exception as e1:
        try:
            sb_df = pd.read_pickle(io.BytesIO(row[0]))
            print(f"pd.read_pickle stock_basic: {len(sb_df)} rows")
        except Exception as e2:
            print(f"stock_basic failed: {e2}")

# 4) 查宗申动力/万丰奥威
if 'sb_df' in dir():
    name_to_code = dict(zip(sb_df.get("name", sb_df.get("con_code", [])), sb_df.get("ts_code", sb_df.get("code", []))))
    for target_name in ["宗申动力", "万丰奥威"]:
        target_code = None
        # 在 stock_basic 里查
        for col in sb_df.columns:
            matches = sb_df[sb_df[col].astype(str).str.contains(target_name, na=False)]
            if not matches.empty:
                # 找 code 列
                for c2 in ["ts_code", "code", "con_code"]:
                    if c2 in sb_df.columns:
                        target_code = matches.iloc[0][c2]
                        break
                break
        if target_code is None:
            print(f"\n未在 stock_basic 中找到: {target_name}")
            continue

        # 查东财板块
        rows = dc_df[dc_df["con_code"] == target_code]
        if rows.empty:
            print(f"\n{target_name}({target_code}): 无东财板块记录")
            continue
        industries = rows[rows["is_industry"]]["concept_name"].tolist()
        concepts = rows[~rows["is_industry"]]["concept_name"].tolist()
        print(f"\n=== {target_name} ({target_code}) ===")
        print(f"  行业板块: {industries}")
        print(f"  概念板块 ({len(concepts)}): {concepts[:15]}")
        if len(concepts) > 15:
            print(f"            +{len(concepts)-15} 个")

# 5) 查 "光学光电子" 相关板块
print(f"\n=== '光学光电子' 相关板块 ===")
plate_names = dc_df["concept_name"].unique().tolist()
for kw in ["光学", "光电子", "激光", "3D", "机器视觉", "传感器", "半导体"]:
    related = [n for n in plate_names if isinstance(n, str) and kw in n]
    for plate in related:
        members = dc_df[dc_df["concept_name"] == plate]
        is_ind = bool(members.iloc[0]["is_industry"]) if not members.empty else False
        top_names = []
        # 取前 10 只成份股的名称
        codes = members["con_code"].tolist()[:10]
        for c in codes:
            # 在 stock_basic 里查名字
            m = sb_df[sb_df["ts_code"] == c] if "ts_code" in sb_df.columns else sb_df[sb_df.get("code", None) == c]
            if not m.empty and "name" in m.columns:
                top_names.append(f"{m.iloc[0]['name']}({c})")
            else:
                top_names.append(c)
        print(f"  [{plate}] ({'行业' if is_ind else '概念'}) {len(members)}只: {', '.join(top_names)}")

conn.close()
