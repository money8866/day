# -*- coding: utf-8 -*-
"""
尾盘信号跟踪表盘后回填工具

功能:
  1. 回填 next_open/next_close/next_pct_chg/next_high/next_low (T+1日行情)
  2. 回填 next_5d_pct/next_10d_pct (T+5/T+10日累计涨幅)
  3. 回填 max_gain/max_drawdown (信号日到最新交易日的最大收益/回撤)
  4. 根据止损止盈规则回填 exit_date/exit_price/exit_reason/pnl, 更新status

数据源: D:\\mystock\\cache_daily\\stock_data.db (stk_factor_pro表)

用法:
  python backfill_tail_tracker.py              # 回填所有pending信号
  python backfill_tail_tracker.py --status     # 查看跟踪表状态
  python backfill_tail_tracker.py --date 20260731  # 仅回填指定信号日的
  python backfill_tail_tracker.py --exit-only  # 仅执行退出判定,不回填行情
"""
import os
import sys
import sqlite3
import argparse
import datetime
import pandas as pd

CACHE_DIR = r'D:\mystock\cache_daily'
TRACKER_DB = os.path.join(CACHE_DIR, 'tail_signal_tracker.db')
STOCK_DB = os.path.join(CACHE_DIR, 'stock_data.db')


def get_conn(db_path):
    return sqlite3.connect(db_path, timeout=10.0)


def get_trading_dates_after(signal_date, count=15):
    """获取signal_date之后的交易日列表(从stock_data.db)"""
    conn = get_conn(STOCK_DB)
    try:
        rows = conn.execute(
            'SELECT DISTINCT trade_date FROM stk_factor_pro WHERE trade_date > ? ORDER BY trade_date ASC LIMIT ?',
            (signal_date, count)
        ).fetchall()
    finally:
        conn.close()
    return [str(r[0]) for r in rows]


def get_stock_daily(ts_code, start_date, end_date):
    """从stk_factor_pro表读取个股日线数据"""
    conn = get_conn(STOCK_DB)
    try:
        df = pd.read_sql_query(
            'SELECT trade_date, open, high, low, close, pre_close, pct_chg, vol '
            'FROM stk_factor_pro WHERE ts_code = ? AND trade_date BETWEEN ? AND ? ORDER BY trade_date',
            conn, params=(ts_code, start_date, end_date)
        )
    finally:
        conn.close()
    if df.empty:
        return None
    df['trade_date'] = df['trade_date'].astype(str)
    return df


def compute_exit(signal_row, daily_df, holding_days=10):
    """
    根据止损止盈规则判定退出
    止损: 最低价 <= 信号价 - 5% (固定5%止损)
    止盈: 最高价 >= 信号价 + 10% (固定10%止盈)
    到期: 持仓满holding_days个交易日

    返回: (exit_date, exit_price, exit_reason, pnl)
    """
    entry_price = signal_row['price']
    if entry_price <= 0 or daily_df is None or daily_df.empty:
        return None, None, None, None

    stop_loss = entry_price * 0.95   # 5% 止损
    take_profit = entry_price * 1.10  # 10% 止盈

    for i, row in daily_df.iterrows():
        # 止损优先(盘中触及止损价,按止损价退出)
        if row['low'] <= stop_loss:
            pnl = (stop_loss - entry_price) / entry_price * 100
            return row['trade_date'], stop_loss, '止损', round(pnl, 2)
        # 止盈(盘中触及止盈价,按止盈价退出)
        if row['high'] >= take_profit:
            pnl = (take_profit - entry_price) / entry_price * 100
            return row['trade_date'], take_profit, '止盈', round(pnl, 2)
        # 到期退出(按收盘价)
        if i >= holding_days - 1:
            pnl = (row['close'] - entry_price) / entry_price * 100
            return row['trade_date'], row['close'], '到期', round(pnl, 2)

    # 未触发退出,返回最新交易日收盘价作为浮动盈亏
    last = daily_df.iloc[-1]
    pnl = (last['close'] - entry_price) / entry_price * 100
    return last['trade_date'], last['close'], '持仓中', round(pnl, 2)


def backfill_one(conn, row, trading_dates_cache):
    """回填单条信号"""
    signal_date = row['signal_date']
    ts_code = row['ts_code']
    entry_price = row['price']

    # 获取信号日之后的交易日
    if signal_date not in trading_dates_cache:
        trading_dates_cache[signal_date] = get_trading_dates_after(signal_date, count=15)
    future_dates = trading_dates_cache[signal_date]

    if not future_dates:
        return False, '无后续交易日数据'

    # 读取信号日后的日线数据
    daily_df = get_stock_daily(ts_code, signal_date, future_dates[-1])
    if daily_df is None or daily_df.empty:
        return False, '无日线数据'

    # 过滤信号日之后的行情
    daily_df = daily_df[daily_df['trade_date'] > signal_date].reset_index(drop=True)
    if daily_df.empty:
        return False, '信号日后无行情'

    # ── T+1 日行情 ──
    t1 = daily_df.iloc[0]
    next_open = t1['open']
    next_close = t1['close']
    next_pct_chg = t1['pct_chg']
    next_high = t1['high']
    next_low = t1['low']

    # ── T+5/T+10 累计涨幅 ──
    next_5d_pct = None
    next_10d_pct = None
    if len(daily_df) >= 5:
        next_5d_pct = round((daily_df.iloc[4]['close'] - entry_price) / entry_price * 100, 2)
    if len(daily_df) >= 10:
        next_10d_pct = round((daily_df.iloc[9]['close'] - entry_price) / entry_price * 100, 2)

    # ── 最大收益/回撤 (信号日到最新) ──
    highs = daily_df['high'].values
    lows = daily_df['low'].values
    max_high = max(highs) if len(highs) > 0 else entry_price
    min_low = min(lows) if len(lows) > 0 else entry_price
    max_gain = round((max_high - entry_price) / entry_price * 100, 2)
    max_drawdown = round((min_low - entry_price) / entry_price * 100, 2)

    # ── 退出判定 ──
    exit_date, exit_price, exit_reason, pnl = compute_exit(row, daily_df)
    status = 'closed' if exit_reason in ('止损', '止盈', '到期') else 'active'

    conn.execute('''
        UPDATE tail_signal_tracker SET
            next_open = ?, next_close = ?, next_pct_chg = ?, next_high = ?, next_low = ?,
            next_5d_pct = ?, next_10d_pct = ?,
            max_gain = ?, max_drawdown = ?,
            exit_date = ?, exit_price = ?, exit_reason = ?, pnl = ?,
            status = ?, updated_at = datetime('now')
        WHERE signal_date = ? AND ts_code = ?
    ''', (
        next_open, next_close, next_pct_chg, next_high, next_low,
        next_5d_pct, next_10d_pct,
        max_gain, max_drawdown,
        exit_date, exit_price, exit_reason, pnl,
        status,
        signal_date, ts_code
    ))
    return True, f'{exit_reason} pnl={pnl}%'


def show_status():
    """显示跟踪表状态"""
    if not os.path.exists(TRACKER_DB):
        print(f"跟踪表不存在: {TRACKER_DB}")
        return

    conn = get_conn(TRACKER_DB)
    # 总览
    total = conn.execute('SELECT COUNT(*) FROM tail_signal_tracker').fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM tail_signal_tracker WHERE status='pending'").fetchone()[0]
    active = conn.execute("SELECT COUNT(*) FROM tail_signal_tracker WHERE status='active'").fetchone()[0]
    closed = conn.execute("SELECT COUNT(*) FROM tail_signal_tracker WHERE status='closed'").fetchone()[0]

    print(f"\n{'═' * 60}")
    print(f"  尾盘信号跟踪表状态")
    print(f"{'═' * 60}")
    print(f"  总信号数: {total}")
    print(f"  pending(待回填): {pending}")
    print(f"  active(持仓中):  {active}")
    print(f"  closed(已平仓):  {closed}")
    print(f"  DB路径: {TRACKER_DB}")

    # 按信号日统计
    print(f"\n  按信号日统计:")
    rows = conn.execute('''
        SELECT signal_date,
               COUNT(*) as total,
               SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) as pending,
               SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) as active,
               SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) as closed,
               ROUND(AVG(pnl), 2) as avg_pnl,
               SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as win,
               SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as lose
        FROM tail_signal_tracker
        GROUP BY signal_date
        ORDER BY signal_date DESC
        LIMIT 20
    ''').fetchall()
    if rows:
        print(f"  {'信号日':<10} {'总数':>4} {'待填':>4} {'持仓':>4} {'平仓':>4} {'胜':>4} {'负':>4} {'胜率':>6} {'均盈亏':>8}")
        for r in rows:
            win_rate = f"{r[6]/(r[6]+r[7])*100:.1f}%" if (r[6]+r[7]) > 0 else '-'
            avg_pnl = f"{r[5]}%" if r[5] is not None else '-'
            print(f"  {r[0]:<10} {r[1]:>4} {r[2]:>4} {r[3]:>4} {r[4]:>4} {r[6]:>4} {r[7]:>4} {win_rate:>6} {avg_pnl:>8}")

    # 退出原因分布
    print(f"\n  退出原因分布:")
    rows = conn.execute('''
        SELECT exit_reason, COUNT(*), ROUND(AVG(pnl), 2)
        FROM tail_signal_tracker
        WHERE exit_reason IS NOT NULL
        GROUP BY exit_reason
    ''').fetchall()
    for r in rows:
        print(f"    {r[0] or 'NULL'}: {r[1]}只, 平均盈亏{r[2] if r[2] else 0}%")

    conn.close()
    print(f"{'═' * 60}\n")


def backfill(signal_date=None, exit_only=False):
    """执行回填"""
    if not os.path.exists(TRACKER_DB):
        print(f"跟踪表不存在: {TRACKER_DB}")
        return

    conn = get_conn(TRACKER_DB)

    # 读取待回填信号
    if signal_date:
        rows = conn.execute(
            'SELECT * FROM tail_signal_tracker WHERE signal_date = ? AND status != ?',
            (signal_date, 'closed')
        ).fetchall()
    elif exit_only:
        rows = conn.execute(
            'SELECT * FROM tail_signal_tracker WHERE status != ?',
            ('closed',)
        ).fetchall()
    else:
        rows = conn.execute(
            'SELECT * FROM tail_signal_tracker WHERE status = ?',
            ('pending',)
        ).fetchall()

    if not rows:
        print("无待回填信号")
        conn.close()
        return

    # 获取列名
    col_names = [desc[0] for desc in conn.execute('SELECT * FROM tail_signal_tracker LIMIT 1').description]
    signals = [dict(zip(col_names, r)) for r in rows]

    print(f"\n{'═' * 60}")
    print(f"  回填{len(signals)}条信号")
    print(f"{'═' * 60}")

    trading_dates_cache = {}
    ok, fail = 0, 0
    for i, sig in enumerate(signals):
        pct = (i + 1) / len(signals) * 100
        print(f"  [{i+1}/{len(signals)}] {pct:.0f}% {sig['ts_code']} {sig.get('name', '')} ", end='')

        try:
            success, msg = backfill_one(conn, sig, trading_dates_cache)
            if success:
                conn.commit()
                ok += 1
                print(f"  ✓ {msg}")
            else:
                fail += 1
                print(f"  ✗ {msg}")
        except Exception as e:
            fail += 1
            print(f"  ✗ 异常: {e}")

    conn.close()
    print(f"\n{'═' * 60}")
    print(f"  完成! 成功{ok} 失败{fail}")
    print(f"{'═' * 60}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='尾盘信号跟踪表盘后回填工具')
    parser.add_argument('--status', action='store_true', help='查看跟踪表状态')
    parser.add_argument('--date', help='仅回填指定信号日(YYYYMMDD)')
    parser.add_argument('--exit-only', action='store_true', help='仅执行退出判定,不限制status')
    args = parser.parse_args()

    if args.status:
        show_status()
    else:
        backfill(signal_date=args.date, exit_only=args.exit_only)
        show_status()
