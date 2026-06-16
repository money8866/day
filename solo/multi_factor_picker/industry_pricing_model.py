# -*- coding: utf-8 -*-
"""
产业资金定价AI模型（Industry Capital Pricing Model, ICPM）

不是选股模型，而是"产业周期定位 + 资金状态诊断"模型。
判断当前股票处于产业周期的哪个阶段、是否处于主升浪、是否见顶、是否可开仓。

核心原则：
  - 生命周期优先于基本面评分
  - 主线 > 因子 > 情绪
  - 不允许"高分但非主线"的股票进入主升浪
  - 不允许"周期反弹"误判为产业主升

输出结构（每只股票）：
  - lifecycle_stage: EARLY_STAGE / ACCUMULATION / MAINLINE_ACCELERATION / DISTRIBUTION / DECLINE
  - momentum_stage: 趋势加速细分
  - mainline_strength: 主线强度 0~1
  - capital_flow_state: STRONG_INFLOW / WEAK_INFLOW / NEUTRAL / OUTFLOW
  - entry_signal: BUY / HOLD / REDUCE / EXIT
  - risk_state: 风险状态描述
  - final_decision: 综合结论
"""
import os
import sys
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
import pandas as pd
import numpy as np
from loguru import logger

# ── 项目内部依赖 ──
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT_DIR = os.path.dirname(_BASE_DIR)
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

from data_fetcher import DataFetcher

# 尝试导入 LLM（tushare_quant.py 中的 deepseek 函数）
try:
    from tushare_quant import deepseek, get_hist_data, TRADE_DATE as TQ_TRADE_DATE, CACHE_DIR as TQ_CACHE_DIR
    _LLM_AVAILABLE = bool(os.getenv("DEEPSEEK_API_KEY"))
except (ImportError, Exception):
    deepseek = None
    get_hist_data = None
    TQ_TRADE_DATE = None
    TQ_CACHE_DIR = None
    _LLM_AVAILABLE = False


# ═══════════════════════════════════════════════
# 主线产业链定义
# ═══════════════════════════════════════════════

MAINLINE_THEMES = {
    # AI算力产业链（含光通信/光芯片/服务器）
    "AI算力链",
    "半导体设备链",
    "半导体材料链",
    # 光通信/光芯片的子集关键词匹配仍归入 AI算力链
}
"""严格的主线主题白名单。只有这些产业链才能被标注为 high mainline_strength"""


# ═══════════════════════════════════════════════
# 数据容器
# ═══════════════════════════════════════════════

@dataclass
class PricingInput:
    """产业资金定价输入"""
    ts_code: str
    name: str
    theme: str          # 产业链归属（如 "AI算力链"）
    industry: str       # 东财行业

    # ── 财务数据（年报最新期） ──
    revenue: float = 0.0
    revenue_yoy: float = 0.0       # 营收同比
    profit: float = 0.0
    profit_yoy: float = 0.0        # 净利润同比（>50% 主升浪条件之一）
    gross_margin: float = 0.0      # 毛利率
    roe: float = 0.0
    rd_ratio: float = 0.0          # 研发费用率

    # ── 订单/景气数据 ──
    contract_liability: float = 0.0
    contract_liability_yoy: float = 0.0  # 合同负债同比（订单爆发代理）
    advance_payment_yoy: float = 0.0

    # ── 市场表现 ──
    close: float = 0.0             # 最新收盘价
    pct_chg: float = 0.0           # 当日涨跌幅
    total_mv: float = 0.0          # 总市值
    circ_mv: float = 0.0           # 流通市值

    # ── 涨幅数据（200/120/60/20 日） ──
    pct_200d: float = 0.0          # 200日涨幅
    pct_120d: float = 0.0          # 120日涨幅
    pct_60d: float = 0.0           # 60日涨幅
    pct_20d: float = 0.0           # 20日涨幅

    # ── 资金流向 ──
    buy_lg_vol: float = 0.0        # 大单买入额
    sell_lg_vol: float = 0.0       # 大单卖出额
    buy_sm_vol: float = 0.0        # 小单买入额
    sell_sm_vol: float = 0.0       # 小单卖出额
    net_mf: float = 0.0            # 净流入

    # ── 行情每日K线（最近120日，用于技术面判定） ──
    kline_120d: pd.DataFrame = field(default_factory=pd.DataFrame)

    # ── 概念标签 ──
    concepts: List[str] = field(default_factory=list)


@dataclass
class PricingResult:
    """产业资金定价输出"""
    ts_code: str
    name: str
    theme: str
    industry: str

    # 核心诊断
    lifecycle_stage: str = ""       # EARLY_STAGE / ACCUMULATION / MAINLINE_ACCELERATION / DISTRIBUTION / DECLINE
    momentum_stage: str = ""        # 趋势阶段描述
    mainline_strength: float = 0.0  # 主线强度 0~1
    capital_flow_state: str = ""    # STRONG_INFLOW / WEAK_INFLOW / NEUTRAL / OUTFLOW
    entry_signal: str = ""          # BUY / HOLD / REDUCE / EXIT
    risk_state: str = ""            # 风险状态

    # 补充信号
    order_explosion_score: float = 0.0   # 订单爆发分 0~100
    expectation_score: float = 0.0       # 预期分 0~100
    is_mainline: bool = False            # 是否主线
    is_mainline_acceleration: bool = False  # 是否确认主升浪

    final_decision: str = ""         # 最终结论
    interpretation: str = ""         # 可读的解释

    # 子维度详情
    sub_details: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "ts_code": self.ts_code,
            "name": self.name,
            "theme": self.theme,
            "lifecycle_stage": self.lifecycle_stage,
            "momentum_stage": self.momentum_stage,
            "mainline_strength": round(self.mainline_strength, 3),
            "capital_flow_state": self.capital_flow_state,
            "entry_signal": self.entry_signal,
            "risk_state": self.risk_state,
            "final_decision": self.final_decision,
            "interpretation": self.interpretation,
        }


# ═══════════════════════════════════════════════
# 数据提取：从 Tushare 缓存提取 PricingInput
# ═══════════════════════════════════════════════

def extract_pricing_data(
    ts_code: str,
    name: str,
    theme: str,
    industry: str,
    financial_batch: Dict,
    daily_basic_row: Optional[pd.Series] = None,
    moneyflow_row: Optional[pd.Series] = None,
    kline_df: Optional[pd.DataFrame] = None,
) -> Optional[PricingInput]:
    """
    从财务缓存 + K线中提取 PricingInput

    Args:
        ts_code: 股票代码
        name: 股票名称
        theme: 产业链归属
        industry: 东财行业
        financial_batch: {ts_code: {income, balance, cashflow, forecast}}
        daily_basic_row: 当日市值行
        moneyflow_row: 当日资金流向行
        kline_df: 日K线DataFrame（含 trade_date, close, pct_chg 等）

    Returns:
        PricingInput or None
    """
    fin = financial_batch.get(ts_code, {})
    income = fin.get('income', pd.DataFrame()) if isinstance(fin.get('income'), pd.DataFrame) else pd.DataFrame()
    balance = fin.get('balance', pd.DataFrame()) if isinstance(fin.get('balance'), pd.DataFrame) else pd.DataFrame()

    if len(income) == 0:
        return None

    # ── 财务数据提取 ──
    inc_sorted = income.sort_values('end_date', ascending=False).reset_index(drop=True)
    bal_sorted = balance.sort_values('end_date', ascending=False).reset_index(drop=True) if len(balance) > 0 else pd.DataFrame()

    latest = inc_sorted.iloc[0]
    revenue = float(latest.get('revenue')) if pd.notna(latest.get('revenue')) else 0.0
    n_income = float(latest.get('n_income')) if pd.notna(latest.get('n_income')) else 0.0
    total_cogs = float(latest.get('total_cogs')) if pd.notna(latest.get('total_cogs')) else 0.0
    rd_exp = float(latest.get('rd_exp')) if pd.notna(latest.get('rd_exp')) else 0.0

    gross_margin = (revenue - total_cogs) / revenue if revenue > 0 else 0.0
    rd_ratio = rd_exp / revenue if revenue > 0 else 0.0

    # ROE
    equity = 0.0
    if len(bal_sorted) > 0:
        equity = float(bal_sorted.iloc[0].get('total_hldr_eqy_exc_min_int')) if pd.notna(bal_sorted.iloc[0].get('total_hldr_eqy_exc_min_int')) else 0.0
    roe = n_income / equity if equity > 0 else 0.0

    # ── 同比增速（年报） ──
    annual = inc_sorted[inc_sorted['end_date'].str.endswith('1231')].copy() if 'end_date' in inc_sorted.columns else inc_sorted.copy()
    rev_yoy, prof_yoy = 0.0, 0.0
    annual_sorted = annual.sort_values('end_date', ascending=False).reset_index(drop=True)
    if len(annual_sorted) >= 2:
        c = annual_sorted.iloc[0]
        cr = float(c.get('revenue')) if pd.notna(c.get('revenue')) else 0.0
        cp = float(c.get('n_income')) if pd.notna(c.get('n_income')) else 0.0
        prev_year = str(c.get('end_date', ''))[:4]
        prev_year_s = str(int(prev_year) - 1) if prev_year.isdigit() else ''
        prev_rows = annual_sorted[annual_sorted['end_date'].str.startswith(prev_year_s)]
        if len(prev_rows) > 0:
            p = prev_rows.iloc[-1]
            pr = float(p.get('revenue')) if pd.notna(p.get('revenue')) else 0.0
            pp = float(p.get('n_income')) if pd.notna(p.get('n_income')) else 0.0
            rev_yoy = (cr - pr) / pr if pr > 0 else 0.0
            prof_yoy = (cp - pp) / pp if pp > 0 else 0.0

    # ── 合同负债同比（订单代理） ──
    cl_yoy, ap_yoy = 0.0, 0.0
    if len(bal_sorted) >= 2:
        bal = bal_sorted
        lat, prv = bal.iloc[0], bal.iloc[1]
        cl_c = float(lat.get('contract_liability', 0)) if pd.notna(lat.get('contract_liability')) else 0.0
        cl_p = float(prv.get('contract_liability', 0)) if pd.notna(prv.get('contract_liability')) else 0.0
        if cl_p > 0:
            cl_yoy = (cl_c - cl_p) / cl_p
        ap_c = float(lat.get('advance_payment', 0)) if pd.notna(lat.get('advance_payment')) else 0.0
        ap_p = float(prv.get('advance_payment', 0)) if pd.notna(prv.get('advance_payment')) else 0.0
        if ap_p > 0:
            ap_yoy = (ap_c - ap_p) / ap_p

    # ── 市值 ──
    close, total_mv, circ_mv = 0.0, 0.0, 0.0
    pct_chg = 0.0
    if daily_basic_row is not None:
        close = float(daily_basic_row.get('close')) if pd.notna(daily_basic_row.get('close')) else 0.0
        total_mv = float(daily_basic_row.get('total_mv')) if pd.notna(daily_basic_row.get('total_mv')) else 0.0
        circ_mv = float(daily_basic_row.get('circ_mv')) if pd.notna(daily_basic_row.get('circ_mv')) else 0.0
        pct_chg = float(daily_basic_row.get('pct_chg')) if pd.notna(daily_basic_row.get('pct_chg')) else 0.0

    # ── 资金流向 ──
    net_mf = 0.0
    buy_lg = sell_lg = buy_sm = sell_sm = 0.0
    if moneyflow_row is not None:
        buy_lg = float(moneyflow_row.get('buy_lg_vol', 0)) if pd.notna(moneyflow_row.get('buy_lg_vol')) else 0.0
        sell_lg = float(moneyflow_row.get('sell_lg_vol', 0)) if pd.notna(moneyflow_row.get('sell_lg_vol')) else 0.0
        buy_sm = float(moneyflow_row.get('buy_sm_vol', 0)) if pd.notna(moneyflow_row.get('buy_sm_vol')) else 0.0
        sell_sm = float(moneyflow_row.get('sell_sm_vol', 0)) if pd.notna(moneyflow_row.get('sell_sm_vol')) else 0.0
        net_mf = buy_lg + buy_sm - sell_lg - sell_sm

    # ── K线涨幅计算（200/120/60/20日） ──
    pct_200d = pct_120d = pct_60d = pct_20d = 0.0
    kline_120d = pd.DataFrame()
    if kline_df is not None and not kline_df.empty:
        kline = kline_df.sort_values('trade_date', ascending=False).reset_index(drop=True)
        kline_120d = kline

        def _calc_pct(lookback: int) -> float:
            if len(kline) <= lookback:
                return 0.0
            start_close = float(kline.iloc[lookback].get('close', 0)) if pd.notna(kline.iloc[lookback].get('close')) else 0.0
            cur_close = float(kline.iloc[0].get('close', 0)) if pd.notna(kline.iloc[0].get('close')) else 0.0
            return (cur_close - start_close) / start_close if start_close > 0 else 0.0

        pct_200d = _calc_pct(min(200, len(kline) - 1))
        pct_120d = _calc_pct(min(120, len(kline) - 1))
        pct_60d = _calc_pct(min(60, len(kline) - 1))
        pct_20d = _calc_pct(min(20, len(kline) - 1))

    return PricingInput(
        ts_code=ts_code,
        name=name,
        theme=theme,
        industry=industry,
        revenue=revenue,
        revenue_yoy=rev_yoy,
        profit=n_income,
        profit_yoy=prof_yoy,
        gross_margin=gross_margin,
        roe=roe,
        rd_ratio=rd_ratio,
        contract_liability_yoy=cl_yoy,
        advance_payment_yoy=ap_yoy,
        close=close,
        pct_chg=pct_chg,
        total_mv=total_mv,
        circ_mv=circ_mv,
        pct_200d=pct_200d,
        pct_120d=pct_120d,
        pct_60d=pct_60d,
        pct_20d=pct_20d,
        buy_lg_vol=buy_lg,
        sell_lg_vol=sell_lg,
        buy_sm_vol=buy_sm,
        sell_sm_vol=sell_sm,
        net_mf=net_mf,
        kline_120d=kline_120d,
    )


# ═══════════════════════════════════════════════
# 核心诊断模型
# ═══════════════════════════════════════════════

class IndustryPricingModel:
    """产业资金定价诊断模型"""

    def __init__(self, config: Dict):
        self.config = config

    # ── 1. 主线强度判定 ──

    def _judge_mainline_strength(self, data: PricingInput) -> Tuple[float, bool, Dict]:
        """
        主线强度评分 0~1

        规则：
          - 主题属于 MAINLINE_THEMES 是必要条件
          - profit_yoy > 30% → +0.3
          - revenue_yoy > 20% → +0.2
          - contract_liability_yoy > 20% → +0.2 (订单爆发)
          - ROE > 15% → +0.15
          - 资金净流入 → +0.15
          - 基础分 0.1

        Returns:
            (mainline_strength, is_mainline, details)
        """
        base = 0.1
        details = {}

        # 主题判定
        is_mainline = data.theme in MAINLINE_THEMES
        details["theme_in_mainline"] = data.theme
        details["is_mainline_theme"] = is_mainline

        if not is_mainline:
            # 非主线主题最高只能 0.3
            strength = base
            if data.profit_yoy > 0.3:
                strength += 0.1
            if data.revenue_yoy > 0.2:
                strength += 0.1
            return round(min(strength, 0.3), 3), False, details

        # 主线主题：逐项加分
        score = base
        if data.profit_yoy > 0.5:
            score += 0.3
            details["profit_boom"] = True
        elif data.profit_yoy > 0.3:
            score += 0.2
            details["profit_growth"] = True

        if data.revenue_yoy > 0.3:
            score += 0.2
            details["revenue_boom"] = True
        elif data.revenue_yoy > 0.2:
            score += 0.15
            details["revenue_growth"] = True

        if data.contract_liability_yoy > 0.3:
            score += 0.2
            details["order_boom"] = True
        elif data.contract_liability_yoy > 0.15:
            score += 0.1
            details["order_growth"] = True

        if data.roe > 0.15:
            score += 0.15
            details["roe_high"] = True

        if data.net_mf > 0:
            score += 0.15
            details["capital_inflow"] = True

        strength = min(score, 1.0)
        details["total_score"] = round(strength, 3)
        return round(strength, 3), True, details

    # ── 2. 订单爆发评分 ──

    def _judge_order_explosion(self, data: PricingInput) -> Tuple[float, Dict]:
        """
        订单爆发评分 0~100

        规则：
          - contract_liability_yoy > 50% → 100
          - contract_liability_yoy > 30% → 80
          - contract_liability_yoy > 15% → 60
          - contract_liability_yoy > 0% → 40
          - 否则 0
        """
        cl = data.contract_liability_yoy
        if cl > 0.5:
            return 100, {"order_explosion": "爆发", "contract_liability_yoy": round(cl, 3)}
        elif cl > 0.3:
            return 80, {"order_explosion": "高增长", "contract_liability_yoy": round(cl, 3)}
        elif cl > 0.15:
            return 60, {"order_explosion": "增长中", "contract_liability_yoy": round(cl, 3)}
        elif cl > 0:
            return 40, {"order_explosion": "微增", "contract_liability_yoy": round(cl, 3)}
        return 0, {"order_explosion": "无增长", "contract_liability_yoy": round(cl, 3)}

    # ── 3. 预期评分 ──

    def _judge_expectation(self, data: PricingInput) -> Tuple[float, Dict]:
        """
        预期评分 0~100

        规则：
          - profit_yoy > 50% → 90+
          - profit_yoy > 30% → 75
          - revenue_yoy > 30% → 85
          - revenue_yoy > 20% → 70
          - 低增速但高 ROE + 高毛利 → 60
        """
        details = {}
        if data.profit_yoy > 0.5:
            score = 95 if data.revenue_yoy > 0.3 else 90
            details["expectation"] = "极高增长"
        elif data.profit_yoy > 0.3:
            score = 85 if data.revenue_yoy > 0.2 else 75
            details["expectation"] = "高增长"
        elif data.revenue_yoy > 0.3:
            score = 85
            details["expectation"] = "营收高增长"
        elif data.revenue_yoy > 0.2:
            score = 70
            details["expectation"] = "营收增长"
        elif data.roe > 0.12 and data.gross_margin > 0.3:
            score = 60
            details["expectation"] = "稳健但增速不高"
        else:
            score = 40
            details["expectation"] = "低增长"

        details["profit_yoy"] = round(data.profit_yoy, 3)
        details["revenue_yoy"] = round(data.revenue_yoy, 3)
        return score, details

    # ── 4. 资金流状态判定 ──

    def _judge_capital_flow(self, data: PricingInput) -> Tuple[str, Dict]:
        """
        资金流状态判定

        STRONG_INFLOW: 大单净买入且总净流入为正，且大单净额 > 总净流入的 60%
        WEAK_INFLOW: 总净流入为正
        NEUTRAL: 净流入接近零
        OUTFLOW: 总净流入为负
        """
        net = data.net_mf
        lg_net = data.buy_lg_vol - data.sell_lg_vol
        total = abs(data.buy_lg_vol) + abs(data.sell_lg_vol) + abs(data.buy_sm_vol) + abs(data.sell_sm_vol)

        details = {
            "net_mf": round(net, 2),
            "lg_net": round(lg_net, 2),
        }

        if total < 1:
            return "NEUTRAL", {**details, "reason": "无资金流向数据"}

        lg_ratio = lg_net / net if abs(net) > 1 else 0

        if net > 0 and lg_ratio > 0.6 and lg_net > 0:
            return "STRONG_INFLOW", {**details, "reason": "大单主导净买入"}
        elif net > 0:
            return "WEAK_INFLOW", {**details, "reason": "中小单净流入"}
        elif net > -1e6:
            return "NEUTRAL", {**details, "reason": "资金平衡"}
        else:
            return "OUTFLOW", {**details, "reason": "资金净流出"}

    # ── 5. 主升浪判定 ──

    def _judge_mainline_acceleration(self, data: PricingInput,
                                      order_score: float, exp_score: float,
                                      is_mainline: bool,
                                      capital_state: str) -> Tuple[bool, str, Dict]:
        """
        主升浪确认判定

        必须同时满足：
          1. revenue_yoy > 30%
          2. profit_yoy > 50%
          3. order_explosion_score > 80
          4. expectation_score > 85
          5. capital_flow_state == STRONG_INFLOW 或 WEAK_INFLOW
          6. theme 属于主线

        否则不得进入主升浪

        Returns:
            (is_acceleration, momentum_stage, details)
        """
        details = {
            "revenue_yoy_ok": data.revenue_yoy > 0.3,
            "profit_yoy_ok": data.profit_yoy > 0.5,
            "order_ok": order_score > 80,
            "expectation_ok": exp_score > 85,
            "capital_ok": capital_state in ("STRONG_INFLOW", "WEAK_INFLOW"),
            "mainline_ok": is_mainline,
        }

        if not is_mainline:
            return False, "非主线，不符合主升浪条件", details

        # 严格检查6条件
        all_ok = all(details.values())
        if all_ok:
            return True, "MAINLINE_ACCELERATION（产业β+业绩+资金三重共振）", details

        # 部分满足：判断处于哪个阶段
        partial_count = sum(1 for v in details.values() if v)
        if partial_count >= 4:
            return False, "ACCUMULATION（接近主升浪，缺部分条件）", details
        elif partial_count >= 2:
            return False, "EARLY_STAGE（萌芽期，多条件不满足）", details
        else:
            return False, "EARLY_STAGE（早期，主题出现但数据和资金未到）", details

    # ── 6. 见顶信号判定 ──

    def _judge_distribution(self, data: PricingInput) -> Tuple[bool, str, Dict]:
        """
        见顶/分歧信号判定

        满足任意 2 条标记为 DISTRIBUTION：
          - 涨幅 > 300%（pct_200d > 3.0）
          - 利润增长但股价加速下降（profit_yoy > 0 但 60日涨幅 < -10%）
          - 资金流出（OUTFLOW）
          - 订单下降（contract_liability_yoy < 0）
        """
        signals = []
        details = {}

        # 涨幅 > 300%
        if data.pct_200d > 3.0:
            signals.append("涨幅超300%")
            details["price_300pct"] = True

        # 利润增长但股价下降
        if data.profit_yoy > 0 and data.pct_60d < -0.1:
            signals.append("利润增长但股价加速下降")
            details["profit_up_price_down"] = True

        # 资金流出
        if data.net_mf < -1e6:
            signals.append("资金净流出")
            details["capital_outflow"] = True

        # 订单下降
        if data.contract_liability_yoy < -0.1:
            signals.append("订单下降")
            details["order_decline"] = True

        is_distribution = len(signals) >= 2
        if is_distribution:
            return True, f"DISTRIBUTION（{' + '.join(signals)}）", details
        elif len(signals) == 1:
            return False, f"关注信号（{signals[0]}）", details
        else:
            return False, "无明显见顶信号", details

    # ── 7. 综合生命周期判定 ──

    def _determine_lifecycle(self, data: PricingInput,
                              is_mainline_acc: bool,
                              is_mainline: bool,
                              is_distribution: bool,
                              mainline_strength: float,
                              order_score: float,
                              capital_state: str) -> Tuple[str, float, str, str, str]:
        """
        综合生命周期判定（生命周期优先级最高）

        规则树：
          1. 如果 is_distribution → DISTRIBUTION
          2. 如果 order_score < 20 且 profit_yoy < 0.1 → EARLY_STAGE
          3. 如果 is_mainline_acc → MAINLINE_ACCELERATION
          4. 如果 mainline_strength >= 0.4 且 capital_state 非 OUTFLOW → ACCUMULATION
          5. 如果 capital_state == OUTFLOW 且 profit_yoy < 0 → DECLINE
          6. 否则 → EARLY_STAGE
        """
        if is_distribution:
            return ("DISTRIBUTION", 0.5, "分歧/顶部震荡",
                    "REDUCE", "分歧期，减仓/止盈为主")

        if order_score < 20 and data.profit_yoy < 0.1:
            return ("EARLY_STAGE", 0.3, "产业萌芽",
                    "BUY", "小仓试错，主题刚出现，数据未验证")

        if is_mainline_acc:
            return ("MAINLINE_ACCELERATION", 1.0, "主升浪",
                    "BUY", "主仓持有，不频繁交易，增长+资金+情绪三重共振")

        if mainline_strength >= 0.4 and capital_state != "OUTFLOW":
            return ("ACCUMULATION", 0.7, "资金建仓",
                    "BUY", "核心布局区，订单放量+业绩加速+资金进入")

        if capital_state == "OUTFLOW" and data.profit_yoy < 0:
            return ("DECLINE", 0.1, "退潮",
                    "EXIT", "清仓，产业逻辑结束+资金流出")

        return ("EARLY_STAGE", 0.2, "产业萌芽",
                "BUY", "小仓试错，等待数据验证")

    # ── 8. 单只股票诊断入口 ──

    def diagnose(self, data: PricingInput) -> PricingResult:
        """
        对单只股票进行产业资金定价诊断

        Args:
            data: PricingInput

        Returns:
            PricingResult
        """
        sub = {}

        # 1. 主线强度
        mainline_str, is_mainline, m_details = self._judge_mainline_strength(data)
        sub["mainline_strength"] = m_details

        # 2. 订单爆发
        order_score, o_details = self._judge_order_explosion(data)
        sub["order_explosion"] = o_details

        # 3. 预期评分
        exp_score, e_details = self._judge_expectation(data)
        sub["expectation"] = e_details

        # 4. 资金流状态
        capital_state, c_details = self._judge_capital_flow(data)
        sub["capital_flow"] = c_details

        # 5. 主升浪判定
        is_acc, momentum_stage, acc_details = self._judge_mainline_acceleration(
            data, order_score, exp_score, is_mainline, capital_state
        )
        sub["mainline_acceleration"] = acc_details

        # 6. 见顶信号
        is_dist, dist_stage, dist_details = self._judge_distribution(data)
        sub["distribution"] = dist_details

        # 7. 综合生命周期判定
        lifecycle_stage, method_confidence, risk_state, entry_signal, interp_short = \
            self._determine_lifecycle(
                data, is_acc, is_mainline, is_dist,
                mainline_str, order_score, capital_state
            )

        # 8. 最终决策文本
        parts = []
        parts.append(f"【{lifecycle_stage}】{risk_state}")
        if is_mainline:
            parts.append("主线主题")
        if is_acc:
            parts.append("✅ 主升浪确认")
        if is_dist:
            parts.append("⚠️ 见顶信号")
        parts.append(f"订单爆发分={order_score:.0f}")
        parts.append(f"预期分={exp_score:.0f}")
        parts.append(f"资金状态={capital_state}")
        interpretation = " | ".join(parts)

        final_decision = entry_signal
        if entry_signal == "BUY" and is_acc:
            final_decision = "BUY"
            interpretation = f"【{lifecycle_stage}】🚀 主升浪确认！" + " | ".join(parts[1:])

        return PricingResult(
            ts_code=data.ts_code,
            name=data.name,
            theme=data.theme,
            industry=data.industry,
            lifecycle_stage=lifecycle_stage,
            momentum_stage=momentum_stage,
            mainline_strength=mainline_str,
            capital_flow_state=capital_state,
            entry_signal=entry_signal,
            risk_state=risk_state,
            order_explosion_score=order_score,
            expectation_score=exp_score,
            is_mainline=is_mainline,
            is_mainline_acceleration=is_acc,
            final_decision=final_decision,
            interpretation=interpretation,
            sub_details=sub,
        )

    # ── 9. 批量诊断 ──

    def diagnose_batch(self, data_list: List[PricingInput]) -> List[PricingResult]:
        """批量诊断"""
        results = [self.diagnose(d) for d in data_list]
        # 按 final_decision 优先级排序：BUY > HOLD > REDUCE > EXIT
        priority = {"BUY": 0, "HOLD": 1, "REDUCE": 2, "EXIT": 3}
        results.sort(key=lambda r: priority.get(r.final_decision, 99))
        return results


# ═══════════════════════════════════════════════
# 输出辅助
# ═══════════════════════════════════════════════

def print_pricing_summary(results: List[PricingResult], top_n: int = 20):
    """打印诊断结果摘要"""
    if not results:
        logger.warning("无诊断结果")
        return

    # 按 mainline_strength 排序
    sorted_r = sorted(results, key=lambda r: r.mainline_strength, reverse=True)

    print("\n" + "=" * 100)
    print(f"{'代码':<10} {'名称':<10} {'主题':<12} {'生命周期':<22} {'主线':<6} {'资金':<6} {'决策':<6}")
    print("=" * 100)

    for r in sorted_r[:top_n]:
        ls_short = r.lifecycle_stage[:14]
        ms = f"{r.mainline_strength:.2f}"
        print(f"{r.ts_code:<10} {r.name:<10} {r.theme:<12} {ls_short:<22} {ms:<6} {r.capital_flow_state:<6} {r.final_decision:<6}")

    print("=" * 100)

    # 按最终决策汇总
    decision_counts = {}
    for r in results:
        decision_counts[r.final_decision] = decision_counts.get(r.final_decision, 0) + 1
    print(f"决策汇总: {decision_counts}")

    # 生命周期分布
    lifecycle_counts = {}
    for r in results:
        lifecycle_counts[r.lifecycle_stage] = lifecycle_counts.get(r.lifecycle_stage, 0) + 1
    print(f"生命周期分布: {lifecycle_counts}")


# ═══════════════════════════════════════════════
# LLM 增强诊断（可选）
# ═══════════════════════════════════════════════

def llm_enhance_diagnosis(data: PricingInput, rule_result: PricingResult) -> Optional[PricingResult]:
    """
    使用 LLM（DeepSeek）对规则引擎结果进行增强验证

    Args:
        data: 输入数据
        rule_result: 规则引擎结果

    Returns:
        如果 LLM 可用，返回增强后的结果；否则返回原始结果
    """
    if not _LLM_AVAILABLE or deepseek is None:
        return None

    prompt = f"""你是一个A股"产业资金定价AI模型"。请对以下股票进行诊断，以JSON格式返回。

股票: {data.name}({data.ts_code})
所属产业链: {data.theme}
所属行业: {data.industry}

财务数据:
- 营收: {data.revenue:.2e}, 营收同比: {data.revenue_yoy*100:.1f}%
- 净利润: {data.profit:.2e}, 净利润同比: {data.profit_yoy*100:.1f}%
- ROE: {data.roe*100:.1f}%
- 毛利率: {data.gross_margin*100:.1f}%
- 研发费用率: {data.rd_ratio*100:.1f}%
- 合同负债同比: {data.contract_liability_yoy*100:.1f}%

行情表现:
- 最新价: {data.close}
- 60日涨幅: {data.pct_60d*100:.1f}%
- 200日涨幅: {data.pct_200d*100:.1f}%

规则引擎初步判定:
- 生命周期阶段: {rule_result.lifecycle_stage}
- 主线强度: {rule_result.mainline_strength:.2f}
- 资金状态: {rule_result.capital_flow_state}
- 决策信号: {rule_result.entry_signal}

请严格按以下规则验证/纠正，仅返回纯JSON：

{{
  "lifecycle_stage": "MAINLINE_ACCELERATION或ACCUMULATION或EARLY_STAGE或DISTRIBUTION或DECLINE",
  "reasoning": "一句话解释判断依据（20字内）",
  "mainline_strength_verify": 0~1,
  "capital_flow_state_verify": "STRONG_INFLOW或WEAK_INFLOW或NEUTRAL或OUTFLOW",
  "entry_signal_verify": "BUY或HOLD或REDUCE或EXIT"
}}

约束：
- 非AI算力/半导体设备/半导体材料链的股票，mainline_strength 最高 0.3
- 只有 revenue_yoy>30% AND profit_yoy>50% AND 订单爆发 AND 主线 theme 才能进入 MAINLINE_ACCELERATION
- 见顶信号：涨幅>300% 或 利润增长但股价跌 或 资金流出 或 订单下降（满足2条即为DISTRIBUTION）
"""
    try:
        resp = deepseek(prompt)
        if not resp:
            return None

        json_match = re.search(r'\{.*\}', resp, re.DOTALL)
        if json_match:
            data_llm = json.loads(json_match.group())
            # 仅当 LLM 置信度明确且与规则引擎判断一致时才替换
            # 这里仅记录 LLM 建议，不覆盖规则引擎结果
            logger.info(f"[LLM] {data.name}: lifecycle={data_llm.get('lifecycle_stage','?')} "
                        f"entry={data_llm.get('entry_signal_verify','?')} "
                        f"reason={data_llm.get('reasoning','')}")
        return None
    except Exception as e:
        logger.warning(f"[LLM] {data.name} LLM 增强诊断失败: {e}")
        return None


# ═══════════════════════════════════════════════
# 对外便捷接口
# ═══════════════════════════════════════════════

def diagnose_stock(
    ts_code: str,
    name: str,
    theme: str,
    industry: str,
    financial_batch: Dict,
    daily_basic_row: Optional[pd.Series] = None,
    moneyflow_row: Optional[pd.Series] = None,
    kline_df: Optional[pd.DataFrame] = None,
    config: Optional[Dict] = None,
    enable_llm: bool = False,
) -> Optional[PricingResult]:
    """
    单只股票诊断的便捷入口

    Args:
        ts_code: 股票代码
        name: 股票名称
        theme: 产业链归属
        industry: 东财行业
        financial_batch: {ts_code: {income, balance, cashflow, forecast}}
        daily_basic_row: 当日市值/行情行
        moneyflow_row: 资金流向行
        kline_df: 日K线DataFrame
        config: 配置（可选，默认空字典）
        enable_llm: 是否启用 LLM 增强诊断

    Returns:
        PricingResult or None（数据不足时）
    """
    data = extract_pricing_data(
        ts_code, name, theme, industry,
        financial_batch, daily_basic_row, moneyflow_row, kline_df
    )
    if data is None:
        return None

    model = IndustryPricingModel(config or {})
    result = model.diagnose(data)

    if enable_llm:
        llm_enhance_diagnosis(data, result)

    return result


# ═══════════════════════════════════════════════
# 主程序入口
# ═══════════════════════════════════════════════

def main():
    """产业资金定价批量诊断主程序"""
    logger.remove()
    logger.add(sys.stderr, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>")

    logger.info("=" * 60)
    logger.info("产业资金定价AI模型 (ICPM) 批量诊断")
    logger.info(f"LLM 增强: {'可用' if _LLM_AVAILABLE else '不可用'}")
    logger.info("=" * 60)

    # 加载配置
    config_path = str(Path(__file__).parent / 'config.yaml')
    import yaml
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # 获取 Token
    token_env = config.get('tushare', {}).get('token_env', 'TUSHARE_TOKEN')
    token = os.environ.get(token_env)
    if not token:
        env_paths = [
            Path(__file__).resolve().parent.parent.parent / "config" / ".env",
            Path(__file__).resolve().parent.parent / "config" / ".env",
        ]
        for ep in env_paths:
            if ep.exists():
                with open(ep, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith('#'):
                            continue
                        if '=' in line:
                            k, v = line.split('=', 1)
                            if k.strip() == token_env:
                                token = v.strip().strip('"\'')
                                break
                break
    if not token:
        logger.error(f"未找到 Tushare Token，请设置环境变量 {token_env}")
        sys.exit(1)

    fetcher = DataFetcher(token, config)

    # ── 1. 获取股票列表 ──
    stocks = fetcher.get_stock_list(list_status='L')
    if config.get('universe', {}).get('exclude_st', True):
        stocks = stocks[~stocks['name'].str.contains('ST', na=False)]
    logger.info(f"待筛选股票: {len(stocks)}")

    # ── 2. 获取最近交易日 ──
    trade_date = fetcher.get_last_trade_date()
    logger.info(f"最近交易日: {trade_date}")

    # ── 3. 获取财务数据（复用缓存） ──
    ts_code_list = stocks['ts_code'].tolist()
    start_year = str(datetime.now().year - 3)
    logger.info("获取财务数据（复用缓存）...")
    financial_batch = fetcher.get_stock_financial_batch(ts_code_list, start_year=start_year, max_workers=16)
    logger.info(f"财务数据: {len(financial_batch)} 只")

    # ── 4. 获取当日行情/市值 ──
    logger.info("获取当日行情数据...")
    daily_basic = fetcher.get_daily_basic(trade_date)
    daily = fetcher.get_daily(trade_date)
    moneyflow = fetcher.get_moneyflow(trade_date)
    logger.info(f"  daily_basic: {len(daily_basic) if daily_basic is not None else 0}")
    logger.info(f"  moneyflow: {len(moneyflow) if moneyflow is not None else 0}")

    # 构建索引
    daily_basic_index = {}
    if daily_basic is not None and not daily_basic.empty:
        for _, row in daily_basic.iterrows():
            daily_basic_index[row['ts_code']] = row

    moneyflow_index = {}
    if moneyflow is not None and not moneyflow.empty:
        for _, row in moneyflow.iterrows():
            moneyflow_index[row['ts_code']] = row

    # ── 5. 加载主题归属（默认从财务批次中推断） ──
    # 用户可以在运行时通过 theme_override 指定主题
    # 默认使用 industry 作为主题

    # ── 6. 提取 PricingInput ──
    logger.info("提取诊断数据...")
    pricing_inputs = []
    skip = 0
    for _, row in stocks.iterrows():
        ts_code = row['ts_code']
        name = row['name']
        industry = str(row.get('industry', '')) if pd.notna(row.get('industry', '')) else ''

        # 默认使用 industry 作为 theme（用户可覆盖）
        theme = industry

        # 获取K线
        kline = None

        pricing = extract_pricing_data(
            ts_code, name, theme, industry,
            financial_batch,
            daily_basic_index.get(ts_code),
            moneyflow_index.get(ts_code),
            kline_df=kline,
        )
        if pricing is not None:
            pricing_inputs.append(pricing)
        else:
            skip += 1

    logger.info(f"有效数据: {len(pricing_inputs)} 只, 跳过: {skip} 只")

    # ── 7. 批量诊断 ──
    logger.info("运行产业资金定价诊断...")
    model = IndustryPricingModel(config)
    results = model.diagnose_batch(pricing_inputs)
    logger.info(f"诊断完成: {len(results)} 只")

    # ── 8. 输出 ──
    print_pricing_summary(results, top_n=30)

    # ── 9. 保存 CSV ──
    output_dir = Path(config.get('output', {}).get('dir', 'output'))
    if not output_dir.is_absolute():
        output_dir = Path(__file__).parent / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_path = output_dir / f"icpm_diagnosis_{ts}.csv"

    dict_list = [r.to_dict() for r in results]
    df_out = pd.DataFrame(dict_list)
    df_out.to_csv(out_path, index=False, encoding='utf-8-sig')
    logger.info(f"已保存: {out_path}")

    logger.info("=" * 60)
    logger.info("产业资金定价诊断完成")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
