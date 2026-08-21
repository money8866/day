#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
游资级别实时主题盯盘系统

功能:
1. 从 theme_stock_map_latest.json 加载主题+成分股映射
2. 通过通达信实时行情获取1分钟级数据
3. 计算各主题实时强度(涨幅+成交额加权),捕捉最先启动的主题
4. 检测各主题内最先启动的个股(游资先锋)
5. 整体市场情绪预警(大面积亏钱/普涨)
6. 通过Server酱推送到微信

运行:python realtime_theme_monitor.py
"""
import io
import os
import sys
import time
import json
import sqlite3
import subprocess
import threading
from datetime import datetime, timedelta
from collections import defaultdict, deque

# =========================
# Windows GBK 控制台输出修复:使用环境变量 PYTHONIOENCODING
# (禁止 sys.stdout = io.TextIOWrapper, 会导致底层 buffer 被 GC 后 close, 引发 I/O on closed file)
# =========================
if sys.platform == 'win32':
    import locale
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import requests
from dotenv import load_dotenv

# ── 同花顺扶摇 MCP 补充数据源 ──
from fuyao_mcp_client import FuyaoMCPClient, format_limit_up_summary, format_hot_stock_summary

# ── 猎尾V5 NEXT-DAY ALPHA ENGINE (ND2) ──
from nd2_alpha import ND2AlphaEngine, V5Selector
from nd2_store import ND2SnapshotStore
from nd2_report import format_console_report, format_wechat_message, PATTERN_CN

# 优先加载项目根目录下 .env；向后兼容 config/.env
_pwd = os.path.dirname(os.path.abspath(__file__))
for _env_path in (
    os.path.join(_pwd, '.env'),
    os.path.join(_pwd, '..', 'config', '.env'),
):
    if os.path.exists(_env_path):
        load_dotenv(_env_path)
        break

# ── 通达信(使用 mootdx) ──
try:
    from mootdx.quotes import TdxHq_API, config
    TDX_AVAILABLE = True
except ImportError:
    TDX_AVAILABLE = False

# ── Tushare(仅用于盘后初始化缓存) ──
try:
    import tushare as ts
    pro = ts.pro_api(os.getenv('TUSHARE_TOKEN'))
    TS_AVAILABLE = True
except:
    TS_AVAILABLE = False

# ── 主题评分算法(来自 theme_trend_sentiment_score.py) ──
import numpy as np
try:
    from theme_trend_sentiment_score import (
        per_stock_features, calc_trend_score, calc_sentiment_score,
        calc_theme_hot_score, get_theme_hot_score_percentile, judge_hot_phase,
        sigmoid, linear
    )
    THEME_SCORE_AVAILABLE = True
except Exception as e:
    print(f"⚠ 主题评分模块加载失败: {e},将使用简化评分")
    THEME_SCORE_AVAILABLE = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")
DB_PATH = os.path.join(CACHE_DIR, "theme_portfolio.db")
# 三大指数代码(用于趋势分/情绪分计算)
INDEX_CODES = {
    "上证指数": "000001.SH",
    "沪深300": "000300.SH",
    "中证2000": "932000.CSI",   # Tushare代码(历史K线用)
}
# 新浪财经指数代码映射(fetch_index_quotes 用)
SINA_INDEX_CODES = {
    "上证指数": "sh000001",
    "沪深300": "sh000300",
    "中证2000": "sz399303",     # 新浪无932000,用国证2000替代
}


class RealtimeThemeMonitor:
    def __init__(self):
        self.api = None
        self.connected = False
        self.best_server = None

        # ── 行情缓存(每分钟更新) ──
        self.quotes = {}            # ts_code -> {price, pct_chg, amount, vol}
        self.prev_quotes = {}       # 上一分钟快照
        self.index_quotes_cache = {}  # 三大指数实时行情 name -> {pct_chg,...}

        # ── 主题数据 ──
        self.theme_stocks = {}      # theme_name -> [(ts_code, name, layer)]
        self.theme_names = []       # 有序主题列表
        self.stock_themes = {}      # ts_code -> [theme_name, ...]

        # ── theme.json 配置 ──
        self.theme_config = {}      # theme_name -> 主题配置字典
        self.theme_json_path = os.path.join(BASE_DIR, 'theme.json')

        # ── 主题历史强度(用于趋势判定) ──
        self.theme_score_history = defaultdict(lambda: deque(maxlen=15))
        self.theme_volume_history = defaultdict(lambda: deque(maxlen=15))

        # ── 指数历史K线(盘后缓存,用于MA5/10/20) ──
        self.index_klines = {}      # name -> DataFrame

        # ── 成分股历史K线(用于主题趋势/情绪分计算) ──
        self.stock_klines = {}      # ts_code -> DataFrame (trade_date, close, high, low, vol, pct_chg)

        # ── 个股技术因子缓存(从cache_daily/stk_factor_*.csv加载,含MACD/KDJ/RSI/BOLL) ──
        self.stock_factors = {}     # ts_code -> DataFrame (含技术指标)
        self.stock_mv = {}          # ts_code -> total_mv (总市值,单位万元)

        # ── 冷却控制(避免重复推送) ──
        self.last_theme_alert = {}      # theme_name -> timestamp
        self.last_market_alert = 0
        self.last_score_alert = 0       # 趋势总评分预警冷却

        # ── 开盘参考价(昨日收盘) ──
        self.ref_prices = {}            # ts_code -> yesterday_close

        # ── 开盘分析标记 ──
        self.opening_analysis_done = False

        # ── 主题生命周期 & T+1预测 ──
        self.stock_zt_first_time = {}      # ts_code -> 首次涨停时间(datetime)
        self.theme_amount_at_1430 = {}     # theme_name -> 14:30成交额基准
        self.theme_lifecycle_cache = {}    # theme_name -> (stage, score, detail)
        self.theme_forward_cache = {}      # theme_name -> t1_score (前瞻分)

        # ── 中军弱转强:分时快照 ──
        # ts_code -> {'morning_min_pct':, 'morning_avg_pct':, 'morning_min_price':, 'morning_amount':, 'afternoon_amount':, 'tail_amount':, 'morning_vol':, 'tail_vol':}
        self.intraday_snapshots = {}
        self.snapshot_morning_done = False    # 10:30后采集早盘数据
        self.snapshot_noon_done = False       # 14:00后采集午盘数据
        self.snapshot_tail_done = False       # 14:30基准已采集
        self.last_w2s_scan_time = 0           # 弱转强扫描冷却
        self.w2s_debug_printed = False       # 弱转强首次扫描输出统计
        self.last_tail_entry_scan_time = 0   # 尾盘突袭扫描冷却
        self.tail_entry_debug_printed = False  # 尾盘突袭首次扫描输出统计
        self.last_market_action_alert = 0      # 大盘动作信号冷却
        self._market_action_triggered = {}     # 防重复: {条件名: 触发时间}
        # 尾盘信号跟踪表(用于未来交易日盘后回填和胜率分析)
        self.tail_tracker_db = os.path.join(BASE_DIR, '..', 'cache_daily', 'tail_signal_tracker.db')
        self._init_tail_tracker()

        # ── 同花顺扶摇 MCP 补充数据 ──
        self.fuyao = FuyaoMCPClient()
        self.fuyao_data = {}                  # 缓存MCP数据
        self.fuyao_last_fetch = {}            # 各工具上次调用时间
        self.fuyao_zt_pool_enabled = True     # 涨停池是否启用
        self.fuyao_dragon_tiger_done = False  # 龙虎榜是否已获取

        # ── 猎尾V5 NEXT-DAY ALPHA (ND2) ──
        self.nd2_engine = ND2AlphaEngine()
        self.nd2_store = ND2SnapshotStore()
        self.nd2_last_scan_time = 0           # V5扫描冷却
        self.nd2_debug_printed = False        # 首次扫描诊断输出
        self.nd2_pushed_time = 0              # 推送冷却(每日一次S/A级推送)

        # ── 换手率缓存(从cache_daily加载) ──
        self.turnover_cache = {}              # ts_code -> turnover_rate(%)

        # ── 服务器列表(使用 mootdx 服务器配置 + 已知可用服务器) ──
        seen = set()
        self.servers = []

        # 从 mootdx config 加载默认服务器列表
        if TDX_AVAILABLE:
            try:
                for host_info in config.HQ_HOSTS:
                    # HQ_HOSTS 格式: (name, ip, port)
                    if len(host_info) >= 3:
                        ip = host_info[1]
                        port = host_info[2]
                        if ip and (ip, port) not in seen:
                            self.servers.append((ip, port))
                            seen.add((ip, port))
            except Exception:
                pass

        # 已知可用的通达信行情服务器(银河证券、国泰君安等)
        extras = [
            # 银河证券服务器
            ("120.76.1.198", 7709),      # 银河证券阿里云行情
            ("222.73.48.27", 7709),      # 银河证券上证云行情
            ("120.76.4.28", 7719),       # 银河证券金融终端阿里云
            ("1.202.143.37", 7709),      # 银河证券富丰电信
            ("111.203.134.118", 7709),   # 银河证券富丰联通
            ("117.133.128.226", 7709),   # 银河证券富丰移动
            ("103.251.85.214", 7709),    # 银河证券上证云上海一
            ("114.141.177.40", 7709),    # 银河证券上证云上海二
            ("27.151.2.113", 7709),      # 银河证券上证云福州一
            ("27.151.2.38", 7709),       # 银河证券上证云福州二
            ("202.100.166.12", 7709),    # 银河证券上证云新疆
            # 备用服务器
            ("119.147.212.81", 7727),    # 备用线路1
            ("119.147.212.80", 7727),    # 备用线路2
            # 原 pytdx 常用服务器
            ("180.153.18.170", 7709),
            ("180.153.18.171", 7709),
            ("202.108.253.130", 7709),
            ("202.108.253.131", 7709),
            ("119.147.164.60", 7709),
            ("jstdx.gtjas.com", 7709),   # 国泰君安
            ("shtdx.gtjas.com", 7709),   # 国泰君安
            ("61.152.249.56", 7709),
            ("60.191.117.167", 7709),
            ("218.108.98.244", 7709),
        ]
        for ip, port in extras:
            if (ip, port) not in seen:
                self.servers.append((ip, port))
                seen.add((ip, port))
        print(f"📡 加载通达信服务器池: {len(self.servers)} 台 (mootdx config + 已知可用服务器)")

        self.sckey = os.getenv("WECHAT_SCKEY")

        # ── 服务器轮巡状态 ──
        self.sorted_servers = []    # 按延迟排序的服务器列表
        self.server_index = 0       # 当前尝试的服务器索引

    # ════════════════════════════════════════════
    # 1. 数据加载
    # ════════════════════════════════════════════
    def _load_theme_json(self):
        """从 theme.json 加载主题配置(龙头/中军/核心公司)"""
        if not os.path.exists(self.theme_json_path):
            print(f"⚠ 未找到 {self.theme_json_path},无法加载主题配置")
            return

        try:
            with open(self.theme_json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            self.theme_config = data.get('HOT_THEMES', {})
            print(f"✅ 从theme.json加载: {len(self.theme_config)} 个主题配置")

            # 打印每个主题的龙头公司
            for theme_name, cfg in self.theme_config.items():
                leaders = cfg.get('leader_companies', [])
                cores = cfg.get('core_companies', [])
                if leaders:
                    print(f"   📌 {theme_name}: 龙头[{', '.join(leaders[:3])}] 核心{len(cores)}家")
        except Exception as e:
            print(f"⚠ theme.json加载失败: {e},无法加载主题配置")
            self.theme_config = {}

    def _get_stock_layer(self, name, theme_name):
        """
        根据 theme.json 判定股票层级
        返回: 'leader'(龙头) / 'middle'(中军) / 'member'(成分股)
        """
        cfg = self.theme_config.get(theme_name)
        if not cfg:
            return 'member'

        leader_companies = cfg.get('leader_companies', [])
        core_companies = cfg.get('core_companies', [])

        # 检查是否为龙头公司(leader_companies中的前3名)
        for leader_name in leader_companies:
            if leader_name in name:
                return 'leader'

        # 检查是否为核心公司(core_companies中)
        for core_name in core_companies:
            if core_name in name:
                return 'middle'

        return 'member'

    def _load_all_stocks_from_tushare(self):
        """
        从Tushare获取全市场股票列表(带缓存)
        返回: name_to_code字典 {name: ts_code}
        """
        cache_file = os.path.join(CACHE_DIR, "all_stocks_name_map.pkl")

        # 检查缓存(缓存有效期1天)
        if os.path.exists(cache_file):
            cache_mtime = os.path.getmtime(cache_file)
            import time
            if time.time() - cache_mtime < 86400:  # 24小时内有效
                import pickle
                with open(cache_file, 'rb') as f:
                    return pickle.load(f)

        if not TS_AVAILABLE:
            print("⚠ Tushare不可用,无法获取全市场股票列表")
            return {}

        try:
            # 获取全市场股票列表(主板+科创板+创业板)
            stocks = []
            for status in ['L', 'D', 'P']:  # 上市、退市、暂停
                df = pro.stock_basic(exchange='', list_status=status,
                                    fields='ts_code,symbol,name,list_date')
                if not df.empty:
                    stocks.append(df)

            import pandas as pd
            df_all = pd.concat(stocks, ignore_index=True)

            # 只保留上市状态的股票(沪市.SH 深市.SZ)
            df_all = df_all[df_all['ts_code'].str.endswith(('.SH', '.SZ'))]

            # 构建名称到代码的映射
            name_to_code = {}
            for _, row in df_all.iterrows():
                name = str(row['name']).strip()
                ts_code = str(row['ts_code']).strip()
                if name and ts_code:
                    # 精确匹配
                    if name not in name_to_code:
                        name_to_code[name] = ts_code

            # 缓存
            import pickle
            with open(cache_file, 'wb') as f:
                pickle.dump(name_to_code, f)

            print(f"✅ 从Tushare获取全市场股票列表: {len(name_to_code)} 只")
            return name_to_code

        except Exception as e:
            print(f"⚠ 从Tushare获取股票列表失败: {e}")
            return {}

    def _match_theme_stocks(self, name_to_code):
        """
        根据theme.json中的core_companies和leader_companies匹配股票代码
        返回: theme_stocks, stock_themes
        """
        theme_stocks = {}
        stock_themes = {}
        matched_count = 0
        total_companies = 0

        for theme_name, cfg in self.theme_config.items():
            theme_stocks[theme_name] = []

            # 获取龙头公司和核心公司
            leader_companies = cfg.get('leader_companies', [])
            core_companies = cfg.get('core_companies', [])

            # 合并所有公司(龙头在前)
            all_companies = leader_companies + core_companies
            total_companies += len(all_companies)

            for company_name in all_companies:
                # 尝试精确匹配
                ts_code = name_to_code.get(company_name)

                if ts_code:
                    # 确定层级
                    if company_name in leader_companies:
                        layer = 'leader'
                    else:
                        layer = 'middle'

                    theme_stocks[theme_name].append((ts_code, company_name, layer))

                    # 记录股票所属主题
                    if ts_code not in stock_themes:
                        stock_themes[ts_code] = []
                    if theme_name not in stock_themes[ts_code]:
                        stock_themes[ts_code].append(theme_name)

                    matched_count += 1
                else:
                    print(f"   ⚠ 未找到股票: {company_name} (主题:{theme_name})")

        print(f"✅ 股票匹配完成: {matched_count}/{total_companies} 只匹配成功")
        return theme_stocks, stock_themes

    def load_theme_db(self):
        # 从 theme_stock_map_latest.json 加载主题和成分股数据(直接读取匹配表,不自行运算)
        # 兼容路径:优先用 BASE_DIR 上级 cache_daily,回退绝对路径
        json_path = os.path.join(BASE_DIR, '..', 'cache_daily', 'theme_stock_map_latest.json')
        if not os.path.exists(json_path):
            json_path = r'D:\mystock\cache_daily\theme_stock_map_latest.json'

        if not os.path.exists(json_path):
            print(f"❌ 未找到主题成份股文件: {json_path},无法加载主题数据")
            sys.exit(1)

        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            print(f"❌ 加载主题成份股文件失败: {e}")
            sys.exit(1)

        themes_data = data.get('themes', {})
        if not themes_data:
            print("❌ 主题成份股文件中无主题数据")
            sys.exit(1)

        trade_date = data.get('trade_date', '?')
        n_stocks_meta = data.get('n_stocks', '?')
        print(f"✅ 从 theme_stock_map_latest.json 加载 (trade_date={trade_date}):")
        print(f"   主题数: {len(themes_data)} 个  股票数: {n_stocks_meta} 只")

        # 构建 主题->股票列表 和 股票->主题列表
        # layer 映射: via='leader_company' -> leader, via='core_company' -> middle, 其余 -> member
        self.theme_stocks = {}
        self.stock_themes = {}

        for theme_name, stocks in themes_data.items():
            self.theme_stocks[theme_name] = []
            for stock_info in stocks:
                ts_code = stock_info.get('code')
                if not ts_code:
                    continue
                name = stock_info.get('name', '')
                via = stock_info.get('via', '')
                if via == 'leader_company':
                    layer = 'leader'
                elif via == 'core_company':
                    layer = 'middle'
                else:
                    layer = 'member'

                self.theme_stocks[theme_name].append((ts_code, name, layer))

                if ts_code not in self.stock_themes:
                    self.stock_themes[ts_code] = []
                if theme_name not in self.stock_themes[ts_code]:
                    self.stock_themes[ts_code].append(theme_name)

        # 构建主题名称列表(按JSON中主题顺序)
        self.theme_names = list(themes_data.keys())

        total_stocks = sum(len(v) for v in self.theme_stocks.values())
        unique_stocks = len(self.stock_themes)

        # 统计跨主题股票
        multi_theme_stocks = {code: len(themes) for code, themes in self.stock_themes.items() if len(themes) > 1}

        print(f"   加载完成: 主题{len(self.theme_stocks)}个 股票{unique_stocks}只 (共{total_stocks}只次) 跨主题{len(multi_theme_stocks)}只")
        print(f"   主题列表: {', '.join(self.theme_names)}")

        # 打印跨主题股票示例
        if multi_theme_stocks:
            print(f"   跨主题股票示例:")
            for code, count in list(multi_theme_stocks.items())[:5]:
                themes = self.stock_themes.get(code, [])
                print(f"      {code}: {count}个主题 ({', '.join(themes)})")

    def load_ref_prices(self):
        """从Tushare获取昨日收盘价(有缓存则用缓存)"""
        if not TS_AVAILABLE:
            print("⚠ Tushare不可用,无法获取收盘价")
            return

        from datetime import datetime as dt
        now = dt.now()

        if now.hour < 15:
            query_date = (now - timedelta(days=1)).strftime('%Y%m%d')
        else:
            query_date = now.strftime('%Y%m%d')

        trade_date = self._get_last_trade_date()  # 使用独立函数,避免依赖Tushare

        cache_file = os.path.join(CACHE_DIR, f"ref_prices_{trade_date}.pkl")

        # 优先尝试Tushare获取昨日收盘价
        all_codes = list(self.stock_themes.keys())
        print(f"⏳ 获取{trade_date}日线数据,共{len(all_codes)}只...")

        tushare_count = 0
        if TS_AVAILABLE:
            # V3: 优先从 daily_cache 表读取已有数据(即使不完整也先用)
            try:
                from stock_cache import get_daily_by_date, get_daily_by_date_count, batch_insert_daily_cache
                daily = get_daily_by_date(trade_date)
                if daily is not None and not daily.empty:
                    daily = daily[daily['ts_code'].isin(all_codes)]
                    for _, row in daily.iterrows():
                        self.ref_prices[row['ts_code']] = {
                            'close': row['close'],
                            'pct_chg': row['pct_chg']
                        }
                    tushare_count = len(self.ref_prices)
                    cached_count = len(self.ref_prices)
                    print(f"   [缓存] daily_cache命中 {cached_count}/{len(all_codes)} 只")
            except Exception as e:
                print(f"   ⚠ daily_cache 读取失败: {e}")
                cached_count = 0

            # 只对缓存中缺失的股票走Tushare
            missing_from_cache = [code for code in all_codes if code not in self.ref_prices]
            if missing_from_cache:
                print(f"   ⏳ 缓存缺失{len(missing_from_cache)}只,走Tushare补充...")
                try:
                    # Tushare pro.daily 限制:每次最多1000只
                    for i in range(0, len(missing_from_cache), 1000):
                        batch = missing_from_cache[i:i+1000]
                        daily2 = pro.daily(ts_code=','.join(batch), start_date=trade_date, end_date=trade_date)
                        time.sleep(0.3)
                        if daily2 is not None and not daily2.empty:
                            for _, row in daily2.iterrows():
                                self.ref_prices[row['ts_code']] = {
                                    'close': row['close'],
                                    'pct_chg': row['pct_chg']
                                }
                            tushare_count += len(daily2)
                            # 回写 daily_cache 表
                            try:
                                batch_insert_daily_cache(daily2)
                            except Exception:
                                pass
                except Exception as e:
                    print(f"   ⚠ Tushare补充失败: {e}, 将从新浪行情获取昨收")
        else:
            print("   ⚠ Tushare不可用,将从新浪行情获取昨收")

        # 如果Tushare没获取到,尝试从新浪行情获取昨收
        if tushare_count == 0 or len(self.ref_prices) < len(all_codes):
            missing_codes = [code for code in all_codes if code not in self.ref_prices]
            print(f"   ⏳ 补充获取 {len(missing_codes)} 只股票昨收...")
            self._fetch_ref_prices_from_sina(missing_codes)

        # 检查缺失的股票
        missing = [code for code in all_codes if code not in self.ref_prices]
        if missing:
            print(f"⚠ Tushare缺失 {len(missing)} 只股票收盘价")
            for code in missing[:5]:  # 只显示前5个
                print(f"   {code}")
            if len(missing) > 5:
                print(f"   ... 还有 {len(missing)-5} 只")

        import pickle
        with open(cache_file, 'wb') as f:
            pickle.dump(self.ref_prices, f)
        print(f"✅ 已获取并缓存昨日收盘价: {len(self.ref_prices)} 只 (Tushare:{tushare_count}, 缺失:{len(missing)})")

    # ── 8. 指数K线缓存加载(用于均线趋势分) ──
    def load_index_klines(self):
        """
        从盘后缓存加载三大指数最近90根日线K线,用于MA5/MA10/MA20计算。
        优先级:pickle缓存 > SQLite cache > Tushare
        """
        import pickle
        from datetime import datetime as dt_dt

        # 直接从 Tushare 交易日历获取最近交易日,不再依赖缓存文件存在性
        trade_date = self._get_last_trade_date()
        self.index_klines = {}   # name -> DataFrame(cols: close, vol, high, low, pct_chg)

        print(f"⏳ 加载指数K线缓存 ({trade_date})...")

        for name, ts_code in INDEX_CODES.items():
            cache_file = os.path.join(CACHE_DIR, f"index_kline_{ts_code}_{trade_date}.pkl")
            df = None

            # 1) 优先加载 pickle 缓存
            if os.path.exists(cache_file):
                try:
                    with open(cache_file, 'rb') as f:
                        df = pickle.load(f)
                    if df is not None and len(df) >= 20:
                        self.index_klines[name] = df
                        continue
                except Exception:
                    pass

            # 2) 从 SQLite cache.db 读取(实际 key 格式: tsc_index_kline_ts_code_000001.SH_20260612)
            db_file = os.path.join(CACHE_DIR, "cache.db")
            if df is None and os.path.exists(db_file):
                try:
                    import sqlite3
                    from io import StringIO
                    conn = sqlite3.connect(db_file)
                    cur = conn.cursor()
                    # 修正:实际 key 格式含 ts_code_ 前缀
                    cur.execute("SELECT data FROM cache_data WHERE key LIKE ? LIMIT 1",
                                (f"%tsc_index_kline_ts_code_{ts_code}_%",))
                    row = cur.fetchone()
                    conn.close()
                    if row:
                        import pandas as pd
                        df = pd.read_csv(StringIO(row[0]))
                        if len(df) >= 20:
                            self.index_klines[name] = df
                            continue
                except Exception:
                    pass

            # 3) 回退到 Tushare (932000.CSI 走中证指数接口,其他走标准 index_daily)
            if df is None or len(df) < 20:
                if TS_AVAILABLE:
                    try:
                        import pandas as pd
                        start = (dt_dt.strptime(trade_date, '%Y%m%d') - timedelta(days=150)).strftime('%Y%m%d')
                        if ts_code.startswith('932000'):
                            # 中证指数走 index_dailybasic 或用 sh000300 替代
                            # 932000.CSI = 国证2000, 用 sz399303 替代(新浪映射中已有)
                            alt_code = '000300.SH'  # 暂时用沪深300替代,避免中证接口报错
                            df = pro.index_daily(ts_code=alt_code, start_date=start, end_date=trade_date)
                        else:
                            df = pro.index_daily(ts_code=ts_code, start_date=start, end_date=trade_date)
                        if df is not None and not df.empty:
                            df = df.sort_values('trade_date').reset_index(drop=True)
                            self.index_klines[name] = df
                    except Exception as e:
                        print(f"   ⚠ {name} Tushare回退失败: {e}")
                        pass

        for name, df in self.index_klines.items():
            print(f"   ✅ {name}({INDEX_CODES[name]}): {len(df)} 根K线,最新收盘={df['close'].iloc[-1]:.2f}")

    def _get_last_trade_date(self):
        """返回最近一个交易日 YYYYMMDD"""
        from datetime import datetime
        now = datetime.now()
        if now.hour < 15:
            q = (now - timedelta(days=1)).strftime('%Y%m%d')
        else:
            q = now.strftime('%Y%m%d')
        # 根据缓存文件存在性回退
        for offset in range(7):
            cand = (datetime.strptime(q, '%Y%m%d') - timedelta(days=offset)).strftime('%Y%m%d')
            for ts_code in INDEX_CODES.values():
                p = os.path.join(CACHE_DIR, f"index_kline_{ts_code}_{cand}.pkl")
                if os.path.exists(p):
                    return cand
        # 默认用最新 query_date
        return q

    def _fetch_ref_prices_from_sina(self, codes):
        """从新浪财经获取股票昨收数据作为Tushare的备选"""
        if not codes:
            return
        # 分批获取，每批最多180只
        for offset in range(0, len(codes), 180):
            batch = codes[offset:offset+180]
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
                }, timeout=8)
                resp.encoding = 'gbk'
                lines = resp.text.strip().split('\n')
                for line in lines:
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
                        
                        if len(fields) >= 3:
                            prev_close = float(fields[2])
                            self.ref_prices[ts_c] = {
                                'close': prev_close,
                                'pct_chg': 0
                            }
                    except:
                        continue
                time.sleep(0.1)
            except Exception as e:
                print(f"   ⚠ 新浪行情获取失败: {e}")
                continue

    # ── 成分股K线加载(用于主题趋势/情绪分计算) ──
    def load_component_klines(self, days=65):
        """加载所有成分股的历史K线数据用于趋势/情绪分计算"""
        if not TS_AVAILABLE or not THEME_SCORE_AVAILABLE:
            print("⚠ Tushare或主题评分模块不可用,跳过K线加载")
            return

        import pandas as pd
        all_codes = list(self.stock_themes.keys())
        if not all_codes:
            return

        trade_date = self._get_last_trade_date()
        start_date = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=days)).strftime('%Y%m%d')

        print(f"⏳ 加载成分股K线: {len(all_codes)} 只, {start_date}~{trade_date}...")

        # V2: 优先从 daily_cache 表逐只查询，未命中的再批量下载
        from stock_cache import get_daily_cache, get_daily_cache_range, batch_insert_daily_cache
        cached_codes = []
        missing_codes = []
        for code in all_codes:
            try:
                _, max_date = get_daily_cache_range(code)
                if max_date is not None and str(max_date) >= str(trade_date):
                    cached_codes.append(code)
                else:
                    missing_codes.append(code)
            except Exception:
                missing_codes.append(code)

        total_loaded = 0
        # 1) 命中缓存的部分
        for code in cached_codes:
            try:
                df = get_daily_cache(code, start_date, trade_date)
                if df is not None and not df.empty:
                    df['trade_date'] = df['trade_date'].astype(str)
                    grp_sorted = df.sort_values('trade_date').reset_index(drop=True)
                    grp_sorted = grp_sorted[['trade_date', 'close', 'high', 'low', 'vol', 'pct_chg']]
                    self.stock_klines[code] = grp_sorted
                    total_loaded += 1
            except Exception:
                pass

        # 2) 未命中的批量下载
        batch_size = 50
        for i in range(0, len(missing_codes), batch_size):
            batch = missing_codes[i:i+batch_size]
            try:
                df = pro.daily(ts_code=",".join(batch), start_date=start_date, end_date=trade_date)
                if df is not None and not df.empty:
                    try:
                        batch_insert_daily_cache(df)
                    except Exception:
                        pass
                    for code, grp in df.groupby('ts_code'):
                        grp_sorted = grp.sort_values('trade_date').reset_index(drop=True)
                        grp_sorted = grp_sorted[['trade_date', 'close', 'high', 'low', 'vol', 'pct_chg']]
                        self.stock_klines[code] = grp_sorted
                        total_loaded += 1
                time.sleep(0.2)  # 避免频率限制
            except Exception as e:
                print(f"⚠ K线获取失败 (batch {i//batch_size + 1}): {e}")
                continue

        print(f"✅ 成分股K线加载完成: {total_loaded}/{len(all_codes)} 只")

    # ── 加载换手率缓存(用于弱转强量价评分) ──
    def load_turnover_cache(self):
        """从cache_daily加载当日换手率"""
        import os
        import pandas as pd
        trade_date = self._get_last_trade_date()
        # 兼容路径
        cache_file = os.path.join(BASE_DIR, '..', 'cache_daily', f'turnover_rate_{trade_date}.csv')
        if not os.path.exists(cache_file):
            cache_file = rf'D:\mystock\cache_daily\turnover_rate_{trade_date}.csv'
        if not os.path.exists(cache_file):
            # 回退到最近一个交易日
            import glob
            files = glob.glob(r'D:\mystock\cache_daily\turnover_rate_*.csv')
            if files:
                cache_file = max(files, key=os.path.getmtime)
            else:
                print("⚠ 换手率缓存文件不存在,弱转强换手率评分将跳过")
                return
        try:
            df = pd.read_csv(cache_file)
            self.turnover_cache = dict(zip(df['ts_code'], df['turnover_rate']))
            print(f"[缓存] 换手率已加载: {len(self.turnover_cache)} 只 ({os.path.basename(cache_file)})")
        except Exception as e:
            print(f"⚠ 换手率缓存加载失败: {e}")

    def load_stock_factors_cache(self):
        """
        从SQLite(stock_data.db)加载最新交易日技术指标(MACD/KDJ/RSI/BOLL/CCI)
        以及从market_*.csv加载总市值
        """
        import os
        import glob
        import sqlite3
        import pandas as pd

        cache_daily = os.path.join(BASE_DIR, '..', 'cache_daily')
        if not os.path.isdir(cache_daily):
            cache_daily = r'D:\mystock\cache_daily'

        # ── 1. 从SQLite加载技术因子(替代旧的stk_factor_*.csv) ──
        db_path = os.path.join(cache_daily, 'stock_data.db')
        if not os.path.exists(db_path):
            print(f"⚠ SQLite数据库不存在: {db_path},技术因子未加载")
        else:
            try:
                conn = sqlite3.connect(db_path, timeout=10.0)
                # 技术指标是前一个交易日收盘后运算的,并非当日实时
                # 取最新交易日数据,但如果最新数据不完整(<1000只)则回退到次新
                rows = conn.execute(
                    'SELECT DISTINCT trade_date FROM stk_factor_pro ORDER BY trade_date DESC LIMIT 2'
                ).fetchall()
                if not rows:
                    conn.close()
                    print("⚠ SQLite中stk_factor_pro表为空,技术因子未加载")
                else:
                    # 检查最新日期数据量,数据量足够时直接取最新(即为T-1)
                    latest_count = conn.execute(
                        'SELECT COUNT(*) FROM stk_factor_pro WHERE trade_date = ?', (rows[0][0],)
                    ).fetchone()[0]
                    if latest_count > 1000:
                        latest_date = str(rows[0][0])
                    elif len(rows) >= 2:
                        latest_date = str(rows[1][0])
                        print(f"  最新日期{rows[0][0]}数据量仅{latest_count}只,回退到{latest_date}")
                    else:
                        latest_date = str(rows[0][0])
                    # 查询最近2个交易日数据(用于KDJ金叉/死叉的前后对比)
                    dates = [str(r[0]) for r in rows[:2]]
                    placeholders = ','.join('?' * len(dates))
                    df_all = pd.read_sql_query(
                        f'SELECT * FROM stk_factor_pro WHERE trade_date IN ({placeholders}) ORDER BY ts_code, trade_date',
                        conn, params=dates
                    )
                    conn.close()

                    if df_all.empty:
                        print(f"⚠ SQLite中{latest_date}无技术因子数据")
                    else:
                        # 重命名字段 (_bfq后缀 -> 简洁名,与_tail_technical_score对齐)
                        factor_rename = {
                            'macd_dif_bfq': 'macd_dif',
                            'macd_dea_bfq': 'macd_dea',
                            'macd_bfq': 'macd',
                            'kdj_bfq': 'kdj_j',
                            'kdj_k_bfq': 'kdj_k',
                            'kdj_d_bfq': 'kdj_d',
                            'rsi_bfq_6': 'rsi_6',
                            'rsi_bfq_12': 'rsi_12',
                            'rsi_bfq_24': 'rsi_24',
                            'boll_mid_bfq': 'boll_mid',
                            'boll_upper_bfq': 'boll_upper',
                            'boll_lower_bfq': 'boll_lower',
                            'cci_bfq': 'cci',
                        }
                        valid_rename = {k: v for k, v in factor_rename.items() if k in df_all.columns}
                        df_all = df_all.rename(columns=valid_rename)

                        # 按ts_code分组存储
                        for code, group_df in df_all.groupby('ts_code'):
                            self.stock_factors[code] = group_df.sort_values('trade_date').reset_index(drop=True)

                        print(f"[缓存] 技术因子已加载: {len(self.stock_factors)} 只 (SQLite {latest_date})")
            except Exception as e:
                print(f"⚠ SQLite技术因子加载失败: {e}")

        # ── 2. 加载总市值 ──
        trade_date = self._get_last_trade_date()
        mv_file = os.path.join(cache_daily, f'market_{trade_date}.csv')
        if not os.path.exists(mv_file):
            files = glob.glob(os.path.join(cache_daily, 'market_*.csv'))
            if files:
                mv_file = max(files, key=os.path.getmtime)
        if os.path.exists(mv_file):
            try:
                df_mv = pd.read_csv(mv_file)
                # total_mv 单位: 万元
                self.stock_mv = dict(zip(df_mv['ts_code'], df_mv['total_mv'].astype(float)))
                print(f"[缓存] 总市值已加载: {len(self.stock_mv)} 只 ({os.path.basename(mv_file)})")
            except Exception as e:
                print(f"⚠ 总市值加载失败: {e}")

    # ── 计算主题趋势/情绪/综合分(每15分钟) ──
    # ── 主题生命周期判定(启动/主升/分歧/退潮) ──
    def classify_theme_lifecycle(self, theme_name):
        """
        判定主题生命周期阶段,基于实时行情+历史强度趋势
        
        返回: (stage, score, detail)
        stage: '启动期'/'主升期'/'分歧期'/'退潮期'
        score: 0-100, 越高越适合次日买入
        """
        stocks = self.theme_stocks.get(theme_name, [])
        if not stocks:
            return '未知', 0, {}

        zt_count = 0
        strong_count = 0       # 涨幅>=5%
        up_count = 0
        down_count = 0
        total_amount = 0
        leader_amount = 0
        leader_pct = None
        leader_name = ''
        pcts = []
        valid = 0
        zt_layers = []         # 涨停股的层级列表

        for ts_code, name, layer in stocks:
            q = self.quotes.get(ts_code)
            if not q:
                continue
            pct = q.get('pct_chg', 0)
            amount = q.get('amount', 0)
            pcts.append(pct)
            total_amount += amount
            valid += 1

            if pct > 0: up_count += 1
            elif pct < 0: down_count += 1

            # 涨停判定(差异化: 主板10%, 双创20%)
            zt_threshold = 19.5 if ts_code.startswith(('300', '688')) else 9.5
            if pct >= zt_threshold:
                zt_count += 1
                zt_layers.append(layer)
                # 记录首次涨停时间
                if ts_code not in self.stock_zt_first_time:
                    self.stock_zt_first_time[ts_code] = datetime.now()

            if pct >= 5:
                strong_count += 1

            if layer == 'leader':
                leader_amount += amount
                if leader_pct is None or pct > leader_pct:
                    leader_pct = pct
                    leader_name = name

        if valid == 0:
            return '未知', 0, {}

        avg_pct = sum(pcts) / valid
        up_ratio = up_count / valid
        leader_concentration = leader_amount / total_amount if total_amount > 0 else 0
        # 扩散度: 涨停股中非龙头的比例
        non_leader_zt = sum(1 for l in zt_layers if l != 'leader')
        diffusion = non_leader_zt / zt_count if zt_count > 0 else 0

        # 历史趋势变化(用 theme_score_history)
        history = list(self.theme_score_history.get(theme_name, []))
        score_accel = 0.0
        if len(history) >= 6:
            score_accel = sum(history[-3:]) / 3 - sum(history[-6:-3]) / 3
        elif len(history) >= 3:
            score_accel = history[-1] - history[0]

        # 量能变化
        vol_history = list(self.theme_volume_history.get(theme_name, []))
        vol_change = 0.0
        if len(vol_history) >= 4:
            recent_vol = sum(vol_history[-2:]) / 2
            prev_vol = sum(vol_history[-4:-2]) / 2
            vol_change = (recent_vol - prev_vol) / prev_vol if prev_vol > 0 else 0

        detail = {
            'avg_pct': round(avg_pct, 2), 'zt_count': zt_count,
            'strong_count': strong_count, 'up_ratio': round(up_ratio, 2),
            'leader_conc': round(leader_concentration, 2),
            'diffusion': round(diffusion, 2),
            'score_accel': round(score_accel, 2),
            'vol_change': round(vol_change, 2),
            'leader_pct': round(leader_pct, 2) if leader_pct is not None else None,
            'leader_name': leader_name,
        }

        # ========== 生命周期判定 ==========

        # 退潮期: 整体下跌
        if avg_pct < -1.5 or (zt_count == 0 and down_count > up_count and avg_pct < -0.5):
            return '退潮期', 15, detail

        # 分歧期: 涨幅放缓+加速度转负, 或量价背离
        if score_accel < -0.3 and avg_pct < 1.5:
            return '分歧期', 35, detail
        # 量价背离: 涨幅为正但量能萎缩
        if avg_pct > 1.0 and vol_change < -0.15:
            return '分歧期', 30, detail

        # 主升期: 涨停>=3 + 涨幅>2 + 加速度>=0
        if zt_count >= 3 and avg_pct >= 2.0 and score_accel >= 0:
            # 扩散度高 = 主升中后段(减分)
            if diffusion >= 0.4:
                return '主升期', 68, detail
            return '主升期', 80, detail

        # 启动期: 涨停1-2只 + 量能放大/加速度为正
        if zt_count >= 1 and avg_pct >= 0.5:
            if vol_change > 0.1 or score_accel > 0.2:
                return '启动期', 88, detail
            return '启动期', 72, detail

        # 温和上涨但无涨停: 潜在启动
        if avg_pct > 0.5 and up_ratio > 0.5:
            return '启动期', 62, detail

        # 震荡
        if abs(avg_pct) <= 0.5:
            return '分歧期', 45, detail

        # 默认: 上涨但特征不明显
        return '启动期', 55, detail

    # ── T+1预测因子: 尾盘资金流向 + 龙头涨停时间 ──
    def calc_t1_prediction_factors(self, theme_name):
        """
        计算T+1预测因子:
        1. 尾盘资金流向(14:30基准 vs 当前成交额增量)
        2. 龙头涨停时间(早盘涨停=次日高溢价)
        
        返回: (score, detail)  score: 0-100
        """
        now = datetime.now()
        stocks = self.theme_stocks.get(theme_name, [])

        # 1. 尾盘资金流向
        current_amount = 0
        for ts_code, name, layer in stocks:
            q = self.quotes.get(ts_code)
            if q:
                current_amount += q.get('amount', 0)

        # 14:30记录基准成交额(仅记录一次)
        if now.hour == 14 and now.minute >= 30 and theme_name not in self.theme_amount_at_1430:
            self.theme_amount_at_1430[theme_name] = current_amount

        late_flow_score = 50  # 默认中性
        flow_ratio = None
        if theme_name in self.theme_amount_at_1430 and self.theme_amount_at_1430[theme_name] > 0:
            base = self.theme_amount_at_1430[theme_name]
            increment = current_amount - base
            flow_ratio = increment / base if base > 0 else 0
            # 尾盘增量越大 = 资金流入越强
            if flow_ratio > 0.30:
                late_flow_score = 85
            elif flow_ratio > 0.15:
                late_flow_score = 70
            elif flow_ratio > 0.05:
                late_flow_score = 55
            else:
                late_flow_score = 35

        # 2. 龙头涨停时间
        zt_time_score = 50  # 默认中性(龙头未涨停)
        leader_zt_time = None
        for ts_code, name, layer in stocks:
            if layer != 'leader':
                continue
            q = self.quotes.get(ts_code)
            if not q:
                continue
            pct = q.get('pct_chg', 0)
            zt_threshold = 19.5 if ts_code.startswith(('300', '688')) else 9.5
            if pct >= zt_threshold and ts_code in self.stock_zt_first_time:
                leader_zt_time = self.stock_zt_first_time[ts_code]
                break

        if leader_zt_time:
            # 早盘涨停 = 次日高溢价
            if leader_zt_time.hour < 10:
                zt_time_score = 90
            elif leader_zt_time.hour == 10:
                zt_time_score = 75
            elif leader_zt_time.hour == 13 and leader_zt_time.minute < 30:
                zt_time_score = 60
            else:
                zt_time_score = 40  # 尾盘涨停 = 低溢价

        score = late_flow_score * 0.5 + zt_time_score * 0.5
        detail = {
            'late_flow_score': late_flow_score,
            'zt_time_score': zt_time_score,
            'flow_ratio': round(flow_ratio, 3) if flow_ratio is not None else None,
            'leader_zt_time': leader_zt_time.strftime('%H:%M') if leader_zt_time else None,
        }
        return round(score, 1), detail

    # ── 次日套利Alpha得分 ──
    def calc_next_day_alpha(self, theme_name, trend_score, sentiment_score):
        """
        计算次日套利Alpha得分
        
        = 生命周期分(30%) + T+1预测分(25%) + 未充分定价分(20%)
          + 联动强度分(15%) + 大盘环境分(10%) - 见顶风险扣分
        
        返回: dict(alpha, stage, signal, ...)
        """
        # 1. 生命周期
        stage, lifecycle_score, lc_detail = self.classify_theme_lifecycle(theme_name)
        self.theme_lifecycle_cache[theme_name] = (stage, lifecycle_score, lc_detail)

        # 2. T+1预测因子
        t1_score, t1_detail = self.calc_t1_prediction_factors(theme_name)
        self.theme_forward_cache[theme_name] = t1_score

        # 3. 未充分定价(逆向因子)
        # 热度低 + 龙头未超买 = 未充分定价
        pricing_score = 50
        leader_pct = lc_detail.get('leader_pct')
        zt_count = lc_detail.get('zt_count', 0)
        avg_pct = lc_detail.get('avg_pct', 0)

        # 龙头未超买(涨幅<3%)加分,超买(>8%)减分
        if leader_pct is not None:
            if leader_pct < 3:
                pricing_score += 15
            elif leader_pct > 8:
                pricing_score -= 20

        # 无涨停=未被市场关注(未充分定价)加分
        if zt_count == 0 and avg_pct > 0:
            pricing_score += 10
        elif zt_count >= 5:
            pricing_score -= 15  # 过多涨停=已充分定价

        pricing_score = max(0, min(100, pricing_score))

        # 4. 联动强度
        up_ratio = lc_detail.get('up_ratio', 0.5)
        strong_count = lc_detail.get('strong_count', 0)
        linkage_score = 0
        if avg_pct > 2: linkage_score += 40
        elif avg_pct > 1: linkage_score += 25
        elif avg_pct > 0: linkage_score += 15
        if up_ratio > 0.7: linkage_score += 30
        elif up_ratio > 0.5: linkage_score += 20
        elif up_ratio > 0.4: linkage_score += 10
        if zt_count >= 3: linkage_score += 30
        elif zt_count >= 1: linkage_score += 15
        linkage_score = min(100, linkage_score)

        # 5. 大盘环境
        report = getattr(self, '_last_report', None)
        market_score = 50
        if report:
            ts = report.get('trend_score', 50)
            if ts >= 75: market_score = 85
            elif ts >= 60: market_score = 65
            elif ts >= 45: market_score = 40
            else: market_score = 20

        # 见顶风险扣分
        risk_penalty = 0
        if stage == '退潮期':
            risk_penalty += 30
        elif stage == '分歧期':
            risk_penalty += 15

        # 量价背离扣分
        vol_change = lc_detail.get('vol_change', 0)
        if avg_pct > 1.0 and vol_change < -0.15:
            risk_penalty += 10

        # 计算Alpha
        alpha = (
            lifecycle_score * 0.30 +
            t1_score * 0.25 +
            pricing_score * 0.20 +
            linkage_score * 0.15 +
            market_score * 0.10
        ) - risk_penalty
        alpha = max(0, min(100, alpha))

        # 买入信号
        if alpha >= 75 and stage == '启动期':
            signal = '买入'
        elif alpha >= 65 and stage in ('启动期', '主升期'):
            signal = '关注'
        elif stage == '退潮期' or alpha < 40:
            signal = '回避'
        else:
            signal = '观望'

        return {
            'alpha': round(alpha, 1),
            'stage': stage,
            'lifecycle_score': lifecycle_score,
            't1_score': t1_score,
            'pricing_score': pricing_score,
            'linkage_score': linkage_score,
            'market_score': market_score,
            'risk_penalty': risk_penalty,
            'signal': signal,
            'leader_pct': leader_pct,
            'leader_name': lc_detail.get('leader_name', ''),
            'zt_count': zt_count,
            'detail': {**lc_detail, **t1_detail}
        }

    def compute_theme_scores_realtime(self):
        """使用实时行情计算各主题的综合评分并输出TOP10"""
        if not THEME_SCORE_AVAILABLE:
            return

        if not self.stock_klines:
            print("⚠ 无成分股K线数据,跳过主题评分")
            return

        if not self.quotes:
            return

        # 获取沪深300指数10日收益率(市场基准)
        market_ret_10 = 0.0
        hs300_kline = self.index_klines.get("沪深300")
        if hs300_kline is not None and len(hs300_kline) >= 11:
            closes = hs300_kline["close"].astype(float).values
            market_ret_10 = (closes[-1] / closes[-11] - 1) * 100

        results = []
        for theme_name, stock_list in self.theme_stocks.items():
            stock_feats = []
            for ts_code, name, layer in stock_list:
                kdf = self.stock_klines.get(ts_code)
                if kdf is None or len(kdf) < 20:
                    continue

                # 复制K线数据并用实时行情更新最后一行
                df_work = kdf.copy()
                quote = self.quotes.get(ts_code)
                if quote and quote.get('pct_chg') is not None:
                    # 更新最后一行:使用实时pct_chg和价格
                    last_idx = len(df_work) - 1
                    df_work.loc[last_idx, 'pct_chg'] = quote['pct_chg']
                    # close/high/low 用当前价格更新
                    if quote.get('price'):
                        df_work.loc[last_idx, 'close'] = quote['price']
                    if quote.get('vol'):
                        df_work.loc[last_idx, 'vol'] = quote['vol']

                feat = per_stock_features(df_work)
                if feat:
                    feat['ts_code'] = ts_code
                    feat['name'] = name
                    feat['layer'] = layer
                    # 添加换手率(新浪API无此字段,使用默认值3.0)
                    feat['turnover'] = 3.0
                    stock_feats.append(feat)

            if len(stock_feats) < 3:
                results.append({
                    'theme': theme_name,
                    'n_stocks': len(stock_feats),
                    'trend_score': 0.0,
                    'sentiment_score': 0.0,
                    'composite_score': 0.0
                })
                continue

            # 计算趋势分和情绪分
            t_score, _ = calc_trend_score(stock_feats, market_ret_10)
            s_score, _ = calc_sentiment_score(stock_feats, market_ret_10)
            c_score = 0.55 * t_score + 0.45 * s_score

            # 计算热榜评分和历史分位数（与 theme_trend_sentiment_score.py 保持一致）
            hot_score, hot_detail = calc_theme_hot_score(stock_feats)
            hot_percentile, _ = get_theme_hot_score_percentile(theme_name, hot_score, days=60)
            hot_phase, hot_warning = judge_hot_phase(
                hot_score=hot_score,
                percentile=hot_percentile,
                top10_count=hot_detail.get('top10_count', 0),
                top5_count=hot_detail.get('top5_count', 0),
                total_stocks=len(stock_feats)
            )

            # 计算次日套利Alpha(生命周期+T+1预测+未充分定价+联动+大盘)
            alpha_info = self.calc_next_day_alpha(theme_name, t_score, s_score)

            results.append({
                'theme': theme_name,
                'n_stocks': len(stock_feats),
                'trend_score': t_score,
                'sentiment_score': s_score,
                'composite_score': c_score,
                'hot_score': round(hot_score, 2),
                'hot_percentile': hot_percentile,
                'hot_phase': hot_phase,
                'hot_warning': hot_warning,
                # 次日套利Alpha相关
                'alpha': alpha_info['alpha'],
                'stage': alpha_info['stage'],
                'signal': alpha_info['signal'],
                'lifecycle_score': alpha_info['lifecycle_score'],
                't1_score': alpha_info['t1_score'],
                'pricing_score': alpha_info['pricing_score'],
                'linkage_score': alpha_info['linkage_score'],
                'risk_penalty': alpha_info['risk_penalty'],
                'leader_name': alpha_info['leader_name'],
                'leader_pct': alpha_info['leader_pct'],
                'zt_count': alpha_info['zt_count'],
                'alpha_detail': alpha_info['detail'],
            })

        # 按次日套利Alpha排序(优先) + 综合分(次优)
        results.sort(key=lambda x: (x.get('alpha', 0), x.get('composite_score', 0)), reverse=True)

        return results

    # ── 14. 汇总:市场情绪综合评分(三大指数 + 主题) ──
    def compute_market_sentiment_report(self):
        """
        每次采集行情后调用:
        1. 拉取三大指数实时行情
        2. 对每个指数计算趋势分/情绪分
        3. 计算市场趋势总评分与仓位建议
        4. 返回字典用于推送和摘要
        """
        if not self.quotes:
            return None

        overview = self.compute_market_overview()

        # 获取指数实时行情
        self.fetch_index_quotes()

        # 逐指数计算
        index_results = []
        for name in INDEX_CODES.keys():
            ts = self.calc_trend_score(name, overview['up'], overview['total'])
            ss = self.calc_sentiment_score(name, overview['up'], overview['total'])
            iq = self.index_quotes_cache.get(name, {})
            pct = iq.get('pct_chg', 0) if iq else 0
            index_results.append({
                'name': name,
                'trend_score': ts,
                'sentiment_score': ss,
                'pct_chg': round(float(pct), 2) if pct else 0,
            })

        trend_score, index_trend, theme_trend, market_status, pos, pos_range = \
            self.calculate_total_market_score(index_results)

        report = {
            'overview': overview,
            'index_results': index_results,
            'trend_score': trend_score,
            'index_trend': index_trend,
            'theme_trend': theme_trend,
            'market_status': market_status,
            'position': pos,
            'position_range': pos_range,
            # 赚钱效应惩罚信息(若有): {reason, original_pos, zt_count, dt_count, up_ratio, down_ratio}
            'penalty_info': getattr(self, '_last_penalty_info', None),
        }
        self._last_report = report
        return report

    # ── 15. 基于趋势总评分的市场情绪预警 ──
    def detect_market_sentiment_v2(self, report):
        """
        替代原简单阈值逻辑:用趋势总评分 + 指数趋势 + 仓位 来生成预警
        丰富预警内容:三大指数涨跌、涨停跌停、趋势分情绪分
        """
        now_ts = time.time()
        now_dt = datetime.now()
        alerts = []
        if report is None:
            return alerts

        # 冷却检查（统一用 last_market_alert，避免重复推送）
        if now_ts - self.last_market_alert < 1800:  # 至少30分钟冷却
            return alerts

        overview = report['overview']
        ts = report['trend_score']
        status = report['market_status']
        pos = report['position']
        index_results = report.get('index_results', [])

        # 获取全市场统计数据(优先使用)
        full_stats = self.get_full_market_stats()
        if full_stats:
            up_ratio = full_stats.get('up_ratio', overview['up_ratio'])
            down_ratio = full_stats.get('down_ratio', overview['down_ratio'])
            zt_count = full_stats.get('zt_count', overview['zt'])
            dt_count = full_stats.get('dt_count', overview['dt'])
            total_count = full_stats.get('total', overview['total'])
            up_count = full_stats.get('up_count', overview['up'])
            down_count = full_stats.get('down_count', overview['down'])
        else:
            # 如果没有全市场统计数据,使用主题股票数据(虽然不准确,但至少有数据)
            up_ratio = overview['up_ratio']
            down_ratio = overview['down_ratio']
            zt_count = overview['zt']
            dt_count = overview['dt']
            total_count = overview['total']
            up_count = overview['up']
            down_count = overview['down']

            # 触发后台获取全市场统计(仅在整点附近)
            if now_dt.minute == 0 or (hasattr(self, '_last_full_stats_request') and now_dt.minute != self._last_full_stats_request):
                self._last_full_stats_request = now_dt.minute
                self.fetch_full_market_stats_sina()

        # 获取昨日数据
        yesterday_data = self.get_yesterday_market_data()

        # 构建三大指数涨跌摘要(完整名称)
        index_summary = ""
        if index_results:
            parts = []
            for r in index_results:
                pct = r.get('pct_chg', 0) or 0
                emoji = "🔴" if pct < 0 else "🟢" if pct > 0 else "⚪"
                parts.append(f"{r['name']}{emoji}{pct:+.2f}%")
            index_summary = " ".join(parts)

        # 构建趋势分/情绪分摘要(完整名称)
        score_summary = ""
        if index_results:
            parts = []
            for r in index_results:
                parts.append(f"{r['name']}趋势{r['trend_score']:.0f}/情绪{r['sentiment_score']:.0f}")
            score_summary = " ".join(parts)

        # 涨跌停摘要(全市场)
        max_lb = ''
        if full_stats:
            lb = full_stats.get('max_limit_height', '')
            if lb and isinstance(lb, (int, float)) and lb > 0:
                max_lb = f" 最高板{lb}板"
        zt_dt = f"涨停{zt_count} 跌停{dt_count}{max_lb}"

        # 昨日对比摘要
        yesterday_summary = ""
        if yesterday_data:
            y_ts = yesterday_data.get('trend_score', '?')
            y_zt = yesterday_data.get('zt_count', '?')
            y_dt = yesterday_data.get('dt_count', '?')
            y_br = yesterday_data.get('broken_rate', '?')
            y_lb = yesterday_data.get('max_limit_height', '')
            yesterday_summary = f"\n📊 昨日: 评分{y_ts} 涨停{y_zt} 跌停{y_dt} 炸板率{y_br}%"
            if y_lb and isinstance(y_lb, (int, float)) and y_lb > 0:
                yesterday_summary += f" 最高板{y_lb}板"

        # 赚钱效应惩罚信息(若有)
        penalty_info = report.get('penalty_info')
        penalty_line = ""
        if penalty_info:
            penalty_line = f"\n⚠️ 空仓警示: {penalty_info['reason']} (原建议{penalty_info['original_pos']}%→{pos}%)"

        # 1) 过热/强势信号
        if ts >= 85 and up_ratio > 70:
            msg = f"🔥🔥【{status}】趋势总评分{ts:.0f} 建议仓位{pos}%\n"
            msg += f"📈 {index_summary}\n"
            msg += f"📊 {score_summary}\n"
            msg += f"上涨{up_count}/{total_count}({up_ratio}%) {zt_dt}"
            msg += yesterday_summary
            alerts.append({'type': 'market_overheat', 'msg': msg})
        # 2) 强势信号
        elif ts >= 75:
            msg = f"🚀【{status}】趋势总评分{ts:.0f} 建议仓位{pos}%\n"
            msg += f"📈 {index_summary}\n"
            msg += f"📊 {score_summary}\n"
            msg += f"上涨{up_count}/{total_count}({up_ratio}%) {zt_dt}"
            msg += yesterday_summary
            alerts.append({'type': 'market_strong', 'msg': msg})
        # 3) 弱势/退潮(已含冷却)
        elif ts <= 35:
            msg = f"❄️❄️【{status}】趋势总评分{ts:.0f} 建议仓位{pos}%({pos_range})\n"
            msg += f"📉 {index_summary}\n"
            msg += f"📊 {score_summary}\n"
            msg += f"下跌{down_count}/{total_count}({down_ratio}%) {zt_dt}"
            msg += yesterday_summary
            msg += penalty_line
            alerts.append({'type': 'market_fear', 'msg': msg})
        # 3.5) 触发赚钱效应惩罚(空仓警示,优先级高于普通情绪)
        elif penalty_info and pos <= 10:
            msg = f"🛑🛑【{status}】趋势总评分{ts:.0f} 建议仓位{pos}%({pos_range})\n"
            msg += f"📉 {index_summary}\n"
            msg += f"📊 {score_summary}\n"
            msg += f"下跌{down_count}/{total_count}({down_ratio}%) {zt_dt}"
            msg += yesterday_summary
            msg += penalty_line
            alerts.append({'type': 'market_fear', 'msg': msg})
        # 4) 普通情绪(市场状态中间档,冷却10分钟)
        else:
            msg = f"📊【{status}】趋势总评分{ts:.0f} 建议仓位{pos}%\n"
            msg += f"📍 {index_summary}\n"
            msg += f"📈 上涨{up_ratio}% 下跌{down_ratio}% {zt_dt}"
            msg += yesterday_summary
            msg += penalty_line
            alerts.append({'type': 'market_neutral', 'msg': msg})

        if alerts:
            self.last_market_alert = now_ts                   
            self.last_score_alert = now_ts
        return alerts

    # ════════════════════════════════════════════
    # 大盘动作信号监测（解析V9.9报告，盘中跟进加仓/减仓条件）
    # ════════════════════════════════════════════

    def _parse_market_action_report(self):
        """
        解析最新 V9.9 市场分析报告，提取动作条件。
        返回: {action, add_conditions, reduce_conditions, position, market_regime, ...}
        条件格式: [(label, op, threshold, current), ...]
        """
        import re, os
        report_dir = os.path.join(BASE_DIR, 'cache_backbone_tushare')
        files = [f for f in os.listdir(report_dir)
                 if f.startswith('market_analysis_') and f.endswith('.txt')
                 and not f.endswith('_v8.txt') and not f.endswith('_v9.txt')]
        if not files:
            self._parsed_action = None
            return None
        files.sort(reverse=True)
        report_path = os.path.join(report_dir, files[0])

        try:
            with open(report_path, 'r', encoding='utf-8') as f:
                text = f.read()
        except Exception:
            self._parsed_action = None
            return None

        result = {
            'action': '维持',
            'add_conditions': [],
            'reduce_conditions': [],
            'position': 0,
            'market_regime': '',
            'report_date': files[0].replace('market_analysis_', '').replace('.txt', ''),
        }

        # 提取动作
        m = re.search(r'【动作】(\S+)', text)
        if m:
            result['action'] = m.group(1)

        # 提取仓位目标
        m = re.search(r'当前目标：(\d+)%', text)
        if m:
            result['position'] = int(m.group(1))

        # 提取市场状态
        m = re.search(r'市场：(\S+)', text)
        if m:
            result['market_regime'] = m.group(1)

        # 提取加仓条件
        in_add = False
        in_reduce = False
        for line in text.split('\n'):
            line = line.strip()
            if line == '加仓：':
                in_add = True
                in_reduce = False
                continue
            if line == '减仓：':
                in_add = False
                in_reduce = True
                continue
            if line.startswith('【') or line.startswith('─'):
                in_add = False
                in_reduce = False
                continue

            if in_add and line:
                cond = self._parse_action_condition(line)
                if cond:
                    result['add_conditions'].append(cond)
            if in_reduce and line:
                cond = self._parse_action_condition(line)
                if cond:
                    result['reduce_conditions'].append(cond)

        self._parsed_action = result
        return result

    def _parse_action_condition(self, line):
        """解析单行条件: '① 上涨家数 >55%（当前 53%）' → (label, op, threshold, current)"""
        import re
        # 去掉序号 ① ② ③
        line = re.sub(r'^[①②③④⑤⑥⑦⑧⑨⑩]\s*', '', line.strip())
        # 匹配: 标签 操作符 阈值%（当前 XX%） 或 标签 描述
        m = re.search(r'(.+?)\s*([><])\s*([\d.]+)%\s*[（(]当前\s*([\d.]+)%\s*[）)]', line)
        if m:
            return (m.group(1).strip(), m.group(2), float(m.group(3)), float(m.group(4)))
        # 无数字条件: "主线强度继续提升" / "主线龙头破位或退潮"
        m = re.search(r'(.+?)(继续提升|破位或退潮|退潮|破位)', line)
        if m:
            return (m.group(1).strip() + m.group(2), 'qualitative', None, None)
        return None

    def _check_market_action_signals(self):
        """
        盘中实时检查大盘动作信号。
        对比报告中的加仓/减仓条件 vs 实时数据。
        触发时返回预警消息列表。
        30分钟冷却，同日同条件不重复触发。
        """
        now_ts = time.time()
        now_dt = datetime.now()

        # 冷却: 30分钟
        if now_ts - self.last_market_action_alert < 1800:
            return []

        # 解析报告（每天只解析一次，缓存）
        if not hasattr(self, '_parsed_action') or self._parsed_action is None:
            parsed = self._parse_market_action_report()
            if parsed is None:
                return []
        else:
            parsed = self._parsed_action

        # 获取实时数据
        full = self.full_market_stats if hasattr(self, 'full_market_stats') and self.full_market_stats else {}
        rt_up_ratio = full.get('up_ratio', 0)
        rt_zt_count = full.get('zt_count', 0)
        rt_dt_count = full.get('dt_count', 0)

        if rt_up_ratio <= 0:
            # 无全市场数据，用主题池数据近似
            ms = self.compute_market_overview()
            rt_up_ratio = ms.get('up_ratio', 50)
            rt_zt_count = ms.get('zt', 0)
            rt_dt_count = ms.get('dt', 0)

        # 获取昨日炸板率（从market_analysis.db）
        yesterday_broken = self._get_yesterday_broken_rate()
        rt_broken = yesterday_broken  # 实时炸板率暂无精确数据，用昨日值

        # 主线强度: 实时主题评分
        mainline_score = self._get_mainline_strength()

        alerts = []

        # ── 检查加仓条件 ──
        add_met = []
        for label, op, threshold, current in parsed.get('add_conditions', []):
            if op == 'qualitative':
                continue  # "主线强度继续提升" 由下方单独判断
            val = None
            if '上涨家数' in label:
                val = rt_up_ratio
            elif '炸板率' in label:
                val = rt_broken
            if val is not None and threshold is not None:
                if op == '>' and val > threshold:
                    add_met.append(f"{label} {op} {threshold}% (实时{val:.0f}% ✅)")
                elif op == '<' and val < threshold:
                    add_met.append(f"{label} {op} {threshold}% (实时{val:.0f}% ✅)")
                elif op == '>' and val <= threshold:
                    add_met.append(f"{label} {op} {threshold}% (实时{val:.0f}% ❌)")
                elif op == '<' and val >= threshold:
                    add_met.append(f"{label} {op} {threshold}% (实时{val:.0f}% ❌)")

        # 主线强度判断: 量化评分≥70且较昨日提升
        if mainline_score >= 70:
            add_met.append("主线强度: 强(≥70) ✅")
        elif mainline_score >= 55:
            add_met.append(f"主线强度: 中({mainline_score:.0f}) ⚠")
        else:
            add_met.append(f"主线强度: 弱({mainline_score:.0f}) ❌")

        # ── 检查减仓条件 ──
        reduce_met = []
        for label, op, threshold, current in parsed.get('reduce_conditions', []):
            if op == 'qualitative':
                continue
            val = None
            if '上涨家数' in label:
                val = rt_up_ratio
            elif '炸板率' in label:
                val = rt_broken
            if val is not None and threshold is not None:
                if op == '<' and val < threshold:
                    reduce_met.append(f"{label} {op} {threshold}% (实时{val:.0f}% ⚠)")
                elif op == '>' and val > threshold:
                    reduce_met.append(f"{label} {op} {threshold}% (实时{val:.0f}% ⚠)")

        # 主线退潮判断
        if mainline_score < 35:
            reduce_met.append(f"主线强度: 极度弱({mainline_score:.0f}) ⚠")
        elif mainline_score < 45:
            reduce_met.append(f"主线强度: 弱({mainline_score:.0f}) ⚠")

        # ── 生成预警 ──
        n_add_met = sum(1 for m in add_met if '✅' in m)
        n_reduce_met = sum(1 for m in reduce_met if '⚠' in m)

        if n_add_met >= 2:
            # 加仓信号触发
            key = f"add_{now_dt.strftime('%Y%m%d')}"
            if key not in self._market_action_triggered:
                self._market_action_triggered[key] = now_ts
                msg = self._build_action_alert(parsed, '加仓', add_met, reduce_met,
                                               rt_up_ratio, rt_zt_count, rt_dt_count, mainline_score)
                alerts.append({'type': 'market_action_add', 'msg': msg})
        elif n_reduce_met >= 2:
            # 减仓信号触发
            key = f"reduce_{now_dt.strftime('%Y%m%d')}"
            if key not in self._market_action_triggered:
                self._market_action_triggered[key] = now_ts
                msg = self._build_action_alert(parsed, '减仓', add_met, reduce_met,
                                               rt_up_ratio, rt_zt_count, rt_dt_count, mainline_score)
                alerts.append({'type': 'market_action_reduce', 'msg': msg})

        if alerts:
            self.last_market_action_alert = now_ts

        return alerts

    def _build_action_alert(self, parsed, signal_type, add_met, reduce_met,
                            up_ratio, zt, dt, mainline):
        """构建大盘动作预警消息"""
        now = datetime.now()
        lines = [
            f"📊 大盘动作预警 [{now.strftime('%H:%M')}]",
            f"报告: {parsed.get('report_date', '?')} 原建议: {parsed.get('action', '维持')}",
            f"市场: {parsed.get('market_regime', '?')} 目标仓位: {parsed.get('position', '?')}%",
            f"─" * 30,
            f"实时: 上涨{up_ratio:.0f}% 涨停{zt} 跌停{dt} 主线{mainline:.0f}",
            f"",
        ]
        if signal_type == '加仓':
            lines.append(f"🟢 【动作】加仓信号触发！")
            lines.append(f"加仓条件检查:")
            for m in add_met:
                lines.append(f"  {m}")
            lines.append(f"减仓条件检查:")
            for m in reduce_met:
                lines.append(f"  {m}")
        else:
            lines.append(f"🔴 【动作】减仓信号触发！")
            lines.append(f"减仓条件检查:")
            for m in reduce_met:
                lines.append(f"  {m}")
            lines.append(f"加仓条件检查:")
            for m in add_met:
                lines.append(f"  {m}")

        lines.append(f"─" * 30)
        lines.append(f"⚠ 炸板率数据为前日值({parsed.get('report_date', '?')})，实时炸板率需人工确认")

        return '\n'.join(lines)

    def _get_yesterday_broken_rate(self):
        """从 market_analysis.db 获取昨日炸板率"""
        import sqlite3
        try:
            db_path = os.path.join(BASE_DIR, 'cache_backbone_tushare', 'market_analysis.db')
            if not os.path.exists(db_path):
                return 25  # 默认值
            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT broken_rate FROM limit_stats ORDER BY trade_date DESC LIMIT 1"
            ).fetchone()
            conn.close()
            if row and row[0] is not None:
                return float(row[0])
        except Exception:
            pass
        return 25

    def _get_mainline_strength(self):
        """实时主线强度: 取TOP3主题量化评分均值"""
        # 优先用 _last_theme_results (由 compute_theme_scores_realtime 设置)
        if hasattr(self, '_last_theme_results') and self._last_theme_results:
            top3 = sorted(self._last_theme_results,
                          key=lambda x: x.get('alpha', 0), reverse=True)[:3]
            if top3:
                return sum(r.get('alpha', 0) for r in top3) / len(top3)
        # 回退: theme_scores dict
        if hasattr(self, 'theme_scores') and self.theme_scores:
            try:
                top3 = sorted(self.theme_scores.items(), key=lambda x: x[1], reverse=True)[:3]
                if top3:
                    return sum(v for _, v in top3) / len(top3)
            except Exception:
                pass
        return 50

    def get_yesterday_market_data(self):
        """获取上一个交易日市场分析数据(用于盘前/盘中对比)
        优先通过 Tushare trade_cal 接口获取上一个交易日,避免周末/节假日指向非交易日
        """
        import sqlite3
        import datetime

        try:
            # 1. 优先通过 Tushare trade_cal 接口获取上一个交易日(最权威)
            yesterday_str = None
            if TS_AVAILABLE and pro is not None and os.getenv('TUSHARE_TOKEN'):
                try:
                    today = datetime.date.today()
                    end_date = today.strftime('%Y%m%d')
                    # 查询范围比 end_date 早一天,避免交易日历未来日期的影响
                    query_end = (today - datetime.timedelta(days=1)).strftime('%Y%m%d')
                    start_date = (today - datetime.timedelta(days=15)).strftime('%Y%m%d')
                    cal_df = pro.trade_cal(
                        exchange='SSE',
                        start_date=start_date,
                        end_date=query_end,
                        is_open='1',
                        fields='cal_date,is_open'
                    )
                    if cal_df is not None and not cal_df.empty:
                        # 找今天之前最近的一个交易日(query_end 已是 end_date-1)
                        past = cal_df.sort_values('cal_date', ascending=False)
                        if not past.empty:
                            yesterday_str = str(past.iloc[0]['cal_date'])
                            print(f"[get_yesterday_market_data] Tushare trade_cal → 上一交易日: {yesterday_str}")
                except Exception as e:
                    print(f"⚠ Tushare trade_cal 获取上一个交易日失败: {e}")
            else:
                print(f"[get_yesterday_market_data] Tushare 不可用 (TS_AVAILABLE={TS_AVAILABLE}, pro={pro is not None}, token={'已设置' if os.getenv('TUSHARE_TOKEN') else '未设置'}), 走本地数据库兜底")

            # 2. 兜底:从本地数据库 MAX(trade_date) 获取
            if not yesterday_str:
                db_path = os.path.join(BASE_DIR, 'cache_backbone_tushare', 'market_analysis.db')
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                cursor.execute("SELECT MAX(trade_date) FROM overall_analysis")
                last_row = cursor.fetchone()
                conn.close()
                if last_row and last_row[0]:
                    yesterday_str = last_row[0]
                else:
                    return None

            # 3. 打开数据库连接查询数据
            db_path = os.path.join(BASE_DIR, 'cache_backbone_tushare', 'market_analysis.db')
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            # 获取上一交易日整体分析
            cursor.execute("SELECT trend_score FROM overall_analysis WHERE trade_date=? ORDER BY id DESC LIMIT 1", (yesterday_str,))
            row = cursor.fetchone()
            if row:
                result = {'trend_score': round(row[0], 0) if row[0] is not None else '?'}
            else:
                result = {'trend_score': '?'}

            # 从 limit_stats 表获取涨跌停数据(新增)
            cursor.execute("""
                SELECT zt_count, dt_count, broken_rate, zhaban_count, max_limit_height,
                       up_count, down_count, total, up_ratio, down_ratio
                FROM limit_stats WHERE trade_date=?
            """, (yesterday_str,))
            limit_row = cursor.fetchone()
            if limit_row:
                # 注意:0 是合法值(完全无涨跌停),不能用 'or' 判断,需显式判 None
                result['zt_count'] = limit_row[0] if limit_row[0] is not None else '?'
                result['dt_count'] = limit_row[1] if limit_row[1] is not None else '?'
                result['broken_rate'] = limit_row[2] if limit_row[2] is not None else '?'
                result['zhaban_count'] = limit_row[3] if limit_row[3] is not None else '?'
                result['max_limit_height'] = limit_row[4] if limit_row[4] is not None else '?'
                result['up_count'] = limit_row[5] if limit_row[5] is not None else '?'
                result['down_count'] = limit_row[6] if limit_row[6] is not None else '?'
                result['total'] = limit_row[7] if limit_row[7] is not None else '?'
                result['up_ratio'] = limit_row[8] if limit_row[8] is not None else '?'
                result['down_ratio'] = limit_row[9] if limit_row[9] is not None else '?'
            else:
                # 回退:从缓存文件获取(兼容旧格式)
                yesterday_cache = os.path.join(BASE_DIR, 'cache_daily', f'full_market_stats_{yesterday_str}.json')
                if os.path.exists(yesterday_cache):
                    import json
                    with open(yesterday_cache, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        result['zt_count'] = data.get('zt_count', '?')
                        result['dt_count'] = data.get('dt_count', '?')
                        result['broken_rate'] = data.get('broken_rate', '?')
                        result['max_limit_height'] = data.get('max_limit_height', '?')
                else:
                    result['zt_count'] = '?'
                    result['dt_count'] = '?'
                    result['broken_rate'] = '?'
                    result['max_limit_height'] = '?'

            conn.close()
            return result
        except Exception as e:
            return None

    # ── 9. 三大指数实时行情 ──
    def fetch_index_quotes(self):
        """
        实时获取三大指数行情(新浪财经),返回 dict: name -> {pct_chg, price, vol, high, low, last_close}
        """
        result = {}
        url = "https://hq.sinajs.cn/list=" + ",".join(SINA_INDEX_CODES.values())
        try:
            resp = requests.get(url, headers={
                'Referer': 'https://finance.sina.com.cn',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }, timeout=6)
            resp.encoding = 'gbk'
            lines = resp.text.strip().split('\n')
            for line in lines:
                if '=' not in line:
                    continue
                var_name = line.split('=')[0].strip().replace('var ', '').replace('hq_str_', '')
                content = line.split('"')[1] if '"' in line else ''
                fields = content.split(',')
                if len(fields) < 10:
                    continue
                name = next((k for k, v in SINA_INDEX_CODES.items() if v == var_name), None)
                if not name:
                    continue
                # 字段:0name,1open,2last_close,3price,4high,5low,... 8vol, 9amount
                try:
                    price = float(fields[3])
                    last_close = float(fields[2])
                    high = float(fields[4])
                    low = float(fields[5])
                    vol = float(fields[8])
                    amount = float(fields[9])
                    pct_chg = (price - last_close) / last_close * 100 if last_close > 0 else 0
                    result[name] = {
                        'price': price,
                        'pct_chg': pct_chg,
                        'vol': int(vol),
                        'amount': amount,
                        'high': high,
                        'low': low,
                        'last_close': last_close,
                    }
                except (ValueError, IndexError):
                    continue
        except Exception as e:
            if not self.index_quotes_cache.get('_warned'):
                print(f"   ⚠ 指数实时行情获取失败: {e}")
                self.index_quotes_cache['_warned'] = True

        if result:
            self.index_quotes_cache.update(result)
        return result

    # ── 10. 市场概览:涨跌家数/涨跌停/炸板 ──
    def compute_market_overview(self):
        total = 0
        up = 0
        down = 0
        zt = 0
        dt = 0
        for ts_code, q in self.quotes.items():
            total += 1
            pct = q.get('pct_chg', 0)
            if pct > 0: up += 1
            elif pct < 0: down += 1
            if pct >= 9.5: zt += 1
            elif pct <= -9.5: dt += 1
        return {
            'total': total,
            'up': up,
            'down': down,
            'zt': zt,
            'dt': dt,
            'up_ratio': round(up / total * 100, 1) if total else 0,
            'down_ratio': round(down / total * 100, 1) if total else 0,
        }

    # ── 10.4 同花顺扶摇 MCP 补充数据获取 ──
    def fetch_fuyao_data(self, now: datetime):
        """
        从同花顺扶摇MCP获取补充数据，作为新浪API的增强
        调用频率:
          - 涨停池: 每60秒(服务端30秒缓存)
          - 热股榜/飙升榜: 每10分钟(600秒)
          - 龙虎榜: 15:00后仅一次
        数据存入 self.fuyao_data 供 print_summary 使用
        """
        if not self.fuyao.is_healthy:
            return

        t = time.time()

        # ── 涨停池 (每60秒) ──
        if self.fuyao_zt_pool_enabled:
            key = 'limit_up_pool'
            if t - self.fuyao_last_fetch.get(key, 0) >= 60:
                zt = self.fuyao.get_limit_up_pool(size=20, sort_field='continue_day_cnt')
                if zt and zt.get('code') == 0:
                    self.fuyao_data[key] = zt
                    self.fuyao_last_fetch[key] = t
                    items = zt.get('data', {}).get('item', [])
                    if items:
                        top_info = f"{items[0].get('name','')}({items[0].get('continue_day_cnt',0)}板)" if items else '无'
                        print(f"   📈 [扶摇] 涨停池 {len(items)}只 | 最高:{top_info}")
                else:
                    self.fuyao_data[key] = None

        # ── 热股榜 (每10分钟) ──
        key = 'hot_stock_list'
        if t - self.fuyao_last_fetch.get(key, 0) >= 600:
            hot = self.fuyao.get_hot_stock_list()
            if hot and hot.get('code') == 0:
                self.fuyao_data[key] = hot
                self.fuyao_last_fetch[key] = t
            else:
                self.fuyao_data[key] = None

        # ── 飙升榜 (每10分钟) ──
        key = 'skyrocket_list'
        if t - self.fuyao_last_fetch.get(key, 0) >= 600:
            sky = self.fuyao.get_skyrocket_list()
            if sky and sky.get('code') == 0:
                self.fuyao_data[key] = sky
                self.fuyao_last_fetch[key] = t
            else:
                self.fuyao_data[key] = None

        # ── 龙虎榜 (15:00后仅一次) ──
        if now.hour >= 15 and not self.fuyao_dragon_tiger_done:
            key = 'dragon_tiger_list'
            dt_data = self.fuyao.get_dragon_tiger_list()
            if dt_data and dt_data.get('code') == 0:
                self.fuyao_data[key] = dt_data
                self.fuyao_dragon_tiger_done = True
                self.fuyao_last_fetch[key] = t
                print(f"   🐉 [扶摇] 龙虎榜已获取")
            else:
                self.fuyao_data[key] = None

    # ── 10.5 新浪全市场涨跌停统计(后台任务) ──
    def fetch_full_market_stats_sina(self):
        """
        使用新浪市场总貌接口获取全市场涨跌停统计(约3秒)
        返回: {total, zt_count, dt_count, up_count, down_count, up_ratio, down_ratio}
        """
        import threading

        def _fetch():
            try:
                import requests
                import time
                import json
                import re

                headers = {
                    "Referer": "https://finance.sina.com.cn/",
                    "User-Agent": "Mozilla/5.0"
                }

                # 新浪市场总貌接口
                url = "http://vip.stock.finance.sina.com.cn/q/view/newMarketsDataAll.php"
                
                try:
                    r = requests.get(url, headers=headers, timeout=10)
                    text = r.text.strip()
                    
                    if text:
                        # 解析JSON数据（格式: jsonData(...)）
                        json_match = re.search(r'\((.*)\)', text)
                        if json_match:
                            market_data = json.loads(json_match.group(1))
                            
                            total = int(market_data.get('total', 0))
                            up_count = int(market_data.get('up', 0))
                            down_count = int(market_data.get('down', 0))
                            zt_count = int(market_data.get('zt', 0))
                            dt_count = int(market_data.get('dt', 0))
                            
                            if total > 0:
                                up_ratio = round(up_count / total * 100, 1)
                                down_ratio = round(down_count / total * 100, 1)
                                
                                self.full_market_stats = {
                                    'total': total,
                                    'zt_count': zt_count,
                                    'dt_count': dt_count,
                                    'up_count': up_count,
                                    'down_count': down_count,
                                    'up_ratio': up_ratio,
                                    'down_ratio': down_ratio,
                                    'updated': time.strftime('%Y-%m-%d %H:%M:%S')
                                }
                                print(f"📊 全市场统计更新: 涨停{zt_count} 跌停{dt_count} 上涨{up_ratio}% 下跌{down_ratio}%")
                                
                                # 保存缓存
                                import os
                                cache_file = os.path.join(CACHE_DIR, 'cache_daily', 'full_market_stats.json')
                                try:
                                    os.makedirs(os.path.dirname(cache_file), exist_ok=True)
                                    with open(cache_file, 'w', encoding='utf-8') as f:
                                        json.dump(self.full_market_stats, f, ensure_ascii=False)
                                except:
                                    pass
                            else:
                                print(f"⚠ 全市场统计获取失败: total=0")
                        else:
                            print(f"⚠ 全市场统计解析失败: 无法提取JSON")
                    else:
                        print(f"⚠ 全市场统计获取失败: 返回为空")
                except Exception as e:
                    print(f"⚠ 新浪市场总貌接口失败: {e}")
                    
                    # 备用方案: 使用东方财富涨跌停板接口
                    try:
                        print("   尝试备用方案: 东方财富涨跌停接口...")
                        zt_url = "http://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=1&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281&fltt=2&invt=2&fid=f3&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23+f:8&fields=f12,f14,f2,f3"
                        dt_url = "http://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=1&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281&fltt=2&invt=2&fid=f3&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23+f:4&fields=f12,f14,f2,f3"
                        
                        zt_count = 0
                        dt_count = 0
                        
                        r_zt = requests.get(zt_url, headers=headers, timeout=10)
                        data_zt = r_zt.json()
                        if data_zt.get('data') and data_zt['data'].get('total'):
                            zt_count = int(data_zt['data']['total'])
                        
                        r_dt = requests.get(dt_url, headers=headers, timeout=10)
                        data_dt = r_dt.json()
                        if data_dt.get('data') and data_dt['data'].get('total'):
                            dt_count = int(data_dt['data']['total'])
                        
                        if zt_count > 0 or dt_count > 0:
                            self.full_market_stats = {
                                'total': 0,
                                'zt_count': zt_count,
                                'dt_count': dt_count,
                                'up_count': 0,
                                'down_count': 0,
                                'up_ratio': 0,
                                'down_ratio': 0,
                                'updated': time.strftime('%Y-%m-%d %H:%M:%S')
                            }
                            print(f"📊 全市场统计更新(备用): 涨停{zt_count} 跌停{dt_count}")
                    except Exception as e2:
                        print(f"⚠ 备用方案也失败: {e2}")
            except Exception as e:
                print(f"⚠ 全市场统计获取失败: {e}")

        # 在后台线程运行
        t = threading.Thread(target=_fetch, daemon=True)
        t.start()
        return "后台获取中..."


    # ── 10.6 获取全市场统计(优先使用内存数据) ──
    def get_full_market_stats(self):
        """获取全市场统计
        优先返回内存中的最新数据,如果没有则返回None触发重新获取
        """
        # 如果有最新数据,直接返回
        if hasattr(self, 'full_market_stats') and self.full_market_stats:
            # 检查数据是否是今天的
            updated = self.full_market_stats.get('updated', '')
            if updated.startswith(time.strftime('%Y-%m-%d')):
                return self.full_market_stats
            else:
                # 数据是昨天的,清空并返回None
                self.full_market_stats = None

        return None

    # ── 11. 趋势评分算法(来自market_analysis.py calc_trend_score) ──
    def calc_trend_score(self, index_name, up_count=0, total_count=0):
        """
        对单个指数计算趋势分:
        MA_SCORE(40) + INDEX_SCORE(30) + BREADTH_SCORE(30)
        历史K线从 self.index_klines 读取,当日收盘价/振幅/成交量从 self.index_quotes_cache 补充
        """
        df = self.index_klines.get(index_name)
        if df is None or len(df) < 20:
            return 50.0

        latest_quote = self.index_quotes_cache.get(index_name, {})
        cur_price = latest_quote.get('price')
        cur_vol = latest_quote.get('vol')

        # 构造临时序列:将历史K线与当日实时行情融合
        # 关键修复:用实时价(cur_price)替换历史最后一天收盘,确保均线反映今日行情
        closes = list(df['close'].values) if 'close' in df.columns else list(df['close_y'].values)
        vols = list(df['vol'].values) if 'vol' in df.columns else [0]*len(closes)

        if cur_price and cur_price > 0:
            # 用实时价替换最后一根K线收盘,保证均线实时反映今日走势
            if len(closes) > 0:
                closes[-1] = float(cur_price)
            else:
                closes.append(float(cur_price))
            # 注意:不替换vol,Sina vol是累计量,不能与历史日成交量比较

        import numpy as np
        closes_arr = np.array(closes, dtype=float)
        ma5 = float(closes_arr[-5:].mean()) if len(closes_arr) >= 5 else closes_arr[-1]
        ma10 = float(closes_arr[-10:].mean()) if len(closes_arr) >= 10 else closes_arr[-1]
        ma20 = float(closes_arr[-20:].mean()) if len(closes_arr) >= 20 else closes_arr[-1]
        cur_close = closes_arr[-1]

        # MA_SCORE(40分)
        ma_score = 0
        if ma5 > ma10 > ma20: ma_score = 40
        elif ma5 > ma10: ma_score = 30
        elif ma5 > ma20: ma_score = 20
        elif ma5 < ma10 < ma20: ma_score = 10
        else: ma_score = 15

        # INDEX_SCORE(30分)
        # 修正:加入当日涨跌幅判断,指数下跌时适当扣分
        index_score = 0
        pct_chg = latest_quote.get('pct_chg', 0) or 0
        if cur_close > ma20: 
            index_score = 30
            if pct_chg < -1.5: index_score -= 10
            elif pct_chg < -0.5: index_score -= 5
        elif cur_close > ma10: 
            index_score = 20
            if pct_chg < -1.5: index_score -= 5
        elif cur_close > ma5: 
            index_score = 10
        else: index_score = 0

        # BREADTH_SCORE(30分)
        breadth_score = 0
        if total_count > 0:
            r = up_count / total_count * 100
            if r > 70: breadth_score = 30
            elif r >= 60: breadth_score = 25
            elif r >= 50: breadth_score = 20
            elif r >= 40: breadth_score = 10
            elif r >= 30: breadth_score = 5
            else: breadth_score = 0
        else:
            # 无成分股数据时:根据指数涨幅给出基础分
            pct = latest_quote.get('pct_chg', 0) or 0
            if pct > 2: breadth_score = 30
            elif pct > 1: breadth_score = 20
            elif pct > 0: breadth_score = 15
            elif pct > -1: breadth_score = 10
            else: breadth_score = 5

        trend_score = max(0, min(100, ma_score + index_score + breadth_score))
        return round(float(trend_score), 1)

    # ── 12. 情绪评分算法(来自market_analysis.py calc_sentiment_score) ──
    def calc_sentiment_score(self, index_name, up_count=0, total_count=0):
        df = self.index_klines.get(index_name)
        if df is None or len(df) < 20:
            return 50.0

        latest_quote = self.index_quotes_cache.get(index_name, {})
        cur_price = latest_quote.get('price')
        cur_vol = latest_quote.get('vol')
        cur_high = latest_quote.get('high')
        cur_low = latest_quote.get('low')
        cur_last_close = latest_quote.get('last_close')

        # 历史 pct_chg
        if 'pct_chg' in df.columns:
            pct_list = list(df['pct_chg'].values)
        else:
            pct_list = []
            closes = list(df['close'].values)
            for i in range(1, len(closes)):
                if closes[i-1] and closes[i]:
                    pct_list.append((closes[i]-closes[i-1])/closes[i-1]*100)

        # 当日涨跌幅
        pct_chg = 0.0
        if cur_price and cur_last_close:
            pct_chg = (float(cur_price) - float(cur_last_close)) / float(cur_last_close) * 100

        # 1) 涨跌方向与强度(30分)
        if pct_chg >= 2: direction_score = 30
        elif pct_chg >= 1: direction_score = 20
        elif pct_chg >= 0: direction_score = 10
        elif pct_chg >= -1: direction_score = 5
        elif pct_chg >= -2: direction_score = 0
        else: direction_score = -20

        # 2) 成交量变化(25分)
        import numpy as np
        vols = list(df['vol'].values) if 'vol' in df.columns else [0]*len(df)
        if cur_vol:
            vols.append(float(cur_vol))
        vol_arr = np.array(vols, dtype=float)
        vol5 = float(vol_arr[-5:].mean()) if len(vol_arr) >= 5 else 0
        vol20 = float(vol_arr[-20:].mean()) if len(vol_arr) >= 20 else vol5
        vol_ratio = vol5 / vol20 if vol20 > 0 else 1
        vol_score = 12.5 + (vol_ratio - 1) * 20
        # 修正:放量下跌恐慌,放量大涨强势
        if pct_chg < -1 and vol_ratio > 1.2:
            vol_score -= 10
        elif pct_chg > 1 and vol_ratio > 1.2:
            vol_score += 5
        vol_score = min(25, max(0, vol_score))

        # 3) 振幅(20分)
        if cur_high and cur_low and cur_low > 0:
            amplitude = (float(cur_high) - float(cur_low)) / float(cur_low) * 100
        else:
            amplitude = 0
        if pct_chg < 0:
            amp_score = max(0, 10 - amplitude)
        else:
            amp_score = min(20, 10 + amplitude * 0.5)

        # 4) 连涨连跌(25分)
        up_streak = 0
        down_streak = 0
        if pct_chg is not None and pct_chg != 0:
            recent = list(pct_list) + [pct_chg]
        else:
            recent = list(pct_list)
        # 从尾部往前数
        for i in range(1, 6):
            if len(recent) >= i:
                v = recent[-i]
                if v > 0: up_streak += 1
                else: break
            else:
                break
        for i in range(1, 6):
            if len(recent) >= i:
                v = recent[-i]
                if v < 0: down_streak += 1
                else: break
            else:
                break
        streak_score = min(25, max(0, 12.5 + up_streak * 3 - down_streak * 4))

        # 5) 波动率惩罚
        if pct_list:
            vol_std = float(np.array(pct_list[-20:], dtype=float).std()) if len(pct_list) >= 10 else 0
        else:
            vol_std = 0
        vol_penalty = max(0, (vol_std - 2.5) * 3) if vol_std > 2.5 else 0

        sentiment_score = direction_score + vol_score + amp_score + streak_score - vol_penalty
        sentiment_score = max(0, min(100, sentiment_score))
        return round(float(sentiment_score), 1)

    # ── 13. 市场趋势总评分(与market_analysis.py calculate_market_trend_score对齐) ──
    def calculate_total_market_score(self, results_per_index):
        """
        results_per_index: [{name, trend_score, sentiment_score, pct_chg}, ...]
        返回: (trend_score, index_trend, theme_trend, market_status, position)
        
        与market_analysis.py保持一致的算法:
        IndexTrend = sh_score * 0.35 + hs300_score * 0.25 + zz2000_score * 0.40
        （原权重0.5/0.3/0.2大盘占80%过度主导,调整为更均衡的0.35/0.25/0.40,
          中证2000反映市场实际赚钱效应,提高权重避免分化市误判）
        ThemeTrend = TOP3主题平均分(无主题数据时用index_trend)
        TrendScore = IndexTrend * 0.4 + ThemeTrend * 0.6
        
        优化:引入昨日基准+平滑过渡,避免盘中分数剧烈波动
        """
        # ── 0. 获取昨日基准分数(用于平滑过渡) ──
        yesterday_data = self.get_yesterday_market_data()
        yesterday_trend_score = yesterday_data.get('trend_score') if yesterday_data else None
        if yesterday_trend_score is not None and yesterday_trend_score != '?':
            try:
                yesterday_trend_score = float(yesterday_trend_score)
                print(f"[平滑] 昨日基准分数: {yesterday_trend_score}")
            except:
                yesterday_trend_score = None
                print(f"[平滑] 昨日分数转换失败: {yesterday_data}")
        else:
            print(f"[平滑] 无昨日基准: yesterday_data={yesterday_data}")
        
        # ── 1. 提取指数趋势分(直接使用,不再额外修正) ──
        sh_score = 0
        hs300_score = 0
        zz2000_score = 0
        
        for r in results_per_index:
            if r['name'] == '上证指数':
                sh_score = r['trend_score']
            elif r['name'] == '沪深300':
                hs300_score = r['trend_score']
            elif r['name'] == '中证2000':
                zz2000_score = r['trend_score']
        
        # 计算指数趋势分(调整权重:中证2000提高至0.40,反映实际赚钱效应)
        index_trend = sh_score * 0.35 + hs300_score * 0.25 + zz2000_score * 0.40
        
        # ── 2. 主题趋势分 ──
        # 有足够主题历史数据(每主题≥5轮)时用TOP3主题平均分,不足时用指数趋势
        MIN_HISTORY_CYCLES = 5
        if self.theme_score_history and len(self.theme_score_history) > 0:
            sufficient_hist = all(len(hist) >= MIN_HISTORY_CYCLES for hist in self.theme_score_history.values() if hist)
            if sufficient_hist:
                vals = []
                for theme, hist in self.theme_score_history.items():
                    if hist:
                        vals.append(hist[-1])
                if vals:
                    top_avg = sum(sorted(vals, reverse=True)[:3]) / min(3, len(vals))
                    theme_trend_raw = min(100, max(30, 50 + top_avg * 6))
                else:
                    theme_trend_raw = index_trend
            else:
                theme_trend_raw = index_trend
        else:
            theme_trend_raw = index_trend
        
        # 获取市场广度用于修正
        overview = self.compute_market_overview()
        up_ratio = overview.get('up_ratio', 50) if overview else 50
        
        # 市场广度修正(轻微):上涨比例<40%时打折
        if up_ratio < 40:
            breadth_factor = 0.85
        elif up_ratio < 50:
            breadth_factor = 0.95
        else:
            breadth_factor = 1.0
        theme_trend = round(theme_trend_raw * breadth_factor, 1)

        self._recent_theme_scores = theme_trend

        # ── 3. 计算实时趋势分(与market_analysis.py同步加分逻辑) ──
        trend_score_raw = round(index_trend * 0.4 + theme_trend * 0.6, 1)
        
        # 主题趋势加分(与market_analysis.py同步)
        if theme_trend > 90:
            trend_score_raw += 10
        elif theme_trend > 85:
            trend_score_raw += 5
        
        # 量能加分:检查今日成交量是否接近60日最大值(简化版:用指数实时成交量)
        # 注:实时量能难以获取60日对比,暂用上涨比例替代判断
        if up_ratio >= 70:
            trend_score_raw += 5  # 大面积上涨视为量能放大
        
        # 赚钱效应修正:涨停/跌停比反映市场真实强弱,弥补指数权重不足
        zt_count = overview.get('zt_count', 0) if overview else 0
        dt_count = overview.get('dt_count', 0) if overview else 0
        if zt_count > 0 and dt_count >= 0:
            zt_dt_ratio = zt_count / max(dt_count, 1)
            if zt_count >= 20 and dt_count <= 3 and zt_dt_ratio > 5:
                trend_score_raw += 8  # 涨停潮+几乎无跌停=强势赚钱效应
            elif zt_count >= 15 and dt_count <= 5 and zt_dt_ratio > 3:
                trend_score_raw += 5  # 明显赚钱效应
            elif zt_count >= 10 and zt_dt_ratio > 2:
                trend_score_raw += 3  # 温和赚钱效应
            # 反向惩罚:跌停>涨停时扣分
            if dt_count > zt_count and dt_count >= 10:
                trend_score_raw -= 8  # 跌停潮=恐慌
            elif dt_count > zt_count and dt_count >= 5:
                trend_score_raw -= 4
        
        trend_score_raw = min(100, max(0, trend_score_raw))
        
        # ── 4. 平滑过渡:引入昨日基准,避免盘中分数剧烈波动 ──
        # 规则:
        # - 开盘前30分钟:昨日基准权重70%,实时权重30%
        # - 盘中逐步降低基准权重,收盘时实时权重100%
        # - 单日波动限制:不能偏离昨日基准超过±15分
        now = datetime.now()
        if yesterday_trend_score is not None and now.hour < 15:
            # 计算盘中时间进度(9:30~11:30上午2小时,13:00~15:00下午2小时)
            minutes_since_open = 0
            if 9 <= now.hour < 12:
                # 上午时段(9:30~11:30)
                if now.hour == 9:
                    minutes_since_open = max(0, now.minute - 30)
                else:
                    minutes_since_open = (now.hour - 9) * 60 + now.minute - 30
            elif 13 <= now.hour < 15:
                # 下午时段(13:00~15:00)
                minutes_since_open = 120 + (now.hour - 13) * 60 + now.minute
            
            # 时间进度权重(开盘时基准权重高,收盘时实时权重高)
            if minutes_since_open <= 30:
                base_weight = 0.70  # 开盘前30分钟,基准权重70%
            elif minutes_since_open <= 120:
                base_weight = 0.50  # 上午,基准权重50%
            elif minutes_since_open <= 240:
                base_weight = 0.30  # 下午前半段,基准权重30%
            else:
                base_weight = 0.10  # 收盘前,基准权重10%
            
            realtime_weight = 1.0 - base_weight
            
            # 平滑计算
            trend_score_smooth = yesterday_trend_score * base_weight + trend_score_raw * realtime_weight
            
            # 单日波动限制:不能偏离昨日基准超过±15分
            max_deviation = 15
            trend_score = max(yesterday_trend_score - max_deviation, 
                             min(yesterday_trend_score + max_deviation, trend_score_smooth))
            
            # 记录平滑信息(用于调试)
            self._smooth_info = {
                'yesterday_base': yesterday_trend_score,
                'realtime_raw': trend_score_raw,
                'base_weight': base_weight,
                'minutes': minutes_since_open,
                'smooth_result': round(trend_score, 1)
            }
            print(f"[平滑] 实时={trend_score_raw:.1f} 基准权重={base_weight:.0%} 平滑后={trend_score:.1f}")
        else:
            # 无昨日基准或收盘后,直接用实时分数
            trend_score = trend_score_raw
            self._smooth_info = None
        
        trend_score = round(trend_score, 1)

        # 市场状态 & 建议仓位（与 market_analysis.py get_market_status_and_position 阈值对齐）
        # 阈值: ≥80/70/60/55/45/35（不再使用滞回，避免忽上忽下）
        if trend_score >= 80:
            market_status, pos_range, pos = "主升浪", "80~100%", 90
        elif trend_score >= 70:
            market_status, pos_range, pos = "强趋势", "60~80%", 70
        elif trend_score >= 60:
            market_status, pos_range, pos = "趋势良好", "50~70%", 60
        elif trend_score >= 55:
            market_status, pos_range, pos = "震荡", "30~50%", 40
        elif trend_score >= 45:
            market_status, pos_range, pos = "弱势", "20~30%", 25
        elif trend_score >= 35:
            market_status, pos_range, pos = "退潮", "10~20%", 15
        else:
            market_status, pos_range, pos = "主跌段", "0~10%", 5

        # ── 赚钱效应惩罚(顶级私募视角:不只是看趋势分,还要看实际赚钱效应) ──
        # 解决"测强不测涨"问题:趋势分可能仍偏高,但跌停潮+恐慌性抛售时必须空仓
        # 惩罚只下减不上加,且仅在弱势环境下生效(强势环境不惩罚)
        penalty_info = self._apply_profit_effect_penalty(
            trend_score, market_status, pos, pos_range,
        )
        self._last_penalty_info = penalty_info  # 保存供 report 使用
        if penalty_info:
            pos = penalty_info['pos']
            pos_range = penalty_info['pos_range']
            market_status = penalty_info['market_status']
            # 调试日志
            print(f"[赚钱效应惩罚] 原仓位→新仓位: {penalty_info['original_pos']}%→{pos}%  原因: {penalty_info['reason']}")

        return trend_score, index_trend, theme_trend, market_status, pos, pos_range

    def _apply_profit_effect_penalty(self, trend_score, market_status, pos, pos_range):
        """
        赚钱效应惩罚机制(顶级私募量化策略)
        基于实际市场亏钱效应,在弱势环境下进一步压低仓位,直至空仓
        返回 None 表示不惩罚;返回 dict 表示惩罚后的新参数
        """
        # 仅在弱势及以下环境生效(趋势良好及以上不惩罚,避免误伤强势行情)
        if trend_score >= 60:
            return None

        # 获取全市场统计(涨跌停、涨跌家数)
        full_stats = self.get_full_market_stats()
        if not full_stats:
            return None

        zt_count = full_stats.get('zt_count', 0) or 0
        dt_count = full_stats.get('dt_count', 0) or 0
        up_ratio = full_stats.get('up_ratio', 50) or 50
        down_ratio = full_stats.get('down_ratio', 50) or 50
        up_count = full_stats.get('up_count', 0) or 0
        down_count = full_stats.get('down_count', 0) or 0

        original_pos = pos
        reason_parts = []

        # ── 规则1: 跌停潮(最严厉) ──
        # 跌停≥100家,无论趋势分多少,强制空仓
        if dt_count >= 100:
            pos = 0
            pos_range = "0%(空仓)"
            reason_parts.append(f"跌停潮({dt_count}家)")
        # ── 规则2: 恐慌性抛售 ──
        # 跌停≥50家 且 跌停 > 涨停 × 2,仓位压到 0~5%
        elif dt_count >= 50 and zt_count > 0 and dt_count >= zt_count * 2:
            pos = min(pos, 5)
            pos_range = "0~5%(几乎空仓)"
            reason_parts.append(f"恐慌抛售(跌停{dt_count}/涨停{zt_count})")
        # ── 规则3: 跌停明显多于涨停 + 大面积下跌 ──
        # 跌停 > 涨停 × 1.5 且 下跌 > 60%,仓位减半
        elif (zt_count > 0 and dt_count >= zt_count * 1.5 and down_ratio > 60) or \
             (zt_count == 0 and dt_count >= 30 and down_ratio > 60):
            pos = min(pos, max(5, pos // 2))
            pos_range = f"0~{max(10, pos)}%(极低仓位)"
            reason_parts.append(f"跌多涨少(跌停{dt_count}/涨停{zt_count},下跌{down_ratio}%)")
        # ── 规则4: 极端弱势(趋势分<45 + 跌停>30 + 上涨<40%) ──
        # 仓位压到 0~5%
        elif trend_score < 45 and dt_count >= 30 and up_ratio < 40:
            pos = min(pos, 5)
            pos_range = "0~5%(几乎空仓)"
            reason_parts.append(f"极端弱势(评分{trend_score:.0f},跌停{dt_count},上涨{up_ratio}%)")
        # ── 规则5: 弱势+赚钱效应缺失(趋势分<55 + 上涨<35% + 跌停>涨停) ──
        # 仓位压到 ≤10%
        elif trend_score < 55 and up_ratio < 35 and dt_count > zt_count:
            pos = min(pos, 10)
            pos_range = "0~10%(极低仓位)"
            reason_parts.append(f"赚钱效应缺失(上涨{up_ratio}%,跌停{dt_count}>涨停{zt_count})")

        if not reason_parts:
            return None

        # 市场状态升级(标注恐慌)
        if pos == 0:
            market_status = "恐慌空仓"
        elif pos <= 5:
            market_status = "极弱空仓"
        elif pos <= 10:
            market_status = "弱势空仓"

        return {
            'pos': pos,
            'pos_range': pos_range,
            'market_status': market_status,
            'original_pos': original_pos,
            'reason': ' + '.join(reason_parts),
            'zt_count': zt_count,
            'dt_count': dt_count,
            'up_ratio': up_ratio,
            'down_ratio': down_ratio,
        }

    # ════════════════════════════════════════════
    # 2. 通达信连接
    # ════════════════════════════════════════════
    def find_fastest_server(self):
        if not TDX_AVAILABLE:
            return
        print("⏳ 测试通达信服务器...")
        results = []

        def _test(host, port, res):
            try:
                api = TdxHq_API()
                start = time.time()
                if not api.connect(host, port, time_out=3):
                    return
                latency = (time.time() - start) * 1000
                for mkt, code in [(0, "600000"), (1, "000001")]:
                    data = api.get_security_bars(9, mkt, code, 0, 5)
                    if data:
                        break
                res.append((host, port, latency))
                api.disconnect()
            except:
                pass

        threads = []
        for host, port in self.servers:
            t = threading.Thread(target=_test, args=(host, port, results))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=5)

        if results:
            results.sort(key=lambda x: x[2])
            self.sorted_servers = [(h, p) for h, p, _ in results]
            self.best_server = self.sorted_servers[0]
            self.server_index = 0
            print(f"✅ 最快服务器: {self.best_server[0]}:{self.best_server[1]} ({results[0][2]:.1f}ms)")
            print(f"   可用服务器: {len(self.sorted_servers)} 台")
        else:
            print("⚠ 未找到可用服务器,使用默认列表轮巡")
            self.sorted_servers = self.servers[:]
            self.best_server = self.servers[0]

    def connect(self, server=None):
        if self.connected:
            return True
        if not TDX_AVAILABLE:
            print("❌ 通达信不可用")
            return False

        try:
            if not self.sorted_servers:
                self.find_fastest_server()
                if not self.sorted_servers:
                    return False

            host, port = server if server else self.best_server
            self.api = TdxHq_API(heartbeat=True)
            self.connected = self.api.connect(host, port)
            if self.connected:
                print(f"✅ 通达信连接成功: {host}:{port}")
                return True
        except Exception as e:
            print(f"❌ 连接 {host}:{port} 失败: {e}")
        return False

    def reconnect_round_robin(self):
        """遍历所有已发现的服务器,一旦成功就停止,返回True/False"""
        if not self.sorted_servers:
            self.find_fastest_server()
            if not self.sorted_servers:
                return False

        self.disconnect()
        start_idx = self.server_index

        for i in range(len(self.sorted_servers)):
            idx = (start_idx + i) % len(self.sorted_servers)
            host, port = self.sorted_servers[idx]
            print(f"   尝试服务器 [{idx+1}/{len(self.sorted_servers)}] {host}:{port}...", end="")
            try:
                api = TdxHq_API(heartbeat=True)
                ok = api.connect(host, port, time_out=3)
                if ok:
                    self.api = api
                    self.connected = True
                    self.best_server = (host, port)
                    self.server_index = idx
                    print(" ✅")
                    return True
                print(" ✗")
            except Exception as e:
                print(f" ✗ ({e})")

        self.connected = False
        return False

    def disconnect(self):
        if self.api and self.connected:
            try:
                self.api.disconnect()
            except:
                pass
            self.connected = False

    # ════════════════════════════════════════════
    # 3. 行情获取
    # ════════════════════════════════════════════
    def fetch_all_quotes(self):
        """通过新浪财经API获取全市场实时行情,失败时自动切换东方财富备用源"""
        stock_codes = list(self.stock_themes.keys())
        quote_map = {}
        first_round = len(self.quotes) == 0
        source = None

        # ── 优先:新浪财经批量接口 ──
        for offset in range(0, len(stock_codes), 180):
            batch = stock_codes[offset:offset + 180]
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
                }, timeout=8)
                resp.encoding = 'gbk'
                lines = resp.text.strip().split('\n')
                for line in lines:
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

                        open_px = float(fields[1])       # fields[1]=今开价
                        prev_close = float(fields[2])
                        price = float(fields[3])
                        high = float(fields[4])
                        low = float(fields[5])
                        volume = float(fields[8])
                        amount = float(fields[9])

                        if prev_close > 0 and price > 0:
                            pct_chg = (price - prev_close) / prev_close * 100
                        else:
                            pct_chg = 0

                        quote_map[ts_c] = {
                            'price': price,
                            'pct_chg': round(pct_chg, 2),
                            'amount': amount,
                            'vol': int(volume),
                            'last_close': prev_close,
                            'open': open_px,
                            'high': high,
                            'low': low,
                        }
                    except (IndexError, ValueError):
                        continue
                source = '新浪财经API'
            except Exception as e:
                if first_round:
                    print(f"   ⚠ 新浪API异常: {e},尝试东方财富备用源...")
                quote_map = {}  # 清空已获取数据,切换备用源
                break

        # ── 备用:东方财富接口 ──
        if not quote_map:
            em_url = 'https://push2.eastmoney.com/api/qt/ulist.np/get'
            em_fields = 'f12,f14,f3,f4,f5,f6,f7,f15,f16,f17'
            secids = []
            for code in stock_codes:
                if code.endswith('.SH'):
                    secids.append('1.' + code.replace('.SH', ''))
                elif code.endswith('.SZ'):
                    secids.append('0.' + code.replace('.SZ', ''))

            # 东方财富每次最多200只,分批请求
            for offset in range(0, len(secids), 180):
                batch_secids = secids[offset:offset + 180]
                params = {
                    'fltt': 2,
                    'invt': 2,
                    'fields': em_fields,
                    'secids': ','.join(batch_secids)
                }
                try:
                    resp = requests.get(em_url, params=params, headers={
                        'User-Agent': 'Mozilla/5.0',
                        'Referer': 'https://www.eastmoney.com'
                    }, timeout=8)
                    data = resp.json()
                    diff = data.get('data', {}).get('diff', [])
                    for item in diff:
                        f12 = item.get('f12', '')  # 代码
                        f3 = item.get('f3', 0)    # 涨跌幅%
                        f4 = item.get('f4', 0)    # 涨跌额
                        f5 = item.get('f5', 0)    # 成交量
                        f6 = item.get('f6', 0)    # 成交额
                        # 根据代码判断市场: 6开头=SH, 0/3开头=SZ
                        if f12.startswith(('6', '9')):
                            ts_c = f12.zfill(6) + '.SH'
                        else:
                            ts_c = f12.zfill(6) + '.SZ'
                        # 东方财富无昨收,用当前价和涨跌额反推
                        if f3 != 0 and f4 != 0:
                            price = round(f4 / (f3 / 100), 2)
                            prev_close = round(price - f4, 2)
                        else:
                            price = 0
                            prev_close = 0
                        quote_map[ts_c] = {
                            'price': price,
                            'pct_chg': round(f3, 2),
                            'amount': f6,
                            'vol': int(f5),
                            'last_close': prev_close,
                            'open': item.get('f17', 0),
                            'high': item.get('f15', 0),
                            'low': item.get('f16', 0),
                        }
                    source = '东方财富API'
                except Exception as e:
                    if first_round:
                        print(f"   ⚠ 东方财富API异常: {e},行情获取失败")
                    return False

        if first_round and quote_map:
            total = len(stock_codes)
            print(f"   ✅ 行情获取: {len(quote_map)}/{total} 只 ({source})")

        if quote_map:
            self.prev_quotes = self.quotes.copy()
            self.quotes = quote_map
            return True
        return False

    # ════════════════════════════════════════════
    # 4. 分析引擎
    # ════════════════════════════════════════════
    def analyze(self):
        now = datetime.now()
        results = {
            'theme_scores': {},
            'theme_volumes': {},
            'market_stats': {},
            'timestamp': now
        }

        total_up = 0
        total_down = 0
        total_zt = 0
        total_dt = 0
        total_count = 0

        # ── 计算每个主题的强度 ──
        for theme_name, stocks in self.theme_stocks.items():
            scores = []
            theme_up = 0
            theme_vol = 0

            for ts_code, name, layer in stocks:
                q = self.quotes.get(ts_code)
                if not q:
                    continue

                pct = q['pct_chg']
                scores.append(pct)
                total_count += 1

                if pct > 0:
                    total_up += 1
                    theme_up += 1
                elif pct < 0:
                    total_down += 1

                if pct >= 9.5:
                    total_zt += 1
                elif pct <= -9.5:
                    total_dt += 1

                theme_vol += q.get('amount', 0)

            if scores:
                avg_score = sum(scores) / len(scores)
                results['theme_scores'][theme_name] = round(avg_score, 2)
                results['theme_volumes'][theme_name] = round(theme_vol / 10000, 2)  # 万元

                self.theme_score_history[theme_name].append(avg_score)
                self.theme_volume_history[theme_name].append(theme_vol)

        # ── 市场情绪 ──
        up_ratio = total_up / total_count * 100 if total_count > 0 else 0
        down_ratio = total_down / total_count * 100 if total_count > 0 else 0
        results['market_stats'] = {
            'total': total_count,
            'up': total_up,
            'down': total_down,
            'up_ratio': round(up_ratio, 1),
            'down_ratio': round(down_ratio, 1),
            'zt_count': total_zt,
            'dt_count': total_dt
        }

        return results

    def detect_market_sentiment(self, results):
        """检测整体市场情绪预警(使用 market_analysis 算法)"""
        report = getattr(self, '_last_report', None)
        if report is None:
            # 回退:按旧简单阈值
            ms = results['market_stats']
            alerts = []
            if time.time() - self.last_market_alert < 1800:
                return alerts
            if ms['up_ratio'] > 80:
                alerts.append({
                    'type': 'market_overheat',
                    'msg': f"🔥🔥 市场过热! 上涨{ms['up']}/{ms['total']}({ms['up_ratio']}%) 涨停{ms['zt_count']}家"
                })
                self.last_market_alert = time.time()
            elif ms['down_ratio'] > 50:
                alerts.append({
                    'type': 'market_fear',
                    'msg': f"❄️❄️ 大面积亏钱! 下跌{ms['down']}/{ms['total']}({ms['down_ratio']}%) 跌停{ms['dt_count']}家"
                })
                self.last_market_alert = time.time()
            return alerts
        # 新算法
        return self.detect_market_sentiment_v2(report)

    # ════════════════════════════════════════════
    # 5. 开盘分析(9:32)
    # ════════════════════════════════════════════
    def run_opening_analysis(self):
        """
        9:32分开盘分析:基于market_analysis算法,输出三大指数+主题+仓位建议。
        """
        now = datetime.now()
        results = self.analyze()
        if not results or not results['theme_scores']:
            return

        # 使用新算法的报告
        report = self.compute_market_sentiment_report()
        ms = results['market_stats']

        # ── 1. 主题排序 TOP10 ──
        sorted_themes = sorted(results['theme_scores'].items(), key=lambda x: x[1], reverse=True)
        top10 = sorted_themes[:10]

        lines = []
        for rank, (theme_name, avg_pct) in enumerate(top10, 1):
            stocks = self.theme_stocks.get(theme_name, [])
            up_count = 0
            total_in_theme = 0
            leader_pct = None
            leader_name = ""
            theme_amount = 0
            for ts_code, name, layer in stocks:
                q = self.quotes.get(ts_code)
                if not q:
                    continue
                total_in_theme += 1
                if q['pct_chg'] > 0: up_count += 1
                theme_amount += q.get('amount', 0)
                if layer == 'leader' and (leader_pct is None or q['pct_chg'] > leader_pct):
                    leader_pct = q['pct_chg']
                    leader_name = name
            up_ratio = up_count / total_in_theme * 100 if total_in_theme > 0 else 0
            leader_str = f"{leader_name}{leader_pct:+.1f}%" if leader_name else "-"
            amount_yi = round(theme_amount / 1e8, 2)
            lines.append((rank, theme_name, avg_pct, up_ratio, leader_str, amount_yi, total_in_theme))

        ts = now.strftime('%H:%M:%S')

        title = f"📊 开盘分析 {now.strftime('%m-%d')}"
        content_lines = [
            f"📊 开盘竞价全景分析",
            f"时间: {ts}",
        ]

        if report:
            content_lines.append(f"市场状态: {report['market_status']}")
            content_lines.append(f"趋势总评分: {report['trend_score']:.1f} 建议仓位: {report['position']}%({report['position_range']})")
            content_lines.append(f"---")
            content_lines.append(f"【三大指数】趋势/情绪/涨幅")
            for r in report['index_results']:
                content_lines.append(f"  {r['name']}: 趋势{r['trend_score']:.0f} 情绪{r['sentiment_score']:.0f} 涨幅{r.get('pct_chg', 0):+.2f}%")
            content_lines.append(f"---")

        content_lines.append(f"【盘面统计】")
        content_lines.append(f"上涨: {ms['up']}/{ms['total']}({ms['up_ratio']}%)  涨停: {ms['zt_count']}  跌停: {ms['dt_count']}")
        content_lines.append(f"---")
        content_lines.append(f"【五维竞争力 TOP10】")
        for rank, name, avg_pct, up_r, ldr, amt, cnt in lines:
            bar = "█" * max(1, min(10, int(abs(avg_pct) + 0.5)))
            direction = "+" if avg_pct >= 0 else ""
            content_lines.append(
                f"#{rank} {name}  {direction}{avg_pct:.1f}%{bar}  ↑{up_r:.0f}%  龙头:{ldr}  {amt}亿  {cnt}只"
            )
        content_lines.append(f"---")
        content_lines.append(f"1板块平均涨幅  2上涨占比  3龙头涨幅  4成交额  5成分股数")

        self.send_wechat(title, '\n'.join(content_lines))

        # 控制台
        print(f"\n{'='*60}")
        print(f"📊 开盘分析 [{ts}]")
        if report:
            print(f"   市场状态: {report['market_status']}  趋势总评分: {report['trend_score']:.1f}  仓位: {report['position']}%")
            for r in report['index_results']:
                print(f"   {r['name']}: 趋势{r['trend_score']:.0f}/情绪{r['sentiment_score']:.0f}/涨幅{r.get('pct_chg', 0):+.2f}%")
        print(f"{'='*60}")
        print(f"{'排名':<4} {'板块':<10} {'强度':<8} {'↑占比':<6} {'龙头':<18} {'成交额':<10} {'成分'}")
        print(f"{'-'*60}")
        for rank, name, avg_pct, up_r, ldr, amt, cnt in lines:
            print(f"{rank:<4} {name:<10} {avg_pct:+.1f}%   {up_r:.0f}%   {ldr:<16} {amt:<8} {cnt}")
        print(f"{'='*60}\n")

    # ════════════════════════════════════════════
    # 6. 推送
    # ════════════════════════════════════════════
    def send_wechat(self, title, content):
        if not self.sckey:
            print("⚠ 未配置WECHAT_SCKEY")
            return
        url = f"https://sctapi.ftqq.com/{self.sckey}.send"
        # 换行归一化:Server酱按Markdown渲染,\n\n = 段落空行,\n = 被合并
        # 解决:每行以2空格结尾(Markdown硬换行),去掉Markdown标题/表格/分割线
        # 规则:1空行→忽略 2---→改为短线分隔 3**粗体**→去掉符号 4##标题→保留文字
        lines = []
        import re
        for raw_line in content.replace('\r\n', '\n').split('\n'):
            stripped = raw_line.strip()
            if not stripped:
                continue
            # 去掉Markdown粗体符号 **text** → text
            stripped = re.sub(r'\*\*(.+?)\*\*', r'\1', stripped)
            # 去掉Markdown标题前缀 ## / ###
            stripped = re.sub(r'^#{1,6}\s*', '', stripped)
            # Markdown水平分割线 → 短线分隔符
            if re.match(r'^-{3,}$', stripped):
                lines.append('━━━━━━━━━━━━' + '  ')
                continue
            # 其他行:行尾2空格 = Markdown硬换行
            lines.append(stripped + '  ')
        normalized = '\n'.join(lines)

        try:
            resp = requests.post(url, data={"title": title, "desp": normalized}, timeout=10)
            print(f"📱 推送成功: {title[:30]}")
            return resp
        except Exception as e:
            print(f"❌ 推送失败: {e}")

    # ════════════════════════════════════════════
    # 7. 主循环
    # ════════════════════════════════════════════
    def run(self):
        print("=" * 60)
        print("🔥 游资级别实时主题盯盘系统 (market_analysis算法)")
        print("   数据源: theme_portfolio.db + mootdx通达信实时行情 + 盘后K线缓存")
        print("   评分模型: MA趋势+指数站位+市场广度 + 情绪评分(量价振幅)")
        print("   更新周期: 60秒")
        print("   推送: Server酱微信")
        print("=" * 60)

        # ── 加载数据 ──
        self.load_theme_db()
        self.load_ref_prices()

        # 加载三大指数盘后K线缓存(用于MA5/MA10/MA20)
        self.load_index_klines()

        # 加载成分股K线数据(用于主题趋势/情绪分计算)
        self.load_component_klines()

        # 加载换手率缓存(用于弱转强量价评分)
        self.load_turnover_cache()

        # 加载技术因子+总市值缓存(用于尾盘突袭技术形态评分+市值过滤)
        self.load_stock_factors_cache()

        # ── 连接 ──
        if TDX_AVAILABLE:
            if not self.connect():
                print("⏳ 首次连接失败,启动服务器轮巡...")
                if not self.reconnect_round_robin():
                    print("⚠️ 通达信服务器不可用,将使用新浪/东方财富API获取行情")
            else:
                print(f"✅ 通达信行情可用")
        else:
            print("⚠️ mootdx未安装,将使用新浪/东方财富API获取行情")

        print(f"\n📊 开始监控 {len(self.theme_stocks)} 个主题 {sum(len(v) for v in self.theme_stocks.values())} 只股票")
        print("   交易时段: 9:30-11:30, 13:00-15:00")
        print("   (按 Ctrl+C 停止)\n")

        cycle = 0
        first_run = True

        try:
            while True:
                now = datetime.now()

                # ── 15:05 自动终止 ──
                if now.hour == 15 and now.minute >= 5:
                    print(f"\n[{now.strftime('%H:%M:%S')}] 🛑 收盘时间到,自动退出")
                    break

                # ── 非交易时段跳过 ──
                if not self.is_trading_time(now):
                    if first_run or now.minute == 0:
                        print(f"[{now.strftime('%H:%M:%S')}] 非交易时段,等待开盘...")
                        first_run = False
                    time.sleep(30)
                    continue

                first_run = False
                cycle += 1

                # ── 获取行情(个股 + 指数) ──
                ok = self.fetch_all_quotes()
                if not ok:
                    print(f"[{now.strftime('%H:%M:%S')}] ⚠ 行情获取失败,尝试重试...")
                    # 行情获取使用新浪/东方财富HTTP API,不依赖通达信连接
                    for quote_retry in range(3):
                        time.sleep(3)
                        ok = self.fetch_all_quotes()
                        if ok:
                            print(f"   第{quote_retry+1}次重试成功")
                            break
                        print(f"   第{quote_retry+1}次重试失败...")
                    if not ok:
                        print(f"   ❌ 放弃本轮,等待下一周期")
                        time.sleep(10)
                        continue

                # ── 计算趋势评分+市场情绪(新算法) ──
                _ = self.compute_market_sentiment_report()

                # ── 同花顺扶摇 MCP 补充数据 ──
                self.fetch_fuyao_data(now)

                # ── 9:32 开盘分析(仅一次) ──
                if not self.opening_analysis_done and now.hour == 9 and 32 <= now.minute <= 35:
                    print(f"\n[{now.strftime('%H:%M:%S')}] ⏰ 触发开盘分析...")
                    self.run_opening_analysis()
                    self.opening_analysis_done = True
                    time.sleep(5)
                    continue

                # ── 分析 ──
                results = self.analyze()
                if not results:
                    time.sleep(10)
                    continue

                # 补充新算法报告到 results(供 print_summary / push_alerts 使用)
                results['sentiment_report'] = getattr(self, '_last_report', None)

                # ── 中军弱转强:分时快照采集 ──
                # 10:30采集早盘数据
                if not self.snapshot_morning_done and now.hour == 10 and now.minute >= 30:
                    self.collect_intraday_snapshot('morning')
                    self.snapshot_morning_done = True
                    print(f"📸 [{now.strftime('%H:%M')}] 早盘分时快照已采集: {len(self.intraday_snapshots)} 只")
                # 14:00采集午盘数据
                if not self.snapshot_noon_done and now.hour == 14 and now.minute >= 0:
                    self.collect_intraday_snapshot('noon')
                    self.snapshot_noon_done = True
                    print(f"📸 [{now.strftime('%H:%M')}] 午盘分时快照已采集")
                # 14:30采集尾盘基准(只采集一次)
                if not self.snapshot_tail_done and now.hour == 14 and now.minute >= 30:
                    self.collect_intraday_snapshot('tail')
                    self.snapshot_tail_done = True
                    print(f"📸 [{now.strftime('%H:%M')}] 14:30尾盘基准已采集")

                # ── 每3分钟输出一次摘要 ──
                if cycle % 3 == 1:
                    self.print_summary(results)

                # ── 每15分钟更新一次全市场统计(后台线程) ──
                if cycle % 90 == 1:  # 60秒×90=5400秒=15分钟
                    print(f"⏳ 后台更新全市场统计...")
                    self.fetch_full_market_stats_sina()

                    # ── 每15分钟计算并输出主题综合分TOP10 ──
                    theme_scores = self.compute_theme_scores_realtime()
                    self._last_theme_results = theme_scores  # 供大盘动作信号监测使用
                    if theme_scores:
                        print(f"\n{'='*90}")
                        print(f"📊 次日套利Alpha TOP10 [{now.strftime('%H:%M:%S')}]")
                        print(f"{'排名':<4} {'主题':<14} {'Alpha':>6} {'信号':<4} {'阶段':<6} {'生命':>4} {'T+1':>4} {'定价':>4} {'联动':>4} {'风险':>4} {'龙头':<14} {'涨停'}")
                        print(f"{'-'*90}")
                        for i, r in enumerate(theme_scores[:10], 1):
                            signal_emoji = {'买入': '✅', '关注': '👀', '观望': '⏸', '回避': '❌'}.get(r.get('signal', ''), '')
                            leader_str = f"{r.get('leader_name', '')}({r.get('leader_pct', 0):+.1f}%)" if r.get('leader_name') else '-'
                            print(f"{i:<4} {r['theme']:<14} {r.get('alpha', 0):>5.1f} {signal_emoji:<4} {r.get('stage', ''):<6} {r.get('lifecycle_score', 0):>4.0f} {r.get('t1_score', 0):>4.0f} {r.get('pricing_score', 0):>4.0f} {r.get('linkage_score', 0):>4.0f} {r.get('risk_penalty', 0):>4.0f} {leader_str:<14} {r.get('zt_count', 0)}")
                        print(f"{'='*90}")

                        # 买入/关注信号汇总
                        buy_signals = [r for r in theme_scores if r.get('signal') in ('买入', '关注')]
                        if buy_signals:
                            print(f"📌 次日套利候选({len(buy_signals)}个):")
                            for r in buy_signals[:5]:
                                leader_str = f"龙头:{r.get('leader_name', '')}({r.get('leader_pct', 0):+.1f}%)" if r.get('leader_name') else ''
                                print(f"   {r.get('signal')} {r['theme']} Alpha{r.get('alpha', 0):.0f} {r.get('stage')} {leader_str} 涨停{r.get('zt_count', 0)}只")

                        print(f"{'='*90}\n")

                        # ── 推送次日套利Alpha TOP10到微信(至少30分钟冷却) ──
                        if time.time() - self.last_score_alert >= 1800:
                            lines = []
                            for i, r in enumerate(theme_scores[:10], 1):
                                signal_tag = r.get('signal', '')
                                stage_tag = r.get('stage', '')
                                leader_str = f" 龙头:{r.get('leader_name', '')}" if r.get('leader_name') else ''
                                zt_str = f" 涨停{r.get('zt_count', 0)}只" if r.get('zt_count', 0) > 0 else ''
                                lines.append(f"{i}. [{signal_tag}]{r['theme']} Alpha{r.get('alpha', 0):.0f} {stage_tag}{leader_str}{zt_str}")
                            # 买入信号单独高亮
                            buy_lines = [r for r in theme_scores[:20] if r.get('signal') == '买入']
                            if buy_lines:
                                lines.append("---")
                                lines.append("✅ 次日买入候选:")
                                for r in buy_lines[:3]:
                                    leader_str = f" 龙头:{r.get('leader_name', '')}({r.get('leader_pct', 0):+.1f}%)" if r.get('leader_name') else ''
                                    lines.append(f"  {r['theme']} Alpha{r.get('alpha', 0):.0f} {r.get('stage')}{leader_str}")
                            content = f"📊 次日套利Alpha TOP10 [{now.strftime('%H:%M')}]\n" + "\n".join(lines)
                            self.send_wechat(f"📊 次日套利Alpha {now.strftime('%H:%M')}", content)
                            self.last_score_alert = time.time()

                # ── 大盘情绪检测 → 推送 ──
                all_alerts = []
                all_alerts.extend(self.detect_market_sentiment(results))

                # ── 大盘动作信号(加仓/减仓) → 推送 ──
                action_alerts = self._check_market_action_signals()
                if action_alerts:
                    all_alerts.extend(action_alerts)

                if all_alerts:
                    self.push_alerts(all_alerts, now)

                # ── 14:30后每2分钟扫描中军弱转强 ──
                if now.hour == 14 and now.minute >= 30:
                    if time.time() - self.last_w2s_scan_time >= 120:  # 2分钟冷却
                        w2s_signals = self.scan_middle_w2s()
                        if w2s_signals:
                            print(f"\n{'='*90}")
                            print(f"🎯 中军弱转强扫描 [{now.strftime('%H:%M:%S')}] 共{len(w2s_signals)}只候选")
                            print(f"{'排名':<4} {'代码':<12} {'名称':<10} {'主题':<12} {'总分':>4} {'形态':>4} {'量价':>4} {'共振':>4} {'信号':<6} {'涨幅':>6} {'关键特征'}")
                            print(f"{'-'*90}")
                            for i, s in enumerate(w2s_signals[:10], 1):
                                emoji = {'强买入': '✅', '关注': '👀', '观望': '⏸'}.get(s['signal'], '')
                                # 提取关键特征
                                d = s.get('detail', {})
                                feats = []
                                if d.get('early_weak'): feats.append('早弱')
                                if d.get('noon_stable'): feats.append('午稳')
                                if d.get('tail_rally'): feats.append(f"尾拉{d['tail_rally']:+.1f}%")
                                if d.get('above_vwap'): feats.append('破均')
                                if d.get('tail_vol_surge'): feats.append('尾量增')
                                if d.get('shrink_vol'): feats.append(f"缩量{d['shrink_vol']:.1f}")
                                if d.get('leader_not_zt'): feats.append('龙头未停')
                                feat_str = ' '.join(feats[:5]) if feats else '-'
                                print(f"{i:<4} {s['ts_code']:<12} {s['name']:<10} {s['theme']:<12} {s['total_score']:>4} {s['pattern_score']:>4} {s['vol_score']:>4} {s['theme_score']:>4} {emoji:<6} {s['pct_chg']:>+5.1f}% {feat_str}")
                            print(f"{'='*90}\n")

                            # 推送强买入信号到微信
                            strong_buys = [s for s in w2s_signals if s['signal'] == '强买入']
                            if strong_buys and time.time() - self.last_w2s_scan_time >= 600:  # 10分钟推送冷却
                                lines = []
                                for s in strong_buys[:5]:
                                    d = s.get('detail', {})
                                    feats = []
                                    if d.get('tail_rally'): feats.append(f"尾拉{d['tail_rally']:+.1f}%")
                                    if d.get('tail_vol_surge'): feats.append('尾盘放量')
                                    if d.get('leader_not_zt'): feats.append('龙头未涨停')
                                    feat_str = ' '.join(feats) if feats else ''
                                    lines.append(f"✅ {s['name']}({s['ts_code']}) 总分{s['total_score']} {s['theme']} 涨{s['pct_chg']:+.1f}% {feat_str}")
                                content = f"🎯 中军弱转强信号 [{now.strftime('%H:%M')}]\n" + "\n".join(lines)
                                self.send_wechat(f"🎯 弱转强 {now.strftime('%H:%M')}", content)

                        self.last_w2s_scan_time = time.time()

                # ── 14:50后每2分钟扫描「猎尾V4」中报池最佳回踩 ──
                if now.hour == 14 and now.minute >= 50:
                    if time.time() - self.last_tail_entry_scan_time >= 120:
                        tail_signals = self.scan_tail_recovery_v3()
                        # 写入跟踪表(用于未来交易日盘后回填和胜率分析)
                        self._save_tail_recovery_signals_to_tracker(tail_signals)
                        if tail_signals:
                            # 控制台输出
                            print(f"\n{'='*110}")
                            print(f"🎯 「猎尾V4」中报池回踩信号 [{now.strftime('%H:%M:%S')}] 共{len(tail_signals)}只候选")
                            print(f"{'排名':<4} {'代码':<12} {'名称':<10} {'主题':<10} {'总分':>4} {'量化':>5} {'二次':>4} {'回踩%':>7} {'乖MA20':>7} {'空间':>6} {'尾量':<9} {'涨幅':>6} {'信号'}")
                            print(f"{'-'*110}")
                            for i, s in enumerate(tail_signals[:10], 1):
                                d = s.get('detail', {})
                                emoji = {'强买入': '✅', '买入观察': '🟢', '关注': '👀'}.get(s['signal'], '')
                                vol_label = str(d.get('tail_vol_label', '-'))
                                print(f"{i:<4} {s['ts_code']:<12} {s['name']:<10} {s['theme']:<10} {s['total_score']:>4} {d.get('quant_score', 0):>5.1f} {d.get('realtime_score', 0):>4} {d.get('ret_pct', 0):>+6.1f}% {d.get('rise_gap_ma20', 0):>+6.1f}% {d.get('upside_pct', 0):>+5.1f}% {vol_label:<9} {s['pct_chg']:>+5.1f}% {s['signal']}{emoji}")
                            print(f"{'='*110}\n")

                            # 推送信号到微信: 中报池回踩形态≥65分推送
                            buy_signals = [s for s in tail_signals if s.get('total_score', 0) >= 65]
                            if buy_signals and time.time() - self.last_tail_entry_scan_time >= 600:
                                lines = []
                                for s in buy_signals[:5]:
                                    d = s.get('detail', {})
                                    feats = []
                                    if d.get('weak_market'): feats.append('⚠弱市')
                                    if d.get('rise_gap_vwap', 0) < 0: feats.append(f"贴VWAP{d['rise_gap_vwap']:+.1f}%")
                                    vol_label = str(d.get('tail_vol_label', ''))
                                    if vol_label.startswith('缩量'): feats.append(f"尾盘{vol_label}")
                                    if d.get('chip_conc', 0) > 50: feats.append(f"筹码{d['chip_conc']:.0f}%")
                                    feat_str = ' '.join(feats) if feats else ''
                                    lines.append(f"{s['signal']} {s['name']}({s['ts_code']}) 总分{s['total_score']} 量化{d.get('quant_score', 0):.1f}/二次{d.get('realtime_score', 0)} 乖离VWAP{d.get('rise_gap_vwap', 0):+.1f}% MA20{d.get('rise_gap_ma20', 0):+.1f}% 空间{d.get('upside_pct', 0):+.1f}% [{d.get('buy_point', '')}] 涨{s['pct_chg']:+.1f}% 止损{d.get('stop_loss', 0):.2f} {feat_str}")
                                content = f"🎯 「猎尾V4」中报池回踩买入信号 [{now.strftime('%H:%M')}]\n" + "\n".join(lines)
                                self.send_wechat(f"🎯 猎尾V4回踩 {now.strftime('%H:%M')}", content)

                        self.last_tail_entry_scan_time = time.time()

                # ── 14:50后每3分钟扫描「猎尾V5」ND2次日Alpha ──
                if now.hour == 14 and now.minute >= 50:
                    # 扫描前先回填昨日快照标签(每个交易日仅一次), 让概率引擎持续学习
                    if not getattr(self, '_nd2_backfill_done', False):
                        self._nd2_backfill_done = True
                        try:
                            filled = self.backfill_nd2_labels_job()
                            if filled:
                                print(f"✅ [V5] ND2标签回填完成: {filled}只")
                                self.nd2_engine.nd2.stats.reset()  # 刷新历史统计缓存
                        except Exception as e:
                            print(f"⚠ ND2标签回填异常: {e}")
                    if time.time() - self.nd2_last_scan_time >= 180:
                        try:
                            self.run_nd2_alpha_scan(now)
                        except Exception as e:
                            print(f"⚠ V5 ND2扫描异常: {e}")
                        self.nd2_last_scan_time = time.time()

                time.sleep(60 - (datetime.now().second % 60))

        except KeyboardInterrupt:
            print("\n🛑 监控已停止")
        finally:
            self.disconnect()

    def is_trading_time(self, dt):
        """判断是否在交易时段内"""
        if dt.weekday() >= 5:
            return False
        h, m = dt.hour, dt.minute
        if (h == 9 and m >= 30) or (h == 10) or (h == 11 and m <= 30):
            return True
        if h == 11 and m > 30:
            return False
        if h == 12:
            return False
        if h == 13 or h == 14:
            return True
        if h == 15:
            return False
        return False

    # ════════════════════════════════════════════
    # 中军弱转强尾盘买入算法
    # ════════════════════════════════════════════

    # ── 分时快照采集 ──
    def collect_intraday_snapshot(self, phase):
        """采集分时快照:phase='morning'(10:30) / 'noon'(14:00) / 'tail'(14:30后)"""
        for ts_code, q in self.quotes.items():
            if not q:
                continue
            snap = self.intraday_snapshots.setdefault(ts_code, {})
            pct = q.get('pct_chg', 0)
            price = q.get('price', 0)
            amount = q.get('amount', 0)
            vol = q.get('vol', 0)
            low = q.get('low', 0)

            if phase == 'morning':
                # 累积早盘数据
                snap['morning_pct'] = pct
                snap['morning_low'] = low
                snap['morning_amount'] = amount
                snap['morning_vol'] = vol
            elif phase == 'noon':
                # 14:00采集,记录午后增量
                snap['noon_pct'] = pct
                snap['noon_amount'] = amount
                snap['noon_vol'] = vol
            elif phase == 'tail':
                # 14:30后,记录尾盘基准
                snap['tail_base_pct'] = pct
                snap['tail_base_amount'] = amount
                snap['tail_base_vol'] = vol
                snap['tail_base_price'] = price

    # ── 硬过滤排除条件 ──
    def _w2s_hard_filter(self, ts_code, theme_name, q):
        """弱转强硬过滤:返回True=通过,False=排除"""
        # 1. 仅中军
        stocks = self.theme_stocks.get(theme_name, [])
        layer = None
        for code, name, ly in stocks:
            if code == ts_code:
                layer = ly
                break
        if layer != 'middle':
            return False, '非中军'

        # 2. 排除北交所
        if ts_code.startswith(('8', '4', '92')):
            return False, '北交所'

        # 3. 放量破位:跌幅>3%且量比>1.5
        pct = q.get('pct_chg', 0)
        if pct < -3:
            return False, '放量下跌'

        # 4. 距MA20检查
        kl = self.stock_klines.get(ts_code)
        if kl is not None and len(kl) >= 20:
            ma20 = kl['close'].iloc[-20:].mean()
            price = q.get('price', 0)
            if price > 0 and price < ma20 * 0.95:
                return False, '跌破MA20'

        # 5. 主题退潮检查
        lc = self.theme_lifecycle_cache.get(theme_name)
        if lc and lc[0] == '退潮期':
            return False, '主题退潮'

        return True, 'OK'

    # ── 分时形态评分(40分) ──
    def _w2s_pattern_score(self, ts_code, q):
        """分时形态:早弱+午稳+尾拉+均线突破+不破低点"""
        snap = self.intraday_snapshots.get(ts_code, {})
        score = 0
        detail = {}

        morning_pct = snap.get('morning_pct', 0)
        noon_pct = snap.get('noon_pct', 0)
        current_pct = q.get('pct_chg', 0)
        morning_low = snap.get('morning_low', 0)
        current_low = q.get('low', 0)
        tail_base_pct = snap.get('tail_base_pct', current_pct)
        current_price = q.get('price', 0)

        # 1. 早盘弱势(10分):早盘最低涨幅<-2%
        if morning_pct < -2:
            score += 10
            detail['early_weak'] = True
        elif morning_pct < 0:
            score += 5
            detail['early_weak'] = 'partial'

        # 2. 午后企稳(8分):午后均价>早盘均价
        if noon_pct > morning_pct and noon_pct > -1:
            score += 8
            detail['noon_stable'] = True

        # 3. 尾盘拉升(12分):14:30后涨幅扩大>=1.5%
        tail_rally = current_pct - tail_base_pct
        if tail_rally >= 1.5:
            score += 12
            detail['tail_rally'] = round(tail_rally, 2)
        elif tail_rally >= 0.8:
            score += 7
            detail['tail_rally'] = round(tail_rally, 2)

        # 4. 分时均价突破(6分):用成交额/量估算均价
        amount = q.get('amount', 0)
        vol = q.get('vol', 0)
        if vol > 0:
            avg_price = amount / vol
            if current_price > avg_price:
                score += 6
                detail['above_vwap'] = True

        # 5. 不破早盘低点(4分)
        if morning_low > 0 and current_low >= morning_low:
            score += 4
            detail['low_held'] = True

        # 排除:全天阴跌尾盘拉(诱多陷阱)
        if current_pct > 0 and morning_pct < -3 and noon_pct < morning_pct:
            score = min(score, 15)
            detail['trap_warning'] = '全天阴跌尾盘拉'

        return min(score, 40), detail

    # ── 量价配合评分(35分) ──
    def _w2s_volume_score(self, ts_code, q):
        """量价配合:尾盘量比放大+缩量回调+放量拉升+换手率"""
        snap = self.intraday_snapshots.get(ts_code, {})
        score = 0
        detail = {}

        tail_base_vol = snap.get('tail_base_vol', 0)
        current_vol = q.get('vol', 0)
        morning_vol = snap.get('morning_vol', 0)

        # 1. 尾盘量能放大(12分):14:30后量能增量占早盘量的比例
        if tail_base_vol > 0 and morning_vol > 0 and current_vol > tail_base_vol:
            tail_increment = current_vol - tail_base_vol
            # 尾盘增量 / 早盘总量(早盘约2小时,尾盘半小时)
            # 合理预期:尾盘半小时增量是早盘2小时的15%-25%
            tail_vol_ratio = tail_increment / morning_vol
            if tail_vol_ratio > 0.25:
                score += 12
                detail['tail_vol_ratio'] = round(tail_vol_ratio, 2)
            elif tail_vol_ratio > 0.15:
                score += 9
                detail['tail_vol_ratio'] = round(tail_vol_ratio, 2)
            elif tail_vol_ratio > 0.08:
                score += 5
                detail['tail_vol_ratio'] = round(tail_vol_ratio, 2)

        # 2. 缩量回调特征(10分):当日量<5日均量*0.9
        kl = self.stock_klines.get(ts_code)
        if kl is not None and len(kl) >= 5:
            recent_5vol = kl['vol'].iloc[-5:].mean()
            if recent_5vol > 0:
                vol_ratio_5d = current_vol / recent_5vol
                if vol_ratio_5d < 0.9:
                    score += 10
                    detail['shrink_vol'] = round(vol_ratio_5d, 2)
                elif vol_ratio_5d < 1.0:
                    score += 5
                    detail['shrink_vol'] = round(vol_ratio_5d, 2)
                elif vol_ratio_5d > 1.2:
                    # 放量但未大涨(主力收集筹码),给部分分
                    if q.get('pct_chg', 0) < 3:
                        score += 4
                        detail['vol_surge_low_pct'] = round(vol_ratio_5d, 2)

        # 3. 尾盘放量拉升(8分):14:30后量>早盘量*10%(简化阈值)
        if tail_base_vol > 0 and morning_vol > 0:
            tail_total = current_vol - tail_base_vol
            if tail_total > morning_vol * 0.15:
                score += 8
                detail['tail_vol_surge'] = True
            elif tail_total > morning_vol * 0.08:
                score += 4

        # 4. 换手率合理(5分):3%-8%
        turn_rate = self.turnover_cache.get(ts_code, 0)
        if turn_rate == 0:
            # 无换手率数据时,用成交额/流通市值估算(简化:给3分默认值)
            score += 3
            detail['turn_rate'] = 'unknown'
        elif 3 <= turn_rate <= 8:
            score += 5
            detail['turn_rate'] = turn_rate
        elif 2 <= turn_rate <= 12:
            score += 3
            detail['turn_rate'] = turn_rate
        elif 1 <= turn_rate <= 15:
            score += 1
            detail['turn_rate'] = turn_rate

        return min(score, 35), detail

    # ── 主题共振评分(25分) ──
    def _w2s_theme_score(self, ts_code, theme_name):
        """主题共振:主题Alpha+龙头未涨停+板块联动"""
        score = 0
        detail = {}

        # 1. 主题Alpha>=65(10分)
        lc = self.theme_lifecycle_cache.get(theme_name)
        if lc:
            stage, lifecycle_score, lc_detail = lc
            if lifecycle_score >= 75:
                score += 10
            elif lifecycle_score >= 65:
                score += 7
            elif lifecycle_score >= 50:
                score += 4
            detail['theme_lifecycle'] = f"{stage}({lifecycle_score})"

        # 2. 龙头未涨停(8分):龙头有空间→中军有跟风空间
        stocks = self.theme_stocks.get(theme_name, [])
        leader_zt = False
        for code, name, layer in stocks:
            if layer != 'leader':
                continue
            q = self.quotes.get(code)
            if q:
                lp = q.get('pct_chg', 0)
                zt_threshold = 19.5 if code.startswith(('300', '688')) else 9.5
                if lp >= zt_threshold:
                    leader_zt = True
                    break
        if not leader_zt:
            score += 8
            detail['leader_not_zt'] = True

        # 3. 板块联动(7分):主题内上涨占比>60%
        up_count = 0
        total = 0
        for code, name, layer in stocks:
            q = self.quotes.get(code)
            if q:
                total += 1
                if q.get('pct_chg', 0) > 0:
                    up_count += 1
        if total > 0:
            up_ratio = up_count / total
            if up_ratio > 0.6:
                score += 7
            elif up_ratio > 0.5:
                score += 4
            detail['theme_up_ratio'] = round(up_ratio, 2)

        return min(score, 25), detail

    # ── 中军弱转强主入口 ──
    def scan_middle_w2s(self):
        """
        中军弱转强扫描(14:30后每分钟运行)
        返回: 弱转强信号列表,按总分排序
        """
        now = datetime.now()
        signals = []
        # 首次扫描输出诊断统计
        if not self.w2s_debug_printed:
            debug_stats = {'total_middle': 0, 'no_quote': 0, 'hardfilter_fail': {}, 'score_dist': {'<55': 0, '55-64': 0, '65-74': 0, '>=75': 0}}
            max_scores = []

        # 遍历所有主题的中军股票
        for theme_name, stocks in self.theme_stocks.items():
            for ts_code, name, layer in stocks:
                if layer != 'middle':
                    continue

                if not self.w2s_debug_printed:
                    debug_stats['total_middle'] += 1

                q = self.quotes.get(ts_code)
                if not q:
                    if not self.w2s_debug_printed:
                        debug_stats['no_quote'] += 1
                    continue

                # 硬过滤
                passed, reason = self._w2s_hard_filter(ts_code, theme_name, q)
                if not passed:
                    if not self.w2s_debug_printed:
                        debug_stats['hardfilter_fail'][reason] = debug_stats['hardfilter_fail'].get(reason, 0) + 1
                    continue

                # 三维度评分
                pattern_score, pattern_detail = self._w2s_pattern_score(ts_code, q)
                vol_score, vol_detail = self._w2s_volume_score(ts_code, q)
                theme_score, theme_detail = self._w2s_theme_score(ts_code, theme_name)

                total_score = pattern_score + vol_score + theme_score

                if not self.w2s_debug_printed:
                    max_scores.append(total_score)
                    if total_score < 55:
                        debug_stats['score_dist']['<55'] += 1
                    elif total_score < 65:
                        debug_stats['score_dist']['55-64'] += 1
                    elif total_score < 75:
                        debug_stats['score_dist']['65-74'] += 1
                    else:
                        debug_stats['score_dist']['>=75'] += 1

                if total_score < 55:
                    continue

                # 信号分级
                if total_score >= 75:
                    signal = '强买入'
                elif total_score >= 65:
                    signal = '关注'
                else:
                    signal = '观望'

                signals.append({
                    'ts_code': ts_code,
                    'name': name,
                    'theme': theme_name,
                    'total_score': total_score,
                    'pattern_score': pattern_score,
                    'vol_score': vol_score,
                    'theme_score': theme_score,
                    'signal': signal,
                    'pct_chg': q.get('pct_chg', 0),
                    'price': q.get('price', 0),
                    'detail': {**pattern_detail, **vol_detail, **theme_detail}
                })

        # 首次扫描输出诊断
        if not self.w2s_debug_printed:
            self.w2s_debug_printed = True
            print(f"\n{'='*70}")
            print(f"🔍 弱转强首次扫描诊断 [{now.strftime('%H:%M:%S')}]")
            print(f"  中军总数: {debug_stats['total_middle']}")
            print(f"  无行情: {debug_stats['no_quote']}")
            print(f"  硬过滤拦截:")
            for r, cnt in sorted(debug_stats['hardfilter_fail'].items(), key=lambda x: -x[1]):
                print(f"    {r}: {cnt}只")
            passed = debug_stats['total_middle'] - debug_stats['no_quote'] - sum(debug_stats['hardfilter_fail'].values())
            print(f"  通过硬过滤: {passed}只")
            print(f"  分数分布: {debug_stats['score_dist']}")
            if max_scores:
                print(f"  最高分: {max(max_scores)}  平均分: {sum(max_scores)/len(max_scores):.1f}")
            print(f"  换手率缓存: {len(self.turnover_cache)}只")
            print(f"  分时快照: {len(self.intraday_snapshots)}只")
            print(f"{'='*70}\n")

        signals.sort(key=lambda x: x['total_score'], reverse=True)
        return signals

    # ════════════════════════════════════════════
    # 「猎尾」2:50尾盘突袭战法
    # ════════════════════════════════════════════
    def _tail_hard_filter(self, ts_code, q):
        """
        尾盘突袭硬过滤: 返回 (True/False, reason)
        排除: 涨停/跌停/振幅>8%/收盘跌>2.5%/不在主题/连板≥2
        """
        pct = q.get('pct_chg', 0)
        high = q.get('high', 0)
        low = q.get('low', 0)
        last_close = q.get('last_close', 0)
        price = q.get('price', 0)

        # 1. 涨停/跌停排除
        limit_up = 19.5 if ts_code.startswith(('300', '688')) else 9.5
        if pct >= limit_up:
            return False, '涨停'
        if pct <= -9.5:
            return False, '跌停'

        # 2. 振幅>8%排除 (太妖,次日方向不确定)
        if last_close > 0 and high > 0 and low > 0:
            amplitude = (high - low) / last_close * 100
            if amplitude > 8:
                return False, f'振幅{amplitude:.1f}%'

        # 3. 收盘跌>2.5%排除 (弱势,次日大概率继续跌)
        if pct < -2.5:
            return False, f'跌{pct:.1f}%'

        # 3.5 收阴线或微涨<1%排除(尾盘战法要求阳线实体,次日惯性高开概率更高)
        if pct < 1.0:
            return False, f'涨幅{pct:.1f}%过低'

        # 4. 不在任何主题中排除
        themes = self.stock_themes.get(ts_code, [])
        if not themes:
            return False, '无主题'

        # 5. 连续涨停≥2天排除 (已过度拉伸,次日追高风险大)
        kl = self.stock_klines.get(ts_code)
        if kl is not None and len(kl) >= 3:
            # 检查昨日和前天是否涨停
            prev_pct = float(kl['pct_chg'].iloc[-1]) if 'pct_chg' in kl.columns else 0
            prev2_pct = float(kl['pct_chg'].iloc[-2]) if 'pct_chg' in kl.columns else 0
            prev_limit = 19.5 if ts_code.startswith(('300', '688')) else 9.5
            if prev_pct >= prev_limit and prev2_pct >= prev_limit:
                return False, '连板2天'

        # 6. 距MA20太远(>25%)排除,高位风险
        if kl is not None and len(kl) >= 20:
            ma20 = kl['close'].iloc[-20:].mean()
            if price > 0 and price > ma20 * 1.25:
                return False, '距MA20>25%'

        # 7. 近5日涨幅>15%排除 (高位滞涨,尾盘拉升多为出货)
        if kl is not None and len(kl) >= 6:
            close_5d_ago = float(kl['close'].iloc[-6]) if len(kl) >= 6 else float(kl['close'].iloc[0])
            if close_5d_ago > 0:
                gain_5d = (price - close_5d_ago) / close_5d_ago * 100
                if gain_5d > 15:
                    return False, f'5日涨{gain_5d:.1f}%'

        # 8. 换手率异常排除 (>15%对倒出货嫌疑, <0.5%流动性差易操纵)
        turn_rate = self.turnover_cache.get(ts_code, 0)
        if turn_rate > 0:
            if turn_rate > 15:
                return False, f'换手{turn_rate:.1f}%过高'
            if turn_rate < 0.5:
                return False, f'换手{turn_rate:.1f}%过低'

        # 9. 主题强度<-1排除 (主题退潮期的尾盘拉升多为诱多)
        themes = self.stock_themes.get(ts_code, [])
        if themes:
            best_strength = max(
                (self.theme_score_history[t][-1] if self.theme_score_history.get(t) else 0)
                for t in themes
            )
            if best_strength < -1:
                return False, '主题退潮'

        # 10. 总市值<8亿排除 (小盘股易被操纵,尾盘拉升多为游资诱多)
        total_mv = self.stock_mv.get(ts_code, 0)
        if total_mv > 0:
            # total_mv 单位万元, 8亿=80000万
            if total_mv < 80000:
                return False, f'市值{total_mv/10000:.1f}亿'

        return True, 'OK'

    def _tail_trend_consistency_score(self, ts_code, q):
        """趋势一致性(10分): MA5>MA10 + MA10>MA20 + Close>MA20"""
        score = 0
        detail = {}
        price = q.get('price', 0)
        kl = self.stock_klines.get(ts_code)
        if kl is None or len(kl) < 20:
            return 0, detail
        ma5 = kl['close'].iloc[-5:].mean()
        ma10 = kl['close'].iloc[-10:].mean()
        ma20 = kl['close'].iloc[-20:].mean()
        if ma5 > ma10:
            score += 3
            detail['ma5_gt_ma10'] = True
        else:
            detail['ma5_gt_ma10'] = False
        if ma10 > ma20:
            score += 3
            detail['ma10_gt_ma20'] = True
        else:
            detail['ma10_gt_ma20'] = False
        if price > ma20:
            score += 4
            detail['close_gt_ma20'] = True
        else:
            detail['close_gt_ma20'] = False
        return min(score, 10), detail

    def _tail_relative_strength_score(self, ts_code, q, theme_name, index_pct):
        """相对强度(15分): RS=个股涨幅-主题均涨幅, Alpha=个股涨幅-指数涨幅"""
        pct = q.get('pct_chg', 0)
        score = 0
        detail = {}
        # RS vs 主题(8分)
        theme_avg_pct = 0
        if theme_name:
            stocks = self.theme_stocks.get(theme_name, [])
            gains = []
            for code, name, ly in stocks:
                qt = self.quotes.get(code)
                if qt and qt.get('pct_chg') is not None:
                    gains.append(qt.get('pct_chg', 0))
            theme_avg_pct = sum(gains) / len(gains) if gains else 0
        rs = pct - theme_avg_pct
        detail['rs_vs_theme'] = round(rs, 2)
        if rs > 3:
            score += 8
        elif rs > 2:
            score += 6
        elif rs > 1:
            score += 4
        elif rs > 0:
            score += 2
        # Alpha vs 指数(7分)
        if index_pct is not None:
            alpha = pct - index_pct
            detail['alpha_vs_index'] = round(alpha, 2)
            if alpha > 3:
                score += 7
            elif alpha > 2:
                score += 5
            elif alpha > 1:
                score += 3
        else:
            detail['alpha_vs_index'] = 0
        return min(score, 15), detail

    def _tail_breakout_score(self, ts_code, q):
        """新高突破(8分): Close突破10日高=资金流代理变量"""
        score = 0
        detail = {}
        price = q.get('price', 0)
        kl = self.stock_klines.get(ts_code)
        if kl is None or len(kl) < 20:
            return 0, detail
        high_10d = kl['high'].iloc[-10:].max()
        high_20d = kl['high'].iloc[-20:].max()
        if price > high_10d:
            score += 8
            detail['breakout_10d'] = True
        elif price > 0 and high_20d > 0:
            dist_20d = (high_20d - price) / price * 100
            detail['dist_20d_high'] = round(dist_20d, 2)
            if dist_20d < 2:
                score += 5
                detail['near_20d_high'] = True
        else:
            detail['breakout_10d'] = False
        return min(score, 8), detail

    def _tail_volatility_penalty(self, ts_code, q):
        """波动率扣分(≤10分): ATR过大扣分"""
        penalty = 0
        detail = {}
        fdf = self.stock_factors.get(ts_code)
        if fdf is None or fdf.empty:
            return 0, detail
        try:
            row = fdf.iloc[-1]
            atr = float(row.get('atr_bfq', 0) or 0)
            close = float(row.get('close', 0) or 0)
            if atr > 0 and close > 0:
                atr_pct = atr / close * 100
                detail['atr_pct'] = round(atr_pct, 2)
                kl = self.stock_klines.get(ts_code)
                if kl is not None and len(kl) >= 20:
                    prices = kl['close'].iloc[-20:].values
                    amp_20d = (kl['high'].iloc[-20:].values - kl['low'].iloc[-20:].values) / prices
                    avg_atr_20d = float(amp_20d.mean() * 100)
                    detail['atr_20d_avg'] = round(avg_atr_20d, 2)
                    if avg_atr_20d > 0 and atr_pct > avg_atr_20d * 2:
                        penalty += 10
                        detail['vol_penalty'] = 'ATR过大'
                    elif avg_atr_20d > 0 and atr_pct > avg_atr_20d * 1.5:
                        penalty += 5
                        detail['vol_penalty'] = 'ATR偏高'
        except Exception:
            pass
        return min(penalty, 10), detail

    def _tail_trap_penalty(self, ts_code, q):
        """
        诱多风险扣分: 返回 (扣分, detail)
        识别"尾盘拉高次日低开"的典型诱多形态

        四大诱多红旗:
        1. 全天弱势+尾盘急拉 (-15分): 最危险的诱多形态
        2. 长下影线+尾盘拉回 (-10分): 盘中暴跌尾盘拉回,次日大概率低开
        3. 高位滞涨+尾盘偷袭 (-8分): 近期涨幅大但当日涨不动,尾盘偷袭
        4. 孤立拉升无配合 (-5分): 主题内无涨停配合,单只孤立异动
        """
        snap = self.intraday_snapshots.get(ts_code, {})
        penalty = 0
        detail = {}

        open_p = q.get('open', 0)
        close = q.get('price', 0)
        high = q.get('high', 0)
        low = q.get('low', 0)
        last_close = q.get('last_close', 0)
        pct = q.get('pct_chg', 0)
        tail_base_price = snap.get('tail_base_price', 0)

        # ── 红旗1: 全天弱势+尾盘急拉 (-15分) ──
        # 判断: 尾盘拉升>0.5% 但 全天下跌(close<open)
        if tail_base_price > 0 and close > 0 and open_p > 0:
            tail_rally = (close - tail_base_price) / tail_base_price * 100
            day_change = (close - open_p) / open_p * 100
            if tail_rally > 0.5 and day_change < -0.3:
                penalty += 15
                detail['trap_weak_day'] = f'全天{day_change:+.1f}%尾拉{tail_rally:+.1f}%'
            elif tail_rally > 0.3 and day_change < 0:
                penalty += 8
                detail['trap_weak_day'] = f'全天{day_change:+.1f}%尾拉{tail_rally:+.1f}%'

        # ── 红旗2: 长下影线+尾盘拉回 (-10分) ──
        # 判断: 下影线长度 > 实体2倍 且 收盘在上半区(尾盘拉回)
        if last_close > 0 and high > 0 and low > 0 and close > 0 and open_p > 0:
            body = abs(close - open_p)
            lower_shadow = min(open_p, close) - low
            upper_shadow = high - max(open_p, close)
            price_range = high - low
            if price_range > 0 and last_close > 0:
                # 下影线占振幅比例
                lower_ratio = lower_shadow / price_range
                body_ratio = body / price_range
                # 长下影线(>40%振幅) + 小实体(<30%振幅) = 盘中暴跌尾盘拉回
                if lower_ratio > 0.4 and body_ratio < 0.3:
                    penalty += 10
                    detail['trap_long_lower'] = f'下影{lower_ratio:.0%}实体{body_ratio:.0%}'
                # 上影线长(冲高回落) + 尾盘在下半区 = 次日大概率跌
                elif upper_shadow > body * 2 and close < (high + low) / 2:
                    penalty += 5
                    detail['trap_upper_shadow'] = True

        # ── 红旗3: 高位滞涨+尾盘偷袭 (-8分) ──
        # 判断: 近5日涨幅>8% 但 当日涨幅<1% 且 尾盘拉升
        kl = self.stock_klines.get(ts_code)
        if kl is not None and len(kl) >= 6:
            close_5d_ago = float(kl['close'].iloc[-6])
            if close_5d_ago > 0 and close > 0:
                gain_5d = (close - close_5d_ago) / close_5d_ago * 100
                if gain_5d > 8 and pct < 1 and tail_base_price > 0:
                    tail_rally = (close - tail_base_price) / tail_base_price * 100 if tail_base_price > 0 else 0
                    if tail_rally > 0.3:
                        penalty += 8
                        detail['trap_high_stall'] = f'5日{gain_5d:.1f}%今{pct:+.1f}%'

        # ── 红旗4: 孤立拉升无配合 (-5分) ──
        # 判断: 主题强度<1 且 主题内涨停=0
        themes = self.stock_themes.get(ts_code, [])
        if themes:
            best_strength = 0
            best_zt = 0
            for t in themes:
                strength = self.theme_score_history[t][-1] if self.theme_score_history.get(t) else 0
                zt_cnt = 0
                for code, name, ly in self.theme_stocks.get(t, []):
                    qt = self.quotes.get(code)
                    if qt:
                        limit = 19.5 if code.startswith(('300', '688')) else 9.5
                        if qt.get('pct_chg', 0) >= limit:
                            zt_cnt += 1
                if strength > best_strength:
                    best_strength = strength
                    best_zt = zt_cnt
            if best_strength < 1 and best_zt == 0:
                penalty += 5
                detail['trap_isolated'] = f'强度{best_strength:.1f}涨停0'

        return min(penalty, 30), detail

    def _tail_attack_score(self, ts_code, q):
        """尾盘攻击力 (20分): 尾盘量占全天比例 + 距最高价距离 + 尾盘拉升"""
        snap = self.intraday_snapshots.get(ts_code, {})
        score = 0
        detail = {}

        morning_vol = snap.get('morning_vol', 0)
        current_price = q.get('price', 0)
        current_vol = q.get('vol', 0)
        high = q.get('high', 0)

        # ── 1. 尾盘拉升幅度 (2分): 降低权重(尾盘拉升≠次日高开) ──
        tail_base_price = snap.get('tail_base_price', 0)
        if tail_base_price > 0 and current_price > 0:
            tail_rally = (current_price - tail_base_price) / tail_base_price * 100
            detail['tail_rally'] = round(tail_rally, 2)
            if tail_rally > 1.0:
                score += 2
            elif tail_rally > 0.5:
                score += 1
        else:
            detail['tail_rally'] = 0

        # ── 2. 尾盘量占全天比例 (10分): 核心攻击力指标 ──
        if current_vol > 0:
            tail_vol = snap.get('tail_vol', 0)
            tail_vol_ratio = tail_vol / current_vol * 100
            detail['tail_vol_ratio_pct'] = round(tail_vol_ratio, 1)
            if tail_vol_ratio > 35:
                score += 10
            elif tail_vol_ratio > 30:
                score += 8
            elif tail_vol_ratio > 25:
                score += 5
            elif tail_vol_ratio > 20:
                score += 2
        else:
            detail['tail_vol_ratio_pct'] = 0

        # ── 3. 距最高价距离 (8分): 收盘越接近最高价越强 ──
        if high > 0 and current_price > 0:
            dist_from_high = (high - current_price) / current_price * 100
            detail['dist_from_high'] = round(dist_from_high, 2)
            if dist_from_high < 0.3:
                score += 8
            elif dist_from_high < 0.6:
                score += 6
            elif dist_from_high < 1.0:
                score += 3
        else:
            detail['dist_from_high'] = 0

        return min(score, 20), detail

    def _tail_structure_score(self, ts_code, q):
        """全天结构质量 (25分): 阳线实体 + 缩量程度 + 连续性(3日连阳)"""
        high = q.get('high', 0)
        low = q.get('low', 0)
        last_close = q.get('last_close', 0)
        price = q.get('price', 0)
        pct = q.get('pct_chg', 0)
        vol = q.get('vol', 0)
        score = 0
        detail = {}

        # ── 1. 阳线实体 (10分): 收阳=多头占优 ──
        if pct > 2:
            score += 10
            detail['yang_line'] = True
        elif pct > 1:
            score += 7
            detail['yang_line'] = True
        elif pct > 0:
            score += 4
            detail['yang_line'] = True
        else:
            detail['yang_line'] = False

        # ── 2. 缩量程度 (10分): 缩量=洗盘结束,蓄力待发 ──
        kl = self.stock_klines.get(ts_code)
        if kl is not None and len(kl) >= 5:
            avg_vol_5d = kl['vol'].iloc[-5:].mean()
            if avg_vol_5d > 0:
                vol_ratio = vol / avg_vol_5d
                detail['vol_ratio_5d'] = round(vol_ratio, 2)
                if vol_ratio < 0.7:
                    score += 10  # 极度缩量: 浮筹清洗干净
                elif vol_ratio < 0.85:
                    score += 8
                elif vol_ratio < 1.0:
                    score += 5
                elif vol_ratio < 1.2:
                    score += 3
        else:
            detail['vol_ratio_5d'] = 0

        # ── 3. 连续性 (5分): 3日连阳+5, 2阳1阴+3 ──
        if kl is not None and len(kl) >= 3:
            closes = kl['close'].iloc[-3:].values
            opens = kl['open'].iloc[-3:].values if 'open' in kl.columns else None
            yang_count = 0
            for i in range(3):
                if opens is not None and closes[i] >= opens[i]:
                    yang_count += 1
                elif opens is None and i > 0 and closes[i] >= closes[i-1]:
                    yang_count += 1
            if opens is not None:
                if yang_count >= 3:
                    score += 5
                    detail['continuity'] = '3日连阳'
                elif yang_count >= 2:
                    score += 3
                    detail['continuity'] = '2阳1阴'
            detail['yang_days'] = yang_count

        return min(score, 25), detail

    def _tail_position_score(self, ts_code, q):
        """位置安全边际 (15分): 距MA5 + 距MA10 + 距20日高回撤"""
        price = q.get('price', 0)
        score = 0
        detail = {}

        kl = self.stock_klines.get(ts_code)
        if kl is None or len(kl) < 20:
            detail['ma5_dist'] = 0
            detail['ma10_dist'] = 0
            detail['pullback'] = 0
            return 3, detail  # 无K线数据给基础分

        ma5 = kl['close'].iloc[-5:].mean()
        ma10 = kl['close'].iloc[-10:].mean()
        high_20d = kl['high'].iloc[-20:].max()

        # ── 1. 距MA5 (5分): 在MA5附近=短线支撑有效 ──
        if price > 0 and ma5 > 0:
            ma5_dist = abs(price - ma5) / ma5 * 100
            detail['ma5_dist'] = round(ma5_dist, 1)
            if ma5_dist < 2:
                score += 5
            elif ma5_dist < 4:
                score += 3
            elif ma5_dist < 6:
                score += 1
        else:
            detail['ma5_dist'] = 0

        # ── 2. 距MA10 (5分): 在MA10上方=中期趋势健康 ──
        if price > 0 and ma10 > 0:
            ma10_ratio = price / ma10
            detail['ma10_ratio'] = round(ma10_ratio, 2)
            if 0.97 <= ma10_ratio <= 1.05:
                score += 5
            elif 0.94 <= ma10_ratio <= 1.08:
                score += 3
            else:
                score += 1
        else:
            detail['ma10_ratio'] = 0

        # ── 3. 距20日高回撤 (5分): 有回撤=有上涨空间 ──
        if price > 0 and high_20d > 0:
            pullback = (high_20d - price) / high_20d * 100
            detail['pullback'] = round(pullback, 1)
            if pullback > 5:
                score += 5
            elif pullback > 2:
                score += 3
            elif pullback > 0:
                score += 1
        else:
            detail['pullback'] = 0

        return min(score, 15), detail

    def _tail_technical_score(self, ts_code, q):
        """
        技术形态加分 (10分,基于stk_factor缓存)
        利用KDJ/RSI技术指标识别技术面健康度
        
        这是胜率提升的核心因子:
        - KDJ金叉: 短期动能向上
        - RSI 40-70健康区: 不超买不超卖,走势稳健
        """
        score = 0
        detail = {}

        fdf = self.stock_factors.get(ts_code)
        if fdf is None or fdf.empty:
            return 0, detail  # 无技术因子数据不加分

        # 取最新一行
        row = fdf.iloc[-1]

        # ── 1. KDJ金叉/位置 (5分) ──
        # KDJ金叉=K上穿D,非J上穿K; 低位金叉信号最强
        try:
            kdj_k = float(row.get('kdj_k', 50))
            kdj_d = float(row.get('kdj_d', 50))
            kdj_j = float(row.get('kdj_j', 50))
            detail['kdj_k'] = round(kdj_k, 1)
            detail['kdj_j'] = round(kdj_j, 1)

            # 金叉判定:需前后两天对比,K从下穿到上穿D
            if fdf is not None and len(fdf) >= 2:
                prev_k = float(fdf.iloc[-2].get('kdj_k', 50))
                prev_d = float(fdf.iloc[-2].get('kdj_d', 50))
                is_golden_cross = (prev_k <= prev_d) and (kdj_k > kdj_d)
                is_dead_cross = (prev_k >= prev_d) and (kdj_k < kdj_d)
            else:
                is_golden_cross = (kdj_k > kdj_d) and (kdj_k < 80)
                is_dead_cross = False

            if is_golden_cross:
                if kdj_k < 20:
                    score += 5  # 低位金叉:信号最强
                    detail['kdj'] = '低位金叉'
                elif kdj_k < 50:
                    score += 4  # 中低位金叉
                    detail['kdj'] = '金叉'
                elif kdj_k < 80:
                    score += 3  # 中位金叉:趋势转强
                    detail['kdj'] = '中位金叉'
                else:
                    score += 1  # 高位金叉:需谨慎
                    detail['kdj'] = '高位金叉'
            elif is_dead_cross:
                score += 0  # 死叉不加分
                detail['kdj'] = '死叉'
            elif kdj_j < 80 and kdj_j > 20:
                score += 2  # 无金叉但KDJ在健康区间
                detail['kdj'] = '健康'
            # J>80超买不加分
        except Exception:
            pass

        # ── 2. RSI健康度 (5分) ──
        try:
            rsi_6 = float(row.get('rsi_6', 50))
            rsi_12 = float(row.get('rsi_12', 50))
            detail['rsi_6'] = round(rsi_6, 1)
            # RSI 40-70 为健康区,不超买不超卖
            if 40 <= rsi_6 <= 70:
                score += 5
            elif 30 <= rsi_6 < 40:
                score += 3  # 接近超卖,有反弹空间
            elif 70 < rsi_6 <= 80:
                score += 2  # 偏强但未严重超买
        except Exception:
            pass

        return min(score, 10), detail

    def _calc_theme_momentum(self, theme_name):
        """
        V2 实时主题动量 (5个轻量指标, 2-5ms/主题)
        返回: (up_ratio, avg_return, leader_return, bullish_count, tail_momentum)
        全部基于盘中实时行情,不计算MACD/RSI/KDJ/生命周期/前瞻分
        """
        stocks = self.theme_stocks.get(theme_name, [])
        if not stocks:
            return 0, 0, 0, 0, None

        up_count = 0
        total_pct = 0
        leader_pct = 0
        leader_found = False
        bullish_score = 0
        valid_count = 0

        for ts_code, name, layer in stocks:
            q = self.quotes.get(ts_code)
            if not q or q.get('price', 0) <= 0:
                continue
            pct = q.get('pct_chg', 0)
            if pct is None:
                continue

            valid_count += 1
            total_pct += pct

            # 上涨家数
            if pct > 0:
                up_count += 1

            # 大涨股数量 (>7% +2, >5% +1)
            if pct > 7:
                bullish_score += 2
            elif pct > 5:
                bullish_score += 1

            # 龙头表现(取最强龙头,而非第一个)
            if layer == 'leader':
                if not leader_found:
                    leader_pct = pct
                    leader_found = True
                else:
                    leader_pct = max(leader_pct, pct)

        if valid_count == 0:
            return 0, 0, 0, 0, None

        # 上涨家数比例
        up_ratio = up_count / valid_count * 100

        # 平均涨幅
        avg_return = total_pct / valid_count

        # 大涨股评分(上限100)
        bullish_count = min(bullish_score, 100)

        # 尾盘动量: 14:30后主题平均涨跌幅
        tail_momentum = None
        now = datetime.now()
        if now.hour >= 14 or (now.hour == 14 and now.minute >= 30):
            # 如果有快照,用快照中的14:30基准价; 否则用当日开盘价近似
            tail_pct_sum = 0
            tail_valid = 0
            for ts_code, name, layer in stocks:
                q = self.quotes.get(ts_code)
                if not q or q.get('price', 0) <= 0:
                    continue
                snap = self.intraday_snapshots.get(ts_code, {})
                base_price = snap.get('tail_base_price', 0)
                if base_price <= 0:
                    base_price = q.get('open', 0)
                if base_price > 0:
                    tail_pct = (q['price'] - base_price) / base_price * 100
                    tail_pct_sum += tail_pct
                    tail_valid += 1
            if tail_valid > 0:
                tail_momentum = tail_pct_sum / tail_valid

        return up_ratio, avg_return, leader_pct, bullish_count, tail_momentum

    def _tail_v2_trade_score(self, up_ratio, avg_return, leader_return, bullish_count, tail_momentum):
        """
        V2 实时主题动量评分 (20分)
        与 tail_strategy.v2_trade_score 保持同步
        """
        score = 0
        detail = {
            'up_ratio': round(up_ratio, 1),
            'avg_return': round(avg_return, 2),
            'leader_return': round(leader_return, 2),
            'bullish_count': round(bullish_count, 1),
            'tail_momentum': round(tail_momentum, 2) if tail_momentum is not None else None,
        }

        # 1. 上涨家数比例 (7分)
        if up_ratio >= 80:
            score += 7
        elif up_ratio >= 60:
            score += 5
        elif up_ratio >= 40:
            score += 3
        elif up_ratio >= 20:
            score += 1

        # 2. 平均涨幅 (5分)
        if avg_return > 3:
            score += 5
        elif avg_return > 2:
            score += 4
        elif avg_return > 1:
            score += 3
        elif avg_return > 0:
            score += 1

        # 3. 龙头表现 (4分)
        if leader_return > 5:
            score += 4
        elif leader_return > 3:
            score += 3
        elif leader_return > 0:
            score += 2

        # 4. 大涨股数量 (2分)
        if bullish_count >= 80:
            score += 2
        elif bullish_count >= 50:
            score += 1

        # 5. 尾盘动量 (2分)
        if tail_momentum is not None:
            if tail_momentum > 1.0:
                score += 2
            elif tail_momentum > 0.5:
                score += 1

        return min(score, 20), detail

    def _tail_theme_score(self, ts_code, q, theme_rank, lifecycle_score, forward_score, layer):
        """主题共振 (20分): 主题排名 + 生命周期分 + 前瞻分 + 龙头地位"""
        score = 0
        detail = {}

        detail['theme_rank'] = theme_rank
        detail['lifecycle_score'] = lifecycle_score
        detail['forward_score'] = forward_score
        detail['layer'] = layer

        # ── 主题排名 (5分): 排名越靠前越强 ──
        if theme_rank <= 2:
            score += 5
        elif theme_rank <= 5:
            score += 3
        elif theme_rank <= 10:
            score += 1

        # ── 生命周期分 (8分): 生命周期阶段越健康越强 ──
        if lifecycle_score > 80:
            score += 8
        elif lifecycle_score > 60:
            score += 6
        elif lifecycle_score > 40:
            score += 4
        elif lifecycle_score > 20:
            score += 2

        # ── 前瞻分 (4分): T+1预测强度 ──
        if forward_score > 80:
            score += 4
        elif forward_score > 50:
            score += 3
        elif forward_score > 30:
            score += 1

        # ── 龙头地位 (3分): 龙头/中军/跟风 ──
        if layer == 'leader':
            score += 3
        elif layer == 'middle':
            score += 2
        elif layer == 'follower':
            score += 1

        return min(score, 20), detail

    def _init_tail_tracker(self):
        """初始化尾盘信号跟踪表(独立SQLite,便于后续回填分析)"""
        try:
            import sqlite3 as _sqlite3
            conn = _sqlite3.connect(self.tail_tracker_db, timeout=10.0)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS tail_signal_tracker (
                    signal_date   TEXT NOT NULL,
                    signal_time   TEXT,
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
                    trend_score   INTEGER,
                    rel_strength_score INTEGER,
                    breakout_score INTEGER,
                    vol_penalty   INTEGER,
                    pct_chg       REAL,
                    price         REAL,
                    detail_json   TEXT,
                    -- 跟踪回填字段(未来交易日盘后更新)
                    next_open     REAL,
                    next_close    REAL,
                    next_pct_chg  REAL,
                    next_high     REAL,
                    next_low      REAL,
                    next_5d_pct   REAL,
                    next_10d_pct  REAL,
                    max_gain      REAL,
                    max_drawdown  REAL,
                    exit_date     TEXT,
                    exit_price    REAL,
                    exit_reason   TEXT,
                    pnl           REAL,
                    status        TEXT DEFAULT 'pending',
                    note          TEXT,
                    updated_at    TEXT,
                    PRIMARY KEY (signal_date, ts_code)
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_tracker_date ON tail_signal_tracker(signal_date)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_tracker_status ON tail_signal_tracker(status)')
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"⚠ 尾盘信号跟踪表初始化失败: {e}")

    def _save_tail_signals_to_tracker(self, signals):
        """
        将尾盘信号写入跟踪表(同一交易日同一只股票覆盖,保留最新评分)

        实盘入表筛选条件(方案K,目标10~30只可操作信号):
        1. 总分 >= 88
        2. 无诱多风险扣分 (trap_penalty == 0)
        3. 技术分 >= 12 (MACD多头+KDJ健康+RSI健康区等共振)
        4. 排除北交所 (9xxxxx/4xxxxx)
        5. 每主题最多TOP2 (按总分降序,避免同主题过度集中)
        """
        if not signals:
            return
        try:
            import sqlite3 as _sqlite3
            import json as _json
            now = datetime.now()
            signal_date = self._get_last_trade_date()
            signal_time = now.strftime('%H:%M:%S')

            # ── 筛选条件 1~4 ──
            candidates = []
            for s in signals:
                # 基础门槛: 买入级及以上
                if s.get('signal') not in ('强买入', '买入'):
                    continue
                # 总分 >= 88
                if s.get('total_score', 0) < 88:
                    continue
                # 无诱多风险
                if s.get('trap_penalty', 0) != 0:
                    continue
                # 技术分 >= 12
                if s.get('tech_score', 0) < 12:
                    continue
                # 排除北交所
                code = s.get('ts_code', '')
                if code.startswith(('9', '4')):
                    continue
                candidates.append(s)

            # ── 筛选条件 5: 每主题TOP2 ──
            theme_groups = {}
            for s in candidates:
                theme = s.get('theme', '其他')
                theme_groups.setdefault(theme, []).append(s)

            final_signals = []
            for theme, stocks in theme_groups.items():
                # 按总分降序排序,取TOP2
                stocks_sorted = sorted(stocks, key=lambda x: -x.get('total_score', 0))
                final_signals.extend(stocks_sorted[:2])

            if not final_signals:
                print(f"[跟踪] 无信号满足筛选条件(>=88+无诱多+技>=12+排北交所+每主题TOP2)")
                return

            conn = _sqlite3.connect(self.tail_tracker_db, timeout=10.0)
            for s in final_signals:
                conn.execute('''
                    INSERT OR REPLACE INTO tail_signal_tracker (
                        signal_date, signal_time, ts_code, name, theme, signal,
                        total_score, attack_score, structure_score, position_score,
                        theme_score, tech_score, trap_penalty, trend_score,
                        rel_strength_score, breakout_score, vol_penalty,
                        pct_chg, price, detail_json,
                        next_open, next_close, next_pct_chg, next_high, next_low,
                        next_5d_pct, next_10d_pct, max_gain, max_drawdown,
                        exit_date, exit_price, exit_reason, pnl, status, note, updated_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?,
                        NULL, NULL, NULL, NULL, NULL,
                        NULL, NULL, NULL, NULL,
                        NULL, NULL, NULL, NULL, 'pending', NULL, ?
                    )
                ''', (
                    signal_date, signal_time, s['ts_code'], s.get('name', ''), s.get('theme', ''), s['signal'],
                    s['total_score'], s['attack_score'], s['structure_score'], s['position_score'],
                    s['theme_score'], s.get('tech_score', 0), s.get('trap_penalty', 0),
                    s.get('trend_score', 0), s.get('rel_strength_score', 0),
                    s.get('breakout_score', 0), s.get('vol_penalty', 0),
                    s.get('pct_chg', 0), s.get('price', 0), _json.dumps(s.get('detail', {}), ensure_ascii=False),
                    signal_time,
                ))
            conn.commit()
            conn.close()
            print(f"[跟踪] 已写入{len(final_signals)}只精选信号到跟踪表 (>=88+无诱多+技>=12+每主题TOP2, signal_date={signal_date})")
        except Exception as e:
            print(f"⚠ 尾盘信号写入跟踪表失败: {e}")

    def _save_tail_signals_to_tracker_v3(self, signals):
        """
        「猎尾V3」尾盘信号写入跟踪表(与V2同库,独立表 tail_signal_tracker_v3)

        V3入表筛选条件:
        1. 信号级别 >= 买入观察 (total_score >= 75)
        2. 排除北交所 (9xxxxx/4xxxxx)
        3. 每主题最多TOP3 (按总分降序)
        """
        if not signals:
            return
        try:
            import sqlite3 as _sqlite3
            import json as _json
            now = datetime.now()
            signal_date = self._get_last_trade_date()
            signal_time = now.strftime('%H:%M:%S')

            conn = _sqlite3.connect(self.tail_tracker_db, timeout=10.0)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS tail_signal_tracker_v3 (
                    signal_date   TEXT NOT NULL,
                    signal_time   TEXT,
                    ts_code       TEXT NOT NULL,
                    name          TEXT,
                    theme         TEXT,
                    signal        TEXT,
                    total_score   INTEGER,
                    theme_score   INTEGER,
                    capital_score INTEGER,
                    role_score    INTEGER,
                    technical_score INTEGER,
                    timing_score  INTEGER,
                    room_score    INTEGER,
                    risk_penalty  INTEGER,
                    role          TEXT,
                    buy_type      TEXT,
                    confidence    INTEGER,
                    next_day_expectation TEXT,
                    pct_chg       REAL,
                    price         REAL,
                    detail_json   TEXT,
                    next_open     REAL,
                    next_close    REAL,
                    next_pct_chg  REAL,
                    next_high     REAL,
                    next_low      REAL,
                    next_5d_pct   REAL,
                    next_10d_pct  REAL,
                    max_gain      REAL,
                    max_drawdown  REAL,
                    exit_date     TEXT,
                    exit_price    REAL,
                    exit_reason   TEXT,
                    pnl           REAL,
                    status        TEXT DEFAULT 'pending',
                    note          TEXT,
                    updated_at    TEXT,
                    PRIMARY KEY (signal_date, ts_code)
                )
            ''')
            # ── 筛选: >=75 + 排除北交所 ──
            candidates = []
            for s in signals:
                if s.get('total_score', 0) < 75:
                    continue
                code = s.get('ts_code', '')
                if code.startswith(('9', '4')):
                    continue
                candidates.append(s)

            # ── 每主题TOP3 ──
            theme_groups = {}
            for s in candidates:
                theme = s.get('theme', '其他')
                theme_groups.setdefault(theme, []).append(s)
            final_signals = []
            for theme, stocks in theme_groups.items():
                stocks_sorted = sorted(stocks, key=lambda x: -x.get('total_score', 0))
                final_signals.extend(stocks_sorted[:3])

            if not final_signals:
                print(f"[跟踪V3] 无信号满足筛选条件(>=75+排北交所+每主题TOP3)")
                conn.close()
                return

            for s in final_signals:
                conn.execute('''
                    INSERT OR REPLACE INTO tail_signal_tracker_v3 (
                        signal_date, signal_time, ts_code, name, theme, signal,
                        total_score, theme_score, capital_score, role_score,
                        technical_score, timing_score, room_score, risk_penalty,
                        role, buy_type, confidence, next_day_expectation,
                        pct_chg, price, detail_json,
                        next_open, next_close, next_pct_chg, next_high, next_low,
                        next_5d_pct, next_10d_pct, max_gain, max_drawdown,
                        exit_date, exit_price, exit_reason, pnl, status, note, updated_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?,
                        NULL, NULL, NULL, NULL, NULL,
                        NULL, NULL, NULL, NULL,
                        NULL, NULL, NULL, NULL, 'pending', NULL, ?
                    )
                ''', (
                    signal_date, signal_time, s['ts_code'], s.get('name', ''), s.get('theme', ''), s['signal'],
                    s['total_score'], s.get('theme_score', 0), s.get('capital_score', 0), s.get('role_score', 0),
                    s.get('technical_score', 0), s.get('timing_score', 0), s.get('room_score', 0), s.get('risk_penalty', 0),
                    s.get('role', ''), s.get('buy_type', ''), s.get('confidence', 0), s.get('next_day_expectation', ''),
                    s.get('pct_chg', 0), s.get('price', 0), _json.dumps(s.get('detail', {}), ensure_ascii=False),
                    signal_time,
                ))
            conn.commit()
            conn.close()
            print(f"[跟踪V3] 已写入{len(final_signals)}只信号到跟踪表V3 (>=75+排北交所+每主题TOP3, signal_date={signal_date})")
        except Exception as e:
            print(f"⚠ V3尾盘信号写入跟踪表失败: {e}")

    # ════════════════════════════════════════════
    # 猎尾V4: 中报优质股池最佳回踩 (学习 push_washout_recovery.py)
    # ════════════════════════════════════════════

    def _fetch_recovery_quotes(self, ts_codes):
        """按需补拉指定股票实时行情(新浪),合并进 self.quotes (中报池不在主题池内的股票用)"""
        need = [c for c in ts_codes if c not in self.quotes or not self.quotes.get(c)]
        if not need:
            return {}
        quote_map = {}
        for offset in range(0, len(need), 80):
            batch = need[offset:offset + 80]
            sina_list = []
            for code in batch:
                if code.endswith('.SH'):
                    sina_list.append('sh' + code.replace('.SH', ''))
                elif code.endswith('.SZ'):
                    sina_list.append('sz' + code.replace('.SZ', ''))
                else:
                    sina_list.append('sz' + code)  # 无后缀默认深市
            url = f"https://hq.sinajs.cn/list={','.join(sina_list)}"
            try:
                resp = requests.get(url, headers={
                    'Referer': 'https://finance.sina.com.cn',
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }, timeout=8)
                resp.encoding = 'gbk'
                for line in resp.text.strip().split('\n'):
                    line = line.strip()
                    if not line or '=' not in line:
                        continue
                    try:
                        var_part = line.split('=', 1)[1]
                        if var_part.count('"') < 2:
                            continue
                        fields = var_part.split('"')[1].split(',')
                        var_name = line.split('hq_str_')[1].split('=')[0]
                        if var_name.startswith('sz'):
                            ts_c = var_name[2:].zfill(6) + '.SZ'
                        elif var_name.startswith('sh'):
                            ts_c = var_name[2:].zfill(6) + '.SH'
                        else:
                            continue
                        if len(fields) < 32:
                            continue
                        open_px = float(fields[1])
                        prev_close = float(fields[2])
                        price = float(fields[3])
                        high = float(fields[4])
                        low = float(fields[5])
                        volume = float(fields[8])
                        amount = float(fields[9])
                        if prev_close > 0 and price > 0:
                            pct_chg = (price - prev_close) / prev_close * 100
                        else:
                            pct_chg = 0
                        quote_map[ts_c] = {
                            'price': price, 'pct_chg': round(pct_chg, 2),
                            'amount': amount, 'vol': int(volume),
                            'last_close': prev_close, 'open': open_px,
                            'high': high, 'low': low,
                        }
                    except (IndexError, ValueError):
                        continue
            except Exception as e:
                print(f"  ⚠ 中报池补拉行情失败: {e}")
        if quote_map:
            self.quotes.update(quote_map)
        return quote_map

    def scan_tail_recovery_v3(self):
        """
        「猎尾V4」中报优质股池最佳回踩 — 学习 push_washout_recovery.py 的精选逻辑

        股票池: report_daily/enhanced_timing_bull_all_*.csv (前一交易日收盘后生成)
        精选条件(沿用 push_washout_recovery.py):
            修正后胜率分级 in [S,A] + 洗盘修复分>=80 + 兑现冲击过滤✅ + 回踩确认✅
        14:50 实时二次确认(找"最佳回踩"):
            ① 今日回踩幅度   (30分): 相对昨收小回调-3%~0%满分(回踩到位), 追高大幅扣分
            ② 现价乖离MA20   (25分): 回踩区(0~+8%)满分, 破位MA20直接剔除
            ③ 现价乖离VWAP   (15分): 越贴近主力成本区(20日VWAP)越"回踩到位"
            ④ 尾盘量能       (15分): 缩量回踩确认(14:30尾盘增量<=0.15)满分
            ⑤ 至止盈位空间   (10分): 5%~20%满分, 空间不足剔除
            ⑥ 买点类型       ( 5分): 买点2(缩量回踩VWAP确认)优先
        综合分 = 前日量化择时分*0.55 + 实时二次确认*0.45
        信号: >=80强买入 >=70买入观察 >=65关注(推送线65)
        """
        import re as _re
        import pandas as pd

        report_dir = os.path.join(BASE_DIR, 'report_daily')
        files = [f for f in os.listdir(report_dir)
                 if f.startswith('enhanced_timing_bull_all_') and f.endswith('.csv')]
        if not files:
            print('⚠ 「猎尾V4」未找到 enhanced_timing_bull_all 报告,跳过中报池回踩扫描')
            return []
        files.sort(reverse=True)
        report_path = os.path.join(report_dir, files[0])
        _m = _re.search(r'enhanced_timing_bull_all_(\d{8})\.csv', files[0])
        report_date = _m.group(1) if _m else '未知'

        df = pd.read_csv(report_path, encoding='utf-8-sig')
        for c in ('洗盘修复分', '量化择时分', '修正后评分', '结构增强分',
                  '现价', 'VWAP', 'MA20', '筹码峰顶', '筹码集中度%',
                  'ATR动态止损价', 'ATR跟踪止盈价'):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)

        # ── 1. 精选: S/A + 修复>=80 + 无冲击 + 回踩确认 (沿用push逻辑) ──
        elite = df[
            df['修正后胜率分级'].isin(['S', 'A']) &
            (df['洗盘修复分'] >= 80) &
            df['兑现冲击过滤'].astype(str).str.contains('✅', na=False) &
            df['回踩确认'].astype(str).str.contains('✅', na=False)
        ].copy()
        if elite.empty:
            print('⚠ 「猎尾V4」中报池精选(S/A+修复>=80+回踩确认)为空,无信号')
            return []

        # ── 2. 补拉不在主题池内的精选股实时行情 ──
        missing = [str(r['代码']).strip() for _, r in elite.iterrows()
                   if str(r['代码']).strip() not in self.quotes]
        if missing:
            fetched = self._fetch_recovery_quotes(missing)
            if fetched:
                print(f"  ✅ 补拉中报池行情: {len(fetched)}只 (不在主题池)")

        # ── 3. 逐只 14:50 实时二次确认 ──
        now = datetime.now()
        index_q = self.quotes.get('000001.SH')
        index_pct = index_q.get('pct_chg', None) if index_q else None

        signals = []
        rejects = {}          # 诊断: 二次确认拦截原因分布
        no_quote_codes = []
        for _, r in elite.iterrows():
            ts_code = str(r['代码']).strip()
            name = str(r['名称']).strip()
            theme = str(r.get('主题', '')).strip() if pd.notna(r.get('主题')) else ''
            quant = float(r['量化择时分'])
            wash = float(r['洗盘修复分'])
            vwap = float(r['VWAP'])
            ma20 = float(r['MA20'])
            chip_peak = float(r['筹码峰顶'])
            chip_conc = float(r['筹码集中度%'])
            buy_point = str(r.get('推荐买点类型', '')) if pd.notna(r.get('推荐买点类型')) else ''
            stop_loss = float(r['ATR动态止损价'])
            target = float(r['ATR跟踪止盈价'])
            market_status = str(r.get('大盘状态', '')) if pd.notna(r.get('大盘状态')) else ''

            q = self.quotes.get(ts_code)
            if not q or q.get('price', 0) <= 0:
                no_quote_codes.append(ts_code)
                continue
            price = q.get('price', 0)
            pct = q.get('pct_chg', 0)

            rise_vwap = (price / vwap - 1) * 100 if vwap > 0 else 0
            ma20_gap = (price / ma20 - 1) * 100 if ma20 > 0 else 0
            upside = (target / price - 1) * 100 if price > 0 and target > 0 else 0
            prev_close = float(r['现价'])            # 昨收=CSV现价(生成日收盘)
            ret_pct = (price / prev_close - 1) * 100 if prev_close > 0 else 0  # 今日相对昨收回踩幅度

            # ── 硬性实时条件: 回踩未破位 + 不追高 + 空间足够 ──
            reject = None
            if ma20_gap < -3:
                reject = f'破位MA20({ma20_gap:+.1f}%)'
            elif rise_vwap < -3:
                reject = f'跌破成本区({rise_vwap:+.1f}%)'
            elif ret_pct < -4:
                reject = f'今日回踩过深({ret_pct:.1f}%)'
            elif ret_pct > 7:
                reject = f'今日追高({ret_pct:+.1f}%)'
            elif upside < 2:
                reject = f'止盈空间不足({upside:+.1f}%)'
            if reject:
                rejects.setdefault(reject, []).append(ts_code)
                continue

            # ── 二次确认评分(0-100) ──
            # ① 今日回踩幅度(30): 最佳回踩=相对昨收小回调/企稳(-3%~+2%), 追高大幅扣分
            if -3 <= ret_pct <= 0:
                s_ret = 30
            elif 0 < ret_pct <= 2:
                s_ret = 26
            elif 2 < ret_pct <= 5:
                s_ret = 16
            elif -4 <= ret_pct < -3:
                s_ret = 14
            elif ret_pct > 5:
                s_ret = 6
            else:
                s_ret = 4
            # ② 乖离MA20(25): 回踩区(0~+8%)满分, 深贴/远离均扣分
            if 0 <= ma20_gap < 8:
                s_ma20 = 25
            elif -3 <= ma20_gap < 0:
                s_ma20 = 18
            elif 8 <= ma20_gap < 12:
                s_ma20 = 16
            elif 12 <= ma20_gap < 18:
                s_ma20 = 10
            else:
                s_ma20 = 6
            # ③ 乖离VWAP(15): 越贴近主力成本区(20日VWAP)越"回踩到位"
            if 0 <= rise_vwap < 8:
                s_vwap = 15
            elif 8 <= rise_vwap < 15:
                s_vwap = 12
            elif 15 <= rise_vwap < 25:
                s_vwap = 8
            elif rise_vwap < 0:
                s_vwap = 10
            else:  # >=25
                s_vwap = 5
            # ④ 尾盘量能(15): 缩量回踩确认
            snap = self.intraday_snapshots.get(ts_code, {})
            noon_vol = snap.get('noon_vol', 0) or 0
            tail_base_vol = snap.get('tail_base_vol', 0) or 0
            if noon_vol > 0 and tail_base_vol >= noon_vol:
                tail_add = (tail_base_vol - noon_vol) / noon_vol  # 14:00→14:30半小时增量占比
                if tail_add <= 0.15:
                    s_vol = 15; vol_label = f'缩量{tail_add:.2f}'
                elif tail_add <= 0.25:
                    s_vol = 11; vol_label = f'温和{tail_add:.2f}'
                elif tail_add <= 0.40:
                    s_vol = 8; vol_label = f'微放{tail_add:.2f}'
                else:
                    s_vol = 4; vol_label = f'放量{tail_add:.2f}'
            else:
                s_vol = 8; vol_label = '无快照'
            # ⑤ 至止盈空间(10)
            if 5 <= upside <= 20:
                s_room = 10
            elif 2 <= upside < 5:
                s_room = 6
            elif upside > 20:
                s_room = 8
            else:
                s_room = 3
            # ⑥ 买点类型(5): 买点2(缩量回踩VWAP确认)优先
            if '买点2' in buy_point:
                s_buy = 5
            elif '买点1' in buy_point:
                s_buy = 3
            else:
                s_buy = 2

            rt_score = s_ret + s_ma20 + s_vwap + s_vol + s_room + s_buy
            weak_market = False
            if index_pct is not None and index_pct <= -1.5:
                rt_score -= 8
                weak_market = True
            rt_score = max(0, min(100, rt_score))

            # ── 综合分: 前日量化 55% + 实时二次确认 45% ──
            total = int(round(quant * 0.55 + rt_score * 0.45))
            if total >= 80:
                signal = '强买入'
            elif total >= 70:
                signal = '买入观察'
            else:
                signal = '关注'
            confidence = int(round(wash * 0.5 + rt_score * 0.5))

            signals.append({
                'ts_code': ts_code,
                'name': name,
                'theme': theme,
                'total_score': total,
                'quant_score': round(quant, 1),
                'realtime_score': rt_score,
                'wash_score': wash,
                'theme_score': 0, 'capital_score': 0, 'role_score': 0,
                'technical_score': 0, 'timing_score': 0, 'risk_penalty': 0,
                'gap_score': 0,
                'signal': signal,
                'role': 'recovery',
                'buy_type': 'PULLBACK_GAP',
                'confidence': confidence,
                'next_day_expectation': f'回踩{ret_pct:+.1f}% 乖离VWAP{rise_vwap:+.1f}% 空间{upside:+.1f}% 止损{stop_loss:.2f}',
                'pct_chg': pct,
                'price': price,
                'detail': {
                    'quant_score': round(quant, 1),
                    'realtime_score': rt_score,
                    'wash_score': wash,
                    'ret_pct': round(ret_pct, 2),
                    'rise_gap_vwap': round(rise_vwap, 2),
                    'rise_gap_ma20': round(ma20_gap, 2),
                    'upside_pct': round(upside, 1),
                    'buy_point': buy_point,
                    'tail_vol_label': vol_label,
                    'chip_peak': chip_peak,
                    'chip_conc': chip_conc,
                    'stop_loss': stop_loss,
                    'take_profit': target,
                    'market_status': market_status,
                    'index_pct': index_pct,
                    'report_date': report_date,
                    'weak_market': weak_market,
                },
                'explain': (f'中报优质股池{report_date} 评级{signal} 洗盘修复{wash:.0f}分 今日回踩{ret_pct:+.1f}% '
                            f'乖离VWAP{rise_vwap:+.1f}% MA20{ma20_gap:+.1f}% 至止盈{upside:+.1f}% [{buy_point}]'),
            })

        # 诊断输出(首次)
        if not self.tail_entry_debug_printed:
            self.tail_entry_debug_printed = True
            passed = len(signals)
            print(f"\n{'='*70}")
            print(f"🔍 「猎尾V4」中报池回踩扫描诊断 [{now.strftime('%H:%M:%S')}] 报告:{report_date}")
            print(f"  精选池: {len(elite)}只 (S/A+修复>=80+兑现过滤+回踩确认)")
            if no_quote_codes:
                print(f"  无实时行情: {len(no_quote_codes)}只 {no_quote_codes[:5]}")
            print(f"  二次确认拦截:")
            for r2, codes in sorted(rejects.items(), key=lambda x: -len(x[1])):
                print(f"    {r2}: {len(codes)}只 {codes[:5]}")
            print(f"  通过二次确认: {passed}只")
            if passed:
                print(f"  总分范围: {min(s['total_score'] for s in signals)}~{max(s['total_score'] for s in signals)}")
            print(f"{'='*70}\n")

        signals.sort(key=lambda x: x['total_score'], reverse=True)
        return signals

    def _save_tail_recovery_signals_to_tracker(self, signals):
        """
        「猎尾V4」中报池回踩信号写入跟踪表 tail_signal_tracker_v4
        入表筛选: total_score >= 65 + 排除北交所
        """
        if not signals:
            return
        try:
            import json as _json
            now = datetime.now()
            signal_date = self._get_last_trade_date()
            signal_time = now.strftime('%H:%M:%S')

            conn = sqlite3.connect(self.tail_tracker_db, timeout=10.0)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS tail_signal_tracker_v4 (
                    signal_date   TEXT NOT NULL,
                    signal_time   TEXT,
                    ts_code       TEXT NOT NULL,
                    name          TEXT,
                    theme         TEXT,
                    signal        TEXT,
                    total_score   INTEGER,
                    quant_score   REAL,
                    realtime_score INTEGER,
                    wash_score    REAL,
                    confidence    INTEGER,
                    next_day_expectation TEXT,
                    pct_chg       REAL,
                    price         REAL,
                    detail_json   TEXT,
                    next_open     REAL,
                    next_close    REAL,
                    next_pct_chg  REAL,
                    next_high     REAL,
                    next_low      REAL,
                    next_5d_pct   REAL,
                    next_10d_pct  REAL,
                    max_gain      REAL,
                    max_drawdown  REAL,
                    exit_date     TEXT,
                    exit_price    REAL,
                    exit_reason   TEXT,
                    pnl           REAL,
                    status        TEXT DEFAULT 'pending',
                    note          TEXT,
                    updated_at    TEXT,
                    PRIMARY KEY (signal_date, ts_code)
                )
            ''')
            candidates = []
            for s in signals:
                if s.get('total_score', 0) < 65:
                    continue
                code = s.get('ts_code', '')
                if code.startswith(('9', '4')):
                    continue
                candidates.append(s)
            if not candidates:
                print(f"[跟踪V4] 无信号满足筛选条件(>=65+排北交所)")
                conn.close()
                return
            for s in candidates:
                conn.execute('''
                    INSERT OR REPLACE INTO tail_signal_tracker_v4 (
                        signal_date, signal_time, ts_code, name, theme, signal,
                        total_score, quant_score, realtime_score, wash_score, confidence,
                        next_day_expectation, pct_chg, price, detail_json,
                        next_open, next_close, next_pct_chg, next_high, next_low,
                        next_5d_pct, next_10d_pct, max_gain, max_drawdown,
                        exit_date, exit_price, exit_reason, pnl, status, note, updated_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                        NULL, NULL, NULL, NULL, 'pending', NULL, ?
                    )
                ''', (
                    signal_date, signal_time, s['ts_code'], s.get('name', ''), s.get('theme', ''), s['signal'],
                    s['total_score'], s.get('quant_score', 0), s.get('realtime_score', 0), s.get('wash_score', 0), s.get('confidence', 0),
                    s.get('next_day_expectation', ''), s.get('pct_chg', 0), s.get('price', 0),
                    _json.dumps(s.get('detail', {}), ensure_ascii=False),
                    signal_time,
                ))
            conn.commit()
            conn.close()
            print(f"[跟踪V4] 已写入{len(candidates)}只信号到跟踪表V4 (>=65+排北交所, signal_date={signal_date})")
        except Exception as e:
            print(f"⚠ V4尾盘信号写入跟踪表失败: {e}")

    # ════════════════════════════════════════════
    # 猎尾V5: NEXT-DAY ALPHA ENGINE (ND2)
    # 14:50 扫描全主题池 -> 次日+2%概率引擎
    # ════════════════════════════════════════════

    def scan_nd2_alpha(self):
        """
        「猎尾V5」NEXT-DAY ALPHA ENGINE
        四层决策: L0市场环境 -> L1分层硬过滤 -> L2三形态分类 -> L3多维评分
        核心目标: 最大化 P(次日最高价 >= 买入价×1.02)
        返回: 信号列表(按rank_score降序)
        """
        import pandas as pd
        now = datetime.now()
        signals = []

        # ── L0 市场环境 ──
        report = getattr(self, '_last_report', None)
        trend_score = report.get('trend_score', 60) if report else 60
        market_status = report.get('market_status', '') if report else ''
        market_multiplier = ND2AlphaEngine.market_multiplier(trend_score, market_status)

        index_q = self.index_quotes_cache.get('上证指数')
        index_pct = index_q.get('pct_chg') if index_q else None

        # ── 主题数据预计算(复用V3扫描的全局缓存逻辑) ──
        theme_momentum_cache = {}
        for theme_name in self.theme_stocks:
            theme_momentum_cache[theme_name] = self._calc_theme_momentum(theme_name)

        theme_zt_counts = {}
        theme_leader_ret = {}
        for theme_name, stocks in self.theme_stocks.items():
            zt = 0
            mx = 0
            for ts_code, _, _ in stocks:
                q = self.quotes.get(ts_code)
                if q and q.get('price', 0) > 0:
                    pct = q.get('pct_chg', 0)
                    limit = 19.5 if ts_code.startswith(('300', '688')) else 9.5
                    if pct >= limit:
                        zt += 1
                    if pct > mx:
                        mx = pct
            theme_zt_counts[theme_name] = zt
            theme_leader_ret[theme_name] = mx

        # ── 遍历主题池(仅评估有分时锚点的股票: 尾盘量比是V5核心输入) ──
        total_scanned = 0
        funnel = defaultdict(int)   # 逐层漏斗诊断
        for ts_code, themes in self.stock_themes.items():
            if not themes:
                continue
            q = self.quotes.get(ts_code)
            if not q or q.get('price', 0) <= 0:
                continue
            snap = self.intraday_snapshots.get(ts_code)
            if not snap or snap.get('tail_base_price', 0) <= 0:
                funnel['无分时锚点'] += 1
                continue
            total_scanned += 1

            theme_name = themes[0]
            kl = self.stock_klines.get(ts_code)
            if kl is not None and len(kl) > 0:
                for _col in ('open', 'high', 'low', 'close', 'pct_chg', 'vol', 'amount'):
                    if _col in kl.columns:
                        kl[_col] = pd.to_numeric(kl[_col], errors='coerce').fillna(0)

            turnover = self.turnover_cache.get(ts_code, 0)
            total_mv = self.stock_mv.get(ts_code, 0) if self.stock_mv else 0

            # 主题上下文
            theme_strength = 0
            if theme_name in self.theme_score_history and self.theme_score_history[theme_name]:
                theme_strength = self.theme_score_history[theme_name][-1]
            up_ratio, avg_return, _, _, _ = theme_momentum_cache.get(theme_name, (0, 0, 0, 0, None))
            theme_limit_count = theme_zt_counts.get(theme_name, 0)
            leader_return = theme_leader_ret.get(theme_name, 0)

            # 股票名称
            stock_name = ''
            for code, n, _ in self.theme_stocks.get(theme_name, []):
                if code == ts_code:
                    stock_name = n
                    break

            q_with_name = dict(q)
            q_with_name['name'] = stock_name

            try:
                sig = self.nd2_engine.evaluate(
                    ts_code=ts_code,
                    q=q_with_name,
                    kline=kl,
                    snap=snap,
                    turnover=turnover,
                    total_mv=total_mv,
                    theme_name=theme_name,
                    theme_strength=theme_strength,
                    theme_up_ratio=up_ratio,
                    theme_limit_count=theme_limit_count,
                    theme_leader_pct=leader_return,
                    trend_score=trend_score,
                    market_status=market_status,
                    index_pct=index_pct,
                )
            except Exception:
                funnel['评估异常'] += 1
                continue

            if sig is None:
                funnel['L1/L2过滤'] += 1
                continue
            if sig.get('grade') == 'REJECT':
                funnel['REJECT(<65分)'] += 1
                continue
            signals.append(sig)

        # ── 排序: rank_score (禁止只按FinalScore) ──
        signals.sort(key=lambda s: -s.get('rank_score', 0))

        # ── 首次扫描诊断 ──
        if not self.nd2_debug_printed:
            self.nd2_debug_printed = True
            grade_dist = defaultdict(int)
            pattern_dist = defaultdict(int)
            for s in signals:
                grade_dist[s['grade']] += 1
                pattern_dist[s['pattern']] += 1
            print(f"\n{'='*70}")
            print(f"🔍 「猎尾V5」ND2首次扫描诊断 [{now.strftime('%H:%M:%S')}]")
            print(f"  扫描股票(有锚点): {total_scanned}只 | 市场乘数: {market_multiplier:.2f} ({market_status or 'N/A'})")
            print(f"  通过L1+L2+L3候选: {len(signals)}只")
            print(f"  漏斗: " + " | ".join(f"{k}={v}" for k, v in sorted(funnel.items(), key=lambda x: -x[1])))
            print(f"  形态分布: {dict(pattern_dist)}")
            print(f"  分级分布: {dict(grade_dist)}")
            if signals:
                print(f"  最高FinalScore: {max(s['final_score'] for s in signals)} "
                      f"| 最高rank: {max(s['rank_score'] for s in signals):.3f}")
            print(f"{'='*70}\n")

        return signals

    def run_nd2_alpha_scan(self, now):
        """V5扫描入口: 控制台报告 + 快照保存 + 微信推送
        精选层(V5Selector): 每日最多推送2只最佳信号, 非主升浪日自动空仓"""
        all_signals = self.scan_nd2_alpha()
        signal_date = self._get_last_trade_date()

        # 保存快照(全量>=60分, 供标签回填学习)
        if all_signals:
            saved = self.nd2_store.save_snapshot(
                signal_date, [s for s in all_signals if s['final_score'] >= 60],
                market_status=getattr(self, '_last_report', {}).get('market_status', '') if self._last_report else ''
            )
            if saved:
                print(f"📸 [V5] ND2快照已保存: {saved}只 -> nd2_snapshot.db")

        # ── V5Selector 精选: 每日最多2只最佳信号 ──
        signals = V5Selector.select(all_signals, max_per_day=2)

        # 控制台报告(精选信号)
        if signals:
            report_str = format_console_report(signals, top_n=2)
            print(report_str)
        elif all_signals:
            print(f"\n🛑 [V5] 今日{len(all_signals)}只候选均未通过精选(非主升浪/不在甜点区) → 空仓纪律")
        else:
            print(f"\n🛑 [V5] 今日无ND2候选 → 空仓")

        # 精选信号推送(每日最多推送一次)
        if signals and time.time() - self.nd2_pushed_time > 3600:
            msg = format_wechat_message(signals, max_n=2)
            if msg:
                mkt_info = {
                    'status': getattr(self, '_last_report', {}).get('market_status', '') if self._last_report else '',
                    'multiplier': ND2AlphaEngine.market_multiplier(
                        getattr(self, '_last_report', {}).get('trend_score', 60) if self._last_report else 60),
                    'trend_score': getattr(self, '_last_report', {}).get('trend_score', 0) if self._last_report else 0,
                }
                full_msg = msg + '\n' + format_wechat_market(mkt_info)
                self.send_wechat(f"🎯 猎尾V5精选·次日Alpha {now.strftime('%H:%M')}", full_msg)
                self.nd2_pushed_time = time.time()

        return signals

    def backfill_nd2_labels_job(self):
        """盘后任务: 回填历史ND2快照标签(优先本地缓存)"""
        try:
            from stock_cache import get_daily_cache
            trade_date = self._get_last_trade_date()
            pending = self.nd2_store.pending_backfill_dates(trade_date)
            if not pending:
                return 0
            total = 0
            for sig_date in pending[:10]:  # 每次最多回填10天
                # 下一交易日 = 快照日之后第一个有数据的交易日, 用缓存探测
                nxt = None
                probe = datetime.strptime(sig_date, '%Y%m%d')
                for _ in range(10):
                    probe = probe + timedelta(days=1)
                    cand = probe.strftime('%Y%m%d')
                    df = get_daily_cache('000001.SZ', cand, cand)
                    if df is not None and not df.empty:
                        nxt = cand
                        break
                if not nxt:
                    continue
                filled = self.nd2_store.backfill_labels(get_daily_cache, sig_date, nxt)
                total += filled
                if filled:
                    print(f"[V5] ND2标签回填 {sig_date} -> {nxt}: {filled}只")
            return total
        except Exception as e:
            print(f"⚠ ND2标签回填任务异常: {e}")
            return 0

    def scan_tail_end_entry(self):
        """
        「猎尾」2:50尾盘突袭战法 — 次日套利最高胜率

        14:50后扫描全市场主题内股票，识别尾盘抢筹信号。
        评分模型 (满分 = 133 - 扣分):
        - 全天结构   (25分): 阳线实体 + 缩量程度 + 连续性
        - 尾盘攻击力 (20分): 尾盘量占比 + 距最高价 + 尾盘拉升
        - 位置安全   (15分): 距MA5/MA10 + 距20日高回撤
        - 趋势一致性 (10分): MA5>MA10/MA10>MA20/Close>MA20
        - 主题共振   (20分): 主题排名 + 生命周期分 + 前瞻分 + 龙头地位
        - 相对强度   (15分): RS(相对主题) + Alpha(相对指数)
        - 新高突破   ( 8分): Close突破10日高/距20日高<2%
        - 技术形态   (10分): KDJ金叉 + RSI健康度
        - 波动率扣分 (≤10分): ATR过大扣分
        - 诱多扣分   (≤30分): 四大诱多红旗

        硬过滤: 涨停/跌停/振幅>8%/跌>2.5%/不在主题/连板≥2/距MA20>25%
        信号: ≥75强买入  ≥65买入  ≥50关注

        返回: 信号列表,按总分降序
        """
        now = datetime.now()
        signals = []

        # ── 全局数据预计算 ──
        # 1. 主题排名: 按最新分数排序生成 {theme_name: rank} 字典
        theme_rank_map = {}
        theme_scores = []
        for tname, scores in self.theme_score_history.items():
            if scores:
                theme_scores.append((tname, scores[-1]))
        theme_scores.sort(key=lambda x: -x[1])
        for rank, (tname, _) in enumerate(theme_scores, 1):
            theme_rank_map[tname] = rank

        # 2. 上证指数涨幅
        index_pct = None
        index_q = self.quotes.get('000001.SH')
        if index_q:
            index_pct = index_q.get('pct_chg', None)

        # 3. V2 实时主题动量: 预计算所有主题的5个轻量指标(2-5ms/主题)
        theme_momentum_cache = {}
        for theme_name in self.theme_stocks:
            theme_momentum_cache[theme_name] = self._calc_theme_momentum(theme_name)

        # 首次扫描输出诊断统计
        if not self.tail_entry_debug_printed:
            debug_stats = {
                'total_in_theme': 0, 'no_quote': 0,
                'hardfilter_fail': {}, 'score_dist': {'<50': 0, '50-64': 0, '65-74': 0, '>=75': 0},
                'max_scores': []
            }

        # 遍历所有在主题中的股票
        for ts_code, themes in self.stock_themes.items():
            if not themes:
                continue

            if not self.tail_entry_debug_printed:
                debug_stats['total_in_theme'] += 1

            q = self.quotes.get(ts_code)
            if not q or q.get('price', 0) <= 0:
                if not self.tail_entry_debug_printed:
                    debug_stats['no_quote'] += 1
                continue

            # 硬过滤
            passed, reason = self._tail_hard_filter(ts_code, q)
            if not passed:
                if not self.tail_entry_debug_printed:
                    debug_stats['hardfilter_fail'][reason] = debug_stats['hardfilter_fail'].get(reason, 0) + 1
                continue

            # 获取主题数据
            theme_name = themes[0]  # 取最强主题
            # 获取排名
            theme_rank = theme_rank_map.get(theme_name, 99)
            # 获取生命周期和前瞻分
            lc = self.theme_lifecycle_cache.get(theme_name)
            lifecycle_score = lc[1] if lc else 0
            forward_score = self.theme_forward_cache.get(theme_name, 0) if hasattr(self, 'theme_forward_cache') else 0
            # 获取layer
            layer = 'follower'
            for code, name, ly in self.theme_stocks.get(theme_name, []):
                if code == ts_code:
                    layer = ly
                    break

            # 多维评分
            attack_score, attack_detail = self._tail_attack_score(ts_code, q)
            structure_score, structure_detail = self._tail_structure_score(ts_code, q)
            position_score, position_detail = self._tail_position_score(ts_code, q)
            trend_score, trend_detail = self._tail_trend_consistency_score(ts_code, q)
            # V2: 实时主题动量替代旧版 _tail_theme_score
            up_ratio, avg_return, leader_return, bullish_count, tail_momentum = theme_momentum_cache.get(theme_name, (0, 0, 0, 0, None))
            theme_score, theme_detail = self._tail_v2_trade_score(up_ratio, avg_return, leader_return, bullish_count, tail_momentum)
            rel_score, rel_detail = self._tail_relative_strength_score(ts_code, q, theme_name, index_pct)
            breakout_score, breakout_detail = self._tail_breakout_score(ts_code, q)
            tech_score, tech_detail = self._tail_technical_score(ts_code, q)
            vol_penalty, vol_detail = self._tail_volatility_penalty(ts_code, q)
            trap_penalty, trap_detail = self._tail_trap_penalty(ts_code, q)

            total_score = (attack_score + structure_score + position_score + trend_score +
                           theme_score + rel_score + breakout_score + tech_score -
                           vol_penalty - trap_penalty)

            if not self.tail_entry_debug_printed:
                debug_stats['max_scores'].append(total_score)
                if total_score < 50:
                    debug_stats['score_dist']['<50'] += 1
                elif total_score < 65:
                    debug_stats['score_dist']['50-64'] += 1
                elif total_score < 75:
                    debug_stats['score_dist']['65-74'] += 1
                else:
                    debug_stats['score_dist']['>=75'] += 1

            if total_score < 50:
                continue

            # 信号分级
            if total_score >= 75:
                signal = '强买入'
            elif total_score >= 65:
                signal = '买入'
            else:
                signal = '关注'

            name = ''
            for theme, stocks in self.theme_stocks.items():
                for code, n, _ in stocks:
                    if code == ts_code:
                        name = n
                        break
                if name:
                    break

            theme_detail['theme'] = theme_name

            signals.append({
                'ts_code': ts_code,
                'name': name,
                'theme': theme_name,
                'total_score': total_score,
                'attack_score': attack_score,
                'structure_score': structure_score,
                'position_score': position_score,
                'theme_score': theme_score,
                'tech_score': tech_score,
                'trap_penalty': trap_penalty,
                'trend_score': trend_score,
                'rel_strength_score': rel_score,
                'breakout_score': breakout_score,
                'vol_penalty': vol_penalty,
                'signal': signal,
                'pct_chg': q.get('pct_chg', 0),
                'price': q.get('price', 0),
                'detail': {**attack_detail, **structure_detail, **position_detail, **trend_detail,
                           **theme_detail, **rel_detail, **breakout_detail, **tech_detail,
                           **vol_detail, **trap_detail, 'layer': layer},
            })

        # 首次扫描输出诊断
        if not self.tail_entry_debug_printed:
            self.tail_entry_debug_printed = True
            passed_count = debug_stats['total_in_theme'] - debug_stats['no_quote'] - sum(debug_stats['hardfilter_fail'].values())
            print(f"\n{'='*70}")
            print(f"🔍 「猎尾」尾盘突袭首次扫描诊断 [{now.strftime('%H:%M:%S')}]")
            print(f"  主题内股票总数: {debug_stats['total_in_theme']}")
            print(f"  无行情: {debug_stats['no_quote']}")
            print(f"  硬过滤拦截:")
            for r, cnt in sorted(debug_stats['hardfilter_fail'].items(), key=lambda x: -x[1]):
                print(f"    {r}: {cnt}只")
            print(f"  通过硬过滤: {passed_count}只")
            print(f"  分数分布: {debug_stats['score_dist']}")
            if debug_stats['max_scores']:
                print(f"  最高分: {max(debug_stats['max_scores'])}  平均分: {sum(debug_stats['max_scores'])/len(debug_stats['max_scores']):.1f}")
            print(f"  分时快照: {len(self.intraday_snapshots)}只")
            print(f"  换手率缓存: {len(self.turnover_cache)}只")
            print(f"{'='*70}\n")

        signals.sort(key=lambda x: x['total_score'], reverse=True)
        return signals

    # ═══════════════════════════════════════════════════════
    # V3 猎尾扫描: 主线资金驱动型多层决策系统
    # ═══════════════════════════════════════════════════════
    def scan_tail_end_entry_v3(self):
        """
        「猎尾V3」2:50尾盘突袭战法 — 多层交易决策系统

        评分模型:
        Final = Theme(30) + Capital(25) + Role(20) + Technical(15) + Timing(10)
                + TomorrowRoom(±5) - Risk(20)

        信号: ≥85强买入  ≥75买入观察  ≥65关注
        返回: 信号列表,按总分降序
        """
        from tail_strategy import TailStrategy
        from theme_engine_v3 import theme_score_v3
        from capital_engine_v3 import capital_score_v3
        from role_engine_v3 import calc_stock_role_score_from_layer

        import pandas as pd
        v3 = TailStrategy()
        now = datetime.now()
        signals = []

        # ── 全局数据预计算 ──
        # 1. 主题排名
        theme_rank_map = {}
        theme_scores = []
        for tname, scores in self.theme_score_history.items():
            if scores:
                theme_scores.append((tname, scores[-1]))
        theme_scores.sort(key=lambda x: -x[1])
        for rank, (tname, _) in enumerate(theme_scores, 1):
            theme_rank_map[tname] = rank

        # 2. 上证指数涨幅
        index_pct = None
        index_q = self.quotes.get('000001.SH')
        if index_q:
            index_pct = index_q.get('pct_chg', None)

        # 3. V2 实时主题动量: 预计算所有主题
        theme_momentum_cache = {}
        for theme_name in self.theme_stocks:
            theme_momentum_cache[theme_name] = self._calc_theme_momentum(theme_name)

        # 4. 计算每个主题的涨停数量 + 龙头强度(主题内最高涨幅, 无论配置层)
        theme_zt_counts = {}
        theme_leader_ret = {}
        for theme_name, stocks in self.theme_stocks.items():
            zt = 0
            mx = 0
            for ts_code, _, _ in stocks:
                q = self.quotes.get(ts_code)
                if q and q.get('price', 0) > 0:
                    pct = q.get('pct_chg', 0)
                    limit = 19.5 if ts_code.startswith(('300', '688')) else 9.5
                    if pct >= limit:
                        zt += 1
                    if pct > mx:
                        mx = pct
            theme_zt_counts[theme_name] = zt
            theme_leader_ret[theme_name] = mx

        # 首次扫描诊断
        if not self.tail_entry_debug_printed:
            debug_stats = {
                'total_in_theme': 0, 'no_quote': 0,
                'hardfilter_fail': {}, 'score_dist': {'<65': 0, '65-74': 0, '75-84': 0, '>=85': 0},
                'max_scores': [], 'v3': True
            }

        # 遍历所有在主题中的股票
        for ts_code, themes in self.stock_themes.items():
            if not themes:
                continue

            if not self.tail_entry_debug_printed:
                debug_stats['total_in_theme'] += 1

            q = self.quotes.get(ts_code)
            if not q or q.get('price', 0) <= 0:
                if not self.tail_entry_debug_printed:
                    debug_stats['no_quote'] += 1
                continue

            # 获取主题数据
            theme_name = themes[0]
            kl = self.stock_klines.get(ts_code)
            if kl is not None and len(kl) > 0:
                # 数值列清洗: 缺失值填0, 防止 float(None) 崩溃
                for _col in ('open', 'high', 'low', 'close', 'pct_chg', 'vol', 'amount'):
                    if _col in kl.columns:
                        kl[_col] = pd.to_numeric(kl[_col], errors='coerce').fillna(0)
            turnover = self.turnover_cache.get(ts_code, 0)
            total_mv = self.stock_mv.get(ts_code, 0) if hasattr(self, 'stock_mv') and self.stock_mv else 0

            # 主题强度
            theme_strength = 0
            if theme_name in self.theme_score_history and self.theme_score_history[theme_name]:
                theme_strength = self.theme_score_history[theme_name][-1]
            # 从momentum cache获取主题数据 (leader_return用主题内实际最高涨幅)
            up_ratio, avg_return, _, _, _ = theme_momentum_cache.get(theme_name, (0, 0, 0, 0, None))
            leader_return = theme_leader_ret.get(theme_name, 0)
            theme_limit_count = theme_zt_counts.get(theme_name, 0)
            theme_avg_pct = avg_return

            # 获取layer
            layer = 'follower'
            for code, name, ly in self.theme_stocks.get(theme_name, []):
                if code == ts_code:
                    layer = ly
                    break

            # 获取股票名称
            stock_name = ''
            for code, n, _ in self.theme_stocks.get(theme_name, []):
                if code == ts_code:
                    stock_name = n
                    break

            # 角色识别
            role, role_score, role_detail = calc_stock_role_score_from_layer(
                layer, ts_code, theme_name, self.theme_stocks, self.quotes,
                kline_cache=self.stock_klines
            )

            # 硬过滤(V3)
            is_leader = (role == 'leader')
            passed, reason = v3.hard_filter_v3(
                ts_code, q, kl, turnover, total_mv, theme_strength,
                is_theme_leader=is_leader
            )
            if not passed:
                if not self.tail_entry_debug_printed:
                    debug_stats['hardfilter_fail'][reason] = debug_stats['hardfilter_fail'].get(reason, 0) + 1
                continue

            # ── V3 多维评分 ──
            # 1. 主题资金层 (30分)
            thm_s, thm_d = theme_score_v3(
                theme_limit_count=theme_limit_count,
                theme_up_ratio=up_ratio,
                leader_change=leader_return,
                theme_avg_change=theme_avg_pct,
                theme_strength=theme_strength,
            )

            # 2. 个股资金行为 (25分)
            cap_s, cap_d = capital_score_v3(ts_code, q, kl, turnover, snap=self.intraday_snapshots.get(ts_code, {}))

            # 3. 技术结构 (15分)
            tech_s, tech_d = v3.technical_structure_v3(q, kl)

            # 4. 尾盘交易 (10分)
            tail_s, tail_d = v3.tail_timing_v3(q, self.intraday_snapshots.get(ts_code, {}))

            # 5. 次日收益空间 (±5)
            room_s, room_d = v3.tomorrow_room_score(q, kl)

            # 6. 风险扣分 (max -20)
            risk_p, risk_d = v3.risk_penalty_v3(
                q, kl, turnover, up_ratio, theme_limit_count,
                is_theme_leader=is_leader,
                snap=self.intraday_snapshots.get(ts_code, {})
            )

            # 6.5 强势基因回调低吸形态加分 (最高+15)
            gap_s, gap_d = v3.pullback_gap_score_v3(q, kl, ts_code)

            total = min(100, thm_s + cap_s + role_score + tech_s + tail_s + gap_s + room_s - risk_p)

            if not self.tail_entry_debug_printed:
                debug_stats['max_scores'].append(total)
                if total < 65:
                    debug_stats['score_dist']['<65'] += 1
                elif total < 75:
                    debug_stats['score_dist']['65-74'] += 1
                elif total < 85:
                    debug_stats['score_dist']['75-84'] += 1
                else:
                    debug_stats['score_dist']['>=85'] += 1

            if total < 65:
                continue

            # 信号分级
            if total >= 85:
                signal = '强买入'
            elif total >= 75:
                signal = '买入观察'
            else:
                signal = '关注'

            # 买入类型
            pullback_quality = tech_d.get('pullback_v3', 0)
            buy_type, confidence = v3.classify_buy_type_v3(
                role, thm_s, up_ratio, theme_limit_count, tech_s, pullback_quality
            )
            # 强势基因回调低吸形态: 加分≥10 覆盖为 PULLBACK_GAP 高置信
            if gap_s >= 10:
                buy_type = 'PULLBACK_GAP'
                confidence = max(confidence, 85)

            # 次日预期
            next_day = v3._estimate_next_day(room_s, role, thm_s, cap_s)

            signal_data = {
                'ts_code': ts_code,
                'name': stock_name,
                'theme': theme_name,
                'total_score': total,
                'theme_score': thm_s,
                'capital_score': cap_s,
                'role_score': role_score,
                'technical_score': tech_s,
                'timing_score': tail_s,
                'room_score': room_s,
                'risk_penalty': risk_p,
                'gap_score': gap_s,
                'signal': signal,
                'role': role,
                'buy_type': buy_type,
                'confidence': confidence,
                'next_day_expectation': next_day,
                'pct_chg': q.get('pct_chg', 0),
                'price': q.get('price', 0),
                'detail': {
                    **thm_d, **cap_d, **role_detail, **tech_d, **tail_d, **room_d, **risk_d,
                    **gap_d,
                    'layer': layer, 'v3_filter_reason': reason,
                },
            }
            signal_data['explain'] = v3.explain_score_v3(signal_data)
            signals.append(signal_data)

        # 首次扫描输出诊断
        if not self.tail_entry_debug_printed:
            self.tail_entry_debug_printed = True
            passed_count = debug_stats['total_in_theme'] - debug_stats['no_quote'] - sum(debug_stats['hardfilter_fail'].values())
            print(f"\n{'='*70}")
            print(f"🔍 「猎尾V3」尾盘突袭首次扫描诊断 [{now.strftime('%H:%M:%S')}]")
            print(f"  主题内股票总数: {debug_stats['total_in_theme']}")
            print(f"  无行情: {debug_stats['no_quote']}")
            print(f"  硬过滤拦截:")
            for r, cnt in sorted(debug_stats['hardfilter_fail'].items(), key=lambda x: -x[1]):
                print(f"    {r}: {cnt}只")
            print(f"  通过硬过滤: {passed_count}只")
            print(f"  分数分布: {debug_stats['score_dist']}")
            if debug_stats['max_scores']:
                print(f"  最高分: {max(debug_stats['max_scores'])}  平均分: {sum(debug_stats['max_scores'])/len(debug_stats['max_scores']):.1f}")
            print(f"{'='*70}\n")

        signals.sort(key=lambda x: x['total_score'], reverse=True)
        return signals

    def print_summary(self, results):
        """控制台输出摘要(含趋势评分+仓位建议+扶摇MCP数据)"""
        now = datetime.now().strftime('%H:%M:%S')
        ms = results['market_stats']
        report = results.get('sentiment_report')

        print(f"\n{'='*60}")
        print(f"📊 [{now}] 大盘监控")
        print(f"   上涨 {ms['up']}/{ms['total']}({ms['up_ratio']}%) 涨停{ms['zt_count']} | 下跌{ms['down']}跌停{ms['dt_count']}")

        if report:
            idx = [f"{r['name']}趋势{r['trend_score']:.0f}/情绪{r['sentiment_score']:.0f}" for r in report['index_results']]
            print(f"   指数评分: " + " | ".join(idx))
            print(f"   市场状态【{report['market_status']}】趋势总评分{report['trend_score']:.1f} 建议仓位{report['position']}%({report['position_range']})")

        # ── 扶摇 MCP 补充数据 ──
        fuyao_status = self.fuyao.status if self.fuyao.is_healthy else f"⚠ {self.fuyao.status}"
        print(f"   扶摇MCP: {fuyao_status}")

        # 涨停池摘要
        zt_data = self.fuyao_data.get('limit_up_pool')
        if zt_data:
            items = zt_data.get('data', {}).get('item', [])
            if items:
                top5 = [f"{it.get('name','')}({it.get('continue_day_cnt',0)}板)" for it in items[:5]]
                print(f"   📈 涨停池(连板): {', '.join(top5)}" + (f" ...共{len(items)}只" if len(items) > 5 else ""))

        # 热股榜摘要
        hot_data = self.fuyao_data.get('hot_stock_list')
        if hot_data:
            items = hot_data.get('data', {}).get('item', [])
            if items:
                top5 = []
                for it in items[:5]:
                    trend = it.get('rank_trend', 'flat')
                    t_emoji = {'up': '↑', 'down': '↓', 'flat': '→'}.get(trend, '')
                    top5.append(f"{it.get('name','')}{t_emoji}")
                print(f"   🔥 热股榜: {', '.join(top5)}")

        # 龙虎榜摘要
        dt_data = self.fuyao_data.get('dragon_tiger_list')
        if dt_data:
            items = dt_data.get('data', {}).get('stock_items', [])
            if items:
                top3 = [f"{it.get('name','')}(净{'买入' if it.get('net_value',0)>0 else '卖出'}{abs(it.get('net_value',0))/1e8:.1f}亿)" for it in items[:3]]
                print(f"   🐉 龙虎榜: {', '.join(top3)} ...共{len(items)}只")

        print(f"{'='*60}")

    def push_alerts(self, alerts, now):
        """批量推送微信通知(纯文本格式,避免Markdown渲染异常)"""
        ts = now.strftime('%H:%M:%S')

        # 分类
        market_msgs = [a['msg'] for a in alerts if a['type'].startswith('market_') and
                       not a['type'].startswith('market_action_')]
        action_alerts = [a for a in alerts if a['type'].startswith('market_action_')]

        # ── 市场情绪预警 ──
        if market_msgs:
            title = f"⚠️ 市场情绪预警 {ts}"
            content_lines = [
                f"⚠️ 市场情绪预警",
                f"时间: {ts}",
                f"---",
            ]
            content_lines.extend(market_msgs)
            content_lines.extend([
                f"---",
                f"📊 数据基于实时主题成分股统计",
            ])
            self.send_wechat(title, '\n'.join(content_lines))

        # ── 大盘动作信号(加仓/减仓) 独立推送 ──
        for a in action_alerts:
            prefix = '🟢 加仓' if a['type'] == 'market_action_add' else '🔴 减仓'
            title = f"{prefix}信号 {ts}"
            self.send_wechat(title, a['msg'])

        # ── 控制台输出 ──
        print(f"\n📱 [{ts}] 推送:")
        for a in alerts:
            print(f"   {a['msg']}")


if __name__ == "__main__":
    def _is_process_alive(pid: int) -> bool:
        """检查 PID 对应的 python 进程是否存活 (Windows 兼容)

        注意: 不能使用 os.kill(pid, 0) —— Windows 上它会调用
        TerminateProcess 直接杀死目标进程(而非查询), 且权限不足时会抛
        OSError 被误判为"进程不存在", 导致锁失效、重复启动。
        改用 tasklist 查询, 并校验进程名包含 python 防止 PID 复用误判。
        """
        try:
            out = subprocess.run(
                ['tasklist', '/FI', f'PID eq {pid}', '/NH'],
                capture_output=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            ).stdout
            # tasklist 输出为系统本地编码(中文系统GBK), 用 errors=replace 容忍非ASCII字节
            text = out.decode('utf-8', errors='replace')
            return any(
                str(pid) in line and 'python' in line.lower()
                for line in text.splitlines()
            )
        except Exception:
            return True  # 无法判断时保守视为存活, 阻止重复启动

    # ── 单实例锁定(仅使用PID文件检查) ──
    lock_file = os.path.join(BASE_DIR, "realtime_theme_monitor.lock")
    current_pid = os.getpid()

    # 检查锁文件
    if os.path.exists(lock_file):
        try:
            with open(lock_file, 'r') as f:
                old_pid_str = f.read().strip()
            if old_pid_str and old_pid_str.isdigit():
                old_pid = int(old_pid_str)
                if old_pid != current_pid:
                    if _is_process_alive(old_pid):
                        print(f"⚠️  监控进程仍在运行 (PID: {old_pid}),退出。")
                        sys.exit(0)
                    else:
                        # 进程不存在，删除残留锁文件
                        print(f"✅ 清理残留锁文件 (旧进程 {old_pid} 已退出)")
                        os.remove(lock_file)
                else:
                    # 锁文件中的PID是当前进程，删除它
                    os.remove(lock_file)
            else:
                # 锁文件存在但内容无效，删除它
                os.remove(lock_file)
        except:
            # 无法读取锁文件，删除它
            try:
                os.remove(lock_file)
            except:
                pass

    # 写入当前PID到锁文件
    with open(lock_file, 'w') as f:
        f.write(str(current_pid))

    monitor = RealtimeThemeMonitor()
    try:
        monitor.run()
    finally:
        # ── 退出时删除锁文件 ──
        try:
            os.remove(lock_file)
            print("✅ 已清理锁文件")
        except:
            pass
