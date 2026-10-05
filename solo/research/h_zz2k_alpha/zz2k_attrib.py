# -*- coding: utf-8 -*-
"""H-ZZ2K-A1 归因与因子重构实验 (研究 ID: H-ZZ2K-ATTRIB).

目的 (用户指令)
    以现有 zz2k_daily_UZZ2K_20260924.csv + 历史 Tushare 缓存为基础, 先验证
    A1 / A2 / C_A3 的真实贡献, 再决定是否 (1) 调整权重 (2) 增加趋势过滤
    (3) 重建股票覆盖机制. 禁止为了让 Top 20 看起来更强势而制造无统计依据的规则.

方法论硬约束 (继承 SPEC §0)
    trading_authorization = NO —— 不产 BUY/SELL, 不含仓位/止损/择时.
    本实验只做「归因 + 预注册决策」, 不修改 SPEC, 不改 zz2k_daily.py.
    所有候选方案与判据写在代码顶部 PRE_REG 常量中, 且在 main() 第一步先打印,
    之后才做任何计算 —— 结果无法反向影响规则定义.

运行
    python -u zz2k_attrib.py

产物 (out/)
    zz2k_attrib_groups.csv       组级 IC (C_k / Cperp_k, 三段 + 全样本 + dedup)
    zz2k_attrib_composites.csv   候选权重方案的 IC / 分层 / 覆盖 / Top20 重合
    zz2k_attrib_incremental.csv  common-sample 嵌套回归 ΔR² (公平归因)
    zz2k_attrib_coverage.csv     覆盖机制对比
    zz2k_attrib_filters.csv      趋势过滤对比
    zz2k_attrib_decision.json    三项决策的 Go/No-Go 与依据
"""
import os
import sys
import json
import time

import numpy as np
import pandas as pd

import zz2k_common as R
import zz2k_run as RUN

SEP = '\u2550' * 78
SUB = '\u2500' * 78

W_RM, W_VOL, W_VOL2 = 250, 60, 120
H = R.PRIMARY_H
LABEL = 'y_ex_zz'
SEGS = ('IS', 'VALID', 'OOS', 'ALL')
EVAL_SEGS = ('IS', 'VALID', 'OOS')
MAIN_POOL = 'U-ZZ2K'

# ================================================================ PRE-REG
# 冻结于运行之前. 以下内容先于任何计算打印到 zz2k_attrib.log.
PRE_REG = {
    'id': 'H-ZZ2K-ATTRIB',
    'goal': '验证 A1/A2/C_A3 真实贡献 -> 决定 权重 / 趋势过滤 / 覆盖机制',
    'scope': {
        'pools': ['U-ZZ2K(主)', 'U-PROXY(对照)'],
        'segs': {'IS': '20240201..20250630', 'VALID': '20250701..20251231',
                 'OOS': '20260101..20260924'},
        'horizon': H, 'label': LABEL,
        'decision_seg': 'IS (只在此段决策)',
        'confirm_segs': ['VALID', 'OOS (只做确认, 不参与选择)'],
    },
    'reused_thresholds': {
        'IC_T_MIN': R.IC_T_MIN, 'VALID_T_MIN': R.VALID_T_MIN,
        'DEDUP_IC_MIN': R.DEDUP_IC_MIN, 'IC_MEAN_MIN': R.IC_MEAN_MIN,
        'MONO_MIN': R.MONO_MIN, 'IC_MIN_N': R.IC_MIN_N, 'NW_LAG': R.NW_LAG,
    },
    'new_thresholds': {
        'DELTA_IC_MIN': 0.005,      # 权重方案相对现行的最小 IS IC 改善
        'COV_GAIN_MIN': 1.30,       # 覆盖重建的最小可用截面倍数
        'COV_IC_TOL': 0.002,        # 覆盖重建允许的 IC 让步
        'FILTER_SPREAD_MIN': 0.002,  # 趋势过滤后 net30 spread 的最小改善
    },
    # (A) 权重候选. leg 表述为 (面板, 权重). 'sgn' = 符号由 IS 段 IC 符号决定
    # (即: 承认预注册方向被数据推翻时, 允许在 IS 段翻转符号; VALID/OOS 只做确认)
    'weight_candidates': {
        'W0_curr':     {'desc': '现行: Cperp_A1 .25 + Cperp_A2 .75',
                        'legs': [('Cperp', 'A1', 0.25), ('Cperp', 'A2', 0.75)]},
        'W1_eq':       {'desc': 'A1/A2 等权',
                        'legs': [('Cperp', 'A1', 0.50), ('Cperp', 'A2', 0.50)]},
        'W2_a2only':   {'desc': '仅 A2',
                        'legs': [('Cperp', 'A2', 1.00)]},
        'W3_a1only':   {'desc': '仅 A1',
                        'legs': [('Cperp', 'A1', 1.00)]},
        'W4_curr_pA3': {'desc': '现行 + 符号化 Cperp_A3 (0.25)',
                        'legs': [('Cperp', 'A1', 0.25), ('Cperp', 'A2', 0.75),
                                 ('Cperp', 'A3', 0.25, 'sgn')]},
        'W5_curr_pA34': {'desc': '现行 + 符号化 Cperp_A3/A4 (0.25/0.20)',
                         'legs': [('Cperp', 'A1', 0.25), ('Cperp', 'A2', 0.75),
                                  ('Cperp', 'A3', 0.25, 'sgn'),
                                  ('Cperp', 'A4', 0.20, 'sgn')]},
        'W6_curr_pA345': {'desc': '现行 + 符号化 Cperp_A3/A4/A5',
                          'legs': [('Cperp', 'A1', 0.25), ('Cperp', 'A2', 0.75),
                                   ('Cperp', 'A3', 0.25, 'sgn'),
                                   ('Cperp', 'A4', 0.20, 'sgn'),
                                   ('Cperp', 'A5', 0.15, 'sgn')]},
        'W7_all5_sgn': {'desc': '五组符号化等权 (对照 COMP_EQ)',
                        'legs': [('Cperp', 'A1', 0.20, 'sgn'),
                                 ('Cperp', 'A2', 0.20, 'sgn'),
                                 ('Cperp', 'A3', 0.20, 'sgn'),
                                 ('Cperp', 'A4', 0.20, 'sgn'),
                                 ('Cperp', 'A5', 0.20, 'sgn')]},
        'W8_curr_rawC': {'desc': '现行权重但用类内 C_k (不做类间正交)',
                         'legs': [('C', 'A1', 0.25), ('C', 'A2', 0.75)]},
    },
    'weight_rule': {
        'select': 'IS 段 IC_mean 最高',
        'go': [
            'IS: IC_mean >= W0_curr.IS + DELTA_IC_MIN (0.005)',
            'VALID: 与 IS 同号 且 |t| >= VALID_T_MIN',
            'OOS: 与 IS 同号 且 IC_mean >= W0_curr.OOS',
        ],
        'no_go': '任一不满足 -> 保持现行 W0_curr, 不因 IS 过拟合改权重',
    },
    # (B) 趋势过滤候选 (只允许时点可得变量)
    'filter_candidates': {
        'F0_none':  '不过滤 (基线)',
        'F1_ma20':  'close > MA20',
        'F2_slope': 'MA20 的 20 日斜率 > 0',
        'F3_ret20': '过去 20 日收益 > 0',
    },
    'filter_rule': {
        'go': [
            '过滤后三段的组合 IC_mean 均不低于未过滤 (容差 0)',
            '过滤后 IS net30 spread 提升 >= FILTER_SPREAD_MIN (0.002)',
            '过滤后三段 IC 与未过滤同号',
        ],
        'note': 'Top20 重合度只作描述, 不作筛选依据',
    },
    # (C) 覆盖机制候选 (cross_orth 的控制变量集合)
    'coverage_candidates': {
        'M_cur':  '现行: cross_orth(5 组 + M0) -> Cperp',
        'M_use':  'cross_orth(仅打分用 A1/A2 + M0)',
        'M_use3': 'cross_orth(A1/A2 + 符号化 A3 + M0)',
        'M_none': '不做类间正交, 直接用 C_k',
    },
    'coverage_rule': {
        'go': [
            'IS 平均可用截面 >= M_cur.IS * COV_GAIN_MIN (1.30)',
            '三段 IC_mean 均 >= M_cur 同段 - COV_IC_TOL (0.002)',
            '不引入新的 SIGN_CONFLICT',
        ],
    },
    'hard_rules': [
        '任何仅在 VALID/OOS 观察到、未在 IS 预注册的方案, 只记为探索性发现, 不得写成规则',
        'U-PROXY 只作稳健性对照: 其符号不得与 U-ZZ2K 的结论相反',
        'trading_authorization = NO',
    ],
}

# ================================================================ AMENDMENT
# v1 缺陷: 权重/覆盖的 IC 是在「各自的有效样本」上算的 —— W0_curr 只在
# 5 组同时有限的 ~627 只/日上评估, W8_curr_rawC 却在 ~1953 只/日上评估;
# 规则 'select: IS IC 最高' 把两者直接比大小, 属于样本选择效应, 与
# zz2k_incremental.csv 的 n 不等是同一类错误。候选与阈值一律不变。
AMENDMENT = {
    'id': 'H-ZZ2K-ATTRIB-v2',
    'reason': 'v1 权重/覆盖裁决存在跨样本 IC 比较 (W0 627 只/日 vs W8 1953 只/日)',
    'change': ('全部候选在同一评估域 um = u & (A1..A5 均有限) & (M0 有限) '
               '上重算三段 IC; 符号化腿的符号亦在该域上判定; '
               '权重裁决与覆盖裁决的 ic_ok 改用 *_cs 列; '
               '覆盖的 coverage_gain 仍读全域 IS_nmean (宽度即受测对象)'),
    'unchanged': ['9 个权重候选', '4 个覆盖候选', '4 个过滤候选',
                  'DELTA_IC_MIN=0.005', 'COV_GAIN_MIN=1.30',
                  'COV_IC_TOL=0.002', 'FILTER_SPREAD_MIN=0.002',
                  'IS 段决策 + VALID/OOS 仅确认', 'trading_authorization=NO'],
    'note': ('无 _cs 后缀的列 = v1 全域口径; 权重裁决不使用它 (跨样本不可比), '
             '覆盖裁决只用它的 nmean (数量), 不用它的 IC'),
}


# ================================================================ helpers
def comp_leg_panel(C, Cperp, kind, k):
    return (Cperp if kind == 'Cperp' else C)[k]


def is_segment_sign(pan, u, Rl, dates, pool, um=None):
    """仅用 IS 段 IC 符号决定方向 (预注册: 决策只看 IS)."""
    ue = u if um is None else (u & um)
    Rf = R.xs_rank_pct(pan, ue)
    ic, cnt = R.daily_ic(Rf, Rl, min_n=R.IC_MIN_N)
    r = R.seg_ic(ic, cnt, dates, pool, 'IS')
    v = r['IC_mean']
    return (1.0 if (not np.isfinite(v) or v >= 0) else -1.0)


def build_candidate(legs, C, Cperp, u, Rl, dates, pool, um=None):
    """按候选定义合成; 'sgn' 腿的符号由 IS 段 IC 决定. 返回 (panel, desc_signs)."""
    pans, ws, signs = {}, {}, {}
    for leg in legs:
        kind, k, w = leg[0], leg[1], leg[2]
        sgn_mode = (len(leg) > 3 and leg[3] == 'sgn')
        pan = comp_leg_panel(C, Cperp, kind, k)
        if sgn_mode:
            s = is_segment_sign(pan, u, Rl, dates, pool, um=um)
        else:
            s = 1.0
        pans['%s_%s' % (kind, k)] = pan * s
        ws['%s_%s' % (kind, k)] = w
        signs['%s_%s' % (kind, k)] = s
    pan, _ = R.composite_from_rank(pans, u, ws)
    return pan, signs


def seg_eval(pan, u, Rl, dates, pool, keep=None, dedup=True, lag=R.NW_LAG,
             um=None):
    """三段 + 全样本 IC/t, 以及 R-DEDUP 全样本 IC, 平均可用截面.
    um != None 时把评估域收敛到 um (同截面比较, v2 修正)."""
    ue = u if um is None else (u & um)
    Rf = R.xs_rank_pct(pan, ue)
    ic, cnt = R.daily_ic(Rf, Rl, min_n=R.IC_MIN_N, keep=keep)
    out = {}
    for s in SEGS:
        r = (R.ic_row(ic, cnt, lag=lag) if s == 'ALL'
             else R.seg_ic(ic, cnt, dates, pool, s, lag=lag))
        out[s] = {'ic': r['IC_mean'], 't': r['t_NW'], 'nd': r['n_days'],
                  'pos': r['pos_ratio']}
    # 平均可用截面 (按段)
    for s in EVAL_SEGS:
        m = R.seg_mask(dates, pool, s)
        c = cnt[m]
        out[s]['nmean'] = float(c[c > 0].mean()) if (c > 0).any() else np.nan
    if dedup:
        Rd = R.dedup_rank(pan, ue, gap=5, q=5)[1]
        dic, dcnt, _ = R.dedup_ic_rank(Rd, Rl, min_n=5)
        out['dedup_ALL'] = R.ic_row(dic, dcnt, lag=0)['IC_mean']
    else:
        out['dedup_ALL'] = np.nan
    return out


def qstat(pan, Yraw, u, dates, pool, seg, um=None):
    dm = None if seg == 'ALL' else R.seg_mask(dates, pool, seg)
    ue = u if um is None else (u & um)
    _, sp = R.quantile_stats(pan, Yraw, ue, horizon=H, q=5, day_mask=dm)
    return sp


def topk_codes(pan, u, t, codes, k=20):
    m = u[:, t] & np.isfinite(pan[:, t])
    idx = np.where(m)[0]
    if idx.size == 0:
        return []
    order = np.argsort(-pan[idx, t], kind='stable')
    return [codes[s] for s in idx[order][:k]]


def light_orth(p, Fp, u, M0, cov, lg=None):
    """pool_orth 轻量版: 去掉 gs_orth/诊断, 口径与 zz2k_run.pool_orth 一致."""
    C, gd = {}, {}
    for k in R.GROUPS:
        fk = {n: Fp[n] for n in R.GROUP_FACTORS[k]}
        keep, drop = R.orth_input(fk, {n: cov[n] for n in fk}, lg=None,
                                  tag='%s/%s' % (p, k))
        if not keep:
            C[k] = np.full(Fp[R.GROUP_FACTORS[k][0]].shape, np.nan, 'float32')
            gd[k] = {'keep': [], 'drop': drop}
            continue
        lo = R.lowdin(R.standardize(keep, u), u, lg=None)
        C[k] = R.group_composite(lo, u, lg=None, tag='%s/C_%s' % (p, k))
        gd[k] = {'keep': sorted(keep), 'drop': drop}
        if lg:
            lg('  组 %s: keep=%d drop=%d' % (k, len(keep), len(drop)))
    return C, gd


# ================================================================ main
def main():
    lg = R.Log('zz2k_attrib')
    lg(SEP)
    lg('H-ZZ2K-ATTRIB 归因与因子重构实验   trading_authorization=NO')
    lg(SEP)
    lg('【预注册协议 · 先于任何计算打印 · 冻结于运行前】')
    for line in json.dumps(PRE_REG, ensure_ascii=False,
                           indent=2).splitlines():
        lg('  ' + line)
    lg(SEP)
    lg('【修订协议 v2 · 同截面评估 · 先于任何计算打印】')
    for line in json.dumps(AMENDMENT, ensure_ascii=False,
                           indent=2).splitlines():
        lg('  ' + line)
    lg(SEP)

    if not R.spec_check(lg=lg):
        lg('EXIT 2 (SPEC 不一致)')
        return 2

    t00 = time.time()
    g = R.load_grid(lg=lg)
    dates = g['dates']
    codes = g['codes']
    basic = R.load_basic(g, lg=lg)
    bench = R.load_bench(g, lg=lg)
    ret = R.ret_panel(g)
    uni, _ = R.build_universe(g, basic, lg=lg)
    lab = R.build_labels(g, bench, uni, lg=lg)
    l1s, _ = R.industry_l1(g, lg=lg)

    # ---- 与过滤/描述相关的时点可得变量 ----
    c = g['close'].astype('float64')
    ma20 = R.roll_mean(c, 20).astype('float64')
    ma60 = R.roll_mean(c, 60).astype('float64')
    slope20 = np.full(c.shape, np.nan)
    slope20[:, 20:] = ma20[:, 20:] / ma20[:, :-20] - 1.0
    ret20 = np.full(c.shape, np.nan)
    ret20[:, 20:] = c[:, 20:] / c[:, :-20] - 1.0
    FILT = {
        'F1_ma20': np.isfinite(ma20) & (c > ma20),
        'F2_slope': np.isfinite(slope20) & (slope20 > 0),
        'F3_ret20': np.isfinite(ret20) & (ret20 > 0),
    }

    O = {'groups': [], 'comp': [], 'inc': [], 'cov': [], 'filt': []}
    DEC = {}

    pools = [p for p in R.POOLS if uni.get(p) is not None]
    for p in pools:
        lg(SEP)
        lg('口径 %s 归因' % p)
        u = uni[p]
        Yraw = lab[p][LABEL][H]

        # ---- 因子层 (复用缓存) ----
        Fp = {}
        for k in ('A1', 'A2', 'A4'):
            Fp.update(R.build_group(k, g, basic, bench, ret, u, l1s, lg=None,
                                    cache=True))
        Fp.update(RUN.build_A35(p, g, bench, basic, ret, u, l1s,
                                W_RM, W_VOL, W_VOL2, lg=None, cache=True))
        cov = R.factor_coverage(Fp, u, dates=dates)
        M0 = R.build_M0(g, basic, bench, ret, l1s, u, lg=lg)

        C, gd = light_orth(p, Fp, u, M0, cov, lg=lg)
        Cperp = R.cross_orth(C, M0, u, lg=lg)
        del Fp
        lg('正交层完成: C=%d Cperp=%d (%.1fs)'
           % (len(C), len(Cperp), time.time() - t00))

        # ---- v2 同截面评估域: 5 组 + M0 均有限 (即现行 cross_orth 的可用域) ----
        um = u.copy()
        for k in R.GROUPS:
            um &= np.isfinite(C[k])
        um &= np.isfinite(M0['mv']) & np.isfinite(M0['beta'])
        lg('同截面评估域 um: 平均 %.1f 只/日 (IS %.1f | VALID %.1f | OOS %.1f)'
           % (um.sum(axis=0).mean(),
              um.sum(axis=0)[R.seg_mask(dates, p, 'IS')].mean(),
              um.sum(axis=0)[R.seg_mask(dates, p, 'VALID')].mean(),
              um.sum(axis=0)[R.seg_mask(dates, p, 'OOS')].mean()))

        LR = {h: R.xs_rank_pct(lab[p][LABEL][h], u) for h in R.HORIZONS}
        Rl = LR[H]
        t_last = len(dates) - 1

        # ================= 1) 组级归因 =================
        lg(SUB)
        lg('1) 组级归因 (C_k / Cperp_k, 含符号翻转假设)')
        for kind, S in (('C', C), ('Cperp', Cperp)):
            for k in R.GROUPS:
                e = seg_eval(S[k], u, Rl, dates, p, dedup=(k in ('A1', 'A2', 'A3')))
                s_is = np.sign(e['IS']['ic']) if np.isfinite(e['IS']['ic']) else np.nan
                O['groups'].append({
                    'pool': p, 'series': '%s_%s' % (kind, k), 'kind': kind,
                    'IS_ic': e['IS']['ic'], 'IS_t': e['IS']['t'],
                    'VALID_ic': e['VALID']['ic'], 'VALID_t': e['VALID']['t'],
                    'OOS_ic': e['OOS']['ic'], 'OOS_t': e['OOS']['t'],
                    'ALL_ic': e['ALL']['ic'], 'dedup_ALL_ic': e['dedup_ALL'],
                    'sign_IS': s_is, 'flip_IS_ic': -e['IS']['ic'],
                    'flip_IS_t': -e['IS']['t'],
                    'flip_VALID_ic': -e['VALID']['ic'],
                    'flip_OOS_ic': -e['OOS']['ic'],
                })
                lg('  %-10s IS %+0.4f(t=%+0.1f)  VALID %+0.4f  OOS %+0.4f  ALL %+0.4f'
                   % ('%s_%s' % (kind, k), e['IS']['ic'], e['IS']['t'],
                      e['VALID']['ic'], e['OOS']['ic'], e['ALL']['ic']))

        # ================= 2) 公平增量归因 (common sample) =================
        # 现行打分完全依赖 Cperp_A1 + Cperp_A2, 而 A3 从未在两组合成里被检验.
        uc = u.copy()
        for M in (C['A1'], C['A2'], C['A3'], Cperp['A3']):
            uc &= np.isfinite(M)
        rc = {k: R.xs_rank_pct(C[k], uc) for k in ('A1', 'A2', 'A3')}
        rp = {k: R.xs_rank_pct(Cperp[k], uc) for k in ('A3',)}
        base = R.xs_reg(LR[H], uc, M0, [])
        MODELS = [
            ('M0', []),
            ('M0+A1', ['C_A1']),
            ('M0+A2', ['C_A2']),
            ('M0+A3', ['C_A3']),
            ('M0+A1+A2', ['C_A1', 'C_A2']),
            ('M0+A1+A2+A3', ['C_A1', 'C_A2', 'C_A3']),
            ('M0+A1+A2+(-A3)', ['C_A1', 'C_A2', '-C_A3']),
            ('M0+A1+A2+CperpA3', ['C_A1', 'C_A2', 'Cperp_A3']),
            ('M0+A1+A2+(-CperpA3)', ['C_A1', 'C_A2', '-Cperp_A3']),
        ]
        for mname, terms in MODELS:
            extra = []
            for t in terms:
                neg = t.startswith('-')
                nm = t[1:] if neg else t
                src = rc if (nm.startswith('C_')) else rp
                extra.append(-src[nm.split('_', 1)[1]] if neg else src[nm.split('_', 1)[1]])
            res = R.xs_reg(LR[H], uc, M0, extra) if extra else base
            d, n = (R.delta_r2(res, base) if extra else (0.0, res['n_days']))
            row = {'pool': p, 'model': mname, 'dR2': d, 'n': n,
                   'r2_mean': res['r2_mean'], 'n_terms': len(extra)}
            for j in range(len(extra)):
                row['beta%d' % j] = res.get('beta%d' % j, np.nan)
                row['t%d' % j] = res.get('t%d' % j, np.nan)
            O['inc'].append(row)
            lg('  %-20s dR2=%+0.4f (n=%d, common sample)' % (mname, d, n))

        # ================= 3) 候选权重方案 =================
        lg(SUB)
        lg('2) 候选权重方案 (三段 IC / 分层 / 覆盖 / Top20 重合)')
        base_codes = None
        CANDS = {}
        for name, cfg in PRE_REG['weight_candidates'].items():
            pan, signs = build_candidate(cfg['legs'], C, Cperp, u, Rl, dates,
                                         p, um=um)
            CANDS[name] = pan
            e = seg_eval(pan, u, Rl, dates, p)
            e_cs = seg_eval(pan, u, Rl, dates, p, um=um)
            sp_is = qstat(pan, Yraw, u, dates, p, 'IS')
            sp_al = qstat(pan, Yraw, u, dates, p, 'ALL')
            sp_is_cs = qstat(pan, Yraw, u, dates, p, 'IS', um=um)
            tc = topk_codes(pan, u, t_last, codes, 20)
            if name == 'W0_curr':
                base_codes = tc
            ov = np.nan
            row = {
                'pool': p, 'cand': name, 'desc': cfg['desc'],
                'legs': '+'.join('%s:%s%s' % (
                    l[0], l[1], ('*%+d' % signs['%s_%s' % (l[0], l[1])]
                                 if len(l) > 3 else '')) for l in cfg['legs']),
                'IS_ic': e['IS']['ic'], 'IS_t': e['IS']['t'],
                'VALID_ic': e['VALID']['ic'], 'VALID_t': e['VALID']['t'],
                'OOS_ic': e['OOS']['ic'], 'OOS_t': e['OOS']['t'],
                'ALL_ic': e['ALL']['ic'], 'dedup_ALL_ic': e['dedup_ALL'],
                'IS_nmean': e['IS']['nmean'], 'VALID_nmean': e['VALID']['nmean'],
                'OOS_nmean': e['OOS']['nmean'],
                'IS_mono': sp_is.get('monotonicity', np.nan),
                'IS_net30': sp_is.get('spread_net30', np.nan),
                'ALL_mono': sp_al.get('monotonicity', np.nan),
                'ALL_net30': sp_al.get('spread_net30', np.nan),
                # ---- v2 同截面 (决策口径) ----
                'IS_ic_cs': e_cs['IS']['ic'], 'IS_t_cs': e_cs['IS']['t'],
                'VALID_ic_cs': e_cs['VALID']['ic'], 'VALID_t_cs': e_cs['VALID']['t'],
                'OOS_ic_cs': e_cs['OOS']['ic'], 'OOS_t_cs': e_cs['OOS']['t'],
                'ALL_ic_cs': e_cs['ALL']['ic'],
                'dedup_ALL_ic_cs': e_cs['dedup_ALL'],
                'IS_nmean_cs': e_cs['IS']['nmean'],
                'IS_net30_cs': sp_is_cs.get('spread_net30', np.nan),
                'IS_mono_cs': sp_is_cs.get('monotonicity', np.nan),
                '_top20': tc,
            }
            O['comp'].append(row)
            lg('  %-14s IS_cs %+0.4f(t=%+0.1f) VALID_cs %+0.4f OOS_cs %+0.4f | '
               'n=%.0f net30_cs=%+.4f | 全域 IS %+0.4f'
               % (name, e_cs['IS']['ic'], e_cs['IS']['t'], e_cs['VALID']['ic'],
                  e_cs['OOS']['ic'], e_cs['IS']['nmean'],
                  sp_is_cs.get('spread_net30', np.nan), e['IS']['ic']))
        for row in O['comp']:
            if row['pool'] != p:
                continue
            if base_codes:
                row['top20_overlap_W0'] = len(
                    set(row.pop('_top20')) & set(base_codes)) / 20.0
            else:
                row.pop('_top20')
                row['top20_overlap_W0'] = np.nan

        # ================= 4) 覆盖机制 =================
        lg(SUB)
        lg('3) 覆盖机制对比')
        COVS = {'M_cur': Cperp, 'M_none': C}
        COVS['M_use'] = R.cross_orth({k: C[k] for k in ('A1', 'A2')}, M0, u, lg=None)
        COVS['M_use3'] = R.cross_orth({k: C[k] for k in ('A1', 'A2', 'A3')},
                                      M0, u, lg=None)
        cov_legs = PRE_REG['weight_candidates']['W0_curr']['legs']
        for mname, S in COVS.items():
            pan, _ = build_candidate(cov_legs, S if mname != 'M_none' else C,
                                     S, u, Rl, dates, p, um=um)
            e = seg_eval(pan, u, Rl, dates, p, dedup=False)
            e_cs = seg_eval(pan, u, Rl, dates, p, dedup=False, um=um)
            O['cov'].append({
                'pool': p, 'mech': mname,
                'IS_ic': e['IS']['ic'], 'VALID_ic': e['VALID']['ic'],
                'OOS_ic': e['OOS']['ic'], 'ALL_ic': e['ALL']['ic'],
                'IS_nmean': e['IS']['nmean'], 'VALID_nmean': e['VALID']['nmean'],
                'OOS_nmean': e['OOS']['nmean'],
                # ---- v2 同截面 (决策口径) ----
                'IS_ic_cs': e_cs['IS']['ic'], 'VALID_ic_cs': e_cs['VALID']['ic'],
                'OOS_ic_cs': e_cs['OOS']['ic'], 'ALL_ic_cs': e_cs['ALL']['ic'],
                'IS_nmean_cs': e_cs['IS']['nmean'],
            })
            lg('  %-8s IS_cs %+0.4f n=%.0f | VALID_cs %+0.4f | OOS_cs %+0.4f '
               '| 全域 IS %+0.4f n=%.0f'
               % (mname, e_cs['IS']['ic'], e_cs['IS']['nmean'], e_cs['VALID']['ic'],
                  e_cs['OOS']['ic'], e['IS']['ic'], e['IS']['nmean']))

        # ================= 5) 趋势过滤 =================
        lg(SUB)
        lg('4) 趋势过滤 (作用于现行 W0_curr 合成)')
        pan0 = CANDS['W0_curr']
        e0 = seg_eval(pan0, u, Rl, dates, p, dedup=False, um=um)
        sp0 = qstat(pan0, Yraw, u, dates, p, 'IS', um=um)
        O['filt'].append({
            'pool': p, 'filter': 'F0_none', 'desc': '不过滤 (基线)',
            'IS_ic': e0['IS']['ic'], 'IS_t': e0['IS']['t'],
            'VALID_ic': e0['VALID']['ic'], 'OOS_ic': e0['OOS']['ic'],
            'IS_net30': sp0.get('spread_net30', np.nan),
            'IS_mono': sp0.get('monotonicity', np.nan),
            'IS_nmean': e0['IS']['nmean'],
            'top20_kept': 20,
        })
        for fname, fdesc in PRE_REG['filter_candidates'].items():
            if fname == 'F0_none':
                continue
            fm = FILT[fname]
            uf = u & fm
            e = seg_eval(pan0, uf, Rl, dates, p, dedup=False, um=um)
            sp = qstat(pan0, Yraw, uf, dates, p, 'IS', um=um)
            tc = topk_codes(pan0, uf, t_last, codes, 20)
            O['filt'].append({
                'pool': p, 'filter': fname, 'desc': fdesc,
                'IS_ic': e['IS']['ic'], 'IS_t': e['IS']['t'],
                'VALID_ic': e['VALID']['ic'], 'OOS_ic': e['OOS']['ic'],
                'IS_net30': sp.get('spread_net30', np.nan),
                'IS_mono': sp.get('monotonicity', np.nan),
                'IS_nmean': e['IS']['nmean'],
                'top20_kept': len(set(tc) & set(base_codes or [])),
            })
            lg('  %-8s IS ic %+0.4f (n=%.0f) VALID %+0.4f OOS %+0.4f | net30 %+.4f | top20保留 %d'
               % (fname, e['IS']['ic'], e['IS']['nmean'], e['VALID']['ic'],
                  e['OOS']['ic'], sp.get('spread_net30', np.nan),
                  len(set(tc) & set(base_codes or []))))

        del C, Cperp, CANDS, COVS

    # ============================================================ 落盘
    dag = pd.DataFrame(O['groups'])
    dcp = pd.DataFrame(O['comp'])
    din = pd.DataFrame(O['inc'])
    dcv = pd.DataFrame(O['cov'])
    dfl = pd.DataFrame(O['filt'])
    R.save_csv(dag, 'zz2k_attrib_groups.csv', lg=lg)
    R.save_csv(dcp, 'zz2k_attrib_composites.csv', lg=lg)
    R.save_csv(din, 'zz2k_attrib_incremental.csv', lg=lg)
    R.save_csv(dcv, 'zz2k_attrib_coverage.csv', lg=lg)
    R.save_csv(dfl, 'zz2k_attrib_filters.csv', lg=lg)

    DEC = decide(O)
    R.save_json(DEC, 'zz2k_attrib_decision.json', lg=lg)

    lg(SEP)
    lg('决策摘要')
    for k, v in DEC.items():
        lg('  %-14s %s' % (k, v.get('verdict', '')))
    lg(SEP)
    lg('done (%.1fs)  trading_authorization=NO' % (time.time() - t00))
    return 0


# ================================================================ decision
def _pick(df, pool, col, key, val):
    sub = df[(df['pool'] == pool) & (df[col] == key)]
    return sub.iloc[0] if len(sub) else None


def decide(O):
    dcp = pd.DataFrame(O['comp'])
    dcv = pd.DataFrame(O['cov'])
    dfl = pd.DataFrame(O['filt'])
    out = {}

    # ---------- (A) 权重 ----------
    # v2: 决策一律读同截面列 *_cs (全域跨样本比较已废)
    w0 = _pick(dcp, MAIN_POOL, 'cand', 'W0_curr', None)
    cands = []
    for _, r in dcp[dcp['pool'] == MAIN_POOL].iterrows():
        if not np.isfinite(r['IS_ic_cs']):
            continue
        cands.append((r['cand'], r['IS_ic_cs'], r))
    cands.sort(key=lambda x: -x[1])
    best = cands[0] if cands else None
    if w0 is not None and best is not None:
        b = best[2]
        checks = {
            'is_gain': bool(b['IS_ic_cs'] >= w0['IS_ic_cs'] + PRE_REG['new_thresholds']['DELTA_IC_MIN']),
            'valid_same_sign': bool(np.isfinite(b['VALID_ic_cs']) and np.isfinite(b['IS_ic_cs'])
                                    and np.sign(b['VALID_ic_cs']) == np.sign(b['IS_ic_cs'])
                                    and abs(b['VALID_t_cs']) >= R.VALID_T_MIN),
            'oos_ge_w0': bool(np.isfinite(b['OOS_ic_cs']) and b['OOS_ic_cs'] >= w0['OOS_ic_cs']
                              and np.isfinite(b['IS_ic_cs'])
                              and np.sign(b['OOS_ic_cs']) == np.sign(b['IS_ic_cs'])),
        }
        go = all(checks.values())
        out['weight'] = {
            'domain': 'common_sample (v2)',
            'baseline': 'W0_curr',
            'baseline_IS_ic': float(w0['IS_ic_cs']),
            'baseline_VALID_ic': float(w0['VALID_ic_cs']),
            'baseline_OOS_ic': float(w0['OOS_ic_cs']),
            'best_on_IS': best[0], 'best_IS_ic': float(b['IS_ic_cs']),
            'checks': checks,
            'verdict': ('GO -> 采用 %s' % best[0]) if go else
                       ('NO-GO -> 保持 W0_curr (最优 IS 方案 %s 未通过确认)' % best[0]),
            'ranking': [{'cand': c, 'IS_ic_cs': float(v)} for c, v, _ in cands],
        }
    else:
        out['weight'] = {'verdict': 'NO_DATA'}

    # ---------- (B) 趋势过滤 ----------
    f0 = _pick(dfl, MAIN_POOL, 'filter', 'F0_none', None)
    fres = []
    for _, r in dfl[(dfl['pool'] == MAIN_POOL) & (dfl['filter'] != 'F0_none')].iterrows():
        checks = {
            'ic_not_worse': bool(np.isfinite(r['IS_ic']) and np.isfinite(r['VALID_ic'])
                                 and np.isfinite(r['OOS_ic'])
                                 and r['IS_ic'] >= f0['IS_ic']
                                 and r['VALID_ic'] >= f0['VALID_ic']
                                 and r['OOS_ic'] >= f0['OOS_ic']),
            'spread_gain': bool(np.isfinite(r['IS_net30'])
                                and r['IS_net30'] >= f0['IS_net30']
                                + PRE_REG['new_thresholds']['FILTER_SPREAD_MIN']),
            'sign_same': bool(np.isfinite(r['IS_ic']) and np.isfinite(f0['IS_ic'])
                              and np.sign(r['IS_ic']) == np.sign(f0['IS_ic'])
                              and np.sign(r['VALID_ic']) == np.sign(f0['IS_ic'])
                              and np.sign(r['OOS_ic']) == np.sign(f0['IS_ic'])),
        }
        fres.append({'filter': r['filter'],
                     'IS_ic': float(r['IS_ic']), 'VALID_ic': float(r['VALID_ic']),
                     'OOS_ic': float(r['OOS_ic']),
                     'IS_net30': float(r['IS_net30']),
                     'top20_kept': int(r['top20_kept']),
                     'checks': checks, 'go': bool(all(checks.values()))})
    out['trend_filter'] = {
        'baseline_IS_ic': float(f0['IS_ic']),
        'baseline_IS_net30': float(f0['IS_net30']),
        'candidates': fres,
        'verdict': ('GO -> 采用 ' + ','.join(x['filter'] for x in fres if x['go']))
                   if any(x['go'] for x in fres) else
                   'NO-GO -> 不增加趋势过滤 (无候选同时满足 IC 不劣化 + net30 改善 + 三段同号)',
    }

    # ---------- (C) 覆盖机制 ----------
    # v2: coverage_gain 读全域 nmean (宽度本身是受测对象);
    #     ic_ok / sign 读同截面 *_cs (否则 IC 优势可能来自换样本)
    mc = _pick(dcv, MAIN_POOL, 'mech', 'M_cur', None)
    cres = []
    for _, r in dcv[(dcv['pool'] == MAIN_POOL) & (dcv['mech'] != 'M_cur')].iterrows():
        checks = {
            'coverage_gain': bool(np.isfinite(r['IS_nmean']) and np.isfinite(mc['IS_nmean'])
                                  and r['IS_nmean'] >= mc['IS_nmean']
                                  * PRE_REG['new_thresholds']['COV_GAIN_MIN']),
            'ic_ok': bool(np.isfinite(r['IS_ic_cs']) and np.isfinite(r['VALID_ic_cs'])
                          and np.isfinite(r['OOS_ic_cs'])
                          and r['IS_ic_cs'] >= mc['IS_ic_cs'] - PRE_REG['new_thresholds']['COV_IC_TOL']
                          and r['VALID_ic_cs'] >= mc['VALID_ic_cs'] - PRE_REG['new_thresholds']['COV_IC_TOL']
                          and r['OOS_ic_cs'] >= mc['OOS_ic_cs'] - PRE_REG['new_thresholds']['COV_IC_TOL']),
            'no_sign_conflict': bool(np.isfinite(r['IS_ic_cs']) and r['IS_ic_cs'] > 0),
        }
        cres.append({'mech': r['mech'],
                     'IS_nmean': float(r['IS_nmean']),
                     'IS_ic_cs': float(r['IS_ic_cs']),
                     'VALID_ic_cs': float(r['VALID_ic_cs']),
                     'OOS_ic_cs': float(r['OOS_ic_cs']),
                     'IS_ic_full': float(r['IS_ic']),
                     'checks': checks, 'go': bool(all(checks.values()))})
    out['coverage'] = {
        'domain': 'coverage_gain=full nmean; ic_ok=common_sample (v2)',
        'baseline_IS_nmean': float(mc['IS_nmean']),
        'baseline_IS_ic_cs': float(mc['IS_ic_cs']),
        'candidates': cres,
        'verdict': ('GO -> 采用 ' + ','.join(x['mech'] for x in cres if x['go']))
                   if any(x['go'] for x in cres) else
                   'NO-GO -> 维持现行 cross_orth(5 组) 覆盖机制',
    }
    return out


if __name__ == '__main__':
    sys.exit(main() or 0)
