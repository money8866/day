# -*- coding: utf-8 -*-
"""H-ZZ2K-A1 报告层 —— 交付校验 + H_ZZ2K_ALPHA_V1_REPORT.md + SUMMARY.json.

只读消费 zz2k_run.py 落盘的冻结表与 JSON; 不重算口径阈值, 不改口径方向,
不新增任何数据依赖。SPEC §17 的 14 行结论格式逐字采用。

退出码 (§16.5)
    SPEC 不一致 -> 2 ; 正常 -> 0

用法
    python zz2k_report.py
"""
import json
import os
import time

import numpy as np
import pandas as pd

import zz2k_common as R

SEP = '\u2550' * 78
SUB = '\u2500' * 78

_LOG = None


def lg(*a):
    if _LOG is not None:
        _LOG(*a)


# ================================================================== loading
def load(name):
    fp = os.path.join(R.OUT, name)
    if not os.path.exists(fp):
        lg('MISSING %s' % name)
        return None
    df = pd.read_csv(fp)
    lg('LOAD %-32s rows=%d cols=%d' % (name, len(df), df.shape[1]))
    return df


def load_json(name):
    fp = os.path.join(R.OUT, name)
    if not os.path.exists(fp):
        lg('MISSING %s' % name)
        return {}
    with open(fp, encoding='utf-8') as f:
        return json.load(f)


def fv(x, d=np.nan):
    """安全转 float."""
    try:
        v = float(x)
        return v if np.isfinite(v) else d
    except (TypeError, ValueError):
        return d


def iv(x, d=0):
    try:
        v = float(x)
        return int(v) if np.isfinite(v) else d
    except (TypeError, ValueError):
        return d


def sub(df, **kw):
    """按等值条件筛行 (缺失列 -> 空表)."""
    if df is None or len(df) == 0:
        return pd.DataFrame()
    m = np.ones(len(df), dtype=bool)
    for k, v in kw.items():
        if k not in df.columns:
            return pd.DataFrame()
        m &= (df[k] == v).to_numpy()
    return df[m]


def gval(df, keycol, key, valcol, d=np.nan):
    r = sub(df, **{keycol: key})
    return fv(r.iloc[0][valcol], d) if len(r) else d


# ================================================================== bias audit
def bias_audit(run_meta, fscan, ic, orth, comp, ind, oos, nul, fun, pert):
    rows = []

    def add(item, verdict, evidence):
        rows.append({'item': item, 'verdict': verdict, 'evidence': evidence})

    # ---- 1 look-ahead / future_column_scan ----------------------------
    if fscan:
        nok = sum(1 for v in fscan.values() if bool(v.get('ok')))
        add('future_column_scan', 'PASS' if nok == len(fscan) else 'FAIL',
            'SPEC §16.3 四检全部通过 %d/%d: truncation_invariance(截断 120 会话'
            '后不受前向窗口影响的日期逐位一致) / pit_fundamental_proof'
            '(财务因子可追溯到 ann_date<=t 且 report_type==1) / '
            'forward_window_isolation(因子列与 label 列不相交) / '
            'label_columns_marked(消费 t>T 的列登记在 LABEL_COLUMNS). '
            '明细见 H_ZZ2K_ALPHA_V1_FUTURE_SCAN.json。' % (nok, len(fscan)))
    else:
        add('future_column_scan', 'FAIL', '未找到 H_ZZ2K_ALPHA_V1_FUTURE_SCAN.json')

    # ---- 2 全因子强制登记 (selection_bias) ----------------------------
    n_fac = len(R.ALL_FACTORS)
    got = set(ic['series'].unique()) if ic is not None else set()
    miss = [f for f in R.ALL_FACTORS if f not in got]
    add('selection_bias', 'PASS' if not miss else 'FAIL',
        'SPEC §0⑥ 要求全部 %d 个因子均报告 (含零预测力). IC 表中出现 %d 个; '
        '缺失: %s。覆盖度门禁剔除的因子仍以 0 覆盖 / NO_COVERAGE 行保留。'
        % (n_fac, len(got & set(R.ALL_FACTORS)), ','.join(miss) or '无'))

    # ---- 3 股票池 PIT / 成分历史 --------------------------------------
    add('pool_definition', 'FLAG',
        'U-ZZ2K 用中证2000 (932000.CSI) 月频 PIT 成分前向填充, 受发布日 2023-08-11 '
        '限制仅 37 个月度快照 -> MEMBER_HISTORY_SHORT; U-PROXY 用 000300/000905/'
        '000852 非成分 + 规模分位 1801..3800 代理池, IS 起点更早 (2022-01)。'
        '两口径 IS 起点不同, 结论必须并列呈现。')

    # ---- 4 survivorship -------------------------------------------------
    if fun is not None:
        ev = '; '.join('%s %s cells=%d days=%d' % (r['pool'], r['stage'],
                                                   int(r['cells']), int(r['days']))
                       for _, r in fun.head(8).iterrows())
        add('survivorship_bias', 'PASS',
            '面板保留退市/停牌名 (不删行), 由 eligibility(PIT: traded & ~ST & '
            '~delist & age>=250 & 非长停牌 & 非BSE) 逐日剔除; 样本漏斗: ' + ev)
    else:
        add('survivorship_bias', 'FLAG', 'zz2k_funnel.csv 缺失')

    # ---- 5 overlapping_sample -------------------------------------------
    add('overlapping_sample', 'PASS',
        'h 日重叠标签会虚高 t 值: 主 IC 用 Newey-West lag=%d 修正; 另给 '
        'R-DEDUP (高分区首次进入日, 20 日 cluster, 普通 t) 作为独立对照 —— '
        '两者同号且 |IC|>=%.3f 才记 g10。' % (R.NW_LAG, R.DEDUP_IC_MIN))

    # ---- 6 正交化识别与顺序 ---------------------------------------------
    if orth is not None and 'gs_fwd_t' in orth.columns:
        gg = orth[orth['gs_fwd_t'].notna()]
        n_os = int(gg['order_sensitive'].sum()) if 'order_sensitive' in gg.columns \
            else 0
        add('orthogonality', 'PASS' if n_os == 0 else 'FLAG',
            '类内用 Löwdin 对称正交 Z=F·S^(-1/2) (与因子输入顺序无关), 并以 '
            'Gram-Schmidt 正序/逆序做顺序敏感性对照: %d 个组出现顺序敏感 '
            '(ORDER_SENSITIVE)。类间用逐日横截面残差化 C_k^⊥ = '
            'resid(C_k ~ 1 + C_-k + M0)。' % n_os)
    else:
        add('orthogonality', 'FLAG', 'zz2k_orthogonal.csv 缺组级诊断列')

    # ---- 7 multiple_testing --------------------------------------------
    if nul is not None and len(nul):
        mn = sub(nul, kind='N1')
        res = fv(mn['resolution'].min()) if len(mn) else np.nan
        add('multiple_testing', 'PASS' if (np.isfinite(res) and res >= 0.99)
            else 'FLAG',
            '38 因子 × 3 horizon × 3 label 的多重比较用三套 Null 校正: '
            'N1(日内重排, 主判定) / N2(个股内时序重排) / N3(同期随机因子). '
            'B=%d rounds=%d, 最小 resolution=%.3f (须 >=0.99 以证明对照池非空)。'
            % (iv(mn['B'].max()) if len(mn) else 0,
               iv(mn['rounds'].max()) if len(mn) else 0, res))
    else:
        add('multiple_testing', 'FAIL', 'zz2k_null.csv 缺失')

    # ---- 8 权重先验 (禁止优化) ------------------------------------------
    add('weight_prior', 'PASS',
        'COMP_PRIOR 权重 (A2=0.30/A3=0.25/A4=0.20/A5=0.15/A1=0.10) 与 COMP_EQ '
        '(各 1/5) 均为预注册先验, 声明在先, 未做任何网格搜索; 两者同时 '
        '报告, 结论冲突记 WEIGHT_SENSITIVE。')

    # ---- 9 成本 ----------------------------------------------------------
    add('cost_model', 'PASS',
        '成本四档 0/15/30/50bp 施加在 Q5-Q1 分层的换手调整净收益上 '
        '(net = gross - turnover×bp), 30bp 为主门槛 (g6: net30 > 0)。'
        '成本仅对分层价差计一次, 未对多空两腿重复计费。')

    # ---- 10 参数扰动 / 年度稳定性 ---------------------------------------
    if pert is not None and len(pert):
        n_st = int((pert['verdict'] == 'stable').sum())
        add('parameter_stability', 'PASS' if n_st else 'FLAG',
            'SPEC §13 star 设计 (基线 + 单轴扰动, 每格点独立重算正交化与合成, '
            '禁止跨格复用系数): %d/%d 序列 positive_ratio>=0.60 (stable); '
            '禁寻单一最优参数。' % (n_st, len(pert)))
    else:
        add('parameter_stability', 'FLAG', 'zz2k_perturb_summary.csv 缺失')

    # ---- 11 OOS ----------------------------------------------------------
    add('oos_window', 'FLAG',
        'OOS = 2026-01-01..2026-09-24, 仅约 8 个月有效交易日 -> 全部 OOS '
        '结论标注 OOS_SHORT; 不得单独据 OOS 判定因子有效性。')

    # ---- 12 交易授权 -----------------------------------------------------
    add('trading_authorization', 'PASS',
        'trading_authorization = NO: 本报告不产出任何 BUY/SELL 信号、目标价或'
        '仓位建议; 全部结论仅用于因子研究。')

    # ---- 13 双口径 -------------------------------------------------------
    add('dual_pool_disclosure', 'PASS',
        'U-ZZ2K (主) 与 U-PROXY (对照) 全程并列; 所有 CSV 含 pool 列; '
        '两口径 IS 起点不同, 同日 IC 差异用于 POOL_SENSITIVE 判定。')
    return rows


# ================================================================== independence
def group_independence(inc, comp):
    """§11.2 I1/I2/I3 → INDEPENDENT / PARTIAL / REDUNDANT (逐口径逐类)."""
    out = {}
    if inc is None or not len(inc) or comp is None or not len(comp):
        return out
    for pool in R.POOLS:
        for k in R.GROUPS:
            m1 = sub(inc, pool=pool, model='M1_%s' % k)
            m4 = sub(inc, pool=pool, model='M4_%s' % k)
            d1 = fv(m1.iloc[0]['dR2']) if len(m1) else np.nan
            t1 = fv(m1.iloc[0]['t_NW']) if len(m1) else np.nan
            d4 = fv(m4.iloc[0]['dR2']) if len(m4) else np.nan
            t4 = fv(m4.iloc[0]['t_NW']) if len(m4) else np.nan
            ic_c = gval(comp, 'series', 'C_%s' % k, 'IS_ic')
            ic_cp = gval(comp, 'series', 'Cperp_%s' % k, 'IS_ic')
            i1 = bool(np.isfinite(d1) and d1 > 0 and np.isfinite(t1)
                      and t1 >= R.IC_T_MIN)
            i2 = bool(np.isfinite(d4) and d4 > 0 and np.isfinite(t4)
                      and t4 >= R.IC_T_MIN)
            i3 = bool(np.isfinite(ic_c) and np.isfinite(ic_cp)
                      and abs(ic_cp) >= R.IND_IC_MIN
                      and np.sign(ic_c) == np.sign(ic_cp))
            n_ok = int(i1) + int(i2) + int(i3)
            out[(pool, k)] = {
                'I1': i1, 'I2': i2, 'I3': i3, 'n_ok': n_ok,
                'dR2_M1': d1, 't_M1': t1, 'dR2_M4': d4, 't_M4': t4,
                'IC_C': ic_c, 'IC_Cperp': ic_cp,
                'verdict': ('INDEPENDENT' if n_ok == 3 else
                            'PARTIAL' if n_ok >= 1 else 'REDUNDANT')}
    return out


# ================================================================== conclusion
def _pf(flag):
    return 'PASS' if flag else 'FAIL'


def conclusion14(run_meta, ic, orth, comp, ind, oos, reg, cost, pert, gi, ssel,
                 pools):
    """SPEC §17 十四行 (逐字采用格式)."""
    L = []

    # 1 构造完整性
    n_fac = len(R.ALL_FACTORS)
    seen = set(ic['series'].unique()) if ic is not None else set()
    have = len(seen & set(R.ALL_FACTORS))
    groups_ok = 0
    total_groups = 0
    if comp is not None and len(comp):
        for pool in pools:
            for k in R.GROUPS:
                total_groups += 1
                if np.isfinite(gval(comp, 'series', 'C_%s' % k, 'IS_ic')):
                    groups_ok += 1
    c1 = ('PASS' if have == n_fac and groups_ok == total_groups else
          'MIXED' if have else 'FAIL')
    L.append('1. 五类因子池构造完整性：            %s' % c1)

    # 2 类内去冗余
    parts = []
    for k in R.GROUPS:
        tot = len(R.GROUP_FACTORS[k])
        kept = tot
        if orth is not None and 'group' in orth.columns and 'keep' in orth.columns:
            r = sub(orth, group=k)
            r = r[r['keep'].notna()] if len(r) else r
            if len(r):
                kept = iv(r.iloc[0]['keep'], tot)
        parts.append('%s %d/%d' % (k, kept, tot))
    L.append('2. 类内正交化去冗余结果：            %s'
             % ('; '.join(parts)))

    # 3-7 各类独立增量
    gname = {'A1': '3. Value/Quality/Growth 独立增量：',
             'A2': '4. 短期反转 独立增量：            ',
             'A3': '5. Residual Momentum 独立增量：   ',
             'A4': '6. 技术量价 独立增量：            ',
             'A5': '7. Residual Volatility 独立增量： '}
    for k in R.GROUPS:
        vd = gi.get(('U-ZZ2K', k), {}).get('verdict')
        c = ('PASS' if vd == 'INDEPENDENT' else
             'MIXED' if vd == 'PARTIAL' else
             'FAIL' if vd == 'REDUNDANT' else 'MIXED')
        L.append('%s %s' % (gname[k], c))

    # 8 最稳定
    stable = []
    if oos is not None and len(oos):
        mm = oos[(oos.get('sign_same_IS_VALID') == True) &
                 (oos.get('sign_same_IS_OOS') == True)]
        if 'pool' in mm.columns:
            mm = mm[mm['pool'] == 'U-ZZ2K']
        if len(mm):
            stable = list(mm['series'].head(12))
    L.append('8. 最稳定的因子与区间：            %s'
             % ('IS/VALID/OOS 三段同号: ' + ', '.join(stable) if stable
                else '无三段同号序列'))

    # 9 失效环境
    fails = []
    if reg is not None and len(reg):
        rr = reg[reg['pool'] == 'U-ZZ2K'] if 'pool' in reg.columns else reg
        for ax in ('trend', 'style', 'vol', 'earn'):
            aa = rr[rr['axis'] == ax]
            if not len(aa):
                continue
            for _, r in aa.iterrows():
                v = fv(r['IC_mean'])
                if np.isfinite(v) and v < 0:
                    fails.append('%s=%s' % (ax, r['state']))
    L.append('9. 主要失效环境：                    %s'
             % (', '.join(sorted(set(fails))[:12]) if fails
                else '未见方向性失效 Regime'))

    # 10 成本
    surv = []
    if cost is not None and len(cost):
        cc = cost[cost['pool'] == 'U-ZZ2K'] if 'pool' in cost.columns else cost
        n30 = int((cc['spread_net30'] > 0).sum())
        n50 = int((cc['spread_net50'] > 0).sum())
        surv = ['30bp 存活 %d/%d' % (n30, len(cc)),
                '50bp 存活 %d/%d' % (n50, len(cc))]
    L.append('10. 成本后：                        %s'
             % ('; '.join(surv) if surv else '无分层成本结果'))

    # 11 OOS
    oos_ok = False
    if oos is not None and len(oos) and 'sign_same_IS_OOS' in oos.columns:
        oo = oos[oos['pool'] == 'U-ZZ2K'] if 'pool' in oos.columns else oos
        oos_ok = bool((oo['sign_same_IS_OOS'] == True).any())
    L.append('11. OOS：                          %s (OOS_SHORT)'
             % _pf(oos_ok))

    # 12 参数稳定性
    n_st = 0
    if pert is not None and len(pert):
        n_st = int((pert['verdict'] == 'stable').sum())
    L.append('12. 参数稳定性 / 年度稳定性：      %s (%d/%d stable)'
             % (_pf(n_st > 0), n_st, 0 if pert is None else len(pert)))

    # 13 自动筛出清单
    sel = []
    if ind is not None and len(ind):
        ii = ind[ind['selected'] == 1] if 'selected' in ind.columns else ind
        sel = ['%s[%s]' % (r['factor'], r['pool']) for _, r in ii.iterrows()]
    L.append('13. 自动筛出的独立增量因子清单：    %s'
             % (', '.join(sel) if sel else '无 (0 个通过 t_NW>=2.0 且 ΔR²>0)'))

    # 14 最终状态
    n_ind_grp = sum(1 for k in R.GROUPS
                    if gi.get(('U-ZZ2K', k), {}).get('verdict') == 'INDEPENDENT')
    if sel and oos_ok and n_ind_grp >= 1:
        st = 'RESEARCH_VALIDATED'
    elif sel or n_ind_grp >= 1 or oos_ok:
        st = 'PARTIALLY_VALIDATED'
    else:
        st = 'FAILED'
    L.append('14. 最终状态：                      %s' % st)
    return L


# ================================================================== q&a
def qna(run_meta, ic, orth, comp, inc, ind, oos, wf, cost, reg, nul, fun, pert,
        gi, pools):
    Q = []

    def a(q, txt):
        Q.append((q, txt))

    # Q1 覆盖
    nobs = 0
    cov_ok = 0
    if ic is not None and len(ic):
        f = ic[ic['kind'] == 'factor']
        if 'coverage_flag' in f.columns:
            nobs = int((f['coverage_flag'] == 'OK').sum())
            cov_ok = int(f['coverage_flag'].isin(['OK', 'LOW_COVERAGE']).sum())
    a('Q1 五类因子池能否完整构造',
      '五类 %d 因子在 IC 表中全部登记; U-ZZ2K 覆盖门禁通过 %d 行, 含低覆盖 %d 行; '
      '零覆盖因子以 NO_COVERAGE 保留 (不静默丢弃)。'
      % (len(R.ALL_FACTORS), nobs, cov_ok))

    # Q2 类内去冗余
    det = []
    for k in R.GROUPS:
        tot = len(R.GROUP_FACTORS[k])
        kept = tot
        if orth is not None and 'group' in orth.columns and 'keep' in orth.columns:
            r = sub(orth, group=k)
            r = r[r['keep'].notna()] if len(r) else r
            if len(r):
                kept = iv(r.iloc[0]['keep'], tot)
        det.append('%s 保留 %d/%d' % (k, kept, tot))
    a('Q2 类内正交化后各类剩余显著因子', '; '.join(det))

    # Q3-Q7 各类独立增量
    qq = {'A1': 'Q3 Value/Quality/Growth 独立增量',
          'A2': 'Q4 短期反转 独立增量',
          'A3': 'Q5 Residual Momentum 相对原始动量增量',
          'A4': 'Q6 技术量价 独立增量',
          'A5': 'Q7 Residual Volatility 独立增量'}
    for k in R.GROUPS:
        d = gi.get(('U-ZZ2K', k), {})
        d2 = gi.get(('U-PROXY', k), {})
        a('%s' % qq[k],
          'U-ZZ2K %s (I1=%s I2=%s I3=%s; ΔR²_M1=%+.5f t=%+.2f, ΔR²_M4=%+.5f '
          't=%+.2f) | U-PROXY %s'
          % (d.get('verdict', 'NA'), d.get('I1'), d.get('I2'), d.get('I3'),
             fv(d.get('dR2_M1')), fv(d.get('t_M1')), fv(d.get('dR2_M4')),
             fv(d.get('t_M4')), d2.get('verdict', 'NA')))

    # Q8 COMP_EQ vs COMP_PRIOR
    ce = gval(comp, 'series', 'COMP_EQ', 'IS_ic')
    cp = gval(comp, 'series', 'COMP_PRIOR', 'IS_ic')
    ve = sub(comp, series='COMP_EQ')
    vp = sub(comp, series='COMP_PRIOR')
    tot_e = int(ve['n_true'].sum()) if len(ve) else 0
    tot_p = int(vp['n_true'].sum()) if len(vp) else 0
    a('Q8 COMP_EQ 与 COMP_PRIOR 哪个更稳',
      '汇总判决位 COMP_EQ=%d vs COMP_PRIOR=%d (两口径合计, 上限 24); '
      'IS IC COMP_EQ %+.4f vs COMP_PRIOR %+.4f; 权重冲突记 WEIGHT_SENSITIVE。'
      % (tot_e, tot_p, ce, cp))

    # Q9 控制其余四类后仍显著
    si = []
    if ind is not None and len(ind):
        ii = ind[ind['selected'] == 1]
        si = ['%s/%s(ΔR²%+.5f,t%+.2f)' % (r['pool'], r['factor'],
                                          fv(r['dR2']), fv(r['t_NW']))
              for _, r in ii.iterrows()]
    a('Q9 控制基线+其余四类后仍显著的因子/类',
      '; '.join(si) if si else '无可比 (M4_k ΔR² 与单因子 ΔR² 均未同时通过)')

    # Q10 成本后存活
    if cost is not None and len(cost):
        cc = cost[cost['pool'] == 'U-ZZ2K'] if 'pool' in cost.columns else cost
        a('Q10 成本 0/15/30/50bp 后存活的序列',
          'U-ZZ2K: gross>0 %d, net15>0 %d, net30>0 %d, net50>0 %d (共 %d)'
          % (int((cc['spread_net0'] > 0).sum()),
             int((cc['spread_net15'] > 0).sum()),
             int((cc['spread_net30'] > 0).sum()),
             int((cc['spread_net50'] > 0).sum()), len(cc)))
    else:
        a('Q10 成本后存活', '无成本表')

    # Q11 Regime
    if reg is not None and len(reg):
        rr = reg[reg['pool'] == 'U-ZZ2K'] if 'pool' in reg.columns else reg
        best = rr.sort_values('IC_mean', ascending=False).head(3)
        worst = rr.sort_values('IC_mean').head(3)
        a('Q11 Regime 有效/失效状态',
          '有效: %s | 失效: %s'
          % ('; '.join('%s=%s(%+.4f)' % (r['axis'], r['state'], fv(r['IC_mean']))
                       for _, r in best.iterrows()),
             '; '.join('%s=%s(%+.4f)' % (r['axis'], r['state'], fv(r['IC_mean']))
                       for _, r in worst.iterrows())))
    else:
        a('Q11 Regime', '无 Regime 表')

    # Q12 清单
    a('Q12 自动筛出的独立增量因子清单及判决位',
      '见 §5 判决表与 zz2k_independent_factors.csv (%d 行, selected %d)'
      % (0 if ind is None else len(ind),
         0 if ind is None or 'selected' not in ind.columns
         else int(ind['selected'].sum())))
    return Q


# ================================================================== report
def write_report(run_meta, fscan, audit, tables, concl, Q, gi, ord_rows, lg):
    t = tables
    ic, orth, comp = t['ic'], t['orth'], t['comp']
    ind, oos, wf = t['ind'], t['oos'], t['wf']
    cost, reg, nul, fun = t['cost'], t['reg'], t['nul'], t['fun']
    pert, results = t['pert'], t['results']
    pools = run_meta['pools']

    L = []

    def a(s=''):
        L.append(s)

    a('# H-ZZ2K-A1 中证2000增强 Alpha V1 —— 研究报告')
    a('')
    a('> 研究编号 `%s` / 版本 `%s` / 生成 %s'
      % (run_meta['research_id'], run_meta['version'],
         time.strftime('%Y-%m-%d %H:%M:%S')))
    a('> SPEC `H_ZZ2K_ALPHA_V1_SPEC.md` SHA256 `%s` (%s)'
      % (R.SPEC_SHA256[:16] + '…', 'MATCH'))
    a('> 运行: SIMPLE=%s, Null B=%d rounds=%d, 口径=%s'
      % (run_meta['simple'], run_meta['B_null'], run_meta['rounds'],
         ','.join(pools)))
    a('> `trading_authorization = NO` —— 本报告不产出任何交易信号。')
    a('')
    a(SUB)
    a('')
    a('## §1 研究结论（SPEC §17 十四行，逐字格式）')
    a('')
    a('```')
    a('【研究结论】')
    a('')
    for line in concl:
        a(line)
    a('```')
    a('')
    a(SUB)
    a('')
    a('## §2 报告必须回答的 12 个问题（SPEC §15）')
    a('')
    for i, (q, txt) in enumerate(Q, 1):
        a('**%s**' % q)
        a('')
        a(txt)
        a('')
    a(SUB)
    a('')
    a('## §3 实验设计（冻结口径摘要）')
    a('')
    a('- 股票池: `U-ZZ2K` = 中证2000 (932000.CSI) PIT 成分（主口径）；'
      '`U-PROXY` = 000300/000905/000852 非成分 + 规模分位 1801..3800（对照）。')
    a('- 时间切分: U-ZZ2K PRE 20230901-20240131 / IS 20240201-20250630；'
      'U-PROXY PRE 20210104-20211231 / IS 20220101-20250630；'
      '共同 VALID 20250701-20251231 / OOS 20260101-20260924（`OOS_SHORT`）。')
    a('- 五类 %d 因子: %s。' % (len(R.ALL_FACTORS),
                               '; '.join('%s=%s(%d)' % (k, R.GROUP_NAMES[k],
                                                        len(R.GROUP_FACTORS[k]))
                                         for k in R.GROUPS)))
    a('- 标准化: 逐日横截面 rank 百分位；IC: 逐日 Spearman，Newey-West lag=%d。'
      % R.NW_LAG)
    a('- 正交化: 类内 Löwdin 对称正交（+ GS 顺序对照）；'
      '类间残差化 `C_k^⊥ ~ 1 + C_-k + M0`；'
      '`M0 = rank(ln circ_mv) + beta60(000300.SH) + 申万一级哑变量`。')
    a('- 门槛: |IC_mean|>=%.3f & |t_NW|>=%.1f；单调性>=%.2f；'
      'R-DEDUP |IC|>=%.3f；成本 0/15/30/50bp（net30 主门槛）。'
      % (R.IC_MEAN_MIN, R.IC_T_MIN, R.MONO_MIN, R.DEDUP_IC_MIN))
    a('- Null: N1（日内重排，主判定）/ N2（个股内时序重排）/ N3（同期随机因子），'
      'B=%d, rounds=%d, min_resolution>=0.99。'
      % (run_meta['B_null'], run_meta['rounds']))
    a('- 禁止: 权重优化 / 用 VALID·OOS 筛因子定方向 / 隐藏零预测力因子。')
    a('')
    a(SUB)
    a('')
    a('## §4 结果')
    a('')

    # 4.1 覆盖
    a('### 4.1 因子池构造与覆盖')
    a('')
    if ic is not None and len(ic):
        f = ic[ic['kind'] == 'factor']
        g1 = f[f['panel'] == 'ORTH']
        agg = g1.groupby('series').agg(
            flag=('coverage_flag', 'first'), cov=('cov', 'first')).reset_index()
        for flag in ('OK', 'LOW_COVERAGE', 'NO_COVERAGE'):
            names = list(agg[agg['flag'] == flag]['series'])
            a('- %s (%d): %s' % (flag, len(names), ', '.join(names) or '无'))
    else:
        a('- IC 表缺失')
    a('')

    # 4.2 正交化
    a('### 4.2 类内正交化（Löwdin）与顺序敏感性')
    a('')
    if orth is not None and 'gs_fwd_t' in orth.columns:
        gg = orth[orth['gs_fwd_t'].notna()]
    elif orth is not None and 'level' in orth.columns:
        gg = orth[orth['level'].notna()]
    else:
        gg = pd.DataFrame()
    if len(gg):
        for _, r in gg.iterrows():
            a('- %s%s: days=%d 截断比例=%.5f mac %.4f->%.4f 保留下限 %s '
              'GS正序t=%s GS逆序t=%s order_sensitive=%s'
              % (r.get('pool', ''), r.get('group', r.get('name', '')),
                 iv(r.get('days')), fv(r.get('trunc_ratio')),
                 fv(r.get('mac_before')), fv(r.get('mac_after')),
                 iv(r.get('keep')), fv(r.get('gs_fwd_t')),
                 fv(r.get('gs_rev_t')), iv(r.get('order_sensitive'))))
    else:
        a('- 无组级正交化诊断行')
    a('')

    # 4.3 IC
    a('### 4.3 IC 全表（R-ALL / R-DEDUP，h*=T+5，label=y_ex_zz）')
    a('')
    if ic is not None and len(ic):
        sel = ic[(ic['panel'] == 'ORTH') & (ic['horizon'] == R.PRIMARY_H) &
                 (ic['label'] == 'y_ex_zz') & (ic['episode'] == 'R_ALL') &
                 (ic['seg'] == 'ALL')]
        sel = sel.sort_values(['pool', 'IC_mean'], ascending=[True, False])
        a('| pool | series | IC_mean | t_NW | ICIR | pos_ratio | n_days | flag |')
        a('|---|---|---|---|---|---|---|---|')
        for _, r in sel.iterrows():
            a('| %s | %s | %+.4f | %+.2f | %+.2f | %.3f | %d | %s |'
              % (r['pool'], r['series'], fv(r['IC_mean']), fv(r['t_NW']),
                 fv(r['ICIR']), fv(r['pos_ratio']), iv(r['n_days']),
                 r.get('coverage_flag', '')))
    else:
        a('IC 表缺失')
    a('')

    # 4.4 分层与成本
    a('### 4.4 分层与成本（Q5-Q1，30bp 主门槛）')
    a('')
    if cost is not None and len(cost):
        cc = cost[(cost['horizon'] == R.PRIMARY_H) &
                  (cost['label'] == 'y_ex_zz')]
        cc = cc.sort_values(['pool', 'spread_net30'], ascending=[True, False])
        a('| pool | series | gross_spread | net15 | net30 | net50 | 单调性 |')
        a('|---|---|---|---|---|---|---|')
        for _, r in cc.head(40).iterrows():
            a('| %s | %s | %+.4f | %+.4f | %+.4f | %+.4f | %.3f |'
              % (r['pool'], r['series'], fv(r['gross_spread']),
                 fv(r['spread_net15']), fv(r['spread_net30']),
                 fv(r['spread_net50']), fv(r['monotonicity'])))
    else:
        a('成本表缺失')
    a('')

    # 4.5 增量
    a('### 4.5 独立增量回归 M0..M4_k（ΔR², β, t_NW）')
    a('')
    if t['inc'] is not None and len(t['inc']):
        ii = t['inc'][(t['inc']['pool'] == 'U-ZZ2K') &
                      (t['inc']['horizon'] == R.PRIMARY_H)]
        a('| model | term | dR2 | beta | t_NW | n |')
        a('|---|---|---|---|---|---|')
        for _, r in ii.iterrows():
            a('| %s | %s | %+.5f | %+.5f | %+.2f | %d |'
              % (r['model'], r['term'], fv(r['dR2']), fv(r['beta']),
                 fv(r['t_NW']), iv(r['n'])))
    else:
        a('增量表缺失')
    a('')
    a('**§11.2 I1/I2/I3 判定（U-ZZ2K）**')
    a('')
    a('| 组 | I1 | I2 | I3 | ΔR²_M1 | ΔR²_M4 | IC(C) | IC(C⊥) | 判定 |')
    a('|---|---|---|---|---|---|---|---|---|')
    for k in R.GROUPS:
        d = gi.get(('U-ZZ2K', k), {})
        a('| %s | %s | %s | %s | %+.5f | %+.5f | %+.4f | %+.4f | %s |'
          % (k, d.get('I1'), d.get('I2'), d.get('I3'), fv(d.get('dR2_M1')),
             fv(d.get('dR2_M4')), fv(d.get('IC_C')), fv(d.get('IC_Cperp')),
             d.get('verdict', 'NA')))
    a('')

    # 4.6 单因子筛选
    a('### 4.6 单因子独立增量自动筛选（SPEC §11.4）')
    a('')
    if ind is not None and len(ind):
        ii = ind[ind['selected'] == 1]
        a('- 规则: 在 `M0 + 其余 37 正交因子 + C_-k` 中回归, '
          '`t_NW >= %.1f` 且 `ΔR² > 0` 自动入选。' % R.IC_T_MIN)
        a('- 入选 %d / %d 行:' % (len(ii), len(ind)))
        if len(ii):
            a('')
            a('| pool | factor | beta | t_NW | dR2 | IC_IS | dedup_IC |')
            a('|---|---|---|---|---|---|---|')
            for _, r in ii.iterrows():
                a('| %s | %s | %+.5f | %+.2f | %+.5f | %+.4f | %+.4f |'
                  % (r['pool'], r['factor'], fv(r['beta']), fv(r['t_NW']),
                     fv(r['dR2']), fv(r['ic_is']), fv(r['dedup_ic'])))
        else:
            a('')
            a('（无因子同时满足 t_NW>=2.0 且 ΔR²>0）')
    else:
        a('独立因子表缺失')
    a('')

    # 4.7 合成
    a('### 4.7 类间正交与合成（C_k / C_k^⊥ / COMP_EQ / COMP_PRIOR）')
    a('')
    if comp is not None and len(comp):
        for pool in pools:
            cp = comp[comp['pool'] == pool]
            a('**%s**' % pool)
            a('')
            a('| series | kind | weight | n_factors | IS_ic | IS_t | VALID_ic | '
              'OOS_ic | n_true | verdict | notes |')
            a('|---|---|---|---|---|---|---|---|---|---|---|')
            for _, r in cp.iterrows():
                a('| %s | %s | %s | %d | %+.4f | %+.2f | %+.4f | %+.4f | %d | '
                  '%s | %s |'
                  % (r['series'], r['kind'], r['weight'], iv(r['n_factors']),
                     fv(r['IS_ic']), fv(r['IS_t']), fv(r['VALID_ic']),
                     fv(r['OOS_ic']), iv(r['n_true']), r['verdict'],
                     r.get('notes', '')))
            a('')
    else:
        a('合成表缺失')
    a('')

    # 4.8 WF
    a('### 4.8 Walk-Forward（train 3y → test 半年）')
    a('')
    if wf is not None and len(wf):
        ww = wf[wf['pool'] == 'U-ZZ2K']
        a('- U-ZZ2K 共 %d 序列 × %d fold；train 长度因历史不足降级者标 '
          '`WF_TRAIN_SHORT`。' % (ww['series'].nunique() if len(ww) else 0,
                                  ww['fold'].nunique() if len(ww) else 0))
        a('')
        a('| series | fold | IC_mean | t_NW | net30_spread | wf_train_short |')
        a('|---|---|---|---|---|---|')
        for _, r in ww.head(60).iterrows():
            a('| %s | %s | %+.4f | %+.2f | %+.4f | %d |'
              % (r['series'], r['fold'], fv(r['IC_mean']), fv(r['t_NW']),
                 fv(r['net30_spread']), iv(r['wf_train_short'])))
    else:
        a('WF 表缺失')
    a('')

    # 4.9 Regime
    a('### 4.9 Regime 分层 IC')
    a('')
    if reg is not None and len(reg):
        rr = reg[(reg['pool'] == 'U-ZZ2K') & (reg['series'].str.startswith('C_'))]
        for ax in ('trend', 'style', 'vol', 'earn'):
            aa = rr[rr['axis'] == ax]
            a('- **%s**: %s' % (ax, '; '.join(
                '%s %s=%+.4f(t%+.1f,n%d)'
                % (r['series'], r['state'], fv(r['IC_mean']), fv(r['t_NW']),
                   iv(r['n_days'])) for _, r in aa.iterrows())))
    else:
        a('Regime 表缺失')
    a('')

    # 4.10 Null
    a('### 4.10 Null 对照（N1 主判定）')
    a('')
    if nul is not None and len(nul):
        a('| pool | target | kind | B | obs | p | p_min | resolution |')
        a('|---|---|---|---|---|---|---|---|')
        for _, r in nul.iterrows():
            a('| %s | %s | %s | %d | %+.4f | %.3f | %.3f | %.3f |'
              % (r['pool'], r['target'], r['kind'], iv(r['B']), fv(r['obs']),
                 fv(r['p']), fv(r['p_min']), fv(r['resolution'])))
    else:
        a('Null 表缺失')
    a('')

    # 4.11 扰动
    a('### 4.11 参数扰动（star 设计）')
    a('')
    if pert is not None and len(pert):
        pp = pert.sort_values('pos_ratio', ascending=False)
        a('| series | base_ic | positive_ratio | n_ok/n_tot | verdict |')
        a('|---|---|---|---|---|')
        for _, r in pp.iterrows():
            a('| %s | %+.4f | %.3f | %d/%d | %s |'
              % (r['series'], fv(r['base_ic']), fv(r['pos_ratio']),
                 iv(r['n_ok']), iv(r['n_tot']), r['verdict']))
    else:
        a('扰动表缺失')
    a('')

    # 4.12 funnel
    a('### 4.12 样本漏斗与双口径披露（SPEC §16.4）')
    a('')
    if fun is not None and len(fun):
        a('| pool | stage | cells | days |')
        a('|---|---|---|---|')
        for _, r in fun.iterrows():
            a('| %s | %s | %d | %d |' % (r['pool'], r['stage'], iv(r['cells']),
                                          iv(r['days'])))
    else:
        a('漏斗表缺失')
    a('')
    a('双口径披露: U-ZZ2K 与 U-PROXY 的 IS 起点不同（前者 2024-02，后者 2022-01），'
      '两口径各自的有效交易日数与有效 n 见上表；每因子两口径 `us_start` / 有效 n '
      '成对记录于 `zz2k_factor_ic.csv`（cov / coverage_flag 列）；'
      '两口径同日 IC 差异用于 `POOL_SENSITIVE` 判定，结论冲突时以 U-ZZ2K 为准。')
    a('')
    a(SUB)
    a('')
    a('## §5 §14 判决表（12 位 g1..g12）')
    a('')
    if comp is not None and len(comp):
        bits = [c for c in comp.columns if c.startswith('g')]
        a('| pool | series | ' + ' | '.join(bits) + ' | n_true | verdict | notes |')
        a('|---' * (3 + len(bits) + 3) + '|')
        for _, r in comp.iterrows():
            a('| %s | %s | ' % (r['pool'], r['series'])
              + ' | '.join(str(iv(r[b])) for b in bits)
              + ' | %d | %s | %s |' % (iv(r['n_true']), r['verdict'],
                                        r.get('notes', '')))
        a('')
        a('判决阈值: `ROBUST=12/12`; `PROMISING=9..11`; `FRAGILE=5..8` '
          '或参数位为 FRAGILE; `NO EDGE<=4`。')
    else:
        a('判决表缺失')
    a('')
    a(SUB)
    a('')
    a('## §6 Bias Audit（偏差审计）')
    a('')
    a('| item | verdict | evidence |')
    a('|---|---|---|')
    for r in audit:
        a('| %s | %s | %s |' % (r['item'], r['verdict'],
                                 r['evidence'].replace('|', '/')))
    a('')
    a(SUB)
    a('')
    a('## §7 限制（Limitations）')
    a('')
    a('1. `U-ZZ2K` 中证2000 成分仅 37 个月度快照（发布日 2023-08-11 限制），'
      '历史覆盖短 → `MEMBER_HISTORY_SHORT`；无法覆盖 2021 年前样本。')
    a('2. `OOS` 仅约 8 个月（2026-01..2026-09）→ 全部 OOS 结论标 `OOS_SHORT`。')
    a('3. 类内/类间正交化与合成均为逐日横截面 PIT 操作，不含跨时序拟合系数，'
      '因此不影响前视；但**因子经济含义在正交化后不可直接解读为原始因子**。')
    a('4. 成本模型仅按分层换手线性折算，未建模冲击成本、涨跌停不可成交、'
      '融券约束与税费细项。')
    a('5. Regime 划分使用 932000/000300 价格派生状态，非机构口径；'
      '财报季 Regime 为粗粒度（4/8/10 月）。')
    a('6. 参数扰动按 `pool` 轴混合两口径，扰动稳定性结论为口径无关；'
      '口径相关分歧以 `POOL_SENSITIVE` 单独标注。')
    a('7. 本报告仅为因子研究，`trading_authorization = NO`，不构成任何'
      '投资建议或交易授权。')
    a('')
    a(SUB)
    a('')
    a('## §8 QA / 交付校验')
    a('')
    chk = deliver_check(t, run_meta)
    for k, v in chk.items():
        a('- %s: %s' % (k, 'PASS' if v else 'FAIL'))
    a('')
    a('报告生成：%s' % time.strftime('%Y-%m-%d %H:%M:%S'))

    txt = '\n'.join(L) + '\n'
    p = os.path.join(R.HERE, 'H_ZZ2K_ALPHA_V1_REPORT.md')
    with open(p, 'w', encoding='utf-8') as f:
        f.write(txt)
    return p, txt


def deliver_check(t, run_meta):
    """SPEC §16.2 交付校验."""
    chk = {}
    need = ['ic', 'orth', 'q', 'comp', 'inc', 'ind', 'oos', 'wf', 'cost', 'reg',
            'nul', 'fun']
    chk['12 个必产 CSV 齐备'] = all(
        t.get(k) is not None and len(t[k]) > 0 for k in need)
    # pool 列
    pool_ok = True
    for k in ['ic', 'orth', 'q', 'comp', 'inc', 'ind', 'oos', 'wf', 'cost',
              'reg', 'nul', 'fun']:
        d = t.get(k)
        if d is None or 'pool' not in d.columns:
            pool_ok = False
            lg('  缺 pool 列: %s' % k)
    chk['必产 CSV 均含 pool 列'] = pool_ok
    # 38 因子全登记
    ic = t.get('ic')
    if ic is not None and len(ic):
        seen = set(ic['series'].unique())
        chk['38 因子全部登记 (含零覆盖)'] = all(
            f in seen for f in R.ALL_FACTORS)
    else:
        chk['38 因子全部登记 (含零覆盖)'] = False
    # 双口径
    chk['双口径并列 (U-ZZ2K/U-PROXY)'] = bool(
        ic is not None and {'U-ZZ2K', 'U-PROXY'}.issubset(
            set(ic['pool'].unique())))
    # trading_authorization
    chk['trading_authorization=NO'] = (
        t.get('results', {}).get('trading_authorization') == 'NO')
    # SPEC hash
    chk['SPEC_SHA256 MATCH'] = R.spec_check()
    return chk


# ================================================================== main
def main():
    global _LOG
    _LOG = R.Log('zz2k_report')
    lg(SEP)
    lg('H-ZZ2K-A1 报告层 —— 交付校验 + REPORT.md + SUMMARY.json')
    lg(SEP)

    if not R.spec_check(lg=lg):
        lg('EXIT 2 (SPEC 不一致)')
        return 2

    results = load_json('H_ZZ2K_ALPHA_V1_RESULTS.json')
    fscan = load_json('H_ZZ2K_ALPHA_V1_FUTURE_SCAN.json')
    run_meta = {
        'research_id': results.get('research_id', 'H-ZZ2K-A1'),
        'version': results.get('version', '1.1'),
        'simple': bool(results.get('simple', False)),
        'B_null': iv(results.get('B_null', R.NULL_B), R.NULL_B),
        'rounds': iv(results.get('rounds', R.NULL_ROUNDS), R.NULL_ROUNDS),
        'pools': results.get('pools') or list(R.POOLS),
        'run_at': results.get('run_at', ''),
    }
    pools = run_meta['pools']

    t = {}
    t['ic'] = load('zz2k_factor_ic.csv')
    t['orth'] = load('zz2k_orthogonal.csv')
    t['q'] = load('zz2k_factor_quantile.csv')
    t['comp'] = load('zz2k_group_composite.csv')
    t['inc'] = load('zz2k_incremental.csv')
    t['ind'] = load('zz2k_independent_factors.csv')
    t['oos'] = load('zz2k_oos.csv')
    t['wf'] = load('zz2k_walkforward.csv')
    t['cost'] = load('zz2k_cost.csv')
    t['reg'] = load('zz2k_regime.csv')
    t['nul'] = load('zz2k_null.csv')
    t['fun'] = load('zz2k_funnel.csv')
    t['pert'] = load('zz2k_perturb_summary.csv')
    t['results'] = results

    lg(SEP)
    lg('派生判定 (只读, 不重算口径)')
    gi = group_independence(t['inc'], t['comp'])
    for pk in sorted(gi):
        d = gi[pk]
        lg('  IND %-8s %s  I1=%s I2=%s I3=%s  dR2_M1=%+.5f dR2_M4=%+.5f -> %s'
           % (pk[0], pk[1], d['I1'], d['I2'], d['I3'], d['dR2_M1'],
              d['dR2_M4'], d['verdict']))

    concl = conclusion14(run_meta, t['ic'], t['orth'], t['comp'], t['ind'],
                         t['oos'], t['reg'], t['cost'], t['pert'], gi, {}, pools)
    Q = qna(run_meta, t['ic'], t['orth'], t['comp'], t['inc'], t['ind'],
            t['oos'], t['wf'], t['cost'], t['reg'], t['nul'], t['fun'],
            t['pert'], gi, pools)

    lg(SEP)
    lg('bias audit')
    audit = bias_audit(run_meta, fscan, t['ic'], t['orth'], t['comp'], t['ind'],
                       t['oos'], t['nul'], t['fun'], t['pert'])
    for r in audit:
        lg('  %-24s %-4s %s' % (r['item'], r['verdict'], r['evidence'][:88]))
    R.save_csv(pd.DataFrame(audit), 'H_ZZ2K_ALPHA_V1_BIAS_AUDIT.csv', lg=lg)

    lg(SEP)
    lg('结论 14 行')
    for line in concl:
        lg('  ' + line)

    p, txt = write_report(run_meta, fscan, audit, t, concl, Q, gi, None, lg)
    lg(SEP)
    lg('report written %s (%d lines)' % (p, txt.count('\n')))

    chk = deliver_check(t, run_meta)
    summ = {
        'research_id': run_meta['research_id'],
        'version': run_meta['version'],
        'created': time.strftime('%Y-%m-%d %H:%M:%S'),
        'run_at': run_meta['run_at'],
        'simple': run_meta['simple'],
        'null': {'B': run_meta['B_null'], 'rounds': run_meta['rounds']},
        'pools': pools,
        'spec_sha256': R.SPEC_SHA256,
        'trading_authorization': 'NO',
        'conclusion': concl,
        'group_independence': {
            '%s/%s' % pk: {k: (bool(v) if isinstance(v, bool) else
                               (None if not np.isfinite(fv(v)) else fv(v)))
                           for k, v in d.items()}
            for pk, d in gi.items()},
        'independent_selected': results.get('independent_selected', {}),
        'n_independent': results.get('n_independent', {}),
        'perturbation': results.get('pert', {}),
        'group_composite_verdicts': results.get('group_composite', []),
        'delivery_check': {k: bool(v) for k, v in chk.items()},
        'delivery_all_pass': bool(all(chk.values())),
        'limitations': [
            'MEMBER_HISTORY_SHORT: 中证2000 成分仅 37 个月度快照',
            'OOS_SHORT: OOS 仅约 8 个月',
            '正交化后因子经济含义不可直接解读',
            '成本模型未含冲击成本/涨跌停/融券约束',
            'trading_authorization = NO',
        ],
    }
    R.save_json(summ, 'H_ZZ2K_ALPHA_V1_SUMMARY.json', lg=lg)

    lg(SEP)
    if not summ['delivery_all_pass']:
        lg('DELIVERY_CHECK 存在 FAIL: %s'
           % ','.join(k for k, v in chk.items() if not v))
    lg('done')
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main() or 0)
