# -*- coding: utf-8 -*-
"""
概率融合器

将10个因子分数加权融合为涨跌概率
同时基于历史条件概率回测，输出未来N日上涨概率
"""
import json
import os
import numpy as np
from pathlib import Path

# 条件概率查表路径
PROB_LOOKUP_DIR = Path(__file__).resolve().parent / "output"
PROB_LOOKUP_PATH = PROB_LOOKUP_DIR / "prob_lookup.json"


# ====================================================================
# 分数扩展：将50附近的分数拉开，提高因子区分度
# ====================================================================
def expand_factor_score(score: float, midpoint: float = 50.0,
                         strength: float = 1.4) -> float:
    """
    对因子分数进行非线性扩展，拉开高分和低分之间的差距。

    与 expand_training.py 中的 _expand_score_vector 保持一致，
    此处为标量版本。

    Args:
        score: 原始因子分数（通常0-100）
        midpoint: 中心点（默认50）
        strength: 扩展强度（默认1.4）

    Returns:
        扩展后的分数
    """
    if score is None or np.isnan(score):
        return 50.0
    deviation = score - midpoint
    abs_dev = abs(deviation)
    if abs_dev <= 10:
        s = 1.2
    elif abs_dev <= 20:
        s = (1.2 + strength) / 2
    else:
        s = strength
    return midpoint + deviation * s * 1.5

# 回测因子→查表key的映射（与prob_lookup.json的key对应）
FACTOR_TO_LOOKUP_KEY = {
    "relative_strength": "f_rs",
    "momentum_acceleration": "f_mom",
    "adx_trend": "f_adx",
    "synergy_coefficient": "f_syn",
    "leadership_divergence": "f_div",
    "breakout_ratio": "f_brk",
}

# 因子权重配置（总权重100）
FACTOR_WEIGHTS = {
    # 动量层 30%
    "relative_strength": 12,
    "momentum_acceleration": 10,
    "adx_trend": 8,
    # 协同度层 25%
    "synergy_coefficient": 10,
    "leadership_divergence": 8,
    "breakout_ratio": 7,
    # 情绪层 20%
    "limit_up_ladder": 12,
    "turnover_distribution": 8,
    # 资金流层 25%
    "etf_net_inflow": 15,
    "north_flow": 10,
}

# 因子中文名
FACTOR_NAMES = {
    "relative_strength": "相对强度",
    "momentum_acceleration": "动量加速度",
    "adx_trend": "ADX趋势",
    "synergy_coefficient": "协同度",
    "leadership_divergence": "分化度",
    "breakout_ratio": "突破比例",
    "limit_up_ladder": "涨停梯队",
    "turnover_distribution": "换手率分布",
    "etf_net_inflow": "ETF净申购",
    "north_flow": "北向资金",
}

# 因子所属层级
FACTOR_LAYERS = {
    "relative_strength": "动量",
    "momentum_acceleration": "动量",
    "adx_trend": "动量",
    "synergy_coefficient": "协同",
    "leadership_divergence": "协同",
    "breakout_ratio": "协同",
    "limit_up_ladder": "情绪",
    "turnover_distribution": "情绪",
    "etf_net_inflow": "资金",
    "north_flow": "资金",
}


def load_prob_lookup() -> dict:
    """加载历史条件概率查表"""
    if not PROB_LOOKUP_PATH.exists():
        return {}
    with open(PROB_LOOKUP_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def lookup_future_prob(factor_key: str, score: float, horizon: str,
                       prob_lookup: dict) -> tuple:
    """
    查表获取某因子在某分值时的未来上涨概率

    Args:
        factor_key: 因子key（如 "relative_strength"）
        score: 因子分值
        horizon: "3d" / "5d" / "10d"
        prob_lookup: 查表字典

    Returns:
        (up_prob, avg_ret, n_samples)
    """
    lookup_key = FACTOR_TO_LOOKUP_KEY.get(factor_key)
    if not lookup_key or lookup_key not in prob_lookup:
        return (None, None, 0)

    horizon_data = prob_lookup[lookup_key].get(horizon, [])
    if not horizon_data:
        return (None, None, 0)

    # 找到score所在的bin
    for bin_info in horizon_data:
        if bin_info["bin_min"] <= score <= bin_info["bin_max"]:
            return (bin_info["up_prob"], bin_info["avg_ret"], bin_info["n_samples"])

    # 如果超出范围，取最近的bin
    if score < horizon_data[0]["bin_min"]:
        return (horizon_data[0]["up_prob"], horizon_data[0]["avg_ret"], horizon_data[0]["n_samples"])
    if score > horizon_data[-1]["bin_max"]:
        return (horizon_data[-1]["up_prob"], horizon_data[-1]["avg_ret"], horizon_data[-1]["n_samples"])

    return (None, None, 0)


def calc_future_probability(factors: dict, prob_lookup: dict = None,
                             horizons: list = None) -> dict:
    """
    基于历史条件概率，计算未来N日上涨概率

    核心逻辑：
    - 对每个有查表数据的因子，查表得到该因子当前分值对应的未来上涨概率
    - 按因子权重加权平均，得到综合未来上涨概率
    - 这是真正的"未来概率预测"，而非当前的"状态描述"

    Returns:
        {
            "3d": {"prob": 65.2, "avg_ret": 1.5, "confidence": "high", "details": [...]},
            "5d": {...},
            "10d": {...},
        }
    """
    if prob_lookup is None:
        prob_lookup = load_prob_lookup()
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

        for factor_key, weight in FACTOR_WEIGHTS.items():
            if factor_key not in FACTOR_TO_LOOKUP_KEY:
                continue  # 跳过没有查表数据的因子（情绪/资金流）

            factor_data = factors.get(factor_key, {})
            score = factor_data.get("score", 50)

            up_prob, avg_ret, n_samples = lookup_future_prob(factor_key, score, h, prob_lookup)
            if up_prob is None:
                continue

            # 权重调整：样本数越多权重越高（可靠性加权）
            reliability_weight = min(1.0, n_samples / 100)  # 100样本为满权重
            effective_weight = weight * reliability_weight

            weighted_prob += up_prob * effective_weight
            total_weight += effective_weight
            valid_factors += 1

            details.append({
                "factor": factor_key,
                "name": FACTOR_NAMES.get(factor_key, factor_key),
                "score": score,
                "up_prob": up_prob,
                "avg_ret": avg_ret,
                "n_samples": n_samples,
                "weight": weight,
            })

        if total_weight > 0 and valid_factors > 0:
            final_prob = weighted_prob / total_weight
            # 计算置信度：有效因子数 + 各因子一致性
            probs = [d["up_prob"] for d in details]
            prob_std = float(np.std(probs)) if len(probs) > 1 else 0

            if valid_factors >= 5 and prob_std < 10:
                confidence = "high"
            elif valid_factors >= 3 and prob_std < 20:
                confidence = "medium"
            else:
                confidence = "low"

            # 加权平均收益
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


def fuse_probability(factors: dict) -> dict:
    """
    将10个因子分数加权融合为涨跌概率（当前状态描述）
    同时计算未来N日上涨概率（历史条件概率预测）
    """
    total_weight = 0
    weighted_sum = 0
    layer_scores = {}
    layer_weights = {}
    factor_details = []
    top_signals = []
    risk_signals = []

    for key, weight in FACTOR_WEIGHTS.items():
        factor_data = factors.get(key, {})
        raw_score = factor_data.get("score", 50)
        signal = factor_data.get("signal", "")

        # 分数扩展：拉开高分和低分之间的差距
        score = expand_factor_score(raw_score)

        weighted_sum += score * weight
        total_weight += weight

        layer = FACTOR_LAYERS.get(key, "其他")
        layer_scores.setdefault(layer, []).append(score)
        layer_weights[layer] = layer_weights.get(layer, 0) + weight

        factor_details.append({
            "key": key,
            "name": FACTOR_NAMES.get(key, key),
            "layer": layer,
            "weight": weight,
            "score": score,
            "raw_score": raw_score,
            "signal": signal,
        })

        # 收集信号
        if score >= 70 and signal:
            top_signals.append(f"{FACTOR_NAMES.get(key, key)}: {signal}({score})")
        if score <= 30 and signal:
            risk_signals.append(f"{FACTOR_NAMES.get(key, key)}: {signal}({score})")

    probability = weighted_sum / total_weight if total_weight > 0 else 50

    # 层级平均分
    layer_avg = {layer: round(sum(scores) / len(scores), 1) for layer, scores in layer_scores.items()}

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

    # 计算未来概率（核心新增）
    future_probs = calc_future_probability(factors)

    return {
        "probability": round(probability, 1),
        "direction": direction,
        "weighted_score": round(weighted_sum / total_weight, 2) if total_weight > 0 else 50,
        "layer_scores": layer_avg,
        "factor_details": sorted(factor_details, key=lambda x: -x["score"]),
        "top_signals": top_signals,
        "risk_signals": risk_signals,
        "future_probs": future_probs,  # 新增：未来上涨概率
    }


def format_report(theme_name: str, theme_info: dict, prediction: dict, stock_count: int) -> str:
    """格式化单主题预测报告"""
    lines = []
    prob = prediction["probability"]
    direction = prediction["direction"]
    future_probs = prediction.get("future_probs", {})

    # 概率条
    bar_len = int(prob / 5)
    bar = "█" * bar_len + "░" * (20 - bar_len)

    lines.append(f"{'='*60}")
    lines.append(f"【{theme_name}】")
    lines.append(f"  当前状态: {prob}% {direction}")
    lines.append(f"  {bar} {prob}%")
    lines.append(f"  成份股: {stock_count}只")

    # 未来上涨概率（核心新增）
    if future_probs:
        lines.append("")
        lines.append("  ── 未来上涨概率（历史回测） ──")
        for h in ["3d", "5d", "10d"]:
            if h not in future_probs:
                continue
            fp = future_probs[h]
            h_label = {"3d": "3日", "5d": "5日", "10d": "10日"}[h]
            prob_h = fp["prob"]
            # 概率标记
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
            lines.append(f"  {mark} 未来{h_label}: 上涨概率 {prob_h}% | 预期收益 {fp['avg_ret']:+.2f}% | 置信{conf_label} | {action}")

    lines.append("")

    # 层级得分
    lines.append("  ── 层级得分 ──")
    for layer, score in sorted(prediction["layer_scores"].items(), key=lambda x: -x[1]):
        indicator = "▲" if score >= 60 else ("▼" if score <= 40 else "─")
        lines.append(f"  {indicator} {layer:<6} {score:>5}")
    lines.append("")

    # 因子明细
    lines.append("  ── 因子明细 ──")
    for f in prediction["factor_details"]:
        score = f["score"]
        indicator = "▲" if score >= 60 else ("▼" if score <= 40 else "─")
        lines.append(f"  {indicator} {f['name']:<10} {score:>5} (权重{f['weight']}%) {f['signal']}")

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

    # 未来概率因子明细（展示各因子的查表结果）
    if future_probs and "5d" in future_probs:
        details = future_probs["5d"]["details"]
        if details:
            lines.append("")
            lines.append("  ── 5日概率因子分解 ──")
            for d in details[:4]:  # 只展示前4个
                lines.append(f"  · {d['name']:<10} 分值{d['score']:>3} → 查表上涨率{d['up_prob']:.0f}% (样本{d['n_samples']})")

    return "\n".join(lines)


def save_report(all_results: list, output_path: str):
    """保存完整报告到JSON"""
    output = {
        "trade_date": all_results[0].get("trade_date", "") if all_results else "",
        "update_time": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_themes": len(all_results),
        "results": all_results,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
