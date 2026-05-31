import tushare as ts
import pandas as pd
import sqlite3
import os
import time
import datetime
from dotenv import load_dotenv

# =====================================
# 环境变量
# =====================================
load_dotenv()

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")

# =====================================
# 初始化 tushare
# =====================================
ts.set_token(TUSHARE_TOKEN)

pro = ts.pro_api()

# =====================================
# SQLite
# =====================================
import os

# =========================
# 当前脚本所在目录
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# =========================
# 缓存目录（和.py同级）
# =========================
CACHE_DIR = os.path.join(BASE_DIR, "cache_daily")

# 创建目录
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

print("缓存目录:", CACHE_DIR)
conn = sqlite3.connect(CACHE_DIR + "/market_data.db")

# =========================
# 获取“最新可用交易日”
# =========================
def get_latest_trade_date():

    now = datetime.datetime.now()

    today = now.strftime('%Y%m%d')

    # 当前时间
    current_time = now.time()

    # 获取交易日历
    cal = pro.trade_cal(
        exchange='',
        start_date='20240101',
        end_date=today
    )

    cal = cal[cal['is_open'] == 1]

    trade_dates = cal['cal_date'].tolist()

    # =========================
    # 今天不是交易日
    # =========================
    if today not in trade_dates:

        return trade_dates[-1]

    # =========================
    # 今天是交易日
    # 15点后使用今天
    # =========================
    if current_time >= datetime.time(15, 0):

        return today

    # =========================
    # 15点前使用上一交易日
    # =========================
    idx = trade_dates.index(today)

    if idx == 0:
        return today

    return trade_dates[idx - 1]


TRADE_DATE = get_latest_trade_date()

print("当前交易日:", TRADE_DATE)

# =====================================
# 初始化数据库
# =====================================
def init_db():

    cursor = conn.cursor()

    # =====================================
    # 日线行情
    # =====================================
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS daily_data (

        ts_code TEXT,
        trade_date TEXT,

        open REAL,
        high REAL,
        low REAL,
        close REAL,

        pre_close REAL,

        change REAL,
        pct_chg REAL,

        vol REAL,
        amount REAL,

        PRIMARY KEY (ts_code, trade_date)
    )
    """)

    # =====================================
    # daily_basic
    # =====================================
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS daily_basic (

        ts_code TEXT,
        trade_date TEXT,

        close REAL,

        turnover_rate REAL,
        turnover_rate_f REAL,

        volume_ratio REAL,

        pe REAL,
        pe_ttm REAL,

        pb REAL,

        ps REAL,
        ps_ttm REAL,

        dv_ratio REAL,
        dv_ttm REAL,

        total_share REAL,
        float_share REAL,
        free_share REAL,

        total_mv REAL,
        circ_mv REAL,

        PRIMARY KEY (ts_code, trade_date)
    )
    """)

    # =====================================
    # 股票基础信息
    # =====================================
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS stock_basic (

        ts_code TEXT PRIMARY KEY,

        symbol TEXT,
        name TEXT,

        area TEXT,
        industry TEXT,

        market TEXT,

        list_date TEXT
    )
    """)

    # =====================================
    # 板块概念
    # =====================================
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS stock_concept (

        ts_code TEXT,
        concept_name TEXT,

        PRIMARY KEY (ts_code, concept_name)
    )
    """)

    # =====================================
    # 索引
    # =====================================
    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_daily_code_date
    ON daily_data(ts_code, trade_date)
    """)

    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_basic_code_date
    ON daily_basic(ts_code, trade_date)
    """)

    conn.commit()

# =====================================
# 获取股票基础信息
# =====================================
def update_stock_basic():

    print("更新 stock_basic...")

    df = pro.stock_basic(
        exchange='',
        list_status='L',
        fields='''
        ts_code,symbol,name,
        area,industry,market,list_date
        '''
    )

    df.to_sql(
        'stock_basic',
        conn,
        if_exists='replace',
        index=False
    )

    print("stock_basic 完成")

# =====================================
# daily_data 最新日期
# =====================================
def get_latest_daily_date(ts_code):

    sql = f"""
    SELECT MAX(trade_date)
    FROM daily_data
    WHERE ts_code='{ts_code}'
    """

    df = pd.read_sql(sql, conn)

    return df.iloc[0,0]

# =====================================
# daily_basic 最新日期
# =====================================
def get_latest_basic_date(ts_code):

    sql = f"""
    SELECT MAX(trade_date)
    FROM daily_basic
    WHERE ts_code='{ts_code}'
    """

    df = pd.read_sql(sql, conn)

    return df.iloc[0,0]

# =====================================
# 更新 daily_data
# =====================================
def update_daily_data(ts_code):

    latest = get_latest_daily_date(ts_code)

    if latest == TRADE_DATE:
        return

    if latest is None:
        start_date = '20200101'
    else:
        start_date = latest

    try:

        df = pro.daily(
            ts_code=ts_code,
            start_date=start_date,
            end_date=TRADE_DATE
        )

        if df.empty:
            return

        df.drop_duplicates(
            subset=['ts_code','trade_date'],
            inplace=True
        )

        df.to_sql(
            'daily_data',
            conn,
            if_exists='append',
            index=False
        )

        time.sleep(0.03)

    except Exception as e:

        print(ts_code, e)

# =====================================
# 更新 daily_basic
# =====================================
def update_daily_basic(ts_code):

    latest = get_latest_basic_date(ts_code)

    if latest == TRADE_DATE:
        return

    if latest is None:
        start_date = '20200101'
    else:
        start_date = latest

    try:

        df = pro.daily_basic(
            ts_code=ts_code,
            start_date=start_date,
            end_date=TRADE_DATE
        )

        if df.empty:
            return

        df.drop_duplicates(
            subset=['ts_code','trade_date'],
            inplace=True
        )

        df.to_sql(
            'daily_basic',
            conn,
            if_exists='append',
            index=False
        )

        time.sleep(0.03)

    except Exception as e:

        print(ts_code, e)

# =====================================
# 更新全市场
# =====================================
def update_all_market():

    basic = pd.read_sql(
        "SELECT * FROM stock_basic",
        conn
    )

    total = len(basic)

    for idx, row in basic.iterrows():

        ts_code = row['ts_code']

        print(f"[{idx+1}/{total}] 更新 {ts_code}")

        update_daily_data(ts_code)

        update_daily_basic(ts_code)

# =====================================
# 获取日线
# =====================================
def get_daily(ts_code, limit=250):

    sql = f"""
    SELECT *
    FROM daily_data
    WHERE ts_code='{ts_code}'
    ORDER BY trade_date
    """

    df = pd.read_sql(sql, conn)

    if df.empty:
        return None

    return df.tail(limit)

# =====================================
# 获取 basic
# =====================================
def get_basic(ts_code, limit=250):

    sql = f"""
    SELECT *
    FROM daily_basic
    WHERE ts_code='{ts_code}'
    ORDER BY trade_date
    """

    df = pd.read_sql(sql, conn)

    if df.empty:
        return None

    return df.tail(limit)

# =====================================
# 获取股票概念（手动导入版）
# =====================================
def import_concept_csv(csv_path):

    """
    CSV格式:

    ts_code,concept_name

    000001.SZ,AI医疗
    000001.SZ,机器人
    """

    df = pd.read_csv(csv_path)

    df.to_sql(
        'stock_concept',
        conn,
        if_exists='replace',
        index=False
    )

    print("概念板块导入完成")

# =====================================
# 查询某概念股票
# =====================================
def get_concept_stocks(concept):

    sql = f"""
    SELECT *
    FROM stock_concept
    WHERE concept_name='{concept}'
    """

    return pd.read_sql(sql, conn)

# =====================================
# 示例：趋势中军模型
# =====================================
def core_trend_strategy(ts_code):

    daily = get_daily(ts_code)

    basic = get_basic(ts_code)

    if daily is None or basic is None:
        return False

    if len(daily) < 60:
        return False

    C = daily['close']

    MA5 = C.rolling(5).mean()

    MA20 = C.rolling(20).mean()

    latest_mv = basic.iloc[-1]['total_mv']

    latest_turnover = basic.iloc[-1]['turnover_rate']

    # 市值过滤
    cond1 = latest_mv > 100e4

    # 站上5日线
    cond2 = C.iloc[-1] > MA5.iloc[-1]

    # 20日线向上
    cond3 = (
        MA20.iloc[-1] >
        MA20.iloc[-2] >
        MA20.iloc[-3]
    )

    # 换手温和
    cond4 = latest_turnover < 15

    return (
        cond1 and
        cond2 and
        cond3 and
        cond4
    )

# =====================================
# 主程序
# =====================================
if __name__ == "__main__":

    init_db()

    # 第一次运行
    update_stock_basic()

    # 每日更新
    update_all_market()

    print("数据库更新完成")