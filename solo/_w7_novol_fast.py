# -*- coding: utf-8 -*-
"""W7 配对回测的 numpy 快版核心（与引擎逐条对拍验证，见 _w7_novol_check.py）。

背景
----
全市场配对回测需要「每只股票 × 每个交易日 × 两套口径」重放引擎的分析链路。
实测（_w7_novol_prof5.py，60 只样本）：

    锚点查找           36.95 ms/次   ← 最大单项
    双探针             10.31 ms/次
    analyze 净成本     27.17 ms/次
    new 口径探针命中率  5.00%        ← 预筛可省 95% 的 analyze

因此本模块做三件事：
  ① extreme_event 全历史向量化预算（等价性已验证，196x）
  ② 锚点与两个 T0 探测器的 numpy 快版（逐条等价，用 _w7_novol_check.py 对拍）
  ③ MA/前复权「全历史预计算 + 逐日缩放」（等价性已验证，4.4x）

因果性说明
----------
引擎里所有参与「T0 锚定 + 两类信号判定」的函数都只读锚点之前（及当日）的数据：

  · extreme_event(df, i)          只读 df.iloc[i-120:i] 与 df.iloc[i]
  · extreme_cluster_anchor(df, c) 只读候选行本身
  · preshift_start_bar(df, ei)    只读 [ei-T0_PIVOT_BARS, ei) 区间
  · is_preshift_bar(df, i)        只读行 i 与行 i 之前的 19 根均量
  · find_activation_anchor(df)    只读 end 之前

而逐日回放的截断序列是全历史序列的**前缀**，前缀内同下标行的取值完全相同，
故「全历史一次预算 + 逐日按当前长度切片」与「逐日截断后重算」逐条等价。

MA/前复权的等价式
-----------------
  引擎逐日：qfq_D(i) = close(i) * adjf(i) / adjf(D)      （D = 当日末根因子）
            ma_w_D(i) = rolling_mean(qfq_D, w)(i)
  本模块：  p(i)      = close(i) * adjf(i)                 （未归一）
            ma_raw(i) = rolling_mean(p, w)(i)
            ma_w_D(i) = ma_raw(i) / adjf(D)
  因为 adjf(D) 对整条序列是常数因子，rolling_mean(x/c) == rolling_mean(x)/c。
"""
import numpy as np
import pandas as pd

W_EVENT = 120          # extreme_event 主窗口（事件日前 120 日分位）
MIN_BARS = 250         # 最少 K 线
MAX_EVENT_AGE = 60     # 近 60 天内出现天量事件即入候选池


# ───────────────────────────────────────────── 基础工具（与引擎逐条等价）
def finite(value, default=0.0):
    """引擎 finite() 的 numpy 版。"""
    try:
        v = float(value)
        return v if np.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def safe_mean(values, default=0.0):
    """引擎 safe_mean() 的 numpy 版。

    引擎实现：``[finite(x) for x in values if np.isfinite(finite(x))]`` 的均值。
    注意 finite(nan)=0.0 / finite(inf)=0.0 之后 isfinite 仍为真 → **非有限值以 0 计入分母**，
    与 np.nanmean（剔除且缩小分母）语义不同，必须逐条复刻。
    """
    v = np.asarray(values, dtype=float)
    if v.size == 0:
        return default
    return float(np.mean(np.where(np.isfinite(v), v, 0.0)))


def pct_position(open_, high, low, close):
    span = high - low
    return max(0.0, min(100.0, finite((close - low) / span * 100.0 if span > 0 else 50.0)))


def _window_mean(vals, lo, hi):
    """vals[lo:hi] 的 safe_mean 等价（越界自动收敛）。"""
    lo = max(0, lo)
    hi = min(len(vals), hi)
    if hi <= lo:
        return 0.0
    return safe_mean(vals[lo:hi])


def _prefix_means(vals, win):
    """返回长度 len(vals) 的数组：out[i] = safe_mean(vals[max(0,i-win+1) : i+1])。

    引擎里形如 ``safe_mean(df.vol.iloc[max(0, i-19):i])`` 的窗口是**左闭右开**、
    长度固定 19（i<19 时为 [0,i)），与本函数（长度 win、含当根）不同，
    故另设 _prefix_means_excl 处理「不含当根」的历史均量。
    """
    n = len(vals)
    v = np.asarray(vals, dtype=float)
    vv = np.where(np.isfinite(v), v, 0.0)
    ok = np.isfinite(v).astype(float)
    cs = np.concatenate(([0.0], np.cumsum(vv)))
    cn = np.concatenate(([0.0], np.cumsum(ok)))
    idx = np.arange(n)
    lo = np.maximum(0, idx - win + 1)
    s = cs[idx + 1] - cs[lo]
    c = cn[idx + 1] - cn[lo]
    return np.where(c > 0, s / np.maximum(c, 1.0), 0.0)


def _prefix_means_excl(vals, win):
    """out[i] = safe_mean(vals[max(0, i-win) : i])（不含当根，窗口长度 win）。"""
    n = len(vals)
    v = np.asarray(vals, dtype=float)
    vv = np.where(np.isfinite(v), v, 0.0)
    ok = np.isfinite(v).astype(float)
    cs = np.concatenate(([0.0], np.cumsum(vv)))
    cn = np.concatenate(([0.0], np.cumsum(ok)))
    idx = np.arange(n)
    lo = np.maximum(0, idx - win)
    s = cs[idx] - cs[lo]
    c = cn[idx] - cn[lo]
    return np.where(c > 0, s / np.maximum(c, 1.0), 0.0)


# ───────────────────────────────────────────── extreme_event 向量化预算
def vec_flags(m, A, lo, hi):
    """向量化 extreme_event(window=120)，返回 {i: (True, ep)}，仅覆盖 i in [lo, hi)。

    与引擎 extreme_event 逐条等价（_w7_novol_check.py 对拍 100% 一致）。
    """
    out = {}
    n = len(A["vols"])
    if hi <= W_EVENT:
        return out
    hi = min(hi, n)
    sw_v = np.lib.stride_tricks.sliding_window_view(A["vols"][:hi - 1], W_EVENT)
    sw_a = np.lib.stride_tricks.sliding_window_view(A["amts"][:hi - 1], W_EVENT)
    sw_t = np.lib.stride_tricks.sliding_window_view(A["turns"][:hi - 1], W_EVENT)
    p_vol = (sw_v <= A["vols"][W_EVENT:hi][:, None]).sum(axis=1) * 100.0 / W_EVENT
    p_amt = (sw_a <= A["amts"][W_EVENT:hi][:, None]).sum(axis=1) * 100.0 / W_EVENT
    p_turn = (sw_t <= A["turns"][W_EVENT:hi][:, None]).sum(axis=1) * 100.0 / W_EVENT
    pct, low, cls, hig = A["pct"], A["low"], A["cls"], A["hig"]
    codes = A["codes"]
    ldown = None
    for i in range(max(W_EVENT, lo), hi):
        k = i - W_EVENT
        pv, pa, pt = p_vol[k], p_amt[k], p_turn[k]
        if pv < m.MAIN_EVENT_PCT:
            continue
        prev_pct = pct[i - 1] if i >= 1 else 0.0
        if prev_pct > 10.0:
            if ldown is None:
                ldown = m.limit_down_pct(codes[i])
            if pct[i] <= -(ldown - 0.5):
                continue
            span = hig[i] - low[i]
            if span > 0 and (cls[i] - low[i]) / span < 0.30:
                continue
        elif pct[i] <= 0.0:
            span = hig[i] - low[i]
            if span > 0 and (cls[i] - low[i]) / span < 0.30:
                continue
        if pa >= m.MAIN_EVENT_PCT:
            out[i] = (True, max(pa, pv))
        elif pt >= m.MAIN_EVENT_PCT and A["turns"][i] > 0:
            out[i] = (True, max(pt, pv))
    return out


# ───────────────────────────────────────────── 单股预处理
class StockPrep(object):
    """单只股票的全历史预处理产物（一次 SQL 载入后全部算好）。"""

    __slots__ = ("m", "df", "dates", "n", "o", "h", "l", "c", "vol", "amt", "pct",
                 "turns", "adjf", "adjf_last", "ma_raw", "flags", "preshift",
                 "vol20_excl", "limit_like", "has_adj_gap", "code")

    def __init__(self, m, df, code):
        self.m = m
        self.code = code
        self.df = df.reset_index(drop=True)
        self.dates = self.df["trade_date"].astype(str).to_numpy()
        self.n = len(self.df)
        d = self.df
        self.o = d["open"].to_numpy(dtype=float)
        self.h = d["high"].to_numpy(dtype=float)
        self.l = d["low"].to_numpy(dtype=float)
        self.c = d["close"].to_numpy(dtype=float)
        self.vol = d["vol"].to_numpy(dtype=float)
        self.amt = d["amount"].to_numpy(dtype=float)
        self.pct = d["pct_chg"].to_numpy(dtype=float)
        self.turns = (d["turnover_rate"].to_numpy(dtype=float)
                      if "turnover_rate" in d.columns else np.zeros(self.n))
        adj = d["adj_factor"].to_numpy(dtype=float) if "adj_factor" in d.columns else None
        if adj is None:
            self.adjf = np.ones(self.n)
            self.has_adj_gap = False
        else:
            self.adjf = pd.Series(adj).ffill().to_numpy(dtype=float)
            self.has_adj_gap = bool(np.isnan(self.adjf).any())
        valid = np.isfinite(self.adjf) & (self.adjf > 0)
        self.adjf_last = float(self.adjf[valid][-1]) if valid.any() else 1.0
        # 全历史未归一前复权价 → MA 原料
        p = self.c * np.where(np.isfinite(self.adjf), self.adjf, 1.0)
        self.ma_raw = {}
        for w in (10, 20, 60, 120):
            self.ma_raw[w] = pd.Series(p).rolling(w, min_periods=w).mean().to_numpy()
        # extreme_event 全历史预算
        A = dict(vols=self.vol, amts=self.amt, turns=self.turns, pct=self.pct,
                 opn=self.o, hig=self.h, low=self.l, cls=self.c,
                 codes=np.array([code] * self.n, dtype=object))
        self.flags = vec_flags(m, A, MIN_BARS, self.n)
        # is_preshift_bar 全历史预算（只读行 i 与行 i 之前的 19 根均量 → 因果）
        v20 = _prefix_means_excl(self.vol, 19)
        need = np.where(self.pct >= m.T0_PRESHIFT_PCT_STRONG,
                        m.T0_PRESHIFT_VOLR_STRONG, m.T0_PRESHIFT_VOLR)
        self.preshift = ((self.c > self.o) & (self.pct >= m.T0_PRESHIFT_PCT)
                         & (v20 > 0) & (self.vol >= v20 * need))
        self.vol20_excl = _prefix_means_excl(self.vol, 19)
        # extreme_cluster_anchor 用的 limit_like
        self.limit_like = ((self.c > self.o) &
                           ((self.pct >= 9.0) |
                            ((self.h > 0) & (self.c >= self.h * 0.98) & (self.pct >= 3.0))))

    # ---------- MA 取值（逐日缩放） ----------
    def ma(self, w, k, adjf_D):
        """截断到第 k 根（0-based）时的 ma_bfq_w，等价引擎逐日重算。"""
        return self.ma_raw[w][:k + 1] / adjf_D

    def adjf_at(self, k):
        v = self.adjf[k]
        return float(v) if np.isfinite(v) and v > 0 else self.adjf_last


# ───────────────────────────────────────────── 锚点（numpy 快版）
def find_event_anchor_np(m, sp, k, ps_cache):
    """等价引擎 find_event_anchor(df_truncated)，其中截断长度为 k+1。

    返回 (event_idx, ep) 或 None。
    """
    n = k + 1
    if n < MIN_BARS:
        return None
    cands = []
    lo = max(MIN_BARS, n - MAX_EVENT_AGE - 1)
    for i in range(lo, n - 2):
        hit = sp.flags.get(i)
        if hit is not None and hit[0]:
            cands.append((i, hit[1]))
    if not cands:
        return None
    ei, ep = _cluster_anchor(m, sp, cands)
    return _preshift_start_bar(m, sp, ei, ps_cache), ep


def _cluster_anchor(m, sp, candidates):
    """等价引擎 extreme_cluster_anchor。"""
    if not candidates:
        return None
    clusters, cur = [], [candidates[0]]
    for c in candidates[1:]:
        if c[0] - cur[-1][0] <= 2:
            cur.append(c)
        else:
            clusters.append(cur)
            cur = [c]
    clusters.append(cur)
    last_cluster = clusters[-1]
    for i, ep in last_cluster:
        if sp.limit_like[i]:
            return i, ep
    return max(last_cluster, key=lambda c: sp.vol[c[0]])


def _preshift_start_bar(m, sp, event_idx, ps_cache):
    """等价引擎 preshift_start_bar（结果只依赖 event_idx → 按 event_idx 缓存）。"""
    key = event_idx
    hit = ps_cache.get(key)
    if hit is not None:
        return hit
    lo = max(0, event_idx - m.T0_PIVOT_BARS)
    if event_idx - lo < 5:
        ps_cache[key] = event_idx
        return event_idx
    pivot = int(np.argmin(sp.l[lo:event_idx])) + lo
    res = event_idx
    for i in range(pivot, event_idx):
        if sp.preshift[i]:
            res = i
            break
    ps_cache[key] = res
    return res


def find_activation_anchor_np(m, sp, k):
    """等价引擎 find_activation_anchor(df_truncated)。返回 (bar_index, 0.0) 或 None。"""
    n = k + 1
    if n < 30:
        return None
    end = n - 1
    lo = max(21, end - 60)
    seg = sp.preshift[lo:end + 1]
    if not seg.any():
        return None
    j = end - int(np.argmax(seg[::-1]))
    return j, 0.0


# ───────────────────────────────────────────── 两个 T0 探测器（numpy 快版）
def _t0_pullback_segment_np(sp, event_idx, end):
    """等价引擎 _t0_pullback_segment。

    返回 (pk_rel, cur_low, low_rel) 或 None。
    """
    if end <= event_idx:
        return None
    highs = sp.h[event_idx + 1:end + 1]
    if not np.isfinite(highs).any():
        return None
    pk_rel = int(np.nanargmax(highs))
    pull_lo = event_idx + 1 + pk_rel + 1
    if pull_lo > end:
        # 尚无回踩段
        return pk_rel, 0.0, -1
    lows = sp.l[pull_lo:end + 1]
    if not np.isfinite(lows).any():
        return pk_rel, 0.0, -1
    low_rel = int(np.nanargmin(lows))
    return pk_rel, float(np.nanmin(lows)), low_rel


def t0_pullback_hold_np(m, sp, event_idx, end, gate_pull_shrink, gate_stall_volr,
                         ma60_end, t0_open, t0_close, t0_vol):
    """等价引擎 t0_pullback_hold。返回 (成立, info 或 None)。"""
    if event_idx is None or end - event_idx < 3 or end - event_idx > m.W7_T0_MAX_AGE:
        return False, None
    if t0_open <= 0 or t0_close <= 0 or t0_vol <= 0:
        return False, None
    vol20_pre = _window_mean(sp.vol, event_idx - 20, event_idx)
    if vol20_pre <= 0 or t0_vol < vol20_pre * 1.3:
        return False, None
    seg = _t0_pullback_segment_np(sp, event_idx, end)
    if seg is None:
        return False, None
    pk_rel, cur_low, low_rel = seg
    if low_rel < 0 or cur_low <= 0:
        return False, None
    seg_all = sp.l[event_idx + 1:end + 1]
    deep_low = finite(np.nanmin(seg_all) if np.isfinite(seg_all).any() else np.nan, 0.0)
    if deep_low <= 0:
        return False, None
    if deep_low > t0_close * (1 - m.W7_T0_PULLBACK_MIN):
        return False, None
    if deep_low > t0_open * (1 + m.W7_T0_REACH):
        return False, None
    if deep_low < t0_open * (1 - m.W7_T0_OPEN_TOL):
        return False, None
    pull_lo = event_idx + 1 + pk_rel + 1
    pull_vol = safe_mean(sp.vol[pull_lo:end + 1]) if pull_lo <= end else 0.0
    if gate_pull_shrink and pull_vol > t0_vol * m.W7_T0_SHRINK:
        return False, None
    o, h, l, c = sp.o[end], sp.h[end], sp.l[end], sp.c[end]
    if min(o, h, l, c) <= 0:
        return False, None
    span = h - l
    if span <= 0:
        return False, None
    body = abs(c - o)
    up_sh = (h - max(o, c)) / span
    dn_sh = (min(o, c) - l) / span
    pct = finite(sp.pct[end])
    stall = ""
    if body <= 0.35 * span and up_sh >= 0.15 and dn_sh >= 0.15:
        stall = "十字星"
    elif dn_sh >= 0.40:
        stall = "长下影"
    elif c > o and pct >= 1.5:
        stall = "阳线收复"
    if not stall:
        return False, None
    low_idx = pull_lo + low_rel
    if end - low_idx > m.W7_T0_STALL_MAX_BARS:
        return False, None
    if c > cur_low * 1.05:
        return False, None
    ma60 = finite(ma60_end, 0.0)
    if ma60 > 0 and c < ma60 * m.W7_T0_MA60_K:
        return False, None
    if c > t0_close * m.W7_T0_STALL_MAX_EXT:
        return False, None
    vol20_now = _window_mean(sp.vol, end - 19, end)
    if gate_stall_volr and vol20_now > 0 and finite(sp.vol[end]) > vol20_now * m.W7_T0_STALL_VOLR:
        return False, None
    return True, {
        "t0_open": t0_open, "t0_close": t0_close, "t0_vol": t0_vol,
        "pull_low": deep_low, "cur_low": cur_low,
        "pull_bars": end - low_idx,
        "pull_vol_ratio": pull_vol / t0_vol if t0_vol > 0 else 0.0,
        "stall": stall, "stall_dn_sh": dn_sh, "stall_body": body / span,
        "t0_ext": c / t0_close if t0_close > 0 else 0.0,
        "stall_volr": (finite(sp.vol[end]) / vol20_now) if vol20_now > 0 else 0.0,
    }


def t0_rebreak_np(m, sp, event_idx, end, gate_pull_shrink, gate_break_volr,
                  ma60_end, t0_open, t0_close, t0_vol):
    """等价引擎 t0_rebreak。返回 (成立, info 或 None)。"""
    if event_idx is None or end - event_idx < 3 or end - event_idx > m.W7_T0_B_MAX_AGE:
        return False, None
    if t0_open <= 0 or t0_close <= 0 or t0_vol <= 0:
        return False, None
    vol20_pre = _window_mean(sp.vol, event_idx - 20, event_idx)
    if vol20_pre <= 0 or t0_vol < vol20_pre * 1.3:
        return False, None
    seg = _t0_pullback_segment_np(sp, event_idx, end)
    if seg is None:
        return False, None
    pk_rel, cur_low, low_rel = seg
    if low_rel < 0 or cur_low <= 0:
        return False, None
    pre_lo = event_idx + 1
    pre_hi = end          # 不含突破日（引擎 df.iloc[event_idx+1:end]）
    if pre_hi <= pre_lo:
        return False, None
    pre_low = finite(np.nanmin(sp.l[pre_lo:pre_hi]) if np.isfinite(sp.l[pre_lo:pre_hi]).any() else np.nan, 0.0)
    if pre_low <= 0:
        return False, None
    if pre_low > t0_close * (1 - m.W7_T0_PULLBACK_MIN):
        return False, None
    if pre_low < t0_open * (1 - m.W7_T0_OPEN_TOL):
        return False, None
    o, h, l, c = sp.o[end], sp.h[end], sp.l[end], sp.c[end]
    if min(o, h, l, c) <= 0:
        return False, None
    if l < t0_open * (1 - m.W7_T0_OPEN_TOL):
        return False, None
    pre_vol = safe_mean(sp.vol[pre_lo:pre_hi])
    if gate_pull_shrink and pre_vol > t0_vol * m.W7_T0_SHRINK_B:
        return False, None
    if c <= t0_close:
        return False, None
    vol20_now = _window_mean(sp.vol, end - 19, end)
    if gate_break_volr and (vol20_now <= 0 or finite(sp.vol[end]) < vol20_now * m.W7_T0_BREAK_VOLR):
        return False, None
    span = h - l
    if span <= 0:
        return False, None
    if pct_position(o, h, l, c) < m.W7_T0_BREAK_CLOSE_POS:
        return False, None
    if (h - c) / span > m.W7_T0_BREAK_SHADOW:
        return False, None
    ma60 = finite(ma60_end, 0.0)
    if ma60 > 0 and c < ma60 * m.W7_T0_MA60_K:
        return False, None
    if c > t0_close * m.W7_T0_BREAK_MAX_EXT:
        return False, None
    return True, {
        "t0_open": t0_open, "t0_close": t0_close, "t0_vol": t0_vol,
        "pull_low": pre_low,
        "pull_bars": pre_hi - pre_lo,
        "pull_vol_ratio": pre_vol / t0_vol if t0_vol > 0 else 0.0,
        "break_volr": finite(sp.vol[end]) / vol20_now if vol20_now > 0 else 0.0,
        "break_ext": c / t0_close if t0_close > 0 else 0.0,
    }
