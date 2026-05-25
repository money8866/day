# -*- coding: utf-8 -*-
"""
盘中实时监控模块 (pytdx版)
- 通过pytdx连接通达信公网免费行情主站
- 3秒轮询股票池，实时预警推送微信
- 数据源: pytdx get_security_quotes(盘中介快照) + get_security_bars(1min K线)
"""

import json, os, sys, time, datetime, math, signal, socket
from pathlib import Path
from collections import deque

sys.path.insert(0, str(Path(__file__).parent))
from config import CACHE_DIR, DIVERGENCE, POSITION, DRAGON

from pytdx.hq import TdxHq_API
from pytdx.config.hosts import hq_hosts


class TdxServerManager:
    """自动选择/切换可用的通达信行情主站"""

    def __init__(self, timeout=3):
        self.timeout = timeout
        self.api = TdxHq_API()
        self.current_host = None
        self._test_and_connect()

    def _test_host(self, ip, port):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(self.timeout)
            s.connect((ip, port))
            s.close()
            return True
        except:
            return False

    def _test_and_connect(self):
        for name, ip, port in hq_hosts:
            if self._test_host(ip, port):
                try:
                    self.api.connect(ip, port)
                    self.current_host = f"{ip}:{port}"
                    print(f"[TDX] Connected to {ip}:{port}")
                    return True
                except:
                    continue
        print("[TDX] ERROR: No reachable TDX server found!")
        return False

    def reconnect(self):
        try:
            self.api.disconnect()
        except:
            pass
        return self._test_and_connect()

    def get_api(self):
        try:
            self.api.get_security_bars(1, 0, "000001", 0, 1)
            return self.api
        except:
            if self.reconnect():
                return self.api
            return None


def calc_rsi(prices, period=14):
    """RSI指标"""
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1 + rs)


class RealtimeMonitor:
    """盘中实时监控 (pytdx驱动)"""

    def __init__(self, watch_pool_path=None, interval_sec=3):
        self.server = TdxServerManager()
        self.interval = interval_sec
        self.watch_stocks = []
        self.watch_pool_path = watch_pool_path
        self.price_cache = {}   # {code: deque(close_prices, maxlen=100)}
        self.bar_cache = {}     # {code: latest_bar_datetime}
        self.vol_cache = {}     # {code: {prev_total, today_total}}
        self.alert_history = {} # {alert_key: last_alert_timestamp}
        self.config = {
            "rsi_overbought": 85,
            "rsi_oversold": 20,
            "stop_loss_pct": abs(POSITION.get("stop_loss", 0.05)) * 100,
            "take_profit_pct": POSITION.get("take_profit", 0.20) * 100,
            "trailing_stop_pct": POSITION.get("trailing_stop", 0.08) * 100,
            "price_change_pct": 2.0,
            "alert_cooldown": 300,
        }
        self.running = False
        self.start_time = None
        self.alert_count = 0

    def load_watch_pool(self):
        """加载监控股票池"""
        if self.watch_pool_path and os.path.exists(self.watch_pool_path):
            path = self.watch_pool_path
        else:
            cache_dir = Path(CACHE_DIR)
            scan_files = sorted(cache_dir.glob("scan_*.json"))
            if not scan_files:
                print("[WARN] No scan files found")
                return False
            path = scan_files[-1]

        print(f"[POOL] Loading: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        stocks = []
        for s in data.get("dragons", [])[:10]:
            code = s.get("code", "")
            market = 0 if code.startswith("6") else 1
            stocks.append({
                "code": code, "name": s.get("name", code),
                "market": market, "type": "dragon",
                "entry_price": s.get("price", 0), "score": s.get("score", 0),
            })
        for s in data.get("divergence", []):
            code = s.get("code", "")
            market = 0 if code.startswith("6") else 1
            stocks.append({
                "code": code, "name": s.get("name", code),
                "market": market, "type": "divergence",
                "entry_price": s.get("price", 0),
            })

        seen = set()
        unique = []
        for s in stocks:
            if s["code"] not in seen:
                seen.add(s["code"])
                unique.append(s)
        self.watch_stocks = unique
        print(f"[POOL] {len(self.watch_stocks)} stocks loaded")
        return True

    def init_price_cache(self):
        """预加载最近100根1min K线用于RSI"""
        if not self.watch_stocks:
            return
        api = self.server.get_api()
        if not api:
            print("[ERROR] Cannot connect to TDX")
            return

        for stock in self.watch_stocks:
            code = stock["code"]
            mkt = stock["market"]
            try:
                bars = api.get_security_bars(1, mkt, code, 0, 100)
                if bars:
                    df = api.to_df(bars)
                    closes = df["close"].tolist()
                    self.price_cache[code] = deque(closes, maxlen=100)
                    self.bar_cache[code] = df.iloc[0]["datetime"]
                    # 获取日线用于量能基准
                    daily = api.get_security_bars(4, mkt, code, 0, 5)
                    if daily:
                        df_d = api.to_df(daily)
                        if len(df_d) >= 2:
                            self.vol_cache[code] = {
                                "prev_total": df_d.iloc[1]["vol"],
                                "today_total": 0,
                            }
                    print(f"  [INIT] {stock['name']}({code}): {len(closes)} bars")
            except Exception as e:
                print(f"  [ERROR] {code}: {e}")
        print(f"[INIT] Cache ready for {len(self.price_cache)} stocks")

    def fetch_realtime_snapshot(self):
        """获取实时快照(盘中)"""
        api = self.server.get_api()
        if not api:
            return {}
        quotes = {}
        for stock in self.watch_stocks:
            code = stock["code"]
            mkt = stock["market"]
            try:
                data = api.get_security_quotes([(mkt, code)])
                if data and data[0]:
                    q = data[0]
                    pct = 0
                    if q["last_close"] and q["last_close"] > 0:
                        pct = (q["price"] - q["last_close"]) / q["last_close"] * 100
                    quotes[code] = {
                        "price": q["price"], "open": q["open"],
                        "high": q["high"], "low": q["low"],
                        "last_close": q["last_close"], "vol": q["vol"],
                        "amount": q["amount"], "bid1": q["bid1"],
                        "ask1": q["ask1"], "pct_change": pct,
                    }
            except:
                pass
        return quotes

    def fetch_latest_bars(self):
        """更新1min K线缓存"""
        api = self.server.get_api()
        if not api:
            return
        for stock in self.watch_stocks:
            code = stock["code"]
            mkt = stock["market"]
            try:
                bars = api.get_security_bars(1, mkt, code, 0, 2)
                if bars:
                    df = api.to_df(bars)
                    latest_dt = df.iloc[0]["datetime"]
                    if code not in self.bar_cache or self.bar_cache[code] != latest_dt:
                        if len(df) >= 2 and code in self.bar_cache:
                            self.price_cache.setdefault(code, deque(maxlen=100)).append(df.iloc[1]["close"])
                        self.price_cache.setdefault(code, deque(maxlen=100)).append(df.iloc[0]["close"])
                        self.bar_cache[code] = latest_dt
            except:
                pass

    def check_alerts(self, quotes):
        """检查预警"""
        alerts = []
        cfg = self.config
        now = time.time()
        for stock in self.watch_stocks:
            code = stock["code"]
            name = stock["name"]
            q = quotes.get(code)
            if not q or q["price"] <= 0:
                continue
            price = q["price"]
            pct = q["pct_change"]

            rsi = 50.0
            if code in self.price_cache and len(self.price_cache[code]) >= 15:
                rsi = calc_rsi(list(self.price_cache[code]))

            # RSI超买
            if rsi > cfg["rsi_overbought"]:
                key = f"{code}_RSI_OB"
                if now - self.alert_history.get(key, 0) > cfg["alert_cooldown"]:
                    alerts.append({"code": code, "name": name, "severity": "\\u26a0\\ufe0f",
                        "type": "RSI超买", "msg": f"RSI={rsi:.1f}>85 过热预警"})
                    self.alert_history[key] = now
            elif rsi < cfg["rsi_oversold"]:
                key = f"{code}_RSI_OS"
                if now - self.alert_history.get(key, 0) > cfg["alert_cooldown"]:
                    alerts.append({"code": code, "name": name, "severity": "\\U0001f7e2",
                        "type": "RSI超卖", "msg": f"RSI={rsi:.1f}<20 超卖反弹"})
                    self.alert_history[key] = now

            entry = stock.get("entry_price", 0)
            if entry and entry > 0 and price > 0:
                pnl = (price - entry) / entry * 100
                if pnl < -cfg["stop_loss_pct"]:
                    key = f"{code}_SL"
                    if now - self.alert_history.get(key, 0) > cfg["alert_cooldown"]:
                        alerts.append({"code": code, "name": name, "severity": "\\U0001f534",
                            "type": "止损", "msg": f"{entry:.2f}->{price:.2f} 亏{pnl:.1f}% 止损!"})
                        self.alert_history[key] = now
                elif pnl > cfg["take_profit_pct"]:
                    key = f"{code}_TP"
                    if now - self.alert_history.get(key, 0) > cfg["alert_cooldown"]:
                        alerts.append({"code": code, "name": name, "severity": "\\U0001f4b0",
                            "type": "止盈", "msg": f"{entry:.2f}->{price:.2f} 盈{pnl:.1f}% 止盈"})
                        self.alert_history[key] = now

            # 急涨急跌
            if code in self.price_cache and len(self.price_cache[code]) >= 2:
                prev = list(self.price_cache[code])[-2]
                if prev > 0:
                    mp = (price - prev) / prev * 100
                    if mp > cfg["price_change_pct"]:
                        key = f"{code}_SU"
                        if now - self.alert_history.get(key, 0) > cfg["alert_cooldown"]:
                            alerts.append({"code": code, "name": name, "severity": "\\U0001f680",
                                "type": "急涨", "msg": f"1min+{mp:.1f}% 突然拉升"})
                            self.alert_history[key] = now
                    elif mp < -cfg["price_change_pct"]:
                        key = f"{code}_SD"
                        if now - self.alert_history.get(key, 0) > cfg["alert_cooldown"]:
                            alerts.append({"code": code, "name": name, "severity": "\\U0001f4c9",
                                "type": "急跌", "msg": f"1min{mp:.1f}% 快速跳水"})
                            self.alert_history[key] = now
        return alerts

    def format_status(self, quotes):
        if not quotes:
            return ""
        lines = []
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        lines.append(f"--- [{now_str}] Monitor ---")
        for stock in self.watch_stocks:
            code = stock["code"]
            name = stock["name"]
            q = quotes.get(code)
            if not q:
                lines.append(f"  {name}({code}): --")
                continue
            pct = q["pct_change"]
            price = q["price"]
            rsi_s = ""
            if code in self.price_cache and len(self.price_cache[code]) >= 15:
                rsi = calc_rsi(list(self.price_cache[code]))
                rsi_s = f" RSI={rsi:.0f}"
            sign = "+" if pct > 0 else "-" if pct < 0 else " "
            lines.append(f"  [{sign}] {name} {price:.2f} ({pct:+.2f}%){rsi_s}")
        return "\\n".join(lines)

    def tick(self):
        """一轮监控"""
        quotes = self.fetch_realtime_snapshot()
        if not quotes:
            self.fetch_latest_bars()
            quotes = {}
            for stock in self.watch_stocks:
                code = stock["code"]
                if code in self.price_cache and self.price_cache[code]:
                    quotes[code] = {"price": self.price_cache[code][-1], "pct_change": 0}
        self.fetch_latest_bars()
        alerts = self.check_alerts(quotes)
        print(self.format_status(quotes))
        if alerts:
            for a in alerts:
                print(f"  [ALERT] {a['severity']} {a['name']}: {a['msg']}")
            self.alert_count += len(alerts)
            return alerts
        return []

    def run(self, hours=None, headless=False):
        if not self.watch_stocks:
            if not self.load_watch_pool():
                print("[ERROR] No watch pool")
                return
        self.init_price_cache()

        if not self._is_trading_time():
            print("[INFO] Outside trading hours (09:15-15:00)")

        self.running = True
        self.start_time = datetime.datetime.now()
        if hours:
            end_time = self.start_time + datetime.timedelta(hours=hours)
            print(f"[MONITOR] {hours}h, {self.interval}s, {len(self.watch_stocks)} stocks")
        else:
            end_time = self.start_time.replace(hour=15, minute=30, second=0)
            print(f"[MONITOR] until {end_time.strftime('%H:%M')}, {self.interval}s, {len(self.watch_stocks)} stocks")

        def handler(sig, frame):
            print("\\n[MONITOR] Stopping...")
            self.running = False
        signal.signal(signal.SIGINT, handler)

        try:
            while self.running:
                if end_time and datetime.datetime.now() > end_time:
                    break
                if not self._is_trading_time():
                    time.sleep(30)
                    continue
                try:
                    self.tick()
                except Exception as e:
                    print(f"[ERROR] {e}")
                    self.server.reconnect()
                time.sleep(self.interval)
        except KeyboardInterrupt:
            pass
        elapsed = (datetime.datetime.now() - self.start_time).total_seconds() / 60
        print(f"\\n[MONITOR] Done. {elapsed:.0f}min, {self.alert_count} alerts")

    def _is_trading_time(self):
        now = datetime.datetime.now()
        if now.weekday() >= 5:
            return False
        t = now.hour * 60 + now.minute
        return (555 <= t <= 690) or (780 <= t <= 900)


def main():
    import argparse
    p = argparse.ArgumentParser(description="盘中实时监控 (pytdx)")
    p.add_argument("--pool", "-p", help="scan_YYYYMMDD.json path")
    p.add_argument("--interval", "-i", type=int, default=3, help="interval sec")
    p.add_argument("--hours", "-t", type=float, help="run hours")
    p.add_argument("--once", "-o", action="store_true", help="single tick")
    p.add_argument("--quiet", "-q", action="store_true", help="headless")
    args = p.parse_args()

    mon = RealtimeMonitor(watch_pool_path=args.pool, interval_sec=args.interval)
    if args.once:
        if not mon.watch_stocks:
            mon.load_watch_pool()
        mon.init_price_cache()
        mon.tick()
    else:
        mon.run(hours=args.hours, headless=args.quiet)


if __name__ == "__main__":
    main()
