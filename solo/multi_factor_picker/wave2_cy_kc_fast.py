#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
双创板深度回调：创新低 vs 不创新低 胜率对比（精简版）
只用 daily 不复权价，速度优先
"""
import os, sys, time, json
import tushare as ts
import numpy as np

for _l in open(r'D:\mystock\config\.env'):
    if _l.strip().startswith('TUSHARE_TOKEN='):
        TOKEN = _l.strip().split('=', 1)[1].strip().strip('"')
        break
pro = ts.pro_api(TOKEN)

OUT_DIR = r'D:\mystock\solo\multi_factor_picker\output'
os.makedirs(OUT_DIR, exist_ok=True)

_log_path = os.path.join(OUT_DIR, 'wave2_cy_kc_fast_log.txt')
_log_f = open(_log_path, 'w', encoding='utf-8', buffering=1)

def log(msg):
    sys.stdout.write(msg + '\n')
    _log_f.write(msg + '\n')
    _log_f.flush()

log('=' * 60)
log("双创板深度回调：创新低 vs 不创新低 胜率对比（精简版）")
log("回测区间: 20240101 ~ 20260620")
log('=' * 60)

# ── 股票池 ─────────────────────────────────────
log("\n[Step1] 获取双创板股票池...")
sb = pro.stock_basic(exchange='', list_status='L', fields='ts_code')
cy_kc = sb[sb['ts_code'].str.startswith(('300', '688', '301'))]
pool = cy_kc['ts_code'].tolist()
log(f"  双创板: {len(pool)} 只")
time.sleep(0.1)

START = '20240101'
END   = '20260620'

signals = []  # (is_higher_low, has_wave2, pullback_pct)

log(f"\n[Step2] 回测中...")
for i, ts_code in enumerate(pool):
    if i % 200 == 0:
        log(f"  进度: {i}/{len(pool)}")
    
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
        closes = df['close'].values  # 不复权价（简化）
        
        # 找一波拉升（20日涨幅≥20%）
        found = False
        for j in range(20, len(closes) - 40):
            gain_20d = (closes[j] - closes[j-20]) / closes[j-20]
            if gain_20d < 0.20:
                continue
            
            # 一波起点/终点
            wave1_start_idx = j - 20
            wave1_end_idx = j
            wave1_peak = closes[j]
            
            for k in range(j, min(j+30, len(closes))):
                if closes[k] > wave1_peak:
                    wave1_peak = closes[k]
                    wave1_end_idx = k
            
            # 一波启动前最低价（前20天）
            pre_low = closes[max(0, wave1_start_idx-20):wave1_start_idx+1].min()
            
            # 找深度回调（回调≥15%）
            for m in range(wave1_end_idx + 3, min(wave1_end_idx + 60, len(closes))):
                pullback = (wave1_peak - closes[m]) / wave1_peak
                if pullback < 0.15:
                    continue
                
                # 调整期最低价
                adj_low = closes[wave1_end_idx:m+1].min()
                is_higher_low = adj_low > pre_low  # True=不创新低
                
                # 二波判断：入场后20天最大涨幅
                entry = closes[m]
                has_wave2 = 0
                max_g = 0
                for n in range(m+1, min(m+21, len(closes))):
                    g = (closes[n] - entry) / entry
                    if g > max_g:
                        max_g = g
                    if g > 0.10:
                        has_wave2 = 1
                        break
                
                signals.append({
                    'is_higher_low': is_higher_low,
                    'has_wave2': has_wave2,
                    'max_gain': max_g,
                    'pullback_pct': pullback,
                })
                
                found = True
                break
            
            if found:
                break
        
    except Exception:
        continue

log(f"\n[Step3] 统计结果")
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
    h = [s for s in subset if s['is_higher_low']]
    l = [s for s in subset if not s['is_higher_low']]
    wr_h = sum(s['has_wave2'] for s in h) / len(h) * 100 if h else 0
    wr_l = sum(s['has_wave2'] for s in l) / len(l) * 100 if l else 0
    log(f"    {label}: 共{len(subset)}只 | 不创新低{len(h)}只(胜率{wr_h:.0f}%) | 创新低{len(l)}只(胜率{wr_l:.0f}%)")

# 保存
result = {
    'total': len(signals),
    'higher_low': {'count': len(higher), 'win_rate': sum(s['has_wave2'] for s in higher)/len(higher)*100 if higher else 0, 'avg_gain': sum(s['max_gain'] for s in higher)/len(higher)*100 if higher else 0},
    'lower_low': {'count': len(lower), 'win_rate': sum(s['has_wave2'] for s in lower)/len(lower)*100 if lower else 0, 'avg_gain': sum(s['max_gain'] for s in lower)/len(lower)*100 if lower else 0},
}
out_path = os.path.join(OUT_DIR, f'wave2_cy_kc_fast_{time.strftime("%Y%m%d_%H%M%S")}.json')
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

log(f"\n  结果已保存: {out_path}")
log(f"  日志文件: {_log_path}")
log('=' * 60)
_log_f.close()
