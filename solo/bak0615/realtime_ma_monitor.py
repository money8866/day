"""
实时均线监控系统
功能：监控盘后选出的策略个股，在股价下跌到10日/20日均线时触发买入信号
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import time
from datetime import datetime, timedelta
import requests
import json
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config', '.env'))

try:
    import tushare as ts
    pro = ts.pro_api(os.getenv('TUSHARE_TOKEN'))
    TUSHARE_AVAILABLE = True
except:
    TUSHARE_AVAILABLE = False
    print("Tushare未配置，将使用通达信")

if TUSHARE_AVAILABLE:
    try:
        from pytdx.hq import TdxHq_API
        TDx_AVAILABLE = True
    except:
        TDx_AVAILABLE = False
else:
    TDx_AVAILABLE = False


class RealTimeMonitor:
    def __init__(self):
        self.api = None
        self.connected = False
        self.signal_history = {}
        self.last_notification_time = {}
        self.data_source = None
        self.best_server = None
        
        self.servers = [
            ("180.153.18.170", 7709),
            ("180.153.18.171", 7709),
            ("180.153.39.51", 7709),
            ("119.147.164.60", 7709),
            ("60.191.117.167", 7709),
            ("218.108.47.69", 7709),
            ("218.108.98.244", 7709),
            ("123.125.108.23", 7709),
            ("123.125.108.24", 7709),
            ("59.173.18.69", 7709),
            ("221.231.141.60", 7709),
        ]
        
        if 'TDx_AVAILABLE' in globals() and TDx_AVAILABLE:
            self.api = TdxHq_API()
    
    def find_fastest_server(self):
        if not TDx_AVAILABLE:
            return
        print("正在测试通达信服务器连接速度...")
        results = []
        import threading
        
        def _test_server(host, port, res):
            try:
                api = TdxHq_API()
                start = time.time()
                if not api.connect(host, port, timeout=3):
                    return
                latency = (time.time() - start) * 1000
                
                # 双市场验证
                test_ok = False
                for market, code in [(0, "600000"), (1, "000001")]:
                    data = api.get_security_bars(9, market, code, 0, 5)
                    if data:
                        test_ok = True
                        break
                
                if not test_ok:
                    return
                
                res.append((host, port, latency))
            except:
                pass
            finally:
                try:
                    api.disconnect()
                except:
                    pass
        
        threads = []
        for host, port in self.servers:
            t = threading.Thread(target=_test_server, args=(host, port, results))
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join(timeout=5)
        
        if results:
            results.sort(key=lambda x: x[2])
            self.best_server = (results[0][0], results[0][1])
            print(f"最快服务器: {results[0][0]}:{results[0][1]} (延迟: {results[0][2]:.2f}ms)")
        else:
            print("未找到可用服务器，使用默认")
            self.best_server = self.servers[0]
    
    def connect(self):
        if self.connected:
            return True
        
        if TDx_AVAILABLE:
            try:
                if self.best_server is None:
                    self.find_fastest_server()
                
                host, port = self.best_server
                print(f"正在连接 {host}:{port}...")
                self.connected = self.api.connect(host, port)
                if self.connected:
                    self.data_source = 'tdx'
                    print("连接成功")
                    return True
            except Exception as e:
                print(f"连接失败: {e}")
        
        if TUSHARE_AVAILABLE:
            self.data_source = 'tushare'
            print("通达信连接失败，使用Tushare获取实时数据")
            return True
        
        print("✗ 无法连接到数据源")
        return False
    
    def disconnect(self):
        if self.connected:
            self.api.disconnect()
            self.connected = False
    
    def get_stock_quotes(self, stock_list):
        if self.data_source == 'tushare':
            return self.get_tushare_quotes(stock_list)
        
        if not self.connect():
            return None
        try:
            return self.api.get_security_quotes(stock_list)
        except Exception as e:
            print(f"获取行情失败: {e}")
            self.connected = False
            return None
    
    def get_tushare_quotes(self, stock_list):
        if not TUSHARE_AVAILABLE:
            return None
        try:
            codes = []
            for _, code in stock_list:
                if code.startswith('6'):
                    codes.append(code.replace('.SH', '') + '.SH')
                else:
                    codes.append(code.replace('.SZ', '') + '.SZ')
            
            quotes = []
            for code in codes[:50]:
                try:
                    today = datetime.now().strftime('%Y%m%d')
                    df = pro.daily(ts_code=code, start_date=today, end_date=today)
                    if df is not None and len(df) > 0:
                        quotes.append({
                            'code': code.replace('.SH', '').replace('.SZ', ''),
                            'close': float(df.iloc[0]['close']),
                            'pct_chg': float(df.iloc[0]['pct_chg']),
                        })
                except:
                    continue
            return quotes if quotes else None
        except Exception as e:
            print(f"Tushare获取行情失败: {e}")
            return None
    
    def disconnect(self):
        if self.api and self.connected:
            self.api.disconnect()
            self.connected = False
    
    def get_history_kline(self, market, code, count=30):
        if not self.connect():
            return None
        try:
            return self.api.get_security_bars(9, market, code, 0, count)
        except:
            return None
    
    def calculate_ma(self, klines, period):
        if not klines or len(klines) < period:
            return None
        closes = [float(k['close']) for k in klines]
        return sum(closes[-period:]) / period
    
    def load_watched_stocks(self):
        watched_stocks = []
        seen_keys = set()
        
        cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache_backbone_tushare')
        
        csv_files = [
            'theme_leaders_final_20260527.csv',
            'daily_review_20260527.txt'
        ]
        
        for csv_file in csv_files:
            file_path = os.path.join(cache_dir, csv_file)
            if os.path.exists(file_path):
                print(f"从 {csv_file} 加载股票...")
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    lines = content.strip().split('\n')
                    for line in lines:
                        for pattern in ['.SH', '.SZ']:
                            if pattern in line:
                                parts = line.split(pattern)
                                if len(parts) >= 1:
                                    code_part = parts[0].split(',')[-1].strip()
                                    code = code_part + pattern
                                    name = parts[0].split(',')[0].strip() if ',' in parts[0] else ''
                                    market = 1 if code.startswith('6') else 0
                                    key = (market, code)
                                    if key not in seen_keys:
                                        seen_keys.add(key)
                                        watched_stocks.append({
                                            'code': code,
                                            'market': market,
                                            'name': name,
                                            'source': csv_file
                                        })
        
        return [(s['market'], s['code'], s['name'], s['source']) for s in watched_stocks]
    
    def check_ma_signals(self, market, code, name, quotes_map):
        key = f"{code}"
        
        if key not in quotes_map:
            return None
        
        quote = quotes_map[key]
        current_price = float(quote.get('price', 0))
        last_close = float(quote.get('last_close', 0))
        
        if current_price <= 0 or last_close <= 0:
            return None
        
        klines = self.get_history_kline(market, code, count=30)
        if not klines or len(klines) < 25:
            return None
        
        ma10 = self.calculate_ma(klines, 10)
        ma20 = self.calculate_ma(klines, 20)
        
        if ma10 is None or ma20 is None:
            return None
        
        ma10_distance = (current_price - ma10) / ma10 * 100
        ma20_distance = (current_price - ma20) / ma20 * 100
        
        signals = []
        
        threshold = -2.0
        
        if ma10_distance <= threshold:
            signal = {
                'type': 'MA10买入',
                'price': current_price,
                'ma': ma10,
                'distance': ma10_distance,
                'last_close': last_close,
                'change_pct': (current_price - last_close) / last_close * 100
            }
            signals.append(signal)
        
        if ma20_distance <= threshold:
            signal = {
                'type': 'MA20买入',
                'price': current_price,
                'ma': ma20,
                'distance': ma20_distance,
                'last_close': last_close,
                'change_pct': (current_price - last_close) / last_close * 100
            }
            signals.append(signal)
        
        return signals if signals else None
    
    def should_notify(self, code, signal_type):
        now = datetime.now()
        
        if code not in self.last_notification_time:
            return True
        
        last_time = self.last_notification_time[code]
        time_diff = (now - last_time).total_seconds()
        
        cooldown = 1800
        
        return time_diff >= cooldown
    
    def send_notification(self, name, code, signals):
        sckey = os.getenv("WECHAT_SCKEY")
        if not sckey:
            print("未配置微信推送")
            return
        
        now = datetime.now().strftime('%H:%M:%S')
        
        signal_details = []
        for sig in signals:
            signal_details.append(
                f"- {sig['type']}: 价格{sig['price']:.2f}元 (距{sig['ma']:.2f}元 {sig['distance']:+.2f}%, 今日{sig['change_pct']:+.2f}%)"
            )
        
        title = f"【均线信号】{name} {now}"
        message = f"""## {name} ({code})

⏰ 时间: {now}

### 买入信号:
{chr(10).join(signal_details)}

💡 建议: 股价触及均线支撑位，关注低吸机会

⚠️ 注意: 控制仓位，设置止损
"""
        
        url = f"https://sctapi.ftqq.com/{sckey}.send"
        try:
            requests.post(url, data={"title": title, "desp": message}, timeout=10)
            print(f"✓ 微信推送成功: {name} {signals[0]['type']}")
        except Exception as e:
            print(f"推送失败: {e}")
    
    def run(self, check_interval=60):
        print("="*60)
        print("实时均线监控系统")
        print("监控条件: 股价下跌至10日/20日均线附近")
        print("触发冷却: 1小时内不重复提醒")
        print("="*60)
        print(f"TDx_AVAILABLE: {TDx_AVAILABLE}")
        print(f"TUSHARE_AVAILABLE: {TUSHARE_AVAILABLE}")
        
        watched_stocks = self.load_watched_stocks()
        
        if not watched_stocks:
            print("未找到监控股票，请先运行盘后分析")
            return
        
        print(f"\n共监控 {len(watched_stocks)} 只股票\n")
        
        for stock in watched_stocks[:20]:
            print(f"  {stock[2]:10s} ({stock[1]})")
        
        if not self.connect():
            return
        
        print(f"\n开始监控，检查间隔: {check_interval}秒")
        print("(按 Ctrl+C 停止)\n")
        
        cooldown_seconds = 3600
        
        try:
            while True:
                now = datetime.now()
                
                if not (9 <= now.hour < 15) or (now.hour == 11 and now.minute > 30) or now.hour == 12:
                    time.sleep(5)
                    continue
                
                stock_list = [(s[0], s[1]) for s in watched_stocks]
                quotes = self.get_stock_quotes(stock_list)
                
                if not quotes:
                    time.sleep(check_interval)
                    continue
                
                quotes_map = {q.get('code', ''): q for q in quotes}
                
                batch_signals = []
                
                for stock in watched_stocks:
                    market, code, name, source = stock
                    key = code
                    
                    last_time = self.last_notification_time.get(key)
                    if last_time and (now - last_time).total_seconds() < cooldown_seconds:
                        continue
                    
                    signals = self.check_ma_signals(market, code, name, quotes_map)
                    
                    if signals:
                        self.last_notification_time[key] = now
                        batch_signals.append({
                            'name': name,
                            'code': code,
                            'signals': signals
                        })
                
                if batch_signals:
                    self.send_batch_notification(batch_signals)
                    print(f"\n[{now.strftime('%H:%M:%S')}] 触发 {len(batch_signals)} 个信号并推送微信:")
                    for sig in batch_signals:
                        print(f"  {sig['name']} ({sig['code']}): {sig['signals'][0]['type']}")
                
                time.sleep(check_interval)
                
        except KeyboardInterrupt:
            print("\n停止监控")
        finally:
            self.disconnect()
            print("已断开连接")
    
    def send_batch_notification(self, signals):
        sckey = os.getenv("WECHAT_SCKEY")
        if not sckey:
            print("未配置微信推送")
            return
        
        now = datetime.now().strftime('%H:%M:%S')
        
        signal_lines = []
        for i, sig in enumerate(signals, 1):
            sig_details = []
            for s in sig['signals']:
                sig_details.append(f"{s['type']}: {s['price']:.2f}元 (距{s['ma']:.2f}元 {s['distance']:+.2f}%)")
            signal_lines.append(f"{i}. **{sig['name']}** ({sig['code']})")
            signal_lines.append(f"   {sig_details[0]}")
        
        title = f"【均线信号】{len(signals)}只个股触及均线 {now}"
        message = f"""## 均线买入信号通知

⏰ 时间: {now}
📊 触发个股: {len(signals)} 只

---

{chr(10).join(signal_lines)}

---

💡 建议: 股价触及均线支撑位，关注低吸机会

⚠️ 注意: 控制仓位，设置止损
"""
        
        url = f"https://sctapi.ftqq.com/{sckey}.send"
        try:
            requests.post(url, data={"title": title, "desp": message}, timeout=10)
            print(f"✓ 微信批量推送成功: {len(signals)} 只个股")
        except Exception as e:
            print(f"推送失败: {e}")


if __name__ == "__main__":
    monitor = RealTimeMonitor()
    monitor.run(check_interval=60)
