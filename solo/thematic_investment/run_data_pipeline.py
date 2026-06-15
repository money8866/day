"""
主题投资系统 - 数据采集管线启动脚本
====================================

使用方式:
    # 单次运行（抓取今日、去重处理、存储）
    python run_data_pipeline.py

    # 指定日期（用于回补历史数据）
    python run_data_pipeline.py --date 2026-06-10

    # 定时循环（例如每 30 分钟跑一次，作为守护进程）
    python run_data_pipeline.py --loop --interval 1800

    # 仅输出 JSON 统计（便于接入其他系统）
    python run_data_pipeline.py --json

依赖: 需先配置好 d:\\mystock\\config\\.env 中的 DeepSeek 与数据账号；
      MongoDB / Milvus 需处于可连接状态（任何一个不可用时会自动降级或跳过）。
"""

from __future__ import annotations

import os
import sys
import json
import time
import argparse
import logging
from typing import Optional, Dict, Any

# --------------------------------------------------------------------------- #
# 路径
# --------------------------------------------------------------------------- #
_CURRENT_DIR: str = os.path.dirname(os.path.abspath(__file__))
if _CURRENT_DIR not in sys.path:
    sys.path.insert(0, _CURRENT_DIR)

from modules.utils import setup_logger, today_str
from modules.data_collector import DataPipeline

logger: logging.Logger = setup_logger(
    name="run_data_pipeline",
    log_dir=os.path.join(_CURRENT_DIR, "logs"),
    log_file="run_data_pipeline.log",
)


# ============================================================================ #
# CLI
# ============================================================================ #
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="主题投资系统 - 文本抓取/清洗/LLM标注/向量嵌入 全流程",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="指定运行日期 (YYYY-MM-DD)，默认为今日",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="循环运行 (按 --interval 的间隔反复执行)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=1800,
        help="循环间隔秒数 (默认 1800s = 30min)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="仅输出 JSON 格式的统计结果 (便于其他程序解析)",
    )
    return parser.parse_args()


def run_once(target_date: Optional[str] = None, json_only: bool = False) -> Dict[str, Any]:
    """执行单次数据采集流程，返回统计 dict"""
    target_date = target_date or today_str()

    if not json_only:
        logger.info("=" * 60)
        logger.info("  开始执行数据采集流程 [target_date = %s]", target_date)
        logger.info("=" * 60)

    pipeline = DataPipeline()
    stats = pipeline.run(target_date=target_date)

    if json_only:
        # 仅打印 json 到 stdout，便于 grep / jq / 管道
        print(json.dumps({"date": target_date, "stats": stats}, ensure_ascii=False))
    else:
        logger.info("=" * 60)
        logger.info("  完成: %s", stats)
        logger.info("=" * 60)

    return stats


def run_loop(interval: int, target_date: Optional[str] = None) -> None:
    """按固定间隔循环执行，捕获 Ctrl+C 优雅退出"""
    logger.info("进入循环模式，每隔 %d 秒执行一次（Ctrl+C 退出）", interval)
    try:
        while True:
            try:
                run_once(target_date=target_date, json_only=False)
            except Exception as exc:
                logger.exception("单次循环异常: %s", exc)
            logger.info("休眠 %d 秒 ...", interval)
            time.sleep(interval)
    except KeyboardInterrupt:
        logger.info("收到中断信号，退出循环")


def main() -> None:
    args = parse_args()

    if args.loop and args.json:
        logger.warning("循环模式下 --json 输出会被日志覆盖，建议分开使用")

    if args.loop:
        run_loop(interval=args.interval, target_date=args.date)
    else:
        run_once(target_date=args.date, json_only=args.json)


if __name__ == "__main__":
    main()
