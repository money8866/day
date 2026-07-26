"""龙头/核心/补涨识别算法."""
from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from theme_kg_v3.schema.dataclasses import LeaderAnalysisResult
from theme_kg_v3.config.settings import LEADER_THRESHOLDS

logger = logging.getLogger(__name__)

# ── 分类常量 ────────────────────────────────────────────────
TYPE_LEADER = "leader"
TYPE_CORE = "core"
TYPE_FOLLOWER = "follower"
TYPE_CATCH_UP = "catch_up"
TYPE_ELIMINATED = "eliminated"


class LeaderIdentifier:
    """龙头/核心/补涨识别器.

    对主题内成分股的多维度数据进行分析，将股票分类为：
        - leader（龙头）: 连续涨停、涨幅领先、率先启动
        - core（核心/中军）: 大盘稳定、机构重仓、高相关性
        - follower（跟风）: 跟随龙头、低弹性
        - catch_up（补涨）: 前期滞涨、近期启动
        - eliminated（淘汰）: 交易不活跃、大幅跑输
    """

    def __init__(self) -> None:
        """初始化识别器，加载龙头识别阈值配置."""
        self.thresholds = LEADER_THRESHOLDS
        logger.info(
            "LeaderIdentifier initialized: leader_min_consecutive_limit_up=%s, "
            "core_min_market_cap=%s, eliminated_max_days=%s",
            self.thresholds.get("leader_min_consecutive_limit_up", 3),
            self.thresholds.get("core_min_market_cap_billion", 10.0),
            self.thresholds.get("eliminated_max_days_no_activity", 60),
        )

    # ──────────────────────────────────────────────
    # 主入口
    # ──────────────────────────────────────────────

    def identify(
        self,
        theme_code: str,
        theme_name: str,
        theme_stocks: List[Dict[str, Any]],
    ) -> LeaderAnalysisResult:
        """对主题内全部成分股执行龙头识别分析.

        Args:
            theme_code: 主题代码.
            theme_name: 主题中文名称.
            theme_stocks: 主题成分股列表，每只股票字典包含:
                stock_code, stock_name, consecutive_limit_up,
                cumulative_return_20d, cumulative_return_60d,
                market_cap_billion, turnover_rate, is_leader,
                leader_type, volume_ratio, avg_return_5d,
                correlation_with_theme.

        Returns:
            LeaderAnalysisResult 包含各类股票的详细列表.
        """
        if not theme_stocks:
            return LeaderAnalysisResult(
                theme_code=theme_code,
                theme_name=theme_name,
                analysis_date=date.today(),
            )

        # 计算主题整体统计量
        theme_stats = self._compute_theme_stats(theme_stocks)

        # 为每只股票计算各类评分
        classifications: Dict[str, List[Dict]] = {
            TYPE_LEADER: [],
            TYPE_CORE: [],
            TYPE_FOLLOWER: [],
            TYPE_CATCH_UP: [],
            TYPE_ELIMINATED: [],
        }

        for stock in theme_stocks:
            classification = self._classify_single_stock(stock, theme_stats)
            if classification is not None:
                type_, record = classification
                classifications[type_].append(record)

        # 按评分排序，限制最大数量
        classifications[TYPE_LEADER] = self._rank_by_score(
            [(s, s.get("_score", 0.0)) for s in classifications[TYPE_LEADER]],
            max_count=3,
        )
        classifications[TYPE_CORE] = self._rank_by_score(
            [(s, s.get("_score", 0.0)) for s in classifications[TYPE_CORE]],
            max_count=5,
        )
        classifications[TYPE_FOLLOWER] = self._rank_by_score(
            [(s, s.get("_score", 0.0)) for s in classifications[TYPE_FOLLOWER]],
            max_count=20,
        )
        classifications[TYPE_CATCH_UP] = self._rank_by_score(
            [(s, s.get("_score", 0.0)) for s in classifications[TYPE_CATCH_UP]],
            max_count=10,
        )
        classifications[TYPE_ELIMINATED] = self._rank_by_score(
            [(s, s.get("_score", 0.0)) for s in classifications[TYPE_ELIMINATED]],
            max_count=50,
        )

        # 调整冲突分类
        classifications = self._adjust_classifications(classifications)

        return LeaderAnalysisResult(
            theme_code=theme_code,
            theme_name=theme_name,
            leaders=classifications[TYPE_LEADER],
            cores=classifications[TYPE_CORE],
            followers=classifications[TYPE_FOLLOWER],
            catch_up_candidates=classifications[TYPE_CATCH_UP],
            eliminated=classifications[TYPE_ELIMINATED],
            analysis_date=date.today(),
        )

    # ──────────────────────────────────────────────
    # 主题统计
    # ──────────────────────────────────────────────

    @staticmethod
    def _compute_theme_stats(
        theme_stocks: List[Dict[str, Any]],
    ) -> Dict[str, float]:
        """计算主题整体统计量，用于各评分函数中的基准值.

        Args:
            theme_stocks: 成分股列表.

        Returns:
            统计量字典.
        """
        n = len(theme_stocks)
        if n == 0:
            return {
                "avg_return_20d": 0.0,
                "avg_return_5d": 0.0,
                "avg_volume_ratio": 0.0,
                "avg_market_cap_billion": 0.0,
                "median_market_cap_billion": 0.0,
                "max_consecutive_limit_up": 0,
                "avg_correlation": 0.0,
                "total_market_cap_billion": 0.0,
                "stock_count": 0,
            }

        returns_20d = [s.get("cumulative_return_20d", 0.0) or 0.0 for s in theme_stocks]
        returns_5d = [s.get("avg_return_5d", 0.0) or 0.0 for s in theme_stocks]
        volumes = [s.get("volume_ratio", 0.0) or 0.0 for s in theme_stocks]
        market_caps = [s.get("market_cap_billion", 0.0) or 0.0 for s in theme_stocks]
        correlations = [s.get("correlation_with_theme", 0.0) or 0.0 for s in theme_stocks]
        limit_ups = [s.get("consecutive_limit_up", 0) or 0 for s in theme_stocks]

        sorted_caps = sorted(market_caps)
        median_cap = sorted_caps[n // 2] if sorted_caps else 0.0

        return {
            "avg_return_20d": sum(returns_20d) / n,
            "avg_return_5d": sum(returns_5d) / n,
            "avg_volume_ratio": sum(volumes) / n,
            "avg_market_cap_billion": sum(market_caps) / n,
            "median_market_cap_billion": median_cap,
            "max_consecutive_limit_up": max(limit_ups),
            "avg_correlation": sum(correlations) / n,
            "total_market_cap_billion": sum(market_caps),
            "stock_count": n,
        }

    # ──────────────────────────────────────────────
    # 单只股票分类
    # ──────────────────────────────────────────────

    def _classify_single_stock(
        self,
        stock: Dict[str, Any],
        theme_stats: Dict[str, float],
    ) -> Optional[Tuple[str, Dict]]:
        """对单只股票执行多分类评分，取最高分分类作为结果.

        Args:
            stock: 股票数据字典.
            theme_stats: 主题统计量.

        Returns:
            (分类类型, 带有评分的股票记录) 或 None（无法分类）.
        """
        # 1) 先检查淘汰
        eliminated_score = self._compute_eliminated_score(stock, theme_stats)
        if eliminated_score > 50:
            record = {**stock, "_score": round(eliminated_score, 2), "classified_type": TYPE_ELIMINATED}
            return (TYPE_ELIMINATED, record)

        # 2) 计算各类评分
        leader_score = self._compute_leader_score(stock, theme_stats)
        core_score = self._compute_core_score(stock, theme_stats)
        catch_up_score = self._compute_catch_up_score(stock, theme_stats)
        follower_score = self._compute_follower_score(stock, theme_stats)

        # 3) 取最高分，但 leader 和 core 有最低门槛
        scores: Dict[str, float] = {
            TYPE_LEADER: leader_score if self._meets_leader_threshold(stock) else 0.0,
            TYPE_CORE: core_score if self._meets_core_threshold(stock) else 0.0,
            TYPE_CATCH_UP: catch_up_score,
            TYPE_FOLLOWER: follower_score,
        }

        best_type = max(scores, key=scores.get)
        best_score = scores[best_type]

        if best_score <= 0:
            return None

        record = {**stock, "_score": round(best_score, 2), "classified_type": best_type}
        return (best_type, record)

    def _meets_leader_threshold(self, stock: Dict[str, Any]) -> bool:
        """检查是否满足 leader 最低门槛.

        Args:
            stock: 股票数据.

        Returns:
            是否满足门槛.
        """
        min_limit_up = self.thresholds.get("leader_min_consecutive_limit_up", 3)
        return (stock.get("consecutive_limit_up", 0) or 0) >= min_limit_up

    def _meets_core_threshold(self, stock: Dict[str, Any]) -> bool:
        """检查是否满足 core 最低门槛.

        Args:
            stock: 股票数据.

        Returns:
            是否满足门槛.
        """
        min_mcap = self.thresholds.get("core_min_market_cap_billion", 10.0)
        return (stock.get("market_cap_billion", 0.0) or 0.0) >= min_mcap

    # ──────────────────────────────────────────────
    # 各类评分
    # ──────────────────────────────────────────────

    def _compute_leader_score(
        self,
        stock: Dict[str, Any],
        theme_stats: Dict[str, float],
    ) -> float:
        """龙头评分（0-100）.

        权重:
            - consecutive_limit_up: 30%
            - cumulative_return_20d (vs 主题排名): 25%
            - volume_ratio: 15%
            - correlation_with_theme: 15%
            - market_cap_factor: 15%

        Args:
            stock: 股票数据.
            theme_stats: 主题统计量.

        Returns:
            0-100 评分.
        """
        # 连续涨停分（0-100）
        limit_up = stock.get("consecutive_limit_up", 0) or 0
        max_limit_up = max(theme_stats["max_consecutive_limit_up"], 1)
        limit_up_score = min(100.0, (limit_up / max_limit_up) * 100.0)

        # 收益率排名分（0-100）：用收益率 vs 主题平均值
        ret = stock.get("cumulative_return_20d", 0.0) or 0.0
        avg_ret = theme_stats["avg_return_20d"]
        if avg_ret > 0:
            ret_ratio = ret / avg_ret
        elif ret > 0:
            ret_ratio = 2.0  # 主题均值为负但个股为正，加分
        else:
            ret_ratio = ret / (abs(avg_ret) + 0.001) if avg_ret < 0 else 0.0
        ret_score = min(100.0, max(0.0, ret_ratio * 50.0))

        # 成交量分
        vol = stock.get("volume_ratio", 0.0) or 0.0
        vol_score = min(100.0, (vol / 2.0) * 100.0)

        # 相关性分
        corr = stock.get("correlation_with_theme", 0.0) or 0.0
        corr_score = corr * 100.0  # 0-100

        # 市值因子（小市值加分，leader 多为小/中盘股）
        mcap = stock.get("market_cap_billion", 0.0) or 0.0
        mcap_factor = max(0.0, 1.0 - mcap / 50.0)  # 市值越大得分越低
        mcap_score = mcap_factor * 100.0

        score = (
            limit_up_score * 0.30
            + ret_score * 0.25
            + vol_score * 0.15
            + corr_score * 0.15
            + mcap_score * 0.15
        )

        return max(0.0, min(100.0, score))

    def _compute_core_score(
        self,
        stock: Dict[str, Any],
        theme_stats: Dict[str, float],
    ) -> float:
        """核心/中军评分（0-100）.

        权重:
            - market_cap: 30%
            - return_stability (5d vs 20d 偏差): 25%
            - correlation: 20%
            - volume_stability: 15%
            - institutional_ownership: 10%

        Args:
            stock: 股票数据.
            theme_stats: 主题统计量.

        Returns:
            0-100 评分.
        """
        # 市值分（大盘股加分，越大约好）
        mcap = stock.get("market_cap_billion", 0.0) or 0.0
        min_core_mcap = self.thresholds.get("core_min_market_cap_billion", 10.0)
        mcap_score = min(100.0, (mcap / min_core_mcap) * 50.0) if min_core_mcap > 0 else 0.0

        # 收益稳定性（5d 与 20d 的偏差越小越好）
        ret_5d = stock.get("avg_return_5d", 0.0) or 0.0
        ret_20d = stock.get("avg_return_20d", 0.0) or 0.0
        dev = abs(ret_5d - ret_20d / 4.0) if ret_20d != 0 else 0.0
        stability_score = max(0.0, 100.0 - dev * 500.0)  # 偏差每 0.01 扣 5 分

        # 相关性分（core 要求高相关性）
        corr = stock.get("correlation_with_theme", 0.0) or 0.0
        corr_score = corr * 100.0

        # 成交量稳定性（volume_ratio 偏离 1.0 越小越好）
        vol = stock.get("volume_ratio", 0.0) or 0.0
        vol_stability = max(0.0, 100.0 - abs(vol - 1.0) * 100.0)

        # 机构持仓（如有）
        inst_own = stock.get("institutional_ownership", 0.0) or 0.0
        inst_score = min(100.0, inst_own * 100.0)

        score = (
            mcap_score * 0.30
            + stability_score * 0.25
            + corr_score * 0.20
            + vol_stability * 0.15
            + inst_score * 0.10
        )

        return max(0.0, min(100.0, score))

    def _compute_catch_up_score(
        self,
        stock: Dict[str, Any],
        theme_stats: Dict[str, float],
    ) -> float:
        """补涨评分（0-100）.

        权重:
            - recent_return_5d: 30%
            - previous_dormancy: 20%
            - volume_expansion: 25%
            - technical_breakout: 25%

        Args:
            stock: 股票数据.
            theme_stats: 主题统计量.

        Returns:
            0-100 评分.
        """
        # 近期收益（5d 超出主题均值越多越好）
        ret_5d = stock.get("avg_return_5d", 0.0) or 0.0
        avg_ret_5d = theme_stats["avg_return_5d"]
        excess_ret = ret_5d - avg_ret_5d
        # 补涨要求近期涨幅 > 主题平均
        recent_score = min(100.0, max(0.0, excess_ret * 500.0)) if excess_ret > 0 else 0.0

        # 前期滞涨（之前 20d 跑输主题均值，越久越好）
        ret_20d = stock.get("cumulative_return_20d", 0.0) or 0.0
        avg_ret_20d = theme_stats["avg_return_20d"]
        dormancy_gap = avg_ret_20d - ret_20d  # 正值表示跑输
        dormancy_score = min(100.0, max(0.0, dormancy_gap * 200.0)) if dormancy_gap > 0 else 0.0

        # 成交量扩张（volume_ratio 从低基期放大）
        vol = stock.get("volume_ratio", 0.0) or 0.0
        avg_vol = theme_stats["avg_volume_ratio"]
        if avg_vol > 0 and vol > avg_vol:
            expansion = vol / avg_vol
            vol_expansion_score = min(100.0, (expansion - 1.0) * 100.0)
        else:
            vol_expansion_score = 0.0

        # 技术突破（volume_ratio > 1.2 且收益率正）
        vol_condition = vol > 1.2
        ret_condition = ret_5d > 0.0
        tech_score = 100.0 if (vol_condition and ret_condition) else 0.0

        score = (
            recent_score * 0.30
            + dormancy_score * 0.20
            + vol_expansion_score * 0.25
            + tech_score * 0.25
        )

        return max(0.0, min(100.0, score))

    def _compute_follower_score(
        self,
        stock: Dict[str, Any],
        theme_stats: Dict[str, float],
    ) -> float:
        """跟风评分（0-100）.

        权重:
            - correlation_with_leaders: 35%
            - return_gap (与龙头差距): 25%
            - volume_ratio: 20%
            - market_cap: 20%

        Args:
            stock: 股票数据.
            theme_stats: 主题统计量.

        Returns:
            0-100 评分.
        """
        # 与主题相关性（代替与龙头相关性）
        corr = stock.get("correlation_with_theme", 0.0) or 0.0
        min_corr = self.thresholds.get("catch_up_min_correlation", 0.7)
        if corr < min_corr:
            corr_score = 0.0
        else:
            corr_score = ((corr - min_corr) / (1.0 - min_corr)) * 100.0

        # 收益差距（跟风股收益应介于龙头和均值之间，不完全落后）
        ret_5d = stock.get("avg_return_5d", 0.0) or 0.0
        avg_ret_5d = theme_stats["avg_return_5d"]
        # 跟风股收益不应太差（在均值附近或略低为佳）
        gap = ret_5d - avg_ret_5d
        if gap > -0.02:  # 不显著跑输
            return_gap_score = 100.0 - min(100.0, abs(gap) * 500.0)
        else:
            return_gap_score = max(0.0, 100.0 - abs(gap) * 300.0)

        # 成交量（适中，不过度活跃也不过于冷清）
        vol = stock.get("volume_ratio", 0.0) or 0.0
        vol_score = max(0.0, 100.0 - abs(vol - 0.8) * 100.0)

        # 市值（中小市值跟风，不超过上限）
        mcap = stock.get("market_cap_billion", 0.0) or 0.0
        max_mcap = self.thresholds.get("follower_max_market_cap_billion", 50.0)
        if mcap <= max_mcap:
            mcap_score = (1.0 - mcap / max_mcap) * 100.0
        else:
            mcap_score = 0.0

        score = (
            corr_score * 0.35
            + return_gap_score * 0.25
            + vol_score * 0.20
            + mcap_score * 0.20
        )

        return max(0.0, min(100.0, score))

    def _compute_eliminated_score(
        self,
        stock: Dict[str, Any],
        theme_stats: Dict[str, float],
    ) -> float:
        """淘汰评分（0-100）.

        权重:
            - days_no_activity: 40%
            - volume_decline: 30%
            - return_gap: 30%

        当评分 > 50 时, 该股票被视为淘汰.

        Args:
            stock: 股票数据.
            theme_stats: 主题统计量.

        Returns:
            0-100 评分.
        """
        max_inactive_days = self.thresholds.get("eliminated_max_days_no_activity", 60)

        # 不活跃天数分
        inactive_days = stock.get("days_no_activity", 0) or 0
        inactivity_score = min(100.0, (inactive_days / max_inactive_days) * 100.0)

        # 成交量萎缩分（volume_ratio < 0.5 记为萎缩）
        vol = stock.get("volume_ratio", 0.0) or 0.0
        avg_vol = theme_stats["avg_volume_ratio"]
        if avg_vol > 0:
            vol_decline_ratio = vol / avg_vol
        else:
            vol_decline_ratio = 0.0

        # volume_ratio 低于 0.5 或远低于主题均值都扣分
        vol_decline_score = 0.0
        if vol < 0.5:
            vol_decline_score = 100.0
        elif vol_decline_ratio < 0.5:
            vol_decline_score = 80.0
        elif vol_decline_ratio < 0.8:
            vol_decline_score = 40.0

        # 收益差距分（明显跑输主题均值）
        ret_20d = stock.get("cumulative_return_20d", 0.0) or 0.0
        avg_ret_20d = theme_stats["avg_return_20d"]
        if avg_ret_20d > 0:
            # 主题上涨但个股下跌或涨幅很小
            relative_gap = (avg_ret_20d - ret_20d) / avg_ret_20d
        elif avg_ret_20d < 0:
            # 主题下跌，个股跌更多
            relative_gap = (ret_20d - avg_ret_20d) / abs(avg_ret_20d) if avg_ret_20d != 0 else 0.0
            relative_gap = max(0.0, -relative_gap)  # 负值表示更差
        else:
            relative_gap = -ret_20d if ret_20d < 0 else 0.0

        return_gap_score = min(100.0, max(0.0, relative_gap * 100.0))

        score = (
            inactivity_score * 0.40
            + vol_decline_score * 0.30
            + return_gap_score * 0.30
        )

        return max(0.0, min(100.0, score))

    # ──────────────────────────────────────────────
    # 排序与冲突处理
    # ──────────────────────────────────────────────

    @staticmethod
    def _rank_by_score(
        stocks_with_scores: List[Tuple[Dict[str, Any], float]],
        max_count: int,
    ) -> List[Dict[str, Any]]:
        """按评分降序排列，取前 max_count 条.

        Args:
            stocks_with_scores: (股票字典, 评分) 列表.
            max_count: 最大返回数量.

        Returns:
            排序后的股票字典列表（带 _score 字段）.
        """
        sorted_stocks = sorted(
            stocks_with_scores,
            key=lambda x: x[1],
            reverse=True,
        )
        return [s[0] for s in sorted_stocks[:max_count]]

    def _adjust_classifications(
        self,
        classifications: Dict[str, List[Dict[str, Any]]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """调整分类冲突，应用业务规则.

        规则:
            1. leader 优先于 follower：若某股同时符合，只保留 leader
            2. core 优先于 follower：若某股同时符合，只保留 core
            3. eliminated 覆盖所有其他分类
            4. 一只股票只能属于一个分类

        Args:
            classifications: 各分类的股票列表.

        Returns:
            调整后的分类字典.
        """
        # 构建所有已分类股票的集合 {stock_code -> type, record}
        # 处理优先级：eliminated > leader > core > catch_up > follower
        all_stocks: Dict[str, Tuple[int, Dict]] = {}

        priority_map = {
            TYPE_ELIMINATED: 0,
            TYPE_LEADER: 1,
            TYPE_CORE: 2,
            TYPE_CATCH_UP: 3,
            TYPE_FOLLOWER: 4,
        }

        for type_, stocks in classifications.items():
            priority = priority_map.get(type_, 99)
            for stock in stocks:
                code = stock.get("stock_code", "")
                if code in all_stocks:
                    existing_priority = all_stocks[code][0]
                    if priority >= existing_priority:
                        continue  # 已有更高优先级分类
                all_stocks[code] = (priority, {**stock, "leader_type": type_})

        # 重新分配到各分类
        result: Dict[str, List[Dict]] = {
            TYPE_LEADER: [],
            TYPE_CORE: [],
            TYPE_FOLLOWER: [],
            TYPE_CATCH_UP: [],
            TYPE_ELIMINATED: [],
        }

        for code, (priority, record) in all_stocks.items():
            type_mapping = {
                0: TYPE_ELIMINATED,
                1: TYPE_LEADER,
                2: TYPE_CORE,
                3: TYPE_CATCH_UP,
                4: TYPE_FOLLOWER,
            }
            target_type = type_mapping.get(priority, TYPE_FOLLOWER)
            result[target_type].append(record)

        return result
