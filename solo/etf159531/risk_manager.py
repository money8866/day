"""第五层：账户净值回撤风控 + 回撤熔断恢复状态机。

净值口径
- NAV = 现金 + ETF 份额 × 当日未复权收盘价 + 其他已登记资产
- 单位净值按外部现金流（出入金）调整，避免出入金造成虚假回撤：
  unit_nav_t = unit_nav_{t-1} × (NAV_t - flow_t) / NAV_{t-1}
- drawdown_t = unit_nav_t / 历史最高单位净值 - 1

未提供账户数据时不计算账户回撤，相关上限不生效（由调用方标记「仅市场信号模式」）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class RiskLimits:
    drawdown: Optional[float]
    dd_cap: float
    breaker_stage: str
    breaker_cap: Optional[float]
    account_cap: float
    note: str

    def as_dict(self) -> dict:
        return {
            "drawdown": self.drawdown,
            "dd_cap": self.dd_cap,
            "breaker_stage": self.breaker_stage,
            "breaker_cap": self.breaker_cap,
            "account_cap": self.account_cap,
            "note": self.note,
        }


STAGE_CN = {
    "NORMAL": "正常",
    "COOLDOWN": "熔断冷静期",
    "RESTART": "熔断恢复期（≤10%）",
    "STEP2": "熔断恢复期（≤25%）",
    "UNDERWATER": "回撤观察",
}


def build_account_nav(snapshot: pd.DataFrame, price: pd.DataFrame,
                      flows: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """构造逐交易日的 NAV / 单位净值 / 回撤序列。

    snapshot: trade_date, cash, etf_shares, other_assets（可稀疏，按 as-of 前向填充）
    price:    trade_date, close（未复权收盘）
    flows:    trade_date, amount（外部净流入，正=入金，负=出金）
    """
    if snapshot is None or snapshot.empty:
        raise ValueError("账户快照为空")
    snap = snapshot.copy()
    snap["trade_date"] = snap["trade_date"].astype(str)
    snap = snap.sort_values("trade_date").drop_duplicates("trade_date", keep="last")
    if "other_assets" not in snap.columns:
        snap["other_assets"] = 0.0
    for c in ("cash", "etf_shares", "other_assets"):
        snap[c] = pd.to_numeric(snap[c], errors="coerce").fillna(0.0)

    px = price.copy()
    px["trade_date"] = px["trade_date"].astype(str)
    px = px.sort_values("trade_date")
    px = px[["trade_date", "close"]].drop_duplicates("trade_date", keep="last")

    start, end = snap["trade_date"].min(), snap["trade_date"].max()
    days = px.loc[(px["trade_date"] >= start) & (px["trade_date"] <= end), "trade_date"].tolist()
    # 快照日期若不在行情日（周末录入），并入最近的下一个交易日
    for d in snap["trade_date"]:
        if d not in days:
            nxt = px.loc[px["trade_date"] >= d, "trade_date"]
            if not nxt.empty:
                days.append(str(nxt.iloc[0]))
    days = sorted(set(days))
    if not days:
        raise ValueError("账户快照区间内没有可用行情")

    base = pd.DataFrame({"trade_date": days})
    base = base.merge(snap, on="trade_date", how="left")
    base[["cash", "etf_shares", "other_assets"]] = (
        base[["cash", "etf_shares", "other_assets"]].ffill()
    )
    base[["cash", "etf_shares", "other_assets"]] = (
        base[["cash", "etf_shares", "other_assets"]].fillna(0.0)
    )
    base = base.merge(px, on="trade_date", how="left")
    base["close"] = base["close"].ffill()

    if flows is None or flows.empty:
        base["flow"] = 0.0
    else:
        fl = flows.copy()
        fl["trade_date"] = fl["trade_date"].astype(str)
        fl["amount"] = pd.to_numeric(fl["amount"], errors="coerce").fillna(0.0)
        agg = fl.groupby("trade_date", as_index=False)["amount"].sum()
        base = base.merge(agg.rename(columns={"amount": "flow"}), on="trade_date", how="left")
        base["flow"] = base["flow"].fillna(0.0)

    base["nav"] = (base["cash"] + base["etf_shares"] * base["close"]
                   + base["other_assets"])
    if base["nav"].iloc[0] <= 0:
        raise ValueError("首日账户净值为 0，无法计算单位净值，请检查快照")

    unit = np.ones(len(base), dtype=float)
    navs = base["nav"].to_numpy(dtype=float)
    flows_arr = base["flow"].to_numpy(dtype=float)
    for i in range(1, len(base)):
        prev_nav = navs[i - 1]
        if prev_nav <= 0:
            unit[i] = unit[i - 1]
            continue
        unit[i] = unit[i - 1] * (navs[i] - flows_arr[i]) / prev_nav
    base["unit_nav"] = unit
    base["peak_unit_nav"] = base["unit_nav"].cummax()
    base["drawdown"] = base["unit_nav"] / base["peak_unit_nav"] - 1.0
    return base


def drawdown_cap(drawdown: Optional[float], cfg: dict) -> float:
    """回撤分层仓位上限。drawdown 为负数或 None。"""
    if drawdown is None or (isinstance(drawdown, float) and np.isnan(drawdown)):
        return 1.0
    tiers = cfg.get("risk", {}).get("drawdown_tiers", [])
    dd = abs(float(drawdown))
    cap = float(cfg.get("risk", {}).get("max_position", 0.65))
    for tier in sorted(tiers, key=lambda t: float(t["dd"])):
        if dd + 1e-12 >= float(tier["dd"]):
            cap = min(cap, float(tier["cap"]))
    return cap


def _rolling_all_true(series: pd.Series, n: int) -> pd.Series:
    return series.astype(float).rolling(n, min_periods=n).sum() >= n


class BreakerMachine:
    """回撤熔断恢复状态机（可逐日 step，也可整段回放）。

    NORMAL -> (dd <= -18%) COOLDOWN -> (冷静期 + 指数 3 日站上 MA20 + vol20<35%) RESTART(≤10%)
    -> (≥10 日 + 指数 3 日站上 MA60 + 无新增风控警报) STEP2(≤25%)
    回撤改善至 12% 以内 -> 回到 NORMAL。
    """

    def __init__(self, cfg: dict):
        b = cfg.get("risk", {}).get("breaker", {})
        self.cool = int(b.get("cooldown_days", 10))
        self.restart_max = float(b.get("restart_max", 0.10))
        self.restart_n = int(b.get("restart_confirm_days", 3))
        self.restart_vol = float(b.get("restart_vol_max", 0.35))
        self.step2_days = int(b.get("step2_days", 10))
        self.step2_n = int(b.get("step2_confirm_days", 3))
        self.step2_max = float(b.get("step2_max", 0.25))
        self.release_dd = float(b.get("release_dd", 0.12))
        self.trigger_dd = float(
            cfg.get("risk", {}).get("drawdown_tiers", [{}])[-1].get("dd", 0.18)
        )
        self.stage = "NORMAL"
        self.cool_start: Optional[int] = None
        self.restart_idx: Optional[int] = None
        self.alert_since_restart = False
        self._i = -1

    def step(self, dd: float, above_ma20_3: bool = False, above_ma60_3: bool = False,
             idx_vol20: float = float("nan"), alert: bool = False) -> tuple[str, float]:
        self._i += 1
        i = self._i

        if self.stage != "NORMAL" and dd > -self.release_dd:
            self.stage = "NORMAL"
            self.cool_start = None
            self.restart_idx = None
            self.alert_since_restart = False

        if self.stage == "NORMAL":
            cap = 1.0
            if dd <= -self.trigger_dd:
                self.stage = "COOLDOWN"
                self.cool_start = i
                cap = 0.0
        elif self.stage == "COOLDOWN":
            cap = 0.0
            cond_days = self.cool_start is not None and (i - self.cool_start) >= self.cool
            vol_ok = (not np.isnan(idx_vol20)) and float(idx_vol20) < self.restart_vol
            if cond_days and bool(above_ma20_3) and vol_ok:
                self.stage = "RESTART"
                self.restart_idx = i
                self.alert_since_restart = False
                cap = self.restart_max
        elif self.stage == "RESTART":
            cap = self.restart_max
            if alert:
                self.alert_since_restart = True
            cond_days = self.restart_idx is not None and (i - self.restart_idx) >= self.step2_days
            if cond_days and bool(above_ma60_3) and not self.alert_since_restart:
                self.stage = "STEP2"
                cap = self.step2_max
        elif self.stage == "STEP2":
            cap = self.step2_max
            if alert:
                self.alert_since_restart = True

        return self.stage, cap


def apply_breaker(nav_df: pd.DataFrame, cfg: dict,
                  index_feat: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """对账户净值序列逐日回放回撤熔断状态机。"""
    df = nav_df.copy().reset_index(drop=True)

    # ---- 指数门控（as-of 合并） ----
    gate_cols = ["above_ma20_3", "above_ma60_3", "idx_vol20", "crisis_trigger"]
    if index_feat is not None and not index_feat.empty:
        idx = index_feat.copy()
        idx["trade_date"] = idx["trade_date"].astype(str)
        idx["above_ma20"] = idx["index_close"] > idx["index_ma20"]
        idx["above_ma60"] = idx["index_close"] > idx["index_ma60"]
        idx = idx.sort_values("trade_date")
        idx["above_ma20_3"] = _rolling_all_true(
            idx["above_ma20"].astype(float), int(cfg.get("risk", {}).get("breaker", {})
                                                .get("restart_confirm_days", 3))).astype(bool)
        idx["above_ma60_3"] = _rolling_all_true(
            idx["above_ma60"].astype(float), int(cfg.get("risk", {}).get("breaker", {})
                                                .get("step2_confirm_days", 3))).astype(bool)
        idx["idx_vol20"] = idx["index_vol20"]
        if "crisis_trigger" not in idx.columns:
            idx["crisis_trigger"] = False
        idx["crisis_trigger"] = idx["crisis_trigger"].fillna(False).astype(bool)
        left = df.sort_values("trade_date").copy()
        right = idx[["trade_date"] + gate_cols].sort_values("trade_date").copy()
        left["_td"] = left["trade_date"].astype(int)
        right["_td"] = right["trade_date"].astype(int)
        df = pd.merge_asof(
            left, right.drop(columns=["trade_date"]),
            on="_td", direction="backward",
        ).drop(columns=["_td"])
    else:
        df["above_ma20_3"] = False
        df["above_ma60_3"] = False
        df["idx_vol20"] = np.nan
        df["crisis_trigger"] = False

    machine = BreakerMachine(cfg)
    stages: list[str] = []
    caps: list[float] = []
    for _, row in df.iterrows():
        stage, cap = machine.step(
            float(row["drawdown"]),
            bool(row.get("above_ma20_3", False)),
            bool(row.get("above_ma60_3", False)),
            float(row.get("idx_vol20", np.nan)),
            bool(row.get("crisis_trigger", False)),
        )
        stages.append(stage)
        caps.append(cap)

    df["breaker_stage"] = stages
    df["breaker_cap"] = caps
    return df


def account_risk(nav_df: Optional[pd.DataFrame], cfg: dict,
                 index_feat: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """合并「回撤分层上限」与「熔断恢复上限」，得到逐日账户风控结果。"""
    if nav_df is None or nav_df.empty:
        return pd.DataFrame(columns=["trade_date", "drawdown", "dd_cap", "breaker_stage",
                                     "breaker_cap", "account_cap", "note"])
    df = apply_breaker(nav_df, cfg, index_feat)
    df["dd_cap"] = [drawdown_cap(dd, cfg) for dd in df["drawdown"]]
    df["account_cap"] = np.minimum(df["dd_cap"], df["breaker_cap"])
    df["note"] = df["breaker_stage"].map(STAGE_CN).fillna(df["breaker_stage"])
    return df


def latest_limits(risk_df: pd.DataFrame, cfg: dict) -> RiskLimits:
    if risk_df is None or risk_df.empty:
        return RiskLimits(
            drawdown=None,
            dd_cap=1.0,
            breaker_stage="NORMAL",
            breaker_cap=None,
            account_cap=1.0,
            note="未提供账户数据，不计算账户回撤（仅市场信号模式）",
        )
    row = risk_df.iloc[-1]
    stage = str(row["breaker_stage"])
    breaker_cap = None if stage == "NORMAL" else float(row["breaker_cap"])
    cap = float(np.minimum(row["dd_cap"], row["breaker_cap"]))
    note = STAGE_CN.get(stage, stage)
    if stage == "NORMAL" and float(row["drawdown"]) < 0:
        note = f"回撤 {row['drawdown'] * 100:.2f}%，按分层上限约束"
    return RiskLimits(
        drawdown=float(row["drawdown"]),
        dd_cap=float(row["dd_cap"]),
        breaker_stage=stage,
        breaker_cap=breaker_cap,
        account_cap=cap,
        note=note,
    )
