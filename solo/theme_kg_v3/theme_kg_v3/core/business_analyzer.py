"""主营业务分析模块.

每日收盘后自动执行:
  1. 获取个股主营构成数据 (Tushare fina_mainbz)
  2. 按主题汇总主营构成
  3. 计算行业权重 (industry_weight)
  4. 更新 theme_config.json 中的 industry_weight 和 purity 字段
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from theme_kg_v3.config.settings import (
    CONFIG_DIR,
    DAILY_CACHE_DIR,
    THEME_CONFIG_PATH,
)
from theme_kg_v3.core.etf_analyzer import get_trade_date, get_pro, normalize_code

logger = logging.getLogger(__name__)

BIZ_CACHE_DIR = DAILY_CACHE_DIR / "business_analysis"
BIZ_CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ────────────────────────────────────────────────────────────
# 主营数据获取
# ────────────────────────────────────────────────────────────

def fetch_mainbz(ts_code: str, trade_date: str) -> pd.DataFrame:
    """获取个股主营业务构成.

    Args:
        ts_code: 股票代码.
        trade_date: 交易日.

    Returns:
        DataFrame: bz_item, bz_sales, bz_profit, ...
    """
    pro = get_pro()
    # 取最近一年的年报
    year = int(trade_date[:4])
    end_date = f"{year}1231"
    cache_key = f"{ts_code}_{year}"
    cache_file = BIZ_CACHE_DIR / f"mainbz_{cache_key}.pkl"
    if cache_file.exists():
        try:
            df = pd.read_pickle(cache_file)
            if df is not None and not df.empty:
                return df
        except Exception:
            pass

    try:
        df = pro.fina_mainbz(ts_code=ts_code, end_date=end_date, type="P")
        time.sleep(0.12)
        if df is not None and not df.empty:
            df.to_pickle(cache_file)
            return df
    except Exception as e:
        logger.debug("获取 %s 主营失败: %s", ts_code, e)
    return pd.DataFrame()


# ────────────────────────────────────────────────────────────
# 主题行业权重计算
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
        logger.info("theme_config.json 已更新（主营分析）")
        return True
    except Exception as e:
        logger.error("保存 theme_config.json 失败: %s", e)
        return False


def calc_industry_weight(
    trade_date: str,
) -> Dict[str, Dict[str, float]]:
    """计算每个主题的行业权重分布.

    基于主题内各股票的主营业务构成，汇总行业占比.

    Args:
        trade_date: 交易日.

    Returns:
        {theme_code: {industry: weight, ...}}
    """
    theme_config = _load_theme_config()
    results: Dict[str, Dict[str, float]] = {}

    for theme_code, cfg in theme_config.items():
        if not isinstance(cfg, dict):
            continue

        # 收集该主题所有股票
        all_stocks = []
        for key in ("leaders", "core_stocks", "secondary"):
            all_stocks.extend(cfg.get(key, []))

        if not all_stocks:
            continue

        # 统计每只股票的营收构成
        industry_sales: Dict[str, float] = defaultdict(float)
        total_sales = 0.0
        stock_count = 0

        for stock in all_stocks[:10]:  # 取前10只足够代表
            norm = normalize_code(stock)
            df = fetch_mainbz(norm, trade_date)
            if df.empty:
                continue

            stock_count += 1
            if "bz_sales" in df.columns:
                stocks_sales = df["bz_sales"].sum()
                total_sales += stocks_sales

                for _, row in df.iterrows():
                    item = str(row.get("bz_item", ""))
                    sales = float(row.get("bz_sales", 0))
                    if item and sales > 0:
                        industry_sales[item] += sales

        if not industry_sales or stock_count == 0:
            continue

        # 计算权重（百分比）
        total = sum(industry_sales.values()) or 1.0
        weights = {
            k: round(v / total * 100, 1)
            for k, v in sorted(industry_sales.items(), key=lambda x: -x[1])
            if v / total > 0.01  # 保留 > 1% 的行业
        }

        results[theme_code] = weights

    return results


def calc_purity(
    theme_code: str,
    theme_cfg: Dict[str, Any],
    trade_date: str,
) -> float:
    """计算主题纯度.

    纯度 = 主营中与主题相关的营收占比
    基于主题的 leaders/core_stocks 的主营构成来判断.

    Args:
        theme_code: 主题代码.
        theme_cfg: 主题配置.
        trade_date: 交易日.

    Returns:
        纯度 0~100.
    """
    # 获取主题关键词（作为判断"相关"的标准）
    keywords = set()
    for f in ("keywords", "core_keywords", "concept_keywords", "industry_keywords"):
        keywords.update(theme_cfg.get(f, []))

    if not keywords:
        return 50.0  # 默认

    # 提取关键词中的核心字（>=2个字）
    core_terms = set()
    for kw in keywords:
        if len(kw) >= 2:
            core_terms.add(kw)

    if not core_terms:
        return 50.0

    # 检查 leaders + core_stocks
    all_stocks = []
    for key in ("leaders", "core_stocks"):
        all_stocks.extend(theme_cfg.get(key, []))

    if not all_stocks:
        return 50.0

    # 计算主营中与主题匹配的比例
    related_sales = 0.0
    total_sales = 0.0

    for stock in all_stocks[:10]:
        norm = normalize_code(stock)
        df = fetch_mainbz(norm, trade_date)
        if df.empty:
            continue

        for _, row in df.iterrows():
            item = str(row.get("bz_item", ""))
            sales = float(row.get("bz_sales", 0))
            if sales <= 0:
                continue
            total_sales += sales
            # 检查行业项目是否包含核心术语
            if any(term in item for term in core_terms):
                related_sales += sales

    if total_sales <= 0:
        return 50.0

    purity = (related_sales / total_sales) * 100
    purity = max(0.0, min(100.0, round(purity, 1)))
    return purity


# ────────────────────────────────────────────────────────────
# 主编排
# ────────────────────────────────────────────────────────────

def run_business_analysis(trade_date: Optional[str] = None) -> Dict[str, Any]:
    """执行主营业务自动分析.

    Args:
        trade_date: 交易日期.

    Returns:
        执行摘要.
    """
    if trade_date is None:
        trade_date = get_trade_date()

    logger.info("=" * 60)
    logger.info("主营业务自动分析 [%s]", trade_date)
    logger.info("=" * 60)

    summary: Dict[str, Any] = {
        "trade_date": trade_date,
        "themes_analyzed": 0,
        "purities_calculated": 0,
        "config_updated": False,
        "purities": {},
    }

    theme_config = _load_theme_config()
    if not theme_config:
        return summary

    # 计算行业权重
    weights = calc_industry_weight(trade_date)
    summary["themes_analyzed"] = len(weights)

    # 计算纯度
    purities: Dict[str, float] = {}
    for code, cfg in theme_config.items():
        if not isinstance(cfg, dict):
            continue
        p = calc_purity(code, cfg, trade_date)
        purities[code] = p
        logger.info("  %s(%s): 纯度 %.1f%%", cfg.get("name_cn", ""), code, p)

    summary["purities_calculated"] = len(purities)
    summary["purities"] = purities

    # 更新配置：写入 purity 和 industry_weight
    updated = False
    for code, cfg in theme_config.items():
        if not isinstance(cfg, dict):
            continue
        if code in purities:
            old_purity = cfg.get("purity", 50)
            if abs(purities[code] - old_purity) > 3:
                cfg["purity"] = purities[code]
                updated = True

        if code in weights:
            cfg["industry_weight"] = {
                k: v for k, v in list(weights[code].items())[:10]
            }
            updated = True

    if updated:
        ok = _save_theme_config(theme_config)
        summary["config_updated"] = ok

    logger.info("-" * 60)
    logger.info("执行摘要:")
    logger.info("  分析主题: %d", summary["themes_analyzed"])
    logger.info("  纯度计算: %d", summary["purities_calculated"])
    logger.info("  配置更新: %s", summary["config_updated"])
    logger.info("=" * 60)

    return summary


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
    )
    run_business_analysis()
