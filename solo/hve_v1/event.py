# -*- coding: utf-8 -*-
"""HVE V1 事件层

§6  HVE 基础条件：
      VR20 >= vr20_min AND daily_return >= return_min AND close > open AND CLV >= clv_min
      high == low → CLV 缺失 → 不产生事件

§9  事件去重（Event Cluster）：
      同一股票两个 HVE 间隔 <= cluster_gap_days 个交易日 → 同一簇；
      primary event = 簇内**第一次**符合条件的 HVE（cluster_anchor="first"，§7.1 裁决）；
      同时保留 cluster_max_volume_date / cluster_max_volume_ratio 供研究。
      V1 不根据未来收益选择 primary event。

§18 Volume Contraction（作为事件属性预计算）：
      volume_decay_5d = MA5(HVE 后 T+1..T+5 的 volume) / HVE_volume
      仅在 T+5 之后可用（数据不足时缺失，不产生 HVE_2ND）。
"""
import numpy as np
import pandas as pd

HVE_COLS = ('ts_code', 'trade_date', 'hve_flag',
            'volume', 'vol_ma20', 'vr20',
            'amount', 'amount_ma20',
            'open', 'high', 'low', 'close',
            'daily_return', 'clv', 'ma20', 'ma60',
            'event_cluster_id', 'event_date',
            'distance_to_20d_high', 'distance_to_60d_high')


def hve_mask(d: pd.DataFrame, cfg: dict) -> np.ndarray:
    """§6 HVE 基础条件（bool 数组，长度与 d 一致）"""
    vr_min = float(cfg['vr20_min'])
    ret_min = float(cfg['return_min'])
    clv_min = float(cfg['clv_min'])

    vr = pd.to_numeric(d['vr20'], errors='coerce').to_numpy(dtype=float)
    ret = pd.to_numeric(d['daily_return'], errors='coerce').to_numpy(dtype=float)
    clv = pd.to_numeric(d['clv'], errors='coerce').to_numpy(dtype=float)
    close = pd.to_numeric(d['close'], errors='coerce').to_numpy(dtype=float)
    open_ = pd.to_numeric(d['open'], errors='coerce').to_numpy(dtype=float)

    with np.errstate(invalid='ignore'):
        return ((vr >= vr_min) & (ret >= ret_min)
                & (close > open_) & (clv >= clv_min))


def cluster_indices(hve_idx, gap: int):
    """§9 事件聚簇：相邻 HVE 间隔 <= gap 个交易日归入同一簇。返回 list[list[int]]。"""
    clusters = []
    cur = []
    for i in hve_idx:
        if cur and (i - cur[-1]) <= gap:
            cur.append(i)
        else:
            if cur:
                clusters.append(cur)
            cur = [i]
    if cur:
        clusters.append(cur)
    return clusters


def volume_decay_5d(d: pd.DataFrame, e: int, window: int = 5) -> float:
    """§18 volume_decay_5d = MA_n(HVE 后 n 日 volume) / HVE_volume（n=window，默认 5）

    不足 window 个交易日 → 缺失（NaN），此时不得产生 HVE_2ND。
    """
    n = len(d)
    if e + window >= n:
        return np.nan
    hve_vol = float(pd.to_numeric(d['vol'].iloc[e], errors='coerce'))
    post = pd.to_numeric(d['vol'].iloc[e + 1: e + 1 + window], errors='coerce')
    if not np.isfinite(hve_vol) or hve_vol <= 0 or post.isna().any() or len(post) < window:
        return np.nan
    return float(post.mean()) / hve_vol


def annotate_events(d: pd.DataFrame, ts_code: str, cfg: dict):
    """在指标 DataFrame 上标注 HVE 事件列，并返回事件簇列表。

    返回 (df, events)：
      df     追加 hve_flag / event_cluster_id / event_date / is_primary /
             cluster_max_volume_date / cluster_max_volume_ratio /
             distance_to_20d_high / distance_to_60d_high
      events 每个事件簇一条：{cluster_id, primary_idx, event_date, hve_idx,
             cluster_max_volume_idx, cluster_max_volume_date,
             cluster_max_volume_ratio, volume_decay_5d}
    """
    n = len(d)
    gap = int(cfg['cluster_gap_days'])
    window = int(cfg.get('volume_decay_window', 5))

    mask = hve_mask(d, cfg)
    d = d.copy()
    d['hve_flag'] = mask
    d['event_cluster_id'] = pd.NA
    d['event_date'] = pd.NA
    d['is_primary'] = False
    d['cluster_max_volume_date'] = pd.NA
    d['cluster_max_volume_ratio'] = np.nan

    # §10 距离前高：只用事件日「之前」的数据（high_prev20 / high_prev60）
    with np.errstate(divide='ignore', invalid='ignore'):
        d['distance_to_20d_high'] = d['close'] / d['high_prev20'] - 1.0
        d['distance_to_60d_high'] = d['close'] / d['high_prev60'] - 1.0

    events = []
    dates = d['trade_date'].astype(str).to_numpy()
    vol = pd.to_numeric(d['vol'], errors='coerce').to_numpy(dtype=float)
    hve_idx = np.where(mask)[0].tolist()

    for ci, cl in enumerate(cluster_indices(hve_idx, gap)):
        cid = f'{ts_code}_{dates[cl[0]]}'
        primary = cl[0]
        jmax = max(cl, key=lambda k: (vol[k] if np.isfinite(vol[k]) else -1.0))
        vd = volume_decay_5d(d, primary, window)

        d.loc[cl, 'event_cluster_id'] = cid
        d.loc[cl, 'event_date'] = dates[primary]
        d.loc[primary, 'is_primary'] = True
        d.loc[cl, 'cluster_max_volume_date'] = dates[jmax]
        d.loc[cl, 'cluster_max_volume_ratio'] = (
            float(vol[jmax]) / float(d['vol_ma20'].iloc[jmax])
            if np.isfinite(vol[jmax]) and np.isfinite(d['vol_ma20'].iloc[jmax])
            and d['vol_ma20'].iloc[jmax] > 0 else np.nan)

        events.append({
            'cluster_id': cid,
            'primary_idx': primary,
            'event_date': dates[primary],
            'hve_idx': cl,
            'cluster_max_volume_idx': jmax,
            'cluster_max_volume_date': dates[jmax],
            'cluster_max_volume_ratio': d['cluster_max_volume_ratio'].iloc[jmax],
            'volume_decay_5d': vd,
        })

    d['volume'] = d['vol']
    return d, events


def events_frame(d: pd.DataFrame, ts_code: str) -> pd.DataFrame:
    """§10 HVE Event 输出表（只含 hve_flag=True 的行）"""
    if 'hve_flag' not in d.columns or not bool(d['hve_flag'].any()):
        return pd.DataFrame(columns=list(HVE_COLS))
    sub = d[d['hve_flag']].copy()
    sub['ts_code'] = ts_code
    return sub[list(HVE_COLS)].reset_index(drop=True)
