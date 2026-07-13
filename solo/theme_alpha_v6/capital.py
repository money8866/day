#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V6.0 - 资金评分模块（重新设计版）

设计原则：评价"资金是否正在持续流入、是否越来越集中、是否成为市场资金主战场"
         而非简单评价"资金大不大"

六维结构：
  ① MarketShare        (20%)  市场成交额占比 — 跨主题百分位
  ② CapitalAcceleration(20%)  资金加速度 — EMA5/EMA20
  ③ MoneyflowQuality   (20%)  资金质量 — 机构资金连续性
  ④ CapitalConcentration(15%) 资金集中度 — Top20%股票成交额占比
  ⑤ CapitalPersistence (15%)  资金持续性 — 10日净流入天数
  ⑥ CapitalRotation    (10%)  资金轮动 — 5日均量/20日均量

归一化：跨主题百分位排名（非 MinMax、非固定区间）
放大：  power(pct, 0.80) 非线性拉伸 — Top10%→92, Top30%→76, Top50%→57, 尾部→28
"""
import os, sys, warnings
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
warnings.filterwarnings("ignore")
import config


# ============================================================
#  原始指标计算（向量化）
# ============================================================

def _theme_amount_series(daily: pd.DataFrame, codes: list) -> pd.Series:
    """主题每日成交额序列（亿元），向量化"""
    sub = daily[daily["ts_code"].isin(codes)]
    if sub.empty:
        return pd.Series(dtype=float)
    return sub.groupby("trade_date")["amount"].sum().sort_index() / 1e8


def _metric_market_share(amt_series: pd.Series, market_turnover: float) -> float:
    """① MarketShare: 当日成交额占全市场比例"""
    if amt_series.empty or market_turnover <= 0:
        return 0.0
    return float(amt_series.iloc[-1] / market_turnover)


def _metric_acceleration(amt_series: pd.Series) -> float:
    """② CapitalAcceleration: EMA5 / EMA20 - 1（资金加速度）
    正值=资金正在加速流入，负值=减速"""
    if len(amt_series) < 20:
        return 0.0
    ema5 = amt_series.ewm(span=5, adjust=False).mean()
    ema20 = amt_series.ewm(span=20, adjust=False).mean()
    denom = ema20.iloc[-1]
    if denom <= 0:
        return 0.0
    return float(ema5.iloc[-1] / denom - 1.0)


def _metric_moneyflow_quality(moneyflow: pd.DataFrame, codes: list,
                               daily: pd.DataFrame) -> float:
    """③ MoneyflowQuality: 机构资金质量（修复版）
    有 moneyflow 时: 机构净流入占比 × 连续流入天数系数
    核心改进：正确使用净流入金额，反映资金真实流向
    """
    if moneyflow is not None and not moneyflow.empty:
        mf = moneyflow[moneyflow["ts_code"].isin(codes)].copy()
        if not mf.empty:
            # 机构资金 = 超大单+大单
            buy_cols = [c for c in ["buy_elg_amount", "buy_elg_amounts",
                                     "elarg_buy_amount"] if c in mf.columns]
            sell_cols = [c for c in ["sell_elg_amount", "sell_elg_amounts",
                                      "elarg_sell_amount"] if c in mf.columns]

            if not buy_cols or not sell_cols:
                # 退化用 net_mf_amount
                if "net_mf_amount" in mf.columns:
                    daily_net = mf.groupby("trade_date")["net_mf_amount"].sum() / 1e4  # 万元
                    inflow_days = (daily_net > 0).sum()
                    total_days = len(daily_net)
                    ratio = inflow_days / total_days if total_days > 0 else 0
                    consec = 0
                    for v in reversed(daily_net.values):
                        if v > 0:
                            consec += 1
                        else:
                            break
                    consec_factor = 1.0 + consec * 0.1
                    return float(np.clip(ratio * consec_factor, -1, 1))
                return 0.0  # 无数据=中性偏低

            # 按日汇总机构买卖
            daily_buy = mf.groupby("trade_date")[buy_cols].sum().sum(axis=1)
            daily_sell = mf.groupby("trade_date")[sell_cols].sum().sum(axis=1)
            daily_net_inst = (daily_buy - daily_sell).sort_index()

            # 总成交额（用 buy+sell 之和作为分母，避免amount单位问题）
            total_buy = daily_buy.sum()
            total_sell = daily_sell.sum()
            total_amt = total_buy + total_sell
            if total_amt <= 0:
                return 0.0

            # 机构净流入占比
            inst_ratio = (total_buy - total_sell) / total_amt  # -1 到 +1

            # 连续流入天数（从最近往前数）
            consec = 0
            for v in reversed(daily_net_inst.values):
                if v > 0:
                    consec += 1
                else:
                    break
            consec_factor = 1.0 + consec * 0.1  # 每连续1天+10%

            return float(np.clip(inst_ratio * consec_factor, -1, 1))

    # 降级：用 daily amount 日环比作为代理
    amt = _theme_amount_series(daily, codes)
    if len(amt) < 10:
        return 0.0
    pct = amt.pct_change().dropna()
    recent = pct.tail(10)
    up_ratio = (recent > 0).mean()
    return float(up_ratio - 0.5)  # 0.5为中性行


def _metric_concentration(daily: pd.DataFrame, codes: list) -> float:
    """④ CapitalConcentration: Top20%股票成交额占比
    集中度高=主线形成，低=分散无核心"""
    latest_day = daily["trade_date"].max()
    latest = daily[(daily["ts_code"].isin(codes)) &
                   (daily["trade_date"] == latest_day)]
    if latest.empty:
        return 0.0
    stock_amt = latest.groupby("ts_code")["amount"].sum().sort_values(ascending=False)
    if stock_amt.empty:
        return 0.0
    n_top = max(1, len(stock_amt) // 5)  # Top20%
    return float(stock_amt.head(n_top).sum() / stock_amt.sum())


def _metric_persistence(daily: pd.DataFrame, codes: list) -> float:
    """⑤ CapitalPersistence: 过去10日成交额 > 20日均量的天数比例
    今天暴增但前9天流出=不给高分"""
    amt = _theme_amount_series(daily, codes)
    if len(amt) < 20:
        return 0.5
    avg_20 = amt.iloc[-20:].mean()
    if avg_20 <= 0:
        return 0.5
    recent_10 = amt.iloc[-10:]
    return float((recent_10 > avg_20).mean())


def _metric_rotation(daily: pd.DataFrame, codes: list) -> float:
    """⑥ CapitalRotation: 近5日均量 / 20日均量 - 1
    正值大=资金正在流入（排名提升），0=稳定，负=流出"""
    amt = _theme_amount_series(daily, codes)
    if len(amt) < 20:
        return 0.0
    avg_5 = amt.iloc[-5:].mean()
    avg_20 = amt.iloc[-20:].mean()
    if avg_20 <= 0:
        return 0.0
    return float(avg_5 / avg_20 - 1.0)


def _metric_net_inflow_direction(moneyflow: pd.DataFrame, codes: list) -> float:
    """⑦ NetInflowDirection: 当日机构资金净流入方向
    正值=机构净买入，负值=机构净卖出
    返回 -1 到 +1 的归一化值
    """
    if moneyflow is None or moneyflow.empty:
        return 0.0

    mf = moneyflow[moneyflow["ts_code"].isin(codes)].copy()
    if mf.empty:
        return 0.0

    # 取最新交易日
    latest_date = mf["trade_date"].max()
    mf_latest = mf[mf["trade_date"] == latest_date]

    buy_cols = [c for c in ["buy_elg_amount", "buy_elg_amounts",
                             "elarg_buy_amount"] if c in mf_latest.columns]
    sell_cols = [c for c in ["sell_elg_amount", "sell_elg_amounts",
                              "elarg_sell_amount"] if c in mf_latest.columns]

    if not buy_cols or not sell_cols:
        # 退化用 net_mf_amount
        if "net_mf_amount" in mf_latest.columns:
            net = mf_latest["net_mf_amount"].sum()
            # 用绝对值归一化
            total_abs = mf_latest["buy_elg_amount"].abs().sum() + mf_latest["sell_elg_amount"].abs().sum() if "buy_elg_amount" in mf_latest.columns else 1
            return float(np.clip(net / (total_abs + 1e-9), -1, 1))
        return 0.0

    total_buy = mf_latest[buy_cols].sum().sum()
    total_sell = mf_latest[sell_cols].sum().sum()
    total = total_buy + total_sell

    if total <= 0:
        return 0.0

    return float(np.clip((total_buy - total_sell) / total, -1, 1))


# ============================================================
#  非线性放大
# ============================================================

def _amplify(pct_ranks: np.ndarray) -> np.ndarray:
    """非线性放大：power(pct, 0.80) × 100
    Top10%→92, Top30%→76, Top50%→57, 尾部→28
    避免 85/86/87 饱和现象"""
    s = np.clip(pct_ranks, 0, 1)
    amplified = np.power(s, config.CAP_AMPLIFY_POWER) * 100
    return np.clip(amplified, config.CAP_AMPLIFY_FLOOR, config.CAP_AMPLIFY_CEIL)


# ============================================================
#  主接口：批量计算所有主题资金评分
# ============================================================

def compute_all_capital_scores(daily: pd.DataFrame, moneyflow: pd.DataFrame,
                                universe: dict, market_turnover: float) -> dict:
    """
    批量计算所有主题的 CapitalScore（跨主题百分位 + 非线性放大）

    返回: {theme_name: (capital_score, sub_metrics_dict)}
    """
    records = []
    for tname, codes in universe.items():
        if len(codes) < config.MIN_THEME_STOCKS:
            continue
        amt = _theme_amount_series(daily, codes)
        if amt.empty:
            continue

        records.append({
            "theme": tname,
            "market_share": _metric_market_share(amt, market_turnover),
            "acceleration": _metric_acceleration(amt),
            "mf_quality": _metric_moneyflow_quality(moneyflow, codes, daily),
            "concentration": _metric_concentration(daily, codes),
            "persistence": _metric_persistence(daily, codes),
            "rotation": _metric_rotation(daily, codes),
            "net_inflow": _metric_net_inflow_direction(moneyflow, codes),
        })

    if not records:
        return {}

    df = pd.DataFrame(records)

    # ===== 跨主题百分位排名 =====
    # 注意：mf_quality 可能全为相同值（降级模式），rank 后差异小
    metric_cols = ["market_share", "acceleration", "mf_quality",
                   "concentration", "persistence", "rotation", "net_inflow"]
    for col in metric_cols:
        df[col + "_pct"] = df[col].rank(pct=True)

    # ===== 加权合成（百分位 → 综合百分位）=====
    df["raw_pct"] = (
        df["market_share_pct"] * config.CAP_W_SHARE +
        df["acceleration_pct"] * config.CAP_W_ACCEL +
        df["mf_quality_pct"] * config.CAP_W_MFLOW +
        df["concentration_pct"] * config.CAP_W_CONC +
        df["persistence_pct"] * config.CAP_W_PERSIST +
        df["rotation_pct"] * config.CAP_W_ROTATION +
        df["net_inflow_pct"] * config.CAP_W_NETFLOW
    )

    # ===== 非线性放大 =====
    df["capital_score"] = _amplify(df["raw_pct"].values)

    # ===== 返回结果 =====
    result = {}
    for _, row in df.iterrows():
        result[row["theme"]] = (
            float(row["capital_score"]),
            {
                "market_share": round(float(row["market_share"]), 4),
                "acceleration": round(float(row["acceleration"]), 4),
                "mf_quality": round(float(row["mf_quality"]), 4),
                "concentration": round(float(row["concentration"]), 4),
                "persistence": round(float(row["persistence"]), 4),
                "rotation": round(float(row["rotation"]), 4),
                "net_inflow": round(float(row["net_inflow"]), 4),
                "persistence_pct": float(row["persistence_pct"]),
                "rotation_pct": float(row["rotation_pct"]),
            }
        )
    return result


# ============================================================
#  兼容旧接口（单主题，无跨主题百分位，仅用于回测/调试）
# ============================================================

def compute_capital_score(daily, moneyflow, codes, market_turnover=0):
    """兼容旧接口：返回单主题资金分（无跨主题百分位，降级为绝对值评分）"""
    amt = _theme_amount_series(daily, codes)
    if amt.empty:
        return 50.0
    ms = _metric_market_share(amt, market_turnover)
    ac = _metric_acceleration(amt)
    mq = _metric_moneyflow_quality(moneyflow, codes, daily)
    cc = _metric_concentration(daily, codes)
    ps = _metric_persistence(daily, codes)
    rt = _metric_rotation(daily, codes)
    # 无跨主题对比时，用绝对值近似评分
    raw = (min(ms * 500, 1.0) * 0.20 +   # 0.2% → 满分
           _sigmoid01(ac * 8) * 0.20 +    # 加速度
           mq * 0.20 +
           cc * 0.15 +
           ps * 0.15 +
           _sigmoid01(rt * 8) * 0.10)
    return float(_amplify(np.array([raw]))[0])


def _sigmoid01(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -10, 10)))
