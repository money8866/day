# -*- coding: utf-8 -*-
"""
HVE-Research V1  Step 1
构建无未来函数的事件研究面板

数据源: D:\mystock\cache_daily\stock_data.db
  daily_cache       OHLC / pre_close / pct_chg / vol / amount
  daily_basic_cache turnover_rate / total_mv / circ_mv   (仅 2023-01 起)
  index_daily_cache 指数日线

关键设计（禁止未来函数）:
  1. 复权: tushare daily 的 pre_close 已做除权调整 -> pct_chg 为全收益日收益率。
     以 close[0] 为基准链式累乘得到后复权总价指数 adj_close，
     factor = adj_close / close 同比例缩放 open/high/low。
  2. 所有 rolling 特征仅使用当日及之前的数据；VR 类指标用"前 N 日均值"作分母；
     分位数用"过去 250 个交易日窗口(含当日)"内的秩。
  3. 前 N 日最高价 hhN 使用 shift(1) 的滚动最大值，不含当日。
"""
import os
import sqlite3
import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

DB = r'D:\mystock\cache_daily\stock_data.db'
OUT = r'D:\mystock\hve_research\data'
os.makedirs(OUT, exist_ok=True)

BASIC_CSV = r'D:\mystock\cache_daily\stock_basic.csv'


def board_of(code: str) -> str:
    """板块划分: MAIN 主板 / GEM 创业板 / STAR 科创板 / BJ 北交所"""
    if code.endswith('.BJ'):
        return 'BJ'
    p = code[:3]
    if code.endswith('.SH'):
        return 'STAR' if p.startswith('68') else 'MAIN'
    return 'GEM' if p.startswith('30') else 'MAIN'


def limit_pct(board: str) -> float:
    return {'MAIN': 10.0, 'GEM': 20.0, 'STAR': 20.0, 'BJ': 30.0}[board]


def roll_pct(x: np.ndarray, w: int = 250, minp: int = 60) -> np.ndarray:
    """窗口内秩百分位（含当日），仅使用历史数据"""
    n = len(x)
    out = np.full(n, np.nan)
    if n < minp:
        return out
    pad = np.full(w - 1, np.nan)
    xp = np.concatenate([pad, x])
    win = sliding_window_view(xp, w)          # (n, w)
    cnt = np.sum(~np.isnan(win), axis=1)
    valid = (cnt >= minp) & ~np.isnan(x)
    le = np.sum(np.where(win <= x[:, None], 1.0, 0.0), axis=1)
    out[valid] = le[valid] / cnt[valid]
    return out


def rmean_prev(x: np.ndarray, w: int) -> np.ndarray:
    """前 w 日均值（严格不含当日），NaN 感知"""
    n = len(x)
    out = np.full(n, np.nan)
    if n <= w:
        return out
    xv = np.where(np.isnan(x), 0.0, x)
    cs = np.cumsum(xv)
    cc = np.cumsum(np.where(np.isnan(x), 0.0, 1.0))
    s = cs[w - 1:-1] - np.concatenate([[0.0], cs])[:n - w]
    k = cc[w - 1:-1] - np.concatenate([[0.0], cc])[:n - w]
    with np.errstate(invalid='ignore', divide='ignore'):
        out[w:] = np.where(k > 0, s / np.maximum(k, 1e-9), np.nan)
    return out


def _win_incl(x: np.ndarray, w: int):
    """滑窗视图 (n, w)，每行 = 当日及前 w-1 日"""
    pad = np.full(w - 1, np.nan)
    xp = np.concatenate([pad, x])
    return sliding_window_view(xp, w)


def rmax_prev(x: np.ndarray, w: int) -> np.ndarray:
    """前 w 日最大值（不含当日）"""
    n = len(x)
    out = np.full(n, np.nan)
    if n <= w:
        return out
    win = _win_incl(x, w)                # 行 t 含 [t-w+1 .. t]
    m = np.where(np.isnan(win), -np.inf, win).max(axis=1)
    out[w:] = m[w - 1:-1]                # 行 t-1 的窗口 = [t-w .. t-1]
    return out


def rmin_prev(x: np.ndarray, w: int) -> np.ndarray:
    n = len(x)
    out = np.full(n, np.nan)
    if n <= w:
        return out
    win = _win_incl(x, w)
    m = np.where(np.isnan(win), np.inf, win).min(axis=1)
    out[w:] = m[w - 1:-1]
    return out


def rmean_incl(x: np.ndarray, w: int) -> np.ndarray:
    n = len(x)
    out = np.full(n, np.nan)
    if n < w:
        return out
    cs = np.cumsum(np.where(np.isnan(x), 0.0, x))
    cnt = np.cumsum(np.where(np.isnan(x), 0.0, 1.0))
    out[w - 1:] = (cs[w - 1:] - np.concatenate([[0.0], cs[:n - w + 1 - 1]])) / \
                  (cnt[w - 1:] - np.concatenate([[0.0], cnt[:n - w + 1 - 1]]))
    return out


def rstd_incl(x: np.ndarray, w: int) -> np.ndarray:
    n = len(x)
    out = np.full(n, np.nan)
    if n < w:
        return out
    xz = np.where(np.isnan(x), 0.0, x)
    c1 = np.cumsum(xz)
    c2 = np.cumsum(xz * xz)
    s1 = c1[w - 1:] - np.concatenate([[0.0], c1[:n - w]])
    s2 = c2[w - 1:] - np.concatenate([[0.0], c2[:n - w]])
    m = s1 / w
    var = np.maximum(s2 / w - m * m, 0.0) * w / (w - 1)
    out[w - 1:] = np.sqrt(var)
    return out


def compute_stock(g: pd.DataFrame) -> pd.DataFrame:
    """单只股票: 计算全部后视特征"""
    n = len(g)
    close_raw = g['close'].values.astype(np.float64)
    high_raw = g['high'].values.astype(np.float64)
    low_raw = g['low'].values.astype(np.float64)
    open_raw = g['open'].values.astype(np.float64)
    vol = g['vol'].values.astype(np.float64)
    amt = g['amount'].values.astype(np.float64)
    pct = g['pct_chg'].values.astype(np.float64)

    ret = pct / 100.0
    # 链式复权总价指数
    cum = np.cumprod(1.0 + ret)
    adj_close = close_raw[0] * cum / (1.0 + ret[0])
    factor = adj_close / np.where(close_raw > 0, close_raw, np.nan)
    adj_high = high_raw * factor
    adj_low = low_raw * factor
    adj_open = open_raw * factor

    ma5 = rmean_incl(adj_close, 5)
    ma10 = rmean_incl(adj_close, 10)
    ma20 = rmean_incl(adj_close, 20)
    ma60 = rmean_incl(adj_close, 60)

    vr5 = vol / rmean_prev(vol, 5)
    vr10 = vol / rmean_prev(vol, 10)
    vr20 = vol / rmean_prev(vol, 20)
    vr60 = vol / rmean_prev(vol, 60)
    amt_r20 = amt / rmean_prev(amt, 20)

    vma5 = rmean_incl(vol, 5)
    vma10 = rmean_incl(vol, 10)
    vma20 = rmean_incl(vol, 20)
    vma60 = rmean_incl(vol, 60)

    vol_pct250 = roll_pct(vol, 250, 60)
    amt_pct250 = roll_pct(amt, 250, 60)

    turn = g['turnover_rate'].values.astype(np.float64)
    turn_pct250 = roll_pct(turn, 250, 60)

    hh20 = rmax_prev(adj_high, 20)
    hh60 = rmax_prev(adj_high, 60)
    ll20 = rmin_prev(adj_low, 20)
    ll60 = rmin_prev(adj_low, 60)
    ch20 = rmax_prev(adj_close, 20)
    ch60 = rmax_prev(adj_close, 60)

    rng = adj_high - adj_low
    rng_safe = np.where(rng > 0, rng, 1.0)
    clv = np.where(rng > 0, (adj_close - adj_low) / rng_safe, 0.5)

    vol20 = rstd_incl(ret, 20) * np.sqrt(252.0)
    vol60 = rstd_incl(ret, 60) * np.sqrt(252.0)

    out = pd.DataFrame({
        'ts_code': g['ts_code'].values,
        'trade_date': g['trade_date'].values.astype(np.int32),
        'open': adj_open, 'high': adj_high, 'low': adj_low, 'close': adj_close,
        'close_raw': close_raw, 'ret': ret, 'pct_chg': pct,
        'vol': vol, 'amount': amt, 'turnover_rate': turn,
        'total_mv': g['total_mv'].values, 'circ_mv': g['circ_mv'].values,
        'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
        'vr5': vr5, 'vr10': vr10, 'vr20': vr20, 'vr60': vr60, 'amt_r20': amt_r20,
        'vma5': vma5, 'vma10': vma10, 'vma20': vma20, 'vma60': vma60,
        'vol_pct250': vol_pct250, 'amt_pct250': amt_pct250, 'turn_pct250': turn_pct250,
        'hh20': hh20, 'hh60': hh60, 'll20': ll20, 'll60': ll60, 'ch20': ch20, 'ch60': ch60,
        'clv': clv, 'vol20': vol20, 'vol60': vol60,
    })
    # 序列位置（该股自身交易日序号），用于前瞻/回看对齐
    out['seq'] = np.arange(n, dtype=np.int32)

    # 动量 / 距离
    c = pd.Series(adj_close)
    out['ret5'] = (c / c.shift(5) - 1).values
    out['ret20'] = (c / c.shift(20) - 1).values
    out['ret60'] = (c / c.shift(60) - 1).values
    m20 = pd.Series(ma20)
    m60 = pd.Series(ma60)
    out['ma20_slope5'] = (m20 / m20.shift(5) - 1).values
    out['ma20_slope10'] = (m20 / m20.shift(10) - 1).values
    out['ma60_slope10'] = (m60 / m60.shift(10) - 1).values
    out['dist_ma20'] = (adj_close / ma20 - 1)
    out['dist_ma60'] = (adj_close / ma60 - 1)
    out['dist_hh20'] = (adj_close / hh20 - 1)
    out['dist_hh60'] = (adj_close / hh60 - 1)

    board = board_of(g['ts_code'].iloc[0])
    lim = limit_pct(board)
    out['board'] = board
    out['up_limit'] = (pct >= lim * 0.98).astype(np.int8)
    out['dn_limit'] = (pct <= -lim * 0.98).astype(np.int8)
    # 一字板: 开=高=低=收 且涨停
    out['yizi'] = ((high_raw <= low_raw * 1.0001) & (pct >= lim * 0.98)).astype(np.int8)
    return out


def read_table_seq(con, table, cols, chunk=400_000):
    """顺序全表扫描（避免大库随机 I/O），分块转 DataFrame"""
    col_sql = ','.join(cols)
    cur = con.execute(f'select {col_sql} from {table}')
    out = []
    tot = 0
    while True:
        rows = cur.fetchmany(chunk)
        if not rows:
            break
        d = pd.DataFrame(rows, columns=cols)
        for c in cols:
            if c not in ('ts_code', 'trade_date'):
                d[c] = pd.to_numeric(d[c], errors='coerce')
        out.append(d)
        tot += len(d)
        print(f'   {table} +{len(d)} (tot {tot})', flush=True)
    return pd.concat(out, ignore_index=True)


def main():
    con = sqlite3.connect(DB)
    print('reading daily_cache (sequential)...', flush=True)
    daily = read_table_seq(con, 'daily_cache',
                           ['ts_code', 'trade_date', 'open', 'high', 'low', 'close',
                            'pre_close', 'pct_chg', 'vol', 'amount'])
    print('reading daily_basic_cache (sequential)...', flush=True)
    basic = read_table_seq(con, 'daily_basic_cache',
                           ['ts_code', 'trade_date', 'turnover_rate', 'total_mv', 'circ_mv'])
    df = daily.merge(basic, on=['ts_code', 'trade_date'], how='left')
    del daily, basic
    print('merged', len(df), flush=True)
    df = df.sort_values(['ts_code', 'trade_date'], kind='mergesort').reset_index(drop=True)

    codes = sorted(df['ts_code'].unique())
    _lim = os.environ.get('HVE_LIMIT')
    if _lim:
        keep = set(codes[:int(_lim)])
        df = df[df['ts_code'].isin(keep)].copy()
        codes = codes[:int(_lim)]
    print('codes', len(codes))

    # 交易日历（以上证指数交易日为准）
    cal = [r[0] for r in con.execute(
        "select distinct trade_date from index_daily_cache where ts_code='000001.SH' order by trade_date")]
    cal = sorted(cal)
    d2i = {int(d): i for i, d in enumerate(cal)}
    print('calendar', len(cal), cal[0], cal[-1])

    parts = []
    ncode = 0
    for code, g in df.groupby('ts_code', sort=False):
        if len(g) < 5:
            continue
        try:
            parts.append(compute_stock(g))
        except Exception as e:
            print('ERR', code, e)
        ncode += 1
        if ncode % 1000 == 0:
            print('  computed', ncode, flush=True)

    panel = pd.concat(parts, ignore_index=True)
    panel['td_idx'] = panel['trade_date'].map(d2i).astype('int32')
    print('panel rows', len(panel))

    # 股票基础信息 / 过滤标记
    sb = pd.read_csv(BASIC_CSV, dtype={'ts_code': str, 'name': str})
    sb['board'] = sb['ts_code'].map(board_of)
    panel = panel.merge(sb[['ts_code', 'name', 'industry', 'list_date', 'board']],
                        on='ts_code', how='left', suffixes=('', '_sb'))
    panel['is_st'] = panel['name'].fillna('').str.contains('ST', regex=False).astype(np.int8)
    panel['is_delist'] = panel['name'].fillna('').str.contains('退', regex=False).astype(np.int8)

    # 每只股票在样本内的上市首日（DB 内首个交易日），用于观测窗口过滤
    first_day = panel.groupby('ts_code')['trade_date'].transform('min')
    panel['obs_days'] = panel.groupby('ts_code')['trade_date'].rank(method='first').astype(np.int32)
    panel['list_date'] = pd.to_numeric(panel['list_date'], errors='coerce')

    f32 = [c for c in panel.columns if panel[c].dtype == np.float64]
    for c in f32:
        panel[c] = panel[c].astype(np.float32)

    panel.to_parquet(os.path.join(OUT, 'panel.parquet'), index=False)
    print('panel saved', panel.shape)

    # ---------- 市场层面板 ----------
    idx = pd.read_sql("select * from index_daily_cache", con)
    idx['td_idx'] = idx['trade_date'].astype(int).map(d2i)
    idx = idx.sort_values(['ts_code', 'td_idx'])
    for c in ['open', 'high', 'low', 'close', 'pct_chg', 'amount']:
        idx[c] = pd.to_numeric(idx[c], errors='coerce')
    idxp = idx.pivot_table(index='td_idx', columns='ts_code',
                           values=['close', 'pct_chg', 'amount'], aggfunc='first')
    idxp.columns = [f'{a}_{b.split(".")[0]}' for a, b in idxp.columns]
    idxp = idxp.reset_index()

    mk = panel.groupby('td_idx').agg(
        n_stocks=('ret', 'size'),
        ew_ret=('ret', 'mean'),
        med_ret=('ret', 'median'),
        tot_amount=('amount', 'sum'),
    ).reset_index()
    # 宽度: close > ma20 / ma60 的比例（仅统计有 ma 的样本）
    ok = panel.dropna(subset=['ma20']).copy()
    ok['ab20'] = (ok['close'] > ok['ma20']).astype(np.float32)
    ok['ab60'] = (ok['close'] > ok['ma60']).astype(np.float32)
    br = ok.groupby('td_idx').agg(breadth20=('ab20', 'mean'), breadth60=('ab60', 'mean')).reset_index()
    mk = mk.merge(br, on='td_idx', how='left')
    # 横截面收益离散度
    mk = mk.merge(
        panel.groupby('td_idx')['ret'].std().rename('xsec_std').reset_index(),
        on='td_idx', how='left')
    market = mk.merge(idxp, on='td_idx', how='left')
    market['trade_date'] = [cal[i] for i in market['td_idx']]
    market.to_parquet(os.path.join(OUT, 'market.parquet'), index=False)
    print('market saved', market.shape)

    # 交易日历
    pd.DataFrame({'td_idx': range(len(cal)), 'trade_date': [int(x) for x in cal]}).to_parquet(
        os.path.join(OUT, 'calendar.parquet'), index=False)
    con.close()


if __name__ == '__main__':
    main()
