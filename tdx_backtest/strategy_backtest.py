# -*- coding: utf-8 -*-
"""
Strategy Backtest — 回测 tushare_quant.strategy 选股效果

将 strategy 函数的硬编码阈值参数化, 遍历不同过滤条件组合,
找到每天选股 1-5 只且胜率最高的参数配置.

依赖:
  - TDX 本地日线数据 (C:\\new_tdx\\vipdoc)
  - stock_basic.csv (股票名称, 用于 ST 过滤)
  - 复制自 tushare_quant.py: strategy + barslast
"""
from __future__ import annotations
import os
import sys
import time
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from itertools import product

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_loader import load_kline, iter_all_day_files, tdx_filename_to_ts_code


# =========================================================
# 股票名称缓存 (ST 过滤用)
# =========================================================
_STOCK_BASIC_CACHE: Dict[str, str] = {}


def load_stock_names(stock_basic_csv: str = r"d:\mystock\cache_daily\stock_basic.csv") -> Dict[str, str]:
    """加载股票名称字典 (ST 过滤用)"""
    global _STOCK_BASIC_CACHE
    if _STOCK_BASIC_CACHE:
        return _STOCK_BASIC_CACHE
    if not os.path.exists(stock_basic_csv):
        print(f"[WARN] stock_basic.csv 不存在: {stock_basic_csv}")
        return {}
    # 尝试 GBK 然后 UTF-8
    for enc in ("gbk", "utf-8", "utf-8-sig"):
        try:
            df = pd.read_csv(stock_basic_csv, encoding=enc)
            if not df.empty and "ts_code" in df.columns:
                _STOCK_BASIC_CACHE = dict(zip(df["ts_code"], df["name"].fillna("")))
                print(f"[StockBasic] 加载 {len(_STOCK_BASIC_CACHE)} 只股票名称 (encoding={enc})")
                return _STOCK_BASIC_CACHE
        except Exception:
            continue
    return {}


def get_stock_name(code: str) -> str:
    return _STOCK_BASIC_CACHE.get(code, code)


# =========================================================
# barslast (复制自 tushare_quant.py)
# =========================================================
def barslast(series: pd.Series) -> pd.Series:
    """向量化版本: 找到最近 True 的距离"""
    arr = series.values.astype(bool)
    n = len(arr)
    true_positions = np.where(arr)[0]
    if len(true_positions) == 0:
        return pd.Series([np.nan] * n, index=series.index)
    start_idx = 0
    if arr[0]:
        valid_positions = true_positions[true_positions > 0]
        if len(valid_positions) == 0:
            return pd.Series([np.nan] * n, index=series.index)
        true_positions = valid_positions
        start_idx = 1
    indices = np.arange(n)
    idx = np.searchsorted(true_positions, indices, side="right") - 1
    result = np.where(idx >= 0, indices - true_positions[idx], np.nan)
    if start_idx == 1:
        result[0] = np.nan
    for pos in true_positions:
        result[pos] = 0
    return pd.Series(result, index=series.index)


# =========================================================
# 参数化过滤条件
# =========================================================
@dataclass
class Filters:
    """strategy 函数的可调过滤条件 (默认值 = 原函数硬编码值)"""
    mv_min:         float = 80.0    # 总市值下限 (亿元)
    ret_2m_max:     float = 1.0     # 两个月涨幅上限
    amp_20d_max:    float = 1.8     # 20日振幅上限
    ma20_dev_max:   float = 1.3     # close/ma20 偏离上限
    ma60_dev_max:   float = 2.0     # close/ma60 偏离上限
    ret_5d_max:     float = 0.3     # 5日涨幅上限
    ztts_min:       int   = 3       # ztts 最小值
    ztts_max:       int   = 90      # ztts 最大值
    amp_ztts_max:   float = 1.3     # ztts 区间振幅上限
    dist_high_max:  float = 1.2     # close/H[-ztts-1] 上限
    high_ratio_min: float = 0.8     # 区间高/60日高 下限
    vol_ratio_min:  float = 0.7     # 量能接近前高比例

    def label(self) -> str:
        """简短标签, 用于打印"""
        return (f"ztts={self.ztts_min}-{self.ztts_max}"
                f",amp_ztts={self.amp_ztts_max}"
                f",dist_high={self.dist_high_max}"
                f",ret5d={self.ret_5d_max}")


# =========================================================
# 参数化 strategy 函数 (复制自 tushare_quant.py L6770, 加参数化)
# =========================================================
def strategy(df: pd.DataFrame, code: str, emotion_stage: str = "强",
             total_mv: float = 0, filters: Filters = None) -> bool:
    """strategy 函数参数化版本 — 默认 filters 与原函数一致"""
    if filters is None:
        filters = Filters()

    # ===== 快速前置过滤 =====
    if len(df) < 80:
        return False
    if total_mv / 10000 < filters.mv_min:
        return False
    if code.startswith("1") or code.startswith("2"):
        return False

    # 两个月涨幅过滤
    if len(df) >= 40:
        C_vals = df["close"].values
        ret_2m = C_vals[-1] / C_vals[-40] - 1
        if ret_2m > filters.ret_2m_max:
            return False

    C = df["close"].values
    O = df["open"].values
    H = df["high"].values
    L = df["low"].values
    VOL = df["vol"].values

    IS_CYB_KCB = code.startswith("3") or code.startswith("688") or code.startswith("689")
    ZT_SINGLE_UP = 1.198 if IS_CYB_KCB else 1.098

    # 今天已涨停
    if len(df) >= 3:
        if C[-1] / C[-2] >= ZT_SINGLE_UP:
            return False

    # ST 过滤
    name = get_stock_name(code)
    if name.upper().startswith("ST") or name.upper().startswith("*ST"):
        return False

    # 20日振幅过滤
    if len(df) >= 20:
        hh = H[-20:].max()
        ll = L[-20:].min()
        if (hh / ll - 1) > filters.amp_20d_max:
            return False

    # 均线
    C_series = pd.Series(C)
    ma5  = C_series.rolling(5).mean().values
    ma10 = C_series.rolling(10).mean().values
    ma20 = C_series.rolling(20).mean().values
    ma22 = C_series.rolling(30).mean().values
    ma60 = C_series.rolling(60).mean().values

    if C[-1] >= ma20[-1] * filters.ma20_dev_max or C[-1] / ma60[-1] > filters.ma60_dev_max:
        return False
    if C[-1] < ma20[-1] or ma10[-1] < ma20[-1] * 0.97 or ma5[-1] < ma10[-1] * 0.97:
        return False

    # 涨停判断
    ZT_1day = (C_series.shift(1) / C_series.shift(2) < 1.08) & (C_series / C_series.shift(1) > 1.098)
    ZT_2day = ((C_series.shift(1) / C_series.shift(2) >= 1.051) &
               (C_series / C_series.shift(1) >= 1.051) &
               (C_series / C_series.shift(2) >= 1.11))
    ZT = ZT_1day | ZT_2day
    ZTTS = barslast(ZT)
    ztts = ZTTS.iloc[-1]
    if ztts == 0:
        ztts = ZTTS.iloc[-2]
    if np.isnan(ztts):
        return False
    ztts = int(ztts)

    # 5日涨幅过滤
    if len(C) >= 6 and (C[-1] / C[-6] - 1) > filters.ret_5d_max:
        return False

    # ztts 范围
    if ztts < filters.ztts_min or ztts > filters.ztts_max:
        return False

    ztts_close = C[-ztts:]
    ztts_df = df.iloc[-ztts:]
    ztts_vol = ztts_df["vol"].values
    vol_ma5 = ztts_df["vol"].rolling(5).mean().values

    ref_close = C[-ztts - 1]
    cond3 = (ztts_close.max() / ztts_close.min()) < filters.amp_ztts_max
    cond4 = (C[-1] / H[-ztts - 1]) < filters.dist_high_max
    cond5 = H[-ztts:].max() >= H[-60:].max() * filters.high_ratio_min
    cond6 = ma22[-1] >= ma22[-2]

    cond_low_vol = (ztts_vol < vol_ma5 * 0.9).any()
    cum_max = np.maximum.accumulate(ztts_close)
    drawdown = (ztts_close - cum_max) / cum_max
    cond_dd = drawdown.min() >= -0.15
    down_k = ztts_df["close"].values < ztts_df["open"].values
    big_vol = ztts_vol > vol_ma5 * 1.5
    big_drop = ztts_df["pct_chg"].values < -5 if "pct_chg" in ztts_df.columns else False
    cond_no_bad_k = ~(down_k & big_vol & big_drop).any()
    cond7 = cond_low_vol and cond_no_bad_k

    TJ = cond3 and cond4 and cond5 and cond6
    if not TJ:
        return False

    # XH 判断
    highest_close = C[-ztts - 1:-1].max()
    vol_peak = VOL[-ztts - 1:-1].max()
    vol_condition = VOL[-1] >= vol_peak * filters.vol_ratio_min if vol_peak > 0 else True
    cond_xh1 = C[-1] > C[-2] and C[-1] > C[-3] and C[-1] / C[-2] > 1.05 and vol_condition
    cond_xh3 = C[-1] >= highest_close and C[-1] / C[-2] < 1.09
    cond_xh2 = C[-1] > C[-2] and C[-1] / ma5[-1] < 1.11 and C[-1] / ma5[-1] > 0.97

    return (cond_xh1 or cond_xh3) and cond_xh2


# =========================================================
# 性能优化: 预计算指标 + 向量化信号生成
# =========================================================
def _rolling_mean_np(arr: np.ndarray, n: int) -> np.ndarray:
    """numpy 版 rolling mean (比 pandas rolling 快 3-5x)"""
    if len(arr) < n:
        return np.full(len(arr), np.nan)
    ret = np.cumsum(arr, dtype=float)
    ret[n:] = ret[n:] - ret[:-n]
    ret = ret / n
    ret[:n - 1] = np.nan
    return ret


def _rolling_max_np(arr: np.ndarray, n: int) -> np.ndarray:
    """numpy 版 rolling max (用 pandas 实现, C 优化)"""
    s = pd.Series(arr)
    return s.rolling(n, min_periods=n).max().values


def _rolling_min_np(arr: np.ndarray, n: int) -> np.ndarray:
    """numpy 版 rolling min"""
    s = pd.Series(arr)
    return s.rolling(n, min_periods=n).min().values


def _barslast_np(cond: np.ndarray) -> np.ndarray:
    """numpy 版 barslast: 每个位置距上次 True 的天数

    特殊规则: 第1个 True 不算, 返回 NaN
    """
    n = len(cond)
    result = np.full(n, np.nan)
    last_true = -1
    for i in range(n):
        if i > 0 and cond[i]:
            last_true = i
        if last_true >= 0:
            result[i] = i - last_true
        if cond[i] and i > 0:
            result[i] = 0
    # 第1天 True → NaN
    if n > 0 and cond[0]:
        result[0] = np.nan
    return result


def precompute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """一次性预计算 strategy 所需的全部指标列

    计算后, 回测时只需查列, 无需重复 rolling/barslast

    Returns:
        df 添加列: ma5/ma10/ma20/ma22/ma60, ret_2m, amp_20d, ma20_dev, ma60_dev,
        ret_5d, today_zt, ztts, vol_ma5, ZT, ZTTS_global
    """
    out = df.copy()
    C = out["close"].values
    H = out["high"].values
    L = out["low"].values
    VOL = out["vol"].values
    n = len(C)

    # 均线 (一次计算)
    out["ma5"]  = _rolling_mean_np(C, 5)
    out["ma10"] = _rolling_mean_np(C, 10)
    out["ma20"] = _rolling_mean_np(C, 20)
    out["ma22"] = _rolling_mean_np(C, 30)  # 原 strategy 中 ma22 = rolling(30)
    out["ma60"] = _rolling_mean_np(C, 60)
    out["vol_ma5"] = _rolling_mean_np(VOL, 5)

    # 涨幅
    if n >= 40:
        ret_2m = np.full(n, np.nan)
        ret_2m[40:] = C[40:] / C[:-40] - 1
        out["ret_2m"] = ret_2m
    if n >= 6:
        ret_5d = np.full(n, np.nan)
        ret_5d[6:] = C[6:] / C[:-6] - 1
        out["ret_5d"] = ret_5d

    # 20日振幅
    out["hh_20"] = _rolling_max_np(H, 20)
    out["ll_20"] = _rolling_min_np(L, 20)
    out["amp_20d"] = out["hh_20"] / out["ll_20"] - 1

    # 偏离度
    out["ma20_dev"] = C / out["ma20"]
    out["ma60_dev"] = C / out["ma60"]

    # 今天涨停
    today_zt = np.zeros(n, dtype=bool)
    if n >= 2:
        today_zt[1:] = C[1:] / C[:-1] >= 1.098  # 保守用 10% (双创板单独判断)
    out["today_zt"] = today_zt

    # ZT, ZTTS (一次计算全部交易日)
    C_s1 = np.concatenate([[np.nan], C[:-1]])
    C_s2 = np.concatenate([[np.nan, np.nan], C[:-2]])
    ZT_1day = (C_s1 / C_s2 < 1.08) & (C / C_s1 > 1.098)
    ZT_2day = (C_s1 / C_s2 >= 1.051) & (C / C_s1 >= 1.051) & (C / C_s2 >= 1.11)
    ZT = (ZT_1day | ZT_2day).astype(bool)
    out["ZT"] = ZT
    out["ztts"] = _barslast_np(ZT)  # 全局 ZTTS: 每个位置距上次 True 的天数

    # 双创板涨停阈值
    return out


def strategy_vectorized(df_pre: pd.DataFrame, code: str,
                        filters: Filters = None) -> np.ndarray:
    """向量化 strategy: 一次性计算所有交易日的信号

    用预计算的指标列, 避免重复 rolling/barslast

    Args:
        df_pre: precompute_indicators 的返回值
        code: 股票代码
        filters: 过滤条件

    Returns:
        bool array, True = 该日触发买入信号
    """
    if filters is None:
        filters = Filters()

    n = len(df_pre)
    signals = np.zeros(n, dtype=bool)
    if n < 80:
        return signals
    if code.startswith("1") or code.startswith("2"):
        return signals

    # ST 过滤 (一次性)
    name = get_stock_name(code)
    if name.upper().startswith("ST") or name.upper().startswith("*ST"):
        return signals

    # 提取预计算的列 (numpy array, 快速访问)
    C = df_pre["close"].values
    H = df_pre["high"].values
    L = df_pre["low"].values
    VOL = df_pre["vol"].values
    O = df_pre["open"].values
    ma5  = df_pre["ma5"].values
    ma10 = df_pre["ma10"].values
    ma20 = df_pre["ma20"].values
    ma22 = df_pre["ma22"].values
    ma60 = df_pre["ma60"].values
    vol_ma5_global = df_pre["vol_ma5"].values
    ZTTS_arr = df_pre["ztts"].values

    # 双创板涨停阈值
    IS_CYB_KCB = code.startswith("3") or code.startswith("688") or code.startswith("689")
    ZT_UP = 1.198 if IS_CYB_KCB else 1.098
    if n >= 2:
        today_zt = np.zeros(n, dtype=bool)
        today_zt[1:] = C[1:] / C[:-1] >= ZT_UP
    else:
        today_zt = np.zeros(n, dtype=bool)

    # 均线排列 (向量化)
    ma_arrange = (C >= ma20) & (ma10 >= ma20 * 0.97) & (ma5 >= ma10 * 0.97)

    # 前置过滤 (向量化, 一次性算出所有不合格的交易日)
    valid = np.ones(n, dtype=bool)
    valid[:80] = False  # 数据不足
    if "ret_2m" in df_pre.columns:
        valid &= df_pre["ret_2m"].values <= filters.ret_2m_max
    valid &= df_pre["amp_20d"].values <= filters.amp_20d_max
    valid &= ~today_zt
    valid &= df_pre["ma20_dev"].values <= filters.ma20_dev_max
    valid &= df_pre["ma60_dev"].values <= filters.ma60_dev_max
    valid &= ma_arrange
    if "ret_5d" in df_pre.columns:
        valid &= df_pre["ret_5d"].values <= filters.ret_5d_max

    # 对每个有效交易日, 判断 ztts 依赖的条件 (动态切片, 难以完全向量化)
    for i in np.where(valid)[0]:
        ztts = ZTTS_arr[i]
        if ztts == 0:
            ztts = ZTTS_arr[i - 1] if i > 0 else np.nan
        if np.isnan(ztts):
            continue
        ztts = int(ztts)
        if ztts < filters.ztts_min or ztts > filters.ztts_max:
            continue
        if i - ztts < 0:
            continue

        # ztts 区间切片 (用预计算的数组, 无需重新 rolling)
        ztts_close = C[i - ztts + 1: i + 1]
        ztts_vol = VOL[i - ztts + 1: i + 1]
        # 完全模拟原版: 在 ztts 区间内重新 rolling(5) (前4个为 NaN)
        # 注意: 必须用 ztts 区间内的数据重算, 不能用全局 vol_ma5 切片
        vol_ma5_slice = _rolling_mean_np(ztts_vol, 5)

        # cond3: 区间振幅
        cond3 = ztts_close.max() / ztts_close.min() < filters.amp_ztts_max
        # cond4: 距前高 (原版 H[-ztts-1] = 切片倒数第ztts+1个 = 全局 H[i-ztts])
        cond4 = C[i] / H[i - ztts] < filters.dist_high_max if i - ztts >= 0 else False
        # cond5: 区间高 / 60日高 (原版 H[-60:] = 从 i-59 到 i, 共60个元素)
        h60_start = max(0, i - 59)
        cond5 = H[i - ztts + 1: i + 1].max() >= H[h60_start: i + 1].max() * filters.high_ratio_min
        # cond6: ma22 走平/拐头
        cond6 = ma22[i] >= ma22[i - 1] if i > 0 else False

        if not (cond3 and cond4 and cond5 and cond6):
            continue

        # 注: 原版有 cond_low_vol / cond_no_bad_k / cond_dd / cond7 计算,
        # 但 TJ 只用 cond3-6, 这些条件未参与最终判断 (死代码), 此处省略

        # XH 判断
        highest_close = C[i - ztts: i].max() if i - ztts >= 0 else C[:i].max()
        vol_peak = VOL[i - ztts: i].max() if i - ztts >= 0 else VOL[:i].max()
        vol_cond = VOL[i] >= vol_peak * filters.vol_ratio_min if vol_peak > 0 else True
        cond_xh1 = (C[i] > C[i - 1] and C[i] > C[i - 2] and
                    C[i] / C[i - 1] > 1.05 and vol_cond)
        cond_xh3 = C[i] >= highest_close and C[i] / C[i - 1] < 1.09
        cond_xh2 = (C[i] > C[i - 1] and
                    C[i] / ma5[i] < 1.11 and C[i] / ma5[i] > 0.97)

        signals[i] = (cond_xh1 or cond_xh3) and cond_xh2

    return signals


# =========================================================
# 回测引擎
# =========================================================
class StrategyBacktester:
    """回测 strategy 选股效果

    流程:
      1. 预加载全部候选股票的 K 线 (到内存)
      2. 遍历回测区间每个交易日 T
      3. 对每只股票取截止 T 日的 K 线切片, 调用 strategy
      4. 记录选中的股票 + 未来 N 日收益
    """

    def __init__(self,
                 start_date: str = "20250101",
                 end_date: str = None,
                 max_stocks: int = None,
                 lookback_days: int = 400):
        """
        Args:
            start_date: 回测起始日 YYYYMMDD
            end_date: 回测结束日 YYYYMMDD (None=最新)
            max_stocks: 最多加载多少只股票 (None=全部, 用于调试)
            lookback_days: 每只股票加载多少天历史 (需 >=80 + ztts_max)
        """
        self.start_date = start_date
        self.end_date = end_date or datetime.now().strftime("%Y%m%d")
        self.lookback_days = lookback_days

        # 预加载
        load_stock_names()
        self.kline_dict: Dict[str, pd.DataFrame] = {}        # 原始 K 线
        self.kline_pre_dict: Dict[str, pd.DataFrame] = {}    # 预计算指标
        self._signal_cache: Dict[str, Dict[str, np.ndarray]] = {}  # 信号缓存
        self._load_all_klines(max_stocks)

        # 回测交易日列表 (从所有股票的 trade_date 并集中提取)
        all_dates = set()
        for df in self.kline_dict.values():
            all_dates.update(df["trade_date"].tolist())
        self.trade_dates = sorted([d for d in all_dates
                                    if self.start_date <= d <= self.end_date])
        # 预建 trade_date → 行索引 的映射 (加速单日查询)
        self._date_idx_map: Dict[str, Dict[str, int]] = {}
        for ts_code, df in self.kline_dict.items():
            self._date_idx_map[ts_code] = dict(zip(df["trade_date"], df.index))
        print(f"[Backtest] 回测区间: {self.start_date} ~ {self.end_date}, "
              f"交易日数: {len(self.trade_dates)}")

    def _load_all_klines(self, max_stocks: Optional[int]):
        """加载全部股票 K 线 + 预计算指标"""
        dt = datetime.strptime(self.start_date, "%Y%m%d")
        load_start = (dt - timedelta(days=self.lookback_days)).strftime("%Y%m%d")

        codes_loaded = 0
        t0 = time.time()
        for path in iter_all_day_files(markets=("SH", "SZ")):
            if max_stocks and codes_loaded >= max_stocks:
                break
            ts_code = tdx_filename_to_ts_code(path)
            if not ts_code:
                continue
            if ts_code.startswith("999") or ts_code.startswith("8") or ts_code.startswith("4"):
                continue
            df = load_kline(ts_code, start_date=load_start, end_date=self.end_date)
            if df.empty or len(df) < 80:
                continue
            if "pct_chg" not in df.columns:
                df["pct_chg"] = df["close"].pct_change() * 100
            self.kline_dict[ts_code] = df
            # 预计算指标 (一次性, 避免回测时重复计算)
            self.kline_pre_dict[ts_code] = precompute_indicators(df)
            codes_loaded += 1

        elapsed = time.time() - t0
        mem_mb = sum(len(v) for v in self.kline_dict.values()) * 8 / 1024 / 1024
        print(f"[Load] 加载 {codes_loaded} 只股票, 耗时 {elapsed:.1f}s, "
              f"内存约 {mem_mb:.0f}MB (含预计算)")

    def _get_signals(self, filters: Filters) -> Dict[str, np.ndarray]:
        """获取或计算所有股票的信号向量 (带缓存)

        每只股票只调用一次 strategy_vectorized, 而非每天调用
        """
        key = filters.label()
        if key in self._signal_cache:
            return self._signal_cache[key]

        signals = {}
        t0 = time.time()
        for ts_code, df_pre in self.kline_pre_dict.items():
            sig = strategy_vectorized(df_pre, ts_code, filters)
            if sig.any():
                signals[ts_code] = sig
        elapsed = time.time() - t0
        print(f"  [Precompute] {key}: {len(signals)} 只有信号, 耗时 {elapsed:.1f}s")
        self._signal_cache[key] = signals
        return signals

    def run_single_day(self, trade_date: str, filters: Filters,
                       total_mv: float = 1e12) -> List[str]:
        """单日选股: 查预计算的信号向量 (O(1) 查询, 无需调用 strategy)

        Args:
            trade_date: 交易日 YYYYMMDD
            filters: 过滤条件
            total_mv: 假设全部股票市值都满足 (设大值跳过市值过滤)
        Returns:
            选中的 ts_code 列表
        """
        signal_dict = self._get_signals(filters)
        selected = []
        for ts_code, sig in signal_dict.items():
            idx_map = self._date_idx_map.get(ts_code)
            if not idx_map:
                continue
            i = idx_map.get(trade_date)
            if i is None or i >= len(sig):
                continue
            if sig[i]:
                selected.append(ts_code)
        return selected

    def evaluate_signals(self, selected: List[str], trade_date: str,
                         hold_days: int = 5) -> Dict[str, float]:
        """评估选股信号未来 N 日收益

        Args:
            selected: 选中的 ts_code 列表
            trade_date: 信号日
            hold_days: 持有天数

        Returns:
            {ts_code: 未来 N 日收益率%}
        """
        results = {}
        # 找到 trade_date 在 df 中的位置
        for ts_code in selected:
            df = self.kline_dict.get(ts_code)
            if df is None:
                continue
            idx = df.index[df["trade_date"] == trade_date].tolist()
            if not idx:
                continue
            i = idx[0]
            if i + hold_days >= len(df):
                continue  # 数据不足
            buy_close = df.iloc[i]["close"]
            sell_close = df.iloc[i + hold_days]["close"]
            ret = (sell_close / buy_close - 1) * 100
            results[ts_code] = ret
        return results

    def run_backtest(self, filters: Filters, hold_days: int = 5,
                     verbose: bool = True) -> Dict:
        """完整回测: 遍历所有交易日

        Returns:
            {
                'daily_counts': [每日选股数],
                'all_returns': [全部交易收益率%],
                'win_rate': 胜率,
                'avg_return': 平均收益,
                'median_return': 中位收益,
                'n_signals': 总信号数,
                'n_days_1_5': 选股1-5只的天数,
                'filters': filters,
            }
        """
        daily_counts = []
        all_returns = []

        for i, td in enumerate(self.trade_dates):
            selected = self.run_single_day(td, filters)
            daily_counts.append(len(selected))

            if selected:
                rets = self.evaluate_signals(selected, td, hold_days)
                all_returns.extend(rets.values())

            if verbose and (i % 20 == 0 or i == len(self.trade_dates) - 1):
                print(f"  [{i+1}/{len(self.trade_dates)}] {td}: 选中 {len(selected)} 只")

        # 统计
        all_returns_arr = np.array(all_returns) if all_returns else np.array([0])
        win_rate = (all_returns_arr > 0).mean() * 100 if len(all_returns) > 0 else 0
        avg_ret = all_returns_arr.mean() if len(all_returns) > 0 else 0
        med_ret = np.median(all_returns_arr) if len(all_returns) > 0 else 0

        daily_counts_arr = np.array(daily_counts)
        n_days_1_5 = int(((daily_counts_arr >= 1) & (daily_counts_arr <= 5)).sum())

        return {
            "daily_counts": daily_counts,
            "all_returns": all_returns,
            "win_rate": round(win_rate, 1),
            "avg_return": round(avg_ret, 2),
            "median_return": round(med_ret, 2),
            "n_signals": len(all_returns),
            "n_days_1_5": n_days_1_5,
            "n_total_days": len(self.trade_dates),
            "filters": filters,
        }


# =========================================================
# 参数搜索
# =========================================================
def search_best_filters(backtester: StrategyBacktester,
                        hold_days: int = 5,
                        target_range: Tuple[int, int] = (1, 5)) -> List[Dict]:
    """遍历参数组合, 找到每天选股 1-5 只且胜率最高的配置

    Returns:
        按胜率降序排列的结果列表
    """
    # 参数网格 (从宽松到收紧)
    param_grid = {
        "ztts_min":       [3, 5, 8],
        "ztts_max":       [90, 60, 40],
        "amp_ztts_max":   [1.3, 1.2, 1.15],
        "dist_high_max":  [1.2, 1.1, 1.05],
        "ret_5d_max":     [0.3, 0.2, 0.15],
        "high_ratio_min": [0.8, 0.85, 0.9],
    }

    # 生成参数组合 (全组合太多, 用重要参数优先)
    # 先固定其他参数, 只调 ztts + amp_ztts + dist_high
    combos = []
    for ztts_min, ztts_max, amp_ztts, dist_high in product(
        param_grid["ztts_min"],
        param_grid["ztts_max"],
        param_grid["amp_ztts_max"],
        param_grid["dist_high_max"],
    ):
        if ztts_min >= ztts_max:
            continue
        combos.append(Filters(
            ztts_min=ztts_min, ztts_max=ztts_max,
            amp_ztts_max=amp_ztts, dist_high_max=dist_high,
        ))

    print(f"\n[Search] 共 {len(combos)} 个参数组合, 每个约 {len(backtester.trade_dates)} 个交易日")
    print("=" * 80)

    results = []
    for i, flt in enumerate(combos):
        t0 = time.time()
        res = backtester.run_backtest(flt, hold_days=hold_days, verbose=False)
        elapsed = time.time() - t0

        # 只保留选股数在目标范围内的
        daily_arr = np.array(res["daily_counts"])
        avg_daily = daily_arr.mean()
        pct_in_range = res["n_days_1_5"] / res["n_total_days"] * 100

        results.append({
            **res,
            "avg_daily_count": round(avg_daily, 2),
            "pct_days_in_range": round(pct_in_range, 1),
            "label": flt.label(),
        })

        print(f"[{i+1}/{len(combos)}] {flt.label()} | "
              f"日均{avg_daily:.1f}只 范围内{pct_in_range:.0f}% | "
              f"信号{res['n_signals']} 胜率{res['win_rate']}% 均收益{res['avg_return']}% | "
              f"{elapsed:.1f}s")

    # 排序: 优先选股数在1-5范围内的天数多, 其次胜率高
    results.sort(key=lambda x: (x["pct_days_in_range"], x["win_rate"]), reverse=True)
    return results


# =========================================================
# 主入口
# =========================================================
def main():
    import argparse

    parser = argparse.ArgumentParser(description="回测 strategy 选股效果")
    parser.add_argument("--start", type=str, default="20250101", help="回测起始日")
    parser.add_argument("--end", type=str, default=None, help="回测结束日")
    parser.add_argument("--max-stocks", type=int, default=None,
                        help="最多加载多少只 (调试用)")
    parser.add_argument("--hold", type=int, default=5, help="持有天数")
    parser.add_argument("--search", action="store_true", help="搜索最佳参数组合")
    parser.add_argument("--default", action="store_true",
                        help="只用默认参数运行一次")
    args = parser.parse_args()

    print("=" * 80)
    print("  Strategy 选股回测")
    print("=" * 80)

    bt = StrategyBacktester(
        start_date=args.start,
        end_date=args.end,
        max_stocks=args.max_stocks,
    )

    if args.search:
        results = search_best_filters(bt, hold_days=args.hold)

        print("\n" + "=" * 80)
        print("  参数搜索结果 TOP 10 (按 范围内天数% + 胜率 排序)")
        print("=" * 80)
        print(f"{'排名':<4} {'参数':<55} {'日均':<6} {'范围内%':<8} {'信号':<6} {'胜率%':<7} {'均收益%':<8}")
        print("-" * 100)
        for i, r in enumerate(results[:10]):
            print(f"{i+1:<4} {r['label']:<55} {r['avg_daily_count']:<6.1f} "
                  f"{r['pct_days_in_range']:<8.1f} {r['n_signals']:<6d} "
                  f"{r['win_rate']:<7.1f} {r['avg_return']:<8.2f}")

        # 保存全部结果
        out_csv = os.path.join(os.path.dirname(__file__), "strategy_backtest_results.csv")
        pd.DataFrame(results).to_csv(out_csv, index=False, encoding="utf-8-sig")
        print(f"\n[结果已保存] {out_csv}")

    elif args.default:
        flt = Filters()
        print(f"\n默认参数: {flt.label()}")
        res = bt.run_backtest(flt, hold_days=args.hold, verbose=True)

        print("\n" + "=" * 60)
        print("  默认参数回测结果")
        print("=" * 60)
        print(f"  交易日数:     {res['n_total_days']}")
        print(f"  总信号数:     {res['n_signals']}")
        print(f"  胜率:         {res['win_rate']}%")
        print(f"  平均收益:     {res['avg_return']}%")
        print(f"  中位收益:     {res['median_return']}%")
        print(f"  日均选股数:   {np.mean(res['daily_counts']):.1f}")
        print(f"  选股1-5只天数: {res['n_days_1_5']}/{res['n_total_days']} "
              f"({res['n_days_1_5']/res['n_total_days']*100:.1f}%)")

        # 每日选股数分布
        arr = np.array(res["daily_counts"])
        print(f"\n  每日选股数分布:")
        print(f"    0只: {(arr==0).sum()} 天")
        print(f"    1-5只: {((arr>=1)&(arr<=5)).sum()} 天")
        print(f"    6-10只: {((arr>=6)&(arr<=10)).sum()} 天")
        print(f"    10+只: {(arr>10).sum()} 天")
        print(f"    最多: {arr.max()} 只")


if __name__ == "__main__":
    main()
