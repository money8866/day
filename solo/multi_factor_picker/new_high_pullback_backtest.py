# -*- coding: utf-8 -*-
"""
创新高后回落低吸策略回测 (v2)
---
基于Tushare stk_factor_pro缓存的回测，验证假设：
"波峰创N日新高的股票，回落后二波胜率更高（无套牢盘压制）"

对比：创新高波峰 vs 未创新高波峰 的二波成功率
"""
import os, sys, time
import numpy as np
import pandas as pd
from datetime import datetime
from pathlib import Path

sys.path.insert(0, r'D:\mystock')
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')
OUT_DIR = r'D:\mystock\solo\multi_factor_picker\output'
os.makedirs(OUT_DIR, exist_ok=True)

import tushare as ts

# ── Tushare初始化 ──
TUSHARE_TOKEN = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

# ── 参数 ──
SURGE_MIN = 0.18       # 一波最小涨幅18%
SURGE_DAYS = 5         # 一波窗口5天
NEW_HIGH_WINDOWS = [60, 120, 250]  # N日新高检测
NEAR_HIGH_PCT = 0.03   # 波峰在N日前高3%内算"创新高"
LOOKBACK_MAX = 150     # 搜索波峰最大回顾天数
HOLD_DAYS = [5, 10, 20, 30]
BACKTEST_START = '20230101'
BACKTEST_END = '20260601'
MIN_SAMPLES = 10       # 分组最小样本数

# ── 缓存的因子数据 ──
FACTOR_CACHE = Path(r'D:\mystock\solo\cache_backbone_tushare')

def load_stock_data(ts_code: str) -> pd.DataFrame:
    """加载stk_factor_pro数据（优先用缓存）"""
    cache_file = FACTOR_CACHE / f"daily_{ts_code.replace('.','_')}.pkl"
    if cache_file.exists():
        df = pd.read_pickle(cache_file)
        return df

    # 从Tushare拉取
    for attempt in range(3):
        try:
            df = pro.daily(ts_code=ts_code, start_date=BACKTEST_START, end_date=BACKTEST_END)
            if df is not None and not df.empty:
                df = df.sort_values('trade_date').reset_index(drop=True)
                df.index = df['trade_date']
                try:
                    df.to_pickle(cache_file)
                except:
                    pass
                return df
        except Exception as e:
            if attempt < 2:
                time.sleep(0.5)
    return None

def find_wave1_with_high_check(closes: np.ndarray, highs: np.ndarray, n: int):
    """
    寻找一波候选，同时检查波峰是否创N日新高

    返回: list of (wave1_high_idx, wave1_low_idx, surge_gain, new_high_windows_met)
    new_high_windows_met: list of int, 满足的N日新高窗口
    """
    candidates = []
    for lookback in range(3, min(LOOKBACK_MAX, n - SURGE_DAYS - 5)):
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
        sg = (window[high_in_win] - window[low_in_win]) / window[low_in_win]
        if sg < SURGE_MIN:
            continue
        w1_high_idx = end_idx - SURGE_DAYS + high_in_win
        w1_low_idx = end_idx - SURGE_DAYS + low_in_win

        if any(h == w1_high_idx for h, *_ in candidates):
            continue

        # ── 宏观结构过滤 ──
        lk_start = max(0, w1_low_idx - 200)
        pre_h = closes[lk_start:w1_low_idx]
        if len(pre_h) >= 20:
            if pre_h.max() > closes[w1_high_idx] * 1.15:
                continue

        # ── 检查是否创新高 ──
        nh_met = []
        w1_price = closes[w1_high_idx]
        for nh_win in NEW_HIGH_WINDOWS:
            if w1_high_idx < nh_win:
                continue
            nh_highs = highs[w1_high_idx - nh_win:w1_high_idx]
            ph = nh_highs.max()
            if ph > 0 and w1_price / ph >= (1 - NEAR_HIGH_PCT):
                nh_met.append(nh_win)

        candidates.append((w1_high_idx, w1_low_idx, sg, nh_met))

    candidates.sort(key=lambda x: (n - x[0]))
    return candidates

def backtest():
    # 读取股票池
    pool_file = r'D:\mystock\solo\report_daily\bull_stocks_qualified.csv'
    pool = pd.read_csv(pool_file, encoding='utf-8-sig')
    codes = pool['code'].astype(str).unique().tolist()
    print(f"股票池: {len(codes)} 只")

    all_rows = []
    total = len(codes)
    t_start = time.time()

    for idx, code in enumerate(codes):
        if (idx + 1) % 100 == 0:
            elapsed = time.time() - t_start
            eta = elapsed / (idx + 1) * (total - idx - 1)
            print(f"  [{idx+1}/{total}] {elapsed:.0f}s | ETA {eta:.0f}s")

        # 补全ts_code后缀
        code_str = str(code).strip().zfill(6)
        if code_str.startswith('6') or code_str.startswith('9'):
            ts_code_full = code_str + '.SH'
        else:
            ts_code_full = code_str + '.SZ'
        df = load_stock_data(ts_code_full)
        if df is None or len(df) < 300:
            continue

        closes = df['close'].values
        highs = df['high'].values
        n_total = len(df)

        # 每10天采样一次
        for n in range(200, n_total - max(HOLD_DAYS), 10):
            cur_close = closes[n]

            wave1_cands = find_wave1_with_high_check(closes[:n + 1], highs[:n + 1], n + 1)
            if not wave1_cands:
                continue

            # 取最近的候选
            best = wave1_cands[0]
            w1_high_idx, w1_low_idx, sg, nh_met = best
            w1_high_price = closes[w1_high_idx]

            # 回落幅度
            pullback = (w1_high_price - cur_close) / w1_high_price
            if pullback <= 0 or pullback > 0.40:
                continue

            # ── 三均线支撑 ──
            close_at_n = closes[n]
            ma60 = np.mean(closes[max(0, n-60):n])
            ma120 = np.mean(closes[max(0, n-120):n]) if n >= 120 else None
            ma250 = np.mean(closes[max(0, n-250):n]) if n >= 250 else None
            above_ma60 = close_at_n > ma60
            above_ma120 = ma120 is not None and close_at_n > ma120
            above_ma250 = ma250 is not None and close_at_n > ma250

            # ── 缩量检查 ──
            vol_before = np.mean(df['vol'].values[max(0, n-20):n-5])
            vol_recent = np.mean(df['vol'].values[n-5:n+1])
            vol_shrink = vol_recent < vol_before * 0.8 if vol_before > 0 else False

            # ── 测试各持有天数 ──
            for hold in HOLD_DAYS:
                if n + hold >= n_total:
                    continue

                exit_price = closes[n + hold]
                gain = (exit_price - cur_close) / cur_close
                max_gain = max(closes[n:n + hold + 1]) / cur_close - 1
                min_gain = min(closes[n:n + hold + 1]) / cur_close - 1
                success = gain > 0

                row = {
                    'code': code,
                    'date': df['trade_date'].iloc[n] if 'trade_date' in df.columns else str(n),
                    'wave1_gain': round(sg * 100, 1),
                    'pullback_pct': round(pullback * 100, 1),
                    'new_high_60': 60 in nh_met,
                    'new_high_120': 120 in nh_met,
                    'new_high_250': 250 in nh_met,
                    'new_high_count': len(nh_met),
                    'is_new_high': len(nh_met) > 0,
                    'above_ma60': above_ma60,
                    'above_ma120': above_ma120,
                    'above_ma250': above_ma250,
                    'three_ma_support': above_ma60 and above_ma120 and above_ma250,
                    'vol_shrink': vol_shrink,
                    'hold_days': hold,
                    'gain': round(gain * 100, 2),
                    'max_gain': round(max_gain * 100, 2),
                    'min_gain': round(min_gain * 100, 2),
                    'success': success,
                }
                all_rows.append(row)

    if not all_rows:
        print("无结果")
        return

    df_result = pd.DataFrame(all_rows)
    print(f"\n总样本: {len(df_result)} 笔")

    # ── 核心对比：创新高 vs 未创新高 ──
    print("\n" + "=" * 70)
    print("核心结论：波峰创新高 vs 未创新高的二波成功率")
    print("=" * 70)

    for is_nh in [True, False]:
        sub = df_result[df_result['is_new_high'] == is_nh]
        label = "创新高" if is_nh else "未创新高"
        if len(sub) >= MIN_SAMPLES:
            print(f"\n{label}: {len(sub)}笔")
            for hold in HOLD_DAYS:
                s = sub[sub['hold_days'] == hold]
                if len(s) >= MIN_SAMPLES:
                    wr = s['success'].mean() * 100
                    avg = s['gain'].mean()
                    max_avg = s['max_gain'].mean()
                    print(f"  持有{hold}d: 胜率{wr:.1f}% 均收益{avg:+.2f}% 最大均收益{max_avg:+.2f}%")
        else:
            print(f"\n{label}: {len(sub)}笔（样本不足）")

    # ── 按新高窗口分层 ──
    print(f"\n── 按新高窗口分层 (持有10天) ──")
    for nh_win in NEW_HIGH_WINDOWS:
        col = f'new_high_{nh_win}'
        for val, label in [(True, f'创{nh_win}日新高'), (False, f'非{nh_win}日新高')]:
            sub = df_result[(df_result[col] == val) & (df_result['hold_days'] == 10)]
            if len(sub) >= MIN_SAMPLES:
                wr = sub['success'].mean() * 100
                avg = sub['gain'].mean()
                print(f"  {label}: {len(sub)}笔 胜率{wr:.1f}% 均收益{avg:+.2f}%")

    # ── 最佳组合搜索 ──
    print(f"\n── 最优组合 TOP10 (持有10天, 样本>={MIN_SAMPLES}) ──")
    grouped = df_result[df_result['hold_days'] == 10].groupby(
        ['is_new_high', 'above_ma60', 'above_ma120', 'above_ma250', 'vol_shrink']
    ).agg(
        count=('success', 'count'),
        win_rate=('success', 'mean'),
        avg_gain=('gain', 'mean'),
        avg_max=('max_gain', 'mean'),
    ).reset_index()
    grouped = grouped[(grouped['count'] >= MIN_SAMPLES)].sort_values('win_rate', ascending=False)
    for _, row in grouped.head(10).iterrows():
        print(f"  新高={int(row['is_new_high'])} MA60={int(row['above_ma60'])} MA120={int(row['above_ma120'])} MA250={int(row['above_ma250'])} 缩量={int(row['vol_shrink'])}: {int(row['count'])}笔 胜率{row['win_rate']*100:.1f}% 均收益{row['avg_gain']*100:+.2f}%")

    # ── 保存 ──
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_path = os.path.join(OUT_DIR, f'new_high_pullback_backtest_{timestamp}.csv')
    df_result.to_csv(out_path, index=False, encoding='utf-8-sig')
    print(f"\n结果已保存: {out_path}")

    return df_result

if __name__ == '__main__':
    t0 = time.time()
    df = backtest()
    print(f"\n总耗时: {time.time() - t0:.0f}s")
    if df is not None:
        input("\n按回车退出...")
