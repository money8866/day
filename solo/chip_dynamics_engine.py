"""
Chip Dynamics Engine - 筹码动力引擎
基于 Tushare 筹码分布接口（cyq_chips / cyq_perf），计算7大筹码动力因子：

├── Peak Migration Velocity（筹码峰迁移速度）
├── Chip Center Velocity（筹码重心迁移）
├── Winning Expansion Velocity（获利盘扩散速度）
├── Overhead Supply Decay（上方压力衰减）
├── Chip Concentration（筹码集中度）
├── Chip Rotation Efficiency（筹码轮换效率）⭐
└── Chip Absorption Score（放量吸筹评分）⭐

用法:
    engine = ChipDynamicsEngine(token="your_tushare_token")
    result = engine.analyze("688135.SH", lookback_days=20)
    print(result)
"""
import os
import time
import math
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import pandas as pd
import numpy as np


# ============================================================
# 工具函数：交易日序列生成
# ============================================================
def _get_trade_dates(end_date: str, lookback_days: int) -> List[str]:
    """生成交易日序列（自然日近似，周末跳过）"""
    end_dt = datetime.strptime(str(end_date).replace('-', ''), '%Y%m%d')
    dates = []
    cur = end_dt
    while len(dates) < lookback_days:
        if cur.weekday() < 5:  # 周一到周五
            dates.append(cur.strftime('%Y%m%d'))
        cur -= timedelta(days=1)
    dates.reverse()
    return dates


# ============================================================
# 主引擎
# ============================================================
class ChipDynamicsEngine:
    """
    筹码动力引擎
    
    输入：股票代码 + 回溯天数
    输出：7大筹码动力因子 + 综合评分
    """

    def __init__(self, token: Optional[str] = None, cache_dir: Optional[str] = None):
        """
        Args:
            token: Tushare token，不传则从环境变量 TUSHARE_TOKEN 读取
            cache_dir: 缓存目录，默认 ./chip_cache
        """
        import tushare as ts
        if token is None:
            token = os.environ.get('TUSHARE_TOKEN', '')
        self.token = token
        ts.set_token(token)
        self.pro = ts.pro_api()

        if cache_dir is None:
            cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chip_cache')
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)

        # Tushare 调用节流（120ms 间隔，500次/分上限）
        self._last_call_ts = 0.0
        self._min_interval = 0.12

    # --------------------------------------------------------
    # 节流 & 缓存
    # --------------------------------------------------------
    def _throttle(self):
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
    # 数据获取：多日筹码分布
    # --------------------------------------------------------
    def fetch_chip_history(self, ts_code: str, trade_dates: List[str]) -> Dict[str, Dict]:
        """
        获取多日筹码分布数据
        
        Returns:
            dict: {trade_date: {chips: DataFrame, perf: DataFrame, ...}}
        """
        result = {}
        for td in trade_dates:
            day_data = {}

            # --- cyq_chips ---
            chips_path = self._cache_path(ts_code, td, 'chips')
            chips_df = self._read_cache(chips_path)
            if chips_df is None or len(chips_df) == 0:
                self._throttle()
                try:
                    chips_df = self.pro.cyq_chips(ts_code=ts_code, trade_date=td)
                except Exception as e:
                    print(f"  [筹码] cyq_chips 获取失败 {ts_code} {td}: {e}")
                    chips_df = pd.DataFrame()
                if chips_df is not None and len(chips_df) > 0:
                    self._write_cache(chips_df, chips_path)
            day_data['chips'] = chips_df

            # --- cyq_perf ---
            perf_path = self._cache_path(ts_code, td, 'perf')
            perf_df = self._read_cache(perf_path)
            if perf_df is None or len(perf_df) == 0:
                self._throttle()
                try:
                    perf_df = self.pro.cyq_perf(ts_code=ts_code, trade_date=td)
                except Exception as e:
                    print(f"  [筹码] cyq_perf 获取失败 {ts_code} {td}: {e}")
                    perf_df = pd.DataFrame()
                if perf_df is not None and len(perf_df) > 0:
                    self._write_cache(perf_df, perf_path)
            day_data['perf'] = perf_df

            result[td] = day_data

        return result

    def fetch_daily_history(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """获取日线行情（用于计算换手率、量比等）"""
        cache_path = os.path.join(self.cache_dir, f"daily_{ts_code}_{start_date}_{end_date}.parquet")
        df = self._read_cache(cache_path)
        if df is None or len(df) == 0:
            # V2: 优先 daily_cache 表
            try:
                from stock_cache import get_daily_cache, get_daily_cache_range, batch_insert_daily_cache
                _, _max_date = get_daily_cache_range(ts_code)
                if _max_date is not None and str(_max_date) >= str(end_date):
                    _c = get_daily_cache(ts_code, start_date, end_date)
                    if _c is not None and not _c.empty:
                        _c['trade_date'] = _c['trade_date'].astype(str)
                        df = _c
            except Exception:
                pass
        if df is None or len(df) == 0:
            self._throttle()
            try:
                df = self.pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
            except Exception as e:
                print(f"  [筹码] daily 获取失败 {ts_code}: {e}")
                df = pd.DataFrame()
            if df is not None and len(df) > 0:
                try:
                    from stock_cache import batch_insert_daily_cache
                    batch_insert_daily_cache(df)
                except Exception:
                    pass
                df = df.sort_values('trade_date').reset_index(drop=True)
                self._write_cache(df, cache_path)
        return df

    def fetch_daily_basic(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """获取日线基本指标（换手率、流通市值等）"""
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
    # 因子 1：Peak Migration Velocity（筹码峰迁移速度）
    # --------------------------------------------------------
    def calc_peak_migration_velocity(self, chip_history: Dict[str, Dict], prices: pd.Series) -> Dict:
        """
        筹码峰迁移速度
        
        逻辑：
        - 找到每日筹码分布中占比最高的价格点（主峰）
        - 计算主峰价格随时间的变化速率
        - 方向：向上=筹码上移（多头强势，向下=筹码下移（空头派发）
        
        Returns:
            {
                'score': 0~100,
                'velocity': 元/天（+上移-下移）
                'direction': 'up'/'down'/'flat'
                'peak_price': 最新主峰价格,
                'details': {...}
            }
        """
        peak_prices = []
        peak_percents = []
        dates_sorted = sorted(chip_history.keys())

        for td in dates_sorted:
            chips = chip_history[td].get('chips')
            if chips is None or len(chips) == 0:
                continue
            peak_row = chips.loc[chips['percent'].idxmax()]
            peak_prices.append(float(peak_row['price']))
            peak_percents.append(float(peak_row['percent']))

        if len(peak_prices) < 3:
            return {'score': 50, 'velocity': 0, 'direction': 'flat',
                    'peak_price': peak_prices[-1] if peak_prices else 0,
                    'details': {'note': '数据不足'}}

        # 线性回归斜率 ========================================
        x = np.arange(len(peak_prices))
        y = np.array(peak_prices)
        slope, intercept = np.polyfit(x, y, 1)

        # 归一化到"速度（百分比形式，便于跨股票横向对比
        latest_price = prices.iloc[-1] if len(prices) > 0 else peak_prices[-1]
        velocity_pct = (slope / latest_price) * 100 if latest_price > 0 else 0

        # 评分：上移越快分越高，下移越快分越低
        if velocity_pct > 2.0:
            score = 95
        elif velocity_pct > 1.0:
            score = 80
        elif velocity_pct > 0.3:
            score = 65
        elif velocity_pct > -0.3:
            score = 50
        elif velocity_pct > -1.0:
            score = 35
        elif velocity_pct > -2.0:
            score = 20
        else:
            score = 10

        direction = 'up' if slope > 0 else ('down' if slope < 0 else 'flat')

        return {
            'score': round(score, 1),
            'velocity': round(slope, 4),  # 元/天
            'velocity_pct': round(velocity_pct, 3),  # %/天
            'direction': direction,
            'peak_price': round(peak_prices[-1], 2),
            'peak_percent': round(peak_percents[-1], 2),
            'peak_price_5d_ago': round(peak_prices[0], 2),
            'details': {
                'peak_prices': [round(p, 2) for p in peak_prices],
                'peak_percents': [round(p, 2) for p in peak_percents],
            }
        }

    # --------------------------------------------------------
    # 因子 2：Chip Center Velocity（筹码重心迁移）
    # --------------------------------------------------------
    def calc_chip_center_velocity(self, chip_history: Dict[str, Dict], prices: pd.Series) -> Dict:
        """
        筹码重心迁移速度
        
        逻辑：
        - 使用 cyq_perf 中的 weight_avg（加权平均成本）作为筹码重心
        - 计算重心随时间的变化速率
        - 重心上移=市场平均成本抬升，多头强势
        
        Returns:
            {
                'score': 0~100,
                'velocity': 元/天,
                'velocity_pct': %/天,
                'direction': 'up'/'down'/'flat',
                'avg_cost': 最新平均成本,
                'details': {...}
            }
        """
        avg_costs = []
        dates_sorted = sorted(chip_history.keys())

        for td in dates_sorted:
            perf = chip_history[td].get('perf')
            if perf is None or len(perf) == 0:
                continue
            avg_costs.append(float(perf.iloc[0]['weight_avg']))

        if len(avg_costs) < 3:
            return {'score': 50, 'velocity': 0, 'direction': 'flat',
                    'avg_cost': avg_costs[-1] if avg_costs else 0,
                    'details': {'note': '数据不足'}}

        # 线性回归斜率
        x = np.arange(len(avg_costs))
        y = np.array(avg_costs)
        slope, intercept = np.polyfit(x, y, 1)

        latest_price = prices.iloc[-1] if len(prices) > 0 else avg_costs[-1]
        velocity_pct = (slope / latest_price) * 100 if latest_price > 0 else 0

        # 重心 vs 当前价 的位置关系
        cost_vs_price = (latest_price - avg_costs[-1]) / avg_costs[-1] * 100 if avg_costs[-1] > 0 else 0

        # 评分：重心上移且股价在重心之上=高分
        base_score = 50
        if velocity_pct > 1.5:
            base_score = 90
        elif velocity_pct > 0.8:
            base_score = 75
        elif velocity_pct > 0.2:
            base_score = 60
        elif velocity_pct > -0.2:
            base_score = 50
        elif velocity_pct > -0.8:
            base_score = 40
        elif velocity_pct > -1.5:
            base_score = 25
        else:
            base_score = 15

        # 股价在平均成本之上加分
        if cost_vs_price > 10:
            score = min(base_score + 10, 100)
        elif cost_vs_price > 0:
            score = min(base_score + 5, 100)
        else:
            score = base_score

        direction = 'up' if slope > 0 else ('down' if slope < 0 else 'flat')

        return {
            'score': round(score, 1),
            'velocity': round(slope, 4),
            'velocity_pct': round(velocity_pct, 3),
            'direction': direction,
            'avg_cost': round(avg_costs[-1], 2),
            'cost_vs_price_pct': round(cost_vs_price, 2),
            'details': {
                'avg_costs': [round(c, 2) for c in avg_costs],
            }
        }

    # --------------------------------------------------------
    # 因子 3：Winning Expansion Velocity（获利盘扩散速度）
    # --------------------------------------------------------
    def calc_winning_expansion_velocity(self, chip_history: Dict[str, Dict]) -> Dict:
        """
        获利盘扩散速度
        
        逻辑：
        - 使用 cyq_perf 中的 winner_rate（盈利筹码占比）
        - 计算获利盘比例的日变化率
        - 获利盘快速扩大=多头力量增强
        
        Returns:
            {
                'score': 0~100,
                'velocity': %/天（获利盘比例变化速率）,
                'winner_rate': 最新获利盘比例,
                'details': {...}
            }
        """
        winner_rates = []
        dates_sorted = sorted(chip_history.keys())

        for td in dates_sorted:
            perf = chip_history[td].get('perf')
            if perf is None or len(perf) == 0:
                continue
            winner_rates.append(float(perf.iloc[0]['winner_rate']))

        if len(winner_rates) < 3:
            return {'score': 50, 'velocity': 0, 'winner_rate': winner_rates[-1] if winner_rates else 0,
                    'details': {'note': '数据不足'}}

        # 线性回归斜率（%/天）
        x = np.arange(len(winner_rates))
        y = np.array(winner_rates)
        slope, intercept = np.polyfit(x, y, 1)

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

        return {
            'score': round(score, 1),
            'velocity': round(slope, 3),  # %/天
            'winner_rate': round(winner_rates[-1], 2),
            'winner_rate_5d_ago': round(winner_rates[0], 2),
            'change_5d': round(winner_rates[-1] - winner_rates[0], 2),
            'details': {
                'winner_rates': [round(w, 2) for w in winner_rates],
            }
        }

    # --------------------------------------------------------
    # 因子 4：Overhead Supply Decay（上方压力衰减）
    # --------------------------------------------------------
    def calc_overhead_supply_decay(self, chip_history: Dict[str, Dict], prices: pd.Series) -> Dict:
        """
        上方压力衰减速度
        
        逻辑：
        - 计算当前价上方套牢盘比例的变化
        - 套牢盘快速减少=上方压力减轻，利于上涨
        - 同时考虑套牢盘绝对量和衰减速率
        
        Returns:
            {
                'score': 0~100,
                'decay_rate': %/天（套牢盘减少速率，正=减少）,
                'above_pct': 最新上方套牢盘比例,
                'details': {...}
            }
        """
        above_pcts = []
        dates_sorted = sorted(chip_history.keys())

        for i, td in enumerate(dates_sorted):
            chips = chip_history[td].get('chips')
            if chips is None or len(chips) == 0:
                continue
            # 用当日收盘价作为基准
            if i < len(prices):
                cur_price = prices.iloc[i]
            else:
                cur_price = prices.iloc[-1]
            above = chips[chips['price'] > cur_price]
            above_pct = float(above['percent'].sum())
            above_pcts.append(above_pct)

        if len(above_pcts) < 3:
            return {'score': 50, 'decay_rate': 0, 'above_pct': above_pcts[-1] if above_pcts else 0,
                    'details': {'note': '数据不足'}}

        # 斜率：负=套牢盘减少（好），正=套牢盘增加（坏）
        x = np.arange(len(above_pcts))
        y = np.array(above_pcts)
        slope, intercept = np.polyfit(x, y, 1)

        # 衰减速率（正号反过来：slope为负表示衰减，正值表示套牢盘增加
        decay_rate = -slope  # 正=压力衰减（好），负=压力增加（坏）

        # 绝对套牢盘比例
        current_above = above_pcts[-1]

        # 综合评分：套牢盘少 + 快速衰减 = 高分
        # 基础分由绝对套牢盘比例决定
        if current_above < 10:
            base_score = 90
        elif current_above < 25:
            base_score = 75
        elif current_above < 40:
            base_score = 60
        elif current_above < 60:
            base_score = 45
        elif current_above < 80:
            base_score = 30
        else:
            base_score = 15

        # 衰减速率调整
        if decay_rate > 3:
            score = min(base_score + 20, 100)
        elif decay_rate > 1.5:
            score = min(base_score + 10, 100)
        elif decay_rate > 0.5:
            score = min(base_score + 5, 100)
        elif decay_rate < -3:
            score = max(base_score - 20, 0)
        elif decay_rate < -1.5:
            score = max(base_score - 10, 0)
        elif decay_rate < -0.5:
            score = max(base_score - 5, 0)
        else:
            score = base_score

        return {
            'score': round(score, 1),
            'decay_rate': round(decay_rate, 3),  # %/天，正=压力衰减
            'above_pct': round(current_above, 2),
            'above_pct_5d_ago': round(above_pcts[0], 2),
            'change_5d': round(current_above - above_pcts[0], 2),
            'details': {
                'above_pcts': [round(a, 2) for a in above_pcts],
            }
        }

    # --------------------------------------------------------
    # 因子 5：Chip Concentration（筹码集中度）
    # --------------------------------------------------------
    def calc_chip_concentration(self, chip_history: Dict[str, Dict]) -> Dict:
        """
        筹码集中度
        
        逻辑：
        - 使用成本分布的宽度（成本区间的集中程度
        - 使用 90%成本区间宽度/中位数 = 成本分布越窄= 集中度越高
        - 同时看趋势：集中度提高=主力吸筹，集中度下降=主力派发
        
        Returns:
            {
                'score': 0~100,
                'concentration_ratio': 集中度比率(0~1,越高越集中),
                'cost_width_pct': 90%成本宽度/中位数,
                'trend': 'increasing'/'decreasing'/'flat',
                'details': {...}
            }
        """
        concentrations = []
        dates_sorted = sorted(chip_history.keys())

        for td in dates_sorted:
            perf = chip_history[td].get('perf')
            if perf is None or len(perf) == 0:
                continue
            row = perf.iloc[0]
            # 90%成本区间
            cost_5 = float(row['cost_5pct'])
            cost_95 = float(row['cost_95pct'])
            cost_50 = float(row['cost_50pct'])

            # 成本宽度 / 中位数（越小越集中）
            if cost_50 > 0:
                width_pct = (cost_95 - cost_5) / cost_50
            else:
                width_pct = 1.0

            # 集中度 = 1 - 宽度/中位数（归一化）
            # 一般股票90%成本宽度通常在20%~80%之间
            conc = max(0, 1 - width_pct)
            concentrations.append({
                'width_pct': width_pct,
                'concentration': conc,
                'cost_5': cost_5,
                'cost_50': cost_50,
                'cost_95': cost_95,
            })

        if len(concentrations) < 2:
            return {'score': 50, 'concentration_ratio': 0, 'cost_width_pct': 0,
                    'trend': 'flat', 'details': {'note': '数据不足'}}

        # 最新集中度
        latest = concentrations[-1]
        first = concentrations[0]

        # 集中度趋势
        conc_change = latest['concentration'] - first['concentration']
        trend = 'increasing' if conc_change > 0 else ('decreasing' if conc_change < 0 else 'flat')

        # 评分：集中度越高越好，且集中度在提高更好
        width = latest['width_pct']
        if width < 0.15:
            base_score = 90
        elif width < 0.25:
            base_score = 75
        elif width < 0.40:
            base_score = 60
        elif width < 0.60:
            base_score = 45
        elif width < 0.80:
            base_score = 30
        else:
            base_score = 15

        # 趋势调整
        if conc_change > 0.10:
            score = min(base_score + 15, 100)
        elif conc_change > 0.03:
            score = min(base_score + 8, 100)
        elif conc_change > -0.03:
            score = base_score
        elif conc_change > -0.10:
            score = max(base_score - 8, 0)
        else:
            score = max(base_score - 15, 0)

        return {
            'score': round(score, 1),
            'concentration_ratio': round(latest['concentration'], 4),
            'cost_width_pct': round(latest['width_pct'] * 100, 2),
            'cost_median': round(latest['cost_50'], 2),
            'cost_90_range': [round(latest['cost_5'], 2), round(latest['cost_95'], 2)],
            'trend': trend,
            'concentration_change': round(conc_change, 4),
            'details': {
                'widths_pct': [round(c['width_pct'] * 100, 2) for c in concentrations],
            }
        }

    # --------------------------------------------------------
    # 因子 6：Chip Rotation Efficiency（筹码轮换效率）⭐
    # --------------------------------------------------------
    def calc_chip_rotation_efficiency(self, chip_history: Dict[str, Dict],
                                       daily_df: pd.DataFrame,
                                       daily_basic_df: pd.DataFrame) -> Dict:
        """
        筹码轮换效率 ⭐
        
        逻辑：
        - 换手率 vs 筹码分布变化量的比值
        - 高换手率但筹码分布变化小 = 筹码在高位充分换手（良性换手不出货）
        - 低换手率但筹码分布变化大 = 筹码快速松动（恐慌/出货）
        - 效率高 = 同样的换手率下，筹码峰移动越小，说明筹码锁定好
        
        公式：
        轮换效率 = 1 - (筹码分布变化量 / 换手率)
        筹码分布变化量 = 两日筹码分布的KL散度或最大变化
        
        Returns:
            {
                'score': 0~100,
                'efficiency': 轮换效率值,
                'avg_turnover': 平均换手率,
                'details': {...}
            }
        """
        dates_sorted = sorted(chip_history.keys())

        if len(dates_sorted) < 3 or daily_df is None or len(daily_df) < 3:
            return {'score': 50, 'efficiency': 0, 'avg_turnover': 0,
                    'details': {'note': '数据不足'}}

        # 计算每日筹码分布变化
        chip_changes = []
        turnovers = []

        for i in range(1, len(dates_sorted)):
            prev_td = dates_sorted[i - 1]
            curr_td = dates_sorted[i]

            prev_chips = chip_history[prev_td].get('chips')
            curr_chips = chip_history[curr_td].get('chips')

            if prev_chips is None or curr_chips is None:
                continue
            if len(prev_chips) == 0 or len(curr_chips) == 0:
                continue

            # 合并价格区间，计算分布变化
            merged = pd.merge(prev_chips[['price', 'percent']],
                            curr_chips[['price', 'percent']],
                            on='price', how='outer', suffixes=('_prev', '_curr'))
            merged = merged.fillna(0)

            # 总变化量（L1距离）
            change_amount = abs(merged['percent_curr'] - merged['percent_prev']).sum() / 2
            chip_changes.append(change_amount)

            # 当日换手率
            daily_row = daily_basic_df[daily_basic_df['trade_date'] == curr_td]
            if len(daily_row) > 0:
                turnover = float(daily_row.iloc[0]['turnover_rate'])
            else:
                turnover = 0
            turnovers.append(turnover)

        if len(chip_changes) == 0 or len(turnovers) == 0:
            return {'score': 50, 'efficiency': 0, 'avg_turnover': 0,
                    'details': {'note': '无法计算'}}

        avg_change = np.mean(chip_changes)
        avg_turnover = np.mean(turnovers)

        # 轮换效率 = 1 - (筹码变化量 / 换手率)
        # 换手率越高，筹码变化越小 = 效率越高（筹码锁定好）
        if avg_turnover > 0:
            efficiency = max(0, 1 - avg_change / avg_turnover)
        else:
            efficiency = 0.5

        # 评分
        if efficiency > 0.7:
            score = 90
        elif efficiency > 0.5:
            score = 75
        elif efficiency > 0.3:
            score = 60
        elif efficiency > 0.1:
            score = 45
        elif efficiency > -0.1:
            score = 30
        else:
            score = 15

        return {
            'score': round(score, 1),
            'efficiency': round(efficiency, 4),
            'avg_turnover': round(avg_turnover, 2),
            'avg_chip_change': round(avg_change, 2),
            'details': {
                'chip_changes': [round(c, 2) for c in chip_changes],
                'turnovers': [round(t, 2) for t in turnovers],
            }
        }

    # --------------------------------------------------------
    # 因子 7：Chip Absorption Score（放量吸筹评分）⭐
    # --------------------------------------------------------
    def calc_chip_absorption_score(self, chip_history: Dict[str, Dict],
                                    daily_df: pd.DataFrame,
                                    daily_basic_df: pd.DataFrame) -> Dict:
        """
        放量吸筹评分 ⭐
        
        逻辑：
        - 成交量放大时，获利盘是否增加？
        - 成交量放大时，筹码集中度是否提高？
        - 成交量放大时，筹码重心是否上移？
        - 三者共振 = 放量吸筹，主力进场
        
        评分维度：
        ① 量价配合（放量+上涨）
        ② 放量时获利盘增加
        ③ 放量时集中度提高
        ④ 放量时重心上移
        
        Returns:
            {
                'score': 0~100,
                'absorption_signal': 'strong'/'moderate'/'weak'/'distribution',
                'volume_ratio': 量比,
                'details': {...}
            }
        """
        dates_sorted = sorted(chip_history.keys())

        if len(dates_sorted) < 5 or daily_df is None or len(daily_df) < 5:
            return {'score': 50, 'absorption_signal': 'unknown', 'volume_ratio': 0,
                    'details': {'note': '数据不足'}}

        # 获取价格和成交量
        prices = []
        volumes = []
        turnovers = []
        for td in dates_sorted:
            d_row = daily_df[daily_df['trade_date'] == td]
            if len(d_row) > 0:
                prices.append(float(d_row.iloc[0]['close']))
                volumes.append(float(d_row.iloc[0]['vol']))
            else:
                prices.append(None)
                volumes.append(None)

            b_row = daily_basic_df[daily_basic_df['trade_date'] == td]
            if len(b_row) > 0:
                turnovers.append(float(b_row.iloc[0]['turnover_rate']))
            else:
                turnovers.append(0)

        # 过滤无效数据
        valid_idx = [i for i, p in enumerate(prices) if p is not None]
        if len(valid_idx) < 3:
            return {'score': 50, 'absorption_signal': 'unknown', 'volume_ratio': 0,
                    'details': {'note': '有效价格数据不足'}}

        valid_prices = [prices[i] for i in valid_idx]
        valid_volumes = [volumes[i] for i in valid_idx]
        valid_turnovers = [turnovers[i] for i in valid_idx]

        # 最近几天 vs 前几天
        n_recent = min(3, len(valid_idx) // 2)
        recent_vol = np.mean(valid_volumes[-n_recent:])
        prev_vol = np.mean(valid_volumes[:-n_recent]) if len(valid_volumes) > n_recent else valid_volumes[0]
        volume_ratio = recent_vol / prev_vol if prev_vol > 0 else 1.0

        recent_price = np.mean(valid_prices[-n_recent:])
        prev_price = np.mean(valid_prices[:-n_recent]) if len(valid_prices) > n_recent else valid_prices[0]
        price_change_pct = (recent_price - prev_price) / prev_price * 100 if prev_price > 0 else 0

        # --- 筹码维度 ---
        # 获利盘变化
        winner_rates = []
        conc_ratios = []
        avg_costs = []
        for td in dates_sorted:
            perf = chip_history[td].get('perf')
            if perf is None or len(perf) == 0:
                continue
            row = perf.iloc[0]
            winner_rates.append(float(row['winner_rate']))
            cost_5 = float(row['cost_5pct'])
            cost_95 = float(row['cost_95pct'])
            cost_50 = float(row['cost_50pct'])
            width_pct = (cost_95 - cost_5) / cost_50 if cost_50 > 0 else 1
            conc_ratios.append(max(0, 1 - width_pct))
            avg_costs.append(float(row['weight_avg']))

        sub_scores = {}

        # ① 量价配合（30%权重）
        if volume_ratio > 2 and price_change_pct > 3:
            sub_scores['volume_price'] = 90
        elif volume_ratio > 1.5 and price_change_pct > 1:
            sub_scores['volume_price'] = 75
        elif volume_ratio > 1.2 and price_change_pct > 0:
            sub_scores['volume_price'] = 60
        elif volume_ratio > 0.8 and price_change_pct > -2:
            sub_scores['volume_price'] = 45
        elif volume_ratio < 0.7 and price_change_pct < -3:
            sub_scores['volume_price'] = 20
        else:
            sub_scores['volume_price'] = 35

        # ② 放量时获利盘增加（25%权重）
        if len(winner_rates) >= 3:
            wr_change = winner_rates[-1] - winner_rates[0]
            if wr_change > 15 and volume_ratio > 1.3:
                sub_scores['winner_expansion'] = 90
            elif wr_change > 8 and volume_ratio > 1.1:
                sub_scores['winner_expansion'] = 75
            elif wr_change > 3:
                sub_scores['winner_expansion'] = 60
            elif wr_change > -3:
                sub_scores['winner_expansion'] = 45
            elif wr_change > -8:
                sub_scores['winner_expansion'] = 30
            else:
                sub_scores['winner_expansion'] = 15
        else:
            sub_scores['winner_expansion'] = 50

        # ③ 放量时集中度提高（25%权重）
        if len(conc_ratios) >= 3:
            conc_change = conc_ratios[-1] - conc_ratios[0]
            if conc_change > 0.08 and volume_ratio > 1.3:
                sub_scores['concentration'] = 90
            elif conc_change > 0.03 and volume_ratio > 1.1:
                sub_scores['concentration'] = 75
            elif conc_change > 0:
                sub_scores['concentration'] = 60
            elif conc_change > -0.03:
                sub_scores['concentration'] = 45
            elif conc_change > -0.08:
                sub_scores['concentration'] = 30
            else:
                sub_scores['concentration'] = 15
        else:
            sub_scores['concentration'] = 50

        # ④ 放量时重心上移（20%权重）
        if len(avg_costs) >= 3:
            cost_change_pct = (avg_costs[-1] - avg_costs[0]) / avg_costs[0] * 100 if avg_costs[0] > 0 else 0
            if cost_change_pct > 5 and volume_ratio > 1.3:
                sub_scores['center_shift'] = 90
            elif cost_change_pct > 2 and volume_ratio > 1.1:
                sub_scores['center_shift'] = 75
            elif cost_change_pct > 0.5:
                sub_scores['center_shift'] = 60
            elif cost_change_pct > -0.5:
                sub_scores['center_shift'] = 45
            elif cost_change_pct > -2:
                sub_scores['center_shift'] = 30
            else:
                sub_scores['center_shift'] = 15
        else:
            sub_scores['center_shift'] = 50

        # 综合评分
        total_score = (
            0.30 * sub_scores['volume_price'] +
            0.25 * sub_scores['winner_expansion'] +
            0.25 * sub_scores['concentration'] +
            0.20 * sub_scores['center_shift']
        )

        # 信号判定
        if total_score >= 80:
            signal = 'strong'
        elif total_score >= 60:
            signal = 'moderate'
        elif total_score >= 40:
            signal = 'weak'
        else:
            signal = 'distribution'

        return {
            'score': round(total_score, 1),
            'absorption_signal': signal,
            'volume_ratio': round(volume_ratio, 2),
            'price_change_pct': round(price_change_pct, 2),
            'sub_scores': sub_scores,
            'details': {
                'prices': [round(p, 2) for p in valid_prices if p is not None],
                'volumes': [round(v, 0) for v in valid_volumes if v is not None],
            }
        }

    # --------------------------------------------------------
    # 综合分析入口
    # --------------------------------------------------------
    def analyze(self, ts_code: str, end_date: Optional[str] = None,
                lookback_days: int = 20) -> Dict:
        """
        完整筹码动力分析
        
        Args:
            ts_code: 股票代码，如 '688135.SH'
            end_date: 截止日期，默认今天
            lookback_days: 回溯天数，默认20天
            
        Returns:
            包含7大因子和综合评分的字典
        """
        if end_date is None:
            end_date = datetime.now().strftime('%Y%m%d')
        end_date = str(end_date).replace('-', '')

        print(f"[ChipDynamics] 分析 {ts_code}，截止日期 {end_date}，回溯 {lookback_days} 天")

        # 生成交易日序列
        trade_dates = _get_trade_dates(end_date, lookback_days)
        start_date = trade_dates[0]

        # 获取筹码历史数据
        print(f"  获取筹码分布数据...")
        chip_history = self.fetch_chip_history(ts_code, trade_dates)

        # 获取日线数据
        print(f"  获取日线行情...")
        daily_df = self.fetch_daily_history(ts_code, start_date, end_date)

        # 获取日线基本指标
        print(f"  获取日线基本指标...")
        daily_basic_df = self.fetch_daily_basic(ts_code, start_date, end_date)

        # 价格序列（按交易日对齐）
        prices = []
        for td in trade_dates:
            row = daily_df[daily_df['trade_date'] == td]
            if len(row) > 0:
                prices.append(float(row.iloc[0]['close']))
            else:
                # 用前一个价格填充
                prices.append(prices[-1] if prices else 0)
        prices = pd.Series(prices)

        # 计算各因子
        print(f"  计算因子 1/7 - 筹码峰迁移速度...")
        f1 = self.calc_peak_migration_velocity(chip_history, prices)

        print(f"  计算因子 2/7 - 筹码重心迁移...")
        f2 = self.calc_chip_center_velocity(chip_history, prices)

        print(f"  计算因子 3/7 - 获利盘扩散速度...")
        f3 = self.calc_winning_expansion_velocity(chip_history)

        print(f"  计算因子 4/7 - 上方压力衰减...")
        f4 = self.calc_overhead_supply_decay(chip_history, prices)

        print(f"  计算因子 5/7 - 筹码集中度...")
        f5 = self.calc_chip_concentration(chip_history)

        print(f"  计算因子 6/7 - 筹码轮换效率...")
        f6 = self.calc_chip_rotation_efficiency(chip_history, daily_df, daily_basic_df)

        print(f"  计算因子 7/7 - 放量吸筹评分...")
        f7 = self.calc_chip_absorption_score(chip_history, daily_df, daily_basic_df)

        # 综合评分（加权）
        weights = {
            'peak_migration': 0.12,      # 筹码峰迁移
            'chip_center': 0.15,       # 筹码重心
            'winning_expansion': 0.15, # 获利盘扩散
            'overhead_decay': 0.13,      # 上方压力衰减
            'concentration': 0.15,     # 筹码集中度
            'rotation_efficiency': 0.15, # 筹码轮换效率 ⭐
            'absorption': 0.15,         # 放量吸筹 ⭐
        }

        total_score = (
            weights['peak_migration'] * f1['score'] +
            weights['chip_center'] * f2['score'] +
            weights['winning_expansion'] * f3['score'] +
            weights['overhead_decay'] * f4['score'] +
            weights['concentration'] * f5['score'] +
            weights['rotation_efficiency'] * f6['score'] +
            weights['absorption'] * f7['score']
        )

        # 综合评级
        if total_score >= 80:
            rating = '极强动力'
        elif total_score >= 65:
            rating = '强势动力'
        elif total_score >= 50:
            rating = '中性偏强'
        elif total_score >= 35:
            rating = '中性偏弱'
        elif total_score >= 20:
            rating = '弱势动力'
        else:
            rating = '极弱动力'

        result = {
            'ts_code': ts_code,
            'end_date': end_date,
            'lookback_days': lookback_days,
            'total_score': round(total_score, 1),
            'rating': rating,
            'current_price': round(prices.iloc[-1], 2) if len(prices) > 0 else 0,
            'factors': {
                'peak_migration_velocity': f1,
                'chip_center_velocity': f2,
                'winning_expansion_velocity': f3,
                'overhead_supply_decay': f4,
                'chip_concentration': f5,
                'chip_rotation_efficiency': f6,
                'chip_absorption_score': f7,
            },
            'factor_weights': weights,
        }

        return result

    # --------------------------------------------------------
    # 格式化输出
    # --------------------------------------------------------
    def format_report(self, result: Dict) -> str:
        """生成可读的文本报告"""
        f = result['factors']
        lines = []

        lines.append("═" * 60)
        lines.append(f"  筹码动力引擎分析报告 - {result['ts_code']}")
        lines.append(f"  截止日期: {result['end_date']}  |  回溯: {result['lookback_days']}天")
        lines.append("═" * 60)
        lines.append("")

        lines.append(f"  当前股价: {result['current_price']:.2f} 元")
        lines.append(f"  综合评分: {result['total_score']:.1f} 分  【{result['rating']}】")
        lines.append("")

        lines.append("─" * 60)
        lines.append("  【七大因子明细】")
        lines.append("─" * 60)

        # 1. 筹码峰迁移速度
        f1 = f['peak_migration_velocity']
        direction_cn = {'up': '↑上移', 'down': '↓下移', 'flat': '→平移'}[f1['direction']]
        lines.append(f"  1. 筹码峰迁移速度  [{f1['score']:.1f}分]")
        lines.append(f"     方向: {direction_cn}  速度: {f1['velocity_pct']:+.3f}%/天")
        lines.append(f"     主峰价格: {f1['peak_price']:.2f}元 (占比 {f1['peak_percent']:.2f}%)")

        # 2. 筹码重心迁移
        f2 = f['chip_center_velocity']
        direction_cn = {'up': '↑上移', 'down': '↓下移', 'flat': '→平移'}[f2['direction']]
        lines.append(f"  2. 筹码重心迁移  [{f2['score']:.1f}分]")
        lines.append(f"     方向: {direction_cn}  速度: {f2['velocity_pct']:+.3f}%/天")
        lines.append(f"     平均成本: {f2['avg_cost']:.2f}元  (股价 vs 成本: {f2['cost_vs_price_pct']:+.2f}%)")

        # 3. 获利盘扩散速度
        f3 = f['winning_expansion_velocity']
        lines.append(f"  3. 获利盘扩散速度  [{f3['score']:.1f}分]")
        lines.append(f"     变化速率: {f3['velocity']:+.3f}%/天")
        lines.append(f"     当前获利盘: {f3['winner_rate']:.2f}%  (5日变化: {f3['change_5d']:+.2f}%)")

        # 4. 上方压力衰减
        f4 = f['overhead_supply_decay']
        lines.append(f"  4. 上方压力衰减  [{f4['score']:.1f}分]")
        decay_desc = '↑衰减加快' if f4['decay_rate'] > 0 else '↓压力增加'
        lines.append(f"     衰减速率: {f4['decay_rate']:+.3f}%/天  {decay_desc}")
        lines.append(f"     当前套牢盘: {f4['above_pct']:.2f}%  (5日变化: {f4['change_5d']:+.2f}%)")

        # 5. 筹码集中度
        f5 = f['chip_concentration']
        trend_cn = {'increasing': '↑提高', 'decreasing': '↓下降', 'flat': '→平稳'}[f5['trend']]
        lines.append(f"  5. 筹码集中度  [{f5['score']:.1f}分]")
        lines.append(f"     趋势: {trend_cn}  90%成本宽度: {f5['cost_width_pct']:.2f}%")
        lines.append(f"     成本中位数: {f5['cost_median']:.2f}元")
        lines.append(f"     90%成本区间: {f5['cost_90_range'][0]:.2f} ~ {f5['cost_90_range'][1]:.2f}元")

        # 6. 筹码轮换效率 ⭐
        f6 = f['chip_rotation_efficiency']
        lines.append(f"  6. 筹码轮换效率 ⭐  [{f6['score']:.1f}分]")
        lines.append(f"     轮换效率: {f6['efficiency']:.4f}")
        lines.append(f"     平均换手率: {f6['avg_turnover']:.2f}%  平均筹码变化: {f6['avg_chip_change']:.2f}%")

        # 7. 放量吸筹评分 ⭐
        f7 = f['chip_absorption_score']
        signal_cn = {'strong': '● 强吸筹', 'moderate': '● 中度吸筹',
                    'weak': '○ 弱吸筹', 'distribution': '✕ 派发',
                    'unknown': '? 未知'}[f7['absorption_signal']]
        lines.append(f"  7. 放量吸筹评分 ⭐  [{f7['score']:.1f}分]")
        lines.append(f"     信号: {signal_cn}  量比: {f7['volume_ratio']:.2f}  价变: {f7['price_change_pct']:+.2f}%")
        if 'sub_scores' in f7:
            ss = f7['sub_scores']
            lines.append(f"     分项: 量价{ss.get('volume_price', 0):.0f} 获利{ss.get('winner_expansion', 0):.0f} 集中{ss.get('concentration', 0):.0f} 重心{ss.get('center_shift', 0):.0f}")

        lines.append("")
        lines.append("─" * 60)
        lines.append("  【综合研判】")
        lines.append("─" * 60)

        # 简单研判逻辑
        strong_factors = []
        weak_factors = []
        factor_names = {
            'peak_migration_velocity': '筹码峰迁移',
            'chip_center_velocity': '筹码重心',
            'winning_expansion_velocity': '获利盘扩散',
            'overhead_supply_decay': '上方压力',
            'chip_concentration': '筹码集中度',
            'chip_rotation_efficiency': '轮换效率',
            'chip_absorption_score': '放量吸筹',
        }
        for key, name in factor_names.items():
            if f[key]['score'] >= 70:
                strong_factors.append(name)
            elif f[key]['score'] <= 30:
                weak_factors.append(name)

        if strong_factors:
            lines.append(f"  强势因子: {', '.join(strong_factors)}")
        if weak_factors:
            lines.append(f"  弱势因子: {', '.join(weak_factors)}")

        # 综合结论
        lines.append("")
        if result['total_score'] >= 65:
            lines.append("  结论: 筹码动力强劲，多头主导，关注突破机会")
        elif result['total_score'] >= 50:
            lines.append("  结论: 筹码动力中性偏强，可逢低关注")
        elif result['total_score'] >= 35:
            lines.append("  结论: 筹码动力偏弱，观望为主")
        else:
            lines.append("  结论: 筹码动力极弱，注意风险")

        lines.append("")
        lines.append("═" * 60)

        return "\n".join(lines)


# ============================================================
# 命令行入口
# ============================================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description='筹码动力引擎 - Chip Dynamics Engine')
    parser.add_argument('ts_code', help='股票代码，如 688135.SH')
    parser.add_argument('--date', '-d', default=None, help='截止日期，默认今天')
    parser.add_argument('--days', '-n', type=int, default=20, help='回溯天数，默认20')
    parser.add_argument('--token', '-t', default=None, help='Tushare token')
    parser.add_argument('--json', action='store_true', help='输出JSON格式')
    args = parser.parse_args()

    engine = ChipDynamicsEngine(token=args.token)
    result = engine.analyze(args.ts_code, end_date=args.date, lookback_days=args.days)

    if args.json:
        import json
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(engine.format_report(result))


if __name__ == '__main__':
    main()
