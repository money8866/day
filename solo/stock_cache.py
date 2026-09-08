import sqlite3
import pandas as pd
import numpy as np
import os
import time
import datetime
from contextlib import contextmanager

CACHE_DIR = r"D:\mystock\cache_daily"
DB_PATH = os.path.join(CACHE_DIR, "stock_data.db")

# Tushare API 懒初始化（自动加载 d:\mystock\config\.env 中的 TUSHARE_TOKEN，新程序可零配置直接调用）
import tushare as ts
_pro = None
def _get_pro():
    global _pro
    if _pro is None:
        try:
            if not os.getenv('TUSHARE_TOKEN'):
                from dotenv import load_dotenv
                _env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', '.env')
                if os.path.exists(_env):
                    load_dotenv(_env)
        except Exception:
            pass
        _pro = ts.pro_api()
    return _pro

# ═══════════════════════════════════════════════════════
# CSV 缓存 I/O
# ═══════════════════════════════════════════════════════

def read_cache_csv(cache_file):
    """从 CSV 文件读取缓存（含 trade_date 类型转换）"""
    try:
        if os.path.exists(cache_file):
            df = pd.read_csv(cache_file)
            if not df.empty and 'trade_date' in df.columns:
                df['trade_date'] = df['trade_date'].astype(str)
                return df
    except Exception:
        pass
    return None

def save_cache_csv(df, cache_file):
    """保存 DataFrame 到 CSV 缓存"""
    try:
        if df is not None and not df.empty:
            df.to_csv(cache_file, index=False)
    except Exception:
        pass

# =========================================================
# 基础工具
# =========================================================

@contextmanager
def get_conn(max_retries=5, retry_delay=1.0):
    # 单一连接 + 长 busy_timeout：让 SQLite 自身等待短暂写锁
    # （@contextmanager 生成器在 with 体内被 throw 后不能再次 yield，原重试结构会触发 RuntimeError，已移除）
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    try:
        conn.execute('PRAGMA busy_timeout = 30000')
    except Exception:
        pass
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _infer_sqlite_type(dtype, col_name):
    """根据 pandas dtype 推断 SQLite 列类型"""
    name_lower = col_name.lower()
    if name_lower in ('ts_code', 'trade_date'):
        return 'TEXT'
    if pd.api.types.is_integer_dtype(dtype):
        return 'INTEGER'
    if pd.api.types.is_float_dtype(dtype):
        return 'REAL'
    if pd.api.types.is_object_dtype(dtype) or pd.api.types.is_string_dtype(dtype):
        return 'TEXT'
    return 'REAL'


def _ensure_table_from_df(df, table_name, pk_cols=('ts_code', 'trade_date')):
    """根据 DataFrame 动态建表（如果不存在）"""
    if df is None or df.empty:
        return
    cols = list(df.columns)
    col_defs = []
    for col in cols:
        sql_type = _infer_sqlite_type(df[col].dtype, col)
        col_defs.append(f'"{col}" {sql_type}')
    pk_str = ', '.join([f'"{c}"' for c in pk_cols])
    create_sql = f'CREATE TABLE IF NOT EXISTS "{table_name}" ({", ".join(col_defs)}, PRIMARY KEY ({pk_str}))'
    with get_conn() as conn:
        conn.execute(create_sql)


def _table_exists(table_name):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,)
        ).fetchone()
    return row is not None


# =========================================================
# daily_cache 表：专门存储 pro.daily 的 11 列基础行情数据
# 技术指标不再落库，由 compute_factor_indicators 从窄表三表实时派生
# =========================================================

DAILY_CACHE_TABLE = 'daily_cache'

_DAILY_CACHE_COLS = [
    'ts_code', 'trade_date', 'open', 'high', 'low', 'close',
    'pre_close', 'change', 'pct_chg', 'vol', 'amount'
]


def _ensure_daily_cache_table():
    """确保 daily_cache 表存在"""
    with get_conn() as conn:
        conn.execute(f'''
            CREATE TABLE IF NOT EXISTS "{DAILY_CACHE_TABLE}" (
                "ts_code" TEXT,
                "trade_date" TEXT,
                "open" REAL,
                "high" REAL,
                "low" REAL,
                "close" REAL,
                "pre_close" REAL,
                "change" REAL,
                "pct_chg" REAL,
                "vol" REAL,
                "amount" REAL,
                PRIMARY KEY ("ts_code", "trade_date")
            )
        ''')
        conn.execute(f'CREATE INDEX IF NOT EXISTS "idx_daily_code_date" ON "{DAILY_CACHE_TABLE}" ("ts_code", "trade_date")')


def get_daily_cache(ts_code, start_date=None, end_date=None):
    """从 daily_cache 表读取单股日线数据

    Returns: DataFrame 或 None
    """
    if not _table_exists(DAILY_CACHE_TABLE):
        return None
    sql = f'SELECT * FROM {DAILY_CACHE_TABLE} WHERE ts_code = ?'
    params = [ts_code]
    if start_date:
        sql += ' AND trade_date >= ?'
        params.append(str(start_date))
    if end_date:
        sql += ' AND trade_date <= ?'
        params.append(str(end_date))
    sql += ' ORDER BY trade_date'
    with get_conn() as conn:
        df = pd.read_sql_query(sql, conn, params=params)
    return df if not df.empty else None


def get_daily_cache_range(ts_code):
    """获取 daily_cache 表中某股票的日期范围

    Returns: (min_date, max_date) 或 (None, None)
    """
    if not _table_exists(DAILY_CACHE_TABLE):
        return None, None
    with get_conn() as conn:
        row = conn.execute(
            f'SELECT MIN(trade_date), MAX(trade_date) FROM {DAILY_CACHE_TABLE} WHERE ts_code = ?',
            (ts_code,)
        ).fetchone()
    if row and row[0]:
        return str(row[0]), str(row[1])
    return None, None


def batch_insert_daily_cache(df_all):
    """批量插入/更新 daily_cache 数据（INSERT OR REPLACE，安全：仅 11 列）

    Returns: 插入/更新的行数
    """
    if df_all is None or df_all.empty:
        return 0
    _ensure_daily_cache_table()
    # 只保留 daily_cache 表的 11 列，忽略多余列
    cols = [c for c in _DAILY_CACHE_COLS if c in df_all.columns]
    df_valid = df_all[cols].copy()
    # 确保 trade_date 为字符串
    df_valid['trade_date'] = df_valid['trade_date'].astype(str)
    placeholders = ','.join(['?'] * len(cols))
    col_str = ','.join([f'"{c}"' for c in cols])
    sql = f'INSERT OR REPLACE INTO {DAILY_CACHE_TABLE} ({col_str}) VALUES ({placeholders})'
    values = [
        [None if pd.isna(v) else v for v in row]
        for row in df_valid[cols].values.tolist()
    ]
    with get_conn() as conn:
        conn.executemany(sql, values)
    return len(values)


# =========================================================
# adj_factor 复权因子缓存（UDC③：pro.adj_factor，独立轻量表）
# 用于把不复权 daily_cache 折算为前复权 OHLC：qfq = raw × adj / 当日adj
# =========================================================

ADJ_FACTOR_TABLE = 'adj_factor_cache'


def get_adj_factor_cache(ts_code, start_date=None, end_date=None):
    """读取单股复权因子（升序），无数据返回 None"""
    if not _table_exists(ADJ_FACTOR_TABLE):
        return None
    sql = f'SELECT ts_code, trade_date, adj_factor FROM {ADJ_FACTOR_TABLE} WHERE ts_code = ?'
    params = [str(ts_code)]
    if start_date:
        sql += ' AND trade_date >= ?'
        params.append(str(start_date))
    if end_date:
        sql += ' AND trade_date <= ?'
        params.append(str(end_date))
    sql += ' ORDER BY trade_date'
    with get_conn() as conn:
        df = pd.read_sql_query(sql, conn, params=params)
    return df if not df.empty else None


def get_adj_factor_range(ts_code):
    """获取某股票复权因子缓存日期范围 (min_date, max_date)"""
    if not _table_exists(ADJ_FACTOR_TABLE):
        return (None, None)
    with get_conn() as conn:
        row = conn.execute(
            f'SELECT MIN(trade_date), MAX(trade_date) FROM {ADJ_FACTOR_TABLE} WHERE ts_code = ?',
            (str(ts_code),)
        ).fetchone()
    if row is None or row[0] is None:
        return (None, None)
    return (str(row[0]), str(row[1]))


def batch_insert_adj_factor(df_all):
    """批量插入/更新复权因子（INSERT OR REPLACE，仅 3 列）"""
    if df_all is None or df_all.empty:
        return 0
    _ensure_adj_factor_table()
    dfv = df_all[['ts_code', 'trade_date', 'adj_factor']].copy()
    dfv['trade_date'] = dfv['trade_date'].astype(str)
    values = [
        [None if pd.isna(v) else v for v in row]
        for row in dfv.values.tolist()
    ]
    with get_conn() as conn:
        conn.executemany(
            f'INSERT OR REPLACE INTO {ADJ_FACTOR_TABLE} (ts_code, trade_date, adj_factor) VALUES (?, ?, ?)',
            values
        )
    return len(values)


def has_adj_market_day(trade_date):
    """该交易日是否已缓存过整市场复权因子（只读判断，避免重复整表拉取）"""
    if not _table_exists(ADJ_FACTOR_TABLE):
        return False
    with get_conn() as conn:
        row = conn.execute(
            f'SELECT COUNT(*) FROM {ADJ_FACTOR_TABLE} WHERE trade_date = ?',
            (str(trade_date),)
        ).fetchone()
    return bool(row and row[0])


def cached_adj_factor(ts_code, start_date, end_date, pro=None, silent=True):
    """带缓存的 pro.adj_factor 单股复权因子（UDC③，缓存优先）

    逻辑：先查缓存是否覆盖 [start, end]；缺最新目标日时先整市场按日补一次
    （避免逐股拉取当日）；仍缺历史段再按 ts_code 拉取缺口。全部写回缓存。

    Returns: 升序 DataFrame(ts_code/trade_date/adj_factor) 或 None
    """
    ts_code = str(ts_code)
    start_date, end_date = str(start_date), str(end_date)

    try:
        cmin, cmax = get_adj_factor_range(ts_code)
    except Exception:
        cmin = cmax = None

    def _read():
        df = get_adj_factor_cache(ts_code, start_date, end_date)
        return df.sort_values('trade_date').reset_index(drop=True) if df is not None else None

    if cmin and cmax and cmin <= start_date and cmax >= end_date:
        return _read()

    _pro = pro or _get_pro()
    # ① 目标日缺失 → 整市场按日补（用行数判断，只在该交易日首次缺失时拉取一次）
    if not (cmin and cmax and cmax >= end_date):
        try:
            if not has_adj_market_day(end_date):
                df_mkt = _pro.adj_factor(trade_date=end_date)
                time.sleep(0.06)
                if df_mkt is not None and not df_mkt.empty:
                    batch_insert_adj_factor(df_mkt)
                    try:
                        cmin, cmax = get_adj_factor_range(ts_code)
                    except Exception:
                        pass
        except Exception:
            pass

    if cmin and cmax and cmin <= start_date and cmax >= end_date:
        return _read()

    # ② 历史段缺口 → 按单股增量拉取（cache 已覆盖前段时只补尾部）
    fetch_start = start_date
    if cmin and cmax:
        if cmin <= start_date:
            fetch_start = cmax
    try:
        df_new = _pro.adj_factor(ts_code=ts_code, start_date=fetch_start, end_date=end_date)
        time.sleep(0.06)
        if df_new is not None and not df_new.empty:
            batch_insert_adj_factor(df_new)
    except Exception:
        pass
    return _read()


# =========================================================
# 全市场单日查询（C 类：替代 pro.daily(trade_date=...)）
# daily_cache 表的主键是 (ts_code, trade_date)，可直接按 trade_date 反查全市场
# =========================================================

def get_daily_by_date(trade_date):
    """按交易日查询全市场日线（替代 pro.daily(trade_date=...)）

    Returns: DataFrame 或 None
    """
    if not _table_exists(DAILY_CACHE_TABLE):
        return None
    sql = (f'SELECT * FROM {DAILY_CACHE_TABLE} WHERE trade_date = ?')
    with get_conn() as conn:
        df = pd.read_sql_query(sql, conn, params=(str(trade_date),))
    return df if not df.empty else None


def get_daily_by_date_count(trade_date):
    """统计 daily_cache 表中某交易日的记录数（用于判断是否已缓存全市场数据）"""
    if not _table_exists(DAILY_CACHE_TABLE):
        return 0
    with get_conn() as conn:
        row = conn.execute(
            f'SELECT COUNT(*) FROM {DAILY_CACHE_TABLE} WHERE trade_date = ?',
            (str(trade_date),)
        ).fetchone()
    return row[0] if row else 0


def count_daily_stocks():
    """统计 daily_cache 表中已缓存的股票数量"""
    if not _table_exists(DAILY_CACHE_TABLE):
        return 0
    with get_conn() as conn:
        row = conn.execute(f'SELECT COUNT(DISTINCT ts_code) FROM {DAILY_CACHE_TABLE}').fetchone()
    return row[0] if row else 0


def count_daily_rows():
    """统计 daily_cache 表的总行数"""
    if not _table_exists(DAILY_CACHE_TABLE):
        return 0
    with get_conn() as conn:
        row = conn.execute(f'SELECT COUNT(*) FROM {DAILY_CACHE_TABLE}').fetchone()
    return row[0] if row else 0


# =========================================================
# 元数据表（替代 txt 状态文件）
# =========================================================

META_TABLE = 'cache_meta'


def _ensure_meta_table():
    with get_conn() as conn:
        conn.execute(f'''
            CREATE TABLE IF NOT EXISTS {META_TABLE} (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT DEFAULT (datetime('now'))
            )
        ''')


def get_meta(key, default=None):
    _ensure_meta_table()
    with get_conn() as conn:
        row = conn.execute(f'SELECT value FROM {META_TABLE} WHERE key = ?', (key,)).fetchone()
    return row[0] if row else default


def set_meta(key, value):
    _ensure_meta_table()
    with get_conn() as conn:
        conn.execute(
            f'INSERT OR REPLACE INTO {META_TABLE} (key, value, updated_at) VALUES (?, ?, datetime("now"))',
            (str(key), str(value))
        )


# ═══════════════════════════════════════════════════════
# 共享缓存函数（统一入口，3个文件共用避免重复）
# ═══════════════════════════════════════════════════════

_STOCK_BASIC_CACHE = os.path.join(CACHE_DIR, 'stock_basic.csv')

def load_stock_basic():
    """从本地缓存读取股票基本信息（ts_code, name, industry, list_date）"""
    try:
        if os.path.exists(_STOCK_BASIC_CACHE):
            sb = pd.read_csv(_STOCK_BASIC_CACHE)
            if not sb.empty and 'ts_code' in sb.columns:
                return sb
    except Exception:
        pass
    return None

def get_list_date(ts_code):
    """获取股票上市日期（stock_basic.csv → API 两级降级）"""
    try:
        sb = load_stock_basic()
        if sb is not None and 'list_date' in sb.columns:
            row = sb[sb['ts_code'] == ts_code]
            if not row.empty:
                ld = row.iloc[0]['list_date']
                if pd.notna(ld) and str(ld).strip():
                    return str(ld).strip()
        df = _get_pro().stock_basic(ts_code=ts_code)
        if not df.empty:
            ld = df.iloc[0].get('list_date', '')
            if ld:
                return str(ld)
    except Exception:
        pass
    return None

def get_effective_date(force_date: str = '') -> str:
    """获取有效交易日：15:00分界线+跳过周末+查交易日历"""
    if force_date and len(force_date) == 8 and force_date.isdigit():
        return force_date
    now = datetime.datetime.now()
    if now.hour < 16:
        d = now - datetime.timedelta(days=1)
    else:
        d = now
    # 跳过周末（周六=5, 周日=6）
    while d.weekday() >= 5:
        d -= datetime.timedelta(days=1)
    base_date = d.strftime('%Y%m%d')
    # 查交易日历确认是开市日（仅当 pro 可用时）
    try:
        pro = _get_pro()
        if pro:
            cal = pro.trade_cal(exchange='', start_date=base_date, end_date=base_date)
            if cal is not None and len(cal) > 0:
                if cal.iloc[0]['is_open'] == 0:
                    # 非交易日，向前找最近的开市日
                    cal_range = pro.trade_cal(exchange='', start_date='20200101', end_date=base_date)
                    if cal_range is not None and len(cal_range) > 0:
                        open_days = cal_range[cal_range['is_open'] == 1]['cal_date'].tolist()
                        if open_days:
                            return str(open_days[-1])
    except Exception:
        pass
    return base_date

def cached_daily(ts_code, start_date, end_date, pro=None):
    """带缓存的 pro.daily() 调用（V4: 委托统一 API daily()，获得新鲜度检查能力）

    daily_cache 表仅存储 pro.daily 的 11 列基础行情数据（open/high/low/close/vol/amount 等），
    技术指标由 compute_factor_indicators 从窄表三表实时派生，不再落库。
    """
    return daily(ts_code, start_date, end_date, pro=pro, auto_fill=True, silent=True)


# ═══════════════════════════════════════════════════════
# UDC 统一日线缓存 API（Unified Daily Cache）
# 新程序一律从这里调用，替代手写 try/except 样板，禁止直接调 pro.daily
# 内部自动完成：读缓存 -> 新鲜度检查 -> API 兜底 -> 写回缓存
# ═══════════════════════════════════════════════════════

# 全市场单日完整性阈值（A股每日约 5540 条，低于此值视为不完整）
UDC_MARKET_MIN_COUNT = 4000


def daily(ts_code, start_date, end_date, pro=None, auto_fill=True, silent=True):
    """UDC① 单股日线：缓存优先 + 新鲜度检查 + API 兜底回写

    Args:
        ts_code:   股票代码，如 '000001.SZ'
        start_date/end_date: 'YYYYMMDD' 字符串
        pro:       可选，已初始化的 tushare pro_api 对象；不传则内部懒加载（自动读 config/.env）
        auto_fill: True=缓存缺失时调 API 并写回；False=只读缓存（纯本地查询）
        silent:    True=不打印日志；False=打印缓存命中/写入日志

    Returns:
        DataFrame（trade_date 升序，11 列标准 daily 格式）或 None（无数据）

    用法:
        from stock_cache import daily
        df = daily('000001.SZ', '20250601', '20250801')
    """
    ts_code = str(ts_code)
    start_date, end_date = str(start_date), str(end_date)
    # ① 缓存优先：max_date 覆盖 end_date 即视为有效（区间内停牌缺行属正常）
    try:
        _, max_date = get_daily_cache_range(ts_code)
        if max_date is not None and str(max_date) >= end_date:
            df = get_daily_cache(ts_code, start_date, end_date)
            if df is not None and not df.empty:
                if not silent:
                    print(f'[daily_cache] 命中 {ts_code} {start_date}~{end_date} ({len(df)} 行)')
                return df.sort_values('trade_date').reset_index(drop=True)
    except Exception as e:
        if not silent:
            print(f'[daily_cache] 读取失败 {ts_code}: {e}')

    # ② 缓存未命中/不够新
    if not auto_fill:
        # 只读模式：返回缓存中已有的部分数据
        try:
            df = get_daily_cache(ts_code, start_date, end_date)
            if df is not None and not df.empty:
                return df.sort_values('trade_date').reset_index(drop=True)
        except Exception:
            pass
        return None
    try:
        _pro = pro or _get_pro()
        df = _pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        time.sleep(0.06)
    except Exception as e:
        if not silent:
            print(f'[daily_cache] API 调用失败 {ts_code}: {e}')
        # 网络异常兜底：返回缓存中已有的部分数据
        try:
            df = get_daily_cache(ts_code, start_date, end_date)
            if df is not None and not df.empty:
                return df.sort_values('trade_date').reset_index(drop=True)
        except Exception:
            pass
        return None
    if df is None or df.empty:
        return None
    df['trade_date'] = df['trade_date'].astype(str)
    # ③ 写回缓存
    try:
        batch_insert_daily_cache(df)
        if not silent:
            print(f'[daily_cache] 写回 {ts_code} {len(df)} 行')
    except Exception as e:
        if not silent:
            print(f'[daily_cache] 写回失败 {ts_code}: {e}')
    return df.sort_values('trade_date').reset_index(drop=True)


# （旧版 daily 已并入上方 UDC 区域，此处保留 daily_market / daily_batch / cache_status）

def daily_market(trade_date, pro=None, auto_fill=True, min_rows=UDC_MARKET_MIN_COUNT, silent=True):
    """UDC② 全市场单日日线：缓存优先 + 完整性检查 + API 兜底回写

    非交易日首次拉取确认无数据后写入 meta 标记，避免节假日反复调 API。

    Args:
        trade_date: 'YYYYMMDD' 字符串
        pro:        可选，已初始化的 pro_api 对象
        auto_fill:  True=缓存缺失时调 API 并写回
        min_rows:   缓存判定阈值：当日记录数 >= min_rows 视为已缓存全市场
        silent:     True=不打印日志

    Returns:
        DataFrame（全市场当日数据）或 None（非交易日/无数据）

    用法:
        from stock_cache import daily_market
        df = daily_market('20260821')
    """
    trade_date = str(trade_date)
    # ① 缓存完整 -> 直接读
    try:
        cnt = get_daily_by_date_count(trade_date)
        if cnt >= min_rows:
            df = get_daily_by_date(trade_date)
            if df is not None and not df.empty:
                if not silent:
                    print(f'[daily_cache] 命中全市场 {trade_date} ({len(df)} 行)')
                return df
        # ② 不完整但此前已确认该日无数据（节假日）-> 返回已有部分
        elif get_meta(f'udc_market_empty_{trade_date}', '') == '1':
            return get_daily_by_date(trade_date) if cnt > 0 else None
    except Exception as e:
        if not silent:
            print(f'[daily_cache] 读取失败 {trade_date}: {e}')

    # ③ 拉全市场并写回
    if not auto_fill:
        try:
            return get_daily_by_date(trade_date)
        except Exception:
            return None
    try:
        _pro = pro or _get_pro()
        df = _pro.daily(trade_date=trade_date)
        time.sleep(0.06)
    except Exception as e:
        if not silent:
            print(f'[daily_cache] API 调用失败 {trade_date}: {e}')
        # 网络异常不记 empty 标记，允许下次重试；返回缓存已有部分
        try:
            return get_daily_by_date(trade_date)
        except Exception:
            return None
    if df is None or df.empty:
        # 确认非交易日/无数据，记标记避免重复调用
        try:
            set_meta(f'udc_market_empty_{trade_date}', '1')
        except Exception:
            pass
        return None
    # ④ 写回缓存
    try:
        batch_insert_daily_cache(df)
        if not silent:
            print(f'[daily_cache] 写回全市场 {trade_date} {len(df)} 行')
    except Exception as e:
        if not silent:
            print(f'[daily_cache] 写回失败 {trade_date}: {e}')
    return df


def daily_batch(codes, start_date, end_date, pro=None, auto_fill=True, api_batch_size=50, silent=True):
    """UDC③ 批量多股日线：逐只查缓存，未命中合并 API（每批上限 50 只）+ 写回

    Args:
        codes:      股票代码列表 ['000001.SZ', ...]
        start_date/end_date: 'YYYYMMDD'
        pro/auto_fill/silent: 同 daily()
        api_batch_size: 每次 API 调用合并的股票数上限（Tushare 单次建议 <=50）

    Returns:
        DataFrame（多股合并，按 ts_code + trade_date 升序）或 None（无任何数据）

    用法:
        from stock_cache import daily_batch
        df = daily_batch(['000001.SZ', '600519.SH'], '20250601', '20250801')
    """
    if not codes:
        return None
    cached_parts, missing = [], []
    for code in codes:
        code = str(code)
        try:
            _, max_date = get_daily_cache_range(code)
            if max_date is not None and str(max_date) >= str(end_date):
                c = get_daily_cache(code, start_date, end_date)
                if c is not None and not c.empty:
                    cached_parts.append(c)
                    continue
        except Exception:
            pass
        missing.append(code)

    if missing and auto_fill:
        _pro = pro or _get_pro()
        for i in range(0, len(missing), api_batch_size):
            chunk = missing[i:i + api_batch_size]
            try:
                batch_df = _pro.daily(ts_code=','.join(chunk), start_date=start_date, end_date=end_date)
                time.sleep(0.06)
                if batch_df is not None and not batch_df.empty:
                    try:
                        batch_insert_daily_cache(batch_df)
                        if not silent:
                            print(f'[daily_cache] 写回 {len(chunk)} 只 {len(batch_df)} 行')
                    except Exception:
                        pass
                    cached_parts.append(batch_df)
            except Exception as e:
                if not silent:
                    print(f'[daily_cache] 批量 API 失败（{chunk[0]} 等 {len(chunk)} 只）: {e}')

    if not cached_parts:
        return None
    out = pd.concat(cached_parts, ignore_index=True)
    out['trade_date'] = out['trade_date'].astype(str)
    return out.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)


# =========================================================
# daily_basic_cache 表：pro.daily_basic 窄表（7 列，替代 stk_factor_pro 市值/换手类列）
# adj_factor_cache 表：pro.adj_factor 复权因子（前复权锚定计算用）
# 二者与 daily_cache(11列) 共同构成新数据源，删除对 stk_factor_pro 宽表的读取依赖
# =========================================================

DAILY_BASIC_CACHE_TABLE = 'daily_basic_cache'
ADJ_FACTOR_CACHE_TABLE = 'adj_factor_cache'

DAILY_BASIC_BATCH_KEY = 'daily_basic_batch_date'
ADJ_FACTOR_BATCH_KEY = 'adj_factor_batch_date'

_DAILY_BASIC_COLS = [
    'ts_code', 'trade_date', 'turnover_rate', 'turnover_rate_f',
    'volume_ratio', 'total_mv', 'circ_mv',
    'pe', 'pe_ttm', 'pb', 'ps', 'dv_ttm', 'float_share', 'total_share',
]

# 估值扩列（生产代码实际使用的 daily_basic 估值字段，逐列幂等补进存量表）
_DAILY_BASIC_EXTRA_COLS = ['pe', 'pe_ttm', 'pb', 'ps', 'dv_ttm', 'float_share', 'total_share']

_ADJ_FACTOR_COLS = ['ts_code', 'trade_date', 'adj_factor']


def _ensure_daily_basic_table():
    """确保 daily_basic_cache 表存在（窄表，PK ts_code+trade_date）"""
    with get_conn() as conn:
        conn.execute(f'''
            CREATE TABLE IF NOT EXISTS "{DAILY_BASIC_CACHE_TABLE}" (
                "ts_code" TEXT,
                "trade_date" TEXT,
                "turnover_rate" REAL,
                "turnover_rate_f" REAL,
                "volume_ratio" REAL,
                "total_mv" REAL,
                "circ_mv" REAL,
                "pe" REAL,
                "pe_ttm" REAL,
                "pb" REAL,
                "ps" REAL,
                "dv_ttm" REAL,
                "float_share" REAL,
                "total_share" REAL,
                PRIMARY KEY ("ts_code", "trade_date")
            )
        ''')
        # 兼容既有存量库：动态补列（早期 6 列 / 7 列 / 无估值列）
        _cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{DAILY_BASIC_CACHE_TABLE}")').fetchall()}
        for _c in ['turnover_rate_f'] + _DAILY_BASIC_EXTRA_COLS:
            if _c not in _cols:
                conn.execute(f'ALTER TABLE "{DAILY_BASIC_CACHE_TABLE}" ADD COLUMN "{_c}" REAL')
        conn.execute(f'CREATE INDEX IF NOT EXISTS "idx_db_date" ON "{DAILY_BASIC_CACHE_TABLE}" ("trade_date")')


def _ensure_adj_factor_table():
    """确保 adj_factor_cache 表存在（PK ts_code+trade_date）"""
    with get_conn() as conn:
        conn.execute(f'''
            CREATE TABLE IF NOT EXISTS "{ADJ_FACTOR_CACHE_TABLE}" (
                "ts_code" TEXT,
                "trade_date" TEXT,
                "adj_factor" REAL,
                PRIMARY KEY ("ts_code", "trade_date")
            )
        ''')
        conn.execute(f'CREATE INDEX IF NOT EXISTS "idx_af_date" ON "{ADJ_FACTOR_CACHE_TABLE}" ("trade_date")')


def batch_insert_daily_basic(df_all):
    """批量插入/更新 daily_basic_cache 数据（UPSERT，只更新传入列，保留存量其他列）

    Args:
        df_all: DataFrame，含 ts_code/trade_date + 若干 daily_basic 字段

    Returns:
        插入/更新的行数
    """
    if df_all is None or df_all.empty:
        return 0
    _ensure_daily_basic_table()
    cols = [c for c in _DAILY_BASIC_COLS if c in df_all.columns]
    if 'ts_code' not in cols or 'trade_date' not in cols:
        return 0
    df_valid = df_all[cols].copy()
    df_valid['trade_date'] = df_valid['trade_date'].astype(str)
    placeholders = ','.join(['?'] * len(cols))
    col_str = ','.join([f'"{c}"' for c in cols])
    update_cols = [c for c in cols if c not in ('ts_code', 'trade_date')]
    if update_cols:
        update_clause = ', '.join([f'"{c}"=excluded."{c}"' for c in update_cols])
        sql = (f'INSERT INTO {DAILY_BASIC_CACHE_TABLE} ({col_str}) VALUES ({placeholders}) '
               f'ON CONFLICT("ts_code", "trade_date") DO UPDATE SET {update_clause}')
    else:
        sql = (f'INSERT INTO {DAILY_BASIC_CACHE_TABLE} ({col_str}) VALUES ({placeholders}) '
               f'ON CONFLICT("ts_code", "trade_date") DO NOTHING')
    values = [
        [None if pd.isna(v) else v for v in row]
        for row in df_valid[cols].values.tolist()
    ]
    with get_conn() as conn:
        conn.executemany(sql, values)
    return len(values)


def batch_insert_adj_factor(df_all):
    """批量插入/更新 adj_factor_cache 数据（INSERT OR REPLACE）

    Args:
        df_all: DataFrame，含 ts_code/trade_date/adj_factor

    Returns:
        插入/更新的行数
    """
    if df_all is None or df_all.empty:
        return 0
    _ensure_adj_factor_table()
    cols = [c for c in _ADJ_FACTOR_COLS if c in df_all.columns]
    if len(cols) != 3:
        return 0
    df_valid = df_all[cols].copy()
    df_valid['trade_date'] = df_valid['trade_date'].astype(str)
    placeholders = ','.join(['?'] * len(cols))
    col_str = ','.join([f'"{c}"' for c in cols])
    sql = f'INSERT OR REPLACE INTO {ADJ_FACTOR_CACHE_TABLE} ({col_str}) VALUES ({placeholders})'
    values = [
        [None if pd.isna(v) else v for v in row]
        for row in df_valid[cols].values.tolist()
    ]
    with get_conn() as conn:
        conn.executemany(sql, values)
    return len(values)


def get_daily_basic(ts_code, start_date=None, end_date=None):
    """读取单股 daily_basic 数据

    Returns: DataFrame 或 None
    """
    if not _table_exists(DAILY_BASIC_CACHE_TABLE):
        return None
    sql = f'SELECT * FROM {DAILY_BASIC_CACHE_TABLE} WHERE ts_code = ?'
    params = [ts_code]
    if start_date:
        sql += ' AND trade_date >= ?'
        params.append(str(start_date))
    if end_date:
        sql += ' AND trade_date <= ?'
        params.append(str(end_date))
    sql += ' ORDER BY trade_date'
    with get_conn() as conn:
        df = pd.read_sql_query(sql, conn, params=params)
    return df if not df.empty else None


def get_adj_factor(ts_code, start_date=None, end_date=None):
    """读取单股复权因子序列

    Returns: DataFrame 或 None
    """
    if not _table_exists(ADJ_FACTOR_CACHE_TABLE):
        return None
    sql = f'SELECT * FROM {ADJ_FACTOR_CACHE_TABLE} WHERE ts_code = ?'
    params = [ts_code]
    if start_date:
        sql += ' AND trade_date >= ?'
        params.append(str(start_date))
    if end_date:
        sql += ' AND trade_date <= ?'
        params.append(str(end_date))
    sql += ' ORDER BY trade_date'
    with get_conn() as conn:
        df = pd.read_sql_query(sql, conn, params=params)
    return df if not df.empty else None


def get_daily_basic_range(ts_code):
    """获取某股 daily_basic_cache 日期范围

    Returns: (min_date, max_date) 或 (None, None)
    """
    if not _table_exists(DAILY_BASIC_CACHE_TABLE):
        return None, None
    with get_conn() as conn:
        row = conn.execute(
            f'SELECT MIN(trade_date), MAX(trade_date) FROM {DAILY_BASIC_CACHE_TABLE} WHERE ts_code = ?',
            (ts_code,)
        ).fetchone()
    if row and row[0]:
        return str(row[0]), str(row[1])
    return None, None


def get_daily_basic_by_date(trade_date):
    """按交易日查询全市场 daily_basic（替代 pro.daily_basic(trade_date=...)）

    Returns: DataFrame 或 None
    """
    if not _table_exists(DAILY_BASIC_CACHE_TABLE):
        return None
    with get_conn() as conn:
        df = pd.read_sql_query(
            f'SELECT * FROM {DAILY_BASIC_CACHE_TABLE} WHERE trade_date = ?',
            conn, params=(str(trade_date),)
        )
    return df if not df.empty else None


def get_daily_basic_by_date_count(trade_date):
    """统计某交易日 daily_basic 全市场记录数"""
    if not _table_exists(DAILY_BASIC_CACHE_TABLE):
        return 0
    with get_conn() as conn:
        row = conn.execute(
            f'SELECT COUNT(*) FROM {DAILY_BASIC_CACHE_TABLE} WHERE trade_date = ?',
            (str(trade_date),)
        ).fetchone()
    return row[0] if row else 0


def get_daily_basic_valuation_count(trade_date):
    """统计某交易日 daily_basic 中估值列（pe/pe_ttm/pb）至少一项非空的记录数

    完整性判断专用：行数达标但估值列全空 = 残缺数据（早期仅缓存换手/市值列），须重拉覆盖
    """
    if not _table_exists(DAILY_BASIC_CACHE_TABLE):
        return 0
    with get_conn() as conn:
        row = conn.execute(
            f'SELECT COUNT(*) FROM {DAILY_BASIC_CACHE_TABLE} '
            f'WHERE trade_date = ? AND (pe IS NOT NULL OR pe_ttm IS NOT NULL OR pb IS NOT NULL)',
            (str(trade_date),)
        ).fetchone()
    return row[0] if row else 0


def get_adj_factor_by_date_count(trade_date):
    """统计某交易日 adj_factor 全市场记录数"""
    if not _table_exists(ADJ_FACTOR_CACHE_TABLE):
        return 0
    with get_conn() as conn:
        row = conn.execute(
            f'SELECT COUNT(*) FROM {ADJ_FACTOR_CACHE_TABLE} WHERE trade_date = ?',
            (str(trade_date),)
        ).fetchone()
    return row[0] if row else 0


def get_daily_basic_batch_date():
    """获取已按日批量缓存的 daily_basic 最新日期"""
    return get_meta(DAILY_BASIC_BATCH_KEY, '')


def set_daily_basic_batch_date(date_str):
    """设置已按日批量缓存的 daily_basic 最新日期"""
    set_meta(DAILY_BASIC_BATCH_KEY, date_str)


def get_adj_factor_batch_date():
    """获取已按日批量缓存的 adj_factor 最新日期"""
    return get_meta(ADJ_FACTOR_BATCH_KEY, '')


def set_adj_factor_batch_date(date_str):
    """设置已按日批量缓存的 adj_factor 最新日期"""
    set_meta(ADJ_FACTOR_BATCH_KEY, date_str)


def daily_basic_market(trade_date, pro=None, auto_fill=True, min_rows=UDC_MARKET_MIN_COUNT, silent=True):
    """UDC⑥ 全市场单日 daily_basic：缓存优先 + 完整性检查 + API 兜底回写

    Args:
        trade_date: 'YYYYMMDD' 字符串
        pro/auto_fill/min_rows/silent: 同 daily_market()

    Returns:
        DataFrame（全市场当日 daily_basic）或 None（非交易日/无数据）
    """
    trade_date = str(trade_date)
    # ① 缓存完整 -> 直接读
    try:
        cnt = get_daily_basic_by_date_count(trade_date)
        if cnt >= min_rows:
            val_cnt = get_daily_basic_valuation_count(trade_date)
            if val_cnt >= min_rows:
                df = get_daily_basic_by_date(trade_date)
                if df is not None and not df.empty:
                    if not silent:
                        print(f'[daily_basic] 命中全市场 {trade_date} ({len(df)} 行)')
                    return df
            elif not silent:
                print(f'[daily_basic] {trade_date} 行数{cnt}达标但估值覆盖{val_cnt}不足，重拉覆盖')
        elif get_meta(f'db_market_empty_{trade_date}', '') == '1':
            return get_daily_basic_by_date(trade_date) if cnt > 0 else None
    except Exception as e:
        if not silent:
            print(f'[daily_basic] 读取失败 {trade_date}: {e}')

    if not auto_fill:
        try:
            return get_daily_basic_by_date(trade_date)
        except Exception:
            return None
    # ③ 拉全市场并写回
    try:
        _pro = pro or _get_pro()
        df = _pro.daily_basic(trade_date=trade_date, fields=','.join(_DAILY_BASIC_COLS))
        time.sleep(0.06)
    except Exception as e:
        if not silent:
            print(f'[daily_basic] API 调用失败 {trade_date}: {e}')
        try:
            return get_daily_basic_by_date(trade_date)
        except Exception:
            return None
    if df is None or df.empty:
        try:
            set_meta(f'db_market_empty_{trade_date}', '1')
        except Exception:
            pass
        return None
    try:
        batch_insert_daily_basic(df)
        set_daily_basic_batch_date(trade_date)
        if not silent:
            print(f'[daily_basic] 写回全市场 {trade_date} {len(df)} 行')
    except Exception as e:
        if not silent:
            print(f'[daily_basic] 写回失败 {trade_date}: {e}')
    return df


def adj_factor_market(trade_date, pro=None, auto_fill=True, min_rows=UDC_MARKET_MIN_COUNT, silent=True):
    """UDC⑦ 全市场单日复权因子：缓存优先 + 完整性检查 + API 兜底回写

    Returns:
        DataFrame（全市场当日 adj_factor）或 None（非交易日/无数据）
    """
    trade_date = str(trade_date)
    try:
        cnt = get_adj_factor_by_date_count(trade_date)
        if cnt >= min_rows:
            if not silent:
                print(f'[adj_factor] 命中全市场 {trade_date} ({cnt} 行)')
            return None  # 已完整，无需返回（引擎按股查询）
        elif get_meta(f'af_market_empty_{trade_date}', '') == '1':
            return None
    except Exception as e:
        if not silent:
            print(f'[adj_factor] 读取失败 {trade_date}: {e}')

    if not auto_fill:
        return None
    try:
        _pro = pro or _get_pro()
        df = _pro.adj_factor(trade_date=trade_date)
        time.sleep(0.06)
    except Exception as e:
        if not silent:
            print(f'[adj_factor] API 调用失败 {trade_date}: {e}')
        return None
    if df is None or df.empty:
        try:
            set_meta(f'af_market_empty_{trade_date}', '1')
        except Exception:
            pass
        return None
    try:
        n = batch_insert_adj_factor(df)
        set_adj_factor_batch_date(trade_date)
        if not silent:
            print(f'[adj_factor] 写回全市场 {trade_date} {n} 行')
    except Exception as e:
        if not silent:
            print(f'[adj_factor] 写回失败 {trade_date}: {e}')
    return df


# =========================================================
# 本地技术指标派生引擎（替代 stk_factor_pro 宽表的 261 列指标）
# 输入：daily_cache + daily_basic_cache + adj_factor_cache 三表 JOIN 结果
# 公式对齐 tushare 官方 stk_factor_pro：
#   - ATR(14)/RSI(n)：Wilder 平滑 ewm(alpha=1/n)（_probe_formula_v5 已抽样验证 |diff|<0.001）
#   - MACD(12,26,9)：dif=ema12-ema26, dea=ema9(dif), macd=2*(dif-dea)
#   - updays：连续上涨天数按符号分段累计
# =========================================================

_MA_EMA_WINDOWS = (5, 10, 20, 30, 60, 90, 250)


def _wilder_rsi(close, n):
    """Wilder RSI：alpha=1/n 递归平滑；平均跌幅为 0 时取 100"""
    delta = close.diff()
    up = delta.clip(lower=0.0)
    dn = (-delta).clip(lower=0.0)
    up_avg = up.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn_avg = dn.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up_avg / dn_avg
    out = 100.0 - 100.0 / (1.0 + rs)
    return out.where(dn_avg > 0, 100.0)


def _derive_single_stock(g):
    """对单股（trade_date 升序）DataFrame 追加全部指标列，返回新 DataFrame

    三口径：bfq=未复权 / qfq=前复权(raw×adj/末因子) / hfq=后复权(raw×adj)
    新列统一收集到 dict，最后 pd.concat(axis=1) 一次性合并（避免逐列 insert 碎片化）
    """
    c = g['close'].astype(float)
    h = g['high'].astype(float)
    l = g['low'].astype(float)
    o = g['open'].astype(float)
    vol = g['vol'].astype(float) if 'vol' in g.columns else None
    new_cols = {}

    # ── 复权价：anchor=序列内最后一个有效复权因子（与 w7._qfq_price 口径一致）──
    if 'adj_factor' in g.columns:
        adj = pd.to_numeric(g['adj_factor'], errors='coerce')
    else:
        adj = pd.Series(np.nan, index=g.index)
    anchor = adj.dropna().iloc[-1] if adj.notna().any() else np.nan
    qf = (adj / anchor) if pd.notna(anchor) and anchor != 0 else pd.Series(np.nan, index=g.index)
    for col, base in (('open', o), ('high', h), ('low', l), ('close', c)):
        new_cols[col + '_qfq'] = base * qf
        new_cols[col + '_hfq'] = base * adj

    price_sets = {
        'bfq': {'high': h, 'low': l, 'close': c},
        'qfq': {'high': new_cols['high_qfq'], 'low': new_cols['low_qfq'], 'close': new_cols['close_qfq']},
        'hfq': {'high': new_cols['high_hfq'], 'low': new_cols['low_hfq'], 'close': new_cols['close_hfq']},
    }

    for basis, P in price_sets.items():
        pc = P['close']
        ph = P['high']
        pl = P['low']

        # MA / EMA 全窗口
        for w in _MA_EMA_WINDOWS:
            new_cols[f'ma_{basis}_{w}'] = pc.rolling(w, min_periods=w).mean()
            new_cols[f'ema_{basis}_{w}'] = pc.ewm(span=w, adjust=False).mean()

        # MACD(12,26,9)
        e12 = pc.ewm(span=12, adjust=False).mean()
        e26 = pc.ewm(span=26, adjust=False).mean()
        dif = e12 - e26
        dea = dif.ewm(span=9, adjust=False).mean()
        new_cols[f'macd_dif_{basis}'] = dif
        new_cols[f'macd_dea_{basis}'] = dea
        new_cols[f'macd_{basis}'] = (dif - dea) * 2.0

        # KDJ(9,3,3)：RSV→SMA(3,1)→SMA(3,1)，J=3K-2D
        hhv = ph.rolling(9, min_periods=9).max()
        llv = pl.rolling(9, min_periods=9).min()
        rng = hhv - llv
        rsv = ((pc - llv) / rng * 100.0).where(rng > 0)
        rsv = rsv.fillna(50.0)
        k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
        d = k.ewm(alpha=1 / 3, adjust=False).mean()
        new_cols[f'kdj_k_{basis}'] = k
        new_cols[f'kdj_d_{basis}'] = d
        new_cols[f'kdj_{basis}'] = 3.0 * k - 2.0 * d

        # RSI(6/12/24) Wilder
        for n in (6, 12, 24):
            new_cols[f'rsi_{basis}_{n}'] = _wilder_rsi(pc, n)

        # BOLL(20,2)：官方用总体标准差 ddof=0（反推验证 med≈3e-04，即输入价格精度极限）
        mid = pc.rolling(20, min_periods=20).mean()
        std20 = pc.rolling(20, min_periods=20).std(ddof=0)
        new_cols[f'boll_mid_{basis}'] = mid
        new_cols[f'boll_upper_{basis}'] = mid + 2.0 * std20
        new_cols[f'boll_lower_{basis}'] = mid - 2.0 * std20

        # ATR(14) Wilder
        tr = pd.concat([ph - pl, (ph - pc.shift(1)).abs(), (pl - pc.shift(1)).abs()], axis=1).max(axis=1)
        new_cols[f'atr_{basis}'] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=1).mean()

        # bias1/2/3：6/12/24 日乖离率
        for tag, w in (('1', 6), ('2', 12), ('3', 24)):
            maw = pc.rolling(w, min_periods=w).mean()
            new_cols[f'bias{tag}_{basis}'] = (pc - maw) / maw * 100.0

        # CCI(14)：Lambert 标准 MD=窗口内各点相对当前 SMA 的均值绝对偏差（与 stk_factor_pro 对齐）
        tp = (ph + pl + pc) / 3.0
        matp = tp.rolling(14, min_periods=14).mean()
        md = tp.rolling(14, min_periods=14).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
        new_cols[f'cci_{basis}'] = (tp - matp) / (0.015 * md)

        # DMI(14,6)：TR/DM 用 14 日求和（通达信式），ADX=MA(DX,6)，ADXR=(ADX+ADX[6])/2
        hd = ph - ph.shift(1)
        ld = pl.shift(1) - pl
        dmp = hd.where((hd > 0) & (hd > ld), 0.0)
        dmm = ld.where((ld > 0) & (ld > hd), 0.0)
        tr14 = tr.rolling(14, min_periods=14).sum()
        pdi = dmp.rolling(14, min_periods=14).sum() / tr14 * 100.0
        mdi = dmm.rolling(14, min_periods=14).sum() / tr14 * 100.0
        new_cols[f'dmi_pdi_{basis}'] = pdi
        new_cols[f'dmi_mdi_{basis}'] = mdi
        dx = ((mdi - pdi).abs() / (mdi + pdi) * 100.0).replace([np.inf, -np.inf], np.nan)
        adx = dx.rolling(6, min_periods=6).mean()
        new_cols[f'dmi_adx_{basis}'] = adx
        new_cols[f'dmi_adxr_{basis}'] = (adx + adx.shift(6)) / 2.0

        # WR(9) / WR1(6)
        for tag, w in (('', 9), ('1', 6)):
            hh = ph.rolling(w, min_periods=w).max()
            ll = pl.rolling(w, min_periods=w).min()
            rng2 = hh - ll
            new_cols[f'wr{tag}_{basis}'] = ((hh - pc) / rng2 * 100.0).where(rng2 > 0)

        if vol is not None:
            vol = vol.fillna(0.0)
            # VR(26)：上涨量/下跌量比率（标准 TDX 公式；stk_factor_pro 的 VR 含未公开内部
            # 修正、中位差异约 3~4，无法用标准变体复现，此处保持标准口径近似）
            dc = pc.diff()
            av = vol.where(dc > 0, 0.0).rolling(26, min_periods=26).sum()
            bv = vol.where(dc < 0, 0.0).rolling(26, min_periods=26).sum()
            cv = vol.where(dc == 0, 0.0).rolling(26, min_periods=26).sum()
            denom = bv + cv / 2.0
            new_cols[f'vr_{basis}'] = ((av + cv / 2.0) / denom * 100.0).where(denom > 0)
            # PSY(12) + PSYMA(6)
            psy = (dc > 0).astype(float).rolling(12, min_periods=12).mean() * 100.0
            new_cols[f'psy_{basis}'] = psy
            new_cols[f'psyma_{basis}'] = psy.rolling(6, min_periods=6).mean()
            # MFI(14)：官方窗口为 14（反推验证 med≈3e-06）
            tp_chg = tp.diff()
            mf = tp * vol
            pos = mf.where(tp_chg > 0, 0.0).rolling(14, min_periods=14).sum()
            neg = mf.where(tp_chg < 0, 0.0).rolling(14, min_periods=14).sum()
            new_cols[f'mfi_{basis}'] = (100.0 - 100.0 / (1.0 + pos / neg)).where(neg > 0, 100.0)
            # OBV：能量潮（官方增量口径 = ±vol×1e-4，起点为缓存首日、绝对值与官方差常数）
            new_cols[f'obv_{basis}'] = (np.sign(dc).fillna(0.0) * vol * 1e-4).cumsum()

    # ── 连涨/连跌天数（pct_chg 判定，与 stk_factor_pro 口径一致；pct 缺失时退化用 close.diff）──
    if 'pct_chg' in g.columns:
        pct = pd.to_numeric(g['pct_chg'], errors='coerce')
    else:
        pct = c.diff() / c.shift(1) * 100.0
    up = (pct > 0).astype(int)
    new_cols['updays'] = up * (up.groupby((up.diff() != 0).cumsum()).cumcount() + 1)
    dn = (pct < 0).astype(int)
    new_cols['downdays'] = dn * (dn.groupby((dn.diff() != 0).cumsum()).cumcount() + 1)

    return pd.concat([g, pd.DataFrame(new_cols, index=g.index)], axis=1)


def compute_factor_indicators(df):
    """在三表 JOIN 基础数据上本地派生技术指标列（替代 stk_factor_pro 宽表指标）

    Args:
        df: 含 ts_code/trade_date/open/high/low/close/vol/adj_factor(+daily_basic 列) 的
            DataFrame，多股时按 (ts_code, trade_date) 任意序传入，内部自动分组计算

    Returns:
        追加指标列后的 DataFrame（每股按 trade_date 升序）
    """
    if df is None or df.empty or 'ts_code' not in df.columns or 'close' not in df.columns:
        return df
    df = df.copy()
    if df['ts_code'].nunique() == 1:
        df = df.sort_values('trade_date')
        df = _derive_single_stock(df)
        return df.reset_index(drop=True)
    parts = []
    for _, gg in df.groupby('ts_code', sort=False):
        gg = gg.sort_values('trade_date')
        gg = _derive_single_stock(gg)
        parts.append(gg)
    return pd.concat(parts, ignore_index=True)


# =========================================================
# 窄表三表兼容层（替代旧 stk_factor_pro 宽表读取）
# daily_cache + daily_basic_cache + adj_factor_cache 三表 LEFT JOIN
# -> compute_factor_indicators 本地派生指标（列名与旧宽表保持一致）
# =========================================================

_COMPAT_IND_CACHE = {}
_COMPAT_IND_CACHE_CAP = 96
_COMPAT_FETCH_ATTEMPTED = set()


def _merge_three_tables(ts_code, start_date=None, end_date=None):
    """三表 LEFT JOIN（daily_cache 为主表）读取单股行情+估值+复权因子"""
    if not _table_exists(DAILY_CACHE_TABLE):
        return None
    sql = f'''
        SELECT d.*,
               b.turnover_rate, b.turnover_rate_f, b.volume_ratio,
               b.total_mv, b.circ_mv, b.pe, b.pe_ttm, b.pb, b.ps,
               b.dv_ttm, b.float_share, b.total_share,
               a.adj_factor
        FROM {DAILY_CACHE_TABLE} d
        LEFT JOIN {DAILY_BASIC_CACHE_TABLE} b
               ON d.ts_code = b.ts_code AND d.trade_date = b.trade_date
        LEFT JOIN {ADJ_FACTOR_CACHE_TABLE} a
               ON d.ts_code = a.ts_code AND d.trade_date = a.trade_date
        WHERE d.ts_code = ?
    '''
    params = [ts_code]
    if start_date:
        sql += ' AND d.trade_date >= ?'
        params.append(str(start_date))
    if end_date:
        sql += ' AND d.trade_date <= ?'
        params.append(str(end_date))
    sql += ' ORDER BY d.trade_date'
    with get_conn() as conn:
        df = pd.read_sql_query(sql, conn, params=params)
    return df if not df.empty else None


def cached_stk_factor_compat(ts_code, start_date, end_date, pro=None, silent=False):
    """窄表三表兼容层：以 daily_cache/daily_basic_cache/adj_factor_cache 为唯一数据源，
    本地派生 stk_factor_pro 全部指标列，完全替代旧宽表读取

    补数策略（与 cached_adj_factor 相同模式）：
      ① 目标日缺失 → 整市场按日补（daily_market/daily_basic_market/adj_factor_market）
      ② 历史段缺口 → 单股增量拉（pro.daily/pro.daily_basic/pro.adj_factor）
    指标计算以该股缓存最早日期为起点（保证 EMA/Wilder 平滑收敛与 OBV 累计口径），
    返回时切回请求区间；列名与旧宽表一致（ma_bfq_5/macd_dif_bfq/kdj_bfq/rsi_bfq_6...）。

    Args:
        silent: True 时抑制日志（保留参数以兼容旧调用方）
    """
    ts_code = str(ts_code)
    start_date, end_date = str(start_date), str(end_date)
    _ensure_daily_cache_table()
    _ensure_daily_basic_table()
    _ensure_adj_factor_table()

    list_date = get_list_date(ts_code)
    required_min = str(list_date) if (list_date and str(list_date) > start_date) else start_date

    def _ranges():
        out = []
        for getter in (get_daily_cache_range, get_daily_basic_range, get_adj_factor_range):
            try:
                out.append(getter(ts_code))
            except Exception:
                out.append((None, None))
        return out

    def _covered(rngs):
        return all(mn is not None and mx is not None
                   and mn <= required_min and mx >= end_date for mn, mx in rngs)

    def _memo_key():
        return (ts_code,) + tuple(r[1] or '' for r in _ranges())

    def _slice_return(df_full):
        mask = (df_full['trade_date'] >= start_date) & (df_full['trade_date'] <= end_date)
        out = df_full.loc[mask]
        return out.reset_index(drop=True) if not out.empty else None

    def _compute_and_store():
        df_full = _merge_three_tables(ts_code, end_date=end_date)
        if df_full is None:
            return None
        df_full = compute_factor_indicators(df_full)
        _COMPAT_IND_CACHE[_memo_key()] = df_full
        if len(_COMPAT_IND_CACHE) > _COMPAT_IND_CACHE_CAP:
            _COMPAT_IND_CACHE.pop(next(iter(_COMPAT_IND_CACHE)))
        return _slice_return(df_full)

    # ① 进程内指标缓存命中（数据版本 cmax 未变时免重算）
    hit = _COMPAT_IND_CACHE.get(_memo_key())
    if hit is not None and len(hit) and hit['trade_date'].iloc[-1] >= end_date:
        return _slice_return(hit)

    # ② 缓存已覆盖请求区间 → 直接读并派生
    if _covered(_ranges()):
        return _compute_and_store()

    _pro = pro or _get_pro()

    # ③ 目标日缺失 → 整市场按日补一次（内部自带完整性检查，命中时开销仅为 COUNT）
    for fill in (daily_market, daily_basic_market, adj_factor_market):
        try:
            fill(end_date, pro=_pro, silent=True)
        except Exception:
            pass

    if _covered(_ranges()):
        return _compute_and_store()

    # ④ 历史段缺口 → 单股增量拉（三表各自补各自缺口；同一缺口进程内只尝试一次）
    if (ts_code, required_min, end_date) not in _COMPAT_FETCH_ATTEMPTED:
        _COMPAT_FETCH_ATTEMPTED.add((ts_code, required_min, end_date))
        for rng_getter, inserter, api_call in (
            (get_daily_cache_range, batch_insert_daily_cache,
             lambda a, b: _pro.daily(ts_code=ts_code, start_date=a, end_date=b, fields=_DAILY_CACHE_COLS)),
            (get_daily_basic_range, batch_insert_daily_basic,
             lambda a, b: _pro.daily_basic(ts_code=ts_code, start_date=a, end_date=b, fields=','.join(_DAILY_BASIC_COLS))),
            (get_adj_factor_range, batch_insert_adj_factor,
             lambda a, b: _pro.adj_factor(ts_code=ts_code, start_date=a, end_date=b)),
        ):
            try:
                mn, mx = rng_getter(ts_code)
                if mn and mx and mn <= required_min and mx >= end_date:
                    continue
                fetch_start = mx if (mn and mx and mn <= required_min) else required_min
                df_new = api_call(fetch_start, end_date)
                time.sleep(0.06)
                if df_new is not None and not df_new.empty:
                    inserter(df_new)
            except Exception:
                continue

    return _compute_and_store()


def cache_status(ts_code=None, trade_date=None):
    """UDC④ 缓存状态查询（诊断用）

    Args:
        ts_code:    传入则查单股缓存范围
        trade_date: 传入则查当日全市场缓存条数

    Returns:
        dict，包含缓存覆盖信息

    用法:
        from stock_cache import cache_status
        cache_status(ts_code='000001.SZ')
        cache_status(trade_date='20260821')
    """
    info = {'db_path': DB_PATH}
    if ts_code:
        info['ts_code'] = ts_code
        info['daily_cache_range'] = get_daily_cache_range(ts_code)
        info['daily_basic_range'] = get_daily_basic_range(ts_code)
        info['adj_factor_range'] = get_adj_factor_range(ts_code)
    if trade_date:
        info['trade_date'] = trade_date
        info['daily_cache_count'] = get_daily_by_date_count(trade_date)
    return info


def udc_stats():
    """UDC⑤ 缓存全貌：日期范围、股票数、总行数、最近 5 个交易日覆盖

    用法:
        from stock_cache import udc_stats
        print(udc_stats())
    """
    if not _table_exists(DAILY_CACHE_TABLE):
        return {'exists': False}
    with get_conn() as conn:
        overall = conn.execute(
            f'SELECT MIN(trade_date), MAX(trade_date), COUNT(DISTINCT ts_code), COUNT(*) FROM {DAILY_CACHE_TABLE}'
        ).fetchone()
        recent = conn.execute(
            f'SELECT trade_date, COUNT(*) FROM {DAILY_CACHE_TABLE} '
            f'GROUP BY trade_date ORDER BY trade_date DESC LIMIT 5'
        ).fetchall()
    return {
        'exists': True,
        'date_range': (overall[0], overall[1]),
        'stock_count': overall[2],
        'total_rows': overall[3],
        'recent_days': [(d, c) for d, c in recent],
    }


def cached_stk_factor_pro(ts_code, start_date, end_date, silent=False):
    """兼容 wrapper（旧接口保留）：转发到窄表三表兼容层 cached_stk_factor_compat

    旧 stk_factor_pro 宽表已废弃，不再读写；本函数仅为存量调用方保留签名。
    全部调用方迁移到 cached_stk_factor_compat 后可删除。
    """
    return cached_stk_factor_compat(ts_code, start_date, end_date, silent=silent)


# =========================================================
# 窄表批量读取公共辅助（替代各下游对 stk_factor_pro 的直查 SQL）
# 全市场/股票池级别的当日与区间读取，按需派生 close_hfq 与技术指标列
# =========================================================

_THREE_TABLE_COLS = frozenset((
    'ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'pre_close',
    'change', 'pct_chg', 'vol', 'amount',
    'turnover_rate', 'turnover_rate_f', 'volume_ratio', 'total_mv', 'circ_mv',
    'pe', 'pe_ttm', 'pb', 'ps', 'dv_ttm', 'float_share', 'total_share',
    'adj_factor',
))
_DERIVED_PRICE_COLS = frozenset((
    'close_hfq', 'open_hfq', 'high_hfq', 'low_hfq',
    'close_qfq', 'open_qfq', 'high_qfq', 'low_qfq',
))

# 指标列 → 窗口化计算所需预热交易日数；表外递归/累计类指标按 250 近似
_IND_WARMUP = {}
for _w in _MA_EMA_WINDOWS:
    for _b in ('bfq', 'qfq', 'hfq'):
        _IND_WARMUP[f'ma_{_b}_{_w}'] = _w
for _b in ('bfq', 'qfq', 'hfq'):
    _IND_WARMUP.update({
        f'boll_mid_{_b}': 20, f'boll_upper_{_b}': 20, f'boll_lower_{_b}': 20,
        f'kdj_k_{_b}': 20, f'kdj_d_{_b}': 20, f'kdj_{_b}': 20,
        f'wr_{_b}': 9, f'wr1_{_b}': 6,
        f'bias1_{_b}': 6, f'bias2_{_b}': 12, f'bias3_{_b}': 24,
        f'cci_{_b}': 30, f'psy_{_b}': 12, f'psyma_{_b}': 20,
        f'vr_{_b}': 26, f'mfi_{_b}': 6, f'atr_{_b}': 40,
    })
del _w, _b


def _indicator_warmup(cols):
    """cols 中指标列所需的最大预热窗口（交易日）"""
    w = 0
    for c in cols or ():
        if c in _THREE_TABLE_COLS or c in _DERIVED_PRICE_COLS:
            continue
        w = max(w, _IND_WARMUP.get(c, 250))
    return w


def _expand_trade_dates_before(anchor_date, n):
    """取 daily_cache 中 <= anchor_date 的最近 n 个不同交易日中最早的一个（窗口化起点）"""
    with get_conn() as conn:
        rows = conn.execute(
            f'SELECT DISTINCT trade_date FROM {DAILY_CACHE_TABLE} '
            f'WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT ?',
            (str(anchor_date), int(n))).fetchall()
    return rows[-1][0] if rows else None


def get_recent_trade_dates(n=250, end_date=None):
    """取最近 n 个交易日（<= end_date，升序返回；无数据返回空列表）

    替代旧 SELECT DISTINCT trade_date FROM stk_factor_pro 读取路径
    """
    if not _table_exists(DAILY_CACHE_TABLE):
        return []
    sql = f'SELECT DISTINCT trade_date FROM {DAILY_CACHE_TABLE}'
    params = []
    if end_date:
        sql += ' WHERE trade_date <= ?'
        params.append(str(end_date))
    sql += ' ORDER BY trade_date DESC LIMIT ?'
    params.append(int(n))
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return sorted(r[0] for r in rows)


def fetch_market_amounts_by_date(start_date, end_date):
    """按日聚合全市场总成交额（替代旧 stk_factor_pro GROUP BY trade_date 聚合读取）

    Returns:
        DataFrame[trade_date, total_amount]（无数据时为空表）
    """
    if not _table_exists(DAILY_CACHE_TABLE):
        return pd.DataFrame()
    with get_conn() as conn:
        df = pd.read_sql_query(
            f'SELECT trade_date, SUM(CAST(amount AS REAL)) AS total_amount '
            f'FROM {DAILY_CACHE_TABLE} '
            f'WHERE trade_date >= ? AND trade_date <= ? '
            f'GROUP BY trade_date ORDER BY trade_date',
            conn, params=(str(start_date), str(end_date)))
    return df


def get_all_cached_ts_codes():
    """全市场已缓存股票代码列表（替代旧 SELECT DISTINCT ts_code FROM stk_factor_pro）"""
    if not _table_exists(DAILY_CACHE_TABLE):
        return []
    with get_conn() as conn:
        rows = conn.execute(f'SELECT DISTINCT ts_code FROM {DAILY_CACHE_TABLE}').fetchall()
    return [r[0] for r in rows]


def _read_three_tables_range(ts_codes=None, start_date=None, end_date=None):
    """窄表三表 LEFT JOIN 批量读取（全市场或指定股票池 × 可选日期区间）"""
    if not _table_exists(DAILY_CACHE_TABLE):
        return None
    sql = (
        f'SELECT d.ts_code, d.trade_date, d.open, d.high, d.low, d.close, d.pre_close, '
        f'd.change, d.pct_chg, d.vol, d.amount, '
        f'b.turnover_rate, b.turnover_rate_f, b.volume_ratio, b.total_mv, b.circ_mv, '
        f'b.pe, b.pe_ttm, b.pb, b.ps, b.dv_ttm, b.float_share, b.total_share, '
        f'a.adj_factor '
        f'FROM {DAILY_CACHE_TABLE} d '
        f'LEFT JOIN {DAILY_BASIC_CACHE_TABLE} b '
        f'ON d.ts_code = b.ts_code AND d.trade_date = b.trade_date '
        f'LEFT JOIN {ADJ_FACTOR_CACHE_TABLE} a '
        f'ON d.ts_code = a.ts_code AND d.trade_date = a.trade_date'
    )
    conds, params = [], []
    if start_date:
        conds.append('d.trade_date >= ?')
        params.append(str(start_date))
    if end_date:
        conds.append('d.trade_date <= ?')
        params.append(str(end_date))

    def _finalize(extra_conds):
        all_conds = conds + list(extra_conds)
        return sql + (' WHERE ' + ' AND '.join(all_conds) if all_conds else '')

    frames = []
    with get_conn() as conn:
        if ts_codes:
            codes = [str(c) for c in ts_codes]
            for i in range(0, len(codes), 500):
                chunk = codes[i:i + 500]
                part = _finalize([f'd.ts_code IN ({",".join("?" * len(chunk))})'])
                frames.append(pd.read_sql_query(part, conn, params=params + chunk))
        else:
            frames.append(pd.read_sql_query(_finalize([]), conn, params=params))
    frames = [f for f in frames if f is not None and not f.empty]
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


def _derive_price_cols(df, want_cols):
    """复权价格列快捷派生（向量计算，口径与 compute_factor_indicators 一致）

    hfq = 原价 × adj_factor（逐行）；qfq = 原价 × adj_factor / 组内末个有效因子
    """
    if 'adj_factor' not in df.columns:
        return df
    adj = pd.to_numeric(df['adj_factor'], errors='coerce')
    need_qfq = any(c.endswith('_qfq') for c in want_cols)
    qf = None
    if need_qfq and 'ts_code' in df.columns:
        anchor = adj.groupby(df['ts_code']).transform('last')
        qf = adj / anchor.where(anchor != 0)
    base_map = {'open': 'open', 'high': 'high', 'low': 'low', 'close': 'close'}
    for c in want_cols:
        stem, _, suffix = c.rpartition('_')
        base = base_map.get(stem)
        if base is None or base not in df.columns:
            continue
        px = pd.to_numeric(df[base], errors='coerce')
        if suffix == 'hfq':
            df[c] = px * adj
        elif suffix == 'qfq' and qf is not None:
            df[c] = px * qf
    return df


def _post_derive_cols(df, cols):
    """按需派生 close_hfq/技术指标列并裁剪输出列"""
    want = [c for c in (cols or ())]
    extra = [c for c in want if c not in _THREE_TABLE_COLS]
    if extra:
        if all(c in _DERIVED_PRICE_COLS for c in extra):
            df = _derive_price_cols(df, extra)
        else:
            df = compute_factor_indicators(df)
    if want:
        keep = [c for c in ('ts_code', 'trade_date') if c in df.columns]
        keep += [c for c in want if c in df.columns and c not in keep]
        if keep:
            df = df[keep]
    return df


def fetch_market_by_date(trade_date, ts_codes=None, cols=None):
    """读取指定交易日全市场（或指定股票池）窄表数据，按需派生指标列

    替代旧 stk_factor_pro 当日直查（SELECT ... WHERE trade_date = ?）。
    cols 含技术指标列（ma_bfq_20/ma_bfq_250 等）时自动向前扩展预热窗口再裁回当日，
    指标口径与全历史计算一致；cols 为 None 时返回全部基础列。

    Returns:
        DataFrame（无数据/表不存在时为空表）
    """
    trade_date = str(trade_date)
    warmup = _indicator_warmup(cols)
    if warmup:
        ext_start = _expand_trade_dates_before(trade_date, warmup)
        if ext_start is None:
            return pd.DataFrame()
        df = _read_three_tables_range(ts_codes, ext_start, trade_date)
    else:
        df = _read_three_tables_range(ts_codes, trade_date, trade_date)
    if df is None or df.empty:
        return pd.DataFrame()
    df = _post_derive_cols(df, cols)
    if warmup:
        df = df[df['trade_date'] == trade_date].reset_index(drop=True)
    return df


def fetch_hist_range(start_date, end_date, ts_codes=None, cols=None):
    """读取历史区间窄表数据（全市场或指定股票池），按需派生指标列

    替代旧 stk_factor_pro 区间直查（WHERE trade_date BETWEEN ...）。
    cols 含技术指标列时自动把起点向前扩展预热窗口，计算后裁剪回请求区间，
    保证区间内每日指标与全历史计算口径一致（ema/macd/rsi 等递归类为近似口径）。

    Returns:
        DataFrame（无数据/表不存在时为空表）
    """
    warmup = _indicator_warmup(cols)
    ext_start = start_date
    if warmup and start_date:
        ext_start = _expand_trade_dates_before(start_date, warmup) or start_date
    df = _read_three_tables_range(ts_codes, ext_start, end_date)
    if df is None or df.empty:
        return pd.DataFrame()
    df = _post_derive_cols(df, cols)
    if warmup and start_date:
        df = df[(df['trade_date'] >= str(start_date))
                & (df['trade_date'] <= str(end_date))].reset_index(drop=True)
    return df


# ═══════════════════════════════════════════════════════
# 数据库维护：VACUUM 压缩 + 缓存清理
# ═══════════════════════════════════════════════════════

def vacuum_database():
    """VACUUM 压缩 stock_data.db，回收碎片空间（建议每月执行一次）

    注意：VACUUM 需要数据库独占访问，执行期间无法读写。
    """
    import time as _time
    before = os.path.getsize(DB_PATH) / (1024 ** 3)
    t0 = _time.time()
    with get_conn() as conn:
        conn.execute('PRAGMA journal_mode=DELETE')
        conn.execute('VACUUM')
    after = os.path.getsize(DB_PATH) / (1024 ** 3)
    print(f'[VACUUM] {before:.2f} GB -> {after:.2f} GB (释放 {before-after:.2f} GB, 耗时 {_time.time()-t0:.1f}s)')


def cleanup_old_cache(keep_days=5):
    """清理过期缓存文件（建议每日收盘后执行）

    Args:
        keep_days: 保留最近 N 天的缓存文件
    """
    import glob as _glob
    import time as _time
    cutoff = _time.time() - keep_days * 86400
    removed = 0
    freed_mb = 0.0

    # 1. 清理 theme_stock_map 历史版本（保留 latest + 最近 keep_days 天）
    for f in _glob.glob(os.path.join(CACHE_DIR, 'theme_stock_map_v*_*.json')):
        try:
            if os.path.getmtime(f) < cutoff:
                sz = os.path.getsize(f)
                os.remove(f)
                removed += 1
                freed_mb += sz / (1024 ** 2)
        except Exception:
            pass

    # 2. 清理过期的择时结果 CSV（enhanced_timing_bull_all_YYYYMMDD.csv）
    for f in _glob.glob(os.path.join(CACHE_DIR, 'enhanced_timing_bull_all_*.csv')):
        try:
            if os.path.getmtime(f) < cutoff:
                sz = os.path.getsize(f)
                os.remove(f)
                removed += 1
                freed_mb += sz / (1024 ** 2)
        except Exception:
            pass

    # 3. 清理过期的 VolMaSync 结果 CSV
    for f in _glob.glob(os.path.join(CACHE_DIR, 'VolMaSync_Stocks_*.csv')):
        try:
            if os.path.getmtime(f) < cutoff:
                sz = os.path.getsize(f)
                os.remove(f)
                removed += 1
                freed_mb += sz / (1024 ** 2)
        except Exception:
            pass

    # 4. 清理 Parquet 缓存目录中过期文件（统一 PARQUET_DIR）
    try:
        from cache_config import PARQUET_DIR
        for f in _glob.glob(os.path.join(PARQUET_DIR, '*.parquet')):
            try:
                if os.path.getmtime(f) < cutoff:
                    sz = os.path.getsize(f)
                    os.remove(f)
                    removed += 1
                    freed_mb += sz / (1024 ** 2)
            except Exception:
                pass
    except Exception:
        pass

    if removed > 0:
        print(f'[清理] 删除 {removed} 个过期文件, 释放 {freed_mb:.1f} MB')
    else:
        print('[清理] 无过期文件')
    return removed


# ═══════════════════════════════════════════════════════
# 快捷测试
# ═══════════════════════════════════════════════════════

if __name__ == '__main__':
    print('=== stock_cache 自检（窄表三表） ===')
    print(f'DB 路径: {DB_PATH}')
    print(f'daily_cache 表存在: {_table_exists(DAILY_CACHE_TABLE)}')
    print(f'daily_basic_cache 表存在: {_table_exists(DAILY_BASIC_CACHE_TABLE)}')
    print(f'adj_factor_cache 表存在: {_table_exists(ADJ_FACTOR_CACHE_TABLE)}')
    print('========================')
