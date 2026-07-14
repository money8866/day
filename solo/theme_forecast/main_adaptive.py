# -*- coding: utf-8 -*-
"""
主题涨跌概率预测系统 - 状态自适应版（实盘入口）

核心改进 vs main.py：
1. 集成市场状态识别（6种状态：抱团上涨/下跌/震荡/轮动/普跌/普涨）
2. 集成3个时序因子（RS斜率/资金集中度/领先滞后）
3. 因子权重随状态切换
4. 抱团下跌期对反向因子取100-score
5. 轮动市按主题类型分化权重（动量类/反转类/中性类）

用法:
    python -m theme_forecast.main_adaptive                  # 全部主题
    python -m theme_forecast.main_adaptive --top 10         # 只看概率最高的10个
    python -m theme_forecast.main_adaptive --theme 光通信    # 只看指定主题
"""
import sys
import os
import argparse
import time
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd
import numpy as np

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from theme_forecast import data_loader as dl
from theme_forecast.factors import momentum, synergy, sentiment, flow
from theme_forecast.factors import timeseries as ts_factors
from theme_forecast.predictor import fuse_probability, format_report, save_report, load_prob_lookup
from theme_forecast.regime_detector import detect_regime, calc_all_theme_rs, format_regime_report
from theme_forecast.adaptive_predictor import (
    fuse_probability_adaptive, format_adaptive_report, load_theme_class_map,
)

# 复用main.py的ETF映射
from theme_forecast.main import ETF_THEME_MAP


def run_adaptive_prediction(all_theme_stocks: dict, trade_date: str,
                              market_index=None, moneyflow=None, limit_list=None,
                              limit_step=None, daily_basic=None, north_hold=None,
                              prob_lookup=None, theme_class_map=None,
                              min_stocks: int = 3) -> list:
    """
    运行状态自适应预测

    流程：
    1. 加载所有主题K线
    2. 计算所有主题RS → 构建rs_history
    3. 识别市场状态（regime）
    4. 逐主题计算因子（动量+协同+情绪+资金流+时序）
    5. 调用fuse_probability_adaptive
    """
    if prob_lookup is None:
        prob_lookup = load_prob_lookup()

    # 加载K线的时间范围（90自然日≈40交易日）
    end = trade_date
    start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=120)).strftime("%Y%m%d")

    # === 步骤1: 加载所有主题K线 ===
    print(f"  加载K线数据({start}~{end})...")
    all_codes = set()
    for stocks in all_theme_stocks.values():
        for s in stocks:
            all_codes.add(s["code"])
    all_codes = list(all_codes)
    all_klines = dl.load_klines(all_codes, start, end)
    print(f"  已加载 {len(all_klines)}/{len(all_codes)} 只股票K线")

    # === 步骤2: 构建每日主题K线快照 + RS历史 ===
    print(f"  构建RS历史...")
    trade_dates = sorted(market_index["trade_date"].unique())
    # 只取最近30个交易日构建rs_history
    recent_dates = trade_dates[-30:] if len(trade_dates) >= 30 else trade_dates

    rs_history = []
    all_theme_klines_by_date = {}  # {date: {theme: {code: df}}}

    for t_date in recent_dates:
        # 当日截面的主题K线
        theme_klines_today = {}
        for theme_name, stocks in all_theme_stocks.items():
            codes = [s["code"] for s in stocks]
            t_klines = {}
            for code in codes:
                if code not in all_klines:
                    continue
                df = all_klines[code]
                # 截止到t_date的数据
                t_pos = df.index[df["trade_date"] <= t_date]
                if len(t_pos) == 0:
                    continue
                t_pos_stock = t_pos[-1]
                if t_pos_stock < 25:
                    continue
                t_klines[code] = df.iloc[:t_pos_stock + 1].copy()
            if len(t_klines) >= min_stocks:
                theme_klines_today[theme_name] = t_klines

        if len(theme_klines_today) < 5:
            continue

        all_theme_klines_by_date[t_date] = theme_klines_today

        # 计算当日RS
        mkt_up_to_t = market_index[market_index["trade_date"] <= t_date]
        rs_dict = calc_all_theme_rs(theme_klines_today, mkt_up_to_t)
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

    if len(rs_history) < 5:
        print(f"  [警告] RS历史不足5天，无法识别市场状态")
        regime_info = {"regime": "轮动", "concentration": 0, "dispersion": 0,
                       "concentration_trend": 0, "top_themes": [], "bottom_themes": [],
                       "duration": 0, "confidence": "low", "top5_rs_mean": 1.0,
                       "all_rs_mean": 1.0, "all_rs_std": 0, "below_09_ratio": 0, "above_10_ratio": 0}
    else:
        # === 步骤3: 识别市场状态 ===
        regime_info = detect_regime(rs_history)

    regime = regime_info["regime"]
    print(f"  市场状态: {regime} (置信度: {regime_info['confidence']})")
    print(f"  抱团度: {regime_info['concentration']} | 持续: {regime_info['duration']}天")
    print(f"  Top5 RS均值: {regime_info['top5_rs_mean']} | 全市场RS均值: {regime_info['all_rs_mean']}")
    if regime_info["top_themes"]:
        print(f"  强势主题: {', '.join(regime_info['top_themes'][:3])}")

    # === 步骤4: 逐主题计算因子 + 预测 ===
    print(f"  逐主题计算因子...")

    # 用最后一天的theme_klines
    last_date = recent_dates[-1]
    all_theme_klines_today = all_theme_klines_by_date.get(last_date, {})
    mkt_up_to_today = market_index[market_index["trade_date"] <= last_date]

    all_results = []
    total = len(all_theme_stocks)

    for idx, (theme_name, stocks) in enumerate(all_theme_stocks.items(), 1):
        theme_klines = all_theme_klines_today.get(theme_name, {})
        if len(theme_klines) < min_stocks:
            continue

        try:
            # 计算因子
            theme_index = momentum.calc_theme_index(theme_klines)
            if theme_index.empty:
                continue

            mom = momentum.calc_all_momentum(theme_klines, mkt_up_to_today)
            syn = synergy.calc_all_synergy(theme_klines)
            sent = sentiment.calc_all_sentiment(
                list(theme_klines.keys()), limit_list, limit_step, daily_basic, trade_date, theme_klines
            )

            # 资金流：找对应的ETF
            etf_code = None
            for ec, tn in ETF_THEME_MAP.items():
                if tn == theme_name or theme_name in tn or tn in theme_name:
                    etf_code = ec
                    break
            etf_share_df = None
            etf_daily_df = None
            if etf_code:
                etf_share_df = dl.load_etf_share(etf_code)
                etf_daily_df = dl.load_etf_daily(etf_code)
            fl = flow.calc_all_flow(etf_share_df, etf_daily_df, list(theme_klines.keys()), moneyflow, north_hold, theme_klines)

            # 时序因子
            tsf = ts_factors.calc_all_timeseries_factors(
                theme_klines, theme_index, mkt_up_to_today, all_theme_klines_today
            )

            # 合并所有因子
            all_factors = {}
            all_factors.update(mom)
            all_factors.update(syn)
            all_factors.update(sent)
            all_factors.update(fl)
            all_factors.update(tsf)
            all_factors.pop("theme_index_close", None)

            # 状态自适应预测
            pred = fuse_probability_adaptive(
                all_factors, regime_info, prob_lookup,
                theme_name=theme_name,
                theme_class_map=theme_class_map,
            )
            pred["theme_name"] = theme_name
            pred["stock_count"] = len(theme_klines)
            pred["trade_date"] = trade_date
            pred["etf_code"] = etf_code

            all_results.append(pred)

            if (idx) % 10 == 0 or idx == total:
                print(f"    [{idx}/{total}] {theme_name}: {pred['probability']}% {pred['direction']}")

        except Exception as e:
            print(f"    [{idx}/{total}] {theme_name} 失败: {e}")

    return all_results, regime_info


def main():
    parser = argparse.ArgumentParser(description="主题涨跌概率预测（状态自适应版）")
    parser.add_argument("--top", type=int, default=0, help="只看概率最高的N个主题")
    parser.add_argument("--theme", type=str, default="", help="只看指定主题")
    parser.add_argument("--min-stocks", type=int, default=5, help="最少成份股数")
    parser.add_argument("--output", type=str, default="", help="输出JSON路径")
    args = parser.parse_args()

    start_time = time.time()

    # 1. 加载主题映射
    print("[1/6] 加载主题成份股映射...")
    themes = dl.load_theme_stocks()
    print(f"  共 {len(themes)} 个主题")

    # 2. 加载主题分类表
    print("[2/6] 加载主题分类表（动量类/反转类/中性类）...")
    theme_class_map = load_theme_class_map()
    if theme_class_map:
        from collections import Counter
        cls_count = Counter(theme_class_map.values())
        print(f"  共 {len(theme_class_map)} 个主题: {dict(cls_count)}")
    else:
        print(f"  [警告] 未找到主题分类表，轮动市将全部使用中性类权重")

    # 3. 获取交易日
    trade_date = dl.get_last_trade_date()
    print(f"[3/6] 交易日: {trade_date}")

    # 4. 加载全市场截面数据
    print("[4/6] 加载全市场截面数据...")
    market_index = dl.load_index_daily("000001.SH", n_days=120)
    print(f"  大盘指数: {len(market_index)} 条")

    moneyflow = dl.load_daily_moneyflow(trade_date)
    print(f"  资金流: {len(moneyflow) if moneyflow is not None else 0} 条")

    limit_list = dl.load_limit_list(trade_date)
    print(f"  涨停: {len(limit_list) if limit_list is not None else 0} 条")

    limit_step = dl.load_limit_step(trade_date)
    print(f"  炸板: {len(limit_step) if limit_step is not None else 0} 条")

    daily_basic = dl.load_daily_basic(trade_date)
    print(f"  daily_basic: {len(daily_basic) if daily_basic is not None else 0} 条")

    north_hold = dl.load_north_hold(trade_date)
    print(f"  北向: {len(north_hold) if north_hold is not None else 0} 条")

    # 5. 运行自适应预测
    print(f"[5/6] 状态自适应预测...")

    target_themes = {}
    for name, stocks in themes.items():
        if args.theme and args.theme not in name:
            continue
        if len(stocks) < args.min_stocks:
            continue
        target_themes[name] = stocks

    print(f"  待计算: {len(target_themes)} 个主题")

    all_results, regime_info = run_adaptive_prediction(
        target_themes, trade_date, market_index,
        moneyflow, limit_list, limit_step, daily_basic, north_hold,
        theme_class_map=theme_class_map,
    )

    # 6. 排序输出
    print(f"[6/6] 排序输出...")

    def sort_key(x):
        fp = x.get("future_probs", {}).get("5d", {})
        return -fp.get("prob", x.get("probability", 50))
    all_results.sort(key=sort_key)

    if args.top > 0:
        all_results = all_results[:args.top]

    elapsed = time.time() - start_time
    print(f"\n耗时: {elapsed:.1f}s")

    # 打印市场状态报告
    print(f"\n{'='*70}")
    print(f"  市场状态报告")
    print(f"{'='*70}")
    print(format_regime_report(regime_info))

    # 汇总表
    print(f"\n{'='*70}")
    print(f"  主题预测汇总（按未来5日上涨概率降序）")
    print(f"{'='*70}")
    print(f"{'#':>3} {'主题':<22} {'类型':<6} {'当前':>5} {'5日':>5} {'10日':>5} {'预期':>7} {'置信':<4}")
    print("-" * 70)
    for i, r in enumerate(all_results, 1):
        fp = r.get("future_probs", {})
        p5 = fp.get("5d", {}).get("prob", 0)
        p10 = fp.get("10d", {}).get("prob", 0)
        ret5 = fp.get("5d", {}).get("avg_ret", 0)
        conf = fp.get("5d", {}).get("confidence", "")
        conf_label = {"high": "高", "medium": "中", "low": "低"}.get(conf, "")
        tc = r.get("theme_class", "") or ""
        print(f"{i:>3} {r['theme_name']:<22} {tc:<6} {r['probability']:>4}% {p5:>4}% {p10:>4}% {ret5:>+6.2f}% {conf_label:<4}")

    # 详细报告
    print(f"\n{'='*70}")
    print(f"  详细报告（Top 5）")
    print(f"{'='*70}")
    for result in all_results[:5]:
        report = format_adaptive_report(result["theme_name"], result, result["stock_count"])
        print(report)
        print()

    # 保存JSON
    output_path = args.output or str(
        PROJECT_ROOT / "theme_forecast" / "output" / f"theme_forecast_adaptive_{trade_date}.json"
    )
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    save_report(all_results, output_path)
    print(f"报告已保存: {output_path}")


if __name__ == "__main__":
    main()
