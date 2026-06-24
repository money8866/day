#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
双创板深度回调回测：创新低 vs 不创新低 胜率对比
股票池：300/688/301 开头，共约1500只
回测区间：2024-01-01 ~ 2026-06-20
"""
import os, sys, time, json
import tushare as ts
import numpy as np

TOKEN = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
pro = ts.pro_api(TOKEN)

OUT_DIR = r'D:\mystock\solo\multi_factor_picker\output'
os.makedirs(OUT_DIR, exist_ok=True)

# 日志双写
_log_path = os.path.join(OUT_DIR, 'wave2_cy_kc_hl_log.txt')
_log_f = open(_log_path, 'w', encoding='utf-8', buffering=1)
_orig_stdout = sys.stdout
class DualOut:
    def write(self, s):
        try: _orig_stdout.write(s)
        except: pass
        _log_f.write(s); _log_f.flush()
    def flush(self):
        try: _orig_stdout.flush()
        except: pass
        _log_f.flush()
sys.stdout = DualOut()

print(f"{'='*60}")
print("双创板深度回调：创新低 vs 不创新低 胜率对比")
print(f"回测区间: 20240101 ~ 20260620")
print(f"{'='*60}\n")

# ── 获取双创板股票池 ─────────────────────────────
print("[Step1] 获取双创板股票池...")
sb = pro.stock_basic(exchange='', list_status='L', fields='ts_code')
cy_kc = sb[sb['ts_code'].str.startswith(('300', '688', '301'))]
pool = cy_kc['ts_code'].tolist()
print(f"  双创板: {len(pool)} 只")
time.sleep(0.1)

# ── 回测参数 ────────────────────────────────────
START = '20240101'
END   = '20260620'
WAVE1_MIN = 0.20   # 一波最低涨幅20%
WAVE2_MIN = 0.10   # 二波最低涨幅10%
WAVE2_WINDOW = 20   # 二波确认窗口20天

signals = []  # {ts_code, pattern, is_higher_low, has_wave2, max_gain, pullback_pct, wave1_gain}

print(f"\n[Step2] 回测中...")
for i, ts_code in enumerate(pool):
    if i % 100 == 0:
        print(f"  进度: {i}/{len(pool)}")
    
    try:
        # 获取 stk_factor_pro（含前复权价）
        df = pro.stk_factor_pro(ts_code=ts_code, start_date=START, end_date=END)
        time.sleep(0.06)
        if df is None or len(df) < 60:
            continue
        
        df = df.sort_values('trade_date').reset_index(drop=True)
        closes = df['close_qfq'].values  # 前复权收盘价
        
        # 找一波拉升（20日涨幅≥20%）
        found = False
        for j in range(20, len(closes) - WAVE2_WINDOW - 20):
            gain_20d = (closes[j] - closes[j-20]) / closes[j-20]
            if gain_20d < WAVE1_MIN:
                continue
            
            # 找到一波终点（最大涨幅点）
            wave1_start_idx = j - 20
            wave1_end_idx = j
            wave1_peak = closes[j]
            
            for k in range(j, min(j+30, len(closes))):
                if closes[k] > wave1_peak:
                    wave1_peak = closes[k]
                    wave1_end_idx = k
            
            # 一波启动前最低价（启动前10天）
            pre_wave_low = closes[max(0, wave1_start_idx-10):wave1_start_idx+1].min()
            
            # 找深度回调（回调≥15%）
            for m in range(wave1_end_idx + 3, min(wave1_end_idx + 60, len(closes))):
                pullback = (wave1_peak - closes[m]) / wave1_peak
                if pullback < 0.15:
                    continue
                
                # 深度回调确认
                adjust_low_price = closes[m]
                adjust_low_idx = m
                
                # 判断创新低：调整期最低价 ≤ 一波启动前最低价
                # 调整期最低价
                adjust_min = closes[wave1_end_idx:m+1].min()
                is_higher_low = adjust_min > pre_wave_low  # True=不创新低, False=创新低
                
                # 二波判断：入场后20天最大涨幅
                entry_price = closes[m]
                has_wave2 = 0
                max_gain = 0
                for n in range(m+1, min(m+WAVE2_WINDOW+1, len(closes))):
                    g = (closes[n] - entry_price) / entry_price
                    if g > max_gain:
                        max_gain = g
                    if g > WAVE2_MIN:
                        has_wave2 = 1
                        break
                
                signals.append({
                    'ts_code': ts_code,
                    'is_higher_low': is_higher_low,
                    'has_wave2': has_wave2,
                    'max_gain': max_gain,
                    'pullback_pct': pullback,
                    'wave1_gain': (wave1_peak - closes[wave1_start_idx]) / closes[wave1_start_idx],
                })
                
                found = True
                break  # 每只股票每个一波只取第一个深度回调
            
            if found:
                break  # 找到一波就跳出
        
    except Exception as e:
        continue

print(f"\n[Step3] 统计结果")
print(f"  总信号数: {len(signals)}")

higher = [s for s in signals if s['is_higher_low']]
lower   = [s for s in signals if not s['is_higher_low']]

print(f"\n  【不创新低深度回调】（调整低点 > 一波前低点）:")
print(f"    信号数: {len(higher)}")
if len(higher) > 0:
    wr = sum(s['has_wave2'] for s in higher) / len(higher) * 100
    avg_g = sum(s['max_gain'] for s in higher) / len(higher) * 100
    print(f"    二波胜率: {wr:.1f}%")
    print(f"    平均最大涨幅: {avg_g:.1f}%")

print(f"\n  【创新低深度回调】（调整低点 ≤ 一波前低点）:")
print(f"    信号数: {len(lower)}")
if len(lower) > 0:
    wr = sum(s['has_wave2'] for s in lower) / len(lower) * 100
    avg_g = sum(s['max_gain'] for s in lower) / len(lower) * 100
    print(f"    二波胜率: {wr:.1f}%")
    print(f"    平均最大涨幅: {avg_g:.1f}%")

# 按回调幅度分层
print(f"\n  【按回调幅度分层】")
tiers = [(15, 25, '15-25%'), (25, 35, '25-35%'), (35, 50, '35-50%'), (50, 100, '50%+')]
for lo, hi, label in tiers:
    subset = [s for s in signals if lo <= s['pullback_pct']*100 < hi]
    if len(subset) == 0:
        continue
    h = [s for s in subset if s['is_higher_low']]
    l = [s for s in subset if not s['is_higher_low']]
    wr_h = sum(s['has_wave2'] for s in h) / len(h) * 100 if len(h) > 0 else 0
    wr_l = sum(s['has_wave2'] for s in l) / len(l) * 100 if len(l) > 0 else 0
    print(f"    {label}: 共{len(subset)}只 | 不创新低{len(h)}只(胜率{wr_h:.0f}%) | 创新低{len(l)}只(胜率{wr_l:.0f}%)")

# 保存结果
result = {
    'total': len(signals),
    'higher_low': {
        'count': len(higher),
        'win_rate': sum(s['has_wave2'] for s in higher) / len(higher) * 100 if len(higher) > 0 else 0,
        'avg_gain': sum(s['max_gain'] for s in higher) / len(higher) * 100 if len(higher) > 0 else 0,
    },
    'lower_low': {
        'count': len(lower),
        'win_rate': sum(s['has_wave2'] for s in lower) / len(lower) * 100 if len(lower) > 0 else 0,
        'avg_gain': sum(s['max_gain'] for s in lower) / len(lower) * 100 if len(lower) > 0 else 0,
    },
}
out_path = os.path.join(OUT_DIR, f'wave2_cy_kc_hl_compare_{time.strftime("%Y%m%d_%H%M%S")}.json')
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print(f"\n  结果已保存: {out_path}")
print(f"  日志文件: {_log_path}")
print(f"{'='*60}")
