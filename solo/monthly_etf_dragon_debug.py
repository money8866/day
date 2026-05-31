#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
月度ETF龙头股筛选系统 - 调试版
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


def load_themes():
    with open(THEME_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data.get('HOT_THEMES', {})


def get_monthly_data(ts_code, months=12):
    """获取个股月度K线数据"""
    try:
        end_date = datetime.now().strftime('%Y%m%d')
        # 获取足够的历史数据用于计算均线
        start_date = (datetime.now() - timedelta(days=months * 40)).strftime('%Y%m%d')
        
        print(f"    获取月度数据: {ts_code} (获取{months}个月)")
        df = pro.monthly(
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields='ts_code,trade_date,open,high,low,close,vol,amount'
        )
        
        if df is None or len(df) == 0:
            print(f"    ⚠️ 无月度数据")
            return None
        
        df = df.sort_values('trade_date').tail(months)
        print(f"    ✓ 获取到 {len(df)} 条月度数据")
        return df
    except Exception as e:
        print(f"    ✗ 获取月度数据失败: {e}")
        return None


def analyze_stock(company, theme_name):
    """分析单只股票"""
    print(f"\n{'='*60}")
    print(f"📊 分析股票: {company} (主题: {theme_name})")
    print(f"{'='*60}")
    
    try:
        # 1. 搜索股票
        print(f"\n  [1/6] 搜索股票...")
        name_df = pro.stock_basic(
            ts_code='',
            name=company,
            fields='ts_code,name,industry,market'
        )
        
        if name_df is None or len(name_df) == 0:
            print(f"    ✗ 未找到股票: {company}")
            return None
        
        ts_code = name_df.iloc[0]['ts_code']
        industry = name_df.iloc[0].get('industry', 'N/A')
        market = name_df.iloc[0].get('market', 'N/A')
        print(f"    ✓ 找到: {ts_code} | 行业: {industry} | 市场: {market}")
        
        # 2. 获取市值（使用最近交易日）
        print(f"\n  [2/6] 获取市值...")
        try:
            # 批量获取市值数据，取最新的一条
            cap_df = pro.daily_basic(
                ts_code=ts_code,
                fields='ts_code,trade_date,total_mv,circ_mv'
            )
            
            if cap_df is None or len(cap_df) == 0:
                print(f"    ✗ 无法获取市值数据")
                market_cap = 0
            else:
                # 取最新的一条数据
                cap_df = cap_df.sort_values('trade_date', ascending=False).head(1)
                market_cap = cap_df.iloc[0]['total_mv']  # 万元
                market_cap_yi = market_cap / 10000  # 亿元
                trade_date = cap_df.iloc[0]['trade_date']
                print(f"    ✓ 总市值: {market_cap_yi:.2f}亿元 (日期: {trade_date})")
        except Exception as e:
            print(f"    ✗ 获取市值失败: {e}")
            market_cap = 0
            market_cap_yi = 0
        
        # 市值筛选
        if market_cap < 1000000:  # 100亿
            print(f"    ✗ 市值不足100亿，筛选淘汰")
            return None
        
        # 3. 获取月度数据
        print(f"\n  [3/6] 获取月度数据...")
        monthly_df = get_monthly_data(ts_code, months=6)
        if monthly_df is None or len(monthly_df) < 3:
            print(f"    ✗ 月度数据不足")
            return None
        
        # 4. 计算技术指标
        print(f"\n  [4/6] 计算技术指标...")
        df = monthly_df.copy()
        
        # 月度涨跌幅
        df['monthly_return'] = df['close'].pct_change() * 100
        
        # 均线系统
        df['ma5'] = df['close'].rolling(5).mean()
        df['ma10'] = df['close'].rolling(10).mean()
        df['ma20'] = df['close'].rolling(20).mean()
        
        # MACD
        exp12 = df['close'].ewm(span=12, adjust=False).mean()
        exp26 = df['close'].ewm(span=26, adjust=False).mean()
        df['macd'] = exp12 - exp26
        df['signal'] = df['macd'].ewm(span=9, adjust=False).mean()
        df['histogram'] = df['macd'] - df['signal']
        
        last = df.iloc[-1]
        print(f"    最新收盘价: {last['close']:.2f}")
        print(f"    本月涨幅: {last['monthly_return']:.2f}%")
        print(f"    5月线: {last['ma5']:.2f}, 10月线: {last['ma10']:.2f}, 20月线: {last['ma20']:.2f}")
        print(f"    MACD柱状图: {last['histogram']:.4f}")
        
        # 5. 趋势判断
        print(f"\n  [5/6] 趋势判断...")
        
        # 检查均线数据是否足够
        has_ma10 = pd.notna(last['ma10'])
        has_ma20 = pd.notna(last['ma20'])
        
        # 均线多头排列（至少需要ma5 > ma10）
        ma_bullish = has_ma10 and (last['ma5'] > last['ma10'])
        if has_ma20:
            ma_bullish = ma_bullish and (last['ma10'] > last['ma20'])
        print(f"    均线多头排列: {'✓' if ma_bullish else '✗'}")
        
        # MACD金叉
        if len(df) >= 2:
            macd_golden = (df.iloc[-2]['macd'] < df.iloc[-2]['signal']) and (last['macd'] > last['signal'])
        else:
            macd_golden = False
        print(f"    MACD金叉: {'✓' if macd_golden else '✗'}")
        
        # 价格站上10月均线（如果没有ma10，检查ma5）
        if has_ma10:
            price_above_ma10 = last['close'] > last['ma10']
        else:
            price_above_ma10 = last['close'] > last['ma5']  # 用ma5替代
        print(f"    价格站上均线: {'✓' if price_above_ma10 else '✗'}")
        
        # MACD柱状图大于0
        macd_positive = last['histogram'] > 0
        print(f"    MACD柱状图>0: {'✓' if macd_positive else '✗'}")
        
        # 趋势向上（只要满足多个条件之一即可）
        is_uptrend = macd_positive or (macd_golden and price_above_ma10) or ma_bullish
        print(f"    趋势向上: {'✓' if is_uptrend else '✗'}")
        
        if not is_uptrend:
            print(f"    ✗ 趋势不符合要求")
            return None
        
        # 6. 涨幅筛选
        print(f"\n  [6/6] 涨幅筛选...")
        return_month = last['monthly_return']
        print(f"    本月涨幅: {return_month:.2f}%")
        
        if return_month > 30:
            print(f"    ✗ 涨幅超过30%，筛选淘汰")
            return None
        
        print(f"    ✓ 涨幅符合要求 (<30%)")
        
        # 判断双创
        code = ts_code.split('.')[0]
        is_cyb = code.startswith('300')
        is_kcb = code.startswith('688')
        print(f"\n  板块判断:")
        print(f"    创业板: {'✓' if is_cyb else '✗'}")
        print(f"    科创板: {'✓' if is_kcb else '✗'}")
        
        # 计算综合得分
        scores = {}
        scores['return'] = min(max(return_month / 10, 0), 10)
        
        df['volume_ma3'] = df['vol'].rolling(3).mean()
        vol_ratio = df['vol'].iloc[-1] / df['volume_ma3'].iloc[-1] if pd.notna(df['volume_ma3'].iloc[-1]) else 1
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
        scores['fundamental'] = fundamental_score
        
        elasticity_score = 0
        if is_kcb:
            elasticity_score += 5
        elif is_cyb:
            elasticity_score += 3
        
        if 100 <= market_cap_yi <= 500:
            elasticity_score += 5
        elif market_cap_yi > 500:
            elasticity_score += 3
        scores['elasticity'] = min(elasticity_score, 10)
        
        total_score = (
            scores['return'] * 0.25 +
            scores['volume'] * 0.25 +
            scores['trend'] * 0.20 +
            scores['fundamental'] * 0.15 +
            scores['elasticity'] * 0.15
        )
        
        print(f"\n  📊 综合得分: {total_score:.2f}/10")
        print(f"    涨幅得分: {scores['return']:.2f}")
        print(f"    成交量得分: {scores['volume']:.2f}")
        print(f"    趋势得分: {scores['trend']:.2f}")
        print(f"    基本面得分: {scores['fundamental']:.2f}")
        print(f"    弹性得分: {scores['elasticity']:.2f}")
        
        return {
            'ts_code': ts_code,
            'name': company,
            'theme': theme_name,
            'market_cap': market_cap_yi,
            'is_cyb': is_cyb,
            'is_kcb': is_kcb,
            'monthly_return': return_month,
            'volume_ratio': vol_ratio,
            'score': total_score,
            'scores': scores
        }
        
    except Exception as e:
        print(f"    ✗ 分析失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    """主函数"""
    print(f"\n{'#'*80}")
    print(f"🏆 月度ETF龙头股筛选分析 - 调试版")
    print(f"{'#'*80}")
    print(f"分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    themes = load_themes()
    
    # 只分析前3个主题进行测试
    test_themes = ['AI算力', '光模块', '人形机器人']
    
    all_dragons = []
    
    for theme_name in test_themes:
        if theme_name not in themes:
            continue
        
        theme_config = themes[theme_name]
        core_companies = theme_config.get('core_companies', [])
        
        print(f"\n\n{'#'*80}")
        print(f"📂 主题: {theme_name}")
        print(f"核心公司: {', '.join(core_companies[:5])}")
        
        for company in core_companies[:3]:  # 每个主题只测试前3个公司
            result = analyze_stock(company, theme_name)
            if result:
                all_dragons.append(result)
            
            time.sleep(0.3)
    
    # 打印结果
    print(f"\n\n{'#'*80}")
    print(f"📈 筛选结果汇总")
    print(f"{'#'*80}\n")
    
    if all_dragons:
        all_dragons = sorted(all_dragons, key=lambda x: x['score'], reverse=True)
        
        for i, dragon in enumerate(all_dragons, 1):
            board_type = ""
            if dragon['is_kcb']:
                board_type = "【科创板】"
            elif dragon['is_cyb']:
                board_type = "【创业板】"
            
            print(f"{i}. {board_type}{dragon['name']} ({dragon['ts_code']})")
            print(f"   主题: {dragon['theme']} | 市值: {dragon['market_cap']:.1f}亿 | "
                  f"本月涨幅: {dragon['monthly_return']:.1f}%")
            print(f"   综合得分: {dragon['score']:.2f}/10")
            print()
    else:
        print("未找到符合条件的龙头股")
        print("\n可能的原因:")
        print("1. 月度数据获取失败")
        print("2. 趋势筛选条件太严格")
        print("3. 市值不足100亿")


if __name__ == "__main__":
    main()
