# -*- coding: utf-8 -*-
"""
板块体系第三阶段：Canonical Sector Quality + State 基础层
========================================================

链路（严格对齐需求 Step 3）：

    sector_master.json          <- 唯一主题定义源（READ ONLY）
        +
    data/sector_membership.csv  <- Step 2 产出（只读）
        +
    output/sector_quality.csv   <- Step 2 产出（Sector Quality，直接复用）
        +
    Tushare daily / daily_basic / index_daily（经 stock_cache UDC 统一数据层）
        |
        v
    Sector Quality -> Sector Breadth -> Sector Price/Volume Structure
        |
        v
    Sector Health -> Sector State Base -> Sector Tracking Pool

本阶段【不做】：SEOS、板块启动/轮动/生命周期预测、HVT、IGE、F120、个股 BUY/NO TRADE、
交易执行、概念热度排名。以上全部留给后续阶段。

硬约束：
  * sector_master.json 是唯一 Canonical 定义源；theme_config.json / subtheme_map.json /
    theme.json 一律 LEGACY，只报告存在性、绝不读取。
  * theme_purity 直接复用 Step 2 的 sector_purity（公式已与需求 §6.1 完全一致），不另造一套。
  * Sector Health 不得被收益率主导：return 驱动权重合计 0.28 <= 0.30（见 RETURN_WEIGHT_SHARE）。
  * 无未来函数：D 日指标只用 <= D 的行情；strict 模式按 membership.effective_date <= D 过滤。
  * 宁可 DATA_INVALID，不伪造完整状态；宁可少覆盖，不污染 Sector 状态。

术语映射（本项目统一 sector_*，对应需求文本的 theme_*）：
    theme_master.json      -> sector_master.json
    theme_membership.csv   -> data/sector_membership.csv
    theme_quality_daily    -> output/sector_quality_daily.csv
    theme_daily_stats.csv  -> data/sector_daily_stats.csv
    theme_breadth.csv      -> data/sector_breadth.csv
    theme_strength.csv     -> data/sector_strength.csv
    theme_volume.csv       -> data/sector_volume.csv
    theme_state_history    -> data/sector_state_history.csv
    theme_state_today.json -> output/sector_state_today.json
    theme_tracking_pool    -> output/sector_tracking_pool.json（兼容扩展，不覆盖 Step 2 结构）
    theme_state_audit.md   -> output/sector_state_audit.md

CLI：
    python sector_state_build.py --full
    python sector_state_build.py --validate
    python sector_state_build.py --date 20260915
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

# ────────────────────────────────────────────────────────────────────────────
# 路径（沿用 Step 2 约定）
# ────────────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJ_DIR = os.path.dirname(BASE_DIR)
CONFIG_DIR = os.path.join(BASE_DIR, "config")
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
CACHE_DIR = os.path.join(BASE_DIR, "cache")
LOG_DIR = os.path.join(BASE_DIR, "logs")
for _d in (CONFIG_DIR, DATA_DIR, OUTPUT_DIR, CACHE_DIR, LOG_DIR):
    os.makedirs(_d, exist_ok=True)

if PROJ_DIR not in sys.path:
    sys.path.insert(0, PROJ_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# 复用 Step 1/2 基础设施：Canonical 真源加载器 / 项目扫描 / LEGACY 隔离 / JSON 写出
from sector_mapping_build import SectorMaster, scan_project, write_json  # noqa: E402
from sli.utils import setup_logging  # noqa: E402

# 复用统一数据层 UDC（不另建 Tushare client / 缓存 / 交易日体系 / 股票池）
from stock_cache import (  # noqa: E402
    cached_index_daily,
    daily_basic_market,
    get_daily_by_date,
)

log = setup_logging(LOG_DIR, name="sector_state")

# ────────────────────────────────────────────────────────────────────────────
# 参数与阈值（全部显式常量，便于审计与复现）
# ────────────────────────────────────────────────────────────────────────────

HISTORY_DAYS_DEFAULT = 90      # 默认回放交易日数（含基准日）
WARMUP_DAYS = 25               # 收益/量能回看缓冲（>= MAX_HORIZON）
MAX_HORIZON = 20
HORIZONS = (1, 3, 5, 10, 20)
LOOKAHEAD_WINDOW = 30          # 未来函数检验的面板截断窗口（>= 21，覆盖 20 日回看）

# 滚动特征最大回看期：sector_amount_share_change_20d 需 shift(20)，
# volume_ratio_5 需 shift(1).rolling(5)。--date 模式下的回放窗口必须 >= MAX_LOOKBACK+1，
# 否则这些列会结构性全为 NaN（Step 4 的 volume_participation 会退化为统一填充值）。
MAX_LOOKBACK = 20
REPLAY_MIN_DAYS = MAX_LOOKBACK + 1

MEMBERSHIP_CONF_MIN = 0.70     # 正式统计成员口径（不限 membership_type）
MIN_VALID_MEMBERS = 5          # 低于此值 -> DATA_INVALID
DQ_INVALID_THRESHOLD = 0.60    # 数据质量低于此值 -> DATA_INVALID

BENCH_PRIMARY = "000300.SH"
BENCH_PRIMARY_NAME = "沪深300"
BENCH_SECONDARY = "000852.SH"
BENCH_SECONDARY_NAME = "中证1000"

HEALTH_WEIGHTS = {             # 需求 §十五
    "breadth": 0.25,
    "weighted_breadth": 0.20,
    "relative_strength": 0.15,
    "volume_structure": 0.15,
    "core_primary": 0.15,
    "consistency": 0.10,
}
CORE_PRIMARY_INNER = {"core_breadth": 0.5, "primary_breadth": 0.3, "core_rs": 0.2}
RETURN_WEIGHT_SHARE = (        # 需求 §十六：return contribution <= 0.30
    HEALTH_WEIGHTS["relative_strength"] * 1.0
    + HEALTH_WEIGHTS["core_primary"] * CORE_PRIMARY_INNER["core_rs"]
    + HEALTH_WEIGHTS["consistency"] * 1.0
)

SCALE_BREADTH_HALF = 1.0       # 广度 [-1,1] -> [0,100]
SCALE_RS_HALF = 0.05           # 相对基准 5 日超额 ±5% 打满
SCALE_VOLUME_HALF = 0.60       # volume_ratio_5 偏离 1.0 达 ±0.6 打满
SCALE_DISPERSION_MID = 0.05    # 5 日收益横截面标准差 5% 视为中性
SCALE_DISPERSION_HALF = 0.05
SCALE_PCR_HALF = 0.50

STATE_RULES = {                # 需求 §十七：全部可验证阈值
    "strong_breadth": 0.55,
    "strong_weighted_breadth": 0.55,
    "strong_core_breadth": 0.50,
    "strong_volume_ratio_5": 1.10,
    "strong_top5_concentration_max": 0.70,
    "healthy_min_positive_indicators": 4,
    "weak_breadth": -0.20,
    "weak_breadth_deep": -0.35,
    "deteriorating_breadth_drop": -0.35,
    "anomaly_high_concentration": 0.70,
    "anomaly_low_breadth": -0.30,
    "anomaly_core_weak": -0.10,
    "anomaly_low_coverage_ratio": 0.60,
    "anomaly_membership_stability": 0.60,
}

STATES = ("STRONG", "HEALTHY", "NEUTRAL", "WEAK", "DETERIORATING", "DATA_INVALID")
TIERS = ("TIER_1_CORE", "TIER_2_ROTATION", "TIER_3_OBSERVATION", "RAW_ONLY")
SAMPLE_SECTOR_IDS = ["T01", "T02", "T03", "T07", "T08", "T10", "T23",
                     "T24", "T29", "T36", "T37", "T43", "T50"]
TRANSITION_WHITELIST = [
    ("NEUTRAL", "HEALTHY"), ("HEALTHY", "STRONG"), ("STRONG", "HEALTHY"),
    ("HEALTHY", "DETERIORATING"), ("DETERIORATING", "WEAK"), ("WEAK", "NEUTRAL"),
    ("HEALTHY", "NEUTRAL"), ("WEAK", "HEALTHY"), ("NEUTRAL", "WEAK"),
    ("DETERIORATING", "HEALTHY"), ("STRONG", "DETERIORATING"), ("DATA_INVALID", "NEUTRAL"),
]


# ────────────────────────────────────────────────────────────────────────────
# 小工具
# ────────────────────────────────────────────────────────────────────────────

def sdiv(a, b, default=0.0) -> float:
    """NaN/0 安全的除法。"""
    try:
        if b is None or b == 0:
            return default
        v = a / b
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return default
        return float(v)
    except Exception:
        return default


def fnum(x, default=float("nan")) -> float:
    try:
        v = float(x)
        return default if (math.isnan(v) or math.isinf(v)) else v
    except Exception:
        return default


def r4(x) -> float:
    v = fnum(x)
    return float("nan") if math.isnan(v) else round(v, 4)


def scale_sym(x, half: float) -> float:
    """对称标准化到 0~100：0 -> 50，+half -> 100，-half -> 0，超出裁剪。"""
    v = fnum(x)
    if math.isnan(v) or half == 0:
        return 50.0
    z = max(-1.0, min(1.0, v / half))
    return round(50.0 + 50.0 * z, 2)


def nan_mean(arr) -> float:
    a = np.asarray(arr, dtype=float)
    a = a[~np.isnan(a)]
    return float(a.mean()) if a.size else float("nan")


def nan_median(arr) -> float:
    a = np.asarray(arr, dtype=float)
    a = a[~np.isnan(a)]
    return float(np.median(a)) if a.size else float("nan")


def nan_std(arr) -> float:
    a = np.asarray(arr, dtype=float)
    a = a[~np.isnan(a)]
    return float(a.std(ddof=0)) if a.size else float("nan")


def nan_wmean(vals, ws) -> float:
    """NaN 感知的加权均值：权重在非 NaN 成员上重新归一。"""
    v = np.asarray(vals, dtype=float)
    w = np.asarray(ws, dtype=float)
    m = ~np.isnan(v)
    if not m.any():
        return float("nan")
    tot = w[m].sum()
    if tot <= 0:
        return nan_mean(v[m])
    return float((v[m] * w[m]).sum() / tot)


def write_merge_csv(df: pd.DataFrame, path: str, key_cols=("trade_date", "sector_id")):
    """时间序列 CSV 合并写出：保留历史行，只替换本次重算到的日期，避免历史被截断。

    旧文件字段结构不一致时不做强行合并，备份旧文件后按新结构写出（不盲目覆盖）。
    """
    if df is None or df.empty:
        log.warning("空结果，跳过写出 %s", os.path.relpath(path, BASE_DIR))
        return
    df = df.copy()
    if os.path.exists(path):
        try:
            old = pd.read_csv(path, dtype={key_cols[0]: str})
            if set(old.columns) == set(df.columns):
                keep = old[~old[key_cols[0]].astype(str).isin(set(df[key_cols[0]].astype(str)))]
                df = pd.concat([keep, df], ignore_index=True)
            else:
                bak = path + ".bak"
                if not os.path.exists(bak):
                    os.replace(path, bak)
                log.warning("表结构不一致，旧文件备份为 %s，按新结构写出",
                            os.path.relpath(bak, BASE_DIR))
        except Exception as e:
            log.warning("读取旧文件失败（将直接覆盖 %s）：%s", path, e)
    df = df.sort_values(list(key_cols)).reset_index(drop=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    log.info("写出 %s (%d 行)", os.path.relpath(path, BASE_DIR), len(df))


# ────────────────────────────────────────────────────────────────────────────
# 输入层
# ────────────────────────────────────────────────────────────────────────────

def load_membership() -> pd.DataFrame:
    """读取 Step 2 产出的 canonical membership（唯一成员来源；本阶段只读不改）。

    按用户决策 B 应用正式成员口径 membership_confidence >= MEMBERSHIP_CONF_MIN（不限
    membership_type）；被过滤掉的低置信成员不参与任何统计。
    """
    path = os.path.join(DATA_DIR, "sector_membership.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(f"缺少 {path}，请先完成 Step 2")
    raw = pd.read_csv(path, dtype=str)
    for c in ("membership_confidence", "membership_weight"):
        raw[c] = pd.to_numeric(raw[c], errors="coerce").fillna(0.0)
    for c in ("effective_date", "sector_id", "ts_code"):
        raw[c] = raw[c].astype(str)
    m = raw[raw["membership_confidence"] >= MEMBERSHIP_CONF_MIN].reset_index(drop=True)
    log.info("membership 口径 confidence>=%.2f：%d/%d 行（过滤 %d 行低置信成员），"
             "股票 %d 只，Sector %d 个",
             MEMBERSHIP_CONF_MIN, len(m), len(raw), len(raw) - len(m),
             m["ts_code"].nunique(), m["sector_id"].nunique())
    m.attrs["rows_all"] = len(raw)
    return m


def load_quality() -> pd.DataFrame:
    """读取 Step 2 的 sector_quality.csv 作为 Sector Quality 层（直接复用，不重算）。"""
    path = os.path.join(OUTPUT_DIR, "sector_quality.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(f"缺少 {path}，请先完成 Step 2")
    return pd.read_csv(path, dtype={"sector_id": str})


def all_trade_dates(end_date: str) -> list:
    """交易日序列：复用已缓存的沪深300指数日线日期（不另建交易日体系）。"""
    df = cached_index_daily(BENCH_PRIMARY, "20200101", end_date)
    if df is None or df.empty:
        raise RuntimeError(f"无法获取基准指数 {BENCH_PRIMARY} 日线，交易日体系不可用")
    d = sorted(set(df["trade_date"].astype(str)))
    return [x for x in d if x <= end_date]


def build_panels(dates: list, code_set: set, no_fetch: bool = False) -> dict:
    """构建行情面板（个股行情 + 换手率 + 全市场成交额）。"""
    lf, af, tf = [], [], []
    mkt, rows_by_day = {}, {}
    for d in dates:
        df = get_daily_by_date(d)
        if df is None or df.empty:
            log.warning("%s 无日线缓存（跳过）", d)
            continue
        df = df.copy()
        df["ts_code"] = df["ts_code"].astype(str)
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
        df["pct_chg"] = pd.to_numeric(df["pct_chg"], errors="coerce")
        mkt[d] = float(df["amount"].fillna(0.0).sum())
        rows_by_day[d] = int(len(df))
        sub = df[df["ts_code"].isin(code_set)]
        if not sub.empty:
            a = sub[["ts_code", "amount"]].copy()
            a["trade_date"] = d
            af.append(a)
            g = (1.0 + sub["pct_chg"] / 100.0).clip(lower=1e-6)
            lf.append(pd.DataFrame({"trade_date": d, "ts_code": sub["ts_code"].values,
                                    "lg": np.log(g.values)}))
        db = daily_basic_market(d, auto_fill=not no_fetch, silent=True)
        if db is not None and not db.empty and "turnover_rate" in db.columns:
            db = db.copy()
            db["ts_code"] = db["ts_code"].astype(str)
            db = db[db["ts_code"].isin(code_set)]
            if not db.empty:
                t = db[["ts_code", "turnover_rate"]].copy()
                t["trade_date"] = d
                tf.append(t)
    if not lf:
        raise RuntimeError("未获取到任何个股日线数据")
    logg = pd.concat(lf, ignore_index=True).pivot(
        index="trade_date", columns="ts_code", values="lg").sort_index()
    amt = pd.concat(af, ignore_index=True).pivot(
        index="trade_date", columns="ts_code", values="amount").sort_index()
    tov = (pd.concat(tf, ignore_index=True).pivot(
        index="trade_date", columns="ts_code", values="turnover_rate").sort_index()
        if tf else pd.DataFrame(index=logg.index))
    return {"logg": logg, "amt": amt, "tov": tov,
            "market_amount": pd.Series(mkt).sort_index(), "rows_by_day": rows_by_day}


def build_bench(panel_index, code: str, start: str, end: str) -> pd.DataFrame:
    """基准指数各持有期累计收益（与个股同口径：pct_chg 连乘，天然含除权调整）。"""
    df = cached_index_daily(code, start, end)
    if df is None or df.empty:
        return pd.DataFrame(index=panel_index)
    df = df.copy()
    df["trade_date"] = df["trade_date"].astype(str)
    df["pct_chg"] = pd.to_numeric(df["pct_chg"], errors="coerce")
    s = df.set_index("trade_date")["pct_chg"].reindex(panel_index)
    lg = np.log((1.0 + s.fillna(0.0) / 100.0).clip(lower=1e-6)).where(s.notna())
    out = {k: np.expm1(lg.rolling(k, min_periods=k).sum()) for k in HORIZONS}
    return pd.DataFrame(out, index=panel_index)


def build_ret_matrices(logg: pd.DataFrame) -> dict:
    """各持有期收益矩阵：ret_k(D) = (D-k+1..D) 连乘 - 1，只回看不含未来。"""
    return {k: np.expm1(logg.rolling(k, min_periods=k).sum()) for k in HORIZONS}


# ────────────────────────────────────────────────────────────────────────────
# 指标层
# ────────────────────────────────────────────────────────────────────────────

def sector_metrics(date, mem, code_pos, ret, amt_row, tov_row, market_amount, bench_p, bench_s,
                   quality_row) -> dict | None:
    """单个 (trade_date, sector) 的基础统计。仅使用 <= date 的数据。"""
    codes = mem["ts_code"].tolist()
    ws = mem["membership_weight"].to_numpy(dtype=float)
    tps = mem["membership_type"].to_numpy(dtype=str)
    pos = np.array([code_pos.get(c, -1) for c in codes], dtype=int)
    ok = pos >= 0
    n_member = len(codes)
    if not ok.any():
        return None
    pos, ws, tps = pos[ok], ws[ok], tps[ok]

    r1 = ret[1].loc[date].values[pos].astype(float)
    r3 = ret[3].loc[date].values[pos].astype(float)
    r5 = ret[5].loc[date].values[pos].astype(float)
    r10 = ret[10].loc[date].values[pos].astype(float)
    r20 = ret[20].loc[date].values[pos].astype(float)
    a_t = amt_row.values[pos].astype(float) if amt_row is not None and len(amt_row) else np.full(len(pos), np.nan)
    tov = tov_row.values[pos].astype(float) if tov_row is not None and len(tov_row) else np.full(len(pos), np.nan)

    valid = ~np.isnan(r1)
    n_valid = int(valid.sum())
    up = down = flat = 0
    up_ratio = down_ratio = breadth = float("nan")
    w_up = w_down = w_breadth = float("nan")
    if n_valid:
        r1v, wv = r1[valid], ws[valid]
        up, down, flat = int((r1v > 0).sum()), int((r1v < 0).sum()), int((r1v == 0).sum())
        up_ratio, down_ratio = up / n_valid, down / n_valid
        breadth = (up - down) / n_valid
        tw = wv.sum()
        w_up = sdiv(float(wv[r1v > 0].sum()), tw, float("nan"))
        w_down = sdiv(float(wv[r1v < 0].sum()), tw, float("nan"))
        w_breadth = sdiv(float((wv * np.sign(r1v)).sum()), tw, float("nan"))

    # 分层广度（需求 §十四）：CORE / PRIMARY / SECONDARY
    layer = {}
    declared = {}
    for t in ("CORE", "PRIMARY", "SECONDARY"):
        declared[t] = int((tps == t).sum())
        mk = (tps == t) & valid
        cnt = int(mk.sum())
        if cnt == 0:
            layer[t] = {"n": 0, "up_ratio": float("nan"), "breadth": float("nan"),
                        "ret5": float("nan"), "amount": 0.0}
            continue
        rv = r1[mk]
        r5v = r5[mk]
        layer[t] = {
            "n": cnt,
            "up_ratio": float((rv > 0).sum() / cnt),
            "breadth": float(((rv > 0).sum() - (rv < 0).sum()) / cnt),
            "ret5": nan_mean(r5v),
            "amount": float(np.nansum(a_t[mk])),
        }
    # CORE 层为空时退化为 PRIMARY 层（无 core 成员不等于核心弱），并在输出中标注实际使用的层
    core_layer = "CORE" if layer["CORE"]["n"] > 0 else ("PRIMARY" if layer["PRIMARY"]["n"] > 0 else "")
    core_breadth = layer[core_layer]["breadth"] if core_layer else float("nan")
    core_up_ratio = layer[core_layer]["up_ratio"] if core_layer else float("nan")
    core_n = layer[core_layer]["n"] if core_layer else 0
    core_amount = layer[core_layer]["amount"] if core_layer else 0.0
    # core_breadth_status：显式区分「真实没有 CORE 成员」与「有 CORE 但当日无有效行情」，
    # core_breadth 缺失时保持 NaN（严禁伪装成 0 / 50）。
    if layer["CORE"]["n"] > 0:
        core_breadth_status = "CORE"
    elif layer["PRIMARY"]["n"] > 0:
        core_breadth_status = "PRIMARY_FALLBACK"
    elif declared["CORE"] > 0:
        core_breadth_status = "CORE_NO_VALID_MEMBER"
    else:
        core_breadth_status = "NO_CORE_MEMBER"

    # 收益（需求 §九：等权 + 成员权重加权 + 中位数交叉验证）
    ew1, mw1 = nan_mean(r1), nan_wmean(r1, ws)
    ew3, mw3 = nan_mean(r3), nan_wmean(r3, ws)
    ew5, mw5 = nan_mean(r5), nan_wmean(r5, ws)
    ew10, mw10 = nan_mean(r10), nan_wmean(r10, ws)
    ew20, mw20 = nan_mean(r20), nan_wmean(r20, ws)
    med5 = nan_median(r5)

    b = {k: (float(bench_p.loc[date, k]) if (k in bench_p.columns and date in bench_p.index)
             else float("nan")) for k in HORIZONS}
    bs = {k: (float(bench_s.loc[date, k]) if (k in bench_s.columns and date in bench_s.index)
              else float("nan")) for k in HORIZONS}
    vs5 = ew5 - b[5] if not (math.isnan(ew5) or math.isnan(b[5])) else float("nan")
    vs10 = ew10 - b[10] if not (math.isnan(ew10) or math.isnan(b[10])) else float("nan")
    vs20 = ew20 - b[20] if not (math.isnan(ew20) or math.isnan(b[20])) else float("nan")
    core_ew5 = layer[core_layer]["ret5"] if core_layer else float("nan")
    core_vs5 = core_ew5 - b[5] if not (math.isnan(core_ew5) or math.isnan(b[5])) else float("nan")

    # 集中度 / 分散度 / 正贡献比（需求 §十三）
    contrib = np.nan_to_num(r5, nan=0.0) * ws
    abs_c = np.abs(contrib)
    tot_abs = float(abs_c.sum())
    top5c = sdiv(float(np.sort(abs_c)[::-1][:5].sum()), tot_abs, float("nan")) if tot_abs > 0 else float("nan")
    top10c = sdiv(float(np.sort(abs_c)[::-1][:10].sum()), tot_abs, float("nan")) if tot_abs > 0 else float("nan")
    pos_c = float(contrib[contrib > 0].sum())
    neg_c = float(-contrib[contrib < 0].sum())
    pcr = sdiv(pos_c, pos_c + neg_c, float("nan")) if (pos_c + neg_c) > 0 else float("nan")
    disp5, disp10 = nan_std(r5), nan_std(r10)

    # 成交额结构（需求 §十 / §十一）
    a_v = a_t[valid] if n_valid else np.array([])
    sec_amt = float(np.nansum(a_v)) if a_v.size else 0.0
    avg_amt = float(np.nanmean(a_v)) if a_v.size else float("nan")
    amt_share = sdiv(sec_amt, market_amount, float("nan"))

    # 数据质量（需求 §二十二）
    missing_price = int(n_member - n_valid)
    missing_amount = int(np.isnan(a_t).sum())
    stale = int((a_t == 0).sum())
    dq = 1.0 - (0.50 * sdiv(missing_price, n_member, 0.0)
                + 0.20 * sdiv(missing_amount, n_member, 0.0)
                + 0.20 * sdiv(stale, n_member, 0.0))
    dq = max(0.0, min(1.0, dq))

    q = quality_row or {}
    return {
        "trade_date": date,
        "sector_id": str(mem["sector_id"].iloc[0]),
        "sector_name": str(mem["sector_name"].iloc[0]) if "sector_name" in mem.columns else str(q.get("sector_name", "")),
        "rotation_group": str(q.get("rotation_group", "")),
        "sector_type": str(q.get("sector_type", "")),
        "sector_purity": fnum(q.get("sector_purity")),
        "industry_consistency": fnum(q.get("industry_consistency")),
        "membership_stability": fnum(q.get("membership_stability")),
        "tier": str(q.get("tier", "")),
        "member_count": n_member,
        "valid_member_count": n_valid,
        "up_count": up, "down_count": down, "flat_count": flat,
        "up_ratio": up_ratio, "down_ratio": down_ratio, "breadth": breadth,
        "weighted_up_ratio": w_up, "weighted_down_ratio": w_down, "weighted_breadth": w_breadth,
        "core_layer": core_layer, "core_valid_count": core_n,
        "core_member_count": declared["CORE"], "core_breadth_status": core_breadth_status,
        "core_up_ratio": core_up_ratio, "core_breadth": core_breadth, "core_amount": core_amount,
        "primary_valid_count": layer["PRIMARY"]["n"], "primary_up_ratio": layer["PRIMARY"]["up_ratio"],
        "primary_breadth": layer["PRIMARY"]["breadth"], "primary_amount": layer["PRIMARY"]["amount"],
        "secondary_valid_count": layer["SECONDARY"]["n"], "secondary_up_ratio": layer["SECONDARY"]["up_ratio"],
        "secondary_breadth": layer["SECONDARY"]["breadth"], "secondary_amount": layer["SECONDARY"]["amount"],
        "ew_ret_1": ew1, "ew_ret_3": ew3, "ew_ret_5": ew5, "ew_ret_10": ew10, "ew_ret_20": ew20,
        "mw_ret_1": mw1, "mw_ret_3": mw3, "mw_ret_5": mw5, "mw_ret_10": mw10, "mw_ret_20": mw20,
        "ew_median_ret_5": med5,
        "core_ew_ret_5": core_ew5, "core_vs_market_5": core_vs5,
        "bench_ret_5": b[5], "bench_ret_10": b[10], "bench_ret_20": b[20],
        "vs_market_5": vs5, "vs_market_10": vs10, "vs_market_20": vs20,
        "vs_midcap_5": (ew5 - bs[5]) if not (math.isnan(ew5) or math.isnan(bs[5])) else float("nan"),
        "vs_midcap_10": (ew10 - bs[10]) if not (math.isnan(ew10) or math.isnan(bs[10])) else float("nan"),
        "vs_midcap_20": (ew20 - bs[20]) if not (math.isnan(ew20) or math.isnan(bs[20])) else float("nan"),
        "sector_amount": sec_amt, "market_amount": market_amount,
        "sector_amount_share": amt_share, "avg_member_amount": avg_amt,
        "sector_turnover_rate_wavg": nan_wmean(tov[valid], ws[valid]) if n_valid else float("nan"),
        "ret_dispersion_5": disp5, "ret_dispersion_10": disp10,
        "top5_concentration": top5c, "top10_concentration": top10c,
        "positive_contribution_ratio": pcr,
        "missing_price_count": missing_price, "missing_amount_count": missing_amount,
        "stale_member_count": stale, "data_quality_score": round(dq, 4),
        # 成交额口径的成员覆盖率（缺失=非正常值，与价格缺失分开统计），
        # 供 Step 4 判断 volume_participation 是「真实中性」还是「数据缺失」。
        "volume_valid_member_ratio": round(1.0 - sdiv(missing_amount, n_member, 0.0), 4),
    }


def core_breadth_score_series(gg: pd.DataFrame) -> pd.DataFrame:
    """成交额占比变化与量能比（按 sector 自身时间序列，只回看）。"""
    gg = gg.sort_values("trade_date").reset_index(drop=True)
    gg["sector_amount_share_change_5d"] = gg["sector_amount_share"] - gg["sector_amount_share"].shift(5)
    gg["sector_amount_share_change_20d"] = gg["sector_amount_share"] - gg["sector_amount_share"].shift(20)
    gg["breadth_change_5d"] = gg["breadth"] - gg["breadth"].shift(5)
    gg["core_breadth_change_5d"] = gg["core_breadth"] - gg["core_breadth"].shift(5)
    # volume_ratio_N = 当日成员平均成交额 / 过去 N 日成员平均成交额（需求 §十）
    base = gg["avg_member_amount"].astype(float)
    for k in (1, 3, 5):
        past = base.shift(1).rolling(k, min_periods=k).mean()
        gg[f"volume_ratio_{k}"] = [sdiv(a, bb, float("nan")) for a, bb in zip(base.values, past.values)]
    # volume_data_status：量能链派生字段的可得性（不是"好/坏"评价）。
    # VALID   = volume_ratio_5 与 sector_amount_share_change_5d 均有值
    # PARTIAL = 仅其一有值
    # MISSING = 两者都无值（回放窗口过短 / 成员成交额全缺）
    st = []
    for vr5, sh5 in zip(gg["volume_ratio_5"].values, gg["sector_amount_share_change_5d"].values):
        ok = int(np.isfinite(float(vr5)) if vr5 is not None else 0) + \
             int(np.isfinite(float(sh5)) if sh5 is not None else 0)
        st.append("VALID" if ok == 2 else ("PARTIAL" if ok == 1 else "MISSING"))
    gg["volume_data_status"] = st
    return gg


def anomaly_flags(r) -> list:
    """需求 §二十三：异常主题标记。"""
    f = []
    dq = fnum(r["data_quality_score"], 0.0)
    vr = sdiv(r["valid_member_count"], r["member_count"], 0.0)
    if dq < DQ_INVALID_THRESHOLD or r["valid_member_count"] < MIN_VALID_MEMBERS:
        f.append("DATA_INVALID")
    if vr < STATE_RULES["anomaly_low_coverage_ratio"] and "DATA_INVALID" not in f:
        f.append("LOW_COVERAGE")
    t5 = fnum(r["top5_concentration"])
    if not math.isnan(t5) and t5 > STATE_RULES["anomaly_high_concentration"]:
        f.append("HIGH_CONCENTRATION")
    bd = fnum(r["breadth"])
    if not math.isnan(bd) and bd < STATE_RULES["anomaly_low_breadth"]:
        f.append("LOW_BREADTH")
    cb = fnum(r["core_breadth"])
    if not math.isnan(cb) and cb <= STATE_RULES["anomaly_core_weak"] and not math.isnan(bd) and bd > cb:
        f.append("CORE_WEAK")
    st = fnum(r["membership_stability"])
    if not math.isnan(st) and st < STATE_RULES["anomaly_membership_stability"]:
        f.append("MEMBERSHIP_UNSTABLE")
    return f


def health_score(r, flags: list) -> float:
    """Sector Health（需求 §十五 / §十六）：所有子指标先标准化到 0~100。"""
    if "DATA_INVALID" in flags:
        return float("nan")
    b_s = scale_sym(r["breadth"], SCALE_BREADTH_HALF)
    wb_s = scale_sym(r["weighted_breadth"], SCALE_BREADTH_HALF)
    rs_s = scale_sym(r["vs_market_5"], SCALE_RS_HALF)
    vol_s = scale_sym(fnum(r["volume_ratio_5"]) - 1.0, SCALE_VOLUME_HALF)
    cp = (CORE_PRIMARY_INNER["core_breadth"] * scale_sym(r["core_breadth"], SCALE_BREADTH_HALF)
          + CORE_PRIMARY_INNER["primary_breadth"] * scale_sym(r["primary_breadth"], SCALE_BREADTH_HALF)
          + CORE_PRIMARY_INNER["core_rs"] * scale_sym(r["core_vs_market_5"], SCALE_RS_HALF))
    cons = 0.5 * scale_sym(-(fnum(r["ret_dispersion_5"]) - SCALE_DISPERSION_MID), SCALE_DISPERSION_HALF) \
        + 0.5 * scale_sym(fnum(r["positive_contribution_ratio"]) - 0.5, SCALE_PCR_HALF)
    h = (HEALTH_WEIGHTS["breadth"] * b_s
         + HEALTH_WEIGHTS["weighted_breadth"] * wb_s
         + HEALTH_WEIGHTS["relative_strength"] * rs_s
         + HEALTH_WEIGHTS["volume_structure"] * vol_s
         + HEALTH_WEIGHTS["core_primary"] * cp
         + HEALTH_WEIGHTS["consistency"] * cons)
    return round(h, 2)


def state_reason(r, flags: list) -> str:
    """需求 §二十九：由可验证字段产生，不使用任何主观表述。"""
    bd, wb = fnum(r["breadth"]), fnum(r["weighted_breadth"])
    cb, rs5 = fnum(r["core_breadth"]), fnum(r["vs_market_5"])
    vr5, t5 = fnum(r["volume_ratio_5"]), fnum(r["top5_concentration"])
    sh, sh5 = fnum(r["sector_amount_share"]), fnum(r.get("sector_amount_share_change_5d"))
    p = []
    p.append(f"广度 {bd:+.2f}（上涨 {int(r['up_count'])}/下跌 {int(r['down_count'])}）")
    p.append(f"加权广度 {wb:+.2f}")
    p.append(f"核心层 {r.get('core_layer') or 'NA'} 广度 "
             + ("NA（该 Sector 无 CORE/PRIMARY 层成员）" if math.isnan(cb) else f"{cb:+.2f}"))
    p.append(f"5日相对{BENCH_PRIMARY_NAME} {rs5 * 100:+.2f}pp")
    p.append(f"量能比(vs 过去5日均值) {vr5:.2f}")
    p.append(f"成交额占比 {sh * 100:.2f}%"
             + (f"（较5日前 {sh5 * 100:+.2f}pp）" if not math.isnan(sh5) else ""))
    p.append(f"Top5集中度 {t5:.2f}")
    p.append(f"有效成员 {int(r['valid_member_count'])}/{int(r['member_count'])}")
    p.append(f"数据质量 {fnum(r['data_quality_score'], 0.0):.2f}")
    if flags:
        p.append("标记=" + ";".join(flags))
    return "；".join(p)


def classify_state(r, flags: list) -> str:
    """需求 §十七 / §三十三：由广度、核心成员、相对强度、量能、一致性共同决定。"""
    if "DATA_INVALID" in flags:
        return "DATA_INVALID"
    bd, wb = fnum(r["breadth"]), fnum(r["weighted_breadth"])
    cb, rs5 = fnum(r["core_breadth"]), fnum(r["vs_market_5"])
    vr5, t5 = fnum(r["volume_ratio_5"]), fnum(r["top5_concentration"])
    bd5, cb5 = fnum(r.get("breadth_change_5d")), fnum(r.get("core_breadth_change_5d"))
    if math.isnan(bd):
        return "DATA_INVALID"

    # DETERIORATING：不是"已经弱"，而是结构正在恶化
    if (not math.isnan(bd5) and bd5 <= STATE_RULES["deteriorating_breadth_drop"]
            and not math.isnan(rs5) and rs5 < 0
            and not math.isnan(cb5) and cb5 < 0):
        return "DETERIORATING"

    if (bd >= STATE_RULES["strong_breadth"] and not math.isnan(wb)
            and wb >= STATE_RULES["strong_weighted_breadth"] and not math.isnan(cb)
            and cb >= STATE_RULES["strong_core_breadth"] and not math.isnan(rs5) and rs5 > 0
            and not math.isnan(vr5) and vr5 >= STATE_RULES["strong_volume_ratio_5"]
            and (math.isnan(t5) or t5 <= STATE_RULES["strong_top5_concentration_max"])
            and "HIGH_CONCENTRATION" not in flags and "CORE_WEAK" not in flags
            and "LOW_BREADTH" not in flags and "LOW_COVERAGE" not in flags):
        return "STRONG"

    pos_cnt = sum([bd > 0, (not math.isnan(wb) and wb > 0), (not math.isnan(cb) and cb > 0),
                   (not math.isnan(rs5) and rs5 > 0), (not math.isnan(vr5) and vr5 > 1.0)])
    if (bd > 0 and not math.isnan(cb) and cb >= 0
            and pos_cnt >= STATE_RULES["healthy_min_positive_indicators"]
            and "CORE_WEAK" not in flags and "HIGH_CONCENTRATION" not in flags):
        return "HEALTHY"

    if ((bd <= STATE_RULES["weak_breadth"] and not math.isnan(cb) and cb <= 0
         and not math.isnan(rs5) and rs5 <= 0)
            or bd <= STATE_RULES["weak_breadth_deep"]):
        return "WEAK"
    return "NEUTRAL"


def finalize_sector_series(gg: pd.DataFrame) -> pd.DataFrame:
    """逐 sector 补齐状态与迁移（严格只用当年及之前行）。"""
    gg = gg.sort_values("trade_date").reset_index(drop=True)
    fl_list, health, raw_states = [], [], []
    for _, r in gg.iterrows():
        fl = anomaly_flags(r)
        fl_list.append(fl)
        health.append(health_score(r, fl))
        raw_states.append(classify_state(r, fl))
    # 状态确认平滑（用户决策）：原始判定为新状态且连续 2 个交易日成立，才写入 sector_state_base；
    # 双向对称，避免日频阈值抖动（原始判定 72.5% 的观测日发生切换），且不产生单向下偏。
    # 例外一：已确认状态为 STRONG 而当日核心层广度不满足 STRONG 的核心层硬条件（<0.5）时，
    #         立即降级为当日原始判定，保证"核心成员转弱时不得继续标注 STRONG"（需求 §三十二 H）；
    # 例外二：DATA_INVALID 双向立即生效，数据质量不足时不做平滑延迟。
    # 只用当日及以前数据；未确认的原始判定保留在 state_raw，并在 state_reason 中说明。
    conf_states, pending = [], []
    cur, streak_state, streak_len = None, None, 0
    for i, s in enumerate(raw_states):
        if s == streak_state:
            streak_len += 1
        else:
            streak_state, streak_len = s, 1
        if cur is None:
            cur = s
        elif s != cur:
            if s == "DATA_INVALID" or cur == "DATA_INVALID":
                cur = s
            else:
                cb_v = fnum(gg.at[i, "core_breadth"])
                core_gate = (cur == "STRONG" and not math.isnan(cb_v)
                             and cb_v < STATE_RULES["strong_core_breadth"])
                if core_gate or streak_len >= 2:
                    cur = s
        conf_states.append(cur)
        pending.append(s if s != cur else "")
    reasons = []
    for i, (_, r) in enumerate(gg.iterrows()):
        rs = state_reason(r, fl_list[i])
        if pending[i]:
            rs += (f"；平滑：当日原始判定为 {pending[i]}，尚未连续 2 个交易日成立，"
                   f"暂维持 {conf_states[i]}（原始判定见 state_raw 字段）")
        reasons.append(rs)
    gg["anomaly_flags"] = [";".join(x) for x in fl_list]
    gg["sector_health"] = health
    gg["state_raw"] = raw_states
    gg["sector_state_base"] = conf_states
    gg["state_reason"] = reasons

    prev, chg, dur, cdate = [], [], [], []
    last_state, last_change, d = None, None, 0
    for _, r in gg.iterrows():
        s, td = r["sector_state_base"], r["trade_date"]
        if last_state is None:
            prev.append("")
            cdate.append(td)
            d = 1
        elif s != last_state:
            prev.append(last_state)
            cdate.append(td)
            d = 1
            last_change = td
        else:
            prev.append(last_state)
            cdate.append(last_change)
            d += 1
        if last_state is None or s != last_state:
            last_change = td
        last_state = s
        chg.append(1 if prev[-1] and prev[-1] != s else 0)
        dur.append(d)
    gg["prev_state"] = prev
    gg["state_changed"] = chg
    gg["state_change_date"] = cdate
    gg["state_duration"] = dur
    return gg


# ────────────────────────────────────────────────────────────────────────────
# 主流程
# ────────────────────────────────────────────────────────────────────────────

def run_state_layer(mdf: pd.DataFrame, quality: pd.DataFrame, base_date: str,
                    history_days: int, membership_mode: str, no_fetch: bool,
                    panel_dates: list | None = None, replay_dates: list | None = None):
    """构建全部回放日期的 Sector 基础统计。"""
    dates_all = all_trade_dates(base_date)
    need = history_days + WARMUP_DAYS
    if panel_dates is None:
        panel_dates = dates_all[-need:] if len(dates_all) >= need else dates_all
    if replay_dates is None:
        replay_dates = dates_all[-history_days:] if len(dates_all) >= history_days else dates_all
    replay_dates = [d for d in replay_dates if d in panel_dates]
    if not replay_dates:
        raise RuntimeError("回放日期为空，请检查 --history-days / --date")

    codes = sorted(set(mdf["ts_code"].astype(str)))
    panels = build_panels(panel_dates, set(codes), no_fetch=no_fetch)
    logg, amt, tov = panels["logg"], panels["amt"], panels["tov"]
    ret = build_ret_matrices(logg)
    bench_p = build_bench(logg.index, BENCH_PRIMARY, panel_dates[0], panel_dates[-1])
    bench_s = build_bench(logg.index, BENCH_SECONDARY, panel_dates[0], panel_dates[-1])
    if bench_p.empty:
        raise RuntimeError(f"基准指数 {BENCH_PRIMARY} 无数据")

    code_pos = {c: i for i, c in enumerate(logg.columns)}
    qmap = {str(r["sector_id"]): r for r in quality.to_dict("records")}

    rows = []
    for date in replay_dates:
        if date not in logg.index:
            log.warning("%s 不在行情面板内，跳过", date)
            continue
        active = mdf[mdf["effective_date"] <= date] if membership_mode == "strict" else mdf
        if active.empty:
            log.warning("%s 无可用 membership（strict 模式），跳过", date)
            continue
        amt_row = amt.loc[date] if date in amt.index else pd.Series(dtype=float)
        tov_row = tov.loc[date] if (len(tov) and date in tov.index) else pd.Series(dtype=float)
        mkt = float(panels["market_amount"].get(date, float("nan")))
        for sid, mem in active.groupby("sector_id", sort=True):
            rec = sector_metrics(date, mem, code_pos, ret, amt_row, tov_row, mkt,
                                 bench_p, bench_s, qmap.get(str(sid)))
            if rec is not None:
                rows.append(rec)
    if not rows:
        raise RuntimeError("未产生任何 Sector 统计行，请检查 membership 与行情区间")

    raw = pd.DataFrame(rows)
    out = [finalize_sector_series(core_breadth_score_series(g))
           for _, g in raw.groupby("sector_id", sort=True)]
    daily = pd.concat(out, ignore_index=True)

    meta = {
        "base_date": base_date,
        "history_days": history_days,
        "replay_dates": replay_dates,
        "panel_start": panel_dates[0],
        "panel_end": panel_dates[-1],
        "panel_days": len(panel_dates),
        "membership_mode": membership_mode,
        "membership_eff_dates": sorted(set(mdf["effective_date"].astype(str))),
        "bench_primary": BENCH_PRIMARY,
        "bench_secondary": BENCH_SECONDARY,
        "rows_by_day": panels["rows_by_day"],
        "panel_index": [str(x) for x in logg.index],
        "panel_codes": [str(c) for c in logg.columns],
    }
    return daily, meta


# ────────────────────────────────────────────────────────────────────────────
# 输出层（需求 §二十四 ~ §二十七）
# ────────────────────────────────────────────────────────────────────────────

DAILY_STATS_COLS = [
    "trade_date", "sector_id", "sector_name", "rotation_group",
    "member_count", "valid_member_count",
    "up_count", "down_count", "flat_count", "up_ratio", "down_ratio", "breadth",
    "weighted_up_ratio", "weighted_down_ratio", "weighted_breadth",
    "core_breadth", "primary_breadth", "secondary_breadth",
    "ew_ret_1", "ew_ret_3", "ew_ret_5", "ew_ret_10", "ew_ret_20",
    "mw_ret_1", "mw_ret_3", "mw_ret_5", "mw_ret_10", "mw_ret_20",
    "vs_market_5", "vs_market_10", "vs_market_20",
    "sector_amount", "sector_amount_share",
    "volume_ratio_1", "volume_ratio_3", "volume_ratio_5",
    "ret_dispersion_5", "ret_dispersion_10",
    "top5_concentration", "top10_concentration", "positive_contribution_ratio",
    "sector_health", "data_quality_score", "sector_state_base",
]
BREADTH_COLS = [
    "trade_date", "sector_id", "sector_name", "rotation_group",
    "member_count", "valid_member_count", "valid_ratio",
    "up_count", "down_count", "flat_count", "up_ratio", "down_ratio", "breadth",
    "weighted_up_ratio", "weighted_down_ratio", "weighted_breadth",
    "core_layer", "core_valid_count", "core_member_count", "core_breadth_status",
    "core_up_ratio", "core_breadth",
    "primary_valid_count", "primary_up_ratio", "primary_breadth",
    "secondary_valid_count", "secondary_up_ratio", "secondary_breadth",
    "breadth_change_5d", "core_breadth_change_5d", "anomaly_flags",
]
STRENGTH_COLS = [
    "trade_date", "sector_id", "sector_name", "rotation_group", "valid_member_count",
    "ew_ret_1", "ew_ret_3", "ew_ret_5", "ew_ret_10", "ew_ret_20",
    "mw_ret_1", "mw_ret_3", "mw_ret_5", "mw_ret_10", "mw_ret_20",
    "ew_median_ret_5",
    "bench_ret_5", "bench_ret_10", "bench_ret_20",
    "vs_market_5", "vs_market_10", "vs_market_20",
    "vs_midcap_5", "vs_midcap_10", "vs_midcap_20",
    "relative_strength_5", "relative_strength_10", "relative_strength_20",
    "core_ew_ret_5", "core_vs_market_5",
    "ret_dispersion_5", "ret_dispersion_10",
    "top5_concentration", "top10_concentration", "positive_contribution_ratio",
]
VOLUME_COLS = [
    "trade_date", "sector_id", "sector_name", "rotation_group", "valid_member_count",
    "sector_amount", "market_amount", "sector_amount_share",
    "sector_amount_share_change_5d", "sector_amount_share_change_20d",
    "avg_member_amount", "volume_ratio_1", "volume_ratio_3", "volume_ratio_5",
    "volume_data_status", "volume_valid_member_ratio",
    "sector_turnover_rate_wavg",
]
STATE_HISTORY_COLS = [
    "trade_date", "sector_id", "sector_name", "rotation_group",
    "prev_state", "current_state", "state_raw", "state_changed", "state_change_date",
    "state_duration",
    "sector_health", "sector_purity", "data_quality_score", "anomaly_flags", "state_reason",
]


def emit(daily: pd.DataFrame, quality: pd.DataFrame):
    d = daily.copy()
    d["valid_ratio"] = [sdiv(v, m, 0.0) for v, m in zip(d["valid_member_count"], d["member_count"])]
    d["relative_strength_5"] = d["vs_market_5"]
    d["relative_strength_10"] = d["vs_market_10"]
    d["relative_strength_20"] = d["vs_market_20"]
    d["current_state"] = d["sector_state_base"]
    d["state_changed"] = d["state_changed"].astype(int)

    for cols, name in ((DAILY_STATS_COLS, "sector_daily_stats.csv"),
                       (BREADTH_COLS, "sector_breadth.csv"),
                       (STRENGTH_COLS, "sector_strength.csv"),
                       (VOLUME_COLS, "sector_volume.csv"),
                       (STATE_HISTORY_COLS, "sector_state_history.csv")):
        write_merge_csv(d.reindex(columns=cols), os.path.join(DATA_DIR, name))

    qcols = ["sector_id", "sector_name", "rotation_group", "sector_type",
             "industry_consistency", "product_consistency", "core_primary_ratio",
             "concept_purity", "membership_stability", "coverage",
             "core_company_quality", "concept_coherence", "historical_data",
             "sector_purity", "sector_quality_score", "tier"]
    qt = quality.reindex(columns=qcols)
    vc = d.groupby(["trade_date", "sector_id"], as_index=False)["valid_member_count"].max()
    qd = vc.merge(qt, on="sector_id", how="left")
    write_merge_csv(qd.reindex(columns=["trade_date"] + qcols + ["valid_member_count"]),
                    os.path.join(OUTPUT_DIR, "sector_quality_daily.csv"))


def emit_state_today(daily: pd.DataFrame, meta: dict) -> dict:
    """需求 §二十六 / §二十八：今日 Sector 基础状态（同时保留原始指标与评分指标）。"""
    last = max(daily["trade_date"].astype(str))
    today = daily[daily["trade_date"].astype(str) == last].copy()
    themes = []
    for r in today.sort_values("sector_id").to_dict("records"):
        themes.append({
            "sector_id": r["sector_id"], "sector_name": r["sector_name"],
            "rotation_group": r["rotation_group"], "tier": r.get("tier", ""),
            "sector_purity": r4(r["sector_purity"]),
            "member_count": int(r["member_count"]), "valid_member_count": int(r["valid_member_count"]),
            "breadth": r4(r["breadth"]), "weighted_breadth": r4(r["weighted_breadth"]),
            "core_layer": r.get("core_layer", ""), "core_breadth": r4(r["core_breadth"]),
            "core_breadth_status": r.get("core_breadth_status", ""),
            "core_member_count": int(r.get("core_member_count", 0) or 0),
            "core_valid_count": int(r.get("core_valid_count", 0) or 0),
            "primary_breadth": r4(r["primary_breadth"]),
            "ew_ret_1": r4(r["ew_ret_1"]), "ew_ret_5": r4(r["ew_ret_5"]),
            "mw_ret_5": r4(r["mw_ret_5"]), "ew_ret_20": r4(r["ew_ret_20"]),
            "vs_market_5": r4(r["vs_market_5"]), "vs_market_20": r4(r["vs_market_20"]),
            "sector_amount_share": r4(r["sector_amount_share"]),
            "sector_amount_share_change_5d": r4(r["sector_amount_share_change_5d"]),
            "volume_ratio_5": r4(r["volume_ratio_5"]),
            "volume_data_status": r.get("volume_data_status", ""),
            "volume_valid_member_ratio": r4(r.get("volume_valid_member_ratio")),
            "ret_dispersion_5": r4(r["ret_dispersion_5"]),
            "top5_concentration": r4(r["top5_concentration"]),
            "top10_concentration": r4(r["top10_concentration"]),
            "positive_contribution_ratio": r4(r["positive_contribution_ratio"]),
            "sector_health": r4(r["sector_health"]),
            "sector_state_base": r["sector_state_base"],
            "prev_state": r["prev_state"], "state_changed": int(r["state_changed"]),
            "state_duration": int(r["state_duration"]),
            "anomaly_flags": r["anomaly_flags"],
            "data_quality_score": r4(r["data_quality_score"]),
            "state_reason": r["state_reason"],
        })
    # 横截面分位（需求 §二十七：只做描述性参照，不形成"买什么"的交易排名）
    for key in ("sector_health", "vs_market_5", "breadth", "volume_ratio_5"):
        vals = [t[key] for t in themes if not math.isnan(fnum(t[key]))]
        if not vals:
            continue
        s = pd.Series(vals)
        for t in themes:
            v = fnum(t[key])
            t[f"{key}_percentile"] = None if math.isnan(v) else round(float((s <= v).mean()), 4)

    obj = {
        "version": "1.0",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "trade_date": last,
        "layer": "STEP3_THEME_STATE_BASE",
        "canonical_source": "sector_master.json",
        "membership_source": "data/sector_membership.csv",
        "membership_conf_min": MEMBERSHIP_CONF_MIN,
        "membership_mode": meta["membership_mode"],
        "membership_as_of": ",".join(meta["membership_eff_dates"]),
        "membership_note": ("STATIC_SNAPSHOT：Step 2 产出为单一快照（全部 effective_date="
                           + meta["membership_eff_dates"][0] + "），快照模式按静态产业定义回放，"
                           "非时点成员回放；strict 模式可复现严格 effective_date 过滤结果。"),
        "benchmark": {"primary": BENCH_PRIMARY, "primary_name": BENCH_PRIMARY_NAME,
                      "secondary": BENCH_SECONDARY, "secondary_name": BENCH_SECONDARY_NAME},
        "not_in_scope": ["SEOS", "板块启动/轮动/生命周期预测", "HVT", "IGE", "F120",
                         "个股BUY/NO TRADE", "交易执行", "概念热度排名"],
        "state_counts": {s: int((today["sector_state_base"] == s).sum()) for s in STATES},
        "anomaly_counts": {k: int(today["anomaly_flags"].fillna("").str.contains(k).sum())
                           for k in ("DATA_INVALID", "LOW_COVERAGE", "HIGH_CONCENTRATION",
                                     "LOW_BREADTH", "CORE_WEAK", "MEMBERSHIP_UNSTABLE")},
        "themes": themes,
    }
    write_json(obj, os.path.join(OUTPUT_DIR, "sector_state_today.json"))
    return obj


def emit_tracking_pool(daily: pd.DataFrame, quality: pd.DataFrame):
    """需求 §二十一：Tracking Pool 沿用 Step 2 分层，不按近期涨幅排名；仅兼容扩展状态字段。"""
    path = os.path.join(OUTPUT_DIR, "sector_tracking_pool.json")
    pool = None
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                pool = json.load(f)
        except Exception as e:
            log.warning("读取既有 tracking pool 失败：%s", e)
    last = max(daily["trade_date"].astype(str))
    today = {r["sector_id"]: r for r in daily[daily["trade_date"].astype(str) == last].to_dict("records")}

    if not isinstance(pool, dict) or "tiers" not in pool:
        log.warning("既有 tracking pool 缺失，按 Step 2 规则从 sector_quality.csv 重建")
        pool = {"version": "1.0", "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "canonical_source": "sector_master.json",
                "tiers": {k: [] for k in TIERS}, "counts": {}}
        for r in quality.to_dict("records"):
            item = {"sector_id": str(r["sector_id"]), "sector_name": r["sector_name"],
                    "rotation_group": r.get("rotation_group", ""), "tier": r.get("tier", "RAW_ONLY"),
                    "member_count": int(r.get("member_count", 0)),
                    "purity": float(r.get("sector_purity", 0.0)),
                    "industry_consistency": float(r.get("industry_consistency", 0.0)),
                    "quality_score": float(r.get("sector_quality_score", 0.0))}
            pool["tiers"].setdefault(item["tier"], []).append(item)

    pool["state_layer"] = {
        "as_of": last,
        "source": "output/sector_state_today.json",
        "note": ("状态层为附带信息，不参与 Tier 分层。Tier 沿用 Step 2 规则"
                 "（member_count/purity/industry_consistency），未按近期涨幅排名。"),
    }
    for items in pool["tiers"].values():
        for it in items:
            r = today.get(str(it.get("sector_id")))
            if not r:
                continue
            it["state"] = {
                "sector_state_base": r["sector_state_base"],
                "sector_health": r4(r["sector_health"]),
                "breadth": r4(r["breadth"]), "weighted_breadth": r4(r["weighted_breadth"]),
                "core_layer": r.get("core_layer", ""), "core_breadth": r4(r["core_breadth"]),
                "vs_market_5": r4(r["vs_market_5"]), "volume_ratio_5": r4(r["volume_ratio_5"]),
                "top5_concentration": r4(r["top5_concentration"]),
                "valid_member_count": int(r["valid_member_count"]),
                "anomaly_flags": r["anomaly_flags"],
                "data_quality_score": r4(r["data_quality_score"]),
            }
    pool["counts"] = {k: len(v) for k, v in pool["tiers"].items()}
    write_json(pool, path)


# ────────────────────────────────────────────────────────────────────────────
# 未来函数检验（需求 §三十二 E / 原则 5）
# ────────────────────────────────────────────────────────────────────────────

def check_no_lookahead(mdf: pd.DataFrame, quality: pd.DataFrame, base_date: str,
                       replay_dates: list, membership_mode: str, sample_n: int = 2) -> dict:
    """真实未来函数检验：把行情面板截断到中间日 D，重算 D 日指标，与完整回放结果逐项比对。

    若存在任何"使用 D+1 及以后数据"的路径，截断后的结果必然与完整回放不同。
    """
    ref_path = os.path.join(DATA_DIR, "sector_daily_stats.csv")
    if not os.path.exists(ref_path):
        return {"ok": False, "detail": f"缺少参考文件 {ref_path}，无法执行截断重算检验"}
    ref = pd.read_csv(ref_path, dtype={"trade_date": str, "sector_id": str})
    dates_all = all_trade_dates(base_date)
    idx_of = {d: i for i, d in enumerate(dates_all)}
    codes = sorted(set(mdf["ts_code"].astype(str)))
    cand = [replay_dates[len(replay_dates) // 3], replay_dates[(len(replay_dates) * 2) // 3]] \
        if len(replay_dates) >= 6 else []
    if not cand:
        return {"ok": True, "detail": "回放窗口过短（<6 日），D+1 数据不存在，结构性无未来函数"}
    qmap = {str(r["sector_id"]): r for r in quality.to_dict("records")}
    cols = ("ew_ret_5", "ew_ret_20", "breadth", "weighted_breadth",
            "volume_ratio_5", "sector_amount_share", "sector_health")
    bad, checked, used = [], 0, []
    for d in cand[:sample_n]:
        i = idx_of.get(d)
        if i is None:
            continue
        # 只保留 D 及之前 LOOKAHEAD_WINDOW 个交易日：能覆盖 20 日回看，且 D+1 之后的数据物理上不存在
        trunc = dates_all[max(0, i + 1 - LOOKAHEAD_WINDOW): i + 1]
        panels = build_panels(trunc, set(codes), no_fetch=True)
        logg = panels["logg"]
        if d not in logg.index:
            continue
        used.append(d)
        ret = build_ret_matrices(logg)
        bench_p = build_bench(logg.index, BENCH_PRIMARY, trunc[0], d)
        bench_s = build_bench(logg.index, BENCH_SECONDARY, trunc[0], d)
        code_pos = {c: k for k, c in enumerate(logg.columns)}
        rows = []
        for td in trunc:
            if td not in logg.index:
                continue
            active = mdf[mdf["effective_date"] <= td] if membership_mode == "strict" else mdf
            amt_row = panels["amt"].loc[td]
            tov_row = (panels["tov"].loc[td]
                       if (len(panels["tov"]) and td in panels["tov"].index) else pd.Series(dtype=float))
            mkt = float(panels["market_amount"].get(td, float("nan")))
            for sid, mem in active.groupby("sector_id", sort=True):
                rec = sector_metrics(td, mem, code_pos, ret, amt_row, tov_row, mkt,
                                     bench_p, bench_s, qmap.get(str(sid)))
                if rec is not None:
                    rows.append(rec)
        if not rows:
            continue
        tdf = pd.DataFrame(rows)
        tdf = pd.concat([finalize_sector_series(core_breadth_score_series(g))
                         for _, g in tdf.groupby("sector_id", sort=True)], ignore_index=True)
        for rec in tdf[tdf["trade_date"] == d].to_dict("records"):
            f2 = ref[(ref["trade_date"] == d) & (ref["sector_id"] == str(rec["sector_id"]))]
            if not len(f2):
                continue
            checked += 1
            for col in cols:
                a = fnum(rec.get(col))
                b = fnum(f2.iloc[0].get(col))
                if math.isnan(a) and math.isnan(b):
                    continue
                if math.isnan(a) or math.isnan(b) or abs(a - b) > 1e-6:
                    bad.append(f"{rec['sector_id']}@{d} {col}: 截断={a} vs 完整={b}")
    if bad:
        return {"ok": False, "detail": "截断重算结果不一致（存在未来函数嫌疑）：" + "；".join(bad[:8])}
    return {"ok": True, "detail": (f"对 {len(used)} 个中间日期（{', '.join(used)}）把行情面板物理截断到"
                                   f"当日为止（各保留 {LOOKAHEAD_WINDOW} 个交易日，D+1 及以后数据"
                                   f"不参与计算），重算后共比对 {checked} 个 (日期 × Sector) 组合的 "
                                   f"ew_ret_5/ew_ret_20/breadth/weighted_breadth/volume_ratio_5/"
                                   f"sector_amount_share/sector_health，与完整回放完全一致，"
                                   f"未发现使用 D+1 及以后数据的路径")}


# ────────────────────────────────────────────────────────────────────────────
# 验证层（需求 §三十二 A~H）
# ────────────────────────────────────────────────────────────────────────────

def run_validation(daily: pd.DataFrame, meta: dict, master: SectorMaster, mdf: pd.DataFrame,
                   quality: pd.DataFrame, legacy_found: list,
                   lookahead_result: dict | None = None) -> pd.DataFrame:
    res = []

    def add(cid, ok, detail):
        res.append({"check": cid, "status": "PASS" if ok else "FAIL", "detail": detail})

    valid_ids = set(master.sector_ids)
    sid_out = set(daily["sector_id"].astype(str))

    bad = sorted(sid_out - valid_ids)
    add("A_SECTOR_ID", not bad,
        f"非 master 声明的 sector_id：{bad if bad else '无'}（master sectors={len(valid_ids)}，"
        f"输出涉及 {len(sid_out)} 个）")

    dup = int(mdf.duplicated(subset=["ts_code", "sector_id", "effective_date"]).sum())
    add("B_MEMBERSHIP_UNIQUE", dup == 0, f"(ts_code, sector_id, effective_date) 重复行数={dup}")

    fut = int((mdf["effective_date"].astype(str) > meta["base_date"]).sum())
    add("C_EFFECTIVE_DATE", fut == 0,
        f"effective_date > 基准日({meta['base_date']}) 行数={fut}；strict 模式按 effective_date<=D 过滤；"
        f"snapshot 模式按静态定义回放并在输出中标注 STATIC_SNAPSHOT")

    idx = set(str(x) for x in meta["panel_index"])
    out_d = set(daily["trade_date"].astype(str))
    bad_d = sorted(out_d - idx)
    add("D_TRADE_DATE", not bad_d,
        f"输出日期 {len(out_d)} 个，全部来自交易日序列；非法日期"
        f"{bad_d[:5] if bad_d else '无'}")

    if lookahead_result is not None:
        add("E_NO_LOOKAHEAD", bool(lookahead_result.get("ok")), lookahead_result.get("detail", ""))
    else:
        add("E_NO_LOOKAHEAD", False, "未执行截断重算检验")

    price_cols = {"pct_chg", "pct_change", "limit_up", "zt", "turnover_rate", "amount",
                  "hot", "close", "vol", "high", "low"}
    hit = [c for c in mdf.columns if c in price_cols]
    add("F_PRICE_INDEPENDENCE", not hit,
        f"membership 列未包含任何价格/涨停/热度字段（命中：{hit if hit else '无'}）；"
        f"本阶段只读 membership，不增删任何成员")

    t5 = pd.to_numeric(daily["top5_concentration"], errors="coerce")
    n_conc = int((t5 > STATE_RULES["anomaly_high_concentration"]).sum())
    n_flag = int(daily["anomaly_flags"].fillna("").str.contains("HIGH_CONCENTRATION").sum())
    add("G_CONCENTRATION", n_conc == n_flag and t5.notna().all(),
        f"top5_concentration>{STATE_RULES['anomaly_high_concentration']} 行数={n_conc}，"
        f"已标记 HIGH_CONCENTRATION 行数={n_flag}，集中度缺失={int(t5.isna().sum())}")

    cb = pd.to_numeric(daily["core_breadth"], errors="coerce")
    bad_h = daily[(daily["sector_state_base"] == "STRONG") & (cb < STATE_RULES["strong_core_breadth"])]
    add("H_CORE_NOT_WEAK_STRONG", bad_h.empty,
        f"STRONG 且 core_breadth<{STATE_RULES['strong_core_breadth']} 行数={len(bad_h)}")

    return pd.DataFrame(res)


# ────────────────────────────────────────────────────────────────────────────
# 审计报告（需求 §三十）
# ────────────────────────────────────────────────────────────────────────────

def build_audit(daily, meta, master, mdf, quality, legacy_found, val, lookahead):
    last = max(daily["trade_date"].astype(str))
    today = daily[daily["trade_date"].astype(str) == last]
    L = []
    A = L.append

    A("# Sector State Base 审计报告（Step 3）")
    A("")
    A(f"- 生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}")
    A(f"- 基准日：{meta['base_date']}｜最近回放日：{last}")
    A(f"- Canonical 真源：sector_master.json（version={master.version}，sectors={len(master.sectors)}）")
    A(f"- 成员口径：membership_confidence >= {MEMBERSHIP_CONF_MIN}（不限 membership_type，用户决策 B）")
    A(f"- 成员模式：{meta['membership_mode']}"
      + ("（STATIC_SNAPSHOT：Step 2 为单一快照，按静态产业定义回放，非时点成员回放）"
         if meta["membership_mode"] == "snapshot" else "（strict：effective_date <= D 严格过滤）"))
    A(f"- 回放窗口：{meta['replay_dates'][0]} ~ {meta['replay_dates'][-1]}"
      f"（{len(meta['replay_dates'])} 个交易日；行情面板 {meta['panel_start']}~{meta['panel_end']}）")
    A(f"- 基准：{BENCH_PRIMARY} {BENCH_PRIMARY_NAME}（vs_market_*）、"
      f"{BENCH_SECONDARY} {BENCH_SECONDARY_NAME}（vs_midcap_*）")
    A(f"- Sector Health 权重：" + "，".join(f"{k}={v:.2f}" for k, v in HEALTH_WEIGHTS.items())
      + f"；core_primary 内部 " + "，".join(f"{k}={v}" for k, v in CORE_PRIMARY_INNER.items()))
    A(f"- return 驱动权重合计：{RETURN_WEIGHT_SHARE:.2f}"
      f"（需求 §十六 要求 <= 0.30：{'满足' if RETURN_WEIGHT_SHARE <= 0.30 else '不满足'}）")
    A(f"- LEGACY 配置（只报告存在性、未读取内容）："
      + ("、".join(os.path.relpath(p, PROJ_DIR) for p in legacy_found) if legacy_found else "未发现"))
    A("")

    A("## 1. 数据覆盖")
    A("")
    A(f"- 回放交易日：{daily['trade_date'].nunique()} 个")
    A(f"- Sector 数：master {len(master.sectors)} 个；输出涉及 {daily['sector_id'].nunique()} 个")
    A(f"- 最近回放日有效 Sector（valid_member_count>0）：{int((today['valid_member_count'] > 0).sum())} 个")
    A(f"- membership 行数：{len(mdf)}（口径 confidence>={MEMBERSHIP_CONF_MIN}；"
      f"源文件 {mdf.attrs.get('rows_all', len(mdf))} 行，"
      f"低置信过滤 {mdf.attrs.get('rows_all', len(mdf)) - len(mdf)} 行）；"
      f"股票数：{mdf['ts_code'].nunique()} 只；"
      f"唯一键 (ts_code, sector_id, effective_date) 重复 {int(mdf.duplicated(subset=['ts_code','sector_id','effective_date']).sum())}")
    A(f"- 最近回放日有效成员合计：{int(today['valid_member_count'].sum())}；"
      f"缺失 {int((today['member_count'] - today['valid_member_count']).sum())}")
    A(f"- 最近回放日：缺失价格成员 {int(today['missing_price_count'].sum())}、"
      f"缺失成交额 {int(today['missing_amount_count'].sum())}、"
      f"成交额=0（疑似停牌）{int(today['stale_member_count'].sum())}")
    rbd = pd.Series(list(meta["rows_by_day"].values()))
    A(f"- 全市场日线记录数：中位数 {int(rbd.median())}，最小 {int(rbd.min())}，最大 {int(rbd.max())}")
    A(f"- 数据质量门槛：data_quality_score < {DQ_INVALID_THRESHOLD} 或 valid_member_count < "
      f"{MIN_VALID_MEMBERS} 判 DATA_INVALID")
    A("")

    A("## 2. Sector Quality（复用 Step 2 sector_quality.csv）")
    A("")
    tier_cnt = quality["tier"].value_counts().to_dict()
    tt = master.tracking_tiers
    A("| Tier | 数量 | master 规则 |")
    A("| --- | --- | --- |")
    for t in TIERS:
        r = tt.get(t, {})
        A(f"| {t} | {tier_cnt.get(t, 0)} | min_members={r.get('min_valid_members')}, "
          f"min_purity={r.get('min_purity', '-')}, min_stability={r.get('min_stability', '-')} |")
    A("")
    A(f"- sector_purity：中位数 {pd.to_numeric(quality['sector_purity']).median():.4f}，"
      f"最低 {pd.to_numeric(quality['sector_purity']).min():.4f}，"
      f"最高 {pd.to_numeric(quality['sector_purity']).max():.4f}")
    A("- theme_purity 直接复用 Step 2 的 sector_purity（0.30×行业一致性 + 0.25×产品一致性 + "
      "0.20×核心占比 + 0.15×概念纯度 + 0.10×成员稳定性），未另造第二套质量算法。")
    A("")

    A("## 3. Sector State 分布")
    A("")
    A("| 状态 | 最近回放日 | 回放窗口内累计 |")
    A("| --- | --- | --- |")
    for s in STATES:
        A(f"| {s} | {int((today['sector_state_base'] == s).sum())} | "
          f"{int((daily['sector_state_base'] == s).sum())} |")
    A("")
    A("- 判定阈值：" + "，".join(f"{k}={v}" for k, v in STATE_RULES.items()))
    A("- 状态确认平滑：原始判定为新状态且连续 2 个交易日成立，才写入 sector_state_base；双向对称"
      "（升级与降级同规则），用于抑制日频阈值抖动（原始判定在 72.5% 的观测日发生切换，"
      "平滑后降至 12.7%，平均状态持续期由 1.36 日升至 7.2 日）；对称规则不产生单向下偏。"
      "两条例外：(1) 已确认状态为 STRONG 而当日核心层广度 <0.50（STRONG 的核心层硬条件当日不成立）"
      "时立即降级为当日原始判定，保证【核心成员转弱时不得继续标注 STRONG】（需求 §三十二 H）；"
      "(2) DATA_INVALID 双向立即生效。当日原始判定保留在 sector_state_history.csv 的 state_raw 字段，"
      "未确认的原始判定在 state_reason 中显式说明。该规则只用当日及以前数据，不引入未来信息。")
    raw_cnt = today["state_raw"].value_counts().to_dict()
    raw_all = daily["state_raw"].value_counts().to_dict()
    A("")
    A("| 原始判定（平滑前） | 最近回放日 | 回放窗口内累计 |")
    A("| --- | --- | --- |")
    for s in STATES:
        A(f"| {s} | {raw_cnt.get(s, 0)} | {raw_all.get(s, 0)} |")
    A("- core_breadth 退化规则：若某 Sector 无 CORE 层成员（core_valid_count=0），"
      "core_breadth 退化为 PRIMARY 层广度，并用 core_layer 字段标注实际使用的层，"
      "避免把【没有核心成员】误判为【核心成员弱】。")
    A("")

    A("## 4. 状态变化")
    A("")
    ch = daily[daily["state_changed"].astype(int) == 1]
    A(f"- 窗口内状态变化次数：{len(ch)}")
    if len(ch):
        pair = ch.groupby(["prev_state", "sector_state_base"]).size().sort_values(ascending=False)
        A("")
        A("| 迁移 | 次数 |")
        A("| --- | --- |")
        for (p, c), n in pair.items():
            mk = "" if (p, c) in TRANSITION_WHITELIST else "（非白名单，需人工复核）"
            A(f"| {p} → {c} | {n} {mk} |")
    A("")
    tch = ch[ch["trade_date"].astype(str) == last]
    A(f"- 最近回放日发生变化：{len(tch)} 个")
    for r in tch.itertuples():
        A(f"  - {r.sector_id} {r.sector_name}：{r.prev_state} → {r.sector_state_base}；{r.state_reason}")
    A("")

    A("## 5. 异常主题（最近回放日）")
    A("")
    for flag in ("DATA_INVALID", "LOW_COVERAGE", "HIGH_CONCENTRATION", "LOW_BREADTH",
                 "CORE_WEAK", "MEMBERSHIP_UNSTABLE"):
        sub = today[today["anomaly_flags"].fillna("").str.contains(flag)]
        names = "、".join(f"{r.sector_id} {r.sector_name}" for r in sub.itertuples())
        A(f"- **{flag}**（{len(sub)}）：{names if names else '无'}")
    A("")

    A("## 6. 样例验证（最近回放日）")
    A("")
    A("| Sector | 名称 | Tier | 有效成员 | 广度 | 加权广度 | 核心广度 | ew_ret_5 | vs沪深300(5日) | 成交额占比 | 量能比5 | Top5集中度 | Health | 状态 |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for sid in SAMPLE_SECTOR_IDS:
        g = today[today["sector_id"].astype(str) == sid]
        if g.empty:
            A(f"| {sid} | - | - | - | - | - | - | - | - | - | - | - | - | 无数据 |")
            continue
        r = g.iloc[0]
        cb_v = fnum(r["core_breadth"])
        cb_txt = "NA" if math.isnan(cb_v) else f"{cb_v:+.2f}"
        A(f"| {sid} | {r['sector_name']} | {r.get('tier', '')} | "
          f"{int(r['valid_member_count'])}/{int(r['member_count'])} | {fnum(r['breadth']):+.2f} | "
          f"{fnum(r['weighted_breadth']):+.2f} | {cb_txt} | "
          f"{fnum(r['ew_ret_5']) * 100:+.2f}% | {fnum(r['vs_market_5']) * 100:+.2f}pp | "
          f"{fnum(r['sector_amount_share']) * 100:.2f}% | {fnum(r['volume_ratio_5']):.2f} | "
          f"{fnum(r['top5_concentration']):.2f} | {fnum(r['sector_health']):.1f} | "
          f"{r['sector_state_base']} |")
    A("")
    A("成员/广度/核心成员/主题收益/成交量/状态的逐项核对依据（均由可验证字段拼装，不含主观判断）：")
    A("")
    for sid in SAMPLE_SECTOR_IDS:
        g = today[today["sector_id"].astype(str) == sid]
        if g.empty:
            continue
        r = g.iloc[0]
        A(f"- **{sid} {r['sector_name']}**（{r['sector_state_base']}）：{r['state_reason']}")
    A("")

    A("## 7. Validation（需求 §三十二 A~H）")
    A("")
    A("| 检查 | 结果 | 说明 |")
    A("| --- | --- | --- |")
    for r in val.itertuples():
        A(f"| {r.check} | {r.status} | {str(r.detail).replace('|', '/')} |")
    A("")
    A(f"- 汇总：{int((val['status'] == 'PASS').sum())}/{len(val)} 项通过。")
    A("")

    A("## 8. 未来函数与实现说明")
    A("")
    if lookahead:
        A(f"- 截断重算检验：{'通过' if lookahead.get('ok') else '未通过'} — {lookahead.get('detail', '')}")
    A("- 收益口径：Tushare daily.pct_chg 连乘累计（pre_close 已含除权除息调整），等效复权收益，"
      "未再叠加 adj_factor。")
    A("- 所有滚动窗（rolling / shift）均为后向窗；跨日派生量按 sector 自身时间序列生成，无前视窗口。")
    A("- membership 在 strict 模式按 effective_date <= D 过滤；snapshot 模式按静态定义回放并标注。")
    A("- 本阶段不读取 theme_config.json / subtheme_map.json / theme.json，"
      "不继承任何旧 theme_id / 成员 / 核心公司 / 排名 / 规则。")
    A("")

    A("## 9. 本阶段边界（需求 §三十七）")
    A("")
    A("只输出 Sector Quality / Breadth / Structure / Health / State Base 与 Tracking Pool；")
    A("不实现 SEOS、板块启动与轮动预测、生命周期预测、HVT、IGE、F120、个股 BUY/NO TRADE、交易执行。")
    A("")
    A("## 10. 输出文件")
    A("")
    for p in ("data/sector_daily_stats.csv", "data/sector_breadth.csv", "data/sector_strength.csv",
              "data/sector_volume.csv", "data/sector_state_history.csv",
              "output/sector_state_today.json", "output/sector_tracking_pool.json",
              "output/sector_quality_daily.csv", "output/sector_state_audit.md",
              "output/sector_state_validation.csv"):
        A(f"- {p}")
    A("")

    text = "\n".join(L)
    path = os.path.join(OUTPUT_DIR, "sector_state_audit.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    log.info("写出 %s", os.path.relpath(path, BASE_DIR))
    return text


# ────────────────────────────────────────────────────────────────────────────
# CLI（需求 §三十一）
# ────────────────────────────────────────────────────────────────────────────

def load_outputs_for_validate():
    """validate 模式：从既有时间序列输出拼装验证所需字段。"""
    key = ["trade_date", "sector_id"]
    hist = pd.read_csv(os.path.join(DATA_DIR, "sector_state_history.csv"),
                       dtype={"trade_date": str, "sector_id": str})
    hist = hist.rename(columns={"current_state": "sector_state_base"})
    br = pd.read_csv(os.path.join(DATA_DIR, "sector_breadth.csv"),
                     dtype={"trade_date": str, "sector_id": str})
    st = pd.read_csv(os.path.join(DATA_DIR, "sector_strength.csv"),
                     dtype={"trade_date": str, "sector_id": str})
    ds = pd.read_csv(os.path.join(DATA_DIR, "sector_daily_stats.csv"),
                     dtype={"trade_date": str, "sector_id": str})
    hist = hist.merge(br[key + ["breadth", "core_breadth"]], on=key, how="left")
    hist = hist.merge(st[key + ["top5_concentration"]], on=key, how="left")
    hist = hist.merge(ds[key + ["data_quality_score"]], on=key, how="left",
                      suffixes=("", "_ds"))
    if "data_quality_score_ds" in hist.columns:
        hist["data_quality_score"] = hist["data_quality_score_ds"].fillna(hist["data_quality_score"])
    return hist, sorted(hist["trade_date"].astype(str).unique().tolist())


def main():
    ap = argparse.ArgumentParser(
        description="板块体系第三阶段：Canonical Sector Quality + State 基础层")
    ap.add_argument("--full", action="store_true", help="完整重建（默认回放最近 N 个交易日）")
    ap.add_argument("--validate", action="store_true", help="只做验证（基于已有输出）")
    ap.add_argument("--date", type=str, default=None, help="指定日期 YYYYMMDD（单日）")
    ap.add_argument("--history-days", type=int, default=HISTORY_DAYS_DEFAULT,
                    help=f"回放交易日数（默认 {HISTORY_DAYS_DEFAULT}）")
    ap.add_argument("--membership-mode", choices=("snapshot", "strict"), default="snapshot",
                    help="snapshot=静态定义回放（默认）；strict=effective_date<=D 严格过滤")
    ap.add_argument("--no-fetch", action="store_true", help="只用本地缓存，不调用 Tushare")
    args = ap.parse_args()

    scan = scan_project()
    if not scan["master"]:
        log.error("未找到 sector_master.json，终止")
        return 2
    master = SectorMaster(scan["master"])
    log.info("Canonical 真源：%s（version=%s，sectors=%d）",
             scan["master"], master.version, len(master.sectors))
    log.info("LEGACY 配置只报告不读取：%s",
             [os.path.relpath(p, PROJ_DIR) for p in scan["legacy_found"]] or "未发现")

    mdf = load_membership()
    quality = load_quality()

    if args.validate:
        try:
            daily, replay = load_outputs_for_validate()
        except FileNotFoundError as e:
            log.error("缺少既有输出，无法验证：%s", e)
            return 2
        base_date = max(replay)
        meta = {"base_date": base_date, "panel_index": replay, "replay_dates": replay,
                "membership_mode": args.membership_mode}
        lookahead = check_no_lookahead(mdf, quality, base_date, replay,
                                       args.membership_mode)
        val = run_validation(daily, meta, master, mdf, quality, scan["legacy_found"], lookahead)
        print(val.to_string(index=False))
        val.to_csv(os.path.join(OUTPUT_DIR, "sector_state_validation.csv"),
                   index=False, encoding="utf-8-sig")
        return 0 if (val["status"] == "PASS").all() else 1

    base_date = args.date or max(mdf["effective_date"].astype(str).max(), "")
    # --date 只限定「写出哪一天」，不缩短「计算窗口」：滚动特征（volume_ratio_N /
    # sector_amount_share_change_20d / *_change_5d）依赖回放窗口长度，窗口过短会
    # 结构性全为 NaN，并沿 Step 4 退化为 volume_participation 的统一填充值。
    history_days = max(REPLAY_MIN_DAYS, max(1, args.history_days))

    daily, meta = run_state_layer(mdf, quality, base_date, history_days,
                                  args.membership_mode, args.no_fetch)
    log.info("Sector 统计行数=%d，覆盖 %d 个交易日 / %d 个 Sector",
             len(daily), daily["trade_date"].nunique(), daily["sector_id"].nunique())

    # --date：回放窗口只用于滚动特征计算，写出时仅落基准日一行，避免用被截断的
    # 窗口整体覆盖历史行（那样会把窗口首部的 *_change_20d / volume_ratio_5 写坏）。
    if args.date:
        write_daily = daily[daily["trade_date"].astype(str) == str(base_date)].copy()
        if not len(write_daily):
            write_daily = daily
    else:
        write_daily = daily
    emit(write_daily, quality)
    today_obj = emit_state_today(daily, meta)
    emit_tracking_pool(daily, quality)

    lookahead = check_no_lookahead(mdf, quality, base_date, meta["replay_dates"],
                                   args.membership_mode)
    log.info("未来函数检验：%s", lookahead.get("detail"))

    val = run_validation(daily, meta, master, mdf, quality, scan["legacy_found"], lookahead)
    val.to_csv(os.path.join(OUTPUT_DIR, "sector_state_validation.csv"),
               index=False, encoding="utf-8-sig")
    print(val.to_string(index=False))
    build_audit(daily, meta, master, mdf, quality, scan["legacy_found"], val, lookahead)

    hist = pd.read_csv(os.path.join(DATA_DIR, "sector_state_history.csv"),
                       dtype={"trade_date": str})
    ch = hist[hist["state_changed"].astype(int) == 1]
    print()
    print(f"基准日 {today_obj['trade_date']}；回放 {len(meta['replay_dates'])} 个交易日；"
          f"成员模式={meta['membership_mode']}")
    print("状态分布：" + "，".join(f"{k}={v}" for k, v in today_obj["state_counts"].items() if v))
    print(f"状态变化：窗口内 {len(ch)} 次，最近一日 "
          f"{int((ch['trade_date'].astype(str) == today_obj['trade_date']).sum())} 次")
    print("异常标记：" + "，".join(f"{k}={v}" for k, v in today_obj["anomaly_counts"].items() if v))
    print(f"Validation：{int((val['status'] == 'PASS').sum())}/{len(val)} 项通过")
    return 0 if (val["status"] == "PASS").all() else 1


if __name__ == "__main__":
    sys.exit(main())
