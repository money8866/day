"""
回升买点 vs 突破H1买入 策略对比回测
数据来源：通达信本地日线 (.day文件)

策略A - 回升买点：L0->H1->L2结构后，价格从L2回升但未突破H1时低吸
策略B - 突破H1买入：L0->H1->L2结构后，价格突破H1当日买入
"""
import os
import sys
import struct
import numpy as np
import pandas as pd
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass

sys.path.insert(0, r'd:\mystock\solo')
from etf_resonance.wave3_detector import find_pivots, Pivot, W1_MIN_GAIN, W2_RETRACE_MIN, W2_RETRACE_MAX

TDX_PATH = r"C:\new_tdx"

@dataclass
class SimpleWave:
    L0: Pivot
    H1: Pivot
    L2: Pivot
    w1_gain: float
    w2_retrace: float

@dataclass
class TradeRecord:
    ts_code: str
    strategy: str
    signal_date: str
    buy_date: str
    buy_price: float
    sell_date: str
    sell_price: float
    hold_days: int
    return_pct: float
    w1_gain: float
    w2_retrace: float

def parse_tdx_day_file(filepath):
    if not os.path.exists(filepath):
        return None
    records = []
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(32)
            if not chunk or len(chunk) < 32:
                break
            date_int = struct.unpack("<i", chunk[0:4])[0]
            open_p = struct.unpack("<i", chunk[4:8])[0] / 100.0
            high_p = struct.unpack("<i", chunk[8:12])[0] / 100.0
            low_p = struct.unpack("<i", chunk[12:16])[0] / 100.0
            close_p = struct.unpack("<i", chunk[16:20])[0] / 100.0
            amount_yuan = struct.unpack("<f", chunk[20:24])[0]
            vol_shares = struct.unpack("<i", chunk[24:28])[0] / 100.0
            date_str = str(date_int)
            records.append({
                "trade_date": date_str,
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "close": close_p,
                "vol": vol_shares,
                "amount": round(amount_yuan / 1000, 3),
            })
    if not records:
        return None
    df = pd.DataFrame(records)
    df = df.sort_values("trade_date").reset_index(drop=True)
    df["pct_chg"] = df["close"].pct_change() * 100
    df["pct_chg"] = df["pct_chg"].fillna(0)
    return df

def ts_code_to_tdx_file(ts_code):
    sym, market = ts_code.split(".")
    if market == "SH":
        prefix = "sh"
        subdir = "sh"
    elif market == "SZ":
        prefix = "sz"
        subdir = "sz"
    else:
        return None
    return os.path.join(TDX_PATH, "vipdoc", subdir, "lday", f"{prefix}{sym}.day")

def get_tdx_kline(ts_code, end_date_str, n_days=250):
    tdx_file = ts_code_to_tdx_file(ts_code)
    if not tdx_file or not os.path.exists(tdx_file):
        return None
    df = parse_tdx_day_file(tdx_file)
    if df is None or df.empty:
        return None
    end_dt = datetime.strptime(end_date_str, '%Y%m%d')
    df['date_dt'] = pd.to_datetime(df['trade_date'])
    filtered = df[df['date_dt'] <= end_dt]
    if len(filtered) < 60:
        return None
    recent = filtered.tail(n_days).copy()
    recent.drop('date_dt', axis=1, inplace=True)
    recent = recent.reset_index(drop=True)
    return recent

def find_simple_wave(pivots: List[Pivot], df: pd.DataFrame) -> Optional[SimpleWave]:
    if len(pivots) < 3:
        return None
    best_wave = None
    best_score = -1
    for i in range(len(pivots) - 2):
        if pivots[i].kind != 'low':
            continue
        L0 = pivots[i]
        H1 = None
        for j in range(i+1, len(pivots)):
            if pivots[j].kind == 'high':
                H1 = pivots[j]
                break
        if H1 is None:
            continue
        L2 = None
        for j in range(i+2, len(pivots)):
            if pivots[j].kind == 'low':
                L2 = pivots[j]
                break
        if L2 is None:
            continue
        w1_gain = (H1.price - L0.price) / max(L0.price, 0.01)
        w2_retrace = (H1.price - L2.price) / max(H1.price - L0.price, 0.01)
        if w1_gain < W1_MIN_GAIN:
            continue
        if not (W2_RETRACE_MIN <= w2_retrace <= W2_RETRACE_MAX):
            continue
        if L2.price <= L0.price:
            continue
        if L2.idx < H1.idx:
            continue
        score = w1_gain * 10
        if score > best_score:
            best_score = score
            best_wave = SimpleWave(
                L0=L0, H1=H1, L2=L2, w1_gain=w1_gain, w2_retrace=w2_retrace
            )
    return best_wave

def get_stock_list():
    stock_list = []
    cons_path = r'D:\mystock\cache_daily\etf_constituents_all.json'
    if os.path.exists(cons_path):
        import json
        with open(cons_path, 'r', encoding='utf-8') as f:
            cons_data = json.load(f)
        if isinstance(cons_data, dict):
            for etf_code, stocks in cons_data.items():
                for s in stocks:
                    tc = s.get('ts_code', '') if isinstance(s, dict) else str(s)
                    if tc and '.' in tc and tc not in stock_list:
                        stock_list.append(tc)
        elif isinstance(cons_data, list):
            for s in cons_data:
                tc = s.get('ts_code', '') if isinstance(s, dict) else str(s)
                if tc and '.' in tc and tc not in stock_list:
                    stock_list.append(tc)
        print(f"从ETF成份股缓存加载: {len(stock_list)} 只", flush=True)
    else:
        for market in ['sh', 'sz']:
            dir_path = os.path.join(TDX_PATH, "vipdoc", market, "lday")
            if not os.path.exists(dir_path):
                continue
            for fname in os.listdir(dir_path):
                if not fname.endswith('.day'):
                    continue
                code = fname.replace('.day', '')
                if code.startswith('sh'):
                    ts_code = code[2:] + '.SH'
                elif code.startswith('sz'):
                    ts_code = code[2:] + '.SZ'
                else:
                    continue
                stock_list.append(ts_code)
        print(f"从通达信目录加载: {len(stock_list)} 只", flush=True)
    return stock_list

def get_trade_dates(start_date, end_date):
    dates = []
    for market in ['sh', 'sz']:
        dir_path = os.path.join(TDX_PATH, "vipdoc", market, "lday")
        if not os.path.exists(dir_path):
            continue
        sample_file = os.listdir(dir_path)[0]
        filepath = os.path.join(dir_path, sample_file)
        df = parse_tdx_day_file(filepath)
        if df is not None:
            df = df[(df['trade_date'] >= start_date) & (df['trade_date'] <= end_date)]
            dates.extend(df['trade_date'].tolist())
            break
    return sorted(list(set(dates)))

def detect_rebound_signal(wave: SimpleWave, df: pd.DataFrame) -> Tuple[Optional[float], dict]:
    if wave is None:
        return None, {}
    current_price = df['close'].values[-1]
    if current_price < wave.L2.price:
        return None, {}
    l2_idx = -1
    for i, d in enumerate(df['trade_date']):
        if str(d) == wave.L2.date:
            l2_idx = i
            break
    if l2_idx == -1:
        return None, {}
    days_since_L2 = len(df) - l2_idx - 1
    if days_since_L2 < 3:
        return None, {}
    if days_since_L2 > 30:
        return None, {}
    if current_price > wave.H1.price:
        return None, {}
    closes = df['close'].values
    vol = df['vol'].values if 'vol' in df.columns else np.zeros(len(df))
    ma5 = np.mean(closes[-5:]) if len(closes) >= 5 else closes[-1]
    ma10 = np.mean(closes[-10:]) if len(closes) >= 10 else closes[-1]
    ma20 = np.mean(closes[-20:]) if len(closes) >= 20 else closes[-1]
    vol_5 = np.mean(vol[-5:])
    vol_20 = np.mean(vol[-20:]) if len(vol) >= 20 else vol[-1]
    vol_ratio = vol_5 / vol_20 if vol_20 > 0 else 0
    rebound_pct = (current_price / wave.L2.price - 1) * 100
    dist_to_H1_pct = (wave.H1.price / current_price - 1) * 100
    score = 0
    reasons = []
    if current_price > ma5:
        score += 15; reasons.append('MA5已突破')
    if current_price > ma10:
        score += 15; reasons.append('MA10已突破')
    if current_price > ma20:
        score += 20; reasons.append('MA20已突破')
    if vol_ratio > 1.0:
        score += 15; reasons.append(f'量比{vol_ratio:.2f}放大')
    if rebound_pct > 5:
        score += 15; reasons.append(f'回升{rebound_pct:.1f}%')
    elif rebound_pct > 0:
        score += 10; reasons.append(f'回升{rebound_pct:.1f}%')
    if dist_to_H1_pct > 5:
        score += 10; reasons.append(f'距H1还有{dist_to_H1_pct:.1f}%空间')
    if 5 <= days_since_L2 <= 15:
        score += 10; reasons.append(f'回调后{days_since_L2}天黄金时间窗口')
    if rebound_pct <= 0:
        return None, {}
    details = {
        'score': score,
        'days_since_L2': days_since_L2,
        'rebound_pct': rebound_pct,
        'dist_to_H1_pct': dist_to_H1_pct,
        'w1_gain': wave.w1_gain,
        'w2_retrace': wave.w2_retrace,
        'reasons': reasons
    }
    return score, details

def main():
    print("=" * 70, flush=True)
    print("    回升买点 vs 突破H1买入 策略对比回测", flush=True)
    print("=" * 70, flush=True)

    START_DATE = "20250101"
    END_DATE = "20260708"
    HOLD_DAYS = 5
    MIN_SCORE = 60
    SKIP_BJ = True

    print(f"回测区间: {START_DATE} ~ {END_DATE}", flush=True)
    print(f"持仓天数: {HOLD_DAYS}", flush=True)
    print(f"回升买点最低评分: {MIN_SCORE}", flush=True)
    print(f"跳过北交所: {SKIP_BJ}", flush=True)
    print("-" * 70, flush=True)

    trade_dates = get_trade_dates(START_DATE, END_DATE)
    date_to_idx = {d: i for i, d in enumerate(trade_dates)}
    print(f"交易天数: {len(trade_dates)}", flush=True)
    print(f"首交易日: {trade_dates[0]}", flush=True)
    print(f"末交易日: {trade_dates[-1]}", flush=True)

    stock_list = get_stock_list()
    if SKIP_BJ:
        stock_list = [s for s in stock_list if not (s.startswith('8') or s.startswith('4') or s.startswith('9'))]
    print(f"\n股票池: {len(stock_list)} 只", flush=True)

    print("\n[1] 预加载股票K线数据...", flush=True)
    stock_data = {}
    for i, ts_code in enumerate(stock_list):
        if (i + 1) % 200 == 0:
            print(f"  进度: {i+1}/{len(stock_list)} | 已加载: {len(stock_data)}", flush=True)
        df = get_tdx_kline(ts_code, END_DATE, n_days=250)
        if df is not None and len(df) >= 60:
            stock_data[ts_code] = df
    print(f"  预加载完成: {len(stock_data)}/{len(stock_list)}", flush=True)

    all_trades = []
    rebound_signal_cache = {}
    breakout_signal_cache = {}

    total_dates = len(trade_dates)

    print("\n[2] 开始对比回测...", flush=True)
    for date_idx, date_str in enumerate(trade_dates):
        if date_idx % 20 == 0:
            print(f"  进度: {date_idx+1}/{total_dates} ({date_str}) | 回升:{len([t for t in all_trades if t.strategy=='rebound'])} 突破:{len([t for t in all_trades if t.strategy=='breakout'])}", flush=True)

        for ts_code, full_df in stock_data.items():
            sliced_df = full_df[full_df['trade_date'] <= date_str].copy()
            if len(sliced_df) < 60:
                continue

            pivots = find_pivots(sliced_df)
            wave = find_simple_wave(pivots, sliced_df)
            if not wave:
                continue

            current_price = sliced_df['close'].values[-1]
            current_date = sliced_df['trade_date'].values[-1]

            h1_price = wave.H1.price
            l2_price = wave.L2.price
            w1_gain = wave.w1_gain
            w2_retrace = wave.w2_retrace

            # ---- 策略A: 回升买点 ----
            score, details = detect_rebound_signal(wave, sliced_df)
            if score is not None and score >= MIN_SCORE:
                if ts_code in rebound_signal_cache:
                    prev_date = rebound_signal_cache[ts_code]
                    days_diff = date_idx - date_to_idx.get(prev_date, -999)
                    if days_diff < HOLD_DAYS:
                        pass
                    else:
                        rebound_signal_cache[ts_code] = date_str
                        buy_idx_a = date_idx + 1
                        if buy_idx_a < len(trade_dates):
                            buy_date_a = trade_dates[buy_idx_a]
                            buy_rows_a = full_df[full_df['trade_date'] == buy_date_a]
                            if not buy_rows_a.empty:
                                buy_price_a = buy_rows_a['open'].iloc[0]
                                sell_idx_a = min(buy_idx_a + HOLD_DAYS, len(trade_dates) - 1)
                                sell_date_a = trade_dates[sell_idx_a]
                                sell_rows_a = full_df[full_df['trade_date'] == sell_date_a]
                                if not sell_rows_a.empty:
                                    sell_price_a = sell_rows_a['close'].iloc[0]
                                    return_pct_a = (sell_price_a / buy_price_a - 1) * 100
                                    all_trades.append(TradeRecord(
                                        ts_code=ts_code, strategy='rebound',
                                        signal_date=date_str, buy_date=buy_date_a,
                                        buy_price=buy_price_a, sell_date=sell_date_a,
                                        sell_price=sell_price_a, hold_days=sell_idx_a - buy_idx_a,
                                        return_pct=round(return_pct_a, 2),
                                        w1_gain=round(w1_gain * 100, 1),
                                        w2_retrace=round(w2_retrace * 100, 1),
                                    ))
                else:
                    rebound_signal_cache[ts_code] = date_str
                    buy_idx_a = date_idx + 1
                    if buy_idx_a < len(trade_dates):
                        buy_date_a = trade_dates[buy_idx_a]
                        buy_rows_a = full_df[full_df['trade_date'] == buy_date_a]
                        if not buy_rows_a.empty:
                            buy_price_a = buy_rows_a['open'].iloc[0]
                            sell_idx_a = min(buy_idx_a + HOLD_DAYS, len(trade_dates) - 1)
                            sell_date_a = trade_dates[sell_idx_a]
                            sell_rows_a = full_df[full_df['trade_date'] == sell_date_a]
                            if not sell_rows_a.empty:
                                sell_price_a = sell_rows_a['close'].iloc[0]
                                return_pct_a = (sell_price_a / buy_price_a - 1) * 100
                                all_trades.append(TradeRecord(
                                    ts_code=ts_code, strategy='rebound',
                                    signal_date=date_str, buy_date=buy_date_a,
                                    buy_price=buy_price_a, sell_date=sell_date_a,
                                    sell_price=sell_price_a, hold_days=sell_idx_a - buy_idx_a,
                                    return_pct=round(return_pct_a, 2),
                                    w1_gain=round(w1_gain * 100, 1),
                                    w2_retrace=round(w2_retrace * 100, 1),
                                ))

            # ---- 策略B: 突破H1买入 ----
            # 信号条件：当日收盘价突破H1（前高），且前一天还未突破
            prev_close = sliced_df['close'].values[-2] if len(sliced_df) >= 2 else 0
            if current_price > h1_price and prev_close <= h1_price:
                if ts_code in breakout_signal_cache:
                    prev_date_b = breakout_signal_cache[ts_code]
                    days_diff_b = date_idx - date_to_idx.get(prev_date_b, -999)
                    if days_diff_b >= HOLD_DAYS:
                        breakout_signal_cache[ts_code] = date_str
                        buy_idx_b = date_idx + 1
                        if buy_idx_b < len(trade_dates):
                            buy_date_b = trade_dates[buy_idx_b]
                            buy_rows_b = full_df[full_df['trade_date'] == buy_date_b]
                            if not buy_rows_b.empty:
                                buy_price_b = buy_rows_b['open'].iloc[0]
                                sell_idx_b = min(buy_idx_b + HOLD_DAYS, len(trade_dates) - 1)
                                sell_date_b = trade_dates[sell_idx_b]
                                sell_rows_b = full_df[full_df['trade_date'] == sell_date_b]
                                if not sell_rows_b.empty:
                                    sell_price_b = sell_rows_b['close'].iloc[0]
                                    return_pct_b = (sell_price_b / buy_price_b - 1) * 100
                                    all_trades.append(TradeRecord(
                                        ts_code=ts_code, strategy='breakout',
                                        signal_date=date_str, buy_date=buy_date_b,
                                        buy_price=buy_price_b, sell_date=sell_date_b,
                                        sell_price=sell_price_b, hold_days=sell_idx_b - buy_idx_b,
                                        return_pct=round(return_pct_b, 2),
                                        w1_gain=round(w1_gain * 100, 1),
                                        w2_retrace=round(w2_retrace * 100, 1),
                                    ))
                else:
                    breakout_signal_cache[ts_code] = date_str
                    buy_idx_b = date_idx + 1
                    if buy_idx_b < len(trade_dates):
                        buy_date_b = trade_dates[buy_idx_b]
                        buy_rows_b = full_df[full_df['trade_date'] == buy_date_b]
                        if not buy_rows_b.empty:
                            buy_price_b = buy_rows_b['open'].iloc[0]
                            sell_idx_b = min(buy_idx_b + HOLD_DAYS, len(trade_dates) - 1)
                            sell_date_b = trade_dates[sell_idx_b]
                            sell_rows_b = full_df[full_df['trade_date'] == sell_date_b]
                            if not sell_rows_b.empty:
                                sell_price_b = sell_rows_b['close'].iloc[0]
                                return_pct_b = (sell_price_b / buy_price_b - 1) * 100
                                all_trades.append(TradeRecord(
                                    ts_code=ts_code, strategy='breakout',
                                    signal_date=date_str, buy_date=buy_date_b,
                                    buy_price=buy_price_b, sell_date=sell_date_b,
                                    sell_price=sell_price_b, hold_days=sell_idx_b - buy_idx_b,
                                    return_pct=round(return_pct_b, 2),
                                    w1_gain=round(w1_gain * 100, 1),
                                    w2_retrace=round(w2_retrace * 100, 1),
                                ))

    print("\n" + "=" * 70, flush=True)
    print("回测完成，开始统计...", flush=True)
    print("=" * 70, flush=True)

    if not all_trades:
        print("无交易记录！", flush=True)
        return

    trades_df = pd.DataFrame([t.__dict__ for t in all_trades])
    output_file = f"tdx_backtest_compare_h{HOLD_DAYS}.csv"
    trades_df.to_csv(output_file, index=False, encoding='utf-8-sig')
    print(f"\n交易明细已保存: {output_file}", flush=True)

    print("\n" + "=" * 70, flush=True)
    print("          📊 策略对比回测报告", flush=True)
    print("=" * 70, flush=True)
    print(f"回测区间:     {START_DATE} ~ {END_DATE}", flush=True)
    print(f"持仓天数:     {HOLD_DAYS}", flush=True)
    print("-" * 70, flush=True)

    for strategy_name, label in [('rebound', '策略A: 回升买点（L2回调低吸）'), ('breakout', '策略B: 突破H1买入')]:
        sub = trades_df[trades_df['strategy'] == strategy_name]
        if sub.empty:
            print(f"\n{label}: 无交易记录", flush=True)
            continue
        total = len(sub)
        winning = len(sub[sub['return_pct'] > 0])
        win_rate = winning / total * 100
        avg_ret = sub['return_pct'].mean()
        med_ret = sub['return_pct'].median()
        max_ret = sub['return_pct'].max()
        min_ret = sub['return_pct'].min()
        avg_hold = sub['hold_days'].mean()
        unique_st = sub['ts_code'].nunique()

        print(f"\n{'─' * 70}", flush=True)
        print(f"  {label}", flush=True)
        print(f"{'─' * 70}", flush=True)
        print(f"  📈 总交易次数: {total}", flush=True)
        print(f"  📈 涉及股票:   {unique_st} 只", flush=True)
        print(f"  🎯 胜率:       {win_rate:.1f}% ({winning}/{total})", flush=True)
        print(f"  💰 平均收益:   {avg_ret:+.2f}%", flush=True)
        print(f"  💰 中位收益:   {med_ret:+.2f}%", flush=True)
        print(f"  📈 最大收益:   {max_ret:+.2f}%", flush=True)
        print(f"  📉 最大亏损:   {min_ret:+.2f}%", flush=True)
        print(f"  ⏱️  平均持仓:   {avg_hold:.1f} 天", flush=True)

        grouped_w1 = sub.groupby(pd.cut(sub['w1_gain'], bins=[0, 50, 80, 100, 150, 200, 1000],
                                        labels=['<50%', '50-80%', '80-100%', '100-150%', '150-200%', '200%+']))
        print(f"\n  📊 按W1涨幅分组:", flush=True)
        for name, group in grouped_w1:
            if len(group) > 0:
                wr = (group['return_pct'] > 0).mean() * 100
                ar = group['return_pct'].mean()
                print(f"    {str(name):10s}: {len(group)}笔 | 胜率{wr:.1f}% | 平均{ar:+.2f}%", flush=True)

        grouped_w2 = sub.groupby(pd.cut(sub['w2_retrace'], bins=[20, 40, 55, 70, 85],
                                        labels=['20-40%', '40-55%', '55-70%', '70-85%']))
        print(f"\n  📊 按W2回调分组:", flush=True)
        for name, group in grouped_w2:
            if len(group) > 0:
                wr = (group['return_pct'] > 0).mean() * 100
                ar = group['return_pct'].mean()
                print(f"    {str(name):10s}: {len(group)}笔 | 胜率{wr:.1f}% | 平均{ar:+.2f}%", flush=True)

    print(f"\n{'=' * 70}", flush=True)
    print("          📊 核心对比汇总", flush=True)
    print("=" * 70, flush=True)
    print(f"{'指标':<12s} | {'回升买点':>14s} | {'突破H1':>14s} | {'差异':>10s}", flush=True)
    print("-" * 58, flush=True)
    for strategy_name in ['rebound', 'breakout']:
        sub = trades_df[trades_df['strategy'] == strategy_name]
    reb = trades_df[trades_df['strategy'] == 'rebound']
    brk = trades_df[trades_df['strategy'] == 'breakout']
    metrics = [
        ('交易次数', len(reb), len(brk), False),
        ('胜率(%)', (reb['return_pct']>0).mean()*100 if len(reb)>0 else 0, (brk['return_pct']>0).mean()*100 if len(brk)>0 else 0, False),
        ('平均收益(%)', reb['return_pct'].mean() if len(reb)>0 else 0, brk['return_pct'].mean() if len(brk)>0 else 0, True),
        ('中位收益(%)', reb['return_pct'].median() if len(reb)>0 else 0, brk['return_pct'].median() if len(brk)>0 else 0, True),
        ('最大收益(%)', reb['return_pct'].max() if len(reb)>0 else 0, brk['return_pct'].max() if len(brk)>0 else 0, True),
        ('最大亏损(%)', reb['return_pct'].min() if len(reb)>0 else 0, brk['return_pct'].min() if len(brk)>0 else 0, True),
    ]
    for name, v1, v2, is_float in metrics:
        if is_float:
            print(f"{name:<12s} | {v1:>14.2f} | {v2:>14.2f} | {v1-v2:>+10.2f}", flush=True)
        else:
            print(f"{name:<12s} | {v1:>14.0f} | {v2:>14.0f} | {v1-v2:>+10.0f}", flush=True)

    print("\n" + "=" * 70, flush=True)
    print("回测报告生成完毕", flush=True)
    print("=" * 70, flush=True)

if __name__ == "__main__":
    main()
