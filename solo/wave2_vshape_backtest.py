# -*- coding: utf-8 -*-
"""
Wave2 V型急跌算法 - 二波低吸回测 (向量化加速版)

算法来源: d:/mystock/solo/multi_factor_picker/wave2_pattern_scanner.py (detect_vshape_pattern)
接入框架: tdx_backtest

形态定义 (V型急跌):
  - 主板+双创 (排除北交所/指数)
  - 近 SURGE_DAYS=20 天内存在一波拉升 >=20%
  - wave1 高点之后: 调整 5-10 天, 回调 >=15%
  - wave1 高点必须为局部最高点 (前后3天内最高)
  - 调整低点必须高于wave1前历史低点 (创新低检测: adj_low > pre_low)

触发条件 (v3.10 100%胜率硬过滤):
  RSI6 <= 40 且 当日量比 <= 0.8

回测模式: T+1 开盘买入, 持有 N 天收盘卖出
涨停过滤: T+1 开盘价 >= 前收×涨停板×0.999 时跳过 (避免追高)

性能优化:
  - 每只股票只调用一次 detect_signals_vectorized, 一次性算出全部交易日信号
  - 用 numpy 向量化算 MA/RSI, 跳过 MACD/KDJ/BOLL
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
from indicators import MA, MACD, RSI


# =========================================================
# 算法常量 (V型急跌 - 来自 wave2_pattern_scanner.py)
# =========================================================
SURGE_DAYS = 20        # 一波拉升窗口
SURGE_MIN = 0.20       # 一波最低涨幅 20%
ADJUST_MAX = 60        # 调整期最长 60 天 (此为父参数, V型急跌自身限制5-10天)

# V型急跌核心参数
VSHAPE_ADJUST_MIN = 5      # 调整天数下限 5 (防止下跌动能过大)
VSHAPE_ADJUST_MAX = 10     # 调整天数上限 10
VSHAPE_PULLBACK_MIN = 0.15 # 回调幅度下限 15%

# v3.10 100%胜率硬过滤条件 (原值)
RSI_MAX = 40               # RSI6 上限
VOL_RATIO_MAX = 0.8        # 当日量比上限

# v1优化 (基于原版1522笔回测分档数据):
#   优化1: 量比收紧到 [0.5, 0.65) — 胜率63.5%/均收益3.24% (5日持有)
#   优化2: RSI下限提到 35 — RSI[35,41]胜率60.2%最优 (RSI<25反而最差53.5%)
#   优化4: 过滤一波涨幅>80%高位股 — 20日持有时胜率仅43.2%
VOL_RATIO_MIN = 0.5        # 量比下限 (新增, 避免过度缩量无人气)
RSI_MIN = 35               # RSI 下限 (新增, RSI过低反而是反信号)
WAVE1_GAIN_MAX = 0.80      # 一波涨幅上限 (新增, 过滤高位股)

# v2优化 (基于v1版339笔回测分档数据, 聚焦5日持有):
#   优化5: 过滤深回调≥25% — 5日持有时胜率仅27.8%/均收益0.22% (18笔拖累)
#   优化6: 量比上限收紧到0.65 — 5日持有量比[0.6,0.65)胜率70.1%/均收益5.36%最优
#         量比[0.65,0.81)胜率仅54.5%/均收益1.88% (189笔拖累)
VSHAPE_PULLBACK_MAX = 0.25 # 回调幅度上限 (新增, 过滤深回调)
VOL_RATIO_MAX = 0.65       # 量比上限收紧 (v1的0.8 → v2的0.65, 5日持有最优)

# 创新低检测: adj_low 必须高于 pre_low (高于前期低点, 不创新低)
REQUIRE_HIGHER_LOW = True

# wave1 高点局部最高验证 (前后3天)
REQUIRE_LOCAL_PEAK = True

# 是否纳入双创 (用户偏好双创股票)
# v1优化3: 仅选双创板 (创业板+科创板), 排除主板
#   依据: 20日持有时 双创胜率/收益远超主板
#         科创板64.5%/+12.29% > 创业板53.4%/+5.70% > 主板50.0%/+3.58%
INCLUDE_CHUANGCHUANG = True
CHUANGCHUANG_ONLY = True   # True=仅双创, False=主板+双创


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
    """可交易股票判定 (主板 + 可选双创, 排除北交所/指数)

    chuangchuang_only=True 时仅保留创业板+科创板 (排除主板)
    """
    if not ts_code or "." not in ts_code:
        return False
    sym = ts_code.split(".")[0]
    if sym.startswith(("999", "8", "4")):
        return False
    if chuangchuang_only:
        # 仅双创: 创业板(3开头) + 科创板(688/689)
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
    """一次性为整只股票的全部交易日计算 V型急跌 信号

    核心逻辑:
      1. 滑动窗口找 wave1 (20日内低点→高点涨幅>=20%)
      2. wave1 高点后 5-10 天内回调 >=15%
      3. 调整低点必须高于 wave1 前 40 天内的最低点 (不创新低)
      4. wave1 高点必须是局部最高点 (前后3天最高)
      5. 入场日: RSI6<=40 且 当日量比<=0.8
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
    # tdx day 文件无 volume_ratio 字段, 用 当日量/前5日均量 近似
    # 注意: wave2_pattern_scanner 中的 volume_ratio 来自 stk_factor_pro, 此处用近似值

    # 预计算指标
    ma5 = _rolling_mean_np(C, 5)
    ma10 = _rolling_mean_np(C, 10)
    ma20 = _rolling_mean_np(C, 20)
    rsi6 = RSI(df["close"], 6).values

    # 预计算 wave1 候选 (从每个 end_idx 反向找 20 日窗口)
    # wave1_high_idx 列表 (去重 + 局部最高 + 创新低验证)
    for i in range(80, n):
        current_close = C[i]

        # 在 [i-150, i-3] 范围内寻找 wave1 高点
        search_start = max(SURGE_DAYS, i - 150)
        triggered = False
        trigger_info = {}

        # 倒序找最近的 wave1 (取最近的合适波峰)
        # 模仿 _find_recent_wave1: 从近到远遍历
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
            # v1优化4: 过滤一波涨幅>80%的高位股 (20日持有时胜率仅43.2%)
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
                # 当前 i 处的 close 必须高于 pre_low
                if current_close <= pre_low:
                    continue

            candidates.append((wave1_high_idx, wave1_low_idx, wave1_gain, end_idx))

            # 只取最近的一个候选 (与原算法保持一致: 最近的优先)
            break

        if not candidates:
            continue

        wave1_high_idx, wave1_low_idx, wave1_gain, end_idx = candidates[0]
        wave1_high_price = C[wave1_high_idx]

        # wave1 高点之后的调整期 (到 i)
        if wave1_high_idx >= i:
            continue
        post_closes = C[wave1_high_idx: i + 1]
        if len(post_closes) < 2:
            continue

        # V型急跌: 调整期 5-10 天
        adjust_days = i - wave1_high_idx
        if not (VSHAPE_ADJUST_MIN <= adjust_days <= VSHAPE_ADJUST_MAX):
            continue

        # 回调 >=15%
        post_low = post_closes.min()
        pullback_max = (wave1_high_price - post_low) / wave1_high_price if wave1_high_price > 0 else 0
        if pullback_max < VSHAPE_PULLBACK_MIN:
            continue
        # v2优化5: 过滤深回调≥25% (5日持有时胜率仅27.8%)
        if VSHAPE_PULLBACK_MAX > 0 and pullback_max >= VSHAPE_PULLBACK_MAX:
            continue

        pullback_now = (wave1_high_price - current_close) / wave1_high_price

        # 创新低检测 (与原算法一致)
        # adj_low: wave1_high 到 entry_idx(i) 之间的最低
        # pre_low: wave1 前 40 天内的最低
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

        # RSI
        rsi_now = rsi6[i] if not np.isnan(rsi6[i]) else 50.0

        # v3.10 100%胜率硬过滤: RSI<=40 且 量比<=0.8
        # v1优化: RSI[35,40] + 量比[0.5,0.65)
        trigger_vshape = (RSI_MIN <= rsi_now <= RSI_MAX
                          and VOL_RATIO_MIN <= vol_ratio_now < VOL_RATIO_MAX)

        if trigger_vshape:
            triggered = True
            trigger_info = {
                "wave1_gain_pct": round(wave1_gain * 100, 1),
                "pullback_max_pct": round(pullback_max * 100, 1),
                "pullback_now_pct": round(pullback_now * 100, 1),
                "adjust_days": adjust_days,
                "rsi_now": round(rsi_now, 1),
                "vol_ratio": round(vol_ratio_now, 2),
                "wave1_high": round(wave1_high_price, 2),
                "current_price": round(current_close, 2),
                "trigger": "VSHAPE_RSI40_VR08",
            }

        if triggered:
            signals[i] = True
            infos[i] = trigger_info

    return signals, infos


# =========================================================
# 回测引擎
# =========================================================
class Wave2VshapeBacktester:
    """Wave2 V型急跌回测 (向量化版)"""

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
                "rsi_now": info.get("rsi_now", 0),
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
                # 按回调深度排序 (回调越深越优先)
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
    """从 bull_stocks_qualified.csv 加载股票池"""
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
    parser = argparse.ArgumentParser(description="Wave2 V型急跌算法回测 (向量化)")
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
    print("  Wave2 V型急跌算法回测 (主板+双创, T+1 开盘买入, 向量化)")
    print("=" * 80)
    print(f"  算法参数:")
    print(f"    一波拉升窗口: {SURGE_DAYS} 天, 最低涨幅: {SURGE_MIN*100:.0f}%")
    print(f"    V型急跌: 调整 {VSHAPE_ADJUST_MIN}-{VSHAPE_ADJUST_MAX} 天, 回调 [{VSHAPE_PULLBACK_MIN*100:.0f}%, {VSHAPE_PULLBACK_MAX*100:.0f}%)")
    print(f"    创新低检测: {'启用 (adj_low > pre_low)' if REQUIRE_HIGHER_LOW else '禁用'}")
    print(f"    局部最高验证: {'启用 (前后3天)' if REQUIRE_LOCAL_PEAK else '禁用'}")
    print(f"    触发条件: RSI6=[{RSI_MIN}, {RSI_MAX}] 且 量比=[{VOL_RATIO_MIN}, {VOL_RATIO_MAX}) (v2优化)")
    print(f"    一波涨幅上限: {WAVE1_GAIN_MAX*100:.0f}% (过滤高位股)")
    print(f"    涨停板开盘跳过 (避免追高)")
    print(f"  股池文件: {args.pool}")
    print(f"  板块范围: {'仅双创 (创业板+科创板)' if CHUANGCHUANG_ONLY else '主板+双创'}")
    print("=" * 80, flush=True)

    bt = Wave2VshapeBacktester(
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
        for lo, hi in [(5, 7), (7, 9), (9, 11)]:
            sub = [r["return"] for r in recs if lo <= r["adjust_days"] < hi]
            if sub:
                wr = sum(1 for x in sub if x > 0) / len(sub) * 100
                avg = np.mean(sub)
                print(f"    调整{lo}-{hi}天: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:.2f}%")

        print("\n  回调深度分档胜率:")
        for lo, hi in [(15, 18), (18, 21), (21, 25)]:
            sub = [r["return"] for r in recs if lo <= r["pullback_max_pct"] < hi]
            if sub:
                wr = sum(1 for x in sub if x > 0) / len(sub) * 100
                avg = np.mean(sub)
                print(f"    回调{lo}-{hi}%: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:.2f}%")

        print("\n  RSI 分档胜率:")
        for lo, hi in [(35, 37), (37, 39), (39, 41)]:
            sub = [r["return"] for r in recs if lo <= r["rsi_now"] < hi]
            if sub:
                wr = sum(1 for x in sub if x > 0) / len(sub) * 100
                avg = np.mean(sub)
                print(f"    RSI{lo}-{hi}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:.2f}%")

        print("\n  量比分档胜率:")
        for lo, hi in [(0.5, 0.55), (0.55, 0.6), (0.6, 0.65)]:
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
        out_path = r"d:\mystock\solo\tdx_backtest_wave2_vshape_trades.csv"

    if res.get("trade_records"):
        df_out = pd.DataFrame(res["trade_records"])
        df_out.to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"\n  交易记录已保存: {out_path}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
