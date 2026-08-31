# -*- coding: utf-8 -*-
"""SLI V1.0 —— A股细分行业龙头量化识别引擎。

用法：
  python -m sli.main --date 20260828
  python -m sli.main --date 20260828 --top 100
  python -m sli.main --industry "钛白粉"
  python -m sli.main --er20 d:/mystock/.../er20_scores.csv --simple
"""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="sli",
        description="A股细分行业龙头识别引擎 SLI V2.0 (Subsector Leader Index)",
    )
    ap.add_argument("--date", default="", help="目标交易日，如 20260828（默认最近交易日）")
    ap.add_argument("--top", type=int, default=None, help="排行榜输出前 N 名（如 100）")
    ap.add_argument("--industry", default="", help="只看某三级行业，如 钛白粉")
    ap.add_argument("--er20", default="", help="ER20 评分 CSV 路径（可选，用于 TradeAlpha）")
    ap.add_argument("--simple", action="store_true",
                    help="简化模式：跳过 T-20/T-60/T-120 生命周期面板")
    ap.add_argument("--v1", action="store_true",
                    help="回退到 V1 模式（不计算产品层/SLI_V2，仅七维 SLI）")
    args = ap.parse_args(argv)

    from .runner import SliRunner

    try:
        runner = SliRunner(date=args.date or None, simple=args.simple)
        result = runner.run(top=args.top,
                            industry=args.industry or None,
                            er20_csv=args.er20 or None,
                            v1=args.v1)
        print(f"\nSLI 完成：{result['date']}  "
              f"行业 {result['n_industries']} | 股票 {result['n_stocks']} | "
              f"龙头 {result['n_leaders']}")
        for k, v in result["paths"].items():
            print(f"  {k:<16} {v}")
        return 0
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"\nSLI 运行失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
