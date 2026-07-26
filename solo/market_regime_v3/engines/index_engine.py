"""Index Strength Engine - 指数强度引擎

计算各市场指数强度评分（0-100），加权聚合为综合指数强度。
"""

import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yaml

# 引入 inst_pullback_v2 模块
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'inst_pullback_v2'))

from data.loader import DataLoader
from data.indicators import sma, ema, slope, macd, rsi, atr, volatility, breakout_pct, new_high_count

from market_regime_v3.factor_registry import GLOBAL_REGISTRY, FactorMeta, FactorCategory


# ===================================================================
# 数据类
# ===================================================================
@dataclass
class IndexStrengthResult:
    """指数强度计算结果"""
    per_index: Dict[str, float]          # 指数代码 -> 总得分
    weighted_score: float                 # 加权总分 0-100
    weights_used: Dict[str, float]        # 最终使用的权重
    contributions: Dict[str, float]       # 各指数贡献值（score * weight）
    sub_scores: Dict[str, Dict[str, float]]  # 指数代码 -> {子因子名: 得分}
    explain: Dict[str, str]              # 指数代码 -> 解释文本


# ===================================================================
# IndexStrengthEngine
# ===================================================================
class IndexStrengthEngine:
    """指数强度引擎

    读取配置、加载指数数据、计算多维度子因子分数、加权聚合。
    支持根据 style_mode 动态调整权重。
    """

    def __init__(self, config: dict):
        """初始化

        Args:
            config: 完整配置字典（从 yaml 加载后的 dict）
        """
        self.cfg = config.get('index_strength', {})
        self.indices_cfg = config.get('indices', {})
        self.loader = DataLoader()

        # 指数列表
        self.index_codes: List[str] = self.indices_cfg.get('codes', [])

        # 基础权重
        self.base_weights: Dict[str, float] = self.indices_cfg.get('base_weights', {})

        # 子因子权重
        self.sub_weights: Dict[str, float] = self.cfg.get('sub_weights', {})

        # 窗口参数
        self.window_trend = self.cfg.get('window_trend', 60)
        self.window_momentum = self.cfg.get('window_momentum', 20)
        self.window_volatility = self.cfg.get('window_volatility', 20)
        self.breakout_lookback = self.cfg.get('breakout_lookback', 20)

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def evaluate(self,
                 start_date: str,
                 end_date: str,
                 style_mode: Optional[str] = None) -> IndexStrengthResult:
        """评估指数强度

        Args:
            start_date: 开始日期 YYYYMMDD
            end_date:   结束日期 YYYYMMDD
            style_mode: 风格模式，用于动态调权（growth/value/risk_on/risk_off）

        Returns:
            IndexStrengthResult
        """
        # 1. 加载基准指数数据（用于相对强度计算）
        benchmark_code = self.cfg.get('relative_strength', {}).get('benchmark', '000300.SH')
        benchmark_df = self.loader.load_index_data(benchmark_code, start_date, end_date, silent=True)
        if benchmark_df is None or benchmark_df.empty:
            benchmark_df = None

        # 2. 逐指数计算
        per_index: Dict[str, float] = {}
        sub_scores: Dict[str, Dict[str, float]] = {}
        explain: Dict[str, str] = {}

        for code in self.index_codes:
            df = self.loader.load_index_data(code, start_date, end_date, silent=True)
            if df is None or df.empty:
                per_index[code] = 0.0
                sub_scores[code] = {}
                explain[code] = f"{code}: 无数据"
                continue

            total_score, subs, exp = self._calc_single_index_strength(code, df, benchmark_df)
            per_index[code] = total_score
            sub_scores[code] = subs
            explain[code] = exp

        # 3. 动态权重调整
        weights = self._get_dynamic_weights(style_mode)

        # 4. 加权聚合
        total_weight = sum(weights.values())
        if total_weight <= 0:
            weighted_score = 0.0
            contributions = {}
        else:
            contributions = {}
            weighted_sum = 0.0
            for code in self.index_codes:
                w = weights.get(code, 0.0)
                s = per_index.get(code, 0.0)
                weighted_sum += w * s
                contributions[code] = w * s
            weighted_score = weighted_sum / total_weight
            weighted_score = max(0.0, min(100.0, weighted_score))

        return IndexStrengthResult(
            per_index=per_index,
            weighted_score=weighted_score,
            weights_used=weights,
            contributions=contributions,
            sub_scores=sub_scores,
            explain=explain,
        )

    # ------------------------------------------------------------------
    # 单指数强度计算
    # ------------------------------------------------------------------
    def _calc_single_index_strength(self,
                                    code: str,
                                    df: pd.DataFrame,
                                    benchmark_df: Optional[pd.DataFrame]) -> Tuple[float, Dict[str, float], str]:
        """计算单个指数的综合强度

        Args:
            code: 指数代码
            df:   该指数的日线 DataFrame
            benchmark_df: 基准指数 DataFrame

        Returns:
            (总分, 子因子分数 dict, 解释文本)
        """
        subs: Dict[str, float] = {}

        # 依次计算各子因子
        subs['trend'] = self._calc_trend_score(df, self.cfg.get('trend', {}))
        subs['momentum'] = self._calc_momentum_score(df, self.cfg.get('momentum', {}))
        subs['relative_strength'] = self._calc_relative_strength(
            df, benchmark_df, self.cfg.get('relative_strength', {}))
        subs['breakout'] = self._calc_breakout_score(df, self.cfg.get('breakout', {}))
        subs['ma_alignment'] = self._calc_ma_alignment(df, self.cfg.get('ma_alignment', {}))
        subs['slope'] = self._calc_slope_score(df, self.cfg.get('slope', {}))
        subs['macd'] = self._calc_macd_score(df, self.cfg.get('macd', {}))
        subs['rsi'] = self._calc_rsi_score(df, self.cfg.get('rsi', {}))
        subs['atr'] = self._calc_atr_score(df, self.cfg.get('atr', {}))
        subs['volume'] = self._calc_volume_score(df, self.cfg.get('volume', {}))

        # 加权汇总
        total = 0.0
        sum_w = 0.0
        details = []
        for factor_name, weight in self.sub_weights.items():
            if factor_name in subs:
                s = subs[factor_name]
                total += s * weight
                sum_w += weight
                details.append(f"{factor_name}={s:.1f}分(w={weight})")

        if sum_w > 0:
            total_score = total / sum_w
        else:
            total_score = 0.0
        total_score = max(0.0, min(100.0, total_score))

        explain_str = f"{code}: 总分{total_score:.1f} | " + ", ".join(details)
        return total_score, subs, explain_str

    # ------------------------------------------------------------------
    # 子因子计算方法（各方法返回 0~100 分数）
    # ------------------------------------------------------------------

    @staticmethod
    def _calc_trend_score(df: pd.DataFrame, config: dict) -> float:
        """趋势得分：检查价格与多条均线的关系

        计算收盘价在 MA20、MA60、MA120 之上的比例。
        """
        if df is None or df.empty:
            return 50.0

        close = df['close']
        ma_periods = config.get('ma_periods', [20, 60, 120])
        bullish_threshold = config.get('bullish_threshold', 0.6)
        bearish_threshold = config.get('bearish_threshold', 0.3)

        above_count = 0
        total = 0
        for p in ma_periods:
            if len(close) >= p:
                ma = sma(close, p)
                if not ma.dropna().empty:
                    if close.iloc[-1] > ma.iloc[-1]:
                        above_count += 1
                    total += 1

        if total == 0:
            return 50.0

        ratio = above_count / total
        # ratio 0->1 映射到 0->100
        score = ratio * 100.0
        return max(0.0, min(100.0, score))

    @staticmethod
    def _calc_momentum_score(df: pd.DataFrame, config: dict) -> float:
        """动量得分：多周期收益率加权

        计算 5、20、60 日收益率，按配置权重汇总后归一化。
        """
        if df is None or df.empty:
            return 50.0

        close = df['close']
        periods = config.get('periods', [5, 20, 60])
        weights = config.get('weights', [0.3, 0.5, 0.2])

        weighted_ret = 0.0
        sum_w = 0.0
        for period, w in zip(periods, weights):
            if len(close) > period:
                ret = close.iloc[-1] / close.iloc[-period - 1] - 1
            else:
                ret = 0.0
            weighted_ret += ret * w
            sum_w += w

        if sum_w > 0:
            weighted_ret /= sum_w

        # 将收益率映射到 0-100
        # 使用 tanh 将 (-inf, +inf) 映射到 (-1, 1)，再转到 (0, 100)
        # 假设 ±5% 作为中性参考
        scaled = np.tanh(weighted_ret * 20)  # 5% 收益率 → tanh(1.0) ≈ 0.76
        score = (scaled + 1.0) * 50.0
        return max(0.0, min(100.0, score))

    @staticmethod
    def _calc_relative_strength(df: pd.DataFrame,
                                benchmark_df: Optional[pd.DataFrame],
                                config: dict) -> float:
        """相对强度得分：指数相对基准指数的累计收益比

        使用 config 中的 period 计算累计收益比，映射到 0~100。
        """
        if df is None or df.empty or benchmark_df is None or benchmark_df.empty:
            return 50.0

        period = config.get('period', 60)
        close = df['close']
        bench_close = benchmark_df['close']

        # 对齐日期
        common_idx = close.index.intersection(bench_close.index)
        if len(common_idx) < 2:
            return 50.0

        a = close.loc[common_idx]
        b = bench_close.loc[common_idx]

        if len(a) < period + 1:
            period = len(a) - 1
        if period < 1:
            return 50.0

        # 累计收益比
        asset_ret = a.iloc[-1] / a.iloc[-period - 1] - 1
        bench_ret = b.iloc[-1] / b.iloc[-period - 1] - 1

        # 相对强度 = 指数收益 - 基准收益（超额）
        excess = asset_ret - bench_ret

        # 将超额收益映射到 0-100
        # ±10% 超额作为极端
        scaled = np.tanh(excess * 15)
        score = (scaled + 1.0) * 50.0
        return max(0.0, min(100.0, score))

    @staticmethod
    def _calc_breakout_score(df: pd.DataFrame, config: dict) -> float:
        """突破得分：价格相对近期高点的位置

        使用 breakout_pct 指标，正的突破百分比 = 强势。
        """
        if df is None or df.empty:
            return 50.0

        lookback = config.get('lookback', 20)
        threshold_pct = config.get('threshold_pct', 0.0)

        close = df['close']
        if len(close) < lookback + 1:
            return 50.0

        bp = breakout_pct(close, lookback)
        if bp.dropna().empty:
            return 50.0

        latest_bp = bp.iloc[-1]

        if latest_bp > 0:
            # 正突破，按幅度给分
            score = 50.0 + min(50.0, latest_bp * 500)  # 0% → 50, +10% → 100
        else:
            # 未突破，按距离高点远近
            score = 50.0 + max(-50.0, latest_bp * 500)  # 0% → 50, -10% → 0
        return max(0.0, min(100.0, score))

    @staticmethod
    def _calc_ma_alignment(df: pd.DataFrame, config: dict) -> float:
        """均线排列得分：短期 > 中期 > 长期 的排列程度

        检查 MA5 > MA10 > MA20 > MA60 > MA120 的排列情况。
        """
        if df is None or df.empty:
            return 50.0

        close = df['close']
        periods = config.get('periods', [5, 10, 20, 60, 120])
        perfect_cnt = config.get('perfect_alignment_count', 5)
        min_cnt = config.get('min_alignment_count', 3)

        mas = {}
        valid = True
        for p in periods:
            if len(close) >= p:
                mas[p] = sma(close, p).iloc[-1]
            else:
                valid = False
                break

        if not valid or len(mas) < 2:
            return 50.0

        # 统计正确排列的相邻均线对数
        sorted_periods = sorted(mas.keys())
        correct_pairs = 0
        total_pairs = len(sorted_periods) - 1
        for i in range(len(sorted_periods) - 1):
            if mas[sorted_periods[i]] > mas[sorted_periods[i + 1]]:
                correct_pairs += 1

        if total_pairs == 0:
            return 50.0

        ratio = correct_pairs / total_pairs
        score = ratio * 100.0
        return max(0.0, min(100.0, score))

    @staticmethod
    def _calc_slope_score(df: pd.DataFrame, config: dict) -> float:
        """斜率得分：均线斜率方向

        计算 MA20 的线性斜率，正斜率为上涨趋势。
        """
        if df is None or df.empty:
            return 50.0

        close = df['close']
        ma_period = config.get('ma_period', 20)
        calc_period = config.get('calc_period', 5)
        bullish_slope = config.get('bullish_slope', 0.0)

        if len(close) < ma_period + calc_period:
            return 50.0

        ma_series = sma(close, ma_period).dropna()
        if len(ma_series) < calc_period + 1:
            return 50.0

        sl = slope(ma_series, calc_period)
        if sl.dropna().empty:
            return 50.0

        latest_slope = sl.iloc[-1]

        # 将斜率映射到 0-100
        # 正斜率越高分越高，负斜率越低分越低
        # 使用 tanh 压缩
        scaled = np.tanh(latest_slope * 50)  # 斜率 ±0.05 已接近极端
        score = (scaled + 1.0) * 50.0
        return max(0.0, min(100.0, score))

    @staticmethod
    def _calc_macd_score(df: pd.DataFrame, config: dict) -> float:
        """MACD 得分：MACD 金叉/死叉状态与柱状线方向

        DIF > DEA 且柱状线上升 = 多头强势。
        """
        if df is None or df.empty:
            return 50.0

        close = df['close']
        fast = config.get('fast', 12)
        slow = config.get('slow', 26)
        signal = config.get('signal', 9)

        if len(close) < slow + signal:
            return 50.0

        dif, dea, hist = macd(close, fast=fast, slow=slow, signal=signal)

        if dif.dropna().empty or dea.dropna().empty or hist.dropna().empty:
            return 50.0

        dif_latest = dif.iloc[-1]
        dea_latest = dea.iloc[-1]
        hist_latest = hist.iloc[-1]
        hist_prev = hist.iloc[-2] if len(hist) >= 2 else hist_latest

        score = 50.0  # 中性

        # DIF 与 DEA 关系
        if dif_latest > dea_latest:
            score += 20.0  # 多头
        else:
            score -= 20.0  # 空头

        # 柱状线方向
        if hist_latest > hist_prev:
            score += 15.0  # 动量增强
        elif hist_latest < hist_prev:
            score -= 15.0  # 动量减弱

        # DIF 绝对值大小（趋势强度）
        dif_norm = np.tanh(abs(dif_latest) * 5)
        if dif_latest > 0:
            score += dif_norm * 15.0
        else:
            score -= dif_norm * 15.0

        return max(0.0, min(100.0, score))

    @staticmethod
    def _calc_rsi_score(df: pd.DataFrame, config: dict) -> float:
        """RSI 得分：RSI 值直接映射

        RSI 本身就是 0-100 范围，直接使用最新值。
        但在超买/超卖区域做适度修正以防止极端值。
        """
        if df is None or df.empty:
            return 50.0

        close = df['close']
        period = config.get('period', 14)
        oversold = config.get('oversold', 30)
        overbought = config.get('overbought', 70)

        if len(close) < period + 1:
            return 50.0

        rsi_series = rsi(close, period)
        if rsi_series.dropna().empty:
            return 50.0

        rsi_val = rsi_series.iloc[-1]
        if np.isnan(rsi_val):
            return 50.0

        # RSI 本身 0-100，直接使用
        score = float(rsi_val)
        return max(0.0, min(100.0, score))

    @staticmethod
    def _calc_atr_score(df: pd.DataFrame, config: dict) -> float:
        """ATR 波动率得分：波动率百分位逆映射

        低波动 = 稳定市场 = 高分；高波动 = 风险加大 = 低分。
        """
        if df is None or df.empty:
            return 50.0

        high = df['high']
        low = df['low']
        close = df['close']
        period = config.get('period', 14)
        low_vol_threshold = config.get('low_vol_threshold', 0.5)

        if len(close) < period + 1:
            return 50.0

        atr_series = atr(high, low, close, period)
        if atr_series.dropna().empty:
            return 50.0

        # 计算 ATR 在回看期内的百分位
        atr_valid = atr_series.dropna()
        if len(atr_valid) < 2:
            return 50.0

        latest_atr = atr_valid.iloc[-1]
        percentile = (atr_valid <= latest_atr).mean()

        # 低波动 = 高分，高波动 = 低分
        # percentile 0->1 映射到 100->0
        score = (1.0 - percentile) * 100.0
        return max(0.0, min(100.0, score))

    @staticmethod
    def _calc_volume_score(df: pd.DataFrame, config: dict) -> float:
        """成交量得分：量比分析

        成交量相对 20 日均量的比值，温和放量 = 健康。
        """
        if df is None or df.empty:
            return 50.0

        vol = df['vol']
        if vol.dropna().empty:
            return 50.0

        ma_period = config.get('ma_period', 20)
        expansion_ratio = config.get('expansion_ratio', 1.2)
        contraction_ratio = config.get('contraction_ratio', 0.8)

        if len(vol) < ma_period:
            return 50.0

        vol_ma = sma(vol, ma_period)
        if vol_ma.dropna().empty:
            return 50.0

        latest_vol = vol.iloc[-1]
        latest_ma = vol_ma.iloc[-1]

        if latest_ma <= 0:
            return 50.0

        ratio = latest_vol / latest_ma

        # 量比 1.0 附近 = 最高分，偏离越远分越低
        if ratio >= 1.0:
            # 放量：1.0 -> 100, expansion_ratio -> 80, 更高 -> 递减
            if ratio <= expansion_ratio:
                score = 100.0 - (ratio - 1.0) / (expansion_ratio - 1.0) * 20.0
            else:
                score = 80.0 - min(80.0, (ratio - expansion_ratio) * 50)
        else:
            # 缩量：1.0 -> 100, contraction_ratio -> 70, 更低 -> 递减
            if ratio >= contraction_ratio:
                score = 100.0 - (1.0 - ratio) / (1.0 - contraction_ratio) * 30.0
            else:
                score = 70.0 - min(70.0, (contraction_ratio - ratio) * 100)

        return max(0.0, min(100.0, score))

    # ------------------------------------------------------------------
    # 动态权重调整
    # ------------------------------------------------------------------
    def _get_dynamic_weights(self, style_mode: Optional[str] = None) -> Dict[str, float]:
        """获取最终权重：基础权重 + 风格偏移

        Args:
            style_mode: 风格模式（growth / value / risk_on / risk_off）

        Returns:
            各指数最终权重 dict
        """
        weights = dict(self.base_weights)

        if style_mode and style_mode in self.indices_cfg.get('dynamic_weights', {}):
            offsets = self.indices_cfg['dynamic_weights'][style_mode]
            for code, offset in offsets.items():
                if code in weights:
                    weights[code] = max(0.0, weights[code] + offset)
                else:
                    weights[code] = max(0.0, offset)

        # 确保非负，若有 0 则保留但归一化会处理
        total = sum(weights.values())
        if total > 0:
            for code in weights:
                weights[code] /= total

        return weights
