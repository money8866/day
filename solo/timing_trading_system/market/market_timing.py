#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
大盘择时引擎
============
基于指数日线数据判断市场状态：
- 主升浪(Bull)   → 重仓
- 震荡偏强(Strong) → 中等仓位
- 中期调整(MidAdj) → 轻仓
- 主跌/退潮(Bear) → 空仓/极轻仓

判断依据：
  1. MA排列 (5/10/20/60)
  2. ADX 趋势强度
  3. 涨跌家数比（用ETF涨跌比近似）
  4. 成交量
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from data import tdx_loader as tdx

LOG = logging.getLogger("timing_trading.market_timing")


@dataclass
class MarketState:
    """大盘状态结果"""
    name: str                              # bull_market / strong_oscillate / mid_adjust / bear_market
    label: str                             # 中文名: 主升浪/震荡偏强/中期调整/主跌
    score: float = 0.0                     # 0-100 分数
    position_suggest: float = 0.0          # 建议仓位比例
    adx: float = 0.0
    ma_arrangement: str = ""               # bullish / bearish / mixed
    advance_ratio: float = 1.0             # 涨跌比
    details: dict = field(default_factory=dict)


# 状态名称常量
STATE_BULL = "bull_market"           # 主升浪
STATE_STRONG = "strong_oscillate"     # 震荡偏强
STATE_MID_ADJ = "mid_adjust"          # 中期调整
STATE_BEAR = "bear_market"           # 主跌/退潮

STATE_LABELS = {
    STATE_BULL: "主升浪",
    STATE_STRONG: "震荡偏强",
    STATE_MID_ADJ: "中期调整",
    STATE_BEAR: "主跌/退潮",
}

STATE_POSITIONS = {
    STATE_BULL: 0.85,
    STATE_STRONG: 0.55,
    STATE_MID_ADJ: 0.25,
    STATE_BEAR: 0.10,
}

STATE_SCORES = {
    STATE_BULL: 90,
    STATE_STRONG: 65,
    STATE_MID_ADJ: 35,
    STATE_BEAR: 15,
}


def _detect_ma_arrangement(df: pd.DataFrame) -> str:
    """检测均线排列状态"""
    if df.empty or len(df) < 2:
        return "unknown"
    last = df.iloc[-1]
    # 多头排列: MA5 > MA20 > MA60
    if last.get("ma5", 0) > last.get("ma20", 0) > last.get("ma60", 0):
        return "bullish"
    # 空头排列: MA5 < MA10 < MA20 < MA60
    if (last.get("ma5", 0) < last.get("ma10", 0) < last.get("ma20", 0)
            and last.get("ma20", 0) < last.get("ma60", 0)):
        return "bearish"
    return "mixed"


def _detect_state(
    ma_arr: str,
    adx_val: float,
    advance_ratio: float,
    ma20_trend: str,
    vol_shrink: bool,
    config: dict,
) -> str:
    """根据指标判断市场状态"""
    cfg = config.get("states", {})

    # 主升浪
    bull_cfg = cfg.get("bull_market", {})
    if (ma_arr == "bullish"
            and adx_val >= bull_cfg.get("adx_min", 25)
            and advance_ratio >= bull_cfg.get("advance_ratio", 1.5)):
        return STATE_BULL

    # 主跌
    bear_cfg = cfg.get("bear_market", {})
    if (ma_arr == "bearish"
            and adx_val <= bear_cfg.get("adx_max", 20)):
        return STATE_BEAR

    # 中期调整
    adj_cfg = cfg.get("mid_adjust", {})
    if (ma20_trend in ("flat", "down")
            and vol_shrink):
        return STATE_MID_ADJ

    # 震荡偏强
    strong_cfg = cfg.get("strong_oscillate", {})
    if (ma20_trend == "up"
            and advance_ratio >= strong_cfg.get("advance_ratio", 1.0)):
        return STATE_STRONG

    # 默认: 震荡
    return STATE_MID_ADJ


def _calc_advance_ratio(
    index_df: pd.DataFrame,
    etf_data: Dict[str, pd.DataFrame],
    config: dict,
    lookback: int = 5,
) -> float:
    """计算近期涨跌比

    优先使用ETF涨跌比例近似，否则用指数自身涨跌天数比
    """
    ad_cfg = config.get("advance_decline", {})
    if ad_cfg.get("use_etf_proxy", True) and etf_data:
        # 用ETF集合的涨跌比例近似全市场
        up_days = 0
        total = 0
        for code, df in etf_data.items():
            if df.empty or len(df) < lookback:
                continue
            recent = df.tail(lookback)
            up_days += (recent["pct_chg"] > 0).sum()
            total += len(recent)
        if total == 0:
            return 1.0
        return up_days / (total - up_days + 1e-6)

    # 用指数自身近N日涨跌幅衡量
    if index_df.empty or len(index_df) < lookback:
        return 1.0
    recent = index_df.tail(lookback)
    up = (recent["pct_chg"] > 0).sum()
    down = (recent["pct_chg"] <= 0).sum()
    return up / (down + 1e-6)


def _get_ma20_trend(df: pd.DataFrame, lookback: int = 5) -> str:
    """判断MA20的方向: up / down / flat"""
    if df.empty or len(df) < lookback + 1:
        return "flat"
    recent = df["ma20"].dropna().tail(lookback + 1)
    if len(recent) < 2:
        return "flat"
    slope = (recent.iloc[-1] - recent.iloc[0]) / recent.iloc[0]
    if slope > 0.005:
        return "up"
    elif slope < -0.005:
        return "down"
    return "flat"


def _is_vol_shrink(df: pd.DataFrame, vol_ma_period: int = 20) -> bool:
    """判断成交量是否萎缩"""
    if df.empty or len(df) < vol_ma_period + 1:
        return False
    last = df.iloc[-1]
    vol_ma = last.get(f"vol_ma{vol_ma_period}", last.get("vol"))
    return last.get("vol_ratio", 1.0) < 0.8


class MarketTimingEngine:
    """大盘择时引擎"""

    def __init__(self, config: dict):
        self.cfg = config.get("market_timing", {})
        self.index_codes = self.cfg.get("indices", ["000001.SH", "399006.SZ"])
        self.tdx_root = config.get("general", {}).get("tdx_root", "C:\\new_tdx")

    def evaluate(
        self,
        trade_date: str = "",
        etf_data: Dict[str, pd.DataFrame] = None,
    ) -> MarketState:
        """评估当前大盘状态

        Args:
            trade_date: YYYYMMDD，为空则使用最新数据
            etf_data: ETF日线数据字典（用于涨跌比计算），可选

        Returns:
            MarketState
        """
        # 1. 加载指数数据
        indices_data = tdx.load_indices(self.index_codes, self.tdx_root)
        if not indices_data:
            LOG.warning("无法加载指数数据，返回默认状态")
            return MarketState(STATE_MID_ADJ, "中期调整", details={"error": "no_index_data"})

        # 2. 取最有代表性的指数（上证）
        main_idx = indices_data.get("000001.SH", None)
        if main_idx is None:
            # 改用第一个可用指数
            main_idx = list(indices_data.values())[0]

        # 3. 计算技术指标
        main_idx = tdx.calc_all_indicators(main_idx)

        # 4. 过滤到目标日期
        if trade_date:
            main_idx = main_idx[main_idx["trade_date"] <= trade_date]
        if main_idx.empty:
            return MarketState(STATE_MID_ADJ, "中期调整", details={"error": "no_data_for_date"})

        # 5. 提取特征
        ma_arr = _detect_ma_arrangement(main_idx)
        adx_val = main_idx["adx"].iloc[-1] if "adx" in main_idx.columns else 0
        advance_ratio = _calc_advance_ratio(main_idx, etf_data or {}, self.cfg)
        ma20_trend = _get_ma20_trend(main_idx)
        vol_shrink = _is_vol_shrink(main_idx, self.cfg.get("volume_ma_period", 20))

        # 6. 判断状态
        state_name = _detect_state(ma_arr, adx_val, advance_ratio,
                                    ma20_trend, vol_shrink, self.cfg)

        state = MarketState(
            name=state_name,
            label=STATE_LABELS.get(state_name, "未知"),
            score=STATE_SCORES.get(state_name, 35),
            position_suggest=STATE_POSITIONS.get(state_name, 0.25),
            adx=round(adx_val, 1),
            ma_arrangement=ma_arr,
            advance_ratio=round(advance_ratio, 2),
            details={
                "main_index_close": float(main_idx["close"].iloc[-1]),
                "ma20_trend": ma20_trend,
                "vol_shrink": vol_shrink,
                "last_date": str(main_idx["trade_date"].iloc[-1]),
                "ma5": round(main_idx["ma5"].iloc[-1], 1) if "ma5" in main_idx.columns else 0,
                "ma20": round(main_idx["ma20"].iloc[-1], 1) if "ma20" in main_idx.columns else 0,
                "ma60": round(main_idx["ma60"].iloc[-1], 1) if "ma60" in main_idx.columns else 0,
            },
        )

        LOG.info("大盘状态: %s (score=%.0f, pos=%.0f%%, ADX=%.1f, 涨跌比=%.2f)",
                 state.label, state.score, state.position_suggest * 100,
                 state.adx, state.advance_ratio)
        return state

    def evaluate_history(self, start_date: str, end_date: str = "") -> pd.DataFrame:
        """历史每日大盘状态评估（用于回测特征）"""
        indices_data = tdx.load_indices(self.index_codes, self.tdx_root,
                                         start_date=start_date, end_date=end_date)
        if not indices_data:
            return pd.DataFrame()

        main_idx = indices_data.get("000001.SH", list(indices_data.values())[0])
        if main_idx.empty:
            return pd.DataFrame()

        main_idx = tdx.calc_all_indicators(main_idx)
        records = []
        for i in range(60, len(main_idx)):
            chunk = main_idx.iloc[:i+1]
            last = chunk.iloc[-1]
            ma_arr = _detect_ma_arrangement(chunk)
            adx_val = last.get("adx", 0)
            advance_ratio = _calc_advance_ratio(chunk, {}, self.cfg)
            ma20_trend = _get_ma20_trend(chunk)
            vol_shrink = _is_vol_shrink(chunk)
            state_name = _detect_state(ma_arr, adx_val, advance_ratio,
                                        ma20_trend, vol_shrink, self.cfg)

            records.append({
                "trade_date": last["trade_date"],
                "market_state": state_name,
                "market_score": STATE_SCORES.get(state_name, 35),
                "adx": round(adx_val, 1),
                "ma_arrangement": ma_arr,
                "advance_ratio": round(advance_ratio, 2),
                "ma20_trend": ma20_trend,
                "close": last["close"],
            })

        return pd.DataFrame(records)


def get_market_state_features(state: MarketState) -> Dict[str, float]:
    """将大盘状态转为数值特征（供LightGBM使用）"""
    return {
        "market_score": state.score,
        "market_position_suggest": state.position_suggest,
        "market_adx": state.adx,
        "market_advance_ratio": state.advance_ratio,
        "market_is_bull": 1.0 if state.name == STATE_BULL else 0.0,
        "market_is_bear": 1.0 if state.name == STATE_BEAR else 0.0,
        "market_is_strong": 1.0 if state.name == STATE_STRONG else 0.0,
        "market_is_midadj": 1.0 if state.name == STATE_MID_ADJ else 0.0,
        "market_ma_bullish": 1.0 if state.ma_arrangement == "bullish" else 0.0,
        "market_ma_bearish": 1.0 if state.ma_arrangement == "bearish" else 0.0,
    }
