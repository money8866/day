"""三花聚顶研究 - 结构识别与事件收益引擎（严格无未来函数）

输入：out/panel.parquet、out/first_board_events.parquet
输出：
  out/tf_base.parquet   每个首板事件一行：T0 特征 + 三花结构特征 + Entry A..E 收益
  out/tf_meta.json      口径与参数记录（可复现）

设计（可复现、无未来函数）：
  1) 「花」= 半径 R=1 的摆动低点；d 日成立，d+R 日方可确认
  2) 三元组候选 = 每事件前 6 个花候选的 C(6,3) 全组合，要求间隔≥2、确认日 ≤ T+15
  3) 核心有效性（与任何 H 定义无关，只做结构合法性）：期间无涨停、结构回撤 ≤ 25%
     取「确认日最早、其次 F1 最早」的三元组作为该事件的三花结构 → 结构选择不依赖参数
  4) H1..H6 定义 = 在结构特征上的筛选条件（参数事后施加，不改变结构选择）
  5) Entry：A 确认日收盘 / B 确认日次日开盘 / C 首破缓冲*buffer* 日收盘 /
     D = C 且首破日量比≥VR / E = D 且首破日收盘≥MA20（不满足则该次突破无效，不交易）
"""
import os
import json
import warnings
import numpy as np
import pandas as pd
from itertools import combinations

warnings.filterwarnings('ignore')

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")

N_WIN = 46
W_OBS = 15
R = 1
MIN_GAP = 2
K_CAND = 6
BRK_SEARCH = 10
HOR = (1, 3, 5, 10, 20)
BUFS = [0.0, 0.005, 0.01, 0.02]
BNAME = {0.0: 'b0', 0.005: 'b5', 0.01: 'b10', 0.02: 'b20'}
SLIP, COMM, STAMP = 0.001, 0.00025, 0.0005
COST = 2 * (SLIP + COMM) + STAMP
CORE_DD = 0.25                 # 核心有效性：结构回撤上限
CTRL_K = list(range(0, 17))

DEFP = dict(price_tol=0.01, vol_tol=0.10, vol_decay_thr=0.80,
            prog_min=0.01, ma_conv=0.05, max_dd=0.10, gap6=6)
DEFS = ['H1', 'H2', 'H3', 'H4', 'H5', 'H6']
COLS = ['ts_code', 'trade_date', 'a_open', 'a_high', 'a_low', 'a_close',
        'vol', 'vol_ma20', 'ma5', 'ma10', 'ma20', 'is_limit_up']

EV_COLS = ['ts_code', 'name', 'industry', 'board', 'is_bse', 'trade_date', 'close',
           'pct_chg', 't0_vr20', 't0_amount', 'one_word', 'opened_board',
           't0_vs_ma20', 't0_vs_ma60', 't0_ret20prev', 't0_quality', 'vol', 'vol_ma20', 'ma20']


def main():
    panel = pd.read_parquet(os.path.join(OUT, "panel.parquet"), columns=COLS)
    fb = pd.read_parquet(os.path.join(OUT, "first_board_events.parquet")).reset_index(drop=True)
    panel = panel.sort_values(['ts_code', 'trade_date'], kind='stable').reset_index(drop=True)
    panel['trade_date'] = panel['trade_date'].astype(np.int64)
    fb['trade_date'] = fb['trade_date'].astype(np.int64)
    n_ev = len(fb)
    print("面板", len(panel), "行 | 首板事件", n_ev)

    key = panel[['ts_code', 'trade_date']].copy()
    key['pos'] = np.arange(len(panel), dtype=np.int64)
    m = fb[['ts_code', 'trade_date']].merge(key, on=['ts_code', 'trade_date'], how='left')
    assert m['pos'].notna().all()
    ev_pos = m['pos'].values.astype(np.int64)

    codes = panel['ts_code'].to_numpy()
    uniq, first_idx, counts = np.unique(codes, return_index=True, return_counts=True)
    c2i = {c: i for i, c in enumerate(uniq)}
    ev_ci = np.fromiter((c2i[c] for c in fb['ts_code'].to_numpy()), dtype=np.int64, count=n_ev)
    avail = (first_idx[ev_ci] + counts[ev_ci] - 1 - ev_pos).astype(np.int64)

    offs = np.arange(N_WIN, dtype=np.int64)
    Wvalid = offs[None, :] <= avail[:, None]
    Wpos = np.clip(ev_pos[:, None] + offs[None, :], 0, len(panel) - 1)

    def g(col):
        v = panel[col].to_numpy()[Wpos].astype(np.float32)
        return np.where(Wvalid, v, np.nan).astype(np.float32)

    A_OPEN, A_HIGH, A_LOW, A_CLOSE = g('a_open'), g('a_high'), g('a_low'), g('a_close')
    VOL, VMA20 = g('vol'), g('vol_ma20')
    MA5, MA10, MA20 = g('ma5'), g('ma10'), g('ma20')
    LIM = g('is_limit_up')
    print("窗口矩阵", A_CLOSE.shape)

    idx = np.arange(n_ev)

    def pref_min(M):
        X = np.where(Wvalid, M, np.inf)
        return np.minimum.accumulate(X, axis=1)

    def pref_max(M, start=0):
        X = np.where(Wvalid, M, -np.inf)
        X[:, :start] = -np.inf
        return np.maximum.accumulate(X, axis=1)

    lo1 = pref_min(A_LOW); lo1[:, 0] = np.inf
    hi1 = pref_max(A_HIGH, start=1)
    hi0 = pref_max(A_HIGH, start=0)
    lim_cum = np.cumsum(np.where(Wvalid, LIM, 0.0), axis=1) - np.where(Wvalid[:, :1], LIM[:, :1], 0.0)

    # ── 花候选
    trough = np.zeros((n_ev, N_WIN), dtype=bool)
    ir = np.arange(1, W_OBS)
    lp, lc, ln = A_LOW[:, ir - 1], A_LOW[:, ir], A_LOW[:, ir + 1]
    okf = np.isfinite(lp) & np.isfinite(lc) & np.isfinite(ln)
    trough[:, ir] = okf & (lc <= lp) & (lc <= ln) & ((lc < lp) | (lc < ln)) & (LIM[:, ir] < 0.5)
    tmp = np.sort(np.where(trough, offs[None, :], 10 ** 6), axis=1)[:, :K_CAND]
    T = np.where(tmp >= 10 ** 6, -1, tmp).astype(np.int32)
    print("花候选: ≥1 个", int((T[:, 0] >= 0).sum()), "| ≥3 个", int((T[:, 2] >= 0).sum()))

    # ── 三元组（与定义无关的候选集）
    combos = list(combinations(range(K_CAND), 3))
    NC = len(combos)
    IKEYS = ('f1', 'f2', 'f3', 'c3', 'ok', 'haslim')
    FKEYS = ('P1', 'P2', 'P3', 'V1', 'V2', 'V3', 'minlo', 'maxhi', 'hilvl', 'ma5c', 'ma10c', 'ma20c')
    M = {k: np.zeros((n_ev, NC), dtype=np.float32) for k in FKEYS}
    M.update({k: np.zeros((n_ev, NC), dtype=np.int64) for k in IKEYS})
    for j, (ia, ib, ic) in enumerate(combos):
        f1, f2, f3 = T[:, ia], T[:, ib], T[:, ic]
        ok = (f1 >= 0) & (f2 >= 0) & (f3 >= 0)
        f1 = np.where(ok, f1, 0); f2 = np.where(ok, f2, 0); f3 = np.where(ok, f3, 0)
        ok &= ((f2 - f1) >= MIN_GAP) & ((f3 - f2) >= MIN_GAP) & ((f3 + R) <= W_OBS)
        c3 = np.clip(f3 + R, 0, N_WIN - 1)
        M['f1'][:, j], M['f2'][:, j], M['f3'][:, j], M['c3'][:, j] = f1, f2, f3, c3
        M['ok'][:, j] = ok
        M['P1'][:, j], M['P2'][:, j], M['P3'][:, j] = A_CLOSE[idx, f1], A_CLOSE[idx, f2], A_CLOSE[idx, f3]
        M['V1'][:, j], M['V2'][:, j], M['V3'][:, j] = VOL[idx, f1], VOL[idx, f2], VOL[idx, f3]
        M['minlo'][:, j], M['maxhi'][:, j], M['hilvl'][:, j] = lo1[idx, c3], hi0[idx, c3], hi1[idx, c3]
        M['ma5c'][:, j], M['ma10c'][:, j], M['ma20c'][:, j] = MA5[idx, c3], MA10[idx, c3], MA20[idx, c3]
        M['haslim'][:, j] = lim_cum[idx, c3]

    den = lambda x: np.where(np.asarray(x) > 0, x, np.nan)
    struct_dd = (1.0 - M['minlo'] / den(M['maxhi'])).astype(np.float32)
    ma_conv = np.fmax(np.abs(M['ma5c'] - M['ma10c']) / den(M['ma10c']),
                      np.abs(M['ma10c'] - M['ma20c']) / den(M['ma20c'])).astype(np.float32)
    core = ((M['ok'] > 0) & (M['haslim'] == 0) & np.isfinite(struct_dd) & (struct_dd <= CORE_DD)
            & np.isfinite(M['P1']) & np.isfinite(M['P3']) & np.isfinite(M['V1']) & np.isfinite(M['V3']))
    has = core.any(axis=1)
    sel = np.where(has)[0]
    print("核心有效三花结构:", int(has.sum()), "| 占比", round(float(has.mean()), 4))

    RULES = {
        'earliest': M['c3'] * 1000 + M['f1'],
        'maxspan': -(M['f3'] - M['f1']) * 1000 + M['c3'],
        'min_dd': np.nan_to_num(struct_dd, nan=1e6, posinf=1e6) * 1000 + M['c3'],
        'latest': -M['c3'] * 1000 - M['f1'],
    }
    pt, vt, vd, pg, mc_, mdd = (DEFP['price_tol'], DEFP['vol_tol'], DEFP['vol_decay_thr'],
                                DEFP['prog_min'], DEFP['ma_conv'], DEFP['max_dd'])

    def pick_j(prio):
        return np.argmin(np.where(core, prio, 10 ** 9), axis=1).astype(np.int64)

    def struct_feats(jj):
        tk = lambda k: M[k][sel, jj[sel]]
        P1, P2, P3 = tk('P1'), tk('P2'), tk('P3')
        V1, V2, V3 = tk('V1'), tk('V2'), tk('V3')
        V0 = VOL[sel, 0]
        ddv = struct_dd[sel, jj[sel]]
        mcv = ma_conv[sel, jj[sel]]
        sp = tk('f3').astype(np.int64) - tk('f1').astype(np.int64)
        TR = np.ones(len(sel), dtype=bool)
        h1 = TR & (P2 >= P1 * (1 - pt)) & (P3 >= P2 * (1 - pt)) & (V2 <= V1 * (1 + vt)) & (V3 <= V2 * (1 + vt))
        h2 = TR & (ddv <= mdd) & (V1 <= vd * V0) & (V2 <= vd * V0) & (V3 <= vd * V0)
        h3 = TR & (P2 >= P1 * (1 + pg)) & (P3 >= P2 * (1 + pg))
        h4 = TR & (mcv <= mc_)
        h5 = h1 & h3 & h4
        h6 = h5 & (sp >= DEFP['gap6'])
        return dict(H1=h1, H2=h2, H3=h3, H4=h4, H5=h5, H6=h6), dict(
            P1=P1, P2=P2, P3=P3, V1=V1, V2=V2, V3=V3, V0=V0, ddv=ddv, mcv=mcv, sp=sp, tk=tk)

    print("--- 结构选择规则敏感性（默认参数下的命中数）---")
    for rn, rp in RULES.items():
        cnt, _ = struct_feats(pick_j(rp))
        print("  %-9s" % rn, {k: int(v.sum()) for k, v in cnt.items()})

    SEL_RULE = os.environ.get('TF_SEL_RULE', 'earliest')
    j = pick_j(RULES[SEL_RULE])
    DF, FF = struct_feats(j)
    print("选用规则:", SEL_RULE)

    # ── 结构特征分布 & 子条件通过率诊断
    P1, P2, P3 = FF['P1'], FF['P2'], FF['P3']
    V1, V2, V3 = FF['V1'], FF['V2'], FF['V3']
    V0d = FF['V0']; ddv = FF['ddv']; mcv = FF['mcv']
    dec = V3 / V1; prog_ = P3 / P1 - 1.0
    print("  k_off(确认日) 分位:", np.percentile(FF['tk']('c3'), [10, 25, 50, 75, 90]).round(1))
    print("  f1_off 分位:", np.percentile(FF['tk']('f1'), [10, 25, 50, 75, 90]).round(1))
    print("  span 分位:", np.percentile(FF['sp'], [10, 25, 50, 75, 90]).round(1))
    for nm, arr in (('volume_decay V3/V1', dec), ('price_prog P3/P1-1', prog_),
                    ('struct_dd', ddv), ('ma_conv', mcv), ('V3/V0', V3 / V0d)):
        print("  %-20s" % nm, np.percentile(arr, [10, 25, 50, 75, 90]).round(4))
    TR = np.ones(len(sel), dtype=bool)
    sub = {
        'H1_价格不降': (P2 >= P1 * (1 - pt)) & (P3 >= P2 * (1 - pt)),
        'H1_量递减': (V2 <= V1 * (1 + vt)) & (V3 <= V2 * (1 + vt)),
        'H3_重心抬升': (P2 >= P1 * (1 + pg)) & (P3 >= P2 * (1 + pg)),
        'H4_均线收敛': mcv <= mc_,
        'H2_dd<=maxdd': ddv <= mdd,
        'H2_三花量<=thr*V0': (V1 <= vd * V0d) & (V2 <= vd * V0d) & (V3 <= vd * V0d),
    }
    print("  子条件通过率:", {k: round(float(v.mean()), 3) for k, v in sub.items()})

    f1, f2, f3, c3 = (FF['tk']('f1').astype(np.int64), FF['tk']('f2').astype(np.int64),
                      FF['tk']('f3').astype(np.int64), FF['tk']('c3').astype(np.int64))
    P1, P2, P3 = FF['P1'], FF['P2'], FF['P3']
    V1, V2, V3 = FF['V1'], FF['V2'], FF['V3']
    hi_lvl = FF['tk']('hilvl').astype(np.float64)
    dd_s, mc_s = FF['ddv'], FF['mcv']
    ma5s, ma10s, ma20s = FF['tk']('ma5c'), FF['tk']('ma10c'), FF['tk']('ma20c')
    V0s = FF['V0']
    nfl = (T[sel] >= 0).sum(axis=1)

    # ── 前瞻收益
    def fwd_pack(off, price, valid):
        out = {}
        price = np.asarray(price, dtype=np.float64)
        for N in HOR:
            p = np.clip(off + N, 0, N_WIN - 1)
            okt = valid & ((off + N) <= (N_WIN - 1)) & Wvalid[sel, p]
            c = A_CLOSE[sel, p]
            out['r%d' % N] = np.where(okt & np.isfinite(c) & np.isfinite(price) & (price > 0),
                                      c / price - 1.0 - COST, np.nan).astype(np.float32)
        hi = np.where(valid, price, np.nan)
        lo = hi.copy(); runmax = hi.copy()
        ddmax = np.zeros(len(sel), dtype=np.float64)
        for s in range(1, 21):
            p = np.clip(off + s, 0, N_WIN - 1)
            okt = valid & ((off + s) <= (N_WIN - 1)) & Wvalid[sel, p]
            hh = np.where(okt, A_HIGH[sel, p], np.nan)
            ll = np.where(okt, A_LOW[sel, p], np.nan)
            cc = np.where(okt, A_CLOSE[sel, p], np.nan)
            hi = np.fmax(hi, hh); lo = np.fmin(lo, ll); runmax = np.fmax(runmax, cc)
            ddmax = np.fmax(ddmax, np.where(np.isfinite(runmax) & (runmax > 0),
                                            (runmax - cc) / runmax, 0.0))
        out['mfe'] = np.where(valid, hi / price - 1.0, np.nan).astype(np.float32)
        out['mae'] = np.where(valid, lo / price - 1.0, np.nan).astype(np.float32)
        out['path_dd'] = np.where(valid, ddmax, np.nan).astype(np.float32)
        return out

    base = fb[EV_COLS].copy()
    tf_flag = np.zeros(len(base), dtype=bool)
    tf_flag[sel] = True
    base['has_tf'] = tf_flag
    strf = ['f1_off', 'f2_off', 'f3_off', 'k_off', 'span', 'P1', 'P2', 'P3', 'V0', 'V1', 'V2',
            'V3', 'volume_decay', 'price_progression', 'vol_vs_t0', 'struct_dd', 'ma_conv',
            'ma5_ma10', 'ma10_ma20', 'ma_stack_up', 'min_close_vs_t0', 'hi_lvl', 'n_flower',
            'k_A', 'k_B']
    for c in strf:
        base[c] = np.nan
    base.loc[sel, 'f1_off'] = f1; base.loc[sel, 'f2_off'] = f2
    base.loc[sel, 'f3_off'] = f3; base.loc[sel, 'k_off'] = c3
    base.loc[sel, 'span'] = (f3 - f1)
    base.loc[sel, 'P1'] = P1; base.loc[sel, 'P2'] = P2; base.loc[sel, 'P3'] = P3
    base.loc[sel, 'V0'] = V0s
    base.loc[sel, 'V1'] = V1; base.loc[sel, 'V2'] = V2; base.loc[sel, 'V3'] = V3
    base.loc[sel, 'volume_decay'] = V3 / V1
    base.loc[sel, 'vol_vs_t0'] = V3 / V0s
    base.loc[sel, 'price_progression'] = P3 / P1 - 1.0
    base.loc[sel, 'struct_dd'] = dd_s
    base.loc[sel, 'ma_conv'] = mc_s
    base.loc[sel, 'ma5_ma10'] = ma5s / ma10s - 1.0
    base.loc[sel, 'ma10_ma20'] = ma10s / ma20s - 1.0
    base.loc[sel, 'ma_stack_up'] = ((ma5s >= ma10s) & (ma10s >= ma20s)).astype(float)
    base.loc[sel, 'min_close_vs_t0'] = lo1[sel, np.clip(c3, 0, N_WIN - 1)] / A_CLOSE[sel, 0] - 1.0
    base.loc[sel, 'hi_lvl'] = hi_lvl
    base.loc[sel, 'n_flower'] = nfl

    # Entry A / B
    eA_off = c3
    eA_px = A_CLOSE[sel, np.clip(c3, 0, N_WIN - 1)].astype(np.float64)
    vA = Wvalid[sel, np.clip(c3, 0, N_WIN - 1)]
    eB_off = c3 + 1
    eB_px = A_OPEN[sel, np.clip(c3 + 1, 0, N_WIN - 1)].astype(np.float64)
    vB = (eB_off <= N_WIN - 1) & Wvalid[sel, np.clip(c3 + 1, 0, N_WIN - 1)]
    base.loc[sel, 'k_A'] = eA_off
    base.loc[sel, 'k_B'] = np.where(vB, eB_off, -1)

    def put(name, off, price, valid, pack):
        base.loc[sel, name + '_k'] = np.where(valid, off, -1)
        for kk, vv in pack.items():
            base.loc[sel, name + '_' + kk] = vv

    put('A', eA_off, eA_px, vA, fwd_pack(eA_off, eA_px, vA))
    put('B', eB_off, eB_px, vB, fwd_pack(eB_off, eB_px, vB))

    for buf in BUFS:
        nm = BNAME[buf]
        Mbrk = np.zeros((len(sel), BRK_SEARCH), dtype=bool)
        for si in range(BRK_SEARCH):
            p = np.clip(c3 + 1 + si, 0, N_WIN - 1)
            okt = ((c3 + 1 + si) <= (N_WIN - 1)) & Wvalid[sel, p]
            h = A_HIGH[sel, p]
            Mbrk[:, si] = okt & np.isfinite(h) & (h > hi_lvl * (1 + buf))
        hh = Mbrk.any(axis=1)
        first = np.argmax(Mbrk, axis=1)
        o = np.where(hh, c3 + 1 + first, 0)
        p = np.clip(o, 0, N_WIN - 1)
        px = A_CLOSE[sel, p].astype(np.float64)
        vv = hh & Wvalid[sel, p]
        vr = VOL[sel, p] / VMA20[sel, p]
        abv = A_CLOSE[sel, p] >= MA20[sel, p]
        prev = np.clip(p - 1, 0, N_WIN - 1)
        gap = A_OPEN[sel, p] / A_CLOSE[sel, prev] - 1.0
        idc = A_CLOSE[sel, p] / A_OPEN[sel, p] - 1.0
        rng = A_HIGH[sel, p] - A_LOW[sel, p]
        pos = np.where(rng > 0, (A_CLOSE[sel, p] - A_LOW[sel, p]) / rng, 1.0)
        base.loc[sel, 'brk_%s_vr' % nm] = np.where(hh, vr, np.nan)
        base.loc[sel, 'brk_%s_abv' % nm] = np.where(hh, abv, np.nan)
        base.loc[sel, 'brk_%s_gap' % nm] = np.where(hh, gap, np.nan)
        base.loc[sel, 'brk_%s_idc' % nm] = np.where(hh, idc, np.nan)
        base.loc[sel, 'brk_%s_pos' % nm] = np.where(hh, pos, np.nan)
        put('C' + nm, o, px, vv, fwd_pack(o, px, vv))
        print("  缓冲", nm, "触发率", round(float(hh.mean()), 3))

    # ── 默认参数下的 H1..H6 标记（沿用已选定的结构，不再改变选择）
    for nm in DEFS:
        fl = np.zeros(len(base), dtype=bool)
        fl[sel[np.where(DF[nm])[0]]] = True
        base['d_' + nm] = fl
    print("已写默认定义命中数:", {k: int(v.sum()) for k, v in DF.items()})

    # ── 对照：首板后第 K 日买入
    for K in CTRL_K:
        p = np.clip(K, 0, N_WIN - 1)
        px = A_CLOSE[idx, p].astype(np.float64)
        vv = Wvalid[idx, p]
        for N in HOR:
            q = np.clip(K + N, 0, N_WIN - 1)
            okt = vv & ((K + N) <= N_WIN - 1) & Wvalid[idx, q]
            c = A_CLOSE[idx, q]
            base['c%d_r%d' % (K, N)] = np.where(okt & np.isfinite(px) & (px > 0),
                                                c / px - 1.0 - COST, np.nan).astype(np.float32)

    base = base.copy()
    base.to_parquet(os.path.join(OUT, "tf_base.parquet"), index=False)
    print("已写 tf_base.parquet", base.shape)

    meta = dict(panel_start=int(panel['trade_date'].min()), panel_end=int(panel['trade_date'].max()),
                n_first_board=int(n_ev), n_core_tf=int(has.sum()),
                N_WIN=N_WIN, W_OBS=W_OBS, pivot_R=R, min_gap=MIN_GAP, k_cand=K_CAND,
                brk_search=BRK_SEARCH, horizons=list(HOR), buffers=BUFS, core_dd=CORE_DD,
                cost=COST, slip=SLIP, comm=COMM, stamp=STAMP, defaults=DEFP, defs=DEFS)
    with open(os.path.join(OUT, "tf_meta.json"), 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print("已写 tf_meta.json")


if __name__ == '__main__':
    main()
