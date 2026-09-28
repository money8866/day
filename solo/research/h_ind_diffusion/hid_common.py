# -*- coding: utf-8 -*-
"""
H-IND-DIFFUSION-01  预注册常量（冻结）与通用统计工具

隔离声明（§0 / §60）：
    本模块自包含，不 import 任何既有研究模块（TL-01 / H-TREND-PULLBACK / HVT / ARCHIVE / H-EARN-*）。
    统计工具为通用实现，与任何历史研究结论文档无耦合。
    历史研究结果（TL-01 FAIL / H-TREND-PULLBACK FAIL）仅作背景，**不作为本研究的先验**（§1）。

预注册纪律（§12 / §33 / §37 / §51 / §54 / §59）：
    1. 所有阈值、窗口、定义、分期、Gate 判据在本文件冻结；试验过程中不得修改。
    2. 参数扰动只允许在 PREREG['grid_*'] 声明的邻域内进行，且必须全报（§37）。
    3. 禁止事后择优：窗口 / 行业 / 年份 / regime / horizon / 分位点（§59）。
    4. 任何失败 Gate 不得通过改变阈值救回（§51 / §54）。

权威来源：research/h_ind_diffusion/SPEC.md（用户提交规格 §0–§61 逐字存档）。
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
OUTD = os.path.join(HERE, 'out')
CD = r'D:\mystock\cache_daily'
# 共享 Tushare 面板（§5：优先使用现有 Cache，不得重复下载）
FS_DATA = os.path.abspath(os.path.join(HERE, '..', 'fundamental_surprise_alpha', 'data'))

for _d in (DATA, OUTD):
    os.makedirs(_d, exist_ok=True)


# ══════════════════════════════════════════════════════════════ Gate 定义（§50 / §51）

GATES = {
    'G1':  {'name': 'PIT / Data Integrity',       'lethal': True,
            'criterion': '无未来函数：全部 rolling min_periods=w，特征时点 <= 决策日，面板与 meta 一致；违规检出数 = 0'},
    'G2':  {'name': 'Industry Mapping',           'lethal': False,
            'criterion': 'SW L1 PIT 覆盖率 >= 0.90（in_date/out_date 生效），UNKNOWN 计数如实披露（不删除样本）'},
    'G3':  {'name': 'Tradability',                'lethal': False,
            'criterion': 'T+1 开盘可成交率 >= 0.95（非停牌、非涨跌停封死）'},
    'G4':  {'name': 'Momentum Control',           'lethal': True,
            'criterion': '控制 Stock Momentum 后 Breadth 增量 alpha > 0 且 t_NW > 0（M2 - M1）'},
    'G5':  {'name': 'Industry Momentum Control',  'lethal': True,
            'criterion': '控制 Industry Momentum 后 Diffusion 增量 alpha > 0 且 t_NW > 0（M3 - M2）'},
    'G6':  {'name': 'Market Control',             'lethal': False,
            'criterion': '控制 Market Return / Market Breadth / Market Momentum 后 Diffusion beta > 0 且 t_NW > 0'},
    'G7':  {'name': 'Random Industry-Date Null',  'lethal': False,
            'criterion': '观测 alpha > N1(Random Industry-Date) null 且单侧 p < perm_alpha'},
    'G8':  {'name': 'Momentum-Matched Null',      'lethal': True,
            'criterion': '观测 alpha > N3/N4(Momentum-Matched) null 且单侧 p < perm_alpha'},
    'G9':  {'name': 'OOS',                        'lethal': True,
            'criterion': 'OOS 期 IC > 0 且 t_NW > 0（分位价差 Q5-Q1 > 0 一致）'},
    'G10': {'name': 'Walk Forward',               'lethal': False,
            'criterion': '>= 6 windows，正窗口比例 > wf_pos_frac（严格多数）'},
    'G11': {'name': 'Cross-Year',                 'lethal': False,
            'criterion': '正年份比例 >= year_pos_frac 且单一年份贡献占比 <= year_max_share'},
    'G12': {'name': 'Regime',                     'lethal': False,
            'criterion': 'BULL/NORMAL/BEAR 三分组符号翻转次数 <= regime_flip_max（禁止只选有效 regime）'},
    'G13': {'name': 'OOS Discrimination',         'lethal': True,
            'criterion': 'OOS |IC| >= ic_min 且 AUC >= auc_min 且 Prec@10 >= prec10_min 且分位单调'},
    'G14': {'name': 'Parameter Stability',        'lethal': False,
            'criterion': '参数面非单点峰值：邻域内同号比例 >= param_same_sign_frac，否则 PARAMETER_FRAGILE'},
    'G15': {'name': 'Tail Stability',             'lethal': False,
            'criterion': 'Leave-Top5% 后 alpha 仍 > 0，否则 EXTREME_TAIL_DEPENDENT'},
    'G16': {'name': 'Cost',                       'lethal': True,
            'criterion': '30bp 往返成本后 Net Alpha > 0，否则 COST_FAIL'},
    'G17': {'name': 'Permutation',                'lethal': False,
            'criterion': 'Diffusion 信号置换检验单侧 p < perm_alpha（n_perm 次）'},
    'G18': {'name': 'Multiple Testing',           'lethal': False,
            'criterion': '关键检验经 BH-FDR(alpha=fdr_alpha) 后仍显著（族内 + 合并池）'},
    'G19': {'name': 'Economic Significance',      'lethal': False,
            'criterion': '净收益幅度 >= econ_min_net（预注册最低经济意义门槛），非仅统计显著'},
    'G20': {'name': 'Incremental Alpha',          'lethal': True,
            'criterion': 'Diffusion 策略相对 B4(Industry Mom + Stock Mom) 增量 alpha > 0 且 t_NW > 0（§44）'},
}


# ══════════════════════════════════════════════════════════════ 预注册

PHASES = ['TRAIN', 'VALID', 'OOS', 'LIVE-LIKE']
PHASE_BOUNDS = {'TRAIN': (2018, 2022), 'VALID': (2023, 2024),
                'OOS': (2025, 2025), 'LIVE-LIKE': (2026, 2026)}

PREREG = {
    'hypothesis_id': 'H-IND-DIFFUSION-01',
    'title': 'Industry Strength Diffusion -> Stock Continuation',
    'version': '1.0',
    'note': '所有取值在跑数之前冻结；试验中不得修改（§59）',
    'spec_ref': 'research/h_ind_diffusion/SPEC.md',

    # -------- 数据源（§5）
    'data_source': 'fs_shared_panel(calendar/price_panel/basic_panel/index_panel) + cache_daily(stock_basic/industry/namechange)',
    'no_redownload': True,

    # -------- Universe（§6）
    'min_listing_days': 60,
    'exclude_st': True,             # 时点 ST/*ST（namechange PIT）
    'exclude_delist': True,         # 退市整理
    'exclude_suspend': True,        # 停牌/无成交（vol <= 0）
    'exclude_bj': True,             # 北交所
    'require_key_data': True,       # 关键数据缺失（MA20/MA60 不可得）

    # -------- 分期（§33 / §35）
    'phases': PHASE_BOUNDS,
    'year_report': (2021, 2022, 2023, 2024, 2025, 2026),

    # -------- 行业（§5 / §7 / §40）
    'ind_level_primary': 'SW_L1',        # 主口径（用户 20260927 确认）
    'ind_level_aux': 'SW_L2',            # 稳健性抽检
    'ind_unknown': 'REPORT_SEPARATELY',  # UNKNOWN 不删除样本
    'ind_n_min': 20,                     # §7 / §40 硬门槛
    'ind_n_alt': (30, 50),               # §40 另测
    'low_sample_flag': 'LOW_SAMPLE',

    # -------- 基础股票信号（§8，控制变量）
    'ret_wins': (1, 3, 5, 10, 20, 60),
    'ma_wins': (5, 10, 20, 60),

    # -------- Industry Momentum（§9）
    'ind_mom_wins': (5, 10, 20, 60),
    'ind_mom_methods': ('EW', 'VW', 'MED'),

    # -------- Breadth（§10 / §13）
    'breadth_wins': (1, 3, 5, 10, 20),
    'breadth_state_win': 20,             # Breadth20 = §10 核心变量（状态划分用）
    'breadth_layers': ('UP', 'MA20', 'MA60', 'RET5', 'RET20', 'RS'),

    # -------- Breadth Change（§11）
    'breadth_chg_wins': (1, 3, 5, 10),
    'breadth_chg_main': 5,               # 主方向变量 BreadthChange5

    # -------- Breadth Acceleration（§12，预注册固定，不得事后选窗口）
    'accel_short': 3,                    # (B_t - B_t-3)
    'accel_long': (5, 10),               # (B_t-5 - B_t-10)

    # -------- Leader Breadth / Concentration（§14 / §15 / §16）
    'leader_pcts': (0.05, 0.10, 0.20),
    'leader_pct_main': 0.20,
    'narrow_gap_thr': 0.10,              # NARROW_LEADERSHIP 判定：LeaderBreadth - OverallBreadth >= thr 且 Overall 未扩散

    # -------- Dispersion（§17）
    'disp_wins': (1, 3, 5, 10),
    'disp_pctl': (90, 10),

    # -------- 扩散状态（§19，最多 4 态）
    'states': ('D1', 'D2', 'D3', 'D4'),
    'state_split': 'SAME_DATE_CROSS_INDUSTRY_MEDIAN',   # Low/High 划分口径（相对，避免 regime 漂移）

    # -------- 四象限（§20）
    'quadrants': ('A', 'B', 'C', 'D'),

    # -------- 前向收益（§21 / §0）
    'horizons': (1, 3, 5, 10, 20),
    'primary_horizons': (3, 5, 10),
    'entry_offset': 1,                   # 收盘出信号 -> 次日开盘成交
    'target_main': 'exe',                # exe_H = close[k+H]/open[k+1]-1（主口径）；raw_H 仅描述

    # -------- 分位（§28）
    'n_quantiles': 5,

    # -------- 判别（§30）
    'auc_labels': ('gt0', 'gt_ind_median'),
    'prec_top_pct': 0.10,

    # -------- 模型阶梯（§22）
    'model_ladder': {'M0': ('stock_mom',),
                     'M1': ('stock_mom', 'ind_mom'),
                     'M2': ('stock_mom', 'ind_mom', 'ind_breadth'),
                     'M3': ('stock_mom', 'ind_mom', 'ind_breadth', 'diffusion')},
    'fm_x': ('diffusion', 'stock_mom', 'ind_mom', 'size', 'liquidity', 'volatility'),

    # -------- Null（§26）
    'null_models': ('N1_RandomIndustryDate', 'N2_RandomWithinIndustryStock',
                    'N3_MomentumMatched', 'N4_IndustryMomentumMatched',
                    'N5_BreadthShuffled'),

    # -------- 反事实（§27）
    'counterfactual': 'StrongMom+BreadthExpansion vs StrongMom+NoExpansion',

    # -------- Baseline（§44）
    'baselines': ('B1_Market', 'B2_IndustryMomentum', 'B3_StockMomentum',
                  'B4_IndustryMom+StockMom'),

    # -------- Regime（§36）
    'regime_def': 'HS300 vs MA200 & 60D momentum',
    'regime': ('BEAR', 'NORMAL', 'BULL'),

    # -------- 参数扰动邻域（§37 / §38，只允许在此范围内）
    'grid_breadth_win': (3, 5, 10, 20),
    'grid_mom_win': (10, 20, 40),
    'grid_ind_n': (15, 20, 30),
    'grid_leader': (0.10, 0.20),

    # -------- 成本（§41）
    'cost_bps': (0, 10, 20, 30, 50),
    'cost_focus_bps': 30,                # 往返（round-trip）口径
    'cost_require_positive': True,

    # -------- 尾部（§39）
    'tail_pcts': (0.01, 0.05, 0.10),
    'tail_leave_pct': 0.05,              # 主判定 Leave-Top5%

    # -------- 多重检验（§46）
    'fdr_alpha': 0.05,
    'fdr_families': ('feature', 'window', 'horizon', 'quantile', 'model', 'regime'),

    # -------- 随机化（§45）
    'seed': 20260927,
    'n_sim': 1000,
    'n_perm': 1000,

    # -------- Gate 判定阈值（§50 / §51 / §52）
    'momentum_threshold': 0.0,           # G4/G5/G19：增量 alpha 必须 > 0
    'ind_coverage_min': 0.90,            # G2
    'tradable_min': 0.95,                # G3
    'ic_min': 0.02,                      # G13
    'auc_min': 0.52,                     # G13
    'prec10_min': 0.10,                  # G13
    'monotonic_required': True,          # §28 / §29：不单调记 LOW_DISCRIMINATION
    'wf_pos_frac': 0.5,                  # G10（严格 > 1/2）
    'year_pos_frac': 0.60,               # G11
    'year_max_share': 0.50,              # G11：单一年份贡献上限
    'regime_flip_max': 1,                # G12
    'param_same_sign_frac': 0.60,        # G14
    'perm_alpha': 0.05,                  # G7 / G8 / G17
    'econ_min_net': 0.0020,              # G19：30bp 后净收益下限（单笔，预注册）
    'n_min_signals': 100,                # 统计充分性下限，不足如实标注而非静默

    # -------- 致命 Gate（§51）
    'lethal_gates': ('G1', 'G4', 'G5', 'G8', 'G9', 'G13', 'G16', 'G20'),

    'trading_authorization': 'NO',       # §61
}


# ══════════════════════════════════════════════════════════════ 派生定义冻结（P2）
# 与 PREREG 分离存放：PREREG 已在 P1 冻结（其 hash 写入 P1 meta），此处新增的是
# §14/§16/§17/§18/§19/§20 所需的「口径唯一化」定义，在 P2 跑数之前冻结。
# 目的：杜绝「同一概念多口径、事后择优」（§59）。任何改动都会改变 derived_hash。

DERIVED = {
    'frozen_at': 'P2 (before any IC / quantile / gate computation)',
    'leader_rank_mom': 'ret_20',        # §14：行业内部龙头排序口径（行业内降序）
    'leader_cond': 'ret_5 > 0',         # §14：LeaderBreadth 的强度条件。
                                        #   必须与排序窗口不同，否则 Top-p 恒等于 1（退化）
    'leader_pct_main': 0.20,            # §14 主口径
    'gap_overall_cond': 'ret_5 > 0',    # §16：OverallBreadth 条件，与 leader 相同以保证可比
    'bottom_cond': 'ret_5 > 0',         # §16：Top20% - Bottom80% 口径
    'rs_ref': 'industry EW',            # §18：超额收益参照（行业等权）
    'rs_win_main': 20,                  # §18：RS_Breadth 主窗口 / RS_BreadthChange 基准
    'rs_chg_win': 5,                    # §18：RS_BreadthChange 的差分窗口
    'disp_wins': (1, 3, 5, 10),         # §17
    'disp_band': (10, 90),              # §17：P90 - P10
    'state_breadth_win': 20,            # §19：Low/High 用 Breadth20
    'state_chg_win': 5,                 # §19：Rising/Falling 用 ΔBreadth5
    'state_split': 'SAME_DATE_CROSS_INDUSTRY_MEDIAN',   # 相对口径，避免 regime 漂移
    'quad_mom_win': 20,                 # §20：Industry Momentum 用 EW20
    'quad_chg_win': 5,                  # §20：Breadth Change 用 ΔBreadth5
    'narrow_rule': '(diff_gap >= narrow_gap_thr) & (dchg_5 <= 0)',
    'diffusion_raw': ('dchg_5', 'accel20', 'lb_020', 'rsb_chg_5'),
    'diffusion_score_components': ('pct_dchg_5', 'pct_accel20', 'pct_lb_020',
                                   'pct_rsb_chg_5'),
    'diffusion_score_weights': 'equal (1/4 each)',   # 不做任何权重搜索
    # ---- §21 / §28 个股层暴露（P3 冻结）----
    'stock_rs': 'rs_20 = ret_20 - mom_ew_20(所属行业)（§18 个股相对行业等权的超额）',
    'stock_exposure': 'exposure = 0.5 * pct_ind(diffusion_score) '
                      '+ 0.5 * pct_within_industry(rs_20)（等权，不做权重搜索）',
    'stock_exposure_scope': 'L1 主口径；暴露仅在 valid 且行业 eligible 且两分量均可得时定义',
    'stock_rs_min_n': 5,              # 行业内部百分位秩的最小成员数
    'quantile_sort_industry': '每日按 diffusion_score 对 eligible 行业排序 -> Q1..Q5',
    'quantile_sort_stock': '每日在每个行业内部按 exposure 排序 -> Q1..Q5（§28 行业内部排序）',
    'median_convention': 'np.nanmedian / np.nanpercentile（线性插值）',
    'rank_convention': '行业内按 ret_20 降序的序数秩（np.argsort，stable），'
                       '龙头集合 = 秩 >= n - ceil(p*n)（p<1 时至少 1 只）；'
                       '连续型动量下并列可忽略',
    'eligible_rule': '行业 N(有效且 ret_20 可得) >= ind_n_min(20) 才进入横截面排名与状态划分',
}


def derived_hash() -> str:
    s = json.dumps(DERIVED, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(s.encode('utf-8')).hexdigest()[:16]


def prereg_hash() -> str:
    """预注册指纹（防篡改；写入 meta 与最终 JSON）"""
    s = json.dumps(PREREG, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(s.encode('utf-8')).hexdigest()[:16]


def dump_prereg():
    fp = os.path.join(DATA, 'hid_prereg.json')
    d = dict(PREREG)
    d['_gates'] = GATES
    d['_prereg_hash'] = prereg_hash()
    with open(fp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2, default=str)
    return fp


# ══════════════════════════════════════════════════════════════ 日志

def mklog(name):
    fp = os.path.join(OUTD, '_hid_%s.txt' % name)
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


# ══════════════════════════════════════════════════════════════ 基础统计工具

def rank_avg(x):
    """平均秩（并列取平均），所有秩统计量的正确前提（1-based）

    向量化实现（np.unique 排序 + 累积计数），与朴素「逐个并列组」写法
    语义完全一致：无并列 → 1..n；有并列 → 组内取平均秩。
    """
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    if n == 0:
        return np.empty(0)
    if n == 1:
        return np.ones(1, dtype=np.float64)
    u, inv, cnt = np.unique(x, return_inverse=True, return_counts=True)
    inv = np.asarray(inv).reshape(-1)
    if len(u) == n:
        r = np.empty(n, dtype=np.float64)
        r[np.argsort(x, kind='mergesort')] = np.arange(1, n + 1, dtype=np.float64)
        return r
    cs = np.cumsum(cnt.astype(np.float64))
    start = cs - cnt                      # 该组之前的元素个数
    avg = start + (cnt.astype(np.float64) + 1.0) / 2.0   # 1-based 平均秩
    return avg[inv]


def _spearman(a, b):
    ra = rank_avg(a)
    rb = rank_avg(b)
    ra = ra - ra.mean()
    rb = rb - rb.mean()
    d = math.sqrt(float((ra * ra).sum()) * float((rb * rb).sum()))
    return float((ra * rb).sum() / d) if d > 0 else np.nan


def _pearson(a, b):
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    a = a - a.mean()
    b = b - b.mean()
    d = math.sqrt(float((a * a).sum()) * float((b * b).sum()))
    return float((a * b).sum() / d) if d > 0 else np.nan


def nw_tstat(x, lag=None):
    """序列均值的 Newey-West t（日度 IC 序列等）"""
    return ic_stats(x, lag if lag is not None else 5)['t_nw']


def ic_stats(ic, lag=19, name=''):
    """IC 序列统计：均值 / ICIR / Newey-West t / 正值占比"""
    x = np.asarray(ic, dtype=np.float64)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 5:
        return {'name': name, 'n': n, 'mean': np.nan, 'icir': np.nan,
                't_nw': np.nan, 'pos_ratio': np.nan, 'std': np.nan}
    mu = float(x.mean())
    d = x - mu
    s = float((d * d).mean())
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


def _auc_from_scores(s, y):
    """Mann-Whitney AUC（并列平均秩），s 越大越可能 y=1"""
    p = float((y == 1).sum())
    nn = float((y == 0).sum())
    if p <= 0 or nn <= 0:
        return np.nan
    r = rank_avg(s)
    return float((r[y == 1].sum() - p * (p + 1) / 2.0) / (p * nn))


def auc_binary(sig, lab, VAL, min_n=20):
    """逐日二值标签 AUC。返回 (NCAL,)"""
    NCAL = sig.shape[1]
    out = np.full(NCAL, np.nan)
    for k in range(NCAL):
        m = VAL[:, k]
        if not m.any():
            continue
        s = np.asarray(sig[m, k], dtype=np.float64)
        y = np.asarray(lab[m, k], dtype=np.float64)
        f = np.isfinite(s) & np.isfinite(y)
        if f.sum() < min_n:
            continue
        out[k] = _auc_from_scores(s[f], y[f])
    return out


def bucket_profile_1d(vals, tgt, nq=5):
    """单日分位分桶均值。返回 (means, cnt)"""
    f = np.isfinite(vals) & np.isfinite(tgt)
    v, t = vals[f], tgt[f]
    means = np.full(nq, np.nan)
    if len(v) < nq * 3:
        return means, np.zeros(nq, dtype=int)
    r = rank_avg(v)
    b = np.minimum(nq - 1, ((r - 1.0) / len(v) * nq).astype(np.int64))
    cnt = np.zeros(nq, dtype=int)
    for q in range(nq):
        sel = (b == q)
        cnt[q] = int(sel.sum())
        if sel.any():
            means[q] = t[sel].mean()
    return means, cnt


def monotonicity(means):
    """分位均值序列单调性：与桶序号 Spearman + 归一化斜率"""
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


def zscore_cs(a, VAL):
    """逐日横截面标准化（匹配前的距离度量用）"""
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


# ══════════════════════════════════════════════════════════════ 面板结构工具（P2-P7 共用）

def rows_by_code(ind, n_codes):
    """每个行业代码出现过的股票行（避免对 (NCODES, NCAL) 做全量 == 扫描）"""
    d = {}
    for i in range(n_codes):
        for c in np.unique(ind[i]):
            c = int(c)
            if c > 0:
                d.setdefault(c, []).append(i)
    return {c: np.asarray(v, dtype=np.int64) for c, v in d.items()}


def code_grid(df, col, n_codes, NCAL, key='ind_code', kcol='k'):
    """行业级长表 -> (n_codes + 1, NCAL) 网格；行 0 = UNKNOWN（全 NaN）。

    行业编码约定（P1）：1..n_codes 连续，0 = UNKNOWN。
    """
    A = np.full((n_codes + 1, NCAL), np.nan, dtype=np.float64)
    if col is None or col not in df.columns:
        return A
    ci = df[key].values.astype(np.int64)
    ki = df[kcol].values.astype(np.int64)
    ok = (ci >= 0) & (ci <= n_codes) & (ki >= 0) & (ki < NCAL)
    A[ci[ok], ki[ok]] = np.asarray(df[col].values[ok], dtype=np.float64)
    return A


def map_grid(A, ind):
    """(n_codes + 1, NCAL) 网格按行业归属映射到 (NCODES, NCAL)（float64）

    逐列取 A[ind[i, k], k]；用 for-each-row/col 的高级索引实现，避免
    A[ind]（对 2D 索引会按行展开成 (NCODES, NCAL, NCAL)）。
    """
    A = np.asarray(A, dtype=np.float64)
    ind = np.asarray(ind, dtype=np.int64)
    NCAL = A.shape[1]
    if A.shape[0] == 0:
        return np.full(ind.shape, np.nan, dtype=np.float64)
    cols = np.arange(NCAL, dtype=np.int64)
    rows = np.clip(ind, 0, A.shape[0] - 1)
    out = A[rows, cols]
    return np.where((ind >= 0) & (ind < A.shape[0]), out, np.nan)


def within_group_pct(X, ind, VAL, min_n=5):
    """行业内百分位秩（0~1，仅 VAL 内且 X 有限者参与），逐日独立。返回 (NCODES, NCAL)

    ind 可为 (NCODES,) 或 (NCODES, NCAL)（行业归属逐日固定时用前者）。
    """
    X = np.asarray(X, dtype=np.float64)
    NCODES, NCAL = X.shape
    ind = np.asarray(ind, dtype=np.int64)
    if ind.ndim == 1:
        ind = np.broadcast_to(ind[:, None], (NCODES, NCAL))
    out = np.full((NCODES, NCAL), np.nan, dtype=np.float64)
    rb = rows_by_code(ind, NCODES)
    for c, rows in rb.items():
        sub = (ind[rows, :] == c) & np.asarray(VAL[rows, :], dtype=bool)
        x = X[rows, :]
        sel = sub & np.isfinite(x)
        n = sel.sum(0)
        ok = n >= min_n
        if not ok.any():
            continue
        Xs = np.where(sel, x, np.inf)
        order = np.argsort(Xs, axis=0, kind='stable')
        rk = np.empty(order.shape, dtype=np.int64)
        vals = np.broadcast_to(np.arange(len(rows), dtype=np.int64)[:, None], order.shape)
        np.put_along_axis(rk, order, vals, axis=0)
        with np.errstate(invalid='ignore'):
            pct = rk / np.maximum(n, 1)
        blk = out[rows, :]
        blk[sel & ok] = pct[sel & ok]
        out[rows, :] = blk
    return out


# ══════════════════════════════════════════════════════════════ 回归 / Fama-MacBeth

def ols_beta(X, y):
    """最小二乘（含截距）。X (n, p)；返回 betas (p+1,) 含截距"""
    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    n, p = X.shape
    A = np.empty((n, p + 1))
    A[:, 0] = 1.0
    A[:, 1:] = X
    try:
        b, *_ = np.linalg.lstsq(A, y, rcond=None)
        return b
    except Exception:
        return np.full(p + 1, np.nan)


def ols_beta_se(X, y):
    """OLS betas + 经典标准误（同方差）"""
    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    n, p = X.shape
    A = np.empty((n, p + 1))
    A[:, 0] = 1.0
    A[:, 1:] = X
    try:
        b, *_ = np.linalg.lstsq(A, y, rcond=None)
        e = y - A @ b
        dof = max(n - p - 1, 1)
        s2 = float((e * e).sum()) / dof
        XtX_inv = np.linalg.pinv(A.T @ A)
        se = np.sqrt(np.maximum(np.diag(XtX_inv) * s2, 0.0))
        return b, se
    except Exception:
        return np.full(p + 1, np.nan), np.full(p + 1, np.nan)


def fama_macbeth(df, ycol, xcols, datecol='trade_date', min_n=30,
                 cond_col=None):
    """Fama-MacBeth：逐日横截面 OLS，再对 beta 序列做 NW t。

    df: 长表；cond_col 为可选的行业列 —— 指定时在每个 (date, industry) 内回归。
    返回 dict: betas(mean), t_nw, n_day, per-col
    """
    cols = [ycol] + list(xcols) + [datecol]
    if cond_col:
        cols = cols + [cond_col]
    d = df[cols].replace([np.inf, -np.inf], np.nan).dropna()
    if not len(d):
        return {'n_day': 0}
    keys = [datecol] + ([cond_col] if cond_col else [])
    acc = {c: [] for c in ('const',) + tuple(xcols)}
    for _, g in d.groupby(keys, sort=True):
        if len(g) < min_n:
            continue
        b = ols_beta(g[list(xcols)].values, g[ycol].values)
        if not np.isfinite(b).all():
            continue
        acc['const'].append(b[0])
        for i, c in enumerate(xcols):
            acc[c].append(b[i + 1])
    out = {'n_day': len(acc['const']), 'ycol': ycol, 'per': {}}
    for c, v in acc.items():
        st = ic_stats(np.asarray(v), lag=19)
        out['per'][c] = {'mean': st['mean'], 't_nw': st['t_nw'], 'n': st['n']}
    out['betas'] = {c: out['per'][c]['mean'] for c in out['per']}
    out['t_nw'] = {c: out['per'][c]['t_nw'] for c in out['per']}
    return out


# ══════════════════════════════════════════════════════════════ 面板 IO（P1 产出 / §57 落盘）

PANEL_DIR = os.path.join(DATA, 'panel')
META_FP = os.path.join(PANEL_DIR, '_meta.json')

# §57 要求的产物一律落在课题根目录
IND_FP = os.path.join(HERE, 'industry_daily_state.parquet')
FEAT_FP = os.path.join(HERE, 'industry_diffusion_features.parquet')
EXP_FP = os.path.join(HERE, 'stock_diffusion_exposure.parquet')


def psave_meta(d):
    os.makedirs(PANEL_DIR, exist_ok=True)
    with open(META_FP, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2, default=str)
    return META_FP


def pload_meta():
    with open(META_FP, 'r', encoding='utf-8') as f:
        return json.load(f)


def psave_meta_side(name, d):
    """阶段副产品元信息：写入 PANEL_DIR/<name>，**绝不覆盖 P1 的 _meta.json**
    （_meta.json 是面板维度的唯一权威来源：NCODES / NCAL / 行业字典）。"""
    os.makedirs(PANEL_DIR, exist_ok=True)
    fp = os.path.join(PANEL_DIR, name)
    with open(fp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2, default=str)
    return fp


def pload_meta_side(name, default=None):
    fp = os.path.join(PANEL_DIR, name)
    if not os.path.exists(fp):
        return default
    with open(fp, 'r', encoding='utf-8') as f:
        return json.load(f)


def pload(name):
    """只读加载 P1 面板列（memmap），形状 (NCODES, NCAL)"""
    return np.load(os.path.join(PANEL_DIR, name + '.npy'), mmap_mode='r')


def pload_day():
    """dates / year / month / phase / regime / bench_code / hs300"""
    return np.load(os.path.join(PANEL_DIR, '_day.npz'))


def pload_codes():
    """P1 落盘的股票代码列表（与 memmap 行序严格一致）。

    P1 由 pd.Categorical.categories 转出，dtype 为 object，故需 allow_pickle=True
    （本地可信产物，非外部输入）；统一在此收敛，避免各阶段各自处理。
    """
    return np.load(os.path.join(PANEL_DIR, '_codes.npy'), allow_pickle=True).astype(str)


def out_csv(name, df):
    fp = os.path.join(HERE, name)
    df.to_csv(fp, index=False, encoding='utf-8-sig')
    return fp
