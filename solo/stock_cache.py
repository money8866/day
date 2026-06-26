import sqlite3
import pandas as pd
import os
from contextlib import contextmanager

CACHE_DIR = r"D:\mystock\cache_daily"
DB_PATH = os.path.join(CACHE_DIR, "stock_data.db")

# =========================================================
# 基础工具
# =========================================================

@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
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


# =========================================================
# 快捷测试
# =========================================================

if __name__ == '__main__':
    print('=== stock_cache 自检 ===')
    print(f'DB 路径: {DB_PATH}')
    print(f'表存在: {_table_exists(STK_FACTOR_TABLE)}')
    if _table_exists(STK_FACTOR_TABLE):
        print(f'股票数: {count_stk_factor_stocks()}')
        print(f'总行数: {count_stk_factor_rows()}')
    print('========================')
