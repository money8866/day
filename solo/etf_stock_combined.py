#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
20天强弱ETF龙头股筛选系统
使用 etf_mainline_strategy_tushare.py 的ETF数据接口和评分逻辑
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
PARENT_DIR = os.path.dirname(BASE_DIR)
DOTENV_PATH = os.path.join(PARENT_DIR, "config", ".env")
load_dotenv(DOTENV_PATH)

CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")
os.makedirs(CACHE_DIR, exist_ok=True)

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
pro = ts.pro_api(TUSHARE_TOKEN)

THEME_PATH = os.path.join(BASE_DIR, "theme.json")


# ============================================================
# 以下为 etf_mainline_strategy_tushare.py 的核心ETF接口
# ============================================================

ETF_POOL = {
    '半导体': '512480', '芯片': '159995', '半导体设备': '159516',
    '人工智能': '159819', '软件': '515230', '通信': '515880',
    '消费电子': '159732', '金融科技': '159851', '游戏': '159869',
    '新能源': '516160', '光伏': '515790', '储能': '159566',
    '电池': '159755', '新能源车': '515030', '创新药': '159992',
    '医疗器械': '159883', '医药': '512010', '军工': '512660',
    '航空航天': '159227', '机器人': '562500', '有色金属': '516650',
    '化工': '159870', '煤炭': '515220', '钢铁': '515210',
    '电力': '159611', '电网设备': '561380', '消费': '159928',
    '食品饮料': '159736', '酒': '512690', '家电': '159996',
    '证券': '512880', '银行': '512800', '红利': '515180',
    '黄金': '518880', '沪深300': '510300', '创业板': '159915',
    '上证50': '510050','双创ETF':'588300','科创ETF':'588050',
}

BENCHMARK_CODE = '510300'
MOM_PERIOD = 20


def get_last_trade_date():
    """获取最近交易日"""
    now = datetime.now()
    if now.hour < 15:
        query_date = (now - timedelta(days=1)).strftime('%Y%m%d')
    else:
        query_date = now.strftime('%Y%m%d')

    cal = pro.trade_cal(exchange='', start_date='20200101', end_date=query_date)
    cal = cal[cal['is_open'] == 1]
    last_trade_date = cal[cal['cal_date'] <= query_date]['cal_date'].max()
    return str(last_trade_date)


def get_etf_data(etf_code, days=150):
    """获取ETF数据"""
    try:
        today = datetime.now()
        start_date = (today - timedelta(days=days)).strftime("%Y%m%d")
        
        if etf_code.startswith("5") or etf_code.startswith("6"):
            ts_code = f"{etf_code}.SH"
        else:
            ts_code = f"{etf_code}.SZ"
        
        df = pro.fund_daily(
            ts_code=ts_code,
            start_date=start_date,
            fields="ts_code,trade_date,close,vol"
        )
        
        if df is not None and len(df) > 0:
            df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
            df = df.sort_values("trade_date").reset_index(drop=True)
            return df
        return None
    except Exception as e:
        print(f"  获取ETF数据失败 {etf_code}: {e}")
        return None


def calculate_etf_score(df, benchmark_df):
    """
    计算ETF多因子综合评分
    因子1: 20日动量 (40%)
    因子2: 量能配合 (25%)
    因子3: 风险调整收益 (20%)
    因子4: 相对强弱 (15%)
    """
    if len(df) < MOM_PERIOD + 5:
        return None

    close = df['close']

    mom_20d = close.pct_change(MOM_PERIOD).iloc[-1] * 100

    vol = df.get('vol', None)
    if vol is None or len(vol) < MOM_PERIOD:
        vol_score = 50
    else:
        recent_vol_avg = vol.tail(5).mean()
        hist_vol_avg = vol.tail(MOM_PERIOD).mean()
        vol_ratio = recent_vol_avg / (hist_vol_avg + 1e-6)
        vol_score = min(vol_ratio * 50, 100)

    daily_returns = close.pct_change().dropna()
    if len(daily_returns) >= MOM_PERIOD:
        volatility = daily_returns.tail(MOM_PERIOD).std() * np.sqrt(252) * 100
        if volatility > 0:
            risk_adj_score = min(mom_20d / volatility * 10, 100)
        else:
            risk_adj_score = 50
    else:
        risk_adj_score = 50

    if benchmark_df is not None and len(benchmark_df) >= MOM_PERIOD + 1:
        bm_return = benchmark_df['close'].pct_change(MOM_PERIOD).iloc[-1] * 100
        relative_strength = mom_20d - bm_return
        rel_score = 50 + relative_strength
        rel_score = max(0, min(100, rel_score))
    else:
        rel_score = 50

    total_score = (
        mom_20d * 0.40 +
        vol_score * 0.25 +
        risk_adj_score * 0.20 +
        rel_score * 0.15
    )

    return {
        'momentum': round(mom_20d, 2),
        'vol_score': round(vol_score, 2),
        'risk_adj': round(risk_adj_score, 2),
        'rel_strength': round(rel_score, 2),
        'total_score': round(total_score, 2)
    }


def rank_all_etfs():
    """对所有ETF进行多因子评分和排序"""
    print("\n" + "="*80)
    print("🔍 使用 etf_mainline_strategy_tushare.py 的ETF评分系统")
    print("="*80)
    print("\n评分公式:")
    print("  ETF综合分 = 20日动量×40% + 量能配合×25% + 风险调整×20% + 相对强弱×15%")
    print()

    # 获取基准数据
    benchmark_df = get_etf_data(BENCHMARK_CODE)
    print(f"  基准ETF: 沪深300 ({BENCHMARK_CODE})")

    rankings = []
    codes_ts = {}
    for name, code in ETF_POOL.items():
        if code.startswith("5") or code.startswith("6"):
            codes_ts[code] = f"{code}.SH"
        else:
            codes_ts[code] = f"{code}.SZ"

    print("\n📊 计算各ETF多因子得分...")
    for name, code in ETF_POOL.items():
        df = get_etf_data(code)
        if df is None:
            print(f"  ⚠️ {name}: 数据获取失败")
            continue

        factors = calculate_etf_score(df, benchmark_df)
        if factors is None:
            print(f"  ⚠️ {name}: 数据不足")
            continue

        latest = df["close"].iloc[-1]
        prev = df["close"].iloc[-2] if len(df) >= 2 else latest
        day_chg = (latest - prev) / prev * 100

        rankings.append({
            "code": code,
            "name": name,
            "close": latest,
            "day_chg": round(day_chg, 2),
            **factors
        })

        print(f"  {name:<10} 综合分: {factors['total_score']:6.1f} | "
              f"动量: {factors['momentum']:>+6.2f}% | "
              f"量能: {factors['vol_score']:5.1f} | "
              f"风险: {factors['risk_adj']:5.1f} | "
              f"相对: {factors['rel_strength']:5.1f}")

        time.sleep(0.2)

    rankings.sort(key=lambda x: x['total_score'], reverse=True)

    print("\n" + "="*80)
    print("🏆 ETF多因子综合评分排名")
    print("="*80)
    print(f"  {'排名':>2} {'名称':<10} {'代码':<8} {'综合分':>6} {'动量':>7} {'量能':>6} {'风险':>6} {'相对':>6}")
    print(f"  {'-'*60}")

    for i, r in enumerate(rankings[:10], 1):
        print(f"  {i:>2}. {r['name']:<10} {r['code']:<8} {r['total_score']:>6.1f} "
              f"{r['momentum']:>+7.2f}% {r['vol_score']:>6.1f} {r['risk_adj']:>6.1f} {r['rel_strength']:>6.1f}")

    return rankings


# ============================================================
# 以下为个股筛选逻辑
# ============================================================

class StockSelector:
    """个股筛选器"""
    
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
        cache_file = os.path.join(CACHE_DIR, f"cache_stock_{key}.pkl")
        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'rb') as f:
                    return pickle.load(f)
            except:
                return None
        return None
    
    def _set_cache(self, key, data):
        """设置缓存"""
        cache_file = os.path.join(CACHE_DIR, f"cache_stock_{key}.pkl")
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
            return False, f"均线不符合"
        
        price_above_ma5 = latest['close'] > latest['ma5']
        if not price_above_ma5:
            return False, f"价格未在MA5上方"
        
        vol_mild = 0.8 < latest.get('vol_ratio', 1) < 3.0
        if not vol_mild:
            return False, f"量比不符合"
        
        not_over_rally = latest.get('return_20', 0) < 50
        if not not_over_rally:
            return False, f"涨幅过大"
        
        return True, "符合"
    
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
            
            return {
                'ts_code': ts_code,
                'name': name,
                'market_cap': market_cap,
                'return_20': latest.get('return_20', 0),
                'vol_ratio': latest.get('vol_ratio', 1),
                'ma5': latest['ma5'],
                'ma20': latest['ma20'],
                'ma60': latest['ma60'],
                'score': latest.get('return_20', 0) / 5
            }
        except:
            return None
    
    def analyze_theme(self, theme_name, theme_config, etf_score):
        """分析主题对应的个股"""
        core_companies = theme_config.get('core_companies', [])
        
        print(f"\n\n{'='*80}")
        print(f"🏆 Top {etf_score['rank']}: {theme_name} (ETF: {etf_score['code']})")
        print(f"    ETF综合分: {etf_score['total_score']:.2f} | 动量: {etf_score['momentum']:+.2f}%")
        print(f"{'='*80}")
        
        candidates = []
        
        for company in core_companies:
            stock_info = self._search_stock(company)
            if not stock_info:
                continue
            
            ts_code = stock_info['ts_code']
            result = self.analyze_stock(ts_code, company)
            
            if result:
                result['market'] = stock_info.get('market', '')
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


def main():
    """主函数"""
    print("="*80)
    print("🚀 20天强弱ETF龙头股筛选系统")
    print("    (使用 etf_mainline_strategy_tushare.py 的ETF评分接口)")
    print("="*80)
    print(f"分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Step 1: 获取并排序所有ETF
    etf_rankings = rank_all_etfs()
    
    if not etf_rankings:
        print("❌ 没有找到有效的ETF数据")
        return
    
    # Step 2: 加载主题配置
    with open(THEME_PATH, 'r', encoding='utf-8') as f:
        themes = json.load(f).get('HOT_THEMES', {})
    
    # Step 3: 创建个股筛选器
    selector = StockSelector()
    
    # Step 4: 为每个ETF主题匹配并筛选个股
    print("\n\n" + "="*80)
    print("📈 ETF及对应个股详情")
    print("="*80)
    
    for i, etf in enumerate(etf_rankings[:10], 1):
        etf['rank'] = i
        
        # 在theme.json中查找对应的主题
        matched_theme = None
        for theme_name, theme_config in themes.items():
            # 简单匹配：主题名称包含ETF名称
            if theme_name in etf['name'] or etf['name'] in theme_name:
                matched_theme = theme_name
                break
            # 检查core_companies中的公司是否与ETF相关
            if theme_config.get('etf', '') == etf['code']:
                matched_theme = theme_name
                break
        
        if matched_theme:
            theme_config = themes[matched_theme]
            selector.analyze_theme(matched_theme, theme_config, etf)
        else:
            print(f"\n\n{'='*80}")
            print(f"🏆 Top {i}: {etf['name']} (ETF: {etf['code']})")
            print(f"    ETF综合分: {etf['total_score']:.2f} | 动量: {etf['momentum']:+.2f}%")
            print(f"{'='*80}")
            print(f"\n  ⚠️ 未在theme.json中找到对应主题配置")
    
    print("\n\n" + "="*80)
    print("✅ 筛选完成")
    print("="*80)


if __name__ == "__main__":
    main()
