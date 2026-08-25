# -*- coding: utf-8 -*-
"""
RIB V2.1 NEXT Forward Test 模块（V2.1 §31/§32）

验证 NEXT 是否真的具有预测价值：
  1. 历史逐日扫描：股票进入 NEXT 池时记录 signal_date/signal_price/
     next_state/trigger_price
  2. 前向追踪 1D/3D/5D：最高涨幅 / 最低跌幅 / 收盘收益 / 是否突破 / 是否失败
  3. 汇总统计：NEXT_WIN_RATE / AVG_RETURN_3D / MEDIAN_RETURN_3D
     / AVG_RETURN_5D / MAX_DRAWDOWN / EXPECTANCY
  4. 状态转换链成功率：WATCH->NEXT / NEXT->BREAKOUT / BREAKOUT->PULLBACK
     / PULLBACK->PRIMARY_BUY

注意：同一股票相邻信号合并（7个交易日内不重复计数），避免窗口重叠虚增样本。

防未来函数：T 日信号只用截至 T 日的数据判定；前向收益用 T+1 开盘买入口径。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd


@dataclass
class NextSignal:
    """一条 NEXT 信号记录（§32）。"""
    ts_code: str = ""
    name: str = ""
    signal_date: str = ""
    signal_price: float = 0.0
    state: str = ""
    next_state: str = ""
    trigger_price: float = 0.0
    # 前向结果
    ret_1d: float = 0.0
    ret_3d: float = 0.0
    ret_5d: float = 0.0
    max_gain_5d: float = 0.0     # 5日内最高涨幅（基于收盘）
    max_loss_5d: float = 0.0     # 5日内最低跌幅（基于收盘）
    broke_out: bool = False      # 3日内是否突破触发价
    failed: bool = False         # 3日内是否跌破信号日收盘的-5%
    win: bool = False            # ret_3d > 0


@dataclass
class ForwardStats:
    """Forward Test 汇总统计。"""
    n_signals: int = 0
    n_evaluable: int = 0
    win_rate: float = 0.0            # NEXT_WIN_RATE（3日收益>0）
    avg_ret_1d: float = 0.0
    avg_ret_3d: float = 0.0
    median_ret_3d: float = 0.0
    avg_ret_5d: float = 0.0
    max_drawdown: float = 0.0        # 最差单信号5日内最低跌幅
    expectancy: float = 0.0          # 3日期望 = 胜率*均盈 + 败率*均亏
    breakout_rate: float = 0.0       # 3日内突破触发价比例
    fail_rate: float = 0.0           # 3日内跌破-5%比例
    avg_max_gain: float = 0.0


def _to_tscode(code6: str) -> str:
    return code6 + (".SH" if code6.startswith("6") else ".SZ")


def run_forward_test(
    df_loader: Callable[[str], pd.DataFrame],
    pool: pd.DataFrame,
    analyze_fn: Callable[[pd.DataFrame], object],
    start_date: str,
    end_date: str,
    merge_days: int = 7,
) -> tuple:
    """执行 NEXT Forward Test。

    Args:
        df_loader: (code6, end_date) -> 全量 DataFrame（含 start~end 之后的数据）
        pool: 股池 DataFrame（需含"代码6"/"名称"列）
        analyze_fn: (df截至T日) -> RIBResult
        start_date/end_date: 信号扫描区间
        merge_days: 同股信号合并窗口

    Returns:
        (signals: List[NextSignal], stats: ForwardStats,
         chain: Dict[str, Dict]) 状态转换链统计
    """
    signals: List[NextSignal] = []
    chain: Dict[str, Dict] = {
        "WATCH->NEXT": {"n": 0, "hit": 0},
        "NEXT->BREAKOUT": {"n": 0, "hit": 0},
        "BREAKOUT->PULLBACK": {"n": 0, "hit": 0},
        "PULLBACK->PRIMARY_BUY": {"n": 0, "hit": 0},
    }

    for _, row in pool.iterrows():
        code6, name = str(row["代码6"]), str(row["名称"])
        try:
            full = df_loader(code6, "")
        except Exception:
            continue
        if full is None or len(full) < 140 or "trade_date" not in full.columns:
            continue

        dates = full["trade_date"].astype(str).tolist()
        # 信号日候选：区间内交易日
        sig_dates = [d for d in dates if start_date <= d <= end_date]
        if not sig_dates:
            continue

        last_signal_date = ""
        prev_state = ""
        for d in sig_dates:
            sub = full[full["trade_date"] <= d].reset_index(drop=True)
            if len(sub) < 130:
                continue
            try:
                r = analyze_fn(sub)
            except Exception:
                continue

            # ── 状态转换链追踪（§31）──
            st = r.state
            if prev_state:
                if prev_state in ("DOWNTREND", "REVERSAL_SETUP", "IMPULSE_ACTIVE",
                                  "IMPULSE_PEAK", "POST_IMPULSE_BASE",
                                  "SECOND_LEG_BREAKOUT") \
                        and st == "PRE_BREAKOUT":
                    pass  # 链中转换单独统计
                key_map = {
                    ("POST_IMPULSE_BASE", "PRE_BREAKOUT"): "WATCH->NEXT",
                    ("PRE_BREAKOUT", "SECOND_LEG_BREAKOUT"): "NEXT->BREAKOUT",
                    ("SECOND_LEG_BREAKOUT", "FIRST_PULLBACK"): "BREAKOUT->PULLBACK",
                    ("SECOND_LEG_BREAKOUT", "PULLBACK_SUPPORT"): "BREAKOUT->PULLBACK",
                    ("PULLBACK_SUPPORT", "PRIMARY_BUY"): "PULLBACK->PRIMARY_BUY",
                    ("RE_ACCELERATION", "PRIMARY_BUY"): "PULLBACK->PRIMARY_BUY",
                }
                k = key_map.get((prev_state, st))
                if k:
                    chain[k]["n"] += 1
                    chain[k]["hit"] += 1
                elif k is None and prev_state == "PRE_BREAKOUT" \
                        and st in ("POST_IMPULSE_BASE", "DOWNTREND",
                                   "FAILED_BREAKOUT", "INVALIDATED"):
                    chain["NEXT->BREAKOUT"]["n"] += 1  # 分母计入
            prev_state = st

            # ── NEXT 信号记录（§32）──
            if r.pool_tier != "NEXT":
                continue
            if last_signal_date:
                # 同股 merge_days 内不重复计
                idx_last = dates.index(last_signal_date)
                idx_now = dates.index(d)
                if idx_now - idx_last < merge_days:
                    continue
            last_signal_date = d

            sig = NextSignal(
                ts_code=_to_tscode(code6), name=name,
                signal_date=d, signal_price=r.close,
                state=r.state, next_state=r.next_state,
                trigger_price=float(r.next_state_gap_price or 0.0),
            )
            # PRE_BREAKOUT/POST_IMPULSE_BASE 优先用检测器触发价(ImpulseHigh+0.3ATR)
            if r.state in ("PRE_BREAKOUT", "POST_IMPULSE_BASE") and r.pre_breakout:
                sig.trigger_price = r.pre_breakout.trigger_price

            # ── 前向 1D/3D/5D ──
            pos = dates.index(d)
            closes = full["close"].values.astype(float)
            highs = full["high"].values.astype(float)
            for k, off in (("ret_1d", 1), ("ret_3d", 3), ("ret_5d", 5)):
                if pos + off < len(dates):
                    sig.__dict__[k] = (closes[pos + off] - closes[pos]) / closes[pos]
            window = slice(pos + 1, min(pos + 6, len(dates)))
            if window.stop > window.start:
                sig.max_gain_5d = float(np.max(highs[window]) / closes[pos] - 1)
                sig.max_loss_5d = float(np.min(full["low"].values.astype(float)[window])
                                        / closes[pos] - 1)
            if sig.trigger_price > 0 and pos + 3 < len(dates):
                sig.broke_out = bool(np.max(highs[pos + 1:pos + 4]) >= sig.trigger_price)
            sig.failed = sig.max_loss_5d <= -0.05
            sig.win = sig.ret_3d > 0
            signals.append(sig)

    stats = _summarize(signals)
    return signals, stats, chain


def _summarize(signals: List[NextSignal]) -> ForwardStats:
    """汇总 Forward Stats（§32）。"""
    st = ForwardStats()
    st.n_signals = len(signals)
    ev = [s for s in signals if s.ret_3d != 0 or s.ret_5d != 0 or s.max_gain_5d != 0]
    ev = [s for s in ev if s.signal_price > 0]
    st.n_evaluable = len(ev)
    if not ev:
        return st
    rets3 = np.array([s.ret_3d for s in ev])
    rets1 = np.array([s.ret_1d for s in ev])
    rets5 = np.array([s.ret_5d for s in ev])
    wins = rets3 > 0
    st.win_rate = float(np.mean(wins))
    st.avg_ret_1d = float(np.mean(rets1))
    st.avg_ret_3d = float(np.mean(rets3))
    st.median_ret_3d = float(np.median(rets3))
    st.avg_ret_5d = float(np.mean(rets5))
    st.max_drawdown = float(min(s.max_loss_5d for s in ev))
    st.avg_max_gain = float(np.mean([s.max_gain_5d for s in ev]))
    gains = rets3[wins]
    losses = rets3[~wins]
    st.expectancy = float(
        st.win_rate * (np.mean(gains) if len(gains) else 0)
        + (1 - st.win_rate) * (np.mean(losses) if len(losses) else 0)
    )
    st.breakout_rate = float(np.mean([s.broke_out for s in ev]))
    st.fail_rate = float(np.mean([s.failed for s in ev]))
    return st


def format_forward_report(signals: List[NextSignal], stats: ForwardStats,
                          chain: Dict[str, Dict]) -> str:
    """渲染 Forward Test 报告文本。"""
    SEP = "─" * 66
    lines = []
    lines.append("NEXT FORWARD TEST（V2.1 §31/§32）")
    lines.append(SEP)
    if stats.n_signals == 0:
        lines.append("区间内无 NEXT 信号，无法验证。")
        lines.append(SEP)
        return "\n".join(lines)

    lines.append(f"信号数: {stats.n_signals}  可评估: {stats.n_evaluable}")
    lines.append(
        f"NEXT_WIN_RATE(3D): {stats.win_rate*100:.1f}%   "
        f"EXPECTANCY(3D): {stats.expectancy*100:+.2f}%"
    )
    lines.append(
        f"AVG 1D: {stats.avg_ret_1d*100:+.2f}%   "
        f"AVG 3D: {stats.avg_ret_3d*100:+.2f}%   MEDIAN 3D: {stats.median_ret_3d*100:+.2f}%   "
        f"AVG 5D: {stats.avg_ret_5d*100:+.2f}%"
    )
    lines.append(
        f"3日内突破触发价: {stats.breakout_rate*100:.1f}%   "
        f"5日内破位(-5%): {stats.fail_rate*100:.1f}%   "
        f"MAX_DRAWDOWN: {stats.max_drawdown*100:.1f}%   "
        f"AVG_MAX_GAIN(5D): {stats.avg_max_gain*100:+.2f}%"
    )
    lines.append(SEP)
    lines.append("状态转换链成功率（区间内）：")
    for k, v in chain.items():
        rate = (v["hit"] / v["n"] * 100) if v["n"] else 0.0
        lines.append(f"  {k:<22} 转换{v['n']}次  其中正向命中{v['hit']}次  "
                     f"({rate:.0f}%)")
    lines.append(SEP)
    lines.append("信号明细（最多20条）：")
    for s in signals[:20]:
        lines.append(
            f"  {s.signal_date} {s.name:<6} {s.state:<18} "
            f"触发{s.trigger_price:>7.2f} "
            f"1D{s.ret_1d*100:+5.1f}% 3D{s.ret_3d*100:+5.1f}% "
            f"5D{s.ret_5d*100:+5.1f}% "
            f"{'突破' if s.broke_out else '－－'}"
        )
    lines.append(SEP)
    return "\n".join(lines)
