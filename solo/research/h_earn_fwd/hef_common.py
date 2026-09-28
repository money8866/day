# -*- coding: utf-8 -*-
"""H-EARN-FWD common: preregistered constants, loaders, statistics.

Hypothesis H-EARN-FWD: next-quarter earnings surprise forward prediction.
All parameters preregistered here BEFORE any result was observed.
Data availability note: market panels start 2018-01-02 -> anchor year 2018
is BURN-IN (features incomplete); TRAIN uses 2019-2022.
"""
import os, sys, json, time, glob
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CD = r'D:\mystock\cache_daily'
PQ = os.path.join(CD, 'parquet')
FS_DATA = os.path.join(HERE, '..', 'fundamental_surprise_alpha', 'data')
OUT = os.path.join(HERE)
DATA = os.path.join(OUT, 'data')
os.makedirs(DATA, exist_ok=True)

# ---------------------------------------------------------------- PREREG
PREREG = {
    'hypothesis_id': 'H-EARN-FWD',
    'created': '2026-09-26',
    'version': 'V1.0',
    # ---- prediction points (trading days before the target-quarter
    #      earnings announcement anchor).  primary study: P60/P30/P20.
    'prediction_offsets': (60, 30, 20),
    'offset_neighborhood': (45, 10),
    'primary_offset': 30,
    'anchor_rule': 'actual f_ann_date of target report (hindsight anchor; '
                   'features strictly PIT). Robustness: prior-year-same-'
                   'quarter anchor as parameter neighborhood.',
    # ---- targets
    'target_a': 'model_surprise_pct',
    'target_b': 'positive_surprise',
    'target_c': 'strong_surprise',
    'strong_q': 0.75,
    'surprise_denom_floor': 0.20,   # denom = max(|Expected|, floor*med|NP| last 8 q)
    'winsor': (0.01, 0.99),
    # ---- expected-earnings benchmarks (preregistered before modeling)
    'expect_seasonal': 'NP[t-4] * (1 + median(last 4 yoy))',       # = N3
    'expect_momentum': 'NP[t-4] * (1 + last yoy)',                 # = N5
    'aux_expected': ('IMPLIED_CONS_FY', 'GUIDE_MIDPOINT'),
    'consensus_note': 'analyst consensus is FY-annual only (report_rc); '
                      'single-quarter consensus UNAVAILABLE -> '
                      'IMPLIED_CONS_FY constructed from PIT FY consensus '
                      'minus published H1 actual with seasonal split; '
                      'clearly labeled, never mixed into main target.',
    'min_broker_reports': 2,
    # ---- history windows
    'hist_windows': (4, 8, 12),
    'hist_window_default': 8,
    # ---- phases by anchor (announcement) year
    'burnin_anchor_years': (2018,),
    'phases': (('TRAIN', 2019, 2022), ('VALID', 2023, 2024),
               ('OOS', 2025, 2025), ('LIVE-LIKE', 2026, 2026)),
    'walkforward': (('W1', 2019, 2020, 2021), ('W2', 2020, 2021, 2022),
                    ('W3', 2021, 2022, 2023), ('W4', 2022, 2023, 2024),
                    ('W5', 2023, 2024, 2025), ('W6', 2024, 2025, 2026)),
    # ---- models (interpretable first)
    'reg_models': ('RIDGE', 'GBM'),
    'cls_models': ('LOGIT', 'GBM_CLS', 'RF'),
    'reg_gbm': {'n_estimators': 300, 'max_depth': 3, 'learning_rate': 0.05,
                'min_samples_leaf': 40, 'subsample': 0.8},
    'ridge_alpha': 10.0,
    # ---- evaluation
    'min_xs': 20,
    'ic_min_n': 15,
    'nw_lag_by_offset': {60: 5, 45: 4, 30: 3, 20: 2, 10: 1},
    'group_buckets': (0.05, 0.10, 0.20),
    # ---- discipline
    'seed': 20260926,
    'target_reports': '2018Q4 .. 2026Q2 (anchor year 2019..2026)',
    'st_kept': True,            # ST/*ST flagged, never silently dropped
    'delisted_kept': True,      # historical universe, no survivorship filter
}

Q_MAP = {'0331': ('Q1', 1), '0630': ('Q2', 2), '0930': ('Q3', 3), '1231': ('Q4', 4)}
Q_NEXT = {1: ('0630', 0), 2: ('0930', 0), 3: ('1231', 0), 4: ('0331', 1)}


def q_info(end_date):
    """'20250630' -> (2025, 3, '2025Q3')"""
    s = str(end_date)
    y, md = int(s[:4]), s[4:]
    v = Q_MAP.get(md)
    if v is None:
        return y, None, None
    return y, v[1], '%dQ%d' % (y, v[1])


def q_add(end_date, k):
    """next k-th quarter end date string after end_date."""
    s = str(end_date)
    y, md = int(s[:4]), s[4:]
    q = Q_MAP[md][1]
    nq = q + k
    y2 = y + (nq - 1) // 4
    q2 = (nq - 1) % 4 + 1
    md2 = {1: '0331', 2: '0630', 3: '0930', 4: '1231'}[q2]
    return '%d%s' % (y2, md2)


def phase_of_year(y):
    for tag, a, b in PREREG['phases']:
        if a <= y <= b:
            return tag
    return 'BURNIN' if y in PREREG['burnin_anchor_years'] else 'OUT'


# ---------------------------------------------------------------- logging
class Log(object):
    def __init__(self, name):
        self.name = name
        self.path = os.path.join(OUT, '%s.log' % name)
        self.t0 = time.time()

    def __call__(self, msg):
        line = '[%7.1fs] %s' % (time.time() - self.t0, msg)
        print(line)
        with open(self.path, 'a', encoding='utf-8') as f:
            f.write(line + '\n')


# ---------------------------------------------------------------- stats
def winsorize(s, lo=0.01, hi=0.99):
    s = pd.Series(s)
    a, b = s.quantile(lo), s.quantile(hi)
    return s.clip(a, b)


def zs(s):
    s = pd.Series(s, dtype=float)
    sd = s.std()
    return (s - s.mean()) / sd if sd and np.isfinite(sd) and sd > 0 else s * 0.0


def rank_pct(s):
    return pd.Series(s).rank(pct=True)


def spearman_ic(pred, actual, min_n=None):
    pred, actual = pd.Series(pred, dtype=float), pd.Series(actual, dtype=float)
    m = pred.notna() & actual.notna() & np.isfinite(pred) & np.isfinite(actual)
    n = int(m.sum())
    min_n = min_n or PREREG['ic_min_n']
    if n < min_n:
        return np.nan, n
    return pred[m].corr(actual[m], method='spearman'), n


def pearson_r(x, y, min_n=15):
    x, y = pd.Series(x, dtype=float), pd.Series(y, dtype=float)
    m = x.notna() & y.notna() & np.isfinite(x) & np.isfinite(y)
    if int(m.sum()) < min_n:
        return np.nan, int(m.sum())
    return x[m].corr(y[m]), int(m.sum())


def auc_score(y, score):
    df = pd.DataFrame({'y': y, 's': score}).dropna()
    pos, neg = df[df.y > 0]['s'], df[df.y <= 0]['s']
    if len(pos) < 10 or len(neg) < 10:
        return np.nan
    r = pd.concat([pos, neg]).rank()
    return float((r.iloc[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2)
                 / (len(pos) * len(neg)))


def ic_stats(ics, lag):
    """mean IC, t-stat with Newey-West(lag), ICIR."""
    x = pd.Series(ics).dropna().astype(float)
    if len(x) < 5:
        return np.nan, np.nan, np.nan, len(x)
    n = len(x)
    mu = x.mean()
    e = x - mu
    g = sum(float((e.iloc[i:] * e.iloc[:-i] if i else e * e).sum()) / n
            for i in range(0, min(lag, n - 1) + 1))
    var = (g / n) if g > 0 else x.var()
    t = mu / np.sqrt(var / n) if var > 0 else np.nan
    return float(mu), float(t), float(mu / x.std()) if x.std() > 0 else np.nan, n


def ols_resid(y, X):
    """y: Series, X: DataFrame -> residual Series (drops na rows jointly)."""
    df = pd.concat([y.rename('y'), X], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
    if len(df) < 30 or df.shape[1] < 2:
        return pd.Series(np.nan, index=y.index)
    A = df.drop(columns='y').values
    A = np.column_stack([np.ones(len(A)), A])
    yv = df['y'].values
    beta, *_ = np.linalg.lstsq(A, yv, rcond=None)
    r = pd.Series(yv - A.dot(beta), index=df.index)
    return r.reindex(y.index)


def dummies(series, prefix):
    d = pd.get_dummies(pd.Series(series).fillna('NA'), prefix=prefix, dtype=float)
    if d.shape[1] > 1:
        d = d.drop(columns=d.columns[-1])
    return d


def demean_by(df, col, by):
    g = df.groupby(by)[col].transform('mean')
    return df[col] - g


def fmt(x, nd=3):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return 'NA'
    return ('%.' + str(nd) + 'f') % x


def pct(x, nd=1):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return 'NA'
    return ('%.' + str(nd) + 'f%%') % (100 * x)


# ---------------------------------------------------------------- loaders
def load_calendar():
    cal = pd.read_parquet(os.path.join(FS_DATA, 'calendar.parquet'))
    cal = cal.sort_values('trade_date').reset_index(drop=True)
    return cal


def trade_offset(cal, date_str, k):
    """date_str 'YYYYMMDD' -> trade_date k trading days BEFORE date (or the
    first trade date <= date - k).  Returns None if before calendar start."""
    sub = cal[cal['trade_date'] <= str(date_str)]
    if len(sub) == 0:
        return None
    i = sub['idx'].iloc[-1] - k
    if i < 0:
        return None
    return cal['trade_date'].iloc[int(i)]


def load_price_panel():
    p = pd.read_parquet(os.path.join(FS_DATA, 'price_panel.parquet'),
                        columns=['ts_code', 'trade_date', 'qfq_close', 'vol', 'amount'])
    return p


def load_basic_panel():
    p = pd.read_parquet(os.path.join(FS_DATA, 'basic_panel.parquet'),
                        columns=['ts_code', 'trade_date', 'close', 'turnover_rate',
                                 'volume_ratio', 'pe_ttm', 'pb', 'ps_ttm',
                                 'total_mv', 'circ_mv'])
    return p


def load_industry_map():
    """PIT SW industry map -> (ts_code, eff_from, eff_to, l1, l2) records."""
    m = pd.read_csv(os.path.join(CD, 'industry', 'sw_industry_map.csv'),
                    dtype=str)
    m['in_date'] = m['in_date'].fillna('19900101')
    m['out_date'] = m['out_date'].fillna('29991231')
    recs = []
    for _, r in m.iterrows():
        recs.append((r['ts_code'], r['in_date'], r['out_date'],
                     r['l1_name'], r['l2_name']))
    im = pd.DataFrame(recs, columns=['ts_code', 'in_date', 'out_date', 'l1', 'l2'])
    return im


def industry_at(im, ts_code, date_str):
    sub = im[(im['ts_code'] == ts_code) & (im['in_date'] <= str(date_str))
             & (im['out_date'] >= str(date_str))]
    if len(sub) == 0:
        return 'UNKNOWN', 'UNKNOWN'
    return sub['l1'].iloc[-1], sub['l2'].iloc[-1]


def industry_map_fast(im):
    """dict ts_code -> sorted list of (in_date, out_date, l1, l2)."""
    d = {}
    for ts, g in im.groupby('ts_code'):
        d[ts] = sorted(zip(g['in_date'], g['out_date'], g['l1'], g['l2']))
    return d


def industry_at_fast(d, ts_code, date_str):
    lst = d.get(ts_code)
    if not lst:
        return 'UNKNOWN', 'UNKNOWN'
    lo, hi = 0, len(lst) - 1
    ans = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if lst[mid][1] >= date_str:
            ans = mid
            hi = mid - 1
        else:
            lo = mid + 1
    if ans is None:
        return 'UNKNOWN', 'UNKNOWN'
    a, b, l1, l2 = lst[ans]
    if a <= date_str <= b:
        return l1, l2
    return 'UNKNOWN', 'UNKNOWN'


FIN_PREFIXES = ['income', 'balance', 'cashflow', 'fina_indicator', 'fin_ind']


def num2str_date(s):
    """numeric (int/float w/ NaN) or str date -> 'YYYYMMDD' object, <NA> miss."""
    v = pd.to_numeric(s, errors='coerce')
    out = pd.Series(pd.NA, index=s.index, dtype='object')
    m = v.notna()
    out.loc[m] = v[m].astype('int64').astype(str)
    return out


def prep_fund(df, dedup_end=True):
    for c in ('ann_date', 'f_ann_date', 'end_date'):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')
    if 'report_type' in df.columns:
        df = df[df['report_type'] == '1']
    if 'f_ann_date' in df.columns:
        df['ann'] = df['f_ann_date'].fillna(df.get('ann_date'))
    else:
        df['ann'] = df.get('ann_date')
    df = df.dropna(subset=['end_date'])
    df = df.drop_duplicates(subset=['ts_code', 'end_date', 'ann'], keep='last')
    if dedup_end:
        df = df.drop_duplicates(subset=['ts_code', 'end_date'], keep='last')
    for c in ('ann_date', 'f_ann_date', 'end_date', 'ann'):
        if c in df.columns:
            df[c] = num2str_date(df[c])
    return df


def load_income():
    """consolidated quarterly income (cumulative), report_type=1 only."""
    want = ['ts_code', 'ann_date', 'f_ann_date', 'end_date', 'report_type',
            'revenue', 'total_revenue', 'n_income', 'n_income_attr_p',
            'operate_profit', 'total_cogs', 'rd_exp', 'total_profit']
    paths = (glob.glob(os.path.join(CD, 'income_??????_??.parquet'))
             + glob.glob(os.path.join(PQ, 'income_*.parquet')))
    dfs = []
    for p in paths:
        try:
            d = pd.read_parquet(p)
        except Exception:
            continue
        if 'ts_code' not in d.columns:
            continue
        cols = [c for c in want if c in d.columns]
        dfs.append(d[cols].drop_duplicates())
    df = pd.concat(dfs, ignore_index=True)
    for c in want:
        if c not in df.columns:
            df[c] = np.nan
    df = prep_fund(df, dedup_end=False)
    df = df.dropna(subset=['ann'])
    df = df[(df['end_date'].astype(str).str[-4:].isin(Q_MAP.keys()))]
    return df


def load_forecast():
    """company earnings forecast (guidance) full history per stock."""
    paths = (glob.glob(os.path.join(CD, 'forecast_??????_??.parquet'))
             + glob.glob(os.path.join(PQ, 'forecast_vip_*.parquet')))
    dfs = []
    for p in paths:
        try:
            d = pd.read_parquet(p)
        except Exception:
            continue
        if 'ts_code' not in d.columns:
            continue
        dfs.append(d.drop_duplicates())
    df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
    if len(df):
        for c in ('ann_date', 'end_date'):
            df[c] = pd.to_numeric(df[c], errors='coerce')
        df = df.dropna(subset=['ann_date', 'end_date'])
        df = df.drop_duplicates(subset=['ts_code', 'ann_date', 'end_date',
                                        'net_profit_min', 'net_profit_max'])
    return df


def load_stk_basic():
    p = os.path.join(CD, 'stock_basic.csv')
    if os.path.exists(p):
        df = pd.read_csv(p, dtype=str)
        keep = [c for c in ['ts_code', 'name', 'industry', 'list_date',
                            'delist_date', 'market'] if c in df.columns]
        return df[keep].drop_duplicates(subset=['ts_code'])
    return pd.DataFrame()


if __name__ == '__main__':
    lg = Log('hef_common_selftest')
    lg('PREREG hypothesis: %s' % PREREG['hypothesis_id'])
    lg('q_add 20250630 +1 = %s' % q_add('20250630', 1))
    lg('q_info 20251231 = %s' % str(q_info('20251231')))
    lg('phase_of 2020=%s 2025=%s 2026=%s 2018=%s' % (
        phase_of_year(2020), phase_of_year(2025), phase_of_year(2026),
        phase_of_year(2018)))
    cal = load_calendar()
    lg('calendar %s .. %s  n=%d' % (cal['trade_date'].iloc[0],
                                    cal['trade_date'].iloc[-1], len(cal)))
    lg('offset(20251028,-30) = %s' % trade_offset(cal, '20251028', 30))
    im = load_industry_map()
    lg('industry records %d stocks %d' % (len(im), im['ts_code'].nunique()))
    f = industry_map_fast(im)
    lg('industry_at_fast(000001.SZ, 20240601) = %s' % str(industry_at_fast(f, '000001.SZ', '20240601')))
    lg('selftest OK')
