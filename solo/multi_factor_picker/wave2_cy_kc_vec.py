#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
双创板深度回调：创新低 vs 不创新低 胜率对比（NumPy向量化加速版）
只跑科创板+创业板样本（300只），快速出结果
"""
import os, sys, time, json, io
import tushare as ts
import numpy as np

for _l in open(r'D:\mystock\config\.env'):
    if _l.strip().startswith('TUSHARE_TOKEN='):
        TOKEN = _l.strip().split('=', 1)[1].strip().strip('"')
        break
pro = ts.pro_api(TOKEN)

OUT_DIR = r'D:\mystock\solo\multi_factor_picker\output'
os.makedirs(OUT_DIR, exist_ok=True)

_log_path = os.path.join(OUT_DIR, 'wave2_cy_kc_vec_log.txt')
_log_f = open(_log_path, 'w', encoding='utf-8', buffering=1)

def log(msg):
    sys.stdout.write(msg + '\n')
    _log_f.write(msg + '\n')
    _log_f.flush()

log('=' * 60)
log("双创板深度回调：创新低 vs 不创新低 胜率对比")
log("回测区间: 20240101 ~ 20260620")
log('=' * 60)

# ── 股票池：科创板688 + 创业板300 各取150只 ──────────────────
log("\n[Step1] 获取双创板股票池...")
sb = pro.stock_basic(exchange='', list_status='L', fields='ts_code')
cy = sb[sb['ts_code'].str.startswith(('300', '688'))]
# 各取150只
cy_sample = cy.iloc[:150]['ts_code'].tolist()
kc_sample = cy[cy['ts_code'].str.startswith('688')].iloc[:150]['ts_code'].tolist()
pool = list(set(cy_sample + kc_sample))
log(f"  样本池: {len(pool)} 只（科创板150 + 创业板150）")
time.sleep(0.1)

START = '20240101'
END   = '20260620'

signals = []

log(f"\n[Step2] 回测中（NumPy向量化）...")
t0 = time.time()

for i, ts_code in enumerate(pool):
    if i % 50 == 0:
        elapsed = time.time() - t0
        log(f"  进度: {i}/{len(pool)} ({elapsed:.0f}s)")
    
    try:
        # V2: 优先 daily_cache 表
        df = None
        try:
            from stock_cache import get_daily_cache, get_daily_cache_range, batch_insert_daily_cache
            _, max_date = get_daily_cache_range(ts_code)
            if max_date is not None and str(max_date) >= str(END):
                cached = get_daily_cache(ts_code, START, END)
                if cached is not None and not cached.empty:
                    cached['trade_date'] = cached['trade_date'].astype(str)
                    df = cached
        except Exception:
            pass
        if df is None:
            df = pro.daily(ts_code=ts_code, start_date=START, end_date=END)
            time.sleep(0.06)
            if df is not None and not df.empty:
                try:
                    batch_insert_daily_cache(df)
                except Exception:
                    pass
        if df is None or len(df) < 60:
            continue

        df = df.sort_values('trade_date').reset_index(drop=True)
        closes = df['close'].values.astype(np.float64)
        n = len(closes)
        
        # NumPy向量化：计算20日涨幅
        gain_20d = (closes[20:] - closes[:-20]) / closes[:-20]
        
        # 找一波拉升起点（20日涨幅≥20%）
        surge_starts = np.where(gain_20d >= 0.20)[0] + 20  # DataFrame索引
        
        if len(surge_starts) == 0:
            continue
        
        # 对每个一波起点，找一波终点（最大涨幅点）
        for s_idx in surge_starts[:3]:  # 每只股票最多取3个一波
            if s_idx >= n - 40:
                continue
            
            # 一波终点：s_idx 到 s_idx+30 之间的最大收盘价
            end_candidates = closes[s_idx:min(s_idx+30, n)]
            if len(end_candidates) == 0:
                continue
            wave1_end_rel = np.argmax(end_candidates)
            wave1_end_idx = s_idx + wave1_end_rel
            wave1_peak = closes[wave1_end_idx]
            
            # 一波启动前最低价（前20天）
            pre_low = closes[max(0, s_idx-20):s_idx+1].min()
            
            # 找深度回调（回调≥15%）
            for m in range(wave1_end_idx + 3, min(wave1_end_idx + 60, n)):
                pullback = (wave1_peak - closes[m]) / wave1_peak
                if pullback < 0.15:
                    continue
                
                # 调整期最低价
                adj_low = closes[wave1_end_idx:m+1].min()
                is_higher_low = adj_low > pre_low
                
                # 二波判断：入场后20天最大涨幅
                entry = closes[m]
                future = closes[m+1:min(m+21, n)]
                if len(future) == 0:
                    break
                max_g = (future - entry).max() / entry
                has_wave2 = 1 if max_g > 0.10 else 0
                
                signals.append({
                    'is_higher_low': is_higher_low,
                    'has_wave2': has_wave2,
                    'max_gain': max_g,
                    'pullback_pct': pullback,
                })
                break  # 每个一波只取第一个深度回调
        
    except Exception:
        continue

elapsed = time.time() - t0
log(f"\n[Step3] 统计结果（耗时 {elapsed:.0f}s）")
log(f"  总信号数: {len(signals)}")

higher = [s for s in signals if s['is_higher_low']]
lower   = [s for s in signals if not s['is_higher_low']]

log(f"\n  【不创新低深度回调】（调整低点 > 一波前低点）:")
log(f"    信号数: {len(higher)}")
if len(higher) > 0:
    wr = sum(s['has_wave2'] for s in higher) / len(higher) * 100
    avg = sum(s['max_gain'] for s in higher) / len(higher) * 100
    log(f"    二波胜率: {wr:.1f}%")
    log(f"    平均最大涨幅: {avg:.1f}%")

log(f"\n  【创新低深度回调】（调整低点 ≤ 一波前低点）:")
log(f"    信号数: {len(lower)}")
if len(lower) > 0:
    wr = sum(s['has_wave2'] for s in lower) / len(lower) * 100
    avg = sum(s['max_gain'] for s in lower) / len(lower) * 100
    log(f"    二波胜率: {wr:.1f}%")
    log(f"    平均最大涨幅: {avg:.1f}%")

# 按回调幅度分层
log(f"\n  【按回调幅度分层】")
for lo, hi, label in [(15,25,'15-25%'), (25,35,'25-35%'), (35,50,'35-50%'), (50,100,'50%+')]:
    subset = [s for s in signals if lo/100 <= s['pullback_pct'] < hi/100]
    if not subset:
        continue
    h = len([s for s in subset if s['is_higher_low']])
    l = len(subset) - h
    wr_h = sum(s['has_wave2'] for s in subset if s['is_higher_low']) / h * 100 if h > 0 else 0
    wr_l = sum(s['has_wave2'] for s in subset if not s['is_higher_low']) / l * 100 if l > 0 else 0
    log(f"    {label}: 共{len(subset)}只 | 不创新低{h}只(胜率{wr_h:.0f}%) | 创新低{l}只(胜率{wr_l:.0f}%)")

# 保存
result = {
    'total': len(signals),
    'elapsed_s': elapsed,
    'higher_low': {
        'count': len(higher),
        'win_rate': sum(s['has_wave2'] for s in higher) / len(higher) * 100 if higher else 0,
        'avg_gain': sum(s['max_gain'] for s in higher) / len(higher) * 100 if higher else 0,
    },
    'lower_low': {
        'count': len(lower),
        'win_rate': sum(s['has_wave2'] for s in lower) / len(lower) * 100 if lower else 0,
        'avg_gain': sum(s['max_gain'] for s in lower) / len(lower) * 100 if lower else 0,
    },
}
out_path = os.path.join(OUT_DIR, f'wave2_cy_kc_vec_{time.strftime("%Y%m%d_%H%M%S")}.json')
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

log(f"\n  结果已保存: {out_path}")
log(f"  日志文件: {_log_path}")
log('=' * 60)
_log_f.close()
