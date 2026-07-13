# -*- coding: utf-8 -*-
"""
回升买点策略 TDX 回测框架 (向量化加速)

策略来源: d:/mystock/solo/etf_resonance/rebound_detector.py
数据源:   通达信本地 .day 文件

形态定义 (回升买点):
  - L0(低) -> H1(高) -> L2(低) 简化波浪结构
  - W1涨幅: 40%~200%
  - W2回调: 20%~85%
  - L2 > L0 (铁律)
  - L2后天数: 3~30天
  - 当前价 > L2 且 < H1 (未突破前高)
  - 回升 > 0

信号评分(满分100):
  MA5突破 +15, MA10突破 +15, MA20突破 +20
  量比>1.0 +15, 回升>5% +15 (or >0 +10)
  距H1>5% +10, L2后5-15天 +10

回测模式: T+1 开盘买入, 持有 N 天收盘卖出
涨停过滤: T+1 开盘价 >= 前收×涨停板×0.999 时跳过
"""
from __future__ import annotations
import os
import sys
import time
from typing import Dict, List, Tuple, Optional

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

TDX_BT_DIR = r"d:\mystock\tdx_backtest"
sys.path.insert(0, TDX_BT_DIR)
from data_loader import load_kline, iter_all_day_files, tdx_filename_to_ts_code

# =========================================================
# 策略常量
# =========================================================
PIVOT_WINDOW = 5
W1_MIN_GAIN = 0.60
W1_MAX_GAIN = 1.0
W2_RETRACE_MIN = 0.20
W2_RETRACE_MAX = 0.85
W2_BREAKOUT_THRESHOLD = 0.70

DAYS_SINCE_L2_MIN = 3
DAYS_SINCE_L2_MAX = 15
VOL_RATIO_MIN = 1.0

MIN_SCORE = 60

INCLUDE_CHUANGCHUANG = True
CHUANGCHUANG_ONLY = True
EXCLUDE_KECHUANG = False  # 回升买点先不限制板块，回测分析后再决定


# =========================================================
# 板块判定
# =========================================================
def is_tradeable(ts_code: str) -> bool:
    if not ts_code or "." not in ts_code:
        return False
    sym = ts_code.split(".")[0]
    if sym.startswith(("999", "8", "4")):
        return False
    if CHUANGCHUANG_ONLY:
        return sym.startswith(("3", "688", "689"))
    if INCLUDE_CHUANGCHUANG:
        return sym.startswith(("60", "00", "3", "688", "689"))
    return sym.startswith(("60", "00"))


# =========================================================
# 向量化 pivot 检测
# =========================================================
def find_pivots_vec(highs: np.ndarray, lows: np.ndarray, dates: np.ndarray,
                    window: int = PIVOT_WINDOW) -> List[Tuple]:
    """向量化枢轴点检测，返回 [(idx, date, price, kind), ...]"""
    n = len(highs)
    if n < window * 2 + 1:
        return []

    pivots = []
    for i in range(window, n - window):
        left_h = highs[i - window:i]
        right_h = highs[i + 1:i + 1 + window]
        if highs[i] >= np.max(left_h) and highs[i] >= np.max(right_h):
            pivots.append((i, str(dates[i]), float(highs[i]), 'high'))

        left_l = lows[i - window:i]
        right_l = lows[i + 1:i + 1 + window]
        if lows[i] <= np.min(left_l) and lows[i] <= np.min(right_l):
            pivots.append((i, str(dates[i]), float(lows[i]), 'low'))

    pivots.sort(key=lambda x: x[0])

    # 去重：合并相邻同类型
    if not pivots:
        return []
    out = [pivots[0]]
    for p in pivots[1:]:
        last = out[-1]
        if p[3] == last[3]:
            if (p[3] == 'high' and p[2] > last[2]) or \
               (p[3] == 'low' and p[2] < last[2]):
                out[-1] = p
        else:
            out.append(p)
    return out


# =========================================================
# 回升买点信号检测 (单只股票，全日期)
# =========================================================
def detect_rebound_signals_vec(df: pd.DataFrame) -> Tuple[np.ndarray, List[Dict]]:
    """
    检测回升买点信号，返回 (signal_array, info_list)
    signal_array[i] = True 表示第i天有信号
    """
    n = len(df)
    signals = np.zeros(n, dtype=bool)
    infos = []

    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values
    vol = df['vol'].values if 'vol' in df.columns else np.ones(n)
    dates = df['trade_date'].values

    if n < 60:
        return signals, infos

    # 计算 MA
    ma5 = np.full(n, np.nan)
    ma10 = np.full(n, np.nan)
    ma20 = np.full(n, np.nan)
    for i in range(4, n):
        ma5[i] = np.mean(closes[i - 4:i + 1])
    for i in range(9, n):
        ma10[i] = np.mean(closes[i - 9:i + 1])
    for i in range(19, n):
        ma20[i] = np.mean(closes[i - 19:i + 1])

    # 量比
    vol_5 = np.full(n, np.nan)
    vol_20 = np.full(n, np.nan)
    for i in range(4, n):
        vol_5[i] = np.mean(vol[i - 4:i + 1])
    for i in range(19, n):
        vol_20[i] = np.mean(vol[i - 19:i + 1])

    # 找 pivot 点
    pivots = find_pivots_vec(highs, lows, dates, PIVOT_WINDOW)
    if len(pivots) < 3:
        return signals, infos

    # 遍历所有 L0->H1->L2 结构
    for i in range(len(pivots) - 2):
        if pivots[i][3] != 'low':
            continue
        L0 = pivots[i]

        # 找 H1
        H1 = None
        for j in range(i + 1, len(pivots)):
            if pivots[j][3] == 'high':
                H1 = pivots[j]
                break
        if H1 is None:
            continue

        # 找 L2
        L2 = None
        for j in range(i + 2, len(pivots)):
            if pivots[j][3] == 'low':
                L2 = pivots[j]
                break
        if L2 is None:
            continue

        L0_price = L0[2]
        H1_price = H1[2]
        L2_price = L2[2]
        L0_idx = L0[0]
        H1_idx = H1[0]
        L2_idx = L2[0]

        # 涨幅和回调
        w1_gain = (H1_price - L0_price) / max(L0_price, 0.01)
        if w1_gain < W1_MIN_GAIN or w1_gain > W1_MAX_GAIN:
            continue

        w2_retrace = (H1_price - L2_price) / max(H1_price - L0_price, 0.01)
        if not (W2_RETRACE_MIN <= w2_retrace <= W2_RETRACE_MAX):
            continue

        if L2_price <= L0_price:
            continue
        if L2_idx < H1_idx:
            continue

        # 信号有效期: L2后3~30天，且未突破H1
        signal_start = L2_idx + DAYS_SINCE_L2_MIN
        signal_end = min(L2_idx + DAYS_SINCE_L2_MAX + 1, n)

        for day_idx in range(signal_start, signal_end):
            if day_idx < 0 or day_idx >= n:
                continue
            cur_price = closes[day_idx]

            # 已回升
            if cur_price <= L2_price:
                continue
            # 未突破H1
            if cur_price >= H1_price:
                continue

            rebound_pct = (cur_price / L2_price - 1) * 100
            if rebound_pct <= 0:
                continue

            dist_to_H1 = (H1_price / cur_price - 1) * 100
            days_since_L2 = day_idx - L2_idx

            # 评分
            score = 0
            if day_idx >= 4 and cur_price > ma5[day_idx]:
                score += 15
            if day_idx >= 9 and cur_price > ma10[day_idx]:
                score += 15
            if day_idx >= 19 and cur_price > ma20[day_idx]:
                score += 20

            vr = vol_5[day_idx] / vol_20[day_idx] if day_idx >= 19 and vol_20[day_idx] > 0 else 0
            if vr > 1.0:
                score += 15

            if rebound_pct > 5:
                score += 15
            elif rebound_pct > 0:
                score += 10

            if dist_to_H1 > 5:
                score += 10

            if 5 <= days_since_L2 <= 15:
                score += 10

            if score < MIN_SCORE:
                continue

            # 优化v2: 量比≥1.0硬过滤 (量比<1.0无Alpha)
            if vr < VOL_RATIO_MIN:
                continue

            # 信号类型
            if w2_retrace >= W2_BREAKOUT_THRESHOLD:
                signal_type = '低吸'
            else:
                continue  # 优化v2: 仅保留低吸(W2≥70%), 突破信号胜率低

            signals[day_idx] = True
            infos.append({
                "signal_idx": day_idx,
                "signal_date": str(dates[day_idx]),
                "score": float(score),
                "signal_type": signal_type,
                "w1_gain": round(w1_gain * 100, 1),
                "w2_retrace": round(w2_retrace * 100, 1),
                "rebound_pct": round(rebound_pct, 1),
                "dist_to_H1": round(dist_to_H1, 1),
                "days_since_L2": days_since_L2,
                "vol_ratio": round(vr, 2),
                "current_price": round(cur_price, 2),
                "L0_date": L0[1],
                "H1_date": H1[1],
                "L2_date": L2[1],
                "L0_price": round(L0_price, 2),
                "H1_price": round(H1_price, 2),
                "L2_price": round(L2_price, 2),
            })

    return signals, infos


# =========================================================
# 回测引擎
# =========================================================
class ReboundBacktester:
    def __init__(self, start_date: str, end_date: str,
                 pool_codes: Optional[List[str]] = None,
                 max_stocks: Optional[int] = None,
                 lookback_days: int = 180):
        self.start_date = start_date
        self.end_date = end_date
        self.pool_codes = set(pool_codes) if pool_codes else None
        self.lookback_days = lookback_days

        self.kline_dict: Dict[str, pd.DataFrame] = {}
        self._date_idx_map: Dict[str, Dict[str, int]] = {}
        self._signal_cache: Dict[str, Tuple[np.ndarray, List[Dict]]] = {}
        self._load_all_klines_and_signals(max_stocks)

        all_dates = set()
        for df in self.kline_dict.values():
            all_dates.update(df["trade_date"].tolist())
        self.trade_dates = sorted([d for d in all_dates
                                   if self.start_date <= d <= self.end_date])
        pool_desc = f"{len(self.pool_codes)}只指定股池" if self.pool_codes else "全双创"
        print(f"[Backtest] 区间: {self.start_date} ~ {self.end_date}, "
              f"交易日: {len(self.trade_dates)}, 股池: {pool_desc}", flush=True)

    def _load_all_klines_and_signals(self, max_stocks: Optional[int]):
        from datetime import datetime, timedelta
        dt = datetime.strptime(self.start_date, "%Y%m%d")
        load_start = (dt - timedelta(days=self.lookback_days)).strftime("%Y%m%d")

        t0 = time.time()
        n_ok, n_skip, n_with_signal = 0, 0, 0
        for path in iter_all_day_files(markets=("SH", "SZ")):
            ts_code = tdx_filename_to_ts_code(path)
            if not ts_code:
                continue
            if not is_tradeable(ts_code):
                continue
            if EXCLUDE_KECHUANG and ts_code.split(".")[0].startswith(("688", "689")):
                continue
            if self.pool_codes is not None and ts_code not in self.pool_codes:
                continue
            if max_stocks and n_ok >= max_stocks:
                break
            df = load_kline(ts_code, start_date=load_start, end_date=self.end_date)
            if df.empty or len(df) < 60:
                n_skip += 1
                continue

            sym = ts_code.split(".")[0]
            if sym.startswith(("3", "688", "689")):
                df["_zt_up"] = 1.198
            else:
                df["_zt_up"] = 1.098

            try:
                signals, infos = detect_rebound_signals_vec(df)
            except Exception:
                n_skip += 1
                continue

            self.kline_dict[ts_code] = df
            self._date_idx_map[ts_code] = dict(zip(df["trade_date"], df.index))
            self._signal_cache[ts_code] = (signals, infos)
            n_ok += 1
            if signals.any():
                n_with_signal += 1

            if n_ok % 500 == 0:
                elapsed = time.time() - t0
                print(f"  [Loading] 已加载 {n_ok} 只 (含信号 {n_with_signal} 只), "
                      f"耗时 {elapsed:.1f}s", flush=True)

        elapsed = time.time() - t0
        print(f"[Load] 加载 {n_ok} 只 (含信号 {n_with_signal} 只), "
              f"跳过 {n_skip}, 耗时 {elapsed:.1f}s", flush=True)

    def run_single_day(self, trade_date: str) -> List[Tuple[str, Dict]]:
        selected = []
        for ts_code, (signals, infos) in self._signal_cache.items():
            if not signals.any():
                continue
            idx_map = self._date_idx_map[ts_code]
            i = idx_map.get(trade_date)
            if i is None or i >= len(signals):
                continue
            if signals[i]:
                # 找对应的info
                for info in infos:
                    if info["signal_idx"] == i:
                        selected.append((ts_code, info))
                        break
        return selected

    def evaluate_signals(self, selected: List[Tuple[str, Dict]],
                         trade_date: str, hold_days: int = 5) -> List[Dict]:
        records = []
        for ts_code, info in selected:
            df = self.kline_dict.get(ts_code)
            if df is None:
                continue
            idx_map = self._date_idx_map[ts_code]
            i = idx_map.get(trade_date)
            if i is None:
                continue

            buy_idx = i + 1
            if buy_idx >= len(df):
                continue
            buy_row = df.iloc[buy_idx]
            prev_close = df.iloc[i]["close"]
            zt_up = buy_row["_zt_up"]
            if buy_row["open"] >= prev_close * zt_up * 0.999:
                continue

            buy_price = buy_row["open"]
            buy_date = buy_row["trade_date"]

            sell_idx = min(buy_idx + hold_days, len(df) - 1)
            sell_row = df.iloc[sell_idx]
            sell_price = sell_row["close"]
            sell_date = sell_row["trade_date"]

            ret = (sell_price / buy_price - 1) * 100
            records.append({
                "ts_code": ts_code,
                "signal_date": trade_date,
                "buy_date": buy_date,
                "buy_price": round(buy_price, 2),
                "sell_date": sell_date,
                "sell_price": round(sell_price, 2),
                "hold_days": sell_idx - buy_idx,
                "return": round(ret, 2),
                "score": info.get("score", 0),
                "signal_type": info.get("signal_type", ""),
                "w1_gain": info.get("w1_gain", 0),
                "w2_retrace": info.get("w2_retrace", 0),
                "rebound_pct": info.get("rebound_pct", 0),
                "dist_to_H1": info.get("dist_to_H1", 0),
                "days_since_L2": info.get("days_since_L2", 0),
                "vol_ratio": info.get("vol_ratio", 0),
            })
        return records

    def run_backtest(self, hold_days: int = 5,
                     top_n: Optional[int] = None,
                     verbose: bool = True) -> Dict:
        daily_counts = []
        all_returns = []
        trade_records = []

        t0 = time.time()
        for i, td in enumerate(self.trade_dates):
            selected = self.run_single_day(td)

            if top_n and len(selected) > top_n:
                selected.sort(key=lambda x: -x[1].get("score", 0))
                selected = selected[:top_n]

            daily_counts.append(len(selected))

            if selected:
                records = self.evaluate_signals(selected, td, hold_days)
                for r in records:
                    all_returns.append(r["return"])
                    trade_records.append(r)

            if verbose and (i % 20 == 0 or i == len(self.trade_dates) - 1):
                elapsed = time.time() - t0
                eta = elapsed / (i + 1) * (len(self.trade_dates) - i - 1)
                print(f"  [{i+1}/{len(self.trade_dates)}] {td}: 选中 {len(selected)} 只, "
                      f"累计 {len(all_returns)} 笔, 耗时 {elapsed:.1f}s, ETA {eta:.0f}s", flush=True)

        all_returns_arr = np.array(all_returns) if all_returns else np.array([0])
        win_rate = (all_returns_arr > 0).mean() * 100 if all_returns else 0
        avg_ret = all_returns_arr.mean() if all_returns else 0
        med_ret = np.median(all_returns_arr) if all_returns else 0
        max_win = all_returns_arr.max() if all_returns else 0
        max_loss = all_returns_arr.min() if all_returns else 0
        win_arr = all_returns_arr[all_returns_arr > 0]
        loss_arr = all_returns_arr[all_returns_arr <= 0]
        avg_win = win_arr.mean() if len(win_arr) > 0 else 0
        avg_loss = loss_arr.mean() if len(loss_arr) > 0 else 0
        profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else 0

        daily_counts_arr = np.array(daily_counts)
        n_days_1_5 = int(((daily_counts_arr >= 1) & (daily_counts_arr <= 5)).sum())

        return {
            "daily_counts": daily_counts,
            "all_returns": all_returns,
            "trade_records": trade_records,
            "win_rate": round(win_rate, 1),
            "avg_return": round(avg_ret, 2),
            "median_return": round(med_ret, 2),
            "max_win": round(max_win, 2),
            "max_loss": round(max_loss, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "n_signals": len(all_returns),
            "n_days_1_5": n_days_1_5,
            "n_total_days": len(self.trade_dates),
        }


def _normalize_code(raw) -> str:
    """将各种格式的股票代码统一为 ts_code 格式 (如 002709.SZ)"""
    s = str(raw).strip()
    # 去掉已有后缀
    if "." in s:
        parts = s.split(".")
        num = parts[0].zfill(6)
        suffix = parts[1] if len(parts) > 1 else ""
        if not suffix:
            suffix = "SH" if num.startswith(("6", "9")) else "SZ"
        return f"{num}.{suffix}"
    # 纯数字
    num = s.zfill(6)
    if num.startswith(("6", "9")):
        return f"{num}.SH"
    else:
        return f"{num}.SZ"


def _load_pool_codes(pool_path: str) -> Optional[List[str]]:
    if not os.path.exists(pool_path):
        print(f"[Pool] 股池文件不存在: {pool_path}, 回退到全双创", flush=True)
        return None
    try:
        df = pd.read_csv(pool_path)
        code_col = None
        for c in ("ts_code", "code", "股票代码", "symbol"):
            if c in df.columns:
                code_col = c
                break
        if code_col:
            raw_codes = df[code_col].tolist()
            codes = [_normalize_code(c) for c in raw_codes]
            print(f"[Pool] 从 {os.path.basename(pool_path)} 加载 {len(codes)} 只股票", flush=True)
            return codes
    except Exception:
        pass
    return None


def print_results(result: Dict, hold_days: int, start: str, end: str, args):
    recs = result["trade_records"]
    print()
    print("=" * 80)
    print("  回测结果 (T+1 开盘买入)")
    print("=" * 80)
    print(f"  回测区间:     {start} ~ {end}")
    print(f"  交易日数:     {result['n_total_days']}")
    print(f"  持有天数:     {hold_days}")
    print(f"  总信号数:     {result['n_signals']}")
    print(f"  胜率:         {result['win_rate']}%")
    print(f"  平均收益:     {result['avg_return']:+.2f}%")
    print(f"  中位收益:     {result['median_return']:+.2f}%")
    print(f"  最大盈利:     {result['max_win']:+.2f}%")
    print(f"  最大亏损:     {result['max_loss']:+.2f}%")
    print(f"  平均盈利:     {result['avg_win']:+.2f}%")
    print(f"  平均亏损:     {result['avg_loss']:+.2f}%")
    print(f"  盈亏比:       {result['profit_factor']}")
    print(f"  日均选股数:   {result['n_signals']/max(result['n_total_days'],1):.1f}")
    print(f"  选股1-5只天数: {result['n_days_1_5']}/{result['n_total_days']} "
          f"({result['n_days_1_5']/max(result['n_total_days'],1)*100:.1f}%)")

    if not recs:
        print("\n  无交易记录")
        return

    # 评分分档
    print("\n  信号评分分档胜率:")
    for lo, hi in [(60, 70), (70, 80), (80, 90), (90, 100), (100, 999)]:
        sub = [r["return"] for r in recs if lo <= r["score"] < hi]
        if sub:
            wr = sum(1 for x in sub if x > 0) / len(sub) * 100
            avg = np.mean(sub)
            label = f"评分{lo}-{hi}" if hi < 999 else f"评分{lo}+"
            print(f"    {label}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:+.2f}%")

    # 信号类型分档
    print("\n  信号类型分档胜率:")
    for st in ['低吸', '突破']:
        sub = [r["return"] for r in recs if r["signal_type"] == st]
        if sub:
            wr = sum(1 for x in sub if x > 0) / len(sub) * 100
            avg = np.mean(sub)
            print(f"    {st}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:+.2f}%")

    # W1涨幅分档
    print("\n  W1涨幅分档胜率:")
    for lo, hi in [(40, 60), (60, 80), (80, 100), (100, 150), (150, 200)]:
        sub = [r["return"] for r in recs if lo <= r["w1_gain"] < hi]
        if sub:
            wr = sum(1 for x in sub if x > 0) / len(sub) * 100
            avg = np.mean(sub)
            print(f"    W1涨幅{lo}-{hi}%: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:+.2f}%")

    # W2回调分档
    print("\n  W2回调分档胜率:")
    for lo, hi in [(20, 40), (40, 55), (55, 70), (70, 85)]:
        sub = [r["return"] for r in recs if lo <= r["w2_retrace"] < hi]
        if sub:
            wr = sum(1 for x in sub if x > 0) / len(sub) * 100
            avg = np.mean(sub)
            print(f"    回调{lo}-{hi}%: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:+.2f}%")

    # 回升幅度分档
    print("\n  回升幅度分档胜率:")
    for lo, hi in [(0, 5), (5, 10), (10, 20), (20, 40), (40, 999)]:
        sub = [r["return"] for r in recs if lo <= r["rebound_pct"] < hi]
        if sub:
            wr = sum(1 for x in sub if x > 0) / len(sub) * 100
            avg = np.mean(sub)
            label = f"回升{lo}-{hi}%" if hi < 999 else f"回升{lo}%+"
            print(f"    {label}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:+.2f}%")

    # 距H1空间分档
    print("\n  距H1空间分档胜率:")
    for lo, hi in [(0, 5), (5, 10), (10, 20), (20, 999)]:
        sub = [r["return"] for r in recs if lo <= r["dist_to_H1"] < hi]
        if sub:
            wr = sum(1 for x in sub if x > 0) / len(sub) * 100
            avg = np.mean(sub)
            label = f"距H1 {lo}-{hi}%" if hi < 999 else f"距H1 {lo}%+"
            print(f"    {label}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:+.2f}%")

    # L2后天数分档
    print("\n  L2后天数分档胜率:")
    for lo, hi in [(3, 5), (5, 10), (10, 15), (15, 20), (20, 31)]:
        sub = [r["return"] for r in recs if lo <= r["days_since_L2"] < hi]
        if sub:
            wr = sum(1 for x in sub if x > 0) / len(sub) * 100
            avg = np.mean(sub)
            print(f"    L2后{lo}-{hi}天: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:+.2f}%")

    # 量比分档
    print("\n  量比分档胜率:")
    for lo, hi in [(0, 0.8), (0.8, 1.0), (1.0, 1.3), (1.3, 1.5), (1.5, 999)]:
        sub = [r["return"] for r in recs if lo <= r["vol_ratio"] < hi]
        if sub:
            wr = sum(1 for x in sub if x > 0) / len(sub) * 100
            avg = np.mean(sub)
            label = f"量比{lo}-{hi}" if hi < 999 else f"量比{lo}+"
            print(f"    {label}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:+.2f}%")

    # 板块分档
    print("\n  板块分档胜率:")
    for board, name in [("gem", "创业板"), ("star", "科创板")]:
        if board == "gem":
            sub = [r["return"] for r in recs if r["ts_code"].startswith("3")]
        else:
            sub = [r["return"] for r in recs if r["ts_code"].startswith(("688", "689"))]
        if sub:
            wr = sum(1 for x in sub if x > 0) / len(sub) * 100
            avg = np.mean(sub)
            print(f"    {name}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:+.2f}%")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="回升买点策略 TDX 回测 (向量化)")
    parser.add_argument("--start", type=str, default="20220101")
    parser.add_argument("--end", type=str, default=None)
    parser.add_argument("--max-stocks", type=int, default=None)
    parser.add_argument("--hold", type=int, default=5, help="持有天数")
    parser.add_argument("--top-n", type=int, default=None)
    parser.add_argument("--out", type=str, default=None)
    parser.add_argument("--pool", type=str,
                        default=r"d:\mystock\solo\report_daily\bull_stocks_qualified.csv",
                        help="股票池 CSV (含 ts_code/code 列)")
    parser.add_argument("--min-score", type=int, default=60, help="最低评分阈值")
    args = parser.parse_args()

    global MIN_SCORE
    MIN_SCORE = args.min_score

    pool_codes = _load_pool_codes(args.pool)

    print("=" * 80)
    print("  回升买点策略 TDX 回测 (T+1 开盘买入, 向量化)")
    print("=" * 80)
    print(f"  策略参数:")
    print(f"    W1涨幅: {W1_MIN_GAIN*100:.0f}%-{W1_MAX_GAIN*100:.0f}%")
    print(f"    W2回调: {W2_RETRACE_MIN*100:.0f}%-{W2_RETRACE_MAX*100:.0f}%")
    print(f"    L2后天数: {DAYS_SINCE_L2_MIN}-{DAYS_SINCE_L2_MAX}天")
    print(f"    最低评分: {MIN_SCORE}")
    print(f"    Pivot窗口: {PIVOT_WINDOW}")
    print(f"    涨停板开盘跳过 (避免追高)")
    print(f"  股池文件: {args.pool}")
    print(f"  板块范围: {'仅创业板' if EXCLUDE_KECHUANG else '创业板+科创板'}")
    print("=" * 80, flush=True)

    bt = ReboundBacktester(
        start_date=args.start,
        end_date=args.end,
        pool_codes=pool_codes,
        max_stocks=args.max_stocks,
    )

    result = bt.run_backtest(hold_days=args.hold, top_n=args.top_n)
    print_results(result, args.hold, args.start, args.end or "latest", args)

    # 保存交易记录
    out_path = args.out or "tdx_backtest_rebound_trades.csv"
    if result["trade_records"]:
        pd.DataFrame(result["trade_records"]).to_csv(out_path, index=False, encoding='utf-8-sig')
        print(f"\n  交易记录已保存: {out_path}")
    print("=" * 80)


if __name__ == "__main__":
    main()
