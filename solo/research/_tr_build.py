# -*- coding: utf-8 -*-
"""首板 → 第一次分歧 → 缩量止跌 → 再启动 研究：面板装配 + 事件链矩阵构建

输出
  research/out/tr_mats.npz   事件窗口矩阵（KMAX+1 列）+ 一维键
  research/out/tr_base.parquet  每个事件一行的 T0 特征 / 维度标签

纪律
  * 只读 panel.parquet / first_board_events.parquet / index.parquet + daily_basic_cache
  * 不修改任何现有模块
  * 所有派生量只用「锚点日及之前」信息（矩阵自带，指标计算在实验层做）
  * 不预设任何阈值
"""
import os
import sqlite3
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')
DB = r"D:\mystock\cache_daily\stock_data.db"
KMAX = 25               # 观察窗口 T0 ~ T+25
BENCH = '000852.SH'     # 基准指数（中证1000，小盘弹性代表）

PCOLS = ['ts_code', 'trade_date', 'seq', 'board',
         'a_open', 'a_high', 'a_low', 'a_close',
         'open', 'high', 'low', 'close', 'pre_close', 'adjf',
         'vol', 'amount', 'limit_price', 'limit_pct',
         'is_limit_up', 'one_word', 'opened_board', 'touch_limit', 'broken_limit',
         'ma5', 'ma10', 'ma20', 'ma60', 'vol_ma5', 'vol_ma20']


# ────────────────────────── Regime（复用 _analyze_tf.py::build_regime 口径） ──────────────────────────
def build_regime():
    conn = sqlite3.connect(DB, timeout=30.0)
    idx = pd.read_sql_query(
        "SELECT trade_date, close FROM index_daily_cache WHERE ts_code='000001.SH' "
        "ORDER BY trade_date", conn)
    conn.close()
    idx['trade_date'] = idx['trade_date'].astype(str)
    c = idx['close'].astype(np.float64)
    ma20, ma60 = c.rolling(20).mean(), c.rolling(60).mean()
    ret20 = c / c.shift(20) - 1.0
    strong = ((c > ma20) & (ma20 > ma60) & (ret20 > 0.03)).values
    bear = ((c < ma20) & (c < ma60) & (ret20 < -0.05)).values
    weak = ((c < ma20 * 0.99) | (ret20 < -0.02)).values
    reg = np.full(len(idx), 'neutral', dtype=object)
    reg[weak] = 'weak'
    reg[bear] = 'bear'
    reg[strong] = 'strong'
    reg[:70] = 'neutral'
    return dict(zip(idx['trade_date'], reg))


def load_basic_codes():
    conn = sqlite3.connect(DB, timeout=60.0)
    x = pd.read_sql_query(
        "SELECT ts_code, trade_date, total_mv, circ_mv, turnover_rate FROM daily_basic_cache",
        conn)
    conn.close()
    x['trade_date'] = x['trade_date'].astype(np.int64)
    return x


def main():
    print('读取 panel ...')
    panel = pd.read_parquet(os.path.join(OUT, 'panel.parquet'), columns=PCOLS)
    panel = panel.sort_values(['ts_code', 'trade_date'], kind='mergesort').reset_index(drop=True)
    print('  行数', len(panel), '股票', panel['ts_code'].nunique())

    codes, cid = np.unique(panel['ts_code'].astype(str).values, return_inverse=True)
    cid = cid.astype(np.int64)
    dt = panel['trade_date'].values.astype(np.int64)
    key = cid * 100000000 + dt
    assert np.all(np.diff(key) > 0), 'key 非严格递增'

    fb = pd.read_parquet(os.path.join(OUT, 'first_board_events.parquet'))
    n = len(fb)
    print('首板事件', n)
    e_cid = pd.Index(codes).get_indexer(fb['ts_code'].astype(str).values)
    assert (e_cid >= 0).all(), '存在未匹配代码'
    ekey = e_cid * 100000000 + fb['trade_date'].values.astype(np.int64)
    pos = np.searchsorted(key, ekey)
    ok = (pos < len(key)) & (key[np.minimum(pos, len(key) - 1)] == ekey)
    print('  锚点定位成功', int(ok.sum()), '/', n)
    fb = fb[ok].reset_index(drop=True)
    e_cid = e_cid[ok]
    pos = pos[ok]
    n = len(fb)

    # ── 窗口行索引
    off = np.arange(KMAX + 1)
    rows = pos[:, None] + off[None, :]
    inb = rows < len(key)
    rr = np.clip(rows, 0, len(key) - 1)
    same = inb & (cid[rr] == e_cid[:, None])
    rows = np.where(same, rows, 0)

    def take2(colname, dtype=np.float32):
        idx = rows
        v = panel[colname].values
        a = v[idx].astype(dtype)
        return np.where(same, a, np.nan).astype(dtype)

    cl = take2('a_close'); hi = take2('a_high'); lo = take2('a_low'); op = take2('a_open')
    vo = take2('vol'); amt = take2('amount')
    ama5 = take2('ma5'); ama10 = take2('ma10'); ama20 = take2('ma20'); ama60 = take2('ma60')
    vma5 = take2('vol_ma5'); vma20 = take2('vol_ma20')
    lim = take2('limit_price'); ow = take2('one_word'); isl = take2('is_limit_up')
    dtt = np.where(same, dt[rows], -1).astype(np.int64)

    # 停牌/交易日断裂：相邻窗口日期间隔 > 10 自然日（用真实日历日，不用 YYYYMMDD 差值）
    pday = pd.to_datetime(panel['trade_date'].astype(str), format='%Y%m%d') \
        .values.astype('datetime64[D]').astype(np.int64)
    dnum = np.where(same, pday[rows], -1).astype(np.int64)
    gap = np.full((n, KMAX + 1), np.nan, dtype=np.float32)
    with np.errstate(all='ignore'):
        gap[:, 1:] = np.where((dnum[:, 1:] >= 0) & (dnum[:, :-1] >= 0),
                              dnum[:, 1:] - dnum[:, :-1], np.nan)
    clean = ~(np.nan_to_num(gap, nan=0.0) > 10).any(axis=1)

    # ── 基准指数
    ix = pd.read_parquet(os.path.join(OUT, 'index.parquet'))
    ix = ix[ix['ts_code'] == BENCH].sort_values('trade_date')
    idate = ix['trade_date'].values.astype(np.int64)
    iclo = ix['close'].values.astype(np.float64)
    p2 = np.searchsorted(idate, np.where(dtt > 0, dtt, 0).ravel())
    p2 = np.clip(p2, 0, len(idate) - 1)
    idxc = np.where((dtt > 0).ravel() & (idate[p2] == np.where(dtt > 0, dtt, 0).ravel()),
                    iclo[p2], np.nan).reshape(n, KMAX + 1).astype(np.float32)

    # ── 衍生矩阵
    with np.errstate(all='ignore'):
        ret = cl / cl[:, :1] - 1.0
        runmax = np.fmax.accumulate(hi, axis=1)
        dd = cl / runmax - 1.0
        ddl = lo / runmax - 1.0
        ddrun = np.fmin.accumulate(dd, axis=1)
        rng = hi - lo
        cp = np.where(rng > 0, (cl - lo) / rng, np.nan)
        vr = vo / vma20
        vov0 = vo / vo[:, :1]
        vov5 = vo / vma5
        dma5 = cl / ama5 - 1.0
        dma10 = cl / ama10 - 1.0
        dma20 = cl / ama20 - 1.0
        dma60 = cl / ama60 - 1.0
        ma5slope = np.full((n, KMAX + 1), np.nan, dtype=np.float32)
        ma5slope[:, 1:] = (ama5[:, 1:] / ama5[:, :-1] - 1.0)
        dayret = np.full((n, KMAX + 1), np.nan, dtype=np.float32)
        dayret[:, 1:] = (cl[:, 1:] / cl[:, :-1] - 1.0)
        idayret = np.full((n, KMAX + 1), np.nan, dtype=np.float32)
        idayret[:, 1:] = (idxc[:, 1:] / idxc[:, :-1] - 1.0)
        rs = dayret - idayret
        idxret = idxc / idxc[:, :1] - 1.0
        dvol = np.where(dayret < 0, vo, 0.0)
        uvol = np.where(dayret > 0, vo, 0.0)

    # ── daily_basic：换手 / 市值（T0 与窗口内逐日）
    print('读取 daily_basic ...')
    db = load_basic_codes()
    db_cid = pd.Index(codes).get_indexer(db['ts_code'].astype(str).values)
    db = db[db_cid >= 0].copy()
    db_cid = db_cid[db_cid >= 0]
    db['_k'] = db_cid.astype(np.int64) * 100000000 + db['trade_date'].values
    db = db.sort_values('_k')
    bk = db['_k'].values
    q = np.searchsorted(bk, np.where(same, key[rows], 0).ravel())
    q = np.clip(q, 0, len(bk) - 1)
    hit = (bk[q] == np.where(same, key[rows], 0).ravel()).reshape(n, KMAX + 1)
    for c in ('turnover_rate', 'total_mv', 'circ_mv'):
        v = db[c].values.astype(np.float64)[q].reshape(n, KMAX + 1)
        globals()['to_' + c] = np.where(hit, v, np.nan).astype(np.float32)
    print('  换手覆盖', round(float(hit.mean()), 3))

    # ── 首板日关键价位（前复权）
    C0 = cl[:, 0].astype(np.float64)
    H0 = hi[:, 0].astype(np.float64)
    L0 = lo[:, 0].astype(np.float64)
    O0 = op[:, 0].astype(np.float64)
    V0 = vo[:, 0].astype(np.float64)
    PC0 = (panel['pre_close'].values[pos].astype(np.float64)
           * panel['adjf'].values[pos].astype(np.float64))
    PC0 = np.where(np.isfinite(PC0) & (PC0 > 0), PC0, np.nan)

    # ── 落盘
    mats = dict(cl=cl, hi=hi, lo=lo, op=op, vo=vo, amt=amt,
                ama5=ama5, ama10=ama10, ama20=ama20, ama60=ama60,
                vma5=vma5, vma20=vma20, lim=lim, ow=ow, isl=isl,
                dt=dtt, gap=gap, idxc=idxc, ret=ret, dd=dd, ddl=ddl, cp=cp,
                vr=vr, vov0=vov0, vov5=vov5,
                dma5=dma5, dma10=dma10, dma20=dma20, dma60=dma60,
                ma5slope=ma5slope, dayret=dayret, idxret=idxret, rs=rs,
                runmax=runmax, ddrun=ddrun, dvol=dvol, uvol=uvol,
                to_rate=to_turnover_rate, to_mv=to_total_mv, to_cmv=to_circ_mv,
                C0=C0.astype(np.float32), H0=H0.astype(np.float32),
                L0=L0.astype(np.float32), O0=O0.astype(np.float32),
                V0=V0.astype(np.float32), PC0=PC0.astype(np.float32))
    p = os.path.join(OUT, 'tr_mats.npz')
    np.savez_compressed(p, **mats)
    print('已写', p, round(os.path.getsize(p) / 1e6, 1), 'MB')

    # ── 事件基础表
    reg = build_regime()
    base = fb[['ts_code', 'name', 'industry', 'list_date', 'trade_date', 'board', 'is_bse',
               'pct_chg', 'amplitude', 'body', 'upper_shadow', 'lower_shadow',
               't0_vr20', 't0_vs_ma20', 't0_vs_ma60', 't0_amount', 't0_ret20prev',
               't0_ret5prev', 't0_quality', 'one_word', 'opened_board', 'limit_pct',
               'ma5', 'ma10', 'ma20', 'ma60', 'vol_ma20']].copy()
    base['cid'] = e_cid
    base['clean'] = clean
    base['pt0'] = dtt[:, 0]
    base['pt_end'] = dtt[:, -1]
    base['regime'] = base['trade_date'].astype(str).map(reg).fillna('neutral')
    base['yr'] = (base['trade_date'] // 10000).astype(int)
    # 上市至 T0 的交易日数
    tds = np.sort(panel['trade_date'].unique())
    ld = pd.to_numeric(base['list_date'].astype(str).str[:8], errors='coerce').fillna(20210101).values.astype(np.int64)
    base['listed_days'] = np.searchsorted(tds, base['trade_date'].values) - np.searchsorted(tds, ld)
    base['turnover_t0'] = to_turnover_rate[:, 0]
    base['total_mv_t0'] = to_total_mv[:, 0]
    base['circ_mv_t0'] = to_circ_mv[:, 0]
    # 市值 / 换手 截面分位（按 T0 日）
    base['mv_pct'] = base.groupby('trade_date')['total_mv_t0'].rank(pct=True)
    base['to_pct'] = base.groupby('trade_date')['turnover_t0'].rank(pct=True)
    base['mv_grp'] = pd.cut(base['mv_pct'], [0, 1 / 3, 2 / 3, 1.0],
                            labels=['Small', 'Mid', 'Large'], include_lowest=True).astype(object)
    base['to_grp'] = pd.cut(base['to_pct'], [0, 1 / 3, 2 / 3, 1.0],
                            labels=['Low', 'Mid', 'High'], include_lowest=True).astype(object)
    base['event_id'] = [f"FB{i:06d}" for i in range(len(base))]
    bp = os.path.join(OUT, 'tr_base.parquet')
    base.to_parquet(bp, index=False)
    print('已写', bp, len(base), '行')

    # ── 体检
    print('\n=== 体检 ===')
    print('事件数', n, '| 窗口完整(未截断)', int((same.sum(1) == KMAX + 1).sum()),
          '| 交易连续(无停牌跨期)', int(clean.sum()))
    print('上市不足60交易日', int((base['listed_days'] < 60).sum()))
    print('按年:\n', base['yr'].value_counts().sort_index().to_string())
    print('板块:\n', base['board'].value_counts().to_string())
    print('Regime(T0):\n', base['regime'].value_counts().to_string())
    print('首板质量:\n', base['t0_quality'].value_counts().to_string())
    print('市值档:\n', base['mv_grp'].value_counts(dropna=False).to_string())
    print('换手/市值覆盖（按年）:',
          base.groupby('yr')['total_mv_t0'].apply(lambda s: round(float(s.notna().mean()), 3)).to_dict())
    print('基准指数', BENCH, '覆盖', round(float(np.isfinite(idxc[:, 0]).mean()), 3))
    print('T+3/T+5/T+10 可计算样本：',
          {k: int(np.isfinite(cl[:, k]).sum()) for k in (3, 5, 10)})


if __name__ == '__main__':
    main()
