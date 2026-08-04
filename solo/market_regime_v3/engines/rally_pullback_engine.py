"""Rally Pullback Engine — 区间放量多涨停拉升后回调 + 低开阳线承接

核心逻辑：
  阶段1: 识别拉升区间（20日内放量+多涨停短线爆发拉升）
  阶段2: 检测回调质量（回撤幅度、MA60支撑）
  阶段3: 识别低开阳线承接信号（低开收阳 → 买点）

总分100分，≥60分视为有效信号。
"""

import sys, os
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'inst_pullback_v2'))
from data.loader import DataLoader
from data.indicators import sma


@dataclass
class RallyPullbackResult:
    """区间放量多涨停回调信号结果"""
    ts_code: str
    name: str = ""
    is_qualified: bool = False
    total_score: float = 0.0       # 总分 0-100

    # 阶段1: 拉升区间
    rally_start_idx: int = -1       # 拉升起点索引
    rally_high_idx: int = -1        # 拉升最高点索引
    rally_high_date: str = ""
    rally_amplitude: float = 0.0    # 拉升幅度
    rally_days: int = 0             # 拉升天数
    rally_vol_expansion: float = 0.0  # 拉升段量比
    rally_limit_up_count: int = 0   # 涨停次数
    rally_max_consecutive_limit_up: int = 0  # 最大连续涨停

    # 阶段2: 回调
    drawdown_from_high: float = 0.0  # 从高点回撤
    pullback_days: int = 0           # 回调天数
    above_ma60: bool = False

    # 阶段3: 低开阳线
    is_low_open_positive: bool = False
    candle_open_gap: float = 0.0     # 低开幅度
    candle_body_pct: float = 0.0     # 阳线实体%
    candle_upper_shadow_pct: float = 0.0  # 上影线%
    candle_lower_shadow_pct: float = 0.0  # 下影线%

    # 子得分
    subs: Dict[str, float] = field(default_factory=dict)

    # 入场参考
    ref_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    atr: float = 0.0


class RallyPullbackEngine:
    """区间放量多涨停拉升后回调 + 低开阳线承接引擎

    配置参数:
      rally:
        lookback: 20                 # 拉升区间回看天数（短线20天内爆发）
        min_amplitude: 0.25          # 最低拉升幅度
        vol_expansion_min: 1.5       # 拉升段放量倍数
        min_limit_up: 2              # 最低涨停次数
        limit_up_threshold: 9.5      # 涨停判定涨幅(%)
      pullback:
        drawdown_min: 0.05           # 最低回撤
        drawdown_max: 0.20           # 最高回撤
        max_pullback_days: 30        # 最长回调天数
        min_pullback_days: 3         # 最短回调天数
      candle:
        min_open_gap: 0.005          # 最低低开幅度(0.5%)
        max_open_gap: 0.05           # 最高低开幅度(5%)
        min_body_pct: 0.005          # 最低阳线实体(0.5%)
        max_lower_shadow_ratio: 2.0  # 下影线/实体最大比例
      scoring:
        vol_expansion: 30
        limit_up: 25
        pullback_quality: 20
        candle_signal: 15
        volume_confirm: 10
        threshold: 60
    """

    def __init__(self, config: dict = None):
        self.cfg = config.get('rally_pullback', {}) if config else {}
        self.loader = DataLoader()

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def detect(self, ts_code: str, trade_date: str = None) -> Optional[RallyPullbackResult]:
        """检测单只股票"""
        if trade_date:
            self.loader.trade_date = trade_date
        td = self.loader.trade_date

        # 加载数据
        lookback = self.cfg.get('rally', {}).get('lookback', 20) + 80
        start_date = (pd.to_datetime(td) - pd.Timedelta(days=lookback + 30)).strftime('%Y%m%d')
        df = self.loader.load_stk_factor(ts_code, start_date, td, silent=True)

        if df is None or df.empty or len(df) < 60:
            return None

        result = RallyPullbackResult(
            ts_code=ts_code,
            name=self.loader.get_stock_name(ts_code),
        )

        close = df['close_qfq'].values if 'close_qfq' in df.columns else df['close'].values
        open_vals = df['open_qfq'].values if 'open_qfq' in df.columns else df['open'].values
        high_vals = df['high_qfq'].values if 'high_qfq' in df.columns else df['high'].values
        low_vals = df['low_qfq'].values if 'low_qfq' in df.columns else df['low'].values
        vol = df['vol'].values if 'vol' in df.columns else None
        pct_chg = df['pct_chg'].values if 'pct_chg' in df.columns else None
        dates = df['trade_date'].values if 'trade_date' in df.columns else None

        n = len(close)

        # ═══════════════════════════════════════════════════════
        # 阶段1: 识别拉升区间（放量+多涨停拉升）
        # ═══════════════════════════════════════════════════════
        rally_cfg = self.cfg.get('rally', {})
        lookback_rally = rally_cfg.get('lookback', 20)
        rally_start = max(0, n - lookback_rally)

        # 找拉升区间内的最高点
        high_idx = rally_start + np.argmax(close[rally_start:])
        high_price = close[high_idx]

        # 找拉升起点（最高点前的最低点）
        low_idx = rally_start + np.argmin(close[rally_start:high_idx + 1])
        low_price = close[low_idx]

        rally_amplitude = (high_price - low_price) / low_price if low_price > 0 else 0
        rally_days = high_idx - low_idx

        min_amplitude = rally_cfg.get('min_amplitude', 0.25)
        if rally_amplitude < min_amplitude:
            return result

        result.rally_amplitude = rally_amplitude
        result.rally_days = rally_days
        result.rally_high_idx = high_idx
        result.rally_start_idx = low_idx
        result.rally_high_date = str(dates[high_idx]) if dates is not None else ""

        # ── 区间放量检测 ──
        if vol is not None and low_idx > 0:
            rally_vol = np.mean(vol[low_idx:high_idx + 1])
            pre_vol = np.mean(vol[max(0, low_idx - 20):low_idx])
            if pre_vol > 0:
                result.rally_vol_expansion = rally_vol / pre_vol

        vol_expansion_min = rally_cfg.get('vol_expansion_min', 1.5)
        if result.rally_vol_expansion < vol_expansion_min:
            return result

        # ── 多涨停检测 ──
        if pct_chg is not None:
            limit_up_threshold = rally_cfg.get('limit_up_threshold', 9.5)
            rally_pct = pct_chg[low_idx:high_idx + 1]
            limit_up_mask = rally_pct >= limit_up_threshold
            result.rally_limit_up_count = int(np.sum(limit_up_mask))

            # 最大连续涨停
            max_consec = 0
            cur_consec = 0
            for v in limit_up_mask:
                if v:
                    cur_consec += 1
                    max_consec = max(max_consec, cur_consec)
                else:
                    cur_consec = 0
            result.rally_max_consecutive_limit_up = max_consec

        min_limit_up = rally_cfg.get('min_limit_up', 2)
        if result.rally_limit_up_count < min_limit_up:
            return result

        # ═══════════════════════════════════════════════════════
        # 阶段2: 回调检测
        # ═══════════════════════════════════════════════════════
        pb_cfg = self.cfg.get('pullback', {})
        drawdown = (high_price - close[-1]) / high_price

        drawdown_min = pb_cfg.get('drawdown_min', 0.05)
        drawdown_max = pb_cfg.get('drawdown_max', 0.20)
        if drawdown < drawdown_min or drawdown > drawdown_max:
            return result

        result.drawdown_from_high = drawdown
        result.pullback_days = n - 1 - high_idx

        max_pb_days = pb_cfg.get('max_pullback_days', 30)
        min_pb_days = pb_cfg.get('min_pullback_days', 3)
        if result.pullback_days < min_pb_days or result.pullback_days > max_pb_days:
            return result

        # MA60支撑
        if 'ma_qfq_60' in df.columns:
            ma60 = df['ma_qfq_60'].iloc[-1]
        else:
            ma60 = pd.Series(close).rolling(60).mean().iloc[-1]
        result.above_ma60 = close[-1] > ma60
        if not result.above_ma60:
            return result

        # ═══════════════════════════════════════════════════════
        # 阶段3: 低开阳线承接信号
        # ═══════════════════════════════════════════════════════
        candle_cfg = self.cfg.get('candle', {})

        if n < 2:
            return result

        today_open = open_vals[-1]
        today_close = close[-1]
        today_high = high_vals[-1]
        today_low = low_vals[-1]
        yesterday_close = close[-2]

        # 低开：今日开盘 < 昨日收盘
        open_gap = (yesterday_close - today_open) / yesterday_close if yesterday_close > 0 else 0
        min_open_gap = candle_cfg.get('min_open_gap', 0.005)
        max_open_gap = candle_cfg.get('max_open_gap', 0.05)

        if open_gap < min_open_gap or open_gap > max_open_gap:
            return result

        result.candle_open_gap = open_gap

        # 阳线：今日收盘 > 今日开盘
        if today_close <= today_open:
            return result

        # 实体阳线（非十字星）
        body_pct = (today_close - today_open) / today_open
        min_body_pct = candle_cfg.get('min_body_pct', 0.005)
        if body_pct < min_body_pct:
            return result

        result.candle_body_pct = body_pct
        result.is_low_open_positive = True

        # 影线分析
        candle_range = today_high - today_low
        if candle_range > 0:
            result.candle_upper_shadow_pct = (today_high - today_close) / candle_range
            result.candle_lower_shadow_pct = (today_open - today_low) / candle_range

        # 下影线不宜过长（排除长下影探底，不是"承接"而是"抄底"）
        max_lower_shadow = candle_cfg.get('max_lower_shadow_ratio', 2.0)
        if body_pct > 0 and result.candle_lower_shadow_pct > 0:
            shadow_ratio = (today_open - today_low) / (today_close - today_open)
            if shadow_ratio > max_lower_shadow:
                return result

        # ═══════════════════════════════════════════════════════
        # 评分模型
        # ═══════════════════════════════════════════════════════
        scoring = self.cfg.get('scoring', {})
        subs = {}

        # 1. 区间放量分 (30分)
        vol_score = self._score_vol_expansion(result.rally_vol_expansion)
        subs['vol_expansion'] = vol_score * scoring.get('vol_expansion', 30)

        # 2. 多涨停分 (25分)
        lu_score = self._score_limit_up(result.rally_limit_up_count)
        subs['limit_up'] = lu_score * scoring.get('limit_up', 25)

        # 3. 回调质量分 (20分)
        pb_score = self._score_pullback(drawdown, result.pullback_days)
        subs['pullback'] = pb_score * scoring.get('pullback_quality', 20)

        # 4. 低开阳线分 (15分)
        candle_score = self._score_candle(open_gap, body_pct,
                                          result.candle_lower_shadow_pct,
                                          result.candle_upper_shadow_pct)
        subs['candle'] = candle_score * scoring.get('candle_signal', 15)

        # 5. 量能确认分 (10分)
        vol_confirm = self._score_volume_confirm(vol, n) if vol is not None else 0.5
        subs['volume_confirm'] = vol_confirm * scoring.get('volume_confirm', 10)

        total = sum(subs.values())
        result.total_score = total
        result.subs = subs

        threshold = scoring.get('threshold', 60)
        if total >= threshold:
            result.is_qualified = True

            # 入场参考价
            result.ref_price = round(today_close, 2)
            # ATR止损
            if 'atr_qfq' in df.columns:
                atr_val = float(df['atr_qfq'].iloc[-1]) if pd.notna(df['atr_qfq'].iloc[-1]) else 0
            else:
                atr_val = self._calc_atr(close, high_vals, low_vals, 14)
            result.atr = round(atr_val, 2)

            if atr_val > 0:
                result.stop_loss = round(today_close - atr_val * 1.5, 2)
                result.take_profit = round(today_close + atr_val * 3.0, 2)
            else:
                result.stop_loss = round(today_close * 0.95, 2)
                result.take_profit = round(today_close * 1.10, 2)

        return result

    # ------------------------------------------------------------------
    # 子评分函数
    # ------------------------------------------------------------------
    @staticmethod
    def _score_vol_expansion(ratio: float) -> float:
        """放量倍率评分 (0~1)
        1.5倍=0.5, 2.0倍=0.7, 3.0倍=0.9, 5.0倍+=1.0
        """
        if ratio < 1.3:
            return 0.0
        score = np.tanh((ratio - 1.3) * 1.5)
        return max(0.0, min(1.0, score))

    @staticmethod
    def _score_limit_up(count: int) -> float:
        """涨停次数评分 (0~1)
        2次=0.5, 3次=0.7, 4次=0.85, 5次+=1.0
        """
        if count < 2:
            return 0.0
        if count >= 5:
            return 1.0
        return 0.3 + (count - 2) * 0.175

    @staticmethod
    def _score_pullback(drawdown: float, days: int) -> float:
        """回调质量评分 (0~1)
        最优回撤8-15%，回调天数5-15天
        """
        # 回撤分
        if 0.08 <= drawdown <= 0.15:
            dd_score = 1.0
        elif 0.05 <= drawdown <= 0.20:
            dd_score = 0.7
        else:
            dd_score = 0.3

        # 天数分
        if 5 <= days <= 15:
            day_score = 1.0
        elif 3 <= days <= 20:
            day_score = 0.7
        else:
            day_score = 0.4

        return (dd_score * 0.6 + day_score * 0.4)

    @staticmethod
    def _score_candle(open_gap: float, body_pct: float,
                      lower_shadow: float, upper_shadow: float) -> float:
        """低开阳线质量评分 (0~1)

        理想形态：低开1-2%，阳线实体1-3%，上影线短，下影线适中
        """
        score = 0.5

        # 低开幅度：0.5-2%最佳
        if 0.008 <= open_gap <= 0.02:
            score += 0.1
        elif 0.005 <= open_gap <= 0.03:
            score += 0.05

        # 阳线实体：1-3%最佳
        if 0.01 <= body_pct <= 0.03:
            score += 0.15
        elif 0.005 <= body_pct <= 0.05:
            score += 0.08

        # 上影线短（<25%）：多方控制力强
        if upper_shadow < 0.25:
            score += 0.1
        elif upper_shadow < 0.40:
            score += 0.05

        # 下影线适中（10-30%）：有承接
        if 0.10 <= lower_shadow <= 0.30:
            score += 0.1
        elif 0.05 <= lower_shadow <= 0.40:
            score += 0.05

        return max(0.0, min(1.0, score))

    @staticmethod
    def _score_volume_confirm(vol: np.ndarray, n: int) -> float:
        """今日量能确认评分 (0~1)
        量比0.7-1.5最佳（温和放量承接，非放量恐慌）
        """
        if len(vol) < 20:
            return 0.5
        today_vol = vol[-1]
        ma20 = np.mean(vol[-21:-1])
        if ma20 <= 0:
            return 0.5
        ratio = today_vol / ma20
        if 0.7 <= ratio <= 1.5:
            return 1.0
        elif 0.5 <= ratio <= 2.0:
            return 0.7
        else:
            return 0.4

    @staticmethod
    def _calc_atr(close, high, low, period=14):
        """简易ATR计算"""
        n = len(close)
        if n < period + 1:
            return 0
        tr = []
        for i in range(1, n):
            tr.append(max(high[i] - low[i],
                          abs(high[i] - close[i - 1]),
                          abs(low[i] - close[i - 1])))
        return np.mean(tr[-period:]) if tr else 0

    # ------------------------------------------------------------------
    # 批量检测
    # ------------------------------------------------------------------
    def detect_batch(self, ts_codes: list, trade_date: str = None) -> list:
        if trade_date:
            self.loader.trade_date = trade_date
        results = []
        for code in ts_codes:
            r = self.detect(code)
            if r is not None and r.is_qualified:
                results.append(r)
        results.sort(key=lambda x: x.total_score, reverse=True)
        return results