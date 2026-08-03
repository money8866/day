# -*- coding: utf-8 -*-
"""
「猎尾」尾盘突袭战法 - 回测75分以上胜率
========================================
基于 tail_backtest_tdx.py，但捕获 >=75 分的信号
结果存入 tail_backtest_75 表
"""
import os
import sys
import json
import sqlite3
import time
import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = r'D:\mystock\cache_daily'
TRACKER_DB = os.path.join(CACHE_DIR, 'tail_backtest_tdx.db')

sys.path.insert(0, BASE_DIR)

# 动态修改 TailStrategy 的阈值: 捕获 >=75 分的信号
import tail_strategy
tail_strategy.TailStrategy.STRONG_BUY_THRESHOLD = 75  # 原85
tail_strategy.TailStrategy.BUY_THRESHOLD = 65          # 不变
tail_strategy.TailStrategy.WATCH_THRESHOLD = 50        # 不变

from tail_backtest_tdx import TailBacktester, TRACKER_DB

class TailBacktester75(TailBacktester):
    """捕获 >=75 分信号的回测器"""

    def _init_db(self):
        """初始化回测结果数据库(使用新表)"""
        conn = sqlite3.connect(TRACKER_DB, timeout=10.0)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS tail_backtest_75 (
                signal_date   TEXT NOT NULL,
                ts_code       TEXT NOT NULL,
                name          TEXT,
                theme         TEXT,
                signal        TEXT,
                total_score   INTEGER,
                attack_score  INTEGER,
                structure_score INTEGER,
                position_score INTEGER,
                theme_score   INTEGER,
                tech_score    INTEGER,
                trap_penalty  INTEGER,
                trend_score    INTEGER,
                rel_strength_score INTEGER,
                breakout_score INTEGER,
                vol_penalty    INTEGER,
                entry_price   REAL,
                entry_date    TEXT,
                exit_date     TEXT,
                exit_price    REAL,
                exit_reason   TEXT,
                pnl           REAL,
                hold_days     INTEGER,
                max_gain      REAL,
                max_drawdown  REAL,
                next_5d_pct   REAL,
                next_10d_pct  REAL,
                status        TEXT DEFAULT 'pending',
                detail_json   TEXT,
                PRIMARY KEY (signal_date, ts_code)
            )
        ''')
        conn.commit()
        conn.close()

    def run(self):
        """执行回测->捕获 >=75 分的信号"""
        print(f"\n{'═' * 60}")
        print(f"  开始回测(>=75分)...")
        print(f"{'═' * 60}")

        conn = sqlite3.connect(TRACKER_DB, timeout=10.0)
        total_signals = 0
        t0 = time.time()

        for i, trade_date in enumerate(self.trading_dates):
            pct_done = (i + 1) / len(self.trading_dates) * 100
            elapsed = time.time() - t0
            eta = elapsed / max(i + 1, 1) * (len(self.trading_dates) - i - 1)
            print(f"  [{i+1}/{len(self.trading_dates)}] {pct_done:.0f}% {trade_date}  ETA {eta:.0f}s", end='')

            day_signals = self._scan_day(trade_date)

            # 捕获 >=75 分的信号(原STRONG_BUY阈值已改为75)
            tracked = [s for s in day_signals if s.get('signal') == '强买入']
            for s in tracked:
                conn.execute('''
                    INSERT OR REPLACE INTO tail_backtest_75 (
                        signal_date, ts_code, name, theme, signal,
                        total_score, attack_score, structure_score, position_score,
                        theme_score, tech_score, trap_penalty,
                        trend_score, rel_strength_score, breakout_score, vol_penalty,
                        entry_price, entry_date, status, detail_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    trade_date, s['ts_code'], s.get('name', ''), s.get('theme', ''), s['signal'],
                    s['total_score'], s['attack_score'], s['structure_score'], s['position_score'],
                    s['theme_score'], s['tech_score'], s['trap_penalty'],
                    s.get('trend_score', 0), s.get('rel_strength_score', 0), s.get('breakout_score', 0), s.get('vol_penalty', 0),
                    s['price'], trade_date, 'pending', json.dumps(s.get('detail', {}), ensure_ascii=False),
                ))

            total_signals += len(tracked)
            print(f"  信号{len(tracked)}只 (累计{total_signals})")

        conn.commit()
        conn.close()

        print(f"\n{'═' * 60}")
        print(f"  回测完成! 共{total_signals}只信号, 耗时{time.time()-t0:.0f}s")
        print(f"{'═' * 60}")

        # 计算退出与盈亏
        self._compute_exits_75()

    def _compute_exits_75(self):
        """计算退出与盈亏(使用tail_backtest_75表)"""
        print(f"\n{'═' * 60}")
        print(f"  计算退出与盈亏...")
        print(f"{'═' * 60}")

        conn = sqlite3.connect(TRACKER_DB, timeout=10.0)
        rows = conn.execute(
            'SELECT signal_date, ts_code, entry_price FROM tail_backtest_75 WHERE status = ?',
            ('pending',)
        ).fetchall()

        if not rows:
            print("  无待计算信号")
            conn.close()
            return

        ok, fail = 0, 0
        for signal_date, ts_code, entry_price in rows:
            try:
                result = self._compute_exit_one(signal_date, ts_code, entry_price)
                if result:
                    exit_date, exit_price, exit_reason, pnl, hold_days, max_gain, max_dd, next_5d, next_10d = result
                    conn.execute('''
                        UPDATE tail_backtest_75 SET
                            exit_date = ?, exit_price = ?, exit_reason = ?, pnl = ?,
                            hold_days = ?, max_gain = ?, max_drawdown = ?,
                            next_5d_pct = ?, next_10d_pct = ?,
                            status = 'closed', entry_date = ?
                        WHERE signal_date = ? AND ts_code = ?
                    ''', (exit_date, exit_price, exit_reason, pnl, hold_days, max_gain, max_dd,
                          next_5d, next_10d, exit_date, signal_date, ts_code))
                    ok += 1
                else:
                    fail += 1
            except Exception as e:
                fail += 1

        conn.commit()
        conn.close()
        print(f"  完成: 成功{ok}, 失败{fail}")


if __name__ == '__main__':
    # 回测2026年
    bt = TailBacktester75('20260101', '20260731')
    bt.prepare_data()
    bt.run()

    # 输出统计
    conn = sqlite3.connect(TRACKER_DB, timeout=10.0)
    print(f"\n{'═' * 60}")
    print(f"  📊 >=75分 信号胜率统计")
    print(f"{'═' * 60}")

    # 按分数段统计
    cur = conn.execute('''
        SELECT
            CASE
                WHEN total_score >= 85 THEN '>=85'
                WHEN total_score >= 80 THEN '80-84'
                ELSE '75-79'
            END as score_range,
            COUNT(*) as total,
            SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as losses,
            ROUND(AVG(pnl), 2) as avg_pnl,
            ROUND(AVG(max_gain), 2) as avg_max_gain,
            ROUND(AVG(max_drawdown), 2) as avg_max_dd,
            ROUND(AVG(hold_days), 1) as avg_hold
        FROM tail_backtest_75
        WHERE status = 'closed'
        GROUP BY score_range
        ORDER BY score_range DESC
    ''')
    print(f"\n{'分段':<8} {'总数':<6} {'胜':<6} {'负':<6} {'胜率':<8} {'平均收益':<10} {'最大收益':<10} {'最大回撤':<10} {'持仓天数':<8}")
    print('-' * 72)
    all_total, all_wins, all_losses = 0, 0, 0
    for r in cur.fetchall():
        win_rate = r[2] / r[1] * 100 if r[1] > 0 else 0
        print(f'{r[0]:<8} {r[1]:<6} {r[2]:<6} {r[3]:<6} {win_rate:<7.1f}% {r[4]:<+7.2f}% {r[5]:<+7.2f}% {r[6]:<+7.2f}% {r[7]:<8}')
        all_total += r[1]; all_wins += r[2]; all_losses += r[3]
    if all_total > 0:
        print(f'\n全量: {all_total}只, 胜{all_wins} 负{all_losses} 胜率{all_wins/all_total*100:.1f}%')

    # 按退出原因统计
    cur = conn.execute('''
        SELECT exit_reason, COUNT(*) as total,
               ROUND(AVG(pnl), 2) as avg_pnl,
               SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
               ROUND(AVG(hold_days), 1) as avg_hold
        FROM tail_backtest_75 WHERE status = 'closed'
        GROUP BY exit_reason ORDER BY total DESC
    ''')
    print(f"\n{'退出原因':<8} {'总数':<6} {'胜':<6} {'胜率':<8} {'平均收益':<10} {'持仓天数':<8}")
    print('-' * 46)
    for r in cur.fetchall():
        win_rate = r[3] / r[1] * 100 if r[1] > 0 else 0
        print(f'{r[0]:<8} {r[1]:<6} {r[3]:<6} {win_rate:<7.1f}% {r[2]:<+7.2f}% {r[4]:<8}')

    conn.close()