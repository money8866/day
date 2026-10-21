# -*- coding: utf-8 -*-
"""MSW 筛选与评分（任务书 §11 scanner.py：MSW-A 长期趋势筛选 + MSW-B 多波段质量评分）

两层分离：
  trend_filter()   MSW-A：未来数月仍可能保持中长期上涨趋势的硬性/连续条件
  quality_score()  MSW-B：独立 MSW_QUALITY_SCORE（六子评分，权重冻结）

评分只用评分日期及以前的数据（PIT）：历史波段统计来自 swing_analyzer（已确认转折点），
相对强度来自 feature_engine.MarketPanel（截至当日），财务来自 data_adapter PIT 查询。
"""
import numpy as np


# ---------------------------------------------------------------- 工具映射

def _lin(x, lo, hi, invert=False):
    """线性映射到 [0,100]；None/NaN → None"""
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(x):
        return None
    if invert:
        lo, hi = hi, lo
    if hi == lo:
        return 50.0
    v = (x - lo) / (hi - lo) * 100.0
    return float(min(100.0, max(0.0, v)))


def _mean_score(parts):
    vals = [p for p in parts if p is not None]
    if not vals:
        return None
    return float(np.mean(vals))


# ---------------------------------------------------------------- MSW-A 趋势筛选

def trend_filter(row, st: dict, rs: dict, funda_state: str,
                 uni_ok: bool, cfg: dict) -> dict:
    """MSW-A：长期趋势股票筛选（任务书 §4.2）

    row: feats.iloc[t]（单行 Series，PIT：该行及之前的数据）
    返回 {'pass': bool, 'reasons': [未过条件], 'features': {...}}
    条件全部来自 msw_config.json trend_filter 段（V1_INITIAL_HYPOTHESIS）。
    """
    tf = cfg.get('trend_filter', {})
    reasons = []
    feats_out = {
        'ma120_slope20': row.get('ma120_slope20'),
        'ma250_slope20': row.get('ma250_slope20'),
        'ret_120d': row.get('ret_120d'),
        'ret_250d': row.get('ret_250d'),
        'rs_120d': rs.get('rs_120d'),
        'rs_250d': rs.get('rs_250d'),
        'dist_high_250d': row.get('dist_high_250d'),
        'ma_align_score': row.get('ma_align_score'),
    }

    def bad(cond, msg):
        if cond:
            reasons.append(msg)

    if not uni_ok:
        reasons.append('universe')
    bad(not np.isfinite(row.get('ma120_slope20', np.nan))
        or row.get('ma120_slope20', -9) <= float(tf.get('ma120_slope_min', 0.0)),
        'ma120_slope')
    ma250_slope = row.get('ma250_slope20', np.nan)
    if not np.isfinite(ma250_slope):
        reasons.append('ma250_slope_nan')
    elif tf.get('ma250_not_declining', True) and ma250_slope < 0:
        # 允许「近 120 日相对强度持续改善」豁免：rs_120d > 0 视为改善
        rs120 = rs.get('rs_120d')
        if rs120 is None or not np.isfinite(rs120) or rs120 <= 0:
            reasons.append('ma250_declining')
    bad(not np.isfinite(row.get('ret_120d', np.nan))
        or row.get('ret_120d', -9) < float(tf.get('min_ret_120d', 0.0)), 'ret_120d')
    bad(not np.isfinite(row.get('ret_250d', np.nan))
        or row.get('ret_250d', -9) < float(tf.get('min_ret_250d', 0.0)), 'ret_250d')
    rs120 = rs.get('rs_120d')
    bad(rs120 is None or not np.isfinite(rs120)
        or rs120 < float(tf.get('rs_120d_min', 0.0)), 'rs_120d')
    dh = row.get('dist_high_250d', np.nan)
    bad(not np.isfinite(dh) or dh < -float(tf.get('max_dist_from_250d_high', 0.35)),
        'dist_high_250d')
    bad(int(st.get('swing_count', 0) or 0) < int(cfg.get('swing', {}).get('min_swing_count_250d', 3)),
        'swing_count')
    if funda_state == 'risk':
        reasons.append('fundamental_risk')
    return {'pass': len(reasons) == 0, 'reasons': reasons, 'features': feats_out}


# ---------------------------------------------------------------- 基本面分层（PIT）

def fundamental_state(funda: dict | None, cfg: dict) -> str:
    """A类持续成长 / B类周期反转 / neutral / risk / unknown（任务书 §4.3）

    只用 ann_date <= 当日 的已公布财报（PIT）。字段：or_yoy/netprofit_yoy/
    grossprofit_margin/ocf_to_or/roe/debt_to_assets。
    """
    if not funda:
        return 'unknown'
    fc = cfg.get('fundamental', {})
    or_yoy = funda.get('or_yoy')
    np_yoy = funda.get('netprofit_yoy')
    gm = funda.get('grossprofit_margin')
    ocf = funda.get('ocf_to_or')
    debt = funda.get('debt_to_assets')
    roe = funda.get('roe')

    # 重大风险：资不抵债级负债率 或 利润崩塌
    if (debt is not None and np.isfinite(debt) and debt > 90.0):
        return 'risk'
    if (np_yoy is not None and np.isfinite(np_yoy) and np_yoy < -100.0):
        return 'risk'

    growth = ((or_yoy is not None and np.isfinite(or_yoy) and or_yoy >= fc.get('class_a_min_or_yoy', 0.0))
              and (np_yoy is not None and np.isfinite(np_yoy) and np_yoy >= fc.get('class_a_min_netprofit_yoy', 0.0))
              and (gm is not None and np.isfinite(gm) and gm >= fc.get('class_a_min_gross_margin', 15.0))
              and (debt is None or not np.isfinite(debt) or debt <= fc.get('class_a_max_debt_to_assets', 80.0)))
    if growth:
        return 'A_growth'
    # B 类周期反转：亏损收窄或现金流转正（不要求高增长）
    rev_signs = 0
    if np_yoy is not None and np.isfinite(np_yoy) and np_yoy > fc.get('class_b_turn_or_yoy_min', -0.30):
        rev_signs += 1
    if ocf is not None and np.isfinite(ocf) and ocf > fc.get('class_b_min_ocf_to_or', -0.50):
        rev_signs += 1
    if roe is not None and np.isfinite(roe) and roe > 0:
        rev_signs += 1
    if rev_signs >= 2:
        return 'B_reversal'
    return 'neutral'


# ---------------------------------------------------------------- MSW-B 质量评分

def quality_score(row, st: dict, rs: dict, uni_row: dict,
                  cfg: dict) -> dict:
    """MSW_QUALITY_SCORE 六子评分（任务书 §5.2，权重冻结）

    row: feats.iloc[t]（单行 Series，PIT）
    返回 {'total': float|None, 'sub': {...}, 'tags': [...]}
    """
    w = cfg.get('quality_score', {}).get('weights', {})

    # 1) 长期趋势质量 25%
    trend = _mean_score([
        _lin(row.get('ma120_slope20'), -0.05, 0.05),
        _lin(row.get('ma250_slope20'), -0.05, 0.05),
        _lin(row.get('ma_align_score'), 0.0, 1.0),
        _lin(rs.get('rs_120d'), -0.20, 0.20),
        _lin(row.get('dist_high_250d'), -0.50, 0.0),
    ])
    # 2) 可交易波段空间 20%
    sh_uni = uni_row.get('stage_high') or (None, None)
    sh_price, sh_bar = sh_uni
    room = None
    if sh_price and np.isfinite(sh_price) and sh_price > 0:
        c = float(row['hfq_close'])
        room = sh_price / c - 1.0 if c > 0 else None
    swing_space = _mean_score([
        _lin(st.get('up_ret_median'), 0.03, 0.25),
        _lin(st.get('up_ret_mean'), 0.03, 0.25),
        _lin(st.get('pullback_depth_mean'), 0.05, 0.20),
        _lin(room, 0.0, 0.30),
    ])
    # 3) 回撤修复能力 20%
    recovery = _mean_score([
        _lin(st.get('recovery_rate'), 0.30, 0.90),
        _lin(st.get('recovery_days_median'), 60.0, 10.0, invert=True),
        _lin(row.get('mdd_250d'), -0.50, -0.10),
        _lin(st.get('breakout_fail_rate'), 0.60, 0.10, invert=True),
    ])
    # 4) 波段出现频率 15%
    cnt = int(st.get('swing_count', 0) or 0)
    freq_count = _lin(cnt, 1.0, 5.0)
    if cnt > 10:                      # 过高频率 = 噪声震荡，降权（§5.3）
        freq_count = (freq_count or 0.0) * 0.7
    frequency = _mean_score([
        freq_count,
        _lin(st.get('opportunity_density'), 0.004, 0.020),
    ])
    # 5) 量价结构质量 10%
    vol_contract = None
    a5 = row.get('amount_ma5')
    a20 = row.get('amount_ma20')
    if a5 is not None and a20 is not None and np.isfinite(a5) and np.isfinite(a20) and a20 > 0:
        vol_contract = float(a5) / float(a20)
    volume_structure = _mean_score([
        _lin(st.get('up_down_vol_ratio'), 0.60, 1.60),
        _lin(vol_contract, 1.00, 0.50, invert=True),
    ])
    # 6) 流动性与风险 10%
    liquidity_risk = _mean_score([
        _lin(uni_row.get('amount_med20_wan'), 3000.0, 50000.0),
        _lin(row.get('atr_pct'), 0.08, 0.02, invert=True),
    ])

    sub = {
        'trend_quality': trend,
        'swing_space': swing_space,
        'recovery': recovery,
        'frequency': frequency,
        'volume_structure': volume_structure,
        'liquidity_risk': liquidity_risk,
    }
    total = None
    if all(v is not None for v in sub.values()):
        total = float(sum(sub[k] * float(w.get(k, 0.0)) for k in sub))
    tags = []
    if (sub['trend_quality'] or 0) >= 70:
        tags.append('长期趋势型')
    if (sub['frequency'] or 0) >= 70:
        tags.append('高波段频率型')
    if (sub['recovery'] or 0) >= 70:
        tags.append('回撤修复型')
    return {'total': total, 'sub': sub, 'tags': tags}


def liquidity_score(uni_row: dict, row) -> float | None:
    """流动性评分（0-100）：20 日成交额中位数 + 换手稳定性"""
    return _lin(uni_row.get('amount_med20_wan'), 3000.0, 50000.0)


def risk_flags(row, st: dict, rs: dict, funda_state: str) -> list:
    """风险标记（估值/财务/流动性/趋势）——日报 risk_flags 字段"""
    flags = []
    if funda_state == 'risk':
        flags.append('财务风险')
    elif funda_state == 'unknown':
        flags.append('财务数据缺失')
    elif funda_state == 'neutral':
        flags.append('基本面中性')
    if row.get('dist_high_250d', 0) is not None and np.isfinite(row.get('dist_high_250d', np.nan)) \
            and row.get('dist_high_250d') < -0.30:
        flags.append('深度回撤')
    if row.get('ma120_slope20', 0) is not None and np.isfinite(row.get('ma120_slope20', np.nan)) \
            and row.get('ma120_slope20') < 0:
        flags.append('MA120走平转下')
    if st.get('breakout_fail_rate') is not None and st['breakout_fail_rate'] > 0.5:
        flags.append('突破失败频繁')
    if st.get('recovery_rate') is not None and st['recovery_rate'] < 0.5:
        flags.append('修复率低')
    if row.get('atr_pct', 0) is not None and np.isfinite(row.get('atr_pct', np.nan)) \
            and row.get('atr_pct') > 0.06:
        flags.append('高波动')
    return flags
