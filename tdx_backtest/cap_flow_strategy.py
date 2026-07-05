# -*- coding: utf-8 -*-
"""
资本流状态切换策略 (Capital Flow Regime Switch)

设计思路:
  机构量化择时中, 资金流是核心驱动力. 本策略通过 4 维资金流指标,
  自动识别市场状态 (early trend / mid expansion / climax / rotation),
  在 expansion 期入场, 在 climax/rotation 退出.

资金流指标:
  - capital_score (0-100): 综合资金得分 (量比+OBV+量价+成交额)
  - cap_accel:         资金加速度 (今日量比 - 5日均量比)
  - cap_persist (0-100): 资金持续性 (近10天放量上涨比例)
  - cap_conc:          资金集中度 (当日额/20日均额)
  - leader_score (0-100): 龙头得分 (涨停+连板+量价+涨幅)
  - theme_lifecycle:   主题生命周期 (early/expansion/climax/rotation)

入场条件:
  - capital_score > 70
  - cap_accel > 0 (资金加速)
  - cap_persist > 60 (持续性)
  - theme_lifecycle = expansion
  - leader_score > threshold

退出条件:
  - cap_accel < 0 (资金减速)
  - 或 cap_conc 下降 (资金集中度下降)

入场: T+1 开盘
退出: 满足条件次日开盘
"""
from __future__ import annotations
import os
import sys
import time
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_loader import load_kline
from indicators import add_indicators, MA, EMA, MACD, KDJ, RSI, BOLL, OBV


# =========================================================
# 股票代码转换
# =========================================================
def code_to_ts_code(code) -> str:
    s = str(code).zfill(6)
    if s.startswith(("6", "9")):
        return f"{s}.SH"
    elif s.startswith(("0", "3", "2")):
        return f"{s}.SZ"
    elif s.startswith(("8", "4")):
        return f"{s}.BJ"
    return f"{s}.SZ"


def _rolling_mean(arr: np.ndarray, n: int) -> np.ndarray:
    if len(arr) < n:
        return np.full(len(arr), np.nan)
    ret = np.cumsum(arr, dtype=float)
    ret[n:] = ret[n:] - ret[:-n]
    ret = ret / n
    ret[:n - 1] = np.nan
    return ret


# =========================================================
# 资金流指标计算
# =========================================================
def compute_cap_flow(df: pd.DataFrame, ts_code: str = "",
                      leader_min_streak: int = 3) -> pd.DataFrame:
    """计算资金流指标 (向量化, 一次性算出所有交易日)

    添加列:
        vol_ratio:      量比 (当日量 / 5日均量)
        capital_score:  资金综合得分 (0-100)
        cap_accel:      资金加速度
        cap_persist:    资金持续性 (0-100)
        cap_conc:       资金集中度
        leader_score:   龙头得分 (0-100, 仅参考, 真正判定用 is_leader)
        max_streak:     历史最大连板数 (截至当日)
        is_leader:      是否为龙头 (硬约束: max_streak >= 3)
        theme_lifecycle: 主题生命周期 (early/expansion/climax/rotation)
    """
    out = df.copy()
    C = out["close"].values
    O = out["open"].values
    H = out["high"].values
    L = out["low"].values
    VOL = out["vol"].values
    AMT = out["amount"].values if "amount" in out.columns else VOL * C
    n = len(C)

    # ===== 1. 量比 (当日量 / 5日均量) =====
    vol_ma5 = _rolling_mean(VOL.astype(float), 5)
    vol_ratio = np.where(vol_ma5 > 0, VOL / vol_ma5, 1.0)
    out["vol_ratio"] = vol_ratio

    # ===== 2. 涨幅 =====
    pct_chg = np.zeros(n)
    if n >= 2:
        pct_chg[1:] = (C[1:] / C[:-1] - 1) * 100
    out["pct_chg"] = pct_chg

    # ===== 3. 量价配合得分 (0-30) =====
    # 放量上涨 > 缩量上涨 > 放量下跌
    s_vp = np.zeros(n)
    # 放量上涨: vol_ratio > 1.2 且 涨幅 > 0
    s_vp[(vol_ratio > 1.2) & (pct_chg > 0)] = 30
    # 缩量上涨: vol_ratio < 1.0 且 涨幅 > 0
    s_vp[(vol_ratio < 1.0) & (pct_chg > 0)] = 22
    # 平量上涨
    s_vp[(vol_ratio >= 1.0) & (vol_ratio <= 1.2) & (pct_chg > 0)] = 25
    # 平量平盘
    s_vp[(pct_chg >= -0.5) & (pct_chg <= 0.5)] = 10
    # 放量下跌: 扣分
    s_vp[(vol_ratio > 1.5) & (pct_chg < -2)] = 0
    # 缩量下跌: 中性
    s_vp[(vol_ratio < 0.8) & (pct_chg < 0)] = 8
    out["s_vp"] = s_vp

    # ===== 4. OBV 趋势得分 (0-25) =====
    obv = out["OBV"].values if "OBV" in out.columns else np.cumsum(np.sign(np.diff(np.concatenate([[C[0]], C]))) * VOL)
    obv_ma5 = _rolling_mean(obv, 5)
    obv_slope = np.zeros(n)
    if n >= 6:
        obv_slope[5:] = (obv[5:] - obv[:-5]) / (np.abs(obv[:-5]) + 1) * 100
    s_obv = np.zeros(n)
    s_obv[obv_slope > 5] = 25
    s_obv[(obv_slope > 2) & (obv_slope <= 5)] = 20
    s_obv[(obv_slope > 0) & (obv_slope <= 2)] = 15
    s_obv[(obv_slope > -2) & (obv_slope <= 0)] = 8
    s_obv[obv_slope <= -2] = 0
    out["s_obv"] = s_obv

    # ===== 5. 资金强度得分 (0-20) (成交额相对20日均额) =====
    amt_ma20 = _rolling_mean(AMT.astype(float), 20)
    amt_ratio = np.where(amt_ma20 > 0, AMT / amt_ma20, 1.0)
    s_amt = np.zeros(n)
    s_amt[amt_ratio > 2.0] = 20
    s_amt[(amt_ratio > 1.5) & (amt_ratio <= 2.0)] = 16
    s_amt[(amt_ratio > 1.0) & (amt_ratio <= 1.5)] = 12
    s_amt[(amt_ratio > 0.7) & (amt_ratio <= 1.0)] = 8
    s_amt[amt_ratio <= 0.7] = 4
    out["s_amt"] = s_amt
    out["amt_ratio"] = amt_ratio

    # ===== 6. 量比得分 (0-25) =====
    s_vr = np.zeros(n)
    # 量比 1.0-2.0 最佳 (温和放量)
    s_vr[(vol_ratio >= 1.0) & (vol_ratio < 2.0)] = 25
    s_vr[(vol_ratio >= 2.0) & (vol_ratio < 3.0)] = 18
    s_vr[(vol_ratio >= 0.8) & (vol_ratio < 1.0)] = 15
    s_vr[vol_ratio >= 3.0] = 10  # 放量过大, 主力可能出货
    s_vr[vol_ratio < 0.8] = 8
    out["s_vr"] = s_vr

    # ===== 7. capital_score = 量价30 + OBV25 + 量比25 + 成交额20 =====
    out["capital_score"] = s_vp + s_obv + s_vr + s_amt

    # ===== 8. cap_accel (资金加速度) =====
    # 今日量比 - 5日平均量比
    vr_ma5 = _rolling_mean(vol_ratio, 5)
    cap_accel = vol_ratio - vr_ma5
    out["cap_accel"] = cap_accel

    # ===== 9. cap_persist (资金持续性, 0-100) =====
    # 近10天中 (vol > vol_ma5 且 涨幅 > 0) 的天数 * 10
    cond_inflow = (VOL > vol_ma5) & (pct_chg > 0)
    # 向量化: 用 cumsum 计算滚动窗口内的 True 数
    cond_int = cond_inflow.astype(int)
    cumsum = np.cumsum(cond_int)
    cap_persist = np.zeros(n)
    if n >= 10:
        cap_persist[9:] = (cumsum[9:] - cumsum[:-9] - cond_int[:-9]) * 10
        cap_persist[cap_persist > 100] = 100
    out["cap_persist"] = cap_persist

    # ===== 10. cap_conc (资金集中度) =====
    # 当日成交额 / 20日平均成交额 (与 amt_ratio 相同, 但用于趋势判断)
    out["cap_conc"] = amt_ratio
    # 集中度变化 (今日 - 昨日)
    cap_conc_chg = np.zeros(n)
    cap_conc_chg[1:] = amt_ratio[1:] - amt_ratio[:-1]
    out["cap_conc_chg"] = cap_conc_chg

    # ===== 11. leader_score (龙头得分, 0-100) =====
    # 涨停次数 * 15 + 连板数 * 10 + 量比 * 5 + 涨幅 * 2 (封顶100)
    # 涨停判断
    # 涨停阈值: 双创 (创业板 300/301, 科创板 688/689) 1.198, 主板 1.098, 北交所 1.298
    if ts_code.startswith(("300", "301", "688", "689")):
        zt_threshold = 1.198
    elif ts_code.endswith(".BJ"):
        zt_threshold = 1.298
    else:
        zt_threshold = 1.098
    zt = np.zeros(n, dtype=bool)
    if n >= 2:
        zt[1:] = C[1:] / C[:-1] >= zt_threshold
    # 近10天涨停次数
    zt_int = zt.astype(int)
    zt_cumsum = np.cumsum(zt_int)
    zt_count_10d = np.zeros(n)
    if n >= 10:
        zt_count_10d[9:] = zt_cumsum[9:] - zt_cumsum[:-9] - zt_int[:-9]

    # 连板数 (连续涨停天数)
    streak = np.zeros(n)
    for i in range(1, n):
        if zt[i]:
            streak[i] = streak[i-1] + 1

    # 历史最大连板数 (截至当日, 不向前看)
    max_streak = np.maximum.accumulate(streak)
    # 龙头硬约束: 最大连板 >= leader_min_streak (硬约束下限3, 防止误设)
    _min_streak = max(3, int(leader_min_streak))
    is_leader = max_streak >= _min_streak

    leader_score = (zt_count_10d * 15 + streak * 10 +
                    np.minimum(vol_ratio, 3) * 5 + np.maximum(pct_chg, 0) * 2)
    leader_score = np.minimum(leader_score, 100)
    out["leader_score"] = leader_score
    out["zt_count_10d"] = zt_count_10d
    out["zt_streak"] = streak
    out["max_streak"] = max_streak
    out["is_leader"] = is_leader

    # ===== 12. theme_lifecycle (主题生命周期) =====
    # 用个股自身的趋势状态 + 量能 + 涨幅判断
    ma5 = out["MA5"].values if "MA5" in out.columns else _rolling_mean(C, 5)
    ma10 = out["MA10"].values if "MA10" in out.columns else _rolling_mean(C, 10)
    ma20 = out["MA20"].values if "MA20" in out.columns else _rolling_mean(C, 20)

    lifecycle = np.array(["unknown"] * n, dtype=object)

    # early trend: ma5 刚突破 ma10, 量比 1.0-1.5, 涨幅 > 0
    early = (pd.notna(ma5) & pd.notna(ma10) & (ma5 > ma10) &
             (vol_ratio >= 1.0) & (vol_ratio < 1.5) & (pct_chg > 0))

    # expansion: ma5 > ma10 > ma20, 量比 > 1.0, 资金持续
    expansion = (pd.notna(ma5) & pd.notna(ma10) & pd.notna(ma20) &
                 (ma5 > ma10) & (ma10 > ma20) &
                 (vol_ratio > 1.0) & (cap_persist >= 40) &
                 (pct_chg > -2))

    # climax: 量比 > 2.0 但涨幅小 (放量滞涨), 或 价格远离 ma20
    dist_ma20 = np.where((pd.notna(ma20)) & (ma20 > 0), C / ma20 - 1, 0)
    climax = ((vol_ratio > 2.0) & (pct_chg < 2) & (pct_chg > -2) |
              (dist_ma20 > 0.15))

    # rotation: ma5 < ma10, 量比 < 0.8, 或 资金持续下降
    rotation = ((pd.notna(ma5) & pd.notna(ma10) & (ma5 < ma10)) &
                ((vol_ratio < 0.8) | (cap_accel < -0.3)))

    # 优先级: climax > expansion > early > rotation
    # (climax 最重要, 因为是卖出信号)
    lifecycle[rotation] = "rotation"
    lifecycle[early] = "early"
    lifecycle[expansion] = "expansion"
    lifecycle[climax] = "climax"
    # 默认 (无明确信号)
    lifecycle[lifecycle == "unknown"] = "neutral"
    out["theme_lifecycle"] = lifecycle

    return out


# =========================================================
# 回测引擎
# =========================================================
@dataclass
class CapFlowConfig:
    """资本流策略参数"""
    capital_score_min: float = 70.0    # 资金得分下限
    cap_accel_min: float = 0.0         # 资金加速度下限
    cap_persist_min: float = 60.0      # 资金持续性下限
    leader_score_min: float = 30.0     # 龙头得分下限 (仅参考, 真正判定用 is_leader)
    require_leader: bool = True        # 硬约束: is_leader 才入场
    leader_min_streak: int = 3         # 龙头最小连板数 (硬约束下限3, 可调到4/5)
    lifecycle_required: str = "expansion"  # 要求的生命周期
    # 退出条件
    exit_cap_accel: float = 0.0        # cap_accel < 此值则退出
    exit_cap_conc_chg: float = 0.0     # cap_conc 下降则退出
    max_hold_days: int = 20            # 最大持有天数
    stop_loss: float = -10.0           # 止损线 (%)


class CapFlowBacktester:
    """资本流状态切换策略回测"""

    def __init__(self, pool_csv: str,
                 start_date: str = "20250101",
                 end_date: str = None,
                 lookback_days: int = 400,
                 leader_min_streak: int = 3):
        from datetime import datetime, timedelta
        self.start_date = start_date
        self.end_date = end_date or datetime.now().strftime("%Y%m%d")
        self.leader_min_streak = max(3, int(leader_min_streak))

        self.pool = pd.read_csv(pool_csv)
        print(f"[Pool] 股池: {len(self.pool)} 只")

        self.kline_dict: Dict[str, pd.DataFrame] = {}
        self._date_idx_map: Dict[str, Dict[str, int]] = {}
        self._load_all_klines(lookback_days)

        all_dates = set()
        for df in self.kline_dict.values():
            all_dates.update(df["trade_date"].tolist())
        self.trade_dates = sorted([d for d in all_dates
                                    if self.start_date <= d <= self.end_date])
        print(f"[Backtest] 区间: {self.start_date} ~ {self.end_date}, "
              f"交易日: {len(self.trade_dates)}")

    def _load_all_klines(self, lookback_days: int):
        from datetime import datetime, timedelta
        dt = datetime.strptime(self.start_date, "%Y%m%d")
        load_start = (dt - timedelta(days=lookback_days)).strftime("%Y%m%d")

        t0 = time.time()
        n_ok, n_fail = 0, 0
        for i, row in self.pool.iterrows():
            ts_code = code_to_ts_code(row["code"])
            df = load_kline(ts_code, start_date=load_start, end_date=self.end_date)
            if df.empty or len(df) < 30:
                n_fail += 1
                continue
            try:
                df = add_indicators(df)
                df = compute_cap_flow(df, ts_code=ts_code,
                                      leader_min_streak=self.leader_min_streak)
                # 涨停阈值
                if ts_code.startswith(("3", "688", "689")):
                    df["_zt_up"] = 1.198
                else:
                    df["_zt_up"] = 1.098
            except Exception:
                n_fail += 1
                continue
            self.kline_dict[ts_code] = df
            self._date_idx_map[ts_code] = dict(zip(df["trade_date"], df.index))
            n_ok += 1

            if (i + 1) % 200 == 0:
                print(f"  [{i+1}/{len(self.pool)}] 已加载 {n_ok} 只, "
                      f"耗时 {time.time()-t0:.1f}s")

        print(f"[Load] 加载 {n_ok} 只, 失败 {n_fail}, 总耗时 {time.time()-t0:.1f}s")

    def _check_entry(self, df: pd.DataFrame, i: int,
                     cfg: CapFlowConfig) -> Tuple[bool, Dict]:
        """检查入场条件"""
        row = df.iloc[i]
        reasons = {
            "capital_score": row["capital_score"],
            "cap_accel": row["cap_accel"],
            "cap_persist": row["cap_persist"],
            "leader_score": row["leader_score"],
            "max_streak": row["max_streak"],
            "is_leader": row["is_leader"],
            "lifecycle": row["theme_lifecycle"],
        }
        # 龙头硬约束: require_leader=True 时必须 is_leader (max_streak>=3)
        leader_ok = (not cfg.require_leader) or row["is_leader"]
        ok = (row["capital_score"] > cfg.capital_score_min and
              row["cap_accel"] > cfg.cap_accel_min and
              row["cap_persist"] > cfg.cap_persist_min and
              row["theme_lifecycle"] == cfg.lifecycle_required and
              leader_ok)
        return ok, reasons

    def _check_exit(self, df: pd.DataFrame, i: int,
                    cfg: CapFlowConfig, hold_days: int,
                    entry_price: float) -> Tuple[bool, str]:
        """检查退出条件"""
        row = df.iloc[i]
        # 1. 资金减速
        if row["cap_accel"] < cfg.exit_cap_accel:
            return True, "cap_accel<0"
        # 2. 资金集中度下降
        if row["cap_conc_chg"] < cfg.exit_cap_conc_chg:
            return True, "cap_conc下降"
        # 3. 生命周期恶化 (climax 或 rotation)
        if row["theme_lifecycle"] in ("climax", "rotation"):
            return True, f"lifecycle={row['theme_lifecycle']}"
        # 4. 最大持有天数
        if hold_days >= cfg.max_hold_days:
            return True, "max_hold"
        # 5. 止损
        ret = (row["close"] / entry_price - 1) * 100
        if ret < cfg.stop_loss:
            return True, f"止损{ret:.1f}%"
        return False, ""

    def run_backtest(self, cfg: CapFlowConfig = None,
                     top_n: int = 5, verbose: bool = True) -> Dict:
        """完整回测: 动态持仓 (不等固定天数)"""
        if cfg is None:
            cfg = CapFlowConfig()

        # 持仓: {ts_code: {entry_idx, entry_price, entry_date, hold_days}}
        holdings: Dict[str, Dict] = {}
        trade_records = []
        daily_counts = []

        t0 = time.time()
        for i, td in enumerate(self.trade_dates):
            daily_signals = 0

            # ===== 1. 检查现有持仓是否退出 =====
            to_remove = []
            for ts_code, pos in holdings.items():
                df = self.kline_dict.get(ts_code)
                if df is None:
                    to_remove.append(ts_code)
                    continue
                idx_map = self._date_idx_map[ts_code]
                cur_i = idx_map.get(td)
                if cur_i is None:
                    continue
                pos["hold_days"] += 1
                should_exit, reason = self._check_exit(
                    df, cur_i, cfg, pos["hold_days"], pos["entry_price"])
                if should_exit:
                    # T+1 开盘卖出 (简化: 当日收盘卖出)
                    sell_price = df.iloc[cur_i]["close"]
                    ret = (sell_price / pos["entry_price"] - 1) * 100
                    trade_records.append({
                        "ts_code": ts_code,
                        "signal_date": pos["entry_date"],
                        "buy_date": pos["entry_date"],
                        "buy_price": pos["entry_price"],
                        "sell_date": td,
                        "sell_price": round(sell_price, 2),
                        "hold_days": pos["hold_days"],
                        "return": round(ret, 2),
                        "exit_reason": reason,
                        "capital_score": pos.get("capital_score", 0),
                        "leader_score": pos.get("leader_score", 0),
                        "max_streak": pos.get("max_streak", 0),
                        "is_leader": pos.get("is_leader", False),
                        "lifecycle": pos.get("lifecycle", ""),
                    })
                    to_remove.append(ts_code)
            for ts_code in to_remove:
                holdings.pop(ts_code, None)

            # ===== 2. 选出新的入场信号 =====
            signals = []
            for ts_code, df in self.kline_dict.items():
                if ts_code in holdings:
                    continue  # 已持仓, 跳过
                idx_map = self._date_idx_map[ts_code]
                cur_i = idx_map.get(td)
                if cur_i is None or cur_i < 30:
                    continue
                ok, reasons = self._check_entry(df, cur_i, cfg)
                if ok:
                    signals.append((ts_code, reasons))

            # 按资金得分排序, 取 top_n
            if top_n and len(signals) > top_n:
                signals.sort(key=lambda x: -x[1]["capital_score"])
                signals = signals[:top_n]
            daily_signals = len(signals)
            daily_counts.append(daily_signals)

            # ===== 3. T+1 开盘买入 (这里简化为当日收盘买入) =====
            for ts_code, reasons in signals:
                df = self.kline_dict[ts_code]
                idx_map = self._date_idx_map[ts_code]
                cur_i = idx_map[td]
                # T+1 开盘
                if cur_i + 1 < len(df):
                    buy_idx = cur_i + 1
                    buy_row = df.iloc[buy_idx]
                    # 检查 T+1 是否涨停 (无法买入)
                    prev_close = df.iloc[cur_i]["close"]
                    if buy_row["open"] >= prev_close * buy_row["_zt_up"] * 0.999:
                        continue
                    buy_price = buy_row["open"]
                    buy_date = buy_row["trade_date"]
                else:
                    continue
                holdings[ts_code] = {
                    "entry_idx": buy_idx,
                    "entry_price": buy_price,
                    "entry_date": buy_date,
                    "hold_days": 0,
                    "capital_score": reasons["capital_score"],
                    "leader_score": reasons["leader_score"],
                    "max_streak": reasons["max_streak"],
                    "is_leader": reasons["is_leader"],
                    "lifecycle": reasons["lifecycle"],
                }

            if verbose and (i % 20 == 0 or i == len(self.trade_dates) - 1):
                elapsed = time.time() - t0
                eta = elapsed / (i + 1) * (len(self.trade_dates) - i - 1)
                print(f"  [{i+1}/{len(self.trade_dates)}] {td}: "
                      f"新信号 {daily_signals}, 持仓 {len(holdings)}, "
                      f"已平仓 {len(trade_records)}, 耗时 {elapsed:.1f}s, ETA {eta:.0f}s")

        # ===== 强制平仓剩余持仓 =====
        last_td = self.trade_dates[-1] if self.trade_dates else None
        for ts_code, pos in holdings.items():
            df = self.kline_dict.get(ts_code)
            if df is None or last_td is None:
                continue
            idx_map = self._date_idx_map[ts_code]
            cur_i = idx_map.get(last_td, len(df) - 1)
            sell_price = df.iloc[cur_i]["close"]
            ret = (sell_price / pos["entry_price"] - 1) * 100
            trade_records.append({
                "ts_code": ts_code,
                "signal_date": pos["entry_date"],
                "buy_date": pos["entry_date"],
                "buy_price": pos["entry_price"],
                "sell_date": last_td,
                "sell_price": round(sell_price, 2),
                "hold_days": pos["hold_days"],
                "return": round(ret, 2),
                "exit_reason": "末尾平仓",
                "capital_score": pos.get("capital_score", 0),
                "leader_score": pos.get("leader_score", 0),
                "max_streak": pos.get("max_streak", 0),
                "is_leader": pos.get("is_leader", False),
                "lifecycle": pos.get("lifecycle", ""),
            })

        # ===== 统计 =====
        returns = [r["return"] for r in trade_records]
        rets = np.array(returns) if returns else np.array([0])
        win_rate = (rets > 0).mean() * 100 if len(returns) > 0 else 0
        avg_ret = rets.mean() if len(returns) > 0 else 0
        med_ret = np.median(rets) if len(returns) > 0 else 0

        # 退出原因统计
        exit_reasons = {}
        for r in trade_records:
            reason = r["exit_reason"]
            exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

        # 持有天数统计
        hold_days = [r["hold_days"] for r in trade_records]
        avg_hold = np.mean(hold_days) if hold_days else 0

        return {
            "trade_records": trade_records,
            "all_returns": returns,
            "daily_counts": daily_counts,
            "win_rate": round(win_rate, 1),
            "avg_return": round(avg_ret, 2),
            "median_return": round(med_ret, 2),
            "n_signals": len(trade_records),
            "avg_hold_days": round(avg_hold, 1),
            "exit_reasons": exit_reasons,
            "n_total_days": len(self.trade_dates),
            "n_days_1_5": int(sum(1 for c in daily_counts if 1 <= c <= 5)),
        }


# =========================================================
# 主入口
# =========================================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description="资本流状态切换策略回测")
    parser.add_argument("--pool", type=str,
                        default=r"D:\mystock\solo\report_daily\bull_stocks_qualified.csv")
    parser.add_argument("--start", type=str, default="20250101")
    parser.add_argument("--end", type=str, default=None)
    parser.add_argument("--top-n", type=int, default=5, help="每日最多选N只")
    parser.add_argument("--capital-score", type=float, default=70.0)
    parser.add_argument("--cap-accel", type=float, default=0.0)
    parser.add_argument("--cap-persist", type=float, default=60.0)
    parser.add_argument("--leader-score", type=float, default=30.0)
    parser.add_argument("--require-leader", type=str, default="true",
                        choices=["true", "false"],
                        help="是否强制要求龙头 (max_streak>=3)")
    parser.add_argument("--leader-min-streak", type=int, default=3,
                        help="龙头最小连板数 (硬约束下限3, 可调到4/5)")
    parser.add_argument("--lifecycle", type=str, default="expansion",
                        choices=["early", "expansion", "climax", "rotation", "neutral"])
    parser.add_argument("--max-hold", type=int, default=20)
    parser.add_argument("--stop-loss", type=float, default=-10.0)
    args = parser.parse_args()

    cfg = CapFlowConfig(
        capital_score_min=args.capital_score,
        cap_accel_min=args.cap_accel,
        cap_persist_min=args.cap_persist,
        leader_score_min=args.leader_score,
        require_leader=(args.require_leader == "true"),
        leader_min_streak=args.leader_min_streak,
        lifecycle_required=args.lifecycle,
        max_hold_days=args.max_hold,
        stop_loss=args.stop_loss,
    )

    bt = CapFlowBacktester(pool_csv=args.pool, start_date=args.start,
                           end_date=args.end,
                           leader_min_streak=cfg.leader_min_streak)
    res = bt.run_backtest(cfg=cfg, top_n=args.top_n)

    print("\n" + "=" * 70)
    print("  资本流状态切换策略 - 回测结果")
    print("=" * 70)
    print(f"  回测区间:     {args.start} ~ {args.end or '最新'}")
    print(f"  交易日数:     {res['n_total_days']}")
    print(f"  入场条件:")
    print(f"    capital_score > {cfg.capital_score_min}")
    print(f"    cap_accel > {cfg.cap_accel_min}")
    print(f"    cap_persist > {cfg.cap_persist_min}")
    print(f"    leader_score > {cfg.leader_score_min} (仅参考)")
    if cfg.require_leader:
        print(f"    require_leader = True (硬约束: max_streak >= {cfg.leader_min_streak})")
    else:
        print(f"    require_leader = False (不强制龙头)")
    print(f"    lifecycle = {cfg.lifecycle_required}")
    print(f"  退出条件:")
    print(f"    cap_accel < {cfg.exit_cap_accel} 或 cap_conc下降 或 lifecycle恶化")
    print(f"    最大持有 {cfg.max_hold_days} 天, 止损 {cfg.stop_loss}%")
    print(f"  ----")
    print(f"  总信号数:     {res['n_signals']}")
    print(f"  胜率:         {res['win_rate']}%")
    print(f"  平均收益:     {res['avg_return']}%")
    print(f"  中位收益:     {res['median_return']}%")
    print(f"  平均持有天数: {res['avg_hold_days']}")
    print(f"  日均选股:     {np.mean(res['daily_counts']):.1f}")
    print(f"  选股1-5只天数: {res['n_days_1_5']}/{res['n_total_days']} "
          f"({res['n_days_1_5']/max(res['n_total_days'],1)*100:.1f}%)")

    if res["all_returns"]:
        rets = np.array(res["all_returns"])
        print(f"  最大盈利:     {rets.max():.2f}%")
        print(f"  最大亏损:     {rets.min():.2f}%")
        wins = rets[rets > 0]
        losses = rets[rets < 0]
        if len(losses) > 0 and len(wins) > 0:
            print(f"  盈亏比:       {abs(wins.mean()/losses.mean()):.2f}")

    print(f"\n  退出原因统计:")
    for reason, cnt in sorted(res["exit_reasons"].items(),
                               key=lambda x: -x[1]):
        pct = cnt / res["n_signals"] * 100 if res["n_signals"] > 0 else 0
        print(f"    {reason}: {cnt} 笔 ({pct:.1f}%)")

    # 按 is_leader / max_streak 档位分析
    if res["trade_records"]:
        df_trades = pd.DataFrame(res["trade_records"])
        print(f"\n  按 max_streak 档位分析:")
        df_trades["streak_bin"] = pd.cut(df_trades["max_streak"],
                                          bins=[-1, 2, 3, 5, 100],
                                          labels=["<3","3","4-5","6+"])
        for bin_name, grp in df_trades.groupby("streak_bin", observed=False):
            if len(grp) > 0:
                wr = (grp["return"] > 0).mean() * 100
                avg = grp["return"].mean()
                print(f"    max_streak {bin_name}: {len(grp)}笔, "
                      f"胜率{wr:.1f}%, 均收益{avg:.2f}%")
        print(f"\n  按 leader_score 档位分析:")
        df_trades["leader_bin"] = pd.cut(df_trades["leader_score"],
                                          bins=[0, 30, 50, 70, 100],
                                          labels=["<30","30-50","50-70","70+"])
        for bin_name, grp in df_trades.groupby("leader_bin", observed=False):
            if len(grp) > 0:
                wr = (grp["return"] > 0).mean() * 100
                avg = grp["return"].mean()
                print(f"    leader {bin_name}: {len(grp)}笔, "
                      f"胜率{wr:.1f}%, 均收益{avg:.2f}%")

    # 保存交易记录
    if res["trade_records"]:
        out_path = os.path.join(os.path.dirname(args.pool),
                                 "cap_flow_backtest_trades.csv")
        pd.DataFrame(res["trade_records"]).to_csv(
            out_path, index=False, encoding="utf-8-sig")
        print(f"\n  [交易记录已保存] {out_path}")


if __name__ == "__main__":
    main()
