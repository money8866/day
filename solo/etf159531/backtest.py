"""历史回测：真实 ETF 成交序列上的 A-E 策略对比与稳健性实验。

关键口径
- 信号在 t 日收盘后生成，最早 t+1 交易日开盘执行，绝不使用未来数据。
- 交易价格使用未复权实际开盘价；指标使用复权收盘价。
- 交易成本 = 佣金 + 滑点（单边），默认各 0.05%，可配置并做敏感性测试。
- 现金余额、ETF 交易单位（默认 100 份）、停牌无法成交、次日跳空均纳入模拟。
- 分红/拆分按复权因子变化折算为现金分配，保持总收益口径一致。
- 不使用 ETF 上市前的中证2000指数行情拼接为「可交易回测」。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

import indicators
import portfolio
import risk_manager
import strategy
from settings import now_cn, resolve_path

TRADING_DAYS = 252


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
@dataclass
class Variant:
    key: str
    name: str
    layers: set[str]
    fixed_weight: Optional[float] = None
    ma_set: Optional[list[int]] = None
    vol_target: Optional[float] = None
    use_oversold: bool = True
    commission: Optional[float] = None
    slippage: Optional[float] = None

    def with_(self, **kw) -> "Variant":
        d = self.__dict__.copy()
        d.update(kw)
        return Variant(**d)


BASE_VARIANTS = [
    Variant("A", "A. ETF 买入持有", set(), fixed_weight=1.00),
    Variant("B", "B. 固定 30% 仓位", set(), fixed_weight=0.30),
    Variant("C", "C. 仅趋势仓位", {"trend"}),
    Variant("D", "D. 趋势+波动率", {"trend", "vol"}),
    Variant("E", "E. 完整五层策略", {"trend", "vol", "oversold", "crisis", "drawdown"}),
]


# --------------------------------------------------------------------------- #
# 数据准备
# --------------------------------------------------------------------------- #
def _variant_cfg(cfg: dict, v: Variant) -> dict:
    """派生只改动波动率目标/成本的配置副本。"""
    import copy

    c = copy.deepcopy(cfg)
    if v.vol_target is not None:
        c.setdefault("volatility", {})["target"] = float(v.vol_target)
    if v.commission is not None:
        c.setdefault("backtest", {})["commission"] = float(v.commission)
    if v.slippage is not None:
        c.setdefault("backtest", {})["slippage"] = float(v.slippage)
    return c


def _index_gates(index_df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    if index_df is None or index_df.empty:
        return pd.DataFrame()
    b = cfg.get("risk", {}).get("breaker", {})
    n20 = int(b.get("restart_confirm_days", 3))
    n60 = int(b.get("step2_confirm_days", 3))
    d = index_df.sort_values("trade_date").reset_index(drop=True).copy()
    d["trade_date"] = d["trade_date"].astype(str)
    close = d["close"].astype(float)
    d["index_ma20"] = indicators.sma(close, 20)
    d["index_ma60"] = indicators.sma(close, 60)
    d["idx_vol20"] = indicators.annualized_vol(close, 20)
    above20 = (close > d["index_ma20"]).astype(float)
    above60 = (close > d["index_ma60"]).astype(float)
    d["above_ma20_3"] = above20.rolling(n20, min_periods=n20).sum() >= n20
    d["above_ma60_3"] = above60.rolling(n60, min_periods=n60).sum() >= n60
    return d[["trade_date", "index_ma20", "index_ma60", "idx_vol20",
              "above_ma20_3", "above_ma60_3"]]


def prepare(cfg: dict, etf: pd.DataFrame, index_df: Optional[pd.DataFrame],
            variant: Variant) -> pd.DataFrame:
    """计算特征 + 市场信号 + 指数门控，返回逐日决策表。"""
    vcfg = _variant_cfg(cfg, variant)
    features = indicators.compute_features(etf, vcfg)
    market, _ = strategy.compute_market(features, vcfg, variant.ma_set)
    if not variant.use_oversold:
        market["os_weight"] = 0.0
        market["crisis_cap"] = np.where(market["crisis_active"], 0.0, 1.0)
    gates = _index_gates(index_df, vcfg) if index_df is not None else pd.DataFrame()
    if not gates.empty:
        # merge_asof 要求数值型键，这里用 YYYYMMDD 整数排序后合并
        left = market.sort_values("trade_date").copy()
        right = gates.sort_values("trade_date").copy()
        left["_td"] = left["trade_date"].astype(int)
        right["_td"] = right["trade_date"].astype(int)
        market = pd.merge_asof(
            left, right.drop(columns=["trade_date"]),
            on="_td", direction="backward",
        ).drop(columns=["_td"])
    else:
        for c in ("above_ma20_3", "above_ma60_3"):
            market[c] = False
        market["idx_vol20"] = np.nan
        market["index_ma20"] = np.nan
        market["index_ma60"] = np.nan
    return market


def warmup_index(df: pd.DataFrame, variant: Variant) -> Optional[int]:
    """该策略正式可交易的首个索引（指标窗口完整）。"""
    if variant.fixed_weight is not None:
        return 0
    cols = ["vol60", "rsi14"]
    if variant.layers & {"trend", "vol", "oversold", "crisis"}:
        ma_set = variant.ma_set or [20, 60, 120]
        cols.append(f"ma{max(ma_set)}")
    if variant.layers & {"trend", "vol", "crisis"}:
        cols.append("dev20")
    cols = [c for c in cols if c in df.columns]
    mask = df[cols].notna().all(axis=1)
    if not mask.any():
        return None
    first = int(np.argmax(mask.to_numpy()))
    # 趋势需连续两日确认
    return min(first + 1, len(df) - 1)


# --------------------------------------------------------------------------- #
# 模拟
# --------------------------------------------------------------------------- #
def simulate(df: pd.DataFrame, cfg: dict, variant: Variant, start_idx: int,
             end_idx: Optional[int] = None,
             initial_cash: Optional[float] = None) -> dict:
    """逐日模拟，返回净值曲线与交易记录。"""
    bt = cfg.get("backtest", {})
    tcfg = cfg.get("trading", {})
    lot = int(tcfg.get("lot_size", 100))
    commission = float(variant.commission if variant.commission is not None
                       else bt.get("commission", 0.0005))
    slippage = float(variant.slippage if variant.slippage is not None
                     else bt.get("slippage", 0.0005))
    cash_yield = float(bt.get("cash_yield", 0.0))
    init_cash = float(initial_cash if initial_cash is not None else bt.get("initial_cash", 1e6))
    max_pos = float(cfg.get("risk", {}).get("max_position", 0.65))
    min_gap = float(tcfg.get("min_trade_gap", 0.05))
    weekly = bool(tcfg.get("weekly_rebalance", True))
    vol_target = float(variant.vol_target if variant.vol_target is not None
                       else cfg.get("volatility", {}).get("target", 0.12))
    floor_vol = float(cfg.get("volatility", {}).get("floor_effective_vol", 0.18))

    n = len(df)
    end_idx = n - 1 if end_idx is None else min(int(end_idx), n - 1)
    start_idx = max(0, int(start_idx))

    cash = init_cash
    shares = 0.0
    pending: Optional[float] = None
    machine = risk_manager.BreakerMachine(cfg) if "drawdown" in variant.layers else None
    peak_equity = init_cash

    records: list[dict] = []
    trades: list[dict] = []

    dates = df["trade_date"].astype(str).tolist()
    opens = df["open"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    adjs = df["adj_factor"].to_numpy(dtype=float)
    prev_closes = df["pre_close"].to_numpy(dtype=float) if "pre_close" in df else closes

    # 首日开盘前：用 start_idx-1 收盘信号决定初仓（不使用未来数据）
    if variant.fixed_weight is not None:
        pending = float(variant.fixed_weight)
    elif start_idx > 0:
        pending = _target_weight(df.iloc[start_idx - 1], cfg, variant, max_pos,
                                 vol_target, floor_vol, dd_cap=1.0, breaker_cap=None)

    for i in range(start_idx, end_idx + 1):
        # ---- 现金收益 ----
        if cash_yield:
            cash *= (1.0 + cash_yield / TRADING_DAYS)

        # ---- 公司行为：复权因子变化折算现金分配 ----
        if i > 0 and shares > 0 and not np.isnan(adjs[i]) and not np.isnan(adjs[i - 1]) \
                and adjs[i - 1] > 0 and abs(adjs[i] / adjs[i - 1] - 1.0) > 1e-10:
            ratio = adjs[i] / adjs[i - 1]
            px = closes[i] if not np.isnan(closes[i]) else prev_closes[i]
            div = px * (ratio - 1.0)
            if abs(div) > 1e-12:
                cash += shares * div

        open_px = opens[i]
        close_px = closes[i]

        # ---- 执行上一交易日收盘后生成的指令（今日开盘） ----
        if pending is not None and not np.isnan(open_px) and open_px > 0:
            equity_open = cash + shares * open_px
            target_w = float(np.clip(pending, 0.0, 1.0))
            notional_w = target_w * equity_open
            if target_w >= 0:
                px_buy = open_px * (1.0 + slippage)
                px_sell = open_px * (1.0 - slippage)
                target_shares = notional_w / open_px
                delta = target_shares - shares
                if delta > 0:
                    affordable = cash / (px_buy * (1.0 + commission)) if cash > 0 else 0.0
                    qty = np.floor(min(delta, affordable) / lot) * lot
                    if qty > 0:
                        cost = qty * px_buy * (1.0 + commission)
                        if cost <= cash + 1e-6:
                            cash -= cost
                            shares += qty
                            trades.append({"trade_date": dates[i], "side": "BUY",
                                           "shares": float(qty), "price": float(px_buy),
                                           "amount": float(cost)})
                elif delta < 0:
                    qty = np.floor(min(-delta, shares) / lot) * lot
                    if qty > 0:
                        proceeds = qty * px_sell * (1.0 - commission)
                        cash += proceeds
                        shares -= qty
                        trades.append({"trade_date": dates[i], "side": "SELL",
                                       "shares": float(qty), "price": float(px_sell),
                                       "amount": float(proceeds)})
            pending = None

        # ---- 今日收盘估值 ----
        px_close = close_px if not np.isnan(close_px) else (open_px if not np.isnan(open_px) else 0.0)
        equity = cash + shares * px_close
        if equity <= 0:
            equity = 1e-9
        peak_equity = max(peak_equity, equity)
        dd = equity / peak_equity - 1.0

        row = df.iloc[i]
        cur_w = (shares * px_close / equity) if equity > 0 else 0.0

        # ---- 计算今日收盘后的目标仓位（下一交易日开盘执行） ----
        bcap = None
        stage = "NORMAL"
        if machine is not None:
            crisis_today = bool(row.get("crisis_active", False))
            crisis_prev = bool(df.iloc[i - 1].get("crisis_active", False)) if i > 0 else False
            idx_vol = row.get("idx_vol20", np.nan)
            idx_vol = float("nan") if pd.isna(idx_vol) else float(idx_vol)
            stage, bcap = machine.step(
                dd,
                bool(row.get("above_ma20_3", False)),
                bool(row.get("above_ma60_3", False)),
                idx_vol,
                crisis_today and not crisis_prev,
            )
        dd_cap_value = risk_manager.drawdown_cap(dd, cfg) if "drawdown" in variant.layers else 1.0
        target = _target_weight(row, cfg, variant, max_pos, vol_target, floor_vol,
                                dd_cap=dd_cap_value, breaker_cap=bcap)

        records.append({
            "trade_date": dates[i], "equity": float(equity), "cash": float(cash),
            "shares": float(shares), "close": float(px_close), "target": float(target),
            "position": float(cur_w), "drawdown": float(dd),
            "dd_cap": float(dd_cap_value), "breaker_cap": bcap,
            "breaker_stage": stage,
        })

        # ---- 是否生成下一日交易指令 ----
        next_date = dates[i + 1] if i + 1 <= end_idx else None
        is_week_last = portfolio.is_week_last(dates[i], next_date)
        risk_cut = target < cur_w - 1e-9
        gap = abs(target - cur_w)
        due = risk_cut or (gap + 1e-12 >= min_gap)
        if weekly and not is_week_last and not risk_cut:
            due = False
        if due:
            pending = target

    curve = pd.DataFrame(records)
    trade_df = pd.DataFrame(trades)
    return {
        "curve": curve,
        "trades": trade_df,
        "variant": variant,
        "commission": commission,
        "slippage": slippage,
    }


def _target_weight(row: pd.Series, cfg: dict, variant: Variant, max_pos: float,
                   vol_target: float, floor_vol: float,
                   dd_cap: float, breaker_cap: Optional[float]) -> float:
    """按策略启用层计算目标仓位（行内只含 t 日及以前的信息）。"""
    if variant.fixed_weight is not None:
        return float(np.clip(variant.fixed_weight, 0.0, 1.0))

    raw = float(row["trend_base"])
    if "oversold" in variant.layers:
        raw += float(row.get("os_weight", 0.0))

    caps = [raw, max_pos]
    if "vol" in variant.layers:
        ev = float(row.get("effective_vol", np.nan))
        if not np.isnan(ev) and ev > 0:
            caps.append(min(max_pos, vol_target / max(ev, floor_vol)))
        else:
            caps.append(0.0)
    if "crisis" in variant.layers:
        caps.append(float(row.get("crisis_cap", 1.0)))
    if "drawdown" in variant.layers:
        caps.append(float(dd_cap))
        if breaker_cap is not None:
            caps.append(float(breaker_cap))
    return float(np.clip(min(caps), 0.0, max_pos))


# --------------------------------------------------------------------------- #
# 绩效指标
# --------------------------------------------------------------------------- #
def compute_metrics(curve: pd.DataFrame, trades: pd.DataFrame,
                    initial_cash: float) -> dict:
    if curve is None or curve.empty:
        return {}
    eq = pd.Series(curve["equity"].to_numpy(dtype=float),
                   index=pd.to_datetime(curve["trade_date"]))
    ret = eq.pct_change().iloc[1:]
    years = len(eq) / TRADING_DAYS
    final = float(eq.iloc[-1])
    cagr = (final / initial_cash) ** (1.0 / years) - 1.0 if years > 0 and final > 0 else np.nan
    dd = eq / eq.cummax() - 1.0
    mdd = float(dd.min())
    vol = float(ret.std(ddof=1) * np.sqrt(TRADING_DAYS)) if len(ret) > 2 else np.nan
    sharpe = float(ret.mean() / ret.std(ddof=1) * np.sqrt(TRADING_DAYS)) \
        if len(ret) > 2 and ret.std(ddof=1) > 0 else np.nan
    downside = ret[ret < 0]
    sortino = float(ret.mean() / downside.std(ddof=1) * np.sqrt(TRADING_DAYS)) \
        if len(downside) > 2 and downside.std(ddof=1) > 0 else np.nan
    calmar = float(cagr / abs(mdd)) if mdd < 0 and not np.isnan(cagr) else np.nan

    yearly = ((1.0 + ret).groupby(ret.index.year).prod() - 1.0) if len(ret) else pd.Series(dtype=float)
    monthly = ((1.0 + ret).groupby([ret.index.year, ret.index.month]).prod() - 1.0) \
        if len(ret) else pd.Series(dtype=float)
    worst_year = float(yearly.min()) if len(yearly) else np.nan
    pos_month_ratio = float((monthly > 0).mean()) if len(monthly) else np.nan

    turnover = np.nan
    avg_pos = float(curve["position"].mean()) if "position" in curve else np.nan
    if trades is not None and not trades.empty:
        traded = float(trades["amount"].abs().sum())
        turnover = traded / float(eq.mean()) / years if years > 0 else np.nan

    # 最长回撤修复时间（交易日）
    peak_idx = 0
    longest = 0
    cur_peak_i = 0
    for i, v in enumerate(eq.to_numpy(dtype=float)):
        if v >= eq.iloc[cur_peak_i]:
            cur_peak_i = i
            continue
        longest = max(longest, i - cur_peak_i)
    recovered = eq.iloc[-1] >= eq.cummax().iloc[-1] - 1e-12
    longest_dd_days = int(longest)

    return {
        "start": str(curve["trade_date"].iloc[0]),
        "end": str(curve["trade_date"].iloc[-1]),
        "days": int(len(eq)),
        "final_equity": final,
        "total_return": final / initial_cash - 1.0,
        "cagr": cagr,
        "mdd": mdd,
        "calmar": calmar,
        "sharpe": sharpe,
        "sortino": sortino,
        "volatility": vol,
        "worst_year": worst_year,
        "positive_month_ratio": pos_month_ratio,
        "yearly_returns": {int(k): float(v) for k, v in yearly.items()},
        "trades": int(len(trades)) if trades is not None else 0,
        "turnover": turnover,
        "avg_position": avg_pos,
        "longest_drawdown_days": longest_dd_days,
        "drawdown_recovered": bool(recovered),
    }


def _fmt_metrics_table(rows: list[tuple[str, dict]]) -> str:
    head = ("| 策略 | 区间 | 年化 | 最大回撤 | Calmar | Sharpe | Sortino | 波动率 | "
            "最差单年 | 正收益月占比 | 交易次数 | 年化换手 | 平均仓位 | 最长回撤(日) |")
    sep = "|" + "---|" * 14
    lines = [head, sep]
    for name, m in rows:
        if not m:
            lines.append(f"| {name} | — | — | — | — | — | — | — | — | — | — | — | — | — |")
            continue
        lines.append(
            f"| {name} | {m['start']}~{m['end']} | {m['cagr']:.2%} | {m['mdd']:.2%} | "
            f"{m['calmar']:.2f} | {m['sharpe']:.2f} | {m['sortino']:.2f} | "
            f"{m['volatility']:.2%} | {m['worst_year']:.2%} | "
            f"{m['positive_month_ratio']:.0%} | {m['trades']} | "
            f"{m['turnover']:.2f} | {m['avg_position']:.0%} | {m['longest_drawdown_days']} |"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 回测主流程
# --------------------------------------------------------------------------- #
def _plot_curves(curves: dict[str, pd.DataFrame], path: Path) -> Optional[str]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        from report import _setup_cjk_font

        _setup_cjk_font()

        fig, axes = plt.subplots(2, 1, figsize=(11, 7.5), dpi=130,
                                 gridspec_kw={"height_ratios": [3, 1]}, sharex=True)
        for name, c in curves.items():
            if c is None or c.empty:
                continue
            x = pd.to_datetime(c["trade_date"])
            nav = c["equity"] / c["equity"].iloc[0]
            axes[0].plot(x, nav, lw=1.4, label=name)
            axes[1].plot(x, (c["equity"] / c["equity"].cummax() - 1) * 100, lw=1.0)
        axes[0].set_ylabel("累计净值")
        axes[0].legend(loc="upper left", fontsize=8)
        axes[0].grid(alpha=0.25)
        axes[1].set_ylabel("回撤 (%)")
        axes[1].grid(alpha=0.25)
        fig.suptitle("159531 策略净值与回撤对比（t 日收盘信号，t+1 开盘成交）")
        fig.tight_layout()
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path)
        plt.close(fig)
        return str(path)
    except Exception:
        return None


def run_variants(cfg: dict, etf: pd.DataFrame, index_df: Optional[pd.DataFrame],
                 variants: list[Variant], start: Optional[str] = None,
                 end: Optional[str] = None, common_start: bool = True) -> dict:
    """在统一口径下回测一组策略。

    指标在完整历史上计算（t 日只用 <=t 的数据），模拟区间再按 start/end 截取，
    这样 2024 年初等分段测试可以使用上市以来的前置历史作为指标热身，且不产生未来函数。
    """
    data = etf.copy()
    data["trade_date"] = data["trade_date"].astype(str)
    data = data.sort_values("trade_date").reset_index(drop=True)
    if end and str(end).lower() != "latest":
        data = data.loc[data["trade_date"] <= str(end)].reset_index(drop=True)
    if data.empty:
        raise ValueError("回测区间内没有 ETF 数据")

    dates = data["trade_date"].tolist()
    base_start = 0
    if start:
        hits = np.where(data["trade_date"].to_numpy() >= str(start))[0]
        if len(hits) == 0:
            raise ValueError(f"起始日期 {start} 晚于数据末端")
        base_start = int(hits[0])
    end_idx = len(data) - 1

    prepared = {}
    starts = {}
    for v in variants:
        df = prepare(cfg, data, index_df, v)
        prepared[v.key] = (df, v)
        w = warmup_index(df, v)
        if w is None:
            starts[v.key] = None
        else:
            starts[v.key] = max(base_start, w)

    valid_starts = [s for s in starts.values() if s is not None]
    if not valid_starts:
        raise ValueError("指标窗口不足，无法回测")
    common = max(valid_starts) if common_start else None

    results: dict[str, dict] = {}
    for key, (df, v) in prepared.items():
        s = common if common_start else starts[key]
        if s is None or s >= end_idx - 5:
            continue
        res = simulate(df, cfg, v, start_idx=s, end_idx=end_idx)
        res["metrics"] = compute_metrics(
            res["curve"], res["trades"],
            float(cfg.get("backtest", {}).get("initial_cash", 1e6)))
        results[key] = res
    return {"results": results, "data_start": data["trade_date"].iloc[0],
            "data_end": data["trade_date"].iloc[-1],
            "common_start_idx": common if common is not None else base_start,
            "dates": dates}


def run_all(cfg: dict, etf: pd.DataFrame, index_df: Optional[pd.DataFrame],
            start: Optional[str] = None, end: Optional[str] = None) -> dict:
    bt = cfg.get("backtest", {})
    out_root = resolve_path(cfg, cfg.get("project", {}).get("report_dir", "reports")) / "backtest"
    stamp = now_cn().strftime("%Y%m%d_%H%M%S")
    out_dir = out_root / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    baseline = run_variants(cfg, etf, index_df, BASE_VARIANTS, start, end)
    results = baseline["results"]

    rows = [(v.name, results[v.key]["metrics"]) for v in BASE_VARIANTS if v.key in results]
    lines = [
        "# 159531 历史回测报告",
        "",
        f"生成时间：{now_cn():%Y-%m-%d %H:%M:%S}",
        f"ETF 数据区间：{baseline['data_start']} ~ {baseline['data_end']}",
        f"策略统一起点索引：{baseline['common_start_idx']}（受 MA120 等指标窗口约束）",
        "",
        "## 一、策略对比（同一初始资金、交易日历、成本假设）",
        "",
        _fmt_metrics_table(rows),
        "",
        "说明：CAGR/夏普等为逐日净值口径；交易次数为实际成交笔数；年化换手 = 总成交额 / 平均净值 / 年数。",
        "",
    ]

    curves = {v.name: results[v.key]["curve"] for v in BASE_VARIANTS if v.key in results}
    png = _plot_curves(curves, out_dir / "equity_curves.png")
    if png:
        lines.append(f"净值曲线：{Path(png).name}")
        lines.append("")

    # 导出明细
    comp = pd.DataFrame([
        {"variant": v.name, **{k: val for k, val in results[v.key]["metrics"].items()
                               if k != "yearly_returns"}}
        for v in BASE_VARIANTS if v.key in results
    ])
    comp.to_csv(out_dir / "comparison.csv", index=False, encoding="utf-8-sig")
    for v in BASE_VARIANTS:
        if v.key in results:
            c = results[v.key]["curve"]
            c.to_csv(out_dir / f"curve_{v.key}.csv", index=False, encoding="utf-8-sig")
            tr = results[v.key]["trades"]
            if not tr.empty:
                tr.to_csv(out_dir / f"trades_{v.key}.csv", index=False, encoding="utf-8-sig")

    # ---- 实验 1：波动率目标 ----
    lines += ["## 二、波动率目标敏感性（完整策略 E）", ""]
    vrows = []
    for vt in bt.get("vol_targets", [0.10, 0.12, 0.14, 0.16]):
        v = BASE_VARIANTS[4].with_(key=f"E_vt{int(vt * 100)}",
                                   name=f"E · 目标波动率 {vt:.0%}", vol_target=float(vt))
        r = run_variants(cfg, etf, index_df, [v], start, end,
                         common_start=False)["results"][v.key]
        vrows.append((v.name, r["metrics"]))
    lines += [_fmt_metrics_table(vrows), ""]

    # ---- 实验 2：趋势均线组合 ----
    lines += ["## 三、趋势均线组合敏感性（完整策略 E）", ""]
    mrows = []
    for ms in bt.get("ma_sets", [[20, 60], [20, 120], [20, 60, 120]]):
        ms = [int(x) for x in ms]
        v = BASE_VARIANTS[4].with_(key="E_ma" + "_".join(map(str, ms)),
                                   name="E · MA" + "/".join(map(str, ms)), ma_set=ms)
        r = run_variants(cfg, etf, index_df, [v], start, end,
                         common_start=False)["results"][v.key]
        mrows.append((v.name, r["metrics"]))
    lines += [_fmt_metrics_table(mrows), ""]

    # ---- 实验 3：超卖模块开关 ----
    lines += ["## 四、超卖战术模块消融（E vs 关闭超卖）", ""]
    no_os = BASE_VARIANTS[4].with_(key="E_no_os", name="E · 关闭超卖加仓", use_oversold=False)
    r_no = run_variants(cfg, etf, index_df, [no_os], start, end,
                        common_start=False)["results"][no_os.key]
    arows = [("E · 开启超卖", results["E"]["metrics"]) if "E" in results else ("E", {}),
             (no_os.name, r_no["metrics"])]
    lines += [_fmt_metrics_table(arows), ""]
    e_prep = prepare(cfg, etf, index_df, BASE_VARIANTS[4])
    os_max = float(e_prep["os_weight"].max()) if "os_weight" in e_prep else 0.0
    if os_max <= 0:
        lines += ["注：本样本内所有超卖事件均未完成反弹确认（或处于冷却期），战术仓位始终为 0，"
                  "因此开启与关闭超卖模块的结果完全一致；该模块在当前 ETF 历史上未贡献增益。", ""]

    # ---- 实验 4：交易成本敏感性 ----
    lines += ["## 五、交易成本 / 滑点敏感性（完整策略 E）", ""]
    crows = []
    for c in bt.get("cost_stress", [0.0005, 0.001, 0.002]):
        v = BASE_VARIANTS[4].with_(key=f"E_cost{int(c * 10000)}",
                                   name=f"E · 单边成本 {c:.2%}", commission=float(c),
                                   slippage=float(c))
        r = run_variants(cfg, etf, index_df, [v], start, end,
                         common_start=False)["results"][v.key]
        crows.append((v.name, r["metrics"]))
    lines += [_fmt_metrics_table(crows), ""]

    # ---- 实验 5：分段 / 压力测试 ----
    lines += ["## 六、分段与压力测试", ""]
    stress_cfg = BASE_VARIANTS[4].with_(key="E_stress", name="E · MA20/60 版本",
                                       ma_set=[20, 60])
    lines.append("受 ETF 2023-09-18 上市限制，2024 年初样本不足 120 日均线，"
                 "压力测试使用 MA20/60 的完整策略版本，并与买入持有对照。")
    lines.append("")
    srows = []
    for sp in bt.get("stress_periods", []):
        s0, s1 = str(sp["start"]), str(sp["end"])
        if s1.lower() == "latest":
            s1 = str(end) if end else baseline["data_end"]
        try:
            rr = run_variants(cfg, etf, index_df,
                              [BASE_VARIANTS[0], stress_cfg], s0, s1, common_start=False)
            for k, r in rr["results"].items():
                label = f"{sp['name']} · {'买入持有' if k == 'A' else '完整策略E(MA20/60)'}"
                srows.append((label, r["metrics"]))
        except Exception as exc:  # 区间过短
            srows.append((f"{sp['name']} · 数据不足（{exc}）", {}))
    lines += [_fmt_metrics_table(srows), ""]
    lines += ["注：压力区间较短时，受「仅每周最后一个交易日再平衡、变动不足 5pp 不交易」约束，"
              "窗口内可能没有任何实际成交（如 2024 年初 2 个月窗口），此时 0% 回撤只代表未建仓，"
              "不代表策略在更长区间也能避开下跌。", ""]

    # ---- 实验 6：滚动窗口稳定性 ----
    lines += ["## 七、滚动窗口稳定性（60 交易日）", ""]
    e_curve = results.get("E", {}).get("curve")
    a_curve = results.get("A", {}).get("curve")
    roll_text = "无足够数据"
    if e_curve is not None and a_curve is not None and not e_curve.empty:
        e_ret = e_curve.set_index("trade_date")["equity"].pct_change()
        a_ret = a_curve.set_index("trade_date")["equity"].pct_change()
        joined = pd.concat([e_ret.rename("E"), a_ret.rename("A")], axis=1).dropna()
        w = int(bt.get("rolling_window", 60))
        if len(joined) >= w * 2:
            chunks = []
            for s in range(0, len(joined) - w + 1, w):
                seg = joined.iloc[s:s + w]
                chunks.append({
                    "start": str(seg.index[0]), "end": str(seg.index[-1]),
                    "E": float((1 + seg["E"]).prod() - 1),
                    "A": float((1 + seg["A"]).prod() - 1),
                })
            ch = pd.DataFrame(chunks)
            ch["excess"] = ch["E"] - ch["A"]
            ch.to_csv(out_dir / "rolling_windows.csv", index=False, encoding="utf-8-sig")
            roll_text = (f"共 {len(ch)} 个窗口，E 跑赢买入持有的窗口占比 "
                         f"{(ch['excess'] > 0).mean():.0%}，平均超额 "
                         f"{ch['excess'].mean():.2%}，最差窗口超额 {ch['excess'].min():.2%}。")
            lines.append(roll_text)
            lines.append("")
            lines.append(ch.to_markdown(index=False))
            lines.append("")
    if roll_text == "无足够数据":
        lines += [roll_text, ""]
    lines.append("说明：策略参数在回测前已固定，未使用任何未来行情选择参数；"
                 "滚动窗口仅用于检验稳定性，不构成样本外参数优化。")
    lines.append("")

    # ---- 限制 ----
    years = (pd.Timestamp(baseline["data_end"]) - pd.Timestamp(baseline["data_start"])).days / 365.25
    lines += [
        "## 八、回测限制",
        "",
        f"- 真实 ETF 数据仅 {baseline['data_start']} ~ {baseline['data_end']}（约 {years:.1f} 年），"
        "样本跨度有限，年度统计的统计显著性偏低。",
        "- 2024 年初压力测试受 ETF 上市时间限制，只能使用较短均线版本，不能等同于完整策略实盘表现。",
        "- 未使用 ETF 上市前的中证2000指数行情拼接成可交易序列；指数数据仅用于熔断恢复门控。",
        "- 交易按次日开盘价成交，未建模盘中冲击、涨跌停无法成交、ETF 折溢价与流动性分层；"
        "成本敏感性表用于评估该不确定性。",
        "- 分红/拆分按复权因子变化折算为现金分配，属于近似处理。",
        "- 历史回测不代表未来收益，最大回撤可能突破 20%。",
        "",
    ]

    summary = "\n".join(lines)
    (out_dir / "summary.md").write_text(summary, encoding="utf-8")
    (out_dir / "summary.json").write_text(json.dumps({
        "generated_at": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
        "data_range": [baseline["data_start"], baseline["data_end"]],
        "comparison": {v.key: results[v.key]["metrics"] for v in BASE_VARIANTS
                       if v.key in results},
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    return {
        "summary_text": summary,
        "out_dir": str(out_dir),
        "comparison": comp,
        "results": results,
        "equity_png": png,
    }
