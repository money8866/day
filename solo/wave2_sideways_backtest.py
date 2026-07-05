# -*- coding: utf-8 -*-
"""
Wave2 强势横盘算法 - 二波低吸回测 (向量化加速版)

算法来源: d:/mystock/solo/multi_factor_picker/wave2_daily.py
接入框架: tdx_backtest

形态定义:
  - 主板股票 (沪深 60/00 开头, 排除双创 688/689)
  - 近 SURGE_DAYS=20 天内存在一波拉升 >=20%
  - wave1 高点之后: 回调 <10% 且调整天数 <=15 天
  - 最小回调 >=5%, 调整期最长 60 天

触发条件 (优先级 1 最优):
  P1: RSI6<50 + 缩量 (5日均量 / wave1前20日均量 <0.8x)
  P2: MACD 金叉 (DIF>DEA) + 站上 MA20

回测模式: T+1 开盘买入, 持有 N 天收盘卖出

性能优化:
  - 每只股票只调用一次 detect_signals_vectorized, 一次性算出全部交易日信号
  - 用 numpy 向量化算 MA/RSI/MACD, 跳过 KDJ/BOLL/OBV
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
# 算法常量 (优化版 - 基于全主板1082笔回测的分档分析)
# =========================================================
SURGE_DAYS = 20        # 一波拉升窗口
SURGE_MIN = 0.20       # 一波最低涨幅 20%
# 优化1: 回调区间从 [5%, 10%) 收紧到 [7%, 9%)
#   原因: 全主板回测显示 回调7-9% 胜率55.6%/均收益3.81%, 而 9-10% 仅42.8%/0.17%
PULLBACK_MIN = 0.07    # 最小回调 7% (黄金区间, 不放宽)
PULLBACK_MAX = 0.09    # 强势横盘: 回调 <9% (黄金区间, 不放宽)
PULLBACK_DAYS_MAX = 20 # 强势横盘: 调整天数 <=20 (v9放宽 15→20)
ADJUST_MAX = 60        # 调整期最长 60 天
# 优化2: 量比从 单边<0.8 改为 区间[0.7, 0.8]
#   原因: 全主板回测显示 量比0.7-0.8 胜率53.2%/均收益1.02%,
#         量比0.5-0.7 胜率41.5%/-1.50%, 量比<0.5 胜率38.6%/-1.09% (过度缩量=无人气)
VOL_SHRINK_RATIO_MIN = 0.7  # 量比下限 (新增, 避免过度缩量)
VOL_SHRINK_RATIO_MAX = 0.8  # 量比上限 (原 VOL_SHRINK_RATIO)
# 优化3: RSI上限从50收紧到40
#   原因: 优化版回测显示 RSI0-30 胜率60%/均收益2.44%, RSI30-40 胜率52.4%/均收益1.88%,
#         RSI40-50 胜率仅44.4%/均收益1.13% (拉低整体)
RSI_MAX = 40           # RSI 上限 (原 50, 优化后收紧到 40)
# 优化4: P2 (MACD金叉+MA20上方) 收紧条件
#   原因: 优化v2回测 P2 笔数919过多(占91%), 胜率仅48.9%, 稀释Alpha
#         - 加 RSI<55 过滤: 避开 RSI>55 的过热区 (略放宽 50→55)
#         - 距MA20距离限制 [0%, 15%]: 避免远离MA20的高位信号 (略放宽 10→15)
#         - 近5日内MACD金叉: 要求是新近金叉而非任何时刻DIF>DEA状态 (略放宽 3→5)
#   v3回测教训: 3日/10%/RSI<50 组合过滤太狠, P2仅1笔样本
P2_RSI_MAX = 55        # P2 的 RSI 上限 (略放宽)
P2_MA20_DIST_MIN = 0.0 # P2 距MA20最小距离 (0%=刚站上)
P2_MA20_DIST_MAX = 0.15 # P2 距MA20最大距离 (15%, 略放宽)
P2_MACD_CROSS_DAYS = 5  # P2 要求近 N 日内 MACD 金叉 (略放宽)

# 优化5: 加入均线位置硬条件 + MA5/MA10 位置分析
#   - 硬条件: 当前价必须站上 MA20 (对 P1/P2 都强制)
#     原因: v3回测 P1 在 MA20 下方有大量假信号, 整体胜率被拉低
#   - 分析维度: MA5 vs MA10 vs MA20 的相对位置
#     * 多头排列 (MA5>MA10>MA20): 强趋势, 顺势
#     * MA5>MA10 但 MA10<MA20: 短期反弹但中期弱
#     * MA5<MA10 但 MA10>MA20: 短期回调但中期强 (低吸精髓)
#     * MA5<MA10<MA20: 空头排列 (已被硬条件过滤)
#   - MA5 上穿 MA10 (短期金叉): 加分项
REQUIRE_ABOVE_MA20 = True  # 硬条件: 当前价必须站上 MA20

# 优化6: P3 触发 - 短期回调形态 (低吸精髓)
#   形态: MA5 < MA10 且 MA10 > MA20 且 当前价 > MA20
#   含义: 中期趋势向上(MA10>MA20), 短期回调(MA5<MA10), 当前价仍站上MA20
#   v4回测: "短期回调"形态胜率100% (4笔), 均收益3.12%
#   v5回测教训: 纯P3无过滤得到354笔, 胜率仅41.2%, 拉低整体
#   v6: 加 RSI<55 + 量比<1.0 + 距MA20[2%, 10%], 67笔胜率41.8%
#   v7: 进一步收紧 RSI<50 + 量比[0.6, 0.9]
#       原因: v6分档显示 RSI30-40 胜率85.7%, 量比0.7-0.8 胜率56.5%
P3_ENABLE = True            # 是否启用 P3
P3_MA20_DIST_MIN = 0.02     # P3 距MA20最小距离 2% (避开刚突破的假信号区)
P3_MA20_DIST_MAX = 0.10     # P3 距MA20最大距离 10% (低吸, 不能离MA20太远)
P3_RSI_MAX = 50             # P3 的 RSI 上限 (收紧 55→50, 避开过热)
P3_VOL_RATIO_MIN = 0.6      # P3 量比下限 (避免过度缩量无人气)
P3_VOL_RATIO_MAX = 0.9      # P3 量比上限 (要求缩量或平量)

# 优化8 (路径A): 统一严格交集模式
#   不分 P1/P2/P3, 用单一条件: 各维度高质量区间取交集
#   v8a (RSI<45 + 量比[0.7,0.8) + 距MA20[2,8%]): 6笔, 胜率50%, 均收益1.74%
#     - 短期回调形态4笔均收益7.98%, 多头排列2笔均收益-10.74%
#     - 进一步证实: 短期回调 (MA5<MA10) 才是低吸精髓
#   v8b (RSI<50 + 量比[0.65, 0.85) + MA5<MA10): 21笔, 胜率52.4%, 均收益5.35%, 中位3.97% ✅ 基础方案
#   v8c 量比下限放宽到 0.5: 27笔, 胜率48.1%, 均收益4.60%, 中位-0.57% ❌ 失败
#     - 教训: 极度缩量(量比<0.65)整体是反信号, v8b的4笔大牛股是偶然分布
#   v9 (2026市场优化): 大盘趋势过滤 + 放宽形态参数
#     - 2026年分析: 沪深300站上MA20仅48.7%, 典型震荡市
#     - 1月大盘弱势期2笔全亏, 2/4/5月大盘强势期7笔胜率100%
#     - 优化: 加大盘趋势过滤(沪深300站上MA20) + 放宽回调[5,10%]/调整<=20/距MA20[2,10%]
STRICT_INTERSECTION_ENABLE = True  # 启用严格交集模式 (覆盖 P1/P2/P3)
STRICT_ONLY = True                # True=只用 STRICT, 禁用 P1/P2/P3
STRICT_MA20_DIST_MIN = 0.02  # 距MA20最小 2%
STRICT_MA20_DIST_MAX = 0.08  # 距MA20最大 8% (黄金区间, 不放宽)
STRICT_RSI_MAX = 50          # RSI 上限 50 (v8b 最佳值)
STRICT_VOL_RATIO_MIN = 0.65  # 量比下限 0.65 (v8b 最佳值)
STRICT_VOL_RATIO_MAX = 0.85  # 量比上限 0.85 (v8b 最佳值)
STRICT_REQUIRE_MA10_ABOVE_MA20 = True  # 强制 MA10 > MA20
# 新增: 强制 MA5 < MA10 (短期回调形态, 真正低吸精髓)
STRICT_REQUIRE_MA5_BELOW_MA10 = True

# 优化9: 大盘趋势过滤 (2026震荡市优化) — 已禁用
#   测试结果: 大盘过滤后信号从21降到15, 胜率从52.4%降到46.7%
#   教训: 低吸本身就是逆势买回调, 大盘弱势时个股回调反而是低吸机会
#         大盘过滤方向错了, 不适用于低吸策略
MARKET_FILTER_ENABLE = False             # 禁用大盘趋势过滤
MARKET_INDEX_CODE = "399300.SZ"          # 沪深300指数
MARKET_MA_PERIOD = 20                    # 大盘MA20


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

def is_tradeable(ts_code: str, include_chuangchuang: bool = True) -> bool:
    """可交易股票判定 (主板 + 可选双创, 排除北交所/指数)"""
    if not ts_code or "." not in ts_code:
        return False
    sym = ts_code.split(".")[0]
    # 排除北交所 (8/4开头) 和指数 (999)
    if sym.startswith(("999", "8", "4")):
        return False
    if include_chuangchuang:
        # 主板 + 创业板(300/301) + 科创板(688/689)
        if sym.startswith(("60", "00", "3", "688", "689")):
            return True
        return False
    else:
        return is_main_board(ts_code)

# v9: 是否纳入双创 (用户偏好双创股票)
INCLUDE_CHUANGCHUANG = True


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
    """一次性为整只股票的全部交易日计算强势横盘信号"""
    n = len(df)
    signals = np.zeros(n, dtype=bool)
    infos: List[Dict] = [{} for _ in range(n)]
    if n < 80:
        return signals, infos

    C = df["close"].values
    H = df["high"].values
    VOL = df["vol"].values

    # 预计算指标
    ma5 = _rolling_mean_np(C, 5)
    ma10 = _rolling_mean_np(C, 10)
    ma20 = _rolling_mean_np(C, 20)
    rsi6 = RSI(df["close"], 6).values
    dif, dea, _ = MACD(df["close"])
    dif = dif.values
    dea = dea.values

    for i in range(80, n):
        current_close = C[i]

        search_start = max(SURGE_DAYS, i - ADJUST_MAX)
        triggered = False
        trigger_info = {}

        for end_idx in range(i, search_start, -1):
            window_start = end_idx - SURGE_DAYS
            if window_start < 0:
                break

            window_closes = C[window_start: end_idx + 1]
            low_idx_in_window = int(np.argmin(window_closes))
            high_idx_in_window = int(np.argmax(window_closes))

            if high_idx_in_window <= low_idx_in_window:
                continue
            if (high_idx_in_window - low_idx_in_window) > SURGE_DAYS - 2:
                continue

            wave1_gain = (window_closes[high_idx_in_window]
                          - window_closes[low_idx_in_window]) / window_closes[low_idx_in_window]
            if wave1_gain < SURGE_MIN:
                continue

            wave1_high_idx = window_start + high_idx_in_window
            wave1_high_price = C[wave1_high_idx]

            if wave1_high_idx >= i:
                continue
            post_closes = C[wave1_high_idx: i + 1]
            if len(post_closes) < 2:
                continue

            post_high = post_closes[0]
            post_low = post_closes.min()
            pullback_max = (post_high - post_low) / post_high if post_high > 0 else 0
            pullback_days = len(post_closes) - 1

            if not (pullback_max < PULLBACK_MAX and pullback_days <= PULLBACK_DAYS_MAX):
                continue

            pullback_now = (post_high - current_close) / post_high
            if pullback_now < PULLBACK_MIN:
                continue

            # 基准量: wave1 高点前 20 日均量
            if wave1_high_idx >= 20:
                base_vol = VOL[wave1_high_idx - 20: wave1_high_idx].mean()
            else:
                base_vol = VOL[:wave1_high_idx + 1].mean() if wave1_high_idx > 0 else 1.0
            if i >= 4:
                recent_vol_5d = VOL[i - 4: i + 1].mean()
            else:
                recent_vol_5d = VOL[: i + 1].mean()
            vol_ratio = recent_vol_5d / base_vol if base_vol and base_vol > 0 else 1.0

            rsi_now = rsi6[i] if not np.isnan(rsi6[i]) else 50.0
            macd_dif_now = dif[i]
            macd_dea_now = dea[i]
            macd_crossed = (macd_dif_now > macd_dea_now) if (not np.isnan(macd_dif_now) and not np.isnan(macd_dea_now)) else False
            ma5_now = ma5[i]
            ma10_now = ma10[i]
            ma20_now = ma20[i]
            above_ma20 = (current_close > ma20_now) if not np.isnan(ma20_now) else False

            # 优化5 硬条件: 必须站上 MA20 (对 P1/P2 都强制)
            if REQUIRE_ABOVE_MA20 and not above_ma20:
                continue

            # MA5/MA10 位置分析
            ma5_above_ma10 = (ma5_now > ma10_now) if (not np.isnan(ma5_now) and not np.isnan(ma10_now)) else False
            ma10_above_ma20 = (ma10_now > ma20_now) if (not np.isnan(ma10_now) and not np.isnan(ma20_now)) else False

            # 均线位置类型
            if ma5_above_ma10 and ma10_above_ma20:
                ma_pattern = "多头排列"  # MA5>MA10>MA20
            elif ma5_above_ma10 and not ma10_above_ma20:
                ma_pattern = "短期反弹"  # MA5>MA10 但 MA10<MA20
            elif not ma5_above_ma10 and ma10_above_ma20:
                ma_pattern = "短期回调"  # MA5<MA10 但 MA10>MA20
            else:
                ma_pattern = "空头排列"  # MA5<MA10<MA20 (理论上已被MA20硬条件过滤)

            # MA5 上穿 MA10 (近3日内)
            ma5_cross_ma10_recent = False
            if i >= 3 and not np.isnan(ma5_now) and not np.isnan(ma10_now):
                if ma5_above_ma10:
                    past_ma5 = ma5[i - 3: i]
                    past_ma10 = ma10[i - 3: i]
                    valid = ~(np.isnan(past_ma5) | np.isnan(past_ma10))
                    if valid.any():
                        has_below = (past_ma5[valid] <= past_ma10[valid]).any()
                        ma5_cross_ma10_recent = has_below

            # STRICT_ONLY 模式下, P1/P2/P3 全部禁用
            _p1_enabled = not (STRICT_INTERSECTION_ENABLE and STRICT_ONLY)
            _p2_enabled = not (STRICT_INTERSECTION_ENABLE and STRICT_ONLY)
            _p3_enabled = not (STRICT_INTERSECTION_ENABLE and STRICT_ONLY)

            # 触发条件 P1: RSI<40 + 缩量在[0.7, 0.8]区间 (避免过度缩量)
            trigger_p1 = (_p1_enabled
                          and (rsi_now < RSI_MAX) and (VOL_SHRINK_RATIO_MIN <= vol_ratio < VOL_SHRINK_RATIO_MAX))

            # 触发条件 P2 (优化版): 近5日内MACD金叉 + MA20上方 + 距MA20距离[0,15%] + RSI<55
            # 检查近 P2_MACD_CROSS_DAYS 日内是否出现 MACD 金叉 (DIF 从下穿上)
            macd_recent_cross = False
            if _p2_enabled and i >= P2_MACD_CROSS_DAYS and not np.isnan(dif[i]) and not np.isnan(dea[i]):
                # 当前 DIF>DEA, 且 (P2_MACD_CROSS_DAYS 天前 DIF<=DEA 或 中间有穿越)
                # 简化: 当前 DIF>DEA, 且在过去 N 天内至少有一天 DIF<=DEA
                past_dif = dif[i - P2_MACD_CROSS_DAYS: i]
                past_dea = dea[i - P2_MACD_CROSS_DAYS: i]
                # 过滤 NaN
                valid = ~(np.isnan(past_dif) | np.isnan(past_dea))
                if valid.any():
                    has_below = (past_dif[valid] <= past_dea[valid]).any()
                    macd_recent_cross = macd_crossed and has_below
                else:
                    macd_recent_cross = False

            # 距 MA20 距离
            ma20_dist = (current_close - ma20_now) / ma20_now if (not np.isnan(ma20_now) and ma20_now > 0) else 0.0
            ma20_in_range = (P2_MA20_DIST_MIN <= ma20_dist <= P2_MA20_DIST_MAX)

            trigger_p2 = (_p2_enabled
                          and macd_recent_cross and above_ma20
                          and ma20_in_range and (rsi_now < P2_RSI_MAX))

            # 触发条件 P3 (新增): 短期回调形态 + 过滤条件
            #   形态: MA5<MA10 且 MA10>MA20 且 站上MA20
            #   过滤: 距MA20[2%, 10%] + RSI<50 + 量比[0.6, 0.9]
            trigger_p3 = (_p3_enabled
                          and not ma5_above_ma10      # MA5 < MA10
                          and ma10_above_ma20          # MA10 > MA20
                          and above_ma20               # 当前价 > MA20
                          and (P3_MA20_DIST_MIN <= ma20_dist <= P3_MA20_DIST_MAX)  # 距MA20 [2%, 10%]
                          and (rsi_now < P3_RSI_MAX)   # RSI < 50
                          and (P3_VOL_RATIO_MIN <= vol_ratio < P3_VOL_RATIO_MAX))  # 量比 [0.6, 0.9]

            # 触发条件 STRICT (路径A): 严格交集
            #   条件: 站上MA20 + 距MA20[2%, 8%] + RSI<50 + 量比[0.65, 0.85) + MA10>MA20 + MA5<MA10
            trigger_strict = (STRICT_INTERSECTION_ENABLE
                              and above_ma20
                              and (STRICT_MA20_DIST_MIN <= ma20_dist <= STRICT_MA20_DIST_MAX)
                              and (rsi_now < STRICT_RSI_MAX)
                              and (STRICT_VOL_RATIO_MIN <= vol_ratio < STRICT_VOL_RATIO_MAX)
                              and (not STRICT_REQUIRE_MA10_ABOVE_MA20 or ma10_above_ma20)
                              and (not STRICT_REQUIRE_MA5_BELOW_MA10 or not ma5_above_ma10))

            if trigger_strict or trigger_p1 or trigger_p2 or trigger_p3:
                triggered = True
                # 优先级: STRICT > P1 > P3 > P2
                if trigger_strict:
                    trig_label = "STRICT_严格交集"
                elif trigger_p1:
                    trig_label = "P1_RSI_缩量"
                elif trigger_p3:
                    trig_label = "P3_短期回调"
                else:
                    trig_label = "P2_MACD_MA20"
                trigger_info = {
                    "wave1_gain_pct": round(wave1_gain * 100, 1),
                    "pullback_max_pct": round(pullback_max * 100, 1),
                    "pullback_now_pct": round(pullback_now * 100, 1),
                    "pullback_days": pullback_days,
                    "rsi_now": round(rsi_now, 1),
                    "vol_ratio": round(vol_ratio, 2),
                    "macd_crossed": macd_crossed,
                    "above_ma20": above_ma20,
                    "ma20_dist_pct": round(ma20_dist * 100, 1),
                    "ma_pattern": ma_pattern,
                    "ma5_above_ma10": ma5_above_ma10,
                    "ma10_above_ma20": ma10_above_ma20,
                    "ma5_cross_ma10_recent": ma5_cross_ma10_recent,
                    "trigger": trig_label,
                    "wave1_high": round(wave1_high_price, 2),
                    "current_price": round(current_close, 2),
                }
                break

        if triggered:
            signals[i] = True
            infos[i] = trigger_info

    return signals, infos


# =========================================================
# 回测引擎
# =========================================================
class Wave2SidewaysBacktester:
    """Wave2 强势横盘回测 (向量化版)"""

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
        # 指定股票池 (None=全主板)
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
        pool_desc = f"{len(self.pool_codes)}只指定股池" if self.pool_codes else "全主板"
        print(f"[Backtest] 区间: {self.start_date} ~ {self.end_date}, "
              f"交易日: {len(self.trade_dates)}, 股池: {pool_desc}", flush=True)

        # 优化9: 预加载大盘趋势 (沪深300 + MA20)
        self.market_above_ma: Dict[str, bool] = {}
        if MARKET_FILTER_ENABLE:
            from data_loader import load_kline as _tdx_load
            from datetime import timedelta
            mk_dt = datetime.strptime(self.start_date, "%Y%m%d")
            mk_start = (mk_dt - timedelta(days=60)).strftime("%Y%m%d")
            mk_df = _tdx_load(MARKET_INDEX_CODE, start_date=mk_start)
            if not mk_df.empty:
                mk_df["ma"] = mk_df["close"].rolling(MARKET_MA_PERIOD).mean()
                mk_df["above"] = mk_df["close"] > mk_df["ma"]
                for _, r in mk_df.iterrows():
                    self.market_above_ma[str(r["trade_date"])] = bool(r["above"])
                above_n = sum(self.market_above_ma.values())
                print(f"[Market] 沪深300站上MA20: {above_n}/{len(self.market_above_ma)} 天", flush=True)
            else:
                print(f"[Market] 沪深300数据加载失败, 大盘过滤禁用", flush=True)

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
            if not is_tradeable(ts_code, INCLUDE_CHUANGCHUANG):
                continue
            # 股池过滤
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
        print(f"[Load] 主板加载 {n_ok} 只 (含信号 {n_with_signal} 只), "
              f"跳过 {n_skip}, 耗时 {elapsed:.1f}s", flush=True)

    def run_single_day(self, trade_date: str) -> List[Tuple[str, Dict]]:
        # 优化9: 大盘趋势过滤 (沪深300必须站上MA20)
        if MARKET_FILTER_ENABLE and self.market_above_ma:
            if not self.market_above_ma.get(trade_date, False):
                return []  # 大盘弱势, 当日不出信号
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
                "pullback_pct": info.get("pullback_now_pct", 0),
                "pullback_days": info.get("pullback_days", 0),
                "rsi_now": info.get("rsi_now", 0),
                "vol_ratio": info.get("vol_ratio", 0),
                "ma20_dist_pct": info.get("ma20_dist_pct", 0),
                "ma_pattern": info.get("ma_pattern", ""),
                "ma5_above_ma10": info.get("ma5_above_ma10", False),
                "ma10_above_ma20": info.get("ma10_above_ma20", False),
                "ma5_cross_ma10_recent": info.get("ma5_cross_ma10_recent", False),
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
    """从 bull_stocks_qualified.csv 或 fallback JSON 加载股票池

    优先级:
      1. pool_path (bull_stocks_qualified.csv) — 含 ts_code 列 (无后缀也支持)
      2. fallback: mainboard_v4_scan_20260618.json — report_daily 下最新扫描结果
      3. 都失败: 返回 None (回退到全主板)
    """
    import json

    # 1. 优先 csv
    if os.path.exists(pool_path):
        try:
            df = pd.read_csv(pool_path)
            # 兼容多种列名: ts_code / code / 股票代码
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
                    # 补齐后缀
                    if "." not in v:
                        # 6开头=SH, 其他=SZ
                        v = f"{v}.SH" if v.startswith("6") else f"{v}.SZ"
                    codes.append(v)
                if codes:
                    print(f"[Pool] 从 {os.path.basename(pool_path)} 加载 {len(codes)} 只股票",
                          flush=True)
                    return codes
        except Exception as e:
            print(f"[Pool] CSV加载失败: {e}", flush=True)

    # 2. fallback: report_daily 下的 mainboard_v4_scan_*.json
    report_dir = os.path.dirname(pool_path)
    if os.path.isdir(report_dir):
        json_files = sorted(
            [f for f in os.listdir(report_dir)
             if f.startswith("mainboard_v4_scan") and f.endswith(".json")],
            reverse=True
        )
        if json_files:
            jpath = os.path.join(report_dir, json_files[0])
            try:
                with open(jpath, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                data_list = obj.get("data", []) if isinstance(obj, dict) else obj
                codes = [d["ts_code"] for d in data_list if d.get("ts_code")]
                if codes:
                    print(f"[Pool] CSV不存在, fallback 到 {json_files[0]}: "
                          f"{len(codes)} 只股票", flush=True)
                    return codes
            except Exception as e:
                print(f"[Pool] JSON fallback失败: {e}", flush=True)

    # 3. 都失败
    print(f"[Pool] 未找到股池文件, 回退到全主板", flush=True)
    return None


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Wave2 强势横盘算法回测 (向量化, 优化版)")
    parser.add_argument("--start", type=str, default="20250101")
    parser.add_argument("--end", type=str, default=None)
    parser.add_argument("--max-stocks", type=int, default=None)
    parser.add_argument("--hold", type=int, default=5)
    parser.add_argument("--top-n", type=int, default=None)
    parser.add_argument("--out", type=str, default=None)
    parser.add_argument("--pool", type=str,
                        default=r"d:\mystock\solo\report_daily\bull_stocks_qualified.csv",
                        help="股票池 CSV (含 ts_code/code 列), 不存在时 fallback 到 JSON")
    args = parser.parse_args()

    # 加载股票池
    pool_codes = _load_pool_codes(args.pool)

    print("=" * 80)
    print("  Wave2 强势横盘算法回测 (主板+双创, T+1 开盘买入, 向量化, 优化版)")
    print("=" * 80)
    print(f"  算法参数 (优化版):")
    print(f"    一波拉升窗口: {SURGE_DAYS} 天, 最低涨幅: {SURGE_MIN*100:.0f}%")
    print(f"    强势横盘: 回调 [{PULLBACK_MIN*100:.0f}%, {PULLBACK_MAX*100:.0f}%), "
          f"调整天数 <={PULLBACK_DAYS_MAX}")
    print(f"    调整期上限: {ADJUST_MAX} 天")
    print(f"    触发 P1: RSI6<{RSI_MAX} + 缩量在[{VOL_SHRINK_RATIO_MIN}, {VOL_SHRINK_RATIO_MAX})区间"
          + (" + 必须站上MA20" if REQUIRE_ABOVE_MA20 else ""))
    print(f"    触发 P2 (优化): 近{P2_MACD_CROSS_DAYS}日MACD金叉 + MA20上方 + "
          f"距MA20[{P2_MA20_DIST_MIN*100:.0f}%, {P2_MA20_DIST_MAX*100:.0f}%] + RSI<{P2_RSI_MAX}")
    if P3_ENABLE:
        print(f"    触发 P3 (新增): 短期回调形态 (MA5<MA10 且 MA10>MA20 且 站上MA20) + "
              f"距MA20[{P3_MA20_DIST_MIN*100:.0f}%, {P3_MA20_DIST_MAX*100:.0f}%] + "
              f"RSI<{P3_RSI_MAX} + 量比[{P3_VOL_RATIO_MIN}, {P3_VOL_RATIO_MAX})")
    if STRICT_INTERSECTION_ENABLE:
        only_flag = " (仅STRICT, 禁用P1/P2/P3)" if STRICT_ONLY else ""
        ma5_flag = " + MA5<MA10" if STRICT_REQUIRE_MA5_BELOW_MA10 else ""
        print(f"    触发 STRICT (路径A){only_flag}: 站上MA20 + 距MA20[{STRICT_MA20_DIST_MIN*100:.0f}%, {STRICT_MA20_DIST_MAX*100:.0f}%] + "
              f"RSI<{STRICT_RSI_MAX} + 量比[{STRICT_VOL_RATIO_MIN}, {STRICT_VOL_RATIO_MAX})"
              + (" + MA10>MA20" if STRICT_REQUIRE_MA10_ABOVE_MA20 else "") + ma5_flag)
    if MARKET_FILTER_ENABLE:
        print(f"    大盘过滤: {MARKET_INDEX_CODE} 站上 MA{MARKET_MA_PERIOD} (弱势期不出信号)")
    print(f"  股池文件: {args.pool}")
    print("=" * 80, flush=True)

    bt = Wave2SidewaysBacktester(
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

    if res.get("trade_records"):
        print("\n  触发条件胜率对比:")
        recs = res["trade_records"]
        for trig in ["STRICT_严格交集", "P1_RSI_缩量", "P2_MACD_MA20", "P3_短期回调"]:
            sub = [r["return"] for r in recs if r["trigger"] == trig]
            if sub:
                wr = sum(1 for x in sub if x > 0) / len(sub) * 100
                avg = np.mean(sub)
                print(f"    {trig}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:.2f}%")

        print("\n  回调深度分档胜率:")
        for lo, hi in [(5, 7), (7, 9), (9, 10)]:
            sub = [r["return"] for r in recs if lo <= r["pullback_pct"] < hi]
            if sub:
                wr = sum(1 for x in sub if x > 0) / len(sub) * 100
                avg = np.mean(sub)
                print(f"    回调{lo}-{hi}%: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:.2f}%")

        print("\n  RSI 分档胜率:")
        for lo, hi in [(0, 30), (30, 40), (40, 50)]:
            sub = [r["return"] for r in recs if lo <= r["rsi_now"] < hi]
            if sub:
                wr = sum(1 for x in sub if x > 0) / len(sub) * 100
                avg = np.mean(sub)
                print(f"    RSI{lo}-{hi}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:.2f}%")

        print("\n  量比分档胜率:")
        for lo, hi in [(0, 0.5), (0.5, 0.7), (0.7, 0.8)]:
            sub = [r["return"] for r in recs if lo <= r["vol_ratio"] < hi]
            if sub:
                wr = sum(1 for x in sub if x > 0) / len(sub) * 100
                avg = np.mean(sub)
                print(f"    量比{lo}-{hi}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:.2f}%")

        # MA20 距离分档 (仅 P2 触发)
        print("\n  P2 距MA20距离分档胜率:")
        p2_recs = [r for r in recs if r["trigger"] == "P2_MACD_MA20"]
        if p2_recs:
            for lo, hi in [(0, 2), (2, 5), (5, 10)]:
                sub = [r["return"] for r in p2_recs
                       if "ma20_dist_pct" in r and lo <= r.get("ma20_dist_pct", 0) < hi]
                if sub:
                    wr = sum(1 for x in sub if x > 0) / len(sub) * 100
                    avg = np.mean(sub)
                    print(f"    距MA20 {lo}-{hi}%: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:.2f}%")

        # MA5/MA10/MA20 均线位置分档
        print("\n  均线位置分档胜率 (MA5/MA10/MA20):")
        for pat in ["多头排列", "短期反弹", "短期回调", "空头排列"]:
            sub = [r["return"] for r in recs if r.get("ma_pattern") == pat]
            if sub:
                wr = sum(1 for x in sub if x > 0) / len(sub) * 100
                avg = np.mean(sub)
                print(f"    {pat}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:.2f}%")

        # MA5 上穿 MA10 (近3日内) 对比
        print("\n  MA5近3日上穿MA10 对比:")
        for label, val in [("是-近3日金叉", True), ("否-未金叉", False)]:
            sub = [r["return"] for r in recs if r.get("ma5_cross_ma10_recent") == val]
            if sub:
                wr = sum(1 for x in sub if x > 0) / len(sub) * 100
                avg = np.mean(sub)
                print(f"    {label}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:.2f}%")

        # MA5 vs MA10 单独对比
        print("\n  MA5 vs MA10 位置对比:")
        for label, cond in [("MA5>MA10", True), ("MA5<MA10", False)]:
            sub = [r["return"] for r in recs if r.get("ma5_above_ma10") == cond]
            if sub:
                wr = sum(1 for x in sub if x > 0) / len(sub) * 100
                avg = np.mean(sub)
                print(f"    {label}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:.2f}%")

        # MA10 vs MA20 单独对比
        print("\n  MA10 vs MA20 位置对比:")
        for label, cond in [("MA10>MA20", True), ("MA10<MA20", False)]:
            sub = [r["return"] for r in recs if r.get("ma10_above_ma20") == cond]
            if sub:
                wr = sum(1 for x in sub if x > 0) / len(sub) * 100
                avg = np.mean(sub)
                print(f"    {label}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:.2f}%")

        out_path = args.out or os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "tdx_backtest_wave2_sideways_trades.csv")
        pd.DataFrame(res["trade_records"]).to_csv(
            out_path, index=False, encoding="utf-8-sig")
        print(f"\n  [交易记录已保存] {out_path}")


if __name__ == "__main__":
    main()
