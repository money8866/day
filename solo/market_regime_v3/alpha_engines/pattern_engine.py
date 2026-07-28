# -*- coding: utf-8 -*-
"""
Historical Pattern Engine (V6.1 Module 1)

核心升级：把交易概率从"规则估计"升级为"历史统计概率"。

功能：
  1. 每日记录回撤候选标的的全量特征到 pattern_history 数据库
  2. 对当前候选股票自动寻找历史相似案例
  3. 输出基于历史样本统计的概率和收益分布
  4. 增加Confidence体系：样本量置信度 + 时效性评分 + 匹配质量
  5. 解决冷启动交易：启发式→数据驱动渐进过渡

设计原则：
  - 概率必须来自历史样本统计，不允许人工设定概率
  - 相似度匹配基于：市场状态 + 回撤特征 + 主题 + 均线类型
  - Confidence决定信号的可靠性，低Conf → 降仓/等待
  - 冷启动阶段使用启发式规则填充，随样本积累平滑过渡
"""

import os
import sys
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from market_regime_v3.alpha_engines.pattern_db import (
    query_pattern_stats, query_similar_patterns,
    batch_save_pattern_records, save_snapshot_records,
    PATTERN_DB_PATH, get_record_count
)
import stock_cache as sc


# ──────────────────────────────────────────────
# 数据结构
# ──────────────────────────────────────────────

@dataclass
class PatternMatchResult:
    """历史模式匹配结果 — V6.2 含Pattern Type"""
    ts_code: str
    name: str = ''
    theme: str = ''

    # 匹配统计
    n_samples: int = 0
    win_probability: float = 0.5       # 最终使用概率（混合后）
    avg_return_5d: float = 0.0         # 未来5日平均收益
    avg_return_10d: float = 0.0        # 未来10日平均收益
    avg_return_20d: float = 0.0        # 未来20日平均收益
    median_return_10d: float = 0.0     # 未来10日中位收益
    avg_max_drawdown: float = 0.0      # 预期最大回撤
    avg_holding_days: float = 0.0      # 平均持有天数
    avg_win_return: float = 0.0        # 成功案例平均收益
    avg_loss_return: float = 0.0       # 失败案例平均亏损

    # ════════════════════════════════════════
    # V6.1 Confidence 字段
    # ════════════════════════════════════════
    confidence: float = 0.0                # 综合置信度 0.0~1.0
    sample_size_confidence: float = 0.0    # 样本量置信度
    recency_score: float = 0.0             # 时效性评分
    match_quality: float = 1.0             # 匹配质量
    recency_weighted_win_rate: float = 0.5 # 时效加权胜率

    # ════════════════════════════════════════
    # V6.1 冷启动字段
    # ════════════════════════════════════════
    cold_start_phase: str = 'data_driven'  # cold / warm / data_driven
    heuristic_probability: float = 0.5     # 启发式估计概率
    heuristic_avg_return: float = 0.0      # 启发式预期收益
    data_probability: float = 0.5          # 纯数据驱动概率
    blend_alpha: float = 0.0               # 混合系数 0=纯启发式, 1=纯数据

    # ════════════════════════════════════════
    # V6.2 Pattern Bucket
    # ════════════════════════════════════════
    pattern_type: str = 'PULLBACK_ALPHA'   # 模式类型

    # 匹配条件（用于debug）
    match_conditions: Dict = field(default_factory=dict)


@dataclass
class PatternEngineResult:
    """模式引擎输出"""
    trade_date: str
    matches: Dict[str, PatternMatchResult] = field(default_factory=dict)
    stats: Dict = field(default_factory=dict)  # 整体统计


# ──────────────────────────────────────────────
# 启发式概率估计
# ──────────────────────────────────────────────

def estimate_heuristic_probability(
    ret_60d: float,
    drawdown: float,
    market_regime: str,
    pullback_ma: str = 'MA20',
    amount: float = 0.0,
    turnover_rate: float = 0.0,
) -> Tuple[float, float, str]:
    """基于交易规则的启发式概率估计

    用于冷启动阶段没有历史样本时的替代方案。
    返回 (heuristic_probability, heuristic_avg_return, phase_label)

    规则逻辑：
      - MA20回踩 + 缩量调整 + 强势市场 → 较高概率
      - MA60深回踩 + 弱势市场 → 较低概率
      - 综合多个维度的加分/扣分
    """
    prob = 0.50
    avg_ret = 0.02  # 默认预期2%

    # 1. 回踩均线类型（锚定强度）
    if pullback_ma == 'MA10':
        prob += 0.08           # 浅回踩，强势特征
        avg_ret += 0.01
    elif pullback_ma == 'MA20':
        prob += 0.05           # 正常回踩
    elif pullback_ma == 'MA30':
        prob += 0.02           # 稍深
    elif pullback_ma == 'MA60':
        prob -= 0.05           # 深回踩，弱势特征

    # 2. 回撤幅度（调整充分度）
    if 0.05 <= drawdown <= 0.15:
        prob += 0.05           # 5-15%回撤，调整充分
    elif 0.15 < drawdown <= 0.25:
        prob += 0.03           # 深度调整，有反弹需求
    elif drawdown > 0.25:
        prob -= 0.05           # 过度回撤，可能趋势破坏

    # 3. 60日涨幅（前期动量）
    if ret_60d >= 0.30:
        prob += 0.08           # 强趋势
        avg_ret += 0.02
    elif ret_60d >= 0.15:
        prob += 0.05
        avg_ret += 0.01
    elif ret_60d < -0.10:
        prob -= 0.08           # 弱势

    # 4. 市场状态（宏观背景）
    market_boost = {
        'Bull': 0.08,
        'Recovery': 0.05,
        '震荡': 0.02,
        '调整': -0.05,
        'Bear': -0.10,
        '主升浪': 0.10,
        '主跌': -0.15,
    }
    prob += market_boost.get(market_regime, 0.0)

    # 5. 量能特征
    if turnover_rate < 5.0:
        prob += 0.03           # 缩量调整，抛压轻

    # 裁剪到合理范围
    prob = max(0.25, min(0.80, prob))
    avg_ret = max(-0.02, min(0.08, avg_ret))

    # 阶段标签
    phase = 'cold'

    return prob, avg_ret, phase


# ──────────────────────────────────────────────
# Pattern Type 分类
# ──────────────────────────────────────────────

PATTERN_TYPES = {
    'PULLBACK_ALPHA': '龙头首次回踩',
    'BREAKOUT_ALPHA': '突破新高',
    'ROTATION_ALPHA': '主题轮动',
    'REBOUND_ALPHA': '超跌反弹',
    'PRE_ROTATE_ALPHA': '主题预启动',
}


def classify_pattern_type(
    ret_60d: float,
    drawdown: float,
    pullback_ma: str = '',
    leader_rank: int = None,
    amount: float = 0.0,
    turnover_rate: float = 0.0,
) -> str:
    """基于个股特征自动分类模式类型

    规则:
      PULLBACK_ALPHA:   ret_60d >= 0, drawdown > 0, 有回踩均线, leader_rank <= 10
      BREAKOUT_ALPHA:   ret_60d > 0, drawdown < 5%, 无明显回踩
      ROTATION_ALPHA:   ret_60d >= 0, 非龙头, 主题轮动特征
      REBOUND_ALPHA:    ret_60d < 0, drawdown > 15%, 超跌
      PRE_ROTATE_ALPHA: 低量能, 低涨幅, 潜在启动
    """
    # 超跌反弹
    if ret_60d < -0.10 and drawdown > 0.15:
        return 'REBOUND_ALPHA'

    # 突破新高: 正收益且回撤很小
    if ret_60d > 0.05 and drawdown < 0.05:
        return 'BREAKOUT_ALPHA'

    # 龙头回踩
    if ret_60d >= 0:
        is_leader = leader_rank is not None and leader_rank <= 10
        has_pullback = bool(pullback_ma) and drawdown > 0.03

        if is_leader and has_pullback:
            return 'PULLBACK_ALPHA'
        elif has_pullback:
            return 'ROTATION_ALPHA'
        elif is_leader:
            return 'PULLBACK_ALPHA'

    # 预启动: 低量能、低涨幅
    if ret_60d < 0.20 and turnover_rate < 3.0 and amount < 5e8:
        return 'PRE_ROTATE_ALPHA'

    # 兜底
    if ret_60d >= 0:
        return 'ROTATION_ALPHA'
    return 'REBOUND_ALPHA'


# ──────────────────────────────────────────────
# 主引擎
# ──────────────────────────────────────────────

class HistoricalPatternEngine:
    """历史模式引擎 — 基于历史样本统计的概率估计 + Confidence + 冷启动"""

    def __init__(self, config: dict):
        cfg = config.get('pattern_engine', {})
        self.min_samples = cfg.get('min_samples', 5)
        self.default_probability = cfg.get('default_probability', 0.5)
        self.drawdown_tolerance = cfg.get('drawdown_tolerance', 0.03)
        self.ret_60d_tolerance = cfg.get('ret_60d_tolerance', 0.10)
        self.enabled = cfg.get('enabled', True)

        # V6.1 Cold Start 配置
        cs_cfg = cfg.get('cold_start', {})
        self.cold_start_enabled = cs_cfg.get('enabled', True)
        self.warmup_threshold = cs_cfg.get('warmup_threshold', 15)
        self.heuristic_mix_min = cs_cfg.get('heuristic_mix_min', 0.70)
        self.heuristic_mix_max = cs_cfg.get('heuristic_mix_max', 0.05)

        # V6.1 Confidence 配置
        conf_cfg = cfg.get('confidence', {})
        self.conf_sample_weight = conf_cfg.get('sample_weight', 0.40)
        self.conf_recency_weight = conf_cfg.get('recency_weight', 0.35)
        self.conf_quality_weight = conf_cfg.get('quality_weight', 0.25)
        self.conf_buy_threshold = conf_cfg.get('buy_threshold', 0.40)
        self.conf_wait_threshold = conf_cfg.get('wait_threshold', 0.25)

    # ──────────────────────────────────────────────
    # 主入口
    # ──────────────────────────────────────────────

    def evaluate(
        self,
        trade_date: str,
        pullback_candidates: List[Dict],
        market_regime: str,
        market_score: float,
        risk_appetite: float,
        heat_score: float,
    ) -> PatternEngineResult:
        """评估所有回撤候选标的的历史模式

        Args:
            trade_date: 交易日
            pullback_candidates: 回撤候选列表
            market_regime: 当前市场状态
            market_score: 市场评分
            risk_appetite: 风险偏好
            heat_score: 热度

        Returns:
            PatternEngineResult (含Confidence + 冷启动信息)
        """
        result = PatternEngineResult(trade_date=trade_date)

        for pc in pullback_candidates:
            code = pc.get('ts_code', '')
            name = pc.get('name', '')
            theme = pc.get('theme', '')
            ret_60d = pc.get('ret_60d', 0.0)
            drawdown = pc.get('drawdown', 0.0)
            pb_ma = pc.get('pullback_ma', 'MA20')
            amount = pc.get('amount', 0.0)
            turnover = pc.get('turnover_rate', 0.0)
            leader_rank = pc.get('leader_rank')

            # V6.2: 分类模式类型（确保分桶查询不互相污染）
            ptype = classify_pattern_type(
                ret_60d=ret_60d, drawdown=drawdown,
                pullback_ma=pb_ma, leader_rank=leader_rank,
                amount=amount, turnover_rate=turnover,
            )

            # 第1步：查找历史相似案例（严格匹配 + 同pattern_type）
            stats = self._find_similar(
                market_regime=market_regime,
                pullback_ma=pb_ma,
                theme=theme,
                pattern_type=ptype,
                drawdown=drawdown,
                ret_60d=ret_60d,
            )

            match = PatternMatchResult(
                ts_code=code,
                name=name,
                theme=theme,
                pattern_type=ptype,
                n_samples=stats['n_samples'],
                win_probability=stats['win_probability'],
                avg_return_5d=stats['avg_return_5d'],
                avg_return_10d=stats['avg_return_10d'],
                avg_return_20d=stats['avg_return_20d'],
                median_return_10d=stats['median_return_10d'],
                avg_max_drawdown=stats['avg_max_drawdown'],
                avg_holding_days=stats['avg_holding_days'],
                avg_win_return=stats['avg_win_return'],
                avg_loss_return=stats['avg_loss_return'],
                # Confidence fields from DB
                confidence=stats.get('confidence', 0.0),
                sample_size_confidence=stats.get('sample_size_confidence', 0.0),
                recency_score=stats.get('recency_score', 0.0),
                match_quality=stats.get('match_quality', 1.0),
                recency_weighted_win_rate=stats.get('recency_weighted_win_rate', stats['win_probability']),
                match_conditions={
                    'market_regime': market_regime,
                    'pullback_ma': pb_ma,
                    'drawdown': drawdown,
                    'ret_60d': ret_60d,
                    'theme': theme,
                    'pattern_type': ptype,
                },
            )

            # 第2步：样本不足 → 放宽条件再试（同pattern_type, 去掉theme）
            if match.n_samples < self.min_samples:
                stats_loose = self._find_similar(
                    market_regime=market_regime,
                    pullback_ma=pb_ma,
                    theme=None,
                    pattern_type=ptype,
                    drawdown=drawdown,
                    ret_60d=ret_60d,
                )
                if stats_loose['n_samples'] >= self.min_samples:
                    match.n_samples = stats_loose['n_samples']
                    match.win_probability = stats_loose['win_probability']
                    match.avg_return_5d = stats_loose['avg_return_5d']
                    match.avg_return_10d = stats_loose['avg_return_10d']
                    match.avg_return_20d = stats_loose['avg_return_20d']
                    match.median_return_10d = stats_loose['median_return_10d']
                    match.avg_max_drawdown = stats_loose['avg_max_drawdown']
                    match.avg_holding_days = stats_loose['avg_holding_days']
                    match.avg_win_return = stats_loose['avg_win_return']
                    match.avg_loss_return = stats_loose['avg_loss_return']
                    # 也更新confidence
                    match.confidence = stats_loose.get('confidence', 0.0)
                    match.sample_size_confidence = stats_loose.get('sample_size_confidence', 0.0)
                    match.recency_score = stats_loose.get('recency_score', 0.0)
                    match.recency_weighted_win_rate = stats_loose.get('recency_weighted_win_rate', stats_loose['win_probability'])

            # 第3步：V6.1 冷启动处理
            if self.cold_start_enabled:
                self._apply_cold_start(match, ret_60d, drawdown, market_regime, pb_ma, amount, turnover)

            result.matches[code] = match

        # 统计
        n_total = len(result.matches)
        n_cold = sum(1 for m in result.matches.values() if m.cold_start_phase == 'cold')
        n_warm = sum(1 for m in result.matches.values() if m.cold_start_phase == 'warm')
        result.stats = {
            'n_total': n_total,
            'n_cold_start': n_cold,
            'n_warm_start': n_warm,
            'n_data_driven': n_total - n_cold - n_warm,
        }

        return result

    # ──────────────────────────────────────────────
    # 冷启动处理
    # ──────────────────────────────────────────────

    def _apply_cold_start(
        self,
        match: PatternMatchResult,
        ret_60d: float,
        drawdown: float,
        market_regime: str,
        pullback_ma: str,
        amount: float,
        turnover: float,
    ):
        """应用冷启动渐进混合策略

        Phase 1 - cold:    样本 < min_samples, 用启发式规则估计
        Phase 2 - warm:    样本 >= min_samples 但 < warmup_threshold, 混合
        Phase 3 - data_driven:  样本 >= warmup_threshold, 纯数据驱动
        """
        n = match.n_samples
        data_p = match.win_probability
        data_ret = match.avg_return_10d

        if n < self.min_samples:
            # ── Phase 1: 纯冷启动 ──
            heur_p, heur_ret, phase = estimate_heuristic_probability(
                ret_60d, drawdown, market_regime, pullback_ma, amount, turnover
            )
            match.cold_start_phase = 'cold'
            match.heuristic_probability = heur_p
            match.heuristic_avg_return = heur_ret
            match.data_probability = data_p
            match.blend_alpha = 0.0  # 纯启发式

            # 使用启发式概率 + 收益
            match.win_probability = heur_p
            match.avg_return_5d = heur_ret * 0.5
            match.avg_return_10d = heur_ret
            match.avg_return_20d = heur_ret * 1.5

            # 置信度很低
            match.confidence = min(match.confidence, 0.15)

        elif n < self.warmup_threshold:
            # ── Phase 2: 暖机阶段 — 平滑混合 ──
            heur_p, heur_ret, _ = estimate_heuristic_probability(
                ret_60d, drawdown, market_regime, pullback_ma, amount, turnover
            )
            # alpha 从 0 线性增长到 1
            alpha = (n - self.min_samples) / (self.warmup_threshold - self.min_samples)
            # heuristic_mix 从 heuristic_mix_min 衰减到 heuristic_mix_max
            heur_weight = self.heuristic_mix_min - alpha * (self.heuristic_mix_min - self.heuristic_mix_max)
            heur_weight = max(self.heuristic_mix_max, min(self.heuristic_mix_min, heur_weight))

            blended_p = heur_weight * heur_p + (1.0 - heur_weight) * data_p
            blended_ret = heur_weight * heur_ret + (1.0 - heur_weight) * data_ret

            match.cold_start_phase = 'warm'
            match.heuristic_probability = heur_p
            match.heuristic_avg_return = heur_ret
            match.data_probability = data_p
            match.blend_alpha = 1.0 - heur_weight

            match.win_probability = blended_p
            match.avg_return_5d = blended_ret * 0.5
            match.avg_return_10d = blended_ret
            match.avg_return_20d = blended_ret * 1.5

            # 信心随样本增多线性提升
            match.confidence = min(match.confidence, 0.3 + 0.5 * alpha)
            # 若DB未提供置信度，从混合进度估算
            if match.confidence < 0.1:
                match.confidence = 0.3 + 0.5 * alpha

        else:
            # ── Phase 3: 纯数据驱动 ──
            match.cold_start_phase = 'data_driven'
            match.heuristic_probability = data_p
            match.heuristic_avg_return = data_ret
            match.data_probability = data_p
            match.blend_alpha = 1.0
            # 若DB未提供置信度（如测试环境），从样本量估算
            if match.confidence < 0.1:
                n_conf = 1.0 / (1.0 + np.exp(-(n - 10) / 4.0))
                match.confidence = min(n_conf, 0.95)

    # ──────────────────────────────────────────────
    # 相似匹配核心
    # ──────────────────────────────────────────────

    def _find_similar(
        self,
        market_regime: str,
        pullback_ma: str,
        theme: Optional[str],
        pattern_type: str = None,
        drawdown: float = 0.0,
        ret_60d: float = 0.0,
    ) -> Dict:
        """查找历史相似案例的汇总统计（含Confidence + 分桶）"""
        return query_pattern_stats(
            market_regime=market_regime,
            pullback_ma=pullback_ma,
            theme=theme,
            pattern_type=pattern_type,
            drawdown_min=drawdown - self.drawdown_tolerance,
            drawdown_max=drawdown + self.drawdown_tolerance,
            ret_60d_min=ret_60d - self.ret_60d_tolerance,
            min_samples=self.min_samples,
        )

    # ──────────────────────────────────────────────
    # 写入模式数据库（V6.1 扩大样本空间）
    # ──────────────────────────────────────────────

    def save_pattern_records(
        self,
        trade_date: str,
        pullback_candidates: List[Dict],
        market_regime: str,
        market_score: float,
        risk_appetite: float,
        heat_score: float,
        smart_money_scores: Dict[str, float] = None,
        leading_stocks: List[Dict] = None,        # ← 新增：龙头股票
        cross_sectional_stocks: List[Dict] = None, # ← 新增：截面排名股票
    ):
        """将今日候选标的写入 pattern_history 数据库

        V6.1 扩展：除了回撤候选，还保存龙头 + 截面排名股票，
        大幅扩大样本空间，让pattern匹配有更多历史数据可查。

        Args:
            trade_date: 交易日
            pullback_candidates: 回撤候选列表
            market_regime: 市场状态
            market_score: 市场评分
            risk_appetite: 风险偏好
            heat_score: 热度
            smart_money_scores: 聪明钱评分
            leading_stocks: 龙头股票列表（leader_result.top_leaders）
            cross_sectional_stocks: 截面排名股票（cs_result.top_n）
        """
        records = []

        # 1) 回撤候选（entry_type = 'pullback'）
        for pc in pullback_candidates:
            code = pc.get('ts_code', '')
            sm_score = (smart_money_scores or {}).get(code, None)
            records.append(self._build_record(
                pc, trade_date, market_regime, market_score, risk_appetite, heat_score,
                sm_score, entry_type='pullback',
                cross_sectional_rank=pc.get('cross_sectional_rank'),
            ))

        # 2) 龙头股票（entry_type = 'leader'）
        seen_codes = set(r.get('ts_code', '') for r in records if r.get('ts_code'))
        if leading_stocks:
            for ls in leading_stocks:
                code = ls.get('ts_code', '')
                if code and code not in seen_codes:
                    sm_score = (smart_money_scores or {}).get(code, None)
                    records.append(self._build_record(
                        ls, trade_date, market_regime, market_score, risk_appetite, heat_score,
                        sm_score, entry_type='leader',
                        leader_rank=ls.get('rank', ls.get('leader_rank')),
                    ))
                    seen_codes.add(code)

        # 3) 截面排名股票（entry_type = 'cross_sectional'）
        if cross_sectional_stocks:
            for cs in cross_sectional_stocks:
                code = cs.get('ts_code', '')
                if code and code not in seen_codes:
                    sm_score = (smart_money_scores or {}).get(code, None)
                    # 尝试获取 rank 属性（StockAlpha 对象或 dict）
                    rank = cs.cross_sectional_rank if hasattr(cs, 'cross_sectional_rank') else cs.get('cross_sectional_rank', cs.get('rank'))
                    records.append(self._build_record(
                        cs, trade_date, market_regime, market_score, risk_appetite, heat_score,
                        sm_score, entry_type='cross_sectional',
                        cross_sectional_rank=rank,
                    ))
                    seen_codes.add(code)

        if not records:
            return 0

        batch_save_pattern_records(records)
        return len(records)

    # ──────────────────────────────────────────────
    # 构建单条记录
    # ──────────────────────────────────────────────

    def _build_record(
        self,
        item: Dict,
        trade_date: str,
        market_regime: str,
        market_score: float,
        risk_appetite: float,
        heat_score: float,
        smart_money_score: float = None,
        entry_type: str = 'pullback',
        leader_rank: int = None,
        cross_sectional_rank: int = None,
    ) -> Dict:
        """将候选标的转为DB记录格式（兼容dict/对象混合输入）"""
        def _get(k, default=None):
            v = item.get(k) if isinstance(item, dict) else getattr(item, k, default)
            return v if v is not None else default

        code = _get('ts_code', '')

        # V6.2: 自动分类pattern_type
        ptype = _get('pattern_type')
        if not ptype:
            ptype = classify_pattern_type(
                ret_60d=_get('ret_60d', 0.0),
                drawdown=_get('drawdown', _get('max_drawdown', 0.0)),
                pullback_ma=_get('pullback_ma', ''),
                leader_rank=_get('leader_rank'),
                amount=_get('amount', 0.0),
                turnover_rate=_get('turnover_rate', 0.0),
            )

        return {
            'ts_code': code,
            'trade_date': trade_date,
            'market_regime': market_regime,
            'market_score': market_score,
            'risk_appetite': risk_appetite,
            'heat_score': heat_score,
            'theme': _get('theme', ''),
            'theme_rank': _get('theme_rank'),
            'theme_strength': _get('theme_strength'),
            'pattern_type': ptype,
            'entry_type': entry_type,
            'leader_rank': leader_rank or _get('leader_rank'),
            'alpha_rank': _get('alpha_rank', _get('total_score')),
            'cross_sectional_rank': cross_sectional_rank,
            'ret_60d': _get('ret_60d', _get('ret_60d', 0.0)),
            'max_drawdown': _get('drawdown', _get('max_drawdown', 0.0)),
            'pullback_ma': _get('pullback_ma', ''),
            'dist_to_ma': _get('dist_to_ma', 0.0),
            'atr': _get('atr', 0.0),
            'turnover_rate': _get('turnover_rate', 0.0),
            'amount': _get('amount', 0.0),
            'smart_money_score': smart_money_score,
            'moneyflow': _get('moneyflow', 0.0),
            'volume_change': _get('volume_change', 0.0),
            'future_5_return': None,
            'future_10_return': None,
            'future_20_return': None,
            'future_max_drawdown': None,
            'holding_days': None,
            'success_flag': None,
        }

    # ──────────────────────────────────────────────
    # 工具
    # ──────────────────────────────────────────────

    def get_db_stats(self) -> Dict:
        """获取数据库统计信息"""
        counts = get_record_count()
        return {
            'db_path': PATTERN_DB_PATH,
            'pattern_records': counts.get('pattern_history', 0),
            'snapshot_records': counts.get('daily_feature_snapshot', 0),
            'factor_records': counts.get('factor_performance', 0),
        }


# ──────────────────────────────────────────────
# CLI 测试
# ──────────────────────────────────────────────

if __name__ == '__main__':
    import yaml
    config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.yaml')
    with open(config_path, 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)

    engine = HistoricalPatternEngine(cfg)
    print(f"[PatternEngine] DB路径: {PATTERN_DB_PATH}")
    print(f"[PatternEngine] 记录数: {engine.get_db_stats()}")

    # 测试查询
    stats = query_pattern_stats(market_regime='Recovery', pullback_ma='MA20')
    print(f"[PatternEngine] 测试查询(Recovery+MA20): {stats['n_samples']}条")

    # 测试启发式概率
    heur_p, heur_ret, phase = estimate_heuristic_probability(
        ret_60d=0.35, drawdown=0.08, market_regime='Bull', pullback_ma='MA20'
    )
    print(f"[PatternEngine] 启发式测试: P={heur_p:.1%} Ret={heur_ret:.1%} Phase={phase}")
