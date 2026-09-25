# -*- coding: utf-8 -*-
"""fs_common: 基本面预期差 Alpha 研究 - 公共常量与工具"""
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data')
OUTD = os.path.join(HERE, 'out')
for _d in (DATA, OUTD):
    os.makedirs(_d, exist_ok=True)

DB = r'D:\mystock\cache_daily\stock_data.db'
CD = r'D:\mystock\cache_daily'
PD = os.path.join(CD, 'parquet')

# 研究样本期（§6）
START = '20180101'
END = '20260924'
TRAIN = ('20180101', '20231231')
VALID = ('20240101', '20251231')
OOS = ('20260101', END)

HORIZONS = (5, 10, 20, 60)

PRICE_COLS = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close',
              'pre_close', 'vol', 'amount']

DBASIC_COLS = ['ts_code', 'trade_date', 'close', 'turnover_rate', 'volume_ratio',
               'pe', 'pe_ttm', 'pb', 'ps', 'ps_ttm', 'dv_ratio', 'dv_ttm',
               'total_mv', 'circ_mv', 'total_share', 'float_share', 'free_share']


class Log(object):
    """同时打印并落盘的日志器"""

    def __init__(self, fname):
        self.lines = []
        self.fp = os.path.join(HERE, fname)

    def __call__(self, *a):
        s = ' '.join(str(x) for x in a)
        print(s, flush=True)
        self.lines.append(s)

    def save(self):
        with open(self.fp, 'w', encoding='utf-8') as f:
            f.write('\n'.join(self.lines))
        return self.fp


def load_token():
    for cand in (r'd:\mystock\config\.env', r'd:\mystock\solo\.env',
                 r'd:\mystock\solo\sli\.env', r'd:\mystock\cache_daily\.env'):
        if not os.path.exists(cand):
            continue
        with open(cand, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                if 'TOKEN' in k.upper():
                    return v.strip().strip('"').strip("'")
    return None


def get_pro():
    import tushare as ts
    tok = load_token()
    if not tok:
        raise RuntimeError('Tushare token not found')
    return ts.pro_api(tok)


def add_qfq(df):
    """按 §价格 计算前复权因子: f_t = prod_{u>t}(pre_close_u / close_{u-1})"""
    df = df.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)
    g = df.groupby('ts_code', sort=False)
    coef = df['pre_close'] / g['close'].shift(1)
    coef = coef.where((coef > 0.5) & (coef < 2.0))
    df['_lc'] = np.log(coef.fillna(1.0))
    cs = df.groupby('ts_code', sort=False)['_lc'].cumsum()
    tot = cs.groupby(df['ts_code']).transform('last')
    df['qf'] = np.exp(tot - cs)
    for c in ('open', 'high', 'low', 'close', 'pre_close'):
        df['qfq_' + c] = df[c] * df['qf']
    return df.drop(columns=['_lc'])


def spearman_ic(x, y):
    """Spearman rank IC，样本不足返回 nan"""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    m = np.isfinite(x) & np.isfinite(y)
    if m.sum() < 8:
        return np.nan
    x, y = x[m], y[m]
    rx = pd.Series(x).rank().values
    ry = pd.Series(y).rank().values
    if rx.std() == 0 or ry.std() == 0:
        return np.nan
    return float(np.corrcoef(rx, ry)[0, 1])


def auc_score(x, y):
    """x 越高越可能是正类(y=1) 的 AUC（Mann-Whitney）"""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    p, n = (y == 1).sum(), (y == 0).sum()
    if p == 0 or n == 0:
        return np.nan
    r = pd.Series(x).rank().values
    return float((r[y == 1].sum() - p * (p + 1) / 2.0) / (p * n))


def period_of(trade_date, train=TRAIN, valid=VALID, oos=OOS):
    if train[0] <= trade_date <= train[1]:
        return 'TRAIN'
    if valid[0] <= trade_date <= valid[1]:
        return 'VALID'
    if oos[0] <= trade_date <= oos[1]:
        return 'OOS'
    return 'PRE'


def winsorize(s, lo=0.01, hi=0.99):
    s = pd.Series(s, dtype=float)
    if s.notna().sum() < 20:
        return s
    a, b = s.quantile(lo), s.quantile(hi)
    return s.clip(a, b)


def zscore(s):
    s = pd.Series(s, dtype=float)
    sd = s.std()
    if not np.isfinite(sd) or sd == 0:
        return pd.Series(np.zeros(len(s)), index=s.index)
    return (s - s.mean()) / sd
