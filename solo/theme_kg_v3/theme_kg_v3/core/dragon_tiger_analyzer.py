"""龙虎榜分析模块.

每日收盘后自动执行:
  1. 获取当日龙虎榜数据 (Tushare top_list)
  2. 获取机构/游资席位买卖明细 (Tushare top_inst)
  3. 按主题汇总龙虎榜数据，发现强势个股
  4. 更新主题的 leaders 和 core_stocks 字段
"""

from __future__ import annotations

import json
import logging
import time
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
from theme_kg_v3.core.classifier import ThemeClassifier
from theme_kg_v3.core.keyword_engine import KeywordEngine

logger = logging.getLogger(__name__)

# ── 缓存目录 ─────────────────────────────────────────────────
DT_CACHE_DIR = DAILY_CACHE_DIR / "dragon_tiger"
DT_CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ────────────────────────────────────────────────────────────
# 龙虎榜数据获取
# ────────────────────────────────────────────────────────────

def fetch_top_list(trade_date: str) -> pd.DataFrame:
    """获取当日龙虎榜列表.

    Args:
        trade_date: 交易日 YYYYMMDD.

    Returns:
        DataFrame: ts_code, name, close, pct_change, amount, ...
    """
    pro = get_pro()
    cache_file = DT_CACHE_DIR / f"top_list_{trade_date}.pkl"
    if cache_file.exists():
        try:
            df = pd.read_pickle(cache_file)
            if df is not None and not df.empty:
                return df
        except Exception:
            pass

    try:
        df = pro.top_list(trade_date=trade_date)
        time.sleep(0.12)
        if df is not None and not df.empty:
            # 标准化股票代码
            if "ts_code" in df.columns:
                df["ts_code"] = df["ts_code"].apply(normalize_code)
            df.to_pickle(cache_file)
            logger.info("获取龙虎榜: %d 只股票", len(df))
            return df
    except Exception as e:
        logger.warning("获取龙虎榜失败: %s", e)
    return pd.DataFrame()


def fetch_top_inst(ts_code: str, trade_date: str) -> pd.DataFrame:
    """获取个股龙虎榜机构席位明细.

    Args:
        ts_code: 股票代码.
        trade_date: 交易日.

    Returns:
        DataFrame: 机构买卖明细.
    """
    pro = get_pro()
    cache_file = DT_CACHE_DIR / f"top_inst_{ts_code}_{trade_date}.pkl"
    if cache_file.exists():
        try:
            df = pd.read_pickle(cache_file)
            if df is not None and not df.empty:
                return df
        except Exception:
            pass

    try:
        df = pro.top_inst(ts_code=ts_code, trade_date=trade_date)
        time.sleep(0.12)
        if df is not None and not df.empty:
            df.to_pickle(cache_file)
            return df
    except Exception as e:
        logger.debug("获取 %s 机构席位明细失败: %s", ts_code, e)
    return pd.DataFrame()


def fetch_top_flush(trade_date: str) -> pd.DataFrame:
    """获取龙虎榜每日明细（包含上榜原因）.

    Args:
        trade_date: 交易日.

    Returns:
        DataFrame: 龙虎榜完整明细.
    """
    pro = get_pro()
    try:
        df = pro.top_flush(trade_date=trade_date)
        time.sleep(0.12)
        if df is not None and not df.empty:
            if "ts_code" in df.columns:
                df["ts_code"] = df["ts_code"].apply(normalize_code)
            return df
    except Exception as e:
        logger.debug("获取龙虎榜明细失败: %s", e)
    return pd.DataFrame()


# ────────────────────────────────────────────────────────────
# 龙虎榜-主题关联分析
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
        logger.info("theme_config.json 已更新（龙虎榜分析）")
        return True
    except Exception as e:
        logger.error("保存 theme_config.json 失败: %s", e)
        return False


def _get_classifier() -> ThemeClassifier | None:
    """获取主题分类器实例."""
    try:
        ke = KeywordEngine(THEME_CONFIG_PATH)
        return ThemeClassifier(keyword_engine=ke)
    except Exception as e:
        logger.warning("初始化分类器失败: %s", e)
        return None


def classify_stock_to_theme(
    ts_code: str,
    stock_name: str,
) -> Optional[str]:
    """将个股归类到主题（简化版，基于关键词）."""
    try:
        ke = _get_classifier().keyword_engine if _get_classifier() else None
        if ke is None:
            return None

        # 使用 stock_name 快速匹配
        results = ke.quick_match(stock_name)
        if results and results[0].score > 20:
            return results[0].theme_code
    except Exception:
        pass
    return None


GroupedDT = Dict[str, Dict[str, Any]]


def analyze_dragon_tiger(trade_date: str) -> GroupedDT:
    """分析当日龙虎榜数据，按主题分组.

    Args:
        trade_date: 交易日.

    Returns:
        {theme_code: {stocks, total_buy, total_sell, leader_candidates, ...}}
    """
    df = fetch_top_list(trade_date)
    if df.empty:
        logger.info("当日无龙虎榜数据")
        return {}

    theme_config = _load_theme_config()
    grouped: GroupedDT = {}

    # 加载分类器用于主题匹配
    classifier = _get_classifier()

    for _, row in df.iterrows():
        ts_code = str(row.get("ts_code", ""))
        stock_name = str(row.get("name", ""))
        pct = float(row.get("pct_change", 0))
        amount = float(row.get("amount", 0)) / 100000  # 千元→亿元
        buy_amount = float(row.get("buy_amount", 0)) / 100000
        sell_amount = float(row.get("sell_amount", 0)) / 100000
        net_buy = buy_amount - sell_amount

        # 确定所属主题
        theme_code = None
        for code, cfg in theme_config.items():
            if not isinstance(cfg, dict):
                continue
            if ts_code in [normalize_code(c) for c in cfg.get("leaders", [])]:
                theme_code = code
                break
            if ts_code in [normalize_code(c) for c in cfg.get("core_stocks", [])]:
                theme_code = code
                break
            # 检查 secondary
            sec = cfg.get("secondary", [])
            if ts_code in [normalize_code(c) for c in sec]:
                theme_code = code
                break

        # 如果不在已知列表中，尝试用分类器
        if theme_code is None and classifier:
            try:
                results = classifier.keyword_engine.quick_match(stock_name)
                if results and results[0].score > 30:
                    theme_code = results[0].theme_code
            except Exception:
                pass

        if theme_code is None:
            continue

        if theme_code not in grouped:
            grouped[theme_code] = {
                "stocks": [],
                "total_buy": 0.0,
                "total_sell": 0.0,
                "total_net_buy": 0.0,
                "leader_candidates": [],
            }

        entry = {
            "ts_code": ts_code,
            "name": stock_name,
            "pct_change": pct,
            "amount": amount,
            "buy": buy_amount,
            "sell": sell_amount,
            "net_buy": net_buy,
        }
        grouped[theme_code]["stocks"].append(entry)
        grouped[theme_code]["total_buy"] += buy_amount
        grouped[theme_code]["total_sell"] += sell_amount
        grouped[theme_code]["total_net_buy"] += net_buy

        # 净买入大且涨停的作为龙头候选
        if net_buy > 0.5 and pct >= 9.8:
            grouped[theme_code]["leader_candidates"].append(ts_code)

    return grouped


# ────────────────────────────────────────────────────────────
# 配置更新
# ────────────────────────────────────────────────────────────

def update_leaders_from_dragon_tiger(
    theme_config: Dict[str, Any],
    dt_grouped: GroupedDT,
) -> Dict[str, Any]:
    """根据龙虎榜数据更新主题的 leaders 列表.

    规则:
      - 连续出现龙虎榜且净买入大的股票提升排名
      - 机构席位大额买入的优先

    Args:
        theme_config: 原始配置.
        dt_grouped: 龙虎榜分组数据.

    Returns:
        更新后的配置.
    """
    config = theme_config.copy()

    for theme_code, data in dt_grouped.items():
        if theme_code not in config:
            continue

        cfg = config[theme_code]
        current_leaders = set(normalize_code(c) for c in cfg.get("leaders", []))

        # 检查龙头候选：是否已有新的强势股
        for candidate in data.get("leader_candidates", []):
            if candidate not in current_leaders:
                # 添加到 leaders 列表（最多5个）
                leaders_list = cfg.setdefault("leaders", [])
                if len(leaders_list) < 5:
                    leaders_list.append(candidate)
                    logger.info(
                        "  %s: 新增龙头候选 %s (龙虎榜净买入)",
                        cfg.get("name_cn", ""), candidate,
                    )

        # 如果有净卖出很大的股票，考虑移除
        for stock in data.get("stocks", []):
            if stock["net_buy"] < -1.0:  # 净卖出 > 1亿
                code = stock["ts_code"]
                if code in current_leaders:
                    # 不直接移除，但记录警告
                    logger.warning(
                        "  %s: 龙头 %s 龙虎榜净卖出 %.2f亿",
                        cfg.get("name_cn", ""), code, stock["net_buy"],
                    )

    return config


# ────────────────────────────────────────────────────────────
# 主编排
# ────────────────────────────────────────────────────────────

def run_dragon_tiger_analysis(trade_date: Optional[str] = None) -> Dict[str, Any]:
    """执行龙虎榜自动分析.

    Args:
        trade_date: 交易日期.

    Returns:
        执行摘要.
    """
    if trade_date is None:
        trade_date = get_trade_date()

    logger.info("=" * 60)
    logger.info("龙虎榜自动分析 [%s]", trade_date)
    logger.info("=" * 60)

    summary: Dict[str, Any] = {
        "trade_date": trade_date,
        "total_stocks": 0,
        "themes_with_dt": 0,
        "leaders_added": 0,
        "config_updated": False,
    }

    # 1. 分析龙虎榜
    dt_grouped = analyze_dragon_tiger(trade_date)
    if not dt_grouped:
        logger.info("当日无主题关联龙虎榜数据")
        return summary

    summary["themes_with_dt"] = len(dt_grouped)
    summary["total_stocks"] = sum(len(d.get("stocks", [])) for d in dt_grouped.values())

    logger.info("龙虎榜关联主题: %d 个", len(dt_grouped))
    for theme_code, data in dt_grouped.items():
        net = data["total_net_buy"]
        logger.info(
            "  %s: %d 只, 净买入 %.2f 亿, 龙头候选 %s",
            theme_code, len(data["stocks"]), net,
            data.get("leader_candidates", []),
        )

    # 2. 更新配置
    theme_config = _load_theme_config()
    updated = update_leaders_from_dragon_tiger(theme_config, dt_grouped)

    added_count = sum(
        len(cfg.get("leaders", []))
        for cfg in updated.values()
        if isinstance(cfg, dict)
    )
    original_count = sum(
        len(cfg.get("leaders", []))
        for cfg in theme_config.values()
        if isinstance(cfg, dict)
    )
    summary["leaders_added"] = added_count - original_count

    if summary["leaders_added"] > 0:
        ok = _save_theme_config(updated)
        summary["config_updated"] = ok

    logger.info("-" * 60)
    logger.info("执行摘要:")
    logger.info("  龙虎榜股票: %d 只", summary["total_stocks"])
    logger.info("  涉及主题: %d 个", summary["themes_with_dt"])
    logger.info("  新增龙头: %d 个", summary["leaders_added"])
    logger.info("  配置更新: %s", summary["config_updated"])
    logger.info("=" * 60)

    return summary


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
    )
    run_dragon_tiger_analysis()
