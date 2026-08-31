# -*- coding: utf-8 -*-
"""
SLI V2 月度更新脚本
===================
SLI 识别产业龙头，80% 权重来自季度/半年度财务数据，无需每日运行。
本脚本是低频重算入口：每月月末（或财报季后）运行一次即可，结果自动
追加为 SQLite 多日期快照，下游经 sli.reader 读取，无需感知更新时机。

运行逻辑：
  1. 解析目标日期：--date 指定，否则自动取最近交易日
  2. 若该日快照已存在（SQLite leaderboard_v2 已有同日数据），默认跳过；
     需要重跑时加 --force
  3. 调用 SliRunner 全量运行（V2 八维评分 → CSV 输出 → SQLite 多日期快照）
  4. 日志追加写入 logs/update_monthly.log，末尾输出更新摘要

用法：
  python -m sli.update_monthly                  # 自动取最近交易日
  python -m sli.update_monthly --force          # 同日已跑过也重跑
  python -m sli.update_monthly --date 20261030  # 指定日期（补跑历史）

每月自动执行（可选，Windows 任务计划程序）：
  schtasks /Create /TN "SLI_MonthlyUpdate" /SC MONTHLY /D 1 /ST 20:00 /TR \
    "python d:\\mystock\\solo\\sli\\update_monthly.py"
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys

from .config import DB_PATH, LOG_DIR
from .runner import SliRunner

LOG_FILE = os.path.join(LOG_DIR, "update_monthly.log")


def _setup_logger() -> logging.Logger:
    os.makedirs(LOG_DIR, exist_ok=True)
    logger = logging.getLogger("sli.update")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8", delay=False)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger


def _snapshot_exists(date: str) -> bool:
    """SQLite 中是否已存在该日期的 V2 快照。"""
    if not os.path.exists(DB_PATH):
        return False
    try:
        conn = sqlite3.connect(DB_PATH)
        try:
            cur = conn.execute(
                "SELECT 1 FROM leaderboard_v2 WHERE trade_date=? LIMIT 1", (date,))
            return cur.fetchone() is not None
        finally:
            conn.close()
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="sli.update_monthly",
        description="SLI V2 月度更新：低频重算并留档 SQLite 多日期快照",
    )
    ap.add_argument("--date", default="",
                    help="目标交易日 YYYYMMDD（缺省自动取最近交易日）")
    ap.add_argument("--force", action="store_true",
                    help="同日快照已存在也强制重跑")
    ap.add_argument("--top", type=int, default=100,
                    help="排行榜输出前 N 名（默认 100）")
    args = ap.parse_args(argv)

    log = _setup_logger()

    # 1. 先解析目标日期（SliRunner 内部自动取最近交易日）
    runner = SliRunner(date=args.date or None, simple=False)
    target = runner._resolve_date()

    # 2. 幂等：同日快照已存在则跳过
    if _snapshot_exists(target) and not args.force:
        log.info("快照 %s 已存在，跳过更新（如需重跑加 --force）", target)
        print(f"\nSLI 快照 {target} 已存在，本次跳过。")
        print("查看结果: python -m sli.reader --top 5")
        return 0

    # 3. 全量运行
    log.info("开始 SLI 月度更新，目标日期 %s ...", target)
    try:
        result = runner.run(top=args.top, industry=None, er20_csv=None, v1=False)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        log.error("SLI 运行失败: %s", exc)
        print(f"\nSLI 运行失败: {exc}", file=sys.stderr)
        return 1

    # 4. 摘要
    log.info("SLI 月度更新完成：%s 行业 %d | 股票 %d | 龙头 %d",
             result["date"], result["n_industries"],
             result["n_stocks"], result["n_leaders"])
    print(f"\nSLI 月度更新完成：{result['date']}  "
          f"行业 {result['n_industries']} | 股票 {result['n_stocks']} | "
          f"龙头 {result['n_leaders']}")
    for k, v in result["paths"].items():
        log.info("  %-16s %s", k, v)
        print(f"  {k:<16} {v}")
    print("\n下游读取（不关心更新时机，自动回退最近快照）：")
    print("  from sli.reader import get_leaderboard_v2, get_subsector_top5")
    return 0


if __name__ == "__main__":
    sys.exit(main())
