# -*- coding: utf-8 -*-
"""
Wave2 强势横盘算法 - 二波低吸回测

算法来源: d:/mystock/solo/multi_factor_picker/wave2_daily.py
接入框架: tdx_backtest (data_loader + indicators + SecondFilterBacktester)

形态定义:
  - 主板股票 (沪深 60/00 开头, 排除双创 688/689)
  - 近 SURGE_DAYS=20 天内存在一波拉升 >=20%
  - wave1 高点之后: 回调 <10% 且调整天数 <=15 天
  - 最小回调 >=5%, 调整期最长 60 天

触发条件 (优先级 1 最优):
  P1: RSI6<50 + 缩量 (5日均量 / wave1前20日均量 <0.8x)
  P2: MACD 金叉 (DIF>DEA) + 站上 MA20

回测模式: T+1 开盘买入, 持有 N 天收盘卖出
"""
from __future__ import annotations
import os
import sys
import time
from typing import Dict, List, Tuple, Optional

import numpy as np
import pandas as pd

# 强制 stdout 行缓冲 (避免管道模式下输出卡住)
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_loader import load_kline, iter_all_day_files, tdx_filename_to_ts_code
from indicators import MA, MACD, RSI


# =========================================================
# 算法常量 (与 solo/wave2_daily.py 保持一致)
# =========================================================
SURGE_DAYS = 20        # 一波拉升窗口
SURGE_MIN = 0.20       # 一波最低涨幅 20%
PULLBACK_MIN = 0.05    # 最小回调 5%
PULLBACK_MAX = 0.10    # 强势横盘: 回调 <10%
PULLBACK_DAYS_MAX = 15 # 强势横盘: 调整天数 <=15
ADJUST_MAX = 60        # 调整期最长 60 天
VOL_SHRINK_RATIO = 0.8 # 缩量阈值 (5日均量/基准量)
RSI_MAX = 50           # RSI 上限


# =========================================================
# 仅预计算本算法所需指标 (跳过 KDJ/BOLL/OBV, 提速 3-5x)
# =========================================================
def _precompute_minimal_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """只算 MA5/10/20/60 + RSI6 + MACD"""
    out = df.copy()
    close = out["close"]
    out["MA5"] = MA(close, 5)
    out["MA10"] = MA(close, 10)
    out["MA20"] = MA(close, 20)
    out["MA60"] = MA(close, 60)
    out["RSI6"] = RSI(close, 6)
    dif, dea, macd = MACD(close)
    out["DIF"] = dif
    out["DEA"] = dea
    out["MACD"] = macd
    return out


# =========================================================
# 工具: 主板判定 (排除双创 688/689, 排除指数 999/8/4)
# =========================================================
def is_main_board(ts_code: str) -> bool:
    """主板: 600/601/603/605/000/001/002/003 (沪深主板+中小板)
    排除: 创业板 300/301, 科创板 688/689, 北证 8/4, 指数 999
    注: 原算法把创业板归为双创 (valid_patterns 不含强势横盘), 此处也排除.
    """
    if not ts_code or "." not in ts_code:
        return False
    sym = ts_code.split(".")[0]
    if sym.startswith(("999", "8", "4")):
        return False
    if sym.startswith(("3", "688", "689")):
        return False
    # 沪深主板+中小板
    if sym.startswith(("60", "00")):
        return True
    return False


# =========================================================
# 信号判断: 单日切片上判定是否触发强势横盘买入
# =========================================================
def detect_strong_sideways_signal(df_slice: pd.DataFrame) -> Tuple[bool, Dict]:
    """对截止到最后一日的 K 线切片判断是否触发强势横盘信号

    Args:
        df_slice: 含 MA5/10/20/60, DIF/DEA/MACD, RSI6 列的 DataFrame, 最后一日为当前日

    Returns:
        (是否触发, 信号详情 dict)
    """
    n = len(df_slice)
    if n < 80:
        return False, {}

    last = df_slice.iloc[-1]
    current_close = last["close"]

    # === 找最近一波 SURGE_MIN=20% 拉升 ===
    # 从倒数第 ADJUST_MAX 天开始向前找
    for end_idx in range(n - 1, max(SURGE_DAYS, n - ADJUST_MAX), -1):
        window_start = end_idx - SURGE_DAYS
        if window_start < 0:
            break

        window = df_slice.iloc[window_start: end_idx + 1]
        window_closes = window["close"].values
        low_idx_in_window = int(np.argmin(window_closes))
        high_idx_in_window = int(np.argmax(window_closes))

        # 低点必须在高点之前
        if high_idx_in_window <= low_idx_in_window:
            continue
        if (high_idx_in_window - low_idx_in_window) > SURGE_DAYS - 2:
            continue

        wave1_gain = (window_closes[high_idx_in_window]
                      - window_closes[low_idx_in_window]) / window_closes[low_idx_in_window]
        if wave1_gain < SURGE_MIN:
            continue

        # wave1 高点在 df_slice 中的绝对索引
        wave1_high_idx = window_start + high_idx_in_window
        wave1_high_price = df_slice.iloc[wave1_high_idx]["close"]

        # wave1 高点之后至今
        post_df = df_slice.iloc[wave1_high_idx:]
        if len(post_df) < 2:
            continue

        post_closes = post_df["close"].values
        post_high = post_closes[0]   # wave1 高点
        post_low = post_closes.min()
        pullback_max = (post_high - post_low) / post_high if post_high > 0 else 0
        pullback_days = len(post_df) - 1

        # === 强势横盘判定 ===
        if not (pullback_max < PULLBACK_MAX and pullback_days <= PULLBACK_DAYS_MAX):
            continue

        # 最小回调过滤
        pullback_now = (post_high - current_close) / post_high
        if pullback_now < PULLBACK_MIN:
            continue

        # === 形态匹配成功, 检查触发条件 ===
        # 基准量: wave1 高点前 20 日均量
        if wave1_high_idx >= 20:
            base_vol = df_slice.iloc[wave1_high_idx - 20: wave1_high_idx]["vol"].mean()
        else:
            base_vol = post_df["vol"].mean()
        # 近 5 日均量
        recent_vol_5d = post_df["vol"].iloc[-5:].mean() if len(post_df) >= 5 else post_df["vol"].mean()
        vol_ratio = recent_vol_5d / base_vol if base_vol and base_vol > 0 else 1.0

        rsi_now = last.get("RSI6", np.nan)
        rsi_now = rsi_now if not pd.isna(rsi_now) else 50.0

        macd_dif = last.get("DIF", np.nan)
        macd_dea = last.get("DEA", np.nan)
        macd_crossed = (macd_dif > macd_dea) if (not pd.isna(macd_dif) and not pd.isna(macd_dea)) else False

        ma20 = last.get("MA20", np.nan)
        above_ma20 = (current_close > ma20) if not pd.isna(ma20) else False

        # 触发条件 P1: RSI<50 + 缩量(<0.8x)
        trigger_p1 = (rsi_now < RSI_MAX) and (vol_ratio < VOL_SHRINK_RATIO)
        # 触发条件 P2: MACD金叉 + MA20上方
        trigger_p2 = macd_crossed and above_ma20

        if trigger_p1 or trigger_p2:
            return True, {
                "wave1_gain_pct": round(wave1_gain * 100, 1),
                "pullback_max_pct": round(pullback_max * 100, 1),
                "pullback_now_pct": round(pullback_now * 100, 1),
                "pullback_days": pullback_days,
                "rsi_now": round(rsi_now, 1),
                "vol_ratio": round(vol_ratio, 2),
                "macd_crossed": macd_crossed,
                "above_ma20": above_ma20,
                "trigger": "P1_RSI_缩量" if trigger_p1 else "P2_MACD_MA20",
                "wave1_high": round(wave1_high_price, 2),
                "current_price": round(current_close, 2),
            }
        # 当前窗口已匹配形态但不触发, 继续找更早的窗口

    return False, {}


# =========================================================
# 回测引擎
# =========================================================
class Wave2SidewaysBacktester:
    """Wave2 强势横盘回测

    流程:
      1. 加载全部主板股票 K 线 + 预计算指标 (MA/RSI/MACD)
      2. 遍历回测区间每个交易日 T
      3. 对每只股票取截止 T 日的 K 线切片, 调用 detect_strong_sideways_signal
      4. 模拟 T+1 开盘买入, T+1+N 收盘卖出
      5. 统计胜率/盈亏比/平均收益
    """

    def __init__(self,
                 start_date: str = "20250101",
                 end_date: str = None,
                 max_stocks: Optional[int] = None,
                 lookback_days: int = 200):
        from datetime import datetime
        self.start_date = start_date
        self.end_date = end_date or datetime.now().strftime("%Y%m%d")
        self.lookback_days = lookback_days

        # 预加载
        self.kline_dict: Dict[str, pd.DataFrame] = {}
        self._date_idx_map: Dict[str, Dict[str, int]] = {}
        self._load_all_klines(max_stocks)

        # 回测交易日
        all_dates = set()
        for df in self.kline_dict.values():
            all_dates.update(df["trade_date"].tolist())
        self.trade_dates = sorted([d for d in all_dates
                                   if self.start_date <= d <= self.end_date])
        print(f"[Backtest] 区间: {self.start_date} ~ {self.end_date}, "
              f"交易日: {len(self.trade_dates)}", flush=True)

    def _load_all_klines(self, max_stocks: Optional[int]):
        from datetime import datetime, timedelta
        dt = datetime.strptime(self.start_date, "%Y%m%d")
        load_start = (dt - timedelta(days=self.lookback_days)).strftime("%Y%m%d")

        t0 = time.time()
        n_ok, n_skip = 0, 0
        for path in iter_all_day_files(markets=("SH", "SZ")):
            ts_code = tdx_filename_to_ts_code(path)
            if not ts_code:
                continue
            # 只保留主板
            if not is_main_board(ts_code):
                continue
            if max_stocks and n_ok >= max_stocks:
                break
            df = load_kline(ts_code, start_date=load_start, end_date=self.end_date)
            if df.empty or len(df) < 80:
                n_skip += 1
                continue
            try:
                # 仅预计算必需指标 (跳过 KDJ/BOLL/OBV)
                df = _precompute_minimal_indicators(df)
            except Exception:
                n_skip += 1
                continue
            # 涨停阈值 (T+1 买入时检查) - 主板 9.8% 即涨停
            df["_zt_up"] = 1.098
            self.kline_dict[ts_code] = df
            self._date_idx_map[ts_code] = dict(zip(df["trade_date"], df.index))
            n_ok += 1

            # 每 500 只打印一次进度
            if n_ok % 500 == 0:
                elapsed = time.time() - t0
                print(f"  [Loading] 已加载 {n_ok} 只, 耗时 {elapsed:.1f}s", flush=True)

        elapsed = time.time() - t0
        print(f"[Load] 主板股票加载 {n_ok} 只, 跳过 {n_skip}, 耗时 {elapsed:.1f}s", flush=True)

    def run_single_day(self, trade_date: str) -> List[Tuple[str, Dict]]:
        """单日选股: 遍历所有股票判断是否触发强势横盘信号"""
        selected = []
        for ts_code, df in self.kline_dict.items():
            idx_map = self._date_idx_map[ts_code]
            i = idx_map.get(trade_date)
            if i is None or i < 80:
                continue
            df_slice = df.iloc[: i + 1]
            try:
                triggered, info = detect_strong_sideways_signal(df_slice)
            except Exception:
                continue
            if triggered:
                selected.append((ts_code, info))
        return selected

    def evaluate_signals(self, selected: List[Tuple[str, Dict]],
                         trade_date: str, hold_days: int = 5) -> List[Dict]:
        """T+1 开盘买入, T+1+N 收盘卖出"""
        records = []
        for ts_code, info in selected:
            df = self.kline_dict.get(ts_code)
            if df is None:
                continue
            idx_map = self._date_idx_map[ts_code]
            i = idx_map.get(trade_date)
            if i is None:
                continue

            # T+1 买入
            buy_idx = i + 1
            if buy_idx >= len(df):
                continue
            buy_row = df.iloc[buy_idx]
            prev_close = df.iloc[i]["close"]
            zt_up = buy_row["_zt_up"]
            # T+1 一字涨停或开盘即涨停 -> 无法买入
            if buy_row["open"] >= prev_close * zt_up * 0.999:
                continue

            buy_price = buy_row["open"]
            buy_date = buy_row["trade_date"]

            # T+1+N 卖出 (收盘价)
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
                "trigger": info.get("trigger", ""),
                "wave1_gain_pct": info.get("wave1_gain_pct", 0),
                "pullback_pct": info.get("pullback_now_pct", 0),
                "pullback_days": info.get("pullback_days", 0),
                "rsi_now": info.get("rsi_now", 0),
                "vol_ratio": info.get("vol_ratio", 0),
            })
        return records

    def run_backtest(self, hold_days: int = 5,
                     top_n: Optional[int] = None,
                     verbose: bool = True) -> Dict:
        """完整回测: 遍历所有交易日"""
        daily_counts = []
        all_returns = []
        trade_records = []

        t0 = time.time()
        for i, td in enumerate(self.trade_dates):
            selected = self.run_single_day(td)

            # 每日最多取 top_n (按回调深度排序, 越深越优先)
            if top_n and len(selected) > top_n:
                selected.sort(key=lambda x: -x[1].get("pullback_now_pct", 0))
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

        # 统计
        all_returns_arr = np.array(all_returns) if all_returns else np.array([0])
        win_rate = (all_returns_arr > 0).mean() * 100 if all_returns else 0
        avg_ret = all_returns_arr.mean() if all_returns else 0
        med_ret = np.median(all_returns_arr) if all_returns else 0

        daily_counts_arr = np.array(daily_counts)
        n_days_1_5 = int(((daily_counts_arr >= 1) & (daily_counts_arr <= 5)).sum())

        return {
            "daily_counts": daily_counts,
            "all_returns": all_returns,
            "trade_records": trade_records,
            "win_rate": round(win_rate, 1),
            "avg_return": round(avg_ret, 2),
            "median_return": round(med_ret, 2),
            "n_signals": len(all_returns),
            "n_days_1_5": n_days_1_5,
            "n_total_days": len(self.trade_dates),
        }


# =========================================================
# 主入口
# =========================================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Wave2 强势横盘算法回测")
    parser.add_argument("--start", type=str, default="20250101", help="回测起始日")
    parser.add_argument("--end", type=str, default=None, help="回测结束日")
    parser.add_argument("--max-stocks", type=int, default=None,
                        help="最多加载多少只主板股票 (调试用)")
    parser.add_argument("--hold", type=int, default=5, help="持有天数")
    parser.add_argument("--top-n", type=int, default=None,
                        help="每日最多选 N 只 (按回调深度排序), 默认不限")
    parser.add_argument("--out", type=str, default=None,
                        help="交易记录 CSV 输出路径")
    args = parser.parse_args()

    print("=" * 80)
    print("  Wave2 强势横盘算法回测 (主板, T+1 开盘买入)")
    print("=" * 80)
    print(f"  算法参数:")
    print(f"    一波拉升窗口: {SURGE_DAYS} 天, 最低涨幅: {SURGE_MIN*100:.0f}%")
    print(f"    强势横盘: 回调 <{PULLBACK_MAX*100:.0f}%, 调整天数 <={PULLBACK_DAYS_MAX}")
    print(f"    最小回调: {PULLBACK_MIN*100:.0f}%, 调整期上限: {ADJUST_MAX} 天")
    print(f"    触发 P1: RSI6<{RSI_MAX} + 缩量(<{VOL_SHRINK_RATIO}x)")
    print(f"    触发 P2: MACD 金叉 + MA20 上方")
    print("=" * 80, flush=True)

    bt = Wave2SidewaysBacktester(
        start_date=args.start,
        end_date=args.end,
        max_stocks=args.max_stocks,
    )

    res = bt.run_backtest(hold_days=args.hold, top_n=args.top_n, verbose=True)

    print("\n" + "=" * 70)
    print("  回测结果 (T+1 开盘买入)")
    print("=" * 70)
    print(f"  回测区间:     {args.start} ~ {args.end or '最新'}")
    print(f"  交易日数:     {res['n_total_days']}")
    print(f"  持有天数:     {args.hold}")
    print(f"  总信号数:     {res['n_signals']}")
    print(f"  胜率:         {res['win_rate']}%")
    print(f"  平均收益:     {res['avg_return']}%")
    print(f"  中位收益:     {res['median_return']}%")
    if res['n_signals'] > 0:
        rets = np.array(res['all_returns'])
        print(f"  最大盈利:     {rets.max():.2f}%")
        print(f"  最大亏损:     {rets.min():.2f}%")
        pos = rets[rets > 0]
        neg = rets[rets < 0]
        if len(neg) > 0 and len(pos) > 0:
            print(f"  盈亏比:       {abs(pos.mean() / neg.mean()):.2f}")
        print(f"  日均选股数:   {np.mean(res['daily_counts']):.1f}")
        print(f"  选股1-5只天数: {res['n_days_1_5']}/{res['n_total_days']} "
              f"({res['n_days_1_5']/res['n_total_days']*100:.1f}%)")

    # 触发条件对比
    if res.get("trade_records"):
        print("\n  触发条件胜率对比:")
        recs = res["trade_records"]
        for trig in ["P1_RSI_缩量", "P2_MACD_MA20"]:
            sub = [r["return"] for r in recs if r["trigger"] == trig]
            if sub:
                wr = sum(1 for x in sub if x > 0) / len(sub) * 100
                avg = np.mean(sub)
                print(f"    {trig}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:.2f}%")

        # 回调深度分档对比
        print("\n  回调深度分档胜率:")
        for lo, hi in [(5, 7), (7, 9), (9, 10)]:
            sub = [r["return"] for r in recs
                   if lo <= r["pullback_pct"] < hi]
            if sub:
                wr = sum(1 for x in sub if x > 0) / len(sub) * 100
                avg = np.mean(sub)
                print(f"    回调{lo}-{hi}%: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:.2f}%")

        # 输出交易记录
        out_path = args.out or os.path.join(
            os.path.dirname(__file__), "wave2_sideways_trades.csv")
        pd.DataFrame(res["trade_records"]).to_csv(
            out_path, index=False, encoding="utf-8-sig")
        print(f"\n  [交易记录已保存] {out_path}")


if __name__ == "__main__":
    main()
