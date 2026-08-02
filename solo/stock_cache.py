import sqlite3
import pandas as pd
import os
import time
import datetime
from contextlib import contextmanager

CACHE_DIR = r"D:\mystock\cache_daily"
DB_PATH = os.path.join(CACHE_DIR, "stock_data.db")

# Tushare API 懒初始化（调用方需确保已设置 TUSHARE_TOKEN 环境变量）
import tushare as ts
_pro = None
def _get_pro():
    global _pro
    if _pro is None:
        _pro = ts.pro_api()
    return _pro

# ═══════════════════════════════════════════════════════
# 缓存状态管理（替代 txt 文件+全局变量混乱局面）
# ═══════════════════════════════════════════════════════

# 批量下载日期（读/写 sc.meta 表）——替代 txt 文件和全局变量
_STK_FACTOR_BATCH_STATUS_KEY = 'stk_factor_batch_date'

def get_batch_date():
    """获取已批量缓存的日期"""
    return get_meta(_STK_FACTOR_BATCH_STATUS_KEY, '')

def set_batch_date(date_str):
    """设置已批量缓存的日期"""
    set_meta(_STK_FACTOR_BATCH_STATUS_KEY, date_str)

# 已补充缓存记录的集合（避免重复日志），进程内有效，不持久化
_cache_supplement_completed = set()


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
    for attempt in range(max_retries):
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        try:
            yield conn
            conn.commit()
            return
        except sqlite3.OperationalError as e:
            if 'database is locked' in str(e) and attempt < max_retries - 1:
                conn.close()
                import time
                time.sleep(retry_delay * (attempt + 1))
                continue
            conn.rollback()
            raise
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
# stk_factor_pro 表操作
# =========================================================

STK_FACTOR_TABLE = 'stk_factor_pro'


def init_stk_factor_table(sample_df=None):
    """初始化 stk_factor_pro 表"""
    if _table_exists(STK_FACTOR_TABLE):
        return
    if sample_df is not None:
        _ensure_table_from_df(sample_df, STK_FACTOR_TABLE)
        _create_stk_factor_indexes()


def _create_stk_factor_indexes():
    """创建辅助索引"""
    with get_conn() as conn:
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{STK_FACTOR_TABLE}_trade_date ON {STK_FACTOR_TABLE}(trade_date)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{STK_FACTOR_TABLE}_total_mv ON {STK_FACTOR_TABLE}(total_mv)")


def get_stk_factor_pro(ts_code, start_date, end_date):
    """查询单股某日期范围的数据，返回 DataFrame（按 trade_date 升序）"""
    if not _table_exists(STK_FACTOR_TABLE):
        return None
    with get_conn() as conn:
        df = pd.read_sql_query(
            f'SELECT * FROM {STK_FACTOR_TABLE} WHERE ts_code = ? AND trade_date BETWEEN ? AND ? ORDER BY trade_date',
            conn, params=(ts_code, str(start_date), str(end_date))
        )
    if df.empty:
        return None
    return df.reset_index(drop=True)


def get_stk_factor_range(ts_code):
    """获取某股票的缓存日期范围 (min_date, max_date)，无数据返回 (None, None)"""
    if not _table_exists(STK_FACTOR_TABLE):
        return (None, None)
    with get_conn() as conn:
        row = conn.execute(
            f'SELECT MIN(trade_date), MAX(trade_date) FROM {STK_FACTOR_TABLE} WHERE ts_code = ?',
            (ts_code,)
        ).fetchone()
    if row is None or row[0] is None:
        return (None, None)
    return (str(row[0]), str(row[1]))


def get_list_date_from_cache(ts_code):
    """从缓存中获取上市日期（取最早交易日期）"""
    if not _table_exists(STK_FACTOR_TABLE):
        return None
    with get_conn() as conn:
        row = conn.execute(
            f'SELECT MIN(trade_date) FROM {STK_FACTOR_TABLE} WHERE ts_code = ?',
            (ts_code,)
        ).fetchone()
    return str(row[0]) if row and row[0] else None


def get_last_adj_factor(ts_code):
    """获取某股票最后一条记录的复权因子（用于除权检测）"""
    if not _table_exists(STK_FACTOR_TABLE):
        return None
    with get_conn() as conn:
        row = conn.execute(
            f'SELECT adj_factor FROM {STK_FACTOR_TABLE} WHERE ts_code = ? ORDER BY trade_date DESC LIMIT 1',
            (ts_code,)
        ).fetchone()
    return row[0] if row and row[0] is not None else None


# =========================================================
# daily_cache 表：专门存储 pro.daily 的 11 列基础行情数据
# 与 stk_factor_pro 表（261列技术指标）隔离，避免 INSERT OR REPLACE 破坏技术指标
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


def batch_insert_stk_factor_pro(df_all):
    """批量插入/更新 stk_factor_pro 数据（INSERT OR REPLACE，keep='last' 语义）
    
    Args:
        df_all: DataFrame，包含所有股票的数据
    
    Returns:
        插入/更新的行数
    """
    if df_all is None or df_all.empty:
        return 0
    
    # 确保表存在
    if not _table_exists(STK_FACTOR_TABLE):
        _ensure_table_from_df(df_all, STK_FACTOR_TABLE)
        _create_stk_factor_indexes()
    
    cols = list(df_all.columns)
    placeholders = ','.join(['?'] * len(cols))
    col_str = ','.join([f'"{c}"' for c in cols])
    sql = f'INSERT OR REPLACE INTO {STK_FACTOR_TABLE} ({col_str}) VALUES ({placeholders})'
    
    # 处理 NaN -> None，否则 SQLite 会报错
    values = [
        [None if pd.isna(v) else v for v in row]
        for row in df_all[cols].values.tolist()
    ]
    
    with get_conn() as conn:
        conn.executemany(sql, values)
    
    return len(values)


def delete_stk_factor_stock(ts_code):
    """删除某只股票的全部 stk_factor_pro 数据（除权时触发）"""
    if not _table_exists(STK_FACTOR_TABLE):
        return
    with get_conn() as conn:
        conn.execute(f'DELETE FROM {STK_FACTOR_TABLE} WHERE ts_code = ?', (ts_code,))


def count_stk_factor_stocks():
    """统计缓存了多少只股票"""
    if not _table_exists(STK_FACTOR_TABLE):
        return 0
    with get_conn() as conn:
        row = conn.execute(f'SELECT COUNT(DISTINCT ts_code) FROM {STK_FACTOR_TABLE}').fetchone()
    return row[0] if row else 0


def count_stk_factor_rows():
    """统计总记录数"""
    if not _table_exists(STK_FACTOR_TABLE):
        return 0
    with get_conn() as conn:
        row = conn.execute(f'SELECT COUNT(*) FROM {STK_FACTOR_TABLE}').fetchone()
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


# =========================================================
# CSV -> SQLite 迁移工具
# =========================================================

def migrate_csv_to_sqlite(csv_dir=None, pattern='stk_pro_*.csv', table_name=STK_FACTOR_TABLE, batch_size=50):
    """将 CSV 缓存批量导入 SQLite
    
    Args:
        csv_dir: CSV 文件目录，默认 CACHE_DIR
        pattern: 文件匹配模式
        table_name: 目标表名
        batch_size: 多少只股票提交一次事务
    
    Returns:
        (stock_count, total_rows)
    """
    import glob
    
    if csv_dir is None:
        csv_dir = CACHE_DIR
    
    csv_files = glob.glob(os.path.join(csv_dir, pattern))
    if not csv_files:
        print(f'[迁移] 未找到匹配 {pattern} 的文件')
        return (0, 0)
    
    print(f'[迁移] 找到 {len(csv_files)} 个 CSV 文件，开始导入...')
    
    total_stocks = 0
    total_rows = 0
    batch_dfs = []
    
    for i, csv_file in enumerate(csv_files):
        try:
            df = pd.read_csv(csv_file)
            if df.empty:
                continue
            if 'trade_date' in df.columns:
                df['trade_date'] = df['trade_date'].astype(str)
            batch_dfs.append(df)
            total_stocks += 1
            total_rows += len(df)
            
            # 批量提交
            if len(batch_dfs) >= batch_size:
                combined = pd.concat(batch_dfs, ignore_index=True)
                batch_insert_stk_factor_pro(combined)
                batch_dfs = []
                print(f'[迁移] 已处理 {total_stocks}/{len(csv_files)} 只股票，累计 {total_rows} 行...')
        except Exception as e:
            print(f'[迁移] 失败 {os.path.basename(csv_file)}: {e}')
    
    # 处理剩余
    if batch_dfs:
        combined = pd.concat(batch_dfs, ignore_index=True)
        batch_insert_stk_factor_pro(combined)
    
    print(f'[迁移] 完成！共 {total_stocks} 只股票，{total_rows} 行数据')
    return (total_stocks, total_rows)


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
    """带缓存的 pro.daily() 调用（V3: 使用独立的 daily_cache 表，避免破坏 stk_factor_pro 技术指标）

    daily_cache 表仅存储 pro.daily 的 11 列基础行情数据（open/high/low/close/vol/amount 等），
    与 stk_factor_pro 表（261列技术指标）隔离，INSERT OR REPLACE 不会破坏技术指标数据。
    """
    # 优先从 daily_cache 读取
    df = get_daily_cache(ts_code, start_date, end_date)
    if df is not None and not df.empty:
        return df.sort_values('trade_date').reset_index(drop=True)
    # 缓存未命中，调用 API 并写入 daily_cache
    pro = pro or _get_pro()
    df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
    time.sleep(0.06)
    if df is None or df.empty:
        return None
    df['trade_date'] = df['trade_date'].astype(str)
    batch_insert_daily_cache(df)
    return df.sort_values('trade_date').reset_index(drop=True)


# ── stk_factor_pro 共享字段列表（261个字段，tushare_quant 完备版）──
_STK_FACTOR_FIELDS = [
    "ts_code", "trade_date", "open", "open_hfq", "open_qfq",
    "high", "high_hfq", "high_qfq", "low", "low_hfq", "low_qfq",
    "close", "close_hfq", "close_qfq", "pre_close", "change", "pct_chg",
    "vol", "amount", "turnover_rate", "turnover_rate_f", "volume_ratio",
    "pe", "pe_ttm", "pb", "ps", "ps_ttm", "dv_ratio", "dv_ttm",
    "total_share", "float_share", "free_share", "total_mv", "circ_mv", "adj_factor",
    "asi_bfq", "asi_hfq", "asi_qfq", "asit_bfq", "asit_hfq", "asit_qfq",
    "atr_bfq", "atr_hfq", "atr_qfq", "bbi_bfq", "bbi_hfq", "bbi_qfq",
    "bias1_bfq", "bias1_hfq", "bias1_qfq",
    "bias2_bfq", "bias2_hfq", "bias2_qfq",
    "bias3_bfq", "bias3_hfq", "bias3_qfq",
    "boll_lower_bfq", "boll_lower_hfq", "boll_lower_qfq",
    "boll_mid_bfq", "boll_mid_hfq", "boll_mid_qfq",
    "boll_upper_bfq", "boll_upper_hfq", "boll_upper_qfq",
    "brar_ar_bfq", "brar_ar_hfq", "brar_ar_qfq",
    "brar_br_bfq", "brar_br_hfq", "brar_br_qfq",
    "cci_bfq", "cci_hfq", "cci_qfq", "cr_bfq", "cr_hfq", "cr_qfq",
    "dfma_dif_bfq", "dfma_dif_hfq", "dfma_dif_qfq",
    "dfma_difma_bfq", "dfma_difma_hfq", "dfma_difma_qfq",
    "dmi_adx_bfq", "dmi_adx_hfq", "dmi_adx_qfq",
    "dmi_adxr_bfq", "dmi_adxr_hfq", "dmi_adxr_qfq",
    "dmi_mdi_bfq", "dmi_mdi_hfq", "dmi_mdi_qfq",
    "dmi_pdi_bfq", "dmi_pdi_hfq", "dmi_pdi_qfq",
    "downdays", "updays", "dpo_bfq", "dpo_hfq", "dpo_qfq",
    "madpo_bfq", "madpo_hfq", "madpo_qfq",
    "ema_bfq_10", "ema_bfq_20", "ema_bfq_250", "ema_bfq_30", "ema_bfq_5", "ema_bfq_60", "ema_bfq_90",
    "ema_hfq_10", "ema_hfq_20", "ema_hfq_250", "ema_hfq_30", "ema_hfq_5", "ema_hfq_60", "ema_hfq_90",
    "ema_qfq_10", "ema_qfq_20", "ema_qfq_250", "ema_qfq_30", "ema_qfq_5", "ema_qfq_60", "ema_qfq_90",
    "emv_bfq", "emv_hfq", "emv_qfq", "maemv_bfq", "maemv_hfq", "maemv_qfq",
    "expma_12_bfq", "expma_12_hfq", "expma_12_qfq",
    "expma_50_bfq", "expma_50_hfq", "expma_50_qfq",
    "kdj_bfq", "kdj_hfq", "kdj_qfq",
    "kdj_d_bfq", "kdj_d_hfq", "kdj_d_qfq",
    "kdj_k_bfq", "kdj_k_hfq", "kdj_k_qfq",
    "ktn_down_bfq", "ktn_down_hfq", "ktn_down_qfq",
    "ktn_mid_bfq", "ktn_mid_hfq", "ktn_mid_qfq",
    "ktn_upper_bfq", "ktn_upper_hfq", "ktn_upper_qfq",
    "lowdays", "topdays",
    "ma_bfq_10", "ma_bfq_20", "ma_bfq_250", "ma_bfq_30", "ma_bfq_5", "ma_bfq_60", "ma_bfq_90",
    "ma_hfq_10", "ma_hfq_20", "ma_hfq_250", "ma_hfq_30", "ma_hfq_5", "ma_hfq_60", "ma_hfq_90",
    "ma_qfq_10", "ma_qfq_20", "ma_qfq_250", "ma_qfq_30", "ma_qfq_5", "ma_qfq_60", "ma_qfq_90",
    "macd_bfq", "macd_hfq", "macd_qfq",
    "macd_dea_bfq", "macd_dea_hfq", "macd_dea_qfq",
    "macd_dif_bfq", "macd_dif_hfq", "macd_dif_qfq",
    "mass_bfq", "mass_hfq", "mass_qfq",
    "ma_mass_bfq", "ma_mass_hfq", "ma_mass_qfq",
    "mfi_bfq", "mfi_hfq", "mfi_qfq",
    "mtm_bfq", "mtm_hfq", "mtm_qfq",
    "mtmma_bfq", "mtmma_hfq", "mtmma_qfq",
    "obv_bfq", "obv_hfq", "obv_qfq",
    "psy_bfq", "psy_hfq", "psy_qfq",
    "psyma_bfq", "psyma_hfq", "psyma_qfq",
    "roc_bfq", "roc_hfq", "roc_qfq",
    "maroc_bfq", "maroc_hfq", "maroc_qfq",
    "rsi_bfq_12", "rsi_bfq_24", "rsi_bfq_6",
    "rsi_hfq_12", "rsi_hfq_24", "rsi_hfq_6",
    "rsi_qfq_6", "rsi_qfq_12", "rsi_qfq_24",
    "taq_down_bfq", "taq_down_hfq", "taq_down_qfq",
    "taq_mid_bfq", "taq_mid_hfq", "taq_mid_qfq",
    "taq_up_bfq", "taq_up_hfq", "taq_up_qfq",
    "trix_bfq", "trix_hfq", "trix_qfq",
    "trma_bfq", "trma_hfq", "trma_qfq",
    "vr_bfq", "vr_hfq", "vr_qfq",
    "wr_bfq", "wr_hfq", "wr_qfq",
    "wr1_bfq", "wr1_hfq", "wr1_qfq",
    "xsii_td1_bfq", "xsii_td1_hfq", "xsii_td1_qfq",
    "xsii_td2_bfq", "xsii_td2_hfq", "xsii_td2_qfq",
    "xsii_td3_bfq", "xsii_td3_hfq", "xsii_td3_qfq",
    "xsii_td4_bfq", "xsii_td4_hfq", "xsii_td4_qfq"
]

def batch_cache_stk_factor_pro(target_date):
    """批量缓存指定日期全市场 stk_factor_pro 数据到 SQLite（字段列表：sc._STK_FACTOR_FIELDS）"""
    pro = _get_pro()
    batch_date = get_batch_date()
    if batch_date == target_date:
        return
    
    print(f"[批量缓存] 开始下载 {target_date} 全市场 stk_factor_pro 数据...")
    try:
        df_all = pro.stk_factor_pro(trade_date=target_date, fields=_STK_FACTOR_FIELDS)
        if df_all is not None and not df_all.empty:
            df_all['trade_date'] = df_all['trade_date'].astype(str)
            
            # 除权检测
            adj_changed_count = 0
            for code, group_df in df_all.groupby('ts_code'):
                if 'adj_factor' not in group_df.columns:
                    continue
                new_adj = group_df['adj_factor'].iloc[-1]
                old_adj = get_last_adj_factor(code)
                if old_adj is not None and new_adj is not None and old_adj != 0 and new_adj != 0:
                    adj_ratio = abs(new_adj - old_adj) / old_adj
                    if adj_ratio >= 0.05:
                        print(f"[除权检测] {code} 复权因子变化: {old_adj} -> {new_adj} ({adj_ratio*100:.2f}%)，删除旧缓存待全量更新")
                        delete_stk_factor_stock(code)
                        adj_changed_count += 1
            
            saved_count = batch_insert_stk_factor_pro(df_all)
            set_batch_date(target_date)
            if adj_changed_count > 0:
                print(f"[批量缓存] 完成：{saved_count} 行已写入 SQLite，{adj_changed_count} 只除权待全量更新")
            else:
                print(f"[批量缓存] 完成：{saved_count} 行已写入 SQLite")
        else:
            print(f"[批量缓存] 警告：{target_date} 无数据返回")
            set_batch_date(target_date)
    except Exception as e:
        print(f"[批量缓存] 失败: {e}")
        import traceback; traceback.print_exc()


def supplement_missing_stocks(trade_date: str, target_count: int = 5000) -> int:
    """补全当日缺失的 stk_factor_pro 数据（先重试批量，再并发按个股补全）

    Args:
        trade_date: 交易日 YYYYMMDD
        target_count: 目标记录数，达到后停止补全

    Returns:
        补全的行数
    """
    import sqlite3 as _sc, time as _time, concurrent.futures as _cf, threading as _th

    def _count_today():
        _c = _sc.connect(DB_PATH)
        _r = _c.execute('SELECT COUNT(*) FROM stk_factor_pro WHERE trade_date=?', (trade_date,)).fetchone()[0]
        _c.close()
        return _r

    # ── Phase 1: 重试批量查询（1次调用，可能 Tushare 已计算完）──
    print(f"  🔄 重试批量查询 {trade_date}...")
    _pro = _get_pro()
    try:
        _df_retry = _pro.stk_factor_pro(trade_date=trade_date, fields=_STK_FACTOR_FIELDS)
        _time.sleep(0.12)
        if _df_retry is not None and not _df_retry.empty:
            _df_retry['trade_date'] = _df_retry['trade_date'].astype(str)
            batch_insert_stk_factor_pro(_df_retry)
            _new_cnt = _count_today()
            if _new_cnt >= target_count:
                print(f"  ✅ 批量重试后数据已完整（{_new_cnt}条）")
                return _new_cnt
    except Exception:
        pass

    # ── Phase 2: 找上一完整交易日，确定缺失股票列表 ──
    _conn = _sc.connect(DB_PATH)
    _cur = _conn.cursor()
    _cur.execute("""
        SELECT trade_date, COUNT(*) as cnt
        FROM stk_factor_pro
        WHERE trade_date < ? AND trade_date >= ?
        GROUP BY trade_date ORDER BY trade_date DESC LIMIT 5
    """, (trade_date, str(int(trade_date) - 8)))
    _prev_date = None
    for _d, _c in _cur.fetchall():
        if _c >= target_count:
            _prev_date = _d
            break
    if _prev_date is None:
        print(f"  ⚠️ 找不到上一个完整交易日，跳过补全")
        _conn.close()
        return 0

    _cur.execute('SELECT DISTINCT ts_code FROM stk_factor_pro WHERE trade_date=?', (_prev_date,))
    _all_codes = {r[0] for r in _cur.fetchall()}
    _cur.execute('SELECT DISTINCT ts_code FROM stk_factor_pro WHERE trade_date=?', (trade_date,))
    _today_codes = {r[0] for r in _cur.fetchall()}
    _conn.close()

    _missing = sorted(_all_codes - _today_codes)
    if not _missing:
        return 0

    _need = min(len(_missing), max(0, target_count - _count_today()))
    _to_supplement = _missing[:_need]
    if not _to_supplement:
        return 0

    print(f"  ⏳ 并发补全 {len(_to_supplement)} 只缺失股票（5线程）...")

    # ── Phase 3: 并发按个股补全 ──
    _lock = _th.Lock()
    _last_call = [0.0]  # 共享的最近API调用时间
    _supplemented = [0]
    _done = [0]

    def _fetch_one(code):
        nonlocal _lock, _last_call, _supplemented, _done
        # 全局速率限制：确保间隔 >= 120ms
        with _lock:
            _elapsed = _time.time() - _last_call[0]
            if _elapsed < 0.12:
                _time.sleep(0.12 - _elapsed)
            _pro_local = _get_pro()
            try:
                _df = _pro_local.stk_factor_pro(
                    ts_code=code, start_date=trade_date, end_date=trade_date,
                    fields=_STK_FACTOR_FIELDS
                )
            except Exception:
                _time.sleep(0.5)
                return None
            finally:
                _last_call[0] = _time.time()
        if _df is not None and not _df.empty:
            _df['trade_date'] = _df['trade_date'].astype(str)
            return _df
        return None

    _batch = []
    with _cf.ThreadPoolExecutor(max_workers=5) as _exec:
        _futures = {_exec.submit(_fetch_one, code): code for code in _to_supplement}
        for _future in _cf.as_completed(_futures):
            _result = _future.result()
            _done[0] += 1
            if _result is not None:
                _batch.append(_result)
                _supplemented[0] += len(_result)
            # 每200只批量写入一次
            if len(_batch) >= 200 or (_done[0] % 100 == 0 and len(_batch) > 0):
                _combined = pd.concat(_batch, ignore_index=True)
                try:
                    batch_insert_stk_factor_pro(_combined)
                except Exception:
                    pass
                _batch = []
                print(f"   进度: {_done[0]}/{len(_to_supplement)}（已补{_supplemented[0]}行）")

    if _batch:
        _combined = pd.concat(_batch, ignore_index=True)
        try:
            batch_insert_stk_factor_pro(_combined)
        except Exception:
            pass
        print(f"   进度: {_done[0]}/{len(_to_supplement)}（已补{_supplemented[0]}行）")

    _final_cnt = _count_today()
    if _supplemented[0] > 0:
        print(f"  ✅ 补全完成：新增 {_supplemented[0]} 行（共 {_final_cnt} 条记录）")
    else:
        print(f"  ℹ️ 补全无新增数据（Tushare 尚未计算完成）")
    return _supplemented[0]


def cached_stk_factor_pro(ts_code, start_date, end_date, silent=False):
    """带缓存的 stk_factor_pro（SQLite + 按需补充）
    
    逻辑：
      1. 先检查 SQLite 缓存是否覆盖请求范围
      2. 若覆盖则直接返回（不需要 batch，不需要补充）
      3. 缓存不足时：先 batch 补最新日，再按需补充历史缺失
    
    Args:
        silent: True 时抑制 "[缓存补充]" 日志（用于预加载等批量场景）
    
    返回按 trade_date 升序的 DataFrame，列与 _STK_FACTOR_FIELDS 一致。
    """
    pro = _get_pro()
    global _cache_supplement_completed
    
    # ── 1. 确定所需最小日期 ──
    list_date = get_list_date(ts_code)
    required_min = list_date if (list_date and list_date > str(start_date)) else str(start_date)
    
    # ── 2. 先检查 SQLite 缓存（必须在 batch 之前，避免 batch 除权检测误删数据）──
    cached_min, cached_max = get_stk_factor_range(ts_code)
    if cached_min and cached_max:
        if cached_min <= required_min and cached_max >= str(end_date):
            df = get_stk_factor_pro(ts_code, start_date, end_date)
            if df is not None and not df.empty:
                return df.reset_index(drop=True)
        # end_date 数据缺失时才需要调用 batch（批量缓存当天全市场）
        if cached_max < str(end_date):
            batch_cache_stk_factor_pro(end_date)
    else:
        # 完全无缓存，先确保批量数据
        batch_cache_stk_factor_pro(end_date)
    
    # ── 3. 再次检查缓存（batch 可能已补充 end_date，也可能触发除权检测删了数据）──
    cached_min, cached_max = get_stk_factor_range(ts_code)
    if cached_min and cached_max:
        if cached_min <= required_min and cached_max >= str(end_date):
            df = get_stk_factor_pro(ts_code, start_date, end_date)
            if df is not None and not df.empty:
                return df.reset_index(drop=True)
    
    # ── 4. 确实缺失，补充 ──
    # 计算实际需要补充的日期范围：只补充缺失的部分，不重复拉取已有数据
    actual_start = str(start_date)
    if cached_min and cached_max:
        if cached_min <= str(start_date):
            actual_start = str(cached_max)
    
    supplement_key = f"{ts_code}_{actual_start}_{end_date}"
    if supplement_key in _cache_supplement_completed:
        df = get_stk_factor_pro(ts_code, start_date, end_date)
        if df is not None and not df.empty:
            return df.reset_index(drop=True)
        return None
    
    _cache_supplement_completed.add(supplement_key)
    
    try:
        df_new = pro.stk_factor_pro(ts_code=ts_code, start_date=actual_start, end_date=end_date)
        time.sleep(0.06)
        if df_new is not None and not df_new.empty:
            df_new['trade_date'] = df_new['trade_date'].astype(str)
            df_new = df_new.sort_values('trade_date').reset_index(drop=True)
            saved = batch_insert_stk_factor_pro(df_new)
            if saved and saved > 0:
                if not silent:
                    print(f"[缓存补充] {ts_code} 成功: {saved} 行 {actual_start}~{end_date}")
            mask = (df_new['trade_date'] >= str(start_date)) & (df_new['trade_date'] <= str(end_date))
            result = df_new[mask].copy().sort_values('trade_date').reset_index(drop=True)
            if not result.empty:
                return result
        else:
            if not silent:
                print(f"[缓存补充] {ts_code} {actual_start}~{end_date} API 返回空数据")
    except Exception as e:
        if not silent:
            print(f"[缓存补充] {ts_code} 失败: {e}")
    
    # 保底：从 SQLite 读
    df = get_stk_factor_pro(ts_code, start_date, end_date)
    if df is not None and not df.empty:
        return df.reset_index(drop=True)
    return None


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
    print('=== stock_cache 自检 ===')
    print(f'DB 路径: {DB_PATH}')
    print(f'表存在: {_table_exists(STK_FACTOR_TABLE)}')
    if _table_exists(STK_FACTOR_TABLE):
        print(f'股票数: {count_stk_factor_stocks()}')
        print(f'总行数: {count_stk_factor_rows()}')
    print('========================')
