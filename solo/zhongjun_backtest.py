# -*- coding: utf-8 -*-
"""
中军企稳·回调买点策略 TDX 回测框架 (向量化加速)

策略来源: d:/mystock/solo/watchlist_buy_signal.py -> calc_buy_signal
数据源:   通达信本地 .day 文件

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
sys.path.insert(0, r"d:\mystock\solo")
from data_loader import load_kline, iter_all_day_files, tdx_filename_to_ts_code
from watchlist_buy_signal import is_shuangchuang, get_board_params

# =========================================================
# 策略常量
# =========================================================
MIN_SCORE = 65          # BUY信号最低评分 (优化v2: 60→65)
STRONG_BUY_SCORE = 80   # 强买信号评分阈值
STRONG_BUY_DAYS_MAX = 4 # 强买信号回调天数上限
INCLUDE_KECHUANG = True
EXCLUDE_KECHUANG = False  # 先不限制，回测分析后再决定


# =========================================================
# 板块判定
# =========================================================
def is_tradeable(ts_code: str) -> bool:
    if not ts_code or "." not in ts_code:
        return False
    sym = ts_code.split(".")[0]
    if sym.startswith(("999", "8", "4")):
        return False
    if EXCLUDE_KECHUANG and sym.startswith(("688", "689")):
        return False
    return sym.startswith(("60", "00", "3", "688", "689"))


# =========================================================
# 预计算所有技术指标
# =========================================================
def precompute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """预计算所有技术指标，添加为列"""
    df = df.copy()
    n = len(df)
    close = df['close']
    high = df['high']
    low = df['low']
    vol = df['vol']
    pct_chg = df['pct_chg'] if 'pct_chg' in df.columns else close.pct_change() * 100

    # 均线
    df['ma5'] = close.rolling(5).mean()
    df['ma10'] = close.rolling(10).mean()
    df['ma20'] = close.rolling(20).mean()
    df['ma60'] = close.rolling(60).mean()

    # 量均线
    df['vol5'] = vol.rolling(5).mean()
    df['vol20'] = vol.rolling(20).mean()
    df['vol_ratio'] = df['vol5'] / (df['vol20'] + 0.0001)

    # KDJ
    low9 = low.rolling(9).min()
    high9 = high.rolling(9).max()
    rsv = (close - low9) / (high9 - low9 + 0.0001) * 100
    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    df['kdj_j'] = 3 * k - 2 * d

    # RSI (14日)
    delta = pct_chg
    gain = delta.where(delta > 0, 0)
    loss = (-delta).where(delta < 0, 0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / (avg_loss + 0.0001)
    df['rsi'] = 100 - (100 / (1 + rs))

    # ATR
    df['atr'] = (high - low).rolling(14).mean()

    # 近20日高点位置 (向量化)
    # 对于每一天i，找close[i-19:i+1]中最大值的位置
    # 使用rolling(20).apply(np.argmax)得到窗口内位置(0-19)
    df['_rolling_argmax'] = close.rolling(20).apply(np.argmax, raw=True)
    # days_from_high = 19 - rolling_argmax (距今天数)
    df['days_from_high'] = 19 - df['_rolling_argmax']
    # recent_high = rolling max
    df['recent_high'] = close.rolling(20).max()
    df['pullback_pct'] = (close / df['recent_high'] - 1) * 100

    # 位置
    df['pos_ma5'] = (close / df['ma5'] - 1) * 100
    df['pos_ma10'] = (close / df['ma10'] - 1) * 100
    df['pos_ma20'] = (close / df['ma20'] - 1) * 100
    df['pos_ma60'] = (close / df['ma60'] - 1) * 100

    # MA60斜率 (10日)
    df['ma60_slope'] = (df['ma60'] / df['ma60'].shift(10) - 1) * 100

    # 趋势条件
    df['trend_ok'] = (df['ma20'] > df['ma60']).fillna(False)

    # 较高点缩量
    # 高点当天的成交量
    df['high_vol'] = vol.rolling(20).apply(lambda x: x[np.argmax(x)], raw=True)
    df['shrink_from_high'] = vol / (df['high_vol'] + 0.0001)

    # K线形态
    df['body'] = (close - df['open']).abs()
    df['lower_shadow'] = pd.concat([close, df['open']], axis=1).min(axis=1) - low

    # 近2日不创新低
    df['no_new_low'] = (low >= low.shift(1)) & (low.shift(1) >= low.shift(2))

    return df


# =========================================================
# 向量化信号检测 (单只股票，全日期)
# =========================================================
def detect_zhongjun_signals_vec(df: pd.DataFrame, ts_code: str) -> Tuple[np.ndarray, List[Dict]]:
    """检测中军企稳回调买点信号，返回 (signal_array, info_list)"""
    n = len(df)
    if n < 60:
        return np.zeros(n, dtype=bool), []

    signals = np.zeros(n, dtype=bool)
    infos = []

    params = get_board_params(ts_code)
    is_sc = is_shuangchuang(ts_code)

    # 获取所有预计算列
    close = df['close'].values
    open_ = df['open'].values
    high = df['high'].values
    low = df['low'].values
    vol = df['vol'].values
    pct_chg = df['pct_chg'].values if 'pct_chg' in df.columns else np.zeros(n)

    ma5 = df['ma5'].values
    ma10 = df['ma10'].values
    ma20 = df['ma20'].values
    ma60 = df['ma60'].values

    vol_ratio = df['vol_ratio'].values
    kdj_j = df['kdj_j'].values
    rsi = df['rsi'].values
    atr = df['atr'].values

    days_from_high = df['days_from_high'].values
    pullback_pct = df['pullback_pct'].values
    pos_ma5 = df['pos_ma5'].values
    pos_ma10 = df['pos_ma10'].values
    pos_ma20 = df['pos_ma20'].values
    pos_ma60 = df['pos_ma60'].values
    ma60_slope = df['ma60_slope'].values
    trend_ok = df['trend_ok'].values
    shrink_from_high = df['shrink_from_high'].values
    body = df['body'].values
    lower_shadow = df['lower_shadow'].values
    no_new_low = df['no_new_low'].values
    trade_dates = df['trade_date'].values

    # 涨停板
    sym = ts_code.split(".")[0]
    zt_up = 1.198 if sym.startswith(("3", "688", "689")) else 1.098
    df_zt = zt_up

    # 从第60天开始遍历 (需要MA60)
    for i in range(59, n):
        # 跳过NaN
        if np.isnan(ma60[i]) or np.isnan(kdj_j[i]) or np.isnan(rsi[i]):
            continue

        # 条件0: 趋势未破坏（必须）
        if not trend_ok[i]:
            continue

        # 优化v2硬过滤: 回调必须≥10% (深回调50.5% vs 浅回调46.0%)
        pb = pullback_pct[i]
        if pb > -10:
            continue

        # 优化v2硬过滤: 量比必须≥1.0 (量比高胜率反而高)
        vr = vol_ratio[i] if not np.isnan(vol_ratio[i]) else 1.0
        if vr < 1.0:
            continue

        score = 0
        resonance = 0

        # MA60向上
        if not np.isnan(ma60_slope[i]) and ma60_slope[i] > 0.5:
            score += 10

        # 回调幅度 (优化v2: 深回调加分)
        if pb <= -15:
            score += 25
            resonance += 1
        elif pb <= -10:
            score += 20
            resonance += 1

        # 回调至MA10
        p10 = pos_ma10[i] if not np.isnan(pos_ma10[i]) else 0
        if -params['ma10_tolerance_down'] <= p10 <= params['ma10_tolerance']:
            score += 20
            resonance += 1
        elif -8 <= p10 < -params['ma10_tolerance_down']:
            score += 8

        # 回调至MA20
        p20 = pos_ma20[i] if not np.isnan(pos_ma20[i]) else 0
        if -3 <= p20 <= 3:
            score += 15
            resonance += 1
        elif -5 <= p20 < -3:
            score += 8

        # 优化v2: 量比放大加分 (替代缩量逻辑)
        if vr >= 1.3:
            score += 15
            resonance += 1
        elif vr >= 1.0:
            score += 10

        # 较高点缩量 (保留，有参考价值)
        sfh = shrink_from_high[i] if not np.isnan(shrink_from_high[i]) else 1.0
        if sfh < 0.5:
            score += 5

        # 优化v2: 删除KDJ加分 (数据证明无效)
        cj = kdj_j[i] if not np.isnan(kdj_j[i]) else 50

        # 长下影线
        ls = lower_shadow[i]
        bd = body[i]
        at = atr[i] if not np.isnan(atr[i]) else 0
        if ls > bd * 1.5 and ls > at * 0.5:
            score += 8
            resonance += 1

        # 近2日不创新低
        if no_new_low[i]:
            score += 8
            resonance += 1

        # 窄幅震荡
        pc = pct_chg[i] if not np.isnan(pct_chg[i]) else 0
        if abs(pc) < 2:
            score += 5

        # 优化v2: 删除RSI低位加分 (数据证明反向)
        cr = rsi[i] if not np.isnan(rsi[i]) else 50

        # 回调天数
        dfh = int(days_from_high[i]) if not np.isnan(days_from_high[i]) else 0
        if 3 <= dfh <= params['max_wait_days']:
            score += 10
        elif dfh > params['max_wait_days']:
            score -= 5

        score = min(100, max(0, score))

        # 优化v2: 共振≥1即可 (共振越多反而越差)
        if resonance >= 1 and score >= MIN_SCORE:
            signals[i] = True
            # 强买标记
            is_strong = score >= STRONG_BUY_SCORE and 1 <= dfh <= STRONG_BUY_DAYS_MAX
            infos.append({
                "signal_idx": i,
                "signal_date": str(trade_dates[i]),
                "score": float(score),
                "strong_buy": is_strong,
                "pullback_pct": round(pb, 2),
                "days_from_high": dfh,
                "pos_ma10": round(p10, 2),
                "pos_ma20": round(p20, 2),
                "vol_ratio": round(vr, 3),
                "rsi": round(cr, 1),
                "kdj_j": round(cj, 1),
                "resonance": resonance,
                "shrink_from_high": round(sfh, 2),
                "current_price": round(close[i], 2),
                "is_shuangchuang": is_sc,
            })

    return signals, infos


# =========================================================
# 回测引擎
# =========================================================
class ZhongjunBacktester:
    def __init__(self, start_date: str, end_date: str,
                 pool_codes: Optional[List[str]] = None,
                 max_stocks: Optional[int] = None,
                 lookback_days: int = 120):
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
        pool_desc = f"{len(self.pool_codes)}只指定股池" if self.pool_codes else "全市场"
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
            if self.pool_codes is not None and ts_code not in self.pool_codes:
                continue
            if max_stocks and n_ok >= max_stocks:
                break
            df = load_kline(ts_code, start_date=load_start, end_date=self.end_date)
            if df.empty or len(df) < 60:
                n_skip += 1
                continue

            # 预计算指标
            try:
                df = precompute_indicators(df)
            except Exception:
                n_skip += 1
                continue

            # 检测信号
            try:
                signals, infos = detect_zhongjun_signals_vec(df, ts_code)
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

    def run_single_day(self, trade_date: str, strong_only: bool = False) -> List[Tuple[str, Dict]]:
        selected = []
        for ts_code, (signals, infos) in self._signal_cache.items():
            if not signals.any():
                continue
            idx_map = self._date_idx_map[ts_code]
            i = idx_map.get(trade_date)
            if i is None or i >= len(signals):
                continue
            if signals[i]:
                for info in infos:
                    if info["signal_idx"] == i:
                        if strong_only and not info.get("strong_buy"):
                            continue
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

            sym = ts_code.split(".")[0]
            zt_up = 1.198 if sym.startswith(("3", "688", "689")) else 1.098
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
                "strong_buy": info.get("strong_buy", False),
                "pullback_pct": info.get("pullback_pct", 0),
                "days_from_high": info.get("days_from_high", 0),
                "pos_ma10": info.get("pos_ma10", 0),
                "pos_ma20": info.get("pos_ma20", 0),
                "vol_ratio": info.get("vol_ratio", 0),
                "rsi": info.get("rsi", 0),
                "kdj_j": info.get("kdj_j", 0),
                "resonance": info.get("resonance", 0),
                "shrink_from_high": info.get("shrink_from_high", 0),
                "is_shuangchuang": info.get("is_shuangchuang", False),
            })
        return records

    def run_backtest(self, hold_days: int = 5,
                     strong_only: bool = False,
                     verbose: bool = True) -> Dict:
        daily_counts = []
        all_returns = []
        trade_records = []

        t0 = time.time()
        for i, td in enumerate(self.trade_dates):
            selected = self.run_single_day(td, strong_only=strong_only)
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
        }


def _normalize_code(raw) -> str:
    s = str(raw).strip()
    if "." in s:
        parts = s.split(".")
        num = parts[0].zfill(6)
        suffix = parts[1] if len(parts) > 1 else ""
        if not suffix:
            suffix = "SH" if num.startswith(("6", "9")) else "SZ"
        return f"{num}.{suffix}"
    num = s.zfill(6)
    if num.startswith(("6", "9")):
        return f"{num}.SH"
    else:
        return f"{num}.SZ"


def _load_pool_codes(pool_path: str) -> Optional[List[str]]:
    if not os.path.exists(pool_path):
        print(f"[Pool] 股池文件不存在: {pool_path}, 回退到全市场", flush=True)
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


def print_results(result: Dict, hold_days: int, start: str, end: str, label: str):
    recs = result["trade_records"]
    print()
    print("=" * 80)
    print(f"  {label}")
    print("=" * 80)
    print(f"  回测区间:     {start} ~ {end}")
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

    if not recs:
        print("\n  无交易记录")
        return

    # 强买 vs 普通
    print("\n  强买 vs 普通信号:")
    for strong, name in [(True, "强买(>=80分+回调1-4天)"), (False, "普通(>=60分)")]:
        sub = [r["return"] for r in recs if r["strong_buy"] == strong]
        if sub:
            wr = sum(1 for x in sub if x > 0) / len(sub) * 100
            avg = np.mean(sub)
            print(f"    {name}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:+.2f}%")

    # 评分分档
    print("\n  评分分档胜率:")
    for lo, hi in [(60, 70), (70, 80), (80, 90), (90, 100), (100, 999)]:
        sub = [r["return"] for r in recs if lo <= r["score"] < hi]
        if sub:
            wr = sum(1 for x in sub if x > 0) / len(sub) * 100
            avg = np.mean(sub)
            label = f"评分{lo}-{hi}" if hi < 999 else f"评分{lo}+"
            print(f"    {label}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:+.2f}%")

    # 回调幅度分档
    print("\n  回调幅度分档胜率:")
    for lo, hi in [(-100, -15), (-15, -10), (-10, -5), (-5, 0), (0, 999)]:
        sub = [r["return"] for r in recs if lo <= r["pullback_pct"] < hi]
        if sub:
            wr = sum(1 for x in sub if x > 0) / len(sub) * 100
            avg = np.mean(sub)
            label = f"回调{abs(lo)}-{abs(hi) if hi < 0 else hi}%"
            print(f"    {label}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:+.2f}%")

    # 回调天数分档
    print("\n  回调天数分档胜率:")
    for lo, hi in [(1, 3), (3, 5), (5, 8), (8, 12), (12, 16), (16, 999)]:
        sub = [r["return"] for r in recs if lo <= r["days_from_high"] < hi]
        if sub:
            wr = sum(1 for x in sub if x > 0) / len(sub) * 100
            avg = np.mean(sub)
            label = f"回调{lo}-{hi}天" if hi < 999 else f"回调{lo}天+"
            print(f"    {label}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:+.2f}%")

    # RSI分档
    print("\n  RSI分档胜率:")
    for lo, hi in [(0, 30), (30, 40), (40, 50), (50, 60), (60, 70), (70, 100)]:
        sub = [r["return"] for r in recs if lo <= r["rsi"] < hi]
        if sub:
            wr = sum(1 for x in sub if x > 0) / len(sub) * 100
            avg = np.mean(sub)
            print(f"    RSI {lo}-{hi}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:+.2f}%")

    # KDJ_J分档
    print("\n  KDJ_J分档胜率:")
    for lo, hi in [(-100, 0), (0, 20), (20, 35), (35, 50), (50, 70), (70, 200)]:
        sub = [r["return"] for r in recs if lo <= r["kdj_j"] < hi]
        if sub:
            wr = sum(1 for x in sub if x > 0) / len(sub) * 100
            avg = np.mean(sub)
            print(f"    J {lo}-{hi}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:+.2f}%")

    # 量比分档
    print("\n  量比分档胜率:")
    for lo, hi in [(0, 0.5), (0.5, 0.7), (0.7, 0.85), (0.85, 1.0), (1.0, 1.3), (1.3, 999)]:
        sub = [r["return"] for r in recs if lo <= r["vol_ratio"] < hi]
        if sub:
            wr = sum(1 for x in sub if x > 0) / len(sub) * 100
            avg = np.mean(sub)
            label = f"量比{lo}-{hi}" if hi < 999 else f"量比{lo}+"
            print(f"    {label}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:+.2f}%")

    # 板块分档
    print("\n  板块分档胜率:")
    for board, name in [("gem", "创业板"), ("star", "科创板"), ("main", "主板")]:
        if board == "gem":
            sub = [r["return"] for r in recs if r["ts_code"].startswith("3")]
        elif board == "star":
            sub = [r["return"] for r in recs if r["ts_code"].startswith(("688", "689"))]
        else:
            sub = [r["return"] for r in recs if r["ts_code"].startswith(("60", "00"))]
        if sub:
            wr = sum(1 for x in sub if x > 0) / len(sub) * 100
            avg = np.mean(sub)
            print(f"    {name}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:+.2f}%")

    # 共振数分档
    print("\n  共振数分档胜率:")
    for res in sorted(set(r["resonance"] for r in recs)):
        sub = [r["return"] for r in recs if r["resonance"] == res]
        if sub:
            wr = sum(1 for x in sub if x > 0) / len(sub) * 100
            avg = np.mean(sub)
            print(f"    共振{res}个: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:+.2f}%")

    # 较高点缩量分档
    print("\n  较高点缩量分档胜率:")
    for lo, hi in [(0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 1.0), (1.0, 999)]:
        sub = [r["return"] for r in recs if lo <= r["shrink_from_high"] < hi]
        if sub:
            wr = sum(1 for x in sub if x > 0) / len(sub) * 100
            avg = np.mean(sub)
            label = f"缩量{lo}-{hi}" if hi < 999 else f"缩量{lo}+"
            print(f"    {label}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:+.2f}%")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="中军企稳回调买点策略 TDX 回测")
    parser.add_argument("--start", type=str, default="20220101")
    parser.add_argument("--end", type=str, default=None)
    parser.add_argument("--max-stocks", type=int, default=None)
    parser.add_argument("--hold", type=int, default=5, help="持有天数")
    parser.add_argument("--strong-only", action="store_true", help="仅回测强买信号")
    parser.add_argument("--out", type=str, default=None)
    parser.add_argument("--pool", type=str,
                        default=r"d:\mystock\solo\report_daily\bull_stocks_qualified.csv",
                        help="股票池 CSV")
    parser.add_argument("--min-score", type=int, default=60, help="最低评分阈值")
    args = parser.parse_args()

    global MIN_SCORE
    MIN_SCORE = args.min_score

    pool_codes = _load_pool_codes(args.pool)

    print("=" * 80)
    print("  中军企稳·回调买点策略 TDX 回测 (T+1 开盘买入)")
    print("=" * 80)
    print(f"  策略参数:")
    print(f"    最低评分: {MIN_SCORE}")
    print(f"    强买阈值: {STRONG_BUY_SCORE}分 + 回调1-{STRONG_BUY_DAYS_MAX}天")
    print(f"  股池文件: {args.pool}")
    label = "强买信号" if args.strong_only else "全部BUY信号"
    print(f"  回测模式: {label}")
    print("=" * 80, flush=True)

    bt = ZhongjunBacktester(
        start_date=args.start,
        end_date=args.end,
        pool_codes=pool_codes,
        max_stocks=args.max_stocks,
    )

    result = bt.run_backtest(hold_days=args.hold, strong_only=args.strong_only)
    print_results(result, args.hold, args.start, args.end or "latest", label)

    out_path = args.out or ("tdx_backtest_zhongjun_strong.csv" if args.strong_only else "tdx_backtest_zhongjun_all.csv")
    if result["trade_records"]:
        pd.DataFrame(result["trade_records"]).to_csv(out_path, index=False, encoding='utf-8-sig')
        print(f"\n  交易记录已保存: {out_path}")
    print("=" * 80)


if __name__ == "__main__":
    main()
