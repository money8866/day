"""
趋势精准入场策略 — 通达信数据回测版本

策略来源: D:\mystock\solo\trend_entry_precision.py
适配框架: tdx_backtest (data_loader + indicators + backtest)

策略逻辑:
  1. MA20走平或上拐（5日趋势 > -2%）
  2. 突破MA60或前60日高点（>1%）
  3. MACD条件（DIF>-1 且 (接近0轴 或 DIF>DEA)）
  4. 涨幅/阳线条件（涨幅>=2% 或 (涨幅>=0.5% 且收阳 且突破前高)）
  5. 距MA20在5%~20%之间（最优12%~16%）
  6. 首次放量确认（当日量 > 前20日最高量×0.7）
  7. 距MA60不能过远（<30%）
  8. 信号日前回溯的第一个最高点必须是前120天的最高点
  9. 数据驱动评分体系（MA20位置+量比+MA60位置+MACD+涨幅+连涨天数惩罚）

交易规则:
  - T+1 开盘买入
  - 持有 N 天收盘卖出
"""
from __future__ import annotations
import os
import sys
import time
from typing import Dict, List, Tuple, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_loader import load_kline, iter_all_day_files, tdx_filename_to_ts_code
from indicators import add_indicators


# =========================================================
# 策略参数（与 trend_entry_precision.py 保持一致）
# =========================================================
MA20_TREND_MIN = -2.0        # MA20 5日趋势最低值
ABOVE_MA20_MIN = 5.0         # 距MA20最低百分比
ABOVE_MA20_MAX = 20.0        # 距MA20最高百分比
ABOVE_MA20_OPTIMAL_LOW = 12.0   # 距MA20最优区间下限
ABOVE_MA20_OPTIMAL_HIGH = 16.0  # 距MA20最优区间上限
VOL_RATIO_MIN = 0.7          # 量比最低值（当日量/前20日最高量）
ABOVE_MA60_MAX = 30.0        # 距MA60最高百分比
PEAK_WINDOW = 30              # 波段高点检测窗口
PEAK_LOOKBACK = 120          # 波段高点回溯天数
ENTRY_SCORE_MIN = 80  # 优化：提高到80分，只使用高质量信号         # 最低入场评分


# =========================================================
# 信号检测函数
# =========================================================
def detect_trend_entry_signal(df_slice: pd.DataFrame) -> Tuple[bool, Dict]:
    """
    检测趋势精准入场信号
    
    Args:
        df_slice: 截止到信号日的K线切片（含技术指标）
        
    Returns:
        (是否触发信号, 信号详情)
    """
    n = len(df_slice)
    if n < 120:  # 需要足够长的历史数据
        return False, {}
    
    last = df_slice.iloc[-1]
    close = last["close"]
    ma20 = last.get("MA20", 0)
    ma60 = last.get("MA60", 0)
    
    if ma20 <= 0 or ma60 <= 0:
        return False, {}
    
    # ──────────────────────────────────────
    # 条件A: 基础趋势条件
    # ──────────────────────────────────────
    
    # A1: MA20走平或上拐
    ma20_5_ago = df_slice.iloc[-6]["MA20"] if n >= 6 else 0
    if ma20_5_ago <= 0:
        return False, {}
    ma20_trend = (ma20 / ma20_5_ago - 1) * 100
    if ma20_trend < MA20_TREND_MIN:
        return False, {}
    
    # A2: 突破MA60或前60日高点
    seg_high = df_slice.iloc[max(0, n - 60):n]["high"].max()
    break_ma60 = close > ma60 * 1.01
    break_60d_high = close > seg_high * 1.01
    if not (break_ma60 or break_60d_high):
        return False, {}
    
    # A3: MACD条件
    dif = last.get("DIF", 0)
    dea = last.get("DEA", 0)
    macd_near_zero = abs(dif) < 1.0
    golden_cross = dif > dea
    if not (macd_near_zero or (golden_cross and dif > -0.5)):
        return False, {}
    
    # A4: 涨幅/阳线条件
    pct_chg = last.get("pct_chg", 0)
    close_above_open = close > last["open"]
    if not (pct_chg >= 2.0 or (pct_chg >= 0.5 and close_above_open and break_60d_high)):
        return False, {}
    
    # ──────────────────────────────────────
    # 条件B: 进场精确定位
    # ──────────────────────────────────────
    
    # B1: 距MA20在合理范围
    above_ma20 = (close / ma20 - 1) * 100
    if above_ma20 < ABOVE_MA20_MIN or above_ma20 > ABOVE_MA20_MAX:
        return False, {}
    
    # B2: 首次放量确认
    vol_20d = df_slice.iloc[max(0, n - 21):n]["vol"]
    if len(vol_20d) < 10:
        return False, {}
    max_vol_20d = vol_20d.max()
    current_vol = last["vol"]
    if max_vol_20d <= 0:
        return False, {}
    vol_ratio_vs_max = current_vol / max_vol_20d
    if vol_ratio_vs_max < VOL_RATIO_MIN:
        return False, {}
    
    # B3: 距MA60不能过远
    above_ma60 = (close / ma60 - 1) * 100
    if above_ma60 > ABOVE_MA60_MAX:
        return False, {}
    
    # B5: 信号日前回溯的第一个最高点必须是前120天的最高点
    peak_idx = None
    for k in range(n - 2, max(PEAK_WINDOW, n - PEAK_LOOKBACK), -1):
        window_start = max(0, k - PEAK_WINDOW)
        window_end = min(n, k + PEAK_WINDOW + 1)
        window_highs = df_slice.iloc[window_start:window_end]["high"]
        if df_slice.iloc[k]["high"] >= window_highs.max():
            peak_idx = k
            break
    if peak_idx is None:
        return False, {}
    lookback_start = max(0, peak_idx - PEAK_LOOKBACK)
    lookback_high = df_slice.iloc[lookback_start:peak_idx]["high"].max()
    if df_slice.iloc[peak_idx]["high"] < lookback_high:
        return False, {}
    
    # ──────────────────────────────────────
    # 数据驱动评分体系
    # ──────────────────────────────────────
    entry_score = 0
    
    # 1. 距MA20位置（+30分）
    if ABOVE_MA20_OPTIMAL_LOW <= above_ma20 <= ABOVE_MA20_OPTIMAL_HIGH:
        entry_score += 30
    elif 16 < above_ma20 <= 20:
        entry_score += 25
    elif 8 <= above_ma20 < 12:
        entry_score += 20
    elif 5 <= above_ma20 < 8:
        entry_score += 10
    
    # 2. 量比（+25分）
    if 1.2 <= vol_ratio_vs_max <= 1.5:
        entry_score += 25
    elif 1.5 < vol_ratio_vs_max <= 2.0:
        entry_score += 18
    elif 2.0 < vol_ratio_vs_max <= 3.0:
        entry_score += 8
    elif 1.0 <= vol_ratio_vs_max < 1.2:
        entry_score += 15
    elif vol_ratio_vs_max > 3.0:
        entry_score -= 10
    else:
        entry_score -= 5
    
    # 3. 距MA60位置（+20分）
    if above_ma60 >= 20:
        entry_score += 20
    elif 15 <= above_ma60 < 20:
        entry_score += 15
    elif 10 <= above_ma60 < 15:
        entry_score += 10
    elif 5 <= above_ma60 < 10:
        entry_score += 5
    
    # 4. MACD DIF（+15分）
    if dif >= 2:
        entry_score += 15
    elif 1 <= dif < 2:
        entry_score += 10
    elif 0 <= dif < 1:
        entry_score += 5
    
    # 5. 涨幅（+10分）
    if 7 <= pct_chg <= 10:
        entry_score += 10
    elif 10 < pct_chg:
        entry_score += 5
    elif 5 <= pct_chg < 7:
        entry_score += 5
    
    # 连涨天数惩罚
    consecutive_up = 1
    for i in range(n - 2, max(0, n - 11), -1):
        if df_slice.iloc[i].get("pct_chg", 0) > 0:
            consecutive_up += 1
        else:
            break
    
    if consecutive_up == 1:
        entry_score += 5
    elif consecutive_up == 2:
        entry_score += 0
    elif consecutive_up == 3:
        entry_score -= 10
    else:
        entry_score -= 20
    
    # 评分过滤
    if entry_score < ENTRY_SCORE_MIN:
        return False, {}
    
    return True, {
        "entry_score": entry_score,
        "consecutive_up": consecutive_up,
        "pct_chg": round(pct_chg, 2),
        "vol_ratio": round(vol_ratio_vs_max, 2),
        "above_ma20_pct": round(above_ma20, 2),
        "above_ma60_pct": round(above_ma60, 2),
        "ma20_trend": round(ma20_trend, 2),
        "macd_dif": round(dif, 2),
        "macd_golden": 1 if dif > dea else 0,
        "rsi6": round(last.get("RSI6", 0), 1),
        "signal_type": "趋势启动",
    }


# =========================================================
# 回测引擎
# =========================================================
class TrendEntryBacktester:
    """趋势精准入场回测"""
    
    def __init__(self,
                 start_date: str = "20250101",
                 end_date: str = None,
                 max_stocks: Optional[int] = None,
                 lookback_days: int = 250):
        from datetime import datetime, timedelta
        self.start_date = start_date
        self.end_date = end_date or datetime.now().strftime("%Y%m%d")
        self.lookback_days = lookback_days
        
        # 预加载K线数据
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
              f"交易日: {len(self.trade_dates)}")
    
    def _load_all_klines(self, max_stocks: Optional[int]):
        """加载全部股票K线（主板+双创）"""
        from datetime import datetime, timedelta
        dt = datetime.strptime(self.start_date, "%Y%m%d")
        load_start = (dt - timedelta(days=self.lookback_days)).strftime("%Y%m%d")
        
        t0 = time.time()
        n_ok, n_skip = 0, 0
        for path in iter_all_day_files(markets=("SH", "SZ")):
            ts_code = tdx_filename_to_ts_code(path)
            if not ts_code:
                continue
            # 排除指数
            if ts_code.startswith(("999", "8", "4")):
                continue
            if max_stocks and n_ok >= max_stocks:
                break
            df = load_kline(ts_code, start_date=load_start, end_date=self.end_date)
            if df.empty or len(df) < 120:
                n_skip += 1
                continue
            try:
                df = add_indicators(df)
            except Exception:
                n_skip += 1
                continue
            self.kline_dict[ts_code] = df
            self._date_idx_map[ts_code] = dict(zip(df["trade_date"], df.index))
            n_ok += 1
        
        elapsed = time.time() - t0
        print(f"[Load] 加载 {n_ok} 只股票, 跳过 {n_skip}, 耗时 {elapsed:.1f}s")
    
    def run_single_day(self, trade_date: str) -> List[Tuple[str, Dict]]:
        """单日选股"""
        selected = []
        for ts_code, df in self.kline_dict.items():
            idx_map = self._date_idx_map[ts_code]
            i = idx_map.get(trade_date)
            if i is None or i < 120:
                continue
            df_slice = df.iloc[: i + 1]
            try:
                triggered, info = detect_trend_entry_signal(df_slice)
            except Exception:
                continue
            if triggered:
                selected.append((ts_code, info))
        return selected
    
    def evaluate_signals(self, selected: List[Tuple[str, Dict]],
                         trade_date: str, hold_days: int = 5) -> List[Dict]:
        """T+1开盘买入, T+1+N收盘卖出"""
        records = []
        for ts_code, info in selected:
            df = self.kline_dict.get(ts_code)
            if df is None:
                continue
            idx_map = self._date_idx_map[ts_code]
            i = idx_map.get(trade_date)
            if i is None:
                continue
            
            # T+1买入
            buy_idx = i + 1
            if buy_idx >= len(df):
                continue
            buy_row = df.iloc[buy_idx]
            buy_price = buy_row["open"]
            buy_date = buy_row["trade_date"]
            
            # T+1+N卖出
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
                "entry_score": info.get("entry_score", 0),
                "consecutive_up": info.get("consecutive_up", 0),
                "pct_chg": info.get("pct_chg", 0),
                "vol_ratio": info.get("vol_ratio", 0),
                "above_ma20_pct": info.get("above_ma20_pct", 0),
                "above_ma60_pct": info.get("above_ma60_pct", 0),
                "macd_dif": info.get("macd_dif", 0),
                "rsi6": info.get("rsi6", 0),
            })
        return records
    
    def run_backtest(self, hold_days: int = 5,
                     min_score: int = 50,
                     verbose: bool = True) -> Dict:
        """完整回测"""
        daily_counts = []
        all_returns = []
        trade_records = []
        
        t0 = time.time()
        for i, td in enumerate(self.trade_dates):
            selected = self.run_single_day(td)
            
            # 评分过滤
            if min_score:
                selected = [(c, info) for c, info in selected
                           if info.get("entry_score", 0) >= min_score]
            
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
                      f"累计 {len(all_returns)} 笔, 耗时 {elapsed:.1f}s, ETA {eta:.0f}s")
        
        # 统计
        all_returns_arr = np.array(all_returns) if all_returns else np.array([0])
        win_rate = (all_returns_arr > 0).mean() * 100 if all_returns else 0
        avg_ret = all_returns_arr.mean() if all_returns else 0
        med_ret = np.median(all_returns_arr) if all_returns else 0
        
        return {
            "daily_counts": daily_counts,
            "all_returns": all_returns,
            "trade_records": trade_records,
            "win_rate": round(win_rate, 1),
            "avg_return": round(avg_ret, 2),
            "median_return": round(med_ret, 2),
            "n_signals": len(all_returns),
            "n_total_days": len(self.trade_dates),
        }


# =========================================================
# 主入口
# =========================================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description="趋势精准入场策略回测")
    parser.add_argument("--start", type=str, default="20250101", help="回测起始日")
    parser.add_argument("--end", type=str, default=None, help="回测结束日")
    parser.add_argument("--max-stocks", type=int, default=None, help="最多加载多少只股票")
    parser.add_argument("--hold", type=int, default=5, help="持有天数")
    parser.add_argument("--min-score", type=int, default=50, help="最低入场评分")
    parser.add_argument("--out", type=str, default=None, help="交易记录CSV输出路径")
    args = parser.parse_args()
    
    print("=" * 80)
    print("  趋势精准入场策略回测 (T+1 开盘买入)")
    print("=" * 80)
    print(f"  策略参数:")
    print(f"    MA20趋势最低: {MA20_TREND_MIN}%")
    print(f"    距MA20范围: {ABOVE_MA20_MIN}% ~ {ABOVE_MA20_MAX}%")
    print(f"    最优距MA20: {ABOVE_MA20_OPTIMAL_LOW}% ~ {ABOVE_MA20_OPTIMAL_HIGH}%")
    print(f"    量比最低: {VOL_RATIO_MIN}")
    print(f"    距MA60最高: {ABOVE_MA60_MAX}%")
    print(f"    最低评分: {args.min_score}")
    print("=" * 80)
    
    bt = TrendEntryBacktester(
        start_date=args.start,
        end_date=args.end,
        max_stocks=args.max_stocks,
    )
    
    res = bt.run_backtest(hold_days=args.hold, min_score=args.min_score, verbose=True)
    
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
    
    # 评分分档
    if res.get("trade_records"):
        print("\n  入场评分分档胜率:")
        for lo, hi in [(80, 100), (70, 80), (60, 70), (50, 60)]:
            sub = [r["return"] for r in res["trade_records"]
                   if lo <= r["entry_score"] < hi]
            if sub:
                wr = sum(1 for x in sub if x > 0) / len(sub) * 100
                avg = np.mean(sub)
                print(f"    评分{lo}-{hi}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:.2f}%")
        
        # 输出交易记录
        out_path = args.out or os.path.join(
            os.path.dirname(__file__), "trend_entry_trades.csv")
        pd.DataFrame(res["trade_records"]).to_csv(
            out_path, index=False, encoding="utf-8-sig")
        print(f"\n  [交易记录已保存] {out_path}")


if __name__ == "__main__":
    main()
