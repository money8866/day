"""
Chip Alpha Engine V2 - 机构级动态筹码Alpha分析引擎
Version: V2.1

核心理念：
  - 不分析静态筹码分布，提取预测性Alpha因子
  - 预测5~20交易日成为趋势龙头的概率
  - 速度和方向 > 绝对水平
  - 质心 > 筹码峰
  - ATR基准阻力 > 固定百分比
  - Kalman Filter提取趋势成分 > 简单线性回归

10大动态因子：
├── Factor 1: Chip Center Velocity（质心迁移速度）
├── Factor 2: Chip Peak Migration（Top3峰迁移合并）[仅展示，不计入评分]
├── Factor 3: Winning Expansion Velocity（获利盘扩散速度）
├── Factor 4: Overhead Supply Decay（ATR基准上方压力衰减）
├── Factor 5: Chip Concentration（80%筹码集中度）
├── Factor 6: Chip Rotation Efficiency CRE（筹码轮换效率）[最高权重]
├── Factor 7: Chip Resilience（回调中质心韧性）
├── Factor 8: Absorption Quality（吸筹质量CLV）
├── Factor 9: Multi-Day Consistency（多日一致性）[仅风险参考]
├── Factor 10: Chip Momentum（筹码动量）[NEW: Kalman Filter + Z-score]

用法:
    engine = ChipAlphaEngineV2(token="your_tushare_token")
    result = engine.analyze("000729.SZ", lookback_days=20)
    print(engine.format_report(result))
"""
import os
import time
import json
import math
import glob
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import pandas as pd
import numpy as np

# 全局API调用锁（确保多线程环境下120ms最小间隔）
_API_LOCK = threading.Lock()


# ============================================================
# 工具函数
# ============================================================
def _get_trade_dates(end_date: str, lookback_days: int) -> List[str]:
    """生成交易日序列（自然日近似，周末跳过）"""
    end_dt = datetime.strptime(str(end_date).replace('-', ''), '%Y%m%d')
    dates = []
    cur = end_dt
    while len(dates) < lookback_days:
        if cur.weekday() < 5:
            dates.append(cur.strftime('%Y%m%d'))
        cur -= timedelta(days=1)
    dates.reverse()
    return dates


def _ema(series: List[float], period: int) -> List[float]:
    """计算EMA"""
    if len(series) == 0:
        return []
    alpha = 2.0 / (period + 1)
    ema_vals = [series[0]]
    for i in range(1, len(series)):
        ema_vals.append(alpha * series[i] + (1 - alpha) * ema_vals[-1])
    return ema_vals


def _linear_slope(values: List[float]) -> float:
    """线性回归斜率"""
    n = len(values)
    if n < 2:
        return 0.0
    x = np.arange(n, dtype=float)
    y = np.array(values, dtype=float)
    slope = np.polyfit(x, y, 1)[0]
    return float(slope)


def _rolling_percentile(values: List[float], window: int = 10) -> List[float]:
    """Rolling percentile rank (0~100)，滚动窗口内当前值排位"""
    result = []
    for i in range(len(values)):
        start = max(0, i - window + 1)
        win = values[start:i + 1]
        rank = sum(1 for v in win if v < values[i])
        pct = (rank / (len(win) - 1)) * 100 if len(win) > 1 else 50.0
        result.append(pct)
    return result


def _rolling_zscore(values: List[float], window: int = 10) -> List[float]:
    """Rolling Z-score（滚动窗口标准化）"""
    result = []
    for i in range(len(values)):
        start = max(0, i - window + 1)
        win = values[start:i + 1]
        if len(win) < 2:
            result.append(0.0)
        else:
            mu = np.mean(win)
            sigma = np.std(win)
            result.append((values[i] - mu) / sigma if sigma > 1e-10 else 0.0)
    return result


def _kalman_filter_1d(values: List[float], process_noise: float = 0.01,
                      measurement_noise: float = 0.1) -> List[float]:
    """一维Kalman Filter，提取趋势成分 + 计算速度"""
    if len(values) < 2:
        return list(values)

    x = values[0]        # 状态估计
    v = 0.0              # 速度估计
    p_xx = 1.0           # 位置协方差
    p_xv = 0.0           # 位置-速度交叉协方差
    p_vv = 1.0           # 速度协方差
    q = process_noise    # 过程噪声
    r = measurement_noise # 测量噪声

    filtered = [x]
    for i in range(1, len(values)):
        # 预测
        x_pred = x + v
        v_pred = v
        p_xx_pred = p_xx + 2 * p_xv + p_vv + q
        p_xv_pred = p_xv + p_vv
        p_vv_pred = p_vv + q

        # 测量更新
        z = values[i]
        y = z - x_pred
        s = p_xx_pred + r
        k_x = p_xx_pred / s if s > 1e-10 else 0
        k_v = p_xv_pred / s if s > 1e-10 else 0

        x = x_pred + k_x * y
        v = v_pred + k_v * y

        p_xx = (1 - k_x) * p_xx_pred
        p_xv = (1 - k_x) * p_xv_pred
        p_vv = p_vv_pred - k_v * p_xv_pred

        filtered.append(x)

    return filtered


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    if abs(b) < 1e-10:
        return default
    return a / b


# ============================================================
# Chip Alpha Engine V2
# ============================================================
class ChipAlphaEngineV2:
    """
    机构级动态筹码Alpha分析引擎

    输入：股票代码 + 回溯天数
    输出：10大动态因子 + 趋势阶段 + 预测概率（JSON）
    """

    # 综合评分权重（V2.1: 降权冗余因子，升权CRE，新增ChipMomentum，移除PeakMigration）
    WEIGHTS = {
        'cre': 0.25,
        'pressure_decay': 0.15,
        'chip_momentum': 0.15,
        'absorption': 0.15,
        'center_velocity': 0.10,
        'winning_expansion': 0.10,
        'resilience': 0.05,
        'concentration': 0.05,
    }

    def __init__(self, token: Optional[str] = None, cache_dir: Optional[str] = None):
        import tushare as ts
        if token is None:
            token = os.environ.get('TUSHARE_TOKEN', '')
        self.token = token
        # 直接传 token 初始化，避免 set_token 写入 ~/tk.csv（沙箱禁止）
        self.pro = ts.pro_api(token)

        if cache_dir is None:
            cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chip_cache')
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)

        self._last_call_ts = 0.0
        self._min_interval = 0.12

    # --------------------------------------------------------
    # 节流 & 缓存（复用V1逻辑）
    # --------------------------------------------------------
    def _throttle(self):
        with _API_LOCK:
            elapsed = time.time() - self._last_call_ts
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_call_ts = time.time()

    def _cache_path(self, ts_code: str, trade_date: str, dtype: str) -> str:
        return os.path.join(self.cache_dir, f"chip_{ts_code}_{trade_date}_{dtype}.parquet")

    def _read_cache(self, path: str) -> Optional[pd.DataFrame]:
        if os.path.exists(path):
            try:
                return pd.read_parquet(path)
            except Exception:
                try:
                    return pd.read_csv(path.replace('.parquet', '.csv'))
                except Exception:
                    return None
        return None

    def _write_cache(self, df: pd.DataFrame, path: str):
        try:
            df.to_parquet(path, index=False)
        except Exception:
            try:
                df.to_csv(path.replace('.parquet', '.csv'), index=False)
            except Exception:
                pass

    # --------------------------------------------------------
    # 数据获取
    # --------------------------------------------------------
    def fetch_chip_history(self, ts_code: str, trade_dates: List[str]) -> Dict[str, Dict]:
        """获取多日筹码分布数据（增量缓存，跨日期复用，后续运行只拉取新增日期）"""
        result = {}
        if not trade_dates:
            return result

        start_date = trade_dates[0]
        end_date = trade_dates[-1]
        date_set = set(trade_dates)

        # 使用统一缓存文件名（无日期范围后缀，存储该股票全部历史数据）
        chips_cache_path = os.path.join(self.cache_dir, f"chips_{ts_code}.parquet")
        perf_cache_path = os.path.join(self.cache_dir, f"perf_{ts_code}.parquet")

        # 读取已有缓存
        all_chips = self._read_cache(chips_cache_path)
        all_perf = self._read_cache(perf_cache_path)

        # 自动迁移旧格式缓存（chips_{ts_code}_{start}_{end}.parquet → chips_{ts_code}.parquet）
        if (all_chips is None or len(all_chips) == 0):
            old_chips = sorted(glob.glob(os.path.join(self.cache_dir, f"chips_{ts_code}_*.parquet")))
            if old_chips:
                old_df = self._read_cache(old_chips[-1])  # 最新日期的旧缓存
                if old_df is not None and len(old_df) > 0:
                    all_chips = old_df.sort_values('trade_date').reset_index(drop=True)
                    self._write_cache(all_chips, chips_cache_path)
        if (all_perf is None or len(all_perf) == 0):
            old_perfs = sorted(glob.glob(os.path.join(self.cache_dir, f"perf_{ts_code}_*.parquet")))
            if old_perfs:
                old_df = self._read_cache(old_perfs[-1])
                if old_df is not None and len(old_df) > 0:
                    all_perf = old_df.sort_values('trade_date').reset_index(drop=True)
                    self._write_cache(all_perf, perf_cache_path)

        # 确定需要新增的日期
        cached_chip_dates = set()
        if all_chips is not None and len(all_chips) > 0 and 'trade_date' in all_chips.columns:
            cached_chip_dates = set(all_chips['trade_date'].astype(str).unique())
        cached_perf_dates = set()
        if all_perf is not None and len(all_perf) > 0 and 'trade_date' in all_perf.columns:
            cached_perf_dates = set(all_perf['trade_date'].astype(str).unique())

        need_chip_dates = date_set - cached_chip_dates
        need_perf_dates = date_set - cached_perf_dates

        # 仅获取缺失日期的芯片数据，合并写入缓存
        if need_chip_dates:
            missing_start = min(need_chip_dates)
            missing_end = max(need_chip_dates)
            self._throttle()
            try:
                new_chips = self.pro.cyq_chips(ts_code=ts_code, start_date=missing_start, end_date=missing_end)
                if new_chips is not None and len(new_chips) > 0:
                    if all_chips is not None and len(all_chips) > 0:
                        combined = pd.concat([all_chips, new_chips], ignore_index=True)
                        combined = combined.drop_duplicates(subset=['trade_date']).sort_values('trade_date').reset_index(drop=True)
                    else:
                        combined = new_chips.sort_values('trade_date').reset_index(drop=True)
                    self._write_cache(combined, chips_cache_path)
                    all_chips = combined
            except Exception as e:
                print(f"  [筹码] cyq_chips 获取失败 {ts_code}: {e}")

        if need_perf_dates:
            missing_start = min(need_perf_dates)
            missing_end = max(need_perf_dates)
            self._throttle()
            try:
                new_perf = self.pro.cyq_perf(ts_code=ts_code, start_date=missing_start, end_date=missing_end)
                if new_perf is not None and len(new_perf) > 0:
                    if all_perf is not None and len(all_perf) > 0:
                        combined = pd.concat([all_perf, new_perf], ignore_index=True)
                        combined = combined.drop_duplicates(subset=['trade_date']).sort_values('trade_date').reset_index(drop=True)
                    else:
                        combined = new_perf.sort_values('trade_date').reset_index(drop=True)
                    self._write_cache(combined, perf_cache_path)
                    all_perf = combined
            except Exception as e:
                print(f"  [筹码] cyq_perf 获取失败 {ts_code}: {e}")

        # 按日期分组
        for td in trade_dates:
            day_data = {}
            if all_chips is not None and len(all_chips) > 0 and 'trade_date' in all_chips.columns:
                day_data['chips'] = all_chips[all_chips['trade_date'].astype(str) == td].reset_index(drop=True)
            else:
                day_data['chips'] = pd.DataFrame()
            if all_perf is not None and len(all_perf) > 0 and 'trade_date' in all_perf.columns:
                day_data['perf'] = all_perf[all_perf['trade_date'].astype(str) == td].reset_index(drop=True)
            else:
                day_data['perf'] = pd.DataFrame()
            result[td] = day_data
        return result

    def fetch_daily_history(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """获取单股日线（V2: 优先 SQLite daily_cache 表，缺失时降级 parquet + pro.daily）"""
        # 1) 优先 SQLite daily_cache 表（与全项目共享）
        try:
            from stock_cache import get_daily_cache, get_daily_cache_range, batch_insert_daily_cache
            _, max_date = get_daily_cache_range(ts_code)
            if max_date is not None and str(max_date) >= str(end_date):
                df = get_daily_cache(ts_code, start_date, end_date)
                if df is not None and not df.empty:
                    df['trade_date'] = df['trade_date'].astype(str)
                    return df.sort_values('trade_date').reset_index(drop=True)
        except Exception:
            pass

        # 2) 降级：本地 parquet 缓存（旧路径，保留兜底）
        cache_path = os.path.join(self.cache_dir, f"daily_{ts_code}_{start_date}_{end_date}.parquet")
        df = self._read_cache(cache_path)
        if df is None or len(df) == 0:
            self._throttle()
            try:
                df = self.pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
            except Exception as e:
                print(f"  [筹码] daily 获取失败 {ts_code}: {e}")
                df = pd.DataFrame()
            if df is not None and len(df) > 0:
                df = df.sort_values('trade_date').reset_index(drop=True)
                self._write_cache(df, cache_path)
                # 同步写入 daily_cache 表，供其他模块复用
                try:
                    batch_insert_daily_cache(df)
                except Exception:
                    pass
        return df

    def fetch_daily_basic(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        cache_path = os.path.join(self.cache_dir, f"daily_basic_{ts_code}_{start_date}_{end_date}.parquet")
        df = self._read_cache(cache_path)
        if df is None or len(df) == 0:
            self._throttle()
            try:
                df = self.pro.daily_basic(ts_code=ts_code, start_date=start_date, end_date=end_date)
            except Exception as e:
                print(f"  [筹码] daily_basic 获取失败 {ts_code}: {e}")
                df = pd.DataFrame()
            if df is not None and len(df) > 0:
                df = df.sort_values('trade_date').reset_index(drop=True)
                self._write_cache(df, cache_path)
        return df

    # --------------------------------------------------------
    # 核心计算：每日质心 ChipCenter = Σ(price × chip%) / Σ(chip%)
    # --------------------------------------------------------
    def _calc_chip_center(self, chips_df: pd.DataFrame) -> float:
        """计算筹码质心 = Σ(price × percent) / Σ(percent)"""
        if chips_df is None or len(chips_df) == 0:
            return 0.0
        total_pct = chips_df['percent'].sum()
        if total_pct < 1e-6:
            return 0.0
        center = (chips_df['price'] * chips_df['percent']).sum() / total_pct
        return float(center)

    def _calc_atr(self, daily_df: pd.DataFrame, period: int = 14) -> pd.Series:
        """计算ATR"""
        if daily_df is None or len(daily_df) < period + 1:
            return pd.Series([0] * len(daily_df) if daily_df is not None else [])
        high = daily_df['high']
        low = daily_df['low']
        close = daily_df['close']
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs()
        ], axis=1).max(axis=1)
        tr = tr.fillna(high - low)
        atr = tr.rolling(window=period, min_periods=1).mean()
        return atr

    def _calc_top3_peaks(self, chips_df: pd.DataFrame) -> List[Tuple[float, float]]:
        """获取Top3筹码峰 [(price, percent), ...]"""
        if chips_df is None or len(chips_df) == 0:
            return []
        top3 = chips_df.nlargest(3, 'percent')
        return [(float(r['price']), float(r['percent'])) for _, r in top3.iterrows()]

    def _calc_80pct_width(self, chips_df: pd.DataFrame) -> float:
        """计算包含80%筹码的价格宽度（归一化）"""
        if chips_df is None or len(chips_df) == 0:
            return 1.0
        sorted_chips = chips_df.sort_values('price')
        total = sorted_chips['percent'].sum()
        if total < 1e-6:
            return 1.0
        cum = sorted_chips['percent'].cumsum()
        # 10%~90% 覆盖80%筹码
        lower_idx = cum[cum >= total * 0.10].index
        upper_idx = cum[cum >= total * 0.90].index
        if len(lower_idx) == 0 or len(upper_idx) == 0:
            return 1.0
        low_price = sorted_chips.loc[lower_idx[0], 'price']
        high_price = sorted_chips.loc[upper_idx[0], 'price']
        median_price = float((low_price + high_price) / 2)
        if median_price < 1e-6:
            return 1.0
        width_pct = (high_price - low_price) / median_price
        return float(width_pct)

    # --------------------------------------------------------
    # 因子 1: Chip Center Velocity（质心迁移速度）[最高优先级]
    # --------------------------------------------------------
    def calc_center_velocity(self, chip_history: Dict, dates_sorted: List[str],
                              prices: List[float]) -> Dict:
        """
        ChipCenter = Σ(price × chip%) / Σ(chip%)
        计算 Center_t, EMA5, EMA20, 线性斜率, 加速度
        核心问题：机构持仓成本是否持续上移？
        """
        centers = []
        for td in dates_sorted:
            chips = chip_history[td].get('chips')
            center = self._calc_chip_center(chips)
            centers.append(center)

        if len(centers) < 3:
            return {'score': 50, 'trend': 'flat', 'change20': 0,
                    'center': centers[-1] if centers else 0, 'details': {'note': '数据不足'}}

        ema5 = _ema(centers, 5)
        ema20 = _ema(centers, min(20, len(centers)))
        slope = _linear_slope(centers)
        # 加速度 = 斜率的变化率
        if len(centers) >= 5:
            slope_first_half = _linear_slope(centers[:len(centers)//2 + 1])
            slope_second_half = _linear_slope(centers[len(centers)//2:])
            acceleration = slope_second_half - slope_first_half
        else:
            acceleration = 0

        latest_price = prices[-1] if prices else centers[-1]
        velocity_pct = _safe_div(slope, latest_price, 0) * 100
        change20 = _safe_div(centers[-1] - centers[0], centers[0], 0) * 100 if centers[0] > 0 else 0

        # EMA趋势判定
        ema_trend = 'up' if ema5[-1] > ema20[-1] else ('down' if ema5[-1] < ema20[-1] else 'flat')
        trend = 'up' if slope > 0 else ('down' if slope < 0 else 'flat')

        # 评分
        if velocity_pct > 1.0:
            score = 95
        elif velocity_pct > 0.5:
            score = 80
        elif velocity_pct > 0.2:
            score = 65
        elif velocity_pct > -0.2:
            score = 50
        elif velocity_pct > -0.5:
            score = 35
        elif velocity_pct > -1.0:
            score = 20
        else:
            score = 10

        # 加速度加分
        if acceleration > 0 and slope > 0:
            score = min(score + 5, 100)
        elif acceleration < 0 and slope > 0:
            score = max(score - 3, 0)

        return {
            'score': round(score, 1),
            'trend': trend,
            'ema_trend': ema_trend,
            'change20': round(change20, 2),
            'center': round(centers[-1], 2),
            'center_ema5': round(ema5[-1], 2),
            'center_ema20': round(ema20[-1], 2),
            'velocity_pct': round(velocity_pct, 3),
            'acceleration': round(acceleration, 4),
            'details': {
                'centers': [round(c, 2) for c in centers],
            }
        }

    # --------------------------------------------------------
    # 因子 2: Chip Peak Migration（Top3峰迁移）
    # --------------------------------------------------------
    def calc_peak_migration(self, chip_history: Dict, dates_sorted: List[str],
                             prices: List[float]) -> Dict:
        """
        Top3筹码峰的迁移距离、速度、稳定性
        多峰合并为单峰 → 加分
        """
        all_peaks = []  # List[List[(price, pct)]]
        for td in dates_sorted:
            chips = chip_history[td].get('chips')
            peaks = self._calc_top3_peaks(chips)
            all_peaks.append(peaks)

        if len(all_peaks) < 3:
            return {'score': 50, 'details': {'note': '数据不足'}}

        # 主峰（Top1）迁移
        main_peaks = [p[0][0] if len(p) > 0 else 0 for p in all_peaks]
        main_pcts = [p[0][1] if len(p) > 0 else 0 for p in all_peaks]

        # 迁移距离
        if len(main_peaks) >= 2 and main_peaks[0] > 0:
            migration_dist = main_peaks[-1] - main_peaks[0]
            migration_pct = _safe_div(migration_dist, main_peaks[0], 0) * 100
        else:
            migration_dist = 0
            migration_pct = 0

        # 迁移速度（斜率）
        slope = _linear_slope(main_peaks)
        latest_price = prices[-1] if prices else (main_peaks[-1] if main_peaks else 1)
        velocity_pct = _safe_div(slope, latest_price, 0) * 100

        # 稳定性：主峰价格的标准差（越小越稳定）
        stability = 1.0 / (1.0 + np.std(main_peaks) / max(np.mean(main_peaks), 1e-6))

        # 峰合并检测：Top1峰占比是否在增大（多峰→单峰）
        merge_score = 0
        if len(main_pcts) >= 2 and main_pcts[0] > 0:
            pct_change = main_pcts[-1] - main_pcts[0]
            if pct_change > 2:
                merge_score = 1  # 峰在合并/集中

        # 评分（以 velocity_pct 为基础）
        if velocity_pct > 0.5:
            base = 85
        elif velocity_pct > 0.2:
            base = 70
        elif velocity_pct > -0.2:
            base = 50
        elif velocity_pct > -0.5:
            base = 35
        else:
            base = 20

        score = base + stability * 10
        if merge_score:
            score = min(score + 10, 100)

        # 迁移幅度修正：峰实际没移动时惩罚高分
        # 防止 velocity_pct 因噪声高位震荡导致虚高（如 migration_pct≈0 但斜率>0.5）
        migration_factor = min(abs(migration_pct) / 3.0, 1.0)  # 3%迁移 = 满因子1.0
        score = round(score * (0.4 + 0.6 * migration_factor), 1)

        return {
            'score': round(min(score, 100), 1),
            'migration_pct': round(migration_pct, 2),
            'velocity_pct': round(velocity_pct, 3),
            'stability': round(stability, 3),
            'merge_detected': merge_score == 1,
            'main_peak_price': round(main_peaks[-1], 2) if main_peaks else 0,
            'main_peak_pct': round(main_pcts[-1], 2) if main_pcts else 0,
            'details': {
                'main_peaks': [round(p, 2) for p in main_peaks],
            }
        }

    # --------------------------------------------------------
    # 因子 3: Winning Expansion Velocity（获利盘扩散速度）
    # --------------------------------------------------------
    def calc_winning_expansion(self, chip_history: Dict, dates_sorted: List[str]) -> Dict:
        """
        不直接用winner_rate，而是计算其 Slope, EMA5, EMA20, 加速度, 连续增加天数
        核心问题：盈利能力在扩张吗？
        """
        winner_rates = []
        for td in dates_sorted:
            perf = chip_history[td].get('perf')
            if perf is None or len(perf) == 0:
                continue
            winner_rates.append(float(perf.iloc[0]['winner_rate']))

        if len(winner_rates) < 3:
            return {'score': 50, 'details': {'note': '数据不足'}}

        ema5 = _ema(winner_rates, 5)
        ema20 = _ema(winner_rates, min(20, len(winner_rates)))
        slope = _linear_slope(winner_rates)

        # 加速度
        if len(winner_rates) >= 6:
            half = len(winner_rates) // 2
            slope_first = _linear_slope(winner_rates[:half + 1])
            slope_second = _linear_slope(winner_rates[half:])
            acceleration = slope_second - slope_first
        else:
            acceleration = 0

        # 连续增加天数
        consecutive_up = 0
        for i in range(len(winner_rates) - 1, 0, -1):
            if winner_rates[i] > winner_rates[i - 1]:
                consecutive_up += 1
            else:
                break

        # EMA趋势
        ema_trend = 'up' if ema5[-1] > ema20[-1] else ('down' if ema5[-1] < ema20[-1] else 'flat')

        # 评分
        if slope > 3:
            score = 95
        elif slope > 1.5:
            score = 80
        elif slope > 0.5:
            score = 65
        elif slope > -0.5:
            score = 50
        elif slope > -1.5:
            score = 35
        elif slope > -3:
            score = 20
        else:
            score = 10

        # 连续增加加分
        if consecutive_up >= 5:
            score = min(score + 5, 100)

        return {
            'score': round(score, 1),
            'velocity': round(slope, 3),
            'acceleration': round(acceleration, 3),
            'consecutive_up_days': consecutive_up,
            'ema_trend': ema_trend,
            'current_winner_rate': round(winner_rates[-1], 2),
            'change': round(winner_rates[-1] - winner_rates[0], 2),
            'details': {
                'winner_rates': [round(w, 2) for w in winner_rates],
            }
        }

    # --------------------------------------------------------
    # 因子 4: Overhead Supply Decay（ATR基准上方压力衰减）
    # --------------------------------------------------------
    def calc_pressure_decay(self, chip_history: Dict, dates_sorted: List[str],
                             daily_df: pd.DataFrame, prices: List[float]) -> Dict:
        """
        不用固定10%，而是计算 Future Resistance = 2×ATR 以上区域
        比较 today vs 20 days ago 的阻力区筹码占比
        """
        atr_series = self._calc_atr(daily_df, period=14)

        above_pcts = []
        for i, td in enumerate(dates_sorted):
            chips = chip_history[td].get('chips')
            if chips is None or len(chips) == 0:
                above_pcts.append(None)
                continue
            cur_price = prices[i] if i < len(prices) else prices[-1]
            # ATR对应
            atr_val = float(atr_series.iloc[i]) if i < len(atr_series) else 0
            if atr_val < 1e-6:
                atr_val = cur_price * 0.03  # 兜底3%
            resistance_zone = cur_price + 2 * atr_val
            # 阻力区内的筹码
            resistance_chips = chips[chips['price'] > resistance_zone]
            above_pct = float(resistance_chips['percent'].sum())
            above_pcts.append(above_pct)

        valid_pcts = [p for p in above_pcts if p is not None]
        if len(valid_pcts) < 3:
            return {'score': 50, 'details': {'note': '数据不足'}}

        current = valid_pcts[-1]
        past = valid_pcts[0]
        slope = _linear_slope(valid_pcts)
        decay_rate = -slope  # 正=衰减

        # 评分：阻力区筹码少 + 快速衰减 = 高分
        if current < 5:
            base = 90
        elif current < 15:
            base = 75
        elif current < 30:
            base = 60
        elif current < 50:
            base = 45
        elif current < 70:
            base = 30
        else:
            base = 15

        if decay_rate > 2:
            score = min(base + 15, 100)
        elif decay_rate > 1:
            score = min(base + 8, 100)
        elif decay_rate > 0.3:
            score = min(base + 4, 100)
        elif decay_rate < -2:
            score = max(base - 15, 0)
        elif decay_rate < -1:
            score = max(base - 8, 0)
        else:
            score = base

        return {
            'score': round(score, 1),
            'decay_rate': round(decay_rate, 3),
            'resistance_chips_pct': round(current, 2),
            'change': round(current - past, 2),
            'details': {
                'above_pcts': [round(p, 2) if p is not None else None for p in above_pcts],
            }
        }

    # --------------------------------------------------------
    # 因子 5: Chip Concentration（80%筹码集中度）
    # --------------------------------------------------------
    def calc_concentration(self, chip_history: Dict, dates_sorted: List[str]) -> Dict:
        """
        计算 80%筹码的价格宽度，归一化
        宽度越小 → 集中度越高 → 分数越高
        """
        widths = []
        for td in dates_sorted:
            chips = chip_history[td].get('chips')
            width = self._calc_80pct_width(chips)
            widths.append(width)

        if len(widths) < 2:
            return {'score': 50, 'details': {'note': '数据不足'}}

        current_width = widths[-1]
        width_change = widths[-1] - widths[0]
        trend = 'tightening' if width_change < -0.02 else ('loosening' if width_change > 0.02 else 'flat')

        # 评分
        if current_width < 0.10:
            base = 95
        elif current_width < 0.20:
            base = 80
        elif current_width < 0.30:
            base = 65
        elif current_width < 0.45:
            base = 50
        elif current_width < 0.60:
            base = 35
        else:
            base = 20

        # 收窄加分
        if width_change < -0.05:
            score = min(base + 10, 100)
        elif width_change < -0.02:
            score = min(base + 5, 100)
        elif width_change > 0.05:
            score = max(base - 10, 0)
        elif width_change > 0.02:
            score = max(base - 5, 0)
        else:
            score = base

        return {
            'score': round(score, 1),
            'width_pct': round(current_width * 100, 2),
            'trend': trend,
            'width_change': round(width_change * 100, 2),
            'details': {
                'widths': [round(w * 100, 2) for w in widths],
            }
        }

    # --------------------------------------------------------
    # 因子 6: Chip Rotation Efficiency CRE（筹码轮换效率）
    # --------------------------------------------------------
    def calc_cre(self, centers: List[float], turnovers: List[float]) -> Dict:
        """
        CRE = ChipCenterChange / AccumulatedTurnover
        高CRE = 机构高效抬升持仓成本
        低CRE = 散户 churn
        """
        if len(centers) < 3 or len(turnovers) < 3:
            return {'score': 50, 'efficiency': 0, 'details': {'note': '数据不足'}}

        center_change = centers[-1] - centers[0]
        accum_turnover = sum(turnovers)

        if accum_turnover < 1e-6:
            cre = 0
        else:
            # 归一化：质心变化% / 累计换手率
            center_change_pct = _safe_div(center_change, centers[0], 0) * 100 if centers[0] > 0 else 0
            cre = _safe_div(center_change_pct, accum_turnover, 0)

        # 评分
        if cre > 0.5:
            score = 95
        elif cre > 0.2:
            score = 80
        elif cre > 0.05:
            score = 65
        elif cre > -0.05:
            score = 50
        elif cre > -0.2:
            score = 35
        elif cre > -0.5:
            score = 20
        else:
            score = 10

        return {
            'score': round(score, 1),
            'efficiency': round(cre, 4),
            'center_change_pct': round(_safe_div(center_change, centers[0], 0) * 100, 2) if centers[0] > 0 else 0,
            'accum_turnover': round(accum_turnover, 2),
            'details': {
                'turnovers': [round(t, 2) for t in turnovers],
            }
        }

    # --------------------------------------------------------
    # 因子 7: Chip Resilience（回调中质心韧性）
    # --------------------------------------------------------
    def calc_resilience(self, centers: List[float], prices: List[float]) -> Dict:
        """
        Resilience = CenterChange / PriceChange（回调期间）
        Price -20% 但 Center -2% → 韧性极高 → 机构没走
        """
        if len(centers) < 5 or len(prices) < 5:
            return {'score': 50, 'resilience_ratio': 0, 'details': {'note': '数据不足'}}

        # 找最大价格回调段
        prices_arr = np.array(prices)
        centers_arr = np.array(centers)

        # 找最高点到最低点
        peak_idx = int(np.argmax(prices_arr))
        trough_idx = int(np.argmin(prices_arr[peak_idx:])) + peak_idx if peak_idx < len(prices_arr) - 1 else len(prices_arr) - 1

        if peak_idx == trough_idx:
            # 无回调，用全段
            peak_idx = 0
            trough_idx = len(prices_arr) - 1

        price_change_pct = _safe_div(prices_arr[trough_idx] - prices_arr[peak_idx], prices_arr[peak_idx], 0) * 100
        center_change_pct = _safe_div(centers_arr[trough_idx] - centers_arr[peak_idx], centers_arr[peak_idx], 0) * 100

        # 韧性比 = |center_change| / |price_change|
        # 价格跌20%，质心只跌2% → ratio = 0.1 → 韧性高
        resilience_ratio = _safe_div(abs(center_change_pct), abs(price_change_pct), 1.0)

        # 评分：ratio越小（质心不动），韧性越高
        if price_change_pct >= 0:
            # 没有回调，中性
            score = 60
        elif resilience_ratio < 0.1:
            score = 95  # 质心几乎不动
        elif resilience_ratio < 0.2:
            score = 85
        elif resilience_ratio < 0.35:
            score = 70
        elif resilience_ratio < 0.5:
            score = 55
        elif resilience_ratio < 0.7:
            score = 40
        else:
            score = 20  # 质心跟着跌，机构在跑

        return {
            'score': round(score, 1),
            'resilience_ratio': round(resilience_ratio, 4),
            'price_change_pct': round(price_change_pct, 2),
            'center_change_pct': round(center_change_pct, 2),
            'peak_price': round(float(prices_arr[peak_idx]), 2),
            'trough_price': round(float(prices_arr[trough_idx]), 2),
            'details': {}
        }

    # --------------------------------------------------------
    # 因子 8: Absorption Quality（吸筹质量）
    # --------------------------------------------------------
    def calc_absorption(self, centers: List[float], daily_df: pd.DataFrame,
                         dates_sorted: List[str], turnovers: List[float]) -> Dict:
        """
        CLV = (Close - Low) / (High - Low)
        放量 + 高CLV + 质心上移 = 吸筹
        放量 + 低CLV + 质心下移 = 派发
        """
        if daily_df is None or len(daily_df) < 3 or len(centers) < 3:
            return {'score': 50, 'signal': 'unknown', 'details': {'note': '数据不足'}}

        # 计算每日CLV
        clv_list = []
        vol_list = []
        center_changes = []
        for i in range(len(dates_sorted)):
            d_row = daily_df[daily_df['trade_date'] == dates_sorted[i]]
            if len(d_row) > 0:
                high = float(d_row.iloc[0]['high'])
                low = float(d_row.iloc[0]['low'])
                close = float(d_row.iloc[0]['close'])
                vol = float(d_row.iloc[0]['vol'])
                clv = _safe_div(close - low, high - low, 0.5)
                clv_list.append(clv)
                vol_list.append(vol)
            else:
                clv_list.append(0.5)
                vol_list.append(0)

            if i > 0:
                center_changes.append(centers[i] - centers[i - 1])
            else:
                center_changes.append(0)

        if len(clv_list) < 3:
            return {'score': 50, 'signal': 'unknown', 'details': {'note': '数据不足'}}

        # 近3日 vs 前3日
        n_recent = min(3, len(vol_list) // 2)
        recent_vol = np.mean(vol_list[-n_recent:])
        prev_vol = np.mean(vol_list[:-n_recent]) if len(vol_list) > n_recent else vol_list[0]
        vol_ratio = _safe_div(recent_vol, prev_vol, 1.0)

        recent_clv = np.mean(clv_list[-n_recent:])
        recent_center_change = np.mean(center_changes[-n_recent:])

        # 吸筹/派发判定
        absorption_signals = 0
        distribution_signals = 0

        for i in range(len(clv_list)):
            if i == 0:
                continue
            is_high_vol = vol_list[i] > np.mean(vol_list[:i]) if i > 0 and np.mean(vol_list[:i]) > 0 else False
            if is_high_vol:
                if clv_list[i] > 0.6 and center_changes[i] > 0:
                    absorption_signals += 1
                elif clv_list[i] < 0.4 and center_changes[i] < 0:
                    distribution_signals += 1

        # 评分
        total_signals = absorption_signals + distribution_signals
        if total_signals > 0:
            absorption_ratio = absorption_signals / total_signals
        else:
            absorption_ratio = 0.5

        # 综合评分
        vol_expansion = vol_ratio > 1.1
        clv_high = recent_clv > 0.55
        center_up = recent_center_change > 0

        if vol_expansion and clv_high and center_up:
            score = 90
            signal = 'strong_absorption'
        elif clv_high and center_up:
            score = 70
            signal = 'absorption'
        elif vol_expansion and not clv_high and not center_up:
            score = 20
            signal = 'distribution'
        elif not clv_high and not center_up:
            score = 35
            signal = 'weak_distribution'
        else:
            score = 50
            signal = 'neutral'

        # 信号比例调整
        score = score * 0.7 + absorption_ratio * 100 * 0.3

        return {
            'score': round(score, 1),
            'signal': signal,
            'vol_ratio': round(vol_ratio, 2),
            'avg_clv': round(recent_clv, 3),
            'center_change_recent': round(recent_center_change, 4),
            'absorption_count': absorption_signals,
            'distribution_count': distribution_signals,
            'details': {
                'clv_list': [round(c, 3) for c in clv_list],
            }
        }

    # --------------------------------------------------------
    # 因子 9: Multi-Day Consistency（多日一致性）
    # --------------------------------------------------------
    def calc_consistency(self, factor_scores_history: Dict) -> Dict:
        """
        计算5/10/20天各因子是否一致改善
        """
        # factor_scores_history: {factor_name: [score_t1, score_t2, ...]}
        consistency_5d = {}
        consistency_10d = {}
        consistency_20d = {}

        for factor, scores in factor_scores_history.items():
            if len(scores) < 2:
                consistency_5d[factor] = 0
                consistency_10d[factor] = 0
                consistency_20d[factor] = 0
                continue

            # 5日一致性
            recent_5 = scores[-min(5, len(scores)):]
            if len(recent_5) >= 2:
                slope_5 = _linear_slope(recent_5)
                consistency_5d[factor] = 1 if slope_5 > 0 else (-1 if slope_5 < 0 else 0)
            else:
                consistency_5d[factor] = 0

            # 10日一致性
            recent_10 = scores[-min(10, len(scores)):]
            if len(recent_10) >= 2:
                slope_10 = _linear_slope(recent_10)
                consistency_10d[factor] = 1 if slope_10 > 0 else (-1 if slope_10 < 0 else 0)
            else:
                consistency_10d[factor] = 0

            # 20日一致性
            slope_20 = _linear_slope(scores)
            consistency_20d[factor] = 1 if slope_20 > 0 else (-1 if slope_20 < 0 else 0)

        # 统计正向因子数
        pos_5 = sum(1 for v in consistency_5d.values() if v > 0)
        pos_10 = sum(1 for v in consistency_10d.values() if v > 0)
        pos_20 = sum(1 for v in consistency_20d.values() if v > 0)
        total = len(consistency_5d) if consistency_5d else 1

        consistency_score = (pos_5 / total * 40 + pos_10 / total * 30 + pos_20 / total * 30)

        return {
            'score': round(consistency_score, 1),
            'positive_5d': pos_5,
            'positive_10d': pos_10,
            'positive_20d': pos_20,
            'total_factors': total,
            'consistency_5d': consistency_5d,
            'consistency_10d': consistency_10d,
            'consistency_20d': consistency_20d,
        }

    # --------------------------------------------------------
    # 因子 10: Chip Momentum（筹码动量）[NEW]
    # 使用Kalman Filter提取质心趋势成分，计算速度与加速度
    # --------------------------------------------------------
    def calc_chip_momentum(self, centers: List[float], prices: List[float]) -> Dict:
        """
        Kalman Filter平滑质心序列，提取趋势成分
        计算趋势速度（一阶导）和加速度（二阶导）
        使用Rolling Z-score标准化
        """
        if len(centers) < 5:
            return {'score': 50, 'momentum': 0, 'acceleration': 0, 'details': {'note': '数据不足'}}

        kf = _kalman_filter_1d(centers, process_noise=0.005, measurement_noise=0.05)

        velocity = []
        for i in range(1, len(kf)):
            velocity.append(kf[i] - kf[i - 1])
        velocity.insert(0, velocity[0] if velocity else 0)

        acceleration = []
        for i in range(1, len(velocity)):
            acceleration.append(velocity[i] - velocity[i - 1])
        acceleration.insert(0, acceleration[0] if acceleration else 0)

        latest_price = prices[-1] if prices else 1
        momentum_pct = _safe_div(velocity[-1], latest_price, 0) * 100
        accel_pct = _safe_div(acceleration[-1], latest_price, 0) * 100

        vel_z = _rolling_zscore(velocity, window=min(10, len(velocity)))[-1]
        accel_z = _rolling_zscore(acceleration, window=min(10, len(velocity)))[-1]

        score = min(vel_z * 10 + 50, 95)
        score = max(score, 5)
        if accel_z > 0:
            score = min(score + accel_z * 5, 100)

        trend = 'up' if velocity[-1] > 0 else ('down' if velocity[-1] < 0 else 'flat')

        return {
            'score': round(score, 1),
            'trend': trend,
            'momentum': round(momentum_pct, 4),
            'acceleration': round(accel_pct, 4),
            'vel_zscore': round(vel_z, 3),
            'accel_zscore': round(accel_z, 3),
            'details': {
                'kf_centers': [round(c, 2) for c in kf],
                'velocity': [round(v, 4) for v in velocity],
            }
        }

    # --------------------------------------------------------
    # 趋势阶段判定
    # --------------------------------------------------------
    def _determine_trend_stage(self, factors: Dict, prices: List[float]) -> str:
        """判定趋势阶段"""
        center_score = factors['center_velocity']['score']
        winning_score = factors['winning_expansion']['score']
        pressure_score = factors['pressure_decay']['score']
        conc_score = factors['concentration']['score']
        absorption = factors['absorption']['signal']
        cre_score = factors['cre']['score']

        # 价格趋势
        price_trend_up = len(prices) >= 2 and prices[-1] > prices[0]

        if absorption == 'distribution' and center_score < 40:
            return 'Distribution'
        elif absorption == 'strong_absorption' and center_score > 60 and pressure_score > 60:
            return 'Accumulation'
        elif center_score > 70 and winning_score > 70 and conc_score > 60:
            return 'Expansion'
        elif center_score > 55 and winning_score > 55 and price_trend_up:
            return 'Early Trend'
        elif center_score < 30 and winning_score < 30 and pressure_score < 30:
            return 'Collapse'
        else:
            return 'Early Trend' if center_score > 50 else 'Accumulation'

    # --------------------------------------------------------
    # 预测概率
    # --------------------------------------------------------
    def _calc_prediction(self, chip_trend_score: float, factors: Dict,
                          trend_stage: str, prices: List[float]) -> Dict:
        """计算预测概率"""
        # 趋势概率
        if trend_stage in ('Expansion', 'Early Trend'):
            trend_prob = min(chip_trend_score * 1.1, 95)
        elif trend_stage == 'Accumulation':
            trend_prob = chip_trend_score * 0.8
        elif trend_stage == 'Distribution':
            trend_prob = max(chip_trend_score * 0.3, 10)
        else:
            trend_prob = chip_trend_score * 0.4

        # 龙头概率（需要多因子共振）
        strong_count = sum(1 for k in ['center_velocity', 'winning_expansion', 'pressure_decay',
                                        'concentration', 'cre', 'chip_momentum']
                           if factors[k]['score'] >= 70)
        leader_prob = min(chip_trend_score * 0.5 + strong_count * 10, 95)

        # 预期持有天数
        if trend_stage == 'Expansion':
            holding_days = '15~20'
        elif trend_stage == 'Early Trend':
            holding_days = '10~15'
        elif trend_stage == 'Accumulation':
            holding_days = '5~10'
        elif trend_stage == 'Distribution':
            holding_days = '0~5'
        else:
            holding_days = '5~10'

        # 趋势失效条件
        invalidation_parts = []
        if trend_stage in ('Early Trend', 'Expansion'):
            invalidation_parts.append('跌破筹码质心（Chip Center）并连续两日放量')
            if factors.get('pressure_decay', {}).get('score', 50) >= 50:
                invalidation_parts.append('压力衰减指标重新恶化（阻力区筹码占比回升）')
        elif trend_stage == 'Accumulation':
            invalidation_parts.append('跌破筹码质心并三日内无法收回')
            invalidation_parts.append('集中度指标趋势由 tightening 转为 loosening')
        elif trend_stage == 'Distribution':
            invalidation_parts.append('TrendStage 维持 Distribution 不变')
            invalidation_parts.append('放量加速下跌')
        else:
            invalidation_parts.append('ChipTrendScore 跌破 40 分')
        invalidation = '；'.join(invalidation_parts) if invalidation_parts else '暂无明确信号'

        return {
            'TrendProbability': round(trend_prob, 1),
            'ExpectedHoldingDays': holding_days,
            'LeaderScore': round(leader_prob, 1),
            'InvalidationCondition': invalidation,
        }

    # --------------------------------------------------------
    # 信号生成
    # --------------------------------------------------------
    def _generate_signals(self, factors: Dict, trend_stage: str) -> List[str]:
        signals = []
        if factors['center_velocity']['score'] >= 65:
            signals.append('筹码质心上移：机构持续抬升持仓成本，多头格局确立')
        if factors['pressure_decay']['score'] >= 65:
            signals.append('上方压力衰减：套牢盘快速消化，阻力区筹码占比下降')
        if factors['absorption']['signal'] in ('strong_absorption', 'absorption'):
            signals.append('主力吸筹信号：放量+高CLV+质心上移三重共振，资金积极承接')
        if factors['winning_expansion']['score'] >= 65:
            signals.append('获利盘加速扩散：盈利筹码占比_slope正向加速，多头力量增强')
        if factors['cre']['score'] >= 65:
            signals.append('筹码轮换高效：单位换手率推动质心上移效率高，机构主导')
        if factors['resilience']['score'] >= 70:
            signals.append('质心韧性极强：回调中价格下跌但质心几乎不动，机构未离场')
        if factors['concentration']['score'] >= 70:
            signals.append('筹码集中度收紧：80%筹码宽度收窄，主力控盘度提升')
        if factors.get('chip_momentum', {}).get('score', 50) >= 65:
            cm = factors['chip_momentum']
            signals.append(f'筹码动量增强：Kalman趋势速度Z={cm.get("vel_zscore", 0):+.2f}，加速度Z={cm.get("accel_zscore", 0):+.2f}')
        if trend_stage in ('Early Trend', 'Expansion'):
            signals.append('趋势龙头候选：多因子共振，符合5~20日趋势领涨特征')
        if factors['center_velocity']['score'] < 35:
            signals.append('警告：筹码质心持续下移，机构可能在降低持仓成本（派发）')
        if factors['absorption']['signal'] == 'distribution':
            signals.append('警告：派发信号显现——放量+低CLV+质心下移，主力出货')
        return signals

    # --------------------------------------------------------
    # 维度评分：将10个因子重组为3个维度
    # --------------------------------------------------------
    def _calc_dimension_scores(self, *factor_scores) -> Dict:
        """将因子分为 趋势动能、筹码质量、量价配合 三个维度"""
        scores = list(factor_scores)
        trend_momentum = np.mean([scores[i] for i in range(min(5, len(scores)))])  # 前5个
        chip_quality = np.mean([scores[i] for i in range(min(4, max(0, len(scores)-4)))])  # 后4个
        if len(scores) >= 3:
            vol_price = np.mean(scores[-3:])
        else:
            vol_price = 50
        return {
            'trend_momentum': round(trend_momentum, 1),
            'chip_quality': round(chip_quality, 1),
            'vol_price_fit': round(vol_price, 1),
        }

    # --------------------------------------------------------
    # 综合分析入口
    # --------------------------------------------------------
    def analyze(self, ts_code: str, end_date: Optional[str] = None,
                lookback_days: int = 20) -> Dict:
        """完整筹码Alpha分析"""
        if end_date is None:
            end_date = datetime.now().strftime('%Y%m%d')
        end_date = str(end_date).replace('-', '')

        print(f"[ChipAlphaV2] 分析 {ts_code}，截止 {end_date}，回溯 {lookback_days} 天")

        trade_dates = _get_trade_dates(end_date, lookback_days)
        start_date = trade_dates[0]

        # 获取数据
        print(f"  获取筹码分布数据...")
        chip_history = self.fetch_chip_history(ts_code, trade_dates)
        print(f"  获取日线行情...")
        daily_df = self.fetch_daily_history(ts_code, start_date, end_date)
        print(f"  获取日线基本指标...")
        daily_basic_df = self.fetch_daily_basic(ts_code, start_date, end_date)

        # 对齐价格和换手率
        prices = []
        turnovers = []
        for td in trade_dates:
            d_row = daily_df[daily_df['trade_date'] == td] if daily_df is not None and len(daily_df) > 0 else pd.DataFrame()
            if len(d_row) > 0:
                prices.append(float(d_row.iloc[0]['close']))
            else:
                prices.append(prices[-1] if prices else 0)

            b_row = daily_basic_df[daily_basic_df['trade_date'] == td] if daily_basic_df is not None and len(daily_basic_df) > 0 else pd.DataFrame()
            if len(b_row) > 0:
                turnovers.append(float(b_row.iloc[0]['turnover_rate']))
            else:
                turnovers.append(0)

        dates_sorted = sorted(chip_history.keys())

        # 预计算质心序列（多因子共用）
        centers = []
        for td in dates_sorted:
            chips = chip_history[td].get('chips')
            centers.append(self._calc_chip_center(chips))

        # 计算各因子
        print(f"  计算因子 1/10 - Chip Center Velocity...")
        f1 = self.calc_center_velocity(chip_history, dates_sorted, prices)

        print(f"  计算因子 2/10 - Chip Peak Migration...")
        f2 = self.calc_peak_migration(chip_history, dates_sorted, prices)

        print(f"  计算因子 3/10 - Winning Expansion...")
        f3 = self.calc_winning_expansion(chip_history, dates_sorted)

        print(f"  计算因子 4/10 - Overhead Supply Decay...")
        f4 = self.calc_pressure_decay(chip_history, dates_sorted, daily_df, prices)

        print(f"  计算因子 5/10 - Chip Concentration...")
        f5 = self.calc_concentration(chip_history, dates_sorted)

        print(f"  计算因子 6/10 - CRE...")
        f6 = self.calc_cre(centers, turnovers)

        print(f"  计算因子 7/10 - Chip Resilience...")
        f7 = self.calc_resilience(centers, prices)

        print(f"  计算因子 8/10 - Absorption Quality...")
        f8 = self.calc_absorption(centers, daily_df, dates_sorted, turnovers)

        print(f"  计算因子 9/10 - Multi-Day Consistency...")
        factor_scores_history = {
            'center_velocity': self._build_factor_history(chip_history, dates_sorted, prices, 'center'),
            'winning_expansion': self._build_factor_history(chip_history, dates_sorted, prices, 'winning'),
            'pressure_decay': self._build_factor_history(chip_history, dates_sorted, prices, 'pressure'),
        }
        f9 = self.calc_consistency(factor_scores_history)

        print(f"  计算因子 10/10 - Chip Momentum (Kalman Filter)...")
        f10 = self.calc_chip_momentum(centers, prices)

        # 汇总因子
        factors = {
            'center_velocity': f1,
            'peak_migration': f2,    # 仅展示，不计入评分
            'winning_expansion': f3,
            'pressure_decay': f4,
            'concentration': f5,
            'cre': f6,
            'resilience': f7,
            'absorption': f8,
            'consistency': f9,       # 仅风险参考，不计入评分
            'chip_momentum': f10,
        }

        # V2.1 综合评分（8因子加权：移除PeakMigration，新增ChipMomentum）
        chip_trend_score = (
            self.WEIGHTS['cre'] * f6['score'] +
            self.WEIGHTS['pressure_decay'] * f4['score'] +
            self.WEIGHTS['chip_momentum'] * f10['score'] +
            self.WEIGHTS['absorption'] * f8['score'] +
            self.WEIGHTS['center_velocity'] * f1['score'] +
            self.WEIGHTS['winning_expansion'] * f3['score'] +
            self.WEIGHTS['resilience'] * f7['score'] +
            self.WEIGHTS['concentration'] * f5['score']
        )

        # 评级
        if chip_trend_score >= 80:
            grade = 'A+'
        elif chip_trend_score >= 65:
            grade = 'A'
        elif chip_trend_score >= 45:
            grade = 'B'
        else:
            grade = 'C'

        # 趋势阶段
        trend_stage = self._determine_trend_stage(factors, prices)

        # 信号
        signals = self._generate_signals(factors, trend_stage)

        # 预测
        prediction = self._calc_prediction(chip_trend_score, factors, trend_stage, prices)

        # 维度评分：将10个因子重组为3个维度
        dim_scores = self._calc_dimension_scores(
            f1['score'], f2['score'], f3['score'],
            f4['score'], f5['score'], f6['score'],
            f7['score'], f8['score'], f10['score']
        )
        # 计算20日价格涨幅
        price_20d_ago = prices[0] if len(prices) > 1 else 0
        price_latest = prices[-1] if prices else 0
        price_return_20d = round((price_latest - price_20d_ago) / price_20d_ago * 100, 2) if price_20d_ago > 0 else 0

        result = {
            'ts_code': ts_code,
            'end_date': end_date,
            'lookback_days': lookback_days,
            'ChipTrendScore': round(chip_trend_score, 1),
            'Grade': grade,
            'TrendStage': trend_stage,
            'DimensionScores': dim_scores,
            'chip_center': round(centers[-1], 2) if centers else 0,
            'price_20d_ago': round(price_20d_ago, 2),
            'price_latest': round(price_latest, 2),
            'price_return_20d': price_return_20d,
            'Factors': {
                'CenterVelocity': {
                    'score': f1['score'],
                    'trend': f1['trend'],
                    'change20': f1['change20'],
                    'ema_trend': f1.get('ema_trend', 'flat'),
                    'velocity_pct': f1.get('velocity_pct', 0),
                    'acceleration': f1.get('acceleration', 0),
                },
                'PeakMigration': {
                    'score': f2['score'],
                    'migration_pct': f2.get('migration_pct', 0),
                    'velocity_pct': f2.get('velocity_pct', 0),
                    'merge_detected': f2.get('merge_detected', False),
                    'stability': f2.get('stability', 0),
                },
                'WinningExpansion': {
                    'score': f3['score'],
                    'velocity': f3.get('velocity', 0),
                    'acceleration': f3.get('acceleration', 0),
                    'consecutive_up_days': f3.get('consecutive_up_days', 0),
                    'ema_trend': f3.get('ema_trend', 'flat'),
                },
                'PressureDecay': {
                    'score': f4['score'],
                    'decay_rate': f4.get('decay_rate', 0),
                    'resistance_chips_pct': f4.get('resistance_chips_pct', 0),
                    'change': f4.get('change', 0),
                },
                'Concentration': {
                    'score': f5['score'],
                    'width_pct': f5.get('width_pct', 0),
                    'trend': f5.get('trend', 'flat'),
                },
                'CRE': {
                    'score': f6['score'],
                    'efficiency': f6.get('efficiency', 0),
                    'center_change_pct': f6.get('center_change_pct', 0),
                    'accum_turnover': f6.get('accum_turnover', 0),
                },
                'Resilience': {
                    'score': f7['score'],
                    'resilience_ratio': f7.get('resilience_ratio', 0),
                    'price_change_pct': f7.get('price_change_pct', 0),
                    'center_change_pct': f7.get('center_change_pct', 0),
                },
                'Absorption': {
                    'score': f8['score'],
                    'signal': f8.get('signal', 'unknown'),
                    'vol_ratio': f8.get('vol_ratio', 0),
                    'avg_clv': f8.get('avg_clv', 0),
                },
                'Consistency': {
                    'score': f9['score'],
                    'positive_5d': f9.get('positive_5d', 0),
                    'positive_10d': f9.get('positive_10d', 0),
                    'positive_20d': f9.get('positive_20d', 0),
                },
                'ChipMomentum': {
                    'score': f10['score'],
                    'trend': f10.get('trend', 'flat'),
                    'momentum': f10.get('momentum', 0),
                    'acceleration': f10.get('acceleration', 0),
                    'vel_zscore': f10.get('vel_zscore', 0),
                    'accel_zscore': f10.get('accel_zscore', 0),
                },
            },
            'Signals': signals,
            'Prediction': prediction,
            'current_price': round(prices[-1], 2) if prices else 0,
            'factor_weights': self.WEIGHTS,
        }

        return result

    def _build_factor_history(self, chip_history: Dict, dates_sorted: List[str],
                               prices: List[float], factor_type: str) -> List[float]:
        """构建因子历史序列（用于一致性计算）"""
        history = []
        n = len(dates_sorted)
        # 用滑动窗口计算
        for end_idx in range(3, n + 1):
            sub_dates = dates_sorted[:end_idx]
            sub_prices = prices[:end_idx]
            sub_history = {td: chip_history[td] for td in sub_dates}

            if factor_type == 'center':
                centers = [self._calc_chip_center(sub_history[td].get('chips')) for td in sub_dates]
                slope = _linear_slope(centers)
                latest_price = sub_prices[-1] if sub_prices else 1
                vel_pct = _safe_div(slope, latest_price, 0) * 100
                score = 95 if vel_pct > 1.0 else (80 if vel_pct > 0.5 else (65 if vel_pct > 0.2 else (50 if vel_pct > -0.2 else (35 if vel_pct > -0.5 else 20))))
                history.append(score)
            elif factor_type == 'winning':
                wr = []
                for td in sub_dates:
                    perf = sub_history[td].get('perf')
                    if perf is not None and len(perf) > 0:
                        wr.append(float(perf.iloc[0]['winner_rate']))
                if len(wr) >= 3:
                    slope = _linear_slope(wr)
                    score = 95 if slope > 3 else (80 if slope > 1.5 else (65 if slope > 0.5 else (50 if slope > -0.5 else (35 if slope > -1.5 else 20))))
                else:
                    score = 50
                history.append(score)
            elif factor_type == 'pressure':
                above_pcts = []
                for i, td in enumerate(sub_dates):
                    chips = sub_history[td].get('chips')
                    if chips is None or len(chips) == 0:
                        continue
                    cur_price = sub_prices[i] if i < len(sub_prices) else sub_prices[-1]
                    above = chips[chips['price'] > cur_price]
                    above_pcts.append(float(above['percent'].sum()))
                if len(above_pcts) >= 3:
                    current = above_pcts[-1]
                    base = 90 if current < 5 else (75 if current < 15 else (60 if current < 30 else (45 if current < 50 else 30)))
                    history.append(base)
                else:
                    history.append(50)

        return history if history else [50]

    # --------------------------------------------------------
    # 维度评分：因子 → 结构/资金/动量 三大维度
    # --------------------------------------------------------
    def _calc_dimension_scores(self, cv_score, pm_score, we_score,
                                pd_score, conc_score, cre_score,
                                res_score, ab_score, cm_score) -> Dict:
        """
        将10个因子（不含Consistency）重组为3个维度：
          • 结构（Structure）：压力衰减+集中度+韧性 → 趋势基础
          • 资金（Flow）：CRE+吸筹质量 → 资金承接
          • 动量（Momentum）：质心速度+筹码动量+获利扩张 → 趋势加速
        PeakMigration 仅展示，不参与加权。
        """
        # 结构：PressureDecay(w=15%) + Resilience(w=5%) + Concentration(w=5%)
        struct_score = (pd_score * 15 + res_score * 5 + conc_score * 5) / 25
        struct_conclusion = (
            "优秀，筹码形态健康" if struct_score >= 80 else
            "良好" if struct_score >= 65 else
            "偏弱" if struct_score >= 50 else
            "差，筹码结构受损"
        )

        # 资金：CRE(w=25%) + Absorption(w=15%)
        flow_score = (cre_score * 25 + ab_score * 15) / 40
        flow_conclusion = (
            "强共振，资金持续承接" if flow_score >= 70 else
            "中性偏强，仍有承接但未形成强共振" if flow_score >= 55 else
            "偏弱，承接不足" if flow_score >= 40 else
            "差，缺乏资金支撑"
        )

        # 动量：CenterVelocity(w=10%) + ChipMomentum(w=15%) + WinningExpansion(w=10%)
        mom_score = (cv_score * 10 + cm_score * 15 + we_score * 10) / 35
        mom_conclusion = (
            "强势加速，动能充足" if mom_score >= 70 else
            "偏强，趋势向上" if mom_score >= 60 else
            "偏弱，短线需要进一步确认" if mom_score >= 45 else
            "动能衰退，回避"
        )

        return {
            'Structure': {'score': round(struct_score, 1), 'conclusion': struct_conclusion},
            'Flow': {'score': round(flow_score, 1), 'conclusion': flow_conclusion},
            'Momentum': {'score': round(mom_score, 1), 'conclusion': mom_conclusion},
        }

    # --------------------------------------------------------
    # 格式化报告
    # --------------------------------------------------------
    def format_report(self, result: Dict) -> str:
        f = result['Factors']
        lines = []

        lines.append("═" * 65)
        lines.append(f"  Chip Alpha Engine V2.1 - {result['ts_code']}")
        lines.append(f"  Date: {result['end_date']}  |  Lookback: {result['lookback_days']}d")
        lines.append("═" * 65)
        lines.append("")

        lines.append(f"  Price: {result['current_price']:.2f}  |  Center: {result.get('chip_center', 0):.2f}（筹码质心/参考支撑）")
        lines.append(f"  ┌─────────────────────────────────────────────")
        lines.append(f"  │ ChipTrendScore: {result['ChipTrendScore']:.1f}  Grade: {result['Grade']}")
        lines.append(f"  │ TrendStage: {result['TrendStage']}")
        pred = result['Prediction']
        lines.append(f"  │ TrendProb: {pred['TrendProbability']:.1f}%  LeaderScore: {pred['LeaderScore']:.1f}")
        lines.append(f"  │ ExpectedHolding: {pred['ExpectedHoldingDays']} days")
        lines.append(f"  │ Invalidation: {pred.get('InvalidationCondition', '')}")
        lines.append(f"  └─────────────────────────────────────────────")
        lines.append("")

        # 维度摘要
        dim = result.get('DimensionScores', {})
        if dim:
            lines.append("─" * 65)
            lines.append("  【三维度质量】")
            lines.append("─" * 65)
            s = dim['Structure']
            f_ = dim['Flow']
            m = dim['Momentum']
            bar_s = "█" * max(1, int(s['score'] / 10)) + "░" * max(0, 10 - max(1, int(s['score'] / 10)))
            bar_f = "█" * max(1, int(f_['score'] / 10)) + "░" * max(0, 10 - max(1, int(f_['score'] / 10)))
            bar_m = "█" * max(1, int(m['score'] / 10)) + "░" * max(0, 10 - max(1, int(m['score'] / 10)))
            lines.append(f"  结构 | {s['score']:5.1f} | {bar_s} {s['conclusion']}")
            lines.append(f"  资金 | {f_['score']:5.1f} | {bar_f} {f_['conclusion']}")
            lines.append(f"  动量 | {m['score']:5.1f} | {bar_m} {m['conclusion']}")
            lines.append("")

        lines.append("─" * 65)
        lines.append("  【10 Dynamic Factors】")
        lines.append("─" * 65)

        # 1. Center Velocity
        cv = f['CenterVelocity']
        trend_arrow = {'up': '↑', 'down': '↓', 'flat': '→'}.get(cv['trend'], '→')
        lines.append(f"  1. Center Velocity  [{cv['score']:.1f}] {trend_arrow}  (w=10%)")
        lines.append(f"     change20: {cv['change20']:+.2f}%  vel: {cv['velocity_pct']:+.3f}%/d")
        lines.append(f"     EMA trend: {cv['ema_trend']}  accel: {cv['acceleration']:+.4f}")

        # 2. Peak Migration [仅展示]
        pm = f['PeakMigration']
        merge_str = ' [MERGING]' if pm['merge_detected'] else ''
        lines.append(f"  2. Peak Migration  [{pm['score']:.1f}]{merge_str}  [仅展示]")
        lines.append(f"     migration: {pm['migration_pct']:+.2f}%  vel: {pm['velocity_pct']:+.3f}%/d")
        lines.append(f"     stability: {pm['stability']:.3f}")

        # 3. Winning Expansion
        we = f['WinningExpansion']
        lines.append(f"  3. Winning Expansion  [{we['score']:.1f}]  (w=10%)")
        lines.append(f"     vel: {we['velocity']:+.3f}%/d  accel: {we['acceleration']:+.3f}")
        lines.append(f"     consecutive_up: {we['consecutive_up_days']}d  EMA: {we['ema_trend']}")

        # 4. Pressure Decay
        pd = f['PressureDecay']
        decay_arrow = '↑' if pd['decay_rate'] > 0 else '↓'
        lines.append(f"  4. Pressure Decay  [{pd['score']:.1f}] {decay_arrow}  (w=15%)")
        lines.append(f"     decay: {pd['decay_rate']:+.3f}%/d  resistance: {pd['resistance_chips_pct']:.2f}%")
        lines.append(f"     change: {pd['change']:+.2f}%")

        # 5. Concentration
        conc = f['Concentration']
        lines.append(f"  5. Concentration  [{conc['score']:.1f}]  (w=5%)")
        lines.append(f"     width: {conc['width_pct']:.2f}%  trend: {conc['trend']}")

        # 6. CRE
        cre = f['CRE']
        lines.append(f"  6. CRE  [{cre['score']:.1f}]")
        lines.append(f"     efficiency: {cre['efficiency']:.4f}  accum_turnover: {cre['accum_turnover']:.1f}%（累计换手率/筹码交换充分度）")

        # 7. Resilience
        res = f['Resilience']
        lines.append(f"  7. Resilience  [{res['score']:.1f}]  (w=5%)")
        lines.append(f"     ratio: {res['resilience_ratio']:.4f}  price: {res['price_change_pct']:.2f}%  center: {res['center_change_pct']:.2f}%")

        # 8. Absorption
        ab = f['Absorption']
        lines.append(f"  8. Absorption  [{ab['score']:.1f}]  {ab['signal']}  (w=15%)")
        lines.append(f"     vol_ratio: {ab['vol_ratio']:.2f}  CLV: {ab['avg_clv']:.3f}")

        # 9. Consistency [仅风险参考]
        cons = f['Consistency']
        lines.append(f"  9. Consistency  [{cons['score']:.1f}]  [风险参考]")
        lines.append(f"     5d: {cons['positive_5d']}/{cons.get('total_factors', 3)}  10d: {cons['positive_10d']}/{cons.get('total_factors', 3)}  20d: {cons['positive_20d']}/{cons.get('total_factors', 3)}")

        # 10. Chip Momentum [NEW]
        cm = f['ChipMomentum']
        cm_arrow = {'up': '↑', 'down': '↓', 'flat': '→'}.get(cm['trend'], '→')
        lines.append(f"  10. Chip Momentum  [{cm['score']:.1f}] {cm_arrow}  ★ (w=15%)")
        lines.append(f"     momentum: {cm['momentum']:+.4f}%/d  accel: {cm['acceleration']:+.4f}%/d")
        lines.append(f"     vel_zscore: {cm['vel_zscore']:+.3f}  accel_zscore: {cm['accel_zscore']:+.3f}")

        # Signals
        lines.append("")
        lines.append("─" * 65)
        lines.append("  【Signals】")
        lines.append("─" * 65)
        if result['Signals']:
            for s in result['Signals']:
                lines.append(f"  • {s}")
        else:
            lines.append("  (no significant signals)")

        lines.append("")
        lines.append("═" * 65)
        return "\n".join(lines)


# ============================================================
# 命令行入口
# ============================================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description='Chip Alpha Engine V2')
    parser.add_argument('ts_code', help='Stock code, e.g. 000729.SZ')
    parser.add_argument('--date', '-d', default=None, help='End date')
    parser.add_argument('--days', '-n', type=int, default=20, help='Lookback days')
    parser.add_argument('--token', '-t', default=None, help='Tushare token')
    parser.add_argument('--json', action='store_true', help='Output JSON')
    args = parser.parse_args()

    engine = ChipAlphaEngineV2(token=args.token)
    result = engine.analyze(args.ts_code, end_date=args.date, lookback_days=args.days)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(engine.format_report(result))


if __name__ == '__main__':
    main()
