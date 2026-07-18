#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主题风格择时引擎
================
对股池中出现的主题进行评分，从趋势强度、广度、龙头健康度、资金流向四个维度
综合评估主题的当前状态，为选股和仓位分配提供依据。
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np
import pandas as pd

LOG = logging.getLogger("timing_trading.theme_timing")


@dataclass
class ThemeState:
    """主题状态评分结果"""
    name: str                                 # 主题名称
    score: float = 0.0                        # 综合评分 0-100
    trend_score: float = 0.0                  # 趋势强度
    breadth_score: float = 0.0                # 广度
    leader_health_score: float = 0.0          # 龙头健康度
    capital_flow_score: float = 0.0           # 资金流向
    stock_count: int = 0                      # 股池中该主题股票数
    top_stocks: List[str] = field(default_factory=list)   # 评分最高的3只代码
    details: dict = field(default_factory=dict)


class ThemeTimingEngine:
    """主题风格择时引擎"""

    def __init__(self, config: dict):
        theme_cfg = config.get("theme_timing", {})
        self.enabled = theme_cfg.get("enabled", True)
        self.min_stocks_per_theme = theme_cfg.get("min_stocks_per_theme", 3)
        self.weights = theme_cfg.get("score_weights", {
            "trend": 0.30,
            "breadth": 0.25,
            "leader_health": 0.25,
            "capital_flow": 0.20,
        })
        self.top_n_themes = theme_cfg.get("top_n_themes", 5)
        self.tdx_root = config.get("general", {}).get("tdx_root", "C:\\new_tdx")
        self.theme_map_path = config.get("general", {}).get("theme_map_path", "")

    # ------------------------------------------------------------------
    # 主题映射加载
    # ------------------------------------------------------------------

    def load_theme_map(self, theme_map_path: str) -> dict:
        """从JSON加载主题-个股映射

        Args:
            theme_map_path: JSON文件路径

        Returns:
            {theme_name: [ts_code, ...]}
        """
        if not os.path.exists(theme_map_path):
            LOG.warning("主题映射文件不存在: %s", theme_map_path)
            return {}

        with open(theme_map_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        result = {}

        # 格式1: {"themes": {"theme_name": [{"code": "...", ...}, ...]}}
        if isinstance(data, dict) and "themes" in data:
            themes = data["themes"]
            for theme_name, stocks in themes.items():
                codes = self._extract_codes(stocks)
                if codes:
                    result[theme_name] = codes
            LOG.info("主题映射加载: %d 个主题 (格式: themes字典)", len(result))
            return result

        # 格式2: 扁平字典 {"theme_name": ["code1", "code2", ...]}
        #        或 {"theme_name": [{"ts_code": "..."}, ...]}
        if isinstance(data, dict):
            for theme_name, stocks in data.items():
                codes = self._extract_codes(stocks)
                if codes:
                    result[theme_name] = codes
            LOG.info("主题映射加载: %d 个主题 (格式: 扁平字典)", len(result))
            return result

        LOG.warning("未知的主题映射格式: %s", type(data))
        return {}

    @staticmethod
    def _extract_codes(stocks) -> List[str]:
        """从主题的个股列表中提取ts_code列表"""
        if not isinstance(stocks, list):
            return []
        codes = []
        for item in stocks:
            if isinstance(item, dict):
                code = item.get("code") or item.get("ts_code") or ""
                if code:
                    codes.append(code)
            elif isinstance(item, str):
                codes.append(item)
        return codes

    # ------------------------------------------------------------------
    # 主题评分
    # ------------------------------------------------------------------

    def evaluate(
        self,
        pool_df: pd.DataFrame,
        trade_date: str = "",
        etf_data: dict = None,
    ) -> List[ThemeState]:
        """评估股池中所有主题的状态

        Args:
            pool_df: 股池DataFrame，需包含 ts_code, name, theme_list 等列
            trade_date: 交易日期 YYYYMMDD，空字符串使用最新数据（暂保留接口）
            etf_data: {code: DataFrame} 字典，辅助判断资金流向（可选）

        Returns:
            按综合评分降序排列的 ThemeState 列表
        """
        if pool_df is None or pool_df.empty:
            LOG.warning("股池为空，跳过主题评分")
            return []

        theme_stocks = self._extract_theme_stocks(pool_df)
        if not theme_stocks:
            LOG.info("股池中未发现主题信息")
            return []

        results: List[ThemeState] = []
        for theme_name, stock_df in theme_stocks.items():
            n_stocks = len(stock_df)

            # 过滤：少于最小股数要求的主题跳过
            if n_stocks < self.min_stocks_per_theme:
                continue

            # 计算各维度评分
            trend_score = self._calc_trend_score(stock_df)
            breadth_score = self._calc_breadth_score(stock_df)
            leader_health_score = self._calc_leader_health_score(stock_df)
            capital_flow_score = self._calc_capital_flow_score(stock_df, etf_data, theme_name)

            # 综合评分
            score = (
                trend_score * self.weights.get("trend", 0.30)
                + breadth_score * self.weights.get("breadth", 0.25)
                + leader_health_score * self.weights.get("leader_health", 0.25)
                + capital_flow_score * self.weights.get("capital_flow", 0.20)
            )

            # 该主题下评分最高的3只股票代码
            top_stocks = self._get_top_stocks(stock_df, n=3)

            state = ThemeState(
                name=theme_name,
                score=round(score, 2),
                trend_score=round(trend_score, 2),
                breadth_score=round(breadth_score, 2),
                leader_health_score=round(leader_health_score, 2),
                capital_flow_score=round(capital_flow_score, 2),
                stock_count=n_stocks,
                top_stocks=top_stocks,
                details={
                    "n_stocks": n_stocks,
                    "trade_date": trade_date,
                },
            )
            results.append(state)

        # 按score降序，取top_n
        results.sort(key=lambda s: s.score, reverse=True)
        results = results[: self.top_n_themes]

        LOG.info(
            "主题评分完成: %d 个主题活跃 (top=%s)",
            len(results),
            [r.name for r in results[:3]] if results else [],
        )
        return results

    def _extract_theme_stocks(self, pool_df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
        """从股池中按theme_list列提取各主题对应的股票子集"""
        if "theme_list" not in pool_df.columns:
            LOG.warning("股池缺少 theme_list 列")
            return {}

        theme_map: Dict[str, list] = {}
        for _, row in pool_df.iterrows():
            themes = row.get("theme_list", [])
            if not isinstance(themes, list) or not themes:
                continue
            for t in themes:
                if t not in theme_map:
                    theme_map[t] = []
                theme_map[t].append(row)

        return {t: pd.DataFrame(rows) for t, rows in theme_map.items()}

    # ------------------------------------------------------------------
    # 各维度评分计算
    # ------------------------------------------------------------------

    def _calc_trend_score(self, stock_df: pd.DataFrame) -> float:
        """计算趋势强度得分

        取该主题在股池中所有个股的均值ma20斜率 * 50 + 50，得分0-100
        """
        # 优先使用 ma20_slope 列（由外部调用方传入日线数据后计算好）
        if "ma20_slope" in stock_df.columns:
            slopes = stock_df["ma20_slope"].dropna()
            if len(slopes) > 0:
                mean_slope = slopes.mean()
                score = mean_slope * 50 + 50
                return max(0.0, min(100.0, score))

        # 备选: 用60日收益%近似趋势强度
        if "60日收益%" in stock_df.columns:
            returns = stock_df["60日收益%"].dropna()
            if len(returns) > 0:
                mean_ret = returns.mean()
                # 0%收益 -> 50分，±20%收益 -> ±20分
                score = mean_ret + 50
                return max(0.0, min(100.0, score))

        return 50.0  # 默认中性分数

    def _calc_breadth_score(self, stock_df: pd.DataFrame) -> float:
        """计算广度得分

        (上涨个股 / 总个股) * 100
        上涨判断依据（按优先级）: ma20_slope>0, pct_chg>0, 60日收益%>0
        """
        n_total = len(stock_df)
        if n_total == 0:
            return 0.0

        # 判断上涨的列（按优先级）
        for col in ["ma20_slope", "pct_chg", "60日收益%"]:
            if col in stock_df.columns and stock_df[col].notna().sum() > n_total * 0.3:
                n_up = (stock_df[col].fillna(0) > 0).sum()
                return min(100.0, (n_up / n_total) * 100)

        # 没有任何涨跌指标，默认50
        return 50.0

    def _calc_leader_health_score(self, stock_df: pd.DataFrame) -> float:
        """计算龙头健康度得分

        取涨停次数最多的3只的均分，max(均分 * 10, 100)
        """
        if "涨停次数" not in stock_df.columns:
            return 50.0

        top3 = stock_df.nlargest(3, "涨停次数")
        mean_limit = top3["涨停次数"].mean()
        score = mean_limit * 10
        return min(100.0, max(0.0, score))

    def _calc_capital_flow_score(
        self,
        stock_df: pd.DataFrame,
        etf_data: dict = None,
        theme_name: str = "",
    ) -> float:
        """计算资金流向得分

        用主题下个股的日均成交额变化率 * 50 + 50，得分0-100
        """
        # 优先使用日均成交额变化率列
        if "日均成交额变化率" in stock_df.columns:
            rates = stock_df["日均成交额变化率"].dropna()
            if len(rates) > 0:
                mean_rate = rates.mean()
                score = mean_rate * 50 + 50
                return max(0.0, min(100.0, score))

        # 用日均成交额(亿)近似：成交额越大说明资金关注度越高
        if "日均成交额(亿)" in stock_df.columns:
            amounts = stock_df["日均成交额(亿)"].dropna()
            if len(amounts) > 0:
                mean_amount = amounts.mean()
                # 假设5亿以上得高分，0亿得0分
                score = mean_amount / 5.0 * 50 + 50
                return max(0.0, min(100.0, score))

        return 50.0

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def _get_top_stocks(self, stock_df: pd.DataFrame, n: int = 3) -> List[str]:
        """获取主题下评分最高的n只股票代码"""
        # 评分列优先级
        score_col = None
        for col in ["最终分", "Bull_v2.1分", "涨停次数"]:
            if col in stock_df.columns:
                score_col = col
                break

        if score_col:
            top = stock_df.nlargest(n, score_col)
        else:
            top = stock_df.head(n)

        codes = []
        for _, row in top.iterrows():
            code = row.get("ts_code") or row.get("code") or ""
            if code:
                codes.append(code)
        return codes

    # ------------------------------------------------------------------
    # 特征提取
    # ------------------------------------------------------------------

    def get_theme_features(self, theme_states: List[ThemeState]) -> dict:
        """将主题状态转为数值特征供LightGBM使用

        Args:
            theme_states: evaluate() 返回的主题状态列表

        Returns:
            特征字典，包含:
            - top_theme_score: 最高分
            - theme_score_spread: 前3名分差
            - top_theme_name: 最高分主题名
            - theme_count_active: 活跃主题数量(score >= 60)
            - top3_avg_*: 前3主题的各维度均值
        """
        features: dict = {
            "top_theme_score": 0.0,
            "theme_score_spread": 0.0,
            "top_theme_name": "",
            "theme_count_active": 0,
        }

        if not theme_states:
            return features

        # 最高分
        top_score = theme_states[0].score
        features["top_theme_score"] = top_score
        features["top_theme_name"] = theme_states[0].name

        # 前3名分差 (最高分 - 第3名分)
        if len(theme_states) >= 3:
            features["theme_score_spread"] = round(top_score - theme_states[2].score, 2)
        elif len(theme_states) >= 2:
            features["theme_score_spread"] = round(top_score - theme_states[1].score, 2)
        else:
            features["theme_score_spread"] = 0.0

        # 活跃主题数量 (score >= 60)
        features["theme_count_active"] = sum(1 for s in theme_states if s.score >= 60)

        # 前3主题的各维度均值
        top3 = theme_states[:3]
        if top3:
            features["top3_avg_trend"] = round(
                float(np.mean([s.trend_score for s in top3])), 2
            )
            features["top3_avg_breadth"] = round(
                float(np.mean([s.breadth_score for s in top3])), 2
            )
            features["top3_avg_leader"] = round(
                float(np.mean([s.leader_health_score for s in top3])), 2
            )
            features["top3_avg_capital"] = round(
                float(np.mean([s.capital_flow_score for s in top3])), 2
            )
        else:
            features["top3_avg_trend"] = 0.0
            features["top3_avg_breadth"] = 0.0
            features["top3_avg_leader"] = 0.0
            features["top3_avg_capital"] = 0.0

        return features


def match_pool_to_themes(
    pool_df: pd.DataFrame,
    theme_states: List[ThemeState],
) -> pd.DataFrame:
    """给股池中每只股票打上最匹配的主题评分

    新增列:
    - best_theme_name: 该股票所属的评分最高的主题名称
    - best_theme_score: 对应的主题综合评分（不属于任何活跃主题则为0）

    Args:
        pool_df: 原始股池DataFrame
        theme_states: 主题评分结果

    Returns:
        新增 "best_theme_name", "best_theme_score" 列的DataFrame副本
    """
    result = pool_df.copy()
    result["best_theme_name"] = ""
    result["best_theme_score"] = 0.0

    if not theme_states:
        return result

    theme_score_map = {s.name: s.score for s in theme_states}
    active_themes = set(theme_score_map.keys())

    def _best_match(themes):
        """找到该股票最匹配的活跃主题"""
        if not isinstance(themes, list) or not themes:
            return "", 0.0

        best_name = ""
        best_score = 0.0
        for t in themes:
            if t in active_themes:
                s = theme_score_map[t]
                if s > best_score:
                    best_score = s
                    best_name = t
        return best_name, best_score

    matches = result["theme_list"].apply(_best_match)
    result["best_theme_name"] = matches.apply(lambda x: x[0])
    result["best_theme_score"] = matches.apply(lambda x: x[1])

    return result
