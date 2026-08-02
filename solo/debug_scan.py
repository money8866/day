# -*- coding: utf-8 -*-
"""调试单日扫描"""
import os, sys, json, sqlite3
import pandas as pd
sys.path.insert(0, r'd:\mystock\solo')
from tail_strategy import TailStrategy
from tail_backtest_tdx import parse_tdx_day_file, ts_code_to_tdx_file, load_theme_stocks, calc_theme_strength

CACHE_DIR = r'D:\mystock\cache_daily'
STOCK_DB = os.path.join(CACHE_DIR, 'stock_data.db')
TRADE_DATE = '20260731'

# 加载主题
theme_stocks, stock_themes = load_theme_stocks()
print(f"主题数: {len(theme_stocks)}, 股票数: {len(stock_themes)}")

# 测试 600594.SH (之前test_tail_end_0731.py 得到103分)
test_code = '600594.SH'
print(f"\n测试 {test_code}:")

# 加载K线
tdx_file = ts_code_to_tdx_file(test_code)
df = parse_tdx_day_file(tdx_file)
df = df[df['trade_date'] <= TRADE_DATE].copy()
print(f"  K线数: {len(df)}")

# 当日行情
day_row = df[df['trade_date'] == TRADE_DATE]
if day_row.empty:
    print(f"  {TRADE_DATE} 无行情")
else:
    day = day_row.iloc[0]
    print(f"  当日: open={day['open']}, close={day['close']}, pct={day['pct_chg']:.2f}%")

# 技术因子(前一日)
prev_date = df[df['trade_date'] < TRADE_DATE]['trade_date'].iloc[-1]
print(f"  前一交易日: {prev_date}")

conn = sqlite3.connect(STOCK_DB, timeout=10.0)
factor_rename = {
    'macd_dif_bfq': 'macd_dif', 'macd_dea_bfq': 'macd_dea', 'macd_bfq': 'macd',
    'kdj_bfq': 'kdj_j', 'kdj_k_bfq': 'kdj_k', 'kdj_d_bfq': 'kdj_d',
    'rsi_bfq_6': 'rsi_6', 'rsi_bfq_12': 'rsi_12', 'rsi_bfq_24': 'rsi_24',
    'boll_mid_bfq': 'boll_mid', 'boll_upper_bfq': 'boll_upper',
    'boll_lower_bfq': 'boll_lower', 'cci_bfq': 'cci',
}
fdf = pd.read_sql_query(
    'SELECT * FROM stk_factor_pro WHERE ts_code = ? AND trade_date = ?',
    conn, params=(test_code, prev_date)
)
conn.close()
if fdf.empty:
    print(f"  无技术因子")
else:
    fdf = fdf.rename(columns=factor_rename)
    factor_row = fdf.iloc[0].to_dict()
    print(f"  macd_dif={factor_row.get('macd_dif')}, turnover_rate={factor_row.get('turnover_rate')}, total_mv={factor_row.get('total_mv')}")

# 主题
themes = stock_themes.get(test_code, [])
print(f"  主题: {themes}")

# 主题强度
for t in themes:
    s, z = calc_theme_strength(t, theme_stocks, {test_code: df}, TRADE_DATE)
    print(f"    {t}: strength={s:.2f}, zt={z}")

# 注:calc_theme_strength需要所有主题成份股的K线,这里只加载了1只,结果不准确
# 加载全市场K线
print("\n加载全市场K线...")
all_klines = {}
for code in stock_themes.keys():
    if code.startswith(('9', '4')):
        continue
    tf = ts_code_to_tdx_file(code)
    if not tf or not os.path.exists(tf):
        continue
    kdf = parse_tdx_day_file(tf)
    if kdf is None or kdf.empty:
        continue
    kdf = kdf[kdf['trade_date'] <= TRADE_DATE].copy()
    if len(kdf) >= 30:
        all_klines[code] = kdf
print(f"  加载: {len(all_klines)}只")

# 重新计算主题强度
best_theme = themes[0] if themes else ''
best_strength = -999
best_zt = 0
for t in themes:
    s, z = calc_theme_strength(t, theme_stocks, all_klines, TRADE_DATE)
    print(f"    {t}: strength={s:.2f}, zt={z}")
    if s > best_strength:
        best_strength = s
        best_theme = t
        best_zt = z

# layer
best_layer = 'follower'
for code, name, ly in theme_stocks.get(best_theme, []):
    if code == test_code:
        best_layer = ly
        break
print(f"  best_theme={best_theme}, strength={best_strength:.2f}, zt={best_zt}, layer={best_layer}")

# 评分
strategy = TailStrategy()
q = {
    'open': float(day['open']),
    'high': float(day['high']),
    'low': float(day['low']),
    'price': float(day['close']),
    'last_close': float(day['pre_close']) if not pd.isna(day['pre_close']) else 0,
    'pct_chg': float(day['pct_chg']),
    'vol': float(day['vol']),
}
turnover = float(factor_row.get('turnover_rate', 0) or 0) if not fdf.empty else 0
total_mv = float(factor_row.get('total_mv', 0) or 0) if not fdf.empty else 0

# 硬过滤测试
passed, reason = strategy.hard_filter(test_code, q, df, turnover, total_mv, best_strength)
print(f"\n  硬过滤: passed={passed}, reason={reason}")

# 完整评分
sig = strategy.score(
    test_code, q, df, factor_row if not fdf.empty else None,
    turnover, total_mv, best_theme, best_strength, best_layer, best_zt, snap=None
)
print(f"\n  评分结果: {sig}")
