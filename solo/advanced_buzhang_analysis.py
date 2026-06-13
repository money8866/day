
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
高级补涨中军检测器 - 简化版
核心逻辑：大容量、大成交、基本面健康、题材热门
"""

import numpy as np
import pandas as pd
from typing import Dict, Any, Optional, List


class AdvancedBuzhangDetector:
    """高级补涨中军检测器 - 简化版"""

    def __init__(self):
        # 权重配置（总和100%）
        self.weights = {
            'big_amount': 0.30,          # 大成交额（30%）
            'turnover_rate': 0.15,       # 换手率（15%）
            'big_market_cap': 0.15,      # 大市值（15%）
            'price_trend': 0.15,         # 价格趋势健康（15%）
            'volume_coordination': 0.10, # 量价配合（10%）
            'technicals': 0.05,          # 技术面健康（5%）
            'gain_control': 0.10,        # 涨幅控制（10%）- 新增：防止过度拉升的股票被选为补涨中军
        }
        # 指标名称
        self.metric_names = {
            'big_amount': '大成交额',
            'turnover_rate': '换手率',
            'big_market_cap': '大市值',
            'price_trend': '价格趋势',
            'volume_coordination': '量价配合',
            'technicals': '技术面健康',
            'gain_control': '涨幅控制'
        }

    def analyze_stock(self, df: pd.DataFrame, zhongjun_df: Optional[pd.DataFrame] = None, 
                     market_cap: Optional[float] = None, turnover_rate: Optional[float] = None) -> Dict[str, Any]:
        """
        分析单只股票的补涨潜力

        Args:
            df: 股票K线数据（按日期排序）
            zhongjun_df: 板块中军K线数据（用于相对强度分析）
            market_cap: 市值（单位：亿）
            turnover_rate: 换手率（%）

        Returns:
            分析结果字典
        """
        if df is None or len(df) < 30:
            return {'valid': False, 'reason': '数据不足'}

        df = df.sort_values('trade_date').reset_index(drop=True)

        closes = df['close'].astype(float).values
        volumes = df['vol'].astype(float).values
        amounts = df['amount'].astype(float).values
        pct_changes = df['pct_chg'].astype(float).values

        metrics = {}
        detected = []

        # 1. 大成交额评分
        amount_score = self._score_big_amount(amounts)
        metrics['big_amount'] = amount_score
        if amount_score > 40:
            detected.append('大成交额')

        # 2. 换手率评分
        turnover_score = self._score_turnover_rate(turnover_rate)
        metrics['turnover_rate'] = turnover_score
        if turnover_score > 40:
            detected.append('换手活跃')

        # 3. 大市值评分
        mc_score = self._score_big_market_cap(market_cap)
        metrics['big_market_cap'] = mc_score
        if mc_score > 40:
            detected.append('大市值')

        # 4. 涨幅控制评分（新增）- 防止过度拉升的股票被选为补涨中军
        gain_score = self._score_gain_control(closes, pct_changes)
        metrics['gain_control'] = gain_score
        if gain_score > 40:
            detected.append('涨幅健康')

        # 5. 价格趋势健康
        trend_score = self._score_price_trend(closes)
        metrics['price_trend'] = trend_score
        if trend_score > 40:
            detected.append('价格健康')

        # 6. 量价配合
        vol_score = self._score_volume_coordination(closes, volumes, amounts)
        metrics['volume_coordination'] = vol_score
        if vol_score > 40:
            detected.append('量价配合')

        # 7. 技术面健康
        tech_score = self._score_technicals(closes, volumes)
        metrics['technicals'] = tech_score
        if tech_score > 40:
            detected.append('技术面良好')

        # 计算综合评分
        total_score = 0
        for key, val in metrics.items():
            total_score += val * self.weights[key]

        # 如果涨幅控制评分过低，直接标记为无效（大幅放宽，避免过度过滤强势主题股）
        if gain_score < 10:
            return {
                'valid': False,
                'overall_score': total_score,
                'metrics': metrics,
                'detected_patterns': detected,
                'pattern_scores': metrics,
                'reason': '涨幅过高，不适合作为补涨中军'
            }

        return {
            'valid': True,
            'overall_score': total_score,
            'metrics': metrics,
            'detected_patterns': detected,
            'pattern_scores': metrics
        }

    def _score_big_amount(self, amounts: np.ndarray) -> float:
        """大成交额评分"""
        if len(amounts) < 5:
            return 0
        
        # 近20日平均成交额（元转亿元）
        avg_20 = np.mean(amounts[-21:-1]) / 100000000 if len(amounts) >= 21 else np.mean(amounts) / 100000000
        
        score = 0
        if avg_20 >= 80:  # 80亿+
            score = 100
        elif avg_20 >= 50:  # 50亿+
            score = 80
        elif avg_20 >= 30:  # 30亿+
            score = 60
        elif avg_20 >= 20:  # 20亿+
            score = 40
        elif avg_20 >= 10:  # 10亿+
            score = 20
        
        # 当日成交活跃，额外加分
        if len(amounts) >= 5:
            avg_5 = np.mean(amounts[-5:-1])
            if amounts[-1] > avg_5 * 1.5:
                score += 20
        
        return min(score, 100)

    def _score_turnover_rate(self, turnover_rate: Optional[float]) -> float:
        """换手率评分（25%以下越高越好）"""
        if turnover_rate is None or turnover_rate <= 0:
            return 0
        
        score = 0
        # 换手率评分逻辑（优化版）：
        # - 25%以下：越高越好，代表交投活跃，机构进出方便
        # - 15%-25%：非常活跃，评分最高
        # - 10%-15%：活跃，评分较高
        # - 5%-10%：适度活跃，评分良好
        # - 1%-5%：一般活跃，评分一般
        # - <1%：不够活跃，机构进出困难
        # - >25%：过度投机，风险较高
        
        if 15.0 < turnover_rate <= 25.0:
            # 非常活跃，最佳区间，评分最高
            score = 100
        elif 10.0 < turnover_rate <= 15.0:
            # 活跃区间，评分较高
            score = 90
        elif 5.0 < turnover_rate <= 10.0:
            # 适度活跃，评分良好
            score = 80
        elif 1.0 < turnover_rate <= 5.0:
            # 一般活跃，评分一般
            score = 70
        elif 0.5 <= turnover_rate <= 1.0:
            # 不够活跃
            score = 50
        elif turnover_rate > 25.0:
            # 过度投机，风险较高
            score = 40
        else:
            # 换手率太低（<0.5%），机构进出困难
            score = 30
        
        return score

    def _score_big_market_cap(self, market_cap: Optional[float]) -> float:
        """大市值评分"""
        if market_cap is None or market_cap <= 0:
            return 0
        
        score = 0
        if market_cap >= 1500:  # 1500亿+
            score = 100
        elif market_cap >= 1000:  # 1000亿+
            score = 80
        elif market_cap >= 600:  # 600亿+
            score = 60
        elif market_cap >= 400:  # 400亿+
            score = 40
        elif market_cap >= 200:  # 200亿+
            score = 20
        
        return score

    def _score_price_trend(self, closes: np.ndarray) -> float:
        """价格趋势健康评分"""
        if len(closes) < 20:
            return 0
        
        score = 0
        
        # MA20向上
        ma20 = self._sma(closes, 20)
        if len(ma20) >= 5:
            slope = (ma20[-1] - ma20[-5]) / ma20[-5]
            if slope > 0.01:  # 0.5日涨幅1%以上
                score += 40
        
        # 价格在MA5之上
        if len(closes) >= 5:
            ma5 = self._sma(closes, 5)
            if closes[-1] > ma5[-1]:
                score += 30
        
        # 近5日没有暴跌
        if len(closes) >= 10:
            recent_max = np.max(closes[-10:-1])
            if closes[-1] > recent_max * 0.85:  # 回撤不超过15%
                score += 30
        
        return min(score, 100)

    def _score_volume_coordination(self, closes: np.ndarray, volumes: np.ndarray, amounts: np.ndarray) -> float:
        """量价配合评分"""
        if len(closes) < 10 or len(volumes) < 10:
            return 0
        
        score = 0
        
        # 量价齐升：最近3日
        if len(closes) >= 5 and len(volumes) >= 5:
            price_up = (closes[-1] > closes[-2]) and (closes[-2] > closes[-3])
            vol_up = (volumes[-1] > volumes[-2]) and (volumes[-2] > volumes[-3])
            if price_up and vol_up:
                score += 40
        
        # 温和放量，不是爆量
        if len(volumes) >= 20:
            avg_vol_20 = np.mean(volumes[-21:-1])
            avg_vol_5 = np.mean(volumes[-5:-1])
            if 1.2 <= avg_vol_5 / avg_vol_20 <= 3:  # 20%~3倍量
                score += 30
        
        # 没有连续放量暴跌
        if len(closes) >= 3 and len(volumes) >= 3:
            bad_signals = 0
            for i in range(-3, 0):
                if closes[i] < closes[i-1] * 0.97 and volumes[i] > volumes[i-1] * 1.5:
                    bad_signals += 1
            if bad_signals == 0:
                score += 30
        
        return min(score, 100)

    def _score_technicals(self, closes: np.ndarray, volumes: np.ndarray) -> float:
        """技术面健康评分"""
        if len(closes) < 20:
            return 0
        
        score = 0
        
        # 均线多头排列（放宽条件：允许MA5 > MA10或股价在所有均线上方）
        ma5 = self._sma(closes, 5)
        ma10 = self._sma(closes, 10)
        ma20 = self._sma(closes, 20)
        if len(ma5) >= 1 and len(ma10) >= 1 and len(ma20) >= 1 and len(closes) >= 1:
            close = closes[-1]
            # 条件1：完美多头排列
            if ma5[-1] > ma10[-1] > ma20[-1]:
                score += 50
            # 条件2：股价在所有均线上方且MA5 > MA10（放宽条件）
            elif close > ma5[-1] and close > ma10[-1] and close > ma20[-1] and ma5[-1] > ma10[-1]:
                score += 40
            # 条件3：股价在MA20上方（最宽松条件）
            elif close > ma20[-1]:
                score += 25
        
        # 成交量活跃，不是极度缩量
        if len(volumes) >= 10:
            avg_vol = np.mean(volumes[-10:-1])
            if volumes[-1] > avg_vol * 0.6:  # 不低于平均60%
                score += 50
        
        return min(score, 100)

    def _sma(self, data: np.ndarray, window: int) -> np.ndarray:
        """简单移动平均"""
        if len(data) < window:
            return data
        weights = np.ones(window) / window
        return np.convolve(data, weights, mode='valid')

    def get_metric_name(self, metric_id: str) -> str:
        """获取指标名称"""
        return self.metric_names.get(metric_id, metric_id)

    def _get_pattern_name(self, pattern_id: str) -> str:
        """获取模式名称（兼容老接口）"""
        return self.metric_names.get(pattern_id, pattern_id)

    def _score_gain_control(self, closes: np.ndarray, pct_changes: np.ndarray) -> float:
        """
        涨幅控制评分 - 防止过度拉升的股票被选为补涨中军
        
        补涨中军应该是涨幅相对合理、尚未过度炒作的股票，而不是已经大幅拉升的股票。
        如果一只股票近期涨幅过大，应该被排除出补涨中军候选。
        """
        if len(closes) < 20:
            return 0
        
        score = 100
        
        # 1. 检查短期涨幅（近5日）
        if len(closes) >= 10:
            recent_5d = closes[-6:-1] if len(closes) >= 6 else closes
            if len(recent_5d) >= 2:
                gain_5d = (closes[-1] - recent_5d[0]) / recent_5d[0] * 100
                # 近5日涨幅超过30%，扣分
                if gain_5d > 30:
                    score -= 40
                elif gain_5d > 20:
                    score -= 20
                elif gain_5d > 15:
                    score -= 10
        
        # 2. 检查中期涨幅（近20日）
        if len(closes) >= 30:
            recent_20d = closes[-21:-1]
            if len(recent_20d) >= 2:
                gain_20d = (closes[-1] - recent_20d[0]) / recent_20d[0] * 100
                # 近20日涨幅超过60%，扣分
                if gain_20d > 60:
                    score -= 40
                elif gain_20d > 40:
                    score -= 25
                elif gain_20d > 30:
                    score -= 15
        
        # 3. 检查长期涨幅（近60日）
        if len(closes) >= 70:
            recent_60d = closes[-61:-1]
            if len(recent_60d) >= 2:
                gain_60d = (closes[-1] - recent_60d[0]) / recent_60d[0] * 100
                # 近60日涨幅超过100%，扣分
                if gain_60d > 100:
                    score -= 50
                elif gain_60d > 80:
                    score -= 35
                elif gain_60d > 60:
                    score -= 20
        
        # 4. 检查是否有连续涨停或大幅拉升（仅检查最近5日，避免过滤已充分调整的股票）
        if len(pct_changes) >= 8:
            recent_pct = pct_changes[-5:]  # 改为检查最近5日，避免过滤已调整的股票
            # 检查是否有连续大涨（连续3日涨幅≥5%）
            streak = 0
            for pct in recent_pct:
                if pct >= 5:
                    streak += 1
                    if streak >= 3:
                        score -= 30
                        break
                else:
                    streak = 0
        
        return max(0, min(score, 100))
