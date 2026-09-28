# -*- coding: utf-8 -*-
"""
H-IND-DIFFUSION-01  P1：基础面板构建（STEP 1-3 / §5-§8）

隔离声明：本模块自包含，不 import 任何既有研究模块。

产出
    data/panel/*.npy         code-major 形状 (NCODES, NCAL)
    data/panel/_day.npz      dates / year / month / phase / regime / bench
    data/panel/_meta.json    元信息 + Universe 剔除明细 + 行业覆盖（披露式，不静默删除）

float32 2D : open high low close pre_close vol amount
             total_mv circ_mv turnover vr
             cl_ma5 cl_ma10 cl_ma20 cl_ma60
             ret_1 ret_3 ret_5 ret_10 ret_20 ret_60
             amt_ma20 rv_20
             exe_1 exe_3 exe_5 exe_10 exe_20      (次开进场：close[k+h]/open[k+1]-1)
             raw_1 raw_3 raw_5 raw_10 raw_20      (描述口径：close[k+h]/close[k]-1)
int8  2D   : st_flag dl_flag valid reason tradable_next   (reason: 0PASS 1ST 2SUSPEND 3NEW 4BJ 5NO_DATA 6ENDED)
int32 1D   : _code_klist        (上市日 -> 历法索引, -1 未知, -2 样本起点前已上市)
int16 1D   : _code_ind_l1 _code_ind_l2   (申万编码, 0 = UNKNOWN)
int8  1D   : _code_is_bj

PIT 口径（§50 G1）
    - 全部价格使用前复权 qfq_*；rolling 一律 min_periods=w，绝不使用未来数据。
    - 行业归属使用 sw_industry_map 的 in_date / out_date 时点区间（不允许用未来行业）。
    - ST / 退市整理使用 namechange 的名称变更区间（时点）。
    - exe_H 使用 open[k+1] 进场，信号日 k 收盘后决策，严格因果。
"""
import os
import sys
import glob
import time
import json

import numpy as np
import pandas as pd
from numpy.lib.format import open_memmap

from hid_common import (DATA, OUTD, FS_DATA, CD, PREREG, GATES, mklog,
                        psave_meta, pload_meta, phase_of, prereg_hash,
                        dump_prereg)

LOG = mklog('panel')

PAN = os.path.join(DATA, 'panel')
os.makedirs(PAN, exist_ok=True)

PCOLS = ['qfq_open', 'qfq_high', 'qfq_low', 'qfq_close', 'qfq_pre_close', 'vol', 'amount']
BCOLS = ['total_mv', 'circ_mv', 'turnover_rate', 'volume_ratio']

F32 = ['open', 'high', 'low', 'close', 'pre_close', 'vol', 'amount',
       'total_mv', 'circ_mv', 'turnover', 'vr',
       'cl_ma5', 'cl_ma10', 'cl_ma20', 'cl_ma60',
       'ret_1', 'ret_3', 'ret_5', 'ret_10', 'ret_20', 'ret_60',
       'amt_ma20', 'rv_20',
       'exe_1', 'exe_3', 'exe_5', 'exe_10', 'exe_20',
       'raw_1', 'raw_3', 'raw_5', 'raw_10', 'raw_20']
I8_MEM = ['st_flag', 'dl_flag', 'valid', 'reason', 'tradable_next']

RETW = PREREG['ret_wins']
HOR = PREREG['horizons']
MAW = PREREG['ma_wins']

REASON = {0: 'PASS', 1: 'ST', 2: 'SUSPEND', 3: 'NEW', 4: 'BJ', 5: 'NO_DATA',
          6: 'ENDED'}


# ─────────────────────────────────────────────── 元信息 / PIT 行业
def load_meta(td, NCAL, codes):
    NC = len(codes)
    list_date = {}
    fp = os.path.join(CD, 'stock_basic.csv')
    if os.path.exists(fp):
        d = pd.read_csv(fp, dtype=str)
        list_date = dict(zip(d['ts_code'], d['list_date']))

    # 编码：>=0 面板内上市索引；-1 未知；-2 样本起点前已上市
    #（searchsorted 对早于 td[0] 的日期一律返回 0，会把「1991 年上市」误判成「上市第 1 天」）
    k_list = np.full(NC, -1, dtype=np.int32)
    for i, c in enumerate(codes):
        ld = list_date.get(c)
        if isinstance(ld, str) and len(ld) == 8 and ld.isdigit():
            if ld < td[0]:
                k_list[i] = -2
            else:
                k_list[i] = int(np.searchsorted(td, ld, side='left'))

    # ---- PIT 行业映射（SW L1 + L2，in_date/out_date）----
    # 主源：data/sw_member_all.csv（hid_fetch_sw.py 取 Tushare index_member_all，5914 只 + 2006 条
    #       历史变更，区间无重叠；cache_daily 版本仅 3000 只且 in_date/out_date 全空，G2 必 FAIL）。
    # 同一代码多段时按 in_date 升序覆盖 → 最新分类胜出（区间已验证不重叠）。
    ind_l1 = np.zeros((NC, NCAL), dtype=np.int16)
    ind_l2 = np.zeros((NC, NCAL), dtype=np.int16)
    fp_new = os.path.join(DATA, 'sw_member_all.csv')
    fp_old = os.path.join(CD, 'industry', 'sw_industry_map.csv')
    fp = fp_new if os.path.exists(fp_new) else fp_old
    src = os.path.basename(fp) if os.path.exists(fp) else 'NONE'
    l1cats, l2cats = [], []
    sw_pairs = {}
    nrec = 0
    if os.path.exists(fp):
        d = pd.read_csv(fp, dtype=str)
        d = d[d['ts_code'].notna()]
        nrec = len(d)
        l1cats = sorted(set(d['l1_name'].dropna()))
        l2cats = sorted(set(d['l2_name'].dropna()))
        e1 = {c: i + 1 for i, c in enumerate(l1cats)}
        e2 = {c: i + 1 for i, c in enumerate(l2cats)}
        cm = pd.Series(np.arange(NC, dtype=np.int32), index=codes)
        d = d.assign(_i=d['ts_code'].map(cm))
        d = d[d['_i'].notna()]
        d['_i'] = d['_i'].astype(np.int32)
        for c in ('in_date', 'out_date'):
            s = d[c].astype(str).str.replace('.0', '', regex=False)
            d[c] = s.where(s.str.match(r'^\d{8}$'), '')
        d = d.sort_values(['_i', 'in_date'])
        for i, k1s, k2s, n1, n2 in zip(d['_i'].values, d['in_date'].values,
                                       d['out_date'].values, d['l1_name'].values,
                                       d['l2_name'].values):
            k1 = int(np.searchsorted(td, k1s, side='left')) if k1s else 0
            k2 = (int(np.searchsorted(td, k2s, side='right')) - 1) if k2s else NCAL - 1
            k1 = max(0, min(k1, NCAL))
            k2 = max(-1, min(k2, NCAL - 1))
            if k2 < k1:
                continue
            a1 = e1.get(n1, 0)
            a2 = e2.get(n2, 0)
            if a1:
                ind_l1[i, k1:k2 + 1] = a1
            if a2:
                ind_l2[i, k1:k2 + 1] = a2
            sw_pairs[i] = sw_pairs.get(i, 0) + 1

    # ---- 时点 ST / 退市整理 ----
    STM = np.zeros((NC, NCAL), dtype=np.int8)
    DLM = np.zeros((NC, NCAL), dtype=np.int8)
    frames = []
    for fp in glob.glob(os.path.join(CD, 'treasure_namechg_*.parquet')):
        try:
            d = pd.read_parquet(fp)
            if len(d):
                frames.append(d[['ts_code', 'name', 'start_date', 'end_date']])
        except Exception:
            pass
    if frames:
        d = pd.concat(frames, ignore_index=True)
        d['ts_code'] = d['ts_code'].astype(str)
        cm = pd.Series(np.arange(NC, dtype=np.int32), index=codes)
        d['_i'] = d['ts_code'].map(cm)
        d = d[d['_i'].notna()]
        d['_i'] = d['_i'].astype(np.int32)
        for c in ('start_date', 'end_date'):
            s = d[c].astype(str).str.replace('.0', '', regex=False)
            d[c] = s.where(s.str.match(r'^\d{8}$'), '19000101')
        d['end_date'] = d['end_date'].where(d['end_date'] != '19000101', '99999999')
        nm = d['name'].astype(str)
        st_f = nm.str.contains('ST', na=False).values
        dl_f = nm.str.contains('退', na=False).values
        k1 = np.searchsorted(td, d['start_date'].values, side='left')
        k2 = np.searchsorted(td, d['end_date'].values, side='right') - 1
        for j in range(len(d)):
            i = int(d['_i'].values[j])
            a, b = max(0, int(k1[j])), min(NCAL - 1, int(k2[j]))
            if b < a:
                continue
            if st_f[j]:
                STM[i, a:b + 1] = 1
            if dl_f[j]:
                DLM[i, a:b + 1] = 1

    return (k_list, ind_l1, ind_l2, STM, DLM, l1cats, l2cats,
            len(sw_pairs), nrec, src)


def load_bench(kmap, NCAL):
    idx = pd.read_parquet(os.path.join(FS_DATA, 'index_panel.parquet'))
    idx['trade_date'] = idx['trade_date'].astype(str)
    codes = sorted(set(idx['ts_code'].astype(str).values))
    out, got = {}, {}
    for code in codes:
        d = idx[idx['ts_code'].astype(str) == code]
        k = d['trade_date'].map(kmap).values
        ok = np.isfinite(k)
        arr = np.full(NCAL, np.nan, dtype=np.float64)
        arr[k[ok].astype(int)] = pd.to_numeric(d['close'], errors='coerce').values[ok]
        out[code] = arr
        got[code] = int(ok.sum())
    return out, got


def regime_series(hs):
    """HS300 vs MA200 & 60D momentum → 0=BEAR 1=NORMAL 2=BULL"""
    s = pd.Series(hs)
    ma200 = s.rolling(200, min_periods=200).mean()
    mom60 = s / s.shift(60) - 1
    up = (s > ma200).values
    mo = (mom60 > 0).values
    r = np.full(len(s), 1, dtype=np.int8)
    r[up & mo] = 2
    r[(~up) & (~mo)] = 0
    r[~np.isfinite(s.values)] = 1
    return r


def main():
    argv = sys.argv[1:]
    limit = 0
    if '--limit' in argv:
        limit = int(argv[argv.index('--limit') + 1])

    LOG.sep('=')
    LOG('H-IND-DIFFUSION-01  P1 基础面板  hash=%s  %s'
        % (prereg_hash(), time.strftime('%Y-%m-%d %H:%M:%S')))
    LOG('预注册已冻结: %s' % dump_prereg())

    # ---------- STEP 1) 日历 ----------
    cal = pd.read_parquet(os.path.join(FS_DATA, 'calendar.parquet'))
    td = cal['trade_date'].astype(str).values
    NCAL = len(td)
    kmap = pd.Series(np.arange(NCAL), index=td)
    year = np.array([int(x[:4]) for x in td], dtype=np.int16)
    month = np.array([int(x[4:6]) for x in td], dtype=np.int16)
    ph = phase_of(year)
    LOG.sep()
    LOG('[STEP1] 日历 %d 交易日  %s ~ %s' % (NCAL, td[0], td[-1]))
    LOG('  分期 TRAIN %d / VALID %d / OOS %d / LIVE-LIKE %d / PRE %d'
        % ((ph == 1).sum(), (ph == 2).sum(), (ph == 3).sum(), (ph == 4).sum(), (ph == 0).sum()))

    # ---------- 行情 ----------
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
        LOG('  [SMOKE] --limit %d' % NCODES)
    LOG.sep()
    LOG('  行情 %d 行 / %d 只' % (int(starts[NCODES]), NCODES))

    # ---------- STEP 2) PIT / Mapping ----------
    (k_list, ind_l1, ind_l2, STM, DLM, l1cats, l2cats,
     n_codes_sw, nrec, src) = load_meta(td, NCAL, codes[:NCODES])
    is_bj = np.array([1 if c.endswith('.BJ') else 0 for c in codes[:NCODES]], dtype=np.int8)
    LOG.sep()
    LOG('[STEP2] PIT / Mapping')
    LOG('  行业映射源 %s：记录 %d 条，覆盖代码 %d / %d (%.4f)'
        % (src, nrec, n_codes_sw, NCODES, n_codes_sw / max(NCODES, 1)))
    LOG('  L1 类别 %d，L2 类别 %d；L1 编码区间 %s'
        % (len(l1cats), len(l2cats), 'PIT(in_date/out_date)' if src == 'sw_member_all.csv'
           else '快照（无区间，全期回填）'))

    # 上市日兜底 + 「样本起点前已上市」标记。
    # stock_basic.csv 为快照（缺 286 只已退市/更名代码）；缺失者用面板内首个有行情日替代 ——
    # 该日不早于真实上市日，故上市天数据算偏小，方向上只会多剔除、不会引入未来信息（保守）。
    pre = (k_list == -2)
    unknown = (k_list == -1)
    first_k = np.full(NCODES, -1, dtype=np.int32)
    for c in range(NCODES):
        a, b = int(starts[c]), int(starts[c + 1])
        if b > a:
            first_k[c] = int(kk[a])
    fill = unknown & (first_k >= 0)
    k_eff = np.where(k_list >= 0, k_list,
                     np.where(fill, first_k, -1)).astype(np.int32)
    LOG('  上市日（stock_basic）：可得 %d / %d（样本起点前 %d，面板内 %d）'
        % (int((k_list != -1).sum()), NCODES, int(pre.sum()), int((k_list >= 0).sum())))
    LOG('  上市日缺口的兜底：首日替代 %d；仍未知 %d'
        % (int(fill.sum()), int(((k_eff < 0) & (~pre)).sum())))

    # ---------- 估值 ----------
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
    LOG('  估值面板 %d 行' % len(bci))

    # ---------- 基准 / regime ----------
    bmk, got = load_bench(kmap, NCAL)
    bcode = '000300.SH' if '000300.SH' in bmk else sorted(bmk)[0]
    hs = bmk[bcode]
    reg = regime_series(hs)
    LOG('  基准 %s 可得 %d 日' % (bcode, got[bcode]))
    LOG('  Regime: BEAR %d / NORMAL %d / BULL %d'
        % ((reg == 0).sum(), (reg == 1).sum(), (reg == 2).sum()))

    # ---------- 输出 memmap ----------
    maps = {}
    for c in F32:
        maps[c] = open_memmap(os.path.join(PAN, c + '.npy'), dtype=np.float32,
                              mode='w+', shape=(NCODES, NCAL))
        maps[c][:] = np.nan
    for c in I8_MEM:
        maps[c] = open_memmap(os.path.join(PAN, c + '.npy'), dtype=np.int8,
                              mode='w+', shape=(NCODES, NCAL))
        maps[c][:] = 0

    min_list = PREREG['min_listing_days']
    HMAX = max(HOR)
    exc = {v: 0 for v in REASON.values()}
    exc_valid = 0
    rows_total = 0

    for c in range(NCODES):
        a, b = int(starts[c]), int(starts[c + 1])
        if b <= a:
            continue     # 无行情行：不落任何数据，也不会计入 Universe 分母

        def full(v, kc):
            z = np.full(NCAL, np.nan, dtype=np.float64)
            z[kc] = v
            return z

        kc = kk[a:b]
        op = full(P['qfq_open'][a:b], kc)
        hi = full(P['qfq_high'][a:b], kc)
        lo = full(P['qfq_low'][a:b], kc)
        cl = full(P['qfq_close'][a:b], kc)
        pc = full(P['qfq_pre_close'][a:b], kc)
        vo = full(P['vol'][a:b], kc)
        am = full(P['amount'][a:b], kc)

        ba, bb = int(bstarts[c]), int(bstarts[c + 1])
        kb = kk[0:0] if ba >= bb else bkk[ba:bb]
        # 估值按日对齐（缺失留空）
        def fullb(col):
            z = np.full(NCAL, np.nan, dtype=np.float64)
            if bb > ba:
                z[kb] = Bd[col][ba:bb]
            return z
        mv = fullb('total_mv')
        cmv = fullb('circ_mv')
        tr = fullb('turnover_rate')
        vr = fullb('volume_ratio')

        s_cl = pd.Series(cl)
        s_hi = pd.Series(hi)
        s_lo = pd.Series(lo)
        s_vo = pd.Series(vo)
        s_am = pd.Series(am)

        ma = {w: s_cl.rolling(w, min_periods=w).mean() for w in MAW}
        r1 = s_cl / s_cl.shift(1) - 1.0
        rv20 = r1.rolling(20, min_periods=20).std()
        amt20 = s_am.rolling(20, min_periods=20).mean()

        # 有效性：结构原因优先（北交所 / 无行情区间 / 上市不足），其次 ST·退市整理，再次停牌与数据缺失。
        # 注：`&` 优先级高于 `>`，比较式必须加括号。
        kk_full = np.arange(NCAL)
        fk, lk = int(kk[a]), int(kk[b - 1])
        traded = np.isfinite(cl)
        ma60ok = np.isfinite(ma[60].values)
        na_f = traded & (~ma60ok)          # 有成交但 MA60 不可得 → 关键数据缺失

        reason = np.zeros(NCAL, dtype=np.int8)
        reason[kk_full < fk] = 3                       # 面板内尚无行情（上市前）
        reason[kk_full > lk] = 6                       # 行情结束（退市 / 摘牌后段）
        if not pre[c]:
            listed = np.where(k_eff[c] >= 0, kk_full - k_eff[c] + 1, -1)
            reason[(listed < min_list) & (reason == 0)] = 3
        reason[is_bj[c] == 1] = 4                      # 北交所（结构性，最高优先）
        reason[((STM[c] == 1) | (DLM[c] == 1)) & (reason == 0)] = 1
        reason[((s_vo.values <= 0) | (~traded)) & (reason == 0)] = 2
        reason[na_f & (reason == 0)] = 5
        valid = (reason == 0).astype(np.int8)

        # 次日可成交（T+1 开盘非一字板且非停牌）
        opn = np.roll(op, -1)
        hin = np.roll(hi, -1)
        lon = np.roll(lo, -1)
        von = np.roll(vo, -1)
        trad = ((von > 0) & np.isfinite(opn) & (opn > 0)
                & ~((hin == lon) & np.isfinite(hin))).astype(np.int8)
        trad[NCAL - 1] = 0

        # 前向收益
        fwd = {}
        for h in HOR:
            clv = np.full(NCAL, np.nan)
            if h < NCAL:
                clv[:NCAL - h] = cl[h:]
            with np.errstate(invalid='ignore', divide='ignore'):
                exe = clv / opn - 1.0
                raw = clv / cl - 1.0
            exe = np.where(np.isfinite(exe) & (opn > 0), exe, np.nan)
            raw = np.where(np.isfinite(raw), raw, np.nan)
            exe[~np.isfinite(opn)] = np.nan
            fwd['exe_%d' % h] = exe
            fwd['raw_%d' % h] = raw
        # h 末端不可得（需要 k+h 存在）
        for h in HOR:
            if h < NCAL:
                fwd['exe_%d' % h][NCAL - h:] = np.nan
                fwd['raw_%d' % h][NCAL - h:] = np.nan

        cols = {
            'open': op, 'high': hi, 'low': lo, 'close': cl, 'pre_close': pc,
            'vol': vo, 'amount': am,
            'total_mv': mv, 'circ_mv': cmv, 'turnover': tr, 'vr': vr,
            'cl_ma5': cl / ma[5].values - 1.0,
            'cl_ma10': cl / ma[10].values - 1.0,
            'cl_ma20': cl / ma[20].values - 1.0,
            'cl_ma60': cl / ma[60].values - 1.0,
            'ret_1': r1.values,
            'ret_3': (s_cl / s_cl.shift(3) - 1.0).values,
            'ret_5': (s_cl / s_cl.shift(5) - 1.0).values,
            'ret_10': (s_cl / s_cl.shift(10) - 1.0).values,
            'ret_20': (s_cl / s_cl.shift(20) - 1.0).values,
            'ret_60': (s_cl / s_cl.shift(60) - 1.0).values,
            'amt_ma20': amt20.values, 'rv_20': rv20.values,
        }
        cols.update(fwd)

        for k, v in cols.items():
            maps[k][c, :] = v.astype(np.float32)
        maps['st_flag'][c, :] = STM[c]
        maps['dl_flag'][c, :] = DLM[c]
        maps['valid'][c, :] = valid
        maps['reason'][c, :] = reason
        maps['tradable_next'][c, :] = trad

        rows_total += int((np.isfinite(cl)).sum())
        for code_i, nm in REASON.items():
            exc[nm] += int((reason == code_i).sum())
        exc_valid += int(valid.sum())

    LOG.sep()
    LOG('[STEP3] Universe（§6，披露式剔除，不静默删除）')
    LOG('  合计单元格 %d；通过 %d (%.2f%%)'
        % (rows_total, exc_valid, 100.0 * exc_valid / max(rows_total, 1)))
    for nm in ('ST', 'SUSPEND', 'NEW', 'BJ', 'NO_DATA', 'ENDED'):
        LOG('  %-8s %d (%.2f%%)' % (nm, exc[nm], 100.0 * exc[nm] / max(rows_total, 1)))

    np.save(os.path.join(PAN, '_code_klist.npy'), k_list)
    np.save(os.path.join(PAN, '_code_ind_l1.npy'), ind_l1)
    np.save(os.path.join(PAN, '_code_ind_l2.npy'), ind_l2)
    np.save(os.path.join(PAN, '_code_is_bj.npy'), is_bj)
    # 代码列表（与 memmap 行序严格一致；下游还原 ts_code 用，避免各阶段各自重建）
    np.save(os.path.join(PAN, '_codes.npy'), codes[:NCODES])

    # 行业覆盖（§5 必须记录）
    vl = maps['valid'][:NCODES]
    cov1 = float(((vl == 1) & (ind_l1 > 0)).sum()) / max(1, int((vl == 1).sum()))
    cov2 = float(((vl == 1) & (ind_l2 > 0)).sum()) / max(1, int((vl == 1).sum()))
    LOG.sep()
    LOG('  行业覆盖（有效股票日）：SW L1 %.4f，SW L2 %.4f' % (cov1, cov2))
    LOG('  L1 UNKNOWN 股票日 %d（如实披露，不删除）'
        % int(((vl == 1) & (ind_l1 == 0)).sum()))

    # dtype 必须定宽（'<U8'）：td 来自 pandas 的 object 数组，直接存会让 npz 变成
    # object 数组，下游只能 allow_pickle=True 才能读回。
    day = {'dates': np.asarray(td, dtype='U8'), 'year': year, 'month': month, 'phase': ph,
           'regime': reg, 'bench_code': np.asarray([bcode], dtype='U10'),
           'hs300': hs.astype(np.float32)}
    np.savez(os.path.join(PAN, '_day.npz'), **day)

    # 每日有效只数（供 §7 cohort 使用）
    nd = (vl == 1).sum(0).astype(np.int32)
    nz = np.flatnonzero(nd > 0)
    LOG('  每日有效只数：min %d / median %d / max %d'
        % (int(nd.min()), int(np.median(nd)), int(nd.max())))
    if len(nz):
        LOG('  首个有效日 %s（此前 %d 日有效只数为 0：全部个股 MA60 不可得，属预期保守行为）'
            % (td[nz[0]], int(nz[0])))

    psave_meta({
        'hypothesis_id': PREREG['hypothesis_id'],
        'prereg_hash': prereg_hash(),
        'built_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'NCAL': int(NCAL), 'NCODES': int(NCODES),
        'n_l1': len(l1cats), 'n_l2': len(l2cats),
        'l1_names': l1cats, 'l2_names': l2cats,
        'ind_source': src, 'ind_records': int(nrec), 'ind_codes': int(n_codes_sw),
        'date_start': str(td[0]), 'date_end': str(td[-1]),
        'excluded_count': {k: int(v) for k, v in exc.items()},
        'rows_total': int(rows_total), 'rows_valid': int(exc_valid),
        'coverage_l1': cov1, 'coverage_l2': cov2,
        'daily_valid': [int(nd.min()), int(np.median(nd)), int(nd.max())],
        'f32_cols': F32, 'i8_cols': I8_MEM,
        'note': 'ret_* 为前复权收盘收益；exe_H=close[k+H]/open[k+1]-1（次日开盘进场）；'
                '行业为 PIT（in_date/out_date）；ST/退市为 namechange 时点标记。',
    })

    LOG.sep('=')
    LOG('DONE  P1')


if __name__ == '__main__':
    main()
