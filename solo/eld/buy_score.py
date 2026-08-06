# -*- coding: utf-8 -*-
"""
Buy Score 交易价值评分（研究价值与交易价值解耦）

设计初衷：V2/Research Score 回答"谁最值得研究"（业绩+预期+趋势+主题），
但"研究价值第一 ≠ 应该优先买入"（如 4 连板高位股风范股份 V2 第一却不可买）。
Buy Score 独立回答"谁今天风险收益比最好、真正可以买"。

权重（0-100）:
    30% 买点质量     - 已有 buy_quality_score（乖离/回踩/缩量/企稳/机构）
    25% 乖离率       - 最佳区-2%~8%满分，追高线15%后快速衰减
    20% 机构状态     - 吸筹100/启动80/洗盘60/派发0
    15% 连板惩罚     - 0板100 / 1板70 / 2板45 / 3板20 / ≥4板0（连板末期禁止追）
    10% 风险收益比   - 事件强度(上行空间) × 乖离(下行风险) 组合

分级:
    >80    ✅ 推荐买
    60-80  👀 观察
    40-60  ⏳ 等回踩
    <40    ❌ 禁止追高
"""
from __future__ import annotations

from typing import Optional

# 乖离率评分（25%权重输入）
_BIAS_OPT_MIN, _BIAS_OPT_MAX = -2.0, 8.0
_BIAS_CHASE = 15.0  # 追高线


def count_consecutive_limit_ups(ts_code: str, daily_data: list) -> int:
    """计算最近连续涨停天数（含当日）。

    板块涨停阈值: 主板10% / 双创20%（按代码前缀判断），从最新日向前数。
    """
    if not daily_data or len(daily_data) < 3:
        return 0
    if ts_code.startswith(("300", "301", "688", "689")):
        limit_pct = 19.5
    else:
        limit_pct = 9.5
    n = 0
    for d in sorted(daily_data, key=lambda x: x.trade_date)[::-1]:
        if d.pre_close and d.pre_close > 0:
            chg = (d.close / d.pre_close - 1) * 100
            if chg >= limit_pct - 0.5:
                n += 1
            else:
                break
        else:
            break
    return n


def _bias_score(bias: float) -> float:
    """乖离率评分: 最佳区-2~8满分100；8~15线性降到80；15以上每多5%减10；
    深负乖离(破位)同样减分: -2→100, 每多负1%减3.75，封底0"""
    if _BIAS_OPT_MIN <= bias <= _BIAS_OPT_MAX:
        return 100.0
    if bias < _BIAS_OPT_MIN:
        # 深回调/破位区: 太深说明弱势崩盘风险高，减分
        return max(0.0, 100.0 - (_BIAS_OPT_MIN - bias) * 3.75)
    if bias <= _BIAS_CHASE:
        # 8~15 → 100~80
        return 100.0 - (bias - _BIAS_OPT_MAX) * (20.0 / (_BIAS_CHASE - _BIAS_OPT_MAX))
    # >15 追高区: 每5个点减10，封底0
    return max(0.0, 80.0 - (bias - _BIAS_CHASE) * 2.0)


def _inst_score(inst_state: str) -> float:
    return {"吸筹": 100.0, "启动": 80.0, "洗盘": 60.0, "派发": 0.0}.get(inst_state, 40.0)


def _cons_score(n_cons: int) -> float:
    return {0: 100.0, 1: 70.0, 2: 45.0, 3: 20.0}.get(n_cons, 0.0)


def _rr_score(event_quality: float, bias: float) -> float:
    """风险收益比: 上行空间(事件强度) vs 下行风险(乖离) 各占一半。
    乖离只取正值计入下行风险（负乖离属弱势信号，不因跌得深反而给满分）"""
    up = min(100.0, max(0.0, event_quality))
    down = max(0.0, 100.0 - max(0.0, bias) * 2.0)  # 乖离0→100分，乖离50→0分
    return 0.5 * up + 0.5 * down


def buy_score_level(score: float) -> str:
    if score > 80:
        return "推荐买"
    if score >= 60:
        return "观察"
    if score >= 40:
        return "等回踩"
    return "禁止追高"


def compute_buy_score(
    ts_code: str,
    daily_data: list,
    buy_quality: float,
    bias: float,
    inst_state: str,
    event_quality: float,
) -> tuple[float, str, dict]:
    """计算 Buy Score。

    Args:
        ts_code: 股票代码（用于判断板块涨停阈值）。
        daily_data: 日线数据列表（含 trade_date/close/pre_close）。
        buy_quality: 买点质量分（0-100）。
        bias: 乖离率((close-ma20)/ma20*100)。
        inst_state: 机构状态（吸筹/启动/洗盘/派发）。
        event_quality: 事件质量分（0-100，代表上行空间）。

    Returns:
        (buy_score, level, breakdown)
    """
    q = min(100.0, max(0.0, float(buy_quality or 0)))
    b = float(bias or 0)
    inst = _inst_score(inst_state or "")
    cons = count_consecutive_limit_ups(ts_code, daily_data)
    cs = _cons_score(cons)
    rr = _rr_score(event_quality, b)

    score = 0.30 * q + 0.25 * _bias_score(b) + 0.20 * inst + 0.15 * cs + 0.10 * rr
    score = min(100.0, score)
    level = buy_score_level(score)
    breakdown = {
        "quality": round(q, 1), "bias_score": round(_bias_score(b), 1),
        "inst_score": round(inst, 1), "cons_count": cons, "cons_score": round(cs, 1),
        "rr_score": round(rr, 1),
    }
    return round(score, 1), level, breakdown
