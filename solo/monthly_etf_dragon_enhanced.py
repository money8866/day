#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
月度ETF龙头股筛选系统 - 增强版
包含AI基本面分析和深度风险检测
"""
import os
import sys
import pickle
import warnings
import time
import json
import requests
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

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")


class AIFundamentalAnalyzer:
    """AI基本面分析器"""
    
    def __init__(self):
        self.api_key = DEEPSEEK_API_KEY
        self.api_url = "https://api.deepseek.com/v1/chat/completions"
    
    def analyze_stock(self, stock_info):
        """使用AI分析个股基本面"""
        if not self.api_key or self.api_key == "sk-xxx":
            return self._default_analysis(stock_info)
        
        prompt = self._build_prompt(stock_info)
        
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "model": "deepseek-chat",
                "messages": [
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.3,
                "max_tokens": 500
            }
            
            response = requests.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                return self._parse_ai_response(result)
            else:
                return self._default_analysis(stock_info)
                
        except Exception as e:
            print(f"    ⚠️ AI分析失败: {e}")
            return self._default_analysis(stock_info)
    
    def _build_prompt(self, stock_info):
        """构建AI分析提示词"""
        name = stock_info.get('name', '')
        industry = stock_info.get('industry', '')
        market_cap = stock_info.get('market_cap', 0)
        monthly_return = stock_info.get('monthly_return', 0)
        fundamentals = stock_info.get('fundamentals', {})
        
        prompt = f"""请分析以下股票的基本面风险（用于量化选股）：

股票名称：{name}
所属行业：{industry}
总市值：{market_cap:.1f}亿元
本月涨幅：{monthly_return:.1f}%

财务指标：
- 市盈率(PE)：{fundamentals.get('pe', 'N/A')}
- 市净率(PB)：{fundamentals.get('pb', 'N/A')}
- 净利润增长率：{fundamentals.get('net_profit_growth', 'N/A')}%
- 资产负债率：{fundamentals.get('debt_ratio', 'N/A')}%
- 毛利率：{fundamentals.get('gross_margin', 'N/A')}%

请从以下维度评估风险（只输出JSON格式）：
{{
  "risk_level": "低/中/高",
  "has_reduced_risk": true/false,
  "has_private_placement_risk": true/false,
  "has_financial_risk": true/false,
  "summary": "一句话总结",
  "recommendation": "推荐/观望/回避"
}}

注意：只输出JSON，不要有其他文字。"""
        
        return prompt
    
    def _parse_ai_response(self, response):
        """解析AI响应"""
        try:
            content = response['choices'][0]['message']['content']
            content = content.strip()
            if content.startswith('```json'):
                content = content[7:]
            if content.startswith('```'):
                content = content[3:]
            if content.endswith('```'):
                content = content[:-3]
            
            result = json.loads(content.strip())
            return result
        except:
            return self._default_analysis({})
    
    def _default_analysis(self, stock_info):
        """默认分析结果"""
        return {
            "risk_level": "中",
            "has_reduced_risk": False,
            "has_private_placement_risk": False,
            "has_financial_risk": False,
            "summary": "基础筛选通过",
            "recommendation": "观望"
        }


class RiskDetector:
    """风险检测器"""
    
    def __init__(self):
        pass
    
    def check_all_risks(self, ts_code, name):
        """综合风险检测"""
        risks = []
        
        # 1. ST风险
        if self._is_st_risk(ts_code):
            risks.append("ST或退市风险")
        
        # 2. 减持风险（简化版）
        if self._has_reduced_history(ts_code):
            risks.append("近期有减持")
        
        # 3. 增发风险
        if self._has_private_placement(ts_code):
            risks.append("近期有增发预案")
        
        # 4. 财务风险
        financial_risk = self._check_financial_risk(ts_code)
        if financial_risk:
            risks.append(financial_risk)
        
        return risks
    
    def _is_st_risk(self, ts_code):
        """检查ST风险"""
        try:
            df = pro.stk_rewards(ts_code=ts_code, trade_date=datetime.now().strftime('%Y%m%d'))
            if df is not None and len(df) > 0:
                for col in df.columns:
                    if 'st' in col.lower():
                        return True
            return False
        except:
            return False
    
    def _has_reduced_history(self, ts_code):
        """检查减持历史（简化版）"""
        # 由于tushare免费版限制，这里返回False
        # 实际生产环境需要接入更多数据源
        return False
    
    def _has_private_placement(self, ts_code):
        """检查增发预案"""
        # 由于tushare免费版限制，这里返回False
        # 实际生产环境需要接入更多数据源
        return False
    
    def _check_financial_risk(self, ts_code):
        """检查财务风险"""
        try:
            df = pro.fina_indicator(
                ts_code=ts_code,
                start_date=(datetime.now() - timedelta(days=180)).strftime('%Y%m%d'),
                fields='ts_code,roe,net_profit_ratio,debt_ratio'
            )
            
            if df is not None and len(df) > 0:
                latest = df.iloc[0]
                
                # ROE过低的警告
                if latest.get('roe', 0) < 0:
                    return "净利润为负"
                
                # 负债率过高的警告
                if latest.get('debt_ratio', 0) > 90:
                    return "资产负债率过高"
                
            return None
        except:
            return None


class MonthlyETFDragonEnhanced:
    """增强版月度ETF龙头股筛选器"""
    
    def __init__(self):
        self.themes = self._load_themes()
        self.cache = {}
        self.ai_analyzer = AIFundamentalAnalyzer()
        self.risk_detector = RiskDetector()
        
    def _load_themes(self):
        """从theme.json加载主题配置"""
        with open(THEME_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get('HOT_THEMES', {})
    
    def _get_cache(self, key):
        """获取缓存"""
        cache_file = os.path.join(CACHE_DIR, f"cache_enhanced_{key}.pkl")
        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'rb') as f:
                    return pickle.load(f)
            except:
                return None
        return None
    
    def _set_cache(self, key, data):
        """设置缓存"""
        cache_file = os.path.join(CACHE_DIR, f"cache_enhanced_{key}.pkl")
        try:
            with open(cache_file, 'wb') as f:
                pickle.dump(data, f)
        except:
            pass
    
    def get_monthly_data(self, ts_code, months=12):
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
            
            if df is None or len(df) == 0:
                return None
            
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
        
        df['monthly_return'] = df['close'].pct_change() * 100
        df['volume_ma3'] = df['vol'].rolling(3).mean()
        df['volume_ratio'] = df['vol'] / df['volume_ma3']
        
        df['ma5'] = df['close'].rolling(5).mean()
        df['ma10'] = df['close'].rolling(10).mean()
        df['ma20'] = df['close'].rolling(20).mean()
        
        exp12 = df['close'].ewm(span=12, adjust=False).mean()
        exp26 = df['close'].ewm(span=26, adjust=False).mean()
        df['macd'] = exp12 - exp26
        df['signal'] = df['macd'].ewm(span=9, adjust=False).mean()
        df['histogram'] = df['macd'] - df['signal']
        
        return df
    
    def get_fundamentals(self, ts_code):
        """获取基本面数据"""
        try:
            df = pro.daily_basic(
                ts_code=ts_code,
                trade_date=datetime.now().strftime('%Y%m%d'),
                fields='ts_code,total_mv,circ_mv,pe,pb'
            )
            
            if df is not None and len(df) > 0:
                return df.iloc[0]
            return None
        except:
            return None
    
    def is_uptrend(self, df):
        """判断月线趋势是否向上"""
        if df is None or len(df) < 3:
            return False
        
        last = df.iloc[-1]
        
        has_ma10 = pd.notna(last['ma10'])
        has_ma20 = pd.notna(last['ma20'])
        
        ma_bullish = has_ma10 and (last['ma5'] > last['ma10'])
        if has_ma20:
            ma_bullish = ma_bullish and (last['ma10'] > last['ma20'])
        
        if len(df) >= 2:
            macd_golden = (df.iloc[-2]['macd'] < df.iloc[-2]['signal']) and (last['macd'] > last['signal'])
        else:
            macd_golden = False
        
        if has_ma10:
            price_above_ma10 = last['close'] > last['ma10']
        else:
            price_above_ma10 = last['close'] > last['ma5']
        
        macd_positive = last['histogram'] > 0
        
        return macd_positive or (macd_golden and price_above_ma10) or ma_bullish
    
    def is_too_rally(self, df, threshold=30):
        """判断是否大幅拉升"""
        if df is None or len(df) < 2:
            return True
        
        last_month = df.iloc[-1]
        return_month = last_month['monthly_return']
        return return_month > threshold
    
    def get_market_cap(self, ts_code):
        """获取个股总市值"""
        try:
            df = pro.daily_basic(
                ts_code=ts_code,
                fields='ts_code,trade_date,total_mv'
            )
            
            if df is not None and len(df) > 0:
                # 取最新的一条数据
                df = df.sort_values('trade_date', ascending=False).head(1)
                return df.iloc[0]['total_mv']
            return 0
        except:
            return 0
    
    def is_chuangye_or_kechuang(self, ts_code):
        """判断是否双创股票"""
        code = ts_code.split('.')[0]
        is_cyb = code.startswith('300')
        is_kcb = code.startswith('688')
        return is_cyb, is_kcb
    
    def calculate_score(self, df, market_cap, is_cyb, is_kcb, fundamentals):
        """计算龙头得分"""
        if df is None or len(df) < 3:
            return 0, {}
        
        last = df.iloc[-1]
        scores = {}
        
        monthly_return = last['monthly_return'] if pd.notna(last['monthly_return']) else 0
        scores['return'] = min(max(monthly_return / 10, 0), 10)
        
        vol_ratio = last['volume_ratio'] if pd.notna(last['volume_ratio']) else 1
        scores['volume'] = min(max((vol_ratio - 1) * 5, 0), 10)
        
        trend_score = 0
        if last['ma5'] > last['ma10']:
            trend_score += 3
        if last['ma10'] > last['ma20']:
            trend_score += 3
        if last['histogram'] > 0:
            trend_score += 4
        scores['trend'] = trend_score
        
        fundamental_score = 7
        if fundamentals is not None:
            pe = fundamentals.get('pe', 0)
            if pe > 0 and pe < 50:
                fundamental_score += 2
            elif pe > 100:
                fundamental_score -= 2
        scores['fundamental'] = fundamental_score
        
        elasticity_score = 0
        if is_kcb:
            elasticity_score += 5
        elif is_cyb:
            elasticity_score += 3
        
        cap_yi = market_cap / 10000
        if 100 <= cap_yi <= 500:
            elasticity_score += 5
        elif cap_yi > 500:
            elasticity_score += 3
        scores['elasticity'] = min(elasticity_score, 10)
        
        total_score = (
            scores['return'] * 0.25 +
            scores['volume'] * 0.25 +
            scores['trend'] * 0.20 +
            scores['fundamental'] * 0.15 +
            scores['elasticity'] * 0.15
        )
        
        return total_score, scores
    
    def analyze_stock(self, company, ts_code):
        """综合分析单只股票"""
        try:
            market_cap = self.get_market_cap(ts_code)
            if market_cap < 1000000:
                return None
            
            monthly_df = self.get_monthly_data(ts_code, months=6)
            if monthly_df is None or len(monthly_df) < 3:
                return None
            
            indicator_df = self.calculate_monthly_indicators(monthly_df)
            if indicator_df is None:
                return None
            
            if not self.is_uptrend(indicator_df):
                return None
            
            if self.is_too_rally(indicator_df, threshold=30):
                return None
            
            is_cyb, is_kcb = self.is_chuangye_or_kechuang(ts_code)
            
            fundamentals = self.get_fundamentals(ts_code)
            
            score, score_details = self.calculate_score(
                indicator_df, market_cap, is_cyb, is_kcb, fundamentals
            )
            
            risks = self.risk_detector.check_all_risks(ts_code, company)
            
            stock_info = {
                'name': company,
                'industry': fundamentals.get('industry', '') if fundamentals else '',
                'market_cap': market_cap / 10000,
                'fundamentals': {
                    'pe': fundamentals.get('pe', 0) if fundamentals else 0,
                    'pb': fundamentals.get('pb', 0) if fundamentals else 0,
                }
            }
            ai_analysis = self.ai_analyzer.analyze_stock(stock_info)
            
            return {
                'ts_code': ts_code,
                'name': company,
                'market_cap': market_cap / 10000,
                'is_cyb': is_cyb,
                'is_kcb': is_kcb,
                'monthly_return': indicator_df.iloc[-1]['monthly_return'],
                'volume_ratio': indicator_df.iloc[-1]['volume_ratio'],
                'score': score,
                'score_details': score_details,
                'risks': risks,
                'ai_analysis': ai_analysis
            }
            
        except Exception as e:
            print(f"    ⚠️ 分析 {company} 时出错: {e}")
            return None
    
    def analyze_theme(self, theme_name, theme_config, limit=3):
        """分析单个主题的龙头股"""
        print(f"\n{'='*60}")
        print(f"📊 分析主题：{theme_name}")
        print(f"{'='*60}")
        
        etf_code = theme_config.get('etf', '')
        core_companies = theme_config.get('core_companies', [])
        
        print(f"  ETF代码: {etf_code}")
        print(f"  核心公司数量: {len(core_companies)}")
        
        candidates = []
        
        for company in core_companies:
            try:
                name_df = pro.stock_basic(
                    ts_code='',
                    name=company,
                    fields='ts_code,name,industry,market'
                )
                
                if name_df is not None and len(name_df) > 0:
                    ts_code = name_df.iloc[0]['ts_code']
                    result = self.analyze_stock(company, ts_code)
                    
                    if result:
                        candidates.append(result)
                        time.sleep(0.1)
                        
            except Exception as e:
                print(f"    ⚠️ 搜索 {company} 失败: {e}")
                continue
        
        candidates = sorted(candidates, key=lambda x: x['score'], reverse=True)
        return candidates[:limit]
    
    def run_analysis(self, themes_to_analyze=None):
        """运行完整分析"""
        print(f"\n{'#'*80}")
        print(f"🏆 月度ETF龙头股筛选分析 - 增强版")
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
            
            time.sleep(0.5)
        
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
                
                if dragon['risks']:
                    print(f"     ⚠️ 风险提示: {', '.join(dragon['risks'])}")
                
                if dragon['ai_analysis']:
                    ai = dragon['ai_analysis']
                    print(f"     🤖 AI分析: {ai.get('summary', '')}")
                    print(f"     📋 AI建议: {ai.get('recommendation', '')}")
                
                all_dragons.append({
                    'theme': theme_name,
                    **dragon
                })
        
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
            
            risk_flag = "⚠️" if dragon['risks'] else "✓"
            
            print(f"{i:2d}. {dragon['name']:8s} | {board_type:4s} | "
                  f"{dragon['theme']:12s} | {dragon['market_cap']:6.1f}亿 | "
                  f"+{dragon['monthly_return']:5.1f}% | 得分:{dragon['score']:.2f} {risk_flag}")


def main():
    """主函数"""
    print("启动月度ETF龙头股筛选系统...")
    
    analyzer = MonthlyETFDragonEnhanced()
    results = analyzer.run_analysis()
    analyzer.print_summary(results)


if __name__ == "__main__":
    main()
