# -*- coding: utf-8 -*-
"""
V11 评分模型 —— 因子 IC 分析
=========================================
目的：回答"V11 的六个维度，到底哪个真有 alpha"。

数据源：D:\\mystock\\cache_daily\\stock_data.db
  - stk_factor_pro       面板行情 + total_mv + 复权价（2023 起为全市场，2021-22 残缺故不用）
  - fina_indicator_cache 财务指标（带 ann_date，做 point-in-time 对齐）
  - stock_basic.csv      股票名称（ST 过滤）

方法：
  1. 向量化复刻 strategy() 选出候选池（模型真实作用的样本空间）
  2. 向量化复刻 V11 六维打分（纯技术版；依赖外部数据的子项单独标注）
  3. 前瞻收益用复权价、次日开盘买入口径（贴近实盘）
  4. 逐截面日算 Spearman RankIC，汇总 IC 均值 / ICIR / t 值 / 正 IC 占比
  5. 按因子分 5 组看收益单调性
  6. 加入经典对照因子作 benchmark

输出：ic_summary.csv / ic_series.csv / quantile_returns.csv / IC分析报告.md
"""
import os
import sys
import sqlite3
import warnings
from datetime import datetime

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

# ========================= 配置 =========================
DB_PATH = r'D:\mystock\cache_daily\stock_data.db'
BASIC_CSV = r'D:\mystock\cache_daily\stock_basic.csv'
OUT_DIR = r'D:\mystock\solo\ic_output'

START_DATE = '20230101'      # 因子表 2023 起才是全市场
END_DATE = '20260907'
HOLD_DAYS = 20               # 与 V11 的 20 日持有口径一致
MIN_POOL = 10                # 每日候选池少于此数则该日不参与 IC 计算（池子中位约 23 只）
MIN_MV_YI = 80.0             # strategy: 总市值 >= 80 亿

# V11 权重
W = {'突破质量': .25, '资金行为': .24, '位置安全': .18,
     '基本面': .15, '动量爆发': .10, '热度持续': .08}


# ========================= 1. 数据加载 =========================
def _connect_ro(retry=12, wait=5):
    """只读连接。数据库为 journal_mode=delete，写事务会阻塞读，故加重试。"""
    import time as _t
    last = None
    for i in range(retry):
        try:
            con = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=120)
            con.execute('PRAGMA busy_timeout=120000')
            return con
        except Exception as e:
            last = e
            _t.sleep(wait)
    raise RuntimeError(f'数据库连接失败(被写锁占用): {last}')


def _query_chunk(args):
    """每个线程独立只读连接，查询一批交易日（带重试）。"""
    import time as _t
    dlist, cols = args
    last = None
    for attempt in range(8):
        try:
            con = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=120)
            try:
                con.execute('PRAGMA busy_timeout=120000')
                ph = ','.join('?' * len(dlist))
                q = f"SELECT {cols} FROM stk_factor_pro WHERE trade_date IN ({ph})"
                return pd.read_sql_query(q, con, params=list(dlist))
            finally:
                con.close()
        except Exception as e:
            last = e
            _t.sleep(6)
    raise RuntimeError(f'分块查询失败: {last}')


def load_panel(n_workers=8, use_cache=True):
    """并行读取行情面板，返回 (T x N) 矩阵字典。首次读取后落盘 parquet 缓存。"""
    print('[1/6] 读取行情面板（并行）...')
    cols = ('ts_code,trade_date,open,high,low,close,close_qfq,open_qfq,'
            'vol,amount,pct_chg,total_mv,turnover_rate,volume_ratio')
    cache_pq = os.path.join(OUT_DIR, f'panel_{START_DATE}_{END_DATE}.parquet')
    os.makedirs(OUT_DIR, exist_ok=True)

    if use_cache and os.path.exists(cache_pq):
        df = pd.read_parquet(cache_pq)
        print(f'    命中缓存 {cache_pq}: {len(df):,} 行')
        return _to_mats(df)

    con = _connect_ro()
    dlist = [r[0] for r in con.execute(
        "SELECT DISTINCT trade_date FROM stk_factor_pro WHERE trade_date>=? AND trade_date<=? "
        "ORDER BY trade_date", (START_DATE, END_DATE))]
    dlist = [d for d in dlist if str(d) >= START_DATE]
    con.close()
    print(f'    目标 {len(dlist)} 个交易日, {n_workers} 线程')

    chunks = [(dlist[i::n_workers], cols) for i in range(n_workers) if dlist[i::n_workers]]
    from concurrent.futures import ThreadPoolExecutor
    parts = []
    with ThreadPoolExecutor(max_workers=n_workers) as ex:
        for i, df in enumerate(ex.map(_query_chunk, chunks), 1):
            parts.append(df)
            print(f'      分块 {i}/{len(chunks)} 完成 ({len(df):,} 行)')

    df = pd.concat(parts, ignore_index=True)

    df['trade_date'] = df['trade_date'].astype(str)
    # 同一 (code, date) 去重
    df = df.drop_duplicates(['ts_code', 'trade_date'])
    print(f'    原始 {len(df):,} 行, {df.ts_code.nunique():,} 只, '
          f'{df.trade_date.nunique()} 个交易日')

    # 剔除数据不完整的交易日（避免半截数据）
    cnt = df.groupby('trade_date').size()
    med = cnt.median()
    bad = cnt[cnt < med * 0.6].index.tolist()
    if bad:
        print(f'    剔除不完整交易日 {len(bad)} 天: {bad[:6]}{"..." if len(bad)>6 else ""}')
        df = df[~df.trade_date.isin(bad)]

    try:
        df.to_parquet(cache_pq, index=False)
        print(f'    已缓存面板 -> {cache_pq}')
    except Exception as e:
        print(f'    缓存写入失败: {e}')

    return _to_mats(df)


def _to_mats(df):
    """长表 -> (T x N) 矩阵字典。"""
    mats = {}
    for c in ['open', 'high', 'low', 'close', 'close_qfq', 'open_qfq',
              'vol', 'amount', 'pct_chg', 'total_mv', 'turnover_rate', 'volume_ratio']:
        mats[c] = (df.pivot(index='trade_date', columns='ts_code', values=c)
                     .sort_index().astype('float32'))
    dates = mats['close'].index
    codes = mats['close'].columns
    print(f'    面板 {len(dates)} 日 x {len(codes)} 只  ({dates[0]} ~ {dates[-1]})')
    return mats, dates, codes


def load_names():
    try:
        b = pd.read_csv(BASIC_CSV, dtype={'ts_code': str})
        return dict(zip(b['ts_code'], b['name'].fillna('')))
    except Exception as e:
        print(f'    名称加载失败: {e}')
        return {}


def load_fina_pit(dates):
    """财务指标 point-in-time：每个交易日只取 ann_date <= 该日 的最新一期。"""
    print('[2/6] 读取财务数据(PIT) ...')
    con = _connect_ro()
    f = pd.read_sql_query(
        "SELECT ts_code, ann_date, end_date, roe, netprofit_yoy, or_yoy "
        "FROM fina_indicator_cache WHERE ann_date IS NOT NULL", con)
    con.close()
    if f.empty:
        return {}
    f['ann_date'] = f['ann_date'].astype(str)
    f = f.sort_values(['ts_code', 'ann_date'])

    # 每个 (code, ann_date) 保留最新
    f = f.drop_duplicates(['ts_code', 'ann_date'], keep='last')
    out = {}
    for code, g in f.groupby('ts_code'):
        out[code] = (g['ann_date'].values,
                     g['roe'].astype('float32').values,
                     g['netprofit_yoy'].astype('float32').values)
    print(f'    {len(out):,} 只股票财务记录')
    return out


def build_fina_matrix(fina, dates, codes):
    """构造 (T x N) 的 ROE / 净利润增速 PIT 矩阵（批量向量化，避免逐列 iloc）。"""
    if not fina:
        return None, None
    dv = np.asarray(dates).astype(str)
    rec_c, rec_p, rec_r, rec_n = [], [], [], []
    for code, (ads, r, n) in fina.items():
        ads = np.asarray(ads).astype(str)
        if len(ads) == 0:
            continue
        pos = np.searchsorted(dv, ads, side='right') - 1     # 公告日在日期轴上的位置
        keep = pos >= 0
        if not keep.any():
            continue
        rec_c.append(np.full(keep.sum(), code))
        rec_p.append(pos[keep])
        rec_r.append(r[keep])
        rec_n.append(n[keep])
    if not rec_c:
        return None, None

    big = pd.DataFrame({
        'code': np.concatenate(rec_c), 'pos': np.concatenate(rec_p),
        'roe': np.concatenate(rec_r), 'npy': np.concatenate(rec_n)})
    big = big.drop_duplicates(['code', 'pos'], keep='last')   # 同日多条取最后
    T = len(dates)
    roe = (big.pivot(index='pos', columns='code', values='roe')
              .reindex(range(T)).reindex(columns=codes).ffill())
    npy = (big.pivot(index='pos', columns='code', values='npy')
              .reindex(range(T)).reindex(columns=codes).ffill())
    roe.index, npy.index = dates, dates
    return roe.astype('float64'), npy.astype('float64')


# ========================= 2. 特征计算 =========================
def compute_features(m):
    """向量化计算全部中间特征，返回特征矩阵字典。"""
    print('[3/6] 计算技术特征 ...')
    C, H, L, O = m['close'], m['high'], m['low'], m['open']
    V, AMT, PCT = m['vol'], m['amount'], m['pct_chg']

    f = {}
    f['ma5'] = C.rolling(5).mean()
    f['ma10'] = C.rolling(10).mean()
    f['ma20'] = C.rolling(20).mean()
    f['ma30'] = C.rolling(30).mean()
    f['ma60'] = C.rolling(60).mean()
    f['ma120'] = C.rolling(120).mean()

    # 收益率
    for n in (3, 5, 6, 10, 11, 20, 21, 40, 120):
        f[f'ret{n}'] = C / C.shift(n) - 1

    # MA 斜率与加速度
    ma20, ma10 = f['ma20'], f['ma10']
    f['ma20_accel'] = ((ma20 - ma20.shift(5)) / ma20) - ((ma20.shift(5) - ma20.shift(10)) / ma20.shift(5))
    f['ma10_accel'] = ((ma10 - ma10.shift(5)) / ma10) - ((ma10.shift(5) - ma10.shift(10)) / ma10.shift(5))
    f['ma20_slope10'] = (ma20 / ma20.shift(10) - 1) * 100

    # 高低点
    f['hhv20_prev'] = H.shift(1).rolling(20).max()
    f['hhv60'] = H.rolling(60).max()
    f['hhv120_prev'] = H.shift(1).rolling(120).max()
    f['llv20'] = C.rolling(20).min()
    f['hhv20_20d'] = H.rolling(20).max()

    # 成交量
    f['vol_ma5'] = V.rolling(5).mean()
    f['vol_ma20'] = V.rolling(20).mean()
    f['vol_ma60'] = V.rolling(60).mean()
    f['vol_ratio'] = V / V.shift(1).rolling(5).mean()
    f['vol_5_20'] = V.rolling(5).mean() / V.rolling(20).mean()

    # 涨停序列（忠实复刻 strategy：主板/双创未区分，与原代码一致）
    s1, s2 = C.shift(1), C.shift(2)
    zt1 = (s1 / s2 < 1.08) & (C / s1 > 1.098)
    zt2 = (s1 / s2 >= 1.051) & (C / s1 >= 1.051) & (C / s2 >= 1.11)
    f['zt'] = (zt1 | zt2).fillna(False)

    # 平台紧凑度（前20日振幅 / 自平台低点涨幅）
    plat_max = H.shift(1).rolling(20).max()
    plat_min = L.shift(1).rolling(20).min()
    f['plat_range'] = (plat_max - plat_min) / plat_min * 100
    f['plat_pull'] = (C / plat_min - 1) * 100

    # 90日振幅
    f['range90'] = (H.rolling(90).max() - L.rolling(90).min()) / L.rolling(90).min()

    # 当日收盘位置
    rng = (H - L).replace(0, np.nan)
    f['close_loc'] = (C - L) / rng

    # 上方阻力距离（近120日局部高点中，高于当前价的最近一个）
    f['res_dist'] = _resistance_distance(H, C, look=120)

    print(f'    {len(f)} 个特征计算完成')
    return f


def _resistance_distance(H, C, look=120):
    """距上方最近阻力的距离(%)。

    原实现逐日扫描 120 日局部高点，O(T*look*N) 太慢；这里用"前 look 日最高价"近似，
    与"最近局部高点"高度相关且可 O(1) 向量化。若最高价不高于当前价则视为无阻力。
    """
    hhv = H.shift(1).rolling(look).max()
    rd = (hhv / C - 1) * 100
    return rd.where(hhv > C)          # 无上方阻力 → NaN


def barslast_matrix(ztdf):
    """沿时间轴计算距离上一次 True 的天数；从未出现则为大数。

    注意：必须用「当前行索引 - 最后一次 True 的行索引」，
    不能用 (T-1) - last（那样只有最后一行正确）。
    """
    T = len(ztdf)
    arr = np.where(ztdf.values, np.arange(T)[:, None], -1)
    last = np.maximum.accumulate(arr, axis=0)
    bl = np.arange(T)[:, None] - last
    bl[last < 0] = 10 ** 6
    return pd.DataFrame(bl, index=ztdf.index, columns=ztdf.columns)


# ========================= 3. 复刻 strategy 候选池 =========================
def strategy_mask(m, f, names):
    """向量化复刻 strategy()，返回 (T x N) 布尔矩阵。"""
    print('[4/6] 复刻 strategy() 筛选候选池 ...')
    C, H, L, V = m['close'], m['high'], m['low'], m['vol']
    MV, PCT = m['total_mv'], m['pct_chg']
    T, N = C.shape

    # --- 前置硬过滤 ---
    ok = pd.DataFrame(True, index=C.index, columns=C.columns)

    ok &= (MV >= MIN_MV_YI * 10000)                      # total_mv 单位:万元
    # 排除代码前缀 1/2（忠实原代码，非 ST 判断；北交所在下游按主板分支处理）
    bad_prefix = pd.DataFrame(False, index=C.index, columns=C.columns)
    for p in ('1', '2'):
        bad_prefix |= pd.DataFrame(
            [[str(c).startswith(p) for c in C.columns]], index=C.index, columns=C.columns)
    ok &= ~bad_prefix

    # 两个月涨幅 > 100%
    ok &= (f['ret40'] <= 1.0)

    # 当日涨停排除（主板1.098 / 双创1.198）
    ratio = C / C.shift(1)
    is_cyb = pd.DataFrame(
        [[str(c).startswith('3') or str(c).startswith('688') or str(c).startswith('689')
          for c in C.columns]], index=C.index, columns=C.columns)
    zt_up = pd.DataFrame(np.where(is_cyb.values, 1.198, 1.098),
                         index=C.index, columns=C.columns)
    ok &= (ratio < zt_up)

    # ST 过滤（用最新名称，近似）
    st_cols = [c for c in C.columns if str(names.get(c, '')).upper().startswith(('ST', '*ST'))]
    if st_cols:
        ok[st_cols] = False

    # 60日振幅（原代码用20日：hh/ll - 1 > 1.8）
    hh20 = H.rolling(20).max()
    ll20 = L.rolling(20).min()
    ok &= ((hh20 / ll20 - 1) <= 1.8)

    # 均线位置
    ok &= (C < f['ma20'] * 1.3) & (C / f['ma60'] <= 2)
    ok &= (C >= f['ma20']) & (f['ma10'] >= f['ma60'] * 0.97) & (f['ma5'] >= f['ma60'] * 0.97)

    # P0 大周期门槛（回测统一启用；生产环境按大盘趋势分开关，此处取"始终启用"）
    ma120_slope = (f['ma120'] - f['ma120'].shift(10)) / f['ma120']
    ok &= ~((C <= f['ma120']) & (ma120_slope < 0) & f['ma120'].notna())
    ok &= ((C / H.rolling(120).max() - 1) >= -0.45)

    # --- ztts 条件 ---
    ztts = barslast_matrix(f['zt'].fillna(False)).astype('float64')
    ztts_prev = ztts.shift(1)          # 若今日信号(=0)则取前一个
    ztts_eff = ztts.where(ztts != 0, ztts_prev)
    ok &= (ztts_eff >= 3) & (ztts_eff <= 90) & np.isfinite(ztts_eff)

    # 近5日累计涨幅 > 30% 排除
    ok &= (C / C.shift(5) - 1 <= 0.30)

    # --- TJ 条件：需要 ztts 窗口内的聚合量，逐日向量化近似 ---
    # cond3 区间振幅<1.3 / cond4 距区间高点<1.2 / cond5 近期高点>=120日高点*0.8
    # cond6 ma30 上行 / 无放量大阴 / 存在缩量日
    ok &= (f['hhv20_20d'] >= f['hhv120_prev'] * 0.8)
    ok &= (f['ma30'] >= f['ma30'].shift(1))
    # 回撤 >= -15%（用近20日近似 ztts 窗口）
    dd = (C - C.rolling(20).max()) / C.rolling(20).max()
    ok &= (dd >= -0.15)
    # 无放量大阴：当日跌幅<-5% 且量>5日均量1.5倍
    bad_k = (PCT < -5) & (V > f['vol_ma5'] * 1.5)
    ok &= ~bad_k.rolling(20).max().fillna(0).astype(bool)

    # --- XH：主板突破 / 双创低吸 ---
    highest_close = C.shift(1).rolling(20).max()
    vol_peak = V.shift(1).rolling(20).max()
    vol_cond = V >= vol_peak * 0.7

    is_main = pd.DataFrame(
        [[str(c).startswith(('600', '601', '603', '000', '001', '002')) for c in C.columns]],
        index=C.index, columns=C.columns)
    main_ok = ((C > C.shift(1)) & (C > C.shift(2)) & (C / C.shift(1) > 1.05) & vol_cond) | \
              ((C.shift(1) < highest_close) & (C >= highest_close) & (C / C.shift(1) < 1.09))
    main_ok &= (C >= f['ma5'] * 0.97) & (C / f['ma5'] < 1.11)

    dist_hi = (highest_close - C) / highest_close
    chi_ok = (dist_hi > 0.03) & (dist_hi < 0.15) & \
             (C >= f['ma20'] * 0.97) & (C <= f['ma20'] * 1.15) & \
             (V < vol_peak * 0.6) & \
             ((C / C.shift(1) - 1).abs() < 0.04) & ((C / C.shift(2) - 1).abs() < 0.06)

    xh = (main_ok & is_main) | (chi_ok & is_cyb)
    mask = ok & xh

    # 需要足够历史
    valid = C.rolling(80).count() >= 80
    mask = mask & valid

    cnt = mask.sum(axis=1)
    print(f'    候选池: 日均 {cnt.mean():.1f} 只, 中位数 {cnt.median():.0f}, '
          f'最大 {cnt.max()}, 非零交易日 {(cnt > 0).sum()}')
    return mask.fillna(False)


# ========================= 4. 复刻 V11 六维打分 =========================
def score_dims(m, f, roe_pit, npy_pit, mask=None):
    """返回 {维度名: (T x N) 打分矩阵}。依赖外部数据的子项用基准值填充并标注。"""
    print('[5/6] 复刻 V11 六维打分 ...')
    C, H, L = m['close'], m['high'], m['low']
    V, PCT = m['vol'], m['pct_chg']

    # ---------- 1. 动量爆发力 ----------
    s = pd.DataFrame(50.0, index=C.index, columns=C.columns)
    s += np.select(
        [f['ma20_accel'] > 0.02, f['ma20_accel'] > 0.008, f['ma20_accel'] > 0, f['ma20_accel'] > -0.008],
        [30, 20, 10, 0], default=-12)
    s += np.select(
        [f['ma10_accel'] > 0.03, f['ma10_accel'] > 0.012, f['ma10_accel'] > 0, f['ma10_accel'] > -0.012],
        [25, 16, 8, -3], default=-10)
    r5 = f['ret5'] * 100
    s += np.select([r5 > 15, r5 > 8, r5 > 3, r5 > -3, r5 > -10], [25, 18, 10, 3, -5], default=-15)
    ret3 = f['ret3'] * 100
    ret7ago = (f['ret10'] - f['ret3']) * 100
    acc = ret3 - ret7ago
    s += np.select([acc > 8, acc > 4, acc > 1, acc > -2], [25, 18, 10, 3], default=-8)
    d20 = (f['hhv20_prev'] - C) / f['hhv20_prev']
    bpow = np.select([C >= f['hhv20_prev'], d20 <= .03, d20 <= .08, d20 <= .15],
                     [1.0, .85, .6, .3], default=0.0)
    s += np.where((bpow >= .85) & (f['vol_5_20'] > 1.3), 20,
                  np.where(bpow >= .85, 10, np.where(bpow >= .6, 5, 0)))
    dim = {'动量爆发': s.clip(0, 100)}

    # ---------- 2. 资金行为（仅 2a/2b，2c 同花顺资金流 / 2d 机构流 不可回溯）----------
    s = pd.DataFrame(50.0, index=C.index, columns=C.columns)
    vr2060 = f['vol_ma20'] / f['vol_ma60']
    s += np.select([vr2060 > 1.5, vr2060 > 1.2, vr2060 > 1.0, vr2060 > 0.7],
                   [12, 6, 2, -5], default=-15)
    vr = V / V.shift(1).rolling(5).mean()
    s += np.select([vr > 3.0, vr > 2.0, vr > 1.3], [10, 6, 3], default=0)
    # 量价关系：近20日上涨日均量 / 下跌日均量
    up = V.where(PCT > 0).rolling(20).mean()
    dn = V.where(PCT < 0).rolling(20).mean()
    vpr = up / dn.replace(0, np.nan)
    s += np.select([vpr > 1.5, vpr > 1.2], [10, 5], default=0)
    dim['资金行为'] = s.clip(0, 100)

    # ---------- 3. 位置安全性 ----------
    s = pd.DataFrame(50.0, index=C.index, columns=C.columns)
    is_high = C >= f['hhv20_prev']
    s += np.where(is_high, 15, np.select([d20 <= .03, d20 <= .08, d20 <= .15, d20 <= .25],
                                         [12, 8, 4, 2], default=0))
    run_up = (C - f['llv20']) / f['llv20']
    s += np.select([run_up <= .15, run_up <= .25], [12, 6], default=0)
    s += np.where(f['range90'] < .25, 8, 0)
    dim['位置安全'] = s.clip(0, 100)

    # ---------- 4. 突破质量 ----------
    s = pd.DataFrame(50.0, index=C.index, columns=C.columns)
    prior = f['hhv20_prev']
    s += np.select([C >= prior * 1.01, C >= prior, C >= prior * .97], [25, 15, 5], default=-15)
    dvr = V / V.shift(1).rolling(5).mean()
    s += np.select([(dvr >= 1.3) & (dvr <= 3.0), dvr > 4.0, dvr < 0.8], [12, -8, -8], default=0)
    s += np.select([f['close_loc'] >= .75, f['close_loc'] < .45], [8, -12], default=0)
    s += np.select([f['ma20_slope10'] > 3, f['ma20_slope10'] < -1], [8, -12], default=0)
    r20 = f['ret20'] * 100
    s += np.select([r20 > 35, r20 > 25], [-15, -8], default=0)
    plat = np.select(
        [(f['plat_range'] < 12) & (f['plat_pull'] < 15),
         (f['plat_range'] < 18) & (f['plat_pull'] < 15),
         f['plat_pull'] >= 18, f['plat_range'] >= 25],
        [6, 4, -6, -4], default=0)
    s += plat
    plat_ok = pd.DataFrame(plat >= 4, index=C.index, columns=C.columns)
    rd = f['res_dist']
    s += np.where(rd.isna(), np.where(plat_ok, 8, 2),
                  np.select([rd < 3, rd < 8, (plat_ok & (rd < 15)), plat_ok],
                            [np.where(plat_ok, -4, -8), np.where(plat_ok, 0, -4), 3, 6], default=0))
    dim['突破质量'] = s.clip(0, 100)

    # ---------- 5. 基本面（PIT 口径：ROE 40% + 净利增速 30% + 营收增速 30% 的合成）----------
    rs = pd.DataFrame(50.0, index=C.index, columns=C.columns)
    if roe_pit is not None:
        rs = pd.DataFrame(50.0, index=C.index, columns=C.columns)
        rs += np.select([roe_pit > 25, roe_pit > 18, roe_pit > 12, roe_pit > 6, roe_pit > 0],
                        [45, 35, 20, 5, -10], default=-25)
        rs += np.select([npy_pit > 100, npy_pit > 50, npy_pit > 20, npy_pit > 0, npy_pit > -20],
                        [25, 18, 10, 3, -8], default=-20)
    dim['基本面'] = rs.clip(0, 100)

    # ---------- 6. 热度：依赖热榜，不可回溯，置基准值 ----------
    dim['热度持续'] = pd.DataFrame(50.0, index=C.index, columns=C.columns)

    # ---------- 整合评分（技术版，hot 与缺失项按 50 计）----------
    base = (dim['资金行为'] * W['资金行为'] + dim['位置安全'] * W['位置安全'] +
            dim['基本面'] * W['基本面'] + dim['突破质量'] * W['突破质量'] +
            dim['动量爆发'] * W['动量爆发'] + dim['热度持续'] * W['热度持续'])
    # 追高惩罚
    bias20 = (C - f['ma20']) / f['ma20'] * 100
    pen = np.where((r5 > 15) & (bias20 > 25), 20,
                   np.where(r5 > 25, 15, np.where(f['ret10'] * 100 > 35, 10,
                                                  np.where(bias20 > 15, 5, 0))))
    # 龙头加分
    v5 = V.shift(1).rolling(5).mean()
    v15 = V.shift(1).rolling(15).mean()
    lead = np.where(C >= f['hhv20_prev'] * 0.97, np.where(v5 > v15 * 1.2, 12, 6), 0)
    base_raw = base - pen + lead
    dim['整合评分(技术版)'] = base_raw.clip(5, 120)

    # ---------- V11.1：资金行为反向 + 换手率负向因子 ----------
    # 资金行为反向：50 - vol_delta（clamp 边界处与 100-x 略有差异，影响可忽略）
    dim['资金行为(反向)'] = 100 - dim['资金行为']

    # 换手率：候选池内截面分位，换手越低得分越高（与生产 build_pool_turnover_pct 口径一致）
    if mask is not None:
        _turn = m['turnover_rate'].astype('float64').where(mask)
        # rank(axis=1) 自动跳过 NaN → 只在当日候选池内排分位
        _pct = _turn.rank(axis=1, pct=True) * 100
        dim['换手率池内分位'] = _pct
        dim['换手率得分(负向)'] = 100 - _pct
    else:
        dim['换手率得分(负向)'] = pd.DataFrame(50.0, index=C.index, columns=C.columns)

    W2 = {'capital': .16, 'position': .20, 'hot': .07, 'fundamental': .18,
          'breakout': .10, 'momentum': .05, 'turnover': .24}
    base2 = (dim['资金行为(反向)'] * W2['capital'] + dim['位置安全'] * W2['position'] +
             dim['热度持续'] * W2['hot'] + dim['基本面'] * W2['fundamental'] +
             dim['突破质量'] * W2['breakout'] + dim['动量爆发'] * W2['momentum'] +
             dim['换手率得分(负向)'] * W2['turnover'])
    dim['整合评分(V11.1)'] = (base2 - pen + lead).clip(5, 120)

    # ---------- 对照因子 ----------
    dim['[对照]20日反转'] = -f['ret20'] * 100
    dim['[对照]市值对数'] = np.log(m['total_mv'].replace(0, np.nan))
    dim['[对照]换手率'] = m['turnover_rate'].astype('float64')
    dim['[对照]5日动量'] = f['ret5'] * 100
    dim['[对照]当日量比'] = f['vol_ratio'].astype('float64')

    return dim


# ========================= 5. 前瞻收益 & IC 分析 =========================
def forward_returns(m, hold=HOLD_DAYS):
    """次日开盘买入、持有 hold 日后的收盘价卖出（复权口径）。"""
    op, cq = m['open_qfq'], m['close_qfq']
    # t 日信号 -> t+1 开盘买入 -> t+1+hold 收盘卖出
    buy = op.shift(-1)
    sell = cq.shift(-(1 + hold))
    return (sell / buy - 1) * 100


def build_long_table(mask, dim, fwd):
    """把 (T x N) 矩阵压成 (date, code, 各因子, 收益) 长表。"""
    idx = np.where(mask.values)
    if len(idx[0]) == 0:
        return pd.DataFrame()
    dates = mask.index[idx[0]]
    codes = mask.columns[idx[1]]
    data = {'trade_date': dates, 'ts_code': codes}
    for k, mat in dim.items():
        data[k] = mat.values[idx]
    data['fwd_ret'] = fwd.values[idx]
    df = pd.DataFrame(data).replace([np.inf, -np.inf], np.nan)
    return df.dropna(subset=['fwd_ret'])


def ic_analysis(lt, factors, min_n=MIN_POOL):
    """逐截面日 Spearman RankIC，并做汇总统计 + 分组收益。"""
    print('[6/6] 计算 RankIC ...')
    dates, ic_rows = [], {f: [] for f in factors}
    for d, g in lt.groupby('trade_date'):
        if len(g) < min_n:
            continue
        dates.append(d)
        for f in factors:
            x = g[f]
            y = g['fwd_ret']
            m_ = x.notna() & y.notna()
            if m_.sum() < min_n:
                ic_rows[f].append(np.nan)
                continue
            ic_rows[f].append(x[m_].rank().corr(y[m_].rank()))
    ic_df = pd.DataFrame(ic_rows, index=dates)

    summary = []
    for f in factors:
        s = ic_df[f].dropna()
        n = len(s)
        if n < 5:
            summary.append(dict(因子=f, 有效天数=n, IC均值=np.nan, IC标准差=np.nan,
                                ICIR=np.nan, t值=np.nan, 正IC占比=np.nan))
            continue
        mu, sd = s.mean(), s.std()
        icir = mu / sd if sd > 0 else np.nan
        t = icir * np.sqrt(n)
        summary.append(dict(因子=f, 有效天数=n, IC均值=mu, IC标准差=sd,
                            ICIR=icir, t值=t, 正IC占比=(s > 0).mean()))
    return ic_df, pd.DataFrame(summary)


def quantile_returns(lt, factors, q=5):
    """按因子截面分 5 组，算各组平均前瞻收益。"""
    rows = []
    for f in factors:
        tmp = lt[['trade_date', f, 'fwd_ret']].dropna()
        grp = []
        for d, g in tmp.groupby('trade_date'):
            if len(g) < MIN_POOL:
                continue
            try:
                g = g.assign(_q=pd.qcut(g[f].rank(method='first'), q, labels=False))
            except Exception:
                continue
            grp.append(g)
        if not grp:
            continue
        gg = pd.concat(grp)
        means = gg.groupby('_q')['fwd_ret'].mean()
        row = {'因子': f}
        for i in range(q):
            row[f'Q{i+1}'] = means.get(i, np.nan)
        row['多空Q5-Q1'] = row.get(f'Q{q}', np.nan) - row.get('Q1', np.nan)
        rows.append(row)
    return pd.DataFrame(rows)


# ========================= main =========================
def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    t0 = datetime.now()

    mats, dates, codes = load_panel()
    names = load_names()
    fina = load_fina_pit(dates)
    roe_pit, npy_pit = build_fina_matrix(fina, dates, codes)

    feats = compute_features(mats)
    mask = strategy_mask(mats, feats, names)
    dim = score_dims(mats, feats, roe_pit, npy_pit, mask=mask)
    fwd = forward_returns(mats, HOLD_DAYS)

    lt = build_long_table(mask, dim, fwd)
    print(f'\n    样本: {len(lt):,} 条, {lt.trade_date.nunique()} 个截面日, '
          f'日均 {len(lt)/max(lt.trade_date.nunique(),1):.1f} 只')

    if lt.empty:
        print('候选池为空，无法分析')
        return

    order = ['整合评分(技术版)', '整合评分(V11.1)', '资金行为(反向)', '换手率得分(负向)',
             '突破质量', '资金行为', '位置安全', '动量爆发', '基本面', '热度持续',
             '[对照]20日反转', '[对照]5日动量', '[对照]市值对数', '[对照]换手率', '[对照]当日量比']
    factors = [f for f in order if f in lt.columns]

    ic_df, summary = ic_analysis(lt, factors)
    qret = quantile_returns(lt, factors)

    # 保存
    ic_df.to_csv(os.path.join(OUT_DIR, 'ic_series.csv'), encoding='utf-8-sig')
    summary.to_csv(os.path.join(OUT_DIR, 'ic_summary.csv'), index=False, encoding='utf-8-sig')
    qret.to_csv(os.path.join(OUT_DIR, 'quantile_returns.csv'), index=False, encoding='utf-8-sig')
    lt.to_csv(os.path.join(OUT_DIR, 'panel_samples.csv'), index=False, encoding='utf-8-sig')

    # 控制台输出
    print('\n' + '=' * 88)
    print(f' RankIC 汇总  |  持有 {HOLD_DAYS} 日  |  次日开盘买入  |  样本 {len(lt):,} 条')
    print('=' * 88)
    print(f"{'因子':<18}{'天数':>6}{'IC均值':>10}{'IC标准差':>10}{'ICIR':>9}{'t值':>9}{'正IC占比':>10}")
    print('-' * 88)
    for _, r in summary.iterrows():
        if np.isnan(r['IC均值']):
            print(f"{r['因子']:<18}{r['有效天数']:>6}      —        —       —       —        —")
            continue
        star = '***' if abs(r['t值']) >= 3 else ('**' if abs(r['t值']) >= 2 else ('*' if abs(r['t值']) >= 1.5 else ''))
        print(f"{r['因子']:<18}{r['有效天数']:>6}{r['IC均值']:>10.4f}{r['IC标准差']:>10.4f}"
              f"{r['ICIR']:>9.3f}{r['t值']:>9.2f}{r['正IC占比']:>9.1%} {star}")

    print('\n' + '=' * 88)
    print(' 分组收益（Q1最低 → Q5最高，单位 %）')
    print('=' * 88)
    print(f"{'因子':<18}{'Q1':>9}{'Q2':>9}{'Q3':>9}{'Q4':>9}{'Q5':>9}{'多空':>10}")
    print('-' * 88)
    for _, r in qret.iterrows():
        print(f"{r['因子']:<18}{r['Q1']:>9.2f}{r['Q2']:>9.2f}{r['Q3']:>9.2f}"
              f"{r['Q4']:>9.2f}{r['Q5']:>9.2f}{r['多空Q5-Q1']:>10.2f}")

    print(f'\n完成，用时 {(datetime.now()-t0).total_seconds():.1f}s')
    print(f'输出目录: {OUT_DIR}')


if __name__ == '__main__':
    main()
