"""公告分析模块.

每日收盘后自动执行:
  1. 获取当日公司公告 (Tushare disclosure)
  2. 提取公告中的主题关键词
  3. 发现新增概念/业务描述，更新 keywords
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from theme_kg_v3.config.settings import (
    CONFIG_DIR,
    DAILY_CACHE_DIR,
    THEME_CONFIG_PATH,
)
from theme_kg_v3.core.etf_analyzer import get_trade_date, get_pro, normalize_code
from theme_kg_v3.core.keyword_engine import KeywordEngine

logger = logging.getLogger(__name__)

ANNOUNCE_CACHE_DIR = DAILY_CACHE_DIR / "announcement"
ANNOUNCE_CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ────────────────────────────────────────────────────────────
# 公告数据获取
# ────────────────────────────────────────────────────────────

def fetch_disclosure(trade_date: str) -> pd.DataFrame:
    """获取当日公司公告.

    Args:
        trade_date: 交易日 YYYYMMDD.

    Returns:
        DataFrame: ts_code, end_date, type, ...
    """
    pro = get_pro()
    cache_file = ANNOUNCE_CACHE_DIR / f"disclosure_{trade_date}.pkl"
    if cache_file.exists():
        try:
            df = pd.read_pickle(cache_file)
            if df is not None and not df.empty:
                return df
        except Exception:
            pass

    try:
        start = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=3)).strftime("%Y%m%d")
        df = pro.disclosure(start_date=start, end_date=trade_date)
        time.sleep(0.12)
        if df is not None and not df.empty:
            if "ts_code" in df.columns:
                df["ts_code"] = df["ts_code"].apply(normalize_code)
            df.to_pickle(cache_file)
            logger.info("获取公司公告: %d 条", len(df))
            return df
    except Exception as e:
        logger.warning("获取公司公告失败: %s", e)
    return pd.DataFrame()


# ────────────────────────────────────────────────────────────
# 公告关键词提取
# ────────────────────────────────────────────────────────────

def extract_keywords_from_title(title: str) -> List[str]:
    """从公告标题提取潜在主题关键词.

    Args:
        title: 公告标题.

    Returns:
        提取的关键词列表.
    """
    if not title:
        return []

    keywords = []
    # 模式: "关于XXX的公告"
    match = re.search(r"关于(.+?)的(?:公告|通知|报告)", title)
    if match:
        content = match.group(1)
        # 按标点/空格分割
        parts = re.split(r"[，,、：:。.；; ]", content)
        for part in parts:
            part = part.strip()
            if len(part) >= 2 and len(part) <= 20:
                keywords.append(part)

    # 模式: "XXX项目投资" / "XXX业务" / "XXX产品"
    patterns = [
        (r"(.{2,10})(?:项目|业务|产品|研发|投资|并购|合作)(?:公告|通知)", 1),
        (r"(?:投资|建设|投产)(.{2,10})(?:项目|生产线)", 1),
        (r"(.{2,8})(?:获得|取得|新增|中标|签约)", 1),
        (r"(?:设立|成立|收购)(.{2,10})(?:公司|子公司|合资)", 1),
    ]
    for pattern, group_idx in patterns:
        match = re.search(pattern, title)
        if match:
            kw = match.group(group_idx).strip()
            if kw and len(kw) >= 2:
                keywords.append(kw)

    return list(dict.fromkeys(keywords))


def calc_announcement_impact(
    trade_date: str,
) -> Dict[str, Dict[str, Any]]:
    """分析当日公告对主题的影响.

    Args:
        trade_date: 交易日.

    Returns:
        {theme_code: {announcement_count, new_keywords, ...}}
    """
    df = fetch_disclosure(trade_date)
    if df.empty:
        return {}

    theme_config = _load_theme_config()
    ke = KeywordEngine(THEME_CONFIG_PATH)
    results: Dict[str, Dict[str, Any]] = {}

    for _, row in df.iterrows():
        ts_code = str(row.get("ts_code", ""))
        title = str(row.get("ann_title", row.get("title", "")))

        if not title or title == "nan":
            continue

        # 用分类器判断所属主题
        try:
            match_results = ke.quick_match(title)
            if not match_results or match_results[0].score < 20:
                continue
            theme_code = match_results[0].theme_code
        except Exception:
            continue

        if theme_code not in results:
            results[theme_code] = {
                "announcement_count": 0,
                "new_keywords": [],
                "all_keywords": [],
                "stock_announcements": [],
            }

        results[theme_code]["announcement_count"] += 1
        results[theme_code]["stock_announcements"].append({
            "ts_code": ts_code,
            "title": title,
        })

        # 提取关键词
        kws = extract_keywords_from_title(title)
        results[theme_code]["all_keywords"].extend(kws)

    # 去重并筛选新关键词
    for theme_code, data in results.items():
        cfg = theme_config.get(theme_code, {})
        existing_kws = set()
        for f in ("keywords", "core_keywords", "concept_keywords", "product_keywords"):
            for kw in cfg.get(f, []):
                existing_kws.add(kw)

        data["all_keywords"] = list(dict.fromkeys(data["all_keywords"]))
        data["new_keywords"] = [kw for kw in data["all_keywords"] if kw not in existing_kws][:5]

    return results


# ────────────────────────────────────────────────────────────
# 配置更新
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
        logger.info("theme_config.json 已更新（公告分析）")
        return True
    except Exception as e:
        logger.error("保存 theme_config.json 失败: %s", e)
        return False


def update_keywords_from_announcements(
    theme_config: Dict[str, Any],
    impact: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """将公告中提取的新关键词更新到主题配置.

    Args:
        theme_config: 原始配置.
        impact: calc_announcement_impact 结果.

    Returns:
        更新后的配置.
    """
    config = theme_config.copy()

    for theme_code, data in impact.items():
        if theme_code not in config:
            continue
        cfg = config[theme_code]
        new_kws = data.get("new_keywords", [])
        if not new_kws:
            continue

        existing = set(cfg.get("keywords", []))
        added = []
        for kw in new_kws:
            if kw not in existing:
                cfg.setdefault("keywords", []).append(kw)
                added.append(kw)

        if added:
            logger.info("  %s: 从公告新增关键词 %s", cfg.get("name_cn", ""), added)

    return config


# ────────────────────────────────────────────────────────────
# 主编排
# ────────────────────────────────────────────────────────────

def run_announcement_analysis(trade_date: Optional[str] = None) -> Dict[str, Any]:
    """执行公告自动分析.

    Args:
        trade_date: 交易日期.

    Returns:
        执行摘要.
    """
    if trade_date is None:
        trade_date = get_trade_date()

    logger.info("=" * 60)
    logger.info("公告自动分析 [%s]", trade_date)
    logger.info("=" * 60)

    summary: Dict[str, Any] = {
        "trade_date": trade_date,
        "total_announcements": 0,
        "themes_affected": 0,
        "new_keywords": 0,
        "config_updated": False,
    }

    impact = calc_announcement_impact(trade_date)
    if not impact:
        logger.info("当日无主题关联公告")
        return summary

    summary["themes_affected"] = len(impact)
    summary["total_announcements"] = sum(d["announcement_count"] for d in impact.values())

    logger.info("公告影响主题: %d 个, 共 %d 条", summary["themes_affected"], summary["total_announcements"])
    for theme_code, data in sorted(impact.items(), key=lambda x: -x[1]["announcement_count"]):
        logger.info(
            "  %s: %d 条公告, 新关键词 %s",
            theme_code, data["announcement_count"], data.get("new_keywords", []),
        )

    theme_config = _load_theme_config()
    updated = update_keywords_from_announcements(theme_config, impact)

    total_new = sum(len(d.get("new_keywords", [])) for d in impact.values())
    summary["new_keywords"] = total_new

    if total_new > 0:
        ok = _save_theme_config(updated)
        summary["config_updated"] = ok

    logger.info("-" * 60)
    logger.info("执行摘要:")
    logger.info("  公告总数: %d", summary["total_announcements"])
    logger.info("  影响主题: %d", summary["themes_affected"])
    logger.info("  新增关键词: %d", summary["new_keywords"])
    logger.info("  配置更新: %s", summary["config_updated"])
    logger.info("=" * 60)

    return summary


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
    )
    run_announcement_analysis()
