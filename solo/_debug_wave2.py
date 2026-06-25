# -*- coding: utf-8 -*-
"""调试光电股份和广合科技的二波检测"""
import os, sys
sys.path.insert(0, r'D:\mystock')
os.environ.setdefault('TUSHARE_TOKEN', '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')

import tushare as ts
import pandas as pd
import numpy as np

ts.set_token(os.environ['TUSHARE_TOKEN'])
pro = ts.pro_api()

# 导入tushare_quant的函数
os.chdir(r'D:\mystock\solo')
from dotenv import load_dotenv
load_dotenv("d:/mystock/config/.env")

# 需要先设置TRADE_DATE
os.environ['TRADE_DATE'] = '20260624'

# 直接导入
import importlib.util
spec = importlib.util.spec_from_file_location("tushare_quant", "d:/mystock/solo/tushare_quant.py")
tq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tq)

TRADE_DATE = '20260624'

for ts_code in ['600184.SH', '001389.SZ']:
    print(f"\n{'='*60}")
    print(f"调试: {ts_code}")
    print(f"{'='*60}")

    # 0. 先验证 classify_wave2_pattern 是否可导入
    print(f"\n[导入检查]")
    try:
        from tushare_quant import classify_wave2_pattern as cwp_test
        print(f"  from tushare_quant import classify_wave2_pattern: 成功")
    except Exception as e:
        print(f"  from tushare_quant import classify_wave2_pattern: 失败 - {e}")
    try:
        cwp_test2 = tq.classify_wave2_pattern
        print(f"  tq.classify_wave2_pattern: 成功")
    except Exception as e:
        print(f"  tq.classify_wave2_pattern: 失败 - {e}")

    # 1. 检测二波（带异常捕获）
    print(f"\n[detect_wave2_reversal 调用]")
    try:
        result = tq.detect_wave2_reversal(ts_code, pro, trade_date=TRADE_DATE)
        print(f"  调用成功")
        print(f"  wave2_score: {result.get('wave2_score', 0)}")
        print(f"  pattern: {result.get('pattern', '')!r}")
        print(f"  signal: {result.get('signal', '')}")
        print(f"  is_perfect: {result.get('is_perfect_signal', False)}")
        print(f"  score_details: {result.get('score_details', '')}")
        print(f"  entry_price: {result.get('entry_price', 0)}")
        print(f"  stop_loss: {result.get('stop_loss', 0)}")
        print(f"  target: {result.get('target', 0)}")
    except Exception as e:
        import traceback
        print(f"  调用异常: {e}")
        traceback.print_exc()
    
    # 2. 手动加载数据检查三均线
    try:
        start_date = (pd.Timestamp.now() - pd.Timedelta(days=400)).strftime('%Y%m%d')
        df = tq.cached_stk_factor_pro(ts_code, start_date, TRADE_DATE)
        if df is not None and not df.empty:
            df['trade_date'] = df['trade_date'].astype(str)
            df = df.sort_values('trade_date').reset_index(drop=True)
            df = df.copy()
            if 'close_qfq' in df.columns:
                df['close'] = df['close_qfq']
            df['ma60'] = df['close'].rolling(60, min_periods=30).mean()
            df['ma120'] = df['close'].rolling(120, min_periods=60).mean()
            df['ma250'] = df['close'].rolling(250, min_periods=120).mean()
            latest = df.iloc[-1]
            close = float(latest['close'])
            ma60 = float(latest.get('ma60', 0)) if not pd.isna(latest.get('ma60', np.nan)) else 0
            ma120 = float(latest.get('ma120', 0)) if not pd.isna(latest.get('ma120', np.nan)) else 0
            ma250 = float(latest.get('ma250', 0)) if not pd.isna(latest.get('ma250', np.nan)) else 0
            above_ma60 = close > ma60 if ma60 > 0 else False
            above_ma120 = close > ma120 if ma120 > 0 else False
            above_ma250 = close > ma250 if ma250 > 0 else False
            print(f"\n  三均线检查:")
            print(f"    close={close:.2f} ma60={ma60:.2f} ma120={ma120:.2f} ma250={ma250:.2f}")
            print(f"    above_ma60={above_ma60} above_ma120={above_ma120} above_ma250={above_ma250}")
            print(f"    三均线支撑={'通过' if (above_ma60 and above_ma120 and above_ma250) else '不通过'}")
    except Exception as e:
        print(f"  数据加载失败: {e}")
    
    # 3. 检查wave1检测
    try:
        start_date = (pd.Timestamp.now() - pd.Timedelta(days=400)).strftime('%Y%m%d')
        df = tq.cached_stk_factor_pro(ts_code, start_date, TRADE_DATE)
        if df is not None and not df.empty:
            df['trade_date'] = df['trade_date'].astype(str)
            df = df.sort_values('trade_date').reset_index(drop=True)
            df = df.copy()
            if 'close_qfq' in df.columns:
                df['close'] = df['close_qfq']
            
            closes = df['close'].values
            n = len(df)
            SURGE_DAYS = 20
            SURGE_MIN = 0.20
            
            # 找wave1候选
            candidates = []
            for lookback in range(3, min(150, n - SURGE_DAYS - 5)):
                end_idx = n - lookback
                if end_idx < SURGE_DAYS:
                    continue
                window = closes[end_idx - SURGE_DAYS:end_idx + 1]
                low_in_win = np.argmin(window)
                high_in_win = np.argmax(window)
                if high_in_win <= low_in_win:
                    continue
                if (high_in_win - low_in_win) > SURGE_DAYS - 2:
                    continue
                surge_gain = (window[high_in_win] - window[low_in_win]) / window[low_in_win]
                if surge_gain < SURGE_MIN:
                    continue
                wave1_high_idx = end_idx - SURGE_DAYS + high_in_win
                if not any(h == wave1_high_idx for h, *_ in candidates):
                    candidates.append((wave1_high_idx, surge_gain))
            
            print(f"\n  wave1候选: {len(candidates)}个")
            for i, (idx, gain) in enumerate(candidates[:5]):
                w1_high = closes[idx]
                post_high = closes[idx:]
                low_after = post_high.min()
                pullback = (w1_high - low_after) / w1_high
                adj_days = int(np.argmin(post_high))
                print(f"    候选{i+1}: idx={idx} date={df.iloc[idx]['trade_date']} gain={gain*100:.1f}% pullback={pullback*100:.1f}% adj_days={adj_days}")
                
                # 创新低检测
                wave1_start_idx = max(0, idx - 20)
                pre_low_start = max(0, wave1_start_idx - 20)
                if idx >= 40:
                    pre_low = closes[pre_low_start:wave1_start_idx+1].min()
                else:
                    pre_low = closes[0:idx+1].min()
                entry_idx = idx + adj_days
                adj_low = closes[idx:entry_idx+1].min()
                is_higher_low = adj_low > pre_low
                print(f"      pre_low={pre_low:.2f} adj_low={adj_low:.2f} is_higher_low={is_higher_low}")
    except Exception as e:
        print(f"  wave1分析失败: {e}")
