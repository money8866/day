#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Stock Alpha Engine V4.1 - Role Evolution Engine
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
为每个子主题内的每只股票自动计算角色(Role)：
  Leader / Core / Momentum / Beta / Follower / Defensive / Weak

角色根据子主题生命周期阶段动态演化权重。
所有角色每日重新计算，不缓存固定角色。

依赖：numpy, pandas, theme_trend_sentiment_score (K线缓存)
完全独立模块。不修改任何现有接口。
"""

import sys
import os
import math
import json
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(BASE_DIR)
sys.path.append(parent_dir)
sys.path.append(BASE_DIR)

import theme_trend_sentiment_score as theme_ts

# ── 8个角色定义 ──
ROLES = ['Emotion Leader', 'Momentum Leader', 'Institution Core',
         'Momentum', 'Beta', 'Follower', 'Defensive', 'Weak']

# ── 聚合大类（兼容旧版 role 判断） ──
ROLE_CLASS_MAP = {
    'Emotion Leader': 'Leader',
    'Momentum Leader': 'Leader',
    'Institution Core': 'Core',
    'Momentum': 'Momentum',
    'Beta': 'Beta',
    'Follower': 'Follower',
    'Defensive': 'Defensive',
    'Weak': 'Weak',
}

# ── 角色描述 ──
ROLE_DESCRIPTIONS = {
    'Emotion Leader': '情绪龙：短线涨停/连板驱动，换手活跃，辨识度高，情绪带动性最强',
    'Momentum Leader': '趋势龙：相对强度领先，新高突破，趋势稳定，决定板块趋势方向',
    'Institution Core': '机构中军：成交额/市值主导，趋势稳定，回撤小，机构资金核心持仓',
    'Momentum': '加速上涨，新高突破，放量，Alpha快速提升',
    'Beta': '创业板/科创板，高波动，弹性最大',
    'Follower': '涨幅落后，Alpha改善，资金流入改善',
    'Defensive': '回撤小，波动低，抗跌',
    'Weak': 'Alpha最低，趋势下降，资金流出',
}

# ── 生命周期演化矩阵 ──
# 每个阶段各角色的权重乘数
LIFECYCLE_EVOLUTION = {
    '萌芽': {
        'Emotion Leader': 3.0, 'Momentum Leader': 3.0, 'Institution Core': 2.0,
        'Momentum': 5.0, 'Beta': 5.0,
        'Follower': 1.0, 'Defensive': 0.5, 'Weak': 0.0,
    },
    '成长': {  # 升温
        'Emotion Leader': 5.0, 'Momentum Leader': 5.0, 'Institution Core': 5.0,
        'Momentum': 4.0, 'Beta': 2.0,
        'Follower': 3.0, 'Defensive': 0.5, 'Weak': 0.0,
    },
    '主升': {
        'Emotion Leader': 5.0, 'Momentum Leader': 5.0, 'Institution Core': 5.0,
        'Momentum': 3.0, 'Beta': 1.0,
        'Follower': 4.0, 'Defensive': 0.5, 'Weak': 0.0,
    },
    '分歧': {  # 分歧期
        'Emotion Leader': 3.0, 'Momentum Leader': 3.0, 'Institution Core': 4.0,
        'Momentum': 2.0, 'Beta': 2.0,
        'Follower': 3.0, 'Defensive': 3.0, 'Weak': 1.0,
    },
    '退潮': {  # 衰退
        'Emotion Leader': 1.5, 'Momentum Leader': 2.0, 'Institution Core': 4.0,
        'Momentum': 1.0, 'Beta': 1.0,
        'Follower': 3.0, 'Defensive': 3.0, 'Weak': 2.0,
    },
    '弱势': {  # 筑底/弱势
        'Emotion Leader': 1.0, 'Momentum Leader': 1.5, 'Institution Core': 3.0,
        'Momentum': 1.5, 'Beta': 1.5,
        'Follower': 4.0, 'Defensive': 3.0, 'Weak': 3.0,
    },
    '潜伏': {  # 筑底前期
        'Emotion Leader': 1.0, 'Momentum Leader': 1.0, 'Institution Core': 3.0,
        'Momentum': 4.0, 'Beta': 3.0,
        'Follower': 5.0, 'Defensive': 3.0, 'Weak': 1.0,
    },
}

# ── 默认演化权重（生命周期未匹配时） ──
DEFAULT_EVOLUTION = {
    'Emotion Leader': 1.0, 'Momentum Leader': 1.0, 'Institution Core': 1.0,
    'Momentum': 1.0, 'Beta': 1.0,
    'Follower': 1.0, 'Defensive': 1.0, 'Weak': 1.0,
}


class StockRoleEngine:
    """
    Role Evolution Engine
    
    输入:
      subtheme_stocks: List[Dict], 子主题内所有股票信息
        [{code, name, industry, concepts, ...}]
      kline_groups: Dict[str, DataFrame], 股票K线数据 {code: kline_df}
      subtheme_stage: str, 子主题生命周期阶段 (潜伏/升温/主升/分歧/退潮/弱势)
      stock_basic_info: Dict[str, Dict], 股票基本信息 {code: {name, industry, ...}}
      daily_basic_info: Dict[str, Dict], 每日基础数据 {code: {total_mv, ...}}
    
    输出:
      {code: {
        role, role_score, role_reason, leader_similarity, confidence,
        role_features: {relative_strength, recognition, ...}
      }}
    """

    def __init__(self, subtheme_stocks: List[Dict], kline_groups: Dict[str, pd.DataFrame],
                 subtheme_stage: str = '潜伏', stock_basic_info: Dict = None,
                 daily_basic_info: Dict = None):
        self.subtheme_stocks = subtheme_stocks
        self.kline_groups = kline_groups
        self.subtheme_stage = subtheme_stage
        self.stock_basic_info = stock_basic_info or {}
        self.daily_basic_info = daily_basic_info or {}

        # 构建代码→名称快速索引
        self._code_map = {s['code']: s for s in subtheme_stocks}
        self._codes = [s['code'] for s in subtheme_stocks]

        # 缓存计算结果
        self._features_cache = {}

    # ═══════════════════════════════════════════════════════════
    # 特征提取
    # ═══════════════════════════════════════════════════════════

    def _get_kline(self, code: str) -> Optional[pd.DataFrame]:
        """安全获取个股K线数据"""
        return self.kline_groups.get(code)

    def _compute_relative_strength(self, code: str) -> float:
        """相对强度：个股收益率 / 子主题平均收益率
        
        在子主题内排名归一化到[0,1]
        """
        kdf = self._get_kline(code)
        if kdf is None or len(kdf) < 5:
            return 0.0

        closes = kdf['close'].astype(float).values
        ret_3 = (closes[-1] / closes[-4] - 1) * 100 if len(closes) >= 4 else 0
        ret_5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else 0

        # 加权：近3日权重更高
        strength = ret_3 * 0.6 + ret_5 * 0.4

        # 在子主题内排名
        all_strengths = []
        for c in self._codes:
            ckdf = self._get_kline(c)
            if ckdf is not None and len(ckdf) >= 4:
                c_closes = ckdf['close'].astype(float).values
                c_ret = (c_closes[-1] / c_closes[-4] - 1) * 100
                all_strengths.append(c_ret)
            else:
                all_strengths.append(0)

        if not all_strengths:
            return 0.0

        # 排名百分位
        rank = sum(1 for s in all_strengths if s <= strength)
        return rank / max(len(all_strengths), 1)

    def _compute_recognition(self, code: str) -> float:
        """辨识度：成交额/换手率在子主题内的排名
        
        识别度高 = 成交额大 + 换手率高 + 市值大
        """
        kdf = self._get_kline(code)
        if kdf is None or len(kdf) < 3:
            return 0.0

        # 最近3日平均成交额
        vol_series = kdf['vol'].astype(float).values[-3:]
        avg_vol = np.mean(vol_series)

        # 最近3日换手率
        turnover_series = kdf.get('turnover', kdf.get('turn', kdf.get('amount', vol_series)))
        if 'turnover' in kdf.columns:
            turn_series = kdf['turnover'].astype(float).values[-3:]
            avg_turn = np.mean(turn_series)
        else:
            avg_turn = 0

        # 在子主题内排名
        all_vols = []
        for c in self._codes:
            ckdf = self._get_kline(c)
            if ckdf is not None and len(ckdf) >= 3:
                all_vols.append(np.mean(ckdf['vol'].astype(float).values[-3:]))
            else:
                all_vols.append(0)

        if not all_vols:
            return 0.0

        # 成交额排名百分位 × 0.6 + 换手率排名百分位 × 0.4
        vol_rank = sum(1 for v in all_vols if v <= avg_vol) / max(len(all_vols), 1)
        return vol_rank * 0.6 + 0.4  # 换手率作为bonus

    def _compute_trend_stability(self, code: str) -> float:
        """趋势稳定性：收益率的标准差逆归一化
        
        低波动 = 高稳定性
        """
        kdf = self._get_kline(code)
        if kdf is None or len(kdf) < 10:
            return 0.5

        ret_series = kdf['pct_chg'].astype(float).values[-20:] if 'pct_chg' in kdf.columns else \
                     np.diff(kdf['close'].astype(float).values) / kdf['close'].astype(float).values[:-1] * 100
        ret_series = ret_series[-10:] if len(ret_series) > 10 else ret_series

        if len(ret_series) < 5:
            return 0.5

        vol = np.std(ret_series)

        # 波动率越低越好：sigmoid逆映射
        # 目标：vol=1 → 0.8, vol=3 → 0.5, vol=8 → 0.2
        stability = 1.0 / (1.0 + math.exp((vol - 3.0) / 2.0))
        return max(0.0, min(1.0, stability))

    def _compute_leader_prob(self, code: str) -> float:
        """历史龙头概率：历史涨停次数/连板高度（代理：近期是否涨停/大涨）"""
        kdf = self._get_kline(code)
        if kdf is None or len(kdf) < 10:
            return 0.0

        # 取近60个交易日统计大涨日（>7%为涨停/大涨代理）
        recent = kdf.tail(min(60, len(kdf)))
        if 'pct_chg' in recent.columns:
            pcts = recent['pct_chg'].astype(float).values
        else:
            closes = recent['close'].astype(float).values
            pcts = np.diff(closes) / closes[:-1] * 100
            pcts = np.append(pcts, 0)

        big_up_days = sum(1 for p in pcts if p > 7)
        ratio = big_up_days / max(len(pcts), 1)

        # 连续大涨（连板代理）
        max_consecutive = 0
        current = 0
        for p in pcts:
            if p > 7:
                current += 1
                max_consecutive = max(max_consecutive, current)
            else:
                current = 0

        consecutive_bonus = min(max_consecutive * 0.15, 0.3)

        return min(ratio * 2 + consecutive_bonus, 1.0)

    def _compute_momentum_acceleration(self, code: str) -> float:
        """动量加速度：近3日收益 - 近10日收益
        
        正则化到[0,1]，加速越快分越高
        """
        kdf = self._get_kline(code)
        if kdf is None or len(kdf) < 11:
            return 0.0

        closes = kdf['close'].astype(float).values
        ret_3 = (closes[-1] / closes[-4] - 1) * 100 if len(closes) >= 4 else 0
        ret_10 = (closes[-1] / closes[-11] - 1) * 100 if len(closes) >= 11 else 0

        acceleration = ret_3 - ret_10  # 正值 = 加速

        # 将加速度映射到[0,1]
        return max(0.0, min(1.0, (acceleration + 10) / 20))

    def _compute_new_high_score(self, code: str) -> float:
        """新高突破：价格接近/突破10日、20日最高点"""
        kdf = self._get_kline(code)
        if kdf is None or len(kdf) < 20:
            return 0.0

        closes = kdf['close'].astype(float).values
        latest = closes[-1]

        high_10 = np.max(closes[-10:])
        high_20 = np.max(closes)

        # 接近10日高点(%) 和 20日高点(%)
        near_10 = latest / high_10 if high_10 > 0 else 0
        near_20 = latest / high_20 if high_20 > 0 else 0

        # weighted: 10日新高更关键
        return max(0, min(1, (near_10 - 0.85) / 0.15)) * 0.6 + \
               max(0, min(1, (near_20 - 0.80) / 0.20)) * 0.4

    def _compute_volume_surge(self, code: str) -> float:
        """放量程度：近3日均量 / 近20日均量"""
        kdf = self._get_kline(code)
        if kdf is None or len(kdf) < 20:
            return 0.0

        vol = kdf['vol'].astype(float).values
        vol_3 = np.mean(vol[-3:])
        vol_20 = np.mean(vol[-20:])

        ratio = vol_3 / max(vol_20, 1)
        # ratio=1.0 → 0.5, ratio=2.0 → 0.8, ratio=0.5 → 0.2
        return max(0.0, min(1.0, (ratio - 0.5) / 2.0 + 0.5))

    def _compute_alpha_improvement(self, code: str) -> float:
        """Alpha改善：近期相对强度 vs 中期相对强度的变化"""
        kdf = self._get_kline(code)
        if kdf is None or len(kdf) < 15:
            return 0.0

        closes = kdf['close'].astype(float).values
        ret_3 = (closes[-1] / closes[-4] - 1) * 100 if len(closes) >= 4 else 0
        ret_10 = (closes[-1] / closes[-11] - 1) * 100 if len(closes) >= 11 else 0

        # 子主题均值
        all_ret_3 = []
        all_ret_10 = []
        for c in self._codes:
            ckdf = self._get_kline(c)
            if ckdf is not None:
                c_closes = ckdf['close'].astype(float).values
                if len(c_closes) >= 4:
                    all_ret_3.append((c_closes[-1] / c_closes[-4] - 1) * 100)
                if len(c_closes) >= 11:
                    all_ret_10.append((c_closes[-1] / c_closes[-11] - 1) * 100)

        avg_ret_3 = np.mean(all_ret_3) if all_ret_3 else 0
        avg_ret_10 = np.mean(all_ret_10) if all_ret_10 else 0

        # Alpha = 个股收益 - 子主题平均收益
        alpha_3 = ret_3 - avg_ret_3
        alpha_10 = ret_10 - avg_ret_10

        # Alpha改善 = 近期Alpha - 中期Alpha
        improvement = alpha_3 - alpha_10

        return max(0.0, min(1.0, (improvement + 10) / 20))

    def _compute_alpha_score(self, code: str) -> float:
        """
        Alpha评分：个股近3日相对子主题均值的超额收益。
        范围 [0,1]，越高表示相对越强势。
        """
        kdf = self._get_kline(code)
        if kdf is None or len(kdf) < 15:
            return 0.0

        closes = kdf['close'].astype(float).values
        ret_3 = (closes[-1] / closes[-4] - 1) * 100 if len(closes) >= 4 else 0

        # 子主题均值
        all_ret_3 = []
        for c in self._codes:
            ckdf = self._get_kline(c)
            if ckdf is not None:
                c_closes = ckdf['close'].astype(float).values
                if len(c_closes) >= 4:
                    all_ret_3.append((c_closes[-1] / c_closes[-4] - 1) * 100)

        avg_ret_3 = np.mean(all_ret_3) if all_ret_3 else 0
        alpha_3 = ret_3 - avg_ret_3
        return max(0.0, min(1.0, (alpha_3 + 10) / 20))

    def _compute_beta_score(self, code: str) -> float:
        """Beta弹性：创业板(300)/科创板(688) + 高波动"""
        score = 0.0

        # 市场板块（降低代码加分，防止高Alpha双创股被"抢"到Beta）
        raw_code = code.split('.')[0]
        if raw_code.startswith('688'):
            score += 0.25  # 科创板（原0.5）
        elif raw_code.startswith('300'):
            score += 0.20  # 创业板（原0.4）
        elif raw_code.startswith('000') or raw_code.startswith('002'):
            score += 0.10  # 中小板/主板（原0.2）

        # 高波动性
        kdf = self._get_kline(code)
        if kdf is not None and len(kdf) >= 10:
            if 'pct_chg' in kdf.columns:
                pcts = kdf['pct_chg'].astype(float).values[-20:]
            else:
                closes = kdf['close'].astype(float).values
                pcts = np.diff(closes) / closes[:-1] * 100
                pcts = np.append(pcts, 0)[-20:]
            vol = np.std(pcts) if len(pcts) > 0 else 0
            # vol=1 → 0.2, vol=3 → 0.6, vol=6 → 0.9
            vol_score = 1.0 / (1.0 + math.exp((2.5 - vol) / 1.5))
            score += vol_score * 0.5

        return min(score, 1.0)

    def _compute_money_flow(self, code: str) -> float:
        """资金流向：量比 + 涨幅方向"""
        kdf = self._get_kline(code)
        if kdf is None or len(kdf) < 20:
            return 0.0

        vol = kdf['vol'].astype(float).values
        vol_3 = np.mean(vol[-3:])
        vol_20 = np.mean(vol[-20:])
        vol_ratio = vol_3 / max(vol_20, 1)

        if 'pct_chg' in kdf.columns:
            ret_3 = np.mean(kdf['pct_chg'].astype(float).values[-3:])
        else:
            closes = kdf['close'].astype(float).values
            ret_3 = (closes[-1] / closes[-4] - 1) * 100 if len(closes) >= 4 else 0

        # 放量 + 上涨 = 资金流入
        if vol_ratio > 1.2 and ret_3 > 0:
            inflow = min(vol_ratio * 0.3, 0.5) + min(ret_3 * 0.05, 0.5)
        elif vol_ratio < 0.8 and ret_3 < 0:
            inflow = -(0.3 + (1 - vol_ratio) * 0.5)  # 缩量下跌 = 流出
        else:
            inflow = 0.2 * (vol_ratio - 1.0) + 0.05 * ret_3

        return max(0.0, min(1.0, (inflow + 1.0) / 2.0))

    def _compute_drawdown(self, code: str) -> float:
        """最大回撤（近20日）：越小越好"""
        kdf = self._get_kline(code)
        if kdf is None or len(kdf) < 5:
            return 0.5

        closes = kdf['close'].astype(float).values[-20:] if len(kdf) >= 20 else kdf['close'].astype(float).values
        peak = np.maximum.accumulate(closes)
        dd = (peak - closes) / peak
        max_dd = np.max(dd) if len(dd) > 0 else 0

        # max_dd = 0.05 → 0.9, max_dd = 0.20 → 0.5, max_dd = 0.40 → 0.1
        return max(0.0, min(1.0, 1.0 / (1.0 + math.exp((max_dd * 100 - 15) / 8))))

    # ═══════════════════════════════════════════════════════════
    # 角色特征提取（7角色共用特征）
    # ═══════════════════════════════════════════════════════════

    def _extract_features(self, code: str) -> Dict[str, float]:
        """提取所有特征供角色评分使用"""
        if code in self._features_cache:
            return self._features_cache[code]

        features = {
            'relative_strength': self._compute_relative_strength(code),
            'recognition': self._compute_recognition(code),
            'trend_stability': self._compute_trend_stability(code),
            'historical_leader_prob': self._compute_leader_prob(code),
            'momentum_acceleration': self._compute_momentum_acceleration(code),
            'new_high_score': self._compute_new_high_score(code),
            'volume_surge': self._compute_volume_surge(code),
            'alpha_improvement': self._compute_alpha_improvement(code),
            'alpha_score': self._compute_alpha_score(code),
            'beta_score': self._compute_beta_score(code),
            'money_flow': self._compute_money_flow(code),
            'drawdown': self._compute_drawdown(code),
        }
        self._features_cache[code] = features
        return features

    # ═══════════════════════════════════════════════════════════
    # 8角色独立评分函数
    # ═══════════════════════════════════════════════════════════

    def _score_emotion_leader(self, f: Dict) -> float:
        """情绪龙: 涨停/连板驱动 + 放量加速 + 换手辨识度 + Alpha领先
        短线情绪核心，带动性最强（游资/敢死队标的）
        """
        # Alpha一致性约束：α<0.50 不应成为情绪龙
        alpha_floor_penalty = max(0.0, (0.50 - f['alpha_score']) * 1.2 + 0.06) if f['alpha_score'] < 0.50 else 0.0
        return (
            f['historical_leader_prob'] * 0.25 +   # 涨停/连板概率（情绪龙核心）
            f['momentum_acceleration'] * 0.20 +     # 短线加速度
            f['volume_surge'] * 0.15 +              # 放量
            f['alpha_score'] * 0.20 +
            f['recognition'] * 0.20                 # 换手/成交辨识度
        ) - alpha_floor_penalty

    def _score_momentum_leader(self, f: Dict) -> float:
        """趋势龙: 相对强度领先 + 新高突破 + 趋势稳定 + Alpha持续
        中线趋势核心，决定板块趋势方向（机构趋势资金标的）
        """
        alpha_floor_penalty = max(0.0, (0.50 - f['alpha_score']) * 1.2 + 0.06) if f['alpha_score'] < 0.50 else 0.0
        return (
            f['relative_strength'] * 0.25 +         # 趋势强度（趋势龙核心）
            f['new_high_score'] * 0.20 +            # 新高突破
            f['alpha_score'] * 0.20 +
            f['trend_stability'] * 0.20 +           # 趋势稳定（拒绝暴涨暴跌）
            f['money_flow'] * 0.15
        ) - alpha_floor_penalty

    def _score_institution_core(self, f: Dict) -> float:
        """机构中军: 成交额主导 + 趋势稳定 + 低回撤 + 资金流入 + Alpha适中
        机构核心持仓，市值大、辨识度高，情绪退潮时的板块压舱石
        """
        return (
            f['recognition'] * 0.25 +               # 成交额/市值主导（机构中军核心）
            f['trend_stability'] * 0.20 +
            (1 - f['drawdown']) * 0.20 +            # 回撤小
            f['money_flow'] * 0.15 +
            f['alpha_score'] * 0.20
        )

    def _score_momentum(self, f: Dict) -> float:
        """Momentum: 加速度最高 + 新高突破 + 放量 + Alpha快速提升"""
        return (
            f['momentum_acceleration'] * 0.30 +
            f['new_high_score'] * 0.25 +
            f['volume_surge'] * 0.20 +
            f['alpha_improvement'] * 0.25
        )

    def _score_beta(self, f: Dict) -> float:
        """Beta: 创业板/科创板 + 高波动（高Alpha股应被扣分、不能Beta）"""
        # 高Alpha惩罚：alpha > 0.50 时按线性扣减，最高扣0.50
        alpha_penalty = max(0.0, (f['alpha_score'] - 0.50) / 0.50 * 0.50) if f['alpha_score'] > 0.50 else 0.0
        return max(0.0, f['beta_score'] - alpha_penalty)

    def _score_follower(self, f: Dict) -> float:
        """Follower: 涨幅落后 + Alpha改善 + 资金流入改善"""
        low_strength = 1.0 - f['relative_strength']
        return (
            low_strength * 0.30 +
            f['alpha_improvement'] * 0.35 +
            f['money_flow'] * 0.35
        )

    def _score_defensive(self, f: Dict) -> float:
        """Defensive: 回撤小 + 波动低 + 抗跌（高Alpha股应被扣分、不能Defensive）"""
        # 高Alpha惩罚：alpha > 0.50 时按线性扣减，最高扣0.60
        alpha_penalty = max(0.0, (f['alpha_score'] - 0.50) / 0.50 * 0.60) if f['alpha_score'] > 0.50 else 0.0
        low_vol = 1.0 - f['beta_score'] * 0.5  # 低Beta=低波动
        base = (
            (1 - f['drawdown']) * 0.40 +
            low_vol * 0.30 +
            f['trend_stability'] * 0.30
        )
        return base - alpha_penalty

    def _score_weak(self, f: Dict) -> float:
        """Weak: Alpha最低 + 趋势下降 + 资金流出"""
        low_strength = 1.0 - f['relative_strength']
        low_alpha = 1.0 - f['alpha_improvement']
        outflow = 1.0 - f['money_flow']
        return (
            low_strength * 0.35 +
            low_alpha * 0.30 +
            outflow * 0.35
        )

    # ═══════════════════════════════════════════════════════════
    # 综合评分 + 角色分配
    # ═══════════════════════════════════════════════════════════

    def _get_evolution_weights(self) -> Dict[str, float]:
        """获取当前生命周期的演化权重"""
        stage = self.subtheme_stage
        # 映射Heat Engine的stage到演化权重
        stage_map = {
            '潜伏': '萌芽', '升温': '成长', '主升': '主升',
            '分歧': '分歧', '退潮': '退潮', '弱势': '弱势',
        }
        mapped = stage_map.get(stage, stage)
        return LIFECYCLE_EVOLUTION.get(mapped, DEFAULT_EVOLUTION)

    def assign_role(self, code: str) -> Dict[str, Any]:
        """为单只股票计算角色"""
        features = self._extract_features(code)
        if not features:
            return {
                'role': 'Weak', 'role_score': 0.0, 'role_reason': '数据不足',
                'leader_similarity': 0.0, 'confidence': 0.0,
                'role_features': {},
            }

        # 8角色原始评分
        raw_scores = {
            'Emotion Leader': self._score_emotion_leader(features),
            'Momentum Leader': self._score_momentum_leader(features),
            'Institution Core': self._score_institution_core(features),
            'Momentum': self._score_momentum(features),
            'Beta': self._score_beta(features),
            'Follower': self._score_follower(features),
            'Defensive': self._score_defensive(features),
            'Weak': self._score_weak(features),
        }

        # 应用演化权重
        evo = self._get_evolution_weights()
        evolved = {}
        for role, score in raw_scores.items():
            evolved[role] = score * evo.get(role, 1.0)

        # 取最高分角色
        best_role = max(evolved, key=evolved.get)
        best_score = evolved[best_role]

        # 归一化：竞争压制
        # 如果第二名差距小(<0.1)，降低置信度
        sorted_scores = sorted(evolved.values(), reverse=True)
        margin = sorted_scores[0] - sorted_scores[1] if len(sorted_scores) > 1 else 1.0
        confidence = min(1.0, max(0.0, 0.5 + margin * 2.0))

        # Leader Similarity：与Leader角色的特征相似度
        leader_features = {
            'relative_strength': 1.0, 'recognition': 1.0,
            'trend_stability': 0.8, 'historical_leader_prob': 1.0,
            'momentum_acceleration': 0.6, 'volume_surge': 0.5,
        }
        sim_numerator = sum(
            min(features.get(k, 0), v) for k, v in leader_features.items()
        )
        sim_denominator = sum(leader_features.values())
        leader_similarity = sim_numerator / max(sim_denominator, 1)

        # 输出特征
        role_features = {k: round(v, 3) for k, v in features.items()}

        return {
            'role': best_role,
            'role_class': ROLE_CLASS_MAP.get(best_role, best_role),
            'role_score': round(best_score, 4),
            'role_reason': ROLE_DESCRIPTIONS.get(best_role, ''),
            'leader_similarity': round(leader_similarity, 3),
            'confidence': round(confidence, 3),
            'role_features': role_features,
            'all_role_scores': {k: round(v, 4) for k, v in evolved.items()},
        }

    # ═══════════════════════════════════════════════════════════
    # 批量运行
    # ═══════════════════════════════════════════════════════════

    def run_all(self) -> Dict[str, Dict[str, Any]]:
        """为子主题内所有股票分配角色"""
        results = {}
        for s in self.subtheme_stocks:
            code = s['code']
            results[code] = self.assign_role(code)
        return results

    # ═══════════════════════════════════════════════════════════
    # 统计摘要
    # ═══════════════════════════════════════════════════════════

    def print_summary(self, results: Dict[str, Dict[str, Any]]):
        """打印角色分布摘要"""
        from collections import Counter
        role_counts = Counter()
        for r in results.values():
            role_counts[r['role']] += 1

        print(f"\n  [RoleEngine] 子主题阶段: {self.subtheme_stage}")
        print(f"  [RoleEngine] 角色分布:")
        for role in ROLES:
            count = role_counts.get(role, 0)
            pct = count / max(len(results), 1) * 100
            print(f"    {role:<12}: {count:3d}只 ({pct:5.1f}%)")

        # 输出三龙三核心摘要
        for role, label in [('Emotion Leader', '情绪龙'),
                            ('Momentum Leader', '趋势龙'),
                            ('Institution Core', '机构中军')]:
            hits = [(c, r) for c, r in results.items() if r['role'] == role]
            if hits:
                top = max(hits, key=lambda x: x[1]['role_score'])
                name = self._code_map.get(top[0], {}).get('name', top[0])
                print(f"  [RoleEngine] {label}: {name}({top[0]}) score={top[1]['role_score']:.3f}")


# ═══════════════════════════════════════════════════════════
# 便利函数：批量运行所有子主题
# ═══════════════════════════════════════════════════════════

def run_stock_role_engine_for_all_subthemes(
    subtheme_stock_index: Dict[str, Dict[str, List[Dict]]],
    kline_groups: Dict[str, pd.DataFrame],
    subtheme_lifecycle: Dict[str, Dict[str, str]],
    stock_basic_info: Dict = None,
    daily_basic_info: Dict = None,
) -> Dict[str, Dict[str, Dict]]:
    """
    为所有子主题运行Role Evolution Engine
    
    输入:
      subtheme_stock_index: {母主题: {子主题: [{code, name, ...}]}}
      kline_groups: {code: kline_df}
      subtheme_lifecycle: {母主题: {子主题: {stage, ...}}}
    
    输出:
      {母主题: {子主题: {code: {role, role_score, ...}}}}
    """
    print("  [RoleEngine] 为所有子主题运行 Role Evolution Engine...")

    all_results = {}
    total_stocks = 0

    for parent_theme, sub_dict in subtheme_stock_index.items():
        all_results[parent_theme] = {}
        for sub_name, stocks in sub_dict.items():
            # 获取子主题生命周期
            stage = '潜伏'
            if parent_theme in subtheme_lifecycle:
                st_info = subtheme_lifecycle[parent_theme].get(sub_name, {})
                if isinstance(st_info, dict):
                    stage = st_info.get('stage', '潜伏')

            if not stocks:
                continue

            engine = StockRoleEngine(
                subtheme_stocks=stocks,
                kline_groups=kline_groups,
                subtheme_stage=stage,
                stock_basic_info=stock_basic_info,
                daily_basic_info=daily_basic_info,
            )
            results = engine.run_all()
            all_results[parent_theme][sub_name] = results
            total_stocks += len(results)

    print(f"  [RoleEngine] 完成: {total_stocks} 只股票角色分配")
    return all_results


def build_subtheme_stock_index_from_report(
    stock_subtheme_map: Dict[str, Dict],
    stocks_output: Dict[str, Dict],
    subtheme_report: Dict[str, Dict],
) -> Dict[str, Dict[str, List[Dict]]]:
    """
    从已有数据构建 subtheme_stock_index
    
    输入:
      stock_subtheme_map: {code: {subtheme, features}}
      stocks_output: {code: {name, industry, concepts, themes, ...}}
      subtheme_report: {subtheme_matrix: {母主题: [{name, stage, ...}]}}
    
    输出: {母主题: {子主题: [{code, name, industry, concepts}, ...]}}
    """
    idx = defaultdict(lambda: defaultdict(list))

    for code, info in stocks_output.items():
        st = info.get('subtheme', '')
        if not st:
            continue
        themes = info.get('themes', [])
        parent = themes[0] if themes else ''

        # 检查子主题是否在 subtheme_report 中
        if subtheme_report:
            matrix = subtheme_report.get('subtheme_matrix', {})
            if parent in matrix:
                valid_subthemes = {s['name'] for s in matrix[parent]}
                if st not in valid_subthemes:
                    continue

        if parent and st:
            idx[parent][st].append({
                'code': code,
                'name': info.get('name', ''),
                'industry': info.get('industry', ''),
                'concepts': info.get('concepts', []),
            })

    return {k: dict(v) for k, v in idx.items()}


def build_subtheme_lifecycle_from_report(subtheme_report: Dict) -> Dict[str, Dict[str, str]]:
    """从 subtheme_report 提取子主题阶段"""
    lifecycle = {}
    matrix = subtheme_report.get('subtheme_matrix', {})
    for parent, subs in matrix.items():
        lifecycle[parent] = {}
        for s in subs:
            lifecycle[parent][s['name']] = {
                'stage': s.get('stage', '潜伏'),
                'score': s.get('score', 0),
            }
    return lifecycle


# ═══════════════════════════════════════════════════════════
# 独立调试入口
# ═══════════════════════════════════════════════════════════

if __name__ == '__main__':
    print("Stock Role Evolution Engine - 独立调试")
    print("=" * 60)

    # 从缓存加载数据
    cache_dir = os.path.join(BASE_DIR, "cache_daily")
    json_file = os.path.join(cache_dir, "theme_stock_map_v2_20260724.json")

    if not os.path.exists(json_file):
        print(f"[错误] 未找到数据文件: {json_file}")
        sys.exit(1)

    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    stocks_output = data.get('stocks', {})
    stock_subtheme_map = data.get('subtheme_dynamic_correlation', {})
    subtheme_report = data.get('subtheme_report', {})

    # 构建索引
    stock_index = build_subtheme_stock_index_from_report(
        stock_subtheme_map, stocks_output, subtheme_report
    )
    lifecycle = build_subtheme_lifecycle_from_report(subtheme_report)

    # 加载K线数据（以第一个子主题为例）
    first_parent = list(stock_index.keys())[0] if stock_index else ''
    first_sub = ''
    if first_parent:
        first_sub = list(stock_index[first_parent].keys())[0]
        print(f"\n调试子主题: {first_parent} → {first_sub}")

        stocks = stock_index[first_parent][first_sub]
        stage = lifecycle.get(first_parent, {}).get(first_sub, {}).get('stage', '潜伏')
        print(f"  成分股: {len(stocks)} 只, 阶段: {stage}")

        # 获取K线
        from datetime import datetime, timedelta
        trade_date = data.get('trade_date', '20260724')
        start = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=90)).strftime("%Y%m%d")

        codes = [s['code'] for s in stocks]
        print(f"  加载K线: {len(codes)} 只股票...")
        kline_df = theme_ts.get_daily_kline(codes, start, trade_date)

        kline_groups = {}
        if kline_df is not None and not kline_df.empty:
            for code, sub in kline_df.groupby("ts_code"):
                kline_groups[code] = sub.sort_values("trade_date")

        # 运行角色引擎
        engine = StockRoleEngine(stocks, kline_groups, stage)
        results = engine.run_all()
        engine.print_summary(results)

        # 输出Leader和Momentum的详细信息
        print("\n  Leader和Momentum详情:")
        for code, r in sorted(results.items(), key=lambda x: -x[1]['role_score'])[:10]:
            name = stocks[0].get('name', code)  # 用第一个stock的name
            # 查找对应的name
            for s in stocks:
                if s['code'] == code:
                    name = s['name']
                    break
            print(f"    {name:<8}({code}) role={r['role']:<10} score={r['role_score']:.3f} conf={r['confidence']:.2f} {r['role_reason'][:20]}")
