"""对 theme3.json 的主题逐一做词对板块名匹配，找到最贴近的行业/概念板块"""
import json, os, sys
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

# 加载东财板块名单
with open(os.path.join(BASE_DIR, "_dc_board_list.json"), "r", encoding="utf-8") as f:
    dc = json.load(f)
INDUSTRY_BOARDS = set(dc["industry_boards"])
CONCEPT_BOARDS = set(dc["concept_boards"])

print(f"行业板块数: {len(INDUSTRY_BOARDS)}")
print(f"概念板块数: {len(CONCEPT_BOARDS)}")

# 给行业板块名做"去Ⅱ/Ⅲ"的简化版本，用于模糊匹配
def normalize(name: str) -> str:
    return name.replace("Ⅱ", "").replace("Ⅲ", "").strip()

INDUSTRY_NORM_MAP = {normalize(n): n for n in INDUSTRY_BOARDS}
CONCEPT_NORM_MAP = {normalize(n): n for n in CONCEPT_BOARDS}

# 加载 theme3.json
with open(os.path.join(BASE_DIR, "theme3.json"), "r", encoding="utf-8") as f:
    theme3 = json.load(f)

cats = theme3.get("CATEGORIES") if "CATEGORIES" in theme3 else theme3


def tokens_from_v2(cfg: dict) -> set:
    """把 V2 配置里的所有关键词抽出来，用于和板块名匹配"""
    s = set()
    def add(x):
        if isinstance(x, str):
            s.add(x)
        elif isinstance(x, list):
            for it in x:
                if isinstance(it, str):
                    s.add(it)
        elif isinstance(x, dict):
            for k in x.keys():
                if isinstance(k, str):
                    s.add(k)
            for v in x.values():
                add(v)
    add(cfg.get("core_semantic", []))
    add(cfg.get("industry_roles", {}))
    add(cfg.get("business_dna_tags", []))
    add(cfg.get("weak_positive_tags", []))
    add(cfg.get("negative_pressure_tags", {}))
    add(cfg.get("industry_soft_constraints", {}))
    return {t for t in s if isinstance(t, str) and 1 <= len(t) <= 12}


def match_boards(token_set: set, boards: set, norm_map: dict, topn=8) -> list:
    """用 token_set 去匹配板块名列表，返回 (板块名, 命中次数) 的 Top N"""
    # 把每个板块名拆成 char set，计算与所有 token 的字符级重叠
    # 同时保留精确/子串匹配作为强信号
    results = {}
    for b in boards:
        b_norm = normalize(b)
        # 命中1：某个 token 精确等于板块名
        exact = any(t == b_norm or t == b for t in token_set)
        # 命中2：某个 token 是板块名的子串
        sub = any(len(t) >= 2 and t in b_norm for t in token_set)
        # 命中3：板块名中的关键字子串是 token 的子串（反向）
        rev = any(len(t) >= 3 and (b_norm in t) for t in token_set)
        score = 0
        if exact:
            score += 100
        if sub:
            # 子串长度越长分越高
            for t in token_set:
                if len(t) >= 2 and t in b_norm:
                    score += 10 + len(t)
        if rev:
            score += 5
        if score > 0:
            results[b] = score

    # 按得分降序，取 topn
    return sorted(results.items(), key=lambda kv: (-kv[1], kv[0]))[:topn]


# 对每个子主题输出匹配结果
print("\n=== 主题 -> 东财行业/概念板块匹配 ===")

stats_hit = 0
for cat_name, cat_obj in cats.items():
    themes = cat_obj.get("themes", {}) or {}
    for theme_name, cfg in themes.items():
        tokens = tokens_from_v2(cfg)
        ind_hits = match_boards(tokens, INDUSTRY_BOARDS, INDUSTRY_NORM_MAP, topn=8)
        con_hits = match_boards(tokens, CONCEPT_BOARDS, CONCEPT_NORM_MAP, topn=8)
        print(f"\n[{cat_name}] {theme_name}")
        print(f"  V2 tokens: {sorted(tokens)[:12]}")
        if ind_hits:
            print(f"  行业板块 Top{len(ind_hits)}: {[(n, s) for n, s in ind_hits]}")
        else:
            print(f"  行业板块 Top: (无命中)")
        if con_hits:
            print(f"  概念板块 Top{len(con_hits)}: {[(n, s) for n, s in con_hits]}")
        else:
            print(f"  概念板块 Top: (无命中)")
        if ind_hits or con_hits:
            stats_hit += 1

print(f"\n\n=== 总览 ===")
print(f"总共 {sum(len(v.get('themes', {})) for v in cats.values())} 个子主题，其中 {stats_hit} 个有板块命中")
