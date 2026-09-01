import argparse
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

CACHE_DIR = os.environ.get("MSTOCK_CACHE", r"D:\mystock\cache_daily")
DB_PATH = os.path.join(CACHE_DIR, "stock_data.db")
BASIC_PATH = os.path.join(CACHE_DIR, "stock_basic.csv")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_daily")
MIN_CIRC_MV = 500_000.0  # 流通市值下限：50 亿元（Tushare 单位：万元）
MIN_BARS = 250  # 最少 K 线：上市满一年才参与（次新股分位样本太少会失真）
DATA_START = "20230103"  # 天量分位历史起点：以 20230103 为起点，此后上市以上市日为起点
MAIN_EVENT_PCT = 99.0  # 主事件门槛：量能+换手历史分位双≥P99 才算天量主事件（原 OR≥P98 命中面过大，V4.3 收紧为 AND≥P99）
MAX_EVENT_AGE = 60  # 近 60 天内出现过天量事件即入候选池
WATCH_MIN_T120 = 60  # V4.3：WATCH 状态 T120 下限，低于此分值的低分兜底票不输出
ANCHORS = {"中际旭创": ("300308.SZ", "20250508"), "华正新材": ("603186.SH", "20250812")}
STATES = ["DOWNTREND", "BASE", "IMPULSE", "EXTREME_CHURN", "ABSORPTION", "DRYUP", "RE_EXPANSION", "BREAKOUT_CONFIRM", "SECOND_WAVE", "DISTRIBUTION", "FAILED"]
WANTED_COLS = ["ts_code", "trade_date", "open", "high", "low", "close", "pct_chg", "vol", "turnover_rate", "turnover_rate_f", "circ_mv", "ma_bfq_10", "ma_bfq_20", "ma_bfq_60", "ma_bfq_120"]


def finite(value, default=0.0):
    try:
        value = float(value)
        return value if np.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def load_sli_codes(date):
    """加载 SLI V2 细分赛道 Top5 龙头代码集合（V4.4：不在票池中的候选直接过滤）
    标准接口 sli.reader.get_subsector_top5（asof 自动对齐最近快照）；无快照时返回 None 不启用过滤"""
    try:
        from sli.reader import get_subsector_top5
        panel = get_subsector_top5(asof=date)
    except Exception as exc:  # 快照缺失/包未生成时降级，不中断主流程
        print(f"[w7] 警告：SLI 龙头票池加载失败({exc})，跳过联动过滤", flush=True)
        return None
    codes = set(panel["ts_code"].astype(str).str.strip())
    codes.discard("")
    print(f"[w7] SLI 龙头票池 {len(codes)} 只，启用联动过滤", flush=True)
    return codes


def clip(value, low=0.0, high=100.0):
    return max(low, min(high, finite(value)))


def percentile_rank(values, value):
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) == 0:
        return 0.0
    return float(np.mean(values <= value) * 100.0)


def pct_position(open_, high, low, close):
    span = high - low
    return clip((close - low) / span * 100.0 if span > 0 else 50.0)


def safe_mean(values, default=0.0):
    values = [finite(x) for x in values if np.isfinite(finite(x))]
    return float(np.mean(values)) if values else default


def market_regime(conn, trade_date):
    # 缓存库无指数数据（daily_cache 仅存个股），改用全市场等权日收益序列近似市场环境
    q = """
        SELECT trade_date, AVG(pct_chg) AS m
        FROM stk_factor_pro
        WHERE trade_date<=? AND pct_chg IS NOT NULL
        GROUP BY trade_date ORDER BY trade_date DESC LIMIT 65
    """
    df = pd.read_sql_query(q, conn, params=(trade_date,)).sort_values("trade_date")
    if len(df) < 25:
        return "RANGE"
    m = pd.to_numeric(df.m, errors="coerce").fillna(0.0) / 100.0
    cum = (1.0 + m).cumprod()
    r20 = cum.iloc[-1] / cum.iloc[-21] - 1.0
    r60 = cum.iloc[-1] / cum.iloc[0] - 1.0
    if r20 > 0.06 and r60 > 0.03:
        return "BULL"
    if r20 > 0.02 or (r20 > -0.02 and r60 > 0):
        return "RECOVERY"
    if r20 < -0.06 and r60 < -0.03:
        return "BEAR"
    return "RANGE"


class CacheReader:
    def __init__(self, db_path=DB_PATH, cache_dir=CACHE_DIR):
        self.db_path = db_path
        self.cache_dir = cache_dir
        self.conn = sqlite3.connect(db_path)
        self.basic = self._load_basic()

    def _load_basic(self):
        if not os.path.exists(BASIC_PATH):
            return pd.DataFrame(columns=["ts_code", "name", "industry", "list_date"])
        df = pd.read_csv(BASIC_PATH, dtype={"ts_code": str, "list_date": str})
        return df.drop_duplicates("ts_code").set_index("ts_code")

    def latest_date(self):
        row = self.conn.execute("SELECT MAX(trade_date) FROM stk_factor_pro").fetchone()
        return str(row[0]) if row and row[0] else ""

    def universe(self, trade_date):
        df = pd.read_sql_query("SELECT ts_code, trade_date, circ_mv, total_mv FROM stk_factor_pro WHERE trade_date=?", self.conn, params=(trade_date,))
        if df.empty:
            return df
        snapshot_path = os.path.join(self.cache_dir, "market_" + trade_date + ".csv")
        if os.path.exists(snapshot_path):
            snapshot = pd.read_csv(snapshot_path, dtype={"ts_code": str})
            keep = [col for col in ["ts_code", "name", "industry", "total_mv", "circ_mv"] if col in snapshot]
            df = df.merge(snapshot[keep].drop_duplicates("ts_code"), on="ts_code", how="left", suffixes=("", "_snapshot"))
            for col in ["name", "industry"]:
                if col not in df:
                    df[col] = np.nan
        if "circ_mv_snapshot" in df:
            df["circ_mv"] = df["circ_mv"].fillna(df["circ_mv_snapshot"])
        if "circ_mv" not in df:
            df["circ_mv"] = df.get("total_mv", np.nan)
        df = df[df.ts_code.str.endswith((".SZ", ".SH"), na=False)]
        df = df[~df.ts_code.str.startswith(("8", "43", "83", "87", "92"), na=False)]
        df = df[df.circ_mv.fillna(0) >= MIN_CIRC_MV]
        if "name" not in df:
            df["name"] = np.nan
        if "industry" not in df:
            df["industry"] = np.nan
        df["name"] = df["name"].fillna(df.ts_code.map(self.basic.get("name", pd.Series(dtype=str))))
        df["industry"] = df["industry"].fillna(df.ts_code.map(self.basic.get("industry", pd.Series(dtype=str))))
        return df.drop_duplicates("ts_code")

    def bars_sql(self, ts_code, end_date):
        """逐只查询全历史（仅用于锚点等少量代码）"""
        available = {row[1] for row in self.conn.execute("PRAGMA table_info(stk_factor_pro)").fetchall()}
        wanted = WANTED_COLS
        selected = [col for col in wanted if col in available]
        q = f"SELECT {','.join(selected)} FROM stk_factor_pro WHERE ts_code=? AND trade_date<=? ORDER BY trade_date"
        df = pd.read_sql_query(q, self.conn, params=(ts_code, end_date))
        if df.empty:
            return df
        for col in wanted:
            if col not in df:
                df[col] = np.nan
        numeric = [x for x in wanted if x not in ("ts_code", "trade_date")]
        for col in numeric:
            if not pd.api.types.is_numeric_dtype(df[col]):
                df[col] = pd.to_numeric(df[col], errors="coerce")
        for window, col in ((10, "ma_bfq_10"), (20, "ma_bfq_20"), (60, "ma_bfq_60"), (120, "ma_bfq_120")):
            if df[col].isna().all():
                df[col] = df.close.rolling(window, min_periods=1).mean()
        return df.dropna(subset=["close", "high", "low", "vol"]).reset_index(drop=True)

    def bars(self, ts_code, end_date):
        df = self.frames.get(ts_code)
        if df is None or df.empty:
            return pd.DataFrame()
        return df[df.trade_date <= end_date].reset_index(drop=True)

    def load_all(self, end_date, codes=None, min_date=None, chunk=500, verbose=False):
        """按代码分批 + 日期范围加载历史（IN 参数过多会导致 SQLite 编译极慢，chunk 保持 500）"""
        available = {row[1] for row in self.conn.execute("PRAGMA table_info(stk_factor_pro)").fetchall()}
        wanted = WANTED_COLS
        selected = [col for col in wanted if col in available]
        if min_date is None:
            min_date = DATA_START
        parts = []
        code_list = list(codes) if codes else [None]
        t0 = time.time()
        for start in range(0, len(code_list), chunk):
            batch = code_list[start:start + chunk]
            if batch == [None]:
                where, params = "trade_date>=? AND trade_date<=?", [min_date, end_date]
            else:
                placeholders = ",".join(["?"] * len(batch))
                where, params = f"trade_date>=? AND trade_date<=? AND ts_code IN ({placeholders})", [min_date, end_date] + batch
            q = f"SELECT {','.join(selected)} FROM stk_factor_pro WHERE {where} ORDER BY trade_date"
            parts.append(pd.read_sql_query(q, self.conn, params=params))
            if verbose:
                print(f"[load] {len(parts)}/{len(code_list)} 行数={len(parts[-1])} 耗时={time.time()-t0:.1f}s", flush=True)
        if not parts:
            self.frames = {}
            return 0
        df = pd.concat(parts, ignore_index=True)
        for col in wanted:
            if col not in df:
                df[col] = np.nan
        numeric = [x for x in wanted if x not in ("ts_code", "trade_date")]
        for col in numeric:
            if not pd.api.types.is_numeric_dtype(df[col]):
                df[col] = pd.to_numeric(df[col], errors="coerce")
        for window, col in ((10, "ma_bfq_10"), (20, "ma_bfq_20"), (60, "ma_bfq_60"), (120, "ma_bfq_120")):
            if df[col].isna().all():
                df[col] = df.groupby("ts_code")["close"].transform(lambda s: s.rolling(window, min_periods=1).mean())
        df = df.dropna(subset=["close", "high", "low", "vol"]).copy()
        frames = {}
        for code, group in df.groupby("ts_code", sort=False):
            frames[code] = group.reset_index(drop=True)
        self.frames = frames
        return len(frames)

    def load_fina(self):
        """加载财务指标缓存（or_yoy 营收增速 / netprofit_yoy 净利增速 / 毛利率 / 现金流质量）"""
        try:
            df = pd.read_sql_query(
                "SELECT ts_code, end_date, ann_date, grossprofit_margin, netprofit_yoy, or_yoy, netprofit_2yoy, ocf_to_or, roe FROM fina_indicator_cache",
                self.conn)
        except Exception:
            df = pd.DataFrame()
        self.fina_frames = {}
        if not df.empty:
            for code, g in df[df.end_date != "EMPTY"].groupby("ts_code"):
                self.fina_frames[code] = g.sort_values("end_date").reset_index(drop=True)
        return len(self.fina_frames)

    def fina(self, ts_code, as_of=None):
        """返回 (最新一期, 上一期) 财务指标；as_of 用于 point-in-time（仅取 ann_date<=as_of 的已公告数据，防未来函数）"""
        g = self.fina_frames.get(ts_code)
        if g is None or g.empty:
            return None, None
        if as_of is not None:
            ann = g.ann_date.astype(str).str.slice(0, 8)
            valid = g[ann <= as_of]
            if valid.empty:
                return None, None
            g = valid
        now = g.iloc[-1]
        prev = g.iloc[-2] if len(g) >= 2 else None
        return now, prev

    def market_curve(self, end_date, min_date=None):
        """全市场等权累计收益序列（RS 与市场环境基准）；按日均值 CSV 增量缓存，避免每次全表聚合"""
        if min_date is None:
            min_date = DATA_START
        cache_path = os.path.join(self.cache_dir, "market_curve.csv")
        q_new = "SELECT trade_date, AVG(pct_chg) AS m FROM stk_factor_pro WHERE trade_date>? AND trade_date<=? AND pct_chg IS NOT NULL GROUP BY trade_date ORDER BY trade_date"
        q_full = "SELECT trade_date, AVG(pct_chg) AS m FROM stk_factor_pro WHERE trade_date>=? AND trade_date<=? AND pct_chg IS NOT NULL GROUP BY trade_date ORDER BY trade_date"
        cached = None
        if os.path.exists(cache_path):
            try:
                cached = pd.read_csv(cache_path, dtype={"trade_date": str})
            except Exception:
                cached = None
        if cached is not None and len(cached):
            last_cached = str(cached.trade_date.iloc[-1])
            if last_cached >= end_date:
                df = cached[cached.trade_date <= end_date].copy()
            else:
                new = pd.read_sql_query(q_new, self.conn, params=(last_cached, end_date))
                if len(new):
                    df = pd.concat([cached[["trade_date", "m"]], new], ignore_index=True)
                    try:
                        df.to_csv(cache_path, index=False)
                    except Exception:
                        pass
                else:
                    df = cached.copy()
        else:
            df = pd.read_sql_query(q_full, self.conn, params=(DATA_START, end_date))
            try:
                df.to_csv(cache_path, index=False)
            except Exception:
                pass
        if df.empty:
            return np.array([], dtype=object), np.array([])
        if str(df.trade_date.iloc[0]) < min_date:
            df = df[df.trade_date >= min_date]
        m = pd.to_numeric(df.m, errors="coerce").fillna(0.0) / 100.0
        dates = df.trade_date.astype(str).to_numpy()
        return dates, (1.0 + m).cumprod().to_numpy()

    def close(self):
        self.conn.close()


@dataclass
class EventAnalysis:
    state: str
    event_idx: int
    event_date: str
    event_percentile: float
    cq: float
    acceptance: float
    sds: float
    lock_score: float
    pp_score: float
    reexpansion: bool
    breakout: bool
    sim_zjxc: float
    sim_hzxc: float
    hvt_sim: float
    score: float
    grade: str
    buy_point: str
    explanation: str
    hard_fail: bool


def trend_features(df, i):
    if i < 65:
        return {"trend": 25.0, "rs": 50.0, "ma20_slope": 0.0, "ma60_slope": 0.0, "position": 50.0}
    row = df.iloc[i]
    ma20 = finite(row.ma_bfq_20, safe_mean(df.close.iloc[i - 19:i + 1]))
    ma60 = finite(row.ma_bfq_60, safe_mean(df.close.iloc[i - 59:i + 1]))
    ma20_prev = finite(df.iloc[i - 10].ma_bfq_20, safe_mean(df.close.iloc[i - 29:i - 9]))
    ma60_prev = finite(df.iloc[i - 20].ma_bfq_60, safe_mean(df.close.iloc[i - 79:i - 19]))
    slope20 = ma20 / ma20_prev - 1 if ma20_prev else 0
    slope60 = ma60 / ma60_prev - 1 if ma60_prev else 0
    ret20 = finite(row.close) / finite(df.iloc[i - 20].close, row.close) - 1
    trend = clip(35 + (ma20 > ma60) * 25 + clip(slope20 * 500, -20, 20) + clip(slope60 * 300, -10, 10) + clip(ret20 * 100, -15, 15))
    position = clip((row.close / ma60 - 0.9) * 500) if ma60 else 50
    return {"trend": trend, "rs": clip(50 + ret20 * 500), "ma20_slope": slope20, "ma60_slope": slope60, "position": position}


def extreme_event(df, i, window=None):
    # window=None：V5 主口径——事件日前 120 日分位（HVT-V3 规格：成交量≥120日99%分位；量能+换手双≥P99）
    # window=250：锚点口径——事件日前 250 日分位（与锚点识别时的历史口径一致）
    window = 120 if window is None else window
    start = i - window
    if start < 0 or i - start < min(window, MIN_BARS):
        return False, 0.0
    tr = finite(df.iloc[i].turnover_rate_f, finite(df.iloc[i].turnover_rate))
    vol = finite(df.iloc[i].vol)
    turns = pd.to_numeric(df.turnover_rate_f.iloc[start:i], errors="coerce").fillna(0).values
    vols = pd.to_numeric(df.vol.iloc[start:i], errors="coerce").fillna(0).values
    p_turn = percentile_rank(turns, tr)
    p_vol = percentile_rank(vols, vol)
    # V4.3→V5：OR≥P98 命中面过大 → AND≥P99；窗口由全历史改为 120 日（规格口径）
    return min(p_turn, p_vol) >= MAIN_EVENT_PCT, max(p_turn, p_vol)


def anchor_features(df, event_date):
    if df.empty or event_date not in set(df.trade_date.astype(str)):
        return None
    i = int(df.index[df.trade_date.astype(str) == event_date][0])
    if i < 250:
        return None
    is_extreme, ep = extreme_event(df, i, window=250)
    if not is_extreme:
        return None
    return behavior_features(df, i, ep)


def behavior_features(df, event_idx, event_percentile, end=None):
    end = min(len(df) - 1, event_idx + 20 if end is None else end)
    event = df.iloc[event_idx]
    after = df.iloc[event_idx + 1:end + 1]
    if after.empty:
        return None
    event_close = finite(event.close)
    event_low = finite(event.low)
    core = (finite(event.high) + event_low + event_close) / 3
    # 破坏程度以“跌破极端换手日低点”为基准（跌破才视为破坏，正常回调不误伤）
    after_low = finite(after.low.min(), event_low)
    breach = max(0.0, (event_low - after_low) / max(event_low, 1))
    max_dd = max(0.0, 1 - after_low / max(event_close, 1))
    vol_decay = clip((1 - safe_mean(after.vol.iloc[:min(5, len(after))]) / finite(event.vol, 1)) * 100)
    acceptance = clip(100 - breach * 400 - (finite(after.close.iloc[min(2, len(after) - 1)]) < event_low) * 25)
    if len(after) >= 3 and (after.pct_chg.iloc[:3] < 0).sum() >= 2:
        acceptance -= 10
    position = pct_position(event.open, event.high, event.low, event.close)
    down = after[after.close < after.open]
    down_decay = clip((1 - safe_mean(down.vol.iloc[-min(5, len(down)):]) / finite(event.vol, 1)) * 100) if not down.empty else 80
    hold = clip(100 - breach * 400)
    cq = clip(0.25 * position + 0.2 * hold + 0.2 * acceptance + 0.15 * vol_decay + 0.1 * hold + 0.1 * 70)
    recent = df.iloc[max(0, end - 4):end + 1]
    ratios = recent.vol / finite(event.vol, 1)
    drawdown = max(0.0, 1 - finite(recent.close.min(), event_close) / event_close)
    support = clip(100 - breach * 400 - (finite(recent.low.min(), event_low) < core * 0.97) * 15)
    trend = trend_features(df, end)
    vol_ratio_score = clip((1 - min(1, safe_mean(ratios.iloc[-5:]) / 0.6)) * 100)
    pos_score = pct_position(recent.open.iloc[-1], recent.high.iloc[-1], recent.low.iloc[-1], recent.close.iloc[-1])
    sds = clip(0.25 * vol_ratio_score + 0.30 * down_decay + 0.20 * support + 0.15 * pos_score + 0.10 * trend["trend"])
    lock = clip(0.30 * sds + 0.25 * cq + 0.20 * acceptance + 0.15 * support + 0.10 * trend["trend"])
    return {"event_percentile": event_percentile, "cq": cq, "acceptance": acceptance, "sds": sds, "lock": lock, "max_dd": max_dd, "trend": trend["trend"], "rs": trend["rs"], "position": position, "event_close": event_close, "event_low": event_low, "core": core, "vol_decay": vol_decay}


def pp_score(df, i, locked, market_ok=True):
    if i < 12 or not locked:
        return 0.0, False
    row = df.iloc[i]
    down = df.iloc[i - 10:i]
    max_down_vol = down.loc[down.close < down.open, "vol"].max()
    pp = finite(row.close) > finite(row.open) and finite(row.vol) > finite(max_down_vol) and pct_position(row.open, row.high, row.low, row.close) >= 65 and market_ok
    if not pp:
        return 0.0, False
    ma10 = finite(row.ma_bfq_10, safe_mean(df.close.iloc[i - 9:i + 1]))
    ma20 = finite(row.ma_bfq_20, safe_mean(df.close.iloc[i - 19:i + 1]))
    score = 60 + 15 * (row.close > ma10) + 10 * (ma10 > ma20) + 10 * (pct_position(row.open, row.high, row.low, row.close) >= 80) + 5 * (row.pct_chg > 2)
    return clip(score), True


def state_and_features(df, event_idx, event_percentile, end=None):
    end = len(df) - 1 if end is None else min(end, len(df) - 1)
    base = behavior_features(df, event_idx, event_percentile, end=end)
    if not base:
        return None
    event = df.iloc[event_idx]
    event_close = base["event_close"]
    core = base["core"]
    post = df.iloc[event_idx + 1:end + 1]
    recent = df.iloc[max(event_idx + 1, end - 4):end + 1]
    down = post[post.close < post.open]
    ratios = post.vol / finite(event.vol, 1)
    low_volume = safe_mean(ratios.iloc[-5:]) <= 0.65 if len(ratios) >= 3 else False
    drawdown = max(0.0, 1 - finite(post.low.min(), event_close) / event_close)
    early = post.iloc[:min(3, len(post))]
    early_hold = finite(early.low.min(), event.low) >= finite(event.low) * 0.97
    recent_window = df.iloc[max(0, end - 19):end + 1]
    recent_hold = finite(recent_window.low.min(), event.low) >= finite(event.low) * 0.95 and finite(df.iloc[end].close) >= finite(df.iloc[end].ma_bfq_20, event.low) * 0.92
    support_ok = early_hold and recent_hold
    persistent_sell = len(post) >= 3 and (post.close < post.open).tail(5).sum() >= 3 and safe_mean(down.vol.tail(3)) > finite(event.vol) * 0.75 if not down.empty else False
    # V4.1：CQ/Acceptance/SDS 阈值不再判死（转由 T120_ALPHA 的 HVT 维度降分体现），
    # 重大风险只保留两类：持续抛压、跌破天量关键支撑未收复
    major_risk = persistent_sell or not support_ok
    latest = df.iloc[end]
    pressure = finite(df.high.iloc[max(event_idx + 1, end - 10):end].max(), latest.high)
    # V4.1 DISTRIBUTION 组合判定：巨量/长上影/收盘弱/破位 需同时出现多项，单因子只降分不淘汰
    vol20 = safe_mean(df.vol.iloc[max(0, end - 19):end])
    weak_close = pct_position(latest.open, latest.high, latest.low, latest.close) < 35
    huge_down = finite(latest.vol) > finite(event.vol) * 0.9 and finite(latest.close) < finite(latest.open)
    upper_shadow = (finite(latest.high) - max(finite(latest.open), finite(latest.close))) / max(finite(latest.high) - finite(latest.low), 0.01)
    distribution = (huge_down and weak_close and not recent_hold) or (upper_shadow > 0.5 and weak_close and not recent_hold and finite(latest.vol) > vol20 * 1.5)
    pp, pp_ok = pp_score(df, end, base["lock"] >= 70 and base["sds"] >= 65)
    reexp = pp_ok and finite(latest.vol) >= vol20 * 0.9 and finite(latest.close) >= pressure * 0.995
    breakout = finite(latest.close) > pressure and finite(latest.vol) >= vol20 * 1.2 and pct_position(latest.open, latest.high, latest.low, latest.close) >= 70 and (latest.high - latest.close) / max(latest.high - latest.low, 0.01) < 0.35
    if len(post) < 5:
        # V4.2 放宽：事件太新不判死，仅持续放量抛压标记观察；其余等待验证期
        state = "FAILED" if persistent_sell else "EXTREME_CHURN"
    elif breakout and len(post) >= 5 and (pp_ok or reexp) and base["lock"] >= 70:
        state = "SECOND_WAVE"
    elif breakout or reexp:
        # V4.2 修复突破：重新放量突破压力位即算有效信号（优先于 FAILED/DISTRIBUTION）
        state = "BREAKOUT_CONFIRM" if breakout else "RE_EXPANSION"
    elif major_risk:
        state = "FAILED" if len(post) >= 8 else "DISTRIBUTION"
    elif distribution:
        state = "DISTRIBUTION"
    elif pp_ok and base["lock"] >= 80 and base["sds"] >= 75:
        # DRYUP 收紧：回测显示宽口径 DRYUP 为负期望（close5 -0.35%），需较强锁筹才保留
        state = "DRYUP"
    elif low_volume and support_ok and base["sds"] >= 80 and base["lock"] >= 80:
        state = "DRYUP"
    elif support_ok and base["acceptance"] >= 70:
        state = "ABSORPTION"
    else:
        state = "EXTREME_CHURN"
    return base, state, pp, pp_ok, reexp, breakout, major_risk, drawdown, pressure


def grade(score):
    if score >= 92: return "★★★★★ SECOND_WAVE_A"
    if score >= 88: return "★★★★☆ SECOND_WAVE_B"
    if score >= 84: return "PRE_SECOND_WAVE"
    if score >= 78: return "LOCKING_WATCH"
    if score >= 70: return "WATCH"
    return "IGNORE"


def similarity(current, anchor):
    if not anchor:
        return 0.0
    fields = [("trend", 0.20), ("event_percentile", 0.15), ("cq", 0.15), ("acceptance", 0.15), ("sds", 0.15), ("lock", 0.10), ("rs", 0.10)]
    distance = sum(weight * min(1.0, abs(finite(current[k]) - finite(anchor[k])) / 100.0) for k, weight in fields)
    return clip((1 - distance) * 100)


# ===== V4.1：T120_ALPHA 六维 / ENTRY_SCORE =====
DIM_NAMES = {"hvt": "天量吸收", "trend": "趋势", "fina": "基本面", "rs": "相对强度", "upside": "上方空间", "sector": "板块"}


class MarketCtx:
    """全市场等权累计收益曲线（RS 基准）：个股交易日必然是市场交易日，searchsorted 可精确命中"""

    def __init__(self, dates, vals):
        self.dates = dates
        self.vals = vals

    def ret(self, d0, d1):
        p0 = int(np.searchsorted(self.dates, d0))
        p1 = int(np.searchsorted(self.dates, d1))
        if p0 >= len(self.vals) or p1 >= len(self.vals):
            return 0.0
        v0, v1 = self.vals[p0], self.vals[p1]
        return v1 / v0 - 1.0 if v0 > 0 else 0.0


def alpha_hvt(base, hvt_sim, drawdown):
    # 维度1 天量吸收 25%：Acceptance + CQ + SDS + 锚点相似度；浅回撤加分、深回撤扣分
    s = 0.35 * base["acceptance"] + 0.30 * base["cq"] + 0.20 * base["sds"] + 0.15 * hvt_sim
    if drawdown <= 0.10:
        s += 4
    elif drawdown > 0.20:
        s -= 8
    if base["acceptance"] >= 90 and hvt_sim >= 90 and drawdown <= 0.10:
        s = max(s, 96.0)  # 特别奖励：Acceptance≥90 AND HVT_SIM≥90 AND 天量后回撤≤10%
    return clip(s)


def alpha_trend(df, i):
    # 维度2 趋势 20%：均线排列 + 斜率 + 平台突破 + 更高低点 + 趋势加速
    row = df.iloc[i]
    close = finite(row.close)
    if close <= 0:
        return 40.0
    ma20 = finite(row.ma_bfq_20, safe_mean(df.close.iloc[max(0, i - 19):i + 1]))
    ma60 = finite(row.ma_bfq_60, safe_mean(df.close.iloc[max(0, i - 59):i + 1]))
    ma120 = finite(row.ma_bfq_120, safe_mean(df.close.iloc[max(0, i - 119):i + 1]))
    align = (close > ma20) + (ma20 > ma60) + (ma60 > ma120)
    ma20_prev = finite(df.iloc[i - 20].ma_bfq_20, close) if i >= 20 else close
    ma60_prev = finite(df.iloc[i - 40].ma_bfq_60, close) if i >= 40 else close
    slope20 = ma20 / ma20_prev - 1 if ma20_prev > 0 else 0.0
    slope60 = ma60 / ma60_prev - 1 if ma60_prev > 0 else 0.0
    hi60 = finite(df.high.iloc[max(0, i - 59):i + 1].max(), close)
    near_high = close / hi60 if hi60 > 0 else 1.0
    lo_recent = finite(df.low.iloc[max(0, i - 29):i + 1].min(), close)
    lo_prior = finite(df.low.iloc[max(0, i - 89):max(1, i - 29)].min(), lo_recent) if i >= 30 else lo_recent
    higher_low = lo_prior > 0 and lo_recent > lo_prior
    ret20 = close / finite(df.iloc[i - 20].close, close) - 1 if i >= 20 else 0.0
    ret20_prev = finite(df.iloc[i - 20].close, close) / finite(df.iloc[i - 40].close, close) - 1 if i >= 40 else 0.0
    accel = ret20 > ret20_prev and ret20 > 0
    s = clip(align * 15 + clip(slope20 * 400, 0, 20) + clip(slope60 * 250, 0, 15) + clip((near_high - 0.9) * 200, 0, 15) + (12 if higher_low else 0) + (8 if accel else 0) + 15)
    return clip(s)


def alpha_fina(fina_now, fina_prev):
    # 维度3 基本面 20%：营收/净利增速 + 利润加速度 + 毛利率 + 现金流；无数据给中性分（不因缺数据否定潜力股）
    if fina_now is None:
        return 55.0
    or_g = finite(fina_now.or_yoy)
    np_g = finite(fina_now.netprofit_yoy)
    margin = finite(fina_now.grossprofit_margin)
    ocf = finite(fina_now.ocf_to_or)

    def gsc(g):
        return clip(50 + g * 1.1)

    s = 0.30 * gsc(or_g) + 0.40 * gsc(np_g) + 0.15 * clip(margin * 1.3) + 0.15 * clip(50 + ocf * 160)
    if fina_prev is not None:
        acc = np_g - finite(fina_prev.netprofit_yoy)
        s += clip(acc * 0.4, -10, 10)  # 利润加速度：增速环比改善加分
    return clip(s)


def alpha_rs(df, i, mkt):
    # 维度4 相对强度 15%：20/60/120 日相对全市场等权超额（不只看绝对 RS），短>中>长 视为加速
    close = finite(df.iloc[i].close)
    if close <= 0:
        return 50.0
    dates = df.trade_date.astype(str).to_numpy()

    def stock_ret(n):
        j = i - n
        base_px = finite(df.iloc[j].close, 0) if j >= 0 else 0.0
        return close / base_px - 1 if base_px > 0 else 0.0

    def mkt_ret(n):
        j = max(0, i - n)
        return mkt.ret(dates[j], dates[i]) if len(dates) else 0.0

    e20 = stock_ret(20) - mkt_ret(20)
    e60 = stock_ret(60) - mkt_ret(60)
    e120 = stock_ret(120) - mkt_ret(120)
    s20, s60, s120 = clip(50 + e20 * 250), clip(50 + e60 * 150), clip(50 + e120 * 100)
    s = 0.40 * s20 + 0.35 * s60 + 0.25 * s120
    if s20 >= s60 and s60 >= s120:
        s = min(100.0, s + 8)
    return clip(s)


def alpha_upside(df, i):
    # 维度5 上方空间 10%：距 60/120/250/全历史高点距离；趋势刚启动 + 长期套牢区远 = 修复空间大
    close = finite(df.iloc[i].close)
    if close <= 0:
        return 40.0

    def dist(n):
        hi = finite(df.high.iloc[max(0, i - n + 1):i + 1].max(), close)
        return max(0.0, 1 - close / hi) if hi > 0 else 0.0

    def spd(d):
        return clip(40 + d * 300)

    return clip(0.25 * spd(dist(60)) + 0.25 * spd(dist(120)) + 0.30 * spd(dist(250)) + 0.20 * spd(dist(750)))


def alpha_sector(industry, sector_strength, sector_growth):
    # 维度6 板块 10%：行业内个股 20 日收益中位数（行业强度）+ 行业净利增速中位数（行业景气）
    if not industry or industry not in sector_strength:
        return 50.0
    return clip(0.6 * sector_strength[industry] + 0.4 * sector_growth.get(industry, 50.0))


# ===== V5：HVT-V3 生命周期 / 未来空间 / 加速度 / 平台 / 派发风险 / 评分 =====
BASE_DATE = "20240801"  # V5：生命周期 BasePrice 起点（本轮主要趋势启动基准日）
EXTREME_EXTENSION = 3.00  # 300% 以上标记 EXTREME_EXTENSION（国恩股份类高位股，不剔除、重分类）
WATCH_MIN_SCORE = 62.0  # V5：C榜(WATCH) 基分下限，低于此的低分兜底票不输出
LIFECYCLE_BANDS = [(0.30, "L1", "初始启动"), (0.80, "L2", "趋势启动"), (1.50, "L3", "趋势中段"),
                   (3.00, "L4", "趋势成熟"), (5.00, "L5", "高位扩张"), (float("inf"), "L6", "极端扩张")]


def lifecycle(df, i):
    """V5 生命周期：BasePrice=20240801 以来最低价，Trend Extension=close/BasePrice-1，分 L1~L6"""
    dates = df.trade_date.astype(str).to_numpy()
    idx = np.where(dates[:i + 1] >= BASE_DATE)[0]
    if len(idx) == 0:
        return {"level": "L1", "band": "初始启动", "extension": 0.0, "base_price": finite(df.low.iloc[max(0, i - 120)], 1.0), "extreme": False}
    base_price = float(df.low.iloc[idx].min())
    close = finite(df.iloc[i].close)
    ext = close / base_price - 1 if base_price > 0 else 0.0
    level, band = "L6", "极端扩张"
    for thr, lv, bn in LIFECYCLE_BANDS:
        if ext < thr:
            level, band = lv, bn
            break
    return {"level": level, "band": band, "extension": ext, "base_price": base_price, "extreme": ext >= EXTREME_EXTENSION}


def lifecycle_score(lc):
    """生命周期评分：L2（趋势刚启动）最优，L1 次之，L6 最低；EXT 靠加速度/趋势维度补偿"""
    return {"L1": 72.0, "L2": 85.0, "L3": 72.0, "L4": 52.0, "L5": 40.0, "L6": 30.0}.get(lc["level"], 50.0)


def hvt_future_space(df, i, lc):
    """V5 未来空间：距60/120/250日高点 + 平台突破临近 + ATR 适中 + MA 排列；CORE 重修复空间、EXT 重突破临近"""
    close = finite(df.iloc[i].close)
    if close <= 0:
        return 40.0
    hi = {}
    for n in (60, 120, 250):
        lo_i = max(0, i - n + 1)
        hi[n] = finite(df.high.iloc[lo_i:i + 1].max(), close)

    def spd(d):
        return clip(40 + d * 280)  # 距高点越远，上方修复空间越大

    d60 = max(0.0, 1 - close / hi[60]) if hi[60] > 0 else 0.0
    d120 = max(0.0, 1 - close / hi[120]) if hi[120] > 0 else 0.0
    d250 = max(0.0, 1 - close / hi[250]) if hi[250] > 0 else 0.0
    near_high = close / hi[60] if hi[60] > 0 else 1.0  # 距60日高点越近=突破临近
    trs = []
    for j in range(max(1, i - 19), i + 1):
        h = finite(df.high.iloc[j]); l = finite(df.low.iloc[j]); c = finite(df.close.iloc[j - 1], h)
        trs.append(h - l)
    atr_pct = (safe_mean(trs) / close) if trs and close else 0.0
    atr_s = clip(100 - abs(atr_pct * 100 - 3.0) * 12)  # 日均振幅 3% 附近最利于趋势延续
    ma20 = finite(df.iloc[i].ma_bfq_20, safe_mean(df.close.iloc[max(0, i - 19):i + 1]))
    ma60 = finite(df.iloc[i].ma_bfq_60, safe_mean(df.close.iloc[max(0, i - 59):i + 1]))
    align = (close > ma20) + (ma20 > ma60)
    if lc["extension"] < 1.5:  # CORE/MID：低位重修复空间
        s = 0.35 * spd(d250) + 0.25 * spd(d120) + 0.20 * clip(near_high * 60 + 40) + 0.10 * atr_s + 0.10 * align * 40
    else:  # EXT：高位重突破临近 + 斜率
        slope = close / finite(df.close.iloc[max(0, i - 20)], close) - 1 if i >= 20 else 0.0
        s = 0.30 * clip(near_high * 90 + 10) + 0.30 * clip(50 + slope * 400) + 0.20 * spd(d60) + 0.10 * atr_s + 0.10 * align * 40
    return clip(s)


def hvt_acceleration(df, i, mkt=None):
    """V5 趋势加速度：RS5/20/60 递进 + 斜率递进（奖励 短>中>长，即加速度>趋势本身）"""
    close = finite(df.iloc[i].close)
    if close <= 0:
        return 50.0

    def sret(n):
        j = i - n
        base = finite(df.iloc[j].close, 0) if j >= 0 else 0.0
        return close / base - 1 if base > 0 else 0.0

    s5, s20, s60 = sret(5), sret(20), sret(60)
    dates = df.trade_date.astype(str).to_numpy()
    mr5 = mr20 = mr60 = 0.0
    if mkt is not None and len(dates):
        mr5 = mkt.ret(dates[max(0, i - 5)], dates[i])
        mr20 = mkt.ret(dates[max(0, i - 20)], dates[i])
        mr60 = mkt.ret(dates[max(0, i - 60)], dates[i])
    rs5, rs20, rs60 = clip(50 + (s5 - mr5) * 400), clip(50 + (s20 - mr20) * 250), clip(50 + (s60 - mr60) * 150)
    s = 0.45 * rs5 + 0.35 * rs20 + 0.20 * rs60
    if rs5 >= rs20 and rs20 >= rs60:
        s = min(100.0, s + 10)  # RS 加速：短>中>长
    if s5 >= s20 >= 0 and s60 >= 0:
        s = min(100.0, s + 6)  # 斜率加速
    return clip(s)


def hvt_platform(df, event_idx, i):
    """V5 平台压缩：天量后至今 振幅/ATR/量能压缩 + 回撤 + 低点抬高 + 高点测试"""
    if i <= event_idx + 4:
        return 50.0
    post = df.iloc[event_idx + 1:i + 1]
    pre10 = df.iloc[max(0, event_idx - 9):event_idx + 1]
    ev_vol = finite(df.iloc[event_idx].vol, 1)
    amp_now = safe_mean((post.high - post.low) / finite(post.close, 1))
    amp_pre = safe_mean((pre10.high - pre10.low) / finite(pre10.close, 1)) if len(pre10) >= 3 else amp_now
    amp_c = clip((1 - amp_now / max(amp_pre, 1e-6)) * 100) if amp_pre > 0 else 50.0
    vol_ratio = safe_mean(post.vol.iloc[-5:]) / max(ev_vol, 1)
    vol_c = clip((1 - vol_ratio / 0.4) * 60)  # 量缩到事件量 4 成以下为佳
    dd = max(0.0, 1 - finite(post.low.min(), 0) / finite(df.iloc[event_idx].close, 1))
    dd_s = clip(100 - dd * 300)
    half = max(1, len(post) // 2)
    lo_f = finite(post.low.iloc[:half].min(), 0)
    lo_b = finite(post.low.iloc[half:].min(), lo_f)
    lift = clip((lo_b / max(lo_f, 1e-6) - 1) * 500 + 50)  # 平台低点抬高
    hi_plat = finite(post.high.max(), 0)
    near = clip(finite(post.close.iloc[-1]) / max(hi_plat, 1e-6) * 100)
    test = clip((near - 70) * 2.5)  # 贴近平台高点=多次测试未破
    return clip(0.30 * amp_c + 0.30 * vol_c + 0.20 * dd_s + 0.10 * lift + 0.10 * test)


def hvt_distribution_risk(df, i, base, lc):
    """V5 派发风险（0~100，越高越危险）：高位滞涨/放量长阴/破中枢/MA20拐头/RS回落/涨缩量跌放量/高位大换手
    V5.1：初版阈值过严几乎不命中（TOP20 全 0），已放宽各因子阈值"""
    risk = 0.0
    row = df.iloc[i]
    close = finite(row.close)
    vol20 = safe_mean(df.vol.iloc[max(0, i - 19):i])
    vr = finite(row.vol) / vol20 if vol20 > 0 else 1.0
    ext = lc["extension"]
    if ext >= 1.5:
        ret10 = close / finite(df.close.iloc[max(0, i - 10)], close) - 1
        if ret10 <= 0.02 and vr >= 1.2:  # 高位滞涨+放量
            risk += 15
    pct = finite(row.pct_chg, 0)
    if pct < -1.5 and vr >= 1.3 and pct_position(row.open, row.high, row.low, row.close) < 40:  # 放量长阴
        risk += 20
    if close < finite(base["core"], 0) * 0.99:  # 跌破天量中枢
        risk += 20
    ma20 = finite(row.ma_bfq_20, 0)
    ma20_prev = finite(df.iloc[max(0, i - 5)].ma_bfq_20, ma20)
    if ma20 > 0 and ma20_prev > 0 and ma20 < ma20_prev * 0.998:  # MA20 拐头
        risk += 14
    s5 = close / finite(df.close.iloc[max(0, i - 5)], close) - 1
    s20 = close / finite(df.close.iloc[max(0, i - 20)], close) - 1
    if s5 < s20 - 0.02:  # 短期 RS 回落
        risk += 10
    up = df.iloc[max(0, i - 9):i + 1]
    up_bars = up[up.close >= up.open]
    down_bars = up[up.close < up.open]
    if len(up_bars) >= 2 and len(down_bars) >= 2:
        if safe_mean(down_bars.vol) > safe_mean(up_bars.vol) * 1.2:  # 下跌放量、上涨缩量
            risk += 12
    if ext >= 1.5 and finite(row.turnover_rate_f, finite(row.turnover_rate, 0)) > 8:  # 高位大换手
        risk += 10
    return clip(risk)


def hvt_v3_score(base, lc, hvt_q, fs, acc, rs, fina, plat, dist_risk):
    """V5 最终评分：BaseScore=0.25*HVT+0.20*Absorption+0.15*Lifecycle+0.15*FutureSpace+0.10*Acceleration+0.05*RS+0.10*Fundamental
    Score = BaseScore - DistributionRiskPenalty（独立惩罚，非线性）"""
    absorption = clip(0.5 * base["lock"] + 0.3 * base["acceptance"] + 0.2 * plat)
    base_score = clip(0.25 * hvt_q + 0.20 * absorption + 0.15 * lifecycle_score(lc)
                      + 0.15 * fs + 0.10 * acc + 0.05 * rs + 0.10 * fina)
    penalty = clip(dist_risk * 0.6, 0, 45)
    return clip(base_score - penalty), base_score, absorption, penalty


def rank_score_v5(score, lc, dist_risk, drawdown):
    """V5 排名分（收益风险比）：Score / (1 + 扩张风险 + 派发风险 + 回撤风险)"""
    ext_risk = clip((lc["extension"] - 2.0) * 30) if lc["extension"] > 2.0 else 0.0
    dd_risk = clip(drawdown * 100 * 0.5)
    return clip(score / (1.0 + (ext_risk + dist_risk + dd_risk) / 100.0))


def hvt_type(state, lc, dist_risk):
    """V5 生命周期类型：DISTRIBUTION（派发）/ CORE（低位）/ MID（中段）/ EXT（高位延续）"""
    if dist_risk >= 60 or state in ("DISTRIBUTION", "FAILED"):
        return "DISTRIBUTION"
    if lc["extension"] < 0.80:
        return "CORE"
    if lc["extension"] < 1.50:
        return "MID"
    return "EXT"


def horizon_phases(tp, lc, breakout):
    """V5 四周期预期阶段文本（T+10/20/60/120）"""
    if tp == "DISTRIBUTION":
        return {"t10": "回避，观察派发确认", "t20": "回避", "t60": "回避", "t120": "回避"}
    lv = lc["level"]
    if breakout:
        t10, t20 = "突破放量确认", "趋势启动，回踩不破MA10持有"
    else:
        t10, t20 = "平台内蓄势，等放量突破", "突破进趋势，不破继续观察"
    if lv in ("L1", "L2"):
        t60, t120 = "中期趋势扩张，空间大", "右尾概率高，跟踪主升"
    elif lv == "L3":
        t60, t120 = "中段换手，斜率决定高度", "需持续加速才有右尾"
    else:
        t60, t120 = "高位延续，破MA20离场", "二次加速机会，严守风险"
    return {"t10": t10, "t20": t20, "t60": t60, "t120": t120}


def t120_alpha_score(dims):
    return clip(0.25 * dims["hvt"] + 0.20 * dims["trend"] + 0.20 * dims["fina"] + 0.15 * dims["rs"] + 0.10 * dims["upside"] + 0.10 * dims["sector"])


def entry_score_v2(df, i, pp, pp_ok, reexp, breakout, event_low, mkt):
    # V4.1 ENTRY_SCORE：独立买点评分。Close_Position/量能只降分不淘汰（Distribution 由组合判定处理）
    row = df.iloc[i]
    close = finite(row.close)
    cp = pct_position(row.open, row.high, row.low, row.close) / 100.0
    vol20 = safe_mean(df.vol.iloc[max(0, i - 19):i])
    vr = finite(row.vol) / vol20 if vol20 > 0 else 1.0
    s_pp = pp if pp_ok else clip(pp * 0.6)  # PP10 是买点指标，未成立只降分
    s_cp = clip(cp * 115)  # CP<0.75 降分不删除
    if breakout:
        s_bo = 92.0
    elif reexp:
        s_bo = 80.0
    elif pp_ok:
        s_bo = 70.0
    else:
        s_bo = 40.0
    if 0.9 <= vr <= 2.5:
        s_v = 85.0  # 温和放量最佳
    elif 0.6 <= vr < 0.9:
        s_v = 60.0
    elif 2.5 < vr <= 3.5:
        s_v = 55.0
    elif vr > 3.5:
        s_v = 30.0  # 巨量警惕
    else:
        s_v = 40.0
    ma20 = finite(row.ma_bfq_20, safe_mean(df.close.iloc[max(0, i - 19):i + 1]))
    d20 = close / ma20 - 1 if ma20 > 0 else 0.0
    if -0.03 <= d20 <= 0.08:
        s_pb = 85.0  # 贴近 MA20 上方：回踩不破最佳
    elif 0.08 < d20 <= 0.15:
        s_pb = 65.0
    elif d20 > 0.15:
        s_pb = 40.0
    elif -0.08 <= d20 < -0.03:
        s_pb = 60.0
    else:
        s_pb = 30.0
    dates = df.trade_date.astype(str).to_numpy()
    sr5 = close / finite(df.iloc[i - 5].close, close) - 1 if i >= 5 and finite(df.iloc[i - 5].close, 0) > 0 else 0.0
    mr5 = mkt.ret(dates[max(0, i - 5)], dates[i]) if (mkt is not None and len(dates)) else 0.0
    s_rs = clip(50 + (sr5 - mr5) * 400)  # 短期 RS：5 日超额
    support = max(ma20, finite(event_low, close) * 0.97)
    downside = max((close - support) / close, 0.0) if close > 0 else 0.02
    rrr = 0.08 / max(downside, 0.02)  # 波段目标 +8% 对比止损距离
    s_rr = clip(30 + rrr * 35)
    total = clip(0.30 * s_pp + 0.15 * s_cp + 0.20 * s_bo + 0.10 * s_v + 0.10 * s_pb + 0.10 * s_rs + 0.05 * s_rr)
    dims = {"pp": s_pp, "cp": s_cp, "breakout": s_bo, "volume": s_v, "pullback": s_pb, "rs": s_rs, "rrr": s_rr}
    return total, dims


ENTRY_KEYS = {"pp": "PP10", "cp": "收盘位置", "breakout": "突破", "volume": "量能", "pullback": "回踩", "rs": "短期RS", "rrr": "风险收益比"}
STATUS_ORDER = ["PRIMARY_BUY", "T120_ROCKET", "CONFIRMED", "WATCH"]


def next_trigger(status, entry_dims, breakout, pp_ok):
    # 下一触发条件：状态升级所需的最小条件组合
    if status == "PRIMARY_BUY":
        return "买点已确认；跌破MA20/事件低点离场"
    if status == "T120_ROCKET":
        parts = []
        if not breakout:
            parts.append("放量突破平台")
        if not pp_ok:
            parts.append("PP10成立")
        if entry_dims["cp"] < 70:
            parts.append("收盘站上当日上1/3")
        if entry_dims["volume"] < 55:
            parts.append("温和放量1~2.5x")
        return "等待：" + "、".join(parts[:2]) if parts else "等待ENTRY回升≥80"
    if status == "CONFIRMED":
        return "等待T120≥85且ENTRY≥80升级PRIMARY"
    return "观察吸收/趋势修复，暂不参与"


def analyze(code, name, industry, df, anchors, reader=None, mkt=None, sector_strength=None, sector_growth=None):
    if len(df) < MIN_BARS:
        return None
    candidates = []
    for i in range(max(MIN_BARS, len(df) - MAX_EVENT_AGE - 1), len(df) - 2):
        ok, ep = extreme_event(df, i)
        if ok:
            candidates.append((i, ep))
    if not candidates:
        return None
    event_idx, ep = candidates[-1]
    result = state_and_features(df, event_idx, ep)
    if not result:
        return None
    base, state, pp, pp_ok, reexp, breakout, major_risk, drawdown, pressure = result
    # V5：移除 V4.1.2 涨幅剔除——高位大涨股不再简单剔除，改由生命周期分类（CORE/MID/EXT/DISTRIBUTION）处理
    last = len(df) - 1
    lc = lifecycle(df, last)
    current = dict(base)
    sim_a = similarity(current, anchors.get("中际旭创"))
    sim_b = similarity(current, anchors.get("华正新材"))
    hvt = (sim_a + sim_b) / 2
    fina_now, fina_prev = (None, None)
    if reader is not None:
        fina_now, fina_prev = reader.fina(code, as_of=str(df.iloc[last].trade_date))
    # V4.1：潜力（T120_ALPHA）与买点（ENTRY_SCORE）分离（保留参考）
    dims = {
        "hvt": alpha_hvt(base, hvt, drawdown),
        "trend": alpha_trend(df, last),
        "fina": alpha_fina(fina_now, fina_prev),
        "rs": alpha_rs(df, last, mkt),
        "upside": alpha_upside(df, last),
        "sector": alpha_sector(industry, sector_strength or {}, sector_growth or {}),
    }
    t120 = t120_alpha_score(dims)
    # V5 新增维度：未来空间 / 趋势加速度 / 平台压缩 / 派发风险
    fs = hvt_future_space(df, last, lc)
    acc = hvt_acceleration(df, last, mkt)
    plat = hvt_platform(df, event_idx, last)
    dist_risk = hvt_distribution_risk(df, last, base, lc)
    score, base_score, absorption, penalty = hvt_v3_score(base, lc, dims["hvt"], fs, acc, dims["rs"], dims["fina"], plat, dist_risk)
    rank = rank_score_v5(score, lc, dist_risk, drawdown)
    tp = hvt_type(state, lc, dist_risk)
    event_low = finite(df.low.iloc[event_idx], 0.0)
    entry, entry_dims = entry_score_v2(df, last, pp, pp_ok, reexp, breakout, event_low, mkt)
    # V5 状态机：派发型不进 A/B 榜；A/B 榜按 HVT-V3 总分；C榜=WATCH 兜底
    trend_state = state in ("BREAKOUT_CONFIRM", "SECOND_WAVE", "RE_EXPANSION")
    trend_confirmed = breakout or reexp or dims["trend"] >= 70
    if tp == "DISTRIBUTION":
        status = "WATCH"
    elif score >= 85 and entry >= 80:
        status = "PRIMARY_BUY"
    elif score >= 80:
        status = "T120_ROCKET"
    elif score >= 70 and (trend_confirmed or tp in ("MID", "EXT")):
        status = "CONFIRMED"
    else:
        status = "WATCH"
    # V5：C榜(WATCH) 低分兜底票不输出
    if status == "WATCH" and base_score < WATCH_MIN_SCORE:
        return None
    event_date = str(df.iloc[event_idx].trade_date)
    core = f"Ac{base['acceptance']:.0f}/CQ{base['cq']:.0f}/SIM{hvt:.0f}/回撤{drawdown * 100:.0f}%"
    ext_txt = f"涨幅{lc['extension'] * 100:.0f}%({lc['level']})"
    v5_dims = {"天量": dims["hvt"], "吸收": absorption, "生命周期": lifecycle_score(lc), "空间": fs, "加速": acc, "RS": dims["rs"], "基本面": dims["fina"]}
    if tp == "DISTRIBUTION":
        reason = f"派发风险(dist={dist_risk:.0f})暂不参与：{core}；{ext_txt}"
    elif status == "PRIMARY_BUY":
        reason = f"HVT-V3高分+买点双高：{core}；{ext_txt}；ENTRY={entry:.0f}"
    elif status == "T120_ROCKET":
        blk = min(entry_dims.items(), key=lambda kv: kv[1])
        short = f"{ENTRY_KEYS.get(blk[0], blk[0])}{blk[1]:.0f}"
        reason = f"潜力高待买点：{core}；{ext_txt}；ENTRY短板={short}"
    elif status == "CONFIRMED":
        wk = min(v5_dims, key=v5_dims.get)
        reason = f"趋势确认潜力中上：{core}；{ext_txt}；弱维={wk}={v5_dims[wk]:.0f}"
    elif status == "WATCH" and (major_risk or state in ("FAILED", "DISTRIBUTION")):
        reason = f"重大风险（抛压/破位）暂不参与：{core}；{ext_txt}"
    else:
        wk = min(v5_dims, key=v5_dims.get)
        reason = f"潜力不足：{core}；{ext_txt}；弱维={wk}={v5_dims[wk]:.0f}"
    explanation = (f"P{int(ep)}天量@{event_date}，{ext_txt}，状态{state}，类型{tp}；"
                   f"HVT-V3={score:.0f}（天量{dims['hvt']:.0f}/吸收{absorption:.0f}/生命{lifecycle_score(lc):.0f}/空间{fs:.0f}/加速{acc:.0f}/RS{dims['rs']:.0f}/基本面{dims['fina']:.0f}），"
                   f"派发风险{dist_risk:.0f}，ENTRY={entry:.0f}，{'今日PP10成立并重新放量' if pp_ok else '尚未出现合格PP10'}。")
    latest_bar = df.iloc[last]
    vol20 = safe_mean(df.vol.iloc[max(0, last - 19):last])
    volr = finite(latest_bar.vol, 0.0) / vol20 if vol20 > 0 else 0.0
    return {"code": code, "name": name, "industry": industry or "未覆盖", "state": state,
            "close": finite(latest_bar.close, 0.0), "pressure": pressure,
            "ma20": finite(latest_bar.ma_bfq_20, 0.0), "volr": volr,
            "score": score, "base_score": base_score, "rank": rank, "type": tp,
            "level": lc["level"], "extension": lc["extension"], "extreme_extension": lc["extreme"],
            "t120": t120, "entry": entry, "entry_dims": entry_dims, "dims": dims, "v5_dims": v5_dims,
            "fs": fs, "acc": acc, "plat": plat, "dist_risk": dist_risk, "absorption": absorption, "penalty": penalty,
            "cq": base["cq"], "acceptance": base["acceptance"], "sds": base["sds"], "lock": base["lock"], "pp": pp,
            "hvt": hvt, "sim_zjxc": sim_a, "sim_hzxc": sim_b, "buy": status, "grade": grade(t120),
            "event_date": event_date, "event_percentile": ep, "reexpansion": reexp, "breakout": breakout,
            "major_risk": major_risk, "hard_fail": major_risk, "reason": reason,
            "next": next_trigger(status, entry_dims, breakout, pp_ok), "explanation": explanation,
            "horizons": horizon_phases(tp, lc, breakout)}


def markdown(results, date):
    # V5：HVT-V3 三榜单（A/CORE、B/EXT、C/WATCH）+ TOP20 总榜 + 行为解释含四周期预期
    results = sorted(results, key=lambda x: (-x["rank"], x["code"]))
    n_core = sum(1 for x in results if x["type"] == "CORE")
    n_mid = sum(1 for x in results if x["type"] == "MID")
    n_ext = sum(1 for x in results if x["type"] == "EXT")
    n_dist = sum(1 for x in results if x["type"] == "DISTRIBUTION")
    lines = [f"# W7 HVT-V3 三榜单（A/CORE · B/EXT · C/WATCH）\n\n交易日：{date}　|　候选总数：{len(results)}"]
    lines.append(f"类型分布：CORE={n_core}　MID={n_mid}　EXT={n_ext}　DISTRIBUTION={n_dist}（DISTRIBUTION=派发风险，仅观察不进 A/B 榜）")
    cnt_state = {}
    for x in results:
        cnt_state[x["state"]] = cnt_state.get(x["state"], 0) + 1
    n_broken = sum(cnt_state.get(s, 0) for s in ("BREAKOUT_CONFIRM", "SECOND_WAVE", "RE_EXPANSION"))
    lines.append(f"状态分布：{'　'.join(f'{s}={c}' for s, c in sorted(cnt_state.items()))}　（已突破类=BREAKOUT_CONFIRM/SECOND_WAVE/RE_EXPANSION 合计 {n_broken} 家）")
    lines.append("价格口径：现价/触发价/MA20均为元；触发价=事件日后10日平台高点，放量(量比≥1.2)突破触发价=买点触发；已突破标的失效位=收盘跌回触发价下方；MA20=总防线；量比=当日量/前20日均量（不含当日）")
    lines.append("")
    col_header = "| # | 代码 | 名称 | 总分 | 类型 | 现价 | 触发价 | MA20 | 量比 | HVT | 吸收 | 生命 | 空间 | 加速 | RS | 基本面 | DRisk | 状态 |"
    col_sep = "| -- | -- | -- | --: | -- | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: | -- |"

    def row(x, idx):
        v = x["v5_dims"]
        return (f"| {idx} | {x['code']} | {x['name']} | {x['score']:.1f} | {x['type']} "
                f"| {x['close']:.2f} | {x['pressure']:.2f} | {x['ma20']:.2f} | ×{x['volr']:.1f} "
                f"| {v['天量']:.0f} | {v['吸收']:.0f} | {v['生命周期']:.0f} | {v['空间']:.0f} | {v['加速']:.0f} | {v['RS']:.0f} | {v['基本面']:.0f} | {x['dist_risk']:.0f} | {x['state']} |")

    # TOP20 总榜（Rank=收益风险比）
    lines.append("## TOP20 总榜（按 Rank=收益风险比）\n")
    lines.append(col_header)
    lines.append(col_sep)
    for k, x in enumerate(results[:20], 1):
        lines.append(row(x, k))
    # A榜 CORE
    core_list = [x for x in results if x["type"] == "CORE"]
    lines.append(f"\n## A榜 CORE-HVT（低位/中位大资金吸收型，T+60/T+120 空间优先）　共{len(core_list)}只\n")
    if core_list:
        lines.append(col_header)
        lines.append(col_sep)
        for k, x in enumerate(core_list[:20], 1):
            lines.append(row(x, k))
    else:
        lines.append("_（今日无 CORE-HVT 候选）_")
    # B榜 EXT
    ext_list = [x for x in results if x["type"] == "EXT"]
    lines.append(f"\n## B榜 EXT-HVT（高位强趋势延续/二次加速，涨幅大不剔除）　共{len(ext_list)}只\n")
    if ext_list:
        lines.append(col_header)
        lines.append(col_sep)
        for k, x in enumerate(ext_list[:20], 1):
            lines.append(row(x, k))
    else:
        lines.append("_（今日无 EXT-HVT 候选）_")
    # MID 补充
    mid_list = [x for x in results if x["type"] == "MID"]
    if mid_list:
        lines.append(f"\n## MID-HVT（趋势中段换手型）　共{len(mid_list)}只\n")
        lines.append(col_header)
        lines.append(col_sep)
        for k, x in enumerate(mid_list[:15], 1):
            lines.append(row(x, k))
    # 已突破标的完整名单（不受榜单前20截断影响，推送引用以此为准）
    broken = [x for x in results if x["state"] in ("BREAKOUT_CONFIRM", "SECOND_WAVE", "RE_EXPANSION")]
    lines.append(f"\n## 已突破标的完整名单（BREAKOUT_CONFIRM/SECOND_WAVE/RE_EXPANSION 共{len(broken)}只）\n")
    if broken:
        lines.append("| # | 代码 | 名称 | 总分 | 类型 | 现价 | 触发价 | MA20 | 量比 | 状态 |")
        lines.append("| -- | -- | -- | --: | -- | --: | --: | --: | --: | -- |")
        for k, x in enumerate(sorted(broken, key=lambda y: -y["score"]), 1):
            lines.append(f"| {k} | {x['code']} | {x['name']} | {x['score']:.1f} | {x['type']} "
                         f"| {x['close']:.2f} | {x['pressure']:.2f} | {x['ma20']:.2f} | ×{x['volr']:.1f} | {x['state']} |")
    else:
        lines.append("_（今日无已突破标的）_")
    # C榜 WATCH
    watch = [x for x in results if x["buy"] == "WATCH"]
    dist_watch = [x for x in watch if x["type"] == "DISTRIBUTION"]
    lines.append(f"\n## C榜 WATCH（暂未突破/派发观察，等趋势确认）　共{len(watch)}只\n")
    lines.append(f"其中 DISTRIBUTION（派发风险）{len(dist_watch)} 只、其余潜力不足 {len(watch) - len(dist_watch)} 只，不逐一列出。")
    watch_top = [x for x in watch if x["score"] >= 75]
    if watch_top:
        lines.append("\n其中 HVT-V3 总分≥75 的潜力票（带风险信号，等修复突破后重新确认）：")
        lines.append("| 代码 | 名称 | 总分 | 类型 | 状态 | 关键原因 |")
        lines.append("| -- | -- | --: | -- | -- | -- |")
        for x in sorted(watch_top, key=lambda x: -x["score"])[:10]:
            lines.append(f"| {x['code']} | {x['name']} | {x['score']:.1f} | {x['type']} | {x['state']} | {x['reason']} |")
    # 行为解释 + 四周期预期
    top = results[:20]
    lines.append("\n## 行为解释与 T+10/20/60/120 预期\n")
    for x in top:
        h = x["horizons"]
        lines.append(f"- **{x['name']}({x['code']})** [{x['type']}/{x['level']}]：{x['explanation']}")
        lines.append(f"　T+10={h['t10']}　|　T+20={h['t20']}　|　T+60={h['t60']}　|　T+120={h['t120']}")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--output", default="")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    reader = CacheReader()
    date = args.date or reader.latest_date()
    universe = reader.universe(date)
    load_codes = list(universe["ts_code"].tolist()) if not universe.empty else []
    print(f"[w7] 日期={date} 股池={len(load_codes)} 开始加载历史...", flush=True)
    reader.load_all(date, codes=load_codes, verbose=args.verbose)
    anchors = {}
    for label, (code, anchor_date) in ANCHORS.items():
        adf = reader.bars_sql(code, date)
        anchors[label] = anchor_features(adf, anchor_date)
    # V4.1：市场等权曲线（RS 基准）+ 财务数据（point-in-time）+ 板块聚合
    mdates, mvals = reader.market_curve(date)
    mkt = MarketCtx(mdates, mvals)
    nfina = reader.load_fina()
    industry_map = {}
    if not universe.empty:
        for _, r in universe.iterrows():
            industry_map[str(r.get("ts_code", ""))] = str(r.get("industry") or "")
    by_ind, fin_ind = {}, {}
    for code, f in reader.frames.items():
        if len(f) < 21:
            continue
        c0, c1 = finite(f.iloc[-21].close, 0.0), finite(f.iloc[-1].close, 0.0)
        ind = industry_map.get(code, "")
        if c0 <= 0 or not ind or ind == "nan":
            continue
        by_ind.setdefault(ind, []).append(c1 / c0 - 1.0)
        g = reader.fina_frames.get(code)
        if g is not None and len(g):
            np_g = finite(g.iloc[-1].netprofit_yoy, None)
            if np_g is not None:
                fin_ind.setdefault(ind, []).append(np_g)
    sector_strength = {ind: clip(50 + float(np.median(v)) * 150) for ind, v in by_ind.items() if len(v) >= 3}
    sector_growth = {ind: clip(50 + float(np.median(v)) * 1.1) for ind, v in fin_ind.items() if len(v) >= 3}
    print(f"[w7] 财务覆盖={nfina} 行业强度={len(sector_strength)} 行业景气={len(sector_growth)}", flush=True)
    sli_codes = load_sli_codes(date)  # V4.4：SLI 龙头票池联动过滤
    results = []
    rows = universe.to_dict("records")
    if args.limit:
        rows = rows[:args.limit]
    t_start = time.time()
    for n, row in enumerate(rows):
        if n and n % 500 == 0:
            print(f"[w7] 分析进度 {n}/{len(rows)} 耗时={time.time()-t_start:.1f}s", flush=True)
        code = str(row.get("ts_code", ""))
        if sli_codes is not None and code not in sli_codes:  # V4.4：不在 SLI 龙头票池中直接过滤
            continue
        if "ST" in str(row.get("name", "")).upper() or "退" in str(row.get("name", "")):
            continue
        name = str(row.get("name") or code)
        basic = reader.basic.loc[code] if code in reader.basic.index else {}
        list_date = str(basic.get("list_date", "")) if hasattr(basic, "get") else ""
        if list_date and list_date.isdigit() and int(list_date) > int(date) - 365:
            continue
        df = reader.bars(code, date)
        industry = str(row.get("industry") or (basic.get("industry", "") if hasattr(basic, "get") else ""))
        result = analyze(code, name, industry, df, anchors, reader=reader, mkt=mkt, sector_strength=sector_strength, sector_growth=sector_growth)
        if result:
            results.append(result)
    text = markdown(results, date)
    output = args.output or os.path.join(OUTPUT_DIR, f"w7_second_wave_{date}.md")
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w", encoding="utf-8") as fh:
        fh.write(text)
    state_counts = {s: 0 for s in STATES}
    for x in results:
        state_counts[x["state"]] = state_counts.get(x["state"], 0) + 1
    buy_counts = {}
    for x in results:
        buy_counts[x["buy"]] = buy_counts.get(x["buy"], 0) + 1
    type_counts = {}
    for x in results:
        type_counts[x["type"]] = type_counts.get(x["type"], 0) + 1
    stats = {
        "date": date, "universe": len(rows), "results": len(results),
        "output": output, "states": {k: v for k, v in state_counts.items() if v},
        "buys": buy_counts, "types": type_counts,
    }
    print(json.dumps(stats, ensure_ascii=False))
    reader.close()


if __name__ == "__main__":
    main()
