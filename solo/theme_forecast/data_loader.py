# -*- coding: utf-8 -*-
"""
数据加载层

复用现有缓存接口：
- K线：优先读 d:\\mystock\\cache_daily\\{code}.csv（tushare_quant 已生成的本地缓存）
- 全市场日数据：DataFetcher.get_daily(trade_date)
- 资金流：DataFetcher.get_moneyflow(trade_date)
- 北向：DataFetcher.get_north_hold(trade_date)
- 涨跌停：DataFetcher.get_limit_list_d(trade_date)
- 指数：DataFetcher.get_index_daily(ts_code, start, end)
- ETF份额：pro.fund_share(ts_code) / pro.fund_daily(ts_code, start, end)
"""
import os
import sys
import json
import time
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 本地K线CSV缓存目录
KLINE_CSV_DIR = Path(r"d:\mystock\cache_daily")
# 主题映射
THEME_MAP_PATH = KLINE_CSV_DIR / "theme_stock_map_latest.json"
# SQLite缓存（复用 theme_trend_sentiment_score 的 DB）
DB_PATH = Path(r"d:\mystock\cache_daily\stock_data.db")


# ====================================================================
# Tushare / DataFetcher 初始化
# ====================================================================
def _init_tushare():
    """初始化 tushare pro 接口"""
    import tushare as ts
    from dotenv import load_dotenv
    load_dotenv("d:/mystock/config/.env")
    token = os.getenv("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN 未设置")
    return ts.pro_api(token)


def _init_datafetcher():
    """初始化 DataFetcher"""
    from multi_factor_picker.data_fetcher import DataFetcher
    from dotenv import load_dotenv
    load_dotenv("d:/mystock/config/.env")
    token = os.getenv("TUSHARE_TOKEN")
    config = {"cache": {"enabled": True, "dir": "cache", "expire_hours": 168}}
    return DataFetcher(token, config)


_pro = None
_df = None


def get_pro():
    global _pro
    if _pro is None:
        _pro = _init_tushare()
    return _pro


def get_df():
    global _df
    if _df is None:
        _df = _init_datafetcher()
    return _df


# ====================================================================
# 交易日工具
# ====================================================================
def get_last_trade_date() -> str:
    """获取最近交易日"""
    df = get_df()
    return df.get_last_trade_date()


def get_trade_dates(end_date: str, n_days: int = 70) -> list:
    """获取 end_date 前 n_days 个自然日内的交易日列表"""
    start = (datetime.strptime(end_date, "%Y%m%d") - timedelta(days=n_days)).strftime("%Y%m%d")
    df = get_df()
    cal = df.get_trade_cal(start_date=start, end_date=end_date)
    if cal is not None and not cal.empty:
        return sorted(cal[cal["is_open"] == "1"]["cal_date"].tolist())
    # 降级：简单推算
    return [end_date]


# ====================================================================
# SQLite 缓存（DataFrame，按交易日隔离）
# ====================================================================
def cache_get(name: str, trade_date: str = None, **kwargs) -> pd.DataFrame:
    """从 SQLite 读取缓存 DataFrame"""
    td = trade_date or get_last_trade_date()
    key = "_".join([name] + [f"{k}_{v}" for k, v in sorted(kwargs.items())])
    safe = key.replace("/", "_").replace(":", "_")
    cache_key = f"tf_{safe}_{td}"

    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(str(DB_PATH))
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT data, expire_time FROM cache_data WHERE key = ?", (cache_key,))
        row = cursor.fetchone()
        if row:
            data_str, expire_time = row
            if expire_time and expire_time > 0 and int(time.time()) > expire_time:
                cursor.execute("DELETE FROM cache_data WHERE key = ?", (cache_key,))
                conn.commit()
                return None
            from io import StringIO
            return pd.read_csv(StringIO(data_str))
    except Exception:
        pass
    finally:
        conn.close()
    return None


def cache_set(name: str, data: pd.DataFrame, expire_hours: int = 24, trade_date: str = None, **kwargs):
    """写入 SQLite 缓存 DataFrame"""
    td = trade_date or get_last_trade_date()
    key = "_".join([name] + [f"{k}_{v}" for k, v in sorted(kwargs.items())])
    safe = key.replace("/", "_").replace(":", "_")
    cache_key = f"tf_{safe}_{td}"

    expire_time = int(time.time()) + expire_hours * 3600 if expire_hours and expire_hours > 0 else 0
    from io import StringIO
    buffer = StringIO()
    data.to_csv(buffer, index=False)
    data_str = buffer.getvalue()

    conn = sqlite3.connect(str(DB_PATH))
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO cache_data (key, data, expire_time, created_at)
            VALUES (?, ?, ?, ?)
        """, (cache_key, data_str, expire_time, int(time.time())))
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


# ====================================================================
# 主题成份股加载
# ====================================================================
def load_theme_stocks() -> dict:
    """
    从 theme_stock_map_latest.json 加载主题→成份股映射

    Returns:
        {theme_name: [{"code","name","via","score",...}, ...]}
    """
    if not THEME_MAP_PATH.exists():
        raise FileNotFoundError(f"主题映射文件不存在: {THEME_MAP_PATH}")
    with open(THEME_MAP_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("themes", {})


# ====================================================================
# K线数据加载（优先本地CSV，降级API）
# ====================================================================
def load_klines(ts_codes: list, start_date: str, end_date: str) -> dict:
    """
    批量加载K线数据，优先读本地CSV缓存

    Returns:
        {ts_code: DataFrame(trade_date, open, high, low, close, vol, amount, pct_chg, turnover_rate)}
    """
    result = {}
    need_fetch = []

    for code in ts_codes:
        csv_path = KLINE_CSV_DIR / f"{code}.csv"
        if csv_path.exists():
            try:
                df = pd.read_csv(csv_path)
                if not df.empty:
                    df["trade_date"] = df["trade_date"].astype(str)
                    df = df[(df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)].copy()
                    if not df.empty:
                        result[code] = df
                        continue
            except Exception:
                pass
        need_fetch.append(code)

    # API拉取剩余
    if need_fetch:
        df_inst = get_df()
        for code in need_fetch:
            try:
                # DataFetcher.get_daily_by_code 带缓存
                if hasattr(df_inst, "get_daily_by_code"):
                    code_df = df_inst.get_daily_by_code(ts_code=code, start_date=start_date, end_date=end_date)
                else:
                    pro = get_pro()
                    code_df = pro.daily(ts_code=code, start_date=start_date, end_date=end_date)
                if code_df is not None and not code_df.empty:
                    code_df["trade_date"] = code_df["trade_date"].astype(str)
                    result[code] = code_df
                time.sleep(0.13)
            except Exception:
                time.sleep(0.13)

    return result


def load_index_daily(ts_code: str = "000001.SH", n_days: int = 70, end_date: str = None) -> pd.DataFrame:
    """加载大盘指数日K"""
    end = end_date or get_last_trade_date()
    start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=n_days)).strftime("%Y%m%d")
    df_inst = get_df()
    return df_inst.get_index_daily(ts_code=ts_code, start_date=start, end_date=end)


# ====================================================================
# 全市场截面数据（资金流/北向/涨跌停）
# ====================================================================
def load_daily_moneyflow(trade_date: str) -> pd.DataFrame:
    """全市场资金流（主力净流入）"""
    cached = cache_get("market_moneyflow", trade_date=trade_date)
    if cached is not None:
        return cached
    df_inst = get_df()
    df = df_inst.get_moneyflow(trade_date)
    if df is not None and not df.empty:
        cache_set("market_moneyflow", df, expire_hours=24, trade_date=trade_date)
    return df


def load_north_hold(trade_date: str) -> pd.DataFrame:
    """北向资金持股"""
    cached = cache_get("north_hold", trade_date=trade_date)
    if cached is not None:
        return cached
    df_inst = get_df()
    df = df_inst.get_north_hold(trade_date)
    if df is not None and not df.empty:
        cache_set("north_hold", df, expire_hours=24, trade_date=trade_date)
    return df


def load_limit_list(trade_date: str) -> pd.DataFrame:
    """涨跌停列表"""
    cached = cache_get("limit_list_d", trade_date=trade_date)
    if cached is not None:
        return cached
    df_inst = get_df()
    df = df_inst.get_limit_list_d(trade_date)
    if df is not None and not df.empty:
        cache_set("limit_list_d", df, expire_hours=24, trade_date=trade_date)
    return df


def load_limit_step(trade_date: str) -> pd.DataFrame:
    """炸板信息"""
    cached = cache_get("limit_step", trade_date=trade_date)
    if cached is not None:
        return cached
    df_inst = get_df()
    df = df_inst.get_limit_step(trade_date)
    if df is not None and not df.empty:
        cache_set("limit_step", df, expire_hours=24, trade_date=trade_date)
    return df


def load_daily_basic(trade_date: str) -> pd.DataFrame:
    """全市场daily_basic（换手率、市值等）"""
    cached = cache_get("daily_basic", trade_date=trade_date)
    if cached is not None:
        return cached
    df_inst = get_df()
    df = df_inst.get_daily_basic(trade_date)
    if df is not None and not df.empty:
        cache_set("daily_basic", df, expire_hours=24, trade_date=trade_date)
    return df


# ====================================================================
# ETF 数据
# ====================================================================
def load_etf_share(ts_code: str, n_days: int = 30) -> pd.DataFrame:
    """
    ETF份额变动数据

    Returns:
        DataFrame(trade_date, total_share)
    """
    end = get_last_trade_date()
    start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=n_days)).strftime("%Y%m%d")

    cached = cache_get("etf_share", ts_code=ts_code)
    if cached is not None:
        return cached

    pro = get_pro()
    try:
        df = pro.fund_share(ts_code=ts_code, start_date=start, end_date=end)
        if df is not None and not df.empty:
            cache_set("etf_share", df, expire_hours=24, ts_code=ts_code)
            return df
    except Exception:
        pass
    return pd.DataFrame()


def load_etf_daily(ts_code: str, n_days: int = 70) -> pd.DataFrame:
    """ETF日线行情"""
    end = get_last_trade_date()
    start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=n_days)).strftime("%Y%m%d")

    cached = cache_get("etf_daily", ts_code=ts_code)
    if cached is not None:
        return cached

    df_inst = get_df()
    df = df_inst.get_fund_daily(ts_code=ts_code, start_date=start, end_date=end)
    if df is not None and not df.empty:
        cache_set("etf_daily", df, expire_hours=24, ts_code=ts_code)
        return df
    return pd.DataFrame()
