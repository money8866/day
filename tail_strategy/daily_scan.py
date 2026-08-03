#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
盘后扫描模块 - 按 daily_cache 统一缓存最佳实践扫描指定历史日期的尾盘信号

数据源优先级 (指南三种模式):
  批量日线: daily_cache 表(模式3) → 未命中合并Tushare API写回
  换手/市值: daily_basic_cache 本地表 → 未命中API写回
  技术指标: 由 daily_cache 的 OHLCV 本地计算 (indicators.py)
"""
import time
from datetime import datetime, timedelta
from typing import List, Optional

import pandas as pd

from .data_loader import DataLoader
from .scoring_engine import TailScoringEngine, TailSignal
from .indicators import enrich_indicators

# daily_cache 表覆盖起点
DAILY_CACHE_MIN_DATE = '20250102'


def _default_warmup_start(trade_date: str) -> str:
    """指标预热起点: 扫描日期回退~120自然日(约60交易日), 不早于表起点"""
    try:
        dt = datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=120)
        ws = dt.strftime('%Y%m%d')
    except Exception:
        ws = trade_date
    return ws if ws >= DAILY_CACHE_MIN_DATE else DAILY_CACHE_MIN_DATE


def scan_date(trade_date: str, min_score: float = 50,
              warmup_start: str = None,
              loader: DataLoader = None,
              verbose: bool = True) -> List[TailSignal]:
    """
    扫描指定日期的全市场尾盘信号 (盘后数据)

    Args:
        trade_date: 扫描日期 YYYYMMDD
        min_score: 最低分数(低于此分不输出)
        warmup_start: 指标预热起点(None=自动回退120自然日)
        loader: 可复用的DataLoader实例
        verbose: 输出进度

    Returns: 按总分降序的 TailSignal 列表
    """
    t0 = time.time()
    if warmup_start is None:
        warmup_start = _default_warmup_start(trade_date)
    dl = loader or DataLoader()
    if not dl.theme_stocks:
        dl.load_theme_map()
    codes = list(dl.stock_themes.keys())

    # 名称映射
    name_map = {}
    for theme, stocks in dl.theme_stocks.items():
        for c, n, _ in stocks:
            if c not in name_map:
                name_map[c] = n

    # ═══ 指南模式3: 批量多只股票查询 (daily_cache优先, 未命中合并API写回) ═══
    if verbose:
        print(f'⏳ 批量加载日线 {warmup_start}~{trade_date} ({len(codes)}只)...')
    df_all = dl.load_daily_batch(codes, warmup_start, trade_date)
    if df_all is None or df_all.empty:
        print('❌ 无日线数据')
        return []
    if verbose:
        print(f'✅ 数据加载: {len(df_all)}行, {df_all["ts_code"].nunique()}只, {time.time()-t0:.1f}s')

    # ═══ 换手率/市值/量比: daily_basic (本地表缓存优先, API写回) ═══
    basic = dl.load_daily_basic(trade_date)
    basic_map = {}
    if basic is not None and not basic.empty:
        basic = basic[basic['ts_code'].isin(set(codes))]
        basic_map = basic.set_index('ts_code').to_dict('index')
        if verbose:
            print(f'✅ daily_basic: {len(basic)}只 (换手/市值/量比)')
    elif verbose:
        print('⚠ daily_basic 无数据, 换手/市值过滤将跳过')

    # ═══ 逐只计算指标并评分 ═══
    engine = TailScoringEngine()
    signals = []
    scored = 0

    for code, grp in df_all.groupby('ts_code'):
        grp = grp.sort_values('trade_date').reset_index(drop=True)
        if not (grp['trade_date'] == trade_date).any():
            continue
        hist = grp[grp['trade_date'] < trade_date]
        if len(hist) < 20:
            continue

        grp_ind = enrich_indicators(grp)
        row = grp_ind[grp_ind['trade_date'] == trade_date].iloc[-1]

        factor_row = row.copy()
        b = basic_map.get(code, {})
        for k in ('turnover_rate', 'volume_ratio', 'total_mv'):
            factor_row[k] = b.get(k, None)

        scored += 1
        sig = engine.score_stock(
            code, name_map.get(code, ''), row, hist, factor_row=factor_row,
            theme_stocks=dl.theme_stocks,
            stock_themes=dl.stock_themes,
            trade_date=trade_date
        )
        if sig and sig.total_score >= min_score:
            signals.append(sig)

    signals.sort(key=lambda s: s.total_score, reverse=True)
    if verbose:
        print(f'✅ {trade_date} 信号: {len(signals)}只 (评分{scored}只, 耗时{time.time()-t0:.1f}s)')
    return signals


def print_signals(signals: List[TailSignal], top_n: int = 40):
    """打印信号表格"""
    header = (f'{"代码":<12}{"名称":<10}{"主题":<10}{"信号":<6}{"总分":<6}'
              f'{"攻击":<5}{"结构":<5}{"位置":<5}{"技术":<5}{"主题":<5}{"资金":<5}'
              f'{"扣分":<5}{"涨幅%":<8}{"价格"}')
    print(header)
    print('-' * 120)
    for s in signals[:top_n]:
        print(f'{s.ts_code:<12}{s.name:<10}{s.theme:<10}{s.signal:<6}{s.total_score:<6.0f}'
              f'{s.attack_score:<5.0f}{s.structure_score:<5.0f}{s.position_score:<5.0f}'
              f'{s.technical_score:<5.0f}{s.theme_score:<5.0f}{s.capital_score:<5.0f}'
              f'{s.trap_penalty:<5.0f}{s.pct_chg:<+8.2f}{s.price:.2f}')
