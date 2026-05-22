import os
import time
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv
import tushare as ts
import sqlite3

# 加载环境变量
load_dotenv()
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

# ============================================
# 配置参数
# ============================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(BASE_DIR, "cache_db")
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "dc_concept.db")

MIN_STOCKS = 5      # 板块最小股票数
TOP_K = 10          # 输出主线数量

# ============================================
# 初始化SQLite数据库
# ============================================
def init_database():
    """初始化SQLite数据库表结构"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 概念题材列表表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS dc_concept (
            theme_code TEXT,
            trade_date TEXT,
            name TEXT,
            pct_change REAL,
            hot INTEGER,
            sort INTEGER,
            strength INTEGER,
            z_t_num INTEGER,
            main_change REAL,
            lead_stock TEXT,
            lead_stock_code TEXT,
            lead_stock_pct_change REAL,
            PRIMARY KEY (theme_code, trade_date)
        )
    """)
    
    # 概念成分股表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS dc_concept_cons (
            ts_code TEXT,
            trade_date TEXT,
            name TEXT,
            theme_code TEXT,
            industry_code TEXT,
            industry TEXT,
            reason TEXT,
            hot_num INTEGER,
            PRIMARY KEY (ts_code, theme_code, trade_date)
        )
    """)
    
    # 日线数据表
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
            vol INTEGER,
            amount REAL,
            PRIMARY KEY (ts_code, trade_date)
        )
    """)
    
    # 股票基本信息表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stock_basic (
            ts_code TEXT PRIMARY KEY,
            name TEXT,
            industry TEXT,
            list_date TEXT
        )
    """)
    
    conn.commit()
    conn.close()
    print(f"数据库初始化完成: {DB_PATH}")

# ============================================
# 获取最近交易日
# ============================================
def get_last_trade_date():
    now = datetime.now()
    
    # 15点前视为上一交易日
    if now.hour < 15:
        query_date = (now - timedelta(days=1)).strftime('%Y%m%d')
    else:
        query_date = now.strftime('%Y%m%d')
    
    # 获取交易日历
    cal = pro.trade_cal(
        exchange='',
        start_date='20200101',
        end_date=query_date
    )
    
    # 只保留开市日
    cal = cal[cal['is_open'] == 1]
    
    # 最近交易日
    last_trade_date = cal[cal['cal_date'] <= query_date]['cal_date'].max()
    
    return str(last_trade_date)

TRADE_DATE = get_last_trade_date()
print(f"分析日期: {TRADE_DATE}")

# ============================================
# 下载东方财富概念题材列表（带缓存）
# ============================================
def download_dc_concept(trade_date=None, force_refresh=False):
    """
    使用 dc_concept 接口下载东方财富概念题材列表
    
    参数:
        trade_date: 交易日期 (YYYYMMDD格式)，默认使用最近交易日
        force_refresh: 是否强制刷新缓存
    """
    if trade_date is None:
        trade_date = TRADE_DATE
    
    conn = sqlite3.connect(DB_PATH)
    
    # 检查缓存
    if not force_refresh:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM dc_concept WHERE trade_date = ?", (trade_date,))
        count = cursor.fetchone()[0]
        if count > 0:
            print(f"从缓存读取概念列表: {trade_date}")
            df = pd.read_sql(f"SELECT * FROM dc_concept WHERE trade_date = '{trade_date}'", conn)
            conn.close()
            return df
    
    print(f"从Tushare下载东方财富概念列表...")
    
    # 调用接口
    df = pro.dc_concept(trade_date=trade_date)
    
    if df is None or df.empty:
        print(f"警告: 未获取到概念列表数据")
        conn.close()
        return pd.DataFrame()
    
    # 转换数据类型
    df['pct_change'] = pd.to_numeric(df['pct_change'], errors='coerce')
    df['hot'] = pd.to_numeric(df['hot'], errors='coerce')
    df['sort'] = pd.to_numeric(df['sort'], errors='coerce')
    df['strength'] = pd.to_numeric(df['strength'], errors='coerce')
    df['z_t_num'] = pd.to_numeric(df['z_t_num'], errors='coerce')
    df['main_change'] = pd.to_numeric(df['main_change'], errors='coerce')
    df['lead_stock_pct_change'] = pd.to_numeric(df['lead_stock_pct_change'], errors='coerce')
    
    # 保存到数据库
    df.to_sql('dc_concept', conn, if_exists='append', index=False)
    conn.commit()
    conn.close()
    
    print(f"概念列表已缓存: {len(df)} 条记录")
    
    return df

# ============================================
# 下载东方财富概念成分股（带缓存）
# ============================================
def download_dc_concept_cons(trade_date=None, force_refresh=False):
    """
    使用 dc_concept_cons 接口下载东方财富概念题材成分股
    
    参数:
        trade_date: 交易日期 (YYYYMMDD格式)，默认使用最近交易日
        force_refresh: 是否强制刷新缓存
    """
    if trade_date is None:
        trade_date = TRADE_DATE
    
    conn = sqlite3.connect(DB_PATH)
    
    # 检查缓存
    if not force_refresh:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM dc_concept_cons WHERE trade_date = ?", (trade_date,))
        count = cursor.fetchone()[0]
        if count > 0:
            print(f"从缓存读取概念成分股: {trade_date}")
            df = pd.read_sql(f"SELECT * FROM dc_concept_cons WHERE trade_date = '{trade_date}'", conn)
            conn.close()
            return df
    
    print(f"从Tushare下载东方财富概念成分股...")
    
    # 调用接口
    df = pro.dc_concept_cons(trade_date=trade_date)
    
    if df is None or df.empty:
        print(f"警告: 未获取到概念成分股数据")
        conn.close()
        return pd.DataFrame()
    
    # 转换数据类型
    df['hot_num'] = pd.to_numeric(df['hot_num'], errors='coerce')
    
    # 保存到数据库
    df.to_sql('dc_concept_cons', conn, if_exists='append', index=False)
    conn.commit()
    conn.close()
    
    print(f"概念成分股已缓存: {len(df)} 条记录")
    
    return df

# ============================================
# 获取股票日线数据（带缓存）
# ============================================
def get_daily_data(trade_date=None):
    if trade_date is None:
        trade_date = TRADE_DATE
    
    conn = sqlite3.connect(DB_PATH)
    
    # 检查缓存
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM daily_data WHERE trade_date = ?", (trade_date,))
    count = cursor.fetchone()[0]
    if count > 0:
        print(f"从缓存读取日线数据: {trade_date}")
        df = pd.read_sql(f"SELECT * FROM daily_data WHERE trade_date = '{trade_date}'", conn)
        conn.close()
        return df
    
    print(f"从Tushare下载日线数据...")
    df = pro.daily(trade_date=trade_date)
    
    if df is None or df.empty:
        conn.close()
        return pd.DataFrame()
    
    # 成交额转亿元（Tushare单位为千元）
    df['amount'] = df['amount'] / 100000
    
    # 保存到数据库
    df.to_sql('daily_data', conn, if_exists='append', index=False)
    conn.commit()
    conn.close()
    
    print(f"日线数据已缓存: {len(df)} 条记录")
    
    return df

# ============================================
# 获取股票基本信息（带缓存）
# ============================================
def get_stock_basic():
    conn = sqlite3.connect(DB_PATH)
    
    # 检查缓存
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM stock_basic")
    count = cursor.fetchone()[0]
    if count > 0:
        df = pd.read_sql("SELECT * FROM stock_basic", conn)
        conn.close()
        return df
    
    print(f"从Tushare下载股票基本信息...")
    df = pro.stock_basic(
        exchange='',
        list_status='L',
        fields='ts_code,name,industry,list_date'
    )
    
    # 保存到数据库
    df.to_sql('stock_basic', conn, if_exists='replace', index=False)
    conn.commit()
    conn.close()
    
    print(f"股票基本信息已缓存: {len(df)} 条记录")
    
    return df

# ============================================
# 计算板块强度评分
# ============================================
def calc_sector_strength(daily_df, concept_stocks):
    """
    计算板块强度评分
    
    参数:
        daily_df: 日线数据DataFrame
        concept_stocks: 该概念包含的股票代码列表
    
    返回:
        综合评分, 龙头代码, 龙头涨幅
    """
    if not concept_stocks or len(concept_stocks) == 0:
        return 0, None, None
    
    # 筛选该概念的股票日线数据
    sector_df = daily_df[daily_df['ts_code'].isin(concept_stocks)]
    
    if sector_df.empty:
        return 0, None, None
    
    pct_chg = sector_df['pct_chg']
    amount = sector_df['amount']
    
    # 1. 平均涨幅
    avg_pct = pct_chg.mean()
    
    # 2. 涨停数量
    limit_up_count = (pct_chg >= 9.5).sum()
    
    # 3. 上涨比例
    up_ratio = (pct_chg > 0).mean()
    
    # 4. 中位数涨幅
    median_pct = pct_chg.median()
    
    # 5. 总成交额（亿元）
    total_amount = amount.sum()
    
    # 6. 资金集中度（前5占比）
    try:
        top5_ratio = (sector_df.sort_values('amount', ascending=False).head(5)['amount'].sum() / total_amount)
    except:
        top5_ratio = 0
    
    # 7. 跌停数量（负向指标）
    limit_down_count = (pct_chg <= -9.5).sum()
    
    # 综合评分
    score = (
        avg_pct * 1.2 +
        limit_up_count * 6 +
        up_ratio * 5 +
        median_pct * 1.5 +
        total_amount * 0.8 +
        top5_ratio * 8 -
        limit_down_count * 10
    )
    
    # 找出龙头（涨幅最高）
    leader_row = sector_df.sort_values('pct_chg', ascending=False).iloc[0]
    leader_code = leader_row['ts_code']
    leader_pct = leader_row['pct_chg']
    
    return score, leader_code, leader_pct

# ============================================
# 分析最强主线
# ============================================
def analyze_top_themes(concept_df, concept_cons_df, daily_df, stock_basic):
    """
    分析最强的TOP_K个主线
    
    参数:
        concept_df: 东方财富概念数据
        concept_cons_df: 东方财富概念成分股数据
        daily_df: 日线数据
        stock_basic: 股票基本信息
    
    返回:
        排序后的主线DataFrame
    """
    if concept_df.empty or concept_cons_df.empty or daily_df.empty:
        return pd.DataFrame()
    
    # 构建概念 -> 股票映射
    concept_stock_map = {}
    
    for _, row in concept_cons_df.iterrows():
        theme_code = row['theme_code']
        ts_code = row['ts_code']
        
        if theme_code not in concept_stock_map:
            concept_stock_map[theme_code] = []
        
        concept_stock_map[theme_code].append(ts_code)
    
    # 分析每个概念
    results = []
    
    for _, concept_row in concept_df.iterrows():
        theme_code = concept_row['theme_code']
        theme_name = concept_row['name']
        
        # 获取成分股
        stocks = concept_stock_map.get(theme_code, [])
        
        # 过滤股票数量过少的概念
        if len(stocks) < MIN_STOCKS:
            continue
        
        score, leader_code, leader_pct = calc_sector_strength(daily_df, stocks)
        
        # 获取龙头名称
        leader_name = stock_basic[stock_basic['ts_code'] == leader_code]['name'].values
        leader_name = leader_name[0] if len(leader_name) > 0 else leader_code
        
        results.append({
            '概念名称': theme_name,
            '概念代码': theme_code,
            '成分股数': len(stocks),
            '热度': concept_row['hot'],
            '强度评分': round(score, 2),
            '东财强度': concept_row['strength'],
            '涨停数': concept_row['z_t_num'],
            '主力净流入(亿)': round(concept_row['main_change'] / 100000000, 2) if pd.notna(concept_row['main_change']) else 0,
            '龙头代码': leader_code,
            '龙头名称': leader_name,
            '龙头涨幅': round(leader_pct, 2) if pd.notna(leader_pct) else 0,
            '东财领涨股': concept_row['lead_stock'],
            '东财排名': concept_row['sort']
        })
    
    # 转换为DataFrame并排序
    result_df = pd.DataFrame(results)
    
    if not result_df.empty:
        # 按强度评分排序
        result_df = result_df.sort_values('强度评分', ascending=False)
        result_df = result_df.head(TOP_K)
        result_df.reset_index(drop=True, inplace=True)
        result_df.index = result_df.index + 1  # 从1开始编号
    
    return result_df

# ============================================
# 主函数
# ============================================
def main():
    print("\n=== 东方财富概念主线分析系统 ===\n")
    
    # 1. 初始化数据库
    init_database()
    
    # 2. 下载概念列表
    concept_df = download_dc_concept()
    
    if concept_df.empty:
        print("无法获取概念列表数据，程序退出")
        return
    
    print(f"获取到 {len(concept_df)} 个概念")
    
    # 3. 下载概念成分股
    concept_cons_df = download_dc_concept_cons()
    
    if concept_cons_df.empty:
        print("无法获取概念成分股数据，程序退出")
        return
    
    print(f"获取到 {len(concept_cons_df)} 条成分股记录")
    
    # 4. 获取日线数据
    daily_df = get_daily_data()
    
    if daily_df.empty:
        print("无法获取日线数据，程序退出")
        return
    
    # 5. 获取股票基本信息
    stock_basic = get_stock_basic()
    
    # 6. 分析最强主线
    top_themes = analyze_top_themes(concept_df, concept_cons_df, daily_df, stock_basic)
    
    # 7. 输出结果
    print(f"\n【最强{TOP_K}个主线】")
    print("=" * 100)
    
    if top_themes.empty:
        print("暂无符合条件的主线")
        return
    
    # 打印排名前10的主线
    output_cols = ['概念名称', '成分股数', '热度', '强度评分', '东财强度', '涨停数', '主力净流入(亿)', '龙头代码', '龙头名称', '龙头涨幅']
    print(top_themes[output_cols].to_string())
    
    # 打印详细描述
    print("\n【主线详细信息】")
    print("=" * 100)
    for idx, row in top_themes.iterrows():
        print(f"{idx}. {row['概念名称']}")
        print(f"   概念代码: {row['概念代码']}")
        print(f"   成分股数: {row['成分股数']}")
        print(f"   热度排名: {row['东财排名']} (热度值: {row['热度']})")
        print(f"   强度评分: {row['强度评分']} (东财强度: {row['东财强度']})")
        print(f"   涨停数量: {row['涨停数']}")
        print(f"   主力净流入: {row['主力净流入(亿)']} 亿元")
        print(f"   龙头: {row['龙头名称']}({row['龙头代码']}) +{row['龙头涨幅']}%")
        print(f"   东财领涨: {row['东财领涨股']}")
        print()

if __name__ == "__main__":
    main()
