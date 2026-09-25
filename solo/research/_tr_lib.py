# -*- coding: utf-8 -*-
"""首板 → 第一次分歧 → 缩量止跌 → 再启动 · 事件链派生库

设计原则
  1. 全部派生量只用「锚点日及之前」的信息 → 无未来函数
  2. 不预设任何阈值：分歧深度 / 缩量阈值 / 结构档 / 再启动量能 均为候选空间参数
  3. 事件链分步可拆：A(首板) → B(分歧) → C(缩量) → D(结构未破) → E(再启动) → F(完整)
  4. 配对超额按「锚点 offset 分层」计算，剔除时点效应

矩阵约定（mats）
  二维键 shape = (n_events, KMAX+1)，列 = 首板后第 k 个交易日（k=0 即首板日）：
    cl hi lo op vo          前复权价 / 量
    ama5 ama10 ama20 ama60  均线（前复权）
    vma5 vma20              均量
    lim ow amt isl          涨停价 / 一字板 / 成交额 / 当日是否涨停
    dt                      交易日（YYYYMMDD）
    gap                     与前一交易日的日历间隔（>5 视为停牌跨期）
    idxc                    基准指数收盘（中证1000）
    ret                     cl[k]/cl[0]-1
    dd                      cl[k]/runmax(hi[0..k])-1（收盘口径回撤）
    ddl                     lo[k]/runmax(hi[0..k])-1（盘中口径回撤）
    cp                      (cl-lo)/(hi-lo)
    vr                      vo/vma20
    vov0                    vo/vo[0]
    vov5                    vo/vma5
    dma5 dma10 dma20 dma60  cl/ma-1
    ma5slope                ama5[k]/ama5[k-1]-1
    dayret                  cl[k]/cl[k-1]-1
    idxret                  idxc[k]/idxc[0]-1
    rs                      dayret - 指数当日涨跌
    runmax                  runmax(hi[0..k])
    ddrun                   runmin(dd[0..k])
    dvol uvol               下跌日成交量 / 上涨日成交量（否则 0）
  一维键（shape=(n,)）：C0 H0 L0 O0 V0 PC0（首板日关键价位，PC0 为前复权前收盘=起涨位置）
"""
import numpy as np

KMAX = 25          # 观察窗口 T0 ~ T+25
NA = 15            # anchor 允许的最大 offset（保证 a + 10 <= KMAX）
HOR = (3, 5, 10)
TH_ALL = (0.01, 0.02, 0.03, 0.05, 0.08)          # 分歧深度候选空间
KD_ALL = (1, 2, 3, 4, 5)                         # 连续缩量天数候选空间
VT_ALL = (1.0, 1.1, 1.2, 1.3, 1.5, 1.8, 2.0)     # 再启动量能候选空间
SLIP_ALL = (0.0, 0.001, 0.002, 0.003)            # 单边滑点档
FEE = 0.00055                                    # 佣金 0.025%×2 + 印花税 0.05%
COST_DEF = 2 * 0.001 + FEE                       # 默认双边成本 = 0.255%


def cost_of(slip):
    return 2.0 * slip + FEE


def load_mats(path):
    """读取 _tr_build.py 落盘的窗口矩阵"""
    z = np.load(path, allow_pickle=False)
    return {k: z[k] for k in z.files}


# ────────────────────────── 固定窗口工具（快，全部向量化） ──────────────────────────
def _seg(m, k0, k1):
    """列区间 [k0, k1] 的子矩阵，越界返回 None"""
    k0 = int(k0)
    k1 = int(k1)
    if k0 < 0 or k1 < k0 or k1 > KMAX:
        return None
    return m[:, k0:k1 + 1]


def wmin(m, k0, k1):
    s = _seg(m, k0, k1)
    if s is None:
        return np.full(m.shape[0], np.nan)
    with np.errstate(all='ignore'):
        return np.nanmin(s, axis=1)


def wmax(m, k0, k1):
    s = _seg(m, k0, k1)
    if s is None:
        return np.full(m.shape[0], np.nan)
    with np.errstate(all='ignore'):
        return np.nanmax(s, axis=1)


def wmean(m, k0, k1):
    s = _seg(m, k0, k1)
    if s is None:
        return np.full(m.shape[0], np.nan)
    with np.errstate(all='ignore'):
        return np.nanmean(s, axis=1)


def wsum(m, k0, k1):
    s = _seg(m, k0, k1)
    if s is None:
        return np.full(m.shape[0], np.nan)
    with np.errstate(all='ignore'):
        return np.nansum(s, axis=1)


def col(m, k):
    if k < 0 or k > KMAX:
        return np.full(m.shape[0], np.nan)
    return m[:, int(k)].astype(np.float64)


# ────────────────────────── 事件链 ──────────────────────────
def first_disagree(mat, th, kmax=5):
    """第一次分歧：k∈{1..kmax} 首个 mat[k] <= -th；无 → -1（mat 为二维矩阵，如 dd / ddl）"""
    n = mat.shape[0]
    src = mat
    fd = np.full(n, -1, dtype=np.int8)
    for k in range(1, kmax + 1):
        hit = (fd < 0) & np.isfinite(src[:, k]) & (src[:, k] <= -th)
        fd[hit] = k
    return fd


def shrink_run(m, fd):
    """自 fd 之后连续缩量天数（vol[k] < vol[k-1]），从 fd+1 起算，上限 KMAX-fd"""
    vo = m['vo']
    n = vo.shape[0]
    run = np.zeros(n, dtype=np.int8)
    for f in range(1, 6):
        sel = np.where(fd == f)[0]
        if sel.size == 0:
            continue
        r = np.zeros(sel.size, dtype=np.int8)
        alive = np.ones(sel.size, dtype=bool)
        for k in range(f + 1, KMAX + 1):
            a = vo[sel, k]
            b = vo[sel, k - 1]
            step = alive & np.isfinite(a) & np.isfinite(b) & (a < b)
            r[step] += 1
            alive &= step
            if not alive.any():
                break
        run[sel] = r
    return run


def decay_metrics(m, fd, kd):
    """卖压衰减候选指标（窗口 = fd+1 .. fd+kd，仅用锚点日及之前信息）"""
    n = m['vo'].shape[0]
    vo = m['vo']
    out = {}
    V1 = np.full(n, np.nan); V2 = np.full(n, np.nan)
    V3 = np.full(n, np.nan); V6 = np.full(n, np.nan)
    V5 = np.full(n, np.nan)
    for f in range(1, 6):
        sel = np.where(fd == f)[0]
        if sel.size == 0:
            continue
        k0, k1 = f + 1, min(f + kd, KMAX)
        if k0 > k1:
            continue
        V1[sel] = wmean(m['vr'][sel], k0, k1)
        V2[sel] = wmean(m['vov0'][sel], k0, k1)
        num = wmean(vo[sel], k0, k1)
        den = vo[sel, f]
        with np.errstate(all='ignore'):
            V3[sel] = np.where(den > 0, num / den, np.nan)
        dvv = wsum(m['dvol'][sel], k0, k1)
        uvv = wsum(m['uvol'][sel], k0, k1)
        with np.errstate(all='ignore'):
            V6[sel] = np.where(uvv > 0, dvv / uvv, np.nan)
    dep = value_at(m['dd'], fd)
    with np.errstate(all='ignore'):
        V5 = dep / np.maximum(1e-6, 1.0 - V3)
    out['V1_vr20'] = V1
    out['V2_vov0'] = V2
    out['V3_vovfd'] = V3
    out['V4_run'] = shrink_run(m, fd).astype(np.float64)
    out['V5_dep_voldrop'] = V5
    out['V6_dvol_uvol'] = V6
    return out


def value_at(mat, fd):
    """按 per-event offset 取值，-1 → nan"""
    fd = np.asarray(fd, dtype=np.int64)
    n = fd.size
    out = np.full(n, np.nan)
    ok = (fd >= 0) & (fd <= KMAX)
    rows = np.where(ok)[0]
    out[rows] = mat[rows, fd[rows]]
    return out


def struct_flags(m, fd, kd):
    """结构未破（窗口 = fd .. fd+kd，全部只用锚点日及之前的信息）"""
    n = m['cl'].shape[0]
    lo_min = np.full(n, np.nan)
    cl_min = np.full(n, np.nan)
    dd_min = np.full(n, np.nan)
    m5 = np.full(n, np.nan); m10 = np.full(n, np.nan); m20 = np.full(n, np.nan)
    for f in range(1, 6):
        sel = np.where(fd == f)[0]
        if sel.size == 0:
            continue
        k1 = min(f + kd, KMAX)
        lo_min[sel] = wmin(m['lo'][sel], f, k1)
        cl_min[sel] = wmin(m['cl'][sel], f, k1)
        dd_min[sel] = wmin(m['dd'][sel], f, k1)
        m5[sel] = wmax(m['ama5'][sel], f, k1)
        m10[sel] = wmax(m['ama10'][sel], f, k1)
        m20[sel] = wmax(m['ama20'][sel], f, k1)
    C0, H0, L0, O0, PC0 = m['C0'], m['H0'], m['L0'], m['O0'], m['PC0']
    out = {
        'S1_low_gt_L0': lo_min >= L0,
        'S2_low_gt_bodymid': lo_min >= (O0 + C0) / 2.0,
        'S3_low_gt_C0': lo_min >= C0,
        'S4_low_gt_pc0': lo_min >= PC0,
        'S5_cl_gt_ma5': cl_min >= m5,
        'S6_cl_gt_ma10': cl_min >= m10,
        'S7_cl_gt_ma20': cl_min >= m20,
        'S8_dd5': dd_min >= -0.05,
        'S8_dd8': dd_min >= -0.08,
        'S8_dd10': dd_min >= -0.10,
        'S8_dd12': dd_min >= -0.12,
        'S8_dd15': dd_min >= -0.15,
        'ddmin': dd_min,
    }
    return out


def restart_day(m, fd, rule, vt=1.0, kmin=None):
    """再启动日：首个满足规则的 k（kmin .. KMAX），无 → -1

    kmin = None 时取 fd+1（即分歧日之后任意一天）；
    传入 kmin 可强制「再启动必须发生在缩量窗口结束之后」，保证事件链因果顺序。
    """
    n = m['cl'].shape[0]
    out = np.full(n, -1, dtype=np.int8)
    cl = m['cl']; hi = m['hi']; vr = m['vr']; rs = m['rs']
    fdv = np.where(fd >= 0, fd, -1)
    kmin_arr = fdv + 1 if kmin is None else np.asarray(kmin, dtype=np.int64)
    for k in range(1, KMAX + 1):
        cand = np.where((out < 0) & (fd >= 0) & (k >= kmin_arr))[0]
        if cand.size == 0:
            continue
        fdk = fd[cand].astype(np.int64)
        c = cl[cand, k]
        ok = np.isfinite(c)
        if rule == 'R1':
            h = hi[cand, fdk]
            ok &= np.isfinite(h) & (c > h)
        elif rule == 'R2':
            rm = _rhs(m, fd, k, 'hi', mode='run')
            ok &= np.isfinite(rm[cand]) & (c > rm[cand])
        elif rule == 'R3':
            rm = _rhs(m, fd, k, 'hi', mode='local')
            ok &= np.isfinite(rm[cand]) & (c > rm[cand])
        elif rule == 'R4':
            ma5 = m['ama5'][cand, k]
            ma5p = m['ama5'][cand, max(k - 1, 0)]
            clp = cl[cand, max(k - 1, 0)]
            ok &= np.isfinite(ma5) & np.isfinite(ma5p) & (c > ma5) & (clp <= ma5p)
        elif rule == 'R5':
            s = m['ma5slope'][cand, k]
            sp = m['ma5slope'][cand, max(k - 1, 0)]
            ok &= np.isfinite(s) & np.isfinite(sp) & (s > 0) & (sp <= 0)
        elif rule == 'R6':
            rm = _rhs(m, fd, k, 'hi', mode='run')
            ok &= np.isfinite(rm[cand]) & (c > rm[cand]) & np.isfinite(vr[cand, k]) & (vr[cand, k] >= vt)
        elif rule == 'R7':
            rm = _rhs(m, fd, k, 'hi', mode='run')
            ok &= np.isfinite(rm[cand]) & (c > rm[cand]) & np.isfinite(rs[cand, k]) & (rs[cand, k] > 0)
        else:
            raise ValueError(rule)
        out[cand[ok]] = k
    return out


def _rhs(m, fd, k, key, mode='run'):
    """per-event 参考水平：run = max(hi[fd..k-1])；local = max(hi[1..k-1])"""
    n = m[key].shape[0]
    out = np.full(n, np.nan)
    if mode == 'local':
        if k < 2:
            return out
        out[:] = np.nanmax(m[key][:, 1:k], axis=1)
        return out
    for f in range(1, 6):
        sel = np.where(fd == f)[0]
        if sel.size == 0 or k <= f:
            continue
        out[sel] = np.nanmax(m[key][sel, f:k], axis=1)
    return out


def entry_day(m, fd, rule, vt=1.0, kmin=None):
    """Entry 候选日（kmin 语义同 restart_day）"""
    if rule == 'E1':
        k0 = (np.where(fd >= 0, fd, -1) + 1) if kmin is None else np.asarray(kmin, dtype=np.int64)
        e = np.where((fd < 0) | (k0 < 0), -1, k0)
        return np.where(e > KMAX, -1, e).astype(np.int8)
    if rule == 'E2':
        return restart_day(m, fd, 'R1', kmin=kmin)
    if rule == 'E3':
        return restart_day(m, fd, 'R2', kmin=kmin)
    if rule == 'E4':
        return restart_day(m, fd, 'R6', vt=vt, kmin=kmin)
    if rule == 'E5':
        base = restart_day(m, fd, 'R6', vt=vt, kmin=kmin)
        cl = m['cl']; ma20 = m['ama20']
        ok = np.zeros(cl.shape[0], dtype=bool)
        for k in range(1, KMAX + 1):
            sel = np.where(base == k)[0]
            if sel.size == 0:
                continue
            ok[sel] = np.isfinite(cl[sel, k]) & np.isfinite(ma20[sel, k]) & (cl[sel, k] >= ma20[sel, k])
        return np.where(ok, base, -1).astype(np.int8)
    if rule == 'E6':
        out = np.full(m['cl'].shape[0], -1, dtype=np.int8)
        cl = m['cl']; ma5 = m['ama5']; dr = m['dayret']
        kmin_arr = (np.where(fd >= 0, fd, -1) + 1) if kmin is None else np.asarray(kmin, dtype=np.int64)
        for k in range(1, KMAX + 1):
            cand = np.where((out < 0) & (fd >= 0) & (k >= kmin_arr))[0]
            if cand.size == 0:
                continue
            ok = (np.isfinite(cl[cand, k]) & np.isfinite(ma5[cand, k]) & (cl[cand, k] > ma5[cand, k])
                  & np.isfinite(dr[cand, k]) & (dr[cand, k] > 0.02))
            out[cand[ok]] = k
        return out
    raise ValueError(rule)


# ────────────────────────── 前瞻收益 ──────────────────────────
def fwd_ret(m, anc, N, cost=0.0):
    cl = m['cl']
    n = cl.shape[0]
    aa = np.asarray(anc, dtype=np.int64)
    out = np.full(n, np.nan)
    ok = (aa >= 0) & (aa + N <= KMAX)
    rows = np.where(ok)[0]
    c0 = cl[rows, aa[rows]]
    c1 = cl[rows, aa[rows] + N]
    good = np.isfinite(c0) & np.isfinite(c1) & (c0 > 0)
    r2 = rows[good]
    out[r2] = cl[r2, aa[r2] + N] / cl[r2, aa[r2]] - 1.0 - cost
    return out


def fwd_mae_mfe(m, anc, N):
    n = m['cl'].shape[0]
    aa = np.asarray(anc, dtype=np.int64)
    mae = np.full(n, np.nan); mfe = np.full(n, np.nan)
    ok = (aa >= 0) & (aa + N <= KMAX)
    if not ok.any():
        return mae, mfe
    ar = np.arange(1, N + 1)
    j = aa[:, None] + ar[None, :]
    rows = np.arange(n)[:, None]
    valid = ok[:, None] & (j <= KMAX)
    jj = np.clip(j, 0, KMAX)
    lo = np.where(valid, m['lo'][rows, jj], np.nan)
    hi = np.where(valid, m['hi'][rows, jj], np.nan)
    c0 = m['cl'][np.arange(n), np.clip(aa, 0, KMAX)]
    with np.errstate(all='ignore'):
        mn = np.nanmin(lo, axis=1)
        mx = np.nanmax(hi, axis=1)
    good = ok & np.isfinite(c0) & (c0 > 0)
    mae[good] = mn[good] / c0[good] - 1.0
    mfe[good] = mx[good] / c0[good] - 1.0
    return mae, mfe


# ────────────────────────── 统计 ──────────────────────────
def stats(v, tag=''):
    x = np.asarray(v, dtype=np.float64)
    x = x[np.isfinite(x)]
    d = {'n': int(x.size)}
    if x.size == 0:
        for k in ('mean', 'med', 'win', 'p25', 'p75', 'pf', 'std'):
            d[k] = np.nan
        return d
    d['mean'] = float(x.mean())
    d['med'] = float(np.median(x))
    d['win'] = float((x > 0).mean())
    d['p25'] = float(np.percentile(x, 25))
    d['p75'] = float(np.percentile(x, 75))
    up = x[x > 0].sum(); dn = -x[x < 0].sum()
    d['pf'] = float(up / dn) if dn > 1e-12 else np.inf
    d['std'] = float(x.std(ddof=1)) if x.size > 1 else np.nan
    return d


def offset_bench(m, N, cost=0.0, mask=None, min_bucket=20, amax=None):
    """全样本「同 offset 进入」的前瞻收益中位数（剔除时点效应）"""
    n = m['cl'].shape[0]
    sel = np.ones(n, dtype=bool) if mask is None else np.asarray(mask)
    if amax is None:
        amax = KMAX - N
    bench = {}
    for a in range(0, amax + 1):
        v = fwd_ret(m, np.full(n, a, dtype=np.int64), N, cost)
        v = v[sel & np.isfinite(v)]
        bench[a] = float(np.median(v)) if v.size >= min_bucket else np.nan
    return bench


def paired_excess(m, mask, anc, N, cost=0.0, bench=None, min_bucket=20):
    """按锚点 offset 分层配对超额：组内中位数 − 同 offset 基准中位数，按桶样本量加权"""
    v = fwd_ret(m, anc, N, cost)
    aa = np.asarray(anc, dtype=np.int64)
    idx = np.where(np.asarray(mask) & np.isfinite(v) & (aa >= 0))[0]
    if idx.size == 0:
        return np.nan, 0, 0.0, np.nan, 0
    a = aa[idx]
    if bench is None:
        bench = offset_bench(m, N, cost, min_bucket=min_bucket)
    num = 0.0; den = 0; posb = 0; nb = 0; diffs = []
    for off in np.unique(a):
        s = (a == off)
        if s.sum() < 5:
            continue
        b = bench.get(int(off), np.nan)
        if not np.isfinite(b):
            continue
        d = float(np.median(v[idx[s]])) - b
        diffs.append(d)
        num += d * int(s.sum()); den += int(s.sum())
        nb += 1; posb += int(d > 0)
    adv = num / den if den > 0 else np.nan
    arr = np.asarray(diffs)
    t = np.nan
    if arr.size >= 3 and arr.std(ddof=1) > 0:
        t = float(arr.mean() / (arr.std(ddof=1) / np.sqrt(arr.size)))
    return adv, den, (posb / nb if nb else 0.0), t, nb
