# -*- coding: utf-8 -*-
"""hep_validate: H-EARN-POST 数据集校验（§3/§4/§5/§19/§35）

只读 data/hep_events.parquet，检查：
  1) 公告时间真实性（ann_date -> 首个可交易日 k1 的滞后）
  2) 无未来信息（时间轴单调：k0 < k1 <= E < E+1 <= Exit）
  3) 年度 x Entry 样本量与各 Horizon 前向收益可用性（含 2026 LIVE-LIKE 充分性）
  4) 关键变量覆盖率与分布
  5) 行业 / Regime 分布
  6) 内部一致性（ex_T == ret_T - idxret_T）
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hep_common import DATA, PREREG, ENTRIES, HORIZONS, FUND_VARS, PX_VARS, Log

log = Log('_hep_validate.txt')


def main():
    dat = pd.read_parquet(os.path.join(DATA, 'hep_events.parquet'))
    log('=' * 72)
    log('H-EARN-POST 数据集校验  shape=%s' % str(dat.shape))
    log('=' * 72)

    # ---------- 1) 公告时间真实性 ----------
    log('1) §4 公告时间真实性')
    dd = pd.to_numeric(dat['delay_days'], errors='coerce')
    log('  k1 相对 ann_date 的自然日滞后: n=%d  min=%s  p25=%s  中位=%s  p75=%s  p95=%s  max=%s'
        % (dd.notna().sum(), dd.min(), dd.quantile(.25), dd.median(),
           dd.quantile(.75), dd.quantile(.95), dd.max()))
    log('  滞后 > 10 自然日的比例 %.4f（长假/周末所致）' % float((dd > 10).mean()))
    log('  滞后 <= 0 的行数 %d（必须为 0：ann_date 当日为 D0，收盘不可用于交易）'
        % int((dd <= 0).sum()))
    log('  ann_date 为周末的比例 %.4f（公告可发生在非交易日）'
        % float(pd.to_datetime(dat['ann_date'], format='%Y%m%d', errors='coerce')
                .dt.dayofweek.isin([5, 6]).mean()))

    # ---------- 2) 无未来信息 ----------
    log('=' * 72)
    log('2) §5/§25 时间轴单调性（无未来信息）')
    k1 = dat['k1'].values
    k0 = dat['k0'].values
    e = dat['entry_k'].values
    bk = dat['buy_k'].values
    log('  k0 == k1-1 : %s' % bool(np.all(k0 == k1 - 1)))
    log('  entry_k - k1 == E : %s' % bool(np.all(e - k1 == dat['E'].values)))
    log('  buy_k - entry_k == 1 : %s' % bool(np.all(bk - e == 1)))

    # ---------- 3) 年度 x Entry x Horizon 可用性 ----------
    log('=' * 72)
    log('3) §3/§19 样本量与 Horizon 可用性')
    t = dat.pivot_table(index='rep_year', columns='E', values='ts_code', aggfunc='count')
    log('  记录数（年度 x Entry）:')
    for ln in t.fillna(0).astype(int).to_string().split('\n'):
        log('    ' + ln)
    log('  唯一股票数（年度）: %s' % dat.groupby('rep_year')['ts_code'].nunique().to_dict())
    log('')
    for T in HORIZONS:
        p = dat.pivot_table(index='rep_year', columns='E', values='ex_%d' % T,
                            aggfunc=lambda s: round(s.notna().mean(), 3))
        log('  T+%d 前向超额收益可用率（年度 x Entry）:' % T)
        for ln in p.fillna(0).to_string().split('\n'):
            log('    ' + ln)
    log('')
    log('  2026 LIVE-LIKE 明细:')
    d26 = dat[dat['rep_year'] == 2026]
    for T in HORIZONS:
        log('    2026 T+%d 可用: %s（n=%d）'
            % (T, d26.groupby('E')['ex_%d' % T].apply(lambda s: int(s.notna().sum())).to_dict(),
               len(d26)))

    # ---------- 4) 关键变量覆盖 ----------
    log('=' * 72)
    log('4) 变量覆盖率（非缺失比例）')
    for grp, cols in (('基本面主变量(§7)', FUND_VARS),
                      ('价格反应(§11/§12)', PX_VARS),
                      ('控制变量(§15–§18)',
                       ['mom_5', 'mom_10', 'mom_20', 'mom_60', 'pe_ttm', 'pb', 'ps_ttm',
                        'dv_ttm', 'total_mv', 'circ_mv', 'turnover', 'volratio', 'amt20']),
                      ('信号(§12–§14)', ['fsc_z', 'pa_z', 'sig_resid', 'sig_quada', 'sig_inter',
                                     'fsc_rank', 'pa_rank', 'pa_ind_rank']),
                      ('原始 surprise(§9)', ['S1_np_4', 'S1_np_8', 'S1_np_12', 'S2_np',
                                          'S3_np', 'S4_np_vs_rev', 'S5_np_vs_ocf'])):
        log('  [%s]' % grp)
        for c in cols:
            if c in dat.columns:
                log('    %-16s %.4f' % (c, float(dat[c].notna().mean())))
            else:
                log('    %-16s <MISSING>' % c)

    # ---------- 5) 行业 / Regime ----------
    log('=' * 72)
    log('5) 行业与 Regime（按 D0）')
    log('  SW L1 类数 %d（含 UNKNOWN）; 缺失率 %.4f'
        % (dat['ind_l1'].nunique(), float((dat['ind_l1'] == 'UNKNOWN').mean())))
    log('  SW L1 前 10 大: %s' % dat['ind_l1'].value_counts().head(10).to_dict())
    log('  Tushare 行业类数 %d; 缺失率 %.4f; 前 10 大: %s'
        % (dat['ind_tx'].nunique(), float((dat['ind_tx'] == 'UNKNOWN').mean()),
           dat['ind_tx'].value_counts().head(10).to_dict()))
    log('  Regime 分布: %s' % dat['regime'].value_counts().to_dict())
    log('  年度 x Regime:')
    p = dat.pivot_table(index='rep_year', columns='regime', values='ts_code', aggfunc='count')
    for ln in p.fillna(0).astype(int).to_string().split('\n'):
        log('    ' + ln)

    # ---------- 6) 数值一致性 + 极端值 ----------
    log('=' * 72)
    log('6) 数值一致性与极端值抽查')
    for T in HORIZONS:
        d = (dat['ex_%d' % T] - (dat['ret_%d' % T] - dat['idxret_%d' % T])).abs().max()
        log('  max|ex_%d - (ret-idxret)| = %.2e' % (T, float(d) if np.isfinite(d) else np.nan))
    for c in ('px_ret', 'mom_20', 'fsc_z'):
        s = dat[c]
        log('  %-10s  min=%s  p1=%s  中位=%s  p99=%s  max=%s'
            % (c, round(s.min(), 4), round(s.quantile(.01), 4), round(s.median(), 4),
               round(s.quantile(.99), 4), round(s.max(), 4)))
    log('')
    log('  E20/E25/E30 是否为同一 stocks 集的 3 个副本：%s'
        % sorted(dat.groupby('E')['ts_code'].nunique().to_dict().items()))
    log('  前向收益横截面样本量（每日）中位: %s'
        % dat.groupby('sig_k')['ex_20'].apply(lambda s: int(s.notna().sum()))
        .median())
    n_small = int((dat.groupby('sig_k')['ex_20'].apply(
        lambda s: int(s.notna().sum())) < 20).sum())
    log('  符合条件的决策日(>=20 横截面样本) 数=%d / 总决策日=%d'
        % (dat.groupby('sig_k')['ex_20'].apply(lambda s: int(s.notna().sum()))
           .ge(20).sum(), dat['sig_k'].nunique()))

    # ---------- 7) 极端值抽查（判断是否数据异常） ----------
    log('=' * 72)
    log('7) 极端值抽查（px_ret 最大/最小 8 行）')
    cols = ['ts_code', 'rep_year', 'E', 'cal_date_k1', 'ann_date', 'ind_tx',
            'px_ret', 'px_ret_rel', 'fsc_z']
    ext = pd.concat([dat.nlargest(8, 'px_ret'), dat.nsmallest(8, 'px_ret')])
    for ln in ext[cols].to_string().split('\n'):
        log('    ' + ln)

    log.save()
    print('DONE')


if __name__ == '__main__':
    main()
