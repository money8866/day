# -*- coding: utf-8 -*-
"""
历史条件概率回测系统

核心逻辑：
1. 一次性加载所有主题成份股的完整历史K线
2. 遍历过去N个交易日，对每个主题计算因子 + 未来收益
3. 按因子分数分箱，统计每组的未来上涨概率

输出：
- 当因子X = 某分箱时，未来3/5/10日上涨概率
- 样本数、平均收益、中位收益
"""
import os
import sys
import json
import time
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from theme_forecast import data_loader as dl
from theme_forecast.factors import momentum, synergy


# ====================================================================
# 数据加载：一次性加载完整历史K线
# ====================================================================
def load_all_klines(ts_codes: list, start_date: str, end_date: str) -> dict:
    """一次性加载所有股票的完整历史K线"""
    print(f"  加载 {len(ts_codes)} 只股票K线 {start_date}~{end_date}...")
    klines = dl.load_klines(ts_codes, start_date, end_date)
    print(f"  成功加载 {len(klines)} 只")
    return klines


def load_market_index(start_date: str, end_date: str) -> pd.DataFrame:
    """加载大盘指数"""
    df_inst = dl.get_df()
    return df_inst.get_index_daily(ts_code="000001.SH", start_date=start_date, end_date=end_date)


# ====================================================================
# 回测引擎
# ====================================================================
def run_backtest(themes: dict, n_months: int = 6, horizon_days: list = None,
                 min_stocks: int = 5) -> pd.DataFrame:
    """
    历史回测

    Args:
        themes: 主题→成份股映射
        n_months: 回测月数
        horizon_days: 未来收益周期 [3, 5, 10]
        min_stocks: 主题最少成份股数

    Returns:
        DataFrame: 每行 = (trade_date, theme, factor_scores, future_returns)
    """
    if horizon_days is None:
        horizon_days = [3, 5, 10]

    # 1. 确定日期范围（多留horizon_max天用于未来收益计算）
    horizon_max = max(horizon_days) + 5
    end_date = dl.get_last_trade_date()
    # 总共需要 n_months + horizon_max 的数据
    total_days = n_months * 30 + horizon_max + 30
    start_date = (datetime.strptime(end_date, "%Y%m%d") - timedelta(days=total_days)).strftime("%Y%m%d")

    print(f"[回测] 日期范围: {start_date} ~ {end_date} (约{n_months}个月+缓冲)")

    # 2. 收集所有需要的股票代码
    all_codes = set()
    valid_themes = {}
    for name, stocks in themes.items():
        codes = [s["code"] for s in stocks if isinstance(s, dict) and "code" in s]
        if len(codes) >= min_stocks:
            valid_themes[name] = codes
            all_codes.update(codes)

    print(f"[回测] 有效主题: {len(valid_themes)} 个, 总股票: {len(all_codes)} 只")

    # 3. 一次性加载所有K线
    all_klines = load_all_klines(list(all_codes), start_date, end_date)
    market_index = load_market_index(start_date, end_date)

    if market_index is None or market_index.empty:
        print("[回测] 大盘指数加载失败")
        return pd.DataFrame()

    # 4. 获取交易日列表（K线中实际存在的日期）
    all_dates = sorted(set(market_index["trade_date"].astype(str).tolist()))
    # 回测起点：留够horizon_max天用于未来收益
    backtest_end_idx = len(all_dates) - horizon_max
    backtest_start_idx = len(all_dates) - n_months * 22  # 约22交易日/月
    backtest_start_idx = max(0, backtest_start_idx)

    backtest_dates = all_dates[backtest_start_idx:backtest_end_idx]
    print(f"[回测] 回测交易日数: {len(backtest_dates)} 天")

    # 5. 逐日逐主题计算
    records = []
    t_start = time.time()

    for i, t_date in enumerate(backtest_dates):
        if i % 10 == 0:
            elapsed = time.time() - t_start
            print(f"  进度: {i}/{len(backtest_dates)} ({i/len(backtest_dates)*100:.0f}%) 耗时:{elapsed:.0f}s")

        # 找到t_date在all_dates中的位置
        t_idx = all_dates.index(t_date)

        # 计算未来收益的日期
        future_dates = {}
        for h in horizon_days:
            if t_idx + h < len(all_dates):
                future_dates[h] = all_dates[t_idx + h]

        # 大盘指数（截止t_date）
        mkt_up_to_t = market_index[market_index["trade_date"].astype(str) <= t_date].copy()

        for theme_name, codes in valid_themes.items():
            # 构建该主题截止t_date的K线
            theme_klines = {}
            for code in codes:
                if code not in all_klines:
                    continue
                df = all_klines[code]
                df_t = df[df["trade_date"].astype(str) <= t_date].copy()
                if len(df_t) >= 20:  # 至少20条数据
                    theme_klines[code] = df_t

            if len(theme_klines) < min_stocks:
                continue

            # 计算因子（只用K线类因子）
            try:
                mom = momentum.calc_all_momentum(theme_klines, mkt_up_to_t)
                syn = synergy.calc_all_synergy(theme_klines)

                # 计算未来收益
                future_rets = {}
                for h, future_date in future_dates.items():
                    rets = []
                    for code, df in theme_klines.items():
                        full_df = all_klines.get(code)
                        if full_df is None:
                            continue
                        t_close = df["close"].iloc[-1] if "close" in df.columns else None
                        f_row = full_df[full_df["trade_date"].astype(str) == future_date]
                        if t_close and not f_row.empty and "close" in f_row.columns:
                            f_close = f_row["close"].iloc[0]
                            ret = (f_close / t_close - 1) * 100
                            rets.append(ret)
                    if rets:
                        future_rets[f"ret_{h}d"] = float(np.mean(rets))

                # 记录
                record = {
                    "trade_date": t_date,
                    "theme": theme_name,
                    "n_stocks": len(theme_klines),
                }

                # 提取因子分数
                for key, val in mom.items():
                    if key == "theme_index_close":
                        continue
                    if isinstance(val, dict) and "score" in val:
                        record[f"f_{key}"] = val["score"]
                        record[f"f_{key}_signal"] = val.get("signal", "")

                for key, val in syn.items():
                    if isinstance(val, dict) and "score" in val:
                        record[f"f_{key}"] = val["score"]
                        record[f"f_{key}_signal"] = val.get("signal", "")

                # 未来收益
                for h in horizon_days:
                    col = f"ret_{h}d"
                    if col in future_rets:
                        record[col] = future_rets[col]
                        record[f"up_{h}d"] = 1 if future_rets[col] > 0 else 0

                records.append(record)

            except Exception as e:
                continue

    print(f"[回测] 完成! 总样本: {len(records)} 条, 耗时: {time.time()-t_start:.0f}s")

    df = pd.DataFrame(records)
    return df


# ====================================================================
# 条件概率统计
# ====================================================================
def calc_conditional_probability(backtest_df: pd.DataFrame, factor_col: str,
                                  horizon: int, n_bins: int = 5) -> pd.DataFrame:
    """
    统计某因子分箱对应的未来上涨概率

    Returns:
        DataFrame: bin_range, n_samples, up_prob, avg_ret, median_ret
    """
    ret_col = f"ret_{horizon}d"
    up_col = f"up_{horizon}d"

    if factor_col not in backtest_df.columns or ret_col not in backtest_df.columns:
        return pd.DataFrame()

    valid = backtest_df[[factor_col, ret_col, up_col]].dropna()
    if len(valid) < 20:
        return pd.DataFrame()

    # 分箱（等频分箱）
    try:
        valid["bin"] = pd.qcut(valid[factor_col], q=n_bins, duplicates="drop", labels=False)
    except Exception:
        return pd.DataFrame()

    results = []
    for bin_idx in sorted(valid["bin"].unique()):
        bin_data = valid[valid["bin"] == bin_idx]
        if len(bin_data) < 5:
            continue

        bin_min = bin_data[factor_col].min()
        bin_max = bin_data[factor_col].max()
        up_prob = bin_data[up_col].mean() * 100
        avg_ret = bin_data[ret_col].mean()
        median_ret = bin_data[ret_col].median()
        win_loss_ratio = abs(bin_data[ret_col].mean() / bin_data[ret_col].std()) if bin_data[ret_col].std() > 0 else 0

        results.append({
            "factor": factor_col,
            "horizon": f"{horizon}d",
            "bin_range": f"[{bin_min:.0f}, {bin_max:.0f}]",
            "bin_min": bin_min,
            "bin_max": bin_max,
            "n_samples": len(bin_data),
            "up_prob": round(up_prob, 1),
            "avg_ret": round(avg_ret, 2),
            "median_ret": round(median_ret, 2),
            "win_loss_ratio": round(win_loss_ratio, 3),
        })

    return pd.DataFrame(results)


def calc_all_conditional_probabilities(backtest_df: pd.DataFrame,
                                        horizons: list = None) -> pd.DataFrame:
    """计算所有因子的条件概率"""
    if horizons is None:
        horizons = [3, 5, 10]

    factor_cols = [c for c in backtest_df.columns if c.startswith("f_") and not c.endswith("_signal")]

    all_results = []
    for factor_col in factor_cols:
        for h in horizons:
            cp = calc_conditional_probability(backtest_df, factor_col, h)
            if not cp.empty:
                all_results.append(cp)

    if all_results:
        return pd.concat(all_results, ignore_index=True)
    return pd.DataFrame()


# ====================================================================
# 输出格式化
# ====================================================================
def format_probability_table(cp_df: pd.DataFrame) -> str:
    """格式化条件概率表"""
    if cp_df.empty:
        return "无数据"

    lines = []
    factor_name_map = {
        "f_relative_strength": "相对强度",
        "f_momentum_acceleration": "动量加速度",
        "f_adx_trend": "ADX趋势",
        "f_synergy_coefficient": "协同度",
        "f_leadership_divergence": "分化度",
        "f_breakout_ratio": "突破比例",
    }

    for factor in cp_df["factor"].unique():
        name = factor_name_map.get(factor, factor)
        factor_data = cp_df[cp_df["factor"] == factor]
        lines.append(f"\n{'='*70}")
        lines.append(f"【{name}】 ({factor})")
        lines.append(f"{'='*70}")

        for h in ["3d", "5d", "10d"]:
            h_data = factor_data[factor_data["horizon"] == h]
            if h_data.empty:
                continue

            lines.append(f"\n  未来{h}上涨概率:")
            lines.append(f"  {'分箱范围':<16} {'样本':>6} {'上涨概率':>8} {'平均收益':>8} {'中位收益':>8}")
            lines.append(f"  {'-'*52}")

            for _, row in h_data.iterrows():
                prob = row["up_prob"]
                # 概率着色标记
                if prob >= 60:
                    mark = "▲"
                elif prob <= 40:
                    mark = "▼"
                else:
                    mark = "─"

                lines.append(
                    f"  {mark} {row['bin_range']:<14} {row['n_samples']:>6} "
                    f"{prob:>7.1f}% {row['avg_ret']:>7.2f}% {row['median_ret']:>7.2f}%"
                )

    return "\n".join(lines)


def save_results(backtest_df: pd.DataFrame, cp_df: pd.DataFrame, output_dir: str):
    """保存结果"""
    os.makedirs(output_dir, exist_ok=True)

    # 回测明细
    backtest_path = os.path.join(output_dir, "backtest_samples.csv")
    backtest_df.to_csv(backtest_path, index=False, encoding="utf-8-sig")
    print(f"回测样本已保存: {backtest_path} ({len(backtest_df)}条)")

    # 条件概率表
    cp_path = os.path.join(output_dir, "conditional_probability.csv")
    cp_df.to_csv(cp_path, index=False, encoding="utf-8-sig")
    print(f"条件概率表已保存: {cp_path}")

    # 概率查表JSON（用于预测系统集成）
    prob_lookup = {}
    for factor in cp_df["factor"].unique():
        factor_data = cp_df[cp_df["factor"] == factor]
        prob_lookup[factor] = {}
        for h in factor_data["horizon"].unique():
            h_data = factor_data[factor_data["horizon"] == h]
            bins = []
            for _, row in h_data.iterrows():
                bins.append({
                    "bin_min": float(row["bin_min"]),
                    "bin_max": float(row["bin_max"]),
                    "up_prob": float(row["up_prob"]),
                    "avg_ret": float(row["avg_ret"]),
                    "n_samples": int(row["n_samples"]),
                })
            prob_lookup[factor][h] = bins

    lookup_path = os.path.join(output_dir, "prob_lookup.json")
    with open(lookup_path, "w", encoding="utf-8") as f:
        json.dump(prob_lookup, f, ensure_ascii=False, indent=2)
    print(f"概率查表已保存: {lookup_path}")


# ====================================================================
# 主入口
# ====================================================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description="历史条件概率回测")
    parser.add_argument("--months", type=int, default=6, help="回测月数")
    parser.add_argument("--min-stocks", type=int, default=5, help="主题最少成份股数")
    args = parser.parse_args()

    print("=" * 70)
    print(f"历史条件概率回测系统 (回测{args.months}个月)")
    print("=" * 70)

    # 1. 加载主题
    print("\n[1/4] 加载主题成份股...")
    themes = dl.load_theme_stocks()
    print(f"  共 {len(themes)} 个主题")

    # 2. 运行回测
    print(f"\n[2/4] 运行历史回测...")
    backtest_df = run_backtest(themes, n_months=args.months, min_stocks=args.min_stocks)

    if backtest_df.empty:
        print("回测无数据!")
        return

    # 统计
    print(f"\n  回测样本: {len(backtest_df)} 条")
    print(f"  覆盖主题: {backtest_df['theme'].nunique()} 个")
    print(f"  覆盖交易日: {backtest_df['trade_date'].nunique()} 天")
    print(f"  日期范围: {backtest_df['trade_date'].min()} ~ {backtest_df['trade_date'].max()}")

    # 未来收益分布
    print(f"\n  未来收益分布:")
    for h in [3, 5, 10]:
        col = f"ret_{h}d"
        up_col = f"up_{h}d"
        if col in backtest_df.columns:
            valid = backtest_df[col].dropna()
            up_rate = backtest_df[up_col].mean() * 100 if up_col in backtest_df.columns else 0
            print(f"    {h}日: 样本{len(valid)}, 上涨率{up_rate:.1f}%, "
                  f"均值{valid.mean():.2f}%, 中位{valid.median():.2f}%")

    # 3. 计算条件概率
    print(f"\n[3/4] 计算条件概率...")
    cp_df = calc_all_conditional_probabilities(backtest_df)
    print(f"  条件概率表: {len(cp_df)} 行")

    # 4. 输出
    print(f"\n[4/4] 输出结果...")
    output_dir = str(PROJECT_ROOT / "output")
    save_results(backtest_df, cp_df, output_dir)

    # 打印概率表
    report = format_probability_table(cp_df)
    print(report)


if __name__ == "__main__":
    main()
