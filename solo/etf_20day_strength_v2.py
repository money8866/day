#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
20天强弱ETF龙头股筛选系统
- 按20天强弱选择最强的ETF
- 个股趋势以5/20/60日均线为依准
- 温和放量上涨，未大幅拉升
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

warnings.filterwarnings('ignore')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOTENV_PATH = os.path.join(os.path.dirname(BASE_DIR), "config", ".env")
load_dotenv(DOTENV_PATH)

CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")
os.makedirs(CACHE_DIR, exist_ok=True)

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
pro = ts.pro_api(TUSHARE_TOKEN)

THEME_PATH = os.path.join(BASE_DIR, "theme.json")


class ETF20DayStrengthSystem:
    """20天强弱ETF龙头股筛选系统"""
    
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
        cache_file = os.path.join(CACHE_DIR, f"cache_20day_{key}.pkl")
        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'rb') as f:
                    return pickle.load(f)
            except:
                return None
        return None
    
    def _set_cache(self, key, data):
        """设置缓存"""
        cache_file = os.path.join(CACHE_DIR, f"cache_20day_{key}.pkl")
        try:
            with open(cache_file, 'wb') as f:
                pickle.dump(data, f)
        except:
            pass
    
    def get_daily_data(self, ts_code, days=120):
        """获取个股日线K线数据"""
        cache_key = f"daily_{ts_code}_{days}"
        cached = self._get_cache(cache_key)
        if cached is not None:
            return cached
        
        try:
            end_date = datetime.now().strftime('%Y%m%d')
            start_date = (datetime.now() - timedelta(days=days * 1.5)).strftime('%Y%m%d')
            
            df = pro.daily(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                fields='ts_code,trade_date,open,high,low,close,vol,amount'
            )
            
            if df is None or len(df) == 0:
                return None
            
            df = df.sort_values('trade_date').tail(days)
            self._set_cache(cache_key, df)
            return df
        except Exception as e:
            print(f"    获取日线数据失败 {ts_code}: {e}")
            return None
    
    def calculate_strength_indicators(self, df):
        """计算日线技术指标"""
        if df is None or len(df) < 60:
            return None
        
        df = df.copy()
        
        # 5/20/60日均线
        df['ma5'] = df['close'].rolling(5).mean()
        df['ma20'] = df['close'].rolling(20).mean()
        df['ma60'] = df['close'].rolling(60).mean()
        
        # 20日涨跌幅
        df['return_20'] = df['close'].pct_change(20) * 100
        
        # 成交量均线
        df['vol_ma5'] = df['vol'].rolling(5).mean()
        df['vol_ma20'] = df['vol'].rolling(20).mean()
        
        # 量比
        df['vol_ratio'] = df['vol'] / df['vol_ma20']
        
        # 5日/20日涨幅
        df['return_5'] = df['close'].pct_change(5) * 100
        df['return_10'] = df['close'].pct_change(10) * 100
        
        return df
    
    def calculate_20day_strength(self, df):
        """计算20天强弱"""
        if df is None or len(df) < 20:
            return 0
        
        latest = df.iloc[-1]
        
        return_score = min(latest.get('return_20', 0) / 5, 10)
        
        trend_score = 0
        if pd.notna(latest['ma5']) and pd.notna(latest['ma20']):
            if latest['ma5'] > latest['ma20']:
                trend_score += 3
            if latest['ma20'] > latest['ma60']:
                trend_score += 3
            if latest['close'] > latest['ma5']:
                trend_score += 2
        trend_score = min(trend_score, 8)
        
        vol_score = min(max((latest.get('vol_ratio', 1) - 1) * 3, 0), 5)
        
        stability_score = 0
        if -20 < latest.get('return_20', 0) < 40:
            stability_score = 2
        if -10 < latest.get('return_10', 0) < 25:
            stability_score += 2
        if -5 < latest.get('return_5', 0) < 20:
            stability_score += 1
        stability_score = min(stability_score, 5)
        
        total_score = (
            return_score * 0.35 +
            trend_score * 0.30 +
            vol_score * 0.20 +
            stability_score * 0.15
        )
        
        return total_score
    
    def calculate_all_etf_strength(self):
        """计算所有ETF 20天强弱"""
        print("\n" + "="*80)
        print("🔍 计算各ETF 20天强弱")
        print("="*80)
        
        etf_scores = []
        
        for theme_name, theme_config in self.themes.items():
            etf_code = theme_config.get('etf', '')
            if not etf_code:
                continue
            
            print(f"\n📊 分析: {theme_name}")
            
            core_companies = theme_config.get('core_companies', [])
            if not core_companies:
                continue
            
            theme_total_strength = 0
            valid_count = 0
            
            for company in core_companies:
                stock_info = self._search_stock(company)
                if not stock_info:
                    continue
                
                ts_code = stock_info['ts_code']
                
                daily_df = self.get_daily_data(ts_code, days=120)
                if daily_df is None or len(daily_df) < 60:
                    continue
                
                indicator_df = self.calculate_strength_indicators(daily_df)
                if indicator_df is None:
                    continue
                
                strength = self.calculate_20day_strength(indicator_df)
                
                theme_total_strength += strength
                valid_count += 1
                
                print(f"  {company}: 20天涨幅 {indicator_df.iloc[-1]['return_20']:.1f}%, 得分 {strength:.2f}")
            
            if valid_count > 0:
                avg_strength = theme_total_strength / valid_count
                etf_scores.append({
                    'theme': theme_name,
                    'etf_code': etf_code,
                    'avg_strength': avg_strength,
                    'company_count': valid_count,
                    'core_companies': core_companies
                })
                print(f"\n  ✅ {theme_name} 平均得分: {avg_strength:.2f}")
        
        if not etf_scores:
            print("❌ 没有找到有效的ETF")
            return []
        
        etf_scores = sorted(etf_scores, key=lambda x: x['avg_strength'], reverse=True)
        
        print("\n" + "="*80)
        print("🏆 ETF 20天强弱排名 (Top 5)")
        print("="*80)
        for i, etf in enumerate(etf_scores[:5], 1):
            print(f"  {i}. {etf['theme']} - 平均得分 {etf['avg_strength']:.2f} (样本 {etf['company_count']}只)")
        
        return etf_scores
    
    def _search_stock(self, company_name):
        """搜索股票代码"""
        try:
            df = pro.stock_basic(
                ts_code='',
                name=company_name,
                fields='ts_code,name,industry,market'
            )
            
            if df is not None and len(df) > 0:
                return df.iloc[0].to_dict()
            return None
        except:
            return None
    
    def get_market_cap(self, ts_code):
        """获取个股总市值（亿元）"""
        try:
            df = pro.daily_basic(
                ts_code=ts_code,
                fields='ts_code,trade_date,total_mv'
            )
            
            if df is not None and len(df) > 0:
                df = df.sort_values('trade_date', ascending=False).head(1)
                return df.iloc[0]['total_mv'] / 10000
            return 0
        except:
            return 0
    
    def is_mild_upward(self, df, name=""):
        """判断是否温和放量上涨"""
        if df is None or len(df) < 60:
            return False, "数据不足"
        
        latest = df.iloc[-1]
        
        # 均线排列: 至少5日 > 20日
        has_valid_ma = pd.notna(latest['ma5']) and pd.notna(latest['ma20']) and pd.notna(latest['ma60'])
        if not has_valid_ma:
            return False, "均线数据不足"
        
        ma_order = (latest['ma5'] > latest['ma20'])
        if not ma_order:
            return False, f"均线不符合 (MA5={latest['ma5']:.2f}, MA20={latest['ma20']:.2f})"
        
        # 价格在5日线上
        price_above_ma5 = latest['close'] > latest['ma5']
        if not price_above_ma5:
            return False, f"价格未在MA5上方 (价格={latest['close']:.2f}, MA5={latest['ma5']:.2f})"
        
        # 温和放量: 量比在0.8-3.0之间（放宽范围）
        vol_mild = 0.8 < latest.get('vol_ratio', 1) < 3.0
        if not vol_mild:
            return False, f"量比不符合 (量比={latest.get('vol_ratio', 1):.2f})"
        
        # 未大幅拉升: 20日涨幅小于50%
        not_over_rally = latest.get('return_20', 0) < 50
        if not not_over_rally:
            return False, f"涨幅过大 (20日涨幅={latest.get('return_20', 0):.1f}%)"
        
        return True, "符合条件"
    
    def analyze_stock(self, ts_code, name):
        """分析个股"""
        try:
            daily_df = self.get_daily_data(ts_code, days=120)
            if daily_df is None or len(daily_df) < 60:
                return None, "数据不足"
            
            indicator_df = self.calculate_strength_indicators(daily_df)
            if indicator_df is None:
                return None, "计算指标失败"
            
            is_pass, reason = self.is_mild_upward(indicator_df, name)
            if not is_pass:
                return None, reason
            
            latest = indicator_df.iloc[-1]
            
            market_cap = self.get_market_cap(ts_code)
            
            # 计算综合得分
            score = self.calculate_20day_strength(indicator_df)
            
            return {
                'ts_code': ts_code,
                'name': name,
                'market_cap': market_cap,
                'return_20': latest.get('return_20', 0),
                'return_10': latest.get('return_10', 0),
                'return_5': latest.get('return_5', 0),
                'vol_ratio': latest.get('vol_ratio', 1),
                'ma5': latest['ma5'],
                'ma20': latest['ma20'],
                'ma60': latest['ma60'],
                'score': score
            }, reason
        except Exception as e:
            return None, f"异常: {e}"
    
    def analyze_top_etfs(self, etf_scores, top_n=3):
        """分析排名前几的ETF"""
        all_candidates = []
        
        for rank, etf in enumerate(etf_scores[:top_n], 1):
            theme_name = etf['theme']
            core_companies = etf['core_companies']
            
            print(f"\n\n{'='*80}")
            print(f"📈 分析 Top {rank}: {theme_name} (平均得分 {etf['avg_strength']:.2f})")
            print(f"{'='*80}")
            
            for company in core_companies:
                print(f"\n🔍 分析: {company}")
                
                stock_info = self._search_stock(company)
                if not stock_info:
                    print(f"  ❌ 未找到股票")
                    continue
                
                ts_code = stock_info['ts_code']
                market = stock_info.get('market', '')
                
                result, reason = self.analyze_stock(ts_code, company)
                
                if result:
                    result['market'] = market
                    result['theme'] = theme_name
                    all_candidates.append(result)
                    print(f"  ✅ {reason}")
                    print(f"    市值: {result['market_cap']:.1f}亿, 20日涨幅: {result['return_20']:.1f}%")
                    print(f"    5日/20日/60日: {result['ma5']:.2f}/{result['ma20']:.2f}/{result['ma60']:.2f}")
                    print(f"    量比: {result['vol_ratio']:.2f}, 得分: {result['score']:.2f}")
                else:
                    print(f"  ❌ {reason}")
        
        return all_candidates
    
    def run_analysis(self):
        """运行完整分析"""
        print("="*80)
        print("🚀 20天强弱ETF龙头股筛选系统")
        print("="*80)
        print(f"分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        etf_scores = self.calculate_all_etf_strength()
        if not etf_scores:
            return
        
        candidates = self.analyze_top_etfs(etf_scores, top_n=5)
        
        if not candidates:
            print("\n\n" + "="*80)
            print("❌ 没有找到符合条件的个股")
            print("="*80)
            return
        
        candidates = sorted(candidates, key=lambda x: x['score'], reverse=True)
        
        print("\n\n" + "="*80)
        print("🏆 所有符合条件的个股排名")
        print("="*80)
        print(f"{'排名':<6}{'股票':<10}{'板块':<8}{'所属ETF':<15}{'市值(亿)':<10}{'20日涨幅(%)':<12}{'量比':<8}{'得分':<8}")
        print("-"*80)
        
        for i, candidate in enumerate(candidates, 1):
            market_tag = ""
            if candidate['market'] == '创业板':
                market_tag = "【创】"
            elif candidate['market'] == '科创板':
                market_tag = "【科】"
            
            print(f"{i:<6}{market_tag}{candidate['name']:<8}{candidate['market']:<6}{candidate['theme']:<15}{candidate['market_cap']:<10.1f}"
                  f"{candidate['return_20']:<12.1f}{candidate['vol_ratio']:<8.2f}{candidate['score']:<8.2f}")
        
        print("\n" + "="*80)
        print(f"✅ 共找到 {len(candidates)} 只符合条件的股票")
        print("="*80)
        
        return candidates


def main():
    """主函数"""
    system = ETF20DayStrengthSystem()
    system.run_analysis()


if __name__ == "__main__":
    main()
