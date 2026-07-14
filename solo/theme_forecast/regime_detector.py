# -*- coding: utf-8 -*-
"""
市场状态识别器

识别4种市场状态：
1. 抱团市 - 少数主题RS持续走高，多数主题RS持续走低，主题间分化大
2. 轮动市 - 主题间RS在均衡区间波动，无明显分化
3. 普跌市 - 80%主题RS<0.9，系统性下跌
4. 普涨市 - 80%主题RS>1.0，系统性上涨

核心指标：
- 抱团度 = Top5主题RS均值 × 主题间RS标准差
- 抱团度>0.4且持续>15天 → 抱团市
- 抱团度<0.3 → 非抱团市，进一步区分轮动/普跌/普涨
"""
import numpy as np
import pandas as pd
from collections import deque


def calc_all_theme_rs(all_theme_klines: dict, market_index: pd.DataFrame,
                       lookback: int = 60) -> dict:
    """
    计算所有主题的相对强度RS（截止当日）

    Args:
        all_theme_klines: {theme_name: {code: df}}
        market_index: 大盘指数DataFrame
        lookback: 计算窗口

    Returns:
        {theme_name: rs_value}
    """
    if market_index is None or market_index.empty:
        return {}

    # 大盘归一化（统一升序排列，防止API返回降序数据）
    m = market_index.sort_values("trade_date").set_index("trade_date")
    close_col = "close" if "close" in m.columns else m.columns[0]
    m_close = m[close_col].iloc[-lookback:] if len(m) >= lookback else m[close_col]
    m_norm = m_close / m_close.iloc[0] * 100

    rs_dict = {}
    for theme_name, klines in all_theme_klines.items():
        if not klines:
            continue
        # 构建主题等权指数
        close_dict = {}
        for code, df in klines.items():
            if "close" in df.columns and "trade_date" in df.columns:
                s = df.set_index("trade_date")["close"].iloc[-lookback:]
                s.name = code
                close_dict[code] = s

        if not close_dict:
            continue

        close_df = pd.DataFrame(close_dict).sort_index()
        # 对齐到大盘日期
        close_df = close_df.loc[close_df.index.isin(m_norm.index)]
        if len(close_df) < 20:
            continue

        # 每列单独用各自第一个有效值归一化
        # 不能用 close_df.iloc[0] 做除数，因为第一行可能有NaN（停牌/未上市）
        # 否则 任意值/NaN = NaN，会导致整列变成NaN
        first_valid = close_df.apply(lambda col: col.dropna().iloc[0] if len(col.dropna()) > 0 else np.nan)
        norm_df = close_df.div(first_valid, axis=1) * 100
        theme_close = norm_df.mean(axis=1)

        # 对齐
        aligned = pd.DataFrame({"theme": theme_close, "market": m_norm}).dropna()
        if len(aligned) < 20:
            continue

        rs = aligned["theme"] / aligned["market"]
        rs_dict[theme_name] = float(rs.iloc[-1])

    return rs_dict


def detect_regime(rs_history: list, window: int = 20) -> dict:
    """
    识别市场状态

    Args:
        rs_history: 历史每日的RS分布 [{date, rs_dict, concentration, dispersion}, ...]
        window: 判断窗口

    Returns:
        {
            "regime": "抱团/轮动/普跌/普涨",
            "concentration": 抱团度,
            "dispersion": 分化度,
            "concentration_trend": 抱团度趋势,
            "top_themes": [强势主题],
            "bottom_themes": [弱势主题],
            "duration": 持续天数,
            "confidence": 置信度,
        }
    """
    if not rs_history or len(rs_history) < 5:
        return {
            "regime": "未知",
            "concentration": 0,
            "dispersion": 0,
            "concentration_trend": 0,
            "top_themes": [],
            "bottom_themes": [],
            "duration": 0,
            "confidence": "low",
        }

    # 取最近window天的数据
    recent = rs_history[-window:] if len(rs_history) >= window else rs_history

    # 最新一天的RS分布
    latest = rs_history[-1]
    rs_dict = latest.get("rs_dict", {})

    if not rs_dict:
        return {
            "regime": "未知",
            "concentration": 0,
            "dispersion": 0,
            "concentration_trend": 0,
            "top_themes": [],
            "bottom_themes": [],
            "duration": 0,
            "confidence": "low",
        }

    rs_values = list(rs_dict.values())
    if len(rs_values) < 5:
        return {
            "regime": "未知",
            "concentration": 0,
            "dispersion": 0,
            "concentration_trend": 0,
            "top_themes": [],
            "bottom_themes": [],
            "duration": 0,
            "confidence": "low",
        }

    # 排序
    sorted_rs = sorted(rs_dict.items(), key=lambda x: -x[1])
    top5_themes = [t[0] for t in sorted_rs[:5]]
    bottom5_themes = [t[0] for t in sorted_rs[-5:]]

    # 核心指标
    top5_rs_mean = float(np.mean([t[1] for t in sorted_rs[:5]]))
    all_rs_mean = float(np.mean(rs_values))
    all_rs_std = float(np.std(rs_values))

    # 抱团度 = Top5 RS均值 × 主题间RS标准差
    # 当top5很强且分化大时，抱团度高
    # 实测分布：正常轮动0.08-0.20，抱团期0.25-1.2
    concentration = top5_rs_mean * all_rs_std

    # 分化度 = Top5均值 - Bottom5均值
    bottom5_rs_mean = float(np.mean([t[1] for t in sorted_rs[-5:]]))
    dispersion = top5_rs_mean - bottom5_rs_mean

    # 抱团度趋势（近5天 vs 前5天）
    concentration_trend = 0
    if len(recent) >= 10:
        recent_conc = [r.get("concentration", 0) for r in recent[-5:]]
        prev_conc = [r.get("concentration", 0) for r in recent[-10:-5]]
        concentration_trend = float(np.mean(recent_conc) - np.mean(prev_conc))

    # 计算抱团持续天数
    duration = 0
    for r in reversed(rs_history):
        if r.get("concentration", 0) > 0.25:
            duration += 1
        else:
            break

    # 判断市场状态
    # 1. 抱团度持续>0.25 且 持续>5天
    # 实测：2025年正常轮动0.08-0.20，2026年5-7月抱团0.28-1.2
    is_persistent_concentrated = concentration > 0.25 and duration >= 5

    # 2. 普跌市：80%主题RS<0.9
    below_09 = sum(1 for v in rs_values if v < 0.9) / len(rs_values)
    is_broad_decline = below_09 > 0.8

    # 3. 普涨市：80%主题RS>1.0
    above_10 = sum(1 for v in rs_values if v > 1.0) / len(rs_values)
    is_broad_rally = above_10 > 0.8

    # 状态判定（优先级：抱团 > 普跌 > 普涨 > 轮动）
    if is_persistent_concentrated:
        # 进一步区分抱团上涨/抱团下跌/抱团震荡
        # 用top5_rs_mean的短期变化方向判断（而非绝对值）
        # 因为60天RS绝对值>1.0不代表当前在涨，需要看近5天变化
        top5_rs_slope = 0
        if len(rs_history) >= 10:
            # 计算每天的top5_rs_mean
            daily_top5 = []
            for r in rs_history[-10:]:
                t_rs = sorted(r.get("rs_dict", {}).items(), key=lambda x: -x[1])
                if len(t_rs) >= 5:
                    daily_top5.append(float(np.mean([t[1] for t in t_rs[:5]])))
            if len(daily_top5) >= 10:
                # 近5天均值 vs 前5天均值，变化率
                recent_top5 = float(np.mean(daily_top5[-5:]))
                prev_top5 = float(np.mean(daily_top5[-10:-5]))
                top5_rs_slope = (recent_top5 - prev_top5) / prev_top5 if prev_top5 > 0 else 0

        # top5_rs_mean变化率>1% → 强势主题在涨 → 抱团上涨
        # top5_rs_mean变化率<-1% → 强势主题在跌 → 抱团下跌
        # 其他 → 抱团震荡
        if top5_rs_slope > 0.01:
            regime = "抱团上涨"
        elif top5_rs_slope < -0.01:
            regime = "抱团下跌"
        else:
            regime = "抱团震荡"
        confidence = "high" if duration >= 15 else "medium"
    elif is_broad_decline:
        regime = "普跌"
        confidence = "high" if below_09 > 0.9 else "medium"
    elif is_broad_rally:
        regime = "普涨"
        confidence = "high" if above_10 > 0.9 else "medium"
    else:
        regime = "轮动"
        confidence = "high" if concentration < 0.2 else "medium"

    return {
        "regime": regime,
        "concentration": round(concentration, 4),
        "dispersion": round(dispersion, 4),
        "concentration_trend": round(concentration_trend, 4),
        "top_themes": top5_themes,
        "bottom_themes": bottom5_themes,
        "duration": duration,
        "confidence": confidence,
        "top5_rs_mean": round(top5_rs_mean, 3),
        "all_rs_mean": round(all_rs_mean, 3),
        "all_rs_std": round(all_rs_std, 3),
        "below_09_ratio": round(below_09, 2),
        "above_10_ratio": round(above_10, 2),
    }


def get_regime_factor_weights(regime: str) -> dict:
    """
    根据市场状态返回因子权重配置

    核心逻辑：
    - 抱团上涨：动量延续为主，追强势主题
    - 抱团下跌：反转因子为主（动量/集中度/RS斜率已反转方向）
    - 抱团震荡：时序因子为主（RS斜率/领先滞后）
    - 轮动：按主题类型分化（动量类/反转类/中性类，见adaptive_predictor）
    - 普跌：反转因子为主，寻找超跌反弹
    - 普涨：低RS补涨因子为主
    """
    if regime == "抱团上涨":
        # 抱团上涨：动量延续为王，追强势主题
        return {
            "relative_strength": 25, "momentum_acceleration": 20, "adx_trend": 15,
            "synergy_coefficient": 10, "leadership_divergence": 5, "breakout_ratio": 5,
            "rs_slope": 15, "concentration_change": 5,
            "limit_up_ladder": 0, "turnover_distribution": 0,
            "etf_net_inflow": 0, "north_flow": 0,
        }
    elif regime == "抱团下跌":
        # 抱团下跌：反转因子为主（动量/集中度/RS斜率在calc_adaptive_prob中会取100-score）
        return {
            "relative_strength": 10, "momentum_acceleration": 20, "adx_trend": 5,
            "synergy_coefficient": 10, "leadership_divergence": 5, "breakout_ratio": 5,
            "rs_slope": 15, "concentration_change": 25,  # 最强反向因子IC=-0.232
            "leader_lag": 5,
            "limit_up_ladder": 0, "turnover_distribution": 0,
            "etf_net_inflow": 0, "north_flow": 0,
        }
    elif regime == "抱团震荡":
        # 抱团震荡：时序因子为主（全部正向IC 0.07-0.14）
        return {
            "relative_strength": 15, "momentum_acceleration": 10, "adx_trend": 5,
            "synergy_coefficient": 5, "leadership_divergence": 5, "breakout_ratio": 5,
            "rs_slope": 25, "concentration_change": 10, "leader_lag": 20,
            "limit_up_ladder": 0, "turnover_distribution": 0,
            "etf_net_inflow": 0, "north_flow": 0,
        }
    elif regime == "普跌":
        # 普跌市：反转因子为主，寻找超跌反弹
        return {
            "relative_strength": 5, "momentum_acceleration": 5, "adx_trend": 5,
            "synergy_coefficient": 10, "leadership_divergence": 10, "breakout_ratio": 5,
            "rs_slope": 15, "concentration_change": 5,
            "limit_up_ladder": 15, "turnover_distribution": 10,
            "etf_net_inflow": 10, "north_flow": 5,
        }
    elif regime == "普涨":
        # 普涨市：低RS补涨因子为主
        return {
            "relative_strength": 5, "momentum_acceleration": 10, "adx_trend": 10,
            "synergy_coefficient": 15, "leadership_divergence": 20, "breakout_ratio": 15,
            "rs_slope": 10, "concentration_change": 5,
            "limit_up_ladder": 5, "turnover_distribution": 5,
            "etf_net_inflow": 5, "north_flow": 0,
        }
    else:  # 轮动市（基础权重，实际会按主题类型分化，见adaptive_predictor）
        return {
            "relative_strength": 12, "momentum_acceleration": 10, "adx_trend": 8,
            "synergy_coefficient": 10, "leadership_divergence": 8, "breakout_ratio": 7,
            "rs_slope": 10, "concentration_change": 5,
            "limit_up_ladder": 12, "turnover_distribution": 8,
            "etf_net_inflow": 5, "north_flow": 5,
        }


# 轮动市按主题类型分化的权重（基于历史IC分类）
ROTATION_THEME_WEIGHTS = {
    "动量类": {
        # 动量延续为主：RS+动量+RS斜率
        "relative_strength": 18, "momentum_acceleration": 18, "adx_trend": 10,
        "synergy_coefficient": 8, "leadership_divergence": 5, "breakout_ratio": 8,
        "rs_slope": 13, "concentration_change": 0,
        "limit_up_ladder": 0, "turnover_distribution": 0,
        "etf_net_inflow": 0, "north_flow": 0,
    },
    "反转类": {
        # 动量反向+协同度+分化度为主
        "relative_strength": 8, "momentum_acceleration": 18, "adx_trend": 5,
        "synergy_coefficient": 15, "leadership_divergence": 18, "breakout_ratio": 8,
        "rs_slope": 13, "concentration_change": 15,
        "limit_up_ladder": 0, "turnover_distribution": 0,
        "etf_net_inflow": 0, "north_flow": 0,
    },
    "中性类": {
        # 保持原统一权重
        "relative_strength": 12, "momentum_acceleration": 10, "adx_trend": 8,
        "synergy_coefficient": 10, "leadership_divergence": 8, "breakout_ratio": 7,
        "rs_slope": 10, "concentration_change": 5,
        "limit_up_ladder": 12, "turnover_distribution": 8,
        "etf_net_inflow": 5, "north_flow": 5,
    },
}

# 反转因子列表
# 抱团下跌期：f_mom IC=-0.093反向, f_concentration IC=-0.232反向, f_rs_slope IC=-0.079反向
REGIME_REVERSE_KEYS = {
    "抱团下跌": ["momentum_acceleration", "concentration_change", "rs_slope"],
}

# 轮动市反转类主题需要反转方向的因子
ROTATION_REVERSE_KEYS = ["momentum_acceleration", "rs_slope", "concentration_change", "relative_strength"]


def format_regime_report(regime_info: dict) -> str:
    """格式化市场状态报告"""
    regime = regime_info["regime"]
    emoji_map = {"抱团": "🎯", "轮动": "🔄", "普跌": "📉", "普涨": "📈", "未知": "❓"}

    lines = []
    lines.append(f"  市场状态: {emoji_map.get(regime, '')} {regime} (置信度: {regime_info['confidence']})")
    lines.append(f"  抱团度: {regime_info['concentration']} | 分化度: {regime_info['dispersion']} | "
                  f"趋势变化: {regime_info['concentration_trend']:+}")
    lines.append(f"  抱团持续: {regime_info['duration']}天")
    lines.append(f"  Top5 RS均值: {regime_info['top5_rs_mean']} | 全市场RS均值: {regime_info['all_rs_mean']} | "
                  f"RS标准差: {regime_info['all_rs_std']}")
    lines.append(f"  RS<0.9占比: {regime_info['below_09_ratio']*100:.0f}% | RS>1.0占比: {regime_info['above_10_ratio']*100:.0f}%")
    if regime_info["top_themes"]:
        lines.append(f"  强势主题: {', '.join(regime_info['top_themes'][:3])}")
    if regime_info["bottom_themes"]:
        lines.append(f"  弱势主题: {', '.join(regime_info['bottom_themes'][:3])}")

    return "\n".join(lines)
