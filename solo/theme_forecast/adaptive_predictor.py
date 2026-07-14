# -*- coding: utf-8 -*-
"""
状态自适应预测器

根据市场状态切换因子权重：
- 抱团上涨：动量延续为主，追强势主题
- 抱团下跌：反转因子为主（动量/集中度/RS斜率取100-score）
- 抱团震荡：时序因子为主（RS斜率/领先滞后）
- 轮动：按主题类型分化（动量类/反转类/中性类）
- 普跌：反转因子为主，寻找超跌反弹
- 普涨：低RS补涨因子为主

核心改进 vs 原predictor.py：
1. 集成市场状态识别（6种状态，区分抱团上涨/下跌/震荡）
2. 集成3个时序因子（RS斜率/资金集中度/领先滞后）
3. 因子权重随状态切换
4. 抱团下跌期对反向因子取100-score（IC反转）
5. 轮动市按主题类型分化权重（动量类/反转类/中性类）
"""
import json
import numpy as np
from pathlib import Path

from theme_forecast.predictor import (
    load_prob_lookup, lookup_future_prob, FACTOR_NAMES,
    FACTOR_TO_LOOKUP_KEY, PROB_LOOKUP_PATH,
)
from theme_forecast.regime_detector import (
    detect_regime, get_regime_factor_weights, format_regime_report,
    ROTATION_THEME_WEIGHTS, REGIME_REVERSE_KEYS, ROTATION_REVERSE_KEYS,
)


# 时序因子中文名
TIMESERIES_FACTOR_NAMES = {
    "rs_slope": "RS斜率",
    "concentration_change": "资金集中度",
    "leader_lag": "领先滞后",
}

# 合并因子名
ALL_FACTOR_NAMES = {**FACTOR_NAMES, **TIMESERIES_FACTOR_NAMES}

# 主题分类表路径
THEME_CLASS_PATH = Path(__file__).resolve().parent / "output" / "theme_class_rotation.json"


def load_theme_class_map() -> dict:
    """加载主题分类表（动量类/反转类/中性类）"""
    if not THEME_CLASS_PATH.exists():
        return {}
    with open(THEME_CLASS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {item["theme"]: item["theme_class"] for item in data}


def fuse_probability_adaptive(factors: dict, regime_info: dict,
                                prob_lookup: dict = None,
                                theme_name: str = "",
                                theme_class_map: dict = None) -> dict:
    """
    状态自适应概率融合

    Args:
        factors: 全部因子（含时序因子）
        regime_info: 市场状态信息
        prob_lookup: 条件概率查表
        theme_name: 主题名（轮动市用于主题分化权重）
        theme_class_map: 主题分类表（{theme: "动量类/反转类/中性类"}）

    Returns:
        {
            "probability": 当前状态概率,
            "direction": 方向,
            "regime": 市场状态,
            "future_probs": 未来概率,
            "factor_details": 因子明细,
            "top_signals": 看涨信号,
            "risk_signals": 风险信号,
            "adaptive_weights": 使用的权重,
            "reverse_keys": 反转的因子,
            "theme_class": 主题类别（轮动市）,
        }
    """
    if prob_lookup is None:
        prob_lookup = load_prob_lookup()

    regime = regime_info["regime"]

    # 确定权重和反转因子
    reverse_keys = []
    theme_class = None

    if regime == "抱团下跌":
        weights = get_regime_factor_weights("抱团下跌")
        reverse_keys = REGIME_REVERSE_KEYS.get("抱团下跌", [])
    elif regime == "轮动":
        # 轮动市：按主题类型分化权重
        if theme_class_map is None:
            theme_class_map = load_theme_class_map()
        theme_class = theme_class_map.get(theme_name, "中性类")
        weights = ROTATION_THEME_WEIGHTS.get(theme_class, ROTATION_THEME_WEIGHTS["中性类"])
        if theme_class == "反转类":
            reverse_keys = ROTATION_REVERSE_KEYS
    else:
        # 其他状态（抱团上涨/抱团震荡/普跌/普涨）
        weights = get_regime_factor_weights(regime)

    # 计算加权得分（应用因子方向反转）
    total_weight = 0
    weighted_sum = 0
    factor_details = []
    top_signals = []
    risk_signals = []

    # 构建因子分数副本（用于反转）
    factor_scores = {}
    for key in weights.keys():
        factor_data = factors.get(key, {})
        score = factor_data.get("score", 50)
        factor_scores[key] = score

    # 应用因子方向反转
    for reverse_key in reverse_keys:
        if reverse_key in factor_scores:
            factor_scores[reverse_key] = 100 - factor_scores[reverse_key]

    for key, weight in weights.items():
        if weight == 0:
            continue  # 该状态下降权的因子

        score = factor_scores.get(key, 50)
        signal = factors.get(key, {}).get("signal", "")
        is_reversed = key in reverse_keys

        weighted_sum += score * weight
        total_weight += weight

        factor_details.append({
            "key": key,
            "name": ALL_FACTOR_NAMES.get(key, key),
            "weight": weight,
            "score": score,
            "signal": signal,
            "active": True,
            "reversed": is_reversed,
        })

        if score >= 70 and signal:
            top_signals.append(f"{ALL_FACTOR_NAMES.get(key, key)}: {signal}({score}{'↺' if is_reversed else ''})")
        if score <= 30 and signal:
            risk_signals.append(f"{ALL_FACTOR_NAMES.get(key, key)}: {signal}({score}{'↺' if is_reversed else ''})")

    # 标记被降权的因子
    for key in factors.keys():
        if key not in weights or weights[key] == 0:
            factor_data = factors[key]
            factor_details.append({
                "key": key,
                "name": ALL_FACTOR_NAMES.get(key, key),
                "weight": 0,
                "score": factor_data.get("score", 50),
                "signal": factor_data.get("signal", ""),
                "active": False,
                "reversed": False,
            })

    probability = weighted_sum / total_weight if total_weight > 0 else 50

    # 方向判断
    if probability >= 70:
        direction = "看涨"
    elif probability >= 60:
        direction = "偏多"
    elif probability >= 45:
        direction = "中性"
    elif probability >= 35:
        direction = "偏空"
    else:
        direction = "看跌"

    # 计算未来概率（仅用有查表数据的因子，但权重用自适应权重）
    # 注意：未来概率查表时用反转后的score
    reversed_factors = {}
    for key, factor_data in factors.items():
        score = factor_data.get("score", 50)
        if key in reverse_keys:
            score = 100 - score
        reversed_factors[key] = {**factor_data, "score": score}

    future_probs = _calc_future_prob_adaptive(reversed_factors, weights, prob_lookup)

    return {
        "probability": round(probability, 1),
        "direction": direction,
        "regime": regime,
        "regime_info": regime_info,
        "future_probs": future_probs,
        "factor_details": sorted(factor_details, key=lambda x: -x["score"] if x["active"] else 1),
        "top_signals": top_signals,
        "risk_signals": risk_signals,
        "adaptive_weights": weights,
        "reverse_keys": reverse_keys,
        "theme_class": theme_class,
    }


def _calc_future_prob_adaptive(factors: dict, weights: dict,
                                 prob_lookup: dict, horizons: list = None) -> dict:
    """
    基于自适应权重的未来概率计算

    与原predictor.calc_future_probability的区别：
    - 使用自适应权重而非固定权重
    - 因子score已在外部应用了反转逻辑
    """
    if horizons is None:
        horizons = ["3d", "5d", "10d"]
    if not prob_lookup:
        return {}

    result = {}

    for h in horizons:
        weighted_prob = 0
        total_weight = 0
        details = []
        valid_factors = 0

        for factor_key, weight in weights.items():
            if weight == 0:
                continue
            if factor_key not in FACTOR_TO_LOOKUP_KEY:
                continue  # 时序因子暂无查表数据

            factor_data = factors.get(factor_key, {})
            score = factor_data.get("score", 50)

            up_prob, avg_ret, n_samples = lookup_future_prob(factor_key, score, h, prob_lookup)
            if up_prob is None:
                continue

            reliability_weight = min(1.0, n_samples / 100)
            effective_weight = weight * reliability_weight

            weighted_prob += up_prob * effective_weight
            total_weight += effective_weight
            valid_factors += 1

            details.append({
                "factor": factor_key,
                "name": ALL_FACTOR_NAMES.get(factor_key, factor_key),
                "score": score,
                "up_prob": up_prob,
                "avg_ret": avg_ret,
                "n_samples": n_samples,
                "weight": weight,
            })

        if total_weight > 0 and valid_factors > 0:
            final_prob = weighted_prob / total_weight
            probs = [d["up_prob"] for d in details]
            prob_std = float(np.std(probs)) if len(probs) > 1 else 0

            if valid_factors >= 5 and prob_std < 10:
                confidence = "high"
            elif valid_factors >= 3 and prob_std < 20:
                confidence = "medium"
            else:
                confidence = "low"

            weighted_ret = sum(d["avg_ret"] * d["weight"] for d in details if d["avg_ret"] is not None)
            total_ret_weight = sum(d["weight"] for d in details if d["avg_ret"] is not None)
            avg_ret = weighted_ret / total_ret_weight if total_ret_weight > 0 else 0

            result[h] = {
                "prob": round(final_prob, 1),
                "avg_ret": round(avg_ret, 2),
                "confidence": confidence,
                "valid_factors": valid_factors,
                "prob_std": round(prob_std, 1),
                "details": sorted(details, key=lambda x: -abs(x["up_prob"] - 50)),
            }

    return result


def format_adaptive_report(theme_name: str, prediction: dict, stock_count: int) -> str:
    """格式化状态自适应预测报告"""
    lines = []
    prob = prediction["probability"]
    direction = prediction["direction"]
    regime = prediction["regime"]
    future_probs = prediction.get("future_probs", {})
    theme_class = prediction.get("theme_class")

    emoji_map = {
        "抱团上涨": "🎯", "抱团下跌": "🎯", "抱团震荡": "🎯",
        "轮动": "🔄", "普跌": "📉", "普涨": "📈",
    }

    lines.append(f"{'='*60}")
    lines.append(f"【{theme_name}】")
    regime_line = f"  市场状态: {emoji_map.get(regime, '')} {regime}"
    if theme_class:
        regime_line += f" | 主题类型: {theme_class}"
    lines.append(regime_line)
    lines.append(f"  当前概率: {prob}% {direction}")

    # 未来概率
    if future_probs:
        lines.append("")
        lines.append("  ── 未来上涨概率（状态自适应） ──")
        for h in ["3d", "5d", "10d"]:
            if h not in future_probs:
                continue
            fp = future_probs[h]
            h_label = {"3d": "3日", "5d": "5日", "10d": "10日"}[h]
            prob_h = fp["prob"]
            if prob_h >= 60:
                mark = "▲"
                action = "看涨"
            elif prob_h >= 55:
                mark = "△"
                action = "偏多"
            elif prob_h >= 45:
                mark = "─"
                action = "中性"
            elif prob_h >= 40:
                mark = "▽"
                action = "偏空"
            else:
                mark = "▼"
                action = "看跌"

            conf_label = {"high": "高", "medium": "中", "low": "低"}[fp["confidence"]]
            lines.append(f"  {mark} 未来{h_label}: {prob_h}% | 预期{fp['avg_ret']:+.2f}% | 置信{conf_label} | {action}")

    lines.append("")

    # 因子明细（显示是否激活、是否反转）
    lines.append("  ── 因子明细（状态自适应权重） ──")
    for f in prediction["factor_details"]:
        score = f["score"]
        weight = f["weight"]
        active = f["active"]
        reversed_flag = f.get("reversed", False)
        if active:
            indicator = "▲" if score >= 60 else ("▼" if score <= 40 else "─")
            reverse_mark = "↺" if reversed_flag else ""
            lines.append(f"  {indicator} {f['name']:<10} {score:>3} (权重{weight:>2}%){reverse_mark} {f['signal']}")
        else:
            lines.append(f"  · {f['name']:<10} {score:>3} (已降权)        {f['signal']}")

    # 信号
    if prediction["top_signals"]:
        lines.append("")
        lines.append("  ── 看涨信号 ──")
        for s in prediction["top_signals"]:
            lines.append(f"  + {s}")
    if prediction["risk_signals"]:
        lines.append("")
        lines.append("  ── 风险信号 ──")
        for s in prediction["risk_signals"]:
            lines.append(f"  ! {s}")

    return "\n".join(lines)
