# -*- coding: utf-8 -*-
"""
H-TREND-PULLBACK-01  P1：基础面板构建

覆盖规格：§4 数据源 / §5 Universe / §6 Cohort 前提 / §7-§8 趋势与强度特征
产出：data/panel/*.npy（code-major，形状 (NCODES, NCAL)） + _day.npz + _meta.json

列清单
  float32 2D : open high low close pre_close vol amount turnover vr total_mv circ_mv
               ma5 ma10 ma20 ma60 slope20 slope60
               ret_1 ret_3 ret_5 ret_10 ret_20 ret_60        (ret_20=MOM20, ret_60=MOM60)
               atr5 atr10 atr14 atr20 atr_pct rsi14 rv_20
               v_ma5 v_ma20 v_ratio peak20 dd_60 pos_60 pos_120
  int8   2D : st_flag dl_flag nh20 valid reason
  int32  1D : _code_klist          (上市日 -> 历法索引, -1 = 未知)
  int16  1D : _code_ind_l1         (SW L1 编码, 0 = UNKNOWN)
  int8   1D : _code_is_bj
  float32 2D: _ind_sum _ind_cnt    ((NIND, NCAL) 行业等权收益分子/分母, 支持 leave-one-out)
  _day.npz  : dates year month regime phase + 各基准 ret_k

口径说明（PIT）
  - 全部使用前复权价 (qfq_*)；rolling 一律 min_periods=w，绝不使用未来数据。
  - peak20 = 前 20 日最高价（不含当日），即 §10 的 Peak，严格因果。
  - slope 为归一化线性回归斜率（除以 MA 水平），符号与未归一化一致。
"""
import os
import sys
import glob
import time
import json

import numpy as np
import pandas as pd
from numpy.lib.format import open_memmap

from hp_common import (DATA, PAN, OUTD, FS_DATA, CD, PREREG, mklog,
                       psave, psave_meta, psave_day, dump_prereg, prereg_hash,
                       roll_slope, phase_of)

LOG = mklog('panel')

PCOLS = ['qfq_open', 'qfq_high', 'qfq_low', 'qfq_close', 'qfq_pre_close',
         'open', 'pre_close', 'vol', 'amount']
BCOLS = ['total_mv', 'circ_mv', 'turnover_rate', 'volume_ratio']

F32 = ['open', 'high', 'low', 'close', 'pre_close', 'vol', 'amount',
       'turnover', 'vr', 'total_mv', 'circ_mv',
       'ma5', 'ma10', 'ma20', 'ma60', 'slope20', 'slope60',
       'ret_1', 'ret_3', 'ret_5', 'ret_10', 'ret_20', 'ret_60',
       'atr5', 'atr10', 'atr14', 'atr20', 'atr_pct', 'rsi14', 'rv_20',
       'v_ma5', 'v_ma20', 'v_ratio',
       'peak20', 'dd_60', 'pos_60', 'pos_120']

I8_MEM = ['st_flag', 'dl_flag', 'nh20']
I8 = I8_MEM + ['valid', 'reason']

BENCH = {'000300.SH': 'hs300', '000852.SH': 'csi1000',
         '000001.SH': 'sse', '000905.SH': 'csi500'}
RETW = (1, 3, 5, 10, 20, 60)


# ─────────────────────────────────────────────────────────── 静态元信息
def load_meta(td, NCAL, codes):
    list_date, ind_tx = {}, {}
    fp = os.path.join(CD, 'stock_basic.csv')
    if os.path.exists(fp):
        d = pd.read_csv(fp, dtype=str)
        list_date = dict(zip(d['ts_code'], d['list_date']))
        if 'industry' in d.columns:
            ind_tx = dict(zip(d['ts_code'], d['industry']))

    sw_raw, sw_nm = {}, {}
    fp = os.path.join(CD, 'industry', 'sw_industry_map.csv')
    if os.path.exists(fp):
        d = pd.read_csv(fp, dtype=str)
        d = d[d['ts_code'].notna() & d['l1_name'].notna()]
        # 预注册：以最早 in_date 归类（O(1) 稳定，避免个股行业漂移）
        d = d.sort_values('in_date').drop_duplicates('ts_code', keep='first')
        sw_raw = dict(zip(d['ts_code'], d['l1_name']))
        sw_nm = dict(zip(d['ts_code'], d.get('l1_code', d['l1_name'])))
    cats = sorted(set(sw_raw.values()))
    enc = {c: i + 1 for i, c in enumerate(cats)}          # 0 = UNKNOWN
    ind_l1 = np.array([enc.get(sw_raw.get(c), 0) for c in codes], dtype=np.int16)
    pd.DataFrame({'id': list(range(len(cats) + 1)),
                  'name': ['UNKNOWN'] + cats}
                 ).to_csv(os.path.join(DATA, 'hp_ind_l1_map.csv'), index=False)
    LOG('  元信息: stock_basic %d 只；SW L1 覆盖 %d / %d（UNKNOWN %d），行业数 %d'
        % (len(list_date), int((ind_l1 > 0).sum()), len(codes),
           int((ind_l1 == 0).sum()), len(cats)))

    k_list = np.full(len(codes), -1, dtype=np.int32)
    for i, c in enumerate(codes):
        ld = list_date.get(c)
        if isinstance(ld, str) and len(ld) == 8 and ld.isdigit():
            k_list[i] = int(np.searchsorted(td, ld, side='left'))
    LOG('  上市日可得 %d / %d' % (int((k_list >= 0).sum()), len(codes)))

    # ---- 时点 ST / 退市（名称变更）----
    n = len(codes)
    cmap = pd.Series(np.arange(n), index=codes)
    STM = np.zeros((n, NCAL), dtype=np.int8)
    DLM = np.zeros((n, NCAL), dtype=np.int8)
    frames = []
    for fp in glob.glob(os.path.join(CD, 'treasure_namechg_*.parquet')):
        try:
            d = pd.read_parquet(fp)
            if len(d):
                frames.append(d[['ts_code', 'name', 'start_date', 'end_date']])
        except Exception:
            pass
    nrec = 0
    if frames:
        d = pd.concat(frames, ignore_index=True)
        d['ts_code'] = d['ts_code'].astype(str)
        for c in ('start_date', 'end_date'):
            s = d[c].astype(str).str.replace('.0', '', regex=False)
            s = s.where(s.str.match(r'^\d{8}$'), '19000101')
            d[c] = s
        d['end_date'] = d['end_date'].where(d['end_date'] != '19000101', '99999999')
        d = d.sort_values(['ts_code', 'start_date']).drop_duplicates(
            ['ts_code', 'start_date'], keep='last')
        nm = d['name'].astype(str)
        st_f = nm.str.contains('ST', na=False).values
        dl_f = nm.str.contains('退', na=False).values
        ci = d['ts_code'].map(cmap).values
        k1 = np.searchsorted(td, d['start_date'].values, side='left')
        k2 = np.searchsorted(td, d['end_date'].values, side='right') - 1
        for j in range(len(d)):
            if not np.isfinite(ci[j]):
                continue
            a, b = int(max(k1[j], 0)), int(min(k2[j], NCAL - 1))
            if b < a:
                continue
            if st_f[j]:
                STM[int(ci[j]), a:b + 1] = 1
            if dl_f[j]:
                DLM[int(ci[j]), a:b + 1] = 1
            nrec += 1
    LOG('  名称变更 %d 条（时点 ST %.4f，退市整理 %.4f）'
        % (nrec, float(STM.mean()), float(DLM.mean())))
    return ind_l1, k_list, STM, DLM, len(cats)


# ─────────────────────────────────────────────────────────── 基准指数
def load_bench(kmap, NCAL):
    idx = pd.read_parquet(os.path.join(FS_DATA, 'index_panel.parquet'))
    idx['trade_date'] = idx['trade_date'].astype(str)
    avail = sorted(set(idx['ts_code'].astype(str).values))
    lv, got = {}, {}
    for code, nm in BENCH.items():
        sub = idx[idx['ts_code'].astype(str) == code]
        if len(sub) == 0:
            lv[nm] = None
            got[nm] = False
            continue
        sub = sub.copy()
        sub['k'] = sub['trade_date'].map(kmap)
        sub = sub[sub['k'].notna()].sort_values('k')
        z = np.full(NCAL, np.nan)
        z[sub['k'].values.astype(int)] = pd.to_numeric(sub['close'], errors='coerce').values
        lv[nm] = pd.Series(z).ffill().values.astype(np.float64)
        got[nm] = True
        LOG('  基准 %s(%s) %d 天  %.1f ~ %.1f'
            % (nm, code, int(np.isfinite(lv[nm]).sum()),
               np.nanmin(lv[nm]), np.nanmax(lv[nm])))
    miss = [k for k, v in got.items() if not v]
    LOG('  基准缺失（如实标注 UNAVAILABLE，不做替代）: %s' % (miss if miss else '无'))
    LOG('  index_panel 全部可用代码: %s' % (','.join(avail[:20])))
    return lv, got


def lvl_ret(L, w, NCAL):
    out = np.full(NCAL, np.nan)
    if L is not None and NCAL > w:
        out[w:] = L[w:] / L[:-w] - 1.0
    return out.astype(np.float32)


# ─────────────────────────────────────────────────────────── 主流程
def main():
    t0 = time.time()
    argv = sys.argv[1:]
    limit = 0
    if '--limit' in argv:
        limit = int(argv[argv.index('--limit') + 1])

    LOG.sep('=')
    LOG('H-TREND-PULLBACK-01  P1 基础面板构建   hash=%s  %s'
        % (prereg_hash(), time.strftime('%Y-%m-%d %H:%M:%S')))
    fp = dump_prereg()
    LOG('预注册已冻结: %s' % fp)

    # ---------- 1) 日历 ----------
    cal = pd.read_parquet(os.path.join(FS_DATA, 'calendar.parquet'))
    td = cal['trade_date'].astype(str).values
    NCAL = len(td)
    kmap = pd.Series(np.arange(NCAL), index=td)
    year = np.array([int(x[:4]) for x in td], dtype=np.int16)
    month = np.array([int(x[4:6]) for x in td], dtype=np.int16)
    ph = phase_of(year)
    LOG.sep()
    LOG('1) 日历 %d 交易日  %s ~ %s' % (NCAL, td[0], td[-1]))
    LOG('   分期 TRAIN %d / VALID %d / OOS %d / LIVE-LIKE %d / PRE %d'
        % ((ph == 1).sum(), (ph == 2).sum(), (ph == 3).sum(), (ph == 4).sum(), (ph == 0).sum()))

    # ---------- 2) 行情 ----------
    px = pd.read_parquet(os.path.join(FS_DATA, 'price_panel.parquet'),
                         columns=['ts_code', 'trade_date'] + PCOLS)
    px['trade_date'] = px['trade_date'].astype(str)
    tc = px['ts_code'].astype('category')
    cats = np.asarray(tc.cat.categories)
    o2 = np.argsort(cats)
    codes = cats[o2]
    remap = np.empty(len(cats), dtype=np.int32)
    remap[o2] = np.arange(len(cats), dtype=np.int32)
    ci = remap[tc.cat.codes.values.astype(np.int64)].astype(np.int32)
    del tc
    kk = px['trade_date'].map(kmap).values
    okm = np.isfinite(kk)
    kk = kk[okm].astype(np.int32)
    ci = ci[okm]
    P = {c: pd.to_numeric(px[c], errors='coerce').values.astype(np.float32)[okm] for c in PCOLS}
    del px, okm
    order = np.lexsort((kk, ci))
    ci, kk = ci[order], kk[order]
    for c in PCOLS:
        P[c] = P[c][order]
    NCODES = len(codes)
    starts = np.append(np.searchsorted(ci, np.arange(NCODES), side='left'), len(ci))
    if limit:
        NCODES = min(NCODES, limit)
        starts = starts[:NCODES + 1]
        LOG('   [SMOKE] --limit %d' % NCODES)
    LOG.sep()
    LOG('2) 行情 %d 行 / %d 只（code-major 排序）' % (len(ci), NCODES))

    # ---------- 3) 元信息 ----------
    ind_l1, k_list, STM, DLM, n_ind = load_meta(td, NCAL, codes[:NCODES])
    is_bj = np.array([1 if c.endswith('.BJ') else 0 for c in codes[:NCODES]], dtype=np.int8)

    # ---------- 4) 估值面板 ----------
    bas = pd.read_parquet(os.path.join(FS_DATA, 'basic_panel.parquet'),
                          columns=['ts_code', 'trade_date'] + BCOLS)
    bas['trade_date'] = bas['trade_date'].astype(str)
    cmap_b = pd.Series(np.arange(NCODES, dtype=np.int32), index=codes[:NCODES])
    bci = bas['ts_code'].map(cmap_b).fillna(-1).astype(np.int32).values
    bkk = bas['trade_date'].map(kmap).values
    bok = (bci >= 0) & np.isfinite(bkk)
    bci, bkk = bci[bok], bkk[bok].astype(np.int32)
    Bd = {c: pd.to_numeric(bas[c], errors='coerce').values.astype(np.float32)[bok] for c in BCOLS}
    del bas, bok
    bord = np.lexsort((bkk, bci))
    bci, bkk = bci[bord], bkk[bord]
    for c in BCOLS:
        Bd[c] = Bd[c][bord]
    bstarts = np.append(np.searchsorted(bci, np.arange(NCODES), side='left'), len(bci))
    LOG('3) 估值面板 %d 行' % len(bci))

    # ---------- 5) 基准 ----------
    LOG.sep()
    LOG('4) 基准指数')
    lv, got = load_bench(kmap, NCAL)

    # ---------- 6) 输出 memmap ----------
    LOG.sep()
    LOG('5) 逐股特征计算（%d 只）' % NCODES)
    maps = {}
    for c in F32:
        maps[c] = open_memmap(os.path.join(PAN, c + '.npy'), dtype=np.float32,
                              mode='w+', shape=(NCODES, NCAL))
        maps[c][:] = np.nan
    for c in I8_MEM:
        maps[c] = open_memmap(os.path.join(PAN, c + '.npy'), dtype=np.int8,
                              mode='w+', shape=(NCODES, NCAL))
        maps[c][:] = 0

    ind_sum = np.zeros((n_ind + 1, NCAL), dtype=np.float64)
    ind_cnt = np.zeros((n_ind + 1, NCAL), dtype=np.float64)
    p_sum = np.zeros(NCAL, dtype=np.float64)
    p_cnt = np.zeros(NCAL, dtype=np.float64)
    p_last = np.full(NCODES, np.nan, dtype=np.float64)

    mp_fast = PREREG['ma_fast']
    mp_slow = PREREG['ma_slow']
    sw = PREREG['slope_win']
    min_list = PREREG['min_listing_days']

    for c in range(NCODES):
        a, b = int(starts[c]), int(starts[c + 1])
        if b <= a:
            continue
        kc = kk[a:b]

        def full(v):
            z = np.full(NCAL, np.nan, dtype=np.float64)
            z[kc] = v
            return z

        op = full(P['qfq_open'][a:b])
        hi = full(P['qfq_high'][a:b])
        lo = full(P['qfq_low'][a:b])
        cl = full(P['qfq_close'][a:b])
        pc = full(P['qfq_pre_close'][a:b])
        vo = full(P['vol'][a:b])
        am = full(P['amount'][a:b])

        s_cl = pd.Series(cl)
        s_hi = pd.Series(hi)
        s_lo = pd.Series(lo)
        s_vo = pd.Series(vo)

        # --- 均线 ---
        ma5 = s_cl.rolling(5, min_periods=5).mean()
        ma10 = s_cl.rolling(10, min_periods=10).mean()
        ma20 = s_cl.rolling(mp_fast, min_periods=mp_fast).mean()
        ma60 = s_cl.rolling(mp_slow, min_periods=mp_slow).mean()

        # --- 动量（§8）---
        rets = {}
        for w in RETW:
            rets[w] = (s_cl / s_cl.shift(w) - 1.0).values

        # --- ATR / 波动（§18）---
        tr = np.maximum(hi - lo, np.maximum(np.abs(hi - pc), np.abs(lo - pc)))
        s_tr = pd.Series(tr)
        atr5 = s_tr.rolling(5, min_periods=5).mean().values
        atr10 = s_tr.rolling(10, min_periods=10).mean().values
        atr14 = s_tr.rolling(14, min_periods=14).mean().values
        atr20 = s_tr.rolling(20, min_periods=20).mean().values
        atr_pct = atr14 / np.where(cl > 0, cl, np.nan)

        d1 = s_cl.pct_change().values
        rv20 = pd.Series(d1).rolling(20, min_periods=20).std().values

        up = np.clip(d1, 0, None)
        dn = np.clip(-d1, 0, None)
        au = pd.Series(up).ewm(alpha=1 / 14.0, adjust=False, min_periods=14).mean().values
        ad = pd.Series(dn).ewm(alpha=1 / 14.0, adjust=False, min_periods=14).mean().values
        with np.errstate(divide='ignore', invalid='ignore'):
            rsi14 = 100.0 - 100.0 / (1.0 + au / np.where(ad > 0, ad, np.nan))

        # --- 量能（§16）---
        v5 = s_vo.rolling(5, min_periods=5).mean().values
        v20 = s_vo.rolling(20, min_periods=20).mean().values
        v_ratio = v5 / np.where(v20 > 0, v20, np.nan)

        # --- 位置（§10 / §15）---
        peak20 = s_hi.shift(1).rolling(20, min_periods=20).max().values
        nh20 = ((hi > peak20) & np.isfinite(peak20)).astype(np.int8)
        dd60 = cl / s_cl.rolling(60, min_periods=60).max().values - 1.0
        lo60 = s_lo.rolling(60, min_periods=60).min().values
        hi60 = s_hi.rolling(60, min_periods=60).max().values
        rng60 = hi60 - lo60
        pos60 = np.where(rng60 > 0, (cl - lo60) / rng60, np.nan)
        lo120 = s_lo.rolling(120, min_periods=120).min().values
        hi120 = s_hi.rolling(120, min_periods=120).max().values
        rng120 = hi120 - lo120
        pos120 = np.where(rng120 > 0, (cl - lo120) / rng120, np.nan)

        # --- 斜率（§7 T2）---
        sl20 = roll_slope(ma20.values, sw) / np.where(ma20.values > 0, ma20.values, np.nan)
        sl60 = roll_slope(ma60.values, sw) / np.where(ma60.values > 0, ma60.values, np.nan)

        # --- 估值 ---
        ba, bb = int(bstarts[c]), int(bstarts[c + 1])
        tv = np.full(NCAL, np.nan)
        cv = np.full(NCAL, np.nan)
        to = np.full(NCAL, np.nan)
        vr = np.full(NCAL, np.nan)
        if bb > ba:
            kb = bkk[ba:bb]
            tv[kb] = Bd['total_mv'][ba:bb]
            cv[kb] = Bd['circ_mv'][ba:bb]
            to[kb] = Bd['turnover_rate'][ba:bb]
            vr[kb] = Bd['volume_ratio'][ba:bb]

        # --- 写盘 ---
        maps['open'][c] = op
        maps['high'][c] = hi
        maps['low'][c] = lo
        maps['close'][c] = cl
        maps['pre_close'][c] = pc
        maps['vol'][c] = vo
        maps['amount'][c] = am
        maps['turnover'][c] = to
        maps['vr'][c] = vr
        maps['total_mv'][c] = tv
        maps['circ_mv'][c] = cv
        maps['ma5'][c] = ma5.values
        maps['ma10'][c] = ma10.values
        maps['ma20'][c] = ma20.values
        maps['ma60'][c] = ma60.values
        maps['slope20'][c] = sl20
        maps['slope60'][c] = sl60
        for w in RETW:
            maps['ret_%d' % w][c] = rets[w]
        maps['atr5'][c] = atr5
        maps['atr10'][c] = atr10
        maps['atr14'][c] = atr14
        maps['atr20'][c] = atr20
        maps['atr_pct'][c] = atr_pct
        maps['rsi14'][c] = rsi14
        maps['rv_20'][c] = rv20
        maps['v_ma5'][c] = v5
        maps['v_ma20'][c] = v20
        maps['v_ratio'][c] = v_ratio
        maps['peak20'][c] = peak20
        maps['dd_60'][c] = dd60
        maps['pos_60'][c] = pos60
        maps['pos_120'][c] = pos120
        maps['nh20'][c] = nh20
        maps['st_flag'][c] = STM[c]
        maps['dl_flag'][c] = DLM[c]

        # --- 行业等权（§27）+ 全 A 等权 B1（§28）---
        g = int(ind_l1[c])
        f = np.isfinite(d1)
        if f.any():
            ind_sum[g, f] += d1[f]
            ind_cnt[g, f] += 1.0
            p_sum[f] += d1[f]
            p_cnt[f] += 1.0

        if (c + 1) % 500 == 0:
            LOG('   ... %d / %d  (%.0fs)' % (c + 1, NCODES, time.time() - t0))

    for v in maps.values():
        v.flush()
    del maps

    # ---------- 7) Universe / 剔除统计（§5）----------
    LOG.sep()
    LOG('6) Universe 与剔除统计（§5，不得静默删除）')
    close = np.asarray(np.load(os.path.join(PAN, 'close.npy'), mmap_mode='r'))
    vol = np.asarray(np.load(os.path.join(PAN, 'vol.npy'), mmap_mode='r'))
    ma60 = np.asarray(np.load(os.path.join(PAN, 'ma60.npy'), mmap_mode='r'))
    tradable = np.isfinite(close) & (close > 0) & np.isfinite(vol) & (vol > 0)
    del close, vol

    K = np.arange(NCAL, dtype=np.int64)[None, :]
    age = K - k_list[:, None].astype(np.int64)

    reason = np.zeros((NCODES, NCAL), dtype=np.int8)
    reason[~np.isfinite(ma60)] = 6                      # 数据缺失（MA60 不可得）
    reason[~tradable] = 3                               # 停牌 / 无成交
    reason[DLM[:NCODES] > 0] = 2                        # 退市整理
    reason[STM[:NCODES] > 0] = 1                        # ST / *ST
    reason[age < min_list] = 4                          # 上市不足 60 交易日（含未上市）
    reason[(is_bj > 0), :] = 5                          # 北交所
    del ma60
    valid = (reason == 0).astype(np.int8)

    NAMES = {0: '通过', 1: 'ST/*ST', 2: '退市整理', 3: '停牌/无成交',
             4: '上市不足%d日' % min_list, 5: '北交所', 6: '数据缺失(MA60)'}
    tot = NCODES * NCAL
    exc = {}
    for k in sorted(NAMES):
        cnt = int((reason == k).sum())
        exc[NAMES[k]] = cnt
        LOG('   %-16s %10d  %.2f%%' % (NAMES[k], cnt, 100.0 * cnt / tot))
    LOG('   合计单元格 %d；通过 %d (%.2f%%)'
        % (tot, int(valid.sum()), 100.0 * float(valid.mean())))

    np.save(os.path.join(PAN, 'valid.npy'), valid)
    np.save(os.path.join(PAN, 'reason.npy'), reason)
    np.save(os.path.join(PAN, '_code_klist.npy'), k_list)
    np.save(os.path.join(PAN, '_code_ind_l1.npy'), ind_l1)
    np.save(os.path.join(PAN, '_code_is_bj.npy'), is_bj)
    with np.errstate(divide='ignore', invalid='ignore'):
        np.save(os.path.join(PAN, '_ind_sum.npy'), ind_sum.astype(np.float32))
        np.save(os.path.join(PAN, '_ind_cnt.npy'), ind_cnt.astype(np.float32))

    # ---------- 8) 日级信息 ----------
    proxy = np.cumprod(1.0 + np.where(p_cnt > 0, p_sum / np.maximum(p_cnt, 1), 0.0))
    day = {'dates': td.astype('U8'), 'year': year, 'month': month,
           'phase': ph, 'proxy': proxy.astype(np.float32)}
    for nm in BENCH.values():
        L = lv.get(nm)
        day['lvl_%s' % nm] = (L.astype(np.float32) if L is not None
                              else np.full(NCAL, np.nan, np.float32))
        for w in RETW:
            day['ret%d_%s' % (w, nm)] = lvl_ret(L, w, NCAL)
    # Regime（§42）：HS300 vs MA200 + 60D 动量
    hs = lv.get('hs300')
    if hs is not None:
        s = pd.Series(hs)
        ma200 = s.rolling(200, min_periods=60).mean()
        r60 = s / s.shift(60) - 1.0
        reg = np.ones(NCAL, dtype=np.int8)
        reg[((s > ma200) & (r60 > 0)).values] = 2
        reg[((s < ma200) & (r60 < 0)).values] = 0
    else:
        reg = np.ones(NCAL, dtype=np.int8)
        LOG('   [WARN] HS300 不可用，Regime 全部记 NORMAL（如实标注）')
    day['regime'] = reg
    psave_day(day)
    LOG('   Regime: BEAR %d / NORMAL %d / BULL %d'
        % (int((reg == 0).sum()), int((reg == 1).sum()), int((reg == 2).sum())))
    LOG('   全 A 等权 PROXY 末日 %.3f，日均收益 %.5f'
        % (proxy[-1], float((p_sum / np.maximum(p_cnt, 1))[p_cnt > 0].mean())))

    psave_meta({'hypothesis_id': PREREG['hypothesis_id'],
                'prereg_hash': prereg_hash(),
                'built_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                'NCAL': int(NCAL), 'NCODES': int(NCODES), 'n_ind': int(n_ind),
                'dates': [str(x) for x in td],
                'codes': [str(x) for x in codes[:NCODES]],
                'bench_available': got,
                'excluded_count': exc,
                'f32_cols': F32, 'i8_cols': I8,
                'note': 'ret_20=MOM20, ret_60=MOM60（§26 控制变量）'})

    # ---------- 9) 快速健全性 ----------
    LOG.sep()
    LOG('7) 健全性抽检')
    for nm in ('close', 'ma20', 'ma60', 'slope20', 'ret_20', 'atr14', 'peak20'):
        z = np.asarray(np.load(os.path.join(PAN, nm + '.npy'), mmap_mode='r'))
        v = z[np.isfinite(z)]
        if len(v):
            LOG('   %-8s finite %.2f%%  p05 %.4f  p50 %.4f  p95 %.4f'
                % (nm, 100.0 * len(v) / z.size, np.quantile(v, .05),
                   np.quantile(v, .50), np.quantile(v, .95)))
    LOG.sep('=')
    LOG('P1 完成 %.0fs  hash=%s' % (time.time() - t0, prereg_hash()))
    print('DONE')
    return 0


if __name__ == '__main__':
    sys.exit(main())
