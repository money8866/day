# -*- coding: utf-8 -*-
"""tl_build: TL-01 趋势生命周期 —— 特征与状态数据集构建

Hypothesis ID : TL-01  Trend Lifecycle Alpha  （§53 交付物第 1、2 项）

流水线（全部落 data/，最终交付物落本目录）
  P1  tl_ts_daily.parquet     时序特征 + 前向目标（行 = 股票 × 交易日，code-major）
  P2  tl_xs_daily.parquet     横截面特征（ts_18/ts_raw/ts_25/atr_pct_pct）
  P3  tl_state_daily.parquet  Model A 状态 / 前状态 / 趋势年龄 / 状态持续 / reacc
  P4  tl01_feature_daily.parquet / tl01_state_daily.parquet（仅研究样本行）

§2 第一原则（禁止从收益率反推状态）
  本脚本把「特征层」与「目标层」严格分成两个函数：
    code_features() 只使用决策日 t 及之前的已观测信息；
    code_targets()  只产出 forward / rel / exe / MFE / MAE / cont / fail 目标列，
                    这些列绝不回流到特征层或状态定义。
  §3 自检：_leak_test() 对随机 (股票, 日期) 用「截断到 t 的数据」重算特征与状态，
  与全样本结果逐项比对，验证无未来函数。

§4 Universe：A 股普通股（剔除 .BJ）、时点 ST/*ST/退市、停牌、上市不足、历史不足 120 日。
  历史回测保留当时真实存在的股票（不做当前存活筛选）。
§5 最小历史长度 120 个交易日，不足标 EXCLUDE_INSUFFICIENT_HISTORY。
§12 基准：沪深 300（index_panel.parquet）+ 自建全 A 等权 PROXY（剔除单日 |r|>50% 的伪记录）。
"""
import os
import sys
import time
import glob
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tl_common import (DATA, OUTD, HERE, FS_DATA, CD, PREREG, STATE_NAMES, Log)
from tl_states import default_cfg, assign_state

log = Log('_tl_build.txt')

try:
    from numpy.lib.stride_tricks import sliding_window_view as _swv
except Exception:                                    # pragma: no cover
    def _swv(a, w):
        a = np.ascontiguousarray(a, dtype=np.float64)
        s = a.strides[0]
        return np.lib.stride_tricks.as_strided(
            a, shape=(len(a) - w + 1, w), strides=(s, s))

RW = PREREG['ret_windows']          # 5,10,20,40,60,120
MAW = (5, 10, 18, 20, 22, 60, 120)  # §6.2 + §33 邻域（ma_win 18/20/22）
SLW = PREREG['slope_windows']       # 10,20,40,60
POSW = PREREG['pos_windows']        # 20,60,120
HZ = PREREG['horizons']             # 3,5,10,20,60

PCOLS = ['qfq_open', 'qfq_high', 'qfq_low', 'qfq_close', 'qfq_pre_close',
         'open', 'pre_close', 'vol', 'amount']
BCOLS = ['total_mv', 'circ_mv', 'pe_ttm', 'pb', 'ps_ttm', 'dv_ttm',
         'turnover_rate', 'volume_ratio']

BLOCKS = ['ret_18', 'ret_20', 'ret_25', 'c_ma20', 'ma20_ma60', 'pos_60',
          'rv_20', 'rs_20']
BLOCK_SIGNS = {'ret_18': 1.0, 'ret_20': 1.0, 'ret_25': 1.0, 'c_ma20': 1.0,
               'ma20_ma60': 1.0, 'pos_60': 1.0, 'rv_20': -1.0, 'rs_20': 1.0}
TS_OF = {'ts_18': ['ret_18', 'c_ma20', 'ma20_ma60', 'pos_60', 'rv_20', 'rs_20'],
         'ts_20': ['ret_20', 'c_ma20', 'ma20_ma60', 'pos_60', 'rv_20', 'rs_20'],
         'ts_25': ['ret_25', 'c_ma20', 'ma20_ma60', 'pos_60', 'rv_20', 'rs_20']}

CHUNK = 300                          # 每块股票数（同时 = 一个 parquet row group）
RG = CHUNK                           # 别名


# ------------------------------------------------------------------ 滚动工具
def roll_stat(y, w, kind='mean', mp=None):
    """因果滚动统计：out[t] 只用 y[t-w+1..t]（NaN 容忍，按 mp 判有效）"""
    y = np.asarray(y, dtype=np.float64)
    n = len(y)
    out = np.full(n, np.nan, dtype=np.float32)
    if w < 1 or n < w:
        return out
    sw = _swv(y, w)
    fin = np.isfinite(sw)
    cnt = fin.sum(1)
    if mp is None:
        mp = (max(3, int(np.ceil(w * 0.8))) if kind in ('mean', 'std', 'slope')
              else max(1, w - 5))
    ok = cnt >= mp
    if kind == 'mean':
        v = np.where(fin, sw, 0.0).sum(1) / np.maximum(cnt, 1)
    elif kind == 'sum':
        v = np.where(fin, sw, 0.0).sum(1)
    elif kind == 'max':
        v = np.where(fin, sw, -np.inf).max(1)
        v = np.where(np.isfinite(v), v, np.nan)
    elif kind == 'min':
        v = np.where(fin, sw, np.inf).min(1)
        v = np.where(np.isfinite(v), v, np.nan)
    elif kind == 'argmax':                    # 距窗口内最高点的期数
        v = (w - 1 - np.where(fin, sw, -np.inf).argmax(1)).astype(float)
    elif kind == 'std':
        mu = np.where(fin, sw, 0.0).sum(1) / np.maximum(cnt, 1)
        v = np.sqrt(np.maximum(
            np.where(fin, (sw - mu[:, None]) ** 2, 0.0).sum(1) / np.maximum(cnt, 1), 0.0))
    elif kind == 'slope':                     # 成对删除的 OLS 斜率
        x = np.arange(w, dtype=np.float64)[None, :]
        X = np.where(fin, x, 0.0)
        Y = np.where(fin, sw, 0.0)
        c = cnt.astype(np.float64)
        sx, sy = X.sum(1), Y.sum(1)
        sxx, sxy = (X * X).sum(1), (X * Y).sum(1)
        den = c * sxx - sx * sx
        good = np.abs(den) > 1e-12
        v = np.where(good, (c * sxy - sx * sy) / np.where(good, den, 1.0), np.nan)
    else:
        raise ValueError(kind)
    out[w - 1:] = np.where(ok, v, np.nan)
    return out


def _shift(v, n):
    y = np.asarray(v, dtype=np.float64)
    out = np.full(len(y), np.nan, dtype=np.float32)
    if n < len(y):
        out[n:] = y[:-n]
    return out


def _ret(px, w):
    px = np.asarray(px, dtype=np.float64)
    n = len(px)
    out = np.full(n, np.nan, dtype=np.float32)
    if n > w:
        with np.errstate(invalid='ignore', divide='ignore'):
            out[w:] = px[w:] / px[:-w] - 1.0
    return out


def _ratio_div(a, b):
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    with np.errstate(invalid='ignore', divide='ignore'):
        return (a / b).astype(np.float32)


def _sub(a, b):
    return (np.asarray(a, dtype=np.float64) - np.asarray(b, dtype=np.float64)).astype(np.float32)


# ------------------------------------------------------------------ §6-§15 特征
def code_features(Cq, Hq, Lq, PCq, V, AMT, idxr):
    """只使用 t 及之前信息的趋势特征（§6–§15）。idxr = 日期级基准收益字典。"""
    F = {}
    for w in RW:
        F['ret_%d' % w] = _ret(Cq, w)
    for w in (18, 25):                       # §33 邻域（trend_win 18/20/25）
        F['ret_%d' % w] = _ret(Cq, w)
    ma = {}
    for w in MAW:
        ma[w] = roll_stat(Cq, w, 'mean')
        F['ma%d' % w] = ma[w]
    for w in (18, 20, 22, 60):
        F['c_ma%d' % w] = _ratio_div(Cq, ma[w]) - 1.0
    for w in (18, 20, 22):
        F['ma%d_ma60' % w] = _ratio_div(ma[w], ma[60]) - 1.0
    for w in (20, 60, 120):
        F['ma%d_slope' % w] = _ratio_div(roll_stat(ma[w], 20, 'slope'), ma[w])
    for w in SLW:
        sl = roll_stat(Cq, w, 'slope')
        F['slope_%d' % w] = sl
        F['slope_%dn' % w] = _ratio_div(sl, Cq)
    for w in POSW:
        lo = roll_stat(Cq, w, 'min')
        hi = roll_stat(Cq, w, 'max')
        F['pos_%d' % w] = _ratio_div(_sub(Cq, lo), _sub(hi, lo))
    tr = np.fmax.reduce(np.vstack([
        np.asarray(Hq, float) - np.asarray(Lq, float),
        np.abs(np.asarray(Hq, float) - np.asarray(PCq, float)),
        np.abs(np.asarray(Lq, float) - np.asarray(PCq, float))]))
    atr = roll_stat(tr, PREREG['atr_window'], 'mean')
    F['atr'] = atr
    F['atr_pct'] = _ratio_div(atr, Cq)
    r1 = _ret(Cq, 1)
    F['rv_20'] = roll_stat(r1, 20, 'std') * np.float32(np.sqrt(252.0))
    F['rv_60'] = roll_stat(r1, 60, 'std') * np.float32(np.sqrt(252.0))
    F['rv_chg'] = _ratio_div(F['rv_20'], F['rv_60']) - 1.0
    F['rv_ma60'] = roll_stat(F['rv_20'], 60, 'mean', mp=30)
    v5 = roll_stat(V, 5, 'mean')
    v10 = roll_stat(V, 10, 'mean')
    v20 = roll_stat(V, 20, 'mean')
    v60 = roll_stat(V, 60, 'mean')
    F['v_ma5_ma20'] = _ratio_div(v5, v20)
    F['v_trend'] = F['v_ma5_ma20'] - 1.0
    F['v_slope'] = _ratio_div(roll_stat(V, PREREG['vol_slope_window'], 'slope'), v20)
    F['v_accel'] = F['v_slope'] - _shift(F['v_slope'], PREREG['accel_lag'])
    F['dd_vol'] = _ratio_div(v10, v60) - 1.0
    F['amt_ma20'] = roll_stat(AMT, 20, 'mean')
    for w in (18, 20, 25, 60):
        F['rs_%d' % w] = _sub(F['ret_%d' % w], idxr[w])
    F['rs_20p'] = _sub(F['ret_20'], idxr['p20'])
    F['rs_60p'] = _sub(F['ret_60'], idxr['p60'])
    F['rs_slope'] = _sub(F['slope_20n'], idxr['slope20n'])
    h60 = roll_stat(Cq, 60, 'max')
    F['dd_60'] = _ratio_div(Cq, h60) - 1.0
    F['dd_min10'] = roll_stat(F['dd_60'], 10, 'min')
    F['dd_dur'] = roll_stat(Cq, 60, 'argmax')
    F['dd_rs'] = _sub(F['ret_10'], idxr[10])
    F['ma20_dist'] = F['c_ma20']
    F['ma60_dist'] = F['c_ma60']
    F['recov_10'] = _ratio_div(Cq, roll_stat(Cq, 10, 'min')) - 1.0
    ts_set = ((ma[20] > ma[60]) & (Cq > ma[20])).astype(np.float64)
    F['trendset'] = ts_set.astype(np.float32)
    F['trend_len60'] = roll_stat(ts_set, 60, 'sum', mp=40)
    F['up_recent20'] = F['ret_20']
    F['ret_1'] = r1
    return F


# ------------------------------------------------------------------ §18-§22 目标
def code_targets(Cq, Hq, Lq, Oq, O, PC, V, idxr):
    n = len(Cq)
    T = {}
    for k in HZ:
        f = np.full(n, np.nan, dtype=np.float32)
        if n > k:
            with np.errstate(invalid='ignore', divide='ignore'):
                f[:-k] = Cq[k:] / Cq[:-k] - 1.0
        T['fwd_%d' % k] = f
        T['rel_%d' % k] = _sub(f, idxr[k])
    eo = np.full(n, np.nan, dtype=np.float32)
    eo[:-1] = Oq[1:]
    for k in HZ:
        e = np.full(n, np.nan, dtype=np.float32)
        if n > k + 1:
            with np.errstate(invalid='ignore', divide='ignore'):
                e[:-k - 1] = Cq[k + 1:] / Oq[1:-k]
        T['exe_%d' % k] = e
    T['entry_open'] = eo
    for k in (5, 10, 20, 60):
        fm = roll_stat(Hq, k, 'max')
        fl = roll_stat(Lq, k, 'min')
        mfe = np.full(n, np.nan, dtype=np.float32)
        mae = np.full(n, np.nan, dtype=np.float32)
        if n > k:
            with np.errstate(invalid='ignore', divide='ignore'):
                mfe[:-k] = fm[k:] / Cq[:-k] - 1.0
                mae[:-k] = fl[k:] / Cq[:-k] - 1.0
        T['mfe_%d' % k] = mfe
        T['mae_%d' % k] = mae
    # §21 延续概率（三个独立定义，预注册）
    fmax20 = roll_stat(Cq, 20, 'max')
    h60 = roll_stat(Cq, 60, 'max')
    c1 = np.full(n, np.nan, dtype=np.float32)
    if n > 20:
        c1[:-20] = (fmax20[20:] > h60[:-20]).astype(np.float32)
    T['cont_nh20'] = c1
    ratio = _ratio_div(Cq, roll_stat(Cq, 20, 'mean'))
    fmin20 = roll_stat(ratio, 20, 'min', mp=20)
    c2 = np.full(n, np.nan, dtype=np.float32)
    if n > 20:
        c2[:-20] = (fmin20[20:] > 1.0).astype(np.float32)
    T['cont_ma20'] = c2
    T['cont_rs20'] = np.where(np.isfinite(T['rel_20']),
                              (T['rel_20'] > 0).astype(np.float32), np.nan)
    # §22 趋势终结（预注册四定义）
    ma20 = roll_stat(Cq, 20, 'mean')
    ma60 = roll_stat(Cq, 60, 'mean')
    for nm, mref in (('fail_ma20', ma20), ('fail_ma60', ma60)):
        b = np.isfinite(Cq) & np.isfinite(mref) & (Cq < mref)
        g = roll_stat(b.astype(np.float64), 2, 'sum', mp=2)
        hit = (g >= 2).astype(np.float64)
        any20 = roll_stat(hit, 20, 'max', mp=1)
        r = np.full(n, np.nan, dtype=np.float32)
        if n > 20:
            r[:-20] = (any20[20:] > 0).astype(np.float32)
        T[nm] = r
    low60 = roll_stat(Cq, 60, 'min')
    b = np.isfinite(Cq) & np.isfinite(_shift(low60, 1)) & (Cq < _shift(low60, 1))
    any20 = roll_stat(b.astype(np.float64), 20, 'max', mp=1)
    r = np.full(n, np.nan, dtype=np.float32)
    if n > 20:
        r[:-20] = (any20[20:] > 0).astype(np.float32)
    T['fail_low'] = r
    T['fail_rs'] = np.where(np.isfinite(T['rel_20']),
                            (T['rel_20'] < -0.10).astype(np.float32), np.nan)
    # 可交易性：T+1 开盘未涨停 / 未停牌
    ok = np.full(n, np.nan, dtype=np.float32)
    if n > 1:
        lu = O[1:] >= PC[1:] * 1.095
        good = np.isfinite(Oq[1:]) & np.isfinite(V[1:]) & (V[1:] > 0) & (~lu)
        ok[:-1] = good.astype(np.float32)
    T['buy_ok'] = ok
    return T


# ------------------------------------------------------------------ 静态元数据
def load_meta(td, NCAL, codes):
    """股票元信息：上市日 / 时点 ST / 退市 / 行业（申万一级 + Tushare）"""
    n = len(codes)
    cmap = pd.Series(np.arange(n), index=codes)
    list_date = {}
    ind_tx_raw = {}
    fp = os.path.join(CD, 'stock_basic.csv')
    if os.path.exists(fp):
        d = pd.read_csv(fp, dtype=str)
        list_date = dict(zip(d['ts_code'], d['list_date']))
        ind_tx_raw = dict(zip(d['ts_code'], d['industry']))
    sw_raw = {}
    fp = os.path.join(CD, 'industry', 'sw_industry_map.csv')
    if os.path.exists(fp):
        d = pd.read_csv(fp, dtype=str)
        d = d[d['ts_code'].notna()].sort_values('in_date')
        sw_raw = dict(zip(d['ts_code'], d['l1_name']))
    log('  元信息: stock_basic %d 只 (list_date/industry)，SW L1 %d 只'
        % (len(list_date), len(sw_raw)))

    def _enc(series_names):
        cats = sorted(set(x for x in series_names if isinstance(x, str) and x))
        m = {c: i + 1 for i, c in enumerate(cats)}      # 0 = UNKNOWN
        return m

    sw_map = _enc(sw_raw.values())
    tx_map = _enc(ind_tx_raw.values())
    ind_l1 = np.array([sw_map.get(sw_raw.get(c), 0) for c in codes], dtype=np.int16)
    ind_tx = np.array([tx_map.get(ind_tx_raw.get(c), 0) for c in codes], dtype=np.int16)
    pd.DataFrame({'code': list(tx_map.keys()), 'id': list(tx_map.values())}
                 ).to_csv(os.path.join(DATA, 'tl_ind_tx_map.csv'), index=False)
    pd.DataFrame({'code': list(sw_map.keys()), 'id': list(sw_map.values())}
                 ).to_csv(os.path.join(DATA, 'tl_ind_l1_map.csv'), index=False)

    k_list = np.full(n, -1, dtype=np.int32)
    for i, c in enumerate(codes):
        ld = list_date.get(c)
        if isinstance(ld, str) and len(ld) == 8 and ld.isdigit():
            k_list[i] = int(np.searchsorted(td, ld, side='left'))
    log('  上市日可得 %d / %d' % (int((k_list >= 0).sum()), n))

    STM = np.zeros((n, NCAL), dtype=np.int8)
    RET = np.zeros((n, NCAL), dtype=np.int8)
    files = glob.glob(os.path.join(CD, 'treasure_namechg_*.parquet'))
    frames = []
    for fp in files:
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
        st_flag = nm.str.contains('ST', na=False).values
        rt_flag = nm.str.contains('退', na=False).values
        ci = d['ts_code'].map(cmap).values
        k1 = np.searchsorted(td, d['start_date'].values, side='left')
        k2 = np.searchsorted(td, d['end_date'].values, side='right') - 1
        for j in range(len(d)):
            if not np.isfinite(ci[j]):
                continue
            a, b = int(max(k1[j], 0)), int(min(k2[j], NCAL - 1))
            if b < a:
                continue
            if st_flag[j]:
                STM[int(ci[j]), a:b + 1] = 1
            if rt_flag[j]:
                RET[int(ci[j]), a:b + 1] = 1
            nrec += 1
    log('  名称变更记录 %d 条（时点 ST/退市标记）；ST 单元 %.4f，退市单元 %.4f'
        % (nrec, float(STM.mean()), float(RET.mean())))
    return ind_l1, ind_tx, sw_map, tx_map, k_list, STM, RET


# ------------------------------------------------------------------ 主流程
def main():
    t0 = time.time()
    log('=' * 78)
    log('TL-01 数据集构建  hypothesis=%s  %s' % (PREREG['hypothesis_id'], PREREG['title']))

    # ---------- 1) 日历 ----------
    cal = pd.read_parquet(os.path.join(FS_DATA, 'calendar.parquet'))
    td = cal['trade_date'].astype(str).values
    NCAL = len(td)
    kmap = pd.Series(np.arange(NCAL), index=td)
    YY = td.astype('U4').astype(np.int16)
    MM = np.array([int(x[4:6]) for x in td], dtype=np.int16)
    log('-' * 78)
    log('1) 日历 %d 个交易日  %s ~ %s' % (NCAL, td[0], td[-1]))

    # ---------- 2) 行情 ----------
    px = pd.read_parquet(os.path.join(FS_DATA, 'price_panel.parquet'),
                         columns=['ts_code', 'trade_date'] + PCOLS)
    px['trade_date'] = px['trade_date'].astype(str)
    tcat = px['ts_code'].astype('category')
    cats = np.asarray(tcat.cat.categories)
    o2 = np.argsort(cats)
    codes = cats[o2]
    remap = np.empty(len(cats), dtype=np.int32)
    remap[o2] = np.arange(len(cats), dtype=np.int32)
    ci = remap[tcat.cat.codes.values.astype(np.int64)].astype(np.int32)
    del tcat
    kk = px['trade_date'].map(kmap).values
    okm = np.isfinite(kk)
    kk = kk[okm].astype(np.int32)
    ci = ci[okm]
    npx = len(ci)
    P = {}
    for c in PCOLS:
        v = pd.to_numeric(px[c], errors='coerce').values.astype(np.float32)[okm]
        P[c] = v
    del px
    order = np.lexsort((kk, ci))
    ci = ci[order]
    kk = kk[order]
    for c in PCOLS:
        P[c] = P[c][order]
    NCODES = len(codes)
    starts = np.append(np.searchsorted(ci, np.arange(NCODES), side='left'), npx)
    log('2) 行情 %d 行 / %d 只（已按 code,date 排序）' % (npx, NCODES))

    # ---------- 3) 元信息 ----------
    ind_l1, ind_tx, sw_map, tx_map, k_list, STM, RET = load_meta(td, NCAL, codes)
    is_bj = np.array([1 if c.endswith('.BJ') else 0 for c in codes], dtype=np.int8)

    # ---------- 4) 自建全 A 等权 PROXY ----------
    Cq = P['qfq_close']
    sum_r = np.zeros(NCAL)
    cnt_r = np.zeros(NCAL)
    for c in range(NCODES):
        a, b = starts[c], starts[c + 1]
        if b <= a:
            continue
        y = np.full(NCAL, np.nan, dtype=np.float64)
        y[kk[a:b]] = Cq[a:b]
        r = np.full(NCAL, np.nan, dtype=np.float64)
        r[1:] = y[1:] / y[:-1] - 1.0
        r[np.abs(r) > 0.5] = np.nan
        f = np.isfinite(r)
        sum_r[f] += r[f]
        cnt_r[f] += 1.0
    mret = np.where(cnt_r > 0, sum_r / np.maximum(cnt_r, 1), 0.0)
    proxy = np.cumprod(1.0 + mret)
    log('3) 自建全 A 等权 PROXY：%d 天，末日 %.3f，日收益均值 %.5f'
        % (int((cnt_r > 0).sum()), proxy[-1], np.nanmean(mret)))

    # ---------- 5) 沪深 300 ----------
    idx = pd.read_parquet(os.path.join(FS_DATA, 'index_panel.parquet'))
    idx = idx[idx['ts_code'] == '000300.SH'].copy()
    idx['k'] = idx['trade_date'].astype(str).map(kmap)
    idx = idx[idx['k'].notna()].sort_values('k')
    hs = np.full(NCAL, np.nan)
    hs[idx['k'].values.astype(int)] = pd.to_numeric(idx['close'], errors='coerce').values
    hs = pd.Series(hs).ffill().bfill().values
    log('4) 沪深300 %d 天  %.1f ~ %.1f' % (len(hs), np.nanmin(hs), np.nanmax(hs)))

    # ---------- 6) 基准收益 & Regime ----------
    def _lvl_ret(L, w):
        L = np.asarray(L, dtype=np.float64)
        out = np.full(NCAL, np.nan)
        if NCAL > w:
            out[w:] = L[w:] / L[:-w] - 1.0
        return out.astype(np.float32)

    idxr = {}
    for w in (3, 5, 10, 18, 20, 25, 40, 60, 120):
        idxr[w] = _lvl_ret(hs, w)
    idxr['p20'] = _lvl_ret(proxy, 20)
    idxr['p60'] = _lvl_ret(proxy, 60)
    idxr['slope20n'] = roll_stat(hs, 20, 'slope') / np.asarray(hs, dtype=np.float64)
    idxr['proxy'] = proxy.astype(np.float32)
    idxr['hs300'] = hs.astype(np.float32)
    p_lvl = pd.Series(proxy)
    p_ma200 = p_lvl.rolling(200, min_periods=60).mean()
    p_r60 = p_lvl / p_lvl.shift(60) - 1.0
    reg = np.ones(NCAL, dtype=np.int8)                       # 1 = NORMAL
    reg[((p_lvl > p_ma200) & (p_r60 > 0)).values] = 2        # 2 = BULL
    reg[((p_lvl < p_ma200) & (p_r60 < 0)).values] = 0        # 0 = BEAR
    log('5) Regime(PROXY: 200MA + 60日动量)：BEAR %d / NORMAL %d / BULL %d 天'
        % (int((reg == 0).sum()), int((reg == 1).sum()), int((reg == 2).sum())))

    # ---------- 7) P1 时序特征 ----------
    log('-' * 78)
    log('6) P1 逐股时序特征（§6–§15）+ 前向目标（§18–§22）')
    outts = os.path.join(DATA, 'tl_ts_daily.parquet')
    if os.path.exists(outts):
        os.remove(outts)
    import pyarrow as pa
    import pyarrow.parquet as pq
    writer = None
    cw, cv, cp, ch, cl = P['qfq_open'], P['qfq_close'], P['qfq_pre_close'], P['qfq_high'], P['qfq_low']
    c_o, c_pc, c_v, c_a = P['open'], P['pre_close'], P['vol'], P['amount']

    if '--leakonly' in sys.argv:                 # 快路径：不重算 P1-P4
        log('LEAK-ONLY：跳过 P1-P4，仅执行 §3 泄漏自检')
        _leak_test(codes, kk, starts, NCAL, td, idxr,
                   cv, ch, cl, cp, c_v, c_a, default_cfg())
        log.save()
        print('DONE(leakonly)')
        return

    bas = pd.read_parquet(os.path.join(FS_DATA, 'basic_panel.parquet'),
                          columns=['ts_code', 'trade_date'] + BCOLS)
    bas['trade_date'] = bas['trade_date'].astype(str)
    bci_all = pd.Categorical(bas['ts_code'], categories=codes).codes.astype(np.int32)
    bkk_all = bas['trade_date'].map(kmap).values
    bok = (bci_all >= 0) & np.isfinite(bkk_all)
    bci = bci_all[bok]
    bkk = bkk_all[bok].astype(np.int32)
    Bd = {c: pd.to_numeric(bas[c], errors='coerce').values.astype(np.float32)[bok]
          for c in BCOLS}
    del bas, bci_all, bkk_all
    bord = np.lexsort((bkk, bci))
    bci, bkk = bci[bord], bkk[bord]
    for c in BCOLS:
        Bd[c] = Bd[c][bord]
    bstarts = np.append(np.searchsorted(bci, np.arange(NCODES), side='left'), len(bci))
    log('  估值面板 %d 行 / %d 只' % (len(bci), NCODES))

    NVALID = 0
    for ci0 in range(0, NCODES, CHUNK):
        ci1 = min(ci0 + CHUNK, NCODES)
        acc = {}
        for c in range(ci0, ci1):
            a, b = starts[c], starts[c + 1]
            if b <= a:
                continue
            ks = kk[a:b]
            def _pick(arr, a=a, b=b, ks=ks):
                y = np.full(NCAL, np.nan, dtype=np.float32)
                y[ks] = arr[a:b]
                return y
            cq, chh, cll, cpq = _pick(cv), _pick(ch), _pick(cl), _pick(cp)
            coq, co, cpc, cvv, cam = (_pick(cw), _pick(c_o), _pick(c_pc),
                                      _pick(c_v), _pick(c_a))
            F = code_features(cq, chh, cll, cpq, cvv, cam, idxr)
            T = code_targets(cq, chh, cll, coq, co, cpc, cvv, idxr)
            aB, bB = bstarts[c], bstarts[c + 1]
            for cn in BCOLS:
                y = np.full(NCAL, np.nan, dtype=np.float32)
                if bB > aB:
                    y[bkk[aB:bB]] = Bd[cn][aB:bB]
                F['bm_' + cn] = y
            hist = np.cumsum(np.isfinite(cq).astype(np.int32)).astype(np.int32)
            k0 = k_list[c]
            if k0 < 0:
                listd = hist.copy()
            else:
                listd = np.maximum(np.arange(NCAL, dtype=np.int32) - k0 + 1, 0)
                listd = np.where(hist > 0, listd, 0)
            susp = (~np.isfinite(cvv) | (cvv <= 0) | ~np.isfinite(cq)).astype(np.int8)
            stv = STM[c]
            rtv = RET[c]
            valid = ((is_bj[c] == 0) & (stv == 0) & (rtv == 0) & (susp == 0)
                     & (hist >= PREREG['min_hist_days'])
                     & (listd >= PREREG['listing_min_days'])
                     & np.isfinite(cq))
            if ci0 == 0 and c == 0:
                pass
            row = {'ts_code': np.full(b - a, codes[c], dtype=object),
                   'trade_date': td[ks],
                   'ci': np.full(b - a, c, dtype=np.int32),
                   'k': ks.astype(np.int32)}
            row['is_bj'] = np.full(b - a, is_bj[c], dtype=np.int8)
            row['is_st'] = stv[ks].astype(np.int8)
            row['is_retire'] = rtv[ks].astype(np.int8)
            row['is_susp'] = susp[ks].astype(np.int8)
            row['listing_days'] = listd[ks].astype(np.int32)
            row['hist_days'] = hist[ks].astype(np.int32)
            row['valid'] = valid[ks].astype(np.int8)
            row['year'] = YY[ks]
            row['month'] = MM[ks]
            row['ind_l1'] = np.full(b - a, ind_l1[c], dtype=np.int16)
            row['ind_tx'] = np.full(b - a, ind_tx[c], dtype=np.int16)
            row['regime'] = reg[ks].astype(np.int8)
            for k2, v in F.items():
                row[k2] = v[ks]
            for k2, v in T.items():
                row[k2] = v[ks]
            NVALID += int(valid[ks].sum())
            for k2, v in row.items():
                acc.setdefault(k2, []).append(v)
        if not acc:
            continue
        df = pd.DataFrame({k2: np.concatenate(v) for k2, v in acc.items()})
        keep = [x for x in df.columns if x not in ('ts_code', 'trade_date')]
        keep = keep + ['ts_code', 'trade_date']
        df = df[keep]
        tbl = pa.Table.from_pandas(df, preserve_index=False)
        if writer is None:
            writer = pq.ParquetWriter(outts, tbl.schema, compression='snappy')
        writer.write_table(tbl)
        if (ci0 // CHUNK) % 5 == 0:
            log('  块 %d/%d 累计行 %d 有效 %d  %.0fs'
                % (ci0 // CHUNK, int(np.ceil(NCODES / CHUNK)), len(df), NVALID,
                   time.time() - t0))
        del df, tbl, acc
    writer.close()
    log('  P1 完成：%s  有效样本 %d 行  %.0fs' % (outts, NVALID, time.time() - t0))
    del P

    # ---------- 8) P2 横截面特征 ----------
    log('-' * 78)
    log('7) P2 横截面特征（§7 TrendStrength_RAW 等权六分量）')
    pf = pq.ParquetFile(outts)
    log('  row groups = %d' % pf.num_row_groups)
    stats = {}
    for blk in BLOCKS:
        t = pf.read(columns=['k', 'valid', blk])
        kv = t.column('k').to_numpy()
        vv = t.column(blk).to_numpy().astype(np.float64)
        vm = t.column('valid').to_numpy()
        m = (vm == 1) & np.isfinite(vv)
        mu, sd, q1, q9 = _date_stats(kv[m], vv[m], NCAL)
        stats[blk] = (mu, sd, q1, q9)
        del t
    t = pf.read(columns=['k', 'valid', 'atr_pct'])
    kv = t.column('k').to_numpy()
    vv = t.column('atr_pct').to_numpy().astype(np.float64)
    vm = t.column('valid').to_numpy()
    m = (vm == 1) & np.isfinite(vv)
    atr_pct_pct, atr_med = _date_rankpct(kv, vv, m, NCAL)
    del t
    log('  横截面统计完成（%d 天 × %d 块）' % (NCAL, len(BLOCKS)))

    outxs = os.path.join(DATA, 'tl_xs_daily.parquet')
    if os.path.exists(outxs):
        os.remove(outxs)
    writer = None
    roff = 0                       # 已处理行数：_date_rankpct 的首返回值按「全表行序」对齐
    for g in range(pf.num_row_groups):
        t = pf.read_row_group(g, columns=['k'] + BLOCKS + ['atr_pct'])
        kv = t.column('k').to_numpy()
        cols = {}
        for blk in BLOCKS:
            mu, sd, q1, q9 = stats[blk]
            v = t.column(blk).to_numpy().astype(np.float64)
            v = np.clip(v, q1[kv], q9[kv])
            with np.errstate(invalid='ignore', divide='ignore'):
                z = (v - mu[kv]) / sd[kv]
            cols['z_' + blk] = (z * BLOCK_SIGNS[blk]).astype(np.float32)
        for nm, blocks in TS_OF.items():
            s = np.zeros(len(kv), dtype=np.float64)
            cnt = np.zeros(len(kv), dtype=np.float64)
            for blk in blocks:
                zz = cols['z_' + blk]
                f = np.isfinite(zz)
                s[f] += zz[f]
                cnt[f] += 1.0
            cols[nm] = np.where(cnt >= 4, s / np.maximum(cnt, 1), np.nan).astype(np.float32)
        ap = t.column('atr_pct').to_numpy().astype(np.float64)
        # _date_rankpct 首返回值与「全表行序」等长（非按交易日），按累积行偏移切片对齐
        ng = t.num_rows
        app = atr_pct_pct[roff:roff + ng]
        roff += ng
        cols['atr_pct_pct'] = np.where(np.isfinite(ap), app, np.nan).astype(np.float32)
        df = pd.DataFrame(cols)
        if 'ts_raw' not in df.columns:
            df['ts_raw'] = df['ts_20']
        df = df[['ts_18', 'ts_raw', 'ts_25', 'atr_pct_pct']]
        tbl = pa.Table.from_pandas(df, preserve_index=False)
        if writer is None:
            writer = pq.ParquetWriter(outxs, tbl.schema, compression='snappy')
        writer.write_table(tbl)
        del t, df, tbl
    writer.close()
    log('  P2 完成：%s' % outxs)

    # ---------- 9) P3 状态 / 年龄 / 持续 ----------
    log('-' * 78)
    log('8) P3 Model A 规则状态 + 趋势年龄 + 状态持续（§16/§23/§24）')
    cfg = default_cfg()
    log('  cfg = %s' % cfg)
    FCOLS = ['k', 'ci', 'ret_18', 'ret_20', 'ret_25', 'c_ma18', 'c_ma20', 'c_ma22',
             'c_ma60', 'ma18_ma60', 'ma20_ma60', 'ma22_ma60', 'ma60_slope', 'slope_20',
             'pos_60', 'pos_120', 'rs_18', 'rs_20', 'rs_25', 'atr_pct', 'rv_20',
             'v_trend', 'v_ma5_ma20', 'dd_60', 'dd_min10', 'trend_len60', 'trendset']
    outst = os.path.join(DATA, 'tl_state_daily.parquet')
    if os.path.exists(outst):
        os.remove(outst)
    writer = None
    seg_err = 0
    sd_sum = 0
    for g in range(pf.num_row_groups):
        t = pf.read_row_group(g, columns=FCOLS)
        tx = pq.ParquetFile(outxs).read_row_group(g, columns=['ts_18', 'ts_raw', 'ts_25',
                                                              'atr_pct_pct'])
        d = {c: t.column(c).to_numpy() for c in FCOLS}
        d['ts_18'] = tx.column('ts_18').to_numpy()
        d['ts_raw'] = tx.column('ts_raw').to_numpy()
        d['ts_25'] = tx.column('ts_25').to_numpy()
        d['atr_pct_pct'] = tx.column('atr_pct_pct').to_numpy()
        d['up_recent20'] = d['ret_20']
        tv = np.full(len(d['k']), np.nan, dtype=np.float32)
        tva18 = np.full(len(d['k']), np.nan, dtype=np.float32)
        tva25 = np.full(len(d['k']), np.nan, dtype=np.float32)
        ta = np.full(len(d['k']), np.nan, dtype=np.float32)
        d['tv'] = tv
        d['tv_alt18'] = tva18
        d['tv_alt25'] = tva25
        d['ta'] = ta
        n = len(d['k'])
        state = np.full(n, -1, dtype=np.int8)
        age = np.zeros(n, dtype=np.int32)
        dur = np.zeros(n, dtype=np.int32)
        prev = np.full(n, -1, dtype=np.int8)
        reacc = np.full(n, np.nan, dtype=np.float32)
        civec = d['ci']
        kvec = d['k']
        bnd = np.flatnonzero(np.diff(civec)) + 1
        bnd = np.concatenate(([0], bnd, [n]))
        for j in range(len(bnd) - 1):
            a, b = bnd[j], bnd[j + 1]
            if b <= a:
                continue
            # 段内（同一股票）计算 TrendVelocity / TrendAcceleration，避免跨股票 shift
            for nm, src in (('tv', 'ts_raw'), ('tva18', 'ts_18'), ('tva25', 'ts_25')):
                ss = np.asarray(d[src][a:b], dtype=np.float64)
                v = ss - _shift(ss, PREREG['velocity_lag'])
                {'tv': tv, 'tva18': tva18, 'tva25': tva25}[nm][a:b] = v
            ta[a:b] = tv[a:b] - _shift(tv[a:b], PREREG['accel_lag'])
            Fsub = {kk2: vv[a:b] for kk2, vv in d.items() if kk2 not in ('ci', 'k')}
            try:
                S = assign_state(Fsub, cfg)
            except Exception:
                seg_err += 1
                continue
            state[a:b] = S
            pv = -1
            ag = 0
            du = 0
            kv = kvec[a:b]
            for i2 in range(b - a):
                contiguous = (i2 == 0) or (kv[i2] - kv[i2 - 1] == 1)
                s_now = int(S[i2])
                prev[a + i2] = pv if (contiguous and pv >= 0) else -1
                du = du + 1 if (contiguous and pv == s_now) else 1
                dur[a + i2] = du
                if s_now in PREREG['trend_states']:
                    ag = ag + 1 if (contiguous and pv in PREREG['trend_states']) else 1
                else:
                    ag = 0
                age[a + i2] = ag
                pv = s_now
            # §15 Potential_Reacceleration（六个时点可观测条件之和，0-6）
            dd = d['dd_60'][a:b]
            dmin = d['dd_min10'][a:b]
            tvv = d['tv'][a:b]
            rvv = d['rv_20'][a:b]
            rvmed = roll_stat(rvv, 60, 'mean', mp=30)
            cond = np.vstack([
                (dmin <= -PREREG['th_reacc_dd_lo']) & (dmin >= -PREREG['th_reacc_dd_hi']),
                tvv > 0,
                d['rs_20'][a:b] > 0,
                d['c_ma20'][a:b] > 0,
                d['v_ma5_ma20'][a:b] < 1.0,
                rvv < rvmed,
            ]).astype(np.float64)
            reacc[a:b] = cond.sum(0).astype(np.float32)
        df = pd.DataFrame({'ci': civec, 'k': kvec, 'state': state, 'state_prev': prev,
                           'trend_age': age, 'state_dur': dur, 'reacc_raw': reacc,
                           'tv': tv, 'ta': ta, 'tv_alt18': tva18, 'tv_alt25': tva25})
        df['state_chg'] = ((df['state'] != df['state_prev'])
                           & (df['state_prev'] >= 0)).astype(np.int8)
        tbl = pa.Table.from_pandas(df, preserve_index=False)
        if writer is None:
            writer = pq.ParquetWriter(outst, tbl.schema, compression='snappy')
        writer.write_table(tbl)
        sd_sum += int((df['state'] < 0).sum())
        del t, tx, d, df, tbl
    writer.close()
    log('  P3 完成：%s  状态缺失 %d  状态机异常块 %d' % (outst, sd_sum, seg_err))

    # ---------- 10) P4 组装交付物 ----------
    log('-' * 78)
    log('9) P4 组装 tl01_feature_daily / tl01_state_daily（仅 valid=1 行）')
    FEAT_TS = ['ts_code', 'trade_date', 'ci', 'k', 'year', 'month', 'ind_l1', 'ind_tx',
               'regime', 'listing_days', 'hist_days',
               'ret_5', 'ret_10', 'ret_20', 'ret_40', 'ret_60', 'ret_120',
               'ma5', 'ma10', 'ma20', 'ma60', 'ma120', 'c_ma18', 'c_ma20', 'c_ma22',
               'c_ma60', 'ma18_ma60', 'ma20_ma60', 'ma22_ma60', 'ma20_slope',
               'ma60_slope', 'ma120_slope', 'slope_10', 'slope_20', 'slope_40',
               'slope_60', 'slope_10n', 'slope_20n', 'slope_40n', 'slope_60n',
               'pos_20', 'pos_60', 'pos_120', 'atr', 'atr_pct', 'rv_20', 'rv_60',
               'rv_chg', 'v_ma5_ma20', 'v_trend', 'v_slope', 'v_accel', 'dd_vol',
               'amt_ma20', 'rs_18', 'rs_20', 'rs_25', 'rs_60', 'rs_20p', 'rs_60p',
               'rs_slope', 'dd_60', 'dd_min10', 'dd_dur', 'dd_rs', 'ma20_dist',
               'ma60_dist', 'recov_10', 'trend_len60', 'valid',
               'bm_total_mv', 'bm_circ_mv', 'bm_pe_ttm', 'bm_pb', 'bm_ps_ttm',
               'bm_dv_ttm', 'bm_turnover_rate', 'bm_volume_ratio',
               'fwd_3', 'fwd_5', 'fwd_10', 'fwd_20', 'fwd_60',
               'rel_3', 'rel_5', 'rel_10', 'rel_20', 'rel_60',
               'exe_3', 'exe_5', 'exe_10', 'exe_20', 'exe_60',
               'mfe_5', 'mfe_10', 'mfe_20', 'mfe_60', 'mae_5', 'mae_10', 'mae_20',
               'mae_60', 'cont_nh20', 'cont_ma20', 'cont_rs20',
               'fail_ma20', 'fail_ma60', 'fail_low', 'fail_rs', 'entry_open', 'buy_ok']
    pfx = pq.ParquetFile(outxs)
    pfs = pq.ParquetFile(outst)
    f1 = os.path.join(HERE, 'tl01_feature_daily.parquet')
    f2 = os.path.join(HERE, 'tl01_state_daily.parquet')
    for f in (f1, f2):
        if os.path.exists(f):
            os.remove(f)
    w1 = w2 = None
    rows_feat = rows_state = 0
    for g in range(pf.num_row_groups):
        tt = pf.read_row_group(g, columns=FEAT_TS)
        tx = pfx.read_row_group(g, columns=['ts_18', 'ts_raw', 'ts_25', 'atr_pct_pct'])
        tst = pfs.read_row_group(g, columns=['ci', 'k', 'state', 'state_prev', 'state_chg',
                                             'trend_age', 'state_dur', 'reacc_raw', 'tv', 'ta',
                                             'tv_alt18', 'tv_alt25'])
        vm = tt.column('valid').to_numpy() == 1
        idx = np.flatnonzero(vm)
        if len(idx) == 0:
            continue
        cols = {}
        for c in FEAT_TS:
            cols[c] = tt.column(c).to_numpy()[idx]
        for c in ('ts_18', 'ts_raw', 'ts_25', 'atr_pct_pct'):
            cols[c] = tx.column(c).to_numpy()[idx]
        for c in ('tv', 'ta', 'tv_alt18', 'tv_alt25', 'reacc_raw'):
            cols[c] = tst.column(c).to_numpy()[idx]
        df = pd.DataFrame(cols)
        tbl = pa.Table.from_pandas(df, preserve_index=False)
        if w1 is None:
            w1 = pq.ParquetWriter(f1, tbl.schema, compression='snappy')
        w1.write_table(tbl)
        rows_feat += len(df)
        cols2 = {'ts_code': df['ts_code'].values, 'trade_date': df['trade_date'].values,
                 'ci': df['ci'].values, 'k': df['k'].values}
        for c in ('state', 'state_prev', 'state_chg', 'trend_age', 'state_dur',
                  'reacc_raw', 'tv', 'ta'):
            cols2[c] = tst.column(c).to_numpy()[idx]
        df2 = pd.DataFrame(cols2)
        tbl2 = pa.Table.from_pandas(df2, preserve_index=False)
        if w2 is None:
            w2 = pq.ParquetWriter(f2, tbl2.schema, compression='snappy')
        w2.write_table(tbl2)
        rows_state += len(df2)
        del tt, tx, tst, df, tbl, df2, tbl2
    w1.close()
    w2.close()
    log('  %s  %d 行' % (os.path.basename(f1), rows_feat))
    log('  %s  %d 行' % (os.path.basename(f2), rows_state))

    # ---------- 11) §3 泄漏自检 ----------
    log('-' * 78)
    log('10) §3 Point-in-Time / Leakage Check（截断重算比对）')
    _leak_test(codes, kk, starts, NCAL, td, idxr,
               cv, ch, cl, cp, c_v, c_a, cfg)

    log('=' * 78)
    log('构建完成  %.0fs' % (time.time() - t0))
    log.save()
    print('DONE')


# ------------------------------------------------------------------ 工具
def _date_stats(kv, v, NCAL):
    """按交易日计算（winsorize 1%/99% 后的）均值/标准差与分位阈值"""
    mu = np.full(NCAL, np.nan)
    sd = np.full(NCAL, np.nan)
    q1 = np.full(NCAL, np.nan)
    q9 = np.full(NCAL, np.nan)
    if len(kv) == 0:
        return mu, sd, q1, q9
    o = np.argsort(kv, kind='stable')
    sk = kv[o]
    sv = v[o]
    b = np.searchsorted(sk, np.arange(NCAL + 1))
    for g in range(NCAL):
        a2, b2 = b[g], b[g + 1]
        if b2 - a2 < 20:
            continue
        seg = sv[a2:b2]
        lo, hi = np.quantile(seg, [0.01, 0.99])
        seg2 = np.clip(seg, lo, hi)
        mu[g] = seg2.mean()
        sd[g] = seg2.std()
        q1[g], q9[g] = lo, hi
    return mu, sd, q1, q9


def _date_rankpct(kv, v, m, NCAL):
    """按交易日的横截面分位（0-1）与同日均值"""
    rp = np.full(len(kv), np.nan)
    med = np.full(NCAL, np.nan)
    k2, v2 = kv[m], v[m]
    o = np.argsort(k2, kind='stable')
    sk, sv = k2[o], v2[o]
    src_idx = np.flatnonzero(m)[o]
    b = np.searchsorted(sk, np.arange(NCAL + 1))
    for g in range(NCAL):
        a2, b2 = b[g], b[g + 1]
        if b2 - a2 < 20:
            continue
        seg = sv[a2:b2]
        med[g] = float(np.median(seg))
        r = np.argsort(np.argsort(seg)).astype(np.float64) / max(b2 - a2 - 1, 1)
        rp[src_idx[a2:b2]] = r
    return rp, med


def _standin(F, mp=250):
    """把横截面口径输入替换为「纯时序口径替身」，仅供泄漏自检使用（不进入正式流水线）。

    ts_raw      : 六分量块各自做 trailing z 后等权平均（正式版为当日横截面 z）
    atr_pct_pct : atr_pct 在过去 250 日中的分位（正式版为当日横截面分位）
    tv(tva*)    : ts_raw(t) - ts_raw(t-5)
    替身与正式版一样只使用 t 及之前的信息；本自检比对「全样本」与「截断到 t」两次计算
    在 t 处是否一致，以验证 code_features / tl_states 无未来引用。
    """
    zs_ = []
    for b2 in PREREG['ts_blocks']:
        sg = -1.0 if b2 == 'neg_rv20' else 1.0
        src = 'rv_20' if b2 == 'neg_rv20' else b2
        v = pd.Series(np.asarray(F[src], dtype=np.float64))
        mu = v.rolling(mp, min_periods=120).mean()
        sd = v.rolling(mp, min_periods=120).std()
        zs_.append(sg * (v - mu) / sd)
    Z = pd.concat(zs_, axis=1)
    tsr = Z.mean(axis=1).values.astype(np.float32)
    ap = pd.Series(np.asarray(F['atr_pct'], dtype=np.float64))
    app = ap.rolling(mp, min_periods=120).rank(pct=True).values.astype(np.float32)
    out = dict(F)
    out['ts_raw'] = tsr
    out['atr_pct_pct'] = app
    out['tv'] = (tsr - _shift(tsr, PREREG['velocity_lag'])).astype(np.float32)
    return out


def _leak_test(codes, kk, starts, NCAL, td, idxr, cv, ch, cl, cp, c_v, c_a, cfg):
    """§3 无未来函数自检：随机 (股票, 日期) 用「截断到 t」的数据重算，与全样本逐项比对。

    (1) code_features 的 9 个时序特征键
    (2) 状态机输入替身 ts_raw / atr_pct_pct / tv
    (3) assign_state 输出状态
    横截面口径的 P2 统计为「按交易日分组」实现（_date_stats / _date_rankpct），
    仅使用当日截面，与 t 之后的数据无关（代码级保证）。
    """
    rng = np.random.RandomState(PREREG['seed'] + 7)
    keys = ['ret_20', 'ma20', 'pos_60', 'dd_60', 'atr_pct', 'slope_20', 'rs_20',
            'trend_len60', 'v_ma5_ma20']
    bad = 0
    checked = 0
    picks = []
    for c in rng.permutation(len(codes)):
        if len(picks) >= 12:
            break
        if starts[c + 1] - starts[c] < 330:
            continue
        picks.append(int(c))
    for c in picks:
        a, b = starts[c], starts[c + 1]
        ks = kk[a:b]

        def _pick(arr):
            y = np.full(NCAL, np.nan, dtype=np.float32)
            y[ks] = arr[a:b]
            return y

        cq, chh, cll, cpq = _pick(cv), _pick(ch), _pick(cl), _pick(cp)
        cvv, cam = _pick(c_v), _pick(c_a)
        Ffull = code_features(cq, chh, cll, cpq, cvv, cam, idxr)
        Gfull = _standin(Ffull)
        Sfull = assign_state(Gfull, cfg)
        for tt in rng.choice(np.arange(300, b - a - 1), size=5, replace=False):
            t = int(ks[tt])

            def _cut(arr, t=t):
                y = np.full(NCAL, np.nan, dtype=np.float32)
                y[:t + 1] = np.asarray(arr, dtype=np.float32)[:t + 1]
                return y

            ir = {}
            for k2, v in idxr.items():
                if isinstance(v, np.ndarray):
                    z = np.full(len(v), np.nan, dtype=np.float32)
                    z[:t + 1] = np.asarray(v, dtype=np.float32)[:t + 1]
                    ir[k2] = z
                else:
                    ir[k2] = v
            Fc = code_features(_cut(cq), _cut(chh), _cut(cll), _cut(cpq),
                               _cut(cvv), _cut(cam), ir)
            Gc = _standin(Fc)
            Sc = assign_state(Gc, cfg)
            for k2 in keys + ['ts_raw', 'atr_pct_pct', 'tv']:
                v1 = (Ffull if k2 in keys else Gfull)[k2][t]
                v2 = (Fc if k2 in keys else Gc)[k2][t]
                checked += 1
                if not (np.isnan(v1) and np.isnan(v2)):
                    if not np.isclose(v1, v2, rtol=1e-5, atol=1e-6, equal_nan=True):
                        bad += 1
                        if bad <= 5:
                            log('    [LEAK] %s t=%s %s: full=%.6f trunc=%.6f'
                                % (codes[c], td[t], k2, v1, v2))
            checked += 1
            if Sfull[t] != Sc[t]:
                bad += 1
                if bad <= 5:
                    log('    [LEAK] %s t=%s state: full=%d trunc=%d'
                        % (codes[c], td[t], Sfull[t], Sc[t]))
    log('  股票 %d 只 × 5 时点；比对项 %d，不一致 %d  ->  %s'
        % (len(picks), checked, bad, 'PASS（无未来函数）' if bad == 0 else 'FAIL'))


if __name__ == '__main__':
    main()
