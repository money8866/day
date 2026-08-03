# -*- coding: utf-8 -*-
"""调试 V2 实时主题动量 - 20260803 电力主题"""
import sys; sys.path.insert(0, r'd:\mystock\solo')
import os, json, pandas as pd, sqlite3
from tail_backtest_tdx import load_theme_stocks, parse_tdx_day_file, ts_code_to_tdx_file, calc_theme_momentum_daily, calc_theme_strength, STOCK_DB
from tail_strategy import TailStrategy

theme_stocks, stock_themes = load_theme_stocks()
strategy = TailStrategy()

# 加载K线
all_klines = {}
for code in stock_themes:
    if code.startswith(('9','4')): continue
    tf = ts_code_to_tdx_file(code)
    if tf and os.path.exists(tf):
        df = parse_tdx_day_file(tf)
        if df is not None:
            df = df[df['trade_date'] <= '20260803'].copy()
            if len(df) >= 20:
                all_klines[code] = df

# 技术因子
conn = sqlite3.connect(STOCK_DB, timeout=10.0)
factor_cache = {}
try:
    rows = conn.execute('SELECT ts_code, trade_date, factor_json FROM stk_factor_pro WHERE trade_date IN ("20260802","20260801")').fetchall()
    for r in rows:
        try:
            f = json.loads(r[2])
            factor_cache[(r[0], r[1])] = f
        except Exception:
            pass
except Exception:
    pass
conn.close()
print(f'因子缓存: {len(factor_cache)}条')

# 主题强度
trade_date = '20260803'
theme_strengths = {}
theme_zt_counts = {}
theme_avg_pcts = {}
theme_momentums = {}
for tn in theme_stocks:
    s, z, avg = calc_theme_strength(tn, theme_stocks, all_klines, trade_date)
    theme_strengths[tn] = s
    theme_zt_counts[tn] = z
    theme_avg_pcts[tn] = avg
    theme_momentums[tn] = calc_theme_momentum_daily(tn, theme_stocks, all_klines, trade_date)

# 电力V2
up, ar, lr, bc, tm = theme_momentums.get('电力', (0,0,0,0,None))
print(f'电力 V2: up_ratio={up:.1f}% avg_return={ar:.2f}% leader_return={lr:.2f}% bullish={bc}')

# 扫描电力主题
theme_name = '电力'
stocks = theme_stocks.get(theme_name, [])
print(f'\n扫描电力主题 {len(stocks)}只:')
power_scores = []
for code, name, layer in stocks:
    if code.startswith(('9','4')): continue
    kl = all_klines.get(code)
    if kl is None: continue
    row = kl[kl['trade_date'] == trade_date]
    if row.empty: continue

    day = row.iloc[0]
    kline_up_to = kl[kl['trade_date'] <= trade_date].copy()
    if len(kline_up_to) < 20: continue

    prev_dates = kl[kl['trade_date'] < trade_date]['trade_date'].tolist()
    factor_row = None
    prev_factor_row = None
    if prev_dates:
        prev_date = prev_dates[-1]
        factor_row = factor_cache.get((code, prev_date))
        if len(prev_dates) >= 2:
            prev_factor_row = factor_cache.get((code, prev_dates[-2]))

    turnover = float(factor_row.get('turnover_rate', 0) or 0) if factor_row else 0
    total_mv = float(factor_row.get('total_mv', 0) or 0) if factor_row else 0

    q = {
        'open': float(day['open']), 'high': float(day['high']), 'low': float(day['low']),
        'price': float(day['close']), 'last_close': float(day['pre_close']) if not pd.isna(day['pre_close']) else 0,
        'pct_chg': float(day['pct_chg']), 'vol': float(day['vol']),
    }

    best_strength = theme_strengths.get(theme_name, 0)
    best_zt = theme_zt_counts.get(theme_name, 0)
    theme_rank = 1
    lifecycle_score = 80 if best_strength > 2 else 60 if best_strength > 1 else 40
    forward_score = 70 if best_strength > 2 else 50 if best_strength > 1 else 30

    up_ratio, avg_return, leader_return, bullish_count, tail_momentum = theme_momentums.get(theme_name, (0,0,0,0,None))

    sig = strategy.score(
        code, q, kline_up_to, factor_row, turnover, total_mv,
        theme_name, best_strength, layer, best_zt,
        snap=None, prev_factor_row=prev_factor_row,
        theme_avg_pct=theme_avg_pcts.get(theme_name),
        index_pct=None,
        theme_rank=theme_rank,
        lifecycle_score=lifecycle_score,
        forward_score=forward_score,
        up_ratio=up_ratio, avg_return=avg_return, leader_return=leader_return,
        bullish_count=bullish_count, tail_momentum=tail_momentum,
    )
    if sig:
        power_scores.append(sig)
        print(f'  {code} {name:<10} 总分{sig["total_score"]:>3} {sig["signal"]:<6} '
              f'结构{sig["structure_score"]:>2} 攻击{sig["attack_score"]:>2} '
              f'位置{sig["position_score"]:>2} 趋势{sig["trend_score"]:>2} '
              f'主题{sig["theme_score"]:>2} 相对{sig["rel_strength_score"]:>2} '
              f'突破{sig["breakout_score"]:>2} 技术{sig["tech_score"]:>2} '
              f'波动扣{sig["vol_penalty"]:>2} 诱多扣{sig["trap_penalty"]:>2}')

print(f'\n电力主题共{len(power_scores)}只通过硬过滤')
power_scores.sort(key=lambda x: -x['total_score'])
for s in power_scores[:5]:
    print(f'  TOP: {s["ts_code"]} {s.get("name","")} 总分{s["total_score"]} {s["signal"]}')

# 打印未通过的原因
print(f'\n未通过硬过滤的分析:')
from tail_strategy import TailStrategy
ts = TailStrategy()
for code, name, layer in stocks[:30]:
    if code.startswith(('9','4')): continue
    kl = all_klines.get(code)
    if kl is None: continue
    row = kl[kl['trade_date'] == trade_date]
    if row.empty: continue
    day = row.iloc[0]
    q = {'price': float(day['close']), 'last_close': float(day['pre_close']) if not pd.isna(day['pre_close']) else 0,
         'pct_chg': float(day['pct_chg']), 'vol': float(day['vol']),
         'high': float(day['high']), 'low': float(day['low']), 'open': float(day['open'])}
    kline_up_to = kl[kl['trade_date'] <= trade_date].copy()
    turnover = 0
    total_mv = 0
    best_strength = theme_strengths.get(theme_name, 0)
    passed, reason = ts.hard_filter(code, q, kline_up_to, turnover, total_mv, best_strength)
    if not passed:
        print(f'  {code} {name:<10} 涨幅{q["pct_chg"]:+.2f}% ❌ {reason}')