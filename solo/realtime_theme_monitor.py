#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
游资级别实时主题盯盘系统

功能：
1. 从 theme_portfolio.db 加载36个主题+949只成分股
2. 通过通达信实时行情获取1分钟级数据
3. 计算各主题实时强度（涨幅+成交额加权），捕捉最先启动的主题
4. 检测各主题内最先启动的个股（游资先锋）
5. 整体市场情绪预警（大面积亏钱/普涨）
6. 通过Server酱推送到微信

运行：python realtime_theme_monitor.py
"""
import os
import sys
import time
import json
import sqlite3
import threading
from datetime import datetime, timedelta
from collections import defaultdict, deque

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config', '.env'))

# ── 控制台编码修复（支持 emoji/Unicode） ──
# 已移除 UTF-8 wrapper，改用环境变量 PYTHONIOENCODING

# ── 通达信 ──
try:
    from pytdx.hq import TdxHq_API
    from pytdx.config.hosts import hq_hosts
    TDX_AVAILABLE = True
except ImportError:
    TDX_AVAILABLE = False
    hq_hosts = []

# ── Tushare（仅用于盘后初始化缓存） ──
try:
    import tushare as ts
    pro = ts.pro_api(os.getenv('TUSHARE_TOKEN'))
    TS_AVAILABLE = True
except:
    TS_AVAILABLE = False


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")
DB_PATH = os.path.join(CACHE_DIR, "theme_portfolio.db")


class RealtimeThemeMonitor:
    def __init__(self):
        self.api = None
        self.connected = False
        self.best_server = None

        # ── 行情缓存（每分钟更新） ──
        self.quotes = {}            # ts_code -> {price, pct_chg, amount, vol}
        self.prev_quotes = {}       # 上一分钟快照

        # ── 主题数据 ──
        self.theme_stocks = {}      # theme_name -> [(ts_code, name, layer)]
        self.theme_names = []       # 有序主题列表
        self.stock_themes = {}      # ts_code -> [theme_name, ...]

        # ── 主题历史强度（用于趋势判定） ──
        self.theme_score_history = defaultdict(lambda: deque(maxlen=15))
        self.theme_volume_history = defaultdict(lambda: deque(maxlen=15))

        # ── 冷却控制（避免重复推送） ──
        self.last_theme_alert = {}      # theme_name -> timestamp
        self.last_market_alert = 0
        self.last_first_mover_alert = defaultdict(float)

        # ── 开盘参考价（昨日收盘） ──
        self.ref_prices = {}            # ts_code -> yesterday_close

        # ── 开盘分析标记 ──
        self.opening_analysis_done = False

        # ── 服务器列表（从 pytdx hq_hosts 池加载，去重） ──
        seen = set()
        self.servers = []
        for name, ip, port in hq_hosts:
            key = (ip, port)
            if key not in seen:
                seen.add(key)
                self.servers.append(key)
        # 额外补充稳定主站（可能不在hq_hosts中）
        extras = [
            ("180.153.18.170", 7709), ("180.153.18.171", 7709),
            ("180.153.18.172", 80),   ("202.108.253.130", 7709),
            ("202.108.253.131", 7709), ("202.108.253.139", 80),
            ("119.147.164.60", 7709), ("jstdx.gtjas.com", 7709),
        ]
        for ip, port in extras:
            if (ip, port) not in seen:
                self.servers.append((ip, port))
                seen.add((ip, port))
        print(f"📡 加载通达信服务器池: {len(self.servers)} 台 (来自 pytdx hq_hosts + 补充)")

        self.sckey = os.getenv("WECHAT_SCKEY")

        # ── 服务器轮巡状态 ──
        self.sorted_servers = []    # 按延迟排序的服务器列表
        self.server_index = 0       # 当前尝试的服务器索引

    # ════════════════════════════════════════════
    # 1. 数据加载
    # ════════════════════════════════════════════
    def load_theme_db(self):
        if not os.path.exists(DB_PATH):
            print(f"错误：未找到 {DB_PATH}，请先运行 theme_portfolio_strategy_cached.py")
            sys.exit(1)

        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()

        cur.execute("SELECT ts_code, name, theme_name, layer FROM portfolio")
        rows = cur.fetchall()

        for ts_code, name, theme_name, layer in rows:
            if theme_name not in self.theme_stocks:
                self.theme_stocks[theme_name] = []
                self.theme_names.append(theme_name)
            self.theme_stocks[theme_name].append((ts_code, name, layer))

            if ts_code not in self.stock_themes:
                self.stock_themes[ts_code] = []
            self.stock_themes[ts_code].append(theme_name)

        conn.close()

        total_stocks = sum(len(v) for v in self.theme_stocks.values())
        print(f"✅ 从数据库加载: {len(self.theme_stocks)} 个主题, {total_stocks} 只股票")
        print(f"   主题列表: {', '.join(self.theme_names)}")

    def load_ref_prices(self):
        """从Tushare获取昨日收盘价（有缓存则用缓存）"""
        if not TS_AVAILABLE:
            print("⚠ Tushare不可用，无法加载昨日收盘价，将仅使用涨跌幅判断")
            return

        from datetime import datetime as dt
        now = dt.now()

        if now.hour < 15:
            query_date = (now - timedelta(days=1)).strftime('%Y%m%d')
        else:
            query_date = now.strftime('%Y%m%d')

        cal = pro.trade_cal(exchange='', start_date='20260101', end_date=query_date)
        cal = cal[cal['is_open'] == 1]
        trade_date = str(cal[cal['cal_date'] <= query_date]['cal_date'].max())

        cache_file = os.path.join(CACHE_DIR, f"ref_prices_{trade_date}.pkl")
        if os.path.exists(cache_file):
            import pickle
            with open(cache_file, 'rb') as f:
                self.ref_prices = pickle.load(f)
            print(f"✅ 从缓存加载昨日收盘价: {len(self.ref_prices)} 只")
            return

        all_codes = list(self.stock_themes.keys())
        print(f"⏳ 获取{trade_date}日线数据，共{len(all_codes)}只...")
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

        import pickle
        with open(cache_file, 'wb') as f:
            pickle.dump(self.ref_prices, f)
        print(f"✅ 已获取并缓存昨日收盘价: {len(self.ref_prices)} 只")

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
            print("⚠ 未找到可用服务器，使用默认列表轮巡")
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
        """遍历所有已发现的服务器，一旦成功就停止，返回True/False"""
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
        """批量获取所有股票最新行情（通达信 get_security_quotes）"""
        if not self.connected and not self.connect():
            return False

        stock_codes = list(self.stock_themes.keys())
        tdx_list = []
        for code in stock_codes:
            if code.endswith('.SH'):
                tdx_list.append((0, code.replace('.SH', '')))
            elif code.endswith('.SZ'):
                tdx_list.append((1, code.replace('.SZ', '')))

        batch_size = 80
        quote_map = {}
        errors = 0

        for i in range(0, len(tdx_list), batch_size):
            batch = tdx_list[i:i + batch_size]
            try:
                quotes = self.api.get_security_quotes(batch)
                if quotes:
                    for q in quotes:
                        raw_code = str(q['code'])
                        market = q['market']
                        # 转回ts_code格式
                        ts_c = raw_code + ('.SH' if market == 0 else '.SZ')
                        price = float(q['price'])
                        last_close = float(q['last_close'])
                        vol = int(q['vol']) if hasattr(q, 'vol') else 0
                        amount_val = float(q['amount']) if hasattr(q, 'amount') else 0

                        if last_close > 0 and price > 0:
                            pct_chg = (price - last_close) / last_close * 100
                        else:
                            pct_chg = 0

                        quote_map[ts_c] = {
                            'price': price,
                            'pct_chg': round(pct_chg, 2),
                            'amount': amount_val,
                            'vol': vol,
                            'last_close': last_close
                        }
            except Exception as e:
                errors += 1
                if errors > 3:
                    self.connected = False
                    print(f"   ⚠ 连续{errors}次查询异常，触发服务器轮巡...")
                    return False
            time.sleep(0.05)

        if quote_map:
            self.prev_quotes = self.quotes.copy()
            self.quotes = quote_map
        else:
            self.connected = False
        return bool(quote_map)

    # ════════════════════════════════════════════
    # 4. 分析引擎
    # ════════════════════════════════════════════
    def analyze(self):
        now = datetime.now()
        results = {
            'theme_scores': {},
            'theme_volumes': {},
            'first_movers': [],
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

                # ── 检测主题内的先锋股 ──
                if layer == 'leader' and pct >= 3:
                    prev = self.prev_quotes.get(ts_code)
                    prev_pct = prev['pct_chg'] if prev else 0
                    delta = pct - prev_pct
                    if delta >= 1.5:
                        results['first_movers'].append({
                            'ts_code': ts_code,
                            'name': name,
                            'theme': theme_name,
                            'pct_chg': pct,
                            'surge_delta': round(delta, 2)
                        })

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
        """检测异常主题：强度突增、领涨板块"""
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

        # ── 检测连续走强（过去3轮趋势判定） ──
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

            # 条件A：领涨主题且强度 > 3%
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

            # 条件B：强度骤升 > 2%（主力突然拉板块）
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

    def detect_first_movers(self, results):
        """检测全市场最先启动的先锋股"""
        now = datetime.now()
        alerts = []

        for fm in results.get('first_movers', []):
            cooldown_key = f"fm_{fm['ts_code']}"
            if time.time() - self.last_first_mover_alert.get(cooldown_key, 0) < 1800:
                continue

            alerts.append({
                'type': 'first_mover',
                'stock': fm['name'],
                'code': fm['ts_code'],
                'theme': fm['theme'],
                'pct_chg': fm['pct_chg'],
                'surge_delta': fm['surge_delta'],
                'msg': f"🚀 先锋启动【{fm['name']}({fm['ts_code'][:6]})】{fm['pct_chg']:+.1f}% 主题:{fm['theme']} 跳涨{fm['surge_delta']:+.1f}%"
            })
            self.last_first_mover_alert[cooldown_key] = time.time()

        return alerts

    def detect_market_sentiment(self, results):
        """检测整体市场情绪预警"""
        ms = results['market_stats']
        now = datetime.now()
        alerts = []

        up_r = ms['up_ratio']
        down_r = ms['down_ratio']
        zt = ms['zt_count']
        dt = ms['dt_count']

        if time.time() - self.last_market_alert < 600:
            return alerts

        if up_r > 80:
            alerts.append({
                'type': 'market_overheat',
                'msg': f"🔥🔥 市场过热! 上涨{ms['up']}/{ms['total']}({up_r}%) 涨停{zt}家 ⚠建议减仓至30%"
            })
            self.last_market_alert = time.time()
        elif down_r > 50:
            alerts.append({
                'type': 'market_fear',
                'msg': f"❄️❄️ 大面积亏钱! 下跌{ms['down']}/{ms['total']}({down_r}%) 跌停{dt}家 ⚠建议减仓至20%"
            })
            self.last_market_alert = time.time()
        elif dt > 20 and down_r > 30:
            alerts.append({
                'type': 'market_crash_warning',
                'msg': f"⚠️ 亏钱效应显著! 跌停{dt}家 下跌占比{down_r}% 建议控制仓位≤40%"
            })
            self.last_market_alert = time.time()
        elif up_r > 60 and zt > 30:
            alerts.append({
                'type': 'market_warming',
                'msg': f"🌡️ 市场回暖! 上涨{ms['up']}/{ms['total']}({up_r}%) 涨停{zt}家 可适当加仓至60%"
            })
            self.last_market_alert = time.time()

        return alerts

    def get_theme_top_movers(self, theme_name, n=3):
        """获取主题内涨幅前n的个股"""
        stocks = self.theme_stocks.get(theme_name, [])
        movers = []
        for ts_code, name, layer in stocks:
            q = self.quotes.get(ts_code)
            if q:
                movers.append((name, q['pct_chg'], layer))
        movers.sort(key=lambda x: x[1], reverse=True)
        return [f"{m[0]}({m[1]:+.1f}%)" for m in movers[:n]]

    # ════════════════════════════════════════════
    # 5. 开盘分析（9:32）
    # ════════════════════════════════════════════
    def run_opening_analysis(self):
        """
        9:32分开盘分析：基于第一次完整行情快照，输出五大维度报告。
        """
        now = datetime.now()
        results = self.analyze()
        if not results or not results['theme_scores']:
            return

        ms = results['market_stats']
        total = ms['total'] if ms['total'] > 0 else 1

        # ── 1. 主题排序 ──
        sorted_themes = sorted(results['theme_scores'].items(), key=lambda x: x[1], reverse=True)
        top10 = sorted_themes[:10]

        # ── 2. 计算各板块的五维数据 ──
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
                if q['pct_chg'] > 0:
                    up_count += 1
                theme_amount += q.get('amount', 0)

                if layer == 'leader' and (leader_pct is None or q['pct_chg'] > leader_pct):
                    leader_pct = q['pct_chg']
                    leader_name = name

            up_ratio = up_count / total_in_theme * 100 if total_in_theme > 0 else 0
            leader_str = f"{leader_name}{leader_pct:+.1f}%" if leader_name else "—"
            amount_yi = round(theme_amount / 1e8, 2)

            lines.append((rank, theme_name, avg_pct, up_ratio, leader_str, amount_yi, total_in_theme))

        # ── 3. 市场情绪分 ──
        up_r = ms['up_ratio']
        zt_r = ms['zt_count'] / total * 100
        dt_r = ms['dt_count'] / total * 100
        sentiment_score = up_r * 0.5 + zt_r * 2.0 - dt_r * 2.0
        sentiment_score = max(0, min(100, round(sentiment_score, 1)))

        if sentiment_score >= 70:
            sentiment_label = "🔥 强势"
        elif sentiment_score >= 45:
            sentiment_label = "🌤 中性偏暖"
        elif sentiment_score >= 25:
            sentiment_label = "🌥 中性偏弱"
        else:
            sentiment_label = "❄ 弱势"

        ts = now.strftime('%H:%M:%S')

        # ── 4. 构建推送内容 ──
        title = f"📊 开盘分析 {now.strftime('%m-%d')}"

        lines_text = ""
        for rank, name, avg_pct, up_r, ldr, amt, cnt in lines:
            bar = "█" * max(1, min(10, int(abs(avg_pct) + 0.5)))
            direction = "+" if avg_pct >= 0 else ""
            lines_text += f"| {rank} | **{name}** | {direction}{avg_pct:.1f}% {bar} | {up_r:.0f}% | {ldr} | {amt}亿 | {cnt}只 |\n"

        content = f"""## 📊 开盘竞价全景分析

**时间:** {ts}
**市场情绪分:** {sentiment_score} — {sentiment_label}

| | | 上涨: {ms['up']}/{ms['total']}({ms['up_ratio']}%) | 涨停: {ms['zt_count']} | 跌停: {ms['dt_count']} | | |
|---|---|---|---|---|---|---|

### 五维竞争力 TOP10

| 排名 | 板块 | ①竞价强度 | ②上涨占比 | ③龙头涨幅 | ④开盘成交额 | ⑤成分股 |
|:---:|:---|:---:|:---:|:---:|:---:|:---:|
{lines_text}

---
*①开盘竞价强度 = 板块个股平均涨跌幅*
*②核心池上涨占比 = 板块内上涨个股占比*
*③龙头涨幅 = 层layer=leader的个股涨幅*
*④开盘成交额 = 板块个股总成交额（亿元）*
*⑤成分股 = 板块内成功获取行情的个股数*
"""
        self.send_wechat(title, content)

        # 控制台也打一份
        print(f"\n{'='*55}")
        print(f"📊 开盘分析 [{ts}]  情绪分: {sentiment_score} {sentiment_label}")
        print(f"{'='*55}")
        print(f"{'排名':<4} {'板块':<10} {'强度':<8} {'↑占比':<6} {'龙头':<18} {'成交额':<10} {'成分'}")
        print(f"{'-'*55}")
        for rank, name, avg_pct, up_r, ldr, amt, cnt in lines:
            print(f"{rank:<4} {name:<10} {avg_pct:+.1f}%   {up_r:.0f}%   {ldr:<16} {amt:<8} {cnt}")
        print(f"{'='*55}\n")

    # ════════════════════════════════════════════
    # 6. 推送
    # ════════════════════════════════════════════
    def send_wechat(self, title, content):
        if not self.sckey:
            print("⚠ 未配置WECHAT_SCKEY")
            return
        url = f"https://sctapi.ftqq.com/{self.sckey}.send"
        try:
            resp = requests.post(url, data={"title": title, "desp": content}, timeout=10)
            print(f"📱 推送成功: {title[:30]}")
            return resp
        except Exception as e:
            print(f"❌ 推送失败: {e}")

    # ════════════════════════════════════════════
    # 7. 主循环
    # ════════════════════════════════════════════
    def run(self):
        print("=" * 60)
        print("🔥 游资级别实时主题盯盘系统")
        print("   数据源: theme_portfolio.db + 通达信实时行情")
        print("   更新周期: 60秒")
        print("   推送: Server酱微信")
        print("=" * 60)

        # ── 加载数据 ──
        self.load_theme_db()
        self.load_ref_prices()

        # ── 连接 ──
        if not self.connect():
            print("⏳ 首次连接失败，启动服务器轮巡...")
            if not self.reconnect_round_robin():
                print("❌ 所有通达信服务器均不可用，退出")
                return

        print(f"\n📊 开始监控 {len(self.theme_stocks)} 个主题 {sum(len(v) for v in self.theme_stocks.values())} 只股票")
        print("   交易时段: 9:30-11:30, 13:00-15:00")
        print("   (按 Ctrl+C 停止)\n")

        cycle = 0
        first_run = True

        try:
            while True:
                now = datetime.now()

                # ── 非交易时段跳过 ──
                if not self.is_trading_time(now):
                    if first_run or now.minute == 0:
                        print(f"[{now.strftime('%H:%M:%S')}] 非交易时段，等待开盘...")
                        first_run = False
                    time.sleep(30)
                    continue

                first_run = False
                cycle += 1

                # ── 获取行情 ──
                ok = self.fetch_all_quotes()
                if not ok:
                    print(f"[{now.strftime('%H:%M:%S')}] ⚠ 行情获取失败，尝试重连+重试...")
                    self.connected = False
                    # 尝试最多2次：每次先换服务器重连，再多次重试取行情
                    for attempt in range(2):
                        if not self.reconnect_round_robin():
                            print(f"   第{attempt+1}次重连失败，5秒后重试...")
                            time.sleep(5)
                            continue
                        # 同一条连接上最多重试3次取行情
                        for quote_retry in range(3):
                            ok = self.fetch_all_quotes()
                            if ok:
                                break
                            print(f"   连接成功但取行情失败，第{quote_retry+1}次重试...")
                            time.sleep(2)
                        if ok:
                            break
                    if not ok:
                        print(f"   ❌ 放弃本轮，等待下一周期")
                        time.sleep(10)
                        continue

                # ── 9:32 开盘分析（仅一次） ──
                if not self.opening_analysis_done and now.hour == 9 and 32 <= now.minute <= 35:
                    print(f"\n[{now.strftime('%H:%M:%S')}] ⏰ 触发开盘分析...")
                    self.run_opening_analysis()
                    self.opening_analysis_done = True
                    # 开盘分析后短暂休息，继续下一周期采集
                    time.sleep(5)
                    continue

                # ── 分析 ──
                results = self.analyze()
                if not results:
                    time.sleep(10)
                    continue

                # ── 每3分钟输出一次摘要（减少刷屏） ──
                if cycle % 3 == 1:
                    self.print_summary(results)

                # ── 检测 → 推送 ──
                all_alerts = []
                all_alerts.extend(self.detect_theme_anomaly(results))
                all_alerts.extend(self.detect_first_movers(results))
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
        """控制台输出摘要"""
        now = datetime.now().strftime('%H:%M:%S')
        ms = results['market_stats']

        print(f"\n{'='*50}")
        print(f"📊 [{now}] 大盘监控")
        print(f"   上涨 {ms['up']}/{ms['total']}({ms['up_ratio']}%) 涨停{ms['zt_count']} | 下跌{ms['down']}跌停{ms['dt_count']}")

        top5 = sorted(results['theme_scores'].items(), key=lambda x: x[1], reverse=True)[:5]
        print(f"\n🔥 主题强度 TOP5:")
        for theme, score in top5:
            print(f"   {theme}: {score:+.1f}%")

        # 先锋股（如果有）
        fms = results.get('first_movers', [])
        if fms:
            print(f"\n🚀 先锋启动:")
            for fm in fms[:3]:
                print(f"   {fm['name']}({fm['ts_code'][:6]}): {fm['pct_chg']:+.1f}% 主题:{fm['theme']}")

        print(f"{'='*50}")

    def push_alerts(self, alerts, now):
        """批量推送微信通知"""
        ts = now.strftime('%H:%M:%S')

        # 分类
        theme_msgs = [a['msg'] for a in alerts if a['type'] in ('theme_leader', 'theme_surge')]
        fm_msgs = [a['msg'] for a in alerts if a['type'] == 'first_mover']
        market_msgs = [a['msg'] for a in alerts if a['type'].startswith('market_')]

        # ── 主题异动推送 ──
        if theme_msgs:
            title = f"🔥 主题异动 {ts} ({len(theme_msgs)}条)"
            content = f"## 🔥 实时主题异动\n\n**时间: {ts}**\n\n---\n\n" + "\n\n".join(theme_msgs)
            content += "\n\n---\n💡 **策略**：优先关注领涨主题的龙头股，等待回调低吸机会"
            self.send_wechat(title, content)

        # ── 先锋启动推送 ──
        if fm_msgs:
            title = f"🚀 先锋启动 {ts} ({len(fm_msgs)}只)"
            content = f"## 🚀 实时先锋启动\n\n**时间: {ts}**\n\n" + "\n".join(fm_msgs)
            content += "\n\n💡 优先关注同主题内还未启动的 core/follower"
            self.send_wechat(title, content)

        # ── 市场情绪预警 ──
        if market_msgs:
            title = f"⚠️ 市场情绪预警 {ts}"
            content = f"## ⚠️ 市场情绪预警\n\n**时间: {ts}**\n\n---\n\n" + "\n\n".join(market_msgs)
            content += "\n\n---\n📊 数据基于实时主题成分股统计"
            self.send_wechat(title, content)

        # ── 控制台输出 ──
        print(f"\n📱 [{ts}] 推送:")
        for a in alerts:
            print(f"   {a['msg']}")


if __name__ == "__main__":
    monitor = RealtimeThemeMonitor()
    monitor.run()
