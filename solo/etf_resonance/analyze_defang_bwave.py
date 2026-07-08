"""德方纳米(300769.SZ) BWave策略分析

逐日复现BWave信号(启动信号+底背离),找出:
  1. BWave是否曾经选中过德方纳米
  2. 选中时的评分维度(A浪/B浪/趋势/启动信号)
  3. 选中后的实际走势
  4. 与W2策略命中点对比

关键区别:
  - W2策略: 纯左侧抄底(现价在L2~H1之间,无信号确认)
  - BWave策略: 右侧确认(需要MACD金叉/RSI金叉/MA5上穿等启动信号)
"""
import os
import sys
import sqlite3
import numpy as np
import pandas as pd
from datetime import datetime

sys.path.insert(0, r'd:\mystock\solo')
os.chdir(r'd:\mystock\solo')

from bwave_strategy import (
    get_data, detect_awave, detect_all_awaves,
    detect_bwave, detect_bwave_relaxed,
    check_launch_signal, detect_bwave_divergence,
    calc_bwave_score, calc_divergence_score,
)

TARGET = '300769.SZ'
NAME = '德方纳米'
DB = r'D:\mystock\cache_daily\stock_data.db'


def fmt_date(d) -> str:
    s = str(d)
    return f"{s[:4]}-{s[4:6]}-{s[6:]}" if len(s) == 8 else s


def scan_bwave_signals(df: pd.DataFrame) -> list:
    """逐日复现BWave信号,返回所有命中点。

    对每个交易日i,用df.iloc[:i+1]切片,运行完整BWave检测流程。
    返回: [(idx, date, price, signal_type, score_dict, awave, bwave, sig), ...]
    """
    hits = []
    start_idx = 250
    for i in range(start_idx, len(df)):
        sub = df.iloc[:i+1].reset_index(drop=True)
        awave = detect_awave(sub)
        if awave is None:
            continue

        results = []

        bwave = detect_bwave(sub, awave)
        if bwave:
            launch = check_launch_signal(sub, awave, bwave)
            if launch:
                launch_idx = launch['launch_idx']
                if len(sub) - launch_idx <= 10:
                    score = calc_bwave_score(awave, bwave, launch)
                    if score['total'] >= 65:
                        results.append(('launch', score, awave, bwave, launch))

            div = detect_bwave_divergence(sub, awave, bwave)
            if div:
                score = calc_divergence_score(awave, bwave, div)
                if score['total'] >= 60:
                    results.append(('divergence', score, awave, bwave, div))

        bwave_r = detect_bwave_relaxed(sub, awave)
        if bwave_r:
            launch = check_launch_signal(sub, awave, bwave_r)
            if launch:
                launch_idx = launch['launch_idx']
                if len(sub) - launch_idx <= 10:
                    score = calc_bwave_score(awave, bwave_r, launch)
                    if score['total'] >= 60:
                        results.append(('launch_relaxed', score, awave, bwave_r, launch))

            div = detect_bwave_divergence(sub, awave, bwave_r)
            if div:
                score = calc_divergence_score(awave, bwave_r, div)
                if score['total'] >= 60:
                    results.append(('divergence_relaxed', score, awave, bwave_r, div))

        for sig_type, score, aw, bw, sig in results:
            hits.append((i, sub.iloc[i]['trade_date'], float(sub.iloc[i]['close']),
                         sig_type, score, aw, bw, sig))
            break

    return hits


def print_score_breakdown(score: dict, sig_type: str):
    """打印评分维度明细。"""
    print(f"    信号类型: {sig_type}")
    print(f"    BWaveScore = {score['total']}")
    print(f"      A浪质量(a_score×30%): {score.get('a_score', 0)} -> {score.get('a_score',0)*0.30:.1f}")
    print(f"      B浪健康(b_score×35%): {score.get('b_score', 0)} -> {score.get('b_score',0)*0.35:.1f}")
    if 'd_score' in score:
        print(f"      底背离(d_score×30%): {score.get('d_score', 0)} -> {score.get('d_score',0)*0.30:.1f}")
        print(f"      趋势保持(t_score×15%): {score.get('t_score', 0)} -> {score.get('t_score',0)*0.15:.1f}")
    else:
        print(f"      趋势保持(t_score×20%): {score.get('t_score', 0)} -> {score.get('t_score',0)*0.20:.1f}")
        print(f"      启动信号(l_score×15%): {score.get('l_score', 0)} -> {score.get('l_score',0)*0.15:.1f}")


def print_wave_structure(awave: dict, bwave: dict, sig: dict):
    """打印A浪/B浪/启动信号结构。"""
    print(f"    A浪(主升浪):")
    print(f"      起 {fmt_date(awave['start_date'])}({awave['start_price']:.2f}) -> "
          f"止 {fmt_date(awave['end_date'])}({awave['end_price']:.2f})")
    print(f"      涨幅 +{awave['gain']:.1f}%  时长{awave['duration']}天  量比{awave.get('vol_ratio',0):.2f}")

    print(f"    B浪(回调):")
    print(f"      高 {fmt_date(bwave['start_date'])}({bwave['high_price']:.2f}) -> "
          f"低 {fmt_date(bwave['low_date'])}({bwave['low_price']:.2f})")
    print(f"      回调 -{bwave['drop']:.1f}%  时长{bwave['duration']}天  时间比{bwave.get('time_ratio',0):.2f}")
    print(f"      缩量{bwave.get('vol_shrink_ratio',0):.2f}  ATR下降{bwave.get('atr_drop',0):.1f}%  "
          f"距MA60 {bwave.get('ma60_dist',0):+.1f}%")

    if 'launch_idx' in sig:
        print(f"    启动信号:")
        print(f"      触发日 idx={sig['launch_idx']}")
        print(f"      MACD金叉={sig.get('macd_golden',False)}  RSI金叉={sig.get('rsi_golden',False)}  "
              f"MA5上穿={sig.get('ma5_crossing',False)}")
        print(f"      突破平台={sig.get('break_platform',False)}  放量={sig.get('vol_surge',False)}  "
              f"RSI6={sig.get('rsi6',0):.1f}")
        print(f"      B浪反弹{sig.get('b_recovery',0):.1f}%  距A高{sig.get('dist_to_a_high',0):.1f}%")


def main():
    print("=" * 78)
    print(f"  德方纳米({TARGET}) BWave策略分析")
    print("=" * 78)

    df = get_data(TARGET)
    if df is None or len(df) < 250:
        print("数据不足")
        return
    print(f"历史数据: {fmt_date(df.iloc[0]['trade_date'])} ~ {fmt_date(df.iloc[-1]['trade_date'])} 共{len(df)}根")
    print(f"历史最高: {df['high'].max():.2f}  历史最低: {df['low'].min():.2f}  现价: {df['close'].values[-1]:.2f}")

    # ===== 1. 当前最新信号 =====
    print(f"\n[1] 当前最新交易日BWave信号")
    latest = None
    awave = detect_awave(df)
    if awave:
        print(f"  最新A浪: 起{fmt_date(awave['start_date'])}({awave['start_price']:.2f}) -> "
              f"止{fmt_date(awave['end_date'])}({awave['end_price']:.2f}) 涨幅+{awave['gain']:.1f}%")
        bwave = detect_bwave(df, awave)
        bwave_r = detect_bwave_relaxed(df, awave)
        for bw_label, bw in [('严格B浪', bwave), ('放宽B浪', bwave_r)]:
            if bw is None:
                continue
            print(f"  {bw_label}: 低点{fmt_date(bw['low_date'])}({bw['low_price']:.2f}) "
                  f"回调-{bw['drop']:.1f}% 时长{bw['duration']}天 缩量{bw.get('vol_shrink',0):.2f}")
            launch = check_launch_signal(df, awave, bw)
            if launch:
                score = calc_bwave_score(awave, bw, launch)
                print(f"    启动信号: BWaveScore={score['total']} (需≥65)")
                if score['total'] >= 65:
                    latest = ('launch', score, awave, bw, launch)
                    print(f"    ✅ 触发启动信号!")
                else:
                    print(f"    评分不足65,未触发")
            else:
                print(f"    无启动信号(需MACD金叉/RSI金叉/MA5上穿)")
            div = detect_bwave_divergence(df, awave, bw)
            if div:
                score = calc_divergence_score(awave, bw, div)
                print(f"    底背离: BWaveScore={score['total']} (需≥60)")
                if score['total'] >= 60 and latest is None:
                    latest = ('divergence', score, awave, bw, div)
                    print(f"    ✅ 触发底背离信号!")
            else:
                print(f"    无底背离")
    else:
        print("  未检测到A浪(主升浪)")

    if latest:
        print(f"\n  当前有效信号详情:")
        print_score_breakdown(latest[1], latest[0])
        print_wave_structure(latest[2], latest[3], latest[4])

    # ===== 2. 历史信号回溯 =====
    print(f"\n[2] 逐日扫描历史BWave信号(启动信号≥65/底背离≥60)")
    print(f"    扫描区间: {fmt_date(df.iloc[250]['trade_date'])} ~ {fmt_date(df.iloc[-1]['trade_date'])}")
    hits = scan_bwave_signals(df)
    print(f"    共命中 {len(hits)} 次")

    if not hits:
        print("\n  ⚠ 回测区间内BWave从未选中过德方纳米")
        print("  对比: W2策略在2024-10-25命中(信号分88)")
        print("  说明: BWave的启动信号/底背离过滤,成功避开了德方纳米这个陷阱!")
        return

    # ===== 3. 展示每次命中详情 =====
    print(f"\n[3] BWave信号命中详情")
    seen_dates = set()
    unique_hits = []
    for idx, date, price, sig_type, score, aw, bw, sig in hits:
        if date in seen_dates:
            continue
        seen_dates.add(date)
        unique_hits.append((idx, date, price, sig_type, score, aw, bw, sig))

    for k, (idx, date, price, sig_type, score, aw, bw, sig) in enumerate(unique_hits):
        print(f"\n  ── 命中{k+1} ── 日期 {fmt_date(date)}  收盘 {price:.2f}  BWaveScore {score['total']}")
        print_score_breakdown(score, sig_type)
        print_wave_structure(aw, bw, sig)

        print(f"    选中后走势:")
        for n in [5, 10, 20, 30, 60]:
            if idx + n < len(df):
                future = df.iloc[idx + n]
                ret = (future['close'] / price - 1) * 100
                arrow = '↑' if ret > 0 else '↓'
                print(f"      +{n:>2d}日({fmt_date(future['trade_date'])}): {future['close']:.2f}  {arrow}{ret:+.1f}%")
        if idx + 60 < len(df):
            future_seg = df.iloc[idx:idx+61]
            max_high = future_seg['high'].max()
            max_low = future_seg['low'].min()
            max_up = (max_high / price - 1) * 100
            max_dn = (max_low / price - 1) * 100
            print(f"      选中后60日内: 最高{max_high:.2f}({max_up:+.1f}%) 最低{max_low:.2f}({max_dn:+.1f}%)")

    # ===== 4. 与W2策略命中点对比 =====
    print(f"\n[4] BWave vs W2 对比分析")
    print(f"    W2策略首次命中: 2024-10-25  价格43.75  信号分88")
    print(f"    W2命中后5日: -12.2%  60日: -21.9%  最低: -32.8%(29.38)")
    print(f"    W2首次跌破L2: 2025-01-02  价格35.56")
    if unique_hits:
        first = unique_hits[0]
        print(f"\n    BWave首次命中: {fmt_date(first[1])}  价格{first[2]:.2f}  BWaveScore {first[4]['total']}")
        idx0 = first[0]
        price0 = first[2]
        if idx0 + 60 < len(df):
            future_seg = df.iloc[idx0:idx0+61]
            max_low = future_seg['low'].min()
            max_dn = (max_low / price0 - 1) * 100
            print(f"    BWave命中后60日最低: {max_low:.2f} ({max_dn:+.1f}%)")
    else:
        print(f"\n    BWave从未命中 -> BWave成功避开了W2的陷阱")


if __name__ == '__main__':
    main()
