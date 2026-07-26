"""Money Flow Factor — 资金流评分.

使用: ETF成交额5日ZScore + 主题成交额增速 + 主力净流入估算。
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any, Dict, Optional

import numpy as np

from theme_engine.score_v3.config import get_factor_weights, get_norm_range
from theme_engine.score_v3.models import MoneyFlowResult

logger = logging.getLogger(__name__)


def normalize_zscore(value: float, max_abs: float = 3.0) -> float:
    """ZScore 归一化到 0~100: z=0 → 50, z=+3 → 100, z=-3 → 0."""
    clipped = max(-max_abs, min(max_abs, value))
    return (clipped + max_abs) / (2 * max_abs) * 100.0


async def calc_money_flow(
    theme_code: str,
    trade_date: str,
    etf_df=None,
    enriched_stocks: Optional[list] = None,
    **kwargs,
) -> MoneyFlowResult:
    """计算资金流评分.

    四个维度:
    1. ETF成交额ZScore: 今日成交额相对于过去20日的ZScore
    2. 主题成交额增速: 主题成分股总成交额5日增速
    3. 主力净流入估算: 基于涨跌+成交额的主力量能估算
    4. 涨幅-额协同: 价格上涨且有量配合
    """
    await asyncio.sleep(0)

    result = MoneyFlowResult()
    weights = get_factor_weights("money_flow")
    if not weights:
        return result

    sub_scores: Dict[str, float] = {}

    # 1. ETF成交额ZScore
    etf_zscore = 0.0
    if etf_df is not None and not etf_df.empty and "amount" in etf_df.columns:
        amounts = etf_df["amount"].values
        # 确保升序
        if len(amounts) >= 20:
            recent = amounts[-20:].astype(float)
            mean_amt = np.mean(recent[:-1])
            std_amt = np.std(recent[:-1])
            if std_amt > 0:
                etf_zscore = (recent[-1] - mean_amt) / std_amt
    etf_vol_score = normalize_zscore(etf_zscore, 3.0)
    sub_scores["etf_amount_change"] = etf_vol_score

    # 2. 主题成交额增速 (今日 vs 5日均)
    theme_amount_growth = 0.0
    if enriched_stocks:
        valid = [s for s in enriched_stocks if s.get("amount") is not None and s.get("amount", 0) > 0]
        if valid:
            amt_today = sum(s.get("amount", 0) for s in valid)
            # 单日数据无法算增速, 用个股平均成交额与个数的乘积变化替代
            avg_amt_per_stock = amt_today / max(len(valid), 1)
            # 用涨幅大于2%的个股占比作为增量资金信号
            strong_up_ratio = sum(1 for s in valid if s.get("pct_chg", 0) > 2) / max(len(valid), 1)
            # 涨幅>5%的个股占比 (强势资金)
            hot_ratio = sum(1 for s in valid if s.get("pct_chg", 0) > 5) / max(len(valid), 1)
            # 综合: 成交活跃度 = 上涨且有量配合
            theme_amount_growth = (strong_up_ratio * 50 + hot_ratio * 100)
    theme_vol_score = max(0.0, min(100.0, theme_amount_growth))
    sub_scores["theme_amount_change"] = theme_vol_score

    # 3. 涨跌幅-金额协同 (主力行为估算)
    main_inflow_score = 50.0
    if etf_df is not None and not etf_df.empty:
        closes = etf_df["close"].values
        amounts = etf_df["amount"].values if "amount" in etf_df.columns else None
        if len(closes) >= 5 and amounts is not None:
            # 今日涨跌幅
            pct_chg = (closes[-1] / closes[-2] - 1) * 100 if closes[-2] > 0 else 0
            # 今日成交额 vs 5日均
            amt_5d_avg = np.mean(amounts[-6:-1].astype(float)) if len(amounts) >= 6 else amounts[-1]
            vol_ratio = amounts[-1] / amt_5d_avg if amt_5d_avg > 0 else 1.0

            # 价格涨+量放 → 主力流入 (高分)
            # 价格跌+量缩 → 正常调整 (中分)
            # 价格涨+量缩 → 无量反弹 (低分)
            # 价格跌+量放 → 主力出货 (极低分)
            if pct_chg > 0 and vol_ratio > 1.1:
                main_inflow_score = 50 + min(50, (pct_chg * 5 + (vol_ratio - 1) * 50))
            elif pct_chg > 0 and vol_ratio <= 1.1:
                main_inflow_score = 50 - max(0, (1.1 - vol_ratio) * 30)
            elif pct_chg <= 0 and vol_ratio > 1.1:
                main_inflow_score = 50 - min(50, (abs(pct_chg) * 5 + (vol_ratio - 1) * 50))
            else:
                main_inflow_score = 50  # 跌且缩量, 中性

    sub_scores["main_net_inflow"] = max(0.0, min(100.0, main_inflow_score))

    # 4. 北向/机构资金代理 (用ETF溢价/折价率估算)
    northbound_score = 50.0
    if etf_df is not None and not etf_df.empty and "close" in etf_df.columns:
        closes = etf_df["close"].values
        if len(closes) >= 3:
            ret_3d = (closes[-1] / closes[-3] - 1) * 100
            # 3日正收益+量配合 = 机构流入
            if ret_3d > 2:
                northbound_score = 70
            elif ret_3d > 0:
                northbound_score = 60
            elif ret_3d > -2:
                northbound_score = 40
            else:
                northbound_score = 25
    sub_scores["northbound_flow"] = northbound_score

    matched_w = 0.0
    score = 0.0
    for key, w in weights.items():
        for sk, sv in sub_scores.items():
            if sk in key:
                score += sv * w
                matched_w += w
                break

    result.score = score / matched_w if matched_w > 0 else 0.0
    result.etf_amount_change = round(etf_zscore, 2)
    result.details = {
        "sub": {k: round(v, 2) for k, v in sub_scores.items()},
        "etf_vol_zscore": round(etf_zscore, 2),
    }

    return result
