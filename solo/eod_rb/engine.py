# -*- coding: utf-8 -*-
"""
EOD-RB 五层信号引擎（纯函数，不直接做 IO，分钟数据由调用方注入）。

层级（对应规范）：
  L1 前期强势(trend)   —— 日线：ret20/ret10/MA20/整理空间
  L2 缩量回踩(pullback) —— 日线：回踩幅度/缩量比率/支撑位
  L3 尾盘再启动(tail)   —— 分钟线：VWAP/整理突破或回抽确认/尾盘量比
  L4 风险过滤(risk)     —— 冲高回落/高位加速/涨停不可买/流动性
  L5 决策(decision)     —— BUY / WATCH / NO TRADE

所有阈值均来自 config.EOD_RB_CONFIG，禁止散落硬编码。
"""
import numpy as np
import pandas as pd

from . import config as C


# ═══════════════════════════════════════════════════════════════
# 指标与板块工具
# ═══════════════════════════════════════════════════════════════
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for w in (5, 10, 20):
        df[f"ma{w}"] = df["close"].rolling(w).mean()
    df["ret20"] = df["close"] / df["close"].shift(20) - 1
    df["ret10"] = df["close"] / df["close"].shift(10) - 1
    df["ret5"] = df["close"] / df["close"].shift(5) - 1
    df["high20"] = df["high"].rolling(20).max()
    df["low20"] = df["low"].rolling(20).min()
    df["high5"] = df["high"].rolling(5).max()
    df["low5"] = df["low"].rolling(5).min()
    return df


def board_of(ts_code: str) -> str:
    c = str(ts_code).split('.')[0]
    if c.startswith(("68", "30")):
        return "star"
    if c.startswith(("60", "00")):
        return "main"
    if c.startswith(("8", "4")):
        return "bj"
    return "main"


_LIMIT_RATIO = {"main": 0.10, "star": 0.20, "bj": 0.30}


def limit_up_price(pre_close, board: str):
    if pre_close is None or pd.isna(pre_close) or pre_close <= 0:
        return None
    return round(float(pre_close) * (1 + _LIMIT_RATIO.get(board, 0.10)), 2)


def amount_to_yi(amount_千元):
    """日线 amount(千元) -> 亿元"""
    try:
        return float(amount_千元) / 1e5
    except (TypeError, ValueError):
        return 0.0


# ═══════════════════════════════════════════════════════════════
# 第一层：前期强势（trend）
# ═══════════════════════════════════════════════════════════════
def layer1_trend(df: pd.DataFrame, cfg: dict):
    """df 为 add_indicators 后的日线（末端即信号日 T）。返回 (ok, info)"""
    if len(df) < 25:
        return False, {"reason": "历史K线不足"}
    t = cfg["trend"]
    row = df.iloc[-1]
    ret20 = row.get("ret20", np.nan)
    ret10 = row.get("ret10", np.nan)
    close = float(row["close"])
    ma20 = row.get("ma20", np.nan)

    a = pd.notna(ret20) and ret20 > t["ret20_min"]
    b = pd.notna(ret10) and ret10 > t["ret10_min"]

    # C：收盘价位于 MA20 上方，或近期（近 N 日）有效突破过 MA20
    recent = df.tail(t["ma20_break_recent"] + 1)
    above_ma20 = close > ma20
    broke_recent = (recent["close"] > recent["ma20"]).any() and not below_many(recent)
    c = above_ma20 or broke_recent

    # D：最近20日最高价与当前价格之间存在合理的整理空间（不会太贴最高点也不会太远）
    high20 = float(row.get("high20", np.nan))
    d = pd.notna(high20) and (0.0 < (high20 / close - 1) < 0.35)

    ok = bool(a and b and c and d)
    info = {
        "ret20": None if pd.isna(ret20) else round(float(ret20) * 100, 2),
        "ret10": None if pd.isna(ret10) else round(float(ret10) * 100, 2),
        "cond_ret20": bool(a), "cond_ret10": bool(b),
        "cond_ma20": bool(c), "cond_space": bool(d),
        "reason": "OK" if ok else "前期强势不满足",
    }
    return ok, info


def below_many(recent: pd.DataFrame, days: int = 3) -> bool:
    """近 N 日收盘普遍低于 MA20（用于排除长期下降趋势中的反弹）"""
    if len(recent) < days:
        return False
    sub = (recent["close"] < recent["ma20"]).iloc[-days:]
    return bool(sub.all())


# ═══════════════════════════════════════════════════════════════
# 第二层：缩量回踩（pullback）
# ═══════════════════════════════════════════════════════════════
def layer2_pullback(df: pd.DataFrame, cfg: dict):
    """在信号日 T 前 1~n 日内识别回踩。返回 (ok, info)"""
    if len(df) < 30:
        return False, {"reason": "历史K线不足"}
    p = cfg["pullback"]
    n = int(p["lookback_window"])
    rise_w = int(p["rise_window"])

    rows = df.reset_index(drop=True)
    # W = 回踩观察窗口（不含当日 T）：[-（n+1) : -1]
    w = rows.iloc[- (n + 1): -1]
    if w.empty:
        return False, {"reason": "回踩窗口为空"}
    highs = w["high"].to_numpy()
    lows = w["low"].to_numpy()
    amounts = w["amount"].to_numpy()

    hi_idx = int(np.argmax(highs))          # 局部高点在 W 内的位置
    local_high = float(highs[hi_idx])
    lo_after = lows[hi_idx:]                # 高点之后的低点
    pullback_low = float(np.min(lo_after)) if len(lo_after) else local_high
    depth = local_high / pullback_low - 1   # 回落幅度（正）

    # 缩量比率：调整期均额 / 上涨期均额
    rise_slice = rows.iloc[- (n + 1 + rise_w): - (n + 1)]
    adj_slice = w.iloc[hi_idx:]
    rise_amt = rise_slice["amount"].mean() if len(rise_slice) else np.nan
    adj_amt = adj_slice["amount"].mean() if len(adj_slice) else np.nan
    shrink = (adj_amt / rise_amt) if (pd.notna(adj_amt) and pd.notna(rise_amt) and rise_amt > 0) else np.nan

    # 支撑位：MA20 与 近3~10日局部低点 的较大者；突破位上沿近似取 回踩窗口高点
    ma20 = float(rows.iloc[-1]["ma20"]) if pd.notna(rows.iloc[-1].get("ma20")) else np.nan
    recent_low = float(np.min(rows.iloc[-10:-1]["low"].to_numpy())) if len(rows) >= 11 else np.nan
    support = max([x for x in (ma20, recent_low, pullback_low) if pd.notna(x)])
    close_t = float(rows.iloc[-1]["close"])

    depth_ok = p["depth_min"] <= depth <= p["depth_max"]
    shrink_ok = pd.notna(shrink) and shrink < p["shrink_ratio"]
    support_ok = close_t >= support
    if p["require_not_below_ma20"] and pd.notna(ma20):
        support_ok = support_ok and close_t >= ma20 * 0.98  # 允许极小容差
    rebound = close_t > pullback_low * 1.005  # 已从回踩低点回升

    ok = bool(depth_ok and shrink_ok and support_ok and rebound)

    info = {
        "depth": round(depth * 100, 2),
        "shrink": None if pd.isna(shrink) else round(float(shrink), 3),
        "support": round(support, 3),
        "local_high": round(local_high, 3),
        "pullback_low": round(pullback_low, 3),
        "cond_depth": bool(depth_ok),
        "cond_shrink": bool(shrink_ok),
        "cond_support": bool(support_ok),
        "reason": "OK" if ok else "回踩结构不满足或支撑失效",
    }
    return ok, info


# ═══════════════════════════════════════════════════════════════
# 第三层：尾盘再启动（tail）——分钟级
# ═══════════════════════════════════════════════════════════════
def layer3_tail(anchor: dict, vol_benchmark, day_row, cfg: dict):
    """anchor 来自 minute.build_tail_anchor；vol_benchmark 为过去N日同窗口均量。
    返回 (ok, tag_or_None, info)"""
    if anchor is None:
        return False, None, {"reason": "无分钟数据"}
    t = cfg["tail"]
    ret = anchor["tail_window_return"]                     # 14:30→14:55 收益
    vwap = anchor.get("vwap_1455")
    p1455 = anchor["price_1455"]
    rng = anchor["range_1400_1430"]                        # (low, high) 14:00~14:30
    tail_vol = anchor["tail_volume"]

    breach = p1455 > rng[1]                                # 突破整理区间上沿
    above_vwap = (vwap is not None) and p1455 > vwap
    reverse = p1455 > anchor["price_1430"]                 # 价格反转向上

    if breach:
        tag = t["tag_breakout"]                            # RB_BREAKOUT
        ret_ok = True
    elif above_vwap and reverse:
        tag = t["tag_reclaim"]                             # RB_RECLAIM
        ret_ok = True
    else:
        tag = None
        ret_ok = False

    ret_ok = ret_ok and ret > t["min_ret"]

    # 尾盘量比：当日尾盘量 / 过去N日同窗口均量
    vol_ratio = (tail_vol / vol_benchmark) if (vol_benchmark and vol_benchmark > 0) else None
    vol_ok = vol_ratio is not None and vol_ratio >= t["vol_ratio_min"]

    # 当日过热过滤：分板块涨停阈值
    pre_close = day_row.get("pre_close")
    board = day_row.get("_board", "main")
    lu = limit_up_price(pre_close, board)
    day_pct = float(day_row.get("pct_chg", 0)) / 100.0 if pd.notna(day_row.get("pct_chg")) else None
    hot = (lu is not None and p1455 >= lu) or (day_pct is not None and day_pct >= t["max_day_pct"])

    above_line = "高于VWAP" if above_vwap else "低于VWAP"
    ok = ret_ok and above_vwap and not hot

    info = {
        "tail_return": round(ret * 100, 2),
        "vwap": None if vwap is None else round(float(vwap), 3),
        "above_vwap": bool(above_vwap),
        "vwap_state": above_line,
        "vol_ratio": None if vol_ratio is None else round(float(vol_ratio), 3),
        "vol_ok": bool(vol_ok),
        "breakout": bool(breach),
        "tag": tag,
        "tag_cn": C.SIGNAL_TAGS.get(tag, "无"),
        "hot": bool(hot),
        "reason": "OK" if ok else "尾盘信号不成立或过热",
    }
    return ok, tag, info


# ═══════════════════════════════════════════════════════════════
# 第四层：风险过滤（risk）
# ═══════════════════════════════════════════════════════════════
def layer4_risk(day_row, anchor, cfg: dict):
    """返回 (ok, risk_str)"""
    r = cfg["risk"]
    board = day_row.get("_board", "main")
    pre_close = day_row.get("pre_close")
    lu = limit_up_price(pre_close, board)
    close = float(day_row["close"])
    high = float(day_row.get("high", close))
    low = float(day_row.get("low", close))
    pct = day_row.get("pct_chg")
    day_pct = float(pct) / 100.0 if pd.notna(pct) else 0.0

    risks = []
    # 冲高回落（上影过长）
    rng = (high - low)
    upper_shadow = (high - close) / rng if rng > 0 else 0.0
    if upper_shadow > r["max_intraday_upper_shadow"]:
        risks.append("冲高回落(长上影)")
    # 高位加速
    if pd.notna(day_row.get("ret5")) and day_row["ret5"] > r["max_accel"]:
        risks.append("高位加速")
    # 涨停不可成交
    if r["reject_limit_buy"] and lu is not None and close >= lu:
        risks.append("涨停不可成交")
    elif lu is not None and close >= lu * r["near_limit_unbuyable"]:
        risks.append("接近涨停可成交概率低")
    # 当日成交额不足（由调用方在 universe/流动性处已过滤，这里复检）
    if amount_to_yi(day_row.get("amount")) < cfg["universe"]["min_amount_yi"]:
        risks.append("成交额不足")

    return (len(risks) == 0), "、".join(risks) if risks else "无"


# ═══════════════════════════════════════════════════════════════
# 日线层评估（L1+L2，用于消融 A/B 与基础候选）
# ═══════════════════════════════════════════════════════════════
def evaluate_daily(ts_code: str, df: pd.DataFrame, cfg: dict):
    if "ma20" not in df.columns:
        df = add_indicators(df)
    row = df.iloc[-1].copy()
    row["_board"] = board_of(ts_code)

    trend_ok, trend_info = layer1_trend(df, cfg)
    pullback_ok, pullback_info = layer2_pullback(df, cfg)

    # 前期涨幅（报告用）：近20日区间涨幅 proxy
    prior_wave = None
    if pd.notna(row.get("high20")) and pd.notna(row.get("low20")) and row["low20"] > 0:
        prior_wave = round((float(row["high20"]) / float(row["low20"]) - 1) * 100, 2)

    base = {
        "ts_code": ts_code,
        "trade_date": str(row["trade_date"]),
        "close": float(row["close"]),
        "pct_chg": None if pd.isna(row.get("pct_chg")) else float(row["pct_chg"]),
        "board": row["_board"],
        "amount_yi": amount_to_yi(row.get("amount")),
        "trend_ok": trend_ok,
        "pullback_ok": pullback_ok,
        "prior_wave": prior_wave,
        "ma20": None if pd.isna(row.get("ma20")) else float(row["ma20"]),
        "support": pullback_info.get("support"),
        "pullback_info": pullback_info,
        "trend_info": trend_info,
    }
    return base


# ═══════════════════════════════════════════════════════════════
# 全五层评估（L1~L5），含尾盘与板块确认
# ═══════════════════════════════════════════════════════════════
def evaluate_full(ts_code, df, anchor, vol_benchmark, market_regime, name, mv_yi, sector, cfg):
    """sector: dict = {"rel_strength": float, "industry_vs_market": float, "breadth": float, "ok": bool}
    返回完整信号 dict（含每日候选表全部字段）。"""
    base = evaluate_daily(ts_code, df, cfg)
    df = df if "ma20" in df.columns else add_indicators(df)
    row = df.iloc[-1].copy()
    row["_board"] = board_of(ts_code)
    row["pre_close"] = row.get("pre_close")

    trend_ok = base["trend_ok"]
    pullback_ok = base["pullback_ok"]

    tail_ok, tag, tail_info = layer3_tail(anchor, vol_benchmark, row, cfg)
    risk_ok, risk_str = layer4_risk(row, anchor, cfg)

    sector_ok = bool(sector.get("ok", False)) if sector else False
    rel_strength = sector.get("rel_strength") if sector else None

    # 决策（L5）
    if not trend_ok:
        decision = "NO TRADE"
        main_risk = "趋势结构失效"
    elif not pullback_ok:
        decision = "NO TRADE"
        main_risk = "回踩结构失效或支撑失守"
    elif not risk_ok:
        decision = "NO TRADE"
        main_risk = risk_str
    elif anchor is None:
        decision = "WATCH" if (trend_ok and pullback_ok) else "NO TRADE"
        main_risk = "无分钟数据，无法确认尾盘信号"
    elif not tail_ok:
        decision = "WATCH"
        main_risk = "尾盘信号不成立"
    elif tail_ok and tail_info.get("vol_ok") is False:
        decision = "WATCH"
        main_risk = "尾盘量能确认不足"
    elif tail_ok and (sector is not None and not sector_ok) and cfg.get("_require_sector", False):
        decision = "WATCH"
        main_risk = "板块确认不足"
    else:
        decision = "BUY"
        main_risk = "无（风险在止损位）"

    signal = dict(base)
    signal.update({
        "name": name,
        "mv_yi": mv_yi,
        "market_regime": market_regime,
        "tail_ok": tail_ok,
        "tail_info": tail_info,
        "tag": tag if (tail_ok and tag) else None,
        "tag_cn": tail_info.get("tag_cn"),
        "tail_return": tail_info.get("tail_return"),
        "tail_vol_ratio": tail_info.get("vol_ratio"),
        "vwap_state": tail_info.get("vwap_state"),
        "breakout_state": "突破" if (tail_ok and tail_info.get("breakout")) else ("回抽" if tail_ok else "无"),
        "rel_strength": rel_strength,
        "sector_ok": sector_ok,
        "risk_ok": risk_ok,
        "risk_str": risk_str,
        "decision": decision,
        "main_risk": main_risk,
        "signal_time": "14:30-14:55" if tail_ok else None,
        "exec_price": tail_info and anchor and (anchor.get("exec_next") if tail_ok else None),
    })
    return signal