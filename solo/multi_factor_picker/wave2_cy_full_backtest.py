#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
双创板全量回测：按形态 + 入场条件 交叉分组统计胜率
股票池：300/688/301 全量（约1500只）
回测区间：2024-01-01 ~ 2026-06-20
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

_log_path = os.path.join(OUT_DIR, 'wave2_cy_full_log.txt')
_log_f = open(_log_path, 'w', encoding='utf-8', buffering=1)

def log(msg):
    sys.stdout.write(msg + '\n')
    _log_f.write(msg + '\n')
    _log_f.flush()

log('=' * 70)
log("双创板全量回测：形态 × 入场条件 交叉分组")
log("股票池：300/688/301 全量 | 区间：20240101~20260620")
log('=' * 70)

# ── 股票池 ────────────────────────────────────────
log("\n[Step1] 获取双创板全量...")
sb = pro.stock_basic(exchange='', list_status='L', fields='ts_code')
cy_kc = sb[sb['ts_code'].str.startswith(('300', '688', '301'))]
pool = cy_kc['ts_code'].tolist()
log(f"  共 {len(pool)} 只")
time.sleep(0.1)

START = '20240101'
END   = '20260620'
WAVE1_MIN   = 0.20   # 一波最低涨幅20%
WAVE2_MIN   = 0.10   # 二波最低涨幅10%
WAVE2_WINDOW = 20     # 二波确认窗口

# 信号容器：每个信号附带形态标签 + 入场条件标签
# pattern: sideways / shallow_pullback / deep_pullback / triangle / volume_pullback / v_shape
# entry_cond: rsi_lt30 / macd_gold / ma20_above / ma60_above / dmi_cross / volume_explode
signals = []

log(f"\n[Step2] 全量回测中（预计15~20分钟）...")
t0 = time.time()
parse_errors = 0

for i, ts_code in enumerate(pool):
    if i % 100 == 0:
        elapsed = time.time() - t0
        eta = elapsed / max(i, 1) * (len(pool) - i)
        log(f"  进度: {i}/{len(pool)} | 耗时{elapsed:.0f}s | 预计剩余{eta/60:.0f}min")

    try:
        # 只取日线（速度优先，足够做形态分类）
        df = pro.daily(ts_code=ts_code, start_date=START, end_date=END)
        time.sleep(0.06)
        if df is None or len(df) < 80:
            continue

        df = df.sort_values('trade_date').reset_index(drop=True)
        closes   = df['close'].values.astype(np.float64)
        volumes  = df['vol'].values.astype(np.float64)
        n = len(closes)

        # ── 找一波拉升（20日涨幅≥20%）────────────────────
        # 向量化：找所有一波起点
        if n < 60:
            continue

        # 计算20日涨幅
        gain_20d = (closes[20:] - closes[:-20]) / closes[:-20]
        surge_starts = np.where(gain_20d >= WAVE1_MIN)[0] + 20

        if len(surge_starts) == 0:
            continue

        # 对每个一波，找终点和深度回调
        for s_idx in surge_starts[:2]:  # 每只股票最多2个一波
            if s_idx >= n - 40:
                continue

            # 一波终点 = s_idx ~ s_idx+30 的最高价
            end_win = closes[s_idx : min(s_idx+30, n)]
            if len(end_win) == 0:
                continue
            wave1_end_rel = np.argmax(end_win)
            wave1_end_idx = s_idx + wave1_end_rel
            wave1_peak = closes[wave1_end_idx]

            # 一波启动前最低价（前40天）
            pre_start = max(0, s_idx - 40)
            pre_low = closes[pre_start : s_idx+1].min()

            # ── 找调整低点（一波终点后3~60天）────────────
            adj_win = closes[wave1_end_idx+1 : min(wave1_end_idx+61, n)]
            if len(adj_win) < 5:
                continue

            adj_low_rel = np.argmin(adj_win)
            adj_low_idx = wave1_end_idx + 1 + adj_low_rel
            adj_low_price = closes[adj_low_idx]

            pullback_pct = (wave1_peak - adj_low_price) / wave1_peak
            if pullback_pct < 0.10:
                continue  # 只看回调≥10%的

            # ── 形态分类 ──────────────────────────────────
            # 计算辅助指标
            adj_days = adj_low_idx - wave1_end_idx
            surge_vol = volumes[max(0, s_idx-5) : s_idx+5].mean()
            adj_vol   = volumes[wave1_end_idx+1 : adj_low_idx+1].mean()
            vol_ratio  = adj_vol / surge_vol if surge_vol > 0 else 1.0

            # MA20/MA60（简化计算）
            if adj_low_idx >= 20:
                ma20 = closes[adj_low_idx-20 : adj_low_idx+1].mean()
            else:
                ma20 = np.nan
            if adj_low_idx >= 60:
                ma60 = closes[adj_low_idx-60 : adj_low_idx+1].mean()
            else:
                ma60 = np.nan

            # RSI-14 简化计算
            if adj_low_idx >= 14:
                deltas = np.diff(closes[adj_low_idx-14 : adj_low_idx+1])
                gains = np.where(deltas > 0, deltas, 0).sum() / 14
                losses = np.where(deltas < 0, -deltas, 0).sum() / 14
                rs = gains / losses if losses > 0 else 100
                rsi = 100 - 100 / (1 + rs)
            else:
                rsi = 50

            # MACD简化（12/26/9 EMA）
            if adj_low_idx >= 26:
                ema12 = pd_ewm(closes[:adj_low_idx+1], 12)[-1]
                ema26 = pd_ewm(closes[:adj_low_idx+1], 26)[-1]
                macd_line = ema12 - ema26
                # 简化：用价格方向代替DEA
                macd_gold = macd_line > 0
            else:
                macd_gold = False

            # 分类
            # 1. 强势横盘：回调<15% + 调整<15天
            if pullback_pct < 0.15 and adj_days <= 15:
                pattern = '强势横盘'
            # 2. 缩量回调：10-20% + vol_ratio<0.7 + adj_days>15
            elif 0.10 <= pullback_pct < 0.20 and vol_ratio < 0.7 and adj_days > 15:
                pattern = '缩量回调'
            # 3. 深度回调：回调≥20%
            elif pullback_pct >= 0.20:
                pattern = '深度回调'
            # 4. 放量回调：10-25% + vol_ratio>1.2
            elif 0.10 <= pullback_pct < 0.25 and vol_ratio > 1.2:
                pattern = '放量回调'
            # 5. V型急跌：调整天数≤10 + 回调≥15%
            elif adj_days <= 10 and pullback_pct >= 0.15:
                pattern = 'V型急跌'
            else:
                pattern = '其他'

            if pattern == '其他':
                continue

            # ── 创新低判断 ────────────────────────────────
            is_higher_low = (adj_low_price > pre_low)

            # ── 入场条件判断（在调整低点处）────────────────
            entry_conds = []
            if not np.isnan(rsi) and rsi < 30:
                entry_conds.append('rsi_lt30')
            if macd_gold:
                entry_conds.append('macd_gold')
            if not np.isnan(ma20) and adj_low_price > ma20:
                entry_conds.append('ma20_above')
            if not np.isnan(ma60) and adj_low_price > ma60:
                entry_conds.append('ma60_above')
            if vol_ratio < 0.7:
                entry_conds.append('vol_shrink')
            if vol_ratio > 1.2:
                entry_conds.append('vol_explode')

            # ── 二波验证 ──────────────────────────────────
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

    except Exception as e:
        parse_errors += 1
        continue

elapsed = time.time() - t0
log(f"\n[Step3] 回测完成（耗时 {elapsed/60:.1f} 分钟，解析错误 {parse_errors} 次）")
log(f"  总信号数: {len(signals)}")

# ── 统计1：按形态 + 创新低 分组 ─────────────────────
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
    log(f"    不创新低: {len(h)}只 | 胜率{wr_h:.1f}% | 均涨{avg_h:.1f}%")
    if l:
        log(f"    创新低:   {len(l)}只 | 胜率{wr_l:.1f}% | 均涨{avg_l:.1f}%")

# ── 统计2：按形态 + 入场条件 分组 ───────────────────
log(f"\n{'='*70}")
log("统计2：按形态 × 入场条件 分组（只看不创新低样本）")
log(f"{'='*70}")

all_conds = ['rsi_lt30', 'macd_gold', 'ma20_above', 'ma60_above', 'vol_shrink', 'vol_explode']
for p in all_patterns:
    subset = [s for s in signals if s['pattern'] == p and s['is_higher_low']]
    if not subset:
        continue
    log(f"\n  【{p}】不创新低共{len(subset)}只")
    for cond in all_conds:
        c_subset = [s for s in subset if cond in s['entry_conds']]
        if len(c_subset) < 5:
            continue
        wr = sum(s['has_wave2'] for s in c_subset) / len(c_subset) * 100
        avg = sum(s['max_gain'] for s in c_subset) / len(c_subset) * 100
        log(f"    +{cond:15s}: {len(c_subset):4d}只 | 胜率{wr:.1f}% | 均涨{avg:.1f}%")

# ── 统计3：最佳组合（形态+条件同时满足）───────────────
log(f"\n{'='*70}")
log("统计3：最佳组合（形态 ∩ 入场条件，不创新低）")
log(f"{'='*70}")

best = []
for p in all_patterns:
    for cond in all_conds:
        subset = [s for s in signals
                  if s['pattern'] == p and s['is_higher_low'] and cond in s['entry_conds']]
        if len(subset) >= 10:
            wr = sum(s['has_wave2'] for s in subset) / len(subset) * 100
            avg = sum(s['max_gain'] for s in subset) / len(subset) * 100
            best.append((wr, avg, len(subset), p, cond))

best.sort(reverse=True)
log("\n  排名 | 胜率  | 均涨  | 样本数 | 形态 × 条件")
log("  ------|--------|--------|--------|------------------")
for i, (wr, avg, cnt, p, cond) in enumerate(best[:15]):
    log(f"  {i+1:2d}    | {wr:5.1f}% | {avg:5.1f}% | {cnt:5d}  | {p} × {cond}")

# ── 保存 ────────────────────────────────────────────
result = {
    'total_signals': len(signals),
    'elapsed_min': round(elapsed / 60, 1),
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
        'higher_low': {'count': len(h), 'win_rate': sum(s['has_wave2'] for s in h)/len(h)*100 if h else 0},
        'lower_low':  {'count': len(l), 'win_rate': sum(s['has_wave2'] for s in l)/len(l)*100 if l else 0},
    }

out_path = os.path.join(OUT_DIR, f'wave2_cy_full_{time.strftime("%Y%m%d_%H%M%S")}.json')
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

log(f"\n  结果已保存: {out_path}")
log(f"  日志文件: {_log_path}")
log('=' * 70)
_log_f.close()

# ── 辅助函数 ────────────────────────────────────────
def pd_ewm(arr, span):
    """简单EMA计算（不用pandas依赖）"""
    alpha = 2.0 / (span + 1)
    ew = [arr[0]]
    for v in arr[1:]:
        ew.append(alpha * v + (1 - alpha) * ew[-1])
    return np.array(ew)
