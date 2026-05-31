# -*- coding: utf-8 -*-
"""行情数据：Tushare 盘后 + 通达信盘中"""
import os
import time
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd
import tushare as ts

from .config import TUSHARE_TOKEN, TDX_SERVERS


pro = ts.pro_api(TUSHARE_TOKEN) if TUSHARE_TOKEN else None

try:
    from pytdx.hq import TdxHq_API
    TDX_AVAILABLE = True
except ImportError:
    TDX_AVAILABLE = False


def get_last_trade_date() -> str:
    now = datetime.now()
    query_date = (
        (now - timedelta(days=1)).strftime("%Y%m%d")
        if now.hour < 15
        else now.strftime("%Y%m%d")
    )
    cal = pro.trade_cal(exchange="", start_date="20200101", end_date=query_date)
    cal = cal[cal["is_open"] == 1]
    return str(cal[cal["cal_date"] <= query_date]["cal_date"].max())


def fetch_daily(trade_date: str, ts_codes: List[str]) -> pd.DataFrame:
    if not ts_codes:
        return pd.DataFrame()
    all_dfs = []
    for i in range(0, len(ts_codes), 100):
        batch = ts_codes[i : i + 100]
        try:
            df = pro.daily(trade_date=trade_date, ts_code=",".join(batch))
            if df is not None and not df.empty:
                all_dfs.append(df)
            time.sleep(0.15)
        except Exception:
            pass
    if all_dfs:
        return pd.concat(all_dfs, ignore_index=True)
    return pd.DataFrame()


def fetch_daily_basic(trade_date: str, ts_codes: List[str]) -> pd.DataFrame:
    try:
        df = pro.daily_basic(
            trade_date=trade_date,
            fields="ts_code,turnover_rate,total_mv,circ_mv",
        )
        if df is not None and not df.empty:
            return df[df["ts_code"].isin(ts_codes)]
    except Exception:
        pass
    return pd.DataFrame()


def fetch_limit_step(trade_date: str) -> Dict[str, int]:
    try:
        df = pro.limit_step(trade_date=trade_date)
        if df is None or df.empty:
            return {}
        return dict(zip(df["ts_code"], df["nums"].fillna(1).astype(int)))
    except Exception:
        return {}


def fetch_limit_list(trade_date: str) -> pd.DataFrame:
    try:
        return pro.limit_list_ths(trade_date=trade_date, limit_type="涨停池")
    except Exception:
        return pd.DataFrame()


def ts_code_to_tdx(ts_code: str) -> Tuple[int, str]:
    """600000.SH -> (1, '600000'), 000001.SZ -> (0, '000001')"""
    code, suffix = ts_code.split(".")
    market = 1 if suffix == "SH" else 0
    return market, code


def tdx_to_ts_code(market: int, code: str) -> str:
    suffix = "SH" if market == 1 else "SZ"
    return f"{code}.{suffix}"


class TdxQuoteClient:
    """通达信实时行情（复用 realtime_ma_monitor 逻辑）"""

    def __init__(self):
        self.api = TdxHq_API() if TDX_AVAILABLE else None
        self.connected = False
        self.best_server = None

    def find_fastest_server(self):
        if not TDX_AVAILABLE:
            return
        results = []

        def _test(host, port, res):
            try:
                api = TdxHq_API()
                start = time.time()
                if not api.connect(host, port, time_out=3):
                    return
                latency = (time.time() - start) * 1000
                for market, code in [(0, "000001"), (1, "600000")]:
                    if api.get_security_bars(9, market, code, 0, 5):
                        res.append((host, port, latency))
                        break
            except Exception:
                pass
            finally:
                try:
                    api.disconnect()
                except Exception:
                    pass

        threads = []
        for host, port in TDX_SERVERS:
            t = threading.Thread(target=_test, args=(host, port, results))
            threads.append(t)
            t.start()
        for t in threads:
            t.join(timeout=5)

        if results:
            results.sort(key=lambda x: x[2])
            self.best_server = (results[0][0], results[0][1])
        else:
            self.best_server = TDX_SERVERS[0]

    def connect(self) -> bool:
        if not TDX_AVAILABLE:
            return False
        if self.connected:
            return True
        if self.best_server is None:
            self.find_fastest_server()
        host, port = self.best_server
        self.connected = self.api.connect(host, port)
        return self.connected

    def disconnect(self):
        if self.api and self.connected:
            self.api.disconnect()
            self.connected = False

    def get_quotes(self, stock_list: List[Tuple[int, str]]) -> List[Dict]:
        if not self.connect():
            return []
        try:
            raw = self.api.get_security_quotes(stock_list)
            if not raw:
                return []
            quotes = []
            for q in raw:
                code = q.get("code", "")
                price = float(q.get("price", 0) or 0)
                last_close = float(q.get("last_close", 0) or 0)
                vol = float(q.get("vol", 0) or 0)
                pct = (price - last_close) / last_close * 100 if last_close else 0
                quotes.append({
                    "code": code,
                    "market": q.get("market", 0),
                    "price": price,
                    "last_close": last_close,
                    "pct_chg": round(pct, 2),
                    "vol": vol,
                    "bid1": float(q.get("bid1", 0) or 0),
                    "ask1": float(q.get("ask1", 0) or 0),
                })
            return quotes
        except Exception:
            self.connected = False
            return []

    def get_bars(self, market: int, code: str, count: int = 30) -> List[Dict]:
        if not self.connect():
            return []
        try:
            bars = self.api.get_security_bars(9, market, code, 0, count)
            return bars or []
        except Exception:
            return []


def is_trading_time(now: Optional[datetime] = None) -> bool:
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.hour * 100 + now.minute
    return (930 <= t <= 1130) or (1300 <= t <= 1500)


def is_early_session(now: Optional[datetime] = None) -> bool:
    now = now or datetime.now()
    return now.hour < 10 or (now.hour == 10 and now.minute <= 30)
