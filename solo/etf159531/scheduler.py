"""每日 18:30（Asia/Shanghai）定时调度。

- 每个工作日触发，内部先按 trade_cal 判断是否交易日；非交易日直接跳过。
- 单实例运行、错过触发允许 1 小时内补跑。
- 任一次执行异常只记录日志，不终止调度进程。
"""
from __future__ import annotations

import logging
import sys

from settings import get_logger, load_config, setup_logging


def start(cfg: dict) -> None:
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("未安装 APScheduler，请先 pip install -r requirements.txt") from exc

    logger = setup_logging(cfg)
    sc = cfg.get("scheduler", {})
    hour = int(sc.get("hour", 18))
    minute = int(sc.get("minute", 30))
    tz = cfg.get("project", {}).get("timezone", "Asia/Shanghai")

    def job() -> None:
        # 延迟导入，避免循环依赖
        import main as app

        logger.info("定时任务触发：开始每日决策")
        try:
            code, _ = app.run_daily(load_config(), date=None, allow_fetch=True)
            logger.info("定时任务结束，退出码=%s", code)
        except Exception:  # noqa: BLE001
            logger.exception("定时任务执行失败（调度器继续运行）")

    sched = BlockingScheduler(timezone=tz)
    sched.add_job(
        job,
        CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone=tz),
        id="etf159531_daily",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    logger.info("调度器已启动：每个工作日 %02d:%02d（%s）运行；Ctrl+C 退出", hour, minute, tz)
    print(f"调度器已启动：每周一至周五 {hour:02d}:{minute:02d}（{tz}），Ctrl+C 退出")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("调度器已停止")
        print("\n调度器已停止")


if __name__ == "__main__":
    setup_logging(load_config())
    start(load_config())
    sys.exit(0)
