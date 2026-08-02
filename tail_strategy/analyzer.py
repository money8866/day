#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
胜率分析 & 信号跟踪模块

功能:
1. 回填历史信号的次日/多日收益(盘后自动更新)
2. 统计各维度因子的胜率贡献
3. 输出胜率报告(按分数段/主题/技术形态分组)
4. 优化建议: 哪些因子组合胜率最高
"""
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

import numpy as np
import pandas as pd

from .data_loader import DataLoader, get_last_trade_date, CACHE_DAILY


class WinRateAnalyzer:
    """胜率分析器"""

    def __init__(self):
        self.loader = DataLoader()
        self.tracker_db = os.path.join(CACHE_DAILY, 'tail_signal_tracker.db')

    # ═══════════════════════════════════════════
    # 信号回填 (盘后运行)
    # ═══════════════════════════════════════════
    def backfill_signals(self, days_back: int = 30):
        """
        回填pending状态信号的次日收益
        从parquet日线数据获取次日开盘/收盘/最高/最低
        """
        if not os.path.exists(self.tracker_db):
            print("❌ 信号跟踪表不存在")
            return

        conn = sqlite3.connect(self.tracker_db)
        pending = conn.execute(
            "SELECT signal_date, ts_code, price FROM tail_signal_tracker WHERE status='pending'"
        ).fetchall()

        if not pending:
            print("✅ 无待回填信号")
            conn.close()
            return

        print(f"⏳ 回填 {len(pending)} 条待处理信号...")
        updated = 0

        for signal_date, ts_code, signal_price in pending:
            # 加载日线
            daily_df = self.loader.load_daily(ts_code, start=signal_date)
            if daily_df is None or daily_df.empty:
                continue

            daily_df['trade_date'] = daily_df['trade_date'].astype(str)
            # 找信号日之后的数据
            future = daily_df[daily_df['trade_date'] > signal_date].sort_values('trade_date')
            if future.empty:
                continue

            # 次日数据
            next_row = future.iloc[0]
            next_open = float(next_row['open'])
            next_close = float(next_row['close'])
            next_high = float(next_row['high'])
            next_low = float(next_row['low'])
            next_pct = float(next_row.get('pct_chg', 0))

            # 持有期收益(以信号日收盘价为基准)
            base_price = signal_price if signal_price and signal_price > 0 else float(
                daily_df[daily_df['trade_date'] == signal_date]['close'].iloc[0]
            ) if not daily_df[daily_df['trade_date'] == signal_date].empty else 0

            if base_price <= 0:
                continue

            # 5日/10日收益
            next_5d_pct = None
            next_10d_pct = None
            if len(future) >= 5:
                next_5d_pct = (float(future.iloc[4]['close']) - base_price) / base_price * 100
            if len(future) >= 10:
                next_10d_pct = (float(future.iloc[9]['close']) - base_price) / base_price * 100

            # 最大浮盈/回撤(5日内)
            hold_5 = future.head(5)
            max_gain = (hold_5['high'].max() - base_price) / base_price * 100
            max_drawdown = (hold_5['low'].min() - base_price) / base_price * 100

            # 盈亏
            pnl = (next_close - base_price) / base_price * 100

            conn.execute('''
                UPDATE tail_signal_tracker SET
                    next_open=?, next_close=?, next_pct_chg=?,
                    next_high=?, next_low=?,
                    next_5d_pct=?, next_10d_pct=?,
                    max_gain=?, max_drawdown=?,
                    pnl=?, status='done', updated_at=?
                WHERE signal_date=? AND ts_code=?
            ''', (
                next_open, next_close, next_pct,
                next_high, next_low,
                next_5d_pct, next_10d_pct,
                max_gain, max_drawdown,
                pnl, datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                signal_date, ts_code,
            ))
            updated += 1

        conn.commit()
        conn.close()
        print(f"✅ 回填完成: {updated}/{len(pending)} 条")

    # ═══════════════════════════════════════════
    # 胜率报告
    # ═══════════════════════════════════════════
    def report(self) -> Dict:
        """生成完整胜率报告"""
        if not os.path.exists(self.tracker_db):
            print("❌ 信号跟踪表不存在")
            return {}

        conn = sqlite3.connect(self.tracker_db)
        df = pd.read_sql_query(
            "SELECT * FROM tail_signal_tracker WHERE status='done'", conn
        )
        conn.close()

        if df.empty:
            print("⚠ 无已完成的信号数据")
            return {}

        report = {}
        total = len(df)
        wins = df[df['pnl'] > 0]
        losses = df[df['pnl'] <= 0]

        report['total'] = total
        report['win_rate'] = len(wins) / total * 100
        report['avg_pnl'] = df['pnl'].mean()
        report['avg_win'] = wins['pnl'].mean() if not wins.empty else 0
        report['avg_loss'] = losses['pnl'].mean() if not losses.empty else 0
        report['avg_max_gain'] = df['max_gain'].mean()
        report['avg_max_drawdown'] = df['max_drawdown'].mean()

        # 按分数段
        bins = [(50, 65), (65, 75), (75, 85), (85, 95), (95, 200)]
        report['by_score'] = {}
        for lo, hi in bins:
            subset = df[(df['total_score'] >= lo) & (df['total_score'] < hi)]
            if not subset.empty:
                wr = (subset['pnl'] > 0).mean() * 100
                report['by_score'][f'{lo}-{hi}'] = {
                    'count': len(subset),
                    'win_rate': round(wr, 1),
                    'avg_pnl': round(subset['pnl'].mean(), 2),
                }

        # 按信号类型
        report['by_signal'] = {}
        for sig in df['signal'].unique():
            subset = df[df['signal'] == sig]
            wr = (subset['pnl'] > 0).mean() * 100
            report['by_signal'][sig] = {
                'count': len(subset),
                'win_rate': round(wr, 1),
                'avg_pnl': round(subset['pnl'].mean(), 2),
            }

        # 按主题
        report['by_theme'] = {}
        for theme in df['theme'].unique():
            subset = df[df['theme'] == theme]
            if len(subset) >= 3:
                wr = (subset['pnl'] > 0).mean() * 100
                report['by_theme'][theme] = {
                    'count': len(subset),
                    'win_rate': round(wr, 1),
                    'avg_pnl': round(subset['pnl'].mean(), 2),
                }

        # 持有期收益
        if 'next_5d_pct' in df.columns:
            valid_5d = df['next_5d_pct'].dropna()
            if not valid_5d.empty:
                report['hold_5d'] = {
                    'avg': round(valid_5d.mean(), 2),
                    'win_rate': round((valid_5d > 0).mean() * 100, 1),
                }

        # 输出
        self._print_report(report)
        return report

    def _print_report(self, report: Dict):
        """打印胜率报告"""
        print(f"\n{'='*70}")
        print(f"📊 尾盘猎手胜率报告")
        print(f"{'='*70}")
        print(f"  总信号: {report['total']}")
        print(f"  胜率: {report['win_rate']:.1f}%")
        print(f"  平均盈亏: {report['avg_pnl']:.2f}%")
        print(f"  平均盈利: {report['avg_win']:.2f}%  平均亏损: {report['avg_loss']:.2f}%")
        print(f"  平均最大浮盈: {report['avg_max_gain']:.2f}%")
        print(f"  平均最大回撤: {report['avg_max_drawdown']:.2f}%")

        if report.get('by_score'):
            print(f"\n  📈 分数段胜率:")
            for k, v in report['by_score'].items():
                print(f"    {k}分: {v['count']}次 胜率{v['win_rate']}% 均盈亏{v['avg_pnl']}%")

        if report.get('by_signal'):
            print(f"\n  📈 信号类型:")
            for k, v in report['by_signal'].items():
                print(f"    {k}: {v['count']}次 胜率{v['win_rate']}% 均盈亏{v['avg_pnl']}%")

        if report.get('by_theme'):
            print(f"\n  📈 主题胜率 (>=3次):")
            sorted_themes = sorted(report['by_theme'].items(), key=lambda x: -x[1]['win_rate'])
            for theme, v in sorted_themes[:10]:
                print(f"    {theme}: {v['count']}次 胜率{v['win_rate']}% 均盈亏{v['avg_pnl']}%")

        if report.get('hold_5d'):
            print(f"\n  📈 持有5日: 均收益{report['hold_5d']['avg']}% 胜率{report['hold_5d']['win_rate']}%")

        print(f"{'='*70}\n")

    # ═══════════════════════════════════════════
    # 因子贡献分析
    # ═══════════════════════════════════════════
    def factor_contribution(self) -> pd.DataFrame:
        """分析各评分维度对胜率的贡献"""
        if not os.path.exists(self.tracker_db):
            return pd.DataFrame()

        conn = sqlite3.connect(self.tracker_db)
        df = pd.read_sql_query(
            "SELECT * FROM tail_signal_tracker WHERE status='done'", conn
        )
        conn.close()

        if df.empty or len(df) < 10:
            print("⚠ 数据不足(需>=10条已完成信号)")
            return pd.DataFrame()

        df['win'] = (df['pnl'] > 0).astype(int)

        # 各维度与胜率的相关性
        factors = ['attack_score', 'structure_score', 'position_score',
                   'tech_score', 'theme_score', 'trap_penalty']
        results = []
        for f in factors:
            if f not in df.columns:
                continue
            col = df[f].astype(float)
            if col.std() == 0:
                continue
            corr = col.corr(df['win'])
            # 高分组 vs 低分组胜率
            median = col.median()
            high_group = df[col >= median]
            low_group = df[col < median]
            high_wr = high_group['win'].mean() * 100 if not high_group.empty else 0
            low_wr = low_group['win'].mean() * 100 if not low_group.empty else 0
            results.append({
                'factor': f,
                'corr_with_win': round(corr, 3),
                'high_group_wr': round(high_wr, 1),
                'low_group_wr': round(low_wr, 1),
                'wr_diff': round(high_wr - low_wr, 1),
            })

        result_df = pd.DataFrame(results).sort_values('wr_diff', ascending=False)
        print(f"\n📊 因子胜率贡献分析:")
        print(result_df.to_string(index=False))
        return result_df
