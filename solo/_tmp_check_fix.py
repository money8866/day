# -*- coding: utf-8 -*-
"""验证修复方案：加速度因子改为截面百分位后的排名变化"""
import sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
import etf_mainline_strategy_tushare as M

TRADE_DATE = '20260806'
CACHE_DIR = M.ETF_FUND_CACHE_DIR
codes_ts = {}
for name, code in M.ETF_POOL.items():
    if code.startswith("5") or code.startswith("6"):
        codes_ts[code] = f"{code}.SH"
    else:
        codes_ts[code] = f"{code}.SZ"

all_data = {}
for code, ts_code in codes_ts.items():
    cache_file = os.path.join(CACHE_DIR, f"{ts_code}_{TRADE_DATE}.csv")
    df = M._read_cache(cache_file)
    if df is not None and 'vol' not in df.columns:
        df = None
    if df is not None and len(df) > 0:
        df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
        df = df.sort_values("trade_date").reset_index(drop=True)
        all_data[code] = df

bm_path = os.path.join(CACHE_DIR, f"idx_000300_{TRADE_DATE}.csv")
benchmark_df = M._read_cache(bm_path)
if benchmark_df is not None:
    benchmark_df["trade_date"] = pd.to_datetime(benchmark_df["trade_date"], format="%Y%m%d")
    benchmark_df = benchmark_df.sort_values("trade_date").reset_index(drop=True)

market_state, state_desc = M.classify_market_state(benchmark_df, M.MOM_PERIOD)
WEIGHT_MATRIX = {
    'trending':   {'mom_cross': 0.30, 'accel': 0.08, 'vol': 0.25, 'risk': 0.12, 'rel': 0.12, 'trend': 0.13, 'shrink': 0.00},
    'oscillating':{'mom_cross': 0.12, 'accel': 0.25, 'vol': 0.15, 'risk': 0.10, 'rel': 0.10, 'trend': 0.08, 'shrink': 0.20},
    'declining':  {'mom_cross': 0.20, 'accel': 0.10, 'vol': 0.15, 'risk': 0.20, 'rel': 0.20, 'trend': 0.10, 'shrink': 0.05},
}
w = WEIGHT_MATRIX.get(market_state, WEIGHT_MATRIX['oscillating'])
code_to_name = {v: k for k, v in M.ETF_POOL.items()}

rankings = []
for code, df in all_data.items():
    close = df['close']; n = len(close)
    mom_5d = close.pct_change(5).iloc[-1] * 100
    mom_20d = close.pct_change(20).iloc[-1] * 100
    mom_60d = close.pct_change(60).iloc[-1] * 100 if n >= 61 else mom_20d
    mom_weighted = mom_5d * 0.30 + mom_20d * 0.50 + mom_60d * 0.20
    mom_accel = mom_5d - mom_20d
    latest = close.iloc[-1]; prev = close.iloc[-2]
    day_chg = (latest - prev) / prev * 100
    rankings.append({'code': code, 'name': code_to_name.get(code, code),
                     'day_chg': round(day_chg, 2), 'mom_5d': round(mom_5d, 2),
                     'mom_20d': round(mom_20d, 2), 'mom_weighted': round(mom_weighted, 2),
                     'mom_accel': round(mom_accel, 2)})

# 原始 accel_score（绝对映射 50+mom_accel*10）
for r in rankings:
    r['accel_abs'] = max(0, min(100, 50 + r['mom_accel'] * 10))

# 截面百分位 accel（最强=100）
valid = sorted(rankings, key=lambda x: x['mom_accel'])
for i, r in enumerate(valid):
    r['accel_pct'] = round((i / (len(valid) - 1)) * 100, 2)

print(f"市场状态: {market_state}，35只ETF 加速度原始值与两种映射对比：")
print(f"{'名称':<8} {'代码':<8} {'当日%':>7} {'mom5d':>7} {'mom20d':>8} {'加速度':>7} {'绝对分':>6} {'百分位':>6}")
for r in sorted(rankings, key=lambda x: x['mom_accel'], reverse=True):
    print(f"{r['name']:<8} {r['code']:<8} {r['day_chg']:>+7.2f} {r['mom_5d']:>+7.2f} {r['mom_20d']:>+8.2f} "
          f"{r['mom_accel']:>+7.2f} {r['accel_abs']:>6.1f} {r['accel_pct']:>6.1f}")

# 用百分位 accel 重算综合分
for r in rankings:
    r['total_new'] = round(r['accel_pct'] * w['accel'] + 0, 2)  # 占位，见下方完整重算

# 完整重算：其它因子保持不变（从原脚本再取一次完整因子）
for r in rankings:
    df = all_data[r['code']]
    factors = M.calculate_multi_factor_score(df, benchmark_df, M.MOM_PERIOD)
    mom_cross = None
    r.update({k: v for k, v in factors.items() if k not in ('mom_cross_score', 'total_score')})

valid_mom = sorted([x for x in rankings], key=lambda x: x['mom_weighted'])
for i, rr in enumerate(valid_mom):
    rr['mom_cross_score'] = round((i / (len(valid_mom) - 1)) * 100, 2)

print("\n=== 修复后排名（accel 用百分位）===")
print(f"{'排名':>3} {'名称':<8} {'代码':<8} {'新总评':>6} {'旧总评':>6} {'当日%':>7} {'mom20':>7} {'截面':>5} {'加速百分':>6}")
for r in rankings:
    r['total_new'] = round(
        r['mom_cross_score'] * w['mom_cross'] + r['accel_pct'] * w['accel'] +
        r['accel_score'] * 0 + r['vol_score'] * w['vol'] + r['risk_adj'] * w['risk'] +
        r['rel_strength'] * w['rel'] + r['trend_quality'] * w['trend'] +
        r.get('shrink_stability', 50) * w['shrink'], 2)
ranked = sorted(rankings, key=lambda x: x['total_new'], reverse=True)
old_rank = {r['code']: i+1 for i, r in enumerate(sorted(rankings, key=lambda x: x['total_score'], reverse=True))}
for i, r in enumerate(ranked):
    print(f"{i+1:>3}. {r['name']:<8} {r['code']:<8} {r['total_new']:>6.1f} {r['total_score']:>6.1f} "
          f"{r['day_chg']:>+7.2f} {r['mom_20d']:>+7.2f} {r['mom_cross_score']:>5.1f} {r['accel_pct']:>6.1f}")
