import os, sqlite3, pandas as pd, json
from io import StringIO
from collections import OrderedDict

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "cache_backbone_tushare", "cache.db")
conn = sqlite3.connect(DB)
cur = conn.cursor()

def load_csv_key(key):
    cur.execute("SELECT data FROM cache_data WHERE key=?", (key,))
    row = cur.fetchone()
    if not row:
        return None
    return pd.read_csv(StringIO(row[0]))

dc_df = load_csv_key("tsc_dc_all_members_20260612")

# === 复刻 theme3_constituents_v2.py 中的 v2_to_old 桥接函数 ===
MANUAL_CONCEPT = {
    "AI应用": ["人工智能", "AI应用", "AI智能体", "多模态AI", "ChatGPT概念", "AIGC概念", "数字人"],
    "AI模型与AI Agent": ["人工智能", "AI应用", "AI智能体", "多模态AI", "ChatGPT概念", "AIGC概念"],
    "AI算力芯片": ["AI芯片", "算力概念", "GPU概念", "人工智能", "半导体概念", "先进封装"],
    "光模块与CPO": ["CPO概念", "光通信模块", "光纤概念", "光通信", "5G概念"],
    "数据中心网络": ["东数西算", "算力概念", "云计算", "数字经济"],
    "数据中心散热": ["液冷概念", "算力概念", "数据中心", "绿色电力"],
    "高速铜连接": ["铜缆高速连接", "连接器", "算力概念"],
    "行星滚柱丝杠": ["人形机器人", "减速器", "工业母机", "高端装备"],
    "核聚变": ["核能核电", "高温超导", "军工"],
}

# 东财所有板块名（行业/概念分开）
ind_names = set(dc_df[dc_df["is_industry"]]["concept_name"].unique())
con_names = set(dc_df[~dc_df["is_industry"]]["concept_name"].unique())
print(f"东财行业板块数: {len(ind_names)}, 概念板块数: {len(con_names)}")

def v2_to_old(cfg, theme_name):
    """模拟桥接：从 V2 字段 映射到 industry/concept/keywords 列表"""
    # 1) industry: 从 industry_soft_constraints + industry_roles key 匹配
    industry_candidates = list(cfg.get("industry_soft_constraints", {}).keys()) + list(cfg.get("industry_roles", {}).keys())
    matched_industries = []
    for cand in industry_candidates:
        for ind in ind_names:
            if cand == ind or cand in ind or ind in cand:
                matched_industries.append(ind)
                break
    # 去重保持顺序
    seen = set()
    matched_industries = [x for x in matched_industries if not (x in seen or seen.add(x))]

    # 2) concept: business_dna_tags + weak_positive_tags 与概念板块名匹配
    concept_candidates = cfg.get("business_dna_tags", []) + cfg.get("weak_positive_tags", [])
    matched_concepts = []
    for cand in concept_candidates:
        for con in con_names:
            if cand == con or cand in con or con in cand:
                matched_concepts.append(con)
                break
    seen = set()
    matched_concepts = [x for x in matched_concepts if not (x in seen or seen.add(x))]

    # 人工补强
    if theme_name in MANUAL_CONCEPT:
        for c in MANUAL_CONCEPT[theme_name]:
            if c in con_names and c not in matched_concepts:
                matched_concepts.append(c)

    # 3) keywords
    keywords = cfg.get("business_dna_tags", []) + cfg.get("weak_positive_tags", []) + cfg.get("core_semantic", [])
    # 4) exclude_keywords
    exclude_keywords = list(cfg.get("negative_pressure_tags", {}).keys())

    return matched_industries, matched_concepts, keywords, exclude_keywords

# === 加载 theme3.json 低空经济主题 ===
theme3 = json.load(open("theme3.json", "r", encoding="utf-8"))
low_altitude = theme3["CATEGORIES"]["低空经济"]["themes"]

target_stocks = [("宗申动力", "001696.SZ"), ("万丰奥威", "002085.SZ")]

# 查这两只股票的东财板块归属
for stock_name, stock_code in target_stocks:
    rows = dc_df[dc_df["con_code"] == stock_code]
    stock_industries = set(rows[rows["is_industry"]]["concept_name"].tolist())
    stock_concepts = set(rows[~rows["is_industry"]]["concept_name"].tolist())

    print(f"\n{'='*80}")
    print(f"📊 {stock_name} ({stock_code})")
    print(f"  东财行业板块: {sorted(stock_industries)}")
    print(f"  东财概念板块 ({len(stock_concepts)}): {sorted(stock_concepts)[:10]}...")
    print()

    # 对每个低空子主题检查
    for theme_name, cfg in low_altitude.items():
        inds, concepts, kws, excludes = v2_to_old(cfg, theme_name)
        print(f"  [{theme_name}]")
        print(f"    映射 industry: {inds}")
        print(f"    映射 concept: {concepts}")

        # 检查命中情况
        ind_hit = bool(stock_industries & set(inds))
        con_hit = stock_concepts & set(concepts)
        kw_hit = any(any(kw in c for c in stock_concepts) for kw in kws)

        # 是否被 exclude 命中
        ex_hit = any(ex for ex in excludes if any(ex in c for c in stock_concepts))

        print(f"    行业命中: {'✅' if ind_hit else '❌'}  概念命中: {con_hit if con_hit else '❌'}  关键词命中: {'✅' if kw_hit else '❌'}  排除命中: {'⚠️' if ex_hit else '❌'}")

        # 检查 match_theme_stocks 用的 is_industry_list 匹配 + concept 匹配
        # (实际 match_theme_stocks 会用 _in_industry_list: 看 stock_basic 的 industry 字段)
        print()

# === 问题2：光学光电子——看哪些子主题能抓到光学光电子概念股 ===
print(f"\n{'='*80}")
print("🔍 光学光电子概念股 - 在消费电子 vs 物理AI 中的命中情况")
print()

# 找"光学光电子"行业的成分股
optical_rows = dc_df[dc_df["concept_name"] == "光学光电子"]
optical_codes = set(optical_rows["con_code"].tolist())
print(f"东财[光学光电子]行业成分股: {len(optical_codes)}只")

# 看消费电子子主题
consumer_elec = theme3["CATEGORIES"]["消费"]["themes"]["消费电子"]
inds, concepts, kws, ex = v2_to_old(consumer_elec, "消费电子")
print(f"\n[消费电子] 配置:")
print(f"  business_dna_tags: {consumer_elec['business_dna_tags']}")
print(f"  weak_positive_tags: {consumer_elec['weak_positive_tags']}")
print(f"  映射 industry: {inds}")
print(f"  映射 concept: {concepts}")

# 看物理AI子主题
physical_ai = theme3["CATEGORIES"]["物理AI"]["themes"]
for tname, tcfg in physical_ai.items():
    inds, concepts, kws, ex = v2_to_old(tcfg, tname)
    # 计算有多少光学光电子概念股在这个主题下
    # 简化：看光学光电子股票的概念与这里 concepts 的交集
    hits = 0
    for code in list(optical_codes)[:30]:
        rows = dc_df[dc_df["con_code"] == code]
        concepts_ = set(rows[~rows["is_industry"]]["concept_name"].tolist())
        if concepts_ & set(concepts):
            hits += 1
    print(f"  [{tname}] 映射concept: {concepts[:6]} → 在光学光电子99只中命中约 {hits} 只")

# 再看这两只股票是否能被消费电子或物理AI 中的概念捕获
print(f"\n  宗申动力 - 消费电子/物理AI 命中?  概念交集:")
for stock_name, stock_code in target_stocks:
    rows = dc_df[dc_df["con_code"] == stock_code]
    stock_concepts = set(rows[~rows["is_industry"]]["concept_name"].tolist())
    for tname, tcfg in physical_ai.items():
        inds, concepts, kws, ex = v2_to_old(tcfg, tname)
        overlap = stock_concepts & set(concepts)
        if overlap:
            print(f"    {stock_name} -> [{tname}] 命中: {overlap}")
    for tname, tcfg in [("消费电子", consumer_elec)]:
        inds, concepts, kws, ex = v2_to_old(tcfg, tname)
        overlap = stock_concepts & set(concepts)
        if overlap:
            print(f"    {stock_name} -> [消费/{tname}] 命中: {overlap}")

conn.close()
