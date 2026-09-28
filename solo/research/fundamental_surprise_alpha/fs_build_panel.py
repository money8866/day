# -*- coding: utf-8 -*-
"""fs_build_panel: 事件 × 价格反应 × 控制变量 × 前向收益（§4/§5/§6/§13/§14/§20）

时间轴（严格 as-of，最保守口径）
  ANNOUNCE_TIME  = ann_date（仅日期粒度；假设信息最早只能在 ann_date 收盘后才公开）
  k0 = ann_date 当日或之前最后一个交易日  → 公告前参照收盘 C0（动量/估值等控制变量均取此点）
  k1 = ann_date 之后第一个交易日          → 首个可反应、且最早可交易的交易日
  SIGNAL_TIME    = ann_date（不晚于 k1 开盘）
  EARLIEST_ENTRY = k1 开盘（E1）；备用锚点 E2=k1 收盘 / E3=k1+2 收盘 / E5=k1+4 收盘

  Gap      = O(k1)/C(k0) - 1          仅此一项在 E1 开盘时已知
  Ret_R1   = C(k1)/preC(k1) - 1       E2 收盘时已知（§14 的 Return_T0 对应量）
  Intra_R1 = C(k1)/O(k1) - 1
  Pos_R1   = (C-L)/(H-L) at k1
  VolR_R1  = V(k1)/MA20(V)(k0)        公告前基准量能，避免用当日量污染
  Cum_R3   = C(k1+2)/C(k0) - 1
  Cum_R5   = C(k1+4)/C(k0) - 1

  说明：不采用「ann_date 当日涨跌幅」作为价格反应——该日收盘发生在公告之前，
  属于公告前信息，若用作反应变量即为未来信息污染（§5）。
"""
import os
import sys
import glob
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fs_common import DATA, CD, HORIZONS, period_of, Log

log = Log('_fs_build_panel.txt')

F32 = ['open', 'close', 'pre_close', 'high', 'low', 'vol', 'amount',
       'qfq_open', 'qfq_close', 'qfq_high', 'qfq_low']
BASIC_KEEP = ['turnover_rate', 'volume_ratio', 'pe', 'pe_ttm', 'pb', 'ps', 'ps_ttm',
              'dv_ratio', 'dv_ttm', 'total_mv', 'circ_mv']


def load_industry():
    m, fb = {}, {}
    fp = os.path.join(CD, 'industry', 'sw_industry_map.csv')
    if os.path.exists(fp):
        d = pd.read_csv(fp, dtype=str)
        d = d[d['ts_code'].notna()].sort_values('in_date')
        m = dict(zip(d['ts_code'], d['l1_name']))
        log('  行业(SW L1) 覆盖 %d 只' % len(m))
    d2 = pd.read_csv(os.path.join(CD, 'stock_basic.csv'), dtype=str)
    fb = dict(zip(d2['ts_code'], d2['industry']))
    log('  行业兜底(stock_basic.industry) 覆盖 %d 只' % len(fb))
    return m, fb


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
    return d


def idx_pct(lvl):
    r = np.full(len(lvl), np.nan)
    r[1:] = lvl[1:] / lvl[:-1] - 1.0
    return r


def main():
    ev = pd.read_parquet(os.path.join(DATA, 'events_base.parquet'))
    log('事件表: %d 行 / %d 只  %s ~ %s' % (
        len(ev), ev['ts_code'].nunique(), ev['ann_date'].min(), ev['ann_date'].max()))

    cal = pd.read_parquet(os.path.join(DATA, 'calendar.parquet'))
    td = cal['trade_date'].values
    NCAL = len(cal)
    log('交易日历: %d 天  %s ~ %s' % (NCAL, td[0], td[-1]))

    # ---------- 1) 价格派生（一律基于 k0 收盘，公告前信息） ----------
    log('=' * 60)
    log('1) 价格派生')
    px = pd.read_parquet(os.path.join(DATA, 'price_panel.parquet'),
                         columns=['ts_code', 'trade_date'] + F32)
    for c in F32:
        px[c] = px[c].astype('float32')
    kmap = pd.Series(np.arange(NCAL), index=td)
    px['k'] = px['trade_date'].map(kmap)
    px = px[px['k'].notna()].copy()
    px['k'] = px['k'].astype('int32')
    px = px.sort_values(['ts_code', 'k']).reset_index(drop=True)
    log('  价格行=%d 股票=%d' % (len(px), px['ts_code'].nunique()))

    g = px.groupby('ts_code', sort=False)
    for n in (5, 10, 20, 60):
        px['mom_%d' % n] = (px['qfq_close'] / g['qfq_close'].shift(n) - 1.0).astype('float32')
    px['vol_ma20_prior'] = g['vol'].transform(
        lambda s: s.shift(1).rolling(20, min_periods=10).mean()).astype('float32')
    px['ret_1'] = (px['close'] / px['pre_close'] - 1.0).astype('float32')
    px['intraday'] = (px['close'] / px['open'] - 1.0).astype('float32')
    rng = (px['high'] - px['low']).astype('float64')
    px['closepos'] = np.where(rng > 0, (px['close'] - px['low']) / rng.replace(0, np.nan), 0.5)
    px['volratio'] = (px['vol'] / px['vol_ma20_prior']).astype('float32')

    # 指数
    idx = pd.read_parquet(os.path.join(DATA, 'index_panel.parquet')).sort_values('trade_date')
    idx['k'] = idx['trade_date'].map(kmap)
    idx = idx[idx['k'].notna()].copy()
    idx['k'] = idx['k'].astype(int)
    lvl = np.full(NCAL, np.nan)
    lvl[idx['k'].values] = pd.to_numeric(idx['close'], errors='coerce').values
    lvl = pd.Series(lvl).ffill().bfill().values
    pd.DataFrame({'trade_date': td, 'idx_level': lvl}).to_parquet(
        os.path.join(DATA, 'index_level.parquet'), index=False)
    ipct = idx_pct(lvl)
    log('  指数水平: %d 天  %.1f ~ %.1f' % (len(lvl), np.nanmin(lvl), np.nanmax(lvl)))

    # ---------- 2) 事件锚点：k1 = ann_date 之后首个交易日 ----------
    log('=' * 60)
    log('2) 事件锚点对齐（k1 = ann_date 之后首个交易日）')
    k1 = np.searchsorted(td, ev['ann_date'].values, side='right')
    ev = ev.copy()
    ev['k1'] = k1
    ev['k0'] = k1 - 1
    okk = (ev['k1'] >= 1) & (ev['k1'] <= NCAL - 1)
    log('  公告日非交易日/超界导致无法映射: %d' % int((~okk).sum()))
    ev = ev[okk].copy()
    ev['k1'] = ev['k1'].astype(int)
    ev['k0'] = ev['k0'].astype(int)
    ev['cal_date_k1'] = td[ev['k1'].values]
    ev['delay_days'] = (pd.to_datetime(ev['cal_date_k1'], format='%Y%m%d')
                        - pd.to_datetime(ev['ann_date'], format='%Y%m%d')).dt.days
    log('  保留事件 %d / %d 只；k1 滞后自然日中位=%.0f p90=%.0f' % (
        len(ev), ev['ts_code'].nunique(), ev['delay_days'].median(),
        ev['delay_days'].quantile(0.9)))

    pr = px[['ts_code', 'k', 'qfq_open', 'qfq_close', 'open', 'close', 'pre_close',
             'high', 'low', 'qfq_high', 'qfq_low', 'vol', 'amount', 'ret_1',
             'intraday', 'closepos', 'volratio'] +
            ['mom_5', 'mom_10', 'mom_20', 'mom_60']].copy()
    pr['k'] = pr['k'].astype('int32')

    def at(base, off, cols, tag):
        """取交易日索引 k1+off 的价格数据（off 可为负）"""
        t = pr[['ts_code', 'k'] + cols].copy()
        t['k'] = t['k'] - off
        t = t.rename(columns={c: '%s_%s' % (tag, c) for c in cols})
        t = t.rename(columns={'k': 'k1'})
        return base.merge(t, on=['ts_code', 'k1'], how='left')

    PCOLS = ['qfq_open', 'qfq_close', 'open', 'close', 'pre_close',
             'high', 'low', 'qfq_high', 'qfq_low', 'vol', 'amount']
    ev = at(ev, -1, PCOLS + ['ret_1', 'intraday', 'closepos', 'volratio'],
            'K0')
    ev = ev.drop(columns=[c for c in ('k',) if c in ev.columns])
    ev = at(ev, 0, PCOLS + ['ret_1', 'intraday', 'closepos', 'volratio'],
            'K1')
    ev = ev.drop(columns=[c for c in ('k',) if c in ev.columns])
    ev = at(ev, 2, ['qfq_close', 'close'], 'K3')
    ev = ev.drop(columns=[c for c in ('k',) if c in ev.columns])
    ev = at(ev, 4, ['qfq_close', 'close'], 'K5')
    ev = ev.drop(columns=[c for c in ('k',) if c in ev.columns])

    # 公告前控制变量：动量 / 估值 / 规模 / 流动性（均取 k0）
    mom = px[['ts_code', 'k', 'mom_5', 'mom_10', 'mom_20', 'mom_60']].copy()
    mom['k'] = mom['k'].astype('int32')
    mom['k'] = mom['k'] + 1
    ev = ev.merge(mom.rename(columns={'k': 'k1'}), on=['ts_code', 'k1'], how='left')
    ev['close_t0'] = ev['K0_close']
    ev['preclose_t0'] = ev['K0_pre_close']
    ev['vol_t0'] = ev['K0_vol']
    ev['amount_t0'] = ev['K0_amount']

    bas = pd.read_parquet(os.path.join(DATA, 'basic_panel.parquet'))
    bas = bas[['ts_code', 'trade_date'] + [c for c in BASIC_KEEP if c in bas.columns]].copy()
    bas['k'] = bas['trade_date'].map(kmap)
    bas = bas[bas['k'].notna()].copy()
    bas['k'] = bas['k'].astype('int32')
    for c in BASIC_KEEP:
        if c in bas.columns:
            bas[c] = pd.to_numeric(bas[c], errors='coerce').astype('float32')
    bas = bas.drop(columns=['trade_date'])
    bas['k'] = bas['k'] + 1
    ev = ev.merge(bas.rename(columns={'k': 'k1'}), on=['ts_code', 'k1'], how='left')

    # ---------- 3) 价格反应（§14，全部为公告后已发生数据） ----------
    log('=' * 60)
    log('3) 价格反应变量')
    ev['Gap'] = ev['K1_open'] / ev['close_t0'] - 1.0
    ev['Ret_R1'] = ev['K1_ret_1']
    ev['Intra_R1'] = ev['K1_intraday']
    ev['Pos_R1'] = ev['K1_closepos']
    ev['VolR_R1'] = ev['K1_volratio']
    ev['Cum_R3'] = ev['K3_close'] / ev['close_t0'] - 1.0
    ev['Cum_R5'] = ev['K5_close'] / ev['close_t0'] - 1.0
    idx_gap = lvl[np.clip(ev['k1'].values, 0, NCAL - 1)] / \
        lvl[np.clip(ev['k0'].values, 0, NCAL - 1)] - 1.0
    ev['RS_R1'] = ev['Ret_R1'] - ipct[np.clip(ev['k1'].values, 0, NCAL - 1)]
    ev['RS_Gap'] = ev['Gap'] - idx_gap
    ev['RS_Cum3'] = ev['Cum_R3'] - (lvl[np.clip(ev['k1'].values + 2, 0, NCAL - 1)]
                                    / lvl[np.clip(ev['k0'].values, 0, NCAL - 1)] - 1.0)

    # ---------- 4) 入口锚点与前向收益 ----------
    log('=' * 60)
    log('4) 入口锚点 E1/E2/E3/E5 与前向收益（相对沪深300 超额）')
    ev['entry_px_E1'] = ev['K1_qfq_open']          # k1 开盘
    ev['entry_px_E2'] = ev['K1_qfq_close']         # k1 收盘
    ev['entry_px_E3'] = ev['K3_qfq_close']         # k1+2 收盘
    ev['entry_px_E5'] = ev['K5_qfq_close']         # k1+4 收盘
    OFF = {'E1': 0, 'E2': 0, 'E3': 2, 'E5': 4}
    for h in HORIZONS:
        for tag, off in OFF.items():
            ex = pr[['ts_code', 'k', 'qfq_close']].copy()
            ex['k'] = ex['k'] - (off + h)
            ex = ex.rename(columns={'qfq_close': 'XF_%d' % h, 'k': 'k1'})
            b = ev.merge(ex, on=['ts_code', 'k1'], how='left')
            ev['ret_%s_T%d' % (tag, h)] = b['XF_%d' % h].values / ev['entry_px_%s' % tag].values - 1.0
            ka = np.clip(ev['k1'].values + off, 0, NCAL - 1)
            kb = np.clip(ev['k1'].values + off + h, 0, NCAL - 1)
            ev['idxret_%s_T%d' % (tag, h)] = lvl[kb] / lvl[ka] - 1.0
            ev['ex_%s_T%d' % (tag, h)] = (ev['ret_%s_T%d' % (tag, h)].values
                                          - ev['idxret_%s_T%d' % (tag, h)].values)
    log('  前向收益覆盖: E1T5=%.3f E1T20=%.3f E3T5=%.3f E5T5=%.3f' % (
        ev['ret_E1_T5'].notna().mean(), ev['ret_E1_T20'].notna().mean(),
        ev['ret_E3_T5'].notna().mean(), ev['ret_E5_T5'].notna().mean()))

    # ---------- 5) 可交易与样本过滤（§6） ----------
    log('=' * 60)
    log('5) 可交易与样本过滤')
    n0 = len(ev)
    okE = (ev['entry_px_E1'].notna() & (ev['entry_px_E1'] > 0)
           & ev['K1_vol'].notna() & (ev['K1_vol'] > 0)
           & ev['close_t0'].notna() & (ev['close_t0'] > 0))
    ev = ev[okE].copy()
    log('  E1 不可交易/公告前无收盘剔除 %d -> %d' % (n0 - len(ev), len(ev)))

    n1 = len(ev)
    ev = ev[ev['mom_60'].notna()].copy()
    log('  上市过短（无 60 日动量）剔除 %d -> %d' % (n1 - len(ev), len(ev)))

    nc = load_name_asof()
    if nc is not None and len(nc):
        ev = ev.reset_index(drop=True)
        ev['_i'] = np.arange(len(ev))
        mm = ev[['_i', 'ts_code', 'ann_date']].merge(nc, on='ts_code', how='left')
        mm = mm[(mm['start_date'] <= mm['ann_date']) & (mm['ann_date'] <= mm['end_date'])]
        mm = mm.drop_duplicates('_i')
        ev = ev.merge(mm[['_i', 'name']].rename(columns={'name': 'name_at_ev'}),
                      on='_i', how='left').drop(columns=['_i'])
        bad = ev['name_at_ev'].astype(str).str.contains('ST|退', na=False)
        log('  ST/退市剔除 %d（名称可得 %d）' % (int(bad.sum()), int(ev['name_at_ev'].notna().sum())))
        ev = ev[~bad].copy()
    else:
        ev['name_at_ev'] = np.nan

    n2 = len(ev)
    lu = (ev['K1_open'] >= ev['K1_pre_close'] * 1.095)
    ld = (ev['K1_open'] <= ev['K1_pre_close'] * 0.905)
    ev = ev[~lu & ~ld].copy()
    log('  E1 一字板剔除 %d -> %d' % (n2 - len(ev), len(ev)))

    # ---------- 6) 行业 / 分期 ----------
    log('=' * 60)
    log('6) 行业与分期')
    sw, fb = load_industry()
    ev['ind_l1'] = ev['ts_code'].map(sw).fillna(ev['ts_code'].map(fb))
    ev['period'] = ev['ann_date'].map(period_of)
    ev['year'] = ev['ann_date'].str[:4]
    log('  行业缺失率: %.4f' % ev['ind_l1'].isna().mean())

    # ---------- 7) S3 行业相对 Surprise（expanding，仅用已公告同业） ----------
    log('=' * 60)
    log('7) S3 行业相对 Surprise')
    ev = ev.sort_values(['end_date', 'ind_l1', 'ann_date']).reset_index(drop=True)
    for src, tag in (('rev_yoy', 'rev'), ('np_yoy', 'np')):
        ev['S3_%s' % tag] = ev[src] - ev.groupby(['end_date', 'ind_l1'])[src].transform(
            lambda s: s.expanding(min_periods=5).median())

    keep_drop = [c for c in ev.columns if c.startswith(('K0_', 'K1_', 'K3_', 'K5_'))]
    ev = ev.drop(columns=keep_drop)
    ev.to_parquet(os.path.join(DATA, 'panel.parquet'), index=False)
    log('已存 data/panel.parquet  shape=%s' % (ev.shape,))
    log('  分期: %s' % ev['period'].value_counts().to_dict())
    log('  年度: %s' % ev['year'].value_counts().sort_index().to_dict())
    log('  公司数: %d' % ev['ts_code'].nunique())
    log.save()
    print('DONE')


if __name__ == '__main__':
    main()
