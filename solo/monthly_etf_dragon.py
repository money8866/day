#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
月度ETF龙头股筛选系统
最小周期：月线
核心功能：从theme.json读取ETF配置，筛选月度最强龙头个股
"""
import os
import sys
import pickle
import warnings
import time
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv

import numpy as np
import pandas as pd
import tushare as ts
from pandas.tseries.offsets import MonthEnd

warnings.filterwarnings('ignore')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOTENV_PATH = os.path.join(os.path.dirname(BASE_DIR), "config", ".env")
load_dotenv(DOTENV_PATH)

CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")
os.makedirs(CACHE_DIR, exist_ok=True)

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
pro = ts.pro_api(TUSHARE_TOKEN)

THEME_PATH = os.path.join(BASE_DIR, "theme.json")


class MonthlyETFDragon:
    """月度ETF龙头股筛选器"""
    
    def __init__(self):
        self.themes = self._load_themes()
        self.cache = {}
        
    def _load_themes(self):
        """从theme.json加载主题配置"""
        with open(THEME_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get('HOT_THEMES', {})
    
    def _get_cache(self, key):
        """获取缓存"""
        cache_file = os.path.join(CACHE_DIR, f"cache_{key}.pkl")
        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'rb') as f:
                    return pickle.load(f)
            except:
                return None
        return None
    
    def _set_cache(self, key, data):
        """设置缓存"""
        cache_file = os.path.join(CACHE_DIR, f"cache_{key}.pkl")
        try:
            with open(cache_file, 'wb') as f:
                pickle.dump(data, f)
        except:
            pass
    
    def get_monthly_data(self, ts_code, months=6):
        """获取个股月度K线数据"""
        cache_key = f"monthly_{ts_code}_{months}"
        cached = self._get_cache(cache_key)
        if cached is not None:
            return cached
        
        try:
            end_date = datetime.now().strftime('%Y%m%d')
            start_date = (datetime.now() - timedelta(days=months * 40)).strftime('%Y%m%d')
            
            df = pro.monthly(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                fields='ts_code,trade_date,open,high,low,close,vol,amount'
            )
            
            df = df.sort_values('trade_date').tail(months)
            self._set_cache(cache_key, df)
            return df
        except Exception as e:
            print(f"获取月度数据失败 {ts_code}: {e}")
            return None
    
    def calculate_monthly_indicators(self, df):
        """计算月度技术指标"""
        if df is None or len(df) < 3:
            return None
        
        df = df.copy()
        
        # 月度涨跌幅
        df['monthly_return'] = df['close'].pct_change() * 100
        
        # 月度成交量变化
        df['volume_ma3'] = df['vol'].rolling(3).mean()
        df['volume_ratio'] = df['vol'] / df['volume_ma3']
        
        # 月线均线系统
        df['ma5'] = df['close'].rolling(5).mean()
        df['ma10'] = df['close'].rolling(10).mean()
        df['ma20'] = df['close'].rolling(20).mean()
        
        # MACD（月线）
        exp12 = df['close'].ewm(span=12, adjust=False).mean()
        exp26 = df['close'].ewm(span=26, adjust=False).mean()
        df['macd'] = exp12 - exp26
        df['signal'] = df['macd'].ewm(span=9, adjust=False).mean()
        df['histogram'] = df['macd'] - df['signal']
        
        return df
    
    def is_uptrend(self, df):
        """判断月线趋势是否向上"""
        if df is None or len(df) < 3:
            return False
        
        last = df.iloc[-1]
        
        # 均线多头排列（5 > 10 > 20）
        ma_bullish = (last['ma5'] > last['ma10'] > last['ma20']) if pd.notna(last['ma20']) else False
        
        # MACD金叉
        macd_golden = (df.iloc[-2]['macd'] < df.iloc[-2]['signal']) and (last['macd'] > last['signal'])
        
        # 价格站上10月均线
        price_above_ma10 = last['close'] > last['ma10']
        
        return ma_bullish or (macd_golden and price_above_ma10)
    
    def is_too_rally(self, df, threshold=30):
        """判断是否大幅拉升"""
        if df is None or len(df) < 2:
            return True
        
        last_month = df.iloc[-1]
        return_month = last_month['monthly_return']
        
        # 本月涨幅超过阈值
        return return_month > threshold
    
    def get_market_cap(self, ts_code):
        """获取个股总市值"""
        try:
            df = pro.daily_basic(
                ts_code=ts_code,
                trade_date=datetime.now().strftime('%Y%m%d'),
                fields='ts_code,total_mv,circ_mv'
            )
            if df is not None and len(df) > 0:
                return df.iloc[0]['total_mv']  # 总市值（万元）
            return 0
        except:
            return 0
    
    def is_chuangye_or_kechuang(self, ts_code):
        """判断是否双创股票"""
        code = ts_code.split('.')[0]
        # 创业板：300xxx
        is_cyb = code.startswith('300')
        # 科创板：688xxx
        is_kcb = code.startswith('688')
        return is_cyb, is_kcb
    
    def check_risk_events(self, ts_code, months=3):
        """检查基本面风险事件"""
        try:
            end_date = datetime.now().strftime('%Y%m%d')
            start_date = (datetime.now() - timedelta(days=months * 35)).strftime('%Y%m%d')
            
            issues = []
            
            # 检查ST/*ST
            try:
                st_df = pro.stk_rewards(ts_code=ts_code, trade_date=datetime.now().strftime('%Y%m%d'))
                if st_df is not None and len(st_df) > 0:
                    issues.append('ST')
            except:
                pass
            
            # 检查增发预案（简化版，实际需要更复杂逻辑）
            # 由于tushare免费版限制，这里用基本面指标替代
            
            # 检查近期涨幅（简化排除）
            
            return len(issues) == 0, issues
        except:
            return True, []
    
    def calculate_score(self, df, market_cap, is_cyb, is_kcb):
        """计算龙头得分"""
        if df is None or len(df) < 3:
            return 0
        
        last = df.iloc[-1]
        scores = {}
        
        # 1. 月度涨幅得分（相对强度）
        monthly_return = last['monthly_return'] if pd.notna(last['monthly_return']) else 0
        scores['return'] = min(max(monthly_return / 10, 0), 10)  # 0-10分
        
        # 2. 成交量得分
        vol_ratio = last['volume_ratio'] if pd.notna(last['volume_ratio']) else 1
        scores['volume'] = min(max((vol_ratio - 1) * 5, 0), 10)  # 0-10分
        
        # 3. 趋势得分
        trend_score = 0
        if last['ma5'] > last['ma10']:
            trend_score += 3
        if last['ma10'] > last['ma20']:
            trend_score += 3
        if last['histogram'] > 0:
            trend_score += 4
        scores['trend'] = trend_score
        
        # 4. 基本面得分（简化版）
        scores['fundamental'] = 7
        
        # 5. 市值弹性得分
        elasticity_score = 0
        # 双创加分
        if is_kcb:
            elasticity_score += 5
        elif is_cyb:
            elasticity_score += 3
        
        # 市值适中加分（100-500亿最佳）
        cap_yi = market_cap / 10000  # 万元转亿元
        if 100 <= cap_yi <= 500:
            elasticity_score += 5
        elif cap_yi > 500:
            elasticity_score += 3
        scores['elasticity'] = min(elasticity_score, 10)
        
        # 综合得分
        total_score = (
            scores['return'] * 0.25 +
            scores['volume'] * 0.25 +
            scores['trend'] * 0.20 +
            scores['fundamental'] * 0.15 +
            scores['elasticity'] * 0.15
        )
        
        return total_score, scores
    
    def analyze_theme(self, theme_name, theme_config, limit=3):
        """分析单个主题的龙头股"""
        print(f"\n{'='*60}")
        print(f"📊 分析主题：{theme_name}")
        print(f"{'='*60}")
        
        etf_code = theme_config.get('etf', '')
        core_companies = theme_config.get('core_companies', [])
        industries = theme_config.get('industry', [])
        concepts = theme_config.get('concept', [])
        
        print(f"  ETF代码: {etf_code}")
        print(f"  核心公司: {', '.join(core_companies[:5])}")
        
        candidates = []
        
        # 获取核心公司对应的股票代码
        for company in core_companies:
            try:
                # 通过股票名称搜索
                name_df = pro.stock_basic(
                    ts_code='',
                    name=company,
                    fields='ts_code,name,industry,market'
                )
                
                if name_df is not None and len(name_df) > 0:
                    ts_code = name_df.iloc[0]['ts_code']
                    
                    # 市值筛选
                    market_cap = self.get_market_cap(ts_code)
                    if market_cap < 1000000:  # 100亿 = 1000000万
                        continue
                    
                    # 获取月度数据
                    monthly_df = self.get_monthly_data(ts_code, months=6)
                    if monthly_df is None or len(monthly_df) < 3:
                        continue
                    
                    # 计算指标
                    indicator_df = self.calculate_monthly_indicators(monthly_df)
                    if indicator_df is None:
                        continue
                    
                    # 趋势筛选
                    if not self.is_uptrend(indicator_df):
                        continue
                    
                    # 涨幅筛选
                    if self.is_too_rally(indicator_df, threshold=30):
                        continue
                    
                    # 风险检查
                    is_safe, issues = self.check_risk_events(ts_code)
                    
                    # 双创判断
                    is_cyb, is_kcb = self.is_chuangye_or_kechuang(ts_code)
                    
                    # 计算得分
                    score, score_details = self.calculate_score(
                        indicator_df, market_cap, is_cyb, is_kcb
                    )
                    
                    candidates.append({
                        'ts_code': ts_code,
                        'name': company,
                        'market_cap': market_cap / 10000,  # 转为亿元
                        'is_cyb': is_cyb,
                        'is_kcb': is_kcb,
                        'monthly_return': indicator_df.iloc[-1]['monthly_return'],
                        'volume_ratio': indicator_df.iloc[-1]['volume_ratio'],
                        'score': score,
                        'score_details': score_details,
                        'is_safe': is_safe,
                        'issues': issues
                    })
                    
                    time.sleep(0.1)  # 避免频率限制
                    
                except Exception as e:
                    print(f"    ⚠️ 搜索 {company} 失败: {e}")
                    continue
                    
            except Exception as e:
                print(f"    ⚠️ 处理 {company} 时出错: {e}")
                continue
        
        # 按得分排序
        candidates = sorted(candidates, key=lambda x: x['score'], reverse=True)
        
        return candidates[:limit]
    
    def run_analysis(self, themes_to_analyze=None):
        """运行完整分析"""
        print(f"\n{'#'*80}")
        print(f"🏆 月度ETF龙头股筛选分析")
        print(f"{'#'*80}")
        print(f"分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        if themes_to_analyze is None:
            themes_to_analyze = list(self.themes.keys())
        
        results = {}
        
        for theme_name in themes_to_analyze:
            if theme_name not in self.themes:
                continue
            
            theme_config = self.themes[theme_name]
            dragons = self.analyze_theme(theme_name, theme_config, limit=3)
            results[theme_name] = dragons
            
            time.sleep(0.5)  # 避免API频率限制
        
        return results
    
    def print_summary(self, results):
        """打印分析总结"""
        print(f"\n\n{'#'*80}")
        print(f"📈 月度ETF龙头股筛选结果汇总")
        print(f"{'#'*80}\n")
        
        all_dragons = []
        
        for theme_name, dragons in results.items():
            if not dragons:
                continue
                
            print(f"\n🎯 {theme_name}")
            print("-" * 80)
            
            for i, dragon in enumerate(dragons, 1):
                board_type = ""
                if dragon['is_kcb']:
                    board_type = "【科创板】"
                elif dragon['is_cyb']:
                    board_type = "【创业板】"
                
                print(f"  {i}. {board_type}{dragon['name']} ({dragon['ts_code']})")
                print(f"     市值: {dragon['market_cap']:.1f}亿 | "
                      f"本月涨幅: {dragon['monthly_return']:.1f}% | "
                      f"量比: {dragon['volume_ratio']:.2f}")
                print(f"     综合得分: {dragon['score']:.2f}/10")
                print(f"     得分详情: {dragon['score_details']}")
                
                if not dragon['is_safe']:
                    print(f"     ⚠️ 风险提示: {', '.join(dragon['issues'])}")
                
                all_dragons.append({
                    'theme': theme_name,
                    **dragon
                })
        
        # 全市场Top 10
        print(f"\n\n{'#'*80}")
        print(f"🏅 全市场月度最强龙头 Top 10")
        print(f"{'#'*80}\n")
        
        top_dragons = sorted(all_dragons, key=lambda x: x['score'], reverse=True)[:10]
        
        for i, dragon in enumerate(top_dragons, 1):
            board_type = ""
            if dragon['is_kcb']:
                board_type = "科创"
            elif dragon['is_cyb']:
                board_type = "创业"
            else:
                board_type = "主板"
            
            print(f"{i:2d}. {dragon['name']:8s} | {board_type:4s} | "
                  f"{dragon['theme']:12s} | {dragon['market_cap']:6.1f}亿 | "
                  f"+{dragon['monthly_return']:5.1f}% | 得分:{dragon['score']:.2f}")


def main():
    """主函数"""
    analyzer = MonthlyETFDragon()
    
    # 分析所有主题
    results = analyzer.run_analysis()
    
    # 打印总结
    analyzer.print_summary(results)


if __name__ == "__main__":
    main()
