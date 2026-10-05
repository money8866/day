# -*- coding: utf-8 -*-
"""H-ZZ2K-A1 common layer — 中证2000增强 Alpha V1 公共数据层 / 方法层.

口径唯一来源: research/h_zz2k_alpha/H_ZZ2K_ALPHA_V1_SPEC.md (v1.1)
本模块只读依赖:
    fundamental_surprise_alpha/  (price_panel / basic_panel / panel / calendar /
                                  index_panel, 以及 fs_common)
    hve/hve_common.py            (build_grid: qfq OHLCV + st/delist/board)
    h_earn_fwd/hef_common.py     (calendar / 行业 / 统计工具)
    D:\\mystock\\cache_daily\\stock_data.db   (index_daily_cache: 932000.CSI)

纪律: 任何改动必须走 SPEC 的 V2 流程; 本文件不含任何结果依赖的开关。
"""
import os
import sys
import json
import time
import hashlib
import sqlite3
import warnings

import numpy as np
import pandas as pd

np.seterr(divide='ignore', invalid='ignore')
warnings.filterwarnings('ignore', message='All-NaN slice encountered')
warnings.filterwarnings('ignore', message='Mean of empty slice')

# ------------------------------------------------------------------ paths
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data')
OUT = os.path.join(HERE, 'out')
os.makedirs(DATA, exist_ok=True)
os.makedirs(OUT, exist_ok=True)

FS_DIR = os.path.abspath(os.path.join(HERE, '..', 'fundamental_surprise_alpha'))
FS_DATA = os.path.join(FS_DIR, 'data')
HVE_DIR = os.path.abspath(os.path.join(HERE, '..', 'hve'))
HEF_DIR = os.path.abspath(os.path.join(HERE, '..', 'h_earn_fwd'))

DB = r'D:\mystock\cache_daily\stock_data.db'
CD = r'D:\mystock\cache_daily'

SPEC_PATH = os.path.join(HERE, 'H_ZZ2K_ALPHA_V1_SPEC.md')
# V1.1 (39075 bytes). 见 SPEC §19 冻结记录; 不一致 -> EXIT 2.
SPEC_SHA256 = '427E6F9F1E145606C27064ECC0D07938E0EC614D687E113D958AE86FFD64A69D'

FP_ZZ2K_MEM = os.path.join(DATA, 'zz2k_members.parquet')
FP_BENCH_MEM = os.path.join(DATA, 'bench_members.parquet')

sys.path.insert(0, FS_DIR)
sys.path.insert(0, HEF_DIR)
sys.path.insert(0, HVE_DIR)
from hef_common import (load_calendar, load_industry_map, industry_map_fast,  # noqa
                        industry_at_fast, dummies, ic_stats, fmt, pct)      # noqa
import hve_common                                                            # noqa

# ------------------------------------------------------------------ constants
BENCH_ZZ = '932000.CSI'      # 主基准 (中证2000)
BENCH_MKT = '000300.SH'      # 市场因子 (全历史, §6.3 强制)
EXCLUDE_IDX = ('000300.SH', '000905.SH', '000852.SH')

HORIZONS = (5, 10, 20)
PRIMARY_H = 5
LABELS = ('y_ex_zz', 'y_ex_sec', 'y_ex_mkt')
COST_BP = (0, 15, 30, 50)
NW_LAG = 5
IC_MIN_N = 30
IC_MEAN_MIN = 0.01
IC_T_MIN = 2.0
MONO_MIN = 0.80
VALID_T_MIN = 1.5
DEDUP_IC_MIN = 0.005
IND_IC_MIN = 0.005
WF_IC_MIN = 0.005
PERT_STABLE = 0.60
PERT_FRAGILE = 0.40
# 数据可用性门禁 (非绩效阈值): 池内覆盖率低于此值的因子不进入类内正交化输入,
# 仍保留在全部 IC/分层/覆盖表中并标记 NO_COVERAGE (SPEC §16.4 要求含 0 覆盖因子).
ORTH_COV_MIN = 0.30

NULL_B = 2000
NULL_ROUNDS = 8
SEED = 20261004

MIN_LIST_DAYS = 250
LONG_SUSP_DAYS = 60

Q_GROUPS = (5, 10)

GROUPS = ('A1', 'A2', 'A3', 'A4', 'A5')
GROUP_NAMES = {
    'A1': 'Value/Quality/Growth', 'A2': 'Short-Term Reversal',
    'A3': 'Residual Momentum', 'A4': 'Technical Price-Volume',
    'A5': 'Residual Volatility',
}
# §11.3 预注册权重 (声明在先, 非数据优化)
COMP_PRIOR = {'A2': 0.30, 'A3': 0.25, 'A4': 0.20, 'A5': 0.15, 'A1': 0.10}

# §6 预注册方向 (+1 期望越大越好 / -1 反之)
FACTOR_SIGN = {
    'A1_V_EP': +1, 'A1_V_BP': +1, 'A1_V_SP': +1, 'A1_V_DP': +1,
    'A1_Q_ROE': +1, 'A1_Q_ROIC': +1, 'A1_Q_GM': +1, 'A1_Q_OCF': +1,
    'A1_Q_LEV': -1, 'A1_G_NPY': +1, 'A1_G_OR': +1, 'A1_G_TR': +1,
    'A2_REV_1': +1, 'A2_REV_5': +1, 'A2_REV_10': +1, 'A2_REV_20': +1,
    'A2_ON_5': +1, 'A2_ID_5': +1, 'A2_MAX20': +1,
    'A3_RM_20': +1, 'A3_RM_60': +1, 'A3_RM_120': +1, 'A3_RM_SHARPE60': +1,
    'A4_TURN_L': -1, 'A4_TURN_CHG': +1, 'A4_VR5': +1, 'A4_VR20': +1,
    'A4_AMT20': +1, 'A4_MA20_DEV': +1, 'A4_MA60_DEV': +1,
    'A4_MA20_SLOPE': +1, 'A4_ATR_R': -1, 'A4_AMP20': -1,
    'A5_RVOL_20': -1, 'A5_RVOL_60': -1, 'A5_RVOL_120': -1,
    'A5_IVOL_SHARE60': -1, 'A5_DRVOL_60': -1,
}
GROUP_FACTORS = {
    'A1': ['A1_V_EP', 'A1_V_BP', 'A1_V_SP', 'A1_V_DP',
           'A1_Q_ROE', 'A1_Q_ROIC', 'A1_Q_GM', 'A1_Q_OCF', 'A1_Q_LEV',
           'A1_G_NPY', 'A1_G_OR', 'A1_G_TR'],
    'A2': ['A2_REV_1', 'A2_REV_5', 'A2_REV_10', 'A2_REV_20',
           'A2_ON_5', 'A2_ID_5', 'A2_MAX20'],
    'A3': ['A3_RM_20', 'A3_RM_60', 'A3_RM_120', 'A3_RM_SHARPE60'],
    'A4': ['A4_TURN_L', 'A4_TURN_CHG', 'A4_VR5', 'A4_VR20', 'A4_AMT20',
           'A4_MA20_DEV', 'A4_MA60_DEV', 'A4_MA20_SLOPE', 'A4_ATR_R', 'A4_AMP20'],
    'A5': ['A5_RVOL_20', 'A5_RVOL_60', 'A5_RVOL_120',
           'A5_IVOL_SHARE60', 'A5_DRVOL_60'],
}
ALL_FACTORS = [f for g in GROUPS for f in GROUP_FACTORS[g]]
NFACT = len(ALL_FACTORS)                                        # 38

# §4.2 时间切分 (双口径)
WINDOWS = {
    'U-ZZ2K': {'PRE': ('20230901', '20240131'), 'IS': ('20240201', '20250630'),
               'VALID': ('20250701', '20251231'), 'OOS': ('20260101', '20260924'),
               'POOL_START': '20230901'},
    'U-PROXY': {'PRE': ('20210104', '20211231'), 'IS': ('20220101', '20250630'),
                'VALID': ('20250701', '20251231'), 'OOS': ('20260101', '20260924'),
                'POOL_START': '20210104'},
}
POOLS = ('U-ZZ2K', 'U-PROXY')


# ------------------------------------------------------------------ logging
class Log(object):
    def __init__(self, name):
        self.name = name
        self.path = os.path.join(OUT, '%s.log' % name)
        self.t0 = time.time()
        self.lines = []
        open(self.path, 'w', encoding='utf-8').close()

    def __call__(self, *a):
        line = '[%7.1fs] %s' % (time.time() - self.t0,
                                ' '.join(str(x) for x in a))
        print(line, flush=True)
        self.lines.append(line)
        with open(self.path, 'a', encoding='utf-8') as f:
            f.write(line + '\n')


# ------------------------------------------------------------------ spec gate
def spec_check(lg=None, strict=True):
    """SPEC 内容哈希校验. 不一致 -> False (调用方应 EXIT 2)."""
    if not os.path.exists(SPEC_PATH):
        if lg:
            lg('SPEC_MISSING %s' % SPEC_PATH)
        return False
    h = hashlib.sha256(open(SPEC_PATH, 'rb').read()).hexdigest().upper()
    ok = (h == SPEC_SHA256)
    if lg:
        lg('SPEC_SHA256 %s (%s)' % (h, 'MATCH' if ok else 'MISMATCH'))
        if not ok:
            lg('SPEC_HASH_MISMATCH expected=%s' % SPEC_SHA256)
    if strict and not ok:
        return False
    return ok


def save_csv(df, name, lg=None):
    fp = os.path.join(OUT, name)
    df.to_csv(fp, index=False, encoding='utf-8-sig')
    if lg:
        lg('SAVE %s  rows=%d' % (name, len(df)))
    return fp


def save_json(obj, name, lg=None):
    fp = os.path.join(OUT, name)
    with open(fp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, default=str)
    if lg:
        lg('SAVE %s' % name)
    return fp


# ------------------------------------------------------------------ grid
_GRID = {}


def load_grid(lg=None):
    if 'g' not in _GRID:
        _GRID['g'] = hve_common.build_grid(lg=lg, use_cache=True)
    return _GRID['g']


def _scatter(g, df, col, dtype='float32', mask_col=None):
    S, N = g['close'].shape
    M = np.full((S, N), np.nan, dtype=dtype)
    si = df['ts_code'].map(g['s2i']).to_numpy()
    di = df['trade_date'].map(g['d2i']).to_numpy()
    ok = pd.notna(si) & pd.notna(di)
    M[si[ok].astype('int64'), di[ok].astype('int64')] = \
        df[col].to_numpy(dtype='float64')[ok]
    return M


def _panel(npz_or_df, g, cols, lg=None, tag=''):
    """从一个 parquet/DataFrame 抽取 cols -> {col: (S,N)}"""
    if isinstance(npz_or_df, pd.DataFrame):
        df = npz_or_df
    else:
        df = pd.read_parquet(npz_or_df, columns=['ts_code', 'trade_date'] + cols)
    df = df.copy()
    df['ts_code'] = df['ts_code'].astype(str)
    df['trade_date'] = df['trade_date'].astype(str)
    out = {}
    for c in cols:
        if c not in df.columns:
            out[c] = None
            continue
        out[c] = _scatter(g, df, c)
    if lg:
        lg('panel %s cols=%d rows=%d' % (tag, len(cols), len(df)))
    return out


def load_basic(g, lg=None):
    cols = ['turnover_rate', 'volume_ratio', 'pe_ttm', 'pb', 'ps_ttm',
            'dv_ttm', 'total_mv', 'circ_mv']
    return _panel(os.path.join(FS_DATA, 'basic_panel.parquet'), g, cols,
                  lg=lg, tag='basic')


# ------------------------------------------------------------------ benchmark
def load_bench(g, lg=None):
    """932000.CSI (DB) + 000300.SH (index_panel) 对齐到 g['dates'].
    returns dict: close_zz, close_mkt, ret_zz, ret_mkt, cum_zz, cum_mkt (N,)"""
    dates = g['dates']
    N = len(dates)
    idx = pd.Index(dates)

    # --- 932000.CSI from sqlite
    con = sqlite3.connect(DB)
    q = ("SELECT trade_date, close FROM index_daily_cache "
         "WHERE ts_code=? ORDER BY trade_date")
    d = pd.read_sql_query(q, con, params=(BENCH_ZZ,))
    con.close()
    d['trade_date'] = d['trade_date'].astype(str)
    s_zz = d.set_index('trade_date')['close'].astype(float).reindex(idx)

    # --- 000300.SH from FS index_panel (已核实仅含 000300.SH)
    p = pd.read_parquet(os.path.join(FS_DATA, 'index_panel.parquet'),
                        columns=['ts_code', 'trade_date', 'close'])
    p['trade_date'] = p['trade_date'].astype(str)
    p = p[p['ts_code'] == BENCH_MKT].drop_duplicates('trade_date')
    s_mkt = p.set_index('trade_date')['close'].astype(float).reindex(idx)

    close_zz = s_zz.to_numpy(dtype='float64')
    close_mkt = s_mkt.to_numpy(dtype='float64')
    ret_zz = np.full(N, np.nan)
    ret_mkt = np.full(N, np.nan)
    ret_zz[1:] = close_zz[1:] / close_zz[:-1] - 1.0
    ret_mkt[1:] = close_mkt[1:] / close_mkt[:-1] - 1.0
    fill = int(np.isfinite(close_zz).sum())
    if lg:
        lg('bench %s n=%d %s..%s | %s n=%d'
           % (BENCH_ZZ, fill,
              dates[np.argmax(np.isfinite(close_zz))],
              dates[N - 1 - int(np.argmax(np.isfinite(close_zz[::-1])))],
              BENCH_MKT, int(np.isfinite(close_mkt).sum())))
    return {'close_zz': close_zz, 'close_mkt': close_mkt,
            'ret_zz': ret_zz, 'ret_mkt': ret_mkt}


# ------------------------------------------------------------------ membership
def membership(fp, g, lg=None, tag=''):
    """月频成分快照 -> (S,N) bool. 前向填充, 生效日 = 快照日之后的第一个交易日."""
    if not os.path.exists(fp):
        if lg:
            lg('MEMBER_DATA_MISSING %s' % fp)
        return None
    m = pd.read_parquet(fp)
    m['con_code'] = m['con_code'].astype(str)
    m['trade_date'] = m['trade_date'].astype(str)
    S, N = g['close'].shape
    out = np.zeros((S, N), dtype=bool)
    snaps = sorted(m['trade_date'].unique())
    dates = g['dates']
    for i, sd in enumerate(snaps):
        i0 = int(np.searchsorted(dates, sd, side='right'))
        i1 = (int(np.searchsorted(dates, snaps[i + 1], side='right'))
              if i + 1 < len(snaps) else N)
        if i0 >= N or i1 <= i0:
            continue
        cs = m.loc[m['trade_date'] == sd, 'con_code'].unique()
        si = np.array([g['s2i'][c] for c in cs if c in g['s2i']], dtype='int64')
        if len(si):
            out[si[:, None], np.arange(i0, i1)[None, :]] = True
    if lg:
        lg('membership %s snaps=%d avg_names=%.0f cells=%d'
           % (tag, len(snaps), out.sum() / max(1, len(snaps)),
              int(out.sum())))
    return out


# ------------------------------------------------------------------ eligibility
def eligibility(g, lg=None):
    """(S,N) bool: traded & ~st & ~delist & age>=250 & 非长停牌 & 非BSE."""
    ok = g['traded'].copy()
    ok &= ~g['st']
    ok &= ~g['delist']
    first = g['first_idx']
    idx = np.arange(g['close'].shape[1])[None, :]
    ok &= (idx - first[:, None]) >= MIN_LIST_DAYS
    ok &= (g['board'] != 'BSE')[:, None]
    miss = (~g['traded']).astype('int32')
    run = np.zeros_like(miss)
    c = np.zeros(miss.shape[0], dtype='int32')
    for j in range(miss.shape[1]):
        c = np.where(miss[:, j] > 0, c + 1, 0)
        run[:, j] = c
    bad = np.zeros_like(ok)
    for k in range(1, LONG_SUSP_DAYS):
        bad[:, k:] |= (run[:, :-k] >= k)
    return ok & ~bad


# ------------------------------------------------------------------ universe
def build_universe(g, basic, lg=None):
    """返回 {pool: (S,N) bool} 与 {pool: (S,N) bool} 成分矩阵(诊断用)."""
    elig = eligibility(g, lg=lg)
    dates = g['dates']
    N = len(dates)
    mem_zz = membership(FP_ZZ2K_MEM, g, lg=lg, tag='U-ZZ2K')
    mem_b = membership(FP_BENCH_MEM, g, lg=lg, tag='U-PROXY')
    uni = {}
    if mem_zz is not None:
        u = elig & mem_zz
        u[:, dates < WINDOWS['U-ZZ2K']['POOL_START']] = False
        uni['U-ZZ2K'] = u
    else:
        uni['U-ZZ2K'] = None
    if mem_b is not None:
        core = (mem_b & elig)
        circ = basic['circ_mv']
        # circ_mv 升序排名 (池内), 取 1801..3800 名
        rk = np.full(circ.shape, np.nan, dtype='float32')
        for t in range(N):
            m = elig[:, t] & np.isfinite(circ[:, t])
            if m.sum() < 3800:
                continue
            v = circ[m, t]
            o = np.argsort(v)
            r = np.empty(len(v), dtype='float32')
            r[o] = np.arange(1, len(v) + 1)
            rk[m, t] = r
        proxy = (~core) & elig & (rk >= 1801) & (rk <= 3800)
        proxy[:, dates < WINDOWS['U-PROXY']['POOL_START']] = False
        uni['U-PROXY'] = proxy
    else:
        uni['U-PROXY'] = None
    if lg:
        for p in POOLS:
            u = uni[p]
            if u is None:
                lg('universe %s = NONE' % p)
                continue
            n = u.sum(axis=0)
            act = n[n > 0]
            ov = ((uni['U-ZZ2K'] & uni['U-PROXY']).sum(axis=0)
                  if (uni['U-ZZ2K'] is not None and uni['U-PROXY'] is not None)
                  else np.zeros(N))
            lg('universe %s days=%d avg_n=%.0f min=%d max=%d | overlap_avg=%.0f'
               % (p, len(act), act.mean() if len(act) else 0,
                  int(act.min()) if len(act) else 0,
                  int(act.max()) if len(act) else 0, ov.mean()))
    return uni, {'mem_zz': mem_zz, 'mem_bench': mem_b, 'elig': elig}


# ------------------------------------------------------------------ returns
def ret_panel(g):
    c = g['close'].astype('float64')
    r = np.full(c.shape, np.nan)
    r[:, 1:] = c[:, 1:] / c[:, :-1] - 1.0
    return r


# ------------------------------------------------------------------ labels
def industry_l1(g, lg=None):
    """(S,N) int32 申万一级名称编码 (PIT, 0=缺失) 与 名称->编码 map."""
    dates = g['dates']
    ind = load_industry_map()
    imf = industry_map_fast(ind)
    uniq = {}
    l1s = np.zeros(g['close'].shape, dtype='int32')
    for s, tc in enumerate(g['codes']):
        lst = imf.get(tc)
        if not lst:
            continue
        for a, b, name, _l2 in lst:
            i0 = int(np.searchsorted(dates, a, side='left'))
            i1 = int(np.searchsorted(dates, b, side='right'))
            if i1 <= i0:
                continue
            if name not in uniq:
                uniq[name] = len(uniq) + 1
            l1s[s, i0:i1] = uniq[name]
    if lg:
        lg('industry L1 levels=%d coverage=%.3f'
           % (len(uniq), float((l1s > 0).mean())))
    return l1s, uniq


def build_labels(g, bench, uni, lg=None):
    """{pool: {'y_h': {h:(S,N)}, 'y_ex_*': {h:(S,N)}}}"""
    dates = g['dates']
    N = len(dates)
    c = g['close'].astype('float64')
    l1s, uniq = industry_l1(g, lg=lg)
    out = {}
    for p in POOLS:
        u = uni[p]
        if u is None:
            continue
        yh = {}
        for h in HORIZONS:
            fwd = np.full(c.shape, np.nan)
            fwd[:, :N - h] = c[:, h:] / c[:, :N - h] - 1.0
            fwd[~u] = np.nan
            yh[h] = fwd
        # 基准超额
        yzz, ymt = {}, {}
        for h in HORIZONS:
            bench_h = np.full(N, np.nan)
            bench_h[:N - h] = (bench['close_zz'][h:] / bench['close_zz'][:N - h]
                               - 1.0)
            mkt_h = np.full(N, np.nan)
            mkt_h[:N - h] = (bench['close_mkt'][h:] / bench['close_mkt'][:N - h]
                             - 1.0)
            yzz[h] = yh[h] - bench_h[None, :]
            ymt[h] = yh[h] - mkt_h[None, :]
        ysec = {}
        for h in HORIZONS:
            ysec[h] = _industry_excess(yh[h], u, l1s)
        out[p] = {'y_h': yh, 'y_ex_zz': yzz, 'y_ex_mkt': ymt,
                  'y_ex_sec': ysec, 'l1': l1s, 'l1_map': uniq}
        if lg:
            for h in HORIZONS:
                lg('labels %s h=%d valid=%d' % (p, h,
                                                int(np.isfinite(yzz[h]).sum())))
    return out


def _industry_excess(y, u, l1s):
    """同期同行业(申万一级)等权平均 y 的超额 (剔除自身)."""
    S, N = y.shape
    out = np.full((S, N), np.nan)
    for t in range(N):
        m = u[:, t] & np.isfinite(y[:, t])
        if m.sum() < 10:
            continue
        gid = l1s[:, t]
        v = y[:, t]
        df = pd.DataFrame({'g': gid[m], 'v': v[m]})
        agg = df.groupby('g')['v'].agg(['sum', 'count'])
        s = agg['sum']; n = agg['count']
        self_g = gid[m]
        sm = s.reindex(self_g).to_numpy()
        cn = n.reindex(self_g).to_numpy()
        ex = (sm - v[m]) / np.maximum(cn - 1, 1)
        ex[cn < 3] = np.nan
        out[m, t] = v[m] - ex
    return out


# ------------------------------------------------------------------ xs ops
def xs_rank_pct(M, u):
    """逐日横截面 rank/(n+1) in (0,1); u 外/缺失 -> NaN."""
    S, N = M.shape
    out = np.full((S, N), np.nan, dtype='float32')
    for t in range(N):
        m = u[:, t] & np.isfinite(M[:, t])
        n = int(m.sum())
        if n < 2:
            continue
        v = M[m, t]
        r = pd.Series(v).rank().to_numpy()
        out[m, t] = (r / (n + 1.0)).astype('float32')
    return out


def xs_z(M, u):
    S, N = M.shape
    out = np.full((S, N), np.nan, dtype='float32')
    for t in range(N):
        m = u[:, t] & np.isfinite(M[:, t])
        if m.sum() < 3:
            continue
        v = M[m, t].astype('float64')
        sd = v.std()
        if sd <= 0:
            continue
        out[m, t] = ((v - v.mean()) / sd).astype('float32')
    return out


def xs_z_rank(M, u):
    """rank 百分位 -> 横截面 z (用于正交化输入)."""
    r = xs_rank_pct(M, u)
    return xs_z(r, u)


def roll_mean(M, w, min_frac=0.6):
    d = pd.DataFrame(M.T)
    return d.rolling(w, min_periods=max(2, int(w * min_frac))).mean() \
            .to_numpy(dtype='float32').T


def roll_sum(M, w, min_frac=0.6):
    d = pd.DataFrame(M.T)
    return d.rolling(w, min_periods=max(2, int(w * min_frac))).sum() \
            .to_numpy(dtype='float32').T


def roll_std(M, w, min_frac=0.6):
    d = pd.DataFrame(M.T)
    return d.rolling(w, min_periods=max(2, int(w * min_frac))).std() \
            .to_numpy(dtype='float32').T


def roll_max(M, w):
    d = pd.DataFrame(M.T)
    return d.rolling(w, min_periods=w).max().to_numpy(dtype='float32').T


# ------------------------------------------------------------------ rolling OLS
def roll_ols(y, X, W, min_obs=120, chunk=160, lg=None, tag=''):
    """逐股滚动窗口 OLS.

    y : (S,N) float, 缺失为 NaN
    X : (N,K) float, 公共回归元 (含截距列)
    返回 resid (S,N), r2 (S,N):
        日期 t 的系数用窗口 (t-W, t] (含 t) 估计, >=min_obs 个有效观测.
    """
    S, N = y.shape
    K = X.shape[1]
    resid = np.full((S, N), np.nan, dtype='float32')
    r2 = np.full((S, N), np.nan, dtype='float32')
    Xf = np.nan_to_num(X.astype('float64'), nan=0.0)
    t0 = time.time()
    for c0 in range(0, S, chunk):
        c1 = min(S, c0 + chunk)
        nc = c1 - c0
        yc = y[c0:c1].astype('float64')
        m = np.isfinite(yc)
        y0 = np.where(m, yc, 0.0)

        def cs(a):
            return np.concatenate([np.zeros((a.shape[0], 1)),
                                   np.cumsum(a, axis=1)], axis=1)

        cy = cs(y0); cy2 = cs(y0 * y0); cmk = cs(m.astype('float64'))
        cxy = [cs(Xf[:, k][None, :] * y0) for k in range(K)]
        cxx = {}
        for k in range(K):
            for l in range(k, K):
                cxx[(k, l)] = cs((Xf[:, k] * Xf[:, l])[None, :] * m)

        eye = np.eye(K)[None, :, :] * 1e-8
        for t in range(W, N):
            lo = t - W + 1
            n = cmk[:, t + 1] - cmk[:, lo]
            good = np.where(n >= min_obs)[0]
            if len(good) == 0:
                continue
            A = np.empty((nc, K, K))
            for k in range(K):
                for l in range(k, K):
                    v = cxx[(k, l)][:, t + 1] - cxx[(k, l)][:, lo]
                    A[:, k, l] = v
                    A[:, l, k] = v
            b = np.empty((nc, K))
            for k in range(K):
                b[:, k] = cxy[k][:, t + 1] - cxy[k][:, lo]
            yy = cy2[:, t + 1] - cy2[:, lo]
            ysum = cy[:, t + 1] - cy[:, lo]
            Ab = A[good] + eye
            bb = b[good]
            try:
                beta = np.linalg.solve(Ab, bb[..., None])[..., 0]
            except np.linalg.LinAlgError:
                continue
            ssr = yy[good] - np.einsum('ij,ij->i', beta, bb)
            tss = yy[good] - ysum[good] ** 2 / np.maximum(n[good], 1)
            with np.errstate(invalid='ignore', divide='ignore'):
                r2v = 1.0 - ssr / tss
            r2[c0 + good, t] = np.clip(r2v, -1.0, 1.0).astype('float32')
            yt = yc[good, t]
            pred = beta @ Xf[t]
            with np.errstate(invalid='ignore'):
                resid[c0 + good, t] = (yt - pred).astype('float32')
        if lg:
            lg('roll_ols %s chunk %d/%d (%.1fs)' % (tag, c1, S,
                                                    time.time() - t0))
    # 未交易日置 NaN
    resid[~np.isfinite(y)] = np.nan
    return resid, r2


# ================================================================== helpers
def _df(M):
    return pd.DataFrame(np.asarray(M, dtype='float64').T)


def _arr(d):
    return d.to_numpy(dtype='float32').T


def roll_beta(y, x, W, min_obs=120):
    """(S,N) 单变量滚动斜率 beta(y ~ 1 + x). y/(S,N), x/(N,)."""
    S, N = y.shape
    out = np.full((S, N), np.nan, dtype='float32')
    xf = np.nan_to_num(np.asarray(x, dtype='float64'), nan=0.0)
    for c0 in range(0, S, 400):
        c1 = min(S, c0 + 400)
        yc = y[c0:c1].astype('float64')
        m = np.isfinite(yc)
        y0 = np.where(m, yc, 0.0)
        mf = m.astype('float64')

        def cs(a):
            return np.concatenate([np.zeros((a.shape[0], 1)),
                                   np.cumsum(a, axis=1)], axis=1)

        cy = cs(y0); cm = cs(mf)
        cx = cs(mf * xf[None, :]); cxx = cs(mf * (xf ** 2)[None, :])
        cxy = cs(y0 * xf[None, :])
        for t in range(W, N):
            lo = t - W + 1
            n = cm[:, t + 1] - cm[:, lo]
            sx = cx[:, t + 1] - cx[:, lo]
            sxx = cxx[:, t + 1] - cxx[:, lo]
            sy = cy[:, t + 1] - cy[:, lo]
            sxy = cxy[:, t + 1] - cxy[:, lo]
            with np.errstate(invalid='ignore', divide='ignore'):
                den = n * sxx - sx ** 2
                b = (n * sxy - sx * sy) / den
            b = np.where(n >= min_obs, b, np.nan)
            out[c0:c1, t] = b.astype('float32')
    out[~np.isfinite(y)] = np.nan
    return out


def pit_fill(g, df, col, lg=None):
    """事件级 (ts_code, ann_date, value) -> (S,N) PIT 前向填充."""
    dates = g['dates']
    S, N = g['close'].shape
    M = np.full((S, N), np.nan, dtype='float32')
    d = df[['ts_code', 'ann_date', col]].copy()
    d['ts_code'] = d['ts_code'].astype(str)
    d['ann_date'] = d['ann_date'].astype(str)
    d = d[d['ts_code'].isin(g['s2i']) & d[col].notna()]
    if not len(d):
        return M
    s = d['ts_code'].map(g['s2i']).to_numpy(dtype='int64')
    i0 = np.searchsorted(dates, d['ann_date'].to_numpy(), side='right')
    keep = i0 < N
    s, i0, v = s[keep], i0[keep], d[col].to_numpy(dtype='float64')[keep]
    order = np.argsort(d['ann_date'].to_numpy()[keep], kind='stable')
    M[s[order], i0[order]] = v[order]
    M = _arr(_df(M).ffill().fillna(np.nan))
    return M


# ================================================================== A1
def build_A1(g, basic, lg=None):
    pe, pb, ps, dv = basic['pe_ttm'], basic['pb'], basic['ps_ttm'], basic['dv_ttm']
    with np.errstate(invalid='ignore', divide='ignore'):
        EP = np.where(pe > 0, 1.0 / pe, np.nan)
        BP = np.where(pb > 0, 1.0 / pb, np.nan)
        SP = np.where(ps > 0, 1.0 / ps, np.nan)
    DP = dv.astype('float32')
    p = os.path.join(FS_DATA, 'panel.parquet')
    cols = ['ts_code', 'ann_date', 'report_type', 'roe', 'roic',
            'grossprofit_margin', 'ocf_to_rev', 'debt_to_assets',
            'netprofit_yoy', 'or_yoy', 'tr_yoy']
    d = pd.read_parquet(p, columns=cols)
    n0 = len(d)
    d = d[d['report_type'].astype(str) == '1']       # §3.2 合并报表 (str 口径)
    if lg:
        lg('A1 PIT rows %d -> report_type==1 %d' % (n0, len(d)))
    F = {
        'A1_V_EP': EP, 'A1_V_BP': BP, 'A1_V_SP': SP, 'A1_V_DP': DP,
        'A1_Q_ROE': pit_fill(g, d, 'roe', lg),
        'A1_Q_ROIC': pit_fill(g, d, 'roic', lg),
        'A1_Q_GM': pit_fill(g, d, 'grossprofit_margin', lg),
        'A1_Q_OCF': pit_fill(g, d, 'ocf_to_rev', lg),
        'A1_G_NPY': pit_fill(g, d, 'netprofit_yoy', lg),
        'A1_G_OR': pit_fill(g, d, 'or_yoy', lg),
        'A1_G_TR': pit_fill(g, d, 'tr_yoy', lg),
    }
    F['A1_Q_LEV'] = -pit_fill(g, d, 'debt_to_assets', lg)
    if lg:
        lg('A1 built: %s (A1_Q_OCF <- panel.ocf_to_rev 经营现金流/营收)'
           % ','.join(sorted(F)))
    return F


# ================================================================== A2
def build_A2(g, lg=None):
    c = g['close'].astype('float64')
    o = g['open'].astype('float64')
    cd, od = _df(c), _df(o)
    ret1 = cd / cd.shift(1) - 1.0
    F = {}
    for k in (1, 5, 10, 20):
        F['A2_REV_%d' % k] = _arr(-(cd / cd.shift(k) - 1.0))
    on1 = od / cd.shift(1) - 1.0
    id1 = cd / od - 1.0
    F['A2_ON_5'] = _arr(-np.expm1(np.log1p(on1).rolling(5, min_periods=5).sum()))
    F['A2_ID_5'] = _arr(-np.expm1(np.log1p(id1).rolling(5, min_periods=5).sum()))
    F['A2_MAX20'] = _arr(-ret1.rolling(20, min_periods=20).max())
    if lg:
        lg('A2 built: %s' % ','.join(sorted(F)))
    return F


# ================================================================== A4
def build_A4(g, basic, lg=None):
    c = g['close'].astype('float64')
    h = g['high'].astype('float64')
    lo = g['low'].astype('float64')
    v = g['vol'].astype('float64')
    amt = g['amount'].astype('float64')
    tv = basic['turnover_rate'].astype('float64')
    cd, hd, ld, vd, ad, td = _df(c), _df(h), _df(lo), _df(v), _df(amt), _df(tv)
    m5 = td.rolling(5, min_periods=4).mean()
    m20 = td.rolling(20, min_periods=16).mean()
    ma20 = cd.rolling(20, min_periods=20).mean()
    ma60 = cd.rolling(60, min_periods=60).mean()
    pc = cd.shift(1)
    tr = pd.DataFrame(np.maximum(np.maximum((hd - ld).to_numpy(),
                                            (hd - pc).abs().to_numpy()),
                                 (ld - pc).abs().to_numpy()),
                      index=cd.index, columns=cd.columns)
    F = {
        'A4_TURN_L': _arr(m5),
        'A4_TURN_CHG': _arr(m5 / m20 - 1.0),
        'A4_VR5': _arr(vd.rolling(5, min_periods=4).mean()
                       / vd.rolling(20, min_periods=16).mean()),
        'A4_VR20': _arr(vd.rolling(20, min_periods=16).mean()
                        / vd.rolling(60, min_periods=48).mean()),
        'A4_AMT20': _arr(ad.rolling(20, min_periods=16).mean()
                         / ad.rolling(60, min_periods=48).mean()),
        'A4_MA20_DEV': _arr(cd / ma20 - 1.0),
        'A4_MA60_DEV': _arr(cd / ma60 - 1.0),
        'A4_MA20_SLOPE': _arr(ma20 / ma20.shift(20) - 1.0),
        'A4_ATR_R': _arr(tr.rolling(14, min_periods=14).mean() / cd),
        'A4_AMP20': _arr(((hd - ld) / pc).rolling(20, min_periods=20).mean()),
    }
    if lg:
        lg('A4 built: %s' % ','.join(sorted(F)))
    return F


# ================================================================== style idx
def ind_equal_ret(g, ret, u, l1s):
    """池内申万一级等权收益 (N, nlev) 与 每股映射 (S,N)."""
    N = ret.shape[1]
    nlev = int(l1s.max()) + 1
    agg = np.full((N, nlev), np.nan, dtype='float64')
    for t in range(N):
        m = u[:, t] & np.isfinite(ret[:, t]) & (l1s[:, t] > 0)
        if m.sum() < 5:
            continue
        gid = l1s[m, t]
        v = ret[m, t]
        s = np.bincount(gid, weights=v, minlength=nlev)
        c = np.bincount(gid, minlength=nlev)
        with np.errstate(invalid='ignore', divide='ignore'):
            agg[t] = np.where(c >= 3, s / np.maximum(c, 1), np.nan)
    r_ind = np.full(ret.shape, np.nan, dtype='float32')
    for t in range(N):
        idx = l1s[:, t]
        ok = idx > 0
        r_ind[ok, t] = agg[t, idx[ok]]
    return r_ind, agg


def style_factors(g, basic, ret, u, lg=None):
    """池内 circ_mv / bp 三分位自建 r_smb / r_hml (§6.5)."""
    N = ret.shape[1]
    circ = basic['circ_mv'].astype('float64')
    pb = basic['pb'].astype('float64')
    with np.errstate(invalid='ignore', divide='ignore'):
        bp = np.where(pb > 0, 1.0 / pb, np.nan)
    smb = np.full(N, np.nan); hml = np.full(N, np.nan)
    for t in range(N):
        m = u[:, t] & np.isfinite(ret[:, t])
        if m.sum() < 90:
            continue
        v = ret[m, t]
        rk = pd.Series(circ[m, t]).rank().to_numpy()
        n = len(rk)
        q = np.ceil(n / 3.0)
        s_lo, s_hi = rk <= q, rk > n - q
        if s_lo.sum() < 30 or s_hi.sum() < 30:
            continue
        smb[t] = v[s_lo].mean() - v[s_hi].mean()
        b = bp[m, t]
        okb = np.isfinite(b)
        if okb.sum() < 90:
            continue
        rkb = pd.Series(b[okb]).rank().to_numpy()
        nb = len(rkb)
        qb = np.ceil(nb / 3.0)
        hi, low = rkb > nb - qb, rkb <= qb
        if hi.sum() < 30 or low.sum() < 30:
            continue
        hml[t] = v[okb][hi].mean() - v[okb][low].mean()
    if lg:
        lg('style r_smb finite=%d r_hml finite=%d'
           % (int(np.isfinite(smb).sum()), int(np.isfinite(hml).sum())))
    return smb, hml


# ================================================================== A3 / A5
def roll_ols_1x(y, x, W, min_obs=120, chunk=400):
    """逐股滚动 OLS(y ~ 1 + x), x 为逐股回归元 (S,N). 返回 resid, r2."""
    S, N = y.shape
    resid = np.full((S, N), np.nan, dtype='float32')
    r2 = np.full((S, N), np.nan, dtype='float32')
    xf = np.asarray(x, dtype='float64')
    for c0 in range(0, S, chunk):
        c1 = min(S, c0 + chunk)
        yc = y[c0:c1].astype('float64')
        xc = xf[c0:c1]
        m = np.isfinite(yc) & np.isfinite(xc)
        y0 = np.where(m, yc, 0.0)
        x0 = np.where(m, xc, 0.0)
        mf = m.astype('float64')

        def cs(a):
            return np.concatenate([np.zeros((a.shape[0], 1)),
                                   np.cumsum(a, axis=1)], axis=1)

        cn = cs(mf); cy = cs(y0); cx = cs(x0); cyy = cs(y0 * y0)
        cxx = cs(x0 * x0); cxy = cs(x0 * y0)
        for t in range(W, N):
            lo = t - W + 1
            n = cn[:, t + 1] - cn[:, lo]
            sy = cy[:, t + 1] - cy[:, lo]
            sx = cx[:, t + 1] - cx[:, lo]
            syy = cyy[:, t + 1] - cyy[:, lo]
            sxx = cxx[:, t + 1] - cxx[:, lo]
            sxy = cxy[:, t + 1] - cxy[:, lo]
            with np.errstate(invalid='ignore', divide='ignore'):
                den = n * sxx - sx ** 2
                b = (n * sxy - sx * sy) / den
                a = (sy - b * sx) / n
                ssr = (syy - 2 * a * sy - 2 * b * sxy + n * a * a
                       + 2 * a * b * sx + b * b * sxx)
                tss = syy - sy * sy / n
                r2v = 1.0 - ssr / tss
            ok = n >= min_obs
            b = np.where(ok, b, np.nan); a = np.where(ok, a, np.nan)
            with np.errstate(invalid='ignore'):
                pred = a + b * xc[:, t]
                resid[c0:c1, t] = np.where(m[:, t] & ok, yc[:, t] - pred, np.nan)
            r2[c0:c1, t] = np.where(ok, np.clip(r2v, -1.0, 1.0), np.nan)
    resid[~np.isfinite(y)] = np.nan
    return resid, r2


def resid_momentum(resid):
    F = {}
    for k in (20, 60, 120):
        s = roll_sum(resid, k, min_frac=0.9)
        sd = roll_std(resid, k, min_frac=0.9)
        with np.errstate(invalid='ignore', divide='ignore'):
            F['A3_RM_%d' % k] = (s / sd).astype('float32')
    m = roll_mean(resid, 60, min_frac=0.9)
    sd = roll_std(resid, 60, min_frac=0.9)
    with np.errstate(invalid='ignore', divide='ignore'):
        F['A3_RM_SHARPE60'] = (m / sd * np.sqrt(243.0)).astype('float32')
    return F


def resid_vol(resid60, r2_60, resid120):
    neg = np.where(resid60 < 0, resid60, np.nan).astype('float32')
    return {
        'A5_RVOL_20': roll_std(resid60, 20, min_frac=0.7),
        'A5_RVOL_60': roll_std(resid60, 60, min_frac=0.7),
        'A5_RVOL_120': roll_std(resid120, 120, min_frac=0.7),
        'A5_IVOL_SHARE60': (1.0 - r2_60).astype('float32'),
        'A5_DRVOL_60': roll_std(neg, 60, min_frac=0.15),
    }


def build_A3_A5(g, bench, basic, ret, u, l1s, lg=None, W_RM=250,
                W_VOL=60, W_VOL2=120):
    """A3 残差动量 (对行业指数) + A5 残差波动 (对三因子)."""
    mkt = bench['ret_mkt']
    r_ex = (ret - mkt[None, :]).astype('float32')
    r_ind_p, _ = ind_equal_ret(g, ret, u, l1s)
    resid3, _ = roll_ols_1x(r_ex, r_ind_p, W_RM, min_obs=120)
    if lg:
        lg('A3 resid3 finite=%.4f' % np.isfinite(resid3).mean())
    A3 = resid_momentum(resid3)
    smb, hml = style_factors(g, basic, ret, u, lg=lg)
    okx = np.isfinite(smb) & np.isfinite(hml) & np.isfinite(mkt)
    r_ex2 = np.where(okx[None, :], r_ex, np.nan).astype('float32')
    N = len(mkt)
    X1 = np.column_stack([np.ones(N), mkt, smb, hml])
    resid60, r2_60 = roll_ols(r_ex2, X1, W_VOL, min_obs=int(W_VOL * 0.6),
                              lg=lg, tag='A5w%d' % W_VOL)
    resid120, _ = roll_ols(r_ex2, X1, W_VOL2, min_obs=int(W_VOL2 * 0.6),
                           lg=lg, tag='A5w%d' % W_VOL2)
    A5 = resid_vol(resid60, r2_60, resid120)
    if lg:
        lg('A5 resid60 finite=%.4f r2_60 mean=%.4f'
           % (np.isfinite(resid60).mean(), np.nanmean(r2_60)))
    return A3, A5, resid3, resid60


# ================================================================== assemble
def build_group(k, g, basic, bench, ret, u, l1s, lg=None, cache=True):
    """按组构建原始因子 (未标准化). 结果缓存到 data/A{k}_raw.npz."""
    fp = os.path.join(DATA, 'A%s_raw.npz' % k)
    if cache and os.path.exists(fp):
        z = np.load(fp, allow_pickle=True)
        F = {n: z[n] for n in z.files}
        if lg:
            lg('group %s loaded from cache (%d factors)' % (k, len(F)))
        return F
    t0 = time.time()
    if k == 'A1':
        F = build_A1(g, basic, lg)
    elif k == 'A2':
        F = build_A2(g, lg)
    elif k == 'A4':
        F = build_A4(g, basic, lg)
    else:
        raise ValueError('A3/A5 需经 build_A3_A5')
    np.savez(fp, **F)
    if lg:
        lg('group %s built %d factors (%.1fs)' % (k, len(F), time.time() - t0))
    return F


def standardize(F, u, lg=None):
    """§7.1 逐日横截面 rank/(n+1)."""
    return {n: xs_rank_pct(M, u) for n, M in F.items()}


# ================================================================== coverage
def factor_coverage(F, u, dates=None):
    """§16.4 每因子池内覆盖: name -> {cov,cells,days,us_start,us_start_date}."""
    tot = int(u.sum())
    out = {}
    for n, M in F.items():
        ok = np.isfinite(M) & u
        cnt = ok.sum(axis=0)
        days = int((cnt > 0).sum())
        j = int(np.argmax(cnt > 0)) if days else -1
        out[n] = {
            'cov': (int(ok.sum()) / tot) if tot else 0.0,
            'cells': int(ok.sum()), 'days': days, 'us_start': j,
            'us_start_date': (str(dates[j]) if (dates is not None and j >= 0)
                              else ''),
        }
    return out


def orth_input(F, cov, min_cov=ORTH_COV_MIN, lg=None, tag=''):
    """数据可用性门禁: 池内覆盖 < min_cov 的因子不进入类内正交化输入.

    被剔除因子仍保留在全部 IC / 分层 / 覆盖表中并标记 NO_COVERAGE
    (SPEC §16.4: 含为 0 的因子必须保留在表中).
    """
    keep, drop = {}, []
    for n, M in F.items():
        if cov.get(n, {}).get('cov', 0.0) >= min_cov:
            keep[n] = M
        else:
            drop.append(n)
    if lg:
        lg('orth_input %s keep=%d drop=%d [%s]'
           % (tag, len(keep), len(drop), ','.join(sorted(drop)) or '-'))
    return keep, sorted(drop)


def coverage_flag(cov, min_cov=ORTH_COV_MIN):
    """覆盖标记: NO_COVERAGE(0) / LOW_COVERAGE(<门禁) / OK."""
    c = cov.get('cov', 0.0)
    if c <= 0.0:
        return 'NO_COVERAGE'
    if c < min_cov:
        return 'LOW_COVERAGE'
    return 'OK'


# ================================================================== orthogonal
def _orth_one_day(Z, thr=1e-6):
    """Z: (G, n) 标准化截面 -> Loewin 对称正交输出 (G, n); 返回截断数."""
    try:
        S = np.corrcoef(Z)
    except Exception:
        return None, 0
    if not np.all(np.isfinite(S)):
        return None, 0
    w, V = np.linalg.eigh(S)
    bad = int((w < thr).sum())
    w = np.clip(w, thr, None)
    Sinv = (V * (1.0 / np.sqrt(w))) @ V.T
    return Sinv @ Z, bad


def lowdin(F, u, lg=None, thr=1e-6, stats=None):
    """F: dict name->(S,N) (已标准化) -> dict name->(S,N) 类内对称正交.

    stats: 可选 dict, 回填 {'days','truncated_dirs','trunc_ratio'} (诊断用).
    """
    names = list(F)
    G = len(names)
    S, N = F[names[0]].shape
    out = {n: np.full((S, N), np.nan, dtype='float32') for n in names}
    trunc = 0
    days = 0
    for t in range(N):
        m = u[:, t]
        for n in names:
            m = m & np.isfinite(F[n][:, t])
        idx = np.where(m)[0]
        if len(idx) < max(30, G * 5):
            continue
        Z = np.vstack([F[n][idx, t] for n in names]).astype('float64')
        Z = Z - Z.mean(axis=1, keepdims=True)
        sd = Z.std(axis=1, keepdims=True)
        if (sd <= 0).any():
            continue
        Z = Z / sd
        Zo, bad = _orth_one_day(Z, thr)
        if Zo is None:
            continue
        trunc += bad
        days += 1
        for i, n in enumerate(names):
            out[n][idx, t] = Zo[i].astype('float32')
    if stats is not None:
        stats.update({'days': days, 'truncated_dirs': trunc,
                      'trunc_ratio': trunc / max(1, days * G)})
    if lg:
        lg('lowdin group=%d factors days=%d truncated_dirs=%d (%.4f)'
           % (G, days, trunc, trunc / max(1, days * G)))
    return out


def gs_orth(F, u, reverse=False, lg=None):
    """顺序 Gram-Schmidt 对照口径 (按 ID 正序 / 逆序)."""
    names = list(F)[::-1] if reverse else list(F)
    S, N = F[names[0]].shape
    out = {n: np.full((S, N), np.nan, dtype='float32') for n in names}
    for t in range(N):
        m = u[:, t]
        for n in names:
            m = m & np.isfinite(F[n][:, t])
        idx = np.where(m)[0]
        if len(idx) < 30:
            continue
        base = None
        for n in names:
            x = F[n][idx, t].astype('float64')
            x = (x - x.mean()) / (x.std() + 1e-12)
            if base is not None:
                for c in base:
                    x = x - c.dot(x) / max(c.dot(c), 1e-12) * c
            base = [x] if base is None else base + [x]
            out[n][idx, t] = x.astype('float32')
    if lg:
        lg('gs_orth (%s) done' % ('reverse' if reverse else 'forward'))
    return out


def group_composite(Forth, u, lg=None, tag=''):
    """§8.2 C_k = 类内等权平均."""
    names = list(Forth)
    S, N = Forth[names[0]].shape
    acc = np.zeros((S, N), dtype='float64')
    cnt = np.zeros((S, N), dtype='int16')
    for n in names:
        v = Forth[n]
        ok = np.isfinite(v)
        acc[ok] += v[ok]
        cnt[ok] += 1
    with np.errstate(invalid='ignore', divide='ignore'):
        C = np.where(cnt > 0, acc / np.maximum(cnt, 1), np.nan).astype('float32')
    if lg:
        lg('composite %s finite=%.4f' % (tag, np.isfinite(C).mean()))
    return C


# ================================================================== prereg
# §18.2 全部阈值集中于此, 禁止散落硬编码
PREREG = {
    'IC_MEAN_MIN': IC_MEAN_MIN, 'IC_T_MIN': IC_T_MIN, 'MONO_MIN': MONO_MIN,
    'NW_LAG': NW_LAG, 'IC_MIN_N': IC_MIN_N,
    'VALID_T_MIN': VALID_T_MIN, 'DEDUP_IC_MIN': DEDUP_IC_MIN,
    'IND_IC_MIN': IND_IC_MIN,
    'IND_T_MIN': IC_T_MIN, 'WF_IC_MIN': WF_IC_MIN,
    'PERT_STABLE': PERT_STABLE, 'PERT_FRAGILE': PERT_FRAGILE,
    'NULL_B': NULL_B, 'NULL_ROUNDS': NULL_ROUNDS, 'SEED': SEED,
    'COST_BP': COST_BP, 'Q_GROUPS': Q_GROUPS,
    'ORTH_COV_MIN': ORTH_COV_MIN,
}
# §3.5 只有 label 列允许消费 t > T
LABEL_COLUMNS = ('y_h', 'y_ex_zz', 'y_ex_mkt', 'y_ex_sec')
FACTOR_COLUMNS = tuple(ALL_FACTORS)


# ================================================================== stats
def nw_t(x, lag=NW_LAG):
    """Newey-West(Bartlett) t. 返回 (mean, t_NW, ICIR)."""
    x = np.asarray(x, dtype='float64')
    x = x[np.isfinite(x)]
    n = x.size
    if n < 5:
        return np.nan, np.nan, np.nan
    mu = float(x.mean())
    e = x - mu
    g = float((e * e).sum()) / n
    for i in range(1, min(lag, n - 1) + 1):
        w = 1.0 - i / (lag + 1.0)
        g += 2.0 * w * float((e[i:] * e[:-i]).sum()) / n
    var = g / n
    t = mu / np.sqrt(var / n) if var > 0 else np.nan
    sd = float(x.std())
    return mu, float(t), (mu / sd if sd > 0 else np.nan)


def daily_ic(Rf, Rl, min_n=IC_MIN_N, keep=None):
    """逐日 Spearman IC. Rf/Rl 均为按日截面 rank 面板 (池外/缺失=NaN)."""
    S, N = Rf.shape
    ic = np.full(N, np.nan, dtype='float64')
    cnt = np.zeros(N, dtype='int32')
    for t in range(N):
        m = np.isfinite(Rf[:, t]) & np.isfinite(Rl[:, t])
        if keep is not None:
            m = m & keep[:, t]
        n = int(m.sum())
        cnt[t] = n
        if n < min_n:
            continue
        av = Rf[m, t].astype('float64')
        bv = Rl[m, t].astype('float64')
        av = av - av.mean()
        bv = bv - bv.mean()
        sa = np.sqrt((av * av).mean())
        sb = np.sqrt((bv * bv).mean())
        if sa <= 0 or sb <= 0:
            continue
        ic[t] = float((av * bv).mean() / (sa * sb))
    return ic, cnt


def ic_row(ic, cnt, lag=NW_LAG, prefix=''):
    mu, t, icir = nw_t(ic, lag)
    x = ic[np.isfinite(ic)]
    pos = float((x > 0).mean()) if x.size else np.nan
    nmean = float(cnt[cnt > 0].mean()) if (cnt > 0).any() else np.nan
    return {prefix + 'IC_mean': mu,
            prefix + 'IC_std': float(x.std()) if x.size else np.nan,
            prefix + 'ICIR': icir, prefix + 't_NW': t,
            prefix + 'pos_ratio': pos, prefix + 'n_mean': nmean,
            prefix + 'n_days': int(x.size)}


def cluster_events(H, gap=5):
    """§7.5 逐股对 H(是否高分区) 事件聚类, 每簇只保留首次进入日."""
    S, N = H.shape
    keep = np.zeros((S, N), dtype=bool)
    for s in range(S):
        idx = np.where(H[s])[0]
        last = -10 ** 9
        for t in idx:
            if t - last > gap:
                keep[s, t] = True
                last = t
    return keep


def _ind_dummies(ind, min_count=3):
    """§8.3 行业哑变量; 当日只数 < min_count 的行业并入「其他」, 丢首列."""
    v = np.asarray(ind)
    if v.size == 0:
        return np.zeros((0, 0))
    vals, cnt = np.unique(v, return_counts=True)
    keep = vals[cnt >= min_count]
    v2 = np.where(np.isin(v, keep), v, -1).astype('int64')
    uq = np.unique(v2)                       # 升序; -1(其他) 排首列 -> 丢弃
    D = np.zeros((len(v2), len(uq)), dtype='float64')
    D[np.arange(len(v2)), np.searchsorted(uq, v2)] = 1.0
    return D[:, 1:]


# ================================================================== M0 base
def build_M0(g, basic, bench, ret, l1s, u, lg=None):
    """§8.3 基线 M0 = rank(ln circ_mv) + beta60(000300.SH) + 行业哑变量."""
    circ = basic['circ_mv'].astype('float64')
    with np.errstate(invalid='ignore', divide='ignore'):
        lnmv = np.where(circ > 0, np.log(circ), np.nan)
    mv = xs_rank_pct(lnmv, u)
    beta60 = roll_beta(ret, bench['ret_mkt'], 250, min_obs=120)
    if lg:
        lg('M0 mv finite=%.3f beta60 finite=%.3f'
           % (np.isfinite(mv).mean(), np.isfinite(beta60).mean()))
    return {'mv': mv, 'beta': beta60, 'l1': l1s}


def _m0_cols(M0, m, t):
    cols = [np.ones(int(m.sum())), M0['mv'][m, t], M0['beta'][m, t]]
    D = _ind_dummies(M0['l1'][m, t])
    for j in range(D.shape[1]):
        cols.append(D[:, j])
    return cols


def cross_orth(C, M0, u, lg=None, min_n=IC_MIN_N):
    """§8.2 C_k^⊥ = 逐日横截面 OLS 残差: C_k ~ 1 + C_{-k} + M0."""
    ks = list(C)
    S, N = C[ks[0]].shape
    out = {k: np.full((S, N), np.nan, dtype='float32') for k in ks}
    days = 0
    for t in range(N):
        m = u[:, t] & np.isfinite(M0['mv'][:, t]) & np.isfinite(M0['beta'][:, t])
        for k in ks:
            m = m & np.isfinite(C[k][:, t])
        n = int(m.sum())
        if n < min_n:
            continue
        base = np.column_stack(_m0_cols(M0, m, t))
        days += 1
        for k in ks:
            others = np.column_stack([C[o][m, t] for o in ks if o != k])
            A = np.column_stack([base, others])
            yv = C[k][m, t].astype('float64')
            beta = _ols(A, yv)
            if beta is None:
                continue
            out[k][m, t] = (yv - A @ beta).astype('float32')
    if lg:
        lg('cross_orth days=%d groups=%d' % (days, len(ks)))
    return out


def composite_from_rank(C, u, weights):
    """按预注册权重合成 (取自 rank(C_k)); 缺失权重重归一."""
    ks = list(C)
    S, N = C[ks[0]].shape
    R = {k: xs_rank_pct(C[k], u) for k in ks}
    acc = np.zeros((S, N), dtype='float64')
    cnt = np.zeros((S, N), dtype='float64')
    for k in ks:
        w = float(weights[k])
        v = R[k]
        ok = np.isfinite(v)
        acc[ok] += w * v[ok]
        cnt[ok] += w
    with np.errstate(invalid='ignore', divide='ignore'):
        out = np.where(cnt > 0, acc / np.maximum(cnt, 1e-12), np.nan)
    return out.astype('float32'), R


# ================================================================== xs reg
def _ols(A, y):
    """横截面 OLS 最小二乘解: 优先正规方程 (A'A)b = A'y, 奇异时回退 SVD.

    两法均为 OLS 的等价数值实现 (差异在机器精度量级); 正规方程快 ~2 个数量级,
    使 §11.4 单因子增量 (38×2 次回归) 与大 Kx 模型可跑完。
    """
    try:
        beta = np.linalg.solve(A.T @ A, A.T @ y)
        if np.all(np.isfinite(beta)):
            return beta
    except np.linalg.LinAlgError:
        pass
    try:
        beta, *_ = np.linalg.lstsq(A, y, rcond=None)
    except np.linalg.LinAlgError:
        return None
    return beta


def xs_reg(Yr, U, M0, extra, min_n=IC_MIN_N, lag=NW_LAG, tag=''):
    """逐日横截面 OLS: rank(label) ~ 1 + M0 + extra[0..K-1].

    返回 dict: r2(每日), betas(N,K), beta{j}/t{j}/pos{j}, r2_mean, n_days.
    """
    S, N = Yr.shape
    Kx = len(extra)
    B = np.full((N, Kx), np.nan, dtype='float64')
    R2 = np.full(N, np.nan, dtype='float64')
    for t in range(N):
        m = U[:, t] & np.isfinite(Yr[:, t])
        if M0 is not None:
            m = m & np.isfinite(M0['mv'][:, t]) & np.isfinite(M0['beta'][:, t])
        for X in extra:
            m = m & np.isfinite(X[:, t])
        n = int(m.sum())
        if n < min_n:
            continue
        cols = _m0_cols(M0, m, t) if M0 is not None else [np.ones(n)]
        cols += [X[m, t].astype('float64') for X in extra]
        A = np.column_stack(cols)
        yv = Yr[m, t].astype('float64')
        beta = _ols(A, yv)
        if beta is None:
            continue
        pred = A @ beta
        tss = float(((yv - yv.mean()) ** 2).sum())
        ssr = float(((yv - pred) ** 2).sum())
        R2[t] = 1.0 - ssr / tss if tss > 0 else np.nan
        if Kx:
            B[t] = beta[-Kx:]
    res = {'r2': R2, 'betas': B,
           'r2_mean': float(np.nanmean(R2)) if np.isfinite(R2).any() else np.nan,
           'n_days': int(np.isfinite(R2).sum())}
    for j in range(Kx):
        mu, tt, _ = nw_t(B[:, j], lag)
        xj = B[np.isfinite(B[:, j]), j]
        res['beta%d' % j] = mu
        res['t%d' % j] = tt
        res['pos%d' % j] = float((xj > 0).mean()) if xj.size else np.nan
    return res


def delta_r2(res_hi, res_lo):
    """ΔR² = mean(R²_hi - R²_lo) 于两模型同日有效处."""
    a, b = res_hi['r2'], res_lo['r2']
    m = np.isfinite(a) & np.isfinite(b)
    if not m.any():
        return np.nan, 0
    return float((a[m] - b[m]).mean()), int(m.sum())


# ================================================================== quantile
def spearman(x, y):
    x = np.asarray(x, dtype='float64'); y = np.asarray(y, dtype='float64')
    m = np.isfinite(x) & np.isfinite(y)
    if m.sum() < 3:
        return np.nan
    a = pd.Series(x[m]).rank().to_numpy()
    b = pd.Series(y[m]).rank().to_numpy()
    a = a - a.mean(); b = b - b.mean()
    sa = np.sqrt((a * a).mean()); sb = np.sqrt((b * b).mean())
    if sa <= 0 or sb <= 0:
        return np.nan
    return float((a * b).mean() / (sa * sb))


def quantile_stats(F, Y, u, horizon=PRIMARY_H, q=5, cost_bp=COST_BP, min_n=30,
                   day_mask=None):
    """§9 分层: 非重叠 h 日周期分组, 报 gross/net/win30/median/maxdd/PF + Q5-Q1."""
    S, N = F.shape
    ret = {gi: [] for gi in range(1, q + 1)}
    obs = {gi: [] for gi in range(1, q + 1)}
    to = {gi: [] for gi in range(1, q + 1)}
    prev = {}
    for t in range(0, N, horizon):
        if day_mask is not None and not day_mask[t]:
            continue
        m = u[:, t] & np.isfinite(F[:, t]) & np.isfinite(Y[:, t])
        n = int(m.sum())
        if n < min_n:
            continue
        idx = np.where(m)[0]
        v = F[idx, t].astype('float64')
        rank = np.empty(len(v), dtype='float64')
        rank[np.argsort(v, kind='stable')] = np.arange(len(v))
        grp = (rank * q // len(v)).astype('int64') + 1
        yv = Y[idx, t].astype('float64')
        for gi in range(1, q + 1):
            mm = grp == gi
            nk = int(mm.sum())
            if nk == 0:
                continue
            ret[gi].append(float(yv[mm].mean()))
            obs[gi].append(yv[mm])
            cur = set(idx[mm].tolist())
            p = prev.get(gi)
            if p:
                to[gi].append(len(cur ^ p) / float(max(len(cur), 1)))
            prev[gi] = cur
    rows = []
    gross = {}
    for gi in range(1, q + 1):
        r = np.array(ret[gi], dtype='float64')
        o = np.concatenate(obs[gi]) if obs[gi] else np.array([])
        tv = float(np.mean(to[gi])) if to[gi] else np.nan
        cum = np.cumprod(1.0 + r) if r.size else np.array([1.0])
        peak = np.maximum.accumulate(cum)
        dd = float((cum / peak - 1.0).min()) if cum.size else np.nan
        pos = float(o[o > 0].sum()) if o.size else 0.0
        neg = float(-o[o < 0].sum()) if o.size else 0.0
        row = {'q_group': gi, 'n_obs': int(o.size),
               'n_mean': float(np.mean([len(x) for x in obs[gi]])) if obs[gi] else np.nan,
               'gross': float(r.mean()) if r.size else np.nan,
               'win30': float((o > 0).mean()) if o.size else np.nan,
               'median': float(np.median(o)) if o.size else np.nan,
               'max_drawdown': dd,
               'profit_factor': (pos / neg) if neg > 0 else np.nan,
               'turnover': tv, 'n_periods': int(r.size)}
        for c in cost_bp:
            row['net%d' % c] = (row['gross'] - (tv * c / 1e4)
                                if np.isfinite(tv) else np.nan)
        gross[gi] = row['gross']
        rows.append(row)
    spread = {}
    gs = [gross.get(g) for g in range(1, q + 1)]
    mono = spearman(np.arange(1, q + 1), gs) if all(
        np.isfinite(x) for x in gs) else np.nan
    for c in cost_bp:
        nq = [r['net%d' % c] for r in rows if r['q_group'] == q]
        n1 = [r['net%d' % c] for r in rows if r['q_group'] == 1]
        spread['spread_net%d' % c] = (nq[0] - n1[0]) if (nq and n1) else np.nan
    spread['monotonicity'] = mono
    spread['gross_spread'] = gross.get(q, np.nan) - gross.get(1, np.nan)
    return rows, spread


# ================================================================== regime
def regime_labels(g, bench, lg=None):
    """§10.3 四 Regime 轴 (逐日 PIT)."""
    N = len(g['dates'])
    cz = pd.Series(bench['close_zz'], dtype='float64')
    ma20 = cz.rolling(20, min_periods=20).mean()
    ma60 = cz.rolling(60, min_periods=60).mean()
    trend = np.full(N, 'NA', dtype=object)
    for t in range(N):
        if not (np.isfinite(cz.iloc[t]) and np.isfinite(ma20.iloc[t])
                and np.isfinite(ma60.iloc[t])):
            continue
        if cz.iloc[t] > ma20.iloc[t] > ma60.iloc[t]:
            trend[t] = 'BULL'
        elif cz.iloc[t] < ma20.iloc[t] < ma60.iloc[t]:
            trend[t] = 'BEAR'
        else:
            trend[t] = 'NEUTRAL'
    cm = pd.Series(bench['close_mkt'], dtype='float64')
    d20 = (cz / cz.shift(20) - 1.0) - (cm / cm.shift(20) - 1.0)
    style = np.where(np.isfinite(d20.to_numpy()), np.where(
        d20.to_numpy() >= 0, 'SMALL', 'LARGE'), 'NA')
    rv = pd.Series(bench['ret_zz'], dtype='float64').rolling(
        20, min_periods=15).std()
    qv = rv.rolling(250, min_periods=120).rank(pct=True).to_numpy()
    vol = np.where(np.isfinite(qv), np.where(
        qv < 1.0 / 3, 'LOW', np.where(qv > 2.0 / 3, 'HIGH', 'MID')), 'NA')
    mon = np.array([int(d[4:6]) for d in g['dates']])
    earn = np.where(np.isin(mon, [4, 8, 10]), 'YES', 'NO')
    if lg:
        lg('regime trend=%s' % dict(zip(*np.unique(trend, return_counts=True))))
    return {'trend': trend, 'style': style, 'vol': vol, 'earn': earn}


def regime_ic(ic, labels, lag=NW_LAG):
    """按 axis 标签分组汇总 IC 均值/t."""
    out = {}
    lab = np.asarray(labels)
    for v in np.unique(lab):
        if v == 'NA':
            continue
        x = ic[lab == v]
        mu, t, _ = nw_t(x, lag)
        out[str(v)] = {'IC_mean': mu, 't_NW': t, 'n_days': int(np.isfinite(x).sum())}
    return out


# ================================================================== null
def _long_valid(Rf, Rl, U, day_mask=None):
    """把每日有效单元格拉平 (按日连续分段)."""
    S, N = Rf.shape
    seg, dl = [], np.zeros(N, dtype='int64')
    for t in range(N):
        if day_mask is not None and not day_mask[t]:
            continue
        m = U[:, t] & np.isfinite(Rf[:, t]) & np.isfinite(Rl[:, t])
        idx = np.where(m)[0]
        seg.append(idx)
        dl[t] = len(idx)
    pos = np.concatenate(seg) if seg else np.array([], dtype='int64')
    starts = np.zeros(N, dtype='int64')
    starts[1:] = np.cumsum(dl)[:-1]
    return pos, dl, starts


def _corr_from_long(a, b, dl, starts, min_n):
    nz = np.where(dl > 0)[0]
    if nz.size == 0:
        return np.full(len(dl), np.nan)
    st = starts[nz]
    n = dl[nz].astype('float64')
    sa = np.add.reduceat(a, st); sb = np.add.reduceat(b, st)
    saa = np.add.reduceat(a * a, st); sbb = np.add.reduceat(b * b, st)
    sab = np.add.reduceat(a * b, st)
    ma, mb = sa / n, sb / n
    va = saa / n - ma * ma
    vb = sbb / n - mb * mb
    cov = sab / n - ma * mb
    with np.errstate(invalid='ignore', divide='ignore'):
        r = cov / np.sqrt(va * vb)
    r = np.where(n >= min_n, r, np.nan)
    out = np.full(len(dl), np.nan)
    out[nz] = r
    return out


def null_test(Rf, Rl, U, kind='N1', B=NULL_B, seed=SEED, min_n=IC_MIN_N,
              day_mask=None):
    """§12 置换检验 (N1 同日打乱 / N2 个股内时序重排). 返回 {obs,p,b,resolution}.

    加速: 逐日 n / Σb / Σb² 在置换下不变, 预计算一次; N1 下逐日 Σa / Σa² 亦不变,
    故每次抽样只需重算逐日 Σ(ap·b)。
    """
    if kind not in ('N1', 'N2'):
        raise ValueError('null_test 仅支持 N1/N2; N3 见 null_n3')
    rng = np.random.default_rng(seed)
    N = Rf.shape[1]
    pos, dl, starts = _long_valid(Rf, Rl, U, day_mask)
    if pos.size == 0:
        return {'obs': np.nan, 'p': np.nan, 'b': B, 'resolution': np.nan}
    day = np.repeat(np.arange(N), dl)
    a = Rf[pos, day].astype('float64')
    b = Rl[pos, day].astype('float64')
    nz = np.where(dl > 0)[0]
    st = starts[nz]
    n = dl[nz].astype('float64')
    good = n >= min_n
    sb = np.add.reduceat(b, st)
    sbb = np.add.reduceat(b * b, st)
    mb = sb / n
    vb = sbb / n - mb * mb

    def _mean_r(sab, sa, saa):
        ma = sa / n
        va = saa / n - ma * ma
        with np.errstate(invalid='ignore', divide='ignore'):
            r = (sab / n - ma * mb) / np.sqrt(va * vb)
        r = np.where(good, r, np.nan)
        return float(np.nanmean(r))

    sa = np.add.reduceat(a, st)
    saa = np.add.reduceat(a * a, st)
    obs = _mean_r(np.add.reduceat(a * b, st), sa, saa)
    draws = np.full(B, np.nan)
    lens = dl[nz]
    if kind == 'N1':
        # 同日置换下 ma / va / mu / den 均不变, 逐次只重算 Σ(a_p·b).
        ma = sa / n
        va = saa / n - ma * ma
        mu = ma * mb
        den = np.sqrt(va * vb)
        segs = list(zip(st.tolist(), lens.tolist()))
        for ib in range(B):
            ap = a.copy()
            for s0, ln in segs:
                rng.shuffle(ap[s0:s0 + ln])          # 段内原地均匀置换
            ap *= b
            with np.errstate(invalid='ignore', divide='ignore'):
                r = (np.add.reduceat(ap, st) / n - mu) / den
            r[~good] = np.nan
            draws[ib] = float(np.nanmean(r))
    else:
        order = np.argsort(pos, kind='stable')
        sid = pos[order]
        S = Rf.shape[0]
        bounds = np.searchsorted(sid, np.arange(S + 1))
        cut = [(order[bounds[s]:bounds[s + 1]]) for s in range(S)]
        acc = np.empty((3, a.size))
        for ib in range(B):
            ap = a.copy()
            for loc in cut:
                if loc.size > 1:
                    ap[loc] = ap[loc][rng.permutation(loc.size)]
            np.multiply(ap, b, out=acc[0])
            acc[1] = ap
            np.multiply(ap, ap, out=acc[2])
            red = np.add.reduceat(acc, st, axis=1)   # 一次 reduceat 取三个和
            draws[ib] = _mean_r(red[0], red[1], red[2])
    ok = draws[np.isfinite(draws)]
    if not ok.size:
        return {'obs': obs, 'p': np.nan, 'b': B, 'resolution': 0.0}
    p = float((ok >= obs).mean())
    return {'obs': obs, 'p': float((p * ok.size + 1) / (ok.size + 1)),
            'b': B, 'resolution': float(ok.size / B)}


def null_n3(ic38, obs, B=NULL_B, seed=SEED):
    """N3: 每日从同期 38 个正交因子中随机抽 1 个作为对照."""
    rng = np.random.default_rng(seed + 7)
    N, K = ic38.shape
    valid = np.isfinite(ic38)
    ncand = valid.sum(axis=1)
    draws = np.full(B, np.nan)
    for ib in range(B):
        tot, cnt = 0.0, 0
        for t in range(N):
            if ncand[t] == 0:
                continue
            cand = np.where(valid[t])[0]
            c = cand[rng.integers(len(cand))]
            tot += ic38[t, c]
            cnt += 1
        draws[ib] = tot / cnt if cnt else np.nan
    ok = draws[np.isfinite(draws)]
    p = float((ok >= obs).mean()) if ok.size else np.nan
    return {'obs': float(obs), 'p': float((p * ok.size + 1) / (ok.size + 1))
            if ok.size else np.nan, 'b': B, 'resolution': float(ok.size / B)}


# ================================================================== verdict
def map_verdict(n_true, pert_fragile=False):
    """§14 判决映射."""
    if pert_fragile:
        return 'FRAGILE'
    if n_true >= 12:
        return 'ROBUST'
    if n_true >= 9:
        return 'PROMISING'
    if n_true >= 5:
        return 'FRAGILE'
    return 'NO EDGE'


def map_verdict_factor(n_true, n_bits=10):
    """单因子判决 (默认 10 位 → 10/8/5; 11 位 → 11/9/5)."""
    pro = int(np.ceil(n_bits * 0.75))
    fra = int(np.ceil(n_bits / 2.4))
    if n_true >= n_bits:
        return 'ROBUST'
    if n_true >= pro:
        return 'PROMISING'
    if n_true >= fra:
        return 'FRAGILE'
    return 'NO EDGE'


# ================================================================== future scan
def future_scan(g, basic, bench, lg=None, ncut=120):
    """§16.3 四项检查. 返回 (ok, detail_dict)."""
    detail = {}
    ok = True
    # 3. forward_window_isolation
    inter = set(FACTOR_COLUMNS) & set(LABEL_COLUMNS)
    detail['forward_window_isolation'] = (len(inter) == 0, sorted(inter))
    ok &= (len(inter) == 0)
    # 4. label_columns_marked
    detail['label_columns_marked'] = (len(LABEL_COLUMNS) == 4, list(LABEL_COLUMNS))
    ok &= (len(LABEL_COLUMNS) == 4)
    # 1. truncation_invariance (A1/A2/A4 价格/估值类)
    S, N = g['close'].shape
    keep_n = N - ncut
    gt = {k: (v[:, :keep_n] if isinstance(v, np.ndarray) and v.ndim == 2 else v)
          for k, v in g.items()}
    gt['dates'] = g['dates'][:keep_n]
    bt = {k: (v[:, :keep_n] if isinstance(v, np.ndarray) and v.ndim == 2 else v)
          for k, v in basic.items()}
    F2 = build_A2(gt)
    F4 = build_A4(gt, bt)
    # 逐位比较 (在全面板同日期重构一次)
    Ff2 = build_A2(g)
    Ff4 = build_A4(g, basic)
    bad = 0
    for n in F2:
        a = Ff2[n][:, :keep_n]
        b = F2[n]
        m = np.isfinite(a) & np.isfinite(b)
        bad += int((np.abs(a[m] - b[m]) > 1e-4).sum())
    for n in F4:
        a = Ff4[n][:, :keep_n]
        b = F4[n]
        m = np.isfinite(a) & np.isfinite(b)
        bad += int((np.abs(a[m] - b[m]) > 1e-4).sum())
    detail['truncation_invariance'] = (bad == 0, bad)
    ok &= (bad == 0)
    # 2. pit_fundamental_proof
    p = os.path.join(FS_DATA, 'panel.parquet')
    d = pd.read_parquet(p, columns=['ts_code', 'ann_date', 'report_type', 'roe'])
    d = d[d['report_type'].astype(str) == '1'].dropna(subset=['roe'])
    M = pit_fill(g, d, 'roe')
    viol = 0
    checked = 0
    rng = np.random.default_rng(SEED)
    ii = np.where(np.isfinite(M))
    if len(ii[0]):
        sel = rng.choice(len(ii[0]), size=min(500, len(ii[0])), replace=False)
        dates = g['dates']
        for k in sel.tolist():
            s, t = int(ii[0][k]), int(ii[1][k])
            tc = g['codes'][s]
            sub = d[(d['ts_code'] == tc) & (d['ann_date'].astype(str) <= dates[t])]
            checked += 1
            if sub.empty or str(sub['ann_date'].iloc[-1]) > dates[t]:
                viol += 1
    detail['pit_fundamental_proof'] = (viol == 0, {'checked': checked, 'viol': viol})
    ok &= (viol == 0)
    if lg:
        for k, v in detail.items():
            lg('future_scan %s -> %s' % (k, v[0]))
    return ok, detail


# ================================================================== segments
def seg_mask(dates, pool, seg):
    """§4.2 段掩码 (PRE/IS/VALID/OOS) -> bool (N,)."""
    lo, hi = WINDOWS[pool][seg]
    d = np.asarray(dates)
    return (d >= lo) & (d <= hi)


def seg_ic(ic, cnt, dates, pool, seg, lag=NW_LAG):
    """§7.4 把日频 IC 切到指定段汇总, 返回 ic_row 结果 + 段信息."""
    m = seg_mask(dates, pool, seg)
    r = ic_row(np.where(m, ic, np.nan), np.where(m, cnt, 0), lag=lag)
    r['seg'] = seg
    r['seg_days'] = int(m.sum())
    return r


# ================================================================== R-DEDUP
def high_quantile_mask(F, u, q=5):
    """逐日池内因子值最高 1/q 记「高分区」事件源 (§7.5 R-DEDUP)."""
    S, N = F.shape
    H = np.zeros((S, N), dtype=bool)
    for t in range(N):
        m = u[:, t] & np.isfinite(F[:, t])
        n = int(m.sum())
        if n < q * 5:
            continue
        idx = np.where(m)[0]
        order = np.argsort(F[idx, t], kind='stable')
        cut = int(np.ceil(n * (1.0 - 1.0 / q)))
        H[idx[order[cut:]], t] = True
    return H


def dedup_rank(F, u, gap=5, q=5):
    """§7.5 R-DEDUP 预处理: 事件掩码 dm 与事件日因子 rank 面板 Rd.

    dm 只依赖 (F, u), 与 label/horizon 无关 → 每因子只做一次, 各 label 复用。
    """
    dm = cluster_events(high_quantile_mask(F, u, q=q), gap=gap)
    Rd = xs_rank_pct(np.where(dm, F, np.nan), dm)
    return dm, Rd


def dedup_ic(F, Rl, u, gap=5, q=5, min_n=5):
    """§7.5 R-DEDUP: 仅取「高分区首次进入日」单元格做逐日横截面 IC.

    消除 h 日重叠标注导致的 t 虚高; 报普通 t (lag=0), 不用 Newey-West.
    """
    dm, Rd = dedup_rank(F, u, gap=gap, q=q)
    ic, cnt = daily_ic(Rd, Rl, min_n=min_n)
    r = ic_row(ic, cnt, lag=0)
    r['n_cells'] = int(dm.sum())
    return ic, cnt, r


def dedup_ic_rank(Rd, Rl, min_n=5):
    """复用 dedup_rank 的 Rd, 对给定 label rank 面板取 R-DEDUP IC."""
    ic, cnt = daily_ic(Rd, Rl, min_n=min_n)
    return ic, cnt, ic_row(ic, cnt, lag=0)


# ================================================================== walk-fwd
WF_TESTS = (('20240101', '20240630'), ('20240701', '20241231'),
            ('20250101', '20250630'), ('20250701', '20251231'),
            ('20260101', '20260630'))
WF_TRAIN_YEARS = 3


def wf_folds(pool, train_years=WF_TRAIN_YEARS, tests=WF_TESTS):
    """§4.3 fold: train train_years 年 → test 半年, step 半年."""
    ps = WINDOWS[pool]['POOL_START']
    out = []
    for tl, th in tests:
        want = '%04d%s' % (int(tl[:4]) - train_years, tl[4:])
        lo = max(ps, want)
        out.append({'train': (lo, tl), 'test': (tl, th),
                    'wf_train_short': (lo != want)})
    return out


def walk_forward(F, Y, u, dates, pool, horizon=PRIMARY_H,
                 train_years=WF_TRAIN_YEARS, qg=5):
    """§10.2 逐 fold 报 n / IC / t_NW / net30(Q5-Q1) / 单调性.

    本模块的类内正交化与合成为「逐日横截面 + PIT」操作, 不含任何跨时间
    拟合系数; 合成权重为预注册常量。因此 fold 内无需在 train 段拟合跨时序
    参数, test 段 IC 天然无跨越泄漏 —— 逐 fold 直接汇报 test 段结果。
    """
    rows = []
    d = np.asarray(dates)
    ic, cnt = daily_ic(xs_rank_pct(F, u), Y)
    for fo in wf_folds(pool, train_years):
        tl, th = fo['test']
        dm = (d >= tl) & (d <= th)
        rr = ic_row(np.where(dm, ic, np.nan), np.where(dm, cnt, 0))
        _, sp = quantile_stats(F, Y, u, horizon=horizon, q=qg, day_mask=dm)
        rows.append({
            'fold': '%s_%s' % (tl, th), 'train_lo': fo['train'][0],
            'train_hi': fo['train'][1], 'test_lo': tl, 'test_hi': th,
            'wf_train_short': int(fo['wf_train_short']),
            'n_days': rr['n_days'], 'IC_mean': rr['IC_mean'],
            't_NW': rr['t_NW'], 'ICIR': rr['ICIR'],
            'pos_ratio': rr['pos_ratio'],
            'monotonicity': sp['monotonicity'],
            'gross_spread': sp['gross_spread'],
            'net30_spread': sp.get('spread_net30', np.nan),
        })
    return rows


def wf_verdict(rows, min_ic=WF_IC_MIN):
    """§10.2 多数 fold 同号 且 |IC| >= 0.005 → WF_STABLE."""
    ic = np.array([r['IC_mean'] for r in rows], dtype='float64')
    ic = ic[np.isfinite(ic)]
    if not ic.size:
        return False, np.nan, 0
    sgn = np.sign(ic)
    maj = np.sign(sgn.sum())
    frac = float((sgn == maj).mean())
    ok = bool(frac > 0.5 and abs(float(ic.mean())) >= min_ic)
    return ok, frac, int(ic.size)


# ================================================================== pipeline
def group_pipeline(F, u, lg=None, tag=''):
    """标准 → 覆盖门禁 → 类内 Löwdin → 类内等权合成. 返回 (C, coverage, drop)."""
    cov = factor_coverage(F, u)
    keep, drop = orth_input(F, cov, lg=lg, tag=tag)
    if not keep:
        raise ValueError('group %s 无可用因子' % tag)
    lo = lowdin(standardize(keep, u), u, lg=lg)
    return group_composite(lo, u, lg=lg, tag=tag), cov, drop


# ================================================================== incremental
def incremental(Yr, u, M0, C, Cperp, Rc, Rp, Ro):
    """§11.1 M0/M1_k/M2a/M2b/M3/M4_k. 返回 (out, comp_eq, comp_prior)."""
    names = list(Ro)
    comp_eq, _ = composite_from_rank(C, u, {k: 1.0 / len(C) for k in C})
    comp_prior, _ = composite_from_rank(C, u, COMP_PRIOR)
    out = {}
    base = xs_reg(Yr, u, M0, [], tag='M0')
    out['M0'] = {'res': base, 'dR2': 0.0, 'n': base['n_days'], 'extra': []}
    for k in Rc:
        r = xs_reg(Yr, u, M0, [Rc[k]], tag='M1_%s' % k)
        d, n = delta_r2(r, base)
        out['M1_%s' % k] = {'res': r, 'dR2': d, 'n': n, 'extra': ['C_%s' % k]}
    r = xs_reg(Yr, u, M0, [Rc[k] for k in Rc], tag='M2a')
    d, n = delta_r2(r, base)
    out['M2a'] = {'res': r, 'dR2': d, 'n': n,
                  'extra': ['C_%s' % k for k in Rc]}
    r = xs_reg(Yr, u, M0, [comp_prior], tag='M2b')
    d, n = delta_r2(r, base)
    out['M2b'] = {'res': r, 'dR2': d, 'n': n, 'extra': ['COMP_PRIOR']}
    r = xs_reg(Yr, u, M0, [Ro[nm] for nm in names], tag='M3')
    d, n = delta_r2(r, base)
    out['M3'] = {'res': r, 'dR2': d, 'n': n, 'extra': names}
    for k in Rp:
        r = xs_reg(Yr, u, M0, [Rp[k]], tag='M4_%s' % k)
        d, n = delta_r2(r, base)
        out['M4_%s' % k] = {'res': r, 'dR2': d, 'n': n,
                            'extra': ['Cperp_%s' % k]}
    return out, comp_eq, comp_prior


def independence_flags(inc, ic_c, ic_cp):
    """§11.2 I1/I2/I3 → INDEPENDENT / PARTIAL / REDUNDANT."""
    out = {}
    for k in GROUPS:
        m1, m4 = inc['M1_%s' % k], inc['M4_%s' % k]
        t1 = m1['res'].get('t0', np.nan)
        t4 = m4['res'].get('t0', np.nan)
        i1 = bool(np.isfinite(m1['dR2']) and m1['dR2'] > 0
                  and np.isfinite(t1) and t1 >= IC_T_MIN)
        i2 = bool(np.isfinite(m4['dR2']) and m4['dR2'] > 0
                  and np.isfinite(t4) and t4 >= IC_T_MIN)
        a, b = ic_c.get(k, np.nan), ic_cp.get(k, np.nan)
        i3 = bool(np.isfinite(a) and np.isfinite(b) and abs(b) >= IND_IC_MIN
                  and np.sign(a) == np.sign(b))
        n_ok = int(i1) + int(i2) + int(i3)
        out[k] = {'I1': i1, 'I2': i2, 'I3': i3, 'n_ok': n_ok,
                  'dR2_M1': m1['dR2'], 'dR2_M4': m4['dR2'],
                  't_M1': t1, 't_M4': t4,
                  'IC_C': a, 'IC_Cperp': b,
                  'verdict': ('INDEPENDENT' if n_ok == 3 else
                              'PARTIAL' if n_ok >= 1 else 'REDUNDANT')}
    return out


def single_factor_incremental(Yr, u, M0, Rc, Ro, t_min=IC_T_MIN):
    """§11.4 单因子级独立增量: y ~ M0 + 该因子 + 其余 37 因子 + C_{-k}."""
    names = list(Ro)
    rows = []
    for tgt in names:
        k = tgt.split('_')[0]
        others = [Ro[n] for n in names if n != tgt]
        cm = [Rc[o] for o in GROUPS if o != k]
        r_with = xs_reg(Yr, u, M0, [Ro[tgt]] + others + cm, tag=tgt)
        r_wo = xs_reg(Yr, u, M0, others + cm, tag=tgt + '_wo')
        d, n = delta_r2(r_with, r_wo)
        b = r_with.get('beta0', np.nan)
        tt = r_with.get('t0', np.nan)
        rows.append({
            'factor': tgt, 'beta': b, 't_NW': tt,
            'pos_ratio': r_with.get('pos0', np.nan),
            'dR2': d, 'n_days': n, 'n_extra': len(others) + len(cm),
            'significant': int(bool(np.isfinite(tt) and tt >= t_min
                                    and np.isfinite(d) and d > 0)),
        })
    return rows


# ================================================================== verdict
def verdict_bits(is_ic, is_t, sign_ok, valid_ic, valid_t, oos_ic, mono, net30,
                 pert_stable, regime_same, null_p, dedup_ic, cperp_t, wf_ok):
    """§14 g1..g12 判决位 (h*=T+5, 主 label y_ex_zz)."""
    def fin(x):
        return bool(np.isfinite(x)) if not isinstance(x, bool) else bool(x)
    b = {
        'g1': bool(sign_ok),
        'g2': bool(fin(is_t) and abs(is_t) >= IC_T_MIN),
        'g3': bool(fin(valid_ic) and fin(valid_t) and fin(is_ic)
                   and np.sign(valid_ic) == np.sign(is_ic)
                   and abs(valid_t) >= VALID_T_MIN),
        'g4': bool(fin(oos_ic) and fin(is_ic)
                   and np.sign(oos_ic) == np.sign(is_ic)),
        'g5': bool(fin(mono) and mono >= MONO_MIN),
        'g6': bool(fin(net30) and net30 > 0),
        'g7': bool(pert_stable),
        'g8': bool(regime_same),
        'g9': bool(fin(null_p) and null_p < 0.05),
        'g10': bool(fin(dedup_ic) and fin(is_ic)
                    and np.sign(dedup_ic) == np.sign(is_ic)
                    and abs(dedup_ic) >= DEDUP_IC_MIN),
        'g11': bool(fin(cperp_t) and cperp_t >= IC_T_MIN),
        'g12': bool(wf_ok),
    }
    return b


def verdict_bits_factor(is_ic, is_t, sign_ok, valid_ic, valid_t, oos_ic, mono,
                        net30, pert_stable, regime_same, null_p, dedup_ic,
                        dR2_single):
    """§14 单因子 10 位 (g1..g10) + g12b(单因子 ΔR² > 0) 额外位."""
    b = {'g1': bool(sign_ok),
         'g2': bool(np.isfinite(is_t) and abs(is_t) >= IC_T_MIN),
         'g3': bool(np.isfinite(valid_ic) and np.isfinite(valid_t)
                    and np.isfinite(is_ic) and np.sign(valid_ic) == np.sign(is_ic)
                    and abs(valid_t) >= VALID_T_MIN),
         'g4': bool(np.isfinite(oos_ic) and np.isfinite(is_ic)
                    and np.sign(oos_ic) == np.sign(is_ic)),
         'g5': bool(np.isfinite(mono) and mono >= MONO_MIN),
         'g6': bool(np.isfinite(net30) and net30 > 0),
         'g7': bool(pert_stable),
         'g8': bool(regime_same),
         'g9': bool(np.isfinite(null_p) and null_p < 0.05),
         'g10': bool(np.isfinite(dedup_ic) and np.isfinite(is_ic)
                     and np.sign(dedup_ic) == np.sign(is_ic)
                     and abs(dedup_ic) >= DEDUP_IC_MIN),
         'g12b': bool(np.isfinite(dR2_single) and dR2_single > 0)}
    return b


def regime_same_sign(d):
    """§10.3 三档市场趋势 IC 同号 → True."""
    v = [x['IC_mean'] for x in d.values() if np.isfinite(x['IC_mean'])]
    if len(v) < 2:
        return False
    s = np.sign(v)
    return bool((s == s[0]).all())


# ================================================================== funnel
def funnel(g, u, elig, F, C, Cperp, n_independent, pool, mem=None, lg=None):
    """§16.4 样本漏斗 (单元格数 / 有效交易日数)."""
    stages = [('全A(close非缺失)', np.isfinite(g['close'])),
              ('池成分(PIT)', None if mem is None else mem.astype(bool)),
              ('可交易/非ST/非退市/上市龄达标', elig),
              ('池内(交集)', u)]
    rows = []
    for sname, M in stages:
        if M is None:
            rows.append({'pool': pool, 'stage': sname, 'cells': 0, 'days': 0})
            continue
        rows.append({'pool': pool, 'stage': sname, 'cells': int(M.sum()),
                     'days': int((M.sum(axis=0) > 0).sum())})
    m = u.copy()
    for fn in ALL_FACTORS:
        m = m & (np.isfinite(F[fn]) if fn in F else False)
    rows.append({'pool': pool, 'stage': '因子有效截面(全因子联合)',
                 'cells': int(m.sum()), 'days': int((m.sum(axis=0) > 0).sum())})
    m2 = u.copy()
    for k in C:
        m2 = m2 & np.isfinite(C[k])
    rows.append({'pool': pool, 'stage': '类内合成可用',
                 'cells': int(m2.sum()), 'days': int((m2.sum(axis=0) > 0).sum())})
    m3 = u.copy()
    for k in Cperp:
        m3 = m3 & np.isfinite(Cperp[k])
    rows.append({'pool': pool, 'stage': '类间正交后可用',
                 'cells': int(m3.sum()), 'days': int((m3.sum(axis=0) > 0).sum())})
    rows.append({'pool': pool, 'stage': '独立增量入池(因子数)',
                 'cells': int(n_independent), 'days': int(n_independent)})
    if lg:
        for r in rows:
            lg('funnel %s %-24s cells=%d days=%d'
               % (pool, r['stage'], r['cells'], r['days']))
    return rows


# ================================================================== §13 perturb
# 预注册格点 (每轴首项 = 主口径; 见 SPEC §13)
PERT_GRID = {
    'h': (5, 10, 20),
    'q_groups': (5, 10),
    'roll_window': (120, 250, 500),
    'vol_window': (20, 60, 120),
    'pool': POOLS,
    'composite': ('COMP_EQ', 'COMP_PRIOR'),
}
PERT_AXES = ('h', 'q_groups', 'roll_window', 'vol_window', 'pool', 'composite')
PERT_BASE = {'h': PRIMARY_H, 'q_groups': 5, 'roll_window': 250,
             'vol_window': 60, 'pool': 'U-ZZ2K', 'composite': 'COMP_PRIOR'}


def star_grid(base=None):
    """§13 star 设计: 基线 + 每轴单点扰动. 返回全部**非基线**格点 (dict)."""
    b = dict(PERT_BASE if base is None else base)
    pts = []
    for a in PERT_AXES:
        for v in PERT_GRID[a]:
            if v == b[a]:
                continue
            p = dict(b)
            p[a] = v
            p['axis'] = a
            pts.append(p)
    return pts


def pert_cfg_key(cfg):
    return 'h%d_q%d_roll%d_vol%d_%s_%s' % (
        cfg['h'], cfg['q_groups'], cfg['roll_window'], cfg['vol_window'],
        cfg['pool'], cfg['composite'])


def pert_pos_ratio(base_ic, ics, min_ic=DEDUP_IC_MIN):
    """§13 positive_ratio = 格点中「IC 与基线同号 且 |IC| >= min_ic」的比例."""
    if not np.isfinite(base_ic):
        return np.nan, 0, 0
    s0 = np.sign(base_ic)
    ok, tot = 0, 0
    for v in ics:
        tot += 1
        if np.isfinite(v) and np.sign(v) == s0 and abs(v) >= min_ic:
            ok += 1
    return ((ok / tot) if tot else np.nan), ok, tot


def pert_verdict(pos_ratio, stable=PERT_STABLE, fragile=PERT_FRAGILE):
    """§13 判定: >=0.60 stable / <=0.40 FRAGILE / 其余 mixed."""
    if not np.isfinite(pos_ratio):
        return 'NA'
    if pos_ratio >= stable:
        return 'stable'
    if pos_ratio <= fragile:
        return 'FRAGILE'
    return 'mixed'


