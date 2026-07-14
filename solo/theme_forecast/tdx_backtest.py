# -*- coding: utf-8 -*-
"""
主题涨跌概率预测 - 通达信回测

数据源：通达信本地.day文件（不复权）
回测范围：20250101起
因子：6个K线类因子（动量+协同度）
预测：基于条件概率查表，输出未来3/5/10日上涨概率
评估：预测准确率、IC、分组收益

用法:
    python -m theme_forecast.tdx_backtest --start 20250101
    python -m theme_forecast.tdx_backtest --start 20250101 --horizon 5
"""
import os
import sys
import struct
import json
import time
import argparse
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from theme_forecast.factors import momentum, synergy
from theme_forecast.predictor import load_prob_lookup, calc_future_probability, FACTOR_WEIGHTS, FACTOR_NAMES

TDX_PATH = r"C:\new_tdx"


# ====================================================================
# 通达信.day文件读取
# ====================================================================
def parse_tdx_day_file(filepath):
    """解析通达信.day文件"""
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
            date_str = str(date_int)
            records.append({
                "trade_date": date_str,
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "close": close_p,
                "vol": vol_shares,
                "amount": round(amount_yuan / 1000, 3),
            })
    if not records:
        return None
    df = pd.DataFrame(records)
    df = df.sort_values("trade_date").reset_index(drop=True)
    df["pct_chg"] = df["close"].pct_change() * 100
    df["pct_chg"] = df["pct_chg"].fillna(0)
    return df


def ts_code_to_tdx_file(ts_code):
    """ts_code → 通达信.day文件路径"""
    sym, market = ts_code.split(".")
    if market == "SH":
        prefix = "sh"
        subdir = "sh"
    elif market == "SZ":
        prefix = "sz"
        subdir = "sz"
    else:
        return None
    return os.path.join(TDX_PATH, "vipdoc", subdir, "lday", f"{prefix}{sym}.day")


def load_all_tdx_klines(ts_codes, start_date, end_date):
    """批量加载所有股票的通达信K线（一次性读取，按日期过滤）"""
    print(f"  加载 {len(ts_codes)} 只股票通达信K线...")
    klines = {}
    missing = 0
    for i, code in enumerate(ts_codes):
        tdx_file = ts_code_to_tdx_file(code)
        if not tdx_file or not os.path.exists(tdx_file):
            missing += 1
            continue
        df = parse_tdx_day_file(tdx_file)
        if df is None or df.empty:
            missing += 1
            continue
        # 按日期过滤
        df = df[(df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)].copy()
        if len(df) >= 30:
            klines[code] = df

        if (i + 1) % 500 == 0:
            print(f"    进度: {i+1}/{len(ts_codes)} (缺失{missing})")

    print(f"  完成: 成功{len(klines)}只, 缺失{missing}只")
    return klines


# ====================================================================
# 回测引擎
# ====================================================================
def run_tdx_backtest(themes, start_date, end_date, horizon=5, min_stocks=5):
    """
    通达信回测主流程

    Args:
        themes: 主题→成份股映射
        start_date: 回测起始日期
        end_date: 回测结束日期
        horizon: 未来收益周期（天）
        min_stocks: 主题最少成份股数

    Returns:
        DataFrame: 每行 = (trade_date, theme, predicted_prob, actual_return, ...)
    """
    print(f"\n[回测] 范围: {start_date} ~ {end_date}, 未来周期: {horizon}日")

    # 1. 加载条件概率查表
    print("[回测] 加载条件概率查表...")
    prob_lookup = load_prob_lookup()
    if not prob_lookup:
        print("[回测] 错误: 未找到prob_lookup.json，请先运行historical_backtest")
        return pd.DataFrame()
    print(f"  查表因子: {list(prob_lookup.keys())}")

    # 2. 收集所有股票代码
    all_codes = set()
    valid_themes = {}
    for name, stocks in themes.items():
        codes = [s["code"] for s in stocks if isinstance(s, dict) and "code" in s]
        if len(codes) >= min_stocks:
            valid_themes[name] = codes
            all_codes.update(codes)
    # 加入大盘指数
    all_codes.add("000001.SH")

    print(f"[回测] 有效主题: {len(valid_themes)}, 总股票: {len(all_codes)}")

    # 3. 一次性加载所有K线（含缓冲期：前推90天用于计算因子，后推horizon+5天用于未来收益）
    buffer_start = (datetime.strptime(start_date, "%Y%m%d") - timedelta(days=120)).strftime("%Y%m%d")
    buffer_end = (datetime.strptime(end_date, "%Y%m%d") + timedelta(days=40)).strftime("%Y%m%d")
    all_klines = load_all_tdx_klines(list(all_codes), buffer_start, buffer_end)

    # 为每只股票构建日期→位置映射，加速切片
    print("[回测] 构建日期索引...")
    stock_date_pos = {}
    for code, df in all_klines.items():
        stock_date_pos[code] = {d: i for i, d in enumerate(df["trade_date"].tolist())}

    # 大盘指数
    market_df = all_klines.get("000001.SH")
    if market_df is None or market_df.empty:
        print("[回测] 错误: 大盘指数加载失败")
        return pd.DataFrame()
    market_df = market_df.reset_index(drop=True)  # 关键：重置index为连续整数
    # 构建日期→位置索引的映射，加速查找
    date_to_pos = {d: i for i, d in enumerate(market_df["trade_date"].tolist())}
    print(f"  大盘指数: {len(market_df)}条, {market_df['trade_date'].min()}~{market_df['trade_date'].max()}")

    # 4. 获取回测交易日列表
    all_dates = sorted(market_df[(market_df["trade_date"] >= start_date) &
                                  (market_df["trade_date"] <= end_date)]["trade_date"].tolist())
    print(f"[回测] 回测交易日: {len(all_dates)}天")

    # 5. 逐日逐主题计算
    records = []
    t_start = time.time()

    for i, t_date in enumerate(all_dates):
        if i % 20 == 0:
            elapsed = time.time() - t_start
            pct = (i / len(all_dates)) * 100
            print(f"  进度: {i}/{len(all_dates)} ({pct:.0f}%) 耗时:{elapsed:.0f}s 样本:{len(records)}", flush=True)

        # 大盘指数（截止t_date）— 用位置切片，比过滤快
        t_pos = date_to_pos.get(t_date)
        if t_pos is None:
            continue
        if t_pos + horizon >= len(market_df):
            continue  # 没有足够的未来数据
        mkt_up_to_t = market_df.iloc[:t_pos + 1].copy()
        if len(mkt_up_to_t) < 30:
            continue

        # 未来horizon天的日期
        future_date = market_df.iloc[t_pos + horizon]["trade_date"]

        for theme_name, codes in valid_themes.items():
            # 构建该主题截止t_date的K线（用位置切片加速）
            theme_klines = {}
            future_closes = {}
            for code in codes:
                if code not in all_klines:
                    continue
                df = all_klines[code]
                date_map = stock_date_pos.get(code)
                if date_map is None:
                    continue
                t_pos_stock = date_map.get(t_date)
                if t_pos_stock is None or t_pos_stock < 25:
                    continue
                # 用位置切片，避免日期过滤
                df_t = df.iloc[:t_pos_stock + 1].copy()
                theme_klines[code] = df_t
                # 未来收盘价
                f_pos = date_map.get(future_date)
                if f_pos is not None:
                    future_closes[code] = df.iloc[f_pos]["close"]

            if len(theme_klines) < min_stocks:
                continue

            # 计算因子
            try:
                mom = momentum.calc_all_momentum(theme_klines, mkt_up_to_t)
                syn = synergy.calc_all_synergy(theme_klines)
                all_factors = {}
                all_factors.update(mom)
                all_factors.update(syn)
                all_factors.pop("theme_index_close", None)

                # 计算未来上涨概率
                future_probs = calc_future_probability(all_factors, prob_lookup, [f"{horizon}d"])
                h_key = f"{horizon}d"
                if h_key not in future_probs:
                    continue

                predicted_prob = future_probs[h_key]["prob"]
                predicted_ret = future_probs[h_key]["avg_ret"]
                confidence = future_probs[h_key]["confidence"]

                # 计算实际未来收益
                rets = []
                for code, df in theme_klines.items():
                    t_close = df["close"].iloc[-1]
                    if code in future_closes:
                        ret = (future_closes[code] / t_close - 1) * 100
                        rets.append(ret)

                if not rets:
                    continue

                actual_ret = float(np.mean(rets))
                actual_up = 1 if actual_ret > 0 else 0

                records.append({
                    "trade_date": t_date,
                    "theme": theme_name,
                    "n_stocks": len(theme_klines),
                    "predicted_prob": predicted_prob,
                    "predicted_ret": predicted_ret,
                    "confidence": confidence,
                    "actual_ret": actual_ret,
                    "actual_up": actual_up,
                    # 因子分数
                    "f_rs": all_factors.get("relative_strength", {}).get("score", 50),
                    "f_mom": all_factors.get("momentum_acceleration", {}).get("score", 50),
                    "f_adx": all_factors.get("adx_trend", {}).get("score", 50),
                    "f_syn": all_factors.get("synergy_coefficient", {}).get("score", 50),
                    "f_div": all_factors.get("leadership_divergence", {}).get("score", 50),
                    "f_brk": all_factors.get("breakout_ratio", {}).get("score", 50),
                })

            except Exception:
                continue

    elapsed = time.time() - t_start
    print(f"\n[回测] 完成! 样本:{len(records)} 耗时:{elapsed:.0f}s")

    return pd.DataFrame(records)


# ====================================================================
# 评估指标
# ====================================================================
def evaluate_backtest(df: pd.DataFrame, horizon: int) -> dict:
    """评估回测结果"""
    if df.empty:
        return {}

    n = len(df)
    actual_up_rate = df["actual_up"].mean() * 100

    # 1. 整体准确率
    predicted_up = df["predicted_prob"] >= 50
    actual_up = df["actual_up"] == 1
    accuracy = (predicted_up == actual_up).mean() * 100

    # 2. IC（Spearman秩相关）
    ic = df["predicted_prob"].corr(df["actual_ret"], method="spearman")

    # 3. 分组回测：按预测概率分5组
    df["prob_group"] = pd.qcut(df["predicted_prob"], q=5, labels=["Q1低", "Q2", "Q3", "Q4", "Q5高"], duplicates="drop")
    group_stats = df.groupby("prob_group", observed=True).agg(
        n=("actual_ret", "count"),
        avg_pred_prob=("predicted_prob", "mean"),
        avg_actual_ret=("actual_ret", "mean"),
        actual_up_rate=("actual_up", "mean"),
    ).reset_index()

    # 4. 关键分组：预测>55% vs 预测<45%
    high_prob = df[df["predicted_prob"] >= 55]
    low_prob = df[df["predicted_prob"] <= 45]

    high_up_rate = high_prob["actual_up"].mean() * 100 if len(high_prob) > 0 else 0
    low_up_rate = low_prob["actual_up"].mean() * 100 if len(low_prob) > 0 else 0
    high_avg_ret = high_prob["actual_ret"].mean() if len(high_prob) > 0 else 0
    low_avg_ret = low_prob["actual_ret"].mean() if len(low_prob) > 0 else 0

    # 5. 多空收益差
    long_short_ret = high_avg_ret - low_avg_ret

    # 6. 置信度分组
    conf_stats = {}
    for conf in ["high", "medium", "low"]:
        sub = df[df["confidence"] == conf]
        if len(sub) > 0:
            conf_stats[conf] = {
                "n": len(sub),
                "accuracy": ((sub["predicted_prob"] >= 50) == (sub["actual_up"] == 1)).mean() * 100,
                "up_rate": sub["actual_up"].mean() * 100,
                "avg_ret": sub["actual_ret"].mean(),
            }

    return {
        "n_samples": n,
        "horizon": horizon,
        "date_range": f"{df['trade_date'].min()}~{df['trade_date'].max()}",
        "n_themes": df["theme"].nunique(),
        "actual_up_rate": round(actual_up_rate, 1),
        "accuracy": round(accuracy, 1),
        "ic": round(ic, 4),
        "high_prob_n": len(high_prob),
        "high_prob_up_rate": round(high_up_rate, 1),
        "high_prob_avg_ret": round(high_avg_ret, 2),
        "low_prob_n": len(low_prob),
        "low_prob_up_rate": round(low_up_rate, 1),
        "low_prob_avg_ret": round(low_avg_ret, 2),
        "long_short_ret": round(long_short_ret, 2),
        "group_stats": group_stats.to_dict("records"),
        "conf_stats": conf_stats,
    }


def format_evaluation(eval_result: dict) -> str:
    """格式化评估报告"""
    if not eval_result:
        return "无评估数据"

    lines = []
    lines.append("=" * 70)
    lines.append(f"  主题涨跌概率预测 - 通达信回测评估报告")
    lines.append("=" * 70)
    lines.append(f"  回测范围: {eval_result['date_range']}")
    lines.append(f"  未来周期: {eval_result['horizon']}日")
    lines.append(f"  样本数: {eval_result['n_samples']}")
    lines.append(f"  覆盖主题: {eval_result['n_themes']}个")
    lines.append(f"  实际上涨率: {eval_result['actual_up_rate']}%")
    lines.append("")

    lines.append("  ── 整体预测效果 ──")
    lines.append(f"  准确率(预测>50% vs 实际): {eval_result['accuracy']}%")
    lines.append(f"  IC(Spearman秩相关): {eval_result['ic']}")
    lines.append("")

    lines.append("  ── 分组回测（预测概率5分组） ──")
    lines.append(f"  {'分组':<8} {'样本':>6} {'预测概率':>8} {'实际收益':>8} {'上涨率':>8}")
    lines.append(f"  {'-'*44}")
    for g in eval_result["group_stats"]:
        lines.append(f"  {g['prob_group']:<8} {g['n']:>6} {g['avg_pred_prob']:>7.1f}% "
                      f"{g['avg_actual_ret']:>+7.2f}% {g['actual_up_rate']*100:>7.1f}%")
    lines.append("")

    lines.append("  ── 关键分组对比 ──")
    lines.append(f"  预测≥55%: {eval_result['high_prob_n']}样本, 上涨率{eval_result['high_prob_up_rate']}%, "
                  f"平均收益{eval_result['high_prob_avg_ret']:+.2f}%")
    lines.append(f"  预测≤45%: {eval_result['low_prob_n']}样本, 上涨率{eval_result['low_prob_up_rate']}%, "
                  f"平均收益{eval_result['low_prob_avg_ret']:+.2f}%")
    lines.append(f"  多空收益差: {eval_result['long_short_ret']:+.2f}%")
    lines.append("")

    lines.append("  ── 置信度分组 ──")
    conf_labels = {"high": "高置信", "medium": "中置信", "low": "低置信"}
    for conf, label in conf_labels.items():
        if conf in eval_result["conf_stats"]:
            cs = eval_result["conf_stats"][conf]
            lines.append(f"  {label}: {cs['n']}样本, 准确率{cs['accuracy']:.1f}%, "
                          f"上涨率{cs['up_rate']:.1f}%, 平均{cs['avg_ret']:+.2f}%")

    lines.append("=" * 70)
    return "\n".join(lines)


# ====================================================================
# 主入口
# ====================================================================
def main():
    parser = argparse.ArgumentParser(description="主题预测通达信回测")
    parser.add_argument("--start", type=str, default="20250101", help="起始日期")
    parser.add_argument("--end", type=str, default="", help="结束日期(默认最近交易日)")
    parser.add_argument("--horizon", type=int, default=5, help="未来收益周期")
    parser.add_argument("--min-stocks", type=int, default=5, help="主题最少成份股数")
    args = parser.parse_args()

    end_date = args.end
    if not end_date:
        # 用通达信大盘数据的最后日期
        mkt_file = ts_code_to_tdx_file("000001.SH")
        mkt_df = parse_tdx_day_file(mkt_file)
        end_date = mkt_df["trade_date"].iloc[-1] if mkt_df is not None else "20260713"

    print("=" * 70)
    print(f"主题涨跌概率预测 - 通达信回测")
    print(f"范围: {args.start} ~ {end_date} | 周期: {args.horizon}日")
    print("=" * 70)

    # 1. 加载主题
    print("\n[1/3] 加载主题成份股...")
    from theme_forecast import data_loader as dl
    themes = dl.load_theme_stocks()
    print(f"  共 {len(themes)} 个主题")

    # 2. 运行回测
    print(f"\n[2/3] 运行回测...")
    df = run_tdx_backtest(themes, args.start, end_date, args.horizon, args.min_stocks)

    if df.empty:
        print("回测无数据!")
        return

    # 3. 评估
    print(f"\n[3/3] 评估...")
    eval_result = evaluate_backtest(df, args.horizon)
    report = format_evaluation(eval_result)
    print(report)

    # 保存
    output_dir = PROJECT_ROOT / "theme_forecast" / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    csv_path = output_dir / f"tdx_backtest_{args.start}_{end_date}_h{args.horizon}.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"\n回测明细已保存: {csv_path}")

    eval_path = output_dir / f"tdx_backtest_eval_{args.start}_{end_date}_h{args.horizon}.json"
    with open(eval_path, "w", encoding="utf-8") as f:
        json.dump(eval_result, f, ensure_ascii=False, indent=2, default=str)
    print(f"评估结果已保存: {eval_path}")


if __name__ == "__main__":
    main()
