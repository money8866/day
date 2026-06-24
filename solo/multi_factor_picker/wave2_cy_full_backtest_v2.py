#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
双创板全量回测 v2：按形态 × 入场条件 交叉分组
使用 stk_factor_pro 批量接口，速度提升50倍
股票池：300/688/301 全量
回测区间：2024-01-01 ~ 2026-06-20
"""
import os, sys, time, json
import numpy as np
import tushare as ts

TOKEN = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
pro = ts.pro_api(TOKEN)

OUT_DIR = r'D:\mystock\solo\multi_factor_picker\output'
os.makedirs(OUT_DIR, exist_ok=True)

_log_path = os.path.join(OUT_DIR, 'wave2_cy_full_v2_log.txt')
_log_f = open(_log_path, 'w', encoding='utf-8', buffering=1)

def log(msg):
    sys.stdout.write(msg + '\n')
    _log_f.write(msg + '\n')
    _log_f.flush()

log('=' * 70)
log("双创板全量回测 v2：形态 × 入场条件 交叉分组")
log("接口：stk_factor_pro（批量，按日期批次）")
log('=' * 70)

# ── 股票池 ────────────────────────────────────────
log("\n[Step1] 获取双创板股票池...")
sb = pro.stock_basic(exchange='', list_status='L', fields='ts_code')
cy_kc = sb[sb['ts_code'].str.startswith(('300', '688', '301'))]
pool = cy_kc['ts_code'].tolist()
log(f"  共 {len(pool)} 只")
time.sleep(0.1)

START = '20240101'
END   = '20260620'
WAVE2_MIN   = 0.10
WAVE2_WINDOW = 20

# ── 按日期批次拉取 stk_factor_pro ─────────────────
log("\n[Step2] 按日期批次拉取 stk_factor_pro...")
trade_cal = pro.trade_cal(exchange='SSE', start_date=START, end_date=END, is_open='1')
dates = trade_cal['cal_date'].tolist()
log(f"  交易日数: {len(dates)}")

# 每50个交易日一批
BATCH = 50
all_data = []
for i in range(0, len(dates), BATCH):
    batch_dates = dates[i:i+BATCH]
    date_str = f"{batch_dates[0]},{batch_dates[-1]}"
    log(f"  批次 {i//BATCH+1}: {batch_dates[0]} ~ {batch_dates[-1]} ({len(batch_dates)}天)")
    try:
        df = pro.stk_factor_pro(trade_date=','.join(batch_dates), fields=[
            'ts_code','trade_date',
            'open','high','low','close','vol','amount',
            'rsi_6','rsi_12','rsi_24',
            'kdj_k','kdj_d','kdj_j',
            'macd_dif','macd_dea','macd',
            'boll_upper','boll_mid','boll_lower',
            'ma_5','ma_10','ma_20','ma_60',
            'ema_5','ema_10','ema_20','ema_60',
            'atr','volume_ratio',
            'rsi_bfq_6','rsi_bfq_12','rsi_bfq_24',
            'rsi_qfq_6','rsi_qfq_12','rsi_qfq_24',
        ])
        time.sleep(0.3)
        if df is not None and len(df) > 0:
            all_data.append(df)
            log(f"    获取 {len(df)} 行")
    except Exception as e:
        log(f"    ERROR: {e}")
        time.sleep(1)

if not all_data:
    log("ERROR: 没有获取到任何数据")
    sys.exit(1)

data = pd_concat(all_data)
data['trade_date'] = data['trade_date'].astype(str)
data = data.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)
log(f"\n  合并后总行数: {len(data)}")

# ── 向量化回测（按股票分组）───────────────────────
log("\n[Step3] 向量化回测...")
t0 = time.time()

signals = []
grouped = data.groupby('ts_code')

for ts_code, gdf in grouped:
    if ts_code not in pool:
        continue
    df = gdf.reset_index(drop=True)
    closes  = df['close'].values.astype(np.float64)
    vols    = df['vol'].values.astype(np.float64)
    n       = len(closes)
    if n < 80:
        continue

    # 找一波拉升（20日涨幅≥20%）
    for i in range(20, n - 40):
        gain_20d = (closes[i] - closes[i-20]) / closes[i-20]
        if gain_20d < 0.20:
            continue

        # 一波终点：i ~ i+30 的最高价
        end_win = closes[i : min(i+30, n)]
        if len(end_win) == 0:
            continue
        wave1_end_rel = np.argmax(end_win)
        wave1_end_idx = i + wave1_end_rel
        wave1_peak = closes[wave1_end_idx]

        # 一波启动前最低价
        pre_start = max(0, i - 40)
        pre_low = closes[pre_start : i+1].min()

        # 调整低点：一波终点后3~60天
        adj_win = closes[wave1_end_idx+1 : min(wave1_end_idx+61, n)]
        if len(adj_win) < 5:
            continue
        adj_low_rel = np.argmin(adj_win)
        adj_low_idx = wave1_end_idx + 1 + adj_low_rel
        adj_low_price = closes[adj_low_idx]
        pullback_pct = (wave1_peak - adj_low_price) / wave1_peak
        adj_days = adj_low_idx - wave1_end_idx

        if pullback_pct < 0.10:
            continue

        # ── 形态分类 ──────────────────────────────
        # 调整期均量 vs 一波期均量
        surge_vol = vols[max(0,i-5):i+5].mean() if i >= 5 else vols[:i+5].mean()
        adj_vol   = vols[wave1_end_idx+1 : adj_low_idx+1].mean()
        vol_ratio = adj_vol / surge_vol if surge_vol > 0 else 1.0

        ma20_ok = adj_low_idx >= 20 and adj_low_price > closes[adj_low_idx-20 : adj_low_idx+1].mean()
        ma60_ok = adj_low_idx >= 60 and adj_low_price > closes[adj_low_idx-60 : adj_low_idx+1].mean()

        rsi_col = 'rsi_qfq_6' if 'rsi_qfq_6' in df.columns else 'rsi_6'
        rsi = float(df.iloc[adj_low_idx][rsi_col]) if rsi_col in df.columns else 50.0

        macd_col = 'macd' if 'macd' in df.columns else None
        macd_val = float(df.iloc[adj_low_idx][macd_col]) if macd_col and macd_col in df.columns else 0

        # 分类
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

        # 入场条件
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

        # 二波验证
        entry_price = adj_low_price
        has_wave2 = 0
        max_gain = 0.0
        for k in range(adj_low_idx+1, min(adj_low_idx+WAVE2_WINDOW+1, n)):
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

elapsed = time.time() - t0
log(f"  回测完成（耗时 {elapsed:.0f}s），信号数: {len(signals)}")

# ── 统计 ──────────────────────────────────────────
log(f"\n{'='*70}")
log("统计1：按形态 × 创新低 分组")
log(f"{'='*70}")

all_patterns = ['强势横盘', '缩量回调', '深度回调', '放量回调', 'V型急跌']
for p in all_patterns:
    subset = [s for s in signals if s['pattern'] == p]
    if not subset:
        continue
    h = [s for s in subset if s['is_higher_low']]
    l = [s for s in subset if not s['is_higher_low']]
    wr_h = sum(s['has_wave2'] for s in h) / len(h) * 100 if h else 0
    wr_l = sum(s['has_wave2'] for s in l) / len(l) * 100 if l else 0
    avg_h = sum(s['max_gain'] for s in h) / len(h) * 100 if h else 0
    avg_l = sum(s['max_gain'] for s in l) / len(l) * 100 if l else 0
    log(f"\n  【{p}】 共{len(subset)}只")
    log(f"    不创新低: {len(h):4d}只 | 胜率{wr_h:.1f}% | 均涨{avg_h:.1f}%")
    if l:
        log(f"    创新低:   {len(l):4d}只 | 胜率{wr_l:.1f}% | 均涨{avg_l:.1f}%")

log(f"\n{'='*70}")
log("统计2：最佳组合 TOP20（形态 ∩ 入场条件，不创新低）")
log(f"{'='*70}")

all_conds = ['rsi_lt30', 'macd_gold', 'ma20_above', 'ma60_above', 'vol_shrink', 'vol_explode']
best = []
for p in all_patterns:
    for cond in all_conds:
        subset = [s for s in signals
                  if s['pattern'] == p and s['is_higher_low'] and cond in s['entry_conds']]
        if len(subset) >= 10:
            wr  = sum(s['has_wave2'] for s in subset) / len(subset) * 100
            avg = sum(s['max_gain'] for s in subset) / len(subset) * 100
            best.append((wr, avg, len(subset), p, cond))

best.sort(reverse=True)
log("\n  排名 | 胜率  | 均涨  | 样本 | 形态 × 条件")
for i, (wr, avg, cnt, p, cond) in enumerate(best[:20]):
    log(f"  {i+1:2d}   | {wr:5.1f}% | {avg:5.1f}% | {cnt:4d} | {p} × {cond}")

# ── 保存 ──────────────────────────────────────────
result = {
    'total_signals': len(signals),
    'elapsed_sec': round(elapsed, 1),
    'by_pattern': {},
    'top_combos': [{'pattern': p, 'cond': c, 'count': cnt, 'win_rate': wr, 'avg_gain': avg}
                    for wr, avg, cnt, p, c in best[:20]]
}
for p in all_patterns:
    subset = [s for s in signals if s['pattern'] == p]
    if not subset:
        continue
    h = [s for s in subset if s['is_higher_low']]
    l = [s for s in subset if not s['is_higher_low']]
    result['by_pattern'][p] = {
        'total': len(subset),
        'higher_low': {'count': len(h), 'win_rate': round(sum(s['has_wave2'] for s in h)/len(h)*100, 1) if h else 0},
        'lower_low':  {'count': len(l), 'win_rate': round(sum(s['has_wave2'] for s in l)/len(l)*100, 1) if l else 0},
    }

out_path = os.path.join(OUT_DIR, f'wave2_cy_full_v2_{time.strftime("%Y%m%d_%H%M%S")}.json')
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

log(f"\n  结果已保存: {out_path}")
log(f"  日志文件: {_log_path}")
log('=' * 70)
_log_f.close()
