# -*- coding: utf-8 -*-
"""
H-TREND-PULLBACK-01  预注册常量（冻结）与通用统计工具

隔离声明（§0）：
    本模块自包含，不 import 任何既有研究模块（TL-01 / HVT / ARCHIVE / H-EARN-* / ...）。
    统计工具为通用实现，与任何历史研究结论文档无耦合。
    历史研究结果仅作背景，不作为本研究的先验。

预注册纪律（§39 / §43 / §63）：
    1. 所有阈值、定义、分期在本文件冻结；试验过程中不得修改。
    2. 参数扰动只允许在 PREREG['grid_*'] 声明的邻域内进行，且必须全报。
    3. 禁止事后择优：阈值 / 年份 / 行业 / horizon / 分位点。
    4. 任何失败 Gate 不得通过改变阈值救回（§26 / §52 / §55）。
"""
from __future__ import annotations

import os
import json
import math
import hashlib

import numpy as np
import pandas as pd


# ══════════════════════════════════════════════════════════════ 路径

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data')
PAN = os.path.join(DATA, 'panel')
OUTD = os.path.join(HERE, 'out')
FS_DATA = os.path.abspath(os.path.join(HERE, '..', 'fundamental_surprise_alpha', 'data'))
CD = r'D:\mystock\cache_daily'

for _d in (DATA, PAN, OUTD):
    os.makedirs(_d, exist_ok=True)


# ══════════════════════════════════════════════════════════════ 预注册

PHASES = ['TRAIN', 'VALID', 'OOS', 'LIVE-LIKE']
PHASE_BOUNDS = {'TRAIN': (2018, 2022), 'VALID': (2023, 2024),
                'OOS': (2025, 2025), 'LIVE-LIKE': (2026, 2026)}

PREREG = {
    'hypothesis_id': 'H-TREND-PULLBACK-01',
    'title': 'Strong Trend -> First Pullback -> Re-acceleration',
    'version': '1.0',
    'note': '所有取值在跑数之前冻结；试验中不得修改',

    # -------- 分期（§39 / §41）
    'phases': PHASE_BOUNDS,
    'year_report': (2021, 2022, 2023, 2024, 2025, 2026),   # §41 逐年全报

    # -------- Universe（§5）
    'min_listing_days': 60,          # 上市不足 60 交易日剔除
    'exclude_st': True,              # ST / *ST（时点，用 namechange PIT）
    'exclude_delist': True,          # 退市整理
    'exclude_suspend': True,         # 停牌（vol<=0）
    'exclude_bj': True,              # 北交所（披露式剔除，见 excluded_count）
    'require_ma60': True,            # 数据严重缺失：MA60 不可得则剔除

    # -------- Strong Trend（§7 / §8）
    'ma_fast': 20,
    'ma_slow': 60,
    'slope_win': 20,                 # slope 窗口（归一化线性回归斜率）
    't1': 'close > ma20 > ma60',
    't2': 'slope20 > 0 AND slope60 >= 0',
    't3': 'ret20 > 0',
    'trend_require': ('T1', 'T2'),   # §7 至少 T1+T2
    't3_aux': True,                  # T3 仅辅助，不作 gate

    # -------- First Pullback（§9-§13）
    'peak_win': 20,                  # §10 Peak = 前 20 日最高价（不含当日）
    'pb_min_days': 2,                # 存在性门槛：Duration >= 2 交易日
    'pb_min_depth': 0.01,            # 存在性门槛：Depth >= 1%
    'pb_max_days': 40,               # 单次回撤过程上限（预注册，防无界）
    'depth_bands': (0.01, 0.02, 0.03, 0.05, 0.08, 0.10),  # §11 全报不择优
    'first_per_leg': True,           # §9 「第一次」= 每个 trend leg 内第一个合格 episode

    # -------- Re-acceleration（§21）
    'reacc_def': 'close > pullback_start_price',
    'reacc_max_days': 20,            # 自 E1 信号日起 20 交易日内，否则记 0
    'reacc_aux': 'break_previous_high',   # 仅报告，不作筛选条件

    # -------- Entry（§24 / §25）
    'entry_offset': 1,               # 收盘出信号 -> 次日开盘成交
    'e1_name': 'Pullback Observation Entry',
    'e2_name': 'Re-acceleration Entry',

    # -------- 前向收益（§23）
    'horizons': (1, 3, 5, 10, 20),
    'primary_horizons': (3, 5, 10),

    # -------- 控制（§26 / §27）
    'mom_ctrl': ('mom20', 'mom60'),
    'ind_level': 'SW_L1',
    'ind_unknown': 'REPORT_SEPARATELY',   # §27 UNKNOWN 不删除样本

    # -------- Baseline / Null（§28 / §29 / §30 / §48）
    'baselines': ('B1_AllStocks', 'B2_StrongTrend', 'B3_TrendRandomPullback',
                  'B4_MomentumMatched', 'B5_IndustryMomentumMatched'),
    'core_counterfactual': 'B3_TrendRandomPullback',
    'match_keys': ('mom20', 'total_mv', 'ind_l1', 'rv_20'),   # §30 匹配维度
    'null_models': ('N1_RandomStock', 'N2_RandomDate', 'N3_RandomPullback',
                    'N4_MomentumMatched', 'N5_IndustryMomentumMatched',
                    'N6_ShuffledPullbackQuality'),

    # -------- Regime（§42）
    'regime_def': 'HS300 vs MA200 & 60D momentum',
    'regime': ('BEAR', 'NORMAL', 'BULL'),

    # -------- 参数扰动邻域（§43，只允许在此范围内）
    'grid_depth': (0.02, 0.03, 0.05, 0.08),
    'grid_duration': (2, 3, 5, 8),
    'grid_mom_fast': (18, 20, 22),
    'grid_mom_slow': (55, 60, 65),

    # -------- 成本（§44）
    'cost_bps': (0, 10, 20, 30, 50),
    'cost_focus_bps': 30,

    # -------- 尾部（§46）
    'tail_pcts': (0.01, 0.05, 0.10),

    # -------- 多重检验（§50）
    'fdr_alpha': 0.05,
    'fdr_families': ('horizon', 'feature', 'quantile', 'window', 'model'),

    # -------- 随机化（§48 / §49）
    'seed': 20260926,
    'n_sim': 1000,
    'n_perm': 1000,

    # -------- Gate 判定阈值（§51 / §52 / §53）
    'momentum_threshold': 0.0,        # G4/G18：momentum-adjusted alpha 必须 > 0
    'ic_min': 0.02,                   # G12：|IC| 下限
    'auc_min': 0.52,                  # G12：AUC 下限
    'prec10_min': 0.10,               # G12：Precision@10 下限
    'monotonic_required': True,       # G12：分位须单调，否则 LOW_DISCRIMINATION
    'wf_pos_frac': 0.5,               # G8：Walk-Forward 须多数窗口为正（严格 > 1/2）
    'year_pos_frac': 0.6,             # G9：跨年份为正比例下限
    'regime_flip_max': 1,             # G10：方向翻转次数上限，超出记 REGIME_UNSTABLE
    'tail_leave_pct': 0.05,           # G13：Leave-Top5% 后 alpha 不得转负
    'perm_alpha': 0.05,               # G15
    'cost_require_positive': True,    # G14：30bp 后仍须具经济意义
    'n_min_signals': 100,             # 统计充分性下限，不足则如实标注而非静默

    # -------- 致命 Gate（§52）
    'lethal_gates': ('G1', 'G4', 'G6', 'G7', 'G8', 'G14', 'G18'),

    'trading_authorization': 'NO',   # §62 任何 PASS 只代表科研层 Alpha 验证
}


def prereg_hash() -> str:
    """预注册指纹（防篡改；写入面板 meta 与最终 registry）"""
    s = json.dumps(PREREG, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(s.encode('utf-8')).hexdigest()[:16]


def dump_prereg():
    fp = os.path.join(DATA, 'hp_prereg.json')
    d = dict(PREREG)
    d['_prereg_hash'] = prereg_hash()
    with open(fp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2, default=str)
    return fp


# ══════════════════════════════════════════════════════════════ 日志

def mklog(name):
    fp = os.path.join(OUTD, '_hp_%s.txt' % name)
    with open(fp, 'w', encoding='utf-8') as f:
        f.write('')

    def _log(msg=''):
        line = str(msg)
        print(line, flush=True)
        with open(fp, 'a', encoding='utf-8') as f:
            f.write(line + '\n')

    _log.sep = lambda ch='─', n=78: _log(ch * n)
    _log.path = fp
    return _log


# ══════════════════════════════════════════════════════════════ 面板 IO

_META_FP = os.path.join(PAN, '_meta.json')


def ppath(name):
    return os.path.join(PAN, name + '.npy')


def psave(name, arr):
    np.save(ppath(name), np.asarray(arr))


def pget(name, mmap=True):
    return np.load(ppath(name), mmap_mode='r' if mmap else None)


def pexists(name):
    return os.path.exists(ppath(name))


def psave_meta(d):
    with open(_META_FP, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2, default=str)


def pload_meta():
    with open(_META_FP, 'r', encoding='utf-8') as f:
        return json.load(f)


def psave_day(d):
    np.savez(os.path.join(PAN, '_day.npz'), **d)


def pload_day():
    return dict(np.load(os.path.join(PAN, '_day.npz'), allow_pickle=True))


# ══════════════════════════════════════════════════════════════ 分期

def phase_of(years):
    """按年返回分期编码 0=PRE 1=TRAIN 2=VALID 3=OOS 4=LIVE-LIKE"""
    y = np.asarray(years)
    out = np.zeros(len(y), dtype=np.int8)
    for i, (nm, (a, b)) in enumerate(PHASE_BOUNDS.items()):
        out[(y >= a) & (y <= b)] = i + 1
    return out


def phase_masks(years):
    p = phase_of(years)
    out = {'ALL': np.ones(len(p), dtype=bool)}
    for i, nm in enumerate(PHASES):
        out[nm] = (p == i + 1)
    return out


# ══════════════════════════════════════════════════════════════ 统计工具

def rank_avg(x):
    """平均秩（并列取平均），所有秩统计量的正确前提"""
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    if n == 0:
        return np.empty(0)
    o = np.argsort(x, kind='mergesort')
    xs = x[o]
    r = np.empty(n, dtype=np.float64)
    i = 0
    while i < n:
        j = i + 1
        while j < n and xs[j] == xs[i]:
            j += 1
        r[o[i:j]] = 0.5 * (i + 1 + j)
        i = j
    return r


def _spearman(a, b):
    ra = rank_avg(a)
    rb = rank_avg(b)
    ra = ra - ra.mean()
    rb = rb - rb.mean()
    d = math.sqrt(float((ra * ra).sum()) * float((rb * rb).sum()))
    return float((ra * rb).sum() / d) if d > 0 else np.nan


def daily_ic(sig, tgt, VAL, min_xs=30):
    """逐日横截面 Spearman IC。sig/tgt/VAL 形状 (NCODE, NCAL)。返回 (NCAL,)"""
    NCAL = sig.shape[1]
    out = np.full(NCAL, np.nan)
    for k in range(NCAL):
        m = VAL[:, k]
        if not m.any():
            continue
        s = np.asarray(sig[m, k], dtype=np.float64)
        t = np.asarray(tgt[m, k], dtype=np.float64)
        f = np.isfinite(s) & np.isfinite(t)
        if int(f.sum()) < min_xs:
            continue
        out[k] = _spearman(s[f], t[f])
    return out


def ic_stats(ic, lag=19, name=''):
    """IC 序列统计：均值 / ICIR / Newey-West t（滞后=lag）"""
    x = np.asarray(ic, dtype=np.float64)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 5:
        return {'name': name, 'n': n, 'mean': np.nan, 'icir': np.nan,
                't_nw': np.nan, 'pos_ratio': np.nan, 'std': np.nan}
    mu = float(x.mean())
    d = x - mu
    g0 = float((d * d).mean())
    s = g0
    L = int(min(lag, n - 2))
    for l in range(1, L + 1):
        gl = float((d[l:] * d[:-l]).mean())
        s += 2.0 * (1.0 - l / (L + 1.0)) * gl
    s = max(s, 1e-18)
    sd = float(x.std(ddof=1))
    return {'name': name, 'n': n, 'mean': mu, 'std': sd,
            'icir': mu / sd if sd > 0 else np.nan,
            't_nw': mu / math.sqrt(s / n),
            'pos_ratio': float((x > 0).mean())}


def auc_binary(sig, lab, VAL):
    """逐日二值标签 AUC（Mann-Whitney 秩和，并列取平均秩）。返回 (NCAL,)"""
    NCAL = sig.shape[1]
    out = np.full(NCAL, np.nan)
    for k in range(NCAL):
        m = VAL[:, k]
        if not m.any():
            continue
        s = np.asarray(sig[m, k], dtype=np.float64)
        y = np.asarray(lab[m, k], dtype=np.float64)
        f = np.isfinite(s) & np.isfinite(y)
        if f.sum() < 20:
            continue
        s, y = s[f], y[f]
        p = float((y == 1).sum())
        nn = float((y == 0).sum())
        if p <= 0 or nn <= 0:
            continue
        r = rank_avg(s)
        out[k] = float((r[y == 1].sum() - p * (p + 1) / 2.0) / (p * nn))
    return out


def auc_top(sig, tgt, VAL, top_pct=0.10):
    """逐日「目标前 top_pct」二值标签 AUC。返回 (NCAL,)"""
    NCAL = sig.shape[1]
    out = np.full(NCAL, np.nan)
    for k in range(NCAL):
        m = VAL[:, k]
        if not m.any():
            continue
        s = np.asarray(sig[m, k], dtype=np.float64)
        t = np.asarray(tgt[m, k], dtype=np.float64)
        f = np.isfinite(s) & np.isfinite(t)
        if f.sum() < 30:
            continue
        s, t = s[f], t[f]
        n = len(t)
        thr = np.quantile(t, 1.0 - top_pct)
        y = (t >= thr).astype(np.float64)
        p = float(y.sum())
        nn = float(n - p)
        if p <= 0 or nn <= 0:
            continue
        r = rank_avg(s)
        out[k] = float((r[y == 1].sum() - p * (p + 1) / 2.0) / (p * nn))
    return out


def bucket_profile(vals, tgt, VAL, nq=10):
    """逐日分位分桶后目标均值（日等权）。

    返回 (means, se, cnt)：means 长度 nq；se 为日间标准误
    """
    NCAL = vals.shape[1]
    acc = np.zeros((nq, NCAL))
    okd = np.zeros((nq, NCAL), dtype=bool)
    for k in range(NCAL):
        m = VAL[:, k]
        if not m.any():
            continue
        v = np.asarray(vals[m, k], dtype=np.float64)
        t = np.asarray(tgt[m, k], dtype=np.float64)
        f = np.isfinite(v) & np.isfinite(t)
        if f.sum() < nq * 3:
            continue
        v, t = v[f], t[f]
        n = len(v)
        r = rank_avg(v)
        b = np.minimum(nq - 1, ((r - 1.0) / n * nq).astype(np.int64))
        for q in range(nq):
            sel = (b == q)
            if sel.any():
                acc[q, k] = t[sel].mean()
                okd[q, k] = True
    means = np.full(nq, np.nan)
    se = np.full(nq, np.nan)
    cnt = okd.sum(1)
    for q in range(nq):
        v = acc[q, okd[q]]
        if len(v):
            means[q] = v.mean()
            se[q] = v.std(ddof=1) / math.sqrt(len(v)) if len(v) > 1 else np.nan
    return means, se, cnt


def monotonicity(means):
    """分位均值序列的单调性：与桶序号的 Spearman 相关 + 线性斜率归一化"""
    m = np.asarray(means, dtype=np.float64)
    f = np.isfinite(m)
    if f.sum() < 3:
        return np.nan, np.nan
    idx = np.arange(len(m))[f]
    rho = _spearman(idx.astype(np.float64), m[f])
    den = float(np.abs(m[f]).mean())
    slope = float(np.polyfit(idx, m[f], 1)[0]) / den if den > 0 else np.nan
    return rho, slope


def bh_fdr(pvals, alpha=0.05):
    """Benjamini-Hochberg。返回 (reject, q)"""
    p = np.asarray(pvals, dtype=np.float64)
    n = len(p)
    if n == 0:
        return np.empty(0, dtype=bool), np.empty(0)
    o = np.argsort(p)
    ranked = p[o]
    q = ranked * n / (np.arange(n) + 1.0)
    q = np.minimum.accumulate(q[::-1])[::-1]
    q = np.clip(q, 0, 1)
    out = np.empty(n)
    out[o] = q
    return out <= alpha, out


def qstats(x):
    """基础分布统计"""
    x = np.asarray(x, dtype=np.float64)
    x = x[np.isfinite(x)]
    if len(x) == 0:
        return {}
    return {'n': int(len(x)), 'mean': float(x.mean()), 'std': float(x.std(ddof=1)),
            'p05': float(np.quantile(x, 0.05)), 'p25': float(np.quantile(x, 0.25)),
            'p50': float(np.quantile(x, 0.50)), 'p75': float(np.quantile(x, 0.75)),
            'p95': float(np.quantile(x, 0.95))}


def roll_slope(s, w):
    """滚动线性回归斜率（x = 0..w-1；窗口须完整有限）"""
    v = np.asarray(s, dtype=np.float64)
    n = len(v)
    out = np.full(n, np.nan)
    if n < w:
        return out
    x = np.arange(w, dtype=np.float64)
    xd = x - x.mean()
    den = float((xd * xd).sum())
    sw = np.lib.stride_tricks.sliding_window_view(v, w)
    ok = np.isfinite(sw).all(1)
    if ok.any():
        out[w - 1:][ok] = (sw[ok] @ xd) / den
    return out


def nw_tstat(x, lag=None):
    """一般序列的 Newey-West t（用于日度序列均值检验）"""
    return ic_stats(x, lag if lag is not None else 5)['t_nw']


def zscore_cs(a, VAL):
    """逐日横截面标准化（用于匹配前的距离度量）"""
    out = np.full_like(np.asarray(a, dtype=np.float64), np.nan)
    NCAL = out.shape[1]
    for k in range(NCAL):
        m = VAL[:, k]
        if not m.any():
            continue
        v = np.asarray(a[m, k], dtype=np.float64)
        f = np.isfinite(v)
        if f.sum() < 5:
            continue
        mu = v[f].mean()
        sd = v[f].std(ddof=1)
        if sd > 0:
            out[m, k] = (np.asarray(a[:, k], dtype=np.float64) - mu) / sd
    return out
