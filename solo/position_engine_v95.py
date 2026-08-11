#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
A股短线动态仓位引擎 V9.9
95分实盘版：简单决策 + 量价环境 + 基础行情完整展示

市场决定仓位，主线决定方向，量价决定资金环境，D日决定节奏，风险决定底线。
"""
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict

from position_engine_v9 import (
    PositionEngineV9,
    MarketDataBundle,
    EngineV9Result,
    LayerScore,
    build_test_bundle,
    _clip,
)

# ============================================================
# 一、7种市场状态 + 完整仓位参数表（配置化）
# ============================================================

SEVEN_REGIMES = {
    "STRONG_TREND": {
        "cn": "强趋势",
        "base": 60,
        "normal_min": 50,
        "normal_max": 70,
        "confirm_cap": 80,
    },
    "RANGE_WITH_MAINLINE": {
        "cn": "震荡有主线",
        "base": 30,
        "normal_min": 25,
        "normal_max": 40,
        "confirm_cap": 60,
    },
    "RANGE_NO_MAINLINE": {
        "cn": "震荡无主线",
        "base": 20,
        "normal_min": 15,
        "normal_max": 25,
        "confirm_cap": 35,
    },
    "HIGH_DISTRIBUTION": {
        "cn": "高位分歧",
        "base": 20,
        "normal_min": 15,
        "normal_max": 30,
        "confirm_cap": 40,
    },
    "FAST_DECLINE": {
        "cn": "快速下跌",
        "base": 5,
        "normal_min": 0,
        "normal_max": 10,
        "confirm_cap": 20,
    },
    "EXTREME_FREEZE": {
        "cn": "极度冰点",
        "base": 5,
        "normal_min": 0,
        "normal_max": 10,
        "confirm_cap": 10,
    },
    "FREEZE_REPAIR": {
        "cn": "冰点修复",
        "base": 15,
        "normal_min": 10,
        "normal_max": 25,
        "confirm_cap": 50,
    },
}

# V9.0 15种 → V9.9 7种 映射
REGIME_MAP = {
    "STRONG_TREND": "STRONG_TREND",
    "STRONG_TREND_ACCELERATION": "STRONG_TREND",
    "STRONG_TREND_LATE": "HIGH_DISTRIBUTION",
    "RANGE_WITH_MAINLINE": "RANGE_WITH_MAINLINE",
    "RANGE_MULTI_ROTATION": "RANGE_WITH_MAINLINE",
    "RANGE_NO_MAINLINE": "RANGE_NO_MAINLINE",
    "WEAK_RANGE": "RANGE_NO_MAINLINE",
    "FAST_DECLINE": "FAST_DECLINE",
    "PANIC_SELLING": "FAST_DECLINE",
    "EXTREME_FREEZE": "EXTREME_FREEZE",
    "FREEZE_REPAIR": "FREEZE_REPAIR",
    "V_SHAPED_REVERSAL": "RANGE_WITH_MAINLINE",
    "POST_CRASH_REBOUND": "FREEZE_REPAIR",
    "HIGH_LEVEL_DISTRIBUTION": "HIGH_DISTRIBUTION",
    "SYSTEMIC_RISK": "EXTREME_FREEZE",
}

# ============================================================
# 二、环境调整参数（简单 ±5% 体系）
# ============================================================

MONEY_EFFECT_ADJ = {"强": +5, "中": 0, "弱": -5, "低": -10, "极差": -15}
MAINLINE_ADJ     = {"强": +5, "中": 0, "弱": -5, "无": -10}
RISK_ADJ         = {"正常风险": 0, "高风险": -10}
# 量价环境调整（最大影响 ±5%，与赚钱效应去重：只使用相对量能+涨跌方向，不重复用上涨比例）
LIQ_ADJ = {
    "放量上涨": +5,
    "温和放量上涨": +2,
    "正常量能": 0,
    "缩量上涨": -2,
    "缩量下跌": -2,
    "放量下跌": -5,
}

# ============================================================
# 三、V9.8 输出数据模型
# ============================================================

@dataclass
class MarketOverview:
    """大盘基础行情快照，用于报告展示"""
    sz_price: float = 0.0
    sz_chg: float = 0.0
    hs300_price: float = 0.0
    hs300_chg: float = 0.0
    zz2000_price: float = 0.0
    zz2000_chg: float = 0.0
    amount: float = 0.0
    up_count: int = 0
    down_count: int = 0
    zt_count: int = 0
    dt_count: int = 0
    zb_count: int = 0
    zb_rate: float = 0.0
    max_height: int = 0


@dataclass
class PositionV99Result:
    market_regime: str = "震荡无主线"
    market_trend: str = "中"
    money_effect: str = "中"
    mainline_strength: str = "中"
    risk_state: str = "正常风险"
    d_day: int = 0
    d_day_desc: str = ""

    liquidity_env: str = "正常量能"
    amount_ratio: float = 1.0
    up_ratio: float = 0.5
    bomb_ratio: float = 0.2
    weak_veto: bool = False

    base_position: int = 20
    money_adj: int = 0
    mainline_adj: int = 0
    risk_adj: int = 0
    liq_adj: int = 0
    position_target: int = 20
    position_normal_min: int = 15
    position_normal_max: int = 30
    position_confirm_cap: int = 35
    add_room: int = 0

    one_sentence: str = ""
    action: str = "维持"
    action_detail: str = ""
    add_conditions: List[str] = field(default_factory=list)
    reduce_conditions: List[str] = field(default_factory=list)
    short_term_hint: str = ""
    final_grade: str = "★★ 防守为主"

    emergency_brake: bool = False
    prev_position: Optional[int] = None
    confidence: int = 70

    overview: Optional[MarketOverview] = None
    v9_full_result: Optional[EngineV9Result] = None

    consistency_checks: Dict[str, str] = field(default_factory=dict)


# ============================================================
# 四、5维判断（复用 V9.0 底层数据）
# ============================================================

def _judge_market_trend(v9):
    s = v9.l1_index.score
    return "强" if s >= 65 else ("弱" if s < 40 else "中")

def _judge_money_effect(v9):
    s = v9.stle
    return "强" if s >= 65 else ("弱" if s < 45 else "中")

def _judge_mainline(v9):
    state = v9.l5_theme.details.get('mainline_state', 'NONE')
    mp = {"STRONG_SINGLE": "强", "STRONG_MULTI": "中", "ROTATION": "中", "WEAK": "弱"}
    return mp.get(state, "无")

def _judge_risk(v9):
    r = v9.l7_risk.score
    brake = v9.position.emergency_brake
    # 量价环境参与风险识别：放量下跌是重要风险增强信号
    liq_env = _judge_liquidity_env(v9)[0]
    if liq_env == "放量下跌":
        up = v9.l2_breadth.details.get('up_ratio', 50)
        zb = v9.l4_sentiment.details.get('broken_rate', 0)
        if up < 45 and zb > 30:
            return "高风险"
    return "高风险" if (r >= 50 or brake in ("LEVEL2","LEVEL3","LEVEL4")) else "正常风险"

def _judge_liquidity_env(v9):
    """量价环境判断：复用 V9.0 L6 流动性层的相对量能数据（5日/20日量比 + 涨跌方向）
    返回 (环境名, 调整值)。严格使用相对量能，不看绝对成交额。"""
    l6d = v9.l6_liquidity.details
    ratio = float(l6d.get('amount_ratio_5_20', 1.0) or 1.0)
    is_up = bool(l6d.get('is_up_day', True))

    # 量价环境判定（6种）：
    # 放量上涨：量比>1.2 + 上涨
    # 温和放量上涨：量比 1.1~1.2 + 上涨
    # 正常量能：量比 0.9~1.1
    # 缩量上涨：量比<0.9 + 上涨
    # 缩量下跌：量比<0.9 + 下跌
    # 放量下跌：量比>1.1 + 下跌
    if ratio >= 1.2:
        env = "放量上涨" if is_up else "放量下跌"
    elif ratio >= 1.1:
        env = "温和放量上涨" if is_up else "放量下跌"
    elif ratio > 0.9:
        env = "正常量能"
    else:
        env = "缩量上涨" if is_up else "缩量下跌"
    return env, LIQ_ADJ.get(env, 0), round(ratio, 2)

def _judge_dday(v9):
    mom = v9.l3_momentum.details.get('mom_state', '')
    sent = v9.l4_sentiment.details.get('sentiment_state', '')
    if mom == "加速" and sent == "情绪高涨":
        return 4, "D4 趋势延续"
    if mom == "正常" and sent in ("情绪高涨", "情绪温和"):
        return 3, "D3 首分窗口"
    if mom == "修复" and sent in ("情绪温和", "情绪中性"):
        return 2, "D2 确认期"
    if mom == "钝化" and sent == "情绪中性":
        return 5, "D5 高位"
    if mom in ("衰退", "急杀"):
        return 6, "D6 高位谨慎"
    if sent in ("情绪冰点", "情绪退潮"):
        return 7, "D7 冰点"
    return 3, "D3 中性"


# ============================================================
# 五、Regime 识别（V9→7种 + 5维投票修正）
# ============================================================

def _classify_regime(v9, trend, money, mainline):
    v9r = v9.regime.regime
    key = REGIME_MAP.get(v9r, "RANGE_NO_MAINLINE")

    # 5维投票修正
    if v9.stle >= 80 and money == "强" and mainline in ("强", "中"):
        key = "STRONG_TREND"
    elif trend == "强" and money == "强" and mainline in ("强", "中"):
        key = "STRONG_TREND"
    elif mainline in ("强", "中") and money in ("强", "中") and key == "RANGE_NO_MAINLINE":
        key = "RANGE_WITH_MAINLINE"
    elif mainline in ("无", "弱") and money == "弱" and key == "RANGE_WITH_MAINLINE":
        key = "RANGE_NO_MAINLINE"

    return key


# ============================================================
# 六、加减仓条件严格检查
# ============================================================

def _check_level1_add(v9):
    """一级加仓：至少满足2项"""
    up = v9.l2_breadth.details.get('up_ratio', 50)
    zb = v9.l4_sentiment.details.get('broken_rate', 0)
    mainline = v9.l5_theme.details.get('mainline_state', 'NONE')
    top1 = v9.l5_theme.details.get('top1_score', 0)
    count = 0
    if up > 55:
        count += 1
    if zb < 25:
        count += 1
    if mainline in ("STRONG_SINGLE", "STRONG_MULTI") and top1 > 60:
        count += 1
    return count >= 2

def _check_level2_add(v9):
    """二级加仓：至少满足3项，且风险正常"""
    if _judge_risk(v9) == "高风险":
        return False
    up = v9.l2_breadth.details.get('up_ratio', 50)
    zb = v9.l4_sentiment.details.get('broken_rate', 0)
    zt = v9.l4_sentiment.details.get('zt_count', 0)
    top1 = v9.l5_theme.details.get('top1_score', 0)
    count = 0
    if up > 60:
        count += 1
    if zb < 20:
        count += 1
    if zt > 60:
        count += 1
    if top1 > 80:
        count += 1
    return count >= 3

def _check_level1_reduce(v9):
    """一级减仓：至少满足2项"""
    up = v9.l2_breadth.details.get('up_ratio', 50)
    zb = v9.l4_sentiment.details.get('broken_rate', 0)
    dt = v9.l4_sentiment.details.get('dt_count', 0)
    mainline = v9.l5_theme.details.get('mainline_state', 'NONE')
    count = 0
    if up < 40:
        count += 1
    if zb > 35:
        count += 1
    if dt > 10:
        count += 1
    if mainline in ("WEAK", "NONE"):
        count += 1
    return count >= 2

def _check_level2_reduce(v9):
    """二级减仓：指数快速下跌+跌停增加+赚钱效应恶化"""
    ret1 = v9.l1_index.details.get('avg_ret1', 0)
    dt = v9.l4_sentiment.details.get('dt_count', 0)
    stle = v9.stle
    return ret1 <= -1.5 and dt >= 10 and stle <= 40


# ============================================================
# 七、文本生成
# ============================================================

def _one_sentence(regime_cn, trend, money, mainline, risk, liq_env="正常量能"):
    if regime_cn == "情绪冰点/退潮":
        return "情绪退潮，一票否决防守，等待上涨家数回暖再恢复。"
    if risk == "高风险":
        if regime_cn == "快速下跌":
            return "快速下跌行情，防守第一，等待企稳信号。"
        if regime_cn == "极度冰点":
            return "情绪极度冰点，控制抄底冲动，等修复确认。"
        return "风险偏高，防守为主，降低仓位和交易频率。"
    if liq_env == "放量下跌":
        return "放量下跌，资金在撤，防守优先。"
    if regime_cn == "强趋势":
        return "强趋势行情，敢于重仓，回踩就是加仓机会。"
    if regime_cn == "震荡有主线":
        if mainline == "强":
            return "指数一般，但赚钱效应和主线较强，聚焦主线结构性进攻。"
        if liq_env == "缩量上涨":
            return "震荡市有主线，但缩量反弹有效性一般，等放量确认。"
        return "震荡市有主线机会，只做主线，不追杂毛。"
    if regime_cn == "震荡无主线":
        return "震荡无主线，降低仓位和频率，快进快出不恋战。"
    if regime_cn == "高位分歧":
        return "高位分歧阶段，不追高，等待分歧后的真正强者。"
    if regime_cn == "冰点修复":
        return "冰点修复初期，小仓位试错，确认后再加仓。"
    return "市场中性，保持谨慎。"

def _final_grade(regime_cn, money, mainline, target, liq_env="正常量能"):
    """星级严格对应市场状态+赚钱+主线+量价+仓位"""
    if regime_cn == "强趋势" and money == "强" and mainline == "强" and liq_env in ("放量上涨", "温和放量上涨"):
        return "★★★★★ 全面进攻"
    if regime_cn == "强趋势":
        return "★★★★ 趋势进攻"
    if regime_cn == "震荡有主线" and money == "强" and mainline == "强" and liq_env in ("放量上涨", "温和放量上涨"):
        return "★★★★ 结构性进攻"
    if regime_cn == "震荡有主线":
        return "★★★ 结构性机会"
    if regime_cn == "冰点修复" and money in ("强", "中"):
        return "★★★ 修复中"
    if regime_cn == "高位分歧":
        return "★★★ 谨慎参与"
    if regime_cn == "震荡无主线":
        return "★★ 轻仓参与"
    if regime_cn == "冰点修复":
        return "★★ 逐步恢复"
    if regime_cn in ("快速下跌", "极度冰点", "情绪冰点/退潮"):
        return "★ 防守为主"
    return "★★★ 中性"


# ============================================================
# 八、仓位滞回
# ============================================================

def _apply_hysteresis(target, prev, emergency, action):
    if prev is None:
        return target
    diff = target - prev
    if diff < 0:
        if emergency or "减仓" in action:
            return target
        return prev - min(abs(diff), 10)
    if diff > 0:
        return prev + min(diff, 8)
    return target


# ============================================================
# 九、大盘基础行情提取
# ============================================================

def _extract_overview(data, v9):
    """从输入数据和V9结果中提取大盘概况"""
    ov = MarketOverview()

    # 指数数据（从 index_data 中取，key 是中文名称，value 是 DataFrame）
    index_data = getattr(data, 'index_data', {}) or {}
    if index_data:
        def _get_last(idx_name):
            """从DataFrame取最后一行的收盘和涨跌幅"""
            df = index_data.get(idx_name)
            if df is None or len(df) == 0:
                return 0.0, 0.0
            try:
                last = df.iloc[-1]
                close = float(last.get('close', last.get('Close', 0)))
                # 涨跌幅：如果有 pct_chg 就用，没有就从前一行算
                if 'pct_chg' in df.columns:
                    chg = float(last['pct_chg'])
                elif 'chg_pct' in df.columns:
                    chg = float(last['chg_pct'])
                elif len(df) >= 2:
                    prev = float(df.iloc[-2].get('close', 0))
                    chg = (close - prev) / prev * 100 if prev > 0 else 0.0
                else:
                    chg = 0.0
                return close, chg
            except Exception:
                return 0.0, 0.0

        ov.sz_price, ov.sz_chg = _get_last("上证指数")
        ov.hs300_price, ov.hs300_chg = _get_last("沪深300")
        ov.zz2000_price, ov.zz2000_chg = _get_last("中证2000")

    # 如果index_data没有，从v9结果中兜底
    if ov.sz_price == 0:
        ov.sz_price = v9.l1_index.details.get('sz_close', 0)
        ov.sz_chg = v9.l1_index.details.get('sz_chg', 0)

    # overview 数据
    overview = getattr(data, 'overview', None)
    if overview:
        ov.amount = overview.get('amount', overview.get('total_amount', 0)) or 0
        ov.up_count = int(overview.get('up_count', overview.get('up', 0)) or 0)
        ov.down_count = int(overview.get('down_count', overview.get('down', 0)) or 0)

    # 从 V9 l2_breadth 兜底上涨/下跌数
    if ov.up_count == 0 and v9.l2_breadth.details:
        up_ratio = v9.l2_breadth.details.get('up_ratio', 50)
        total = v9.l2_breadth.details.get('total_stocks', 5000)
        ov.up_count = int(total * up_ratio / 100)
        ov.down_count = int(total * (100 - up_ratio) / 100)

    # 涨跌停数据（优先 overview 涨停池/跌停池口径，与大盘概况一致；limit_stats 作兜底）
    if overview:
        ov.zt_count = int(overview.get('zt_count', 0) or 0)
        ov.dt_count = int(overview.get('dt_count', 0) or 0)
        ov.zb_count = int(overview.get('zb_count', 0) or 0)
        ov.zb_rate = float(overview.get('zb_rate', 0) or 0)
    if ov.zt_count == 0:
        limit_stats = getattr(data, 'limit_stats', None)
        if limit_stats:
            ov.zt_count = int(limit_stats.get('zt_count', limit_stats.get('limit_up', 0)) or 0)
            ov.dt_count = int(limit_stats.get('dt_count', limit_stats.get('limit_down', 0)) or 0)
            ov.zb_count = int(limit_stats.get('zb_count', limit_stats.get('broken_count', limit_stats.get('broken', 0))) or 0)
            ov.zb_rate = float(limit_stats.get('zb_rate', limit_stats.get('broken_rate', 0)) or 0)

    # 从 V9 l4_sentiment 兜底
    if ov.zt_count == 0:
        ov.zt_count = int(v9.l4_sentiment.details.get('zt_count', 0))
        ov.dt_count = int(v9.l4_sentiment.details.get('dt_count', 0))
        ov.zb_rate = float(v9.l4_sentiment.details.get('broken_rate', 0))
    if ov.zb_count == 0 and ov.zb_rate > 0 and ov.zt_count > 0:
        # 炸板数 = 涨停数 * 炸板率 / (1 - 炸板率)
        zb_est = ov.zt_count * ov.zb_rate / max(100 - ov.zb_rate, 1)
        ov.zb_count = int(round(zb_est))

    # 最高连板
    ov.max_height = int(getattr(data, 'max_limit_height', 0) or 0)

    # 成交额（亿）：如果是元级别转成亿
    if ov.amount > 100000:
        ov.amount = round(ov.amount / 100000000, 0)

    return ov


# ============================================================
# 十、6项一致性自动检查
# ============================================================

def _run_consistency_checks(r, v9):
    checks = {}

    # 检查1：仓位一致（目标在正常区间内，极端风险/一票否决除外）
    if r.emergency_brake or r.weak_veto:
        checks["仓位一致"] = "PASS"
    elif r.position_normal_min <= r.position_target <= r.position_normal_max:
        checks["仓位一致"] = "PASS"
    else:
        checks["仓位一致"] = "FAIL（目标%d%%，区间%d%%~%d%%）" % (
            r.position_target, r.position_normal_min, r.position_normal_max)

    # 检查2：量价一致（使用相对量能判断，且调整不超过±5%）
    if -5 <= r.liq_adj <= 5 and 0 < r.amount_ratio <= 3:
        checks["量价一致"] = "PASS"
    else:
        checks["量价一致"] = "FAIL（量价调整%d%%或量比%.2f异常）" % (r.liq_adj, r.amount_ratio)

    # 检查3：加仓一致
    if r.action == "加仓":
        if _check_level1_add(v9) or _check_level2_add(v9):
            checks["加仓一致"] = "PASS"
        else:
            checks["加仓一致"] = "FAIL（无满足的加仓条件）"
    else:
        checks["加仓一致"] = "PASS"

    # 检查4：D日一致（D1/D2/D3不能单独触发加仓）
    if r.action == "加仓" and r.d_day <= 3:
        # 加仓必须是因为满足一级/二级条件，而不是因为D日
        if _check_level1_add(v9) or _check_level2_add(v9):
            checks["D日一致"] = "PASS"
        else:
            checks["D日一致"] = "FAIL（D%d单独触发了加仓）" % r.d_day
    else:
        checks["D日一致"] = "PASS"

    # 检查5：风险优先
    if r.emergency_brake:
        if r.position_target <= 10:
            checks["风险优先"] = "PASS"
        else:
            checks["风险优先"] = "FAIL（紧急制动但仓位%d%%>10%%）" % r.position_target
    else:
        checks["风险优先"] = "PASS"

    # 检查6：星级一致
    star_count = r.final_grade.count("★")
    if r.market_regime == "强趋势":
        expected_min, expected_max = 4, 5
    elif r.market_regime in ("震荡有主线", "高位分歧", "冰点修复"):
        expected_min, expected_max = 3, 4
    elif r.market_regime in ("震荡无主线",):
        expected_min, expected_max = 2, 3
    elif r.market_regime in ("快速下跌", "极度冰点", "情绪冰点/退潮"):
        expected_min, expected_max = 1, 2
    else:
        expected_min, expected_max = 1, 2
    if expected_min <= star_count <= expected_max:
        checks["星级一致"] = "PASS"
    else:
        checks["星级一致"] = "FAIL（%s，星级%d）" % (r.market_regime, star_count)

    # 检查7：基础行情完整
    ov = r.overview
    if ov and ov.sz_price > 0 and ov.zt_count >= 0 and ov.amount > 0 and ov.max_height >= 0:
        checks["基础行情"] = "PASS"
    else:
        checks["基础行情"] = "FAIL"

    return checks


# ============================================================
# 十一、主引擎
# ============================================================

class PositionEngineV99:
    VERSION = "V9.9"

    def __init__(self):
        self.v9 = PositionEngineV9()

    def analyze(self, data, prev_pos=None):
        v9 = self.v9.analyze(data)
        r = PositionV99Result(prev_position=prev_pos, v9_full_result=v9)

        # 提取大盘基础行情
        r.overview = _extract_overview(data, v9)

        # 5维判断 + 量价环境
        r.market_trend = _judge_market_trend(v9)
        r.money_effect = _judge_money_effect(v9)
        r.mainline_strength = _judge_mainline(v9)
        r.risk_state = _judge_risk(v9)
        r.d_day, r.d_day_desc = _judge_dday(v9)
        r.liquidity_env, r.liq_adj, r.amount_ratio = _judge_liquidity_env(v9)

        # 极弱风控数据：上涨家数比例 + 炸板率比例（一票否决闸门用）
        up_ratio = v9.l2_breadth.details.get('up_ratio', 50) / 100.0
        bomb_ratio = v9.l4_sentiment.details.get('broken_rate', 0) / 100.0
        r.up_ratio = up_ratio
        r.bomb_ratio = bomb_ratio
        weak_veto = up_ratio < 0.35
        r.weak_veto = weak_veto
        # 一票否决时赚钱效应降级为"低"（上涨占比仅30%的盘面，不能再标"中"）
        if weak_veto:
            r.money_effect = "低"

        # Regime
        regime_key = _classify_regime(v9, r.market_trend, r.money_effect, r.mainline_strength)
        info = SEVEN_REGIMES[regime_key]
        r.market_regime = info["cn"]
        r.base_position = info["base"]
        normal_min = info["normal_min"]
        normal_max = info["normal_max"]
        r.position_confirm_cap = info["confirm_cap"]

        # 极弱风控一票否决：上涨家数<35% → 情绪冰点/退潮，无论其他因素，上限强制封死15%
        if weak_veto:
            r.market_regime = "情绪冰点/退潮"
            r.position_confirm_cap = min(r.position_confirm_cap, 15)
            normal_max = min(normal_max, 15)
            if normal_min > normal_max:
                normal_min = normal_max

        # 环境调整
        r.money_adj = MONEY_EFFECT_ADJ.get(r.money_effect, 0)
        r.mainline_adj = MAINLINE_ADJ.get(r.mainline_strength, 0)
        r.risk_adj = RISK_ADJ.get(r.risk_state, 0)
        r.liq_adj = LIQ_ADJ.get(r.liquidity_env, 0)

        # 基准目标 = 基础仓位 + 负向调整（正向调整不主动加，需确认后逐步兑现）
        neg_adj = min(0, r.money_adj) + min(0, r.mainline_adj) + min(0, r.risk_adj) + min(0, r.liq_adj)
        base_target = r.base_position + neg_adj
        # 潜在上限 = 基础仓位 + 全部调整（正向调整是潜在可兑现空间）
        potential_target = r.base_position + r.money_adj + r.mainline_adj + r.risk_adj + r.liq_adj
        potential_target = max(0, min(r.position_confirm_cap, potential_target))

        target = max(0, min(r.position_confirm_cap, base_target))

        # 极端风险闸门
        r.emergency_brake = _check_level2_reduce(v9) or (v9.position.emergency_brake in ("LEVEL3", "LEVEL4"))
        if r.emergency_brake:
            target = min(target, 10)
            potential_target = min(potential_target, 10)
            r.position_confirm_cap = min(r.position_confirm_cap, 10)
            normal_min = 0
            normal_max = 10

        # 动作判断（严格：条件不满足绝不输出"加仓"）
        # 一票否决：上涨家数<35% → 减仓/防守（优先于一切）
        if weak_veto:
            r.action = "减仓/防守"
            r.action_detail = "极弱风控（上涨%.0f%%＜35%%）" % (up_ratio * 100)
        elif up_ratio > 0.55 and bomb_ratio < 0.20:
            # 加仓必须赚钱效应确认：上涨>55% 且 炸板率<20%（修复"弱势却加仓"BUG）
            if _check_level2_add(v9):
                r.action = "加仓"
                r.action_detail = "5～10%"
                target = min(potential_target, normal_max, target + 10)
            elif _check_level1_add(v9):
                r.action = "加仓"
                r.action_detail = "5%"
                target = min(potential_target, normal_max, target + 5)
            else:
                r.action = "维持"
                r.action_detail = ""
        elif up_ratio < 0.40 or bomb_ratio > 0.30 or r.d_day >= 6:
            # 弱势/高炸板/高位谨慎 → 减仓控仓
            r.action = "减仓/控仓"
            r.action_detail = "弱势环境（涨%.0f%%/炸板%.0f%%/D%d）" % (up_ratio * 100, bomb_ratio * 100, r.d_day)
        elif _check_level2_reduce(v9):
            r.action = "快速减仓"
            r.action_detail = "10～20%"
            target = max(0, target - 15)
        elif _check_level1_reduce(v9):
            r.action = "减仓"
            r.action_detail = "5～10%"
            target = max(0, target - 8)
        else:
            r.action = "维持"
            r.action_detail = ""

        # 目标仓位计算：
        # - 维持：目标 = 基准目标（市场状态的合理持仓水平，作为指引）
        #   不等于立即调仓，只是告诉用户"这种环境下大概应该持有多少"
        # - 加仓：从prev逐步加，单日不超过8%
        # - 减仓：从prev快速减
        if r.action == "维持":
            target = base_target
        elif r.action == "加仓" and prev_pos is not None:
            max_add_target = min(potential_target, normal_max)
            diff = max_add_target - prev_pos
            if diff > 0:
                actual_add = min(diff, 8)
                target = prev_pos + actual_add
            else:
                target = prev_pos
                r.action = "维持"
                r.action_detail = ""
        elif r.action == "减仓" and prev_pos is not None:
            target = _apply_hysteresis(target, prev_pos, r.emergency_brake, r.action)
        elif r.action == "快速减仓" and prev_pos is not None:
            target = _apply_hysteresis(target, prev_pos, r.emergency_brake, r.action)
        elif r.action in ("减仓/防守", "减仓/控仓") and prev_pos is not None:
            # 目标 = 当前减10%，且不超过风控上限（一票否决15%/普通确认上限）
            cap = r.position_confirm_cap
            target = max(0, min(prev_pos - 10, cap))

        # 最终限制
        target = max(0, min(r.position_confirm_cap, target))
        target = int(round(target))

        # 目标仓位必须落在正常区间内（极端风险/一票否决除外）
        if not r.emergency_brake and not r.weak_veto and r.action != "快速减仓":
            if target < normal_min:
                # 低于正常区间下限：如果没有明显高风险，修正到下限；否则保持（说明状态需要降级）
                if r.risk_state != "高风险":
                    target = normal_min
            if target > normal_max:
                target = normal_max

        r.position_target = target
        r.position_normal_min = normal_min
        r.position_normal_max = normal_max

        # 可加仓空间
        r.add_room = r.position_confirm_cap - r.position_target

        # 一句话
        r.one_sentence = _one_sentence(
            r.market_regime, r.market_trend, r.money_effect,
            r.mainline_strength, r.risk_state, r.liquidity_env
        )

        # 加减仓条件
        r.add_conditions = self._build_add_conditions(v9)
        r.reduce_conditions = self._build_reduce_conditions(v9)

        # 短线策略
        r.short_term_hint = self._build_short_term_hint(r)

        # 评级（一票否决时直接锁定防守星级）
        if r.weak_veto:
            r.final_grade = "★ 防守为主"
        else:
            r.final_grade = _final_grade(r.market_regime, r.money_effect, r.mainline_strength, r.position_target, r.liquidity_env)

        # 置信度
        r.confidence = int(v9.regime.confidence * 100)

        # 一致性检查
        r.consistency_checks = _run_consistency_checks(r, v9)

        return r

    def _build_add_conditions(self, v9):
        conds = []
        up = v9.l2_breadth.details.get('up_ratio', 50)
        zb = v9.l4_sentiment.details.get('broken_rate', 0)
        if up <= 55:
            conds.append("上涨家数 >55%%（当前 %.0f%%）" % up)
        if zb >= 25:
            conds.append("炸板率 <25%%（当前 %.0f%%）" % zb)
        conds.append("主线强度继续提升")
        return conds[:3]

    def _build_reduce_conditions(self, v9):
        conds = []
        up = v9.l2_breadth.details.get('up_ratio', 50)
        zb = v9.l4_sentiment.details.get('broken_rate', 0)
        if up > 40:
            conds.append("上涨家数 <40%%（当前 %.0f%%）" % up)
        if zb < 35:
            conds.append("炸板率 >35%%（当前 %.0f%%）" % zb)
        conds.append("主线龙头破位或退潮")
        return conds[:3]

    def _build_short_term_hint(self, r):
        if r.market_regime in ("快速下跌", "极度冰点", "情绪冰点/退潮"):
            return "空仓观望为主，不抄底"
        if r.mainline_strength == "无":
            return "无主线，少动多看"
        if r.d_day <= 2:
            return "D1-D2：确认后小仓位试错"
        if r.d_day == 3:
            return "D3：首分低吸重点窗口"
        if r.d_day in (4, 5):
            return "D4-D5：趋势延续，持有或回踩加仓"
        if r.d_day in (6, 7):
            return "D6-D7：高位谨慎，不追涨"
        return "等待新一轮启动信号"

    def generate_report(self, r):
        L = []
        L.append("═" * 42)
        L.append("       A股短线仓位建议  V9.9")
        L.append("═" * 42)

        # 大盘概况
        ov = r.overview
        if ov:
            def _fmt_chg(v):
                return ("+%.2f%%" % v) if v >= 0 else ("%.2f%%" % v)
            sz_line = ""
            if ov.sz_price > 0:
                sz_line = "上证%.2f（%s）" % (ov.sz_price, _fmt_chg(ov.sz_chg))
            hs_line = ""
            if ov.hs300_price > 0:
                hs_line = "  沪深300 %.2f（%s）" % (ov.hs300_price, _fmt_chg(ov.hs300_chg))
            zz_line = ""
            if ov.zz2000_price > 0:
                zz_line = "  中证2000 %.2f（%s）" % (ov.zz2000_price, _fmt_chg(ov.zz2000_chg))
            L.append(sz_line + hs_line + zz_line)

            amount_line = ""
            if ov.amount > 0:
                amount_line = "成交%.0f亿" % ov.amount
            ud_line = ""
            if ov.up_count > 0 or ov.down_count > 0:
                ud_line = "  涨%d/跌%d" % (ov.up_count, ov.down_count)
            ztdt_line = "  涨%d/跌%d" % (ov.zt_count, ov.dt_count)
            L.append(amount_line + ud_line + ztdt_line)

            zb_line = ""
            if ov.zb_rate > 0:
                zb_line = "炸板%d（%.1f%%）" % (ov.zb_count, ov.zb_rate)
            lb_line = ""
            if ov.max_height > 0:
                lb_line = "  最高连板%d板" % ov.max_height
            L.append(zb_line + lb_line)
        else:
            L.append("（暂无大盘数据）")

        L.append("─" * 42)

        # 市场判断
        L.append("市场：%s | 赚钱：%s | 主线：%s" % (r.market_regime, r.money_effect, r.mainline_strength))
        liq_line = "量价：%s" % r.liquidity_env
        if r.amount_ratio > 0:
            liq_line += "（量比%.2f）" % r.amount_ratio
        L.append("%s | 风险：%s | 节奏：%s" % (liq_line, r.risk_state, r.d_day_desc))
        L.append("")
        L.append("当前目标：%d%%   可加仓空间：%d%%" % (r.position_target, r.add_room))
        L.append("正常区间：%d%%～%d%%   确认上限：%d%%" % (r.position_normal_min, r.position_normal_max, r.position_confirm_cap))
        L.append("")
        L.append("一句话：%s" % r.one_sentence)
        L.append("─" * 42)

        # 动作
        action_line = "【动作】%s" % r.action
        if r.action_detail:
            action_line += "（%s）" % r.action_detail
        L.append(action_line)

        if r.action == "维持" and not _check_level1_add(r.v9_full_result):
            L.append("暂不加仓，等待市场进一步确认。")

        L.append("")
        L.append("加仓：")
        for i, c in enumerate(r.add_conditions, 1):
            L.append("  %s %s" % (_circled_num(i), c))
        L.append("减仓：")
        for i, c in enumerate(r.reduce_conditions, 1):
            L.append("  %s %s" % (_circled_num(i), c))

        L.append("─" * 42)

        # 策略
        L.append("【策略】%s" % r.short_term_hint)
        if r.weak_veto:
            L.append("主线：少做  非主线：不做")
        elif r.mainline_strength in ("强", "中"):
            L.append("主线：做    非主线：少做")
        else:
            L.append("主线：少做  非主线：不做")
        L.append("最终：%s" % r.final_grade)
        L.append("═" * 42)
        return "\n".join(L)


def _circled_num(n):
    """数字转带圈字符 ①②③..."""
    if 1 <= n <= 20:
        return chr(0x2460 + n - 1)
    return "%d." % n


# ============================================================
# 十二、便捷接口
# ============================================================

def run_v99(data, prev_pos=None):
    return PositionEngineV99().analyze(data, prev_pos)

def run_v99_report(data, prev_pos=None):
    e = PositionEngineV99()
    r = e.analyze(data, prev_pos)
    return e.generate_report(r), r


# 兼容旧名
PositionEngineV98 = PositionEngineV99
PositionV98Result = PositionV99Result
PositionEngineV97 = PositionEngineV99
PositionV97Result = PositionV99Result
PositionEngineV96 = PositionEngineV99
PositionV96Result = PositionV99Result
