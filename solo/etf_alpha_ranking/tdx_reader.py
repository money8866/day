#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
TDX Data Reader
===============
Primary market data source: TongDaXin local .day files.
Fallback: CSV cache (Tushare fund_daily format).

.day file format: 32 bytes/record (little-endian)
  [0:4]   int32  date (YYYYMMDD)
  [4:8]   int32  open  (/100)
  [8:12]  int32  high  (/100)
  [12:16] int32  low   (/100)
  [16:20] int32  close (/100)
  [20:24] float32 amount (千元)
  [24:28] int32  volume (手/100)
  [28:32] reserved
"""
from __future__ import annotations

import os
import struct
import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

LOG = logging.getLogger("etf_alpha_ranking.tdx")

__all__ = [
    "TDXReader",
    "parse_tdx_day_file",
    "ts_code_to_tdx_file",
]


def ts_code_to_tdx_file(ts_code: str, tdx_root: str) -> Optional[str]:
    """Map a ts_code (e.g. 159516.SZ) to its TDX .day file path."""
    if "." not in ts_code:
        return None
    sym, market = ts_code.split(".")
    sym = sym.lstrip("0") or "0"  # TDX filenames drop leading zeros
    if market == "SH":
        prefix, subdir = "sh", "sh"
    elif market == "SZ":
        prefix, subdir = "sz", "sz"
    else:
        return None
    return os.path.join(tdx_root, "vipdoc", subdir, "lday", f"{prefix}{sym}.day")


def parse_tdx_day_file(filepath: str) -> Optional[pd.DataFrame]:
    """Parse a TDX .day file into a DataFrame."""
    if not os.path.exists(filepath):
        return None
    records = []
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(32)
            if not chunk or len(chunk) < 32:
                break
            date_int = struct.unpack("<i", chunk[0:4])[0]
            open_p = struct.unpack("<i", chunk[4:8])[0] / 100.0
            high_p = struct.unpack("<i", chunk[8:12])[0] / 100.0
            low_p = struct.unpack("<i", chunk[12:16])[0] / 100.0
            close_p = struct.unpack("<i", chunk[16:20])[0] / 100.0
            amount_val = struct.unpack("<f", chunk[20:24])[0]
            vol_shares = struct.unpack("<i", chunk[24:28])[0] / 100.0
            records.append({
                "trade_date": str(date_int),
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "close": close_p,
                "vol": vol_shares,
                "amount": round(amount_val / 1000.0, 3),  # 千元 -> 万元
            })
    if not records:
        return None
    df = pd.DataFrame(records).sort_values("trade_date").reset_index(drop=True)
    df["pct_chg"] = (df["close"].pct_change() * 100.0).fillna(0.0)
    return df


class TDXReader:
    """Load daily OHLCV from TDX .day files, with CSV cache fallback.

    Priority:
      1. TDX .day file (fastest, local, always up-to-date after TDX sync)
      2. CSV cache at ``daily_cache_path`` (Tushare fund_daily format)
    """

    def __init__(self, tdx_root: str, daily_cache_path: str = ""):
        self.tdx_root = tdx_root
        self.daily_cache_path = daily_cache_path
        self._day_cache: Dict[str, pd.DataFrame] = {}

    # ------------------------------------------------------------------
    # Single code loaders
    # ------------------------------------------------------------------
    def load_daily_price(self, ts_code: str,
                         start_date: str = "", end_date: str = "") -> pd.DataFrame:
        """Load daily OHLCV for a single code (ETF / stock / index).

        Returns a DataFrame with columns:
            trade_date, open, high, low, close, vol, amount, pct_chg, ts_code
        Empty DataFrame if not found.
        """
        if ts_code in self._day_cache:
            df = self._day_cache[ts_code]
        else:
            df = self._load_raw(ts_code)
            if df is None or df.empty:
                return pd.DataFrame()
            self._day_cache[ts_code] = df

        df = df.copy()
        if start_date:
            df = df[df["trade_date"] >= start_date]
        if end_date:
            df = df[df["trade_date"] <= end_date]
        df["ts_code"] = ts_code
        return df.reset_index(drop=True)

    def _load_raw(self, ts_code: str) -> Optional[pd.DataFrame]:
        # 1) TDX .day
        tdx_file = ts_code_to_tdx_file(ts_code, self.tdx_root)
        if tdx_file:
            df = parse_tdx_day_file(tdx_file)
            if df is not None and not df.empty:
                return df
        # 2) CSV cache
        if self.daily_cache_path:
            for fname in (f"{ts_code}.csv", f"{ts_code.replace('.', '_')}.csv"):
                fp = os.path.join(self.daily_cache_path, fname)
                if os.path.exists(fp):
                    df = self._load_csv(fp, ts_code)
                    if df is not None and not df.empty:
                        return df
        return None

    @staticmethod
    def _load_csv(filepath: str, ts_code: str) -> Optional[pd.DataFrame]:
        try:
            df = pd.read_csv(filepath)
        except Exception as e:
            LOG.warning("CSV read failed %s: %s", filepath, e)
            return None
        if "trade_date" not in df.columns:
            return None
        df["trade_date"] = df["trade_date"].astype(str)
        keep = ["trade_date", "open", "high", "low", "close", "vol", "amount"]
        for c in keep:
            if c not in df.columns:
                df[c] = np.nan
        df = df[keep].copy()
        for c in ["open", "high", "low", "close", "vol", "amount"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["close"]).sort_values("trade_date").reset_index(drop=True)
        df["pct_chg"] = (df["close"].pct_change() * 100.0).fillna(0.0)
        return df

    # ------------------------------------------------------------------
    # Batch loaders
    # ------------------------------------------------------------------
    def load_batch_etf(self, etf_codes: List[str],
                       start_date: str = "", end_date: str = "") -> Dict[str, pd.DataFrame]:
        """Batch load ETF daily data. Returns {ts_code: DataFrame}."""
        out: Dict[str, pd.DataFrame] = {}
        for code in etf_codes:
            df = self.load_daily_price(code, start_date, end_date)
            if not df.empty and len(df) >= 30:
                out[code] = df
        LOG.info("TDX batch loaded %d/%d ETFs", len(out), len(etf_codes))
        return out

    def load_batch_stocks(self, stock_codes: List[str],
                          start_date: str = "", end_date: str = "") -> Dict[str, pd.DataFrame]:
        """Batch load stock daily data."""
        return self.load_batch_etf(stock_codes, start_date, end_date)

    def load_index(self, ts_code: str,
                   start_date: str = "", end_date: str = "") -> pd.DataFrame:
        """Load an index daily series (e.g. 000300.SH)."""
        return self.load_daily_price(ts_code, start_date, end_date)

    # ------------------------------------------------------------------
    # Incremental cache update
    # ------------------------------------------------------------------
    def update_daily_cache(self, trade_date: str, etf_codes: List[str],
                           start_date: str = "") -> int:
        """Persist the latest TDX data for ``trade_date`` to CSV cache.

        Writes/merges into ``daily_cache_path`` so other tools can reuse it.
        Returns the number of records written.
        """
        if not self.daily_cache_path:
            return 0
        os.makedirs(self.daily_cache_path, exist_ok=True)
        written = 0
        for code in etf_codes:
            df = self.load_daily_price(code, start_date, trade_date)
            if df.empty:
                continue
            fp = os.path.join(self.daily_cache_path, f"{code}.csv")
            out = df[["ts_code", "trade_date", "open", "high", "low",
                      "close", "vol", "amount", "pct_chg"]].copy()
            out.rename(columns={"pct_chg": "pct_chg"}, inplace=True)
            if os.path.exists(fp):
                try:
                    old = pd.read_csv(fp)
                    old["trade_date"] = old["trade_date"].astype(str)
                    combined = pd.concat([old, out], ignore_index=True)
                    combined = combined.drop_duplicates(
                        subset=["ts_code", "trade_date"], keep="last")
                    combined = combined.sort_values("trade_date").reset_index(drop=True)
                    combined.to_csv(fp, index=False)
                    written += len(out)
                    continue
                except Exception as e:
                    LOG.warning("merge failed %s: %s, overwriting", code, e)
            out.to_csv(fp, index=False)
            written += len(out)
        LOG.info("update_daily_cache: %d records for %s", written, trade_date)
        return written

    def get_last_trade_date(self, etf_codes: List[str]) -> str:
        """Return the most recent trade_date available across the given ETFs."""
        latest = ""
        for code in etf_codes:
            df = self.load_daily_price(code)
            if not df.empty:
                d = str(df["trade_date"].iloc[-1])
                if d > latest:
                    latest = d
        return latest
