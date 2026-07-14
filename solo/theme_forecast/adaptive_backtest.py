# -*- coding: utf-8 -*-
"""
状态自适应回测验证

对比3种方案的IC和分组收益：
1. 固定权重（原predictor）
2. 状态自适应（adaptive_predictor）
3. 状态自适应+时序因子（完整版）

核心验证：抱团市期间，自适应方案是否比固定权重更好
"""
import os
import sys
import json
import time
import struct
import argparse
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from theme_forecast.factors import momentum, synergy
from theme_forecast.factors import timeseries as ts_factors
from theme_forecast.predictor import load_prob_lookup, FACTOR_WEIGHTS, FACTOR_NAMES
from theme_forecast.adaptive_predictor import fuse_probability_adaptive
from theme_forecast.regime_detector import detect_regime, calc_all_theme_rs, get_regime_factor_weights

TDX_PATH = r"C:\new_tdx"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"


# ====================================================================
# 通达信数据读取
# ====================================================================
def parse_tdx_day_file(filepath):
    if not os.path.exists(filepath):
        return None
    records = []
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(32)
            if not chunk or len(chunk) < 32:
                break
            date_int = struct.unpack("<i", chunk[0:4])[0]
            open_p = struct.unpack("<i", chunk[4:8])[0] / 100.0
            high_p = struct.unpack("<i", chunk[8:12])[0] / 100.0
            low_p = struct.unpack("<i", chunk[12:16])[0] / 100.0
            close_p = struct.unpack("<i", chunk[16:20])[0] / 100.0
            amount_yuan = struct.unpack("<f", chunk[20:24])[0]
            vol_shares = struct.unpack("<i", chunk[24:28])[0] / 100.0
            records.append({
                "trade_date": str(date_int),
                "open": open_p, "high": high_p, "low": low_p, "close": close_p,
                "vol": vol_shares, "amount": round(amount_yuan / 1000, 3),
            })
    if not records:
        return None
    df = pd.DataFrame(records).sort_values("trade_date").reset_index(drop=True)
    df["pct_chg"] = df["close"].pct_change().fillna(0) * 100
    return df


def ts_code_to_tdx_file(ts_code):
    sym, market = ts_code.split(".")
    if market == "SH":
        return os.path.join(TDX_PATH, "vipdoc", "sh", "lday", f"sh{sym}.day")
    elif market == "SZ":
        return os.path.join(TDX_PATH, "vipdoc", "sz", "lday", f"sz{sym}.day")
    return None


# ====================================================================
# 回测主流程
# ====================================================================
def run_adaptive_backtest(themes, start_date, end_date, horizon=5, min_stocks=5):
    """
    状态自适应回测

    每日流程：
    1. 计算所有主题的RS → 识别市场状态
    2. 根据市场状态选择因子权重
    3. 对每个主题计算全部因子（含时序因子）
    4. 用自适应权重融合 → 预测概率
    5. 记录预测概率 vs 实际收益
    """
    print(f"\n[回测] 范围: {start_date}~{end_date}, 周期: {horizon}日")

    # 1. 加载条件概率查表
    prob_lookup = load_prob_lookup()
    if not prob_lookup:
        print("[回测] 错误: 未找到prob_lookup.json")
        return pd.DataFrame()

    # 2. 收集股票代码
    all_codes = set()
    valid_themes = {}
    for name, stocks in themes.items():
        codes = [s["code"] for s in stocks if isinstance(s, dict) and "code" in s]
        if len(codes) >= min_stocks:
            valid_themes[name] = codes
            all_codes.update(codes)
    all_codes.add("000001.SH")

    print(f"[回测] 有效主题: {len(valid_themes)}, 总股票: {len(all_codes)}")

    # 3. 加载K线
    buffer_start = (datetime.strptime(start_date, "%Y%m%d") - timedelta(days=150)).strftime("%Y%m%d")
    buffer_end = (datetime.strptime(end_date, "%Y%m%d") + timedelta(days=40)).strftime("%Y%m%d")
    print(f"[回测] 加载K线 {buffer_start}~{buffer_end}...")

    all_klines = {}
    missing = 0
    for i, code in enumerate(all_codes):
        tdx_file = ts_code_to_tdx_file(code)
        if not tdx_file or not os.path.exists(tdx_file):
            missing += 1
            continue
        df = parse_tdx_day_file(tdx_file)
        if df is None or df.empty:
            missing += 1
            continue
        df = df[(df["trade_date"] >= buffer_start) & (df["trade_date"] <= buffer_end)].copy()
        if len(df) >= 30:
            all_klines[code] = df
        if (i + 1) % 500 == 0:
            print(f"  进度: {i+1}/{len(all_codes)} (缺失{missing})", flush=True)
    print(f"  完成: 成功{len(all_klines)}, 缺失{missing}")

    # 构建日期→位置索引
    stock_date_pos = {code: {d: i for i, d in enumerate(df["trade_date"].tolist())}
                       for code, df in all_klines.items()}

    market_df = all_klines.get("000001.SH")
    if market_df is None:
        print("[回测] 大盘指数加载失败")
        return pd.DataFrame()
    market_df = market_df.reset_index(drop=True)
    date_to_pos = {d: i for i, d in enumerate(market_df["trade_date"].tolist())}

    all_dates = sorted(market_df[(market_df["trade_date"] >= start_date) &
                                  (market_df["trade_date"] <= end_date)]["trade_date"].tolist())
    print(f"[回测] 回测交易日: {len(all_dates)}天")

    # 4. 逐日回测
    records = []
    rs_history = []  # 用于市场状态识别的历史RS数据
    regime_counts = defaultdict(int)
    t_start = time.time()

    for i, t_date in enumerate(all_dates):
        if i % 20 == 0:
            elapsed = time.time() - t_start
            pct = i / len(all_dates) * 100
            print(f"  进度: {i}/{len(all_dates)} ({pct:.0f}%) 耗时:{elapsed:.0f}s 样本:{len(records)}", flush=True)

        t_pos = date_to_pos.get(t_date)
        if t_pos is None or t_pos + horizon >= len(market_df):
            continue
        mkt_up_to_t = market_df.iloc[:t_pos + 1].copy()
        if len(mkt_up_to_t) < 30:
            continue
        future_date = market_df.iloc[t_pos + horizon]["trade_date"]

        # === 步骤1: 计算所有主题RS → 识别市场状态 ===
        all_theme_klines_today = {}
        for theme_name, codes in valid_themes.items():
            theme_klines = {}
            for code in codes:
                if code not in all_klines:
                    continue
                date_map = stock_date_pos.get(code)
                if date_map is None:
                    continue
                t_pos_stock = date_map.get(t_date)
                if t_pos_stock is None or t_pos_stock < 25:
                    continue
                theme_klines[code] = all_klines[code].iloc[:t_pos_stock + 1].copy()
            if len(theme_klines) >= min_stocks:
                all_theme_klines_today[theme_name] = theme_klines

        if len(all_theme_klines_today) < 5:
            continue

        rs_dict = calc_all_theme_rs(all_theme_klines_today, mkt_up_to_t)
        if len(rs_dict) < 5:
            continue

        rs_values = list(rs_dict.values())
        sorted_rs = sorted(rs_dict.items(), key=lambda x: -x[1])
        top5_rs_mean = float(np.mean([t[1] for t in sorted_rs[:5]]))
        all_rs_std = float(np.std(rs_values))
        concentration = top5_rs_mean * all_rs_std

        rs_history.append({
            "date": t_date,
            "rs_dict": rs_dict,
            "concentration": concentration,
        })

        # 识别市场状态
        regime_info = detect_regime(rs_history)
        regime = regime_info["regime"]
        regime_counts[regime] += 1

        # === 步骤2: 逐主题计算因子 + 预测 ===
        for theme_name, theme_klines in all_theme_klines_today.items():
            try:
                # 计算全部因子
                theme_index = momentum.calc_theme_index(theme_klines)
                if theme_index.empty:
                    continue

                mom = momentum.calc_all_momentum(theme_klines, mkt_up_to_t)
                syn = synergy.calc_all_synergy(theme_klines)
                tsf = ts_factors.calc_all_timeseries_factors(
                    theme_klines, theme_index, mkt_up_to_t, all_theme_klines_today
                )

                all_factors = {}
                all_factors.update(mom)
                all_factors.update(syn)
                all_factors.update(tsf)
                all_factors.pop("theme_index_close", None)

                # 状态自适应预测（传入theme_name用于轮动市主题分化）
                pred_adaptive = fuse_probability_adaptive(
                    all_factors, regime_info, prob_lookup,
                    theme_name=theme_name,
                )
                prob_adaptive = pred_adaptive["probability"]
                fp_adaptive = pred_adaptive.get("future_probs", {}).get(f"{horizon}d", {}).get("prob", prob_adaptive)

                # 固定权重预测（对比基准）
                from theme_forecast.predictor import fuse_probability
                # 临时只传有时序的因子给固定权重
                fixed_factors = {k: v for k, v in all_factors.items() if k in FACTOR_WEIGHTS}
                pred_fixed = fuse_probability(fixed_factors)
                prob_fixed = pred_fixed["probability"]
                fp_fixed = pred_fixed.get("future_probs", {}).get(f"{horizon}d", {}).get("prob", prob_fixed)

                # 计算实际收益
                rets = []
                for code, df in theme_klines.items():
                    t_close = df["close"].iloc[-1]
                    f_pos = stock_date_pos.get(code, {}).get(future_date)
                    if f_pos is not None:
                        f_close = all_klines[code].iloc[f_pos]["close"]
                        rets.append((f_close / t_close - 1) * 100)

                if not rets:
                    continue

                actual_ret = float(np.mean(rets))
                actual_up = 1 if actual_ret > 0 else 0

                # 时序因子分数
                rs_slope_score = tsf.get("rs_slope", {}).get("score", 50)
                conc_score = tsf.get("concentration_change", {}).get("score", 50)
                leader_lag_score = tsf.get("leader_lag", {}).get("score", 50)

                records.append({
                    "trade_date": t_date,
                    "theme": theme_name,
                    "regime": regime,
                    "concentration": round(concentration, 4),
                    # 固定权重
                    "prob_fixed": round(prob_fixed, 1),
                    "fp_fixed_5d": round(fp_fixed, 1),
                    # 自适应
                    "prob_adaptive": round(prob_adaptive, 1),
                    "fp_adaptive_5d": round(fp_adaptive, 1),
                    # 时序因子
                    "f_rs_slope": rs_slope_score,
                    "f_concentration": conc_score,
                    "f_leader_lag": leader_lag_score,
                    # 原因子
                    "f_rs": all_factors.get("relative_strength", {}).get("score", 50),
                    "f_mom": all_factors.get("momentum_acceleration", {}).get("score", 50),
                    # 实际
                    "actual_ret": actual_ret,
                    "actual_up": actual_up,
                })

            except Exception:
                continue

    elapsed = time.time() - t_start
    print(f"\n[回测] 完成! 样本:{len(records)} 耗时:{elapsed:.0f}s")
    print(f"[回测] 市场状态分布: {dict(regime_counts)}")
    return pd.DataFrame(records)


# ====================================================================
# 评估对比
# ====================================================================
def evaluate_comparison(df: pd.DataFrame, horizon: int) -> dict:
    """对比固定权重 vs 状态自适应"""
    if df.empty:
        return {}

    results = {}

    # 整体对比
    for method, prob_col, fp_col in [
        ("固定权重", "prob_fixed", "fp_fixed_5d"),
        ("自适应", "prob_adaptive", "fp_adaptive_5d"),
    ]:
        sub = df.dropna(subset=[fp_col])
        if sub.empty:
            continue

        ic = sub[fp_col].corr(sub["actual_ret"], method="spearman")
        accuracy = ((sub[fp_col] >= 50) == (sub["actual_up"] == 1)).mean() * 100

        # 分组
        sub = sub.copy()
        sub["group"] = pd.qcut(sub[fp_col], q=5, labels=["Q1", "Q2", "Q3", "Q4", "Q5"], duplicates="drop")
        group = sub.groupby("group", observed=True).agg(
            n=("actual_ret", "count"),
            avg_pred=(fp_col, "mean"),
            avg_ret=("actual_ret", "mean"),
            up_rate=("actual_up", "mean"),
        ).reset_index()

        high = sub[sub[fp_col] >= 55]
        low = sub[sub[fp_col] <= 45]
        long_short = (high["actual_ret"].mean() - low["actual_ret"].mean()) if len(high) > 0 and len(low) > 0 else 0

        results[method] = {
            "n_samples": len(sub),
            "ic": round(ic, 4),
            "accuracy": round(accuracy, 1),
            "high_n": len(high),
            "high_up_rate": round(high["actual_up"].mean() * 100, 1) if len(high) > 0 else 0,
            "high_avg_ret": round(high["actual_ret"].mean(), 2) if len(high) > 0 else 0,
            "low_n": len(low),
            "low_up_rate": round(low["actual_up"].mean() * 100, 1) if len(low) > 0 else 0,
            "low_avg_ret": round(low["actual_ret"].mean(), 2) if len(low) > 0 else 0,
            "long_short": round(long_short, 2),
            "group_stats": group.to_dict("records"),
        }

    # 按市场状态分组对比
    regime_comparison = {}
    for regime in df["regime"].unique():
        regime_df = df[df["regime"] == regime]
        regime_comparison[regime] = {
            "n_samples": len(regime_df),
            "fixed": {
                "ic": round(regime_df["fp_fixed_5d"].corr(regime_df["actual_ret"], method="spearman"), 4)
                       if len(regime_df) > 10 else 0,
                "long_short": _calc_long_short(regime_df, "fp_fixed_5d"),
            },
            "adaptive": {
                "ic": round(regime_df["fp_adaptive_5d"].corr(regime_df["actual_ret"], method="spearman"), 4)
                       if len(regime_df) > 10 else 0,
                "long_short": _calc_long_short(regime_df, "fp_adaptive_5d"),
            },
        }

    results["regime_comparison"] = regime_comparison
    results["horizon"] = horizon
    results["date_range"] = f"{df['trade_date'].min()}~{df['trade_date'].max()}"

    return results


def _calc_long_short(df: pd.DataFrame, col: str) -> float:
    sub = df.dropna(subset=[col])
    if len(sub) < 10:
        return 0
    high = sub[sub[col] >= 55]
    low = sub[sub[col] <= 45]
    if len(high) > 0 and len(low) > 0:
        return round(high["actual_ret"].mean() - low["actual_ret"].mean(), 2)
    return 0


def format_comparison_report(eval_result: dict) -> str:
    lines = []
    lines.append("=" * 70)
    lines.append("  状态自适应 vs 固定权重 回测对比报告")
    lines.append("=" * 70)
    lines.append(f"  回测范围: {eval_result['date_range']}")
    lines.append(f"  未来周期: {eval_result['horizon']}日")
    lines.append("")

    # 整体对比
    lines.append("  ── 整体对比 ──")
    lines.append(f"  {'方法':<10} {'样本':>6} {'IC':>8} {'准确率':>6} {'多空差':>8} {'高概率组上涨率':>12}")
    lines.append(f"  {'-'*56}")
    for method in ["固定权重", "自适应"]:
        if method not in eval_result:
            continue
        r = eval_result[method]
        lines.append(f"  {method:<10} {r['n_samples']:>6} {r['ic']:>8} {r['accuracy']:>5}% "
                      f"{r['long_short']:>+7.2f}% {r['high_up_rate']:>11.1f}%")
    lines.append("")

    # 按市场状态分组
    lines.append("  ── 按市场状态分组对比 ──")
    lines.append(f"  {'状态':<6} {'样本':>6} {'固定IC':>8} {'自适应IC':>8} {'IC提升':>8} {'固定多空':>8} {'自适应多空':>10}")
    lines.append(f"  {'-'*62}")
    for regime, data in eval_result.get("regime_comparison", {}).items():
        fixed_ic = data["fixed"]["ic"]
        adapt_ic = data["adaptive"]["ic"]
        ic_improve = adapt_ic - fixed_ic
        fixed_ls = data["fixed"]["long_short"]
        adapt_ls = data["adaptive"]["long_short"]
        lines.append(f"  {regime:<6} {data['n_samples']:>6} {fixed_ic:>8} {adapt_ic:>8} "
                      f"{ic_improve:>+8} {fixed_ls:>+7.2f}% {adapt_ls:>+9.2f}%")
    lines.append("")

    # 分组收益对比
    for method in ["固定权重", "自适应"]:
        if method not in eval_result:
            continue
        r = eval_result[method]
        lines.append(f"  ── {method} 分组收益 ──")
        lines.append(f"  {'分组':<6} {'样本':>6} {'预测概率':>8} {'实际收益':>8} {'上涨率':>8}")
        lines.append(f"  {'-'*40}")
        for g in r["group_stats"]:
            lines.append(f"  {g['group']:<6} {g['n']:>6} {g['avg_pred']:>7.1f}% "
                          f"{g['avg_ret']:>+7.2f}% {g['up_rate']*100:>7.1f}%")
        lines.append("")

    lines.append("=" * 70)
    return "\n".join(lines)


# ====================================================================
# 主入口
# ====================================================================
def main():
    parser = argparse.ArgumentParser(description="状态自适应回测验证")
    parser.add_argument("--start", type=str, default="20250101")
    parser.add_argument("--end", type=str, default="")
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument("--min-stocks", type=int, default=5)
    args = parser.parse_args()

    end_date = args.end
    if not end_date:
        mkt_file = ts_code_to_tdx_file("000001.SH")
        mkt_df = parse_tdx_day_file(mkt_file)
        end_date = mkt_df["trade_date"].iloc[-1] if mkt_df is not None else "20260713"

    print("=" * 70)
    print(f"状态自适应回测验证")
    print(f"范围: {args.start}~{end_date} | 周期: {args.horizon}日")
    print("=" * 70)

    # 加载主题
    print("\n[1/3] 加载主题...")
    from theme_forecast import data_loader as dl
    themes = dl.load_theme_stocks()
    print(f"  共 {len(themes)} 个主题")

    # 回测
    print(f"\n[2/3] 运行回测...")
    df = run_adaptive_backtest(themes, args.start, end_date, args.horizon, args.min_stocks)

    if df.empty:
        print("回测无数据!")
        return

    # 评估
    print(f"\n[3/3] 评估对比...")
    eval_result = evaluate_comparison(df, args.horizon)
    report = format_comparison_report(eval_result)
    print(report)

    # 保存
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / f"adaptive_backtest_{args.start}_{end_date}_h{args.horizon}.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"\n回测明细: {csv_path}")

    eval_path = OUTPUT_DIR / f"adaptive_eval_{args.start}_{end_date}_h{args.horizon}.json"
    with open(eval_path, "w", encoding="utf-8") as f:
        json.dump(eval_result, f, ensure_ascii=False, indent=2, default=str)
    print(f"评估结果: {eval_path}")


if __name__ == "__main__":
    main()
