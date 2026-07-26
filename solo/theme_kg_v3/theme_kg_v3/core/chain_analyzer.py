"""产业链分析模块.

每日收盘后自动执行:
  1. 分析主题内股票在产业链上的分布
  2. 发现产业链上下游关系变化
  3. 更新 industry_chains 和 chain_relations
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd

from theme_kg_v3.config.settings import (
    CONFIG_DIR,
    DAILY_CACHE_DIR,
    THEME_CONFIG_PATH,
)
from theme_kg_v3.core.etf_analyzer import get_trade_date, get_pro, normalize_code

logger = logging.getLogger(__name__)

CHAIN_CACHE_DIR = DAILY_CACHE_DIR / "chain_analysis"
CHAIN_CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ────────────────────────────────────────────────────────────
# 产业链关系发现
# ────────────────────────────────────────────────────────────

def _load_theme_config() -> Dict[str, Any]:
    if not THEME_CONFIG_PATH.exists():
        return {}
    with open(THEME_CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save_theme_config(config: Dict[str, Any]) -> bool:
    try:
        with open(THEME_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        logger.info("theme_config.json 已更新（产业链分析）")
        return True
    except Exception as e:
        logger.error("保存 theme_config.json 失败: %s", e)
        return False


# 产业链上下游关键词规则
_CHAIN_KEYWORDS: Dict[str, List[str]] = {
    "上游": ["原材料", "矿", "开采", "硅料", "锂矿", "稀土", "化工原料", "能源"],
    "中游": ["制造", "加工", "生产", "代工", "组装", "封测", "晶圆", "电池"],
    "下游": ["应用", "终端", "品牌", "销售", "零售", "运营", "云计算", "AI应用"],
}


def classify_chain_position(business_desc: str) -> str:
    """根据业务描述判断在产业链中的位置.

    Args:
        business_desc: 业务描述文本.

    Returns:
        "上游" / "中游" / "下游" / "未知".
    """
    if not business_desc:
        return "未知"

    scores = {"上游": 0, "中游": 0, "下游": 0}
    for position, kws in _CHAIN_KEYWORDS.items():
        for kw in kws:
            if kw in business_desc:
                scores[position] += 1

    max_pos = max(scores, key=scores.get)
    return max_pos if scores[max_pos] > 0 else "未知"


def calc_chain_distribution(
    theme_code: str,
    theme_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """计算主题内股票在产业链上的分布.

    Args:
        theme_code: 主题代码.
        theme_cfg: 主题配置.

    Returns:
        {current_chains, chain_stocks, suggestions}
    """
    current_chains = theme_cfg.get("industry_chains", [])
    if not current_chains:
        return {"current_chains": [], "suggestions": []}

    # 当前产业链节点过少时，尝试发现新节点
    suggestions: List[str] = []

    # 从 keywords 中提取潜在的产业链节点
    keywords = set()
    for f in ("keywords", "product_keywords", "industry_keywords", "concept_keywords"):
        keywords.update(theme_cfg.get(f, []))

    if len(current_chains) < 3:
        # 从关键词中推荐新产业链节点
        for kw in keywords:
            if kw not in current_chains and len(kw) >= 2:
                suggestions.append(kw)
        suggestions = suggestions[:5]  # 最多推荐5个

    return {
        "current_chains": current_chains,
        "chain_count": len(current_chains),
        "suggestions": suggestions,
    }


def calc_chain_relations(
    theme_config: Dict[str, Any],
) -> Dict[str, List[str]]:
    """计算主题间的产业链关联关系.

    如果两个主题共享 industry_chains 节点，则建立 relation.

    Returns:
        {(theme_a, theme_b): [shared_chains]}
    """
    chain_map: Dict[str, Set[str]] = {}
    for code, cfg in theme_config.items():
        if not isinstance(cfg, dict):
            continue
        chains = set(cfg.get("industry_chains", []))
        if chains:
            chain_map[code] = chains

    relations: Dict[str, List[str]] = {}
    codes = list(chain_map.keys())
    for i in range(len(codes)):
        for j in range(i + 1, len(codes)):
            shared = chain_map[codes[i]] & chain_map[codes[j]]
            if shared:
                key = f"{codes[i]}<->{codes[j]}"
                relations[key] = list(shared)

    return relations


# ────────────────────────────────────────────────────────────
# 主编排
# ────────────────────────────────────────────────────────────

def run_chain_analysis(trade_date: Optional[str] = None) -> Dict[str, Any]:
    """执行产业链自动分析.

    Args:
        trade_date: 交易日期.

    Returns:
        执行摘要.
    """
    if trade_date is None:
        trade_date = get_trade_date()

    logger.info("=" * 60)
    logger.info("产业链自动分析 [%s]", trade_date)
    logger.info("=" * 60)

    summary: Dict[str, Any] = {
        "trade_date": trade_date,
        "themes_analyzed": 0,
        "chain_suggestions": 0,
        "relations_found": 0,
        "config_updated": False,
    }

    theme_config = _load_theme_config()
    if not theme_config:
        return summary

    # 1. 分析每个主题的产业链分布
    total_suggestions = 0
    for code, cfg in theme_config.items():
        if not isinstance(cfg, dict):
            continue
        result = calc_chain_distribution(code, cfg)
        if result.get("suggestions"):
            total_suggestions += len(result["suggestions"])
            logger.info(
                "  %s: 产业链推荐新增 %s",
                cfg.get("name_cn", ""), result["suggestions"],
            )

    summary["themes_analyzed"] = len(theme_config)
    summary["chain_suggestions"] = total_suggestions

    # 2. 发现主题间产业链关联
    relations = calc_chain_relations(theme_config)
    summary["relations_found"] = len(relations)

    if relations:
        logger.info("产业链关联 (%d 对):", len(relations))
        for rel, shared in sorted(relations.items(), key=lambda x: -len(x[1]))[:5]:
            logger.info("  %s: %s", rel, shared)

    # 3. 如果有新建议，更新配置
    if total_suggestions > 0:
        updated = False
        for code, cfg in theme_config.items():
            if not isinstance(cfg, dict):
                continue
            result = calc_chain_distribution(code, cfg)
            if result.get("suggestions"):
                current = cfg.get("industry_chains", [])
                existing = set(current)
                added = [s for s in result["suggestions"] if s not in existing]
                if added:
                    current.extend(added)
                    cfg["industry_chains"] = current
                    updated = True

        if updated:
            # 写入 relations 到配置
            theme_config["_chain_relations"] = {
                "last_updated": trade_date,
                "relations": relations,
            }
            ok = _save_theme_config(theme_config)
            summary["config_updated"] = ok

    logger.info("-" * 60)
    logger.info("执行摘要:")
    logger.info("  分析主题: %d", summary["themes_analyzed"])
    logger.info("  产业链建议: %d", summary["chain_suggestions"])
    logger.info("  关联发现: %d 对", summary["relations_found"])
    logger.info("  配置更新: %s", summary["config_updated"])
    logger.info("=" * 60)

    return summary


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
    )
    run_chain_analysis()
