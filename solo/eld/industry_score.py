"""
行业评分模块 — Industry Score

基于主题引擎的行业排名输出评分。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .config import get_config
from .models import IndustryScoreResult

logger = logging.getLogger(__name__)


def score_industry(ts_code: str, data_source: Any) -> IndustryScoreResult:
    """对个股所属行业进行评分

    从主题引擎获取行业排名，根据排名区间映射为百分制分数。

    Args:
        ts_code: 股票代码
        data_source: 数据源（需提供 get_industry_rank / theme_engine 接口）

    Returns:
        IndustryScoreResult: 行业评分结果
    """
    logic: list[str] = []
    cfg = get_config().industry

    # 获取行业排名
    industry_rank = 999
    theme_score = 0.0
    is_top_theme = False

    try:
        # 优先从数据源的 theme_engine 获取排名
        theme_engine = getattr(data_source, "theme_engine", None)
        if theme_engine is not None:
            rank_info = getattr(theme_engine, "get_industry_rank", None)
            if rank_info is not None:
                result = rank_info(ts_code)
                if result is not None:
                    industry_rank = result.get("rank", 999)
                    theme_score = result.get("score", 0.0)
                    logic.append(f"主题引擎行业排名: #{industry_rank}")
            else:
                logic.append("主题引擎无 get_industry_rank 接口")
        else:
            logic.append("数据源无 theme_engine，尝试其他接口")

        # 降级：直接从 data_source 获取行业排名
        if industry_rank == 999:
            direct_rank = getattr(data_source, "get_industry_rank", None)
            if direct_rank is not None:
                rank_val = direct_rank(ts_code)
                if rank_val is not None:
                    industry_rank = int(rank_val)
                    logic.append(f"数据源行业排名: #{industry_rank}")

        # 再降级：通过行业涨跌幅排序估算
        if industry_rank == 999:
            industry_list = getattr(data_source, "get_industry_performance", None)
            if industry_list is not None:
                perf = industry_list()
                if perf is not None and not perf.empty:
                    stock_industry = getattr(data_source, "get_stock_industry", None)
                    if stock_industry is not None:
                        ind = stock_industry(ts_code)
                        if ind is not None and ind in perf.index:
                            sorted_inds = perf.sort_values(ascending=False)
                            rank_pos = sorted_inds.index.get_loc(ind)
                            if isinstance(rank_pos, (int, float)):
                                industry_rank = int(rank_pos) + 1
                                theme_score = float(sorted_inds.iloc[rank_pos])
                                logic.append(f"行业涨跌幅估算排名: #{industry_rank}")

    except Exception as exc:
        logger.warning("获取行业排名失败 %s: %s", ts_code, exc)
        logic.append(f"获取行业排名异常: {exc}")

    # 根据排名映射分数
    if industry_rank <= cfg.top3_max_rank:
        score = cfg.top3_score
        is_top_theme = True
        logic.append(f"行业排名 TOP3（#{industry_rank}）→ {score}分")
    elif industry_rank <= cfg.top5_max_rank:
        score = cfg.top5_score
        is_top_theme = True
        logic.append(f"行业排名 TOP5（#{industry_rank}）→ {score}分")
    elif industry_rank <= cfg.top10_max_rank:
        score = cfg.top10_score
        logic.append(f"行业排名 TOP10（#{industry_rank}）→ {score}分")
    elif industry_rank <= 50:
        score = cfg.normal_score
        logic.append(f"行业排名中等（#{industry_rank}）→ {score}分")
    else:
        score = cfg.cold_score
        logic.append(f"行业排名靠后（#{industry_rank}）→ {score}分")

    return IndustryScoreResult(
        score=score,
        industry_rank=industry_rank,
        theme_score=theme_score,
        is_top_theme=is_top_theme,
        logic=logic,
    )
