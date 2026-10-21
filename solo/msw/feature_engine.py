# -*- coding: utf-8 -*-
"""MSW 特征引擎（任务书 §11 feature_engine.py）

全部特征为 PIT（point-in-time）：任意一行的特征只由该行及之前的数据决定，
可通过「截断 DataFrame 重算」验证一致性（tests/test_pit.py）。

价格基准：hfq（后复权）序列列 hfq_open/hfq_high/hfq_low/hfq_close。
"""
import numpy as np
import pandas as pd


# ---------------------------------------------------------------- 单股特征

def _rolling_max(s: pd.Series, win: int) -> pd.Series:
    return s.rolling(win, min_periods=win).max()


def _rolling_min(s: pd.Series, win: int) -> pd.Series:
    return s.rolling(win, min_periods=win).min()


def compute_features(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """在单股日线（含 hfq_* 列）上计算全部趋势/波动/量价特征（纯函数，PIT）。

    输入列：trade_date, open/high/low/close, vol, amount, pct_chg, hfq_* （来自 data_adapter）
    输出：在原 DataFrame 上新增特征列并返回（不修改传入对象）。
    """
    d = df.copy()
    c = d['hfq_close']
    h = d['hfq_high']
    l = d['hfq_low']
    amt = d['amount']                      # 千元

    # ---- 均线与排列 ----
    for w in (5, 10, 20, 60, 120, 250):
        d[f'ma{w}'] = c.rolling(w, min_periods=w).mean()
    d['ma_align'] = ((d['ma20'] > d['ma60']) & (d['ma60'] > d['ma120']) & (d['ma120'] > d['ma250']))
    d['ma_align_score'] = (
        (d['ma20'] > d['ma60']).astype(float)
        + (d['ma60'] > d['ma120']).astype(float)
        + (d['ma120'] > d['ma250']).astype(float)
    ) / 3.0

    # ---- 均线斜率（20 日） ----
    for w in (60, 120, 250):
        d[f'ma{w}_slope20'] = d[f'ma{w}'] / d[f'ma{w}'].shift(20) - 1.0

    # ---- 区间收益 ----
    for w in (20, 60, 120, 250):
        d[f'ret_{w}d'] = c / c.shift(w) - 1.0
    d['daily_ret'] = c.pct_change()

    # ---- 距高点幅度 ----
    for w in (60, 120, 250):
        d[f'dist_high_{w}d'] = c / _rolling_max(h, w) - 1.0     # <=0
    d['dist_low_60d'] = c / _rolling_min(l, 60) - 1.0

    # ---- ATR20（hfq，简单均值口径）与波动率 ----
    prev_c = c.shift(1)
    tr = pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    d['atr20'] = tr.rolling(20, min_periods=20).mean()
    d['atr_pct'] = d['atr20'] / c
    d['volatility_60d'] = d['daily_ret'].rolling(60, min_periods=60).std()

    # ---- 250 日最大回撤（滚动，PIT：只用过去 250 日） ----
    roll_max = _rolling_max(c, 250)
    d['mdd_250d'] = c / roll_max - 1.0

    # ---- 量能特征 ----
    d['amount_ma20'] = amt.rolling(20, min_periods=20).mean()
    d['amount_ma5'] = amt.rolling(5, min_periods=5).mean()
    d['amount_ma10'] = amt.rolling(10, min_periods=10).mean()
    d['vr20'] = amt / d['amount_ma20']
    d['vr5'] = amt / d['amount_ma5']
    d['vol_ma20'] = d['vol'].rolling(20, min_periods=20).mean()
    d['vol_ratio'] = d['vol'] / d['vol_ma20']

    # ---- 超跌类 ----
    d['rsi6'] = _rsi(c, 6)
    d['rsi12'] = _rsi(c, 12)
    mid = c.rolling(20, min_periods=20).mean()
    sd = c.rolling(20, min_periods=20).std(ddof=0)
    d['boll_mid'] = mid
    d['boll_upper'] = mid + 2.0 * sd
    d['boll_lower'] = mid - 2.0 * sd
    rng = (d['boll_upper'] - d['boll_lower'])
    d['boll_pos'] = (c - d['boll_lower']) / rng.replace(0.0, np.nan)
    d['dev_ma20'] = c / d['ma20'] - 1.0
    d['dev_ma60'] = c / d['ma60'] - 1.0

    # ---- K线位置 ----
    hl = (h - l).replace(0.0, np.nan)
    d['clv'] = ((c - l) - (h - c)) / hl

    # ---- 近期高低点（信号引擎引用） ----
    d['high_5'] = _rolling_max(h, 5)
    d['high_10'] = _rolling_max(h, 10)
    d['high_20'] = _rolling_max(h, 20)
    d['low_5'] = _rolling_min(l, 5)
    d['low_10'] = _rolling_min(l, 10)
    d['low_20'] = _rolling_min(l, 20)

    return d


def _rsi(close: pd.Series, win: int) -> pd.Series:
    """Wilder RSI"""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    ag = gain.ewm(alpha=1.0 / win, adjust=False, min_periods=win).mean()
    al = loss.ewm(alpha=1.0 / win, adjust=False, min_periods=win).mean()
    rs = ag / al.replace(0.0, np.nan)
    return (100.0 - 100.0 / (1.0 + rs)).fillna(50.0)


# ---------------------------------------------------------------- 横截面面板

class MarketPanel:
    """全市场横截面面板（hfq 收盘矩阵 + 行业代理 + 基准收益），PIT 安全。

    用途：
      - 行业代理指数收益（个股 industry 字段等权中位数）→ 行业相对强度
      - 基准指数（000300.SH 等）累计收益 → 宽基相对强度
    构建方式：一次性从 daily_cache+adj_factor_cache 读取区间内全部 (date, code) 的
    hfq 收盘价（float32 矩阵），之后全部内存运算，无任何未来信息
    （矩阵本身不含未来，按日期对齐查询）。
    """

    def __init__(self, data, start_date: str, end_date: str, cfg: dict):
        self.cfg = cfg
        sql = (
            'SELECT d.trade_date, d.ts_code, d.close * COALESCE(a.adj_factor, 1.0) AS hfq '
            'FROM daily_cache d '
            'LEFT JOIN adj_factor_cache a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date '
            'WHERE d.trade_date>=? AND d.trade_date<=? '
            "AND (d.ts_code LIKE '%.SH' OR d.ts_code LIKE '%.SZ') "
            "AND substr(d.ts_code,1,1) NOT IN ('4','8','9')"
        )
        from msw.data_adapter import norm_date
        import sqlite3
        with sqlite3.connect(f'file:{data.db_path}?mode=ro', uri=True, timeout=60.0) as conn:
            raw = pd.read_sql(sql, conn, params=(norm_date(start_date), norm_date(end_date)))
        raw['trade_date'] = raw['trade_date'].astype(str)
        raw['hfq'] = pd.to_numeric(raw['hfq'], errors='coerce')
        self.codes = sorted(raw['ts_code'].unique().tolist())
        self.dates = sorted(raw['trade_date'].unique().tolist())
        mat = (raw.pivot_table(index='trade_date', columns='ts_code', values='hfq')
                  .reindex(index=self.dates, columns=self.codes)
                  .astype('float32'))
        self.close = mat
        # 日收益矩阵（hfq）
        self.ret = mat / mat.shift(1) - 1.0

        # 行业映射
        names, lds, inds = data.basic_maps()
        self.industry = pd.Series({c: inds.get(c, '未知') for c in self.codes})

        # 基准指数序列（点位，不复权）
        bm = cfg.get('trend_filter', {}).get('benchmark', '000300.SH')
        self.benchmark_code = bm
        bs = data.index_series(bm, end_date, lookback=len(self.dates) + 400,
                               start_date=start_date)
        bs = bs[bs.index.isin(self.dates)]
        self.bench_close = bs.astype('float32')
        bench_ret = (self.bench_close / self.bench_close.shift(1) - 1.0).fillna(0.0)
        self.bench_ret = bench_ret

        # 行业代理日收益（行业内个股收益中位数）
        self._ind_ret = {}
        for ind in self.industry.unique():
            cols = self.industry[self.industry == ind].index
            if len(cols) == 0:
                continue
            med = self.ret[cols].median(axis=1, skipna=True).fillna(0.0)
            self._ind_ret[ind] = med

        # 预计算滚动累计收益（log 空间，O(1) 查询）
        self.bench_cum = {w: _cum_win(bench_ret, w) for w in (60, 120, 250)}
        self.ind_cum = {}
        for ind, med in self._ind_ret.items():
            for w in (60, 120, 250):
                self.ind_cum[(ind, w)] = _cum_win(med, w)

    # ---------------- 基准相对强度 ----------------

    def benchmark_cum(self, win: int, as_of: str) -> float:
        """基准指数截至 as_of 的 win 日累计收益（as_of 当日收盘已知，PIT）"""
        s = self.bench_cum.get(win)
        if s is None or as_of not in s.index:
            return float('nan')
        return float(s.loc[as_of])

    def industry_cum(self, industry: str, win: int, as_of: str) -> float:
        """行业代理（个股收益中位数复合）截至 as_of 的 win 日累计收益"""
        s = self.ind_cum.get((industry, win))
        if s is None or as_of not in s.index:
            return float('nan')
        return float(s.loc[as_of])

    def breadth(self, as_of: str):
        """全市场上涨家数占比（hfq 收益>0）"""
        if as_of not in self.ret.index:
            return float('nan')
        row = self.ret.loc[as_of]
        row = row.dropna()
        if row.empty:
            return float('nan')
        return float((row > 0).mean())


def _cum_win(series: pd.Series, win: int) -> pd.Series:
    """滚动 win 日累计收益（log 空间差分，O(n)）；前 win-1 行为 NaN"""
    r = pd.to_numeric(series, errors='coerce').fillna(0.0).clip(lower=-0.99)
    logc = np.log1p(r.to_numpy(dtype=float)).cumsum()
    out = np.full(len(r), np.nan)
    if len(r) > win:
        out[win:] = np.exp(logc[win:] - logc[:-win]) - 1.0
    return pd.Series(out, index=series.index)


def relative_strength(stock_df: pd.DataFrame, panel: 'MarketPanel | None',
                      as_of_idx: int, cfg: dict) -> dict:
    """个股在 as_of 行的相对强度特征（rs_120d/rs_250d/industry_rs_120d）

    stock_df: compute_features 后的单股 DataFrame（含 ret_120d/ret_250d）
    as_of_idx: 行号（该行及之前的数据）
    """
    out = {'rs_120d': np.nan, 'rs_250d': np.nan, 'industry_rs_120d': np.nan}
    if panel is None:
        return out
    date = str(stock_df['trade_date'].iloc[as_of_idx])
    s120 = float(stock_df['ret_120d'].iloc[as_of_idx])
    s250 = float(stock_df['ret_250d'].iloc[as_of_idx])
    b120 = panel.benchmark_cum(120, date)
    b250 = panel.benchmark_cum(250, date)
    if np.isfinite(s120) and np.isfinite(b120):
        out['rs_120d'] = s120 - b120
    if np.isfinite(s250) and np.isfinite(b250):
        out['rs_250d'] = s250 - b250
    ind = panel.industry.get(str(stock_df['ts_code'].iloc[0]), '未知')
    i120 = panel.industry_cum(ind, 120, date)
    if np.isfinite(s120) and np.isfinite(i120):
        out['industry_rs_120d'] = s120 - i120
    return out
