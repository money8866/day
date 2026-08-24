# -*- coding: utf-8 -*-
"""
RIB 主入口 - A股长期下跌反转后二波启动选股引擎

使用方法：
  python main_rib.py                    # 使用缓存数据运行
  python main_rib.py --backtest         # 运行回测
  python main_rib.py --code 600519.SH   # 分析单只股票
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

# 确保路径正确
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rib.config import RIB_CONFIG
from rib.cache import UDCache
from rib.data_feed import DataFeed
from rib.engine import RIBEngine, RIBResult
from rib.report import generate_report, generate_summary, save_report
from rib.filters import MarketSnapshot


def load_stock_pool(csv_path: str = "") -> List[Dict]:
    """加载股票池。"""
    if csv_path and os.path.exists(csv_path):
        df = pd.read_csv(csv_path, encoding="utf-8")
        return df.to_dict("records")

    # 默认使用本地缓存
    cache = UDCache()
    cached = cache.get("stock_basic", "cache_key", "stock_list")
    if cached and isinstance(cached, list):
        return cached

    # 示例股票池（实际运行时应从数据库或API获取）
    default_pool = [
        {"ts_code": "600519.SH", "name": "贵州茅台", "industry": "白酒"},
        {"ts_code": "000858.SZ", "name": "五粮液", "industry": "白酒"},
        {"ts_code": "601318.SH", "name": "中国平安", "industry": "保险"},
        {"ts_code": "600036.SH", "name": "招商银行", "industry": "银行"},
        {"ts_code": "000001.SZ", "name": "平安银行", "industry": "银行"},
        {"ts_code": "600887.SH", "name": "伊利股份", "industry": "乳制品"},
        {"ts_code": "000333.SZ", "name": "美的集团", "industry": "家电"},
        {"ts_code": "600276.SH", "name": "恒瑞医药", "industry": "医药"},
        {"ts_code": "002594.SZ", "name": "比亚迪", "industry": "新能源车"},
        {"ts_code": "300750.SZ", "name": "宁德时代", "industry": "电池"},
        {"ts_code": "601012.SH", "name": "隆基绿能", "industry": "光伏"},
        {"ts_code": "002475.SZ", "name": "立讯精密", "industry": "电子"},
        {"ts_code": "600030.SH", "name": "中信证券", "industry": "证券"},
        {"ts_code": "601166.SH", "name": "兴业银行", "industry": "银行"},
        {"ts_code": "000651.SZ", "name": "格力电器", "industry": "家电"},
    ]
    return default_pool


def scan_stocks(
    stock_codes: Optional[List[str]] = None,
    pool_path: str = "",
    use_cache: bool = True,
    limit: int = 0,
) -> List[RIBResult]:
    """扫描股票池。

    Args:
        stock_codes: 指定股票代码列表（可选）
        pool_path: 股票池CSV路径
        use_cache: 是否使用缓存
        limit: 处理数量限制

    Returns:
        分析结果列表
    """
    print("=" * 60)
    print("RIB 选股引擎 REVERSAL-IMPULSE-BASE-100 V1.0")
    print("=" * 60)
    print()

    # 初始化组件
    engine = RIBEngine()
    cache = UDCache()
    feed = DataFeed(cache)

    # 加载股票池
    if stock_codes:
        pool = [{"ts_code": code, "name": "", "industry": ""} for code in stock_codes]
    else:
        pool = load_stock_pool(pool_path)

    if limit > 0:
        pool = pool[:limit]

    print(f"待分析股票数: {len(pool)}")
    print("-" * 60)

    # 加载市场数据
    market_snapshot = MarketSnapshot(regime="normal")
    try:
        index_df = feed.get_index_data("000001.SH", days=120)
        if len(index_df) > 0:
            closes = index_df["close"].values.astype(float)
            market_snapshot.index_price = closes[-1]
            market_snapshot.index_change_pct = (closes[-1] - closes[-20]) / closes[-20] * 100
            ma20_idx = max(0, len(closes) - 20)
            ma60_idx = max(0, len(closes) - 60)
            market_snapshot.index_ma20_slope = (closes[-1] - np.mean(closes[ma20_idx:])) / np.mean(closes[ma20_idx:])
            market_snapshot.index_ma60_slope = (closes[-1] - np.mean(closes[ma60_idx:])) / np.mean(closes[ma60_idx:])
            market_snapshot.regime = engine.market_filter.evaluate(market_snapshot).regime
            print(f"上证指数: {closes[-1]:.2f} ({market_snapshot.index_change_pct:+.2f}%)")
            print(f"市场环境: {market_snapshot.regime}")
    except Exception as e:
        print(f"市场数据加载失败: {e}")
    print()

    # 分析每只股票
    results = []
    for i, stock in enumerate(pool):
        ts_code = stock.get("ts_code", "")
        name = stock.get("name", "")
        industry = stock.get("industry", "")

        if not ts_code:
            continue

        print(f"[{i+1}/{len(pool)}] 分析 {ts_code} {name}...", end=" ")

        try:
            # 获取K线数据
            df = feed.get_daily_kline(ts_code, count=280, use_cache=use_cache)

            if len(df) < 130:
                print(f"K线不足({len(df)}根)")
                continue

            # 执行分析
            result = engine.analyze(
                df,
                ts_code=ts_code,
                name=name,
                industry=industry,
                market_snapshot=market_snapshot,
            )
            results.append(result)

            # 输出关键信息
            if result.state == "PRIMARY_BUY":
                fs = result.final_score
                score_str = f"SCORE={fs.total:.0f}" if fs else "N/A"
                print(f"★ PRIMARY BUY {score_str}")
            elif result.state == "INVALIDATED" and result.veto_triggered:
                print(f"否决: {result.veto_triggered[0][:40]}...")
            elif not result.is_valid:
                print(f"失败: {result.state}")
            else:
                fs = result.final_score
                score_str = f"SCORE={fs.total:.0f}" if fs else "N/A"
                print(f"{result.state} {score_str}")

        except Exception as e:
            print(f"异常: {e}")
            continue

    print()
    print("-" * 60)
    print(f"分析完成: {len(results)} 只股票")

    # 统计
    primary_buys = [r for r in results if r.state == "PRIMARY_BUY"]
    print(f"PRIMARY BUY 候选: {len(primary_buys)} 只")

    if primary_buys:
        print()
        print("★" * 30)
        print("PRIMARY BUY 候选列表：")
        print("★" * 30)
        for r in sorted(primary_buys, key=lambda x: x.final_score.total if x.final_score else 0, reverse=True):
            fs = r.final_score
            tp = r.trade_plan
            print(f"  {r.ts_code} {r.name}")
            print(f"    评分: {fs.total:.0f} ({fs.grade})")
            print(f"    现价: {r.close:.2f}  盈亏比: {r.risk_reward:.1f}")
            if tp:
                print(f"    买入区: {tp.zone_low:.2f}~{tp.zone_high:.2f}  止损: {tp.stop_loss:.2f}")
            print()

    return results


def run_single_analysis(ts_code: str, use_cache: bool = True):
    """分析单只股票并输出完整报告。"""
    engine = RIBEngine()
    cache = UDCache()
    feed = DataFeed(cache)

    print(f"正在分析 {ts_code}...")
    df = feed.get_daily_kline(ts_code, count=280, use_cache=use_cache)

    if len(df) < 130:
        print(f"K线不足 {len(df)} 根，无法分析")
        return

    result = engine.analyze(df, ts_code=ts_code)

    # 生成报告
    report = generate_report(result)
    print(report)

    # 保存报告
    output_dir = RIB_CONFIG.get("output", {}).get("dir", "output/rib")
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, f"rib_{ts_code}_{result.date}.md")
    save_report(report, filepath)
    print(f"\n报告已保存: {filepath}")


def run_backtest():
    """运行回测。"""
    from rib.backtest import RIBBacktest

    print("RIB 回测引擎启动")
    print("-" * 60)

    # 加载测试数据
    pool = load_stock_pool()
    cache = UDCache()
    feed = DataFeed(cache)

    stock_data = {}
    for stock in pool[:50]:  # 回测使用前50只
        ts_code = stock.get("ts_code", "")
        if ts_code:
            df = feed.get_daily_kline(ts_code, count=500, use_cache=True)
            if len(df) >= 130:
                stock_data[ts_code] = df

    print(f"加载股票数据: {len(stock_data)} 只")

    # 运行回测
    bt = RIBBacktest()
    metrics = bt.run(stock_data, holding_days=5)

    # 输出结果
    print("\n" + "=" * 60)
    print("回测结果")
    print("=" * 60)
    print(f"总信号数: {metrics.total_signals}")
    print(f"PRIMARY BUY数: {metrics.primary_buy_count}")
    print(f"胜率: {metrics.win_rate*100:.1f}%")
    print(f"平均收益: {metrics.avg_return*100:.2f}%")
    print(f"3日收益: {metrics.avg_return_3d*100:.2f}%")
    print(f"5日收益: {metrics.avg_return_5d*100:.2f}%")
    print(f"最大收益: {metrics.max_return*100:.2f}%")
    print(f"最大亏损: {metrics.max_loss*100:.2f}%")
    print(f"Profit Factor: {metrics.profit_factor:.2f}")
    print(f"平均持仓: {metrics.avg_holding_days:.1f}日")

    # 策略对比
    print("\n策略对比：")
    compare = bt.compare_strategies()
    for key, val in compare.items():
        print(f"  {val['description']}: 胜率{val['win_rate']*100:.1f}% 平均收益{val['avg_return']*100:.2f}%")

    # 保存结果
    output_dir = RIB_CONFIG.get("output", {}).get("dir", "output/rib")
    os.makedirs(output_dir, exist_ok=True)
    bt.save_results(output_dir)
    print(f"\n结果已保存至: {output_dir}")


def main():
    parser = argparse.ArgumentParser(description="RIB 选股引擎 V1.0")
    parser.add_argument("--code", type=str, help="分析单只股票代码，如 600519.SH")
    parser.add_argument("--backtest", action="store_true", help="运行回测")
    parser.add_argument("--pool", type=str, help="股票池CSV路径")
    parser.add_argument("--limit", type=int, default=0, help="处理数量限制")
    parser.add_argument("--no-cache", action="store_true", help="不使用缓存")
    parser.add_argument("--export", type=str, help="导出报告路径")

    args = parser.parse_args()

    if args.backtest:
        run_backtest()
    elif args.code:
        run_single_analysis(args.code, use_cache=not args.no_cache)
    else:
        # 扫描全市场
        results = scan_stocks(
            pool_path=args.pool,
            use_cache=not args.no_cache,
            limit=args.limit,
        )

        # 生成并保存摘要
        summary = generate_summary(results)
        output_dir = RIB_CONFIG.get("output", {}).get("dir", "output/rib")
        os.makedirs(output_dir, exist_ok=True)
        date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        summary_path = os.path.join(output_dir, f"rib_summary_{date_str}.md")
        save_report(summary, summary_path)
        print(f"\n摘要报告已保存: {summary_path}")

        # 保存完整结果
        results_path = os.path.join(output_dir, f"rib_results_{date_str}.json")
        save_data = []
        for r in results:
            item = {
                "ts_code": r.ts_code,
                "name": r.name,
                "state": r.state,
                "close": r.close,
                "final_score": r.final_score.total if r.final_score else 0,
                "grade": r.final_score.grade if r.final_score else "",
                "is_primary_buy": r.final_score.is_primary_buy if r.final_score else False,
                "risk_reward": r.risk_reward,
                "market_regime": r.market_regime,
            }
            save_data.append(item)

        with open(results_path, "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
        print(f"详细结果已保存: {results_path}")


if __name__ == "__main__":
    main()
