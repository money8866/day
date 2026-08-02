"""
数据装载器 DataLoader

通用抽象：
  load_local_data(data_type, **kwargs)   →  优先从本地缓存读取，缺失时 API 补全

已支持的 data_type:
  - "daily"        日线行情
  - "stock_basic"  股票基本信息
  - "daily_basic"  每日基本面（市值/换手率）

所有读取操作优先走本地缓存（CSV / Parquet / SQLite），
仅对真正缺失的日期范围调用 Tushare API 补全。
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import tushare as ts

from .config import get_config

logger = logging.getLogger("mainline_pullback.data_loader")

# 全局 tushare pro 实例（懒初始化）
_pro: Any = None


def _get_pro():
    global _pro
    if _pro is None:
        token = get_config().path.tushare_token
        if token:
            ts.set_token(token)
        _pro = ts.pro_api()
    return _pro


# ════════════════════════════════════════════════════════════
# 通用本地缓存加载器
# ════════════════════════════════════════════════════════════

def load_local_data(
    data_type: str,
    **kwargs: Any,
) -> Optional[pd.DataFrame]:
    """通用本地数据加载函数 — 100% 优先本地缓存，缺失时 API 补全。

    Args:
        data_type: 数据类型，支持 "daily" / "stock_basic" / "daily_basic"。
        **kwargs: 按 data_type 不同传入不同参数（见各函数文档）。

    Returns:
        DataFrame，失败时返回 None。
    """
    loader_map = {
        "daily": _load_daily,
        "stock_basic": _load_stock_basic,
        "daily_basic": _load_daily_basic,
    }
    loader = loader_map.get(data_type)
    if loader is None:
        logger.error(f"未知的 data_type: {data_type}，可选: {list(loader_map.keys())}")
        return None
    return loader(**kwargs)


# ════════════════════════════════════════════════════════════
# 日线行情
# ════════════════════════════════════════════════════════════

# 全局内存缓存（避免重复读盘）
_daily_snapshot_cache: dict[str, pd.DataFrame] = {}       # "YYYYMMDD" → DataFrame
_daily_individual_cache: dict[str, pd.DataFrame] = {}     # "ts_code" → DataFrame
_stock_basic_df: Optional[pd.DataFrame] = None


def _load_daily(
    ts_code: str = "",
    trade_date: str = "",
    start_date: str = "",
    end_date: str = "",
) -> Optional[pd.DataFrame]:
    """加载日线行情数据。

    数据来源优先级：
      1. 个股缓存文件 {cache_daily}/{ts_code}.csv
      2. 日快照文件 cache_daily/daily_{YYYYMMDD}.csv
      3. Tushare API 补全

    Args:
        ts_code:   股票代码（如 "000001.SZ"），留空则加载全市场
        trade_date: 指定交易日（如 "20260724"）
        start_date: 起始日期（如 "20260701"），与 end_date 配合使用
        end_date:   截止日期

    Returns:
        DataFrame — 列包括 ts_code, trade_date, open, high, low, close,
                    pre_close, change, pct_chg, vol, amount
    """
    cfg = get_config()
    cache_dir = Path(cfg.path.cache_daily)

    # ── 1. 个股缓存文件（{ts_code}.csv）— 含多日数据 ──
    if ts_code:
        # 先从内存缓存查
        if ts_code in _daily_individual_cache:
            df = _daily_individual_cache[ts_code]
            if _check_date_coverage(df, start_date, end_date):
                return _filter_date_range(df, start_date, end_date)
        # 再从磁盘读
        ind_path = cache_dir / f"{ts_code}.csv"
        if ind_path.exists():
            try:
                df = pd.read_csv(ind_path, dtype={"ts_code": str})
                if not df.empty and "trade_date" in df.columns:
                    df["trade_date"] = df["trade_date"].astype(str)
                    _daily_individual_cache[ts_code] = df
                    if _check_date_coverage(df, start_date, end_date):
                        return _filter_date_range(df, start_date, end_date)
            except Exception as exc:
                logger.warning("读取个股缓存 %s 失败: %s", ind_path.name, exc)

    # ── 2. 日快照文件（daily_{YYYYMMDD}.csv）— 仅当指定 trade_date 时 ──
    if trade_date:
        cache_key = trade_date
        if cache_key in _daily_snapshot_cache:
            df = _daily_snapshot_cache[cache_key]
            if ts_code:
                sub = df[df["ts_code"] == ts_code]
                return sub if not sub.empty else None
            return df

        snap_path = cache_dir / f"daily_{trade_date}.csv"
        if snap_path.exists():
            try:
                df = pd.read_csv(snap_path, dtype={"ts_code": str})
                if not df.empty:
                    df["trade_date"] = df["trade_date"].astype(str)
                    _daily_snapshot_cache[cache_key] = df
                    if ts_code:
                        sub = df[df["ts_code"] == ts_code]
                        return sub if not sub.empty else None
                    return df
            except Exception as exc:
                logger.warning("读取日快照 %s 失败: %s", snap_path.name, exc)

    # ── 3. 范围查询：构建日期列表逐个查快照 ──
    if start_date and end_date and not ts_code:
        # 全市场范围数据：拼合多个 daily_YYYYMMDD.csv
        frames: list[pd.DataFrame] = []
        dates = _build_date_range(start_date, end_date)
        for d in dates:
            if d in _daily_snapshot_cache:
                frames.append(_daily_snapshot_cache[d])
            else:
                snap_path = cache_dir / f"daily_{d}.csv"
                if snap_path.exists():
                    try:
                        df = pd.read_csv(snap_path, dtype={"ts_code": str})
                        if not df.empty:
                            df["trade_date"] = df["trade_date"].astype(str)
                            _daily_snapshot_cache[d] = df
                            frames.append(df)
                    except Exception:
                        pass
        if frames:
            result = pd.concat(frames, ignore_index=True)
            result = result.drop_duplicates(subset=["ts_code", "trade_date"])
            return result.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)

    # ── 4. API 补全（仅当本地确实缺失）──
    return _supplement_daily_from_api(
        ts_code=ts_code,
        trade_date=trade_date,
        start_date=start_date,
        end_date=end_date,
        cache_dir=cache_dir,
    )


def _check_date_coverage(df: pd.DataFrame, start_date: str, end_date: str) -> bool:
    """检查 DataFrame 是否覆盖了 [start_date, end_date] 范围"""
    if not start_date or not end_date:
        return True  # 未指定范围时视为覆盖
    if df.empty:
        return False
    min_d = df["trade_date"].min()
    max_d = df["trade_date"].max()
    return min_d <= start_date and max_d >= end_date


def _filter_date_range(df: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
    """按日期范围过滤 DataFrame"""
    if not start_date and not end_date:
        return df
    mask = True
    if start_date:
        mask &= df["trade_date"] >= start_date
    if end_date:
        mask &= df["trade_date"] <= end_date
    result = df[mask].copy()
    return result.sort_values("trade_date").reset_index(drop=True) if not result.empty else result


def _build_date_range(start_date: str, end_date: str) -> list[str]:
    """生成 YYYYMMDD 日期列表（仅交易日估算，不做精确校验）"""
    dates: list[str] = []
    try:
        start = datetime.strptime(start_date, "%Y%m%d")
        end = datetime.strptime(end_date, "%Y%m%d")
        current = start
        while current <= end:
            if current.weekday() < 5:  # 粗略跳过周末
                dates.append(current.strftime("%Y%m%d"))
            current += timedelta(days=1)
    except Exception:
        pass
    return dates


def _supplement_daily_from_api(
    ts_code: str,
    trade_date: str,
    start_date: str,
    end_date: str,
    cache_dir: Path,
) -> Optional[pd.DataFrame]:
    """API 补全日线行情并写入本地缓存"""
    pro = _get_pro()

    # 确定实际需要拉取的日期范围
    actual_start = start_date or trade_date or "20200101"
    actual_end = end_date or trade_date or datetime.now().strftime("%Y%m%d")

    try:
        if ts_code:
            logger.info("[API补全] %s 日线 %s~%s", ts_code, actual_start, actual_end)
            # V2: 优先 daily_cache 表
            df = None
            try:
                from stock_cache import get_daily_cache, get_daily_cache_range, batch_insert_daily_cache
                _, _max_date = get_daily_cache_range(ts_code)
                if _max_date is not None and str(_max_date) >= str(actual_end):
                    df = get_daily_cache(ts_code, actual_start, actual_end)
                    if df is not None and not df.empty:
                        df['trade_date'] = df['trade_date'].astype(str)
            except Exception:
                pass
            if df is None or df.empty:
                df = pro.daily(
                    ts_code=ts_code,
                    start_date=actual_start,
                    end_date=actual_end,
                )
                if df is not None and not df.empty:
                    try:
                        from stock_cache import batch_insert_daily_cache
                        batch_insert_daily_cache(df)
                    except Exception:
                        pass
            time.sleep(0.12)
        else:
            logger.info("[API补全] 全市场日线 %s~%s", actual_start, actual_end)
            # V2: 优先 daily_cache 表（按日期遍历）
            df = None
            try:
                from stock_cache import get_daily_by_date, get_daily_by_date_count, batch_insert_daily_cache
                _parts = []
                _cur = datetime.strptime(actual_start, '%Y%m%d')
                _end_dt = datetime.strptime(actual_end, '%Y%m%d')
                while _cur <= _end_dt:
                    _td = _cur.strftime('%Y%m%d')
                    if get_daily_by_date_count(_td) > 0:
                        _d = get_daily_by_date(_td)
                        if _d is not None and not _d.empty:
                            _parts.append(_d)
                    _cur += timedelta(days=1)
                if _parts:
                    df = pd.concat(_parts, ignore_index=True)
            except Exception:
                pass
            if df is None or df.empty:
                df = pro.daily(
                    start_date=actual_start,
                    end_date=actual_end,
                )
                if df is not None and not df.empty:
                    try:
                        from stock_cache import batch_insert_daily_cache
                        batch_insert_daily_cache(df)
                    except Exception:
                        pass
            time.sleep(0.12)

        if df is None or df.empty:
            logger.warning("[API补全] %s 无数据返回", ts_code or "全市场")
            return None

        df["trade_date"] = df["trade_date"].astype(str)

        # 写入个股缓存（仅当 ts_code 指定时）
        if ts_code:
            ind_path = cache_dir / f"{ts_code}.csv"
            existing: Optional[pd.DataFrame] = None
            if ind_path.exists():
                try:
                    existing = pd.read_csv(ind_path, dtype={"ts_code": str})
                    existing["trade_date"] = existing["trade_date"].astype(str)
                except Exception:
                    pass
            if existing is not None:
                combined = pd.concat([existing, df], ignore_index=True)
                combined = combined.drop_duplicates(subset=["trade_date"]).sort_values("trade_date")
            else:
                combined = df.sort_values("trade_date")
            combined.to_csv(ind_path, index=False)
            _daily_individual_cache[ts_code] = combined
        else:
            # 全市场数据拆成个股 CSV（谨慎操作，仅写 trade_date 对应的快照）
            if trade_date:
                snap_path = cache_dir / f"daily_{trade_date}.csv"
                df.to_csv(snap_path, index=False)
                _daily_snapshot_cache[trade_date] = df

        return df.sort_values("trade_date").reset_index(drop=True)

    except Exception as exc:
        logger.error("[API补全] %s 失败: %s", ts_code or "全市场", exc)
        return None


# ════════════════════════════════════════════════════════════
# 股票基本信息
# ════════════════════════════════════════════════════════════

def _load_stock_basic(
    fields: Optional[list[str]] = None,
) -> Optional[pd.DataFrame]:
    """加载股票基本信息。

    数据来源优先级：
      1. cache_daily/stock_basic.csv
      2. Tushare stock_basic API

    Returns:
        DataFrame — 列: ts_code, name, industry, list_date, market 等
    """
    global _stock_basic_df
    cfg = get_config()
    cache_dir = Path(cfg.path.cache_daily)

    # 1. 内存缓存
    if _stock_basic_df is not None:
        return _stock_basic_df

    # 2. 本地 CSV
    sb_path = cache_dir / "stock_basic.csv"
    if sb_path.exists():
        try:
            df = pd.read_csv(
                sb_path,
                dtype={"ts_code": str, "name": str, "industry": str, "list_date": str},
            )
            _stock_basic_df = df
            logger.info("加载 stock_basic.csv: %d 条记录", len(df))
            return df
        except Exception as exc:
            logger.warning("读取 stock_basic.csv 失败: %s", exc)

    # 3. API
    pro = _get_pro()
    try:
        logger.info("[API补全] stock_basic ...")
        df = pro.stock_basic(
            fields="ts_code,name,industry,area,market,list_date",
        )
        time.sleep(0.12)
        if df is not None and not df.empty:
            df.to_csv(sb_path, index=False)
            _stock_basic_df = df
            logger.info("API 获取 stock_basic: %d 条", len(df))
            return df
    except Exception as exc:
        logger.error("[API补全] stock_basic 失败: %s", exc)

    return None


# ════════════════════════════════════════════════════════════
# 每日基本面（市值/换手率）
# ════════════════════════════════════════════════════════════

def _load_daily_basic(
    ts_code: str = "",
    trade_date: str = "",
    start_date: str = "",
    end_date: str = "",
) -> Optional[pd.DataFrame]:
    """加载每日基本面数据（市值、换手率等）。

    数据来源优先级：
      1. treasure_daily_basic_*.parquet 缓存
      2. daily_basic_{ts_code}.csv 个股缓存
      3. Tushare daily_basic API

    Args:
        ts_code:    股票代码
        trade_date: 指定交易日
        start_date: 起始日期
        end_date:   截止日期

    Returns:
        DataFrame — 列: ts_code, trade_date, turnover_rate, volume_ratio,
                    total_mv, circ_mv, pe, pb
    """
    cfg = get_config()
    cache_dir = Path(cfg.path.cache_daily)

    # 1. Parquet 缓存（treasure_daily_basic_*.parquet）
    parquet_files = sorted(cache_dir.glob("treasure_daily_basic_*.parquet"))
    if parquet_files:
        try:
            df = pd.read_parquet(parquet_files[-1])
            if "trade_date" in df.columns:
                df["trade_date"] = df["trade_date"].astype(str)
            if ts_code:
                sub = df[df["ts_code"] == ts_code]
                if not sub.empty:
                    return _filter_date_range(sub, start_date, end_date)
            return _filter_date_range(df, start_date, end_date)
        except Exception as exc:
            logger.warning("读取 treasure_daily_basic parquet 失败: %s", exc)

    # 2. 个股 daily_basic CSV 缓存
    if ts_code:
        safe_code = ts_code.replace(".", "_")
        db_path = cache_dir / f"daily_basic_{safe_code}.csv"
        if db_path.exists():
            try:
                df = pd.read_csv(db_path, dtype={"ts_code": str})
                df["trade_date"] = df["trade_date"].astype(str)
                sub = _filter_date_range(df, start_date, end_date)
                if sub is not None and not sub.empty:
                    return sub
            except Exception as exc:
                logger.warning("读取 daily_basic %s 失败: %s", db_path.name, exc)

    # 3. API
    pro = _get_pro()
    try:
        actual_start = start_date or trade_date or "20200101"
        actual_end = end_date or trade_date or datetime.now().strftime("%Y%m%d")
        logger.info("[API补全] daily_basic %s %s~%s", ts_code or "全市场", actual_start, actual_end)

        params = dict(
            start_date=actual_start,
            end_date=actual_end,
            fields="ts_code,trade_date,turnover_rate,turnover_rate_f,volume_ratio,pe,pe_ttm,pb,total_mv,circ_mv",
        )
        if ts_code:
            params["ts_code"] = ts_code
        df = pro.daily_basic(**params)
        time.sleep(0.12)

        if df is not None and not df.empty:
            df["trade_date"] = df["trade_date"].astype(str)
            # 写入个股缓存
            if ts_code:
                safe_code = ts_code.replace(".", "_")
                db_path = cache_dir / f"daily_basic_{safe_code}.csv"
                df.to_csv(db_path, index=False)
            return df.sort_values("trade_date").reset_index(drop=True)
    except Exception as exc:
        logger.error("[API补全] daily_basic 失败: %s", exc)

    return None


# ════════════════════════════════════════════════════════════
# 交易日识别
# ════════════════════════════════════════════════════════════

def get_last_trade_date() -> str:
    """获取最近一个交易日

    规则：
      - 16:00 前 → 上一交易日
      - 16:00 后 → 当天
      - 周六/周日 → 上周五
    """
    now = datetime.now()
    if now.hour < 16:
        d = now - timedelta(days=1)
    else:
        d = now
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def get_full_stock_list() -> list[str]:
    """获取全市场股票代码列表（排除退市/ST等）"""
    sb = _load_stock_basic()
    if sb is None or sb.empty:
        return []
    return sb["ts_code"].tolist()
