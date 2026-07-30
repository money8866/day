"""资金流向分析模块.

每日收盘后自动执行:
  1. 获取个股/行业资金流向 (Tushare moneyflow)
  2. 按主题汇总资金流入/流出
  3. 计算主题轮动指标 (rotation)
  4. 更新 theme_config.json 中的 rotation 字段
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

FLOW_CACHE_DIR = DAILY_CACHE_DIR / "flow_analysis"
FLOW_CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ────────────────────────────────────────────────────────────
# 资金流向数据获取
# ────────────────────────────────────────────────────────────

def fetch_moneyflow(trade_date: str) -> pd.DataFrame:
    """获取个股资金流向.

    Args:
        trade_date: 交易日 YYYYMMDD.

    Returns:
        DataFrame: ts_code, name, buy_sm, buy_md, buy_lg, buy_elg, ...
    """
    pro = get_pro()
    cache_file = FLOW_CACHE_DIR / f"moneyflow_{trade_date}.pkl"
    if cache_file.exists():
        try:
            df = pd.read_pickle(cache_file)
            if df is not None and not df.empty:
                return df
        except Exception:
            pass

    try:
        df = pro.moneyflow(trade_date=trade_date)
        time.sleep(0.12)
        if df is not None and not df.empty:
            if "ts_code" in df.columns:
                df["ts_code"] = df["ts_code"].apply(normalize_code)
            df.to_pickle(cache_file)
            logger.info("获取资金流向: %d 只股票", len(df))
            return df
    except Exception as e:
        logger.warning("获取资金流向失败: %s", e)
    return pd.DataFrame()


def fetch_moneyflow_industry(trade_date: str) -> pd.DataFrame:
    """获取行业资金流向（申万行业）.

    Args:
        trade_date: 交易日.

    Returns:
        DataFrame: 行业资金流向.
    """
    pro = get_pro()
    try:
        df = pro.moneyflow_industry(trade_date=trade_date)
        time.sleep(0.12)
        if df is not None and not df.empty:
            df = df.sort_values("net_amount", ascending=False)
            return df
    except Exception as e:
        logger.debug("获取行业资金流向失败: %s", e)
    return pd.DataFrame()


# ────────────────────────────────────────────────────────────
# 主题资金汇总
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
        logger.info("theme_config.json 已更新（资金分析）")
        return True
    except Exception as e:
        logger.error("保存 theme_config.json 失败: %s", e)
        return False


def calc_theme_moneyflow(trade_date: str) -> Dict[str, Dict[str, Any]]:
    """计算每个主题的资金流向汇总.

    Args:
        trade_date: 交易日.

    Returns:
        {theme_code: {net_amount, buy_lg, buy_elg, stock_flow, ...}}
    """
    df = fetch_moneyflow(trade_date)
    if df.empty:
        return {}

    theme_config = _load_theme_config()
    results: Dict[str, Dict[str, Any]] = {}

    for theme_code, cfg in theme_config.items():
        if not isinstance(cfg, dict):
            continue

        # 收集该主题所有关联股票
        theme_stocks = set()
        for key in ("leaders", "core_stocks", "secondary"):
            codes = cfg.get(key, [])
            for c in codes:
                theme_stocks.add(normalize_code(c))

        if not theme_stocks:
            continue

        # 从资金流向数据中筛选
        theme_flow = df[df["ts_code"].isin(theme_stocks)].copy()
        if theme_flow.empty:
            continue

        # 汇总资金数据
        total_net = theme_flow.get("net_mf_amount", pd.Series(0)).sum()  # 万元
        buy_lg = theme_flow.get("buy_lg_amount", pd.Series(0)).sum()  # 大单买入
        buy_elg = theme_flow.get("buy_elg_amount", pd.Series(0)).sum()  # 超大单买入
        sell_lg = theme_flow.get("sell_lg_amount", pd.Series(0)).sum()
        sell_elg = theme_flow.get("sell_elg_amount", pd.Series(0)).sum()

        # 个股资金流向详情
        stock_flow = []
        for _, row in theme_flow.iterrows():
            stock_flow.append({
                "ts_code": row.get("ts_code", ""),
                "name": row.get("name", ""),
                "net_amount": float(row.get("net_mf_amount", 0)),
                "buy_lg": float(row.get("buy_lg_amount", 0)),
                "buy_elg": float(row.get("buy_elg_amount", 0)),
            })

        # 排序：净流入大的在前
        stock_flow.sort(key=lambda x: -x["net_amount"])

        # 主力净额（大单+超大单）
        main_force_net = (buy_lg + buy_elg) - (sell_lg + sell_elg)

        results[theme_code] = {
            "total_net_amount": round(float(total_net), 2),
            "main_force_net": round(float(main_force_net), 2),
            "buy_lg": round(float(buy_lg), 2),
            "buy_elg": round(float(buy_elg), 2),
            "stock_count": len(theme_flow),
            "positive_stocks": int((theme_flow.get("net_mf_amount", pd.Series(0)) > 0).sum()),
            "top_inflow_stock": stock_flow[0]["ts_code"] if stock_flow else "",
            "stock_flow": stock_flow[:5],  # 前5只
        }

    return results


# ────────────────────────────────────────────────────────────
# 主题轮动计算
# ────────────────────────────────────────────────────────────

def calc_theme_rotation(
    moneyflow: Dict[str, Dict[str, Any]],
    lookback_days: int = 5,
) -> Dict[str, float]:
    """计算主题轮动指标.

    基于资金流和趋势判断主题轮动状态:
      - 资金大幅流入 + 主力净买 → 轮入 (rotation > 0)
      - 资金流出 → 轮出 (rotation < 0)

    Args:
        moneyflow: calc_theme_moneyflow 的结果.
        lookback_days: 回溯天数（暂未使用，预留）.

    Returns:
        {theme_code: rotation_score (-100 ~ +100)}
    """
    if not moneyflow:
        return {}

    # 计算所有主题的资金净流
    flows = []
    for theme_code, data in moneyflow.items():
        flows.append((theme_code, data["main_force_net"]))

    if not flows:
        return {}

    # 归一化到 -100 ~ +100
    max_abs = max(abs(f) for _, f in flows) or 1.0
    rotations: Dict[str, float] = {}
    for theme_code, net in flows:
        score = (net / max_abs) * 100
        score = max(-100.0, min(100.0, round(score, 1)))
        rotations[theme_code] = score

    return rotations


# ────────────────────────────────────────────────────────────
# 主编排
# ────────────────────────────────────────────────────────────

def run_flow_analysis(trade_date: Optional[str] = None) -> Dict[str, Any]:
    """执行资金流向自动分析.

    Args:
        trade_date: 交易日期.

    Returns:
        执行摘要.
    """
    if trade_date is None:
        trade_date = get_trade_date()

    logger.info("=" * 60)
    logger.info("资金流向自动分析 [%s]", trade_date)
    logger.info("=" * 60)

    summary: Dict[str, Any] = {
        "trade_date": trade_date,
        "themes_analyzed": 0,
        "rotation_updated": 0,
        "config_updated": False,
    }

    # 1. 计算主题资金流向
    moneyflow = calc_theme_moneyflow(trade_date)
    if not moneyflow:
        logger.info("当日无主题关联资金流向数据")
        return summary

    summary["themes_analyzed"] = len(moneyflow)

    logger.info("主题资金流向 (%d 个):", len(moneyflow))
    for theme_code, data in sorted(
        moneyflow.items(), key=lambda x: -x[1]["main_force_net"],
    ):
        symbol = "🟢" if data["main_force_net"] > 0 else "🔴"
        logger.info(
            "  %s %s: 主力净 %.0f 万 | 净流入 %.0f 万 | %d/%d 只流入",
            symbol, theme_code,
            data["main_force_net"],
            data["total_net_amount"],
            data["positive_stocks"], data["stock_count"],
        )

    # 2. 计算轮动指标
    rotations = calc_theme_rotation(moneyflow)

    # 3. 更新配置中的 rotation 字段
    theme_config = _load_theme_config()
    updated_count = 0
    for theme_code, rot in rotations.items():
        if theme_code in theme_config:
            cfg = theme_config[theme_code]
            if isinstance(cfg, dict):
                old_rot = cfg.get("rotation", 0)
                if abs(rot - old_rot) > 1:  # 变化 > 1 才更新
                    cfg["rotation"] = rot
                    updated_count += 1

    summary["rotation_updated"] = updated_count

    if updated_count > 0:
        # 也更新 flow_summary 到配置
        theme_config["_flow"] = {
            "last_updated": trade_date,
            "themes": {
                k: {
                    "net_amount": v["total_net_amount"],
                    "main_force": v["main_force_net"],
                    "rotation": rotations.get(k, 0),
                }
                for k, v in moneyflow.items()
            },
        }
        ok = _save_theme_config(theme_config)
        summary["config_updated"] = ok

    # 4. 输出轮动排名
    sorted_rot = sorted(rotations.items(), key=lambda x: -x[1])
    logger.info("")
    logger.info("主题轮动排名 (Top 5):")
    for theme_code, rot in sorted_rot[:5]:
        name = theme_config.get(theme_code, {}).get("name_cn", theme_code)
        logger.info("  %s(%s): rotation=%+.1f", name, theme_code, rot)

    logger.info("-" * 60)
    logger.info("执行摘要:")
    logger.info("  分析主题: %d", summary["themes_analyzed"])
    logger.info("  轮动更新: %d 个", summary["rotation_updated"])
    logger.info("  配置更新: %s", summary["config_updated"])
    logger.info("=" * 60)

    return summary


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
    )
    run_flow_analysis()
