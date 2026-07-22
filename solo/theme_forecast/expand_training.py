# -*- coding: utf-8 -*-
"""
扩大训练集 + Walk Forward验证

策略：
1. 用tdx_backtest产出的21417条样本（1.5年）重新训练条件概率查表
2. Walk Forward：用2025年数据训练，用2026年数据验证
3. 对比原查表（3个月训练）vs 新查表（1年训练）的IC变化

用法:
    python -m theme_forecast.expand_training
"""
import os
import sys
import json
import numpy as np
import pandas as pd
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

OUTPUT_DIR = Path(__file__).resolve().parent / "output"


# ====================================================================
# 分数扩展：将50附近的分数拉开，提高因子区分度
# ====================================================================
def _expand_score_vector(scores: np.ndarray, midpoint: float = 50.0,
                          low_strength: float = 1.4, high_strength: float = 1.4) -> np.ndarray:
    """
    对因子分数向量进行非线性扩展，拉开高分和低分之间的差距。

    核心逻辑：
    - 以midpoint为中心，对偏离中心的值进行指数放大
    - 50分以下的压缩到更低，50分以上的推到更高
    - 使用非线性映射：expanded = midpoint + (score - midpoint) * strength
      strength在极端区更大（>1.4），在中间区更小（~1.2）

    目的：让prob_lookup中不同分箱的上涨概率差异更大，
    解决目前所有分箱概率挤在54%-58%之间的核心问题。
    """
    if scores is None or len(scores) == 0:
        return scores

    scores = np.asarray(scores, dtype=float)

    # 非线性扩展：越远离midpoint，扩展幅度越大
    deviation = scores - midpoint
    abs_dev = np.abs(deviation)

    # 分三段：小偏离(0-10)用1.2x, 中偏离(10-20)用1.4x, 大偏离(>20)用1.6x
    strength_map = np.ones_like(abs_dev)
    strength_map[abs_dev <= 10] = low_strength
    strength_map[(abs_dev > 10) & (abs_dev <= 20)] = (low_strength + high_strength) / 2
    strength_map[abs_dev > 20] = high_strength

    expanded = midpoint + deviation * strength_map * 1.5
    return expanded


def _compute_bayesian_bin_stats(bin_data: pd.DataFrame, overall_up_rate: float,
                                  ret_col: str, up_col: str,
                                  min_samples: int = 30) -> dict:
    """
    使用贝叶斯收缩计算分箱统计量。

    核心逻辑：
    - 样本少的箱（极端分数区间，样本天然少）的上涨概率向整体均值收缩
    - 收缩程度 = 1 / (1 + n_samples / prior_strength)
    - prior_strength=50 意味着50个样本时收缩一半，200个样本时几乎不收缩

    Args:
        bin_data: 分箱内的样本DataFrame
        overall_up_rate: 整体上涨率（先验）
        ret_col: 收益率列名
        up_col: 是否上涨列名
        min_samples: 最小样本数阈值

    Returns:
        dict: bin统计量（已收缩）
    """
    n = len(bin_data)
    if n < min_samples:
        return None

    raw_up_prob = float(bin_data[up_col].mean())
    raw_avg_ret = float(bin_data[ret_col].mean())

    # 贝叶斯收缩：样本越少，越向整体均值靠拢
    # prior_strength控制收缩速度，值越大收缩越强
    prior_strength = 50.0
    shrinkage = n / (n + prior_strength)

    adjusted_up_prob = overall_up_rate * (1 - shrinkage) + raw_up_prob * shrinkage
    adjusted_avg_ret = raw_avg_ret * shrinkage  # 收益也做同样收缩

    return {
        "n_samples": n,
        "up_prob": float(adjusted_up_prob * 100),
        "avg_ret": float(adjusted_avg_ret),
        "raw_up_prob": float(raw_up_prob * 100),
        "raw_avg_ret": float(raw_avg_ret),
        "shrinkage": float(shrinkage),
    }


# ====================================================================
# 1. 加载回测样本
# ====================================================================
def load_backtest_samples():
    """加载通达信回测产出的样本"""
    csv_path = OUTPUT_DIR / "tdx_backtest_20250101_20260713_h5.csv"
    if not csv_path.exists():
        print(f"错误: 未找到回测样本 {csv_path}")
        print("请先运行: python -m theme_forecast.tdx_backtest --start 20250101 --horizon 5")
        return None
    df = pd.read_csv(csv_path)
    print(f"加载回测样本: {len(df)}条")
    print(f"  日期范围: {df['trade_date'].min()}~{df['trade_date'].max()}")
    print(f"  主题数: {df['theme'].nunique()}")
    return df


# ====================================================================
# 2. 训练条件概率查表（扩大样本版）
# ====================================================================
def train_prob_lookup(df: pd.DataFrame, factor_cols: list, horizon: int = 5,
                       n_bins: int = 5) -> dict:
    """
    用扩大后的样本训练条件概率查表

    Args:
        df: 回测样本DataFrame
        factor_cols: 因子列名列表
        horizon: 未来收益周期
        n_bins: 分箱数

    Returns:
        查表字典 {factor_key: {horizon: [{bin_min, bin_max, up_prob, avg_ret, n_samples}]}}
    """
    ret_col = f"actual_ret"  # 回测CSV中的实际收益列
    up_col = f"actual_up"

    lookup = {}

    for factor_col in factor_cols:
        if factor_col not in df.columns:
            continue

        valid = df[[factor_col, ret_col, up_col]].dropna()
        if len(valid) < 50:
            continue

        # 等频分箱
        try:
            valid["bin"] = pd.qcut(valid[factor_col], q=n_bins, duplicates="drop", labels=False)
        except Exception:
            continue

        bins = []
        for bin_idx in sorted(valid["bin"].unique()):
            bin_data = valid[valid["bin"] == bin_idx]
            if len(bin_data) < 10:
                continue

            bins.append({
                "bin_min": float(bin_data[factor_col].min()),
                "bin_max": float(bin_data[factor_col].max()),
                "up_prob": float(bin_data[up_col].mean() * 100),
                "avg_ret": float(bin_data[ret_col].mean()),
                "n_samples": int(len(bin_data)),
            })

        if bins:
            lookup[factor_col] = {f"{horizon}d": bins}

    return lookup


def train_prob_lookup_multi_horizon(df: pd.DataFrame, factor_cols: list,
                                      horizons: list = None, n_bins: int = 7) -> dict:
    """
    多horizon训练条件概率查表（3d/5d/10d）— 优化版

    关键改进：
    1. 分数扩展：对因子分数进行非线性拉伸（50分附近的分数拉开差距）
    2. 贝叶斯收缩：样本少的箱的概率向整体均值收缩，提高稳定性
    3. 更多分箱：从5箱增加到7箱，提高粒度

    Args:
        df: 回测样本DataFrame，需含 ret_Nd/up_Nd 列
        factor_cols: 因子列名列表
        horizons: 周期列表，默认[3, 5, 10]
        n_bins: 分箱数，默认7（原5）

    Returns:
        查表字典 {factor_key: {"3d": [...], "5d": [...], "10d": [...]}}
    """
    if horizons is None:
        horizons = [3, 5, 10]

    lookup = {}

    for factor_col in factor_cols:
        if factor_col not in df.columns:
            continue

        factor_lookup = {}
        for h in horizons:
            ret_col = f"ret_{h}d"
            up_col = f"up_{h}d"
            if ret_col not in df.columns or up_col not in df.columns:
                print(f"  [跳过] {factor_col} {h}d: 缺列 {ret_col}/{up_col}")
                continue

            valid = df[[factor_col, ret_col, up_col]].dropna().copy()
            if len(valid) < 100:
                print(f"  [跳过] {factor_col} {h}d: 样本不足 {len(valid)}")
                continue

            # ===== 改进1: 分数扩展 =====
            # 对因子分数进行非线性拉伸，拉开极端区间的差距
            expanded = _expand_score_vector(valid[factor_col].values)
            valid["score_expanded"] = expanded
            overall_up_rate = float(valid[up_col].mean())

            # ===== 改进2: 更多分箱（7箱vs原5箱） =====
            try:
                valid["bin"] = pd.qcut(valid["score_expanded"], q=n_bins,
                                        duplicates="drop", labels=False)
            except Exception:
                continue

            # ===== 改进3: 贝叶斯收缩 =====
            bins = []
            for bin_idx in sorted(valid["bin"].unique()):
                bin_data = valid[valid["bin"] == bin_idx]
                # 获取原始score的范围（而非扩展后的），保留bin_min/max与原系统兼容
                raw_min = float(bin_data[factor_col].min())
                raw_max = float(bin_data[factor_col].max())

                # 贝叶斯收缩
                stats = _compute_bayesian_bin_stats(bin_data, overall_up_rate,
                                                     ret_col, up_col, min_samples=20)
                if stats is None:
                    continue

                bins.append({
                    "bin_min": raw_min,
                    "bin_max": raw_max,
                    "up_prob": round(stats["up_prob"], 2),
                    "avg_ret": round(stats["avg_ret"], 4),
                    "n_samples": stats["n_samples"],
                    # 扩展后的边界（内部使用）
                    "score_min": float(bin_data["score_expanded"].min()),
                    "score_max": float(bin_data["score_expanded"].max()),
                })

            if bins:
                factor_lookup[f"{h}d"] = bins
                print(f"  {factor_col} {h}d: {len(bins)}bins, {len(valid)}样本, "
                      f"上涨率{overall_up_rate*100:.1f}%")

        if factor_lookup:
            lookup[factor_col] = factor_lookup

    return lookup


# ====================================================================
# 3. Walk Forward验证
# ====================================================================
def walk_forward_evaluate(df: pd.DataFrame, factor_cols: list, horizon: int = 5):
    """
    Walk Forward验证：2025年训练，2026年验证

    Returns:
        train_stats, test_stats
    """
    # 分割数据
    df["trade_date_str"] = df["trade_date"].astype(str)
    train_df = df[df["trade_date_str"] < "20260101"].copy()
    test_df = df[df["trade_date_str"] >= "20260101"].copy()

    print(f"\n  Walk Forward分割:")
    print(f"    训练集(2025年): {len(train_df)}条, {train_df['trade_date'].min()}~{train_df['trade_date'].max()}")
    print(f"    验证集(2026年): {len(test_df)}条, {test_df['trade_date'].min()}~{test_df['trade_date'].max()}")

    if train_df.empty or test_df.empty:
        return None, None

    # 用训练集训练查表
    train_lookup = train_prob_lookup(train_df, factor_cols, horizon)

    # 在验证集上预测
    test_predictions = []
    for _, row in test_df.iterrows():
        weighted_prob = 0
        total_weight = 0

        # 因子权重（与predictor.py一致）
        weights = {
            "f_rs": 12, "f_mom": 10, "f_adx": 8,
            "f_syn": 10, "f_div": 8, "f_brk": 7,
        }

        for factor_col, weight in weights.items():
            if factor_col not in train_lookup:
                continue
            score = row.get(factor_col)
            if pd.isna(score):
                continue

            # 查表
            bins = train_lookup[factor_col].get(f"{horizon}d", [])
            up_prob = None
            for b in bins:
                if b["bin_min"] <= score <= b["bin_max"]:
                    up_prob = b["up_prob"]
                    break
            if up_prob is None:
                if bins:
                    if score < bins[0]["bin_min"]:
                        up_prob = bins[0]["up_prob"]
                    elif score > bins[-1]["bin_max"]:
                        up_prob = bins[-1]["up_prob"]

            if up_prob is not None:
                weighted_prob += up_prob * weight
                total_weight += weight

        if total_weight > 0:
            pred_prob = weighted_prob / total_weight
            test_predictions.append({
                "predicted_prob": pred_prob,
                "actual_ret": row["actual_ret"],
                "actual_up": row["actual_up"],
            })

    if not test_predictions:
        return train_lookup, None

    pred_df = pd.DataFrame(test_predictions)

    # 评估验证集
    ic = pred_df["predicted_prob"].corr(pred_df["actual_ret"], method="spearman")
    accuracy = ((pred_df["predicted_prob"] >= 50) == (pred_df["actual_up"] == 1)).mean() * 100

    # 分组
    pred_df["group"] = pd.qcut(pred_df["predicted_prob"], q=5, labels=["Q1", "Q2", "Q3", "Q4", "Q5"], duplicates="drop")
    group_stats = pred_df.groupby("group", observed=True).agg(
        n=("actual_ret", "count"),
        avg_pred=("predicted_prob", "mean"),
        avg_ret=("actual_ret", "mean"),
        up_rate=("actual_up", "mean"),
    ).reset_index()

    # 多空对比
    high = pred_df[pred_df["predicted_prob"] >= 55]
    low = pred_df[pred_df["predicted_prob"] <= 45]

    test_stats = {
        "n_samples": len(pred_df),
        "ic": round(ic, 4),
        "accuracy": round(accuracy, 1),
        "high_n": len(high),
        "high_up_rate": round(high["actual_up"].mean() * 100, 1) if len(high) > 0 else 0,
        "high_avg_ret": round(high["actual_ret"].mean(), 2) if len(high) > 0 else 0,
        "low_n": len(low),
        "low_up_rate": round(low["actual_up"].mean() * 100, 1) if len(low) > 0 else 0,
        "low_avg_ret": round(low["actual_ret"].mean(), 2) if len(low) > 0 else 0,
        "long_short": round((high["actual_ret"].mean() - low["actual_ret"].mean()), 2) if len(high) > 0 and len(low) > 0 else 0,
        "group_stats": group_stats.to_dict("records"),
    }

    return train_lookup, test_stats


# ====================================================================
# 4. 全样本训练（用于生产）
# ====================================================================
def train_full_lookup(df: pd.DataFrame, factor_cols: list, horizon: int = 5) -> dict:
    """用全部21417条样本训练最终查表"""
    return train_prob_lookup(df, factor_cols, horizon)


# ====================================================================
# 5. 格式化输出
# ====================================================================
def format_walk_forward_report(train_lookup: dict, test_stats: dict, full_lookup: dict,
                                 total_samples: int) -> str:
    lines = []
    lines.append("=" * 70)
    lines.append("  扩大训练集 + Walk Forward验证报告")
    lines.append("=" * 70)
    lines.append(f"  总样本: {total_samples}条 (1.5年)")
    lines.append("")

    if test_stats:
        lines.append("  ── Walk Forward验证（2025训练→2026验证）──")
        lines.append(f"  验证集样本: {test_stats['n_samples']}")
        lines.append(f"  IC: {test_stats['ic']}")
        lines.append(f"  准确率: {test_stats['accuracy']}%")
        lines.append("")
        lines.append(f"  预测≥55%: {test_stats['high_n']}样本, 上涨率{test_stats['high_up_rate']}%, "
                      f"平均{test_stats['high_avg_ret']:+.2f}%")
        lines.append(f"  预测≤45%: {test_stats['low_n']}样本, 上涨率{test_stats['low_up_rate']}%, "
                      f"平均{test_stats['low_avg_ret']:+.2f}%")
        lines.append(f"  多空收益差: {test_stats['long_short']:+.2f}%")
        lines.append("")

        lines.append("  ── 分组回测（验证集） ──")
        lines.append(f"  {'分组':<6} {'样本':>6} {'预测概率':>8} {'实际收益':>8} {'上涨率':>8}")
        lines.append(f"  {'-'*40}")
        for g in test_stats["group_stats"]:
            lines.append(f"  {g['group']:<6} {g['n']:>6} {g['avg_pred']:>7.1f}% "
                          f"{g['avg_ret']:>+7.2f}% {g['up_rate']*100:>7.1f}%")
        lines.append("")

    lines.append("  ── 新查表vs旧查表对比 ──")
    lines.append(f"  旧查表: 3个月训练, 2958样本, IC=0.0468")
    if test_stats:
        lines.append(f"  新查表: 1年训练(Walk Forward), {test_stats['n_samples']}验证样本, IC={test_stats['ic']}")
    lines.append(f"  全量查表: 1.5年训练, {total_samples}样本 (用于生产)")
    lines.append("")

    # 新查表vs旧查表的因子概率对比
    lines.append("  ── 新查表因子概率（5日，全量训练） ──")
    factor_names = {
        "f_rs": "相对强度", "f_mom": "动量加速度", "f_adx": "ADX趋势",
        "f_syn": "协同度", "f_div": "分化度", "f_brk": "突破比例",
    }
    for factor_key, factor_data in full_lookup.items():
        name = factor_names.get(factor_key, factor_key)
        bins = factor_data.get("5d", [])
        if not bins:
            continue
        lines.append(f"\n  【{name}】")
        lines.append(f"  {'分箱范围':<16} {'样本':>6} {'上涨概率':>8} {'平均收益':>8}")
        lines.append(f"  {'-'*44}")
        for b in bins:
            mark = "▲" if b["up_prob"] >= 58 else ("▼" if b["up_prob"] <= 52 else "─")
            lines.append(f"  {mark} [{b['bin_min']:.0f}, {b['bin_max']:.0f}]{'':<6} "
                          f"{b['n_samples']:>6} {b['up_prob']:>7.1f}% {b['avg_ret']:>+7.2f}%")

    lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)


# ====================================================================
# 主入口
# ====================================================================
def main():
    print("=" * 70)
    print("扩大训练集 + Walk Forward验证（多horizon: 3d/5d/10d）")
    print("=" * 70)

    # 1. 加载回测样本
    print("\n[1/4] 加载回测样本...")
    df = load_backtest_samples()
    if df is None:
        return

    factor_cols = ["f_rs", "f_mom", "f_adx", "f_syn", "f_div", "f_brk"]

    # 2. Walk Forward验证（5d，保持原逻辑）
    print("\n[2/4] Walk Forward验证（5d）...")
    train_lookup, test_stats = walk_forward_evaluate(df, factor_cols, horizon=5)

    # 3. 全样本多horizon训练（3d/5d/10d）
    print("\n[3/4] 全样本多horizon训练查表（3d/5d/10d）...")
    # 检查主CSV是否有多horizon列
    has_multi_horizon = all(f"ret_{h}d" in df.columns and f"up_{h}d" in df.columns
                              for h in [3, 5, 10])
    if has_multi_horizon:
        print(f"  主CSV检测到3d/5d/10d列，训练多horizon查表...")
        full_lookup = train_prob_lookup_multi_horizon(df, factor_cols, horizons=[3, 5, 10])
    else:
        print(f"  主CSV只有5d列（列名actual_ret/actual_up），使用优化方法训练5d查表...")
        # 列名映射：actual_ret → ret_5d, actual_up → up_5d
        df_5d = df.rename(columns={"actual_ret": "ret_5d", "actual_up": "up_5d"})
        full_lookup = train_prob_lookup_multi_horizon(df_5d, factor_cols, horizons=[5])
        print(f"  5d训练完成: {len(full_lookup)}个因子")

        # 3d/10d用backtest_samples.csv（2958样本，3个月）
        bs_path = OUTPUT_DIR / "backtest_samples.csv"
        if bs_path.exists():
            bs_df = pd.read_csv(bs_path)
            print(f"  加载backtest_samples: {len(bs_df)}条")
            # backtest_samples用全称列名，需映射到缩写
            col_map = {
                "f_relative_strength": "f_rs",
                "f_momentum_acceleration": "f_mom",
                "f_adx_trend": "f_adx",
                "f_synergy_coefficient": "f_syn",
                "f_leadership_divergence": "f_div",
                "f_breakout_ratio": "f_brk",
            }
            bs_df = bs_df.rename(columns=col_map)
            bs_factor_cols = [col_map.get(c, c) for c in factor_cols]
            # 检查3d/10d列
            has_3d_10d = all(f"ret_{h}d" in bs_df.columns and f"up_{h}d" in bs_df.columns
                               for h in [3, 10])
            if has_3d_10d:
                print(f"  训练3d/10d查表...")
                extra_lookup = train_prob_lookup_multi_horizon(bs_df, bs_factor_cols, horizons=[3, 10])
                # 合并到full_lookup
                for fk, fv in extra_lookup.items():
                    if fk not in full_lookup:
                        full_lookup[fk] = {}
                    for h_key, bins in fv.items():
                        if h_key not in full_lookup[fk]:  # 不覆盖5d
                            full_lookup[fk][h_key] = bins
                print(f"  合并完成: 3d/10d已加入")
            else:
                print(f"  [警告] backtest_samples也缺少3d/10d列")
        else:
            print(f"  [警告] 未找到backtest_samples.csv: {bs_path}")
    print(f"  训练完成: {len(full_lookup)}个因子")

    # 4. 保存新查表
    print("\n[4/4] 保存...")
    report = format_walk_forward_report(train_lookup, test_stats, full_lookup, len(df))
    print(report)

    # 备份旧查表
    old_lookup_path = OUTPUT_DIR / "prob_lookup.json"
    backup_path = OUTPUT_DIR / "prob_lookup_old_3months.json"
    if old_lookup_path.exists() and not backup_path.exists():
        import shutil
        shutil.copy(old_lookup_path, backup_path)
        print(f"旧查表已备份: {backup_path}")

    # 保存新查表
    with open(old_lookup_path, "w", encoding="utf-8") as f:
        json.dump(full_lookup, f, ensure_ascii=False, indent=2)
    print(f"新查表已保存: {old_lookup_path}")

    # 打印查表结构
    print(f"\n查表结构:")
    for fk, fv in full_lookup.items():
        horizons = list(fv.keys())
        print(f"  {fk}: {horizons}")

    # 保存评估结果
    if test_stats:
        eval_path = OUTPUT_DIR / "walk_forward_eval.json"
        with open(eval_path, "w", encoding="utf-8") as f:
            json.dump({
                "train_period": "2025年",
                "test_period": "2026年",
                "test_stats": test_stats,
                "full_samples": len(df),
            }, f, ensure_ascii=False, indent=2, default=str)
        print(f"评估结果已保存: {eval_path}")


if __name__ == "__main__":
    main()
