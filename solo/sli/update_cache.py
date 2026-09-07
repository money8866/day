# -*- coding: utf-8 -*-
"""
SLI 轻量增量缓存更新（供每日 run_all / IGE 前置调用）
=====================================================
只把 trade_cal / daily / daily_basic 缓存补齐到「最近交易日」，
不做任何 SLI 评分重算（SLI 快照由 update_monthly 低频生成）。
下游 IGE 为 cache-first（不请求 API），必须先由本脚本保证当日行情缓存存在。

用法：
    python -X utf8 -m sli.update_cache             # 目标日 = 今天
    python -X utf8 -m sli.update_cache --date 20260907
退出码：0 = 正常（打印最近交易日 YYYYMMDD）；1 = 失败
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import logging
import os
import sys

import pandas as pd

from .cache import SliCache
from .config import CACHE_DIR
from .datasource import DataSource
from .utils import load_token

logger = logging.getLogger("sli.update_cache")

# 需要保障覆盖的最近交易日数量（覆盖上次全量跑之后的增量）
BACKFILL_DAYS = 10


def _today() -> str:
    return _dt.date.today().strftime("%Y%m%d")


def _cached_daily_dates(prefix: str) -> set[str]:
    """现有 daily_* / daily_basic_* 缓存已覆盖的交易日集合。"""
    out: set[str] = set()
    for p in glob.glob(os.path.join(CACHE_DIR, f"{prefix}_*.parquet")):
        base = os.path.basename(p)[len(prefix) + 1:-len(".parquet")]
        if base.isdigit() and len(base) == 8:
            out.add(base)
    return out


def update_cache(target: str) -> str:
    """补齐至最近交易日缓存，返回该最近交易日 YYYYMMDD。"""
    cache = SliCache(CACHE_DIR)
    ds = DataSource(load_token(), cache)

    # 1) 日历对齐：get_trade_cal 内部缓存优先，缺文件时拉取并写
    #    trade_cal_20240101_{target}.parquet（IGE 精确依赖该文件）
    cal = ds.get_trade_cal("20240101", target)
    if cal is None or cal.empty:
        logger.error("trade_cal 拉取失败（%s）", target)
        return ""
    if "cal_date" not in cal.columns or "is_open" not in cal.columns:
        logger.error("trade_cal 缺 cal_date/is_open 列")
        return ""
    open_dates = sorted(
        cal.loc[cal["is_open"] == 1, "cal_date"].astype(str).tolist())
    open_dates = [d for d in open_dates if d <= target]
    if not open_dates:
        logger.error("目标日前无开市日: %s", target)
        return ""
    td = open_dates[-1]  # 最近交易日

    # 2) daily / daily_basic 增量补齐（只补缺失的最近交易日）
    have_d = _cached_daily_dates("daily")
    have_b = _cached_daily_dates("daily_basic")
    need_d = [d for d in open_dates[-BACKFILL_DAYS:] if d not in have_d]
    need_b = [d for d in open_dates[-BACKFILL_DAYS:] if d not in have_b]

    if need_d:
        logger.info("补齐 daily 缺失 %d 天: %s .. %s", len(need_d), need_d[0], need_d[-1])
        df = ds.get_daily_dates(need_d)
        logger.info("daily 已补齐 %d 行", 0 if df is None else len(df))
    else:
        logger.info("daily 缓存已覆盖最近交易日 %s，无需拉取", td)
    if need_b:
        logger.info("补齐 daily_basic 缺失 %d 天: %s .. %s", len(need_b), need_b[0], need_b[-1])
        dfb = ds.get_daily_basic_dates(need_b)
        logger.info("daily_basic 已补齐 %d 行", 0 if dfb is None else len(dfb))
    else:
        logger.info("daily_basic 缓存已覆盖最近交易日 %s，无需拉取", td)

    print(f"最近交易日: {td}")
    return td


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SLI 轻量增量缓存更新（IGE 前置）")
    ap.add_argument("--date", default=_today(), help="目标日 YYYYMMDD（缺省今天）")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S", stream=sys.stdout)
    td = update_cache(args.date)
    if not td:
        return 1
    logger.info("缓存更新完成，最近交易日 %s", td)
    return 0


if __name__ == "__main__":
    sys.exit(main())
