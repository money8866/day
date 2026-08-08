#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
A股短线动态仓位引擎 V9.0
Regime-Aware Adaptive Position Engine

七层市场状态模型 → 15种Regime → 动态仓位矩阵 → 多因子乘数 → 紧急制动 → 恢复阶梯
"""
import os
import sys
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timedelta

# ============================================================
# 数据模型
# ============================================================

@dataclass
class LayerScore:
    """单层评分输出"""
    score: float = 50.0          # 0~100
    direction: str = "neutral"    # bullish / neutral / bearish
    confidence: float = 0.5       # 0~1
    details: Dict = field(default_factory=dict)


@dataclass
class RegimeResult:
    """Regime 识别结果"""
    regime: str = "UNKNOWN"
    regime_cn: str = "未知"
    confidence: float = 0.0
    supporting_factors: List[str] = field(default_factory=list)
    contradicting_factors: List[str] = field(default_factory=list)
    score_breakdown: Dict = field(default_factory=dict)


@dataclass
class PositionResult:
    """仓位计算结果"""
    base_position: float = 0.0
    recommended_position: float = 0.0
    max_position: float = 0.0
    core_position: float = 0.0
    attack_position: float = 0.0
    trial_position: float = 0.0
    cash_position: float = 100.0
    position_confidence: float = 0.0
    positive_factors: List[str] = field(default_factory=list)
    negative_factors: List[str] = field(default_factory=list)
    upgrade_conditions: List[str] = field(default_factory=list)
    downgrade_conditions: List[str] = field(default_factory=list)
    emergency_brake: str = "NONE"
    operation_mode: str = "Normal"


@dataclass
class MarketDataBundle:
    """输入数据捆绑（所有计算所需的原始数据）"""
    # 指数数据 (name -> DataFrame)
    index_data: Dict[str, pd.DataFrame] = field(default_factory=dict)
    # 市场概况
    overview: Dict = field(default_factory=dict)
    # 涨跌停统计
    limit_stats: Dict = field(default_factory=dict)
    # 连板高度
    max_limit_height: int = 0
    # 主题 TOP3 分数
    theme_top3_scores: List[float] = field(default_factory=list)
    # 主题主线信息（可选）
    theme_info: Dict = field(default_factory=dict)
    # 历史数据
    hist_overall: Optional[pd.DataFrame] = None
    hist_limit: Optional[pd.DataFrame] = None
    # 前一日引擎结果（用于状态迁移和滞回）
    prev_result: Optional["EngineV9Result"] = None
    # 组合最大回撤（可选）
    portfolio_drawdown: float = 0.0


@dataclass
class EngineV9Result:
    """V9 引擎完整输出"""
    trade_date: str = ""
    # 七层评分
    l1_index: LayerScore = field(default_factory=LayerScore)
    l2_breadth: LayerScore = field(default_factory=LayerScore)
    l3_momentum: LayerScore = field(default_factory=LayerScore)
    l4_sentiment: LayerScore = field(default_factory=LayerScore)
    l5_theme: LayerScore = field(default_factory=LayerScore)
    l6_liquidity: LayerScore = field(default_factory=LayerScore)
    l7_risk: LayerScore = field(default_factory=LayerScore)
    # 核心指标
    stle: float = 50.0       # Short-Term Trading Environment
    tes: float = 50.0        # Trade Environment Score
    position_score: float = 50.0
    # Regime
    regime: RegimeResult = field(default_factory=RegimeResult)
    prev_regime: str = "UNKNOWN"
    transition: str = "STABLE"
    transition_confidence: float = 0.0
    # 仓位
    position: PositionResult = field(default_factory=PositionResult)
    # 数据质量
    data_quality: float = 100.0
    # 原始数据引用
    data_bundle: Optional[MarketDataBundle] = None


# ============================================================
# 工具函数
# ============================================================

def _clip(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))

def _safe_div(a, b):
    if b is None or b == 0:
        return 0.0
    return a / b

def _percentile_rank(series: pd.Series, value: float) -> float:
    """计算 value 在 series 中的百分位 (0~100)"""
    if series is None or len(series) < 5:
        return 50.0
    s = series.dropna()
    if len(s) < 3:
        return 50.0
    rank = (s < value).sum() / len(s) * 100
    return float(rank)

def _calc_ma_slope(close_series: pd.Series, period: int, lookback: int = 5) -> float:
    """计算 MA 斜率（百分比）"""
    if len(close_series) < period + lookback:
        return 0.0
    ma = close_series.rolling(period).mean()
    current = ma.iloc[-1]
    prev = ma.iloc[-1 - lookback]
    if prev is None or prev == 0 or pd.isna(prev):
        return 0.0
    return (current - prev) / prev * 100.0


# ============================================================
# L1: 指数趋势模型
# ============================================================

INDEX_LIST_V9 = [
    ("上证指数", "000001.SH"),
    ("沪深300", "000300.SH"),
    ("中证1000", "000852.SH"),
    ("中证2000", "932000.CSI"),
    ("创业板指", "399006.SZ"),
    ("科创50", "000688.SH"),
]

def calc_ma_scores(df: pd.DataFrame) -> Dict[str, float]:
    """计算单指数的多周期MA得分"""
    if df is None or len(df) < 60:
        return {"total": 50.0, "ma5": 50, "ma10": 50, "ma20": 50, "ma60": 50, "ma120": 50,
                "ma20_slope": 0, "ma60_slope": 0, "dist_ma20": 0, "dist_ma60": 0}
    
    close = df['close'].astype(float)
    latest = close.iloc[-1]
    
    ma5 = close.rolling(5).mean().iloc[-1]
    ma10 = close.rolling(10).mean().iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1]
    ma60 = close.rolling(60).mean().iloc[-1]
    ma120 = close.rolling(120).mean().iloc[-1] if len(close) >= 120 else ma60
    ma250 = close.rolling(250).mean().iloc[-1] if len(close) >= 250 else ma120
    
    ma20_slope = _calc_ma_slope(close, 20, 5)
    ma60_slope = _calc_ma_slope(close, 60, 10)
    
    dist_ma20 = (latest - ma20) / ma20 * 100 if ma20 > 0 else 0
    dist_ma60 = (latest - ma60) / ma60 * 100 if ma60 > 0 else 0
    
    # 均线排列评分
    ma_score = 0.0
    if latest > ma5 > ma10 > ma20 > ma60:
        ma_score = 95  # 完全多头
    elif ma5 > ma10 > ma20 > ma60:
        ma_score = 85
    elif ma5 > ma10 > ma20:
        ma_score = 70
    elif latest > ma20 and ma20_slope > 0:
        ma_score = 55
    elif latest > ma20:
        ma_score = 45
    elif latest > ma60:
        ma_score = 35
    elif ma5 < ma10 < ma20 < ma60:
        ma_score = 10  # 完全空头
    elif ma5 < ma10 < ma20:
        ma_score = 20
    else:
        ma_score = 30
    
    # 斜率修正
    if ma20_slope > 1.0:
        ma_score += 8
    elif ma20_slope > 0.3:
        ma_score += 4
    elif ma20_slope < -1.0:
        ma_score -= 10
    elif ma20_slope < -0.3:
        ma_score -= 5
    
    # 动量加速 (5日涨速 vs 20日涨速)
    ret5 = close.pct_change(5).iloc[-1] * 100 if len(close) > 5 else 0
    ret20 = close.pct_change(20).iloc[-1] * 100 if len(close) > 20 else 0
    accel = ret5 - ret20 / 4  # 归一化比较
    if accel > 0.5:
        ma_score += 3  # 加速
    elif accel < -0.5:
        ma_score -= 3  # 减速
    
    ma_score = _clip(ma_score)
    
    # N日收益率 (用于回撤)
    ret1 = close.pct_change(1).iloc[-1] * 100 if len(close) > 1 else 0
    ret3 = close.pct_change(3).iloc[-1] * 100 if len(close) > 3 else 0
    ret5_v = close.pct_change(5).iloc[-1] * 100 if len(close) > 5 else 0
    
    # 最大回撤 (20日)
    if len(close) >= 20:
        roll_max = close.tail(20).cummax()
        dd_20 = ((close.tail(20) - roll_max) / roll_max * 100).min()
    else:
        dd_20 = 0.0
    
    return {
        "total": ma_score,
        "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60,
        "ma120": ma120, "ma250": ma250,
        "ma20_slope": round(ma20_slope, 3),
        "ma60_slope": round(ma60_slope, 3),
        "dist_ma20": round(dist_ma20, 2),
        "dist_ma60": round(dist_ma60, 2),
        "ret1": round(ret1, 2),
        "ret3": round(ret3, 2),
        "ret5": round(ret5_v, 2),
        "ret20": round(ret20, 2),
        "dd_20": round(dd_20, 2),
    }


def calc_l1_index_trend(data: MarketDataBundle) -> LayerScore:
    """
    L1 指数趋势层
    综合6大指数的趋势得分，加权：上证0.2 / 沪深300 0.2 / 中证1000 0.2 / 中证2000 0.2 / 创业板 0.1 / 科创50 0.1
    """
    index_scores = {}
    total_score = 0.0
    weights = {
        "上证指数": 0.20,
        "沪深300": 0.20,
        "中证1000": 0.20,
        "中证2000": 0.20,
        "创业板指": 0.10,
        "科创50": 0.10,
    }
    
    ma20_slopes = []
    ma60_slopes = []
    ret1_list = []
    ret3_list = []
    
    for name, _ in INDEX_LIST_V9:
        df = data.index_data.get(name)
        if df is None or df.empty:
            index_scores[name] = 50.0
            continue
        ms = calc_ma_scores(df)
        index_scores[name] = ms["total"]
        ma20_slopes.append(ms["ma20_slope"])
        ma60_slopes.append(ms["ma60_slope"])
        ret1_list.append(ms["ret1"])
        ret3_list.append(ms["ret3"])
        w = weights.get(name, 0.1)
        total_score += ms["total"] * w
    
    # 计算置信度（数据完整度）
    valid_count = sum(1 for v in index_scores.values() if v != 50.0 or data.index_data.get(name) is not None)
    confidence = min(1.0, valid_count / 6.0)
    
    # 方向判定
    avg_ma20_slope = np.mean(ma20_slopes) if ma20_slopes else 0
    if total_score >= 65 and avg_ma20_slope > 0:
        direction = "bullish"
    elif total_score <= 35 and avg_ma20_slope < 0:
        direction = "bearish"
    else:
        direction = "neutral"
    
    details = {
        "index_scores": index_scores,
        "avg_ma20_slope": round(avg_ma20_slope, 3),
        "avg_ma60_slope": round(np.mean(ma60_slopes), 3) if ma60_slopes else 0,
        "avg_ret1": round(np.mean(ret1_list), 2) if ret1_list else 0,
        "avg_ret3": round(np.mean(ret3_list), 2) if ret3_list else 0,
    }
    
    return LayerScore(
        score=_clip(total_score),
        direction=direction,
        confidence=confidence,
        details=details
    )


# ============================================================
# L2: 市场宽度模型
# ============================================================

def calc_l2_breadth(data: MarketDataBundle, hist_limit: Optional[pd.DataFrame] = None) -> LayerScore:
    """
    L2 市场宽度层
    指标：上涨比例、涨跌比、涨停跌停比、创新高新低比、Breadth扩张/收缩
    使用历史百分位标准化
    """
    overview = data.overview
    limit_stats = data.limit_stats
    
    up_count = limit_stats.get('up_count', overview.get('up_count', 0))
    down_count = limit_stats.get('down_count', overview.get('down_count', 0))
    total = up_count + down_count
    
    zt_count = limit_stats.get('zt_count', overview.get('zt_count', 0))
    dt_count = limit_stats.get('dt_count', overview.get('dt_count', 0))
    zb_count = limit_stats.get('zhaban_count', overview.get('zb_count', 0))
    
    # 1. 上涨比例 (UpDownRatio)
    up_ratio = up_count / total * 100 if total > 0 else 50
    
    # 2. 涨跌比 (AdvanceDeclineRatio)
    ad_ratio = up_count / down_count if down_count > 0 else 10.0
    
    # 3. 涨停跌停比 (LimitUpLimitDownRatio)
    zt_dt_ratio = zt_count / max(1, dt_count)
    
    # 4. 涨停触及率 (LimitUpHitRate)
    hit_rate = zt_count / total * 100 if total > 0 else 0
    
    # 5. 跌停率 (LimitDownRate)
    dt_rate = dt_count / total * 100 if total > 0 else 0
    
    # 使用固定阈值评分（后续可替换为历史百分位）
    # === 上涨比例评分 (25分) ===
    if up_ratio >= 70:
        up_score = 25
    elif up_ratio >= 60:
        up_score = 22
    elif up_ratio >= 55:
        up_score = 18
    elif up_ratio >= 50:
        up_score = 14
    elif up_ratio >= 40:
        up_score = 8
    elif up_ratio >= 30:
        up_score = 4
    else:
        up_score = 0
    
    # === 涨跌比评分 (20分) ===
    if ad_ratio >= 3.0:
        ad_score = 20
    elif ad_ratio >= 2.0:
        ad_score = 16
    elif ad_ratio >= 1.5:
        ad_score = 12
    elif ad_ratio >= 1.0:
        ad_score = 8
    elif ad_ratio >= 0.7:
        ad_score = 4
    elif ad_ratio >= 0.5:
        ad_score = 2
    else:
        ad_score = 0
    
    # === 涨跌停结构评分 (20分) ===
    if zt_dt_ratio >= 10 and dt_count <= 5:
        ztdt_score = 20
    elif zt_dt_ratio >= 5:
        ztdt_score = 16
    elif zt_dt_ratio >= 2:
        ztdt_score = 12
    elif zt_dt_ratio >= 1:
        ztdt_score = 8
    elif zt_dt_ratio >= 0.5:
        ztdt_score = 4
    else:
        ztdt_score = 0
    
    # === 涨停强度 (20分) ===
    zt_total = zt_count + zb_count
    close_rate = zt_count / zt_total * 100 if zt_total > 0 else 50
    if zt_count >= 80 and close_rate > 70:
        zt_strength = 20
    elif zt_count >= 60:
        zt_strength = 16
    elif zt_count >= 40:
        zt_strength = 12
    elif zt_count >= 25:
        zt_strength = 8
    elif zt_count >= 15:
        zt_strength = 4
    else:
        zt_strength = 0
    
    # === 跌停压力逆评分 (15分) ===
    if dt_count == 0:
        dt_inverse = 15
    elif dt_count <= 3:
        dt_inverse = 12
    elif dt_count <= 8:
        dt_inverse = 8
    elif dt_count <= 15:
        dt_inverse = 4
    elif dt_count <= 30:
        dt_inverse = 2
    else:
        dt_inverse = 0
    
    total_breadth = up_score + ad_score + ztdt_score + zt_strength + dt_inverse
    total_breadth = _clip(total_breadth)
    
    # 方向
    if total_breadth >= 65:
        direction = "bullish"
    elif total_breadth <= 35:
        direction = "bearish"
    else:
        direction = "neutral"
    
    # 广度扩张/收缩 (与昨日对比)
    breadth_change = 0
    if hist_limit is not None and len(hist_limit) >= 2:
        prev_up_ratio = hist_limit.iloc[-2].get('up_ratio', up_ratio)
        breadth_change = up_ratio - prev_up_ratio
    
    details = {
        "up_ratio": round(up_ratio, 1),
        "up_count": up_count,
        "down_count": down_count,
        "zt_count": zt_count,
        "dt_count": dt_count,
        "ad_ratio": round(ad_ratio, 2),
        "zt_dt_ratio": round(zt_dt_ratio, 2),
        "hit_rate": round(hit_rate, 3),
        "dt_rate": round(dt_rate, 3),
        "close_rate": round(close_rate, 1),
        "breadth_change": round(breadth_change, 1),
        "component_scores": {
            "up_ratio": up_score,
            "ad_ratio": ad_score,
            "zt_dt": ztdt_score,
            "zt_strength": zt_strength,
            "dt_inverse": dt_inverse,
        }
    }
    
    confidence = 0.9 if total > 2000 else 0.7 if total > 1000 else 0.5
    
    return LayerScore(
        score=total_breadth,
        direction=direction,
        confidence=confidence,
        details=details
    )


# ============================================================
# L3: 短线 Momentum
# ============================================================

def calc_l3_momentum(data: MarketDataBundle, hist_limit: Optional[pd.DataFrame] = None) -> LayerScore:
    """
    L3 短线动量层
    关注：1/3/5日涨跌、涨速、涨停增加速度、连板高度、强势股持续率
    识别：加速 / 正常 / 钝化 / 衰退 / 急杀 / 修复
    """
    overview = data.overview
    limit_stats = data.limit_stats
    zt_count = limit_stats.get('zt_count', overview.get('zt_count', 0))
    dt_count = limit_stats.get('dt_count', overview.get('dt_count', 0))
    max_height = data.max_limit_height
    
    # 各指数多日收益
    ret1_list = []
    ret3_list = []
    ret5_list = []
    for name, _ in INDEX_LIST_V9:
        df = data.index_data.get(name)
        if df is None or len(df) < 10:
            continue
        close = df['close'].astype(float)
        if len(close) > 1:
            ret1_list.append(close.pct_change(1).iloc[-1] * 100)
        if len(close) > 3:
            ret3_list.append(close.pct_change(3).iloc[-1] * 100)
        if len(close) > 5:
            ret5_list.append(close.pct_change(5).iloc[-1] * 100)
    
    avg_ret1 = np.mean(ret1_list) if ret1_list else 0
    avg_ret3 = np.mean(ret3_list) if ret3_list else 0
    avg_ret5 = np.mean(ret5_list) if ret5_list else 0
    
    # 1. 短期动量评分 (30分) - 基于1日和3日收益
    mom_score = 0
    if avg_ret1 >= 2:
        mom_score += 15
    elif avg_ret1 >= 1:
        mom_score += 12
    elif avg_ret1 >= 0.5:
        mom_score += 9
    elif avg_ret1 >= 0:
        mom_score += 6
    elif avg_ret1 >= -0.5:
        mom_score += 3
    elif avg_ret1 >= -1.5:
        mom_score += 1
    else:
        mom_score += 0
    
    if avg_ret3 >= 4:
        mom_score += 15
    elif avg_ret3 >= 2:
        mom_score += 12
    elif avg_ret3 >= 1:
        mom_score += 9
    elif avg_ret3 >= 0:
        mom_score += 6
    elif avg_ret3 >= -2:
        mom_score += 3
    else:
        mom_score += 0
    
    # 2. 动量加速 (20分) - 5日 vs 20日 归一化
    accel_score = 10  # 默认中性
    if len(ret5_list) > 0 and len(ret3_list) > 0:
        # 用 1日 - 3日/3 作为加速度近似
        accel = avg_ret1 - avg_ret3 / 3
        if accel > 1.0:
            accel_score = 20  # 强加速
        elif accel > 0.3:
            accel_score = 16  # 温和加速
        elif accel > 0:
            accel_score = 12  # 微加速
        elif accel > -0.3:
            accel_score = 8   # 微减速
        elif accel > -1.0:
            accel_score = 4   # 明显减速
        else:
            accel_score = 0   # 急刹车
    
    # 3. 连板高度 (20分)
    if max_height >= 10:
        height_score = 20
    elif max_height >= 7:
        height_score = 17
    elif max_height >= 5:
        height_score = 14
    elif max_height >= 4:
        height_score = 11
    elif max_height >= 3:
        height_score = 8
    elif max_height >= 2:
        height_score = 5
    else:
        height_score = 2
    
    # 4. 涨停变化速度 (20分) - 需要历史数据
    zt_change_score = 10  # 默认中性
    if hist_limit is not None and len(hist_limit) >= 3:
        zt_list = hist_limit['zt_count'].tail(5).tolist() if 'zt_count' in hist_limit.columns else []
        if len(zt_list) >= 3:
            zt_today = zt_count
            zt_3d_ago = zt_list[-3] if len(zt_list) >= 3 else zt_count
            zt_change = (zt_today - zt_3d_ago) / max(1, zt_3d_ago) * 100
            if zt_change > 50:
                zt_change_score = 20
            elif zt_change > 20:
                zt_change_score = 16
            elif zt_change > 5:
                zt_change_score = 13
            elif zt_change > -10:
                zt_change_score = 10
            elif zt_change > -30:
                zt_change_score = 6
            elif zt_change > -50:
                zt_change_score = 3
            else:
                zt_change_score = 0
    
    # 5. 强势股持续性 (10分)
    persistence_score = 5
    if hist_limit is not None and len(hist_limit) >= 5:
        zt_series = hist_limit['zt_count'].tail(5) if 'zt_count' in hist_limit.columns else pd.Series([zt_count]*5)
        if len(zt_series) >= 3:
            trend = zt_series.diff().dropna()
            pos_days = (trend > 0).sum()
            if pos_days >= 3:
                persistence_score = 10
            elif pos_days >= 2:
                persistence_score = 7
            elif pos_days >= 1:
                persistence_score = 5
            else:
                persistence_score = 2
    
    total_mom = mom_score + accel_score + height_score + zt_change_score + persistence_score
    total_mom = _clip(total_mom)
    
    # 动量状态判定
    if total_mom >= 75 and accel_score >= 12:
        mom_state = "加速"
    elif total_mom >= 65:
        mom_state = "正常"
    elif 45 <= total_mom < 65 and accel_score < 8:
        mom_state = "钝化"
    elif 30 <= total_mom < 50 and accel_score < 5:
        mom_state = "衰退"
    elif total_mom < 30 and accel_score < 4:
        mom_state = "急杀"
    elif total_mom >= 40 and accel_score >= 10:
        mom_state = "修复"
    else:
        mom_state = "中性"
    
    if total_mom >= 60:
        direction = "bullish"
    elif total_mom <= 40:
        direction = "bearish"
    else:
        direction = "neutral"
    
    details = {
        "avg_ret1": round(avg_ret1, 2),
        "avg_ret3": round(avg_ret3, 2),
        "avg_ret5": round(avg_ret5, 2),
        "max_height": max_height,
        "zt_count": zt_count,
        "mom_state": mom_state,
        "component_scores": {
            "momentum": mom_score,
            "acceleration": accel_score,
            "board_height": height_score,
            "zt_change": zt_change_score,
            "persistence": persistence_score,
        }
    }
    
    confidence = 0.85 if ret1_list else 0.6
    
    return LayerScore(
        score=total_mom,
        direction=direction,
        confidence=confidence,
        details=details
    )


# ============================================================
# L4: 短线情绪模型 (STSS)
# ============================================================

def calc_l4_sentiment(data: MarketDataBundle, hist_limit: Optional[pd.DataFrame] = None) -> LayerScore:
    """
    L4 短线情绪层 Short-Term Sentiment Score (STSS)
    
    STSS = 
    20% × LimitUpScore
    15% × LimitDownInverse
    15% × BoardHeight
    15% × PromotionRate (连板晋级率/炸板率反向)
    10% × YesterdayLimitUpPerformance
    10% × HighLevelPremium
    10% × BrokenBoardInverse
    5% × SentimentAcceleration
    """
    overview = data.overview
    limit_stats = data.limit_stats
    
    zt_count = limit_stats.get('zt_count', overview.get('zt_count', 0))
    dt_count = limit_stats.get('dt_count', overview.get('dt_count', 0))
    zb_count = limit_stats.get('zhaban_count', overview.get('zb_count', 0))
    broken_rate = limit_stats.get('broken_rate', overview.get('zb_rate', 0))
    max_height = data.max_limit_height
    
    total_touch = zt_count + zb_count
    
    # 1. 涨停数量分 (20分)
    if zt_count >= 100:
        lu_score = 20
    elif zt_count >= 80:
        lu_score = 18
    elif zt_count >= 60:
        lu_score = 15
    elif zt_count >= 40:
        lu_score = 12
    elif zt_count >= 25:
        lu_score = 9
    elif zt_count >= 15:
        lu_score = 6
    elif zt_count >= 8:
        lu_score = 3
    else:
        lu_score = 0
    
    # 2. 跌停逆评分 (15分)
    if dt_count == 0:
        ld_inv = 15
    elif dt_count <= 3:
        ld_inv = 12
    elif dt_count <= 8:
        ld_inv = 9
    elif dt_count <= 15:
        ld_inv = 6
    elif dt_count <= 30:
        ld_inv = 3
    elif dt_count <= 50:
        ld_inv = 1
    else:
        ld_inv = 0
    
    # 3. 连板高度 (15分)
    if max_height >= 10:
        bh_score = 15
    elif max_height >= 8:
        bh_score = 13
    elif max_height >= 6:
        bh_score = 11
    elif max_height >= 5:
        bh_score = 9
    elif max_height >= 4:
        bh_score = 7
    elif max_height >= 3:
        bh_score = 5
    elif max_height >= 2:
        bh_score = 3
    else:
        bh_score = 1
    
    # 4. 晋级率 (15分) - 用 1-炸板率 代替
    promotion_rate = (100 - broken_rate) if total_touch > 0 else 50
    if promotion_rate >= 85:
        promo_score = 15
    elif promotion_rate >= 75:
        promo_score = 12
    elif promotion_rate >= 65:
        promo_score = 9
    elif promotion_rate >= 55:
        promo_score = 6
    elif promotion_rate >= 45:
        promo_score = 3
    else:
        promo_score = 0
    
    # 5. 昨日涨停今日表现 (10分) - 近似用连板数量/昨日涨停数
    yest_perf_score = 5  # 默认中性
    if hist_limit is not None and len(hist_limit) >= 2:
        prev_zt = hist_limit.iloc[-2].get('zt_count', zt_count) if 'zt_count' in hist_limit.columns else zt_count
        # 用今日连板数近似昨日涨停晋级
        lb_estimate = min(zt_count * 0.3, max_height * 3)  # 粗略估计
        ratio = lb_estimate / max(1, prev_zt) * 100
        if ratio >= 40:
            yest_perf_score = 10
        elif ratio >= 25:
            yest_perf_score = 8
        elif ratio >= 15:
            yest_perf_score = 6
        elif ratio >= 8:
            yest_perf_score = 4
        else:
            yest_perf_score = 2
    
    # 6. 高标溢价 (10分) - 用连板高度和涨停数的组合
    high_premium = 5
    if max_height >= 5 and zt_count >= 30:
        high_premium = 10
    elif max_height >= 4 and zt_count >= 20:
        high_premium = 8
    elif max_height >= 3 and zt_count >= 15:
        high_premium = 6
    elif max_height >= 2:
        high_premium = 4
    else:
        high_premium = 2
    
    # 7. 炸板率反向 (10分)
    if broken_rate <= 10:
        bb_inv = 10
    elif broken_rate <= 20:
        bb_inv = 8
    elif broken_rate <= 30:
        bb_inv = 6
    elif broken_rate <= 40:
        bb_inv = 4
    elif broken_rate <= 50:
        bb_inv = 2
    else:
        bb_inv = 0
    
    # 8. 情绪加速度 (5分)
    accel_score = 2.5
    if hist_limit is not None and len(hist_limit) >= 3:
        # 计算情绪变化速度
        zt_prev = hist_limit.iloc[-2].get('zt_count', zt_count) if 'zt_count' in hist_limit.columns else zt_count
        dt_prev = hist_limit.iloc[-2].get('dt_count', dt_count) if 'dt_count' in hist_limit.columns else dt_count
        zt_change = zt_count - zt_prev
        dt_change = dt_count - dt_prev
        
        if zt_change > 15 and dt_change < 0:
            accel_score = 5
        elif zt_change > 5 and dt_change <= 0:
            accel_score = 4
        elif zt_change >= 0 and dt_change <= 3:
            accel_score = 3
        elif zt_change < -10 or dt_change > 10:
            accel_score = 0
        elif zt_change < -5 or dt_change > 5:
            accel_score = 1
        else:
            accel_score = 2.5
    
    total_stss = (lu_score + ld_inv + bh_score + promo_score + 
                  yest_perf_score + high_premium + bb_inv + accel_score)
    total_stss = _clip(total_stss)
    
    # 情绪状态
    if total_stss >= 80:
        sentiment_state = "情绪高涨"
        direction = "bullish"
    elif total_stss >= 65:
        sentiment_state = "情绪温和"
        direction = "bullish"
    elif total_stss >= 50:
        sentiment_state = "情绪中性"
        direction = "neutral"
    elif total_stss >= 35:
        sentiment_state = "情绪偏弱"
        direction = "bearish"
    elif total_stss >= 20:
        sentiment_state = "情绪退潮"
        direction = "bearish"
    else:
        sentiment_state = "情绪冰点"
        direction = "bearish"
    
    details = {
        "zt_count": zt_count,
        "dt_count": dt_count,
        "zb_count": zb_count,
        "broken_rate": round(broken_rate, 1),
        "max_height": max_height,
        "promotion_rate": round(promotion_rate, 1),
        "sentiment_state": sentiment_state,
        "component_scores": {
            "limit_up": lu_score,
            "limit_down_inv": ld_inv,
            "board_height": bh_score,
            "promotion": promo_score,
            "yesterday_perf": yest_perf_score,
            "high_premium": high_premium,
            "broken_board_inv": bb_inv,
            "acceleration": accel_score,
        }
    }
    
    confidence = 0.9 if total_touch > 0 else 0.7
    
    return LayerScore(
        score=total_stss,
        direction=direction,
        confidence=confidence,
        details=details
    )


# ============================================================
# L5: 主题 / 主线环境
# ============================================================

def calc_l5_theme(data: MarketDataBundle) -> LayerScore:
    """
    L5 主题/主线层
    复用现有主题系统输出，判断主线强度和状态
    """
    theme_scores = data.theme_top3_scores or []
    theme_info = data.theme_info or {}
    
    # TOP1, TOP3 平均分
    top1 = theme_scores[0] if len(theme_scores) >= 1 else 50
    top3_avg = sum(theme_scores[:3]) / min(3, len(theme_scores)) if theme_scores else 50
    
    # 主线集中度 (Top1 - Top3 差距)
    if len(theme_scores) >= 3:
        concentration = theme_scores[0] - theme_scores[2]
    elif len(theme_scores) >= 2:
        concentration = theme_scores[0] - theme_scores[1]
    else:
        concentration = 0
    
    # 龙头强度
    leader_strength = theme_info.get('leader_strength', 
                                      70 if top1 >= 75 else 50 if top1 >= 60 else 30)
    
    # 主线状态判定
    # 如果 theme_info 中显式指定了 mainline_state，优先使用（来自外部主题引擎）
    explicit_mainline = theme_info.get('mainline_state', None)
    if explicit_mainline and explicit_mainline in ("STRONG_SINGLE", "STRONG_MULTI", "ROTATION", "WEAK", "NONE"):
        mainline_state = explicit_mainline
        # 基于显式状态给出 state_score
        state_map = {
            "STRONG_SINGLE": 90,
            "STRONG_MULTI": 78,
            "ROTATION": 65,
            "WEAK": 50,
            "NONE": 30,
        }
        state_score = state_map.get(mainline_state, 50)
    else:
        # 自动判定
        if top1 >= 80 and top3_avg >= 70 and concentration >= 10:
            mainline_state = "STRONG_SINGLE"
            state_score = 90
        elif top1 >= 75 and top3_avg >= 65:
            mainline_state = "STRONG_MULTI"
            state_score = 78
        elif top1 >= 70 and top3_avg >= 60 and concentration >= 5:
            mainline_state = "ROTATION"
            state_score = 65
        elif top1 >= 60:
            mainline_state = "WEAK"
            state_score = 50
        else:
            mainline_state = "NONE"
            state_score = 30
    
    # 综合评分
    # 60% × Top1强度 + 20% × Top3均值 + 10% × 集中度 + 10% × 龙头强度
    total_theme = (top1 * 0.55 + top3_avg * 0.20 + 
                   min(concentration, 30) / 30 * 10 +  # 集中度最高10分
                   leader_strength * 0.15)
    
    # 显式主线状态加成
    if explicit_mainline == "STRONG_SINGLE":
        total_theme = max(total_theme, 75)
    elif explicit_mainline == "STRONG_MULTI":
        total_theme = max(total_theme, 65)
    
    total_theme = _clip(total_theme)
    
    if total_theme >= 70:
        direction = "bullish"
    elif total_theme <= 40:
        direction = "bearish"
    else:
        direction = "neutral"
    
    details = {
        "top1_score": round(top1, 1),
        "top3_avg": round(top3_avg, 1),
        "concentration": round(concentration, 1),
        "leader_strength": leader_strength,
        "mainline_state": mainline_state,
        "state_score": state_score,
        "theme_count": len(theme_scores),
    }
    
    confidence = 0.9 if len(theme_scores) >= 3 else 0.7 if theme_scores else 0.5
    
    return LayerScore(
        score=total_theme,
        direction=direction,
        confidence=confidence,
        details=details
    )


# ============================================================
# L6: 流动性模型
# ============================================================

def calc_l6_liquidity(data: MarketDataBundle) -> LayerScore:
    """
    L6 流动性层
    结合成交额 + 涨跌方向判断流动性质量
    """
    overview = data.overview
    total_amount = overview.get('total_amount', 0)
    
    # 计算各指数成交额变化
    amount_5d_avg = 0
    amount_20d_avg = 0
    first_idx = None
    for name, _ in INDEX_LIST_V9:
        df = data.index_data.get(name)
        if df is not None and 'amount' in df.columns and len(df) >= 20:
            amt = df['amount'].astype(float)
            amount_5d_avg = amt.tail(5).mean()
            amount_20d_avg = amt.tail(20).mean()
            first_idx = name
            break
    
    amount_ratio = amount_5d_avg / amount_20d_avg if amount_20d_avg > 0 else 1.0
    
    # 判断涨跌方向
    avg_ret1 = 0
    ret_list = []
    for name, _ in INDEX_LIST_V9:
        df = data.index_data.get(name)
        if df is not None and len(df) > 1:
            ret_list.append(df['close'].astype(float).pct_change(1).iloc[-1] * 100)
    if ret_list:
        avg_ret1 = np.mean(ret_list)
    
    up_count = data.limit_stats.get('up_count', overview.get('up_count', 0))
    down_count = data.limit_stats.get('down_count', overview.get('down_count', 0))
    is_up_day = up_count > down_count
    
    zt_count = data.limit_stats.get('zt_count', overview.get('zt_count', 0))
    dt_count = data.limit_stats.get('dt_count', overview.get('dt_count', 0))
    
    # 流动性类型判定
    if amount_ratio > 1.2 and is_up_day and zt_count > dt_count:
        liq_type = "正向放量"
        type_score = 90
    elif amount_ratio > 1.2 and not is_up_day and dt_count > zt_count:
        liq_type = "恐慌放量"
        type_score = 30
    elif amount_ratio > 1.15 and is_up_day:
        liq_type = "温和放量上涨"
        type_score = 80
    elif 0.9 <= amount_ratio <= 1.1 and is_up_day:
        liq_type = "健康量能"
        type_score = 70
    elif amount_ratio < 0.85 and is_up_day:
        liq_type = "缩量上涨"
        type_score = 55
    elif amount_ratio < 0.8 and not is_up_day:
        liq_type = "缩量调整"
        type_score = 45
    elif 0.9 <= amount_ratio <= 1.1 and not is_up_day:
        liq_type = "平量调整"
        type_score = 40
    elif amount_ratio > 1.2 and not is_up_day:
        liq_type = "放量下跌"
        type_score = 25
    else:
        liq_type = "中性"
        type_score = 50
    
    # 绝对成交额评分 (万亿市场基准)
    if total_amount >= 15000:  # 1.5万亿+
        abs_score = 95
    elif total_amount >= 12000:
        abs_score = 85
    elif total_amount >= 10000:
        abs_score = 75
    elif total_amount >= 8000:
        abs_score = 60
    elif total_amount >= 6000:
        abs_score = 45
    elif total_amount >= 4000:
        abs_score = 30
    else:
        abs_score = 20
    
    # 综合：60% 类型 + 40% 绝对量
    total_liq = type_score * 0.6 + abs_score * 0.4
    total_liq = _clip(total_liq)
    
    if total_liq >= 65:
        direction = "bullish"
    elif total_liq <= 35:
        direction = "bearish"
    else:
        direction = "neutral"
    
    details = {
        "total_amount": total_amount,
        "amount_ratio_5_20": round(amount_ratio, 2),
        "liquidity_type": liq_type,
        "type_score": type_score,
        "abs_score": abs_score,
        "is_up_day": is_up_day,
        "avg_ret1": round(avg_ret1, 2),
    }
    
    confidence = 0.85 if total_amount > 0 else 0.5
    
    return LayerScore(
        score=total_liq,
        direction=direction,
        confidence=confidence,
        details=details
    )


# ============================================================
# L7: 风险状态模型
# ============================================================

def calc_l7_risk(data: MarketDataBundle, hist_limit: Optional[pd.DataFrame] = None) -> LayerScore:
    """
    L7 风险层 Market Risk Score
    越高表示风险越大（注意：这是反向分数，越高越差）
    输出的 score 是 风险程度 (0=无风险, 100=极端风险)
    """
    overview = data.overview
    limit_stats = data.limit_stats
    
    dt_count = limit_stats.get('dt_count', overview.get('dt_count', 0))
    zb_count = limit_stats.get('zhaban_count', overview.get('zb_count', 0))
    broken_rate = limit_stats.get('broken_rate', overview.get('zb_rate', 0))
    zt_count = limit_stats.get('zt_count', overview.get('zt_count', 0))
    
    # 各指数跌幅
    ret1_list = []
    ret3_list = []
    dd_list = []
    for name, _ in INDEX_LIST_V9:
        df = data.index_data.get(name)
        if df is None or len(df) < 10:
            continue
        close = df['close'].astype(float)
        if len(close) > 1:
            ret1_list.append(close.pct_change(1).iloc[-1] * 100)
        if len(close) > 3:
            ret3_list.append(close.pct_change(3).iloc[-1] * 100)
        if len(close) >= 20:
            roll_max = close.tail(20).cummax()
            dd = ((close.tail(20) - roll_max) / roll_max * 100).min()
            dd_list.append(abs(dd))
    
    avg_ret1 = np.mean(ret1_list) if ret1_list else 0
    avg_ret3 = np.mean(ret3_list) if ret3_list else 0
    avg_dd = np.mean(dd_list) if dd_list else 0
    
    risk_score = 0
    
    # 1. 短期跌幅风险 (25分)
    if avg_ret1 <= -3:
        risk_score += 25
    elif avg_ret1 <= -2:
        risk_score += 20
    elif avg_ret1 <= -1.5:
        risk_score += 15
    elif avg_ret1 <= -1:
        risk_score += 10
    elif avg_ret1 <= -0.5:
        risk_score += 5
    else:
        risk_score += 0
    
    # 2. 3日累计跌幅 (15分)
    if avg_ret3 <= -6:
        risk_score += 15
    elif avg_ret3 <= -4:
        risk_score += 12
    elif avg_ret3 <= -3:
        risk_score += 9
    elif avg_ret3 <= -2:
        risk_score += 6
    elif avg_ret3 <= -1:
        risk_score += 3
    else:
        risk_score += 0
    
    # 3. 跌停数量风险 (20分)
    if dt_count >= 50:
        risk_score += 20
    elif dt_count >= 30:
        risk_score += 16
    elif dt_count >= 20:
        risk_score += 13
    elif dt_count >= 10:
        risk_score += 10
    elif dt_count >= 5:
        risk_score += 6
    elif dt_count >= 3:
        risk_score += 3
    else:
        risk_score += 0
    
    # 4. 炸板率风险 (15分)
    if broken_rate >= 50:
        risk_score += 15
    elif broken_rate >= 40:
        risk_score += 12
    elif broken_rate >= 35:
        risk_score += 9
    elif broken_rate >= 30:
        risk_score += 6
    elif broken_rate >= 25:
        risk_score += 3
    else:
        risk_score += 0
    
    # 5. 风险加速度 (15分) - 跌停/炸板变化速度
    accel_risk = 5
    if hist_limit is not None and len(hist_limit) >= 2:
        prev_dt = hist_limit.iloc[-2].get('dt_count', dt_count) if 'dt_count' in hist_limit.columns else dt_count
        prev_zb = hist_limit.iloc[-2].get('zhaban_count', zb_count) if 'zhaban_count' in hist_limit.columns else zb_count
        
        dt_change = dt_count - prev_dt
        zb_change = zb_count - prev_zb
        
        if dt_change >= 20 or zb_change >= 30:
            accel_risk = 15
        elif dt_change >= 10 or zb_change >= 20:
            accel_risk = 12
        elif dt_change >= 5 or zb_change >= 10:
            accel_risk = 9
        elif dt_change >= 2 or zb_change >= 5:
            accel_risk = 6
        elif dt_change <= 0 and zb_change <= 0:
            accel_risk = 2
        else:
            accel_risk = 5
    risk_score += accel_risk
    
    # 6. 最大回撤风险 (10分)
    if avg_dd >= 10:
        risk_score += 10
    elif avg_dd >= 7:
        risk_score += 8
    elif avg_dd >= 5:
        risk_score += 6
    elif avg_dd >= 3:
        risk_score += 4
    elif avg_dd >= 2:
        risk_score += 2
    else:
        risk_score += 0
    
    risk_score = _clip(risk_score)
    
    # 风险等级
    if risk_score >= 80:
        risk_level = "极端风险"
        direction = "bearish"
    elif risk_score >= 60:
        risk_level = "高风险"
        direction = "bearish"
    elif risk_score >= 40:
        risk_level = "中等风险"
        direction = "neutral"
    elif risk_score >= 20:
        risk_level = "低风险"
        direction = "bullish"
    else:
        risk_level = "极低风险"
        direction = "bullish"
    
    # 风险加速状态
    if accel_risk >= 12:
        risk_accel = "快速恶化"
    elif accel_risk >= 8:
        risk_accel = "缓慢上升"
    elif accel_risk <= 3:
        risk_accel = "风险缓解"
    else:
        risk_accel = "稳定"
    
    details = {
        "risk_level": risk_level,
        "risk_acceleration": risk_accel,
        "avg_ret1": round(avg_ret1, 2),
        "avg_ret3": round(avg_ret3, 2),
        "avg_drawdown_20d": round(avg_dd, 2),
        "dt_count": dt_count,
        "broken_rate": round(broken_rate, 1),
        "component_scores": {
            "one_day_drop": 25 - risk_score // 1,  # 反向显示
            "three_day_drop": 0,
            "limit_down": dt_count,
            "broken_board": broken_rate,
            "acceleration": accel_risk,
            "drawdown": avg_dd,
        }
    }
    
    confidence = 0.9 if ret1_list else 0.7
    
    return LayerScore(
        score=risk_score,
        direction=direction,
        confidence=confidence,
        details=details
    )


# ============================================================
# 核心指标: STLE & TES & PositionScore
# ============================================================

def calculate_stle(l4: LayerScore, l3: LayerScore, l2: LayerScore, 
                   l5: LayerScore, l6: LayerScore, leader_strength: float = 50) -> float:
    """
    STLE = Short-Term Trading Environment (短线交易环境)
    
    STLE = 
    25% × STSS
    20% × MomentumScore
    20% × BreadthScore
    15% × ThemeScore
    10% × LeaderStrength
    10% × LiquidityScore
    """
    stss = l4.score
    mom = l3.score
    breadth = l2.score
    theme = l5.score
    liq = l6.score
    
    stle = (stss * 0.25 + mom * 0.20 + breadth * 0.20 + 
            theme * 0.15 + leader_strength * 0.10 + liq * 0.10)
    return _clip(stle)


def calculate_tes(stle: float, mainline_strength: float, breadth: float,
                  sentiment: float, momentum: float, liquidity: float,
                  risk_inverse: float) -> float:
    """
    TES = Trade Environment Score (交易环境评分)
    
    TES = 
    25% STLE
    20% MainlineStrength
    15% Breadth
    15% Sentiment
    10% Momentum
    10% Liquidity
    5% RiskInverse
    """
    tes = (stle * 0.25 + mainline_strength * 0.20 + breadth * 0.15 +
           sentiment * 0.15 + momentum * 0.10 + liquidity * 0.10 + risk_inverse * 0.05)
    return _clip(tes)


def calculate_position_score(stle: float, regime_score: float, theme_strength: float,
                             breadth: float, momentum: float, liquidity: float,
                             risk_inverse: float, confidence: float) -> float:
    """
    PositionScore 0~100
    
    PositionScore =
    25% STLE
    20% RegimeScore
    15% ThemeStrength
    15% Breadth
    10% Momentum
    5% Liquidity
    5% RiskInverse
    5% Confidence
    """
    ps = (stle * 0.25 + regime_score * 0.20 + theme_strength * 0.15 +
          breadth * 0.15 + momentum * 0.10 + liquidity * 0.05 +
          risk_inverse * 0.05 + confidence * 100 * 0.05)
    return _clip(ps)


# ============================================================
# 15种核心 Regime 定义
# ============================================================

REGIME_DEFINITIONS = {
    "STRONG_TREND": {
        "cn": "强趋势上行",
        "base_position": 60,
        "max_position": 80,
    },
    "STRONG_TREND_ACCELERATION": {
        "cn": "强趋势加速",
        "base_position": 65,
        "max_position": 85,
    },
    "STRONG_TREND_LATE": {
        "cn": "强趋势末端",
        "base_position": 45,
        "max_position": 60,
    },
    "RANGE_WITH_MAINLINE": {
        "cn": "震荡+强主线",
        "base_position": 40,
        "max_position": 60,
    },
    "RANGE_MULTI_ROTATION": {
        "cn": "震荡+多主线轮动",
        "base_position": 30,
        "max_position": 50,
    },
    "RANGE_NO_MAINLINE": {
        "cn": "震荡+无主线",
        "base_position": 15,
        "max_position": 30,
    },
    "WEAK_RANGE": {
        "cn": "弱势震荡",
        "base_position": 10,
        "max_position": 25,
    },
    "FAST_DECLINE": {
        "cn": "快速下跌",
        "base_position": 5,
        "max_position": 15,
    },
    "PANIC_SELLING": {
        "cn": "恐慌杀跌",
        "base_position": 3,
        "max_position": 10,
    },
    "EXTREME_FREEZE": {
        "cn": "极度冰点",
        "base_position": 5,
        "max_position": 20,
    },
    "FREEZE_REPAIR": {
        "cn": "冰点修复",
        "base_position": 15,
        "max_position": 35,
    },
    "V_SHAPED_REVERSAL": {
        "cn": "V型反转确认",
        "base_position": 35,
        "max_position": 60,
    },
    "POST_CRASH_REBOUND": {
        "cn": "暴跌后首次反弹",
        "base_position": 15,
        "max_position": 30,
    },
    "HIGH_LEVEL_DISTRIBUTION": {
        "cn": "高位分歧",
        "base_position": 25,
        "max_position": 40,
    },
    "SYSTEMIC_RISK": {
        "cn": "系统性风险",
        "base_position": 0,
        "max_position": 5,
    },
}


# ============================================================
# Regime 多因子投票识别
# ============================================================

def classify_regime_v9(l1: LayerScore, l2: LayerScore, l3: LayerScore,
                       l4: LayerScore, l5: LayerScore, l6: LayerScore,
                       l7: LayerScore, stle: float,
                       prev_regime: str = "UNKNOWN",
                       hist_limit: Optional[pd.DataFrame] = None) -> RegimeResult:
    """
    V9 Regime 分类器：多因子投票
    
    优先级: Risk > Sentiment > Breadth > Theme > Momentum > Index Trend
    """
    index_s = l1.score
    breadth_s = l2.score
    mom_s = l3.score
    sentiment_s = l4.score
    theme_s = l5.score
    liq_s = l6.score
    risk_s = l7.score  # 风险分越高越差
    risk_inv = 100 - risk_s
    
    mom_state = l3.details.get('mom_state', '')
    mainline_state = l5.details.get('mainline_state', 'NONE')
    risk_accel = l7.details.get('risk_acceleration', '')
    sentiment_state = l4.details.get('sentiment_state', '')
    
    # 提取辅助指标
    avg_ret1 = l1.details.get('avg_ret1', 0)
    avg_ret3 = l1.details.get('avg_ret3', 0)
    dt_count = l4.details.get('dt_count', 0)
    zt_count = l4.details.get('zt_count', 0)
    broken_rate = l4.details.get('broken_rate', 0)
    max_height = l4.details.get('max_height', 0)
    up_ratio = l2.details.get('up_ratio', 50)
    
    scores = {
        "IndexTrend": index_s,
        "Breadth": breadth_s,
        "Momentum": mom_s,
        "Sentiment": sentiment_s,
        "Theme": theme_s,
        "Liquidity": liq_s,
        "RiskInverse": risk_inv,
    }
    
    supporting = []
    contradicting = []
    
    # ========== 紧急风险优先 ==========
    # R15: 系统性风险
    if risk_s >= 80 and (avg_ret1 <= -2.5 or dt_count >= 40 or up_ratio <= 20):
        regime = "SYSTEMIC_RISK"
        confidence = 0.9
        supporting = [f"RiskScore={risk_s:.0f}", f"单日跌幅{avg_ret1:.1f}%", f"跌停{dt_count}只"]
        return RegimeResult(regime=regime, regime_cn=REGIME_DEFINITIONS[regime]["cn"],
                            confidence=confidence,
                            supporting_factors=supporting, contradicting_factors=[],
                            score_breakdown=scores)
    
    # R9: 恐慌杀跌
    if risk_s >= 65 and risk_accel == "快速恶化" and (avg_ret1 <= -1.5 or dt_count >= 20):
        regime = "PANIC_SELLING"
        confidence = 0.85
        supporting = [f"RiskScore={risk_s:.0f}", f"风险{risk_accel}", f"跌幅{avg_ret1:.1f}%", f"跌停{dt_count}只"]
        if index_s > 40:
            contradicting = [f"IndexTrend={index_s:.0f}"]
        return RegimeResult(regime=regime, regime_cn=REGIME_DEFINITIONS[regime]["cn"],
                            confidence=confidence,
                            supporting_factors=supporting, contradicting_factors=contradicting,
                            score_breakdown=scores)
    
    # R10: 极度冰点（情绪冰点 + 赚钱效应极差 + 风险高）
    if (sentiment_s <= 22 and risk_s >= 50
            and (zt_count <= 10 or up_ratio <= 25 or dt_count >= 40)
            and avg_ret1 >= -2.0):  # 不是暴跌当天，而是长期冰点状态
        regime = "EXTREME_FREEZE"
        confidence = 0.8
        supporting = [f"Sentiment={sentiment_s:.0f}", f"涨停{zt_count}只", f"跌停{dt_count}只", f"上涨比例{up_ratio:.0f}%"]
        return RegimeResult(regime=regime, regime_cn=REGIME_DEFINITIONS[regime]["cn"],
                            confidence=confidence,
                            supporting_factors=supporting, contradicting_factors=[],
                            score_breakdown=scores)
    
    # R8: 快速下跌（单日大跌）
    if risk_s >= 50 and avg_ret1 <= -1.0 and (dt_count >= 10 or up_ratio <= 35):
        regime = "FAST_DECLINE"
        confidence = 0.8
        supporting = [f"RiskScore={risk_s:.0f}", f"跌幅{avg_ret1:.1f}%", f"跌停{dt_count}只", f"上涨比例{up_ratio:.0f}%"]
        return RegimeResult(regime=regime, regime_cn=REGIME_DEFINITIONS[regime]["cn"],
                            confidence=confidence,
                            supporting_factors=supporting, contradicting_factors=[],
                            score_breakdown=scores)
    
    # ========== 修复/反转状态 ==========
    # 前一个状态是否为极端负面
    prev_negative = prev_regime in ("SYSTEMIC_RISK", "PANIC_SELLING", "FAST_DECLINE", "EXTREME_FREEZE", "POST_CRASH_REBOUND")
    
    # R11: 冰点修复
    if (prev_negative or risk_s >= 50) and 30 <= sentiment_s <= 50 and mom_state == "修复" and zt_count > dt_count:
        regime = "FREEZE_REPAIR"
        confidence = 0.75
        supporting = [f"情绪从冰点修复至{sentiment_s:.0f}", f"动量状态={mom_state}", f"涨停{zt_count}>跌停{dt_count}"]
        if index_s < 40:
            contradicting.append(f"指数仍弱 Index={index_s:.0f}")
        return RegimeResult(regime=regime, regime_cn=REGIME_DEFINITIONS[regime]["cn"],
                            confidence=confidence,
                            supporting_factors=supporting, contradicting_factors=contradicting,
                            score_breakdown=scores)
    
    # R13: 暴跌后首次反弹
    if prev_regime in ("PANIC_SELLING", "FAST_DECLINE", "EXTREME_FREEZE") and avg_ret1 > 1.0 and sentiment_s <= 45:
        regime = "POST_CRASH_REBOUND"
        confidence = 0.7
        supporting = [f"前日{prev_regime}", f"今日反弹{avg_ret1:.1f}%", f"情绪仍弱{sentiment_s:.0f}"]
        contradicting = ["反弹确认不足"]
        return RegimeResult(regime=regime, regime_cn=REGIME_DEFINITIONS[regime]["cn"],
                            confidence=confidence,
                            supporting_factors=supporting, contradicting_factors=contradicting,
                            score_breakdown=scores)
    
    # R12: V型反转确认
    if (prev_regime in ("FREEZE_REPAIR", "POST_CRASH_REBOUND") 
            and stle >= 60 and sentiment_s >= 55 and breadth_s >= 55
            and mom_state in ("加速", "正常") and mainline_state in ("STRONG_SINGLE", "STRONG_MULTI")):
        regime = "V_SHAPED_REVERSAL"
        confidence = 0.75
        supporting = [f"STLE={stle:.0f}", f"情绪={sentiment_s:.0f}", f"主线={mainline_state}", f"动量={mom_state}"]
        return RegimeResult(regime=regime, regime_cn=REGIME_DEFINITIONS[regime]["cn"],
                            confidence=confidence,
                            supporting_factors=supporting, contradicting_factors=[],
                            score_breakdown=scores)
    
    # ========== 强趋势系列 ==========
    # R2: 强趋势加速
    if (index_s >= 75 and mom_state == "加速" and sentiment_s >= 75 
            and breadth_s >= 65 and stle >= 75):
        regime = "STRONG_TREND_ACCELERATION"
        confidence = 0.85
        supporting = [f"Index={index_s:.0f}", f"情绪={sentiment_s:.0f}", f"动量{mom_state}", f"广度={breadth_s:.0f}"]
        return RegimeResult(regime=regime, regime_cn=REGIME_DEFINITIONS[regime]["cn"],
                            confidence=confidence,
                            supporting_factors=supporting, contradicting_factors=[],
                            score_breakdown=scores)
    
    # R1: 强趋势上行
    if index_s >= 70 and sentiment_s >= 65 and stle >= 65 and breadth_s >= 55:
        regime = "STRONG_TREND"
        confidence = 0.8
        supporting = [f"Index={index_s:.0f}", f"情绪={sentiment_s:.0f}", f"STLE={stle:.0f}", f"广度={breadth_s:.0f}"]
        if mainline_state == "NONE":
            contradicting.append("主线不明确")
        return RegimeResult(regime=regime, regime_cn=REGIME_DEFINITIONS[regime]["cn"],
                            confidence=confidence,
                            supporting_factors=supporting, contradicting_factors=contradicting,
                            score_breakdown=scores)
    
    # R3: 强趋势末端
    if (index_s >= 65 and sentiment_s <= 50 and broken_rate >= 30 
            and mom_state in ("钝化", "衰退") and risk_s >= 35):
        regime = "STRONG_TREND_LATE"
        confidence = 0.75
        supporting = [f"指数仍强{index_s:.0f}", f"情绪退潮{sentiment_s:.0f}", f"炸板率{broken_rate:.0f}%", f"动量{mom_state}"]
        return RegimeResult(regime=regime, regime_cn=REGIME_DEFINITIONS[regime]["cn"],
                            confidence=confidence,
                            supporting_factors=supporting, contradicting_factors=[],
                            score_breakdown=scores)
    
    # R14: 高位分歧
    if index_s >= 60 and sentiment_s <= 45 and (broken_rate >= 35 or dt_count >= 8):
        regime = "HIGH_LEVEL_DISTRIBUTION"
        confidence = 0.75
        supporting = [f"指数{index_s:.0f}", f"情绪{sentiment_s:.0f}", f"炸板率{broken_rate:.0f}%", f"跌停{dt_count}只"]
        return RegimeResult(regime=regime, regime_cn=REGIME_DEFINITIONS[regime]["cn"],
                            confidence=confidence,
                            supporting_factors=supporting, contradicting_factors=[],
                            score_breakdown=scores)
    
    # ========== 震荡系列 ==========
    # 主线优先：先判断有主线的震荡，再判断弱势震荡和无主线
    
    # R4: 震荡+强主线
    if (mainline_state in ("STRONG_SINGLE", "STRONG_MULTI")
            and theme_s >= 65 and stle >= 55
            and risk_s <= 55 and sentiment_s >= 45):
        regime = "RANGE_WITH_MAINLINE"
        confidence = 0.8
        supporting = [f"主线={mainline_state}", f"主题分={theme_s:.0f}", f"STLE={stle:.0f}", f"情绪={sentiment_s:.0f}"]
        if index_s < 50:
            contradicting.append(f"指数偏弱 Index={index_s:.0f}")
        return RegimeResult(regime=regime, regime_cn=REGIME_DEFINITIONS[regime]["cn"],
                            confidence=confidence,
                            supporting_factors=supporting, contradicting_factors=contradicting,
                            score_breakdown=scores)
    
    # R5: 震荡+多主线轮动
    if (mainline_state in ("ROTATION", "STRONG_MULTI")
            and 45 <= stle <= 65 and sentiment_s >= 45
            and 40 <= index_s <= 70):
        regime = "RANGE_MULTI_ROTATION"
        confidence = 0.75
        supporting = [f"主线轮动={mainline_state}", f"STLE={stle:.0f}", f"指数{index_s:.0f}"]
        return RegimeResult(regime=regime, regime_cn=REGIME_DEFINITIONS[regime]["cn"],
                            confidence=confidence,
                            supporting_factors=supporting, contradicting_factors=[],
                            score_breakdown=scores)
    
    # R6: 震荡+无主线
    if (40 <= index_s <= 65 and mainline_state in ("WEAK", "NONE")
            and theme_s <= 55 and stle <= 50):
        regime = "RANGE_NO_MAINLINE"
        confidence = 0.75
        supporting = [f"指数震荡{index_s:.0f}", f"无明确主线={mainline_state}", f"主题分={theme_s:.0f}", f"STLE={stle:.0f}"]
        return RegimeResult(regime=regime, regime_cn=REGIME_DEFINITIONS[regime]["cn"],
                            confidence=confidence,
                            supporting_factors=supporting, contradicting_factors=[],
                            score_breakdown=scores)
    
    # R7: 弱势震荡
    if (30 <= index_s <= 50 and risk_s <= 50 and sentiment_s >= 30
            and avg_ret1 >= -1.0 and dt_count <= 10):
        regime = "WEAK_RANGE"
        confidence = 0.7
        supporting = [f"指数弱势{index_s:.0f}", f"情绪{sentiment_s:.0f}", f"风险可控{risk_s:.0f}"]
        if mainline_state != "NONE":
            contradicting.append(f"仍有主线机会={mainline_state}")
        return RegimeResult(regime=regime, regime_cn=REGIME_DEFINITIONS[regime]["cn"],
                            confidence=confidence,
                            supporting_factors=supporting, contradicting_factors=contradicting,
                            score_breakdown=scores)
    
    # ========== 默认回退 ==========
    # 综合判断
    if stle >= 60 and index_s >= 60:
        regime = "RANGE_WITH_MAINLINE" if mainline_state in ("STRONG_SINGLE", "STRONG_MULTI") else "STRONG_TREND"
        confidence = 0.6
    elif stle >= 45 and index_s >= 45:
        if mainline_state in ("STRONG_SINGLE", "STRONG_MULTI", "ROTATION"):
            regime = "RANGE_MULTI_ROTATION"
        else:
            regime = "RANGE_NO_MAINLINE"
        confidence = 0.6
    elif risk_s >= 50:
        regime = "WEAK_RANGE"
        confidence = 0.6
    else:
        regime = "RANGE_NO_MAINLINE"
        confidence = 0.5
    
    supporting = [f"STLE={stle:.0f}", f"Index={index_s:.0f}", f"主线={mainline_state}"]
    
    return RegimeResult(regime=regime, regime_cn=REGIME_DEFINITIONS.get(regime, {"cn": regime})["cn"],
                        confidence=confidence,
                        supporting_factors=supporting, contradicting_factors=[],
                        score_breakdown=scores)


# ============================================================
# Regime 状态迁移检测
# ============================================================

def detect_regime_transition(current_regime: str, prev_regime: str,
                             l3: LayerScore, l4: LayerScore, l7: LayerScore) -> Tuple[str, float]:
    """
    检测 Regime 迁移方向和置信度
    返回: (transition_type, confidence)
    """
    if prev_regime == "UNKNOWN" or prev_regime == current_regime:
        return "STABLE", 0.9
    
    # 负面迁移（恶化）
    negative_paths = {
        ("STRONG_TREND", "STRONG_TREND_LATE"): 0.8,
        ("STRONG_TREND", "HIGH_LEVEL_DISTRIBUTION"): 0.8,
        ("STRONG_TREND_LATE", "FAST_DECLINE"): 0.85,
        ("HIGH_LEVEL_DISTRIBUTION", "FAST_DECLINE"): 0.85,
        ("RANGE_WITH_MAINLINE", "RANGE_NO_MAINLINE"): 0.7,
        ("RANGE_MULTI_ROTATION", "RANGE_NO_MAINLINE"): 0.7,
        ("RANGE_NO_MAINLINE", "WEAK_RANGE"): 0.75,
        ("WEAK_RANGE", "FAST_DECLINE"): 0.8,
        ("FAST_DECLINE", "PANIC_SELLING"): 0.85,
        ("PANIC_SELLING", "SYSTEMIC_RISK"): 0.9,
        ("FAST_DECLINE", "EXTREME_FREEZE"): 0.8,
    }
    
    # 正面迁移（修复）
    positive_paths = {
        ("EXTREME_FREEZE", "FREEZE_REPAIR"): 0.8,
        ("FREEZE_REPAIR", "POST_CRASH_REBOUND"): 0.7,
        ("POST_CRASH_REBOUND", "V_SHAPED_REVERSAL"): 0.75,
        ("FREEZE_REPAIR", "V_SHAPED_REVERSAL"): 0.65,
        ("V_SHAPED_REVERSAL", "RANGE_WITH_MAINLINE"): 0.7,
        ("V_SHAPED_REVERSAL", "STRONG_TREND"): 0.6,
        ("WEAK_RANGE", "RANGE_NO_MAINLINE"): 0.7,
        ("RANGE_NO_MAINLINE", "RANGE_WITH_MAINLINE"): 0.7,
        ("RANGE_WITH_MAINLINE", "STRONG_TREND"): 0.7,
    }
    
    key = (prev_regime, current_regime)
    
    if key in negative_paths:
        return "DETERIORATING", negative_paths[key]
    elif key in positive_paths:
        return "RECOVERING", positive_paths[key]
    else:
        # 跨状态跳变，置信度较低
        mom_state = l3.details.get('mom_state', '')
        risk_accel = l7.details.get('risk_acceleration', '')
        
        if risk_accel == "快速恶化":
            return "ABRUPT_DETERIORATION", 0.6
        elif mom_state == "修复":
            return "ABRUPT_RECOVERY", 0.5
        else:
            return "TRANSITION_UNCERTAIN", 0.4


# ============================================================
# Emergency Brake 紧急制动
# ============================================================

def check_emergency_brake(l1: LayerScore, l2: LayerScore, l4: LayerScore,
                          l7: LayerScore) -> Tuple[str, float]:
    """
    紧急制动系统：独立于 Regime 的快速降仓机制
    
    返回: (brake_level, multiplier)
    brake_level: NONE / LEVEL1 / LEVEL2 / LEVEL3 / LEVEL4
    multiplier: 仓位乘数
    """
    avg_ret1 = l1.details.get('avg_ret1', 0)
    up_ratio = l2.details.get('up_ratio', 50)
    breadth_change = l2.details.get('breadth_change', 0)
    dt_count = l4.details.get('dt_count', 0)
    zt_count = l4.details.get('zt_count', 0)
    broken_rate = l4.details.get('broken_rate', 0)
    risk_s = l7.score
    risk_accel = l7.details.get('risk_acceleration', '')
    
    # LEVEL 4: 系统性风险
    if risk_s >= 80 and (avg_ret1 <= -3.0 or (dt_count >= 50 and up_ratio <= 20)):
        return "LEVEL4", 0.05
    
    # LEVEL 3: 恐慌性杀跌
    if risk_s >= 65 and risk_accel == "快速恶化" and (avg_ret1 <= -2.0 or dt_count >= 30):
        return "LEVEL3", 0.10
    
    # LEVEL 2: 加速下跌
    level2_cond = 0
    if avg_ret1 <= -1.5:
        level2_cond += 1
    if dt_count >= 20:
        level2_cond += 1
    if zt_count <= 15 and dt_count >= 10:
        level2_cond += 1
    if broken_rate >= 45:
        level2_cond += 1
    if breadth_change <= -15 and up_ratio <= 35:
        level2_cond += 1
    
    if level2_cond >= 3 and risk_s >= 50:
        return "LEVEL2", 0.50
    
    # LEVEL 1: 预警
    level1_cond = 0
    if avg_ret1 <= -1.0:
        level1_cond += 1
    if dt_count >= 10:
        level1_cond += 1
    if broken_rate >= 35:
        level1_cond += 1
    if up_ratio <= 40:
        level1_cond += 1
    if risk_accel == "快速恶化":
        level1_cond += 1
    
    if level1_cond >= 2:
        return "LEVEL1", 0.70
    
    return "NONE", 1.0


# ============================================================
# 仓位乘数体系
# ============================================================

def calc_market_quality_multiplier(stle: float) -> float:
    """
    Market Quality Multiplier
    基于 STLE 动态调整
    """
    if stle >= 85:
        return 1.20
    elif stle >= 80:
        return 1.15
    elif stle >= 75:
        return 1.10
    elif stle >= 70:
        return 1.05
    elif stle >= 65:
        return 1.00
    elif stle >= 60:
        return 0.95
    elif stle >= 55:
        return 0.90
    elif stle >= 50:
        return 0.80
    elif stle >= 45:
        return 0.70
    elif stle >= 40:
        return 0.60
    elif stle >= 35:
        return 0.45
    elif stle >= 30:
        return 0.30
    else:
        return 0.20


def calc_theme_multiplier(l5: LayerScore) -> float:
    """
    Theme Multiplier
    基于主线强度
    """
    mainline_state = l5.details.get('mainline_state', 'NONE')
    theme_s = l5.score
    leader_strength = l5.details.get('leader_strength', 50)
    
    if mainline_state == "STRONG_SINGLE" and leader_strength >= 75:
        return 1.20
    elif mainline_state == "STRONG_SINGLE":
        return 1.10
    elif mainline_state == "STRONG_MULTI":
        return 1.05
    elif mainline_state == "ROTATION":
        return 0.95
    elif mainline_state == "WEAK":
        return 0.80
    else:  # NONE
        return 0.60


def calc_risk_multiplier(l7: LayerScore) -> float:
    """
    Risk Multiplier
    风险越高，乘数越低
    """
    risk_s = l7.score
    risk_accel = l7.details.get('risk_acceleration', '')
    
    base_mult = 1.0
    if risk_s >= 80:
        base_mult = 0.20
    elif risk_s >= 70:
        base_mult = 0.35
    elif risk_s >= 60:
        base_mult = 0.50
    elif risk_s >= 50:
        base_mult = 0.65
    elif risk_s >= 40:
        base_mult = 0.80
    elif risk_s >= 30:
        base_mult = 0.90
    else:
        base_mult = 1.0
    
    # 风险加速额外惩罚
    if risk_accel == "快速恶化":
        base_mult *= 0.8
    elif risk_accel == "缓慢上升":
        base_mult *= 0.9
    elif risk_accel == "风险缓解":
        base_mult = min(1.0, base_mult * 1.1)
    
    return base_mult


def calc_signal_multiplier(l3: LayerScore, l4: LayerScore) -> float:
    """
    Signal Multiplier
    基于动量和情绪的组合
    """
    mom_s = l3.score
    sent_s = l4.score
    mom_state = l3.details.get('mom_state', '')
    
    base = 0.5 + (mom_s + sent_s) / 200  # 0.5~1.5 范围
    
    if mom_state == "加速":
        base += 0.1
    elif mom_state in ("衰退", "急杀"):
        base -= 0.1
    
    return max(0.3, min(1.3, base))


# ============================================================
# 仓位分层计算
# ============================================================

def calc_position_tiers(recommended_position: float, regime: str) -> Dict[str, float]:
    """
    计算仓位分层：核心/进攻/试错/现金
    """
    pos = recommended_position
    
    if regime in ("STRONG_TREND", "STRONG_TREND_ACCELERATION"):
        core_ratio = 0.60
        attack_ratio = 0.30
        trial_ratio = 0.10
    elif regime in ("STRONG_TREND_LATE", "V_SHAPED_REVERSAL"):
        core_ratio = 0.55
        attack_ratio = 0.30
        trial_ratio = 0.15
    elif regime == "RANGE_WITH_MAINLINE":
        core_ratio = 0.50
        attack_ratio = 0.30
        trial_ratio = 0.20
    elif regime == "RANGE_MULTI_ROTATION":
        core_ratio = 0.40
        attack_ratio = 0.35
        trial_ratio = 0.25
    elif regime == "RANGE_NO_MAINLINE":
        core_ratio = 0.30
        attack_ratio = 0.30
        trial_ratio = 0.40
    elif regime == "WEAK_RANGE":
        core_ratio = 0.20
        attack_ratio = 0.30
        trial_ratio = 0.50
    elif regime == "FREEZE_REPAIR":
        core_ratio = 0.25
        attack_ratio = 0.25
        trial_ratio = 0.50
    elif regime == "POST_CRASH_REBOUND":
        core_ratio = 0.15
        attack_ratio = 0.25
        trial_ratio = 0.60
    elif regime in ("FAST_DECLINE", "PANIC_SELLING", "EXTREME_FREEZE", "SYSTEMIC_RISK"):
        core_ratio = 0.10
        attack_ratio = 0.10
        trial_ratio = 0.20
    elif regime == "HIGH_LEVEL_DISTRIBUTION":
        core_ratio = 0.40
        attack_ratio = 0.30
        trial_ratio = 0.30
    else:
        core_ratio = 0.40
        attack_ratio = 0.30
        trial_ratio = 0.30
    
    core = round(pos * core_ratio, 1)
    attack = round(pos * attack_ratio, 1)
    trial = round(pos * trial_ratio, 1)
    cash = round(100 - pos, 1)
    
    return {
        "core": core,
        "attack": attack,
        "trial": trial,
        "cash": cash,
    }


# ============================================================
# 加减仓条件生成
# ============================================================

def generate_upgrade_conditions(regime: str, stle: float, l4: LayerScore, 
                                l5: LayerScore, l2: LayerScore) -> List[str]:
    """生成加仓触发条件"""
    conditions = []
    sent_s = l4.score
    zt_count = l4.details.get('zt_count', 0)
    broken_rate = l4.details.get('broken_rate', 0)
    top1 = l5.details.get('top1_score', 0)
    up_ratio = l2.details.get('up_ratio', 50)
    
    if stle < 60:
        conditions.append(f"STLE 提升至 60+（当前 {stle:.0f}）")
    if sent_s < 55:
        conditions.append(f"情绪分提升至 55+（当前 {sent_s:.0f}）")
    if zt_count < 40:
        conditions.append(f"涨停数量 > 40只（当前 {zt_count}）")
    if broken_rate > 25:
        conditions.append(f"炸板率降至 < 25%（当前 {broken_rate:.0f}%）")
    if top1 < 70:
        conditions.append(f"主线Top1评分 > 70（当前 {top1:.0f}）")
    if up_ratio < 55:
        conditions.append(f"上涨比例 > 55%（当前 {up_ratio:.0f}%）")
    
    if not conditions:
        conditions.append("维持当前仓位，等待新的催化因素")
    
    return conditions[:4]


def generate_downgrade_conditions(regime: str, l7: LayerScore, 
                                  l4: LayerScore, l5: LayerScore) -> List[str]:
    """生成减仓触发条件"""
    conditions = []
    risk_s = l7.score
    dt_count = l4.details.get('dt_count', 0)
    broken_rate = l4.details.get('broken_rate', 0)
    mainline_state = l5.details.get('mainline_state', 'NONE')
    
    if risk_s < 40:
        conditions.append(f"风险分上升至 > 40（当前 {risk_s:.0f}）")
    if dt_count < 10:
        conditions.append(f"跌停数量 > 10只（当前 {dt_count}）")
    if broken_rate < 35:
        conditions.append(f"炸板率升至 > 35%（当前 {broken_rate:.0f}%）")
    if mainline_state not in ("NONE",):
        conditions.append("主线龙头跌停或主线退潮")
    conditions.append("指数跌破关键支撑位")
    
    return conditions[:4]


# ============================================================
# 滞回机制 & 仓位变化速度控制
# ============================================================

def apply_position_hysteresis(new_position: float, prev_position: Optional[float],
                              regime: str, transition: str,
                              brake_level: str) -> float:
    """
    仓位滞回机制：
    - 降仓可以快
    - 加仓必须慢
    - 极端风险除外
    """
    if prev_position is None:
        return new_position
    
    # 紧急制动：快速降仓
    if brake_level in ("LEVEL3", "LEVEL4"):
        return new_position  # 紧急情况直接降
    
    diff = new_position - prev_position
    
    # 降仓：可以快，但也限制单日极端跳变
    if diff < 0:
        max_drop = 25 if transition == "DETERIORATING" else 20
        if abs(diff) > max_drop:
            return prev_position - max_drop
        return new_position
    
    # 加仓：必须慢
    # 不同 Regime 加仓速度不同
    if regime in ("STRONG_TREND", "STRONG_TREND_ACCELERATION"):
        max_add = 15
    elif regime in ("RANGE_WITH_MAINLINE", "V_SHAPED_REVERSAL"):
        max_add = 12
    elif regime in ("RANGE_MULTI_ROTATION", "FREEZE_REPAIR"):
        max_add = 10
    elif regime in ("POST_CRASH_REBOUND", "EXTREME_FREEZE"):
        max_add = 5
    else:
        max_add = 8
    
    if diff > max_add:
        return prev_position + max_add
    
    return new_position


# ============================================================
# 组合回撤保护
# ============================================================

def apply_drawdown_protection(position: float, drawdown: float) -> Tuple[float, str]:
    """
    组合最大回撤保护
    """
    if drawdown <= 0:
        return position, ""
    
    if drawdown >= 15:
        return position * 0.40, f"组合回撤{drawdown:.1f}%，防御模式（×0.4）"
    elif drawdown >= 12:
        return position * 0.55, f"组合回撤{drawdown:.1f}%，大幅降仓（×0.55）"
    elif drawdown >= 8:
        return position * 0.70, f"组合回撤{drawdown:.1f}%，适度降仓（×0.7）"
    elif drawdown >= 5:
        return position * 0.85, f"组合回撤{drawdown:.1f}%，轻微降仓（×0.85）"
    else:
        return position, ""


# ============================================================
# 数据质量保护
# ============================================================

def calc_data_quality(data: MarketDataBundle) -> float:
    """
    数据质量评分 (0~100)
    缺数据 = 降低风险，而不是假设正常
    """
    score = 100.0
    penalties = 0
    
    # 指数数据完整性
    index_count = sum(1 for v in data.index_data.values() if v is not None and not v.empty)
    if index_count < 6:
        penalties += (6 - index_count) * 5
    
    # 涨跌停数据
    if not data.limit_stats or data.limit_stats.get('zt_count', 0) == 0:
        if data.overview.get('zt_count', 0) == 0:
            penalties += 10  # 涨跌停数据缺失
    
    # 成交额数据
    if not data.overview.get('total_amount', 0):
        penalties += 10
    
    # 主题数据
    if not data.theme_top3_scores:
        penalties += 15
    
    return max(20.0, 100.0 - penalties)


def apply_data_quality_guard(position: float, data_quality: float) -> Tuple[float, str]:
    """数据质量差 → 降低仓位"""
    if data_quality >= 80:
        return position, ""
    elif data_quality >= 70:
        return position * 0.85, f"数据质量{data_quality:.0f}%，仓位×0.85"
    elif data_quality >= 60:
        return position * 0.70, f"数据质量{data_quality:.0f}%，仓位×0.70"
    elif data_quality >= 50:
        return position * 0.55, f"数据质量{data_quality:.0f}%，仓位×0.55"
    else:
        return position * 0.40, f"数据质量{data_quality:.0f}%，严重降级×0.40"


# ============================================================
# 指数与个股背离检测
# ============================================================

def detect_index_stock_divergence(l1: LayerScore, l2: LayerScore, l4: LayerScore) -> Tuple[str, float]:
    """
    检测指数与个股赚钱效应的背离
    返回: (divergence_type, adjustment_multiplier)
    divergence_type: NONE / INDEX_FAKE_STRENGTH / INDEX_WEAK_BUT_STRUCTURAL / STRONG_CONSISTENT
    """
    index_s = l1.score
    breadth_s = l2.score
    sent_s = l4.score
    
    up_ratio = l2.details.get('up_ratio', 50)
    zt_count = l4.details.get('zt_count', 0)
    dt_count = l4.details.get('dt_count', 0)
    
    # A: 指数虚强（指数涨但个股普跌）
    if index_s >= 60 and breadth_s <= 40 and sent_s <= 40:
        return "INDEX_FAKE_STRENGTH", 0.70  # 降低仓位
    
    # B: 指数弱但结构性行情存在
    if index_s <= 45 and breadth_s >= 55 and sent_s >= 55 and zt_count >= 30:
        return "INDEX_WEAK_BUT_STRUCTURAL", 1.10  # 可以比指数指示的更高一些
    
    # C: 高度一致
    if abs(index_s - breadth_s) <= 10 and abs(index_s - sent_s) <= 15:
        return "STRONG_CONSISTENT", 1.0
    
    return "NONE", 1.0


# ============================================================
# 主引擎类 PositionEngineV9
# ============================================================

class PositionEngineV9:
    """
    A股短线动态仓位引擎 V9.0
    Regime-Aware Adaptive Position Engine
    
    七层模型 → 15种Regime → 动态仓位 → 多因子乘数 → 紧急制动 → 恢复阶梯
    """
    
    def __init__(self):
        pass
    
    def analyze(self, data: MarketDataBundle) -> EngineV9Result:
        """
        主分析入口
        """
        result = EngineV9Result()
        result.data_bundle = data
        result.trade_date = ""  # 调用方填充
        
        # ===== 数据质量检查 =====
        data_quality = calc_data_quality(data)
        result.data_quality = data_quality
        
        # ===== 历史数据准备 =====
        hist_limit = data.hist_limit
        
        # ===== L1~L7 七层评分 =====
        l1 = calc_l1_index_trend(data)
        result.l1_index = l1
        
        l2 = calc_l2_breadth(data, hist_limit)
        result.l2_breadth = l2
        
        l3 = calc_l3_momentum(data, hist_limit)
        result.l3_momentum = l3
        
        l4 = calc_l4_sentiment(data, hist_limit)
        result.l4_sentiment = l4
        
        l5 = calc_l5_theme(data)
        result.l5_theme = l5
        
        l6 = calc_l6_liquidity(data)
        result.l6_liquidity = l6
        
        l7 = calc_l7_risk(data, hist_limit)
        result.l7_risk = l7
        
        # ===== 核心指标 =====
        leader_strength = l5.details.get('leader_strength', 50)
        stle = calculate_stle(l4, l3, l2, l5, l6, leader_strength)
        result.stle = stle
        
        mainline_strength = l5.details.get('state_score', 50)
        risk_inv = 100 - l7.score
        tes = calculate_tes(stle, mainline_strength, l2.score, l4.score, 
                             l3.score, l6.score, risk_inv)
        result.tes = tes
        
        # ===== Regime 识别 =====
        prev_regime = "UNKNOWN"
        prev_position = None
        if data.prev_result is not None:
            prev_regime = data.prev_result.regime.regime
            prev_position = data.prev_result.position.recommended_position
        
        regime_result = classify_regime_v9(
            l1, l2, l3, l4, l5, l6, l7, stle, prev_regime, hist_limit
        )
        result.regime = regime_result
        
        # ===== 状态迁移检测 =====
        transition, trans_conf = detect_regime_transition(
            regime_result.regime, prev_regime, l3, l4, l7
        )
        result.prev_regime = prev_regime
        result.transition = transition
        result.transition_confidence = trans_conf
        
        # ===== Emergency Brake =====
        brake_level, brake_mult = check_emergency_brake(l1, l2, l4, l7)
        
        # ===== 背离检测 =====
        div_type, div_mult = detect_index_stock_divergence(l1, l2, l4)
        
        # ===== 仓位计算 =====
        pos_result = self._calculate_position(
            regime_result, stle, l1, l2, l3, l4, l5, l6, l7,
            brake_level, brake_mult, div_type, div_mult,
            prev_position, data.portfolio_drawdown, data_quality
        )
        result.position = pos_result
        
        # ===== PositionScore =====
        overall_conf = np.mean([
            l1.confidence, l2.confidence, l3.confidence, l4.confidence,
            l5.confidence, l6.confidence, l7.confidence, regime_result.confidence
        ])
        regime_score = min(100, max(0, 
            list(REGIME_DEFINITIONS.keys()).index(regime_result.regime) 
            / len(REGIME_DEFINITIONS) * 100
        )) if regime_result.regime in REGIME_DEFINITIONS else 50
        
        # 用 Regime 的基础仓位映射为 score
        base_pos = REGIME_DEFINITIONS.get(regime_result.regime, {"base_position": 30})["base_position"]
        max_pos = REGIME_DEFINITIONS.get(regime_result.regime, {"max_position": 50})["max_position"]
        regime_norm = base_pos / 80 * 100  # 用基础仓位比例代表
        
        position_score = calculate_position_score(
            stle, regime_norm, l5.score, l2.score,
            l3.score, l6.score, risk_inv, overall_conf
        )
        result.position_score = position_score
        
        # 仓位置信度
        result.position.position_confidence = round(overall_conf * 100, 1)
        
        # ===== 正负因素解释 =====
        pos_result.positive_factors = self._build_positive_factors(
            l1, l2, l3, l4, l5, l6, l7, stle, regime_result
        )
        pos_result.negative_factors = self._build_negative_factors(
            l1, l2, l3, l4, l5, l6, l7, regime_result, brake_level, data_quality
        )
        
        # ===== 加减仓条件 =====
        pos_result.upgrade_conditions = generate_upgrade_conditions(
            regime_result.regime, stle, l4, l5, l2
        )
        pos_result.downgrade_conditions = generate_downgrade_conditions(
            regime_result.regime, l7, l4, l5
        )
        
        # 紧急制动状态
        pos_result.emergency_brake = brake_level
        
        # 操作模式
        if brake_level in ("LEVEL3", "LEVEL4") or regime_result.regime == "SYSTEMIC_RISK":
            pos_result.operation_mode = "Capital Preservation"
        elif brake_level == "LEVEL2" or regime_result.regime in ("PANIC_SELLING", "FAST_DECLINE"):
            pos_result.operation_mode = "Defensive"
        elif stle >= 75 and regime_result.regime in ("STRONG_TREND", "STRONG_TREND_ACCELERATION"):
            pos_result.operation_mode = "Aggressive"
        else:
            pos_result.operation_mode = "Normal"
        
        return result
    
    def _calculate_position(self, regime_result: RegimeResult, stle: float,
                            l1: LayerScore, l2: LayerScore, l3: LayerScore,
                            l4: LayerScore, l5: LayerScore, l6: LayerScore,
                            l7: LayerScore, brake_level: str, brake_mult: float,
                            div_type: str, div_mult: float,
                            prev_position: Optional[float],
                            portfolio_drawdown: float,
                            data_quality: float) -> PositionResult:
        """核心仓位计算逻辑"""
        pos = PositionResult()
        
        regime = regime_result.regime
        if regime not in REGIME_DEFINITIONS:
            regime = "RANGE_NO_MAINLINE"
        
        base_position = REGIME_DEFINITIONS[regime]["base_position"]
        max_position = REGIME_DEFINITIONS[regime]["max_position"]
        
        pos.base_position = float(base_position)
        pos.max_position = float(max_position)
        
        # 多因子乘数
        mq_mult = calc_market_quality_multiplier(stle)
        theme_mult = calc_theme_multiplier(l5)
        risk_mult = calc_risk_multiplier(l7)
        signal_mult = calc_signal_multiplier(l3, l4)
        
        # 计算推荐仓位
        recommended = base_position * mq_mult * theme_mult * risk_mult * signal_mult * div_mult
        
        # 限制在 [0, max_position]
        recommended = max(0.0, min(max_position, recommended))
        
        # 紧急制动
        recommended *= brake_mult
        recommended = min(recommended, max_position)
        
        # 组合回撤保护
        recommended, dd_note = apply_drawdown_protection(recommended, portfolio_drawdown)
        
        # 数据质量保护
        recommended, dq_note = apply_data_quality_guard(recommended, data_quality)
        
        # 滞回机制
        recommended = apply_position_hysteresis(
            recommended, prev_position, regime, 
            "DETERIORATING" if risk_mult < 0.8 else "STABLE",
            brake_level
        )
        
        # 最终限制
        recommended = round(max(0.0, min(max_position, recommended)), 1)
        pos.recommended_position = recommended
        
        # 仓位分层
        tiers = calc_position_tiers(recommended, regime)
        pos.core_position = tiers["core"]
        pos.attack_position = tiers["attack"]
        pos.trial_position = tiers["trial"]
        pos.cash_position = tiers["cash"]
        
        return pos
    
    def _build_positive_factors(self, l1, l2, l3, l4, l5, l6, l7, stle, regime) -> List[str]:
        """构建正向因素列表"""
        factors = []
        
        if stle >= 70:
            factors.append(f"STLE 短线交易环境强 ({stle:.0f}) → +10~15%")
        elif stle >= 60:
            factors.append(f"STLE 交易环境尚可 ({stle:.0f}) → +0~5%")
        
        if l5.details.get('mainline_state') in ("STRONG_SINGLE", "STRONG_MULTI"):
            factors.append(f"主线强度高 ({l5.details['mainline_state']}) → +10~20%")
        
        if l4.score >= 65:
            factors.append(f"市场情绪良好 ({l4.details.get('sentiment_state', '')}, {l4.score:.0f}) → +6%")
        
        if l2.score >= 65:
            factors.append(f"市场广度健康 (上涨比例 {l2.details.get('up_ratio', 0):.0f}%) → +5%")
        
        if l3.details.get('mom_state') == "加速":
            factors.append(f"动量加速 → +5%")
        
        if l6.details.get('liquidity_type') in ("正向放量", "健康量能"):
            factors.append(f"流动性良好 ({l6.details['liquidity_type']}) → +3%")
        
        if l7.score <= 20:
            factors.append(f"风险极低 → +3%")
        
        if not factors:
            factors.append("暂无显著正向因素")
        
        return factors[:5]
    
    def _build_negative_factors(self, l1, l2, l3, l4, l5, l6, l7, regime, brake_level, data_quality) -> List[str]:
        """构建负向因素列表"""
        factors = []
        
        if brake_level != "NONE":
            factors.append(f"紧急制动 {brake_level} 触发 → 仓位大幅降低")
        
        if l7.score >= 50:
            factors.append(f"市场风险较高 ({l7.details.get('risk_level', '')}, Risk={l7.score:.0f}) → -20~35%")
        
        if l4.score <= 35:
            factors.append(f"情绪退潮 ({l4.details.get('sentiment_state', '')}, {l4.score:.0f}) → -15%")
        
        if l5.details.get('mainline_state') == "NONE":
            factors.append("无明确主线 → -40%")
        elif l5.details.get('mainline_state') == "WEAK":
            factors.append("主线弱 → -20%")
        
        if l2.score <= 40:
            factors.append(f"市场广度差 (上涨比例 {l2.details.get('up_ratio', 0):.0f}%) → -10%")
        
        if l3.details.get('mom_state') in ("衰退", "急杀"):
            factors.append(f"动量{l3.details['mom_state']} → -10%")
        
        if data_quality < 80:
            factors.append(f"数据质量不足 ({data_quality:.0f}%) → 降仓保护")
        
        if not factors:
            factors.append("暂无显著负向因素")
        
        return factors[:5]
    
    def generate_report(self, result: EngineV9Result) -> str:
        """生成 V9.0 格式报告"""
        lines = []
        lines.append("=" * 80)
        lines.append("A股短线动态仓位引擎 V9.0")
        lines.append("=" * 80)
        lines.append("")
        
        # 市场状态
        lines.append("【市场状态】")
        lines.append(f"  Regime: {result.regime.regime_cn} ({result.regime.regime})")
        lines.append(f"  Confidence: {result.regime.confidence * 100:.0f}%")
        lines.append("")
        
        # 指数环境
        lines.append("【指数环境】")
        lines.append(f"  IndexTrend: {result.l1_index.score:.1f} ({result.l1_index.direction})")
        lines.append(f"  Breadth: {result.l2_breadth.score:.1f} ({result.l2_breadth.direction})")
        lines.append(f"  Momentum: {result.l3_momentum.score:.1f} ({result.l3_momentum.direction}) [ {result.l3_momentum.details.get('mom_state', '')} ]")
        lines.append("")
        
        # 短线赚钱环境
        lines.append("【短线赚钱环境】")
        lines.append(f"  STLE (短线交易环境): {result.stle:.1f}")
        lines.append(f"  TES  (交易环境评分): {result.tes:.1f}")
        lines.append(f"  PositionScore: {result.position_score:.1f}")
        
        stle_desc = ""
        if result.stle >= 80:
            stle_desc = "极强赚钱环境"
        elif result.stle >= 70:
            stle_desc = "强赚钱环境"
        elif result.stle >= 60:
            stle_desc = "可积极交易"
        elif result.stle >= 50:
            stle_desc = "结构性交易"
        elif result.stle >= 40:
            stle_desc = "防守交易"
        elif result.stle >= 30:
            stle_desc = "高风险"
        else:
            stle_desc = "禁止主动进攻"
        lines.append(f"  环境评级: {stle_desc}")
        lines.append("")
        
        # 市场情绪
        l4d = result.l4_sentiment.details
        lines.append("【市场情绪】")
        lines.append(f"  涨停: {l4d.get('zt_count', 0)}  |  跌停: {l4d.get('dt_count', 0)}")
        lines.append(f"  炸板率: {l4d.get('broken_rate', 0)}%")
        lines.append(f"  连板高度: {l4d.get('max_height', 0)}板")
        lines.append(f"  晋级率: {l4d.get('promotion_rate', 0):.1f}%")
        lines.append(f"  情绪状态: {l4d.get('sentiment_state', '')} (STSS={result.l4_sentiment.score:.1f})")
        lines.append("")
        
        # 主题环境
        l5d = result.l5_theme.details
        lines.append("【主题环境】")
        lines.append(f"  MainlineState: {l5d.get('mainline_state', 'NONE')}")
        lines.append(f"  MainlineStrength: {l5d.get('state_score', 0):.1f}")
        lines.append(f"  Top1: {l5d.get('top1_score', 0):.1f}")
        lines.append(f"  Top3_avg: {l5d.get('top3_avg', 0):.1f}")
        lines.append(f"  LeaderStrength: {l5d.get('leader_strength', 0)}")
        lines.append("")
        
        # 流动性
        l6d = result.l6_liquidity.details
        lines.append("【流动性】")
        lines.append(f"  全市场成交额: {l6d.get('total_amount', 0):.0f}亿")
        lines.append(f"  5日/20日量比: {l6d.get('amount_ratio_5_20', 1.0):.2f}")
        lines.append(f"  流动性类型: {l6d.get('liquidity_type', '')}")
        lines.append(f"  LiquidityScore: {result.l6_liquidity.score:.1f}")
        lines.append("")
        
        # 风险
        l7d = result.l7_risk.details
        lines.append("【风险】")
        lines.append(f"  RiskScore: {result.l7_risk.score:.1f} ({l7d.get('risk_level', '')})")
        lines.append(f"  RiskAcceleration: {l7d.get('risk_acceleration', '')}")
        lines.append(f"  20日回撤: {l7d.get('avg_drawdown_20d', 0):.2f}%")
        lines.append(f"  1日跌幅: {l7d.get('avg_ret1', 0):.2f}%")
        lines.append("")
        
        # 仓位
        pos = result.position
        lines.append("【仓位】")
        lines.append(f"  Base Position:        {pos.base_position:.0f}%")
        lines.append(f"  Recommended Position: {pos.recommended_position:.1f}%")
        lines.append(f"  Maximum Position:     {pos.max_position:.0f}%")
        lines.append("")
        
        # 仓位分层
        lines.append("【仓位分层】")
        lines.append(f"  核心仓 (Core):   {pos.core_position:.1f}%")
        lines.append(f"  进攻仓 (Attack): {pos.attack_position:.1f}%")
        lines.append(f"  试错仓 (Trial):  {pos.trial_position:.1f}%")
        lines.append(f"  现金  (Cash):    {pos.cash_position:.1f}%")
        lines.append("")
        
        # 仓位调整原因
        lines.append("【仓位调整原因】")
        lines.append("  Positive:")
        for i, f in enumerate(pos.positive_factors, 1):
            lines.append(f"    {i}. {f}")
        lines.append("  Negative:")
        for i, f in enumerate(pos.negative_factors, 1):
            lines.append(f"    {i}. {f}")
        lines.append("")
        
        # 加仓条件
        lines.append("【加仓条件】")
        for i, c in enumerate(pos.upgrade_conditions, 1):
            lines.append(f"  {i}. {c}")
        lines.append("")
        
        # 减仓条件
        lines.append("【减仓条件】")
        for i, c in enumerate(pos.downgrade_conditions, 1):
            lines.append(f"  {i}. {c}")
        lines.append("")
        
        # 极端风险
        lines.append("【极端风险】")
        lines.append(f"  EmergencyBrake: {pos.emergency_brake}")
        if result.l7_risk.details.get('risk_acceleration'):
            lines.append(f"  风险加速度: {result.l7_risk.details['risk_acceleration']}")
        lines.append("")
        
        # 状态迁移
        lines.append("【状态迁移】")
        lines.append(f"  Previous: {result.prev_regime}")
        lines.append(f"  Current:  {result.regime.regime}")
        lines.append(f"  Transition: {result.transition} ({result.transition_confidence * 100:.0f}%)")
        lines.append("")
        
        # 置信度
        lines.append("【置信度】")
        lines.append(f"  Regime Confidence:   {result.regime.confidence * 100:.0f}%")
        lines.append(f"  Position Confidence: {pos.position_confidence:.0f}%")
        lines.append(f"  Data Quality:        {result.data_quality:.0f}%")
        lines.append("")
        
        # 最终操作
        mode_cn = {
            "Aggressive": "积极进攻",
            "Normal": "正常交易",
            "Defensive": "防守模式",
            "Capital Preservation": "保本优先",
        }
        lines.append("【最终操作】")
        lines.append(f"  模式: {pos.operation_mode} ({mode_cn.get(pos.operation_mode, pos.operation_mode)})")
        lines.append(f"  推荐仓位: {pos.recommended_position:.1f}%")
        lines.append("")
        
        lines.append("=" * 80)
        
        return "\n".join(lines)


# ============================================================
# 极端案例测试支持
# ============================================================

def build_test_bundle(
    # 指数
    index_ret1: float = 0.0, index_ret3: float = 0.0, 
    index_ma_state: str = "neutral",
    # 广度
    up_ratio: float = 50, up_count: int = 2500, down_count: int = 2500,
    # 情绪
    zt_count: int = 30, dt_count: int = 5, zhaban_count: int = 10,
    broken_rate: float = 25.0, max_limit_height: int = 3,
    # 主题
    theme_top3: Optional[List[float]] = None,
    mainline_state: str = "NONE", leader_strength: float = 50,
    # 流动性
    total_amount: float = 8000, amount_ratio: float = 1.0,
    # 风险
    drawdown: float = 2.0,
    # 前一状态
    prev_regime: str = "UNKNOWN",
) -> MarketDataBundle:
    """
    构造测试用的 MarketDataBundle（用于极端案例测试）
    """
    data = MarketDataBundle()
    
    # 构造指数数据（简化版）
    dates = pd.date_range(end='2024-01-01', periods=70, freq='B')
    base_price = 3000.0
    
    # 根据 ma_state 构造价格序列
    if index_ma_state == "strong_bull":
        prices = [base_price * (1 + 0.003 * i) for i in range(70)]
    elif index_ma_state == "bull":
        prices = [base_price * (1 + 0.002 * i) for i in range(70)]
    elif index_ma_state == "bear":
        prices = [base_price * (1 - 0.002 * i) for i in range(70)]
    elif index_ma_state == "strong_bear":
        prices = [base_price * (1 - 0.004 * i) for i in range(70)]
    else:
        prices = [base_price + np.sin(i * 0.2) * 50 for i in range(70)]
    
    # 叠加单日涨跌幅
    prices[-1] = prices[-2] * (1 + index_ret1 / 100) if len(prices) > 1 else prices[-1]
    
    df = pd.DataFrame({
        'trade_date': [d.strftime('%Y%m%d') for d in dates],
        'open': [p * 0.998 for p in prices],
        'high': [p * 1.005 for p in prices],
        'low': [p * 0.995 for p in prices],
        'close': prices,
        'vol': [1000000.0] * 70,
        'amount': [total_amount * 100000 / 3000] * 70,  # 简化
        'pct_chg': [0.0] + [prices[i]/prices[i-1] - 1 for i in range(1, len(prices))],
    })
    df['pct_chg'] = df['pct_chg'] * 100
    
    data.index_data["上证指数"] = df
    data.index_data["沪深300"] = df
    data.index_data["中证2000"] = df
    
    # 市场概况
    data.overview = {
        "total_amount": total_amount,
        "up_count": up_count,
        "down_count": down_count,
        "zt_count": zt_count,
        "dt_count": dt_count,
        "zb_count": zhaban_count,
        "zb_rate": broken_rate,
    }
    
    # 涨跌停统计
    data.limit_stats = {
        "zt_count": zt_count,
        "dt_count": dt_count,
        "zhaban_count": zhaban_count,
        "broken_rate": broken_rate,
        "up_count": up_count,
        "down_count": down_count,
        "up_ratio": up_ratio,
        "down_ratio": 100 - up_ratio,
        "total": up_count + down_count,
    }
    
    data.max_limit_height = max_limit_height
    
    # 主题
    data.theme_top3_scores = theme_top3 or [50, 45, 40]
    data.theme_info = {
        "leader_strength": leader_strength,
        "mainline_state": mainline_state,
    }
    
    data.portfolio_drawdown = drawdown
    
    # 模拟 prev_result
    if prev_regime != "UNKNOWN":
        from dataclasses import dataclass as _dc
        prev = EngineV9Result()
        prev.regime = RegimeResult(regime=prev_regime, regime_cn=prev_regime)
        prev.position.recommended_position = REGIME_DEFINITIONS.get(
            prev_regime, {"base_position": 30}
        )["base_position"]
        data.prev_result = prev
    
    return data


# ============================================================
# 快速接口（供外部模块调用）
# ============================================================

def run_position_engine_v9(data: MarketDataBundle) -> EngineV9Result:
    """
    便捷接口：运行 V9 仓位引擎
    """
    engine = PositionEngineV9()
    return engine.analyze(data)


def generate_v9_report(data: MarketDataBundle) -> str:
    """
    便捷接口：生成并返回 V9 报告文本
    """
    engine = PositionEngineV9()
    result = engine.analyze(data)
    return engine.generate_report(result), result


