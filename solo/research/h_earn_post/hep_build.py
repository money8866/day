# -*- coding: utf-8 -*-
"""hep_build: H-EARN-POST 中报事件数据集

时间轴（严格 as-of，§4/§5）
  REPORT_PERIOD    = end_date（中报 = YYYY0630）
  ANNOUNCEMENT_DATE= ann_date（三大表 + fina_indicator 四源交叉，取 income 优先）
  D0               = ann_date 之后第一个交易日 = k1（首个可交易日）
  k0               = k1 - 1（公告前参照收盘）
  E{20,25,30}      = k1 + k（观察窗口收盘；信号在该收盘后已知）
  Entry            = E+1 开盘
  Exit             = E+1+T 收盘（T ∈ {5,10,20,60}）

产出 data/hep_events.parquet（每行 = 一个「中报 × Entry」记录）

§1 隔离：本脚本只读 Tushare cache（cache_daily）与市场级行情/估值/日历/指数文件，
       不 import 任何策略模块，不使用任何既有策略的信号或筛选结果。
"""
import os
import sys
import glob
import time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hep_common import (DATA, OUTD, CD, PD, FS_DATA, PREREG, ENTRIES, HORIZONS,
                        F32, BASIC_KEEP, MIN_XS, Log,
                        zs, rank_pct, spearman_ic, ols_resid)

log = Log('_hep_build.txt')

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
           'roe_yoy', 'grossprofit_margin', 'netprofit_margin', 'or_yoy', 'tr_yoy',
           'netprofit_yoy', 'dt_netprofit_yoy', 'ocf_yoy', 'assets_yoy',
           'debt_to_assets', 'ocfps', 'bps', 'eps', 'q_roe', 'q_ocf_to_sales']

NUM = {'income': ['revenue', 'total_revenue', 'n_income', 'n_income_attr_p',
                  'total_cogs', 'operate_profit', 'rd_exp'],
       'balance': ['total_assets', 'inventories', 'accounts_receiv', 'goodwill',
                   'fix_assets', 'intan_assets', 'total_hldr_eqy_exc_min_int'],
       'cashflow': ['n_cashflow_act', 'c_pay_acq_const_fiolta',
                    'n_cash_flows_fnc_act', 'n_cashflow_inv_act'],
       'fina_ind': [c for c in FI_WANT if c not in ('ts_code', 'ann_date', 'end_date')]}


# ------------------------------------------------------------------ 载入
def load_family(prefixes, want, tag):
    files = []
    for d in (CD, PD):
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
        if len(frames) >= 400:
            frames = [pd.concat(frames, ignore_index=True)]
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=want)
    log('  [%s] 文件=%d 坏=%d 行=%d' % (tag, len(files), bad, len(out)))
    return out


def prep(d, tag):
    d = d.copy()
    d['ts_code'] = d['ts_code'].astype(str)
    for c in ('end_date', 'ann_date', 'f_ann_date'):
        if c in d.columns:
            d[c] = d[c].astype(str).str.replace('.0', '', regex=False)
    d = d[d['end_date'].str.match(r'^\d{8}$')]
    d = d[d['ann_date'].astype(str).str.match(r'^\d{8}$')]
    d = d[d['end_date'].str[4:].isin(['0331', '0630', '0930', '1231'])]
    d = d.sort_values(['ts_code', 'end_date', 'ann_date'])
    d = d.drop_duplicates(['ts_code', 'end_date', 'ann_date'], keep='last')
    d = d.drop_duplicates(['ts_code', 'end_date'], keep='first')
    return d.reset_index(drop=True)


def load_industry():
    """返回 (SW 一级, Tushare industry)。

    SW L1 来自 cache_daily/industry/sw_industry_map.csv（仅 3000 只，事件股票覆盖约 53.6%），
    未覆盖 -> 'UNKNOWN'（§18 中立化时作为独立组，不与其他分类体系混用）。
    Tushare industry 来自 stock_basic.csv（覆盖 100%，110 类），用于 S3 行业相对 Surprise
    与 §11 行业相对价格反应（需要全样本覆盖），并作为 §18 的稳健性口径。
    """
    sw, tx = {}, {}
    fp = os.path.join(CD, 'industry', 'sw_industry_map.csv')
    if os.path.exists(fp):
        d = pd.read_csv(fp, dtype=str)
        d = d[d['ts_code'].notna()].sort_values('in_date')
        sw = dict(zip(d['ts_code'], d['l1_name']))
        log('  行业(SW L1) 覆盖 %d 只 / %d 类' % (len(sw), d['l1_name'].nunique()))
    fp2 = os.path.join(CD, 'stock_basic.csv')
    if os.path.exists(fp2):
        d2 = pd.read_csv(fp2, dtype=str)
        tx = dict(zip(d2['ts_code'], d2['industry']))
        log('  行业(Tushare) 覆盖 %d 只 / %d 类' % (len(tx), d2['industry'].nunique()))
    return sw, tx


def load_name_asof():
    frames = []
    for fp in glob.glob(os.path.join(CD, 'treasure_namechg_*.parquet')):
        try:
            d = pd.read_parquet(fp)
            if len(d):
                frames.append(d[['ts_code', 'name', 'start_date', 'end_date']])
        except Exception:
            pass
    if not frames:
        return None
    d = pd.concat(frames, ignore_index=True)
    d['ts_code'] = d['ts_code'].astype(str)
    for c in ('start_date', 'end_date'):
        s = d[c].astype(str).str.replace('.0', '', regex=False)
        s = s.where(s.str.match(r'^\d{8}$'), '99999999')
        d[c] = s
    d['start_date'] = d['start_date'].where(d['start_date'].str.len() == 8, '19000101')
    log('  名称变更记录 %d 行 / %d 只' % (len(d), d['ts_code'].nunique()))
    return d


# ------------------------------------------------------------------ 主流程
def main():
    t0 = time.time()
    log('=' * 72)
    log('H-EARN-POST 数据集构建（预注册：%s）' % PREREG['hypothesis_id'])

    # ---------- 1) 日历 + 行情矩阵 ----------
    log('=' * 72)
    log('1) 交易日历与行情矩阵')
    cal = pd.read_parquet(os.path.join(FS_DATA, 'calendar.parquet'))
    td = cal['trade_date'].astype(str).values
    NCAL = len(td)
    kmap = pd.Series(np.arange(NCAL), index=td)
    log('  交易日 %d  %s ~ %s' % (NCAL, td[0], td[-1]))

    px = pd.read_parquet(os.path.join(FS_DATA, 'price_panel.parquet'),
                         columns=['ts_code', 'trade_date'] + F32)
    px['trade_date'] = px['trade_date'].astype(str)
    codes = np.sort(px['ts_code'].astype(str).unique())
    cmap = pd.Series(np.arange(len(codes)), index=codes)
    log('  行情 %d 行 / %d 只' % (len(px), len(codes)))

    si = px['ts_code'].map(cmap).values.astype(np.int64)
    kk = px['trade_date'].map(kmap).values
    okk = np.isfinite(kk)
    si, kk = si[okk], kk[okk].astype(np.int64)
    M = {}
    for c in F32:
        a = np.full((len(codes), NCAL), np.nan, dtype=np.float32)
        a[si, kk] = pd.to_numeric(px[c], errors='coerce').values[okk].astype(np.float32)
        M[c] = a
    del px
    C, O, Cq, Oq = M['close'], M['open'], M['qfq_close'], M['qfq_open']
    PC, V, AMT = M['pre_close'], M['vol'], M['amount']
    log('  矩阵就绪 C/O/Cq/Oq/V/AMT')

    # ---------- 2) 指数 + Regime ----------
    log('=' * 72)
    log('2) 基准指数 000300.SH 与 Regime')
    idx = pd.read_parquet(os.path.join(FS_DATA, 'index_panel.parquet'))
    idx = idx[idx['ts_code'] == '000300.SH'].copy()
    idx['k'] = idx['trade_date'].astype(str).map(kmap)
    idx = idx[idx['k'].notna()].sort_values('k')
    lvl = np.full(NCAL, np.nan)
    lvl[idx['k'].values.astype(int)] = pd.to_numeric(idx['close'], errors='coerce').values
    lvl = pd.Series(lvl).ffill().bfill().values
    log('  指数 %d 天  %.1f ~ %.1f' % (len(lvl), np.nanmin(lvl), np.nanmax(lvl)))

    s_lvl = pd.Series(lvl, index=td)
    ma200 = s_lvl.rolling(200, min_periods=60).mean()
    r60 = s_lvl / s_lvl.shift(60) - 1.0
    reg = pd.Series('NORMAL', index=s_lvl.index)
    reg[(s_lvl > ma200) & (r60 > 0)] = 'BULL'
    reg[(s_lvl < ma200) & (r60 < 0)] = 'BEAR'
    REG = reg.values
    log('  Regime 分布: %s' % pd.Series(REG).value_counts().to_dict())

    # ---------- 3) 三大表 + 财务指标 ----------
    log('=' * 72)
    log('3) 载入三大表与财务指标（全量扫描 Tushare cache）')
    bcache = os.path.join(DATA, '_hep_base_raw.parquet')
    if os.path.exists(bcache):
        base = pd.read_parquet(bcache)
        log('  [dev-cache] 复用 %s  %d 行 / %d 只（删该文件可强制重扫）'
            % (os.path.basename(bcache), len(base), base['ts_code'].nunique()))
    else:
        inc = prep(clean(load_family(['income'], INC_WANT, 'income'), 'income'), 'income')
        bal = prep(clean(load_family(['balance'], BAL_WANT, 'balance'), 'balance'), 'balance')
        cf = prep(clean(load_family(['cashflow'], CF_WANT, 'cashflow'), 'cashflow'), 'cashflow')
        fi = prep(clean(load_family(['treasure_fin_ind', 'fin_ind'], FI_WANT, 'fina_ind'),
                        'fina_ind'), 'fina_ind')
        log('  覆盖: income %d / balance %d / cashflow %d / fina_ind %d 只' % (
            inc['ts_code'].nunique(), bal['ts_code'].nunique(),
            cf['ts_code'].nunique(), fi['ts_code'].nunique()))

        base = inc.rename(columns={'ann_date': 'ann_inc'})
        for d, tag in ((bal, 'bal'), (cf, 'cf'), (fi, 'fi')):
            cols = ['ts_code', 'end_date', 'ann_date'] + [c for c in d.columns if c not in
                                                          ('ts_code', 'end_date', 'ann_date',
                                                           'f_ann_date', 'report_type')]
            base = base.merge(d[cols].rename(columns={'ann_date': 'ann_' + tag}),
                              on=['ts_code', 'end_date'], how='left')
        base['ann_date'] = base['ann_inc']
        for c in ('ann_bal', 'ann_cf', 'ann_fi'):
            base['ann_date'] = base['ann_date'].where(
                base['ann_date'].astype(str).str.len() == 8, base[c])
        base = base[base['ann_date'].astype(str).str.match(r'^\d{8}$')].copy()
        log('  合并后 %d 行 / %d 只' % (len(base), base['ts_code'].nunique()))

        ed = pd.to_datetime(base['end_date'], format='%Y%m%d', errors='coerce')
        ad = pd.to_datetime(base['ann_date'], format='%Y%m%d', errors='coerce')
        base['gap_days'] = (ad - ed).dt.days
        n_bad = int((~base['gap_days'].between(10, 200)).sum())
        base = base[base['gap_days'].between(10, 200)].copy()
        log('  §4 公告时滞不可信剔除 %d -> %d' % (n_bad, len(base)))

        base['quarter'] = base['end_date'].str[4:6].astype(int)
        base['fyear'] = base['end_date'].str[:4].astype(int)
        base = base.sort_values(['ts_code', 'end_date']).reset_index(drop=True)
        base.to_parquet(bcache, index=False)

    # ---------- 4) 特征（§7/§8） ----------
    log('=' * 72)
    log('4) 基本面特征（§7 六组 + §8 增长 vs 加速度）')
    base['rev_cum'] = pd.to_numeric(base['revenue'], errors='coerce').fillna(
        pd.to_numeric(base['total_revenue'], errors='coerce'))
    base['np_cum'] = pd.to_numeric(base['n_income_attr_p'], errors='coerce')
    base['dp_cum'] = pd.to_numeric(base['profit_dedt'], errors='coerce')
    base['ocf_cum'] = pd.to_numeric(base['n_cashflow_act'], errors='coerce')

    def lag(d, n, same_period=False):
        v = pd.Series(d, dtype=float).groupby(base['ts_code'].values, sort=False).shift(n)
        v.index = base.index
        if same_period:
            mm = base['end_date'].groupby(base['ts_code'].values, sort=False).shift(n).str[4:]
            v = v.where(base['end_date'].str[4:] == mm.values, np.nan)
        return v

    g = base.groupby('ts_code', sort=False)
    pq = g['quarter'].shift(1)
    py = g['fyear'].shift(1)
    adj = (pq == base['quarter'] - 1) & (py == base['fyear'])
    for c in ('rev_cum', 'np_cum', 'dp_cum', 'ocf_cum'):
        base[c + '_p4'] = lag(base[c], 4, True)
        # 单季化（相邻季相减；Q1 累计即单季；缺前一季 -> 置 NaN）
        prev = g[c].shift(1)
        base[c + '_q'] = np.where(adj, base[c] - prev,
                                  np.where(base['quarter'] == 1, base[c], np.nan))
        base[c + '_qp4'] = lag(base[c + '_q'], 4, True)
        # 环比分母：仅当上一报告期确实是本期的上一季度
        base[c + '_qm1'] = np.where(adj, prev, np.nan)

    base['rev_yoy'] = base['rev_cum'] / base['rev_cum_p4'] - 1.0
    base['np_yoy'] = base['np_cum'] / base['np_cum_p4'] - 1.0
    base['dp_yoy'] = base['dp_cum'] / base['dp_cum_p4'] - 1.0
    base['ocf_yoy'] = base['ocf_cum'] / base['ocf_cum_p4'] - 1.0
    base['rev_yoy_q'] = base['rev_cum_q'] / base['rev_cum_qp4'] - 1.0
    base['np_yoy_q'] = base['np_cum_q'] / base['np_cum_qp4'] - 1.0
    base['rev_qoq'] = base['rev_cum_q'] / base['rev_cum_qm1'].replace(0, np.nan) - 1.0
    base['np_qoq'] = base['np_cum_q'] / base['np_cum_qm1'].replace(0, np.nan) - 1.0
    base['dp_qoq'] = base['dp_cum_q'] / base['dp_cum_qm1'].replace(0, np.nan) - 1.0

    for c in ('rev_yoy', 'np_yoy', 'dp_yoy', 'ocf_yoy', 'rev_qoq', 'np_qoq', 'dp_qoq',
              'rev_yoy_q', 'np_yoy_q'):
        base[c] = base[c].where(base[c].abs() < 50.0)

    # §8 加速度：YoY(t) − YoY(t−4)（同季同比的同比变化，即 §8 原意）+ YoY(t) − YoY(t−1)
    for tag in ('rev', 'np', 'dp', 'ocf'):
        base[tag + '_accel'] = base[tag + '_yoy'] - lag(base[tag + '_yoy'], 4, True)
        base[tag + '_accel_q'] = base[tag + '_yoy'] - lag(base[tag + '_yoy'], 1)

    # 盈利能力
    tv = pd.to_numeric(base['total_revenue'], errors='coerce')
    tc = pd.to_numeric(base['total_cogs'], errors='coerce')
    base['gpm_calc'] = (tv - tc) / tv.replace(0, np.nan) * 100.0
    base['npm_calc'] = base['np_cum'] / tv.replace(0, np.nan) * 100.0
    base['gpm'] = pd.to_numeric(base['grossprofit_margin'], errors='coerce').fillna(base['gpm_calc'])
    base['npm'] = pd.to_numeric(base['netprofit_margin'], errors='coerce').fillna(base['npm_calc'])
    base['roe'] = pd.to_numeric(base['roe'], errors='coerce')
    base['roe_dt'] = pd.to_numeric(base['roe_dt'], errors='coerce')
    base['roe_yoy'] = base['roe'] / lag(base['roe'], 4, True).replace(0, np.nan) - 1.0
    base['roe_chg'] = base['roe'] - lag(base['roe'], 4, True)
    base['gpm_chg'] = base['gpm'] - lag(base['gpm'], 4, True)
    base['npm_chg'] = base['npm'] - lag(base['npm'], 4, True)

    # 现金流
    base['ocf_to_np'] = base['ocf_cum'] / base['np_cum'].abs().replace(0, np.nan)
    base['ocf_to_rev'] = base['ocf_cum'] / base['rev_cum'].replace(0, np.nan)

    # 资产负债表
    base['debt'] = (pd.to_numeric(base['total_assets'], errors='coerce')
                    - pd.to_numeric(base['total_hldr_eqy_exc_min_int'], errors='coerce'))
    for c, nm in (('accounts_receiv', 'ar'), ('inventories', 'inv'), ('debt', 'debt'),
                  ('total_hldr_eqy_exc_min_int', 'eqy')):
        base[c] = pd.to_numeric(base[c], errors='coerce')
        lag4 = lag(base[c], 4, True)
        base[nm + '_growth'] = base[c] / lag4.where(lag4.abs() > 1e-9, np.nan) - 1.0
    base['equity_growth'] = base['eqy_growth']       # Cash_Growth 可得代理（money_cap 不在缓存）
    base['ar_minus_rev'] = base['ar_growth'] - base['rev_yoy']
    base['inv_minus_rev'] = base['inv_growth'] - base['rev_yoy']

    log('  特征覆盖: rev_yoy=%.3f np_yoy=%.3f roe=%.3f ocf_to_np=%.3f ar_growth=%.3f' % (
        1 - base['rev_yoy'].notna().mean(), 1 - base['np_yoy'].notna().mean(),
        1 - base['roe'].notna().mean(), 1 - base['ocf_to_np'].notna().mean(),
        1 - base['ar_growth'].notna().mean()))

    # ---------- 5) Surprise S1-S5（§9） ----------
    log('=' * 72)
    log('5) Surprise S1–S5（§9）')
    for win in PREREG['hist_windows']:
        for tag, src in (('np', 'np_yoy'), ('rev', 'rev_yoy'), ('ocf', 'ocf_yoy')):
            sh = lag(base[src], 1)
            med = sh.groupby(base['ts_code']).rolling(win, min_periods=3).median()
            med.index = med.index.droplevel(0)
            base['S1_%s_%d' % (tag, win)] = base[src] - med.sort_index()
    # S2: 当前 YoY 在自身过去 12 期的分位（不含当期）
    for tag in ('np', 'rev', 'ocf'):
        src = tag + '_yoy'
        vals = []
        for _, sub in base.groupby('ts_code', sort=False):
            v = sub[src].values
            out = np.full(len(v), np.nan)
            for i in range(len(v)):
                h = v[max(0, i - 12):i]
                h = h[np.isfinite(h)]
                if len(h) >= 6 and np.isfinite(v[i]):
                    out[i] = float((h < v[i]).mean())
            vals.append(pd.Series(out, index=sub.index))
        base['S2_' + tag] = pd.concat(vals).sort_index()
    base['S4_np_vs_rev'] = base['np_yoy'] - base['rev_yoy']
    base['S4_dp_vs_rev'] = base['dp_yoy'] - base['rev_yoy']
    base['S5_np_vs_ocf'] = base['np_yoy'] - base['ocf_yoy']
    # 主模型别名（§40 邻域在 S1_*_4/8/12 上扰动；主模型固定取 hist_window_default）
    _hw = PREREG['hist_window_default']
    for tag in ('np', 'rev'):
        base[tag + '_s1'] = base['S1_%s_%d' % (tag, _hw)]
    base['np_minus_ocf'] = base['S5_np_vs_ocf']
    log('  S1/S2/S4/S5 完成（S1 主窗口=%d）' % _hw)

    # ---------- 6) 事件 = 中报 ----------
    log('=' * 72)
    log('6) 抽取中报事件（end_date 月 = %s）' % PREREG['period_month'])
    ev = base[base['end_date'].str[4:8] == PREREG['period_month']].copy()
    ev = ev.reset_index(drop=True)
    log('  中报事件 %d 行 / %d 只' % (len(ev), ev['ts_code'].nunique()))

    # ---------- 7) S3 行业相对 Surprise（as-of expanding） ----------
    log('=' * 72)
    log('7) 行业与 S3 行业相对 Surprise')
    sw, tx = load_industry()
    ev['ind_l1'] = ev['ts_code'].map(sw).fillna('UNKNOWN')      # 申万一级（§18 主口径）
    ev['ind_tx'] = ev['ts_code'].map(tx).fillna('UNKNOWN')      # Tushare 行业（全样本）
    log('  SW L1 缺失（UNKNOWN）比例 %.4f' % float((ev['ind_l1'] == 'UNKNOWN').mean()))
    log('  Tushare 行业缺失（UNKNOWN）比例 %.4f' % float((ev['ind_tx'] == 'UNKNOWN').mean()))
    ev = ev.sort_values(['end_date', 'ind_tx', 'ann_date']).reset_index(drop=True)
    for src, tag in (('rev_yoy', 'rev'), ('np_yoy', 'np')):
        ev['S3_%s' % tag] = ev[src] - ev.groupby(['end_date', 'ind_tx'])[src].transform(
            lambda s: s.expanding(min_periods=5).median())

    # ---------- 8) 时间锚点 ----------
    log('=' * 72)
    log('8) 时间锚点 k1 / k0 与 Regime / year')
    k1 = np.searchsorted(td, ev['ann_date'].values, side='right').astype(np.int64)
    ev['k1'] = k1
    ev['k0'] = k1 - 1
    ok = (ev['k1'] >= 1) & (ev['k1'] <= NCAL - 1)
    log('  ann_date 无法映射到交易日剔除 %d' % int((~ok).sum()))
    ev = ev[ok].copy()
    ev['cal_date_k1'] = td[ev['k1'].values]
    ev['delay_days'] = (pd.to_datetime(ev['cal_date_k1'], format='%Y%m%d')
                        - pd.to_datetime(ev['ann_date'], format='%Y%m%d')).dt.days
    ev['rep_year'] = ev['end_date'].str[:4].astype(int)
    ev['regime'] = REG[ev['k1'].values]
    ev['year'] = ev['cal_date_k1'].str[:4]
    ev['month'] = ev['cal_date_k1'].str[4:6]
    log('  k1 滞后自然日中位=%.0f p90=%.0f；年度分布=%s' % (
        ev['delay_days'].median(), ev['delay_days'].quantile(0.9),
        ev['rep_year'].value_counts().sort_index().to_dict()))
    log('  Regime 分布(D0)=%s' % ev['regime'].value_counts().to_dict())

    # ---------- 9) 行业/规模/流动性/动量控制变量（as-of E） ----------
    log('=' * 72)
    log('9) 价格反应（§11/§12）与控制变量（§15–§18）')
    bas = pd.read_parquet(os.path.join(FS_DATA, 'basic_panel.parquet'),
                          columns=['ts_code', 'trade_date'] + BASIC_KEEP)
    bas['trade_date'] = bas['trade_date'].astype(str)
    bsi = bas['ts_code'].map(cmap)
    bk = bas['trade_date'].map(kmap)
    bm = bsi.notna() & bk.notna()
    bas = bas[bm].copy()
    bsi = bas['ts_code'].map(cmap).values.astype(np.int64)
    bk = bas['trade_date'].map(kmap).values.astype(np.int64)
    BM = {}
    for c in BASIC_KEEP:
        a = np.full((len(codes), NCAL), np.nan, dtype=np.float32)
        a[bsi, bk] = pd.to_numeric(bas[c], errors='coerce').values.astype(np.float32)
        BM[c] = a
    del bas
    log('  估值矩阵就绪 %d 列' % len(BM))

    rows = []
    for e in ENTRIES:
        d = ev.copy()
        d['entry_k'] = d['k1'] + e            # E
        d['sig_k'] = d['entry_k']
        d['buy_k'] = d['k1'] + e + 1          # E+1 开盘
        d['E'] = e
        si_f = d['ts_code'].map(cmap).values.astype(float)
        s_ok = np.isfinite(si_f)
        d['si'] = np.where(s_ok, si_f, -1).astype(np.int64)
        sidx = d['si'].values
        kk0 = np.clip(d['k0'].values, 0, NCAL - 1)
        sE_raw = d['sig_k'].values
        inb = (sidx >= 0) & (sE_raw >= 0) & (sE_raw <= NCAL - 1)
        sidx = np.where(inb, sidx, 0)
        sE = np.clip(sE_raw, 0, NCAL - 1)

        # §11 价格反应：k0 收盘 -> E 收盘
        with np.errstate(invalid='ignore', divide='ignore'):
            rr = Cq[sidx, sE] / Cq[sidx, kk0] - 1.0
        ir = lvl[np.clip(sE, 0, NCAL - 1)] / lvl[np.clip(kk0, 0, NCAL - 1)] - 1.0
        d['px_ret'] = np.where(inb, rr, np.nan)
        d['px_ret_rel'] = d['px_ret'] - ir
        d['px_ret_bench'] = ir
        # §11 行业相对吸收：同报告期同行业内、按 sig_k 升序的 as-of 中位数（无未来信息）
        d['px_ret_ind_rel'] = np.nan
        _g = d.sort_values(['rep_year', 'ind_tx', 'sig_k'])
        _grp = _g.groupby(['rep_year', 'ind_tx'])['px_ret']
        _med = _grp.transform(lambda s: s.expanding(min_periods=8).median())
        _cnt = _grp.transform(lambda s: s.expanding(min_periods=1).count())
        _rel = (_g['px_ret'] - _med).where(_cnt >= 8)
        d['px_ret_ind_rel'] = _rel.reindex(d.index)
        # §11 子窗口反应（D0 -> D0+k，k ∈ {5,10,20}；仅当 k <= E 且索引在界内）
        for k in (5, 10, 20):
            a = np.full(len(d), np.nan)
            kk = kk0 + k
            mk = inb & (kk <= sE) & (kk <= NCAL - 1)
            ixk = np.flatnonzero(mk)
            if len(ixk):
                with np.errstate(invalid='ignore', divide='ignore'):
                    a[ixk] = (Cq[sidx[ixk], kk[ixk]]
                              / Cq[sidx[ixk], kk0[ixk]] - 1.0)
            d['px_ret_%d' % k] = a

        # §16 Momentum
        for n in (5, 10, 20, 60):
            a = np.full(len(d), np.nan)
            m = inb & ((sE - n) >= 0)
            ix = np.flatnonzero(m)
            if len(ix):
                with np.errstate(invalid='ignore', divide='ignore'):
                    a[ix] = Cq[sidx[ix], sE[ix]] / Cq[sidx[ix], sE[ix] - n] - 1.0
            d['mom_%d' % n] = a
        # §15/§17 估值 / 规模 / 流动性（as-of E）
        for c, nm in (('pe_ttm', 'pe_ttm'), ('pb', 'pb'), ('ps_ttm', 'ps_ttm'),
                      ('dv_ttm', 'dv_ttm'), ('total_mv', 'total_mv'),
                      ('circ_mv', 'circ_mv'), ('turnover_rate', 'turnover'),
                      ('volume_ratio', 'volratio')):
            a = np.full(len(d), np.nan)
            m = inb & (sE <= NCAL - 1)
            ix = np.flatnonzero(m)
            if len(ix):
                a[ix] = BM[c][sidx[ix], sE[ix]]
            d[nm] = a
        # 20 日均额（流动性）
        a = np.full(len(d), np.nan)
        ix = np.flatnonzero(inb & (sE >= 20))
        if len(ix):
            acc = np.zeros(len(ix))
            for j in range(1, 21):
                acc += AMT[sidx[ix], sE[ix] - j]
            a[ix] = acc / 20.0
        d['amt20'] = a

        # §19 前向收益（E+1 开盘 -> E+1+T 收盘）
        ep = np.full(len(d), np.nan)
        ixep = np.flatnonzero(inb & (d['buy_k'].values <= NCAL - 1))
        if len(ixep):
            ep[ixep] = Oq[sidx[ixep], d['buy_k'].values[ixep]]
        d['entry_px'] = ep
        for T in HORIZONS:
            ex = d['buy_k'].values + T
            m = inb & (ex <= NCAL - 1)
            ix = np.flatnonzero(m)
            r = np.full(len(d), np.nan)
            xr = np.full(len(d), np.nan)
            if len(ix):
                with np.errstate(invalid='ignore', divide='ignore'):
                    r[ix] = Cq[sidx[ix], ex[ix]] / Oq[sidx[ix], d['buy_k'].values[ix]] - 1.0
                xr[ix] = (lvl[np.clip(ex[ix], 0, NCAL - 1)]
                          / lvl[np.clip(d['buy_k'].values[ix], 0, NCAL - 1)] - 1.0)
            d['ret_%d' % T] = r
            d['idxret_%d' % T] = xr
            d['ex_%d' % T] = r - xr
            # 稳健性：指数起点用 E 收盘（比策略多一个交易日暴露）
            xr2 = np.full(len(d), np.nan)
            if len(ix):
                xr2[ix] = (lvl[np.clip(ex[ix], 0, NCAL - 1)]
                           / lvl[np.clip(sE[ix], 0, NCAL - 1)] - 1.0)
            d['idxret2_%d' % T] = xr2
            d['ex2_%d' % T] = r - xr2
        # §25 事件后路径：Entry 后 k 个交易日的累计超额收益（同一 Entry 起点，无未来信息）
        for k in PREREG['path_horizons']:
            ex = d['buy_k'].values + k
            m = inb & (ex <= NCAL - 1) & (d['buy_k'].values <= NCAL - 1)
            ix = np.flatnonzero(m)
            a = np.full(len(d), np.nan)
            if len(ix):
                with np.errstate(invalid='ignore', divide='ignore'):
                    a[ix] = (Cq[sidx[ix], ex[ix]] / Oq[sidx[ix], d['buy_k'].values[ix]] - 1.0
                             - (lvl[np.clip(ex[ix], 0, NCAL - 1)]
                                / lvl[np.clip(d['buy_k'].values[ix], 0, NCAL - 1)] - 1.0))
            d['pex_%d' % k] = a
        # §25 事件后路径：自 D0（中报首个可交易日 k1 收盘）起的累计收益 / 超额收益
        for k in PREREG['path_horizons']:
            exk = d['k1'].values + k
            m = inb & (exk <= NCAL - 1)
            ix = np.flatnonzero(m)
            r0 = np.full(len(d), np.nan)
            e0 = np.full(len(d), np.nan)
            if len(ix):
                with np.errstate(invalid='ignore', divide='ignore'):
                    r0[ix] = Cq[sidx[ix], exk[ix]] / Cq[sidx[ix], d['k1'].values[ix]] - 1.0
                    e0[ix] = r0[ix] - (lvl[np.clip(exk[ix], 0, NCAL - 1)]
                                       / lvl[np.clip(d['k1'].values[ix], 0, NCAL - 1)] - 1.0)
            d['p0ret_%d' % k] = r0
            d['p0ex_%d' % k] = e0
        for c in ('buy_open', 'buy_preclose', 'buy_vol'):
            a = np.full(len(d), np.nan)
            ix = np.flatnonzero(inb & (d['buy_k'].values <= NCAL - 1))
            if len(ix):
                a[ix] = {'buy_open': O, 'buy_preclose': PC, 'buy_vol': V}[c][
                    sidx[ix], d['buy_k'].values[ix]]
            d[c] = a
        rows.append(d)
    dat = pd.concat(rows, ignore_index=True)
    log('  E20/25/30 记录 %d 行' % len(dat))

    # ---------- 10) 过滤（§5 可交易条件） ----------
    log('=' * 72)
    log('10) 可交易性过滤')
    n0 = len(dat)
    m = dat['entry_px'].notna() & (dat['entry_px'] > 0) & dat['buy_vol'].notna() & (dat['buy_vol'] > 0)
    dat = dat[m].copy()
    log('  入场价/停牌剔除 %d -> %d' % (n0 - len(dat), len(dat)))
    n1 = len(dat)
    dat = dat[dat['mom_60'].notna()].copy()
    log('  上市不足 60 交易日剔除 %d -> %d' % (n1 - len(dat), len(dat)))
    n2 = len(dat)
    lu = dat['buy_open'] >= dat['buy_preclose'] * 1.095
    ld = dat['buy_open'] <= dat['buy_preclose'] * 0.905
    dat = dat[~(lu | ld)].copy()
    log('  入场日一字板剔除 %d -> %d' % (n2 - len(dat), len(dat)))

    nc = load_name_asof()
    if nc is not None and len(nc):
        dat = dat.reset_index(drop=True)
        dat['_i'] = np.arange(len(dat))
        mm = dat[['_i', 'ts_code', 'ann_date']].merge(nc, on='ts_code', how='left')
        mm = mm[(mm['start_date'] <= mm['ann_date']) & (mm['ann_date'] <= mm['end_date'])]
        mm = mm.drop_duplicates('_i')
        dat = dat.merge(mm[['_i', 'name']].rename(columns={'name': 'name_at_ev'}),
                        on='_i', how='left').drop(columns=['_i'])
        bad = dat['name_at_ev'].astype(str).str.contains('ST|退', na=False)
        log('  ST/退市剔除 %d（名称可得 %d）' % (int(bad.sum()), int(dat['name_at_ev'].notna().sum())))
        dat = dat[~bad].copy()

    # ---------- 11) 派生信号（§12/§13/§14） ----------
    log('=' * 72)
    log('11) 合成信号（§12/§13/§14）')
    dat['fsc_z'] = np.nan
    dat['pa_z'] = np.nan
    dat['pa_rk'] = np.nan
    dat['sig_resid'] = np.nan
    dat['fsc_rank'] = np.nan
    dat['pa_rank'] = np.nan
    dat['pa_ind_rank'] = np.nan
    grp = dat.groupby('sig_k', sort=False)
    # 十变量在 cohort(sig_k) 内的等权 z 合成
    zcols = []
    for v in PREREG['fund_vars']:
        dat['z_' + v] = np.nan
        zcols.append('z_' + v)
    for k, idx in grp.groups.items():
        d = dat.loc[idx]
        for v in PREREG['fund_vars']:
            dat.loc[idx, 'z_' + v] = zs(d[v]).values
        zmat = dat.loc[idx, zcols]
        cnt = zmat.notna().sum(axis=1)
        dat.loc[idx, 'fsc_z'] = np.where(cnt >= 4, zmat.sum(axis=1) / cnt, np.nan)
        dat.loc[idx, 'pa_z'] = zs(d['px_ret_rel']).values
        dat.loc[idx, 'pa_rank'] = rank_pct(d['px_ret_rel']).values
        dat.loc[idx, 'pa_ind_rank'] = rank_pct(d['px_ret_ind_rel']).values
        # 注意：必须读 dat（已写入 fsc_z），d 为本轮循环开始时的副本
        dat.loc[idx, 'fsc_rank'] = rank_pct(dat.loc[idx, 'fsc_z']).values
    # §24 残差化：FSC 对 PA 正交（逐 cohort）
    dat['sig_resid'] = np.nan
    for k, idx in dat.groupby('sig_k', sort=False).groups.items():
        d = dat.loc[idx]
        dat.loc[idx, 'sig_resid'] = ols_resid(d['fsc_z'].values,
                                              d['pa_z'].values)
    # §13/§14 象限
    dat['sig_quada'] = ((dat['fsc_rank'] >= 0.7) & (dat['pa_rank'] <= 0.6)).astype(float)
    dat.loc[dat['fsc_rank'].isna() | dat['pa_rank'].isna(), 'sig_quada'] = np.nan
    dat['sig_inter'] = dat['fsc_z'] * (-dat['pa_z'])
    # 方向：仅用 TRAIN 期（rep_year<=2022）确定，冻结后用于全样本
    tr = dat[dat['rep_year'].between(*PREREG['phases'][0][1:])]
    dirs = {}
    for c in ('fsc_z', 'sig_resid', 'sig_inter'):
        y = tr[c].values
        r = tr['ex_%d' % PREREG['primary_horizon']].values
        ic = spearman_ic(y, r)
        dirs[c] = 1.0 if (np.isfinite(ic) and ic >= 0) else -1.0
    dirs['sig_quada'] = 1.0
    log('  TRAIN 期方向判定: %s' % {k: int(v) for k, v in dirs.items()})
    for c in ('fsc_z', 'sig_resid', 'sig_inter'):
        dat[c + '_d'] = dat[c] * dirs[c]
    dat['sig_quada_d'] = dat['sig_quada']
    dat.attrs['dirs'] = dirs

    pd.Series(dirs).to_csv(os.path.join(DATA, 'hep_dirs.csv'))
    dat.to_parquet(os.path.join(DATA, 'hep_events.parquet'), index=False)
    log('=' * 72)
    log('已存 data/hep_events.parquet shape=%s  %.1fs' % (dat.shape, time.time() - t0))
    log('  Entry 分布: %s' % dat['E'].value_counts().to_dict())
    log('  年度×Entry 计数:')
    piv = dat.pivot_table(index='rep_year', columns='E', values='ts_code', aggfunc='count')
    for ln in piv.to_string().split('\n'):
        log('    ' + ln)
    log('  T+20 前向收益可用性: ' + str(dat.groupby('E')['ex_20'].apply(lambda s: round(s.notna().mean(), 4)).to_dict()))
    log('  T+60 前向收益可用性: ' + str(dat.groupby('E')['ex_60'].apply(lambda s: round(s.notna().mean(), 4)).to_dict()))
    log.save()
    print('DONE')


def clean(d, tag):
    for c in NUM.get(tag, []):
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors='coerce')
    return d


if __name__ == '__main__':
    main()
