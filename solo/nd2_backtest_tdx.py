# -*- coding: utf-8 -*-
"""
「猎尾V5」NEXT-DAY ALPHA ENGINE - 通达信本地回测
================================================
数据源:
  - 通达信 .day 日线 (C:/new_tdx/vipdoc/*/lday)      - OHLCV + pct_chg
  - 通达信 .lc5 5分钟线 (C:/new_tdx/vipdoc/*/fzline)  - 重建14:00/14:30分时锚点
  - SQLite stock_data.db stk_factor_pro               - 换手率/总市值
  - theme_stock_map_latest.json                       - 主题成份股

回测口径(与实时14:50扫描语义一致):
  1. 每个交易日T: 用 T 日收盘价作为14:50信号触发价(用户指定"收盘价"口径)
  2. 分时锚点: 从 T 日5分钟K线重建 morning/noon(14:00)/tail_base(14:30) 累计量与14:30价
     -> tail_inc = 全天量(cur_vol) - tail_base_vol(14:30), 与实时一致(14:50后量极少)
  3. kline传截至 T-1(不含当日), 当日信息全部经q传入 -> 无前视偏差
  4. L0市场乘数: 指数日线近似 trend_score -> market_status
  5. 买入: 信号日收盘价; 标签: T+1 最高/最低/收盘 vs 买入价×±2%

用法:
  python -X utf8 nd2_backtest_tdx.py                          # 默认20260224~20260522
  python -X utf8 nd2_backtest_tdx.py --start 20260224 --end 20260522 --limit 250
"""
import os
import sys
import json
import time
import sqlite3
import argparse
import datetime as dt
from collections import defaultdict

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = r'D:\mystock\cache_daily'
TDX_PATH = r'C:\new_tdx'
FACTOR_DB = os.path.join(CACHE_DIR, 'stock_data.db')
THEME_MAP_FILE = os.path.join(CACHE_DIR, 'theme_stock_map_latest.json')
OUT_CSV = os.path.join(CACHE_DIR, 'nd2_backtest_tdx.csv')

sys.path.insert(0, BASE_DIR)
from nd2_alpha import ND2AlphaEngine


def ts_to_tdx_sym(ts_code):
    """600000.SH -> sh600000 ; 932000.CSI -> sh932000(近似)"""
    code, suffix = ts_code.split('.')
    if suffix == 'SH':
        return 'sh' + code
    elif suffix == 'SZ':
        return 'sz' + code
    elif suffix == 'CSI':
        return 'sh' + code
    return None


class V5Selector:
    """
    V5精选器: 从当日信号中选出最多 max_per_day 只最佳标的
    - 基于回测分析: ND2 10-11 甜点区 + BREAKOUT_TAIL 形态 + 尾流≥20
    - 目标: 日频1-2信号, P_up 提升 20pp
    """

    @staticmethod
    def select(signals, max_per_day=2):
        if not signals:
            return signals
        # 硬门槛
        qualified = []
        for s in signals:
            # 核心: ND2甜点区 (10-11)
            if not (10 <= s.get('nd2_potential', 0) < 12):
                continue
            # 形态: 排除STEALTH (历史P_up 31%)
            if s.get('pattern') == 'STEALTH_ACCUMULATION':
                continue
            # 尾流 ≥ 20
            if s.get('tail_flow', 0) < 20:
                continue
            # 形态质量 ≥ 10
            if s.get('pattern_quality', 0) < 10:
                continue
            # 强基因 ≥ 2
            if s.get('strong_gene', 0) < 2:
                continue
            # 涨幅 ≥ 1.5%
            if s.get('pct_chg', 0) < 1.5:
                continue
            # 总分 ≥ 68
            if s.get('final_score', 0) < 68:
                continue
            # 风险 ≤ 2
            if s.get('risk_penalty', 0) > 2:
                continue
            qualified.append(s)
        # 按 rank_score 排序取前 max_per_day
        qualified.sort(key=lambda s: -s.get('rank_score', 0))
        return qualified[:max_per_day]


class ND2Backtester:
    def __init__(self, start_date, end_date, max_candidates=250, use_selector=False):
        self.start_date = start_date
        self.end_date = end_date
        self.max_candidates = max_candidates
        self.use_selector = use_selector
        self.engine = ND2AlphaEngine()
        self.reader = None
        self.theme_stocks = {}      # {theme: [(code,name,layer)]}
        self.stock_themes = {}      # {ts_code: [theme]}
        self.stock_names = {}       # {ts_code: name}
        self.all_klines = {}        # {ts_code: daily df (含pct_chg/vol)}
        self.date_idx = {}          # {ts_code: {trade_date: 行位置}} O(1)索引
        self.index_klines = {}      # {name: daily df}
        self.fz_cache = {}          # {ts_code: fzline df} 懒加载
        self.factor_cache = {}      # {(ts_code, date): (total_mv, turnover_rate)}
        self.trading_dates = []
        self.signals = []           # 全部信号(含T+1标签)
        self.candidates_count = 0   # 粗筛总数(基准对比)

    # ══════════════════════════════════════════
    # 数据加载
    # ══════════════════════════════════════════
    def _get_reader(self):
        if self.reader is None:
            from mootdx.reader import Reader
            self.reader = Reader.factory(market='std', tdxdir=TDX_PATH)
        return self.reader

    def load_theme_stocks(self):
        with open(THEME_MAP_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        layer_map = {'leader': 'leader', 'middle': 'middle', 'core': 'leader',
                     'extended': 'follower', 'follower': 'follower'}
        for theme_name, stocks in data.get('themes', {}).items():
            sl = []
            for s in stocks:
                code = s.get('code', '')
                if not code:
                    continue
                layer = layer_map.get(s.get('irs_layer', ''), 'follower')
                if s.get('score', 0) >= 80:
                    layer = 'leader'
                elif s.get('score', 0) >= 60:
                    layer = 'middle'
                sl.append((code, s.get('name', ''), layer))
                self.stock_names[code] = s.get('name', '')
                self.stock_themes.setdefault(code, []).append(theme_name)
            self.theme_stocks[theme_name] = sl
        # 过滤北交所
        for code in list(self.stock_themes.keys()):
            if code.startswith(('8', '4', '92')):
                del self.stock_themes[code]

    def _load_daily(self, ts_code):
        """读取并规范化一只股票的日线(截至end_date, 前推120天)"""
        r = self._get_reader()
        sym = ts_to_tdx_sym(ts_code)
        if not sym:
            return None
        try:
            df = r.daily(symbol=sym)
        except Exception:
            return None
        if df is None or len(df) == 0:
            return None
        df = df.rename(columns={'volume': 'vol'})
        df = df.reset_index()
        df['trade_date'] = df['date'].dt.strftime('%Y%m%d')
        df = df.sort_values('trade_date').reset_index(drop=True)
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df['open'] = pd.to_numeric(df['open'], errors='coerce')
        df['high'] = pd.to_numeric(df['high'], errors='coerce')
        df['low'] = pd.to_numeric(df['low'], errors='coerce')
        df['vol'] = pd.to_numeric(df['vol'], errors='coerce').fillna(0)
        df['pre_close'] = df['close'].shift(1)
        df['pct_chg'] = df['close'].pct_change() * 100
        df['pct_chg'] = df['pct_chg'].fillna(0)
        # 回测区间(前推120天供MA计算)
        start_dt = dt.datetime.strptime(self.start_date, '%Y%m%d')
        ext_start = (start_dt - dt.timedelta(days=150)).strftime('%Y%m%d')
        df = df[(df['trade_date'] >= ext_start) & (df['trade_date'] <= self.end_date)]
        return df.reset_index(drop=True)

    def load_klines(self):
        codes = list(self.stock_themes.keys())
        t0 = time.time()
        for i, code in enumerate(codes):
            df = self._load_daily(code)
            if df is not None and len(df) >= 30:
                self.all_klines[code] = df
                self.date_idx[code] = {d: j for j, d in enumerate(df['trade_date'].tolist())}
            if (i + 1) % 500 == 0:
                print(f"    日线进度: {i+1}/{len(codes)} ({time.time()-t0:.0f}s)")
        # 指数
        for name, code in [('上证指数', '000001.SH'), ('沪深300', '000300.SH'),
                           ('中证2000', '932000.CSI'), ('深证成指', '399001.SZ')]:
            df = self._load_daily(code)
            if df is not None and len(df) >= 60:
                self.index_klines[name] = df
        print(f"    日线: {len(self.all_klines)}只 指数: {list(self.index_klines.keys())} ({time.time()-t0:.0f}s)")

    def load_factors(self):
        """从 stk_factor_pro 预加载 换手率/总市值(回测区间)"""
        if not os.path.exists(FACTOR_DB):
            print('    ⚠ 因子库不存在')
            return
        conn = sqlite3.connect(FACTOR_DB, timeout=10.0)
        df = pd.read_sql_query(
            'SELECT ts_code, trade_date, total_mv, turnover_rate '
            'FROM stk_factor_pro WHERE trade_date BETWEEN ? AND ?',
            conn, params=(self.start_date, self.end_date))
        conn.close()
        if df.empty:
            return
        df['trade_date'] = df['trade_date'].astype(str)
        for _, row in df.iterrows():
            self.factor_cache[(row['ts_code'], row['trade_date'])] = (
                float(row['total_mv']) if pd.notna(row['total_mv']) else 0,
                float(row['turnover_rate']) if pd.notna(row['turnover_rate']) else 0)
        print(f"    因子缓存: {len(self.factor_cache)}条")

    def get_trading_dates(self):
        dates = set()
        for kl in self.all_klines.values():
            for d in kl['trade_date'].tolist():
                if self.start_date <= d <= self.end_date:
                    dates.add(d)
        self.trading_dates = sorted(dates)
        print(f"    回测交易日: {len(self.trading_dates)}天 [{self.start_date}~{self.end_date}]")

    # ══════════════════════════════════════════
    # 5分钟K线 -> 分时锚点
    # ══════════════════════════════════════════
    def _get_fzline(self, ts_code):
        """懒加载5分钟K线(df 全历史)"""
        if ts_code in self.fz_cache:
            return self.fz_cache[ts_code]
        r = self._get_reader()
        sym = ts_to_tdx_sym(ts_code)
        if not sym:
            self.fz_cache[ts_code] = None
            return None
        try:
            df = r.fzline(symbol=sym)
        except Exception:
            df = None
        if df is None or len(df) == 0:
            self.fz_cache[ts_code] = None
            return None
        df = df.reset_index()
        df['day'] = df['date'].dt.strftime('%Y%m%d')
        df['hhmm'] = df['date'].dt.strftime('%H:%M')
        df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0)
        self.fz_cache[ts_code] = df
        return df

    def rebuild_snap(self, ts_code, trade_date):
        """
        从 T 日5分钟K线重建分时锚点(与实时 collect_intraday_snapshot 同语义)
        - morning_vol/noon_vol/tail_base_vol: 股单位(与实时快照一致, precompute转手)
        - tail_base_price: 14:30 bar 收盘价
        返回 None 表示无锚点(该股当日无5分钟数据)
        """
        fz = self._get_fzline(ts_code)
        if fz is None:
            return None
        day = fz[fz['day'] == trade_date]
        if day.empty:
            return None
        day = day.sort_values('hhmm')
        morning_vol = noon_vol = tail_base_vol = 0.0
        tail_base_price = None
        cum = 0.0
        for _, b in day.iterrows():
            cum += float(b['volume'])
            if b['hhmm'] <= '11:30':
                morning_vol = cum
            if b['hhmm'] <= '14:00':
                noon_vol = cum
            if b['hhmm'] <= '14:30':
                tail_base_vol = cum
                tail_base_price = float(b['close'])
        if tail_base_price is None or tail_base_price <= 0:
            return None
        return {
            'morning_vol': morning_vol,
            'noon_vol': noon_vol,
            'tail_base_vol': tail_base_vol,
            'tail_base_price': tail_base_price,
            'noon_pct': None,
            'tail_base_pct': None,
        }

    # ══════════════════════════════════════════
    # L0 市场状态(指数日线近似)
    # ══════════════════════════════════════════
    @staticmethod
    def _index_trend_score(kl, trade_date):
        """指数趋势分 0~100(简化): MA排列 + 20日涨幅 + MA20位置"""
        rows = kl[kl['trade_date'] <= trade_date]
        if len(rows) < 60:
            return 50.0
        close = float(rows.iloc[-1]['close'])
        ma5 = float(rows['close'].iloc[-5:].mean())
        ma10 = float(rows['close'].iloc[-10:].mean())
        ma20 = float(rows['close'].iloc[-20:].mean())
        ma60 = float(rows['close'].iloc[-60:].mean())
        c20 = float(rows['close'].iloc[-21])
        g20 = (close / c20 - 1) * 100 if c20 > 0 else 0
        score = 50.0
        if close > ma20 > ma60:
            score += 15
        elif close > ma20:
            score += 8
        if close > ma5 > ma10 > ma20:
            score += 10
        elif close > ma10 > ma20:
            score += 5
        if 5 <= g20 <= 20:
            score += 15
        elif 0 <= g20 < 5:
            score += 8
        elif g20 > 20:
            score += 10
        if close < ma20:
            score -= 10
        if g20 < -5:
            score -= 12
        return max(0.0, min(100.0, score))

    def market_status_for(self, trade_date):
        """返回 (trend_score, market_status, index_pct)"""
        scores = {}
        for name, kl in self.index_klines.items():
            scores[name] = self._index_trend_score(kl, trade_date)
        if scores:
            sh = scores.get('上证指数', 50)
            hs = scores.get('沪深300', sh)
            zz = scores.get('中证2000', sh)
            de = scores.get('深证成指', sh)
            # 加权: 上证0.35/沪深300 0.25/中证2000 0.40 (无中证2000时用深证成指近似)
            if '中证2000' in scores:
                index_trend = sh * 0.35 + hs * 0.25 + zz * 0.40
            else:
                index_trend = sh * 0.4 + hs * 0.3 + de * 0.3
        else:
            index_trend = 50.0
        trend_score = round(index_trend, 1)   # ThemeTrend 简化=IndexTrend
        if trend_score >= 80:
            status = '主升浪'
        elif trend_score >= 70:
            status = '强趋势'
        elif trend_score >= 60:
            status = '趋势良好'
        elif trend_score >= 55:
            status = '震荡'
        elif trend_score >= 45:
            status = '弱势'
        elif trend_score >= 35:
            status = '退潮'
        else:
            status = '主跌段'
        # 上证当日涨跌
        index_pct = None
        sh_kl = self.index_klines.get('上证指数')
        if sh_kl is not None:
            row = sh_kl[sh_kl['trade_date'] == trade_date]
            if not row.empty:
                index_pct = float(row.iloc[0]['pct_chg'])
        return trend_score, status, index_pct

    # ══════════════════════════════════════════
    # 主题指标(日线)
    # ══════════════════════════════════════════
    def calc_theme_metrics(self, trade_date):
        """返回 {theme: (strength, up_ratio, limit_count, leader_pct)}  (O(1)索引)"""
        out = {}
        for theme, stocks in self.theme_stocks.items():
            gains = []
            up = zt = 0
            mx = -999.0
            for code, _, _ in stocks:
                kl = self.all_klines.get(code)
                if kl is None:
                    continue
                pos = self.date_idx[code].get(trade_date)
                if pos is None:
                    continue
                pct = float(kl['pct_chg'].iloc[pos])
                gains.append(pct)
                if pct > 0:
                    up += 1
                limit = 19.5 if code.startswith(('300', '688')) else 9.5
                if pct >= limit:
                    zt += 1
                mx = max(mx, pct)
            if not gains:
                out[theme] = (0.0, 50.0, 0, 0.0)
            else:
                out[theme] = (sum(gains) / len(gains), up / len(gains) * 100, zt, mx)
        return out

    # ══════════════════════════════════════════
    # 每日扫描
    # ══════════════════════════════════════════
    def scan_day(self, trade_date, theme_metrics, trend_score, market_status, index_pct):
        """扫描 T 日候选,返回信号列表(含当日信息,标签后续补齐)"""
        signals = []
        # ── 粗筛候选(与 _run_v5_now.py 一致: 涨幅0.5~8%、K线≥21、市值≥8亿、非北交所) ──
        cands = []
        for ts_code, themes in self.stock_themes.items():
            kl = self.all_klines.get(ts_code)
            if kl is None:
                continue
            pos = self.date_idx[ts_code].get(trade_date)
            if pos is None:
                continue
            day = kl.iloc[pos]
            pct = float(day['pct_chg'])
            if not (0.5 <= pct <= 8.0):
                continue
            if pos < 21:
                continue
            mv, _ = self.factor_cache.get((ts_code, trade_date), (0, 0))
            if mv and 0 < mv < 80000:
                continue
            cands.append(ts_code)
        self.candidates_count += len(cands)

        # 量比排序取前 N(与实时一致: 尾盘引擎优先量能放大)
        def vol_ratio_key(ts):
            kl = self.all_klines.get(ts)
            pos = self.date_idx[ts].get(trade_date)
            if pos is None or pos < 1:
                return 0
            yv = float(kl['vol'].iloc[pos - 1])
            cv = float(kl['vol'].iloc[pos])
            return -(cv / yv) if yv > 0 else 0
        cands.sort(key=vol_ratio_key)
        cands = cands[:self.max_candidates]

        for ts_code in cands:
            # 分时锚点(5分钟K线)
            snap = self.rebuild_snap(ts_code, trade_date)
            if snap is None:
                continue
            kl = self.all_klines[ts_code]
            pos = self.date_idx[ts_code][trade_date]
            day = kl.iloc[pos]
            prev = kl.iloc[:pos].copy()   # 截至T-1(无前视)
            # q: 模拟新浪实时(vol单位=股: daily手×100)
            q = {
                'open': float(day['open']),
                'high': float(day['high']),
                'low': float(day['low']),
                'price': float(day['close']),
                'last_close': float(prev.iloc[-1]['close']) if len(prev) else float(day['open']),
                'pct_chg': float(day['pct_chg']),
                'vol': float(day['vol']) * 100.0,
                'name': self.stock_names.get(ts_code, ''),
            }
            mv, turnover = self.factor_cache.get((ts_code, trade_date), (0, 0))
            if turnover is None:
                turnover = 0

            theme_name = self.stock_themes[ts_code][0]
            ts, ur, lc, lp = theme_metrics.get(theme_name, (0.0, 50.0, 0, 0.0))
            # 个股自身涨幅作为个股主题位置
            try:
                sig = self.engine.evaluate(
                    ts_code=ts_code, q=q, kline=prev, snap=snap,
                    turnover=turnover, total_mv=mv,
                    theme_name=theme_name, theme_strength=ts,
                    theme_up_ratio=ur, theme_limit_count=lc,
                    theme_leader_pct=lp,
                    trend_score=trend_score, market_status=market_status,
                    index_pct=index_pct,
                )
            except Exception:
                continue
            if sig is None:
                continue
            if sig.get('grade') == 'REJECT':
                continue
            sig['_signal_date'] = trade_date
            sig['_snap'] = snap
            signals.append(sig)
        # 精选: 每日最多2只
        if self.use_selector:
            signals = V5Selector.select(signals, max_per_day=2)
        return signals

    # ══════════════════════════════════════════
    # 标签回填(T+1 最高/最低/收盘 vs 买入价×±2%)
    # ══════════════════════════════════════════
    def backfill_labels(self):
        for s in self.signals:
            kl = self.all_klines.get(s['ts_code'])
            if kl is None:
                continue
            pos = self.date_idx[s['ts_code']].get(s['_signal_date'])
            if pos is None or pos + 1 >= len(kl):
                continue
            future = kl.iloc[pos + 1:]
            buy = s['price']  # 信号日收盘价 = 买入价
            t1 = future.iloc[0]
            s['_buy'] = buy
            s['_t1_high'] = float(t1['high'])
            s['_t1_low'] = float(t1['low'])
            s['_t1_close'] = float(t1['close'])
            s['y_up_2'] = 1 if s['_t1_high'] >= buy * 1.02 else 0
            s['y_close_2'] = 1 if s['_t1_close'] >= buy * 1.02 else 0
            s['y_dd_2'] = 1 if s['_t1_low'] <= buy * 0.98 else 0
            s['ret_1d'] = (s['_t1_close'] / buy - 1) * 100
            s['ret_3d'] = ((future.iloc[2]['close'] / buy - 1) * 100) if len(future) >= 3 else None
            s['ret_5d'] = ((future.iloc[4]['close'] / buy - 1) * 100) if len(future) >= 5 else None
            s['ret_10d'] = ((future.iloc[9]['close'] / buy - 1) * 100) if len(future) >= 10 else None
            # 未来15日窗口内最大收益/最大回撤(从买入价)
            win = future.head(15)
            s['max_gain'] = (float(win['high'].max()) / buy - 1) * 100
            s['max_dd'] = (float(win['low'].min()) / buy - 1) * 100

    # ══════════════════════════════════════════
    # 主流程
    # ══════════════════════════════════════════
    def run(self):
        print(f"\n{'═'*70}")
        print(f"  「猎尾V5」NEXT-DAY ALPHA ENGINE - 通达信本地回测")
        print(f"  区间: {self.start_date} ~ {self.end_date} | 每日候选上限: {self.max_candidates}")
        print(f"{'═'*70}")
        print("\n[1/4] 加载主题/日线/因子...")
        self.load_theme_stocks()
        print(f"  主题: {len(self.theme_stocks)} 股票: {len(self.stock_themes)}")
        self.load_klines()
        self.load_factors()
        self.get_trading_dates()

        print("\n[2/4] 逐日扫描...")
        t0 = time.time()
        for i, trade_date in enumerate(self.trading_dates):
            trend_score, market_status, index_pct = self.market_status_for(trade_date)
            theme_metrics = self.calc_theme_metrics(trade_date)
            sigs = self.scan_day(trade_date, theme_metrics, trend_score, market_status, index_pct)
            self.signals.extend(sigs)
            if (i + 1) % 5 == 0 or i == len(self.trading_dates) - 1:
                el = time.time() - t0
                eta = el / (i + 1) * (len(self.trading_dates) - i - 1)
                print(f"    [{i+1}/{len(self.trading_dates)}] {trade_date} "
                      f"候选{len(sigs)}只 累计{len(self.signals)}  "
                      f"市场{market_status}({trend_score:.0f}) ETA{eta:.0f}s")
        print(f"  扫描完成: 信号{len(self.signals)}只 (粗筛候选累计{self.candidates_count}) 耗时{time.time()-t0:.0f}s")

        print("\n[3/4] 标签回填(T+1)...")
        self.backfill_labels()
        n_labeled = sum(1 for s in self.signals if s.get('y_up_2') is not None)
        print(f"  已回填: {n_labeled}/{len(self.signals)}")

        print("\n[4/4] 统计报告...")
        self.report()

        # 导出CSV
        cols = ['_signal_date', 'ts_code', 'name', 'theme', 'pattern', 'grade',
                'final_score', 'tail_flow', 'pattern_quality', 'strong_gene',
                'nd2_potential', 'theme_alpha', 'market_alpha', 'bonus', 'risk_penalty',
                'rank_score', 'p_up_2', 'p_dd_2', 'market_multiplier',
                'price', 'pct_chg', 'y_up_2', 'y_close_2', 'y_dd_2',
                'ret_1d', 'ret_3d', 'ret_5d', 'ret_10d', 'max_gain', 'max_dd']
        df = pd.DataFrame([{c: s.get(c) for c in cols} for s in self.signals])
        df.columns = [c.lstrip('_') for c in df.columns]
        df.to_csv(OUT_CSV, index=False, encoding='utf-8-sig')
        print(f"\n  CSV已导出: {OUT_CSV} ({len(df)}行)")

    # ══════════════════════════════════════════
    # 统计
    # ══════════════════════════════════════════
    @staticmethod
    def _fmt_prob(y_col):
        pass

    def _stats_block(self, rows, title, indent=''):
        """输出一组信号的统计: P_UP_2/P_CLOSE_2/P_DD_2 + T+1收益"""
        if not rows:
            print(f"{indent}{title}: 无样本")
            return
        n = len(rows)
        up = sum(1 for r in rows if r.get('y_up_2') == 1)
        cl = sum(1 for r in rows if r.get('y_close_2') == 1)
        dd = sum(1 for r in rows if r.get('y_dd_2') == 1)
        ret1 = np.mean([r['ret_1d'] for r in rows if r.get('ret_1d') is not None])
        win1 = sum(1 for r in rows if r.get('ret_1d') is not None and r['ret_1d'] > 0)
        ret5 = np.mean([r['ret_5d'] for r in rows if r.get('ret_5d') is not None])
        ret10 = np.mean([r['ret_10d'] for r in rows if r.get('ret_10d') is not None])
        mxg = np.mean([r['max_gain'] for r in rows if r.get('max_gain') is not None])
        mxd = np.mean([r['max_dd'] for r in rows if r.get('max_dd') is not None])
        print(f"{indent}{title}: n={n:4d} | P(高≥+2%)={up/n*100:5.1f}%  P(收≥+2%)={cl/n*100:5.1f}%  "
              f"P(破-2%)={dd/n*100:5.1f}%  | T+1均{ret1:+6.2f}% 胜率{win1/n*100:5.1f}%  "
              f"T+5均{ret5:+6.2f}%  T+10均{ret10:+6.2f}%  | 峰值{+mxg:+5.1f}% 谷底{mxd:+5.1f}%")

    def report(self):
        S = self.signals
        labeled = [s for s in S if s.get('y_up_2') is not None]
        print(f"\n{'─'*70}")
        print(f"  总体(仅已回填标签)")
        self._stats_block(labeled, '全部信号')
        # 基准: 粗筛候选的T+1表现(用未过滤信号近似 - 这里用全部评估候选的标签较难, 用B级以下观察)
        print(f"  基准: 粗筛候选累计 {self.candidates_count}只/日均{self.candidates_count/len(self.trading_dates):.0f}只")

        print(f"\n{'─'*70}")
        print("  按分级")
        for g in ['S', 'A', 'B']:
            gr = [s for s in labeled if s['grade'] == g]
            self._stats_block(gr, f'  {g}级')

        print(f"\n{'─'*70}")
        print("  按形态")
        for p in ['PULLBACK_GAP', 'BREAKOUT_TAIL', 'STEALTH_ACCUMULATION']:
            pr = [s for s in labeled if s['pattern'] == p]
            self._stats_block(pr, f'  {p}')

        print(f"\n{'─'*70}")
        print("  按FinalScore段")
        for lo, hi, tag in [(80, 999, '80+'), (75, 80, '75-80'), (70, 75, '70-75'), (65, 70, '65-70')]:
            br = [s for s in labeled if lo <= s['final_score'] < hi]
            self._stats_block(br, f'  {tag}分')

        print(f"\n{'─'*70}")
        print("  按rank_score分位")
        if labeled:
            ranked = sorted(labeled, key=lambda s: -s['rank_score'])
            q1 = ranked[:max(1, len(ranked)//4)]
            q4 = ranked[-max(1, len(ranked)//4):]
            self._stats_block(q1, '  前25%(rank)')
            self._stats_block(q4, '  后25%(rank)')

        print(f"\n{'─'*70}")
        print("  按月")
        months = sorted(set(s['_signal_date'][:6] for s in labeled))
        for m in months:
            mr = [s for s in labeled if s['_signal_date'][:6] == m]
            self._stats_block(mr, f'  {m}')

        print(f"\n{'─'*70}")
        print("  分项均值(全部信号)")
        keys = ['final_score', 'tail_flow', 'pattern_quality', 'strong_gene',
                'nd2_potential', 'theme_alpha', 'market_alpha', 'risk_penalty']
        avg = {k: np.mean([s.get(k, 0) for s in S]) for k in keys}
        print(f"    总分{avg['final_score']:.1f} 尾流{avg['tail_flow']:.1f}/25 形态{avg['pattern_quality']:.1f}/15 "
              f"基因{avg['strong_gene']:.1f}/10 ND2{avg['nd2_potential']:.1f}/15 "
              f"主题{avg['theme_alpha']:.1f}/12 市场{avg['market_alpha']:.1f}/8 风险-{avg['risk_penalty']:.1f}")

        print(f"\n{'─'*70}")
        print("  TOP10 (按rank_score)")
        top = sorted(S, key=lambda s: -s['rank_score'])[:10]
        for s in top:
            t1 = f"T+1={s['ret_1d']:+.1f}%" if s.get('ret_1d') is not None else 'T+1=?'
            print(f"    {s['_signal_date']} {s['name']}({s['ts_code']}) [{s['theme']}] "
                  f"{s['grade']} {s['final_score']}分 rank{s['rank_score']:.3f} "
                  f"形态:{s['pattern'][:10]} {t1} P_up{s['p_up_2']:.0%}")

        print(f"\n{'─'*70}\n")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='猎尾V5 - 通达信本地回测')
    parser.add_argument('--start', default='20260224', help='起始日期(默认20260224, 本地.lc5覆盖起点)')
    parser.add_argument('--end', default='20260522', help='结束日期(默认20260522, 本地.lc5覆盖终点)')
    parser.add_argument('--limit', type=int, default=250, help='每日候选上限(默认250)')
    parser.add_argument('--selector', action='store_true', help='启用V5精选器(日频1-2)')
    args = parser.parse_args()

    bt = ND2Backtester(args.start, args.end, args.limit, use_selector=args.selector)
    bt.run()
