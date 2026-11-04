# -*- coding: utf-8 -*-
"""市场微结构日序列（V10.7 增量优化）

=========================================
为什么需要这个模块：
  V10.0 的快照（MarketSnapshot）只保存**当日**截面值，
  判定「卖压是否连续衰减」「广度是否持续改善」这类**变化量**条件时，
  必须能取到最近 N 个交易日的连续序列：
    · 跌停家数 dt_count       —— S4 卖压衰减确认
    · 下跌家数 down_count     —— S4 卖压衰减确认
    · 上涨占比 up_ratio       —— S4/S5 确认
    · 涨停家数 zt_count       —— 涨停/跌停比
    · 全市场成交额 amount_yi  —— 量价四要素
    · 核心指数涨跌幅 index_chg —— 量价四要素

  原实现每次都逐日扫 daily_cache（实测单日 562 次 SQL），
  全年回放直接超时。本模块：
    1. 用**一条**聚合 SQL 按 trade_date 分组统计（不含代码前缀的部分）
    2. 代码前缀相关的涨跌停统计分块拉取后用 pandas 算
    3. 结果落 sqlite 增量缓存 → 第二次起近乎零成本

★ 口径与 V10.0 完全一致（避免新旧系统打架）：
    广度     = 上涨家数 / (上涨家数 + 下跌家数)，平盘单列
    涨跌停   = 收盘价判定，主板 10% / 双创与北交所 20%
              （复用 market_regime_v10._limit_thresholds，不另立标准）
    成交额   = 全市场 daily_cache amount 求和（千元 → 亿元）
    指数     = 中证2000(932000.CSI)，与 V10.0 的 CORE_INDEX 相同

★ 不得使用未来数据：所有接口都按 trade_date **截断**，
  序列只包含 trade_date 当日及之前的数据。
"""
import os
import sys
import time
import sqlite3
import datetime
from typing import Optional, List, Dict, Any

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

CACHE_DIR = r"D:\mystock\cache_daily"
STOCK_DB = os.path.join(CACHE_DIR, "stock_data.db")
MICRO_DB = os.path.join(BASE_DIR, "cache_micro", "micro_daily.db")

# 核心指数（与 V10.0 一致：中证2000 = 短线小盘代表）
CORE_CODE = "932000.CSI"

SCHEMA = """
CREATE TABLE IF NOT EXISTS micro_daily (
    trade_date   TEXT PRIMARY KEY,
    up_count     INTEGER,
    down_count   INTEGER,
    flat_count   INTEGER,
    total        INTEGER,
    zt_count     INTEGER,
    dt_count     INTEGER,
    amount_yi    REAL,
    index_chg    REAL,
    updated_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_micro_date ON micro_daily(trade_date);
"""


def _ro_conn():
    if not os.path.exists(STOCK_DB):
        return None
    try:
        return sqlite3.connect(f"file:{STOCK_DB.replace(chr(92), '/')}?mode=ro",
                               uri=True, timeout=30)
    except Exception:
        return None


def _write_conn():
    os.makedirs(os.path.dirname(MICRO_DB), exist_ok=True)
    conn = sqlite3.connect(MICRO_DB, timeout=60)
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# ============================================================
# 涨跌停统计（复用 V10.0 的阈值口径，避免两套标准）
# ============================================================
def _limit_thresholds(ts_code: str) -> float:
    """与 market_regime_v10._limit_thresholds 完全一致。

    主板 10%；双创（688/300/301）与北交所（8xx/92x）20%。
    ST 股 5% 无法从 daily_cache 识别 → 口径限制随结果返回。
    """
    code = str(ts_code)
    if code.startswith(('688', '300', '301', '8', '92')):
        return 20.0
    return 10.0


def _chunk(seq: List[str], size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


# ============================================================
# 构建
# ============================================================
def build(start_date: str = None, end_date: str = None,
          progress: bool = False, force: bool = False) -> int:
    """构建/增量补齐微结构日序列。返回新增或更新的天数。

    ★ 只写入 **end_date 当日及之前** 的数据 —— 保证不使用未来数据。
    """
    end_date = str(end_date or datetime.date.today().strftime('%Y%m%d'))
    start_date = str(start_date or '20210101')

    conn = _ro_conn()
    if conn is None:
        return 0

    wconn = _write_conn()
    try:
        have = set()
        if not force:
            rows = wconn.execute(
                "SELECT trade_date FROM micro_daily WHERE trade_date BETWEEN ? AND ?",
                (start_date, end_date)).fetchall()
            have = {r[0] for r in rows}

        # ---- 1) 已有日期清单 ----
        dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM daily_cache "
            "WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",
            (start_date, end_date)).fetchall()]
        if not dates:
            return 0
        todo = [d for d in dates if d not in have]
        if not todo:
            return 0

        # ---- 2) 分块拉取明细，算涨跌停 ----
        records: Dict[str, Dict[str, Any]] = {}
        CHUNK = 40
        for group in _chunk(todo, CHUNK):
            lo, hi = group[0], group[-1]
            df = pd.read_sql_query(
                "SELECT trade_date, ts_code, pct_chg, amount FROM daily_cache "
                "WHERE trade_date BETWEEN ? AND ?", conn, params=(lo, hi))
            if df.empty:
                continue
            df = df.dropna(subset=['pct_chg'])
            thr = df['ts_code'].map(_limit_thresholds).astype(float)
            pct = df['pct_chg'].astype(float)
            df = df.assign(_zt=((pct >= thr * 0.99) & (pct < thr + 0.1)).astype(int),
                           _dt=((pct <= -thr * 0.99) & (pct > -thr - 0.1)).astype(int),
                           _up=(pct > 0).astype(int),
                           _dn=(pct < 0).astype(int),
                           _fl=(pct == 0).astype(int))
            g = df.groupby('trade_date').agg(
                up_count=('_up', 'sum'), down_count=('_dn', 'sum'),
                flat_count=('_fl', 'sum'), total=('pct_chg', 'size'),
                zt_count=('_zt', 'sum'), dt_count=('_dt', 'sum'),
                amount_k=('amount', 'sum')).reset_index()
            for r in g.itertuples(index=False):
                records[str(r.trade_date)] = {
                    "up_count": int(r.up_count), "down_count": int(r.down_count),
                    "flat_count": int(r.flat_count), "total": int(r.total),
                    "zt_count": int(r.zt_count), "dt_count": int(r.dt_count),
                    "amount_yi": round(float(r.amount_k) / 100000.0, 4),
                    "index_chg": None,
                }
            if progress:
                print(f"  微结构已算到 {hi}（{len(records)} 天）")

        # ---- 3) 核心指数涨跌幅 ----
        try:
            idx = pd.read_sql_query(
                "SELECT trade_date, pct_chg FROM index_daily_cache "
                "WHERE ts_code = ? AND trade_date BETWEEN ? AND ?",
                conn, params=(CORE_CODE, start_date, end_date))
            for r in idx.itertuples(index=False):
                d = str(r.trade_date)
                if d in records and r.pct_chg is not None:
                    records[d]["index_chg"] = round(float(r.pct_chg), 4)
        except Exception:
            pass

        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        rows = [(d, v["up_count"], v["down_count"], v["flat_count"], v["total"],
                 v["zt_count"], v["dt_count"], v["amount_yi"], v["index_chg"], now)
                for d, v in records.items()]
        if rows:
            wconn.executemany(
                "INSERT OR REPLACE INTO micro_daily "
                "(trade_date, up_count, down_count, flat_count, total, "
                " zt_count, dt_count, amount_yi, index_chg, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
            wconn.commit()
        return len(rows)
    finally:
        try:
            conn.close()
        except Exception:
            pass
        try:
            wconn.close()
        except Exception:
            pass


def ensure(start_date: str = None, end_date: str = None) -> bool:
    """确保序列覆盖到 end_date（缺口自动补齐）。返回是否补齐成功。"""
    end_date = str(end_date or datetime.date.today().strftime('%Y%m%d'))
    build(start_date, end_date)
    # build 提交后，RO 连接偶发瞬时不可见（同进程写锁竞态），小重试吸收，
    # 避免误报「未就绪」导致上层判定 UNKNOWN。
    for _ in range(3):
        if get_row(end_date) is not None:
            return True
        time.sleep(0.1)
    return False


# ============================================================
# 读取
# ============================================================
def get_row(trade_date: str) -> Optional[Dict[str, Any]]:
    """取单日微结构数据。"""
    if not os.path.exists(MICRO_DB):
        return None
    try:
        conn = sqlite3.connect(f"file:{MICRO_DB.replace(chr(92), '/')}?mode=ro",
                               uri=True, timeout=20)
    except Exception:
        return None
    try:
        cur = conn.execute(
            "SELECT trade_date, up_count, down_count, flat_count, total, "
            "zt_count, dt_count, amount_yi, index_chg "
            "FROM micro_daily WHERE trade_date = ?", (str(trade_date),))
        r = cur.fetchone()
        if not r:
            return None
        return _row_to_dict(r)
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _row_to_dict(r) -> Dict[str, Any]:
    up, dn = int(r[1]), int(r[2])
    denom = up + dn
    return {
        "trade_date": r[0],
        "up_count": up, "down_count": dn, "flat_count": int(r[3]),
        "total": int(r[4]),
        "zt_count": int(r[5]), "dt_count": int(r[6]),
        "amount_yi": r[7],
        "index_chg": r[8],
        "up_ratio": (up / denom) if denom else None,
        "zt_dt_ratio": (int(r[5]) / int(r[6])) if r[6] else (
            float('inf') if r[5] else None),
    }


def load_series(end_date: str, days: int = 30) -> List[Dict[str, Any]]:
    """取截至 end_date（含）的最近 days 天序列，升序。

    ★ 严格截断到 end_date —— 不含任何未来数据。
    """
    if not os.path.exists(MICRO_DB):
        return []
    try:
        conn = sqlite3.connect(f"file:{MICRO_DB.replace(chr(92), '/')}?mode=ro",
                               uri=True, timeout=20)
    except Exception:
        return []
    try:
        rows = conn.execute(
            "SELECT trade_date, up_count, down_count, flat_count, total, "
            "zt_count, dt_count, amount_yi, index_chg FROM micro_daily "
            "WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT ?",
            (str(end_date), int(days))).fetchall()
        return [_row_to_dict(r) for r in reversed(rows)]
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def series_value(series: List[Dict[str, Any]], key: str,
                 offset: int = 0) -> Optional[float]:
    """取序列中倒数第 offset+1 个元素的某字段（offset=0 为最新日）。"""
    idx = -(offset + 1)
    if len(series) <= abs(idx):
        return None
    v = series[idx].get(key)
    return None if v is None else float(v)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="市场微结构日序列")
    ap.add_argument("start", nargs="?", default="20210101")
    ap.add_argument("end", nargs="?", default=None)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    n = build(args.start, args.end, progress=True, force=args.force)
    print(f"新增/更新 {n} 天")
    s = load_series(args.end or datetime.date.today().strftime('%Y%m%d'), 5)
    for r in s:
        print(r)
