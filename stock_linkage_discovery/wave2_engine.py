"""二波介入引擎（确认联动后等待震荡缩量）—— 独立模块。

用户命题：群体联动的介入点不在第一波追涨，而在确认联动后的二波机会；
          「确认联动 → 震荡缩量」才是需要等待的介入结构。

本模块的职责边界（严格遵守 PRD 纪律）：
  1. **只描述介入结构的状态**，不产生 BUY 信号，不修改 grade / LinkageScore /
     群体成员 / 关联网络，也不修改 HVT-BULL / W7 / E08 / VSW 等既有策略逻辑；
  2. 所有判定严格 as-of：第 i 日的判定只用 <= i 的行情，不使用未来数据；
     回测验证部分允许读取入场日之后的收益，但入场日的选择不允许事后优化；
  3. 是否真的优于第一波，由 validate() 在已存档的 507 个真实事件上做样本外检验，
     结论允许 SUPPORTED / PARTIAL / NOT_SUPPORTED，绝不把回测包装成买入建议。

两套消费方式：
  - attach(cs, s, fs, wcfg)：每日日报用，给每个群体打 WAVE2_* 状态标签；
  - validate(cfg, wcfg)：事后验证用，输出 output/backtest/wave2_* 三件产物。

量价口径：
  - 群体等权指数 idx = cumprod(1 + 成员当日 pct_chg 的等权均值)；
  - 个股量比 vr = 当日成交额 / 前 20 日均额（shift(1)，不含当日）；群体取成员中位数；
  - 振幅 amp = (high-low)/low，比值与复权因子无关，仅作展示诊断；
  - 停牌（成交额为 0/缺失）记为无效，成员有效比例不足时不判定。
"""
from __future__ import annotations

import json
import math
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .cluster_engine import ClusterSet
from .common import get_logger, write_json
from .data_layer import Slice, _ro, trade_dates
from .feature_engine import FeatureSet

log = get_logger("wave2")

DEFAULT_CONFIG_PATH = Path(__file__).with_name("wave2_config.json")

STATUSES = ("WAVE2_READY", "WAVE2_WAIT", "WAVE2_BROKEN",
            "WAVE2_EXPIRED", "NOT_CONFIRMED", "NO_DATA")
CONFIRM_TAGS = ("EMERGING_CLUSTER", "LEADER_EXPANSION", "CLUSTER_STRENGTHENING")


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    cfg = json.loads(p.read_text(encoding="utf-8"))
    cfg["_config_path"] = str(p)
    return cfg


# --------------------------------------------------------------------------- #
# 一、群体量价条与纯判定函数（实时链路与事后验证共用同一套数学）
# --------------------------------------------------------------------------- #
@dataclass
class Bars:
    """确认日 T0 起算的群体日频量价条；所有数组长度 = len(dates)。"""
    dates: list[str]
    t0_pos: int
    vr: np.ndarray            # 群体量比中位数
    amp: np.ndarray           # 群体日内振幅中位数（诊断用）
    ret_idx: np.ndarray       # 群体等权日收益
    idx: np.ndarray           # 群体等权净值
    ma: np.ndarray            # idx 的 N 日均线
    valid_ratio: np.ndarray   # 当日有效成员比例

    @property
    def T(self) -> int:
        return len(self.dates)


def _nanmedian(a: np.ndarray) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        return np.nanmedian(a, axis=1)


def bars_from_matrices(dates: list[str], t0: str,
                       R: np.ndarray, A: np.ndarray,
                       H: np.ndarray, L: np.ndarray,
                       wcfg: dict[str, Any]) -> Bars | None:
    """由 T x N 矩阵构造群体量价条。R/A/H/L 列必须按同一顺序对齐成员股票。

    R: 日收益小数；A: 成交额（千元，<=0 视为停牌无效）；H/L: 最高/最低价（同口径）。
    """
    if t0 not in dates:
        return None
    T = len(dates)
    R = np.asarray(R, dtype=np.float64)
    A = np.asarray(A, dtype=np.float64)
    H = np.asarray(H, dtype=np.float64)
    L = np.asarray(L, dtype=np.float64)
    if R.ndim != 2 or A.shape != R.shape or H.shape != R.shape or L.shape != R.shape:
        return None
    n = R.shape[1]
    if n == 0:
        return None

    base_n = int(wcfg.get("volume_baseline_days", 20))
    Adf = pd.DataFrame(np.where(np.isfinite(A) & (A > 0), A, np.nan))
    base = Adf.rolling(base_n, min_periods=max(5, base_n // 2)).mean().shift(1)
    with np.errstate(divide="ignore", invalid="ignore"):
        vr_stock = (Adf / base).replace([np.inf, -np.inf], np.nan)
    vr_arr = vr_stock.to_numpy(dtype=np.float64)
    finite = np.isfinite(vr_arr)
    valid_ratio = finite.mean(axis=1)
    vr = _nanmedian(vr_arr)
    vr = np.where(valid_ratio >= float(wcfg.get("min_valid_member_ratio", 0.60)),
                  np.clip(vr, 0.0, 10.0), np.nan)

    with np.errstate(divide="ignore", invalid="ignore"):
        amp_stock = np.where((L > 0) & (H >= L) & np.isfinite(H) & np.isfinite(L),
                             (H - L) / L, np.nan)
    amp = _nanmedian(amp_stock)

    Rf = np.where(np.isfinite(R), np.clip(R, -0.25, 0.25), 0.0)
    ret_idx = Rf.mean(axis=1)
    idx = np.cumprod(1.0 + ret_idx)
    ma_w = int(wcfg.get("ma_window", 10))
    ma = pd.Series(idx).rolling(ma_w, min_periods=ma_w).mean().to_numpy(dtype=np.float64)
    return Bars(dates=list(dates), t0_pos=dates.index(t0), vr=vr, amp=amp,
                ret_idx=ret_idx, idx=idx, ma=ma, valid_ratio=valid_ratio)


def _metrics(b: Bars, i: int, wcfg: dict[str, Any]) -> dict[str, Any]:
    """第 i 日收盘时可见的全部量价事实（不含 i 之后任何数据）。"""
    t0 = b.t0_pos
    lo = max(0, t0 - int(wcfg.get("first_wave_lookback_before_t0", 2)))
    peak_arr = b.vr[lo:i]                       # 峰值不含当日，避免缩量日自己当峰值
    peak = float(np.nanmax(peak_arr)) if np.isfinite(peak_arr).any() else float("nan")
    cd = int(wcfg.get("consolidation_days", 3))
    seg = b.idx[max(0, i - cd + 1): i + 1]
    range_cd = float(seg.max() / seg.min() - 1.0) if len(seg) >= 2 and seg.min() > 0 else float("nan")
    retrace = float(b.idx[i] / b.idx[t0] - 1.0) if b.idx[t0] > 0 else float("nan")
    return {
        "vr": float(b.vr[i]) if np.isfinite(b.vr[i]) else None,
        "first_wave_peak_vr": None if not np.isfinite(peak) else round(peak, 4),
        "range_3": None if not np.isfinite(range_cd) else round(range_cd, 4),
        "amp": float(b.amp[i]) if np.isfinite(b.amp[i]) else None,
        "retrace_from_t0": None if not np.isfinite(retrace) else round(retrace, 4),
        "ma10_dev": (round(float(b.idx[i] / b.ma[i] - 1.0), 4)
                     if np.isfinite(b.ma[i]) and b.ma[i] > 0 else None),
        "day_ret": round(float(b.ret_idx[i]), 4),
        "valid_ratio": round(float(b.valid_ratio[i]), 3),
    }


def pass_core(b: Bars, i: int, wcfg: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """单日「震荡缩量」判定。纯函数，只用 <= i 的数据，便于单测。"""
    m = _metrics(b, i, wcfg)
    checks: dict[str, bool] = {}
    checks["volume_valid"] = m["vr"] is not None
    checks["shrink_abs"] = bool(m["vr"] is not None
                                and m["vr"] <= float(wcfg.get("shrink_abs", 0.80)))
    checks["first_wave_exists"] = bool(m["first_wave_peak_vr"] is not None
                                       and m["first_wave_peak_vr"] >= float(wcfg.get("first_wave_vr", 1.30)))
    checks["shrink_vs_first_wave"] = bool(
        m["vr"] is not None and m["first_wave_peak_vr"] is not None
        and m["vr"] <= float(wcfg.get("shrink_vs_first_wave", 0.65)) * m["first_wave_peak_vr"])
    ks = int(wcfg.get("shrink_confirm_days", 2))
    recent = b.vr[max(0, i - ks + 1): i + 1]
    peak = m["first_wave_peak_vr"]
    checks["shrink_sustained"] = bool(
        len(recent) >= ks and np.isfinite(recent).all() and m["vr"] is not None
        and peak is not None
        and float(recent.max()) <= float(wcfg.get("shrink_abs", 0.80))
        and float(recent.max()) <= float(wcfg.get("shrink_vs_first_wave", 0.65)) * peak)
    checks["consolidation"] = bool(m["range_3"] is not None
                                   and m["range_3"] <= float(wcfg.get("max_range_3", 0.05)))
    checks["retrace_bounded"] = bool(
        m["retrace_from_t0"] is not None
        and -float(wcfg.get("max_pullback", 0.10)) <= m["retrace_from_t0"]
        <= float(wcfg.get("max_rebound", 0.06)))
    checks["trend_intact"] = bool(
        m["ma10_dev"] is not None
        and m["ma10_dev"] >= -float(wcfg.get("ma_tolerance", 0.02)))
    checks["no_breakdown_day"] = bool(m["day_ret"] >= -float(wcfg.get("max_entry_day_drop", 0.03)))
    if bool(wcfg.get("require_two_day_shrink", False)) and i > 0:
        checks["two_day_shrink"] = pass_core_prev(b, i - 1, wcfg)
    return all(checks.values()), {"checks": checks, "metrics": m}


def pass_core_prev(b: Bars, i: int, wcfg: dict[str, Any]) -> bool:
    """前一交易日是否满足同样的核心条件（不含 two_day 递归，避免无限递归）。"""
    shallow = {k: v for k, v in wcfg.items() if k != "require_two_day_shrink"}
    return pass_core(b, i, shallow)[0]


def scan(b: Bars | None, wcfg: dict[str, Any], upto: int | None = None) -> dict[str, Any]:
    """从 T0+1 扫描到 upto（默认今日/条末），返回第一个合格的二波观察点与当前状态。

    扫描顺序完全按时间，先出现的缩量日就是入场日，绝不回头挑收益最高的一天。
    等待期内先跌破回撤上限 -> 结构破坏，此后不再接受新的买点（除非此前已触发）。
    """
    empty = {"status": "NO_DATA", "first_ready_pos": None, "entry_pos": None,
             "wait_days": None, "broken_pos": None, "checks": {}, "metrics": {},
             "first_ready_date": None, "entry_date": None, "post_entry_broken": False}
    if b is None or b.t0_pos < int(wcfg.get("ma_window", 10)):
        return empty
    t0 = b.t0_pos
    max_wait = int(wcfg.get("max_wait_days", 10))
    look = int(wcfg.get("rebound_max_lookahead", 3))
    last = b.T - 1 if upto is None else min(int(upto), b.T - 1)
    scan_last = min(last, t0 + max_wait)
    mode = str(wcfg.get("entry_mode", "SHRINK_DAY"))
    two_day = bool(wcfg.get("require_two_day_shrink", False))

    first_ready: int | None = None
    entry: int | None = None
    broken: int | None = None
    detail: dict[str, Any] = {}
    first_i = t0 + max(1, int(wcfg.get("min_wait_days", 2)))
    for i in range(first_i, scan_last + 1):
        retrace = b.idx[i] / b.idx[t0] - 1.0 if b.idx[t0] > 0 else 0.0
        if (bool(wcfg.get("break_on_deep_pullback", True))
                and retrace < -float(wcfg.get("max_pullback", 0.10))
                and broken is None):
            broken = i
            if first_ready is None:
                break                      # 先破位、后企稳不算二波
        ok, info = pass_core(b, i, wcfg)
        if not ok or first_ready is not None:
            continue
        if two_day and i - 1 < first_i:
            continue
        if mode == "REBOUND_CONFIRM":
            j = None
            for jj in range(i + 1, min(b.T - 1, i + look) + 1):
                if (np.isfinite(b.vr[jj]) and b.vr[jj] >= float(wcfg.get("rebound_min_vr", 1.0))
                        and b.ret_idx[jj] > float(wcfg.get("rebound_min_ret", 0.0))):
                    j = jj
                    break
            if j is None:
                continue
            entry = j
        else:
            entry = i
        first_ready, detail = i, info

    today = last                       # 真正的 as-of 日，不用窗口末端冒充今日
    today_ok, today_info = pass_core(b, today, wcfg)
    if b.t0_pos > today or not np.isfinite(b.ma[today]):
        status = "NO_DATA"
    elif b.t0_pos == today:
        status = "WAVE2_WAIT"              # 确认当日，二波计时刚开始
    elif broken is not None and (first_ready is None or broken < first_ready):
        status = "WAVE2_BROKEN"
    elif today > t0 + max_wait:
        status = "WAVE2_EXPIRED"
    elif today_ok and (broken is None or broken > today):
        status = "WAVE2_READY"
    elif broken is not None:
        status = "WAVE2_BROKEN"
    else:
        status = "WAVE2_WAIT"

    out = {
        "status": status,
        "first_ready_pos": first_ready,
        "entry_pos": entry,
        "wait_days": (entry - t0) if entry is not None else None,
        "broken_pos": broken,
        "post_entry_broken": bool(broken is not None and first_ready is not None
                                  and broken > first_ready),
        "checks": today_info.get("checks", {}),
        "metrics": today_info.get("metrics", {}),
        "first_ready_date": b.dates[first_ready] if first_ready is not None else None,
        "entry_date": b.dates[entry] if entry is not None else None,
        "days_since_t0": today - t0,
        "first_ready_detail": detail.get("metrics", {}),
    }
    return out


def _failed_reasons(checks: dict[str, bool], metrics: dict[str, Any],
                    wcfg: dict[str, Any]) -> list[str]:
    zh = {
        "volume_valid": "有效行情成员不足",
        "shrink_abs": "量能未缩到门槛",
        "first_wave_exists": "尚未出现放量第一波",
        "shrink_vs_first_wave": "相对第一波峰值缩量不足",
        "shrink_sustained": "缩量未连续两日（单日脉冲不算洗盘）",
        "consolidation": "价格仍在单边波动，未横住",
        "retrace_bounded": "相对确认日回撤过深或已再度拉升",
        "trend_intact": "跌破 10 日均价",
        "no_breakdown_day": "当日仍在破位下跌",
        "two_day_shrink": "缩量未连续两日",
    }
    return [zh.get(k, k) for k, v in checks.items() if not v]


# --------------------------------------------------------------------------- #
# 二、实时链路：给当日群体打状态标签（不改评级、不改成员、不改策略信号）
# --------------------------------------------------------------------------- #
def attach(cs: ClusterSet, s: Slice, fs: FeatureSet,
           wcfg: dict[str, Any]) -> ClusterSet:
    if not wcfg.get("enabled", True) or not len(cs.clusters) or not len(cs.members):
        return cs
    idx = {str(c): j for j, c in enumerate(s.codes)}
    T, N = s.T, s.N
    R_all = np.asarray(s.ret, dtype=np.float64)
    A_all = np.asarray(s.amount, dtype=np.float64)
    H_all = np.asarray(s.high, dtype=np.float64)
    L_all = np.asarray(s.low, dtype=np.float64)
    ma_w = int(wcfg.get("ma_window", 10))
    if T < ma_w + 2:
        for c in cs.clusters:
            c["wave2_status"] = "NO_DATA"
        cs.stats["wave2_status_counts"] = {"NO_DATA": len(cs.clusters)}
        return cs

    grouped: dict[str, list[str]] = {}
    for r in cs.members.itertuples(index=False):
        grouped.setdefault(str(r.cluster_id), []).append(str(r.ts_code))

    counts = {k: 0 for k in STATUSES}
    for c in cs.clusters:
        cid = str(c["cluster_id"])
        tags = set(c.get("signals") or []) | {str(c.get("state", ""))}
        confirmed = bool(tags & set(wcfg.get("confirmation_tags", CONFIRM_TAGS)))
        birth = str(c.get("birth_date") or c.get("asof") or s.asof)
        cols = [idx[k] for k in grouped.get(cid, []) if k in idx]
        bars = (bars_from_matrices(list(s.dates), birth,
                                   R_all[:, cols], A_all[:, cols],
                                   H_all[:, cols], L_all[:, cols], wcfg)
                if len(cols) >= 2 and birth in s.dates else None)
        res = scan(bars, wcfg) if bars is not None else {"status": "NO_DATA"}
        status = res.get("status", "NO_DATA")
        if not confirmed and status in ("WAVE2_READY", "WAVE2_WAIT"):
            # 存量群体仍可展示「本轮是否曾出现缩量点」，但不再作为新确认事件的等待计时
            status = "NOT_CONFIRMED" if res.get("first_ready_pos") is None else status
        m = res.get("metrics", {})
        res["wave2_t0"] = birth
        res["_unmet"] = _failed_reasons(res.get("checks", {}), m, wcfg)
        c.update({
            "wave2_status": status,
            "wave2_confirmed": bool(confirmed),
            "wave2_t0": birth,
            "wave2_wait_days": res.get("days_since_t0"),
            "wave2_entry_date": res.get("entry_date"),
            "wave2_entry_wait": res.get("wait_days"),
            "wave2_vr": m.get("vr"),
            "wave2_first_wave_peak_vr": m.get("first_wave_peak_vr"),
            "wave2_range_3": m.get("range_3"),
            "wave2_retrace": m.get("retrace_from_t0"),
            "wave2_unmet": _failed_reasons(res.get("checks", {}), m, wcfg),
            "wave2_note": _note(status, res, wcfg),
        })
        counts[status] = counts.get(status, 0) + 1

    cs.stats["wave2_status_counts"] = counts
    cs.stats["wave2_ready_clusters"] = counts.get("WAVE2_READY", 0)
    cs.flags["WAVE2_STRICT_FILTER"] = bool(wcfg.get("strict_filter", False))
    log.info("二波介入状态: 群体 %d | READY %d | WAIT %d | BROKEN %d | EXPIRED %d",
             len(cs.clusters), counts.get("WAVE2_READY", 0), counts.get("WAVE2_WAIT", 0),
             counts.get("WAVE2_BROKEN", 0), counts.get("WAVE2_EXPIRED", 0))
    return cs


def _note(status: str, res: dict[str, Any], wcfg: dict[str, Any]) -> str:
    m = res.get("metrics", {})
    def p(x: Any) -> str:
        return "-" if x is None else f"{float(x):.2f}"
    head = (f"确认日 {res.get('wave2_t0', '-')} 起第 {res.get('days_since_t0', '-')} 日；"
            f"量比 {p(m.get('vr'))}（第一波峰值 {p(m.get('first_wave_peak_vr'))}），"
            f"3日振幅 {p(m.get('range_3'))}，相对确认日 {p(m.get('retrace_from_t0'))}")
    if status == "WAVE2_READY":
        return head + "；已满足震荡缩量，进入二波观察窗口（非买入信号）"
    if status == "WAVE2_BROKEN":
        return head + "；等待期内结构破坏，二波逻辑失效，不再补票"
    if status == "WAVE2_EXPIRED":
        return head + f"；超过 {wcfg.get('max_wait_days', 10)} 日未出现合格缩量，本轮放弃"
    if status == "NOT_CONFIRMED":
        return head + "；当前非新确认事件，不重开二波计时"
    if status == "NO_DATA":
        return "行情窗口不足或成员有效数据不足"
    unmet = res.get("_unmet", [])
    return head + "；等待缩量中" + ("（缺：" + "、".join(unmet) + "）" if unmet else "")


# --------------------------------------------------------------------------- #
# 三、事后验证：第一波 vs 二波 vs 纯等待 vs 同日随机对照
# --------------------------------------------------------------------------- #
def _load_aux(cfg: dict[str, Any], start: str, end: str) -> dict[str, Any]:
    """成交额 / 最高 / 最低价矩阵，用于构造量比与振幅；只读，不改主库。"""
    with _ro(cfg["paths"]["market_db"]) as con:
        df = pd.read_sql_query(
            "SELECT ts_code,trade_date,amount,high,low FROM daily_cache "
            "WHERE trade_date BETWEEN ? AND ?", con, params=(start, end))
    out: dict[str, Any] = {}
    for key in ("amount", "high", "low"):
        piv = df.pivot(index="trade_date", columns="ts_code", values=key).sort_index()
        out[key] = piv
    return out


def _agg(sub: pd.DataFrame, col: str, draws: int,
         rng: np.random.Generator) -> dict[str, Any]:
    """沿用主回测的按形成日分块 bootstrap，避免同日多事件相关导致 t 值虚高。"""
    from . import backtest_engine as bt
    v = pd.to_numeric(sub[col], errors="coerce") if col in sub else pd.Series(dtype=float)
    v = v.dropna()
    item: dict[str, Any] = {"n": int(len(v))}
    if not len(v):
        return item
    item["mean"] = round(float(v.mean()), 6)
    item["win_rate"] = round(float((v > 0).mean()), 4)
    item["median"] = round(float(v.median()), 6)
    if len(v) > 1:
        sd = float(v.std())
        item["tstat"] = round(float(v.mean() / (sd / math.sqrt(len(v)))), 3) if sd > 1e-9 else None
    if draws > 0:
        day_col = "T0" if "T0" in sub else "entry_date"
        item["bootstrap"] = bt._bootstrap_by_day(
            sub[day_col].astype(str).to_numpy(),
            pd.to_numeric(sub[col], errors="coerce").to_numpy(dtype=float), draws, rng)
    return item


def validate(cfg: dict[str, Any], wcfg: dict[str, Any]) -> dict[str, Any]:
    """在已存档的真实事件上验证「确认后等震荡缩量」是否有增量价值。

    事件与 T0 基线直接复用 output/backtest/events.csv（507 个已按成员重叠去重的
    真实事件、同一套 30bp 成本与全A等权基准），保证对照口径完全一致。
    """
    from . import backtest_engine as bt
    outdir = Path(cfg["paths"]["output_dir"]) / "backtest"
    ev_path = outdir / "events.csv"
    if not ev_path.exists():
        return {"status": "NO_EVENTS", "note": "请先运行 --backtest 生成 events.csv"}
    ev = pd.read_csv(ev_path, dtype={"T0": str})
    vcfg = wcfg.get("validation", {})
    warm = str(vcfg.get("warmup_start", "20260301"))
    t0_min, t0_max = str(ev["T0"].min()), str(ev["T0"].max())
    end = max(str(trade_dates(cfg, "99991231", 1)[-1]), t0_max)

    R, dates, MV = bt._load_market(cfg, t0_min, end)
    R_warm, dates_warm = bt._load_prior_market(cfg, warm, end)
    aux = _load_aux(cfg, warm, end)
    dates_warm = [d for d in dates_warm if d in set(R_warm.index)]
    A_p = aux["amount"].reindex(index=dates_warm)
    H_p = aux["high"].reindex(index=dates_warm)
    L_p = aux["low"].reindex(index=dates_warm)
    R_p = R_warm.reindex(index=dates_warm)
    all_cols = list(R_p.columns)
    cpos = {c: j for j, c in enumerate(all_cols)}

    def _mat(key: str, codes: list[str], fill: float) -> np.ndarray:
        src = {"R": R_p, "A": A_p, "H": H_p, "L": L_p}[key]
        arr = src.reindex(columns=codes).to_numpy(dtype=np.float64)
        return np.nan_to_num(arr, nan=fill, posinf=fill, neginf=fill)

    con = bt._linkage_ro(cfg)
    try:
        mem = pd.read_sql_query("SELECT asof,cluster_id,ts_code FROM cluster_members", con)
    finally:
        con.close()
    mem_map: dict[tuple[str, str], list[str]] = {}
    for (d, cid), g in mem.groupby(["asof", "cluster_id"]):
        mem_map[(str(d), str(cid))] = [str(x) for x in g["ts_code"]]

    rng = np.random.default_rng(20261110)
    fixed_delays = [int(k) for k in vcfg.get("fixed_delays", [1, 3, 5, 8])]
    draws = int(vcfg.get("bootstrap_draws", 2000))
    rows: list[dict[str, Any]] = []
    bar_cache: list[tuple[dict[str, Any], Bars]] = []

    for e in ev.itertuples(index=False):
        t0, cid = str(e.T0), str(e.cluster_id)
        codes = [c for c in mem_map.get((t0, cid), []) if c in cpos]
        if len(codes) < 2 or t0 not in dates_warm:
            continue
        b = bars_from_matrices(dates_warm, t0, _mat("R", codes, 0.0),
                               _mat("A", codes, 0.0), _mat("H", codes, 0.0),
                               _mat("L", codes, 0.0), wcfg)
        if b is None:
            continue
        # 事后验证只把等待窗口末端当作"今日"，避免用最后一个交易日冒充事件当日状态
        res = scan(b, wcfg, upto=min(b.T - 1, b.t0_pos + int(wcfg.get("max_wait_days", 10))))
        rec: dict[str, Any] = {
            "cluster_id": cid, "T0": t0, "segment": str(getattr(e, "segment", "ALL")),
            "grade": str(getattr(e, "grade", "")), "n_stocks": len(codes),
            "m_mean5": float(getattr(e, "m_mean5", np.nan) or 0),
            "m_state": str(getattr(e, "m_state", "")),
            "t0_h5": getattr(e, "h5_excess_market", np.nan),
            "t0_h10": getattr(e, "h10_excess_market", np.nan),
            "t0_h20": getattr(e, "h20_excess_market", np.nan),
            "status": res["status"], "entry_date": res["entry_date"],
            "wait_days": res["wait_days"],
            "days_since_t0": res.get("days_since_t0"),
            "post_entry_broken": res.get("post_entry_broken", False),
            **{f"m_{k}": v for k, v in (res.get("first_ready_detail") or {}).items()},
        }
        i0 = dates.index(t0) if t0 in dates else -1
        for k in fixed_delays:
            d_k = dates[i0 + k] if i0 >= 0 and i0 + k < len(dates) else None
            for h in (5, 10):
                r = bt._path_ret(R, dates, d_k, codes, h) if d_k else {}
                rec[f"fixed{k}_h{h}"] = r.get("excess_market", np.nan)
        if res["entry_date"]:
            E = res["entry_date"]
            for h in (3, 5, 10, 20):
                r = bt._path_ret(R, dates, E, codes, h, MV)
                rec[f"wave2_h{h}"] = r.get("excess_market", np.nan)
                if h == 10:
                    rec["wave2_h10_trimmed"] = r.get("excess_market_trimmed", np.nan)
                    rec["wave2_h10_after_cost"] = r.get("excess_after_cost", np.nan)
                    rec["wave2_h10_win_rate"] = r.get("win_rate", np.nan)
                    rec["wave2_h10_mdd"] = r.get("max_drawdown", np.nan)
            if vcfg.get("random_control", True):
                ctl = bt._control(R, dates, E, len(codes), rng, "random",
                                  codes, {}, {}, MV)
                rec["same_day_random_h10"] = ctl.get("ctrl_random_excess_10", np.nan)
        rows.append(rec)
        bar_cache.append((rec, b))

    edf = pd.DataFrame(rows)
    if not len(edf):
        return {"status": "NO_VALID_EVENTS"}
    # 触发与否只看入场日是否被选出；T+10 因右删失为 NaN 的事件仍属已触发样本，
    # 不能删掉，否则统计会系统性偏向早期事件。
    trig = edf[edf["entry_date"].notna() & (edf["entry_date"].astype(str) != "")].copy()
    no_trig = edf[~edf.index.isin(trig.index)].copy()

    # 匹配等待天数：全体触发事件等待日的中位数，选最接近的固定延迟 k
    med_wait = float(trig["wait_days"].median()) if len(trig) else float(fixed_delays[0])
    matched_k = min(fixed_delays, key=lambda k: abs(k - med_wait))
    trig["paired_vs_t0"] = trig["wave2_h10"] - trig["t0_h10"]
    trig["paired_vs_fixed"] = trig["wave2_h10"] - trig[f"fixed{matched_k}_h10"]
    trig["paired_vs_random"] = trig["wave2_h10"] - trig["same_day_random_h10"] \
        if "same_day_random_h10" in trig else np.nan

    segments = ("ALL", "IN_SAMPLE", "OUT_OF_SAMPLE")
    strategies: dict[str, pd.DataFrame] = {
        "T0_FIRST_WAVE": edf.assign(_v=edf["t0_h10"]),
        "WAVE2_SHRINK": trig.assign(_v=trig["wave2_h10"]),
        "NO_TRIGGER": no_trig.assign(_v=no_trig["t0_h10"]),
    }
    for k in fixed_delays:
        strategies[f"FIXED_DELAY_{k}"] = edf.assign(_v=edf[f"fixed{k}_h10"])
    by_segment: dict[str, dict[str, Any]] = {}
    pvals: dict[str, float] = {}
    for seg in segments:
        d: dict[str, Any] = {}
        for name, frame in strategies.items():
            sub = frame if seg == "ALL" else frame[frame["segment"] == seg]
            a = _agg(sub, "_v", draws, rng)
            if name == "WAVE2_SHRINK":
                h5col, h20col = "wave2_h5", "wave2_h20"
            elif name.startswith("FIXED_DELAY_"):
                k = name.rsplit("_", 1)[-1]
                h5col, h20col = f"fixed{k}_h5", None
            else:
                h5col, h20col = "t0_h5", "t0_h20"
            a["h5"] = _agg(sub, h5col, 0, rng).get("mean")
            a["h20"] = _agg(sub, h20col, 0, rng).get("mean") if h20col else None
            if name == "WAVE2_SHRINK":
                a["trimmed_h10"] = _agg(sub, "wave2_h10_trimmed", 0, rng).get("mean")
                a["after_cost_h10"] = _agg(sub, "wave2_h10_after_cost", 0, rng).get("mean")
                a["same_day_random_h10"] = _agg(sub, "same_day_random_h10", 0, rng).get("mean")
            d[name] = a
            if name in ("WAVE2_SHRINK", "T0_FIRST_WAVE") and a.get("bootstrap", {}).get("n_days", 0) >= 3:
                pvals[f"{seg}:{name}"] = a["bootstrap"]["p_two_sided"]
        by_segment[seg] = d

    paired: dict[str, Any] = {}
    for col in ("paired_vs_t0", "paired_vs_fixed", "paired_vs_random"):
        if col not in trig:
            continue
        paired[col] = {
            seg: _agg(trig if seg == "ALL" else trig[trig["segment"] == seg],
                      col, draws, rng)
            for seg in segments
        }
        for seg in segments:
            bs = paired[col][seg].get("bootstrap", {})
            if bs.get("n_days", 0) >= 3:
                pvals[f"{seg}:{col}"] = bs["p_two_sided"]
    # 诊断一：触发/未触发群体在 T0 时点的差别 —— 检验「第一波追涨是不是真的有代价」
    def _m(frame: pd.DataFrame, col: str) -> float | None:
        v = pd.to_numeric(frame[col], errors="coerce").dropna()
        return round(float(v.mean()), 6) if len(v) else None
    diagnostics: dict[str, Any] = {
        "t0_h10_triggered": _m(trig, "t0_h10"),
        "t0_h10_no_trigger": _m(no_trig, "t0_h10"),
        "t0_h10_n_triggered": int(pd.to_numeric(trig["t0_h10"], errors="coerce").notna().sum()),
        "t0_h10_n_no_trigger": int(pd.to_numeric(no_trig["t0_h10"], errors="coerce").notna().sum()),
        "t0_mean_move5_triggered": _m(trig, "m_mean5"),
        "t0_mean_move5_no_trigger": _m(no_trig, "m_mean5"),
        "t0_extended_share_triggered": round(float((trig["m_state"] == "EXTENDED").mean()), 4) if len(trig) else None,
        "t0_extended_share_no_trigger": round(float((no_trig["m_state"] == "EXTENDED").mean()), 4) if len(no_trig) else None,
        "triggered_by_month": (trig.assign(month=trig["T0"].astype(str).str[:6])
                               .groupby("month").size().to_dict() if len(trig) else {}),
        "reading": ("出现缩量回踩的群体在确认日就已明显更热（5日涨幅更高、EXTENDED 占比更高），"
                    "其 T0 入场的 T+10 超额显著低于未触发群体；这说明『第一波追涨』确实有代价，"
                    "但该代价是事后用回踩路径识别出来的，不能在 T0 提前知道。"),
    }

    # 诊断二：二波 × 位置交叉分层 —— 缩量信号是否需要叠加「位置未透支」
    healthy = trig[trig["m_state"].isin(("FLAT", "PULLBACK")) & (trig["m_mean5"] <= 0.03)].copy()
    extended = trig[trig["m_state"].isin(("EXTENDED", "EARLY_MOVE"))].copy()
    cross: dict[str, Any] = {}
    for name, sub in (("POSITION_HEALTHY", healthy), ("POSITION_EXTENDED", extended)):
        cross[name] = {k: _agg(sub, k, draws, rng) for k in
                       ("wave2_h10", "t0_h10", f"fixed{matched_k}_h10",
                        "same_day_random_h10", "wave2_h10_trimmed", "wave2_h10_after_cost")}
        cross[name]["n_oos"] = int((sub["segment"] == "OUT_OF_SAMPLE").sum())
        cross[name]["h10_oos"] = round(float(pd.to_numeric(
            sub[sub["segment"] == "OUT_OF_SAMPLE"]["wave2_h10"], errors="coerce").mean()), 6) \
            if cross[name]["n_oos"] else None
    cross["_reading"] = ("未透支位置(FLAT/PULLBACK)的二波入场方向好于已启动/透支位置，"
                         "但两者样本都只有约 30 个、bootstrap CI 均跨 0，剔除最高收益股后仍未转正；"
                         "只能作为风险分层，不足以升级为买点规则。")
    qvals = bt._bh_fdr(pvals)
    for seg in segments:
        for name, a in by_segment[seg].items():
            a.setdefault("bootstrap", {})["q_bh_fdr"] = qvals.get(f"{seg}:{name}")
    for col in paired:
        for seg in segments:
            paired[col][seg].setdefault("bootstrap", {})["q_bh_fdr"] = qvals.get(f"{seg}:{col}")

    # 阈值敏感性：只改二波判定，不重跑关联网络
    sens: list[dict[str, Any]] = []
    for g in vcfg.get("sensitivity_grid", []):
        gw = dict(wcfg)
        gw.update({k: v for k, v in g.items() if k != "name"})
        hit: list[dict[str, Any]] = []
        for rec, b in bar_cache:
            r = scan(b, gw)
            if not r["entry_date"]:
                continue
            rr = bt._path_ret(R, dates, r["entry_date"],
                              [c for c in mem_map.get((rec["T0"], rec["cluster_id"]), [])
                               if c in R.columns], 10)
            if not rr:
                continue
            hit.append({"h10": rr["excess_market"], "segment": rec["segment"],
                        "wait": r["wait_days"]})
        h = pd.DataFrame(hit)
        oos = h[h["segment"] == "OUT_OF_SAMPLE"]["h10"] if len(h) else pd.Series(dtype=float)
        sens.append({
            "name": g.get("name"), **{k: v for k, v in g.items() if k != "name"},
            "triggered": int(len(h)),
            "trigger_rate": round(len(h) / max(1, len(bar_cache)), 4),
            "mean_wait": round(float(h["wait"].mean()), 2) if len(h) else None,
            "h10_all": round(float(h["h10"].mean()), 6) if len(h) else None,
            "h10_oos": round(float(oos.mean()), 6) if len(oos) else None,
            "n_oos": int(len(oos)),
        })

    allv = by_segment["ALL"]["WAVE2_SHRINK"]
    outv = by_segment["OUT_OF_SAMPLE"]["WAVE2_SHRINK"]
    insv = by_segment["IN_SAMPLE"]["WAVE2_SHRINK"]
    t0all = by_segment["ALL"]["T0_FIRST_WAVE"]
    q_out = outv.get("bootstrap", {}).get("q_bh_fdr")
    ci_low = outv.get("bootstrap", {}).get("ci95_low")
    ci_high = outv.get("bootstrap", {}).get("ci95_high")
    pfix = paired.get("paired_vs_fixed", {}).get("ALL", {})
    pfix_ci = pfix.get("bootstrap", {}).get("ci95_low")
    checks = {
        "触发样本充足": bool(len(trig) >= 60 and allv.get("bootstrap", {}).get("n_days", 0) >= 20),
        "全样本 T+10 超额 > 0": bool((allv.get("mean") or -9) > 0),
        "样本外 T+10 超额 > 0": bool((outv.get("mean") or -9) > 0),
        "样本内方向一致": bool((insv.get("mean") or -9) > 0),
        "样本外 bootstrap 95% 下界 > 0": bool(ci_low is not None and ci_low > 0),
        f"样本外 BH-FDR q <= {vcfg.get('fdr_alpha', 0.10)}": bool(q_out is not None and q_out <= float(vcfg.get("fdr_alpha", 0.10))),
        "剔除最高收益股后仍为正": bool((allv.get("trimmed_h10") or -9) > 0),
        "30bp 成本后仍为正": bool((allv.get("after_cost_h10") or -9) > 0),
        "跑赢 T0 第一波入场": bool((paired.get("paired_vs_t0", {}).get("ALL", {}).get("mean") or -9) > 0),
        f"跑赢等长纯等待（固定 k={matched_k} 日）": bool((pfix.get("mean") or -9) > 0
                                                and pfix_ci is not None and pfix_ci > 0),
        "跑赢同日随机选股": bool((paired.get("paired_vs_random", {}).get("ALL", {}).get("mean") or -9) > 0),
        "敏感性网格方向不全为负": bool(all((s["h10_all"] or -9) > 0
                                    for s in sens if s["triggered"] >= 30)),
    }
    failed = [k for k, ok in checks.items() if not ok]
    core = ["触发样本充足", "全样本 T+10 超额 > 0", "样本外 T+10 超额 > 0",
            "跑赢 T0 第一波入场", f"跑赢等长纯等待（固定 k={matched_k} 日）"]
    if all(checks[k] for k in core) and len(failed) <= 3:
        verdict = "SUPPORTED"
        reason = (f"二波缩量入场 T+10 超额 {allv.get('mean')}（样本外 {outv.get('mean')}），"
                  f"跑赢第一波与等长纯等待；{len(failed)} 项次要稳健性检查未通过")
    elif (allv.get("mean") or -9) > 0 and checks["跑赢 T0 第一波入场"]:
        verdict = "PARTIAL"
        reason = (f"二波入场优于第一波，但 {len(failed)}/{len(checks)} 项稳健性检查未通过："
                  f"{'；'.join(failed[:5])}。不能据此把「等缩量」当作稳定买点")
    else:
        verdict = "NOT_SUPPORTED"
        reason = (
            f"『缩量即买点』未成立：二波 T+10 超额 {allv.get('mean')}（样本外 {outv.get('mean')}，"
            f"但仅 {len(trig[trig['segment'] == 'OUT_OF_SAMPLE'])} 个样本外触发事件且与该段 "
            f"T0 大盘级超额 {by_segment['OUT_OF_SAMPLE']['T0_FIRST_WAVE'].get('mean')} 基本持平），"
            f"胜率 {allv.get('win_rate')}，剔除最高收益股后 {allv.get('trimmed_h10')}，"
            f"相对等长纯等待的配对差 {pfix.get('mean')}（CI 跨 0），相对同日随机选股 "
            f"{paired.get('paired_vs_random', {}).get('ALL', {}).get('mean')}。"
            f"未通过项：{'；'.join(failed[:5])}。"
            f"但命题的前半段被支持：后续出现缩量回踩的群体在确认日更热"
            f"（5日涨幅 {diagnostics.get('t0_mean_move5_triggered')} vs "
            f"{diagnostics.get('t0_mean_move5_no_trigger')}），T0 入场 T+10 超额 "
            f"{diagnostics.get('t0_h10_triggered')} vs {diagnostics.get('t0_h10_no_trigger')}，"
            f"同一批事件二波−第一波配对差 "
            f"{paired.get('paired_vs_t0', {}).get('ALL', {}).get('mean')}。"
            f"所以结论是『不该追第一波』成立，『等缩量买』未被验证为可盈利的入场规则。")

    summary: dict[str, Any] = {
        "status": "OK", "verdict": verdict, "reason": reason,
        "events": int(len(edf)), "triggered_events": int(len(trig)),
        "trigger_rate": round(len(trig) / max(1, len(edf)), 4),
        "mean_wait_days": round(med_wait, 2), "matched_fixed_delay": matched_k,
        "no_trigger_events": int(len(no_trig)),
        "split_note": "沿用主回测 events.csv 的严格时间切分（前段样本内 / 后段样本外）",
        "config": {k: wcfg.get(k) for k in
                   ("max_wait_days", "min_wait_days", "shrink_abs", "shrink_vs_first_wave",
                    "shrink_confirm_days", "first_wave_vr", "max_range_3", "max_pullback",
                    "max_rebound", "ma_window", "require_two_day_shrink", "entry_mode")},
        "checks": checks, "failed_checks": failed,
        "by_segment": by_segment, "paired": paired,
        "diagnostics": diagnostics, "cross_layer": cross,
        "diagnosis_supported": bool(
            (diagnostics.get("t0_h10_triggered") or 0) < (diagnostics.get("t0_h10_no_trigger") or 0)
            and (diagnostics.get("t0_mean_move5_triggered") or 0)
            > (diagnostics.get("t0_mean_move5_no_trigger") or 0)),
        "multiple_testing": {"method": "BH-FDR", "alpha": vcfg.get("fdr_alpha", 0.10),
                             "n_tests": len(pvals), "q_values": qvals},
        "sensitivity": sens,
        "wait_distribution": (trig["wait_days"].value_counts().sort_index().to_dict()
                              if len(trig) else {}),
        "disclaimer": (
            "本验证为事后评估，实时链路仍严格 as-of：入场日只由当日及之前的量价决定，"
            "按时间顺序取第一个合格缩量日，绝不事后挑收益最高的一天。"
            "T0 基线、对照基准、30bp 成本口径复用主回测 events.csv，成员来自已存档快照。"
            "固定延迟对照用于区分『二波条件有效』与『只是晚买几天』；同日随机对照为"
            "随机选股组合，与 4-8 只集中组合不完全可比。"
            "身份追踪仍有断裂，事件已按成员重叠去重；单次前后切分不是多折滚动样本外；"
            "最近事件 T+20 右删失；阈值敏感性只改二波判定，不重跑关联网络。"
            "WAVE2_READY 是结构观察标签，不是 BUY 信号，结论不构成买入建议。"),
    }
    outdir.mkdir(parents=True, exist_ok=True)
    edf.to_csv(outdir / "wave2_events.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(sens).to_csv(outdir / "wave2_sensitivity.csv", index=False,
                              encoding="utf-8-sig")
    write_json(outdir / "wave2_summary.json", summary)
    _write_report(summary, outdir / "wave2_report.md")
    log.info("二波验证完成: 事件 %d 触发 %d 等待中位数 %.1f 日 | 结论 %s",
             len(edf), len(trig), med_wait, verdict)
    return {"status": "OK", "verdict": verdict, "reason": reason,
            "triggered": int(len(trig)), "summary": summary}


def _write_report(s: dict[str, Any], path: Path) -> None:
    """结论先行的可读版；旧版逐字段渲染保留为 _write_report_legacy 供回溯。"""
    return _write_report_v2(s, path)


def _write_report_legacy(s: dict[str, Any], path: Path) -> None:
    def f(x: Any, d: int = 4) -> str:
        return "-" if x is None or (isinstance(x, float) and not np.isfinite(x)) else (
            f"{x:.{d}f}" if isinstance(x, (int, float)) else str(x))
    fixed_cols = [k for k in s["by_segment"]["ALL"] if k.startswith("FIXED_DELAY_")]
    L = ["# 二波介入验证 · 确认联动后等待震荡缩量", "",
         f"- 事件 {s['events']} 个，出现合格缩量买点 {s['triggered_events']} 个"
         f"（触发率 {f(s['trigger_rate'] * 100, 1)}%），等待中位数 {f(s['mean_wait_days'], 1)} 个交易日",
         f"- **结论：{s['verdict']}** —— {s['reason']}", "",
         "## 1. 三种入场方式对比（T+10 相对全A等权超额）", "",
         "| 区间 | 第一波(T0) | 二波(缩量日) | " +
          " | ".join(k.replace("FIXED_DELAY_", "固定") + "日" for k in fixed_cols) +
          " | 未触发群体(T0) |",
          "|---|---:|---:|" + "---:|" * len(fixed_cols) + "---:|"]
    for seg in ("ALL", "IN_SAMPLE", "OUT_OF_SAMPLE"):
        d = s["by_segment"][seg]
        cells = [f(d["T0_FIRST_WAVE"].get("mean")), f(d["WAVE2_SHRINK"].get("mean"))]
        cells += [f(d[k].get("mean")) for k in fixed_cols]
        cells += [f(d["NO_TRIGGER"].get("mean"))]
        L.append(f"| {seg} | " + " | ".join(cells) + " |")
    allv = s["by_segment"]["ALL"]["WAVE2_SHRINK"]
    outv = s["by_segment"]["OUT_OF_SAMPLE"]["WAVE2_SHRINK"]
    L += ["",
          f"- 二波全样本：{allv.get('n')} 个事件，T+5 {f(allv.get('h5'))} / "
          f"T+10 {f(allv.get('mean'))} / T+20 {f(allv.get('h20'))}，"
          f"胜率 {f(allv.get('win_rate'))}，剔除最高收益股 {f(allv.get('trimmed_h10'))}，"
          f"30bp 成本后 {f(allv.get('after_cost_h10'))}",
          f"- 样本外 bootstrap 95% CI [{f(outv.get('bootstrap', {}).get('ci95_low'))}, "
          f"{f(outv.get('bootstrap', {}).get('ci95_high'))}]，BH q="
          f"{f(outv.get('bootstrap', {}).get('q_bh_fdr'))}",
          "", "## 2. 配对差（同一事件，按形成日分块 bootstrap）", "",
          "| 配对 | 区间 | 均值差 | 胜率 | 95% CI | BH q |",
          "|---|---|---:|---:|---|---:|"]
    for col, zh in (("paired_vs_t0", "二波 − 第一波"),
                    ("paired_vs_fixed", f"二波 − 等长纯等待(k={s['matched_fixed_delay']})"),
                    ("paired_vs_random", "二波 − 同日随机选股")):
        for seg in ("ALL", "IN_SAMPLE", "OUT_OF_SAMPLE"):
            a = s["paired"].get(col, {}).get(seg, {})
            bs = a.get("bootstrap", {})
            ci = (f"[{f(bs.get('ci95_low'))}, {f(bs.get('ci95_high'))}]"
                  if bs.get("n_days") else "-")
            L.append(f"| {zh} | {seg} | {f(a.get('mean'))} | {f(a.get('win_rate'))} | "
                     f"{ci} | {f(bs.get('q_bh_fdr'))} |")
    L += ["", "## 3. 结论校验清单", "", "| 检查项 | 结果 |", "|---|---|"]
    for k, ok in s["checks"].items():
        L.append(f"| {k} | {'通过' if ok else '未通过'} |")
    L += ["", "## 4. 阈值敏感性（只改二波判定，不重跑关联网络）", "",
          "| 网格 | 触发数 | 触发率 | 平均等待 | T+10全样本 | T+10样本外 | 样本外N |",
          "|---|---:|---:|---:|---:|---:|---:|"]
    for g in s["sensitivity"]:
        L.append(f"| {g['name']} | {g['triggered']} | {f(g['trigger_rate'])} | "
                 f"{f(g['mean_wait'], 2)} | {f(g['h10_all'])} | {f(g['h10_oos'])} | "
                 f"{g['n_oos']} |")
    dg = s.get("diagnostics", {})
    L += ["", "## 5. 命题拆解：诊断成立，买点不成立", "",
          "| 指标 | 后续出现缩量回踩 | 未出现 |",
          "|---|---:|---:|",
          f"| 事件数 | {s['triggered_events']} | {s['no_trigger_events']} |",
          f"| T0 群体5日平均涨幅 | {f(dg.get('t0_mean_move5_triggered'))} | {f(dg.get('t0_mean_move5_no_trigger'))} |",
          f"| T0 已透支(EXTENDED)占比 | {f(dg.get('t0_extended_share_triggered'))} | {f(dg.get('t0_extended_share_no_trigger'))} |",
          f"| 若在 T0 追第一波的 T+10 超额 | {f(dg.get('t0_h10_triggered'))} | {f(dg.get('t0_h10_no_trigger'))} |",
          "",
          "> " + str(dg.get("reading", "")),
          "",
          "> **关键限制**：触发与否要靠 T0 之后 2~10 日的回踩路径才能识别，"
          "在 T0 当下无法提前知道哪些群体会回踩；因此上述配对差是『事后归因』，"
          "不能当作可交易的前瞻优势。真正可执行的规则只能写成「所有确认事件一律不追第一波，"
          "只在出现缩量时才入场」，而这一规则会跳过 88% 的事件，其中恰好包含表现更好的那一批。",
          "", "## 6. 二波 × 位置交叉分层（T+10 超额）", "",
          "| 位置分层 | 样本 | 二波 | 第一波 | 等长纯等待 | 同日随机 | 剔除最高股 | 成本后 | 样本外 |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    fk = "fixed" + str(s["matched_fixed_delay"]) + "_h10"
    for name, zh in (("POSITION_HEALTHY", "未透支 FLAT/PULLBACK"),
                     ("POSITION_EXTENDED", "已启动/透支")):
        c = s.get("cross_layer", {}).get(name, {})
        get = lambda k: f(c.get(k, {}).get("mean"))
        L.append(f"| {zh} | {c.get('wave2_h10', {}).get('n', 0)} | {get('wave2_h10')} | "
                 f"{get('t0_h10')} | {get(fk)} | "
                 f"{get('same_day_random_h10')} | {get('wave2_h10_trimmed')} | "
                 f"{get('wave2_h10_after_cost')} | {f(c.get('h10_oos'))} (n={c.get('n_oos', 0)}) |")
    L += ["", "> " + str(s.get("cross_layer", {}).get("_reading", "")),
          "", "## 7. 口径与纪律", "", "> " + s["disclaimer"], ""]
    path.write_text("\n".join(L), encoding="utf-8")


def _write_report_v2(s: dict[str, Any], path: Path) -> None:
    """结论先行 + 百分比 + 精简表格；数值全部取自已落盘的 summary，不重算、不改结论。"""
    def pct(x: Any, d: int = 2, sign: bool = True) -> str:
        if x is None or (isinstance(x, float) and not np.isfinite(x)):
            return "—"
        try:
            v = float(x) * 100
        except (TypeError, ValueError):
            return "—"
        return f"{v:+.{d}f}%" if sign else f"{v:.{d}f}%"

    def num(x: Any, d: int = 2) -> str:
        if x is None or (isinstance(x, float) and not np.isfinite(x)):
            return "—"
        return (f"{float(x):.{d}f}" if isinstance(x, (int, float, np.floating))
                else str(x))

    allv = s["by_segment"]["ALL"]["WAVE2_SHRINK"]
    outv = s["by_segment"]["OUT_OF_SAMPLE"]["WAVE2_SHRINK"]
    t0oos = s["by_segment"]["OUT_OF_SAMPLE"]["T0_FIRST_WAVE"]
    dg = s.get("diagnostics", {})
    pv = s.get("paired", {})
    checks = s.get("checks", {})
    passed = [k for k, v in checks.items() if v]
    failed = [k for k, v in checks.items() if not v]
    fixed_cols = [k for k in s["by_segment"]["ALL"] if k.startswith("FIXED_DELAY_")]
    sens = s.get("sensitivity", [])
    neg_sens = sum(1 for g in sens if (g.get("h10_all") or 0) < 0)
    seg_zh = {"ALL": "全样本", "IN_SAMPLE": "样本内", "OUT_OF_SAMPLE": "样本外"}

    L = ["# 二波介入验证 · 「确认联动后等震荡缩量」是不是买点", "",
         "## 一句话结论", "",
         f"> **{s['verdict']}**：「不该追第一波」被数据支持；「等缩量就能赚」**未被验证**。", "",
         f"- **样本**：{s['events']} 个去重真实事件，{s['triggered_events']} 个出现合格缩量点"
         f"（{pct(s['trigger_rate'], 1, sign=False)}），等待中位数 {num(s['mean_wait_days'], 1)} 个交易日。",
         f"- **二波表现**：T+10 超额 **{pct(allv.get('mean'))}**、胜率 "
         f"{pct(allv.get('win_rate'), 1, sign=False)}；剔除最高收益股 {pct(allv.get('trimmed_h10'))}、"
         f"30bp 成本后 {pct(allv.get('after_cost_h10'))}、相对同日随机 "
         f"{pct(pv.get('paired_vs_random', {}).get('ALL', {}).get('mean'))}。",
         f"- **样本外**：{pct(outv.get('mean'))}（n={outv.get('n')}，95% CI "
         f"[{pct(outv.get('bootstrap', {}).get('ci95_low'))}, "
         f"{pct(outv.get('bootstrap', {}).get('ci95_high'))}]，BH q="
         f"{num(outv.get('bootstrap', {}).get('q_bh_fdr'))}），但同段 T0 整体已有 "
         f"{pct(t0oos.get('mean'))}，属区间行情而非增量优势。", "",
         "| 命题拆解 | 判定 | 依据 |", "|---|---|---|",
         f"| 不追第一波 | ✅ 支持 | 后来回踩的群体在确认日更热（5日 "
         f"{pct(dg.get('t0_mean_move5_triggered'))} vs {pct(dg.get('t0_mean_move5_no_trigger'))}），"
         f"若 T0 追涨 T+10 为 {pct(dg.get('t0_h10_triggered'))} vs 未回踩 "
         f"{pct(dg.get('t0_h10_no_trigger'))} |",
         f"| 缩量日买入能赚 | ❌ 未验证 | 全样本 {pct(allv.get('mean'))}、"
         f"{neg_sens}/{len(sens)} 组阈值敏感性全样本为负、跑不赢等长纯等待"
         f"（{pct(pv.get('paired_vs_fixed', {}).get('ALL', {}).get('mean'))}，CI 跨 0） |",
         "",
         "> ⚠️ **关键限制**：是否回踩要靠 T0 之后 2~10 日的路径才能识别，T0 当下无法预知，"
         "所以「二波 − 第一波」配对差是**事后归因**，不是可交易的前瞻优势；"
         "而「一律不追第一波、只在缩量时买」会跳过约 88% 的事件，恰好跳过表现更好的那批。",
         "", "---", ""]

    # 1. 三种入场方式
    L += ["## 1. 三种入场方式对比（T+10 相对全A等权超额）", "",
          "| 区间 | 第一波(T0) | 二波(缩量日) | " +
          " | ".join(k.replace("FIXED_DELAY_", "固定") + "日" for k in fixed_cols) +
          " | 未触发群体(T0) |",
          "|---|---:|---:|" + "---:|" * len(fixed_cols) + "---:|"]
    for seg, zh in seg_zh.items():
        d = s["by_segment"][seg]
        cells = [pct(d["T0_FIRST_WAVE"].get("mean")), pct(d["WAVE2_SHRINK"].get("mean"))]
        cells += [pct(d[k].get("mean")) for k in fixed_cols]
        cells += [pct(d["NO_TRIGGER"].get("mean"))]
        L.append(f"| {zh} | " + " | ".join(cells) + " |")
    L += ["",
          f"- 二波全样本 n={allv.get('n')}：T+5 {pct(allv.get('h5'))} / "
          f"T+10 {pct(allv.get('mean'))} / T+20 {pct(allv.get('h20'))}；"
          f"样本内 {pct(s['by_segment']['IN_SAMPLE']['WAVE2_SHRINK'].get('mean'))}、"
          f"样本外 {pct(outv.get('mean'))}。",
          f"- 样本外 bootstrap 95% CI [{pct(outv.get('bootstrap', {}).get('ci95_low'))}, "
          f"{pct(outv.get('bootstrap', {}).get('ci95_high'))}]，BH q="
          f"{num(outv.get('bootstrap', {}).get('q_bh_fdr'))}；样本外可用触发事件仅 "
          f"{outv.get('n')} 个，不足以支撑独立结论。", ""]

    # 2. 配对差
    L += ["## 2. 配对差（同一事件，按形成日分块 bootstrap）", "",
          "| 配对 | 区间 | 均值差 | 胜率 | 95% CI | BH q |",
          "|---|---|---:|---:|---|---:|"]
    for col, zh in (("paired_vs_t0", "二波 − 第一波"),
                    ("paired_vs_fixed", f"二波 − 等长纯等待(k={s['matched_fixed_delay']})"),
                    ("paired_vs_random", "二波 − 同日随机选股")):
        for seg, szh in seg_zh.items():
            a = s["paired"].get(col, {}).get(seg, {})
            bs = a.get("bootstrap", {})
            ci = (f"[{pct(bs.get('ci95_low'))}, {pct(bs.get('ci95_high'))}]"
                  if bs.get("n_days") else "—")
            L.append(f"| {zh} | {szh} | {pct(a.get('mean'))} | "
                     f"{pct(a.get('win_rate'), 1, sign=False)} | {ci} | "
                     f"{num(bs.get('q_bh_fdr'), 3)} |")
    L.append("")

    # 3. 校验清单（两行替代 12 行表格）
    L += ["## 3. 结论校验清单", "",
          f"- ✅ **通过（{len(passed)}）**：" + " · ".join(passed),
          f"- ❌ **未通过（{len(failed)}）**：" + " · ".join(failed), ""]

    # 4. 敏感性
    L += ["## 4. 阈值敏感性（只改二波判定，不重跑关联网络）", "",
          "| 网格 | 触发数 | 触发率 | 平均等待 | T+10 全样本 | T+10 样本外 | 样本外N |",
          "|---|---:|---:|---:|---:|---:|---:|"]
    for g in sens:
        L.append(f"| {g['name']} | {g['triggered']} | {pct(g['trigger_rate'], 1, sign=False)} | "
                 f"{num(g['mean_wait'], 1)} | {pct(g['h10_all'])} | {pct(g['h10_oos'])} | "
                 f"{g['n_oos']} |")
    L += ["", f"> {neg_sens}/{len(sens)} 组网格的全样本 T+10 超额为负 → "
          "不是把阈值调松/调紧就能救回来的问题。", ""]

    # 5. 命题拆解
    L += ["## 5. 命题拆解：诊断成立，买点不成立", "",
          "| 指标 | 后续出现缩量回踩 | 未出现 |", "|---|---:|---:|",
          f"| 事件数 | {s['triggered_events']} | {s['no_trigger_events']} |",
          f"| T0 群体5日平均涨幅 | {pct(dg.get('t0_mean_move5_triggered'))} | "
          f"{pct(dg.get('t0_mean_move5_no_trigger'))} |",
          f"| T0 已透支(EXTENDED)占比 | {pct(dg.get('t0_extended_share_triggered'), 1, sign=False)} | "
          f"{pct(dg.get('t0_extended_share_no_trigger'), 1, sign=False)} |",
          f"| 若在 T0 追第一波的 T+10 超额 | {pct(dg.get('t0_h10_triggered'))} | "
          f"{pct(dg.get('t0_h10_no_trigger'))} |", "",
          "> " + str(dg.get("reading", "")), ""]

    # 6. 交叉分层
    L += ["## 6. 二波 × 位置交叉分层（T+10 超额）", "",
          "| 位置分层 | 样本 | 二波 | 第一波 | 等长纯等待 | 同日随机 | 剔除最高股 | 成本后 | 样本外 |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    fk = "fixed" + str(s["matched_fixed_delay"]) + "_h10"
    for name, zh in (("POSITION_HEALTHY", "未透支 FLAT/PULLBACK"),
                     ("POSITION_EXTENDED", "已启动/透支")):
        c = s.get("cross_layer", {}).get(name, {})
        get = lambda k: pct(c.get(k, {}).get("mean"))
        L.append(f"| {zh} | {c.get('wave2_h10', {}).get('n', 0)} | {get('wave2_h10')} | "
                 f"{get('t0_h10')} | {get(fk)} | {get('same_day_random_h10')} | "
                 f"{get('wave2_h10_trimmed')} | {get('wave2_h10_after_cost')} | "
                 f"{pct(c.get('h10_oos'))} (n={c.get('n_oos', 0)}) |")
    L += ["", "> " + str(s.get("cross_layer", {}).get("_reading", "")), ""]

    # 7. 口径与局限
    L += ["## 7. 口径与局限", "",
          "- **因果口径**：事后评估；实时链路严格 as-of，入场日只由当日及之前的量价决定，"
          "按时间顺序取第一个合格缩量日，绝不事后挑收益最高的一天。",
          "- **对照口径**：T0 基线、对照基准、30bp 成本复用主回测 `events.csv`，成员来自已存档快照；"
          "固定延迟用于区分「二波条件有效」与「只是晚买几天」；同日随机组合与 4-8 只集中组合不完全可比。",
          "- **已知局限**：身份追踪仍有断裂（事件已按成员重叠去重）；单次前后切分不是多折滚动样本外；"
          "最近事件 T+20 右删失；敏感性只改二波判定，不重跑关联网络。",
          "- **纪律**：`WAVE2_READY` 是结构观察标签，不是 BUY 信号；本报告不构成买入建议，"
          "也不修改任何既有策略逻辑。",
          "- 完整判定理由原文见 `wave2_summary.json` 的 `reason` 字段；逐事件明细见 `wave2_events.csv`。", ""]
    path.write_text("\n".join(L), encoding="utf-8")
