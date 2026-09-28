# -*- coding: utf-8 -*-
"""
H-TREND-PULLBACK-01  P1c：趋势日面板 + First-Pullback 事件构建

覆盖规格：§6 Cohort / §9-§13 回撤定义 / §16-§20 回撤特征 / §21 Re-acceleration
          / §22-§25 Target 与 Entry Anchor / §57 失败模式

核心设计（一次扫描产出三张表）
  1) hp_trenddays.parquet —— 每一「强趋势日」一行（§6 Strong Trend Cohort）
     · 自带 is_pb 标记，从而 B2/B3 反事实可在同一张表内完成匹配（§28/§29/§30）
     · 所有特征严格 PIT：只用截至当日（含）的信息
  2) hp_events.parquet    —— E1（Pullback Observation Entry）事件（§24）
  3) hp_events_e2.parquet —— E2（Re-acceleration Entry）信号（§25），锚点独立

Pre-registered 口径（不可事后更改）
  · 强趋势 strong(§7) = valid ∧ Close>MA20>MA60 ∧ slope20>0 ∧ slope60>=0
  · Run           = 自「最近一次 20 日新高日 h」次日起，至「下一次 20 日新高日」前一日止
  · 回撤起点 pb_start = Run 内第一个「收盘下跌日」（Close < 前收），严格晚于 h
  · Peak          = pb_start 当日的 peak20（= 前 20 日最高价，不含当日，含 h 日高价）
  · Episode       = [pb_start, ep_end]，ep_end = 最早发生{收盘收复 close[h]、下一 20 日新高、
                    所在强趋势 leg 结束、距 pb_start 满 40 交易日}之日
  · Depth         = (Peak - 区间内最低价 Low) / Peak（Low 自 pb_start 起算，绝不含 h 日自身最低价）
  · Duration      = 自 pb_start 起的交易日数
  · E1 信号日     = Episode 内首个满足 Depth>=1% 且 Duration>=2 的交易日；同日收盘出信号，次日开盘成交
  · 「第一次」    = 同一 trend leg 内首个「合格」Episode（未合格的浅回撤不占用名额）
  · reacc1(§21)   = 自 E1 信号日起 20 交易日内首次 Close > close[h]
  · reacc2/E2     = 自 E1 信号日起 20 交易日内首次 Close > Peak（突破前高，独立入场锚点）
  · 目标: exe_H = close[k+H]/open[k+1] - 1（次日开盘进场，主口径）
          raw_H = close[k+H]/close[k] - 1（收盘锚，仅描述）
"""
import os
import sys
import time

import numpy as np
import pandas as pd

from hp_common import (DATA, PAN, OUTD, PREREG, mklog, pload_meta, pload_day,
                       pexists)

LOG = mklog('events')

HZ = tuple(PREREG['horizons'])
PH_MIN_D = PREREG['pb_min_depth']
PH_MIN_N = PREREG['pb_min_days']
PH_MAX_N = PREREG['pb_max_days']
REACC_MAX = PREREG['reacc_max_days']
WIN = 20                                   # §57 失败模式观察窗口（交易日）


def _f32(d):
    """把 dict 中的 float64 列统一压成 float32（控制全量内存）"""
    return {k: (np.asarray(v).astype(np.float32)
                if np.asarray(v).dtype == np.float64 else v)
            for k, v in d.items()}


def _ep_cummean(vals, mask, ids, ncal):
    """Episode 内累计均值（跳过 NaN，且不因 NaN 污染计数）"""
    out = np.full(ncal, np.nan)
    a = np.asarray(vals, dtype=np.float64)
    if mask.any():
        ok = np.isfinite(a)
        sub = pd.DataFrame({'ep': ids[mask],
                            'v': np.where(ok, a, 0.0)[mask],
                            'c': ok.astype(np.float64)[mask]})
        g = sub.groupby('ep')
        num = g['v'].cumsum().values
        den = g['c'].cumsum().values
        out[mask] = np.where(den > 0, num / np.maximum(den, 1e-12), np.nan)
    return out


def _ep_cummin(vals, mask, ids, ncal):
    """Episode 内累计最小值"""
    out = np.full(ncal, np.nan)
    if mask.any():
        out[mask] = (pd.DataFrame({'ep': ids[mask],
                                   'v': np.asarray(vals, dtype=np.float64)[mask]})
                     .groupby('ep')['v'].cummin().values)
    return out


def _ep_cumcount(flag, mask, ids, ncal):
    """Episode 内 0/1 指示变量的累计计数（专用于「上涨日数 / 下跌日数」这类频次）

    注意：不能用 _ep_cummean —— 指示变量下分子与分母同源，结果恒为 1。
    """
    out = np.full(ncal, np.nan)
    if mask.any():
        sub = pd.DataFrame({'ep': ids[mask],
                            'v': np.where(np.asarray(flag, dtype=bool), 1.0, 0.0)[mask]})
        out[mask] = sub.groupby('ep')['v'].cumsum().values
    return out


def main():
    t0 = time.time()
    argv = sys.argv[1:]
    limit = 0
    if '--limit' in argv:
        limit = int(argv[argv.index('--limit') + 1])

    LOG.sep('=')
    LOG('H-TREND-PULLBACK-01  P1c 趋势日面板 + 首次回撤事件  %s'
        % time.strftime('%Y-%m-%d %H:%M:%S'))

    M = pload_meta()
    NCAL, NC = int(M['NCAL']), int(M['NCODES'])
    if limit:
        NC = min(NC, limit)
        LOG('   [SMOKE] --limit %d' % NC)
    day = pload_day()
    dates = [str(x) for x in day['dates']]
    date_int = np.array([int(x) for x in dates], dtype=np.int32)
    year, month = day['year'], day['month']
    regime = day['regime']
    ph = day['phase']

    need = ['close', 'open', 'high', 'low', 'vol', 'amount', 'turnover',
            'ma20', 'ma60', 'slope20', 'slope60', 'ret_5', 'ret_10', 'ret_20',
            'ret_60', 'atr_pct', 'rv_20', 'v_ma20', 'v_ratio', 'rsi14',
            'pos_60', 'pos_120', 'dd_60', 'nh20', 'peak20', 'valid',
            'total_mv', 'circ_mv']
    C = {}
    for nm in need:
        C[nm] = np.asarray(np.load(os.path.join(PAN, nm + '.npy'), mmap_mode='r'))[:NC]
    ind_l1 = np.asarray(np.load(os.path.join(PAN, '_code_ind_l1.npy')))[:NC]

    # ---- 行业等权指数（§20 / §27）----
    isum = np.asarray(np.load(os.path.join(PAN, '_ind_sum.npy'))).astype(np.float64)
    icnt = np.asarray(np.load(os.path.join(PAN, '_ind_cnt.npy'))).astype(np.float64)
    with np.errstate(divide='ignore', invalid='ignore'):
        ind_r1 = np.where(icnt >= 5, isum / np.maximum(icnt, 1.0), np.nan)
    ind_lvl = np.full_like(ind_r1, np.nan)
    ff = np.where(np.isfinite(ind_r1), 1.0 + np.nan_to_num(ind_r1), 1.0)
    ind_lvl[:, 0] = 1.0
    ind_lvl[:, 1:] = np.cumprod(ff[:, 1:], axis=1)
    LOG('   行业指数 %d 个（成分>=5 才计入）' % ind_r1.shape[0])

    # ---- 基准（§19）----
    b_ret = {w: day.get('ret%d_hs300' % w, np.full(NCAL, np.nan)).astype(np.float64)
             for w in HZ}
    hs_lvl = day.get('lvl_hs300', np.full(NCAL, np.nan)).astype(np.float64)
    have_hs = bool(M['bench_available'].get('hs300', False))
    LOG('   HS300 可得: %s（其余基准 UNAVAILABLE，如实标注，不做替代）' % have_hs)

    arr = np.arange(NCAL)
    rows_ev, rows_e2, rows_all = [], [], []
    n_leg_tot = n_run_tot = n_ep_tot = n_cand_tot = 0
    t_prev = time.time()

    for c in range(NC):
        v = C['valid'][c]
        if not v.any():
            continue
        cl, op, hi, lo = C['close'][c], C['open'][c], C['high'][c], C['low'][c]
        ma20, ma60 = C['ma20'][c], C['ma60'][c]
        s20, s60 = C['slope20'][c], C['slope60'][c]

        # 注意：面板 valid 为 int8（0/1），必须显式转 bool。
        # 否则 strong 为 int8，`~strong` 会退化为按位取反（~1=-2, ~0=-1），
        # 用作 fancy index 只会写到末尾两天，非强趋势日将保留 leg 编号，
        # 导致 same_leg 逸出强趋势集合、混入不合格 E1 事件。
        strong = ((v > 0) & np.isfinite(cl) & np.isfinite(ma20) & np.isfinite(ma60)
                  & (cl > ma20) & (ma20 > ma60)
                  & np.isfinite(s20) & np.isfinite(s60)
                  & (s20 > 0) & (s60 >= 0))
        if not strong.any():
            continue

        # ---- trend leg ----
        d = np.diff(strong.astype(np.int8), prepend=np.int8(0))
        leg_arr = np.cumsum(d == 1).astype(np.int32)
        leg_arr[~strong] = -1
        n_leg_tot += int((d == 1).sum())
        leg_start_fwd = np.maximum.accumulate(np.where(d == 1, arr, -1))
        leg_end = np.maximum.accumulate(np.where(strong, arr, -1)[::-1])[::-1]

        # ---- 20 日新高（须在强趋势内）与 Run 划分 ----
        nh = (C['nh20'][c] > 0) & strong
        n_run_tot += int(nh.sum())
        idx = np.where(nh, arr, -1)
        last_nh = np.maximum.accumulate(idx)
        has = last_nh >= 0
        ln = np.maximum(last_nh, 0)
        same_leg = has & (leg_arr[ln] == leg_arr)
        same_leg[~has] = False

        # Run 结束 = 下一个 20 日新高前一日，且不超过 leg 结束
        nhk = np.where(nh, arr, NCAL)
        rev = np.minimum.accumulate(nhk[::-1])[::-1]
        nxt_nh = np.full(NCAL, NCAL)
        nxt_nh[:-1] = rev[1:]
        run_end = np.minimum(nxt_nh - 1, NCAL - 1)
        run_end = np.minimum(run_end, np.where(leg_end >= 0, leg_end, NCAL - 1))
        run_end = np.where(same_leg, run_end, -1)

        # ---- 回撤起点：Run 内第一个「收盘下跌日」（严格晚于 h）----
        prev_cl = np.full(NCAL, np.nan)
        prev_cl[1:] = cl[:-1]
        down = same_leg & np.isfinite(prev_cl) & (cl < prev_cl) & (arr > last_nh)
        gdn = pd.Series(np.where(down, 1.0, 0.0)).groupby(last_nh).cumsum().values
        first_dn = down & (gdn == 1)
        pb_idx = np.where(first_dn, arr, NCAL)
        pb_start = pd.Series(pb_idx).groupby(last_nh).transform('min').values
        pb_start = np.where(same_leg, pb_start, NCAL)
        n_ep_tot += int(first_dn.sum())

        # ---- Episode 结束：最早{收复 close[h]、下一新高、leg 结束、40 日上限} ----
        close_at_h = np.where(has, cl[ln], np.nan)
        reclaim = (same_leg & np.isfinite(close_at_h) & np.isfinite(cl)
                   & (cl > close_at_h) & (arr > last_nh))
        rec_idx = np.where(reclaim, arr, NCAL)
        rec_first = pd.Series(rec_idx).groupby(last_nh).transform('min').values
        ep_end = np.minimum.reduce([rec_first, run_end,
                                    pb_start + PH_MAX_N - 1])
        ep_end = np.where(same_leg & (pb_start < NCAL), ep_end, -1)
        in_ep = (same_leg & (pb_start < NCAL) & (ep_end >= 0)
                 & (arr >= pb_start) & (arr <= ep_end))
        in_ep[~same_leg] = False

        ep_id = np.where(in_ep, last_nh, -1)
        pb_fwd = np.minimum(np.maximum(pb_start, 0), NCAL - 1)

        # ---- 回撤几何（§10-§13）----
        peak_ep = np.where(in_ep, C['peak20'][c][pb_fwd], np.nan)
        ep_low = _ep_cummin(lo, in_ep, ep_id, NCAL)
        with np.errstate(divide='ignore', invalid='ignore'):
            depth_run = (peak_ep - ep_low) / peak_ep
        ep_days = np.where(in_ep, arr - pb_start + 1.0, np.nan)
        with np.errstate(divide='ignore', invalid='ignore'):
            speed = depth_run / ep_days

        # ---- Episode 内量能 / 波动（§16-§18）----
        d1 = pd.Series(cl).pct_change().values
        vol_pb = _ep_cummean(C['vol'][c], in_ep, ep_id, NCAL)
        to_pb = _ep_cummean(C['turnover'][c], in_ep, ep_id, NCAL)
        atr_pb = _ep_cummean(C['atr_pct'][c], in_ep, ep_id, NCAL)
        rv_pb = np.sqrt(np.maximum(_ep_cummean(np.where(np.isfinite(d1), d1 ** 2, np.nan),
                                              in_ep, ep_id, NCAL), 0.0))
        dn_vol = _ep_cummean(np.where(np.isfinite(d1) & (d1 < 0), C['vol'][c], np.nan),
                             in_ep, ep_id, NCAL)
        up_vol = _ep_cummean(np.where(np.isfinite(d1) & (d1 > 0), C['vol'][c], np.nan),
                             in_ep, ep_id, NCAL)
        dn_n = _ep_cumcount(np.isfinite(d1) & (d1 < 0), in_ep, ep_id, NCAL)
        up_n = _ep_cumcount(np.isfinite(d1) & (d1 > 0), in_ep, ep_id, NCAL)

        # 趋势参照 = 以高点日 h 为终点、向前 20 日的均值（严格 PIT）
        to20 = pd.Series(C['turnover'][c]).rolling(20, min_periods=20).mean().values
        with np.errstate(divide='ignore', invalid='ignore'):
            vol_ref = C['v_ma20'][c][ln]
            to_ref = np.where(has, to20[ln], np.nan)
            atr_ref = np.where(has, C['atr_pct'][c][ln], np.nan)
            rv_ref = np.where(has, C['rv_20'][c][ln], np.nan)
            vol_ratio = vol_pb / vol_ref
            to_ratio = to_pb / to_ref
            atr_ratio = atr_pb / atr_ref
            rv_ratio = rv_pb / rv_ref
            dn_vol_ratio = dn_vol / (up_vol + dn_vol)
            dn_day_ratio = dn_n / (up_n + dn_n)

        # ---- 距均线（§14 / §15）----
        with np.errstate(divide='ignore', invalid='ignore'):
            cl_ma20 = cl / ma20 - 1.0
            cl_ma60 = cl / ma60 - 1.0
            lo_ma20 = lo / ma20 - 1.0
            lo_ma60 = lo / ma60 - 1.0
            ep_low_ma20 = ep_low / ma20 - 1.0
            ep_low_ma60 = ep_low / ma60 - 1.0

        # ---- 相对强度（§19 / §20）----
        with np.errstate(divide='ignore', invalid='ignore'):
            rel_c = np.where(ln > 0, cl / cl[ln] - 1.0, np.nan)
            rel_m = np.where(ln > 0, hs_lvl / hs_lvl[ln] - 1.0, np.nan)
            rs_pb_mkt = rel_c - rel_m if have_hs else np.full(NCAL, np.nan)
            il = ind_lvl[int(ind_l1[c])]
            rel_i = np.where(ln > 0, il / il[ln] - 1.0, np.nan)
            rs_pb_ind = rel_c - rel_i

        # ---- 趋势强度（§8；取高点日状态，用于匹配与控制）----
        with np.errstate(divide='ignore', invalid='ignore'):
            ts_ret20 = np.where(has, C['ret_20'][c][ln], np.nan)
            ts_ret60 = np.where(has, C['ret_60'][c][ln], np.nan)
            ts_cl_ma20 = np.where(has, cl[ln] / ma20[ln] - 1.0, np.nan)
            ts_ma20_ma60 = np.where(has, ma20[ln] / ma60[ln] - 1.0, np.nan)
            ts_slope20 = np.where(has, s20[ln], np.nan)

        # ---- 目标收益（§22 / §23）----
        op1 = np.full(NCAL, np.nan)
        op1[:-1] = op[1:]
        entry_ok = np.zeros(NCAL, dtype=bool)
        entry_ok[:-1] = v[1:] & np.isfinite(op[1:]) & (op[1:] > 0)
        tgt = {}
        for hh in HZ:
            cl_h = np.full(NCAL, np.nan)
            if hh < NCAL:
                cl_h[:NCAL - hh] = cl[hh:]
            with np.errstate(divide='ignore', invalid='ignore'):
                tgt['exe_%d' % hh] = np.where(entry_ok, cl_h / op1 - 1.0, np.nan)
                tgt['raw_%d' % hh] = cl_h / cl - 1.0
        gap1 = np.where(entry_ok, op1 / cl - 1.0, np.nan)

        # ---- E1 信号日：Episode 内首个 Depth>=1% 且 Duration>=2（每 leg 取最早）----
        qual = in_ep & np.isfinite(depth_run) & (depth_run >= PH_MIN_D) \
            & np.isfinite(ep_days) & (ep_days >= PH_MIN_N)
        sig_k = np.array([], dtype=np.int64)
        if qual.any():
            qdf = pd.DataFrame({'k': arr, 'ln': last_nh, 'leg': leg_arr, 'q': qual})
            qq = qdf[qdf['q']]
            first_in_run = qq.groupby('ln')['k'].min()
            sig = pd.DataFrame({'ln': first_in_run.index.values,
                                'k': first_in_run.values})
            sig['leg'] = leg_arr[sig['ln'].values]
            sig = sig.sort_values('k').drop_duplicates('leg', keep='first')
            sig_k = sig['k'].values
            n_cand_tot += len(sig_k)

        # ================= 组装：所有强趋势日 =================
        sel = np.where(strong)[0]
        if len(sel):
            rec = {
                'code': np.repeat(c, len(sel)),
                'k': sel.astype(np.int32),
                'date': date_int[sel],
                'ind_l1': np.repeat(int(ind_l1[c]), len(sel)),
                'phase': ph[sel], 'year': year[sel], 'month': month[sel],
                'regime': regime[sel],
                'leg': leg_arr[sel],
                'trend_age': (sel - leg_start_fwd[sel]),
                'nh_flag': nh[sel].astype(np.int8),
                'in_ep': in_ep[sel].astype(np.int8),
                'since_high': np.where(same_leg[sel], sel - last_nh[sel], np.nan),
                'since_pb': np.where(in_ep[sel], sel - pb_fwd[sel], np.nan),
                'peak': peak_ep[sel],
                'ep_days': ep_days[sel],
                'depth': depth_run[sel],
                'speed': speed[sel],
                'ep_low': ep_low[sel],
                'cl_ma20': cl_ma20[sel], 'cl_ma60': cl_ma60[sel],
                'lo_ma20': lo_ma20[sel], 'lo_ma60': lo_ma60[sel],
                'ep_low_ma20': ep_low_ma20[sel], 'ep_low_ma60': ep_low_ma60[sel],
                'vol_ratio': vol_ratio[sel], 'to_ratio': to_ratio[sel],
                'atr_ratio': atr_ratio[sel], 'rv_ratio': rv_ratio[sel],
                'dn_vol_ratio': dn_vol_ratio[sel], 'dn_day_ratio': dn_day_ratio[sel],
                'rs_pb_mkt': rs_pb_mkt[sel], 'rs_pb_ind': rs_pb_ind[sel],
                'ret_5': C['ret_5'][c][sel], 'ret_10': C['ret_10'][c][sel],
                'mom20': C['ret_20'][c][sel], 'mom60': C['ret_60'][c][sel],
                'rs5': C['ret_5'][c][sel] - b_ret[5][sel],
                'rs10': C['ret_10'][c][sel] - b_ret[10][sel],
                'rs20': C['ret_20'][c][sel] - b_ret[20][sel],
                'total_mv': C['total_mv'][c][sel], 'circ_mv': C['circ_mv'][c][sel],
                'turnover': C['turnover'][c][sel],
                'atr_pct': C['atr_pct'][c][sel], 'rv_20': C['rv_20'][c][sel],
                'v_ratio': C['v_ratio'][c][sel], 'rsi14': C['rsi14'][c][sel],
                'pos_60': C['pos_60'][c][sel], 'pos_120': C['pos_120'][c][sel],
                'dd_60': C['dd_60'][c][sel],
                'ts_ret20': ts_ret20[sel], 'ts_ret60': ts_ret60[sel],
                'ts_cl_ma20': ts_cl_ma20[sel], 'ts_ma20_ma60': ts_ma20_ma60[sel],
                'ts_slope20': ts_slope20[sel],
                'gap1': gap1[sel],
            }
            for hh in HZ:
                rec['exe_%d' % hh] = tgt['exe_%d' % hh][sel]
                rec['raw_%d' % hh] = tgt['raw_%d' % hh][sel]
            rec['is_pb'] = np.isin(sel, sig_k).astype(np.int8)
            rows_all.append(pd.DataFrame(_f32(rec)))

        # ================= 组装：E1 / E2 事件 =================
        for k in sig_k:
            k = int(k)
            h = int(last_nh[k])
            end = int(min(ep_end[k], NCAL - 1))
            if end < k:
                end = k
            pk = float(peak_ep[k])
            lo_win = float(np.nanmin(lo[k:end + 1]))
            ep_low_sig = float(ep_low[k])
            # re-acceleration 窗口：信号日之后 20 交易日内
            we = np.arange(k + 1, min(k + 1 + REACC_MAX, NCAL))

            def _first(cond):
                if not len(cond):
                    return 0, np.nan
                hit = np.where(cond)[0]
                return (1, int(hit[0] + 1)) if len(hit) else (0, np.nan)

            ok_w = v[we] & np.isfinite(cl[we])
            reacc1, reacc1_day = _first(ok_w & (cl[we] > close_at_h[k]))
            reacc2, reacc2_day = _first(ok_w & np.isfinite(pk) & (cl[we] > pk))
            # 成功 = Episode 内收复 close[h]，或因再创 20 日新高而终止
            resolved = int(bool(rec_first[k] <= end)
                           or (nxt_nh[k] == end + 1 and nxt_nh[k] < NCAL))

            e = {'code': c, 'k': k, 'date': int(date_int[k]),
                 'h_k': h, 'pb_k': int(pb_fwd[k]),
                 'entry_date': int(date_int[k + 1]) if k + 1 < NCAL else 0,
                 'entry_price': float(op1[k]) if np.isfinite(op1[k]) else np.nan,
                 'entry_ok': int(entry_ok[k]),
                 'leg': int(leg_arr[k]),
                 'since_high': int(k - h), 'ep_days': int(k - pb_fwd[k] + 1),
                 'peak': pk, 'pb_low_to_end': lo_win, 'ep_low_at_sig': ep_low_sig,
                 'depth_final': (pk - lo_win) / pk if pk > 0 else np.nan,
                 'depth_at_sig': float(depth_run[k]),
                 'end_k': end, 'end_date': int(date_int[min(end, NCAL - 1)]),
                 'dur_to_end': int(end - k + 1),
                 'resolved': resolved,
                 'reacc1': reacc1, 'reacc1_day': reacc1_day,
                 'reacc2': reacc2, 'reacc2_day': reacc2_day,
                 'ind_l1': int(ind_l1[c]), 'phase': int(ph[k]), 'year': int(year[k]),
                 'regime': int(regime[k]),
                 'vol_ratio': float(vol_ratio[k]), 'to_ratio': float(to_ratio[k]),
                 'atr_ratio': float(atr_ratio[k]), 'rv_ratio': float(rv_ratio[k]),
                 'dn_vol_ratio': float(dn_vol_ratio[k]),
                 'dn_day_ratio': float(dn_day_ratio[k]),
                 'rs_pb_mkt': float(rs_pb_mkt[k]), 'rs_pb_ind': float(rs_pb_ind[k]),
                 'mom20': float(C['ret_20'][c][k]), 'mom60': float(C['ret_60'][c][k]),
                 'total_mv': float(C['total_mv'][c][k]),
                 'circ_mv': float(C['circ_mv'][c][k]),
                 'turnover': float(C['turnover'][c][k]),
                 'rv_20': float(C['rv_20'][c][k]), 'atr_pct': float(C['atr_pct'][c][k]),
                 'pos_60': float(C['pos_60'][c][k]), 'dd_60': float(C['dd_60'][c][k]),
                 'ts_ret20': float(ts_ret20[k]), 'ts_ret60': float(ts_ret60[k]),
                 'ts_cl_ma20': float(ts_cl_ma20[k]),
                 'ts_ma20_ma60': float(ts_ma20_ma60[k]),
                 'ts_slope20': float(ts_slope20[k])}
            for hh in HZ:
                e['exe_%d' % hh] = float(tgt['exe_%d' % hh][k])
                e['raw_%d' % hh] = float(tgt['raw_%d' % hh][k])
            # ---- §57 失败模式 ----
            jw = np.arange(k + 1, min(k + 1 + WIN, NCAL))
            prior60 = float(np.nanmin(lo[max(0, k - 59):k + 1]))
            def _any(cond):
                return int(bool(np.any(cond))) if len(cond) else 0
            e['F1_break_ma20'] = _any(v[jw] & np.isfinite(ma20[jw]) & (cl[jw] < ma20[jw]))
            e['F2_break_ma60'] = _any(v[jw] & np.isfinite(ma60[jw]) & (cl[jw] < ma60[jw]))
            e['F3_prior_low_break'] = _any(v[jw] & np.isfinite(lo[jw]) & (lo[jw] < prior60))
            e['F4_deeper_drawdown'] = _any(v[jw] & np.isfinite(lo[jw]) & (lo[jw] < ep_low_sig))
            e['F6_gap_down'] = _any(np.isfinite(op1[jw]) & np.isfinite(cl[jw - 1])
                                    & (op1[jw] / cl[jw - 1] - 1.0 < -0.03))
            e['F8_regime_bear'] = int(regime[k] == 0)
            if len(jw):
                ki = il[k]
                ir = il[jw[-1]] / ki - 1.0 if np.isfinite(ki) and ki > 0 else np.nan
            else:
                ir = np.nan
            e['F9_industry_fail'] = int(bool(np.isfinite(ir) and ir < 0))
            if reacc2 == 1:
                rk = k + int(reacc2_day)
                e['F5_false_reacc'] = int(cl[min(k + WIN, NCAL - 1)] < cl[k])
                vv = C['v_ma20'][c][rk] if rk < NCAL else np.nan
                e['F7_vol_fail'] = int(not (np.isfinite(vv) and np.isfinite(C['vol'][c][rk])
                                            and C['vol'][c][rk] > vv))
            else:
                e['F5_false_reacc'] = 0
                e['F7_vol_fail'] = 0
            rows_ev.append(e)

            # ---- E2（§25）：信号日 = 首次 Close>Peak ----
            if reacc2 == 1:
                k2 = k + int(reacc2_day)
                if k2 < NCAL:
                    e2 = {'code': c, 'k': k2, 'e1_k': k, 'date': int(date_int[k2]),
                          'entry_date': int(date_int[k2 + 1]) if k2 + 1 < NCAL else 0,
                          'entry_price': float(op1[k2]) if np.isfinite(op1[k2]) else np.nan,
                          'entry_ok': int(entry_ok[k2]),
                          'leg': int(leg_arr[k2]), 'since_e1': int(k2 - k),
                          'peak': pk, 'ind_l1': int(ind_l1[c]),
                          'phase': int(ph[k2]), 'year': int(year[k2]),
                          'regime': int(regime[k2]),
                          'cl_ma20': float(cl_ma20[k2]), 'cl_ma60': float(cl_ma60[k2]),
                          'depth_at_sig': float(depth_run[k]),
                          'mom20': float(C['ret_20'][c][k2]),
                          'total_mv': float(C['total_mv'][c][k2]),
                          'atr_pct': float(C['atr_pct'][c][k2]),
                          'rsi14': float(C['rsi14'][c][k2]),
                          'gap1': float(gap1[k2])}
                    for hh in HZ:
                        e2['exe_%d' % hh] = float(tgt['exe_%d' % hh][k2])
                        e2['raw_%d' % hh] = float(tgt['raw_%d' % hh][k2])
                    rows_e2.append(e2)

        if (c + 1) % 500 == 0:
            dt = time.time() - t_prev
            t_prev = time.time()
            LOG('   ... %d / %d  leg=%d run=%d ep=%d cand=%d  (%.0fs)'
                % (c + 1, NC, n_leg_tot, n_run_tot, n_ep_tot, n_cand_tot, dt))

    LOG.sep()
    LOG('扫描完成：trend leg %d，Run %d，Episode %d，E1 候选（每 leg 首个合格）%d'
        % (n_leg_tot, n_run_tot, n_ep_tot, n_cand_tot))

    if not rows_all:
        LOG('[FAIL] 无强趋势日，检查面板')
        return 1
    TD = pd.concat(rows_all, ignore_index=True)
    for c_ in TD.columns:
        if TD[c_].dtype == np.float64:
            TD[c_] = TD[c_].astype(np.float32)
    EV = pd.DataFrame(rows_ev) if rows_ev else pd.DataFrame()
    E2 = pd.DataFrame(rows_e2) if rows_e2 else pd.DataFrame()

    LOG('趋势日面板 %d 行（宽 %d 列）；E1 %d 条；E2 %d 条'
        % (len(TD), TD.shape[1], len(EV), len(E2)))
    LOG('   强趋势日内属于回撤 Episode 的比例 %.2f%%；is_pb=1 %d 行'
        % (100.0 * TD['in_ep'].mean(), int(TD['is_pb'].sum())))
    if len(EV):
        LOG('   depth_at_sig  p25 %.4f / p50 %.4f / p75 %.4f'
            % tuple(np.nanquantile(EV['depth_at_sig'], [.25, .5, .75])))
        LOG('   depth_final   p25 %.4f / p50 %.4f / p75 %.4f'
            % tuple(np.nanquantile(EV['depth_final'], [.25, .5, .75])))
        LOG('   ep_days       p25 %.1f / p50 %.1f / p75 %.1f'
            % tuple(np.nanquantile(EV['ep_days'], [.25, .5, .75])))
        LOG('   resolved %.3f  reacc1 %.3f（收复 close[h]）  reacc2 %.3f（突破 Peak）'
            % (float(EV['resolved'].mean()), float(EV['reacc1'].mean()),
               float(EV['reacc2'].mean())))
        LOG('   entry_ok %.4f（次日不可交易 = 停牌或末日）' % float(EV['entry_ok'].mean()))
        for hh in HZ:
            v_ = EV['exe_%d' % hh]
            LOG('   E1 exe_%d  mean %+.4f  median %+.4f  n %d'
                % (hh, float(v_.mean()), float(v_.median()), int(v_.notna().sum())))
    if len(E2):
        for hh in HZ:
            v_ = E2['exe_%d' % hh]
            LOG('   E2 exe_%d  mean %+.4f  median %+.4f  n %d'
                % (hh, float(v_.mean()), float(v_.median()), int(v_.notna().sum())))
    LOG.sep()
    LOG('   by phase:')
    for pn, pi in [('TRAIN', 1), ('VALID', 2), ('OOS', 3), ('LIVE-LIKE', 4)]:
        if len(EV):
            m = (EV['phase'] == pi)
            LOG('     %-10s E1 %6d  reacc1 %.3f  reacc2 %.3f'
                % (pn, int(m.sum()),
                   float(EV.loc[m, 'reacc1'].mean()) if m.any() else np.nan,
                   float(EV.loc[m, 'reacc2'].mean()) if m.any() else np.nan))

    TD.to_parquet(os.path.join(DATA, 'hp_trenddays.parquet'), index=False)
    EV.to_parquet(os.path.join(DATA, 'hp_events.parquet'), index=False)
    E2.to_parquet(os.path.join(DATA, 'hp_events_e2.parquet'), index=False)
    LOG('落盘: hp_trenddays.parquet / hp_events.parquet / hp_events_e2.parquet')
    LOG.sep('=')
    LOG('P1c 完成 %.0fs' % (time.time() - t0))
    print('DONE')
    return 0


if __name__ == '__main__':
    sys.exit(main())
