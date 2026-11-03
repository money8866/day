# -*- coding: utf-8 -*-
"""补齐 index_daily_cache 缺失的交易日。

★ 为什么必须自动做：
  `market_regime_v10._query_index_hist` 只查 index_daily_cache。
  若该表缺目标交易日（tushare 当日 15:00 后才就绪，而缓存是更早跑的），
  返回的序列末行是**上一交易日**，下游会把它当成「当日」——
  报告里显示的是昨天的收盘价与昨天的涨幅，却标成今天的。

  实测 2026-11-03 事故：
    报告写「上证 3638.03 +1.08% / 中证2000 2681.29 +2.47%」→ 那是 11-02 的数据
    11-03 真实：上证 3608.10 **-0.82%**、中证2000 2623.84 **-2.14%**、
              创业板指 -2.73%、科创50 -3.81%（全线下跌）
    报告据此写「放量上涨」，完全颠倒了当日方向。

  本模块在盘后流程里自动补齐，同时也可手动运行：
      python _index_backfill.py            # 补齐所有缺口
      python _index_backfill.py --check    # 只查不补
"""
import os
import sys
import time
import sqlite3
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
INDEX_DB = os.path.join(ROOT, 'cache_daily', 'stock_data.db')


def _load_pro():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, 'config', '.env'))
    import os as _os
    import tushare as ts
    ts.set_token(_os.getenv('TUSHARE_TOKEN'))
    return ts.pro_api()


def index_codes(conn=None):
    """库内已有的指数代码列表。"""
    own = conn is None
    if own:
        conn = sqlite3.connect(f'file:{INDEX_DB}?mode=ro', uri=True)
    try:
        return [r[0] for r in conn.execute(
            'SELECT DISTINCT ts_code FROM index_daily_cache')]
    finally:
        if own:
            conn.close()


def missing_index_dates(conn=None, max_lookback=15, today=None):
    """找出 index_daily_cache 缺失的交易日（相对 daily_cache 日历）。

    ★ 只报「已经收盘但未入库」的历史缺口，不含当天
      （当天指数数据在 15:00 后才由 tushare 就绪，早盘必然缺失）。
    """
    if today is None:
        import datetime as _dt
        today = _dt.datetime.now().strftime('%Y%m%d')
    own = conn is None
    if own:
        conn = sqlite3.connect(f'file:{INDEX_DB}?mode=ro', uri=True)
    try:
        have = {r[0] for r in conn.execute(
            'SELECT DISTINCT trade_date FROM index_daily_cache')}
        rows = [r[0] for r in conn.execute(
            'SELECT DISTINCT trade_date FROM daily_cache '
            'ORDER BY trade_date DESC LIMIT ?', (max_lookback,))]
    finally:
        if own:
            conn.close()
    return sorted(d for d in rows if d not in have and d < str(today))


def backfill(target_dates=None, max_lookback=15, verbose=True):
    """把缺失日期的指数日线补进 index_daily_cache。返回补入行数。"""
    conn = sqlite3.connect(INDEX_DB)
    cur = conn.cursor()
    try:
        if target_dates is None:
            target_dates = missing_index_dates(conn, max_lookback)
        if not target_dates:
            if verbose:
                print('[Index] 无缺口')
            return 0
        if verbose:
            print(f'[Index] 待补日期: {target_dates}')
        codes = index_codes(conn)
        pro = _load_pro()
        added = 0
        for code in codes:
            ok = False
            for attempt in range(3):
                try:
                    d = pro.index_daily(ts_code=code,
                                         start_date=min(target_dates),
                                         end_date=max(target_dates))
                    ok = True
                    break
                except Exception as e:
                    if attempt == 2 and verbose:
                        print(f'[Index]   {code} 重试失败: {str(e)[:50]}')
                    time.sleep(0.5)
            if not ok or d is None or not len(d):
                continue
            for _, r in d.iterrows():
                td = str(r['trade_date'])
                if td not in target_dates:
                    continue
                cur.execute('SELECT COUNT(*) FROM index_daily_cache '
                            'WHERE ts_code=? AND trade_date=?', (code, td))
                if cur.fetchone()[0]:
                    continue
                cur.execute(
                    'INSERT OR REPLACE INTO index_daily_cache '
                    '(ts_code, trade_date, close, pct_chg, vol, amount) '
                    'VALUES (?,?,?,?,?,?)',
                    (code, td, float(r['close']), float(r['pct_chg']),
                     float(r['vol'] or 0), float(r['amount'] or 0)))
                added += 1
                if verbose:
                    print(f'[Index]   ✓ {code} {td} close={r["close"]:.2f} '
                          f'pct_chg={r["pct_chg"]:+.2f}%')
            time.sleep(0.08)
        conn.commit()
        if verbose:
            print(f'[Index] ✓ 补入 {added} 行')
        return added
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true', help='只检查缺口不补')
    ap.add_argument('--lookback', type=int, default=15,
                    help='最多检查最近多少个交易日')
    args = ap.parse_args()

    gaps = missing_index_dates(max_lookback=args.lookback)
    if not gaps:
        print('[Index] ✓ index_daily_cache 无缺口')
        return
    print(f'[Index] ⚠ 缺失 {len(gaps)} 个交易日: {gaps}')
    if args.check:
        return
    backfill(gaps)


if __name__ == '__main__':
    main()
