# =========================================================
# AI主线ETF系统 v5.0（主题分析增强版）
# =========================================================
# 基于 etf_quant.py 优化
# 升级内容：
#
# 1、用 theme_trend_sentiment_score.py 的主题趋势+情绪分析代替原 block.py
# 2、保留原 ETF 技术分析体系
# 3、保留持仓管理和 DeepSeek 日报
# 4、主题分析 + ETF 分析双驱动
# =========================================================

import os
import io
import sys
import time
import json
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv
import sqlite3

# 添加上级目录到路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# =========================
# 环境变量（先设置token再导入其他模块）
# =========================
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", ".env"))

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
SERVERCHAN_KEY = os.getenv("WECHAT_SCKEY")

# =========================================================
# 初始化 tushare token（避免 tk.csv问题）
# =========================================================
import tushare as ts
ts.pro_api(TUSHARE_TOKEN)

# 现在导入其他模块
pro = ts.pro_api()

# 缓存/报告目录统一到 d:\stock\ 下
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STOCK_DATA_DIR = r"d:\mystock"
CACHE_DIR = os.path.join(STOCK_DATA_DIR, "cache_daily")
REPORT_DIR = os.path.join(STOCK_DATA_DIR, "report_daily")
DB_PATH = os.path.join(REPORT_DIR, "etf_result.db")
NEWS_CACHE_DIR = os.path.join(STOCK_DATA_DIR, "news_cache")

os.makedirs(STOCK_DATA_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)
os.makedirs(NEWS_CACHE_DIR, exist_ok=True)

# =========================================================
# 持仓管理
# =========================================================
def migrate_old_data():
    """将旧的portfolio表数据迁移到新表"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # 检查旧表是否存在
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='portfolio'")
        if not cursor.fetchone():
            conn.close()
            return
        
        # 迁移数据到新表
        cursor.execute("SELECT * FROM portfolio WHERE status='holding'")
        old_data = cursor.fetchall()
        
        for row in old_data:
            # 插入到active_holdings
            cursor.execute("""
                INSERT OR IGNORE INTO active_holdings 
                (ts_code, industry, buy_date, buy_price, shares, target_weight, stop_loss, take_profit, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (row[0], row[1], row[2], row[3], row[4] if len(row) > 4 else 0, 
                  row[5] if len(row) > 5 else 0, row[6] if len(row) > 6 else 0, 
                  row[7] if len(row) > 7 else 0, row[8] if len(row) > 8 else 'holding'))
        
        print(f"[迁移] 已迁移 {len(old_data)} 条持仓数据")
        conn.commit()
    except Exception as e:
        print(f"[迁移] 数据迁移出错: {e}")
    conn.close()

def init_portfolio_table():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 新的持仓表：按日期存储
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_holding (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT,
            ts_code TEXT,
            industry TEXT,
            weight REAL DEFAULT 0,
            buy_date TEXT,
            buy_price REAL,
            current_price REAL,
            pnl_pct REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 活跃持仓表：存储当前状态
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS active_holdings (
            ts_code TEXT PRIMARY KEY,
            industry TEXT,
            buy_date TEXT,
            buy_price REAL,
            current_price REAL,
            shares INTEGER DEFAULT 0,
            target_weight REAL DEFAULT 0,
            stop_loss REAL DEFAULT 0,
            take_profit REAL DEFAULT 0,
            status TEXT DEFAULT 'holding'
        )
    """)
    
    # 交易日志表
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
    
    # ETF每日快照表
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
    
    # 待处理订单表
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
    
    # 尝试迁移旧数据
    migrate_old_data()

def load_portfolio():
    print(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("SELECT * FROM active_holdings WHERE status='holding'", conn)
    print(df)
    conn.close()
    return df

def save_portfolio_action(ts_code, industry, action, price, shares=0, pnl=0, reason='', target_weight=0):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    today = TRADE_DATE
    if action == 'buy':
        cursor.execute("""
            INSERT OR REPLACE INTO active_holdings
            (ts_code, industry, buy_date, buy_price, current_price, shares, target_weight, stop_loss, take_profit, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'holding')
        """, (ts_code, industry, today, price, price, shares, target_weight, round(price * 0.95, 3), round(price * 1.20, 3)))
    elif action == 'sell':
        cursor.execute("UPDATE active_holdings SET status='closed' WHERE ts_code=? AND status='holding'", (ts_code,))
    cursor.execute("INSERT INTO trade_log (trade_date, ts_code, industry, action, price, shares, pnl, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (today, ts_code, industry, action, price, shares, pnl, reason))
    conn.commit()
    conn.close()

def update_portfolio_prices(result_df, target_position_pct=25):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    portfolio = pd.read_sql("SELECT * FROM active_holdings WHERE status='holding'", conn)
    
    # 重新计算权重
    hold_count = len(portfolio)
    per_etf_weight = target_position_pct / hold_count if hold_count > 0 else 0
    
    # 更新价格和权重，并保存每日持仓快照
    for _, row in portfolio.iterrows():
        match = result_df[result_df['ETF'] == row['ts_code']]
        current_price = row['buy_price']
        pnl_pct = 0
        if not match.empty:
            current_price = match.iloc[0]['收盘价']
            pnl_pct = round((current_price - row['buy_price']) / row['buy_price'] * 100, 2)
        
        # 更新活跃持仓
        cursor.execute("UPDATE active_holdings SET current_price=?, target_weight=? WHERE ts_code=? AND status='holding'",
                      (current_price, per_etf_weight, row['ts_code']))
        
        # 保存每日持仓
        cursor.execute("""
            INSERT OR REPLACE INTO daily_holding
            (trade_date, ts_code, industry, weight, buy_date, buy_price, current_price, pnl_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (TRADE_DATE, row['ts_code'], row['industry'], per_etf_weight, row['buy_date'], row['buy_price'], current_price, pnl_pct))
    
    conn.commit()
    conn.close()

def save_daily_holding_snapshot(portfolio_df, result_df, target_position_pct=25):
    """保存每日持仓快照"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    hold_count = len(portfolio_df)
    per_etf_weight = target_position_pct / hold_count if hold_count > 0 else 0
    
    # 删除当日旧数据
    cursor.execute("DELETE FROM daily_holding WHERE trade_date=?", (TRADE_DATE,))
    
    for _, row in portfolio_df.iterrows():
        match = result_df[result_df['ETF'] == row['ts_code']]
        current_price = row['buy_price'] if 'buy_price' in row else 0
        pnl_pct = 0
        if not match.empty:
            current_price = match.iloc[0]['收盘价']
            pnl_pct = round((current_price - row['buy_price']) / row['buy_price'] * 100, 2)
        
        cursor.execute("""
            INSERT INTO daily_holding
            (trade_date, ts_code, industry, weight, buy_date, buy_price, current_price, pnl_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (TRADE_DATE, row['ts_code'], row['industry'], row.get('target_weight', per_etf_weight), 
                 row['buy_date'], row['buy_price'], current_price, pnl_pct))
    
    conn.commit()
    conn.close()

def load_daily_holding(date_str=None):
    """加载每日持仓数据"""
    conn = sqlite3.connect(DB_PATH)
    if date_str:
        df = pd.read_sql("SELECT * FROM daily_holding WHERE trade_date=? ORDER BY id", conn, params=(date_str,))
    else:
        df = pd.read_sql("SELECT * FROM daily_holding WHERE trade_date=? ORDER BY id", conn, params=(TRADE_DATE,))
    conn.close()
    return df

def save_daily_snapshot(result_df, position_pct, emotion_score):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM daily_snapshot WHERE trade_date=?", (TRADE_DATE,))
    for _, row in result_df.iterrows():
        cursor.execute("INSERT INTO daily_snapshot (trade_date, ts_code, industry, score, signal, stage, pct_chg, position_pct, emotion) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (TRADE_DATE, row['ETF'], row['行业'], row['总评分'], row['信号'], row['波段阶段'], row['涨跌幅'], position_pct, emotion_score))
    conn.commit()
    conn.close()

def save_pending_order(ts_code, industry, signal, suggest_price, position_pct, score):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO pending_orders (trade_date, ts_code, industry, signal, suggest_price, position_pct, score, status) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')",
                   (TRADE_DATE, ts_code, industry, signal, suggest_price, position_pct, score))
    conn.commit()
    conn.close()
    print(f"   [待买入] 保存策略建议: {industry}({ts_code}) 建议价:{suggest_price}")

def check_and_trigger_orders(result_df):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    yesterday = (datetime.strptime(TRADE_DATE, '%Y%m%d') - timedelta(days=1)).strftime('%Y%m%d')
    pending_df = pd.read_sql(f"SELECT * FROM pending_orders WHERE status='pending' AND trade_date='{yesterday}'", conn)
    triggered_orders = []
    if pending_df.empty:
        conn.close()
        return triggered_orders
    print(f"\n[检查待买入订单] 昨日待处理: {len(pending_df)} 条")
    for _, order in pending_df.iterrows():
        holding_df = pd.read_sql(f"SELECT * FROM active_holdings WHERE ts_code='{order['ts_code']}' AND status='holding'", conn)
        if not holding_df.empty:
            continue
        try:
            df = pro.fund_daily(ts_code=order['ts_code'], start_date=TRADE_DATE, end_date=TRADE_DATE)
            if df.empty:
                continue
            today_low = df.iloc[0]['low']
            today_close = df.iloc[0]['close']
            if today_low <= order['suggest_price']:
                actual_price = min(order['suggest_price'], today_close)
                cursor.execute("INSERT OR REPLACE INTO active_holdings (ts_code, industry, buy_date, buy_price, current_price, shares, target_weight, stop_loss, take_profit, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'holding')",
                               (order['ts_code'], order['industry'], TRADE_DATE, actual_price, actual_price, 100, order['position_pct'] * 0.2, round(actual_price * 0.95, 3), round(actual_price * 1.20, 3)))
                cursor.execute("INSERT INTO trade_log (trade_date, ts_code, industry, action, price, shares, pnl, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                               (TRADE_DATE, order['ts_code'], order['industry'], 'buy', actual_price, 100, 0, f"策略触发:{order['signal']}"))
                cursor.execute("UPDATE pending_orders SET status='triggered', triggered_date=?, triggered_price=? WHERE id=?", (TRADE_DATE, actual_price, order['id']))
                triggered_orders.append({'industry': order['industry'], 'ts_code': order['ts_code'], 'actual_price': actual_price})
        except:
            continue
    conn.commit()
    conn.close()
    return triggered_orders

def cleanup_expired_orders(days=5):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    expire_date = (datetime.strptime(TRADE_DATE, '%Y%m%d') - timedelta(days=days)).strftime('%Y%m%d')
    cursor.execute("UPDATE pending_orders SET status='expired' WHERE status='pending' AND trade_date < ?", (expire_date,))
    conn.commit()
    conn.close()

def load_daily_snapshot(date_str=None, days=5):
    conn = sqlite3.connect(DB_PATH)
    if date_str:
        df = pd.read_sql(f"SELECT * FROM daily_snapshot WHERE trade_date='{date_str}'", conn)
    else:
        start = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
        df = pd.read_sql(f"SELECT * FROM daily_snapshot WHERE trade_date >= '{start}' ORDER BY trade_date ASC", conn)
    conn.close()
    return df

def load_last_report():
    report_dir = REPORT_DIR
    if not os.path.exists(report_dir):
        return ""
    reports = sorted([f for f in os.listdir(report_dir) if f.startswith('AI_ETF_Report_') and f.endswith('.md')])
    if len(reports) >= 2:
        last_report_file = os.path.join(report_dir, reports[-2]) if reports[-1].endswith(TRADE_DATE + '.md') else os.path.join(report_dir, reports[-1])
        try:
            with open(last_report_file, 'r', encoding='utf-8') as f:
                content = f.read()
            return content[:500] + '...' if len(content) > 500 else content
        except:
            pass
    return ""

def analyze_portfolio(result_df, portfolio_df, target_position_pct=25):
    if portfolio_df.empty:
        return ""
    lines = []
    
    # 计算当前总持仓权重
    total_current_weight = portfolio_df['target_weight'].sum()
    cash_pct = 100 - total_current_weight
    
    lines.append(f"  【总仓位】当前持仓: {total_current_weight:.1f}% | 目标仓位: {target_position_pct}% | 现金: {cash_pct:.1f}%\n")
    
    for _, p in portfolio_df.iterrows():
        today_data = result_df[result_df['ETF'] == p['ts_code']]
        if today_data.empty:
            lines.append(f"  - {p['industry']}({p['ts_code']}): 权重{p.get('target_weight', 0):.1f}% | 买入价{p['buy_price']}({p['buy_date']}), 今日无数据")
            continue
        row = today_data.iloc[0]
        pnl_pct = round((row['收盘价'] - p['buy_price']) / p['buy_price'] * 100, 2)
        hold_days = (datetime.strptime(TRADE_DATE, '%Y%m%d') - datetime.strptime(p['buy_date'], '%Y%m%d')).days
        alert = ""
        if row['收盘价'] <= p['stop_loss']:
            alert = "[!!止损!!]"
        elif row['收盘价'] >= p['take_profit']:
            alert = "[!!止盈!!]"
        lines.append(f"  - {p['industry']}({p['ts_code']}): 权重{p.get('target_weight', 0):.1f}% | 买入{p['buy_price']} 现价{row['收盘价']} 盈亏{pnl_pct:+.2f}% 持有{hold_days}天 {alert}")
    return "\n".join(lines)

def check_sell_signals(result_df, portfolio_df):
    if portfolio_df.empty:
        return []
    sell_actions = []
    for _, p in portfolio_df.iterrows():
        today_data = result_df[result_df['ETF'] == p['ts_code']]
        if today_data.empty:
            continue
        row = today_data.iloc[0]
        sell_reason = None
        if row['收盘价'] <= p['stop_loss']:
            sell_reason = f"止损"
        elif row['收盘价'] >= p['take_profit']:
            sell_reason = f"止盈"
        elif row['信号'] in ['趋势衰竭', '高位滞涨', '破位下跌']:
            sell_reason = f"卖出信号:{row['信号']}"
        if sell_reason:
            sell_actions.append({'ts_code': p['ts_code'], 'industry': p['industry'], 'current_price': row['收盘价'], 'pnl_pct': round((row['收盘价'] - p['buy_price']) / p['buy_price'] * 100, 2), 'reason': sell_reason})
    return sell_actions

def execute_sell_actions(sell_actions):
    if not sell_actions:
        return
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    for action in sell_actions:
        print(f"\n[卖出] {action['industry']}: 价格{action['current_price']}, 盈亏{action['pnl_pct']:+.2f}%")
        cursor.execute("UPDATE active_holdings SET status='closed' WHERE ts_code=? AND status='holding'", (action['ts_code'],))
        cursor.execute("INSERT INTO trade_log (trade_date, ts_code, industry, action, price, shares, pnl, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (TRADE_DATE, action['ts_code'], action['industry'], 'sell', action['current_price'], 100, action['pnl_pct'], action['reason']))
    conn.commit()
    conn.close()

def rebalance_portfolio(portfolio_df, target_position_pct, result_df):
    if portfolio_df.empty:
        return []
    rebalance_actions = []
    conn = sqlite3.connect(DB_PATH)
    
    # 计算每只持仓的目标权重：平均分配
    hold_count = len(portfolio_df)
    if hold_count > 0:
        per_etf_weight = target_position_pct / hold_count
    else:
        per_etf_weight = 0
    
    for _, p in portfolio_df.iterrows():
        today_data = result_df[result_df['ETF'] == p['ts_code']]
        new_target_weight = per_etf_weight
        if p.get('target_weight', 0) > 0:
            weight_change = (new_target_weight - p['target_weight']) / p['target_weight'] * 100 if p['target_weight'] > 0 else 100
            if abs(weight_change) > 20:  # 只有变化超过20%才建议调仓
                if weight_change < 0:
                    print(f"   [减仓建议] {p['industry']}: 权重从{p['target_weight']:.1f}%降至{new_target_weight:.1f}%")
                else:
                    print(f"   [加仓建议] {p['industry']}: 权重从{p['target_weight']:.1f}%增至{new_target_weight:.1f}%")
                rebalance_actions.append({'ts_code': p['ts_code'], 'industry': p['industry'], 'new_weight': new_target_weight})
        conn.execute("UPDATE active_holdings SET target_weight=? WHERE ts_code=? AND status='holding'", (new_target_weight, p['ts_code']))
    conn.commit()
    conn.close()
    return rebalance_actions

# =========================================================
# ETF池
# =========================================================
ETF_POOL = {
    '半导体': '512480.SH', '人工智能': '159819.SZ', '算力': '561210.SH', '机器人': '562500.SH',
    '软件': '515230.SH', '通信': '515880.SH', '新能源': '516160.SH', '光伏': '515790.SH',
    '储能': '159566.SZ', '军工': '512660.SH', '创新药': '159992.SZ', '消费电子': '159732.SZ',
    '黄金': '518880.SH', '证券': '512880.SH', '红利': '515180.SH', '银行': '512800.SH',
    '消费': '159928.SZ', '酒': '512690.SH', '电池': '159755.SZ', '有色金属': '516650.SH',
    '芯片': '159995.SZ', '化工': '159870.SZ', '煤炭': '515220.SH', '游戏': '159869.SZ',
    '金融科技': '159851.SZ', '电力': '159611.SZ', '新能源车': '515030.SH','智能驾驶': '516520.SH',
}

# =========================
# 获取最近交易日
# =========================

def get_last_trade_date():

    now = datetime.now()

    # =========================
    # 9点前：视为上一自然日
    # =========================
    if now.hour < 15:

        query_date = (now - timedelta(days=1)).strftime('%Y%m%d')

    else:

        query_date = now.strftime('%Y%m%d')

    # =========================
    # 获取交易日历
    # =========================
    cal = pro.trade_cal(
        exchange='',
        start_date='20200101',
        end_date=query_date
    )

    # 只保留开市日
    cal = cal[cal['is_open'] == 1]

    # 最近交易日
    last_trade_date = cal[
        cal['cal_date'] <= query_date
    ]['cal_date'].max()

    return str(last_trade_date)

TRADE_DATE = get_last_trade_date()
print("当前交易日:", TRADE_DATE)

# =========================================================
# ETF历史数据
# =========================================================
def get_etf_data(ts_code):
    cache_file = os.path.join(CACHE_DIR, f"{ts_code}.csv")
    if os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file)
            df['trade_date'] = df['trade_date'].astype(str)
            if len(df) > 60 and (df['trade_date'] == TRADE_DATE).any():
                return df.sort_values('trade_date')
        except:
            pass
    try:
        df = pro.fund_daily(ts_code=ts_code, start_date='20250101', end_date=TRADE_DATE)
        if not df.empty:
            df = df.sort_values('trade_date')
            df.to_csv(cache_file, index=False)
            time.sleep(0.05)
            return df
    except:
        pass
    return None

def get_index_data():
    cache_file = os.path.join(CACHE_DIR, "000300.csv")
    if os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file)
            df['trade_date'] = df['trade_date'].astype(str)
            if len(df) > 100:
                return df.sort_values('trade_date')
        except:
            pass
    df = pro.index_daily(ts_code='000300.SH', start_date='20250101', end_date=TRADE_DATE)
    df = df.sort_values('trade_date')
    df.to_csv(cache_file, index=False)
    return df

# =========================================================
# 技术指标
# =========================================================
def calc_indicators(df):
    df = df.copy()
    for ma in [5, 10, 20, 60]:
        df[f'ma{ma}'] = df['close'].rolling(ma).mean()
    df['vol5'] = df['vol'].rolling(5).mean()
    for n in [5, 10, 20]:
        df[f'pct{n}'] = (df['close'] / df['close'].shift(n) - 1) * 100
    df['slope20'] = (df['ma20'] / df['ma20'].shift(5) - 1) * 100
    df['volatility'] = df['pct_chg'].rolling(10).std()
    df['tr'] = np.maximum(df['high'] - df['low'], np.maximum(abs(df['high'] - df['close'].shift(1)), abs(df['low'] - df['close'].shift(1))))
    df['atr'] = df['tr'].rolling(14).mean()
    return df

def weekly_trend(df):
    try:
        weekly = df.copy()
        weekly.index = pd.to_datetime(weekly['trade_date'])
        weekly = weekly.resample('W').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'vol': 'sum'})
        weekly['ma5'] = weekly['close'].rolling(5).mean()
        weekly['ma10'] = weekly['close'].rolling(10).mean()
        return weekly['ma5'].iloc[-1] > weekly['ma10'].iloc[-1]
    except:
        return False

def market_risk(index_df):
    index_df['ma20'] = index_df['close'].rolling(20).mean()
    latest = index_df.iloc[-1]
    return ('risk_off', 0.3) if latest['close'] < latest['ma20'] else ('risk_on', 1.0)

def relative_strength(df, index_df):
    return round((df['close'].iloc[-1] / df['close'].iloc[-20] - 1 - (index_df['close'].iloc[-1] / index_df['close'].iloc[-20] - 1)) * 100, 2)

def volatility_compress(df):
    return df['atr'].iloc[-1] < df['atr'].rolling(20).mean().iloc[-1] * 0.8

def mainline_start(df):
    latest = df.iloc[-1]
    return latest['close'] > df['high'].rolling(30).max().iloc[-2] and latest['vol'] > df['vol5'].iloc[-2] * 1.5

def main_uptrend(df):
    latest = df.iloc[-1]
    return latest['ma5'] > latest['ma10'] > latest['ma20'] and latest['slope20'] > 2

def first_dip(df):
    latest = df.iloc[-1]
    try:
        return latest['close'] > latest['ma20'] and latest['vol'] < latest['vol5'] and abs(latest['close'] - latest['ma10']) / latest['ma10'] < 0.015
    except:
        return False

def trend_exhaust(df):
    latest = df.iloc[-1]
    return latest['pct20'] > 20 and (latest['high'] - latest['close']) / latest['close'] > 0.03 and latest['vol'] > df['vol5'].iloc[-1] * 2

def wave_stage(df):
    rise = (df['close'].iloc[-1] / df['low'].rolling(20).min().iloc[-1] - 1) * 100
    return ('波段后期', rise) if rise >= 20 else ('主升阶段', rise) if rise >= 8 else ('启动初期', rise)

def ai_sentiment(industry):
    return min(50 + len(INDUSTRY_EVENTS.get(industry, [])) * 5, 100)

INDUSTRY_EVENTS = {
    '半导体': ['HBM', 'GPU', 'AI芯片'], '人工智能': ['Agent', '大模型'], '算力': ['液冷', '数据中心'],
    '机器人': ['人形机器人'], '创新药': ['FDA', 'BD']
}

def volume_structure(df):
    latest = df.iloc[-1]
    return 15 if latest['vol'] > latest['vol5'] * 1.5 else (10 if latest['close'] > latest['ma20'] and latest['vol'] < latest['vol5'] else 0)

def breadth_score(df):
    return (df['pct_chg'].tail(10) > 0).sum() * 5

def signal_level(df):
    return 'S' if mainline_start(df) and weekly_trend(df) else 'A' if main_uptrend(df) and first_dip(df) else 'B' if main_uptrend(df) else 'D' if trend_exhaust(df) else 'C'

def buy_signal(df):
    return '主线启动' if mainline_start(df) else '第一次分歧低吸' if first_dip(df) else '主升浪' if main_uptrend(df) else '趋势衰竭' if trend_exhaust(df) else '观察'

def etf_score(df, industry, index_df):
    latest = df.iloc[-1]
    score = latest['pct5'] * 2 + latest['pct10']
    if latest['ma5'] > latest['ma10'] > latest['ma20']:
        score += 20
    if latest['slope20'] > 2:
        score += 15
    rs = relative_strength(df, index_df)
    score += rs * 1.5
    for func in [mainline_start, main_uptrend, first_dip, weekly_trend, volatility_compress]:
        if func(df):
            score += 20 if func.__name__ in ['mainline_start', 'main_uptrend'] else 15 if func.__name__ == 'first_dip' else 10
    score += volume_structure(df) + breadth_score(df) + ai_sentiment(industry) * 0.3
    if wave_stage(df)[1] > 20:
        score -= 15
    if trend_exhaust(df):
        score -= 30
    score -= latest['volatility']
    return round(score, 2), rs

def market_style(result_df):
    styles = {'AI科技成长': ['人工智能','算力','半导体','芯片','软件'], '消费成长': ['消费电子','游戏','白酒'], '红利防御': ['红利','煤炭','电力'], '周期资源': ['黄金','有色金属']}
    all_result = []
    for style, sectors in styles.items():
        df_style = result_df[result_df['行业'].isin(sectors)]
        if len(df_style) == 0:
            continue
        score = df_style['总评分'].mean()
        hot = (df_style['涨跌幅'] > 3).sum() * 2
        trend_score = (df_style['涨跌幅'] > 0).mean() * 100
        all_result.append({'风格': style, '当前得分': round(score * 0.6 + hot * 0.2 + trend_score * 0.2, 2), '热度': hot, '趋势强度': trend_score})
    return pd.DataFrame(all_result).sort_values('当前得分', ascending=False)

# =========================================================
# 主题分析结果获取（核心新增）
# =========================================================
def get_theme_analysis():
    """获取主题趋势+情绪分析结果（延迟导入 + 缓存检查，避免 import 时自动执行模块级代码）
    
    输出内容：
    - 60日趋势平均分TOP2中线主题
    - 当日趋势分TOP3短线主线
    """
    # 先查缓存：SQLite 里已有今天数据就直接读，不跑 theme_score 模块
    theme_db = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "solo", "cache_daily", "theme_trend_sentiment.db"
    )
    cached_results = None
    if os.path.exists(theme_db):
        try:
            conn = sqlite3.connect(theme_db)
            df_cache = pd.read_sql(
                "SELECT * FROM theme_scores WHERE trade_date = ? ORDER BY composite_score DESC",
                conn,
                params=(TRADE_DATE,)
            )
            conn.close()
            if not df_cache.empty:
                cached_results = df_cache.to_dict("records")
        except:
            pass

    if cached_results:
        print(f"[主题分析] 从缓存读取 {len(cached_results)} 个主题评分（{TRADE_DATE}）")
        results = cached_results
        signals = {"climax_warning": [], "buy": [], "dip_buy": []}
        for r in results:
            if r.get("climax_warning", 0) == 1:
                signals["climax_warning"].append(r)
    else:
        try:
            # 延迟导入：只在需要时才导入 theme_trend_sentiment_score
            import importlib
            theme_score = importlib.import_module("theme_trend_sentiment_score")
            print("\n[主题分析] 正在获取主题趋势+情绪分析...")
            results, signals = theme_score.run_theme_analysis()
        except Exception as e:
            print(f"[主题分析] 获取失败: {e}")
            return "【主题分析】获取失败", "【交易信号】获取失败", []

    # ====== 1. 60日趋势平均分TOP2中线主题 ======
    mid_term_lines = ["【中线主题TOP2（60日趋势平均分）】"]
    try:
        # 延迟导入 theme_score 以加载 get_60day_avg_trend_score
        if not cached_results:
            import importlib
            theme_score = importlib.import_module("theme_trend_sentiment_score")
        theme_60day_avg = theme_score.get_60day_avg_trend_score()
        if theme_60day_avg:
            sorted_60day = sorted(theme_60day_avg.items(), key=lambda x: -x[1])
            for i, (theme, avg_score) in enumerate(sorted_60day[:2]):
                # 找今天对应的 trend_score
                r = next((x for x in results if x['theme'] == theme), None)
                today_trend = r['trend_score'] if r else 0
                today_sentiment = r['sentiment_score'] if r else 0
                icon = "🟢" if today_trend >= 70 else "🟡" if today_trend >= 50 else "⚪"
                mid_term_lines.append(f"  {icon} {theme:<10} 60日平均{avg_score:.1f}  今日趋势{today_trend:.1f} 情绪{today_sentiment:.1f}")
        else:
            mid_term_lines.append("  无数据")
    except Exception as e:
        mid_term_lines.append(f"  获取失败: {e}")
    mid_term_lines.append("")
    
    # ====== 2. 当日趋势分TOP3短线主线 ======
    short_term_lines = ["【短线主线TOP3（当日趋势分）】"]
    sorted_by_trend = sorted(results, key=lambda x: x['trend_score'], reverse=True)
    for i, r in enumerate(sorted_by_trend[:3]):
        if r['trend_score'] >= 40:
            td = r.get("trend_detail", {}) or {}
            icon = "🟢" if r['trend_score'] >= 70 else "🟡" if r['trend_score'] >= 50 else "⚪"
            short_term_lines.append(f"  {icon} {r['theme']:<10} 趋势{r['trend_score']:.1f} 情绪{r['sentiment_score']:.1f}  10日{td.get('avg_ret_10', 0):+.1f}%")
    short_term_lines.append("")
    
    theme_analysis_text = "\n".join(mid_term_lines + short_term_lines)

    # 交易信号（精简）
    signal_lines = ["【主题交易信号】"]
    if signals.get("climax_warning"):
        signal_lines.append("\n🚨【高潮警示】")
        for w in signals["climax_warning"][:3]:
            signal_lines.append(f"  ⚠️ {w['theme']}: 趋势{w['trend_score']:.0f} 情绪{w['sentiment_score']:.0f}")
    if signals.get("buy"):
        signal_lines.append("\n🟢【买入信号】")
        for s in signals["buy"][:3]:
            signal_lines.append(f"  {s['theme']}: 趋势{s['trend_score']:.0f} RSI:{s['rsi']}")
    if signals.get("dip_buy"):
        signal_lines.append("\n💎【低吸博弈】")
        for d in signals["dip_buy"][:3]:
            signal_lines.append(f"  {d['theme']}: 趋势{d['trend_score']:.0f} 情绪{d['sentiment_score']:.0f}")
    theme_signals_text = "\n".join(signal_lines)

    return theme_analysis_text, theme_signals_text, results

# =========================================================
# DeepSeek日报
# =========================================================
def deepseek_report(result_df, style_df, risk_state, emotion_text, sector_text,
                    theme_analysis_text, theme_signals_text, portfolio_text="", new_positions=None, etf_pool=None):
    # ETF数据（包含规模和代码信息）
    if etf_pool is None:
        etf_pool = ETF_POOL
    
    # 构建板块->ETF映射说明
    etf_mapping_text = "\n【板块ETF代码对照表】\n"
    etf_mapping_text += f"{'板块':<12}{'ETF代码':<15}{'说明'}\n"
    etf_mapping_text += "-" * 50 + "\n"
    # 按行业列出ETF（从result_df中获取已分析的ETF）
    analyzed_etfs = result_df[['行业', 'ETF', '总评分']].head(10)
    for _, row in analyzed_etfs.iterrows():
        etf_mapping_text += f"{row['行业']:<12}{row['ETF']:<15}{'已分析'}\n"
    
    prompt = f"""
你是中国顶级ETF基金经理，每天给出延续性分析。
【大盘市场情绪(仓位核心参考)】
{emotion_text}

【主题趋势+情绪分析】（核心参考）
{theme_analysis_text}

【市场主题信号】
{theme_signals_text}

{etf_mapping_text}

ETF数据（评分TOP30）：
{analyzed_etfs.to_string(index=False)}

市场风格：
{style_df.to_string(index=False)}

当前持仓（注意下面的总仓位信息）：
{portfolio_text}

请输出：
1、大盘分析
2、市场主线(趋势和情绪双重分析,重点突出仓位建议)
3、持仓跟踪分析（持有/减仓/清仓/加仓）,不要用表格,用适合手机查看的格式。**重要：**根据上面的【总仓位】信息，当前持仓各ETF权重相加应该等于总持仓权重，现金=100-总持仓。
4、低吸方向
5、高潮方向（注意风险）
6、明日策略（**重要：每个策略必须包含对应ETF代码，格式：板块名称(ETF代码)，如：电力ETF(159611.SZ)）
7、仓位建议

**明日策略格式要求**：
- 每个操作必须标注ETF代码
- 格式：「板块名称(ETF代码)」，如：电力链(159611.SZ)、煤炭ETF(515220.SH)
- 不要只写板块名称，必须带上对应的ETF代码

格式：Markdown，微信推送友好,手机阅读友好,文字精简。
"""
    url = "https://api.deepseek.com/chat/completions"
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    try:
        response = requests.post(url, headers=headers, json={"model": "deepseek-chat", "messages": [{"role": "system", "content": "你是顶级A股ETF主线基金经理"}, {"role": "user", "content": prompt}], "temperature": 0.2}, timeout=120)
        return response.json()['choices'][0]['message']['content']
    except Exception as e:
        return str(e)

def save_report(content):
    report_file = os.path.join(REPORT_DIR, f"AI_ETF_Report_{TRADE_DATE}.md")
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(content)
    return report_file

def send_report(content):
    if not SERVERCHAN_KEY:
        return
    import re
    content = re.sub(r'<[^>]+>', '', content)
    try:
        requests.post(f"https://sctapi.ftqq.com/{SERVERCHAN_KEY}.send", data={"title": f"ETF日报{TRADE_DATE}", "desp": content}, timeout=30)
        print("推送成功")
    except Exception as e:
        print("推送失败:", e)

# =========================================================
# 主程序
# =========================================================
def main():
    print("=" * 60)
    print("AI主线ETF系统 v5.0（主题分析增强版）")
    print("=" * 60)
    
    init_portfolio_table()
    
    # 加载持仓
    portfolio_df = load_portfolio()
    print(f"\n[持仓] 当前持仓: {len(portfolio_df)} 只")
    
    # 检查待买入订单
    triggered_orders = check_and_trigger_orders(None)
    if triggered_orders:
        print(f"[触发买入] {len(triggered_orders)} 条")
        portfolio_df = load_portfolio()
    cleanup_expired_orders(days=5)
    
    # 获取主题分析
    print("=" * 60)
    theme_analysis_text, theme_signals_text, theme_results = get_theme_analysis()
    print(theme_analysis_text)
    print(theme_signals_text)
    
    # 指数
    index_df = get_index_data()
    index_df = calc_indicators(index_df)
    
    # 市场情绪 → 使用 market_analysis.py 的大盘分析
    import importlib
    ma = importlib.import_module("market_analysis")
    ma_results, ma_position, ma_reason, ma_style_allocations, ma_overview = ma.analyze_market()
    
    # 从数据库或重新计算获取趋势评分（用于显示）
    theme_csv = os.path.join(BASE_DIR, "cache_backbone_tushare", "theme_trend_sentiment.csv")
    theme_top3_scores = None
    if os.path.exists(theme_csv):
        try:
            df_theme = pd.read_csv(theme_csv, encoding='utf-8-sig')
            if not df_theme.empty and 'trend_score' in df_theme.columns:
                df_theme = df_theme.sort_values('rank').head(3)
                theme_top3_scores = df_theme['trend_score'].tolist()
        except:
            pass
    
    ts, it, tt = ma.calculate_market_trend_score(ma_results, theme_top3_scores)
    ms, pr, tp = ma.get_market_status_and_position(ts)
    
    # 构建 emotion_text 用于 DeepSeek 日报
    emotion_lines = ["【大盘分析】"]
    
    # 市场趋势总评分
    status_icon = "🚀" if "主升浪" in ms else ("📈" if "上升" in ms or "良好" in ms else ("⚠️" if "退潮" in ms or "主跌" in ms else "📊"))
    emotion_lines.append(f"  {status_icon} 市场状态: 【{ms}】")
    emotion_lines.append(f"  总趋势分: {ts:.1f} | 指数趋势: {it:.1f} | 主题趋势: {tt:.1f}")
    emotion_lines.append(f"  建议仓位: {ma_position}%")
    emotion_lines.append("")
    
    if ma_overview:
        ov = ma_overview
        emotion_lines.append(f"【市场概况】")
        emotion_lines.append(f"  上证{ov['sh_index']:.2f}({ov['sh_pct']:+.2f}%) "
                             f"成交{ov['total_amount']:.0f}亿 涨{ov['up_count']}跌{ov['down_count']} "
                             f"涨停{ov['zt_count']}跌停{ov['dt_count']}炸板率{ov['zb_rate']}%")
    emotion_lines.append("")
    emotion_lines.append(f"【各指数分析】")
    for r in ma_results:
        emotion_lines.append(f"  {r['name']}: 趋势{r['trend_score']:.1f}({r['trend_status']}) 情绪{r['sentiment_score']:.1f}({r['sentiment_status']}) 涨跌{r['pct_chg']:+.2f}%")
    emotion_lines.append(f"\n综合建议仓位: {ma_position}%")
    emotion_lines.append(f"理由: {ma_reason}")
    emotion_text = "\n".join(emotion_lines)
    print(emotion_text)

    # 情绪分（买入选股条件用）
    emotion_scores = [r['sentiment_score'] for r in ma_results]
    emotion_score = sum(emotion_scores) / len(emotion_scores) if emotion_scores else 50

    # 风险状态（用于 DeepSeek 日报）
    risk_state = "risk_on" if ma_position >= 50 else "risk_off"

    # 仓位百分比
    position_pct = ma_position
    print(f"市场状态: {risk_state}, 建议仓位: {position_pct}%")

    # 主题排名摘要（供 DeepSeek 日报引用）
    sector_df = pd.DataFrame({'name': [r['theme'] for r in theme_results], '评分': [r['composite_score'] for r in theme_results]})
    top_sector = sector_df.head(10)
    sector_text = top_sector.to_string(index=False)
    
    # ===== 主题->ETF映射：只分析前排主题相关的ETF =====
    top_theme_names = set()
    if theme_results:
        # 中期趋势TOP2 + 短线TOP3的去重集合
        for r in theme_results[:8]:  # 取前8名大致覆盖
            if r['trend_score'] >= 50:  # 趋势分不低于50
                top_theme_names.add(r['theme'])
    
    # 主题名到ETF_POOL行业名的映射（覆盖所有20个主题）
    THEME_TO_ETF_INDUSTRY = {
        'AI算力链': '算力', 'AI应用': '软件', 'AI终端': '消费电子',
        '半导体': '半导体', '人形机器人': '机器人', '华为鸿蒙': '软件',
        '智能驾驶': '智能驾驶', '低空经济': '军工', '商业航天': '军工',
        '核聚变': '电力', '信创软件': '软件', '金融科技': '金融科技',
        '券商': '证券', '电力链': '电力', '煤炭链': '煤炭',
        '有色资源': '有色金属', '消费': '消费', '保险': '银行',
        '银行': '银行',
    }
    
    # 收集需要分析的ETF行业名（去重）
    target_industries = set()
    for tn in top_theme_names:
        etf_ind = THEME_TO_ETF_INDUSTRY.get(tn)
        if etf_ind and etf_ind in ETF_POOL:
            target_industries.add(etf_ind)
    
    if not target_industries:
        # 兜底：取当日最强主题TOP5的映射结果
        for r in theme_results[:5]:
            etf_ind = THEME_TO_ETF_INDUSTRY.get(r['theme'])
            if etf_ind and etf_ind in ETF_POOL:
                target_industries.add(etf_ind)
    
    print(f"\n[聚焦] 前排主题关联ETF行业: {target_industries}")

    # ETF分析（只分析前排主题相关的ETF，不扩散）
    all_result = []
    # 按主题排名顺序遍历ETF_POOL
    prioritized_etfs = []
    # 先把匹配的放进去（按主题排名顺序）
    for r in theme_results:
        etf_ind = THEME_TO_ETF_INDUSTRY.get(r['theme'])
        if etf_ind and etf_ind in ETF_POOL and etf_ind not in [p[0] for p in prioritized_etfs]:
            prioritized_etfs.append((etf_ind, ETF_POOL[etf_ind]))
    # 再补充target_industries中漏掉的（兜底）
    for ind, code in ETF_POOL.items():
        if ind in target_industries and ind not in [p[0] for p in prioritized_etfs]:
            prioritized_etfs.append((ind, code))
    
    for industry, ts_code in prioritized_etfs:
        print(f"分析 {industry}({ts_code})")
        df = get_etf_data(ts_code)
        if df is None or len(df) < 60:
            continue
        df = calc_indicators(df)
        latest = df.iloc[-1]
        score, rs = etf_score(df, industry, index_df)
        stage, rise = wave_stage(df)
        signal = buy_signal(df)
        all_result.append({'行业': industry, 'ETF': ts_code, '收盘价': round(latest['close'], 2), '涨跌幅': round(latest['pct_chg'], 2),
                          'RS强度': rs, '波段阶段': stage, '信号': signal, '总评分': score})
    
    result_df = pd.DataFrame(all_result).sort_values('总评分', ascending=False)
    print(result_df)
    
    # 持仓更新
    update_portfolio_prices(result_df, ma_position)
    portfolio_df = load_portfolio()
    portfolio_text = analyze_portfolio(result_df, portfolio_df, ma_position)
    save_daily_snapshot(result_df, position_pct, emotion_score)
    # 保存每日持仓快照
    save_daily_holding_snapshot(portfolio_df, result_df, ma_position)
    
    # 卖出检查
    sell_actions = check_sell_signals(result_df, portfolio_df)
    if sell_actions:
        print(f"[卖出检查] {len(sell_actions)} 个")
        execute_sell_actions(sell_actions)
        portfolio_df = load_portfolio()
        portfolio_text = analyze_portfolio(result_df, portfolio_df, ma_position)
    
    # 新开仓（聚焦前排，最多3只）
    new_positions = []
    bought_count = 0
    for _, row in result_df.iterrows():
        if row['信号'] in ['主线启动', '第一次分歧低吸', '主升浪'] and portfolio_df[portfolio_df['ts_code'] == row['ETF']].empty:
            if row['行业'] in target_industries and row['总评分'] >= 60 and emotion_score > 50:
                save_pending_order(row['ETF'], row['行业'], row['信号'], round(row['收盘价'] * 1.01, 3), position_pct, row['总评分'])
                new_positions.append({'industry': row['行业'], 'ts_code': row['ETF'], 'signal': row['信号'], 'price': row['收盘价']})
                bought_count += 1
                if bought_count >= 3:
                    break
    
    # 市场风格
    style_df = market_style(result_df)
    print("\n市场风格:")
    print(style_df)
    
    # AI日报
    print("\nAI日报生成中...")
    report = deepseek_report(result_df, style_df, risk_state, emotion_text, sector_text,
                            theme_analysis_text, theme_signals_text, portfolio_text, new_positions)
    
    report_file = save_report(report)
    print("\n" + "=" * 60)
    print("AI日报")
    print("=" * 60)
    print(report)
    print(f"\n报告已保存: {report_file}")
    
    send_report(report)
    print("\n系统运行完成")

if __name__ == '__main__':
    main()
