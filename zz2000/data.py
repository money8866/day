# -*- coding: utf-8 -*-
"""ZZ2000 数据层：优先本地 Tushare 缓存；仅在缓存落后时用 Tushare 增量补齐。

所有外部行情库一律只读打开。任何取数失败都返回 None / UNKNOWN，绝不推断数值。
"""
from __future__ import annotations

import os
import sqlite3
import datetime as dt
from pathlib import Path

import pandas as pd

from . import config as C

_UNK = 'UNKNOWN'
_pro = None


# --------------------------------------------------------------------------- 基础工具
def ro_connect(path: str) -> sqlite3.Connection:
    """只读打开外部数据库，避免污染项目主缓存。"""
    uri = Path(path).as_uri() + '?mode=ro'
    return sqlite3.connect(uri, uri=True, timeout=30)


def rw_connect() -> sqlite3.Connection:
    return sqlite3.connect(C.CACHE_DB, timeout=30)


def init_cache() -> None:
    with rw_connect() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT, updated_at TEXT);
        CREATE TABLE IF NOT EXISTS trade_cal (
            exchange TEXT, cal_date TEXT PRIMARY KEY, is_open INTEGER, pretrade_date TEXT);
        CREATE TABLE IF NOT EXISTS index_daily (
            ts_code TEXT, trade_date TEXT, open REAL, high REAL, low REAL, close REAL,
            pre_close REAL, pct_chg REAL, vol REAL, amount REAL,
            PRIMARY KEY(ts_code, trade_date));
        CREATE TABLE IF NOT EXISTS etf_daily (
            ts_code TEXT, trade_date TEXT, pre_close REAL, open REAL, high REAL, low REAL,
            close REAL, pct_chg REAL, vol REAL, amount REAL,
            PRIMARY KEY(ts_code, trade_date));
        CREATE TABLE IF NOT EXISTS etf_adj (
            ts_code TEXT, trade_date TEXT, adj_factor REAL,
            PRIMARY KEY(ts_code, trade_date));
        CREATE TABLE IF NOT EXISTS etf_div (
            ts_code TEXT, div_date TEXT, raw_json TEXT,
            PRIMARY KEY(ts_code, div_date));
        CREATE TABLE IF NOT EXISTS etf_meta (
            ts_code TEXT PRIMARY KEY, name TEXT, management TEXT, fund_type TEXT,
            invest_type TEXT, benchmark TEXT, found_date TEXT, list_date TEXT,
            m_fee REAL, c_fee REAL, status TEXT, updated_at TEXT);
        CREATE TABLE IF NOT EXISTS limit_list (
            trade_date TEXT, ts_code TEXT, name TEXT, pct_chg REAL, limit_times INTEGER,
            limit_type TEXT, PRIMARY KEY(trade_date, ts_code, limit_type));
        CREATE TABLE IF NOT EXISTS market_daily (
            trade_date TEXT, ts_code TEXT, close REAL, pct_chg REAL, amount REAL,
            PRIMARY KEY(trade_date, ts_code));
        ''')


def meta_get(k: str, default=None):
    with rw_connect() as con:
        r = con.execute('SELECT v FROM meta WHERE k=?', (k,)).fetchone()
    return r[0] if r else default


def meta_set(k: str, v: str) -> None:
    with rw_connect() as con:
        con.execute('INSERT OR REPLACE INTO meta VALUES (?,?,?)',
                    (k, str(v), dt.datetime.now().isoformat(timespec='seconds')))


def load_env() -> dict:
    env = {}
    if not os.path.exists(C.ENV_FILE):
        return env
    for line in open(C.ENV_FILE, encoding='utf-8', errors='ignore'):
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def get_pro():
    """惰性初始化 Tushare pro_api；token 只从 config/.env 读取。"""
    global _pro
    if _pro is not None:
        return _pro
    tok = load_env().get('TUSHARE_TOKEN')
    if not tok:
        return None
    try:
        import tushare as ts
        ts.set_token(tok)
        _pro = ts.pro_api()
    except Exception:
        _pro = None
    return _pro


def ts_call(fn_name: str, **kw):
    """单次 Tushare 调用，失败返回 None（不重试、不推断）。"""
    pro = get_pro()
    if pro is None:
        return None
    fn = getattr(pro, fn_name, None)
    if fn is None:
        return None
    try:
        df = fn(**kw)
        return None if df is None or df.empty else df
    except Exception as e:
        print(f'[data] Tushare {fn_name} 调用失败: {type(e).__name__}: {str(e)[:160]}')
        return None


def today_str() -> str:
    return dt.date.today().strftime('%Y%m%d')


def now_hhmm() -> str:
    return dt.datetime.now().strftime('%H:%M')


# --------------------------------------------------------------------------- 交易日历
def ensure_trade_cal(force: bool = False):
    """缓存交易日历；>7 天才重新向 Tushare 校验一次。"""
    init_cache()
    last = meta_get('cal_refresh_date')
    if not force and last:
        try:
            if (dt.date.today() - dt.date.fromisoformat(last)).days < 7:
                return
        except ValueError:
            pass
    df = ts_call('trade_cal', exchange='SSE', start_date='20230101', end_date='20271231')
    if df is None:
        return  # 保留已有日历，不推断
    with rw_connect() as con:
        con.executemany('INSERT OR REPLACE INTO trade_cal VALUES (?,?,?,?)',
                        [(r.exchange, r.cal_date, int(r.is_open), r.pretrade_date)
                         for r in df.itertuples()])
    meta_set('cal_refresh_date', dt.date.today().isoformat())


def trading_days() -> list[str]:
    ensure_trade_cal()
    with rw_connect() as con:
        rows = con.execute("SELECT cal_date FROM trade_cal WHERE is_open=1 ORDER BY cal_date").fetchall()
    return [r[0] for r in rows]


def is_open(date: str):
    """返回 1/0/None（日历缺失时 None = UNKNOWN）。"""
    with rw_connect() as con:
        r = con.execute('SELECT is_open FROM trade_cal WHERE cal_date=?', (date,)).fetchone()
    return r[0] if r else None


def latest_trading_day(on_or_before: str) -> str | None:
    days = [d for d in trading_days() if d <= on_or_before]
    return days[-1] if days else None


# --------------------------------------------------------------------------- 指数日线
def seed_index_from_main_db() -> int:
    """把项目主缓存里的 932000.CSI 历史一次性导入本模块缓存。"""
    if not os.path.exists(C.MAIN_DB):
        return 0
    try:
        with ro_connect(C.MAIN_DB) as src:
            rows = src.execute(
                "SELECT ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol,amount "
                "FROM index_daily_cache WHERE ts_code=? ORDER BY trade_date", (C.INDEX_CODE,)
            ).fetchall()
    except Exception as e:
        print(f'[data] 主库指数读取失败: {e}')
        return 0
    if rows:
        with rw_connect() as con:
            con.executemany('INSERT OR REPLACE INTO index_daily VALUES (?,?,?,?,?,?,?,?,?,?)', rows)
    return len(rows)


def refresh_index(end_date: str | None = None) -> int:
    """从 Tushare 增量补齐指数日线（只补缓存末端之后的日期）。"""
    if not end_date:
        end_date = today_str()
    with rw_connect() as con:
        mx = con.execute('SELECT MAX(trade_date) FROM index_daily WHERE ts_code=?',
                         (C.INDEX_CODE,)).fetchone()[0]
    if not mx:
        n = seed_index_from_main_db()
        print(f'[data] 指数历史已从主库导入 {n} 行')
        with rw_connect() as con:
            mx = con.execute('SELECT MAX(trade_date) FROM index_daily WHERE ts_code=?',
                             (C.INDEX_CODE,)).fetchone()[0]
    if mx and mx >= end_date:
        return 0
    start = (dt.datetime.strptime(mx, '%Y%m%d') - dt.timedelta(days=20)).strftime('%Y%m%d') if mx else '20230101'
    df = ts_call('index_daily', ts_code=C.INDEX_CODE, start_date=start, end_date=end_date)
    if df is None:
        return 0
    recs = [(C.INDEX_CODE, r.trade_date, r.open, r.high, r.low, r.close,
             r.pre_close, r.pct_chg, r.vol, r.amount) for r in df.itertuples()]
    with rw_connect() as con:
        con.executemany('INSERT OR REPLACE INTO index_daily VALUES (?,?,?,?,?,?,?,?,?,?)', recs)
    return len(recs)


def index_bars() -> pd.DataFrame:
    with rw_connect() as con:
        return pd.read_sql_query(
            'SELECT * FROM index_daily WHERE ts_code=? ORDER BY trade_date',
            con, params=(C.INDEX_CODE,))


# --------------------------------------------------------------------------- ETF 基础信息
def refresh_etf_meta(force: bool = False) -> pd.DataFrame | None:
    last = meta_get('fund_basic_date')
    if not force and last and (dt.date.today() - dt.date.fromisoformat(last)).days < 30:
        with rw_connect() as con:
            return pd.read_sql_query('SELECT * FROM etf_meta', con)
    df = ts_call('fund_basic', market='E', status='L')
    if df is None:
        with rw_connect() as con:
            return pd.read_sql_query('SELECT * FROM etf_meta', con)
    hit = df[df['benchmark'].fillna('').str.contains('中证2000') &
             df['name'].fillna('').str.contains('2000')].copy()
    cols = ['ts_code', 'name', 'management', 'fund_type', 'invest_type', 'benchmark',
            'found_date', 'list_date', 'm_fee', 'c_fee', 'status']
    now = dt.datetime.now().isoformat(timespec='seconds')
    with rw_connect() as con:
        con.executemany('INSERT OR REPLACE INTO etf_meta VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                        [tuple(r) + (now,) for r in hit[cols].itertuples(index=False, name=None)])
    meta_set('fund_basic_date', dt.date.today().isoformat())
    return hit[cols]


def csi2000_candidates() -> pd.DataFrame:
    """核验后的中证2000 ETF 清单（含增强策略，由调用方按 invest_type 筛选）。"""
    init_cache()
    df = refresh_etf_meta()
    if df is None or df.empty:
        return pd.DataFrame()
    return df


# --------------------------------------------------------------------------- ETF 行情/复权/分红
def refresh_etf_daily(codes, start_date='20230101', end_date=None) -> int:
    if not end_date:
        end_date = today_str()
    total = 0
    for code in codes:
        with rw_connect() as con:
            mx = con.execute('SELECT MAX(trade_date) FROM etf_daily WHERE ts_code=?', (code,)).fetchone()[0]
        st = start_date
        if mx:
            if mx >= end_date:
                continue
            st = (dt.datetime.strptime(mx, '%Y%m%d') - dt.timedelta(days=15)).strftime('%Y%m%d')
        df = ts_call('fund_daily', ts_code=code, start_date=st, end_date=end_date)
        if df is None:
            continue
        recs = [(code, r.trade_date, r.pre_close, r.open, r.high, r.low, r.close,
                 r.pct_chg, r.vol, r.amount) for r in df.itertuples()]
        with rw_connect() as con:
            con.executemany('INSERT OR REPLACE INTO etf_daily VALUES (?,?,?,?,?,?,?,?,?,?)', recs)
        total += len(recs)
    return total


def refresh_etf_adj(codes, end_date=None) -> int:
    if not end_date:
        end_date = today_str()
    total = 0
    for code in codes:
        df = ts_call('fund_adj', ts_code=code, start_date='20200101', end_date=end_date)
        if df is None:
            continue
        recs = [(code, r.trade_date, r.adj_factor) for r in df.itertuples()]
        with rw_connect() as con:
            con.executemany('INSERT OR REPLACE INTO etf_adj VALUES (?,?,?)', recs)
        total += len(recs)
    return total


def refresh_etf_div(codes) -> int:
    """分红口径核验：Tushare fund_div 为空时记录 EMPTY 标记，不推断为「无分红」。"""
    total = 0
    for code in codes:
        df = ts_call('fund_div', ts_code=code)
        raw = 'EMPTY' if df is None else df.to_json(orient='records', force_ascii=False)
        with rw_connect() as con:
            con.execute('INSERT OR REPLACE INTO etf_div VALUES (?,?,?)', (code, today_str(), raw))
        total += 1
    return total


def etf_bars(code: str) -> pd.DataFrame:
    """返回未复权行情 + 前复权收盘价 qfq_close（以最新 adj_factor 为基准）。"""
    with rw_connect() as con:
        d = pd.read_sql_query(
            'SELECT * FROM etf_daily WHERE ts_code=? ORDER BY trade_date', con, params=(code,))
        a = pd.read_sql_query(
            'SELECT trade_date, adj_factor FROM etf_adj WHERE ts_code=? ORDER BY trade_date',
            con, params=(code,))
    if d.empty:
        return d
    d = d.merge(a, on='trade_date', how='left')
    if a.empty:
        # 复权因子缺失：明确标注，不擅自当作 1.0
        d['adj_factor'] = float('nan')
        d['qfq_close'] = float('nan')
        d['adj_status'] = 'UNKNOWN'
    else:
        latest = float(a['adj_factor'].iloc[-1])
        d['qfq_close'] = d['close'] * d['adj_factor'] / latest
        d['adj_status'] = 'OK'
        d.loc[d['adj_factor'].isna(), 'adj_status'] = 'PARTIAL'
    return d


def div_status(code: str) -> str:
    with rw_connect() as con:
        r = con.execute('SELECT raw_json FROM etf_div WHERE ts_code=? ORDER BY div_date DESC LIMIT 1',
                        (code,)).fetchone()
    if not r:
        return 'UNKNOWN'
    if r[0] == 'EMPTY':
        return 'NO_DIV_RECORD'
    return 'HAS_DIV_RECORD'


# --------------------------------------------------------------------------- 全市场日线（Tushare 直取兜底）
def ensure_market_daily(date: str) -> int:
    """缓存当日全市场个股日线；项目批量库尚未落库时，直接向 Tushare pro.daily 取数。

    只存计算 breadth / 昨涨停次日表现所需的列，避免与项目主缓存重复。
    """
    with rw_connect() as con:
        n = con.execute('SELECT COUNT(*) FROM market_daily WHERE trade_date=?', (date,)).fetchone()[0]
    if n > 3000:
        return n
    df = ts_call('daily', trade_date=date)
    if df is None or df.empty:
        return n
    recs = [(date, row['ts_code'], row.get('close'), row.get('pct_chg'), row.get('amount'))
            for _, row in df.iterrows()]
    with rw_connect() as con:
        con.executemany('INSERT OR REPLACE INTO market_daily VALUES (?,?,?,?,?)', recs)
    return len(recs)


def market_pct_map(date: str, codes=None) -> dict:
    """取指定个股当日涨跌幅；优先项目主缓存，其次本模块的 Tushare 直取缓存。"""
    out = {}
    try:
        if os.path.exists(C.MAIN_DB):
            with ro_connect(C.MAIN_DB) as con:
                if codes:
                    ph = ','.join('?' * len(codes))
                    rows = con.execute(
                        f'SELECT ts_code,pct_chg FROM daily_cache WHERE trade_date=? AND ts_code IN ({ph})',
                        [date] + list(codes)).fetchall()
                else:
                    rows = con.execute('SELECT ts_code,pct_chg FROM daily_cache WHERE trade_date=?',
                                       (date,)).fetchall()
            out = {r[0]: r[1] for r in rows if r[1] is not None}
    except Exception:
        pass
    if len(out) < (len(codes) if codes else 3000):
        ensure_market_daily(date)
        with rw_connect() as con:
            if codes:
                ph = ','.join('?' * len(codes))
                rows = con.execute(
                    f'SELECT ts_code,pct_chg FROM market_daily WHERE trade_date=? AND ts_code IN ({ph})',
                    [date] + list(codes)).fetchall()
            else:
                rows = con.execute('SELECT ts_code,pct_chg FROM market_daily WHERE trade_date=?',
                                   (date,)).fetchall()
        for r in rows:
            if r[1] is not None and r[0] not in out:
                out[r[0]] = r[1]
    return out


# --------------------------------------------------------------------------- 市场情绪
def sentiment(date: str) -> dict:
    """全市场上涨比例 + 涨停/跌停数量 + 炸板率/连板高度。

    首选项目本地 Tushare 批量缓存；若当日批量库未落库，则用 Tushare
    `daily` + `limit_list_d` 直取并落盘，口径明确标注。两者都失败才标记 UNKNOWN。
    """
    out = {'date': date, 'missing': [], 'source': []}
    try:
        with ro_connect(C.MICRO_DB) as con:
            r = con.execute('SELECT up_count,down_count,flat_count,total,zt_count,dt_count,amount_yi '
                            'FROM micro_daily WHERE trade_date=?', (date,)).fetchone()
        if r:
            up, down, flat, tot, zt, dt_, amt = r
            out.update(up_count=up, down_count=down, flat_count=flat, total=tot,
                       zt_count=zt, dt_count=dt_, amount_yi=amt)
            out['up_ratio'] = (up / tot) if tot else None
            out['source'].append('micro_daily.db(本地Tushare批量缓存)')
    except Exception as e:
        print(f'[data] micro_daily 读取失败: {e}')

    if out.get('total') is None:
        # 兜底：Tushare pro.daily 直取（与 micro_daily 同源、可验证）
        ensure_market_daily(date)
        try:
            with rw_connect() as con:
                row = con.execute('''SELECT
                    SUM(CASE WHEN pct_chg>0 THEN 1 ELSE 0 END),
                    SUM(CASE WHEN pct_chg<0 THEN 1 ELSE 0 END),
                    SUM(CASE WHEN pct_chg=0 THEN 1 ELSE 0 END),
                    COUNT(*), SUM(amount)/100000.0
                    FROM market_daily WHERE trade_date=?''', (date,)).fetchone()
            if row and row[3] and row[3] > 3000:
                up, down, flat, tot, amt = row
                out.update(up_count=up, down_count=down, flat_count=flat, total=tot,
                           amount_yi=amt)
                out['up_ratio'] = up / tot
                out['source'].append('Tushare pro.daily 直取(本模块缓存)')
                # 涨跌停家数用 Tushare 涨跌停池精确口径
                nu, nd, nz = (ensure_limit_list(date, t) for t in ('U', 'D', 'Z'))
                if nu:
                    out['zt_count'] = nu
                if nd:
                    out['dt_count'] = nd
                if nu or nd:
                    out['source'].append('Tushare limit_list_d(U/D) 精确口径')
                if nz:
                    out['zhaban_count'] = nz
                    out['broken_rate'] = round(100.0 * nz / (nu + nz), 1) if (nu + nz) else None
                with rw_connect() as con:
                    h = con.execute('SELECT MAX(limit_times) FROM limit_list '
                                    'WHERE trade_date=? AND limit_type=?', (date, 'U')).fetchone()
                if h and h[0] is not None:
                    out['max_limit_height'] = h[0]
        except Exception as e:
            print(f'[data] Tushare 直取兜底计算失败: {e}')
        if out.get('total') is None:
            out['missing'].append('全市场涨跌家数/涨停跌停数')

    try:
        with ro_connect(C.MA_DB) as con:
            r = con.execute('SELECT broken_rate,zhaban_count,max_limit_height,up_ratio '
                            'FROM limit_stats WHERE trade_date=? ORDER BY id DESC LIMIT 1',
                            (date,)).fetchone()
        if r:
            for k, v in zip(('broken_rate', 'zhaban_count', 'max_limit_height'), r[:3]):
                if out.get(k) is None and v is not None:
                    out[k] = v
            out['source'].append('market_analysis.db(本地Tushare批量缓存)')
        elif out.get('broken_rate') is None:
            out['missing'].append('炸板率/连板高度')
    except Exception as e:
        print(f'[data] limit_stats 读取失败: {e}')
        if out.get('broken_rate') is None:
            out['missing'].append('炸板率/连板高度')
    return out


def ensure_limit_list(date: str, limit_type='U') -> int:
    with rw_connect() as con:
        n = con.execute('SELECT COUNT(*) FROM limit_list WHERE trade_date=? AND limit_type=?',
                        (date, limit_type)).fetchone()[0]
    if n:
        return n
    df = ts_call('limit_list_d', trade_date=date, limit_type=limit_type)
    if df is None:
        return 0
    for c in ['ts_code', 'name', 'pct_chg', 'limit_times']:
        if c not in df.columns:
            df[c] = None
    recs = [(date, row['ts_code'], row.get('name'), row.get('pct_chg'),
             row.get('limit_times'), limit_type) for _, row in df.iterrows()]
    with rw_connect() as con:
        con.executemany('INSERT OR REPLACE INTO limit_list VALUES (?,?,?,?,?,?)', recs)
    return len(recs)


def yzt_performance(date: str, prev_date: str) -> dict:
    """昨日涨停股在今日的表现。样本来自 Tushare limit_list_d，收益来自本地 daily_cache。

    口径：前一日 Tushare 涨停池(U)全部成分 → 今日 daily_cache.pct_chg；
    晋级率 = 今日仍在涨停池(U)中的占比。
    """
    out = {'date': date, 'prev_date': prev_date, 'status': 'OK', 'missing': []}
    n_prev = ensure_limit_list(prev_date, 'U')
    n_today = ensure_limit_list(date, 'U')
    if not n_prev:
        out['status'] = 'UNKNOWN'
        out['missing'].append(f'{prev_date} 涨停池(Tushare limit_list_d)')
        return out
    if not n_today:
        out['missing'].append(f'{date} 涨停池(晋级率不可算)')
    try:
        with rw_connect() as con:
            codes = [r[0] for r in con.execute(
                'SELECT ts_code FROM limit_list WHERE trade_date=? AND limit_type=?',
                (prev_date, 'U')).fetchall()]
            today_zt = set(r[0] for r in con.execute(
                'SELECT ts_code FROM limit_list WHERE trade_date=? AND limit_type=?',
                (date, 'U')).fetchall())
        pmap = market_pct_map(date, codes)
        pcts = [float(pmap[c]) for c in codes if c in pmap and pmap[c] is not None]
        out['sample_n'] = len(codes)
        out['matched_n'] = len(pcts)
        out['avg_pct'] = (sum(pcts) / len(pcts)) if pcts else None
        out['up_ratio'] = (sum(1 for p in pcts if p > 0) / len(pcts)) if pcts else None
        out['promote_n'] = len([c for c in codes if c in today_zt]) if n_today else None
        out['promote_rate'] = (out['promote_n'] / len(codes)) if (n_today and codes) else None
        if not pcts:
            out['status'] = 'UNKNOWN'
            out['missing'].append(f'{date} 个股日线(daily_cache / Tushare pro.daily)')
        out['source'] = 'Tushare limit_list_d(U) + 本地 daily_cache / Tushare pro.daily'
    except Exception as e:
        print(f'[data] 昨日涨停次日表现计算失败: {e}')
        out['status'] = 'UNKNOWN'
        out['missing'].append('昨日涨停次日表现')
    return out


def daily_cache_status(date: str) -> dict:
    n = 0
    try:
        if os.path.exists(C.MAIN_DB):
            with ro_connect(C.MAIN_DB) as con:
                n = con.execute('SELECT COUNT(*) FROM daily_cache WHERE trade_date=?', (date,)).fetchone()[0]
    except Exception:
        n = 0
    if n <= 3000:
        n = max(n, ensure_market_daily(date))
        src = 'Tushare pro.daily 直取(本模块缓存)'
    else:
        src = 'daily_cache(本地Tushare批量缓存)'
    return {'rows': n, 'ok': n > 3000, 'source': src}
