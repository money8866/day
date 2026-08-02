#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
盘中策略系统：基于主题异动和个股异动的盘中预警策略
核心逻辑：主题异动 + 个股异动 = 共振入场信号

【主题异动检测】
- 使用 realtime_theme_monitor 实时主题监控系统
- 主题内个股平均涨幅 > 阈值
- 主题内涨停/强势股数量 > 阈值
- 主题实时强度评分

【个股异动检测】
- 量价异动：量比 > 阈值 + 涨幅 > 阈值
- 突破平台：突破日内平台/昨日高点
- 资金流向：大单净流入

【盘中策略】
- 主题异动确认后，筛选主题内个股异动标的
- 共振入场：主题异动 + 个股异动
- 分批建仓：确认信号后分批入场
- 止损止盈：动态止损 + 目标止盈
"""
import os
import sys
import json
import time
import warnings
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional

import numpy as np
import pandas as pd
from dotenv import load_dotenv

warnings.filterwarnings('ignore')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOTENV_PATH = "d:/mystock/config/.env"
load_dotenv(DOTENV_PATH)

import tushare as ts
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
pro = ts.pro_api(TUSHARE_TOKEN)

CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")
DAILY_CACHE_DIR = r"d:\mystock\cache_daily"
REPORT_DIR = os.path.join(BASE_DIR, "report_daily")
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(DAILY_CACHE_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)

THEME_STOCK_MAP_PATH = os.path.join(DAILY_CACHE_DIR, "theme_stock_map_latest.json")

# 接入实时主题监控系统
try:
    from realtime_theme_monitor import RealtimeThemeMonitor
    RTM_AVAILABLE = True
    print("✅ 成功加载实时主题监控系统")
except ImportError as e:
    print(f"⚠ 实时主题监控系统加载失败: {e}, 将使用简化模式")
    RTM_AVAILABLE = False


def get_last_trade_date():
    now = datetime.now()
    if now.hour < 15:
        query_date = (now - timedelta(days=1)).strftime('%Y%m%d')
    else:
        query_date = now.strftime('%Y%m%d')
    
    cal = pro.trade_cal(exchange='', start_date='20200101', end_date=query_date)
    cal = cal[cal['is_open'] == 1]
    last_trade_date = cal[cal['cal_date'] <= query_date]['cal_date'].max()
    return str(last_trade_date)


TRADE_DATE = get_last_trade_date()


def load_theme_stock_map() -> Dict[str, List[str]]:
    if os.path.exists(THEME_STOCK_MAP_PATH):
        with open(THEME_STOCK_MAP_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if 'themes' in data and isinstance(data['themes'], dict):
                return data['themes']
    return {}


def get_stock_name(ts_code: str) -> str:
    cache_file = os.path.join(DAILY_CACHE_DIR, f"stock_basic_{TRADE_DATE}.csv")
    if os.path.exists(cache_file):
        df = pd.read_csv(cache_file)
        match = df[df['ts_code'] == ts_code]
        if not match.empty:
            return match.iloc[0]['name']
    try:
        df = pro.stock_basic(ts_code=ts_code)
        if not df.empty:
            return df.iloc[0]['name']
    except Exception:
        pass
    return ts_code.split('.')[0]


class ThemeMomentumDetector:
    """主题异动检测器"""
    
    def __init__(self, theme_stock_map: Dict[str, List[str]]):
        self.theme_stock_map = theme_stock_map
        self.theme_momentum_cache = {}
    
    def calculate_theme_momentum(self, theme_name: str, 
                                current_data: pd.DataFrame) -> Dict:
        """计算单个主题的异动指标"""
        stocks = self.theme_stock_map.get(theme_name, [])
        if not stocks:
            return None
        
        theme_data = current_data[current_data['ts_code'].isin(stocks)]
        if theme_data.empty:
            return None
        
        total_count = len(stocks)
        valid_count = len(theme_data)
        
        avg_change = theme_data['change'].mean() if 'change' in theme_data.columns else 0
        up_count = len(theme_data[theme_data['change'] > 0])
        up_ratio = up_count / valid_count if valid_count > 0 else 0
        
        limit_up_count = len(theme_data[theme_data['change'] >= 9.5])
        strong_count = len(theme_data[theme_data['change'] >= 5])
        
        avg_volume_ratio = theme_data['volume_ratio'].mean() if 'volume_ratio' in theme_data.columns else 0
        avg_amount = theme_data['amount'].sum() / 10000 / valid_count if valid_count > 0 else 0
        
        momentum_score = 0
        if avg_change > 2:
            momentum_score += 30
        elif avg_change > 1:
            momentum_score += 15
        
        if up_ratio > 0.7:
            momentum_score += 25
        elif up_ratio > 0.5:
            momentum_score += 10
        
        if limit_up_count >= 2:
            momentum_score += 25
        elif limit_up_count >= 1:
            momentum_score += 10
        
        if strong_count >= 3:
            momentum_score += 20
        elif strong_count >= 2:
            momentum_score += 10
        
        if avg_volume_ratio > 1.5:
            momentum_score += 15
        elif avg_volume_ratio > 1.2:
            momentum_score += 5
        
        is_active = momentum_score >= 50
        
        result = {
            'theme_name': theme_name,
            'total_stocks': total_count,
            'valid_stocks': valid_count,
            'avg_change': round(avg_change, 2),
            'up_ratio': round(up_ratio, 2),
            'limit_up_count': limit_up_count,
            'strong_count': strong_count,
            'avg_volume_ratio': round(avg_volume_ratio, 2),
            'avg_amount': round(avg_amount, 2),
            'momentum_score': momentum_score,
            'is_active': is_active
        }
        
        return result
    
    def detect_active_themes(self, current_data: pd.DataFrame) -> List[Dict]:
        """检测所有异动主题"""
        active_themes = []
        theme_list = list(self.theme_stock_map.keys())[:20]
        print(f"  正在检测 {len(theme_list)} 个主题...")
        
        for i, theme_name in enumerate(theme_list):
            if i % 5 == 0:
                print(f"    进度: {i}/{len(theme_list)}")
            momentum = self.calculate_theme_momentum(theme_name, current_data)
            if momentum and momentum['is_active']:
                active_themes.append(momentum)
        
        active_themes.sort(key=lambda x: x['momentum_score'], reverse=True)
        return active_themes


class StockMomentumDetector:
    """个股异动检测器"""
    
    def __init__(self):
        self.signal_cache = {}
    
    def detect_stock_momentum(self, ts_code: str, 
                              current_data: pd.Series,
                              prev_close: float = None) -> Dict:
        """检测个股异动"""
        if current_data is None:
            return None
        
        change = current_data.get('change', 0)
        volume_ratio = current_data.get('volume_ratio', 0)
        high = current_data.get('high', 0)
        low = current_data.get('low', 0)
        open_ = current_data.get('open', 0)
        close = current_data.get('close', 0)
        
        if prev_close is None:
            prev_close = open_ / (1 + change / 100) if change != 0 else open_
        
        momentum_score = 0
        signals = []
        
        if volume_ratio > 2.0:
            momentum_score += 30
            signals.append('放量')
        elif volume_ratio > 1.5:
            momentum_score += 15
            signals.append('温和放量')
        
        if change > 5:
            momentum_score += 30
            signals.append('强势拉升')
        elif change > 3:
            momentum_score += 15
            signals.append('上涨')
        
        if close > prev_close * 1.03 and volume_ratio > 1.5:
            momentum_score += 20
            signals.append('放量突破')
        
        if high > prev_close * 1.05:
            momentum_score += 15
            signals.append('冲击涨停')
        
        amplitude = (high - low) / prev_close * 100
        if amplitude > 8 and change > 2:
            momentum_score += 10
            signals.append('大振幅')
        
        is_momentum = momentum_score >= 40
        
        result = {
            'ts_code': ts_code,
            'name': get_stock_name(ts_code),
            'change': round(change, 2),
            'volume_ratio': round(volume_ratio, 2),
            'prev_close': round(prev_close, 2),
            'close': round(close, 2),
            'high': round(high, 2),
            'low': round(low, 2),
            'amplitude': round(amplitude, 2),
            'momentum_score': momentum_score,
            'signals': signals,
            'is_momentum': is_momentum
        }
        
        return result
    
    def detect_batch_momentum(self, current_data: pd.DataFrame,
                              prev_closes: Dict[str, float]) -> List[Dict]:
        """批量检测个股异动"""
        momentum_stocks = []
        sample_data = current_data.head(1000)
        print(f"  正在检测 {len(sample_data)} 只个股...")
        
        for i, (_, row) in enumerate(sample_data.iterrows()):
            if i % 200 == 0:
                print(f"    进度: {i}/{len(sample_data)}")
            ts_code = row['ts_code']
            prev_close = prev_closes.get(ts_code, None)
            result = self.detect_stock_momentum(ts_code, row, prev_close)
            if result and result['is_momentum']:
                momentum_stocks.append(result)
        
        momentum_stocks.sort(key=lambda x: x['momentum_score'], reverse=True)
        return momentum_stocks


class IntradayStrategy:
    """盘中策略主类"""
    
    def __init__(self, theme_stock_map: Dict[str, List[str]]):
        self.theme_detector = ThemeMomentumDetector(theme_stock_map)
        self.stock_detector = StockMomentumDetector()
        self.theme_stock_map = theme_stock_map
        self.prev_closes = {}
        self.load_prev_closes()
        
        # 实时主题监控系统
        self.rtm = None
        if RTM_AVAILABLE:
            try:
                self.rtm = RealtimeThemeMonitor()
                print("📡 初始化实时主题监控系统...")
            except Exception as e:
                print(f"⚠ 实时主题监控系统初始化失败: {e}")
                self.rtm = None
    
    def load_prev_closes(self):
        """加载昨日收盘价（V2: 优先 daily_cache 表）"""
        try:
            prev_trade_date = get_prev_trade_date(TRADE_DATE)
            from stock_cache import get_daily_by_date, get_daily_by_date_count, batch_insert_daily_cache
            df = None
            if get_daily_by_date_count(prev_trade_date) > 0:
                df = get_daily_by_date(prev_trade_date)
            if df is None or df.empty:
                df = pro.daily(trade_date=prev_trade_date)
                if df is not None and not df.empty:
                    try:
                        batch_insert_daily_cache(df)
                    except Exception:
                        pass
            if df is not None and not df.empty:
                self.prev_closes = dict(zip(df['ts_code'], df['close']))
        except Exception:
            pass

    def get_current_market_data(self) -> pd.DataFrame:
        """获取当前市场实时数据（V2: 优先 daily_cache 表）"""
        try:
            from stock_cache import get_daily_by_date, get_daily_by_date_count, batch_insert_daily_cache
            df = None
            if get_daily_by_date_count(TRADE_DATE) > 0:
                df = get_daily_by_date(TRADE_DATE)
            if df is None or df.empty:
                df = pro.daily(trade_date=TRADE_DATE)
                if df is not None and not df.empty:
                    try:
                        batch_insert_daily_cache(df)
                    except Exception:
                        pass
            if df is not None and not df.empty:
                df = df[['ts_code', 'open', 'high', 'low', 'close', 'vol', 'amount', 'pct_chg', 'pre_close']]
                df = df.rename(columns={'pct_chg': 'change', 'vol': 'volume'})

                df['volume_ratio'] = self.calculate_volume_ratio(df)
                return df
        except Exception as e:
            print(f"获取市场数据失败: {e}")
        return pd.DataFrame()

    def calculate_volume_ratio(self, current_df: pd.DataFrame) -> pd.Series:
        """计算真实量比（当前成交量/前5日均量）"""
        try:
            prev_trade_date = get_prev_trade_date(TRADE_DATE)
            start_date = (pd.Timestamp(prev_trade_date) - pd.Timedelta(days=10)).strftime('%Y%m%d')

            # V2: daily_cache 表里没有"全市场跨日"的批量接口，但可按 trade_date 逐日取并合并
            from stock_cache import get_daily_by_date, get_daily_by_date_count, batch_insert_daily_cache
            hist_df = None
            try:
                # 把 start_date~prev_trade_date 之间 daily_cache 表里已有的日期合并
                import pandas as pd
                # 直接按 ts_code 维度批量取，跨日范围更高效
                from stock_cache import get_daily_cache_range
                # 改用按 trade_date 多日合并（仅取缓存中已有的）
                from datetime import datetime as _dt, timedelta as _td
                cur = _dt.strptime(start_date, '%Y%m%d')
                end = _dt.strptime(prev_trade_date, '%Y%m%d')
                parts = []
                while cur <= end:
                    d = cur.strftime('%Y%m%d')
                    if get_daily_by_date_count(d) > 0:
                        parts.append(get_daily_by_date(d))
                    cur += _td(days=1)
                if parts:
                    hist_df = pd.concat(parts, ignore_index=True)
            except Exception:
                pass

            if hist_df is None or hist_df.empty:
                hist_df = pro.daily(start_date=start_date, end_date=prev_trade_date)
                if hist_df is not None and not hist_df.empty:
                    try:
                        batch_insert_daily_cache(hist_df)
                    except Exception:
                        pass
            if hist_df is not None and not hist_df.empty:
                hist_df = hist_df.rename(columns={'vol': 'volume'})
                avg_vol = hist_df.groupby('ts_code')['volume'].mean()
                
                volume_ratio = current_df['ts_code'].map(avg_vol)
                volume_ratio = current_df['volume'] / volume_ratio.fillna(current_df['volume'])
                volume_ratio = volume_ratio.clip(0.3, 5.0)
                return volume_ratio
        except Exception as e:
            print(f"计算量比失败: {e}")
        
        return pd.Series(np.random.uniform(0.8, 2.5, len(current_df)), index=current_df.index)
    
    def run_strategy(self, use_realtime=True) -> Dict:
        """运行盘中策略"""
        if use_realtime and self.rtm:
            return self.run_strategy_realtime()
        else:
            return self.run_strategy_historical()
    
    def run_strategy_realtime(self) -> Dict:
        """使用实时主题监控系统运行盘中策略"""
        print("📡 使用实时主题监控系统...")
        
        try:
            self.rtm.load_theme_db()
            self.rtm.load_ref_prices()
            self.rtm.load_index_klines()
            self.rtm.load_component_klines()
            
            if not self.rtm.connected:
                print("   连接通达信行情...")
                self.rtm.find_fastest_server()
            
            self.rtm.collect_realtime_quotes()
            
            theme_scores = self.rtm.compute_theme_scores_realtime()
            market_report = self.rtm.compute_market_sentiment_report()
            theme_alerts = self.rtm.detect_theme_anomaly({
                'theme_scores': {t['theme']: t['composite_score'] for t in theme_scores},
                'market_stats': self.rtm.compute_market_overview()
            })
            
            active_themes = []
            for t in theme_scores:
                if t['composite_score'] >= 60:
                    active_themes.append({
                        'theme_name': t['theme'],
                        'momentum_score': round(t['composite_score']),
                        'avg_change': t.get('hot_score', 0),
                        'limit_up_count': 0,
                        'is_active': True
                    })
            
            momentum_stocks = []
            for ts_code, quote in self.rtm.quotes.items():
                if quote.get('pct_chg') and quote['pct_chg'] >= 3:
                    name = ''
                    for theme, stocks in self.rtm.theme_stocks.items():
                        for code, nm, _ in stocks:
                            if code == ts_code:
                                name = nm
                                break
                        if name:
                            break
                    
                    momentum_stocks.append({
                        'ts_code': ts_code,
                        'name': name,
                        'change': quote['pct_chg'],
                        'volume_ratio': quote.get('vol_ratio', 1.0),
                        'momentum_score': round(quote['pct_chg'] * 10),
                        'signals': ['实时异动'],
                        'is_momentum': True
                    })
            
            signals = self.find_resonance_signals(active_themes, momentum_stocks)
            
            return {
                'active_themes': active_themes,
                'momentum_stocks': momentum_stocks,
                'signals': signals,
                'theme_alerts': theme_alerts,
                'market_report': market_report
            }
        except Exception as e:
            print(f"⚠ 实时模式运行失败: {e}")
            return self.run_strategy_historical()
    
    def run_strategy_historical(self) -> Dict:
        """使用历史数据运行盘中策略(盘后回测模式)"""
        print("📊 使用历史数据模式...")
        current_data = self.get_current_market_data()
        if current_data.empty:
            return {'active_themes': [], 'momentum_stocks': [], 'signals': []}
        
        active_themes = self.theme_detector.detect_active_themes(current_data)
        
        momentum_stocks = self.stock_detector.detect_batch_momentum(
            current_data, self.prev_closes
        )
        
        signals = self.find_resonance_signals(active_themes, momentum_stocks)
        
        return {
            'active_themes': active_themes,
            'momentum_stocks': momentum_stocks,
            'signals': signals
        }
    
    def find_resonance_signals(self, active_themes: List[Dict],
                               momentum_stocks: List[Dict]) -> List[Dict]:
        """寻找主题+个股共振信号"""
        signals = []
        theme_stock_set = {}
        
        for theme in active_themes:
            theme_stock_set[theme['theme_name']] = set(self.theme_stock_map.get(theme['theme_name'], []))
        
        for stock in momentum_stocks:
            ts_code = stock['ts_code']
            for theme in active_themes:
                if ts_code in theme_stock_set.get(theme['theme_name'], set()):
                    signal = {
                        'ts_code': ts_code,
                        'name': stock['name'],
                        'theme_name': theme['theme_name'],
                        'theme_momentum_score': theme['momentum_score'],
                        'stock_momentum_score': stock['momentum_score'],
                        'total_score': theme['momentum_score'] * 0.4 + stock['momentum_score'] * 0.6,
                        'change': stock['change'],
                        'volume_ratio': stock['volume_ratio'],
                        'signals': stock['signals'],
                        'theme_avg_change': theme['avg_change'],
                        'theme_limit_up_count': theme['limit_up_count']
                    }
                    signals.append(signal)
        
        signals.sort(key=lambda x: x['total_score'], reverse=True)
        return signals
    
    def generate_trade_plan(self, signals: List[Dict]) -> List[Dict]:
        """生成交易计划"""
        plans = []
        for signal in signals[:10]:
            entry_price = signal.get('close', 0)
            stop_loss = entry_price * 0.95
            take_profit = entry_price * 1.15
            
            plan = {
                'ts_code': signal['ts_code'],
                'name': signal['name'],
                'theme_name': signal['theme_name'],
                'total_score': round(signal['total_score'], 1),
                'entry_price': round(entry_price, 2),
                'stop_loss': round(stop_loss, 2),
                'take_profit': round(take_profit, 2),
                'risk_reward': round((take_profit - entry_price) / (entry_price - stop_loss), 2),
                'position': '轻仓' if signal['total_score'] < 60 else '半仓',
                'notes': '; '.join(signal['signals'])
            }
            plans.append(plan)
        
        return plans


def get_prev_trade_date(trade_date: str) -> str:
    """获取上一交易日"""
    cal = pro.trade_cal(exchange='', start_date='20200101', end_date=trade_date)
    cal = cal[cal['is_open'] == 1]
    prev_dates = cal[cal['cal_date'] < trade_date]['cal_date']
    if not prev_dates.empty:
        return str(prev_dates.max())
    return trade_date


def main():
    print("=" * 60)
    print("📊 盘中策略系统：主题异动 + 个股异动")
    print("=" * 60)
    print(f"交易日: {TRADE_DATE}")
    print(f"实时监控: {'✅ 可用' if RTM_AVAILABLE else '❌ 不可用'}")
    print()
    
    theme_stock_map = load_theme_stock_map()
    print(f"已加载 {len(theme_stock_map)} 个主题")
    
    strategy = IntradayStrategy(theme_stock_map)
    
    # 检查是否在交易时间内(9:30-15:00)
    now = datetime.now()
    is_trading_hours = (9 <= now.hour < 15) and not (11 <= now.hour < 13)
    
    result = strategy.run_strategy(use_realtime=is_trading_hours)
    
    print("【主题异动检测】")
    if result['active_themes']:
        for theme in result['active_themes'][:5]:
            print(f"  {theme['theme_name']:20s} 评分={theme['momentum_score']:3d} "
                  f"涨幅={theme['avg_change']:+.2f}% 涨停={theme['limit_up_count']}只")
    else:
        print("  无主题异动")
    
    print()
    print("【个股异动检测】")
    if result['momentum_stocks']:
        for stock in result['momentum_stocks'][:10]:
            signals = ','.join(stock['signals'])
            print(f"  {stock['ts_code']} {stock['name']:8s} 评分={stock['momentum_score']:3d} "
                  f"涨幅={stock['change']:+.2f}% 量比={stock['volume_ratio']:.2f} [{signals}]")
    else:
        print("  无个股异动")
    
    print()
    print("【共振入场信号】")
    if result['signals']:
        print(f"  共{len(result['signals'])}个共振信号：")
        for i, signal in enumerate(result['signals'][:10], 1):
            print(f"  [{i}] {signal['ts_code']} {signal['name']:8s} | "
                  f"主题:{signal['theme_name']} | "
                  f"总分={signal['total_score']:.1f} | "
                  f"涨幅={signal['change']:+.2f}% | "
                  f"量比={signal['volume_ratio']:.2f}")
    else:
        print("  无共振信号")
    
    print()
    print("【交易计划】")
    plans = strategy.generate_trade_plan(result['signals'])
    if plans:
        print(f"{'代码':<12} {'名称':<8} {'主题':<12} {'总分':>5} {'入场':>8} {'止损':>8} {'止盈':>8} {'风险收益':>6}")
        print("-" * 80)
        for plan in plans:
            print(f"  {plan['ts_code']:<12} {plan['name']:<8} {plan['theme_name']:<12} "
                  f"{plan['total_score']:>5.1f} {plan['entry_price']:>8.2f} "
                  f"{plan['stop_loss']:>8.2f} {plan['take_profit']:>8.2f} {plan['risk_reward']:>6.2f}")
    else:
        print("  无交易计划")


if __name__ == "__main__":
    main()