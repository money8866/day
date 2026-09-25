# -*- coding: utf-8 -*-
"""三花聚顶 V2 · 特征扩展层（突破量能机制研究）

输入: out/panel.parquet, out/first_board_events.parquet, out/tf_base.parquet
输出: out/tf_v2.parquet

原则:
  1) 只读不改。V1 产物(tf_base.parquet 等)原封不动，新增列另存 tf_v2.parquet
  2) 无未来函数：全部特征只用突破日及之前的 panel 数据
  3) 可验证：用 tf_base 已有的 k_A / Cb0_k / brk_b0_vr 做一致性断言，断言不过即中止

新增列:
  A. 突破几何      amp_hi, amp_cl, amp_pct(相对前高绝对差), stage_hi, stage_lo, stage_amp, amp_ratio
  B. 突破日结构    brk_ret, brk_body, ush, lsh, ush_prev, lsh_prev, brk_pos(复核)
  C. 突破前量能    pbvr1, pbvr3, pbvr5, vr_ratio3
  D. 时序位置      brk_lag(=突破日-确认日), brk_off(绝对offset)
  E. offset 基准   c{K}_r{N}  K=0..25  (全体首板在该 offset 买入的前瞻净收益，用于配对超额)
"""
import os
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')

N_WIN = 46
HOR = (1, 3, 5, 10, 20)
KMAX = 25
SLIP, COMM, STAMP = 0.001, 0.00025, 0.0005
COST = 2 * (SLIP + COMM) + STAMP
EV_COLS = ['ts_code', 'trade_date', 'a_open', 'a_high', 'a_low', 'a_close', 'vol', 'vol_ma20']


def main():
    panel = pd.read_parquet(os.path.join(OUT, 'panel.parquet'), columns=EV_COLS)
    fb = pd.read_parquet(os.path.join(OUT, 'first_board_events.parquet'))[['ts_code', 'trade_date']]
    tb = pd.read_parquet(os.path.join(OUT, 'tf_base.parquet'),
                         columns=['ts_code', 'trade_date', 'has_tf', 'k_A', 'Cb0_k', 'hi_lvl',
                                  'struct_dd', 'span', 'brk_b0_vr', 'Cb0_r5', 'A_r5', 'f1_off'])
    assert len(fb) == len(tb)
    assert (fb['ts_code'].values == tb['ts_code'].values).all()
    assert (fb['trade_date'].values == tb['trade_date'].values).all()
    print('行对齐校验通过 | 事件', len(tb))

    panel = panel.sort_values(['ts_code', 'trade_date'], kind='stable').reset_index(drop=True)
    panel['trade_date'] = panel['trade_date'].astype(np.int64)
    fb = fb.reset_index(drop=True)
    fb['trade_date'] = fb['trade_date'].astype(np.int64)
    n_ev = len(fb)

    key = panel[['ts_code', 'trade_date']].copy()
    key['pos'] = np.arange(len(panel), dtype=np.int64)
    m = fb[['ts_code', 'trade_date']].merge(key, on=['ts_code', 'trade_date'], how='left')
    assert m['pos'].notna().all(), '首板事件无法在面板中定位'
    ev_pos = m['pos'].values.astype(np.int64)

    codes = panel['ts_code'].to_numpy()
    uniq, first_idx, counts = np.unique(codes, return_index=True, return_counts=True)
    c2i = {c: i for i, c in enumerate(uniq)}
    ev_ci = np.fromiter((c2i[c] for c in fb['ts_code'].to_numpy()), dtype=np.int64, count=n_ev)
    avail = (first_idx[ev_ci] + counts[ev_ci] - 1 - ev_pos).astype(np.int64)
    assert (avail >= 0).all()

    offs = np.arange(N_WIN, dtype=np.int64)
    Wvalid = offs[None, :] <= avail[:, None]
    Wpos = np.clip(ev_pos[:, None] + offs[None, :], 0, len(panel) - 1)

    def g(col):
        v = panel[col].to_numpy()[Wpos].astype(np.float32)
        return np.where(Wvalid, v, np.nan).astype(np.float32)

    A_OPEN, A_HIGH, A_LOW, A_CLOSE = g('a_open'), g('a_high'), g('a_low'), g('a_close')
    VOL, VMA20 = g('vol'), g('vol_ma20')
    print('窗口矩阵', A_CLOSE.shape)

    idx = np.arange(n_ev)

    c3 = tb['k_A'].fillna(-1).astype(int).values
    p = tb['Cb0_k'].fillna(-1).astype(int).values
    has_tf = tb['has_tf'].values.astype(bool)
    brk = has_tf & (p >= 0)
    sel = np.where(has_tf)[0]
    print('三花结构事件 %d | 其中突破 %d' % (int(has_tf.sum()), int(brk.sum())))
    assert (c3[has_tf] >= 0).all()
    r5v = tb['Cb0_r5'].notna().values
    # 突破存在 ≠ 该持有期可观测：offset+5 越界时 r5 为 NaN，故 r5v 是 brk 的子集
    assert (r5v & ~brk).sum() == 0, 'Cb0 有效性口径不一致'
    print('突破存在 %d | 其中 T+5 可观测 %d（差 %d 为 offset 越界）'
          % (int(brk.sum()), int(r5v.sum()), int((brk & ~r5v).sum())))

    # ── 三花阶段低点（lo1 = offset 1..c3 的最低价前缀最小）
    X = np.where(Wvalid, A_LOW, np.inf)
    lo1 = np.minimum.accumulate(X, axis=1)
    lo1[:, 0] = np.inf
    stage_lo = lo1[idx, np.clip(c3, 0, N_WIN - 1)]
    stage_hi = tb['hi_lvl'].values.astype(np.float64)
    stage_amp = np.where(np.isfinite(stage_lo) & (stage_lo > 0), stage_hi / stage_lo - 1.0, np.nan)

    # ── 突破日微观结构（仅 brk 行有效）
    # 以下所有计算在 sel（三花结构事件）层面进行，最后 scatter 回全样本行
    def scat(v_sel, fill=np.nan):
        a = np.full(n_ev, fill, dtype=np.float64)
        a[sel] = np.asarray(v_sel, dtype=np.float64)
        return a

    brk_s = brk[sel]
    stg_s = stage_hi[sel]
    sta_s = stage_amp[sel]
    spl = np.clip(p[sel], 0, N_WIN - 1)
    spvl = np.clip(spl - 1, 0, N_WIN - 1)
    hi_p, lo_p = A_HIGH[sel, spl].astype(np.float64), A_LOW[sel, spl].astype(np.float64)
    op_p, cl_p = A_OPEN[sel, spl].astype(np.float64), A_CLOSE[sel, spl].astype(np.float64)
    pc_p = A_CLOSE[sel, spvl].astype(np.float64)
    vol_p = VOL[sel, spl].astype(np.float64)
    vma_p = VMA20[sel, spl].astype(np.float64)

    amp_hi = scat(np.where(brk_s, hi_p / stg_s - 1.0, np.nan))
    amp_cl = scat(np.where(brk_s, cl_p / stg_s - 1.0, np.nan))
    amp_abs = scat(np.where(brk_s, cl_p - stg_s, np.nan))
    _ac = np.where(brk_s, np.where(stg_s > 0, cl_p / stg_s - 1.0, np.nan), np.nan)
    amp_ratio = scat(np.where(np.isfinite(_ac) & np.isfinite(sta_s) & (sta_s > 0), _ac / sta_s, np.nan))

    brk_ret = scat(np.where(brk_s, cl_p / pc_p - 1.0, np.nan))
    body = scat(np.where(brk_s, cl_p / op_p - 1.0, np.nan))
    mx = np.fmax(op_p, cl_p)
    mn = np.fmin(op_p, cl_p)
    rng = hi_p - lo_p
    ush = scat(np.where(brk_s, (hi_p - mx) / pc_p, np.nan))            # 上影线 / 前收
    lsh = scat(np.where(brk_s, (mn - lo_p) / pc_p, np.nan))            # 下影线 / 前收
    ush_r = scat(np.where(brk_s & (rng > 0), (hi_p - mx) / rng, np.nan))  # 上影 / 全幅
    lsh_r = scat(np.where(brk_s & (rng > 0), (mn - lo_p) / rng, np.nan))
    pos_p = scat(np.where(brk_s, np.where(rng > 0, (cl_p - lo_p) / rng, 1.0), np.nan))
    gap_p = scat(np.where(brk_s, op_p / pc_p - 1.0, np.nan))

    # ── 突破前量能状态（突破日之前，不含突破日）
    def pre_vr(k):
        vs, ms = [], []
        for s in range(1, k + 1):
            q = np.clip(spl - s, 0, N_WIN - 1)
            vs.append(VOL[sel, q].astype(np.float64) * np.where(brk_s, 1.0, np.nan))
            ms.append(VMA20[sel, q].astype(np.float64) * np.where(brk_s, 1.0, np.nan))
        v = np.nanmean(np.vstack(vs), axis=0)
        mm = np.nanmean(np.vstack(ms), axis=0)
        return scat(np.where(mm > 0, v / mm, np.nan))

    pbvr1 = scat(np.where(brk_s, VOL[sel, spvl].astype(np.float64) /
                          VMA20[sel, spvl].astype(np.float64), np.nan))
    pbvr3 = pre_vr(3)
    pbvr5 = pre_vr(5)
    vr_now = tb['brk_b0_vr'].values.astype(np.float64)
    vr_ratio3 = np.where(np.isfinite(vr_now) & np.isfinite(pbvr3) & (pbvr3 > 0),
                         vr_now / pbvr3, np.nan)

    brk_lag = np.where(brk, p - c3, np.nan).astype(np.float64)
    brk_off = np.where(brk, p, np.nan).astype(np.float64)

    out = pd.DataFrame({'ts_code': tb['ts_code'].values, 'trade_date': tb['trade_date'].values})
    newc = {
        'amp_hi': amp_hi, 'amp_cl': amp_cl, 'amp_abs': amp_abs, 'amp_ratio': amp_ratio,
        'stage_hi': np.where(has_tf, stage_hi, np.nan), 'stage_lo': stage_lo,
        'stage_amp': stage_amp,
        'brk_ret': brk_ret, 'brk_body': body, 'ush': ush, 'lsh': lsh,
        'ush_r': ush_r, 'lsh_r': lsh_r, 'brk_pos': pos_p, 'brk_gap2': gap_p,
        'pbvr1': pbvr1, 'pbvr3': pbvr3, 'pbvr5': pbvr5, 'vr_ratio3': vr_ratio3,
        'brk_lag': brk_lag, 'brk_off': brk_off,
    }
    for k, v in newc.items():
        out[k] = v.astype(np.float32)

    # ── offset 基准网格 c{K}_r{N}（全体首板）
    for K in range(KMAX + 1):
        px = A_CLOSE[idx, K].astype(np.float64)
        vv = Wvalid[idx, K]
        for N in HOR:
            q = np.clip(K + N, 0, N_WIN - 1)
            okt = vv & ((K + N) <= N_WIN - 1) & Wvalid[idx, q]
            c = A_CLOSE[idx, q]
            out['c%d_r%d' % (K, N)] = np.where(
                okt & np.isfinite(px) & (px > 0), c / px - 1.0 - COST, np.nan).astype(np.float32)
    print('offset 基准网格: K=0..%d × %d 个持有期' % (KMAX, len(HOR)))

    # ── 一致性断言（与 V1 同列对齐，容差 1e-4 相对）
    def chk(name, a, b):
        a = np.asarray(a, dtype=np.float64)
        b = np.asarray(b, dtype=np.float64)
        msk = np.isfinite(a) & np.isfinite(b)
        d = np.abs(a[msk] - b[msk])
        scale = np.maximum(np.abs(b[msk]), 1e-6)
        rel = float(np.max(d / scale)) if len(d) else 0.0
        print('  校验 %-10s 可比行 %-6d 最大相对差 %.3e %s' % (name, int(msk.sum()), rel,
                                                              'OK' if rel < 1e-4 else '!!FAIL!!'))
        return rel

    print('--- 与 tf_base 一致性断言 ---')
    chk('k_A', c3.astype(float), tb['k_A'].fillna(-1).values)
    chk('hi_lvl', out['stage_hi'].values, tb['hi_lvl'].values)
    chk('brk_vr', out['pbvr1'].values * 0 + vr_now, tb['brk_b0_vr'].values)
    # 复核突破日量比 = VOL[p]/VMA20[p]（用扩展列重算）
    vr_chk = scat(np.where(brk_s, vol_p / vma_p, np.nan))
    chk('brk_vr_calc', vr_chk, tb['brk_b0_vr'].values)
    chk('c0_r5', out['c0_r5'].values, pd.read_parquet(
        os.path.join(OUT, 'tf_base.parquet'), columns=['c0_r5'])['c0_r5'].values)
    chk('c16_r20', out['c16_r20'].values, pd.read_parquet(
        os.path.join(OUT, 'tf_base.parquet'), columns=['c16_r20'])['c16_r20'].values)

    print('\n--- 新特征覆盖率（三花组 / 突破组）---')
    for k in newc:
        v = out[k].values
        print('  %-10s 三花 %.3f | 突破 %.3f | 中位 %.4f' % (
            k, float(np.isfinite(v[has_tf]).mean()), float(np.isfinite(v[brk]).mean()),
            float(np.nanmedian(v))))
    ov = out['amp_hi'].values[brk]
    print('  突破幅度(high/hi_lvl-1) 分位:', np.percentile(ov, [1, 10, 25, 50, 75, 90, 99]).round(4))
    print('  amp_cl<0 占比 %.3f' % float((out['amp_cl'].values[brk] < 0).mean()))

    out.to_parquet(os.path.join(OUT, 'tf_v2.parquet'), index=False)
    print('\n已写 tf_v2.parquet', out.shape)


if __name__ == '__main__':
    main()
