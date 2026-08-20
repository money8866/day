#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
「猎尾V5」实时数据独立运行脚本
用当前实时行情跑 V5 ND2 引擎(不推送/不写库,纯只读测试)

关键: 用新浪5分钟K线重建 14:00/14:30 分时锚点(独立进程无主进程的快照积累)
"""
import sys
import time
import traceback
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

sys.path.insert(0, r'd:\mystock\solo')


def ts_to_sina(ts_code):
    """600000.SH -> sh600000"""
    code, suffix = ts_code.split('.')
    return ('sh' if suffix == 'SH' else 'sz') + code


def fetch_minute_bars(ts_code, today_str):
    """拉新浪5分钟K线,返回今天的bar列表"""
    sym = ts_to_sina(ts_code)
    url = (f'https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20t=/CN_MarketDataService.getKLineData'
           f'?symbol={sym}&scale=5&ma=no&datalen=48')
    try:
        r = requests.get(url, timeout=8, headers={'Referer': 'https://finance.sina.com.cn'})
        txt = r.text
        # JSONP剥壳: "var t=[{...},...]" 或 "[{...}]"
        s = txt.find('[')
        e = txt.rfind(']')
        if s < 0 or e < 0:
            return None
        import json
        bars = json.loads(txt[s:e + 1])
        return [b for b in bars if str(b.get('day', '')).startswith(today_str)]
    except Exception:
        return None


def rebuild_snap(q, bars, now):
    """
    从5分钟K线重建分时锚点snap(含时间归一化)
    - morning_vol: 11:30累计(真实)
    - noon_vol / tail_base_vol: 虚拟锚点,使尾盘量比归一到20分钟标准
    - tail_base_price: 14:30真实价格
    """
    if not bars:
        return None
    cum = 0
    morning_vol = noon_vol = tail_base_vol = 0
    tail_base_price = None
    for b in bars:
        v = float(b.get('volume', 0))
        t = str(b.get('day', ''))[11:16]
        cum += v
        if t <= '11:30':
            morning_vol = cum
        if t <= '14:00':
            noon_vol = cum
        if t <= '14:30':
            tail_base_vol = cum
            tail_base_price = float(b.get('close', 0))

    # 新浪实时vol与5分钟K线volume单位均为"股", 保持股单位(与主进程快照一致)
    cur_vol = float(q.get('vol', 0))
    if tail_base_vol <= 0 or cur_vol <= tail_base_vol or not tail_base_price:
        return None

    tail_inc = cur_vol - tail_base_vol                      # 14:30~现在真实增量
    noon_to_tail = max(tail_base_vol - noon_vol, 1)         # 14:00~14:30真实量

    # 时间归一化: 尾盘已进行 minutes_elapsed 分钟,外推到标准20分钟
    minutes_elapsed = max((now.hour - 14) * 60 + now.minute - 30, 1)
    minutes_elapsed = min(minutes_elapsed, 20)
    virtual_tail_inc = tail_inc * 20.0 / minutes_elapsed
    # 虚拟锚点: 使 ratio = virtual_tail_inc / noon_to_tail
    virtual_tail_base = cur_vol - virtual_tail_inc
    if virtual_tail_base <= noon_vol:
        virtual_tail_base = noon_vol + 1

    return {
        # 快照vol为"股"单位(与主进程collect_intraday_snapshot一致), precompute再统一转手
        'morning_vol': morning_vol,
        'noon_vol': noon_vol,
        'tail_base_vol': virtual_tail_base,   # 虚拟锚点(归一化)
        'tail_base_price': tail_base_price,   # 真实14:30价
        'noon_pct': None,
        'tail_base_pct': None,
        '_tail_inc_real': tail_inc,
        '_noon_to_tail': noon_to_tail,
    }


def main():
    t_start = time.time()
    now = datetime.now()
    today_str = now.strftime('%Y-%m-%d')
    print(f"🕐 启动时间: {now.strftime('%H:%M:%S')}")

    # ── 1. 初始化监控器(复用缓存: K线/主题/换手/市值) ──
    print("\n[1/5] 初始化监控器(加载缓存)...")
    import realtime_theme_monitor as rtm
    monitor = rtm.RealtimeThemeMonitor()
    # 复用主循环run()的完整初始化序列(不进循环)
    monitor.load_theme_db()
    monitor.load_ref_prices()
    monitor.load_index_klines()
    monitor.load_component_klines()
    monitor.load_turnover_cache()
    monitor.load_stock_factors_cache()
    print(f"   主题数: {len(monitor.theme_stocks)} | 股票数: {len(monitor.stock_themes)} | "
          f"K线: {len(monitor.stock_klines)}只 | 耗时{time.time()-t_start:.0f}s")

    # ── 2. 拉实时行情 ──
    print("\n[2/5] 拉取实时行情...")
    monitor.fetch_all_quotes()
    n_valid = sum(1 for q in monitor.quotes.values() if q.get('price', 0) > 0)
    print(f"   行情: {n_valid}只有效")

    # ── 3. 粗筛(涨幅0.5~8%, K线>=21天, 市值>=8亿, 非北交所) ──
    print("\n[3/5] 粗筛候选...")
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
    # 按量能排序(今日量/昨日量优先,尾盘引擎核心)
    def vol_ratio_key(ts):
        q = monitor.quotes.get(ts, {})
        kl = monitor.stock_klines.get(ts)
        try:
            yv = float(kl['vol'].iloc[-1])
            return -(q.get('vol', 0) / yv) if yv > 0 else 0
        except Exception:
            return 0
    candidates.sort(key=vol_ratio_key)
    max_n = 250
    candidates = candidates[:max_n]
    print(f"   粗筛通过: {len(candidates)}只 (量能排序,上限{max_n})")

    # ── 4. 并发拉5分钟K线重建分时锚点 ──
    print(f"\n[4/5] 重建分时锚点(新浪5分钟K线, 8线程)...")
    t4 = time.time()
    rebuilt = 0
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
                    rebuilt += 1
            except Exception:
                continue
    print(f"   锚点重建: {rebuilt}/{len(candidates)}只 | 耗时{time.time()-t4:.0f}s")

    # ── 5. 跑V5引擎 ──
    print(f"\n[5/5] 运行「猎尾V5」ND2引擎...")
    t5 = time.time()
    # 生成市场情绪报告(L0市场乘数依赖)
    monitor.nd2_debug_printed = False   # 强制打印首次诊断
    try:
        monitor.compute_market_sentiment_report()
    except Exception as e:
        print(f"   ⚠ 市场情绪报告异常(用默认值): {e}")

    signals = monitor.scan_nd2_alpha()
    print(f"\n{'='*70}")
    print(f"🏁 V5扫描完成: {len(signals)}只候选 | 引擎耗时{time.time()-t5:.0f}s | 总耗时{time.time()-t_start:.0f}s")
    print(f"{'='*70}")

    if signals:
        from nd2_report import format_console_report
        print(format_console_report(signals, top_n=15))

        # 汇总
        grades = {}
        for s in signals:
            grades[s['grade']] = grades.get(s['grade'], 0) + 1
        print(f"\n📊 分级汇总: {grades}")
        sa = [s for s in signals if s['grade'] in ('S', 'A')]
        if sa:
            print(f"\n🎯 S/A级信号(实盘可操作):")
            for s in sa:
                print(f"  {s['grade']}级 {s['name']}({s['ts_code']}) {s['final_score']}分 "
                      f"P(次日高≥2%)={s['p_up_2']:.0%} 形态:{s['pattern']}")
        else:
            print("\n(今日无S/A级信号 - 宁缺毋滥)")
    else:
        print("\n(无候选信号)")

    return signals


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
