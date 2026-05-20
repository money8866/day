import tushare as ts
import pandas as pd
import numpy as np
import sqlite3
import json
import pickle
from datetime import datetime, timedelta
import requests
import time
from pathlib import Path
from functools import wraps

import os
from dotenv import load_dotenv
# =====================================
# 环境变量
# =====================================
load_dotenv()
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
WECHAT_SCKEY = os.getenv("WECHAT_SCKEY")
pro = ts.pro_api()

# DeepSeek API配置
DEEPSEEK_API_KEY = "your_deepseek_api_key"
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

def rate_limit(max_per_minute=100):
    """速率限制装饰器"""
    interval = 60.0 / max_per_minute
    
    def decorator(func):
        last_called = [0.0]
        
        @wraps(func)
        def wrapper(*args, **kwargs):
            elapsed = time.time() - last_called[0]
            left_to_wait = interval - elapsed
            if left_to_wait > 0:
                time.sleep(left_to_wait)
            ret = func(*args, **kwargs)
            last_called[0] = time.time()
            return ret
        return wrapper
    return decorator

class StockCache:
    """股票数据缓存管理"""
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    CACHE_DIR = os.path.join(BASE_DIR, "cache_daily")
    def __init__(self, cache_dir=CACHE_DIR):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        self.daily_db = self.cache_dir / 'daily_data.db'
        self.info_db = self.cache_dir / 'stock_info.db'
        
        # 初始化SQLite数据库
        self._init_daily_db()
        self._init_info_db()
    
    def _init_daily_db(self):
        conn = sqlite3.connect(self.daily_db)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS daily_quotes (
                ts_code TEXT, trade_date TEXT, 
                open REAL, high REAL, low REAL, close REAL, vol REAL,
                PRIMARY KEY (ts_code, trade_date)
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_ts_code ON daily_quotes(ts_code)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_trade_date ON daily_quotes(trade_date)')
        conn.close()
    
    def _init_info_db(self):
        conn = sqlite3.connect(self.info_db)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS stock_basic (
                ts_code TEXT PRIMARY KEY, name TEXT, industry TEXT,
                market TEXT, list_date TEXT, update_date TEXT
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS financial_info (
                ts_code TEXT PRIMARY KEY, 
                eps REAL, pe REAL, pb REAL, roe REAL,
                total_mv REAL, circ_mv REAL,
                update_date TEXT
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS events_info (
                ts_code TEXT PRIMARY KEY,
                has_reduction INTEGER, has_placement INTEGER,
                update_date TEXT
            )
        ''')
        conn.close()
    
    @rate_limit(max_per_minute=100)  # 限制100次/分钟
    def _fetch_daily_batch(self, ts_codes, start_date, end_date):
        """批量获取日线数据（带速率限制）"""
        try:
            # 批量获取多个股票的日线数据
            ts_codes_str = ','.join(ts_codes)
            df = pro.daily(ts_code=ts_codes_str, start_date=start_date, 
                          end_date=end_date, fields='ts_code,trade_date,open,high,low,close,vol')
            return df
        except Exception as e:
            print(f"批量获取日线数据失败: {e}")
            return pd.DataFrame()
    
    def get_daily_data_batch(self, ts_codes, start_date, end_date):
        """批量获取并缓存日线数据"""
        conn = sqlite3.connect(self.daily_db)
        
        # 检查哪些股票的数据需要更新
        need_fetch = []
        for ts_code in ts_codes:
            # 检查最近30天是否有数据
            check_date = (datetime.now() - timedelta(days=30)).strftime('%Y%m%d')
            result = conn.execute(
                "SELECT COUNT(*) FROM daily_quotes WHERE ts_code=? AND trade_date>=?",
                (ts_code, check_date)
            ).fetchone()
            
            if result[0] < 20:  # 最近30天数据不足20条，需要重新获取
                need_fetch.append(ts_code)
        
        conn.close()
        
        # 分批获取缺失的数据（每批20只股票，避免接口超限）
        if need_fetch:
            batch_size = 20
            for i in range(0, len(need_fetch), batch_size):
                batch = need_fetch[i:i+batch_size]
                print(f"批量获取第{i//batch_size + 1}批数据，共{len(batch)}只股票...")
                
                df = self._fetch_daily_batch(batch, start_date, end_date)
                
                if not df.empty:
                    conn = sqlite3.connect(self.daily_db)
                    # 使用replace方式避免重复
                    for ts_code in batch:
                        stock_df = df[df['ts_code'] == ts_code]
                        if not stock_df.empty:
                            stock_df.to_sql('daily_quotes', conn, 
                                          if_exists='append', index=False)
                    conn.close()
                
                # 批次间延迟
                time.sleep(0.5)
        
        # 从缓存读取所有数据
        conn = sqlite3.connect(self.daily_db)
        placeholders = ','.join(['?'] * len(ts_codes))
        query = f"""
            SELECT ts_code, trade_date, open, high, low, close, vol 
            FROM daily_quotes 
            WHERE ts_code IN ({placeholders}) AND trade_date>=? AND trade_date<=?
            ORDER BY ts_code, trade_date
        """
        params = ts_codes + [start_date, end_date]
        df = pd.read_sql_query(query, conn, params=params)
        conn.close()
        
        return df
    
    @rate_limit(max_per_minute=50)  # 财务信息接口限制更严格
    def _fetch_financial_batch(self, ts_codes):
        """批量获取财务信息"""
        try:
            # 批量获取估值指标
            ts_codes_str = ','.join(ts_codes[:50])  # 限制最多50只
            daily = pro.daily_basic(ts_code=ts_codes_str,
                                   trade_date=datetime.now().strftime('%Y%m%d'),
                                   fields='ts_code,pe,pe_ttm,pb,total_mv,circ_mv')
            return daily
        except Exception as e:
            print(f"批量获取财务信息失败: {e}")
            return pd.DataFrame()
    
    def get_financial_info_batch(self, ts_codes):
        """批量获取财务信息并缓存"""
        conn = sqlite3.connect(self.info_db)
        
        # 筛选需要更新的股票
        need_update = []
        for ts_code in ts_codes:
            check = conn.execute(
                "SELECT update_date FROM financial_info WHERE ts_code=?", 
                (ts_code,)
            ).fetchone()
            
            if not check or (datetime.now() - datetime.strptime(check[0], '%Y%m%d')).days >= 7:
                need_update.append(ts_code)
        
        conn.close()
        
        if need_update:
            # 分批更新财务信息
            batch_size = 30
            for i in range(0, len(need_update), batch_size):
                batch = need_update[i:i+batch_size]
                df = self._fetch_financial_batch(batch)
                
                if not df.empty:
                    df['update_date'] = datetime.now().strftime('%Y%m%d')
                    conn = sqlite3.connect(self.info_db)
                    for _, row in df.iterrows():
                        conn.execute("""
                            INSERT OR REPLACE INTO financial_info 
                            (ts_code, pe, pb, total_mv, circ_mv, update_date)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (row['ts_code'], row['pe'], row['pb'], 
                             row['total_mv'], row['circ_mv'], row['update_date']))
                    conn.commit()
                    conn.close()
                
                time.sleep(0.5)
        
        # 读取所有财务信息
        conn = sqlite3.connect(self.info_db)
        placeholders = ','.join(['?'] * len(ts_codes))
        query = f"""
            SELECT ts_code, pe, pb, total_mv, circ_mv, update_date
            FROM financial_info 
            WHERE ts_code IN ({placeholders})
        """
        df = pd.read_sql_query(query, conn, params=ts_codes)
        conn.close()
        
        return df

class StockScreener:
    def __init__(self):
        self.cache = StockCache()
    
    def get_stock_list(self):
        """获取符合条件的股票池（ST条件筛选）"""
        print("正在获取股票基本信息...")
        
        # 使用缓存获取股票列表（每天更新一次）
        cache_file = Path('stock_cache/stock_list.pkl')
        today = datetime.now().strftime('%Y%m%d')
        
        if cache_file.exists():
            with open(cache_file, 'rb') as f:
                cached_data = pickle.load(f)
                if cached_data.get('date') == today:
                    print(f"从缓存读取股票列表，共{len(cached_data['stocks'])}只")
                    return cached_data['stocks']
        
        # 获取所有股票列表（使用缓存减少请求）
        all_stocks = pro.stock_basic(exchange='', list_status='L', 
                                     fields='ts_code,name,industry,market,list_date')
        
        # ST条件筛选
        def is_st_stock(name):
            if pd.isna(name):
                return True
            name_str = str(name)
            return 'ST' in name_str or '*ST' in name_str or 'st' in name_str
        
        def is_gem_or_star(ts_code):
            return ts_code.startswith('300') or ts_code.startswith('301') or ts_code.startswith('688')
        
        all_stocks['is_st'] = all_stocks['name'].apply(is_st_stock)
        all_stocks['is_gem_star'] = all_stocks['ts_code'].apply(is_gem_or_star)
        
        filtered = all_stocks[~all_stocks['is_st'] & all_stocks['is_gem_star']]
        stock_list = filtered['ts_code'].tolist()
        
        # 缓存结果
        with open(cache_file, 'wb') as f:
            pickle.dump({'date': today, 'stocks': stock_list}, f)
        
        print(f"共筛选出{len(stock_list)}只符合条件的股票（非ST+创业板/科创板）")
        return stock_list
    
    def calculate_indicators(self, stock_data):
        """计算公式中的技术指标"""
        if stock_data.empty or len(stock_data) < 60:
            return None
        
        df = stock_data.sort_values('trade_date').reset_index(drop=True)
        
        # 转换数据类型
        for col in ['close', 'vol', 'high', 'low', 'open']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # 检查是否有缺失值
        if df[['close', 'vol', 'high', 'low']].isnull().any().any():
            return None
        
        try:
            # ZT条件：涨停板识别
            df['prev_2'] = df['close'].shift(2)
            df['prev_1'] = df['close'].shift(1)
            cond1 = (df['prev_1'] / df['prev_2']) < 1.08
            
            cond2 = (df['close'] / df['prev_1']) > 1.102
            
            df['vol_ma60'] = df['vol'].rolling(window=60, min_periods=1).mean()
            cond3 = (df['vol'] / df['vol_ma60']) > 1.5
            
            df['ZT'] = (cond1 & cond2 & cond3).astype(int)
            
            # 找出最近一次涨停的位置
            zt_positions = df[df['ZT'] == 1].index.tolist()
            if not zt_positions:
                return None
            
            latest_zt = max(zt_positions)
            current_idx = len(df) - 1
            ztts = current_idx - latest_zt
            
            if ztts <= 2 or ztts > 30:
                return None
            
            # TJ条件1: COUNT(C < REF(C, ZTTS+1), ZTTS) = 0
            ref_close = df.iloc[latest_zt]['close']
            after_zt = df.iloc[latest_zt+1:current_idx+1]
            
            if len(after_zt) == 0:
                return None
                
            cond_count = (after_zt['close'] < ref_close).sum()
            if cond_count > 0:
                return None
            
            # TJ条件2: HHV(C, ZTTS) / LLV(C, ZTTS) < 1.3
            if len(after_zt) > 0:
                highest_close = after_zt['close'].max()
                lowest_close = after_zt['close'].min()
                if lowest_close > 0 and highest_close / lowest_close >= 1.3:
                    return None
            else:
                return None
            
            # TJ条件3: C/REF(H, ZTTS) < 1.1
            ref_high = df.iloc[latest_zt]['high']
            current_close = df.iloc[current_idx]['close']
            if current_close / ref_high >= 1.1:
                return None
            
            # TJ条件4: HHV(H, ZTTS) >= HHV(H, 60)*0.9
            highest_high_zt = after_zt['high'].max()
            start_idx = max(0, latest_zt - 60)
            highest_high_60 = df.iloc[start_idx:latest_zt+1]['high'].max()
            if highest_high_zt < highest_high_60 * 0.9:
                return None
            
            # TJ条件5: MA(C,22) >= REF(MA(C,22), 1)
            df['ma22'] = df['close'].rolling(window=22, min_periods=1).mean()
            if current_idx >= 1 and df.iloc[current_idx-1]['ma22'] > df.iloc[current_idx]['ma22']:
                return None
            
            # XH条件
            ref_hhv = after_zt['close'].max()
            today_close = df.iloc[current_idx]['close']
            yesterday_close = df.iloc[current_idx-1]['close']
            
            if today_close > ref_hhv and (today_close / yesterday_close) > 1.03:
                return {
                    'ts_code': df.iloc[current_idx]['ts_code'], 
                    'ztts': ztts,
                    'trade_date': df.iloc[current_idx]['trade_date']
                }
            
            return None
            
        except Exception as e:
            print(f"计算指标时出错: {e}")
            return None
    
    def scan_stocks(self):
        """扫描所有符合条件的股票"""
        stock_list = self.get_stock_list()
        results = []
        
        if not stock_list:
            print("没有符合条件的股票")
            return results
        
        print(f"开始扫描{len(stock_list)}只股票...")
        print("正在批量获取日线数据...")
        
        # 批量获取所有股票的日线数据
        end_date = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - timedelta(days=180)).strftime('%Y%m%d')
        
        # 分批处理，避免内存溢出
        batch_size = 50
        all_daily_data = []
        
        for i in range(0, len(stock_list), batch_size):
            batch = stock_list[i:i+batch_size]
            print(f"处理第{i//batch_size + 1}批，共{len(batch)}只股票...")
            
            df_batch = self.cache.get_daily_data_batch(batch, start_date, end_date)
            if not df_batch.empty:
                all_daily_data.append(df_batch)
            
            # 批次间休息
            time.sleep(1)
        
        if not all_daily_data:
            print("未获取到任何日线数据")
            return results
        
        daily_df = pd.concat(all_daily_data, ignore_index=True)
        
        # 批量获取财务信息
        print("正在批量获取财务信息...")
        fin_df = self.cache.get_financial_info_batch(stock_list)
        
        # 创建财务信息字典
        fin_dict = {}
        for _, row in fin_df.iterrows():
            fin_dict[row['ts_code']] = {
                'pe': row['pe'],
                'pb': row['pb'],
                'total_mv': row['total_mv']
            }
        
        print("开始逐只分析技术形态...")
        
        # 逐只股票分析
        for idx, ts_code in enumerate(stock_list):
            if idx % 20 == 0:
                print(f"分析进度: {idx}/{len(stock_list)}")
            
            try:
                # 获取该股票的日线数据
                stock_data = daily_df[daily_df['ts_code'] == ts_code]
                
                if stock_data.empty or len(stock_data) < 60:
                    continue
                
                stock_data = stock_data.copy()
                result = self.calculate_indicators(stock_data)
                
                if result:
                    # 获取股票基本信息
                    stock_basic = pro.stock_basic(ts_code=ts_code, 
                                                  fields='ts_code,name,industry')
                    
                    fin_info = fin_dict.get(ts_code, {})
                    
                    result.update({
                        'name': stock_basic.iloc[0]['name'] if not stock_basic.empty else ts_code,
                        'industry': stock_basic.iloc[0]['industry'] if not stock_basic.empty else '',
                        'pe': fin_info.get('pe'),
                        'pb': fin_info.get('pb'),
                        'total_mv': fin_info.get('total_mv')
                    })
                    results.append(result)
                    
                    print(f"✓ 找到符合条件的股票: {ts_code} - {result.get('name', '')}")
                    
            except Exception as e:
                print(f"处理{ts_code}时出错: {e}")
                continue
        
        return results
    
    def analyze_with_deepseek(self, stocks):
        """使用DeepSeek分析股票"""
        if not stocks:
            print("❌ 没有符合条件的股票")
            return
        
        stock_list = [f"{s['ts_code']}({s.get('name', '')})" for s in stocks]
        
        prompt = f"""
        请分析和筛选出这些股票中业务增长较明确，估值属于合理空间，属于当前行情热点，基本面没有雷，没有减持，没有增发预案的股票：
        
        候选股票列表: {', '.join(stock_list)}
        
        请逐只股票按照以下标准评估：
        1. 业务增长明确性
        2. 估值合理性（PE、PB是否在合理区间）
        3. 是否为当前市场热点板块
        4. 基本面风险
        5. 是否存在减持风险
        6. 是否有增发预案
        
        请给出详细的筛选理由，并最终推荐最符合条件的3-5只股票。
        """
        
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": "你是一个专业的股票分析师，擅长基本面和技术面分析。"},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.3
        }
        
        try:
            print("\n🤖 正在调用DeepSeek API进行分析...")
            response = requests.post(DEEPSEEK_API_URL, headers=headers, json=data, timeout=30)
            if response.status_code == 200:
                result = response.json()
                print("\n" + "="*80)
                print("📊 DeepSeek 分析结果：")
                print("="*80)
                print(result['choices'][0]['message']['content'])
                print("="*80)
            else:
                print(f"❌ DeepSeek API调用失败: {response.status_code}")
                self._print_candidate_stocks(stocks)
        except Exception as e:
            print(f"❌ 调用DeepSeek失败: {e}")
            self._print_candidate_stocks(stocks)
    
    def _print_candidate_stocks(self, stocks):
        """打印候选股票列表"""
        print("\n📋 候选股票列表（可手动分析）：")
        for stock in stocks:
            print(f"  - {stock['ts_code']} ({stock.get('name', '')}) | "
                  f"PE: {stock.get('pe', 'N/A')} | PB: {stock.get('pb', 'N/A')}")

def main():
    print("🚀 股票筛选程序启动")
    print("="*60)
    print("优化说明：")
    print("1. 批量获取数据，减少API调用次数")
    print("2. 自动速率限制，避免超频")
    print("3. 多层次缓存机制")
    print("4. 分批处理，防止内存溢出")
    print("="*60)
    
    screener = StockScreener()
    
    print("\n📈 开始筛选符合条件的股票...")
    stocks = screener.scan_stocks()
    
    print("\n" + "="*60)
    print(f"✅ 共筛选出 {len(stocks)} 只候选股票")
    
    if stocks:
        print("\n📋 候选股票详细列表:")
        print("-"*60)
        for idx, stock in enumerate(stocks, 1):
            print(f"{idx}. {stock['ts_code']} - {stock.get('name', '未知')}")
            print(f"   涨停后天数: {stock['ztts']}天 | 交易日期: {stock.get('trade_date', '未知')}")
            print(f"   估值: PE={stock.get('pe', 'N/A')}, PB={stock.get('pb', 'N/A')}")
            print(f"   行业: {stock.get('industry', '未知')}")
            print("-"*40)
        
        screener.analyze_with_deepseek(stocks)
    else:
        print("\n❌ 未找到符合条件的股票")

if __name__ == "__main__":
    main()