"""机构研报分析模块.

每日收盘后自动执行:
  1. 获取当日机构研报数据 (Tushare report_rc)
  2. 统计各主题个股的机构覆盖度
  3. 发现新被覆盖的个股，更新 relations
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
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
from theme_kg_v3.core.classifier import ThemeClassifier
from theme_kg_v3.core.keyword_engine import KeywordEngine

logger = logging.getLogger(__name__)

RESEARCH_CACHE_DIR = DAILY_CACHE_DIR / "research_analysis"
RESEARCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ────────────────────────────────────────────────────────────
# 机构研报数据获取
# ────────────────────────────────────────────────────────────

def fetch_report_rc(trade_date: str) -> pd.DataFrame:
    """获取当日机构研报覆盖记录.

    Args:
        trade_date: 交易日 YYYYMMDD.

    Returns:
        DataFrame: ts_code, name, report_type, report_title, ...
    """
    pro = get_pro()
    cache_file = RESEARCH_CACHE_DIR / f"report_rc_{trade_date}.pkl"
    if cache_file.exists():
        try:
            df = pd.read_pickle(cache_file)
            if df is not None and not df.empty:
                return df
        except Exception:
            pass

    # report_rc 只需要日期参数
    try:
        # 用最近30天获取研报
        start = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=30)).strftime("%Y%m%d")
        df = pro.report_rc(start_date=start, end_date=trade_date)
        time.sleep(0.12)
        if df is not None and not df.empty:
            if "ts_code" in df.columns:
                df["ts_code"] = df["ts_code"].apply(normalize_code)
            df.to_pickle(cache_file)
            logger.info("获取机构研报: %d 条记录", len(df))
            return df
    except Exception as e:
        logger.warning("获取机构研报失败: %s", e)
    return pd.DataFrame()


# ────────────────────────────────────────────────────────────
# 研报-主题关联
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
        logger.info("theme_config.json 已更新（研报分析）")
        return True
    except Exception as e:
        logger.error("保存 theme_config.json 失败: %s", e)
        return False


def _get_classifier() -> ThemeClassifier | None:
    try:
        ke = KeywordEngine(THEME_CONFIG_PATH)
        return ThemeClassifier(keyword_engine=ke)
    except Exception:
        return None


def calc_research_coverage(
    trade_date: str,
    lookback_days: int = 30,
) -> Dict[str, Dict[str, Any]]:
    """计算各主题最近 N 天的机构研报覆盖情况.

    Args:
        trade_date: 交易日.
        lookback_days: 回溯天数.

    Returns:
        {theme_code: {stock_count, total_reports, hot_stocks, new_covered, ...}}
    """
    start = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=lookback_days)).strftime("%Y%m%d")
    df = fetch_report_rc(trade_date)
    if df.empty:
        return {}

    # 仅保留 lookback_days 内的数据
    if "report_date" in df.columns:
        df = df[df["report_date"] >= start].copy()

    if df.empty:
        return {}

    theme_config = _load_theme_config()
    classifier = _get_classifier()

    # 统计每个主题的研报数量
    theme_stock_counts: Dict[str, Counter] = {}
    theme_reports: Dict[str, List[Dict[str, Any]]] = {}

    for _, row in df.iterrows():
        ts_code = str(row.get("ts_code", ""))
        stock_name = str(row.get("name", ""))

        # 确定所属主题
        theme_code = None
        for code, cfg in theme_config.items():
            if not isinstance(cfg, dict):
                continue
            all_stocks = set(
                normalize_code(c) for c in (
                    cfg.get("leaders", []) + cfg.get("core_stocks", [])
                )
            )
            if ts_code in all_stocks:
                theme_code = code
                break

        if theme_code is None and classifier:
            try:
                results = classifier.keyword_engine.quick_match(stock_name)
                if results and results[0].score > 30:
                    theme_code = results[0].theme_code
            except Exception:
                pass

        if theme_code is None:
            continue

        if theme_code not in theme_stock_counts:
            theme_stock_counts[theme_code] = Counter()
            theme_reports[theme_code] = []

        theme_stock_counts[theme_code][ts_code] += 1

        title = row.get("report_title", "") or row.get("title", "") or ""
        theme_reports[theme_code].append({
            "ts_code": ts_code,
            "name": stock_name,
            "title": title,
            "count": 1,
        })

    # 整理结果
    results: Dict[str, Dict[str, Any]] = {}
    for theme_code, counter in theme_stock_counts.items():
        total_reports = sum(counter.values())
        # 按研报数降序排列
        sorted_stocks = counter.most_common()
        hot_stocks = [s[0] for s in sorted_stocks[:3]]
        # 新增覆盖（之前不在配置中的）
        cfg = theme_config.get(theme_code, {})
        existing = set(
            normalize_code(c) for c in (
                cfg.get("leaders", []) + cfg.get("core_stocks", [])
            )
        )
        new_covered = [s[0] for s in sorted_stocks if s[0] not in existing][:3]

        results[theme_code] = {
            "stock_count": len(counter),
            "total_reports": total_reports,
            "hot_stocks": hot_stocks,
            "new_covered": new_covered,
            "details": sorted_stocks[:10],
        }

    return results


# ────────────────────────────────────────────────────────────
# 配置更新
# ────────────────────────────────────────────────────────────

def update_relations_from_research(
    theme_config: Dict[str, Any],
    coverage: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """根据研报覆盖更新主题配置.

    将新增覆盖的个股加入 core_stocks 列表.

    Args:
        theme_config: 原始配置.
        coverage: calc_research_coverage 的结果.

    Returns:
        更新后的配置.
    """
    config = theme_config.copy()

    for theme_code, data in coverage.items():
        if theme_code not in config:
            continue
        cfg = config[theme_code]
        new_stocks = data.get("new_covered", [])
        if not new_stocks:
            continue

        existing = set(normalize_code(c) for c in cfg.get("core_stocks", []))
        existing.update(normalize_code(c) for c in cfg.get("leaders", []))

        added = []
        for s in new_stocks:
            if s not in existing:
                cfg.setdefault("core_stocks", []).append(s)
                added.append(s)

        if added:
            logger.info("  %s: 新增核心股 %s (机构研报覆盖)", cfg.get("name_cn", ""), added)

    return config


# ────────────────────────────────────────────────────────────
# 主编排
# ────────────────────────────────────────────────────────────

def run_research_analysis(trade_date: Optional[str] = None) -> Dict[str, Any]:
    """执行机构研报自动分析.

    Args:
        trade_date: 交易日期.

    Returns:
        执行摘要.
    """
    if trade_date is None:
        trade_date = get_trade_date()

    logger.info("=" * 60)
    logger.info("机构研报自动分析 [%s]", trade_date)
    logger.info("=" * 60)

    summary: Dict[str, Any] = {
        "trade_date": trade_date,
        "total_reports": 0,
        "themes_covered": 0,
        "new_stocks_added": 0,
        "config_updated": False,
    }

    # 1. 计算研报覆盖
    coverage = calc_research_coverage(trade_date)
    if not coverage:
        logger.info("当日无主题关联研报数据")
        return summary

    summary["themes_covered"] = len(coverage)
    total_reports = sum(d["total_reports"] for d in coverage.values())
    summary["total_reports"] = total_reports

    logger.info("研报覆盖主题: %d 个, 共 %d 篇", len(coverage), total_reports)
    for theme_code, data in sorted(coverage.items(), key=lambda x: -x[1]["total_reports"]):
        logger.info(
            "  %s: %d 只股票, %d 篇, 热门: %s",
            theme_code, data["stock_count"], data["total_reports"],
            data["hot_stocks"],
        )

    # 2. 更新配置
    theme_config = _load_theme_config()
    updated = update_relations_from_research(theme_config, coverage)

    total_new = sum(
        len(d.get("new_covered", []))
        for d in coverage.values()
    )
    summary["new_stocks_added"] = total_new

    if total_new > 0:
        ok = _save_theme_config(updated)
        summary["config_updated"] = ok

    logger.info("-" * 60)
    logger.info("执行摘要:")
    logger.info("  研报总数: %d 篇", summary["total_reports"])
    logger.info("  覆盖主题: %d 个", summary["themes_covered"])
    logger.info("  新增个股: %d 只", summary["new_stocks_added"])
    logger.info("  配置更新: %s", summary["config_updated"])
    logger.info("=" * 60)

    return summary


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
    )
    run_research_analysis()
