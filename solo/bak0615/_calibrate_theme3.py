"""用东财板块名校准 theme3.json

核心思路：
1. 用东财板块名（行业/概念）去重并标准化（去除Ⅱ/Ⅲ）。
2. 从每个子主题的 V2 字段里提取关键词 tokens，用字符包含 + 精确名匹配
   计算每个板块名的得分。
3. 对 industry / concept 分别取 Top N（带阈值过滤），写回 theme3.json：
   - industry（行业板块名数组，去Ⅱ后缀）
   - concept（概念板块名数组）
   - keywords（保留原 business_dna_tags + weak_positive_tags）
   - exclude_keywords（从 negative_pressure_tags 生成）
"""
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

# ---------- 1) 东财板块名单 ----------
with open(os.path.join(BASE_DIR, "_dc_board_list.json"), "r", encoding="utf-8") as f:
    dc = json.load(f)

INDUSTRY_BOARDS_SET = set(dc["industry_boards"])  # 带Ⅱ/Ⅲ
CONCEPT_BOARDS_SET = set(dc["concept_boards"])

def strip_roman(s: str) -> str:
    return s.replace("Ⅱ", "").replace("Ⅲ", "").strip()

# 去Ⅱ/Ⅲ版本 -> 优先保留"不带"的版本；如果两个版本都有，给不带的排前面
def dedupe_and_rank(boards: set) -> dict:
    """返回 {简化名: 优先选用的原始名}"""
    simple_to_best = {}
    for b in boards:
        s = strip_roman(b)
        cur = simple_to_best.get(s)
        # 选优先：s == b 即没有罗马数字（更清晰），或者已存在的版本保持
        if cur is None:
            simple_to_best[s] = b
        else:
            # 如果两个版本都有（如"保险"与"保险Ⅱ"），优先保留带Ⅱ/Ⅲ的（东财标准名）
            if "Ⅱ" in b or "Ⅲ" in b:
                simple_to_best[s] = b
    return simple_to_best

INDUSTRY_SIMPLE = dedupe_and_rank(INDUSTRY_BOARDS_SET)  # simplify -> original
CONCEPT_SIMPLE = dedupe_and_rank(CONCEPT_BOARDS_SET)

# 一些在东财行业/概念里反复出现的噪声词：出现在多个主题里，但不应作为匹配强信号
NOISE_FOR_INDUSTRY = {
    "半导体", "消费电子", "医药", "白酒", "煤炭", "新能源", "军工",
    "房地产", "家电", "电子", "银行", "保险", "券商", "新材料",
}
NOISE_FOR_CONCEPT = {"消费电子", "新能源车", "新能源", "医药医疗风格"}

# ---------- 2) 加载 theme3.json ----------
with open(os.path.join(BASE_DIR, "theme3.json"), "r", encoding="utf-8") as f:
    theme3 = json.load(f)

cats = theme3.get("CATEGORIES") if "CATEGORIES" in theme3 else theme3


# ---------- 3) 工具函数 ----------
def collect_tokens(cfg: dict) -> list:
    """把 V2 的各种关键字段拍扁成一个 token 列表，保留下游可定位原始字段"""
    sources = {
        "core_semantic": cfg.get("core_semantic", []) or [],
        "industry_roles": list(cfg.get("industry_roles", {}).keys()) or [],
        "business_dna_tags": cfg.get("business_dna_tags", []) or [],
        "weak_positive_tags": cfg.get("weak_positive_tags", []) or [],
        "industry_soft_constraints": list(cfg.get("industry_soft_constraints", {}).keys()) or [],
    }
    tokens = []
    for src, lst in sources.items():
        for t in lst:
            if isinstance(t, str) and 1 <= len(t) <= 18:
                tokens.append((t, src))
    return tokens


def compute_score(board_name: str, tokens: list, noise_set: set) -> int:
    """给一个板块名打分。基础分 = 精确匹配 100 + 部分匹配若干。"""
    simple = strip_roman(board_name)
    score = 0
    reasons = []
    for tok, src in tokens:
        t_simple = strip_roman(tok)
        if not t_simple:
            continue
        # 精确匹配（去掉Ⅱ/Ⅲ之后完全一致）
        if t_simple == simple:
            score += 200
            reasons.append(f"EXACT[{t_simple}=={simple}]")
            continue
        # 子串：token 是板块名的子串（>=2字）
        if len(t_simple) >= 2 and t_simple in simple:
            bonus = 10 + len(t_simple)
            if t_simple in noise_set:
                bonus = max(2, bonus // 4)  # 噪声词大幅降权
            score += bonus
            reasons.append(f"SUB[{t_simple}⊂{simple}]")
            continue
        # 反向：板块名是 token 的子串（罕见，比如 token 很长）
        if len(simple) >= 2 and simple in t_simple:
            score += 5
            reasons.append(f"RSUB[{simple}⊂{t_simple}]")
    # 板块名至少 2 个字，且得分为正才返回
    return score if score > 0 else 0


def pick_top_boards(tokens: list, boards_simple: dict, noise_set: set, topn=6, min_score=12) -> list:
    scored = []
    for simple, origin in boards_simple.items():
        s = compute_score(origin, tokens, noise_set)
        if s >= min_score:
            scored.append((simple, origin, s))
    # 排序：先按得分，再按"简化名是否等于一个原始名"（更常见、更短的名优先）
    scored.sort(key=lambda kv: (-kv[2], -len(kv[0]), kv[0]))
    out = []
    seen_simples = set()
    for simple, origin, s in scored:
        if simple in seen_simples:
            continue
        # 过滤掉极可能是噪声的板块（例如完全来自噪声词贡献）
        token_strs = {strip_roman(t) for t, _ in tokens}
        non_noise_match = any(
            len(t) >= 2 and t in simple and t not in noise_set
            for t in token_strs
        )
        if not non_noise_match and len(out) >= 2:  # 前2名即使是噪声也留，避免主题无行业
            continue
        out.append(simple)
        seen_simples.add(simple)
        if len(out) >= topn:
            break
    return out


# ---------- 4) 对每个子主题做校准 ----------
summary_lines = []
total_industry = 0
total_concept = 0

for cat_name in sorted(cats.keys()):
    cat_obj = cats[cat_name]
    themes = cat_obj.get("themes", {}) or {}
    for theme_name in sorted(themes.keys()):
        cfg = themes[theme_name] or {}
        tokens = collect_tokens(cfg)

        # 行业板块
        industry_list = pick_top_boards(
            tokens, INDUSTRY_SIMPLE, NOISE_FOR_INDUSTRY, topn=6, min_score=12
        )

        # 概念板块
        concept_list = pick_top_boards(
            tokens, CONCEPT_SIMPLE, NOISE_FOR_CONCEPT, topn=6, min_score=12
        )

        # keywords = business_dna_tags + weak_positive_tags（保留人工语义，不改动）
        keywords = list(dict.fromkeys(
            (cfg.get("business_dna_tags", []) or []) + (cfg.get("weak_positive_tags", []) or [])
        ))
        keywords = [k for k in keywords if k][:20]

        # exclude_keywords = negative_pressure_tags keys
        exclude_kws = list(cfg.get("negative_pressure_tags", {}).keys()) or []

        # 写回 theme3.json 的这个主题（保留原 V2 字段，追加 industry / concept / keywords / exclude_keywords）
        cfg_new = dict(cfg)
        cfg_new["industry"] = industry_list
        cfg_new["concept"] = concept_list
        cfg_new["keywords"] = keywords
        cfg_new["exclude_keywords"] = exclude_kws
        # 同时覆盖 core_companies / leader_companies（若 V2 没给则留空列表，不改之前值）
        cfg_new.setdefault("core_companies", [])
        cfg_new.setdefault("leader_companies", [])
        cat_obj["themes"][theme_name] = cfg_new

        total_industry += len(industry_list)
        total_concept += len(concept_list)

        line = (
            f"[{cat_name:8}] {theme_name:20} "
            f"industry({len(industry_list):2})={industry_list} | "
            f"concept({len(concept_list):2})={concept_list}"
        )
        summary_lines.append(line)


# ---------- 5) 输出 ----------
out_path = os.path.join(BASE_DIR, "theme3.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(theme3, f, ensure_ascii=False, indent=2)

print("\n".join(summary_lines))
print()
print(f"共 70 个主题；写入行业板块映射 {total_industry} 条，概念板块映射 {total_concept} 条")
print(f"已保存到: {out_path}")
