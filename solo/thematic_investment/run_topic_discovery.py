"""
主题发现管线启动脚本
======================

典型用法:
  # 1) 默认：回看 24h 内新增文本 → 聚类 → 命名 → 落库
  python run_topic_discovery.py

  # 2) 自定义回看窗口（例如每 6h 增量识别
  python run_topic_discovery.py --hours 6

  # 3) 输出 JSON（供其他系统对接）
  python run_topic_discovery.py --json

  # 4) 循环定时（每 12h = 43200s 跑一次）
  python run_topic_discovery.py --loop --interval 43200

依赖:
  pip install hdbscan scikit-learn numpy httpx pymongo pymilvus
  DeepSeek API key / MongoDB / Milvus 均已通过 config.yaml + .env 配置。
"""

from __future__ import annotations

import os
import sys
import json
import time
import argparse
import logging
from typing import Dict, Any

_CURRENT_DIR: str = os.path.dirname(os.path.abspath(__file__))
if _CURRENT_DIR not in sys.path:
    sys.path.insert(0, _CURRENT_DIR)

from modules.topic_discovery import TopicDiscoveryPipeline
from modules.utils import setup_logger

logger: logging.Logger = setup_logger(
    name="run_topic_discovery",
    log_dir=os.path.join(_CURRENT_DIR, "logs"),
    log_file="run_topic_discovery.log",
)


def run_once(hours: int, json_only: bool = False) -> Dict[str, Any]:
    logger.info("[run] 启动: 回看 %dh 窗口主题发现管线", hours)
    stats = TopicDiscoveryPipeline(hours=hours).run()
    if json_only:
        print(json.dumps(stats, ensure_ascii=False, default=str))
    return stats


def run_loop(hours: int, interval_seconds: int) -> None:
    logger.info("[run] 进入循环模式: 每 %ds 执行一次", interval_seconds)
    try:
        while True:
            try:
                TopicDiscoveryPipeline(hours=hours).run()
            except Exception as exc:
                logger.exception("单次主题发现异常: %s", exc)
            logger.info("休眠 %ds ...", interval_seconds)
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        logger.info("收到中断，退出")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="主题发现: A股二级主题自动识别 + 一级主题挂靠",
    )
    parser.add_argument("--hours", type=int, default=24,
                        help="回看时间窗口(小时), 默认 24")
    parser.add_argument("--loop", action="store_true",
                        help="循环执行")
    parser.add_argument("--interval", type=int, default=43200,
                        help="循环间隔秒数 (默认 12h = 43200)")
    parser.add_argument("--json", action="store_true",
                        help="以 JSON 输出统计结果")
    args = parser.parse_args()

    if args.loop:
        run_loop(hours=args.hours, interval_seconds=args.interval)
    else:
        run_once(hours=args.hours, json_only=args.json)


if __name__ == "__main__":
    main()
