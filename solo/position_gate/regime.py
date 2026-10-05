# -*- coding: utf-8 -*-
"""CSI2000 Position Gate —— 六档状态机 + 仓位阶梯（§11~§21）

纪律（§30）：市场给信号，仓位做响应；仓位不预测市场。
本文件不做任何收益预测，只把「当前市场是否正在授权更高风险暴露」翻译成仓位响应。
"""
import numpy as np
import pandas as pd

from position_gate import config as C


def quantize_position(position_pct: float, step: float = None) -> float:
    """执行仓位按固定档位向下取整，避免实际暴露高于模型原始建议。"""
    step = float(step or C.POSITION_STEP_PCT)
    if not np.isfinite(position_pct) or step <= 0:
        return 0.0
    return float(np.floor(max(position_pct, 0.0) / step) * step)


# =========================================================
# §11 六档市场状态
# =========================================================
def classify_regime(feat: pd.DataFrame, th: dict = None) -> pd.DataFrame:
    """返回 DataFrame[regime, regime_reason]，按时间升序（PIT：只用当日及之前）"""
    th = {**C.REGIME_TH, **(th or {})}
    f = feat
    close, ma20, ma60 = f['close'], f['ma20'], f['ma60']
    ma20_up = f['ma20_slope_5'] > 0
    ma60_up = f['ma60_slope_20'] > 0
    b = f['breadth_ma20']
    bc5 = f['breadth_change_5']
    rs20 = f['rs20_vs_all']
    rsc5 = f['rs20_change_5']
    ro = f['riskoff_count']
    pres = f['breadth_pressure']

    c_strong = (close > ma20) & (ma20 > ma60) & ma20_up & ma60_up & \
               (b >= th['strong_breadth']) & (rs20 > 0) & (rsc5 > 0) & \
               (pres > 0) & (ro <= th['strong_riskoff_max'])
    c_trend = (close > ma20) & (ma20 > ma60) & ma20_up & \
              (b >= th['trend_breadth']) & (rs20 > 0) & (ro <= th['trend_riskoff_max'])
    c_exp = (close > ma20) & ma20_up & (b >= th['expansion_breadth']) & \
            ((rs20 > 0) | (rsc5 > 0))
    c_off = (close < ma20) & (~ma20_up) & \
            ((b < th['off_breadth']) | (ro >= th['off_riskoff'])) & (rs20 < 0)
    c_rec = (close > ma20) & (bc5 > 0) & (rsc5 > 0)

    out = pd.DataFrame(index=f.index)
    out['regime'] = 'RANGE'
    out['regime_reason'] = '兜底：不满足其它状态的严格条件'
    for name, cond, reason in [
        ('RECOVERY', c_rec, 'Close重回MA20 + 宽度改善 + RS修复'),
        ('OFF', c_off, 'Close<MA20 + MA20下降 + (宽度<35% 或 Risk-Off>=2) + RS20<0'),
        ('EXPANSION', c_exp, 'Close>MA20 + MA20向上 + 宽度>=55% + RS改善'),
        ('TREND', c_trend, 'Close>MA20>MA60 + MA20向上 + 宽度>=60% + RS20>0 + Risk-Off<=1'),
        ('STRONG_TREND', c_strong, 'MA20/MA60双上行 + 宽度>=65% + RS20>0且改善 + 新高>新低 + 无Risk-Off'),
    ]:
        m = cond.fillna(False).values
        out.loc[m, 'regime'] = name
        out.loc[m, 'regime_reason'] = reason
    return out


# =========================================================
# §12 状态区间内的细化（0~1 质量分）
# =========================================================
def quality_score(feat: pd.DataFrame) -> pd.Series:
    f = feat
    n1 = ((f['breadth_ma20'] - 0.30) / 0.45).clip(0, 1)
    n2 = (0.5 + f['breadth_change_5'] / 20.0).clip(0, 1)
    n3 = (0.5 + f['rs20_vs_all'] / 6.0).clip(0, 1)
    n4 = (0.5 + f['rs20_change_5'] / 6.0).clip(0, 1)
    n5 = (0.5 + f['breadth_pressure'] * 5.0).clip(0, 1)
    n6 = (1.0 - f['riskoff_count'] / 4.0).clip(0, 1)
    w = {'breadth': 0.30, 'breadth_mom': 0.15, 'rs20': 0.20,
         'rs20_mom': 0.15, 'pressure': 0.10, 'riskoff': 0.10}
    q = (w['breadth'] * n1 + w['breadth_mom'] * n2 + w['rs20'] * n3 +
         w['rs20_mom'] * n4 + w['pressure'] * n5 + w['riskoff'] * n6) / sum(w.values())
    return q.clip(0, 1)


# =========================================================
# §13 满仓资格 FULL_EXPOSURE_CONFIRMATION
# =========================================================
def full_exposure_conditions(feat: pd.DataFrame) -> pd.DataFrame:
    f = feat
    close, ma20, ma60 = f['close'], f['ma20'], f['ma60']
    c = pd.DataFrame(index=f.index)
    c['c1'] = (close > ma20) & (ma20 > ma60)
    c['c2'] = (f['ma20_slope_5'] > 0) & (f['ma20_slope_10'] > 0)          # MA20连续向上
    c['c3'] = f['breadth_ma20'] >= 0.65
    c['c4'] = f['rs20_vs_all'] > 0
    c['c5'] = f['rs20_change_5'] > 0                                       # RS20连续改善
    c['c6'] = f['new_high_ratio'] > f['new_low_ratio']
    # c7 最近5日没有重大 Risk-Off（无 R3/R4/R5 级事件，且当日事件数<=1）
    severe5 = (f[['R3_drop5', 'R4_drop20', 'R5_breadth_drop']]
               .fillna(False).astype(int).sum(axis=1) > 0)
    c['c7'] = (severe5.rolling(C.FULL_CONFIRM_LOOKBACK, min_periods=1).sum() == 0) & \
              (f['riskoff_count'].rolling(C.FULL_CONFIRM_LOOKBACK, min_periods=1).max() <= 1)
    # c8 最近5日没有「连续放量下跌」（价格↓且 volume_ratio_20>1.2 连续2日）
    vol_down = (f['ret1'] < 0) & (f['volume_ratio_20'] > 1.2)
    two_in_row = vol_down & vol_down.shift(1)
    c['c8'] = (two_in_row.fillna(False).astype(int)
               .rolling(C.FULL_CONFIRM_LOOKBACK, min_periods=1).sum() == 0)
    c = c.fillna(False).astype(bool)
    c['all_ok'] = c[['c1', 'c2', 'c3', 'c4', 'c5', 'c6', 'c7', 'c8']].all(axis=1)
    c['full_exposure_eligible'] = c['all_ok'].rolling(
        C.FULL_CONFIRM_MIN_DAYS, min_periods=C.FULL_CONFIRM_MIN_DAYS).min().fillna(0).astype(bool)
    return c


# =========================================================
# §12/§13/§15/§16 仓位 walk
# =========================================================
def position_walk(feat: pd.DataFrame, regime: pd.DataFrame, full: pd.DataFrame,
                  bands: dict = None, steps: dict = None) -> pd.DataFrame:
    bands = bands or C.REGIME_BANDS
    steps = steps or {'up': C.STEP_UP, 'up_strong': C.STEP_UP_STRONG,
                      'down': C.STEP_DOWN, 'down_risk': C.STEP_DOWN_RISK}
    q = quality_score(feat)
    out = pd.DataFrame(index=feat.index)

    for i, d in enumerate(feat.index):
        rg = regime.at[d, 'regime']
        fmin, ftarget, fmax = bands[rg]
        qq = float(q.iloc[i]) if np.isfinite(q.iloc[i]) else 0.35
        raw = fmin + (fmax - fmin) * qq                       # §12 区间内细化
        dvg = bool(feat.at[d, 'INDEX_BREADTH_DIVERGENCE']) if 'INDEX_BREADTH_DIVERGENCE' in feat else False
        hp = bool(feat.at[d, 'HEALTHY_PULLBACK']) if 'HEALTHY_PULLBACK' in feat else False
        if dvg:                                                # §17 指数假强：不允许因指数上涨加仓
            raw = min(raw, fmin + 0.4 * (fmax - fmin))
        ro_cnt = int(feat.at[d, 'riskoff_count']) if np.isfinite(feat.at[d, 'riskoff_count']) else 0

        elig = bool(full.at[d, 'full_exposure_eligible']) if d in full.index else False
        cap = fmax
        if rg == 'STRONG_TREND' and not elig:
            cap = min(fmax, C.STRONG_TREND_CAP_NO_FULL)        # §13/§14 满仓资格失效 → 上限 85
        elif rg == 'STRONG_TREND' and elig:
            cap = 100.0

        prev = out['position'].iloc[i - 1] if i > 0 else 0.0
        up = prev + (steps['up_strong'] if (elig and rg in ('TREND', 'STRONG_TREND')) else steps['up'])
        down = prev - (steps['down_risk'] if ro_cnt >= 2 else steps['down'])
        if hp:                                                 # §18 指数假弱：不允许因单日下跌大幅减仓
            down = prev
        pos = min(max(raw, down), up)
        pos = min(pos, cap)
        pos = max(pos, 0.0)
        pos = quantize_position(pos)
        out.loc[d, 'position'] = pos
        out.loc[d, 'raw_target'] = raw
        out.loc[d, 'band_min'] = fmin
        out.loc[d, 'band_target'] = ftarget
        out.loc[d, 'band_max'] = fmax
        out.loc[d, 'cap'] = cap
        out.loc[d, 'quality'] = qq
        out.loc[d, 'full_exposure_eligible'] = elig
        out.loc[d, 'daily_cap_applied'] = cap < fmax
    return out


# =========================================================
# 汇总：每日 Gate 输出
# =========================================================
def run_gate(feat: pd.DataFrame, bands: dict = None, th: dict = None,
             steps: dict = None) -> pd.DataFrame:
    """feat → 每日 Gate 完整输出（§28 每日核心输出字段）"""
    reg = classify_regime(feat, th=th)
    full = full_exposure_conditions(feat)
    pos = position_walk(feat, reg, full, bands=bands, steps=steps)
    out = pd.concat([feat, reg, full, pos], axis=1)
    out = out.loc[:, ~out.columns.duplicated()]        # full_exposure_eligible 两侧同名
    out['position_min'] = out['band_min']
    out['position_target'] = out['band_target']
    out['position_max'] = out['cap']
    out['reason'] = out.apply(_reason, axis=1)
    return out


def _reason(r) -> str:
    bits = []
    bits.append(f"{r['regime']}")
    if r.get('INDEX_BREADTH_DIVERGENCE'):
        bits.append('指数背离(不加仓)')
    if r.get('HEALTHY_PULLBACK'):
        bits.append('健康回调(不减仓)')
    if r.get('riskoff_count', 0) >= 2:
        bits.append(f"Risk-Off={int(r['riskoff_count'])}")
    if r.get('full_exposure_eligible'):
        bits.append('满仓资格=YES')
    return ' | '.join(bits)


# =========================================================
# §21 组合集中度校验（不选股，只给上限）
# =========================================================
def concentration_limits(position_pct: float, conc: dict = None) -> dict:
    """在给定总仓位下，给出单股/单主题/单策略的暴露上限（%总资产）"""
    conc = conc or C.CONCENTRATION
    return {
        'position_gate': round(position_pct, 1),
        'single_stock_max': round(min(conc['single_stock_max'], position_pct), 1),
        'theme_exposure_max': round(min(conc['theme_exposure_max'], position_pct), 1),
        'strategy_exposure_max': round(min(conc['strategy_exposure_max'], position_pct), 1),
    }
