# -*- coding: utf-8 -*-
"""首板 → 第一次分歧 → 缩量止跌 → 再启动 · 扩展层

  §28 分期：TRAIN / VALIDATION / OOS 单列，禁止混算
  §29 Walk-Forward：滚动窗口，观察 T+3/T+5 方向是否一致
  §30 样本量纪律：N<50 描述 / N<100 不作核心结论 / N>=200 进入核心比较
  §32 失败案例：失败模式统计 + 共同特征
  §34 HVT 正交：只比较，不修改 HVT

输入（全部为本研究自建产物 + 只读外部产物）
  out/tr_mats.npz                     窗口矩阵
  out/tr_base.parquet                 事件主表
  three_stage_rebound_events_*.csv    事件链回溯表（含 A–G 组标志与锚点）
  three_stage_rebound_features_*.csv  分歧锚定特征表
  out/panel.parquet                   全市场面板（仅用于 HVT 事件的前瞻收益）
  ../report_daily/hvt_bull_backtest_events_20250101_20260828.csv  HVT 事件层回测明细

输出
  three_stage_rebound_oos_<date>.csv
  three_stage_rebound_failures_<date>.csv
  out/tr_ext_meta.json
"""
import os
import json
import warnings
import numpy as np
import pandas as pd

import _tr_lib as L

warnings.filterwarnings('ignore', category=RuntimeWarning)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')
DATE = '20260925'
HOR = L.HOR
COST = L.COST_DEF
MIN_N_CORE = 200
MIN_N_DESC = 50

print('载入 ...')
m = L.load_mats(os.path.join(OUT, 'tr_mats.npz'))
base = pd.read_parquet(os.path.join(OUT, 'tr_base.parquet'))
ev = pd.read_csv(os.path.join(HERE, 'three_stage_rebound_events_%s.csv' % DATE),
                 encoding='utf-8-sig', dtype={'t0_date': str, 'fd_date': str,
                                              'restart_date': str, 'entry_date': str})
ft = pd.read_csv(os.path.join(HERE, 'three_stage_rebound_features_%s.csv' % DATE),
                 encoding='utf-8-sig')
n = len(base)
yy = base['yr'].values
mass = (base['listed_days'].values >= 60) & base['clean'].values.astype(bool)
KD = int(ev['kd'].iloc[0])

GMSK = {
    'A_全部首板': mass,
    'B_第一次分歧': ev['in_B'].values.astype(bool),
    'C_分歧+缩量': ev['in_C'].values.astype(bool),
    'D_分歧+缩量+结构未破': ev['in_D'].values.astype(bool),
    'E_分歧+缩量+再启动': ev['in_E'].values.astype(bool),
    'F_分歧+缩量+结构未破+再启动': ev['in_F'].values.astype(bool),
    'G_F+再启动量能确认': ev['in_G'].values.astype(bool),
}
_z = np.zeros(n, dtype=np.int64)
ANCH = {
    'A_全部首板': _z,
    'B_第一次分歧': np.clip(ev['fd_offset'].values, 0, L.KMAX).astype(np.int64),
    'C_分歧+缩量': np.clip(ev['fd_offset'].values + KD, 0, L.KMAX).astype(np.int64),
    'D_分歧+缩量+结构未破': np.clip(ev['fd_offset'].values + KD, 0, L.KMAX).astype(np.int64),
    'E_分歧+缩量+再启动': np.clip(ev['restart_offset'].values, 0, L.KMAX).astype(np.int64),
    'F_分歧+缩量+结构未破+再启动': np.clip(ev['restart_offset'].values, 0, L.KMAX).astype(np.int64),
    'G_F+再启动量能确认': np.clip(ev['restart_offset'].values, 0, L.KMAX).astype(np.int64),
}
mass_dev = mass & np.isin(yy, [2021, 2022, 2023])          # TRAIN
mass_val = mass & np.isin(yy, [2024, 2025])                # VALIDATION
mass_oos = mass & (yy >= 2026)                             # OOS
PERIOD_MSK = {'TRAIN': mass_dev, 'VALID': mass_val, 'OOS': mass_oos,
              'ALL': mass}


def mk_bench(pm, amax=None):
    if amax is None:
        amax = L.NA
    return {N: L.offset_bench(m, N, COST, mask=pm, min_bucket=20, amax=amax) for N in HOR}


BENCH = {k: mk_bench(v) for k, v in PERIOD_MSK.items()}


def perf(mk, anc, bench, cost=COST, min_bucket=20):
    mk = np.asarray(mk, dtype=bool)
    o = {'N': int(mk.sum())}
    for N in HOR:
        v = L.fwd_ret(m, anc, N, cost)
        st = L.stats(v[mk])
        o['m%d' % N] = st['med'] * 100
        o['a%d' % N] = st['mean'] * 100
        o['w%d' % N] = st['win'] * 100
        o['f%d' % N] = st['pf']
        ex, den, posb, t, nb = L.paired_excess(m, mk, anc, N, cost, bench=bench[N],
                                              min_bucket=min_bucket)
        o['e%d' % N] = ex * 100
        o['t%d' % N] = t
        o['b%d' % N] = nb
    return o


def r3(x):
    try:
        return round(float(x), 3)
    except Exception:
        return np.nan


def row_of(section, gname, gmk, ganc, period, bench):
    d = perf(gmk, ganc, bench)
    r = {'section': section, 'group': gname, 'period': period, 'N': d['N']}
    for N in HOR:
        r['T%d_med' % N] = r3(d['m%d' % N])
        r['T%d_mean' % N] = r3(d['a%d' % N])
        r['T%d_win' % N] = r3(d['w%d' % N])
        r['T%d_pf' % N] = r3(d['f%d' % N])
        r['T%d_ex' % N] = r3(d['e%d' % N])
        r['T%d_exT' % N] = r3(d['t%d' % N])
        r['T%d_buckets' % N] = d['b%d' % N]
    return r


# ══════════════════════ §28 分期单列 ══════════════════════
print('\n[OOS] 分期单列 (TRAIN 2021-2023 / VALID 2024-2025 / OOS 2026)')
OROWS = []
for gname, gmk in GMSK.items():
    for pn, pmsk in PERIOD_MSK.items():
        OROWS.append(row_of('OOS_分期', gname, gmk & pmsk, ANCH[gname], pn, BENCH[pn]))

# 年度序列（不做任何参数选择，只观察方向一致性）
print('[OOS] 年度序列')
for gname, gmk in GMSK.items():
    for y in sorted(set(yy[mass].tolist())):
        pmsk = mass & (yy == y)
        OROWS.append(row_of('OOS_年度', gname, gmk & pmsk, ANCH[gname], int(y), BENCH['ALL']))

# ══════════════════════ §29 Walk-Forward ══════════════════════
print('[WF] Walk-Forward')
WF = [([2021, 2022], 2023), ([2022, 2023], 2024), ([2023, 2024], 2025), ([2024, 2025], 2026)]
WROWS = []
for tr_y, te_y in WF:
    pm_tr = mass & np.isin(yy, tr_y)
    pm_te = mass & (yy == te_y)
    b = mk_bench(pm_tr)
    for gname, gmk in GMSK.items():
        d = perf(gmk & pm_te, ANCH[gname], b)
        r = {'section': 'WF_窗口', 'group': gname,
             'period': 'IS:%s->OOS:%d' % ('/'.join(map(str, tr_y)), te_y),
             'N': d['N']}
        for N in HOR:
            r['T%d_med' % N] = r3(d['m%d' % N])
            r['T%d_ex' % N] = r3(d['e%d' % N])
            r['T%d_exT' % N] = r3(d['t%d' % N])
        WROWS.append(r)
OROWS.extend(WROWS)

oos = pd.DataFrame(OROWS + WROWS)
p = os.path.join(HERE, 'three_stage_rebound_oos_%s.csv' % DATE)
oos.to_csv(p, index=False, encoding='utf-8-sig')
print('  已写', p, oos.shape)

# ══════════════════════ §32 失败案例 ══════════════════════
print('\n[FAIL] 失败案例统计')
F_mk = GMSK['F_分歧+缩量+结构未破+再启动']
F_anc = ANCH['F_分歧+缩量+结构未破+再启动']
ar = np.arange(n)
cl = m['cl']; hi = m['hi']; lo = m['lo']; op = m['op']
C0v = m['C0'].astype(np.float64)
rs_off = np.clip(ev['restart_offset'].values, 0, L.KMAX)
r5 = L.fwd_ret(m, F_anc, 5, COST)
mae5, mfe5 = L.fwd_mae_mfe(m, F_anc, 5)
low_to = np.full(n, np.nan); hi_to = np.full(n, np.nan)
for k in range(1, 6):
    j = np.clip(rs_off + k, 0, L.KMAX)
    v = np.where(F_mk & (rs_off + k <= L.KMAX), lo[ar, j], np.nan)
    low_to = np.fmin(low_to, v)
    w = np.where(F_mk & (rs_off + k <= L.KMAX), hi[ar, j], np.nan)
    hi_to = np.fmax(hi_to, w)
gap_open = op[ar, rs_off] / np.where(rs_off > 0, cl[ar, np.clip(rs_off - 1, 0, L.KMAX)], np.nan) - 1
day_is_up = (r5 > 0)
ddmin = ft['ddmin'].values / 100.0
V1 = ft['V1_vr20'].values / 100.0
rvr = ev['restart_vr'].values
T0VR = ft['t0_vr20'].values
dd_fd = ft['dd_at_fd'].values / 100.0
CP_fd = ft['cp_at_fd'].values / 100.0
TURN = ft['to_rate_t0'].values
REG = ft['regime_t0'].astype(str).values

PAT = [
    ('P1_入场后T+5亏损超过5%', r5 <= -0.05),
    ('P2_缩量后跌破首板收盘价', low_to < C0v),
    ('P3_再启动假突破(再启动后T+5<0)', (r5 <= 0)),
    ('P4_放量失败(再启动VR>=1.5且T+5<0)', (rvr >= 1.5) & (r5 <= 0)),
    ('P5_高开低走(开盘>昨收2%且收阴)', (gap_open > 0.02) & (cl[ar, rs_off] < op[ar, rs_off])),
    ('P6_冲高回落(5日内最高>=+5%但T+5<=0)', (mfe5 >= 0.05) & (r5 <= 0)),
    ('P7_跌破首板最低价', low_to < m['L0'].astype(np.float64)),
]
frows = []
base_f = F_mk
for lb, mk in PAT:
    sub = base_f & mk
    d = perf(sub, F_anc, BENCH['ALL'])
    d2 = perf(base_f & (~mk), F_anc, BENCH['ALL'])
    frows.append({'section': 'FAIL_模式', 'group': lb, 'N': d['N'],
                  'share_of_F_pct': r3(100.0 * sub.sum() / max(1, base_f.sum())),
                  'T3_med': r3(d['m3']), 'T5_med': r3(d['m5']), 'T10_med': r3(d['m10']),
                  'T5_win': r3(d['w5']), 'T5_ex': r3(d['e5']),
                  '非该模式_T5_med': r3(d2['m5']), '非该模式_T5_win': r3(d2['w5']),
                  'feat_t0_vr20': r3(np.nanmean(T0VR[sub])),
                  'feat_dd_at_fd_pct': r3(np.nanmean(dd_fd[sub]) * 100),
                  'feat_V1_vr20': r3(np.nanmean(V1[sub])),
                  'feat_restart_vr': r3(np.nanmean(rvr[sub])),
                  'feat_cp_at_fd': r3(np.nanmean(CP_fd[sub])),
                  'feat_turnover_t0': r3(np.nanmean(TURN[sub])),
                  'feat_BULL_pct': r3(100.0 * np.mean(REG[sub] == 'BULL'))})

# 整体失败画像：F 组内 赢家 vs 输家 的特征差异
win_mk = base_f & (r5 > 0)
los_mk = base_f & (r5 <= 0)
diff_rows = []
for nm, arr in (('t0_vr20(首板量比)', T0VR), ('dd_at_fd%(分歧深度)', dd_fd * 100),
                ('V1_vr20(缩量窗口量比)', V1), ('restart_vr(再启动量比)', rvr),
                ('cp_at_fd(分歧日收盘位置)', CP_fd), ('turnover_t0%(首板换手)', TURN),
                ('ddmin%(分歧后最大回撤)', ddmin * 100)):
    a = np.nanmean(arr[win_mk]); b = np.nanmean(arr[los_mk])
    diff_rows.append({'section': 'FAIL_赢输特征差', 'group': nm,
                      'N': int(los_mk.sum()),
                      'T3_med': r3(a), 'T5_med': r3(b), 'T10_med': np.nan,
                      'T5_win': r3(a - b), 'T5_ex': np.nan,
                      'feat_t0_vr20': np.nan})
fail = pd.DataFrame(frows + diff_rows)
p = os.path.join(HERE, 'three_stage_rebound_failures_%s.csv' % DATE)
fail.to_csv(p, index=False, encoding='utf-8-sig')
print('  已写', p, fail.shape)

# ══════════════════════ §34 HVT 正交（只比较，不改 HVT） ══════════════════════
print('\n[HVT] 正交验证')
hvt_path = os.path.join(HERE, '..', 'report_daily',
                        'hvt_bull_backtest_events_20250101_20260828.csv')
HROWS = []
HVT_LO, HVT_HI = 20250101, 20260828
if os.path.exists(hvt_path):
    hv = pd.read_csv(hvt_path, encoding='utf-8-sig',
                     usecols=['ts_code', 't0_date', 'score', 'grade', 'final_state', 'v3_state'])
    hv = hv.dropna(subset=['ts_code', 't0_date'])
    hv['t0_date'] = hv['t0_date'].astype(np.int64)
    hv = hv[(hv['t0_date'] >= HVT_LO) & (hv['t0_date'] <= HVT_HI)]
    print('  HVT 事件', len(hv), '区间', hv['t0_date'].min(), '~', hv['t0_date'].max())

    pan = pd.read_parquet(os.path.join(OUT, 'panel.parquet'),
                          columns=['ts_code', 'trade_date', 'a_close'])
    pan = pan.sort_values(['ts_code', 'trade_date'], kind='mergesort')
    LUT = {}
    for c, gdf in pan.groupby('ts_code', sort=False):
        LUT[c] = (gdf['trade_date'].to_numpy(), gdf['a_close'].to_numpy(dtype=np.float64))
    del pan

    def fwd_px(code, d, k, cost=COST):
        v = LUT.get(code)
        if v is None:
            return np.nan
        dd, cc = v
        i = int(np.searchsorted(dd, d))
        if i >= dd.size or dd[i] != d:
            return np.nan
        j = i + k
        if j >= dd.size:
            return np.nan
        c0 = cc[i]; c1 = cc[j]
        if not np.isfinite(c0) or not np.isfinite(c1) or c0 <= 0:
            return np.nan
        return c1 / c0 - 1.0 - cost

    hv['hvt_r3'] = [fwd_px(c, int(d), 3) for c, d in zip(hv['ts_code'], hv['t0_date'])]
    hv['hvt_r5'] = [fwd_px(c, int(d), 5) for c, d in zip(hv['ts_code'], hv['t0_date'])]
    hvf = hv.dropna(subset=['hvt_r5'])
    print('  HVT 有效前瞻样本', len(hvf), '| T+3 中位 %.2f%% | T+5 中位 %.2f%%'
          % (hvf['hvt_r3'].median() * 100, hvf['hvt_r5'].median() * 100))

    # 我方样本限制在同区间
    my_date = ev['t0_date'].astype(np.int64).values
    in_win = (my_date >= HVT_LO) & (my_date <= HVT_HI)
    for gname in ('B_第一次分歧', 'D_分歧+缩量+结构未破',
                  'F_分歧+缩量+结构未破+再启动', 'G_F+再启动量能确认'):
        gmk = GMSK[gname] & in_win
        r3m = L.fwd_ret(m, ANCH[gname], 3, COST)
        r5m = L.fwd_ret(m, ANCH[gname], 5, COST)
        my_codes = set(ev['ts_code'].values[gmk])
        hvt_codes = set(hvf['ts_code'].values)
        inter = my_codes & hvt_codes
        HROWS.append({'section': 'HVT_对照', 'group': gname, 'period': '可交易口径',
                      'N': int(gmk.sum()),
                      'T3_med': r3(np.nanmedian(r3m[gmk]) * 100),
                      'T5_med': r3(np.nanmedian(r5m[gmk]) * 100),
                      'T5_win': r3(np.nanmean(r5m[gmk] > 0) * 100),
                      'T5_ex': np.nan,
                      'hvt_N': len(hvf),
                      'overlap_codes': len(inter),
                      'overlap_pct_of_mine': r3(100.0 * len(inter) / max(1, len(my_codes))),
                      'overlap_pct_of_hvt': r3(100.0 * len(inter) / max(1, len(hvt_codes))),
                      'note': '区间 %d~%d' % (HVT_LO, HVT_HI)})
    HROWS.append({'section': 'HVT_自身', 'group': 'HVT-BULL 事件层(同口径重算)',
                  'period': '可交易口径', 'N': len(hvf),
                  'T3_med': r3(hvf['hvt_r3'].median() * 100),
                  'T5_med': r3(hvf['hvt_r5'].median() * 100),
                  'T5_win': r3((hvf['hvt_r5'] > 0).mean() * 100),
                  'T5_ex': np.nan, 'hvt_N': len(hvf), 'overlap_codes': np.nan,
                  'overlap_pct_of_mine': np.nan, 'overlap_pct_of_hvt': np.nan, 'note': ''})
    # 失败重合率：HVT 中 T+5<0 的事件里，其 ts_code 出现在我方 F 组失败样本中的比例
    Fcode = set(ev['ts_code'].values[GMSK['F_分歧+缩量+结构未破+再启动']])
    hvt_fail = hvf[hvf['hvt_r5'] < 0]
    HROWS.append({'section': 'HVT_失败重合', 'group': 'HVT失败样本中其个股曾进入我方F组',
                  'period': '可交易口径', 'N': len(hvt_fail),
                  'T3_med': np.nan, 'T5_med': np.nan, 'T5_win': np.nan, 'T5_ex': np.nan,
                  'hvt_N': np.nan,
                  'overlap_codes': int(sum(c in Fcode for c in hvt_fail['ts_code'])),
                  'overlap_pct_of_mine': np.nan,
                  'overlap_pct_of_hvt': r3(100.0 * sum(c in Fcode for c in hvt_fail['ts_code'])
                                           / max(1, len(hvt_fail))), 'note': ''})
    # 月度中位 T+5 相关性
    hv_m = hvf.assign(mo=(hvf['t0_date'] // 100).astype(str)) \
        .groupby('mo')['hvt_r5'].median()
    gmk_all = GMSK['F_分歧+缩量+结构未破+再启动']
    my_m = pd.Series(L.fwd_ret(m, ANCH['F_分歧+缩量+结构未破+再启动'], 5, COST)[gmk_all],
                     index=(my_date[gmk_all] // 100).astype(str)).groupby(level=0).median()
    j = pd.concat([hv_m.rename('hvt'), my_m.rename('mine')], axis=1).dropna()
    corr = r3(j['hvt'].corr(j['mine'])) if len(j) >= 5 else np.nan
    HROWS.append({'section': 'HVT_收益相关', 'group': '月度中位T+5 相关系数(F组 vs HVT)',
                  'period': '可交易口径', 'N': int(len(j)), 'T3_med': np.nan, 'T5_med': np.nan,
                  'T5_win': np.nan, 'T5_ex': corr, 'hvt_N': np.nan, 'overlap_codes': np.nan,
                  'overlap_pct_of_mine': np.nan, 'overlap_pct_of_hvt': np.nan,
                  'note': '共同月份数 %d' % len(j)})
else:
    print('  未找到 HVT 事件明细，跳过')
hvdf = pd.DataFrame(HROWS)
p = os.path.join(OUT, 'tr_ext_hvt.csv')
hvdf.to_csv(p, index=False, encoding='utf-8-sig')
print('  已写', p, hvdf.shape)

# ══════════════════════ 摘要打印 ══════════════════════
print('\n===== 分期摘要（事件链口径，单位 %）=====')
for gname in GMSK:
    s = oos[(oos.section == 'OOS_分期') & (oos.group == gname)]
    line = []
    for _, r in s.iterrows():
        line.append('%s N=%d T5=%.2f(ex%.2f) T3=%.2f W5=%.0f%%'
                    % (r['period'], r['N'], r['T5_med'], r['T5_ex'], r['T3_med'], r['T5_win']))
    print(' ', gname, ' | '.join(line))

print('\n===== Walk-Forward（测试年 T+5 中位 / 超额）=====')
for _, r in oos[oos.section == 'WF_窗口'].iterrows():
    print('  %-32s %-22s N=%-5d T5_med=%7.3f T5_ex=%7.3f T3_med=%7.3f'
          % (r['group'], r['period'], r['N'], r['T5_med'], r['T5_ex'], r['T3_med']))

print('\n===== 失败模式（F 组内）=====')
for _, r in fail[fail.section == 'FAIL_模式'].iterrows():
    print('  %-38s N=%-4d 占比%5.1f%% T5=%7.3f 非该模式T5=%7.3f'
          % (r['group'], r['N'], r['share_of_F_pct'], r['T5_med'], r['非该模式_T5_med']))

print('\n===== HVT 正交 =====')
print(hvdf[['section', 'group', 'N', 'T3_med', 'T5_med', 'T5_win', 'overlap_codes',
            'overlap_pct_of_mine', 'overlap_pct_of_hvt', 'T5_ex']].to_string(index=False))

meta = {'date': DATE, 'kd': KD, 'hvt_lo': HVT_LO, 'hvt_hi': HVT_HI,
        'periods': {k: int(v.sum()) for k, v in PERIOD_MSK.items()}}
with open(os.path.join(OUT, 'tr_ext_meta.json'), 'w', encoding='utf-8') as f:
    json.dump(meta, f, ensure_ascii=False, indent=1, default=str)
print('\n完成扩展层')
