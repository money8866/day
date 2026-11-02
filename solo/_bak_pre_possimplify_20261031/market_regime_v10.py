#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
A 股短线市场节点识别 + 五状态机 + 动态仓位建议（V10.0）

设计原则（对应 V9.9 的增量升级，不改动 V9.9 原有选股/买卖逻辑）：
  1. 市场环境判断与个股买入信号严格分离 —— 本模块只输出「环境状态 + 仓位空间」，
     永远不输出「买入某只股票」。
  2. 数据缺失一律标 UNKNOWN（None），绝不默认填成正常值。
  3. 不把极端低广度、低位置分位直接解释成「底部」—— 底部是卖压衰减 + 修复确认的事。
  4. 量价只用全市场成交额口径，指数量比单独标注，不混用。
  5. 节点识别用多指标联合判断，输出具体证据；状态切换需满足确认/退出条件。

独立运行：
    python market_regime_v10.py                 # 用最近交易日
    python market_regime_v10.py 20261030        # 指定交易日
    python market_regime_v10.py 20261030 --json # 输出 JSON
"""
import os
import sys
import json
import sqlite3
import argparse
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

# ============================================================
# 常量
# ============================================================

# 五状态
STATE_NORMAL = "S1"
STATE_DRAWDOWN = "S2"
STATE_PANIC = "S3"
STATE_EASING = "S4"
STATE_REPAIR = "S5"

STATE_CN = {
    STATE_NORMAL: "正常",
    STATE_DRAWDOWN: "市场退潮",
    STATE_PANIC: "恐慌加速",
    STATE_EASING: "卖压衰减观察",
    STATE_REPAIR: "修复确认",
}

# 节点类型
NODE_PANIC = "PANIC_ACCELERATION"
NODE_EXTREME = "EXTREME_SENTIMENT"
NODE_EASING = "SELLING_PRESSURE_EASING"
NODE_REPAIR = "REPAIR_CONFIRMED"

NODE_CN = {
    NODE_PANIC: "恐慌加速",
    NODE_EXTREME: "极端情绪",
    NODE_EASING: "卖压衰减",
    NODE_REPAIR: "修复确认",
}

# 观察的指数（每个都必须用自己的历史序列，不允许互相替代）
INDEX_LIST = [
    ("上证指数", "000001.SH"),
    ("沪深300", "000300.SH"),
    ("中证2000", "932000.CSI"),
    ("创业板指", "399006.SZ"),
    ("科创50", "000688.SH"),
]
CORE_INDEX = "中证2000"      # 短线小盘代表
BENCH_INDEX = "沪深300"      # 大盘代表

# 全市场日线缓存（5.7 年历史，1410 个交易日）
CACHE_DIR = r"D:\mystock\cache_daily"
STOCK_DB = os.path.join(CACHE_DIR, "stock_data.db")

CONFIG_PATH = os.path.join(BASE_DIR, "config_v10_market_regime.json")

# 状态持久化表（记录昨日状态，用于连续性与切换原因）
STATE_DB = os.path.join(BASE_DIR, "cache_backbone_tushare", "market_regime_v10.db")

UNKNOWN = "UNKNOWN"


# ============================================================
# 配置
# ============================================================

def load_config(path: str = None) -> dict:
    """加载阈值配置。配置文件缺失时返回内置默认值（保证模块可独立运行）。"""
    p = path or CONFIG_PATH
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[V10] 配置文件读取失败({e})，使用内置默认阈值")
        return _DEFAULT_CONFIG


_DEFAULT_CONFIG = {
    "breadth": {"panic_ratio": 0.20, "extreme_percentile": 0.10,
                "easing_rebound_pp": 8.0, "repair_ratio": 0.55,
                "repair_delta_pp": 10.0, "drawdown_ratio": 0.35,
                "normal_lower_ratio": 0.40},
    "limit_down": {"panic_increase_ratio": 1.5, "panic_abs_count": 30,
                   "easing_decrease_ratio": 0.7, "easing_abs_decrease": 10,
                   "repair_decrease_ratio": 0.8, "high_percentile": 0.90},
    "broken_rate": {"extreme_percentile": 0.85, "easing_max": 0.35,
                    "repair_max": 0.30},
    "promotion_rate": {"extreme_percentile": 0.20, "repair_min": 0.25},
    "strong_stock": {"extreme_mean_pct": -2.0, "easing_mean_pct": -1.0,
                     "repair_mean_pct": 0.0, "repair_up_ratio": 0.45},
    "amount": {"panic_expand_ratio": 1.15, "easing_shrink_ratio": 0.95,
               "min_history_days": 20},
    "index_relative": {"panic_gap_pct": -1.0, "repair_gap_min": -0.3},
    "index_trend": {"ma20_window": 20, "ma60_window": 60,
                    "repair_above_ma20_ratio": 0.5,
                    "drawdown_below_ma20_ratio": 0.5,
                    "near_low_lookback": 60, "near_low_pct": 3.0},
    "position_percentile": {"close_lookback": 250, "drawdown_lookback": 250,
                            "min_bars": 120},
    "nodes": {"panic_min_hits": 3, "extreme_min_hits": 2,
              "easing_min_hits": 2, "repair_min_hits": 3},
    "state_machine": {"confirm_days": {"S2": 1, "S3": 1, "S4": 2, "S5": 2},
                      "exit_days": {"S2": 2, "S3": 3, "S4": 2, "S5": 2},
                      "fast_risk_escalation": True, "unknown_confidence": "低"},
    "position_advice": {
        "S1": {"target": None, "action": "按现有策略与个股信号执行", "allow_probe": True},
        "S2": {"target": 30, "action": "降低交易频率，减少追高，重新评估持仓风险",
               "allow_probe": False},
        "S3": {"target": 5, "target_max": 5, "action": "防守为主，不因指数跌幅大而主动抄底",
               "allow_probe": False},
        "S4": {"target": 5, "target_max": 5, "action": "观察为主，不自动买入，不降低原有买入标准",
               "allow_probe": False},
        "S5": {"target": 20, "action": "允许重新评估试探性仓位，但仅在个股信号成立时买入",
               "allow_probe": True},
    },
    "observation": {
        "S3": ["跌停家数是否见顶回落", "市场广度是否止跌回升", "前期强势股负反馈是否收敛"],
        "S4": ["跌停家数是否连续下降", "指数低点是否不再下移", "炸板率是否同步改善"],
        "S5": ["修复的持续性（连续第2日广度回升）", "主线板块是否普遍走强", "强势股溢价能否延续"],
        "S2": ["跌停家数与广度是否继续恶化", "成交额是放量还是缩量下跌", "强势股是否出现集中补跌"],
        "S1": ["广度与跌停结构是否出现变化", "成交额与指数方向是否背离", "主线持续性"],
    },
}


# ============================================================
# 数据结构
# ============================================================

@dataclass
class BreadthData:
    """市场广度。平盘家数单列，不丢弃后与其他口径混用。"""
    up_count: int = 0
    down_count: int = 0
    flat_count: int = 0
    total: int = 0
    breadth_ratio: Optional[float] = None      # up / (up + down)
    total_based_ratio: Optional[float] = None  # up / total（含平盘），备用口径
    prev_breadth_ratio: Optional[float] = None
    delta_pp: Optional[float] = None           # 相对前一交易日的百分点变化
    breadth_percentile: Optional[float] = None  # 在历史序列中的分位(0~100)
    _hist: Optional[List[float]] = field(default=None, repr=False)


@dataclass
class LimitData:
    """涨跌停与短线情绪。口径统一为「收盘价」判定。"""
    zt_count: Optional[int] = None
    dt_count: Optional[int] = None
    zhaban_count: Optional[int] = None
    broken_rate: Optional[float] = None         # 炸板 / (封住 + 炸板)
    max_height: Optional[int] = None            # 最高连板
    promotion_rate: Optional[float] = None      # 连板晋级率 = 昨日涨停中今日继续涨停
    strong_stock_mean_pct: Optional[float] = None   # 昨日涨停股今日平均涨幅
    strong_stock_up_ratio: Optional[float] = None    # 昨日涨停股今日上涨比例
    strong_stock_count: Optional[int] = None
    dt_percentile: Optional[float] = None       # 跌停家数历史分位
    prev_dt_count: Optional[int] = None
    prev_zt_count: Optional[int] = None
    dt_change_ratio: Optional[float] = None     # 跌停家数相对前日变化倍数
    _dt_hist: Optional[List[float]] = field(default=None, repr=False)


@dataclass
class AmountData:
    """成交额与量价关系。口径 = 全市场日线 amount 求和（千元→亿元）。"""
    total_amount: Optional[float] = None        # 亿元
    prev_amount: Optional[float] = None
    chg_vs_prev: Optional[float] = None         # 相对前一交易日 %
    chg_vs_ma5: Optional[float] = None          # 相对过去5日均额 %
    chg_vs_ma20: Optional[float] = None         # 相对过去20日均额 %
    index_chg: Optional[float] = None           # 核心指数当日涨跌幅 %
    pattern: str = UNKNOWN                      # 放量上涨/缩量上涨/放量下跌/缩量下跌
    _hist: Optional[List[float]] = field(default=None, repr=False)


@dataclass
class IndexData:
    """单个指数的趋势与位置。"""
    name: str = ""
    code: str = ""
    close: Optional[float] = None
    pct_chg: Optional[float] = None
    ret5: Optional[float] = None
    ret20: Optional[float] = None
    ma20: Optional[float] = None
    ma60: Optional[float] = None
    above_ma20: Optional[bool] = None
    above_ma60: Optional[bool] = None
    max_drawdown_20d: Optional[float] = None
    near_low: Optional[bool] = None            # 是否接近近期低点
    close_percentile_250d: Optional[float] = None  # 250 日收盘分位
    drawdown_percentile: Optional[float] = None     # 近期回撤幅度的历史分位
    bars_available: int = 0
    percentile_note: str = ""                   # 分位口径说明


@dataclass
class MarketSnapshot:
    """某个交易日的市场快照。所有缺失字段为 None（= UNKNOWN）。"""
    trade_date: str = ""
    breadth: BreadthData = field(default_factory=BreadthData)
    limit: LimitData = field(default_factory=LimitData)
    amount: AmountData = field(default_factory=AmountData)
    indexes: Dict[str, IndexData] = field(default_factory=dict)
    small_vs_large_gap: Optional[float] = None   # 中证2000 - 沪深300（百分点）
    quality_flags: List[str] = field(default_factory=list)  # 数据质量提示
    prev_snapshot: Optional["MarketSnapshot"] = field(default=None, repr=False)


@dataclass
class NodeResult:
    """节点识别结果。"""
    is_node_day: bool = False
    node_type: str = "无"
    node_cn: str = "无"
    confidence: str = "低"
    evidence: List[str] = field(default_factory=list)      # 最多5条
    uncertainties: List[str] = field(default_factory=list)  # 最多3条
    hit_detail: Dict[str, List[str]] = field(default_factory=dict)  # 各节点命中项明细


@dataclass
class RegimeDecision:
    """状态机输出。"""
    state: str = STATE_NORMAL
    state_cn: str = "正常"
    prev_state: str = STATE_NORMAL
    changed: bool = False
    consecutive_days: int = 1
    confidence: str = "中"
    reasons: List[str] = field(default_factory=list)
    triggers: List[str] = field(default_factory=list)
    pending_days: int = 0
    pending_target: str = ""


@dataclass
class PositionAdvice:
    """仓位建议。严格区分目标仓位 / 实际仓位 / 加仓空间。"""
    state: str = STATE_NORMAL
    target_position: Optional[int] = None       # 系统建议目标仓位
    actual_position: Optional[int] = None       # 实际仓位（未提供则 None）
    add_room: Optional[int] = None              # 允许增加的仓位空间
    allow_probe: bool = False
    action: str = ""
    add_conditions: List[str] = field(default_factory=list)
    reduce_conditions: List[str] = field(default_factory=list)
    excess_note: str = ""                      # 实际高于目标时的提示


# ============================================================
# 数据采集
# ============================================================

def _db():
    if not os.path.exists(STOCK_DB):
        return None
    conn = sqlite3.connect(STOCK_DB, timeout=30.0)
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _query_daily(conn, trade_date: str) -> Optional[pd.DataFrame]:
    """取全市场某交易日日线（复用现有 daily_cache，不重复下载）。"""
    if conn is None:
        return None
    try:
        df = pd.read_sql_query(
            "SELECT ts_code, trade_date, pct_chg, amount, close, vol "
            "FROM daily_cache WHERE trade_date = ?", conn, params=(str(trade_date),))
        return df if not df.empty else None
    except Exception:
        return None


def _limit_thresholds(ts_code: str) -> float:
    """涨跌停幅度：主板 10%，双创（688/300/301）20%。ST 股 5% 未在 daily_cache 中标记 → 记为 UNKNOWN 风险。"""
    code = str(ts_code)
    if code.startswith(('688', '300', '301', '8', '92')):
        return 20.0
    return 10.0


def _count_limit(df: pd.DataFrame) -> Dict[str, Any]:
    """按收盘价判定涨跌停。ST 股(5%)与北交所(30%)无法从 daily_cache 区分 → 口径说明随结果返回。"""
    if df is None or df.empty:
        return {}
    thr = df['ts_code'].map(_limit_thresholds)
    pct = df['pct_chg'].astype(float)
    zt = int(((pct >= thr * 0.99) & (pct < thr + 0.1)).sum())
    dt = int(((pct <= -thr * 0.99) & (pct > -thr - 0.1)).sum())
    return {"zt_count": zt, "dt_count": dt}


def _query_index_hist(conn, code: str, end_date: str, lookback_days: int = 420) -> Optional[pd.DataFrame]:
    """取指数历史序列（复用 index_daily_cache）。每个指数独立取，不互相替代。"""
    if conn is None:
        return None
    try:
        start = (datetime.strptime(str(end_date), "%Y%m%d")
                 - timedelta(days=lookback_days)).strftime("%Y%m%d")
        df = pd.read_sql_query(
            "SELECT ts_code, trade_date, close, pct_chg, vol, amount "
            "FROM index_daily_cache WHERE ts_code = ? AND trade_date >= ? AND trade_date <= ? "
            "ORDER BY trade_date",
            conn, params=(code, start, str(end_date)))
        return df if not df.empty else None
    except Exception:
        return None


def _hist_slice(hist_pack: Optional[dict], key: str, trade_date: str,
                lookback: int) -> List[float]:
    """从预计算历史包取截至 trade_date 的最近 lookback 个值（升序）。

    未提供 hist_pack 时返回空列表（调用方负责回落到实时查询）。
    """
    if not hist_pack:
        return []
    src = hist_pack.get(key) or {}
    dates = sorted([d for d in src if d <= str(trade_date)])
    vals = [src[d] for d in dates[-lookback:]]
    return [v for v in vals if v is not None]


def _percentile_rank(hist: List[float], value: float) -> Optional[float]:
    """value 在 hist 中的分位（0~100）。样本不足返回 None。"""
    if not hist or value is None or value != value:
        return None
    s = [h for h in hist if h is not None and h == h]
    if len(s) < 20:
        return None
    return float(sum(1 for h in s if h <= value) / len(s) * 100)


def _compute_index_metrics(df: Optional[pd.DataFrame], name: str, code: str,
                           cfg: dict) -> IndexData:
    """计算单个指数的趋势/位置指标。"""
    out = IndexData(name=name, code=code)
    if df is None or len(df) < 20:
        out.percentile_note = "历史不足，位置分位 UNKNOWN"
        return out

    d = df.reset_index(drop=True)
    out.bars_available = len(d)
    out.close = float(d['close'].iloc[-1])
    out.pct_chg = float(d['pct_chg'].iloc[-1]) if 'pct_chg' in d.columns else None

    c = d['close'].astype(float)
    ma20w = cfg["index_trend"]["ma20_window"]
    ma60w = cfg["index_trend"]["ma60_window"]
    if len(c) >= ma20w:
        out.ma20 = float(c.rolling(ma20w).mean().iloc[-1])
    if len(c) >= ma60w:
        out.ma60 = float(c.rolling(ma60w).mean().iloc[-1])
    if out.ma20:
        out.above_ma20 = bool(out.close > out.ma20)
    if out.ma60:
        out.above_ma60 = bool(out.close > out.ma60)
    if len(c) >= 21:
        out.ret5 = float((c.iloc[-1] / c.iloc[-6] - 1) * 100)
        out.ret20 = float((c.iloc[-1] / c.iloc[-21] - 1) * 100)
        w = c.tail(20)
        out.max_drawdown_20d = float((w / w.cummax() - 1).min() * 100)

    # 是否接近近期低点
    lb = cfg["index_trend"]["near_low_lookback"]
    near_pct = cfg["index_trend"]["near_low_pct"]
    if len(c) >= lb:
        low = float(c.tail(lb).min())
        out.near_low = bool(out.close <= low * (1 + near_pct / 100.0))

    # 250 日收盘分位 —— 口径：收盘价在最近 N 个交易日 [最低,最高] 区间中的位置
    pl = cfg["position_percentile"]["close_lookback"]
    min_bars = cfg["position_percentile"]["min_bars"]
    if len(c) >= min_bars:
        w = c.tail(pl)
        lo, hi = float(w.min()), float(w.max())
        if hi > lo:
            out.close_percentile_250d = float((out.close - lo) / (hi - lo) * 100)
            out.percentile_note = (f"收盘价在最近 {pl} 个交易日[最低{lo:.2f}, 最高{hi:.2f}]"
                                  f"区间中的位置（样本 {len(w)} 根）")
        else:
            out.percentile_note = f"近 {pl} 日无波动，分位不可计算"
    else:
        out.percentile_note = f"仅 {len(c)} 根K线 < {min_bars}，250日分位 UNKNOWN"

    # 近期回撤幅度的历史分位 —— 口径：当前20日最大回撤在过去 N 日各20日回撤中的分位
    dl = cfg["position_percentile"]["drawdown_lookback"]
    if len(c) >= 60:
        roll_mdd = (c.rolling(20).max().sub(c) / c.rolling(20).max()).abs().dropna()
        hist_mdd = (roll_mdd * 100).tail(dl).tolist()
        cur_mdd = out.max_drawdown_20d
        if cur_mdd is not None and hist_mdd:
            out.drawdown_percentile = _percentile_rank(hist_mdd, abs(cur_mdd))
    return out


def _collect_breadth_history(conn, end_date: str, days: int = 250) -> List[float]:
    """取历史广度序列（供分位计算）。daily_cache 已缓存，无需重新下载。"""
    if conn is None:
        return []
    try:
        days_list = [r[0] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM daily_cache WHERE trade_date <= ? "
            "ORDER BY trade_date DESC LIMIT ?", (str(end_date), days))]
    except Exception:
        return []
    out = []
    for d in sorted(days_list):
        df = _query_daily(conn, d)
        if df is None or df.empty:
            continue
        up = int((df['pct_chg'] > 0).sum())
        dn = int((df['pct_chg'] < 0).sum())
        if up + dn > 0:
            out.append(up / (up + dn) * 100)
    return out


def _collect_amount_history(conn, end_date: str, days: int = 60) -> List[float]:
    """取历史全市场成交额序列（亿元）。"""
    if conn is None:
        return []
    try:
        days_list = [r[0] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM daily_cache WHERE trade_date <= ? "
            "ORDER BY trade_date DESC LIMIT ?", (str(end_date), days))]
    except Exception:
        return []
    out = []
    for d in sorted(days_list):
        df = _query_daily(conn, d)
        if df is None or df.empty:
            continue
        out.append(float(df['amount'].sum()) / 100000.0)
    return out


def _collect_limit_history(conn, end_date: str, days: int = 250) -> List[float]:
    """取历史跌停家数序列（供分位计算）。"""
    if conn is None:
        return []
    try:
        days_list = [r[0] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM daily_cache WHERE trade_date <= ? "
            "ORDER BY trade_date DESC LIMIT ?", (str(end_date), days))]
    except Exception:
        return []
    out = []
    for d in sorted(days_list):
        df = _query_daily(conn, d)
        if df is None or df.empty:
            continue
        r = _count_limit(df)
        if r:
            out.append(float(r["dt_count"]))
    return out


def collect_market_snapshot(trade_date: str, cfg: dict,
                            prev_snapshot: Optional[MarketSnapshot] = None,
                            hist_pack: Optional[dict] = None) -> MarketSnapshot:
    """采集某交易日的市场快照。

    全部指标口径：
      · 广度     = 上涨家数 / (上涨家数 + 下跌家数)，平盘家数单列
      · 涨跌停   = 收盘价判定，主板 10% / 双创 20%（ST 股 5% 无法从 daily_cache 识别）
      · 炸板率   = 炸板 / (封住 + 炸板)，炸板来自 tushare limit_list_d（不可得时 UNKNOWN）
      · 成交额   = 全市场 daily_cache amount 求和（千元→亿元）
      · 位置分位 = 收盘价在最近 N 个交易日 [最低,最高] 区间中的位置

    hist_pack: 可选的预计算历史序列 {'breadth': {date: ratio}, 'amount': {date: yi},
    'dt': {date: count}}，批量回放时注入以避免逐日重复扫库。
    """
    snap = MarketSnapshot(trade_date=str(trade_date))
    snap.prev_snapshot = prev_snapshot
    conn = _db()
    if conn is None:
        snap.quality_flags.append("daily_cache 不可用，全部指标 UNKNOWN")
        return snap

    try:
        df = _query_daily(conn, trade_date)
        if df is None or df.empty:
            snap.quality_flags.append(f"{trade_date} 无全市场日线数据")
            return snap

        # ---------- 广度 ----------
        b = snap.breadth
        b.up_count = int((df['pct_chg'] > 0).sum())
        b.down_count = int((df['pct_chg'] < 0).sum())
        b.flat_count = int((df['pct_chg'] == 0).sum())
        b.total = int(len(df))
        if b.up_count + b.down_count > 0:
            b.breadth_ratio = b.up_count / (b.up_count + b.down_count)
            b.total_based_ratio = b.up_count / b.total if b.total else None
        b._hist = (_hist_slice(hist_pack, "breadth", trade_date,
                               cfg["position_percentile"]["close_lookback"])
                   if hist_pack else
                   _collect_breadth_history(conn, trade_date,
                                            cfg["position_percentile"]["close_lookback"]))
        if b.breadth_ratio is not None and b._hist:
            b.breadth_percentile = _percentile_rank(b._hist, b.breadth_ratio * 100)

        # ---------- 涨跌停 ----------
        lim = snap.limit
        lc = _count_limit(df)
        if lc:
            lim.zt_count = lc["zt_count"]
            lim.dt_count = lc["dt_count"]
        lim._dt_hist = (_hist_slice(hist_pack, "dt", trade_date,
                                    cfg["position_percentile"]["close_lookback"])
                        if hist_pack else
                        _collect_limit_history(conn, trade_date,
                                               cfg["position_percentile"]["close_lookback"]))
        if lim.dt_count is not None and lim._dt_hist:
            lim.dt_percentile = _percentile_rank(lim._dt_hist, float(lim.dt_count))

        # ---------- 炸板（tushare limit_list_d）----------
        try:
            from dotenv import load_dotenv
            load_dotenv(os.path.join(os.path.dirname(BASE_DIR), 'config', '.env'))
            import tushare as ts
            ts.set_token(os.getenv('TUSHARE_TOKEN'))
            pro = ts.pro_api()
            ldf = pro.limit_list_d(trade_date=str(trade_date))
            if ldf is not None and not ldf.empty:
                zb = ldf[ldf['limit'] == 'Z']['ts_code'].astype(str).tolist()
                lim.zhaban_count = len(zb)
                if lim.zt_count is not None:
                    tot = lim.zt_count + lim.zhaban_count
                    if tot > 0:
                        lim.broken_rate = lim.zhaban_count / tot * 100
        except Exception as e:
            snap.quality_flags.append(f"炸板数据不可得({e}) → broken_rate UNKNOWN")

        # ---------- 昨日涨停股的今日表现 + 连板晋级率 ----------
        try:
            from dotenv import load_dotenv
            load_dotenv(os.path.join(os.path.dirname(BASE_DIR), 'config', '.env'))
            import tushare as ts
            ts.set_token(os.getenv('TUSHARE_TOKEN'))
            pro = ts.pro_api()
            cal = pro.trade_cal(exchange='', start_date='20240101', end_date=str(trade_date))
            open_days = sorted(cal[cal['is_open'] == 1]['cal_date'].astype(str).tolist())
            prior = [x for x in open_days if x < str(trade_date)]
            if prior:
                yday = prior[-1]
                ydf = _query_daily(conn, yday)
                if ydf is not None and not ydf.empty:
                    ylc = _count_limit(ydf)
                    y_zt = set(ydf[((ydf['pct_chg'] >= ydf['ts_code'].map(_limit_thresholds) * 0.99)
                                    & (ydf['pct_chg'] < ydf['ts_code'].map(_limit_thresholds) + 0.1))
                                   ]['ts_code'].astype(str).tolist())
                    if y_zt:
                        today = df[df['ts_code'].astype(str).isin(y_zt)]
                        if not today.empty:
                            pcts = today['pct_chg'].astype(float)
                            lim.strong_stock_count = int(len(pcts))
                            lim.strong_stock_mean_pct = float(pcts.mean())
                            lim.strong_stock_up_ratio = float((pcts > 0).mean())
                            # 连板晋级率：昨日涨停中今日继续涨停
                            thr_now = today['ts_code'].map(_limit_thresholds)
                            continued = int(((pcts >= thr_now * 0.99) & (pcts < thr_now + 0.1)).sum())
                            lim.promotion_rate = continued / len(y_zt) * 100
                    lim.prev_zt_count = ylc.get("zt_count")
                    ylc2 = _count_limit(ydf)
                    lim.prev_dt_count = ylc2.get("dt_count")
        except Exception as e:
            snap.quality_flags.append(f"昨日涨停股表现不可得({e}) → UNKNOWN")

        if lim.dt_count is not None and lim.prev_dt_count:
            lim.dt_change_ratio = lim.dt_count / lim.prev_dt_count if lim.prev_dt_count else None

        # ---------- 成交额 ----------
        am = snap.amount
        am._hist = (_hist_slice(hist_pack, "amount", trade_date, 60)
                    if hist_pack else _collect_amount_history(conn, trade_date, 60))
        am.total_amount = float(df['amount'].sum()) / 100000.0
        if len(am._hist) >= 2:
            am.prev_amount = am._hist[-2]
            if am.prev_amount:
                am.chg_vs_prev = (am.total_amount / am.prev_amount - 1) * 100
            if len(am._hist) >= 6:
                ma5 = float(np.mean(am._hist[-6:-1]))
                if ma5:
                    am.chg_vs_ma5 = (am.total_amount / ma5 - 1) * 100
            if len(am._hist) >= 21:
                ma20 = float(np.mean(am._hist[-21:-1]))
                if ma20:
                    am.chg_vs_ma20 = (am.total_amount / ma20 - 1) * 100

        # ---------- 指数 ----------
        for nm, code in INDEX_LIST:
            h = _query_index_hist(conn, code, trade_date, 420)
            snap.indexes[nm] = _compute_index_metrics(h, nm, code, cfg)

        core = snap.indexes.get(CORE_INDEX)
        bench = snap.indexes.get(BENCH_INDEX)
        if core and bench and core.pct_chg is not None and bench.pct_chg is not None:
            snap.small_vs_large_gap = core.pct_chg - bench.pct_chg

        # ---------- 量价形态 ----------
        if am.total_amount is not None and core and core.pct_chg is not None:
            am.index_chg = core.pct_chg
            if am.chg_vs_ma5 is not None:
                expand = am.chg_vs_ma5 > 0
                if expand and core.pct_chg > 0:
                    am.pattern = "放量上涨"
                elif expand and core.pct_chg < 0:
                    am.pattern = "放量下跌"
                else:
                    am.pattern = "缩量上涨" if core.pct_chg > 0 else "缩量下跌"
            else:
                am.pattern = UNKNOWN

        # 前一日快照的广度对比
        if prev_snapshot and prev_snapshot.breadth.breadth_ratio is not None \
                and b.breadth_ratio is not None:
            b.prev_breadth_ratio = prev_snapshot.breadth.breadth_ratio
            b.delta_pp = (b.breadth_ratio - b.prev_breadth_ratio) * 100

    finally:
        conn.close()

    return snap


# ============================================================
# 节点识别
# ============================================================

def detect_market_nodes(snap: MarketSnapshot, cfg: dict) -> NodeResult:
    """多指标联合判断市场节点日。

    每个节点都要求命中若干项条件，且输出具体证据。
    特别地：**极端低广度 / 低位置分位不会被单独当作底部信号**，
    它们只作为「极端情绪」节点的证据之一。
    """
    res = NodeResult()
    b, lim, am = snap.breadth, snap.limit, snap.amount
    # 前一日核心指数涨跌幅（用于「跌幅收窄」这类变化量判定）
    prev_core_chg = None
    if snap.prev_snapshot is not None:
        _pc = snap.prev_snapshot.indexes.get(CORE_INDEX)
        if _pc is not None:
            prev_core_chg = _pc.pct_chg

    # ---------- A. 恐慌加速 ----------
    panic_hits = []
    if b.breadth_ratio is not None and b.breadth_ratio < cfg["breadth"]["panic_ratio"]:
        panic_hits.append(f"市场广度 {b.breadth_ratio*100:.1f}% < {cfg['breadth']['panic_ratio']*100:.0f}%")
    if lim.dt_change_ratio is not None and lim.dt_change_ratio >= cfg["limit_down"]["panic_increase_ratio"]:
        panic_hits.append(f"跌停家数较前日放大 {lim.dt_change_ratio:.2f} 倍（{lim.prev_dt_count}→{lim.dt_count}）")
    elif lim.dt_count is not None and lim.dt_count >= cfg["limit_down"]["panic_abs_count"]:
        panic_hits.append(f"跌停家数 {lim.dt_count} 只，达恐慌阈值 {cfg['limit_down']['panic_abs_count']}")
    if (am.chg_vs_ma20 is not None and am.index_chg is not None and am.index_chg < 0
            and am.chg_vs_ma20 > (cfg["amount"]["panic_expand_ratio"] - 1) * 100):
        panic_hits.append(f"指数下跌 {am.index_chg:.2f}% 且成交额较20日均额放大 {am.chg_vs_ma20:.1f}%")
    if snap.small_vs_large_gap is not None and snap.small_vs_large_gap < cfg["index_relative"]["panic_gap_pct"]:
        panic_hits.append(f"小盘显著弱于大盘 {snap.small_vs_large_gap:.2f}个百分点")
    if lim.strong_stock_mean_pct is not None and lim.strong_stock_mean_pct < cfg["strong_stock"]["extreme_mean_pct"]:
        panic_hits.append(f"昨日涨停股今日平均 {lim.strong_stock_mean_pct:.2f}%，负反馈显著")

    # ---------- B. 极端情绪 ----------
    extreme_hits = []
    if b.breadth_percentile is not None and b.breadth_percentile <= cfg["breadth"]["extreme_percentile"] * 100:
        extreme_hits.append(f"广度处于历史 {b.breadth_percentile:.1f}% 分位（极端低位）")
    if lim.dt_percentile is not None and lim.dt_percentile >= cfg["limit_down"]["high_percentile"] * 100:
        extreme_hits.append(f"跌停家数处于历史 {lim.dt_percentile:.1f}% 分位（高位）")
    if lim.broken_rate is not None and lim.broken_rate >= 60:
        extreme_hits.append(f"炸板率 {lim.broken_rate:.1f}%，处于异常高位")
    if lim.promotion_rate is not None and lim.promotion_rate <= cfg["promotion_rate"]["extreme_percentile"] * 100:
        extreme_hits.append(f"连板晋级率 {lim.promotion_rate:.1f}%，处于历史低位")
    if lim.strong_stock_mean_pct is not None and lim.strong_stock_mean_pct < cfg["strong_stock"]["extreme_mean_pct"]:
        extreme_hits.append(f"昨日涨停股今日平均 {lim.strong_stock_mean_pct:.2f}%")

    # ---------- C. 卖压衰减（至少两个交易日的变化） ----------
    easing_hits = []
    if lim.dt_count is not None and lim.prev_dt_count is not None and lim.prev_dt_count > 0:
        if lim.dt_count <= lim.prev_dt_count * cfg["limit_down"]["easing_decrease_ratio"]:
            easing_hits.append(f"跌停家数 {lim.prev_dt_count}→{lim.dt_count}，明显减少")
        elif (lim.prev_dt_count - lim.dt_count) >= cfg["limit_down"]["easing_abs_decrease"]:
            easing_hits.append(f"跌停家数 {lim.prev_dt_count}→{lim.dt_count}，减少 {lim.prev_dt_count-lim.dt_count} 只")
    if b.delta_pp is not None and b.delta_pp >= cfg["breadth"]["easing_rebound_pp"]:
        easing_hits.append(f"广度较前日回升 {b.delta_pp:.1f} 个百分点")
    core = snap.indexes.get(CORE_INDEX)
    # ★ 只用「变化量」作为卖压衰减证据。接近近期低点属于「状态」而非「改善」，
    #   熊市中天天成立，会把卖压衰减节点变成常态 —— 已剔除。
    if core and core.pct_chg is not None and prev_core_chg is not None \
            and core.pct_chg > prev_core_chg and core.pct_chg >= 0:
        easing_hits.append(f"{CORE_INDEX} 跌幅收窄（昨{prev_core_chg:+.2f}%→今{core.pct_chg:+.2f}%）")
    if lim.strong_stock_mean_pct is not None and lim.strong_stock_mean_pct < 0 \
            and lim.strong_stock_mean_pct >= cfg["strong_stock"]["easing_mean_pct"]:
        easing_hits.append(f"昨日涨停股平均 {lim.strong_stock_mean_pct:.2f}%（负反馈减弱但仍为负）")
    if lim.broken_rate is not None and lim.broken_rate <= cfg["broken_rate"]["easing_max"]:
        easing_hits.append(f"炸板率 {lim.broken_rate:.1f}% ≤ {cfg['broken_rate']['easing_max']*100:.0f}%")

    # ---------- D. 修复确认（指数 + 广度 + 短线情绪 三方面共同改善） ----------
    repair_hits = []
    trend_ok = False
    if core:
        # ★ 只认「站上 MA20 且当日收涨」这一条 —— 「近5日累计转正」在震荡市太常见，
        #   会把单日脉冲误判为趋势性修复。
        if core.above_ma20 and (core.pct_chg or 0) > 0:
            trend_ok = True
            repair_hits.append(f"{CORE_INDEX} 收涨 {core.pct_chg:.2f}% 且站上 MA20（MA20={core.ma20:.2f}）")
        elif core.pct_chg is not None and core.pct_chg > 0 and core.above_ma60:
            trend_ok = True
            repair_hits.append(f"{CORE_INDEX} 收涨 {core.pct_chg:.2f}% 且站上 MA60")
    breadth_ok = False
    if b.breadth_ratio is not None and b.delta_pp is not None:
        # ★ 必须同时满足「水平够高」与「有回升」：
        #   只看 delta 会把单日暴涨误判为修复（历史检验：单日反弹后 5 日广度回落 22pp）
        if b.breadth_ratio >= cfg["breadth"]["repair_ratio"] and b.delta_pp >= cfg["breadth"]["repair_delta_pp"]:
            breadth_ok = True
            repair_hits.append(f"广度回升至 {b.breadth_ratio*100:.1f}%（较前日 +{b.delta_pp:.1f}pp）")
    # ★ 短线情绪：跌停减少是**必要**条件，另需至少一条情绪改善佐证。
    #   原实现是 4 个 OR 分支任一满足即可 —— 「炸板率≤30%」在震荡市几乎天天成立，
    #   导致「修复确认」几乎天天触发（历史检验：修复后5日广度反回落 22pp）。
    dt_cut = (lim.dt_count is not None and lim.prev_dt_count is not None
              and lim.prev_dt_count > 0
              and lim.dt_count <= lim.prev_dt_count * cfg["limit_down"]["repair_decrease_ratio"])
    sentiment_ok = False
    if dt_cut:
        repair_hits.append(f"跌停家数 {lim.prev_dt_count}→{lim.dt_count}，明显减少")
        if lim.broken_rate is not None and lim.broken_rate <= cfg["broken_rate"]["repair_max"]:
            sentiment_ok = True
            repair_hits.append(f"炸板率 {lim.broken_rate:.1f}% ≤ {cfg['broken_rate']['repair_max']*100:.0f}%")
        if lim.strong_stock_mean_pct is not None and lim.strong_stock_mean_pct >= cfg["strong_stock"]["repair_mean_pct"]:
            sentiment_ok = True
            repair_hits.append(f"昨日涨停股今日平均 {lim.strong_stock_mean_pct:+.2f}%，负反馈转正")
        if lim.promotion_rate is not None and lim.promotion_rate >= cfg["promotion_rate"]["repair_min"] * 100:
            sentiment_ok = True
            repair_hits.append(f"连板晋级率 {lim.promotion_rate:.1f}%")

    res.hit_detail = {
        NODE_PANIC: panic_hits, NODE_EXTREME: extreme_hits,
        NODE_EASING: easing_hits, NODE_REPAIR: repair_hits,
    }

    # ---------- 节点裁决 ----------
    nc = cfg["nodes"]
    if len(panic_hits) >= nc["panic_min_hits"]:
        res.is_node_day, res.node_type, res.node_cn = True, NODE_PANIC, NODE_CN[NODE_PANIC]
        res.evidence = panic_hits[:5]
        res.confidence = "高" if len(panic_hits) >= nc["panic_min_hits"] + 1 else "中"
    elif len(repair_hits) >= nc["repair_min_hits"] and trend_ok and breadth_ok and sentiment_ok:
        # 修复确认要求三个方面同时成立
        res.is_node_day, res.node_type, res.node_cn = True, NODE_REPAIR, NODE_CN[NODE_REPAIR]
        res.evidence = repair_hits[:5]
        res.confidence = "高" if len(repair_hits) >= nc["repair_min_hits"] + 1 else "中"
    elif len(easing_hits) >= nc["easing_min_hits"]:
        res.is_node_day, res.node_type, res.node_cn = True, NODE_EASING, NODE_CN[NODE_EASING]
        res.evidence = easing_hits[:5]
        res.confidence = "中" if len(easing_hits) >= nc["easing_min_hits"] + 1 else "低"
    elif len(extreme_hits) >= nc["extreme_min_hits"]:
        res.is_node_day, res.node_type, res.node_cn = True, NODE_EXTREME, NODE_CN[NODE_EXTREME]
        res.evidence = extreme_hits[:5]
        res.confidence = "中"

    # 不确定因素
    unc = []
    if not res.is_node_day:
        unc.append("未达到任一节点的联合命中阈值")
    if snap.quality_flags:
        unc.extend(snap.quality_flags[:2])
    if b.breadth_percentile is None:
        unc.append("广度历史分位 UNKNOWN（历史样本不足）")
    if res.node_type == NODE_PANIC:
        unc.append("恐慌加速≠底部：需等待卖压衰减与修复确认")
    if res.node_type == NODE_EXTREME:
        unc.append("极端情绪≠底部≠买点，仅提高观察优先级")
    if res.node_type == NODE_REPAIR:
        unc.append("修复确认需连续2日验证，单日不足")
    res.uncertainties = unc[:3]

    return res


# ============================================================
# 五状态机
# ============================================================

def _state_db():
    conn = sqlite3.connect(STATE_DB, timeout=30.0)
    conn.execute("""CREATE TABLE IF NOT EXISTS regime_state (
        trade_date TEXT PRIMARY KEY,
        state TEXT, prev_state TEXT, consecutive_days INTEGER,
        confidence TEXT, reasons TEXT, triggers TEXT, node_type TEXT,
        pending_target TEXT, pending_days INTEGER)""")
    conn.commit()
    return conn


def load_prev_state(trade_date: str):
    """读取上一交易日的状态记录。"""
    if not os.path.exists(STATE_DB):
        return None
    conn = sqlite3.connect(STATE_DB, timeout=30.0)
    try:
        cur = conn.cursor()
        cur.execute("SELECT trade_date, state, consecutive_days, confidence, reasons, "
                    "triggers, node_type, pending_target, pending_days "
                    "FROM regime_state WHERE trade_date < ? "
                    "ORDER BY trade_date DESC LIMIT 1", (str(trade_date),))
        row = cur.fetchone()
        if not row:
            return None
        return {"trade_date": row[0], "state": row[1], "consecutive_days": row[2],
                "confidence": row[3], "reasons": json.loads(row[4]) if row[4] else [],
                "triggers": json.loads(row[5]) if row[5] else [], "node_type": row[6],
                "pending_target": row[7], "pending_days": row[8] or 0}
    finally:
        conn.close()


def save_state(trade_date: str, dec: RegimeDecision, node_type: str):
    conn = _state_db()
    try:
        cur = conn.cursor()
        cur.execute("INSERT OR REPLACE INTO regime_state "
                    "(trade_date, state, prev_state, consecutive_days, confidence, reasons, "
                    "triggers, node_type, pending_target, pending_days) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (str(trade_date), dec.state, dec.prev_state, dec.consecutive_days,
                     dec.confidence, json.dumps(dec.reasons, ensure_ascii=False),
                     json.dumps(dec.triggers, ensure_ascii=False), node_type,
                     dec.pending_target, dec.pending_days))
        conn.commit()
    finally:
        conn.close()


def decide_state(snap: MarketSnapshot, node: NodeResult, prev: Optional[dict],
                 cfg: dict) -> RegimeDecision:
    """五状态机入口（统一补齐 state_cn，避免各分支遗漏）。"""
    dec = _decide_state_inner(snap, node, prev, cfg)
    dec.state_cn = STATE_CN.get(dec.state, dec.state)
    return dec


def _decide_state_inner(snap: MarketSnapshot, node: NodeResult, prev: Optional[dict],
                        cfg: dict) -> RegimeDecision:
    """五状态机核心（统一转移模型）。

    每日先算出「今日应处于的状态」target，再用连续天数确认决定是否真的切换：
      · 同一状态 → 直接保持，连续天数 +1
      · 切出风险态（S3/S4/S5）→ 需连续 exit_days 日无风险节点
        （风险解除不能仅依赖单日反弹）
      · 其余切换 → 需连续 confirm_days 日匹配
      · S3 恐慌加速 confirm_days=1 → 允许极端行情快速风险升级

    确认计数用 pending_target + pending_days 持久化。
    ★ 早期版本用互斥的 if/elif 分支，「进入」分支总是先于「退出」分支命中，
      导致退出计数被打断、状态一旦进入就永远无法退出
      （历史检验：S1 仅占 3%、S2 从未出现）。现改为统一模型。
    """
    dec = RegimeDecision()
    sm = cfg["state_machine"]
    node_type = node.node_type

    prev_state = prev["state"] if prev else STATE_NORMAL
    prev_days = prev["consecutive_days"] if prev else 0
    prev_pending_target = prev.get("pending_target") if prev else None
    prev_pending_days = (prev.get("pending_days") or 0) if prev else 0
    dec.prev_state = prev_state
    dec.consecutive_days = prev_days

    RISK_STATES = (STATE_PANIC, STATE_EASING, STATE_REPAIR)

    # ---------- 1) 今日应处于的状态 ----------
    node_target = {
        NODE_PANIC: STATE_PANIC,
        NODE_REPAIR: STATE_REPAIR,
        NODE_EASING: STATE_EASING,
    }.get(node_type)
    env_target = STATE_DRAWDOWN if _is_drawdown(snap, cfg) else STATE_NORMAL

    if node_target is not None:
        target = node_target
    else:
        target = env_target

    # ---------- 2) 需要的确认天数 ----------
    if target == prev_state:
        need = 1
    elif prev_state in RISK_STATES and target not in RISK_STATES:
        need = sm["exit_days"].get(prev_state, 2)     # 切出风险态要更谨慎
    elif target == STATE_PANIC:
        need = 1                                     # 快速风险升级
    else:
        need = sm["confirm_days"].get(target, 1)

    # ---------- 3) 状态转移 ----------
    if target == prev_state:
        dec.state = prev_state
        dec.consecutive_days = prev_days + 1
        dec.changed = False
        dec.pending_target = ""
        dec.pending_days = 0
        if node_target is not None:
            dec.triggers = node.evidence[:3]
            dec.reasons.append(f"{STATE_CN.get(target, target)}持续")
            dec.confidence = node.confidence
        else:
            dec.reasons.append(f"维持 {STATE_CN.get(prev_state, prev_state)}")
            dec.confidence = "中"
        return dec

    # 需要确认：累计连续匹配天数
    if prev_pending_target == target:
        hits = prev_pending_days + 1
    else:
        hits = 1

    if hits >= need:
        dec.state = target
        dec.consecutive_days = 1
        dec.changed = True
        dec.pending_target = ""
        dec.pending_days = 0
        if node_target is not None:
            dec.triggers = node.evidence[:3]
            dec.reasons.append(
                f"连续{hits}日满足 {STATE_CN.get(target, target)} 条件"
                + ("（允许极端行情快速风险升级）" if target == STATE_PANIC else ""))
            dec.confidence = node.confidence
        else:
            dec.reasons.append(f"市场环境回落至 {STATE_CN.get(target, target)}")
            dec.confidence = "中"
        return dec

    # 尚未确认 → 维持原状态，记录待确认
    dec.state = prev_state
    dec.consecutive_days = prev_days
    dec.changed = False
    dec.pending_target = target
    dec.pending_days = hits
    dec.triggers = node.evidence[:2]
    dec.reasons.append(
        f"{STATE_CN.get(target, target)}信号第{hits}次出现"
        f"（需连续{need}日确认），暂维持 {STATE_CN.get(prev_state, prev_state)}")
    dec.confidence = "低"
    return dec


def _is_drawdown(snap: MarketSnapshot, cfg: dict) -> bool:
    """退潮判定：市场广度明显低于正常水位。

    ★ 修正：原实现把「核心指数跌破 MA20」也作为退潮判据。2026 年 1-10 月中证2000
      长期位于 MA20 下方，导致几乎每天都被判为退潮候选，S1/S2 几乎无法出现
      （历史检验：S1 仅占 3%、S2 从未出现）。现只保留「广度低于正常水位」这一条；
      「极低广度」本身属于 S3/S4 的范畴，由节点优先级链处理。
    """
    b = snap.breadth
    # ★ 用「低于正常水位」而非恐慌线：0.35 是 S3 的阈值，用在这里会让
    #   S2 退潮几乎无法出现（广度一低通常同时触发恐慌/极端节点）。
    br = cfg["breadth"]["normal_lower_ratio"]
    return b.breadth_ratio is not None and b.breadth_ratio < br


# ============================================================
# 仓位建议
# ============================================================

def suggest_position(dec: RegimeDecision, snap: MarketSnapshot,
                     actual_position: Optional[int], cfg: dict) -> PositionAdvice:
    """生成仓位建议。严格区分：目标仓位 / 实际仓位 / 允许增加的仓位空间。

    ⚠ 目标仓位是「系统建议」，不是「必须买入」。个股不满足原有买入条件时，
       实际可以持有 0%。
    """
    pa = PositionAdvice()
    pa.state = dec.state
    spec = cfg["position_advice"].get(dec.state, cfg["position_advice"]["S1"])
    pa.target_position = spec.get("target")
    pa.action = spec.get("action", "")
    pa.allow_probe = bool(spec.get("allow_probe", False))
    pa.actual_position = actual_position

    # 加仓空间 = 目标 - 实际（实际未提供时为 UNKNOWN）
    if pa.target_position is not None and actual_position is not None:
        pa.add_room = max(0, pa.target_position - actual_position)
        if actual_position > pa.target_position:
            pa.excess_note = (f"实际仓位 {actual_position}% 高于建议目标 {pa.target_position}%，"
                             f"超出 {actual_position - pa.target_position} 个百分点，"
                             f"建议按原有风险规则评估减仓（本模块不执行任何交易）")
    elif pa.target_position is not None:
        pa.add_room = None    # 实际仓位未提供 → 加仓空间 UNKNOWN

    # 加仓必须满足的条件
    pa.add_conditions = [
        "个股原有买入信号成立（环境状态不替代个股信号）",
    ]
    if dec.state == STATE_REPAIR:
        pa.add_conditions.append("修复连续性得到验证（连续2日）")
        pa.add_conditions.append("跌停家数与炸板率同步改善")
    elif dec.state == STATE_EASING:
        pa.add_conditions.append("卖压衰减需再确认一日，且不降低原有买入标准")
    elif dec.state == STATE_NORMAL:
        pa.add_conditions.append("无额外限制，按原有策略执行")
    elif dec.state == STATE_PANIC:
        pa.add_conditions.append("需先出现卖压衰减信号，不得仅因跌幅大而买入")
    elif dec.state == STATE_DRAWDOWN:
        pa.add_conditions.append("需广度与跌停结构同时改善")

    pa.reduce_conditions = [
        "跌停家数继续增加或广度跌破关键位置",
        "市场状态由 S4/S5 退回 S2/S3",
        "个股原有止损或退出条件触发",
    ]
    return pa


# ============================================================
# 报告渲染
# ============================================================

def render_v10_report(snap: MarketSnapshot, node: NodeResult, dec: RegimeDecision,
                       pa: PositionAdvice, cfg: dict) -> str:
    L = []
    b, lim, am = snap.breadth, snap.limit, snap.amount
    core = snap.indexes.get(CORE_INDEX)
    bench = snap.indexes.get(BENCH_INDEX)

    L.append("═" * 46)
    L.append("      A股短线市场环境判断  V10.0")
    L.append("═" * 46)

    # 【市场节点判断】
    L.append("")
    L.append("【市场节点判断】")
    L.append(f"  节点日：{'是' if node.is_node_day else '否'}")
    L.append(f"  节点类型：{node.node_cn}（{node.node_type if node.is_node_day else '无'}）")
    L.append(f"  当前市场状态：{dec.state} {dec.state_cn}")
    L.append(f"  状态持续时间：{dec.consecutive_days} 个交易日")
    L.append(f"  状态置信度：{dec.confidence}")
    if node.evidence:
        L.append("  核心证据：")
        for i, e in enumerate(node.evidence[:5], 1):
            L.append(f"    {i}. {e}")
    if node.uncertainties:
        L.append("  不确定因素：")
        for i, u in enumerate(node.uncertainties[:3], 1):
            L.append(f"    {i}. {u}")
    if dec.reasons:
        L.append("  状态判定依据：")
        for r in dec.reasons[:3]:
            L.append(f"    · {r}")
    if dec.triggers:
        L.append("  状态触发指标：")
        for t in dec.triggers[:3]:
            L.append(f"    · {t}")

    # 【市场广度与情绪】
    L.append("")
    L.append("【市场广度与情绪】")
    L.append(f"  上涨家数占比：{b.breadth_ratio*100:.1f}%" if b.breadth_ratio is not None
             else "  上涨家数占比：UNKNOWN")
    if b.delta_pp is not None:
        L.append(f"    较前一交易日：{b.delta_pp:+.1f} 个百分点")
    if b.breadth_percentile is not None:
        L.append(f"    历史分位：{b.breadth_percentile:.1f}%（近250日口径）")
    L.append(f"  上涨/下跌/平盘：{b.up_count} / {b.down_count} / {b.flat_count}"
             f"（总 {b.total}，平盘不计入广度分母）")
    L.append(f"  涨停家数：{lim.zt_count if lim.zt_count is not None else 'UNKNOWN'}")
    L.append(f"  跌停家数：{lim.dt_count if lim.dt_count is not None else 'UNKNOWN'}"
             + (f"（前日 {lim.prev_dt_count}）" if lim.prev_dt_count is not None else ""))
    L.append(f"  炸板率：{lim.broken_rate:.1f}%" if lim.broken_rate is not None
             else "  炸板率：UNKNOWN")
    L.append(f"  连板晋级率：{lim.promotion_rate:.1f}%" if lim.promotion_rate is not None
             else "  连板晋级率：UNKNOWN")
    if lim.strong_stock_mean_pct is not None:
        L.append(f"  昨日涨停股次日表现：平均 {lim.strong_stock_mean_pct:+.2f}%，"
                 f"上涨比例 {lim.strong_stock_up_ratio*100:.1f}%（{lim.strong_stock_count} 只）")
    else:
        L.append("  昨日涨停股次日表现：UNKNOWN")

    # 【量价判断】
    L.append("")
    L.append("【量价判断】")
    L.append(f"  成交额：{am.total_amount:.0f} 亿（全市场日线求和口径）"
             if am.total_amount is not None else "  成交额：UNKNOWN")
    L.append(f"    较前一交易日：{am.chg_vs_prev:+.1f}%" if am.chg_vs_prev is not None
             else "    较前一交易日：UNKNOWN")
    L.append(f"    相对5日均额：{am.chg_vs_ma5:+.1f}%" if am.chg_vs_ma5 is not None
             else "    相对5日均额：UNKNOWN")
    L.append(f"    相对20日均额：{am.chg_vs_ma20:+.1f}%" if am.chg_vs_ma20 is not None
             else "    相对20日均额：UNKNOWN")
    L.append(f"    量价形态：{am.pattern}")
    if core and bench and snap.small_vs_large_gap is not None:
        L.append(f"  小盘相对大盘：{snap.small_vs_large_gap:+.2f} 个百分点"
                 f"（{CORE_INDEX} {core.pct_chg:+.2f}% vs {BENCH_INDEX} {bench.pct_chg:+.2f}%）")
    L.append("    注：成交额为全市场口径；指数量比不在此处混用")

    # 【指数与位置】
    L.append("")
    L.append("【指数趋势与位置分位】")
    L.append(f"  {'指数':<10}{'收盘':>11}{'涨跌%':>8}{'MA20':>11}{'MA60':>11}"
             f"{'5日%':>8}{'20日%':>8}{'250日分位':>11}{'回撤分位':>10}")
    for nm, _ in INDEX_LIST:
        ix = snap.indexes.get(nm)
        if not ix or ix.close is None:
            L.append(f"  {nm:<10}{'UNKNOWN':>11}")
            continue
        def f(v, w=11, p=2):
            return f"{v:>{w}.{p}f}" if v is not None else f"{'UNKNOWN':>{w}}"
        L.append(f"  {nm:<10}{ix.close:>11.2f}{ix.pct_chg:>8.2f}"
                 f"{f(ix.ma20)}{f(ix.ma60)}{f(ix.ret5, 8)}{f(ix.ret20, 8)}"
                 f"{f(ix.close_percentile_250d, 11, 1)}{f(ix.drawdown_percentile, 10, 1)}")
    if core:
        L.append(f"  分位口径（各指数独立计算，以 {CORE_INDEX} 为例）：{core.percentile_note}")
    L.append("  注：位置分位低 ≠ 底部，仅为位置描述；底部需卖压衰减 + 修复确认")

    # 【仓位建议】
    L.append("")
    L.append("【仓位建议】")
    if pa.actual_position is not None:
        L.append(f"  当前实际仓位：{pa.actual_position}%")
    else:
        L.append("  当前实际仓位：未提供")
    if pa.target_position is not None:
        L.append(f"  建议目标仓位：{pa.target_position}%")
    else:
        L.append("  建议目标仓位：按现有策略（无统一目标）")
    if pa.add_room is not None:
        L.append(f"  允许加仓空间：{pa.add_room}%")
    else:
        L.append("  允许加仓空间：UNKNOWN（实际仓位未提供）")
    L.append(f"  当前是否允许主动试探：{'是' if pa.allow_probe else '否'}")
    L.append(f"  建议动作：{pa.action}")
    L.append("  加仓必须满足的条件：")
    for c in pa.add_conditions[:4]:
        L.append(f"    · {c}")
    L.append("  需要降低仓位的条件：")
    for c in pa.reduce_conditions[:3]:
        L.append(f"    · {c}")
    if pa.excess_note:
        L.append(f"  ⚠ {pa.excess_note}")

    # 【明日观察重点】
    L.append("")
    L.append("【明日观察重点】")
    obs = cfg["observation"].get(dec.state, cfg["observation"]["S1"])
    for i, o in enumerate(obs[:3], 1):
        L.append(f"  {i}. {o}")

    # 一句话结论
    tgt = f"目标仓位{pa.target_position}%" if pa.target_position is not None else "按现有策略"
    L.append("")
    L.append(f"▎{dec.state} {dec.state_cn}｜{node.node_cn}｜{pa.action}｜"
             f"{tgt}｜切换关键：{_switch_hint(dec, cfg)}")
    L.append("═" * 46)
    return "\n".join(L)


def _switch_hint(dec: RegimeDecision, cfg: dict) -> str:
    if dec.state == STATE_PANIC:
        return "跌停家数回落 + 广度连续回升"
    if dec.state == STATE_EASING:
        return "卖压衰减需再确认1日，且跌停/广度同步改善"
    if dec.state == STATE_REPAIR:
        return "修复连续性验证 + 主线普遍走强"
    if dec.state == STATE_DRAWDOWN:
        return "广度回升 + 指数站上MA20"
    return "广度与跌停结构出现变化"


# ============================================================
# 一句话串联的完整分析
# ============================================================

def analyze_trade_date(trade_date: str, cfg: dict = None,
                       actual_position: Optional[int] = None,
                       persist: bool = True) -> dict:
    """对一个交易日跑完整链路：快照 → 节点 → 状态机 → 仓位建议。"""
    cfg = cfg or load_config()
    prev = load_prev_state(trade_date)
    snap = collect_market_snapshot(trade_date, cfg)
    node = detect_market_nodes(snap, cfg)
    dec = decide_state(snap, node, prev, cfg)
    pa = suggest_position(dec, snap, actual_position, cfg)
    if persist:
        try:
            save_state(trade_date, dec, node.node_type)
        except Exception as e:
            print(f"[V10] 状态保存失败: {e}")
    return {"snapshot": snap, "node": node, "decision": dec, "advice": pa}


# ============================================================
# CLI
# ============================================================

def _last_trade_date() -> str:
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(BASE_DIR), 'config', '.env'))
        import tushare as ts
        ts.set_token(os.getenv('TUSHARE_TOKEN'))
        pro = ts.pro_api()
        cal = pro.trade_cal(exchange='', start_date='20240101',
                            end_date=datetime.now().strftime('%Y%m%d'))
        op = cal[cal['is_open'] == 1]['cal_date'].astype(str).tolist()
        return max(op)
    except Exception:
        return datetime.now().strftime('%Y%m%d')


def main():
    ap = argparse.ArgumentParser(description="A股短线市场环境判断 V10.0")
    ap.add_argument("trade_date", nargs="?", help="交易日 YYYYMMDD，默认最近交易日")
    ap.add_argument("--config", help="配置文件路径")
    ap.add_argument("--actual-position", type=int, help="当前实际仓位（%%），用于计算加仓空间")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    cfg = load_config(args.config)
    td = args.trade_date or _last_trade_date()
    res = analyze_trade_date(td, cfg, args.actual_position)
    if args.json:
        snap, node, dec, pa = res["snapshot"], res["node"], res["decision"], res["advice"]
        print(json.dumps({
            "trade_date": td,
            "node": {"is_node_day": node.is_node_day, "type": node.node_type,
                     "confidence": node.confidence, "evidence": node.evidence,
                     "uncertainties": node.uncertainties},
            "state": {"code": dec.state, "name": dec.state_cn, "days": dec.consecutive_days,
                      "confidence": dec.confidence, "reasons": dec.reasons},
            "breadth": {"up": snap.breadth.up_count, "down": snap.breadth.down_count,
                        "flat": snap.breadth.flat_count,
                        "ratio": snap.breadth.breadth_ratio,
                        "percentile": snap.breadth.breadth_percentile},
            "limit": {"zt": snap.limit.zt_count, "dt": snap.limit.dt_count,
                      "broken_rate": snap.limit.broken_rate,
                      "promotion_rate": snap.limit.promotion_rate,
                      "strong_mean_pct": snap.limit.strong_stock_mean_pct},
            "amount": {"total_yi": snap.amount.total_amount,
                       "vs_prev": snap.amount.chg_vs_prev,
                       "vs_ma5": snap.amount.chg_vs_ma5,
                       "vs_ma20": snap.amount.chg_vs_ma20,
                       "pattern": snap.amount.pattern},
            "position": {"target": pa.target_position, "actual": pa.actual_position,
                         "add_room": pa.add_room, "allow_probe": pa.allow_probe},
            "quality_flags": snap.quality_flags,
        }, ensure_ascii=False, indent=2))
    else:
        print(render_v10_report(res["snapshot"], res["node"], res["decision"],
                                res["advice"], cfg))


if __name__ == "__main__":
    main()
