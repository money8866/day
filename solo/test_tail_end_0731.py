# -*- coding: utf-8 -*-
"""
「猎尾」2:50尾盘突袭战法 - 20260731 回测
用7/31收盘数据模拟14:50尾盘扫描
"""
import os
import sys
import json
import time
import warnings
from datetime import datetime, timedelta
from collections import defaultdict, deque

warnings.filterwarnings('ignore')

# 确保能导入主模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import tushare as ts

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, '..', 'cache_daily')

TRADE_DATE = '20260731'
print(f"=== 「猎尾」尾盘突袭战法回测 [{TRADE_DATE}] ===\n")

# ── 1. 加载主题成份股 ──
print("[1/5] 加载主题成份股...")
json_path = os.path.join(CACHE_DIR, 'theme_stock_map_latest.json')
with open(json_path, 'r', encoding='utf-8') as f:
    data = json.load(f)

themes_data = data.get('themes', {})
theme_stocks = {}   # theme -> [(ts_code, name, layer)]
stock_themes = {}   # ts_code -> [theme, ...]

for theme_name, stocks in themes_data.items():
    theme_stocks[theme_name] = []
    for stock_info in stocks:
        ts_code = stock_info.get('code')
        if not ts_code:
            continue
        name = stock_info.get('name', '')
        via = stock_info.get('via', '')
        layer = 'leader' if via == 'leader_company' else ('middle' if via == 'core_company' else 'member')
        theme_stocks[theme_name].append((ts_code, name, layer))
        if ts_code not in stock_themes:
            stock_themes[ts_code] = []
        if theme_name not in stock_themes[ts_code]:
            stock_themes[ts_code].append(theme_name)

print(f"  主题数: {len(theme_stocks)}  股票数: {len(stock_themes)}")

# ── 2. 加载7/31日线行情(用作quotes) ──
print(f"\n[2/5] 加载{TRADE_DATE}日线行情...")
pro = ts.pro_api()

# 获取所有主题股票的当日行情
all_codes = list(stock_themes.keys())
print(f"  待查询股票: {len(all_codes)} 只")

# 分批获取行情
batch_size = 200
quotes = {}
kline_cache = {}  # ts_code -> DataFrame
start_date = (datetime.strptime(TRADE_DATE, '%Y%m%d') - timedelta(days=90)).strftime('%Y%m%d')

print(f"  拉取日线 {start_date}~{TRADE_DATE} ...")
for i in range(0, len(all_codes), batch_size):
    batch = all_codes[i:i+batch_size]
    try:
        df = pro.daily(ts_code=",".join(batch), start_date=start_date, end_date=TRADE_DATE)
        if df is not None and not df.empty:
            for code, grp in df.groupby('ts_code'):
                grp_sorted = grp.sort_values('trade_date').reset_index(drop=True)
                grp_sorted = grp_sorted[['trade_date', 'close', 'high', 'low', 'vol', 'pct_chg', 'open', 'pre_close', 'amount']]
                kline_cache[code] = grp_sorted
                # 取7/31当天的数据作为quote
                today_row = grp_sorted[grp_sorted['trade_date'] == TRADE_DATE]
                if not today_row.empty:
                    row = today_row.iloc[0]
                    quotes[code] = {
                        'price': float(row['close']),
                        'pct_chg': float(row['pct_chg']),
                        'high': float(row['high']),
                        'low': float(row['low']),
                        'open': float(row['open']),
                        'last_close': float(row['pre_close']),
                        'vol': float(row['vol']),
                        'amount': float(row['amount']),
                    }
    except Exception as e:
        print(f"  批次{i//batch_size}失败: {e}")
    time.sleep(0.15)  # 限速

print(f"  行情加载完成: {len(quotes)} 只有quote, {len(kline_cache)} 只有K线")

# ── 3. 加载换手率 + 技术因子 + 总市值 ──
print(f"\n[3/5] 加载换手率+技术因子+总市值缓存...")
turnover_file = os.path.join(CACHE_DIR, f'turnover_rate_{TRADE_DATE}.csv')
turnover_cache = {}
if os.path.exists(turnover_file):
    df_t = pd.read_csv(turnover_file)
    turnover_cache = dict(zip(df_t['ts_code'], df_t['turnover_rate']))
    print(f"  换手率: {len(turnover_cache)} 只")
else:
    print(f"  ⚠ 换手率文件不存在: {turnover_file}")

# 加载技术因子(从SQLite读取T-1数据,技术指标是前一交易日收盘后运算的)
import sqlite3
stock_factors = {}
db_path = os.path.join(CACHE_DIR, 'stock_data.db')
if os.path.exists(db_path):
    try:
        conn = sqlite3.connect(db_path, timeout=10.0)
        # 取次新交易日(T-1),避免使用尚未生成的当日数据
        rows = conn.execute(
            'SELECT DISTINCT trade_date FROM stk_factor_pro ORDER BY trade_date DESC LIMIT 2'
        ).fetchall()
        if rows:
            factor_date = str(rows[1][0]) if len(rows) >= 2 else str(rows[0][0])
            df_all = pd.read_sql_query(
                'SELECT * FROM stk_factor_pro WHERE trade_date = ?',
                conn, params=(factor_date,)
            )
            conn.close()
            if not df_all.empty:
                # 重命名字段 (_bfq后缀 -> 简洁名)
                factor_rename = {
                    'macd_dif_bfq': 'macd_dif', 'macd_dea_bfq': 'macd_dea', 'macd_bfq': 'macd',
                    'kdj_bfq': 'kdj_j', 'kdj_k_bfq': 'kdj_k', 'kdj_d_bfq': 'kdj_d',
                    'rsi_bfq_6': 'rsi_6', 'rsi_bfq_12': 'rsi_12', 'rsi_bfq_24': 'rsi_24',
                    'boll_mid_bfq': 'boll_mid', 'boll_upper_bfq': 'boll_upper',
                    'boll_lower_bfq': 'boll_lower', 'cci_bfq': 'cci',
                }
                valid_rename = {k: v for k, v in factor_rename.items() if k in df_all.columns}
                df_all = df_all.rename(columns=valid_rename)
                for code, group_df in df_all.groupby('ts_code'):
                    stock_factors[code] = group_df.sort_values('trade_date').reset_index(drop=True)
                print(f"  技术因子: {len(stock_factors)} 只 (SQLite {factor_date})")
            else:
                print(f"  ⚠ SQLite中{factor_date}无技术因子数据")
        else:
            conn.close()
            print("  ⚠ SQLite中stk_factor_pro表为空")
    except Exception as e:
        print(f"  ⚠ SQLite技术因子加载失败: {e}")
else:
    print(f"  ⚠ SQLite数据库不存在: {db_path}")

# 加载总市值(market_*.csv)
stock_mv = {}
mv_file = os.path.join(CACHE_DIR, f'market_{TRADE_DATE}.csv')
if not os.path.exists(mv_file):
    files = glob.glob(os.path.join(CACHE_DIR, 'market_*.csv'))
    if files:
        mv_file = max(files, key=os.path.getmtime)
if os.path.exists(mv_file):
    df_mv = pd.read_csv(mv_file)
    stock_mv = dict(zip(df_mv['ts_code'], df_mv['total_mv'].astype(float)))
    print(f"  总市值: {len(stock_mv)} 只 ({os.path.basename(mv_file)})")
else:
    print(f"  ⚠ 总市值文件不存在")

# ── 4. 模拟分时快照(用OHLC估算) ──
print(f"\n[4/5] 模拟14:30分时快照(用OHLC估算)...")
intraday_snapshots = {}
for ts_code, q in quotes.items():
    close = q['price']
    high = q['high']
    low = q['low']
    open_p = q['open']
    vol = q['vol']

    # 尾盘基准价: 用OHLC加权平均(偏向close)
    # 如果close > 均价,说明尾盘拉升; 如果close < 均价,说明尾盘回落
    tail_base_price = (open_p + high + low + 2 * close) / 5.0

    # 成交量分布: 早盘45%, 午盘前段40%, 尾盘15%
    morning_vol = vol * 0.45
    tail_base_vol = vol * 0.85  # 14:30累计量

    intraday_snapshots[ts_code] = {
        'tail_base_price': tail_base_price,
        'tail_base_vol': tail_base_vol,
        'morning_vol': morning_vol,
    }

print(f"  快照模拟完成: {len(intraday_snapshots)} 只")

# ── 5. 计算主题强度历史(简化: 用当日涨跌幅) ──
print(f"\n[5/5] 计算主题强度...")
theme_score_history = defaultdict(lambda: deque(maxlen=15))
theme_volume_history = defaultdict(lambda: deque(maxlen=15))
for theme_name, stocks in theme_stocks.items():
    total_pct = 0
    total_vol = 0
    cnt = 0
    for ts_code, name, layer in stocks:
        q = quotes.get(ts_code)
        if q:
            total_pct += q['pct_chg']
            total_vol += q['amount']
            cnt += 1
    avg_pct = total_pct / cnt if cnt > 0 else 0
    theme_score_history[theme_name].append(avg_pct)
    theme_volume_history[theme_name].append(total_vol)

# ── 6. 构造Monitor实例并注入数据 ──
print(f"\n[6/6] 运行「猎尾」扫描...")

from realtime_theme_monitor import RealtimeThemeMonitor
monitor = RealtimeThemeMonitor.__new__(RealtimeThemeMonitor)
# 手动初始化跟踪表(因为用了__new__绕过__init__)
monitor.tail_tracker_db = os.path.join(CACHE_DIR, 'tail_signal_tracker.db')
monitor._init_tail_tracker()
# 覆盖日期方法,确保信号日为回测日
monitor._get_last_trade_date = lambda: TRADE_DATE

# 注入数据
monitor.quotes = quotes
monitor.theme_stocks = theme_stocks
monitor.stock_themes = stock_themes
monitor.stock_klines = kline_cache
monitor.intraday_snapshots = intraday_snapshots
monitor.turnover_cache = turnover_cache
monitor.stock_factors = stock_factors
monitor.stock_mv = stock_mv
monitor.theme_score_history = theme_score_history
monitor.theme_volume_history = theme_volume_history
monitor.tail_entry_debug_printed = False

# 运行扫描
t0 = time.time()
signals = monitor.scan_tail_end_entry()
elapsed = time.time() - t0

# 写入跟踪表(用于未来交易日盘后回填和胜率分析)
monitor._save_tail_signals_to_tracker(signals)

print(f"\n扫描耗时: {elapsed:.2f}s")
print(f"信号总数: {len(signals)}")

# 输出结果
if signals:
    print(f"\n{'='*130}")
    print(f"🎯 「猎尾」尾盘突袭信号 [{TRADE_DATE} 收盘模拟] 共{len(signals)}只候选")
    print(f"{'='*130}")
    print(f"{'排名':<4} {'代码':<12} {'名称':<10} {'主题':<14} {'总分':>4} {'攻击':>4} {'结构':>4} {'位置':>4} {'共振':>4} {'技术':>4} {'诱多':>4} {'信号':<6} {'涨幅':>7} {'关键特征'}")
    print(f"{'-'*130}")

    for i, s in enumerate(signals[:20], 1):
        emoji = {'强买入': '✅', '买入': '🟢', '关注': '👀'}.get(s['signal'], '')
        d = s.get('detail', {})
        feats = []
        if d.get('tail_rally') is not None:
            feats.append(f"尾拉{d['tail_rally']:+.2f}%")
        if d.get('tail_vol_ratio') and d['tail_vol_ratio'] > 0.05:
            feats.append(f"尾量{d['tail_vol_ratio']:.2f}")
        if d.get('amplitude') is not None:
            feats.append(f"振幅{d['amplitude']}%")
        if d.get('vol_ratio_5d') and d['vol_ratio_5d'] < 0.85:
            feats.append(f"缩量{d['vol_ratio_5d']:.2f}")
        if d.get('close_ratio') and d['close_ratio'] > 0.95:
            feats.append('光头阳')
        if d.get('layer') == 'leader':
            feats.append('龙头')
        elif d.get('layer') == 'middle':
            feats.append('中军')
        # 技术因子
        if d.get('macd') == '零上多头': feats.append('MACD零上')
        elif d.get('macd') == '多头': feats.append('MACD多头')
        if d.get('kdj') == '金叉': feats.append('KDJ金叉')
        if d.get('rsi_6'): feats.append(f"RSI{d['rsi_6']:.0f}")
        if d.get('boll'): feats.append(f"BOLL:{d['boll']}")
        # 诱多红旗
        if d.get('trap_weak_day'): feats.append(f"⚠全天弱")
        if d.get('trap_long_lower'): feats.append(f"⚠长下影")
        if d.get('trap_high_stall'): feats.append(f"⚠高位滞涨")
        if d.get('trap_isolated'): feats.append(f"⚠孤立")
        if d.get('trap_upper_shadow'): feats.append(f"⚠上影")
        feat_str = ' '.join(feats[:7]) if feats else '-'
        trap_str = f"-{s.get('trap_penalty', 0)}" if s.get('trap_penalty', 0) > 0 else '0'
        print(f"{i:<4} {s['ts_code']:<12} {s['name']:<10} {s['theme']:<14} {s['total_score']:>4} {s['attack_score']:>4} {s['structure_score']:>4} {s['position_score']:>4} {s['theme_score']:>4} {s.get('tech_score', 0):>4} {trap_str:>4} {emoji:<6} {s['pct_chg']:>+6.1f}% {feat_str}")
    print(f"{'='*130}")

    # 信号统计
    strong = [s for s in signals if s['signal'] == '强买入']
    buy = [s for s in signals if s['signal'] == '买入']
    watch = [s for s in signals if s['signal'] == '关注']
    print(f"\n📊 信号分布: 强买入{len(strong)}  买入{len(buy)}  关注{len(watch)}")

    # 诱多扣分统计
    trapped = [s for s in signals if s.get('trap_penalty', 0) > 0]
    if trapped:
        print(f"⚠ 诱多风险标的: {len(trapped)}只 (扣分{min(s['trap_penalty'] for s in trapped)}~{max(s['trap_penalty'] for s in trapped)})")

    # 输出强买入+买入的详细信息
    buy_all = strong + buy
    if buy_all:
        print(f"\n{'─'*80}")
        print(f"💎 买入级信号详情:")
        print(f"{'─'*80}")
        for s in buy_all:
            d = s.get('detail', {})
            print(f"\n  {s['signal']} {s['name']}({s['ts_code']}) 总分{s['total_score']}  {s['theme']}")
            print(f"    涨幅: {s['pct_chg']:+.1f}%  价格: {s['price']:.2f}")
            print(f"    尾盘攻击: {s['attack_score']}/40  (尾拉{d.get('tail_rally', 0):+.2f}% 尾量{d.get('tail_vol_ratio', 0):.2f} 收盘{d.get('close_ratio', 0):.2f})")
            print(f"    全天结构: {s['structure_score']}/30  (振幅{d.get('amplitude', 0)}% 量比5d:{d.get('vol_ratio_5d', 0):.2f})")
            print(f"    位置安全: {s['position_score']}/20  (MA5距{d.get('ma5_dist', 0)}% MA10比{d.get('ma10_ratio', 0)} 回撤{d.get('pullback', 0)}%)")
            print(f"    主题共振: {s['theme_score']}/10  (强度{d.get('theme_strength', 0)} {d.get('layer', '')} 主题涨停{d.get('theme_zt', 0)})")
            if s.get('trap_penalty', 0) > 0:
                print(f"    诱多扣分: -{s['trap_penalty']}  ", end='')
                trap_reasons = []
                if d.get('trap_weak_day'): trap_reasons.append(f"全天弱尾拉({d['trap_weak_day']})")
                if d.get('trap_long_lower'): trap_reasons.append(f"长下影({d['trap_long_lower']})")
                if d.get('trap_high_stall'): trap_reasons.append(f"高位滞涨({d['trap_high_stall']})")
                if d.get('trap_isolated'): trap_reasons.append(f"孤立拉升({d['trap_isolated']})")
                if d.get('trap_upper_shadow'): trap_reasons.append("上影线")
                print(' + '.join(trap_reasons))
else:
    print("\n⚠ 无信号产生,请检查评分阈值或数据")

print(f"\n=== 回测完成 ===")
