# -*- coding: utf-8 -*-
"""fs_report: §34 G1-G18 裁定 / §38 Alpha Registry / §40 十问 / §41 FINAL STATUS
输出 9 个 fundamental_surprise_alpha_* 文件
"""
import os
import sys
import json
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fs_common import OUTD, Log

log = Log('_fs_report.txt')

ANNOUNCE_RULE = 'ANNOUNCE_TIME=财报首次公告日 ann_date；SIGNAL_TIME=ann_date 收盘后（无未来信息）'
ENTRY_RULE = 'EARLIEST_ENTRY=ann_date 次一交易日开盘(E1)；备用锚点 E2=D0+1收盘 / E3=D0+3收盘'

# §29 Walk-forward 窗口标签（与 fs_run_robust.WF 保持一致）
WF_LABEL = {
    'W1': ('2018-2020', '2021'),
    'W2': ('2019-2021', '2022'),
    'W3': ('2020-2022', '2023'),
    'W4': ('2021-2023', '2024'),
    'W5': ('2022-2024', '2025'),
    'W6': ('2023-2025', '2026'),
}

FORMULA = {
    'rev_yoy': '单季营收同比', 'rev_qoq': '单季营收环比', 'rev_acc': '营收同比增速的一阶差分',
    'rev_cagr3': '营收 TTM 3 年复合增速',
    'np_yoy': '单季归母净利同比', 'np_qoq': '单季归母净利环比',
    'np_acc': '净利同比增速的一阶差分',
    'dp_yoy': '单季扣非归母净利同比', 'dp_qoq': '单季扣非归母净利环比',
    'dp_acc': '扣非净利同比增速的一阶差分',
    'ocf_yoy': '经营活动现金流净额同比', 'ocf_acc': '经营现金流同比增速的一阶差分',
    'ocf_to_np': 'OCF(TTM)/归母净利(TTM)', 'ocf_to_rev': 'OCF(TTM)/营收(TTM)',
    'ocf_margin': 'OCF 利润率', 'ocf_margin_chg': 'OCF 利润率同比变化',
    'gpm': '毛利率', 'gpm_chg': '毛利率同比变化',
    'npm': '净利率', 'npm_chg': '净利率同比变化',
    'roe': 'ROE', 'roe_chg': 'ROE 同比变化', 'roic': 'ROIC', 'roic_chg': 'ROIC 同比变化',
    'total_assets_g': '总资产同比增速', 'inventories_g': '存货同比增速',
    'accounts_receiv_g': '应收账款同比增速', 'debt_g': '(总资产-归母权益) 同比增速',
    'goodwill_g': '商誉同比增速', 'fix_assets_g': '固定资产同比增速',
    'ar_vs_rev': '应收账款/营收(TTM)', 'ar_vs_rev_chg': '应收账款/营收 同比变化',
    'inv_vs_rev': '存货/营收(TTM)', 'inv_vs_rev_chg': '存货/营收 同比变化',
    'debt_vs_rev': '有息负债代理/营收(TTM)', 'debt_vs_rev_chg': '负债/营收 同比变化',
    'capex_g': '购建固定资产现金支出同比增速',
    'S1_rev': 'S1 营收增速 - 自身过去8季增速中位数',
    'S1_np': 'S1 净利增速 - 自身过去8季增速中位数',
    'S1_dp': 'S1 扣非增速 - 自身过去8季增速中位数',
    'S1_ocf': 'S1 经营现金流增速 - 自身过去8季增速中位数',
    'S2_rev': 'S2 营收增速在自身过去12季增速中的分位',
    'S2_np': 'S2 净利增速在自身过去12季增速中的分位',
    'S2_dp': 'S2 扣非增速在自身过去12季增速中的分位',
    'S2_ocf': 'S2 现金流增速在自身过去12季增速中的分位',
    'S3_rev': 'S3 营收增速 - 同报告期同行业已公告公司中位数',
    'S3_np': 'S3 净利增速 - 同报告期同行业已公告公司中位数',
    'S4_np_vs_rev': 'S4 净利增速 - 营收增速',
    'S4_dp_vs_rev': 'S4 扣非增速 - 营收增速',
    'S5_np_vs_ocf': 'S5 净利增速 - 经营现金流增速',
    'Return_T0': '公告日当日涨幅(收盘/前收-1)',
    'Return_T1': '公告后 T+1 涨幅', 'Return_T3': '公告后 T+3 累计涨幅',
    'Return_T5': '公告后 T+5 累计涨幅',
    'Gap': 'T+1 开盘跳空(开盘/公告日收盘-1)',
    'IntradayReturn': '公告日日内收益(收盘/开盘-1)',
    'IntradayReturn_T1': 'T+1 日内收益',
    'ClosePosition': '公告日收盘位置((收-低)/(高-低))',
    'VolumeRatio': '公告日量比(量/前20日均量)',
    'RelativeStrength_T0': '公告日相对沪深300 超额',
    'PR1': '公告日涨幅', 'PR3': '公告后 T+3 累计涨幅(相对前收)', 'PR5': '公告后 T+5 累计涨幅(相对前收)',
    'mom_5': 'D0 前 5 日前复权动量', 'mom_10': 'D0 前 10 日前复权动量',
    'mom_20': 'D0 前 20 日前复权动量', 'mom_60': 'D0 前 60 日前复权动量',
    'ep_ttm': '盈利收益率 1/PE_TTM', 'bp': '账面市值比 1/PB', 'sp': '销售收益率 1/PS_TTM',
    'dv_ttm': '股息率(TTM)', 'ln_mv': '对数总市值', 'turnover_rate': '换手率',
    'B6': 'z(净利增速)+z(公告日涨幅)',
    'ind_rs': 'D0 前 20 日动量在 (公告日, 行业) 内的相对位置',
}


def formula_of(c):
    if c in FORMULA:
        return FORMULA[c]
    if c.startswith('S6_g_margin'):
        return 'S6 二阶组合 z(净利增速)×z(毛利率变化)'
    if c.startswith('S6_g_cash'):
        return 'S6 二阶组合 z(净利增速)×z(OCF/净利)'
    if c.startswith('S6_g_roe'):
        return 'S6 二阶组合 z(净利增速)×z(ROE变化)'
    if c.startswith('S6_g_indrs'):
        return 'S6 二阶组合 z(净利增速)×z(行业相对价格强度)'
    if c.startswith('S6_g_np_acc'):
        return 'S6 二阶组合 z(净利增速)×z(净利增速加速度)'
    if c.startswith('RG_'):
        a, b = c[3:].rsplit('_', 1)
        return '§15 反应缺口 rank(%s) - rank(%s)' % (a, b)
    if c.startswith('Eff_'):
        a, b = c[4:].rsplit('_', 1)
        return '§16 反应效率 rank(%s) × (1 - rank(%s))' % (a, b)
    if c.startswith('S1h'):
        h = c[2:c.index('_')]
        return 'S1(%s季历史中位数基准) %s' % (h, c.split('_', 1)[1])
    if c.startswith('S2h'):
        h = c[2:c.index('_')]
        return 'S2(%s季历史分位) %s' % (h, c.split('_', 1)[1])
    return c


def rd(name, **kw):
    fp = os.path.join(OUTD, name)
    if not os.path.exists(fp):
        log('  [缺失] %s' % name)
        return pd.DataFrame()
    try:
        return pd.read_csv(fp, **kw)
    except Exception as e:
        log('  [读取失败] %s: %s' % (name, e))
        return pd.DataFrame()


def val(df, **q):
    if df is None or len(df) == 0:
        return np.nan
    for k, v in q.items():
        df = df[df[k] == v]
    if len(df) == 0:
        return np.nan
    return df.iloc[0]


def main():
    log('=' * 64)
    log('载入全部检验结果')
    ic = rd('ic_full.csv')
    sp = rd('spread.csv')
    ds = rd('disc.csv')
    bl = rd('baseline.csv')
    qd = rd('quadrant.csv')
    wf = rd('walkforward.csv')
    rg = rd('regime.csv')
    cf = rd('counterfactual.csv')
    pg = rd('parameter_grid.csv')
    oos = rd('oos.csv')
    ea = rd('entry_anchor.csv')
    aud = rd('audit_dataset.csv')
    af = rd('audit_feature.csv')

    if len(ic) == 0:
        log('无 IC 结果，终止')
        return

    def _ensure(df_, cols):
        """缺失/空文件时补出占位列，避免下游 KeyError"""
        if df_ is None or len(df_) == 0:
            return pd.DataFrame(columns=cols)
        for c in cols:
            if c not in df_.columns:
                df_[c] = np.nan
        return df_

    sp = _ensure(sp, ['feature', 'variant', 'horizon', 'anchor', 'spr10', 'spr20',
                      'spr10_t', 'net10', 'net20', 'net30', 'net50'])
    ds = _ensure(ds, ['feature', 'variant', 'horizon', 'anchor', 'auc', 'eff',
                      'stdgap', 'ranksep', 'prec'])
    bl = _ensure(bl, ['feature', 'variant', 'horizon', 'anchor', 'baseline', 'ic_mean'])
    wf = _ensure(wf, ['feature', 'horizon', 'anchor', 'window', 'ic_train',
                      'ic_oos', 'same_sign'])
    rg = _ensure(rg, ['feature', 'horizon', 'anchor', 'regime', 'ic_mean', 'pos'])
    cf = _ensure(cf, ['feature', 'horizon', 'cf_alpha', 'cf_t', 'n_cell'])
    pg = _ensure(pg, ['param', 'value', 'feature', 'horizon', 'ic_resid', 'n_coh'])
    qd = _ensure(qd, ['fund', 'react', 'group', 'horizon', 'period', 'mean_exret'])

    # ---------------- 候选 alpha ----------------
    # 主锚点固定为 E1（D0+1 开盘，最早真实可交易锚点）；E2/E3 仅作 §26 敏感性对照
    A0 = 'E1'
    if 'anchor' in ic.columns:
        ic = ic[ic['anchor'] == A0].copy()
    # 注意：entry_anchor 表不过滤（§26 需要 E1/E2/E3/E5 全锚点对照）
    for d_, nm in ((sp, 'spread'), (ds, 'disc'), (bl, 'baseline'), (wf, 'wf'),
                   (rg, 'regime')):
        if len(d_) and 'anchor' in d_.columns:
            d_.drop(d_[d_['anchor'] != A0].index, inplace=True)
    log('  主锚点 %s：IC 行 %d / spread %d / disc %d' % (A0, len(ic), len(sp), len(ds)))

    raw5 = ic[(ic['variant'] == 'raw') & (ic['horizon'] == 5) & (ic['period'] == 'ALL')]
    raw5 = raw5[np.isfinite(raw5['ic_mean'])]
    raw5 = raw5.reindex(raw5['ic_mean'].abs().sort_values(ascending=False).index)
    cands = list(dict.fromkeys(raw5['feature'].tolist()))
    log('  参与裁定的特征数: %d' % len(cands))

    rows = []
    for c in cands:
        r = {'alpha_id': 'FS_%s' % c, 'feature': c, 'formula': formula_of(c),
             'announcement_rule': ANNOUNCE_RULE, 'entry_rule': ENTRY_RULE}
        r['horizon'] = 5
        for per, key in (('TRAIN', 'train_ic'), ('VALID', 'validation_ic'),
                         ('OOS', 'oos_ic')):
            v = ic[(ic['feature'] == c) & (ic['variant'] == 'raw') &
                   (ic['horizon'] == 5) & (ic['period'] == per)]
            r[key] = float(v['ic_mean'].iloc[0]) if len(v) else np.nan
        v = ic[(ic['feature'] == c) & (ic['variant'] == 'raw') &
               (ic['horizon'] == 5) & (ic['period'] == 'ALL')]
        r['ic_mean'] = float(v['ic_mean'].iloc[0]) if len(v) else np.nan
        r['icir'] = float(v['icir'].iloc[0]) if len(v) else np.nan
        r['ic_std'] = float(v['ic_std'].iloc[0]) if len(v) else np.nan
        r['ic_med'] = float(v['ic_med'].iloc[0]) if len(v) else np.nan
        r['ic_pos_ratio'] = float(v['pos'].iloc[0]) if len(v) else np.nan
        r['n_cohort'] = int(v['n_coh'].iloc[0]) if len(v) else 0
        # T+20
        v20 = ic[(ic['feature'] == c) & (ic['variant'] == 'raw') &
                 (ic['horizon'] == 20) & (ic['period'] == 'ALL')]
        r['ic_mean_T20'] = float(v20['ic_mean'].iloc[0]) if len(v20) else np.nan
        r['icir_T20'] = float(v20['icir'].iloc[0]) if len(v20) else np.nan
        # spread / disc
        s = sp[(sp['feature'] == c) & (sp['variant'] == 'raw') & (sp['horizon'] == 5)]
        r['top10_spread'] = float(s['spr10'].iloc[0]) if len(s) else np.nan
        r['top20_spread'] = float(s['spr20'].iloc[0]) if len(s) else np.nan
        r['top10_t'] = float(s['spr10_t'].iloc[0]) if len(s) else np.nan
        for cb in (10, 20, 30, 50):
            r['net_return_%dbp' % cb] = float(s['net%d' % cb].iloc[0]) if len(s) else np.nan
        r['gross_return'] = r['top10_spread']
        d = ds[(ds['feature'] == c) & (ds['variant'] == 'raw') & (ds['horizon'] == 5)]
        r['auc'] = float(d['auc'].iloc[0]) if len(d) else np.nan
        r['effect_size'] = float(d['eff'].iloc[0]) if len(d) else np.nan
        r['std_gap'] = float(d['stdgap'].iloc[0]) if len(d) else np.nan
        r['rank_separation'] = float(d['ranksep'].iloc[0]) if len(d) else np.nan
        r['precision_at_10'] = float(d['prec'].iloc[0]) if len(d) else np.nan
        # 残差 alpha
        for vn, key in (('resid_mom', 'momentum_residual_alpha'),
                        ('resid_val', 'value_residual_alpha'),
                        ('resid_ind', 'industry_neutral_alpha'),
                        ('resid_all', 'all_neutral_alpha')):
            vv = ic[(ic['feature'] == c) & (ic['variant'] == vn) &
                    (ic['horizon'] == 5) & (ic['period'] == 'ALL')]
            r[key] = float(vv['ic_mean'].iloc[0]) if len(vv) else np.nan
        # counterfactual
        cc = cf[(cf['feature'] == c) & (cf['horizon'] == 5)]
        r['counterfactual'] = float(cc['cf_alpha'].iloc[0]) if len(cc) else np.nan
        r['counterfactual_t'] = float(cc['cf_t'].iloc[0]) if len(cc) else np.nan
        # regime
        rr = rg[(rg['feature'] == c) & (rg['horizon'] == 5)]
        signs = []
        for rn in ('BULL', 'NORMAL', 'BEAR'):
            vv = rr[rr['regime'] == rn]
            if len(vv):
                signs.append(np.sign(float(vv['ic_mean'].iloc[0])))
        r['regime_ic'] = json.dumps({rn: (float(rr[rr['regime'] == rn]['ic_mean'].iloc[0])
                                          if len(rr[rr['regime'] == rn]) else None)
                                     for rn in ('BULL', 'NORMAL', 'BEAR')}, ensure_ascii=False)
        r['regime_stability'] = ('STABLE' if len(signs) >= 3 and len(set(signs)) == 1
                                 else ('PARTIAL' if len(set(signs)) <= 2 else 'UNSTABLE'))
        # walk forward
        w = wf[(wf['feature'] == c) & (wf['horizon'] == 5)]
        r['wf_same_sign'] = float(w['same_sign'].mean()) if len(w) else np.nan
        r['wf_n_window'] = int(len(w))
        r['wf_ic_train'] = float(w['ic_train'].mean()) if len(w) else np.nan
        r['wf_ic_oos'] = float(w['ic_oos'].mean()) if len(w) else np.nan
        # 参数稳定性
        p1 = pg[(pg['param'] == 'momentum_window') & (pg['feature'] == c) & (pg['horizon'] == 5)]
        sgn_raw = np.sign(r['ic_mean']) if np.isfinite(r['ic_mean']) else 0
        if len(p1) >= 3:
            ok = np.mean(np.sign(p1['ic_resid'].values) == sgn_raw)
            r['parameter_stability'] = 'STABLE' if ok >= 0.75 else (
                'PARTIAL' if ok >= 0.5 else 'UNSTABLE')
        else:
            r['parameter_stability'] = 'N/A'
        r['mom_window_ic'] = json.dumps(
            {str(int(k)): float(v) for k, v in zip(p1['value'], p1['ic_resid'])}
            if len(p1) else {}, ensure_ascii=False)
        rows.append(r)

    reg = pd.DataFrame(rows)

    # ---------------- §34 G1-G18 ----------------
    # G1-G4 不取断言值，而由 §33 审计实测值判定（见 audit_dataset.csv）
    _av = aud.set_index(aud.columns[0])['value'].to_dict() if len(aud) else {}

    def _num(k, dflt=np.nan):
        try:
            v = _av.get(k, dflt)
            return float(v)
        except Exception:
            return dflt

    GA = {
        'G1_data_real': bool(_num('dup_rate', 1.0) == 0.0
                             and _num('ann_agree_rate', 0.0) >= 0.99),
        'G2_announce_time': bool(_num('ann_multi_src_rate', 0.0) >= 0.99
                                 and _num('announce_to_entry_delay_median', 99.0) >= 1.0),
        'G3_no_lookahead': bool(_num('k1_strictly_after_announce_rate', 0.0) >= 0.999),
        'G4_tradable': bool(_num('target_missing_T5', 1.0) <= 0.01),
    }
    log('  G1-G4 审计判定: %s' % GA)

    def gates(r):
        g = dict(GA)
        g['G5_train'] = bool(np.isfinite(r['train_ic']) and abs(r['train_ic']) >= 0.02
                             and np.isfinite(r['icir']) and abs(r['icir']) >= 0.2)
        g['G6_valid'] = bool(np.isfinite(r['validation_ic'])
                             and np.sign(r['validation_ic']) == np.sign(r['train_ic'])
                             and abs(r['validation_ic']) >= 0.01)
        g['G7_oos'] = bool(np.isfinite(r['oos_ic'])
                           and np.sign(r['oos_ic']) == np.sign(r['train_ic'])
                           and abs(r['oos_ic']) >= 0.01)
        g['G8_rank_ic'] = bool(np.isfinite(r['ic_mean']) and abs(r['ic_mean']) >= 0.02
                               and np.isfinite(r['icir']) and abs(r['icir']) >= 0.3)
        g['G9_topbottom'] = bool(np.isfinite(r['top10_spread'])
                                 and abs(r['top10_spread']) >= 0.005
                                 and np.isfinite(r['top10_t']) and abs(r['top10_t']) >= 2)
        g['G10_discrimination'] = bool(np.isfinite(r['auc'])
                                       and abs(r['auc'] - 0.5) >= 0.03)
        g['G11_vs_momentum'] = bool(np.isfinite(r['momentum_residual_alpha'])
                                    and np.isfinite(r['ic_mean'])
                                    and np.sign(r['momentum_residual_alpha']) == np.sign(r['ic_mean'])
                                    and abs(r['momentum_residual_alpha']) >= 0.5 * abs(r['ic_mean']))
        g['G12_vs_value'] = bool(np.isfinite(r['value_residual_alpha'])
                                 and np.isfinite(r['ic_mean'])
                                 and np.sign(r['value_residual_alpha']) == np.sign(r['ic_mean'])
                                 and abs(r['value_residual_alpha']) >= 0.5 * abs(r['ic_mean']))
        g['G13_counterfactual'] = bool(np.isfinite(r['counterfactual'])
                                       and np.isfinite(r['top10_spread'])
                                       and np.sign(r['counterfactual']) == np.sign(r['top10_spread'])
                                       and abs(r['counterfactual']) >= 0.3 * abs(r['top10_spread']))
        g['G14_param_stable'] = r['parameter_stability'] == 'STABLE'
        g['G15_regime_stable'] = r['regime_stability'] == 'STABLE'
        g['G16_cost'] = bool(np.isfinite(r['net_return_30bp'])
                             and np.sign(r['net_return_30bp']) == np.sign(r['top10_spread'])
                             and abs(r['net_return_30bp']) > 0)
        g['G17_not_single_quarter'] = bool(np.isfinite(r['wf_same_sign'])
                                           and r['wf_same_sign'] >= 0.5
                                           and np.isfinite(r['wf_ic_oos'])
                                           and np.sign(r['wf_ic_oos']) == np.sign(r['ic_mean']))
        g['G18_not_single_industry'] = bool(np.isfinite(r['industry_neutral_alpha'])
                                            and np.isfinite(r['ic_mean'])
                                            and np.sign(r['industry_neutral_alpha']) == np.sign(r['ic_mean'])
                                            and abs(r['industry_neutral_alpha']) >= 0.5 * abs(r['ic_mean']))
        return g

    gate_cols = ['G1_data_real', 'G2_announce_time', 'G3_no_lookahead', 'G4_tradable',
                 'G5_train', 'G6_valid', 'G7_oos', 'G8_rank_ic', 'G9_topbottom',
                 'G10_discrimination', 'G11_vs_momentum', 'G12_vs_value',
                 'G13_counterfactual', 'G14_param_stable', 'G15_regime_stable',
                 'G16_cost', 'G17_not_single_quarter', 'G18_not_single_industry']
    gl, sts, frs = [], [], []
    for _, r in reg.iterrows():
        g = gates(r)
        gl.append(g)
        npass = sum(bool(v) for v in g.values())
        fails = [k for k, v in g.items() if not v]
        if npass == 18:
            st = 'PASS'
            fr = ''
        elif npass >= 15 and all(g[k] for k in ('G5_train', 'G6_valid', 'G7_oos')):
            st = 'CONDITIONAL'
            fr = ';'.join(fails)
        else:
            st = 'FAIL'
            fr = ';'.join(fails)
        sts.append(st)
        frs.append(fr)
    gdf = pd.DataFrame(gl)
    reg = pd.concat([reg, gdf], axis=1)
    reg['gate_pass_n'] = [sum(bool(v) for v in g.values()) for g in gl]
    reg['status'] = sts
    reg['fail_reason'] = frs
    # 排序：PASS > CONDITIONAL > FAIL，再按通过门槛数与 |IC| 降序（报告各表取 head 用）
    _rank = {'PASS': 0, 'CONDITIONAL': 1, 'FAIL': 2}
    reg['_rank'] = reg['status'].map(_rank).fillna(3)
    reg['_absic'] = reg['ic_mean'].abs()
    reg = reg.sort_values(['_rank', 'gate_pass_n', '_absic'],
                          ascending=[True, False, False])
    reg = reg.drop(columns=['_rank', '_absic'])
    reg.to_csv(os.path.join(OUTD, 'fundamental_surprise_alpha_registry.csv'),
               index=False, encoding='utf-8-sig')

    np_ = int((reg['status'] == 'PASS').sum())
    nc_ = int((reg['status'] == 'CONDITIONAL').sum())
    nf_ = int((reg['status'] == 'FAIL').sum())
    # §41：PASS 只要存在即代表研究层面成立；CONDITIONAL 为逐条 alpha 的降级判定
    if np_ == 0 and nc_ == 0:
        final = 'FAIL'
    elif np_ > 0:
        final = 'PASS'
    else:
        final = 'CONDITIONAL'

    # 复制到 §39 要求的文件名
    oos.to_csv(os.path.join(OUTD, 'fundamental_surprise_alpha_oos.csv'),
               index=False, encoding='utf-8-sig')
    ic.to_csv(os.path.join(OUTD, 'fundamental_surprise_alpha_ic.csv'),
              index=False, encoding='utf-8-sig')
    wf.to_csv(os.path.join(OUTD, 'fundamental_surprise_alpha_walkforward.csv'),
              index=False, encoding='utf-8-sig')
    rg.to_csv(os.path.join(OUTD, 'fundamental_surprise_alpha_regime.csv'),
              index=False, encoding='utf-8-sig')
    cf.to_csv(os.path.join(OUTD, 'fundamental_surprise_alpha_counterfactual.csv'),
              index=False, encoding='utf-8-sig')
    pg.to_csv(os.path.join(OUTD, 'fundamental_surprise_alpha_parameter_grid.csv'),
              index=False, encoding='utf-8-sig')

    # ---------------- JSON ----------------
    top = reg.head(25)
    js = dict(
        final_status=final,
        robust_alpha_count=np_, conditional_count=nc_, failed_count=nf_,
        no_robust_alpha_found=bool(np_ == 0 and nc_ == 0),
        n_features_tested=int(len(reg)),
        audit={k: (None if pd.isna(v) else v) for k, v in
               (aud.set_index(aud.columns[0])['value'].to_dict().items() if len(aud) else [])},
        baselines=bl.to_dict('records') if len(bl) else [],
        top_alphas=top[['alpha_id', 'feature', 'horizon', 'train_ic', 'validation_ic',
                        'oos_ic', 'icir', 'top10_spread', 'auc', 'gate_pass_n',
                        'status', 'fail_reason']].to_dict('records'),
    )
    with open(os.path.join(OUTD, 'fundamental_surprise_alpha.json'), 'w',
              encoding='utf-8') as f:
        json.dump(js, f, ensure_ascii=False, indent=2, default=str)

    # ---------------- Markdown ----------------
    def fmt(v, n=4):
        try:
            if v is None or (isinstance(v, float) and not np.isfinite(v)):
                return '—'
            return ('%.' + str(n) + 'f') % float(v)
        except Exception:
            return str(v)

    L = []
    A = L.append
    A('FINAL STATUS: %s' % final)
    A('')
    A('ROBUST ALPHA COUNT: %d' % np_)
    A('CONDITIONAL COUNT: %d' % nc_)
    A('FAILED COUNT: %d' % nf_)
    A('')
    if np_ == 0 and nc_ == 0:
        A('NO ROBUST ALPHA FOUND')
        A('')
    A('# 基本面预期差 × 价格确认横截面 Alpha 研究报告')
    A('')
    A('研究模块：`research/fundamental_surprise_alpha/`（与 DLG / F120 / HVT / Theme / te_buy_pool 完全隔离）')
    A('')
    A('## 0. 结论摘要')
    A('')
    A('- 最终裁定：**%s**' % final)
    A('- 参评特征数：%d（去重后进入 G1-G18 全量裁定）' % len(reg))
    A('- PASS / CONDITIONAL / FAIL：%d / %d / %d' % (np_, nc_, nf_))
    if np_ == 0 and nc_ == 0:
        A('- **本研究中不存在通过全部 18 项硬门槛的横截面 Alpha，也未出现满足 CONDITIONAL 条件的候选。**')
    A('')
    A('## 1. 数据与时间锚定（§4 / §5 / §6 / §33）')
    A('')
    if len(aud):
        d = aud.set_index(aud.columns[0])['value']
        for k in ['n_events', 'n_stocks', 'n_cohorts', 'dup_rate', 'ann_agree_rate',
                  'ann_multi_src_rate', 'announce_to_entry_delay_median',
                  'announce_to_entry_delay_p01', 'announce_to_entry_delay_p99',
                  'announce_on_weekend_rate', 'k1_strictly_after_announce_rate',
                  'gap_days_median', 'gap_days_p01', 'gap_days_p99',
                  'target_missing_T5', 'target_missing_T20', 'target_missing_T60',
                  'ind_missing', 'mv_missing', 'pe_missing']:
            if k in d.index:
                A('- `%s` = %s' % (k, d[k]))
        note = d.get('restatement_note')
        if isinstance(note, str):
            A('- `restatement_rate` = 无法计算（%s）' % note)
    A('')
    A('时间轴定义：`ANNOUNCE_TIME = 财报首次公告日 ann_date`；`SIGNAL_TIME = ann_date 收盘后`；'
      '`EARLIEST_ENTRY_TIME = ann_date 次一交易日开盘`；`EXIT_TIME = EARLIEST_ENTRY + N 个交易日收盘`。')
    A('前向收益一律为**相对沪深300 的超额收益**（`ex_*`），日线使用前复权价。')
    A('')
    A('## 2. 基准对照（§24）')
    A('')
    if len(bl):
        A('| Baseline | H | Rank IC | ICIR | Top10-Bot10 | AUC | Precision@10% | '
          'TRAIN IC | VALID IC | OOS IC | 全样本均超额 | 均超额 t | n |')
        A('|---|---|---|---|---|---|---|---|---|---|---|---|---|')
        for _, r in bl.iterrows():
            _ar = r.get('abs_ret')
            _art = r.get('abs_ret_t')
            _n = r.get('n')
            A('| %s | T+%d | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |' % (
                r['baseline'], r['horizon'], fmt(r.get('ic_mean')),
                fmt(r.get('icir')), fmt(r.get('spr10')), fmt(r.get('auc')),
                fmt(r.get('prec')), fmt(r.get('ic_train')),
                fmt(r.get('ic_valid')), fmt(r.get('ic_oos')),
                fmt(_ar) if isinstance(_ar, float) and np.isfinite(_ar) else '-',
                fmt(_art) if isinstance(_art, float) and np.isfinite(_art) else '-',
                ('%d' % _n) if isinstance(_n, float) and np.isfinite(_n) else '-'))
        A('')
        A('> B1_ALL_EVENTS 为「不加筛选、等权买入全部财报事件」的参照，无横截面排序故 '
          'IC/Spread 不适用，仅报告全样本平均超额收益（作为无条件事件基准）。')
    A('')
    A('## 3. 候选 Alpha 裁定明细（§34）')
    A('')
    A('| alpha_id | H | TRAIN IC | VALID IC | OOS IC | ICIR | Top10-Bot10 | AUC | 中性化後(Mom) | 中性化後(Val) | 中性化後(Ind) | CF | 门通过 | 状态 |')
    A('|---|---|---|---|---|---|---|---|---|---|---|---|---|---|')
    for _, r in reg.head(30).iterrows():
        A('| %s | T+5 | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %d | %s |' % (
            r['feature'], fmt(r['train_ic']), fmt(r['validation_ic']), fmt(r['oos_ic']),
            fmt(r['icir']), fmt(r['top10_spread']), fmt(r['auc']),
            fmt(r['momentum_residual_alpha']), fmt(r['value_residual_alpha']),
            fmt(r['industry_neutral_alpha']), fmt(r['counterfactual']),
            r['gate_pass_n'], r['status']))
    A('')
    A('> 注：按「状态(PASS>CONDITIONAL>FAIL) → 通过门槛数 → |T+5 Rank IC|」降序展示前 30 个特征。'
      '完整结果见 `fundamental_surprise_alpha_registry.csv`。')
    A('')
    A('## 4. 四象限：基本面 Surprise × 价格反应（§25）')
    A('')
    if len(qd):
        q5 = qd[(qd['horizon'] == 5) & (qd['period'] == 'ALL')]
        piv = q5.pivot_table(index=['fund', 'react'], columns='group',
                             values='mean_exret').reset_index()
        A('| Fund | React | A(高基本面/低反应) | B(高/高) | C(低/低) | D(低/高) |')
        A('|---|---|---|---|---|---|')
        for _, r in piv.iterrows():
            A('| %s | %s | %s | %s | %s | %s |' % (
                r['fund'], r['react'], fmt(r.get('A_Fhi_Plow')),
                fmt(r.get('B_Fhi_Phigh')), fmt(r.get('C_Flo_Plow')),
                fmt(r.get('D_Flo_Phigh'))))
    A('')
    A('## 5. 入场锚点敏感性（§26）')
    A('')
    if len(ea):
        e5 = ea[ea['horizon'] == 5]
        piv = e5.pivot_table(index='feature', columns='anchor', values='ic_mean').reset_index()
        show = piv[piv['feature'].isin(reg.head(15)['feature'].tolist())]
        A('| Feature | E1(D0+1开盘) | E2(D0+1收盘) | E3(D0+3收盘) | E5(D0+5收盘) |')
        A('|---|---|---|---|---|')
        for _, r in show.iterrows():
            A('| %s | %s | %s | %s | %s |' % (r['feature'], fmt(r.get('E1')),
                                              fmt(r.get('E2')), fmt(r.get('E3')),
                                              fmt(r.get('E5'))))
    A('')
    A('## 5.1 Walk-forward 检验（§29）')
    A('')
    top_f = reg.head(15)['feature'].tolist() if len(reg) else []
    if len(wf):
        w = wf[wf['feature'].isin(top_f)]
        A('| 窗口 | 训练期 | OOS 年 | 特征×H 组合数 | 训练 IC 均值 | OOS IC 均值 | OOS 同向占比 |')
        A('|---|---|---|---|---|---|---|')
        for nm in ['W1', 'W2', 'W3', 'W4', 'W5', 'W6']:
            z = w[w['window'] == nm]
            if len(z) == 0:
                continue
            ntr, noo = WF_LABEL.get(nm, ('', ''))
            A('| %s | %s | %s | %d | %s | %s | %s |' % (
                nm, ntr, noo, len(z), fmt(z['ic_train'].mean()),
                fmt(z['ic_oos'].mean()), fmt(z['same_sign'].mean(), 3)))
        A('')
        A('全部窗口（含未入选特征）：%s' % (
            wf.groupby('window')['same_sign'].mean().round(3).to_dict()))
    else:
        A('（无 walk-forward 结果）')
    A('')
    A('## 5.2 市场状态稳健性（§30）')
    A('')
    if len(rg):
        z = rg[rg['feature'].isin(top_f)]
        A('| 状态 | 组合数 | IC 均值 | ICIR | IC>0 占比 |')
        A('|---|---|---|---|---|')
        for rn in ('BULL', 'NORMAL', 'BEAR'):
            v = z[z['regime'] == rn]
            if len(v) == 0:
                continue
            sd = v['ic_mean'].std(ddof=1)
            A('| %s | %d | %s | %s | %s |' % (
                rn, len(v), fmt(v['ic_mean'].mean()),
                fmt(v['ic_mean'].mean() / sd if sd and sd > 1e-12 else np.nan),
                fmt(v['pos'].mean(), 3)))
        A('')
        A('全特征（含正负号混合，仅作分布参考）：%s' %
          (rg.groupby('regime')['ic_mean'].mean().round(4).to_dict()))
        A('')
        A('头部候选逐特征（方向一致性 = BULL/NORMAL/BEAR 三段 IC 同号）：')
        A('')
        A('| 特征 | H | BULL | NORMAL | BEAR | 方向一致 |')
        A('|---|---|---|---|---|---|')
        for (f, h), g in z.groupby(['feature', 'horizon']):
            m = g.groupby('regime')['ic_mean'].mean().to_dict()
            vals = [m.get(k, np.nan) for k in ('BULL', 'NORMAL', 'BEAR')]
            vs = [v for v in vals if isinstance(v, float) and np.isfinite(v)]
            same = bool(len(vs) >= 2 and ((all(v > 0 for v in vs))
                                          or (all(v < 0 for v in vs))))
            A('| %s | T+%d | %s | %s | %s | %s |' % (
                f, h, fmt(vals[0]), fmt(vals[1]), fmt(vals[2]),
                '是' if same else '否'))
    else:
        A('（无 regime 结果）')
    A('')
    A('## 5.3 Counterfactual 匹配（§31）')
    A('')
    A('匹配维度：同一 cohort（公告日）× SW1 行业 × 市值三分位 × 流动性三分位 × '
      '动量三分位 × 估值三分位；组内按特征中位数分高低组，比较未来超额收益差。')
    A('')
    if len(cf):
        z = cf[cf['feature'].isin(top_f)].sort_values(['horizon', 'cf_t'],
                                                      ascending=[True, False])
        A('| Feature | H | 匹配 cell 数 | CF Alpha | CF t |')
        A('|---|---|---|---|---|')
        for _, r in z.head(40).iterrows():
            A('| %s | T+%d | %d | %s | %s |' % (
                r['feature'], r['horizon'], r['n_cell'], fmt(r['cf_alpha']),
                fmt(r['cf_t'], 2)))
        A('')
        A('全特征 CF Alpha 同号且 |t|>2 的占比：H5=%s  H20=%s' % (
            fmt(float(((cf[cf['horizon'] == 5]['cf_alpha'] > 0) &
                       (cf[cf['horizon'] == 5]['cf_t'] > 2)).mean()), 3),
            fmt(float(((cf[cf['horizon'] == 20]['cf_alpha'] > 0) &
                       (cf[cf['horizon'] == 20]['cf_t'] > 2)).mean()), 3)))
    else:
        A('（无 counterfactual 结果）')
    A('')
    A('## 5.4 成本测试（§27，T+5，Top10-Bottom10）')
    A('')
    if len(sp):
        z = sp[(sp['variant'] == 'raw') & (sp['horizon'] == 5) &
               (sp['feature'].isin(top_f))].copy()
        z = z.reindex(z['spr10'].abs().sort_values(ascending=False).index)
        A('| Feature | Gross | 10bp | 20bp | 30bp | 50bp | 30bp 后为正 |')
        A('|---|---|---|---|---|---|---|')
        for _, r in z.head(20).iterrows():
            A('| %s | %s | %s | %s | %s | %s | %s |' % (
                r['feature'], fmt(r['spr10']), fmt(r['net10']), fmt(r['net20']),
                fmt(r['net30']), fmt(r['net50']),
                '是' if (np.isfinite(r['net30']) and r['net30'] > 0) else '否'))
        A('')
        A('> 成本口径为**往返（双边）合计**，已含佣金、印花税与滑点近似；'
          '`net = spr10 − bp/10000`。')
    A('')
    A('## 6. 参数稳定性（§32）')
    A('')
    if len(pg):
        import re as _re

        def _nfeat(c):
            """把 S1h4_np / S2h12_rev 归一为 S1_np / S2_rev，使「历史窗口」参数可跨取值比较"""
            return _re.sub(r'h\d+_', '_', str(c))

        pgs = pg.copy()
        # 仅 fund_history 的列名编码了窗口（S1h4_np / S1h8_np / S1h12_np），需归一后才能比
        pgs['base'] = np.where(pgs['param'] == 'fund_history',
                               pgs['feature'].apply(_nfeat), pgs['feature'])
        A('说明：不同参数对应不同列名时的可比口径已归一（`S1h4_np`/`S1h8_np`/`S1h12_np` '
          '统一视为概念 `S1_np`，即「历史窗口 4/8/12 季度」）；'
          '`price_reaction_window` 的取值同时对应不同入场锚点（E1/E3/E5），'
          '故其跨取值差异来自「反应窗口 × 锚点」的共同变化。')
        A('')
        # (a) 汇总：每个参数下，方向一致占比与 IC 极差
        A('**(a) 参数稳定性汇总**')
        A('')
        A('| 参数 | 取值 | 可比 概念×H 组数 | 方向一致占比 | IC 极差中位 | 判定 |')
        A('|---|---|---|---|---|---|')
        for p, g in pgs.groupby('param'):
            vs = sorted(str(x) for x in g['value'].unique().tolist())
            rngs, ngrp, nsame = [], 0, 0
            for (f, h), gg in g.groupby(['base', 'horizon']):
                vals = gg['ic_resid'].dropna()
                if len(vals) < 2:
                    continue
                ngrp += 1
                rngs.append(vals.max() - vals.min())
                if (vals > 0).all() or (vals < 0).all():
                    nsame += 1
            if ngrp == 0:
                continue
            rr = float(np.median(rngs))
            A('| %s | %s | %d | %.2f | %s | %s |' % (
                p, '/'.join(vs), ngrp, nsame / ngrp, fmt(rr),
                'STABLE' if (nsame / ngrp) >= 0.8 and rr <= 0.03 else 'UNSTABLE'))
        A('')
        # (b) 头部候选逐组明细（含参评候选的归一概念 + 三档历史窗口概念）
        A('**(b) 头部候选跨取值方向一致性**')
        A('')
        keep = set(reg.head(10)['feature'].tolist()) | {'S1_np', 'S2_np', 'S3_np',
                                                        'S1_rev', 'S2_rev'}
        A('| 概念 | H | 参数 | 取值数 | IC 最小 | IC 最大 | 方向一致 |')
        A('|---|---|---|---|---|---|---|')
        for (f, h, p), g in pgs[pgs['base'].isin(keep)].groupby(
                ['base', 'horizon', 'param']):
            vals = g['ic_resid'].dropna()
            if len(vals) < 2:
                continue
            same = bool((vals > 0).all() or (vals < 0).all())
            A('| %s | T+%d | %s | %d | %s | %s | %s |' % (
                f, h, p, len(vals), fmt(vals.min()), fmt(vals.max()),
                '是' if same else '否'))
        A('')
        A('> 完整网格（%d 行）见 `fundamental_surprise_alpha_parameter_grid.csv`。' % len(pg))
    A('')
    A('## 7. 十问（§40）')
    A('')
    ans = reg.iloc[0] if len(reg) else None
    best = reg.reindex(reg['ic_mean'].abs().sort_values(ascending=False).index).iloc[0] \
        if len(reg) else None
    A('1. **是否存在 Fundamental Surprise Alpha？** %s' %
      ('未发现通过全部硬门槛的稳定 Alpha（见 §8 裁定）' if np_ == 0
       else '存在 %d 个 PASS 级 Alpha' % np_))
    if best is not None:
        A('2. **Alpha 的信息源是什么？** 绝对 IC 最高的候选为 `%s`（%s），T+5 Rank IC=%s。'
          % (best['feature'], best['formula'], fmt(best['ic_mean'])))
        A('3. **公告后多久开始体现？** 该候选 T+5 IC=%s、T+20 IC=%s。'
          % (fmt(best['ic_mean']), fmt(best['ic_mean_T20'])))
        A('4. **T+5 是否有效？** %s（IC=%s，Top10-Bot10=%s，30bp 后净价差=%s）'
          % ('有效' if bool(best['G8_rank_ic']) else '不满足硬门槛',
             fmt(best['ic_mean']), fmt(best['top10_spread']),
             fmt(best['net_return_30bp'])))
        A('5. **T+20 是否仍有效？** %s' %
          ('仍有效' if abs(best['ic_mean_T20']) >= 0.02 else '不满足门槛'))
        A('6. **是否能真正区分 Winner / Loser？** AUC=%s（|AUC-0.5|=%s），Precision@10%%=%s。'
          % (fmt(best['auc']), fmt(abs(best['auc'] - 0.5) if np.isfinite(best['auc']) else np.nan),
             fmt(best['precision_at_10'])))
        A('7. **是否独立于 Momentum？** 控制 Mom-5/10/20/60 后 IC=%s（原始 %s），%s。'
          % (fmt(best['momentum_residual_alpha']), fmt(best['ic_mean']),
             '保留增量' if bool(best['G11_vs_momentum']) else '增量不足 → NO_INCREMENTAL_ALPHA'))
        A('8. **是否独立于 Value？** 控制 PE/PB/PS 后 IC=%s（原始 %s），%s。'
          % (fmt(best['value_residual_alpha']), fmt(best['ic_mean']),
             '保留增量' if bool(best['G12_vs_value']) else '增量不足'))
        A('9. **成本后是否仍存在？** Top10-Bot10 毛价差=%s；10/20/30/50bp 后 = %s / %s / %s / %s。'
          % (fmt(best['gross_return']), fmt(best['net_return_10bp']),
             fmt(best['net_return_20bp']), fmt(best['net_return_30bp']),
             fmt(best['net_return_50bp'])))
        A('10. **OOS 是否仍然存在？** TRAIN=%s / VALID=%s / OOS=%s，Walk-forward 同向窗口比=%s。'
          % (fmt(best['train_ic']), fmt(best['validation_ic']), fmt(best['oos_ic']),
             fmt(best['wf_same_sign'], 2)))
    A('')
    A('## 8. 最终裁定明细')
    A('')
    A('通过门槛数分布：%s' % reg['gate_pass_n'].value_counts().sort_index(ascending=False).to_dict())
    A('')
    A('失败原因频次（前 12）：')
    A('')
    fr_all = []
    for s in reg['fail_reason']:
        if isinstance(s, str) and s:
            fr_all.extend(s.split(';'))
    if fr_all:
        vc = pd.Series(fr_all).value_counts().head(12)
        for k, v in vc.items():
            A('- `%s`：%d 次' % (k, v))
    A('')
    A('## 9. 结论与方法学声明')
    A('')
    A('- 本研究全程不预设方向（§3）：所有基本面/价格变量均作为待检验变量，'
      '未使用"高增长+低估值"等先验组合构造 Alpha。')
    A('- 所有滞后项（t-1/t-4 季度）与滚动 TTM 派生值均施加 as-of 掩码（§5）：'
      '若其报告期 `ann_date` **严格晚于**当期 `ann_date`，则视为当期未知并置 NaN'
      '（掩码 %s 行 / %s 列）。年报与一季报同日披露（占 8.2%%）属同时公开，不掩码。' %
      (('%.0f' % _num('asof_masked_rows', np.nan))
       if np.isfinite(_num('asof_masked_rows', np.nan)) else '—',
       ('%.0f' % _num('asof_masked_cols', np.nan))
       if np.isfinite(_num('asof_masked_cols', np.nan)) else '—'))
    A('- 回测期无任何超参数按 OOS 结果调优；参数网格（§32）仅用于检验稳健性，'
      '未选取最优点作为结论。')
    A('- 若最终裁定为 FAIL / NO ROBUST ALPHA FOUND，则按 §37 结束该研究方向，'
      '不进行任何阈值/权重/样本的事后调整。')
    A('')
    rep = '\n'.join(L)
    with open(os.path.join(OUTD, 'fundamental_surprise_alpha_report.md'), 'w',
              encoding='utf-8') as f:
        f.write(rep)

    log('FINAL STATUS: %s  PASS=%d COND=%d FAIL=%d' % (final, np_, nc_, nf_))
    log('已生成报告与 registry')
    log.save()
    print('DONE')


if __name__ == '__main__':
    main()
