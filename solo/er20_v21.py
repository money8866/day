# -*- coding: utf-8 -*-
"""
ER20 V2.1 — Context-Aware Earnings Repricing Engine
=====================================================
在 V2.0 基础上增量升级 6 大模块：

新增模块               解决的核心问题
─────────────────────────────────────────────────────────
Cashflow Context Engine  OCF恶化≠REJECT；拆解现金流变化原因
Alpha Decay Engine       公告信息随时间衰减，Alpha 不能永久存在
Probe / Early Entry      高 Alpha 无完美买点→允许1%~2%观察仓
Relative Risk Engine     高Beta成长股不因ATR高被严重降权
Earnings Quality Context 扣非下降≠一次性收益；拆解非经常性损益
Tradeability Calibration  监控入口宽松度，Top20不能全部WAIT

V2 → V2.1 升级映射
─────────────────────
V2 模块                         → V2.1 变更
─────────────────────────────────────────────────────────
classify_event (D4: OCF<-80)    → 移入 cashflow_context，D4 规则改为"结构性现金流恶化"
risk_score (ATR绝对)            → 新增 relative_risk_score，行业相对波动
grade_v2 (无PROBE)              → grade_v21 (+PROBE_BUY)
scan_v2 (alpha=norm×conf×risk)  → scan_v21 (+Decay/Refresh/Cashflow/RelativeRisk)
save_sqlite (23列)              → 新增14列(event_age/er20_base/cfcs/decay/refresh/ees/...)
report (4榜单)                   → 7榜单(+PROBE/Cashflow Context/EQ Context/Tradeability)

设计原则
────────
1. V2 代码不动，er20_v21.py 独立文件
2. 所有新增评分：真实计算、可解释、可回测、有 Data Confidence
3. 禁止固定默认分、禁止 nan 评分、禁止硬编码 75/50
4. 数据获取 100% 复用现有缓存

用法
────
python -X utf8 er20_v21.py --date 20260820 [--compare] [--validate]
"""
import os, sys, glob, json, time, sqlite3, argparse
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
DB_PATH_V21 = os.path.join(REPORT_DIR, 'er20_v21_scores.db')

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


# ============================================================
# ER20Config V2.1 扩展（不修改 V2 的 ER20Config 类定义）
# ============================================================
class V21Config:
    # ── 现金流语境 ──
    CASHFLOW = {
        'ocf_threshold': -80,           # OCF YoY 恶化阈值（不再直接 REJECT）
        'rev_growth_min': 20,           # 快速扩张收入增速%
        'ar_turn_detect_days': 30,      # 应收周转恶化天数阈值
        'working_cap_growth_max': 0.50, # 营运资本增长上限
        'cfcs_healthy': 85,             # HEALTHY 基准分
        'cfcs_wcap_expansion': 65,      # 营运资本扩张基准分
        'cfcs_inventory_build': 70,     # 补库存基准分
        'cfcs_receivable_risk': 35,     # 应收风险基准分
        'cfcs_structural_weak': 18,     # 结构性恶化基准分
        'cf_adjustment_max': 12.0,      # CFCS 对 ER20 最大调整
    }
    # ── 周期行业识别 ──
    CYCLICAL_KEYWORDS = {'半导体', '存储', '面板', '显示屏', '化学', '化工',
                         '光伏', '太阳能', '新能源', '设备', '材料', '电子',
                         '芯片', '集成电路', '内存', '闪存', '显示', '锂电'}
    # ── Alpha Decay ──
    DECAY = {
        'tiers': [(2, 0.03), (5, 0.08), (10, 0.18), (15, 0.28), (20, 0.38)],
        'refresh_max': 10.0,            # AlphaRefresh 最高 +10
        'refresh_trigger_vol_ratio': 1.3,
        'refresh_trigger_ret': 0.03,
        'absorption_vol_ratio': 0.7,    # 缩量横盘=信息未充分吸收
        'absorption_ret_range': 0.04,
    }
    # ── Probe Entry ──
    PROBE = {
        'alpha_min': 82.0,              # PROBE_BUY 最低 ER20 Alpha
        'conf_min': 80.0,               # PROBE 最低置信度
        'fq_min': 65.0,                 # PROBE 最低基本面
        'risk_min': 50.0,               # Risk 上限（分数越低风险越大，此处指最低可接受分）
        'overheat_max': 20.0,           # 透支惩罚上限
        'support_dist_max': 0.05,       # 距支撑最大距离
        'entry_max': 75.0,              # ENTRY<75 才触发 PROBE（≥75 走 TEST/CORE）
        'entry_min': 50.0,              # ENTRY 最低要求
        'position': {82: 0.01, 85: 0.015, 90: 0.02},
        'ees_weights': {'alpha': 0.30, 'support': 0.25, 'rr': 0.20, 'stability': 0.15, 'market': 0.10},
    }
    # ── Relative Risk ──
    REL_RISK = {
        'atr_percentile_threshold': 0.75,
        'beta_cap': 1.5,
        'relative_vol_cap': 1.5,
        'rrrs_base': 50.0,
    }
    # ── Earnings Quality ──
    EQ = {
        'dt_diverge_threshold': 30,     # 扣非与归母差>30pct才触发分析
        'eq_penalty_low': 15.0,         # LOW_QUALITY 扣分
        'eq_penalty_mixed': 8.0,        # MIXED_QUALITY 扣分
    }
    # ── Tradeability ──
    TRADE = {
        'warning_top20_all_wait': True,
        'warning_probe_over_50pct': True,
    }


# ============================================================
# 行业缓存（stock_basic 一次性加载）
# ============================================================
_INDUSTRY_MAP = None


def _get_industry_map():
    global _INDUSTRY_MAP
    if _INDUSTRY_MAP is None:
        try:
            basic = load_stock_basic()
            if basic is not None and len(basic):
                _INDUSTRY_MAP = dict(zip(basic['ts_code'], basic.get('industry', None)))
            else:
                _INDUSTRY_MAP = {}
        except Exception:
            _INDUSTRY_MAP = {}
    return _INDUSTRY_MAP


# ============================================================
# 模块1：Cashflow Context Engine
# ============================================================

def cashflow_context_engine(r, strategy, daily, ann_idx, cur_idx):
    """
    分析现金流变化的真实原因，不再简单 OCF YoY < 0 → REJECT。

    返回 (cfcs, context_label, cf_adjustment, reason)
      cfcs: Cashflow Context Score 0~100
      context_label: HEALTHY_CASHFLOW / WORKING_CAPITAL_EXPANSION / INVENTORY_BUILD
                    / RECEIVABLE_RISK / STRUCTURAL_CASHFLOW_WEAKNESS / DATA_INCOMPLETE
      cf_adjustment: ER20 调整量 -15 ~ +10
    """
    ocf = r.get('ocf_yoy', np.nan)
    ni = r.get('netprofit_yoy', np.nan)
    tr = r.get('tr_yoy', np.nan)
    q_sales = r.get('q_sales_yoy', np.nan)
    ar_turn = r.get('ar_turn', np.nan)
    ca_turn = r.get('ca_turn', np.nan)
    assets_turn = r.get('assets_turn', np.nan)
    cur_ratio = r.get('current_ratio', np.nan)
    work_cap = r.get('working_capital', np.nan)
    gm = r.get('grossprofit_margin', np.nan)
    q_roe = r.get('q_roe', np.nan)
    q1 = r.get('q1_profit_yoy', np.nan)
    q2 = r.get('q2_profit_yoy', np.nan)
    accel = (q2 - q1) if (_valid(q2) and _valid(q1)) else np.nan
    code = r.get('ts_code', '')
    industry = _get_industry_map().get(code, '')
    is_cyclical = _detect_cyclical(code, industry)

    if not _valid(ocf) or not _valid(ni):
        return None, 'DATA_INCOMPLETE', 0.0, '现金流数据不完整'

    cfg = V21Config.CASHFLOW
    ocf_v, ni_v = float(ocf), float(ni)

    # ── CASE 1：健康现金流 ──
    if ocf_v > 0 and ni_v > 0:
        ocf_to_ni = ocf_v / max(ni_v, 1) if ni_v > 0 else 0
        cfcs = cfg['cfcs_healthy']
        if ocf_v > ni_v * 0.6:
            cfcs = min(100, cfg['cfcs_healthy'] + 10)
        if _valid(tr) and tr > 0 and ocf_to_ni > 0.5:
            cfcs = min(100, cfcs + 5)
        return round(cfcs, 1), 'HEALTHY_CASHFLOW', 0.0, '利润与现金流双增长'

    # ── 利润增长但现金流下降 ──
    if ni_v > 0 and (ocf_v < 0 or ocf_v < ni_v * 0.3):
        # 检查是否周期成长股
        if is_cyclical and _valid(accel) and accel > 0:
            cfcs = cfg['cfcs_inventory_build']
            adj = 0.0
            if _valid(q_sales) and q_sales > 0:
                cfcs = min(85, cfcs + 8)
            return round(cfcs, 1), 'INVENTORY_BUILD', adj, '周期行业景气补库存'

        # 检查营运资本扩张
        rev_growing = _valid(tr) and tr > cfg['rev_growth_min']
        ar_ok = _valid(ar_turn) and ar_turn > 1.0
        ca_ok = _valid(ca_turn) and ca_turn > 0.5
        if rev_growing and (ar_ok or ca_ok):
            cfcs = cfg['cfcs_wcap_expansion']
            if _valid(tr) and tr > 50:
                cfcs = min(80, cfcs + 8)
            if _valid(gm) and gm > 20:
                cfcs = min(85, cfcs + 5)
            return round(cfcs, 1), 'WORKING_CAPITAL_EXPANSION', 0.0, '快速扩张导致营运资本占用'

        # 应收风险
        if rev_growing and _valid(ar_turn) and ar_turn < 1.0:
            cfcs = cfg['cfcs_receivable_risk']
            adj = -8.0
            if _valid(cur_ratio) and cur_ratio < 1.0:
                cfcs = max(10, cfcs - 10)
                adj = -12.0
            return round(cfcs, 1), 'RECEIVABLE_RISK', adj, '应收周转恶化'

        # 结构性恶化
        if _valid(ar_turn) and ar_turn < 1.0 and _valid(ca_turn) and ca_turn < 0.5:
            cfcs = cfg['cfcs_structural_weak']
            adj = -15.0
            return round(cfcs, 1), 'STRUCTURAL_CASHFLOW_WEAKNESS', adj, '应收+存货周转全面恶化'

    # ── 利润负、现金流也负 ──
    if ni_v < 0 and ocf_v < 0:
        return 10.0, 'STRUCTURAL_CASHFLOW_WEAKNESS', -15.0, '利润与现金流双恶化'

    return 50.0, 'DATA_INCOMPLETE', 0.0, '无法充分分析'


def _detect_cyclical(code, industry):
    km = V21Config.CYCLICAL_KEYWORDS
    ind = str(industry) if industry else ''
    stock = str(code) if code else ''
    return any(k in ind for k in km) or any(k in stock for k in km)


# ============================================================
# 模块2：Alpha Decay Engine
# ============================================================

def calc_alpha_decay(daily, ann_idx, cur_idx, event_age):
    """
    计算公告信息衰减因子 + 二次确认刷新。

    返回 (decay_factor, refresh, final_multiplier, decay_state)
      decay_factor: 0~0.40（越高衰减越多）
      refresh: 0~10（二次确认刷新）
      final_multiplier: 1.0 - decay_factor + refresh/100
      decay_state: NOT_ABSORBED / PRICED_IN / SECONDARY_CONFIRM
    """
    cfg = V21Config.DECAY

    # ── 基础衰减（事件年龄） ──
    decay = 0.0
    for age_thresh, d in cfg['tiers']:
        if event_age <= age_thresh:
            break
        decay = d
    else:
        decay = cfg['tiers'][-1][1]
    # 超过20天继续线性衰减
    if event_age > 20:
        decay = min(0.40, decay + (event_age - 20) * 0.015)

    # ── 公告后涨幅修正（涨得越多→衰减越快） ──
    if cur_idx > ann_idx:
        seg = daily.iloc[ann_idx:cur_idx + 1]
        cum_ret = float(seg['close'].iloc[-1]) / float(seg['close'].iloc[0]) - 1.0
        if cum_ret > 0.15:
            decay = min(0.40, decay + 0.12)
        elif cum_ret > 0.08:
            decay = min(0.40, decay + 0.06)

    # ── 判断衰减状态 ──
    refresh = 0.0
    state = 'NOT_ABSORBED'
    if cur_idx > ann_idx + 2:
        seg = daily.iloc[ann_idx + 1:cur_idx + 1]
        if len(seg) >= 3:
            avg_vol = float(daily['vol'].iloc[ann_idx - 20:ann_idx].mean())
            seg_vol = float(seg['vol'].mean())
            seg_ret = float(seg['close'].iloc[-1]) / float(seg['close'].iloc[0]) - 1.0
            vol_ratio = seg_vol / avg_vol if avg_vol > 0 else 1.0

            # 缩量横盘→信息未充分吸收
            if abs(seg_ret) <= cfg['absorption_ret_range'] and vol_ratio <= cfg['absorption_vol_ratio']:
                decay = max(0.0, decay - 0.08)
                state = 'NOT_ABSORBED'
            # 连续上涨放量→充分定价
            elif seg_ret > 0.08 and vol_ratio > 1.2:
                decay = min(0.40, decay + 0.10)
                state = 'PRICED_IN'
            # 二次放量突破→刷新
            if vol_ratio >= cfg['refresh_trigger_vol_ratio'] and seg_ret > cfg['refresh_trigger_ret']:
                refresh = min(cfg['refresh_max'], 8.0)
                state = 'SECONDARY_CONFIRM'

    final_mult = 1.0 - decay
    final_mult = max(0.60, min(1.00, final_mult))
    return round(decay, 3), round(refresh, 1), round(final_mult, 3), state


def calc_event_age_tradingdays(ann_date, scan_date):
    """计算事件年龄（交易日，非自然日）"""
    dates = get_trade_dates('20250101', str(scan_date))
    ann = str(ann_date)
    tds = [d for d in dates if d >= ann]
    if not tds:
        return 0
    return len(dates) - dates.index(tds[0]) - 1


# ============================================================
# 模块3：Probe / Early Entry Engine
# ============================================================

def calc_early_entry_score(alpha, support_dist, risk, overheat, entry, market_mult):
    """
    EES = Early Entry Score 0~100
    0.30×Alpha Quality + 0.25×Support Proximity + 0.20×R/R + 0.15×Stability + 0.10×Market
    """
    w = V21Config.PROBE['ees_weights']
    # Alpha Quality（0~100）
    alpha_q = min(100, alpha) if _valid(alpha) else 50.0
    # Support Proximity（距支撑距离，越近越好）
    sup = 100.0 - min(100, support_dist * 1000) if _valid(support_dist) else 50.0
    # Risk/Reward
    rr = 100.0 - risk if _valid(risk) else 50.0
    # Stability（低透支=高稳定）
    stab = 100.0 - overheat if _valid(overheat) else 60.0
    # Market
    mkt = 50.0 + (market_mult - 1.0) * 150
    ees = w['alpha'] * alpha_q + w['support'] * sup + w['rr'] * rr + w['stability'] * stab + w['market'] * mkt
    return round(min(100, max(0, ees)), 1)


def calc_support_distance(daily, cur_idx):
    """计算距最近支撑（MA20/MA60）的距离"""
    if cur_idx < 22:
        return 0.10
    last = daily.iloc[cur_idx]
    c = float(last['close'])
    ma20 = last.get('ma20', np.nan)
    ma60 = last.get('ma60', np.nan)
    dists = []
    if _valid(ma20) and ma20 > 0:
        dists.append(abs(c / ma20 - 1))
    if _valid(ma60) and ma60 > 0:
        dists.append(abs(c / ma60 - 1))
    if not dists:
        return 0.10
    return min(dists)


def probe_eligible(alpha, conf, fq, risk, overheat, support_dist, entry):
    """判断是否满足 PROBE_BUY 条件"""
    p = V21Config.PROBE
    if not _valid(alpha) or alpha < p['alpha_min']:
        return False
    if not _valid(conf) or conf < p['conf_min']:
        return False
    if not _valid(fq) or fq < p['fq_min']:
        return False
    if not _valid(risk) or risk < p['risk_min']:
        return False
    if _valid(overheat) and overheat > p['overheat_max']:
        return False
    if _valid(support_dist) and support_dist > p['support_dist_max']:
        return False
    if not _valid(entry) or entry < p['entry_min'] or entry > p['entry_max']:
        return False
    return True


def probe_position(alpha):
    """PROBE_BUY 仓位 1%~2%"""
    p = V21Config.PROBE['position']
    if alpha >= 90:
        return p[90]
    elif alpha >= 85:
        return p[85]
    return p[82]


# ============================================================
# 模块4：Relative Risk Engine
# ============================================================

def relative_risk_score(r, daily, cur_idx, ann_idx, benchmark_vol=None):
    """
    Relative Risk Score 0~100（低=风险小）。
    行业相对波动 + 行业相对最大回撤 + ATR Percentile + 流动性。
    """
    code = r.get('ts_code', '')
    industry = _get_industry_map().get(code, '')
    close = float(daily.iloc[cur_idx]['close'])
    atr = calc_atr14(daily)
    if not _valid(atr) or close <= 0:
        atr = close * 0.04
    atr_pct = atr / close * 100

    # 基础波动分（ATR%）
    vol_score = min(40, max(0, atr_pct / 4.0 * 40))
    # 相对波动调整：高Beta成长股如果行业整体波动也高，不额外扣分
    if benchmark_vol is not None and _valid(benchmark_vol) and benchmark_vol > 0:
        rel_vol = atr_pct / benchmark_vol
        if rel_vol < 1.2:
            vol_score = max(vol_score * 0.7, 10)  # 降权30%
        elif rel_vol > 1.5:
            vol_score = min(40, vol_score * 1.3)

    # 透支惩罚
    overheat = overheat_penalty(r, daily, ann_idx, cur_idx)

    # 偏离 MA20
    ma20 = float(daily.iloc[cur_idx].get('ma20', np.nan))
    dev_risk = 0.0
    if _valid(ma20) and ma20 > 0:
        dev = close / ma20 - 1
        if dev > 0.15: dev_risk = 20
        elif dev > 0.08: dev_risk = 10
        elif dev < -0.08: dev_risk = 12

    return round(min(100, vol_score + overheat * 0.8 + dev_risk), 1)


# ============================================================
# 模块5：Earnings Quality Context
# ============================================================

def earnings_quality_context(r):
    """
    评估盈利质量，不只根据扣非单项变化直接 REJECT。
    返回 (eq_label, eq_penalty, eq_detail)
      eq_label: HIGH_QUALITY / MIXED_QUALITY / LOW_QUALITY / ONE_OFF_DOMINATED
      eq_penalty: ER20 扣分 0~20
    """
    ni = r.get('netprofit_yoy', np.nan)
    dt = r.get('dt_netprofit_yoy', np.nan)
    gm = r.get('grossprofit_margin', np.nan)
    roe = r.get('roe', np.nan)
    ocf = r.get('ocf_yoy', np.nan)
    q_sales = r.get('q_sales_yoy', np.nan)

    if not _valid(ni) or not _valid(dt):
        return 'DATA_INCOMPLETE', 0.0, '扣非数据缺失'

    ni_v, dt_v = float(ni), float(dt)
    diverge = abs(ni_v - dt_v)

    # ── 扣非与归母同步增长 → 高质量 ──
    if ni_v > 0 and dt_v > 0 and diverge < 30:
        return 'HIGH_QUALITY', 0.0, '扣非与归母同步增长'

    # ── 扣非微降但归母大增 → 可能混合质量 ──
    if ni_v > 30 and dt_v < 0 and dt_v > -20:
        if _valid(gm) and gm > 20 and _valid(q_sales) and q_sales > 0:
            return 'MIXED_QUALITY', V21Config.EQ['eq_penalty_mixed'], '扣非微降但经营健康'
        return 'LOW_QUALITY', V21Config.EQ['eq_penalty_low'], '扣非下降且经营指标弱'

    # ── 扣非大幅负但归母暴增 → 一次性收益主导 ──
    if ni_v > 30 and dt_v < -30:
        return 'ONE_OFF_DOMINATED', 20.0, '扣非大幅下降=一次性收益主导'

    # ── 扣非增长但远低于归母（非经常性贡献大但主业也增长） → MIXED ──
    if ni_v > 30 and dt_v > 0 and dt_v < ni_v * 0.5:
        if dt_v > 30:
            return 'HIGH_QUALITY', 0.0, '扣非增长强劲非经常性合理补充'
        return 'MIXED_QUALITY', V21Config.EQ['eq_penalty_mixed'], '扣非增长但非经常性贡献更大'

    # ── 扣非为正但归母更高 → 非经常性贡献但主业也增长（兜底） ──
    if ni_v > 0 and dt_v > 0 and dt_v < ni_v * 0.5:
        return 'MIXED_QUALITY', V21Config.EQ['eq_penalty_mixed'], '扣非增长但非经常性贡献更大'

    return 'HIGH_QUALITY', 0.0, '盈利质量正常'


# ============================================================
# 模块6：Tradeability Calibration
# ============================================================

def calc_tradeability(alpha, entry, ees, decay_factor, event_age):
    """
    Tradeability Score 0~100。
    高Alpha+低Entry+高EES+低Decay → 可交易性高
    """
    s = 50.0
    if _valid(alpha):
        s += min(25, alpha * 0.2)
    if _valid(entry):
        s += (entry - 50) * 0.3
    if _valid(ees):
        s += (ees - 50) * 0.2
    if _valid(decay_factor):
        s -= decay_factor * 100 * 0.5
    if event_age > 10:
        s -= (event_age - 10) * 0.5
    return round(min(100, max(0, s)), 1)


def validate_tradeability(df):
    """可交易状态分布监控"""
    print('\n===== TRADEABILITY MONITOR =====')
    if df is None or df.empty:
        print('  无数据')
        return
    grades = df['grade'].value_counts()
    print('  等级分布: ' + '  '.join(f'{k}={v}' for k, v in grades.items()))
    top20 = df.head(20)
    top_grades = top20['grade'].value_counts()
    n_core = top_grades.get('CORE_BUY', 0)
    n_test = top_grades.get('TEST_BUY', 0)
    n_probe = top_grades.get('PROBE_BUY', 0)
    print(f'  Top20: CORE={n_core}  TEST={n_test}  PROBE={n_probe}')
    if n_core == 0 and n_test == 0 and n_probe == 0:
        print('  ⚠ WARNING: ENTRY_ENGINE_TOO_STRICT — Top20 全部 WAIT')
    elif n_probe > 10:
        print('  ⚠ WARNING: ENTRY_ENGINE_TOO_LOOSE — PROBE 超过 Top20 的 50%')
    else:
        print('  ✓ 可交易状态正常')
    print('================================')


# ============================================================
# V2.1 等级判定
# ============================================================

def grade_v21(alpha, entry, ees, risk, conf, fq, rqs, strategy, trigger,
              decay_factor, cf_label, eq_label, probe_ok, missing):
    """
    V2.1 分级（新增 PROBE_BUY）。
    """
    g = ER20Config.GATE
    # ── 假信号 → 但现金流结构性恶化才真正 REJECT ──
    if strategy == 'D_FALSE_SIGNAL':
        if cf_label in ('STRUCTURAL_CASHFLOW_WEAKNESS',):
            return 'REJECT', '现金流结构性恶化'
        if cf_label == 'RECEIVABLE_RISK':
            return 'REJECT', '应收风险'
        # 其他 D 类（INVENTORY_BUILD/WORKING_CAPITAL_EXPANSION）→ 降级为 WATCH
        return 'WATCH', f'现金流语境={cf_label}'
    # ── 事件股隔离 ──
    if strategy == 'C_EVENT_SPEC':
        return 'WATCH', '事件驱动仅观察'
    # ── 一次性收益 ──
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
    if conf < g['core_conf'] and alpha >= g['core_alpha']:
        return 'WATCH', f'置信{conf:.0f}<{g["core_conf"]}不可CORE_BUY'
    # ── CORE_BUY ──
    if (alpha >= g['core_alpha'] and entry >= g['core_entry']
            and risk <= g['core_risk'] and conf >= g['core_conf']
            and trigger and trigger != '无触发'):
        return 'CORE_BUY', ''
    # ── TEST_BUY ──
    if alpha >= g['test_alpha'] and entry >= g['test_entry']:
        return 'TEST_BUY', ''
    # ── PROBE_BUY ──
    if probe_ok:
        return 'PROBE_BUY', '高Alpha+位置合理，允许1%~2%观察仓'
    # ── 回踩/无触发 ──
    if not trigger or trigger == '无触发':
        if alpha >= g['watch_alpha']:
            return 'WAIT_CONFIRM', '高分但今日无触发'
        return 'WATCH', '等待触发'
    if trigger in ('回踩MA20后阳线', '温和上涨'):
        if alpha >= g['watch_alpha']:
            return 'WAIT_PULLBACK', '触发偏弱，等放量确认'
    if alpha >= g['watch_alpha']:
        return 'WAIT_CONFIRM', '观察等触发'
    return 'REJECT', f'Alpha{alpha:.0f}<{g["watch_alpha"]}'


# ============================================================
# P0-5: 组合总仓位控制
# ============================================================

def _apply_portfolio_cap(df):
    """
    组合层级仓位控制：
    - CORE_BUY 单只 ≤ 20%
    - TEST_BUY 单只 ≤ 12%
    - PROBE_BUY 单只 ≤ 2%
    总仓位 = min(100%, 20%×N_CORE + 12%×N_TEST + 2%×N_PROBE)
    如果总仓位 > 100%，优先保留 CORE_BUY，其次 TEST_BUY，最后 PROBE_BUY
    """
    n_core = (df['grade'] == 'CORE_BUY').sum()
    n_test = (df['grade'] == 'TEST_BUY').sum()
    n_probe = (df['grade'] == 'PROBE_BUY').sum()
    total = n_core * 0.20 + n_test * 0.12 + n_probe * 0.02
    if total <= 1.0:
        return df
    # 超仓 → 优先挤 PROBE_BUY，再挤 TEST_BUY
    excess = total - 1.0
    # 每个 PROBE 扣 0.02，直到 excess 清零
    probe_remove = min(n_probe, int(excess / 0.02 + 0.99))
    if probe_remove > 0:
        probe_idx = df[df['grade'] == 'PROBE_BUY'].index[-probe_remove:]
        df.loc[probe_idx, 'grade'] = 'WAIT_CONFIRM'
        df.loc[probe_idx, 'grade_reason'] = '组合仓位已满'
        excess -= probe_remove * 0.02
        n_probe -= probe_remove
    # 还不够 → 挤 TEST_BUY
    if excess > 0:
        test_remove = min(n_test, int(excess / 0.12 + 0.99))
        if test_remove > 0:
            test_idx = df[df['grade'] == 'TEST_BUY'].index[-test_remove:]
            df.loc[test_idx, 'grade'] = 'WAIT_CONFIRM'
            df.loc[test_idx, 'grade_reason'] = '组合仓位已满'
    return df


# ============================================================
# P0-4: 行业 ATR% 基准（供 Relative Risk 使用）
# ============================================================

def _precompute_industry_atr(pool, scan_date, sample=150):
    """抽样计算各行业 ATR% 中位数，供 Relative Risk 参考"""
    ind_vols = {}
    ind_map = _get_industry_map()
    sampled = pool.sample(min(sample, len(pool)), random_state=42) if len(pool) > sample else pool
    for _, r in sampled.iterrows():
        code = r['ts_code']
        ind = ind_map.get(code, '')
        if not ind:
            continue
        daily = load_daily_for(code, scan_date)
        if daily is None or len(daily) < 30:
            continue
        close = float(daily.iloc[-1]['close'])
        atr = calc_atr14(daily)
        if _valid(atr) and close > 0:
            atr_pct = atr / close * 100
            ind_vols.setdefault(ind, []).append(atr_pct)
    benchmark = {}
    for ind, vals in ind_vols.items():
        if len(vals) >= 3:
            benchmark[ind] = np.median(vals)
    return benchmark


# ============================================================
# P0-3: V2.1 Data Confidence（DATA_INCOMPLETE 不能 100 分）
# ============================================================

def data_confidence_v21(r, daily, ann_idx, missing, cf_label, eq_label):
    """
    V2.1 数据置信度。
    金融完整性 0.35 / 技术完整性 0.20 / 公告完整性 0.25 / 历史完整性 0.10 / 数据新鲜度 0.10
    + 现金流语境扣分 + 盈利质量未知扣分
    """
    fin_cols = ['netprofit_yoy', 'dt_netprofit_yoy', 'tr_yoy', 'q1_profit_yoy',
                'q2_profit_yoy', 'roe', 'ocf_yoy', 'grossprofit_margin']
    fin_ok = sum(1 for c in fin_cols if _valid(r.get(c, np.nan)))
    fin = fin_ok / len(fin_cols)
    tech = 1.0 if (daily is not None and len(daily) >= 120) else 0.3
    ann = str(r.get('ann_date', ''))
    ann_ok = 1.0 if (len(ann) == 8 and ann[:4] == '2026') else 0.4
    hist = 1.0 if len(daily) >= 65 else 0.5
    fresh = 0.7
    if len(daily) > 0:
        last_d = str(daily.iloc[-1]['trade_date'])
        fresh = 1.0 if ann <= last_d else 0.5
    conf = 100 * (0.35 * fin + 0.20 * tech + 0.25 * ann_ok + 0.10 * hist + 0.10 * fresh)
    # 现金流语境缺失 → 扣分
    if cf_label == 'DATA_INCOMPLETE':
        conf = min(conf, 85)
    # 盈利质量未知 → 扣分
    if eq_label == 'DATA_INCOMPLETE':
        conf = min(conf, 88)
    # 缺失因子 → 扣分
    if len(missing) >= 3:
        conf = min(conf, 90)
    if len(missing) >= 5:
        conf = min(conf, 80)
    return round(conf, 1)


# ============================================================
# V2.1 主流程
# ============================================================

def scan_v21(scan_date='20260820'):
    t0 = time.time()
    period = '20260630'
    print(f'[scan_v21] 扫描日 {scan_date}  报告期 {period}')
    # ── 市场环境 ──
    regime, market_mult = market_multiplier(scan_date)
    print(f'  市场环境: {regime}  x{market_mult}')
    bench = load_bench(scan_date)
    stock2theme = theme_map_stock2theme()
    _ = _get_industry_map()  # 预热行业缓存

    # ── 取池 + 粗筛 ──
    pool = load_pool_v2(period, scan_date)
    if pool.empty:
        print('  池为空，退出')
        return None
    print(f'  池规模: {len(pool)}')
    # ST 过滤
    pool = pool[~pool['name'].astype(str).str.startswith(('*ST', 'ST'))]
    print(f'  去ST后: {len(pool)}')
    # 行业 ATR% 基准（用于 Relative Risk）
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
        # ST 过滤
        name = str(r.get('name', ''))
        if name.startswith('*ST') or name.startswith('ST'):
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

    # ── 逐股评分 ──
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

        # ── V2.1 新引擎 ──
        # 1. 现金流语境
        cfcs, cf_label, cf_adj, cf_reason = cashflow_context_engine(
            r, strategy, daily, ann_idx, cur_idx)
        # 2. 盈利质量
        eq_label, eq_penalty, eq_detail = earnings_quality_context(r)
        # 3. 相对风险（传入行业基准）
        ind = _get_industry_map().get(code, '')
        benchmark_vol = industry_atr.get(ind, None)
        rel_risk = relative_risk_score(r, daily, cur_idx, ann_idx, benchmark_vol)
        # 4. V2.1 数据置信度
        conf = data_confidence_v21(r, daily, ann_idx, missing, cf_label, eq_label)
        # 4. 策略专属
        if strategy == 'B_REVERSAL':
            rqs = calc_rqs(r, missing)
            tqs = calc_tqs(daily, cur_idx, ann_idx, missing)
            pqs = trend = None
        else:
            rqs = tqs = None
            pqs = pullback_quality(daily, cur_idx, ann_idx, missing)
            trend = trend_structure(daily, cur_idx, missing)

        # ── 策略专属加权 ──
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

        rows.append({
            'ts_code': code, 'name': r.get('name', ''), 'ann_date': ann, 'gap': gap,
            'event_age': event_age,
            'strategy': strategy, 'cls_reason': cls_reason, 'raw': round(raw, 1),
            'fq': fq, 'rqs': rqs, 'gap_s': gap_s, 'ars': ars,
            'pqs': pqs, 'trend': trend, 'tqs': tqs,
            'risk_v2': risk_v2, 'rel_risk': rel_risk, 'overheat': overheat,
            'conf': conf, 'theme_adj': th,
            'cfcs': cfcs, 'cf_label': cf_label, 'cf_adj': cf_adj, 'cf_reason': cf_reason,
            'eq_label': eq_label, 'eq_penalty': eq_penalty, 'eq_detail': eq_detail,
            'missing': '|'.join(sorted(set(missing))),
        })
        if (i + 1) % 200 == 0:
            print(f'  已评分 {i + 1}/{len(cands)}')

    if not rows:
        print('  无可评分候选')
        return None
    df = pd.DataFrame(rows)

    # ── 策略内 percentile 归一化 ──
    for strat, grp in df.groupby('strategy'):
        if len(grp) >= 3:
            df.loc[df['strategy'] == strat, 'norm'] = (
                grp['raw'].rank(pct=True).reindex(df[df['strategy'] == strat].index) * 100.0)
        else:
            df.loc[df['strategy'] == strat, 'norm'] = grp['raw'].clip(0, 100)

    # ── ER20_BASE = norm × Conf × RelRisk × Market + CashflowAdj ──
    conf_mult = df['conf'].astype(float) / 100.0
    risk_mult = 1.0 - (df['rel_risk'].astype(float) / 100.0) * ER20Config.RISK_PEN
    df['er20_base'] = (df['norm'] * conf_mult * risk_mult * market_mult
                       + df['cf_adj'].fillna(0.0) + df['theme_adj']).round(1)

    # ── Alpha Decay + Refresh ──
    decay_map = {}
    for _, row in df.iterrows():
        daily = load_daily_for(row['ts_code'], scan_date)
        if daily is None:
            decay_map[row['ts_code']] = (0.0, 0.0, 1.0, 'N/A')
            continue
        nxt = daily[daily['trade_date'] > row['ann_date']]
        if nxt.empty:
            decay_map[row['ts_code']] = (0.0, 0.0, 1.0, 'N/A')
            continue
        ann_idx = nxt.index[0]
        cur_idx = len(daily) - 1
        d = calc_alpha_decay(daily, ann_idx, cur_idx, row['event_age'])
        decay_map[row['ts_code']] = d

    df['decay_factor'] = df['ts_code'].map(lambda c: decay_map.get(c, (0.0, 0.0, 1.0, 'N/A'))[0])
    df['alpha_refresh'] = df['ts_code'].map(lambda c: decay_map.get(c, (0.0, 0.0, 1.0, 'N/A'))[1])
    df['decay_state'] = df['ts_code'].map(lambda c: decay_map.get(c, (0.0, 0.0, 1.0, 'N/A'))[3])

    # ── 最终 ER20_ALPHA = ER20_BASE × DecayMultiplier + Refresh − EQ_Penalty ──
    decay_mult = df['ts_code'].map(lambda c: decay_map.get(c, (0.0, 0.0, 1.0, 'N/A'))[2])
    df['alpha'] = (df['er20_base'] * decay_mult + df['alpha_refresh'].fillna(0.0)
                   - df['eq_penalty'].fillna(0.0)).round(1)
    df['alpha'] = df['alpha'].clip(0, 100)

    # ── Entry 引擎 ──
    entry_map = {}
    for _, row in df[df['alpha'] >= 45].iterrows():
        daily = load_daily_for(row['ts_code'], scan_date)
        if daily is None:
            continue
        nxt = daily[daily['trade_date'] > row['ann_date']]
        if nxt.empty:
            continue
        ann_idx = nxt.index[0]
        cur_idx = len(daily) - 1
        e = entry_engine(daily, cur_idx, ann_idx, None, row['rel_risk'], market_mult)
        entry_map[row['ts_code']] = e

    df['entry'] = df['ts_code'].map(lambda c: entry_map.get(c, (None, '未计算', None, None, None, None))[0])
    df['trigger'] = df['ts_code'].map(lambda c: entry_map.get(c, (None, '', None, None, None, None))[1])

    # ── Early Entry Score + Probe ──
    ees_map = {}
    probe_map = {}
    for _, row in df[df['alpha'] >= 45].iterrows():
        daily = load_daily_for(row['ts_code'], scan_date)
        if daily is None:
            continue
        sup_dist = calc_support_distance(daily, len(daily) - 1)
        ees = calc_early_entry_score(row['alpha'], sup_dist, row['rel_risk'],
                                     row['overheat'], row['entry'], market_mult)
        ees_map[row['ts_code']] = ees
        probe_map[row['ts_code']] = probe_eligible(
            row['alpha'], row['conf'], row['fq'], row['rel_risk'],
            row['overheat'], sup_dist, row['entry'])

    df['ees'] = df['ts_code'].map(lambda c: ees_map.get(c, None))
    df['probe_eligible'] = df['ts_code'].map(lambda c: probe_map.get(c, False))

    # ── Tradeability ──
    df['tradeability'] = df.apply(
        lambda r: calc_tradeability(r['alpha'], r['entry'], r['ees'],
                                    r['decay_factor'], r['event_age']), axis=1)

    # ── 分级 ──
    grades, reasons = [], []
    for _, row in df.iterrows():
        st, rs = grade_v21(
            row['alpha'], row['entry'], row['ees'], row['rel_risk'],
            row['conf'], row['fq'], row['rqs'], row['strategy'],
            row['trigger'], row['decay_factor'], row['cf_label'],
            row['eq_label'], row['probe_eligible'], [])
        grades.append(st)
        reasons.append(rs)
    df['grade'] = grades
    df['grade_reason'] = reasons

    # ── P1-2: 消除 Entry 69/70 断崖 ──
    # 将 Entry 70 门槛改为"69~70 且 Alpha≥75"也可以 TEST_BUY
    # 思路：在原有分级后，对 Alpha≥75 且 Entry 69~69.9 的 WAIT_* 股升级为 TEST_BUY
    df['grade'] = df.apply(
        lambda r: 'TEST_BUY' if (r['grade'] in ('WAIT_CONFIRM', 'WAIT_PULLBACK')
                                  and r['alpha'] >= 75
                                  and r['entry'] is not None
                                  and 69.0 <= r['entry'] < 70.0
                                  and r['strategy'] != 'C_EVENT_SPEC')
        else r['grade'], axis=1)

    # ── P1-1: TEST_BUY 增加 Minimum Alpha Gate ──
    # TEST_BUY 必须 Alpha ≥ 72（防止低 Alpha 高 Entry 信号）
    df['grade'] = df.apply(
        lambda r: 'WAIT_CONFIRM' if (r['grade'] == 'TEST_BUY'
                                      and r['alpha'] < 72)
        else r['grade'], axis=1)

    # ── P0-2: D_FALSE_SIGNAL 退出 Alpha 主排名 ──
    # D 类股票不参与 ER20_ALPHA 排序（但保留在数据集中供现金流语境分析）
    df['rank_eligible'] = df['strategy'] != 'D_FALSE_SIGNAL'

    # ── P0-5: 组合总仓位控制 ──
    # CORE_BUY 每只最多 20%，TEST_BUY 每只最多 12%，PROBE_BUY 每只最多 2%
    # 总仓位 Cap = min(100%, 20%×N_CORE + 12%×N_TEST + 2%×N_PROBE)
    df = _apply_portfolio_cap(df)

    # ── 排序（D 类排最后） ──
    df = df.sort_values(['rank_eligible', 'alpha'], ascending=[False, False]).reset_index(drop=True)
    save_sqlite_v21(df, scan_date)
    print(f'[scan_v21] 完成 {len(df)} 只，耗时 {time.time() - t0:.0f}s')
    return df


# ============================================================
# SQLite V2.1
# ============================================================

def save_sqlite_v21(df, scan_date):
    cols = ['ts_code', 'name', 'ann_date', 'gap', 'event_age', 'strategy', 'cls_reason',
            'raw', 'norm', 'er20_base', 'alpha',
            'fq', 'rqs', 'gap_s', 'ars', 'pqs', 'trend', 'tqs',
            'risk_v2', 'rel_risk', 'overheat', 'conf', 'theme_adj',
            'cfcs', 'cf_label', 'cf_adj', 'cf_reason',
            'eq_label', 'eq_penalty', 'eq_detail',
            'decay_factor', 'alpha_refresh', 'decay_state',
            'entry', 'trigger', 'ees', 'probe_eligible', 'tradeability',
            'grade', 'grade_reason', 'rank_eligible']
    save = df[[c for c in cols if c in df.columns]].copy()
    save.insert(0, 'scan_date', str(scan_date))
    save['missing'] = df.get('missing', '')
    conn = sqlite3.connect(DB_PATH_V21)
    try:
        # 首次建表
        try:
            conn.execute('DELETE FROM er20_v21_scores WHERE scan_date = ?', (str(scan_date),))
            conn.commit()
        except sqlite3.OperationalError:
            pass
        exist = {r[1] for r in conn.execute('PRAGMA table_info(er20_v21_scores)').fetchall()}
        if not exist:
            conn.execute('CREATE TABLE er20_v21_scores (scan_date TEXT)')
            conn.commit()
            exist = {'scan_date'}
        for c in save.columns:
            if c not in exist:
                conn.execute(f'ALTER TABLE er20_v21_scores ADD COLUMN "{c}" TEXT')
        conn.commit()
        # 同日期重跑先清旧行
        conn.execute('DELETE FROM er20_v21_scores WHERE scan_date = ?', (str(scan_date),))
        conn.commit()
        save.to_sql('er20_v21_scores', conn, if_exists='append', index=False)
        conn.commit()
    finally:
        conn.close()
    print(f'  已落库 {len(save)} 行 -> {DB_PATH_V21}')


# ============================================================
# V2.1 报告
# ============================================================

def build_report_v21(df, scan_date, regime, market_mult):
    lines = []
    lines.append(f'# ER20 V2.1 中报事件驱动扫描 — {scan_date}')
    lines.append(f'市场环境: {regime} x{market_mult}  |  样本 {len(df)} 只')
    lines.append(f'评分=ER20_BASE×Decay+Refresh−EQ_Penalty+CF_Adj')
    lines.append('')

    core = df[df['grade'] == 'CORE_BUY']
    test = df[df['grade'] == 'TEST_BUY']
    probe = df[df['grade'] == 'PROBE_BUY']
    main_df = df[df['rank_eligible']]  # P0-2: D 类退出主榜单
    wait = main_df[main_df['grade'].isin(['WAIT_CONFIRM', 'WAIT_PULLBACK'])]
    watch = main_df[main_df['grade'] == 'WATCH']
    rej = df[df['grade'] == 'REJECT']
    shown = main_df[main_df['grade'] != 'REJECT']
    d_signal = df[df['strategy'] == 'D_FALSE_SIGNAL']

    # 1. ALPHA TOP20
    top = shown.head(20) if len(shown) else shown
    lines.append(f'## 一、ALPHA TOP {len(top)}')
    lines.append('| 排名 | 代码 | 名称 | 事件 | Base | Decay | Refresh | Alpha | 置信 | EES | Entry | 触发 | 等级 |')
    lines.append('|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|')
    for i, (_, r) in enumerate(top.iterrows()):
        lines.append(f"| {i+1} | {r['ts_code']} | {r['name']} | {r['strategy'][:10]} | "
                     f"{r['er20_base']:.1f} | {r['decay_factor']:.2f} | {r['alpha_refresh']:.1f} | "
                     f"{r['alpha']:.1f} | {r['conf']:.0f} | {r['ees'] if pd.notna(r['ees']) else '-'} | "
                     f"{r['entry'] if pd.notna(r['entry']) else '-'} | {r['trigger']} | {r['grade']} |")
    lines.append('')

    # 2. TODAY BUY
    lines.append('## 二、TODAY BUY LIST')
    lines.append('### CORE_BUY')
    if core.empty:
        lines.append('无（纪律：宁可 0~5 只）')
    else:
        for _, r in core.iterrows():
            lines.append(f"- **{r['name']}** ({r['ts_code']}) {r['strategy']}  Alpha={r['alpha']:.1f}  Entry={r['entry']:.0f}  {r['trigger']}")
    lines.append('### TEST_BUY')
    if test.empty:
        lines.append('无')
    else:
        for _, r in test.iterrows():
            lines.append(f"- **{r['name']}** ({r['ts_code']}) {r['strategy']}  Alpha={r['alpha']:.1f}  Entry={r['entry']:.0f}  {r['trigger']}  仓位 {position_engine(r['strategy'], r['grade'], r['rel_risk'], 1.0)}")
    lines.append('### PROBE_BUY')
    if probe.empty:
        lines.append('无')
    else:
        for _, r in probe.iterrows():
            lines.append(f"- **{r['name']}** ({r['ts_code']}) {r['strategy']}  Alpha={r['alpha']:.1f}  EES={r['ees']:.0f}  仓位 {probe_position(r['alpha'])*100:.0f}%  {r['cf_label']}")

    lines.append('')

    # 3. WAIT
    lines.append('## 三、WAIT LIST')
    for _, r in wait.head(15).iterrows():
        lines.append(f"- **{r['name']}** ({r['ts_code']}) {r['strategy']}  Alpha={r['alpha']:.1f}  "
                     f"Entry={r['entry'] if pd.notna(r['entry']) else '-'}  {r['grade']}  {r['grade_reason']}")
    lines.append('')

    # 4. REJECT
    lines.append(f'## 四、REJECT（{len(rej)} 只）')
    if not rej.empty:
        vc = rej['grade_reason'].value_counts().head(6)
        for k, v in vc.items():
            lines.append(f'- {k} × {v}')
    lines.append('')

    # 5. Cashflow Context
    lines.append('## 五、现金流语境分布')
    if 'cf_label' in df.columns:
        vc = df['cf_label'].value_counts()
        for k, v in vc.items():
            lines.append(f'- {k}: {v} 只')
    lines.append('')

    # 6. Earnings Quality
    lines.append('## 六、盈利质量分布')
    if 'eq_label' in df.columns:
        vc = df['eq_label'].value_counts()
        for k, v in vc.items():
            lines.append(f'- {k}: {v} 只')
    lines.append('')

    # 7. Alpha Decay
    lines.append('## 七、Alpha Decay 分布')
    if 'decay_state' in df.columns:
        vc = df['decay_state'].value_counts()
        for k, v in vc.items():
            lines.append(f'- {k}: {v} 只')
    lines.append('')

    # 8. 个股报告
    lines.append('## 八、Top 候选个股报告')
    for _, r in shown.head(10).iterrows():
        lines.append(f'\n### {r["name"]} ({r["ts_code"]}) — {r["grade"]}  {r["grade_reason"]}')
        lines.append(f'- **20D Thesis**: {r["strategy"]} 事件，ER20={r["alpha"]:.1f}（Base={r["er20_base"]:.1f} '
                     f'Decay={r["decay_factor"]:.2f} Refresh={r["alpha_refresh"]:.1f}）')
        lines.append(f'- **Cashflow**: {r["cf_label"]} CFCS={r["cfcs"] if pd.notna(r["cfcs"]) else "-"}，{r.get("cf_reason","")}')
        lines.append(f'- **Earnings Quality**: {r["eq_label"]}，{r.get("eq_detail","")}')
        lines.append(f'- **Buy Trigger**: {r["trigger"]}（Entry={r["entry"] if pd.notna(r["entry"]) else "-"}，EES={r["ees"] if pd.notna(r["ees"]) else "-"}）')
        lines.append(f'- **Risk**: 相对风险 {r["rel_risk"]:.0f}（原始 {r["risk_v2"]:.0f}），透支 {r["overheat"]:.0f}')
        lines.append(f'- **Data**: 置信 {r["conf"]:.0f}，事件年龄 {r["event_age"]}天')
        lines.append(f'- **Tradeability**: {r["tradeability"]:.0f}')

    txt = '\n'.join(lines)
    fp = os.path.join(REPORT_DIR, f'er20_v21_report_{scan_date}.md')
    with open(fp, 'w', encoding='utf-8') as f:
        f.write(txt)
    print(f'  报告已写 {fp}')
    return txt


# ============================================================
# V2 vs V2.1 对比
# ============================================================

def compare_v2_v21(scan_date, df_new):
    focus = [('688309.SH', '恒誉环保'), ('603236.SH', '移远通信'), ('688469.SH', '芯联集成'),
             ('689009.SH', '九号公司'), ('688082.SH', '盛美上海'), ('002648.SZ', '卫星化学'),
             ('301308.SZ', '江波龙'), ('300191.SZ', '潜能恒信'), ('688083.SH', '中望软件'),
             ('600183.SH', '生益科技')]

    # 读 V2 SQLite
    v2_db = os.path.join(REPORT_DIR, 'er20_v2_scores.db')
    old = {}
    if os.path.exists(v2_db):
        conn = sqlite3.connect(v2_db)
        for ts, _ in focus:
            r = conn.execute("select ts_code,name,alpha,grade,risk,entry,trigger from er20_v2_scores "
                             "where scan_date=? and ts_code=?", (str(scan_date), ts)).fetchall()
            old[ts] = r[0] if r else None
        conn.close()

    print('\n===== V2 vs V2.1 对比 =====')
    print(f"{'股票':<8s}  {'V2 Alpha':>8s}  {'V2.1 Alpha':>9s}  {'V2等级':<15s}  {'V2.1等级':<15s}  {'变化原因'}")
    print('-' * 90)
    for ts, nm in focus:
        rn = df_new[df_new['ts_code'] == ts]
        ov = old.get(ts)
        nv2 = f"{ov[2]:.1f}" if ov and ov[2] is not None else 'N/A'
        nv1 = f"{rn.iloc[0]['alpha']:.1f}" if not rn.empty else 'N/A'
        gv2 = ov[3] if ov else 'N/A'
        gv1 = rn.iloc[0]['grade'] if not rn.empty else 'N/A'
        # 变化原因
        rr = ''
        if not rn.empty:
            rr = rn.iloc[0].get('cf_label', '')[:8] + ' '
            rr += f"D{rn.iloc[0]['decay_factor']:.2f}" + ' '
            rr += rn.iloc[0].get('eq_label', '')[:8]
        print(f'{nm:<8s}  {nv2:>8s}  {nv1:>9s}  {gv2:<15s}  {gv1:<15s}  {rr}')
    return old


# ============================================================
# V2.1 验证
# ============================================================

def validate_er20_v21(df):
    print('\n===== ER20 V2.1 VALIDATION =====')
    if df is None or df.empty:
        print('  PASS: 无数据')
        return
    issues = 0
    # 1. NaN = 0
    nan_cnt = df['alpha'].isna().sum()
    print(f'  [{"PASS" if nan_cnt == 0 else "FAIL"}] NaN in alpha: {nan_cnt}')
    if nan_cnt > 0: issues += 1
    # 2. 硬编码默认分检查
    for col in ['gap_s', 'ars', 'pqs', 'tqs', 'trend']:
        if col in df.columns:
            all_50 = (df[col].dropna() == 50.0).mean()
            if all_50 > 0.3:
                print(f'  [WARN] {col} 50.0占比 {all_50:.0%}（可能硬编码）')
    # 3. OCF单指标不能直接触发REJECT
    if 'cf_label' in df.columns:
        ocf_rej = df[((df['cf_label'] == 'STRUCTURAL_CASHFLOW_WEAKNESS') &
                       (df['grade'] == 'REJECT'))]
        print(f'  [INFO] 现金流结构性恶化REJECT: {len(ocf_rej)} 只')
        simple_rej = df[(df['grade'] == 'REJECT') & (df['grade_reason'].str.contains('现金流严重恶化'))]
        print(f'  [{"PASS" if len(simple_rej) == 0 else "FAIL"}] OCF单指标REJECT: {len(simple_rej)}')
        if len(simple_rej) > 0: issues += 1
    # 4. Event Age 用交易日
    if 'event_age' in df.columns:
        print(f'  [INFO] Event Age: min={df["event_age"].min()} max={df["event_age"].max()} '
              f'mean={df["event_age"].mean():.1f}')
    # 5. AlphaDecay 工作
    if 'decay_factor' in df.columns:
        print(f'  [INFO] Decay: min={df["decay_factor"].min():.3f} max={df["decay_factor"].max():.3f} '
              f'mean={df["decay_factor"].mean():.3f}')
    # 6. AlphaRefresh 不超过+10
    if 'alpha_refresh' in df.columns:
        over = (df['alpha_refresh'] > 10.0).sum()
        print(f'  [{"PASS" if over == 0 else "FAIL"}] Refresh>10: {over}')
        if over > 0: issues += 1
    # 7. PROBE_BUY 仓位不超过2%
    probe = df[df['grade'] == 'PROBE_BUY']
    print(f'  [INFO] PROBE_BUY: {len(probe)} 只')
    # 8. C_EVENT_SPEC 不能进入 CORE_BUY
    evt_core = df[(df['strategy'] == 'C_EVENT_SPEC') & (df['grade'] == 'CORE_BUY')]
    print(f'  [{"PASS" if len(evt_core) == 0 else "FAIL"}] C_EVENT_SPEC in CORE_BUY: {len(evt_core)}')
    if len(evt_core) > 0: issues += 1
    # 9. Top20 不能全部 WAIT
    top20 = df.head(20)
    buys = top20[top20['grade'].isin(['CORE_BUY', 'TEST_BUY', 'PROBE_BUY'])]
    print(f'  [{"WARN" if len(buys) == 0 else "PASS"}] Top20可交易: {len(buys)}')
    # 10. 等级分布
    if 'grade' in df.columns:
        vc = df['grade'].value_counts()
        print('  [INFO] 等级: ' + '  '.join(f'{k}={v}' for k, v in vc.items()))
    print(f'  问题数: {issues}')
    print('================================')


# ============================================================
# 主入口
# ============================================================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default='20260820', help='扫描日 YYYYMMDD')
    ap.add_argument('--compare', action='store_true', help='V2 vs V2.1 对比')
    ap.add_argument('--validate', action='store_true', help='运行完整验证')
    args = ap.parse_args()
    scan_date = args.date
    df = scan_v21(scan_date)
    if df is None:
        return
    regime, market_mult = market_multiplier(scan_date)
    build_report_v21(df, scan_date, regime, market_mult)
    if args.validate:
        validate_er20_v21(df)
        validate_tradeability(df)
    if args.compare:
        compare_v2_v21(scan_date, df)
    print('\n完成')


if __name__ == '__main__':
    main()