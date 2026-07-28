import os
import sys
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.indicators import ema, sma, slope, atr, adx, macd, new_high_count, price_position, volume_ratio
from data.loader import DataLoader, load_config


@dataclass
class PullbackResult:
    ts_code: str
    name: str
    is_qualified: bool = False
    quality_score: float = 0.0
    ret_60d: float = 0.0
    ret_20d: float = 0.0
    drawdown_from_high: float = 0.0
    recent_high_date: str = ""
    pullback_ma: str = ""
    ma_60_up: bool = False
    ma_120_up: bool = False
    is_first_pullback: bool = False
    no_volume_panic: bool = False
    details: Dict = field(default_factory=dict)


class PullbackDetector:
    def __init__(self, config=None):
        self.config = config or load_config()
        self.cfg = self.config['pullback']
        self.loader = DataLoader()

    def detect(self, ts_code: str, trade_date=None) -> Optional[PullbackResult]:
        if trade_date:
            self.loader.trade_date = trade_date
        td = self.loader.trade_date
        lookback = self.config['general']['lookback_days']
        start_date = (pd.to_datetime(td) - pd.Timedelta(days=lookback)).strftime('%Y%m%d')

        df = self.loader.load_stk_factor(ts_code, start_date, td, silent=True)
        if df is None or df.empty or len(df) < 120:
            return None

        result = PullbackResult(
            ts_code=ts_code,
            name=self.loader.get_stock_name(ts_code),
        )

        close = df['close_qfq'].values
        dates = df['trade_date'].values

        if not self._check_ma_up(df):
            return result

        result.ma_60_up = True
        result.ma_120_up = True

        if not self._check_60d_return(close):
            return result

        result.ret_60d = (close[-1] / close[-min(60, len(close))] - 1)
        if len(close) >= 20:
            result.ret_20d = close[-1] / close[-min(20, len(close))] - 1

        if not self._check_recent_high(close, dates):
            return result

        high_idx, high_price = self._find_recent_high(close)
        result.recent_high_date = str(dates[high_idx])
        result.drawdown_from_high = (high_price - close[-1]) / high_price

        if not self._check_pullback_range(result.drawdown_from_high):
            return result

        pullback_ma = self._check_pullback_to_ma(df)
        if not pullback_ma:
            return result

        result.pullback_ma = pullback_ma

        if not self._check_not_below_ma60(df):
            return result

        if not self._check_not_third_pullback(df):
            return result

        result.is_first_pullback = True

        if not self._check_no_volume_panic(df):
            return result

        result.no_volume_panic = True

        # ── V2.1 新增形态质量过滤 ──
        if not self._check_daily_drop(df):
            return result

        if not self._check_volume_shrink(df):
            return result

        if not self._check_momentum_health(df):
            return result

        result.quality_score = self._calc_quality(result)
        result.is_qualified = True

        return result

    def _check_ma_up(self, df):
        if len(df) < 120:
            return False
        # 使用 stk_factor_pro 的预计算字段（前复权）
        ma60_series = df['ma_qfq_60'].dropna() if 'ma_qfq_60' in df.columns else pd.Series(df['close_qfq']).rolling(60).mean().dropna()
        # MA120 无预计算字段，保持rolling计算
        ma120 = pd.Series(df['close_qfq'].values).rolling(120).mean()
        ma120_series = ma120.dropna()
        if ma60_series.empty or ma120_series.empty:
            return False
        sl_60 = slope(ma60_series.reset_index(drop=True), min(5, len(ma60_series)))
        sl_120 = slope(ma120_series.reset_index(drop=True), min(5, len(ma120_series)))
        s60 = sl_60.iloc[-1] if sl_60 is not None and len(sl_60) > 0 else 0
        s120 = sl_120.iloc[-1] if sl_120 is not None and len(sl_120) > 0 else 0
        return s60 > 0 and s120 > 0

    def _check_60d_return(self, close):
        ret_60d = close[-1] / close[-min(60, len(close))] - 1
        return ret_60d >= self.cfg.get('min_ret_60d', 0.30)

    def _check_recent_high(self, close, dates):
        if len(close) < 20:
            return False
        high_20 = close[-20:].max()
        return close[-1] < high_20

    def _find_recent_high(self, close):
        high_60 = close[-60:].max()
        high_idx = len(close) - 60 + np.argmax(close[-60:])
        return high_idx, high_60

    def _check_pullback_range(self, drawdown):
        return self.cfg.get('pullback_range_min', 0.05) <= drawdown <= self.cfg.get('pullback_range_max', 0.20)

    def _check_pullback_to_ma(self, df):
        """检查是否温和回踩均线（从上方靠近，非暴跌破位触均线）
        
        V2.1增强：验证昨日收盘在MA附近（回踩是从上往下的温和接触，
        而非一日内暴跌数个百分点到均线位置）
        """
        close_val = df['close_qfq'].iloc[-1]
        ma_map = {'ma_qfq_10': 'MA10', 'ma_qfq_20': 'MA20', 'ma_qfq_30': 'MA30'}
        max_drop_to_ma = self.cfg.get('max_drop_to_ma', 0.05)
        
        for col, name in ma_map.items():
            if col not in df.columns:
                continue
            ma_val = df[col].iloc[-1]
            if pd.isna(ma_val):
                continue
            dist = abs(close_val - ma_val) / ma_val
            if dist < 0.03:
                # 增强检查：昨日收盘不应大幅高于MA（防止暴跌穿MA后碰巧落在MA附近）
                if len(df) >= 2:
                    prev_close = df['close_qfq'].iloc[-2]
                    drop_to_ma = (prev_close - ma_val) / ma_val
                    if drop_to_ma > max_drop_to_ma:
                        # 如拓荆科技：昨收791→MA30=713，drop=10.9%>>5%，破位而非回踩
                        return ""
                return name
        return ""

    def _check_not_below_ma60(self, df):
        if 'ma_qfq_60' in df.columns:
            ma60_val = df['ma_qfq_60'].iloc[-1]
        else:
            ma60_val = pd.Series(df['close_qfq'].values).rolling(60).mean().iloc[-1]
        if pd.isna(ma60_val):
            return False
        return df['close_qfq'].iloc[-1] > ma60_val

    def _check_not_third_pullback(self, df):
        close = df['close_qfq'].values
        ma30_col = df['ma_qfq_30'] if 'ma_qfq_30' in df.columns else pd.Series(close).rolling(30).mean()
        crosses = 0
        recent = min(120, len(df))
        for i in range(recent - 10, recent):
            if i < 1:
                continue
            if pd.isna(ma30_col.iloc[i]) or pd.isna(ma30_col.iloc[i - 1]):
                continue
            if close[i] < ma30_col.iloc[i] and close[i - 1] >= ma30_col.iloc[i - 1]:
                crosses += 1
        return crosses <= self.cfg.get('max_pullback_count', 2)

    def _check_no_volume_panic(self, df):
        if 'vol' not in df.columns or 'pct_chg' not in df.columns:
            return True
        recent = df.tail(self.cfg.get('no_volume_panic_days', 20))
        for i in range(len(recent)):
            vol_ratio = recent['vol'].iloc[i] / recent['vol'].iloc[:i + 1].mean() if i > 0 else 1.0
            pct = recent['pct_chg'].iloc[i]
            if vol_ratio > 2.5 and pct < -5:
                return False
        return True

    def _check_daily_drop(self, df):
        """检查回踩日跌幅是否在合理范围（防止破位大阴线）
        
        要求：当日跌幅 <= max_daily_drop（默认5%）
        拓荆(-10.34%)案例：-10.34% << -5% → 过滤
        """
        if 'pct_chg' not in df.columns:
            return True
        pct_chg = float(df['pct_chg'].iloc[-1])
        max_drop = self.cfg.get('max_daily_drop', 0.05)
        # pct_chg 单位已是%（如 -10.34），max_drop 需转为百分数比较
        return pct_chg > -(max_drop * 100)

    def _check_volume_shrink(self, df):
        """检查回踩日量能是否较主升峰值显著缩量（抛压枯竭验证）
        
        要求：今日量 / 近N日峰值量 < shrink_ratio（默认0.6）
        即成交量需缩量至少40%以上，避免放量下跌中承接
        """
        if 'vol' not in df.columns or len(df) < 2:
            return True
        today_vol = float(df['vol'].iloc[-1])
        if today_vol <= 0:
            return True
        peak_window = self.cfg.get('volume_shrink_peak_window', 30)
        shrink_ratio = self.cfg.get('volume_shrink_ratio', 0.60)
        # 取最近peak_window天（不含今日）最大量
        recent_vol = df['vol'].iloc[-min(peak_window, len(df) - 1):-1]
        peak_vol = recent_vol.max()
        if peak_vol <= 0:
            return True
        return (today_vol / peak_vol) < shrink_ratio

    def _check_momentum_health(self, df):
        """检查MACD/RSI动量是否健康（排除空头加速阶段）
        
        RSI_6 < rsi_min(35) → 超卖破位，非正常回踩
        MACD柱连续N日下降且均为负 → 空头加速中，不接飞刀
        """
        if len(df) < 5:
            return True
        # ── RSI检查 ──
        rsi_min = self.cfg.get('rsi_min', 35)
        if 'rsi_qfq_6' in df.columns:
            rsi = df['rsi_qfq_6'].iloc[-1]
            if pd.notna(rsi) and float(rsi) < rsi_min:
                return False
        # ── MACD柱加速下跌检查 ──
        if 'macd_qfq' in df.columns:
            decline_days = self.cfg.get('macd_hist_decline_days', 3)
            if len(df) >= decline_days + 1:
                macd_vals = df['macd_qfq'].iloc[-(decline_days + 1):].values
                macd_vals = [float(v) for v in macd_vals if pd.notna(v)]
                if len(macd_vals) >= decline_days + 1:
                    latest = macd_vals[-1]
                    # 连续N日MACD柱下降且均为负 → 空头加速
                    if latest < 0 and all(macd_vals[i] > macd_vals[i + 1] for i in range(len(macd_vals) - 1)):
                        return False
        return True

    def _calc_quality(self, result):
        score = 0.5
        dd = result.drawdown_from_high
        if 0.08 <= dd <= 0.15:
            score += 0.2
        elif 0.05 <= dd <= 0.20:
            score += 0.1
        if result.pullback_ma == "MA20":
            score += 0.15
        elif result.pullback_ma == "MA10":
            score += 0.1
        if result.is_first_pullback:
            score += 0.1
        if result.no_volume_panic:
            score += 0.05
        return min(1.0, score)

    def detect_batch(self, ts_codes: List[str], trade_date=None) -> List[PullbackResult]:
        if trade_date:
            self.loader.trade_date = trade_date
        results = []
        for code in ts_codes:
            r = self.detect(code)
            if r is not None and r.is_qualified:
                results.append(r)
        results.sort(key=lambda x: x.quality_score, reverse=True)
        return results