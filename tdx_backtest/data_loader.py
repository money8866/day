# -*- coding: utf-8 -*-
"""
Data Handler — 通达信本地数据读取模块

支持:
  - .day 日线文件 (32 字节/条, little-endian)
  - .lc5 5分钟线文件 (32 字节/条)
  - 上证 sh / 深证 sz / 北证 bj
  - 内存优化: 按需加载 + 日期过滤 + 迭代器批量遍历

通达信日线文件结构 (每条 32 字节):
  bytes  0-3:  日期      (int32,  YYYYMMDD)
  bytes  4-7:  开盘价*100 (int32)
  bytes  8-11: 最高价*100 (int32)
  bytes 12-15: 最低价*100 (int32)
  bytes 16-19: 收盘价*100 (int32)
  bytes 20-23: 成交额     (float32, 单位:元)
  bytes 24-27: 成交量     (int32,   /100 = 股数)
  bytes 28-31: 保留字段
"""
from __future__ import annotations
import os
import struct
import glob
from datetime import datetime
from typing import Iterator, List, Optional

import pandas as pd
import numpy as np

# =========================================================
# 全局配置
# =========================================================
TDX_DIR = r"C:\new_tdx"
VIPDOC_DIR = os.path.join(TDX_DIR, "vipdoc")

# market -> (子目录, 文件前缀)
_MARKET_MAP = {
    "SH": ("sh", "sh"),
    "SZ": ("sz", "sz"),
    "BJ": ("bj", "bj"),
}


# =========================================================
# 代码转换工具
# =========================================================
def ts_code_to_tdx_path(ts_code: str, freq: str = "day") -> str:
    """ts_code (600000.SH) → 通达信文件路径

    Args:
        ts_code: 股票代码，如 '600000.SH' / '000001.SZ' / '000001.SH'(指数)
        freq: 'day' 日线 / 'lc5' 5分钟线 / 'lc1' 1分钟线
    """
    # 修复：上证股票代码可能缺前导0（如 111011.SH → 601011.SH）
    sym, market = ts_code.split(".")
    if market == "SH" and len(sym) == 6 and sym.startswith("1"):
        # 可能是代码解析错误，尝试修复（11xxxx → 60xxxx 或 10xxxx）
        # 但实际数据中，上证股票是 60xxxx/50xxxx/55xxxx，深圳是 00xxxx/30xxxx/68xxxx
        # 这里暂时不修复，因为可能是ETF或其他品种
        pass
    if market not in _MARKET_MAP:
        raise ValueError(f"不支持的市场: {market}")
    subdir, prefix = _MARKET_MAP[market]
    return os.path.join(VIPDOC_DIR, subdir, "lday" if freq == "day" else "fzline",
                        f"{prefix}{sym}.{freq}")


def tdx_filename_to_ts_code(filename: str) -> Optional[str]:
    """sh600000.day → 600000.SH"""
    name = os.path.basename(filename).split(".")[0]
    if name.startswith("sh"):
        return name[2:] + ".SH"
    if name.startswith("sz"):
        return name[2:] + ".SZ"
    if name.startswith("bj"):
        return name[2:] + ".BJ"
    return None


# =========================================================
# 核心: 二进制文件解析
# =========================================================
def parse_day_file(filepath: str,
                   start_date: Optional[str] = None,
                   end_date: Optional[str] = None) -> pd.DataFrame:
    """解析通达信 .day 文件，返回 DataFrame

    Args:
        filepath: .day 文件路径
        start_date: 起始日期 YYYYMMDD (含), None 不限制
        end_date:   结束日期 YYYYMMDD (含), None 不限制

    Returns:
        DataFrame[trade_date, open, high, low, close, vol, amount]
        按日期升序; vol 单位=股, amount 单位=元
    """
    if not os.path.exists(filepath):
        return pd.DataFrame()

    # 用 numpy 一次性读取全部字节，再切片解析，比循环 struct.unpack 快 10 倍
    raw = np.fromfile(filepath, dtype=np.uint8)
    n = len(raw) // 32
    if n == 0:
        return pd.DataFrame()
    raw = raw[: n * 32].reshape(n, 32)

    # 逐字段切片 (little-endian)
    date_int = raw[:, 0:4].copy().view(np.int32).flatten()
    open_p   = raw[:, 4:8].copy().view(np.int32).flatten() / 100.0
    high_p   = raw[:, 8:12].copy().view(np.int32).flatten() / 100.0
    low_p    = raw[:, 12:16].copy().view(np.int32).flatten() / 100.0
    close_p  = raw[:, 16:20].copy().view(np.int32).flatten() / 100.0
    amount   = raw[:, 20:24].copy().view(np.float32).flatten()      # 成交额(元)
    vol      = raw[:, 24:28].copy().view(np.int32).flatten() / 100.0  # 成交量(股)

    df = pd.DataFrame({
        "trade_date": date_int.astype(str),
        "open":   open_p,
        "high":   high_p,
        "low":    low_p,
        "close":  close_p,
        "vol":    vol,
        "amount": amount,
    })

    # 日期过滤
    if start_date:
        df = df[df["trade_date"] >= start_date]
    if end_date:
        df = df[df["trade_date"] <= end_date]

    df = df.sort_values("trade_date").reset_index(drop=True)
    return df


def parse_lc5_file(filepath: str,
                   start_date: Optional[str] = None,
                   end_date: Optional[str] = None) -> pd.DataFrame:
    """解析通达信 .lc5 5分钟线文件

    文件结构 (32 字节/条):
      bytes  0-1: 日期 (raw, 需解码: YYYYMM -> MMDD 组合)
      bytes  2-5:  时间 (HHMM)
      bytes  6-9:  开盘价*100
      bytes 10-13: 最高价*100
      bytes 14-17: 最低价*100
      bytes 18-21: 收盘价*100
      bytes 22-25: 成交额 (float32)
      bytes 26-29: 成交量 (int32)
      bytes 30-31: 保留
    """
    if not os.path.exists(filepath):
        return pd.DataFrame()
    raw = np.fromfile(filepath, dtype=np.uint8)
    n = len(raw) // 32
    if n == 0:
        return pd.DataFrame()
    raw = raw[: n * 32].reshape(n, 32).copy()

    # 通达信5分钟线日期编码: bytes 0-1 = (year-2004)*2048 + month*100 + day
    date_raw = raw[:, 0:2].view(np.uint16).flatten()
    year = date_raw // 2048 + 2004
    md   = date_raw %  2048
    month = md // 100
    day   = md %  100
    time_raw = raw[:, 2:4].copy().view(np.uint16).flatten()
    hh = time_raw // 100
    mm = time_raw %  100
    # 组合为 YYYYMMDD 字符串
    dt_str = (year * 10000 + month * 100 + day).astype(str)

    open_p  = raw[:, 4:8].copy().view(np.int32).flatten() / 100.0
    high_p  = raw[:, 8:12].copy().view(np.int32).flatten() / 100.0
    low_p   = raw[:, 12:16].copy().view(np.int32).flatten() / 100.0
    close_p = raw[:, 16:20].copy().view(np.int32).flatten() / 100.0
    amount  = raw[:, 20:24].copy().view(np.float32).flatten()
    vol     = raw[:, 24:28].copy().view(np.int32).flatten()

    df = pd.DataFrame({
        "trade_date": dt_str,
        "time": time_raw,
        "open": open_p, "high": high_p, "low": low_p, "close": close_p,
        "vol": vol, "amount": amount,
    })
    if start_date:
        df = df[df["trade_date"] >= start_date]
    if end_date:
        df = df[df["trade_date"] <= end_date]
    return df.reset_index(drop=True)


# =========================================================
# 高层加载接口
# =========================================================
def load_kline(ts_code: str,
               start_date: Optional[str] = None,
               end_date: Optional[str] = None,
               freq: str = "day") -> pd.DataFrame:
    """加载单只股票 K 线

    Args:
        ts_code: '600000.SH' / '000001.SZ'
        start_date, end_date: YYYYMMDD
        freq: 'day' / 'lc5' / 'lc1'

    Returns:
        DataFrame, 含 ts_code 列; 列: ts_code, trade_date, open, high, low, close, vol, amount
    """
    path = ts_code_to_tdx_path(ts_code, freq=freq)
    if freq == "day":
        df = parse_day_file(path, start_date, end_date)
    else:
        df = parse_lc5_file(path, start_date, end_date)
    if df.empty:
        return df
    df.insert(0, "ts_code", ts_code)
    # 衍生字段
    df["pct_chg"] = df["close"].pct_change() * 100
    return df


def load_multiple(codes: List[str],
                  start_date: Optional[str] = None,
                  end_date: Optional[str] = None) -> pd.DataFrame:
    """加载多只股票日线，concat 成长表

    Returns:
        DataFrame[ts_code, trade_date, open, high, low, close, vol, amount, pct_chg]
    """
    frames = []
    for code in codes:
        df = load_kline(code, start_date, end_date)
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def iter_all_day_files(markets: List[str] = ("SH", "SZ")) -> Iterator[str]:
    """迭代器: 遍历全部 .day 文件路径 (内存优化, 避免一次 glob 全部)

    Args:
        markets: 要遍历的市场 ('SH'/'SZ'/'BJ')

    Yields:
        .day 文件完整路径
    """
    for mkt in markets:
        if mkt not in _MARKET_MAP:
            continue
        subdir, _ = _MARKET_MAP[mkt]
        lday_dir = os.path.join(VIPDOC_DIR, subdir, "lday")
        if not os.path.isdir(lday_dir):
            continue
        # glob 返回列表, 大目录可能占内存; 用 os.scandir 迭代器更优
        with os.scandir(lday_dir) as it:
            for entry in it:
                if entry.name.endswith(".day") and entry.is_file():
                    yield entry.path


def list_all_codes(markets: List[str] = ("SH", "SZ")) -> List[str]:
    """列出全部可用股票代码"""
    codes = []
    for path in iter_all_day_files(markets):
        c = tdx_filename_to_ts_code(path)
        if c:
            codes.append(c)
    return codes


# =========================================================
# CLI 自检
# =========================================================
if __name__ == "__main__":
    print(f"TDX 目录: {TDX_DIR}")
    print(f"vipdoc 目录: {VIPDOC_DIR}")

    # 测试: 上证指数
    df = load_kline("999999.SH", start_date="20240101")
    # 上证指数代码在通达信里是 sh999999, 如果不存在试 000001.SH
    if df.empty:
        df = load_kline("000001.SH", start_date="20240101")
    if not df.empty:
        print(f"\n上证指数(000001.SH): {len(df)} 条")
        print(df.head(3))
        print(df.tail(3))
    else:
        print("未找到上证指数数据")

    # 测试: 平安银行
    df2 = load_kline("000001.SZ", start_date="20240101")
    if not df2.empty:
        print(f"\n平安银行(000001.SZ): {len(df2)} 条")
        print(df2.tail(3))

    # 统计文件数
    n_sh = len(glob.glob(os.path.join(VIPDOC_DIR, "sh", "lday", "*.day")))
    n_sz = len(glob.glob(os.path.join(VIPDOC_DIR, "sz", "lday", "*.day")))
    print(f"\n文件统计: SH={n_sh}, SZ={n_sz}, 合计={n_sh + n_sz}")
