# -*- coding: utf-8 -*-
"""
「猎尾」尾盘突袭战法 - TDX历史回测框架
================================================
基于通达信本地.day文件 + SQLite(stk_factor_pro)技术指标
默认以收盘价作为2:50信号触发价进行历史回测

数据源:
  - 通达信 .day 文件 (C:/new_tdx/vipdoc/sh|sz/lday/*.day) - 日线OHLC
  - SQLite stock_data.db stk_factor_pro表 - 技术指标(MACD/KDJ/RSI/BOLL)
  - theme_stock_map_latest.json - 主题成份股映射

回测规则:
  1. 每个交易日14:50模拟扫描(用当日收盘价作为信号触发价)
  2. 入库所有强买入信号(>=85分),并标注是否通过方案K(>=88+无诱多+技>=12+排北交所+每主题TOP2)
  3. T+1开盘价买入(次日开盘)
  4. 止损: -5% / 止盈: +10% / 到期: 持仓10个交易日
  5. 统计胜率/盈亏/最大收益/回撤(全部信号 vs 方案K信号)

用法:
  python tail_backtest_tdx.py                            # 默认回测2026年
  python tail_backtest_tdx.py --start 20260101 --end 20260630
  python tail_backtest_tdx.py --start 20260101 --end 20260731 --report
  python tail_backtest_tdx.py --status                   # 查看历史回测结果
"""
import os
import sys
import struct
import json
import time
import sqlite3
import argparse
import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# ============================================================
# 路径配置
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = r'D:\mystock\cache_daily'
TDX_PATH = r"C:\new_tdx"
STOCK_DB = os.path.join(CACHE_DIR, 'stock_data.db')
TRACKER_DB = os.path.join(CACHE_DIR, 'tail_backtest_tdx.db')
THEME_MAP_FILE = os.path.join(CACHE_DIR, 'theme_stock_map_latest.json')

sys.path.insert(0, BASE_DIR)
from tail_strategy import TailStrategy


# ============================================================
# 通达信 .day 文件读取
# ============================================================
def parse_tdx_day_file(filepath):
    """解析通达信.day文件,返回DataFrame"""
    if not os.path.exists(filepath):
        return None
    records = []
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(32)
            if not chunk or len(chunk) < 32:
                break
            date_int = struct.unpack("<i", chunk[0:4])[0]
            open_p = struct.unpack("<i", chunk[4:8])[0] / 100.0
            high_p = struct.unpack("<i", chunk[8:12])[0] / 100.0
            low_p = struct.unpack("<i", chunk[12:16])[0] / 100.0
            close_p = struct.unpack("<i", chunk[16:20])[0] / 100.0
            amount_yuan = struct.unpack("<f", chunk[20:24])[0]
            vol_shares = struct.unpack("<i", chunk[24:28])[0] / 100.0
            date_str = str(date_int)
            records.append({
                "trade_date": date_str,
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "close": close_p,
                "vol": vol_shares,
                "amount": round(amount_yuan / 1000, 3),
            })
    if not records:
        return None
    df = pd.DataFrame(records)
    df = df.sort_values("trade_date").reset_index(drop=True)
    df["pre_close"] = df["close"].shift(1)
    df["pct_chg"] = df["close"].pct_change() * 100
    df["pct_chg"] = df["pct_chg"].fillna(0)
    return df


def ts_code_to_tdx_file(ts_code):
    """ts_code → 通达信.day文件路径"""
    sym, market = ts_code.split(".")
    if market == "SH":
        return os.path.join(TDX_PATH, "vipdoc", "sh", "lday", f"sh{sym}.day")
    elif market == "SZ":
        return os.path.join(TDX_PATH, "vipdoc", "sz", "lday", f"sz{sym}.day")
    return None


# ============================================================
# 主题成份股加载
# ============================================================
def load_theme_stocks(theme_file=None):
    """加载主题成份股映射,返回 (theme_stocks, stock_themes)
    theme_stocks: {theme_name: [(code, name, layer), ...]}
    stock_themes: {ts_code: [theme_name, ...]}
    """
    if theme_file is None:
        theme_file = THEME_MAP_FILE
    if not os.path.exists(theme_file):
        print(f"[错误] 主题文件不存在: {theme_file}")
        return {}, {}

    with open(theme_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    theme_stocks = {}
    stock_themes = {}
    for theme_name, stocks in data.get('themes', {}).items():
        layer_map = {'leader': 'leader', 'middle': 'middle', 'core': 'leader',
                     'extended': 'follower', 'follower': 'follower'}
        stock_list = []
        for s in stocks:
            code = s.get('code', '')
            name = s.get('name', '')
            # 从irs_layer推断layer
            layer = layer_map.get(s.get('irs_layer', ''), 'follower')
            if s.get('score', 0) >= 80:
                layer = 'leader'
            elif s.get('score', 0) >= 60:
                layer = 'middle'
            stock_list.append((code, name, layer))
            stock_themes.setdefault(code, []).append(theme_name)
        theme_stocks[theme_name] = stock_list

    return theme_stocks, stock_themes


# ============================================================
# 主题强度计算(简化版)
# ============================================================
def calc_theme_strength(theme_name, theme_stocks, all_klines, trade_date):
    """
    简化主题强度: 主题内成份股当日平均涨幅
    返回: (strength, zt_count)
    """
    stocks = theme_stocks.get(theme_name, [])
    if not stocks:
        return 0.0, 0

    gains = []
    zt_count = 0
    for code, name, layer in stocks:
        kl = all_klines.get(code)
        if kl is None or kl.empty:
            continue
        row = kl[kl['trade_date'] == trade_date]
        if row.empty:
            continue
        pct = float(row.iloc[0]['pct_chg'])
        gains.append(pct)
        # 涨停判定
        limit = 19.5 if code.startswith(('300', '688')) else 9.5
        if pct >= limit:
            zt_count += 1

    if not gains:
        return 0.0, 0

    avg_gain = sum(gains) / len(gains)
    # 简化强度: 平均涨幅 - 大盘平均(近似0)
    strength = avg_gain
    return strength, zt_count


# ============================================================
# 回测引擎
# ============================================================
class TailBacktester:
    def __init__(self, start_date, end_date):
        self.start_date = start_date
        self.end_date = end_date
        self.strategy = TailStrategy()
        self.theme_stocks = {}
        self.stock_themes = {}
        self.all_klines = {}  # {ts_code: DataFrame}
        self.factor_cache = {}  # {(ts_code, trade_date): factor_row}
        self.trading_dates = []
        self._init_db()

    def _init_db(self):
        """初始化回测结果数据库"""
        conn = sqlite3.connect(TRACKER_DB, timeout=10.0)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS tail_backtest (
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
                entry_price   REAL,
                entry_date    TEXT,
                -- 退出与盈亏
                exit_date     TEXT,
                exit_price    REAL,
                exit_reason   TEXT,
                pnl           REAL,
                hold_days     INTEGER,
                -- 区间统计
                max_gain      REAL,
                max_drawdown  REAL,
                next_5d_pct   REAL,
                next_10d_pct  REAL,
                status        TEXT DEFAULT 'pending',
                k_filtered    INTEGER DEFAULT 0,  -- 是否通过方案K(>=88+无诱多+技>=12+排北交所+每主题TOP2)
                detail_json   TEXT,
                PRIMARY KEY (signal_date, ts_code)
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_bt_date ON tail_backtest(signal_date)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_bt_status ON tail_backtest(status)')
        # 字段迁移: 旧表无k_filtered字段时自动添加
        cols = [c[1] for c in conn.execute('PRAGMA table_info(tail_backtest)').fetchall()]
        if 'k_filtered' not in cols:
            conn.execute('ALTER TABLE tail_backtest ADD COLUMN k_filtered INTEGER DEFAULT 0')
        conn.commit()
        conn.close()

    def prepare_data(self):
        """加载所有必需数据"""
        print(f"\n{'═' * 60}")
        print(f"  「猎尾」尾盘突袭战法 - TDX历史回测")
        print(f"  回测区间: {self.start_date} ~ {self.end_date}")
        print(f"{'═' * 60}")

        # 1. 加载主题
        print("\n[1/4] 加载主题成份股...")
        self.theme_stocks, self.stock_themes = load_theme_stocks()
        all_codes = list(self.stock_themes.keys())
        # 过滤北交所
        all_codes = [c for c in all_codes if not c.startswith(('9', '4'))]
        print(f"  主题数: {len(self.theme_stocks)}, 股票数: {len(all_codes)}")

        # 2. 加载通达信K线(扩展区间: 回测前60个交易日用于MA计算)
        print("\n[2/4] 加载通达信K线...")
        # 扩展起始日期前推90天(约60个交易日)
        start_dt = datetime.datetime.strptime(self.start_date, '%Y%m%d')
        extend_start = (start_dt - datetime.timedelta(days=120)).strftime('%Y%m%d')
        t0 = time.time()
        for i, code in enumerate(all_codes):
            tdx_file = ts_code_to_tdx_file(code)
            if not tdx_file or not os.path.exists(tdx_file):
                continue
            df = parse_tdx_day_file(tdx_file)
            if df is None or df.empty:
                continue
            df = df[(df['trade_date'] >= extend_start) & (df['trade_date'] <= self.end_date)].copy()
            if len(df) >= 30:
                self.all_klines[code] = df
            if (i + 1) % 500 == 0:
                print(f"    进度: {i+1}/{len(all_codes)} ({time.time()-t0:.0f}s)")
        print(f"  完成: {len(self.all_klines)}只 ({time.time()-t0:.0f}s)")

        # 3. 获取交易日历(回测区间内)
        print("\n[3/4] 获取交易日历...")
        self.trading_dates = sorted(set(
            d for kl in self.all_klines.values()
            for d in kl['trade_date'].tolist()
            if self.start_date <= d <= self.end_date
        ))
        print(f"  交易日数: {len(self.trading_dates)}")

        # 4. 预加载技术因子(从SQLite)
        print("\n[4/4] 预加载技术因子...")
        self._preload_factors()
        print(f"  技术因子缓存: {len(self.factor_cache)}条")

    def _preload_factors(self):
        """从SQLite预加载技术因子(回测区间内+前一日)"""
        if not os.path.exists(STOCK_DB):
            print(f"  ⚠ SQLite不存在: {STOCK_DB}")
            return

        conn = sqlite3.connect(STOCK_DB, timeout=10.0)
        # 字段重命名映射
        factor_rename = {
            'macd_dif_bfq': 'macd_dif', 'macd_dea_bfq': 'macd_dea', 'macd_bfq': 'macd',
            'kdj_bfq': 'kdj_j', 'kdj_k_bfq': 'kdj_k', 'kdj_d_bfq': 'kdj_d',
            'rsi_bfq_6': 'rsi_6', 'rsi_bfq_12': 'rsi_12', 'rsi_bfq_24': 'rsi_24',
            'boll_mid_bfq': 'boll_mid', 'boll_upper_bfq': 'boll_upper',
            'boll_lower_bfq': 'boll_lower', 'cci_bfq': 'cci',
        }

        # 扩展前一日(技术指标是前一交易日收盘后运算的)
        start_dt = datetime.datetime.strptime(self.start_date, '%Y%m%d')
        extend_start = (start_dt - datetime.timedelta(days=10)).strftime('%Y%m%d')

        df = pd.read_sql_query(
            'SELECT ts_code, trade_date, close, total_mv, turnover_rate, '
            'macd_dif_bfq, macd_dea_bfq, macd_bfq, '
            'kdj_bfq, kdj_k_bfq, kdj_d_bfq, '
            'rsi_bfq_6, rsi_bfq_12, rsi_bfq_24, '
            'boll_mid_bfq, boll_upper_bfq, boll_lower_bfq, cci_bfq '
            'FROM stk_factor_pro WHERE trade_date BETWEEN ? AND ?',
            conn, params=(extend_start, self.end_date)
        )
        conn.close()

        if df.empty:
            return

        df = df.rename(columns=factor_rename)
        df['trade_date'] = df['trade_date'].astype(str)

        for _, row in df.iterrows():
            key = (row['ts_code'], row['trade_date'])
            self.factor_cache[key] = row.to_dict()

    def run(self):
        """执行回测"""
        print(f"\n{'═' * 60}")
        print(f"  开始回测...")
        print(f"{'═' * 60}")

        conn = sqlite3.connect(TRACKER_DB, timeout=10.0)
        total_signals = 0
        total_k = 0
        t0 = time.time()

        for i, trade_date in enumerate(self.trading_dates):
            pct_done = (i + 1) / len(self.trading_dates) * 100
            elapsed = time.time() - t0
            eta = elapsed / max(i + 1, 1) * (len(self.trading_dates) - i - 1)
            print(f"  [{i+1}/{len(self.trading_dates)}] {pct_done:.0f}% {trade_date}  ETA {eta:.0f}s", end='')

            # 扫描当日所有主题内股票
            day_signals = self._scan_day(trade_date)

            # 入库所有强买入信号(>=85分),并标注是否通过方案K
            k_signals = self.strategy.filter_for_tracking(day_signals)
            k_codes = {(s['ts_code']) for s in k_signals}

            tracked = [s for s in day_signals if s.get('signal') == '强买入']
            for s in tracked:
                is_k = 1 if s['ts_code'] in k_codes else 0
                conn.execute('''
                    INSERT OR REPLACE INTO tail_backtest (
                        signal_date, ts_code, name, theme, signal,
                        total_score, attack_score, structure_score, position_score,
                        theme_score, tech_score, trap_penalty,
                        entry_price, entry_date, status, k_filtered, detail_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                ''', (
                    trade_date, s['ts_code'], s.get('name', ''), s.get('theme', ''), s['signal'],
                    s['total_score'], s['attack_score'], s['structure_score'], s['position_score'],
                    s['theme_score'], s['tech_score'], s['trap_penalty'],
                    s['price'], trade_date, is_k, json.dumps(s.get('detail', {}), ensure_ascii=False),
                ))

            total_signals += len(tracked)
            total_k += len(k_signals)
            print(f"  强买入{len(tracked)}只 方案K{len(k_signals)}只 (累计{total_signals}/{total_k})")

        conn.commit()
        conn.close()

        print(f"\n{'═' * 60}")
        print(f"  回测完成! 共{total_signals}只信号(方案K {total_k}只), 耗时{time.time()-t0:.0f}s")
        print(f"{'═' * 60}")

        # 计算退出与盈亏
        self._compute_exits()

    def _scan_day(self, trade_date):
        """扫描某交易日所有股票,返回信号列表"""
        signals = []
        # 先计算所有主题强度
        theme_strengths = {}
        theme_zt_counts = {}
        for theme_name in self.theme_stocks:
            s, z = calc_theme_strength(theme_name, self.theme_stocks, self.all_klines, trade_date)
            theme_strengths[theme_name] = s
            theme_zt_counts[theme_name] = z

        # 遍历所有股票
        for ts_code, themes in self.stock_themes.items():
            if ts_code.startswith(('9', '4')):  # 排除北交所
                continue
            kl = self.all_klines.get(ts_code)
            if kl is None or kl.empty:
                continue

            # 当日行情
            day_row = kl[kl['trade_date'] == trade_date]
            if day_row.empty:
                continue
            day = day_row.iloc[0]

            # 历史K线(当日及之前)
            kline_up_to = kl[kl['trade_date'] <= trade_date].copy()
            if len(kline_up_to) < 20:
                continue

            # 取最强主题
            best_theme = themes[0]
            best_strength = theme_strengths.get(themes[0], 0)
            best_zt = theme_zt_counts.get(themes[0], 0)
            best_layer = 'follower'
            for t in themes:
                s = theme_strengths.get(t, 0)
                if s > best_strength:
                    best_strength = s
                    best_theme = t
                    best_zt = theme_zt_counts.get(t, 0)
                # 获取layer
                for code, name, ly in self.theme_stocks.get(t, []):
                    if code == ts_code:
                        if s >= best_strength:
                            best_layer = ly

            # 技术因子(取前一交易日的,技术指标是收盘后运算的)
            # 找前一个交易日
            prev_dates = kl[kl['trade_date'] < trade_date]['trade_date'].tolist()
            factor_row = None
            if prev_dates:
                prev_date = prev_dates[-1]
                factor_row = self.factor_cache.get((ts_code, prev_date))

            # 换手率
            turnover = float(factor_row.get('turnover_rate', 0) or 0) if factor_row else 0
            # 总市值
            total_mv = float(factor_row.get('total_mv', 0) or 0) if factor_row else 0

            # 构建q
            q = {
                'open': float(day['open']),
                'high': float(day['high']),
                'low': float(day['low']),
                'price': float(day['close']),  # 用收盘价作为2:50信号触发价
                'last_close': float(day['pre_close']) if not pd.isna(day['pre_close']) else 0,
                'pct_chg': float(day['pct_chg']),
                'vol': float(day['vol']),
            }

            # 获取股票名称
            name = ''
            for code, n, ly in self.theme_stocks.get(best_theme, []):
                if code == ts_code:
                    name = n
                    best_layer = ly
                    break

            # 评分
            sig = self.strategy.score(
                ts_code, q, kline_up_to, factor_row, turnover, total_mv,
                best_theme, best_strength, best_layer, best_zt, snap=None
            )
            if sig is not None:
                sig['name'] = name
                signals.append(sig)

        return signals

    def _compute_exits(self):
        """计算每只信号的退出和盈亏"""
        print(f"\n{'═' * 60}")
        print(f"  计算退出与盈亏...")
        print(f"{'═' * 60}")

        conn = sqlite3.connect(TRACKER_DB, timeout=10.0)
        rows = conn.execute(
            'SELECT signal_date, ts_code, entry_price FROM tail_backtest WHERE status = ?',
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
                        UPDATE tail_backtest SET
                            exit_date = ?, exit_price = ?, exit_reason = ?, pnl = ?,
                            hold_days = ?, max_gain = ?, max_drawdown = ?,
                            next_5d_pct = ?, next_10d_pct = ?, status = 'closed',
                            entry_date = ?
                        WHERE signal_date = ? AND ts_code = ?
                    ''', (
                        exit_date, exit_price, exit_reason, pnl,
                        hold_days, max_gain, max_dd, next_5d, next_10d,
                        exit_date,  # entry_date = T+1(实际买入日)
                        signal_date, ts_code
                    ))
                    ok += 1
                else:
                    fail += 1
            except Exception as e:
                fail += 1

        conn.commit()
        conn.close()
        print(f"  完成: 成功{ok}, 失败{fail}")

    def _compute_exit_one(self, signal_date, ts_code, entry_price):
        """计算单只信号退出"""
        kl = self.all_klines.get(ts_code)
        if kl is None or entry_price <= 0:
            return None

        # 信号日之后的行情
        future = kl[kl['trade_date'] > signal_date].head(15)
        if future.empty:
            return None

        # T+1开盘价买入
        t1 = future.iloc[0]
        buy_price = float(t1['open'])
        buy_date = t1['trade_date']

        stop_loss = buy_price * 0.95
        take_profit = buy_price * 1.10

        exit_date = None
        exit_price = None
        exit_reason = None
        hold_days = 0

        # 从T+1开始遍历(买入当天不算持仓)
        for i, (_, row) in enumerate(future.iterrows()):
            hold_days = i + 1
            # 止损优先
            if float(row['low']) <= stop_loss:
                exit_date = row['trade_date']
                exit_price = stop_loss
                exit_reason = '止损'
                break
            # 止盈
            if float(row['high']) >= take_profit:
                exit_date = row['trade_date']
                exit_price = take_profit
                exit_reason = '止盈'
                break
            # 到期
            if hold_days >= 10:
                exit_date = row['trade_date']
                exit_price = float(row['close'])
                exit_reason = '到期'
                break

        if exit_date is None:
            # 未触发退出,用最后一日收盘
            last = future.iloc[-1]
            exit_date = last['trade_date']
            exit_price = float(last['close'])
            exit_reason = '持仓中'

        pnl = (exit_price - buy_price) / buy_price * 100

        # 区间统计
        highs = future['high'].astype(float).values
        lows = future['low'].astype(float).values
        max_gain = (max(highs) - buy_price) / buy_price * 100 if len(highs) > 0 else 0
        max_dd = (min(lows) - buy_price) / buy_price * 100 if len(lows) > 0 else 0

        # T+5/T+10累计涨幅(从买入价算)
        next_5d = None
        next_10d = None
        if len(future) >= 5:
            next_5d = (float(future.iloc[4]['close']) - buy_price) / buy_price * 100
        if len(future) >= 10:
            next_10d = (float(future.iloc[9]['close']) - buy_price) / buy_price * 100

        return (exit_date, exit_price, exit_reason, round(pnl, 2), hold_days,
                round(max_gain, 2), round(max_dd, 2),
                round(next_5d, 2) if next_5d is not None else None,
                round(next_10d, 2) if next_10d is not None else None)


# ============================================================
# 结果统计与展示
# ============================================================
def show_status():
    """展示回测结果统计"""
    if not os.path.exists(TRACKER_DB):
        print(f"回测数据库不存在: {TRACKER_DB}")
        return

    conn = sqlite3.connect(TRACKER_DB, timeout=10.0)
    total = conn.execute('SELECT COUNT(*) FROM tail_backtest').fetchone()[0]
    if total == 0:
        print("回测数据库为空")
        conn.close()
        return

    closed = conn.execute("SELECT COUNT(*) FROM tail_backtest WHERE status='closed'").fetchone()[0]
    print(f"\n{'═' * 70}")
    print(f"  「猎尾」尾盘突袭战法 - 回测结果")
    print(f"{'═' * 70}")
    print(f"  总信号数: {total} (已平仓: {closed})")

    # 按月统计
    print(f"\n  按月统计:")
    print(f"  {'月份':<8} {'信号':>4} {'平仓':>4} {'胜':>4} {'负':>4} {'胜率':>6} {'均盈亏':>8} {'总盈亏':>8} {'均持仓':>6}")
    rows = conn.execute('''
        SELECT substr(signal_date, 1, 6) as month,
               COUNT(*) as total,
               SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) as closed,
               SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as win,
               SUM(CASE WHEN pnl <= 0 AND status='closed' THEN 1 ELSE 0 END) as lose,
               ROUND(AVG(CASE WHEN status='closed' THEN pnl END), 2) as avg_pnl,
               ROUND(SUM(CASE WHEN status='closed' THEN pnl END), 2) as sum_pnl,
               ROUND(AVG(CASE WHEN status='closed' THEN hold_days END), 1) as avg_hold
        FROM tail_backtest
        GROUP BY month ORDER BY month
    ''').fetchall()
    for r in rows:
        win_rate = f"{r[3]/(r[3]+r[4])*100:.1f}%" if (r[3]+r[4]) > 0 else '-'
        avg_pnl = f"{r[5]}%" if r[5] is not None else '-'
        sum_pnl = f"{r[6]}%" if r[6] is not None else '-'
        avg_hold = f"{r[7]}天" if r[7] is not None else '-'
        print(f"  {r[0]:<8} {r[1]:>4} {r[2]:>4} {r[3]:>4} {r[4]:>4} {win_rate:>6} {avg_pnl:>8} {sum_pnl:>8} {avg_hold:>6}")

    # 退出原因分布
    print(f"\n  退出原因分布:")
    rows = conn.execute('''
        SELECT exit_reason, COUNT(*), ROUND(AVG(pnl), 2), ROUND(AVG(hold_days), 1)
        FROM tail_backtest WHERE exit_reason IS NOT NULL
        GROUP BY exit_reason
    ''').fetchall()
    print(f"  {'原因':<8} {'数量':>4} {'均盈亏':>8} {'均持仓':>6}")
    for r in rows:
        pnl_str = f"{r[2]}%" if r[2] is not None else '-'
        hold_str = f"{r[3]}天" if r[3] is not None else '-'
        print(f"  {r[0] or 'NULL':<8} {r[1]:>4} {pnl_str:>8} {hold_str:>6}")

    # TOP10盈利信号
    print(f"\n  TOP10盈利信号:")
    rows = conn.execute('''
        SELECT signal_date, ts_code, name, theme, total_score, tech_score,
               exit_reason, pnl, hold_days
        FROM tail_backtest WHERE pnl IS NOT NULL
        ORDER BY pnl DESC LIMIT 10
    ''').fetchall()
    print(f"  {'信号日':<10} {'代码':<12} {'名称':<10} {'主题':<12} {'总分':>4} {'技分':>4} {'退出':>6} {'盈亏':>7} {'持仓':>4}")
    for r in rows:
        print(f"  {r[0]:<10} {r[1]:<12} {r[2]:<10} {r[3]:<12} {r[4]:>4} {r[5]:>4} {r[6]:>6} {r[7]:>+7.1f}% {r[8]:>4}天")

    # TOP10亏损信号
    print(f"\n  TOP10亏损信号:")
    rows = conn.execute('''
        SELECT signal_date, ts_code, name, theme, total_score, tech_score,
               exit_reason, pnl, hold_days
        FROM tail_backtest WHERE pnl IS NOT NULL
        ORDER BY pnl ASC LIMIT 10
    ''').fetchall()
    print(f"  {'信号日':<10} {'代码':<12} {'名称':<10} {'主题':<12} {'总分':>4} {'技分':>4} {'退出':>6} {'盈亏':>7} {'持仓':>4}")
    for r in rows:
        print(f"  {r[0]:<10} {r[1]:<12} {r[2]:<10} {r[3]:<12} {r[4]:>4} {r[5]:>4} {r[6]:>6} {r[7]:>+7.1f}% {r[8]:>4}天")

    # 整体统计
    print(f"\n  整体统计(全部信号):")
    rows = conn.execute('''
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as win,
            SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as lose,
            ROUND(AVG(pnl), 2) as avg_pnl,
            ROUND(AVG(max_gain), 2) as avg_max_gain,
            ROUND(AVG(max_drawdown), 2) as avg_max_dd,
            ROUND(AVG(hold_days), 1) as avg_hold,
            ROUND(AVG(next_5d_pct), 2) as avg_5d,
            ROUND(AVG(next_10d_pct), 2) as avg_10d
        FROM tail_backtest WHERE status = 'closed'
    ''').fetchone()
    win_rate = rows[1]/(rows[1]+rows[2])*100 if (rows[1]+rows[2]) > 0 else 0
    print(f"    胜率: {win_rate:.1f}% ({rows[1]}胜/{rows[2]}负/{rows[0]}总)")
    print(f"    均盈亏: {rows[3]}%  均最大收益: {rows[4]}%  均最大回撤: {rows[5]}%")
    print(f"    均持仓: {rows[6]}天  T+5均涨: {rows[7]}%  T+10均涨: {rows[8]}%")

    # 方案K子统计
    print(f"\n  方案K子统计(k_filtered=1):")
    rows = conn.execute('''
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as win,
            SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as lose,
            ROUND(AVG(pnl), 2) as avg_pnl,
            ROUND(AVG(max_gain), 2) as avg_max_gain,
            ROUND(AVG(max_drawdown), 2) as avg_max_dd,
            ROUND(AVG(hold_days), 1) as avg_hold,
            ROUND(AVG(next_5d_pct), 2) as avg_5d,
            ROUND(AVG(next_10d_pct), 2) as avg_10d
        FROM tail_backtest WHERE status = 'closed' AND k_filtered = 1
    ''').fetchone()
    if rows[0] > 0:
        win_rate = rows[1]/(rows[1]+rows[2])*100 if (rows[1]+rows[2]) > 0 else 0
        print(f"    胜率: {win_rate:.1f}% ({rows[1]}胜/{rows[2]}负/{rows[0]}总)")
        print(f"    均盈亏: {rows[3]}%  均最大收益: {rows[4]}%  均最大回撤: {rows[5]}%")
        print(f"    均持仓: {rows[6]}天  T+5均涨: {rows[7]}%  T+10均涨: {rows[8]}%")
    else:
        print(f"    无方案K信号")

    # 按分数段统计
    print(f"\n  按总分段统计(已平仓):")
    print(f"  {'分数段':<10} {'总数':>5} {'胜':>4} {'负':>4} {'胜率':>7} {'均盈亏':>8} {'T+5':>7} {'T+10':>7}")
    rows = conn.execute('''
        SELECT
            CASE
                WHEN total_score >= 95 THEN '95+'
                WHEN total_score >= 90 THEN '90-95'
                WHEN total_score >= 85 THEN '85-90'
                WHEN total_score >= 80 THEN '80-85'
                WHEN total_score >= 75 THEN '75-80'
                WHEN total_score >= 70 THEN '70-75'
                ELSE '65-70'
            END as bucket,
            COUNT(*) as total,
            SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as win,
            SUM(CASE WHEN pnl <= 0 AND status='closed' THEN 1 ELSE 0 END) as lose,
            ROUND(AVG(CASE WHEN status='closed' THEN pnl END), 2) as avg_pnl,
            ROUND(AVG(CASE WHEN status='closed' THEN next_5d_pct END), 2) as avg_5d,
            ROUND(AVG(CASE WHEN status='closed' THEN next_10d_pct END), 2) as avg_10d
        FROM tail_backtest
        GROUP BY bucket ORDER BY bucket DESC
    ''').fetchall()
    for r in rows:
        wr = f"{r[2]/(r[2]+r[3])*100:.1f}%" if (r[2]+r[3]) > 0 else '-'
        ap = f"{r[4]}%" if r[4] is not None else '-'
        a5 = f"{r[5]}%" if r[5] is not None else '-'
        a10 = f"{r[6]}%" if r[6] is not None else '-'
        print(f"  {r[0]:<10} {r[1]:>5} {r[2]:>4} {r[3]:>4} {wr:>7} {ap:>8} {a5:>7} {a10:>7}")

    conn.close()
    print(f"{'═' * 70}\n")


# ============================================================
# 主入口
# ============================================================
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='「猎尾」尾盘突袭战法 - TDX历史回测')
    parser.add_argument('--start', default='20260101', help='起始日期 YYYYMMDD (默认20260101)')
    parser.add_argument('--end', default='20260731', help='结束日期 YYYYMMDD (默认20260731)')
    parser.add_argument('--status', action='store_true', help='只查看回测结果')
    parser.add_argument('--report', action='store_true', help='生成Excel报告')
    args = parser.parse_args()

    if args.status:
        show_status()
    else:
        bt = TailBacktester(args.start, args.end)
        bt.prepare_data()
        bt.run()
        show_status()

        if args.report:
            # 导出Excel
            conn = sqlite3.connect(TRACKER_DB, timeout=10.0)
            df = pd.read_sql_query('SELECT * FROM tail_backtest ORDER BY signal_date, pnl DESC', conn)
            conn.close()
            report_file = os.path.join(CACHE_DIR, f'tail_backtest_report_{args.start}_{args.end}.xlsx')
            df.to_excel(report_file, index=False)
            print(f"\n  Excel报告已生成: {report_file}")
