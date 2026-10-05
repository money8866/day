# -*- coding: utf-8 -*-
"""CSI2000 Position Gate —— 特征引擎（§5~§10 五类核心变量）

只产出可解释的独立字段，不做「趋势总分」式加权合并（§5 明确禁止）。
所有列一律以 trade_date 为索引、按时间升序；每个时点只用 <= 当日 的数据（无未来函数）。

T1~T5 / breadth_* / rs* / volume_* / R1~R7 全部保留为独立布尔或数值列。
"""
import gc
import os
import pickle

import numpy as np
import pandas as pd

from position_gate import config as C
from position_gate.data import GateData


# =========================================================
# 工具：宽表透视图（缓存到 position_gate/cache，避免反复读 21.9GB 主库）
# =========================================================
def _pivot_path(start: str, end: str) -> str:
    return os.path.join(C.CACHE_DIR, f'pivots_{start}_{end}.pkl')


def _pivot(panel: pd.DataFrame, col: str) -> pd.DataFrame:
    """长表 → 宽表（trade_date × ts_code）。daily_cache 主键唯一，pivot 即可"""
    try:
        w = panel.pivot(index='trade_date', columns='ts_code', values=col)
    except Exception:
        w = panel.pivot_table(index='trade_date', columns='ts_code', values=col, aggfunc='first')
    return w.astype('float32')


def load_pivots(data: GateData, start: str, end: str,
                refresh: bool = False, verbose: bool = True) -> dict:
    """加载/构建个股日线宽表：close / high / low / pct_chg / total_mv"""
    path = _pivot_path(start, end)
    if not refresh and os.path.exists(path):
        if verbose:
            print(f'[features] 命中宽表缓存 {os.path.basename(path)}', flush=True)
        try:
            with open(path, 'rb') as f:
                return pickle.load(f)
        except ModuleNotFoundError as e:
            if verbose:
                print(f'[features] 旧缓存依赖缺失({e})，重建宽表缓存 ...', flush=True)
        except Exception as e:
            if verbose:
                print(f'[features] 宽表缓存读取失败({e})，重建 ...', flush=True)
    if verbose:
        print(f'[features] 读取全市场日线 {start}~{end} ...', flush=True)
    panel = data.market_panel(start, end)
    pv = {c: _pivot(panel, c) for c in ('close', 'high', 'low', 'pct_chg')}
    if verbose:
        print(f'[features] 个股宽表 {pv["close"].shape[0]} 天 × {pv["close"].shape[1]} 只', flush=True)
    del panel
    gc.collect()
    mv = data.mv_panel(start, end)
    pv['total_mv'] = _pivot(mv, 'total_mv')
    del mv
    gc.collect()
    try:
        with open(path, 'wb') as f:
            pickle.dump(pv, f, protocol=4)
    except Exception as e:
        print(f'[features] 宽表缓存写入失败（不影响运行）: {e}')
    return pv


def _rolling_mean(w: pd.DataFrame, n: int) -> pd.DataFrame:
    return w.rolling(n, min_periods=n).mean()


# =========================================================
# §5 Trend
# =========================================================
def trend_features(idx: pd.DataFrame, ma_windows=None, ma20_slope_windows=None,
                   pivot_ma: int = 20) -> pd.DataFrame:
    """§5 Trend

    pivot_ma 为「中枢均线」窗口（默认 20）。§25 参数稳定性会扰动为 15/25；
    此时仍输出 ma20 / ma20_slope_* 列名（值为 pivot 均线），使状态机口径不变。
    """
    wins = sorted(set(ma_windows or C.MA_WINDOWS) | {pivot_ma})
    close = idx['close'].astype('float64')
    out = pd.DataFrame(index=idx.index)
    for n in wins:
        out[f'ma{n}'] = close.rolling(n, min_periods=n).mean()
    if pivot_ma != 20:                                  # 别名：状态机只认 ma20 这个名字
        out['ma20'] = out[f'ma{pivot_ma}']
    for n in (ma20_slope_windows or C.MA20_SLOPE_WINDOWS):
        out[f'ma{pivot_ma}_slope_{n}'] = (out[f'ma{pivot_ma}'] / out[f'ma{pivot_ma}'].shift(n) - 1) * 100
        if pivot_ma != 20:
            out[f'ma20_slope_{n}'] = out[f'ma{pivot_ma}_slope_{n}']
    for n in C.MA60_SLOPE_WINDOWS:
        out[f'ma60_slope_{n}'] = (out['ma60'] / out['ma60'].shift(n) - 1) * 100
    out['close'] = close
    # T1~T5 独立字段（不合并为趋势总分）
    out['T1_close_gt_ma20'] = (close > out['ma20'])
    out['T2_ma20_gt_ma60'] = (out['ma20'] > out['ma60'])
    out['T3_ma20_slope_up'] = (out['ma20_slope_5'] > 0)
    out['T4_close_gt_ma60'] = (close > out['ma60'])
    out['T5_ma60_slope_up'] = (out['ma60_slope_20'] > 0)
    return out


# =========================================================
# §6/§7 Breadth
# =========================================================
def build_universe(pv: dict, data: GateData, official_only: bool = False,
                   size_skip: int = None, size_n: int = None):
    """逐日 universe 掩码（dates × codes，bool）+ 来源标记

    双轨口径：
      ① OFFICIAL：存在 <=t 的官方中证2000成分快照时，用最近一期快照（as-of，无未来函数）
      ② DYNAMIC ：快照不可用时，用当日 total_mv 排名动态重构小盘池
                   （剔除市值最大的 SIZE_UNIVERSE_SKIP 只后，取剩余最小的 SIZE_UNIVERSE_N 只）
    """
    skip = C.SIZE_UNIVERSE_SKIP if size_skip is None else size_skip
    n_take = C.SIZE_UNIVERSE_N if size_n is None else size_n
    all_dates = list(pv['close'].index)
    all_codes = list(pv['close'].columns)
    code_pos = {c: i for i, c in enumerate(all_codes)}

    mask = np.zeros((len(all_dates), len(all_codes)), dtype=bool)
    src = np.full(len(all_dates), 'DYNAMIC', dtype=object)

    # ---- 基础池：非 ST（EXCLUDE_ST）----
    sb = data.stock_basic()
    nm = dict(zip(sb['ts_code'].astype(str), sb['name'].astype(str)))
    base_ok = np.ones(len(all_codes), dtype=bool)
    if C.EXCLUDE_ST:
        for c, i in code_pos.items():
            if 'ST' in nm.get(c, ''):
                base_ok[i] = False

    # ---- ① 官方快照 as-of ----
    snaps = data.member_snapshot_map()
    if snaps:
        from bisect import bisect_right
        snap_dates = sorted(snaps)
        for di, d in enumerate(all_dates):
            k = bisect_right(snap_dates, d) - 1
            if k < 0:
                continue
            for cc in snaps[snap_dates[k]]:
                j = code_pos.get(cc)
                if j is not None and base_ok[j]:
                    mask[di, j] = True
            src[di] = 'OFFICIAL'

    # ---- ② 动态小盘池（补齐官方快照缺失的日期；official_only=True 时跳过） ----
    need_dyn = np.where(src == 'DYNAMIC')[0] if not official_only else np.array([], dtype=int)
    if len(need_dyn):
        mv_w = pv['total_mv'].reindex(index=all_dates, columns=all_codes)
        mv_arr = mv_w.values
        for di in need_dyn:
            row = mv_arr[di]
            valid = np.isfinite(row) & base_ok
            idxs = np.where(valid)[0]
            if len(idxs) <= skip:
                continue
            order = idxs[np.argsort(row[idxs], kind='mergesort')]
            take = order[skip:skip + n_take]
            mask[di, take] = True
        del mv_w, mv_arr
        gc.collect()

    return (pd.DataFrame(mask, index=all_dates, columns=all_codes),
            pd.Series(src, index=all_dates))


def breadth_features(pv: dict, universe: pd.DataFrame, src: pd.Series,
                     ma_windows=None, new_high_window: int = None,
                     pivot_ma: int = 20) -> pd.DataFrame:
    """§6/§7 宽度：breadth_ma5/20/60/120 + 扩散指标

    pivot_ma 为中枢窗口（默认 20）。§25 扰动为 15/25 时，`breadth_ma20` 列名不变、
    口径换成 pivot 窗口，其余窗口仍按真实窗口命名。
    """
    ma_windows = ma_windows or C.BREADTH_MA_WINDOWS
    wins = sorted((set(ma_windows) | {pivot_ma}) - ({20} if pivot_ma != 20 else set()))
    nhw = new_high_window or C.NEW_HIGH_WINDOW
    idx_dates = universe.index
    codes = universe.columns
    out = pd.DataFrame(index=idx_dates)
    umask = universe.values
    out['universe_n'] = umask.sum(axis=1)
    out['universe_src'] = src.reindex(idx_dates).values

    close_w = pv['close'].reindex(index=idx_dates, columns=codes)
    pct_w = pv['pct_chg'].reindex(index=idx_dates, columns=codes)
    cw = close_w.values

    above20 = None
    for n in wins:
        ma = _rolling_mean(close_w, n).values
        a = (cw > ma) & umask & np.isfinite(ma)
        denom = (umask & np.isfinite(ma)).sum(axis=1)
        col = 'breadth_ma20' if n == pivot_ma else f'breadth_ma{n}'
        out[col] = np.where(denom > 0, a.sum(axis=1) / np.maximum(denom, 1), np.nan)
        if n == pivot_ma:
            above20 = a
        del ma, a
        gc.collect()
    for n in C.BREADTH_CHANGE_WINDOWS:
        out[f'breadth_change_{n}'] = (out['breadth_ma20'] - out['breadth_ma20'].shift(n)) * 100

    # 上涨/下跌家数比
    valid_pct = umask & np.isfinite(pct_w.values)
    up = valid_pct & (pct_w.values > 0)
    dn = valid_pct & (pct_w.values < 0)
    denom = valid_pct.sum(axis=1)
    out['adv_ratio'] = np.where(dn.sum(axis=1) > 0,
                                up.sum(axis=1) / np.maximum(dn.sum(axis=1), 1), np.nan)
    out['up_ratio'] = np.where(denom > 0, up.sum(axis=1) / np.maximum(denom, 1), np.nan)
    if above20 is not None:
        out['up_ma20_ratio'] = np.where(denom > 0,
                                        (up & above20).sum(axis=1) / np.maximum(denom, 1), np.nan)
    else:
        out['up_ma20_ratio'] = np.nan

    # 20日新高 / 新低
    high_w = pv['high'].reindex(index=idx_dates, columns=codes)
    low_w = pv['low'].reindex(index=idx_dates, columns=codes)
    hh = high_w.rolling(nhw, min_periods=nhw).max().values
    ll = low_w.rolling(nhw, min_periods=nhw).min().values
    nh = umask & np.isfinite(hh) & (cw >= hh * 0.999)
    nl = umask & np.isfinite(ll) & (cw <= ll * 1.001)
    dnh = (umask & np.isfinite(hh)).sum(axis=1)
    dnl = (umask & np.isfinite(ll)).sum(axis=1)
    out['new_high_ratio'] = np.where(dnh > 0, nh.sum(axis=1) / np.maximum(dnh, 1), np.nan)
    out['new_low_ratio'] = np.where(dnl > 0, nl.sum(axis=1) / np.maximum(dnl, 1), np.nan)
    out['breadth_pressure'] = out['new_high_ratio'] - out['new_low_ratio']

    del close_w, pct_w, high_w, low_w, hh, ll, cw, above20
    gc.collect()
    return out


# =========================================================
# §8 Relative
# =========================================================
def relative_features(data: GateData, base_close: pd.Series,
                      rs_windows=None, rs_main: int = 20) -> pd.DataFrame:
    """§8 相对强度

    rs_main 为主 RS 窗口（默认 20）。§25 扰动为 10/30 时，rs20_* 列名不变、
    口径换成该窗口，便于状态机在邻近参数下复测。
    """
    rs_windows = tuple(sorted(set(rs_windows or C.RS_WINDOWS) | {rs_main}))
    out = pd.DataFrame(index=base_close.index)
    comp_ret = {}
    for code, name in C.COMPARATORS.items():
        cs = data.index_close(code)
        if cs.empty:
            continue
        cs = cs.reindex(base_close.index).ffill()
        comp_ret[name] = cs
    for n in rs_windows:
        br = base_close / base_close.shift(n) - 1
        for name, cs in comp_ret.items():
            out[f'rs{n}_vs_{name}'] = (br - (cs / cs.shift(n) - 1)) * 100
    suff = f'rs{rs_main}_vs_'
    out['rs20_vs_all'] = out.get(suff + '全A', np.nan)
    out['rs20_vs_1000'] = out.get(suff + '中证1000', np.nan)
    out['rs20_vs_500'] = out.get(suff + '中证500', np.nan)
    out['rs20_vs_300'] = out.get(suff + '沪深300', np.nan)
    out['rs20_change_5'] = out['rs20_vs_all'] - out['rs20_vs_all'].shift(5)
    out['rs20_change_20'] = out['rs20_vs_all'] - out['rs20_vs_all'].shift(20)
    return out


# =========================================================
# §9 Volume
# =========================================================
def volume_features(idx: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=idx.index)
    vol = idx['vol'].astype('float64')
    amt = idx['amount'].astype('float64')
    close = idx['close'].astype('float64')
    for n in C.VOL_RATIO_WINDOWS:
        out[f'volume_ratio_{n}'] = vol / vol.rolling(n, min_periods=n).mean()
    out['turnover_ratio_20'] = amt / amt.rolling(20, min_periods=20).mean()   # 指数无换手率，用成交额比代理
    out['vol_ma5'] = vol.rolling(5, min_periods=5).mean()
    out['ret1'] = close.pct_change() * 100
    out['ret5'] = (close / close.shift(5) - 1) * 100
    out['ret20'] = (close / close.shift(20) - 1) * 100
    out['ret60'] = (close / close.shift(60) - 1) * 100

    price_up = (close > close.shift(5))
    vol_up = (out['vol_ma5'] > out['vol_ma5'].shift(5))
    st = np.where(price_up & vol_up, 'A',
                  np.where(price_up & ~vol_up, 'B',
                           np.where(~price_up & ~vol_up, 'C', 'D')))
    out['price_vol_state'] = st
    out.loc[out['vol_ma5'].isna() | out['vol_ma5'].shift(5).isna(), 'price_vol_state'] = None
    lab = {'A': '价格↑+量↑(趋势扩张)', 'B': '价格↑+量↓(健康趋势/惜售)',
           'C': '价格↓+量↓(正常调整)', 'D': '价格↓+量↑(风险释放)'}
    out['price_vol_label'] = out['price_vol_state'].map(lab)
    return out


# =========================================================
# §10 Risk-Off
# =========================================================
def riskoff_features(idx: pd.DataFrame, tr: pd.DataFrame,
                     br: pd.DataFrame, rs: pd.DataFrame,
                     vol_ratio_th: float = None) -> pd.DataFrame:
    vol_th = C.R2_VOL_RATIO if vol_ratio_th is None else vol_ratio_th
    close = idx['close'].astype('float64')
    ma20 = tr['ma20']
    out = pd.DataFrame(index=idx.index)
    out['R1_close_below_ma20_2d'] = (close < ma20) & (close.shift(1) < ma20.shift(1))
    out['R2_below_ma20_volume'] = (close < ma20) & \
        (idx['vol'] / idx['vol'].rolling(20, min_periods=20).mean() > vol_th)
    out['R3_drop5'] = ((close / close.shift(5) - 1) * 100 <= C.R3_DROP5)
    out['R4_drop20'] = ((close / close.shift(20) - 1) * 100 <= C.R4_DROP20)
    out['R5_breadth_drop'] = (br['breadth_change_5'] <= -C.R5_BREADTH_DROP)
    out['R6_newlow_gt_newhigh'] = (br['new_low_ratio'] > br['new_high_ratio'])
    out['R7_rs20_deteriorate'] = (rs['rs20_change_5'] <= C.R7_RS20_DETERIORATION)
    evt_cols = [c for c in out.columns if c[:2] in C.RISK_OFF_EVENTS]
    for c in evt_cols:
        out[c] = out[c].fillna(False).astype(bool)
    out['riskoff_count'] = out[evt_cols].sum(axis=1).astype(int)
    return out


# =========================================================
# §17/§18 假强 / 假弱
# =========================================================
def divergence_features(idx: pd.DataFrame, br: pd.DataFrame, rs: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=idx.index)
    ret1 = idx['close'].pct_change() * 100
    b_dn = br['breadth_ma20'] < br['breadth_ma20'].shift(5)
    nl_up = br['new_low_ratio'] > br['new_low_ratio'].shift(5)
    rs_dn = rs['rs20_vs_all'] < rs['rs20_vs_all'].shift(5)
    b_up = br['breadth_ma20'] > br['breadth_ma20'].shift(5)
    nh_up = br['new_high_ratio'] > br['new_high_ratio'].shift(5)
    rs_up = rs['rs20_vs_all'] > rs['rs20_vs_all'].shift(5)
    vol_dn = idx['vol'].rolling(5).mean() < idx['vol'].rolling(5).mean().shift(5)

    out['div_warn_count'] = (b_dn.fillna(False).astype(int) + nl_up.fillna(False).astype(int)
                             + rs_dn.fillna(False).astype(int))
    out['INDEX_BREADTH_DIVERGENCE'] = (ret1 > 0) & (out['div_warn_count'] >= 2)
    out['hp_ok_count'] = (b_up.fillna(False).astype(int) + nh_up.fillna(False).astype(int)
                          + rs_up.fillna(False).astype(int) + vol_dn.fillna(False).astype(int))
    out['HEALTHY_PULLBACK'] = (ret1 < 0) & (out['hp_ok_count'] >= 3)
    return out


# =========================================================
# 工具：剥离 Gate 派生列（消融 / 阶梯对照 / 分档统计必须从原始特征重建）
# =========================================================
GATE_COLS = ('regime', 'regime_reason', 'position', 'raw_target', 'band_min', 'band_target',
             'band_max', 'cap', 'quality', 'full_exposure_eligible', 'daily_cap_applied',
             'all_ok', 'position_min', 'position_target', 'position_max', 'reason')


def as_raw_feat(df: pd.DataFrame) -> pd.DataFrame:
    """返回仅含原始特征的副本

    若直接对 run_gate 的输出再次调用 run_gate，concat 去重会保留旧 regime/position，
    使消融与阶梯对照结果失真——故统一在此剥离 Gate 派生列。
    """
    drop = [c for c in list(GATE_COLS) + [f'c{i}' for i in range(1, 9)] if c in df.columns]
    return df.drop(columns=drop)


# =========================================================
# 汇总入口
# =========================================================
def build_all(data: GateData = None, start: str = C.DATA_START, end: str = C.DATA_END,
              official_only: bool = False, size_skip: int = None, size_n: int = None,
              pivot_ma: int = 20, rs_windows=None, rs_main: int = None,
              verbose: bool = True, pv: dict = None, uni: pd.DataFrame = None,
              src: pd.Series = None):
    """产出 (feat, parts)；feat 为全部字段合并的宽表

    pivot_ma / rs_main 用于 §25 参数稳定性扰动。
    pv / uni / src 可由调用方预加载并复用（§25 需跑多组变体，避免重复构建 universe）。
    指数类特征借用输出窗口之前的指数历史预热（个人日线无预热可用）。
    """
    data = data or GateData()
    rs_main = C.RS_MAIN if rs_main is None else rs_main
    if pv is None:
        pv = load_pivots(data, start, end, verbose=verbose)
    idx_all = data.index_ohlc(C.CORE_INDEX)
    idx_win = idx_all[(idx_all.index >= start) & (idx_all.index <= end)]
    if idx_win.empty:
        raise RuntimeError(f'{C.CORE_INDEX} 无数据（{start}~{end}）')
    pos0 = idx_all.index.get_loc(idx_win.index[0])
    idx = idx_all.iloc[max(0, int(pos0) - int(C.WARMUP_DAYS)):]
    idx = idx[idx.index <= end]

    ma_windows = tuple(sorted({pivot_ma} | {5, 10, 60, 120}))
    if uni is None:
        uni, src = build_universe(pv, data, official_only=official_only,
                                  size_skip=size_skip, size_n=size_n)
    common = sorted(set(uni.index) & set(idx.index))
    uni = uni.loc[common]
    src = src.loc[common]
    if verbose:
        print(f'[features] universe：OFFICIAL {int((src == "OFFICIAL").sum())} 天 / '
              f'DYNAMIC {int((src == "DYNAMIC").sum())} 天，可用 {len(common)} 天；'
              f'指数预热 {len(idx) - len(common)} 天', flush=True)

    tr = trend_features(idx, ma_windows=ma_windows, pivot_ma=pivot_ma)
    br = breadth_features(pv, uni, src, pivot_ma=pivot_ma)
    rs = relative_features(data, idx['close'], rs_windows=rs_windows, rs_main=rs_main)
    vol = volume_features(idx)
    ro = riskoff_features(idx, tr, br, rs)
    dv = divergence_features(idx, br, rs)

    feat = pd.concat([tr,
                      vol[['volume_ratio_5', 'volume_ratio_20', 'turnover_ratio_20',
                           'ret1', 'ret5', 'ret20', 'ret60', 'price_vol_state', 'price_vol_label']],
                      rs, br, ro, dv], axis=1)
    feat = feat[(feat.index >= start) & (feat.index <= end)]     # 裁掉预热段
    feat.index.name = 'trade_date'
    parts = {'trend': tr.loc[feat.index], 'volume': vol.loc[feat.index],
             'relative': rs.loc[feat.index], 'breadth': br.loc[feat.index],
             'riskoff': ro.loc[feat.index], 'divergence': dv.loc[feat.index]}
    del uni
    gc.collect()
    return feat, parts


if __name__ == '__main__':
    f, _ = build_all()
    pd.set_option('display.width', 250)
    print(f.shape)
    print(f.tail(2).T.to_string())
