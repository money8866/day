#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
实时扫描模块 - 尾盘实时信号捕捉

运行模式:
  14:30后启动, 每60秒扫描一次全市场主题内股票
  14:50后进入高频模式, 每30秒扫描
  15:00收盘后输出最终信号并推送

数据源: 新浪财经实时行情(主) + 通达信mootdx(备)
"""
import os
import sys
import time
import json
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import defaultdict

import requests
import numpy as np
import pandas as pd

from .data_loader import DataLoader, get_last_trade_date, CACHE_DAILY, PROJECT_ROOT
from .scoring_engine import TailScoringEngine, TailSignal

# 通达信
try:
    from mootdx.quotes import TdxHq_API, config as tdx_config
    TDX_AVAILABLE = True
except ImportError:
    TDX_AVAILABLE = False


class LiveScanner:
    """
    尾盘实时扫描器
    
    14:30后每轮扫描:
    1. 获取全市场主题内股票实时行情(新浪批量接口)
    2. 对每只股票进行六维评分
    3. 输出TOP信号 + 推送微信
    """

    def __init__(self, min_score: float = 65, top_n: int = 15):
        self.min_score = min_score
        self.top_n = top_n
        self.loader = DataLoader()
        self.engine = TailScoringEngine()

        # 实时行情缓存
        self.quotes: Dict[str, Dict] = {}  # ts_code -> {price, pct_chg, high, low, open, vol, amount, ...}
        self.prev_quotes: Dict[str, Dict] = {}

        # 分时快照(14:30基准)
        self.tail_base: Dict[str, Dict] = {}  # ts_code -> {price, vol, time}
        self.tail_base_captured = False

        # 信号历史
        self.signal_history: List[TailSignal] = []
        self.final_signals: List[TailSignal] = []

        # 推送
        self.sckey = os.getenv('WECHAT_SCKEY', '')
        self.pushplus_token = os.getenv('PUSHPLUS', '')

        # 通达信
        self.tdx_api = None
        self.tdx_connected = False

    # ═══════════════════════════════════════════
    # 初始化
    # ═══════════════════════════════════════════
    def init(self) -> bool:
        """初始化: 加载主题映射 + 技术因子"""
        print("🚀 尾盘猎手实时扫描器启动")
        print(f"   最低分数: {self.min_score}  TOP N: {self.top_n}")

        if not self.loader.load_theme_map():
            return False

        # 预加载最新技术因子
        trade_date = get_last_trade_date()
        factor_dates = self.loader.get_factor_dates(limit=3)
        if factor_dates:
            latest_factor_date = factor_dates[0]
            codes = list(self.loader.stock_themes.keys())
            self._factors_df = self.loader.load_factors_batch(latest_factor_date, codes)
            self._factor_map = {}
            if not self._factors_df.empty:
                for _, row in self._factors_df.iterrows():
                    self._factor_map[row['ts_code']] = row
            print(f"✅ 技术因子已加载: {len(self._factor_map)}只 ({latest_factor_date})")
        else:
            self._factor_map = {}
            print("⚠ 无技术因子数据")

        # 预加载日线(用于位置评分)
        self._daily_cache: Dict[str, pd.DataFrame] = {}
        print(f"⏳ 预加载主题股票日线数据...")
        codes = list(self.loader.stock_themes.keys())
        loaded = 0
        for code in codes:
            df = self.loader.load_daily(code, start='20250101')
            if df is not None and len(df) >= 20:
                self._daily_cache[code] = df
                loaded += 1
        print(f"✅ 日线预加载: {loaded}/{len(codes)}只")

        return True

    # ═══════════════════════════════════════════
    # 行情获取
    # ═══════════════════════════════════════════
    def fetch_quotes(self) -> int:
        """从新浪财经批量获取实时行情, 返回获取数量"""
        codes = list(self.loader.stock_themes.keys())
        quote_map = {}

        for offset in range(0, len(codes), 180):
            batch = codes[offset:offset + 180]
            sina_list = []
            for code in batch:
                if code.endswith('.SH'):
                    sina_list.append('sh' + code.replace('.SH', ''))
                elif code.endswith('.SZ'):
                    sina_list.append('sz' + code.replace('.SZ', ''))

            url = f"https://hq.sinajs.cn/list={','.join(sina_list)}"
            try:
                resp = requests.get(url, headers={
                    'Referer': 'https://finance.sina.com.cn',
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }, timeout=10)
                resp.encoding = 'gbk'
                for line in resp.text.strip().split('\n'):
                    line = line.strip()
                    if not line or '=' not in line:
                        continue
                    try:
                        var_part = line.split('=', 1)[1]
                        if var_part.count('"') < 2:
                            continue
                        data_str = var_part.split('"')[1]
                        fields = data_str.split(',')
                        var_name = line.split('hq_str_')[1].split('=')[0]

                        if var_name.startswith('sz'):
                            ts_c = var_name[2:].zfill(6) + '.SZ'
                        elif var_name.startswith('sh'):
                            ts_c = var_name[2:].zfill(6) + '.SH'
                        else:
                            continue

                        if len(fields) < 32:
                            continue

                        prev_close = float(fields[2])
                        price = float(fields[3])
                        high = float(fields[4])
                        low = float(fields[5])
                        volume = float(fields[8])
                        amount = float(fields[9])
                        open_p = float(fields[1])

                        if prev_close > 0 and price > 0:
                            pct_chg = (price - prev_close) / prev_close * 100
                        else:
                            pct_chg = 0

                        quote_map[ts_c] = {
                            'price': price,
                            'pct_chg': round(pct_chg, 2),
                            'high': high,
                            'low': low,
                            'open': open_p,
                            'vol': volume,
                            'amount': amount,
                            'last_close': prev_close,
                            'pre_close': prev_close,
                            'close': price,
                        }
                    except Exception:
                        continue
                time.sleep(0.05)
            except Exception as e:
                print(f"⚠ 行情获取失败: {e}")
                continue

        self.prev_quotes = self.quotes.copy()
        self.quotes = quote_map
        return len(quote_map)

    # ═══════════════════════════════════════════
    # 尾盘基准捕获 (14:30)
    # ═══════════════════════════════════════════
    def capture_tail_base(self):
        """14:30捕获尾盘基准价格和成交量"""
        if self.tail_base_captured:
            return
        now = datetime.now()
        if now.hour == 14 and now.minute >= 30:
            for code, q in self.quotes.items():
                self.tail_base[code] = {
                    'price': q.get('price', 0),
                    'vol': q.get('vol', 0),
                    'time': now.strftime('%H:%M:%S'),
                }
            self.tail_base_captured = True
            print(f"✅ 尾盘基准已捕获: {len(self.tail_base)}只 ({now.strftime('%H:%M:%S')})")

    # ═══════════════════════════════════════════
    # 扫描
    # ═══════════════════════════════════════════
    def scan(self) -> List[TailSignal]:
        """执行一轮扫描, 返回信号列表"""
        signals = []
        trade_date = get_last_trade_date()

        for ts_code in self.loader.stock_themes:
            q = self.quotes.get(ts_code)
            if not q or q.get('price', 0) <= 0:
                continue

            # 构造row (兼容评分引擎)
            row = pd.Series(q)

            # 日线数据
            daily_df = self._daily_cache.get(ts_code)
            if daily_df is None or len(daily_df) < 20:
                continue

            # 如果有尾盘基准, 增强攻击力评分
            if ts_code in self.tail_base:
                base = self.tail_base[ts_code]
                base_price = base.get('price', 0)
                if base_price > 0 and q['price'] > 0:
                    tail_rally = (q['price'] - base_price) / base_price * 100
                    row['tail_rally'] = tail_rally

            # 技术因子
            factor_row = self._factor_map.get(ts_code)

            # 获取名称
            name = ''
            for theme, stocks in self.loader.theme_stocks.items():
                for code, n, _ in stocks:
                    if code == ts_code:
                        name = n
                        break
                if name:
                    break

            # 评分
            sig = self.engine.score_stock(
                ts_code=ts_code,
                name=name,
                row=row,
                daily_df=daily_df,
                factor_row=factor_row,
                theme_stocks=self.loader.theme_stocks,
                stock_themes=self.loader.stock_themes,
                all_quotes=self.quotes,
                trade_date=trade_date,
            )

            if sig and sig.total_score >= self.min_score:
                signals.append(sig)

        signals.sort(key=lambda s: s.total_score, reverse=True)
        return signals[:self.top_n]

    # ═══════════════════════════════════════════
    # 主循环
    # ═══════════════════════════════════════════
    def run(self):
        """主运行循环"""
        if not self.init():
            return

        print(f"\n⏰ 等待尾盘时段 (14:30-15:00)...")
        print(f"   当前时间: {datetime.now().strftime('%H:%M:%S')}")

        # 等待到14:25
        while True:
            now = datetime.now()
            # 非交易日/非交易时段直接运行一次演示
            if now.hour >= 15:
                print("⚠ 已收盘, 执行一次演示扫描")
                self._run_once()
                return
            if now.hour == 14 and now.minute >= 25:
                break
            if now.hour < 14:
                # 距离14:25还早, 先做一次数据预热
                print(f"   数据预热中... ({now.strftime('%H:%M:%S')})")
                n = self.fetch_quotes()
                print(f"   获取行情: {n}只")
                # 每5分钟刷新一次直到14:25
                wait_sec = min(300, (14 * 60 + 25 - now.hour * 60 - now.minute) * 60)
                time.sleep(min(wait_sec, 60))
                continue
            time.sleep(10)

        # 尾盘主循环
        print(f"\n🎯 尾盘扫描开始 [{datetime.now().strftime('%H:%M:%S')}]")
        scan_count = 0

        while True:
            now = datetime.now()
            if now.hour >= 15:
                break

            # 获取行情
            n = self.fetch_quotes()
            if n == 0:
                time.sleep(5)
                continue

            # 捕获14:30基准
            self.capture_tail_base()

            # 扫描
            signals = self.scan()
            scan_count += 1

            # 输出
            self._print_signals(signals, scan_count)
            self.signal_history.extend(signals)

            # 14:50后高频
            if now.hour == 14 and now.minute >= 50:
                interval = 30
            else:
                interval = 60

            time.sleep(interval)

        # 收盘后最终信号
        self._finalize()

    def _run_once(self):
        """非交易时段执行一次扫描(演示/测试)"""
        n = self.fetch_quotes()
        print(f"   获取行情: {n}只")
        if n > 0:
            signals = self.scan()
            self._print_signals(signals, 1)
            self._save_signals(signals)

    def _finalize(self):
        """收盘后最终汇总"""
        # 最后一轮扫描
        self.fetch_quotes()
        final = self.scan()
        self.final_signals = final

        print(f"\n{'='*70}")
        print(f"🏆 尾盘猎手最终信号 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")
        print(f"{'='*70}")
        self._print_signals(final, 0)

        # 保存
        self._save_signals(final)

        # 推送
        if final:
            self._push_wechat(final)

    # ═══════════════════════════════════════════
    # 输出 & 推送
    # ═══════════════════════════════════════════
    def _print_signals(self, signals: List[TailSignal], scan_no: int):
        """控制台输出信号"""
        now = datetime.now().strftime('%H:%M:%S')
        if scan_no > 0:
            print(f"\n{'─'*60}")
            print(f"📡 第{scan_no}轮扫描 [{now}] 信号{len(signals)}只")
        else:
            print(f"\n{'─'*60}")

        if not signals:
            print("   (无满足条件的信号)")
            return

        for i, s in enumerate(signals[:10], 1):
            flag = '🔴' if s.signal == '强买入' else ('🟡' if s.signal == '买入' else '⚪')
            print(f"  {flag} #{i} {s.name}({s.ts_code}) {s.signal} "
                  f"总分{s.total_score:.0f} "
                  f"[攻{s.attack_score:.0f}/构{s.structure_score:.0f}/位{s.position_score:.0f}"
                  f"/技{s.technical_score:.0f}/题{s.theme_score:.0f}/资{s.capital_score:.0f}"
                  f"/扣{s.trap_penalty:.0f}] "
                  f"涨{s.pct_chg:+.1f}% ¥{s.price:.2f} "
                  f"主题:{s.theme}")

    def _save_signals(self, signals: List[TailSignal]):
        """保存信号到SQLite"""
        if not signals:
            return
        db_path = os.path.join(CACHE_DAILY, 'tail_signal_tracker.db')
        try:
            conn = sqlite3.connect(db_path, timeout=10.0)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS tail_signal_tracker (
                    signal_date TEXT NOT NULL,
                    signal_time TEXT,
                    ts_code TEXT NOT NULL,
                    name TEXT,
                    theme TEXT,
                    signal TEXT,
                    total_score INTEGER,
                    attack_score INTEGER,
                    structure_score INTEGER,
                    position_score INTEGER,
                    theme_score INTEGER,
                    tech_score INTEGER,
                    trap_penalty INTEGER,
                    pct_chg REAL,
                    price REAL,
                    detail_json TEXT,
                    next_open REAL, next_close REAL, next_pct_chg REAL,
                    next_high REAL, next_low REAL,
                    next_5d_pct REAL, next_10d_pct REAL,
                    max_gain REAL, max_drawdown REAL,
                    exit_date TEXT, exit_price REAL, exit_reason TEXT,
                    pnl REAL, status TEXT DEFAULT 'pending',
                    note TEXT, updated_at TEXT,
                    PRIMARY KEY (signal_date, ts_code)
                )
            ''')
            trade_date = get_last_trade_date()
            now_str = datetime.now().strftime('%H:%M:%S')
            for s in signals:
                conn.execute('''
                    INSERT OR REPLACE INTO tail_signal_tracker (
                        signal_date, signal_time, ts_code, name, theme, signal,
                        total_score, attack_score, structure_score, position_score,
                        theme_score, tech_score, trap_penalty, pct_chg, price, detail_json,
                        status, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'pending', ?)
                ''', (
                    trade_date, now_str, s.ts_code, s.name, s.theme, s.signal,
                    int(s.total_score), int(s.attack_score), int(s.structure_score),
                    int(s.position_score), int(s.theme_score), int(s.technical_score),
                    int(s.trap_penalty), s.pct_chg, s.price,
                    json.dumps(s.detail, ensure_ascii=False), now_str,
                ))
            conn.commit()
            conn.close()
            print(f"💾 信号已保存: {len(signals)}只 -> {db_path}")
        except Exception as e:
            print(f"⚠ 信号保存失败: {e}")

    def _push_wechat(self, signals: List[TailSignal]):
        """推送到微信(Server酱 + PushPlus)"""
        if not signals:
            return

        now = datetime.now().strftime('%H:%M')
        title = f"🎯 尾盘猎手信号 {now} ({len(signals)}只)"
        lines = [f"尾盘猎手 {now}", ""]
        for i, s in enumerate(signals[:8], 1):
            lines.append(
                f"{i}. {s.name}({s.ts_code}) {s.signal} "
                f"{s.total_score:.0f}分 涨{s.pct_chg:+.1f}% "
                f"[{s.theme}]"
            )
        content = '\n'.join(lines)

        # Server酱
        if self.sckey:
            try:
                requests.post(
                    f"https://sctapi.ftqq.com/{self.sckey}.send",
                    data={'title': title, 'desp': content},
                    timeout=10
                )
                print("📱 Server酱推送成功")
            except Exception as e:
                print(f"⚠ Server酱推送失败: {e}")

        # PushPlus
        if self.pushplus_token:
            try:
                requests.post(
                    "http://www.pushplus.plus/send",
                    json={
                        'token': self.pushplus_token,
                        'title': title,
                        'content': content.replace('\n', '<br>'),
                        'template': 'html',
                    },
                    timeout=10
                )
                print("📱 PushPlus推送成功")
            except Exception as e:
                print(f"⚠ PushPlus推送失败: {e}")
