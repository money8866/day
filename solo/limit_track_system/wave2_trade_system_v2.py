# -*- coding: utf-8 -*-
"""
涨停二波交易系统 V2
优化版：更高止盈、更严格筛选

与V1对比：
- V1: 止盈5%, 止损3%, 调整3-20天
- V2: 止盈8%, 止损2%, 调整5-15天, 增加乖离和连续小阳线过滤
"""

import os
import sys
import json
import pickle
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dotenv import load_dotenv
import warnings
warnings.filterwarnings('ignore')

# 添加d:\mystock到路径
MYSTOCK_DIR = r"d:\mystock"
if MYSTOCK_DIR not in sys.path:
    sys.path.append(MYSTOCK_DIR)

# 加载环境变量
DOTENV_PATH = os.path.join(MYSTOCK_DIR, "config", ".env")
load_dotenv(DOTENV_PATH)

import tushare as ts
TUSHARE_TOKEN = os.getenv('TUSHARE_TOKEN')
pro = ts.pro_api(TUSHARE_TOKEN)

# 路径配置
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache")
TRADE_DB = os.path.join(CACHE_DIR, "wave2_trades_v2.db")
os.makedirs(CACHE_DIR, exist_ok=True)


# ==================== V2 交易参数（优化版）====================
class TradeConfig:
    # 买入条件 - 更严格的调整天数
    MIN_ADJUST_DAYS = 5      # 最小调整天数（V1:3）
    MAX_ADJUST_DAYS = 15     # 最大调整天数（V1:20）
    
    # 揉搓线参数
    RUBBING_BODY_MAX = 0.025 # 揉搓线最大实体（2.5%，V1:3%）
    RUBBING_SHADOW_MIN = 0.01 # 揉搓线最小影线（1%）
    
    # 企稳信号 - V2新增连续小阳线要求
    STABILIZATION_SCORE_MIN = 60  # 企稳分数最小值（V1:50）
    MIN_CONSECUTIVE_POSITIVE = 2  # 最小连续小阳线数（V2新增）
    
    # 仓位管理
    MAX_POSITIONS = 3         # 最大持仓数
    POSITION_SIZE = 0.3       # 单只仓位比例（30%）
    
    # 止盈止损 - V2更高盈亏比
    PROFIT_TARGET = 0.08      # 盈利目标（8%，V1:5%）
    STOP_LOSS = 0.02         # 止损线（2%，V1:3%）
    TRAILING_STOP = 0.03      # 追踪止盈（3%，V1:2%）
    
    # 其他过滤 - V2新增乖离过滤
    MIN_MARKET_CAP = 40       # 最小市值（亿）
    MIN_PRICE = 5             # 最低股价
    MAX_MA5_DEVIATION = 0.10 # 最大5日线乖离（10%，V2新增）


# ==================== 数据库初始化 ====================
def init_trade_db():
    """初始化交易数据库"""
    conn = sqlite3.connect(TRADE_DB)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_code TEXT NOT NULL,
            name TEXT,
            buy_date TEXT,
            buy_price REAL,
            shares INTEGER,
            amount REAL,
            stop_loss_price REAL,
            target_price REAL,
            status TEXT DEFAULT 'holding',
            create_time TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_code TEXT NOT NULL,
            name TEXT,
            action TEXT NOT NULL,
            price REAL,
            shares INTEGER,
            amount REAL,
            profit REAL,
            profit_rate REAL,
            holding_days INTEGER,
            reason TEXT,
            trade_date TEXT,
            create_time TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_code TEXT NOT NULL,
            name TEXT,
            signal_date TEXT,
            signal_type TEXT,
            signal_score REAL,
            price REAL,
            industry TEXT,
            market_cap REAL,
            create_time TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(trade_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_signals_date ON signals(signal_date)')
    
    conn.commit()
    conn.close()


# ==================== V2 K线形态识别（更严格）====================
def is_rubbing_line_v2(k1, k2):
    """
    V2版揉搓线识别 - 更严格条件
    """
    try:
        body1 = abs(k1['close'] - k1['open']) / k1['open']
        shadow1_upper = (k1['high'] - max(k1['open'], k1['close'])) / k1['open']
        shadow1_lower = (min(k1['open'], k1['close']) - k1['low']) / k1['open']
        
        body2 = abs(k2['close'] - k2['open']) / k2['open']
        shadow2_upper = (k2['high'] - max(k2['open'], k2['close'])) / k2['open']
        shadow2_lower = (min(k2['open'], k2['close']) - k2['low']) / k2['open']
        
        # 条件1：两根都是小实体（更严格2.5%）
        cond1 = body1 <= TradeConfig.RUBBING_BODY_MAX
        cond2 = body2 <= TradeConfig.RUBBING_BODY_MAX
        
        # 条件3：两根都有下影线
        cond3 = shadow1_lower > 0.008 and shadow2_lower > 0.008
        
        # 条件4：第二根影线更长
        cond4 = (shadow2_upper + shadow2_lower) > (shadow1_upper + shadow1_lower)
        
        # 条件5：两根K线实体部分有重叠
        max_oc1 = max(k1['open'], k1['close'])
        min_oc1 = min(k1['open'], k1['close'])
        max_oc2 = max(k2['open'], k2['close'])
        min_oc2 = min(k2['open'], k2['close'])
        cond5 = min(max_oc1, max_oc2) > max(min_oc1, min_oc2)
        
        # V2: 至少4个条件满足（V1:3个）
        score = sum([cond1, cond2, cond3, cond4, cond5])
        
        return score >= 4
        
    except Exception as e:
        return False


def is_stabilization_kline_v2(kline, prev_klines):
    """
    V2版企稳信号 - 增加连续小阳线要求
    """
    try:
        open_p = kline['open']
        close_p = kline['close']
        high_p = kline['high']
        low_p = kline['low']
        
        body_size = abs(close_p - open_p) / open_p
        lower_shadow = (min(open_p, close_p) - low_p) / open_p
        
        # 1. 小阳线且下影线较长
        cond1 = close_p > open_p and body_size < 0.025 and lower_shadow > 0.012
        
        # 2. 缩量（更严格0.7倍）
        if len(prev_klines) >= 5:
            avg_vol = np.mean([k['vol'] for k in prev_klines[-5:]])
            cond2 = kline['vol'] < avg_vol * 0.7
        else:
            cond2 = True
        
        # 3. 价格在合理区间
        cond3 = body_size < lower_shadow
        
        return cond1 and cond2 and cond3
        
    except Exception as e:
        return False


def check_consecutive_positive(daily_df, zt_date, zt_close):
    """
    V2新增：检查连续小阳线
    """
    try:
        if daily_df is None or daily_df.empty:
            return 0
        
        daily_df = daily_df.sort_values('trade_date')
        
        # 找到涨停日之后的数据
        zt_idx = daily_df[daily_df['trade_date'] == zt_date].index
        if len(zt_idx) == 0:
            return 0
        
        zt_idx = zt_idx[0]
        after_zt = daily_df.loc[zt_idx:]
        
        # 统计连续小阳线
        consecutive = 0
        for _, row in after_zt.iterrows():
            if row['close'] > row['open'] and (row['close'] - row['open']) / row['open'] < 0.03:
                consecutive += 1
            else:
                break
        
        return consecutive
        
    except:
        return 0


def calculate_ma5_deviation_v2(daily_df):
    """
    V2新增：计算5日线乖离
    """
    try:
        if daily_df is None or daily_df.empty or len(daily_df) < 10:
            return 0
        
        daily_df = daily_df.sort_values('trade_date').tail(15).copy()
        daily_df['ma5'] = daily_df['close'].rolling(window=5).mean()
        
        latest_close = daily_df['close'].iloc[-1]
        latest_ma5 = daily_df['ma5'].iloc[-1]
        
        if latest_ma5 > 0:
            return abs(latest_close - latest_ma5) / latest_ma5
        return 0
        
    except:
        return 0


def analyze_rubbing_stabilization_v2(daily_df, zt_history):
    """
    V2版揉搓线企稳信号分析
    """
    try:
        if daily_df is None or daily_df.empty or len(daily_df) < 20:
            return False, "数据不足", 0
        
        if not zt_history:
            return False, "无涨停历史", 0
        
        daily_df = daily_df.sort_values('trade_date').tail(15).copy()
        klines = daily_df.to_dict('records')
        
        # 计算调整天数
        last_zt_date = zt_history[0]['date']
        zt_close = zt_history[0].get('close', 0)
        
        try:
            zt_dt = datetime.strptime(last_zt_date, '%Y%m%d')
            today_dt = datetime.now()
            adjust_days = (today_dt - zt_dt).days
        except:
            adjust_days = 0
        
        # 检查调整天数范围（V2: 5-15天）
        if not (TradeConfig.MIN_ADJUST_DAYS <= adjust_days <= TradeConfig.MAX_ADJUST_DAYS):
            return False, f"调整{adjust_days}天不在范围", 0
        
        # V2新增：乖离过滤
        ma5_deviation = calculate_ma5_deviation_v2(daily_df)
        if ma5_deviation > TradeConfig.MAX_MA5_DEVIATION:
            return False, f"乖离过大{ma5_deviation*100:.1f}%", 0
        
        # V2新增：连续小阳线检查
        consecutive_pos = check_consecutive_positive(daily_df, last_zt_date, zt_close)
        if consecutive_pos < TradeConfig.MIN_CONSECUTIVE_POSITIVE:
            return False, f"连续小阳线{consecutive_pos}个不足", 0
        
        # 检查揉搓线形态（V2更严格）
        for i in range(len(klines) - 3):
            k1 = klines[-(i+4)]
            k2 = klines[-(i+3)]
            
            if is_rubbing_line_v2(k1, k2):
                if i + 2 < len(klines):
                    next_kline = klines[-(i+2)]
                    prev_klines = klines[:-(i+2)]
                    
                    if is_stabilization_kline_v2(next_kline, prev_klines):
                        score = 65
                        
                        # 调整天数加分（V2: 7-12天最佳）
                        if 7 <= adjust_days <= 12:
                            score += 25
                        elif 5 <= adjust_days < 7 or 12 < adjust_days <= 15:
                            score += 15
                        
                        # 乖离加分
                        if ma5_deviation < 0.05:
                            score += 10
                        
                        # 连续小阳线加分
                        if consecutive_pos >= 3:
                            score += 15
                        elif consecutive_pos >= 2:
                            score += 10
                        
                        return True, f"揉搓线+企稳+连续小阳{consecutive_pos}(调整{adjust_days}天)", min(score, 95)
        
        return False, f"无信号(连续{consecutive_pos}小阳)", 0
        
    except Exception as e:
        return False, f"分析失败:{str(e)}", 0


# ==================== V2 交易执行 ====================
def filter_stocks_by_trade_signal_v2(trade_date, lookback_days=20):
    """V2版股票筛选"""
    try:
        print(f"\n{'='*60}")
        print(f"🔍 V2筛选交易信号股票")
        print(f"{'='*60}")
        
        signals = []
        
        start_date = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=lookback_days)).strftime('%Y%m%d')
        
        db_path = os.path.join(BASE_DIR, "cache", "limit_history.db")
        conn = sqlite3.connect(db_path)
        
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(limit_stocks)")
        columns = [col[1] for col in cursor.fetchall()]
        
        if 'close_price' in columns:
            price_col = 'close_price'
        else:
            price_col = 'close_price'
        
        query = f'''
            SELECT ts_code, trade_date, {price_col}, name, industry 
            FROM limit_stocks 
            WHERE trade_date BETWEEN ? AND ?
            GROUP BY ts_code
            HAVING MIN(trade_date)
        '''
        
        for row in conn.execute(query, (start_date, trade_date)):
            ts_code = row[0]
            zt_date = row[1]
            zt_close = row[2]
            name = row[3]
            industry = row[4]
            
            try:
                if zt_close < TradeConfig.MIN_PRICE:
                    continue
                
                start = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=30)).strftime('%Y%m%d')
                # V2: 优先 daily_cache 表
                daily_df = None
                try:
                    from stock_cache import get_daily_cache, get_daily_cache_range, batch_insert_daily_cache
                    _, _max_date = get_daily_cache_range(ts_code)
                    if _max_date is not None and str(_max_date) >= str(trade_date):
                        daily_df = get_daily_cache(ts_code, start, trade_date)
                        if daily_df is not None and not daily_df.empty:
                            daily_df['trade_date'] = daily_df['trade_date'].astype(str)
                except Exception:
                    pass
                if daily_df is None or daily_df.empty:
                    daily_df = pro.daily(ts_code=ts_code, start_date=start, end_date=trade_date)
                    if daily_df is not None and not daily_df.empty:
                        try:
                            from stock_cache import batch_insert_daily_cache
                            batch_insert_daily_cache(daily_df)
                        except Exception:
                            pass
                
                if daily_df is None or daily_df.empty:
                    continue
                
                daily_df = daily_df.sort_values('trade_date')
                
                zt_history = [{'date': zt_date, 'close': zt_close}]
                is_signal, signal_desc, signal_score = analyze_rubbing_stabilization_v2(daily_df, zt_history)
                
                if is_signal and signal_score >= TradeConfig.STABILIZATION_SCORE_MIN:
                    latest_price = daily_df['close'].iloc[-1]
                    
                    signals.append({
                        'ts_code': ts_code,
                        'name': name,
                        'industry': industry,
                        'zt_date': zt_date,
                        'zt_price': zt_close,
                        'current_price': latest_price,
                        'signal_type': '揉搓线企稳V2',
                        'signal_desc': signal_desc,
                        'signal_score': signal_score,
                        'profit_potential': (latest_price - zt_close) / zt_close * 100
                    })
                    
                    print(f"✓ V2信号: {name} | {signal_desc} | 分数:{signal_score}")
            
            except Exception as e:
                continue
        
        conn.close()
        
        signals.sort(key=lambda x: x['signal_score'], reverse=True)
        
        print(f"\n📊 V2信号筛选完成: 共{len(signals)}个信号")
        
        return signals
        
    except Exception as e:
        print(f"❌ V2筛选失败: {e}")
        return []


def execute_buy_v2(ts_code, name, price, trade_date, signal_score):
    """V2版买入"""
    try:
        positions = get_holding_positions_v2()
        if len(positions) >= TradeConfig.MAX_POSITIONS:
            return False, "持仓已满"
        
        for pos in positions:
            if pos['ts_code'] == ts_code:
                return False, "已在持仓中"
        
        shares = int(TradeConfig.POSITION_SIZE * 100000 / price / 100) * 100
        if shares < 100:
            return False, "资金不足"
        
        amount = shares * price
        
        stop_loss_price = price * (1 - TradeConfig.STOP_LOSS)
        target_price = price * (1 + TradeConfig.PROFIT_TARGET)
        
        conn = sqlite3.connect(TRADE_DB)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO positions 
            (ts_code, name, buy_date, buy_price, shares, amount, stop_loss_price, target_price, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'holding')
        ''', (ts_code, name, trade_date, price, shares, amount, stop_loss_price, target_price))
        
        cursor.execute('''
            INSERT INTO trades 
            (ts_code, name, action, price, shares, amount, trade_date, reason)
            VALUES (?, ?, '买入', ?, ?, ?, ?, ?)
        ''', (ts_code, name, price, shares, amount, trade_date, "揉搓线企稳V2买入"))
        
        conn.commit()
        conn.close()
        
        return True, {
            'ts_code': ts_code,
            'name': name,
            'buy_price': price,
            'shares': shares,
            'amount': amount,
            'stop_loss': stop_loss_price,
            'target': target_price
        }
        
    except Exception as e:
        return False, str(e)


def execute_sell_v2(position_id, ts_code, name, price, reason, trade_date):
    """V2版卖出"""
    try:
        conn = sqlite3.connect(TRADE_DB)
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM positions WHERE id=? AND status="holding"', (position_id,))
        pos = cursor.fetchone()
        
        if not pos:
            return False, "无持仓"
        
        buy_price = pos[3]
        shares = pos[4]
        buy_amount = pos[5]
        
        sell_amount = shares * price
        profit = sell_amount - buy_amount
        profit_rate = profit / buy_amount * 100
        
        buy_date = datetime.strptime(pos[3], '%Y%m%d')
        sell_date = datetime.strptime(trade_date, '%Y%m%d')
        holding_days = (sell_date - buy_date).days
        
        cursor.execute('UPDATE positions SET status="sold" WHERE id=?', (position_id,))
        
        cursor.execute('''
            INSERT INTO trades 
            (ts_code, name, action, price, shares, amount, profit, profit_rate, holding_days, trade_date, reason)
            VALUES (?, ?, '卖出', ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (ts_code, name, price, shares, sell_amount, profit, profit_rate, holding_days, trade_date, reason))
        
        conn.commit()
        conn.close()
        
        return True, {
            'profit': profit,
            'profit_rate': profit_rate,
            'holding_days': holding_days
        }
        
    except Exception as e:
        return False, str(e)


def get_holding_positions_v2():
    """V2获取持仓"""
    try:
        conn = sqlite3.connect(TRADE_DB)
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM positions WHERE status="holding"')
        cols = [desc[0] for desc in cursor.description]
        positions = [dict(zip(cols, row)) for row in cursor.fetchall()]
        
        conn.close()
        return positions
        
    except Exception as e:
        print(f"获取持仓失败: {e}")
        return []


def check_and_execute_trades_v2(trade_date):
    """V2检查并执行交易"""
    try:
        positions = get_holding_positions_v2()
        
        if not positions:
            return
        
        current_prices = {}
        for pos in positions:
            ts_code = pos['ts_code']
            try:
                # V2: 优先 daily_cache 表
                df = None
                try:
                    from stock_cache import get_daily_cache, batch_insert_daily_cache
                    df = get_daily_cache(ts_code, trade_date, trade_date)
                    if df is not None and not df.empty:
                        df['trade_date'] = df['trade_date'].astype(str)
                except Exception:
                    pass
                if df is None or df.empty:
                    df = pro.daily(ts_code=ts_code, trade_date=trade_date)
                    if df is not None and not df.empty:
                        try:
                            from stock_cache import batch_insert_daily_cache
                            batch_insert_daily_cache(df)
                        except Exception:
                            pass
                if df is not None and not df.empty:
                    current_prices[ts_code] = df['close'].iloc[-1]
            except:
                pass
        
        for pos in positions:
            ts_code = pos['ts_code']
            name = pos['name']
            buy_price = pos['buy_price']
            stop_loss = pos['stop_loss_price']
            target = pos['target_price']
            
            current_price = current_prices.get(ts_code)
            
            if not current_price:
                print(f"⚠️ 无法获取 {name} 当前价格")
                continue
            
            profit_rate = (current_price - buy_price) / buy_price * 100
            
            print(f"\n📊 {name}: 买入{buy_price:.2f} | 当前{current_price:.2f} | 盈亏{profit_rate:.2f}%")
            
            # V2止盈（8%）
            if current_price >= target:
                success, result = execute_sell_v2(pos['id'], ts_code, name, current_price, "V2止盈8%", trade_date)
                if success:
                    print(f"✅ V2止盈卖出: {name} | 盈利{result['profit_rate']:.2f}%")
                continue
            
            # V2止损（2%）
            if current_price <= stop_loss:
                success, result = execute_sell_v2(pos['id'], ts_code, name, current_price, "V2止损2%", trade_date)
                if success:
                    print(f"❌ V2止损卖出: {name} | 亏损{result['profit_rate']:.2f}%")
                continue
            
            # V2追踪止盈（回撤3%）
            if profit_rate > 8:
                peak_profit = pos.get('peak_profit', profit_rate)
                if profit_rate < peak_profit - 3:
                    success, result = execute_sell_v2(pos['id'], ts_code, name, current_price, "V2追踪止盈", trade_date)
                    if success:
                        print(f"✅ V2追踪止盈: {name} | 盈利{result['profit_rate']:.2f}%")
        
    except Exception as e:
        print(f"检查持仓失败: {e}")


def daily_trade_v2(trade_date):
    """V2每日交易"""
    try:
        print(f"\n{'='*70}")
        print(f"📈 V2每日交易 | {trade_date}")
        print(f"{'='*70}")
        
        init_trade_db()
        
        print(f"\n[1/3] 检查持仓...")
        check_and_execute_trades_v2(trade_date)
        
        print(f"\n[2/3] V2筛选买入信号...")
        signals = filter_stocks_by_trade_signal_v2(trade_date)
        
        if not signals:
            print("⚠️ 无V2买入信号")
            return
        
        print(f"\n[3/3] V2执行买入...")
        positions = get_holding_positions_v2()
        
        for signal in signals[:TradeConfig.MAX_POSITIONS - len(positions)]:
            ts_code = signal['ts_code']
            name = signal['name']
            price = signal['current_price']
            
            positions = get_holding_positions_v2()
            if len(positions) >= TradeConfig.MAX_POSITIONS:
                break
            
            holding_codes = [p['ts_code'] for p in positions]
            if ts_code in holding_codes:
                continue
            
            success, result = execute_buy_v2(ts_code, name, price, trade_date, signal['signal_score'])
            
            if success:
                print(f"✅ V2买入成功: {name} @ {price:.2f}")
            else:
                print(f"⚠️ V2买入失败: {name} - {result}")
        
        print_trade_stats_v2()
        
    except Exception as e:
        print(f"❌ V2交易失败: {e}")


def print_trade_stats_v2():
    """V2打印交易统计"""
    try:
        conn = sqlite3.connect(TRADE_DB)
        
        df = pd.read_sql('''
            SELECT action, COUNT(*) as count, SUM(amount) as total_amount, 
                   SUM(profit) as total_profit, AVG(profit_rate) as avg_rate
            FROM trades
            GROUP BY action
        ''', conn)
        
        if not df.empty:
            print(f"\n📊 V2交易统计")
            for _, row in df.iterrows():
                print(f"  {row['action']}: {row['count']}笔 | 总额{row['total_amount']:.2f}万")
                if row['action'] == '卖出' and row['total_profit'] != 0:
                    print(f"    盈亏: {row['total_profit']:.2f}元 | 平均{row['avg_rate']:.2f}%")
        
        sell_df = pd.read_sql('''
            SELECT COUNT(*) as total, 
                   SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END) as wins
            FROM trades
            WHERE action='卖出'
        ''', conn)
        
        if not sell_df.empty:
            total = sell_df['total'].iloc[0]
            wins = sell_df['wins'].iloc[0]
            if total > 0:
                win_rate = wins / total * 100
                print(f"  V2胜率: {win_rate:.1f}% ({wins}/{total})")
        
        conn.close()
        
    except Exception as e:
        print(f"V2统计失败: {e}")


# ==================== V2 回测 ====================
def backtest_strategy_v2(start_date, end_date, lookback_days=20):
    """V2策略回测"""
    try:
        print(f"\n{'='*70}")
        print(f"📈 V2策略回测 | {start_date} ~ {end_date}")
        print(f"{'='*70}")
        
        init_trade_db()
        
        cal = pro.trade_cal(start_date=start_date, end_date=end_date)
        trading_days = cal[cal['is_open'] == 1]['cal_date'].tolist()
        
        print(f"V2回测交易日: {len(trading_days)}天")
        
        total_profit = 0
        total_trades = 0
        win_trades = 0
        
        for trade_date in trading_days:
            try:
                zt_df = pro.limit_list_ths(trade_date=trade_date, limit_type='涨停池')
                
                if zt_df is None or zt_df.empty:
                    continue
                
                for _, row in zt_df.iterrows():
                    ts_code = row.get('ts_code')
                    zt_close = row.get('close', 0)
                    
                    name = row.get('name', '')
                    if 'ST' in name.upper():
                        continue
                    
                    future_start = (datetime.strptime(trade_date, '%Y%m%d') + timedelta(days=1)).strftime('%Y%m%d')
                    future_end = (datetime.strptime(trade_date, '%Y%m%d') + timedelta(days=18)).strftime('%Y%m%d')
                    
                    try:
                        # V2: 优先 daily_cache 表
                        daily_df = None
                        try:
                            from stock_cache import get_daily_cache, get_daily_cache_range, batch_insert_daily_cache
                            _, _max_date = get_daily_cache_range(ts_code)
                            if _max_date is not None and str(_max_date) >= str(future_end):
                                daily_df = get_daily_cache(ts_code, future_start, future_end)
                                if daily_df is not None and not daily_df.empty:
                                    daily_df['trade_date'] = daily_df['trade_date'].astype(str)
                        except Exception:
                            pass
                        if daily_df is None or daily_df.empty:
                            daily_df = pro.daily(ts_code=ts_code, start_date=future_start, end_date=future_end)
                            if daily_df is not None and not daily_df.empty:
                                try:
                                    from stock_cache import batch_insert_daily_cache
                                    batch_insert_daily_cache(daily_df)
                                except Exception:
                                    pass
                        
                        if daily_df is None or daily_df.empty or len(daily_df) < 5:
                            continue
                        
                        daily_df = daily_df.sort_values('trade_date')
                        
                        klines = daily_df.head(7).to_dict('records')
                        
                        # V2更严格的揉搓线
                        for i in range(len(klines) - 3):
                            k1 = klines[i]
                            k2 = klines[i+1]
                            
                            if is_rubbing_line_v2(k1, k2):
                                buy_price = k2['close']
                                
                                # V2止盈止损
                                target_price = buy_price * 1.08
                                stop_price = buy_price * 0.98
                                
                                for j in range(i+2, len(klines)):
                                    sell_price = klines[j]['close']
                                    profit_rate = (sell_price - buy_price) / buy_price
                                    
                                    if sell_price >= target_price:
                                        total_profit += profit_rate
                                        total_trades += 1
                                        win_trades += 1
                                        print(f"✓ V2止盈: {name} {profit_rate*100:.2f}%")
                                        break
                                    elif sell_price <= stop_price:
                                        total_profit += profit_rate
                                        total_trades += 1
                                        print(f"✗ V2止损: {name} {profit_rate*100:.2f}%")
                                        break
                                
                                break
                    
                    except Exception as e:
                        continue
            
            except Exception as e:
                continue
        
        print(f"\n📊 V2回测结果")
        print(f"  V2总交易: {total_trades}笔")
        print(f"  V2胜率: {win_trades/total_trades*100:.1f}%")
        print(f"  V2总收益率: {total_profit*100:.2f}%")
        if total_trades > 0:
            print(f"  V2平均收益: {total_profit/total_trades*100:.2f}%")
        
    except Exception as e:
        print(f"❌ V2回测失败: {e}")


# ==================== V2 主程序 ====================
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='涨停二波交易系统 V2')
    parser.add_argument('mode', choices=['trade', 'backtest', 'stats'],
                       help='模式: trade=每日交易, backtest=回测, stats=统计')
    parser.add_argument('--date', '-d', help='交易日期 (YYYYMMDD)')
    parser.add_argument('--start', help='回测开始日期 (YYYYMMDD)')
    parser.add_argument('--end', help='回测结束日期 (YYYYMMDD)')
    
    args = parser.parse_args()
    
    if args.mode == 'trade':
        if args.date:
            trade_date = args.date
        else:
            now = datetime.now()
            if now.hour < 15:
                query_date = (now - timedelta(days=1)).strftime('%Y%m%d')
            else:
                query_date = now.strftime('%Y%m%d')
            
            cal = pro.trade_cal(end_date=query_date)
            trade_date = cal[cal['is_open'] == 1]['cal_date'].iloc[-1]
        
        daily_trade_v2(trade_date)
    
    elif args.mode == 'backtest':
        start = args.start or (datetime.now() - timedelta(days=60)).strftime('%Y%m%d')
        end = args.end or (datetime.now() - timedelta(days=1)).strftime('%Y%m%d')
        backtest_strategy_v2(start, end)
    
    elif args.mode == 'stats':
        print_trade_stats_v2()
