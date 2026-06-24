#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""只跑688科创板回测（接上次的进度）"""
import os, sys, time, json
import numpy as np
import tushare as ts

TOKEN = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
pro = ts.pro_api(TOKEN)

OUT = r'D:\mystock\solo\multi_factor_picker\output'
os.makedirs(OUT, exist_ok=True)

_log_path = os.path.join(OUT, 'wave2_688_only_log.txt')
_log_f = open(_log_path, 'w', encoding='utf-8', buffering=1)

def log(msg):
    sys.stdout.write(msg + '\n')
    _log_f.write(msg + '\n')
    _log_f.flush()

def divsafe(a, b, default=0.0):
    return a / b if b != 0 else default

log('=' * 70)
log('688科创板 单独回测')
log('=' * 70)

sb = pro.stock_basic(exchange='', list_status='L', fields='ts_code')
time.sleep(0.1)
pool_688 = [c for c in sb['ts_code'].tolist() if c.startswith('688')]
log(f'688池: {len(pool_688)} 只')

START = '20240101'
END   = '20260620'
WAVE2_MIN   = 0.10
WAVE2_WINDOW = 20
ALL_PATTERNS = ['强势横盘', '缩量回调', '深度回调', '放量回调', 'V型急跌']
ALL_CONDS    = ['rsi_lt30', 'ma20_above', 'ma60_above', 'vol_shrink']

t0 = time.time()
signals = []
parse_errors = 0

for i, ts_code in enumerate(pool_688):
    if i % 100 == 0:
        el  = time.time() - t0
        eta = divsafe(el, max(i,1)) * (len(pool_688) - i)
        log(f'  进度: {i}/{len(pool_688)} | {el:.0f}s | 剩余~{eta/60:.0f}min')

    try:
        df = pro.daily(ts_code=ts_code, start_date=START, end_date=END)
        time.sleep(0.06)
        if df is None or len(df) < 80:
            continue
        df = df.sort_values('trade_date').reset_index(drop=True)
        closes  = df['close'].values.astype(np.float64)
        vols    = df['vol'].values.astype(np.float64)
        n = len(closes)
        if n < 60:
            continue

        for s_idx in range(20, n - 40):
            gain_20d = (closes[s_idx] - closes[s_idx - 20]) / closes[s_idx - 20]
            if gain_20d < 0.20:
                continue

            end_win = closes[s_idx : min(s_idx + 30, n)]
            if len(end_win) == 0:
                continue
            wave1_end_rel = int(np.argmax(end_win))
            wave1_end_idx = s_idx + wave1_end_rel
            wave1_peak = closes[wave1_end_idx]

            pre_start = max(0, s_idx - 40)
            pre_low = closes[pre_start : s_idx + 1].min()

            adj_win = closes[wave1_end_idx + 1 : min(wave1_end_idx + 61, n)]
            if len(adj_win) < 5:
                continue
            adj_low_rel = int(np.argmin(adj_win))
            adj_low_idx = wave1_end_idx + 1 + adj_low_rel
            adj_low_price = closes[adj_low_idx]
            pullback_pct = (wave1_peak - adj_low_price) / wave1_peak
            adj_days = adj_low_idx - wave1_end_idx

            if pullback_pct < 0.10:
                continue

            surge_vol = vols[max(0, s_idx - 5) : s_idx + 5].mean()
            adj_vol   = vols[wave1_end_idx + 1 : adj_low_idx + 1].mean()
            vol_ratio  = adj_vol / surge_vol if surge_vol > 0 else 1.0

            ma20_ok = False
            ma60_ok = False
            if adj_low_idx >= 20:
                ma20_ok = adj_low_price > closes[adj_low_idx - 20 : adj_low_idx + 1].mean()
            if adj_low_idx >= 60:
                ma60_ok = adj_low_price > closes[adj_low_idx - 60 : adj_low_idx + 1].mean()

            rsi = 50.0
            if adj_low_idx >= 14:
                d = np.diff(closes[adj_low_idx - 14 : adj_low_idx + 1])
                g = float(np.where(d > 0, d, 0.0).sum()) / 14.0
                l = float(np.where(d < 0, -d, 0.0).sum()) / 14.0
                rs = g / l if l > 0 else 100.0
                rsi = 100.0 - 100.0 / (1.0 + rs)

            macd_val = 0.0
            if adj_low_idx >= 26:
                def ewm(arr, span):
                    a = 2.0 / (span + 1.0)
                    e = [arr[0]]
                    for v in arr[1:]:
                        e.append(a * v + (1.0 - a) * e[-1])
                    return np.array(e)
                e12 = ewm(closes[:adj_low_idx + 1], 12)
                e26 = ewm(closes[:adj_low_idx + 1], 26)
                macd_val = float((e12 - e26)[-1])

            if pullback_pct < 0.15 and adj_days <= 15:
                pattern = '强势横盘'
            elif 0.10 <= pullback_pct < 0.20 and vol_ratio < 0.7 and adj_days > 15:
                pattern = '缩量回调'
            elif pullback_pct >= 0.20:
                pattern = '深度回调'
            elif 0.10 <= pullback_pct < 0.25 and vol_ratio > 1.2:
                pattern = '放量回调'
            elif adj_days <= 10 and pullback_pct >= 0.15:
                pattern = 'V型急跌'
            else:
                continue

            is_higher_low = adj_low_price > pre_low

            entry_conds = []
            if rsi < 30:
                entry_conds.append('rsi_lt30')
            if macd_val > 0:
                entry_conds.append('macd_gold')
            if ma20_ok:
                entry_conds.append('ma20_above')
            if ma60_ok:
                entry_conds.append('ma60_above')
            if vol_ratio < 0.7:
                entry_conds.append('vol_shrink')
            if vol_ratio > 1.2:
                entry_conds.append('vol_explode')

            entry_price = adj_low_price
            has_wave2 = 0
            max_gain = 0.0
            for k in range(adj_low_idx + 1, min(adj_low_idx + WAVE2_WINDOW + 1, n)):
                g = (closes[k] - entry_price) / entry_price
                if g > max_gain:
                    max_gain = g
                if g >= WAVE2_MIN:
                    has_wave2 = 1
                    break

            signals.append({
                'ts_code': ts_code,
                'pattern': pattern,
                'is_higher_low': is_higher_low,
                'entry_conds': entry_conds,
                'has_wave2': has_wave2,
                'max_gain': max_gain,
                'pullback_pct': pullback_pct,
                'adj_days': adj_days,
                'rsi': rsi,
            })

    except Exception as e:
        parse_errors += 1
        continue

el = time.time() - t0
log(f'\n[688科创板] 完成！耗时 {el/60:.1f} min，信号 {len(signals)} 条，错误 {parse_errors} 次')

# ── 统计 ──────────────────────────────────────────
log('\n' + '=' * 70)
log('688科创板 统计结果')
log('=' * 70)

for p in ALL_PATTERNS:
    subset = [s for s in signals if s['pattern'] == p]
    if not subset:
        continue
    h  = [s for s in subset if s['is_higher_low']]
    lo = [s for s in subset if not s['is_higher_low']]
    wr_h  = divsafe(sum(s['has_wave2'] for s in h),  len(h))  * 100
    wr_lo = divsafe(sum(s['has_wave2'] for s in lo), len(lo)) * 100
    avg_h  = divsafe(sum(s['max_gain'] for s in h),  len(h))  * 100
    avg_lo = divsafe(sum(s['max_gain'] for s in lo), len(lo)) * 100
    log(f'\n  [{p}] 共{len(subset)}只')
    log(f'    不创新低: {len(h):4d}只 | 胜率{wr_h:.1f}% | 均涨{avg_h:.1f}%')
    if lo:
        log(f'    创新低:   {len(lo):4d}只 | 胜率{wr_lo:.1f}% | 均涨{avg_lo:.1f}%')

# 最佳组合
log('\n  --- 688科创板 最佳组合 TOP15 ---')
best = []
for p in ALL_PATTERNS:
    for cond in ALL_CONDS:
        subset = [s for s in signals if s['pattern'] == p and s['is_higher_low'] and cond in s['entry_conds']]
        if len(subset) >= 5:
            wr  = divsafe(sum(s['has_wave2'] for s in subset), len(subset)) * 100
            avg = divsafe(sum(s['max_gain'] for s in subset), len(subset)) * 100
            best.append((wr, avg, len(subset), p, cond))
best.sort(reverse=True)
for i, (wr, avg, cnt, p, cond) in enumerate(best[:15]):
    log(f'    {i+1:2d}. 胜率{wr:.1f}% 均涨{avg:.1f}% 样本{cnt:4d} | {p} x {cond}')

# 保存
result = {'688科创板': {'total': len(signals), 'patterns': {}}}
for p in ALL_PATTERNS:
    subset = [s for s in signals if s['pattern'] == p]
    if not subset:
        continue
    h  = [s for s in subset if s['is_higher_low']]
    lo = [s for s in subset if not s['is_higher_low']]
    result['688科创板']['patterns'][p] = {
        'total': len(subset),
        'higher_low': {'count': len(h), 'win_rate': round(divsafe(sum(s['has_wave2'] for s in h), max(len(h),1)) * 100, 1)},
        'lower_low':  {'count': len(lo),'win_rate': round(divsafe(sum(s['has_wave2'] for s in lo),max(len(lo),1)) * 100, 1)},
    }

out_path = os.path.join(OUT, f'wave2_688_only_{time.strftime("%Y%m%d_%H%M%S")}.json')
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

log(f'\n结果已保存: {out_path}')
log('=' * 70)
_log_f.close()
print('688_DONE')
