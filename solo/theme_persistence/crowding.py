# -*- coding: utf-8 -*-
"""
拥挤度惩罚 (Crowding Penalty)

避免买入晚期拥挤的主题。
拥挤度 = 40% 价格拥挤 + 30% 量能拥挤 + 30% 波动率拥挤

惩罚:
  拥挤度 <60:  0
  60-80:       -5
  80-90:       -10
  >90:         -20
"""
import numpy as np
import pandas as pd


def calculate_crowding_penalty(etf_df: pd.DataFrame,
                                stock_data: dict = None) -> dict:
    """
    计算拥挤度惩罚

    Args:
        etf_df: ETF日线数据
        stock_data: 成份股日线 (可选, 用于辅助计算)

    Returns:
        {'penalty': 0~-20, 'crowding_score': 0-100,
         'price_crowding': 0-100, 'volume_crowding': 0-100,
         'volatility_crowding': 0-100, 'details': {...}}
    """
    if etf_df is None or len(etf_df) < 60:
        return _default_result()

    close = etf_df['close'].astype(float).reset_index(drop=True)
    vol = etf_df['vol'].astype(float).reset_index(drop=True) if 'vol' in etf_df.columns else None

    # === 1. 价格拥挤: 20D收益在近250日的百分位 ===
    ret_20d = (close.iloc[-1] / close.iloc[-21] - 1) * 100 if len(close) >= 21 else 0

    # 计算过去250日每天的20D收益, 看当前处于什么百分位
    lookback = min(250, len(close) - 20)
    if lookback > 20:
        rolling_ret = close.pct_change(20).iloc[-lookback:] * 100
        rolling_ret = rolling_ret.dropna()
        if len(rolling_ret) > 10:
            price_percentile = float(np.mean(rolling_ret <= ret_20d)) * 100
        else:
            price_percentile = 50
    else:
        price_percentile = 50

    # === 2. 量能拥挤: 20日均量在近250日的百分位 ===
    if vol is not None and len(vol) >= 40:
        vol_ma20 = vol.rolling(20).mean()
        current_vol_ma20 = vol_ma20.iloc[-1]
        lookback_vol = min(250, len(vol_ma20))
        vol_history = vol_ma20.iloc[-lookback_vol:].dropna()
        if len(vol_history) > 10 and not np.isnan(current_vol_ma20):
            volume_percentile = float(np.mean(vol_history <= current_vol_ma20)) * 100
        else:
            volume_percentile = 50
    else:
        volume_percentile = 50

    # === 3. 波动率拥挤: ATR百分位 ===
    if len(close) >= 22 and vol is not None:
        # ATR = 14日平均真实波幅
        high = etf_df['high'].astype(float).reset_index(drop=True) if 'high' in etf_df.columns else close
        low = etf_df['low'].astype(float).reset_index(drop=True) if 'low' in etf_df.columns else close
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs()
        ], axis=1).max(axis=1)
        atr_14 = tr.rolling(14).mean()
        current_atr = atr_14.iloc[-1]
        lookback_atr = min(250, len(atr_14))
        atr_history = atr_14.iloc[-lookback_atr:].dropna()
        if len(atr_history) > 10 and not np.isnan(current_atr):
            volatility_percentile = float(np.mean(atr_history <= current_atr)) * 100
        else:
            volatility_percentile = 50
    else:
        # 用收益率标准差代理
        if len(close) >= 40:
            ret_std_20 = close.pct_change().tail(20).std()
            lookback_ret = min(250, len(close) - 20)
            rolling_std = close.pct_change().rolling(20).std().iloc[-lookback_ret:].dropna()
            if len(rolling_std) > 10:
                volatility_percentile = float(np.mean(rolling_std <= ret_std_20)) * 100
            else:
                volatility_percentile = 50
        else:
            volatility_percentile = 50

    # === 拥挤度综合 ===
    crowding = (
        price_percentile * 0.40 +
        volume_percentile * 0.30 +
        volatility_percentile * 0.30
    )

    # === 惩罚 ===
    if crowding < 60:
        penalty = 0
    elif crowding < 80:
        penalty = -5
    elif crowding < 90:
        penalty = -10
    else:
        penalty = -20

    return {
        'penalty': penalty,
        'crowding_score': round(float(crowding), 2),
        'price_crowding': round(float(price_percentile), 2),
        'volume_crowding': round(float(volume_percentile), 2),
        'volatility_crowding': round(float(volatility_percentile), 2),
        'details': {
            'ret_20d': round(float(ret_20d), 2),
            'price_percentile': round(float(price_percentile), 1),
            'volume_percentile': round(float(volume_percentile), 1),
            'volatility_percentile': round(float(volatility_percentile), 1),
        }
    }


def _default_result():
    return {
        'penalty': 0, 'crowding_score': 50.0,
        'price_crowding': 50.0, 'volume_crowding': 50.0,
        'volatility_crowding': 50.0, 'details': {}
    }
