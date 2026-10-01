# -*- coding: utf-8 -*-
"""HVE-Research V1 common layer.

Research title
    High Volume Event -> Post-Event Path -> HVT-BULL / W7 second breakout

Core hypothesis
    HVT-BULL and the W7 second-wave buy point may not be two independent
    strategies but two different price paths after the SAME mother event:
    a标志性高量事件 (High Volume Event, HVE).

Discipline (spec section 1 / 40)
    * No look-ahead anywhere.  Every rolling percentile / mean / std /
      volume baseline uses only data at or before the decision session.
    * Candidate definitions and every price-layer parameter below are
      PRE-REGISTERED before any result is inspected.  Selection is
      IS -> OOS, never "the threshold that looked best".
    * No composite score, no 0-100 ranking (spec section 36).
    * Historical universe only; delisted names are kept so the result is
      not a survivorship artefact (spec section 33).

Data source: the existing Tushare cache only (spec section 2).
"""
import os, sys, json, time, warnings
import numpy as np
import pandas as pd

# Every division below is guarded by np.where(...) and NaN is the intended
# sentinel, so the "invalid value / divide" RuntimeWarnings are pure noise in
# the audit logs.  Silence only those categories.
np.seterr(divide='ignore', invalid='ignore')
warnings.filterwarnings('ignore', message='All-NaN slice encountered')
warnings.filterwarnings('ignore', message='Mean of empty slice')

HERE = os.path.dirname(os.path.abspath(__file__))
FS_DATA = os.path.abspath(os.path.join(HERE, '..',
                                       'fundamental_surprise_alpha', 'data'))
CD = r'D:\mystock\cache_daily'
PQ = os.path.join(CD, 'parquet')
REPORT_DAILY = os.path.abspath(os.path.join(HERE, '..', '..', 'report_daily'))
OUT = HERE
DATA = os.path.join(OUT, 'data')
os.makedirs(DATA, exist_ok=True)

sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..', 'h_earn_fwd')))
from hef_common import (load_calendar, trade_offset, load_industry_map,   # noqa
                        industry_map_fast, industry_at_fast,               # noqa
                        ols_resid, dummies, demean_by, fmt, pct)           # noqa

GRID_CACHE = os.path.join(DATA, 'hve_grid.npz')
GRID_META = os.path.join(DATA, 'hve_grid_meta.json')
IND_CACHE = os.path.join(DATA, 'hve_ind.npz')
IND_META = os.path.join(DATA, 'hve_ind_meta.json')

# Bump whenever the *content* of a cached array changes for a reason the
# freshness checks below cannot see (a formula fix, a dtype change, a
# dropped field).  Without it a stale cache is loaded silently and the
# module reports numbers produced by code that no longer exists -- which
# already happened twice (namechange path fix, body formula fix).
CACHE_VERSION = 4       # 4: _roll_* now roll along time (axis 1)

# =====================================================================
# PREREG -- frozen before any result was observed
# =====================================================================
PREREG = {
    'hypothesis_id': 'HVE-Research-V1',
    'created': '2026-09-26',
    'version': 'V1.0',
    'title': 'High Volume Event -> Post-Event Path -> HVT-BULL / W7',

    # ---- sample -------------------------------------------------------
    'study_start': '20190101',
    'burnin_start': '20180102',
    'min_list_days': 250,          # listing age required at the event date
    'min_hist_bars': 250,          # bars required before an event
    'st_excluded': True,
    'delist_excluded': True,
    'long_suspension_days': 60,    # a 60+ session hole forbids the event
    'universe_layers': ('ALL', 'MAIN', 'GEM', 'STAR'),

    # ---- section 4.1 volume shock candidates --------------------------
    'volume_indicators': ('VR5', 'VR10', 'VR20', 'VR60',
                          'VOL_MA20', 'VOL_MA60', 'AMT_MA20',
                          'VOL_PCT', 'AMT_PCT', 'TURN', 'TURN_PCT'),
    'abs_thresholds': (1.5, 2.0, 2.5, 3.0, 4.0),
    'pct_thresholds': (0.90, 0.95, 0.975, 0.99),
    'pct_indicators': ('VOL_PCT', 'AMT_PCT', 'TURN_PCT'),   # use pct_thresholds
    'ratio_indicators': ('VR5', 'VR10', 'VR20', 'VR60',
                         'VOL_MA20', 'VOL_MA60', 'AMT_MA20', 'TURN'),
    'ratio_extended': (1.2, 1.5, 1.8, 2.0, 2.5, 3.0, 4.0, 5.0),
    'pct_rank_window': 250,
    'ma_window': (20, 60),
    'bin_pct_default': 0.0,

    # ---- section 5 event families -------------------------------------
    'event_types': ('HVE_A', 'HVE_B', 'HVE_C'),
    'price_gate_B': {'abs_pct_chg': 0.03},
    'price_gate_C': {'pct_chg_min': 0.03, 'clv_min': 0.70,
                     'body_min': 0.30, 'close_gt_ma20': True},
    'clv_rule': '(close - low) / (high - low)',
    'body_rule': '(close - open) / (high - low)',
    'body_rule_note': 'aligned with the production HVT-BULL engine '
                      '(hvt_bull/engine.py t0_body); the earlier '
                      'pre-registration draft wrote "/ pre_close", which '
                      'makes body>=0.30 a +30% day and therefore an empty '
                      'gate.  Corrected before any HVE_C result existed.',

    # ---- section 7 event de-duplication -------------------------------
    'cluster_gap': (3, 5, 10),
    'cluster_gap_primary': 5,
    'cluster_anchor': ('first', 'max_vol', 'max_amount'),
    'cluster_anchor_primary': 'max_amount',

    # ---- section 8/9 path --------------------------------------------
    'path_horizons': (1, 3, 5, 10, 20, 40, 60),
    'horizons': (3, 5, 10, 20),
    'primary_horizon': 10,
    'mfe_mae_window': 20,
    'path_prior_high': (20, 60),

    # ---- section 9/11 state machine (evaluated PIT at every day) ------
    'state_rules': {
        'structure_keep': 'close >= HVE_low and close >= 0.93*HVE_close',
        'structure_break': 'close < 0.90*HVE_close',
        'bull_hold': 'close > MA20 and MA20_slope5 > 0 '
                     'and close >= 0.95*HVE_close',
        'digestion': 'structure_keep and not bull_hold '
                     'and maxdd_from_HVE_close > -0.20',
        're_expansion': 'previous state was DIGESTION and close > MA20 '
                        'and vol/MA20(vol) >= 1.0 and close > recent 10d high',
        'fail': 'structure_break_triggered',
    },
    'contraction_ratio': (0.8, 0.6, 0.5, 0.4),
    'contraction_days': (2, 3, 5, 10),
    'contraction_primary': (0.6, 3),
    'contraction_metric': ('V_t_over_HVE', 'MA5_over_MA20',
                           'min_V_over_HVE'),
    'contraction_metric_primary': 'MA5_over_MA20',

    # ---- section 12 HVT-BULL candidate construction --------------------
    'hvt_confirm_max_wait': 20,    # sessions after t0 within which the
                                   # candidate must be confirmed
    'hvt_layers': ('L0_HVE', 'L1_TREND', 'L2_TREND_UP',
                   'L3_STRENGTH_CLOSE', 'L4_STRENGTH_HIGH',
                   'L5_NO_DAMAGE', 'L6_REEXPANSION'),
    'hvt_conditions': ('T_CLOSE_GT_MA20', 'T_MA20_SLOPE_UP',
                       'S_CLOSE_GT_HVE_CLOSE', 'S_CLOSE_GT_HVE_HIGH',
                       'N_NO_STRUCTURAL_DAMAGE', 'R_REEXPANSION'),
    'hvt_tail_horizon': 20,        # 'sustained right tail' window ...
    'hvt_tail_move': 0.25,         # ... and its size, measured from entry
    'hvt_dist_hi20_buckets': (-0.10, -0.05, 0.0),
    'hvt_maxdd_buckets': (-0.15, -0.08, -0.03),
    'hvt_entry_rule': 'a condition set is entered at the FIRST session of '
                      't0+1 .. t0+20 on which every condition holds, '
                      'evaluated with information up to that session only; '
                      'returns are measured from that session close.  L0 '
                      '(the unfiltered HVE) has no confirmation day and is '
                      'entered at the HVE close instead.  A no-show event is '
                      'dropped from the layer, never held as cash.',

    # ---- section 13/14/15/28 W7 second wave ----------------------------
    'w7_confirm_max_wait': 60,     # sessions after t0 in which the second
                                   # expansion must appear
    'w7_digest_min_days': 3,       # 'price digestion' quantified: sessions
                                   # before the breakout that are NOT in bull
                                   # hold while structure still holds
    'w7_chain': ('W0_HVE', 'W1_DIGESTION', 'W2_CONTRACTION',
                 'W3_STRUCTURE'),
    'w7_gate_entry_rule': 'each W7 stage is entered at the FIRST session of '
                          't0+1 .. t0+60 on which the stage condition holds, '
                          'using information up to that session only; the '
                          'breakout stage additionally requires that the '
                          'contraction completed strictly earlier',

    # ---- section 14 breakout definitions ------------------------------
    'breakout_defs': ('B20', 'B40', 'B_HVE', 'B_LOCAL'),
    'breakout_local_window': 10,
    'breakout_vol_min': 1.0,       # vol / MA20(vol) at the breakout day

    # ---- section 15 / 28 entry variants -------------------------------
    'entry_variants': ('ENTRY_PRE5', 'ENTRY_PRE3', 'ENTRY_PRE1',
                       'ENTRY_BD', 'ENTRY_BD1', 'ENTRY_PULLBACK'),
    'entry_pullback_max_wait': 10,
    'entry_pullback_tol': 0.97,    # retest must hold 0.97 * breakout level

    # ---- section 17 benchmarks / 18 null -----------------------------
    'benchmarks': ('B1_random_stock', 'B2_random_volume_event',
                   'B3_pure_breakout', 'B4_pure_momentum',
                   'B5_HVE_unfiltered', 'B6_HVE_random_entry'),
    'null_models': ('N1_random_date', 'N2_random_stock', 'N3_random_breakout',
                    'N4_random_entry_after_HVE', 'N5_shuffled_path'),
    'n_perm': 1000,
    'random_entry_max_lag': 60,

    # ---- section 19 costs --------------------------------------------
    'cost_bp': (0, 10, 20, 30, 50),
    'primary_cost_bp': 30,
    'cost_note': 'round trip: commission 2.5bp/side + stamp 5bp/sell + '
                 'slippage/impact 7.5bp/side = 30bp; the ladder is scaled '
                 'linearly from the same components.  Registered before the '
                 'run, never lowered after seeing a return.',

    # ---- section 23 walk forward -------------------------------------
    'wf_train_years': 3,
    'wf_valid_years': 1,
    'wf_oos_years': 1,
    'wf_start_year': 2019,
    'wf_end_year': 2026,

    # ---- section 24 parameter perturbation ---------------------------
    'perturb_pct': 0.20,

    # ---- regimes (section 22 / 29), independent of the strategy -------
    'regime_rule': 'CSI300 on the event date: BULL if close > MA60 and '
                   '60d return > +5%; BEAR if close < MA60 and 60d return '
                   '< -5%; else RANGE.',
    'vol_regime_rule': 'cross-sectional median 20d realised vol of the '
                       'universe on the event date; HIGH if above its own '
                       'expanding 60th percentile, LOW below the 40th, else MID.',
    'turnover_regime_rule': 'market aggregate turnover_rate on the event '
                            'date versus its own expanding median.',

    # ---- section 30 attribute layers ---------------------------------
    'mv_layers': (0.33, 0.67),
    'liq_layers': (0.33, 0.67),

    # ---- statistics ---------------------------------------------------
    'min_events': 30,
    'min_xs': 20,
    'seed': 20261101,
    'fdr_q': 0.05,
    'tail_flags': (0.05, 0.10),
    'tail_share_flag': 0.50,

    # ---- discipline ---------------------------------------------------
    'trading_authorization': 'NO',
    'breakout_day_entry_note': 'ENTRY_BD books at the breakout day CLOSE '
                               '(the earliest session on which the breakout '
                               'is observable); ENTRY_BD1 books at the next '
                               'close.  Both are reported, never mixed.',
}

Q_IS = (2019, 2022)
Q_VALID = (2023, 2024)
Q_OOS = (2025, 2026)


def phase_of_year(y):
    if Q_IS[0] <= y <= Q_IS[1]:
        return 'IS'
    if Q_VALID[0] <= y <= Q_VALID[1]:
        return 'VALID'
    if Q_OOS[0] <= y <= Q_OOS[1]:
        return 'OOS'
    return 'PRE' if y < Q_IS[0] else 'OUT'


# =====================================================================
# logging
# =====================================================================
class Log(object):
    def __init__(self, name):
        self.name = name
        self.path = os.path.join(OUT, '%s.log' % name)
        self.t0 = time.time()
        # Truncate once per process: an append-only log silently mixes the
        # output of consecutive runs, which makes "the latest numbers" a
        # reading exercise.  Later calls in the same process still append.
        with open(self.path, 'w', encoding='utf-8'):
            pass

    def __call__(self, msg):
        line = '[%7.1fs] %s' % (time.time() - self.t0, msg)
        print(line)
        with open(self.path, 'a', encoding='utf-8') as f:
            f.write(line + '\n')


# =====================================================================
# grid construction
# =====================================================================
def _board_of(code):
    c = code[:3]
    if c in ('688', '689'):
        return 'STAR'
    if c in ('300', '301'):
        return 'GEM'
    if code[:2] in ('43', '83', '87', '88', '92') or c in ('430', '830'):
        return 'BSE'
    return 'MAIN'


def _roll_mean(M, w, min_frac=0.8):
    """Trailing mean over the last w SESSIONS.  Time is axis 1.

    The frame is built from M.T so that pandas rolls along the time axis; a
    frame built from M would roll along the stock axis and turn every ratio
    indicator into a cross-sectional statistic.
    """
    d = pd.DataFrame(np.asarray(M).T)
    return d.rolling(w, min_periods=max(2, int(w * min_frac))).mean(
    ).to_numpy().T


def _roll_max_prev(M, w):
    """trailing w-day high EXCLUDING today (i.e. 前w日最高).  Time is axis 1."""
    d = pd.DataFrame(np.asarray(M).T)
    return d.shift(1).rolling(w, min_periods=w).max().to_numpy().T


def _roll_min_prev(M, w):
    """trailing w-day low EXCLUDING today.  Time is axis 1."""
    d = pd.DataFrame(np.asarray(M).T)
    return d.shift(1).rolling(w, min_periods=w).min().to_numpy().T


def _roll_pct_rank(M, w, lg=None, tag=''):
    """PIT percentile rank of M[:, t] inside M[:, t-w+1 .. t] (0-1).

    NaN cells get NaN.  Loop over t with a vectorised comparison; the full
    (S, N, w) broadcast would need ~2.7 GB, so we go day by day.
    """
    S, N = M.shape
    out = np.full((S, N), np.nan, dtype='float32')
    V = np.nan_to_num(M, nan=np.nan)
    for t in range(N):
        a = max(0, t - w + 1)
        blk = V[:, a:t + 1]
        cur = V[:, t]
        cnt = np.isfinite(blk).sum(axis=1)
        le = (blk <= cur[:, None]).sum(axis=1)
        ok = (cnt >= w * 0.8) & np.isfinite(cur)
        r = np.full(S, np.nan)
        r[ok] = le[ok] / cnt[ok]
        out[:, t] = r
    if lg and (N % 500 == 0):
        lg('  pct-rank %s done (%d days)' % (tag, N))
    return out


def build_grid(lg=None, use_cache=True):
    """Wide per-stock matrices over the trading calendar.

    Returns dict:
      dates (N,) 'YYYYMMDD' str
      codes (S,) ts_code
      open/high/low/close  (S,N) float32   qfq (adjusted) OHLC
      vol amount           (S,N) float32   raw share / value
      turnover             (S,N) float32   turnover_rate (%)
      pct_chg              (S,N) float32   close/pre_close - 1 (raw)
      traded               (S,N) bool      a real print exists that day
      st / delist          (S,N) bool      PIT name-based flags
      limup limdn oneword  (S,N) bool      board-aware limit flags
      board                (S,)  str
      d2i / s2i            dicts
    """
    if use_cache and os.path.exists(GRID_CACHE):
        z = np.load(GRID_CACHE, allow_pickle=True)
        if ('last_idx' not in z.files or '_v' not in z.files or
                int(z['_v']) != CACHE_VERSION):   # stale cache -> rebuild
            z.close()
        else:
            g = {k: z[k] for k in z.files}
            g['dates'] = g['dates'].astype(str)
            g['codes'] = g['codes'].astype(str)
            g['board'] = g['board'].astype(str)
            g['d2i'] = {d: i for i, d in enumerate(g['dates'])}
            g['s2i'] = {c: i for i, c in enumerate(g['codes'])}
            if lg:
                lg('grid loaded from cache  S=%d N=%d' % g['close'].shape)
            return g

    t0 = time.time()
    cal = load_calendar().sort_values('idx').reset_index(drop=True)
    dates = cal['trade_date'].astype(str).to_numpy()
    N = len(dates)
    d2i = {d: i for i, d in enumerate(dates)}

    cols = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close',
            'pre_close', 'vol', 'amount', 'qfq_open', 'qfq_high',
            'qfq_low', 'qfq_close']
    p = pd.read_parquet(os.path.join(FS_DATA, 'price_panel.parquet'),
                        columns=cols)
    p['ts_code'] = p['ts_code'].astype(str)
    p['trade_date'] = p['trade_date'].astype(str)
    p = p[p['trade_date'].isin(d2i)]
    codes = np.sort(p['ts_code'].unique())
    s2i = {c: i for i, c in enumerate(codes)}
    S = len(codes)
    if lg:
        lg('price panel %d rows -> S=%d N=%d (%.1fs)'
           % (len(p), S, N, time.time() - t0))

    si = p['ts_code'].map(s2i).to_numpy()
    di = p['trade_date'].map(d2i).to_numpy().astype('int64')

    def scatter(col):
        M = np.full((S, N), np.nan, dtype='float32')
        M[si, di] = p[col].to_numpy(dtype='float64')
        return M

    open_ = scatter('qfq_open')
    high = scatter('qfq_high')
    low = scatter('qfq_low')
    close = scatter('qfq_close')
    traded = ~np.isnan(close)
    vol = np.nan_to_num(scatter('vol'), nan=0.0)
    amount = np.nan_to_num(scatter('amount'), nan=0.0)
    rclose = scatter('close')
    pre_close = scatter('pre_close')
    pct_chg = np.where((rclose > 0) & (pre_close > 0),
                       rclose / pre_close - 1.0, np.nan).astype('float32')
    if lg:
        lg('scattered OHLCV (%.1fs)' % (time.time() - t0))

    # ---- turnover (daily_basic) ---------------------------------------
    b = pd.read_parquet(os.path.join(FS_DATA, 'basic_panel.parquet'),
                        columns=['ts_code', 'trade_date', 'turnover_rate'])
    b['ts_code'] = b['ts_code'].astype(str)
    b['trade_date'] = b['trade_date'].astype(str)
    b = b[b['trade_date'].isin(d2i) & b['ts_code'].isin(s2i)]
    turnover = np.full((S, N), np.nan, dtype='float32')
    turnover[b['ts_code'].map(s2i).to_numpy(),
             b['trade_date'].map(d2i).to_numpy().astype('int64')] = \
        b['turnover_rate'].to_numpy(dtype='float64')

    # ---- ST / delisting (PIT namechange) ------------------------------
    st = np.zeros((S, N), dtype=bool)
    delist = np.zeros((S, N), dtype=bool)
    nchg = 0
    for f in os.listdir(CD):
        if not (f.startswith('treasure_namechg') and f.endswith('.parquet')):
            continue
        try:
            d = pd.read_parquet(os.path.join(CD, f),
                                columns=['ts_code', 'name', 'start_date',
                                         'end_date'])
        except Exception:
            continue
        nchg += 1
        for r in d.itertuples(index=False):
            s = s2i.get(str(r.ts_code))
            if s is None:
                continue
            a = str(r.start_date) if r.start_date is not None else '19000101'
            bnd = str(r.end_date) if r.end_date == r.end_date else '29991231'
            i0 = int(np.searchsorted(dates, a, side='left'))
            i1 = int(np.searchsorted(dates, bnd, side='right')) - 1
            if i1 < i0:
                continue
            nm = str(r.name)
            if 'ST' in nm.upper():
                st[s, i0:i1 + 1] = True
            if '退' in nm:
                delist[s, i0:i1 + 1] = True
    if lg:
        lg('namechange files %d  ST cells %.4f  delist cells %.4f'
           % (nchg, float(st.mean()), float(delist.mean())))

    # ---- board-aware limit flags --------------------------------------
    board = np.array([_board_of(c) for c in codes])
    lim = np.where((board == 'STAR') | (board == 'GEM'), 0.20,
                   np.where(board == 'BSE', 0.30, 0.10)).astype('float64')
    st_main = st & (board == 'MAIN')[:, None]
    lim2 = np.where(st_main, 0.05, lim[:, None])
    validpx = (pre_close > 0) & (rclose > 0)
    limpx = np.round(pre_close * (1.0 + lim2), 2)
    limdn_px = np.round(pre_close * (1.0 - lim2), 2)
    limup = validpx & (rclose >= limpx - 0.005)
    limdn = validpx & (rclose <= limdn_px + 0.005)
    oneword = limup & (np.abs(high - low) < 1e-9)
    del pre_close, rclose, limpx, limdn_px, validpx

    # ---- listing age (sessions since first print) ---------------------
    first = np.argmax(traded, axis=1)
    first = np.where(traded.any(axis=1), first, N)

    # ---- terminal trading (survivorship audit, spec section 33) -------
    # The Tushare cache carries no exchange delist_date and the namechange
    # cache contains no '退' name, so `delist` above can only ever flag the
    # ST part.  Record every stock whose last print falls well before the
    # panel end: its forward windows are truncated, and that truncation is
    # reported instead of being silently applied as a filter.
    lastx = np.argmax(traded[:, ::-1], axis=1)
    last_idx = np.where(traded.any(axis=1), N - 1 - lastx, -1)
    gone = (last_idx >= 0) & (last_idx < N - 1 - PREREG['long_suspension_days'])
    if lg:
        lg('terminal-print audit: %d/%d codes stop >%dd early '
           '(ST cells %.4f from namechange, delist cells %.4f)'
           % (int(gone.sum()), S, PREREG['long_suspension_days'],
              float(st.mean()), float(delist.mean())))

    g = {'dates': dates, 'codes': codes, 'open': open_, 'high': high,
         'low': low, 'close': close, 'vol': vol, 'amount': amount,
         'turnover': turnover, 'pct_chg': pct_chg, 'traded': traded,
         'st': st, 'delist': delist, 'limup': limup, 'limdn': limdn,
         'oneword': oneword, 'board': board, 'first_idx': first,
         'last_idx': last_idx, 'gone': gone,
         'd2i': d2i, 's2i': s2i, '_v': np.array(CACHE_VERSION)}
    np.savez(GRID_CACHE, **g)
    with open(GRID_META, 'w', encoding='utf-8') as f:
        json.dump({'S': int(S), 'N': int(N), 'dates': [str(dates[0]),
                                                       str(dates[-1])],
                   'n_st_cells': int(st.sum()),
                   'n_delist_cells': int(delist.sum()),
                   'delist_status': 'DELIST_PIT_UNAVAILABLE',
                   'n_codes_gone': int(gone.sum()),
                   'n_limup': int(limup.sum()),
                   'n_limdn': int(limdn.sum()),
                   'built_sec': round(time.time() - t0, 1)}, f,
                  ensure_ascii=False, indent=1)
    if lg:
        lg('grid built & cached (%.1fs)' % (time.time() - t0))
    return g


# ---------------------------------------------------------------- indicators
def build_indicators(g, lg=None, use_cache=True):
    """PIT volume-shock indicators + price descriptors.

    Every entry uses only information available at the close of day t.
    The percentile-rank passes cost ~2 min, so the result is cached next to
    the grid; the cache is keyed on the indicator name list and rebuilt
    whenever PREREG['volume_indicators'] changes.
    """
    want = set(PREREG['volume_indicators'])
    if use_cache and os.path.exists(IND_CACHE):
        z = np.load(IND_CACHE, allow_pickle=True)
        have = {k[2:] for k in z.files if k.startswith('I_')}
        if (want <= have and '_v' in z.files and
                int(z['_v']) == CACHE_VERSION):
            ind = {k[2:]: z[k] for k in z.files if k.startswith('I_')}
            px = {k[2:]: z[k] for k in z.files if k.startswith('P_')}
            ma = {int(k[2:]): z[k] for k in z.files if k.startswith('M_')}
            z.close()
            if lg:
                lg('indicators loaded from cache  %d ind / %d px'
                   % (len(ind), len(px)))
            return ind, px, ma
        z.close()

    vol, amount, turn = g['vol'], g['amount'], g['turnover']
    close = g['close']
    t0 = time.time()
    ma = {}
    for w in PREREG['ma_window']:
        ma[w] = _roll_mean(close, w)
    ind = {}
    for w in (5, 10, 20, 60):
        m = _roll_mean(vol, w)
        ind['VR%d' % w] = np.where(m > 0, vol / m, np.nan).astype('float32')
    m20, m60 = _roll_mean(vol, 20), _roll_mean(vol, 60)
    ind['VOL_MA20'] = np.where(m20 > 0, vol / m20, np.nan).astype('float32')
    ind['VOL_MA60'] = np.where(m60 > 0, vol / m60, np.nan).astype('float32')
    a20 = _roll_mean(amount, 20)
    ind['AMT_MA20'] = np.where(a20 > 0, amount / a20, np.nan).astype('float32')
    ind['TURN'] = np.where(m20 > 0, turn / _roll_mean(turn, 20),
                           np.nan).astype('float32')
    if lg:
        lg('ratio indicators done (%.1fs)' % (time.time() - t0))
    w = PREREG['pct_rank_window']
    ind['VOL_PCT'] = _roll_pct_rank(vol, w, lg, 'VOL_PCT')
    ind['AMT_PCT'] = _roll_pct_rank(amount, w, lg, 'AMT_PCT')
    ind['TURN_PCT'] = _roll_pct_rank(turn, w, lg, 'TURN_PCT')
    if lg:
        lg('percentile indicators done (%.1fs)' % (time.time() - t0))

    px = {}
    px['ma20'] = ma[20]
    px['ma60'] = ma[60]
    # ma20 / ma60 / hi20 / hi60 stay float64: they are compared against a
    # price level, and that comparison must not be decided by a float32
    # rounding step.  The pure descriptors are stored float32 -- the grid
    # has 12.3M cells so every float64 array costs 98 MB, which matters on
    # a box whose free physical memory is under 2 GB.
    px['ma20_slope5'] = np.where(
        ma[20].shape == close.shape,
        (ma[20] - np.roll(ma[20], 5, axis=1)) / 5.0,
        np.nan).astype('float32')
    px['ma20_slope5'][:, :5] = np.nan
    hl = g['high'] - g['low']
    px['clv'] = np.where(hl > 0, (close - g['low']) / hl,
                         np.nan).astype('float32')
    pre = np.roll(close, 1, axis=1)
    pre[:, 0] = np.nan
    px['body'] = np.where(hl > 0, (close - g['open']) / hl,
                          np.nan).astype('float32')
    px['r1'] = np.where(pre > 0, close / pre - 1.0,
                        np.nan).astype('float32')
    px['hi20'] = _roll_max_prev(g['high'], 20)
    px['hi60'] = _roll_max_prev(g['high'], 60)
    px['lo20'] = _roll_min_prev(g['low'], 20).astype('float32')
    px['r20'] = np.where(np.roll(close, 20, axis=1) > 0,
                         close / np.roll(close, 20, axis=1) - 1.0,
                         np.nan).astype('float32')
    px['r60'] = np.where(np.roll(close, 60, axis=1) > 0,
                         close / np.roll(close, 60, axis=1) - 1.0,
                         np.nan).astype('float32')
    del hl, pre
    if use_cache:
        blob = {}
        for k, v in ind.items():
            blob['I_' + k] = v
        for k, v in px.items():
            blob['P_' + k] = v
        for k, v in ma.items():
            blob['M_%d' % k] = v
        blob['_v'] = np.array(CACHE_VERSION)
        np.savez(IND_CACHE, **blob)
        with open(IND_META, 'w', encoding='utf-8') as f:
            json.dump({'indicators': sorted(ind), 'price': sorted(px),
                       'ma': sorted(ma)}, f, ensure_ascii=False, indent=1)
        if lg:
            lg('indicators cached (%.1fs)' % (time.time() - t0))
    return ind, px, ma


def eligibility(g):
    """(S,N) bool: tradable, non-ST, non-delisting, aged, active."""
    ok = g['traded'].copy()
    if PREREG['st_excluded']:
        ok &= ~g['st']
    if PREREG['delist_excluded']:
        ok &= ~g['delist']
    first = g['first_idx']
    idx = np.arange(g['close'].shape[1])[None, :]
    ok &= (idx - first[:, None]) >= PREREG['min_list_days']
    # break after a long suspension hole (>=60 consecutive missing prints)
    miss = (~g['traded']).astype('int32')
    run = np.zeros_like(miss)
    c = np.zeros(miss.shape[0], dtype='int32')
    for j in range(miss.shape[1]):
        c = np.where(miss[:, j] > 0, c + 1, 0)
        run[:, j] = c
    bad = np.zeros_like(ok)
    for k in range(1, PREREG['long_suspension_days']):
        bad[:, k:] |= (run[:, :-k] >= k)
    return ok & ~bad


# ---------------------------------------------------------------- clustering
def cluster_events(mask, gap):
    """(S,N) bool -> (S,N) bool keeping the FIRST cell of each cluster.

    A cluster chains consecutive True cells whose gap (in sessions) is
    <= gap, exactly as in cluster_events_ranked: the distance is measured
    against the PREVIOUS True cell, not against the cluster's first cell
    (comparing against the first cell would split 0/4/8/12 apart at gap=5
    while the ranked variant would merge them -- the two anchors must
    disagree only about WHICH cell represents a cluster, never about how
    many clusters exist).
    """
    S, N = mask.shape
    out = np.zeros_like(mask)
    for s in range(S):
        r = np.flatnonzero(mask[s])
        if len(r) == 0:
            continue
        out[s, r[0]] = True
        prev = r[0]
        for v in r[1:]:
            if v - prev > gap:
                out[s, v] = True
            prev = v
    return out


def cluster_events_ranked(mask, gap, rank):
    """(S,N) bool + (S,N) float rank -> one representative per cluster,
    the cell with the largest rank inside the cluster (ties -> earliest)."""
    S, N = mask.shape
    out = np.zeros_like(mask)
    for s in range(S):
        r = np.flatnonzero(mask[s])
        if len(r) == 0:
            continue
        groups, cur = [], [r[0]]
        for v in r[1:]:
            if v - cur[-1] <= gap:
                cur.append(v)
            else:
                groups.append(cur)
                cur = [v]
        groups.append(cur)
        for gp in groups:
            rv = rank[s, gp]
            rv = np.where(np.isfinite(rv), rv, -np.inf)
            j = gp[int(np.argmax(rv))] if np.isfinite(rv).any() else gp[0]
            out[s, j] = True
    return out


# ---------------------------------------------------------------- event table
def events_from_mask(g, mask, extra=None):
    """(S,N) bool -> event table with ts_code / s_i / t0 / t0_date."""
    si, di = np.nonzero(mask)
    df = pd.DataFrame({'s_i': si.astype('int32'), 't0': di.astype('int32')})
    df['ts_code'] = g['codes'][df['s_i'].to_numpy()]
    df['t0_date'] = g['dates'][df['t0'].to_numpy()]
    df['year'] = df['t0_date'].str[:4].astype(int)
    df['board'] = g['board'][df['s_i'].to_numpy()]
    df = df.sort_values(['t0', 'ts_code']).reset_index(drop=True)
    if extra:
        for k, M in extra.items():
            df[k] = M[df['s_i'].to_numpy(), df['t0'].to_numpy()]
    return df


def fwd_ret(g, si, di, h, field='close', base='close'):
    """return from the CLOSE of di to the CLOSE of di+h (NaN if off-grid).

    field='open' -> exit at the open; base='open' -> enter at the open of di.
    """
    N = g['close'].shape[1]
    a = np.asarray(di)
    b = a + int(h)
    ok = (a >= 0) & (a <= N - 1) & (b >= 0) & (b <= N - 1)
    aa = np.clip(a, 0, N - 1)
    bb = np.clip(b, 0, N - 1)
    p0 = g[base][si, aa].astype('float64')
    p1 = g[field][si, bb].astype('float64')
    r = np.where((p0 > 0) & np.isfinite(p1), p1 / p0 - 1.0, np.nan)
    return np.where(ok, r, np.nan)


def path_metrics(g, si, di, wins=(1, 3, 5, 10, 20, 40, 60), chunk=40000):
    """forward return grid + MFE / MAE / max-drawdown / volume evolution.

    Entry base = the event-day close.  MFE/MAE use the intraday high/low of
    sessions di+1 .. di+path_horizons[-1], never the entry day.  Processed in
    chunks so a 500k-event set never materialises a (E, 2120) matrix.
    """
    N = g['close'].shape[1]
    si = np.asarray(si)
    di = np.asarray(di)
    E = len(si)
    out = {}
    for h in wins:
        out['ret%d' % h] = fwd_ret(g, si, di, h)
    K = PREREG['path_horizons'][-1]
    for k in ('mfe', 'mae', 'maxdd', 'mfe20', 'mae20', 'vol5_ratio',
              'vol_min_ratio', 'vol_max_ratio'):
        out[k] = np.full(E, np.nan, dtype='float64')
    off = np.arange(1, K + 1)
    off20 = np.arange(1, PREREG['mfe_mae_window'] + 1)
    for a in range(0, E, chunk):
        b = min(E, a + chunk)
        s_, d_ = si[a:b], di[a:b]
        base = g['close'][s_, d_].astype('float64')
        cols = d_[:, None] + off[None, :]
        okw = cols <= N - 1
        cc = np.clip(cols, 0, N - 1)
        hi = g['high'][s_[:, None], cc].astype('float64')
        lo = g['low'][s_[:, None], cc].astype('float64')
        cl = g['close'][s_[:, None], cc].astype('float64')
        vv = g['vol'][s_[:, None], cc].astype('float64')
        hi = np.where(okw, hi, np.nan)
        lo = np.where(okw, lo, np.nan)
        cl = np.where(okw, cl, np.nan)
        vv = np.where(okw, vv, np.nan)
        up = hi / base[:, None] - 1.0
        dn = lo / base[:, None] - 1.0
        cr = cl / base[:, None] - 1.0
        out['mfe'][a:b] = np.nanmax(np.where(np.isfinite(up), up, -np.inf),
                                    axis=1)
        out['mae'][a:b] = np.nanmin(np.where(np.isfinite(dn), dn, np.inf),
                                    axis=1)
        run = np.maximum.accumulate(
            np.where(np.isfinite(cr), cr, -np.inf), axis=1)
        out['maxdd'][a:b] = np.nanmin(np.where(np.isfinite(cr), cr - run,
                                               np.nan), axis=1)
        w20 = off20.size
        out['mfe20'][a:b] = np.nanmax(
            np.where(np.isfinite(up[:, :w20]), up[:, :w20], -np.inf), axis=1)
        out['mae20'][a:b] = np.nanmin(
            np.where(np.isfinite(dn[:, :w20]), dn[:, :w20], np.inf), axis=1)
        hv = np.where(vv[:, 0] > 0, vv[:, 0], np.nan)
        with np.errstate(invalid='ignore'):
            out['vol5_ratio'][a:b] = np.nanmean(vv[:, :5], axis=1) / hv
            out['vol_min_ratio'][a:b] = np.nanmin(vv, axis=1) / hv
            out['vol_max_ratio'][a:b] = np.nanmax(vv, axis=1) / hv
    for k in ('mfe', 'mae', 'maxdd', 'mfe20', 'mae20'):
        out[k] = np.where(np.isfinite(out[k]), out[k], np.nan)
    return out


# ------------------------------------------------------- forward matrices
def fwd_matrix(g, h, field='close', base='close'):
    """(S,N) forward return from the close of t to the close of t+h.

    Vectorised twin of fwd_ret: for a large scan (hundreds of candidate
    definitions) pre-computing the whole surface once and then indexing it
    with a boolean mask is orders of magnitude cheaper than gathering the
    window per candidate.  Outcome only -- never used as an input.
    """
    p0 = g[base].astype('float64')
    p1 = g[field].astype('float64')
    out = np.full(p0.shape, np.nan, dtype='float64')
    h = int(h)
    if h > 0:
        out[:, :-h] = np.where((p0[:, :-h] > 0) & (p1[:, h:] > 0),
                               p1[:, h:] / p0[:, :-h] - 1.0, np.nan)
    elif h < 0:
        out[:, -h:] = np.where((p0[:, -h:] > 0) & (p1[:, :h] > 0),
                               p1[:, :h] / p0[:, -h:] - 1.0, np.nan)
    else:
        out[:] = 0.0
    out[~np.isfinite(p0)] = np.nan
    return out


def mfe_mae_matrix(g, k):
    """(S,N) MFE and MAE over the k sessions AFTER t (never including t)."""
    hi = g['high'].astype('float64')
    lo = g['low'].astype('float64')
    base = g['close'].astype('float64')
    S, N = base.shape
    mx = np.full((S, N), -np.inf)
    mn = np.full((S, N), np.inf)
    for j in range(1, int(k) + 1):
        sh = np.full((S, N), np.nan)
        sh[:, :-j] = hi[:, j:]
        mx = np.fmax(mx, sh)
        sh = np.full((S, N), np.nan)
        sh[:, :-j] = lo[:, j:]
        mn = np.fmin(mn, sh)
    mfe = np.where(np.isfinite(mx) & (base > 0), mx / base - 1.0, np.nan)
    mae = np.where(np.isfinite(mn) & (base > 0), mn / base - 1.0, np.nan)
    return mfe, mae


def mfe_mae_at(g, si, di, k, chunk=40000):
    """MFE / MAE over the k sessions AFTER di (the reference session excluded).

    Per-event twin of mfe_mae_matrix.  The full (S,N) surface costs 2 x 98 MB,
    so the per-event gather is offered as a first-class primitive instead of
    being rebuilt inside every module that needs an entry-day risk profile.
    """
    N = g['close'].shape[1]
    si = np.asarray(si)
    di = np.asarray(di)
    E = len(si)
    mfe = np.full(E, np.nan, dtype='float64')
    mae = np.full(E, np.nan, dtype='float64')
    off = np.arange(1, int(k) + 1)
    for a in range(0, E, chunk):
        b = min(E, a + chunk)
        s_, d_ = si[a:b], di[a:b]
        base = g['close'][s_, d_].astype('float64')
        cols = d_[:, None] + off[None, :]
        okw = cols <= N - 1
        cc = np.clip(cols, 0, N - 1)
        hi = np.where(okw, g['high'][s_[:, None], cc].astype('float64'), np.nan)
        lo = np.where(okw, g['low'][s_[:, None], cc].astype('float64'), np.nan)
        b_ = np.where(base > 0, base, np.nan)[:, None]
        with np.errstate(invalid='ignore'):
            up = hi / b_ - 1.0
            dn = lo / b_ - 1.0
        hi_m = np.nanmax(np.where(np.isfinite(up), up, -np.inf), axis=1)
        lo_m = np.nanmin(np.where(np.isfinite(dn), dn, np.inf), axis=1)
        mfe[a:b] = np.where(np.isfinite(hi_m), hi_m, np.nan)
        mae[a:b] = np.where(np.isfinite(lo_m), lo_m, np.nan)
    return mfe, mae


def shift_fwd(M, j):
    """shift row-wise so cell t holds M[:, t+j] (NaN past the edge)."""
    out = np.full(M.shape, np.nan, dtype=M.dtype)
    if j > 0:
        out[:, :-j] = M[:, j:]
    elif j < 0:
        out[:, -j:] = M[:, :j]
    else:
        out[:] = M
    return out


# ---------------------------------------------------------------- statistics
def perf_stats(r, cost_bp=0.0):
    """Single-leg performance ledger (spec section 20)."""
    r = pd.Series(np.asarray(r, dtype='float64')).replace(
        [np.inf, -np.inf], np.nan).dropna().to_numpy()
    n = len(r)
    d = {'n': n}
    if n == 0:
        # the degenerate branch must return the SAME key set as the normal
        # one: an extreme pre-registered threshold can legitimately select
        # nothing, and a caller reading d['tail_share'] must not blow up
        # only on the candidates that happened to be empty.
        for k in ('win_rate', 'mean', 'median', 'mean_win', 'mean_loss',
                  'pf', 'expectancy', 'std', 't', 'p05', 'p95',
                  'tail_share'):
            d[k] = np.nan
        return d
    net = r - cost_bp / 10000.0
    w = net[net > 0]
    l = net[net <= 0]
    d['win_rate'] = float(len(w)) / n
    d['mean'] = float(net.mean())
    d['median'] = float(np.median(net))
    d['mean_win'] = float(w.mean()) if len(w) else np.nan
    d['mean_loss'] = float(l.mean()) if len(l) else np.nan
    gp, gl = float(w.sum()), float(-l.sum())
    d['pf'] = (gp / gl) if gl > 0 else np.inf
    d['expectancy'] = d['mean']
    d['std'] = float(net.std(ddof=1)) if n > 1 else np.nan
    d['t'] = (d['mean'] / (d['std'] / np.sqrt(n))
              if n > 1 and d['std'] > 0 else np.nan)
    d['p05'] = float(np.percentile(net, 5))
    d['p95'] = float(np.percentile(net, 95))
    d['tail_share'] = (float(np.sort(net)[-max(1, int(n * 0.05)):].sum())
                       / max(1e-9, gp))
    return d


def perf_row(tag, r, cost_bp=0.0, **kw):
    d = perf_stats(r, cost_bp)
    d['tag'] = tag
    for k, v in kw.items():
        d[k] = v
    return d


# ------------------------------------------- shared post-event primitives
def win_post(M, si, di, K):
    """(E,K) values of M at t0+1 .. t0+K, plus the validity mask.

    Columns are sessions t0+1 .. t0+K in that order, so column index h-1 is
    session t0+h.  Cells past the panel edge are NaN and are excluded from
    every statistic downstream.  One definition for every post-event read in
    the study, so the convention cannot drift between modules.
    """
    N = M.shape[1]
    off = np.arange(1, int(K) + 1)
    cols = np.asarray(di)[:, None] + off[None, :]
    ok = cols <= N - 1
    v = np.asarray(M)[np.asarray(si)[:, None], np.clip(cols, 0, N - 1)]
    return np.where(ok, v, np.nan).astype('float64'), ok


def contraction_hit(M, D, thr):
    """'D consecutive columns <= thr' -> (flag, first_end).

    first_end is the 1-based position inside the window of the LAST session
    of the first qualifying run, or 0 when no run exists.  It is what lets a
    contraction be ordered against a later event instead of merely being
    co-located with it.
    """
    E, K = M.shape
    if D > K:
        return np.zeros(E, dtype=bool), np.zeros(E, dtype='int32')
    mn = np.full((E, K - D + 1), np.inf)
    for j in range(D):
        seg = M[:, j:K - D + 1 + j]
        mn = np.fmin(mn, np.where(np.isfinite(seg), seg, np.inf))
    ok = mn <= thr
    flag = ok.any(axis=1)
    first = np.where(flag, np.argmax(ok, axis=1) + D, 0).astype('int32')
    return flag, first


def ledger_block(tag, section, sel, col, yr, **meta):
    """One aggregate row: the section-20 ledger for the selected events.

    `sel` and `col` are full-length event arrays; the phase masks are applied
    to the selected subset (masking the full-length phase array with a
    subset index would silently mis-align the rows).  Rows that build their
    label from a window overlapping the outcome must say so: pass
    lookahead='label_window_overlaps_outcome' in `meta`.
    """
    idx = np.flatnonzero(np.asarray(sel))
    r = np.asarray(col, dtype='float64')[idx]
    y = np.asarray(yr)[idx]
    s0 = perf_stats(r, 0)
    s30 = perf_stats(r, PREREG['primary_cost_bp'])
    d = {'section': section, 'tag': tag, 'n_events': int(len(r))}
    d.update(meta)
    for k in ('mean', 'median', 'win_rate', 'pf', 'tail_share', 'mean_win',
              'mean_loss', 't', 'p95'):
        d['r_' + k] = s0[k]
    d['r_mean_net30'] = s30['mean']
    d['r_pf_net30'] = s30['pf']
    for ph, (a, b) in (('IS', Q_IS), ('VALID', Q_VALID), ('OOS', Q_OOS)):
        m = (y >= a) & (y <= b)
        d['n_' + ph] = int(m.sum())
        d['mean_' + ph] = float(np.nanmean(r[m])) if m.any() else np.nan
    return d


def ic_stats(x, lag):
    """mean, Newey-West(Bartlett) t, ICIR, n  -- corrected estimator."""
    x = pd.Series(x).dropna().astype(float)
    if len(x) < 5:
        return np.nan, np.nan, np.nan, len(x)
    d = x.to_numpy(dtype=float)
    n = len(d)
    mu = float(d.mean())
    d = d - mu
    L = int(min(lag, n - 1))
    om = float(d @ d) / n
    for i in range(1, L + 1):
        om += 2.0 * (1.0 - i / (L + 1.0)) * float(d[i:] @ d[:-i]) / n
    var = om / n
    t = mu / np.sqrt(var) if var > 0 else np.nan
    sd = float(d.std(ddof=1))
    return mu, float(t), (mu / sd if sd > 0 else np.nan), n


def bh_fdr(pvals):
    p = pd.Series(pvals, dtype=float)
    m = p.notna()
    v = p[m].to_numpy()
    n = len(v)
    if n == 0:
        return p
    order = np.argsort(v)
    ranked = v[order]
    adj = ranked * n / (np.arange(n) + 1.0)
    adj = np.minimum.accumulate(adj[::-1])[::-1]
    out = np.empty(n)
    out[order] = np.clip(adj, 0, 1)
    r = pd.Series(np.nan, index=p.index, dtype=float)
    r[m] = out
    return r


def xsz(v, day):
    v = pd.to_numeric(pd.Series(v).reset_index(drop=True), errors='coerce')
    g = v.groupby(pd.Series(np.asarray(day)))
    return g.transform(
        lambda x: (x - x.mean()) / x.std() if x.std() else 0.0).to_numpy()


def winsor_xs(df, col, by, lo=0.01, hi=0.99):
    g = df.groupby(by)[col]
    ql, qh = g.transform('quantile', lo), g.transform('quantile', hi)
    return df[col].clip(ql, qh)


def spearman_with_p(pred, actual, min_n=15):
    pred, actual = pd.Series(pred, dtype=float), pd.Series(actual, dtype=float)
    m = pred.notna() & actual.notna() & np.isfinite(pred) & np.isfinite(actual)
    n = int(m.sum())
    if n < min_n:
        return np.nan, np.nan, n
    r = pred[m].corr(actual[m], method='spearman')
    if not np.isfinite(r) or abs(r) >= 1:
        return r, np.nan, n
    t = r * np.sqrt((n - 2) / max(1e-12, 1 - r * r))
    from math import erf
    p = 2 * (1 - 0.5 * (1 + erf(abs(t) / np.sqrt(2))))
    return float(r), float(p), n


def save_csv(df, name):
    p = os.path.join(DATA, name)
    df.to_csv(p, index=False, encoding='utf-8-sig')
    return p


def save_json(obj, name):
    p = os.path.join(DATA, name)
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, default=str)
    return p


if __name__ == '__main__':
    lg = Log('hve_common_selftest')
    lg('PREREG %s v%s' % (PREREG['hypothesis_id'], PREREG['version']))
    g = build_grid(lg=lg, use_cache=True)
    lg('S=%d N=%d %s..%s' % (g['close'].shape + (g['dates'][0],
                                                  g['dates'][-1])))
    ind, px, ma = build_indicators(g, lg=lg)
    el = eligibility(g)
    lg('eligibility cells %.4f' % float(el.mean()))
    for k in ('VR20', 'AMT_MA20', 'AMT_PCT', 'TURN_PCT'):
        v = ind[k][el & np.isfinite(ind[k])]
        lg('  %-9s median %.3f  p90 %.3f  p99 %.3f'
           % (k, float(np.median(v)), float(np.percentile(v, 90)),
              float(np.percentile(v, 99))))
    m = (ind['VR20'] >= 2.0) & el & np.isfinite(px['r1'])
    lg('raw VR20>=2 events %d' % int(m.sum()))
    m2 = cluster_events(m, 5)
    lg('after cluster(gap=5) events %d' % int(m2.sum()))
    ev = events_from_mask(g, m2)
    pm = path_metrics(g, ev['s_i'].to_numpy(), ev['t0'].to_numpy())
    lg('path ret10 median %.4f  mfe %.4f  mae %.4f'
       % (float(np.nanmedian(pm['ret10'])), float(np.nanmedian(pm['mfe'])),
          float(np.nanmedian(pm['mae']))))
    lg('perf %s' % str({k: round(v, 4) for k, v in
                        perf_stats(pm['ret10']).items()
                        if isinstance(v, float)}))
    lg('selftest OK')
