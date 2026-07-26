# -*- coding: utf-8 -*-
"""
龙头质量评分引擎 - Leader Quality Engine V3

通过 Factor Registry 注册因子，在主题内部筛选优质龙头。
可以作为独立引擎运行，也可以注册到 GLOBAL_REGISTRY。

评分维度：
  - 截面得分 (Cross-section, 40%): 60日/20日收益、金额排名、新高天数、RSI健康度、波动控制、量比
  - 持续性得分 (Persistence, 30%): 存续天数、排名稳定性、Top3占比、排名动量
  - 动量得分 (Momentum, 20%): 加权5/20/60日收益
  - 量能健康度 (Volume Health, 10%): 量比稳定性、无极端放量
"""

import os
import sys
import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from market_regime_v3.factor_registry import GLOBAL_REGISTRY, FactorMeta, FactorCategory
from inst_pullback_v2.data.loader import DataLoader
from inst_pullback_v2.data.indicators import sma, rsi, volume_ratio
from market_regime_v3.engines.theme_correlation import ThemeCorrelationEngine, build_theme_corr_result


# ──────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────
_LOOKBACK_DAYS = 120  # 单只股票拉取的历史数据天数（含60日收益和持续性计算所需）
_THEME_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'theme_kg_v3', 'theme_kg_v3', 'config', 'theme_config.json'
)


# ──────────────────────────────────────────────
# 数据类定义
# ──────────────────────────────────────────────

@dataclass
class LeaderQualityResult:
    """龙头质量评分结果"""
    per_theme: Dict[str, List[Dict]]  # theme_name -> [{ts_code, name, score, factors}]
    top_leaders: List[Dict]  # overall top leaders across all themes
    theme_leader_strength: Dict[str, float]  # theme_name -> average leader score
    explain: Dict[str, str]


@dataclass
class LeaderScore:
    """单只龙头评分"""
    ts_code: str
    name: str
    total_score: float  # 0-100
    cross_section_score: float
    persistence_score: float
    momentum_score: float
    volume_health_score: float
    factors: Dict[str, float]  # individual factor scores
    rank: int = 0


# ──────────────────────────────────────────────
# 注册因子到 GLOBAL_REGISTRY
# ──────────────────────────────────────────────

def _register_factors():
    """将龙头质量相关的因子注册到 GLOBAL_REGISTRY"""
    factors = [
        # ── 截面因子 ──
        FactorMeta(
            name="leader_cross_section_ret_60d",
            category=FactorCategory.MOMENTUM,
            description="截面得分-60日收益(tanh归一化)",
            weight=0.20, min_value=0.0, max_value=100.0,
        ),
        FactorMeta(
            name="leader_cross_section_ret_20d",
            category=FactorCategory.MOMENTUM,
            description="截面得分-20日收益(tanh归一化)",
            weight=0.15, min_value=0.0, max_value=100.0,
        ),
        FactorMeta(
            name="leader_cross_section_amount_rank",
            category=FactorCategory.VOLUME,
            description="截面得分-主题内金额百分位排名",
            weight=0.20, min_value=0.0, max_value=100.0,
        ),
        FactorMeta(
            name="leader_cross_section_new_high_days",
            category=FactorCategory.TREND,
            description="截面得分-近20日新高天数",
            weight=0.10, min_value=0.0, max_value=100.0,
        ),
        FactorMeta(
            name="leader_cross_section_rsi_health",
            category=FactorCategory.TREND,
            description="截面得分-RSI_6健康度(40-80最优)",
            weight=0.10, min_value=0.0, max_value=100.0,
        ),
        FactorMeta(
            name="leader_cross_section_volatility_control",
            category=FactorCategory.VOLATILITY,
            description="截面得分-波动控制(低波动高分)",
            weight=0.10, min_value=0.0, max_value=100.0,
        ),
        FactorMeta(
            name="leader_cross_section_volume_ratio",
            category=FactorCategory.VOLUME,
            description="截面得分-量比健康度(0.8-2.0最优)",
            weight=0.15, min_value=0.0, max_value=100.0,
        ),
        # ── 持续性因子 ──
        FactorMeta(
            name="leader_persistence_tenure_days",
            category=FactorCategory.TREND,
            description="持续性-主题内存续天数",
            weight=0.25, min_value=0.0, max_value=100.0,
        ),
        FactorMeta(
            name="leader_persistence_rank_stability",
            category=FactorCategory.TREND,
            description="持续性-排名稳定性(低波动高分)",
            weight=0.20, min_value=0.0, max_value=100.0,
        ),
        FactorMeta(
            name="leader_persistence_top3_ratio_20d",
            category=FactorCategory.TREND,
            description="持续性-20日Top3占比",
            weight=0.20, min_value=0.0, max_value=100.0,
        ),
        FactorMeta(
            name="leader_persistence_top3_ratio_60d",
            category=FactorCategory.TREND,
            description="持续性-60日Top3占比",
            weight=0.15, min_value=0.0, max_value=100.0,
        ),
        FactorMeta(
            name="leader_persistence_rank_momentum",
            category=FactorCategory.MOMENTUM,
            description="持续性-排名动量(5日排名改善)",
            weight=0.20, min_value=0.0, max_value=100.0,
        ),
        # ── 综合因子 ──
        FactorMeta(
            name="leader_momentum",
            category=FactorCategory.MOMENTUM,
            description="动量得分-加权5/20/60日收益",
            weight=0.20, min_value=0.0, max_value=100.0,
        ),
        FactorMeta(
            name="leader_volume_health",
            category=FactorCategory.VOLUME,
            description="量能健康度-量比稳定性+无极端放量",
            weight=0.10, min_value=0.0, max_value=100.0,
        ),
    ]
    for meta in factors:
        GLOBAL_REGISTRY.register(meta, computer=lambda **kw: 0.0)


# 模块加载时自动注册
_register_factors()


# ──────────────────────────────────────────────
# LeaderQualityEngine
# ──────────────────────────────────────────────

class LeaderQualityEngine:
    """龙头质量评分引擎

    在主题内部筛选优质龙头，从截面表现、持续性、动量、量能健康度
    四个维度综合评分。

    用法:
        engine = LeaderQualityEngine(config)
        result = engine.evaluate(trade_date, theme_stock_map, top_themes)
    """

    def __init__(self, config: dict):
        """初始化引擎

        Args:
            config: 全局配置字典，读取 config['leader_quality'] 段
        """
        self.cfg = config.get('leader_quality', {})
        self.loader = DataLoader()

        # 动态主题相关性引擎(可选,用于解决双叙事交叉股归属)
        self._corr_engine = ThemeCorrelationEngine(config)
        self._corr_engine_ready = False

        # 子权重
        self._sub_weights = self.cfg.get('sub_weights', {
            'cross_section': 0.40,
            'persistence': 0.30,
            'momentum': 0.20,
            'volume_health': 0.10,
        })

        # 截面因子权重
        self._cs_weights = self.cfg.get('cross_section', {})

        # 持续性因子权重
        self._pers_weights = self.cfg.get('persistence', {})

        # 阈值
        self._thresholds = self.cfg.get('thresholds', {
            'min_tenure_days': 3,
            'min_ret_20d': 0.05,
            'max_volatility': 0.40,
        })

        # Top N 配置
        self._top_n = self.cfg.get('top_n_per_theme', 5)

    # ──────────────────────────────────────────
    # 公开接口
    # ──────────────────────────────────────────

    def evaluate(self, trade_date: str, theme_stock_map: dict,
                 top_themes: List[str]) -> LeaderQualityResult:
        """评估龙头质量

        Args:
            trade_date: 交易日 YYYYMMDD
            theme_stock_map: 主题-股票映射，支持 dict-in-list 格式
            top_themes: 待评分的主题名称列表

        Returns:
            LeaderQualityResult
        """
        start_date = self._calc_start_date(trade_date)

        per_theme: Dict[str, List[Dict]] = {}
        all_leader_scores: List[Tuple[str, LeaderScore]] = []  # (theme_name, score)

        # 遍历每个主题
        for theme_name in top_themes:
            theme_stocks = self._get_theme_stocks(theme_stock_map, theme_name)
            if not theme_stocks:
                continue

            # 批量加载该主题所有股票的因子数据
            factor_data = self.loader.load_stk_factor_batch(
                theme_stocks, start_date, trade_date
            )

            # 计算主题内每只股票的评分
            stock_scores = self._score_theme_stocks(
                theme_stocks, factor_data, trade_date
            )

            # 过滤不达标的股票
            stock_scores = self._filter_stocks(stock_scores, factor_data, trade_date)

            if not stock_scores:
                continue

            # 排序取 Top N
            stock_scores.sort(key=lambda x: x.total_score, reverse=True)
            for i, s in enumerate(stock_scores):
                s.rank = i + 1

            theme_top = stock_scores[:self._top_n]
            per_theme[theme_name] = [
                {
                    'ts_code': s.ts_code,
                    'name': s.name,
                    'score': round(s.total_score, 2),
                    'factors': s.factors,
                }
                for s in theme_top
            ]

            # 记录所有评分用于全局排名
            for s in stock_scores:
                all_leader_scores.append((theme_name, s))

        # ── 主题归属优化器 ──
        # 检查是否有股票（如 飞龙股份）因行业分类被匹配到错误主题
        # 根据概念标签重叠度 + 动态量价相关性 重分配到最匹配的主题
        per_theme = self._optimize_theme_assignment(
            per_theme, top_themes, theme_stock_map, trade_date
        )

        # 同步更新 all_leader_scores 中的 theme_name（反映优化后的归属）
        # 构建 {code: correct_theme} 映射表
        code_to_theme = {}
        for theme_name, leaders in per_theme.items():
            for ld in leaders:
                code = ld.get('ts_code', '') or ld.get('code', '')
                if code:
                    code_to_theme[code] = theme_name
        all_leader_scores = [
            (code_to_theme.get(s.ts_code, theme_name), s)
            for theme_name, s in all_leader_scores
        ]

        # 全局 Top Leaders（跨主题）
        all_leader_scores.sort(key=lambda x: x[1].total_score, reverse=True)
        top_leaders = [
            {
                'ts_code': s.ts_code,
                'name': s.name,
                'total_score': round(s.total_score, 2),
                'cross_section_score': round(s.cross_section_score, 2),
                'persistence_score': round(s.persistence_score, 2),
                'momentum_score': round(s.momentum_score, 2),
                'volume_health_score': round(s.volume_health_score, 2),
                'factors': s.factors,
                'theme': theme_name,
            }
            for theme_name, s in all_leader_scores[:self._top_n * 3]
        ]

        # 主题龙头强度（主题内前3名平均分）
        theme_leader_strength = {}
        for theme_name, leaders in per_theme.items():
            scores = [l['score'] for l in leaders[:3]]
            theme_leader_strength[theme_name] = round(
                sum(scores) / len(scores), 2
            ) if scores else 0.0

        # Explain
        explain = self._build_explain(per_theme, theme_leader_strength)

        return LeaderQualityResult(
            per_theme=per_theme,
            top_leaders=top_leaders,
            theme_leader_strength=theme_leader_strength,
            explain=explain,
        )

    # ──────────────────────────────────────────
    # 主题股票提取
    # ──────────────────────────────────────────

    def _get_theme_stocks(self, theme_stock_map: dict, theme_name: str) -> List[str]:
        """从主题-股票映射中提取股票代码列表

        支持三种格式：
        - list of dict: [{'ts_code': '...', 'name': '...'}, ...]
        - list of str: ['300502.SZ', '300308.SZ']
        - dict: {ts_code: {...}, ...}
        """
        data = theme_stock_map.get(theme_name, [])
        codes = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    code = item.get('ts_code', '') or item.get('code', '')
                    if code:
                        codes.append(code)
                elif isinstance(item, str):
                    codes.append(item)
            return codes
        if isinstance(data, dict):
            return list(data.keys())
        return []

    # ──────────────────────────────────────────
    # 日期计算
    # ──────────────────────────────────────────

    def _calc_start_date(self, trade_date: str) -> str:
        """计算拉取历史数据的起始日期（回退约120个交易日）"""
        from datetime import datetime, timedelta
        dt = datetime.strptime(trade_date, '%Y%m%d')
        # 粗略估算：120个交易日约168自然日
        start = dt - timedelta(days=int(_LOOKBACK_DAYS * 1.4))
        return start.strftime('%Y%m%d')

    # ──────────────────────────────────────────
    # 主题内批量评分
    # ──────────────────────────────────────────

    def _score_theme_stocks(self, ts_codes: List[str],
                            factor_data: Dict[str, pd.DataFrame],
                            trade_date: str) -> List[LeaderScore]:
        """计算主题内所有股票的评分"""
        stock_scores = []

        # 预提取最新日期的 amount，用于主题内排名
        amount_map = {}
        for ts_code in ts_codes:
            df = factor_data.get(ts_code)
            if df is not None and not df.empty:
                df_td = df[df['trade_date'].astype(str) == trade_date]
                if not df_td.empty:
                    amount_map[ts_code] = float(df_td.iloc[0].get('amount', 0))

        for ts_code in ts_codes:
            df = factor_data.get(ts_code)
            if df is None or df.empty:
                continue

            # 获取股票名称
            name = self.loader.get_stock_name(ts_code)

            # 计算各维度评分
            cs_score = self._calc_cross_section(df, trade_date, amount_map)
            pers_score = self._calc_persistence(df, trade_date)
            mom_score = self._calc_momentum(df, trade_date)
            vh_score = self._calc_volume_health(df, trade_date)

            # 综合评分
            cs_w = self._sub_weights.get('cross_section', 0.40)
            pers_w = self._sub_weights.get('persistence', 0.30)
            mom_w = self._sub_weights.get('momentum', 0.20)
            vh_w = self._sub_weights.get('volume_health', 0.10)

            total = (cs_score * cs_w + pers_score * pers_w +
                     mom_score * mom_w + vh_score * vh_w)

            stock_scores.append(LeaderScore(
                ts_code=ts_code,
                name=name,
                total_score=round(total, 2),
                cross_section_score=round(cs_score, 2),
                persistence_score=round(pers_score, 2),
                momentum_score=round(mom_score, 2),
                volume_health_score=round(vh_score, 2),
                factors={
                    'cross_section': round(cs_score * cs_w, 2),
                    'persistence': round(pers_score * pers_w, 2),
                    'momentum': round(mom_score * mom_w, 2),
                    'volume_health': round(vh_score * vh_w, 2),
                },
            ))

        return stock_scores

    # ──────────────────────────────────────────
    # 过滤
    # ──────────────────────────────────────────

    def _filter_stocks(self, stock_scores: List[LeaderScore],
                       factor_data: Dict[str, pd.DataFrame],
                       trade_date: str) -> List[LeaderScore]:
        """根据阈值过滤不合格股票"""
        thresholds = self._thresholds
        min_tenure = thresholds.get('min_tenure_days', 3)
        min_ret_20d = thresholds.get('min_ret_20d', 0.05)
        max_vol = thresholds.get('max_volatility', 0.40)

        filtered = []
        for s in stock_scores:
            df = factor_data.get(s.ts_code)
            if df is None or df.empty:
                continue

            df = df.sort_values('trade_date').reset_index(drop=True)

            # 20日收益率检查
            ret_20d = self._safe_ret(df, 'close_hfq', 20)
            if ret_20d is None or ret_20d < min_ret_20d:
                continue

            # 波动率检查
            vol_20d = self._safe_volatility(df, 'close_hfq', 20)
            if vol_20d is not None and vol_20d > max_vol:
                continue

            # 存续天数检查（用数据量近似）
            if len(df) < min_tenure:
                continue

            filtered.append(s)

        return filtered

    # ──────────────────────────────────────────
    # 截面得分 (Cross-section Score, 权重 0.40)
    # ──────────────────────────────────────────

    def _calc_cross_section(self, df: pd.DataFrame, trade_date: str,
                            amount_map: Dict[str, float]) -> float:
        """计算截面得分"""
        weights = self._cs_weights
        w_ret60 = weights.get('ret_60d', 0.20)
        w_ret20 = weights.get('ret_20d', 0.15)
        w_amount = weights.get('amount_rank', 0.20)
        w_new_high = weights.get('new_high_days', 0.10)
        w_rsi = weights.get('rsi_health', 0.10)
        w_vol = weights.get('volatility_control', 0.10)
        w_vr = weights.get('volume_ratio', 0.15)

        df = df.sort_values('trade_date').reset_index(drop=True)

        # ── ret_60d: 60日收益 tanh 归一化（0-100） ──
        ret_60d = self._safe_ret(df, 'close_hfq', 60)
        score_ret60 = self._tanh_score(ret_60d) if ret_60d is not None else 50.0

        # ── ret_20d: 20日收益 tanh 归一化 ──
        ret_20d = self._safe_ret(df, 'close_hfq', 20)
        score_ret20 = self._tanh_score(ret_20d) if ret_20d is not None else 50.0

        # ── amount_rank: 主题内金额百分位排名 ──
        ts_code = df.iloc[-1].get('ts_code', '')
        current_amount = amount_map.get(ts_code, 0)
        if amount_map and current_amount > 0:
            # 按金额降序排名，金额越大排名越高
            sorted_amounts = sorted(amount_map.values(), reverse=True)
            total = len(sorted_amounts)
            rank = sum(1 for a in sorted_amounts if a > current_amount) + 1
            score_amount = (1 - rank / total) * 100 if total > 0 else 50.0
        else:
            score_amount = 50.0

        # ── new_high_days: 近20日接近20日新高的天数 ──
        score_new_high = self._calc_new_high_score(df)

        # ── rsi_health: RSI_6 在 40-80 之间得高分 ──
        score_rsi = self._calc_rsi_health_score(df)

        # ── volatility_control: 低波动得高分 ──
        score_vol = self._calc_volatility_control_score(df)

        # ── volume_ratio: 量比在 0.8-2.0 之间得高分 ──
        score_vr = self._calc_volume_ratio_health_score(df)

        total = (score_ret60 * w_ret60 + score_ret20 * w_ret20 +
                 score_amount * w_amount + score_new_high * w_new_high +
                 score_rsi * w_rsi + score_vol * w_vol +
                 score_vr * w_vr)

        return min(100.0, max(0.0, total))

    def _tanh_score(self, ret: float) -> float:
        """tanh 归一化收益到 0-100 分

        对收益率使用 tanh 映射到 [-1, 1]，再线性映射到 [0, 100]。
        正收益集中在 50-100，负收益集中在 0-50。
        """
        # 对收益率做 tanh，使极端值被压缩到 [-1, 1]
        t = np.tanh(ret * 5)  # 5x 放大使区分度更好
        return (t + 1) * 50  # 映射到 [0, 100]

    def _calc_new_high_score(self, df: pd.DataFrame) -> float:
        """计算新高天数得分

        统计近20日内收盘价达到或接近20日最高价的次数。
        """
        if len(df) < 20:
            return 50.0
        recent = df.tail(20).reset_index(drop=True)
        close = recent['close_hfq'].values.astype(float)
        # 计算滚动20日最高价
        high_20 = pd.Series(close).rolling(20, min_periods=1).max().values
        # 在最高价的98%以内视为接近新高
        near_high = (close / high_20) >= 0.98
        count = int(near_high.sum())
        # 满分10天，线性映射
        score = min(100.0, count / 10 * 100)
        return score

    def _calc_rsi_health_score(self, df: pd.DataFrame) -> float:
        """计算 RSI_6 健康度得分

        RSI_6 在 40-80 之间得高分，偏离越远得分越低。
        """
        if 'rsi_bfq_6' not in df.columns:
            return 50.0
        latest_rsi = df['rsi_bfq_6'].iloc[-1]
        if pd.isna(latest_rsi):
            return 50.0
        rsi_val = float(latest_rsi)
        if 40 <= rsi_val <= 80:
            return 100.0
        if rsi_val < 40:
            return max(0.0, rsi_val / 40 * 100)
        # rsi_val > 80
        return max(0.0, (100 - rsi_val) / 20 * 100)

    def _calc_volatility_control_score(self, df: pd.DataFrame) -> float:
        """计算波动控制得分

        20日收益率波动越低得分越高。
        采用百分位映射：年化波动率 0% -> 100分, 60% -> 0分。
        """
        vol = self._safe_volatility(df, 'close_hfq', 20)
        if vol is None:
            return 50.0
        # 年化波动率，假设20日波动 * sqrt(252/20)
        annual_vol = vol * np.sqrt(252 / 20)
        # 线性映射：0% -> 100分, 60% -> 0分
        score = max(0.0, min(100.0, (0.60 - annual_vol) / 0.60 * 100))
        return score

    def _calc_volume_ratio_health_score(self, df: pd.DataFrame) -> float:
        """计算量比健康度得分

        当日量比在 0.8-2.0 之间得满分，偏离越远得分越低。
        """
        if len(df) < 20:
            return 50.0
        close_col = 'close_hfq' if 'close_hfq' in df.columns else 'close'
        close_s = df[close_col]
        vol_s = df['vol'] if 'vol' in df.columns else df['volume']

        # 计算量比
        avg_vol = vol_s.tail(21).head(20).mean()  # 过去20日均量（不含当天）
        if avg_vol == 0 or pd.isna(avg_vol):
            return 50.0
        current_vr = float(vol_s.iloc[-1]) / float(avg_vol)
        if 0.8 <= current_vr <= 2.0:
            return 100.0
        if current_vr < 0.8:
            return max(0.0, current_vr / 0.8 * 100)
        # current_vr > 2.0
        return max(0.0, max(0.0, (5.0 - current_vr) / 3.0 * 100))

    # ──────────────────────────────────────────
    # 持续性得分 (Persistence Score, 权重 0.30)
    # ──────────────────────────────────────────

    def _calc_persistence(self, df: pd.DataFrame, trade_date: str) -> float:
        """计算持续性得分

        如果数据不足，默认返回 50。
        """
        weights = self._pers_weights
        w_tenure = weights.get('tenure_days', 0.25)
        w_stability = weights.get('rank_stability', 0.20)
        w_top3_20d = weights.get('top3_ratio_20d', 0.20)
        w_top3_60d = weights.get('top3_ratio_60d', 0.15)
        w_rmom = weights.get('rank_momentum', 0.20)

        df = df.sort_values('trade_date').reset_index(drop=True)

        # 数据不足时默认 50
        if len(df) < 10:
            return 50.0

        # ── tenure_days: 数据集中存续天数 ──
        # 用有完整量价数据的天数近似存续天数
        close = df['close_hfq'].values
        valid_days = int(pd.notna(close).sum())
        score_tenure = min(100.0, valid_days / 60 * 100)

        # ── rank_stability: 用金额排名稳定性近似 ──
        # 按日计算金额的滚动排名波动，波动越低越稳定
        if 'amount' in df.columns and len(df) >= 20:
            amount_series = df['amount'].fillna(0).values
            # 用日金额变化率的标准差衡量稳定性
            pct_changes = np.diff(amount_series) / (amount_series[:-1] + 1e-10)
            pct_changes = pct_changes[~np.isnan(pct_changes) & ~np.isinf(pct_changes)]
            if len(pct_changes) > 5:
                rank_std = float(np.std(pct_changes))
                score_stability = max(0.0, min(100.0, (1.0 - np.tanh(rank_std * 2)) * 100))
            else:
                score_stability = 50.0
        else:
            score_stability = 50.0

        # ── top3_ratio_20d: 近20日在主题内金额排名前3的比例 ──
        # 注：这里无法获取每日的真实主题内排名，用金额增长的一致性来近似
        score_top3_20d = self._calc_top3_ratio(df, 20)

        # ── top3_ratio_60d: 近60日在主题内金额排名前3的比例 ──
        score_top3_60d = self._calc_top3_ratio(df, 60)

        # ── rank_momentum: 排名动量（近期金额趋势改善） ──
        score_rmom = self._calc_rank_momentum(df)

        total = (score_tenure * w_tenure + score_stability * w_stability +
                 score_top3_20d * w_top3_20d + score_top3_60d * w_top3_60d +
                 score_rmom * w_rmom)

        return min(100.0, max(0.0, total))

    def _calc_top3_ratio(self, df: pd.DataFrame, lookback: int) -> float:
        """估算近 N 日金额排名 Top3 的比例

        通过金额增长率的一致性来近似衡量。
        如果金额持续增长且增速稳定，视为持续保持领先地位。
        """
        if len(df) < lookback or 'amount' not in df.columns:
            return 50.0
        recent = df.tail(lookback).copy()
        amount = recent['amount'].fillna(0).values
        if len(amount) < 10:
            return 50.0
        # 计算金额每日变化率
        changes = np.diff(amount) / (amount[:-1] + 1e-10)
        # 正变化率占比高 -> 金额持续增长 -> 近似高排名比例
        positive_ratio = float((changes > 0).sum() / len(changes))
        # 金额变异系数小 -> 金额稳定 -> 近似排名稳定
        cv = float(np.std(amount) / (np.mean(amount) + 1e-10))
        cv_score = max(0.0, min(100.0, (2.0 - cv) / 2.0 * 100))
        combined = positive_ratio * 0.6 + cv_score * 0.4
        return min(100.0, combined * 100)

    def _calc_rank_momentum(self, df: pd.DataFrame) -> float:
        """计算排名动量

        近5日金额趋势改善程度：近期金额增速是否加快。
        """
        if len(df) < 10 or 'amount' not in df.columns:
            return 50.0
        recent = df.tail(10).copy()
        amount = recent['amount'].fillna(0).values
        if len(amount) < 6:
            return 50.0
        # 前5天的平均增长率 vs 后5天的平均增长率
        early_slice = amount[:5]
        late_slice = amount[-5:]
        early_growth = (early_slice[-1] - early_slice[0]) / (early_slice[0] + 1e-10)
        late_growth = (late_slice[-1] - late_slice[0]) / (late_slice[0] + 1e-10)
        improvement = late_growth - early_growth
        # tanh 映射到 [0, 100]
        return (np.tanh(improvement * 5) + 1) * 50

    # ──────────────────────────────────────────
    # 动量得分 (Momentum Score, 权重 0.20)
    # ──────────────────────────────────────────

    def _calc_momentum(self, df: pd.DataFrame, trade_date: str) -> float:
        """计算动量得分

        加权 5/20/60 日收益，权重由 config 控制。
        默认权重：[5d: 0.3, 20d: 0.5, 60d: 0.2]
        """
        weights = self.cfg.get('momentum', {}).get('weights', [0.3, 0.5, 0.2])
        periods = [5, 20, 60]

        total = 0.0
        total_w = 0.0
        for period, w in zip(periods, weights):
            ret = self._safe_ret(df, 'close_hfq', period)
            if ret is not None:
                score = self._tanh_score(ret)
                total += score * w
                total_w += w

        if total_w == 0:
            return 50.0
        return min(100.0, max(0.0, total / total_w))

    # ──────────────────────────────────────────
    # 量能健康度 (Volume Health Score, 权重 0.10)
    # ──────────────────────────────────────────

    def _calc_volume_health(self, df: pd.DataFrame, trade_date: str) -> float:
        """计算量能健康度

        - 量比稳定性：近期量比变异系数低 -> 高分
        - 无极端放量：量比 < 4.0 -> 高分
        """
        if len(df) < 20 or 'vol' not in df.columns:
            return 50.0

        close_col = 'close_hfq' if 'close_hfq' in df.columns else 'close'
        recent = df.tail(30).copy()
        vol = recent['vol'].fillna(0).values.astype(float)

        if len(vol) < 21:
            return 50.0

        # 计算每日量比
        vr_list = []
        for i in range(20, len(vol)):
            avg = vol[i - 20:i].mean()
            if avg > 0:
                vr_list.append(vol[i] / avg)
        if not vr_list:
            return 50.0

        vr_arr = np.array(vr_list)

        # ── 量比稳定性：变异系数越低越好 ──
        cv = float(np.std(vr_arr) / (np.mean(vr_arr) + 1e-10))
        stability_score = max(0.0, min(100.0, (1.0 - cv) * 100))

        # ── 无极端放量：量比 < 4.0 为健康 ──
        extreme_ratio = float((vr_arr > 4.0).sum() / len(vr_arr))
        no_extreme_score = (1.0 - extreme_ratio) * 100

        # ── 量比适中：均值在 0.5-2.5 之间加分 ──
        mean_vr = float(np.mean(vr_arr))
        if 0.5 <= mean_vr <= 2.5:
            moderate_bonus = 10.0
        else:
            moderate_bonus = 0.0

        combined = stability_score * 0.40 + no_extreme_score * 0.40 + moderate_bonus
        return min(100.0, max(0.0, combined))

    # ──────────────────────────────────────────
    # 工具方法
    # ──────────────────────────────────────────

    @staticmethod
    def _safe_ret(df: pd.DataFrame, price_col: str, period: int) -> Optional[float]:
        """安全计算收益率"""
        if price_col not in df.columns or len(df) < period + 1:
            return None
        close = df[price_col].values
        if pd.isna(close[-1]) or pd.isna(close[-(period + 1)]):
            return None
        if close[-(period + 1)] == 0:
            return None
        return float((close[-1] - close[-(period + 1)]) / close[-(period + 1)])

    @staticmethod
    def _safe_volatility(df: pd.DataFrame, price_col: str, period: int) -> Optional[float]:
        """安全计算波动率"""
        if price_col not in df.columns or len(df) < period + 1:
            return None
        close = df[price_col].values.astype(float)
        rets = np.diff(close[-(period + 1):]) / (close[-(period + 1):-1] + 1e-10)
        rets = rets[~np.isnan(rets) & ~np.isinf(rets)]
        if len(rets) < 5:
            return None
        return float(np.std(rets))

    # ──────────────────────────────────────────
    # 主题归属优化器
    # ──────────────────────────────────────────

    def _optimize_theme_assignment(self, per_theme: Dict[str, List[Dict]],
                                    top_themes: List[str],
                                    theme_stock_map: dict,
                                    trade_date: str = None) -> Dict[str, List[Dict]]:
        """
        Theme归属优化器：基于个股概念标签 vs 主题概念的重叠度，
        将跨主题概念重叠的个股重分配到最匹配的主题。

        【核心逻辑】
        使用"增量重叠 + 置信度惩罚"双重机制：
        1. 增量重叠: 计算个股与各主题的 unique 概念重叠（去重当前主题已覆盖的部分）
        2. 置信度惩罚: 如果个股通过低置信度方式进入当前主题(via=dc_industry_board, score<60)，
           且与另一主题有独特概念重叠，则视为应迁移

        这解决了飞龙股份(002536.SZ)的案例：
        - 飞龙因行业(汽车配件)低置信度匹配到新能源车(score=49)
        - 但其拥有'液冷概念'标签 -> AI算力主题的独有概念重叠
        - 优化器将飞龙重分配到AI算力主题

        Args:
            per_theme: 当前按主题分组的龙头结果
            top_themes: 活跃主题列表(中文名)
            theme_stock_map: 主题-个股映射(含concepts/via/score字段)

        Returns:
            调整后的 per_theme
        """
        if not self.cfg.get('enable_theme_optimizer', True):
            return per_theme

        # 1. 构建主题画像(仅使用概念类字段，避免关键词稀释)
        theme_profiles = self._load_theme_profiles(top_themes)
        if not theme_profiles:
            return per_theme

        # 2. 构建个股概念索引 + 入场元数据
        stock_concepts = self._extract_stock_concepts(theme_stock_map)
        if not stock_concepts:
            return per_theme

        # 3. 提取个股在各主题中的入场置信度
        #    via=leader_company/core_company -> 高置信度(不迁移)
        #    via=dc_industry_board + score<60 -> 低置信度(可迁移)
        stock_entry_meta = self._extract_stock_entry_meta(theme_stock_map)

        # 3.5 准备动态相关性引擎(用于解决双叙事交叉股)
        enable_corr = self.cfg.get('enable_dynamic_correlation', True)
        if enable_corr:
            self._prepare_corr_engine(top_themes)
            self._corr_engine_ready = True

        # 4. 检查每只股票是否需要迁移或驱逐
        reassignments = []  # (code, name, from_theme, to_theme, overlap, incremental, corr)
        evictions = []  # (code, name, from_theme, reason)  零概念匹配的低置信度杂音股
        # 缓存相关性结果，避免重复计算
        corr_cache: Dict[str, Dict[str, float]] = {}
        corr_done: set = set()  # 已计算过相关性的股票代码集合

        for theme_name, leaders in per_theme.items():
            current_profile = theme_profiles.get(theme_name)
            if current_profile is None:
                continue

            for leader in leaders:
                code = leader.get('ts_code', '') or leader.get('code', '')
                if not code:
                    continue
                name = leader.get('name', code)
                sc_set = stock_concepts.get(code)
                if not sc_set:
                    continue

                # 获取该股票在当前主题的入场置信度
                entry_info = stock_entry_meta.get(code, {})
                entry_via = entry_info.get(theme_name, {}).get('via', '')
                entry_score = entry_info.get(theme_name, {}).get('score', 100)
                is_low_confidence = (entry_score < 60 and
                                     entry_via not in ('leader_company', 'core_company'))

                # 计算当前主题的重叠度
                current_overlap = self._calc_concept_overlap(sc_set, current_profile)

                # ── 驱逐检查：零/微量概念匹配 + 低置信度入场的杂音股 ──
                # 阈值<2.0表示最多只有1个低权重子串匹配(如"电机" in "发电机概念")，
                # 不足以证明该股与主题真正相关
                if is_low_confidence and current_overlap < 2.0:
                    # 检查是否与其他任何主题有概念重叠
                    has_any_overlap = False
                    for other_name, other_profile in theme_profiles.items():
                        if other_name == theme_name:
                            continue
                        other_ov = self._calc_concept_overlap(sc_set, other_profile)
                        if other_ov > 0:
                            has_any_overlap = True
                            break
                    if not has_any_overlap:
                        evictions.append((code, name, theme_name,
                                          f"零概念重叠(via={entry_via},score={entry_score})"))
                        continue  # 跳过后续迁移检查

                # 计算增量重叠: 与其他主题的重叠中，不被当前主题覆盖的部分
                best_theme = theme_name
                best_incremental = 0.0
                best_overlap_raw = 0.0
                best_corr_score = 0.0

                for other_name, other_profile in theme_profiles.items():
                    if other_name == theme_name:
                        continue
                    # 计算与其他主题的完整重叠
                    other_overlap = self._calc_concept_overlap(sc_set, other_profile)
                    # 增量重叠 = 其他主题中有而当前主题没有的匹配
                    incremental = self._calc_incremental_overlap(
                        sc_set, other_profile, current_profile
                    )
                    # 判断条件:
                    #   条件A: 其他主题重叠度 > 当前主题(绝对优势)
                    #   条件B: 低置信度入场 + 增量重叠>0（独特概念匹配）
                    cond_a = (other_overlap > current_overlap + 0.5)
                    cond_b = (is_low_confidence and incremental > 0)
                    # 条件C: 动态相关性确认(量价三因子)
                    cond_c = False
                    if enable_corr and self._corr_engine_ready:
                        # 首次遇到该股票时评估相关性（仅一次，结果缓存）
                        if code not in corr_cache:
                            try:
                                corr_cache[code] = self._corr_engine.evaluate(
                                    code, trade_date,
                                    top_themes
                                )
                                corr_done.add(code)
                            except Exception as _exc:
                                corr_done.add(code)  # 标记为已处理，避免重复尝试
                        if code in corr_cache and corr_cache[code]:
                            other_corr = corr_cache[code].get(other_name, 0.0)
                            current_corr = corr_cache[code].get(theme_name, 0.0)
                            cond_c = (other_corr > current_corr + 0.05)

                    if cond_a or cond_b or cond_c:
                        # 综合评分: 选增量重叠最高的(静态胜出时)或相关性最高的(动态胜出时)
                        if cond_c:
                            corr_val = corr_cache[code].get(other_name, 0.0)
                            if corr_val > best_corr_score:
                                best_theme = other_name
                                best_incremental = incremental
                                best_overlap_raw = other_overlap
                                best_corr_score = corr_val
                        elif incremental > best_incremental:
                            best_theme = other_name
                            best_incremental = incremental
                            best_overlap_raw = other_overlap

                if best_theme != theme_name:
                    reassignments.append((
                        code, name, theme_name, best_theme,
                        best_overlap_raw, best_incremental,
                        corr_cache.get(code, {}).get(best_theme, 0.0)
                    ))

        if not reassignments:
            return per_theme

        # 5. 执行重分配
        print(f"\n  [主题归属优化] 发现 {len(reassignments)} 只个股需调整主题归属:")
        for item in reassignments:
            code, name, from_t, to_t = item[0], item[1], item[2], item[3]
            score_ov, inc, corr_score = item[4], item[5], item[6] if len(item) > 6 else 0.0
            corr_str = f" 相关={corr_score:.2f}" if corr_score > 0 else ""
            print(f"    {name}({code}): {from_t} -> {to_t} "
                  f"(重叠={score_ov:.1f}, 增量={inc:.1f}{corr_str})")

        # ── 驱逐输出 ──
        if evictions:
            print(f"  [主题驱逐] 发现 {len(evictions)} 只零概念杂音股需从主题移除:")
            for code, name, from_t, reason in evictions:
                print(f"    {name}({code}): 从 {from_t} 移除 ({reason})")

        # 从原主题移除，加入新主题
        for item in reassignments:
            code, name, from_t, to_t = item[0], item[1], item[2], item[3]
            score_ov, inc = item[4], item[5]
            # 先找到并保存待迁移的项目
            orig_item = None
            if from_t in per_theme:
                for ld in per_theme[from_t]:
                    if (ld.get('ts_code', '') or ld.get('code', '')) == code:
                        orig_item = ld
                        break
                # 从原主题移除
                per_theme[from_t] = [l for l in per_theme[from_t]
                                     if (l.get('ts_code', '') or l.get('code', '')) != code]
            if orig_item:
                if to_t not in per_theme:
                    per_theme[to_t] = []
                per_theme[to_t].append(orig_item)

        # 执行驱逐：从主题中移除零概念杂音股
        if evictions:
            for code, name, from_t, _reason in evictions:
                if from_t in per_theme:
                    per_theme[from_t] = [l for l in per_theme[from_t]
                                         if (l.get('ts_code', '') or l.get('code', '')) != code]

        per_theme = {k: v for k, v in per_theme.items() if v}
        return per_theme

    @staticmethod
    def _calc_incremental_overlap(stock_concepts: set, target_profile: dict,
                                   current_profile: dict) -> float:
        """
        计算个股与目标主题的"增量重叠"：目标主题中有、但当前主题没有的匹配。

        这衡量了如果迁移到目标主题，能新增多少独特的加权概念覆盖。
        使用加权匹配确保权威来源(如 eastmoney_concepts)的匹配得到更高权重。
        """
        target_weighted = target_profile.get('weighted', {})
        target_all = target_profile.get('all_terms', set())
        current_weighted = current_profile.get('weighted', {})
        current_all = current_profile.get('all_terms', set())

        if not target_all or not stock_concepts:
            return 0.0

        incremental = 0.0
        for sc in stock_concepts:
            # 找到最佳目标术语匹配
            matched_term = None
            match_type = None  # 'exact', 'substr', 'superstr'
            if sc in target_all:
                matched_term = sc
                match_type = 'exact'
            else:
                for t in target_all:
                    if t in sc:
                        if match_type != 'exact':
                            matched_term = t
                            match_type = 'substr'
                        break
                if not matched_term:
                    for t in target_all:
                        if sc in t:
                            matched_term = t
                            match_type = 'superstr'
                            break

            if matched_term is None:
                continue

            # 检查当前主题是否也覆盖了这个匹配
            covered = False
            if matched_term in current_all:
                covered = True
            else:
                # 子串检查: 当前主题是否有术语是匹配术语的子串/超串
                for ct in current_all:
                    if ct in matched_term or matched_term in ct:
                        covered = True
                        break

            if not covered:
                # 增量匹配: 使用目标主题的权重
                base_w = target_weighted.get(matched_term, 2.0)
                if match_type == 'exact':
                    incremental += base_w
                elif match_type == 'substr':
                    incremental += base_w * 0.75
                else:  # superstr
                    incremental += base_w * 0.5

        return incremental

    def _prepare_corr_engine(self, top_themes: List[str]):
        """准备动态相关性引擎的主题资产映射(ETF+龙头代码)"""
        try:
            self._corr_engine.prepare_theme_assets(_THEME_CONFIG_PATH, top_themes)
        except Exception as e:
            print(f"  [主题归属优化] 准备相关性引擎失败: {e}")

    def _load_theme_profiles(self, top_themes: List[str]) -> Dict[str, dict]:
        """
        从 theme_config.json 加载活跃主题的 keywords + eastmoney_concepts。

        返回增强画像，包含按来源分层的术语权重：
        - eastmoney(官方概念分类): 权重4.0
        - industry_chains(产业链叙事): 权重4.0
        - core_keywords(主题核心词): 权重3.0
        - ths_concepts(同花顺概念): 权重2.0
        - keywords/keyword类(一般关键词): 权重1.5

        Returns:
            {theme_cn_name: {
                'all_terms': set,      # 全量术语(用于快速检查)
                'weighted': dict,      # {term: weight}
            }}
        """
        if not os.path.exists(_THEME_CONFIG_PATH):
            print(f"  [主题归属优化] 主题配置文件不存在: {_THEME_CONFIG_PATH}")
            return {}

        try:
            with open(_THEME_CONFIG_PATH, 'r', encoding='utf-8') as f:
                raw = json.load(f)
        except Exception as e:
            print(f"  [主题归属优化] 加载主题配置失败: {e}")
            return {}

        profiles = {}
        top_set = set(top_themes)
        for eng_key, cfg in raw.items():
            if not isinstance(cfg, dict):
                continue
            cn_name = cfg.get('name_cn', '')
            if not cn_name or cn_name not in top_set:
                continue

            weighted = {}  # term -> weight
            all_terms = set()

            def _add_terms(source_list, weight):
                for t in source_list:
                    t_clean = t.strip().lower()
                    if not t_clean:
                        continue
                    all_terms.add(t_clean)
                    # 保留最高权重：如果已存在且新权重更高，则更新
                    if t_clean not in weighted or weighted[t_clean] < weight:
                        weighted[t_clean] = weight

            # 官方概念分类(权重最高)
            _add_terms(cfg.get('eastmoney_concepts', []), 4.0)
            # 产业链叙事
            _add_terms(cfg.get('industry_chains', []), 4.0)
            # 主题核心词
            _add_terms(cfg.get('core_keywords', []), 3.0)
            # 同花顺概念
            _add_terms(cfg.get('ths_concepts', []), 2.5)
            # 其他关键词
            for kw_key in ['keywords', 'concept_keywords', 'product_keywords',
                           'industry_keywords', 'brand_keywords']:
                _add_terms(cfg.get(kw_key, []), 1.5)
            # exclude_keywords 不加入(负向过滤)

            profiles[cn_name] = {
                'all_terms': all_terms,
                'weighted': weighted,
            }

        return profiles

    def _extract_stock_concepts(self, theme_stock_map: dict) -> Dict[str, set]:
        """
        从 theme_stock_map 中提取每只股票的所有concept标签。

        theme_stock_map 格式:
        {
            'AI算力': [{'code': '...', 'concepts': ['液冷概念', ...]}, ...],
            '新能源车': [...],
        }

        Returns:
            {stock_code: {'液冷概念', '机器人概念', ...}}
        """
        sc_map = {}
        for theme_name, stocks in theme_stock_map.items():
            if not isinstance(stocks, list):
                continue
            for s in stocks:
                if not isinstance(s, dict):
                    continue
                code = s.get('ts_code', '') or s.get('code', '')
                if not code:
                    continue
                concepts_list = s.get('concepts', [])
                if isinstance(concepts_list, list) and concepts_list:
                    if code not in sc_map:
                        sc_map[code] = set()
                    for c in concepts_list:
                        if isinstance(c, str):
                            sc_map[code].add(c.strip().lower())
        return sc_map

    def _extract_stock_entry_meta(self, theme_stock_map: dict) -> Dict[str, dict]:
        """
        提取个股在各主题中的入场元数据(via/score)，用于置信度判断。

        theme_stock_map 中每个股票条目包含:
        {
            'code': '002536.SZ',
            'via': 'dc_industry_board',     # 入场方式
            'score': 49,                    # 匹配置信度(0-100)
            ...
        }

        Returns:
            {stock_code: {theme_cn_name: {'via': str, 'score': float}, ...}}
        """
        meta = {}
        for theme_name, stocks in theme_stock_map.items():
            if not isinstance(stocks, list):
                continue
            for s in stocks:
                if not isinstance(s, dict):
                    continue
                code = s.get('ts_code', '') or s.get('code', '')
                if not code:
                    continue
                via = s.get('via', '')
                score = s.get('score', 100) or 100
                if code not in meta:
                    meta[code] = {}
                meta[code][theme_name] = {
                    'via': str(via),
                    'score': float(score) if score is not None else 100.0,
                }
        return meta

    @staticmethod
    def _calc_concept_overlap(stock_concepts: set, theme_profile: dict) -> float:
        """
        计算个股概念与主题画像的加权重叠度。

        使用 theme_profile['weighted'] 中的权重:
        - eastmoney/industry_chains: 4.0
        - exact match: 直接用术语权重
        - substring match: 权重 * 0.75
        """
        weighted = theme_profile.get('weighted', {})
        all_terms = theme_profile.get('all_terms', set())
        if not all_terms or not stock_concepts:
            return 0.0

        score = 0.0
        for sc in stock_concepts:
            # 精确匹配
            if sc in all_terms:
                w = weighted.get(sc, 2.0)
                score += w
                continue
            # 子串匹配: 个股概念包含主题术语
            best_w = 0.0
            best_term = None
            for term in all_terms:
                if term in sc:
                    w = weighted.get(term, 1.5) * 0.75
                    if w > best_w:
                        best_w = w
                        best_term = term
            if best_w > 0:
                score += best_w
                continue
            # 主题术语包含个股概念
            for term in all_terms:
                if sc in term:
                    w = weighted.get(term, 1.5) * 0.5
                    score += w
                    break

        return score

    @staticmethod
    def _build_explain(per_theme: dict,
                       theme_leader_strength: dict) -> Dict[str, str]:
        """构建评分解释"""
        explain = {}
        # 主题 leader 强度排名
        if theme_leader_strength:
            sorted_themes = sorted(
                theme_leader_strength.items(), key=lambda x: x[1], reverse=True
            )
            top3 = sorted_themes[:3]
            explain['theme_strength_top3'] = (
                f"龙头强度前三主题: {'; '.join(f'{t[0]}({t[1]:.1f}分)' for t in top3)}"
            )
        # 各主题龙头数量
        theme_counts = {
            t: len(v) for t, v in per_theme.items()
        }
        if theme_counts:
            avg = np.mean(list(theme_counts.values()))
            explain['theme_leader_count'] = (
                f"平均每个主题筛选出 {avg:.1f} 只龙头"
            )
        return explain
