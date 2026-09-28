# -*- coding: utf-8 -*-
"""TL-01 公共常量（预注册）与统计工具

Hypothesis ID : TL-01  Trend Lifecycle Alpha
研究主题      : 股票进入趋势后，趋势生命周期是否存在稳定、可重复的状态转换规律，
                以及这些状态转换能否预测未来 T+3 / T+5 / T+10 / T+20 / T+60 的风险收益。

隔离声明
  本模块为独立科研模块，位于 research/trend_lifecycle/，不 import 亦不修改以下任何模块：
  ARCHIVE / HVT / DLG / F120 / Theme Quant / te_buy_pool / 突破策略 / 天量策略 / 首板策略。
  数据只来自 Tushare cache 的行情/估值/日历面板（price_panel / basic_panel / index_panel）
  与 stock_basic / sw_industry_map / treasure_namechg，不使用任何既有策略的信号、标签、
  筛选结果或排序结果。
  §1 明确：禁止继承此前任何已 FAIL / STOP 的结论作为正向先验；本研究不引用任何既有策略。

预注册（§33/§40）
  以下常量在构建数据集之前即冻结；后续所有分析只读取，不再修改。
  方向（信号符号）只用 TRAIN 期（2018–2022）判定后冻结，OOS 不参与任何选择。
"""
import os
import math
import json
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data')
OUTD = os.path.join(HERE, 'out')
for _d in (DATA, OUTD):
    os.makedirs(_d, exist_ok=True)

# ------------------------------------------------------------------ 数据源
FS_DATA = os.path.join(HERE, '..', 'fundamental_surprise_alpha', 'data')
CD = r'D:\mystock\cache_daily'
PD = os.path.join(CD, 'parquet')

# ------------------------------------------------------------------ 预注册
PREREG = {
    'hypothesis_id': 'TL-01',
    'title': 'Trend Lifecycle Alpha',
    # ---- §18 预测目标
    'horizons': (3, 5, 10, 20, 60),
    'primary_horizon': 20,
    'secondary_horizons': (3, 5, 10, 60),
    # ---- §4/§5 股票池
    'min_hist_days': 120,           # §5 至少 120 个交易日
    'listing_min_days': 120,
    'exclude_suffix': ('.BJ',),     # 不交易北交所
    'exclude_st': True,
    # ---- §6-§13 特征窗口（预注册）
    'ret_windows': (5, 10, 20, 40, 60, 120),
    'ma_windows': (5, 10, 20, 60, 120),
    'slope_windows': (10, 20, 40, 60),
    'pos_windows': (20, 60, 120),
    'rv_windows': (20, 60),
    'atr_window': 14,
    'vol_slope_window': 20,
    'velocity_lag': 5,              # §13 TrendVelocity = TS_t - TS_{t-5}
    'accel_lag': 5,                 # §13 TrendAcceleration = TV_t - TV_{t-5}
    # §7 TrendStrength 六个预注册分量（等权，不人为赋权）
    'ts_blocks': ('ret_20', 'c_ma20', 'ma20_ma60', 'pos_60', 'neg_rv20', 'rs_20'),
    # ---- §16 Model A 规则状态：预注册阈值
    'th_pos_expa_lo': 0.40,
    'th_pos_expa_hi': 0.85,
    'th_pos_mid': 0.50,
    'th_pos_hi': 0.90,
    'th_dd_mild': 0.05,
    'th_dd_deep': 0.15,
    'th_dd_term': 0.20,
    'th_atr_acc_pct': 0.60,
    'th_atr_hi_pct': 0.70,
    'th_v_expand': 0.0,
    'th_reacc_lookback': 10,
    'th_reacc_dd_lo': 0.02,
    'th_reacc_dd_hi': 0.30,
    # ---- §16 Model B 聚类
    'cluster_k': (4, 5, 6, 7, 8),
    'cluster_primary_k': 6,
    'cluster_feats': ('ts_raw', 'slope_20', 'pos_60', 'rv_20', 'rs_20',
                      'v_trend', 'dd_60'),
    'cluster_fit_n': 300000,
    # ---- §31 分期（按自然年）
    'phases': (('TRAIN', 2018, 2022), ('VALID', 2023, 2024),
               ('OOS', 2025, 2025), ('LIVE-LIKE', 2026, 2026)),
    # ---- §32 Walk Forward（训练起、训练止、测试年）
    'walkforward': (('W1', 2018, 2020, 2021), ('W2', 2019, 2021, 2022),
                    ('W3', 2020, 2022, 2023), ('W4', 2021, 2023, 2024),
                    ('W5', 2022, 2024, 2025), ('W6', 2023, 2025, 2026)),
    # ---- §33 参数扰动
    'grid_ma': (18, 20, 22),
    'grid_trend_win': (18, 20, 25),
    'grid_rs_win': (18, 20, 25),
    'grid_dd': (0.05, 0.075, 0.10),
    'grid_age': (15, 20, 30),
    # ---- §38 成本（往返 bp）
    'cost_bps': (0, 10, 20, 30, 50),
    # ---- §35/§36 零假设与置换
    'seed': 20260926,
    'n_perm': 1000,
    'null_shift_days': 40,
    'null_z_pass': 2.0,
    # ---- §21/§28 Top/Bottom 分位
    'pct_cut_lo': 0.10,
    'pct_cut_hi': 0.90,
    # ---- §43 组合
    'pf_pct': (0.10, 0.20, 0.30),
    'pf_primary_pct': 0.10,
    'pf_primary_horizon': 20,
    # ---- 信号族（预注册，全测不择优）
    'signals': ('SIG_TS',       # 静态趋势强度
                'SIG_TV',       # 趋势速度
                'SIG_TA',       # 趋势加速度
                'SIG_REACC',    # 再启动结构
                'SIG_AGE',      # 趋势年龄（取负）
                'SIG_TRANS',    # 状态转换条件期望（TRAIN 拟合）
                'SIG_TRANS_CORE'),   # 预注册核心转换 0/1
    'primary_signal': 'SIG_TRANS',
    'trend_states': (1, 2, 3, 4, 5, 6, 7, 8),
    'non_trend_states': (0, 9, 10),
    # §17/§47 需单独报告的状态转换
    'report_transitions': ((0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6),
                           (6, 7), (7, 8), (7, 9), (8, 3), (8, 4)),
    'core_transitions': ((1, 2), (2, 3), (3, 4), (7, 8)),
    # ---- §29 稳定性门槛（判定用，非可调）
    'year_stable_min': 0.60,
    'regime_reverse_max': 0.5,
    'auc_min': 0.52,
}
HORIZONS = PREREG['horizons']
PHASE_TRAIN = PREREG['phases'][0][1:]

WINSOR = (0.01, 0.99)
MIN_XS = 20
STATE_NAMES = {
    0: 'S0_非趋势', 1: 'S1_萌芽', 2: 'S2_确认', 3: 'S3_扩张', 4: 'S4_加速',
    5: 'S5_高位拥挤', 6: 'S6_衰减', 7: 'S7_正常回撤', 8: 'S8_再启动',
    9: 'S9_趋势破坏', 10: 'S10_趋势终结',
}


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


# ------------------------------------------------------------------ 统计工具
def winsorize(s, lo=WINSOR[0], hi=WINSOR[1]):
    s = pd.Series(s, dtype=float)
    if s.notna().sum() < 20:
        return s
    a, b = s.quantile(lo), s.quantile(hi)
    return s.clip(a, b)


def zs(s):
    s = winsorize(s)
    sd = s.std(ddof=0)
    if not np.isfinite(sd) or sd < 1e-12:
        return pd.Series(np.zeros(len(s)), index=s.index)
    return (s - s.mean()) / sd


def rank_pct(s):
    s = pd.Series(s, dtype=float)
    if s.notna().sum() < 2:
        return pd.Series(np.full(len(s), np.nan), index=s.index)
    return s.rank(pct=True)


def spearman_ic(x, y):
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    m = np.isfinite(x) & np.isfinite(y)
    if m.sum() < MIN_XS:
        return np.nan
    x, y = x[m], y[m]
    if np.std(x) < 1e-12 or np.std(y) < 1e-12:
        return np.nan
    rx = pd.Series(x).rank().values
    ry = pd.Series(y).rank().values
    return float(np.corrcoef(rx, ry)[0, 1])


def pearson_r(x, y):
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    m = np.isfinite(x) & np.isfinite(y)
    if m.sum() < 8 or np.std(x[m]) < 1e-12 or np.std(y[m]) < 1e-12:
        return np.nan
    return float(np.corrcoef(x[m], y[m])[0, 1])


def auc_score(x, y):
    """x 越高越可能属于正类（y=1）的 AUC"""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    p, n = int((y == 1).sum()), int((y == 0).sum())
    if p == 0 or n == 0:
        return np.nan
    r = pd.Series(x).rank().values
    return float((r[y == 1].sum() - p * (p + 1) / 2.0) / (p * n))


def ic_stats(ics, horizon=20):
    """IC 序列统计；t 值用 Newey-West（滞后 = horizon-1）"""
    s = pd.Series(ics, dtype=float).dropna()
    n = len(s)
    if n < 5:
        return dict(n=n, mean_ic=np.nan, med_ic=np.nan, ic_std=np.nan,
                    icir=np.nan, ic_t=np.nan, pos_ratio=np.nan, p_raw=np.nan)
    mu, sd = float(s.mean()), float(s.std(ddof=1))
    icir = mu / sd if sd > 1e-12 else np.nan
    lag = max(1, int(horizon) - 1)
    x = s.values - mu
    g0 = float(np.dot(x, x) / n)
    var = g0
    for L in range(1, min(lag, n - 1) + 1):
        gl = float(np.dot(x[L:], x[:-L]) / n)
        var += 2.0 * (1.0 - L / float(lag + 1)) * gl
    se = math.sqrt(max(var, 1e-18) / n)
    t = float(mu / se) if se > 1e-12 else np.nan
    return dict(n=n, mean_ic=mu, med_ic=float(s.median()), ic_std=sd,
                icir=float(icir) if np.isfinite(icir) else np.nan,
                ic_t=t, pos_ratio=float((s > 0).mean()),
                p_raw=_t_pvalue(t, max(n - 1, 1)))


def _t_pvalue(t, df):
    """双尾 p 值（正态近似，df>=30 足够；否则用 t 近似）"""
    if not np.isfinite(t):
        return np.nan
    try:
        from scipy import stats
        return float(2.0 * stats.t.sf(abs(t), df))
    except Exception:
        z = abs(float(t))
        return float(math.erfc(z / math.sqrt(2.0)))


def t_pvalue(t, df=1e9):
    return _t_pvalue(t, df)


def bh_fdr(pvals, alpha=0.05):
    """Benjamini-Hochberg FDR：返回 (adjusted_p, reject)"""
    s = pd.Series(pvals, dtype=float)
    idx = s.dropna().sort_values().index
    m = len(idx)
    adj = pd.Series(np.nan, index=s.index)
    if m == 0:
        return adj, pd.Series(False, index=s.index)
    prev = 1.0
    q = {}
    for rank, i in enumerate(reversed(list(idx)), start=1):
        k = m - rank + 1
        v = min(prev, float(s[i]) * m / k)
        q[i] = v
        prev = v
    for i, v in q.items():
        adj[i] = v
    return adj, adj <= alpha


def ols_resid(y, X):
    y = np.asarray(y, dtype=float)
    X = np.asarray(X, dtype=float)
    if X.ndim == 1:
        X = X[:, None]
    m = np.isfinite(y) & np.all(np.isfinite(X), axis=1)
    out = np.full(len(y), np.nan)
    if m.sum() < max(12, X.shape[1] + 4):
        return out
    A = np.column_stack([np.ones(m.sum()), X[m]])
    b, *_ = np.linalg.lstsq(A, y[m], rcond=None)
    out[m] = y[m] - A.dot(b)
    return out


def ols_full(y, X):
    """返回 (beta, resid, r2, n, k)"""
    y = np.asarray(y, dtype=float)
    X = np.asarray(X, dtype=float)
    if X.ndim == 1:
        X = X[:, None]
    m = np.isfinite(y) & np.all(np.isfinite(X), axis=1)
    if m.sum() < max(12, X.shape[1] + 4):
        return None, np.full(len(y), np.nan), np.nan, int(m.sum()), X.shape[1]
    A = np.column_stack([np.ones(m.sum()), X[m]])
    b, *_ = np.linalg.lstsq(A, y[m], rcond=None)
    fit = A.dot(b)
    resid = y[m] - fit
    sst = float(((y[m] - y[m].mean()) ** 2).sum())
    sse = float((resid ** 2).sum())
    r2 = 1.0 - sse / sst if sst > 1e-18 else np.nan
    out = np.full(len(y), np.nan)
    out[m] = resid
    return b, out, r2, int(m.sum()), X.shape[1]


def dummies(series, drop_first=True):
    s = pd.Series(series).astype(str).fillna('NA')
    cats = sorted(s.unique())
    if len(cats) <= 1:
        return np.zeros((len(s), 0)), []
    use = cats[1:] if drop_first else cats
    return np.column_stack([(s == c).values.astype(float) for c in use]), use


def demean_by(s, g):
    s = pd.Series(s, dtype=float)
    return s - s.groupby(pd.Series(g).values).transform('mean')


def rank_avg(x):
    """平均秩（并列取平均），IC/AUC 需要（SIG_TRANS 等离散信号并列极多）"""
    u, inv, cnt = np.unique(np.asarray(x, dtype=np.float64),
                            return_inverse=True, return_counts=True)
    csum = np.cumsum(cnt)
    start = csum - cnt
    return ((start + csum - 1) / 2.0)[inv]


def daily_ic(sig, y, VAL, min_xs=50, days=None):
    """逐日横截面 Spearman IC；sig/y 为 (NCAL, NCODE)，VAL 为同形布尔掩码。
    days 给定时只计算这些交易日（其余位置保持 NaN），用于分期/平移场景提速。"""
    NCAL = sig.shape[0]
    out = np.full(NCAL, np.nan)
    rng = range(NCAL) if days is None else np.asarray(days)
    for k in rng:
        v = VAL[k]
        if not v.any():
            continue
        s, yy = sig[k], y[k]
        m = v & np.isfinite(s) & np.isfinite(yy)
        n = int(m.sum())
        if n < min_xs:
            continue
        xs = s[m].astype(np.float64)
        ys = yy[m].astype(np.float64)
        if xs.std() < 1e-12 or ys.std() < 1e-12:
            continue
        rx = rank_avg(xs)
        ry = rank_avg(ys)
        rx = (rx - rx.mean()) / (rx.std() + 1e-12)
        ry = (ry - ry.mean()) / (ry.std() + 1e-12)
        out[k] = float(np.dot(rx, ry) / n)
    return out


PANDIR = os.path.join(DATA, 'panel')


def panel_meta():
    with open(os.path.join(PANDIR, 'meta.json'), encoding='utf-8') as f:
        return json.load(f)


def pget(name, mode='r'):
    """以 memmap 打开面板列（NCAL x NCODE），只加载所需列"""
    return np.lib.format.open_memmap(os.path.join(PANDIR, name + '.npy'), mode=mode)


def phase_of(year):
    """§31 分期：由自然年映射到 TRAIN / VALID / OOS / LIVE"""
    y = np.asarray(year)
    out = np.full(len(y), '', dtype=object)
    for nm, a, b in PREREG['phases']:
        out[(y >= a) & (y <= b)] = nm
    return out


def qstats(v):
    """分布统计：Mean/Median/Std/WinRate/P10/P25/P75/P90（§19）"""
    v = np.asarray(v, dtype=np.float64)
    v = v[np.isfinite(v)]
    n = len(v)
    if n < 5:
        return dict(n=n, mean=np.nan, med=np.nan, std=np.nan, win=np.nan,
                    p10=np.nan, p25=np.nan, p75=np.nan, p90=np.nan)
    q = np.percentile(v, [10, 25, 50, 75, 90])
    return dict(n=n, mean=float(v.mean()), med=float(q[2]), std=float(v.std(ddof=1)),
                win=float((v > 0).mean()), p10=float(q[0]), p25=float(q[1]),
                p75=float(q[3]), p90=float(q[4]))


def fmt(x, nd=4):
    if x is None:
        return ''
    try:
        v = float(x)
    except Exception:
        return str(x)
    if not np.isfinite(v):
        return 'n/a'
    return ('%.' + str(nd) + 'f') % v


def pct(x, nd=2):
    if x is None:
        return ''
    try:
        v = float(x)
    except Exception:
        return str(x)
    if not np.isfinite(v):
        return 'n/a'
    return ('%.' + str(nd) + 'f%%') % (v * 100.0)
