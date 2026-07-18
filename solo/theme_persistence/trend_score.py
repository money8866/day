# -*- coding: utf-8 -*-
"""
趋势稳定性评分 (Trend Stability Score) — 权重 25%

判断主题趋势是否健康可持续。
1. 长期趋势 (40%): MA20>MA60 (+30) + MA20斜率向上 (+20)
2. 动量质量 (30%): 20D/60D收益 + 相对沪深300强弱 → Strong30/Medium20/Weak10
3. 回撤质量 (30%): 60日最大回撤 → <8%:30 / 8-15%:20 / 15-25%:10 / >25%:0
"""
import numpy as np
import pandas as pd


def calculate_trend_stability(etf_df: pd.DataFrame,
                               benchmark_df: pd.DataFrame = None) -> dict:
    """
    计算趋势稳定性评分

    Args:
        etf_df: ETF日线数据, 必须含 close 列, trade_date 列
        benchmark_df: 沪深300日线, 含 close 列 (可选)

    Returns:
        {'score': 0-100, 'ma_trend': 0-50, 'momentum_quality': 0-30,
         'drawdown_quality': 0-30, 'details': {...}}
    """
    if etf_df is None or len(etf_df) < 60:
        return _default_result()

    close = etf_df['close'].astype(float).reset_index(drop=True)

    # === 1. 长期趋势 (40% → 满分50) ===
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    last_ma20 = ma20.iloc[-1]
    last_ma60 = ma60.iloc[-1]
    last_close = close.iloc[-1]

    ma_score = 0
    if not np.isnan(last_ma20) and not np.isnan(last_ma60) and last_ma20 > last_ma60:
        ma_score += 30  # MA20 > MA60

    # MA20斜率 (近5日)
    if not np.isnan(ma20.iloc[-1]) and not np.isnan(ma20.iloc[-5]):
        ma20_slope = (ma20.iloc[-1] / ma20.iloc[-5] - 1) * 100
        if ma20_slope > 0:
            ma_score += 20  # MA20斜率向上

    ma_trend = min(50, ma_score)

    # === 2. 动量质量 (30% → 满分30) ===
    ret_20d = (close.iloc[-1] / close.iloc[-21] - 1) * 100 if len(close) >= 21 else 0
    ret_60d = (close.iloc[-1] / close.iloc[-61] - 1) * 100 if len(close) >= 61 else 0

    # 相对沪深300强弱
    rs_vs_bm = 0
    if benchmark_df is not None and len(benchmark_df) >= 21:
        bm_close = benchmark_df['close'].astype(float).reset_index(drop=True)
        bm_ret_20d = (bm_close.iloc[-1] / bm_close.iloc[-21] - 1) * 100
        rs_vs_bm = ret_20d - bm_ret_20d

    # Strong: 20D>5% 且 60D>10% 且 RS>3%
    # Medium: 20D>0 且 60D>0
    # Weak: 其他
    if ret_20d > 5 and ret_60d > 10 and rs_vs_bm > 3:
        momentum_quality = 30
    elif ret_20d > 0 and ret_60d > 0:
        momentum_quality = 20
    else:
        momentum_quality = 10

    # === 3. 回撤质量 (30% → 满分30) ===
    close_60 = close.tail(60)
    running_max = close_60.cummax()
    drawdown = (close_60 / running_max - 1) * 100
    max_drawdown = abs(drawdown.min())

    if max_drawdown < 8:
        drawdown_quality = 30
    elif max_drawdown < 15:
        drawdown_quality = 20
    elif max_drawdown < 25:
        drawdown_quality = 10
    else:
        drawdown_quality = 0

    # === 综合: 40% MA + 30% 动量 + 30% 回撤 ===
    # 归一化到0-100
    total = (ma_trend / 50 * 40) + (momentum_quality / 30 * 30) + (drawdown_quality / 30 * 30)
    total = max(0, min(100, total))

    return {
        'score': round(total, 2),
        'ma_trend': round(ma_trend, 2),
        'momentum_quality': round(momentum_quality, 2),
        'drawdown_quality': round(drawdown_quality, 2),
        'details': {
            'ma20': round(float(last_ma20), 4) if not np.isnan(last_ma20) else 0,
            'ma60': round(float(last_ma60), 4) if not np.isnan(last_ma60) else 0,
            'ret_20d': round(float(ret_20d), 2),
            'ret_60d': round(float(ret_60d), 2),
            'rs_vs_csi300': round(float(rs_vs_bm), 2),
            'max_drawdown_60d': round(float(max_drawdown), 2),
        }
    }


def _default_result():
    return {
        'score': 50.0, 'ma_trend': 25.0, 'momentum_quality': 15.0,
        'drawdown_quality': 15.0, 'details': {}
    }
