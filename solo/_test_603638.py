import sys, os
sys.path.insert(0, r'D:\mystock\solo')
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')
os.chdir(r'D:\mystock\solo')
if 'TUSHARE_TOKEN' not in os.environ:
    for _l in open(r'D:\mystock\config\.env'):
        if _l.strip().startswith('TUSHARE_TOKEN='):
            os.environ['TUSHARE_TOKEN'] = _l.strip().split('=', 1)[1].strip().strip('"')
            break
os.environ['TS_TOKEN'] = os.environ['TUSHARE_TOKEN']

import warnings
warnings.filterwarnings('ignore')
import numpy as np
import pandas as pd

# 直接使用 WavePatternDetector 的 load_data（内部调用缓存）
from wave2_pattern_scanner import WavePatternDetector

code = '603638.SH'

# 模拟6/25的数据状态（WavePatternDetector force_date='20260625'）
detector = WavePatternDetector(force_date='20260625')
df = detector.load_data(code, lookback=80)

if df is not None:
    print(f"数据行数: {len(df)}, 最新日期: {df.iloc[-1]['trade_date']}")
    print(df[['trade_date','close','vol','pct_chg']].tail(8).to_string())
    
    closes = df['close'].values
    n = len(closes)
    
    # === 旧版一波检测（tushare_quant._detect_wave2_reversal_old）===
    SURGE_MIN = 0.20
    SURGE_DAYS_MIN = 7
    SURGE_DAYS_MAX = 21
    
    old_candidates = []
    for i in range(min(n - 30, n - 3), 30, -1):
        for wave1_len in range(SURGE_DAYS_MIN, SURGE_DAYS_MAX + 1):
            ws = i - wave1_len
            if ws < 0:
                break
            window = closes[ws:i + 1]
            lo_i = np.argmin(window)
            hi_i = np.argmax(window)
            if hi_i <= lo_i:
                continue
            if (hi_i - lo_i) > wave1_len - 2:
                continue
            gain = (window[hi_i] - window[lo_i]) / window[lo_i]
            if gain < SURGE_MIN:
                continue
            old_candidates.append((ws + hi_i, ws + lo_i, gain))
            break
        if old_candidates:
            break
    
    print(f"\n旧版一波候选: {len(old_candidates)} 个")
    if old_candidates:
        h, lo, g = old_candidates[0]
        post = closes[h:]
        pullback = (closes[h] - closes[-1]) / closes[h]
        adj_days = n - 1 - h
        print(f"  idx={h}, 日期={df.iloc[h]['trade_date']}, 涨幅={g*100:.1f}%, 回调={pullback*100:.1f}%, 调整={adj_days}天")
    
    # === 新版一波检测（WavePatternDetector._find_recent_wave1）===
    SURGE_DAYS = 20
    new_candidates = []
    for lookback in range(3, min(150, n - SURGE_DAYS - 5)):
        end_idx = n - lookback
        if end_idx < SURGE_DAYS:
            continue
        window = closes[end_idx - SURGE_DAYS:end_idx + 1]
        lo_i = np.argmin(window)
        hi_i = np.argmax(window)
        if hi_i <= lo_i:
            continue
        if (hi_i - lo_i) > SURGE_DAYS - 2:
            continue
        gain = (window[hi_i] - window[lo_i]) / window[lo_i]
        if gain < SURGE_MIN:
            continue
        wave1_high_idx = end_idx - SURGE_DAYS + hi_i
        wave1_low_idx = end_idx - SURGE_DAYS + lo_i
        # 宏观过滤
        ls = max(0, wave1_low_idx - 200)
        pre = closes[ls:wave1_low_idx]
        if len(pre) >= 20 and pre.max() > closes[wave1_high_idx] * 1.15:
            continue
        new_candidates.append((wave1_high_idx, wave1_low_idx, gain))
    new_candidates.sort(key=lambda x: (n - x[0]))
    
    print(f"\n新版一波候选: {len(new_candidates)} 个")
    for i, (h, lo, g) in enumerate(new_candidates[:3]):
        post = closes[h:]
        pullback = (closes[h] - closes[-1]) / closes[h]
        adj_days = n - 1 - h
        print(f"  候选{i+1}: idx={h}, 日期={df.iloc[h]['trade_date']}, 涨幅={g*100:.1f}%, 回调={pullback*100:.1f}%, 调整={adj_days}天")
else:
    print("数据加载失败")
