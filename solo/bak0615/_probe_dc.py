"""探索东财行业/概念板块名，为校准 theme3.json 做准备"""
import os, sys
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
import theme_trend_sentiment_score as tts

dc_df = tts.get_dc_members()

# 统计每个板块名的属性：is_industry / 成分股数 / 包含的股票名
# dc_df 列一般是：con_code, concept_name, is_industry (需要确认)
print("dc_df columns:", list(dc_df.columns))
print("dc_df shape:", dc_df.shape)
print()

# 每个板块名去重
by_name = {}
for _, r in dc_df.iterrows():
    name = r["concept_name"]
    entry = by_name.setdefault(name, {"is_industry": bool(r.get("is_industry", False)), "codes": set()})
    entry["codes"].add(r["con_code"])

print(f"唯一板块名数量: {len(by_name)}")

# 行业 vs 概念
industry_boards = sorted([n for n, v in by_name.items() if v["is_industry"]])
concept_boards = sorted([n for n, v in by_name.items() if not v["is_industry"]])
print(f"行业板块数: {len(industry_boards)}")
print(f"概念板块数: {len(concept_boards)}")
print()

# 打印前 30 行业板块名
print("=== 行业板块 (前40) ===")
for n in industry_boards[:40]:
    print(f"  {n} ({len(by_name[n]['codes'])}只)")
print()

# 打印全部概念板块名（分批）
print("=== 概念板块 (按字母序，前150) ===")
for n in concept_boards[:150]:
    print(f"  {n} ({len(by_name[n]['codes'])}只)")

# 存一份完整名单，后面匹配用
with open(os.path.join(BASE_DIR, "_dc_board_list.json"), "w", encoding="utf-8") as f:
    import json
    json.dump({
        "industry_boards": industry_boards,
        "concept_boards": concept_boards,
    }, f, ensure_ascii=False, indent=2)
print(f"\n已保存: {BASE_DIR}/_dc_board_list.json")
