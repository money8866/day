"""概念热度自动分析模块.

每日收盘后自动执行:
  1. 获取东方财富概念板块日线数据 (Tushare concept)
  2. 获取同花顺概念板块日线数据 (Tushare ths_concept)
  3. 计算各主题关联概念的涨幅/成交量/热度变化
  4. 发现新兴概念标签，更新主题的 concept_keywords / eastmoney_concepts / ths_concepts
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from theme_kg_v3.config.settings import (
    CONFIG_DIR,
    DAILY_CACHE_DIR,
    THEME_CONFIG_PATH,
)
from theme_kg_v3.core.etf_analyzer import get_trade_date, init_tushare, get_pro, normalize_code

logger = logging.getLogger(__name__)

# ── 缓存目录 ─────────────────────────────────────────────────
CONCEPT_CACHE_DIR = DAILY_CACHE_DIR / "concept_analysis"
CONCEPT_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 概念名称 → {ts_code, pct_change, total_mv} 缓存（从 dc_index 构建）
_concept_cache: dict[str, dict[str, str | float]] = {}


# ────────────────────────────────────────────────────────────
# 东方财富概念数据获取（使用 dc_index 接口）
# ────────────────────────────────────────────────────────────

def _load_concept_cache(trade_date: str) -> pd.DataFrame:
    """从 pickle 缓存加载概念列表，并同步到 _concept_cache."""
    cache_file = CONCEPT_CACHE_DIR / f"dc_concept_list_{trade_date}.pkl"
    if cache_file.exists():
        try:
            df = pd.read_pickle(cache_file)
            if df is not None and not df.empty:
                logger.debug("概念列表缓存命中: %d 个", len(df))
                _sync_concept_cache(df)
                return df
        except Exception:
            pass
    return pd.DataFrame()


def _save_concept_cache(df: pd.DataFrame, trade_date: str) -> None:
    """保存概念列表到 pickle 缓存."""
    cache_file = CONCEPT_CACHE_DIR / f"dc_concept_list_{trade_date}.pkl"
    try:
        df.to_pickle(cache_file)
        logger.debug("概念列表已缓存: %s", cache_file)
    except Exception as e:
        logger.debug("缓存概念列表失败: %s", e)


def _sync_concept_cache(df: pd.DataFrame) -> None:
    """将 dc_index DataFrame 同步到内存缓存."""
    global _concept_cache
    for _, row in df.iterrows():
        name = str(row.get("name", ""))
        if not name:
            continue
        _concept_cache[name] = {
            "ts_code": str(row.get("ts_code", "")),
            "pct_change": float(row.get("pct_change", 0) or 0),
            "total_mv": float(row.get("total_mv", 0) or 0),
        }


def fetch_concept_list(trade_date: str) -> pd.DataFrame:
    """获取东方财富全量概念板块列表.

    使用 pro.dc_index(idx_type="概念板块") 接口，
    带 pickle 文件缓存。

    Args:
        trade_date: 交易日 YYYYMMDD.

    Returns:
        DataFrame: ts_code, name, pct_change, total_mv 等
    """
    # 1. 读缓存
    cached = _load_concept_cache(trade_date)
    if not cached.empty:
        return cached

    pro = get_pro()
    try:
        df = pro.dc_index(trade_date=trade_date, idx_type="概念板块")
        time.sleep(0.12)
        if df is not None and not df.empty:
            logger.info("获取东方财富概念板块: %d 个", len(df))
            _sync_concept_cache(df)
            _save_concept_cache(df, trade_date)
            return df
    except Exception as e:
        logger.warning("获取东方财富概念列表失败: %s", e)
    return pd.DataFrame()


def fetch_concept_daily(concept_name: str, trade_date: str) -> pd.Series | None:
    """获取单只概念的日线行情（从 _concept_cache 直接查，零 API 调用）.

    dc_index 接口已返回 pct_change 和 total_mv，
    无需再调 concept_daily / dc_member + daily。

    Args:
        concept_name: 概念名称 (如 "CPO概念").
        trade_date: 交易日 YYYYMMDD.

    Returns:
        Series 或 None.
    """
    info = _concept_cache.get(concept_name)
    if not info:
        logger.debug("概念 %s 未在缓存中找到", concept_name)
        return None

    return pd.Series({
        "pct_change": info["pct_change"],
        "amount": info["total_mv"],       # 用总市值近似
        "vol_ratio": 1.0,
    })





# ────────────────────────────────────────────────────────────
# 同花顺概念数据
# ────────────────────────────────────────────────────────────

def fetch_ths_concept_list() -> pd.DataFrame:
    """获取同花顺全量概念板块列表.

    参考 theme_trend_sentiment_score.get_ths_members() 的调用方式，
    使用 pro.ths_index(exchange='A', type='N') 获取概念列表。
    """
    pro = get_pro()
    try:
        df = pro.ths_index(exchange='A', type='N', fields='ts_code,name,count,list_date')
        time.sleep(0.12)
        if df is not None and not df.empty:
            logger.info("获取同花顺概念板块: %d 个", len(df))
            return df
    except Exception as e:
        logger.warning("获取同花顺概念列表失败: %s", e)
    return pd.DataFrame()


_ths_name_to_code: Optional[Dict[str, str]] = None


def _get_ths_name_to_code() -> Dict[str, str]:
    """构建同花顺概念名称→ts_code映射（带缓存）.

    参考 theme_trend_sentiment_score.get_ths_members() 的调用方式。
    """
    global _ths_name_to_code
    if _ths_name_to_code is not None:
        return _ths_name_to_code
    df = fetch_ths_concept_list()
    if df is not None and not df.empty and "name" in df.columns:
        _ths_name_to_code = dict(zip(df["name"], df["ts_code"]))
    else:
        _ths_name_to_code = {}
    return _ths_name_to_code


def fetch_ths_concept_daily(concept_name: str, trade_date: str) -> pd.Series | None:
    """获取同花顺概念日线.

    先通过 ths_index 获取名称→代码映射，再用 ts_code 调用 ths_daily。
    """
    name_map = _get_ths_name_to_code()
    ts_code = name_map.get(concept_name)
    if not ts_code:
        logger.debug("同花顺概念 %s 未找到对应 ts_code", concept_name)
        return None
    pro = get_pro()
    try:
        df = pro.ths_daily(ts_code=ts_code, start_date=trade_date, end_date=trade_date)
        time.sleep(0.12)
        if df is not None and not df.empty:
            return df.iloc[0]
    except Exception as e:
        logger.warning("获取同花顺概念 %s(%s) 日线失败: %s", concept_name, ts_code, e)
    return None


# ────────────────────────────────────────────────────────────
# 概念-主题热度匹配
# ────────────────────────────────────────────────────────────

def _load_theme_config() -> Dict[str, Any]:
    """加载 theme_config.json."""
    if not THEME_CONFIG_PATH.exists():
        return {}
    with open(THEME_CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save_theme_config(config: Dict[str, Any]) -> bool:
    """保存 theme_config.json."""
    try:
        with open(THEME_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        logger.info("theme_config.json 已更新（概念分析）")
        return True
    except Exception as e:
        logger.error("保存 theme_config.json 失败: %s", e)
        return False


def calc_concept_heat_for_theme(
    theme_code: str,
    theme_cfg: Dict[str, Any],
    trade_date: str,
) -> Dict[str, Any]:
    """计算指定主题关联概念的热度变化.

    分析每个主题的 eastmoney_concepts / ths_concepts 中
    概念板块的当日涨幅、成交量变化，返回热度评分.

    Args:
        theme_code: 主题代码.
        theme_cfg: 主题配置.
        trade_date: 交易日.

    Returns:
        {heat_score, rising_concepts, new_concepts, suggestions}
    """
    em_concepts = theme_cfg.get("eastmoney_concepts", [])
    ths_concepts = theme_cfg.get("ths_concepts", [])

    all_concept_scores: List[Dict[str, Any]] = []
    rising_concepts: List[str] = []
    heat_score = 50.0  # 基准分

    # 分析东方财富概念
    for concept_name in em_concepts:
        score_info = _single_concept_score(concept_name, trade_date, source="eastmoney")
        if score_info:
            all_concept_scores.append(score_info)
            if score_info["pct_change"] is not None and score_info["pct_change"] > 2.0:
                rising_concepts.append(concept_name)

    # 分析同花顺概念
    for concept_name in ths_concepts:
        score_info = _single_concept_score(concept_name, trade_date, source="ths")
        if score_info and score_info not in all_concept_scores:
            all_concept_scores.append(score_info)
            if score_info["pct_change"] is not None and score_info["pct_change"] > 2.0:
                rising_concepts.append(concept_name)

    if not all_concept_scores:
        return {"heat_score": 50.0, "rising_concepts": [], "new_concepts": [], "details": []}

    # 综合热度评分
    avg_pct = sum(s.get("pct_change", 0) or 0 for s in all_concept_scores) / len(all_concept_scores)
    heat_score = 50.0 + avg_pct * 5  # 每涨1%加5分
    heat_score = max(0.0, min(100.0, heat_score))

    # 发现新概念: 检查 theme_cfg 中是否已有
    new_concepts = _discover_new_concepts(theme_code, theme_cfg, trade_date)

    return {
        "heat_score": round(heat_score, 1),
        "rising_concepts": rising_concepts,
        "new_concepts": new_concepts[:3],  # 最多推荐3个
        "details": all_concept_scores[:10],
    }


def _single_concept_score(
    concept_name: str,
    trade_date: str,
    source: str = "eastmoney",
) -> Dict[str, Any] | None:
    """计算单只概念的评分."""
    try:
        if source == "eastmoney":
            series = fetch_concept_daily(concept_name, trade_date)
        else:
            series = fetch_ths_concept_daily(concept_name, trade_date)

        if series is None:
            return None

        pct = series.get("pct_change")
        amount = series.get("amount", 0)
        vol_ratio = series.get("vol_ratio", 1.0)

        return {
            "name": concept_name,
            "source": source,
            "pct_change": float(pct) if pct else 0.0,
            "amount": float(amount) if amount else 0.0,
            "vol_ratio": float(vol_ratio) if vol_ratio else 1.0,
        }
    except Exception as e:
        logger.debug("概念 %s 评分失败: %s", concept_name, e)
        return None


_concept_list_cache: Optional[pd.DataFrame] = None


def _get_concept_list_cached(trade_date: str) -> pd.DataFrame:
    """获取并缓存东方财富概念列表（避免重复请求）."""
    global _concept_list_cache
    if _concept_list_cache is not None and not _concept_list_cache.empty:
        return _concept_list_cache
    df = fetch_concept_list(trade_date)
    if not df.empty:
        _concept_list_cache = df
    return df


def _discover_new_concepts(
    theme_code: str,
    theme_cfg: Dict[str, Any],
    trade_date: str,
) -> List[str]:
    """发现与主题相关的新兴概念标签.

    通过概念列表 + 关键词匹配，推荐新概念标签.

    Args:
        theme_code: 主题代码.
        theme_cfg: 主题配置.
        trade_date: 交易日.

    Returns:
        推荐的新概念列表.
    """
    pro = get_pro()
    new_concepts: List[str] = []
    existing = set(theme_cfg.get("eastmoney_concepts", []) + theme_cfg.get("ths_concepts", []))

    try:
        # 先获取全量概念列表（不含行情）
        concept_df = _get_concept_list_cached(trade_date)
        if concept_df.empty:
            return []

        # 获取主题关键词
        keywords = set(
            theme_cfg.get("keywords", [])
            + theme_cfg.get("core_keywords", [])
            + theme_cfg.get("concept_keywords", [])
        )
        theme_name = theme_cfg.get("name_cn", "")

        # 筛选概念名称匹配主题关键词的候选
        candidates = []
        for _, row in concept_df.iterrows():
            cname = str(row.get("name", ""))
            if not cname or cname in existing:
                continue
            # 关键词匹配
            for kw in keywords:
                if len(kw) >= 2 and kw in cname:
                    candidates.append(cname)
                    break
            else:
                # 反向匹配：概念名在主题名中
                if theme_name and cname in theme_name:
                    candidates.append(cname)

        if not candidates:
            return []

        # 对候选概念获取日线行情检查涨幅（最多查5个）
        for cname in candidates[:5]:
            try:
                series = fetch_concept_daily(cname, trade_date)
                if series is None:
                    continue
                pct = float(series.get("pct_change", 0) or 0)
                if pct > 3.0:
                    new_concepts.append(cname)
            except Exception:
                continue
    except Exception as e:
        logger.debug("发现新概念异常: %s", e)

    return list(dict.fromkeys(new_concepts))


# ────────────────────────────────────────────────────────────
# 主编排
# ────────────────────────────────────────────────────────────

def run_concept_analysis(trade_date: Optional[str] = None) -> Dict[str, Any]:
    """执行概念热度自动分析.

    Args:
        trade_date: 交易日期 YYYYMMDD（自动判定）.

    Returns:
        执行摘要.
    """
    if trade_date is None:
        trade_date = get_trade_date()

    logger.info("=" * 60)
    logger.info("概念热度自动分析 [%s]", trade_date)
    logger.info("=" * 60)

    summary: Dict[str, Any] = {
        "trade_date": trade_date,
        "themes_analyzed": 0,
        "concepts_checked": 0,
        "new_concepts_found": 0,
        "config_updated": False,
        "suggestions": [],
    }

    theme_config = _load_theme_config()
    if not theme_config:
        logger.warning("theme_config.json 为空")
        return summary

    total_new = 0
    for code, cfg in theme_config.items():
        if not isinstance(cfg, dict):
            continue
        result = calc_concept_heat_for_theme(code, cfg, trade_date)
        summary["concepts_checked"] += len(result.get("details", []))

        # 如果有新概念推荐，添加到配置
        new_items = result.get("new_concepts", [])
        if new_items:
            existing_em = set(cfg.get("eastmoney_concepts", []))
            existing_ths = set(cfg.get("ths_concepts", []))
            added = []
            for nc in new_items:
                if nc not in existing_em and nc not in existing_ths:
                    # 默认添加到 eastmoney_concepts
                    cfg.setdefault("eastmoney_concepts", []).append(nc)
                    # 也添加到 concept_keywords
                    if nc not in cfg.get("concept_keywords", []):
                        cfg.setdefault("concept_keywords", []).append(nc)
                    added.append(nc)
            if added:
                total_new += len(added)
                logger.info("  %s(%s): 新增概念 %s", cfg.get("name_cn", ""), code, added)
                summary["suggestions"].append(f"{code}: +{added}")

        # 记录热度异常的概念（涨幅 > 5%）
        for detail in result.get("details", []):
            if detail.get("pct_change", 0) > 5.0:
                logger.info(
                    "  %s 概念热度异常: %s 涨幅 %.1f%%",
                    cfg.get("name_cn", ""),
                    detail.get("name", ""),
                    detail.get("pct_change", 0),
                )

    summary["themes_analyzed"] = len(theme_config)
    summary["new_concepts_found"] = total_new

    if total_new > 0:
        ok = _save_theme_config(theme_config)
        summary["config_updated"] = ok
    else:
        logger.info("无新概念发现，无需更新配置")

    logger.info("-" * 60)
    logger.info("执行摘要:")
    logger.info("  分析主题: %d", summary["themes_analyzed"])
    logger.info("  检查概念: %d", summary["concepts_checked"])
    logger.info("  新增概念: %d", summary["new_concepts_found"])
    logger.info("  配置更新: %s", summary["config_updated"])
    logger.info("=" * 60)

    return summary


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
    )
    run_concept_analysis()
