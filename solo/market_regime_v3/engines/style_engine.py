"""Style Engine - 风格轮动引擎

评估各风格（大盘、小盘、成长、价值、科技、红利、消费、医疗、军工、AI）的强度评分，
判断当前市场风格偏好，提供主题建议。
"""

import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yaml

# 添加项目路径
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)
sys.path.insert(0, os.path.join(_PROJECT_ROOT, 'inst_pullback_v2'))

from data.loader import DataLoader
from data.indicators import sma, atr


@dataclass
class StyleResult:
    """风格评估结果"""
    dominant_style: str                       # 主导风格，如 "Technology"
    style_scores: Dict[str, float]            # 所有风格得分 0-100
    top_styles: List[str]                      # 排名前 3 的风格
    suggestions: List[str]                     # 主题建议（来自 config）
    sub_scores: Dict[str, Dict[str, float]]   # 各风格 -> 子因子得分
    explain: Dict[str, str]                   # 各风格 -> 解释文本


class StyleEngine:
    """风格轮动引擎

    读取配置中的 style 分类，对每种风格加载对应 ETF 数据，
    计算动量、趋势、成交量、波动率等子因子得分，加权聚合为风格得分。
    """

    # 高波动偏好风格列表（成长/科技类偏好高波动）
    HIGH_VOL_STYLES = {"Growth", "Technology", "SmallCap", "AI", "Military"}

    def __init__(self, config: dict):
        """初始化

        Args:
            config: 完整配置字典（从 yaml 加载后的 dict）
        """
        self.style_cfg = config.get('style', {})
        self.suggestions_cfg = config.get('style_suggestions', {})
        self.categories = self.style_cfg.get('categories', [])
        self.lookback = self.style_cfg.get('lookback', 60)
        self.top_n = self.style_cfg.get('top_n', 3)
        self.loader = DataLoader()

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def evaluate(self, start_date: str, end_date: str) -> StyleResult:
        """评估各风格强度

        Args:
            start_date: 开始日期 YYYYMMDD
            end_date:   结束日期 YYYYMMDD

        Returns:
            StyleResult
        """
        style_scores: Dict[str, float] = {}
        sub_scores: Dict[str, Dict[str, float]] = {}
        explain: Dict[str, str] = {}

        # 逐风格计算得分
        for cat in self.categories:
            name = cat['name']
            total, subs, exp = self._calc_style_score(cat, start_date, end_date)
            style_scores[name] = total
            sub_scores[name] = subs
            explain[name] = exp

        # 获取排名前 3 的风格
        top_styles = self._get_top_styles(style_scores)

        # 确定主导风格（最高分）
        dominant = top_styles[0] if top_styles else "Unknown"

        # 获取主题建议
        suggestions = self._get_suggestions(dominant, style_scores, top_styles)

        return StyleResult(
            dominant_style=dominant,
            style_scores=style_scores,
            top_styles=top_styles,
            suggestions=suggestions,
            sub_scores=sub_scores,
            explain=explain,
        )

    # ------------------------------------------------------------------
    # 单风格得分计算
    # ------------------------------------------------------------------
    def _calc_style_score(self,
                          style_cfg: dict,
                          start_date: str,
                          end_date: str) -> Tuple[float, Dict[str, float], str]:
        """计算单个风格的综合得分

        1. 加载该风格对应 ETF 的数据
        2. 计算各子因子得分（动量、趋势、成交量、波动率、宽度贡献）
        3. 按配置权重加权汇总

        Args:
            style_cfg: 风格配置 dict（包含 name, etf_codes, weights 等）
            start_date: 开始日期
            end_date:   结束日期

        Returns:
            (总分, 子因子分数 dict, 解释文本)
        """
        name = style_cfg['name']
        etf_codes = style_cfg.get('etf_codes', [])
        weights = style_cfg.get('weights', {})

        if not etf_codes:
            return 50.0, {}, f"{name}: 无 ETF 配置，默认 50 分"

        # 加载所有 ETF 数据
        etf_dfs: Dict[str, pd.DataFrame] = {}
        for code in etf_codes:
            df = self.loader.load_index_data(code, start_date, end_date, silent=True)
            if df is not None and not df.empty:
                etf_dfs[code] = df

        if not etf_dfs:
            return 50.0, {}, f"{name}: 所有 ETF 无数据，默认 50 分"

        # 计算各子因子得分
        subs: Dict[str, float] = {}

        # 动量：多周期收益率加权
        subs['momentum'] = self._calc_momentum_score(etf_dfs)

        # 趋势：均线排列得分
        subs['trend'] = self._calc_trend_score(etf_dfs)

        # 成交量：量比变化
        subs['volume'] = self._calc_volume_score(etf_dfs)

        # 波动率：ATR 百分位（按风格类型决定高低偏好）
        subs['volatility'] = self._calc_volatility_score(etf_dfs, name)

        # 宽度贡献：暂不直接计算，使用默认值 50
        subs['breadth_contribution'] = 50.0

        # 加权汇总
        total = 0.0
        sum_w = 0.0
        details = []
        for factor_name, w in weights.items():
            if factor_name in subs:
                s = subs[factor_name]
                total += s * w
                sum_w += w
                details.append(f"{factor_name}={s:.1f}分(w={w})")

        if sum_w > 0:
            total_score = total / sum_w
        else:
            total_score = 50.0
        total_score = max(0.0, min(100.0, total_score))

        explain_str = f"{name}: {total_score:.1f}分 | " + ", ".join(details)
        return total_score, subs, explain_str

    # ------------------------------------------------------------------
    # 子因子计算方法
    # ------------------------------------------------------------------

    def _get_price_col(self, df: pd.DataFrame) -> str:
        """获取可用价格列名，优先 close_hfq（后复权）"""
        if 'close_hfq' in df.columns:
            return 'close_hfq'
        return 'close'

    @staticmethod
    def _safe_float(val, default=50.0) -> float:
        """安全转换 float，处理 NaN/None"""
        if val is None:
            return default
        try:
            v = float(val)
            if np.isnan(v) or np.isinf(v):
                return default
            return v
        except (TypeError, ValueError):
            return default

    def _calc_momentum_score(self,
                              etf_dfs: Dict[str, pd.DataFrame]) -> float:
        """动量得分：多周期收益率加权

        对每只 ETF 计算 5/20/60 日收益率并加权平均，再取所有 ETF 均值。
        使用 tanh 将收益率映射到 0-100 范围。
        """
        if not etf_dfs:
            return 50.0

        # 多周期及权重
        periods = [5, 20, 60]
        period_weights = [0.3, 0.5, 0.2]
        etf_scores = []

        for _code, df in etf_dfs.items():
            col = self._get_price_col(df)
            close = df[col]
            if len(close) < max(periods) + 1:
                continue

            weighted_ret = 0.0
            sum_w = 0.0
            for period, w in zip(periods, period_weights):
                ret = close.iloc[-1] / close.iloc[-period - 1] - 1
                weighted_ret += ret * w
                sum_w += w

            if sum_w > 0:
                weighted_ret /= sum_w

            # tanh 映射：±5% 收益率映射到 0-100（tanh(1) ≈ 0.76）
            scaled = np.tanh(weighted_ret * 20)
            score = (scaled + 1.0) * 50.0
            etf_scores.append(score)

        if not etf_scores:
            return 50.0

        return max(0.0, min(100.0, float(np.mean(etf_scores))))

    def _calc_trend_score(self, etf_dfs: Dict[str, pd.DataFrame]) -> float:
        """趋势得分：MA 排列检查

        检查收盘价与 MA20、MA60、MA120 的关系，
        价格在均线之上计 1 分，计算所有 ETF 的平均得分。
        """
        if not etf_dfs:
            return 50.0

        ma_periods = [20, 60, 120]
        etf_scores = []

        for _code, df in etf_dfs.items():
            col = self._get_price_col(df)
            close = df[col]
            if len(close) < min(ma_periods):
                continue

            above_count = 0
            total = 0
            for p in ma_periods:
                if len(close) >= p:
                    ma = sma(close, p)
                    if not ma.dropna().empty:
                        if close.iloc[-1] > ma.iloc[-1]:
                            above_count += 1
                        total += 1

            if total > 0:
                score = (above_count / total) * 100.0
                etf_scores.append(score)

        if not etf_scores:
            return 50.0

        return max(0.0, min(100.0, float(np.mean(etf_scores))))

    def _calc_volume_score(self, etf_dfs: Dict[str, pd.DataFrame]) -> float:
        """成交量得分：量比分析

        计算最新成交量相对 20 日均量的比值。
        量比接近 1.0 为健康，过高或过低都会扣分。
        """
        if not etf_dfs:
            return 50.0

        period = 20
        etf_scores = []

        for _code, df in etf_dfs.items():
            if 'vol' not in df.columns:
                continue
            vol = df['vol']
            if len(vol) < period + 1:
                continue

            vol_ma = sma(vol, period)
            if vol_ma.dropna().empty:
                continue

            latest_vol = vol.iloc[-1]
            latest_ma = vol_ma.iloc[-1]

            if latest_ma <= 0:
                continue

            ratio = latest_vol / latest_ma

            # 量比评分：1.0 附近最高，偏离越远分越低
            if ratio >= 1.0:
                if ratio <= 1.2:
                    # 1.0 -> 100, 1.2 -> 80
                    score = 100.0 - (ratio - 1.0) / 0.2 * 20.0
                else:
                    # 1.2 -> 80, 1.4 -> 70, ...
                    score = 80.0 - min(80.0, (ratio - 1.2) * 50)
            else:
                if ratio >= 0.8:
                    # 1.0 -> 100, 0.8 -> 70
                    score = 100.0 - (1.0 - ratio) / 0.2 * 30.0
                else:
                    # 0.8 -> 70, 0.5 -> 40, ...
                    score = 70.0 - min(70.0, (0.8 - ratio) * 100)

            etf_scores.append(score)

        if not etf_scores:
            return 50.0

        return max(0.0, min(100.0, float(np.mean(etf_scores))))

    def _calc_volatility_score(self,
                                etf_dfs: Dict[str, pd.DataFrame],
                                style_name: str) -> float:
        """波动率得分：ATR 百分位

        对于成长/科技类风格（HIGH_VOL_STYLES）：高波动 = 高分
        对于价值/红利类风格：低波动 = 高分

        使用 ATR(14) 在回看期内的百分位排名。
        """
        if not etf_dfs:
            return 50.0

        high_vol_preference = style_name in self.HIGH_VOL_STYLES
        etf_scores = []

        for _code, df in etf_dfs.items():
            if not all(c in df.columns for c in ['high', 'low', 'close']):
                continue

            close = df['close']
            if len(close) < 15:  # ATR(14) + 1
                continue

            atr_series = atr(df['high'], df['low'], close, period=14)
            atr_valid = atr_series.dropna()
            if len(atr_valid) < 2:
                continue

            latest_atr = atr_valid.iloc[-1]
            percentile = (atr_valid <= latest_atr).mean()

            if high_vol_preference:
                # 高波动偏好：高百分位 = 高分
                score = percentile * 100.0
            else:
                # 低波动偏好：低百分位 = 高分
                score = (1.0 - percentile) * 100.0

            etf_scores.append(score)

        if not etf_scores:
            return 50.0

        return max(0.0, min(100.0, float(np.mean(etf_scores))))

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _get_top_styles(self, style_scores: Dict[str, float]) -> List[str]:
        """获取排名前 N 的风格

        Args:
            style_scores: 风格 -> 得分 dict

        Returns:
            按得分降序排列的 top_n 风格名称列表
        """
        sorted_styles = sorted(style_scores.items(), key=lambda x: x[1], reverse=True)
        return [name for name, _ in sorted_styles[:self.top_n]]

    def _get_suggestions(self,
                          dominant_style: str,
                          style_scores: Dict[str, float],
                          top_styles: List[str]) -> List[str]:
        """获取主题建议

        优先检查组合建议（如 Growth_Technology 两个风格都在 top_styles 中），
        若组合键存在则使用组合建议，否则回落为单一主导风格的建议。

        Args:
            dominant_style: 主导风格名称
            style_scores:   所有风格得分
            top_styles:     排名前 3 的风格列表

        Returns:
            主题建议列表
        """
        # 尝试组合建议（top 2 风格构成组合键）
        if len(top_styles) >= 2:
            # 检查两种排列顺序
            for i in range(len(top_styles)):
                for j in range(i + 1, len(top_styles)):
                    combo_key = f"{top_styles[i]}_{top_styles[j]}"
                    if combo_key in self.suggestions_cfg:
                        return list(self.suggestions_cfg[combo_key])

        # 回落：使用主导风格
        if dominant_style in self.suggestions_cfg:
            return list(self.suggestions_cfg[dominant_style])

        return []
