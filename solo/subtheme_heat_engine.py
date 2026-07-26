#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Sub-theme Heat Matrix Engine
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
为每个子主题独立计算：
  - Intrinsic Score    （内在质量）
  - Tradable Score     （可交易性）
  - Lifecycle Stage    （生命周期阶段）
  - Transition Score   （阶段迁移概率）
  - PRE_ROTATE Signal  （轮动预信号）
  - Confidence         （综合置信度）
  - Expected Days      （预期停留天数）

输出：
  - subtheme_matrix    每个母主题的子主题矩阵
  - contribution_analysis  贡献度分解
  - internal_rotation  内部轮动方向检测

依赖：numpy, pandas, theme_trend_sentiment_score (K线数据)
不修改任何现有接口。
"""

import sys
import os
import json
import math
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(BASE_DIR)
sys.path.append(parent_dir)
sys.path.append(BASE_DIR)

import theme_trend_sentiment_score as theme_ts

# ── 生命周期阶段枚举 ──
LIFECYCLE_STAGES = [
    '潜伏',    # 筑底期：价格在MA20下方蓄势
    '升温',    # 启动期：突破MA20，量能温和放大
    '主升',    # 主升期：MA5>MA10>MA20多头排列，强烈动量
    '分歧',    # 分歧期：高波动，多空争夺
    '退潮',    # 退潮期：跌破MA10，量能萎缩
    '弱势',    # 弱势期：跌破MA60，趋势破坏
]

LIFECYCLE_DAYS_ESTIMATE = {
    '潜伏': 15,
    '升温': 10,
    '主升': 8,
    '分歧': 5,
    '退潮': 12,
    '弱势': 20,
}

# ── 阶段迁移阈值 ──
TRANSITION_THRESHOLDS = {
    ('潜伏', '升温'): 0.55,
    ('升温', '主升'): 0.60,
    ('主升', '分歧'): 0.50,
    ('分歧', '退潮'): 0.45,
    ('退潮', '弱势'): 0.50,
    ('弱势', '潜伏'): 0.40,
    ('升温', '分歧'): 0.35,  # 直接转分歧（加速失败）
}

# ── 贡献度权重 ──
CONTRIBUTION_WEIGHTS = {
    'intrinsic': 0.35,
    'tradable': 0.20,
    'lifecycle': 0.25,
    'transition': 0.20,
}


class SubthemeHeatEngine:
    """
    Sub-theme Heat Matrix Engine
    
    输入:
      themes_output:     {母主题: [{code, name, via, score, industry, concepts}, ...]}
      stocks_output:     {code: {name, industry, themes, concepts, subtheme, ...}}
      stock_subtheme_map:{code: {subtheme, confidence, features}}
      subtheme_heat:     {母主题: {subthemes: {子主题: {stock_count, heat_score, ...}}}}
      trade_date:        str YYYYMMDD
    
    输出:
      report dict with subtheme_matrix, contribution_analysis, internal_rotation
    """

    def __init__(self, themes_output, stocks_output, stock_subtheme_map,
                 subtheme_heat, trade_date):
        self.themes_output = themes_output
        self.stocks_output = stocks_output
        self.stock_subtheme_map = stock_subtheme_map
        self.subtheme_heat = subtheme_heat
        self.trade_date = trade_date

        # 构建反索引: {母主题: {子主题: [code, ...]}}
        self._subtheme_stocks = self._build_subtheme_stock_index()
        
        # 缓存K线数据
        self._kline_df = None
        self._kline_groups = {}
        self._index_df = None
        self._market_ret_10 = 0.0

    # ─── 索引构建 ──────────────────────────────────────────

    def _build_subtheme_stock_index(self) -> Dict[str, Dict[str, List[str]]]:
        """构建 {母主题: {子主题: [code]}} 索引"""
        idx = defaultdict(lambda: defaultdict(list))
        for code, info in self.stocks_output.items():
            st = info.get('subtheme', '')
            if not st:
                continue
            themes = info.get('themes', [])
            parent = themes[0] if themes else ''
            if parent and st:
                idx[parent][st].append(code)
        return {k: dict(v) for k, v in idx.items()}

    # ─── K线数据加载 ───────────────────────────────────────

    def _ensure_kline(self):
        """确保K线数据和指数数据已加载"""
        if self._kline_df is not None:
            return
        
        # 收集所有子主题成分股的 code
        all_codes = set()
        for parent, sub_dict in self._subtheme_stocks.items():
            for codes in sub_dict.values():
                all_codes.update(codes)

        if not all_codes:
            # 从 heat matrix 的 top_stocks 中收集
            for parent, ph in self.subtheme_heat.items():
                for sub_name, sh in ph.get('subthemes', {}).items():
                    for ts in sh.get('top_stocks', []):
                        if ts.get('code'):
                            all_codes.add(ts['code'])

        if not all_codes:
            print("  [HeatEngine] 无子主题成分股数据")
            return

        start = (datetime.strptime(self.trade_date, "%Y%m%d") -
                 timedelta(days=90)).strftime("%Y%m%d")
        
        print(f"  [HeatEngine] 加载 {len(all_codes)} 只子主题成分股K线...")
        self._kline_df = theme_ts.get_daily_kline(list(all_codes), start, self.trade_date)
        
        if self._kline_df is not None and not self._kline_df.empty:
            for code, sub in self._kline_df.groupby("ts_code"):
                self._kline_groups[code] = sub.sort_values("trade_date")
            print(f"  [HeatEngine] K线数据: {len(self._kline_df)} 条 / {len(self._kline_groups)} 只")
        else:
            print("  [HeatEngine] 警告: K线数据为空")

        # 指数数据
        self._index_df = theme_ts.get_index_kline("000300.SH")
        if self._index_df is not None and not self._index_df.empty:
            self._index_df = self._index_df.sort_values("trade_date")
            closes = self._index_df["close"].astype(float).values
            if len(closes) >= 11:
                self._market_ret_10 = (closes[-1] / closes[-11] - 1) * 100

    # ─── 1. Intrinsic Score ────────────────────────────────

    def compute_intrinsic(self, sub_name: str, sub_heat_data: dict) -> Dict[str, Any]:
        """
        内在质量评分（基于子主题自身质地）
        
        因子:
          - keyword_penetration: 关键词渗透深度
          - core_ratio:          核心公司覆盖率
          - concentration:       集中度（越低越均匀健康）
          - confidence_avg:      平均分配置信度
        
        输出: {score: 0-100, features: {...}}
        """
        features = {}
        weights = {'penetration': 0.35, 'core': 0.30, 'concentration': 0.15, 'confidence': 0.20}

        # 从 heat matrix 获取基础数据
        features['penetration'] = sub_heat_data.get('keyword_penetration', 0.0)
        features['core_ratio'] = sub_heat_data.get('core_ratio', 0.0)
        features['concentration_raw'] = sub_heat_data.get('concentration', 0.0)
        # 集中度反转：过高 = 不健康（少数股票撑场面）
        features['concentration_inv'] = 1.0 - min(features['concentration_raw'], 1.0)
        
        # 平均置信度：从 stock_subtheme_map 获取
        confs = []
        parent_theme = self._find_parent_by_subtheme(sub_name)
        if parent_theme:
            codes = self._subtheme_stocks.get(parent_theme, {}).get(sub_name, [])
            for code in codes:
                st_info = self.stock_subtheme_map.get(code, {})
                confs.append(st_info.get('subtheme_confidence', 0.0))
        features['confidence_avg'] = np.mean(confs) if confs else 0.5

        score = (
            features['penetration'] * weights['penetration'] +
            features['core_ratio'] * weights['core'] +
            features['concentration_inv'] * weights['concentration'] +
            features['confidence_avg'] * weights['confidence']
        )
        score = round(min(score * 100, 100), 1)

        return {'score': score, 'features': features}

    def _find_parent_by_subtheme(self, sub_name: str) -> Optional[str]:
        for parent, sub_dict in self._subtheme_stocks.items():
            if sub_name in sub_dict:
                return parent
        return None

    # ─── 2. Tradable Score ─────────────────────────────────

    def compute_tradable(self, sub_name: str, sub_heat_data: dict) -> Dict[str, Any]:
        """
        可交易性评分
        
        因子:
          - stock_count:    成分股数量
          - parent_ratio:   占母主题比例
          - heat_score:     heat matrix 的综合热度
        
        输出: {score: 0-100, features: {...}}
        """
        stock_count = sub_heat_data.get('stock_count', 0)
        parent_ratio = sub_heat_data.get('parent_ratio', 0)
        heat_score = sub_heat_data.get('heat_score', 0)

        # 数量分: 1~5只=25, 6~15=50, 16~30=75, >30=100
        if stock_count <= 1:
            count_score = 10
        elif stock_count <= 5:
            count_score = 25 + (stock_count - 1) * 6.25
        elif stock_count <= 15:
            count_score = 50 + (stock_count - 5) * 2.5
        elif stock_count <= 30:
            count_score = 75 + (stock_count - 15) * 1.67
        else:
            count_score = 100

        # 比例分
        ratio_score = parent_ratio * 100

        # 热度分
        heat_score_pct = heat_score * 100

        score = round(count_score * 0.40 + ratio_score * 0.25 + heat_score_pct * 0.35, 1)

        return {
            'score': min(score, 100),
            'features': {
                'stock_count': stock_count,
                'parent_ratio': parent_ratio,
                'count_score': count_score,
                'ratio_score': ratio_score,
            }
        }

    # ─── 3. Lifecycle Stage ────────────────────────────────

    def compute_lifecycle(self, sub_name: str, sub_heat_data: dict) -> Dict[str, Any]:
        """
        生命周期阶段识别
        
        基于成分股的量价特征判断：
          - 均线位置（MA5/MA10/MA20/MA60突破率）
          - 收益率斜率（3/5/10日平均收益）
          - 量比变化
        
        输出: {stage, score, features, signal, expected_days}
        """
        self._ensure_kline()

        parent_theme = self._find_parent_by_subtheme(sub_name)
        if not parent_theme:
            return {'stage': '潜伏', 'score': 0, 'signal': 'HOLD',
                    'expected_days': 15, 'features': {}}

        codes = self._subtheme_stocks.get(parent_theme, {}).get(sub_name, [])
        if not codes:
            return {'stage': '潜伏', 'score': 0, 'signal': 'HOLD',
                    'expected_days': 15, 'features': {}}

        # 聚合成分股的K线统计
        pct_above_ma5 = []
        pct_above_ma10 = []
        pct_above_ma20 = []
        pct_above_ma60 = []
        avg_ret_3 = []
        avg_ret_5 = []
        avg_ret_10 = []
        avg_vol_ratio = []

        for code in codes:
            kdf = self._kline_groups.get(code)
            if kdf is None or len(kdf) < 5:
                continue

            latest = kdf.iloc[-1]
            close = float(latest.get('close', latest.get('pct_chg', 0)))
            ma5 = float(latest.get('ma5', 0))
            ma10 = float(latest.get('ma10', 0))
            ma20 = float(latest.get('ma20', 0))
            ma60 = float(latest.get('ma60', 0))
            vol = float(latest.get('vol', 0))

            # 均线位置
            pct_above_ma5.append(1.0 if ma5 > 0 and close > ma5 else 0.0)
            pct_above_ma10.append(1.0 if ma10 > 0 and close > ma10 else 0.0)
            pct_above_ma20.append(1.0 if ma20 > 0 and close > ma20 else 0.0)
            pct_above_ma60.append(1.0 if ma60 > 0 and close > ma60 else 0.0)

            # 收益率
            if len(kdf) >= 4:
                closes = kdf['close'].astype(float).values
                avg_ret_3.append((closes[-1] / closes[-4] - 1) * 100)
            if len(kdf) >= 6:
                closes = kdf['close'].astype(float).values
                avg_ret_5.append((closes[-1] / closes[-6] - 1) * 100)
            if len(kdf) >= 11:
                closes = kdf['close'].astype(float).values
                avg_ret_10.append((closes[-1] / closes[-11] - 1) * 100)

            # 量比（近3日均量 / 近20日均量）
            if len(kdf) >= 20:
                vol_series = kdf['vol'].astype(float).values
                vol_3 = np.mean(vol_series[-3:])
                vol_20 = np.mean(vol_series[-20:])
                avg_vol_ratio.append(vol_3 / max(vol_20, 1))
            else:
                avg_vol_ratio.append(1.0)

        if not pct_above_ma5:
            return {'stage': '潜伏', 'score': 0, 'signal': 'HOLD',
                    'expected_days': 15, 'features': {}}

        # 聚合统计
        features = {}
        features['ma5_ratio'] = np.mean(pct_above_ma5)
        features['ma10_ratio'] = np.mean(pct_above_ma10)
        features['ma20_ratio'] = np.mean(pct_above_ma20)
        features['ma60_ratio'] = np.mean(pct_above_ma60)
        features['avg_ret_3'] = np.mean(avg_ret_3) if avg_ret_3 else 0
        features['avg_ret_5'] = np.mean(avg_ret_5) if avg_ret_5 else 0
        features['avg_ret_10'] = np.mean(avg_ret_10) if avg_ret_10 else 0
        features['avg_vol_ratio'] = np.mean(avg_vol_ratio) if avg_vol_ratio else 1.0

        # ── 阶段判定决策树 ──
        ma5 = features['ma5_ratio']
        ma10 = features['ma10_ratio']
        ma20 = features['ma20_ratio']
        ma60 = features['ma60_ratio']
        ret3 = features['avg_ret_3']
        ret5 = features['avg_ret_5']
        vol = features['avg_vol_ratio']

        if ma5 > 0.6 and ma10 > 0.5 and ma20 > 0.4 and ret3 > 2:
            # 主升：大部分股票在MA5上方，短期动量强
            stage = '主升'
            score = min(100, (ma5 * 30 + ma10 * 25 + ma20 * 20 + min(ret3, 10) * 2.5))
        elif ma5 > 0.4 and ma20 > 0.3 and vol > 1.2 and ret3 > 0:
            # 升温：突破MA20，量能放大
            stage = '升温'
            score = min(100, (ma20 * 35 + ma5 * 20 + min(vol, 2) * 15 + max(ret3, 0) * 3))
        elif ma10 < 0.3 and ma20 < 0.3 and ma60 < 0.2 and ret5 < -3:
            # 弱势：全面跌破MA60
            stage = '弱势'
            score = min(100, (1 - ma10) * 30 + (1 - ma20) * 25 + abs(ret5) * 2)
        elif ma10 < 0.4 and ma20 < 0.4 and ret5 < -1:
            # 退潮：跌破MA10
            stage = '退潮'
            score = min(100, (1 - ma10) * 35 + (1 - ma20) * 25 + abs(ret5) * 2)
        elif vol > 1.3 and abs(ret3) > 3 and (ma5 > 0.3 and ma5 < 0.6):
            # 分歧：放量高波动，均线附近
            stage = '分歧'
            score = min(100, vol * 25 + abs(ret3) * 5 + ma5 * 20)
        else:
            # 潜伏：其余情况
            stage = '潜伏'
            score = min(100, (1 - ma20) * 20 + ma5 * 15 + ma60 * 10)

        score = round(score, 1)
        expected_days = LIFECYCLE_DAYS_ESTIMATE.get(stage, 10)

        # 信号
        if stage in ('主升', '升温'):
            signal = 'ROTATE_IN'
        elif stage == '分歧':
            signal = 'ROTATE_OUT'
        elif stage in ('退潮', '弱势'):
            signal = 'AVOID'
        else:
            signal = 'HOLD'

        # SHORT信号：从主升/升温刚转分歧
        if stage == '分歧' and features.get('avg_vol_ratio', 0) > 1.5:
            signal = 'SHORT'

        return {
            'stage': stage,
            'score': score,
            'signal': signal,
            'expected_days': expected_days,
            'features': features,
        }

    # ─── 4. Transition Score ───────────────────────────────

    def compute_transition(self, lifecycle: dict, intrinsic_score: float, 
                           tradable_score: float) -> Dict[str, Any]:
        """
        阶段迁移概率
        
        因子:
          - 当前阶段自然衰减概率
          - 动量支持度（lifecycle 内的收益率斜率）
          - 内在质量支持度
          - 可交易性支持度
        
        输出: {transition_prob, next_stage, pre_rotate, days_left}
        """
        stage = lifecycle.get('stage', '潜伏')
        features = lifecycle.get('features', {})
        lifecycle_score = lifecycle.get('score', 0)

        # 可能的下一阶段
        transitions = {
            '潜伏': [('升温', 0.55), ('分歧', 0.05)],
            '升温': [('主升', 0.50), ('分歧', 0.25), ('退潮', 0.05)],
            '主升': [('分歧', 0.45), ('退潮', 0.10), ('弱势', 0.02)],
            '分歧': [('退潮', 0.35), ('弱势', 0.10), ('主升', 0.15)],
            '退潮': [('弱势', 0.40), ('潜伏', 0.15), ('升温', 0.05)],
            '弱势': [('潜伏', 0.30), ('退潮', 0.10), ('升温', 0.02)],
        }

        # 基础迁移概率
        candidates = transitions.get(stage, [])
        if not candidates:
            return {'transition_prob': 0, 'next_stage': stage,
                    'pre_rotate': False, 'days_left': 10, 'details': {}}

        # 方向性动量修正
        ret3 = features.get('avg_ret_3', 0)
        ret5 = features.get('avg_ret_5', 0)
        vol = features.get('avg_vol_ratio', 1.0)

        momentum_boost = 0
        if stage == '潜伏' and ret3 > 0.5 and vol > 1.1:
            momentum_boost = 0.15  # 潜伏末期放量 → 升温概率增加
        elif stage == '升温' and ret3 > 3 and vol > 1.3:
            momentum_boost = 0.20  # 强升温 → 主升概率增加
        elif stage == '主升' and (ret3 < 0 or vol > 2.0):
            momentum_boost = 0.15  # 主升末期缩量或爆量 → 分歧概率增加
        elif stage == '分歧' and ret5 < -3:
            momentum_boost = 0.15  # 分歧持续下跌 → 退潮概率增加
        elif stage == '退潮' and ret5 > -1 and vol < 0.8:
            momentum_boost = 0.10  # 退潮缩量企稳 → 潜伏概率增加

        # 内在质量修正
        quality_boost = 0
        if intrinsic_score < 30:
            quality_boost = -0.10  # 质量差 → 迁移受阻
        elif intrinsic_score > 70:
            quality_boost = 0.08   # 质量好 → 正向迁移加速

        next_stage = candidates[0][0]
        best_prob = candidates[0][1]
        
        # 应用修正
        prob = min(best_prob + momentum_boost + quality_boost, 0.95)
        prob = max(prob, 0.05)

        # PRE_ROTATE 判定：概率 > 阈值且动量信号明确
        threshold = 0.55 if stage in ('潜伏', '升温') else 0.50
        pre_rotate = prob > threshold and momentum_boost > 0

        # 预计剩余天数（基于概率的反比关系）
        days_left = max(2, int(10 * (1 - prob) + 2))

        return {
            'transition_prob': round(prob, 3),
            'next_stage': next_stage,
            'pre_rotate': pre_rotate,
            'days_left': days_left,
            'details': {
                'base_prob': best_prob,
                'momentum_boost': round(momentum_boost, 3),
                'quality_boost': round(quality_boost, 3),
            }
        }

    # ─── 5. 综合 Confidence ────────────────────────────────

    def compute_confidence(self, intrinsic: dict, tradable: dict,
                           lifecycle: dict, transition: dict) -> float:
        """综合置信度 = 四维加权"""
        i_score = intrinsic['score'] / 100
        t_score = tradable['score'] / 100
        l_score = lifecycle['score'] / 100
        tr_prob = transition['transition_prob']

        # 一致性惩罚：各维度分歧大时降权
        scores = [i_score, t_score, l_score]
        std = np.std(scores) if len(scores) > 0 else 0
        consistency_penalty = min(std * 0.3, 0.15)  # 最多罚0.15

        raw = (
            i_score * CONTRIBUTION_WEIGHTS['intrinsic'] +
            t_score * CONTRIBUTION_WEIGHTS['tradable'] +
            l_score * CONTRIBUTION_WEIGHTS['lifecycle'] +
            tr_prob * CONTRIBUTION_WEIGHTS['transition']
        )
        confidence = max(0, min(1, raw - consistency_penalty))
        return round(confidence, 3)

    # ─── 6. Contribution Analysis ──────────────────────────

    def compute_contribution(self, sub_name: str, sub_heat_data: dict,
                             composite_score: float, lifecycle: dict,
                             intrinsic: dict) -> Dict[str, Any]:
        """
        贡献度 = SubthemeScore × MoneyFlow × MarketCapWeight
        
        MoneyFlow:    基于量比估算资金流向强度
        MarketCapWeight: 成分股总市值占母主题比例（用stock_count proxy）
        """
        self._ensure_kline()

        # MoneyFlow: 量比 > 1.2 且 收益率 > 0 → 资金流入
        vol_ratio = lifecycle.get('features', {}).get('avg_vol_ratio', 1.0)
        ret3 = lifecycle.get('features', {}).get('features', {}).get('avg_ret_3', 0)
        
        if vol_ratio > 1.2 and ret3 > 0:
            money_flow = min(vol_ratio * 0.5, 1.0)
        elif vol_ratio < 0.8:
            money_flow = max(vol_ratio * 0.3, 0.1)
        else:
            money_flow = 0.5

        # MarketCapWeight: 使用 parent_ratio 近似
        market_cap_weight = sub_heat_data.get('parent_ratio', 0.1)

        # 原始贡献度
        contribution = composite_score * money_flow * market_cap_weight

        return {
            'contribution_raw': round(contribution, 4),
            'money_flow': round(money_flow, 3),
            'market_cap_weight': round(market_cap_weight, 3),
            'contribution_score': round(min(contribution * 100, 100), 1),
        }

    # ─── 7. Internal Rotation Detection ────────────────────

    def detect_internal_rotation(self, parent_theme: str) -> List[Dict[str, Any]]:
        """
        检测母主题内部的子主题轮动方向
        
        规则:
          1. 比较各子主题的 composite_score 与生命周期信号
          2. 识别从"退潮/弱势→升温/主升"的轮入方向
          3. 识别从"主升/分歧→退潮/弱势"的轮出方向
        
        输出: [{'from': 'A', 'to': 'B', 'strength': 0.8, 'reason': '...'}, ...]
        """
        # 获取该母主题的所有子主题评分
        subtheme_results = {}
        for sub_name, heat_data in self.subtheme_heat.get(parent_theme, {}).get('subthemes', {}).items():
            # 用内部缓存的评分
            intrinsic = self.compute_intrinsic(sub_name, heat_data)
            tradable = self.compute_tradable(sub_name, heat_data)
            lifecycle = self.compute_lifecycle(sub_name, heat_data)
            transition = self.compute_transition(lifecycle, intrinsic['score'], tradable['score'])
            confidence = self.compute_confidence(intrinsic, tradable, lifecycle, transition)
            contribution = self.compute_contribution(sub_name, heat_data,
                                                      intrinsic['score'], lifecycle, intrinsic)
            
            composite_score = (
                intrinsic['score'] * CONTRIBUTION_WEIGHTS['intrinsic'] +
                tradable['score'] * CONTRIBUTION_WEIGHTS['tradable'] +
                lifecycle['score'] * CONTRIBUTION_WEIGHTS['lifecycle'] +
                transition['transition_prob'] * 100 * CONTRIBUTION_WEIGHTS['transition']
            )
            subtheme_results[sub_name] = {
                'score': round(composite_score, 1),
                'stage': lifecycle['stage'],
                'signal': lifecycle['signal'],
                'transition_prob': transition['transition_prob'],
                'pre_rotate': transition['pre_rotate'],
                'contribution': contribution['contribution_score'],
            }

        # 按得分排序
        sorted_subs = sorted(subtheme_results.items(), key=lambda x: -x[1]['score'])

        rotations = []
        # 寻找轮动对: 高得分上升期 vs 低得分下降期
        for i, (name_a, data_a) in enumerate(sorted_subs):
            if data_a['stage'] in ('主升', '升温', '潜伏'):
                for j, (name_b, data_b) in enumerate(sorted_subs):
                    if i == j:
                        continue
                    if data_b['stage'] in ('退潮', '弱势') or (
                            data_b['stage'] == '分歧' and data_b['signal'] == 'ROTATE_OUT'):
                        # A 轮入, B 轮出
                        strength = min(1.0, (data_a['score'] - data_b['score']) / 100 *
                                       (1.0 if data_a['pre_rotate'] else 0.7))
                        if strength > 0.15:
                            rotations.append({
                                'from': name_b,
                                'to': name_a,
                                'strength': round(strength, 2),
                                'reason': f"{name_b}({data_b['stage']})→{name_a}({data_a['stage']})",
                                'from_signal': data_b['signal'],
                                'to_signal': data_a['signal'],
                            })

        # 去重 + 排序
        seen_pairs = set()
        unique_rotations = []
        for r in sorted(rotations, key=lambda x: -x['strength']):
            pair_key = f"{r['from']}→{r['to']}"
            if pair_key not in seen_pairs:
                seen_pairs.add(pair_key)
                unique_rotations.append(r)

        return unique_rotations[:5]  # 最多5组

    # ─── 主报告生成 ────────────────────────────────────────

    def generate_report(self) -> Dict[str, Any]:
        """
        生成 Sub-theme Heat Matrix 完整报告
        
        输出:
        {
          'subtheme_matrix': {母主题: [{name, score, stage, signal, confidence, ...}]}
          'contribution_analysis': {母主题: [{name, contribution%, ...}]}
          'internal_rotation': {母主题: [{'from','to','strength','reason'}, ...]}
          'report_metadata': {generated_at, total_subthemes, ...}
        }
        """
        print("  [HeatEngine] 生成 Sub-theme Heat Matrix Report...")

        matrix = {}
        contribution_map = {}
        rotation_map = {}

        total_subthemes = 0

        # 按母主题遍历
        parent_themes = sorted(self.subtheme_heat.keys())

        for parent_theme in parent_themes:
            sub_dict = self.subtheme_heat[parent_theme].get('subthemes', {})
            if not sub_dict:
                continue

            subtheme_list = []
            contrib_list = []

            for sub_name, heat_data in sub_dict.items():
                # 四维评分
                intrinsic = self.compute_intrinsic(sub_name, heat_data)
                tradable = self.compute_tradable(sub_name, heat_data)
                lifecycle = self.compute_lifecycle(sub_name, heat_data)
                transition = self.compute_transition(lifecycle, intrinsic['score'], tradable['score'])
                confidence = self.compute_confidence(intrinsic, tradable, lifecycle, transition)
                contribution = self.compute_contribution(sub_name, heat_data,
                                                          intrinsic['score'], lifecycle, intrinsic)

                # 综合分
                composite_score = (
                    intrinsic['score'] * CONTRIBUTION_WEIGHTS['intrinsic'] +
                    tradable['score'] * CONTRIBUTION_WEIGHTS['tradable'] +
                    lifecycle['score'] * CONTRIBUTION_WEIGHTS['lifecycle'] +
                    transition['transition_prob'] * 100 * CONTRIBUTION_WEIGHTS['transition']
                )
                composite_score = round(composite_score, 1)

                subtheme_list.append({
                    'name': sub_name,
                    'score': composite_score,
                    'stage': lifecycle['stage'],
                    'signal': lifecycle['signal'],
                    'confidence': confidence,
                    'expected_days': lifecycle['expected_days'],
                    'pre_rotate': transition['pre_rotate'],
                    'transition_prob': transition['transition_prob'],
                    'next_stage': transition['next_stage'],
                    'days_left': transition['days_left'],
                    'intrinsic_score': intrinsic['score'],
                    'tradable_score': tradable['score'],
                    'lifecycle_score': lifecycle['score'],
                    'contribution_pct': contribution['contribution_score'],
                })
                contrib_list.append({
                    'name': sub_name,
                    'contribution_score': contribution['contribution_score'],
                    'money_flow': contribution['money_flow'],
                })
                total_subthemes += 1

            # 排序：按综合分降序
            subtheme_list.sort(key=lambda x: -x['score'])
            matrix[parent_theme] = subtheme_list

            # 贡献度分析 → 归一化为百分比
            total_contrib = sum(c['contribution_score'] for c in contrib_list)
            if total_contrib > 0:
                for c in contrib_list:
                    c['contribution_pct'] = round(c['contribution_score'] / total_contrib * 100, 1)
            else:
                for c in contrib_list:
                    c['contribution_pct'] = round(100 / max(len(contrib_list), 1), 1)
            contrib_list.sort(key=lambda x: -x['contribution_pct'])
            contribution_map[parent_theme] = contrib_list

            # 内部轮动
            rotations = self.detect_internal_rotation(parent_theme)
            if rotations:
                rotation_map[parent_theme] = rotations

        report = {
            'subtheme_matrix': matrix,
            'contribution_analysis': contribution_map,
            'internal_rotation': rotation_map,
            'report_metadata': {
                'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
                'trade_date': self.trade_date,
                'total_parent_themes': len(parent_themes),
                'total_subthemes': total_subthemes,
                'total_stocks_analyzed': len(self.stock_subtheme_map),
            }
        }

        # 打印摘要
        n_rot = sum(len(v) for v in rotation_map.values())
        print(f"  [HeatEngine] 报告完成: {total_subthemes}子主题 / {n_rot}轮动方向")
        for pt in parent_themes:
            if pt in matrix:
                top = matrix[pt][:3]
                top_str = ', '.join(f"{t['name']}({t['score']})" for t in top)
                print(f"    {pt}: {top_str}")

        return report


# ═══════════════════════════════════════════════════════════
# 便利函数：一键运行
# ═══════════════════════════════════════════════════════════

def run_subtheme_heat_engine(themes_output, stocks_output, stock_subtheme_map,
                              subtheme_heat, trade_date) -> dict:
    """
    一键运行 Sub-theme Heat Matrix Engine
    
    输入: 与 SubthemeHeatEngine.__init__ 相同
    输出: report dict
    """
    engine = SubthemeHeatEngine(
        themes_output, stocks_output, stock_subtheme_map,
        subtheme_heat, trade_date
    )
    return engine.generate_report()


if __name__ == '__main__':
    # 独立调试入口
    CACHE_DIR = os.path.join(BASE_DIR, "cache_daily")
    json_file = os.path.join(CACHE_DIR, "theme_stock_map_v2_20260724.json")
    if os.path.exists(json_file):
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        from build_theme_stock_map_v2 import (
            calc_subtheme_heat_matrix
        )
        themes_output = {}  # 需要从data重建
        stocks_output = data.get('stocks', {})
        stock_subtheme_map = data.get('subtheme_dynamic_correlation', {})
        subtheme_heat = data.get('subtheme_heat_matrix', {})
        trade_date = data.get('trade_date', '20260724')

        # 重建 themes_output
        for code, info in stocks_output.items():
            for theme in info.get('themes', []):
                if theme not in themes_output:
                    themes_output[theme] = []
                themes_output[theme].append({
                    'code': code,
                    'name': info.get('name', ''),
                    'industry': info.get('industry', ''),
                    'concepts': info.get('concepts', []),
                })

        report = run_subtheme_heat_engine(
            themes_output, stocks_output, stock_subtheme_map,
            subtheme_heat, trade_date
        )

        # 输出到文件
        out_dir = os.path.join(BASE_DIR, "report_daily")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"subtheme_heat_report_{trade_date}.json")
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"报告已保存: {out_path}")
