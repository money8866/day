# -*- coding: utf-8 -*-
"""「猎尾V3」尾盘突袭 - 单测: 用最近交易日数据验证盘中扫描"""
import os, sys, json, time, glob, sqlite3, warnings
from datetime import datetime, timedelta
from collections import defaultdict, deque, Counter
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
import tushare as ts

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, '..', 'cache_daily')
TRADE_DATE = '20260803'

# 1. 加载主题成份股
json_path = os.path.join(CACHE_DIR, 'theme_stock_map_latest.json')
with open(json_path, 'r', encoding='utf-8') as f:
    data = json.load(f)
themes_data = data.get('themes', {})
theme_stocks, stock_themes = {}, {}
for theme_name, stocks in themes_data.items():
    theme_stocks[theme_name] = []
    for s in stocks:
        code = s.get('code')
        if not code: continue
        via = s.get('via', '')
        layer = 'leader' if via == 'leader_company' else ('middle' if via == 'core_company' else 'member')
        theme_stocks[theme_name].append((code, s.get('name', ''), layer))
        stock_themes.setdefault(code, []).append(theme_name)

# 2. 加载日线行情
pro = ts.pro_api()
all_codes = list(stock_themes.keys())
quotes, kline_cache = {}, {}
start_date = (datetime.strptime(TRADE_DATE, '%Y%m%d') - timedelta(days=90)).strftime('%Y%m%d')
batch_size = 200
for i in range(0, len(all_codes), batch_size):
    batch = all_codes[i:i+batch_size]
    try:
        df = pro.daily(ts_code=",".join(batch), start_date=start_date, end_date=TRADE_DATE)
        if df is not None and not df.empty:
            for code, grp in df.groupby('ts_code'):
                grp = grp.sort_values('trade_date').reset_index(drop=True)
                grp = grp[['trade_date', 'close', 'high', 'low', 'vol', 'pct_chg', 'open', 'pre_close', 'amount']]
                kline_cache[code] = grp
                today = grp[grp['trade_date'] == TRADE_DATE]
                if not today.empty:
                    r = today.iloc[0]
                    quotes[code] = {'price': float(r['close']), 'pct_chg': float(r['pct_chg']),
                                    'high': float(r['high']), 'low': float(r['low']),
                                    'open': float(r['open']), 'last_close': float(r['pre_close']),
                                    'vol': float(r['vol']), 'amount': float(r['amount'])}
    except Exception as e:
        pass
    time.sleep(0.15)
print(f'行情加载完成: {len(quotes)}只有今日行情, {len(kline_cache)}只K线')

# 3. 加载缓存数据
turnover_cache = {}
tf = os.path.join(CACHE_DIR, f'turnover_rate_{TRADE_DATE}.csv')
if os.path.exists(tf):
    df_t = pd.read_csv(tf); turnover_cache = dict(zip(df_t['ts_code'], df_t['turnover_rate']))
    print(f'换手率缓存: {len(turnover_cache)}只')

stock_factors = {}
db_path = os.path.join(CACHE_DIR, 'stock_data.db')
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path, timeout=10.0)
    rows = conn.execute('SELECT DISTINCT trade_date FROM stk_factor_pro ORDER BY trade_date DESC LIMIT 2').fetchall()
    if rows:
        factor_date = str(rows[1][0]) if len(rows) >= 2 else str(rows[0][0])
        df_all = pd.read_sql_query('SELECT * FROM stk_factor_pro WHERE trade_date = ?', conn, params=(factor_date,))
        conn.close()
        if not df_all.empty:
            fr = {'macd_dif_bfq': 'macd_dif', 'macd_dea_bfq': 'macd_dea', 'macd_bfq': 'macd',
                  'kdj_bfq': 'kdj_j', 'kdj_k_bfq': 'kdj_k', 'kdj_d_bfq': 'kdj_d',
                  'rsi_bfq_6': 'rsi_6', 'rsi_bfq_12': 'rsi_12', 'rsi_bfq_24': 'rsi_24',
                  'boll_mid_bfq': 'boll_mid', 'boll_upper_bfq': 'boll_upper', 'boll_lower_bfq': 'boll_lower', 'cci_bfq': 'cci'}
            valid = {k: v for k, v in fr.items() if k in df_all.columns}
            df_all = df_all.rename(columns=valid)
            for code, g in df_all.groupby('ts_code'):
                stock_factors[code] = g.sort_values('trade_date').reset_index(drop=True)
    print(f'技术因子: {len(stock_factors)}只')

stock_mv = {}
mv_file = os.path.join(CACHE_DIR, f'market_{TRADE_DATE}.csv')
if not os.path.exists(mv_file):
    files = glob.glob(os.path.join(CACHE_DIR, 'market_*.csv'))
    if files: mv_file = max(files, key=os.path.getmtime)
if os.path.exists(mv_file):
    df_mv = pd.read_csv(mv_file); stock_mv = dict(zip(df_mv['ts_code'], df_mv['total_mv'].astype(float)))
    print(f'总市值: {len(stock_mv)}只')

# 4. 模拟分时快照
intraday_snapshots = {}
for code, q in quotes.items():
    c, h, l, o, v = q['price'], q['high'], q['low'], q['open'], q['vol']
    tail_base_price = (o + h + l + 2 * c) / 5.0
    intraday_snapshots[code] = {'tail_base_price': tail_base_price, 'tail_base_vol': v * 0.85, 'morning_vol': v * 0.45}

# 5. 主题强度历史
theme_score_history = defaultdict(lambda: deque(maxlen=15))
theme_volume_history = defaultdict(lambda: deque(maxlen=15))
for tn, stocks in theme_stocks.items():
    tp, tv, cnt = 0, 0, 0
    for code, _, _ in stocks:
        q = quotes.get(code)
        if q: tp += q['pct_chg']; tv += q['amount']; cnt += 1
    theme_score_history[tn].append(tp / cnt if cnt else 0)
    theme_volume_history[tn].append(tv)

# 6. 动态实例化并运行V3扫描
from realtime_theme_monitor import RealtimeThemeMonitor
monitor = RealtimeThemeMonitor.__new__(RealtimeThemeMonitor)
monitor.tail_tracker_db = os.path.join(CACHE_DIR, 'tail_signal_tracker.db')
monitor._init_tail_tracker()
monitor._get_last_trade_date = lambda: TRADE_DATE
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
monitor.theme_lifecycle_cache = {}
monitor.theme_forward_cache = {}
monitor.stock_zt_first_time = {}

t0 = time.time()
signals = monitor.scan_tail_end_entry_v3()
print(f'\n扫描耗时: {time.time()-t0:.1f}s, 信号数: {len(signals)}')

# ── 输出全部信号 ──
print(f"\n\n{'='*130}")
print(f"🎯 「猎尾V3」尾盘突袭信号 [{TRADE_DATE}] 完整列表 共{len(signals)}只")
print(f"{'='*130}")
print(f"{'排名':<4} {'代码':<12} {'名称':<10} {'主题':<14} {'总分':>4} {'主题':>4} {'资金':>4} {'角色':>4} {'技术':>4} {'尾盘':>4} {'空间':>4} {'风险':>4} {'信号':<8} {'角色':<6} {'涨幅':>7}")
print(f"{'-'*130}")

for i, s in enumerate(signals, 1):
    emoji = {'强买入': '✅', '买入观察': '🟢', '关注': '👀'}.get(s['signal'], '')
    role_cn = {'leader': '龙头', 'core': '中军', 'follow': '跟风', 'weak': '弱关联'}.get(s.get('role', ''), s.get('role', ''))
    print(f"{i:<4} {s['ts_code']:<12} {s['name']:<10} {s['theme']:<14} {s['total_score']:>4} {s['theme_score']:>4} {s['capital_score']:>4} {s['role_score']:>4} {s['technical_score']:>4} {s['timing_score']:>4} {s['room_score']:>4} {s['risk_penalty']:>4} {emoji:<8} {role_cn:<6} {s['pct_chg']:>+6.1f}%")

print(f"{'='*130}")

# 统计
strong = sum(1 for s in signals if s['signal'] == '强买入')
buy = sum(1 for s in signals if s['signal'] == '买入观察')
watch = sum(1 for s in signals if s['signal'] == '关注')
role_cnt = Counter(s.get('role', '') for s in signals)
buy_cnt = Counter(s.get('buy_type', '') for s in signals)
print(f'\n📊 统计: 强买入{strong}  买入观察{buy}  关注{watch}')
print(f'🏆 角色分布: {dict(role_cnt)}')
print(f'🎯 买入类型: {dict(buy_cnt)}')

# 显示TOP5可解释性
print(f"\n📋 TOP3信号评分明细:")
for s in signals[:3]:
    print(f"\n{s.get('explain', '')}")
