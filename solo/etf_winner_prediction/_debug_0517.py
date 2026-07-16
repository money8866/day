#!/usr/bin/env python
"""Debug: 20260517 feature comparison"""
import pandas as pd
import numpy as np
import yaml
import os
import sys
import pickle

sys.path.insert(0, r'D:\mystock\solo')
from etf_winner_prediction.lightgbm_predictor import FeatureBuilder
from etf_alpha_engine.data_loader import DataLoader

with open(r'D:\mystock\solo\etf_winner_prediction\config.yaml', 'r') as f:
    cfg = yaml.safe_load(f)

dl = DataLoader(cfg)
fb = FeatureBuilder(cfg)

model_dir = r'D:\mystock\solo\etf_winner_prediction\output\models'
import json
with open(os.path.join(model_dir, 'feature_names.json'), 'r') as f:
    feat_names = json.load(f)

codes = [
    ('518880.SH', 'Gold'),
    ('159516.SZ', 'SemiEquip'),
    ('512480.SH', 'Semi'),
    ('159755.SZ', 'Battery'),
]

print("=" * 90)
print("  20260517 Feature Comparison")
print("=" * 90)

results = []
for code, name in codes:
    df = dl.load_etf_data([code], '20250101', '20260517').get(code)
    if df is None or df.empty:
        print(f"{name}: No data")
        continue
    df = df.sort_values('trade_date').reset_index(drop=True)
    df = df[df['trade_date'] <= '20260517'].copy()

    close = df['close'].values.astype(float)
    high = df['high'].values.astype(float)
    low = df['low'].values.astype(float)
    vol = df['vol'].values.astype(float)
    amount = df['amount'].values.astype(float)
    pct_chg = df['pct_chg'].values.astype(float)

    feats = fb.build(close, high, low, vol, amount, pct_chg)
    results.append((name, feats))

    print(f"\n--- {name} ({code}) ---")
    # Show all momentum features
    for k in ['close', 'ret_5d', 'ret_20d', 'ret_60d',
              'dist_ema20', 'dist_ema60', 'ema20_slope', 'ema60_slope', 'ema_alignment',
              'momentum_accel',
              'new_high_20d', 'new_high_60d', 'up_ratio_20d', 'vol_price_corr_20d',
              'ma_spread', 'breakout_strength', 'breakout_60', 'price_position_60',
              'vol_20d', 'vol_60d', 'max_dd_20', 'max_dd_60', 'sharpe_60',
              'adx_14', 'rsi_14', 'hurst', 'amt_20d_avg', 'amt_trend']:
        if k in feats:
            print(f"  {k:25s}: {feats[k]:>10.4f}")

    X = pd.DataFrame([feats])[feat_names]
    for h in [20, 40, 60]:
        with open(os.path.join(model_dir, f'lgbm_{h}d.pkl'), 'rb') as f:
            m = pickle.load(f)
        p = m.predict(X)[0]
        print(f"  {h}D prediction           : {p*100:>10.1f}%")
    print()

# Compare key features
print("=" * 90)
print("  Key Feature Delta (SemiEquip vs Gold)")
print("=" * 90)
gold_feats = [r for r in results if r[0] == 'Gold'][0][1]
semi_feats = [r for r in results if r[0] == 'SemiEquip'][0][1]
print(f"{'Feature':25s} {'Gold':>10s} {'SemiEquip':>10s} {'Delta':>10s}")
print("-" * 60)
for k in ['ret_5d', 'ret_20d', 'ret_60d', 'ema_alignment', 'ema20_slope',
          'new_high_20d', 'new_high_60d', 'up_ratio_20d', 'vol_price_corr_20d',
          'ma_spread', 'breakout_strength', 'price_position_60',
          'vol_20d', 'vol_60d', 'max_dd_60', 'sharpe_60', 'adx_14', 'rsi_14',
          'amt_20d_avg', 'amt_trend']:
    if k in gold_feats and k in semi_feats:
        delta = semi_feats[k] - gold_feats[k]
        print(f"{k:25s} {gold_feats[k]:>10.4f} {semi_feats[k]:>10.4f} {delta:>+10.4f}")

# Show feature importance for 20D model
print("\n" + "=" * 90)
print("  20D Model - Feature Importance (Gain) Top 15")
print("=" * 90)
with open(os.path.join(model_dir, 'lgbm_20d.pkl'), 'rb') as f:
    m = pickle.load(f)
imp = m.booster_.feature_importance(importance_type='gain')
names = m.booster_.feature_name()
pairs = sorted(zip(names, imp), key=lambda x: -x[1])
for n, v in pairs[:15]:
    print(f"  {n:25s}: {v:>10.0f}")

# Check if momentum features have any importance
print("\n  Momentum features importance:")
for n, v in pairs:
    if n in ['new_high_20d', 'new_high_60d', 'up_ratio_20d', 'vol_price_corr_20d',
              'ma_spread', 'breakout_strength', 'momentum_accel', 'ema_alignment',
              'ema20_slope', 'ema60_slope']:
        rank = pairs.index((n, v)) + 1
        print(f"  #{rank:2d} {n:25s}: {v:>10.0f}")
