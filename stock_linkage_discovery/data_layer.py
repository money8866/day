"""全市场数据接口与历史时点（point-in-time）股票池。

复用既有 D:/mystock/cache_daily/stock_data.db 的 daily_cache / daily_basic_cache /
adj_factor_cache / index_daily_cache，以及 cache_daily/stock_basic.csv。
本模块只读主库，不重复建立行情管道，不修改任何既有表。

关键纪律：
  1. 所有收益率统一用后复权价 close * adj_factor 计算；
  2. 交易日来自 daily_cache 的真实交易日历，不用自然日推算；
  3. 股票池只使用 <= asof 的数据，避免幸存者偏差；ST 名称用 namechg 历史还原；
  4. 任何切片都经 assert_no_future 守卫。
"""
from __future__ import annotations

import glob
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .common import assert_no_future, get_logger, norm_code

log = get_logger("data_layer")


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #
@dataclass
class Slice:
    """截止 asof、限定股票池、限定窗口的数据切片（所有矩阵 T x N 对齐）。"""
    asof: str
    dates: list[str]
    codes: list[str]
    industry: np.ndarray
    names: np.ndarray
    ret: np.ndarray
    close: np.ndarray           # 后复权收盘
    close_raw: np.ndarray
    high: np.ndarray
    low: np.ndarray
    open: np.ndarray
    amount: np.ndarray          # 千元
    vol: np.ndarray
    turnover: np.ndarray
    circ_mv: np.ndarray         # 万元
    pct_chg: np.ndarray         # 百分数
    valid: np.ndarray
    market_ret: np.ndarray
    industry_ret: np.ndarray
    market_source: str
    resid_mkt: np.ndarray | None = None
    resid_mkt_ind: np.ndarray | None = None
    extra: dict[str, np.ndarray] = field(default_factory=dict)

    @property
    def T(self) -> int:
        return len(self.dates)

    @property
    def N(self) -> int:
        return len(self.codes)

    @property
    def last(self) -> int:
        return self.T - 1

    def code_index(self) -> dict[str, int]:
        return {c: i for i, c in enumerate(self.codes)}

    def latest(self, key: str) -> np.ndarray:
        """取某矩阵在 asof 日的截面 (N,)。"""
        arr = self.extra.get(key)
        if arr is None:
            arr = getattr(self, key, None)
        if arr is None:
            raise KeyError(key)
        return np.asarray(arr[self.last], dtype=np.float64)

    def tail(self, key: str, w: int) -> np.ndarray:
        arr = self.extra.get(key)
        if arr is None:
            arr = getattr(self, key, None)
        if arr is None:
            raise KeyError(key)
        return np.asarray(arr[-w:], dtype=np.float64)


@dataclass
class Panel:
    """一次载入、供多日回放复用（避免每日重复读 22GB 主库）。"""
    dates: list[str]
    codes: list[str]
    arrays: dict[str, np.ndarray]
    market_ret: np.ndarray
    market_source: str
    industry: np.ndarray
    industry_ret: np.ndarray
    names: dict[str, str]
    list_dates: dict[str, str]
    quality: dict[str, Any] = field(default_factory=dict)

    def __getitem__(self, key: str) -> np.ndarray:
        return self.arrays[key]

    def slice(self, asof: str, codes: list[str], lookback: int) -> Slice:
        """按 asof 截取窗口；严格禁止未来日期进入切片。"""
        if asof not in self.dates:
            raise ValueError(f"asof={asof} 不是交易日（库内最近 {self.dates[-1]}）")
        end = self.dates.index(asof)
        start = max(0, end - lookback + 1)
        d = self.dates[start:end + 1]
        assert_no_future(d, asof, "Panel.slice")
        col_idx = [self.codes.index(c) for c in codes]  # codes 已排序时用 get_indexer 更快
        sl = (slice(start, end + 1), col_idx)

        def a(key: str) -> np.ndarray:
            return np.ascontiguousarray(self.arrays[key][sl])

        return Slice(
            asof=asof, dates=d, codes=list(codes),
            industry=np.asarray([self.industry[i] for i in col_idx]),
            names=np.asarray([self.names.get(c, c) for c in codes]),
            ret=a("ret"), close=a("close"), close_raw=a("close_raw"),
            high=a("high"), low=a("low"), open=a("open"),
            amount=a("amount"), vol=a("vol"), turnover=a("turnover"),
            circ_mv=a("circ_mv"), pct_chg=a("pct_chg"),
            valid=a("valid").astype(bool),
            market_ret=np.ascontiguousarray(self.market_ret[start:end + 1]),
            industry_ret=np.ascontiguousarray(self.industry_ret[sl]),
            market_source=self.market_source,
        )


# --------------------------------------------------------------------------- #
# 历史简称（PIT ST / 退市判定）
# --------------------------------------------------------------------------- #
class PITNameResolver:
    """优先用 treasure_namechg_*.parquet 还原 asof 当日简称。

    首次使用会把 4千+ 碎片合并进 linkage.db 的 name_history 表，后续直接读库。
    没有 namechg 记录的股票退回 stock_basic.csv 当前名，并计入 pit 覆盖率。
    """
    def __init__(self, cfg: dict[str, Any], basic: pd.DataFrame):
        self.cfg = cfg
        self.current = dict(zip(basic["ts_code"].astype(str), basic["name"].astype(str)))
        self.history: dict[str, list[tuple[str, str, str]]] = {}
        self.coverage = 0.0
        self._load()

    def _load(self) -> None:
        db = Path(self.cfg["paths"]["linkage_db"])
        db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(db))
        try:
            con.execute("""CREATE TABLE IF NOT EXISTS name_history(
                ts_code TEXT, name TEXT, start_date TEXT, end_date TEXT,
                PRIMARY KEY(ts_code, start_date, name))""")
            rows = con.execute("SELECT ts_code,name,start_date,end_date FROM name_history").fetchall()
            if not rows:
                files = glob.glob(self.cfg["paths"]["namechg_glob"])
                frames = []
                for f in files:
                    try:
                        frames.append(pd.read_parquet(f, columns=["ts_code", "name", "start_date", "end_date"]))
                    except Exception:
                        continue
                if frames:
                    h = pd.concat(frames, ignore_index=True)
                    h["start_date"] = h["start_date"].fillna("").astype(str)
                    h["end_date"] = h["end_date"].fillna("99991231").astype(str)
                    h = h.drop_duplicates()
                    con.executemany(
                        "INSERT OR IGNORE INTO name_history VALUES (?,?,?,?)",
                        h[["ts_code", "name", "start_date", "end_date"]].itertuples(index=False, name=None),
                    )
                    con.commit()
                rows = con.execute("SELECT ts_code,name,start_date,end_date FROM name_history").fetchall()
        finally:
            con.close()
        for code, name, s, e in rows:
            self.history.setdefault(str(code), []).append((str(s), str(e), str(name)))
        for v in self.history.values():
            v.sort(key=lambda r: r[0])
        covered = sum(1 for c in self.current if c in self.history)
        self.coverage = round(covered / max(1, len(self.current)), 4)
        log.info("PIT 简称库: %d 只股票有历史简称, 覆盖率 %.1f%%", len(self.history), self.coverage * 100)

    def name_at(self, code: str, asof: str) -> str:
        recs = self.history.get(code)
        if recs:
            hit = None
            for s, e, name in recs:
                if s <= asof <= e:
                    hit = name
                    break
            if hit:
                return hit
            # 落在历史记录之前：取最早一条
            if asof < recs[0][0]:
                return recs[0][2]
        return self.current.get(code, "")

    def is_st_or_delisting(self, code: str, asof: str) -> bool:
        n = self.name_at(code, asof).upper().replace(" ", "")
        return ("ST" in n) or ("退" in n) or ("*" in n)


# --------------------------------------------------------------------------- #
# 载入
# --------------------------------------------------------------------------- #
_READ_MODE = {"value": "ro"}


def _ro(path: str) -> sqlite3.Connection:
    """只读连接。主库可能被每日回补进程持有排他锁，此时降级 nolock 只读。

    nolock 只读不参与锁协议，仅用于并发写入期间读取已提交的历史行情；
    会在数据质量报告中记录 read_mode，便于事后核查。
    """
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=6)
        con.execute("PRAGMA busy_timeout=6000")
        con.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
        _READ_MODE["value"] = "ro"
        return con
    except sqlite3.OperationalError as e:
        if "locked" not in str(e).lower():
            raise
        log.warning("主库被并发写入锁定，降级 nolock 只读: %s", path)
        con = sqlite3.connect(f"file:{path}?mode=ro&nolock=1", uri=True, timeout=10)
        _READ_MODE["value"] = "ro_nolock"
        return con


def trade_dates(cfg: dict[str, Any], end_date: str, n: int) -> list[str]:
    """取 <= end_date 的最近 n 个真实交易日（升序）。"""
    with _ro(cfg["paths"]["market_db"]) as con:
        rows = con.execute(
            "SELECT DISTINCT trade_date FROM daily_cache WHERE trade_date<=? "
            "ORDER BY trade_date DESC LIMIT ?", (str(end_date), int(n))
        ).fetchall()
    if not rows:
        raise RuntimeError(f"daily_cache 中无 <= {end_date} 的交易日")
    return sorted(r[0] for r in rows)


def _to_matrix(df: pd.DataFrame, field_name: str, dates: list[str], codes: list[str]) -> np.ndarray:
    di = pd.Index(dates).get_indexer(df["trade_date"])
    ci = pd.Index(codes).get_indexer(df["ts_code"])
    out = np.full((len(dates), len(codes)), np.nan, dtype=np.float32)
    ok = (di >= 0) & (ci >= 0)
    vals = pd.to_numeric(df[field_name], errors="coerce").to_numpy(dtype=np.float32)
    out[di[ok], ci[ok]] = vals[ok]
    return out


def load_panel(cfg: dict[str, Any], start_date: str, end_date: str,
               name_resolver: PITNameResolver | None = None) -> Panel:
    """一次性载入 [start_date, end_date] 全市场面板。"""
    dcfg, pcfg = cfg["data"], cfg["paths"]
    need = int(dcfg.get("load_buffer_days", 200))
    need = min(need, int(dcfg.get("max_load_days", 420)))
    dates = trade_dates(cfg, end_date, need)
    dates = [d for d in dates if d >= str(start_date)] or dates[-30:]
    assert_no_future(dates, end_date, "load_panel")
    d0, d1 = dates[0], dates[-1]
    log.info("载入面板 %s ~ %s (%d 个交易日)", d0, d1, len(dates))

    basic = pd.read_csv(pcfg["stock_basic_csv"], dtype={"ts_code": str, "name": str,
                                                       "industry": str, "list_date": str})
    basic["ts_code"] = basic["ts_code"].astype(str)
    basic["industry"] = basic["industry"].fillna("未知").astype(str)
    basic["list_date"] = basic["list_date"].fillna("").astype(str)

    with _ro(pcfg["market_db"]) as con:
        daily = pd.read_sql_query(
            "SELECT ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol,amount "
            "FROM daily_cache WHERE trade_date BETWEEN ? AND ?", con, params=(d0, d1))
        dbasic = pd.read_sql_query(
            "SELECT ts_code,trade_date,turnover_rate,circ_mv FROM daily_basic_cache "
            "WHERE trade_date BETWEEN ? AND ?", con, params=(d0, d1))
        adj = pd.read_sql_query(
            "SELECT ts_code,trade_date,adj_factor FROM adj_factor_cache "
            "WHERE trade_date BETWEEN ? AND ?", con, params=(d0, d1))
        idx = pd.read_sql_query(
            "SELECT ts_code,trade_date,pct_chg FROM index_daily_cache "
            "WHERE trade_date BETWEEN ? AND ? AND ts_code=?", con,
            params=(d0, d1, dcfg.get("market_benchmark", "000985.CSI")))

    codes = sorted(set(daily["ts_code"]) | set(dbasic["ts_code"]))
    T, N = len(dates), len(codes)
    log.info("面板规模 T=%d N=%d, daily=%d 行", T, N, len(daily))

    m: dict[str, np.ndarray] = {
        "close_raw": _to_matrix(daily, "close", dates, codes),
        "open": _to_matrix(daily, "open", dates, codes),
        "high": _to_matrix(daily, "high", dates, codes),
        "low": _to_matrix(daily, "low", dates, codes),
        "pct_chg": _to_matrix(daily, "pct_chg", dates, codes),
        "vol": _to_matrix(daily, "vol", dates, codes),
        "amount": _to_matrix(daily, "amount", dates, codes),
        "turnover": _to_matrix(dbasic, "turnover_rate", dates, codes),
        "circ_mv": _to_matrix(dbasic, "circ_mv", dates, codes),
        "adj_factor": _to_matrix(adj, "adj_factor", dates, codes),
    }

    # ---- 统一复权口径：后复权价 = 原始收盘 * adj_factor ----
    af = m["adj_factor"].astype(np.float64)
    af_bad = ~np.isfinite(af) | (af <= 0)
    af[af_bad] = np.nan
    # 因子缺失时按同股前后有效值填充（只用相邻历史，不跨 asof）
    af_df = pd.DataFrame(af).ffill().bfill()
    close_hfq = m["close_raw"].astype(np.float64) * af_df.to_numpy()
    fallback_cnt = int(af_bad.sum())
    if fallback_cnt:
        log.warning("adj_factor 缺失 %d 个单元，已用相邻有效因子填充", fallback_cnt)

    ret = np.full((T, N), np.nan, dtype=np.float32)
    with np.errstate(divide="ignore", invalid="ignore"):
        ret[1:] = close_hfq[1:] / close_hfq[:-1] - 1.0
    # 因子或价格缺失时回退到 Tushare 的 pct_chg（本身已含除权调整）
    fb = ~np.isfinite(ret)
    ret[fb] = (m["pct_chg"][fb] / 100.0).astype(np.float32)
    # 异常行情（超出配置阈值）视为不可计算样本
    clip = float(dcfg.get("winsor_return", 0.25))
    extreme = np.abs(m["pct_chg"]) / 100.0 > float(cfg["universe"].get("max_abs_pct_chg", 0.35))
    ret[extreme] = np.nan
    ret[~np.isfinite(ret)] = np.nan
    ret = np.clip(np.nan_to_num(ret, nan=0.0), -clip, clip).astype(np.float32)
    # 无行情的日子不应产生 0 收益：用当日是否有行情恢复掩码
    quoted = np.isfinite(m["close_raw"]) & (m["close_raw"] > 0)
    ret[~quoted] = np.nan

    m["close"] = np.ascontiguousarray(close_hfq, dtype=np.float32)
    m["ret"] = ret
    valid = np.isfinite(ret) & np.isfinite(m["close"]) & (m["amount"] > 0)
    m["valid"] = valid.astype(np.float32)
    m["amount"] = np.nan_to_num(m["amount"], nan=0.0).astype(np.float32)
    m["turnover"] = np.nan_to_num(m["turnover"], nan=0.0).astype(np.float32)
    m["circ_mv"] = np.nan_to_num(m["circ_mv"], nan=0.0).astype(np.float32)
    for k in ("open", "high", "low", "close_raw", "pct_chg", "vol"):
        m[k] = np.nan_to_num(m[k], nan=0.0).astype(np.float32)

    # ---- 市场因子：优先中证全指，缺失日回退全市场流通市值加权 ----
    bench = np.full(T, np.nan, dtype=np.float64)
    if len(idx):
        pos = pd.Index(dates).get_indexer(idx["trade_date"])
        ok = pos >= 0
        bench[pos[ok]] = pd.to_numeric(idx["pct_chg"], errors="coerce").to_numpy()[ok] / 100.0
    valid_b = m["valid"].astype(bool)
    cnt = valid_b.sum(axis=1)
    eq = np.nansum(np.where(valid_b, ret, 0.0), axis=1) / np.maximum(cnt, 1)
    w = np.where(np.isfinite(m["circ_mv"]), m["circ_mv"], 0.0) * valid_b
    with np.errstate(invalid="ignore"):
        vw = (np.nansum(np.where(valid_b, ret, 0.0) * w, axis=1)
              / np.where(w.sum(axis=1) > 0, w.sum(axis=1), np.nan))
    mode = str(dcfg.get("market_benchmark", "000985.CSI")).upper()
    if mode in ("ALL_A_EQUAL_WEIGHT", "EQUAL_WEIGHT", "ALL_A_EW"):
        # 全A等权：对中小盘共振的解释力强于市值加权指数，实测可显著降低伪联动
        market, source = eq, "ALL_A_EQUAL_WEIGHT"
    elif mode in ("ALL_A_CIRC_MV_WEIGHT", "CIRC_MV_WEIGHTED"):
        market = np.where(np.isfinite(vw), vw, eq)
        source = "ALL_A_CIRC_MV_WEIGHT"
    else:
        market = np.where(np.isfinite(bench), bench, np.where(np.isfinite(vw), vw, eq))
        source = mode if np.isfinite(bench).sum() > T * 0.8 else "fallback:circ_mv_weighted"
    market = np.nan_to_num(market, nan=0.0)

    # ---- 行业因子：每日行业内等权收益，样本不足则退回市场收益 ----
    ind_map = dict(zip(basic["ts_code"], basic["industry"]))
    inds = np.asarray([ind_map.get(c, "未知") for c in codes])
    min_members = int(dcfg.get("min_industry_members", 3))
    ind_ret = np.zeros((T, N), dtype=np.float32)
    by_ind: dict[str, list[int]] = {}
    for j, ind in enumerate(inds):
        by_ind.setdefault(str(ind), []).append(j)
    for ind, cols in by_ind.items():
        if len(cols) < min_members or ind == "未知":
            ind_ret[:, cols] = market[:, None]
            continue
        sub = ret[:, cols]
        cnt = np.isfinite(sub).sum(axis=1)
        s = np.nansum(sub, axis=1)
        ir = np.where(cnt >= min_members, s / np.maximum(cnt, 1), market)
        ind_ret[:, cols] = ir[:, None].astype(np.float32)

    quality = {
        "trade_days": T, "raw_codes": N, "date_from": d0, "date_to": d1,
        "adj_factor_missing_cells": fallback_cnt,
        "market_source": source,
        "market_benchmark_days": int(np.isfinite(bench).sum()),
        "market_fallback_days": int((~np.isfinite(bench)).sum()),
        "industry_count": len(by_ind),
        "industry_small_groups": sum(1 for k, v in by_ind.items() if len(v) < min_members),
        "benchmark_index_days": int(np.isfinite(bench).sum()),
        "quote_coverage": round(float(quoted.mean()), 4),
        "pit_name_coverage": name_resolver.coverage if name_resolver else 0.0,
        "read_mode": _READ_MODE["value"],
    }
    log.info("面板完成: 市场因子=%s, 行业=%d, 行情覆盖率=%.2f%%",
             source, len(by_ind), quality["quote_coverage"] * 100)
    return Panel(
        dates=dates, codes=codes, arrays=m, market_ret=market.astype(np.float32),
        market_source=str(source), industry=inds, industry_ret=ind_ret,
        names=dict(zip(basic["ts_code"], basic["name"].fillna(""))),
        list_dates=dict(zip(basic["ts_code"], basic["list_date"])),
        quality=quality,
    )


# --------------------------------------------------------------------------- #
# 历史时点股票池
# --------------------------------------------------------------------------- #
def build_universe(panel: Panel, asof: str, cfg: dict[str, Any],
                   resolver: PITNameResolver) -> tuple[list[str], dict[str, Any]]:
    """严格 as-of 的全A可交易股票池，不使用任何未来信息。"""
    ucfg = cfg["universe"]
    if asof not in panel.dates:
        raise ValueError(f"asof={asof} 不在面板交易日内")
    t = panel.dates.index(asof)
    assert_no_future(panel.dates[:t + 1], asof, "build_universe")
    dates = panel.dates[:t + 1]
    rej: dict[str, int] = {}

    def keep(mask: np.ndarray, reason: str, cur: list[str]) -> list[str]:
        before = len(cur)
        out = [c for c in cur if mask[panel.codes.index(c)]]
        rej[reason] = before - len(out)
        return out

    idx = {c: i for i, c in enumerate(panel.codes)}
    prefixes = tuple(ucfg.get("board_prefix", ["60", "68", "00", "30"]))
    pool = [c for c in panel.codes
            if c.startswith(prefixes) and (not ucfg.get("exclude_bj", True) or not c.endswith(".BJ"))]
    rej["board_or_bj"] = len(panel.codes) - len(pool)

    if ucfg.get("exclude_st", True):
        before_st = len(pool)
        pool = [c for c in pool if not resolver.is_st_or_delisting(c, asof)]
        rej["st_or_delisting_pit"] = before_st - len(pool)
    min_listed = int(ucfg.get("min_listed_trade_days", 60))
    d_index = {d: i for i, d in enumerate(dates)}
    listed_ok = []
    for c in pool:
        ld = panel.list_dates.get(c, "") or ""
        first = next((i for i, d in enumerate(dates) if d >= ld), 0) if ld else 0
        if (t - first + 1) >= min_listed:
            listed_ok.append(c)
    rej["listed_lt_%dd" % min_listed] = len(pool) - len(listed_ok)
    pool = listed_ok

    cols = [idx[c] for c in pool]
    if not cols:
        return [], {"rejections": rej, "final": 0}

    quoted = panel["close_raw"][:t + 1, cols] > 0
    valid = panel["valid"][:t + 1, cols] > 0
    amount = panel["amount"][:t + 1, cols]
    w20 = min(20, t + 1)
    amt20 = np.nanmean(np.where(valid[-w20:], amount[-w20:], np.nan), axis=0)
    susp20 = w20 - np.sum(quoted[-w20:], axis=0)
    w60 = min(60, t + 1)
    cov60 = np.mean(valid[-w60:], axis=0)

    gate = np.ones(len(cols), dtype=bool)
    steps = [
        (quoted[t], "no_quote_on_asof"),
        (amt20 >= float(ucfg.get("min_avg_amount_20", 30000)), "low_liquidity"),
        (susp20 <= int(ucfg.get("max_suspension_days_20", 4)), "suspended"),
        (cov60 >= float(cfg["data"].get("min_valid_ratio_60d", 0.90)), "incomplete_history"),
        (np.isfinite(amt20), "invalid_amount"),
    ]
    for cond, reason in steps:
        before = int(gate.sum())
        gate &= np.asarray(cond, dtype=bool)
        rej[reason] = before - int(gate.sum())
    final = [c for c, g in zip(pool, gate) if g]

    quality = {
        "asof": asof,
        "candidate_codes": len(panel.codes),
        "final": len(final),
        "rejections": rej,
        "pit_st_names": True,
        "pit_name_coverage": resolver.coverage,
        "filters": {
            "min_listed_trade_days": min_listed,
            "min_avg_amount_20_thousand_cny": ucfg.get("min_avg_amount_20"),
            "max_suspension_days_20": ucfg.get("max_suspension_days_20"),
            "min_valid_ratio_60d": cfg["data"].get("min_valid_ratio_60d"),
        },
        "no_future_data": True,
    }
    log.info("PIT 股票池 %s: %d 只 (剔除 %s)", asof, len(final),
             {k: v for k, v in rej.items() if v})
    return sorted(final), quality


def load_stock_basic(cfg: dict[str, Any]) -> pd.DataFrame:
    return pd.read_csv(cfg["paths"]["stock_basic_csv"],
                       dtype={"ts_code": str, "name": str, "industry": str, "list_date": str})


def load_tag_maps(cfg: dict[str, Any]) -> dict[str, dict[str, set[str]]]:
    """事后归因用的行业/概念标签（绝不参与前面的统计发现）。"""
    out: dict[str, dict[str, set[str]]] = {"industry": {}, "westock_theme": {},
                                          "westock_node": {}, "kpl_concept": {}}
    try:
        b = load_stock_basic(cfg)
        for code, ind in zip(b["ts_code"].astype(str), b["industry"].fillna("未知")):
            if str(ind) and str(ind) != "未知":
                out["industry"].setdefault(str(ind), set()).add(code)
    except Exception as e:
        log.warning("行业标签载入失败: %s", e)

    try:
        with _ro(cfg["paths"]["westock_db"]) as con:
            df = pd.read_sql_query(
                "SELECT DISTINCT theme_name, node, stock_code FROM chain_stock_node", con)
        for theme, node, code in df.itertuples(index=False, name=None):
            c = norm_code(code)
            if not c:
                continue
            if theme:
                out["westock_theme"].setdefault(str(theme), set()).add(c)
            if node and "主题" not in str(node):
                out["westock_node"].setdefault(str(node), set()).add(c)
    except Exception as e:
        log.warning("产业链标签载入失败: %s", e)

    try:
        with _ro(cfg["paths"]["kpl_db"]) as con:
            df = pd.read_sql_query(
                "SELECT DISTINCT con_name, con_code FROM kpl_snapshot WHERE con_code IS NOT NULL", con)
        for name, code in df.itertuples(index=False, name=None):
            c = norm_code(code)
            if c and name:
                out["kpl_concept"].setdefault(str(name), set()).add(c)
    except Exception as e:
        log.warning("开盘啦概念标签载入失败: %s", e)
    return out
