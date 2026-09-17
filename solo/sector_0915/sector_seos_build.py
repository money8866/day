# -*- coding: utf-8 -*-
"""
第四步 — SEOS: Sector Early Signal & Opportunity System
=======================================================

定位
----
在 Step 1/2（Canonical Theme Definition + Membership）与 Step 3（Theme Quality +
Theme State 基础层）之上，识别主题的「状态改善 / 启动前兆 / 强化 / 退潮 / 再强化 /
轮动切换」，输出主题层信号，不输出任何个股信号。

链路：
    sector_master.json → theme_mapping → theme_membership
        → Theme Daily Statistics → Theme Health
        → SEOS → EARLY / EMERGING / CONFIRMING / STRONG / COOLING / DETERIORATING / EXITING
        → Theme Watchlist (opportunity pool)

纪律（对应需求 §一 ~ §三、§三十七、§四十七）
--------------------------------------------
* 唯一主题定义只允许 sector_master.json（= 需求中的 theme_master.json）。
  theme_config.json / subtheme_map.json / theme.json 永远 LEGACY / REFERENCE ONLY，
  本程序绝不读取（由 --validate 的 legacy 隔离检查强制）。
* 本阶段是纯消费层：不取 Tushare、不重建行业/概念分类、不重建股票主题归属、
  不重建 core / primary company、不重建 keyword mapping。
* 不进入 HVT / IGE / F120 / 个股评分 / 个股 BUY / NO TRADE / 交易执行 / 仓位建议 /
  个股止损 / 个股买点。SEOS 的输出对象只有 THEME（本项目命名 = Sector）。

术语映射（项目约定）
--------------------
需求文档使用 theme_*，本项目自 Step 1 起统一使用 sector_*：
    theme_id            ≡ sector_id
    theme_name          ≡ sector_name
    theme_master.json   ≡ sector_master.json
    theme_* 输出文件     ≡ sector_* 输出文件
    theme_seos.py       ≡ sector_seos_build.py
语义一一对应，不改变任何字段含义。

用法
----
    python sector_seos_build.py --full                 # 全量重算并写出
    python sector_seos_build.py --date 20260915        # 只重算指定交易日
    python sector_seos_build.py --validate             # 12 项验证
    python sector_seos_build.py --full --validate
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
import sys
from datetime import datetime

import numpy as np
import pandas as pd

# ────────────────────────────────────────────────────────────────────────────
# 路径
# ────────────────────────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJ_DIR = os.path.dirname(BASE_DIR)
for _p in (PROJ_DIR, BASE_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

CONFIG_DIR = os.path.join(BASE_DIR, "config")
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
LOG_DIR = os.path.join(BASE_DIR, "logs")

from sector_mapping_build import SectorMaster, scan_project, write_json  # noqa: E402
from sli.utils import setup_logging  # noqa: E402

log = setup_logging(LOG_DIR, name="sector_seos")

SEOS_CONFIG_PATH = os.path.join(CONFIG_DIR, "seos_config.json")
MASTER_PATH = os.path.join(BASE_DIR, "sector_master.json")

IN_DAILY = os.path.join(DATA_DIR, "sector_daily_stats.csv")
IN_BREADTH = os.path.join(DATA_DIR, "sector_breadth.csv")
IN_STRENGTH = os.path.join(DATA_DIR, "sector_strength.csv")
IN_VOLUME = os.path.join(DATA_DIR, "sector_volume.csv")
IN_STATE_HISTORY = os.path.join(DATA_DIR, "sector_state_history.csv")
IN_MEMBERSHIP = os.path.join(DATA_DIR, "sector_membership.csv")
IN_QUALITY = os.path.join(OUTPUT_DIR, "sector_quality_daily.csv")
IN_STATE_TODAY = os.path.join(OUTPUT_DIR, "sector_state_today.json")

OUT_SEOS_DAILY = os.path.join(DATA_DIR, "sector_seos_daily.csv")
OUT_FLAGS = os.path.join(DATA_DIR, "sector_signal_flags.csv")
OUT_DIVERGENCE = os.path.join(DATA_DIR, "sector_divergence.csv")
OUT_ROTATION = os.path.join(DATA_DIR, "sector_rotation.csv")
OUT_GROUP = os.path.join(DATA_DIR, "sector_group_seos_daily.csv")
OUT_PHASE_HISTORY = os.path.join(DATA_DIR, "sector_phase_history.csv")
OUT_SEOS_TODAY = os.path.join(OUTPUT_DIR, "sector_seos_today.json")
OUT_POOL = os.path.join(OUTPUT_DIR, "sector_opportunity_pool.json")
OUT_BACKTEST = os.path.join(OUTPUT_DIR, "seos_backtest.md")
OUT_AUDIT = os.path.join(OUTPUT_DIR, "seos_audit.md")
OUT_VALIDATION = os.path.join(OUTPUT_DIR, "seos_validation.csv")

LEGACY_NAMES = ("theme_config.json", "subtheme_map.json", "theme.json")

LADDER = ["DORMANT", "EARLY", "EMERGING", "CONFIRMING", "STRONG"]
RANK = {p: i for i, p in enumerate(LADDER)}
ALL_PHASES = LADDER + ["COOLING", "DETERIORATING", "EXITING", "DATA_INVALID"]

# 状态确认streak（需求 §十九/§二十）：覆盖态 enter/off + 梯级升(up)/降(below)确认
STREAK_KEYS = ["exit", "exit_off", "deter", "deter_off", "cool", "cool_off", "up", "below"]

ALLOWED_TRANSITIONS = {
    "DORMANT": {"DORMANT", "EARLY", "EMERGING", "CONFIRMING", "STRONG",
                "DETERIORATING", "EXITING"},
    "EARLY": {"DORMANT", "EARLY", "EMERGING", "CONFIRMING", "STRONG",
              "DETERIORATING", "EXITING"},
    "EMERGING": {"EARLY", "EMERGING", "CONFIRMING", "STRONG",
                 "DETERIORATING", "EXITING"},
    "CONFIRMING": {"EMERGING", "CONFIRMING", "STRONG",
                   "COOLING", "DETERIORATING", "EXITING"},
    "STRONG": {"CONFIRMING", "STRONG", "COOLING", "DETERIORATING", "EXITING"},
    "COOLING": {"DORMANT", "EARLY", "EMERGING", "CONFIRMING", "STRONG",
                "COOLING", "DETERIORATING", "EXITING"},
    "DETERIORATING": {"DORMANT", "EARLY", "EMERGING", "CONFIRMING", "STRONG",
                      "DETERIORATING", "EXITING"},
    "EXITING": {"EXITING", "DORMANT"},
    "DATA_INVALID": {"DATA_INVALID", "DORMANT"},
}

FLAG_NAMES = [
    "BREADTH_EXPANDING", "CORE_EXPANDING", "RS_TURNING_UP", "VOLUME_PARTICIPATION",
    "AMOUNT_SHARE_EXPANDING", "HEALTH_IMPROVING", "EXTENSION_HIGH", "CONCENTRATION_HIGH",
    "CORE_WEAK", "BREADTH_DIVERGENCE", "RS_DIVERGENCE", "VOLUME_DIVERGENCE",
    "FAILED_EXPANSION",
]
POSITIVE_FLAGS = [
    "BREADTH_EXPANDING", "CORE_EXPANDING", "RS_TURNING_UP", "VOLUME_PARTICIPATION",
    "AMOUNT_SHARE_EXPANDING", "HEALTH_IMPROVING",
]

SEOS_COMPONENTS = [
    "breadth_expansion", "core_breadth", "relative_strength", "volume_participation",
    "amount_share", "health_momentum", "consistency",
]

IMPROVING_DIMS = [
    "breadth_delta_5", "core_breadth_delta_5", "rs_turn_5",
    "volume_ratio_delta_5", "amount_share_delta_5", "theme_health_delta_5",
]

# 全程序打开过的文件（用于 legacy 隔离验证）
READ_FILES: list = []


# ────────────────────────────────────────────────────────────────────────────
# 基础工具
# ────────────────────────────────────────────────────────────────────────────

def _read_csv(path: str, **kw) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"缺少输入文件: {path}")
    READ_FILES.append(os.path.abspath(path))
    kw.setdefault("dtype", {})
    if isinstance(kw["dtype"], dict):
        kw["dtype"].setdefault("trade_date", str)
        kw["dtype"].setdefault("sector_id", str)
    return pd.read_csv(path, **kw)


def load_json(path: str):
    if not os.path.exists(path):
        raise FileNotFoundError(f"缺少配置文件: {path}")
    READ_FILES.append(os.path.abspath(path))
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def dump_json(obj, path: str):
    """写出严格合法 JSON：非有限浮点一律写为 null（避免 NaN 污染下游解析）。"""
    def _clean(o):
        if isinstance(o, dict):
            return {k: _clean(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [_clean(v) for v in o]
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating, float)):
            v = float(o)
            return None if (math.isnan(v) or math.isinf(v)) else v
        if isinstance(o, (np.bool_,)):
            return bool(o)
        return o

    clean = _clean(obj)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2, allow_nan=False)
    log.info("写出 %s", os.path.relpath(path, BASE_DIR))


def write_merge_csv(df: pd.DataFrame, path: str, key_cols=("trade_date", "sector_id")):
    """时间序列合并写出：保留历史行，只替换本次重算覆盖到的键；表结构不一致时备份 .bak。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        df.to_csv(path, index=False, encoding="utf-8-sig")
        log.info("写出 %s (%d 行, 新建)", os.path.relpath(path, BASE_DIR), len(df))
        return
    old = pd.read_csv(path, dtype={c: str for c in key_cols if c in df.columns})
    if list(old.columns) != list(df.columns):
        shutil.copy2(path, path + ".bak")
        log.warning("表结构变化，已备份 %s，按新结构写出", os.path.basename(path) + ".bak")
        df.to_csv(path, index=False, encoding="utf-8-sig")
        log.info("写出 %s (%d 行, 结构更新)", os.path.relpath(path, BASE_DIR), len(df))
        return
    keys = set(map(tuple, df[list(key_cols)].astype(str).values))
    keep = ~old[list(key_cols)].astype(str).apply(tuple, axis=1).isin(keys)
    out = pd.concat([old[keep], df], ignore_index=True)
    out = out.sort_values(list(key_cols), kind="mergesort").reset_index(drop=True)
    out.to_csv(path, index=False, encoding="utf-8-sig")
    log.info("写出 %s (%d 行, 合并保留 %d 行)", os.path.relpath(path, BASE_DIR),
             len(out), int(keep.sum()))


def _fv(v) -> float:
    """安全转 float，None/NaN/inf → nan。"""
    if v is None:
        return float("nan")
    try:
        x = float(v)
    except (TypeError, ValueError):
        return float("nan")
    if math.isnan(x) or math.isinf(x):
        return float("nan")
    return x


def _cmp(v, thr, op) -> bool:
    x = _fv(v)
    if math.isnan(x):
        return False
    return op(x, thr)


def _gt(v, thr) -> bool:
    return _cmp(v, thr, lambda a, b: a > b)


def _ge(v, thr) -> bool:
    return _cmp(v, thr, lambda a, b: a >= b)


def _lt(v, thr) -> bool:
    return _cmp(v, thr, lambda a, b: a < b)


def _le(v, thr) -> bool:
    return _cmp(v, thr, lambda a, b: a <= b)


def _eq(v, s) -> bool:
    return v is not None and str(v) == str(s)


def ramp_series(s, lo: float, hi: float) -> pd.Series:
    v = pd.to_numeric(s, errors="coerce").astype(float)
    if hi <= lo:
        return pd.Series(np.nan, index=v.index)
    return ((v - lo) / (hi - lo) * 100.0).clip(lower=0.0, upper=100.0)


def ramp_scalar(x, lo: float, hi: float, neutral: float) -> float:
    x = _fv(x)
    if math.isnan(x) or hi <= lo:
        return float(neutral)
    if x <= lo:
        return 0.0
    if x >= hi:
        return 100.0
    return (x - lo) / (hi - lo) * 100.0


def _num(v, default=float("nan")) -> float:
    x = _fv(v)
    return default if math.isnan(x) else x


def r4(x):
    """数值型输出助手：非有限值 → None（JSON 友好）。"""
    x = _fv(x)
    return None if math.isnan(x) else round(x, 4)


def s4(x) -> str:
    """文本展示助手：非有限值 → NA。"""
    x = _fv(x)
    return "NA" if math.isnan(x) else f"{round(x, 4):g}"


# ────────────────────────────────────────────────────────────────────────────
# 输入层（纯消费 Step 3 产出）
# ────────────────────────────────────────────────────────────────────────────

def build_panel() -> pd.DataFrame:
    daily = _read_csv(IN_DAILY)
    breadth = _read_csv(IN_BREADTH)
    strength = _read_csv(IN_STRENGTH)
    volume = _read_csv(IN_VOLUME)
    hist = _read_csv(IN_STATE_HISTORY)
    quality = _read_csv(IN_QUALITY)
    state_today = load_json(IN_STATE_TODAY)

    key = ["trade_date", "sector_id"]

    b_cols = ["core_layer", "core_valid_count", "primary_valid_count",
              "secondary_valid_count", "breadth_change_5d", "core_breadth_change_5d"]
    s_cols = ["relative_strength_5", "relative_strength_10", "relative_strength_20",
              "bench_ret_5", "ew_median_ret_5", "core_ew_ret_5", "core_vs_market_5"]
    v_cols = ["market_amount", "avg_member_amount", "sector_amount_share_change_5d",
              "sector_amount_share_change_20d", "sector_turnover_rate_wavg"]
    h_cols = ["prev_state", "current_state", "state_raw", "state_changed",
              "state_duration", "state_reason"]
    q_cols = ["tier", "sector_purity", "sector_quality_score"]

    df = daily.copy()
    df = df.merge(breadth[key + b_cols], on=key, how="left", validate="one_to_one")
    df = df.merge(strength[key + s_cols], on=key, how="left", validate="one_to_one")
    df = df.merge(volume[key + v_cols], on=key, how="left", validate="one_to_one")
    df = df.merge(hist[key + h_cols], on=key, how="left", validate="one_to_one")
    df = df.merge(quality[key + q_cols], on=key, how="left", validate="one_to_one")

    # Step 3 的横截面成交额占比与 volume 表一致（交叉校验，不新建口径）
    if "sector_amount_share" in volume.columns:
        chk = df.merge(volume[key + ["sector_amount_share"]], on=key,
                       how="left", suffixes=("", "_vol"), validate="one_to_one")
        gap = (chk["sector_amount_share"] - chk["sector_amount_share_vol"]).abs().max()
        if gap > 1e-9:
            log.warning("sector_amount_share 在 daily 与 volume 表间存在差异 max=%.3e", gap)

    df["trade_date"] = df["trade_date"].astype(str)
    df["sector_id"] = df["sector_id"].astype(str)
    df = df.sort_values(["sector_id", "trade_date"], kind="mergesort").reset_index(drop=True)

    log.info("输入面板 %d 行 / %d 主题 / %d 交易日",
             len(df), df["sector_id"].nunique(), df["trade_date"].nunique())
    return df, state_today


# ────────────────────────────────────────────────────────────────────────────
# 特征层（需求 §五 ~ §十二）
# ────────────────────────────────────────────────────────────────────────────

def add_delta(df: pd.DataFrame, base: str, k: int, out: str) -> None:
    df[out] = df[base] - df.groupby("sector_id", sort=False)[base].shift(k)


def compute_features(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    df = df.copy()

    # 语义别名：Step 3 的 sector_* → SEOS 的 theme_*
    df["theme_health"] = pd.to_numeric(df["sector_health"], errors="coerce")
    df["theme_amount_share"] = pd.to_numeric(df["sector_amount_share"], errors="coerce")
    df["relative_strength"] = pd.to_numeric(df["relative_strength_5"], errors="coerce")
    df["sector_state_base"] = df["current_state"].astype(str)

    g = df.groupby("sector_id", sort=False)

    # §五：原始特征（全部只使用 <=D 的行情）
    add_delta(df, "breadth", 1, "breadth_delta_1")
    add_delta(df, "breadth", 3, "breadth_delta_3")
    add_delta(df, "breadth", 5, "breadth_delta_5")
    add_delta(df, "breadth", 10, "breadth_delta_10")
    add_delta(df, "weighted_breadth", 1, "weighted_breadth_delta_1")
    add_delta(df, "weighted_breadth", 3, "weighted_breadth_delta_3")
    add_delta(df, "weighted_breadth", 5, "weighted_breadth_delta_5")
    add_delta(df, "core_breadth", 1, "core_breadth_delta_1")
    add_delta(df, "core_breadth", 3, "core_breadth_delta_3")
    add_delta(df, "core_breadth", 5, "core_breadth_delta_5")
    add_delta(df, "primary_breadth", 3, "primary_breadth_delta_3")
    add_delta(df, "relative_strength", 3, "relative_strength_delta_3")
    add_delta(df, "relative_strength", 5, "relative_strength_delta_5")
    add_delta(df, "relative_strength", 10, "relative_strength_delta_10")
    add_delta(df, "volume_ratio_5", 3, "volume_ratio_delta_3")
    add_delta(df, "volume_ratio_5", 5, "volume_ratio_delta_5")
    add_delta(df, "theme_amount_share", 3, "amount_share_delta_3")
    add_delta(df, "theme_amount_share", 5, "amount_share_delta_5")
    add_delta(df, "theme_amount_share", 10, "amount_share_delta_10")
    add_delta(df, "theme_health", 1, "theme_health_delta_1")
    add_delta(df, "theme_health", 3, "theme_health_delta_3")
    add_delta(df, "theme_health", 5, "theme_health_delta_5")
    add_delta(df, "theme_health", 10, "theme_health_delta_10")

    # §六：Breadth Expansion —— 短窗均值相对长窗基线的抬升（扩散 vs 少数股上涨）
    for k in (3, 5, 10, 20):
        df[f"breadth_ma{k}"] = g["breadth"].transform(
            lambda s, k=k: s.rolling(k, min_periods=k).mean())
    df["breadth_expansion_3"] = df["breadth_ma3"] - df["breadth_ma10"]
    df["breadth_expansion_5"] = df["breadth_ma5"] - df["breadth_ma10"]
    df["breadth_expansion_10"] = df["breadth_ma10"] - df["breadth_ma20"]

    # §七：Breadth Acceleration
    df["breadth_acceleration"] = df["breadth_delta_3"] - df["breadth_delta_10"] / 3.0

    # §八：核心成员扩散
    df["core_lead_breadth"] = df["core_breadth"] - df["secondary_breadth"]

    # §九：Relative Strength Turn
    df["rs_turn_3"] = df["relative_strength_delta_3"]
    df["rs_turn_5"] = df["relative_strength_delta_5"]
    df["rs_turn_10"] = df["relative_strength_delta_10"]
    prev_rs = g["relative_strength"].shift(1)
    fresh = (df["relative_strength"] > 0) & (prev_rs <= 0)
    df["rs_fresh_cross"] = np.where(prev_rs.isna(), np.nan, fresh.astype(float))

    # §十：成交量结构
    df["volume_ratio_5_excess"] = df["volume_ratio_5"] - 1.0

    # §十一：Theme Health Momentum
    df["health_slope_5"] = df["theme_health_delta_5"] / 5.0
    df["health_slope_10"] = df["theme_health_delta_10"] / 10.0

    # §十三 consistency 辅助
    df["concentration_inverse"] = 1.0 - pd.to_numeric(df["top5_concentration"], errors="coerce")
    dim_mat = df[IMPROVING_DIMS]
    valid_cnt = dim_mat.notna().sum(axis=1)
    pos_cnt = (dim_mat > 0).sum(axis=1)
    df["improving_dims"] = pos_cnt.astype(int)
    df["dim_valid_count"] = valid_cnt.astype(int)
    df["improving_dim_ratio"] = np.where(valid_cnt > 0, pos_cnt / valid_cnt.replace(0, np.nan), np.nan)

    return df


# ────────────────────────────────────────────────────────────────────────────
# SEOS Score（需求 §十三 ~ §十六、§三十八）
# ────────────────────────────────────────────────────────────────────────────

def compute_scores(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    df = df.copy()
    neutral = float(cfg["data_rules"]["neutral_fill_score"])
    ramps = cfg["ramps"]

    for comp in SEOS_COMPONENTS:
        cw = cfg["component_weights"][comp]
        tot = pd.Series(0.0, index=df.index)
        wsum = 0.0
        for key, w in cw.items():
            if key in ramps and isinstance(ramps[key], list) and len(ramps[key]) == 2:
                v = ramp_series(df[key], float(ramps[key][0]), float(ramps[key][1]))
                v = v.fillna(neutral)
            elif key in ("rs_fresh_cross", "improving_dim_ratio"):
                # 无量纲比率/事件，直接映射到 0~100
                v = pd.to_numeric(df[key], errors="coerce").astype(float) * 100.0
                v = v.fillna(neutral)
            else:
                raise KeyError(f"ramps 中缺少 {comp} 的子项 {key} 的斜坡配置")
            tot = tot + w * v
            wsum += w
        df[f"seos_{comp}"] = tot / wsum if wsum > 0 else neutral

    df["seos_raw"] = sum(float(cfg["score_weights"][c]) * df[f"seos_{c}"] for c in SEOS_COMPONENTS)

    # §十五 / §三十八：extension_penalty，乘性扣减
    ep = cfg["extension_penalty"]
    pen = pd.Series(0.0, index=df.index)
    pen_w = 0.0
    for key, w in ep["weights"].items():
        lo, hi = ep["ramps"][key]
        v = ramp_series(df[key], float(lo), float(hi)).fillna(0.0) / 100.0
        pen = pen + w * v
        pen_w += w
    df["extension_penalty"] = (pen / pen_w if pen_w > 0 else pen).clip(0.0, 1.0)

    max_ded = float(ep.get("max_deduction", 1.0))
    df["seos_score"] = df["seos_raw"] * (1.0 - np.minimum(df["extension_penalty"], max_ded))

    # 数据质量与 warmup
    dq_thr = float(cfg["data_rules"]["data_quality_invalid_threshold"])
    df["data_quality_score"] = pd.to_numeric(df["data_quality_score"], errors="coerce")
    df["data_invalid"] = df["data_quality_score"].fillna(0.0) < dq_thr
    req = list(cfg["data_rules"]["warmup_required_deltas"])
    df["warmup_ready"] = df[req].notna().all(axis=1)

    # DATA_INVALID 正确传播：不产出评分
    df.loc[df["data_invalid"], "seos_score"] = np.nan
    return df


# ────────────────────────────────────────────────────────────────────────────
# Signal Flags（需求 §二十二）+ startup_quality（§二十三）
# ────────────────────────────────────────────────────────────────────────────

def compute_flags(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    df = df.copy()
    fr = cfg["flag_rules"]

    def _num_series(name):
        return pd.to_numeric(df[name], errors="coerce").astype(float)

    flags = {
        "BREADTH_EXPANDING": _num_series("breadth_delta_5") >= fr["breadth_expanding_delta_5"],
        "CORE_EXPANDING": _num_series("core_breadth_delta_5") >= fr["core_expanding_delta_5"],
        "RS_TURNING_UP": _num_series("rs_turn_5") > fr["rs_turning_up_turn_5"],
        "VOLUME_PARTICIPATION": _num_series("volume_ratio_5") >= fr["volume_participation_ratio_5"],
        "AMOUNT_SHARE_EXPANDING": _num_series("amount_share_delta_5") > fr["amount_share_expanding_delta_5"],
        "HEALTH_IMPROVING": _num_series("theme_health_delta_5") > fr["health_improving_delta_5"],
        "EXTENSION_HIGH": _num_series("extension_penalty") >= fr["ext_high_threshold"],
        "CONCENTRATION_HIGH": _num_series("top5_concentration") >= fr["concentration_high_top5"],
        "CORE_WEAK": (_num_series("core_breadth_delta_5") < fr["core_weak_delta_5"])
                     & (_num_series("core_breadth") < fr["core_weak_level"]),
        "BREADTH_DIVERGENCE": (_num_series("ew_ret_5") >= fr["divergence_min_ew_ret_5"])
                              & (_num_series("breadth_delta_5") <= fr["divergence_breadth_delta_5_max"]),
        "RS_DIVERGENCE": (_num_series("rs_turn_5") > 0)
                         & (_num_series("breadth_delta_5") <= fr["divergence_breadth_delta_5_max"]),
        "VOLUME_DIVERGENCE": (_num_series("volume_ratio_5") >= fr["volume_divergence_ratio_5"])
                             & (_num_series("breadth_delta_5") <= fr["volume_divergence_breadth_delta_5_max"]),
    }
    lag = int(fr["failed_expansion_prior_lag"])
    prior = df.groupby("sector_id", sort=False)["breadth_delta_5"].shift(lag)
    flags["FAILED_EXPANSION"] = (prior >= fr["failed_expansion_prior_delta_5"]) \
        & (_num_series("breadth_delta_3") <= fr["failed_expansion_now_delta_3"])

    # DATA_INVALID 行的所有 flags 强制 False（验证 CHECK_DATA_INVALID_PROPAGATION 口径）
    invalid = df["data_invalid"].astype(bool)
    for k, v in flags.items():
        df[k] = (v.fillna(False).astype(bool) & ~invalid)

    df["flag_count"] = df[FLAG_NAMES].sum(axis=1).astype(int)
    df["positive_flag_count"] = df[POSITIVE_FLAGS].sum(axis=1).astype(int)
    return df


def compute_startup_quality(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    df = df.copy()
    sq = cfg["startup_quality_rules"]
    bd5 = pd.to_numeric(df["breadth_delta_5"], errors="coerce")
    cd5 = pd.to_numeric(df["core_breadth_delta_5"], errors="coerce")
    vr5 = pd.to_numeric(df["volume_ratio_5"], errors="coerce")
    c5 = pd.to_numeric(df["top5_concentration"], errors="coerce")

    failed = df["FAILED_EXPANSION"]
    narrow = (c5 >= sq["narrow_min_top5_concentration"]) & ((bd5 <= 0) | (cd5 <= 0))
    vspike = (vr5 >= sq["volume_spike_min_ratio_5"]) & (bd5 <= sq["volume_spike_max_breadth_delta_5"])
    healthy = ((bd5 >= sq["healthy_min_breadth_delta_5"]) & (cd5 >= sq["healthy_min_core_delta_5"])
               & (vr5 >= sq["healthy_min_volume_ratio_5"])
               & (c5 <= sq["healthy_max_top5_concentration"]))
    no_data = bd5.isna() | cd5.isna() | df["seos_score"].isna()

    df["startup_quality"] = np.select(
        [no_data, failed, narrow, vspike, healthy],
        ["INSUFFICIENT_DATA", "FAILED_EXPANSION", "NARROW_LEADERSHIP", "VOLUME_SPIKE", "HEALTHY_EXPANSION"],
        default="NEUTRAL",
    )
    return df


# ────────────────────────────────────────────────────────────────────────────
# Divergence（需求 §二十四）
# ────────────────────────────────────────────────────────────────────────────

def compute_divergence(df: pd.DataFrame) -> pd.DataFrame:
    """需求 §二十四：四类背离显式落列（价格-广度 / 价格-核心 / RS-广度 / 量-广度）。"""
    df = df.copy()
    r5 = pd.to_numeric(df["ew_ret_5"], errors="coerce")
    bd5 = pd.to_numeric(df["breadth_delta_5"], errors="coerce")
    cd5 = pd.to_numeric(df["core_breadth_delta_5"], errors="coerce")
    rt5 = pd.to_numeric(df["rs_turn_5"], errors="coerce")
    vr5 = pd.to_numeric(df["volume_ratio_5"], errors="coerce")

    df["price_breadth_gap"] = r5 - bd5
    df["price_core_gap"] = r5 - cd5
    df["rs_breadth_gap"] = rt5 - bd5
    df["volume_breadth_gap"] = (vr5 - 1.0) - bd5

    # 四类背离（与 compute_flags 中的同名 flag 保持同一口径，显式落列便于下游消费）
    df["PRICE_BREADTH_DIVERGENCE"] = df["BREADTH_DIVERGENCE"].astype(bool)
    df["RS_BREADTH_DIVERGENCE"] = df["RS_DIVERGENCE"].astype(bool)
    df["VOLUME_BREADTH_DIVERGENCE"] = df["VOLUME_DIVERGENCE"].astype(bool)
    df["PRICE_CORE_DIVERGENCE"] = ((r5 > 0) & (cd5 < 0)).fillna(False).astype(bool)

    div_cols = ["PRICE_BREADTH_DIVERGENCE", "PRICE_CORE_DIVERGENCE",
                "RS_BREADTH_DIVERGENCE", "VOLUME_BREADTH_DIVERGENCE"]
    df["divergence_count"] = df[div_cols].sum(axis=1).astype(int)
    df["divergence_level"] = np.select(
        [df["divergence_count"] == 0, df["divergence_count"] == 1,
         df["divergence_count"] == 2, df["divergence_count"] >= 3],
        ["NONE", "LOW", "MEDIUM", "HIGH"], default="NONE")
    return df


# ────────────────────────────────────────────────────────────────────────────
# Rotation Signal（需求 §二十五）
# ────────────────────────────────────────────────────────────────────────────

def compute_rotation(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    df = df.copy()
    rr = cfg["rotation_rules"]
    lag = int(rr["ref_window_lag"])
    ln = int(rr["ref_window_len"])

    # 参照窗 = [t-lag-len+1, t-lag]，只看历史
    df["ref_breadth"] = df.groupby("sector_id", sort=False)["breadth"].transform(
        lambda s: s.shift(lag).rolling(ln, min_periods=ln).mean())
    df["ref_breadth_delta"] = df["breadth"] - df["ref_breadth"]

    ref = pd.to_numeric(df["ref_breadth"], errors="coerce")
    bd5 = pd.to_numeric(df["breadth_delta_5"], errors="coerce")
    bd3 = pd.to_numeric(df["breadth_delta_3"], errors="coerce")
    cd5 = pd.to_numeric(df["core_breadth_delta_5"], errors="coerce")
    cd3 = pd.to_numeric(df["core_breadth_delta_3"], errors="coerce")
    rt5 = pd.to_numeric(df["rs_turn_5"], errors="coerce")
    as5 = pd.to_numeric(df["amount_share_delta_5"], errors="coerce")

    in_cond = ((ref <= rr["in_ref_breadth_max"]) & (bd5 >= rr["in_breadth_delta_5_min"])
               & (cd5 >= rr["in_core_delta_5_min"]) & (rt5 >= rr["in_rs_turn_5_min"])
               & (as5 >= rr["in_amount_share_delta_5_min"]))
    out_cond = ((ref >= rr["out_ref_breadth_min"]) & (bd3 <= rr["out_breadth_delta_3_max"])
                & (cd3 <= rr["out_core_delta_3_max"]) & (rt5 <= rr["out_rs_turn_5_max"]))

    df["rotation_signal"] = np.select(
        [ref.isna().fillna(True), in_cond.fillna(False), out_cond.fillna(False)],
        ["ROTATION_NEUTRAL", "ROTATION_IN", "ROTATION_OUT"], default="ROTATION_NEUTRAL")

    reasons = []
    for i in range(len(df)):
        if df["rotation_signal"].iat[i] == "ROTATION_IN":
            reasons.append(
                f"参照窗 Breadth {s4(ref.iat[i])} 偏弱，近5日 Breadth {s4(bd5.iat[i])}、"
                f"Core {s4(cd5.iat[i])}、RS turn {s4(rt5.iat[i])} 同步改善")
        elif df["rotation_signal"].iat[i] == "ROTATION_OUT":
            reasons.append(
                f"参照窗 Breadth {s4(ref.iat[i])} 偏强，近3日 Breadth {s4(bd3.iat[i])}、"
                f"Core {s4(cd3.iat[i])}、RS turn {s4(rt5.iat[i])} 转弱")
        elif pd.isna(ref.iat[i]):
            reasons.append("参照窗数据未就绪")
        else:
            reasons.append("未观测到结构性流入/流出")
    df["rotation_reason"] = reasons
    return df


# ────────────────────────────────────────────────────────────────────────────
# Theme Phase + Hysteresis + 状态转换（需求 §十七 ~ §二十）
# ────────────────────────────────────────────────────────────────────────────

def _ladder_target(r, seos: float, warm: bool, psr: dict, thr: dict) -> int:
    if not warm:
        return 0
    lvl = 0
    if (seos >= thr["early"] and _gt(r.breadth_delta_5, 0) and _gt(r.core_breadth_delta_5, 0)
            and _ge(r.rs_turn_5, 0) and _lt(r.extension_penalty, psr["early_max_extension_penalty"])):
        lvl = 1
    if (lvl >= 1 and seos >= thr["emerging"] and _gt(r.breadth_delta_5, 0)
            and _gt(r.core_breadth_delta_5, 0) and _gt(r.rs_turn_5, 0)):
        lvl = 2
    if (lvl >= 2 and seos >= thr["confirming"]
            and _ge(r.improving_dims, psr["confirming_min_improving_dims"])):
        lvl = 3
    if (lvl >= 3 and seos >= thr["strong"]
            and _ge(r.breadth, psr["strong_breadth_min"])
            and _ge(r.core_breadth, psr["strong_core_breadth_min"])
            and _ge(r.relative_strength, psr["strong_vs_market_5_min"])
            and _ge(r.volume_ratio_5, psr["strong_volume_ratio_5_min"])
            and _le(r.top5_concentration, psr["strong_top5_concentration_max"])):
        lvl = 4
    return lvl


def _phase_conditions(r, cfg: dict) -> tuple:
    """三类覆盖态的当日结构条件（只依赖 <=D 数据）。"""
    psr = cfg["phase_structural_rules"]
    seos = _fv(r.seos_score)
    exiting = (_eq(r.sector_state_base, psr["exiting_state_required"])
               and _lt(seos, psr["exiting_seos_max"])
               and _lt(r.breadth_delta_5, 0) and _lt(r.breadth_delta_10, 0)
               and _lt(r.core_breadth_delta_5, 0))
    deter = (_le(r.theme_health_delta_5, psr["deteriorating_health_delta_5_max"])
             and _lt(r.breadth_delta_5, 0) and _lt(r.core_breadth_delta_5, 0))
    cool = (_ge(seos, psr["cooling_min_seos"])
            and (_lt(r.breadth_delta_3, 0) or _lt(r.core_breadth_delta_3, 0)
                 or _lt(r.rs_turn_5, 0)))
    return exiting, deter, cool


def _phase_transition(prev: str, origin, lvl_target: int, r, cfg: dict, invalid: bool,
                      st: dict):
    """状态机（需求 §十七 ~ §二十）。

    上升：梯级目标需【连续 confirm_days 日】高于当前级才升级（防抖 §二十）；
          梯级由嵌套结构条件定义（lvl=4 蕴含 lvl>=1/2/3），实际跳幅仍受 max_ladder_jump_up 限制。
    下降：SEOS 需【连续 confirm_days 日】跌破 phase_exit_thresholds（Hysteresis）才降级，
          且每次最多降一级。
    覆盖态：EXITING / DETERIORATING / COOLING 需【连续 confirm_days 日】条件成立才进入，
            条件【连续 confirm_days 日】解除才退出 —— 防止状态来回跳（需求 §二十）。
    """
    psr = cfg["phase_structural_rules"]
    ex = cfg["phase_exit_thresholds"]
    conf = int(psr["confirm_days"])
    step = int(psr["max_ladder_jump_up"])
    seos = _fv(r.seos_score)

    if invalid:
        return "DATA_INVALID", origin
    if prev == "DATA_INVALID":
        return ("DORMANT" if bool(r.warmup_ready) else "DATA_INVALID"), None

    exiting = st["exit"] >= conf
    deter = st["deter"] >= conf
    # COOLING 只允许从 STRONG / CONFIRMING 起步（需求 §十八：原为强/确认态后开始退潮）
    cool = (st["cool"] >= conf) and (prev in psr["cooling_from_phases"] or prev == "COOLING")
    # EXITING / DETERIORATING 是「由强转弱」语义，只能从已启动过的状态进入，
    # 避免从未启动的 DORMANT 直接跳到风险态（需求 §十八 / §十九）。
    risk_ok = prev in psr["risk_from_phases"]
    exiting = exiting and risk_ok
    deter = deter and risk_ok

    if prev == "EXITING":
        if st["exit_off"] >= conf:
            return "DORMANT", None
        return "EXITING", origin

    if prev == "DETERIORATING":
        if exiting:
            return "EXITING", origin
        if st["deter_off"] >= conf:
            return (LADDER[lvl_target] if lvl_target > 0 else "DORMANT"), None
        return "DETERIORATING", origin

    if prev == "COOLING":
        if exiting:
            return "EXITING", origin
        if deter:
            return "DETERIORATING", origin
        if st["cool_off"] >= conf:
            o = RANK.get(origin)
            if o is not None and lvl_target >= o:
                return origin, None
            return (LADDER[lvl_target] if lvl_target > 0 else "DORMANT"), None
        return "COOLING", origin

    r0 = RANK.get(prev)
    if r0 is None:
        r0 = 0
    if exiting:
        return "EXITING", origin
    if deter:
        return "DETERIORATING", origin
    if cool and r0 >= RANK["CONFIRMING"]:
        return "COOLING", prev

    if lvl_target > r0:
        # 升级需连续 confirm_days 日梯级目标高于当前级（防抖 §二十）
        if st["up"] >= conf:
            return LADDER[min(lvl_target, r0 + step)], None
        return LADDER[r0], None
    if lvl_target == r0:
        return LADDER[r0], None
    if r0 == 0:
        return "DORMANT", None
    if _lt(seos, ex.get(prev.lower(), 0.0)) and st["below"] >= conf:
        return LADDER[r0 - 1], None
    return LADDER[r0], None


PHASE_OUT_COLS = ["phase_raw", "theme_phase", "prev_phase", "phase_transition",
                  "phase_changed", "phase_change_date", "phase_duration", "signal_reason"]


def build_reason(r, flags: dict, warm: bool, invalid: bool) -> str:
    if invalid:
        return "数据质量不足，输出 DATA_INVALID"
    if not warm:
        return "滑动窗口未就绪（warmup），不参与启动判定"
    out = []
    bd5, cd5, rt5, as5, v5 = _fv(r.breadth_delta_5), _fv(r.core_breadth_delta_5), _fv(r.rs_turn_5), \
        _fv(r.amount_share_delta_5), _fv(r.volume_ratio_5)
    if _fv(getattr(r, "breadth_expansion_5", float("nan"))) == _fv(getattr(r, "breadth_expansion_5", float("nan"))):
        out.append(f"Breadth 扩张(5-10) {s4(r.breadth_expansion_5)}")
    if not math.isnan(bd5):
        out.append(f"Breadth 5D {bd5:+.2f}" + ("（扩散）" if flags.get("BREADTH_EXPANDING") else ""))
    if not math.isnan(cd5):
        out.append(f"Core Breadth 5D {cd5:+.2f}" + ("（核心扩散）" if flags.get("CORE_EXPANDING") else ""))
    if not math.isnan(rt5):
        out.append(f"RS turn 5D {rt5:+.3f}" + ("（转正）" if flags.get("RS_TURNING_UP") else ""))
    if not math.isnan(as5):
        out.append(f"主题成交额占比变化 5D {as5:+.4f}" + ("（提升）" if flags.get("AMOUNT_SHARE_EXPANDING") else ""))
    if not math.isnan(v5):
        out.append(f"量比 5D {s4(v5)}")
    out.append("Top5 集中度 " + s4(r.top5_concentration)
               + ("（正常）" if not flags.get("CONCENTRATION_HIGH") else "（偏高）"))
    out.append(f"extension_penalty {s4(r.extension_penalty)}")
    return "；".join(out)


def compute_phases(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    psr = cfg["phase_structural_rules"]
    thr = cfg["phase_thresholds"]
    ex_map = cfg["phase_exit_thresholds"]

    order = df.sort_values(["sector_id", "trade_date"], kind="mergesort").index
    sub = df.loc[order]
    recs = list(sub.itertuples(index=False))

    flag_arrays = {k: sub[k].to_numpy() for k in FLAG_NAMES}

    cols = {c: [] for c in PHASE_OUT_COLS}
    cur_sid = None
    phase, origin, chg_date, dur = "DORMANT", None, None, 0
    st = {k: 0 for k in STREAK_KEYS}

    for i, r in enumerate(recs):
        if r.sector_id != cur_sid:
            cur_sid = r.sector_id
            phase, origin, chg_date, dur = "DORMANT", None, None, 0
            st = {k: 0 for k in STREAK_KEYS}

        invalid = bool(r.data_invalid)
        warm = bool(r.warmup_ready)
        seos = _fv(r.seos_score)
        lvl = 0 if invalid else _ladder_target(r, seos, warm, psr, thr)
        raw = "DATA_INVALID" if invalid else (LADDER[lvl] if warm else "DORMANT")

        if invalid:
            st = {k: 0 for k in STREAK_KEYS}
        else:
            ex_c, de_c, co_c = _phase_conditions(r, cfg)
            for name, cond in (("exit", ex_c), ("deter", de_c), ("cool", co_c)):
                st[name] = st[name] + 1 if cond else 0
                st[name + "_off"] = 0 if cond else st[name + "_off"] + 1
            # 梯级升/降确认 streak（§二十 防抖）：以【转换前】状态为基准 ——
            # 升级需 lvl_target 连续 confirm_days 日高于当前级，
            # 降级需 SEOS 连续 confirm_days 日跌破当前级退出阈值。
            r0 = RANK.get(phase)
            up_c = (r0 is not None and warm and lvl > r0)
            below_c = False
            if r0 is not None and r0 > 0:
                below_c = _lt(seos, ex_map.get(phase.lower(), 0.0))
            st["up"] = st["up"] + 1 if up_c else 0
            st["below"] = st["below"] + 1 if below_c else 0

        new_phase, new_origin = _phase_transition(phase, origin, lvl, r, cfg, invalid, st)

        prev = phase
        changed = new_phase != prev
        if changed:
            chg_date = str(r.trade_date)
            dur = 1
        else:
            dur = dur + 1
        phase, origin = new_phase, new_origin

        flags = {k: bool(flag_arrays[k][i]) for k in FLAG_NAMES}
        cols["phase_raw"].append(raw)
        cols["theme_phase"].append(phase)
        cols["prev_phase"].append(prev)
        cols["phase_transition"].append(f"{prev}->{phase}" if changed else "")
        cols["phase_changed"].append(bool(changed))
        cols["phase_change_date"].append(chg_date or str(r.trade_date))
        cols["phase_duration"].append(int(dur))
        cols["signal_reason"].append(build_reason(r, flags, warm, invalid))

    out = pd.DataFrame(cols, index=sub.index)
    for c in PHASE_OUT_COLS:
        df[c] = out[c]
    return df


# ────────────────────────────────────────────────────────────────────────────
# Rotation Group（需求 §二十六 ~ §二十七）—— 只作辅助信息，不覆盖单主题状态
# ────────────────────────────────────────────────────────────────────────────

def compute_groups(df: pd.DataFrame, cfg: dict, group_names: dict) -> pd.DataFrame:
    gr = cfg["group_rules"]
    g = df.groupby(["trade_date", "rotation_group"], dropna=False)

    out = g.agg(
        group_theme_count=("sector_id", "size"),
        group_breadth=("breadth", "mean"),
        group_core_breadth=("core_breadth", "mean"),
        group_health=("theme_health", "mean"),
        group_seos=("seos_score", "mean"),
        group_amount_share=("theme_amount_share", "sum"),
    ).reset_index()
    out["group_positive_ratio"] = g["breadth"].apply(lambda s: float((s > 0).mean())).values
    out["group_phase_strong"] = g["theme_phase"].apply(lambda s: int((s == "STRONG").sum())).values
    out["group_phase_confirming"] = g["theme_phase"].apply(lambda s: int((s == "CONFIRMING").sum())).values
    out["group_phase_emerging"] = g["theme_phase"].apply(lambda s: int((s == "EMERGING").sum())).values
    out["group_phase_early"] = g["theme_phase"].apply(lambda s: int((s == "EARLY").sum())).values
    out["group_phase_cooling"] = g["theme_phase"].apply(lambda s: int((s == "COOLING").sum())).values
    out["group_phase_risk"] = g["theme_phase"].apply(
        lambda s: int(s.isin(["DETERIORATING", "EXITING"]).sum())).values

    out = out.sort_values(["rotation_group", "trade_date"], kind="mergesort").reset_index(drop=True)
    gg = out.groupby("rotation_group", sort=False)
    out["group_seos_delta_5"] = out["group_seos"] - gg["group_seos"].shift(5)
    out["group_breadth_delta_5"] = out["group_breadth"] - gg["group_breadth"].shift(5)
    out["group_health_delta_5"] = out["group_health"] - gg["group_health"].shift(5)
    out["group_name"] = out["rotation_group"].map(lambda x: group_names.get(x, ""))

    st = []
    for r in out.itertuples(index=False):
        seos = _fv(r.group_seos)
        br = _fv(r.group_breadth)
        d5 = _fv(r.group_seos_delta_5)
        if _lt(seos, gr["weakening_max_group_seos"]) and _lt(d5, gr["weakening_group_seos_delta_5_max"]):
            st.append(gr["state_weakening"])
        elif _ge(seos, gr["strong_min_group_seos"]) and _ge(br, gr["strong_min_group_breadth"]):
            st.append(gr["state_strong"])
        elif _ge(seos, gr["improving_min_group_seos"]):
            st.append(gr["state_improving"])
        else:
            st.append(gr["state_neutral"])
    out["group_rotation_state"] = st

    ordered = ["trade_date", "rotation_group", "group_name", "group_theme_count", "group_breadth",
               "group_breadth_delta_5", "group_core_breadth", "group_health", "group_health_delta_5",
               "group_seos", "group_seos_delta_5", "group_positive_ratio", "group_amount_share",
               "group_phase_strong", "group_phase_confirming", "group_phase_emerging",
               "group_phase_early", "group_phase_cooling", "group_phase_risk",
               "group_rotation_state"]
    out = out[ordered]
    return out.sort_values(["trade_date", "rotation_group"], kind="mergesort").reset_index(drop=True)


# ────────────────────────────────────────────────────────────────────────────
# 完整流水线（纯函数：给定面板 → 全部输出，便于无未来函数截断检验）
# ────────────────────────────────────────────────────────────────────────────

def compute_pipeline(panel: pd.DataFrame, cfg: dict, group_names: dict):
    df = compute_features(panel, cfg)
    df = compute_scores(df, cfg)
    df = compute_flags(df, cfg)
    df["core_pattern"] = [core_pattern_of(r) for r in df.itertuples(index=False)]
    df = compute_startup_quality(df, cfg)
    df = compute_divergence(df)
    df = compute_rotation(df, cfg)
    df = compute_phases(df, cfg)
    groups = compute_groups(df, cfg, group_names)
    return df, groups


# ────────────────────────────────────────────────────────────────────────────
# 输出列定义
# ────────────────────────────────────────────────────────────────────────────

SEOS_DAILY_COLS = [
    "trade_date", "sector_id", "sector_name", "rotation_group",
    "theme_health", "theme_health_delta_1", "theme_health_delta_3", "theme_health_delta_5",
    "theme_health_delta_10", "health_slope_5", "health_slope_10",
    "breadth", "breadth_delta_1", "breadth_delta_3", "breadth_delta_5", "breadth_delta_10",
    "breadth_expansion_3", "breadth_expansion_5", "breadth_expansion_10", "breadth_acceleration",
    "weighted_breadth", "weighted_breadth_delta_1", "weighted_breadth_delta_3", "weighted_breadth_delta_5",
    "core_breadth", "core_breadth_delta_1", "core_breadth_delta_3", "core_breadth_delta_5",
    "core_lead_breadth", "core_layer", "primary_breadth", "primary_breadth_delta_3", "secondary_breadth",
    "relative_strength", "relative_strength_delta_3", "relative_strength_delta_5",
    "relative_strength_delta_10", "rs_turn_3", "rs_turn_5", "rs_turn_10", "rs_fresh_cross",
    "volume_ratio_1", "volume_ratio_3", "volume_ratio_5", "volume_ratio_delta_3", "volume_ratio_delta_5",
    "theme_amount_share", "amount_share_delta_3", "amount_share_delta_5", "amount_share_delta_10",
    "ret_dispersion_5", "top5_concentration", "top10_concentration", "positive_contribution_ratio",
    "improving_dims", "improving_dim_ratio",
    "ew_ret_5", "ew_ret_10", "ew_ret_20",
    "seos_breadth_expansion", "seos_core_breadth", "seos_relative_strength",
    "seos_volume_participation", "seos_amount_share", "seos_health_momentum", "seos_consistency",
    "seos_raw", "extension_penalty", "seos_score",
    "core_pattern", "theme_phase", "phase_raw", "phase_transition", "prev_phase",
    "phase_changed", "phase_change_date", "phase_duration",
    "startup_quality", "rotation_signal", "signal_reason",
    "data_quality_score", "warmup_ready", "data_invalid", "sector_state_base",
]

FLAGS_COLS = (["trade_date", "sector_id", "sector_name", "rotation_group"]
              + FLAG_NAMES + ["flag_count", "positive_flag_count"])

DIVERGENCE_COLS = [
    "trade_date", "sector_id", "sector_name", "rotation_group",
    "PRICE_BREADTH_DIVERGENCE", "PRICE_CORE_DIVERGENCE", "RS_BREADTH_DIVERGENCE",
    "VOLUME_BREADTH_DIVERGENCE",
    "price_breadth_gap", "price_core_gap", "rs_breadth_gap", "volume_breadth_gap",
    "divergence_count", "divergence_level",
]

ROTATION_COLS = [
    "trade_date", "sector_id", "sector_name", "rotation_group",
    "ref_breadth", "ref_breadth_delta", "breadth", "breadth_delta_5", "core_breadth_delta_5",
    "rs_turn_5", "amount_share_delta_5", "rotation_signal", "rotation_reason",
]

PHASE_HISTORY_COLS = [
    "trade_date", "sector_id", "sector_name", "rotation_group",
    "prev_phase", "current_phase", "phase_raw", "phase_changed", "phase_change_date",
    "phase_duration", "phase_transition", "seos_score", "theme_health",
    "data_quality_score", "signal_reason",
]

GROUP_COLS = [
    "trade_date", "rotation_group", "group_name", "group_theme_count", "group_breadth",
    "group_breadth_delta_5", "group_core_breadth", "group_health", "group_health_delta_5",
    "group_seos", "group_seos_delta_5", "group_positive_ratio", "group_amount_share",
    "group_phase_strong", "group_phase_confirming", "group_phase_emerging",
    "group_phase_early", "group_phase_cooling", "group_phase_risk", "group_rotation_state",
]


def core_pattern_of(row) -> str:
    """§八 核心成员扩散三型：A 健康扩散 / B 边缘股行情 / C 早期改善。"""
    cd5 = _fv(row.core_breadth_delta_5)
    pd3 = _fv(row.primary_breadth_delta_3)
    sd = _fv(row.secondary_breadth)
    sdc = row.secondary_breadth  # 无 delta 列，用 level 与 core delta 组合判断
    if math.isnan(cd5) or math.isnan(pd3) or math.isnan(sd):
        return "INSUFFICIENT_DATA"
    if cd5 > 0 and pd3 > 0 and sd > 0.0:
        return "A_HEALTHY_DIFFUSION"
    if cd5 < 0 and sd > 0.0:
        return "B_EDGE_DRIVEN"
    if cd5 > 0 and sd <= 0.0:
        return "C_EARLY_CORE_ONLY"
    return "D_MIXED"


# ────────────────────────────────────────────────────────────────────────────
# Opportunity Pool（需求 §二十八 ~ §二十九）
# ────────────────────────────────────────────────────────────────────────────

def build_opportunity_pool(df: pd.DataFrame, cfg: dict, trade_date: str) -> dict:
    op = cfg["opportunity_pool"]
    today = df[df["trade_date"] == trade_date].copy()
    src = op["priority_source"]
    inverted = set(op.get("inverted_ramps", []))

    prio = pd.Series(0.0, index=today.index)
    wsum = 0.0
    parts = {}
    for pk, w in op["priority_weights"].items():
        col = src[pk]
        rk = col
        lo, hi = op["priority_ramps"][rk if rk in op["priority_ramps"] else pk]
        v = ramp_series(today[col], float(lo), float(hi))
        if pk in inverted:
            v = 100.0 - v
        v = v.fillna(float(cfg["data_rules"]["neutral_fill_score"]))
        parts[pk] = v.round(4)
        prio = prio + w * v
        wsum += w
    today["priority_score"] = (prio / wsum).round(4)
    for pk, v in parts.items():
        today[f"priority_{pk}"] = v

    buckets: dict = {}
    for phase, bucket in op["phase_to_bucket"].items():
        buckets.setdefault(bucket, [])
    buckets.setdefault("EXCLUDED", [])

    max_items = int(op["max_items_per_bucket"])
    records = []
    for r in today.sort_values("priority_score", ascending=False).itertuples(index=False):
        bucket = op["phase_to_bucket"].get(str(r.theme_phase))
        if bucket is None:
            buckets["EXCLUDED"].append(r.sector_id)
            continue
        flags = [f for f in FLAG_NAMES if bool(getattr(r, f))]
        rec = {
            "sector_id": r.sector_id,
            "sector_name": r.sector_name,
            "rotation_group": r.rotation_group,
            "theme_health": r4(r.theme_health),
            "theme_health_delta_5": r4(r.theme_health_delta_5),
            "seos_score": r4(r.seos_score),
            "priority_score": r4(r.priority_score),
            "theme_phase": r.theme_phase,
            "prev_phase": r.prev_phase,
            "phase_duration": int(r.phase_duration),
            "breadth": r4(r.breadth),
            "breadth_delta_5": r4(r.breadth_delta_5),
            "breadth_acceleration": r4(r.breadth_acceleration),
            "core_breadth": r4(r.core_breadth),
            "core_breadth_delta_5": r4(r.core_breadth_delta_5),
            "core_pattern": core_pattern_of(r),
            "relative_strength": r4(r.relative_strength),
            "rs_turn_5": r4(r.rs_turn_5),
            "volume_ratio_5": r4(r.volume_ratio_5),
            "amount_share_delta_5": r4(r.amount_share_delta_5),
            "top5_concentration": r4(r.top5_concentration),
            "extension_penalty": r4(r.extension_penalty),
            "startup_quality": r.startup_quality,
            "rotation_signal": r.rotation_signal,
            "signal_flags": flags,
            "reason": [s for s in str(r.signal_reason).split("；")],
            "data_quality_score": r4(r.data_quality_score),
        }
        buckets[bucket].append(rec)
        records.append((bucket, rec))

    # 分桶排序（EARLY/EMERGING 优先按加速与核心扩散，RISK 桶按风险降序）
    for b in buckets:
        if b == "EXCLUDED":
            continue
        rev = b in ("RISK_WATCH", "COOLING_WATCH")
        buckets[b] = sorted(
            buckets[b],
            key=lambda x: (-(x["priority_score"] or 0) if rev else (x["priority_score"] or 0)),
        )[:max_items]

    pool = {
        "version": cfg["version"],
        "layer": "STEP4_SEOS",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "trade_date": trade_date,
        "canonical_source": os.path.relpath(MASTER_PATH, BASE_DIR).replace("\\", "/"),
        "config_source": os.path.relpath(SEOS_CONFIG_PATH, BASE_DIR).replace("\\", "/"),
        "not_a_buy_list": True,
        "notice": ("theme_opportunity_pool 只表示值得进一步研究的【主题】，"
                   "不表示 BUY，不表示买入其中任何个股；个股层留到后续阶段。"),
        "priority_weights": op["priority_weights"],
        "sort_rule": "priority_score DESC（= Breadth 加速度 + 核心扩散 + RS 转正 + 低 extension + 低集中度）",
        "buckets": {k: v for k, v in buckets.items() if k != "EXCLUDED"},
        "bucket_counts": {k: len(v) for k, v in buckets.items() if k != "EXCLUDED"},
        "excluded_phases": sorted(set(
            str(p) for p in today["theme_phase"] if str(p) not in op["phase_to_bucket"])),
    }
    return pool


# ────────────────────────────────────────────────────────────────────────────
# 回测验证（需求 §三十）
# ────────────────────────────────────────────────────────────────────────────

def build_backtest(df: pd.DataFrame, cfg: dict) -> tuple:
    bt = cfg["backtest"]
    horizons = [int(h) for h in bt["horizons"]]
    seed = int(bt["random_seed"])
    draws = int(bt.get("random_draws", 500))

    d = df.sort_values(["sector_id", "trade_date"], kind="mergesort").copy()
    g = d.groupby("sector_id", sort=False)
    d["nav"] = g["ew_ret_1"].transform(lambda s: (1.0 + s.fillna(0.0)).cumprod())
    g2 = d.groupby("sector_id", sort=False)
    for h in horizons:
        nxt = g2["nav"].shift(-h)
        d[f"fwd_{h}"] = nxt / d["nav"] - 1.0

    # 事件 = 状态首次进入（去重叠）
    d["prev_rotation_signal"] = d.groupby("sector_id", sort=False)["rotation_signal"].shift(1)
    events = {}
    for sig in bt["signals"]:
        if sig == "ROTATION_IN":
            events[sig] = d[(d["rotation_signal"] == "ROTATION_IN")
                            & (d["prev_rotation_signal"] != "ROTATION_IN")]
        else:
            events[sig] = d[(d["theme_phase"] == sig) & (d["prev_phase"] != sig)]
    events["ALL_THEMES"] = d

    rng = random.Random(seed)
    rows = []
    base_stats = {}
    for h in horizons:
        pool = d[f"fwd_{h}"].dropna()
        base_stats[h] = (len(pool), float(pool.mean()), float(pool.median()),
                         float((pool > 0).mean()))
        arr = pool.to_numpy()

        for sig, ed in events.items():
            s = ed[f"fwd_{h}"].dropna()
            if len(s) == 0:
                continue
            n = len(s)
            if sig == "ALL_THEMES":
                rows.append({"signal": sig, "horizon": h, "n": n,
                             "mean": float(s.mean()), "median": float(s.median()),
                             "win_rate": float((s > 0).mean()),
                             "vs_all": 0.0, "vs_random": 0.0, "pctile": float("nan")})
                continue
            rand = []
            for _ in range(draws):
                k = min(n, len(arr))
                if k == 0:
                    break
                samp = [arr[rng.randrange(len(arr))] for _ in range(k)]
                rand.append(sum(samp) / k)
            rand_mean = float(np.mean(rand)) if rand else float("nan")
            pct = float(np.mean([1.0 if m <= s.mean() else 0.0 for m in rand])) if rand else float("nan")
            rows.append({"signal": sig, "horizon": h, "n": n,
                         "mean": float(s.mean()), "median": float(s.median()),
                         "win_rate": float((s > 0).mean()),
                         "vs_all": float(s.mean() - base_stats[h][1]),
                         "vs_random": float(s.mean() - rand_mean),
                         "pctile": pct,
                         "random_mean": rand_mean,
                         "all_mean": base_stats[h][1]})
    return pd.DataFrame(rows), d, base_stats


def render_backtest_md(btdf: pd.DataFrame, base_stats: dict, cfg: dict, trade_date: str) -> str:
    horizons = [int(h) for h in cfg["backtest"]["horizons"]]
    L = []
    A = L.append
    A("# SEOS 历史回测验证")
    A("")
    A(f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    A(f"- 数据截止：{trade_date}")
    A(f"- 主题层：STEP4_SEOS（消费 Step 3 产出；不涉及个股）")
    A(f"- 收益口径：主题等权 ew_ret_1 连乘净值，前向 h 交易日收益（nav[t+h]/nav[t]-1）")
    A(f"- 信号口径：状态/轮动信号【首次进入】当日（去重叠），随机基线 seed={cfg['backtest']['random_seed']}、"
      f"抽样 {cfg['backtest'].get('random_draws', 500)} 次")
    A("")
    A("> 声明：本回测仅用于验证「规则是否有效」，**结果不得用于反向修改阈值或权重**（需求 §三十一）。")
    A("> 规则在回测之前已固定于 config/seos_config.json。")
    A("")
    A("## 1. 基线")
    A("")
    A("| 持有期 | 样本数 | 均值 | 中位数 | 胜率 |")
    A("| --- | --- | --- | --- | --- |")
    for h in horizons:
        n, mean, med, win = base_stats.get(h, (0, float("nan"), float("nan"), float("nan")))
        A(f"| T+{h} | {n} | {mean*100:+.2f}% | {med*100:+.2f}% | {win*100:.1f}% |")
    A("")
    A("## 2. 信号表现")
    A("")
    A("| 信号 | 持有期 | 信号数 | 均值 | 中位数 | 胜率 | 超额(全样本) | 超额(随机) | 随机分位 |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for h in horizons:
        sub = btdf[btdf["horizon"] == h]
        for _, r in sub.iterrows():
            pct = r.get("pctile")
            pct_s = "—" if pct is None or (isinstance(pct, float) and math.isnan(pct)) else f"{pct*100:.1f}%"
            A(f"| {r['signal']} | T+{int(r['horizon'])} | {int(r['n'])} | {r['mean']*100:+.2f}% | "
              f"{r['median']*100:+.2f}% | {r['win_rate']*100:.1f}% | {r['vs_all']*100:+.2f}% | "
              f"{r['vs_random']*100:+.2f}% | {pct_s} |")
    A("")
    A("## 3. 逐信号汇总（跨持有期）")
    A("")
    for sig in cfg["backtest"]["signals"]:
        sub = btdf[btdf["signal"] == sig]
        if sub.empty:
            A(f"### {sig}")
            A("")
            A("- 样本期内未出现该信号。")
            A("")
            continue
        A(f"### {sig}")
        A("")
        A(f"- 信号次数：{int(sub['n'].iloc[0])}")
        for _, r in sub.iterrows():
            A(f"- T+{int(r['horizon'])}：均值 {r['mean']*100:+.2f}%，"
              f"胜率 {r['win_rate']*100:.1f}%，相对全样本 {r['vs_all']*100:+.2f}%，"
              f"相对随机 {r['vs_random']*100:+.2f}%")
        A("")
    A("## 4. 说明与限制")
    A("")
    A(f"- 样本区间仅 {base_stats.get(horizons[0], (0,))[0]} 条 (主题×交易日) 观测，"
      f"且同主题相邻交易日信号高度相关，统计显著性有限。")
    A("- T+20 需要 20 个交易日前瞻，样本末端自动截断。")
    A("- 本阶段不因回测结果调整任何参数；若需优化，必须划分训练/验证/测试期后另立阶段。")
    A("")
    return "\n".join(L)


# ────────────────────────────────────────────────────────────────────────────
# Today JSON（需求 §三十六 + §四十三 下一阶段预留接口）
# ────────────────────────────────────────────────────────────────────────────

def build_today_json(df: pd.DataFrame, groups: pd.DataFrame, trade_date: str,
                     cfg: dict, state_today: dict) -> dict:
    today = df[df["trade_date"] == trade_date].copy()
    today = today.sort_values("seos_score", ascending=False, na_position="last")

    themes = []
    for r in today.itertuples(index=False):
        flags = [f for f in FLAG_NAMES if bool(getattr(r, f))]
        themes.append({
            "sector_id": r.sector_id,
            "sector_name": r.sector_name,
            "rotation_group": r.rotation_group,
            "theme_health": r4(r.theme_health),
            "theme_health_delta_5": r4(r.theme_health_delta_5),
            "seos_score": r4(r.seos_score),
            "breadth": r4(r.breadth),
            "breadth_delta_5": r4(r.breadth_delta_5),
            "breadth_expansion_5": r4(r.breadth_expansion_5),
            "breadth_acceleration": r4(r.breadth_acceleration),
            "core_breadth": r4(r.core_breadth),
            "core_breadth_delta_5": r4(r.core_breadth_delta_5),
            "core_pattern": r.core_pattern,
            "relative_strength": r4(r.relative_strength),
            "rs_turn_5": r4(r.rs_turn_5),
            "volume_ratio_5": r4(r.volume_ratio_5),
            "amount_share_delta_5": r4(r.amount_share_delta_5),
            "top5_concentration": r4(r.top5_concentration),
            "extension_penalty": r4(r.extension_penalty),
            "theme_phase": r.theme_phase,
            "prev_phase": r.prev_phase,
            "phase_changed": bool(r.phase_changed),
            "phase_duration": int(r.phase_duration),
            "phase_transition": r.phase_transition,
            "startup_quality": r.startup_quality,
            "rotation_signal": r.rotation_signal,
            "rotation_reason": r.rotation_reason,
            "signal_flags": flags,
            "signal_reason": r.signal_reason,
            "reason": [s for s in str(r.signal_reason).split("；")],
            "data_quality_score": r4(r.data_quality_score),
            "sector_state_base": r.sector_state_base,
        })

    phase_counts = {p: int((today["theme_phase"] == p).sum()) for p in ALL_PHASES}
    rotation_counts = {k: int((today["rotation_signal"] == k).sum())
                       for k in ("ROTATION_IN", "ROTATION_OUT", "ROTATION_NEUTRAL")}
    flag_counts = {f: int(today[f].sum()) for f in FLAG_NAMES}
    group_state_counts = {k: int((groups[groups["trade_date"] == trade_date]["group_rotation_state"] == k).sum())
                          for k in ("STRONG", "IMPROVING", "NEUTRAL", "WEAKENING")}

    gtoday = groups[groups["trade_date"] == trade_date]
    group_records = [{
        "rotation_group": r.rotation_group,
        "group_name": r.group_name,
        "group_theme_count": int(r.group_theme_count),
        "group_breadth": r4(r.group_breadth),
        "group_health": r4(r.group_health),
        "group_seos": r4(r.group_seos),
        "group_seos_delta_5": r4(r.group_seos_delta_5),
        "group_positive_ratio": r4(r.group_positive_ratio),
        "group_amount_share": r4(r.group_amount_share),
        "group_rotation_state": r.group_rotation_state,
    } for r in gtoday.itertuples(index=False)]

    return {
        "version": cfg["version"],
        "layer": "STEP4_SEOS",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "trade_date": trade_date,
        "canonical_source": os.path.relpath(MASTER_PATH, BASE_DIR).replace("\\", "/"),
        "config_source": os.path.relpath(SEOS_CONFIG_PATH, BASE_DIR).replace("\\", "/"),
        "inputs": {
            "sector_daily_stats": os.path.relpath(IN_DAILY, BASE_DIR).replace("\\", "/"),
            "sector_breadth": os.path.relpath(IN_BREADTH, BASE_DIR).replace("\\", "/"),
            "sector_strength": os.path.relpath(IN_STRENGTH, BASE_DIR).replace("\\", "/"),
            "sector_volume": os.path.relpath(IN_VOLUME, BASE_DIR).replace("\\", "/"),
            "sector_state_history": os.path.relpath(IN_STATE_HISTORY, BASE_DIR).replace("\\", "/"),
            "sector_quality_daily": os.path.relpath(IN_QUALITY, BASE_DIR).replace("\\", "/"),
            "membership_as_of": state_today.get("membership_as_of"),
            "membership_conf_min": state_today.get("membership_conf_min"),
        },
        "benchmark": state_today.get("benchmark"),
        "not_a_buy_list": True,
        "notice": ("本文件只描述【主题】层的状态与结构变化，不输出个股、不构成 BUY / NO TRADE、"
                   "不含仓位与买卖点。SEOS ≠ 追涨分。"),
        "phase_counts": phase_counts,
        "rotation_counts": rotation_counts,
        "flag_counts": flag_counts,
        "group_state_counts": group_state_counts,
        "seos_distribution": {
            "count_valid": int(pd.to_numeric(today["seos_score"], errors="coerce").notna().sum()),
            "mean": r4(pd.to_numeric(today["seos_score"], errors="coerce").mean()),
            "median": r4(pd.to_numeric(today["seos_score"], errors="coerce").median()),
            "p90": r4(pd.to_numeric(today["seos_score"], errors="coerce").quantile(0.90)),
        },
        "groups": group_records,
        "sectors": themes,
    }


# ────────────────────────────────────────────────────────────────────────────
# 验证（需求 §四十四）
# ────────────────────────────────────────────────────────────────────────────

LOOKAHEAD_COLS_SEOS = [
    "breadth_delta_5", "breadth_expansion_5", "breadth_expansion_10", "breadth_acceleration",
    "core_breadth_delta_5", "core_lead_breadth", "rs_turn_5", "rs_turn_10", "rs_fresh_cross",
    "volume_ratio_delta_5", "amount_share_delta_3", "amount_share_delta_5", "amount_share_delta_10",
    "theme_health_delta_3", "theme_health_delta_5", "theme_health_delta_10",
    "improving_dims", "improving_dim_ratio",
    "seos_breadth_expansion", "seos_core_breadth", "seos_relative_strength",
    "seos_volume_participation", "seos_amount_share", "seos_health_momentum", "seos_consistency",
    "seos_raw", "extension_penalty", "seos_score", "flag_count", "positive_flag_count",
]
LOOKAHEAD_COLS_PHASE = [
    "theme_phase", "phase_raw", "prev_phase", "phase_duration", "phase_change_date",
    "phase_transition", "signal_reason",
]
LOOKAHEAD_COLS_ROTATION = ["ref_breadth", "ref_breadth_delta", "rotation_signal"]


def _truncation_check(panel: pd.DataFrame, full: pd.DataFrame, cfg: dict, group_names: dict,
                      dates: list, cols: list, tol: float):
    """物理截断重算：只用 <=D 的数据重跑全链路，逐列比对 D 日数值。"""
    worst = 0.0
    worst_at = ""
    n_cmp = 0
    for d in dates:
        sub_panel = panel[panel["trade_date"] <= d]
        sub, _ = compute_pipeline(sub_panel, cfg, group_names)
        a = full[full["trade_date"] == d].set_index("sector_id")[cols]
        b = sub[sub["trade_date"] == d].set_index("sector_id")[cols]
        b = b.reindex(a.index)
        for c in cols:
            if not pd.api.types.is_numeric_dtype(a[c]):
                mask = (a[c].astype(str) != b[c].astype(str))
                diff = int(mask.sum())
                if diff > 0:
                    return False, f"{d}/{c} 文本不一致 {diff} 行", 1.0
                n_cmp += len(a)
                continue
            diff = (a[c].astype(float) - b[c].astype(float)).abs()
            m = float(np.nanmax(diff.values)) if len(diff) else 0.0
            if m == m and m > worst:
                worst = m
                worst_at = f"{d}/{c}"
            n_cmp += int(diff.notna().sum())
    ok = worst <= tol
    detail = (f"比对 {len(dates)} 个截断日 × {len(cols)} 列 / {n_cmp} 个数值，"
              f"最大偏差 {worst:.3e}" + (f" @ {worst_at}" if worst_at else ""))
    return ok, detail, worst


def run_validation(panel: pd.DataFrame, df: pd.DataFrame, groups: pd.DataFrame, cfg: dict,
                   master: SectorMaster, group_names: dict, rows: list) -> pd.DataFrame:
    checks = []

    def add(cid, ok, detail):
        checks.append({"check": cid, "status": "PASS" if ok else "FAIL", "detail": detail})

    # 1) 唯一主题定义来源
    ids_master = set(master.sector_ids)
    ids_out = set(df["sector_id"].astype(str))
    add("CHECK_MASTER_SOURCE",
        len(ids_master) == 50 and ids_out.issubset(ids_master),
        f"sector_master.json v{master.version} / {len(ids_master)} 主题；"
        f"SEOS 输出 {len(ids_out)} 个主题，全部合法" if ids_out.issubset(ids_master)
        else f"存在非法主题 id：{sorted(ids_out - ids_master)}")

    # 2) Legacy config 未参与
    reads = [os.path.basename(p) for p in READ_FILES]
    legacy_hit = [n for n in LEGACY_NAMES if n in reads]
    scanned = scan_project()
    add("CHECK_LEGACY_ISOLATION",
        not legacy_hit,
        f"本次运行读取 {len(READ_FILES)} 个文件，未包含任何 legacy 配置；"
        f"磁盘上仍存在 legacy 文件（仅作参考，未被读取）：{scanned.get('legacy_found') or '无'}"
        if not legacy_hit else f"检测到 legacy 配置被读取：{legacy_hit}")

    # 3) theme_id 全合法 + 无空
    null_ids = int(df["sector_id"].isna().sum())
    add("CHECK_THEME_ID_VALID",
        (ids_out == ids_master) and null_ids == 0,
        f"主题 id 覆盖 {len(ids_out)}/{len(ids_master)}，空值 {null_ids} 个；"
        f"缺失：{sorted(ids_master - ids_out) or '无'}")

    # 4) membership 无未来泄漏
    memb = _read_csv(IN_MEMBERSHIP, dtype={"trade_date": str, "effective_date": str})
    eff = pd.to_datetime(memb["effective_date"], errors="coerce")
    max_td = pd.to_datetime(df["trade_date"].max(), format="%Y%m%d")
    n_future = int((eff > max_td).sum())
    as_of = None
    try:
        as_of = load_json(IN_STATE_TODAY).get("membership_as_of")
    except Exception:
        pass
    as_of_ok = True
    if as_of:
        try:
            as_of_ok = pd.to_datetime(str(as_of), format="%Y%m%d") <= max_td
        except Exception:
            as_of_ok = False
    add("CHECK_MEMBERSHIP_NO_LEAK",
        n_future == 0 and as_of_ok,
        f"membership effective_date 最大 {eff.max().date() if eff.notna().any() else 'NA'}，"
        f"超出面板末日 {n_future} 行；membership_as_of={as_of}（<= 面板末日：{as_of_ok}）；"
        f"SEOS 不重建归属，直接沿用 Step 3 快照")

    # 5~7) 未来函数：物理截断重算
    tol = float(cfg["data_rules"]["lookahead_tolerance"])
    n_dates = int(cfg["data_rules"]["lookahead_check_dates"])
    all_dates = sorted(df["trade_date"].unique())
    cand = [d for d in all_dates if d not in all_dates[:22]]
    if len(cand) <= n_dates:
        sample = cand
    else:
        step = max(1, len(cand) // n_dates)
        sample = cand[::step][:n_dates]
        if all_dates[-1] not in sample:
            sample[-1] = all_dates[-1]
    sample = sorted(set(sample))

    ok, detail, _ = _truncation_check(panel, df, cfg, group_names, sample,
                                      LOOKAHEAD_COLS_SEOS, tol)
    add("CHECK_NO_LOOKAHEAD_SEOS", ok, detail)
    ok, detail, _ = _truncation_check(panel, df, cfg, group_names, sample,
                                      LOOKAHEAD_COLS_PHASE, tol)
    add("CHECK_NO_LOOKAHEAD_PHASE", ok, detail)
    ok, detail, _ = _truncation_check(panel, df, cfg, group_names, sample,
                                      LOOKAHEAD_COLS_ROTATION, tol)
    add("CHECK_NO_LOOKAHEAD_ROTATION", ok, detail)

    # 8) 指标可回溯（评分 = 权重×分项 的独立复算 + 特征列齐备）
    w = cfg["score_weights"]
    recon = sum(float(w[c]) * df[f"seos_{c}"] for c in SEOS_COMPONENTS)
    max_ded = float(cfg["extension_penalty"]["max_deduction"])
    expect = recon * (1.0 - np.minimum(df["extension_penalty"], max_ded))
    valid = df["seos_score"].notna()
    dev = float((df.loc[valid, "seos_score"] - expect[valid]).abs().max()) if valid.any() else 0.0
    missing_cols = [c for c in SEOS_DAILY_COLS if c not in df.columns]
    nan_components = int((valid & df[[f"seos_{c}" for c in SEOS_COMPONENTS]].isna().any(axis=1)).sum())
    add("CHECK_INDICATOR_TRACEABLE",
        dev <= 1e-9 and not missing_cols and nan_components == 0,
        f"评分独立复算最大偏差 {dev:.3e}；输出列缺失 {len(missing_cols)} 个{missing_cols}；"
        f"有效行分项缺失 {nan_components} 行")

    # 9) 状态转换无异常跳变
    chg = df[df["phase_changed"]]
    bad = []
    for r in chg.itertuples(index=False):
        allowed = ALLOWED_TRANSITIONS.get(r.prev_phase, set())
        if r.theme_phase not in allowed:
            bad.append(f"{r.trade_date}/{r.sector_id}:{r.prev_phase}->{r.theme_phase}")
    add("CHECK_NO_PHASE_JUMP", len(bad) == 0,
        f"状态转换 {len(chg)} 次，非法跳变 {len(bad)} 次"
        + (f"，示例：{bad[:5]}" if bad else "（全部落在白名单转移集合内）"))

    # 10) Hysteresis 正常
    ex_thr = cfg["phase_exit_thresholds"]
    viol = []
    per_sec = df.groupby("sector_id", sort=False)
    osc = 0
    total_chg = 0
    win = int(cfg["data_rules"]["oscillation_window"])
    for sid, sub in per_sec:
        seq = sub.sort_values("trade_date")
        phases = seq["theme_phase"].tolist()
        dates = seq["trade_date"].tolist()
        seos = pd.to_numeric(seq["seos_score"], errors="coerce").tolist()
        last_exit = {}  # 状态 -> 最近一次离开该状态的下标
        for i, p in enumerate(phases):
            if i > 0 and p != phases[i - 1]:
                total_chg += 1
                prev = phases[i - 1]
                if prev in RANK and p in RANK and RANK[p] < RANK[prev]:
                    lim = float(ex_thr.get(prev.lower(), 0.0))
                    s = seos[i]
                    if s == s and s >= lim:
                        viol.append(f"{dates[i]}/{sid}:{prev}->{p} SEOS={s:.2f}>=exit{lim}")
                last_exit[prev] = i
                # 抖动定义：离开状态 p 后在 win 日内又回到 p（A→…→A 往返）
                j = last_exit.get(p)
                if j is not None and (i - j) <= win:
                    osc += 1
    rate = (osc / total_chg) if total_chg else 0.0
    max_rate = float(cfg["data_rules"]["max_oscillation_rate"])
    add("CHECK_HYSTERESIS",
        len(viol) == 0 and rate <= max_rate,
        f"降级必须低于退出阈值：违约 {len(viol)} 次{('，示例 ' + str(viol[:3])) if viol else ''}；"
        f"{win} 日内回转（抖动）{osc}/{total_chg} = {rate*100:.1f}%（上限 {max_rate*100:.0f}%）")

    # 11) 数据缺失正确处理
    req = list(cfg["data_rules"]["warmup_required_deltas"])
    warm_false = ~df["warmup_ready"]
    bad_warm = int((warm_false & ~df["theme_phase"].isin(["DORMANT", "DATA_INVALID"])).sum())
    warm_scope = int((warm_false & ~df["data_invalid"]).sum())
    add("CHECK_MISSING_DATA_HANDLED",
        bad_warm == 0,
        f"滑动窗口未就绪 {warm_scope} 行（{','.join(req)} 缺值），全部落于 DORMANT/DATA_INVALID；"
        f"越界 {bad_warm} 行；未就绪行的分项按 neutral_fill_score 中性填充，不产生启动信号")

    # 12) DATA_INVALID 正确传播（合成注入）
    last = df["trade_date"].max()
    inj = panel.copy()
    m = inj["trade_date"] == last
    dq_thr = float(cfg["data_rules"]["data_quality_invalid_threshold"])
    inj.loc[m, "data_quality_score"] = max(0.0, dq_thr - 0.30)
    inj_df, _ = compute_pipeline(inj, cfg, group_names)
    dt = inj_df[inj_df["trade_date"] == last]
    ok_inj = bool((dt["theme_phase"] == "DATA_INVALID").all()) and bool(dt["seos_score"].isna().all())
    add("CHECK_DATA_INVALID_PROPAGATION",
        ok_inj and int(df[df["data_invalid"]].shape[0]) == 0,
        f"真实数据 DATA_INVALID 行 {int(df['data_invalid'].sum())}；"
        f"注入 dq<{dq_thr} 后末日 {len(dt)} 个主题全部为 DATA_INVALID 且 seos_score 为空：{ok_inj}；"
        f"flags 全部置 False：{bool((dt[FLAG_NAMES].sum().sum()) == 0)}")

    out = pd.DataFrame(checks)
    return out


# ────────────────────────────────────────────────────────────────────────────
# 审计报告（需求 §三十三）
# ────────────────────────────────────────────────────────────────────────────

def build_audit(df, groups, cfg, master, group_names, val, trade_date, state_today) -> str:
    L = []
    A = L.append
    A("# SEOS 审计报告（第四步）")
    A("")
    A(f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    A(f"- 数据截止：{trade_date}")
    A(f"- 配置：config/seos_config.json v{cfg['version']}")
    A(f"- 主题定义唯一来源：sector_master.json v{master.version}（= 需求文档的 theme_master.json）")
    A("")

    A("## 1. 继承关系与边界")
    A("")
    A("```")
    A("sector_master.json  →  theme_mapping  →  theme_membership")
    A("        →  Theme Daily Statistics  →  Theme Health")
    A("        →  SEOS  →  Signal Flags / Theme Phase / Rotation Signal")
    A("        →  Opportunity Pool  →  下一阶段（个股层，本阶段不进入）")
    A("```")
    A("")
    A("- SEOS 只消费 Step 3 产出，不重新定义行业分类、概念分类、股票主题归属、"
      "core / primary company、keyword mapping。")
    A("- 输出对象只有主题（THEME），不含任何个股信号。")
    A("- 明确不进入：HVT / IGE / F120 / 个股评分 / 个股 BUY / NO TRADE / 交易执行 / "
      "仓位建议 / 个股止损 / 个股买点。")
    A("")

    A("## 2. 术语映射（需求 ↔ 本项目）")
    A("")
    A("| 需求文档 | 本项目 |")
    A("| --- | --- |")
    A("| theme_master.json | sector_master.json（项目根目录，非 config/） |")
    A("| theme_id / theme_name | sector_id / sector_name |")
    A("| theme_seos.py | sector_seos_build.py |")
    A("| data/theme_seos_daily.csv | data/sector_seos_daily.csv |")
    A("| data/theme_signal_flags.csv | data/sector_signal_flags.csv |")
    A("| data/theme_divergence.csv | data/sector_divergence.csv |")
    A("| data/theme_rotation.csv | data/sector_rotation.csv |")
    A("| data/group_seos_daily.csv | data/sector_group_seos_daily.csv |")
    A("| data/theme_phase_history.csv | data/sector_phase_history.csv |")
    A("| output/theme_seos_today.json | output/sector_seos_today.json |")
    A("| output/theme_opportunity_pool.json | output/sector_opportunity_pool.json |")
    A("| config/seos_config.json | config/seos_config.json（同名保留） |")
    A("| output/seos_backtest.md / seos_audit.md | 同名保留 |")
    A("")
    A("字段语义一一对应，未改变任何定义。")
    A("")

    A("## 3. 输入契约与覆盖率")
    A("")
    A(f"- 输入：sector_daily_stats.csv / sector_breadth.csv / sector_strength.csv / "
      f"sector_volume.csv / sector_state_history.csv / sector_quality_daily.csv / "
      f"sector_state_today.json（Step 3 产出）")
    A(f"- 面板：{len(df)} 行 = {df['sector_id'].nunique()} 主题 × {df['trade_date'].nunique()} 交易日"
      f"（{df['trade_date'].min()} ~ {df['trade_date'].max()}）")
    dq_ok = df["data_quality_score"].notna().mean()
    A(f"- data_quality_score 覆盖率 {dq_ok*100:.2f}%；DATA_INVALID 行 {int(df['data_invalid'].sum())}")
    A(f"- refresh 覆盖：增量重算 {len(df[df['trade_date'] == trade_date])} 行（{trade_date}）")
    A(f"- membership_as_of：{state_today.get('membership_as_of')}；"
      f"membership_conf_min：{state_today.get('membership_conf_min')}")
    A(f"- 基准：{state_today.get('benchmark')}")
    A("")

    A("## 4. SEOS 特征与权重")
    A("")
    A("| 支柱 | 权重 | 子项 | 子权重 |")
    A("| --- | --- | --- | --- |")
    for c in SEOS_COMPONENTS:
        sw = cfg["score_weights"][c]
        subs = "、".join(f"{k} {v}" for k, v in cfg["component_weights"][c].items())
        A(f"| {c} | {sw:.2f} | {subs} | — |")
    A("")
    A(f"- 一致性维度（improving_dim_ratio）由 {len(IMPROVING_DIMS)} 个同步改善维度构成："
      + "、".join(IMPROVING_DIMS))
    A(f"- extension_penalty 权重："
      + "、".join(f"{k} {v}" for k, v in cfg["extension_penalty"]["weights"].items())
      + f"；最大扣减 {cfg['extension_penalty']['max_deduction']}")
    A("- ramp 区间按各特征在全样本上的无条件分布做单调尺度归一（仅缩放，不做收益择优、不搜索阈值）。")
    A("")

    A("## 5. Phase 分布与状态转换")
    A("")
    A("| 阶段 | 末日数量 | 窗口内出现次数 |")
    A("| --- | --- | --- |")
    tdy = df[df["trade_date"] == trade_date]
    for p in ALL_PHASES:
        A(f"| {p} | {int((tdy['theme_phase'] == p).sum())} | {int((df['theme_phase'] == p).sum())} |")
    A("")
    chg = df[df["phase_changed"]]
    A(f"- 窗口内状态转换 {len(chg)} 次")
    top = chg["phase_transition"].value_counts().head(12)
    A("")
    A("| 转换 | 次数 |")
    A("| --- | --- |")
    for k, v in top.items():
        A(f"| {k} | {int(v)} |")
    A("")
    psr_txt = cfg["phase_structural_rules"]
    A("Hysteresis：进入用 phase_thresholds（EARLY 55 / EMERGING 65 / CONFIRMING 72 / STRONG 75），"
      "退出（降级）用 phase_exit_thresholds（EARLY 48 / EMERGING 58 / CONFIRMING 65 / STRONG 70）；"
      f"梯级升级需目标级连续 {psr_txt['confirm_days']} 日高于当前级、降级需 SEOS 连续 "
      f"{psr_txt['confirm_days']} 日跌破退出阈值；"
      f"覆盖态（COOLING / DETERIORATING / EXITING）需连续 {psr_txt['confirm_days']} 日条件成立才进入，"
      "需连续同日数解除才退出；DETERIORATING / EXITING 仅允许从已启动状态"
      f"（{'/'.join(psr_txt['risk_from_phases'])}）进入。")
    A("")

    A("## 6. Signal Flags 统计")
    A("")
    A("| Flag | 窗口内次数 | 末日数量 |")
    A("| --- | --- | --- |")
    for f in FLAG_NAMES:
        A(f"| {f} | {int(df[f].sum())} | {int(tdy[f].sum())} |")
    A("")

    A("## 7. startup_quality 结构类型")
    A("")
    A("| 结构类型 | 窗口内 | 末日 |")
    A("| --- | --- | --- |")
    for q in sorted(df["startup_quality"].dropna().unique()):
        A(f"| {q} | {int((df['startup_quality'] == q).sum())} | {int((tdy['startup_quality'] == q).sum())} |")
    A("")
    A("| 核心扩散形态（core_pattern） | 窗口内 | 末日 |")
    A("| --- | --- | --- |")
    for q in sorted(df["core_pattern"].dropna().unique()):
        A(f"| {q} | {int((df['core_pattern'] == q).sum())} | {int((tdy['core_pattern'] == q).sum())} |")
    A("")

    A("## 8. Divergence 与 Rotation")
    A("")
    A("| 背离类型 | 窗口内 | 末日 |")
    A("| --- | --- | --- |")
    for c in ["PRICE_BREADTH_DIVERGENCE", "PRICE_CORE_DIVERGENCE", "RS_BREADTH_DIVERGENCE",
              "VOLUME_BREADTH_DIVERGENCE"]:
        A(f"| {c} | {int(df[c].sum())} | {int(tdy[c].sum())} |")
    A("")
    A("| rotation_signal | 窗口内 | 末日 |")
    A("| --- | --- | --- |")
    for s in ["ROTATION_IN", "ROTATION_OUT", "ROTATION_NEUTRAL"]:
        A(f"| {s} | {int((df['rotation_signal'] == s).sum())} | {int((tdy['rotation_signal'] == s).sum())} |")
    A("")
    A("> ROTATION_IN / ROTATION_OUT 只描述【已观测到】的结构变化，不预测未来轮动。")
    A("")

    A("## 9. Rotation Group（辅助信息，不覆盖单主题状态）")
    A("")
    A("| Group | 名称 | 主题数 | Breadth | Health | SEOS | SEOS 5D变化 | 状态 |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in groups[groups["trade_date"] == trade_date].itertuples(index=False):
        A(f"| {r.rotation_group} | {r.group_name} | {int(r.group_theme_count)} | {s4(r.group_breadth)} | "
          f"{s4(r.group_health)} | {s4(r.group_seos)} | {s4(r.group_seos_delta_5)} | {r.group_rotation_state} |")
    A("")

    A("## 10. 验证结果")
    A("")
    A("| 检查项 | 结果 | 说明 |")
    A("| --- | --- | --- |")
    for r in val.itertuples(index=False):
        A(f"| {r.check} | {r.status} | {r.detail} |")
    A("")
    A(f"- 合计 {int((val['status'] == 'PASS').sum())}/{len(val)} 项通过。")
    A("")

    A("## 11. 未来函数与 Legacy 污染检查")
    A("")
    A("- 未来函数：以【物理截断重算】为准 —— 只用 <=D 的数据重跑整条链路，"
      "逐列比对 D 日数值（见 CHECK_NO_LOOKAHEAD_SEOS / PHASE / ROTATION）。")
    A("- Legacy 污染：本程序读取的文件清单在运行期被记录，"
      "theme_config.json / subtheme_map.json / theme.json 未出现在清单中。")
    A("")

    A("## 12. 停止边界")
    A("")
    A("第四步到此停止。未实现、且本阶段不实现：Step 5 个股 Theme→Stock Candidate、"
      "Step 6 HVT / IGE / F120、Step 7 Execution、Step 8 BUY / NO TRADE。")
    A("")
    return "\n".join(L)


# ────────────────────────────────────────────────────────────────────────────
# 主流程
# ────────────────────────────────────────────────────────────────────────────

def resolve_dates(df: pd.DataFrame, args) -> list:
    dates = sorted(df["trade_date"].unique())
    if args.date:
        if args.date not in dates:
            raise SystemExit(f"指定日期 {args.date} 不在面板中")
        return [args.date]
    return dates


def write_outputs(df: pd.DataFrame, groups: pd.DataFrame, cfg: dict, trade_date: str) -> None:
    d = df.copy()
    d["current_phase"] = d["theme_phase"]

    daily = d.reindex(columns=[c for c in SEOS_DAILY_COLS if c in d.columns])
    missing = [c for c in SEOS_DAILY_COLS if c not in d.columns]
    if missing:
        log.warning("SEOS_DAILY_COLS 缺失列（已跳过）：%s", missing)
    for c in daily.columns:
        if c not in ("trade_date", "sector_id", "sector_name", "rotation_group", "core_layer",
                     "core_pattern", "theme_phase", "phase_raw", "phase_transition", "prev_phase",
                     "phase_change_date", "startup_quality", "rotation_signal", "signal_reason",
                     "sector_state_base"):
            daily[c] = pd.to_numeric(daily[c], errors="coerce").round(6)
    daily = daily.sort_values(["trade_date", "sector_id"], kind="mergesort").reset_index(drop=True)
    write_merge_csv(daily, OUT_SEOS_DAILY)

    flags = d.reindex(columns=[c for c in FLAGS_COLS if c in d.columns]).copy()
    flags = flags.sort_values(["trade_date", "sector_id"], kind="mergesort").reset_index(drop=True)
    write_merge_csv(flags, OUT_FLAGS)

    div = d.reindex(columns=[c for c in DIVERGENCE_COLS if c in d.columns]).copy()
    for c in DIVERGENCE_COLS:
        if c.startswith(("PRICE_", "RS_", "VOLUME_")):
            div[c] = div[c].astype(bool)
    for c in ("price_breadth_gap", "price_core_gap", "rs_breadth_gap", "volume_breadth_gap"):
        div[c] = pd.to_numeric(div[c], errors="coerce").round(6)
    div = div.sort_values(["trade_date", "sector_id"], kind="mergesort").reset_index(drop=True)
    write_merge_csv(div, OUT_DIVERGENCE)

    rot = d.reindex(columns=[c for c in ROTATION_COLS if c in d.columns]).copy()
    for c in ("ref_breadth", "ref_breadth_delta", "breadth", "breadth_delta_5",
              "core_breadth_delta_5", "rs_turn_5", "amount_share_delta_5"):
        rot[c] = pd.to_numeric(rot[c], errors="coerce").round(6)
    rot = rot.sort_values(["trade_date", "sector_id"], kind="mergesort").reset_index(drop=True)
    write_merge_csv(rot, OUT_ROTATION)

    ph = d.reindex(columns=[c for c in PHASE_HISTORY_COLS if c in d.columns]).copy()
    for c in ("theme_health", "seos_score", "data_quality_score"):
        ph[c] = pd.to_numeric(ph[c], errors="coerce").round(4)
    ph = ph.sort_values(["trade_date", "sector_id"], kind="mergesort").reset_index(drop=True)
    write_merge_csv(ph, OUT_PHASE_HISTORY)

    g = groups.reindex(columns=[c for c in GROUP_COLS if c in groups.columns]).copy()
    for c in g.columns:
        if c not in ("trade_date", "rotation_group", "group_name", "group_rotation_state"):
            g[c] = pd.to_numeric(g[c], errors="coerce").round(6)
    write_merge_csv(g, OUT_GROUP, key_cols=("trade_date", "rotation_group"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Step 4 — SEOS 主题早期信号与轮动系统")
    ap.add_argument("--full", action="store_true", help="全量重算并写出")
    ap.add_argument("--date", type=str, default=None, help="只重算指定交易日 YYYYMMDD")
    ap.add_argument("--validate", action="store_true", help="执行 12 项验证")
    ap.add_argument("--no-write", action="store_true", help="只计算不写文件（调试用）")
    args = ap.parse_args()

    if not (args.full or args.date or args.validate):
        ap.print_help()
        return 1

    cfg = load_json(SEOS_CONFIG_PATH)
    master = SectorMaster(MASTER_PATH)
    group_names = {}
    try:
        raw = json.load(open(MASTER_PATH, "r", encoding="utf-8"))
        for g in raw.get("rotation_groups", []) or []:
            if isinstance(g, dict):
                group_names[g.get("id")] = g.get("name", "")
    except Exception as e:  # pragma: no cover
        log.warning("读取 rotation_groups 名称失败：%s", e)

    panel, state_today = build_panel()
    df, groups = compute_pipeline(panel, cfg, group_names)
    trade_date = df["trade_date"].max()
    log.info("SEOS 计算完成：%d 行，末日 %s", len(df), trade_date)

    val = None
    if args.validate:
        val = run_validation(panel, df, groups, cfg, master, group_names, [])
        n_pass = int((val["status"] == "PASS").sum())
        log.info("验证：%d/%d 通过", n_pass, len(val))
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        val.to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")
        # 验证报告的 Audit 需要 val，故在 validate 时一并刷新审计
        with open(OUT_AUDIT, "w", encoding="utf-8") as f:
            f.write(build_audit(df, groups, cfg, master, group_names, val, trade_date, state_today))
        log.info("写出 %s", os.path.relpath(OUT_AUDIT, BASE_DIR))

    if args.no_write:
        return 0 if (val is None or (val["status"] == "PASS").all()) else 1

    # 写出（--date 时只覆写该日，其余历史行合并保留）
    d = df if not args.date else df
    write_outputs(d, groups, cfg, trade_date)

    dump_json(build_today_json(d, groups, trade_date, cfg, state_today), OUT_SEOS_TODAY)
    dump_json(build_opportunity_pool(d, cfg, trade_date), OUT_POOL)

    btdf, _, base_stats = build_backtest(d, cfg)
    if not os.path.exists(OUT_BACKTEST) or args.full or args.date:
        with open(OUT_BACKTEST, "w", encoding="utf-8") as f:
            f.write(render_backtest_md(btdf, base_stats, cfg, trade_date))
        log.info("写出 %s", os.path.relpath(OUT_BACKTEST, BASE_DIR))

    if val is not None:
        with open(OUT_AUDIT, "w", encoding="utf-8") as f:
            f.write(build_audit(df, groups, cfg, master, group_names, val, trade_date, state_today))
        log.info("写出 %s", os.path.relpath(OUT_AUDIT, BASE_DIR))

    return 0 if (val is None or (val["status"] == "PASS").all()) else 1


if __name__ == "__main__":
    sys.exit(main())
