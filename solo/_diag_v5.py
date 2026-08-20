#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""诊断V5: 输出得分最高的REJECT股票的完整分项,定位卡点"""
import sys
import time
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, r'd:\mystock\solo')

from _run_v5_now import fetch_minute_bars, rebuild_snap, ts_to_sina
import realtime_theme_monitor as rtm


def main():
    t0 = time.time()
    now = datetime.now()
    today_str = now.strftime('%Y-%m-%d')

    monitor = rtm.RealtimeThemeMonitor()
    monitor.load_theme_db()
    monitor.load_ref_prices()
    monitor.load_index_klines()
    monitor.load_component_klines()
    monitor.load_turnover_cache()
    monitor.load_stock_factors_cache()

    monitor.fetch_all_quotes()

    # 粗筛同主脚本
    candidates = []
    for ts_code, themes in monitor.stock_themes.items():
        if not themes or ts_code.startswith(('8', '4', '92')):
            continue
        q = monitor.quotes.get(ts_code)
        if not q or q.get('price', 0) <= 0:
            continue
        pct = q.get('pct_chg', 0)
        if not (0.5 <= pct <= 8.0):
            continue
        kl = monitor.stock_klines.get(ts_code)
        if kl is None or len(kl) < 21:
            continue
        mv = monitor.stock_mv.get(ts_code, 0) if monitor.stock_mv else 0
        if mv and 0 < mv < 80000:
            continue
        candidates.append(ts_code)

    def vol_ratio_key(ts):
        q = monitor.quotes.get(ts, {})
        kl = monitor.stock_klines.get(ts)
        try:
            yv = float(kl['vol'].iloc[-1])
            return -(q.get('vol', 0) / yv) if yv > 0 else 0
        except Exception:
            return 0
    candidates.sort(key=vol_ratio_key)
    candidates = candidates[:250]
    print(f"候选: {len(candidates)}只 | 初始化+行情耗时{time.time()-t0:.0f}s")

    # 重建锚点
    from concurrent.futures import ThreadPoolExecutor, as_completed
    monitor.intraday_snapshots = {}

    def worker(ts_code):
        q = monitor.quotes.get(ts_code)
        bars = fetch_minute_bars(ts_code, today_str)
        snap = rebuild_snap(q, bars, datetime.now()) if bars else None
        return ts_code, snap

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(worker, ts): ts for ts in candidates}
        for fu in as_completed(futures):
            try:
                ts_code, snap = fu.result(timeout=15)
                if snap:
                    monitor.intraday_snapshots[ts_code] = snap
            except Exception:
                continue
    print(f"锚点: {len(monitor.intraday_snapshots)}只 | 总耗时{time.time()-t0:.0f}s")

    # 市场情绪
    monitor.compute_market_sentiment_report()

    # 逐股评估(收集所有结果含REJECT)
    results = []
    for ts_code in candidates:
        if ts_code not in monitor.intraday_snapshots:
            continue
        themes = monitor.stock_themes.get(ts_code, [])
        if not themes:
            continue
        theme_name = themes[0]
        q = monitor.quotes.get(ts_code)
        kl = monitor.stock_klines.get(ts_code)
        import pandas as pd
        if kl is not None and len(kl) > 0:
            for _col in ('open', 'high', 'low', 'close', 'pct_chg', 'vol', 'amount'):
                if _col in kl.columns:
                    kl[_col] = pd.to_numeric(kl[_col], errors='coerce').fillna(0)
        snap = monitor.intraday_snapshots[ts_code]
        stock_name = ''
        for code, n, _ in monitor.theme_stocks.get(theme_name, []):
            if code == ts_code:
                stock_name = n
                break
        q2 = dict(q)
        q2['name'] = stock_name
        try:
            sig = monitor.nd2_engine.evaluate(
                ts_code=ts_code, q=q2, kline=kl, snap=snap,
                turnover=monitor.turnover_cache.get(ts_code, 0),
                total_mv=monitor.stock_mv.get(ts_code, 0),
                theme_name=theme_name,
                trend_score=getattr(monitor, '_last_report', {}).get('trend_score', 60) if monitor._last_report else 60,
                market_status=getattr(monitor, '_last_report', {}).get('market_status', '') if monitor._last_report else '',
            )
        except Exception as e:
            continue
        if sig:
            results.append(sig)

    # NONE的被过滤了,按分数排序看TOP15
    results.sort(key=lambda s: -s.get('final_score', 0))
    print(f"\n评估出信号: {len(results)}只 (None被L1/L2过滤)")
    print(f"\n{'='*80}")
    print(f"TOP15 得分明细:")
    print(f"{'='*80}")
    for i, s in enumerate(results[:15], 1):
        print(f"\n{i:2}. {s['name']}({s['ts_code']}) [{s.get('theme','')}] "
              f"{s['final_score']}分 {s['grade']}级 {s['pattern']}")
        print(f"    趋势{s['trend_structure']}/15 形态{s['pattern_quality']}/15 尾流{s['tail_flow']}/25 "
              f"基因{s['strong_gene']}/10 ND2{s['nd2_potential']}/15 "
              f"主题{s['theme_alpha']}/12 市场{s['market_alpha']}/8 "
              f"+{s['bonus']} -{s['risk_penalty']}")
        tf = s['detail'].get('tailflow', {})
        print(f"    尾盘详情: 量比{tf.get('tail_volume_ratio')} 涨{tf.get('tail_return')}% "
              f"收盘位{tf.get('close_position')} 买压{tf.get('buy_pressure_proxy')}")

    # 分项平均(诊断系统性卡点)
    if results:
        n = len(results)
        print(f"\n{'='*80}")
        print(f"全样本({n}只)分项均值:")
        print(f"  趋势 {sum(s['trend_structure'] for s in results)/n:.1f}/15")
        print(f"  形态 {sum(s['pattern_quality'] for s in results)/n:.1f}/15")
        print(f"  尾流 {sum(s['tail_flow'] for s in results)/n:.1f}/25  <- V5核心")
        print(f"  基因 {sum(s['strong_gene'] for s in results)/n:.1f}/10")
        print(f"  ND2  {sum(s['nd2_potential'] for s in results)/n:.1f}/15")
        print(f"  主题 {sum(s['theme_alpha'] for s in results)/n:.1f}/12")
        print(f"  市场 {sum(s['market_alpha'] for s in results)/n:.1f}/8")
        print(f"  风险扣分均值 {sum(s['risk_penalty'] for s in results)/n:.1f}")
        print(f"  总分均值 {sum(s['final_score'] for s in results)/n:.1f}")
        print(f"{'='*80}")


if __name__ == '__main__':
    main()
