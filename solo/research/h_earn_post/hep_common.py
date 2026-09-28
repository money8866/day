# -*- coding: utf-8 -*-
"""H-EARN-POST 公共常量（预注册）与工具

Hypothesis ID : H-EARN-POST
主题          : 中报公布并经过 20–30 个交易日市场消化后，基本面改善与股价反应之间
                是否仍存在可交易的错配（二次定价 Alpha）

隔离声明（§1）
  本模块为独立科研模块，位于 research/h_earn_post/，不 import 亦不修改以下任何模块：
  ARCHIVE / HVT / DLG / F120 / Theme Quant / te_buy_pool / 突破策略 / 天量策略 / 首板策略。
  数据来源只使用 Tushare cache（D:\\mystock\\cache_daily）与本研究自建缓存；
  不使用上述模块的任何信号、标签、筛选结果或排序结果。

预注册（§40：所有参数必须先写死，禁止事后择优）
  以下常量在构建数据集之前即冻结；后续所有分析只读取，不再修改。
"""
import os
import math
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data')
OUTD = os.path.join(HERE, 'out')
for _d in (DATA, OUTD):
    os.makedirs(_d, exist_ok=True)

# ------------------------------------------------------------------ 数据源
CD = r'D:\mystock\cache_daily'
PD = os.path.join(CD, 'parquet')
DB = os.path.join(CD, 'stock_data.db')
# 市场级数据（行情/估值/日历/指数），均为 Tushare cache，与任何策略模块无关
FS_DATA = os.path.join(HERE, '..', 'fundamental_surprise_alpha', 'data')

# ------------------------------------------------------------------ 预注册
PREREG = {
    'hypothesis_id': 'H-EARN-POST',
    # 研究对象：中报（end_date 月 = 0630）
    'period_month': '0630',
    # 观察窗口 / 候选 Entry（中报后第 k 个交易日）
    'entries': (20, 25, 30),
    # 预测周期
    'horizons': (5, 10, 20, 60),
    'primary_horizon': 20,          # §19 Primary = T+20
    'secondary_horizons': (5, 10, 60),
    # §25 事件后路径（Entry 后 k 个交易日的累计超额收益，用于 TYPE A/B/C/D 判定）
    'path_horizons': (1, 2, 3, 5, 10, 15, 20, 25, 30, 40, 60),
    # 时间锚点
    'D0': 'ann_date 之后第一个交易日（首个可交易日）',
    'entry_price': 'E+1 开盘（信号在 E 收盘后已知）',
    'exit_price': 'E+1+T 收盘',
    'benchmark': '000300.SH 沪深300 买入持有',
    # 成本（往返 bp）
    'cost_bps': (0, 10, 20, 30, 50),
    # §40 参数邻域
    'hist_windows': (4, 8, 12),
    'hist_window_default': 8,       # 主模型使用的中位窗口中位数（§40 邻域 = 4/8/12）
    'pct_cuts': (0.2, 0.3, 0.4, 0.5),
    'reaction_bins': ('LOW', 'MID', 'HIGH'),
    # §36 分期（按中报所属年度）
    'phases': (('TRAIN', 2018, 2022), ('VALID', 2023, 2024),
               ('OOS', 2025, 2025), ('LIVE-LIKE', 2026, 2026)),
    # §38 Walk Forward（训练年 → 测试年）
    'walkforward': (('W1', 2018, 2020, 2021), ('W2', 2019, 2021, 2022),
                    ('W3', 2020, 2022, 2023), ('W4', 2021, 2023, 2024),
                    ('W5', 2022, 2024, 2025), ('W6', 2023, 2025, 2026)),
    # §7/§41 受测基本面变量（≤10）
    'fund_vars': ('np_s1', 'rev_s1', 'np_accel', 'rev_accel', 'dp_yoy',
                  'roe_chg', 'gpm_chg', 'np_minus_ocf', 'ar_minus_rev',
                  'inv_minus_rev'),
    # §11/§41 价格反应变量（≤6）：D0→E 的自身/指数/行业相对吸收 + D0→+5/+10/+20 子窗口
    'px_vars': ('px_ret', 'px_ret_rel', 'px_ret_ind_rel',
                'px_ret_5', 'px_ret_10', 'px_ret_20'),
    # 主信号（预注册，四选一不做事后择优；SIG_RESID 为核心假设）
    'signals': ('SIG_FSC',        # 纯基本面 surprise 合成
                'SIG_RESID',      # 基本面 surprise 对 Price Absorption 正交后的残差 = 二次定价核心
                'SIG_QUADA',      # High FSC & Low/Mid PA 象限
                'SIG_INTER'),     # FSC_z × (−PA_z) 交互
    'primary_signal': 'SIG_RESID',
    # Regime（project-wide 统一定义：指数 200 日均线 + 60 日动量）
    'regime_rule': '>MA200 且 60日动量>0 = BULL；<MA200 且 <0 = BEAR；其余 NORMAL',
    # 随机种子
    'seed': 20260926,
    'n_seed': 30,
    # §32/§33/§34 零假设诊断常量（非可调参数，仅用于构造零假设与反事实）
    'null_shift_days': 40,      # N2 随机日期：Entry 前后平移 ±40 个交易日内随机取日（δ≠0）
    'null_pct_cut_hi': 0.7,     # §34 High Fundamental Surprise = cohort FSC 分位 ≥ 0.7
    'null_pct_cut_lo': 0.3,     # §34 Matched Control 池 = cohort FSC 分位 ≤ 0.3
    'null_z_pass': 2.0,         # §33 判定门槛：z ≥ 2.0 才认为显著区别于零假设
}
ENTRIES = PREREG['entries']
HORIZONS = PREREG['horizons']
FUND_VARS = PREREG['fund_vars']
PX_VARS = PREREG['px_vars']
SIGNALS = PREREG['signals']

WINSOR = (0.01, 0.99)
MIN_XS = 20          # 横截面最小样本（低于此不计算 IC/AUC）
F32 = ['open', 'close', 'pre_close', 'qfq_open', 'qfq_close', 'vol', 'amount']
BASIC_KEEP = ['turnover_rate', 'volume_ratio', 'pe', 'pe_ttm', 'pb', 'ps', 'ps_ttm',
              'dv_ratio', 'dv_ttm', 'total_mv', 'circ_mv', 'total_share', 'free_share']


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
    """横截面 z-score（先 winsorize；标准差为 0 时返回 0）"""
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
    """Spearman Rank IC（样本不足返回 nan）"""
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
    """x 越高越可能属于正类（y=1）的 AUC（Mann-Whitney）"""
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
    """IC 序列统计；t 值用 Newey-West（滞后 = horizon-1，重叠窗口自相关修正）"""
    s = pd.Series(ics, dtype=float).dropna()
    n = len(s)
    if n < 5:
        return dict(n=n, mean_ic=np.nan, med_ic=np.nan, ic_std=np.nan,
                    icir=np.nan, ic_t=np.nan, pos_ratio=np.nan)
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
    return dict(n=n, mean_ic=mu, med_ic=float(s.median()), ic_std=sd,
                icir=float(icir) if np.isfinite(icir) else np.nan,
                ic_t=float(mu / se) if se > 1e-12 else np.nan,
                pos_ratio=float((s > 0).mean()))


def ols_resid(y, X):
    """y 对 X 回归取残差（X 不含常数项时自动补）；返回残差数组（与 y 等长，NaN 位置为 NaN）"""
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


def dummies(series):
    """类别 -> 哑变量矩阵（丢弃第一类）"""
    s = pd.Series(series).astype(str).fillna('NA')
    cats = sorted(s.unique())
    if len(cats) <= 1:
        return np.zeros((len(s), 0))
    return np.column_stack([(s == c).values.astype(float) for c in cats[1:]])


def demean_by(s, g):
    s = pd.Series(s, dtype=float)
    return s - s.groupby(pd.Series(g).values).transform('mean')


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
