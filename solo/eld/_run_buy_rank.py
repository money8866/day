"""
ELD V5.0 Trade Alpha 终极买入排序引擎

从 ELD V2 报告(WATCH 候选池)出发，基于日线数据计算：
  FUND(15%) + TREND(20%) + ENTRY(25%) + CAPITAL(10%) + VP(10%) + RR(20%)
加 TRIGGER_SCORE、TRIGGER_CONFIDENCE（触发可信度），
最终合成 TRADE_ALPHA = BUY × TRIGGER_CONFIDENCE × RR_FACTOR。

核心哲学: Trade Alpha = 交易价值，而不是股票质量。
  BUY 高 ≠ 现在可以买；ENTRY 高 ≠ 一定值得买；TRIG 高 ≠ 盈亏比合理。
  RR 基于触发价诚实计算: RR1 < 1.0 直接 WATCH，禁止任何主动买入。
  一只 BUY=84、T1 确认、RR=2.5 的股票，可以超过 BUY=92、T3 未突破、RR=0.8 的股票。

状态机: PRIMARY_BUY / PROBE-A / PROBE-B / PROBE-C / NEXT / WATCH
输出: A榜（当前可买，TRADE_ALPHA DESC）+ B榜（触发即买）+ TOP 交易计划（20 字段）。

用法：
  cd D:\\mystock\\solo
  python eld/_run_buy_rank.py [--date 20260826] [--top 20] [--no-save]
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_env_path = r"D:\mystock\config\.env"
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("TUSHARE_TOKEN="):
                os.environ["TUSHARE_TOKEN"] = line.split("=", 1)[1].strip()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("eld.v3rank")

from eld.utils import get_last_trade_date

# ══════════════════════════════════════════════════════════════
# 常量
# ══════════════════════════════════════════════════════════════

CACHE_DAILY = Path(r"D:\mystock\cache_daily")
REPORT_DIR = Path(r"D:\mystock\report_daily")
STOCK_DB = CACHE_DAILY / "stock_data.db"

WEIGHTS = {
    "FUND": 0.15,
    "TREND": 0.20,
    "ENTRY": 0.25,
    "CAPITAL": 0.10,
    "VP": 0.10,
    "RR": 0.20,
}

ENTRY_BONUS = {
    "T1": 8.0,
    "T2": 10.0,
    "T3": 5.0,
    "T4": 3.0,
    "T5": -10.0,
}

# 机构状态对本层评分的调整
STATE_ADJ = {"吸筹": 8, "洗盘": 5, "启动": 10, "加速": 12, "派发": -12, "未知": 0}

# 市场环境 -> 仓位区间联动（V5 第三十节，含 BEAR 上限）
# 元组: (PRIMARY, PROBE-A, PROBE-B, PROBE-C)
MARKET_POSITION = {
    "BULL": ("20-30%", "15-20%", "10-15%", "5-10%"),
    "NEUTRAL": ("15-25%", "12-16%", "10-13%", "8-12%"),
    "RECOVERY": ("15-25%", "12-16%", "10-13%", "8-12%"),
    "WEAK": ("5-15%", "8-12%", "6-10%", "5-8%"),
    "BEAR": ("<=10%", "5-8%", "4-6%", "3-5%"),
}


def rr_factor(rr1: float) -> float:
    """V5 第二十节: RR -> 乘数因子。RR<1.0 直接归零（禁止任何主动买入）。"""
    if rr1 >= 2.5:
        return 1.10
    if rr1 >= 2.0:
        return 1.05
    if rr1 >= 1.5:
        return 1.00
    if rr1 >= 1.2:
        return 0.90
    if rr1 >= 1.0:
        return 0.70
    return 0.0


def rr_score_map(rr1: float) -> float:
    """V5 第十四节: RR1 -> RR_SCORE 分档。"""
    if rr1 >= 2.5:
        return 95.0
    if rr1 >= 2.0:
        return 90.0
    if rr1 >= 1.5:
        return 80.0
    if rr1 >= 1.2:
        return 70.0
    if rr1 >= 1.0:
        return 62.0
    return 35.0

# 市场环境（由 ELD 报告 JSON 的 market_regime 字段注入，默认 NEUTRAL）
market_regime: str = "NEUTRAL"

# 治理红旗名单（ts_code -> [风险描述]），运行时由 main() 注入
red_flag_map: dict[str, list[str]] = {}


def load_market_regime(trade_date: str) -> str:
    """从 ELD V2 报告 JSON 读取 market_regime（BULL/NEUTRAL/RECOVERY/WEAK/BEAR）。"""
    import json

    json_path = REPORT_DIR / f"eld_report_{trade_date}.json"
    if not json_path.exists():
        logger.warning("未找到市场环境文件 %s，默认 NEUTRAL", json_path)
        return "NEUTRAL"
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        regime = str(data.get("market_regime", "NEUTRAL")).upper()
        if regime not in MARKET_POSITION:
            regime = "NEUTRAL"
        return regime
    except Exception as exc:
        logger.warning("读取市场环境失败(%s)，默认 NEUTRAL", exc)
        return "NEUTRAL"


def load_red_flags(trade_date: str) -> dict[str, list[str]]:
    """读取治理红旗名单：优先 eld_red_flags_<date>.csv，回退静态 eld_red_flags.csv。

    列: ts_code,risk_type,risk_desc。同一 ts_code 多行记录合并为多条描述。
    """
    import csv

    flag_path = REPORT_DIR / f"eld_red_flags_{trade_date}.csv"
    if not flag_path.exists():
        flag_path = REPORT_DIR / "eld_red_flags.csv"
    if not flag_path.exists():
        return {}
    mapping: dict[str, list[str]] = {}
    try:
        with open(flag_path, encoding="utf-8-sig", newline="") as fh:
            for rec in csv.DictReader(fh):
                ts = _s(rec.get("ts_code")).strip()
                rtype = _s(rec.get("risk_type")).strip()
                desc = _s(rec.get("risk_desc")).strip()
                label = f"{rtype}：{desc}" if (rtype and desc) else (rtype or desc or "治理红旗")
                if ts:
                    mapping.setdefault(ts, []).append(label)
    except Exception as exc:
        logger.warning("读取治理红旗失败(%s)，忽略 %s", exc, flag_path.name)
        return {}
    if mapping:
        logger.info("治理红旗已加载: %d 只标的 / %d 条 (%s)",
                    len(mapping), sum(len(v) for v in mapping.values()), flag_path.name)
    return mapping


def compress_position(pos: str) -> str:
    """治理红旗仓位压缩：区间上下限各减半（12-16% -> 6-8%，<=10% -> ≤5%）。"""
    p = (pos or "").strip().rstrip("%")
    if not p or p == "0":
        return "0%"
    try:
        if p.startswith("<="):
            return f"≤{max(1.0, float(p[2:]) / 2):.0f}%"
        parts = [x for x in p.split("-") if x]
        if len(parts) >= 2:
            lo, hi = float(parts[0]), float(parts[1])
            return f"{max(1.0, lo / 2):.0f}-{hi / 2:.0f}%"
        return f"≤{max(1.0, float(parts[0]) / 2):.0f}%"
    except ValueError:
        return "≤5%"


def _f(val, default: float = 0.0) -> float:
    """安全转 float（NaN/None -> default）。"""
    try:
        v = float(val)
        return default if pd.isna(v) else v
    except (TypeError, ValueError):
        return default


def _s(val, default: str = "") -> str:
    """安全转 str（NaN -> default）。"""
    try:
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return default
        return str(val)
    except Exception:
        return default


# ══════════════════════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════════════════════

@dataclass
class V3Result:
    ts_code: str
    name: str
    industry: str
    close: float
    v2_score: float
    # 六层评分
    fund: float = 0.0
    trend: float = 0.0
    entry: float = 0.0
    capital: float = 0.0
    vp: float = 0.0
    rr: float = 0.0
    trigger: float = 0.0
    buy_score: float = 0.0
    # V5 核心新指标
    trade_alpha: float = 0.0      # BUY × TRIG_CONF × RR_FACTOR
    trig_conf: float = 0.0        # TRIGGER_CONFIDENCE（触发可信度）
    rr1: float = 0.0              # (Target1-Trigger)/(Trigger-Stop)
    rr2: float = 0.0              # (Target2-Trigger)/(Trigger-Stop)
    buy_zone: str = ""            # 建议买入区间
    invalidation: float = 0.0     # INVALIDATION_PRICE（结构失效价）
    # 买点与等级
    buy_type: str = "T5"
    confirmed: bool = False        # T1_CONFIRM / T2_CONFIRM
    level: str = "WATCH"
    position: str = "0%"
    # 关键价位
    ma20: float = 0.0
    ma60: float = 0.0
    breakout_price: float = 0.0
    stop_loss: float = 0.0
    target1: float = 0.0
    target2: float = 0.0
    # 诊断
    notes: list[str] = field(default_factory=list)
    veto: list[str] = field(default_factory=list)
    redflags: list[str] = field(default_factory=list)
    # B榜触发模拟（NEXT/PROBE 升级路径）
    trigger_condition: str = ""
    trigger_price: float = 0.0
    proj_level: str = ""
    proj_position: str = "0%"
    proj_buy: float = 0.0
    proj_alpha: float = 0.0
    proj_alpha_rr1: float = 0.0


# ══════════════════════════════════════════════════════════════
# 数据加载
# ══════════════════════════════════════════════════════════════

def load_candidates(trade_date: str) -> pd.DataFrame:
    """从 ELD V2 报告 CSV 读取候选池。"""
    path = REPORT_DIR / f"eld_report_{trade_date}.csv"
    if not path.exists():
        raise FileNotFoundError(f"未找到 ELD V2 报告: {path}")
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={"ts_code": str})
    need = ["ts_code", "name", "industry", "forecast_pct",
            "event_quality", "expectation_gap_v2", "institution_accumulation",
            "final_score_v2", "trend", "etf_score", "institution_state"]
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise ValueError(f"报告缺少字段: {missing}")
    # 过滤 ST/*ST（不交易风险警示股）
    before = len(df)
    df = df[~df["name"].astype(str).str.upper().str.contains("ST", na=False)]
    # 过滤北交所（920/8/4 开头，用户不做北交所）
    n_bse = len(df)
    df = df[~df["ts_code"].astype(str).str.match(r"^(92[0-9]{4}|[84][0-9]{5})\.BJ$", na=False)]
    logger.info("候选池: %d 只 (来源 %s，剔除 ST %d 只 + 北交所 %d 只)",
                len(df), path.name, before - len(df), n_bse - len(df))
    return df


def load_daily_cached(trade_date: str, codes: set[str],
                      lookback_days: int = 320) -> dict[str, pd.DataFrame]:
    """从共享 SQLite (stock_data.db / daily_cache 表) 加载候选股日线，返回 {ts_code: df}。

    与 ELD V2 的 get_daily_data 优先级一致（第 0 层即通用 daily_cache 表）。
    """
    import sqlite3

    if not STOCK_DB.exists():
        raise FileNotFoundError(f"未找到共享日线数据库: {STOCK_DB}")
    start = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=lookback_days)).strftime("%Y%m%d")
    codes_list = sorted(codes)
    result: dict[str, pd.DataFrame] = {}
    # 分批 IN 查询，避免单条 SQL 变量数超限
    batch = 400
    with sqlite3.connect(STOCK_DB) as conn:
        for i in range(0, len(codes_list), batch):
            chunk = codes_list[i:i + batch]
            ph = ",".join("?" * len(chunk))
            sql = (
                "SELECT ts_code, trade_date, open, high, low, close, vol "
                f"FROM daily_cache WHERE trade_date >= ? AND trade_date <= ? "
                f"AND ts_code IN ({ph})"
            )
            df = pd.read_sql_query(sql, conn, params=[start, trade_date] + chunk)
            for code, g in df.groupby("ts_code"):
                g = g.drop_duplicates(subset=["trade_date"], keep="last")
                g = g.sort_values("trade_date").reset_index(drop=True)
                result[code] = g
    logger.info("SQLite 日线加载: %d/%d 只候选股 [%s ~ %s]",
                len(result), len(codes), start, trade_date)
    return result


# ══════════════════════════════════════════════════════════════
# 技术指标
# ══════════════════════════════════════════════════════════════

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """计算 MA5/10/20/60、成交量均线、ATR14、涨跌幅。"""
    d = df.copy()
    d["vol_ma5"] = d["vol"].rolling(5).mean()
    d["vol_ma20"] = d["vol"].rolling(20).mean()
    d["ma5"] = d["close"].rolling(5).mean()
    d["ma10"] = d["close"].rolling(10).mean()
    d["ma20"] = d["close"].rolling(20).mean()
    d["ma60"] = d["close"].rolling(60).mean()
    prev_close = d["close"].shift(1)
    tr = pd.concat([
        d["high"] - d["low"],
        (d["high"] - prev_close).abs(),
        (d["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    d["atr"] = tr.rolling(14).mean()
    d["pct_chg"] = d["close"].pct_change() * 100
    return d


def ma_slope(ma: pd.Series, days: int = 5) -> float:
    """均线斜率（最近 N 天变化率%）。"""
    if len(ma) < days + 1:
        return 0.0
    a, b = ma.iloc[-1], ma.iloc[-days - 1]
    if pd.isna(a) or pd.isna(b) or b == 0:
        return 0.0
    return (a / b - 1) * 100


def detect_platform(d: pd.DataFrame, lookback: int = 30) -> tuple[bool, float, float]:
    """检测近 N 日是否形成平台，返回 (是否平台, 平台高, 平台低)。

    平台定义：区间振幅 <= 18%，且收盘价变异系数 <= 10%。
    注意：调用方应排除突破日后再传入（否则平台高会被突破日污染）。
    """
    dd = d.tail(lookback)
    if len(dd) < 15:
        return False, 0.0, 0.0
    hi, lo = dd["high"].max(), dd["low"].min()
    if lo <= 0 or hi <= 0:
        return False, hi, lo
    amp = (hi - lo) / lo * 100
    if amp > 18:
        return False, hi, lo
    mean_close = dd["close"].mean()
    if mean_close <= 0:
        return False, hi, lo
    cv = dd["close"].std() / mean_close
    if cv > 0.10:
        return False, hi, lo
    return True, hi, lo


# ══════════════════════════════════════════════════════════════
# 买点类型判定
# ══════════════════════════════════════════════════════════════

def classify_buy_type(d: pd.DataFrame) -> tuple[str, list[str], dict]:
    """判定买点类型 T1-T5。

    返回 (类型, 理由列表, 关键价位信息)。info 额外含:
      confirmed: T1_CONFIRM/T2_CONFIRM（当日已满足触发）
      trigger_price: T3/T4 的精确触发价
    """
    reasons: list[str] = []
    info: dict = {"resistance": 0.0, "plat_hi": 0.0, "plat_lo": 0.0,
                  "confirmed": False, "trigger_price": 0.0}
    if len(d) < 60:
        return "T5", ["数据不足60日"], info

    close = d["close"].iloc[-1]
    vol = d["vol"].iloc[-1]
    high_last = d["high"].iloc[-1]
    ma20 = d["ma20"].iloc[-1]
    ma60 = d["ma60"].iloc[-1]
    ma20_s = ma_slope(d["ma20"], 5)
    ma60_s = ma_slope(d["ma60"], 10)
    vol_ma20 = d["vol_ma20"].iloc[-1]

    # 前期压力位：最近10日之前的最高价（排除潜在突破窗口）
    d_pre = d.iloc[:-10]
    resistance = d_pre["high"].max() if len(d_pre) >= 30 else 0.0
    info["resistance"] = resistance

    # 平台（排除最近1日，避免突破日污染）
    is_platform, plat_hi, plat_lo = detect_platform(d.iloc[:-1], 30)
    info["plat_hi"], info["plat_lo"] = plat_hi, plat_lo

    # 收盘位置（当日振幅内的位置，0~1）
    day_range = high_last - d["low"].iloc[-1]
    close_pos = (close - d["low"].iloc[-1]) / day_range if day_range > 0 else 1.0

    # ── T1 突破确认：今日放量突破前期压力位 ──
    # V4 要求: 收盘位置 >= 当日振幅80%
    if (resistance > 0 and close > resistance * 1.003
            and vol_ma20 and vol >= 1.3 * vol_ma20
            and close_pos >= 0.80 and close > ma20):
        info["confirmed"] = True
        reasons.append(f"T1_CONFIRM: 放量突破压力{resistance:.2f}(量比{vol/vol_ma20:.2f})，"
                       f"收盘位置{close_pos*100:.0f}%")
        return "T1", reasons, info

    # T1 变体：最近5日内放量突破，且此后未跌回（突破确认延续）
    d5 = d.tail(5)
    for i in range(len(d5) - 1):
        row = d5.iloc[i]
        vr = d.loc[row.name, "vol_ma20"]
        if (resistance > 0 and pd.notna(vr) and vr > 0
                and row["vol"] >= 1.3 * vr and row["close"] > resistance * 1.003):
            after = d5.iloc[i + 1:]
            if len(after) > 0 and after["close"].min() > resistance * 0.99 and close > ma20:
                info["confirmed"] = True
                reasons.append(f"T1_CONFIRM: {row['trade_date']}放量突破压力{resistance:.2f}后站稳")
                return "T1", reasons, info

    # ── T2 突破后缩量回踩 ──
    d10 = d.tail(10)
    breakout_pos = None  # 突破日在 d10 中的位置
    for i in range(len(d10) - 2):  # 排除最近2日（回踩期）
        row = d10.iloc[i]
        vr = row["vol_ma20"]
        if (resistance > 0 and pd.notna(vr) and vr > 0
                and row["vol"] >= 1.3 * vr and row["close"] > resistance * 1.003):
            breakout_pos = i
            break
    if breakout_pos is not None:
        after = d10.iloc[breakout_pos + 1:]
        breakout_vol = d10.iloc[breakout_pos]["vol"]
        recent_vol_avg = d["vol"].iloc[-3:].mean()
        recent_low = after["low"].min() if len(after) > 0 else close
        cond_shrink = recent_vol_avg < breakout_vol * 0.75
        cond_hold = len(after) >= 1 and recent_low > resistance * 0.99 and close > ma20 * 0.985
        if cond_shrink and cond_hold:
            pct_last = d["pct_chg"].iloc[-1]
            vol_last = d["vol"].iloc[-1]
            vol_prev = d["vol"].iloc[-2] if len(d) >= 2 else 0
            # T2_CONFIRM: 重新放量阳线 + 收盘重新站回关键位
            re_confirm = (pct_last > 0 and vol_prev > 0 and vol_last > vol_prev * 1.1
                          and (vol_ma20 and vol_ma20 > 0 and vol_last > vol_ma20)
                          and close > ma20)
            if re_confirm:
                info["confirmed"] = True
                reasons.append(f"T2_CONFIRM: 突破{resistance:.2f}后缩量回踩，今日放量阳线站回MA20")
            else:
                reasons.append(
                    f"T2回踩中: 突破压力{resistance:.2f}后缩量回踩"
                    f"(突破量{breakout_vol/10000:.1f}万手->近3日均量{recent_vol_avg/10000:.1f}万手)")
            reasons.append(f"回踩低点{recent_low:.2f}未破压力位/MA20({ma20:.2f})")
            return "T2", reasons, info

    # ── T3 平台突破临界 ──
    if (is_platform and plat_hi > 0
            and plat_hi * 0.97 <= close <= plat_hi * 1.03
            and ma20_s > 0.2):
        vr_5_20 = d["vol"].iloc[-5:].mean() / vol_ma20 if vol_ma20 > 0 else 0
        if vr_5_20 >= 1.05:
            # T3 精确触发价 = 平台压力 x 1.003
            info["trigger_price"] = plat_hi * 1.003
            reasons.append(f"T3临界: 距平台高{plat_hi:.2f}仅{(close/plat_hi-1)*100:+.1f}%，"
                           f"触发价{info['trigger_price']:.2f}")
            reasons.append(f"MA20向上(斜率{ma20_s:.1f}%)，近5日量能为均量{vr_5_20:.2f}倍")
            return "T3", reasons, info

    # ── T4 底部确认 ──
    if ma60_s <= 1.0 and ma20_s > 0.2 and close > ma20:
        d20 = d.tail(20)
        first_wave = ((d20["vol"] >= 2.0 * d20["vol_ma20"]) & (d20["pct_chg"] > 3)).any()
        if first_wave:
            low10 = d["low"].iloc[-10:].min()
            low20 = d["low"].iloc[-20:].min()
            if low10 > low20 * 0.97:
                # T4 触发价 = 近10日高 x 1.003（重新启动位）
                info["trigger_price"] = d["high"].iloc[-10:].max() * 1.003
                reasons.append(f"T4底部: MA60斜率{ma60_s:.1f}%走平，MA20拐头向上(斜率{ma20_s:.1f}%)")
                reasons.append("第一波放量上涨后回调不破核心支撑")
                return "T4", reasons, info

    # ── T5 ──
    reasons.append(f"未识别高级买点(平台:{is_platform}，MA20斜率:{ma20_s:.1f}%)")
    return "T5", reasons, info


# ══════════════════════════════════════════════════════════════
# 六层评分
# ══════════════════════════════════════════════════════════════

def score_fundamental(row: pd.Series) -> tuple[float, list[str]]:
    """FUND(V4): 预期差20% + 业绩增长20% + 扣非质量15% + 行业景气15% + 持续性15% + 事件催化10% + 估值位置5%"""
    gap = _f(row.get("expectation_gap_v2"), 50)
    event = _f(row.get("event_quality"), 50)
    forecast = _f(row.get("forecast_pct"), 30)
    ind_score = _f(row.get("industry_score"), 60)
    etf = _f(row.get("etf_score"), 50)

    s_gap = min(100.0, gap)
    if forecast >= 100:
        s_growth = 100.0
    elif forecast >= 60:
        s_growth = 90.0
    elif forecast >= 30:
        s_growth = 75.0
    else:
        s_growth = 50.0
    s_quality = event            # 事件质量含扣非占比过滤，代理扣非质量
    s_industry = min(100.0, ind_score)
    s_sustain = event * 0.6 + etf * 0.4
    s_catalyst = event
    # 估值/预期位置: etf_score 代理（未大涨的补涨弹性高）
    s_valuation = etf

    score = (s_gap * 0.20 + s_growth * 0.20 + s_quality * 0.15 +
             s_industry * 0.15 + s_sustain * 0.15 + s_catalyst * 0.10 + s_valuation * 0.05)
    notes = [f"预期差{gap:.0f}/增速{forecast:.0f}%/事件{event:.0f}/行业{ind_score:.0f}"]
    return min(100.0, score), notes


def score_trend(d: pd.DataFrame) -> tuple[float, list[str]]:
    """TREND: 右侧趋势结构 A/B/C 级。"""
    notes: list[str] = []
    if len(d) < 60:
        return 30.0, ["数据不足60日"]

    close = d["close"].iloc[-1]
    ma20 = d["ma20"].iloc[-1]
    ma60 = d["ma60"].iloc[-1]
    ma20_s = ma_slope(d["ma20"], 5)
    ma60_s = ma_slope(d["ma60"], 10)

    score = 50.0
    grade = "C"
    if ma20_s > 0.5 and ma60_s > 0.5 and close > ma20 and ma20 > ma60:
        score, grade = 90.0, "A"
        notes.append(f"A级: MA20/60多头排列，股价站上MA20，MA20斜率{ma20_s:.1f}%")
    elif ma20_s > 0.5 and close > ma20:
        score, grade = 78.0, "B"
        notes.append(f"B级: 站上MA20且MA20向上(斜率{ma20_s:.1f}%)，MA60斜率{ma60_s:.1f}%")
    elif ma20_s > 0.5 and close <= ma20:
        score, grade = 62.0, "B-"
        notes.append(f"B-: MA20向上但股价在MA20下方(乖离{(close/ma20-1)*100:.1f}%)")
    elif close > ma20 and ma60_s > 0:
        score, grade = 58.0, "C+"
        notes.append(f"C+: 股价站上MA20但MA20未拐头(斜率{ma20_s:.1f}%)")
    else:
        score, grade = 30.0, "C"
        notes.append("C级: MA20向下/股价位于MA20下方")

    is_platform, _, _ = detect_platform(d.iloc[:-1], 30)
    if is_platform and grade in ("A", "B"):
        score = min(100.0, score + 5)
        notes.append("平台结构良好")
    if ma60_s < 0 and ma20_s > 0.5 and close > ma20:
        score = min(100.0, score + 5)
        notes.append("长期下降趋势刚突破/底部完成")

    return min(100.0, score), notes


def score_entry(d: pd.DataFrame, buy_type: str, reasons: list[str]) -> tuple[float, list[str]]:
    """ENTRY: 买点状态评分（权重最高的层）。"""
    base = {"T1": 92.0, "T2": 90.0, "T3": 82.0, "T4": 75.0, "T5": 50.0}
    score = base[buy_type]
    notes = list(reasons)
    if buy_type in ("T1", "T2") and len(d) >= 20:
        close = d["close"].iloc[-1]
        ma20 = d["ma20"].iloc[-1]
        if ma20 > 0:
            bias = (close / ma20 - 1) * 100
            if 0 <= bias <= 5:
                score = min(100.0, score + 3)
                notes.append(f"距MA20 {bias:.1f}%（最佳区间）")
            elif bias > 10:
                score = max(base[buy_type] - 15, score - 15)
                notes.append(f"距MA20 {bias:.1f}%（过远，扣分）")
    return min(100.0, score), notes


def score_capital(d: pd.DataFrame, inst_score: float, inst_state: str) -> tuple[float, list[str]]:
    """CAPITAL: 机构/资金确认（量价关系重估，非直接用原始吸筹分）。"""
    notes: list[str] = []
    if len(d) < 25:
        return 40.0, ["数据不足25日"]

    vol_ma = d["vol_ma20"].iloc[-1]
    if not vol_ma or vol_ma <= 0 or pd.isna(vol_ma):
        return 40.0, ["成交量数据异常"]

    d10 = d.tail(10)
    up_days = d10[d10["pct_chg"] > 0]
    down_days = d10[d10["pct_chg"] < 0]
    up_vol_ok = ((up_days["vol"] > vol_ma * 1.1).sum() / len(up_days)) if len(up_days) > 0 else 0.0
    down_vol_ok = ((down_days["vol"] < vol_ma * 0.9).sum() / len(down_days)) if len(down_days) > 0 else 0.0

    score = 50.0
    if up_vol_ok >= 0.7 and down_vol_ok >= 0.7:
        score = 85.0
        notes.append("近10日上涨放量+下跌缩量（量价配合优秀）")
    elif up_vol_ok >= 0.5 and down_vol_ok >= 0.5:
        score = 70.0
        notes.append("近10日量价配合良好")
    elif up_vol_ok >= 0.5 or down_vol_ok >= 0.5:
        score = 60.0
        notes.append("近10日量价配合一般")
    else:
        notes.append("近10日量价配合差")

    # 近5日资金方向近似（量幅加权）
    d5 = d.tail(5)
    w = (d5["vol"] * d5["pct_chg"].abs()).sum()
    if w > 0:
        flow = (d5["vol"] * d5["pct_chg"]).sum() / w
        if flow > 2:
            score = min(100.0, score + 10)
            notes.append(f"近5日资金方向: 净流入(+{flow:.1f})")
        elif flow < -2:
            score = max(10.0, score - 10)
            notes.append(f"近5日资金方向: 净流出({flow:.1f})")

    score += STATE_ADJ.get(inst_state, 0)
    score = max(5.0, min(100.0, score))
    notes.append(f"机构状态[{inst_state}] 原始吸筹分{inst_score:.0f}（本层重估）")
    return score, notes


def score_volume_price(d: pd.DataFrame, buy_type: str) -> tuple[float, list[str]]:
    """VP: 量价结构评分。"""
    notes: list[str] = []
    if len(d) < 30:
        return 40.0, ["数据不足30日"]

    vol_ma = d["vol_ma20"].iloc[-1]
    close = d["close"].iloc[-1]
    ma20 = d["ma20"].iloc[-1]
    ma20_s = ma_slope(d["ma20"], 5)
    if not vol_ma or vol_ma <= 0 or pd.isna(vol_ma):
        return 40.0, ["成交量数据异常"]

    d20 = d.tail(20)
    vol_ratio = d20["vol"] / d20["vol_ma20"]
    has_surge = ((vol_ratio >= 1.3) & (d20["pct_chg"] > 0)).any()
    vol_recent = d["vol"].iloc[-5:].mean()
    shrink = vol_recent / vol_ma
    d10 = d.tail(10)
    up_days = d10[d10["pct_chg"] > 0]
    down_days = d10[d10["pct_chg"] < 0]
    up_vol = up_days["vol"].mean() if len(up_days) else 0.0
    down_vol = down_days["vol"].mean() if len(down_days) else 0.0

    score = 50.0
    if buy_type in ("T1", "T2") and has_surge:
        if shrink <= 0.85:
            score = 90.0
            notes.append("放量突破后缩量回踩（最佳量价结构）")
        elif shrink <= 1.0:
            score = 80.0
            notes.append("放量突破后量能温和收缩")
        else:
            score = 65.0
            notes.append("放量突破后量能未有效收缩")
    elif buy_type == "T3" and has_surge:
        score = 78.0
        notes.append("平台末端量能开始放大")
    elif buy_type == "T4":
        if shrink <= 0.9:
            score = 80.0
            notes.append("底部放量后缩量整理（次优结构）")
        else:
            score = 68.0
            notes.append("底部放量整理中")
    else:
        if close > ma20 and ma20_s > 0:
            score = 60.0
            notes.append("站上MA20且MA20向上")
        else:
            score = 40.0
            notes.append("量价结构未确认")

    # 危险结构惩罚
    last_vr = d["vol"].iloc[-1] / vol_ma
    if last_vr > 2.5 and close < ma20:
        score = min(score, 40)
        notes.append("⚠️ 高位放巨量且跌破MA20")
    if up_vol > 0 and down_vol > up_vol * 1.2 and len(down_days) >= 3:
        score = min(score, 45)
        notes.append("⚠️ 下跌放量>上涨放量（负量价关系）")
    d4 = d.tail(4).iloc[:-1]
    if ((d4["vol"] > d4["vol_ma20"] * 1.3) & (d4["pct_chg"] > 0)).sum() >= 3 and ma20 > 0 and close / ma20 > 1.15:
        score = min(score, 50)
        notes.append("⚠️ 连续放量上涨后乖离过大")
    if shrink < 0.8 and close < ma20:
        score = min(score, 35)
        notes.append("⚠️ 缩量跌破MA20")

    return max(5.0, min(100.0, score)), notes


def score_trigger(d: pd.DataFrame, buy_type: str, info: dict) -> tuple[float, list[str]]:
    """TRIGGER_SCORE(V4 第十五节): 距离真正买点还有多远。

    组成: 距突破位25% + 量能准备20% + 收盘位置15% + MA20位置10%
          + 平台完整度15% + 压力位情况10% + 资金预热5%
    距离越近分越高。已确认(T1/T2_CONFIRM)直接高分。
    """
    notes: list[str] = []
    if len(d) < 60:
        return 30.0, ["数据不足60日"]

    close = d["close"].iloc[-1]
    high_last = d["high"].iloc[-1]
    low_last = d["low"].iloc[-1]
    ma20 = d["ma20"].iloc[-1]
    vol_ma20 = d["vol_ma20"].iloc[-1]
    is_platform, plat_hi, plat_lo = info["plat_hi"] > 0, info["plat_hi"], info["plat_lo"]
    resistance = info["resistance"]

    # ── 已确认: T1/T2_CONFIRM 直接 95+ ──
    if info.get("confirmed"):
        score = 96.0 if buy_type == "T1" else 93.0
        notes.append(f"{buy_type}_CONFIRM 已触发")
        return score, notes

    # ① 距突破位 (25%)：以触发价为基准
    trigger_price = info.get("trigger_price", 0.0)
    ref = trigger_price if trigger_price > 0 else max(resistance, plat_hi)
    if ref <= 0 or close <= 0:
        s_dist = 30.0
    else:
        dist_pct = (ref / close - 1) * 100
        if dist_pct <= 0:
            s_dist = 95.0 if buy_type in ("T3", "T4") else 85.0
            notes.append(f"已达突破位(距{ref:.2f} {dist_pct:+.1f}%)")
        elif dist_pct <= 1:
            s_dist = 97.0
            notes.append(f"距突破位{dist_pct:.1f}%")
        elif dist_pct <= 2:
            s_dist = 92.0
            notes.append(f"距突破位{dist_pct:.1f}%")
        elif dist_pct <= 3:
            s_dist = 85.0
            notes.append(f"距突破位{dist_pct:.1f}%")
        else:
            s_dist = max(30.0, 80.0 - (dist_pct - 3) * 8)
            notes.append(f"距突破位{dist_pct:.1f}%（偏远）")

    # ② 量能准备 (20%)：近5日均量 vs 20日均量
    if vol_ma20 and vol_ma20 > 0:
        vr = d["vol"].iloc[-5:].mean() / vol_ma20
        if vr >= 1.15:
            s_vol = 92.0
        elif vr >= 1.05:
            s_vol = 85.0
        elif vr >= 0.9:
            s_vol = 72.0
        else:
            s_vol = 55.0
        notes.append(f"量能准备{vr:.2f}x")
    else:
        s_vol = 50.0

    # ③ 收盘位置 (15%)：当日振幅内位置
    day_range = high_last - low_last
    cp = (close - low_last) / day_range if day_range > 0 else 1.0
    s_close = 60.0 + cp * 40.0
    notes.append(f"收盘位置{cp*100:.0f}%")

    # ④ MA20位置 (10%)
    if ma20 > 0:
        bias = (close / ma20 - 1) * 100
        if 0 <= bias <= 5:
            s_ma = 90.0
        elif -3 <= bias < 0:
            s_ma = 80.0
        elif bias <= 10:
            s_ma = 68.0
        else:
            s_ma = 45.0
        notes.append(f"距MA20 {bias:+.1f}%")
    else:
        s_ma = 50.0

    # ⑤ 平台完整度 (15%)
    if is_platform:
        plat_days = 30
        s_plat = 88.0
        notes.append("平台结构完整")
    else:
        s_plat = 50.0
        notes.append("无明显平台")

    # ⑥ 压力位情况 (10%)：突破后上方是否还有近压力
    high60 = d["high"].iloc[-60:-5].max()
    if high60 > close:
        gap = (high60 / close - 1) * 100
        if gap >= 6:
            s_res = 85.0
        elif gap >= 3:
            s_res = 70.0
        else:
            s_res = 50.0
            notes.append(f"上方压力距{gap:.1f}%（偏近）")
    else:
        s_res = 90.0
        notes.append("已越60日压力")

    # ⑦ 资金预热 (5%)：近5日资金方向
    d5 = d.tail(5)
    w = (d5["vol"] * d5["pct_chg"].abs()).sum()
    if w > 0:
        flow = (d5["vol"] * d5["pct_chg"]).sum() / w
        s_flow = 70.0 + flow * 10.0
    else:
        s_flow = 60.0

    score = (s_dist * 0.25 + s_vol * 0.20 + s_close * 0.15 + s_ma * 0.10 +
             s_plat * 0.15 + s_res * 0.10 + s_flow * 0.05)
    return max(5.0, min(100.0, score)), notes


def score_trig_conf(d: pd.DataFrame, buy_type: str, info: dict,
                    trig_score: float, capital_score: float) -> float:
    """V5 TRIGGER_CONFIDENCE（第十九节）: 触发以后突破成功的可信度。

    考虑: 平台完整度 + 突破距离 + 量能准备 + MA20方向 + 资金准备 + 压力位 + 收盘位置。
    已确认(T1/T2_CONFIRM)高可信；单日放巨量+长上影显著降级（第三十三节追高保护）。
    """
    if len(d) < 60:
        return 30.0
    close = d["close"].iloc[-1]
    high_last = d["high"].iloc[-1]
    low_last = d["low"].iloc[-1]
    ma20 = d["ma20"].iloc[-1]
    ma20_s = ma_slope(d["ma20"], 5)
    vol_ma20 = d["vol_ma20"].iloc[-1]
    vol_last = d["vol"].iloc[-1]
    is_platform = info["plat_hi"] > 0

    conf = 50.0
    # 平台完整度: 结构越完整，假突破概率越低
    conf += 15.0 if is_platform else 0.0
    # 突破距离: 距离越近，动能损耗越小
    ref = info.get("trigger_price", 0.0) or max(info.get("resistance", 0.0), info["plat_hi"])
    if ref > 0 and close > 0:
        dist = (ref / close - 1) * 100
        if dist <= 1:
            conf += 15.0
        elif dist <= 2:
            conf += 12.0
        elif dist <= 3:
            conf += 8.0
        elif dist <= 5:
            conf += 4.0
    # 量能准备
    if vol_ma20 and vol_ma20 > 0:
        vr5 = d["vol"].iloc[-5:].mean() / vol_ma20
        if vr5 >= 1.1:
            conf += 12.0
        elif vr5 >= 0.95:
            conf += 8.0
        elif vr5 >= 0.85:
            conf += 4.0
    # MA20 方向
    conf += 8.0 if ma20_s > 0.3 else (4.0 if ma20_s > 0 else 0.0)
    # 资金准备
    if capital_score >= 70:
        conf += 8.0
    elif capital_score >= 55:
        conf += 5.0
    elif capital_score < 40:
        conf -= 5.0
    # 收盘位置（当日 K 线强度）
    day_range = high_last - low_last
    cp = (close - low_last) / day_range if day_range > 0 else 1.0
    conf += (cp - 0.5) * 10.0

    # 已确认: T1/T2_CONFIRM 高可信
    if info.get("confirmed"):
        conf = max(conf, 88.0)

    # 追高保护（第三十三节）: 单日放巨量 + 长上影 -> 降级
    if vol_ma20 and vol_ma20 > 0 and vol_last > 2.0 * vol_ma20:
        upper_shadow = high_last - max(close, d["open"].iloc[-1])
        if day_range > 0 and upper_shadow / day_range > 0.4:
            conf -= 20.0

    return max(10.0, min(100.0, conf))


def score_rr(d: pd.DataFrame, trigger_price: float = 0.0, confirmed: bool = False) -> tuple[float, list[str], dict]:
    """V5 RR_SCORE（第十三~十七节）: 基于触发价诚实计算 RR1/RR2。

    RISK    = (Trigger - Stop) / Trigger
    RR1     = (Target1 - Trigger) / (Trigger - Stop)
    RR2     = (Target2 - Trigger) / (Trigger - Stop)

    止损优先结构失效点（核心支撑/平台下沿/MA20/前突破位），非固定 -5%。
    返回 (RR_SCORE, 说明, 价位信息)。info 额外含 rr1/rr2/trigger/invalidation。
    """
    notes: list[str] = []
    if len(d) < 60:
        return 30.0, ["数据不足60日"], {}

    close = d["close"].iloc[-1]
    ma20 = d["ma20"].iloc[-1]
    atr = d["atr"].iloc[-1] if pd.notna(d["atr"].iloc[-1]) else close * 0.03
    high60 = d["high"].iloc[-60:-5].max()
    high20 = d["high"].iloc[-20:].max()
    is_platform, plat_hi, plat_lo = detect_platform(d.iloc[:-1], 30)

    # ── 触发价基准（V5 核心：RR 以触发价为锚，而非现价）──
    # 已确认(T1/T2_CONFIRM): 以现价为触发价（买点已在当前价位成立）
    trigger = trigger_price if trigger_price > 0 else (close if confirmed else close * 1.02)

    # ── 结构止损（第十六节：优先技术结构失效点，禁止简单固定-5%）──
    # 候选支撑: 核心低点支撑 / 平台下沿 / MA20，取触发价下方最近的有效结构位
    low10 = d["low"].iloc[-10:].min()
    low20 = d["low"].iloc[-20:].min()
    core_support = max(low10, low20 * 1.005) if low20 > 0 else low10
    supports = [x for x in (core_support, plat_lo if is_platform else 0.0,
                            ma20 if pd.notna(ma20) and ma20 > 0 else 0.0)
                if 0 < x < trigger * 0.99]
    struct_stop = max(supports) if supports else trigger * 0.95
    # 结构位下方再留 0.5*ATR 缓冲，但总止损空间不超过 8%
    stop = max(struct_stop - 0.5 * atr, trigger * 0.92)
    risk = (trigger - stop) / trigger if trigger > 0 else 1.0
    risk_pct = risk * 100

    # ── 目标价（第十七节：前高/平台高度/ATR扩展/近端压力，禁止拍脑袋）──
    # Target1: 高于触发价 1.5% 以上的近端阻力最近者，下限 1.5*ATR 扩展，封顶 3*ATR
    resists = [x for x in (high60, high20, plat_hi if is_platform else 0.0)
               if x > trigger * 1.015]
    near_resist = min(resists) if resists else trigger + 2.5 * atr
    target1 = max(min(near_resist, trigger + 3.0 * atr), trigger + 1.5 * atr)
    # Target2: 突破 T1 后下一阻力（封顶 4.5*ATR），下限 T1 + 1R
    far_resists = [x for x in (high60, high20) if x > target1 * 1.01]
    t2_resist = min(far_resists) if far_resists else trigger + 4.0 * atr
    target2 = max(min(t2_resist, trigger + 4.5 * atr), target1 + (target1 - stop))

    rr1 = (target1 - trigger) / (trigger - stop) if trigger > stop else 0.0
    rr2 = (target2 - trigger) / (trigger - stop) if trigger > stop else 0.0

    # ── RR_SCORE 映射（第十四节分档）+ 止损空间/目标可靠性微调 ──
    score = rr_score_map(rr1)
    notes.append(f"RR1={rr1:.2f}（T1 {target1:.2f}-触发{trigger:.2f} vs 止损{stop:.2f}）")
    if rr1 >= 2.0:
        notes.append("盈亏比优秀")
    elif rr1 >= 1.5:
        notes.append("盈亏比合格")
    elif rr1 < 1.0:
        notes.append("⚠️ 第一目标收益空间小于止损风险，禁止买入")
    if risk_pct <= 5:
        score = min(100.0, score + 5)
        notes.append(f"止损空间{risk_pct:.1f}%（优秀）")
    elif risk_pct <= 7:
        notes.append(f"止损空间{risk_pct:.1f}%（可接受）")
    else:
        score = max(30.0, score - 12)
        notes.append(f"止损空间{risk_pct:.1f}%（过大，降级）")
    if rr2 >= rr1 * 1.8:
        score = min(100.0, score + 3)
        notes.append(f"RR2={rr2:.2f}（第二目标弹性充足）")
    atr_pct = atr / close * 100
    if atr_pct > 6:
        score = max(30.0, score - 5)
        notes.append(f"ATR波幅{atr_pct:.1f}%（波动大）")

    # ── 结构失效价（第三十二节）──
    invalidation = max(struct_stop * 0.995, stop)

    info = {"stop": stop, "target1": target1, "target2": target2,
            "rr_ratio": rr1, "rr1": rr1, "rr2": rr2,
            "stop_dist": risk_pct, "trigger": trigger,
            "struct_stop": struct_stop, "invalidation": invalidation}
    return max(5.0, min(100.0, score)), notes, info


# ══════════════════════════════════════════════════════════════
# 硬性淘汰条件
# ══════════════════════════════════════════════════════════════

def check_veto(d: pd.DataFrame, buy_type: str, rr_info: dict, vp_score: float = 100.0,
               capital_score: float = 100.0, t1_confirm: bool = False) -> tuple[bool, list[str]]:
    """硬性淘汰条件检查（V4），任一成立禁止 PRIMARY_BUY。"""
    vetoes: list[str] = []
    if len(d) < 60:
        return True, ["数据不足60日"]

    close = d["close"].iloc[-1]
    ma20 = d["ma20"].iloc[-1]
    ma20_s = ma_slope(d["ma20"], 5)
    ma60_s = ma_slope(d["ma60"], 10)
    vol_ma = d["vol_ma20"].iloc[-1]

    if close < ma20 * 0.98 and ma20_s < -0.3:
        vetoes.append(f"股价低于MA20 {(close/ma20-1)*100:.1f}%且MA20向下")
    if ma20_s < -0.3 and ma60_s < -0.3:
        vetoes.append(f"MA20({ma20_s:.1f}%)与MA60({ma60_s:.1f}%)同时向下")
    is_platform, plat_hi, plat_lo = detect_platform(d.iloc[:-1], 30)
    if is_platform and buy_type in ("T1", "T2"):
        d10 = d.tail(10)
        if (d10["close"] > plat_hi * 1.01).any() and close < plat_lo * 1.02:
            vetoes.append("突破后放量跌回平台")
    low10 = d["low"].iloc[-10:].min()
    low40 = d["low"].iloc[-40:].min()
    if low10 < low40 * 0.97:
        vetoes.append(f"近10日低点{low10:.2f}跌破核心支撑{low40:.2f}")
    last_pct = d["pct_chg"].iloc[-1]
    last_vr = d["vol"].iloc[-1] / vol_ma if vol_ma and vol_ma > 0 else 1.0
    if last_vr > 1.5 and last_pct < -4:
        vetoes.append(f"放量长阴(量比{last_vr:.2f}，跌{last_pct:.1f}%)")
    d5 = d.tail(5)
    down_vol_days = int(((d5["pct_chg"] < -1) & (d5["vol"] > d5["vol_ma20"] * 1.1)).sum())
    if down_vol_days >= 3:
        vetoes.append(f"近5日{down_vol_days}日放量下跌")
    bias = (close / ma20 - 1) * 100 if ma20 > 0 else 0.0
    if bias > 10:
        vetoes.append(f"距MA20 {bias:.1f}% > 10%")
    high60 = d["high"].iloc[-60:-5].max()
    resistance = max(plat_hi if is_platform else 0.0, high60)
    if resistance > 0 and close < resistance and (resistance / close - 1) * 100 < 2:
        vetoes.append(f"距压力位{resistance:.2f} < 2%未突破")
    # ── V5 RR 淘汰（基于触发价的 RR1）──
    rr1 = rr_info.get("rr1", rr_info.get("rr_ratio", 0))
    if rr1 < 1.0:
        vetoes.append(f"RR1={rr1:.2f} < 1.0（第一目标收益不抵止损风险，禁止主动买入）")
    elif rr1 < 1.2:
        vetoes.append(f"RR1={rr1:.2f} < 1.2（偏弱，禁止PRIMARY_BUY）")
    if rr_info.get("stop_dist", 99) > 8:
        vetoes.append(f"止损空间{rr_info.get('stop_dist', 99):.1f}% > 8%")
    elif rr_info.get("stop_dist", 99) > 7:
        vetoes.append(f"止损空间{rr_info.get('stop_dist', 99):.1f}% > 7%（降低评级）")

    # ── V4 新增淘汰 ──
    # 量价背离 < 50
    if vp_score < 50:
        vetoes.append(f"量价结构分{vp_score:.0f} < 50（严重背离）")
    # 资金分 < 40（T1 强突破除外）
    if capital_score < 40 and not t1_confirm:
        vetoes.append(f"资金确认分{capital_score:.0f} < 40")
    # 高位放量滞涨：乖离>8% 且近3日量比>1.3 且近3日累计涨幅<1%
    if bias > 8 and vol_ma and vol_ma > 0:
        d3 = d.tail(3)
        if d3["vol"].mean() > vol_ma * 1.3 and d3["pct_chg"].sum() < 1:
            vetoes.append("高位放量滞涨")
    # 追高过滤：单日涨幅>7% 且 距MA20>8%（等待缩量回踩）
    if last_pct > 7 and bias > 8:
        vetoes.append(f"追高风险(单日+{last_pct:.1f}%，乖离{bias:.1f}%)")

    return len(vetoes) > 0, vetoes


# ══════════════════════════════════════════════════════════════
# 单股评分
# ══════════════════════════════════════════════════════════════

def score_stock(row: pd.Series, d: pd.DataFrame) -> V3Result:
    ts_code = str(row["ts_code"])
    close = float(d["close"].iloc[-1])

    r = V3Result(
        ts_code=ts_code,
        name=_s(row.get("name")),
        industry=_s(row.get("industry")),
        close=close,
        v2_score=_f(row.get("final_score_v2")),
    )

    buy_type, buy_reasons, type_info = classify_buy_type(d)
    r.buy_type = buy_type
    r.confirmed = type_info.get("confirmed", False)

    r.fund, fund_notes = score_fundamental(row)
    r.trend, trend_notes = score_trend(d)
    r.entry, entry_notes = score_entry(d, buy_type, buy_reasons)
    r.capital, cap_notes = score_capital(
        d, _f(row.get("institution_accumulation"), 50), _s(row.get("institution_state"), "未知"))
    r.vp, vp_notes = score_volume_price(d, buy_type)
    r.trigger, trig_notes = score_trigger(d, buy_type, type_info)
    # V5: RR 以触发价为锚（T3/T4 用精确触发价；已确认用现价）
    r.rr, rr_notes, rr_info = score_rr(d, type_info.get("trigger_price", 0.0), r.confirmed)
    r.notes = fund_notes + trend_notes + entry_notes + trig_notes + cap_notes + vp_notes + rr_notes

    r.ma20 = _f(d["ma20"].iloc[-1])
    r.ma60 = _f(d["ma60"].iloc[-1])
    # 触发价按买点类型区分：T3 临界用平台高x1.003，其余用前期压力位
    if type_info.get("trigger_price", 0) > 0:
        r.breakout_price = type_info["trigger_price"]
    else:
        r.breakout_price = max(type_info.get("resistance", 0.0), type_info.get("plat_hi", 0.0))
    r.stop_loss = rr_info.get("stop", close * 0.93)
    r.target1 = rr_info.get("target1", close * 1.08)
    r.target2 = rr_info.get("target2", close * 1.15)
    r.rr1 = rr_info.get("rr1", 0.0)
    r.rr2 = rr_info.get("rr2", 0.0)
    r.invalidation = rr_info.get("invalidation", r.stop_loss)
    # 触发价（无精确触发价时用 RR 里的 trigger 基准）
    r.trigger_price = rr_info.get("trigger", r.breakout_price)

    buy_score = (r.fund * WEIGHTS["FUND"] + r.trend * WEIGHTS["TREND"] +
                 r.entry * WEIGHTS["ENTRY"] + r.capital * WEIGHTS["CAPITAL"] +
                 r.vp * WEIGHTS["VP"] + r.rr * WEIGHTS["RR"])
    buy_score += ENTRY_BONUS.get(buy_type, 0.0)
    r.buy_score = max(0.0, min(100.0, buy_score))

    # ── V5 核心指标 ──
    r.trig_conf = score_trig_conf(d, buy_type, type_info, r.trigger, r.capital)
    # TRADE_ALPHA = BUY × (TRIG_CONF/100) × RR_FACTOR，归一化 0~100
    r.trade_alpha = r.buy_score * (r.trig_conf / 100.0) * rr_factor(r.rr1)
    r.trade_alpha = max(0.0, min(100.0, r.trade_alpha))
    # 买入区间: 触发价 ~ 触发价×1.02（已确认则现价 ~ ×1.02）
    zone_base = close if r.confirmed else max(r.trigger_price, close)
    r.buy_zone = f"{zone_base:.2f}~{zone_base * 1.02:.2f}"

    vetoed, vetoes = check_veto(d, buy_type, rr_info, r.vp, r.capital,
                                t1_confirm=(buy_type == "T1" and r.confirmed))
    r.veto = vetoes

    market = MARKET_POSITION.get(market_regime, MARKET_POSITION["NEUTRAL"])
    pos_primary, pos_probe_a, pos_probe_b, pos_probe_c = market

    # 治理红旗：附标签、记入 notes（禁 PRIMARY，PROBE 仓位上限压缩）
    flags = red_flag_map.get(ts_code, [])
    if flags:
        r.redflags = list(flags)
        r.notes.append(f"⚠治理红旗: {'；'.join(flags)}")

    # ── V5 交易状态机（第二十二~二十七节）──
    # RR1 < 1.0: 无论评分多高，直接 WATCH（第十四/十五节）
    if r.rr1 < 1.0:
        r.level = "WATCH"
        r.position = "0%"
        return r
    # T5: 禁止 PRIMARY/PROBE，只能 WATCH（第九节）
    if buy_type == "T5":
        r.level = "WATCH"
        r.position = "0%"
        return r

    # PRIMARY_BUY: ALPHA>=85 + BUY>=82 + ENTRY>=85 + TREND>=75 + RR1>=1.5 + VP>=75
    # 且 T1_CONFIRM / T2_CONFIRM / 强T3突破确认 + 无硬性淘汰
    strong_t3 = (buy_type == "T3" and r.confirmed)
    primary_ok = ((r.confirmed or strong_t3) and buy_type in ("T1", "T2", "T3")
                  and not vetoed and not flags
                  and r.trade_alpha >= 85 and r.buy_score >= 82 and r.entry >= 85
                  and r.trend >= 75 and r.rr1 >= 1.5 and r.vp >= 75)
    if primary_ok:
        r.level = "PRIMARY_BUY"
        r.position = pos_primary
        return r

    # BEAR 市场: 仅极强确认买点保留，其余 NEXT/WATCH
    if market_regime == "BEAR" and not (r.confirmed and r.trade_alpha >= 85):
        r.level = "NEXT" if r.buy_score >= 70 else "WATCH"
        r.position = "0%"
        return r

    # PROBE-A: ALPHA>=78 + T3 + ENTRY>=80 + TRIGGER>=85 + RR1>=1.5 + BUY>=82
    if (buy_type == "T3" and r.trade_alpha >= 78 and r.buy_score >= 82
            and r.entry >= 80 and r.trigger >= 85 and r.rr1 >= 1.5
            and not vetoed and r.trend >= 70):
        r.level = "PROBE-A"
        r.position = compress_position(pos_probe_a) if flags else pos_probe_a
        return r
    # PROBE-B: ALPHA 72~78 + T3 + ENTRY>=78 + TRIGGER>=80 + RR1>=1.3
    # RR1 < 1.5 时仓位上限 10%（第二十四节）
    if (buy_type == "T3" and r.trade_alpha >= 72 and r.entry >= 78
            and r.trigger >= 80 and r.rr1 >= 1.3
            and not vetoed and r.trend >= 70):
        r.level = "PROBE-B"
        base = "8-10%" if r.rr1 < 1.5 else pos_probe_b
        r.position = compress_position(base) if flags else base
        return r
    # PROBE-C: T4 + ENTRY>=75 + TREND>=70 + RR1>=1.5
    if (buy_type == "T4" and r.entry >= 75 and r.trend >= 70
            and r.rr1 >= 1.5 and not vetoed and r.buy_score >= 75):
        r.level = "PROBE-C"
        r.position = compress_position(pos_probe_c) if flags else pos_probe_c
        return r

    # NEXT: 高质量 + 未触发 + 触发后有较高交易价值（第二十六节）
    # WATCH: ALPHA < 65 或 RR < 1.2 或趋势未确认（第二十七节）
    if (r.buy_score >= 80 and r.entry >= 75 and r.trend >= 75
            and r.rr1 >= 1.5 and r.trade_alpha >= 65):
        r.level = "NEXT"
    elif r.buy_score >= 70:
        r.level = "NEXT" if r.trade_alpha >= 65 else "WATCH"
    else:
        r.level = "WATCH"
    r.position = "0%"
    return r


# ══════════════════════════════════════════════════════════════
# 输出
# ══════════════════════════════════════════════════════════════

# 等级优先序（V5）：PRIMARY > PROBE-A > PROBE-B > PROBE-C > NEXT > WATCH
LEVEL_ORDER = {"PRIMARY_BUY": 0, "PROBE-A": 1, "PROBE-B": 2, "PROBE-C": 3,
               "NEXT": 4, "WATCH": 5}
# 买点类型优先级（数字越小越优先）
TYPE_ORDER = {"T1": 0, "T2": 1, "T3": 2, "T4": 3, "T5": 4}


def sort_final(results: list[V3Result]) -> list[V3Result]:
    """V5 最终排序（第二十八~二十九节）。

    第一层: 交易状态 PRIMARY > PROBE-A > PROBE-B > PROBE-C > NEXT > WATCH
    第二层: TRADE_ALPHA DESC（交易价值，而非 BUY）
    同分: ENTRY DESC > TRIGGER DESC > RR DESC > BUY DESC

    禁止用 BUY_SCORE DESC 作为最终交易排名——BUY 回答"股票质量如何"，
    TRADE_ALPHA 回答"这是不是一笔值得做的交易"。
    """
    return sorted(results, key=lambda r: (
        LEVEL_ORDER.get(r.level, 9),
        -r.trade_alpha,
        -r.entry,
        -r.trigger,
        -r.rr,
        -r.buy_score,
    ))


def sort_b_board(results: list[V3Result]) -> list[V3Result]:
    """B榜（触发即买榜）排序：TRADE_ALPHA > TRIGGER > ENTRY > RR。"""
    return sorted(results, key=lambda r: (
        -r.trade_alpha, -r.trigger, -r.entry, -r.rr,
    ))


def build_trigger_info(r: V3Result, d: pd.DataFrame) -> None:
    """为 NEXT（及 PROBE_BUY 升级路径）估算触发条件与触发后等级。

    模拟假设：次日以触发价 T 收盘、放量>=1.3x、无冲高回落 -> 买点升级为 T1_CONFIRM。
    重估 ENTRY（基准92+触发溢价）与 RR（基于触发价用 score_rr 重算 RR1），
    再用六层权重重算 BUY_SCORE 与 TRADE_ALPHA，判断触发后能否达到 PRIMARY_BUY。
    """
    close = r.close
    trigger = r.breakout_price if r.breakout_price > close * 0.995 else close * 1.02
    r.trigger_price = trigger

    # 触发后重估 ENTRY：T1 基准 92，距 MA20 溢价（触发价通常贴着 MA20 上方）
    proj_entry = 92.0
    ma20 = r.ma20 if r.ma20 > 0 else trigger * 0.95
    bias = (trigger / ma20 - 1) * 100
    if 0 <= bias <= 5:
        proj_entry += 3
    elif bias > 10:
        proj_entry -= 15
    proj_entry = min(100.0, proj_entry)

    # 触发后重估 RR：以触发价为锚，复用 score_rr（结构止损 + RR1 分档）
    proj_rr, _, proj_info = score_rr(d, trigger, confirmed=True)
    proj_rr1 = proj_info.get("rr1", 0.0)
    r.proj_alpha_rr1 = proj_rr1

    # 触发后重估 TREND：突破使 MA20 继续向上、股价站上 -> 至少 B+，原趋势高则保持
    proj_trend = max(r.trend, 80.0) if r.trend >= 78 else min(100.0, r.trend + 12)
    # 量价与资金：放量突破确认
    proj_vp = max(r.vp, 80.0)
    proj_cap = min(100.0, r.capital + 10)

    proj_buy = (r.fund * WEIGHTS["FUND"] + proj_trend * WEIGHTS["TREND"] +
                proj_entry * WEIGHTS["ENTRY"] + proj_cap * WEIGHTS["CAPITAL"] +
                proj_vp * WEIGHTS["VP"] + proj_rr * WEIGHTS["RR"] + ENTRY_BONUS["T1"])
    proj_buy = max(0.0, min(100.0, proj_buy))
    r.proj_buy = proj_buy

    # V5: 触发后 TRIG_CONF 高（已确认） + RR_FACTOR -> proj_alpha
    proj_conf = 90.0
    r.proj_alpha = max(0.0, min(100.0, proj_buy * (proj_conf / 100.0) * rr_factor(proj_rr1)))

    # 触发后等级与仓位（市场联动）
    market = MARKET_POSITION.get(market_regime, MARKET_POSITION["NEUTRAL"])
    pos_primary, pos_probe_a, _, _ = market
    capped_pa = compress_position(pos_probe_a) if r.redflags else pos_probe_a
    if ((not r.redflags) and r.proj_alpha >= 85 and proj_buy >= 82 and proj_entry >= 85
            and proj_trend >= 75 and proj_rr1 >= 1.5 and proj_vp >= 75):
        r.proj_level = "PRIMARY_BUY"
        r.proj_position = pos_primary
    elif r.proj_alpha >= 78 and proj_rr1 >= 1.5:
        r.proj_level = "PROBE-A"
        r.proj_position = capped_pa
    else:
        r.proj_level = "NEXT"
        r.proj_position = "0%"

    r.trigger_condition = (
        f"放量(≥1.3x均量)收盘突破{trigger:.2f}且收盘位置≥80%"
    )


def print_dual_boards(results: list[V3Result], top_n: int, trade_date: str) -> None:
    """输出 A榜（当前可交易）/ B榜（触发即买）双榜单（V5 格式）。"""
    probe_levels = ("PRIMARY_BUY", "PROBE-A", "PROBE-B", "PROBE-C")
    list_a = [r for r in results if r.level in probe_levels]
    list_b = sort_b_board([r for r in results if r.level == "NEXT"])

    logger.info("")
    logger.info("═" * 80)
    logger.info("V5.0 双榜单 | 交易日 %s | 市场环境: %s", trade_date, market_regime)
    logger.info("═" * 80)

    # ── A榜：当前可交易（TRADE_ALPHA DESC）──
    logger.info("")
    logger.info("① 【A榜：当前可交易】(PRIMARY_BUY/PROBE-A/B/C，共 %d 只)", len(list_a))
    if not list_a:
        logger.info("  当前没有适合直接买入/试仓的股票。")
    else:
        logger.info("%-5s %-12s %-8s %6s %6s %6s %5s %-6s %-9s %-9s %-8s %-7s %s",
                    "排名", "代码", "名称", "ALPHA", "BUY", "ENTRY", "TRIG", "买点",
                    "等级", "触发价", "止损", "目标1", "仓位")
        for i, r in enumerate(list_a[:top_n]):
            trig = "✓" if r.confirmed else f"{r.trigger:.0f}"
            logger.info("%-5d %-12s %-8s %6.1f %6.1f %6.0f %5s %-6s %-9s %-9.2f %-8.2f %-7.2f %s",
                        i + 1, r.ts_code, r.name + ("⚠" if r.redflags else ""),
                        r.trade_alpha, r.buy_score, r.entry,
                        trig, r.buy_type + ("✓" if r.confirmed else ""), r.level,
                        r.trigger_price or r.breakout_price, r.stop_loss, r.target1, r.position)

    # ── B榜：触发即买（TRADE_ALPHA DESC）──
    logger.info("")
    logger.info("② 【B榜：触发即买】(仅 NEXT，共 %d 只，触发前禁止买入)", len(list_b))
    if not list_b:
        logger.info("  （空）")
    else:
        logger.info("%-5s %-12s %-8s %6s %6s %6s %5s %-6s %-9s %-9s %-12s %s",
                    "排名", "代码", "名称", "ALPHA", "BUY", "ENTRY", "TRIG", "买点",
                    "当前等级", "触发价", "触发后等级", "触发后仓位")
        for i, r in enumerate(list_b[:top_n]):
            logger.info("%-5d %-12s %-8s %6.1f %6.1f %6.0f %5.0f %-6s %-9s %-9.2f %-12s %s",
                        i + 1, r.ts_code, r.name + ("⚠" if r.redflags else ""),
                        r.proj_alpha, r.buy_score, r.entry,
                        r.trigger, r.buy_type, r.level, r.trigger_price,
                        r.proj_level, r.proj_position)

    # ── 统计 ──
    n_primary = sum(1 for r in results if r.level == "PRIMARY_BUY")
    n_pa = sum(1 for r in results if r.level == "PROBE-A")
    n_pb = sum(1 for r in results if r.level == "PROBE-B")
    n_pc = sum(1 for r in results if r.level == "PROBE-C")
    n_next = len(list_b)
    logger.info("")
    if n_primary == 0:
        logger.info(">>> 当前没有符合最高胜率买点(PRIMARY_BUY)的股票。")
    if n_pa + n_pb + n_pc == 0:
        logger.info(">>> 当前没有适合试仓(PROBE)的股票。")
    logger.info("等级统计: PRIMARY_BUY=%d, PROBE-A=%d, PROBE-B=%d, PROBE-C=%d, NEXT=%d, WATCH=%d",
                n_primary, n_pa, n_pb, n_pc, n_next,
                len(results) - n_primary - n_pa - n_pb - n_pc - n_next)
    logger.info("排序规则: 状态分层 > TRADE_ALPHA DESC > ENTRY > TRIGGER > RR > BUY")


def print_top5_detail(results: list[V3Result], trade_date: str) -> None:
    """③ TOP 股票交易计划（V5 第三十四节 C 部分，20 字段模板）。"""
    probe_levels = ("PRIMARY_BUY", "PROBE-A", "PROBE-B", "PROBE-C")
    list_a = [r for r in results if r.level in probe_levels]
    list_b = sort_b_board([r for r in results if r.level == "NEXT"])
    top5 = (list_a + list_b)[:5]

    logger.info("")
    logger.info("─" * 76)
    logger.info("③ TOP 交易计划（A榜优先）| %s | 市场: %s", trade_date, market_regime)
    logger.info("─" * 76)
    for i, r in enumerate(top5):
        logger.info("")
        logger.info("【%d】%s%s (%s) %s", i + 1,
                    r.name, "⚠" if r.redflags else "", r.ts_code, r.industry)
        logger.info("  当前状态: %s", r.level)
        if r.redflags:
            logger.info("  ⚠治理红旗: %s（仓位已强制压缩）", "；".join(r.redflags))
        logger.info("  TRADE_ALPHA: %.1f | BUY: %.1f | ENTRY: %.0f | TRIGGER: %.0f | TRIG_CONF: %.0f",
                    r.trade_alpha, r.buy_score, r.entry, r.trigger, r.trig_conf)
        logger.info("  当前价格: %.2f", r.close)
        logger.info("  MA20: %.2f | 核心支撑: %.2f", r.ma20,
                    r.stop_loss if r.level in probe_levels else r.ma20)
        logger.info("  平台压力: %.2f", r.breakout_price if r.breakout_price > 0 else 0.0)
        trigger_disp = r.trigger_price if r.trigger_price else r.breakout_price
        logger.info("  触发价: %.2f", trigger_disp)
        zone_base = r.close if r.confirmed else trigger_disp
        logger.info("  买入区间: %.2f ~ %.2f", zone_base, zone_base * 1.02)
        logger.info("  止损: %.2f（止损幅度 %.1f%%）", r.stop_loss,
                    (1 - r.stop_loss / trigger_disp) * 100 if trigger_disp > 0 else 0.0)
        logger.info("  目标1: %.2f | 目标2: %.2f", r.target1, r.target2)
        logger.info("  RR1: %.2f | RR2: %.2f", r.rr1, r.rr2)
        logger.info("  买入理由: %s", "；".join(r.notes[:3]) if r.notes else "")
        # 主要风险
        risks = [v for v in r.veto[:2]] if r.veto else []
        if not risks:
            if r.rr1 < 1.5:
                risks.append(f"RR1={r.rr1:.2f} 偏弱，需低仓位")
            if (r.trigger_price / r.close - 1) * 100 > 3:
                risks.append(f"距触发价{(r.trigger_price/r.close-1)*100:.1f}% 尚远")
        logger.info("  主要风险: %s", "；".join(risks) if risks else "结构健康，正常短线风险")
        # 失效条件
        if r.veto:
            fails = [f"硬性淘汰: {v}" for v in r.veto[:2]]
        else:
            fails = [f"收盘跌破 {r.invalidation:.2f}（核心支撑+放量）",
                     f"放量跌破MA20({r.ma20:.2f})且MA20拐头向下"]
        logger.info("  失效条件: %s", "；".join(fails))
        logger.info("  建议仓位: %s", r.position)
        if r.level in probe_levels:
            if r.confirmed:
                why = f"TRADE_ALPHA={r.trade_alpha:.0f}，{r.buy_type}_CONFIRM 已确认，RR1={r.rr1:.2f}，买点成立"
            else:
                why = f"距触发价{(trigger_disp/r.close-1)*100:.1f}%，TRIGGER={r.trigger:.0f}，接近可执行买点，等待放量突破确认"
        elif r.veto:
            why = f"当前禁止买入：{'；'.join(r.veto[:2])}。需先修复结构后重新评估"
        elif trigger_disp > 0 and trigger_disp <= r.close:
            why = f"已越过触发价{trigger_disp:.2f}但未获有效突破确认（收盘位置/量能不达标），等待回踩缩量后重新突破"
        else:
            why = f"尚未突破，距触发价{(trigger_disp/r.close-1)*100:.1f}%，触发后 ALPHA≈{r.proj_alpha:.0f} -> {r.proj_level}，禁止提前买入"
        logger.info("  为什么现在买/不能买: %s", why)
    logger.info("")
    logger.info("─" * 76)


def save_results(results: list[V3Result], trade_date: str) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = REPORT_DIR / f"eld_buy_rank_{trade_date}.csv"
    df = pd.DataFrame([{
        "ts_code": r.ts_code, "name": r.name, "industry": r.industry,
        "redflag": " | ".join(r.redflags),
        "close": r.close, "v2_score": r.v2_score,
        "trade_alpha": round(r.trade_alpha, 1),
        "buy_score": round(r.buy_score, 1), "fund": round(r.fund, 1),
        "trend": round(r.trend, 1), "entry": round(r.entry, 1),
        "capital": round(r.capital, 1), "vp": round(r.vp, 1), "rr": round(r.rr, 1),
        "trigger": round(r.trigger, 1), "trig_conf": round(r.trig_conf, 1),
        "rr1": round(r.rr1, 2), "rr2": round(r.rr2, 2),
        "confirmed": r.confirmed,
        "buy_type": r.buy_type, "level": r.level, "position": r.position,
        "ma20": round(r.ma20, 2), "ma60": round(r.ma60, 2),
        "breakout_price": round(r.breakout_price, 2),
        "stop_loss": round(r.stop_loss, 2), "target1": round(r.target1, 2),
        "target2": round(r.target2, 2), "buy_zone": r.buy_zone,
        "invalidation": round(r.invalidation, 2),
        "trigger_condition": r.trigger_condition,
        "trigger_price": round(r.trigger_price, 2) if r.trigger_price else 0.0,
        "proj_level": r.proj_level, "proj_position": r.proj_position,
        "proj_buy": round(r.proj_buy, 1),
        "proj_alpha": round(r.proj_alpha, 1),
        "veto": " | ".join(r.veto),
        "notes": " | ".join(r.notes),
    } for r in results])
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    logger.info("CSV 已保存: %s", csv_path)

    probe_levels = ("PRIMARY_BUY", "PROBE-A", "PROBE-B", "PROBE-C")
    list_a = [r for r in results if r.level in probe_levels]
    list_b = sort_b_board([r for r in results if r.level == "NEXT"])
    md_path = REPORT_DIR / f"eld_buy_rank_{trade_date}.md"
    lines = [
        f"# ELD V5.0 Trade Alpha 双榜单买入排序报告 - {trade_date}（市场环境: {market_regime}）",
        "",
        "> TRADE_ALPHA = BUY × TRIGGER_CONFIDENCE × RR_FACTOR。排序铁律: 状态分层(PRIMARY>PROBE-A>PROBE-B>PROBE-C>NEXT>WATCH) -> 同层内 TRADE_ALPHA DESC -> ENTRY > TRIGGER > RR > BUY",
        "> Trade Alpha = 交易价值，不是股票质量。RR1<1.0 直接 WATCH，禁止任何主动买入。宁可输出0个PRIMARY_BUY，也绝不凑数量。",
        "",
        f"## A榜：当前可交易（{len(list_a)} 只）",
        "",
    ]
    if list_a:
        lines += [
            "| 排名 | 代码 | 名称 | ALPHA | BUY | ENTRY | TRIG | RR1 | 买点 | 等级 | 触发价 | 止损 | 目标1 | 仓位 |",
            "|------|------|------|-------|-----|-------|------|-----|------|------|--------|------|-------|------|",
        ]
        for i, r in enumerate(list_a[:20]):
            lines.append(
                f"| {i+1} | {r.ts_code} | {r.name}{'⚠' if r.redflags else ''} | {r.trade_alpha:.1f} | {r.buy_score:.1f} | {r.entry:.0f} | "
                f"{r.trigger:.0f} | {r.rr1:.2f} | {r.buy_type} | {r.level} | "
                f"{r.trigger_price or r.breakout_price:.2f} | {r.stop_loss:.2f} | {r.target1:.2f} | {r.position} |"
            )
    else:
        lines += ["（空）"]
    if not any(r.level == "PRIMARY_BUY" for r in results):
        lines += ["", "**当前没有符合最高胜率买点(PRIMARY_BUY)的股票。**"]
    if not list_a:
        lines += ["", "**当前没有适合试仓(PROBE)的股票。**"]
    lines += [
        "",
        f"## B榜：触发即买（{len(list_b)} 只，触发前禁止买入）",
        "",
        "| 排名 | 代码 | 名称 | ALPHA | BUY | ENTRY | TRIG | RR1 | 买点 | 触发价 | 触发后等级 | 触发后仓位 |",
        "|------|------|------|-------|-----|-------|------|-----|------|--------|------------|------------|",
    ]
    for i, r in enumerate(list_b[:20]):
        lines.append(
            f"| {i+1} | {r.ts_code} | {r.name}{'⚠' if r.redflags else ''} | {r.proj_alpha:.1f} | {r.buy_score:.1f} | {r.entry:.0f} | "
            f"{r.trigger:.0f} | {r.proj_alpha_rr1:.2f} | {r.buy_type} | "
            f"{r.trigger_price:.2f} | {r.proj_level} | {r.proj_position} |"
        )
    top5 = (list_a + list_b)[:5]
    lines += ["", "## TOP 5 详细分析（A榜优先，不足补B榜）", ""]
    for i, r in enumerate(top5):
        lines += [f"### {i+1}. {r.name}{'⚠' if r.redflags else ''} ({r.ts_code}) {r.industry}", ""]
        lines += [f"- **状态**: {r.level} | ALPHA={r.trade_alpha:.1f} | BUY={r.buy_score:.1f} | ENTRY={r.entry:.0f} | TRIG={r.trigger:.0f} | TRIG_CONF={r.trig_conf:.0f} | RR1={r.rr1:.2f} | 买点={r.buy_type}{'_CONFIRM' if r.confirmed else ''}"]
        if r.redflags:
            lines += [f"- **⚠治理红旗**: {'；'.join(r.redflags)}（PRIMARY 已禁用，仓位上限压缩）"]
        lines += [f"- **买入理由**: {'；'.join(r.notes[:3])}"]
        if r.level in probe_levels:
            if r.confirmed:
                lines += [f"- **买点**: {r.buy_type}_CONFIRM 已确认 | 止损 {r.stop_loss:.2f} | 目标1 {r.target1:.2f} | 目标2 {r.target2:.2f}"]
            else:
                lines += [f"- **触发价**: {r.trigger_price:.2f} | 条件: {r.trigger_condition}"]
                lines += [f"- **止损/目标**: 止损 {r.stop_loss:.2f} | 目标1 {r.target1:.2f} | 目标2 {r.target2:.2f}"]
        else:
            lines += [f"- **触发条件**: {r.trigger_condition}（触发后 BUY≈{r.proj_buy:.0f} -> {r.proj_level}，仓位 {r.proj_position}）"]
            lines += [f"- **触发/止损/目标**: 触发 {r.trigger_price:.2f} | 止损 {r.stop_loss:.2f} | 目标1 {r.target1:.2f} | 目标2 {r.target2:.2f}"]
        if r.veto:
            lines += [f"- **硬性淘汰**: {'；'.join(r.veto[:3])}"]
        lines += [f"- **建议仓位**: {r.position}", ""]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Markdown 已保存: %s", md_path)


# ══════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════

def main():
    global market_regime, red_flag_map

    parser = argparse.ArgumentParser(description="ELD V5.0 Trade Alpha 终极买入排序引擎")
    parser.add_argument("--date", default="", help="交易日 (默认: 最近交易日)")
    parser.add_argument("--top", type=int, default=20, help="输出 TOP N (默认20)")
    parser.add_argument("--no-save", action="store_true", help="不保存文件")
    args = parser.parse_args()

    trade_date = args.date or get_last_trade_date()
    market_regime = load_market_regime(trade_date)
    red_flag_map = load_red_flags(trade_date)
    logger.info("=" * 60)
    logger.info("ELD V5.0 Trade Alpha 终极买入排序引擎 开始 | 交易日: %s | 市场环境: %s",
                trade_date, market_regime)
    logger.info("=" * 60)

    candidates = load_candidates(trade_date)
    codes = set(candidates["ts_code"].astype(str))
    daily_all = load_daily_cached(trade_date, codes)

    results: list[V3Result] = []
    skip = 0
    daily_cache_for_trigger: dict[str, pd.DataFrame] = {}
    probe_levels = ("PRIMARY_BUY", "PROBE-A", "PROBE-B", "PROBE-C")
    for _, row in candidates.iterrows():
        ts_code = str(row["ts_code"])
        df = daily_all.get(ts_code)
        if df is None or len(df) < 60:
            skip += 1
            continue
        try:
            d = compute_indicators(df)
            r = score_stock(row, d)
            if r.level == "NEXT" or r.level in probe_levels:
                daily_cache_for_trigger[ts_code] = d
            results.append(r)
        except Exception as exc:
            logger.warning("评分失败 %s: %s", ts_code, exc)
            skip += 1

    logger.info("评分完成: %d 只，跳过 %d 只", len(results), skip)

    # B榜触发模拟（NEXT 与 PROBE 的 PRIMARY 升级路径）
    for r in results:
        if (r.level == "NEXT" or r.level in probe_levels) and r.ts_code in daily_cache_for_trigger:
            build_trigger_info(r, daily_cache_for_trigger[r.ts_code])

    # 两阶段排序铁律：状态分层 -> 同层内 ENTRY>买点类型>BUY>TREND>RR>资金
    results = sort_final(results)

    level_counts: dict[str, int] = {}
    for r in results:
        level_counts[r.level] = level_counts.get(r.level, 0) + 1
    logger.info("等级分布: %s", level_counts)

    print_dual_boards(results, args.top, trade_date)
    print_top5_detail(results, trade_date)

    if not args.no_save:
        save_results(results, trade_date)


if __name__ == "__main__":
    main()
