#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
游资级别实时主题盯盘系统

功能:
1. 从 theme_portfolio.db 加载36个主题+949只成分股
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

        # ── 冷却控制(避免重复推送) ──
        self.last_theme_alert = {}      # theme_name -> timestamp
        self.last_market_alert = 0
        self.last_score_alert = 0       # 趋势总评分预警冷却

        # ── 开盘参考价(昨日收盘) ──
        self.ref_prices = {}            # ts_code -> yesterday_close

        # ── 开盘分析标记 ──
        self.opening_analysis_done = False

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
        # 加载 theme.json 配置
        self._load_theme_json()

        if not self.theme_config:
            print("❌ theme.json配置为空,无法加载主题数据")
            sys.exit(1)

        # 从Tushare获取全市场股票列表
        print("⏳ 正在获取全市场股票列表...")
        name_to_code = self._load_all_stocks_from_tushare()

        if not name_to_code:
            print("❌ 无法获取股票列表,退出")
            sys.exit(1)

        # 根据theme.json配置匹配股票
        print("⏳ 正在匹配主题股票...")
        self.theme_stocks, self.stock_themes = self._match_theme_stocks(name_to_code)

        # 构建主题名称列表
        self.theme_names = list(self.theme_config.keys())

        total_stocks = sum(len(v) for v in self.theme_stocks.values())
        unique_stocks = len(self.stock_themes)

        # 统计跨主题股票
        multi_theme_stocks = {code: len(themes) for code, themes in self.stock_themes.items() if len(themes) > 1}

        print(f"✅ 从theme.json加载:")
        print(f"   主题数: {len(self.theme_stocks)} 个")
        print(f"   股票数: {unique_stocks} 只 (共 {total_stocks} 只次)")
        print(f"   跨主题: {len(multi_theme_stocks)} 只")
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
            try:
                daily = pro.daily(ts_code=','.join(all_codes[:3000]), start_date=trade_date, end_date=trade_date)
                time.sleep(0.3)

                if len(all_codes) > 3000:
                    daily2 = pro.daily(ts_code=','.join(all_codes[3000:]), start_date=trade_date, end_date=trade_date)
                    import pandas as pd
                    daily = pd.concat([daily, daily2], ignore_index=True) if not daily2.empty else daily

                if not daily.empty:
                    for _, row in daily.iterrows():
                        self.ref_prices[row['ts_code']] = {
                            'close': row['close'],
                            'pct_chg': row['pct_chg']
                        }
                    tushare_count = len(daily)
            except Exception as e:
                print(f"   ⚠ Tushare获取失败: {e}, 将从新浪行情获取昨收")
                tushare_count = 0
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

        # 分批获取(每批50只)
        batch_size = 50
        total_loaded = 0
        for i in range(0, len(all_codes), batch_size):
            batch = all_codes[i:i+batch_size]
            try:
                df = pro.daily(ts_code=",".join(batch), start_date=start_date, end_date=trade_date)
                if df is not None and not df.empty:
                    for code, grp in df.groupby('ts_code'):
                        grp_sorted = grp.sort_values('trade_date').reset_index(drop=True)
                        # 只保留需要的列
                        grp_sorted = grp_sorted[['trade_date', 'close', 'high', 'low', 'vol', 'pct_chg']]
                        self.stock_klines[code] = grp_sorted
                        total_loaded += 1
                time.sleep(0.2)  # 避免频率限制
            except Exception as e:
                print(f"⚠ K线获取失败 (batch {i//batch_size + 1}): {e}")
                continue

        print(f"✅ 成分股K线加载完成: {total_loaded}/{len(all_codes)} 只")

    # ── 计算主题趋势/情绪/综合分(每15分钟) ──
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

            results.append({
                'theme': theme_name,
                'n_stocks': len(stock_feats),
                'trend_score': t_score,
                'sentiment_score': s_score,
                'composite_score': c_score,
                'hot_score': round(hot_score, 2),
                'hot_percentile': hot_percentile,
                'hot_phase': hot_phase,
                'hot_warning': hot_warning
            })

        # 按综合分排序
        results.sort(key=lambda x: x['composite_score'], reverse=True)

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
        if now_ts - self.last_market_alert < 600:  # 至少10分钟冷却
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
            msg = f"❄️❄️【{status}】趋势总评分{ts:.0f} 建议仓位{pos}%\n"
            msg += f"📉 {index_summary}\n"
            msg += f"📊 {score_summary}\n"
            msg += f"下跌{down_count}/{total_count}({down_ratio}%) {zt_dt}"
            msg += yesterday_summary
            alerts.append({'type': 'market_fear', 'msg': msg})
        # 4) 普通情绪(市场状态中间档,冷却10分钟)
        else:
            msg = f"📊【{status}】趋势总评分{ts:.0f} 建议仓位{pos}%\n"
            msg += f"📍 {index_summary}\n"
            msg += f"📈 上涨{up_ratio}% 下跌{down_ratio}% {zt_dt}"
            msg += yesterday_summary
            alerts.append({'type': 'market_neutral', 'msg': msg})

        if alerts:
            self.last_market_alert = now_ts                   
            self.last_score_alert = now_ts
        return alerts

    def get_yesterday_market_data(self):
        """获取昨日市场分析数据"""
        import sqlite3
        import datetime

        try:
            # 获取昨日日期
            today = datetime.date.today()
            yesterday = today - datetime.timedelta(days=1)
            yesterday_str = yesterday.strftime('%Y%m%d')

            db_path = os.path.join(BASE_DIR, 'cache_backbone_tushare', 'market_analysis.db')
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            # 获取昨日整体分析
            cursor.execute("SELECT trend_score FROM overall_analysis WHERE trade_date=? ORDER BY id DESC LIMIT 1", (yesterday_str,))
            row = cursor.fetchone()
            if row:
                result = {'trend_score': round(row[0], 0) if row[0] else '?'}
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
                result['zt_count'] = limit_row[0] or '?'
                result['dt_count'] = limit_row[1] or '?'
                result['broken_rate'] = limit_row[2] or '?'
                result['zhaban_count'] = limit_row[3] or '?'
                result['max_limit_height'] = limit_row[4] or '?'
                result['up_count'] = limit_row[5] or '?'
                result['down_count'] = limit_row[6] or '?'
                result['total'] = limit_row[7] or '?'
                result['up_ratio'] = limit_row[8] or '?'
                result['down_ratio'] = limit_row[9] or '?'
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
        index_score = 0
        if cur_close > ma20: index_score = 30
        elif cur_close > ma10: index_score = 20
        elif cur_close > ma5: index_score = 10
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

    # ── 13. 市场趋势总评分(来自market_analysis.py calculate_market_trend_score) ──
    def calculate_total_market_score(self, results_per_index):
        """
        results_per_index: [{name, trend_score, sentiment_score, pct_chg}, ...]
        返回: (trend_score, index_trend, theme_trend, market_status, position)
        修正:指数趋势分应融合实时涨跌幅,让当日涨幅参与综合判断
        """
        # ── 1. 融合实时涨跌的动态指数趋势分 ──
        # 每个指数的综合分 = MA趋势分(70%) + 实时涨幅分(30%)
        # 实时涨幅分:按当日涨跌幅直接给分,反映当日市场强度
        enhanced_scores = []
        for r in results_per_index:
            ma_trend = r.get('trend_score', 50)
            pct_chg = r.get('pct_chg', 0) or 0
            # 实时涨幅分: +2%以上80分,+1.5%以上70分,+1%以上60分,+0.5%以上50分,正数40分,负数30分
            if pct_chg >= 2.0: rt_score = 80
            elif pct_chg >= 1.5: rt_score = 70
            elif pct_chg >= 1.0: rt_score = 60
            elif pct_chg >= 0.5: rt_score = 50
            elif pct_chg > 0: rt_score = 40
            elif pct_chg >= -0.5: rt_score = 30
            elif pct_chg >= -1.5: rt_score = 20
            else: rt_score = 10
            # 综合:均线权重70% + 实时涨幅权重30%
            enhanced = ma_trend * 0.7 + rt_score * 0.3
            enhanced_scores.append({
                'name': r['name'],
                'ma_trend': ma_trend,
                'rt_score': rt_score,
                'enhanced': enhanced,
                'pct_chg': pct_chg
            })

        # ── 2. 加权指数趋势(动态权重:涨幅大的指数权重更高) ──
        total_pct = sum(max(r['pct_chg'], 0) for r in enhanced_scores)
        if total_pct > 0:
            index_trend = sum(
                r['enhanced'] * (max(r['pct_chg'], 0.1) / total_pct)
                for r in enhanced_scores
            )
        else:
            # 传统固定权重
            sh_score = next((r['ma_trend'] for r in enhanced_scores if r['name'] == '上证指数'), 50)
            hs_score = next((r['ma_trend'] for r in enhanced_scores if r['name'] == '沪深300'), 50)
            zz_score = next((r['ma_trend'] for r in enhanced_scores if r['name'] == '中证2000'), 50)
            index_trend = round(sh_score * 0.5 + hs_score * 0.3 + zz_score * 0.2, 1)

        index_trend = round(index_trend, 1)

        # ThemeTrend:结合主题强度和市场广度计算，避免虚高
        # 1. 获取主题平均涨幅（历史数据）
        if self.theme_score_history and len(self.theme_score_history) > 0:
            vals = []
            for theme, hist in self.theme_score_history.items():
                if hist:
                    vals.append(hist[-1])
            if vals:
                top_avg = sum(sorted(vals, reverse=True)[:3]) / min(3, len(vals))
                # 基础theme_trend = 50 + 主题涨幅 * 系数，但需要市场广度修正
                theme_trend_raw = min(100, max(30, 50 + top_avg * 6))
            else:
                top_avg = 0
                theme_trend_raw = 50
        else:
            top_avg = 0
            theme_trend_raw = 50

        # 2. 获取市场广度（上涨比例）用于修正theme_trend
        # 获取overview数据中的上涨比例
        overview = self.compute_market_overview()
        up_ratio = overview.get('up_ratio', 50) if overview else 50

        # 3. 市场广度修正：上涨比例<50%时，主题趋势需要打折
        # 广度修正系数 = 0.5 + (up_ratio / 100) * 0.5，即40%(弱市)→0.7, 70%(强市)→0.85
        breadth_factor = 0.5 + (up_ratio / 100) * 0.5
        theme_trend = round(theme_trend_raw * breadth_factor, 1)

        self._recent_theme_scores = theme_trend

        # TrendScore = IndexTrend * 0.4 + ThemeTrend * 0.6
        trend_score = round(index_trend * 0.4 + theme_trend * 0.6, 1)
        # 移除不合理的加分规则，改为基于市场广度的修正
        if up_ratio < 40:  # 市场极弱时额外减分
            trend_score -= 10
        elif up_ratio < 50:  # 市场偏弱时轻微减分
            trend_score -= 5
        trend_score = min(100, max(0, trend_score))

        # 市场状态 & 建议仓位
        if trend_score >= 85:
            market_status, pos_range, pos = "主升浪", "80~100%", 90
        elif trend_score >= 75:
            market_status, pos_range, pos = "强趋势", "60~80%", 70
        elif trend_score >= 65:
            market_status, pos_range, pos = "趋势良好", "50~70%", 60
        elif trend_score >= 55:
            market_status, pos_range, pos = "震荡", "30~50%", 40
        elif trend_score >= 45:
            market_status, pos_range, pos = "弱势", "20~30%", 25
        elif trend_score >= 35:
            market_status, pos_range, pos = "退潮", "10~20%", 15
        else:
            market_status, pos_range, pos = "主跌段", "0~10%", 5

        return trend_score, index_trend, theme_trend, market_status, pos, pos_range

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
            em_fields = 'f12,f14,f3,f4,f5,f6,f7'
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
                            'high': 0,
                            'low': 0,
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

    def detect_theme_anomaly(self, results):
        """检测异常主题:强度突增、领涨板块"""
        now = datetime.now()
        alerts = []

        # ── 主题强度排序 ──
        sorted_themes = sorted(results['theme_scores'].items(), key=lambda x: x[1], reverse=True)

        # 取前5名
        top5 = sorted_themes[:5]
        if not top5:
            return alerts

        leader_theme = top5[0]
        leader_score = leader_theme[1]

        # ── 检测连续走强(过去3轮趋势判定) ──
        for theme_name, score in top5:
            history = list(self.theme_score_history[theme_name])
            if len(history) < 5:
                continue

            recent_avg = sum(history[-3:]) / 3
            prev_avg = sum(history[-6:-3]) / 3 if len(history) >= 6 else 0

            score_accel = recent_avg - prev_avg

            cooldown_key = f"theme_{theme_name}"
            last_alert = self.last_theme_alert.get(cooldown_key, 0)
            if time.time() - last_alert < 900:
                continue

            # 条件A:领涨主题且强度 > 3%
            if theme_name == leader_theme[0] and score >= 3 and score_accel > 0.5:
                top_stocks = self.get_theme_top_movers(theme_name, n=3)
                alerts.append({
                    'type': 'theme_leader',
                    'theme': theme_name,
                    'score': score,
                    'accel': round(score_accel, 2),
                    'top_stocks': top_stocks,
                    'msg': f"📈 领涨主题【{theme_name}】强度{score:+.1f}% 加速{score_accel:+.1f}% 先锋:{top_stocks}"
                })
                self.last_theme_alert[cooldown_key] = time.time()

            # 条件B:强度骤升 > 2%(主力突然拉板块)
            elif score_accel > 2 and score >= 2:
                top_stocks = self.get_theme_top_movers(theme_name, n=3)
                alerts.append({
                    'type': 'theme_surge',
                    'theme': theme_name,
                    'score': score,
                    'accel': round(score_accel, 2),
                    'top_stocks': top_stocks,
                    'msg': f"⚡ 异动主题【{theme_name}】强度{score:+.1f}% 飙升{score_accel:+.1f}% 先锋:{top_stocks}"
                })
                self.last_theme_alert[cooldown_key] = time.time()

        return alerts

    def detect_market_sentiment(self, results):
        """检测整体市场情绪预警(使用 market_analysis 算法)"""
        report = getattr(self, '_last_report', None)
        if report is None:
            # 回退:按旧简单阈值
            ms = results['market_stats']
            alerts = []
            if time.time() - self.last_market_alert < 600:
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

    def get_theme_top_movers(self, theme_name, n=3):
        """获取主题内涨幅前n的个股,带层级标记"""
        stocks = self.theme_stocks.get(theme_name, [])
        movers = []
        for ts_code, name, layer in stocks:
            q = self.quotes.get(ts_code)
            if q:
                movers.append((name, q['pct_chg'], layer))
        movers.sort(key=lambda x: x[1], reverse=True)

        # 层级标记映射
        layer_mark = {
            'leader': '⭐龙头',
            'middle': '▲中军',
            'member': '○成分'
        }

        return [f"{m[0]}{layer_mark.get(m[2], '')}({m[1]:+.1f}%)" for m in movers[:n]]

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

        # ── 连接 ──
        if not self.connect():
            print("⏳ 首次连接失败,启动服务器轮巡...")
            if not self.reconnect_round_robin():
                print("❌ 所有通达信服务器均不可用,退出")
                return

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
                    print(f"[{now.strftime('%H:%M:%S')}] ⚠ 行情获取失败,尝试重连+重试...")
                    self.connected = False
                    for attempt in range(2):
                        if not self.reconnect_round_robin():
                            print(f"   第{attempt+1}次重连失败,5秒后重试...")
                            time.sleep(5)
                            continue
                        for quote_retry in range(3):
                            ok = self.fetch_all_quotes()
                            if ok: break
                            print(f"   连接成功但取行情失败,第{quote_retry+1}次重试...")
                            time.sleep(2)
                        if ok: break
                    if not ok:
                        print(f"   ❌ 放弃本轮,等待下一周期")
                        time.sleep(10)
                        continue

                # ── 计算趋势评分+市场情绪(新算法) ──
                _ = self.compute_market_sentiment_report()

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

                # ── 每3分钟输出一次摘要 ──
                if cycle % 3 == 1:
                    self.print_summary(results)

                # ── 每15分钟更新一次全市场统计(后台线程) ──
                if cycle % 90 == 1:  # 60秒×90=5400秒=15分钟
                    print(f"⏳ 后台更新全市场统计...")
                    self.fetch_full_market_stats_sina()

                    # ── 每15分钟计算并输出主题综合分TOP10 ──
                    theme_scores = self.compute_theme_scores_realtime()
                    if theme_scores:
                        print(f"\n{'='*70}")
                        print(f"📊 主题综合评分 TOP10 [{now.strftime('%H:%M:%S')}]")
                        print(f"{'排名':<4} {'主题':<14} {'综合分':<8} {'趋势分':<8} {'情绪分':<8} {'热度分':<8} {'分位%':<6} {'阶段':<8}")
                        print(f"{'-'*70}")
                        for i, r in enumerate(theme_scores[:10], 1):
                            print(f"{i:<4} {r['theme']:<14} {r['composite_score']:>6.1f}   {r['trend_score']:>6.1f}   {r['sentiment_score']:>6.1f}   {r.get('hot_score', 0):>6.2f}   {r.get('hot_percentile', 0):<5.1f}   {r.get('hot_phase', '正常'):<8}")
                        print(f"{'='*70}\n")

                        # ── 推送主题综合分TOP10到微信 ──
                        lines = []
                        for i, r in enumerate(theme_scores[:10], 1):
                            phase_tag = r.get('hot_phase', '')
                            hot_info = f" 热度{r.get('hot_score', 0):.1f}({r.get('hot_percentile', 0):.0f}%)"
                            if phase_tag and phase_tag != '正常':
                                hot_info += f" {phase_tag}"
                            lines.append(f"{i}. {r['theme']} 综合分{r['composite_score']:.0f}(趋势{r['trend_score']:.0f}/情绪{r['sentiment_score']:.0f}){hot_info}")
                        content = f"📊 主题综合评分 TOP10 [{now.strftime('%H:%M')}]\n" + "\n".join(lines)
                        self.send_wechat(f"📊 主题综合评分 TOP10 {now.strftime('%H:%M')}", content)

                # ── 检测 → 推送 ──
                all_alerts = []
                all_alerts.extend(self.detect_theme_anomaly(results))
                all_alerts.extend(self.detect_market_sentiment(results))

                if all_alerts:
                    self.push_alerts(all_alerts, now)

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

    def print_summary(self, results):
        """控制台输出摘要(含趋势评分+仓位建议)"""
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

        top5 = sorted(results['theme_scores'].items(), key=lambda x: x[1], reverse=True)[:5]
        print(f"\n🔥 主题强度 TOP5:")
        for theme, score in top5:
            print(f"   {theme}: {score:+.1f}%")

        print(f"{'='*60}")

    def push_alerts(self, alerts, now):
        """批量推送微信通知(纯文本格式,避免Markdown渲染异常)"""
        ts = now.strftime('%H:%M:%S')

        # 分类
        theme_msgs = [a['msg'] for a in alerts if a['type'] in ('theme_leader', 'theme_surge')]
        market_msgs = [a['msg'] for a in alerts if a['type'].startswith('market_')]

        # ── 主题异动推送 ──
        if theme_msgs:
            title = f"🔥 主题异动 {ts} ({len(theme_msgs)}条)"
            content_lines = [
                f"🔥 实时主题异动",
                f"时间: {ts}",
                f"---",
            ]
            content_lines.extend(theme_msgs)
            content_lines.extend([
                f"---",
                f"💡 策略:优先关注领涨主题的龙头股,等待回调低吸机会",
            ])
            self.send_wechat(title, '\n'.join(content_lines))

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

        # ── 控制台输出 ──
        print(f"\n📱 [{ts}] 推送:")
        for a in alerts:
            print(f"   {a['msg']}")


if __name__ == "__main__":
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
                    try:
                        os.kill(old_pid, 0)  # 检查进程是否存在
                        print(f"⚠️  监控进程仍在运行 (PID: {old_pid}),退出。")
                        sys.exit(0)
                    except OSError:
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
