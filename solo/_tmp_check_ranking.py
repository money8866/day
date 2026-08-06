# -*- coding: utf-8 -*-
"""复现 20260806 ETF 多因子评分排名，检查 515030 为何排第一"""
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

# 加载全部缓存
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

print(f"加载 {len(all_data)} 只ETF缓存")

# 基准（沪深300缓存，若无则用假数据）
bm_path = os.path.join(CACHE_DIR, f"idx_000300_{TRADE_DATE}.csv")
benchmark_df = M._read_cache(bm_path)
if benchmark_df is not None:
    benchmark_df["trade_date"] = pd.to_datetime(benchmark_df["trade_date"], format="%Y%m%d")
    benchmark_df = benchmark_df.sort_values("trade_date").reset_index(drop=True)
    print("基准缓存存在")
else:
    # 用 512880 代替（仅用于对比）
    ref = all_data.get("512880")
    benchmark_df = ref.copy() if ref is not None else None
    print("基准缓存缺失，用512880代替")

# 市场状态
market_state, state_desc = M.classify_market_state(benchmark_df, M.MOM_PERIOD)
print(f"市场状态: {market_state} - {state_desc}")

WEIGHT_MATRIX = {
    'trending':   {'mom_cross': 0.30, 'accel': 0.08, 'vol': 0.25, 'risk': 0.12, 'rel': 0.12, 'trend': 0.13, 'shrink': 0.00},
    'oscillating':{'mom_cross': 0.12, 'accel': 0.25, 'vol': 0.15, 'risk': 0.10, 'rel': 0.10, 'trend': 0.08, 'shrink': 0.20},
    'declining':  {'mom_cross': 0.20, 'accel': 0.10, 'vol': 0.15, 'risk': 0.20, 'rel': 0.20, 'trend': 0.10, 'shrink': 0.05},
}
w = WEIGHT_MATRIX.get(market_state, WEIGHT_MATRIX['oscillating'])

code_to_name = {v: k for k, v in M.ETF_POOL.items()}
rankings = []
for code, df in all_data.items():
    factors = M.calculate_multi_factor_score(df, benchmark_df, M.MOM_PERIOD)
    if factors is None:
        print(f"[SKIP] {code} 数据不足")
        continue
    latest = df["close"].iloc[-1]
    prev = df["close"].iloc[-2] if len(df) >= 2 else latest
    day_chg = (latest - prev) / prev * 100
    rankings.append({"code": code, "name": code_to_name.get(code, code),
                     "close": latest, "day_chg": round(day_chg, 2), **factors})

valid = [r for r in rankings if r.get('mom_weighted') is not None]
n_total = len(valid)
sorted_by_mom = sorted(valid, key=lambda x: x['mom_weighted'])
for i, r in enumerate(sorted_by_mom):
    rank_pct = (i / (n_total - 1)) * 100
    r['mom_cross_score'] = round(rank_pct, 2)

for r in rankings:
    mom_cross = r.get('mom_cross_score') if r.get('mom_cross_score') is not None else 50
    total = (mom_cross * w['mom_cross'] + r['accel_score'] * w['accel'] +
             r['vol_score'] * w['vol'] + r['risk_adj'] * w['risk'] +
             r['rel_strength'] * w['rel'] + r['trend_quality'] * w['trend'] +
             r.get('shrink_stability', 50) * w['shrink'])
    r['total_score'] = round(total, 2)

rankings.sort(key=lambda x: x['total_score'], reverse=True)

print(f"\n=== 全排名（{market_state} 权重）===")
print(f"{'排名':>3} {'名称':<8} {'代码':<8} {'总评':>6} {'当日%':>7} {'mom20':>7} {'加权动量':>8} {'截面':>5} {'加速':>6} {'量价':>6} {'风险':>6} {'相对':>6} {'趋势':>6} {'缩量':>6}")
for i, r in enumerate(rankings):
    print(f"{i+1:>3}. {r['name']:<8} {r['code']:<8} {r['total_score']:>6.1f} {r['day_chg']:>+7.2f} "
          f"{r['momentum']:>+7.2f} {r['mom_weighted']:>8.2f} {r['mom_cross_score']:>5.1f} "
          f"{r['accel_score']:>6.1f} {r['vol_score']:>6.1f} {r['risk_adj']:>6.1f} "
          f"{r['rel_strength']:>6.1f} {r['trend_quality']:>6.1f} {r.get('shrink_stability', 0):>6.1f}")

# 515030 单独详情
r515 = next((r for r in rankings if r['code'] == '515030'), None)
if r515:
    print(f"\n=== 515030(新能源车) 因子详情 ===")
    for k, v in r515.items():
        print(f"  {k}: {v}")
