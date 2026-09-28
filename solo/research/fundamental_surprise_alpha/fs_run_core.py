# -*- coding: utf-8 -*-
"""fs_run_core: 核心横截面检验
  §21 Rank IC / §22 Top-Bottom / §23 Winner-Loser / §24 Baseline / §25 四象限
  §17 Momentum / §18 Value / §19 Size-Liquidity / §20 Industry 控制 / §33 审计
  §26 入场锚点 E1 / E2 / E3（各锚点只使用该时点已经真实可知的特征）

设计要点
  cohort = 公告后首个交易日 k1（同一 ann_date 的事件共享同一入场日）
  每个锚点 A 独立成一套检验：
      A=E1  入场 k1 开盘，可用价格反应仅 Gap
      A=E2  入场 k1 收盘，可用价格反应至 k1 收盘
      A=E3  入场 k1+2 收盘，可用价格反应至 k1+2 收盘
  基本面特征在 k1 开盘前即已公布，三个锚点均可用。
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fs_engine as E
from fs_common import DATA, OUTD, HORIZONS, Log

log = Log('_fs_run_core.txt')

ANCHORS = ['E1', 'E2', 'E3']

CTRL_SETS = {
    'mom': (['mom_5', 'mom_10', 'mom_20', 'mom_60'], None, E.REQ['mom']),
    'val': (['ep_ttm', 'bp', 'sp'], None, E.REQ['val']),
    'siz': (['ln_mv', 'turnover_rate'], None, E.REQ['siz']),
    'ind': ([], 'ind_l1', E.REQ['ind']),
    'all': (['mom_5', 'mom_10', 'mom_20', 'mom_60', 'ep_ttm', 'bp', 'sp',
             'ln_mv', 'turnover_rate'], 'ind_l1', E.REQ['all']),
}


def add_combos(df, Z, R):
    """§12 S6（仅二阶）/ §15 反应缺口 / §16 反应效率（全部由已公开信息构造）"""
    # S6：只允许二阶乘积
    df['S6_g_margin'] = Z['np_yoy'].values * Z['gpm_chg'].values
    df['S6_g_cash'] = Z['np_yoy'].values * Z['ocf_to_np'].values
    df['S6_g_roe'] = Z['np_yoy'].values * Z['roe_chg'].values
    df['S6_g_indrs'] = Z['np_yoy'].values * Z['ind_rs'].values
    df['S6_g_np_acc'] = Z['np_yoy'].values * Z['np_acc'].values
    # §15/§16：基本面 Surprise 与价格反应的差 / 效率
    #   pr1 依赖 Ret_R1（E2 起可知）；pr3 依赖 Cum_R3（E3 起可知）
    for f in ('S1_np', 'S1_dp', 'S2_np', 'S3_np'):
        for r_ in ('Ret_R1', 'Cum_R3'):
            if r_ not in R.columns:
                continue
            rg, ef = E.combo_name(f, r_)
            df[rg] = R[f].values - R[r_].values
            df[ef] = R[f].values * (1.0 - R[r_].values)
    return df


def nframe(df, feats, ctrls, mnn, indc):
    """中性化后补回 cohort 键与前向收益列（供 IC / Spread / Disc 复用）"""
    R = E.neutralize(df, feats, ctrls, min_n=mnn, ind_col=indc)
    R['k'] = df['k'].values
    for a in ANCHORS:
        for h in HORIZONS:
            col = E.tgt(h, a)
            if col in df.columns:
                R[col] = df[col].values
    return R


def main():
    log('=' * 64)
    log('0) 载入面板')
    df = pd.read_parquet(os.path.join(DATA, 'panel.parquet'))
    df = E.add_derived(df)
    df = E.add_ind_l2(df)
    df = E.add_hist_variants(df)
    df, asof = E.apply_asof_mask(df)
    log('  §5 as-of 掩码: %s' % asof)
    log('  面板 %d 行 / %d 只 / %d 个公告日 cohort' % (
        len(df), df['ts_code'].nunique(), df['k'].nunique()))
    log('  分期: %s' % df['period'].value_counts().to_dict())

    per_map = df.drop_duplicates('k').set_index('k')['period'].to_dict()

    # ---- 行业内相对价格强度（k0 前 20 日动量在 (cohort, 行业) 内的相对位置）----
    gr = df.groupby(['k', 'ind_l1'])['mom_20']
    r = gr.rank(method='average')
    n = gr.transform('size')
    df['ind_rs'] = np.where(n > 1, (r - 1) / (n - 1) - 0.5, np.nan)

    # ---- 静态特征（k1 开盘前即已知：基本面 / 动量 / 估值 / 规模 / 行业）----
    static = [c for c in (E.FUND_ALL + E.MOM_RAW + E.VALUE_RAW + E.SIZE_RAW + ['ind_rs'])
              if c in df.columns]

    # ---- 组合特征（由静态特征与反应特征的 Z/R 生成）----
    Zsrc = static + [c for c in E.REACT_ALL if c in df.columns]
    Z0 = E.g_z(df, Zsrc)
    R0 = E.g_rank(df, Zsrc)
    df = add_combos(df, Z0, R0)
    combos = [c for c in df.columns if c.startswith(('S6_', 'RG_', 'Eff_'))]
    hist = [c for c in df.columns if c.startswith(('S1h', 'S2h'))]

    # 组合特征按依赖的锚点分层（唯一来源：E.view_features）
    combos = [c for c in df.columns if c.startswith(('S6_', 'RG_', 'Eff_'))]
    hist = [c for c in df.columns if c.startswith(('S1h', 'S2h'))]
    log('  静态特征 %d / 二阶组合 %d / 历史窗口变体 %d' % (
        len(static), len(combos), len(hist)))

    # ---- 特征类别 ----
    cat = {}
    for c in E.FUND_REV:
        cat[c] = 'REV'
    for c in E.FUND_NP:
        cat[c] = 'NP'
    for c in E.FUND_QUAL:
        cat[c] = 'QUAL'
    for c in E.FUND_BS:
        cat[c] = 'BS'
    for c in E.SURPRISE:
        cat[c] = 'SURPRISE'
    for c in E.REACT_ALL:
        cat[c] = 'REACTION'
    for c in E.MOM_RAW:
        cat[c] = 'MOMENTUM'
    for c in E.VALUE_RAW:
        cat[c] = 'VALUE'
    for c in E.SIZE_RAW:
        cat[c] = 'SIZE'
    for c in combos:
        cat[c] = 'S6_RG'
    for c in hist:
        cat[c] = 'SURPRISE'
    cat['ind_rs'] = 'IND_RS'

    # 各锚点可见特征集合
    view_cols = {}
    for a in ANCHORS:
        vue = E.view_features(df, a)
        view_cols[a] = vue
        log('  锚点 %s 可见特征 %d 个（含反应变量 %s）' % (
            a, len(vue), [c for c in E.REACT_VIEW[a] if c in df.columns]))

    # ---------- §33 数据质量审计 ----------
    log('=' * 64)
    log('1) §33 数据质量审计')
    aud = []
    for c in dict.fromkeys(static + hist + combos + list(E.REACT_ALL)):
        if c not in df.columns:
            continue
        v = pd.to_numeric(df[c], errors='coerce')
        zz = E.g_z(df, [c])[c]
        aud.append(dict(feature=c, category=cat.get(c, '?'),
                        missing_rate=float(v.isna().mean()),
                        outlier_rate=float((zz.abs() > 5).mean()),
                        n_valid=int(v.notna().sum())))
    pd.DataFrame(aud).to_csv(os.path.join(OUTD, 'audit_feature.csv'),
                             index=False, encoding='utf-8-sig')

    dup = int(df.duplicated(['ts_code', 'end_date']).sum())
    dq = dict(
        n_events=len(df), n_stocks=int(df['ts_code'].nunique()),
        n_cohorts=int(df['k'].nunique()),
        dup_ts_end=dup, dup_rate=dup / max(len(df), 1),
        ann_agree_rate=float(df['ann_agree'].mean()),
        ann_multi_src_rate=float((df['ann_src_n'] >= 2).mean()),
        announce_time_granularity='ann_date（日粒度，无时分）；假设信息最早于 ann_date 收盘后公开',
        announce_to_entry_delay_median=float(df['delay_days'].median()),
        announce_to_entry_delay_p01=float(df['delay_days'].quantile(0.01)),
        announce_to_entry_delay_p99=float(df['delay_days'].quantile(0.99)),
        announce_on_weekend_rate=float(
            (pd.to_datetime(df['ann_date'], format='%Y%m%d').dt.dayofweek >= 5).mean()),
        k1_strictly_after_announce_rate=float(
            (pd.to_datetime(df['cal_date_k1'], format='%Y%m%d')
             > pd.to_datetime(df['ann_date'], format='%Y%m%d')).mean()),
        gap_days_median=float(df['gap_days'].median()),
        asof_lag1_leak_rows=asof['asof_lag1_leak_rows'],
        asof_ttm_leak_rows=asof['asof_ttm_leak_rows'],
        asof_masked_rows=asof['asof_masked_rows'],
        asof_masked_cols=asof['asof_masked_cols'],
        asof_note='§5 前视检查：滞后一期/滚动四期的报告若 ann_date 严格晚于当期 ann_date，'
                  '则其派生值当期未知，已置 NaN；同日披露（年报+一季报同日）属同时公开，不掩码',
        restatement_rate=np.nan,
        restatement_note='三表 parquet 同 end_date 重复行 ann_date 相同，无重述版本历史，'
                         '无法还原"当时可得版本"；已改为仅用首次公告日 ann_date 锚定，'
                         '不使用后续修正值（f_ann_date 已用于一致性校验）',
        survivorship_note='样本含已退市/已 ST 个股（按 as-of 名称剔除事件日当时为 ST/退市者），'
                          '不存在"只保留存活至今个股"的幸存者偏差',
        target_missing_T5=float(df['ex_E1_T5'].isna().mean()),
        target_missing_T20=float(df['ex_E1_T20'].isna().mean()),
        target_missing_T60=float(df['ex_E1_T60'].isna().mean()),
        ind_missing=float(df['ind_l1'].isna().mean()),
        mv_missing=float(df['ln_mv'].isna().mean()),
        pe_missing=float(df['ep_ttm'].isna().mean()),
    )
    pd.DataFrame([dq]).T.rename(columns={0: 'value'}).to_csv(
        os.path.join(OUTD, 'audit_dataset.csv'), encoding='utf-8-sig')
    log('  审计键值: %s' % {k: dq[k] for k in
                        ('n_events', 'n_stocks', 'n_cohorts', 'dup_rate',
                         'restatement_rate')})

    # ---------- §21 Rank IC（含 §17-§20 中性化）----------
    log('=' * 64)
    log('2) §21 横截面 Rank IC（三锚点 × 中性化）')
    ic_rows, coh_store = [], {}
    for a in ANCHORS:
        feats = view_cols[a]
        variants = {'raw': df}
        for vname, (ctrls, indc, mnn) in CTRL_SETS.items():
            variants['resid_' + vname] = nframe(df, feats, ctrls, mnn, indc)
        for vname, data in variants.items():
            hset = list(HORIZONS) if vname == 'raw' else [5, 20]
            for h in hset:
                ycol = E.tgt(h, a)
                if ycol not in df.columns:
                    continue
                icd = E.cohort_ic(data, feats, ycol)
                for c in feats:
                    cu, cnt, ic = icd[c]
                    if len(cu) == 0:
                        continue
                    if vname == 'raw' and h in (5, 20) and a in ('E1', 'E3'):
                        coh_store[(c, a, h)] = pd.DataFrame({'k': cu, 'ic': ic, 'n': cnt})
                    st = E.ic_stats(cu, cnt, ic, per_map)
                    for p, d in st.items():
                        ic_rows.append(dict(feature=c, category=cat.get(c, '?'),
                                            anchor=a, variant=vname, horizon=h,
                                            period=p, **d))
        log('  锚点 %s 完成（raw + %d 中性化）' % (a, len(CTRL_SETS)))
    icdf = pd.DataFrame(ic_rows)
    icdf.to_csv(os.path.join(OUTD, 'ic_full.csv'), index=False, encoding='utf-8-sig')
    log('  IC 结果 %d 行' % len(icdf))

    if coh_store:
        allc = []
        for (c, a, h), d in coh_store.items():
            d = d.copy()
            d['feature'] = c
            d['anchor'] = a
            d['horizon'] = h
            allc.append(d)
        pd.concat(allc, ignore_index=True).to_parquet(
            os.path.join(OUTD, 'cohort_ic.parquet'), index=False)

    # ---------- §22 Top-Bottom / §23 判别 ----------
    log('=' * 64)
    log('3) §22 Top-Bottom / §23 Winner-Loser 判别')
    sp_rows, dc_rows = [], []
    for a in ANCHORS:
        feats = view_cols[a]
        resid_all = nframe(df, feats, CTRL_SETS['all'][0],
                           CTRL_SETS['all'][2], CTRL_SETS['all'][1])
        for vname, data in (('raw', df), ('resid_all', resid_all)):
            for h in (5, 20):
                ycol = E.tgt(h, a)
                for c in feats:
                    s = E.spread_stats(data, c, ycol)
                    base = dict(feature=c, category=cat.get(c, '?'),
                                anchor=a, variant=vname, horizon=h)
                    if s:
                        sp_rows.append(dict(base, **s))
                    dd = E.disc_stats(data, c, ycol)
                    if dd:
                        dc_rows.append(dict(base, **dd))
        log('  锚点 %s 完成' % a)
    pd.DataFrame(sp_rows).to_csv(os.path.join(OUTD, 'spread.csv'), index=False,
                                 encoding='utf-8-sig')
    pd.DataFrame(dc_rows).to_csv(os.path.join(OUTD, 'disc.csv'), index=False,
                                 encoding='utf-8-sig')

    # ---------- §24 Baseline ----------
    log('=' * 64)
    log('4) §24 基准对照')
    Zn = E.g_z(df, ['np_yoy', 'Gap', 'Cum_R3'])
    df['B5'] = Zn['Gap'].values                                  # E1 可知的价格反应
    df['B6'] = Zn['np_yoy'].values + Zn['Gap'].values            # 简单 成长+反应
    df['B7'] = Zn['np_yoy'].values + Zn['Cum_R3'].values         # E3 可知版本
    bl_map = {
        'E1': {'B1_ALL_EVENTS': None, 'B2_MOMENTUM': 'mom_20', 'B3_VALUE': 'bp',
               'B4_EARNINGS_GROWTH': 'np_yoy', 'B5_PRICE_REACTION': 'B5',
               'B6_GROWTH_x_REACTION': 'B6'},
        'E3': {'B1_ALL_EVENTS': None, 'B2_MOMENTUM': 'mom_20', 'B3_VALUE': 'bp',
               'B4_EARNINGS_GROWTH': 'np_yoy', 'B5_PRICE_REACTION': 'Cum_R3',
               'B6_GROWTH_x_REACTION': 'B7'},
    }
    blr = []
    for a, m in bl_map.items():
        for h in (5, 10, 20):
            y = E.tgt(h, a)
            for nm, c in m.items():
                if c is None:
                    v = df[y].dropna()
                    blr.append(dict(anchor=a, baseline=nm, horizon=h,
                                    ic_mean=np.nan, icir=np.nan, spr10=np.nan,
                                    spr20=np.nan, auc=np.nan, prec=np.nan,
                                    abs_ret=float(v.mean()) if len(v) else np.nan,
                                    abs_ret_t=float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v))))
                                    if len(v) > 1 and v.std(ddof=1) > 0 else np.nan,
                                    n=int(len(v))))
                    continue
                icd = E.cohort_ic(df, [c], y)[c]
                st = E.ic_stats(icd[0], icd[1], icd[2], per_map)
                s = E.spread_stats(df, c, y)
                dsc = E.disc_stats(df, c, y)
                blr.append(dict(anchor=a, baseline=nm, horizon=h,
                                ic_mean=st['ALL']['ic_mean'], icir=st['ALL']['icir'],
                                spr10=s['spr10'] if s else np.nan,
                                spr20=s['spr20'] if s else np.nan,
                                auc=dsc['auc'] if dsc else np.nan,
                                prec=dsc['prec'] if dsc else np.nan,
                                abs_ret=np.nan, abs_ret_t=np.nan, n=np.nan,
                                ic_train=st['TRAIN']['ic_mean'],
                                ic_valid=st['VALID']['ic_mean'],
                                ic_oos=st['OOS']['ic_mean']))
    pd.DataFrame(blr).to_csv(os.path.join(OUTD, 'baseline.csv'), index=False,
                             encoding='utf-8-sig')

    # ---------- §25 四象限 ----------
    log('=' * 64)
    log('5) §25 Fundamental × PriceReaction 四象限')
    qr = []
    for f in ('S1_np', 'S1_dp', 'S2_np', 'S3_np'):
        for r_, a in (('Gap', 'E1'), ('Ret_R1', 'E2'), ('Cum_R3', 'E3')):
            if f not in df.columns or r_ not in df.columns:
                continue
            rr = E.g_rank(df, [f, r_])
            fh = (rr[f] > 0.5).values
            ph = (rr[r_] > 0.5).values
            grp = np.where(fh & ~ph, 0, np.where(fh & ph, 1, np.where(~fh & ~ph, 2, 3)))
            for h in (5, 10, 20):
                y = pd.to_numeric(df[E.tgt(h, a)], errors='coerce').values
                for pname in ('ALL', 'TRAIN', 'VALID', 'OOS'):
                    m = np.isfinite(y)
                    if pname != 'ALL':
                        m = m & (df['period'].values == pname)
                    if not m.any():
                        continue
                    codes = pd.factorize(df['k'].values[m], sort=True)[0]
                    M, C = E.seg_mean(codes, grp[m], y[m], 4)
                    ok = C >= 3
                    for gi, gname in enumerate(['A_Fhi_Plow', 'B_Fhi_Phigh',
                                                'C_Flo_Plow', 'D_Flo_Phigh']):
                        v = M[:, gi][ok[:, gi]]
                        qr.append(dict(fund=f, react=r_, anchor=a, horizon=h,
                                       period=pname, group=gname,
                                       n=int(np.isfinite(v).sum()),
                                       mean_exret=float(np.nanmean(v)) if len(v) else np.nan))
    pd.DataFrame(qr).to_csv(os.path.join(OUTD, 'quadrant.csv'), index=False,
                            encoding='utf-8-sig')

    log('=' * 64)
    log('核心检验完成')
    log.save()
    print('DONE')


if __name__ == '__main__':
    main()
