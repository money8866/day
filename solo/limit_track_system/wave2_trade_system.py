# -*- coding: utf-8 -*-
"""
涨停二波交易系统
策略：揉搓线企稳买入，5%以上卖出
目标：首板后抓二次启动主升浪

核心逻辑：
1. 涨停后调整等待
2. 出现揉搓线企稳信号买入
3. 5%以上卖出
4. 止损3%
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
TRADE_DB = os.path.join(CACHE_DIR, "wave2_trades.db")
os.makedirs(CACHE_DIR, exist_ok=True)


# ==================== 交易参数 ====================
class TradeConfig:
    # 买入条件
    MIN_ADJUST_DAYS = 3      # 最小调整天数
    MAX_ADJUST_DAYS = 20     # 最大调整天数
    
    # 揉搓线参数
    RUBBING_BODY_MAX = 0.03  # 揉搓线最大实体（3%）
    RUBBING_SHADOW_MIN = 0.01  # 揉搓线最小影线（1%）
    
    # 企稳信号
    STABILIZATION_SCORE_MIN = 50  # 企稳分数最小值
    
    # 仓位管理
    MAX_POSITIONS = 3         # 最大持仓数
    POSITION_SIZE = 0.3       # 单只仓位比例（30%）
    
    # 止盈止损
    PROFIT_TARGET = 0.05      # 盈利目标（5%）
    STOP_LOSS = 0.03          # 止损线（3%）
    TRAILING_STOP = 0.02      # 追踪止盈（2%）
    
    # 其他过滤
    MIN_MARKET_CAP = 40      # 最小市值（亿）
    MIN_PRICE = 5            # 最低股价


# ==================== 数据库初始化 ====================
def init_trade_db():
    """初始化交易数据库"""
    conn = sqlite3.connect(TRADE_DB)
    cursor = conn.cursor()
    
    # 持仓记录
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
    
    # 交易记录
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
    
    # 每日信号记录
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
    
    # 创建索引
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(trade_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_signals_date ON signals(signal_date)')
    
    conn.commit()
    conn.close()


# ==================== K线形态识别 ====================
def is_rubbing_line(k1, k2):
    """
    判断是否形成揉搓线形态
    
    揉搓线特征：
    1. 两根K线，一根带上下影线的小实体
    2. 第二天实体更小，影线更长
    3. 通常出现在调整末期
    """
    try:
        # 计算K线特征
        body1 = abs(k1['close'] - k1['open']) / k1['open']
        shadow1_upper = (k1['high'] - max(k1['open'], k1['close'])) / k1['open']
        shadow1_lower = (min(k1['open'], k1['close']) - k1['low']) / k1['open']
        
        body2 = abs(k2['close'] - k2['open']) / k2['open']
        shadow2_upper = (k2['high'] - max(k2['open'], k2['close'])) / k2['open']
        shadow2_lower = (min(k2['open'], k2['close']) - k2['low']) / k2['open']
        
        # 条件1：第一根是小实体
        cond1 = body1 <= TradeConfig.RUBBING_BODY_MAX
        
        # 条件2：第二根实体更小或相近
        cond2 = body2 <= body1 * 1.3
        
        # 条件3：两根都有下影线
        cond3 = shadow1_lower > 0.005 and shadow2_lower > 0.005
        
        # 条件4：第二根影线更长（洗盘特征）
        cond4 = (shadow2_upper + shadow2_lower) > (shadow1_upper + shadow1_lower) * 0.8
        
        # 条件5：两根K线实体部分有重叠
        max_oc1 = max(k1['open'], k1['close'])
        min_oc1 = min(k1['open'], k1['close'])
        max_oc2 = max(k2['open'], k2['close'])
        min_oc2 = min(k2['open'], k2['close'])
        cond5 = min(max_oc1, max_oc2) > max(min_oc1, min_oc2)
        
        # 至少3个条件满足
        score = sum([cond1, cond2, cond3, cond4, cond5])
        
        return score >= 3
        
    except Exception as e:
        return False


def is_stabilization_kline(kline, prev_klines):
    """
    判断是否企稳信号
    """
    try:
        open_p = kline['open']
        close_p = kline['close']
        high_p = kline['high']
        low_p = kline['low']
        
        body_size = abs(close_p - open_p) / open_p
        lower_shadow = (min(open_p, close_p) - low_p) / open_p
        
        # 1. 小阳线且下影线较长（企稳下影线）
        cond1 = close_p > open_p and body_size < 0.03 and lower_shadow > 0.015
        
        # 2. 缩量
        if len(prev_klines) >= 5:
            avg_vol = np.mean([k['vol'] for k in prev_klines[-5:]])
            cond2 = kline['vol'] < avg_vol * 0.8
        else:
            cond2 = True
        
        # 3. 价格在合理区间
        cond3 = body_size < lower_shadow
        
        return cond1 and cond2 and cond3
        
    except Exception as e:
        return False


def analyze_rubbing_stabilization(daily_df, zt_history):
    """
    分析揉搓线企稳信号
    
    返回：(是否信号, 信号描述, 信号分数)
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
        
        # 检查调整天数范围
        if not (TradeConfig.MIN_ADJUST_DAYS <= adjust_days <= TradeConfig.MAX_ADJUST_DAYS):
            return False, f"调整{adjust_days}天不在范围", 0
        
        # 检查揉搓线形态
        rubbing_found = False
        for i in range(len(klines) - 2):
            k1 = klines[-(i+3)]
            k2 = klines[-(i+2)]
            
            if is_rubbing_line(k1, k2):
                # 检查揉搓线后是否有企稳K线
                if i + 1 < len(klines):
                    next_kline = klines[-(i+1)]
                    prev_klines = klines[:-(i+1)]
                    
                    if is_stabilization_kline(next_kline, prev_klines):
                        rubbing_found = True
                        
                        # 计算信号分数
                        score = 60
                        
                        # 调整天数加分
                        if 5 <= adjust_days <= 10:
                            score += 20
                        elif 3 <= adjust_days < 5 or 10 < adjust_days <= 15:
                            score += 10
                        
                        # 揉搓线后企稳加分
                        score += 15
                        
                        return True, f"揉搓线+企稳K线(调整{adjust_days}天)", min(score, 95)
        
        # 检查简单的企稳信号
        latest_kline = klines[-1]
        if is_stabilization_kline(latest_kline, klines[:-1]):
            score = 40
            if 5 <= adjust_days <= 10:
                score += 15
            
            return score >= TradeConfig.STABILIZATION_SCORE_MIN, f"企稳K线(调整{adjust_days}天)", score
        
        return False, f"无信号(调整{adjust_days}天)", 0
        
    except Exception as e:
        return False, f"分析失败:{str(e)}", 0


# ==================== 股票筛选 ====================
def get_stock_basic_info(ts_code):
    """获取股票基本信息"""
    try:
        df = pro.stock_basic(ts_code=ts_code)
        if df is not None and not df.empty:
            return df.iloc[0].to_dict()
        return None
    except:
        return None


def filter_stocks_by_trade_signal(trade_date, lookback_days=20):
    """
    筛选符合交易信号的股票
    
    筛选条件：
    1. 近20天内有涨停
    2. 涨停后调整3-20天
    3. 出现揉搓线企稳信号
    4. 市值>40亿
    5. 股价>5元
    """
    try:
        print(f"\n{'='*60}")
        print(f"🔍 筛选交易信号股票")
        print(f"{'='*60}")
        
        signals = []
        
        # 获取近N天有涨停的股票
        start_date = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=lookback_days)).strftime('%Y%m%d')
        
        db_path = os.path.join(BASE_DIR, "cache", "limit_history.db")
        conn = sqlite3.connect(db_path)
        
        # 检查表结构并选择正确的字段名
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(limit_stocks)")
        columns = [col[1] for col in cursor.fetchall()]
        
        # 选择存在的字段
        if 'close_price' in columns:
            price_col = 'close_price'
        else:
            price_col = 'close_price'  # 默认使用close_price
        
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
                # 获取基本信息
                basic_info = get_stock_basic_info(ts_code)
                if not basic_info:
                    continue
                
                # 市值过滤
                market_cap = basic_info.get('market_cap', 0)
                if market_cap > 0 and market_cap < TradeConfig.MIN_MARKET_CAP:
                    continue
                
                # 股价过滤
                if zt_close < TradeConfig.MIN_PRICE:
                    continue
                
                # 获取日线数据
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
                
                # 分析揉搓线企稳信号
                zt_history = [{'date': zt_date, 'close': zt_close}]
                is_signal, signal_desc, signal_score = analyze_rubbing_stabilization(daily_df, zt_history)
                
                if is_signal and signal_score >= TradeConfig.STABILIZATION_SCORE_MIN:
                    latest_price = daily_df['close'].iloc[-1]
                    
                    signals.append({
                        'ts_code': ts_code,
                        'name': name,
                        'industry': industry,
                        'market_cap': market_cap,
                        'zt_date': zt_date,
                        'zt_price': zt_close,
                        'current_price': latest_price,
                        'signal_type': '揉搓线企稳',
                        'signal_desc': signal_desc,
                        'signal_score': signal_score,
                        'profit_potential': (latest_price - zt_close) / zt_close * 100
                    })
                    
                    print(f"✓ 信号: {name} {ts_code} | {signal_desc} | 分数:{signal_score}")
            
            except Exception as e:
                continue
        
        conn.close()
        
        # 按信号分数排序
        signals.sort(key=lambda x: x['signal_score'], reverse=True)
        
        print(f"\n📊 信号筛选完成: 共{len(signals)}个信号")
        
        return signals
        
    except Exception as e:
        print(f"❌ 筛选失败: {e}")
        return []


# ==================== 交易执行 ====================
def execute_buy(ts_code, name, price, trade_date, signal_score):
    """
    执行买入
    
    返回：(是否成功, 成交信息)
    """
    try:
        # 检查持仓是否已满
        positions = get_holding_positions()
        if len(positions) >= TradeConfig.MAX_POSITIONS:
            return False, "持仓已满"
        
        # 检查是否已持仓
        for pos in positions:
            if pos['ts_code'] == ts_code:
                return False, "已在持仓中"
        
        # 计算买入数量（向下取整100股）
        shares = int(TradeConfig.POSITION_SIZE * 100000 / price / 100) * 100
        if shares < 100:
            return False, "资金不足"
        
        amount = shares * price
        
        # 计算止损价和目标价
        stop_loss_price = price * (1 - TradeConfig.STOP_LOSS)
        target_price = price * (1 + TradeConfig.PROFIT_TARGET)
        
        # 保存持仓
        conn = sqlite3.connect(TRADE_DB)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO positions 
            (ts_code, name, buy_date, buy_price, shares, amount, stop_loss_price, target_price, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'holding')
        ''', (ts_code, name, trade_date, price, shares, amount, stop_loss_price, target_price))
        
        # 记录交易
        cursor.execute('''
            INSERT INTO trades 
            (ts_code, name, action, price, shares, amount, trade_date, reason)
            VALUES (?, ?, '买入', ?, ?, ?, ?, ?)
        ''', (ts_code, name, price, shares, amount, trade_date, f"揉搓线企稳买入"))
        
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


def execute_sell(position_id, ts_code, name, price, reason, trade_date):
    """
    执行卖出
    
    返回：(是否成功, 盈亏信息)
    """
    try:
        conn = sqlite3.connect(TRADE_DB)
        cursor = conn.cursor()
        
        # 获取持仓
        cursor.execute('SELECT * FROM positions WHERE id=? AND status="holding"', (position_id,))
        pos = cursor.fetchone()
        
        if not pos:
            return False, "无持仓"
        
        buy_price = pos[3]
        shares = pos[4]
        buy_amount = pos[5]
        
        # 计算盈亏
        sell_amount = shares * price
        profit = sell_amount - buy_amount
        profit_rate = profit / buy_amount * 100
        
        # 计算持有天数
        buy_date = datetime.strptime(pos[3], '%Y%m%d')
        sell_date = datetime.strptime(trade_date, '%Y%m%d')
        holding_days = (sell_date - buy_date).days
        
        # 更新持仓状态
        cursor.execute('UPDATE positions SET status="sold" WHERE id=?', (position_id,))
        
        # 记录交易
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


def get_holding_positions():
    """获取当前持仓"""
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


def check_and_execute_trades(trade_date):
    """
    检查持仓并执行止盈止损
    
    检查逻辑：
    1. 盈利达到5% → 卖出
    2. 亏损达到3% → 止损
    3. 追踪止盈：盈利回撤超过2% → 卖出
    """
    try:
        positions = get_holding_positions()
        
        if not positions:
            return
        
        # 获取当前价格
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
            
            print(f"\n📊 {name}: 买入价{buy_price:.2f} | 当前{current_price:.2f} | 盈亏{profit_rate:.2f}%")
            
            # 止盈
            if current_price >= target:
                success, result = execute_sell(pos['id'], ts_code, name, current_price, "止盈5%", trade_date)
                if success:
                    print(f"✅ 止盈卖出: {name} | 盈利{result['profit_rate']:.2f}%")
                continue
            
            # 止损
            if current_price <= stop_loss:
                success, result = execute_sell(pos['id'], ts_code, name, current_price, "止损3%", trade_date)
                if success:
                    print(f"❌ 止损卖出: {name} | 亏损{result['profit_rate']:.2f}%")
                continue
            
            # 追踪止盈（盈利回撤超过2%）
            if profit_rate > 5:
                peak_profit = pos.get('peak_profit', profit_rate)
                if profit_rate < peak_profit - 2:
                    success, result = execute_sell(pos['id'], ts_code, name, current_price, "追踪止盈", trade_date)
                    if success:
                        print(f"✅ 追踪止盈: {name} | 盈利{result['profit_rate']:.2f}%")
        
    except Exception as e:
        print(f"检查持仓失败: {e}")


# ==================== 每日交易 ====================
def daily_trade(trade_date):
    """
    每日交易流程
    
    1. 检查持仓执行止盈止损
    2. 筛选新的买入信号
    3. 执行买入
    """
    try:
        print(f"\n{'='*70}")
        print(f"📈 每日交易 | {trade_date}")
        print(f"{'='*70}")
        
        # 初始化数据库
        init_trade_db()
        
        # 1. 检查持仓
        print(f"\n[1/3] 检查持仓...")
        check_and_execute_trades(trade_date)
        
        # 2. 筛选买入信号
        print(f"\n[2/3] 筛选买入信号...")
        signals = filter_stocks_by_trade_signal(trade_date)
        
        if not signals:
            print("⚠️ 无买入信号")
            return
        
        # 3. 执行买入
        print(f"\n[3/3] 执行买入...")
        positions = get_holding_positions()
        
        for signal in signals[:TradeConfig.MAX_POSITIONS - len(positions)]:
            ts_code = signal['ts_code']
            name = signal['name']
            price = signal['current_price']
            
            # 获取持仓
            positions = get_holding_positions()
            if len(positions) >= TradeConfig.MAX_POSITIONS:
                break
            
            # 检查是否已持仓
            holding_codes = [p['ts_code'] for p in positions]
            if ts_code in holding_codes:
                continue
            
            # 执行买入
            success, result = execute_buy(ts_code, name, price, trade_date, signal['signal_score'])
            
            if success:
                print(f"✅ 买入成功: {name} @ {price:.2f} | {result['shares']}股 | 金额{result['amount']:.2f}")
            else:
                print(f"⚠️ 买入失败: {name} - {result}")
        
        # 4. 显示持仓
        print(f"\n[持仓状态]")
        positions = get_holding_positions()
        
        if positions:
            total_value = 0
            total_cost = 0
            
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
                        current_price = df['close'].iloc[-1]
                        current_value = pos['shares'] * current_price
                        profit = current_value - pos['amount']
                        profit_rate = profit / pos['amount'] * 100
                        
                        print(f"  {pos['name']}: 买入{pos['buy_price']:.2f} | 当前{current_price:.2f} | 盈亏{profit_rate:.2f}%")
                        
                        total_value += current_value
                        total_cost += pos['amount']
                except:
                    print(f"  {pos['name']}: 无法获取现价")
            
            if total_cost > 0:
                total_profit_rate = (total_value - total_cost) / total_cost * 100
                print(f"\n  总盈亏: {total_profit_rate:.2f}%")
        else:
            print("  空仓")
        
        # 5. 绩效统计
        print_trade_stats()
        
    except Exception as e:
        print(f"❌ 交易失败: {e}")


def print_trade_stats():
    """打印交易统计"""
    try:
        conn = sqlite3.connect(TRADE_DB)
        
        # 总交易统计
        df = pd.read_sql('''
            SELECT action, COUNT(*) as count, SUM(amount) as total_amount, 
                   SUM(profit) as total_profit, AVG(profit_rate) as avg_rate
            FROM trades
            GROUP BY action
        ''', conn)
        
        if not df.empty:
            print(f"\n📊 交易统计")
            for _, row in df.iterrows():
                print(f"  {row['action']}: {row['count']}笔 | 总额{row['total_amount']:.2f}万")
                if row['action'] == '卖出' and row['total_profit'] != 0:
                    print(f"    盈亏: {row['total_profit']:.2f}元 | 平均{row['avg_rate']:.2f}%")
        
        # 胜率统计
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
                print(f"  胜率: {win_rate:.1f}% ({wins}/{total})")
        
        conn.close()
        
    except Exception as e:
        print(f"统计失败: {e}")


# ==================== 历史回测 ====================
def backtest_strategy(start_date, end_date, lookback_days=20):
    """
    策略回测
    
    回测逻辑：
    1. 逐日扫描历史数据
    2. 检测揉搓线企稳信号
    3. 模拟买入卖出
    4. 统计绩效
    """
    try:
        print(f"\n{'='*70}")
        print(f"📈 策略回测 | {start_date} ~ {end_date}")
        print(f"{'='*70}")
        
        init_trade_db()
        
        # 获取交易日列表
        cal = pro.trade_cal(start_date=start_date, end_date=end_date)
        trading_days = cal[cal['is_open'] == 1]['cal_date'].tolist()
        
        print(f"回测交易日: {len(trading_days)}天")
        
        total_profit = 0
        total_trades = 0
        win_trades = 0
        
        for trade_date in trading_days:
            # 获取当天涨停的股票
            try:
                zt_df = pro.limit_list_ths(trade_date=trade_date, limit_type='涨停池')
                
                if zt_df is None or zt_df.empty:
                    continue
                
                # 检查每只股票的揉搓线信号
                for _, row in zt_df.iterrows():
                    ts_code = row.get('ts_code')
                    zt_date = trade_date
                    zt_close = row.get('close', 0)
                    
                    # 跳过ST股
                    name = row.get('name', '')
                    if 'ST' in name.upper():
                        continue
                    
                    # 获取后续数据
                    future_start = (datetime.strptime(trade_date, '%Y%m%d') + timedelta(days=1)).strftime('%Y%m%d')
                    future_end = (datetime.strptime(trade_date, '%Y%m%d') + timedelta(days=15)).strftime('%Y%m%d')
                    
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
                        
                        if daily_df is None or daily_df.empty or len(daily_df) < 3:
                            continue
                        
                        daily_df = daily_df.sort_values('trade_date')
                        
                        # 检查揉搓线形态
                        klines = daily_df.head(5).to_dict('records')
                        
                        for i in range(len(klines) - 2):
                            k1 = klines[i]
                            k2 = klines[i+1]
                            
                            if is_rubbing_line(k1, k2):
                                # 模拟买入
                                buy_price = k2['close']
                                
                                # 检查后续是否达到5%
                                for j in range(i+2, len(klines)):
                                    sell_price = klines[j]['close']
                                    profit_rate = (sell_price - buy_price) / buy_price
                                    
                                    if profit_rate >= 0.05:
                                        # 止盈
                                        total_profit += profit_rate
                                        total_trades += 1
                                        win_trades += 1
                                        print(f"✓ 止盈: {name} {profit_rate*100:.2f}%")
                                        break
                                    elif profit_rate <= -0.03:
                                        # 止损
                                        total_profit += profit_rate
                                        total_trades += 1
                                        print(f"✗ 止损: {name} {profit_rate*100:.2f}%")
                                        break
                                
                                break
                    
                    except Exception as e:
                        continue
            
            except Exception as e:
                continue
        
        print(f"\n📊 回测结果")
        print(f"  总交易: {total_trades}笔")
        print(f"  胜率: {win_trades/total_trades*100:.1f}%")
        print(f"  总收益率: {total_profit*100:.2f}%")
        print(f"  平均收益: {total_profit/total_trades*100:.2f}%")
        
    except Exception as e:
        print(f"❌ 回测失败: {e}")


# ==================== 主程序 ====================
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='涨停二波交易系统')
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
            # 获取最近交易日
            now = datetime.now()
            if now.hour < 15:
                query_date = (now - timedelta(days=1)).strftime('%Y%m%d')
            else:
                query_date = now.strftime('%Y%m%d')
            
            cal = pro.trade_cal(end_date=query_date)
            trade_date = cal[cal['is_open'] == 1]['cal_date'].iloc[-1]
        
        daily_trade(trade_date)
    
    elif args.mode == 'backtest':
        start = args.start or (datetime.now() - timedelta(days=60)).strftime('%Y%m%d')
        end = args.end or (datetime.now() - timedelta(days=1)).strftime('%Y%m%d')
        backtest_strategy(start, end)
    
    elif args.mode == 'stats':
        print_trade_stats()
