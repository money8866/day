# -*- coding: utf-8 -*-
"""fs_build_fund: 基本面事件表（§4 公告时间真实性 / §5 无未来信息 / §7-§12 特征库）

核心纪律
  1. 事件锚点 = 定期财报首次公告日 ann_date（无法确认则 EXCLUDE）
  2. 所有滞后项（t-1Q / t-4Q / t-8Q ...）必须满足 ann_date_lag < ann_date_current
  3. 不预设任何方向（增长高低/现金流好坏只作为待检验变量）
"""
import os
import sys
import glob
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fs_common import DATA, CD, PD, Log

log = Log('_fs_build_fund.txt')

INC_WANT = ['ts_code', 'ann_date', 'f_ann_date', 'end_date', 'report_type',
            'revenue', 'total_revenue', 'n_income', 'n_income_attr_p',
            'total_cogs', 'operate_profit', 'rd_exp']
BAL_WANT = ['ts_code', 'ann_date', 'f_ann_date', 'end_date', 'report_type',
            'total_assets', 'inventories', 'accounts_receiv', 'goodwill',
            'fix_assets', 'intan_assets', 'total_hldr_eqy_exc_min_int']
CF_WANT = ['ts_code', 'ann_date', 'f_ann_date', 'end_date', 'report_type',
           'n_cashflow_act', 'c_pay_acq_const_fiolta', 'n_cash_flows_fnc_act',
           'n_cashflow_inv_act']
FI_WANT = ['ts_code', 'ann_date', 'end_date', 'profit_dedt', 'roe', 'roe_dt',
           'grossprofit_margin', 'netprofit_margin', 'ocf_to_sales', 'roic',
           'debt_to_assets', 'ebitda', 'netdebt', 'ocfps', 'bps', 'eps',
           'assets_yoy', 'eqt_yoy', 'op_yoy', 'netprofit_yoy', 'dt_netprofit_yoy',
           'tr_yoy', 'or_yoy', 'q_roe', 'q_dt_roe', 'q_npta', 'q_ocf_to_sales']

NUM_INC = ['revenue', 'total_revenue', 'n_income', 'n_income_attr_p', 'total_cogs',
           'operate_profit', 'rd_exp']
NUM_BAL = ['total_assets', 'inventories', 'accounts_receiv', 'goodwill',
           'fix_assets', 'intan_assets', 'total_hldr_eqy_exc_min_int']
NUM_CF = ['n_cashflow_act', 'c_pay_acq_const_fiolta', 'n_cash_flows_fnc_act',
          'n_cashflow_inv_act']


def load_family(prefixes, want, tag):
    files = []
    for d in (PD, CD):
        for f in os.listdir(d):
            if f.endswith('.parquet') and any(f.startswith(p) for p in prefixes):
                files.append(os.path.join(d, f))
    files = sorted(set(files))
    frames, bad = [], 0
    for fp in files:
        try:
            d = pd.read_parquet(fp)
        except Exception:
            bad += 1
            continue
        if 'report_type' in d.columns:
            d = d[d['report_type'].astype(str).str.replace('.0', '', regex=False) == '1']
        cs = [c for c in want if c in d.columns]
        if 'ts_code' not in cs or 'end_date' not in cs or len(d) == 0:
            bad += 1
            continue
        frames.append(d[cs].copy())
        if len(frames) >= 500:
            frames = [pd.concat(frames, ignore_index=True)]
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=want)
    log('  [%s] 文件=%d 坏=%d 行=%d' % (tag, len(files), bad, len(out)))
    return out


def clean_num(d, cols):
    for c in cols:
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors='coerce')
    return d


def prep(d, tag):
    d['ts_code'] = d['ts_code'].astype(str)
    d['end_date'] = d['end_date'].astype(str).str.replace('.0', '', regex=False)
    d['ann_date'] = d['ann_date'].astype(str).str.replace('.0', '', regex=False)
    d = d[d['end_date'].str.match(r'^\d{8}$') & d['ann_date'].str.match(r'^\d{8}$')]
    d = d[d['end_date'].str[4:].isin(['0331', '0630', '0930', '1231'])]
    d = d.sort_values(['ts_code', 'end_date', 'ann_date'])
    d = d.drop_duplicates(['ts_code', 'end_date', 'ann_date'], keep='last')
    d = d.drop_duplicates(['ts_code', 'end_date'], keep='first')
    return d.reset_index(drop=True)


def main():
    log('=' * 64)
    log('1) 载入三表 + 财务指标（只读既有 parquet 缓存）')
    inc = prep(load_family(['income'], INC_WANT, 'income'), 'income')
    bal = prep(load_family(['balance'], BAL_WANT, 'balance'), 'balance')
    cf = prep(load_family(['cashflow'], CF_WANT, 'cashflow'), 'cashflow')
    fi = prep(load_family(['treasure_fin_ind', 'fin_ind'], FI_WANT, 'fina_ind'), 'fina_ind')
    inc = clean_num(inc, NUM_INC)
    bal = clean_num(bal, NUM_BAL)
    cf = clean_num(cf, NUM_CF)
    fi = clean_num(fi, [c for c in FI_WANT if c not in ('ts_code', 'ann_date', 'end_date')])

    log('  覆盖: income %d 只 / balance %d / cashflow %d / fina_ind %d' % (
        inc['ts_code'].nunique(), bal['ts_code'].nunique(),
        cf['ts_code'].nunique(), fi['ts_code'].nunique()))

    # ---------- 2) 合成事件主表 ----------
    log('=' * 64)
    log('2) 合成事件主表 (ts_code, end_date)')
    base = inc.rename(columns={'ann_date': 'ann_inc'})
    for d, tag in ((bal, 'bal'), (cf, 'cf'), (fi, 'fi')):
        cols = ['ts_code', 'end_date', 'ann_date'] + [c for c in d.columns
                                                      if c not in ('ts_code', 'end_date', 'ann_date', 'f_ann_date')]
        base = base.merge(d[cols].rename(columns={'ann_date': 'ann_' + tag}),
                          on=['ts_code', 'end_date'], how='left')
    log('  合并后: %d 行 / %d 只' % (len(base), base['ts_code'].nunique()))

    # ann_date 取 income 优先，缺失回退 balance/cashflow/fina_ind
    base['ann_date'] = base['ann_inc']
    for c in ('ann_bal', 'ann_cf', 'ann_fi'):
        base['ann_date'] = base['ann_date'].where(base['ann_date'].notna()
                                                  & (base['ann_date'].astype(str).str.len() == 8), base[c])
    # 四源一致性（审计用）
    annm = base[['ann_inc', 'ann_bal', 'ann_cf', 'ann_fi']].astype(str)
    annm = annm.where(annm.apply(lambda s: s.str.len() == 8))
    base['ann_src_n'] = annm.notna().sum(axis=1)
    base['ann_agree'] = annm.apply(lambda r: len(set(r.dropna())) <= 1, axis=1)

    base['end_date'] = base['end_date'].astype(str)
    base['ann_date'] = base['ann_date'].astype(str)
    base = base[base['ann_date'].str.match(r'^\d{8}$')].copy()

    # §5 公告时间合理性
    ed = pd.to_datetime(base['end_date'], format='%Y%m%d', errors='coerce')
    ad = pd.to_datetime(base['ann_date'], format='%Y%m%d', errors='coerce')
    base['gap_days'] = (ad - ed).dt.days
    base['plausible'] = base['gap_days'].between(10, 200)
    log('  公告时滞 gap_days: 无效(<0或>400)=%d  可疑(不在10~200)=%d' % (
        int((~base['gap_days'].between(0, 400)).sum()), int((~base['plausible']).sum())))
    log('  多源公告日完全一致比例: %.4f  (中位源数=%.1f)' % (
        base['ann_agree'].mean(), base['ann_src_n'].median()))

    base = base[base['plausible']].copy()
    base['quarter'] = base['end_date'].str[4:6].astype(int)
    base['fyear'] = base['end_date'].str[:4].astype(int)
    base = base.sort_values(['ts_code', 'end_date']).reset_index(drop=True)
    log('  剔除不可信公告时滞后: %d 事件 / %d 只' % (len(base), base['ts_code'].nunique()))

    # ---------- 3) 单季化 ----------
    log('=' * 64)
    log('3) 累计值单季化 + TTM')
    rev = base['revenue'].fillna(base['total_revenue'])
    base['rev_cum'] = rev
    for col, src in (('rev', 'rev_cum'), ('npa', 'n_income_attr_p'),
                     ('ocf', 'n_cashflow_act'), ('dp', 'profit_dedt')):
        base[src] = pd.to_numeric(base[src], errors='coerce')
        g = base.groupby('ts_code', sort=False)
        prev = base[src].groupby(base['ts_code']).shift(1)
        prev_q = base['quarter'].groupby(base['ts_code']).shift(1)
        prev_y = base['fyear'].groupby(base['ts_code']).shift(1)
        same_chain = (base['quarter'] != 3) & (prev_q == base['quarter'] - 1) & (prev_y == base['fyear'])
        base[col + '_q'] = np.where(same_chain, base[src] - prev, base[src])
        base[col + '_ttm'] = base[col + '_q'].groupby(base['ts_code']).rolling(4, min_periods=4).sum().reset_index(level=0, drop=True)

    # ---------- 4) 同比 / 环比 / 同比加速度 ----------
    log('=' * 64)
    log('4) 同比、环比、加速度（全部 as-of）')
    g = base.groupby('ts_code', sort=False)

    def yoy(col):
        cur = base[col + '_q']
        lag4 = cur.groupby(base['ts_code']).shift(4)
        lag4d = base['end_date'].groupby(base['ts_code']).shift(4)
        ok = (lag4.abs() > 1e-9) & (base['end_date'].str[4:] == lag4d.str[4:])
        return (cur / lag4.where(ok, np.nan) - 1.0)

    def qoq(col):
        cur = base[col + '_q']
        lag1 = cur.groupby(base['ts_code']).shift(1)
        ok = (lag1.abs() > 1e-9)
        return (cur / lag1.where(ok, np.nan) - 1.0)

    base['rev_yoy'] = yoy('rev')
    base['rev_qoq'] = qoq('rev')
    base['np_yoy'] = yoy('npa')
    base['np_qoq'] = qoq('npa')
    base['ocf_yoy'] = yoy('ocf')
    base['dp_yoy'] = yoy('dp')
    base['dp_qoq'] = qoq('dp')

    # 去年同期基数异常保护（避免分母极小造成爆炸）
    for c in ('rev_yoy', 'np_yoy', 'ocf_yoy', 'dp_yoy', 'rev_qoq', 'np_qoq', 'dp_qoq'):
        base[c] = base[c].where(base[c].abs() < 20.0)

    for c in ('rev_yoy', 'np_yoy', 'ocf_yoy', 'dp_yoy'):
        lag1 = base[c].groupby(base['ts_code']).shift(1)
        base[c.replace('_yoy', '_acc')] = base[c] - lag1

    base['rev_cagr3'] = np.where(
        base['rev_ttm'].notna() & (g['rev_ttm'].shift(12) > 0) & (base['rev_ttm'] > 0),
        (base['rev_ttm'] / g['rev_ttm'].shift(12)) ** (1 / 3.0) - 1.0, np.nan)

    # ---------- 5) 盈利质量 ----------
    log('5) 盈利质量 / 资产负债表')
    base['ocf_to_np'] = base['ocf_ttm'] / base['npa_ttm'].abs().replace(0, np.nan)
    base['ocf_to_rev'] = base['ocf_ttm'] / base['rev_ttm'].replace(0, np.nan)
    base['npm'] = base['npa_ttm'] / base['rev_ttm'].replace(0, np.nan)
    base['gpm'] = base['grossprofit_margin']
    base['ocf_margin'] = base['ocf_ttm'] / base['rev_ttm'].replace(0, np.nan)
    for c, src in (('npm', 'npm'), ('gpm', 'gpm'), ('roe', 'roe'), ('roic', 'roic'),
                   ('ocf_margin', 'ocf_margin')):
        lag4 = base[src].groupby(base['ts_code']).shift(4)
        base[c + '_chg'] = base[src] - lag4
        base[c + '_acc'] = base[c + '_chg'] - base[c + '_chg'].groupby(base['ts_code']).shift(1)

    # §10 资产负债表
    base['debt'] = base['total_assets'] - base['total_hldr_eqy_exc_min_int']
    for c in ('total_assets', 'inventories', 'accounts_receiv', 'debt',
              'total_hldr_eqy_exc_min_int', 'goodwill', 'fix_assets'):
        lag4 = base[c].groupby(base['ts_code']).shift(4)
        ok = (lag4.abs() > 1e-9)
        base[c + '_g'] = base[c] / lag4.where(ok, np.nan) - 1.0
    base['ar_vs_rev'] = base['accounts_receiv'] / base['rev_ttm'].replace(0, np.nan)
    base['inv_vs_rev'] = base['inventories'] / base['rev_ttm'].replace(0, np.nan)
    base['debt_vs_rev'] = base['debt'] / base['rev_ttm'].replace(0, np.nan)
    base['ar_vs_rev_chg'] = base['ar_vs_rev'] - base['ar_vs_rev'].groupby(base['ts_code']).shift(4)
    base['inv_vs_rev_chg'] = base['inv_vs_rev'] - base['inv_vs_rev'].groupby(base['ts_code']).shift(4)
    base['debt_vs_rev_chg'] = base['debt_vs_rev'] - base['debt_vs_rev'].groupby(base['ts_code']).shift(4)
    base['capex_g'] = np.nan
    lag4 = base['c_pay_acq_const_fiolta'].groupby(base['ts_code']).shift(4)
    base['capex_g'] = base['c_pay_acq_const_fiolta'] / lag4.where(lag4.abs() > 1e-9, np.nan) - 1.0

    # ---------- 6) Surprise 定义（§12） ----------
    log('6) Surprise S1-S6')
    for src, tag in (('rev_yoy', 'rev'), ('np_yoy', 'np'), ('dp_yoy', 'dp'),
                     ('ocf_yoy', 'ocf')):
        sh = base[src].groupby(base['ts_code']).shift(1)
        med = sh.groupby(base['ts_code']).rolling(8, min_periods=4).median()
        med.index = med.index.droplevel(0)
        base['S1_%s' % tag] = base[src] - med.sort_index()

    # S2: 当前同比在自身过去 12 期同比中的分位（不含当期）
    for tag in ('rev', 'np', 'dp', 'ocf'):
        src = tag + '_yoy'
        vals = []
        for _, sub in base.groupby('ts_code', sort=False):
            v = sub[src].values
            out = np.full(len(v), np.nan)
            for i in range(len(v)):
                hist = v[max(0, i - 12):i]
                hist = hist[np.isfinite(hist)]
                if len(hist) >= 6 and np.isfinite(v[i]):
                    out[i] = float((hist < v[i]).mean())
            vals.append(pd.Series(out, index=sub.index))
        if vals:
            base['S2_' + tag] = pd.concat(vals).sort_index()

    # S4 盈利质量 surprise / S5 现金流确认
    base['S4_np_vs_rev'] = base['np_yoy'] - base['rev_yoy']
    base['S4_dp_vs_rev'] = base['dp_yoy'] - base['rev_yoy']
    base['S5_np_vs_ocf'] = base['np_yoy'] - base['ocf_yoy']

    base.to_parquet(os.path.join(DATA, 'events_base.parquet'), index=False)
    log('  已存 data/events_base.parquet  shape=%s' % (base.shape,))
    log.save()
    print('DONE')


if __name__ == '__main__':
    main()
