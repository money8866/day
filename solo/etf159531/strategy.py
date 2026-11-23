"""五层仓位模型的市场部分：趋势 / 波动率 / 超卖状态机 / 危机开关。

不做账户净值相关计算（见 risk_manager.py），也不做仓位落地（见 portfolio.py）。
本模块所有输出均为逐日序列，t 日只依赖 t 日及以前的数据。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from indicators import effective_volatility


# --------------------------------------------------------------------------- #
# 第一层：趋势基础仓位
# --------------------------------------------------------------------------- #
@dataclass
class TrendResult:
    level: int
    base_weight: float
    dirs: dict[str, str]
    confirmed_mas: list[str]
    changed: bool
    change_reason: str

    def as_dict(self) -> dict:
        return {
            "trend_level": self.level,
            "base_weight": self.base_weight,
            "dirs": self.dirs,
            "confirmed_mas": self.confirmed_mas,
            "changed": self.changed,
            "change_reason": self.change_reason,
        }


def _layer_weight(level: int, layers: dict) -> float:
    """按确认条数取基础仓位；配置缺省时按 0/20/40/65 兜底。"""
    default = {0: 0.0, 1: 0.20, 2: 0.40, 3: 0.65}
    table = default.copy()
    table.update({int(k): float(v) for k, v in (layers or {}).items()})
    return float(table.get(int(level), 0.0))


def trend_series(df: pd.DataFrame, cfg: dict,
                 ma_set: Optional[list[int]] = None) -> pd.DataFrame:
    """逐日趋势状态。

    确认规则：收盘价连续 confirm_days 个交易日位于同一均线同一侧，才算该均线方向被确认。
    对 MA20/MA60/MA120 三条（或回溯测试指定的组合）统计确认在上的条数。
    """
    trend_cfg = cfg.get("trend", {})
    confirm_days = int(trend_cfg.get("confirm_days", 2))
    layers = trend_cfg.get("layers", {})
    mas = ma_set or [20, 60, 120]

    close = df["close_adj"].astype(float)
    confirmed_count = pd.Series(0, index=df.index, dtype=int)
    dirs_cols: dict[str, pd.Series] = {}
    for w in mas:
        col = f"ma{w}"
        if col not in df.columns:
            raise ValueError(f"缺少均线列 {col}")
        above = close > df[col].astype(float)
        above_prev = above.shift(1)
        confirmed = above & above_prev & above_prev.notna()
        below = close < df[col].astype(float)
        below_prev = below.shift(1)
        confirmed_below = below & below_prev & below_prev.notna()
        confirmed_count = confirmed_count + confirmed.astype(int)
        dirs_cols[f"MA{w}"] = np.where(
            confirmed, "上", np.where(confirmed_below, "下", "未确认")
        )

    out = pd.DataFrame({
        "trend_level": confirmed_count.astype(int),
        "trend_base": confirmed_count.astype(int).map(lambda x: _layer_weight(x, layers)),
    }, index=df.index)
    for k, v in dirs_cols.items():
        out[f"dir_{k}"] = v
    out["trend_dirs"] = out[[f"dir_{k}" for k in dirs_cols]].apply(
        lambda r: " / ".join(f"{k}:{r[f'dir_{k}']}" for k in dirs_cols), axis=1
    )
    return out


def trend_result(df: pd.DataFrame, idx: int, cfg: dict,
                 ma_set: Optional[list[int]] = None) -> TrendResult:
    """单日趋势结果（含与上一交易日的对比说明）。"""
    series = trend_series(df, cfg, ma_set)
    i = len(df) - 1 if idx is None else idx
    row = series.iloc[i]
    prev_level = int(series.iloc[i - 1]["trend_level"]) if i > 0 else 0
    level = int(row["trend_level"])

    mas = ma_set or [20, 60, 120]
    dirs = {f"MA{w}": str(row[f"dir_MA{w}"]) for w in mas}
    confirmed = [k for k, v in dirs.items() if v == "上"]

    changed = level != prev_level
    if not changed:
        reason = f"确认站上的均线条数维持 {level} 条，基础仓位不变"
    elif level > prev_level:
        reason = f"确认站上的均线条数由 {prev_level} 增至 {level}，基础仓位上调"
    else:
        reason = f"确认站上的均线条数由 {prev_level} 降至 {level}，基础仓位下调"

    return TrendResult(
        level=level,
        base_weight=float(row["trend_base"]),
        dirs=dirs,
        confirmed_mas=confirmed,
        changed=changed,
        change_reason=reason,
    )


# --------------------------------------------------------------------------- #
# 第二层：波动率仓位上限
# --------------------------------------------------------------------------- #
def vol_cap(vol20: float, vol60: float, cfg: dict) -> float:
    """vol_cap = min(max_position, target / effective_vol)。"""
    vcfg = cfg.get("volatility", {})
    rcfg = cfg.get("risk", {})
    target = float(vcfg.get("target", 0.12))
    floor = float(vcfg.get("floor_effective_vol", 0.18))
    max_pos = float(rcfg.get("max_position", 0.65))
    ev = effective_volatility(vol20, vol60, floor)
    if np.isnan(ev) or ev <= 0:
        return 0.0
    return float(min(max_pos, target / ev))


# --------------------------------------------------------------------------- #
# 第三层：极端超卖反弹状态机
# --------------------------------------------------------------------------- #
STATES = ("NORMAL", "OVERSOLD_WATCH", "RECOVERY_1", "RECOVERY_2", "EXPIRED")


@dataclass
class OversoldEvent:
    event_id: str
    trigger_idx: int
    trigger_date: str
    trigger_price: float
    lowest_close: float
    state: str = "OVERSOLD_WATCH"
    tactical: float = 0.0
    activate_idx: Optional[int] = None
    activate_date: Optional[str] = None
    exit_idx: Optional[int] = None
    exit_date: Optional[str] = None
    exit_reason: Optional[str] = None
    confirm_idx: Optional[int] = None
    confirm_date: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "trigger_date": self.trigger_date,
            "trigger_price": self.trigger_price,
            "lowest_close": self.lowest_close,
            "state": self.state,
            "tactical_weight": self.tactical,
            "activate_date": self.activate_date,
            "exit_date": self.exit_date,
            "exit_reason": self.exit_reason,
        }


def _two_day_above(close: np.ndarray, ma: np.ndarray, i: int) -> bool:
    if i < 1:
        return False
    a, b = close[i], close[i - 1]
    ma_a, ma_b = ma[i], ma[i - 1]
    if any(np.isnan(x) for x in (a, b, ma_a, ma_b)):
        return False
    return bool(a > ma_a and b > ma_b)


def oversold_series(df: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, list[dict], list[dict]]:
    """运行超卖事件状态机。

    返回 (逐日状态表, 事件明细, 每日超卖权重)。
    状态迁移：NORMAL -> OVERSOLD_WATCH -> RECOVERY_1 -> RECOVERY_2 -> EXPIRED
    """
    ocfg = cfg.get("oversold", {})
    rsi_trig = float(ocfg.get("rsi_trigger", 20))
    dev_thr = float(ocfg.get("dev_ma20", -0.08))
    confirm_window = int(ocfg.get("confirm_window", 10))
    rsi_rebound = float(ocfg.get("rsi_rebound_level", 30))
    w1 = float(ocfg.get("weight_step1", 0.05))
    w2 = float(ocfg.get("weight_step2", 0.10))
    hold_days = int(ocfg.get("exit_hold_days", 10))
    exit_rsi = float(ocfg.get("exit_rsi", 65))
    cooldown = int(ocfg.get("cooldown_days", 20))

    n = len(df)
    close = df["close_adj"].to_numpy(dtype=float)
    ma5 = df["ma5"].to_numpy(dtype=float)
    ma10 = df["ma10"].to_numpy(dtype=float)
    ma20 = df["ma20"].to_numpy(dtype=float)
    rsi6 = df["rsi6"].to_numpy(dtype=float)
    dates = df["trade_date"].astype(str).tolist()

    states = ["NORMAL"] * n
    weights = np.zeros(n, dtype=float)
    event_ids: list[Optional[str]] = [None] * n
    lowest_arr = np.full(n, np.nan)
    events: list[OversoldEvent] = []

    active: Optional[OversoldEvent] = None
    cooldown_until = -1
    counter: dict[str, int] = {}

    def close_event(i: int, reason: str) -> None:
        nonlocal active, cooldown_until
        assert active is not None
        active.exit_idx = i
        active.exit_date = dates[i]
        active.exit_reason = reason
        active.state = "EXPIRED"
        active.tactical = 0.0
        cooldown_until = i + cooldown
        active = None

    for i in range(n):
        c = close[i]
        if np.isnan(c) or np.isnan(ma20[i]) or np.isnan(rsi6[i]):
            if active is not None:
                states[i] = active.state
                weights[i] = active.tactical
                event_ids[i] = active.event_id
                lowest_arr[i] = active.lowest_close
            continue

        if active is None:
            trigger = (
                i > cooldown_until
                and rsi6[i] <= rsi_trig
                and c <= ma20[i] * (1.0 + dev_thr)
            )
            if trigger:
                key = dates[i]
                counter[key] = counter.get(key, 0) + 1
                eid = f"OS-{key}" + (f"-{counter[key]}" if counter[key] > 1 else "")
                active = OversoldEvent(
                    event_id=eid, trigger_idx=i, trigger_date=key,
                    trigger_price=float(c), lowest_close=float(c),
                )
                events.append(active)
            else:
                states[i] = "NORMAL"
                continue

        # ---- 已有事件：先判退出（使用「此前」记录的最低收盘价） ----
        if active.state in ("RECOVERY_1", "RECOVERY_2"):
            hold = i - int(active.activate_idx)
            if hold >= hold_days:
                close_event(i, f"持有满 {hold_days} 个交易日")
            elif rsi6[i] >= exit_rsi:
                close_event(i, f"RSI6={rsi6[i]:.1f} >= {exit_rsi:g}")
            elif c < active.lowest_close:
                close_event(i, f"收盘价 {c:.3f} 跌破事件最低收盘价 {active.lowest_close:.3f}")

        if active is None:
            states[i] = "EXPIRED"
            weights[i] = 0.0
            continue

        if active.state == "OVERSOLD_WATCH":
            if i - active.trigger_idx > confirm_window:
                close_event(i, f"触发后 {confirm_window} 个交易日内未完成初步反弹确认")
                states[i] = "EXPIRED"
                continue
            rsi_cross = (i >= 1 and not np.isnan(rsi6[i - 1])
                         and rsi6[i - 1] <= rsi_rebound < rsi6[i])
            if rsi_cross and _two_day_above(close, ma5, i):
                active.state = "RECOVERY_1"
                active.tactical = w1
                active.activate_idx = i
                active.activate_date = dates[i]
                active.confirm_idx = i
                active.confirm_date = dates[i]
        elif active.state == "RECOVERY_1":
            if _two_day_above(close, ma10, i):
                active.state = "RECOVERY_2"
                active.tactical = w2

        # ---- 更新事件最低收盘价（在退出判定之后） ----
        if c < active.lowest_close:
            active.lowest_close = float(c)

        states[i] = active.state
        weights[i] = active.tactical
        event_ids[i] = active.event_id
        lowest_arr[i] = active.lowest_close

    out = pd.DataFrame({
        "os_state": states,
        "os_weight": weights,
        "os_event_id": event_ids,
        "os_lowest": lowest_arr,
    }, index=df.index)
    return out, [e.as_dict() for e in events], events


def event_at_date(events: list[OversoldEvent], trade_date: str) -> Optional[OversoldEvent]:
    """返回覆盖该交易日的最近一个超卖事件。"""
    hit = None
    for e in events:
        if e.trigger_date <= trade_date and (e.exit_date is None or trade_date <= e.exit_date):
            hit = e
    return hit


# --------------------------------------------------------------------------- #
# 第四层：危机开关
# --------------------------------------------------------------------------- #
@dataclass
class CrisisState:
    active: bool
    trigger_date: Optional[str]
    release_date: Optional[str]
    probe_active: bool
    probe_weight: float
    cap: float
    reason: str

    def as_dict(self) -> dict:
        return {
            "active": self.active,
            "trigger_date": self.trigger_date,
            "release_date": self.release_date,
            "probe_active": self.probe_active,
            "probe_weight": self.probe_weight,
            "cap": self.cap,
            "reason": self.reason,
        }


def crisis_series(df: pd.DataFrame, os_weight: pd.Series, cfg: dict
                  ) -> tuple[pd.DataFrame, list[dict]]:
    """危机开关状态机（带触发的 latch，避免同日反复切换）。

    触发：close/MA20-1 <= -8% 且 vol20 > 35%
    解除：close/MA20-1 > -8% 或 vol20 <= 35%
    危机中 cap=0；若超卖反弹确认激活战术仓，允许最多 probe_max 的试探仓。
    """
    ccfg = cfg.get("crisis", {})
    dev_thr = float(ccfg.get("dev_ma20", -0.08))
    vol_thr = float(ccfg.get("vol20_threshold", 0.35))
    probe_max = float(ccfg.get("probe_max", 0.10))

    n = len(df)
    dev = df["dev20"].to_numpy(dtype=float)
    vol20 = df["vol20"].to_numpy(dtype=float)
    os_w = os_weight.to_numpy(dtype=float)
    dates = df["trade_date"].astype(str).tolist()

    active_flags = np.zeros(n, dtype=bool)
    probe_flags = np.zeros(n, dtype=bool)
    caps = np.ones(n, dtype=float)
    events: list[dict] = []

    active = False
    cur: Optional[dict] = None

    for i in range(n):
        can_eval = not (np.isnan(dev[i]) or np.isnan(vol20[i]))
        if not active:
            if can_eval and dev[i] <= dev_thr and vol20[i] > vol_thr:
                active = True
                cur = {
                    "event_id": f"CR-{dates[i]}",
                    "trigger_date": dates[i],
                    "release_date": None,
                    "probe_date": None,
                    "probe_fail_date": None,
                    "status": "ACTIVE",
                }
                events.append(cur)
        else:
            if can_eval and (dev[i] > dev_thr or vol20[i] <= vol_thr):
                active = False
                cur["release_date"] = dates[i]
                cur["status"] = "RELEASED"
                cur = None

        active_flags[i] = active
        if active:
            probe = os_w[i] > 0
            probe_flags[i] = probe
            caps[i] = min(probe_max, os_w[i]) if probe else 0.0
            if probe and cur is not None and cur["probe_date"] is None:
                cur["probe_date"] = dates[i]
            if (not probe) and cur is not None and cur["probe_date"] and \
                    cur["probe_fail_date"] is None and os_w[i] == 0:
                # 试探仓因超卖事件退出而撤出
                cur["probe_fail_date"] = dates[i]
        else:
            caps[i] = 1.0

    out = pd.DataFrame({
        "crisis_active": active_flags,
        "crisis_probe": probe_flags,
        "crisis_cap": caps,
    }, index=df.index)
    return out, events


def crisis_state_at(df: pd.DataFrame, crisis_df: pd.DataFrame, idx: int,
                    cfg: dict) -> CrisisState:
    i = len(df) - 1 if idx is None else idx
    row = crisis_df.iloc[i]
    active = bool(row["crisis_active"])
    probe = bool(row["crisis_probe"])
    dev = df["dev20"].iloc[i]
    vol20 = df["vol20"].iloc[i]
    trigger_date = None
    for j in range(i, -1, -1):
        if not bool(crisis_df.iloc[j]["crisis_active"]):
            break
        trigger_date = str(df["trade_date"].iloc[j])
    if active:
        reason = (f"收盘价低于 MA20 {dev * 100:.2f}% 且 vol20={vol20 * 100:.2f}% > "
                  f"{float(cfg.get('crisis', {}).get('vol20_threshold', 0.35)) * 100:.0f}%，危机开关开启")
    else:
        reason = "危机条件未同时满足（或已解除）"
    return CrisisState(
        active=active,
        trigger_date=trigger_date,
        release_date=None if active else (
            str(df["trade_date"].iloc[i - 1]) if i > 0 else None
        ),
        probe_active=probe,
        probe_weight=float(row["crisis_cap"]) if probe else 0.0,
        cap=float(row["crisis_cap"]),
        reason=reason,
    )


# --------------------------------------------------------------------------- #
# 汇总：市场模型
# --------------------------------------------------------------------------- #
def compute_market(df: pd.DataFrame, cfg: dict,
                   ma_set: Optional[list[int]] = None) -> tuple[pd.DataFrame, dict]:
    """返回逐日市场信号表与事件明细。

    表内 market_final = min(trend_base + os_weight, vol_cap, crisis_cap, max_position)
    """
    if df.empty:
        raise ValueError("行情为空，无法计算市场信号")

    trend = trend_series(df, cfg, ma_set)
    os_df, os_events_dict, os_events = oversold_series(df, cfg)
    crisis_df, crisis_events = crisis_series(df, os_df["os_weight"], cfg)
    crisis_df["crisis_trigger"] = (
        crisis_df["crisis_active"].astype(bool)
        & ~crisis_df["crisis_active"].shift(1, fill_value=False).astype(bool)
    )

    out = pd.concat([df, trend, os_df, crisis_df], axis=1)

    max_pos = float(cfg.get("risk", {}).get("max_position", 0.65))
    out["vol_cap"] = [
        vol_cap(v20, v60, cfg)
        for v20, v60 in zip(out["vol20"].to_numpy(), out["vol60"].to_numpy())
    ]
    raw = out["trend_base"].astype(float) + out["os_weight"].astype(float)
    out["raw_weight"] = raw
    out["market_final"] = np.minimum.reduce([
        raw.to_numpy(dtype=float),
        out["vol_cap"].to_numpy(dtype=float),
        out["crisis_cap"].to_numpy(dtype=float),
        np.full(len(out), max_pos),
    ])
    out["market_final"] = np.clip(out["market_final"], 0.0, max_pos)

    events = {
        "oversold": os_events_dict,
        "crisis": crisis_events,
        "_oversold_objects": os_events,
    }
    return out, events


def binding_constraint(trend_base: float, os_weight: float, vol_cap_value: float,
                       crisis_cap_value: float, dd_cap_value: float,
                       max_position: float, breaker_cap: Optional[float] = None) -> tuple[float, str]:
    """按「取最严格上限」原则确定最终仓位，并返回决定该仓位的约束名。"""
    raw = float(trend_base) + float(os_weight)
    candidates = {
        "策略组合(趋势+超卖)": raw,
        "波动率上限": float(vol_cap_value),
        "危机开关上限": float(crisis_cap_value),
        "账户回撤上限": float(dd_cap_value),
        "系统仓位上限": float(max_position),
    }
    if breaker_cap is not None:
        candidates["回撤熔断恢复上限"] = float(breaker_cap)
    name, value = min(candidates.items(), key=lambda kv: kv[1])
    return float(max(0.0, value)), name
