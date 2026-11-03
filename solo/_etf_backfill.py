# -*- coding: utf-8 -*-
"""补齐 ETF 行情面板缺失的交易日。

★ 为什么需要这个：
  etf_daily.db 的数据靠 `etf_mainline.py --fetch` 手动拉取，
  漏拉几天就会在面板上留下**空档**。空档会让后续所有基于
  「行业 nav 序列」的判定失真：
    · 主线判定的 ret20 / excess20 窗口跨越空档 → 实际跨度不是 20 天
    · days_strong 回溯时遇到空档 → 连续计数被打断
    · 盘中实时价接在最后一个有数据的日期之后 → 少算 N 天涨跌

实测 2026-11-03：面板最新只到 10-30，缺 11-02（10-31 无交易），
  今日实时 nav 相对 10-30 少算了 11-02 一整天的涨跌。

用法：
    python _etf_backfill.py            # 补齐所有缺失交易日
    python _etf_backfill.py --days 5   # 只查最近 5 天内的缺口
"""
import os
import sys
import time
import sqlite3
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)


def _load_pro():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, 'config', '.env'))
    import tushare as ts
    from dotenv import load_dotenv as _ld
    _ld(os.path.join(ROOT, 'config', '.env'))
    import os as _os
    ts.set_token(_os.getenv('TUSHARE_TOKEN'))
    return ts.pro_api()


def missing_dates(panel, max_lookback=30, today=None):
    """找出面板缺失的交易日。

    ★ 只报「**已经收盘但未入库**」的历史缺口。
      当天（尚未收盘，或刚收盘数据未就绪）不算缺口 ——
      否则每次盘中检查都会报缺今天，制造噪音。
    """
    have = set(panel['trade_date'].unique())
    if not have:
        return []
    last = panel['trade_date'].max()
    if today is None:
        import datetime as _dt
        today = _dt.datetime.now().strftime('%Y%m%d')
    # 从 daily_cache 拿权威交易日历
    import sqlite3 as _sq
    db = os.path.join(ROOT, 'cache_daily', 'stock_data.db')
    if not os.path.exists(db):
        return []
    conn = _sq.connect(f'file:{db}?mode=ro', uri=True)
    try:
        rows = [r[0] for r in conn.execute(
            'SELECT DISTINCT trade_date FROM daily_cache '
            'WHERE trade_date > ? ORDER BY trade_date', (last,))]
    finally:
        conn.close()
    # 排除当天（未收盘 / 数据未就绪）
    gaps = [d for d in rows if d not in have and d < str(today)]
    return gaps[:max_lookback]


def backfill(target_dates=None, panel=None):
    """把缺失日期的 ETF 行情补进 etf_daily.db。"""
    import etf_mainline as em
    if panel is None:
        panel = em.load_etf_panel()
    if target_dates is None:
        target_dates = missing_dates(panel)
    if not target_dates:
        print('面板无缺口')
        return 0

    print(f'待补日期: {target_dates}')
    pro = _load_pro()
    codes = sorted(em.all_codes())

    conn = sqlite3.connect(em.ETF_DB)
    cur = conn.cursor()
    added = 0
    for c in codes:
        try:
            d = pro.fund_daily(ts_code=c, start_date=min(target_dates),
                               end_date=max(target_dates))
        except Exception as e:
            print(f'  {c} 异常 {str(e)[:60]}')
            time.sleep(0.05)
            continue
        if d is None or not len(d):
            continue
        d = d.sort_values('trade_date')
        for _, r in d.iterrows():
            td = str(r['trade_date'])
            if td not in target_dates:
                continue
            cur.execute('SELECT COUNT(*) FROM etf_daily WHERE ts_code=? AND trade_date=?',
                        (c, td))
            if cur.fetchone()[0]:
                continue
            hist = panel[panel.ts_code == c].sort_values('trade_date')
            if hist.empty:
                continue
            # 复权 nav：用 pct_chg 累乘（ETF 份额折算会让 close 跳变）
            nav = hist.nav.iloc[-1] * (1 + (r['pct_chg'] or 0) / 100.0)
            cur.execute(
                'INSERT OR REPLACE INTO etf_daily (ts_code, trade_date, nav, amount, pct_chg) '
                'VALUES (?,?,?,?,?)',
                (c, td, nav, float(r['amount'] or 0), float(r['pct_chg'] or 0)))
            added += 1
        time.sleep(0.04)
    conn.commit()
    conn.close()
    print(f'✓ 补入 {added} 行')
    return added


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=30,
                    help='最多回补多少个缺失交易日')
    ap.add_argument('--check', action='store_true', help='只检查缺口不补')
    args = ap.parse_args()

    import etf_mainline as em
    panel = em.load_etf_panel()
    gaps = missing_dates(panel, args.days)
    print(f'面板: {panel.trade_date.nunique()} 个交易日，最新 {panel.trade_date.max()}')
    if not gaps:
        print('✓ 无缺口')
        return
    print(f'⚠ 缺失 {len(gaps)} 个交易日: {gaps}')
    if args.check:
        return
    backfill(gaps, panel)
    p2 = em.load_etf_panel()
    print(f'补后: {p2.trade_date.nunique()} 个交易日，最新 {p2.trade_date.max()}')


if __name__ == '__main__':
    main()
