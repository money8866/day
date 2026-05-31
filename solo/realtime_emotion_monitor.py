"""
实时大盘情绪监控系统
功能：
1. 每5分钟轮巡大盘指数（上证、深证、创业板）
2. 实时计算MA5/MA10/MA20/MA60均线状态
3. 动态计算情绪指数
4. 根据情绪和均线状态调整仓位建议
5. 市场发生重大变化时推送微信提醒
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import time
import numpy as np
from datetime import datetime, timedelta
import requests
import json
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config', '.env'))

SERVERCHAN_KEY = os.getenv('SERVERCHAN_KEY', '')

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


class RealtimeEmotionMonitor:
    def __init__(self):
        self.api = None
        self.connected = False
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
        
        self.baseline_data = {}
        
        self.emotion_cache = {
            'last_update': None,
            'emotion_score': 50,
            'trend_score': 50,
            'position': 50,
            'ma_status': '初始化中'
        }
    
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
                
                test_ok = False
                for market, code in [(0, "000001"), (1, "399001")]:
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
        if self.connected and self.api:
            try:
                self.api.disconnect()
            except:
                pass
            self.connected = False
    
    def get_index_quotes_tdx(self):
        indices = [
            (0, "000001", "上证指数"),
            (0, "000300", "沪深300"),
            (1, "399001", "深证成指"),
            (1, "399006", "创业板指"),
        ]
        
        quotes = {}
        for market, code, name in indices:
            try:
                data = self.api.get_security_bars(9, market, code, 0, 90)
                if data and len(data) > 0:
                    df = self._parse_kline_data(data)
                    quotes[name] = df
            except Exception as e:
                print(f"获取{name}数据失败: {e}")
        
        return quotes
    
    def get_index_quotes_tushare(self):
        try:
            today = datetime.now().strftime('%Y%m%d')
            indices = {
                '000001.SH': '上证指数',
                '000300.SH': '沪深300',
                '399001.SZ': '深证成指',
                '399006.SZ': '创业板指',
            }
            
            quotes = {}
            for ts_code, name in indices.items():
                df = pro.index_daily(ts_code=ts_code, start_date='20240101', end_date=today)
                if df is not None and not df.empty:
                    df = df.sort_values('trade_date').tail(90)
                    quotes[name] = df
                time.sleep(0.1)
            
            return quotes
        except Exception as e:
            print(f"Tushare获取指数数据失败: {e}")
            return {}
    
    def _parse_kline_data(self, data):
        import pandas as pd
        if not data:
            return pd.DataFrame()
        
        df = pd.DataFrame(data)
        if 'date' in df.columns:
            df['trade_date'] = pd.to_datetime(df['date']).dt.strftime('%Y%m%d')
        if 'close' in df.columns:
            df['close'] = df['close'].astype(float)
        
        return df
    
    def calculate_realtime_emotion(self, quotes):
        if not quotes or '上证指数' not in quotes:
            return self.emotion_cache
        
        sh_df = quotes['上证指数']
        if len(sh_df) < 60:
            return self.emotion_cache
        
        closes = sh_df['close'].values
        current_price = closes[-1]
        
        ma5 = np.mean(closes[-5:])
        ma10 = np.mean(closes[-10:])
        ma20 = np.mean(closes[-20:])
        ma60 = np.mean(closes[-60:])
        
        ma5_prev = np.mean(closes[-6:-1]) if len(closes) >= 6 else ma5
        ma10_prev = np.mean(closes[-11:-1]) if len(closes) >= 11 else ma10
        ma20_prev = np.mean(closes[-21:-1]) if len(closes) >= 21 else ma20
        ma60_prev = np.mean(closes[-61:-1]) if len(closes) >= 61 else ma60
        
        above_ma5 = current_price >= ma5
        above_ma10 = current_price >= ma10
        above_ma20 = current_price >= ma20
        above_ma60 = current_price >= ma60
        
        ma5_up = ma5 > ma5_prev
        ma10_up = ma10 > ma10_prev
        ma20_up = ma20 > ma20_prev
        ma60_up = ma60 > ma60_prev
        
        ma多头 = above_ma5 and above_ma10 and above_ma20 and above_ma60
        ma空头 = not above_ma5 and not above_ma10 and not above_ma20 and not above_ma60
        
        ma_status = "多头排列" if ma多头 else \
                    "空头排列" if ma空头 else \
                    "短期偏弱" if not above_ma5 else \
                    "中期偏弱" if not above_ma20 else \
                    "长期偏弱"
        
        trend_score = 50
        
        if above_ma5:
            trend_score += 12
        else:
            trend_score -= 18
        
        if ma5_up:
            trend_score += 8
        else:
            trend_score -= 10
        
        if above_ma20:
            trend_score += 10
        else:
            trend_score -= 15
        
        if ma20_up:
            trend_score += 8
        else:
            trend_score -= 12
        
        if above_ma60:
            trend_score += 15
        else:
            trend_score -= 20
        
        if ma60_up:
            trend_score += 10
        else:
            trend_score -= 15
        
        if ma多头:
            trend_score += 15
        if ma空头:
            trend_score -= 20
        
        trend_score = max(0, min(100, trend_score))
        
        base_score = 20
        zt_score = 15
        dt_score = 10
        
        emotion_score = base_score + zt_score - dt_score
        
        if not above_ma5:
            emotion_score *= 0.55
        elif not above_ma20:
            emotion_score *= 0.70
        elif not above_ma60:
            emotion_score *= 0.85
        elif trend_score < 50:
            emotion_score *= 0.65
        elif trend_score < 35:
            emotion_score *= 0.45
        
        emotion_score = max(0, min(100, emotion_score))
        
        if trend_score >= 70:
            position = 70
        elif trend_score >= 50:
            position = 50
        elif trend_score >= 35:
            position = 35
        elif trend_score >= 20:
            position = 20
        else:
            position = 10
        
        if emotion_score < 30:
            position = int(position * 0.6)
        elif emotion_score < 50:
            position = int(position * 0.8)
        
        self.emotion_cache = {
            'last_update': datetime.now().strftime('%H:%M:%S'),
            'emotion_score': emotion_score,
            'trend_score': trend_score,
            'position': position,
            'ma_status': ma_status,
            'current_price': current_price,
            'ma5': ma5,
            'ma10': ma10,
            'ma20': ma20,
            'ma60': ma60,
            'above_ma5': above_ma5,
            'above_ma20': above_ma20,
            'above_ma60': above_ma60,
            'pct_chg': ((current_price / closes[-2]) - 1) * 100 if len(closes) >= 2 else 0
        }
        
        return self.emotion_cache
    
    def should_notify(self, key, min_interval=300):
        now = time.time()
        if key not in self.last_notification_time:
            return True
        
        if now - self.last_notification_time[key] >= min_interval:
            return True
        
        return False
    
    def send_notification(self, title, content):
        if not SERVERCHAN_KEY:
            print(f"[{title}] {content}")
            return False
        
        try:
            url = f"https://sctapi.ftqq.com/{SERVERCHAN_KEY}.send"
            data = {
                "title": title,
                "desp": content
            }
            resp = requests.post(url, data=data, timeout=10)
            result = resp.json()
            if result.get('code') == 0 or result.get('data', {}).get('error') == 0:
                print(f"✓ 微信推送成功: {title}")
                return True
            else:
                print(f"✗ 微信推送失败: {result}")
                return False
        except Exception as e:
            print(f"✗ 微信推送异常: {e}")
            return False
    
    def check_and_notify(self, emotion_data):
        changes = []
        
        if 'prev_emotion' in self.baseline_data:
            prev = self.baseline_data['prev_emotion']
            curr = emotion_data
            
            emotion_diff = curr['emotion_score'] - prev['emotion_score']
            trend_diff = curr['trend_score'] - prev['trend_score']
            
            if abs(emotion_diff) >= 15:
                if emotion_diff > 0:
                    changes.append(f"情绪指数大幅上升 {emotion_diff:+.1f}分")
                else:
                    changes.append(f"情绪指数大幅下降 {emotion_diff:+.1f}分")
            
            if abs(trend_diff) >= 20:
                if trend_diff > 0:
                    changes.append(f"趋势评分大幅上升 {trend_diff:+.1f}分")
                else:
                    changes.append(f"趋势评分大幅下降 {trend_diff:+.1f}分")
            
            if curr['ma_status'] != prev.get('ma_status'):
                changes.append(f"均线状态改变: {prev.get('ma_status')} → {curr['ma_status']}")
            
            if not prev['above_ma5'] and curr['above_ma5']:
                changes.append("🎯 突破5日均线！")
            elif prev['above_ma5'] and not curr['above_ma5']:
                changes.append("⚠️ 跌破5日均线！")
            
            if not prev['above_ma20'] and curr['above_ma20']:
                changes.append("🚀 突破20日均线！")
            elif prev['above_ma20'] and not curr['above_ma20']:
                changes.append("🔻 跌破20日均线！")
        
        if changes:
            title = f"📊 实时情绪变化 {datetime.now().strftime('%H:%M')}"
            content = "\n".join(changes)
            content += f"\n\n当前状态:"
            content += f"\n情绪指数: {emotion_data['emotion_score']:.1f}"
            content += f"\n趋势评分: {emotion_data['trend_score']:.1f}"
            content += f"\n均线状态: {emotion_data['ma_status']}"
            content += f"\n建议仓位: {emotion_data['position']}%"
            
            if self.should_notify('emotion_change', min_interval=600):
                self.send_notification(title, content)
                self.last_notification_time['emotion_change'] = time.time()
        
        self.baseline_data['prev_emotion'] = emotion_data.copy()
    
    def print_status(self, emotion_data):
        print(f"\n{'='*80}")
        print(f"📊 实时大盘情绪监控 | 更新时间: {emotion_data['last_update']}")
        print(f"{'='*80}")
        
        print(f"\n【上证指数】")
        print(f"  当前价格: {emotion_data['current_price']:.2f}")
        print(f"  涨跌幅: {emotion_data['pct_chg']:+.2f}%")
        
        print(f"\n【均线状态】")
        print(f"  MA5:  {emotion_data['ma5']:8.2f}  {'✓' if emotion_data['above_ma5'] else '✗'}")
        print(f"  MA10: {emotion_data['ma10']:8.2f}  {'✓' if emotion_data['above_ma10'] else '✗'}")
        print(f"  MA20: {emotion_data['ma20']:8.2f}  {'✓' if emotion_data['above_ma20'] else '✗'}")
        print(f"  MA60: {emotion_data['ma60']:8.2f}  {'✓' if emotion_data['above_ma60'] else '✗'}")
        
        print(f"\n【情绪指标】")
        print(f"  情绪指数: {emotion_data['emotion_score']:5.1f}")
        print(f"  趋势评分: {emotion_data['trend_score']:5.1f}")
        print(f"  均线状态: {emotion_data['ma_status']}")
        
        print(f"\n【仓位建议】")
        print(f"  🎯 推荐仓位: {emotion_data['position']:2d}%")
        
        if emotion_data['position'] >= 70:
            print(f"  📈 市场强势，可适当提高仓位")
        elif emotion_data['position'] >= 50:
            print(f"  📊 市场震荡，控制仓位为主")
        elif emotion_data['position'] >= 35:
            print(f"  ⚠️ 市场偏弱，轻仓谨慎操作")
        else:
            print(f"  🔻 市场弱势，多看少动")
        
        print(f"{'='*80}")
    
    def run(self, interval=300):
        print("="*80)
        print("🚀 实时大盘情绪监控系统启动")
        print("="*80)
        print(f"监控间隔: {interval}秒 (5分钟)")
        print(f"数据来源: {'通达信' if self.data_source == 'tdx' else 'Tushare'}")
        print("="*80)
        
        if not self.connect():
            print("✗ 无法连接数据源，系统退出")
            return
        
        try:
            while True:
                now = datetime.now()
                
                if now.hour < 9 or (now.hour == 9 and now.minute < 30):
                    print(f"\r[{now.strftime('%H:%M:%S')}] 盘前时间，等待中...", end='', flush=True)
                    time.sleep(30)
                    continue
                
                if now.hour >= 15:
                    print(f"\n[{now.strftime('%H:%M:%S')}] 收盘了，系统进入休眠模式")
                    break
                
                if self.data_source == 'tdx':
                    quotes = self.get_index_quotes_tdx()
                else:
                    quotes = self.get_index_quotes_tushare()
                
                if quotes:
                    emotion_data = self.calculate_realtime_emotion(quotes)
                    self.print_status(emotion_data)
                    self.check_and_notify(emotion_data)
                else:
                    print(f"\n[{now.strftime('%H:%M:%S')}] 获取数据失败，等待重试...")
                
                time.sleep(interval)
        
        except KeyboardInterrupt:
            print("\n\n用户中断，系统退出")
        finally:
            self.disconnect()
            print("已断开连接")


if __name__ == "__main__":
    # 使用说明：
    # - 默认每5分钟更新一次
    # - 可以自定义间隔时间：monitor.run(interval=300)  # 5分钟
    # - 盘中实时监控：每5分钟自动获取最新行情，计算情绪和仓位建议
    monitor = RealtimeEmotionMonitor()
    monitor.run(interval=300)
