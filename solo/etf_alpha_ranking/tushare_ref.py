#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Tushare Reference Data
======================
LOW-FREQUENCY reference data only. NEVER used for daily price calculation
(TDX is the primary daily source).

Provides:
  - ETF basic info (weekly)
  - ETF component stocks (weekly)
  - Industry / concept mapping (weekly)
  - Financial data (quarterly) -- optional

All results cached to local SQLite (database.py). When Tushare is not
configured, the system gracefully falls back to:
  - config.yaml etf_universe  (ETF -> theme)
  - theme_stock_map_latest.json  (theme -> stocks)
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Dict, List, Optional

import pandas as pd

LOG = logging.getLogger("etf_alpha_ranking.tushare")

TUSHARE_RATE_INTERVAL = 0.12  # 120ms thread-safe minimum


class TushareRef:
    """Low-frequency Tushare reference loader with local caching."""

    def __init__(self, config: dict, db):
        self.config = config
        self.db = db
        ref_cfg = config.get("tushare_ref", {})
        self.etf_basic_days = ref_cfg.get("etf_basic_update_days", 7)
        self.components_days = ref_cfg.get("etf_components_update_days", 7)
        self.financial_days = ref_cfg.get("financial_update_days", 90)
        self.enable_share = ref_cfg.get("enable_share_size", False)
        self._pro = None
        self._token = ""
        self._last_call_ts = 0.0

    # ------------------------------------------------------------------
    # Lazy Tushare init
    # ------------------------------------------------------------------
    def _init_pro(self) -> bool:
        if self._pro is not None:
            return True
        data_cfg = self.config.get("data", {})
        env_file = data_cfg.get("tushare_env_file", "")
        token_env = data_cfg.get("tushare_token_env", "TUSHARE_TOKEN")
        token = os.getenv(token_env, "")
        if not token and env_file and os.path.exists(env_file):
            try:
                from dotenv import load_dotenv
                load_dotenv(env_file)
                token = os.getenv(token_env, "")
            except Exception:
                pass
        if not token or not token.strip():
            return False
        try:
            import tushare as ts
            ts.set_token(token.strip())
            self._pro = ts.pro_api()
            self._token = token.strip()
            return True
        except Exception as e:
            LOG.warning("Tushare init failed: %s", e)
            return False

    def _throttle(self):
        elapsed = time.time() - self._last_call_ts
        if elapsed < TUSHARE_RATE_INTERVAL:
            time.sleep(TUSHARE_RATE_INTERVAL - elapsed)
        self._last_call_ts = time.time()

    # ------------------------------------------------------------------
    # ETF basic info
    # ------------------------------------------------------------------
    def update_etf_basic(self, etf_codes: List[str], force: bool = False) -> pd.DataFrame:
        """Update ETF basic info. Returns cached DataFrame."""
        existing = self.db.get_etf_basic()
        cached_codes = set(existing["ts_code"]) if not existing.empty else set()
        need = [c for c in etf_codes if c not in cached_codes] if not force else etf_codes
        if not need:
            return existing
        rows = []
        if self._init_pro():
            try:
                self._throttle()
                df = self._pro.fund_basic(market="E",
                                          fields="ts_code,name,list_date,management")
                if df is not None and not df.empty:
                    df = df[df["ts_code"].isin(need)]
                    for _, r in df.iterrows():
                        theme = self.config.get("etf_universe", {}).get(r["ts_code"], "")
                        rows.append({
                            "ts_code": r["ts_code"], "name": r.get("name", ""),
                            "exchange": "SH" if r["ts_code"].endswith(".SH") else "SZ",
                            "theme": theme, "industry": theme,
                            "updated": time.strftime("%Y-%m-%d"),
                        })
            except Exception as e:
                LOG.warning("fund_basic failed: %s", e)
        # Fallback / fill missing from config
        have = {r["ts_code"] for r in rows}
        for code in need:
            if code in have:
                continue
            theme = self.config.get("etf_universe", {}).get(code, "")
            rows.append({
                "ts_code": code, "name": code, "exchange": code.split(".")[-1],
                "theme": theme, "industry": theme,
                "updated": time.strftime("%Y-%m-%d"),
            })
        self.db.upsert_etf_basic(rows)
        return self.db.get_etf_basic()

    # ------------------------------------------------------------------
    # ETF components -> theme mapping
    # ------------------------------------------------------------------
    def update_theme_mapping(self, etf_theme_map: Dict[str, str]) -> Dict[str, List[str]]:
        """Build theme -> [stock_codes] mapping.

        Priority:
          1. theme_stock_map_latest.json (authoritative, per project rule)
          2. Tushare fund_portfolio (if json missing)
        """
        json_path = self.config.get("data", {}).get("theme_map_json", "")
        theme_stocks = self._load_theme_json(json_path)
        if theme_stocks:
            # Filter to themes that appear in our ETF universe
            wanted_themes = set(etf_theme_map.values())
            filtered: Dict[str, List[str]] = {}
            for theme, stocks in theme_stocks.items():
                if theme in wanted_themes:
                    filtered[theme] = stocks[:80]
                else:
                    # fuzzy match
                    for w in wanted_themes:
                        if w in theme or theme in w:
                            filtered.setdefault(w, [])
                            filtered[w].extend(stocks[:80])
            # dedup
            for k in filtered:
                seen = set()
                filtered[k] = [s for s in filtered[k] if not (s in seen or seen.add(s))]
            self.db.upsert_theme_mapping(filtered)
            LOG.info("theme mapping from JSON: %d themes", len(filtered))
            return filtered

        # Fallback: Tushare fund_portfolio
        if self._init_pro():
            mapping: Dict[str, List[str]] = {}
            for code, theme in etf_theme_map.items():
                try:
                    self._throttle()
                    df = self._pro.fund_portfolio(ts_code=code)
                    if df is not None and not df.empty:
                        col = "stock_symbol" if "stock_symbol" in df.columns else (
                            "symbol" if "symbol" in df.columns else None)
                        if col is None:
                            continue
                        stocks = sorted(set(df[col].tolist()))[:50]
                        mapping[theme] = [self._normalize_code(s) for s in stocks]
                except Exception as e:
                    LOG.warning("fund_portfolio %s failed: %s", code, e)
            if mapping:
                self.db.upsert_theme_mapping(mapping)
            return mapping
        return {}

    def fill_missing_with_etf_components(self, etf_theme_map: Dict[str, str],
                                          theme_stocks: Dict[str, List[str]]
                                          ) -> Dict[str, List[str]]:
        """For ETFs whose theme has no stock pool, fetch ETF constituents
        from Tushare fund_portfolio and use them as the theme stock pool.

        Returns the updated theme_stocks dict (mutates in place also).
        """
        # identify ETFs whose theme is missing from theme_stocks
        existing_themes = set(theme_stocks.keys())
        missing: Dict[str, str] = {}  # theme -> etf_code (first ETF with this theme)
        for etf_code, theme in etf_theme_map.items():
            if theme not in existing_themes and theme not in missing:
                missing[theme] = etf_code
        if not missing:
            return theme_stocks
        if not self._init_pro():
            LOG.warning("Tushare not available, cannot fill %d missing themes", len(missing))
            return theme_stocks
        LOG.info("filling %d missing themes via ETF constituents: %s",
                 len(missing), list(missing.keys()))
        filled = 0
        for theme, etf_code in missing.items():
            try:
                self._throttle()
                df = self._pro.fund_portfolio(ts_code=etf_code)
                if df is not None and not df.empty:
                    col = "stock_symbol" if "stock_symbol" in df.columns else (
                        "symbol" if "symbol" in df.columns else None)
                    if col is None:
                        continue
                    stocks = sorted(set(df[col].dropna().tolist()))
                    stocks = [self._normalize_code(str(s)) for s in stocks if str(s).strip()]
                    stocks = [s for s in stocks if len(s) >= 8 and s[-3] == "."
                              and s[-2:] in ("SH", "SZ")
                              and len(s.split(".")[0]) == 6]
                    stocks = stocks[:60]
                    if stocks:
                        theme_stocks[theme] = stocks
                        filled += 1
            except Exception as e:
                LOG.warning("fund_portfolio %s (%s) failed: %s", etf_code, theme, e)
        if filled:
            self.db.upsert_theme_mapping(theme_stocks)
            LOG.info("filled %d missing themes with ETF constituents", filled)
        return theme_stocks

    @staticmethod
    def _load_theme_json(path: str) -> Dict[str, List[str]]:
        if not path or not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            themes = data.get("themes", data)
            out: Dict[str, List[str]] = {}
            for theme, items in themes.items():
                codes = []
                for it in items:
                    if isinstance(it, dict) and "code" in it:
                        codes.append(it["code"])
                    elif isinstance(it, str):
                        codes.append(it)
                if codes:
                    out[theme] = codes
            return out
        except Exception as e:
            LOG.warning("theme json load failed %s: %s", path, e)
            return {}

    @staticmethod
    def _normalize_code(symbol: str) -> str:
        symbol = str(symbol).strip()
        if "." in symbol:
            # already has market suffix, just normalize
            parts = symbol.rsplit(".", 1)
            code = parts[0].zfill(6)
            market = parts[1].upper()
            if market in ("SH", "SZ"):
                return f"{code}.{market}"
            # unknown market, guess from code
            symbol = code
        symbol = symbol.zfill(6)
        if symbol.startswith(("60", "68", "9")):
            return f"{symbol}.SH"
        return f"{symbol}.SZ"

    # ------------------------------------------------------------------
    # Optional: ETF share size (for capital consistency feature)
    # ------------------------------------------------------------------
    def get_etf_share_size(self, etf_code: str,
                           start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        if not self.enable_share or not self._init_pro():
            return None
        try:
            self._throttle()
            df = self._pro.etf_share_size(ts_code=etf_code,
                                          start_date=start_date, end_date=end_date)
            return df
        except Exception as e:
            LOG.warning("etf_share_size %s failed: %s", etf_code, e)
            return None
