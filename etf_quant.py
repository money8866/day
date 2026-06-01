# =========================================================
# AI主线ETF系统 v4.0（机构中观增强版）
# =========================================================
# 升级内容：
#
# 1、主线ETF轮动
# 2、市场风格识别
# 3、风险控制
# 4、趋势突破
# 5、主升浪识别
# 6、第一次分歧低吸
# 7、趋势衰竭
# 8、周线共振
# 9、相对强弱RS
# 10、波动率压缩
# 11、成交量结构
# 12、行业轮动速度
# 13、板块宽度
# 14、ETF资金流
# 15、AI新闻情绪
# 16、动态仓位
# 17、DeepSeek日报
# =========================================================

import os
import io
import sys
import time
import json
import requests
import numpy as np
import pandas as pd
import tushare as ts
import tushare_quant,block,emotion
import block_decline_risk as drc
from datetime import datetime, timedelta
from dotenv import load_dotenv
import sqlite3

# =========================
# Windows UTF-8: 使用环境变量，不替换 stdout/stderr
# =========================
# 注意：在 .bat 文件中设置 PYTHONIOENCODING=utf-8
# 不要在这里替换 sys.stdout/stderr（会导致 I/O operation on closed file）


# =========================================================
# 环境变量
# =========================================================
load_dotenv("config/.env")

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
SERVERCHAN_KEY = os.getenv("WECHAT_SCKEY")

# =========================================================
# 初始化
# =========================================================
ts.set_token(TUSHARE_TOKEN)

pro = ts.pro_api()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CACHE_DIR = os.path.join(BASE_DIR, "cache_daily")
REPORT_DIR = os.path.join(BASE_DIR, "report_daily")

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)
DB_PATH = os.path.join(
    REPORT_DIR,
    "etf_result.db"
)

# =========================================================
# 持仓管理
# =========================================================
def init_portfolio_table():
    """初始化持仓表和交易记录表"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS portfolio (
            ts_code TEXT PRIMARY KEY,
            industry TEXT,
            buy_date TEXT,
            buy_price REAL,
            current_price REAL,
            shares INTEGER DEFAULT 0,
            stop_loss REAL DEFAULT 0,
            take_profit REAL DEFAULT 0,
            status TEXT DEFAULT 'holding'
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trade_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT,
            ts_code TEXT,
            industry TEXT,
            action TEXT,
            price REAL,
            shares INTEGER,
            pnl REAL DEFAULT 0,
            reason TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT,
            ts_code TEXT,
            industry TEXT,
            score REAL,
            signal TEXT,
            stage TEXT,
            pct_chg REAL,
            position_pct REAL,
            emotion REAL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pending_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT,
            ts_code TEXT,
            industry TEXT,
            signal TEXT,
            suggest_price REAL,
            position_pct REAL,
            score REAL,
            status TEXT DEFAULT 'pending',
            triggered_date TEXT,
            triggered_price REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def load_portfolio():
    """加载当前持仓"""
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql(
        "SELECT * FROM portfolio WHERE status='holding'",
        conn
    )
    conn.close()
    return df

def save_portfolio_action(ts_code, industry, action, price, shares=0, pnl=0, reason=''):
    """记录交易操作"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    today = TRADE_DATE

    if action == 'buy':
        # 计算止损止盈
        stop_loss = round(price * 0.95, 3)  # -5%止损
        take_profit = round(price * 1.20, 3)  # +20%止盈
        cursor.execute("""
            INSERT OR REPLACE INTO portfolio
            (ts_code, industry, buy_date, buy_price, current_price, shares, stop_loss, take_profit, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'holding')
        """, (ts_code, industry, today, price, price, shares, stop_loss, take_profit))
    elif action == 'sell':
        cursor.execute("""
            UPDATE portfolio SET status='closed' WHERE ts_code=? AND status='holding'
        """, (ts_code,))

    # 记录交易日志
    cursor.execute("""
        INSERT INTO trade_log (trade_date, ts_code, industry, action, price, shares, pnl, reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (today, ts_code, industry, action, price, shares, pnl, reason))

    conn.commit()
    conn.close()

def update_portfolio_prices(result_df):
    """更新持仓当前价格"""
    conn = sqlite3.connect(DB_PATH)
    portfolio = pd.read_sql(
        "SELECT * FROM portfolio WHERE status='holding'",
        conn
    )

    for _, row in portfolio.iterrows():
        ts_code = row['ts_code']
        match = result_df[result_df['ETF'] == ts_code]
        if not match.empty:
            current_price = match.iloc[0]['收盘价']
            conn.execute(
                "UPDATE portfolio SET current_price=? WHERE ts_code=? AND status='holding'",
                (current_price, ts_code)
            )
    conn.commit()
    conn.close()

def save_daily_snapshot(result_df, position_pct, emotion_score):
    """保存每日ETF分析快照（用于后续延续性分析）"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # 删除当天旧快照
    cursor.execute("DELETE FROM daily_snapshot WHERE trade_date=?", (TRADE_DATE,))
    for _, row in result_df.iterrows():
        cursor.execute("""
            INSERT INTO daily_snapshot
            (trade_date, ts_code, industry, score, signal, stage, pct_chg, position_pct, emotion)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            TRADE_DATE, row['ETF'], row['行业'],
            row['总评分'], row['信号'], row['波段阶段'],
            row['涨跌幅'], position_pct, emotion_score
        ))
    conn.commit()
    conn.close()

def save_pending_order(ts_code, industry, signal, suggest_price, position_pct, score):
    """保存待买入的策略建议"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO pending_orders
        (trade_date, ts_code, industry, signal, suggest_price, position_pct, score, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
    """, (TRADE_DATE, ts_code, industry, signal, suggest_price, position_pct, score))
    conn.commit()
    conn.close()
    print(f"   [待买入] 保存策略建议: {industry}({ts_code}) 建议价:{suggest_price}")

def load_pending_orders(status='pending'):
    """加载待处理的策略建议"""
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql(
        f"SELECT * FROM pending_orders WHERE status='{status}' ORDER BY trade_date DESC",
        conn
    )
    conn.close()
    return df

def check_and_trigger_orders(result_df):
    """
    检查昨日策略建议是否触发买入
    条件：今日最低价 <= 建议买入价
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 获取昨日待处理订单
    yesterday = (datetime.strptime(TRADE_DATE, '%Y%m%d') - timedelta(days=1)).strftime('%Y%m%d')
    pending_df = pd.read_sql(
        f"SELECT * FROM pending_orders WHERE status='pending' AND trade_date='{yesterday}'",
        conn
    )
    
    triggered_orders = []
    
    if pending_df.empty:
        conn.close()
        return triggered_orders
    
    print(f"\n[检查待买入订单] 昨日待处理: {len(pending_df)} 条")
    
    for _, order in pending_df.iterrows():
        ts_code = order['ts_code']
        industry = order['industry']
        suggest_price = order['suggest_price']
        signal = order['signal']
        score = order['score']
        
        # 检查是否已持仓
        holding_df = pd.read_sql(
            f"SELECT * FROM portfolio WHERE ts_code='{ts_code}' AND status='holding'",
            conn
        )
        if not holding_df.empty:
            print(f"   [跳过] {industry} 已持仓")
            continue
        
        # 获取今日行情数据
        try:
            df = pro.fund_daily(ts_code=ts_code, start_date=TRADE_DATE, end_date=TRADE_DATE)
            if df.empty:
                continue
            
            today_low = df.iloc[0]['low']
            today_close = df.iloc[0]['close']
            
            # 判断是否触发买入：最低价 <= 建议价
            if today_low <= suggest_price:
                print(f"   [触发买入] {industry}({ts_code}) 今日最低:{today_low} <= 建议价:{suggest_price}")
                
                # 计算实际买入价（取建议价和收盘价的较小值）
                actual_price = min(suggest_price, today_close)
                
                # 加入持仓
                stop_loss = round(actual_price * 0.95, 3)
                take_profit = round(actual_price * 1.20, 3)
                cursor.execute("""
                    INSERT OR REPLACE INTO portfolio
                    (ts_code, industry, buy_date, buy_price, current_price, shares, stop_loss, take_profit, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'holding')
                """, (ts_code, industry, TRADE_DATE, actual_price, today_close, 100, stop_loss, take_profit))
                
                # 记录交易日志
                cursor.execute("""
                    INSERT INTO trade_log (trade_date, ts_code, industry, action, price, shares, pnl, reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (TRADE_DATE, ts_code, industry, 'buy', actual_price, 100, 0, f"策略触发:{signal}"))
                
                # 更新订单状态
                cursor.execute("""
                    UPDATE pending_orders SET status='triggered', triggered_date=?, triggered_price=?
                    WHERE id=?
                """, (TRADE_DATE, actual_price, order['id']))
                
                triggered_orders.append({
                    'industry': industry,
                    'ts_code': ts_code,
                    'signal': signal,
                    'suggest_price': suggest_price,
                    'actual_price': actual_price,
                    'today_low': today_low
                })
            else:
                print(f"   [未触发] {industry} 今日最低:{today_low} > 建议价:{suggest_price}")
                
        except Exception as e:
            print(f"   [错误] {industry}: {e}")
            continue
    
    conn.commit()
    conn.close()
    
    return triggered_orders

def cleanup_expired_orders(days=5):
    """清理过期的待买入订单"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    expire_date = (datetime.strptime(TRADE_DATE, '%Y%m%d') - timedelta(days=days)).strftime('%Y%m%d')
    cursor.execute("""
        UPDATE pending_orders SET status='expired' 
        WHERE status='pending' AND trade_date < ?
    """, (expire_date,))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    if affected > 0:
        print(f"   [清理] {affected} 条过期订单已标记")
    return affected

def load_daily_snapshot(date_str=None, days=5):
    """加载历史每日快照"""
    conn = sqlite3.connect(DB_PATH)
    if date_str:
        df = pd.read_sql(
            f"SELECT * FROM daily_snapshot WHERE trade_date='{date_str}'",
            conn
        )
    else:
        start = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
        df = pd.read_sql(
            f"SELECT * FROM daily_snapshot WHERE trade_date >= '{start}' ORDER BY trade_date ASC",
            conn
        )
    conn.close()
    return df

def load_last_report():
    """加载昨日报告摘要"""
    report_dir = REPORT_DIR
    if not os.path.exists(report_dir):
        return ""
    reports = sorted([f for f in os.listdir(report_dir) if f.startswith('AI_ETF_Report_') and f.endswith('.md')])
    if len(reports) >= 2:
        # 取倒数第二个（倒数第一个可能是今天的）
        last_report_file = os.path.join(report_dir, reports[-2]) if reports[-1].endswith(TRADE_DATE + '.md') else os.path.join(report_dir, reports[-1])
        try:
            with open(last_report_file, 'r', encoding='utf-8') as f:
                content = f.read()
            # 取前500字作为摘要
            return content[:500] + '...' if len(content) > 500 else content
        except:
            pass
    return ""

def analyze_portfolio(result_df, portfolio_df):
    """分析当前持仓：盈亏、趋势变化、操作建议"""
    if portfolio_df.empty:
        return ""

    lines = []
    for _, p in portfolio_df.iterrows():
        ts_code = p['ts_code']
        industry = p['industry']
        buy_price = p['buy_price']
        buy_date = p['buy_date']
        stop_loss = p['stop_loss']
        take_profit = p['take_profit']

        # 匹配今日数据
        today_data = result_df[result_df['ETF'] == ts_code]
        if today_data.empty:
            lines.append(f"  - {industry}({ts_code}): 买入价{buy_price}({buy_date}), 今日无数据")
            continue

        row = today_data.iloc[0]
        current_price = row['收盘价']
        pnl_pct = round((current_price - buy_price) / buy_price * 100, 2)
        hold_days = (datetime.strptime(TRADE_DATE, '%Y%m%d') - datetime.strptime(buy_date, '%Y%m%d')).days

        # 止损止盈判断
        alert = ""
        if current_price <= stop_loss:
            alert = "[!!止损!!] 触发止损！建议卖出"
        elif current_price >= take_profit:
            alert = "[!!止盈!!] 触发止盈！建议卖出"
        elif pnl_pct <= -3:
            alert = "[警告] 接近止损"
        elif pnl_pct >= 15:
            alert = "[OK] 接近止盈"

        # 趋势变化
        stage = row.get('波段阶段', '未知')
        signal = row.get('信号', '未知')
        score = row.get('总评分', 0)
        score_change = ""

        # 对比昨日快照
        yesterday_snap = load_daily_snapshot(
            (datetime.strptime(TRADE_DATE, '%Y%m%d') - timedelta(days=1)).strftime('%Y%m%d'),
            days=3
        )
        if not yesterday_snap.empty:
            y_data = yesterday_snap[yesterday_snap['ts_code'] == ts_code]
            if not y_data.empty:
                y_score = y_data.iloc[0]['score']
                score_change = f"(昨日{y_score}→今日{score})"

        lines.append(
            f"  - {industry}({ts_code}): 买入价{buy_price}({buy_date}), "
            f"现价{current_price}, 盈亏{pnl_pct:+.2f}%, "
            f"持有{hold_days}天, 评分{score}{score_change}, "
            f"波段={stage}, 信号={signal} {alert}"
        )

    return "\n".join(lines)

def check_sell_signals(result_df, portfolio_df):
    """
    检查持仓是否需要卖出
    卖出条件：
    1. 触发止损（当前价 <= 止损价）
    2. 触发止盈（当前价 >= 止盈价）
    3. 信号变为卖出信号
    4. 评分大幅下降（评分 < 40）
    """
    if portfolio_df.empty:
        return []
    
    sell_actions = []
    conn = sqlite3.connect(DB_PATH)
    
    for _, p in portfolio_df.iterrows():
        ts_code = p['ts_code']
        industry = p['industry']
        buy_price = p['buy_price']
        stop_loss = p['stop_loss']
        take_profit = p['take_profit']
        buy_date = p['buy_date']
        
        # 匹配今日数据
        today_data = result_df[result_df['ETF'] == ts_code]
        if today_data.empty:
            continue
        
        row = today_data.iloc[0]
        current_price = row['收盘价']
        signal = row.get('信号', '')
        score = row.get('总评分', 0)
        stage = row.get('波段阶段', '')
        
        sell_reason = None
        
        # 1. 止损检查
        if current_price <= stop_loss:
            sell_reason = f"止损触发: {current_price} <= {stop_loss}"
        
        # 2. 止盈检查
        elif current_price >= take_profit:
            sell_reason = f"止盈触发: {current_price} >= {take_profit}"
        
        # 3. 卖出信号检查
        elif signal in ['趋势衰竭', '高位滞涨', '破位下跌']:
            sell_reason = f"卖出信号: {signal}"
        
        # 4. 评分大幅下降
        elif score < 40:
            pnl_pct = (current_price - buy_price) / buy_price * 100
            if pnl_pct > 0:  # 有盈利时才考虑卖出
                sell_reason = f"评分下降: {score} < 40"
        
        if sell_reason:
            pnl = round((current_price - buy_price) / buy_price * 100, 2)
            sell_actions.append({
                'ts_code': ts_code,
                'industry': industry,
                'buy_price': buy_price,
                'current_price': current_price,
                'pnl_pct': pnl,
                'reason': sell_reason,
                'buy_date': buy_date
            })
    
    conn.close()
    return sell_actions

def execute_sell_actions(sell_actions):
    """执行卖出操作"""
    if not sell_actions:
        return
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    for action in sell_actions:
        ts_code = action['ts_code']
        industry = action['industry']
        current_price = action['current_price']
        pnl_pct = action['pnl_pct']
        reason = action['reason']
        
        print(f"\n[卖出] {industry}({ts_code}): 价格{current_price}, 盈亏{pnl_pct:+.2f}%, 原因:{reason}")
        
        # 更新持仓状态
        cursor.execute("""
            UPDATE portfolio SET status='closed' WHERE ts_code=? AND status='holding'
        """, (ts_code,))
        
        # 记录交易日志
        cursor.execute("""
            INSERT INTO trade_log (trade_date, ts_code, industry, action, price, shares, pnl, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (TRADE_DATE, ts_code, industry, 'sell', current_price, 100, pnl_pct, reason))
    
    conn.commit()
    conn.close()

def init_style_table():

    conn = sqlite3.connect(DB_PATH)

    cursor = conn.cursor()

    cursor.execute("""

        CREATE TABLE IF NOT EXISTS style_history (

            date TEXT,

            风格 TEXT,

            当前得分 REAL,

            热度 REAL,

            趋势强度 REAL,

            成交额 REAL,

            轮动强度 REAL,

            风格状态 TEXT

        )

    """)

    conn.commit()

    conn.close()

def save_style_history(style_df):

    conn = sqlite3.connect(DB_PATH)

    today = TRADE_DATE

    style_df = style_df.copy()

    style_df['date'] = today

    # =========================
    # 删除当天旧数据（防止重复）
    # =========================
    conn.execute(

        "DELETE FROM style_history WHERE date=?",

        (today,)
    )

    # =========================
    # 写入数据库
    # =========================
    style_df.to_sql(

        'style_history',

        conn,

        if_exists='append',

        index=False
    )

    conn.commit()

    conn.close()

def load_style_history(days=10):

    conn = sqlite3.connect(DB_PATH)

    start_date = (
        datetime.now() - timedelta(days=days)
    ).strftime('%Y-%m-%d')

    query = f"""

        SELECT *
        FROM style_history

        WHERE date >= '{start_date}'

        ORDER BY date ASC

    """

    df = pd.read_sql(query, conn)

    conn.close()

    return df


# =========================================================
# ETF池
# =========================================================
ETF_POOL = {

    '半导体': '512480.SH',
    '人工智能': '159819.SZ',
    '算力': '561210.SH',
    '机器人': '562500.SH',

    '软件': '515230.SH',
    '通信': '515880.SH',

    '新能源': '516160.SH',
    '光伏': '515790.SH',
    '储能': '159566.SZ',

    '军工': '512660.SH',

    '创新药': '159992.SZ',

    '消费电子': '159732.SZ',

    '黄金': '518880.SH',

    '证券': '512880.SH',

    '红利': '515180.SH',

    '银行': '512800.SH',

    '消费': '159928.SZ',

    '酒': '512690.SH',

    '电池': '159755.SZ',

    '有色金属': '516650.SH',

    '芯片': '159995.SZ',
    '化工': '159870.SZ',
    '半导体设备': '159516.SZ',
    '煤炭': '515220.SH',
    '游戏': '159869.SZ',
    '金融科技': '159851.SZ',
    '电力': '159611.SZ',
    '新能源':'516160.SH',
    '电网设备':'561380.SH',
    '新能源车':'515030.SH',
    '航空航天':'159227.SZ',
    '医疗器械':'159883.SZ',
    '食品饮料':'159736.SZ',
    '钢铁':'515210.SH',

}

# =========================================================
# 行业催化
# =========================================================
INDUSTRY_EVENTS = {

    '半导体': [
        'HBM',
        'GPU',
        'AI芯片',
        '先进封装',
        '存储涨价'
    ],

    '人工智能': [
        'Agent',
        '大模型',
        'AI应用'
    ],

    '算力': [
        '液冷',
        '数据中心',
        '英伟达'
    ],

    '机器人': [
        '人形机器人',
        'Tesla Bot'
    ],

    '创新药': [
        'FDA',
        'BD',
        'ASCO'
    ]
}

# =========================================================
# 最近交易日
# =========================================================
def get_last_trade_date():
    """获取最近一个交易日（带缓存）"""
    # 缓存文件路径
    cache_file = os.path.join(CACHE_DIR, "trade_date.txt")
    
    # 检查缓存是否存在且是今天创建的
    if os.path.exists(cache_file):
        file_time = os.path.getmtime(cache_file)
        file_date = datetime.fromtimestamp(file_time).strftime('%Y%m%d')
        today = datetime.now().strftime('%Y%m%d')
        if file_date == today:
            try:
                with open(cache_file, 'r') as f:
                    cached_date = f.read().strip()
                    if cached_date:
                        print(f"   从缓存读取交易日: {cached_date}")
                        return cached_date
            except:
                pass
    
    # 缓存不存在或过期，调用API获取
    now = datetime.now()

    if now.hour < 15:
        query_date = (
            now - timedelta(days=1)
        ).strftime('%Y%m%d')

    else:
        query_date = now.strftime('%Y%m%d')

    cal = pro.trade_cal(
        exchange='',
        start_date='20240101',
        end_date=query_date
    )

    cal = cal[cal['is_open'] == 1]

    trade_date = str(
        cal[
            cal['cal_date'] <= query_date
        ]['cal_date'].max()
    )
    
    # 保存到缓存
    try:
        with open(cache_file, 'w') as f:
            f.write(trade_date)
    except:
        pass
    
    return trade_date

TRADE_DATE = get_last_trade_date()
#TRADE_DATE = "20260529"
print("当前交易日:", TRADE_DATE)

# =========================================================
# ETF历史数据
# =========================================================
def get_etf_data(ts_code):

    cache_file = os.path.join(
        CACHE_DIR,
        f"{ts_code}.csv"
    )

    # Check primary and fallback cache
    for cf in [cache_file, os.path.join(REPORT_DIR, f"{ts_code}.csv")]:
        if os.path.exists(cf):
            try:
                df = pd.read_csv(cf)
                df['trade_date'] = df['trade_date'].astype(str)
                if (
                    len(df) > 120
                    and (df['trade_date'] == TRADE_DATE).any()
                ):
                    return df.sort_values('trade_date')
            except:
                pass

    try:

        df = pro.fund_daily(
            ts_code=ts_code,
            start_date='20250101',
            end_date=TRADE_DATE
        )

        if df.empty:

            return None

        df = df.sort_values('trade_date')

        try:
            df.to_csv(
                cache_file,
                index=False
            )
        except Exception:
            # fallback to workspace
            fallback = os.path.join(REPORT_DIR, f"{ts_code}.csv")
            df.to_csv(fallback, index=False)

        time.sleep(0.05)

        return df

    except Exception as e:

        print(ts_code, e)

        return None

# =========================================================
# 指数数据
# =========================================================
def get_index_data():
    """获取沪深300指数数据（带缓存和增量更新）"""
    cache_file = os.path.join(CACHE_DIR, "000300.csv")

    if os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file)
            df['trade_date'] = df['trade_date'].astype(str)
            
            # 检查是否包含当日数据
            if len(df) > 100 and (df['trade_date'] == TRADE_DATE).any():
                return df.sort_values('trade_date')
            
            # 缓存存在但缺少当日数据，尝试增量更新
            if len(df) > 100:
                last_date = df['trade_date'].max()
                if last_date < TRADE_DATE:
                    print(f"   增量更新指数数据: {last_date} -> {TRADE_DATE}")
                    new_data = pro.index_daily(
                        ts_code='000300.SH',
                        start_date=str(int(last_date) + 1),
                        end_date=TRADE_DATE
                    )
                    if not new_data.empty:
                        df = pd.concat([df, new_data], ignore_index=True)
                        df = df.sort_values('trade_date')
                        df.to_csv(cache_file, index=False)
                        return df
        except Exception as e:
            print(f"   缓存读取失败: {e}")

    # 缓存不存在或增量更新失败，重新获取全部数据
    df = pro.index_daily(
        ts_code='000300.SH',
        start_date='20250101',
        end_date=TRADE_DATE
    )

    df = df.sort_values('trade_date')
    df.to_csv(cache_file, index=False)

    return df

# =========================================================
# 技术指标
# =========================================================
def calc_indicators(df):

    df = df.copy()

    # =====================================================
    # 均线
    # =====================================================
    for ma in [5, 10, 20, 60]:

        df[f'ma{ma}'] = (
            df['close'].rolling(ma).mean()
        )

    # =====================================================
    # 成交量
    # =====================================================
    df['vol5'] = (
        df['vol'].rolling(5).mean()
    )

    # =====================================================
    # 涨幅
    # =====================================================
    for n in [5, 10, 20]:

        df[f'pct{n}'] = (

            df['close']
            /
            df['close'].shift(n)
            - 1

        ) * 100

    # =====================================================
    # 趋势斜率
    # =====================================================
    df['slope20'] = (

        df['ma20']
        /
        df['ma20'].shift(5)
        - 1

    ) * 100

    # =====================================================
    # 波动率
    # =====================================================
    df['volatility'] = (
        df['pct_chg'].rolling(10).std()
    )

    # =====================================================
    # ATR波动
    # =====================================================
    df['tr'] = np.maximum(

        df['high'] - df['low'],

        np.maximum(

            abs(
                df['high']
                - df['close'].shift(1)
            ),

            abs(
                df['low']
                - df['close'].shift(1)
            )
        )
    )

    df['atr'] = (
        df['tr'].rolling(14).mean()
    )

    return df

# =========================================================
# 周线趋势
# =========================================================
def weekly_trend(df):

    try:

        weekly = df.copy()

        weekly.index = pd.to_datetime(
            weekly['trade_date']
        )

        weekly = weekly.resample('W').agg({

            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'vol': 'sum'
        })

        weekly['ma5'] = (
            weekly['close'].rolling(5).mean()
        )

        weekly['ma10'] = (
            weekly['close'].rolling(10).mean()
        )

        latest = weekly.iloc[-1]

        return (
            latest['ma5']
            >
            latest['ma10']
        )

    except:

        return False

# =========================================================
# 市场风险
# =========================================================
def market_risk(index_df):

    index_df['ma20'] = (
        index_df['close'].rolling(20).mean()
    )

    latest = index_df.iloc[-1]

    if latest['close'] < latest['ma20']:

        return 'risk_off', 0.3

    return 'risk_on', 1.0

# =========================================================
# 相对强弱RS
# =========================================================
def relative_strength(df, index_df):

    etf_return = (
        df['close'].iloc[-1]
        /
        df['close'].iloc[-20]
        - 1
    ) * 100

    index_return = (
        index_df['close'].iloc[-1]
        /
        index_df['close'].iloc[-20]
        - 1
    ) * 100

    return round(
        etf_return - index_return,
        2
    )

# =========================================================
# 波动率压缩
# =========================================================
def volatility_compress(df):

    latest_atr = df['atr'].iloc[-1]

    atr_mean = (
        df['atr'].rolling(20).mean().iloc[-1]
    )

    return latest_atr < atr_mean * 0.8

# =========================================================
# 主线启动
# =========================================================
def mainline_start(df):

    latest = df.iloc[-1]

    range30 = (

        df['high'].rolling(30).max().iloc[-2]

        /

        df['low'].rolling(30).min().iloc[-2]
    )

    breakout = (

        latest['close']

        >

        df['high'].rolling(30).max().iloc[-2]
    )

    volume_expand = (

        latest['vol']

        >

        df['vol5'].iloc[-2] * 1.5
    )

    return (

        range30 < 1.25

        and breakout

        and volume_expand
    )

# =========================================================
# 主升浪
# =========================================================
def main_uptrend(df):

    latest = df.iloc[-1]

    return (

        latest['ma5']

        >

        latest['ma10']

        >

        latest['ma20']

        and latest['slope20'] > 2

        and latest['pct5']
        >
        latest['pct10'] / 2
    )

# =========================================================
# 第一次低吸
# =========================================================
def first_dip(df):

    latest = df.iloc[-1]

    try:

        breakout_recent = (

            df['close'].rolling(20).max().shift(5)

            <

            df['close'].shift(5)
        )

        return (

            breakout_recent.iloc[-1]

            and latest['close'] > latest['ma20']

            and latest['vol'] < latest['vol5']

            and abs(

                latest['close']
                - latest['ma10']

            ) / latest['ma10'] < 0.015
        )

    except:

        return False

# =========================================================
# 趋势衰竭
# =========================================================
def trend_exhaust(df):

    latest = df.iloc[-1]

    upper_shadow = (

        latest['high']
        - latest['close']

    ) / latest['close']

    volume_blowoff = (

        latest['vol']

        >

        df['vol5'].iloc[-1] * 2
    )

    return (

        latest['pct20'] > 20

        and upper_shadow > 0.03

        and volume_blowoff
    )

# =========================================================
# 波段阶段
# =========================================================
def wave_stage(df):

    latest = df.iloc[-1]

    low20 = (
        df['low'].rolling(20).min().iloc[-1]
    )

    rise = (

        latest['close']
        /
        low20
        - 1

    ) * 100

    if rise < 8:

        return '启动初期', rise

    elif rise < 20:

        return '主升阶段', rise

    else:

        return '波段后期', rise

# =========================================================
# AI情绪
# =========================================================
def ai_sentiment(industry):

    score = 50

    events = INDUSTRY_EVENTS.get(
        industry,
        []
    )

    score += len(events) * 5

    return min(score, 100)

# =========================================================
# 板块宽度（简化版）
# =========================================================
def breadth_score(df):

    positive_days = (
        df['pct_chg'].tail(10) > 0
    ).sum()

    return positive_days * 5

# =========================================================
# 成交量结构
# =========================================================
def volume_structure(df):

    latest = df.iloc[-1]

    # 缩量调整
    if (

        latest['close'] > latest['ma20']

        and latest['vol'] < latest['vol5']
    ):

        return 10

    # 放量突破
    if (

        latest['vol']
        >
        latest['vol5'] * 1.5
    ):

        return 15

    return 0

# =========================================================
# 信号等级
# =========================================================
def signal_level(df):

    if (
        mainline_start(df)
        and weekly_trend(df)
    ):

        return 'S'

    if (
        main_uptrend(df)
        and first_dip(df)
    ):

        return 'A'

    if main_uptrend(df):

        return 'B'

    if trend_exhaust(df):

        return 'D'

    return 'C'

# =========================================================
# 买点
# =========================================================
def buy_signal(df):

    if mainline_start(df):

        return '主线启动'

    if first_dip(df):

        return '第一次分歧低吸'

    if main_uptrend(df):

        return '主升浪'

    if trend_exhaust(df):

        return '趋势衰竭'

    return '观察'

# =========================================================
# ETF总评分
# =========================================================
def etf_score(df, industry, index_df):

    latest = df.iloc[-1]

    score = 0

    # =====================================================
    # 趋势
    # =====================================================
    score += latest['pct5'] * 2

    score += latest['pct10']

    # =====================================================
    # 多头排列
    # =====================================================
    if (

        latest['ma5']

        >

        latest['ma10']

        >

        latest['ma20']

    ):

        score += 20

    # =====================================================
    # 趋势斜率
    # =====================================================
    if latest['slope20'] > 2:

        score += 15

    # =====================================================
    # RS
    # =====================================================
    rs = relative_strength(df, index_df)

    score += rs * 1.5

    # =====================================================
    # 主线启动
    # =====================================================
    if mainline_start(df):

        score += 25

    # =====================================================
    # 主升浪
    # =====================================================
    if main_uptrend(df):

        score += 20

    # =====================================================
    # 第一次低吸
    # =====================================================
    if first_dip(df):

        score += 20

    # =====================================================
    # 周线共振
    # =====================================================
    if weekly_trend(df):

        score += 15

    # =====================================================
    # 波动率压缩
    # =====================================================
    if volatility_compress(df):

        score += 10

    # =====================================================
    # 成交量结构
    # =====================================================
    score += volume_structure(df)

    # =====================================================
    # 板块宽度
    # =====================================================
    score += breadth_score(df)

    # =====================================================
    # AI情绪
    # =====================================================
    score += ai_sentiment(industry) * 0.3

    # =====================================================
    # 波段
    # =====================================================
    stage, rise = wave_stage(df)

    if rise > 20:

        score -= 15

    # =====================================================
    # 趋势衰竭
    # =====================================================
    if trend_exhaust(df):

        score -= 30

    # =====================================================
    # 波动率惩罚
    # =====================================================
    score -= latest['volatility']

    return round(score, 2), rs

# =========================================================
# 市场风格
# =========================================================
def calc_style_score(df):

    return (
        df["pct_chg"].mean() * 2
        + (df["pct_chg"] > 3).sum() * 3
        + (df["pct_chg"] > 5).sum() * 5
        + df["amount"].sum() / 1e8
    )

def calc_style_trend(close):

    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()

    score = 0

    if ma5.iloc[-1] > ma10.iloc[-1]:
        score += 50

    if close.iloc[-1] > ma5.iloc[-1]:
        score += 50

    return score

import pandas as pd
import numpy as np

# =========================================================
# 市场风格轮动分析
# =========================================================
def market_style(result_df, history_style_df=None):

    styles = {

        'AI科技成长': [
            '人工智能','AI','算力','CPO','光模块',
            '液冷','服务器','半导体','芯片','先进封装',
            '存储','EDA','软件','信创','鸿蒙',
            '数据要素','云计算','大模型',
            '机器人','人形机器人','自动驾驶'
        ],

        '消费成长': [
            '消费电子','苹果','MR','VR',
            '智能穿戴','游戏','传媒',
            '影视','旅游','食品','白酒','医美'
        ],

        '高端制造': [
            '新能源车','锂电','储能',
            '风电','光伏','军工',
            '工业母机','机器人',
            '高铁','航空发动机'
        ],

        '金融地产': [
            '证券','互联网金融',
            '银行','保险','地产','REITs'
        ],

        '红利防御': [
            '红利','高股息','央企',
            '公用事业','电力',
            '煤炭','运营商','港口'
        ],

        '周期资源': [
            '黄金','有色','铜',
            '稀土','钢铁','化工',
            '石油','天然气'
        ],

        '医药医疗': [
            '创新药','CXO','医疗器械',
            '中药','生物医药','AI医疗'
        ],

        '全球出海': [
            '跨境电商','出口',
            '航运','面板',
            '家电','汽车出口'
        ]
    }

    all_result = []

    # =====================================================
    # 当前风格评分
    # =====================================================
    for style, sectors in styles.items():

        df_style = result_df[
            result_df['行业'].isin(sectors)
        ]

        if len(df_style) == 0:
            continue

        # =========================
        # 基础强度
        # =========================
        score = (
            df_style['总评分'].mean()
        )

        # =========================
        # 热度
        # =========================
        hot = (
            (df_style['涨跌幅'] > 3).sum() * 2
            + (df_style['涨跌幅'] > 5).sum() * 5
        )

        # =========================
        # 成交额
        # =========================
        amount_score = (
            df_style['成交额'].sum() / 1e8
        )

        # =========================
        # 趋势强度
        # =========================
        trend_score = (
            (df_style['涨跌幅'] > 0).mean() * 100
        )

        total_score = (
            score * 0.5
            + hot * 0.2
            + trend_score * 0.2
            + amount_score * 0.1
        )

        all_result.append({

            '风格': style,

            '当前得分': round(total_score, 2),

            '热度': round(hot, 2),

            '趋势强度': round(trend_score, 2),

            '成交额': round(amount_score, 2)
        })

    style_df = pd.DataFrame(all_result)
    save_style_history(style_df)
    history_style_df = load_style_history()

    # =====================================================
    # 风格轮动（核心）
    # =====================================================
    if history_style_df is not None and len(history_style_df) > 0:

        latest_history = history_style_df.groupby(
            '风格'
        ).tail(1)

        style_df = style_df.merge(

            latest_history[['风格', '当前得分']],

            on='风格',

            how='left',

            suffixes=('', '_昨日')
        )

        # =========================
        # 轮动强度
        # =========================
        style_df['轮动强度'] = (

            style_df['当前得分']
            - style_df['当前得分_昨日']

        ).round(2)

        # =========================
        # 风格状态
        # =========================
        style_df['风格状态'] = np.where(

            style_df['轮动强度'] > 15,

            '主升加强',

            np.where(

                style_df['轮动强度'] > 5,

                '持续活跃',

                np.where(

                    style_df['轮动强度'] < -10,

                    '退潮',

                    '震荡'
                )
            )
        )

    else:

        style_df['轮动强度'] = 0
        style_df['风格状态'] = '未知'

    # =====================================================
    # 排序
    # =====================================================
    style_df = style_df.sort_values(

        ['当前得分', '轮动强度'],

        ascending=False
    )

    return style_df

# =========================================================
# DeepSeek日报
# =========================================================
def deepseek_report(result_df, style_df, risk_state, emotion_text, sector_text, sector_text_his, portfolio_text="", last_report_summary="", history_snap_df=None, decline_report="", new_positions=None):

    # 构建持仓和延续性信息
    decline_section = ""
    if decline_report:
        decline_section = f"""
退潮风控报告（已自动应用评分折扣）：
{decline_report}

请在持仓分析和风险方向中重点提及退潮预警板块。"""

    portfolio_section = ""
    if portfolio_text:
        portfolio_section = f"""
当前持仓分析：
{portfolio_text}

请务必对每只持仓给出明确操作建议（持有/减仓/卖出/加仓），并说明理由。"""
    
    # 新开仓信息
    new_position_section = ""
    if new_positions and len(new_positions) > 0:
        new_positions_text = "\n".join([f"  - {p['industry']}({p['ts_code']}): {p['signal']}, 价格: {p['price']}" for p in new_positions])
        new_position_section = f"""
今日新开仓信号：
{new_positions_text}

以上是今日触发的新开仓信号，请在报告中重点关注并给出明确的买入建议。"""

    last_report_section = ""
    if last_report_summary:
        last_report_section = f"""
昨日报告摘要：
{last_report_summary}

请确保今日分析与昨日建议有延续性，说明昨日建议的执行情况。"""

    history_section = ""
    if history_snap_df is not None and not history_snap_df.empty:
        # 取近3日TOP5 ETF评分变化
        top_today = result_df.nlargest(5, '总评分')[['行业', 'ETF', '总评分', '涨跌幅']].copy()
        top_today['日期'] = TRADE_DATE

        pivot = history_snap_df.pivot_table(
            index='industry', columns='trade_date', values='score', aggfunc='first'
        )
        # 只取近3日
        recent_dates = sorted(pivot.columns)[-3:]
        pivot = pivot[recent_dates]
        # 取今日TOP5对应的行业
        top_industries = top_today['行业'].tolist()
        pivot_top = pivot[pivot.index.isin(top_industries)]

        if not pivot_top.empty:
            history_section = f"""
近3日主线评分变化：
{pivot_top.to_string()}

请分析主线是加强还是减弱，轮动方向是否发生变化。"""

    prompt = f"""
你是中国顶级ETF基金经理，连续管理这只组合，每天给出延续性分析。

当前市场情绪：

{emotion_text}

当前最强主线列表：

{sector_text}

近10日最强主线列表:

{sector_text_his}

当前市场状态：

{risk_state}

市场风格：

{style_df.to_string(index=False)}

ETF数据：

{result_df.to_string(index=False)}
{decline_section}
{portfolio_section}
{new_position_section}
{last_report_section}
{history_section}
请综合分析以下内容,输出：

# ETF日报{TRADE_DATE}

内容：

1、大盘分析和行情主线（与昨日对比变化）
2、持仓跟踪分析（逐只分析盈亏、趋势变化、操作建议：持有/减仓/清仓/加仓）
3、适合低吸方向（区分：新开仓 vs 对持仓加仓）
4、接近高潮方向（注意风险）
5、风险方向
6、明日策略（含代码、名称、价格、具体操作）
7、仓位建议

格式要求：Markdown格式，适合手机阅读（微信Server酱推送）。

重要：
- 不要用任何HTML标签（如<font>/<b>/<color>），微信不支持
- 风险提示用🔴（红圈）前缀，机会提示用🟢（绿圈）前缀
- 仓位建议格式：「最终建议仓位：XX%」用**粗体**即可
- 持仓建议用Emoji表示：🔴减仓/清仓 🟡观望 🟢持有/加仓
- 所有文字必须纯文本+Markdown，不能有任何HTML
- 如列表方式,请用Markdown列表格式(带序号和换行),如：
    - 项目1
    - 项目2
    - 项目3
"""

    url = "https://api.deepseek.com/chat/completions"

    headers = {

        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",

        "Content-Type": "application/json"
    }

    data = {

        "model": "deepseek-chat",

        "messages": [

            {
                "role": "system",
                "content": "你是顶级A股ETF主线基金经理"
            },

            {
                "role": "user",
                "content": prompt
            }
        ],

        "temperature": 0.2
    }

    try:

        response = requests.post(
            url,
            headers=headers,
            json=data,
            timeout=120
        )

        return response.json()[
            'choices'
        ][0]['message']['content']

    except Exception as e:

        return str(e)

# =========================================================
# 保存报告
# =========================================================
def save_report(content):

    report_file = os.path.join(

        REPORT_DIR,

        f"AI_ETF_Report_{TRADE_DATE}.md"
    )

    with open(

        report_file,

        'w',

        encoding='utf-8'

    ) as f:

        f.write(content)

    return report_file

def save_report_html(content):
    """生成HTML版本的ETF报告（直接包装Markdown内容）"""
    html_file = os.path.join(REPORT_DIR, f"AI_ETF_Report_{TRADE_DATE}.html")

    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI主线ETF日报 {TRADE_DATE}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: #1a1a1a;
            color: #e0e0e0;
            padding: 20px;
            max-width: 1200px;
            margin: 0 auto;
            line-height: 1.6;
        }}
        .header {{
            background: linear-gradient(135deg, #1a3a1a 0%, #0d2d0d 100%);
            padding: 30px;
            border-radius: 12px;
            margin-bottom: 20px;
            text-align: center;
            border: 1px solid #2d5a2d;
        }}
        .header h1 {{
            color: #4ade80;
            font-size: 28px;
            margin: 0 0 10px 0;
        }}
        .header .date {{
            color: #8b949e;
            font-size: 14px;
        }}
        .report-content {{
            background: #161b22;
            border-radius: 12px;
            padding: 30px;
            border: 1px solid #30363d;
            font-size: 15px;
        }}
        .report-content pre {{
            white-space: pre-wrap;
            word-wrap: break-word;
            font-family: inherit;
            margin: 0;
            color: #e0e0e0;
        }}
        .footer {{
            text-align: center;
            color: #6e7681;
            margin-top: 30px;
            font-size: 13px;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>📊 AI主线ETF日报</h1>
        <div class="date">交易日: {TRADE_DATE} | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
    </div>
    <div class="report-content">
        <pre>{content}</pre>
    </div>
    <div class="footer">
        <p>本报告由AI生成，仅供参考，不构成投资建议</p>
    </div>
</body>
</html>"""

    with open(html_file, 'w', encoding='utf-8') as f:
        f.write(html_content)

    return html_file

# =========================================================
# 手机推送
# =========================================================
def send_report(content):

    if not SERVERCHAN_KEY:

        return

    # 清理HTML标签（Server酱不支持HTML）
    import re
    # 去掉所有HTML标签，保留文字
    content = re.sub(r'<[^>]+>', '', content)
    # 替换常见HTML实体
    content = content.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')

    url = (
        f"https://sctapi.ftqq.com/"
        f"{SERVERCHAN_KEY}.send"
    )

    data = {

        "title": f"ETF日报{TRADE_DATE}",

        "desp": content
    }

    try:

        requests.post(
            url,
            data=data,
            timeout=30
        )

        print("推送成功")

    except Exception as e:

        print("推送失败:", e)

# =========================================================
# 主程序
# =========================================================
def main():

    print("=" * 60)

    print("AI主线ETF系统 v5.0（持仓延续版）")

    print("=" * 60)

    init_style_table()
    init_portfolio_table()

    # =====================================================
    # 加载昨日持仓
    # =====================================================
    portfolio_df = load_portfolio()
    if not portfolio_df.empty:
        print(f"\n[持仓] 当前持仓: {len(portfolio_df)} 只")
        for _, p in portfolio_df.iterrows():
            print(f"  - {p['industry']}({p['ts_code']}): 买入价{p['buy_price']}({p['buy_date']})")
    else:
        print("\n[持仓] 当前无持仓")

    # =====================================================
    # 检查昨日策略建议是否触发买入
    # =====================================================
    triggered_orders = check_and_trigger_orders(None)
    if triggered_orders:
        print(f"\n[触发买入] 今日触发 {len(triggered_orders)} 条策略")
        for order in triggered_orders:
            print(f"  - {order['industry']}: 建议价{order['suggest_price']} -> 买入价{order['actual_price']}")
        # 重新加载持仓
        portfolio_df = load_portfolio()

    # 清理过期订单
    cleanup_expired_orders(days=5)

    # 加载昨日报告摘要
    last_report_summary = load_last_report()
    if last_report_summary:
        print(f"\n[报告] 昨日报告已加载 ({len(last_report_summary)}字)")

    # 加载历史快照
    history_snap_df = load_daily_snapshot(days=5)
    if not history_snap_df.empty:
        print(f"\n[快照] 历史快照: {history_snap_df['trade_date'].nunique()}天")
    
    # =====================================================
    # 指数
    # =====================================================
    index_df = get_index_data()

    index_df = calc_indicators(index_df)

    # =========================板块分析
    sector_df = block.analyze_hot_sectors()

    # =========================
    # Decline Risk Control
    # =========================
    decline_warnings = []
    decline_report_text = ''
    if 'sector_state' in dir(block):
        zhuxian_col = None
        pingfen_col = None
        for c in sector_df.columns:
            if c in ('主线', 'name'):
                zhuxian_col = c
            if c in ('评分', 'score', 'pingfen'):
                pingfen_col = c
        for idx, row in sector_df.iterrows():
            name = str(row.get(zhuxian_col, '')) if zhuxian_col else ''
            state = block.sector_state.get(name)
            if state and len(state.get('history', [])) >= 3:
                risk = drc.calc_decline_risk(name, state['history'][-1], state)
                sector_df.at[idx, '退潮等级'] = risk['level']
                sector_df.at[idx, '退潮信号'] = risk['detail']
                sector_df.at[idx, '退潮折扣'] = risk['discount']
                if risk['level'] >= 1:
                    decline_warnings.append({
                        'sector': name,
                        'level': risk['level'],
                        'level_label': drc.LEVEL_LABELS.get(risk['level'], str(risk['level'])),
                        'signals': ','.join(risk['signal_labels'])
                    })
        if decline_warnings:
            decline_warnings.sort(key=lambda x: x['level'], reverse=True)
            decline_report_text = drc.format_decline_report(decline_warnings)
            print("\n[!!退潮预警!!]")
            for w in decline_warnings:
                print(f"  L{w['level']}: {w['sector']} - {w['signals']}")
    # =========================
    # 市场情绪
    # =========================
    emotion_result = emotion.analyze_market_emotion(
        sector_df
    )

    emotion_text = ""

    if emotion_result:

        emotion_text = str(emotion_result)

    print(emotion_text)

    # 提取情绪分数
    emotion_score = 50
    if isinstance(emotion_result, dict):
        emotion_score = emotion_result.get('情绪分', 50)
    elif isinstance(emotion_result, str):
        import re
        m = re.search(r'情绪[分：:]+(\d+)', emotion_result)
        if m:
            emotion_score = int(m.group(1))

    if not sector_df.empty:

        print("\n========== 最强主线板块 ==========\n")

        top_sector = sector_df.head(20)

        print(top_sector)

    else:

        top_sector = pd.DataFrame()

    sector_text = ""
    if not top_sector.empty:

        sector_text = top_sector.to_string(index=False)

    sector_df_his = block.load_history()
    sector_text_his = sector_df_his.to_string(index=False)

    # =====================================================
    # 市场风险 & 仓位（优先使用emotion模块）
    # =====================================================
    risk_state, position = market_risk(index_df)

    # 用emotion模块的仓位系统替代简单MA20判断
    if isinstance(emotion_result, dict) and '最终建议仓位' in emotion_result:
        emotion_pos_str = emotion_result['最终建议仓位']
        try:
            position = int(emotion_pos_str.replace('%', '')) / 100
        except Exception:
            pass
        risk_state = emotion_result.get('指数环境', risk_state)
    elif isinstance(emotion_result, dict) and '情绪分' in emotion_result:
        e_score = emotion_result.get('情绪分', 50)
        from emotion import calc_final_position, analyze_index_environment
        try:
            idx_env = analyze_index_environment(get_index_data())
            pos_data = calc_final_position(e_score, idx_env)
            position = pos_data['final_pos']
            risk_state = idx_env.get('trend', risk_state)
        except Exception:
            pass

    print("市场状态:", risk_state)

    print("建议仓位:", position)

    position_pct = round(position * 100)

    all_result = []

    # =====================================================
    # ETF分析
    # =====================================================
    for industry, ts_code in ETF_POOL.items():

        print(f"\n分析 {industry}")

        df = get_etf_data(ts_code)

        if df is None:

            continue

        if len(df) < 60:

            continue

        df = calc_indicators(df)

        latest = df.iloc[-1]

        # =================================================
        # 评分
        # =================================================
        score, rs = etf_score(

            df,

            industry,

            index_df
        )

        # =================================================
        # 波段
        # =================================================
        stage, rise = wave_stage(df)

        # =================================================
        # 信号
        # =================================================
        signal = buy_signal(df)

        level = signal_level(df)

        all_result.append({

            '行业': industry,

            'ETF': ts_code,

            '收盘价': round(
                latest['close'],
                2
            ),
            '涨跌幅': round(
                latest['pct_chg'],
                2
            ),

            '成交额': round(
                latest['amount'] / 1e8,
                2
            ),
            'RS强度': rs,

            '5日涨幅': round(
                latest['pct5'],
                2
            ),

            '10日涨幅': round(
                latest['pct10'],
                2
            ),

            '20日涨幅': round(
                latest['pct20'],
                2
            ),

            '波段阶段': stage,

            '波段涨幅': round(
                rise,
                2
            ),

            'AI情绪': ai_sentiment(
                industry
            ),

            '信号': signal,

            '等级': level,

            '总评分': score
        })

    # =====================================================
    # DataFrame
    # =====================================================
    result_df = pd.DataFrame(all_result)
    print(result_df)
    result_df = result_df.sort_values(
        '总评分',
        ascending=False
    )

    # =====================================================
    # 更新持仓价格 & 持仓分析
    # =====================================================
    update_portfolio_prices(result_df)
    portfolio_df = load_portfolio()
    portfolio_text = analyze_portfolio(result_df, portfolio_df)
    
    # 保存今日快照
    save_daily_snapshot(result_df, position_pct, emotion_score)

    # =====================================================
    # 检查卖出信号
    # =====================================================
    sell_actions = check_sell_signals(result_df, portfolio_df)
    if sell_actions:
        print(f"\n[卖出检查] 发现 {len(sell_actions)} 个卖出信号")
        execute_sell_actions(sell_actions)
        # 重新加载持仓
        portfolio_df = load_portfolio()
        portfolio_text = analyze_portfolio(result_df, portfolio_df)

    # =====================================================
    # 新开仓逻辑：当出现买入信号且未持仓时记录交易
    # =====================================================
    new_positions = []
    
    # 获取主线板块列表（用于过滤）
    main_sectors = []
    if not sector_df.empty:
        # 提取主线板块名称
        for col in sector_df.columns:
            if col in ['主线', 'name', '行业', '板块']:
                main_sectors = sector_df[col].dropna().tolist()
                break
    
    for _, row in result_df.iterrows():
        ts_code = row['ETF']
        industry = row['行业']
        signal = row['信号']
        price = row['收盘价']
        score = row['总评分']
        
        # 判断是否已持仓
        already_holding = not portfolio_df[portfolio_df['ts_code'] == ts_code].empty
        
        # 判断是否为买入信号
        buy_signals = ['主线启动', '第一次分歧低吸', '主升浪']
        
        # 综合过滤条件
        # 1. 是否属于主线板块
        is_main_sector = False
        if main_sectors:
            is_main_sector = industry in main_sectors or any(sector in industry for sector in main_sectors)
        
        # 2. 市场情绪是否合适（情绪指数 > 50）
        emotion_ok = emotion_score > 50
        
        # 3. 总评分是否足够高（>= 60）
        score_ok = score >= 60
        
        # 4. 没有退潮风险
        no_decline_risk = industry not in [w['sector'] for w in decline_warnings] if decline_warnings else True
        
        # 满足所有条件才开仓
        if signal in buy_signals and not already_holding and emotion_ok and score_ok and is_main_sector and no_decline_risk:
            print(f"\n[策略建议] 新仓信号: {industry}({ts_code}) - {signal}, 价格: {price}, 评分: {score}")
            # 保存待买入订单（次日检查是否触发）
            suggest_price = round(price * 1.01, 3)  # 建议价格为今日收盘价上浮1%
            save_pending_order(ts_code, industry, signal, suggest_price, position_pct, score)
            new_positions.append({
                'industry': industry,
                'ts_code': ts_code,
                'signal': signal,
                'suggest_price': suggest_price,
                'current_price': price
            })
    
    # 重新加载持仓（包含新开仓）
    portfolio_df = load_portfolio()
    portfolio_text = analyze_portfolio(result_df, portfolio_df)

    # =====================================================
    # 市场风格
    # =====================================================
    style_df = market_style(result_df)

    # =====================================================
    # 输出
    # =====================================================
    print("\n")

    print("=" * 60)

    print("ETF主线排名")

    print("=" * 60)

    print(result_df)

    print("\n")

    print("=" * 60)

    print("市场风格")

    print("=" * 60)

    print(style_df)

    if portfolio_text:
        print("\n[持仓] 持仓分析:")
        print(portfolio_text)

    # =====================================================
    # AI日报（增强版：含持仓跟踪+延续性分析）
    # =====================================================
    print("\nAI日报生成中...\n")

    report = deepseek_report(

        result_df,

        style_df,

        risk_state,
        emotion_text, sector_text, sector_text_his,
        portfolio_text=portfolio_text,
        last_report_summary=last_report_summary,
        history_snap_df=history_snap_df,
        decline_report=decline_report_text,
        new_positions=new_positions
    )

    # =====================================================
    # 保存
    # =====================================================
    report_file = save_report(report)

    print("\n")

    print("=" * 60)

    print("AI主线ETF日报")

    print("=" * 60)

    print(report)

    print("\n报告已保存:", report_file)

    # =====================================================
    # 保存HTML版本报告
    # =====================================================
    html_file = save_report_html(report)
    print("HTML报告已保存:", html_file)

    # =====================================================
    # 手机推送
    # =====================================================
    send_report(report)

    print("\n系统运行完成")

if __name__ == '__main__':

    main()