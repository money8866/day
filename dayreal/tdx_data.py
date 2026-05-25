from pytdx.hq import TdxHq_API
import time
import threading
import os


class TdxData:
    def __init__(self, config=None):
        self.api = TdxHq_API()
        self.connected = False
        self.concept_stocks_cache = {}
        
        self.servers = [
            # 上海
            ("180.153.18.170", 7709),
            ("180.153.18.171", 7709),
            ("180.153.39.51", 7709),
            # 深圳
            ("119.147.164.60", 7709),
            # 杭州
            ("60.191.117.167", 7709),
            ("218.108.47.69", 7709),
            ("218.108.98.244", 7709),
            # 北京
            ("123.125.108.23", 7709),
            ("123.125.108.24", 7709),
            # 武汉
            ("59.173.18.69", 7709),
            # 南京
            ("221.231.141.60", 7709),
            # 通常比较稳的券商线路
            ("jstdx.gtjas.com", 7709),
        ]
        
        if config:
            custom_servers = config.get('tdx.servers')
            if custom_servers:
                self.servers = [(server['host'], server['port']) for server in custom_servers]
        
        self.best_server = None
        
        # 常用指数
        self.index_list = [
            (1, "000001"),  # 上证指数
            (0, "399001"),  # 深证成指
            (0, "399006"),  # 创业板指
            (0, "399300"),  # 沪深300
        ]

    def _test_server(self, host, port, results):
        """测试单个服务器的连接速度"""
        start_time = time.time()
        try:
            api = TdxHq_API()
            if api.connect(host, port, time_out=3):
                latency = (time.time() - start_time) * 1000
                results.append((host, port, latency))
                api.disconnect()
        except Exception:
            pass

    def find_fastest_server(self):
        """自动查找最快的服务器"""
        print("正在测试通达信服务器连接速度...")
        
        results = []
        threads = []
        
        for host, port in self.servers:
            t = threading.Thread(target=self._test_server, args=(host, port, results))
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join(timeout=5)
        
        if results:
            results.sort(key=lambda x: x[2])
            self.best_server = (results[0][0], results[0][1])
            print(f"最快服务器: {results[0][0]}:{results[0][1]} (延迟: {results[0][2]:.2f}ms)")
            return self.best_server
        else:
            print("未找到可用服务器，使用默认")
            self.best_server = self.servers[0]
            return self.best_server

    def connect(self):
        if not self.connected:
            try:
                if self.best_server is None:
                    self.find_fastest_server()
                
                host, port = self.best_server
                print(f"正在连接 {host}:{port}...")
                self.connected = self.api.connect(host, port)
                if self.connected:
                    print("连接成功")
                return self.connected
            except Exception as e:
                print(f"连接失败: {e}")
                return False
        return True

    def disconnect(self):
        if self.connected:
            self.api.disconnect()
            self.connected = False

    def get_stock_quotes(self, stock_list):
        if not self.connect():
            return None

        try:
            quotes = self.api.get_security_quotes(stock_list)
            return quotes
        except Exception as e:
            print(f"获取实时行情失败: {e}")
            self.disconnect()
            return None

    def get_concept_stocks(self, concept_name):
        if concept_name in self.concept_stocks_cache:
            return self.concept_stocks_cache[concept_name]
            
        if not self.connect():
            return []

        for retry in range(3):
            try:
                blocks = self.api.get_and_parse_block_info(concept_name)
                if blocks:
                    stock_list = [(1, stock['code']) if stock['code'].startswith('6') else (0, stock['code']) 
                                 for stock in blocks]
                    self.concept_stocks_cache[concept_name] = stock_list
                    return stock_list
                return []
            except Exception as e:
                if retry < 2:
                    print(f"获取概念板块股票失败(重试 {retry+1}/3): {e}")
                    self.api.disconnect()
                    time.sleep(0.5)
                    self.connect()
                else:
                    print(f"获取概念板块股票失败: {e}")
                    return []

    def get_history_kline(self, market, code, category=9, count=10):
        if not self.connect():
            return None

        try:
            klines = self.api.get_security_bars(category, market, code, 0, count)
            return klines
        except Exception as e:
            print(f"获取历史K线失败: {e}")
            return None
    
    def get_index_quotes(self):
        """
        获取主要指数行情
        
        Returns:
            指数行情列表
        """
        if not self.connect():
            return None
        
        try:
            return self.api.get_security_quotes(self.index_list)
        except Exception as e:
            print(f"获取指数行情失败: {e}")
            return None
    
    def calculate_index_change(self, index_quotes=None):
        """
        计算主要指数的平均涨跌幅
        
        Args:
            index_quotes: 指数行情数据
            
        Returns:
            平均涨跌幅（百分比）
        """
        if index_quotes is None:
            index_quotes = self.get_index_quotes()
            
        if not index_quotes:
            return 0.0
        
        total_change = 0.0
        count = 0
        
        for quote in index_quotes:
            price = quote.get("price", 0)
            last_close = quote.get("last_close", 0)
            
            if last_close > 0:
                change_pct = (price - last_close) / last_close * 100
                total_change += change_pct
                count += 1
        
        if count == 0:
            return 0.0
        
        return total_change / count
