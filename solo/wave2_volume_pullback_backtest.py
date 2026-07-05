# -*- coding: utf-8 -*-
"""
Wave2 放量回调算法 - 二波低吸回测 (向量化加速版)

算法来源: d:/mystock/solo/multi_factor_picker/wave2_pattern_scanner.py (detect_volume_pullback_pattern)
接入框架: tdx_backtest

形态定义 (放量回调):
  - 主板+双创 (排除北交所/指数)
  - 近 SURGE_DAYS=20 天内存在一波拉升 >=20%
  - wave1 高点之后: 回调 [10%, 20%) 且 调整天数 >=10 天
  - 放量: 调整期均量(adj_vol) / 基期均量(base_vol) > 1.2
    - base_vol = wave1_high_idx前60日均量
    - adj_vol = wave1_high_idx+1到entry_idx的均量
  - 创新低检测: adj_low > pre_low (不创新低)
  - wave1 高点必须为局部最高点 (前后3天内最高)

触发条件 (v3.10 100%胜率硬过滤):
  回调 >=18% 且 当日量比 <= 1.0

回测模式: T+1 开盘买入, 持有 5/20 天收盘卖出
涨停过滤: T+1 开盘价 >= 前收×涨停板×0.999 时跳过 (避免追高)

性能优化:
  - 每只股票只调用一次 detect_signals_vectorized, 一次性算出全部交易日信号
  - 用 numpy 向量化算 MA, 跳过 MACD/KDJ/BOLL
"""
from __future__ import annotations
import os
import sys
import time
from typing import Dict, List, Tuple, Optional

import numpy as np
import pandas as pd

# 强制 stdout 行缓冲
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

# 加载 tdx_backtest 模块
TDX_BT_DIR = r"d:\mystock\tdx_backtest"
sys.path.insert(0, TDX_BT_DIR)
from data_loader import load_kline, iter_all_day_files, tdx_filename_to_ts_code
from indicators import MA


# =========================================================
# 算法常量 (放量回调 - 来自 wave2_pattern_scanner.py)
# =========================================================
SURGE_DAYS = 20        # 一波拉升窗口
SURGE_MIN = 0.20       # 一波最低涨幅 20%
ADJUST_MAX = 60        # 调整期最长 60 天 (父参数)

# 放量回调核心参数 (来自 wave2_pattern_scanner.py detect_volume_pullback_pattern)
DEEP_ADJUST_MIN = 10           # 调整天数下限 10 天
PULLBACK_ADJUST_MAX = 15      # 调整天数上限 (收紧到10-15天)
PULLBACK_RATIO_MIN = 0.10      # 回调幅度下限 10%
PULLBACK_RATIO_MAX = 0.20      # 回调幅度上限 20% (排除深度回调重叠区)
VOL_RATIO_ADJ_MIN = 1.5        # 放量阈值: 调整期均量 / 基期均量 > 1.5 (原2.0)

# v3.10 100%胜率硬过滤条件
VOL_RATIO_ENTRY_MAX = 1.0      # 入场日量比上限 1.0
PULLBACK_ENTRY_MIN = 0.18      # 入场日回调深度下限 18%

# 评分阈值 (保留供参考, 向量化版不做复杂评分)
SCORE_THRESHOLD_GEM = 20       # 双创阈值
SCORE_THRESHOLD_MAIN = 10      # 主板阈值

# v1优化4: 过滤一波涨幅>80%高位股
WAVE1_GAIN_MAX = 0.80

# 创新低检测: adj_low 必须高于 pre_low (高于前期低点, 不创新低)
REQUIRE_HIGHER_LOW = True

# wave1 高点局部最高验证 (前后3天)
REQUIRE_LOCAL_PEAK = True

# 是否纳入双创 (主板+双创全市场)
INCLUDE_CHUANGCHUANG = True
CHUANGCHUANG_ONLY = True       # True=仅双创


# =========================================================
# 主板/双创判定
# =========================================================
def is_main_board(ts_code: str) -> bool:
    if not ts_code or "." not in ts_code:
        return False
    sym = ts_code.split(".")[0]
    if sym.startswith(("999", "8", "4")):
        return False
    if sym.startswith(("3", "688", "689")):
        return False
    if sym.startswith(("60", "00")):
        return True
    return False

def is_tradeable(ts_code: str, include_chuangchuang: bool = True,
                 chuangchuang_only: bool = False) -> bool:
    if not ts_code or "." not in ts_code:
        return False
    sym = ts_code.split(".")[0]
    if sym.startswith(("999", "8", "4")):
        return False
    if chuangchuang_only:
        return sym.startswith(("3", "688", "689"))
    if include_chuangchuang:
        if sym.startswith(("60", "00", "3", "688", "689")):
            return True
        return False
    else:
        return is_main_board(ts_code)


# =========================================================
# numpy 版 rolling 工具
# =========================================================
def _rolling_mean_np(arr: np.ndarray, n: int) -> np.ndarray:
    if len(arr) < n:
        return np.full(len(arr), np.nan)
    ret = np.cumsum(arr, dtype=float)
    ret[n:] = ret[n:] - ret[:-n]
    ret = ret / n
    ret[:n - 1] = np.nan
    return ret


# =========================================================
# 向量化信号生成
# =========================================================
def detect_signals_vectorized(df: pd.DataFrame) -> Tuple[np.ndarray, List[Dict]]:
    """一次性为整只股票的全部交易日计算 放量回调 信号

    核心逻辑:
      1. 滑动窗口找 wave1 (20日内低点→高点涨幅>=20%)
      2. wave1 高点后 调整天数 >=10 天, 回调 [10%, 20%)
      3. 放量检测: 调整期均量(adj_vol) / 基期均量(base_vol) > 1.2
      4. 调整低点必须高于 wave1 前 40 天内的最低点 (不创新低)
      5. wave1 高点必须是局部最高点 (前后3天最高)
      6. 入场日: 回调 >=18% 且 当日量比 <= 1.0
    """
    n = len(df)
    signals = np.zeros(n, dtype=bool)
    infos: List[Dict] = [{} for _ in range(n)]
    if n < 80:
        return signals, infos

    C = df["close"].values
    H = df["high"].values
    L = df["low"].values
    VOL = df["vol"].values

    for i in range(80, n):
        current_close = C[i]
        triggered = False
        trigger_info = {}

        # 倒序找最近的 wave1 (取最近的合适波峰)
        candidates = []
        for end_idx_offset in range(3, min(150, i - SURGE_DAYS)):
            end_idx = i - end_idx_offset
            if end_idx < SURGE_DAYS:
                break
            window_start = end_idx - SURGE_DAYS
            if window_start < 0:
                continue
            window_closes = C[window_start: end_idx + 1]
            low_idx_in_win = int(np.argmin(window_closes))
            high_idx_in_win = int(np.argmax(window_closes))
            if high_idx_in_win <= low_idx_in_win:
                continue
            if (high_idx_in_win - low_idx_in_win) > SURGE_DAYS - 2:
                continue
            wave1_gain = (window_closes[high_idx_in_win]
                          - window_closes[low_idx_in_win]) / window_closes[low_idx_in_win]
            if wave1_gain < SURGE_MIN:
                continue
            if WAVE1_GAIN_MAX > 0 and wave1_gain > WAVE1_GAIN_MAX:
                continue
            wave1_high_idx = window_start + high_idx_in_win
            wave1_low_idx = window_start + low_idx_in_win

            # 局部最高验证 (前后3天)
            if REQUIRE_LOCAL_PEAK:
                is_local_peak = True
                for off in range(1, 4):
                    if (wave1_high_idx - off >= 0
                            and C[wave1_high_idx - off] > C[wave1_high_idx]):
                        is_local_peak = False
                        break
                    if (wave1_high_idx + off < n
                            and C[wave1_high_idx + off] > C[wave1_high_idx]):
                        is_local_peak = False
                        break
                if not is_local_peak:
                    continue

            # 历史低点验证 (不创新低): adj_low > pre_low
            if REQUIRE_HIGHER_LOW:
                pre_low_start = max(0, wave1_low_idx - 20)
                pre_low = C[pre_low_start: wave1_low_idx + 1].min()
                if current_close <= pre_low:
                    continue

            candidates.append((wave1_high_idx, wave1_low_idx, wave1_gain, end_idx))
            break

        if not candidates:
            continue

        wave1_high_idx, wave1_low_idx, wave1_gain, end_idx = candidates[0]
        wave1_high_price = C[wave1_high_idx]

        if wave1_high_idx >= i:
            continue
        post_closes = C[wave1_high_idx: i + 1]
        if len(post_closes) < 2:
            continue

        # 调整天数 [10, 15] 天
        adjust_days = i - wave1_high_idx
        if not (DEEP_ADJUST_MIN <= adjust_days <= PULLBACK_ADJUST_MAX):
            continue

        # 回调 [10%, 20%)
        post_low = post_closes.min()
        pullback_max = (wave1_high_price - post_low) / wave1_high_price if wave1_high_price > 0 else 0
        if not (PULLBACK_RATIO_MIN <= pullback_max < PULLBACK_RATIO_MAX):
            continue

        pullback_now = (wave1_high_price - current_close) / wave1_high_price

        # ── 放量检测 ──
        # base_vol = wave1_high_idx 前 60 日均量
        vol_base_start = max(0, wave1_high_idx - 60)
        base_vol = VOL[vol_base_start:wave1_high_idx].mean() if wave1_high_idx > 0 else VOL.mean()
        # adj_vol = wave1_high_idx+1 到 i 的均量
        adj_vol = VOL[wave1_high_idx + 1: i + 1].mean()
        vol_ratio_adj = adj_vol / base_vol if base_vol > 0 else 1.0
        if vol_ratio_adj <= VOL_RATIO_ADJ_MIN:
            continue

        # 创新低检测
        if REQUIRE_HIGHER_LOW:
            wave1_start_idx = max(0, wave1_high_idx - 20)
            pre_low_start = max(0, wave1_start_idx - 20)
            if wave1_high_idx >= 40:
                pre_low = C[pre_low_start: wave1_start_idx + 1].min()
            else:
                pre_low = C[0: wave1_high_idx + 1].min()
            adj_low = C[wave1_high_idx: i + 1].min()
            if adj_low <= pre_low:
                continue

        # 当日量比 (近似: 当日量 / 前5日均量)
        if i >= 5:
            base_v_5d = VOL[i - 5: i].mean()
        else:
            base_v_5d = VOL[: i].mean() if i > 0 else 1.0
        vol_ratio_now = VOL[i] / base_v_5d if (base_v_5d and base_v_5d > 0) else 1.0

        # v3.10 100%胜率硬过滤: 回调 >=18% 且 量比 <= 1.0
        if pullback_max >= PULLBACK_ENTRY_MIN and vol_ratio_now <= VOL_RATIO_ENTRY_MAX:
            triggered = True
            trigger_info = {
                "wave1_gain_pct": round(wave1_gain * 100, 1),
                "pullback_max_pct": round(pullback_max * 100, 1),
                "pullback_now_pct": round(pullback_now * 100, 1),
                "adjust_days": adjust_days,
                "vol_ratio_adj": round(vol_ratio_adj, 2),
                "vol_ratio": round(vol_ratio_now, 2),
                "wave1_high": round(wave1_high_price, 2),
                "current_price": round(current_close, 2),
                "trigger": "VOLUME_PULLBACK_18_VR1.0",
            }

        if triggered:
            signals[i] = True
            infos[i] = trigger_info

    return signals, infos


# =========================================================
# 回测引擎
# =========================================================
class Wave2VolumePullbackBacktester:
    """Wave2 放量回调回测 (向量化版)"""

    def __init__(self,
                 start_date: str = "20250101",
                 end_date: str = None,
                 max_stocks: Optional[int] = None,
                 lookback_days: int = 200,
                 pool_codes: Optional[List[str]] = None):
        from datetime import datetime
        self.start_date = start_date
        self.end_date = end_date or datetime.now().strftime("%Y%m%d")
        self.lookback_days = lookback_days
        self.pool_codes = set(pool_codes) if pool_codes else None

        self.kline_dict: Dict[str, pd.DataFrame] = {}
        self._date_idx_map: Dict[str, Dict[str, int]] = {}
        self._signal_cache: Dict[str, Tuple[np.ndarray, List[Dict]]] = {}
        self._load_all_klines_and_signals(max_stocks)

        all_dates = set()
        for df in self.kline_dict.values():
            all_dates.update(df["trade_date"].tolist())
        self.trade_dates = sorted([d for d in all_dates
                                   if self.start_date <= d <= self.end_date])
        pool_desc = f"{len(self.pool_codes)}只指定股池" if self.pool_codes else "全主板+双创"
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
            if not is_tradeable(ts_code, INCLUDE_CHUANGCHUANG, CHUANGCHUANG_ONLY):
                continue
            if self.pool_codes is not None and ts_code not in self.pool_codes:
                continue
            if max_stocks and n_ok >= max_stocks:
                break
            df = load_kline(ts_code, start_date=load_start, end_date=self.end_date)
            if df.empty or len(df) < 80:
                n_skip += 1
                continue

            # 涨停板幅度: 主板10%, 双创20%
            sym = ts_code.split(".")[0]
            if sym.startswith(("3", "688", "689")):
                df["_zt_up"] = 1.198  # 双创 20%
            else:
                df["_zt_up"] = 1.098  # 主板 10%

            try:
                signals, infos = detect_signals_vectorized(df)
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
                selected.append((ts_code, infos[i]))
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
            # 涨停板开盘跳过 (避免追高)
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
                "trigger": info.get("trigger", ""),
                "wave1_gain_pct": info.get("wave1_gain_pct", 0),
                "pullback_max_pct": info.get("pullback_max_pct", 0),
                "pullback_now_pct": info.get("pullback_now_pct", 0),
                "adjust_days": info.get("adjust_days", 0),
                "vol_ratio_adj": info.get("vol_ratio_adj", 0),
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


def _load_pool_codes(pool_path: str) -> Optional[List[str]]:
    if not os.path.exists(pool_path):
        print(f"[Pool] 股池文件不存在: {pool_path}, 回退到全主板+双创", flush=True)
        return None

    try:
        df = pd.read_csv(pool_path)
        code_col = None
        for c in ("ts_code", "code", "股票代码", "symbol"):
            if c in df.columns:
                code_col = c
                break
        if code_col:
            codes = []
            for v in df[code_col].astype(str).tolist():
                v = v.strip()
                if not v or v == "nan":
                    continue
                if "." not in v:
                    v = f"{v}.SH" if v.startswith("6") else f"{v}.SZ"
                codes.append(v)
            if codes:
                print(f"[Pool] 从 {os.path.basename(pool_path)} 加载 {len(codes)} 只股票", flush=True)
                return codes
    except Exception as e:
        print(f"[Pool] CSV加载失败: {e}", flush=True)

    print(f"[Pool] 回退到全主板+双创", flush=True)
    return None


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Wave2 放量回调算法回测 (向量化)")
    parser.add_argument("--start", type=str, default="20250101")
    parser.add_argument("--end", type=str, default=None)
    parser.add_argument("--max-stocks", type=int, default=None)
    parser.add_argument("--hold", type=int, default=5, help="持有天数")
    parser.add_argument("--top-n", type=int, default=None)
    parser.add_argument("--out", type=str, default=None)
    parser.add_argument("--pool", type=str,
                        default=r"d:\mystock\solo\report_daily\bull_stocks_qualified.csv",
                        help="股票池 CSV (含 ts_code/code 列)")
    args = parser.parse_args()

    pool_codes = _load_pool_codes(args.pool)

    print("=" * 80)
    print("  Wave2 放量回调算法回测 (主板+双创, T+1 开盘买入, 向量化)")
    print("=" * 80)
    print(f"  算法参数:")
    print(f"    一波拉升窗口: {SURGE_DAYS} 天, 最低涨幅: {SURGE_MIN*100:.0f}%")
    print(f"    放量回调: 调整 >= {DEEP_ADJUST_MIN} 天, 回调 [{PULLBACK_RATIO_MIN*100:.0f}%, {PULLBACK_RATIO_MAX*100:.0f}%)")
    print(f"    放量阈值: 调整期均量/基期均量 > {VOL_RATIO_ADJ_MIN}")
    print(f"    创新低检测: {'启用 (adj_low > pre_low)' if REQUIRE_HIGHER_LOW else '禁用'}")
    print(f"    局部最高验证: {'启用 (前后3天)' if REQUIRE_LOCAL_PEAK else '禁用'}")
    print(f"    触发条件: 回调 >= {PULLBACK_ENTRY_MIN*100:.0f}% 且 量比 <= {VOL_RATIO_ENTRY_MAX} (v3.10硬过滤)")
    print(f"    一波涨幅上限: {WAVE1_GAIN_MAX*100:.0f}% (过滤高位股)")
    print(f"    涨停板开盘跳过 (避免追高)")
    print(f"  股池文件: {args.pool}")
    print(f"  板块范围: {'仅双创 (创业板+科创板)' if CHUANGCHUANG_ONLY else '主板+双创'}")
    print("=" * 80, flush=True)

    bt = Wave2VolumePullbackBacktester(
        start_date=args.start,
        end_date=args.end,
        max_stocks=args.max_stocks,
        pool_codes=pool_codes,
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

    # 分档统计
    if res.get("trade_records"):
        recs = res["trade_records"]

        print("\n  调整天数分档胜率:")
        for lo, hi in [(10, 15), (15, 20), (20, 30), (30, 61)]:
            sub = [r["return"] for r in recs if lo <= r["adjust_days"] < hi]
            if sub:
                wr = sum(1 for x in sub if x > 0) / len(sub) * 100
                avg = np.mean(sub)
                print(f"    调整{lo}-{hi}天: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:.2f}%")

        print("\n  回调深度分档胜率:")
        for lo, hi in [(10, 13), (13, 16), (16, 18), (18, 20)]:
            sub = [r["return"] for r in recs if lo <= r["pullback_max_pct"] < hi]
            if sub:
                wr = sum(1 for x in sub if x > 0) / len(sub) * 100
                avg = np.mean(sub)
                print(f"    回调{lo}-{hi}%: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:.2f}%")

        print("\n  调整期量比分档胜率:")
        for lo, hi in [(1.2, 1.5), (1.5, 2.0), (2.0, 3.0), (3.0, 999)]:
            sub = [r["return"] for r in recs if lo <= r["vol_ratio_adj"] < hi]
            if sub:
                wr = sum(1 for x in sub if x > 0) / len(sub) * 100
                avg = np.mean(sub)
                print(f"    量比{lo}-{hi}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:.2f}%")

        print("\n  当日量比分档胜率:")
        for lo, hi in [(0, 0.5), (0.5, 0.8), (0.8, 1.0), (1.0, 1.2)]:
            sub = [r["return"] for r in recs if lo <= r["vol_ratio"] < hi]
            if sub:
                wr = sum(1 for x in sub if x > 0) / len(sub) * 100
                avg = np.mean(sub)
                print(f"    量比{lo}-{hi}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:.2f}%")

        print("\n  一波涨幅分档胜率:")
        for lo, hi in [(20, 30), (30, 50), (50, 80)]:
            sub = [r["return"] for r in recs if lo <= r["wave1_gain_pct"] < hi]
            if sub:
                wr = sum(1 for x in sub if x > 0) / len(sub) * 100
                avg = np.mean(sub)
                print(f"    一波{lo}-{hi}%: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:.2f}%")

        # 板块分档
        print("\n  板块分档胜率:")
        for board, name in [("main", "主板"), ("gem", "创业板"), ("star", "科创板")]:
            if board == "main":
                sub = [r["return"] for r in recs
                       if not r["ts_code"].startswith(("3", "688", "689"))]
            elif board == "gem":
                sub = [r["return"] for r in recs if r["ts_code"].startswith("3")]
            else:
                sub = [r["return"] for r in recs
                       if r["ts_code"].startswith(("688", "689"))]
            if sub:
                wr = sum(1 for x in sub if x > 0) / len(sub) * 100
                avg = np.mean(sub)
                print(f"    {name}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:.2f}%")

    # 保存交易记录
    if args.out:
        out_path = args.out
    else:
        out_path = r"d:\mystock\solo\tdx_backtest_wave2_volume_pullback_trades.csv"

    if res.get("trade_records"):
        df_out = pd.DataFrame(res["trade_records"])
        df_out.to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"\n  交易记录已保存: {out_path}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
