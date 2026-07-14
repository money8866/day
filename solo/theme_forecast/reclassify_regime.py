# -*- coding: utf-8 -*-
"""
基于已有回测CSV，用新阈值重新计算regime和自适应概率
无需重新跑3.7小时回测

核心改进：
1. 抱团度阈值0.25（旧0.4过高导致所有天数都判为轮动）
2. 区分抱团上涨/抱团下跌/抱团震荡（用top5_rs_mean方向判断）
3. 抱团下跌期对反向因子（f_mom/f_concentration/f_rs_slope）取100-score
4. 轮动市按主题分化权重（动量类/反转类/中性类，基于历史IC分类）
"""
import pandas as pd
import numpy as np
import json
from pathlib import Path
from collections import defaultdict

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
THEME_CLASS_PATH = OUTPUT_DIR / "theme_class_rotation.json"

# 自适应权重
REGIME_WEIGHTS = {
    "抱团上涨": {
        # 抱团上涨：动量延续为王，追强势主题
        "relative_strength": 25, "momentum_acceleration": 20, "adx_trend": 15,
        "synergy_coefficient": 10, "leadership_divergence": 5, "breakout_ratio": 5,
        "rs_slope": 15, "concentration_change": 5,
    },
    "抱团下跌": {
        # 抱团下跌：反转因子为主（动量/集中度/RS斜率已反转方向）
        "relative_strength": 10, "momentum_acceleration": 20, "adx_trend": 5,
        "synergy_coefficient": 10, "leadership_divergence": 5, "breakout_ratio": 5,
        "rs_slope": 15, "concentration_change": 25,  # 最强反向因子IC=-0.232
        "leader_lag": 5,
    },
    "抱团震荡": {
        # 抱团震荡：时序因子为主（全部正向IC 0.07-0.14）
        "relative_strength": 15, "momentum_acceleration": 10, "adx_trend": 5,
        "synergy_coefficient": 5, "leadership_divergence": 5, "breakout_ratio": 5,
        "rs_slope": 25, "concentration_change": 10, "leader_lag": 20,  # 最强IC=0.139/0.124
    },
    "轮动": {
        # 轮动市基础权重（实际会按主题类型分化，见ROTATION_THEME_WEIGHTS）
        "relative_strength": 12, "momentum_acceleration": 10, "adx_trend": 8,
        "synergy_coefficient": 10, "leadership_divergence": 8, "breakout_ratio": 7,
        "rs_slope": 10, "concentration_change": 5,
    },
    "普跌": {
        "relative_strength": 5, "momentum_acceleration": 5, "adx_trend": 5,
        "synergy_coefficient": 10, "leadership_divergence": 10, "breakout_ratio": 5,
        "rs_slope": 15, "concentration_change": 5,
    },
    "普涨": {
        "relative_strength": 5, "momentum_acceleration": 10, "adx_trend": 10,
        "synergy_coefficient": 15, "leadership_divergence": 20, "breakout_ratio": 15,
        "rs_slope": 10, "concentration_change": 5,
    },
}

# 轮动市按主题类型分化的权重
ROTATION_THEME_WEIGHTS = {
    "动量类": {
        # 动量延续为主：RS+动量+RS斜率
        "relative_strength": 18, "momentum_acceleration": 18, "adx_trend": 10,
        "synergy_coefficient": 8, "leadership_divergence": 5, "breakout_ratio": 8,
        "rs_slope": 13, "concentration_change": 0,
    },
    "反转类": {
        # 动量反向+协同度+分化度为主
        "relative_strength": 8, "momentum_acceleration": 18, "adx_trend": 5,
        "synergy_coefficient": 15, "leadership_divergence": 18, "breakout_ratio": 8,
        "rs_slope": 13, "concentration_change": 15,
    },
    "中性类": {
        # 保持原统一权重
        "relative_strength": 12, "momentum_acceleration": 10, "adx_trend": 8,
        "synergy_coefficient": 10, "leadership_divergence": 8, "breakout_ratio": 7,
        "rs_slope": 10, "concentration_change": 5,
    },
}

# 反转类主题需要反转方向的因子（IC为负的因子）
ROTATION_REVERSE_KEYS = ["momentum_acceleration", "rs_slope", "concentration_change", "relative_strength"]

# 固定权重（原predictor）
FIXED_WEIGHTS = {
    "relative_strength": 12, "momentum_acceleration": 10, "adx_trend": 8,
    "synergy_coefficient": 10, "leadership_divergence": 8, "breakout_ratio": 7,
}


def load_theme_class_map() -> dict:
    """加载主题分类表（动量类/反转类/中性类）"""
    if not THEME_CLASS_PATH.exists():
        return {}
    with open(THEME_CLASS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {item["theme"]: item["theme_class"] for item in data}


def reclassify_regime(concentration, duration, top5_rs_mean=None):
    """用新阈值重新分类regime，区分抱团上涨/下跌"""
    if concentration > 0.25 and duration >= 5:
        # 进一步区分抱团上涨 vs 抱团下跌
        if top5_rs_mean is not None and top5_rs_mean > 1.0:
            return "抱团上涨"
        elif top5_rs_mean is not None and top5_rs_mean < 0.95:
            return "抱团下跌"
        else:
            return "抱团震荡"
    else:
        return "轮动"


def calc_adaptive_prob(row, theme_class_map=None):
    """
    用自适应权重重新计算概率

    关键逻辑：
    1. 抱团下跌期：对反向因子（f_mom/f_concentration/f_rs_slope）取100-score
    2. 轮动市：按主题类型分化权重
       - 动量类：动量延续权重
       - 反转类：反转权重（对反向因子取100-score）
       - 中性类：保持原统一权重
    """
    regime = row["regime_new"]

    # 可用的因子分数（CSV中有f_rs, f_mom, f_adx, f_syn, f_div, f_brk, f_rs_slope, f_concentration, f_leader_lag）
    factor_scores = {
        "relative_strength": row.get("f_rs", 50),
        "momentum_acceleration": row.get("f_mom", 50),
        "adx_trend": row.get("f_adx", 50),
        "synergy_coefficient": row.get("f_syn", 50),
        "leadership_divergence": row.get("f_div", 50),
        "breakout_ratio": row.get("f_brk", 50),
        "rs_slope": row.get("f_rs_slope", 50),
        "concentration_change": row.get("f_concentration", 50),
        "leader_lag": row.get("f_leader_lag", 50),
    }

    # 确定权重和反转因子列表
    reverse_keys = []

    if regime == "抱团下跌":
        # 抱团下跌期：反转方向因子（IC为负的因子取100-score）
        # f_mom IC=-0.093反向, f_concentration IC=-0.232反向, f_rs_slope IC=-0.079反向
        weights = REGIME_WEIGHTS["抱团下跌"]
        reverse_keys = ["momentum_acceleration", "concentration_change", "rs_slope"]
    elif regime == "轮动":
        # 轮动市：按主题类型分化权重
        theme = row.get("theme", "")
        if theme_class_map is None:
            theme_class_map = load_theme_class_map()
        theme_class = theme_class_map.get(theme, "中性类")
        weights = ROTATION_THEME_WEIGHTS.get(theme_class, ROTATION_THEME_WEIGHTS["中性类"])
        if theme_class == "反转类":
            reverse_keys = ROTATION_REVERSE_KEYS
    else:
        # 其他regime用对应权重
        weights = REGIME_WEIGHTS.get(regime, REGIME_WEIGHTS["轮动"])

    # 应用因子方向反转
    for reverse_key in reverse_keys:
        if reverse_key in factor_scores:
            factor_scores[reverse_key] = 100 - factor_scores[reverse_key]

    total_weight = 0
    weighted_sum = 0
    for key, weight in weights.items():
        if weight == 0:
            continue
        score = factor_scores.get(key, 50)
        weighted_sum += score * weight
        total_weight += weight

    return round(weighted_sum / total_weight, 1) if total_weight > 0 else 50


def main():
    csv_path = OUTPUT_DIR / "adaptive_backtest_20250101_20260713_h5.csv"
    df = pd.read_csv(csv_path)
    print(f"加载: {len(df)}条样本")

    # 加载主题分类表
    theme_class_map = load_theme_class_map()
    print(f"加载主题分类表: {len(theme_class_map)}个主题")
    if theme_class_map:
        from collections import Counter
        cls_count = Counter(theme_class_map.values())
        print(f"  分类统计: {dict(cls_count)}")

    # 1. 计算每条的抱团持续天数（按trade_date分组，用concentration>0.25的连续天数）
    df = df.sort_values(["theme", "trade_date"]).reset_index(drop=True)

    # 按日期计算持续天数（同一天所有主题的duration相同）
    dates = sorted(df["trade_date"].unique())
    date_duration = {}
    for i, d in enumerate(dates):
        conc = df[df["trade_date"] == d]["concentration"].iloc[0]
        if conc > 0.25:
            # 往前数连续>0.25的天数
            dur = 1
            for j in range(i - 1, -1, -1):
                prev_conc = df[df["trade_date"] == dates[j]]["concentration"].iloc[0]
                if prev_conc > 0.25:
                    dur += 1
                else:
                    break
            date_duration[d] = dur
        else:
            date_duration[d] = 0

    df["duration"] = df["trade_date"].map(date_duration)

    # 2. 用新阈值重新分类regime
    # 需要top5_rs_mean来区分抱团上涨/下跌
    # CSV中没有top5_rs_mean，但可以用f_rs（相对强度因子分）近似判断
    # f_rs是主题自身的RS，>60表示RS>1，<40表示RS<0.9
    # 用每日所有主题的f_rs均值来近似top5_rs_mean的方向
    df["avg_f_rs"] = df.groupby("trade_date")["f_rs"].transform("mean")

    def classify_row(row):
        # 用当日平均f_rs近似判断市场方向
        # f_rs>55 → 市场偏强（top5_rs_mean>1.0）→ 抱团上涨
        # f_rs<45 → 市场偏弱（top5_rs_mean<0.95）→ 抱团下跌
        avg_rs = row["avg_f_rs"]
        top5_rs_est = 1.0 if avg_rs >= 55 else (0.9 if avg_rs <= 45 else 0.97)
        return reclassify_regime(row["concentration"], row["duration"], top5_rs_est)

    df["regime_new"] = df.apply(classify_row, axis=1)

    # 3. 重新计算自适应概率（使用主题分化权重）
    df["prob_adaptive_new"] = df.apply(lambda r: calc_adaptive_prob(r, theme_class_map), axis=1)

    # 4. 评估对比
    print(f"\n新regime分布: {df['regime_new'].value_counts().to_dict()}")
    print(f"旧regime分布: {df['regime'].value_counts().to_dict()}")

    # 按月度看regime
    df["month"] = df["trade_date"].astype(str).str[:6]
    monthly = df.groupby("month").agg(
        conc=("concentration", "mean"),
        regime=("regime_new", lambda x: x.mode()[0]),
    ).reset_index()
    print(f"\n月度regime:")
    print(monthly.to_string(index=False))

    # 5. IC对比
    print(f"\n{'='*70}")
    print(f"IC对比（新阈值0.25 + 主题分化权重）")
    print(f"{'='*70}")

    # 固定权重IC（用prob_fixed）
    fixed_ic = df["prob_fixed"].corr(df["actual_ret"], method="spearman")
    # 旧自适应IC
    old_adaptive_ic = df["prob_adaptive"].corr(df["actual_ret"], method="spearman")
    # 新自适应IC
    new_adaptive_ic = df["prob_adaptive_new"].corr(df["actual_ret"], method="spearman")

    print(f"  固定权重IC:        {fixed_ic:.4f}")
    print(f"  旧自适应IC(阈值0.4): {old_adaptive_ic:.4f}")
    print(f"  新自适应IC(阈值0.25+主题分化): {new_adaptive_ic:.4f}")

    # 6. 按regime分组IC
    print(f"\n  按市场状态分组IC:")
    print(f"  {'状态':<8} {'样本':>6} {'固定IC':>8} {'旧自适应IC':>10} {'新自适应IC':>10} {'IC提升':>8}")
    print(f"  {'-'*56}")
    for regime in df["regime_new"].unique():
        sub = df[df["regime_new"] == regime]
        f_ic = sub["prob_fixed"].corr(sub["actual_ret"], method="spearman") if len(sub) > 10 else 0
        o_ic = sub["prob_adaptive"].corr(sub["actual_ret"], method="spearman") if len(sub) > 10 else 0
        n_ic = sub["prob_adaptive_new"].corr(sub["actual_ret"], method="spearman") if len(sub) > 10 else 0
        print(f"  {regime:<8} {len(sub):>6} {f_ic:>8.4f} {o_ic:>10.4f} {n_ic:>10.4f} {n_ic-f_ic:>+8.4f}")

    # 6.1 轮动市按主题类别分组IC
    if theme_class_map:
        print(f"\n  轮动市按主题类别分组IC:")
        rotation_sub = df[df["regime_new"] == "轮动"].copy()
        rotation_sub["theme_class"] = rotation_sub["theme"].map(lambda x: theme_class_map.get(x, "中性类"))
        print(f"  {'类别':<8} {'样本':>6} {'新自适应IC':>10}")
        print(f"  {'-'*30}")
        for cls in ["动量类", "反转类", "中性类"]:
            t = rotation_sub[rotation_sub["theme_class"] == cls]
            if len(t) < 30: continue
            n_ic = t["prob_adaptive_new"].corr(t["actual_ret"], method="spearman")
            print(f"  {cls:<8} {len(t):>6} {n_ic:>10.4f}")

    # 7. 分组收益对比
    print(f"\n  分组收益对比（新自适应）:")
    df["group_new"] = pd.qcut(df["prob_adaptive_new"], q=5, labels=["Q1", "Q2", "Q3", "Q4", "Q5"], duplicates="drop")
    group = df.groupby("group_new", observed=True).agg(
        n=("actual_ret", "count"),
        avg_pred=("prob_adaptive_new", "mean"),
        avg_ret=("actual_ret", "mean"),
        up_rate=("actual_up", "mean"),
    ).reset_index()
    print(f"  {'分组':<6} {'样本':>6} {'预测概率':>8} {'实际收益':>8} {'上涨率':>8}")
    print(f"  {'-'*40}")
    for _, g in group.iterrows():
        print(f"  {g['group_new']:<6} {g['n']:>6} {g['avg_pred']:>7.1f}% {g['avg_ret']:>+7.2f}% {g['up_rate']*100:>7.1f}%")

    # 8. 抱团期专题分析（按上涨/下跌/震荡分拆）
    print(f"\n  ── 抱团期分拆分析 ──")
    for sub_regime in ["抱团上涨", "抱团震荡", "抱团下跌"]:
        sub = df[df["regime_new"] == sub_regime]
        if len(sub) < 10:
            continue
        f_ic = sub["prob_fixed"].corr(sub["actual_ret"], method="spearman")
        n_ic = sub["prob_adaptive_new"].corr(sub["actual_ret"], method="spearman")
        print(f"\n  【{sub_regime}】（{len(sub)}样本）:")
        print(f"    固定IC: {f_ic:.4f} → 自适应IC: {n_ic:.4f} (提升{n_ic-f_ic:+.4f})")
        print(f"    实际平均收益: {sub['actual_ret'].mean():+.2f}%")

        high = sub[sub["prob_adaptive_new"] >= 55]
        low = sub[sub["prob_adaptive_new"] <= 45]
        if len(high) > 0:
            print(f"    高概率组: {len(high)}样本, 上涨率{high['actual_up'].mean()*100:.1f}%, 平均{high['actual_ret'].mean():+.2f}%")
        if len(low) > 0:
            print(f"    低概率组: {len(low)}样本, 上涨率{low['actual_up'].mean()*100:.1f}%, 平均{low['actual_ret'].mean():+.2f}%")
        if len(high) > 0 and len(low) > 0:
            print(f"    多空差: {high['actual_ret'].mean()-low['actual_ret'].mean():+.2f}%")

    # 保存
    out_path = OUTPUT_DIR / "adaptive_backtest_reclassified.csv"
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\n已保存: {out_path}")


if __name__ == "__main__":
    main()
