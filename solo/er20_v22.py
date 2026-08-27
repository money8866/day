# -*- coding: utf-8 -*-
"""
ER20 V2.2 — Price-Absorption Gated Earnings Engine
=====================================================
在 V2.1 基础上增量升级（不推翻 V2 / V2.1，只优化决策逻辑）。

核心原则：
  "业绩决定有没有20日逻辑，价格吸收决定预期差是否还在，技术触发决定今天能不能买。"

V2.1 → V2.2 升级映射
─────────────────────────────────────────────────────────
V2.1 模块                     → V2.2 变更
─────────────────────────────────────────────────────────
cashflow 6分类                → 7分类（+SEASONAL_CYCLE），CF_Adj 对齐区间
calc_alpha_decay（tier衰减）  → PriceAbsorption 六量 + 公告前大涨检测 + Decay 三态
Refresh 任意状态可触发        → 仅 SECONDARY_CONFIRM + 4 条件（回踩未破/缩量/放量/RS改善）
entry_engine（5维Entry）      → Trigger 三分类(T1/T2/T3) + TriggerScore 0~100
calc_early_entry_score        → EES = Trend+Trigger+Volume+PQS−Overextension
grade_v21 门槛                → 规格门槛(CORE: A≥85/EES≥80/TS≥80/R≤35; TEST: A≥75/EES≥70/TS≥70/R≤50; PROBE: A≥70/EES≥60)
组合仓位(20%/12%/2%)          → CORE 15% / TEST 12% / PROBE 5% + 5~8只 + 同主题≤30%
报告 8 榜单                    → 规格 6 区块固定格式（ALPHA TOP20 与 TODAY BUY 完全分离）

评分链（与规格一致）：
  ER20_BASE = norm × Conf/100 × (1−RelRisk×0.40) × Market + CF_Adj + Theme
  ALPHA     = ER20_BASE × DecayMultiplier + Refresh − EQ_Penalty
  EES       = 0.25·Trend + 0.30·Trigger + 0.15·Volume + 0.20·PQS − 0.10·Overextension

用法：
  python -X utf8 er20_v22.py --date 20260820 [--validate] [--compare]
"""
import os, sys, json, time, sqlite3, argparse
from collections import Counter

import numpy as np
import pandas as pd

SOLO_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SOLO_DIR)
sys.path.insert(0, os.path.join(SOLO_DIR, 'multi_factor_picker'))
sys.path.insert(0, os.path.join(SOLO_DIR, 'etf_alpha_ranking'))

CACHE_DIR = r'D:\mystock\cache_daily'
REPORT_DIR = r'D:\mystock\solo\report_daily'
TDX_PATH = r'C:\new_tdx\vipdoc'
DB_PATH_V22 = os.path.join(REPORT_DIR, 'er20_v22_scores.db')

# ── 复用 V2 全部接口 ──
from bts.data import load_daily, get_trade_dates, parse_tdx_day_file, load_stock_basic
from bts.indicators import add_ma, add_rsi, ma_slope
from er20_strategy import (calc_atr14, calc_ret_pct, _calc_q2_single, theme_map_stock2theme)
from er20_v2 import (
    ER20Config, _valid, safe_score, _pct, calc_macd, load_bench,
    load_pool_v2, load_daily_for, classify_event,
    fundamental_quality, calc_rqs, expect_gap_score, overheat_penalty,
    calc_ars, trend_structure, volume_structure, pullback_quality,
    breakout_quality, momentum_score, support_structure, calc_tqs,
    risk_score, data_confidence, market_multiplier, theme_score,
    entry_engine, position_engine, save_sqlite as _save_sqlite_v2,
    grade_v2, validate_er20_scores,
)
# ── 复用 V2.1 已修复模块（P0/P1 修复成果） ──
from er20_v21 import (
    V21Config, _get_industry_map, _detect_cyclical,
    calc_event_age_tradingdays, relative_risk_score,
    earnings_quality_context, data_confidence_v21,
    calc_support_distance, _precompute_industry_atr,
)


# ============================================================
# V22Config — V2.2 全部阈值与权重（单点可调）
# ============================================================
class V22Config:
    # ── 现金流语境 7 分类 + 调整区间 ──
    CASHFLOW = {
        'cfcs_healthy': 85,
        'cfcs_seasonal': 60,
        'cfcs_inventory': 70,
        'cfcs_wcap': 65,
        'cfcs_receivable': 35,
        'cfcs_structural': 18,
        'adj_healthy_max': 5.0,       # HEALTHY +0~5
        'adj_inventory_min': -2.0,    # INVENTORY_BUILD 0~-2
        'adj_wcap_min': -3.0,         # WCAP_EXPANSION 0~-3
        'adj_receivable_min': -10.0,  # RECEIVABLE_RISK -10~-20
        'adj_receivable_max': -20.0,
        'adj_structural_min': -5.0,   # STRUCTURAL -5~-15
        'adj_structural_max': -15.0,
        'rev_growth_min': 20,
        'ar_turn_risk': 1.0,
    }
    # ── 季节性行业（现金流季度性波动豁免） ──
    SEASONAL_KEYWORDS = {'白酒', '食品', '饮料', '啤酒', '乳业', '农业', '养殖',
                         '旅游', '餐饮', '零售', '服装', '服饰', '家电',
                         '纺服', '商业', '消费', '日化', '美容'}
    # ── Price Absorption / Decay 三态 ──
    DECAY = {
        'tiers': [(2, 0.00), (5, 0.03), (10, 0.08), (15, 0.15), (20, 0.25)],
        'abs_ret_range': 0.04,     # 缩量横盘判定
        'abs_vol_ratio': 0.70,
        'priced_in_ret': 0.08,     # 连续上涨放量 → PRICED_IN
        'priced_in_vol': 1.20,
        'pre_priced_ret': 0.20,    # 公告前20日涨幅>20% → 预期差耗尽
        'pre_priced_gap': 0.05,    # 公告前已涨12%+ 且公告日高开>5%
        'not_absorbed_max': 0.03,  # NOT_ABSORBED decay 区间
        'secondary_min': 0.03,     # SECONDARY_CONFIRM decay 区间
        'secondary_max': 0.08,
        'priced_in_min': 0.15,     # PRICED_IN decay 下限
        'refresh_max': 8.0,        # Refresh 0~8
        'refresh_vol_ratio': 1.30,
        'refresh_vol_ok': 0.90,    # 回踩期缩量阈值
    }
    # ── Trigger 三分类 ──
    TRIGGER = {
        'breakout_vol': 1.20,      # T1 放量阈值
        'breakout_close': 0.70,    # 收盘位置加分阈值
        'pullback_d20_lo': -0.03,  # T2 回踩区间
        'pullback_d20_hi': 0.04,
        'pullback_vol_max': 1.30,
        'reclaim_vol': 1.00,       # T3 收复量能
    }
    # ── EES 权重（规格：Trend+Trigger+Volume+PQS−Overextension） ──
    EES = {'trend': 0.25, 'trigger': 0.30, 'volume': 0.15, 'pqs': 0.20, 'overheat': 0.10}
    # ── 买入门槛（规格九） ──
    GATE = {
        'core_alpha': 85.0, 'core_ees': 80.0, 'core_ts': 80.0,
        'core_risk': 35.0, 'core_conf': 80.0, 'core_fq': 35.0,
        'core_overheat': 20.0,
        'test_alpha': 80.0, 'test_ees': 72.0, 'test_ts': 72.0, 'test_risk': 50.0,
        'probe_alpha': 72.0, 'probe_ees': 60.0,
        'watch_alpha': 60.0,
        'fq_floor_reject': 25.0,
        'rqs_high_grade': 70.0,
        'conf_watch': 50.0,
        'overheat_pullback': 25.0,   # 透支>25 → WAIT_PULLBACK
    }
    NEXT5 = {
        'limit': 2,
        'alpha': 0.35,
        'ees': 0.25,
        'ts': 0.20,
        'conf': 0.10,
        'risk': 0.10,
    }
    FUSION = {
        'er20': 0.50,
        'egpt_quality': 0.25,
        'egpt_pullback': 0.15,
        'execution': 0.10,
        'min_score': 70.0,
        'second_gap': 12.0,
    }
    # ── 组合控制（规格十） ──
    PORTFOLIO = {
        'core_pos': 0.15, 'test_pos': 0.12, 'probe_pos': 0.05,
        'max_hold': 8,                # 重点持仓 5~8 只
        'theme_cap': 0.30,            # 同主题总仓位 ≤30%
    }


class SeptemberConfig:
    FUNDAMENTAL_CORE = 46.0
    FUNDAMENTAL_WATCH = 34.0
    SETUP_READY = 26.0
    SETUP_WATCH = 18.0
    MAX_PRE_RET = 20.0
    MAX_POST_RET = 12.0
    MAX_OVERHEAT = 20.0


def _september_num(value):
    try:
        value = float(value)
        return value if np.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def calc_september_opportunity(r, cfcs, cf_label, eq_label, pre_ret, post_ret,
                               overheat, rel_risk, decay_state, trigger_type,
                               trigger_score, pullback_quality_score):
    dt = _september_num(r.get('dt_netprofit_yoy'))
    ni = _september_num(r.get('netprofit_yoy'))
    rev = _september_num(r.get('tr_yoy'))
    q1 = _september_num(r.get('q1_profit_yoy'))
    q2 = _september_num(r.get('q2_profit_yoy'))
    q_sales = _september_num(r.get('q_sales_yoy'))
    gm = _september_num(r.get('grossprofit_margin'))
    roe = _september_num(r.get('roe'))
    ar_turn = _september_num(r.get('ar_turn'))
    ca_turn = _september_num(r.get('ca_turn'))

    growth = 0.0
    growth += 10.0 if dt is not None and dt >= 30 else 6.0 if dt is not None and dt >= 15 else 2.0 if dt is not None and dt > 0 else 0.0
    growth += 8.0 if ni is not None and ni >= 30 else 5.0 if ni is not None and ni >= 15 else 2.0 if ni is not None and ni > 0 else 0.0
    growth += 10.0 if rev is not None and rev >= 20 else 6.0 if rev is not None and rev >= 10 else 2.0 if rev is not None and rev > 0 else 0.0

    continuation = 0.0
    accel = q2 - q1 if q2 is not None and q1 is not None else None
    continuation += 10.0 if accel is not None and accel >= 20 else 7.0 if accel is not None and accel >= 0 else 3.0 if accel is not None and accel >= -20 else 0.0
    continuation += 10.0 if q_sales is not None and rev is not None and q_sales >= rev else 6.0 if q_sales is not None and q_sales > 0 else 2.0 if q_sales is not None and q_sales >= -10 else 0.0

    quality = 0.0
    quality += 10.0 if cf_label == 'HEALTHY_CASHFLOW' else 7.0 if cf_label in ('SEASONAL_CYCLE', 'INVENTORY_BUILD', 'WORKING_CAPITAL_EXPANSION') else 2.0 if cf_label == 'DATA_INCOMPLETE' else 0.0
    quality += 4.0 if cfcs is not None and cfcs >= 75 else 2.0 if cfcs is not None and cfcs >= 60 else 0.0
    quality += 3.0 if gm is not None and gm >= 20 else 1.0 if gm is not None and gm >= 10 else 0.0
    quality += 3.0 if roe is not None and roe >= 8 else 1.0 if roe is not None and roe >= 4 else 0.0

    risk = 12.0
    risk -= 6.0 if cf_label in ('RECEIVABLE_RISK', 'STRUCTURAL_CASHFLOW_WEAKNESS') else 0.0
    risk -= 3.0 if ar_turn is not None and ar_turn < 1.0 else 0.0
    risk -= 2.0 if ca_turn is not None and ca_turn < 0.5 else 0.0
    risk -= 3.0 if rel_risk >= 60 else 1.0 if rel_risk >= 45 else 0.0
    risk -= 4.0 if eq_label == 'ONE_OFF_DOMINATED' else 0.0
    risk = max(0.0, risk)
    fundamental_score = round(min(60.0, growth + continuation + quality + risk), 1)

    expectation = 20.0
    expectation -= 8.0 if pre_ret >= SeptemberConfig.MAX_PRE_RET else 4.0 if pre_ret >= 12 else 0.0
    expectation -= 8.0 if post_ret >= SeptemberConfig.MAX_POST_RET else 4.0 if post_ret >= 8 else 0.0
    expectation -= 4.0 if overheat >= SeptemberConfig.MAX_OVERHEAT else 0.0
    expectation = max(0.0, expectation)
    absorption = 8.0 if decay_state == 'SECONDARY_CONFIRM' else 6.0 if decay_state == 'NOT_ABSORBED' else 0.0
    trigger = 8.0 if trigger_type in ('T2_PULLBACK', 'T3_RECLAIM') and trigger_score >= 72 else 3.0 if trigger_type in ('T2_PULLBACK', 'T3_RECLAIM') else 0.0
    pullback = 4.0 if pullback_quality_score is not None and pullback_quality_score >= 65 else 2.0 if pullback_quality_score is not None and pullback_quality_score >= 50 else 0.0
    setup_score = round(min(40.0, expectation + absorption + trigger + pullback), 1)

    blocked = eq_label == 'ONE_OFF_DOMINATED' or cf_label in ('RECEIVABLE_RISK', 'STRUCTURAL_CASHFLOW_WEAKNESS')
    fundamental_label = 'FUNDAMENTAL_CORE' if fundamental_score >= SeptemberConfig.FUNDAMENTAL_CORE and not blocked else 'FUNDAMENTAL_WATCH' if fundamental_score >= SeptemberConfig.FUNDAMENTAL_WATCH and not blocked else 'FUNDAMENTAL_REJECT'
    setup_label = 'SETUP_READY' if setup_score >= SeptemberConfig.SETUP_READY else 'SETUP_WATCH' if setup_score >= SeptemberConfig.SETUP_WATCH else 'SETUP_WAIT'
    opportunity_label = 'SEPTEMBER_OPPORTUNITY' if fundamental_label == 'FUNDAMENTAL_CORE' and setup_label == 'SETUP_READY' else 'RESEARCH_ONLY' if fundamental_label == 'FUNDAMENTAL_CORE' else 'WATCH'

    continuation_note = 'Q2动能维持、营收与扣非继续共振、现金流不恶化'
    invalidation = 'Q3增速显著降档或应收/现金流转弱'
    if setup_label == 'SETUP_WAIT':
        invalidation = '价格尚未消化，等待回踩MA20缩量或MA60放量收复'
    elif blocked:
        invalidation = '现金流或盈利质量未修复前不纳入研究池'
    return {
        'sep_score': round(fundamental_score + setup_score, 1),
        'sep_label': opportunity_label,
        'sep_fundamental_score': fundamental_score,
        'sep_fundamental_label': fundamental_label,
        'sep_setup_score': setup_score,
        'sep_setup_label': setup_label,
        'sep_growth': round(growth, 1),
        'sep_continuation': round(continuation, 1),
        'sep_quality': round(quality, 1),
        'sep_expectation': round(expectation, 1),
        'sep_risk': round(risk, 1),
        'sep_absorption': round(absorption, 1),
        'sep_trigger': round(trigger + pullback, 1),
        'sep_accel': round(accel, 1) if accel is not None else None,
        'sep_condition': continuation_note,
        'sep_invalidation': invalidation,
    }


# ============================================================
# 模块1：Cashflow Context Engine V2.2（7 分类 + 区间对齐）
# ============================================================
def cashflow_context_engine_v22(r, strategy, daily, ann_idx, cur_idx):
    """
    现金流语境 7 分类：
      HEALTHY_CASHFLOW (+0~5) / SEASONAL_CYCLE (0) / INVENTORY_BUILD (0~-2)
      WORKING_CAPITAL_EXPANSION (0~-3) / RECEIVABLE_RISK (-10~-20)
      STRUCTURAL_CASHFLOW_WEAKNESS (-5~-15) / DATA_INCOMPLETE (0, 仅降置信度)
    返回 (cfcs, label, cf_adjustment, reason)
    """
    ocf = r.get('ocf_yoy', np.nan)
    ni = r.get('netprofit_yoy', np.nan)
    tr = r.get('tr_yoy', np.nan)
    q_sales = r.get('q_sales_yoy', np.nan)
    ar_turn = r.get('ar_turn', np.nan)
    ca_turn = r.get('ca_turn', np.nan)
    cur_ratio = r.get('current_ratio', np.nan)
    gm = r.get('grossprofit_margin', np.nan)
    q1 = r.get('q1_profit_yoy', np.nan)
    q2 = r.get('q2_profit_yoy', np.nan)
    accel = (q2 - q1) if (_valid(q2) and _valid(q1)) else np.nan
    code = r.get('ts_code', '')
    industry = _get_industry_map().get(code, '')
    is_cyclical = _detect_cyclical(code, industry)
    is_seasonal = any(k in str(industry) for k in V22Config.SEASONAL_KEYWORDS)

    cfg = V22Config.CASHFLOW
    if not _valid(ocf) or not _valid(ni):
        return None, 'DATA_INCOMPLETE', 0.0, '现金流数据不完整'
    ocf_v, ni_v = float(ocf), float(ni)

    # ── CASE 1：健康现金流（+0~5） ──
    if ocf_v > 0 and ni_v > 0:
        cfcs = cfg['cfcs_healthy']
        adj = 0.0
        if ocf_v > ni_v * 0.6:
            cfcs = min(100, cfcs + 10)
            adj = 3.0
        if _valid(tr) and tr > 0 and ocf_v / ni_v > 0.5:
            cfcs = min(100, cfcs + 5)
            adj = min(cfg['adj_healthy_max'], adj + 2.0)
        return round(cfcs, 1), 'HEALTHY_CASHFLOW', round(adj, 1), '利润与现金流双增长'

    # ── 利润正、现金流偏弱/负 ──
    if ni_v > 0 and (ocf_v < 0 or ocf_v < ni_v * 0.3):
        # 季节性行业：Q2 环比改善 + 现金流温和为负 → 不处罚
        if is_seasonal and -50 <= ocf_v < 0 and _valid(q2) and _valid(q1) and q2 > q1:
            return float(cfg['cfcs_seasonal']), 'SEASONAL_CYCLE', 0.0, '消费行业季节性现金流波动'
        # 周期景气补库存（0~-2）
        if is_cyclical and _valid(accel) and accel > 0:
            cfcs = cfg['cfcs_inventory']
            adj = 0.0
            if not (_valid(q_sales) and q_sales > 0):
                cfcs = max(50, cfcs - 10)
                adj = cfg['adj_inventory_min']
            return round(cfcs, 1), 'INVENTORY_BUILD', round(adj, 1), '周期景气补库存'
        # 营运资本扩张（0~-3）
        rev_growing = _valid(tr) and tr > cfg['rev_growth_min']
        ar_ok = _valid(ar_turn) and ar_turn > 1.0
        ca_ok = _valid(ca_turn) and ca_turn > 0.5
        if rev_growing and (ar_ok or ca_ok):
            cfcs = cfg['cfcs_wcap']
            adj = 0.0
            if _valid(tr) and tr > 50:
                cfcs = min(80, cfcs + 8)
            if _valid(gm) and gm > 20:
                cfcs = min(85, cfcs + 5)
            if _valid(ar_turn) and ar_turn < cfg['ar_turn_risk'] * 2:
                adj = cfg['adj_wcap_min']
            return round(cfcs, 1), 'WORKING_CAPITAL_EXPANSION', round(adj, 1), '快速扩张营运资本占用'
        # 应收风险（-10~-20）
        if rev_growing and _valid(ar_turn) and ar_turn < cfg['ar_turn_risk']:
            cfcs = cfg['cfcs_receivable']
            adj = cfg['adj_receivable_min']
            if _valid(cur_ratio) and cur_ratio < 1.0:
                cfcs = max(10, cfcs - 10)
                adj = -15.0
            if _valid(ca_turn) and ca_turn < 0.5:
                adj = cfg['adj_receivable_max']
            return round(cfcs, 1), 'RECEIVABLE_RISK', round(adj, 1), '应收周转恶化'
        # 周转全面恶化（-10）
        if _valid(ar_turn) and ar_turn < 1.0 and _valid(ca_turn) and ca_turn < 0.5:
            return float(cfg['cfcs_structural']), 'STRUCTURAL_CASHFLOW_WEAKNESS', -10.0, '应收+存货周转全面恶化'
        # 一般性现金流偏弱（-5）
        return 45.0, 'STRUCTURAL_CASHFLOW_WEAKNESS', -5.0, '现金流质量偏弱'

    # ── 利润负、现金流也负（-15） ──
    if ni_v < 0 and ocf_v < 0:
        return 10.0, 'STRUCTURAL_CASHFLOW_WEAKNESS', cfg['adj_structural_max'], '利润与现金流双恶化'

    return 50.0, 'DATA_INCOMPLETE', 0.0, '无法充分分析'


# ============================================================
# 模块2：Price Absorption（公告后价格吸收六量 + 公告前大涨检测）
# ============================================================
def price_absorption(daily, ann_idx, cur_idx, event_age, bench=None):
    """
    计算公告日至当前的六项价格吸收指标：
      post_ret / max_ret / drawdown / rel_str(RS) / vol_struct / event_age
    外加：
      pre_ret / pre_priced（公告前20日涨幅 → 预期差是否在公告前已耗尽）
      pullback_vol_ratio（回踩期量比，Refresh 条件用）
      rel_str_improve（近5日 RS 是否改善，Refresh 条件用）
    """
    seg = daily.iloc[ann_idx:cur_idx + 1]
    c0 = float(seg.iloc[0]['close'])
    c_now = float(seg.iloc[-1]['close'])
    post_ret = c_now / c0 - 1.0 if c0 > 0 else 0.0
    hi_max = float(seg['high'].max())
    max_ret = hi_max / c0 - 1.0 if c0 > 0 else 0.0
    drawdown = c_now / hi_max - 1.0 if hi_max > 0 else 0.0
    pre20 = daily.iloc[max(0, ann_idx - 20):ann_idx]
    avg_vol = float(pre20['vol'].mean()) if len(pre20) > 0 else float(daily['vol'].mean())
    vol_struct = float(seg['vol'].mean()) / avg_vol if avg_vol > 0 else 1.0

    # 公告前 20 日涨幅（预期差耗尽检测）
    pre_ret = 0.0
    if ann_idx >= 21:
        pre_c0 = float(daily.iloc[ann_idx - 21]['close'])
        pre_ret = float(daily.iloc[ann_idx - 1]['close']) / pre_c0 - 1.0 if pre_c0 > 0 else 0.0
    gap_ann = float(seg.iloc[0]['close']) / float(daily.iloc[max(0, ann_idx - 1)]['close']) - 1.0
    pre_priced = pre_ret > V22Config.DECAY['pre_priced_ret'] or (
        pre_ret > V22Config.DECAY['pre_priced_ret'] - 0.08 and gap_ann > V22Config.DECAY['pre_priced_gap'])

    # Relative Strength（相对上证指数同区间）
    rel_str = None
    if bench is not None and not bench.empty:
        b0 = bench[bench['date'] == str(seg.iloc[0]['trade_date'])]
        b1 = bench[bench['date'] == str(seg.iloc[-1]['trade_date'])]
        if not b0.empty and not b1.empty:
            rb = float(b1['close'].iloc[0]) / float(b0['close'].iloc[0]) - 1.0
            rel_str = post_ret - rb

    # RS 改善：近 5 日相对强度 > 公告后整体 RS
    rel_str_improve = False
    if bench is not None and len(seg) >= 5 and cur_idx >= 5:
        seg5 = seg.iloc[-5:]
        r5 = float(seg5.iloc[-1]['close']) / float(seg5.iloc[0]['close']) - 1.0
        b0 = bench[bench['date'] == str(seg5.iloc[0]['trade_date'])]
        b1 = bench[bench['date'] == str(seg5.iloc[-1]['trade_date'])]
        if not b0.empty and not b1.empty:
            rb5 = float(b1['close'].iloc[0]) / float(b0['close'].iloc[0]) - 1.0
            rel_str_improve = (r5 - rb5) > (rel_str if rel_str is not None else 0.0)

    # 回踩期量比（近 5 日 vs 公告前 20 日均量）
    pullback_vol_ratio = None
    if len(seg) >= 6:
        pv = float(seg.iloc[-5:]['vol'].mean())
        pullback_vol_ratio = pv / avg_vol if avg_vol > 0 else 1.0

    return {
        'post_ret': post_ret, 'max_ret': max_ret, 'drawdown': drawdown,
        'rel_str': rel_str, 'vol_struct': vol_struct, 'event_age': event_age,
        'pre_ret': pre_ret, 'pre_priced': pre_priced,
        'pullback_vol_ratio': pullback_vol_ratio, 'rel_str_improve': rel_str_improve,
    }


# ============================================================
# 模块3：Alpha Decay V2.2（三态 + Refresh 4 条件）
# ============================================================
def calc_alpha_decay_v22(daily, ann_idx, cur_idx, event_age, ab):
    """
    Decay 三态（对齐规格五）：
      NOT_ABSORBED      decay 0~0.03
      SECONDARY_CONFIRM decay 0.03~0.08（+ Refresh 0~8）
      PRICED_IN         decay >= 0.15（公告前已大涨 / 公告后连续上涨放量）
    Refresh 仅允许 SECONDARY_CONFIRM 且满足 4 条件：
      1. 回踩未破趋势  2. 缩量  3. 重新放量  4. RS 改善
    返回 (decay, refresh, mult, state)
    """
    cfg = V22Config.DECAY
    decay = 0.0
    for age_thresh, d in cfg['tiers']:
        if event_age <= age_thresh:
            break
        decay = d
    else:
        decay = cfg['tiers'][-1][1]
    if event_age > 20:
        decay = min(0.30, decay + (event_age - 20) * 0.01)

    seg = daily.iloc[ann_idx:cur_idx + 1]
    pre20 = daily.iloc[max(0, ann_idx - 20):ann_idx]
    avg_vol = float(pre20['vol'].mean()) if len(pre20) > 0 else 0.0
    seg_vol = float(seg['vol'].mean()) if len(seg) > 0 else 0.0
    vol_ratio = seg_vol / avg_vol if avg_vol > 0 else 1.0
    seg_ret = ab['post_ret']

    # ── 公告前已大涨 → PRICED_IN（预期差耗尽），Refresh 禁用 ──
    if ab['pre_priced']:
        decay = max(decay, cfg['priced_in_min'])
        return round(decay, 3), 0.0, round(max(0.60, 1.0 - decay), 3), 'PRICED_IN'

    state = 'NOT_ABSORBED'
    refresh = 0.0

    if abs(seg_ret) <= cfg['abs_ret_range'] and vol_ratio <= cfg['abs_vol_ratio']:
        # 缩量横盘 → 信息未充分吸收
        decay = min(cfg['not_absorbed_max'], decay)
        state = 'NOT_ABSORBED'
    elif seg_ret > cfg['priced_in_ret'] and vol_ratio > cfg['priced_in_vol']:
        # 连续上涨放量 → 充分定价
        decay = max(decay, cfg['priced_in_min'])
        return round(decay, 3), 0.0, round(max(0.60, 1.0 - decay), 3), 'PRICED_IN'
    elif ab['drawdown'] <= -0.03 and vol_ratio >= cfg['refresh_vol_ratio']:
        # 回踩后二次放量 → SECONDARY_CONFIRM 候选（检查 4 条件）
        ok = 0
        ma20 = float(daily.iloc[cur_idx].get('ma20', np.nan))
        c = float(daily.iloc[cur_idx]['close'])
        if _valid(ma20) and c > ma20 * 0.97:
            ok += 1                                     # 1. 回踩未破趋势
        if ab['pullback_vol_ratio'] is not None and ab['pullback_vol_ratio'] <= cfg['refresh_vol_ok']:
            ok += 1                                     # 2. 缩量
        if vol_ratio >= cfg['refresh_vol_ratio']:
            ok += 1                                     # 3. 重新放量
        if ab['rel_str_improve']:
            ok += 1                                     # 4. RS 改善
        decay = min(cfg['secondary_max'], max(cfg['secondary_min'], decay))
        state = 'SECONDARY_CONFIRM'
        if ok == 4:
            refresh = cfg['refresh_max']
        elif ok == 3:
            refresh = 6.0
        elif ok >= 2:
            refresh = 4.0
    else:
        decay = min(cfg['secondary_max'], max(cfg['secondary_min'], decay))
        state = 'SECONDARY_CONFIRM' if vol_ratio >= 1.20 else 'NOT_ABSORBED'

    mult = max(0.60, min(1.00, 1.0 - decay))
    return round(decay, 3), round(refresh, 1), round(mult, 3), state


# ============================================================
# 模块4：Trigger 三分类（T1_BREAKOUT / T2_PULLBACK / T3_RECLAIM）
# ============================================================
def trigger_score_v22(daily, cur_idx):
    """
    TriggerScore 0~100，只保留 3 类触发。
    返回 (ts, ttype, desc)：
      T1_BREAKOUT 放量突破 MA60/平台（要求 趋势向上 + 量比>=1.2）
      T2_PULLBACK 趋势健康缩量回踩 MA20 后阳线确认
      T3_RECLAIM  短暂跌破后重新站回 MA20/MA60（量能改善）
      否则 NO_TRIGGER (0)
    """
    if cur_idx < 25:
        return 0.0, 'NO_TRIGGER', '历史不足'
    last = daily.iloc[cur_idx]
    c = float(last['close'])
    o = float(last['open'])
    prev = daily.iloc[cur_idx - 1]
    pc = float(prev['close'])
    ret = c / pc - 1.0 if pc > 0 else 0.0
    ma5 = float(last.get('ma5', np.nan))
    ma20 = float(last.get('ma20', np.nan))
    ma60 = float(last.get('ma60', np.nan))
    prev_ma20 = float(prev.get('ma20', np.nan))
    prev_ma60 = float(prev.get('ma60', np.nan))
    ma5v = float(daily['vol'].iloc[max(0, cur_idx - 5):cur_idx].mean())
    vr = float(last['vol']) / ma5v if ma5v > 0 else 0.0
    hi3 = float(daily['high'].iloc[max(0, cur_idx - 3):cur_idx].max())
    rng = float(last['high']) - float(last['low'])
    cp = (c - float(last['low'])) / rng if rng > 0 else 0.5
    cfg = V22Config.TRIGGER

    # ── T1_BREAKOUT ──
    t1_ma60 = _valid(ma60) and ma60 > 0 and pc <= ma60 and c > ma60 and vr >= cfg['breakout_vol']
    t1_plat = c > hi3 and vr >= cfg['breakout_vol'] and ret > 0
    if t1_ma60 or t1_plat:
        ts = 80.0
        if t1_ma60:
            ts += 5
        if cp >= cfg['breakout_close']:
            ts += 5
        if 1.5 <= vr <= 2.5:
            ts += 5
        if _valid(ma20) and c > ma20:
            ts += 5
        return round(min(100.0, ts), 1), 'T1_BREAKOUT', '放量突破'

    # ── T3_RECLAIM ──
    broke = (_valid(prev_ma20) and pc < prev_ma20) or (_valid(prev_ma60) and pc < prev_ma60)
    reclaim20 = _valid(ma20) and pc < ma20 and c >= ma20
    reclaim60 = _valid(ma60) and pc < ma60 and c >= ma60
    if broke and (reclaim20 or reclaim60) and vr >= cfg['reclaim_vol'] and ret > 0:
        ts = 72.0
        if reclaim20:
            ts += 6
        if vr >= 1.3:
            ts += 6
        if ret > 0.02:
            ts += 4
        return round(min(100.0, ts), 1), 'T3_RECLAIM', '收复均线'

    # ── T2_PULLBACK ──
    trend_ok = (_valid(ma60) and c > ma60) or (_valid(ma20) and c > ma20)
    if trend_ok and _valid(ma20) and ma20 > 0:
        d20 = c / ma20 - 1.0
        if cfg['pullback_d20_lo'] <= d20 <= cfg['pullback_d20_hi'] and c > o and vr <= cfg['pullback_vol_max']:
            ts = 70.0
            if d20 >= 0:
                ts += 5
            if vr <= 1.0:
                ts += 5
            if _valid(ma5) and c > ma5:
                ts += 4
            return round(min(100.0, ts), 1), 'T2_PULLBACK', '回踩MA20阳线'

    return 0.0, 'NO_TRIGGER', '无触发'


# ============================================================
# 模块5：EES（Entry Score V2.2）
# ============================================================
def calc_ees_v22(trend, ts, volume, pqs, overheat):
    """
    EES = Trend + Trigger + Volume + PullbackQuality − Overextension
    0~100。无触发(ts=0)时 EES 天然压低 → 只能 WATCH/WAIT。
    """
    w = V22Config.EES
    trend_v = float(trend) if _valid(trend) else 40.0
    vol_v = float(volume) if _valid(volume) else 50.0
    pqs_v = float(pqs) if _valid(pqs) else 50.0
    oh_v = float(overheat) if _valid(overheat) else 0.0
    ees = (w['trend'] * trend_v + w['trigger'] * float(ts)
           + w['volume'] * vol_v + w['pqs'] * pqs_v
           - w['overheat'] * (oh_v / 40.0 * 100.0))
    return round(min(100.0, max(0.0, ees)), 1)


def next5_score_v22(row):
    cfg = V22Config.NEXT5
    alpha = float(row.get('alpha', 0.0) or 0.0)
    ees = float(row.get('ees', 0.0) or 0.0)
    ts = float(row.get('ts', 0.0) or 0.0)
    conf = float(row.get('conf', 0.0) or 0.0)
    risk = float(row.get('rel_risk', 100.0) or 100.0)
    score = (cfg['alpha'] * alpha + cfg['ees'] * ees + cfg['ts'] * ts
             + cfg['conf'] * conf + cfg['risk'] * (100.0 - risk))
    return round(max(0.0, min(100.0, score)), 1)


def _load_egpt_fusion(scan_date):
    path = os.path.join(REPORT_DIR, f'zhongbao_egpt_timing_{scan_date}.csv')
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        egpt = pd.read_csv(path, encoding='utf-8-sig')
    except Exception:
        return pd.DataFrame()
    if '代码' not in egpt.columns:
        return pd.DataFrame()
    egpt = egpt.copy()
    egpt['ts_code'] = egpt['代码'].astype(str).str.strip()
    egpt['ts_code'] = egpt['ts_code'].apply(
        lambda x: x if '.' in x else f'{x[:6]}.SH' if x.startswith(('5', '6', '9')) else f'{x[:6]}.SZ')
    return egpt.drop_duplicates('ts_code', keep='first')


def _fusion_score_v22(row):
    cfg = V22Config.FUSION
    er20 = float(row.get('next5_score', 0.0) or 0.0)
    egpt_quality = float(row.get('egpt_quality_score', 50.0) or 50.0)
    egpt_pullback = float(row.get('egpt_pullback_score', 50.0) or 50.0)
    execution = float(row.get('execution_score', 75.0) or 75.0)
    score = (cfg['er20'] * er20 + cfg['egpt_quality'] * egpt_quality
             + cfg['egpt_pullback'] * egpt_pullback + cfg['execution'] * execution)
    return round(max(0.0, min(100.0, score)), 1)


# ============================================================
# 模块6：Grade V2.2（规格九门槛）
# ============================================================
def grade_v22(alpha, ees, ts, ttype, risk, overheat, conf, fq, rqs,
              strategy, cf_label, eq_label, missing):
    """
    状态机：REJECT → WATCH → WAIT_CONFIRM → WAIT_PULLBACK → PROBE_BUY → TEST_BUY → CORE_BUY
    BUY 必须同时满足 Alpha + 技术触发 + 风险门控；高分无触发必须 WAIT。
    """
    g = V22Config.GATE
    # ── D_FALSE_SIGNAL：只进 WATCH（不入任何 BUY） ──
    if strategy == 'D_FALSE_SIGNAL':
        if cf_label in ('STRUCTURAL_CASHFLOW_WEAKNESS', 'RECEIVABLE_RISK'):
            return 'WATCH', f'假信号+现金流{cf_label}'
        return 'WATCH', f'假信号(现金流{cf_label})'
    # ── 事件股隔离 ──
    if strategy == 'C_EVENT_SPEC':
        return 'WATCH', '事件驱动仅观察'
    # ── 一次性收益主导 ──
    if eq_label == 'ONE_OFF_DOMINATED':
        return 'REJECT', '一次性收益主导'
    # ── Fundamental Floor ──
    if fq is not None and fq < g['fq_floor_reject']:
        if strategy == 'B_REVERSAL' and rqs is not None and rqs >= g['rqs_high_grade']:
            pass
        else:
            return 'REJECT', f'基本面{fq:.0f}<{g["fq_floor_reject"]}'
    # ── 置信度 ──
    if conf < g['conf_watch']:
        return 'WATCH', f'数据置信{conf:.0f}<{g["conf_watch"]}'
    # ── 无触发：高分必须 WAIT，不允许自动买入 ──
    if ts == 0 or ttype == 'NO_TRIGGER':
        if alpha >= g['watch_alpha']:
            return 'WAIT_CONFIRM', '高分但今日无触发'
        return 'WATCH', '等待触发'
    # ── 高透支 → WAIT_PULLBACK（禁止 BUY） ──
    if overheat > g['overheat_pullback']:
        if alpha >= g['watch_alpha']:
            return 'WAIT_PULLBACK', f'透支{overheat:.0f}，等回调'
        return 'WATCH', '透支过高'
    # ── T1_BREAKOUT 拦截（V2.2 combo 规则）：放量突破不追，最高 WAIT_PULLBACK ──
    if ttype == 'T1_BREAKOUT':
        if alpha >= g['watch_alpha']:
            return 'WAIT_PULLBACK', 'T1突破不追，等回踩'
        return 'WATCH', 'T1突破不追'
    # ── CORE_BUY ──
    if (alpha >= g['core_alpha'] and ees >= g['core_ees'] and ts >= g['core_ts']
            and risk <= g['core_risk'] and conf >= g['core_conf']
            and fq is not None and fq >= g['core_fq']
            and overheat <= g['core_overheat']):
        return 'CORE_BUY', ''
    # ── TEST_BUY ──
    if alpha >= g['test_alpha'] and ees >= g['test_ees'] and ts >= g['test_ts'] \
            and risk <= g['test_risk']:
        return 'TEST_BUY', ''
    # ── PROBE_BUY（小仓试错） ──
    if alpha >= g['probe_alpha'] and ees >= g['probe_ees']:
        return 'PROBE_BUY', '小仓试错'
    # ── EES 分级兜底 ──
    if ees < 60:
        return 'WATCH', f'EES{ees:.0f}<60'
    if ts < 70:
        if alpha >= g['watch_alpha']:
            return 'WAIT_PULLBACK', '触发偏弱，等放量确认'
        return 'WATCH', '触发偏弱'
    if alpha >= g['watch_alpha']:
        return 'WAIT_CONFIRM', '观察等触发'
    return 'WATCH', f'Alpha{alpha:.0f}<{g["watch_alpha"]}'


# ============================================================
# 模块7：组合总仓位控制 V2.2
# ============================================================
def _apply_portfolio_cap_v22(df):
    """
    规格十：
      CORE_BUY 单股 15% / TEST_BUY 12% / PROBE_BUY 5%
      重点持仓 5~8 只；同主题总仓位 ≤30%
      总仓位 >100% 或 只数 >8 → 优先挤 PROBE，再挤 TEST（不降低标准硬塞）
    """
    cfg = V22Config.PORTFOLIO
    pos = {'CORE_BUY': cfg['core_pos'], 'TEST_BUY': cfg['test_pos'], 'PROBE_BUY': cfg['probe_pos']}
    buy = df[df['grade'].isin(['CORE_BUY', 'TEST_BUY', 'PROBE_BUY'])].copy()
    if buy.empty:
        return df
    buy['_pos'] = buy['grade'].map(pos)
    # 只数限制 5~8；保留顺序：CORE > TEST > PROBE（规格：优先挤 PROBE 再挤 TEST）
    buy['_rank'] = buy['grade'].map({'PROBE_BUY': 0, 'TEST_BUY': 1, 'CORE_BUY': 2})
    buy = buy.sort_values(['_rank', 'alpha'], ascending=[False, False])
    max_hold = max(5, min(cfg['max_hold'], len(buy)))
    # 移除最低等级的最弱股，直到 ≤ max_hold 且总仓位 ≤100%
    keep = []
    total = 0.0
    for _, r in buy.iterrows():
        if len(keep) >= max_hold or total + r['_pos'] > 1.0:
            continue
        keep.append(r.name)
        total += r['_pos']
    # 同主题限仓（theme 列已预生成）
    if 'theme' in df.columns:
        theme_pos = {}
        recheck = list(keep)
        for idx in recheck:
            th = str(df.loc[idx, 'theme'])
            if th and th != 'nan':
                cur = theme_pos.get(th, 0.0)
                if cur + pos[df.loc[idx, 'grade']] > cfg['theme_cap']:
                    keep.remove(idx)
                    total -= pos[df.loc[idx, 'grade']]
                    continue
                theme_pos[th] = cur + pos[df.loc[idx, 'grade']]
    # 只挤 BUY 集合中未被保留的股票，不动 WATCH/REJECT/WAIT_*
    drop = set(buy.index) - set(keep)
    df.loc[list(drop), 'grade'] = 'WAIT_CONFIRM'
    df.loc[list(drop), 'grade_reason'] = '组合仓位已满'
    # 移除辅助列
    if '_pos' in df.columns:
        df = df.drop(columns=['_pos'])
    return df


# ============================================================
# 主流程 scan_v22
# ============================================================
def scan_v22(scan_date='20260820'):
    t0 = time.time()
    period = '20260630'
    print(f'[scan_v22] 扫描日 {scan_date}  报告期 {period}')
    regime, market_mult = market_multiplier(scan_date)
    print(f'  市场环境: {regime}  x{market_mult}')
    bench = load_bench(scan_date)
    stock2theme = theme_map_stock2theme()
    _ = _get_industry_map()

    pool = load_pool_v2(period, scan_date)
    if pool.empty:
        print('  池为空，退出')
        return None
    print(f'  池规模: {len(pool)}')
    pool = pool[~pool['name'].astype(str).str.startswith(('*ST', 'ST'))]
    print(f'  去ST后: {len(pool)}')
    industry_atr = _precompute_industry_atr(pool, scan_date)
    dates = get_trade_dates('20250101', str(scan_date))
    cur_i = len(dates) - 1
    ann_idx_all = {d: i for i, d in enumerate(dates)}

    cands = []
    for _, r in pool.iterrows():
        if str(r['ts_code']).endswith('.BJ'):
            continue
        ann = str(r.get('ann_date', ''))
        if len(ann) != 8:
            continue
        if str(r.get('name', '')).startswith(('*ST', 'ST')):
            continue
        tds = [d for d in dates if d >= ann]
        if not tds or tds[0] not in ann_idx_all:
            continue
        gap = cur_i - ann_idx_all[tds[0]]
        if not (ER20Config.ANN_WINDOW_MIN <= gap <= ER20Config.ANN_WINDOW_MAX):
            continue
        q2 = r.get('q2_profit_yoy', np.nan)
        ni = r.get('netprofit_yoy', np.nan)
        if not (_valid(q2) or _valid(ni)):
            continue
        cands.append((r, ann, gap))
    print(f'  公告窗口内候选: {len(cands)}')

    rows = []
    for i, (r, ann, gap) in enumerate(cands):
        code = r['ts_code']
        daily = load_daily_for(code, scan_date)
        if daily is None:
            continue
        nxt = daily[daily['trade_date'] > ann]
        if nxt.empty:
            continue
        ann_idx = nxt.index[0]
        cur_idx = len(daily) - 1
        if ann_idx >= cur_idx:
            continue
        event_age = calc_event_age_tradingdays(ann, scan_date)
        missing = []
        strategy, cls_reason = classify_event(r, daily, ann_idx, cur_idx)

        # ── V2 公共因子（复用） ──
        fq = fundamental_quality(r, missing)
        gap_s, gap_note = expect_gap_score(r, daily, ann_idx)
        ars, ars_note = calc_ars(daily, ann_idx, cur_idx, bench)
        risk_v2 = risk_score(r, daily, cur_idx, ann_idx)
        overheat = overheat_penalty(r, daily, ann_idx, cur_idx)
        th = theme_score(code, stock2theme)

        # ── V2.2 现金流语境（7 分类） ──
        cfcs, cf_label, cf_adj, cf_reason = cashflow_context_engine_v22(
            r, strategy, daily, ann_idx, cur_idx)
        eq_label, eq_penalty, eq_detail = earnings_quality_context(r)
        ind = _get_industry_map().get(code, '')
        benchmark_vol = industry_atr.get(ind, None)
        rel_risk = relative_risk_score(r, daily, cur_idx, ann_idx, benchmark_vol)
        conf = data_confidence_v21(r, daily, ann_idx, missing, cf_label, eq_label)

        # ── 技术因子（统一三件套，EES 用） ──
        pqs = pullback_quality(daily, cur_idx, ann_idx, missing)
        trend = trend_structure(daily, cur_idx, missing)
        volume = volume_structure(daily, cur_idx, missing)
        rqs = calc_rqs(r, missing) if strategy == 'B_REVERSAL' else None
        tqs = calc_tqs(daily, cur_idx, ann_idx, missing) if strategy == 'B_REVERSAL' else None

        # ── 价格吸收 + Decay + Trigger + EES ──
        ab = price_absorption(daily, ann_idx, cur_idx, event_age, bench)
        decay, refresh, mult, decay_state = calc_alpha_decay_v22(
            daily, ann_idx, cur_idx, event_age, ab)
        ts, ttype, tdesc = trigger_score_v22(daily, cur_idx)
        ees = calc_ees_v22(trend, ts, volume, pqs, overheat)
        sep = calc_september_opportunity(
            r, cfcs, cf_label, eq_label, ab['pre_ret'] * 100,
            ab['post_ret'] * 100, overheat, rel_risk, decay_state,
            ttype, ts, pqs)

        # ── 策略专属加权（raw） ──
        wmap = ER20Config.W_B if strategy == 'B_REVERSAL' else ER20Config.W_A
        parts = {}
        if strategy == 'B_REVERSAL':
            parts = {'rqs': rqs, 'fq': fq, 'gap': gap_s, 'ars': ars, 'tqs': tqs, 'risk': rel_risk}
        else:
            parts = {'fq': fq, 'gap': gap_s, 'ars': ars, 'pqs': pqs, 'trend': trend, 'risk': rel_risk}
        wsum, ssum = 0.0, 0.0
        for k, wgt in wmap.items():
            v = parts.get(k)
            if _valid(v):
                wsum += wgt
                ssum += wgt * float(v)
            else:
                missing.append(k)
        if wsum == 0:
            continue
        raw = ssum / wsum

        # 主题（组合限仓用）
        theme_list = stock2theme.get(code, [])
        theme = theme_list[0] if theme_list else ''

        rows.append({
            'ts_code': code, 'name': r.get('name', ''), 'ann_date': ann, 'gap': gap,
            'scan_close': float(daily.iloc[cur_idx]['close']),
            'event_age': event_age, 'strategy': strategy, 'cls_reason': cls_reason,
            'raw': round(raw, 1), 'fq': fq, 'rqs': rqs, 'gap_s': gap_s, 'ars': ars,
            'pqs': pqs, 'trend': trend, 'tqs': tqs, 'volume': volume,
            'risk_v2': risk_v2, 'rel_risk': rel_risk, 'overheat': overheat,
            'conf': conf, 'theme_adj': th, 'theme': theme,
            'cfcs': cfcs, 'cf_label': cf_label, 'cf_adj': cf_adj, 'cf_reason': cf_reason,
            'eq_label': eq_label, 'eq_penalty': eq_penalty, 'eq_detail': eq_detail,
            'post_ret': round(ab['post_ret'] * 100, 2), 'max_ret': round(ab['max_ret'] * 100, 2),
            'drawdown': round(ab['drawdown'] * 100, 2),
            'rel_str': round(ab['rel_str'] * 100, 2) if ab['rel_str'] is not None else None,
            'vol_struct': round(ab['vol_struct'], 2), 'pre_ret': round(ab['pre_ret'] * 100, 2),
            'pre_priced': ab['pre_priced'],
            'decay_factor': decay, 'alpha_refresh': refresh, 'decay_state': decay_state,
            'ts': ts, 'ttype': ttype, 'tdesc': tdesc, 'ees': ees,
            **sep,
            'missing': '|'.join(sorted(set(missing))),
            'dt_netprofit_yoy': r.get('dt_netprofit_yoy'),
            'tr_yoy': r.get('tr_yoy'),
            'netprofit_yoy': r.get('netprofit_yoy'),
        })
        if (i + 1) % 200 == 0:
            print(f'  已评分 {i + 1}/{len(cands)}')

    if not rows:
        print('  无可评分候选')
        return None
    df = pd.DataFrame(rows)

    # ── 策略内 percentile 归一化 ──
    df['norm'] = np.nan
    for strat, grp in df.groupby('strategy'):
        if len(grp) >= 3:
            df.loc[df['strategy'] == strat, 'norm'] = (
                grp['raw'].rank(pct=True).reindex(df[df['strategy'] == strat].index) * 100.0)
        else:
            df.loc[df['strategy'] == strat, 'norm'] = grp['raw'].clip(0, 100)

    # ── ER20_BASE = norm × Conf × RelRisk × Market + CF_Adj + Theme ──
    conf_mult = df['conf'].astype(float) / 100.0
    risk_mult = 1.0 - (df['rel_risk'].astype(float) / 100.0) * ER20Config.RISK_PEN
    df['er20_base'] = (df['norm'] * conf_mult * risk_mult * market_mult
                       + df['cf_adj'].fillna(0.0) + df['theme_adj']).round(1)

    # ── 最终 ALPHA = Base × Decay + Refresh − EQ_Penalty ──
    decay_mult = df['decay_factor'].map(lambda d: max(0.60, min(1.00, 1.0 - d)))
    df['alpha'] = (df['er20_base'] * decay_mult + df['alpha_refresh'].fillna(0.0)
                   - df['eq_penalty'].fillna(0.0)).round(1)
    df['alpha'] = df['alpha'].clip(0, 100)
    df['next5_score'] = df.apply(next5_score_v22, axis=1)

    egpt = _load_egpt_fusion(scan_date)
    if egpt.empty:
        df['egpt_covered'] = 0
        df['egpt_quality_score'] = 50.0
        df['egpt_pullback_score'] = 50.0
        df['egpt_buy_point'] = ''
        df['egpt_theme_status'] = ''
        df['egpt_decision'] = ''
        df['execution_score'] = 75.0
        df['fusion_gate'] = 1
    else:
        egpt_cols = [c for c in ['ts_code', '翻倍潜力分', '回踩买点分', '买点确认',
                                 '主题状态', '次日操作', '主题热度%'] if c in egpt.columns]
        df = df.merge(egpt[egpt_cols], on='ts_code', how='left')
        covered = df['翻倍潜力分'].notna() | df['回踩买点分'].notna()
        df['egpt_covered'] = covered.astype(int)
        df['egpt_quality_score'] = pd.to_numeric(df.get('翻倍潜力分'), errors='coerce').fillna(50.0).clip(0, 100)
        df['egpt_pullback_score'] = pd.to_numeric(df.get('回踩买点分'), errors='coerce').fillna(50.0).clip(0, 100)
        df['egpt_buy_point'] = df.get('买点确认', '').fillna('').astype(str)
        df['egpt_theme_status'] = df.get('主题状态', '').fillna('').astype(str)
        df['egpt_decision'] = df.get('次日操作', '').fillna('').astype(str)
        heat = pd.to_numeric(df.get('主题热度%'), errors='coerce').fillna(0.0)
        df['execution_score'] = 75.0
        df.loc[df['egpt_buy_point'].str.contains('买点2', na=False), 'execution_score'] += 12.0
        df.loc[df['egpt_buy_point'].str.contains('买点1', na=False), 'execution_score'] += 5.0
        df.loc[df['egpt_buy_point'].eq('未突破'), 'execution_score'] -= 8.0
        df.loc[heat.between(5.0, 15.0), 'execution_score'] += 5.0
        df.loc[heat.ge(15.0), 'execution_score'] -= 10.0
        df['execution_score'] = df['execution_score'].clip(0, 100)
        df['fusion_gate'] = 1
        df.loc[df['egpt_decision'].str.startswith('❌'), 'fusion_gate'] = 0
        df.loc[df['egpt_buy_point'].str.startswith('买点1') & heat.ge(15.0), 'fusion_gate'] = 0

    df['fusion_score'] = df.apply(_fusion_score_v22, axis=1)
    df.loc[df['egpt_covered'] == 0, 'fusion_score'] = df.loc[df['egpt_covered'] == 0, 'next5_score']
    df['fusion_score'] = df['fusion_score'].clip(0, 100).round(1)
    df['next5_rank'] = df['next5_score'].rank(method='first', ascending=False).astype(int)

    # ── D_FALSE_SIGNAL 退出主排名（P0-2 延续） ──
    df['rank_eligible'] = df['strategy'] != 'D_FALSE_SIGNAL'

    # ── 分级 ──
    grades, reasons = [], []
    for _, row in df.iterrows():
        st, rs = grade_v22(
            row['alpha'], row['ees'], row['ts'], row['ttype'],
            row['rel_risk'], row['overheat'], row['conf'], row['fq'], row['rqs'],
            row['strategy'], row['cf_label'], row['eq_label'], [])
        grades.append(st)
        reasons.append(rs)
    df['grade'] = grades
    df['grade_reason'] = reasons

    # ── 双模式：质量线评级快照（组合限仓/Top2名额前，过滤模式依据） ──
    # 2026H1 OOS 验证: PASS(非REJECT非WAIT_CONFIRM) 在 B2/B3 批次 close5 连续正 spread
    # 生产口径比回测略严: 同步剔除 D_FALSE_SIGNAL 假信号事件(rank_eligible), 保证
    # SQLite filter_pass=1 与报告【3B】区块展示集合一致
    df['grade_filter'] = grades
    _elig = df['strategy'] != 'D_FALSE_SIGNAL'
    df['filter_pass'] = [
        (g not in ('REJECT', 'WAIT_CONFIRM')) and bool(e)
        for g, e in zip(grades, _elig)]

    # ── 组合仓位控制 V2.2 ──
    df = _apply_portfolio_cap_v22(df)

    trade_dates = [str(d) for d in dates]
    next_date = next((d for d in trade_dates if d > str(scan_date)), None)
    df['entry_date'] = next_date
    df['entry_price_model'] = '次日开盘价'
    df['next_day_signal'] = 0
    buy_mask = df['grade'].isin(['CORE_BUY', 'TEST_BUY', 'PROBE_BUY'])
    eligible_signal = buy_mask
    ranked = df.loc[eligible_signal].sort_values(
        ['next5_score', 'ees', 'alpha'], ascending=[False, False, False])
    signal_idx = ranked.head(V22Config.NEXT5['limit']).index
    df.loc[signal_idx, 'next_day_signal'] = 1
    limited_idx = df.index[buy_mask & ~df.index.isin(signal_idx)]
    df.loc[limited_idx, 'grade'] = 'WAIT_CONFIRM'
    df.loc[limited_idx, 'grade_reason'] = '次日信号已限额'

    # ── 排序（D 类垫底，其余按 Alpha） ──
    df = df.sort_values(['rank_eligible', 'alpha'], ascending=[False, False]).reset_index(drop=True)
    save_sqlite_v22(df, scan_date)
    print(f'[scan_v22] 完成 {len(df)} 只，耗时 {time.time() - t0:.0f}s')
    return df


# ============================================================
# SQLite V2.2
# ============================================================
def save_sqlite_v22(df, scan_date):
    cols = ['ts_code', 'name', 'ann_date', 'gap', 'event_age', 'strategy', 'cls_reason',
            'raw', 'norm', 'er20_base', 'alpha', 'next5_score', 'fusion_score', 'next5_rank',
            'egpt_covered', 'egpt_quality_score', 'egpt_pullback_score', 'egpt_buy_point',
            'egpt_theme_status', 'egpt_decision', 'execution_score', 'fusion_gate',
            'fq', 'rqs', 'gap_s', 'ars', 'pqs', 'trend', 'volume', 'tqs',
            'risk_v2', 'rel_risk', 'overheat', 'conf', 'theme_adj', 'theme',
            'cfcs', 'cf_label', 'cf_adj', 'cf_reason',
            'eq_label', 'eq_penalty', 'eq_detail',
            'post_ret', 'max_ret', 'drawdown', 'rel_str', 'vol_struct', 'pre_ret',
            'pre_priced', 'decay_factor', 'alpha_refresh', 'decay_state',
            'ts', 'ttype', 'tdesc', 'ees',
            'dt_netprofit_yoy', 'tr_yoy', 'netprofit_yoy',
            'sep_score', 'sep_label', 'sep_fundamental_score', 'sep_fundamental_label',
            'sep_setup_score', 'sep_setup_label', 'sep_growth', 'sep_continuation',
            'sep_quality', 'sep_expectation', 'sep_risk', 'sep_absorption', 'sep_trigger',
            'sep_accel', 'sep_condition', 'sep_invalidation',
            'grade', 'grade_filter', 'filter_pass', 'grade_reason', 'rank_eligible',
            'entry_date', 'entry_price_model', 'next_day_signal']
    save = df[[c for c in cols if c in df.columns]].copy()
    save.insert(0, 'scan_date', str(scan_date))
    save['missing'] = df.get('missing', '')
    # 表列均为 TEXT affinity; bool 直接入库会变成文本, 显式转 0/1 保证可比较
    if 'filter_pass' in save.columns:
        save['filter_pass'] = save['filter_pass'].astype(int)
    conn = sqlite3.connect(DB_PATH_V22)
    try:
        try:
            conn.execute('DELETE FROM er20_v22_scores WHERE scan_date = ?', (str(scan_date),))
            conn.commit()
        except sqlite3.OperationalError:
            pass
        exist = {r[1] for r in conn.execute('PRAGMA table_info(er20_v22_scores)').fetchall()}
        if not exist:
            conn.execute('CREATE TABLE er20_v22_scores (scan_date TEXT)')
            conn.commit()
            exist = {'scan_date'}
        for c in save.columns:
            if c not in exist:
                conn.execute(f'ALTER TABLE er20_v22_scores ADD COLUMN "{c}" TEXT')
        conn.commit()
        conn.execute('DELETE FROM er20_v22_scores WHERE scan_date = ?', (str(scan_date),))
        conn.commit()
        save.to_sql('er20_v22_scores', conn, if_exists='append', index=False)
        conn.commit()
    finally:
        conn.close()
    print(f'  已落库 {len(save)} 行 -> {DB_PATH_V22}')


# ============================================================
# 报告 V2.2（规格十三：6 区块固定格式）
# ============================================================
def fq_disp(r):
    return f"{r['fq']:.0f}" if pd.notna(r.get('fq')) else '-'


def _next_action(row):
    """WAIT 股票的下一触发条件"""
    if row['grade_reason'] and '组合仓位已满' in str(row['grade_reason']):
        return '等现有仓位释放'
    if row['overheat'] > V22Config.GATE['overheat_pullback']:
        return '等回调：距MA20回落至3%内'
    if row['ts'] == 0 or row['ttype'] == 'NO_TRIGGER':
        return '放量突破MA60 或 缩量回踩MA20后阳线'
    if row['ts'] < 70:
        return '放量确认突破（量比≥1.2）'
    return '等回踩MA20缩量+重新放量'


def _reject_summary(df):
    cnt = Counter()
    for _, r in df[df['grade'] == 'REJECT'].iterrows():
        rs = str(r['grade_reason'])
        if rs.startswith('基本面'):
            cnt['基本面<25'] += 1
        elif '一次性' in rs:
            cnt['一次性收益主导'] += 1
        elif '现金流' in rs:
            cnt['现金流恶化REJECT'] += 1
        elif 'Alpha' in rs:
            cnt['低Alpha'] += 1
        else:
            cnt[rs[:20]] += 1
    return cnt.most_common(8)


def build_report_v22(df, scan_date, regime, market_mult):
    lines = []
    main_df = df[df['rank_eligible']]

    # 【1. 市场环境】
    lines.append(f'# ER20 V2.2 中报事件驱动扫描 — {scan_date}')
    lines.append('')
    lines.append('【1. 市场环境】')
    lines.append(f'MarketRegime: {regime} | Multiplier: x{market_mult}')
    lines.append('')
    lines.append(f'样本 {len(df)} 只')

    # 【2. ALPHA TOP20】
    top = main_df.head(20)
    lines.append('')
    lines.append('【2. ALPHA TOP20】')
    lines.append('| 排名 | 股票 | Event | Alpha | FQ | CF_CONTEXT | EES | Trigger | Status |')
    lines.append('|---:|---|---|---:|---|---:|---|---|')
    for i, (_, r) in enumerate(top.iterrows()):
        cf = r['cf_label'] if r['cf_label'] else '-'
        trig = f"{r['ttype']} {r['ts']:.0f}" if r['ttype'] != 'NO_TRIGGER' else '无触发'
        lines.append(f"| {i+1} | {r['name']}({r['ts_code'][:6]}) | {r['strategy'][:12]} | "
                     f"{r['alpha']:.1f} | {fq_disp(r)} | {cf[:14]} | {r['ees']:.0f} | {trig} | {r['grade']} |")
    lines.append('')

    fundamental = main_df[main_df['sep_fundamental_label'].eq('FUNDAMENTAL_CORE')].copy()
    fundamental = fundamental.sort_values(['sep_fundamental_score', 'alpha'], ascending=[False, False]).head(20)
    lines.append('【2B. SEPTEMBER FUNDAMENTAL CORE】')
    lines.append('只表示业绩与质量研究优先级；不代表价格已具备交易条件。')
    lines.append('| 股票 | 基本面分层 | 基本面分 | 增长/延续/质量/风险 | 当前执行 | 失效边界 |')
    lines.append('|---|---|---:|---|---|---|')
    for _, r in fundamental.iterrows():
        parts = f"{r['sep_growth']:.0f}/{r['sep_continuation']:.0f}/{r['sep_quality']:.0f}/{r['sep_risk']:.0f}"
        lines.append(f"| {r['name']}({r['ts_code'][:6]}) | {r['sep_fundamental_label']} | "
                     f"{r['sep_fundamental_score']:.1f} | {parts} | {r['grade']} | {r['sep_invalidation']} |")
    if fundamental.empty:
        lines.append('当前公告窗口内没有基本面核心研究标的。')
    lines.append('')

    opportunity = main_df[main_df['sep_label'].eq('SEPTEMBER_OPPORTUNITY')].copy()
    opportunity = opportunity.sort_values(['sep_setup_score', 'sep_fundamental_score', 'alpha'], ascending=[False, False, False]).head(20)
    lines.append('【2C. SEPTEMBER OPPORTUNITY WATCH】')
    lines.append('仅当基本面核心与价格吸收/触发同时成立才进入本池；仍以 TODAY BUY 的状态机为唯一执行依据。')
    lines.append('| 股票 | 机会状态 | 基本面/准备度 | 预期/吸收/触发 | 当前执行 | 延续条件 |')
    lines.append('|---|---|---:|---|---|---|')
    for _, r in opportunity.iterrows():
        setup = f"{r['sep_expectation']:.0f}/{r['sep_absorption']:.0f}/{r['sep_trigger']:.0f}"
        scores = f"{r['sep_fundamental_score']:.0f}/{r['sep_setup_score']:.0f}"
        lines.append(f"| {r['name']}({r['ts_code'][:6]}) | {r['sep_setup_label']} | {scores} | "
                     f"{setup} | {r['grade']} | {r['sep_condition']} |")
    if opportunity.empty:
        lines.append('当前没有同时通过基本面核心与价格准备度约束的九月机会。')
    lines.append('')

    # 【3. TODAY BUY】
    lines.append('【3. TODAY BUY】')
    for gname in ('CORE_BUY', 'TEST_BUY', 'PROBE_BUY'):
        sub = df[(df['grade'] == gname) & (df['next_day_signal'] == 1)]
        lines.append(f'### {gname}（{len(sub)} 只）')
        if sub.empty:
            lines.append('无（纪律：宁可空仓，不降标准）')
        else:
            for _, r in sub.iterrows():
                pos = {'CORE_BUY': 0.15, 'TEST_BUY': 0.12, 'PROBE_BUY': 0.05}[gname]
                bp = f"{r['ttype']} {r['ts']:.0f}分" if r['ttype'] != 'NO_TRIGGER' else r['tdesc']
                lines.append(f"- **{r['name']}** ({r['ts_code'][:6]}) | Alpha={r['alpha']:.1f} | "
                             f"ER20次日分={r['next5_score']:.1f} | EES={r['ees']:.0f} | "
                             f"买点={bp} | EGPT诊断={r.get('egpt_buy_point', '') or '未覆盖'} | 仓位={pos:.0%}")
    lines.append('')

    # 【3B. FILTER POOL 过滤线观察池】双模式之二
    # 依据: grade_filter 快照(限仓/名额前) 非REJECT非WAIT_CONFIRM, 与2026H1 OOS PASS口径一致
    fp_df = df[(df.get('filter_pass', False)) & (df['rank_eligible'])]
    lines.append('【3B. FILTER POOL 过滤线观察池】'
                 f'共 {len(fp_df)} 只 / 全样本 {len(df)} '
                 f'(质量线快照: TOP2 名额外可跟踪池)')
    if fp_df.empty:
        lines.append('今日无过滤线通过标的')
    else:
        fp_sorted = fp_df.sort_values(
            ['alpha', 'fusion_score'], ascending=[False, False])
        g_cnt = Counter(fp_df['grade_filter'])
        cnt_s = ' | '.join(f'{g}×{n}' for g, n in g_cnt.most_common())
        lines.append(f'池内分布: {cnt_s}')
        show = fp_sorted.head(40)
        lines.append('| 股票 | Alpha | Fusion | 初评 | 当前Status | Decay | Trigger | 下一动作 |')
        lines.append('|---|---:|---:|---|---|---|---|---|')
        for _, r in show.iterrows():
            trig = f"{r['ttype']} {r['ts']:.0f}" if r['ttype'] != 'NO_TRIGGER' else '无触发'
            act = 'T+1开盘可买(Top2)' if r['next_day_signal'] == 1 else _next_action(r)
            status = r['grade'] if r['grade'] == r['grade_filter'] else \
                f"{r['grade']}({r['grade_reason'][:8]})"
            lines.append(f"| {r['name']}({r['ts_code'][:6]}) | {r['alpha']:.1f} | "
                         f"{r['fusion_score']:.1f} | {r['grade_filter']} | {status} | "
                         f"{r['decay_state'][:12]} | {trig} | {act[:14]} |")
        if len(fp_sorted) > 40:
            lines.append(f'…其余 {len(fp_sorted) - 40} 只按 Alpha 排序见 SQLite (filter_pass=1)')
    lines.append('')

    # 【4. WAIT TOP10】
    wait = main_df[main_df['grade'].isin(['WAIT_CONFIRM', 'WAIT_PULLBACK'])].head(10)
    lines.append('【4. WAIT TOP10】')
    lines.append('| 股票 | Alpha | 等待原因 | 下一触发条件 |')
    lines.append('|---|---:|---|---|')
    for _, r in wait.iterrows():
        lines.append(f"| {r['name']}({r['ts_code'][:6]}) | {r['alpha']:.1f} | "
                     f"{r['grade_reason']} | {_next_action(r)} |")
    lines.append('')

    # 【5. REJECT SUMMARY】
    lines.append('【5. REJECT SUMMARY】（共 %d 只）' % (df['grade'] == 'REJECT').sum())
    for reason, n in _reject_summary(df):
        lines.append(f'- {reason} × {n}')
    lines.append('')

    # 【6. 每日Top候选简述】（Top10）
    lines.append('【6. 每日Top候选简述】')
    for _, r in top.head(10).iterrows():
        dt = r['dt_netprofit_yoy'] if 'dt_netprofit_yoy' in r and pd.notna(r.get('dt_netprofit_yoy')) else None
        tr = r['tr_yoy'] if 'tr_yoy' in r and pd.notna(r.get('tr_yoy')) else None
        lines.append(f"### {r['name']} ({r['ts_code']})  Alpha={r['alpha']:.1f}  {r['grade']}")
        lines.append(f"- 20D Thesis: {r['strategy']}，{r['decay_state']}，"
                     f"公告后涨幅{float(r['post_ret']):.1f}%{'  ⚠公告前已涨' if r['pre_priced'] else ''}")
        dt_s = f"{dt:.1f}%" if dt is not None else 'NA'
        tr_s = f"{tr:.1f}%" if tr is not None else 'NA'
        lines.append(f"- Fundamental: FQ={fq_disp(r)}，扣非增速{dt_s}/营收增速{tr_s}")
        lines.append(f"- Cashflow: {r['cf_label']}（{r['cf_reason']}）")
        lines.append(f"- Trigger: {r['ttype']} {r['ts']:.0f}分 | Risk: {r['rel_risk']:.0f} | Overheat: {r['overheat']:.0f}")
        lines.append(f"- Next Action: {'今日可买(过RiskGate)' if r['grade'] in ('CORE_BUY','TEST_BUY','PROBE_BUY') else _next_action(r)}")
    lines.append('')
    return '\n'.join(lines)


# ============================================================
# 验证 + 对比
# ============================================================
def validate_er20_v22(df):
    g = V22Config.GATE
    print('===== ER20 V2.2 VALIDATION =====')
    ok = True
    n_nan = df['alpha'].isna().sum()
    print(f'  [{"PASS" if n_nan == 0 else "FAIL"}] NaN in alpha: {n_nan}')
    if n_nan: ok = False

    core = df[df['grade'] == 'CORE_BUY']
    test = df[df['grade'] == 'TEST_BUY']
    probe = df[df['grade'] == 'PROBE_BUY']
    bad_core = core[(core['alpha'] < g['core_alpha']) | (core['ees'] < g['core_ees'])
                    | (core['ts'] < g['core_ts']) | (core['rel_risk'] > g['core_risk'])
                    | (core['overheat'] > g['core_overheat'])]
    print(f'  [{"PASS" if bad_core.empty else "FAIL"}] CORE_BUY 门槛: {len(bad_core)} 违规')
    if not bad_core.empty: ok = False
    bad_test = test[(test['alpha'] < g['test_alpha']) | (test['ees'] < g['test_ees'])
                    | (test['ts'] < g['test_ts']) | (test['rel_risk'] > g['test_risk'])]
    print(f'  [{"PASS" if bad_test.empty else "FAIL"}] TEST_BUY 门槛: {len(bad_test)} 违规')
    if not bad_test.empty: ok = False
    bad_probe = probe[(probe['alpha'] < g['probe_alpha']) | (probe['ees'] < g['probe_ees'])]
    print(f'  [{"PASS" if bad_probe.empty else "FAIL"}] PROBE_BUY 门槛: {len(bad_probe)} 违规')
    if not bad_probe.empty: ok = False

    d_in_buy = df[(df['strategy'] == 'D_FALSE_SIGNAL') & (df['grade'].isin(['CORE_BUY', 'TEST_BUY', 'PROBE_BUY']))]
    print(f'  [{"PASS" if d_in_buy.empty else "FAIL"}] D_FALSE_SIGNAL 不入BUY: {len(d_in_buy)}')
    if not d_in_buy.empty: ok = False

    no_trig_buy = df[(df['ts'] == 0) & (df['grade'].isin(['CORE_BUY', 'TEST_BUY', 'PROBE_BUY']))]
    print(f'  [{"PASS" if no_trig_buy.empty else "FAIL"}] 无触发不入BUY: {len(no_trig_buy)}')
    if not no_trig_buy.empty: ok = False

    st = df[df['name'].astype(str).str.startswith(('ST', '*ST'))]
    print(f'  [{"PASS" if st.empty else "FAIL"}] ST 已过滤: {len(st)}')
    if not st.empty: ok = False

    n_buy = len(core) + len(test) + len(probe)
    total_pos = len(core) * 0.15 + len(test) * 0.12 + len(probe) * 0.05
    print(f'  [{"PASS" if total_pos <= 1.0 + 1e-9 else "FAIL"}] 组合总仓位 {total_pos:.0%} ≤100%')
    if total_pos > 1.0: ok = False
    print(f'  [{"PASS" if n_buy <= 8 else "FAIL"}] 重点持仓 {n_buy} 只 ≤8')
    if n_buy > 8: ok = False

    if 'theme' in df.columns:
        th_pos = {}
        for _, r in df[df['grade'].isin(['CORE_BUY', 'TEST_BUY', 'PROBE_BUY'])].iterrows():
            t = str(r['theme'])
            if t and t != 'nan':
                p = {'CORE_BUY': 0.15, 'TEST_BUY': 0.12, 'PROBE_BUY': 0.05}[r['grade']]
                th_pos[t] = th_pos.get(t, 0) + p
        over = {k: v for k, v in th_pos.items() if v > 0.30}
        print(f'  [{"PASS" if not over else "FAIL"}] 同主题限仓: {over if over else "无超限"}')
        if over: ok = False

    print(f'  等级: {dict(Counter(df["grade"]))}')
    print(f'  问题数: {0 if ok else "有FAIL"}')
    return ok


def compare_v21_v22(scan_date, df_new):
    print('===== V2.1 vs V2.2 对比（10 只重点股） =====')
    watch = {'恒誉环保', '恒誉科技', '移远通信', '芯联集成', '九号公司', '盛美上海',
             '卫星化学', '江波龙', '潜能恒信', '中望软件', '生益科技'}
    try:
        v21 = pd.read_sql('SELECT * FROM er20_v21_scores WHERE scan_date=?',
                          sqlite3.connect(os.path.join(REPORT_DIR, 'er20_v21_scores.db')),
                          params=[str(scan_date)])
    except Exception:
        v21 = pd.DataFrame()
    print(f'{"股票":<8}{"V21_A":>7}{"V22_A":>7}  {"V21等级":<16}{"V22等级":<16}')
    print('-' * 60)
    for _, r in df_new.iterrows():
        nm = str(r['name'])
        if nm in watch:
            a22 = r['alpha']
            g22 = r['grade']
            a21 = v21[v21['ts_code'] == r['ts_code']]['alpha'].iloc[0] if not v21.empty and not v21[v21['ts_code'] == r['ts_code']].empty else None
            g21 = v21[v21['ts_code'] == r['ts_code']]['grade'].iloc[0] if not v21.empty and not v21[v21['ts_code'] == r['ts_code']].empty else '-'
            print(f'{nm:<8}{a21 if a21 is not None else "--":>7}{a22:>7.1f}  {str(g21):<16}{g22:<16}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default='20260820')
    ap.add_argument('--validate', action='store_true')
    ap.add_argument('--compare', action='store_true')
    args = ap.parse_args()
    df = scan_v22(args.date)
    if df is None:
        return
    regime, market_mult = market_multiplier(args.date)
    rep = build_report_v22(df, args.date, regime, market_mult)
    fp = os.path.join(REPORT_DIR, f'er20_v22_report_{args.date}.md')
    with open(fp, 'w', encoding='utf-8') as f:
        f.write(rep)
    print(f'  报告已写 {fp}')
    if args.validate:
        validate_er20_v22(df)
    if args.compare:
        compare_v21_v22(args.date, df)


if __name__ == '__main__':
    main()
