# -*- coding: utf-8 -*-
"""fs_build_panel: 事件 × 价格反应 × 控制变量 × 前向收益（§4/§5/§6/§13/§14/§20）

时间轴（严格 as-of）
  ANNOUNCE_TIME  = ann_date (D0)
  SIGNAL_TIME    = D0 收盘后
  EARLIEST_ENTRY = D0 之后首个交易日开盘 (E1)
  其它锚点       = D0+1 收盘 (E2) / D0+3 收盘 (E3)
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


def main():
    ev = pd.read_parquet(os.path.join(DATA, 'events_base.parquet'))
    log('事件表: %d 行 / %d 只  %s ~ %s' % (
        len(ev), ev['ts_code'].nunique(), ev['ann_date'].min(), ev['ann_date'].max()))

    cal = pd.read_parquet(os.path.join(DATA, 'calendar.parquet'))
    kmap = dict(zip(cal['trade_date'], cal['idx']))
    NCAL = len(cal)
    log('交易日历: %d 天' % NCAL)

    # ---------- 1) 价格派生 ----------
    log('=' * 60)
    log('1) 价格派生（动量/量比/位置）')
    px = pd.read_parquet(os.path.join(DATA, 'price_panel.parquet'))
    px = px[['ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'pre_close',
             'vol', 'amount', 'qfq_open', 'qfq_close', 'qfq_high', 'qfq_low']].copy()
    for c in F32:
        px[c] = px[c].astype('float32')
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

    # 指数（基准 / Regime）
    idx = pd.read_parquet(os.path.join(DATA, 'index_panel.parquet')).sort_values('trade_date')
    idx['k'] = idx['trade_date'].map(kmap)
    idx = idx[idx['k'].notna()].copy()
    idx['k'] = idx['k'].astype(int)
    lvl = np.full(NCAL, np.nan)
    lvl[idx['k'].values] = pd.to_numeric(idx['close'], errors='coerce').values
    lvl = pd.Series(lvl).ffill().bfill().values
    pd.DataFrame({'trade_date': cal['trade_date'].values, 'idx_level': lvl}).to_parquet(
        os.path.join(DATA, 'index_level.parquet'), index=False)
    log('  指数水平: %d 天  %.1f ~ %.1f' % (len(lvl), np.nanmin(lvl), np.nanmax(lvl)))

    # ---------- 2) 事件锚点 ----------
    log('=' * 60)
    log('2) 事件锚点对齐')
    ev['k'] = ev['ann_date'].map(kmap)
    miss = int(ev['k'].isna().sum())
    ev = ev[ev['k'].notna()].copy()
    ev['k'] = ev['k'].astype(int)
    log('  ann_date 非交易日剔除 %d；剩余 %d 事件 / %d 只' % (miss, len(ev), ev['ts_code'].nunique()))

    # ---------- 3) D0 快照（价格反应 + 估值 + 动量） ----------
    snap = px[['ts_code', 'k', 'ret_1', 'intraday', 'closepos', 'volratio', 'vol',
               'amount', 'close', 'pre_close']].rename(columns={
        'ret_1': 'Return_T0', 'intraday': 'IntradayReturn', 'closepos': 'ClosePosition',
        'volratio': 'VolumeRatio', 'vol': 'vol_t0', 'amount': 'amount_t0',
        'close': 'close_t0', 'pre_close': 'preclose_t0'}).copy()
    snap['k'] = snap['k'].astype('int32')
    ev = ev.merge(snap, on=['ts_code', 'k'], how='left')

    bas = pd.read_parquet(os.path.join(DATA, 'basic_panel.parquet'))
    bas = bas[['ts_code', 'trade_date'] + [c for c in BASIC_KEEP if c in bas.columns]].copy()
    bas['k'] = bas['trade_date'].map(kmap)
    bas = bas[bas['k'].notna()].copy()
    bas['k'] = bas['k'].astype('int32')
    for c in BASIC_KEEP:
        if c in bas.columns:
            bas[c] = pd.to_numeric(bas[c], errors='coerce').astype('float32')
    ev = ev.merge(bas.drop(columns=['trade_date']), on=['ts_code', 'k'], how='left')
    log('  D0 快照匹配: pe_ttm=%.3f pb=%.3f total_mv=%.3f' % (
        ev['pe_ttm'].notna().mean(), ev['pb'].notna().mean(), ev['total_mv'].notna().mean()))

    mom = px[['ts_code', 'k', 'mom_5', 'mom_10', 'mom_20', 'mom_60']].copy()
    mom['k'] = mom['k'].astype('int32')
    ev = ev.merge(mom, on=['ts_code', 'k'], how='left')
    ev['idx_ret_t0'] = ev['k'].map(dict(zip(cal['idx'].astype(int), idx_pct(lvl))))
    ev['RelativeStrength_T0'] = ev['Return_T0'] - ev['idx_ret_t0']

    # ---------- 4) 入口锚点与前向收益 ----------
    log('=' * 60)
    log('4) 入口锚点（E1=D0+1开盘 / E2=D0+1收盘 / E3=D0+3收盘）与前向收益')
    pr = px[['ts_code', 'k', 'qfq_open', 'qfq_close', 'open', 'close', 'pre_close',
             'high', 'low', 'qfq_high', 'qfq_low', 'vol']].copy()
    pr['k'] = pr['k'].astype('int32')

    def at_offset(base, off, cols, tag):
        t = pr[['ts_code', 'k'] + cols].copy()
        t['k'] = t['k'] - off
        t = t.rename(columns={c: '%s_%s' % (tag, c) for c in cols})
        return base.merge(t, on=['ts_code', 'k'], how='left')

    ev = at_offset(ev, 1, ['qfq_open', 'qfq_close', 'open', 'close', 'pre_close',
                           'high', 'low', 'qfq_high', 'qfq_low', 'vol'], 'E1')
    ev = at_offset(ev, 3, ['qfq_close', 'close', 'pre_close', 'vol'], 'E3')
    ev = at_offset(ev, 5, ['qfq_close', 'close'], 'E5')

    ev['entry_px_E1'] = ev['E1_qfq_open']
    ev['entry_px_E2'] = ev['E1_qfq_close']
    ev['entry_px_E3'] = ev['E3_qfq_close']
    ev['Gap'] = ev['E1_open'] / ev['close_t0'] - 1.0
    ev['IntradayReturn_T1'] = ev['E1_close'] / ev['E1_open'] - 1.0
    ev['Return_T1'] = ev['E1_close'] / ev['E1_pre_close'] - 1.0
    ev['Return_T3'] = ev['E3_close'] / ev['close_t0'] - 1.0
    ev['Return_T5'] = ev['E5_close'] / ev['close_t0'] - 1.0
    ev['PR1'] = ev['Return_T0']
    ev['PR3'] = ev['E3_close'] / ev['preclose_t0'] - 1.0
    ev['PR5'] = ev['E5_close'] / ev['preclose_t0'] - 1.0

    for h in HORIZONS:
        ev = at_offset(ev, 1 + h, ['qfq_close'], 'X1_%d' % h)
        ev = at_offset(ev, 3 + h, ['qfq_close'], 'X3_%d' % h)
        for tag, ecol in (('E1', 'entry_px_E1'), ('E2', 'entry_px_E2'), ('E3', 'entry_px_E3')):
            xc = 'X1_%d_qfq_close' % h if tag in ('E1', 'E2') else 'X3_%d_qfq_close' % h
            off = 1 if tag in ('E1', 'E2') else 3
            ev['ret_%s_T%d' % (tag, h)] = ev[xc] / ev[ecol] - 1.0
            ka = np.clip(ev['k'].values + off, 0, NCAL - 1)
            kb = np.clip(ev['k'].values + off + h, 0, NCAL - 1)
            ev['idxret_%s_T%d' % (tag, h)] = lvl[kb] / lvl[ka] - 1.0
            ev['ex_%s_T%d' % (tag, h)] = ev['ret_%s_T%d' % (tag, h)] - ev['idxret_%s_T%d' % (tag, h)]
    log('  前向收益覆盖: E1T5=%.3f E1T20=%.3f E3T5=%.3f' % (
        ev['ret_E1_T5'].notna().mean(), ev['ret_E1_T20'].notna().mean(),
        ev['ret_E3_T5'].notna().mean()))

    # ---------- 5) 可交易与样本过滤（§6） ----------
    log('=' * 60)
    log('5) 可交易与样本过滤')
    n0 = len(ev)
    okE = ev['entry_px_E1'].notna() & (ev['entry_px_E1'] > 0) & ev['E1_vol'].notna() & (ev['E1_vol'] > 0)
    ev = ev[okE].copy()
    log('  E1 入口不可交易剔除 %d -> %d' % (n0 - len(ev), len(ev)))

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
    lu = (ev['E1_open'] >= ev['E1_pre_close'] * 1.095)
    ld = (ev['E1_open'] <= ev['E1_pre_close'] * 0.905)
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

    # ---------- 7) 行业相对 Surprise（expanding，仅用已公告同业） ----------
    log('=' * 60)
    log('7) S3 行业相对 Surprise')
    ev = ev.sort_values(['end_date', 'ind_l1', 'ann_date']).reset_index(drop=True)
    for src, tag in (('rev_yoy', 'rev'), ('np_yoy', 'np')):
        ev['S3_%s' % tag] = ev[src] - ev.groupby(['end_date', 'ind_l1'])[src].transform(
            lambda s: s.expanding(min_periods=5).median())

    ev.to_parquet(os.path.join(DATA, 'panel.parquet'), index=False)
    log('已存 data/panel.parquet  shape=%s' % (ev.shape,))
    log('  分期: %s' % ev['period'].value_counts().to_dict())
    log('  年度: %s' % ev['year'].value_counts().sort_index().to_dict())
    log('  公司数: %d' % ev['ts_code'].nunique())
    log.save()
    print('DONE')


def idx_pct(lvl):
    r = np.full(len(lvl), np.nan)
    r[1:] = lvl[1:] / lvl[:-1] - 1.0
    return r


if __name__ == '__main__':
    main()
