# -*- coding: utf-8 -*-
"""
RIB 批量运行脚本 - 在 Trae Exec 环境中执行选股扫描

用法：
  python run_rib_scan.py              # 使用 mock 数据
  python run_rib_scan.py --live       # 尝试真实数据
"""
import os
import sys

# 确保路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rib.config import RIB_CONFIG
from rib.cache import UDCache
from rib.data_feed import DataFeed
from rib.engine import RIBEngine
from rib.report import generate_report, generate_summary, save_report
from rib._mcp_client import set_mock_mode


def main():
    print("=" * 60)
    print("RIB 选股引擎 REVERSAL-IMPULSE-BASE-100 V1.0")
    print("=" * 60)
    print()

    # 使用 mock 模式（演示）
    set_mock_mode(True)

    engine = RIBEngine()
    cache = UDCache()
    feed = DataFeed(cache)

    # 示例股票列表
    test_stocks = [
        {"ts_code": "600519.SH", "name": "贵州茅台", "industry": "白酒"},
        {"ts_code": "000858.SZ", "name": "五粮液", "industry": "白酒"},
        {"ts_code": "601318.SH", "name": "中国平安", "industry": "保险"},
        {"ts_code": "600036.SH", "name": "招商银行", "industry": "银行"},
        {"ts_code": "002594.SZ", "name": "比亚迪", "industry": "新能源车"},
        {"ts_code": "300750.SZ", "name": "宁德时代", "industry": "电池"},
        {"ts_code": "601012.SH", "name": "隆基绿能", "industry": "光伏"},
        {"ts_code": "002475.SZ", "name": "立讯精密", "industry": "电子"},
        {"ts_code": "600030.SH", "name": "中信证券", "industry": "证券"},
        {"ts_code": "000651.SZ", "name": "格力电器", "industry": "家电"},
    ]

    print(f"测试股票数: {len(test_stocks)}")
    print("-" * 60)

    results = []
    for stock in test_stocks:
        ts_code = stock["ts_code"]
        name = stock["name"]
        industry = stock["industry"]

        print(f"分析 {ts_code} {name}...", end=" ")

        try:
            df = feed.get_daily_kline(ts_code, count=280, use_cache=True)

            if len(df) < 130:
                print(f"K线不足({len(df)}根)")
                continue

            result = engine.analyze(df, ts_code=ts_code, name=name, industry=industry)
            results.append(result)

            fs = result.final_score
            score_str = f"SCORE={fs.total:.0f}" if fs else "N/A"
            print(f"{result.state} {score_str}")

        except Exception as e:
            print(f"异常: {e}")
            import traceback
            traceback.print_exc()

    # 输出摘要
    print("\n" + "=" * 60)
    print("分析完成")
    print("=" * 60)

    primary = [r for r in results if r.state == "PRIMARY_BUY"]
    high_score = [r for r in results if r.final_score and r.final_score.total >= 70]

    print(f"总分析: {len(results)} 只")
    print(f"PRIMARY BUY: {len(primary)} 只")
    print(f"70分以上: {len(high_score)} 只")
    print()

    # 显示候选
    for r in sorted(high_score, key=lambda x: x.final_score.total if x.final_score else 0, reverse=True):
        fs = r.final_score
        print(f"  {r.ts_code} {r.name:8s} | {r.state:25s} | SCORE={fs.total:5.1f} ({fs.grade:15s}) | RR={r.risk_reward:.1f}")

    print()

    # 生成报告
    summary = generate_summary(results)
    output_dir = RIB_CONFIG.get("output", {}).get("dir", "output/rib")
    os.makedirs(output_dir, exist_ok=True)

    summary_path = os.path.join(output_dir, "rib_demo_summary.md")
    save_report(summary, summary_path)
    print(f"摘要报告: {summary_path}")

    # 为 PRIMARY BUY 候选生成详细报告
    for r in primary:
        detail = generate_report(r)
        detail_path = os.path.join(output_dir, f"rib_{r.ts_code}_detail.md")
        save_report(detail, detail_path)
        print(f"详细报告: {detail_path}")

    print("\n完成！")


if __name__ == "__main__":
    main()
