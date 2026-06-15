"""
回测运行脚本 (run_backtest.py)
===============================

使用方式:
  python run_backtest.py --start 2024-01-01 --end 2024-12-31 --cash 1000000

参数说明:
  --start: 开始日期 (YYYY-MM-DD)
  --end: 结束日期 (YYYY-MM-DD)
  --cash: 初始资金 (默认 100万)
  --stocks: 股票代码列表, 逗号分隔
  --optimize: 是否进行参数优化
"""

import argparse
import json
import sys
import os

_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if _CURRENT_DIR not in sys.path:
    sys.path.insert(0, _CURRENT_DIR)

from modules.backtest_engine import BacktestEngine, ParameterOptimizer
from modules.analyzer import generate_report, print_summary


def main():
    parser = argparse.ArgumentParser(description="主题轮动策略回测")
    parser.add_argument("--start", required=True, help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--cash", type=float, default=1000000.0, help="初始资金")
    parser.add_argument("--stocks", default="002594,300750,600519,002460",
                        help="股票代码列表, 逗号分隔")
    parser.add_argument("--optimize", action="store_true", help="是否参数优化")
    args = parser.parse_args()

    # 解析股票代码
    stock_codes = [s.strip() for s in args.stocks.split(",") if s.strip()]

    print(f"📊 回测参数:")
    print(f"   日期范围: {args.start} ~ {args.end}")
    print(f"   初始资金: ¥{args.cash:,.0f}")
    print(f"   股票池: {stock_codes}")
    print()

    # 创建回测引擎
    engine = BacktestEngine(args.start, args.end, args.cash)
    engine.add_stock_data(stock_codes)
    engine.add_strategy()
    engine.add_analyzers()

    # 运行回测
    print("🚀 开始回测...")
    engine.run()
    print("✅ 回测完成")
    print()

    # 获取结果
    results = engine.get_results()

    # 打印摘要
    print_summary(results)
    print()

    # 生成报告
    print("📝 生成回测报告...")
    report_path = generate_report(results, args.start, args.end)
    print(f"📄 报告已保存: {report_path}")
    print()

    # 参数优化（可选）
    if args.optimize:
        print("🔍 开始参数优化...")
        optimizer = ParameterOptimizer(engine)
        param_grid = {
            "rebalance_days": [1, 3, 5],
            "max_single_weight": [0.2, 0.25, 0.3],
        }
        opt_results = optimizer.optimize(param_grid, n_jobs=3)
        print(f"📊 参数优化完成, 共测试 {len(opt_results)} 组参数")
        for i, (params, res) in enumerate(opt_results[:3], 1):
            print(f"   {i}. 参数: {params} → 夏普比率: {res['sharpe_ratio']:.2f}")


if __name__ == "__main__":
    main()
