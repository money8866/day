# -*- coding: utf-8 -*-
"""
HVE-Research V1 — Step 11
复刻 solo/hvt_bull/config.yaml 的入场条件，在无未来函数口径下检验其增量价值

复刻对象（config.yaml）
----------------------
universe      : 非ST / 非北交所 / 上市满120日 / 20日均成交额>=3000万 / 市值>=30亿
hvt           : 250日量(换手)分位 + ratio_20(当日/20日均) + amount_ratio_20
                A: pct>=99 & ratio>=3.0 | B: pct>=98 & ratio>=2.0 | C: pct>=95 & ratio>=1.8
price_strength: pct_chg>=3% , close_pos>=0.70 , body>=0.30
trend         : close>ma20 , ma20斜率>0 , close>ma60
gain_structure: r20∈[10,40]% , r60∈[10,80]% , dist_high_120∈[5,30]%
chip_lock     : T+1..T+5 均量<=T0量*0.60 , 最低价>=T0收盘*0.93 , T+5收盘>=T0收盘*0.95
breakout      : close>T0_High*1.01 & 当日量>=20日均量*1.3 & close_pos>=0.75

口径说明（与 config 的差异，全部为数据可得性所迫）
-------------------------------------------------
* turnover_rate / total_mv 在本项目缓存中仅 2023-01 起可用。250 日内流通股本近似不变
  ⇒ 成交量分位 ≈ 换手率分位，故主口径用 vol_pct250 代理；2023+ 子样本另做真实换手率复检。
* config 的 percentile_120(120日分位) 面板无对应列，改用 vol_pct250（250日分位）。
* config ma60_slope_days=20，面板仅有 ma60_slope10，用 10 日近似。
* config 未显式给事件冷却，state_machine 中两类 HVT 各自独立冷却 10 日 ⇒ gap=10, mode=first。
* config breakout_window=120 ⇒ W=120。
* 入场价一律为信号确认日收盘价；收益 = close[T+d+h]/close[T+d]-1。
"""
import os
import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view
import hve_lib as L

DATA = L.DATA
OUT = r'D:\mystock\hve_research\out'
W = 120
OUT_H = [3, 5, 10, 20]
COSTS = [0, 10, 20, 30, 50]
IS_END = 20230630
RNG = np.random.default_rng(L.SEED)
NBOOT = 300
AMT_MIN = 30000.0      # 千元 = 3000 万元
MV_MIN = 300000.0      # 万元 = 30 亿元
MIN_LISTED = 120


# ---------------- 滚动（严格按个股切块，不跨股污染）----------------
def roll_stat(vals, n, stat='mean', shift=1, minp=None):
    m = len(vals)
    minp = n if minp is None else minp
    out = np.full(m, np.nan)
    if m <= n:
        return out
    pad = np.full(n, np.nan)
    xp = np.concatenate([pad, vals])
    win = sliding_window_view(xp, n)
    w = win[:m] if shift else win[1:m + 1]
    cnt = np.sum(~np.isnan(w), axis=1)
    ok = cnt >= minp
    if stat == 'mean':
        s = np.nansum(w, axis=1)
        out[ok] = s[ok] / cnt[ok]
    elif stat == 'max':
        out[ok] = np.nanmax(w, axis=1)[ok]
    elif stat == 'min':
        out[ok] = np.nanmin(w, axis=1)[ok]
    return out


def add_roll(p, col, n, stat, shift, minp, newname):
    v = p[col].values.astype(np.float64)
    out = np.full(len(v), np.nan)
    codes = p['_c'].values
    brk = np.flatnonzero(np.diff(codes)) + 1
    starts = np.concatenate([[0], brk])
    ends = np.concatenate([brk, [len(v)]])
    for s, e in zip(starts, ends):
        out[s:e] = roll_stat(v[s:e], n, stat, shift, minp)
    p[newname] = out


# ---------------- 聚类 ----------------
def cluster_events(rows, tdv, codes, score=None, gap=10, mode='first'):
    keep = np.ones(len(rows), bool)
    if len(rows) == 0:
        return rows
    brk = np.flatnonzero(np.diff(codes)) + 1
    starts = np.concatenate([[0], brk])
    ends = np.concatenate([brk, [len(rows)]])
    for s, e in zip(starts, ends):
        t = tdv[s:e]
        if e - s == 1:
            keep[s] = True
            continue
        newc = np.concatenate([[True], np.diff(t) > gap])
        cid = np.cumsum(newc) - 1
        if mode == 'first':
            sel = np.zeros(e - s, bool)
            sel[np.flatnonzero(np.concatenate([[True], cid[1:] != cid[:-1]]))] = True
        else:
            sc = np.where(np.isnan(score[s:e]), -np.inf, score[s:e])
            sel = np.zeros(e - s, bool)
            for c in np.unique(cid):
                idx = np.flatnonzero(cid == c)
                sel[idx[np.nanargmax(sc[idx])]] = True
        keep[s:e] = sel
    return rows[keep]


# ---------------- 事件集构造 ----------------
class EvSet:
    def __init__(self, p, rows, name, oc, mktf):
        self.p = p
        self.rows = np.asarray(rows, dtype=np.int64)
        self.name = name
        self.N = len(p)
        cats = pd.Categorical(p['ts_code'].values)
        self.code_i = cats.codes
        self.ev_code = cats.codes[self.rows]
        self.off = np.arange(1, W + 1)
        self.idx = np.clip(self.rows[:, None] + self.off[None, :], 0, self.N - 1)
        self.same = (self.code_i[self.idx] == self.ev_code[:, None])
        self._cache = {}
        self.oc = oc
        self.mktf = mktf
        self.tdi = p['td_idx'].values

    def mat(self, arr, name, dtype=np.float64):
        if name in self._cache:
            return self._cache[name]
        v = np.where(self.same, np.asarray(arr, dtype=dtype)[self.idx], np.nan)
        self._cache[name] = v
        return v

    def outcomes(self, d, prefix, horizons=OUT_H):
        dd = np.asarray(d, dtype=np.float64)
        ok = np.isfinite(dd) & (dd >= 0)
        tgt = np.where(ok, np.clip(self.rows + np.clip(dd, 0, W).astype(np.int64), 0, self.N - 1),
                       self.rows)
        same = ok & (self.code_i[tgt] == self.ev_code)
        tdi = self.tdi[tgt]
        out = {}
        for h in horizons:
            r = np.where(same, self.oc[f'ret_{h}'].values[tgt], np.nan)
            out[f'{prefix}ret_{h}'] = r
            out[f'{prefix}mfe_{h}'] = np.where(same, self.oc[f'mfe_{h}'].values[tgt], np.nan)
            out[f'{prefix}mae_{h}'] = np.where(same, self.oc[f'mae_{h}'].values[tgt], np.nan)
            mf = self.mktf[h][np.clip(tdi, 0, len(self.mktf[h]) - 1)]
            out[f'{prefix}exc_{h}'] = r - mf
        return out


def summarize(df, mask, tag, label, seg, rows_list, horizons=OUT_H, costs_bp=0.0):
    sub = df[mask]
    if len(sub) < 40:
        return
    for h in horizons:
        st = L.stat_full(sub, f'{tag}ret_{h}', f'{tag}mfe_{h}', f'{tag}mae_{h}',
                         costs_bp, f'{tag}exc_{h}', label)
        if st.get('n', 0) < 40:
            continue
        rows_list.append(dict(model=label, segment=seg, horizon=h, n=st['n'],
                              win=st['win'], mean=st['mean'], median=st['median'],
                              pf=st['pf'], t=st['t'], exc=st.get('exc_mean', np.nan),
                              mfe=st['mfe'], mae=st['mae']))


def main():
    print('[1/6] 载入面板 ...', flush=True)
    cols = ['ts_code', 'trade_date', 'td_idx', 'open', 'high', 'low', 'close',
            'ret', 'pct_chg', 'vol', 'amount', 'turnover_rate', 'total_mv',
            'ma20', 'ma60', 'vr20', 'amt_r20', 'vol_pct250', 'turn_pct250',
            'vol20', 'ret20', 'ret60',
            'ma20_slope10', 'ma60_slope10', 'board', 'up_limit', 'yizi',
            'is_st', 'is_delist', 'obs_days']
    p = pd.read_parquet(os.path.join(DATA, 'panel.parquet'), columns=cols)
    p['_c'] = pd.factorize(p['ts_code'], sort=False)[0]
    add_roll(p, 'amount', 20, 'mean', 1, 20, 'amt20p')
    add_roll(p, 'amount', 20, 'mean', 1, 20, '_a')
    p['amt_r20p'] = p['amount'] / p['amt20p']
    add_roll(p, 'high', 120, 'max', 1, 60, 'hh120')
    add_roll(p, 'vol', 20, 'mean', 0, 20, 'vma20i')
    p['vr20i'] = p['vol'] / p['vma20i']
    add_roll(p, 'turnover_rate', 20, 'mean', 1, 20, 'turn20p')
    p['turn_r20p'] = p['turnover_rate'] / p['turn20p']
    hl = (p['high'].values - p['low'].values)
    hl = np.where(hl <= 0, np.nan, hl)
    p['cp'] = (p['close'].values - p['low'].values) / hl
    p['body'] = (p['close'].values - p['open'].values) / hl
    p['dist_hh120'] = (p['hh120'].values / p['close'].values - 1.0) * 100.0
    p.drop(columns=['_a'], inplace=True)
    print('      面板就绪', p.shape, flush=True)

    MKT = pd.read_parquet(os.path.join(DATA, 'market.parquet'), columns=['td_idx', 'ew_ret', 'med_ret'])
    MKT = L.add_mkt_fwd(MKT, OUT_H)
    mktf = {h: MKT[f'mkt_fwd_{h}'].values.astype(np.float64) for h in OUT_H}
    oc = pd.read_parquet(os.path.join(DATA, 'outcomes.parquet'),
                         columns=[f'{k}_{h}' for h in OUT_H for k in ('ret', 'mfe', 'mae')])

    # ---------------- universe ----------------
    print('[2/6] 构造 universe ...', flush=True)
    mvv = p['total_mv'].values
    uni = ((p['board'].values != 'BJ') &
           (np.nan_to_num(p['is_st'].values) == 0) &
           (np.nan_to_num(p['is_delist'].values) == 0) &
           (np.nan_to_num(p['obs_days'].values) >= MIN_LISTED) &
           (np.nan_to_num(p['amt20p'].values) >= AMT_MIN) &
           np.isfinite(p['vr20'].values) & np.isfinite(p['amt_r20p'].values) &
           np.isfinite(p['hh120'].values) & np.isfinite(p['ma20'].values) &
           np.where(np.isfinite(mvv), mvv >= MV_MIN, True))
    print(f'      universe rows = {int(uni.sum()):,} / {len(p):,}', flush=True)

    # ---------------- HVT 事件 ----------------
    print('[3/6] 生成 HVT 事件 ...', flush=True)
    pct = p['vol_pct250'].values * 100.0
    vr = p['vr20'].values
    ar = p['amt_r20p'].values
    hvtA = uni & (pct >= 99.0) & (vr >= 3.0) & (ar >= 2.0)
    hvtB = uni & (pct >= 98.0) & (vr >= 2.0) & (ar >= 2.0)
    hvtC = uni & (pct >= 95.0) & (vr >= 1.8) & (ar >= 2.0)
    hvtAB = hvtA | hvtB
    tdv = p['td_idx'].values
    cdv = p['_c'].values
    volv = p['vol'].values

    def cl(mask, gap=10, mode='first'):
        rows = np.flatnonzero(mask)
        return cluster_events(rows, tdv[rows], cdv[rows], volv, gap=gap, mode=mode)

    ev_rows = cl(hvtAB, gap=10, mode='first')
    ref_rows = cl(uni & (vr >= 2.0), gap=5, mode='first')
    print(f'      HVT-A|B 原始 {int(hvtAB.sum()):,} → 聚类(gap10,first) {len(ev_rows):,}')
    print(f'      REF vr20>=2 原始 {int((uni & (vr>=2.0)).sum()):,} → 聚类(gap5,first) {len(ref_rows):,}', flush=True)

    keep_cols = ['ts_code', 'trade_date', 'td_idx', 'row', 'close', 'high', 'low', 'open',
                 'vol', 'amount', 'pct_chg', 'cp', 'body', 'ma20', 'ma60', 'ma20_slope10',
                 'ma60_slope10', 'ret20', 'ret60', 'dist_hh120', 'vr20', 'amt_r20p',
                 'vol_pct250', 'turn_pct250', 'turn_r20p', 'total_mv', 'vol20', 'board',
                 'up_limit', 'yizi', 'obs_days']
    p['row'] = np.arange(len(p), dtype=np.int64)

    ev = p.iloc[ev_rows][keep_cols].reset_index(drop=True)
    E = EvSet(p, ev_rows, 'hvt', oc, mktf)
    rf = p.iloc[ref_rows][keep_cols].reset_index(drop=True)
    R = EvSet(p, ref_rows, 'ref', oc, mktf)

    print('[4/6] 构造 T+1..T+120 偏移矩阵 ...', flush=True)
    close_m = E.mat(p['close'].values, 'close')
    high_m = E.mat(p['high'].values, 'high')
    low_m = E.mat(p['low'].values, 'low')
    vol_m = E.mat(p['vol'].values, 'vol')
    vr20i_m = E.mat(p['vr20i'].values, 'vr20i')
    cp_m = E.mat(p['cp'].values, 'cp')
    del E._cache['vr20i'], E._cache['cp']

    n = len(ev)
    oos = ev['trade_date'].values > IS_END
    nref = len(rf)
    oos_r = rf['trade_date'].values > IS_END

    # ---- 条件（全部只看事件日及之前）----
    c0 = ev['close'].values
    h0 = ev['high'].values
    ps = ((ev['pct_chg'].values >= 3.0) & (ev['cp'].values >= 0.70) & (ev['body'].values >= 0.30))
    tr = ((c0 > ev['ma20'].values) & (ev['ma20_slope10'].values > 0) & (c0 > ev['ma60'].values))
    r20 = ev['ret20'].values * 100.0
    r60 = ev['ret60'].values * 100.0
    dh = ev['dist_hh120'].values
    gs = ((r20 >= 10.0) & (r20 <= 40.0) & (r60 >= 10.0) & (r60 <= 80.0) &
          (dh >= 5.0) & (dh <= 30.0))

    v5 = np.nanmean(vol_m[:, :5], axis=1)
    l5 = np.nanmin(low_m[:, :5], axis=1)
    c5 = close_m[:, 4]
    chip = ((v5 <= ev['vol'].values * 0.60) & (l5 >= c0 * 0.93) & (c5 >= c0 * 0.95))

    cond = (close_m > (h0[:, None] * 1.01)) & (vr20i_m >= 1.3) & (cp_m >= 0.75)
    anyb = cond.any(axis=1)
    d_br = np.where(anyb, np.argmax(cond, axis=1) + 1.0, np.nan)
    ev['d_br'] = d_br
    ev['chip'] = chip
    vrp = vol_m / np.where(ev['vol'].values[:, None] > 0, ev['vol'].values[:, None], np.nan)
    mv_br = np.full(n, np.nan)
    for i in np.flatnonzero(np.isfinite(d_br)):
        k = int(d_br[i]) - 1
        if k >= 1:
            mv_br[i] = np.nanmin(vrp[i, :k])
    ev['minvr_pre_br'] = mv_br
    print(f'      突破命中率 {anyb.mean():.3f}  中位 d_br={np.nanmedian(d_br):.0f}', flush=True)

    # ---- 结果矩阵 ----
    print('[5/6] 计算各入场点结果 ...', flush=True)
    for tag, d in (('hve_', np.zeros(n)), ('chip_', np.full(n, 5.0)), ('br_', d_br)):
        for k, v in E.outcomes(d, tag).items():
            ev[k] = v
    for k, v in R.outcomes(np.zeros(nref), 'hve_').items():
        rf[k] = v

    # ---------------- 分层 ----------------
    rows_list = []
    segs = {'IS': (~oos), 'OOS': oos, 'ALL': np.ones(n, bool)}
    LAYERS = [
        ('L0_HVT_only', np.ones(n, bool), 'hve_'),
        ('L1_+PriceStrength', ps, 'hve_'),
        ('L2_+Trend', ps & tr, 'hve_'),
        ('L3_+GainStruct', ps & tr & gs, 'hve_'),
        ('L4_+ChipLock(entryT+5)', ps & tr & gs & chip, 'chip_'),
        ('L5_+Breakout(entry=brk)', ps & tr & gs & np.isfinite(d_br), 'br_'),
        ('L5b_+Breakout+Contr<=0.6', ps & tr & gs & np.isfinite(d_br) & (mv_br <= 0.6), 'br_'),
        ('L5c_Brk_noGain', ps & tr & np.isfinite(d_br), 'br_'),
        ('L5d_Brk_noPrice', tr & gs & np.isfinite(d_br), 'br_'),
        ('L5e_Brk_noTrend', ps & gs & np.isfinite(d_br), 'br_'),
        ('L5f_Brk_only', np.isfinite(d_br), 'br_'),
    ]
    for sname, sm in segs.items():
        for label, mask, tag in LAYERS:
            summarize(ev, sm & mask, tag, label, sname, rows_list)
    for sname, sm in (('IS', ~oos_r), ('OOS', oos_r), ('ALL', np.ones(nref, bool))):
        summarize(rf, sm, 'hve_', 'REF_HVE_vr20>=2(gap5,first)', sname, rows_list)
    rep = pd.DataFrame(rows_list)
    rep.to_csv(os.path.join(OUT, '12_hvt_config_replication.csv'), index=False, encoding='utf-8-sig')
    print('\n=== config 复刻分层（OOS, T+10, 0bp）===')
    print(rep[(rep.segment == 'OOS') & (rep.horizon == 10)][
        ['model', 'n', 'win', 'mean', 'median', 'pf', 'exc', 'mae']].round(4).to_string(index=False))
    print('\n=== IS 对照（T+10）===')
    print(rep[(rep.segment == 'IS') & (rep.horizon == 10)][
        ['model', 'n', 'win', 'mean', 'pf', 'exc']].round(4).to_string(index=False))

    # ---------------- 单条件消融 ----------------
    abl = []
    base = ev.loc[oos, 'hve_ret_10'].values
    base_st = L.stat(base, 0.0)
    abl.append(dict(cond='(base) HVT only', n=base_st['n'], mean=base_st['mean'],
                    pf=base_st['pf'], win=base_st['win'], exc=np.nan,
                    d_mean=0.0, d_pf=0.0, d_win=0.0))
    CONDS = {
        'price_strength(全)': (ps, 'hve_'),
        'trend(全)': (tr, 'hve_'),
        'gain_structure(全)': (gs, 'hve_'),
        'gain_r20∈[10,40]': ((r20 >= 10) & (r20 <= 40), 'hve_'),
        'gain_r60∈[10,80]': ((r60 >= 10) & (r60 <= 80), 'hve_'),
        'gain_dist120∈[5,30]': ((dh >= 5) & (dh <= 30), 'hve_'),
        'chip_lock(T+1..T+5)→T+5入场': (chip, 'chip_'),
        'breakout(存在)→突破日入场': (np.isfinite(d_br), 'br_'),
        'breakout+缩量<=0.6': (np.isfinite(d_br) & (mv_br <= 0.6), 'br_'),
        'pct_chg>=3': (ev['pct_chg'].values >= 3.0, 'hve_'),
        'close_pos>=0.70': (ev['cp'].values >= 0.70, 'hve_'),
        'body>=0.30': (ev['body'].values >= 0.30, 'hve_'),
        'close>ma20': (c0 > ev['ma20'].values, 'hve_'),
        'ma20_slope10>0': (ev['ma20_slope10'].values > 0, 'hve_'),
        'close>ma60': (c0 > ev['ma60'].values, 'hve_'),
        'T0涨停': (np.nan_to_num(ev['up_limit'].values) > 0, 'hve_'),
    }
    for name, (mk, tag) in CONDS.items():
        m = oos & mk
        if m.sum() < 40:
            continue
        st = L.stat_full(ev[m], f'{tag}ret_10', f'{tag}mfe_10', f'{tag}mae_10', 0.0,
                         f'{tag}exc_10', name)
        if st.get('n', 0) < 40:
            continue
        abl.append(dict(cond=name, n=st['n'], mean=st['mean'], pf=st['pf'], win=st['win'],
                        exc=st.get('exc_mean', np.nan),
                        d_mean=st['mean'] - base_st['mean'],
                        d_pf=(st['pf'] - base_st['pf']) if np.isfinite(st['pf']) else np.nan,
                        d_win=st['win'] - base_st['win']))
    abl = pd.DataFrame(abl)
    abl.to_csv(os.path.join(OUT, '12b_hvt_config_ablation.csv'), index=False, encoding='utf-8-sig')
    print('\n=== 单条件消融（OOS, 相对 HVT only, T+10, 0bp）===')
    print(abl.round(4).to_string(index=False))

    # ---------------- 分级 ----------------
    gr = []
    evp = pct[ev_rows]
    for sname, sm in segs.items():
        for lvl, mk in (('HVT-A(pct99,rat3.0)', evp >= 99.0),
                        ('HVT-B(pct98,rat2.0)', (evp >= 98.0) & (evp < 99.0)),
                        ('HVT-C(pct95,rat1.8)', (evp >= 95.0) & (evp < 98.0))):
            summarize(ev, sm & mk, 'hve_', lvl, sname, gr)
    gr = pd.DataFrame(gr)
    gr.to_csv(os.path.join(OUT, '12c_hvt_grade.csv'), index=False, encoding='utf-8-sig')
    print('\n=== HVT 分级（OOS, T+10）===')
    print(gr[(gr.segment == 'OOS') & (gr.horizon == 10)][
        ['model', 'n', 'win', 'mean', 'pf', 'exc']].round(4).to_string(index=False))

    # ---------------- 同日配对 Null ----------------
    print('[6/6] 同日配对 Null ...', flush=True)
    d = p.loc[uni, ['ts_code', 'td_idx', 'trade_date', 'vol20', 'row']].reset_index(drop=True)
    for h in OUT_H:
        d[f'ret_{h}'] = oc[f'ret_{h}'].values[d['row'].values]
    d = d.sort_values('td_idx').reset_index(drop=True)
    td2 = d['td_idx'].values
    uq, st_ = np.unique(td2, return_index=True)
    en_ = np.concatenate([st_[1:], [len(td2)]])
    vol20 = np.nan_to_num(d['vol20'].values)
    qs = np.nanpercentile(vol20, np.arange(10, 100, 10))
    vq = np.digitize(vol20, qs)
    key2 = td2 * 20 + vq
    o2 = np.argsort(key2, kind='stable')
    k2s = key2[o2]
    u2, s2 = np.unique(k2s, return_index=True)
    e2 = np.concatenate([s2[1:], [len(o2)]])
    retc = {h: d[f'ret_{h}'].values.astype(np.float64) for h in OUT_H}

    def null_test(df, mask, tag, label, oosm):
        out = []
        for seg, sm in (('IS', ~oosm), ('OOS', oosm)):
            mm = mask & sm
            if mm.sum() < 50:
                continue
            sub = df[mm]
            tds = sub['td_idx'].values
            sl = np.clip(np.searchsorted(uq, tds), 0, len(st_) - 1)
            cnt = np.maximum(en_[sl] - st_[sl], 1)
            vqe = np.digitize(np.nan_to_num(sub['vol20'].values), qs)
            kk = tds * 20 + vqe
            sl2 = np.clip(np.searchsorted(u2, kk), 0, len(s2) - 1)
            cnt2 = np.maximum(e2[sl2] - s2[sl2], 1)
            for h in OUT_H:
                real = sub[f'{tag}ret_{h}'].values
                real_m = np.nanmean(real)
                if not np.isfinite(real_m):
                    continue
                b1 = np.empty(NBOOT); b2 = np.empty(NBOOT)
                for b in range(NBOOT):
                    pk = st_[sl] + (RNG.random(len(tds)) * cnt).astype(np.int64)
                    b1[b] = np.nanmean(retc[h][np.clip(pk, 0, len(d) - 1)])
                    pk2 = s2[sl2] + (RNG.random(len(tds)) * cnt2).astype(np.int64)
                    b2[b] = np.nanmean(retc[h][o2[np.clip(pk2, 0, len(o2) - 1)]])
                c1, c2 = float(b1.mean()), float(b2.mean())
                out.append(dict(strategy=label, segment=seg, horizon=h,
                                n=int(np.sum(~np.isnan(real))), mean=real_m,
                                win=float(np.nanmean(real > 0)),
                                ctrl_sameday=c1, alpha_sameday=real_m - c1,
                                p_sameday=float((b1 >= real_m).mean()),
                                ctrl_voladj=c2, alpha_voladj=real_m - c2,
                                p_voladj=float((b2 >= real_m).mean())))
                for c in COSTS:
                    stx = L.stat(real, costs_bp=c)
                    out.append(dict(strategy=label, segment=seg, horizon=h, cost_bp=c,
                                    n=stx['n'], mean=stx['mean'], win=stx['win'],
                                    alpha_voladj=stx['mean'] - c2))
        return out

    nr = []
    NR = [
        ('hve_', np.ones(n, bool), 'R0_HVT_only'),
        ('hve_', ps & tr, 'R2_HVT+PS+Trend'),
        ('hve_', ps & tr & gs, 'R3_+GainStruct'),
        ('chip_', ps & tr & gs & chip, 'R4_+ChipLock'),
        ('br_', ps & tr & gs & np.isfinite(d_br), 'R5_+Breakout'),
        ('br_', ps & tr & gs & np.isfinite(d_br) & (mv_br <= 0.6), 'R5b_Brk+Contr<=0.6'),
    ]
    for tag, mask, label in NR:
        nr += null_test(ev, mask, tag, label, oos)
    nr += null_test(rf, np.ones(nref, bool), 'hve_', 'REF_HVE_vr20>=2', oos_r)
    nr = pd.DataFrame(nr)
    nr.to_csv(os.path.join(OUT, '12d_hvt_config_null.csv'), index=False, encoding='utf-8-sig')
    print('\n=== 同日配对 Null（OOS, T+10, 0bp）===')
    print(nr[(nr.segment == 'OOS') & (nr.horizon == 10) & nr['cost_bp'].isna()][
        ['strategy', 'n', 'mean', 'win', 'ctrl_voladj', 'alpha_voladj', 'p_voladj']].round(4).to_string(index=False))
    print('\n=== 成本扫描（OOS, T+10, 波动率配对 α, bp）===')
    cs = nr[(nr.segment == 'OOS') & (nr.horizon == 10) & nr['cost_bp'].notna()]
    print(cs.pivot_table(index='strategy', columns='cost_bp', values='alpha_voladj').round(4).to_string())

    # ---------------- 集中度 ----------------
    conc = []
    for tag, mask, label in NR + [('hve_', np.ones(nref, bool), 'REF_HVE_vr20>=2')]:
        dfx, oo = (rf, oos_r) if label.startswith('REF') else (ev, oos)
        for h in OUT_H:
            r = dfx.loc[oo & mask, f'{tag}ret_{h}'].values
            cc = L.concentration(r, label)
            cc['horizon'] = h
            conc.append(cc)
    conc = pd.DataFrame(conc)
    conc.to_csv(os.path.join(OUT, '12e_hvt_config_concentration.csv'), index=False, encoding='utf-8-sig')
    print('\n=== 尾部集中度（OOS, T+10）===')
    print(conc[conc.horizon == 10][
        ['label', 'n', 'mean_all', 'mean_ex_top5', 'pf_all', 'pf_ex_top5', 'top5_contrib']].round(4).to_string(index=False))

    # ---------------- 可成交性 ----------------
    tr_ = []
    for seg, sm in (('ALL', np.ones(n, bool)), ('OOS', oos)):
        mm = sm & ps & tr & gs & np.isfinite(d_br)
        if mm.sum() < 50:
            continue
        arr = np.clip(d_br[mm].astype(np.int64) - 1, 0, W - 1)
        idx = np.clip(ev.loc[mm, 'row'].values + arr, 0, len(p) - 1)
        isup = np.nan_to_num(p['up_limit'].values[idx]) > 0
        isyz = np.nan_to_num(p['yizi'].values[idx]) > 0
        r10 = ev.loc[mm, 'br_ret_10'].values
        tr_.append(dict(segment=seg, n=len(r10), br_day_limitup=float(isup.mean()),
                        br_day_yizi=float(isyz.mean()),
                        mean_all=float(np.nanmean(r10)),
                        mean_nonlimit=float(np.nanmean(r10[~isup])),
                        mean_limit=float(np.nanmean(r10[isup])) if isup.any() else np.nan))
    tr_ = pd.DataFrame(tr_)
    tr_.to_csv(os.path.join(OUT, '12f_hvt_config_tradability.csv'), index=False, encoding='utf-8-sig')
    print('\n=== 突破日可成交性 ===')
    print(tr_.round(4).to_string(index=False))

    print('\nDONE', flush=True)


if __name__ == '__main__':
    main()
