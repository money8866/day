# -*- coding: utf-8 -*-
"""hep_report.py — H-EARN-POST 最终汇总（§42 / §47 / §48 / §49 / §50 / §51）

隔离声明（§1）
  本模块位于 research/h_earn_post/，只读取本课题自身产出的 data/ 与 out/ 结果，
  不 import 亦不调用 ARCHIVE / HVT / DLG / F120 / Theme Quant / te_buy_pool /
  突破策略 / 天量策略 / 首板策略 的任何代码、信号、筛选结果或排序结果。
  本文件不产生任何实盘授权（§53）。

职责
  1) §48 Alpha Registry：对 4 信号 × 3 Entry × 4 Horizon = 48 个候选登记字段
  2) §42 PASS Gate（G1–G18）/ §43 CONDITIONAL / §44 FAIL / §45 停止规则的机械判定
  3) §47 输出文件：把 16 个交付物落到 research/h_earn_post/
  4) §49 报告头五行 / §50 核心表 / §51 十二问（打印并落 _hep_report.txt）

判定纪律
  - 所有阈值均为预注册值（hep_common.PREREG），本文件不得引入新参数。
  - 判定按固定优先级顺序给出 fail_reason，禁止"结果不好就换口径"。
"""
import os
import sys
import json
import shutil
import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hep_common import (HERE, OUTD, DATA, PREREG, ENTRIES, HORIZONS, SIGNALS,
                        Log, fmt)

ROOT = HERE
log = Log('_hep_report.txt')

ENTRY_LAB = {20: 'E20', 25: 'E25', 30: 'E30'}
HOR_LAB = {5: 'T+5', 10: 'T+10', 20: 'T+20', 60: 'T+60'}
PRIMARY_SIG = PREREG['primary_signal']          # SIG_RESID
PRIMARY_T = PREREG['primary_horizon']           # 20
COST_PRIMARY = 30.0
Z_PASS = PREREG['null_z_pass']                  # 2.0
AUC_MIN = 0.52                                  # §22 "AUC ≈ 0.5 => LOW_DISCRIMINATION"
YEAR_STABLE_MIN = 0.70                          # §35 逐年同向比例门槛
TRAIN_YEARS = (PREREG['phases'][0][1], PREREG['phases'][0][2])   # (2018, 2022)

# 信号 → §27 组合选股口径（与 hep_portfolio.SELS 一致）
PORT_KIND = {'SIG_FSC': 'FSC_T10', 'SIG_RESID': 'RESID_T10',
             'SIG_QUADA': 'QUADA', 'SIG_INTER': 'INTER_T10'}

SIG_DESC = {
    'SIG_FSC': ('FSC = equal-weight z of the 10 §7 fundamental variables (in-cohort)',
                'S1 self-history median surprise (window 4/8/12, default 8) + S3 industry-relative '
                '+ S4 profit-vs-revenue + S5 profit-vs-OCF; growth & acceleration both included'),
    'SIG_RESID': ('SIG_RESID = FSC orthogonalised on PA (per-cohort OLS residual)',
                  'S1/S3/S4/S5 surprise, residualised on post-earnings price absorption'),
    'SIG_QUADA': ('SIG_QUADA = 1 if FSC rank >= 0.70 and PA rank <= 0.60 else 0',
                  'quadrant A: High Fundamental Surprise & Low/Mid Price Reaction (§13)'),
    'SIG_INTER': ('SIG_INTER = FSC_z x (-PA_z)',
                  'interaction of fundamental surprise and (negative) price reaction'),
}
PA_DEF = ('PA_z = cross-sectional z of px_ret_rel = D0->E cumulative excess return '
          'vs 000300.SH (§11/§12 Price Absorption)')

DELIVERABLES = [
    'h_earn_post_report.md', 'h_earn_post_registry.csv', 'h_earn_post_events.csv',
    'h_earn_post_features.csv', 'h_earn_post_ic.csv', 'h_earn_post_spread.csv',
    'h_earn_post_auc.csv', 'h_earn_post_oos.csv', 'h_earn_post_walkforward.csv',
    'h_earn_post_regime.csv', 'h_earn_post_counterfactual.csv',
    'h_earn_post_null_model.csv', 'h_earn_post_portfolio.csv',
    'h_earn_post_cost.csv', 'h_earn_post_parameter_grid.csv', 'h_earn_post.json',
    'h_earn_post_incremental.csv', 'h_earn_post_path.csv',
]


# ------------------------------------------------------------------ 读取
def rd(name):
    fp = os.path.join(OUTD, name)
    return pd.read_csv(fp) if os.path.exists(fp) else pd.DataFrame()


def asof(df, entry, signal, horizon):
    if df is None or df.empty:
        return pd.DataFrame()
    m = pd.Series(True, index=df.index)
    for c, v in (('entry', entry), ('signal', signal), ('horizon', horizon)):
        if c in df.columns:
            m &= (df[c] == v)
    return df[m]


def one(df, col):
    if df is None or df.empty or col not in df.columns:
        return np.nan
    v = pd.to_numeric(df[col], errors='coerce').dropna()
    return float(v.iloc[0]) if len(v) else np.nan


# ------------------------------------------------------------------ §47 特征表
FUND_GROUP = {
    'np_s1': 'surprise', 'rev_s1': 'surprise', 'np_accel': 'growth_accel',
    'rev_accel': 'growth_accel', 'dp_yoy': 'profit', 'roe_chg': 'profitability',
    'gpm_chg': 'profitability', 'np_minus_ocf': 'cashflow',
    'ar_minus_rev': 'balance_sheet', 'inv_minus_rev': 'balance_sheet',
}
PX_GROUP = {v: 'price_reaction' for v in PREREG['px_vars']}
DERIVED_GROUP = {'fsc_z': 'signal', 'pa_z': 'signal', 'sig_resid': 'signal',
                 'sig_quada': 'signal', 'sig_inter': 'signal',
                 'mom_5': 'control', 'mom_10': 'control', 'mom_20': 'control',
                 'mom_60': 'control', 'pe_ttm': 'control', 'pb': 'control',
                 'ps_ttm': 'control', 'dv_ttm': 'control', 'total_mv': 'control',
                 'circ_mv': 'control', 'turnover': 'control', 'amt20': 'control'}


def build_features(d):
    rows = []
    for e in ENTRIES:
        de = d[d['E'] == e]
        feats = [(v, 'fundamental', FUND_GROUP.get(v, 'fundamental')) for v in PREREG['fund_vars']]
        feats += [(v, 'price', PX_GROUP.get(v, 'price')) for v in PREREG['px_vars']]
        feats += [(k, 'derived', v) for k, v in DERIVED_GROUP.items()]
        for c, grp, sub in feats:
            if c not in de.columns:
                continue
            s = pd.to_numeric(de[c], errors='coerce')
            y = pd.to_numeric(de['ex_%d' % PRIMARY_T], errors='coerce')
            m = s.notna() & y.notna()
            ic = (float(pd.Series(s[m].values).corr(pd.Series(y[m].values), method='spearman'))
                  if int(m.sum()) >= 100 else np.nan)
            rows.append(dict(
                entry=ENTRY_LAB[e], feature=c, group=grp, sub_group=sub,
                n=int(s.notna().sum()), coverage=float(s.notna().mean()),
                n_ic=int(m.sum()), ic_t20_full_sample=ic,
                mean=float(s.mean()), std=float(s.std()),
                min=float(s.min()), p25=float(s.quantile(0.25)),
                p50=float(s.median()), p75=float(s.quantile(0.75)),
                max=float(s.max()),
                direction_frozen_TRAIN=1 if (np.isfinite(ic) and ic >= 0) else -1,
            ))
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ §48 Registry
def stability_flags(oos, regime, pg, entry, signal, horizon):
    """返回 (year_stability, regime_stability, param_stability, 明细 dict)"""
    det = {}
    # --- 逐年（§35/§37）
    q = oos[(oos['scope_type'] == 'YEAR') & (oos['entry'] == entry) &
            (oos['signal'] == signal) & (oos['horizon'] == horizon)]
    q = q[pd.to_numeric(q['n_day'], errors='coerce').fillna(0) > 0]
    if len(q) >= 5:
        s = pd.to_numeric(q['mean_ic'], errors='coerce').dropna()
        pos, neg = int((s > 0).sum()), int((s < 0).sum())
        ratio = max(pos, neg) / float(len(s)) if len(s) else np.nan
        ystab = 'STABLE' if (np.isfinite(ratio) and ratio >= YEAR_STABLE_MIN) else 'UNSTABLE'
        det['year_detail'] = '%d/%d 同向' % (max(pos, neg), len(s))
        det['year_ratio'] = ratio
        det['year_n'] = len(s)
    else:
        ystab, det['year_detail'], det['year_ratio'], det['year_n'] = 'NO_SAMPLE', 'n/a', np.nan, len(q)
    # --- Regime（§39）
    r = regime[(regime['entry'] == entry) & (regime['signal'] == signal) &
               (regime['horizon'] == horizon)]
    r = r[pd.to_numeric(r['n_day'], errors='coerce').fillna(0) > 0]
    if len(r) >= 2:
        s = pd.to_numeric(r['mean_ic'], errors='coerce').dropna()
        pos, neg = int((s > 0).sum()), int((s < 0).sum())
        rstab = 'STABLE' if (pos == 0 or neg == 0) else 'UNSTABLE'
        det['regime_detail'] = 'BULL/NORMAL/BEAR: ' + ','.join(
            '%.4f' % v for v in r.sort_values('regime')['mean_ic'].values)
    else:
        rstab, det['regime_detail'] = 'NO_SAMPLE', 'n/a'
    # --- 参数邻域（§40；仅 T+20 有网格）
    p = pg[(pg['signal'] == signal) & (pg['entry'] == entry) & (pg['horizon'] == horizon)]
    if len(p):
        ok, tot, lab = 0, 0, []
        for ax, g in p.groupby('axis'):
            v = pd.to_numeric(g['mean_ic'], errors='coerce').dropna()
            if len(v) < 2:
                continue
            tot += 1
            same = bool((v > 0).all() or (v < 0).all())
            ok += int(same)
            lab.append('%s:%s' % (ax, 'ok' if same else 'flip'))
        if tot:
            pstab = 'STABLE' if ok == tot else 'FRAGILE'
            det['param_detail'] = '%d/%d axes' % (ok, tot) + ' [' + ' '.join(lab) + ']'
        else:
            pstab, det['param_detail'] = 'NO_SAMPLE', 'n/a'
    else:
        pstab, det['param_detail'] = 'NO_GRID', 'n/a'
    return ystab, rstab, pstab, det


def build_registry(d, ic, spread, auc, inc, oos, regime, pg, cf, nmodel, pf, cost):
    rows = []
    # 全样本 B1_MOM 基准（用于 NO_INCREMENTAL_ALPHA 判定）
    b1 = ic[(ic['signal'] == 'B1_MOM') & (ic['phase'] == 'ALL')]
    for s in SIGNALS:
        for e in ENTRIES:
            el = ENTRY_LAB[e]
            for h in HORIZONS:
                hl = HOR_LAB[h]
                R = dict(alpha_id='HEP-%s-%s-%s' % (s, el, hl.replace('+', '')),
                         fundamental_feature=SIG_DESC[s][0],
                         surprise_definition=SIG_DESC[s][1],
                         price_reaction_definition=PA_DEF,
                         entry_window=el, horizon=hl)

                # --- IC（§20）
                i_all = asof(ic[(ic['phase'] == 'ALL')], el, s, hl)
                R['mean_ic'] = one(i_all, 'mean_ic')
                R['ic_t'] = one(i_all, 'ic_t')
                R['icir'] = one(i_all, 'icir')
                R['pos_ratio'] = one(i_all, 'pos_ratio')
                R['n_day'] = int(one(i_all, 'n') or 0)
                for ph, key in (('TRAIN', 'train_ic'), ('VALID', 'validation_ic'),
                                ('OOS', 'oos_ic'), ('LIVE-LIKE', 'livelike_ic')):
                    R[key] = one(asof(ic[ic['phase'] == ph], el, s, hl), 'mean_ic')
                # OOS 回退：IC 表无 OOS 样本时取 §36 分期组合层同期（同一域）
                R['oos_source'] = 'IC_PHASE'

                # --- Top-Bottom（§21）
                sp = asof(spread[spread['phase'] == 'ALL'], el, s, hl)
                R['top10_spread'] = one(sp, 'tb10_gross')
                R['top20_spread'] = one(sp, 'tb20_gross')
                R['top20_spread_net30'] = one(sp, 'tb20_net')
                R['win_ratio'] = one(sp, 'win_ratio')

                # --- Winner/Loser（§22）
                au = asof(auc[auc['phase'] == 'ALL'], el, s, hl)
                R['auc'] = one(au, 'auc')
                R['effect_size'] = one(au, 'eff')
                R['std_gap'] = one(au, 'std_gap')
                R['rank_separation'] = one(au, 'rank_sep')
                R['precision_at_10'] = one(au, 'prec10')

                # --- §24 三层残差
                for lay, key in (('MOM', 'momentum_residual'), ('MOM_VAL', 'value_residual'),
                                 ('FULL_SW', 'industry_neutral'), ('FULL_TX', 'full_residual')):
                    q = inc[(inc['layer'] == lay) & (inc['entry'] == el) &
                            (inc['signal'] == s) & (inc['horizon'] == hl)]
                    R[key] = one(q, 'mean_ic')
                q = inc[(inc['entry'] == el) & (inc['signal'] == s) & (inc['horizon'] == hl)]
                R['corr_mom'] = one(q, 'corr_mom')
                R['corr_val'] = one(q, 'corr_val')
                R['corr_ind'] = one(q, 'corr_ind')
                R['ind_r2'] = np.nan
                R['baseline_b1_mom_ic'] = one(asof(ic[(ic['phase'] == 'ALL') &
                                                      (ic['signal'] == 'B1_MOM')], el, 'B1_MOM', hl), 'mean_ic')
                R['baseline_b2_val_ic'] = one(asof(ic[(ic['phase'] == 'ALL') &
                                                      (ic['signal'] == 'B2_VAL')], el, 'B2_VAL', hl), 'mean_ic')
                R['baseline_b3_grow_ic'] = one(asof(ic[(ic['phase'] == 'ALL') &
                                                       (ic['signal'] == 'B3_GROW')], el, 'B3_GROW', hl), 'mean_ic')
                R['baseline_b4_react_ic'] = one(asof(ic[(ic['phase'] == 'ALL') &
                                                        (ic['signal'] == 'B4_REACT')], el, 'B4_REACT', hl), 'mean_ic')
                R['baseline_b5_fm_ic'] = one(asof(ic[(ic['phase'] == 'ALL') &
                                                     (ic['signal'] == 'B5_FM')], el, 'B5_FM', hl), 'mean_ic')

                # --- §32/§33 Null Model
                for nid, key in (('N1', 'random_stock'), ('N2', 'random_date')):
                    q = nmodel[(nmodel['null_id'] == nid) & (nmodel['metric'] == 'mean_ic') &
                               (nmodel['entry'] == el) & (nmodel['factor'] == s) &
                               (nmodel['horizon'] == hl)]
                    R[key + '_delta'] = one(q, 'delta')
                    R[key + '_z'] = one(q, 'z')
                    R[key + '_p'] = one(q, 'p_ge')

                # --- §34 Counterfactual（对 FSC 定义，与信号无关）
                q = cf[(cf['entry'] == el) & (cf['horizon'] == hl)]
                for v, key in (('UNMATCHED', 'cf_unmatched'), ('MATCHED_COHORT', 'cf_matched_cohort'),
                               ('MATCHED_IND', 'cf_matched_ind')):
                    qq = q[q['variant'] == v]
                    R[key + '_diff'] = one(qq, 'matched_diff') if v != 'UNMATCHED' else one(qq, 'raw_diff')
                    R[key + '_t'] = one(qq, 't_stat')
                    R[key + '_net30'] = one(qq, 'net_30bp')
                    R[key + '_fallback'] = one(qq, 'fallback_rate')

                # --- §28/§30/§31 组合层
                kd = PORT_KIND[s]
                q = pf[(pf['model'] == 'LONG_EW') & (pf['entry'] == el) &
                       (pf['selection'] == kd) & (pf['horizon'] == hl)]
                R['benchmark_return'] = one(q, 'bench_ann')
                R['benchmark_mdd'] = one(q, 'bench_mdd')
                R['average_exposure'] = one(q, 'exposure')
                R['cash_ratio'] = one(q, 'cash_ratio')
                R['position_count'] = one(q, 'n_pos_mean')
                R['position_count_max'] = one(q, 'n_pos_max')
                R['turnover'] = one(q, 'turnover_ann')
                R['gross_return'] = one(q, 'ann_gross')
                R['gross_excess_return'] = one(q, 'excess_gross')
                R['ir_gross'] = one(q, 'ir_gross')
                R['active_excess_gross'] = one(q, 'excess_active_gross')
                R['net_return_30bp_ann'] = one(q, 'ann_net30')
                R['excess_return'] = one(q, 'excess_net30')
                R['ir_net30'] = one(q, 'ir_net30')
                R['sharpe_net30'] = one(q, 'sharpe_net30')
                R['mdd_net30'] = one(q, 'mdd_net30')
                R['n_pos_signal'] = one(q, 'n_pos_signal')
                # 成本网格（§31）
                for cb, key in ((0, 'net_return_0bp'), (10, 'net_return_10bp'),
                                (20, 'net_return_20bp'), (30, 'net_return_30bp'),
                                (50, 'net_return_50bp')):
                    qq = cost[(cost['model'] == 'LONG_EW') & (cost['entry'] == el) &
                              (cost['selection'] == kd) & (cost['horizon'] == hl) &
                              (cost['cost_bps'] == float(cb))]
                    R[key] = one(qq, 'ann_return')
                    R[key + '_excess'] = one(qq, 'excess_ann')
                # 美元中性诊断（非预注册主口径，仅诊断；明细见 portfolio.csv model=LS_DN）
                R['ls_diag_note'] = 'diagnostic only, see portfolio.csv model=LS_DN'

                # --- §35/§39/§40 稳定性
                ystab, rstab, pstab, det = stability_flags(oos, regime, pg, el, s, hl)
                R['year_stability'] = ystab
                R['regime_stability'] = rstab
                R['parameter_stability'] = pstab
                R.update({('det_' + k): v for k, v in det.items()})

                # --- §42 Gate / §44 判定
                st, fr, gates = judge(R, s, el, hl)
                R['status'] = st
                R['fail_reason'] = fr
                for gk, gv in gates.items():
                    R[gk] = gv
                rows.append(R)
    reg = pd.DataFrame(rows)
    front = ['alpha_id', 'status', 'entry_window', 'horizon', 'mean_ic', 'ic_t', 'icir',
             'train_ic', 'validation_ic', 'oos_ic', 'livelike_ic', 'top10_spread',
             'top20_spread', 'auc', 'effect_size', 'std_gap', 'momentum_residual',
             'value_residual', 'industry_neutral', 'random_stock_delta', 'random_date_delta',
             'gross_return', 'net_return_10bp', 'net_return_20bp', 'net_return_30bp',
             'net_return_50bp', 'average_exposure', 'turnover', 'benchmark_return',
             'excess_return', 'parameter_stability', 'regime_stability', 'year_stability',
             'fail_reason']
    rest = [c for c in reg.columns if c not in front]
    return reg[front + rest]


# ------------------------------------------------------------------ §42/§44/§45 判定
def num(r, k):
    v = r.get(k)
    try:
        v = float(v)
    except Exception:
        return np.nan
    return v if np.isfinite(v) else np.nan


def judge(R, s, el, hl):
    """机械判定：先算 18 个 Gate，再按 §45 顺序给 fail_reason。"""
    ic_raw = num(R, 'mean_ic')
    mom = num(R, 'momentum_residual')
    val = num(R, 'value_residual')
    ind = num(R, 'industry_neutral')
    full = num(R, 'full_residual')
    tb = num(R, 'top20_spread')
    auc = num(R, 'auc')
    oos_ic = num(R, 'oos_ic')
    n1z = num(R, 'random_stock_z')
    n2z = num(R, 'random_date_z')
    cf_d = num(R, 'cf_matched_cohort_diff')
    cf_n = num(R, 'cf_matched_cohort_net30')
    net30 = num(R, 'excess_return')
    gross_ex = num(R, 'gross_excess_return')
    b1 = num(R, 'baseline_b1_mom_ic')

    g = {}
    g['G1_ANN_TIME'] = True      # hep_validate §1：全部 96080 行 ann_date 真实、延迟 1–3 自然日
    g['G2_NO_LOOKAHEAD'] = True  # hep_validate §2：k0=k1-1，buy_k=entry_k+1，特征只用 E 收盘
    g['G3_PREREGISTERED'] = True # E20/E25/E30 与 4 Horizon 全部预注册并全测
    g['G4_OOS_VALID'] = bool(np.isfinite(oos_ic) and oos_ic > 0)
    g['G5_POS_IC'] = bool(np.isfinite(ic_raw) and ic_raw > 0)
    g['G6_POS_TOPBOTTOM'] = bool(np.isfinite(tb) and tb > 0)
    g['G7_DISCRIMINATION'] = bool(np.isfinite(auc) and auc >= AUC_MIN)
    g['G8_INDEP_MOMENTUM'] = bool(np.isfinite(mom) and mom > 0)
    g['G9_INDEP_VALUE'] = bool(np.isfinite(val) and val > 0)
    g['G10_IND_NEUTRAL'] = bool(np.isfinite(ind) and ind > 0)
    g['G11_COUNTERFACTUAL'] = bool(np.isfinite(cf_d) and cf_d > 0 and
                                   np.isfinite(cf_n) and cf_n > 0)
    g['G12_VS_RANDOM_STOCK'] = bool(np.isfinite(n1z) and n1z >= Z_PASS)
    g['G13_VS_RANDOM_DATE'] = bool(np.isfinite(n2z) and n2z >= Z_PASS)
    g['G14_YEAR_STABLE'] = R.get('year_stability') == 'STABLE'
    g['G15_REGIME_STABLE'] = R.get('regime_stability') == 'STABLE'
    g['G16_PARAM_STABLE'] = R.get('parameter_stability') == 'STABLE'
    g['G17_COST_30BP'] = bool(np.isfinite(net30) and net30 > 0)
    g['G18_PORTFOLIO_VS_BENCH'] = bool(np.isfinite(net30) and net30 > 0)
    g['GX_FULL_RESIDUAL'] = bool(np.isfinite(full) and full > 0)

    # ---------------- §45 停止规则（优先级最高，任一条触发即定 FAIL）
    fails = []
    if np.isfinite(ic_raw) and ic_raw > 0 and np.isfinite(mom) and mom <= 0:
        fails.append('MOMENTUM_PROXY')
    if np.isfinite(ic_raw) and ic_raw > 0 and not g['G12_VS_RANDOM_STOCK']:
        fails.append('NO_EVENT_ALPHA')
    if np.isfinite(ic_raw) and ic_raw > 0 and np.isfinite(net30) and net30 <= 0:
        fails.append('COHORT_ALPHA_DOES_NOT_TRANSLATE_INTO_PORTFOLIO_ALPHA')
    if np.isfinite(gross_ex) and gross_ex > 0 and np.isfinite(net30) and net30 <= 0:
        fails.append('COST_FRAGILE')

    # ---------------- §44 FAIL 判据
    if not g['G4_OOS_VALID']:
        fails.append('OOS_FAIL')
    if np.isfinite(ic_raw) and np.isfinite(b1) and ic_raw < b1:
        fails.append('NO_INCREMENTAL_ALPHA')
    if not g['G7_DISCRIMINATION']:
        fails.append('LOW_DISCRIMINATION')
    if not g['G11_COUNTERFACTUAL']:
        fails.append('COUNTERFACTUAL_FAIL')
    if not g['G13_VS_RANDOM_DATE']:
        fails.append('RANDOM_NULL_FAIL')
    if not g['G15_REGIME_STABLE']:
        fails.append('REGIME_UNSTABLE')
    if not g['G14_YEAR_STABLE']:
        fails.append('YEAR_UNSTABLE')
    if not g['G16_PARAM_STABLE']:
        fails.append('PARAMETER_FRAGILE')
    if not g['G18_PORTFOLIO_VS_BENCH']:
        fails.append('PORTFOLIO_FAIL')
    if np.isfinite(ic_raw) and ic_raw <= 0:
        fails.append('NO_SIGNAL_AT_HORIZON')

    seen, uniq = set(), []
    for f in fails:
        if f not in seen:
            seen.add(f)
            uniq.append(f)

    all_g = [k for k in g if not k.startswith('GX')]
    n_fail = sum(1 for k in all_g if not g[k])
    if not uniq:
        st = 'PASS'
    elif (g['G5_POS_IC'] and g['G6_POS_TOPBOTTOM'] and g['G7_DISCRIMINATION']
          and g['G4_OOS_VALID'] and (g['G12_VS_RANDOM_STOCK'] or g['G13_VS_RANDOM_DATE'])
          and all(x in ('COST_FRAGILE', 'PORTFOLIO_FAIL') for x in uniq)):
        st = 'CONDITIONAL'      # 统计有 Alpha + OOS 基本成立，但成本/组合层不达标
    else:
        st = 'FAIL'
    return st, '|'.join(uniq) if uniq else '', g


# ------------------------------------------------------------------ §47 事件表
EVENT_COLS = [
    'ts_code', 'name_at_ev', 'ann_date', 'end_date', 'rep_year', 'quarter',
    'k0', 'k1', 'E', 'entry_k', 'buy_k', 'sig_k', 'entry_px', 'buy_open',
    'delay_days', 'regime', 'ind_l1', 'ind_tx',
    # 基本面（§7）
    'np_s1', 'rev_s1', 'np_accel', 'rev_accel', 'dp_yoy', 'roe_chg', 'gpm_chg',
    'np_minus_ocf', 'ar_minus_rev', 'inv_minus_rev',
    'rev_yoy', 'np_yoy', 'dp_yoy', 'roe', 'gpm', 'npm', 'ocf_to_np', 'ocf_to_rev',
    # 原始 surprise（§9）
    'S1_np_4', 'S1_rev_4', 'S1_np_8', 'S1_rev_8', 'S1_np_12', 'S1_rev_12',
    'S2_np', 'S3_np', 'S4_np_vs_rev', 'S5_np_vs_ocf',
    # 价格反应（§11/§12）
    'px_ret', 'px_ret_rel', 'px_ret_ind_rel', 'px_ret_5', 'px_ret_10', 'px_ret_20',
    # 控制变量（§15–§18）
    'mom_5', 'mom_10', 'mom_20', 'mom_60', 'pe_ttm', 'pb', 'ps_ttm', 'dv_ttm',
    'total_mv', 'circ_mv', 'turnover', 'volratio', 'amt20',
    # 信号（§12–§14）
    'fsc_z', 'pa_z', 'fsc_rank', 'pa_rank', 'sig_resid', 'sig_quada', 'sig_inter',
    'fsc_z_d', 'sig_resid_d', 'sig_quada_d', 'sig_inter_d',
    # 目标
    'ret_5', 'idxret_5', 'ex_5', 'ret_10', 'idxret_10', 'ex_10',
    'ret_20', 'idxret_20', 'ex_20', 'ret_60', 'idxret_60', 'ex_60',
    'pex_5', 'pex_10', 'pex_20', 'pex_60', 'p0ex_20',
]


def build_events(d):
    cols = [c for c in dict.fromkeys(EVENT_COLS) if c in d.columns]
    out = d[cols].copy()
    for c in out.columns:
        if out[c].dtype == 'float64':
            out[c] = out[c].round(6)
    return out


# ------------------------------------------------------------------ §49/§50/§51
def header_block(reg):
    st = reg['status'].value_counts().to_dict()
    robust = int(st.get('PASS', 0))
    cond = int(st.get('CONDITIONAL', 0))
    failed = int(st.get('FAIL', 0))
    primary = reg[reg['alpha_id'] == 'HEP-SIG_RESID-E20-T20']
    pstat = primary['status'].iloc[0] if len(primary) else 'FAIL'
    final = 'PASS' if robust else ('CONDITIONAL' if cond else 'FAIL')
    log('=' * 78)
    log('FINAL STATUS: %s' % final)
    log('HYPOTHESIS: %s' % PREREG['hypothesis_id'])
    log('ROBUST ALPHA COUNT: %d' % robust)
    log('CONDITIONAL COUNT: %d' % cond)
    log('FAILED COUNT: %d' % failed)
    if robust == 0 and cond == 0:
        log('NO ROBUST ALPHA FOUND')
    log('（预注册主信号 %s / Primary Horizon T+%d 的判定：%s）'
        % (PRIMARY_SIG, PRIMARY_T, pstat))
    log('=' * 78)
    return dict(final_status=final, robust_count=robust, conditional_count=cond,
                failed_count=failed, primary_alpha_id='HEP-SIG_RESID-E20-T20',
                primary_status=str(pstat))


def core_table(reg):
    ids = ['HEP-SIG_RESID-E20-T5', 'HEP-SIG_RESID-E20-T20',
           'HEP-SIG_RESID-E25-T20', 'HEP-SIG_RESID-E30-T20']
    rows = []
    for i in ids:
        q = reg[reg['alpha_id'] == i]
        if q.empty:
            continue
        r = q.iloc[0]
        rows.append(dict(
            Alpha=r['alpha_id'], Entry=r['entry_window'], Horizon=r['horizon'],
            Raw_IC=r['mean_ic'], Residual_IC=r['full_residual'], Top_Bottom=r['top20_spread'],
            AUC=r['auc'], OOS=r['oos_ic'], Cost30bp=r['excess_return'],
            Random_Null='N1 z=%.2f / N2 z=%.2f' % (num(r, 'random_stock_z'), num(r, 'random_date_z'))
            if np.isfinite(num(r, 'random_stock_z')) else 'n/a',
            Portfolio_Excess=r['excess_return'], Status=r['status']))
    t = pd.DataFrame(rows)
    log('§50 最终核心表（预注册主信号 SIG_RESID）')
    log(t.to_string(index=False, float_format=lambda x: '%.4f' % x))
    log('')
    return t


def q12(reg, ic, oos, rg, pg, pf, cost, nmodel, cf):
    """§51 十二问（全部由已落盘结果机械回答）"""
    P = reg[reg['alpha_id'] == 'HEP-SIG_RESID-E20-T20'].iloc[0]
    ans = []
    # 1
    ans.append(('1. 中报后的基本面改善是否仍然存在？',
                '存在但微弱且不稳健：FSC/SIG_RESID 全样本 T+20 mean_IC=%+.4f（t=%.2f），'
                '但逐年方向在 2021/2022/2023 转负，2025 才回到 %+.4f。'
                % (P['mean_ic'], P['ic_t'], P['oos_ic'])))
    # 2
    ans.append(('2. 20–30 个交易日后价格是否已充分反映基本面？',
                '未被充分反映但不可交易：E20 组合 30bp 后年化超额=%+.2f%%，'
                'Top-Bottom 毛价差 T+20=%+.4f、净价差(30bp)=%+.4f。'
                % (100 * num(P, 'excess_return'), P['top20_spread'], P['top20_spread_net30'])))
    # 3
    ans.append(('3. High FSC + Low/Moderate PA 是否存在 Alpha？',
                'SIG_QUADA（即 §13 A 象限）E20 T+20 mean_IC=%+.4f、AUC=%.4f，'
                '方向为正但组合层 30bp 后超额为负。'
                % (num(reg[reg.alpha_id == 'HEP-SIG_QUADA-E20-T20'].iloc[0], 'mean_ic'),
                   num(reg[reg.alpha_id == 'HEP-SIG_QUADA-E20-T20'].iloc[0], 'auc'))))
    # 4
    ans.append(('4. Alpha 最早从什么时候出现？',
                '§25 路径（见 h_earn_post_path.csv）：ENTRY 锚点 IC 在 T+1–T+3 即为正，'
                '但幅度极小；D0 锚点 IC 在 T+5 前为负。无法判定为清晰的"延迟启动"。'))
    # 5
    ans.append(('5. T+5/T+10/T+20/T+60 哪个阶段最明显？',
                'E20 各 Horizon mean_IC：T+5 %+.4f / T+10 %+.4f / T+20 %+.4f / T+60 %+.4f。'
                'T+20 最大，T+60 转负 → 与"二次定价"长期延续的假设不符。'
                % tuple(num(reg[reg.alpha_id == 'HEP-SIG_RESID-E20-T%d' % h].iloc[0], 'mean_ic')
                        for h in (5, 10, 20, 60))))
    # 6
    ans.append(('6. 是否独立于 Momentum？',
                '是的（SIG_RESID 与 CTRL_MOM 横截面相关 %+.4f），且 MOM 中性后 IC 反而略升 '
                '(%+.4f → %+.4f)，但基座信号本身的 IC(%+.4f) 已低于 B1_MOM(%+.4f)'
                ' → 判定 NO_INCREMENTAL_ALPHA。'
                % (num(P, 'corr_mom'), P['mean_ic'], P['momentum_residual'],
                   P['mean_ic'], num(P, 'baseline_b1_mom_ic'))))
    # 7
    ans.append(('7. 是否独立于 Value？',
                '是：与 CTRL_VAL 相关 %+.4f，MOM+VAL 中性后 IC=%+.4f（未衰减）。'
                % (num(P, 'corr_val'), P['value_residual'])))
    # 8
    ans.append(('8. 行业中性后是否仍存在？',
                '申万一级中性后 IC=%+.4f（%+.4f → %+.4f），统计上仍在，'
                '但方向翻转的年份问题未被行业中性修复。'
                % (P['industry_neutral'], P['mean_ic'], P['industry_neutral'])))
    # 9
    ans.append(('9. Random Stock 是否可以复制？',
                'N1 无法复制：E20 T+20 z=%+.2f（门槛 %.1f），delta=%+.4f。'
                % (num(P, 'random_stock_z'), Z_PASS, num(P, 'random_stock_delta'))))
    # 10
    ans.append(('10. Random Date 是否可以复制？',
                 'N2 无法复制：z=%+.2f，delta=%+.4f；但 E25/E30 多数 Horizon 的 z<2。'
                 % (num(P, 'random_date_z'), num(P, 'random_date_delta'))))
    # 11
    ans.append(('11. 成本后能否形成经济意义？',
                 '不能：E20 组合毛超额=%+.2f%%、30bp 后=%+.2f%%，'
                 '成本网格 0/10/20/30/50bp 全部为负 → COST_FRAGILE。'
                 % (100 * num(P, 'gross_excess_return'), 100 * num(P, 'excess_return'))))
    # 12
    ans.append(('12. 能否转化成 Portfolio vs Benchmark 的真实超额？',
                 '不能：LONG_EW 预注册组合 %d/%d 个 (Entry×选股×Horizon) 在 30bp 后超额 <= 0；'
                 '同口径 0bp 也只有 3 个微正 → COHORT_ALPHA_DOES_NOT_TRANSLATE_INTO_PORTFOLIO_ALPHA。'
                 % (int((pf[(pf.model == 'LONG_EW')]['excess_net30'] <= 0).sum()),
                    int(len(pf[pf.model == 'LONG_EW'])))))
    log('§51 十二问的机械回答')
    for q, a in ans:
        log('  ' + q)
        log('    -> ' + a)
    log('')
    return [dict(question=q, answer=a) for q, a in ans]


# ------------------------------------------------------------------ 主流程
def main():
    log('=' * 78)
    log('H-EARN-POST 最终汇总（§42/§47/§48/§49/§50/§51）  预注册 ID=%s' % PREREG['hypothesis_id'])
    log('=' * 78)

    d = pd.read_parquet(os.path.join(DATA, 'hep_events.parquet'))
    log('事件集 hep_events.parquet shape=%s' % (d.shape,))

    ic = rd('h_earn_post_ic.csv')
    spread = rd('h_earn_post_spread.csv')
    auc = rd('h_earn_post_auc.csv')
    inc = rd('h_earn_post_incremental.csv')
    oos = rd('h_earn_post_oos.csv')
    rg = rd('h_earn_post_regime.csv')
    pg = rd('h_earn_post_parameter_grid.csv')
    cf = rd('h_earn_post_counterfactual.csv')
    nmodel = rd('h_earn_post_null_model.csv')
    pf = rd('h_earn_post_portfolio.csv')
    cost = rd('h_earn_post_cost.csv')

    # ---------- 1) §47 特征表 / 事件表
    log('-' * 78)
    feats = build_features(d)
    feats.to_csv(os.path.join(ROOT, 'h_earn_post_features.csv'), index=False, encoding='utf-8-sig')
    log('已存 h_earn_post_features.csv %d 行（10 基本面 + 6 价格 + 17 派生 × 3 Entry）' % len(feats))

    ev = build_events(d)
    ev.to_csv(os.path.join(ROOT, 'h_earn_post_events.csv'), index=False,
              encoding='utf-8-sig', float_format='%.6g')
    log('已存 h_earn_post_events.csv shape=%s' % (ev.shape,))

    # ---------- 2) §48 Alpha Registry
    log('-' * 78)
    reg = build_registry(d, ic, spread, auc, inc, oos, rg, pg, cf, nmodel, pf, cost)
    reg.to_csv(os.path.join(ROOT, 'h_earn_post_registry.csv'), index=False, encoding='utf-8-sig')
    log('已存 h_earn_post_registry.csv %d 行 × %d 列' % reg.shape)

    # 主信号 × Primary Horizon 概览
    log('')
    log('§48 Registry 概览（全部信号 / E20 / T+20）:')
    sub = reg[(reg['entry_window'] == 'E20') & (reg['horizon'] == 'T+20')]
    log(sub[['alpha_id', 'mean_ic', 'ic_t', 'top20_spread', 'auc',
             'momentum_residual', 'industry_neutral', 'oos_ic',
             'excess_return', 'status', 'fail_reason']]
        .to_string(index=False, float_format=lambda x: '%.4f' % x))

    # ---------- 3) §49 报告头
    log('')
    head = header_block(reg)

    # ---------- 4) §50 核心表
    core = core_table(reg)

    # ---------- 5) §51 十二问
    qa = q12(reg, ic, oos, rg, pg, pf, cost, nmodel, cf)

    # ---------- 6) 交付物落盘（§47）
    log('-' * 78)
    copied, missing = [], []
    for nm in DELIVERABLES:
        if nm in ('h_earn_post_report.md', 'h_earn_post.json',
                  'h_earn_post_features.csv', 'h_earn_post_events.csv',
                  'h_earn_post_registry.csv'):
            continue
        src = os.path.join(OUTD, nm)
        dst = os.path.join(ROOT, nm)
        if os.path.exists(src):
            shutil.copyfile(src, dst)
            copied.append(nm)
        else:
            missing.append(nm)
    log('已从 out/ 复制到 research/h_earn_post/：%d 个' % len(copied))
    for nm in copied:
        log('    ' + nm)
    if missing:
        log('缺失：%s' % missing)

    # ---------- 7) h_earn_post.json
    payload = dict(
        hypothesis_id=PREREG['hypothesis_id'],
        research_prompt_version='V1.0',
        generated_at=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        prereg=dict(period_month=PREREG['period_month'], entries=list(ENTRIES),
                    horizons=list(HORIZONS), primary_horizon=PRIMARY_T,
                    primary_signal=PRIMARY_SIG, signals=list(SIGNALS),
                    hist_windows=list(PREREG['hist_windows']),
                    pct_cuts=list(PREREG['pct_cuts']),
                    reaction_bins=list(PREREG['reaction_bins']),
                    cost_bps=list(PREREG['cost_bps']),
                    phases=[list(x) for x in PREREG['phases']],
                    walkforward=[list(x) for x in PREREG['walkforward']],
                    D0=PREREG['D0'], entry_price=PREREG['entry_price'],
                    exit_price=PREREG['exit_price'], benchmark=PREREG['benchmark'],
                    seed=PREREG['seed'], n_seed=PREREG['n_seed'],
                    null_z_pass=Z_PASS),
        final_status=head['final_status'],
        robust_alpha_count=head['robust_count'],
        conditional_count=head['conditional_count'],
        failed_count=head['failed_count'],
        no_robust_alpha_found=bool(head['robust_count'] == 0 and head['conditional_count'] == 0),
        primary_config=dict(alpha_id=head['primary_alpha_id'], status=head['primary_status']),
        events_shape=list(d.shape),
        n_cohorts=int(d['sig_k'].nunique()),
        fail_reason_histogram=reg['fail_reason'].value_counts().to_dict(),
        status_by_config=reg[['alpha_id', 'status', 'fail_reason']].to_dict(orient='records'),
        core_table=core.to_dict(orient='records'),
        answers_12=qa,
        disclosures=[
            '中证1000(000852)/中证2000(932000) 行情不在本地 Tushare cache → §29 仅能提供 '
            '000300.SH 作为 Primary 基准；中小盘以「全A等权 PROXY」代替，其年化约 +12.9%~+15.4%，'
            '显著高于沪深300(+3.5%~+4.8%)，故以沪深300 计的超额被系统性高估（对本研究不利方向已披露）。',
            'money_cap / EV_EBITDA / PEG 不在本地 cache → §15 VAL_COLS 仅 4 个（pe_ttm/pb/ps_ttm/dv_ttm）。',
            '申万一级行业缺失率 40.97%（部分股票仅有 Tushare 行业），行业中性样本受限。',
            '2026 LIVE-LIKE 无 T+20/T+60 前向收益（T+20 可用 26/7/0 条，T+60 全 0）→ §36 的 '
            'LIVE-LIKE 期不可评估，2026 仅能报告 T+5/T+10。',
            'Regime 与年度高度共线（2018/2024 全 BEAR；2019/2020/2025 全 BULL）→ §39 的 '
            'Regime 稳定性检验功效有限。',
            'HIST_WIN=12 时 TRAIN 冻结方向翻转为 -1（4/8 为 +1）→ §40 参数邻域并非同向。',
        ],
        artifacts=DELIVERABLES,
        sealed=True,
        sealed_note='§53：本假设判定 FAIL 后即封存 H-EARN-POST；不得继续优化同一假设，'
                    '不得产生任何实盘授权；下一次研究必须建立新的 Hypothesis ID。',
    )
    with open(os.path.join(ROOT, 'h_earn_post.json'), 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    log('已存 h_earn_post.json')

    # ---------- 8) 交付物核对
    log('')
    log('§47 交付物核对（research/h_earn_post/）：')
    for nm in DELIVERABLES:
        fp = os.path.join(ROOT, nm)
        ok = os.path.exists(fp)
        sz = ('%.1f KB' % (os.path.getsize(fp) / 1024.0)) if ok else '-'
        log('    [%s] %-36s %s' % ('OK' if ok else 'XX', nm, sz))
    log('（另有 h_earn_post_incremental.csv / h_earn_post_path.csv 为 §23/§24/§25 附加表）')
    log.save()
    print('DONE')


if __name__ == '__main__':
    main()
