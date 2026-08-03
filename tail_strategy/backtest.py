#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
回测引擎 - 基于历史数据验证尾盘战法胜率

回测逻辑:
1. 遍历历史交易日, 对每日所有主题内股票进行尾盘评分
2. 筛选出满足条件的信号(强买入/买入)
3. 计算次日收益(开盘买入/收盘卖出)及持有N日收益
4. 统计胜率、盈亏比、最大回撤等指标
"""
import os
import sys
import time
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .data_loader import DataLoader, get_last_trade_date, PARQUET_DIR, STOCK_DATA_DB, CACHE_DAILY
from .scoring_engine import TailScoringEngine, TailSignal
from .indicators import enrich_indicators


@dataclass
class BacktestResult:
    """单次信号回测结果"""
    signal: TailSignal
    next_open: float = 0.0
    next_close: float = 0.0
    next_high: float = 0.0
    next_low: float = 0.0
    next_pct: float = 0.0       # 次日涨跌幅(收盘vs昨收)
    open_pct: float = 0.0       # 次日开盘涨幅(开盘vs昨收)
    hold_1d: float = 0.0        # 持有1日收益(次日收盘买/卖)
    hold_3d: float = 0.0        # 持有3日收益
    hold_5d: float = 0.0        # 持有5日收益
    max_gain: float = 0.0       # 持有期内最大浮盈
    max_drawdown: float = 0.0   # 持有期内最大回撤
    win: bool = False           # 是否盈利


@dataclass
class BacktestStats:
    """回测统计"""
    total_signals: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    avg_return: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    max_consecutive_win: int = 0
    max_consecutive_loss: int = 0
    avg_hold_3d: float = 0.0
    avg_hold_5d: float = 0.0
    avg_max_gain: float = 0.0
    avg_max_drawdown: float = 0.0
    # 按分数段统计
    score_bins: Dict = field(default_factory=dict)
    # 按信号类型统计
    signal_type_stats: Dict = field(default_factory=dict)


class BacktestEngine:
    """
    尾盘战法回测引擎
    
    使用项目已有的 parquet 日线数据 + stock_data.db 技术因子
    无需额外下载数据, 直接复用缓存
    
    性能优化: 预加载全部parquet到内存, 避免逐只IO
    """

    def __init__(self, min_score: float = 65, top_n: int = 10):
        """
        Args:
            min_score: 最低信号分数(默认65=买入级)
            top_n: 每日最多选取信号数(控制仓位)
        """
        self.min_score = min_score
        self.top_n = top_n
        self.loader = DataLoader()
        self.engine = TailScoringEngine()
        self.results: List[BacktestResult] = []
        self._all_daily: Dict[str, pd.DataFrame] = {}  # 预加载的全量日线

    def run(self, start_date: str = '20250101', end_date: str = None,
            codes: List[str] = None, verbose: bool = True) -> BacktestStats:
        """
        执行回测
        
        Args:
            start_date: 回测起始日
            end_date: 回测结束日
            codes: 限定股票池(None=使用主题内所有股票)
            verbose: 是否输出进度
        """
        if end_date is None:
            end_date = get_last_trade_date()

        # 加载主题映射
        if not self.loader.load_theme_map():
            print("❌ 无法加载主题映射, 回测终止")
            return BacktestStats()

        # 确定股票池
        if codes is None:
            codes = list(self.loader.stock_themes.keys())
        if verbose:
            print(f"📊 回测参数: {start_date} ~ {end_date}, 股票池 {len(codes)} 只, 最低分 {self.min_score}")

        # 按指南加载数据: daily_cache 优先 (模式3批量查询), API兜底写回
        if verbose:
            print(f"⏳ 从 daily_cache 加载日线数据(含指标计算)...")
        self._load_all_from_daily_cache(codes, start_date, end_date, verbose)
        if verbose:
            print(f"✅ 数据加载完成: {len(self._all_daily)} 只股票")

        # 获取交易日列表
        trade_dates = self._get_trade_dates(start_date, end_date)
        if not trade_dates:
            print("❌ 无交易日数据")
            return BacktestStats()

        if verbose:
            print(f"📅 交易日: {len(trade_dates)} 天 ({trade_dates[0]} ~ {trade_dates[-1]})")

        # 逐日加载 daily_basic(换手/市值/量比), 本地表缓存优先
        self._basic_by_date: Dict[str, Dict] = {}
        if verbose:
            print(f"⏳ 加载 daily_basic(换手/市值)...")
        for td in trade_dates:
            bdf = self.loader.load_daily_basic(td)
            if bdf is not None and not bdf.empty:
                self._basic_by_date[td] = bdf.set_index('ts_code').to_dict('index')

        # 逐日回测
        self.results = []
        for i, td in enumerate(trade_dates):
            if verbose and (i + 1) % 10 == 0:
                print(f"  进度: {i+1}/{len(trade_dates)} ({td}) 已产生 {len(self.results)} 个信号")

            # 当日信号
            signals = self._scan_day(td, codes)
            if not signals:
                continue

            # 取TOP N
            signals.sort(key=lambda s: s.total_score, reverse=True)
            signals = signals[:self.top_n]

            # 计算次日收益
            for sig in signals:
                bt = self._calc_next_day_return(sig, td, trade_dates)
                if bt:
                    self.results.append(bt)

        # 统计
        stats = self._calc_stats()
        if verbose:
            self._print_stats(stats)
        return stats

    def _load_all_from_daily_cache(self, codes: List[str], start: str, end: str, verbose: bool = True):
        """
        按 daily_cache 统一缓存指南加载数据 (模式3: 批量多只股票查询)
        daily_cache 优先 → 未命中合并API写回; 技术指标由 OHLCV 本地计算
        """
        from datetime import datetime as _dt, timedelta as _td

        # 向前多取~60个自然日用于指标预热(MACD/KDJ/MA20)
        try:
            dt_start = _dt.strptime(start, '%Y%m%d') - _td(days=90)
            ext_start = dt_start.strftime('%Y%m%d')
        except Exception:
            ext_start = start

        # daily_cache 表覆盖范围自20250102起
        if ext_start < '20250102':
            ext_start = '20250102'

        all_df = self.loader.load_daily_batch(codes, ext_start, end)
        if all_df is None or all_df.empty:
            print("❌ daily_cache 无数据")
            return

        if verbose:
            dmin = all_df['trade_date'].min()
            dmax = all_df['trade_date'].max()
            print(f"    日线: {len(all_df)}行, {all_df['ts_code'].nunique()}只 ({dmin} ~ {dmax})")

        # 逐只计算技术指标并组装
        loaded = 0
        for code, grp in all_df.groupby('ts_code'):
            grp = grp.sort_values('trade_date').reset_index(drop=True)
            if len(grp) < 20:
                continue
            grp = enrich_indicators(grp)
            self._all_daily[code] = grp
            loaded += 1

        if verbose:
            print(f"    组装完成: {loaded}只股票有足够数据")

    def _preload_daily(self, codes: List[str], verbose: bool = True):
        """(备用)批量预加载parquet日线到内存"""
        import glob as _glob
        loaded = 0
        total = len(codes)
        for i, ts_code in enumerate(codes):
            code_part = ts_code.replace('.', '_')
            pattern = os.path.join(PARQUET_DIR, f"daily_code_{code_part}_*.parquet")
            files = _glob.glob(pattern)
            if files:
                try:
                    df = pd.read_parquet(files[0])
                    df['trade_date'] = df['trade_date'].astype(str)
                    df = df.sort_values('trade_date').reset_index(drop=True)
                    self._all_daily[ts_code] = df
                    loaded += 1
                except Exception:
                    pass
            if verbose and (i + 1) % 500 == 0:
                print(f"    日线加载: {i+1}/{total} ({loaded}只成功)")

    def _get_trade_dates(self, start: str, end: str) -> List[str]:
        """从预加载数据推断交易日(取数据最丰富的股票)"""
        best_dates = []
        for df in self._all_daily.values():
            dates = sorted(df['trade_date'].unique())
            if len(dates) > len(best_dates):
                best_dates = dates
        if best_dates:
            return [d for d in best_dates if start <= d <= end]
        # 回退: 从因子表获取
        dates = self.loader.get_factor_dates(limit=9000)
        return sorted([d for d in dates if start <= d <= end])

    def _scan_day(self, trade_date: str, codes: List[str]) -> List[TailSignal]:
        """扫描单日所有股票的尾盘信号(使用预加载数据)"""
        signals = []

        # 逐只评分(使用内存中的日线+因子数据)
        for ts_code in codes:
            if ts_code not in self.loader.stock_themes:
                continue

            daily_df = self._all_daily.get(ts_code)
            if daily_df is None or len(daily_df) < 25:
                continue

            # 当日行
            day_mask = daily_df['trade_date'] == trade_date
            if not day_mask.any():
                continue
            row = daily_df[day_mask].iloc[0]

            # 历史日线(不含当日)
            hist_df = daily_df[daily_df['trade_date'] < trade_date]
            if len(hist_df) < 20:
                continue

            # 获取名称
            name = ''
            for theme, stocks in self.loader.theme_stocks.items():
                for code, n, _ in stocks:
                    if code == ts_code:
                        name = n
                        break
                if name:
                    break

            # 因子行: 技术指标(本地计算) + daily_basic(换手/市值/量比)
            factor_row = row
            basic_map = getattr(self, '_basic_by_date', {}).get(trade_date)
            if basic_map:
                b = basic_map.get(ts_code)
                if b:
                    factor_row = row.copy()
                    factor_row['turnover_rate'] = b.get('turnover_rate', None)
                    factor_row['volume_ratio'] = b.get('volume_ratio', None)
                    factor_row['total_mv'] = b.get('total_mv', None)

            sig = self.engine.score_stock(
                ts_code=ts_code,
                name=name,
                row=row,
                daily_df=hist_df,
                factor_row=factor_row,
                theme_stocks=self.loader.theme_stocks,
                stock_themes=self.loader.stock_themes,
                all_quotes=None,
                trade_date=trade_date,
            )

            if sig and sig.total_score >= self.min_score:
                signals.append(sig)

        return signals

    def _calc_next_day_return(self, sig: TailSignal, signal_date: str,
                              trade_dates: List[str]) -> Optional[BacktestResult]:
        """计算信号次日及持有期收益(使用预加载数据)"""
        # 找到信号日在交易日列表中的位置
        try:
            idx = trade_dates.index(signal_date)
        except ValueError:
            return None

        if idx + 1 >= len(trade_dates):
            return None  # 无次日数据

        next_date = trade_dates[idx + 1]

        # 从预加载数据获取
        daily_df = self._all_daily.get(sig.ts_code)
        if daily_df is None:
            return None

        next_rows = daily_df[daily_df['trade_date'] == next_date]
        if next_rows.empty:
            return None

        next_row = next_rows.iloc[0]
        bt = BacktestResult(signal=sig)

        bt.next_open = float(next_row.get('open', 0))
        bt.next_close = float(next_row.get('close', 0))
        bt.next_high = float(next_row.get('high', 0))
        bt.next_low = float(next_row.get('low', 0))
        bt.next_pct = float(next_row.get('pct_chg', 0))

        # 开盘涨幅(信号日收盘 -> 次日开盘)
        if sig.price > 0:
            bt.open_pct = (bt.next_open - sig.price) / sig.price * 100

        # 持有收益(以次日开盘价为买入价)
        if bt.next_open > 0:
            bt.hold_1d = (bt.next_close - bt.next_open) / bt.next_open * 100

            # 持有3日
            if idx + 4 < len(trade_dates):
                date_3d = trade_dates[idx + 4]
                rows_3d = daily_df[daily_df['trade_date'] == date_3d]
                if not rows_3d.empty:
                    close_3d = float(rows_3d.iloc[0]['close'])
                    bt.hold_3d = (close_3d - bt.next_open) / bt.next_open * 100

            # 持有5日
            if idx + 6 < len(trade_dates):
                date_5d = trade_dates[idx + 6]
                rows_5d = daily_df[daily_df['trade_date'] == date_5d]
                if not rows_5d.empty:
                    close_5d = float(rows_5d.iloc[0]['close'])
                    bt.hold_5d = (close_5d - bt.next_open) / bt.next_open * 100

            # 最大浮盈/回撤(持有期内)
            hold_dates = trade_dates[idx + 1: idx + 6]
            hold_data = daily_df[daily_df['trade_date'].isin(hold_dates)]
            if not hold_data.empty:
                highs = hold_data['high'].values.astype(float)
                lows = hold_data['low'].values.astype(float)
                bt.max_gain = (highs.max() - bt.next_open) / bt.next_open * 100
                bt.max_drawdown = (lows.min() - bt.next_open) / bt.next_open * 100

        # 胜负判定: 次日收盘 > 次日开盘 为赢
        bt.win = bt.hold_1d > 0

        return bt

    def _calc_stats(self) -> BacktestStats:
        """计算回测统计指标"""
        stats = BacktestStats()
        if not self.results:
            return stats

        stats.total_signals = len(self.results)
        wins = [r for r in self.results if r.win]
        losses = [r for r in self.results if not r.win]

        stats.win_count = len(wins)
        stats.loss_count = len(losses)
        stats.win_rate = len(wins) / len(self.results) * 100

        hold_1d_returns = [r.hold_1d for r in self.results]
        stats.avg_return = np.mean(hold_1d_returns)

        if wins:
            stats.avg_win = np.mean([r.hold_1d for r in wins])
        if losses:
            stats.avg_loss = np.mean([r.hold_1d for r in losses])

        # 盈亏比
        total_profit = sum(r.hold_1d for r in wins) if wins else 0
        total_loss = abs(sum(r.hold_1d for r in losses)) if losses else 1
        stats.profit_factor = total_profit / total_loss if total_loss > 0 else 999

        # 最大连胜/连败
        stats.max_consecutive_win = self._max_streak(True)
        stats.max_consecutive_loss = self._max_streak(False)

        # 持有期收益
        h3 = [r.hold_3d for r in self.results if r.hold_3d != 0]
        h5 = [r.hold_5d for r in self.results if r.hold_5d != 0]
        stats.avg_hold_3d = np.mean(h3) if h3 else 0
        stats.avg_hold_5d = np.mean(h5) if h5 else 0
        stats.avg_max_gain = np.mean([r.max_gain for r in self.results])
        stats.avg_max_drawdown = np.mean([r.max_drawdown for r in self.results])

        # 按分数段统计
        bins = [(50, 65), (65, 75), (75, 85), (85, 100), (100, 999)]
        for lo, hi in bins:
            subset = [r for r in self.results if lo <= r.signal.total_score < hi]
            if subset:
                wr = sum(1 for r in subset if r.win) / len(subset) * 100
                avg = np.mean([r.hold_1d for r in subset])
                stats.score_bins[f'{lo}-{hi}'] = {
                    'count': len(subset), 'win_rate': round(wr, 1), 'avg_return': round(avg, 2)
                }

        # 按信号类型统计
        for sig_type in ('强买入', '买入', '关注'):
            subset = [r for r in self.results if r.signal.signal == sig_type]
            if subset:
                wr = sum(1 for r in subset if r.win) / len(subset) * 100
                avg = np.mean([r.hold_1d for r in subset])
                stats.signal_type_stats[sig_type] = {
                    'count': len(subset), 'win_rate': round(wr, 1), 'avg_return': round(avg, 2)
                }

        return stats

    def _max_streak(self, is_win: bool) -> int:
        """计算最大连胜/连败"""
        max_streak = 0
        current = 0
        for r in self.results:
            if r.win == is_win:
                current += 1
                max_streak = max(max_streak, current)
            else:
                current = 0
        return max_streak

    def _print_stats(self, stats: BacktestStats):
        """输出回测统计"""
        print(f"\n{'='*70}")
        print(f"📊 尾盘战法回测报告")
        print(f"{'='*70}")
        print(f"  信号总数: {stats.total_signals}")
        print(f"  胜率: {stats.win_rate:.1f}% ({stats.win_count}胜 / {stats.loss_count}负)")
        print(f"  平均收益: {stats.avg_return:.2f}%")
        print(f"  平均盈利: {stats.avg_win:.2f}%  平均亏损: {stats.avg_loss:.2f}%")
        print(f"  盈亏比: {stats.profit_factor:.2f}")
        print(f"  最大连胜: {stats.max_consecutive_win}  最大连败: {stats.max_consecutive_loss}")
        print(f"  持有3日均收益: {stats.avg_hold_3d:.2f}%  持有5日均收益: {stats.avg_hold_5d:.2f}%")
        print(f"  平均最大浮盈: {stats.avg_max_gain:.2f}%  平均最大回撤: {stats.avg_max_drawdown:.2f}%")

        if stats.score_bins:
            print(f"\n  📈 分数段胜率:")
            for bin_name, data in sorted(stats.score_bins.items()):
                print(f"    {bin_name}分: {data['count']}次 胜率{data['win_rate']}% 均收益{data['avg_return']}%")

        if stats.signal_type_stats:
            print(f"\n  📈 信号类型统计:")
            for sig_type, data in stats.signal_type_stats.items():
                print(f"    {sig_type}: {data['count']}次 胜率{data['win_rate']}% 均收益{data['avg_return']}%")

        print(f"{'='*70}\n")

    def save_results(self, db_path: str = None):
        """保存回测结果到SQLite"""
        if db_path is None:
            db_path = os.path.join(CACHE_DAILY, 'tail_backtest_result.db')

        conn = sqlite3.connect(db_path)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS backtest_signals (
                signal_date TEXT,
                ts_code TEXT,
                name TEXT,
                theme TEXT,
                signal TEXT,
                total_score REAL,
                attack_score REAL,
                structure_score REAL,
                position_score REAL,
                technical_score REAL,
                theme_score REAL,
                capital_score REAL,
                trap_penalty REAL,
                pct_chg REAL,
                price REAL,
                next_open REAL,
                next_close REAL,
                next_pct REAL,
                open_pct REAL,
                hold_1d REAL,
                hold_3d REAL,
                hold_5d REAL,
                max_gain REAL,
                max_drawdown REAL,
                win INTEGER,
                PRIMARY KEY (signal_date, ts_code)
            )
        ''')

        for r in self.results:
            s = r.signal
            conn.execute('''
                INSERT OR REPLACE INTO backtest_signals VALUES (
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                )
            ''', (
                s.trade_date, s.ts_code, s.name, s.theme, s.signal,
                s.total_score, s.attack_score, s.structure_score,
                s.position_score, s.technical_score, s.theme_score,
                s.capital_score, s.trap_penalty, s.pct_chg, s.price,
                r.next_open, r.next_close, r.next_pct, r.open_pct,
                r.hold_1d, r.hold_3d, r.hold_5d,
                r.max_gain, r.max_drawdown, int(r.win),
            ))

        conn.commit()
        conn.close()
        print(f"✅ 回测结果已保存: {db_path} ({len(self.results)}条)")
