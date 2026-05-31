#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
20天强弱ETF龙头股筛选系统
按20天强弱选择最强的ETF，输出格式：先ETF，再对应个股
"""
import os
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
        
        df['ma5'] = df['close'].rolling(5).mean()
        df['ma20'] = df['close'].rolling(20).mean()
        df['ma60'] = df['close'].rolling(60).mean()
        
        df['return_20'] = df['close'].pct_change(20) * 100
        df['vol_ma20'] = df['vol'].rolling(20).mean()
        df['vol_ratio'] = df['vol'] / df['vol_ma20']
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
        print("\nETF排序公式：")
        print("  ETF强度 = Σ(成分股20天强弱得分) / 成分股数量")
        print("  = Σ(return_score×35% + trend_score×30% + vol_score×20% + stability_score×15%) / n")
        print()
        
        etf_scores = []
        
        for theme_name, theme_config in self.themes.items():
            etf_code = theme_config.get('etf', '')
            if not etf_code:
                continue
            
            print(f"\n📊 计算: {theme_name}")
            
            core_companies = theme_config.get('core_companies', [])
            if not core_companies:
                continue
            
            stock_strengths = []
            
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
                stock_strengths.append({
                    'name': company,
                    'ts_code': ts_code,
                    'strength': strength,
                    'return_20': indicator_df.iloc[-1].get('return_20', 0)
                })
                
                print(f"  {company}: 20天涨幅 {indicator_df.iloc[-1]['return_20']:.1f}%, 得分 {strength:.2f}")
            
            if stock_strengths:
                avg_strength = sum(s['strength'] for s in stock_strengths) / len(stock_strengths)
                etf_scores.append({
                    'theme': theme_name,
                    'etf_code': etf_code,
                    'avg_strength': avg_strength,
                    'stock_count': len(stock_strengths),
                    'stocks': stock_strengths
                })
                print(f"\n  ➤ {theme_name} 平均得分: {avg_strength:.2f} ({len(stock_strengths)}只)")
        
        if not etf_scores:
            print("❌ 没有找到有效的ETF")
            return []
        
        etf_scores = sorted(etf_scores, key=lambda x: x['avg_strength'], reverse=True)
        
        print("\n" + "="*80)
        print("🏆 ETF 20天强弱排名")
        print("="*80)
        for i, etf in enumerate(etf_scores, 1):
            print(f"  {i:2d}. {etf['theme']:<15} 平均得分: {etf['avg_strength']:5.2f} (样本 {etf['stock_count']}只)")
        
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
    
    def is_mild_upward(self, df):
        """判断是否温和放量上涨"""
        if df is None or len(df) < 60:
            return False, "数据不足"
        
        latest = df.iloc[-1]
        
        has_valid_ma = pd.notna(latest['ma5']) and pd.notna(latest['ma20']) and pd.notna(latest['ma60'])
        if not has_valid_ma:
            return False, "均线数据不足"
        
        ma_order = (latest['ma5'] > latest['ma20'])
        if not ma_order:
            return False, f"均线不符合 (MA5={latest['ma5']:.2f}, MA20={latest['ma20']:.2f})"
        
        price_above_ma5 = latest['close'] > latest['ma5']
        if not price_above_ma5:
            return False, f"价格未在MA5上方"
        
        vol_mild = 0.8 < latest.get('vol_ratio', 1) < 3.0
        if not vol_mild:
            return False, f"量比不符合 (量比={latest.get('vol_ratio', 1):.2f})"
        
        not_over_rally = latest.get('return_20', 0) < 50
        if not not_over_rally:
            return False, f"涨幅过大 (20日涨幅={latest.get('return_20', 0):.1f}%)"
        
        return True, "符合"
    
    def analyze_stock(self, ts_code, name):
        """分析个股"""
        try:
            daily_df = self.get_daily_data(ts_code, days=120)
            if daily_df is None or len(daily_df) < 60:
                return None
            
            indicator_df = self.calculate_strength_indicators(daily_df)
            if indicator_df is None:
                return None
            
            is_pass, reason = self.is_mild_upward(indicator_df)
            if not is_pass:
                return None
            
            latest = indicator_df.iloc[-1]
            market_cap = self.get_market_cap(ts_code)
            score = self.calculate_20day_strength(indicator_df)
            
            return {
                'ts_code': ts_code,
                'name': name,
                'market_cap': market_cap,
                'return_20': latest.get('return_20', 0),
                'vol_ratio': latest.get('vol_ratio', 1),
                'ma5': latest['ma5'],
                'ma20': latest['ma20'],
                'ma60': latest['ma60'],
                'score': score
            }
        except:
            return None
    
    def run_analysis(self):
        """运行完整分析"""
        print("="*80)
        print("🚀 20天强弱ETF龙头股筛选系统")
        print("="*80)
        print(f"分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        etf_scores = self.calculate_all_etf_strength()
        if not etf_scores:
            return
        
        print("\n\n" + "="*80)
        print("📈 ETF及对应个股详情")
        print("="*80)
        
        for rank, etf in enumerate(etf_scores, 1):
            theme_name = etf['theme']
            etf_code = etf['etf_code']
            
            print(f"\n\n{'='*80}")
            print(f"🏆 Top {rank}: {theme_name} (ETF: {etf_code})")
            print(f"    平均强度得分: {etf['avg_strength']:.2f} | 成分股: {etf['stock_count']}只")
            print(f"{'='*80}")
            
            candidates = []
            
            for stock in etf['stocks']:
                name = stock['name']
                ts_code = stock['ts_code']
                
                result = self.analyze_stock(ts_code, name)
                
                if result:
                    stock_info = self._search_stock(name)
                    result['market'] = stock_info.get('market', '') if stock_info else ''
                    result['theme'] = theme_name
                    candidates.append(result)
            
            if candidates:
                candidates = sorted(candidates, key=lambda x: x['score'], reverse=True)
                
                print(f"\n  ✅ 符合条件个股: {len(candidates)}只")
                print(f"\n  {'排名':<6}{'股票名称':<10}{'板块':<8}{'市值(亿)':<10}{'20日涨幅(%)':<12}{'量比':<8}{'MA5/MA20/MA60':<28}{'得分':<8}")
                print(f"  {'-'*100}")
                
                for i, c in enumerate(candidates, 1):
                    market_tag = ""
                    if c['market'] == '创业板':
                        market_tag = "【创】"
                    elif c['market'] == '科创板':
                        market_tag = "【科】"
                    
                    print(f"  {i:<6}{market_tag}{c['name']:<8}{c['market']:<6}{c['market_cap']:<10.1f}"
                          f"{c['return_20']:<12.1f}{c['vol_ratio']:<8.2f}"
                          f"{c['ma5']:.1f}/{c['ma20']:.1f}/{c['ma60']:.1f}  "
                          f"{c['score']:<8.2f}")
            else:
                print(f"\n  ❌ 没有符合条件个股")
        
        print("\n\n" + "="*80)
        print("✅ 筛选完成")
        print("="*80)


def main():
    """主函数"""
    system = ETF20DayStrengthSystem()
    system.run_analysis()


if __name__ == "__main__":
    main()
