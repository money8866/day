"""TERE V1/V3 入口.

用法:
    python -m theme_engine.main --date 20260724          # V1 默认
    python -m theme_engine.main --date 20260724 --v3     # V3 模式
    python -m theme_engine.main --date 20260724 --v3 --single AI_COMPUTE
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from theme_engine.api.engine import TERE


def setup_logging(verbose: bool = False) -> None:
    """配置日志."""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(
        level=level,
        format=fmt,
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数."""
    parser = argparse.ArgumentParser(
        description="TERE V1/V3 - Theme & ETF Resonance Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m theme_engine.main --date 20260724              # V1 默认
  python -m theme_engine.main --date 20260724 --v3         # V3 模式
  python -m theme_engine.main --date 20260724 --v3 --dry-run --single AI_COMPUTE
        """,
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="交易日 YYYYMMDD (默认: 当前日期)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅计算不保存到数据库",
    )
    parser.add_argument(
        "--single",
        type=str,
        default=None,
        help="仅计算单个主题代码 (如 AI_COMPUTE)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="输出详细调试日志",
    )
    parser.add_argument(
        "--v3",
        action="store_true",
        help="使用 V3 机构动态轮动评分",
    )
    parser.add_argument(
        "--skip-etf",
        action="store_true",
        help="跳过 ETF 强度计算",
    )
    parser.add_argument(
        "--skip-breadth",
        action="store_true",
        help="跳过扩散度计算",
    )
    parser.add_argument(
        "--skip-leader",
        action="store_true",
        help="跳过龙头强度计算",
    )
    parser.add_argument(
        "--skip-purity",
        action="store_true",
        help="跳过纯度计算",
    )
    parser.add_argument(
        "--skip-resonance",
        action="store_true",
        help="跳过共振计算",
    )
    parser.add_argument(
        "--skip-flow",
        action="store_true",
        help="跳过资金流计算",
    )
    parser.add_argument(
        "--skip-stage",
        action="store_true",
        help="跳过生命周期判定",
    )
    parser.add_argument(
        "--skip-signal",
        action="store_true",
        help="跳过信号生成",
    )
    parser.add_argument(
        "--skip-rotation",
        action="store_true",
        help="跳过轮动预测",
    )
    parser.add_argument(
        "--export",
        type=str,
        default=None,
        help="导出排行榜到指定 CSV 路径",
    )
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> None:
    """主入口."""
    args = parse_args(argv)
    setup_logging(args.verbose)

    logger = logging.getLogger(__name__)

    if args.v3:
        logger.info("TERE V3 启动 (机构动态轮动评分)")
        await _run_v3(args)
    else:
        logger.info("TERE V1 启动")
        await _run_v1(args)


async def _run_v1(args: argparse.Namespace) -> None:
    """运行 V1 引擎."""
    engine = TERE()
    try:
        result = await engine.run(
            trade_date=args.date,
            dry_run=args.dry_run,
            single=args.single,
            skip_etf=args.skip_etf,
            skip_breadth=args.skip_breadth,
            skip_leader=args.skip_leader,
            skip_purity=args.skip_purity,
            skip_resonance=args.skip_resonance,
            skip_flow=args.skip_flow,
            skip_stage=args.skip_stage,
            skip_signal=args.skip_signal,
            skip_rotation=args.skip_rotation,
        )

        if result.error:
            logger.error("引擎运行出错: %s", result.error)
            print(f"\n错误: {result.error}", file=sys.stderr)

        print(f"\n{'='*60}")
        print(f"  TERE V1 排行榜 - {result.trade_date}")
        print(f"{'='*60}")
        print(f"{'排名':>4} {'主题名称':<20} {'总分':>7} {'ETF':>7} {'扩散':>7} {'龙头':>7} {'信号':<12}")
        print(f"{'-'*60}")

        for theme in result.ranking[:20]:
            print(
                f"{theme.rank:>4} "
                f"{theme.theme_name:<20} "
                f"{theme.total_score:>6.1f} "
                f"{theme.etf_strength:>6.1f} "
                f"{theme.breadth_score:>6.1f} "
                f"{theme.leader_strength:>6.1f} "
                f"{theme.signal:<12}"
            )

        if len(result.ranking) > 20:
            print(f"  ... 共 {len(result.ranking)} 个主题")

        if args.export:
            out_path = await engine.export_ranking(
                trade_date=result.trade_date,
                output_path=args.export,
            )
            print(f"\n已导出到: {out_path}")

    except KeyboardInterrupt:
        logger.warning("用户中断")
        print("\n\n用户中断")
    except Exception as e:
        logger.exception("运行异常")
        print(f"\n运行失败: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        await engine.cleanup()


async def _run_v3(args: argparse.Namespace) -> None:
    """运行 V3 引擎."""
    from theme_engine.score_v3.engine import V3Engine
    from theme_engine.score_v3.report import render_full_report

    engine = V3Engine()
    try:
        # 将 V1 skip 参数转为 V3 skip_factors
        skip_factors = []
        if args.skip_etf:
            skip_factors.extend(["etf_trend", "etf_accel"])
        if args.skip_breadth:
            skip_factors.append("breadth")
        if args.skip_leader:
            skip_factors.append("leader")
        if args.skip_flow:
            skip_factors.append("money")
        if args.skip_resonance:
            skip_factors.extend(["resonance", "lifecycle"])

        result = await engine.run(
            trade_date=args.date,
            dry_run=args.dry_run,
            single=args.single,
            skip_factors=skip_factors,
        )

        if result.error:
            logger.error("V3引擎运行出错: %s", result.error)
            print(f"\n错误: {result.error}", file=sys.stderr)

        # 输出完整报告
        report = render_full_report(result)
        print()
        print(report)

    except KeyboardInterrupt:
        logger.warning("用户中断")
        print("\n\n用户中断")
    except Exception as e:
        logger.exception("V3运行异常")
        print(f"\nV3运行失败: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        await engine.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
