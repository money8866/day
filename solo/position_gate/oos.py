# -*- coding: utf-8 -*-
"""CSI2000 Position Gate —— §25 参数稳定性 + §26 Walk-Forward / OOS

纪律（§30）：
  1) 本文件不做「用 OOS 结果反向优化参数」。全部变体只用于检验规则在邻近参数下是否稳健。
  2) IS 选优 → OOS 检验仅作为「防过拟合对照」，选优动作只发生在 IS 段内。
  3) 严格按时间切分，绝不随机打乱。
"""
import os
import pickle

import numpy as np
import pandas as pd

from position_gate import config as C
from position_gate import backtest as B
from position_gate import features as F
from position_gate import regime as R
from position_gate.data import GateData


# =========================================================
# §25 变体定义
# =========================================================
def variant_grid() -> list:
    """邻近参数扰动网格（单参数依次扰动，不做组合爆炸）"""
    g = [dict(name='baseline(20/60/20/2/10)', group='baseline', value=None, kw={}, th={}, steps={})]
    for v in (15, 20, 25):
        g.append(dict(name=f'MA中枢={v}', group='MA_pivot', value=v,
                      kw={'pivot_ma': v}, th={}, steps={}))
    for v in (0.55, 0.60, 0.65, 0.70):
        g.append(dict(name=f'TREND宽度阈值={v:.0%}', group='Breadth_th', value=v,
                      kw={}, th={'trend_breadth': v}, steps={}))
    for v in (10, 20, 30):
        g.append(dict(name=f'RS窗口={v}', group='RS_window', value=v,
                      kw={'rs_main': v, 'rs_windows': (5, 20, 60)}, th={}, steps={}))
    for v in (1, 2, 3):
        g.append(dict(name=f'RiskOff事件数={v}', group='RiskOff_count', value=v,
                      kw={}, th={'off_riskoff': v}, steps={}))
    for v in (5.0, 10.0, 15.0):
        g.append(dict(name=f'加仓幅度={v:.0f}%', group='Step_up', value=v,
                      kw={}, th={},
                      steps={'up': v, 'up_strong': min(v * 1.5, 15.0),
                             'down': C.STEP_DOWN, 'down_risk': C.STEP_DOWN_RISK}))
    # 去重：baseline 与 MA=20 / RS=20 / Breadth=60 / RiskOff=2 / Step=10 等价，只保留一条 baseline
    out, seen = [], set()
    for v in g:
        key = (v['group'], v['value'])
        if key in seen:
            continue
        seen.add(key)
        out.append(v)
    return out


def build_variant(v: dict, data: GateData, pv, uni, src,
                  start=C.DATA_START, end=C.DATA_END) -> pd.DataFrame:
    feat, _ = F.build_all(data=data, start=start, end=end, pv=pv, uni=uni, src=src,
                          verbose=False, **v['kw'])
    return R.run_gate(feat, th=v['th'] or None, steps=v['steps'] or None)


def build_variant_cache(data: GateData, start=C.DATA_START, end=C.DATA_END,
                        variants: list = None, verbose: bool = True,
                        refresh: bool = False) -> dict:
    """一次性构建全部扰动变体的 Gate（供 §25 与滚动 OOS 复用，避免重复计算）"""
    variants = variants or variant_grid()
    path = os.path.join(C.CACHE_DIR, f'variants_{start}_{end}.pkl')
    if not refresh and os.path.exists(path):
        try:
            with open(path, 'rb') as f:
                cache = pickle.load(f)
            if set(cache) == {v['name'] for v in variants}:
                if verbose:
                    print(f'[oos] 命中变体缓存 {os.path.basename(path)}', flush=True)
                return cache
        except Exception:
            pass
    pv = F.load_pivots(data, start, end, verbose=verbose)
    uni, src = F.build_universe(pv, data)
    idx_win = data.index_ohlc(C.CORE_INDEX)
    idx_win = idx_win[(idx_win.index >= start) & (idx_win.index <= end)]
    uni = uni.loc[sorted(set(uni.index) & set(idx_win.index))]
    src = src.loc[uni.index]
    cache = {}
    for k, v in enumerate(variants):
        cache[v['name']] = build_variant(v, data, pv, uni, src, start=start, end=end)
        if verbose:
            print(f"[oos] 变体 {k + 1}/{len(variants)} {v['name']} 完成", flush=True)
    try:
        with open(path, 'wb') as f:
            pickle.dump(cache, f, protocol=4)
    except Exception as e:
        print(f'[oos] 变体缓存写入失败（不影响运行）: {e}')
    return cache


def variant_meta(variants: list = None) -> dict:
    return {v['name']: v for v in (variants or variant_grid())}


# =========================================================
# §25 参数稳定性矩阵
# =========================================================
def param_stability(data: GateData = None, start: str = C.DATA_START, end: str = C.DATA_END,
                    cost_bp: float = 15.0, variants: list = None,
                    verbose: bool = True, cache: dict = None) -> pd.DataFrame:
    """对每个扰动变体输出 全区间 / IS / VAL / OOS 的风险指标，并给出稳健性结论"""
    data = data or GateData()
    variants = variants or variant_grid()
    if cache is None:
        cache = build_variant_cache(data, start, end, variants, verbose=verbose)

    ref = None
    rows = []
    for v in variants:
        gate = B.trim_warmup(cache[v['name']])
        segs = {'ALL': (start, end), **C.SPLITS}
        for seg, (s, e) in segs.items():
            sub = gate[(gate.index >= s) & (gate.index <= e)]
            if sub.empty:
                continue
            bt = B.daily_returns(sub, cost_bp=cost_bp)
            m = B.metrics(bt, cost_bp=cost_bp)
            b100 = B.metrics(B.const_returns(sub, 100.0, cost_bp=cost_bp), cost_bp=cost_bp)
            rows.append({
                'group': v['group'], 'variant': v['name'], 'param_value': v['value'],
                'segment': seg, 'n_days': len(sub),
                'CAGR_%': m['CAGR_%'], 'vol_%': m['vol_%'], 'maxDD_%': m['maxDD_%'],
                'Calmar': m['Calmar'], 'Sharpe': m['Sharpe'],
                'avg_position_%': m['avg_position_%'], 'flat_days_%': m['flat_days_%'],
                'maxDD_B100_%': b100['maxDD_%'],
                'maxDD_改善_pp': round(m['maxDD_%'] - b100['maxDD_%'], 2),
            })
            if v['group'] == 'baseline' and seg == 'ALL':
                ref = m

    df = pd.DataFrame(rows)
    # 稳健性判定：以全区间 maxDD 与 Calmar 相对 baseline 的偏离衡量
    if ref is not None:
        base_dd = ref['maxDD_%']
        base_calmar = ref['Calmar']
        verdict = []
        for _, r in df.iterrows():
            if r['segment'] != 'ALL':
                verdict.append('')
                continue
            dd_ratio = (r['maxDD_%'] / base_dd) if (np.isfinite(base_dd) and base_dd != 0) else np.nan
            cal_ok = (not np.isfinite(base_calmar) or base_calmar == 0
                      or (np.isfinite(r['Calmar']) and r['Calmar'] >= 0.7 * base_calmar))
            if not np.isfinite(dd_ratio):
                verdict.append('样本不足')
            elif dd_ratio <= 1.25 and cal_ok:
                verdict.append('稳健(邻近参数结论不变)')
            else:
                verdict.append(f'敏感(maxDD×{dd_ratio:.2f})')
        df['稳健性'] = verdict
    return df


# =========================================================
# §26 逐年 / 滚动 OOS
# =========================================================
def annual_table(gate: pd.DataFrame, cost_bp: float = 15.0) -> pd.DataFrame:
    """逐年 Gate vs 固定仓位基准（§23 指标口径）"""
    gate = B.trim_warmup(gate)
    rows = []
    for y in sorted({d[:4] for d in gate.index}):
        sub = gate[[d[:4] == y for d in gate.index]]
        if len(sub) < 20:
            continue
        rec = {'year': y, 'n_days': len(sub)}
        bt = B.daily_returns(sub, cost_bp=cost_bp)
        m = B.metrics(bt, cost_bp=cost_bp)
        rec.update({f'gate_{k}': m[k] for k in ('CAGR_%', 'vol_%', 'maxDD_%', 'Calmar', 'avg_position_%')})
        for name, expo in C.BENCHMARKS.items():
            mb = B.metrics(B.const_returns(sub, expo, cost_bp=cost_bp), cost_bp=cost_bp)
            rec.update({f'{name}_CAGR_%': mb['CAGR_%'], f'{name}_maxDD_%': mb['maxDD_%']})
        rec['maxDD_改善_pp'] = round(rec['gate_maxDD_%'] - rec['B100_maxDD_%'], 2)
        rows.append(rec)
    return pd.DataFrame(rows)


def rolling_oos(gate: pd.DataFrame, cost_bp: float = 15.0,
                train_years: int = 3, test_years: int = 1,
                cache: dict = None) -> pd.DataFrame:
    """滚动「训练窗(诊断) + 测试窗(检验)」

    本 Gate 为规则型（无可拟合参数），故训练窗只用于回答一个问题：
    「在训练窗内表现最好的邻近参数组合，其下一个测试年是否仍然成立？」
    这是防过拟合检验，不是把测试结果用于调参。
    """
    years = sorted({int(d[:4]) for d in gate.index})
    if not years:
        return pd.DataFrame()
    if cache is None:
        cache = build_variant_cache(GateData(), verbose=False)
    cache = {k: B.trim_warmup(v) for k, v in cache.items()}

    rows = []
    for ty in years:
        tr_s, tr_e = f'{ty - train_years}0101', f'{ty - 1}1231'
        te_s, te_e = f'{ty}0101', f'{ty}1231'
        if tr_s < C.DATA_START:
            continue
        best, best_calmar = None, -np.inf
        for name, g in cache.items():
            tr = g[(g.index >= tr_s) & (g.index <= tr_e)]
            te = g[(g.index >= te_s) & (g.index <= te_e)]
            if len(tr) < 120 or len(te) < 20:
                continue
            cal = B.metrics(B.daily_returns(tr, cost_bp=cost_bp), cost_bp=cost_bp)['Calmar']
            if np.isfinite(cal) and cal > best_calmar:
                best, best_calmar = name, cal
        if best is None:
            continue
        te = cache[best][(cache[best].index >= te_s) & (cache[best].index <= te_e)]
        if te.empty:
            continue
        m = B.metrics(B.daily_returns(te, cost_bp=cost_bp), cost_bp=cost_bp)
        base = cache['baseline(20/60/20/2/10)']
        te_b = base[(base.index >= te_s) & (base.index <= te_e)]
        mb = B.metrics(B.daily_returns(te_b, cost_bp=cost_bp), cost_bp=cost_bp)
        b100 = B.metrics(B.const_returns(te, 100.0, cost_bp=cost_bp), cost_bp=cost_bp)
        rows.append({
            'test_year': ty, 'train_window': f'{tr_s}~{tr_e}',
            'IS_best_variant': best, 'IS_Calmar': round(best_calmar, 2),
            'OOS_CAGR_%': m['CAGR_%'], 'OOS_maxDD_%': m['maxDD_%'], 'OOS_Calmar': m['Calmar'],
            'baseline_OOS_Calmar': mb['Calmar'], 'baseline_OOS_maxDD_%': mb['maxDD_%'],
            'B100_OOS_maxDD_%': b100['maxDD_%'],
            'OOS_maxDD_改善_pp': round(m['maxDD_%'] - b100['maxDD_%'], 2),
        })
    return pd.DataFrame(rows)


# =========================================================
# §27 Q7 仓位阶梯结构比较
# =========================================================
LADDER_CANDIDATES = {
    '0/20/40/60/80/100(§11 六档)': 'six',
    '0/25/50/75/100(五档)': 'five',
    '0/10/25/45/65/85/100(七档)': 'seven',
}


def _ladder_bands(kind: str, th: dict = None) -> dict:
    th = {**C.REGIME_TH, **(th or {})}
    if kind == 'five':
        return {'OFF': (0.0, 6.0, 12.5), 'RECOVERY': (12.5, 18.75, 25.0),
                'RANGE': (25.0, 37.5, 50.0), 'EXPANSION': (50.0, 62.5, 75.0),
                'TREND': (75.0, 87.5, 100.0), 'STRONG_TREND': (75.0, 87.5, 100.0)}
    if kind == 'seven':
        return {'OFF': (0.0, 5.0, 10.0), 'RECOVERY': (10.0, 17.5, 25.0),
                'RANGE': (25.0, 35.0, 45.0), 'EXPANSION': (45.0, 55.0, 65.0),
                'TREND': (65.0, 75.0, 85.0), 'STRONG_TREND': (85.0, 92.5, 100.0)}
    return dict(C.REGIME_BANDS)


def ladder_compare(feat: pd.DataFrame, cost_bp: float = 15.0) -> pd.DataFrame:
    """§27 Q7：比较不同仓位阶梯结构的 OOS 表现"""
    feat = F.as_raw_feat(feat)
    rows = []
    for label, kind in LADDER_CANDIDATES.items():
        gate = R.run_gate(feat, bands=_ladder_bands(kind))
        gate = B.trim_warmup(gate)
        for seg, (s, e) in {'ALL': (C.DATA_START, C.DATA_END), **C.SPLITS}.items():
            sub = gate[(gate.index >= s) & (gate.index <= e)]
            if len(sub) < 20:
                continue
            m = B.metrics(B.daily_returns(sub, cost_bp=cost_bp), cost_bp=cost_bp)
            rows.append({'ladder': label, 'segment': seg, 'n_days': len(sub),
                         'CAGR_%': m['CAGR_%'], 'vol_%': m['vol_%'], 'maxDD_%': m['maxDD_%'],
                         'Calmar': m['Calmar'], 'Sharpe': m['Sharpe'],
                         'avg_position_%': m['avg_position_%'],
                         'turnover_per_year': m['turnover_per_year']})
    return pd.DataFrame(rows)


if __name__ == '__main__':
    pd.set_option('display.width', 260)
    g = pd.read_pickle('position_gate/cache/gate.pkl')
    print(annual_table(g).to_string(index=False))
    print(rolling_oos(g).to_string(index=False))
