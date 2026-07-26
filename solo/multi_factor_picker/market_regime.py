"""
市场状态检测模块
==================
根据沪深300指数的技术面数据，动态判定当前市场所处的6种状态，
为超跌择时等策略提供因子参数调整依据。

核心逻辑：
  1. 趋势方向（MA20/MA60位置与斜率）
  2. 回撤深度（60日最大回撤）
  3. 波动率状态（20日/60日波动率比值）
  4. 上涨天数占比（20日内）
  5. 极端行情检测

6种市场状态及对应的策略参数调整：
  ┌──────────────┬─────────┬──────────┬────────┬────────┬────────┐
  │ 市场状态      │ 综合分   │ 回撤最优  │ 缩量   │ RSI    │ K线    │
  │              │ 阈值     │ 区间      │ 阈值   │ 超卖   │ 止跌   │
  ├──────────────┼─────────┼──────────┼────────┼────────┼────────┤
  │ 强势市场     │ ≥80分   │ 3-10%    │ <0.85  │ <40    │ 宽松   │
  │ 震荡偏强     │ ≥80分   │ 5-12%    │ <0.80  │ <35    │ 正常   │
  │ 震荡市(默认)  │ ≥82分   │ 8-15%    │ <0.80  │ <35    │ 标准   │
  │ 震荡偏弱     │ ≥83分   │ 8-18%    │ <0.70  │ <30    │ 严格   │
  │ 弱势市场     │ ≥85分   │ 12-22%   │ <0.60  │ <25    │ 严格   │
  │ 极端暴跌     │ ≥72分   │ 15-35%   │ <0.50  │ <20    │ 侧重下影线 │
  └──────────────┴─────────┴──────────┴────────┴────────┴────────┘

逻辑解释：
  - 弱势市场：回撤容易很深，要求更大幅度回撤+极度缩量+低RSI，提高综合分门槛防假信号
  - 极端暴跌：恐慌性抛售后往往是大机会，降低综合分门槛捕捉左侧机会，但要求深度回撤
  - 强势市场：数据显示强势市场下降低阈值会让大量假信号涌入（5月阈值75胜率仅21%），
    因此统一提升至≥80分，宁可错过也不追回调陷阱
  - 震荡市：基准档位从80提升至82，过滤3月大量震荡市中质量参差的信号
"""
import numpy as np
import pandas as pd
from enum import Enum
from typing import Dict, Optional


class MarketRegime(Enum):
    STRONG_BULL = "强势市场"
    BULLISH_OSC = "震荡偏强"
    NEUTRAL = "震荡市"
    BEARISH_OSC = "震荡偏弱"
    STRONG_BEAR = "弱势市场"
    EXTREME_CRASH = "极端暴跌"


def detect_market_regime(hs300_daily: pd.DataFrame) -> Dict:
    """
    基于沪深300日线数据判定市场状态

    Parameters
    ----------
    hs300_daily : DataFrame
        沪深300日线数据，需含 trade_date, close, high, low, vol
        至少60个交易日

    Returns
    -------
    dict:
        regime: MarketRegime enum
        regime_name: str
        params: dict 各因子动态参数
        market_stats: dict 当前市场统计指标
    """
    if hs300_daily is None or len(hs300_daily) < 60:
        return _default_regime()

    df = hs300_daily.sort_values('trade_date').reset_index(drop=True)
    closes = df['close'].values.astype(float)
    n = len(closes)
    price = closes[-1]

    # ── 均线位置与斜率 ──
    ma20 = float(np.mean(closes[-20:]))
    ma60 = float(np.mean(closes[-60:])) if n >= 60 else ma20
    above_ma20 = price > ma20
    above_ma60 = price > ma60

    ma20_prev = float(np.mean(closes[-25:-5])) if n >= 25 else ma20
    ma20_slope = (ma20 - ma20_prev) / ma20_prev * 100

    # ── 波动率 ──
    returns = np.diff(closes) / closes[:-1] * 100
    vol_20 = float(np.std(returns[-20:])) if len(returns) >= 20 else float(np.std(returns))
    vol_60 = float(np.std(returns[-60:])) if len(returns) >= 60 else vol_20
    vol_ratio = vol_20 / vol_60 if vol_60 > 0 else 1.0

    # ── 回撤 ──
    recent_high = float(np.max(closes[-60:]))
    drawdown = (recent_high - price) / recent_high * 100

    # ── 趋势强度（上涨天数占比） ──
    up_days = sum(1 for r in returns[-20:] if r > 0)

    # ═══════════════════════════════════════
    # 市场状态判定（优先级从高到低）
    # ═══════════════════════════════════════
    is_extreme_crash = (
        drawdown > 18
        and vol_ratio > 1.4
        and not above_ma60
        and up_days <= 6
    )
    is_strong_bear = (
        drawdown > 10
        and not above_ma20
        and ma20_slope < -0.5
        and up_days <= 8
    )
    is_strong_bull = (
        above_ma20
        and above_ma60
        and ma20_slope > 0.3
        and up_days >= 12
    )
    is_bullish_osc = (
        above_ma20
        and ma20_slope > -0.3
        and up_days >= 8
    )
    is_bearish_osc = (
        not above_ma20
        and ma20_slope < 0.3
        and drawdown > 5
    )

    if is_extreme_crash:
        regime = MarketRegime.EXTREME_CRASH
    elif is_strong_bear:
        regime = MarketRegime.STRONG_BEAR
    elif is_strong_bull:
        regime = MarketRegime.STRONG_BULL
    elif is_bullish_osc:
        regime = MarketRegime.BULLISH_OSC
    elif is_bearish_osc:
        regime = MarketRegime.BEARISH_OSC
    else:
        regime = MarketRegime.NEUTRAL

    params = _get_regime_params(regime)

    # ═══════════════════════════════════════
    # 大盘连续下跌调整（极端行情保护）
    # ═══════════════════════════════════════
    adjustment = 0

    # 1. 连续阴线天数（最近10日内）
    consec_down = 0
    max_consec_down = 0
    for r in returns[-15:]:
        if r < 0:
            consec_down += 1
            max_consec_down = max(max_consec_down, consec_down)
        else:
            consec_down = 0

    # 2. 回调深度调整
    if max_consec_down >= 5:
        adjustment += 5
    elif max_consec_down >= 3:
        adjustment += 2

    # 3. 大幅跌破MA60且无明显反弹
    if not above_ma60 and drawdown > 8 and ma20_slope < -0.5:
        adjustment += 3

    # 4. 上涨/下跌天数严重失衡（单边下跌）
    if up_days <= 5 and drawdown > 5:
        adjustment += 3

    # 5. MA20下方持续运行天数
    below_ma20_days = sum(1 for c in closes[-20:] if c < np.mean(closes[-20:]))
    if below_ma20_days >= 15 and not above_ma20:
        adjustment += 2

    # ═══════════════════════════════════════
    # 信号质量保护（连续低胜率风险预警）
    # ═══════════════════════════════════════
    # 基于市场技术指标代理，当市场处于"高假信号区域"时额外提升阈值:
    #   a) 方向不明的高波动震荡(上涨日6-9天+波动率偏高+MA20走平)
    #      → 对应3月震荡市假信号频发场景
    #   b) 指数看似强势但波动剧烈(above MA20/MA60但vol_ratio>1.3+up_days≤11)
    #      → 对应5月强势市场下个股假信号频发场景

    # a) 假信号震荡区: 方向不明的高波动
    is_choppy_false_zone = (
        6 <= up_days <= 9
        and vol_ratio > 1.1
        and -0.3 <= ma20_slope <= 0.5
    )
    if is_choppy_false_zone:
        adjustment += 3

    # b) 强指数假个股区: 指数看似强但内部波动大
    is_false_breakout_zone = (
        above_ma20
        and above_ma60
        and vol_ratio > 1.3
        and up_days <= 11
    )
    if is_false_breakout_zone:
        adjustment += 2

    params['min_score'] = params.get('min_score', 80) + adjustment
    params['adjustment'] = adjustment

    params['market_stats'] = {
        'hs300_price': round(price, 2),
        'ma20': round(ma20, 2),
        'ma60': round(ma60, 2),
        'above_ma20': above_ma20,
        'above_ma60': above_ma60,
        'ma20_slope%': round(ma20_slope, 2),
        'max_drawdown60%': round(drawdown, 1),
        'vol_20d%': round(vol_20, 2),
        'vol_ratio_20/60': round(vol_ratio, 2),
        'up_days_20d': up_days,
        'max_consec_down': max_consec_down,
        'adjustment': adjustment,
    }

    return {'regime': regime, 'regime_name': regime.value, 'params': params}


def _default_regime() -> Dict:
    return {
        'regime': MarketRegime.NEUTRAL,
        'regime_name': MarketRegime.NEUTRAL.value,
        'params': _get_regime_params(MarketRegime.NEUTRAL),
    }


def _get_regime_params(regime: MarketRegime) -> Dict:
    """
    根据市场状态返回超跌因子的动态参数

    返回参数说明：
      min_score      : 综合分最低阈值（>=此值才算有效信号）
      f1_peak_start  : F1回撤深度打分"满分区间"起始值(%)
      f1_peak_end    : F1回撤深度打分"满分区间"结束值(%)
      f1_penalty_after: F1超过多少后开始大幅扣分(%)
      f2_vol_good    : F2缩量阈值—量比低于此值给高分
      f4_rsi_os_low  : F4 RSI超卖阈值下限（低于此值满分）
      f4_rsi_os_high : F4 RSI超卖阈值上限（高于此值0分）
      f5_min_shadow  : F5 K线下影线最低比例要求
      description    : 参数说明
    """
    params_map = {
        MarketRegime.STRONG_BULL: {
            'min_score': 80,
            'f1_peak_start': 3, 'f1_peak_end': 10,
            'f1_penalty_after': 18,
            'f2_vol_good': 0.85,
            'f4_rsi_os_low': 25, 'f4_rsi_os_high': 50,
            'f5_min_shadow': 0.30,
            'description': '强势市场：回调浅、时间短，但提升阈值≥80过滤假信号（5月回测数据支撑）',
        },
        MarketRegime.BULLISH_OSC: {
            'min_score': 80,
            'f1_peak_start': 5, 'f1_peak_end': 12,
            'f1_penalty_after': 20,
            'f2_vol_good': 0.80,
            'f4_rsi_os_low': 25, 'f4_rsi_os_high': 45,
            'f5_min_shadow': 0.35,
            'description': '震荡偏强：回调浅但持续性不确定，收紧阈值过滤假信号',
        },
        MarketRegime.NEUTRAL: {
            'min_score': 82,
            'f1_peak_start': 8, 'f1_peak_end': 15,
            'f1_penalty_after': 25,
            'f2_vol_good': 0.80,
            'f4_rsi_os_low': 25, 'f4_rsi_os_high': 45,
            'f5_min_shadow': 0.40,
            'description': '震荡市：基准档位提升至82过滤质量参差信号（3月回测数据支撑）',
        },
        MarketRegime.BEARISH_OSC: {
            'min_score': 83,
            'f1_peak_start': 8, 'f1_peak_end': 18,
            'f1_penalty_after': 28,
            'f2_vol_good': 0.70,
            'f4_rsi_os_low': 20, 'f4_rsi_os_high': 40,
            'f5_min_shadow': 0.45,
            'description': '震荡偏弱：收紧条件，要求更充分回撤+更明显缩量',
        },
        MarketRegime.STRONG_BEAR: {
            'min_score': 85,
            'f1_peak_start': 12, 'f1_peak_end': 22,
            'f1_penalty_after': 30,
            'f2_vol_good': 0.60,
            'f4_rsi_os_low': 15, 'f4_rsi_os_high': 35,
            'f5_min_shadow': 0.50,
            'description': '弱势市场：严格筛选，深度回撤+极度缩量+低RSI才安全',
        },
        MarketRegime.EXTREME_CRASH: {
            'min_score': 72,
            'f1_peak_start': 15, 'f1_peak_end': 35,
            'f1_penalty_after': 45,
            'f2_vol_good': 0.50,
            'f4_rsi_os_low': 10, 'f4_rsi_os_high': 30,
            'f5_min_shadow': 0.50,
            'description': '极端暴跌：恐慌抛售后大机会，大幅放宽阈值但要求深度回撤+极度缩量',
        },
    }
    return params_map.get(regime, params_map[MarketRegime.NEUTRAL])
