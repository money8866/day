# ENTRY_NEXT_OPEN_PATCHED 买入价=次日开盘(回测+1.76%优于收盘+1.08%)
# -*- coding: utf-8 -*-
"""
量能爆发+宽幅震荡选股策略 — 通达信回测版

从 tushare_quant.py 的 detect_volume_surge_swing() 提取核心逻辑,
适配 StrategyBacktester 框架进行向量化历史回测.

使用方式:
    python volume_surge_strategy.py                          # 默认参数回测
    python volume_surge_strategy.py --start 20240101 --end 20260701
    python volume_surge_strategy.py --start 20230101 --search  # 参数搜索
"""
from __future__ import annotations
import os, sys, time, argparse
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from itertools import product

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_loader import load_kline, iter_all_day_files, tdx_filename_to_ts_code
from strategy_backtest import (StrategyBacktester, Filters,
                               _rolling_mean_np, _rolling_max_np, _rolling_min_np,
                               load_stock_names, get_stock_name, barslast)


# =========================================================
# 量能爆发专属过滤参数
# =========================================================
@dataclass
class VolSurgeFilters:
    """量能爆发策略过滤参数 (默认值 = tushare_quant 中硬编码值)"""
    max_vol_ratio_min: float = 2.6       # 最大量比下限
    vol_gt2_min:       int   = 3         # 量比>2天数的下限
    avg_amplitude_min: float = 4.5       # 日均振幅下限
    range_swing_min:   float = 35.0      # 区间振幅下限
    price_change_min:  float = -10.0     # 区间涨幅下限
    price_change_max:  float = 100.0     # 区间涨幅上限
    hist_vol_pct_min:  float = 50.0      # 近历史最高量%下限
    ma20_chg_10d_min:  float = -0.3      # MA20近10天变化率下限
    ma20_chg_20d_min:  float = -1.0      # MA20近20天变化率下限
    vol_vs_base_min:   float = 1.1       # 近20日均量/起涨前基量下限
    vol_vs_peak_min:   float = 0.5       # 近20日均量/高点5日均量下限
    a_gain_min:        float = 15.0      # A浪涨幅下限
    fib_786_ratio:     float = 0.92      # B浪回撤不跌穿78.6%的容差
    retrace_ratio_max: float = 50.0      # B浪回撤/A浪比例上限
    pre_peak_gain_max: float = 70.0      # 一波游：高峰前涨幅上限
    dist_from_peak_max: float = 15.0     # 一波游：距高峰上限
    bounce_min:        float = 10.0      # 一波游：反弹下限
    total_score_min:   float = 65.0      # 综合评分下限

    # === MA聚合起涨过滤 ===
    # 当MA5/MA10/MA20三线粘合后放量突破，确认盘整结束+趋势启动
    ma_converge_max_dev: float = 6.0     # 三线最大偏离度(%)，越小越粘合
    entry_pct_chg_min:   float = 1.0     # 当日最低涨幅(%)
    entry_pct_chg_max:   float = 9.5     # 当日最高涨幅(%)，排除涨停

    def label(self) -> str:
        return (f"MR{self.max_vol_ratio_min}_VG{self.vol_gt2_min}"
                f"_AA{self.avg_amplitude_min}_RS{self.range_swing_min}")


# =========================================================
# Numpy 版 EWM (滚动指数加权)
# =========================================================
def _ewm_np(arr: np.ndarray, span: int) -> np.ndarray:
    """numpy 版指数加权移动平均 (相当于 pandas ewm(span=span).mean)"""
    s = pd.Series(arr)
    return s.ewm(span=span, adjust=False).mean().values


# =========================================================
# 预计算量能爆发策略所需指标
# =========================================================
def precompute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """在 K 线基础上增加量能爆发策略所需的所有指标列"""
    out = df.copy()
    C = out["close"].values
    H = out["high"].values
    L = out["low"].values
    VOL = out["vol"].values
    n = len(C)

    # --- 通用均线 ---
    out["ma5"]  = _rolling_mean_np(C, 5)
    out["ma10"] = _rolling_mean_np(C, 10)
    out["ma20"] = _rolling_mean_np(C, 20)
    out["ma60"] = _rolling_mean_np(C, 60)

    # --- 量能指标 ---
    out["vol_ma20"]   = _rolling_mean_np(VOL, 20)
    out["vol_ma5"]    = _rolling_mean_np(VOL, 5)
    out["vol_ma60"]   = _rolling_mean_np(VOL, 60)

    # 量比
    vol_ratio = np.full(n, np.nan)
    vol_ratio[20:] = VOL[20:] / np.maximum(out["vol_ma20"].values[20:], 1)
    out["vol_ratio"] = vol_ratio

    # 区间最大值（60天滚动）
    out["hh_60"] = _rolling_max_np(H, 60)
    out["ll_60"] = _rolling_min_np(L, 60)

    # 20日振幅
    out["hh_20"] = _rolling_max_np(H, 20)
    out["ll_20"] = _rolling_min_np(L, 20)
    out["amp_20d"] = out["hh_20"] / out["ll_20"] - 1

    # 涨幅
    if n >= 40:
        ret_2m = np.full(n, np.nan)
        ret_2m[40:] = C[40:] / C[:-40] - 1
        out["ret_2m"] = ret_2m
    if n >= 6:
        ret_5d = np.full(n, np.nan)
        ret_5d[6:] = C[6:] / C[:-6] - 1
        out["ret_5d"] = ret_5d

    # --- 阳线标记 ---
    O = out["open"].values
    out["is_yang"] = (C > O).astype(np.int8)

    # --- 涨停标记 (粗判10%，双创板在策略函数中调整) ---
    zt_rough = np.zeros(n, dtype=np.int8)
    if n >= 2:
        zt_rough[1:] = (C[1:] / C[:-1] >= 1.098).astype(np.int8)
    out["is_zt"] = zt_rough

    # --- MACD ---
    ema12 = _ewm_np(C, 12)
    ema26 = _ewm_np(C, 26)
    macd_dif = ema12 - ema26
    macd_dea = _ewm_np(macd_dif, 9)
    macd_bar = 2 * (macd_dif - macd_dea)
    out["macd_dif"] = macd_dif
    out["macd_dea"] = macd_dea
    out["macd_bar"] = macd_bar

    # MACD 状态: 0=其他, 1=绿柱缩短, 2=刚刚红柱, 3=加速度由负转正（最早）
    macd_status = np.zeros(n, dtype=np.int8)
    for i in range(3, n):
        if macd_bar[i-1] < 0 < macd_bar[i]:
            macd_status[i] = 2  # 刚刚红柱
        elif (macd_bar[i] < 0 and macd_bar[i] > macd_bar[i-1] >
              macd_bar[i-2]):
            macd_status[i] = 1  # 绿柱连续缩短
        # 加速度提前: bar变化率的变化由负转正,且macd_bar未创新低
        elif macd_bar[i] < 0:
            accel_1 = macd_bar[i] - macd_bar[i-1]
            accel_2 = macd_bar[i-1] - macd_bar[i-2]
            # 柱体未创新低 + 加速度由负转正（下跌放缓→即将拐头）
            bar_not_new_low = macd_bar[i] > min(macd_bar[i-3:i])
            accel_turns_pos = accel_2 < 0 < accel_1
            if bar_not_new_low and accel_turns_pos:
                macd_status[i] = 3  # 加速度由负转正
    out["macd_status"] = macd_status

    return out


# =========================================================
# 向量化选股信号生成
# =========================================================
def volume_surge_strategy_vectorized(
    df_pre: pd.DataFrame, code: str,
    vf: VolSurgeFilters = None
) -> np.ndarray:
    """量能爆发策略 — 每只股票一次性计算全部交易日的信号

    核心逻辑来自 tushare_quant.py 的 detect_volume_surge_swing(),
    适配为按日索引向量化版本.

    Returns:
        bool array, True = 该日触发买入信号
    """
    if vf is None:
        vf = VolSurgeFilters()

    n = len(df_pre)
    signals = np.zeros(n, dtype=np.float64)
    if n < 180:
        return signals
    if code.startswith("1") or code.startswith("2"):
        return signals

    # ST 过滤
    name = get_stock_name(code)
    if name.upper().startswith("ST") or name.upper().startswith("*ST"):
        return signals

    C = df_pre["close"].values
    H = df_pre["high"].values
    L = df_pre["low"].values
    O = df_pre["open"].values
    VOL = df_pre["vol"].values
    ma20 = df_pre["ma20"].values
    ma10 = df_pre["ma10"].values
    ma5 = df_pre["ma5"].values
    vol_ma20 = df_pre["vol_ma20"].values
    vol_ma5 = df_pre["vol_ma5"].values
    vol_ratio = df_pre["vol_ratio"].values
    is_yang = df_pre["is_yang"].values.astype(bool)
    is_zt_global = df_pre["is_zt"].values.astype(bool)
    macd_bar = df_pre["macd_bar"].values
    macd_status = df_pre["macd_status"].values

    # 前置过滤 (向量化)
    valid = np.ones(n, dtype=bool)
    valid[:180] = False  # 最少180天数据

    # 遍历每个有效交易日
    for i in np.where(valid)[0]:
        start = max(0, i - 200)
        recent = slice(start, i + 1)  # 包含 i 共 ~200 天
        recent_len = i - start + 1

        vol_arr = VOL[recent]
        high_arr = H[recent]
        low_arr = L[recent]
        close_arr = C[recent]
        if recent_len < 20:
            continue

        # === 1. 量能硬条件 ===
        vol_ma20_local = _rolling_mean_np(vol_arr, 20)
        if len(vol_ma20_local) == 0 or vol_ma20_local[-1] == 0:
            continue
        vol_ratio_local = vol_arr / np.maximum(vol_ma20_local, 1)
        max_vol_ratio = float(np.nanmax(vol_ratio_local))
        vol_ratio_gt2 = int(np.sum(vol_ratio_local > 2.0))
        vol_ratio_gt3 = int(np.sum(vol_ratio_local > 3.0))

        if max_vol_ratio < vf.max_vol_ratio_min:
            continue
        if vol_ratio_gt2 < vf.vol_gt2_min:
            continue

        # === 2. 振幅条件 ===
        amplitude = (high_arr - low_arr) / np.maximum(close_arr, 0.01) * 100
        avg_amplitude = float(np.mean(amplitude[-120:]))
        amp_gt8_count = int(np.sum(amplitude > 8))
        range_high = float(np.max(high_arr))
        range_low = float(np.min(low_arr))
        range_swing = (range_high / range_low - 1) * 100 if range_low > 0 else 0

        if avg_amplitude < vf.avg_amplitude_min:
            continue
        if range_swing < vf.range_swing_min:
            continue

        # === 3. 区间涨幅 ===
        price_change = (close_arr[-1] / close_arr[0] - 1) * 100
        if price_change < vf.price_change_min:
            continue
        if price_change > vf.price_change_max:
            continue

        # === 4. 历史量 %% ===
        hist_vol = VOL[:i+1]
        hist_vol_max = float(np.max(hist_vol)) if len(hist_vol) > 0 else 0
        recent_vol_max = float(np.max(vol_arr))
        vol_vs_hist_pct = (recent_vol_max / hist_vol_max * 100) if hist_vol_max > 0 else 0
        if vol_vs_hist_pct < vf.hist_vol_pct_min:
            continue

        # === 5. MA20 趋势 (近10天变化, 近20天变化) ===
        ma20_local = _rolling_mean_np(close_arr, 20)
        if len(ma20_local) >= 41:
            ma20_now = float(ma20_local[-1])
            ma20_10ago = float(ma20_local[-11]) if not np.isnan(ma20_local[-11]) else ma20_now
            ma20_20ago = float(ma20_local[-21]) if not np.isnan(ma20_local[-21]) else ma20_now
            if ma20_now > 0 and ma20_10ago > 0 and ma20_20ago > 0:
                ma20_chg_10d = (ma20_now / ma20_10ago - 1) * 100
                ma20_chg_20d = (ma20_now / ma20_20ago - 1) * 100
                if ma20_chg_10d < vf.ma20_chg_10d_min or ma20_chg_20d < vf.ma20_chg_20d_min:
                    continue

        # 股价站上MA20
        close_latest = float(close_arr[-1])
        ma20_latest = float(ma20_local[-1]) if len(ma20_local) > 0 and not np.isnan(ma20_local[-1]) else 0
        if ma20_latest > 0 and close_latest < ma20_latest:
            continue

        # === 6. 基量 vs 活跃量 ===
        vol_200 = VOL[max(0, i-199):i+1]
        if len(vol_200) < 20:
            continue
        peak_vol_idx = int(np.argmax(vol_200))
        peak_vol_price = float(H[max(0, i-199)+peak_vol_idx])

        pre_peak_start = max(0, peak_vol_idx - 20)
        pre_peak_end = max(0, peak_vol_idx - 3)
        if pre_peak_end <= pre_peak_start:
            base_vol = float(np.mean(vol_200[:peak_vol_idx])) if peak_vol_idx > 0 else float(np.mean(vol_200))
        else:
            base_vol = float(np.mean(vol_200[pre_peak_start:pre_peak_end]))
        base_vol = max(base_vol, 1)

        recent_vol_20 = float(np.mean(vol_200[-20:])) if len(vol_200) >= 20 else float(np.mean(vol_200))
        vol_vs_base = recent_vol_20 / base_vol
        if vol_vs_base < vf.vol_vs_base_min:
            continue

        # === 7. 近期 vs 高点量能 ===
        peak_5d_start = max(0, peak_vol_idx - 5)
        peak_5d_end = min(len(vol_200), peak_vol_idx + 6)
        peak_5d_vol = float(np.mean(vol_200[peak_5d_start:peak_5d_end])) if peak_5d_end > peak_5d_start else recent_vol_20
        peak_5d_vol = max(peak_5d_vol, 1)
        vol_vs_peak = recent_vol_20 / peak_5d_vol
        if vol_vs_peak < vf.vol_vs_peak_min:
            continue

        # === 8. ABC 结构 ===
        a_low = float(np.min(low_arr[:peak_vol_idx+1]))
        a_gain = (peak_vol_price / a_low - 1) * 100 if a_low > 0 else 0
        if a_gain < vf.a_gain_min:
            continue

        if peak_vol_idx < len(low_arr) - 3:
            b_low = float(np.min(low_arr[peak_vol_idx:]))
            b_drop = (1 - b_low / peak_vol_price) * 100
            retrace_ratio = b_drop / a_gain * 100 if a_gain > 0 else 0
        else:
            b_low = close_latest
            retrace_ratio = 0

        fib_786 = peak_vol_price - (peak_vol_price - a_low) * 0.786
        if b_low < fib_786 * vf.fib_786_ratio:
            continue
        if retrace_ratio > vf.retrace_ratio_max:
            continue

        # === 9. 排除一波游 ===
        peak_idx_local = int(np.argmax(high_arr))
        peak_price_local = float(high_arr[peak_idx_local])
        pre_peak_low = float(np.min(low_arr[:peak_idx_local+1])) if peak_idx_local > 0 else float(low_arr[0])
        pre_peak_gain = (peak_price_local / pre_peak_low - 1) * 100 if pre_peak_low > 0 else 0
        dist_from_peak = (1 - close_arr[-1] / peak_price_local) * 100

        if peak_idx_local < len(high_arr) - 10:
            post_peak_low = float(np.min(low_arr[peak_idx_local:]))
            bounce = (close_arr[-1] / post_peak_low - 1) * 100 if post_peak_low > 0 else 0
        else:
            bounce = 0

        if pre_peak_gain > vf.pre_peak_gain_max and dist_from_peak > vf.dist_from_peak_max and bounce < vf.bounce_min:
            continue

        # === 10. 综合评分（量能/振幅基础分） ===
        vol_score = min(max_vol_ratio / 5.0, 1) * 30
        freq_score = min(vol_ratio_gt2 / 7, 1) * 20
        amp_score = min(avg_amplitude / 7, 1) * 20
        big_amp_score = min(amp_gt8_count / 15, 1) * 15
        swing_score = min(range_swing / 60, 1) * 15
        base_score = vol_score + freq_score + amp_score + big_amp_score + swing_score

        if base_score < vf.total_score_min:
            continue

        # === 11. MA聚合确认 + 量比分位评分 ===
        # MA5/MA10/MA20三线粘合表明盘整结束，突破后趋势启动
        qiang_score = 0.0
        if not np.isnan(ma5[i]) and not np.isnan(ma10[i]) and not np.isnan(ma20[i]):
            ma_min = min(ma5[i], ma10[i], ma20[i])
            ma_max = max(ma5[i], ma10[i], ma20[i])
            if ma_min > 0:
                ma_spread = (ma_max / ma_min - 1) * 100
                if ma_spread <= vf.ma_converge_max_dev:
                    # 三线粘合，加分
                    qiang_score += 15 * (1 - ma_spread / vf.ma_converge_max_dev)
                else:
                    # 均线发散太开，可能是追高信号，减分
                    qiang_score -= 10

        # 量比分位评分（择优排序用）
        lookback_start = max(20, i - 10)
        vol_ratio_slice = vol_ratio[lookback_start:i + 1]
        valid_ratios = vol_ratio_slice[~np.isnan(vol_ratio_slice)]
        if len(valid_ratios) >= 3:
            rank = np.sum(valid_ratios <= vol_ratio[i]) / len(valid_ratios)
            qiang_score += 10 * max(rank - 0.3, 0)  # 量比超过70%分位加分

        total_score = base_score + qiang_score

        # === 12. MACD 三阶递进确认（加速度→绿柱缩短→刚红柱） ===
        # 按信号可靠度分阶梯评分门槛：加速度（最早/有噪声）需更高评分
        macd_pass = False
        cur_bar = float(macd_bar[i])
        prev_bar = float(macd_bar[i - 1]) if i > 0 else cur_bar
        prev2_bar = float(macd_bar[i - 2]) if i > 1 else prev_bar

        # ① 加速度由负转正（最早信号，提前2-3天，有噪声）
        # 门槛用 base_score（不含强者恒强加成），确保量能/振幅本身达标
        if i >= 3:
            accel_1 = cur_bar - prev_bar
            accel_2 = prev_bar - prev2_bar
            if (cur_bar < 0 and
                cur_bar > min(macd_bar[i - 3:i]) and
                accel_2 < 0 < accel_1 and
                base_score >= vf.total_score_min + 5):  # 加速度信号需更高评分过滤噪声
                macd_pass = True
        # ② 绿柱连续缩短（中间信号，可靠度一般）
        if not macd_pass and cur_bar < 0 and cur_bar > prev_bar > prev2_bar:
            macd_pass = True
        # ③ 刚刚红柱（最慢信号，可靠度最高）
        if not macd_pass and prev_bar < 0 < cur_bar:
            macd_pass = True
        # ④ 红柱回调缩短+接近0：上升趋势中短暂回调，红柱接近零轴（放宽连续缩短要求，0.7倍阈值）
        if not macd_pass and cur_bar > 0 and prev_bar > 0 and cur_bar < abs(macd_bar[i - 4]) * 0.7:
            macd_pass = True
        # ⑤ 红柱回调后反弹：上升趋势中短暂回调后重新发力（cur>prev且prev<prev2）
        if not macd_pass and cur_bar > 0 and prev_bar > 0 and cur_bar > prev_bar and prev_bar < prev2_bar:
            macd_pass = True

        if not macd_pass:
            continue

        # === 13. 当日买入确认 ===
        # 涨幅确认：避免追涨停（>9.5%）和买入平盘（<0.5%）
        pct_chg_i = float(df_pre.iloc[i]["pct_chg"])
        if pct_chg_i < vf.entry_pct_chg_min or pct_chg_i > vf.entry_pct_chg_max:
            continue

        # 记录评分用于后续择优排序
        signals[i] = total_score

    return signals


# =========================================================
# 回测入口
# =========================================================
def load_stock_pool_from_csv(csv_path: str) -> List[str]:
    """从 bull_stocks_all.csv 读入股票池代码列表"""
    df = pd.read_csv(csv_path, encoding='utf-8-sig')
    codes = []
    for _, row in df.iterrows():
        code = str(row.get('code', ''))
        if not code:
            continue
        if not code.endswith('.SH') and not code.endswith('.SZ'):
            if code.startswith('6') or code.startswith('9'):
                code += '.SH'
            elif code.startswith('0') or code.startswith('1') or code.startswith('2') or code.startswith('3'):
                code += '.SZ'
        if code.endswith('.SH') and code.startswith(('5', '6')):
            codes.append(code)
        elif code.endswith('.SZ') and code.startswith(('0', '1', '2', '3')):
            codes.append(code)
    codes = sorted(set(codes))
    print(f"[Pool] 股票池: {csv_path} → {len(codes)} 只")
    return codes


# 大盘过滤阈值: 三指数(上证/沪深300/创业板指)20日动量均值(%), 高于此值才允许交易
MOM_GATE_THRESHOLD = 3.0


def run_backtest(start_date: str = "20240101",
                 end_date: str = None,
                 hold_days: int = 5,
                 max_stocks: int = None,
                 vf: VolSurgeFilters = None,
                 stock_pool: List[str] = None,
                 max_daily: int = 5,
                 verbose: bool = True) -> Dict:
    """运行量能爆发策略回测

    Args:
        start_date: 回测起始日 YYYYMMDD
        end_date: 回测结束日 YYYYMMDD
        hold_days: 持有天数 (默认5, 量能爆发策略检查T+5胜率)
        max_stocks: 限加载股票数 (调试用)
        vf: 过滤参数
        verbose: 打印进度

    Returns:
        回测结果字典
    """
    if vf is None:
        vf = VolSurgeFilters()
    if end_date is None:
        end_date = datetime.now().strftime("%Y%m%d")

    print("=" * 60)
    print(f"  量能爆发+宽幅震荡策略 回测")
    print(f"  区间: {start_date} ~ {end_date}  持有: T+{hold_days}  每日选股: 最多{max_daily}只")
    print(f"  过滤: {vf.label()}  硬止损: -7%  大盘过滤: 三指数动量>+3%  买入: 次日开盘")
    print(f"  MA聚合起涨: 三线偏离<{vf.ma_converge_max_dev}% 涨幅{vf.entry_pct_chg_min}%~{vf.entry_pct_chg_max}%")
    print("=" * 60)

    # 加载数据
    t0 = time.time()
    kline_dict = {}

    # 加载 K 线
    load_stock_names()
    codes_loaded = 0
    dt = datetime.strptime(start_date, "%Y%m%d")
    load_start = (dt - timedelta(days=400)).strftime("%Y%m%d")

    # --- 加载大盘状态（三指数 20日动量均值） ---
    idx3_mom20 = {}  # trade_date -> 三指数20日动量均值(%)
    try:
        mom_maps = []
        for _code in ("000001.SH", "000300.SH", "399006.SZ"):
            _df = load_kline(_code, start_date=load_start, end_date=end_date)
            if not _df.empty:
                _df = precompute_indicators(_df)
                _mom = (_df["close"] / _df["close"].shift(20) - 1) * 100
                mom_maps.append(dict(zip(_df["trade_date"].values,
                                         _mom.values)))
        if len(mom_maps) == 3:
            _all = sorted(set().union(*[set(m) for m in mom_maps]))
            for _d in _all:
                _vals = [m[_d] for m in mom_maps
                         if _d in m and not pd.isna(m[_d])]
                if len(_vals) == 3:
                    idx3_mom20[_d] = float(np.mean(_vals))
            _gt = sum(1 for v in idx3_mom20.values() if v > MOM_GATE_THRESHOLD)
            print(f"[Market] 三指数动量数据: {len(idx3_mom20)} 天, "
                  f"动量>+{MOM_GATE_THRESHOLD}% 天={_gt}")
    except Exception as e:
        print(f"[Market] 三指数加载失败: {e}，不过滤")

    if stock_pool:
        # 只加载股票池中的
        pool_set = set(stock_pool)
        for path in iter_all_day_files(markets=("SH", "SZ")):
            ts_code = tdx_filename_to_ts_code(path)
            if ts_code not in pool_set:
                continue
            df = load_kline(ts_code, start_date=load_start, end_date=end_date)
            if df.empty or len(df) < 180:
                continue
            df = precompute_indicators(df)
            kline_dict[ts_code] = df
            codes_loaded += 1
            if max_stocks and codes_loaded >= max_stocks:
                break
    else:
        for path in iter_all_day_files(markets=("SH", "SZ")):
            if max_stocks and codes_loaded >= max_stocks:
                break
            ts_code = tdx_filename_to_ts_code(path)
            # 只保留沪深A股(首字符6/3/0)，排除基金5/1、B股2/9、北交所4/8
            if not ts_code or ts_code[0] not in "630":
                continue
            df = load_kline(ts_code, start_date=load_start, end_date=end_date)
            if df.empty or len(df) < 180:
                continue
            df = precompute_indicators(df)
            kline_dict[ts_code] = df
            codes_loaded += 1

    elapsed = time.time() - t0
    mem_mb = sum(len(v) for v in kline_dict.values()) * 8 / 1024 / 1024
    print(f"[Load] 加载 {codes_loaded} 只股票, 耗时 {elapsed:.1f}s, 内存约 {mem_mb:.0f}MB")

    # 生成信号
    t0 = time.time()
    signals_dict = {}
    for ts_code, df_pre in kline_dict.items():
        sig = volume_surge_strategy_vectorized(df_pre, ts_code, vf)
        if sig.any():
            signals_dict[ts_code] = sig
    elapsed = time.time() - t0
    print(f"[Signal] 生成信号: {len(signals_dict)} 只有信号, 耗时 {elapsed:.1f}s")

    # 交易日列表
    all_dates = set()
    for df in kline_dict.values():
        all_dates.update(df["trade_date"].tolist())
    trade_dates = sorted([d for d in all_dates if start_date <= d <= end_date])
    date_idx_map = {}
    for ts_code, df in kline_dict.items():
        date_idx_map[ts_code] = dict(zip(df["trade_date"], df.index))
    print(f"[Dates] 回测交易日: {len(trade_dates)} 天")

    # 逐日回测
    daily_counts = []
    all_returns = []
    market_skipped_days = 0  # 大盘过滤跳过的天数

    for td_idx, td in enumerate(trade_dates):
        # === 大盘过滤: 三指数20日动量均值 > 阈值 ===
        td_mom20 = idx3_mom20.get(td)
        if td_mom20 is not None and td_mom20 <= MOM_GATE_THRESHOLD:
            daily_counts.append(0)
            market_skipped_days += 1
            continue

        selected = []
        # 收集当日所有信号及其评分/距MA20 (r2排序需用)
        daily_candidates = []
        for ts_code, sig in signals_dict.items():
            idx_map = date_idx_map.get(ts_code)
            if not idx_map:
                continue
            i = idx_map.get(td)
            if i is None or i >= len(sig):
                continue
            score = sig[i]
            if score > 0:
                _df = kline_dict.get(ts_code)
                if _df is None:
                    continue
                _ma20 = float(_df.iloc[i]["ma20"])
                _pos = (float(_df.iloc[i]["close"]) / _ma20 - 1) * 100 if _ma20 > 0 else 99.0
                daily_candidates.append((ts_code, float(score), _pos))

        # r2 择优: 距MA20升序优先(<=8%在前), >8% 排尾部补足
        if daily_candidates:
            daily_candidates.sort(key=lambda x: (x[2] > 8, x[2]))
            selected = [c[0] for c in daily_candidates[:max_daily]]

        daily_counts.append(len(selected))

        # 评估 T+N 日收益（带硬止损：买入后任何一天盘中跌破-7%即止损）
        if selected:
            stop_loss_pct = -7.0
            for ts_code in selected:
                df = kline_dict.get(ts_code)
                if df is None:
                    continue
                idx = df.index[df["trade_date"] == td].tolist()
                if not idx:
                    continue
                i = idx[0]
                if i + 1 >= len(df):      # 需有次日数据(次日开盘买入)
                    continue
                buy_close = df.iloc[i + 1]["open"]   # 次日开盘价买入
                # 硬止损检查：买入后持有期内任何一天的最低价跌破止损位
                exit_idx = i + 1 + hold_days
                if exit_idx >= len(df):
                    exit_idx = len(df) - 1
                stopped = False
                for j in range(i + 2, exit_idx + 1):
                    low_price = df.iloc[j]["low"]
                    if low_price / buy_close - 1 <= stop_loss_pct / 100:
                        ret = stop_loss_pct
                        stopped = True
                        break
                if not stopped:
                    if i + 1 + hold_days < len(df):
                        sell_close = df.iloc[i + 1 + hold_days]["close"]
                        ret = (sell_close / buy_close - 1) * 100
                    else:
                        continue
                all_returns.append(ret)

        if verbose and (td_idx % 20 == 0 or td_idx == len(trade_dates) - 1):
            print(f"  [{td_idx+1}/{len(trade_dates)}] {td}: 选中 {len(selected)} 只, "
                  f"累计信号 {len(all_returns)} 个")

    # 统计结果
    all_returns_arr = np.array(all_returns) if all_returns else np.array([0])
    win_rate = (all_returns_arr > 0).mean() * 100 if len(all_returns) > 0 else 0
    avg_ret = all_returns_arr.mean() if len(all_returns) > 0 else 0
    med_ret = np.median(all_returns_arr) if len(all_returns) > 0 else 0
    daily_counts_arr = np.array(daily_counts)
    n_days_1_5 = int(((daily_counts_arr >= 1) & (daily_counts_arr <= 5)).sum())
    n_days_0   = int((daily_counts_arr == 0).sum())

    print()
    print("=" * 50)
    print("  回测结果")
    print("=" * 50)
    print(f"  回测天数:     {len(trade_dates)}")
    print(f"  日均选股:     {np.mean(daily_counts):.1f} 只")
    print(f"  选股1-5只天数: {n_days_1_5} / {len(trade_dates)} ({n_days_1_5/len(trade_dates)*100:.0f}%)")
    print(f"  零选股天数:   {n_days_0} / {len(trade_dates)} ({n_days_0/len(trade_dates)*100:.0f}%)")
    if idx3_mom20:
        print(f"  大盘过滤跳过: {market_skipped_days} 天")
    print(f"  硬止损:       -7%")
    print(f"  总信号数:     {len(all_returns)}")
    print(f"  持有天数:     T+{hold_days}")
    print(f"  胜率:         {win_rate:.1f}%")
    print(f"  平均收益:     {avg_ret:+.2f}%")
    print(f"  中位收益:     {med_ret:+.2f}%")
    print(f"  最大单笔收益: {all_returns_arr.max():+.2f}%" if len(all_returns) > 0 else "")
    print(f"  最小单笔收益: {all_returns_arr.min():+.2f}%" if len(all_returns) > 0 else "")
    print()

    return {
        "daily_counts": daily_counts,
        "all_returns": all_returns,
        "win_rate": round(win_rate, 1),
        "avg_return": round(avg_ret, 2),
        "median_return": round(med_ret, 2),
        "n_signals": len(all_returns),
        "n_days_1_5": n_days_1_5,
        "n_total_days": len(trade_dates),
        "market_skipped": market_skipped_days,
        "stop_loss": -7.0,
        "filters": vf,
    }


# =========================================================
# 主入口
# =========================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="量能爆发策略回测")
    parser.add_argument("--start", default="20240101", help="回测起始日 YYYYMMDD")
    parser.add_argument("--end", default=None, help="回测结束日 YYYYMMDD")
    parser.add_argument("--hold", type=int, default=5, help="持有天数")
    parser.add_argument("--hold-range", type=str, default=None,
                        help="测试多持有期,逗号分隔,如 3,5,10,20")
    parser.add_argument("--max-stocks", type=int, default=None, help="最大加载股票数")
    parser.add_argument("--max-daily", type=int, default=5,
                        help="每日最多选股数 (默认5, 按评分择优)")
    parser.add_argument("--search", action="store_true", help="参数搜索模式")
    parser.add_argument("--stock-pool", type=str, default=None,
                        help="股票池CSV路径,如 report_daily/bull_stocks_all.csv")
    args = parser.parse_args()

    # 加载股票池
    stock_pool_codes = None
    if args.stock_pool:
        stock_pool_codes = load_stock_pool_from_csv(args.stock_pool)

    if args.hold_range:
        # ---- 多持股期对比 ----
        hold_list = sorted(set(int(h) for h in args.hold_range.split(",") if h.strip()))
        print(f"\n多持股期测试: {hold_list}\n")
        results = []
        for hd in hold_list:
            r = run_backtest(args.start, args.end, hd, args.max_stocks,
                             stock_pool=stock_pool_codes, max_daily=args.max_daily,
                             verbose=False)
            results.append((hd, r))
            print()

        print("=" * 70)
        print("  不同持股期对比")
        print("=" * 70)
        print(f"  {'持股':>4} | {'胜率':>6} {'平均收益':>8} {'中位收益':>8} "
              f"{'信号数':>6} {'选股1-5天':>8}")
        print(f"  {'-'*50}")
        for hd, r in results:
            print(f"  T+{hd:<2} | {r['win_rate']:>5.1f}% {r['avg_return']:>+7.2f}% "
                  f"{r['median_return']:>+7.2f}% {r['n_signals']:>6d} "
                  f"{r['n_days_1_5']:>4d}/{r['n_total_days']}")
    elif args.search:
        # 参数搜索
        vf_base = VolSurgeFilters()
        # 搜索量比下限和评分下限
        param_grid = {
            "max_vol_ratio_min": [2.3, 2.6, 3.0],
            "total_score_min": [55, 60, 65, 70],
            "vol_vs_base_min": [1.0, 1.3, 1.5],
        }
        best = None
        best_win = 0
        results = []
        for mr, ts, vvb in product(*param_grid.values()):
            vf = VolSurgeFilters(
                max_vol_ratio_min=mr,
                total_score_min=ts,
                vol_vs_base_min=vvb,
            )
            result = run_backtest(args.start, args.end, args.hold, max_stocks=2000, vf=vf, verbose=False)
            win = result["win_rate"]
            n = result["n_signals"]
            n_1_5 = result["n_days_1_5"]
            results.append((mr, ts, vvb, win, n, n_1_5))
            if win > best_win:
                best_win = win
                best = (mr, ts, vvb, result)

        print("\n" + "=" * 70)
        print("  参数搜索结果 (按胜率排序)")
        print("=" * 70)
        print(f"  {'量比下限':>8} {'评分下限':>8} {'基量比下限':>8} {'胜率':>6} {'信号数':>8} {'选股天数':>8}")
        for mr, ts, vvb, win, n, n_1_5 in sorted(results, key=lambda x: -x[3]):
            print(f"  {mr:>8.1f} {ts:>8.0f} {vvb:>8.1f} {win:>6.1f}% {n:>8d} {n_1_5:>8d}")

        if best:
            print(f"\n  最优参数: 量比>{best[0]} 评分>{best[1]} 基量>{best[2]}")
            print(f"  胜率: {best_win:.1f}%")
    else:
        run_backtest(args.start, args.end, args.hold, args.max_stocks,
                     stock_pool=stock_pool_codes, max_daily=args.max_daily)
