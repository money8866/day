# -*- coding: utf-8 -*-
"""
涨停二波交易系统 V3
优化版：止盈6%，止损2.5%（中间路线）
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

MYSTOCK_DIR = r"d:\mystock"
if MYSTOCK_DIR not in sys.path:
    sys.path.append(MYSTOCK_DIR)

DOTENV_PATH = os.path.join(MYSTOCK_DIR, "config", ".env")
load_dotenv(DOTENV_PATH)

import tushare as ts
TUSHARE_TOKEN = os.getenv('TUSHARE_TOKEN')
pro = ts.pro_api(TUSHARE_TOKEN)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache")
TRADE_DB = os.path.join(CACHE_DIR, "wave2_trades_v3.db")
os.makedirs(CACHE_DIR, exist_ok=True)


class TradeConfig:
    MIN_ADJUST_DAYS = 3
    MAX_ADJUST_DAYS = 20
    RUBBING_BODY_MAX = 0.03
    RUBBING_SHADOW_MIN = 0.01
    STABILIZATION_SCORE_MIN = 50
    MAX_POSITIONS = 3
    POSITION_SIZE = 0.3
    
    # V3参数：止盈6%，止损2.5%（中间路线）
    PROFIT_TARGET = 0.06
    STOP_LOSS = 0.025
    TRAILING_STOP = 0.025
    
    MIN_MARKET_CAP = 40
    MIN_PRICE = 5


def init_trade_db():
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
    
    conn.commit()
    conn.close()


def is_rubbing_line_v3(k1, k2):
    try:
        body1 = abs(k1['close'] - k1['open']) / k1['open']
        shadow1_upper = (k1['high'] - max(k1['open'], k1['close'])) / k1['open']
        shadow1_lower = (min(k1['open'], k1['close']) - k1['low']) / k1['open']
        
        body2 = abs(k2['close'] - k2['open']) / k2['open']
        shadow2_upper = (k2['high'] - max(k2['open'], k2['close'])) / k2['open']
        shadow2_lower = (min(k2['open'], k2['close']) - k2['low']) / k2['open']
        
        cond1 = body1 <= TradeConfig.RUBBING_BODY_MAX
        cond2 = body2 <= body1 * 1.3
        cond3 = shadow1_lower > 0.005 and shadow2_lower > 0.005
        cond4 = (shadow2_upper + shadow2_lower) > (shadow1_upper + shadow1_lower) * 0.8
        cond5 = min(max(k1['open'], k1['close']), max(k2['open'], k2['close'])) > max(min(k1['open'], k1['close']), min(k2['open'], k2['close']))
        
        score = sum([cond1, cond2, cond3, cond4, cond5])
        return score >= 3
        
    except Exception as e:
        return False


def is_stabilization_kline_v3(kline, prev_klines):
    try:
        open_p = kline['open']
        close_p = kline['close']
        high_p = kline['high']
        low_p = kline['low']
        
        body_size = abs(close_p - open_p) / open_p
        lower_shadow = (min(open_p, close_p) - low_p) / open_p
        
        cond1 = close_p > open_p and body_size < 0.03 and lower_shadow > 0.015
        
        if len(prev_klines) >= 5:
            avg_vol = np.mean([k['vol'] for k in prev_klines[-5:]])
            cond2 = kline['vol'] < avg_vol * 0.8
        else:
            cond2 = True
        
        cond3 = body_size < lower_shadow
        return cond1 and cond2 and cond3
        
    except Exception as e:
        return False


def analyze_rubbing_stabilization_v3(daily_df, zt_history):
    try:
        if daily_df is None or daily_df.empty or len(daily_df) < 20:
            return False, "数据不足", 0
        
        if not zt_history:
            return False, "无涨停历史", 0
        
        daily_df = daily_df.sort_values('trade_date').tail(15).copy()
        klines = daily_df.to_dict('records')
        
        last_zt_date = zt_history[0]['date']
        zt_close = zt_history[0].get('close', 0)
        
        try:
            zt_dt = datetime.strptime(last_zt_date, '%Y%m%d')
            today_dt = datetime.now()
            adjust_days = (today_dt - zt_dt).days
        except:
            adjust_days = 0
        
        if not (TradeConfig.MIN_ADJUST_DAYS <= adjust_days <= TradeConfig.MAX_ADJUST_DAYS):
            return False, f"调整{adjust_days}天不在范围", 0
        
        for i in range(len(klines) - 3):
            k1 = klines[-(i+3)]
            k2 = klines[-(i+2)]
            
            if is_rubbing_line_v3(k1, k2):
                if i + 1 < len(klines):
                    next_kline = klines[-(i+1)]
                    prev_klines = klines[:-(i+1)]
                    
                    if is_stabilization_kline_v3(next_kline, prev_klines):
                        score = 50
                        
                        if 5 <= adjust_days <= 12:
                            score += 20
                        
                        return True, f"揉搓线+企稳(调整{adjust_days}天)", min(score, 90)
        
        return False, "无信号", 0
        
    except Exception as e:
        return False, f"分析失败:{str(e)}", 0


def filter_stocks_by_trade_signal_v3(trade_date, lookback_days=20):
    try:
        print(f"\n{'='*60}")
        print(f"🔍 V3筛选交易信号股票")
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
                daily_df = pro.daily(ts_code=ts_code, start_date=start, end_date=trade_date)
                
                if daily_df is None or daily_df.empty:
                    continue
                
                daily_df = daily_df.sort_values('trade_date')
                
                zt_history = [{'date': zt_date, 'close': zt_close}]
                is_signal, signal_desc, signal_score = analyze_rubbing_stabilization_v3(daily_df, zt_history)
                
                if is_signal and signal_score >= TradeConfig.STABILIZATION_SCORE_MIN:
                    latest_price = daily_df['close'].iloc[-1]
                    
                    signals.append({
                        'ts_code': ts_code,
                        'name': name,
                        'industry': industry,
                        'zt_date': zt_date,
                        'zt_price': zt_close,
                        'current_price': latest_price,
                        'signal_type': '揉搓线企稳V3',
                        'signal_desc': signal_desc,
                        'signal_score': signal_score,
                        'profit_potential': (latest_price - zt_close) / zt_close * 100
                    })
                    
                    print(f"✓ V3信号: {name} {signal_desc} | 分数:{signal_score}")
            
            except Exception as e:
                continue
        
        conn.close()
        signals.sort(key=lambda x: x['signal_score'], reverse=True)
        print(f"\n📊 V3信号筛选完成: 共{len(signals)}个信号")
        
        return signals
        
    except Exception as e:
        print(f"❌ V3筛选失败: {e}")
        return []


def execute_buy_v3(ts_code, name, price, trade_date, signal_score):
    try:
        positions = get_holding_positions_v3()
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
        ''', (ts_code, name, price, shares, amount, trade_date, "揉搓线企稳V3买入"))
        
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


def execute_sell_v3(position_id, ts_code, name, price, reason, trade_date):
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


def get_holding_positions_v3():
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


def check_and_execute_trades_v3(trade_date):
    try:
        positions = get_holding_positions_v3()
        
        if not positions:
            return
        
        current_prices = {}
        for pos in positions:
            ts_code = pos['ts_code']
            try:
                df = pro.daily(ts_code=ts_code, trade_date=trade_date)
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
            
            if current_price >= target:
                success, result = execute_sell_v3(pos['id'], ts_code, name, current_price, "V3止盈6%", trade_date)
                if success:
                    print(f"✅ V3止盈卖出: {name} | 盈利{result['profit_rate']:.2f}%")
                continue
            
            if current_price <= stop_loss:
                success, result = execute_sell_v3(pos['id'], ts_code, name, current_price, "V3止损2.5%", trade_date)
                if success:
                    print(f"❌ V3止损卖出: {name} | 亏损{result['profit_rate']:.2f}%")
                continue
            
            if profit_rate > 6:
                peak_profit = pos.get('peak_profit', profit_rate)
                if profit_rate < peak_profit - 2.5:
                    success, result = execute_sell_v3(pos['id'], ts_code, name, current_price, "V3追踪止盈", trade_date)
                    if success:
                        print(f"✅ V3追踪止盈: {name} | 盈利{result['profit_rate']:.2f}%")
        
    except Exception as e:
        print(f"检查持仓失败: {e}")


def daily_trade_v3(trade_date):
    try:
        print(f"\n{'='*70}")
        print(f"📈 V3每日交易 | {trade_date}")
        print(f"{'='*70}")
        
        init_trade_db()
        
        print(f"\n[1/3] 检查持仓...")
        check_and_execute_trades_v3(trade_date)
        
        print(f"\n[2/3] V3筛选买入信号...")
        signals = filter_stocks_by_trade_signal_v3(trade_date)
        
        if not signals:
            print("⚠️ 无V3买入信号")
            return
        
        print(f"\n[3/3] V3执行买入...")
        positions = get_holding_positions_v3()
        
        for signal in signals[:TradeConfig.MAX_POSITIONS - len(positions)]:
            ts_code = signal['ts_code']
            name = signal['name']
            price = signal['current_price']
            
            positions = get_holding_positions_v3()
            if len(positions) >= TradeConfig.MAX_POSITIONS:
                break
            
            holding_codes = [p['ts_code'] for p in positions]
            if ts_code in holding_codes:
                continue
            
            success, result = execute_buy_v3(ts_code, name, price, trade_date, signal['signal_score'])
            
            if success:
                print(f"✅ V3买入成功: {name} @ {price:.2f}")
            else:
                print(f"⚠️ V3买入失败: {name} - {result}")
        
        print_trade_stats_v3()
        
    except Exception as e:
        print(f"❌ V3交易失败: {e}")


def print_trade_stats_v3():
    try:
        conn = sqlite3.connect(TRADE_DB)
        
        df = pd.read_sql('''
            SELECT action, COUNT(*) as count, SUM(amount) as total_amount, 
                   SUM(profit) as total_profit, AVG(profit_rate) as avg_rate
            FROM trades
            GROUP BY action
        ''', conn)
        
        if not df.empty:
            print(f"\n📊 V3交易统计")
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
                print(f"  V3胜率: {win_rate:.1f}% ({wins}/{total})")
        
        conn.close()
        
    except Exception as e:
        print(f"V3统计失败: {e}")


def backtest_strategy_v3(start_date, end_date, lookback_days=20):
    try:
        print(f"\n{'='*70}")
        print(f"📈 V3策略回测 | {start_date} ~ {end_date}")
        print(f"{'='*70}")
        
        init_trade_db()
        
        cal = pro.trade_cal(start_date=start_date, end_date=end_date)
        trading_days = cal[cal['is_open'] == 1]['cal_date'].tolist()
        
        print(f"V3回测交易日: {len(trading_days)}天")
        
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
                        daily_df = pro.daily(ts_code=ts_code, start_date=future_start, end_date=future_end)
                        
                        if daily_df is None or daily_df.empty or len(daily_df) < 5:
                            continue
                        
                        daily_df = daily_df.sort_values('trade_date')
                        
                        klines = daily_df.head(7).to_dict('records')
                        
                        for i in range(len(klines) - 3):
                            k1 = klines[i]
                            k2 = klines[i+1]
                            
                            if is_rubbing_line_v3(k1, k2):
                                buy_price = k2['close']
                                
                                target_price = buy_price * 1.06
                                stop_price = buy_price * 0.975
                                
                                for j in range(i+2, len(klines)):
                                    sell_price = klines[j]['close']
                                    profit_rate = (sell_price - buy_price) / buy_price
                                    
                                    if sell_price >= target_price:
                                        total_profit += profit_rate
                                        total_trades += 1
                                        win_trades += 1
                                        print(f"✓ V3止盈: {name} {profit_rate*100:.2f}%")
                                        break
                                    elif sell_price <= stop_price:
                                        total_profit += profit_rate
                                        total_trades += 1
                                        print(f"✗ V3止损: {name} {profit_rate*100:.2f}%")
                                        break
                                
                                break
                        
                    except Exception as e:
                        continue
            
            except Exception as e:
                continue
        
        print(f"\n📊 V3回测结果")
        print(f"  V3总交易: {total_trades}笔")
        print(f"  V3胜率: {win_trades/total_trades*100:.1f}%")
        print(f"  V3总收益率: {total_profit*100:.2f}%")
        if total_trades > 0:
            print(f"  V3平均收益: {total_profit/total_trades*100:.2f}%")
        
    except Exception as e:
        print(f"❌ V3回测失败: {e}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='涨停二波交易系统 V3')
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
        
        daily_trade_v3(trade_date)
    
    elif args.mode == 'backtest':
        start = args.start or (datetime.now() - timedelta(days=60)).strftime('%Y%m%d')
        end = args.end or (datetime.now() - timedelta(days=1)).strftime('%Y%m%d')
        backtest_strategy_v3(start, end)
    
    elif args.mode == 'stats':
        print_trade_stats_v3()
