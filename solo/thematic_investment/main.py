"""
主题投资系统 - 主入口
======================
演示 config 加载、数据库连接初始化、工具函数调用的完整流程。

运行:
    cd thematic_investment
    python main.py
"""

from __future__ import annotations

import os
import sys
import signal
import logging
from typing import Dict, Any

# ---------------------------------------------------------------------------
# 路径调整：允许模块独立运行 & IDE 直接执行
# ---------------------------------------------------------------------------
_CURRENT_DIR: str = os.path.dirname(os.path.abspath(__file__))
if _CURRENT_DIR not in sys.path:
    sys.path.insert(0, _CURRENT_DIR)

import pandas as pd  # noqa: F401

from modules.utils import setup_logger, TradeCalendar, timing, handle_exception
from modules.db_connector import (
    MongoConnector, PgConnector, MilvusConnector, close_all_connections,
    CONFIG,
)

# ---------------------------------------------------------------------------
# 全局 Logger
# ---------------------------------------------------------------------------
logger: logging.Logger = setup_logger(
    name="thematic_main",
    log_dir=os.path.join(_CURRENT_DIR, "logs"),
    log_file="thematic_investment.log",
    level=CONFIG["logging"]["level"],
    console_output=CONFIG["logging"].get("console_output", True),
)


# ---------------------------------------------------------------------------
# 优雅退出信号处理
# ---------------------------------------------------------------------------
def _signal_handler(sig: int, frame: Any) -> None:
    """捕获 Ctrl+C / SIGTERM，清理资源后退出。"""
    logger.info("收到退出信号 (%s)，正在清理资源 ...", sig)
    close_all_connections()
    sys.exit(0)


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


# ---------------------------------------------------------------------------
# 演示任务
# ---------------------------------------------------------------------------
@timing()
@handle_exception(default_return=None, reraise=False)
def demo_mongodb() -> None:
    """MongoDB 连接与写入读取演示。"""
    logger.info("=== MongoDB 演示 ===")
    try:
        with MongoConnector() as db:
            collection = db["demo_collection"]
            # 写入
            import time as _time
            insert_result = collection.insert_one({
                "type": "demo",
                "timestamp": _time.strftime("%Y-%m-%d %H:%M:%S"),
                "message": "Hello, Thematic Investment System!",
            })
            logger.info("写入文档 ID: %s", insert_result.inserted_id)

            # 读取最近一条
            latest = collection.find_one(
                {"type": "demo"},
                sort=[("_id", -1)],
            )
            logger.info("最新文档: %s", latest)
    except Exception as exc:
        logger.warning("MongoDB 不可用 (请检查服务是否启动): %s", exc)


@timing()
@handle_exception(default_return=None, reraise=False)
def demo_postgresql() -> None:
    """PostgreSQL 连接演示。"""
    logger.info("=== PostgreSQL 演示 ===")
    try:
        with PgConnector() as conn:
            result = conn.execute("SELECT CURRENT_TIMESTAMP AS now;")
            row = result.fetchone()
            logger.info("PostgreSQL 服务器时间: %s", row)
    except Exception as exc:
        logger.warning("PostgreSQL 不可用: %s", exc)


@timing()
@handle_exception(default_return=None, reraise=False)
def demo_milvus() -> None:
    """Milvus 连接演示 (仅检查连接，不执行读写)。"""
    logger.info("=== Milvus 演示 ===")
    try:
        with MilvusConnector() as mc:
            logger.info("Milvus 连接别名: %s", mc.alias)
            logger.info("Milvus collection 前缀: %s", mc.collection_prefix)
    except Exception as exc:
        logger.warning("Milvus 不可用: %s", exc)


@timing()
@handle_exception(default_return=None, reraise=False)
def demo_trade_calendar() -> None:
    """交易日历演示。"""
    logger.info("=== 交易日历演示 ===")
    from datetime import date
    import pandas as pd  # noqa: F401

    dates = TradeCalendar.get_trade_dates(
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 15),
        source="tushare",
    )
    logger.info("2026-06-01 ~ 2026-06-15 共 %d 个交易日", len(dates))
    if dates:
        logger.info("首个交易日: %s", dates[0])
        logger.info("最后交易日: %s", dates[-1])

    today_ = date.today()
    is_trade = TradeCalendar.is_trade_day(today_, source="tushare")
    logger.info("今天 %s %s交易日", today_, "是" if is_trade else "不是")

    try:
        next_ = TradeCalendar.next_trade_day(today_, source="tushare")
        prev_ = TradeCalendar.prev_trade_day(today_, source="tushare")
        logger.info("下一交易日: %s, 上一交易日: %s", next_, prev_)
    except RuntimeError as exc:
        logger.warning("无法获取相邻交易日: %s", exc)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def print_banner(cfg: Dict[str, Any]) -> None:
    logger.info("=" * 60)
    logger.info("  主题投资系统 - 初始化完成")
    logger.info("  数据源: %s", cfg["data_sources"]["primary"])
    logger.info("  回测区间: %s ~ %s",
                cfg["backtest"]["start_date"],
                cfg["backtest"]["end_date"])
    logger.info("  初始资金: %.2f 元", cfg["backtest"]["initial_capital"])
    logger.info("=" * 60)


def main() -> None:
    """主流程 - 依次演示各模块。"""
    print_banner(CONFIG)

    # 1. 数据库
    demo_mongodb()
    demo_postgresql()
    demo_milvus()

    # 2. 交易日历
    demo_trade_calendar()

    logger.info("所有演示任务执行完毕。")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("用户手动中断")
    except Exception as exc:
        logger.exception("程序异常退出: %s", exc)
    finally:
        close_all_connections()
        logger.info("资源清理完成，退出。")
