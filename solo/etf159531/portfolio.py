"""账户数据（快照 / 出入金 / 交易流水）读取校验，以及交易建议计算。

仅在用户提供 account/snapshot.csv 时才进入「账户模式」；
否则只能计算市场模型仓位，报告须明确标注「仅市场信号模式」。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from settings import resolve_path

SNAPSHOT_COLS = ["trade_date", "cash", "etf_shares", "other_assets"]
FLOW_COLS = ["trade_date", "amount", "note"]
TRADE_COLS = ["trade_date", "action", "shares", "price", "fee", "note"]


@dataclass
class AccountData:
    snapshot: pd.DataFrame
    flows: pd.DataFrame
    trades: pd.DataFrame
    source_dir: Path
    warnings: list[str] = field(default_factory=list)


def _read_csv(path: Path, required: list[str], name: str) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=required)
    df = pd.read_csv(path, dtype={"trade_date": str}, encoding="utf-8-sig")
    df.columns = [c.strip() for c in df.columns]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"{name} 缺少字段 {missing}，请参考 account/ 目录下的模板")
    df["trade_date"] = df["trade_date"].astype(str).str.replace("-", "", regex=False).str[:8]
    if df["trade_date"].str.len().ne(8).any():
        raise ValueError(f"{name} 存在非法 trade_date（需 YYYYMMDD）")
    return df


def load_account(cfg: dict) -> Optional[AccountData]:
    """读取账户数据；若不存在 snapshot.csv 返回 None（仅市场信号模式）。"""
    acfg = cfg.get("account", {})
    base = resolve_path(cfg, acfg.get("dir", "account"))
    snap_path = base / acfg.get("snapshot_file", "snapshot.csv")
    if not snap_path.exists():
        return None

    warnings: list[str] = []
    snap = _read_csv(snap_path, ["trade_date", "cash", "etf_shares"], "account/snapshot.csv")
    flows = _read_csv(base / acfg.get("cashflow_file", "cashflows.csv"), FLOW_COLS,
                      "account/cashflows.csv")
    trades = _read_csv(base / acfg.get("trades_file", "trades.csv"), TRADE_COLS,
                       "account/trades.csv")

    if snap.empty:
        raise ValueError("account/snapshot.csv 为空")
    if "other_assets" not in snap.columns:
        snap["other_assets"] = 0.0

    for c in ("cash", "etf_shares", "other_assets"):
        snap[c] = pd.to_numeric(snap[c], errors="coerce")
        if snap[c].isna().any():
            raise ValueError(f"account/snapshot.csv 的 {c} 存在非数值或空值")
        if (snap[c] < 0).any():
            raise ValueError(f"account/snapshot.csv 的 {c} 不能为负")

    snap = snap.sort_values("trade_date").drop_duplicates("trade_date", keep="last")
    if snap["trade_date"].duplicated().any():
        warnings.append("snapshot.csv 存在重复日期，已保留最后一条")

    if not flows.empty:
        flows["amount"] = pd.to_numeric(flows["amount"], errors="coerce")
        if flows["amount"].isna().any():
            raise ValueError("account/cashflows.csv 的 amount 存在非数值")
    if not trades.empty:
        for c in ("shares", "price", "fee"):
            trades[c] = pd.to_numeric(trades[c], errors="coerce").fillna(0.0)

    return AccountData(snapshot=snap, flows=flows, trades=trades,
                       source_dir=base, warnings=warnings)


def latest_account_state(account: AccountData, close_price: float) -> dict:
    """最新一条账户快照的净值、持仓市值与仓位。"""
    row = account.snapshot.sort_values("trade_date").iloc[-1]
    etf_value = float(row["etf_shares"]) * float(close_price)
    nav = float(row["cash"]) + etf_value + float(row.get("other_assets", 0.0) or 0.0)
    weight = etf_value / nav if nav > 0 else 0.0
    return {
        "snapshot_date": str(row["trade_date"]),
        "cash": float(row["cash"]),
        "shares": float(row["etf_shares"]),
        "other_assets": float(row.get("other_assets", 0.0) or 0.0),
        "etf_value": etf_value,
        "nav": nav,
        "current_weight": weight,
    }


@dataclass
class TradePlan:
    action: str                 # BUY / SELL / HOLD
    current_weight: float
    target_weight: float
    target_shares: float
    delta_shares: float
    exec_shares: float
    ref_price: float
    est_amount: float
    note: str

    def as_dict(self) -> dict:
        return {
            "action": self.action,
            "current_weight": self.current_weight,
            "target_weight": self.target_weight,
            "target_shares": self.target_shares,
            "delta_shares": self.delta_shares,
            "exec_shares": self.exec_shares,
            "ref_price": self.ref_price,
            "est_amount": self.est_amount,
            "note": self.note,
        }


def plan_trade(target_weight: float, state: dict, ref_price: float, cfg: dict) -> TradePlan:
    """由目标仓位推导建议买卖份额。

    - 目标份额 = 目标仓位 × 账户净值 / 参考价
    - 买入按 lot_size 向下取整，并受现金约束；卖出按 lot_size 向下取整，并受持仓约束
    - 仅用于估算；实际成交价变化后须重算
    """
    tcfg = cfg.get("trading", {})
    lot = int(tcfg.get("lot_size", 100))
    cost = float(tcfg.get("commission", 0.0005)) + float(tcfg.get("slippage", 0.0005))

    nav = float(state["nav"])
    shares = float(state["shares"])
    cash = float(state["cash"])
    cur_w = float(state["current_weight"])
    target_weight = max(0.0, min(1.0, float(target_weight)))
    if ref_price <= 0:
        raise ValueError("参考价必须为正")

    target_value = target_weight * nav
    target_shares = target_value / ref_price
    delta = target_shares - shares

    if delta > 0:
        affordable = cash / (ref_price * (1.0 + cost)) if cash > 0 else 0.0
        buy = np.floor(min(delta, affordable) / lot) * lot
        if buy <= 0:
            return TradePlan("HOLD", cur_w, target_weight, target_shares, delta, 0.0,
                             ref_price, 0.0, "可用现金不足一个交易单位，暂不买入")
        return TradePlan("BUY", cur_w, target_weight, target_shares, delta, float(buy),
                         ref_price, float(buy * ref_price),
                         f"买入 {int(buy)} 份（按 {lot} 份整数倍取整）")
    if delta < 0:
        sell = np.floor(min(-delta, shares) / lot) * lot
        if sell <= 0:
            return TradePlan("HOLD", cur_w, target_weight, target_shares, delta, 0.0,
                             ref_price, 0.0, "减仓差额不足一个交易单位，暂不卖出")
        return TradePlan("SELL", cur_w, target_weight, target_shares, delta, -float(sell),
                         ref_price, float(sell * ref_price),
                         f"卖出 {int(sell)} 份（按 {lot} 份整数倍取整）")
    return TradePlan("HOLD", cur_w, target_weight, target_shares, 0.0, 0.0,
                     ref_price, 0.0, "目标与当前份额一致")


def is_week_last(trade_date: str, next_trade_date: Optional[str]) -> bool:
    """是否为该周最后一个交易日（用日历给出的下一交易日判断，避免把周三当成周末）。"""
    if not next_trade_date:
        return True
    d = pd.Timestamp(trade_date)
    nd = pd.Timestamp(next_trade_date)
    return (d.isocalendar().week != nd.isocalendar().week) or (d.year != nd.year)


def should_rebalance(target_weight: float, current_weight: float, cfg: dict,
                     force: bool = False) -> tuple[bool, str]:
    """周度再平衡 + 5 个百分点阈值；风险降低指令强制优先执行。"""
    tcfg = cfg.get("trading", {})
    gap = abs(target_weight - current_weight)
    if force:
        return True, "风险控制指令，优先执行"
    if target_weight < current_weight - 1e-9:
        return True, "目标仓位低于当前仓位（降风险指令优先执行，不受周度阈值限制）"
    min_gap = float(tcfg.get("min_trade_gap", 0.05))
    if gap + 1e-12 < min_gap:
        return False, f"目标与当前相差 {gap * 100:.2f} 个百分点，小于 {min_gap * 100:.0f}% 阈值"
    return True, f"目标与当前相差 {gap * 100:.2f} 个百分点，达到调仓阈值"


def ensure_templates(cfg: dict) -> list[Path]:
    """生成账户 CSV 模板（不会覆盖已存在的真实文件）。"""
    acfg = cfg.get("account", {})
    base = resolve_path(cfg, acfg.get("dir", "account"))
    base.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []

    tpl = {
        "template_snapshot.csv": (
            SNAPSHOT_COLS,
            [["20260101", "100000.00", "0", "0.00"]],
        ),
        "template_cashflows.csv": (
            FLOW_COLS,
            [["20260101", "100000.00", "初始入金"]],
        ),
        "template_trades.csv": (
            TRADE_COLS,
            [["20260101", "BUY", "1000", "1.000", "0.50", "示例行，请删除"]],
        ),
    }
    for name, (cols, rows) in tpl.items():
        path = base / name
        if path.exists():
            continue
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(cols)
            w.writerows(rows)
        created.append(path)
    return created


def validate_trades_consistency(account: AccountData, tolerance_shares: float = 1.0) -> list[str]:
    """交易流水与快照份额的粗略一致性检查（仅提示，不阻断）。"""
    issues: list[str] = []
    if account.trades.empty or account.snapshot.empty:
        return issues
    trades = account.trades.sort_values("trade_date")
    net = 0.0
    for _, r in trades.iterrows():
        if str(r["action"]).upper() == "BUY":
            net += float(r["shares"])
        elif str(r["action"]).upper() == "SELL":
            net -= float(r["shares"])
    snap_net = float(account.snapshot.sort_values("trade_date").iloc[-1]["etf_shares"])
    if abs(net - snap_net) > tolerance_shares:
        issues.append(
            f"交易流水累计份额 {net:.0f} 与最新快照份额 {snap_net:.0f} 不一致，请核对"
        )
    return issues
