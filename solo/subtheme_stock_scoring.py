#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Stock Alpha Engine V4.2 - Sub-theme Stock Scoring
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
根据 Role Engine 输出，完成 Sub-theme 内部股票评分。

评分对象：仅限当前 Sub-theme，禁止跨 Sub-theme 排名。

Stock Alpha Score (0~100):
  Relative Strength (15%) + Trend (15%) + Acceleration (10%) +
  Money Flow (10%) + Volume (8%) + Chip Distribution (8%) +
  Risk (8%) + Fundamental (8%) + Leader Similarity (5%) +
  Liquidity (5%) + Market Cap (4%) + Volatility (4%)

Final Score = Stock Alpha × 45% + Role Score × 25% +
              Sub-theme Score × 15% + Theme Score × 10% + Market Score × 5%

每 Sub-theme 自动输出：
  Leader: 1只, Core: 2只, Momentum: 2只, Beta: 2只, Follower: 2只

输出字段：
  code, name, theme, subtheme, role, stock_alpha, final_score,
  leader_similarity, signal, confidence

完全独立模块，不修改任何现有接口。
"""

import sys
import os
import math
import json
from collections import defaultdict, Counter
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(BASE_DIR)
sys.path.append(parent_dir)
sys.path.append(BASE_DIR)

import theme_trend_sentiment_score as theme_ts

# ── Stock Alpha 因子权重 ──
STOCK_ALPHA_WEIGHTS = {
    'relative_strength': 0.15,
    'trend': 0.15,
    'acceleration': 0.10,
    'money_flow': 0.10,
    'volume': 0.08,
    'chip_distribution': 0.08,
    'risk': 0.08,
    'fundamental': 0.08,
    'leader_similarity': 0.05,
    'liquidity': 0.05,
    'market_cap': 0.04,
    'volatility': 0.04,
}

# ── Top Picks 每角色数量配置 ──
TOP_PICKS_CONFIG = {
    'Leader': 1,
    'Core': 2,
    'Momentum': 2,
    'Beta': 2,
    'Follower': 2,
}

# ── 输出角色（Defensive/Weak 不输出 Top Picks） ──
OUTPUT_ROLES = ['Leader', 'Core', 'Momentum', 'Beta', 'Follower']


class SubthemeStockScorer:
    """
    Sub-theme 内部股票评分引擎

    输入:
      subtheme_name: str, 子主题名称
      stocks: List[Dict], 子主题内股票列表 [{code, name, industry, concepts}]
      kline_groups: Dict[str, DataFrame], {code: kline_df}
      role_results: Dict[str, Dict], {code: {role, role_score, leader_similarity, ...}}
      subtheme_score: float, 子主题综合分 (0~100, 来自 Heat Engine)
      theme_score: float, 母主题分 (0~100)
      market_score: float, 市场分 (0~100)
      daily_basic: Dict[str, Dict], {code: {total_mv, pe, turnover_rate, ...}}
    """

    def __init__(self, subtheme_name: str, stocks: List[Dict],
                 kline_groups: Dict[str, pd.DataFrame],
                 role_results: Dict[str, Dict],
                 subtheme_score: float = 50.0,
                 theme_score: float = 50.0,
                 market_score: float = 50.0,
                 daily_basic: Dict = None):
        self.subtheme_name = subtheme_name
        self.stocks = stocks
        self.kline_groups = kline_groups
        self.role_results = role_results
        self.subtheme_score = subtheme_score
        self.theme_score = theme_score
        self.market_score = market_score
        self.daily_basic = daily_basic or {}

        self._codes = [s['code'] for s in stocks]
        self._code_map = {s['code']: s for s in stocks}

    # ═══════════════════════════════════════════════════════════
    # 12维因子计算（均在子主题内归一化 0~100）
    # ═══════════════════════════════════════════════════════════

    def _safe_kline(self, code: str) -> Optional[pd.DataFrame]:
        return self.kline_groups.get(code)

    def _rank_in_subtheme(self, values: Dict[str, float]) -> Dict[str, float]:
        """在子主题内将值转换为百分位排名 (0~100)"""
        codes = list(values.keys())
        vals = np.array([values.get(c, 0) for c in codes])
        if len(vals) == 0:
            return {c: 50.0 for c in codes}
        # 百分位排名
        ranks = pd.Series(vals).rank(pct=True) * 100
        return {codes[i]: float(ranks.iloc[i]) for i in range(len(codes))}

    def _apply_sample_penalty(self, scores: Dict[str, float]) -> Dict[str, float]:
        """样本量惩罚：子主题股票过少时对得分打折

        公式: penalty = min(n_stocks / 20, 1.0)
        少于20只线性打折，20只以上全额。
        例: 8只股票 → penalty=0.4 → alpha缩水60%向50靠拢
        """
        n = len(self._codes)
        if n >= 20:
            return scores
        penalty = n / 20.0
        return {c: round(s * penalty + 50 * (1 - penalty), 2)
                for c, s in scores.items()}

    def _calc_relative_strength(self) -> Dict[str, float]:
        """相对强度：近5日/10日/20日收益率加权（子主题内排名0~100）"""
        scores = {}
        for code in self._codes:
            kdf = self._safe_kline(code)
            if kdf is None or len(kdf) < 5:
                scores[code] = 0
                continue
            closes = kdf['close'].astype(float).values
            ret_5 = (closes[-1] / closes[-5] - 1) * 100 if len(closes) >= 5 else 0
            ret_10 = (closes[-1] / closes[-10] - 1) * 100 if len(closes) >= 10 else 0
            ret_20 = (closes[-1] / closes[-20] - 1) * 100 if len(closes) >= 20 else 0
            scores[code] = ret_5 * 0.5 + ret_10 * 0.3 + ret_20 * 0.2
        return self._rank_in_subtheme(scores)

    def _calc_trend(self) -> Dict[str, float]:
        """趋势：MA5/MA10/MA20 多头排列程度"""
        scores = {}
        for code in self._codes:
            kdf = self._safe_kline(code)
            if kdf is None or len(kdf) < 20:
                scores[code] = 0
                continue
            closes = kdf['close'].astype(float).values[-20:]
            ma5 = np.mean(closes[-5:])
            ma10 = np.mean(closes[-10:]) if len(closes) >= 10 else ma5
            ma20 = np.mean(closes) if len(closes) >= 20 else ma10

            # 多头排列：MA5 > MA10 > MA20
            if ma5 > ma10 > ma20:
                score = 100
            elif ma5 > ma10 and ma10 <= ma20:
                score = 60  # 短期强，中期弱
            elif ma5 > ma20 > ma10:
                score = 50  # 短期突破中期均线
            elif ma5 <= ma10 and ma10 > ma20:
                score = 40  # 短期回调
            else:
                score = 20  # 空头排列

            # 均线斜率加分（MA5方向）
            if len(closes) >= 10:
                ma5_prev = np.mean(closes[-10:-5])
                slope = (ma5 - ma5_prev) / max(ma5_prev, 0.01) * 100
                slope_bonus = max(0, min(20, slope))
                score = min(100, score + slope_bonus)

            scores[code] = score
        return self._rank_in_subtheme(scores)

    def _calc_acceleration(self) -> Dict[str, float]:
        """加速度：近3日收益 - 近10日收益，加速越快分越高"""
        scores = {}
        for code in self._codes:
            kdf = self._safe_kline(code)
            if kdf is None or len(kdf) < 11:
                scores[code] = 0
                continue
            closes = kdf['close'].astype(float).values
            ret_3 = (closes[-1] / closes[-4] - 1) * 100
            ret_10 = (closes[-1] / closes[-11] - 1) * 100
            accel = ret_3 - ret_10  # 正值 = 加速
            scores[code] = accel
        return self._rank_in_subtheme(scores)

    def _calc_money_flow(self) -> Dict[str, float]:
        """资金流向：量比 + 涨跌幅方向"""
        scores = {}
        for code in self._codes:
            kdf = self._safe_kline(code)
            if kdf is None or len(kdf) < 20:
                scores[code] = 0
                continue
            vol = kdf['vol'].astype(float).values
            vol_3 = np.mean(vol[-3:])
            vol_20 = np.mean(vol[-20:])
            vol_ratio = vol_3 / max(vol_20, 1)

            if 'pct_chg' in kdf.columns:
                ret_3 = np.mean(kdf['pct_chg'].astype(float).values[-3:])
            else:
                closes = kdf['close'].astype(float).values
                ret_3 = (closes[-1] / closes[-4] - 1) * 100

            # 放量上涨 = 强流入，缩量下跌 = 流出
            if vol_ratio > 1.3 and ret_3 > 1:
                flow = 100
            elif vol_ratio > 1.1 and ret_3 > 0:
                flow = 70
            elif vol_ratio < 0.8 and ret_3 < -1:
                flow = 10
            elif vol_ratio < 0.9 and ret_3 < 0:
                flow = 30
            else:
                flow = 50
            scores[code] = flow
        return self._rank_in_subtheme(scores)

    def _calc_volume(self) -> Dict[str, float]:
        """量能：近3日/20日均量比，量能活跃度"""
        scores = {}
        for code in self._codes:
            kdf = self._safe_kline(code)
            if kdf is None or len(kdf) < 20:
                scores[code] = 0
                continue
            vol = kdf['vol'].astype(float).values
            vol_3 = np.mean(vol[-3:])
            vol_20 = np.mean(vol[-20:])
            ratio = vol_3 / max(vol_20, 1)

            # ratio: 0.5→10, 1.0→50, 1.5→75, 2.0→90, 3.0→100
            vol_score = max(0, min(100, (ratio - 0.5) / 2.5 * 100))
            scores[code] = vol_score
        return self._rank_in_subtheme(scores)

    def _calc_chip_distribution(self) -> Dict[str, float]:
        """筹码分布代理：价格位置（当前价/区间振幅中位）
        
        用近20日价格位置判断筹码集中度。
        价格在区间中部 = 筹码集中, 在顶部 = 获利盘多, 在底部 = 套牢
        """
        scores = {}
        for code in self._codes:
            kdf = self._safe_kline(code)
            if kdf is None or len(kdf) < 10:
                scores[code] = 50
                continue
            recent = kdf.tail(20)
            closes = recent['close'].astype(float).values
            highs = recent['high'].astype(float).values if 'high' in recent.columns else closes
            lows = recent['low'].astype(float).values if 'low' in recent.columns else closes

            latest = closes[-1]
            high_20 = np.max(highs)
            low_20 = np.min(lows)
            mid_20 = (high_20 + low_20) / 2

            if high_20 == low_20:
                scores[code] = 50
                continue

            # 位置百分比：0=底部, 100=顶部
            position = (latest - low_20) / (high_20 - low_20) * 100

            # 中部40-60% = 最佳（筹码集中）
            # 顶部80-100% = 获利盘多（风险）
            # 底部0-20% = 套牢盘重
            if 40 <= position <= 60:
                chip_score = 100
            elif 30 <= position < 40 or 60 < position <= 70:
                chip_score = 80
            elif 20 <= position < 30 or 70 < position <= 80:
                chip_score = 60
            elif 10 <= position < 20 or 80 < position <= 90:
                chip_score = 30
            else:
                chip_score = 10

            scores[code] = chip_score
        return self._rank_in_subtheme(scores)

    def _calc_risk(self) -> Dict[str, float]:
        """风险：最大回撤的逆（回撤越小分越高）"""
        scores = {}
        for code in self._codes:
            kdf = self._safe_kline(code)
            if kdf is None or len(kdf) < 10:
                scores[code] = 50
                continue
            closes = kdf['close'].astype(float).values[-20:]
            if len(closes) < 5:
                scores[code] = 50
                continue
            peak = np.maximum.accumulate(closes)
            dd = (peak - closes) / peak
            max_dd = np.max(dd)

            # max_dd: 0.02→95, 0.05→80, 0.10→60, 0.20→30, 0.30→10
            risk_score = max(0, min(100, 100 - max_dd * 300))
            scores[code] = risk_score
        return self._rank_in_subtheme(scores)

    def _calc_fundamental(self) -> Dict[str, float]:
        """基本面：使用 daily_basic 中的 PE、PB 等（如有），否则使用市值代理"""
        scores = {}
        for code in self._codes:
            db = self.daily_basic.get(code, {})
            if not db:
                scores[code] = 50
                continue

            # PE: 正常范围(10-50)得分高
            pe = db.get('pe', db.get('pe_ttm', 0))
            if pe and 0 < pe < 200:
                if 15 <= pe <= 40:
                    pe_score = 100
                elif 10 <= pe < 15 or 40 < pe <= 60:
                    pe_score = 80
                elif 5 <= pe < 10 or 60 < pe <= 100:
                    pe_score = 50
                else:
                    pe_score = 30
            else:
                pe_score = 50

            # PB: 正常范围(1-5)得分高
            pb = db.get('pb', 0)
            if pb and 0 < pb < 20:
                if 1 <= pb <= 3:
                    pb_score = 100
                elif 0.5 <= pb < 1 or 3 < pb <= 5:
                    pb_score = 70
                else:
                    pb_score = 40
            else:
                pb_score = 50

            # 净利润增速
            yoy = db.get('yoy_profit', db.get('yoy', 0))
            if yoy:
                if yoy > 30:
                    yoy_score = 100
                elif yoy > 10:
                    yoy_score = 80
                elif yoy > 0:
                    yoy_score = 60
                elif yoy > -20:
                    yoy_score = 30
                else:
                    yoy_score = 10
            else:
                yoy_score = 50

            scores[code] = pe_score * 0.35 + pb_score * 0.25 + yoy_score * 0.40
        return self._rank_in_subtheme(scores)

    def _calc_leader_similarity(self) -> Dict[str, float]:
        """Leader Similarity：直接使用 Role Engine 的输出"""
        raw = {}
        for code in self._codes:
            rr = self.role_results.get(code, {})
            raw[code] = rr.get('leader_similarity', 0) * 100
        return self._rank_in_subtheme(raw)

    def _calc_liquidity(self) -> Dict[str, float]:
        """流动性：换手率（近3日均值在子主题内排名）"""
        scores = {}
        for code in self._codes:
            kdf = self._safe_kline(code)
            if kdf is None or len(kdf) < 3:
                scores[code] = 0
                continue
            # 用成交额 proxy 换手率
            if 'amount' in kdf.columns:
                amt_3 = np.mean(kdf['amount'].astype(float).values[-3:])
                scores[code] = amt_3
            elif 'vol' in kdf.columns:
                vol_3 = np.mean(kdf['vol'].astype(float).values[-3:])
                scores[code] = vol_3
            else:
                scores[code] = 0
        return self._rank_in_subtheme(scores)

    def _calc_market_cap(self) -> Dict[str, float]:
        """市值：中等市值得分最高（避免过小或过大）"""
        scores = {}
        for code in self._codes:
            db = self.daily_basic.get(code, {})
            mv = db.get('total_mv', db.get('circ_mv', 0))
            if mv and mv > 0:
                mv_yi = mv / 1e8  # 转换为亿元
                # 50-500亿最佳
                if 50 <= mv_yi <= 500:
                    cap_score = 100
                elif 20 <= mv_yi < 50 or 500 < mv_yi <= 1000:
                    cap_score = 80
                elif 10 <= mv_yi < 20 or 1000 < mv_yi <= 3000:
                    cap_score = 60
                elif 5 <= mv_yi < 10:
                    cap_score = 40
                else:
                    cap_score = 20
            else:
                cap_score = 50
            scores[code] = cap_score
        return self._rank_in_subtheme(scores)

    def _calc_volatility(self) -> Dict[str, float]:
        """波动率：中等波动最佳（太低=不活跃，太高=风险）"""
        scores = {}
        for code in self._codes:
            kdf = self._safe_kline(code)
            if kdf is None or len(kdf) < 10:
                scores[code] = 50
                continue

            if 'pct_chg' in kdf.columns:
                pcts = kdf['pct_chg'].astype(float).values[-20:]
            else:
                closes = kdf['close'].astype(float).values[-21:]
                pcts = np.diff(closes) / closes[:-1] * 100

            if len(pcts) < 5:
                scores[code] = 50
                continue

            vol = np.std(pcts)
            # vol: 1→40, 2→70, 3→85, 5→70, 8→40, 12→20
            if vol <= 0.5:
                v_score = 20  # 太不活跃
            elif vol <= 1.5:
                v_score = 50
            elif vol <= 3.0:
                v_score = 85  # 理想波动
            elif vol <= 5.0:
                v_score = 75
            elif vol <= 8.0:
                v_score = 50
            else:
                v_score = 20  # 太高风险
            scores[code] = v_score
        return self._rank_in_subtheme(scores)

    # ═══════════════════════════════════════════════════════════
    # Stock Alpha Score (0~100)
    # ═══════════════════════════════════════════════════════════

    def compute_stock_alpha(self) -> Dict[str, float]:
        """计算所有股票的 Stock Alpha Score (0~100)"""
        # 计算12维因子
        factor_funcs = {
            'relative_strength': self._calc_relative_strength,
            'trend': self._calc_trend,
            'acceleration': self._calc_acceleration,
            'money_flow': self._calc_money_flow,
            'volume': self._calc_volume,
            'chip_distribution': self._calc_chip_distribution,
            'risk': self._calc_risk,
            'fundamental': self._calc_fundamental,
            'leader_similarity': self._calc_leader_similarity,
            'liquidity': self._calc_liquidity,
            'market_cap': self._calc_market_cap,
            'volatility': self._calc_volatility,
        }

        factor_results = {}
        for name, func in factor_funcs.items():
            factor_results[name] = func()

        # 加权合成 + 样本量惩罚
        alphas = {}
        for code in self._codes:
            alpha = 0.0
            for name, weight in STOCK_ALPHA_WEIGHTS.items():
                factor_val = factor_results.get(name, {}).get(code, 50)
                alpha += factor_val * weight
            alphas[code] = round(min(100, max(0, alpha)), 2)

        # 样本量惩罚：子主题股票<20只时向50收缩
        alphas = self._apply_sample_penalty(alphas)
        return alphas

    # ═══════════════════════════════════════════════════════════
    # Final Score (0~100)
    # ═══════════════════════════════════════════════════════════

    def compute_final_scores(self, stock_alphas: Dict[str, float]) -> Dict[str, float]:
        """计算 Final Score

        Final Score = Stock Alpha × 45% + Role Score × 25% +
                      Sub-theme Score × 15% + Theme Score × 10% + Market Score × 5%
        """
        scores = {}
        for code in self._codes:
            stock_alpha = stock_alphas.get(code, 0)

            # Role Score (0~100)：从 role_results 中取角色分
            rr = self.role_results.get(code, {})
            role_score_raw = rr.get('role_score', 0)
            role_score = min(100, role_score_raw * 20)  # 角色原始分 ~0-5, 映射到0-100

            final = (
                stock_alpha * 0.45 +
                role_score * 0.25 +
                self.subtheme_score * 0.15 +
                self.theme_score * 0.10 +
                self.market_score * 0.05
            )
            scores[code] = round(min(100, max(0, final)), 2)

        return scores

    # ═══════════════════════════════════════════════════════════
    # Top Picks 生成
    # ═══════════════════════════════════════════════════════════

    def generate_top_picks(self, stock_alphas: Dict[str, float] = None,
                           final_scores: Dict[str, float] = None) -> Dict[str, List[Dict]]:
        """
        按角色生成 Top Picks

        输出:
          {role: [{code, name, theme, subtheme, role, stock_alpha, final_score,
                   leader_similarity, signal, confidence}, ...]}
        """
        if stock_alphas is None:
            stock_alphas = self.compute_stock_alpha()
        if final_scores is None:
            final_scores = self.compute_final_scores(stock_alphas)

        # 按角色分组
        role_groups = defaultdict(list)
        for code in self._codes:
            rr = self.role_results.get(code, {})
            role = rr.get('role', 'Weak')

            entry = {
                'code': code,
                'name': self._code_map.get(code, {}).get('name', code),
                'theme': '',  # 由调用方填充
                'subtheme': self.subtheme_name,
                'role': role,
                'stock_alpha': stock_alphas.get(code, 0),
                'final_score': final_scores.get(code, 0),
                'leader_similarity': rr.get('leader_similarity', 0),
                'signal': 'buy',
                'confidence': rr.get('confidence', 0),
            }
            role_groups[role].append(entry)

        # 对每个输出角色按 Final Score 排序取 Top N
        top_picks = {}
        for role in OUTPUT_ROLES:
            candidates = role_groups.get(role, [])
            candidates.sort(key=lambda x: -x['final_score'])
            n = TOP_PICKS_CONFIG.get(role, 1)
            selected = candidates[:n]

            # 为每个 pick 生成 signal
            for pick in selected:
                if pick['final_score'] >= 80:
                    pick['signal'] = 'strong_buy'
                elif pick['final_score'] >= 60:
                    pick['signal'] = 'buy'
                elif pick['final_score'] >= 40:
                    pick['signal'] = 'watch'
                else:
                    pick['signal'] = 'pass'

            top_picks[role] = selected

        return top_picks

    # ═══════════════════════════════════════════════════════════
    # 批量运行（全量输出）
    # ═══════════════════════════════════════════════════════════

    def run_all(self) -> Dict[str, Any]:
        """运行完整评分流水线，返回全量数据"""
        stock_alphas = self.compute_stock_alpha()
        final_scores = self.compute_final_scores(stock_alphas)
        top_picks = self.generate_top_picks(stock_alphas, final_scores)

        # 全量结果
        all_results = {}
        for code in self._codes:
            rr = self.role_results.get(code, {})
            all_results[code] = {
                'code': code,
                'name': self._code_map.get(code, {}).get('name', code),
                'subtheme': self.subtheme_name,
                'role': rr.get('role', ''),
                'stock_alpha': stock_alphas.get(code, 0),
                'final_score': final_scores.get(code, 0),
                'leader_similarity': rr.get('leader_similarity', 0),
                'confidence': rr.get('confidence', 0),
                'role_score': rr.get('role_score', 0),
                'role_features': rr.get('role_features', {}),
            }

        return {
            'subtheme': self.subtheme_name,
            'n_stocks': len(self.stocks),
            'stock_alphas': stock_alphas,
            'final_scores': final_scores,
            'all_results': all_results,
            'top_picks': top_picks,
        }


# ═══════════════════════════════════════════════════════════
# 便利函数：为所有子主题运行
# ═══════════════════════════════════════════════════════════

def _normalize_score_to_100(raw_score, max_raw=100):
    """将原始分归一化到0~100"""
    if max_raw <= 0:
        return 50.0
    return round(min(100, max(0, raw_score / max_raw * 100)), 1)


def run_subtheme_stock_scoring_for_all(
    stocks_output: Dict[str, Dict],
    stock_subtheme_map: Dict[str, Dict],
    subtheme_report: Dict,
    role_results: Dict[str, Dict],
    kline_groups: Dict[str, pd.DataFrame],
    daily_basic: Dict = None,
) -> Dict[str, Dict]:
    """
    为所有子主题运行 Stock Scoring Engine

    输入:
      stocks_output: {code: {name, industry, themes, concepts, subtheme, ...}}
      stock_subtheme_map: {code: {subtheme, subtheme_confidence, ...}}
      subtheme_report: {subtheme_matrix: {母主题: [{name, score, stage, ...}]}}
      role_results: {code: {role, role_score, leader_similarity, ...}}
      kline_groups: {code: kline_df}
      daily_basic: {code: {total_mv, pe, ...}}

    输出:
      {母主题: {子主题: full_results_dict}}
    """
    # 构建 {母主题: {子主题: [{code, name, ...}]}} 索引
    subtheme_stock_index = defaultdict(lambda: defaultdict(list))
    for code, info in stocks_output.items():
        st = info.get('subtheme', '')
        if not st:
            continue
        themes = info.get('themes', [])
        parent = themes[0] if themes else ''
        if parent and st:
            subtheme_stock_index[parent][st].append({
                'code': code,
                'name': info.get('name', ''),
                'industry': info.get('industry', ''),
                'concepts': info.get('concepts', []),
            })

    # 构建 {母主题: {子主题: score}} 索引
    matrix = subtheme_report.get('subtheme_matrix', {})
    subtheme_scores = {}  # {母主题: {子主题名: score}}
    parent_scores = {}    # {母主题: avg_score}
    for parent, subs in matrix.items():
        subtheme_scores[parent] = {}
        total_score = 0
        for s in subs:
            name = s.get('name', '')
            score = s.get('score', 50)
            subtheme_scores[parent][name] = score
            total_score += score
        parent_scores[parent] = total_score / max(len(subs), 1)

    # 市场分（用所有股票平均 Final Score 的代理）
    market_score = 50.0

    all_results = {}
    total_stocks = 0

    for parent, subs in subtheme_stock_index.items():
        all_results[parent] = {}
        parent_score = parent_scores.get(parent, 50.0)

        for sub_name, stocks in subs.items():
            if not stocks:
                continue

            sub_score = subtheme_scores.get(parent, {}).get(sub_name, 50.0)

            scorer = SubthemeStockScorer(
                subtheme_name=sub_name,
                stocks=stocks,
                kline_groups=kline_groups,
                role_results=role_results,
                subtheme_score=sub_score,
                theme_score=parent_score,
                market_score=market_score,
                daily_basic=daily_basic,
            )
            result = scorer.run_all()
            all_results[parent][sub_name] = result
            total_stocks += len(stocks)

    print(f"  [StockScoring] 完成: {total_stocks} 只股票评分")
    return all_results


# ═══════════════════════════════════════════════════════════
# 展平 Top Picks 为统一输出格式
# ═══════════════════════════════════════════════════════════

def flatten_top_picks(all_scoring_results: Dict[str, Dict]) -> List[Dict]:
    """
    展平所有子主题的 Top Picks 为列表

    输出: [{code, name, theme, subtheme, role, stock_alpha, final_score,
            leader_similarity, signal, confidence}]
    """
    flat = []
    for parent, subs in all_scoring_results.items():
        for sub_name, result in subs.items():
            top_picks = result.get('top_picks', {})
            for role, picks in top_picks.items():
                for pick in picks:
                    pick['theme'] = parent
                    flat.append(pick)
    return flat


def print_top_picks_summary(flat_picks: List[Dict], top_n: int = 20):
    """打印 Top Picks 摘要"""
    sorted_picks = sorted(flat_picks, key=lambda x: -x['final_score'])
    print(f"\n  [StockScoring] Top Picks 总数: {len(flat_picks)}")
    print(f"  {'代码':<12} {'名称':<8} {'主题':<12} {'子主题':<12} {'角色':<10} {'Alpha':<8} {'Final':<8} {'信号':<10}")
    print(f"  {'─'*80}")
    for pick in sorted_picks[:top_n]:
        print(f"  {pick['code']:<12} {pick['name']:<8} {pick['theme']:<12} "
              f"{pick['subtheme']:<10} {pick['role']:<10} {pick['stock_alpha']:<8} "
              f"{pick['final_score']:<8} {pick['signal']:<10}")
    print(f"  {'─'*80}")


if __name__ == '__main__':
    print("Stock Alpha Engine V4.2 - Sub-theme Stock Scoring")
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
    role_results = data.get('role_evolution', {})
    subtheme_report = data.get('subtheme_report', {})

    # 加载K线
    trade_date = data.get('trade_date', '20260724')
    dt = datetime.strptime(str(trade_date), "%Y%m%d")
    start = (dt - timedelta(days=90)).strftime("%Y%m%d")
    end = str(trade_date)

    codes = list(role_results.keys())
    print(f"加载 {len(codes)} 只股票K线...")
    kline_df = theme_ts.get_daily_kline(codes, start, end)

    kline_groups = {}
    if kline_df is not None and not kline_df.empty:
        for code, sub in kline_df.groupby("ts_code"):
            kline_groups[code] = sub.sort_values("trade_date")
    print(f"K线加载完成: {len(kline_groups)} 只")

    results = run_subtheme_stock_scoring_for_all(
        stocks_output, stock_subtheme_map, subtheme_report,
        role_results, kline_groups
    )

    flat = flatten_top_picks(results)
    print_top_picks_summary(flat)
