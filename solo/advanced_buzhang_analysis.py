#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
高级补涨中军检测器 - 寻找起爆前的量价形态
6种经典形态识别算法
"""

import numpy as np
import pandas as pd
from typing import Dict, Any, Optional, List


class AdvancedBuzhangDetector:
    """高级补涨中军检测器 - 寻找起爆前的量价形态"""

    def __init__(self):
        # 形态权重配置
        self.pattern_weights = {
            'shrinkage_callback': 0.25,      # 缩量回调
            'platform_breakout': 0.20,       # 平台突破
            'rubbing_line': 0.10,            # 揉搓洗盘
            'bullish_engulfing': 0.10,       # 看涨吞没
            'golden_cross_strength': 0.15,   # 金叉强势
            'volume_spike': 0.20,            # 成交量异动
        }

    def analyze_stock(self, df: pd.DataFrame, zhongjun_df: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
        """
        分析单只股票的补涨潜力

        Args:
            df: 股票K线数据（按日期排序）
            zhongjun_df: 板块中军K线数据（用于相对强度分析）

        Returns:
            分析结果字典
        """
        if df is None or len(df) < 30:
            return {'valid': False, 'reason': '数据不足'}

        df = df.sort_values('trade_date').reset_index(drop=True)

        # 1. 计算基础指标
        closes = df['close'].astype(float).values
        volumes = df['vol'].astype(float).values
        amounts = df['amount'].astype(float).values
        pct_changes = df['pct_chg'].astype(float).values
        highs = df['high'].astype(float).values
        lows = df['low'].astype(float).values

        # 计算均线
        ma5 = pd.Series(closes).rolling(5).mean().values
        ma10 = pd.Series(closes).rolling(10).mean().values
        ma20 = pd.Series(closes).rolling(20).mean().values
        ma60 = pd.Series(closes).rolling(60).mean().values

        # 成交量均线
        vol_ma5 = pd.Series(volumes).rolling(5).mean().values
        vol_ma10 = pd.Series(volumes).rolling(10).mean().values
        vol_ma20 = pd.Series(volumes).rolling(20).mean().values

        # 2. 检查6种形态
        pattern_scores = {}
        detected_patterns = []

        # 形态1: 缩量回调（权重25%）
        shrinkage_score = self._check_shrinkage_callback(df, volumes, vol_ma5, vol_ma20, closes, ma20)
        if shrinkage_score > 0:
            pattern_scores['shrinkage_callback'] = shrinkage_score
            detected_patterns.append('shrinkage_callback')

        # 形态2: 平台突破（权重20%）
        breakout_score = self._check_platform_breakout(df, closes, ma5, ma10, ma20, volumes)
        if breakout_score > 0:
            pattern_scores['platform_breakout'] = breakout_score
            detected_patterns.append('platform_breakout')

        # 形态3: 揉搓线（权重10%）
        rubbing_score = self._check_rubbing_line(df, highs, lows, closes)
        if rubbing_score > 0:
            pattern_scores['rubbing_line'] = rubbing_score
            detected_patterns.append('rubbing_line')

        # 形态4: 看涨吞没（权重10%）
        engulfing_score = self._check_bullish_engulfing(df, closes, pct_changes)
        if engulfing_score > 0:
            pattern_scores['bullish_engulfing'] = engulfing_score
            detected_patterns.append('bullish_engulfing')

        # 形态5: 金叉强势（权重15%）
        golden_score = self._check_golden_cross_strength(df, closes, ma5, ma10, ma20, volumes)
        if golden_score > 0:
            pattern_scores['golden_cross_strength'] = golden_score
            detected_patterns.append('golden_cross_strength')

        # 形态6: 量能异动（权重20%）
        volume_score = self._check_volume_spike(df, volumes, vol_ma5, vol_ma20, amounts)
        if volume_score > 0:
            pattern_scores['volume_spike'] = volume_score
            detected_patterns.append('volume_spike')

        # 3. 计算综合评分
        overall_score = 0
        for pattern, score in pattern_scores.items():
            weight = self.pattern_weights.get(pattern, 0)
            overall_score += score * weight

        # 4. 相对强度分析（如果提供了中军数据）
        relative_strength = 0
        if zhongjun_df is not None and len(zhongjun_df) >= 20:
            relative_strength = self._calc_relative_strength(df, zhongjun_df)
            # 相对强度加成
            if relative_strength > 0 and len(detected_patterns) >= 2:
                overall_score += relative_strength * 0.1

        # 5. 验证有效性
        valid = overall_score >= 40 and len(detected_patterns) >= 1

        return {
            'valid': valid,
            'overall_score': overall_score,
            'detected_patterns': detected_patterns,
            'pattern_scores': pattern_scores,
            'relative_strength': relative_strength,
            'volume_ratio': self._calc_volume_ratio(volumes),
            'trend_direction': self._判断趋势方向(ma5, ma10, ma20),
        }

    def _check_shrinkage_callback(self, df: pd.DataFrame, volumes: np.ndarray,
                                   vol_ma5: np.ndarray, vol_ma20: np.ndarray,
                                   closes: np.ndarray, ma20: np.ndarray) -> float:
        """
        缩量回调形态
        特征：股价回调但成交量萎缩，可能是主力洗盘
        """
        if len(closes) < 30:
            return 0

        # 最近5日成交量明显萎缩
        recent_5_vol_avg = np.mean(volumes[-5:])
        prev_10_vol_avg = np.mean(volumes[-15:-5])

        # 检查是否缩量（缩量比例）
        shrinkage_ratio = recent_5_vol_avg / prev_10_vol_avg if prev_10_vol_avg > 0 else 1

        # 股价在MA20附近或之上
        current_price = closes[-1]
        ma20_current = ma20[-1]

        # 最近有回调迹象（股价从近期高点回调但未破MA20）
        recent_high = np.max(closes[-10:])
        callback_ratio = (recent_high - current_price) / recent_high if recent_high > 0 else 0

        # 评分逻辑
        score = 0
        if shrinkage_ratio < 0.7:  # 缩量明显
            score += 40
        elif shrinkage_ratio < 0.85:
            score += 25

        if 0.03 < callback_ratio < 0.15:  # 回调幅度适中（3%-15%）
            score += 30

        if current_price >= ma20_current * 0.98:  # 守住MA20
            score += 30

        return min(score, 100)

    def _check_platform_breakout(self, df: pd.DataFrame, closes: np.ndarray,
                                  ma5: np.ndarray, ma10: np.ndarray, ma20: np.ndarray,
                                  volumes: np.ndarray) -> float:
        """
        平台突破形态
        特征：长期横盘后放量突破
        """
        if len(closes) < 60:
            return 0

        # 检查是否在平台整理（最近20日振幅<15%）
        recent_20_closes = closes[-20:]
        platform_high = np.max(recent_20_closes)
        platform_low = np.min(recent_20_closes)
        platform_range = (platform_high - platform_low) / platform_low if platform_low > 0 else 0

        # 检查是否突破平台
        current_price = closes[-1]
        ma20_slope = (ma20[-1] - ma20[-10]) / ma20[-10] * 100 if ma20[-10] > 0 else 0

        # 突破时成交量是否放大
        vol_today = volumes[-1]
        vol_ma20 = np.mean(volumes[-21:-1])

        score = 0

        # 平台整理条件
        if platform_range < 0.15:
            score += 35

            # 突破平台
            if current_price > platform_high * 0.98:
                score += 30

            # MA20向上
            if ma20_slope > 0.5:
                score += 20

            # 放量突破
            if vol_today > vol_ma20 * 1.5:
                score += 15

        return min(score, 100)

    def _check_rubbing_line(self, df: pd.DataFrame, highs: np.ndarray,
                            lows: np.ndarray, closes: np.ndarray) -> float:
        """
        揉搓线形态
        特征：长上下影线，波动剧烈但收盘接近
        """
        if len(closes) < 5:
            return 0

        # 最近5日出现揉搓线特征
        score = 0
        for i in range(-5, 0):
            high = highs[i]
            low = lows[i]
            close = closes[i]

            body = abs(close - (high + low) / 2)
            total_range = high - low

            if total_range > 0:
                # 上下影线长度
                upper_shadow = high - max(closes[i], (high + low) / 2)
                lower_shadow = min(closes[i], (high + low) / 2) - low
                body_ratio = body / total_range

                # 揉搓线特征：上下影线较长，实体较小
                if upper_shadow > total_range * 0.2 and lower_shadow > total_range * 0.2:
                    if body_ratio < 0.3:  # 实体小
                        score += 20
                        break

        return min(score, 100)

    def _check_bullish_engulfing(self, df: pd.DataFrame, closes: np.ndarray,
                                   pct_changes: np.ndarray) -> float:
        """
        看涨吞没形态
        特征：今日阳线吞没昨日阴线
        """
        if len(closes) < 3:
            return 0

        score = 0

        # 检查最近3日
        for i in range(-3, 0):
            if i == -len(closes):
                continue

            today_close = closes[i]
            yesterday_close = closes[i - 1]

            # 今日上涨，昨日下跌
            if pct_changes[i] > 2 and pct_changes[i - 1] < -1:
                # 阳线实体吞没阴线
                today_body = today_close - max(closes[i - 1], closes[i])
                yesterday_body = min(closes[i - 1], closes[i - 1]) - closes[i - 1]

                if today_body > abs(yesterday_body) * 1.5:
                    score += 50
                    break

        return min(score, 100)

    def _check_golden_cross_strength(self, df: pd.DataFrame, closes: np.ndarray,
                                      ma5: np.ndarray, ma10: np.ndarray,
                                      ma20: np.ndarray, volumes: np.ndarray) -> float:
        """
        金叉强势形态
        特征：均线金叉后价格走强
        """
        if len(closes) < 25:
            return 0

        score = 0

        # MA5上穿MA10金叉
        golden_cross_idx = None
        for i in range(-15, -5):
            if ma5[i - 1] < ma10[i - 1] and ma5[i] >= ma10[i]:
                golden_cross_idx = i
                break

        if golden_cross_idx is not None:
            # 金叉后价格持续走强
            price_after_golden = np.mean(closes[golden_cross_idx:])
            price_before_golden = np.mean(closes[golden_cross_idx - 5:golden_cross_idx])

            if price_after_golden > price_before_golden * 1.02:
                score += 40

            # 成交量配合
            vol_after = np.mean(volumes[golden_cross_idx:])
            vol_before = np.mean(volumes[golden_cross_idx - 10:golden_cross_idx - 5])
            if vol_after > vol_before * 1.2:
                score += 30

            # 均线多头排列
            if ma5[-1] > ma10[-1] > ma20[-1]:
                score += 30

        return min(score, 100)

    def _check_volume_spike(self, df: pd.DataFrame, volumes: np.ndarray,
                             vol_ma5: np.ndarray, vol_ma20: np.ndarray,
                             amounts: np.ndarray) -> float:
        """
        量能异动形态
        特征：成交量异常放大
        """
        if len(volumes) < 25:
            return 0

        score = 0

        # 3日均量与20日均量比
        vol_3_avg = np.mean(volumes[-3:])
        vol_20_avg = np.mean(volumes[-21:-1])
        vol_ratio = vol_3_avg / vol_20_avg if vol_20_avg > 0 else 1

        # 成交额放大
        amount_3_avg = np.mean(amounts[-3:])
        amount_20_avg = np.mean(amounts[-21:-1])
        amount_ratio = amount_3_avg / amount_20_avg if amount_20_avg > 0 else 1

        # 放量程度
        if vol_ratio > 2.0:
            score += 50
        elif vol_ratio > 1.5:
            score += 35
        elif vol_ratio > 1.2:
            score += 20

        # 成交额同步放大
        if amount_ratio > 1.5:
            score += 30
        elif amount_ratio > 1.2:
            score += 15

        # 温和放量更佳（不要暴量）
        if 1.3 < vol_ratio < 2.5:
            score += 20

        return min(score, 100)

    def _calc_relative_strength(self, df: pd.DataFrame, zhongjun_df: pd.DataFrame) -> float:
        """
        计算相对强度（与板块中军对比）
        """
        if df is None or zhongjun_df is None:
            return 0

        closes = df['close'].astype(float).values
        zj_closes = zhongjun_df['close'].astype(float).values

        if len(closes) < 20 or len(zj_closes) < 20:
            return 0

        # 计算20日相对强弱
        stock_ret = (closes[-1] - closes[-20]) / closes[-20] * 100
        zj_ret = (zj_closes[-1] - zj_closes[-20]) / zj_closes[-20] * 100

        relative = stock_ret - zj_ret

        # 相对强度评分
        if relative > 5:
            return 30  # 明显跑赢
        elif relative > 0:
            return 15  # 小幅跑赢
        else:
            return 0

    def _calc_volume_ratio(self, volumes: np.ndarray) -> float:
        """计算量比（3日均量/20日均量）"""
        if len(volumes) < 23:
            return 1.0
        vol_3_avg = np.mean(volumes[-3:])
        vol_20_avg = np.mean(volumes[-23:-3])
        return vol_3_avg / vol_20_avg if vol_20_avg > 0 else 1.0

    def _判断趋势方向(self, ma5: np.ndarray, ma10: np.ndarray, ma20: np.ndarray) -> str:
        """判断趋势方向"""
        if len(ma5) < 10:
            return "震荡"

        # 各均线斜率
        ma5_slope = (ma5[-1] - ma5[-5]) / ma5[-5] * 100
        ma10_slope = (ma10[-1] - ma10[-5]) / ma10[-5] * 100
        ma20_slope = (ma20[-1] - ma20[-5]) / ma20[-5] * 100

        avg_slope = (ma5_slope + ma10_slope + ma20_slope) / 3

        if avg_slope > 1:
            return "上升"
        elif avg_slope < -1:
            return "下降"
        else:
            return "震荡"
