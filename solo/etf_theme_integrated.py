#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
20天强弱ETF龙头股筛选系统（整合版）
使用 theme_portfolio_strategy_cached.py 生成的主题和成分股数据
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
# ETF评分逻辑（来自 etf_mainline_strategy_tushare.py）
# ============================================================

ETF_POOL = {
    '半导体': '512480', '芯片': '159995', '半导体设备': '159516',
    '人工智能': '159819', '软件': '515230', '通信': '515880',
    '消费电子': '159732', '金融科技': '159851', '游戏': '159869',
    '新能源': '516160', '光伏': '515790', '储能': '159566',
    '电池': '159755', '新能源车': '515030', '创新药': '159992',
    '医疗器械': '159886', '医药': '512010', '军工': '512660',
    '航空航天': '159227', '机器人': '562500', '有色金属': '516650',
    '化工': '159870', '煤炭': '515220', '钢铁': '515210',
    '电力': '159628', '电网设备': '561700', '消费': '159928',
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
    if df is None or len(df) < MOM_PERIOD + 5:
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
    print("🔍 ETF多因子评分系统（来自 etf_mainline_strategy_tushare.py）")
    print("="*80)
    print("\n评分公式:")
    print("  ETF综合分 = 20日动量×40% + 量能配合×25% + 风险调整×20% + 相对强弱×15%")
    print()

    # 获取基准数据
    benchmark_df = get_etf_data(BENCHMARK_CODE)
    print(f"  基准ETF: 沪深300 ({BENCHMARK_CODE})")

    rankings = []

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

        print(f"  {name:10s} 综合分: {factors['total_score']:6.1f} | "
              f"动量: {factors['momentum']:>+7.2f}% | "
              f"量能: {factors['vol_score']:5.1f} | "
              f"风险: {factors['risk_adj']:5.1f} | "
              f"相对: {factors['rel_strength']:5.1f}")

        time.sleep(0.2)

    rankings.sort(key=lambda x: x['total_score'], reverse=True)

    print("\n" + "="*80)
    print("🏆 ETF多因子综合评分排名 (TOP 15)")
    print("="*80)
    print(f"  {'排名':<4} {'名称':<12} {'代码':<8} {'综合分':>6} {'动量':>8} {'量能':>6} {'风险':>6} {'相对':>6}")
    print(f"  {'-'*65}")

    for i, r in enumerate(rankings[:15], 1):
        print(f"  {i:<4} {r['name']:<12} {r['code']:<8} {r['total_score']:6.1f} "
              f"{r['momentum']:>+8.2f}% {r['vol_score']:6.1f} {r['risk_adj']:6.1f} {r['rel_strength']:6.1f}")

    return rankings


# ============================================================
# 个股筛选逻辑
# ============================================================

def load_theme_portfolio_csv():
    """从 theme_portfolio_strategy_cached.py 生成的 CSV 文件加载主题和成分股"""
    print("\n" + "="*80)
    print("📂 加载主题投资组合数据...")
    print("="*80)
    
    # 查找最新的 theme_portfolio CSV 文件
    csv_files = []
    for filename in os.listdir(CACHE_DIR):
        if filename.startswith("theme_portfolio_") and filename.endswith(".csv"):
            csv_files.append(filename)
    
    if not csv_files:
        print("\n❌ 未找到 theme_portfolio CSV 文件！")
        print("   请先运行: python theme_portfolio_strategy_cached.py")
        return None
    
    # 取最新的文件
    csv_files.sort(reverse=True)
    latest_csv = csv_files[0]
    csv_path = os.path.join(CACHE_DIR, latest_csv)
    
    print(f"\n✓ 找到文件: {latest_csv}")
    
    try:
        df = pd.read_csv(csv_path, encoding='utf-8-sig')
        print(f"✓ 加载成功: {len(df)} 条记录")
        print(f"✓ 主题数量: {df['themes'].nunique()}")
        print(f"✓ 股票数量: {df['ts_code'].nunique()}")
        return df
    except Exception as e:
        print(f"\n❌ 加载CSV失败: {e}")
        return None


def get_daily_data(ts_code, days=120):
    """获取个股日线K线数据"""
    cache_key = f"daily_{ts_code}_{days}"
    cache_file = os.path.join(CACHE_DIR, f"cache_{cache_key}.pkl")
    
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'rb') as f:
                return pickle.load(f)
        except:
            pass
    
    try:
        end_date = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - timedelta(days=days * 1.5)).strftime('%Y%m%d')
        
        df = pro.daily(
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields='ts_code,trade_date,open,high,low,close,vol,amount'
        )
        
        if df is not None and len(df) > 0:
            df = df.sort_values('trade_date').tail(days)
            with open(cache_file, 'wb') as f:
                pickle.dump(df, f)
            return df
        return None
    except Exception as e:
        return None


def calculate_strength_indicators(df):
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
    
    return df


def is_mild_upward(df):
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
        return False, f"价格未在MA5上方 (价格={latest['close']:.2f}, MA5={latest['ma5']:.2f})"
    
    vol_mild = 0.8 < latest.get('vol_ratio', 1) < 3.0
    if not vol_mild:
        return False, f"量比不符合 (量比={latest.get('vol_ratio', 1):.2f})"
    
    not_over_rally = latest.get('return_20', 0) < 50
    if not not_over_rally:
        return False, f"涨幅过大 (20日涨幅={latest.get('return_20', 0):.1f}%)"
    
    return True, "符合"


def match_theme_to_etf(theme_name, etf_rankings, theme_config):
    """匹配主题到ETF"""
    # 方法1: 通过 theme.json 中的 etf 字段
    if theme_config and theme_config.get('etf'):
        for etf in etf_rankings:
            if etf['code'] == theme_config['etf']:
                return etf
    
    # 方法2: 通过名称模糊匹配
    for etf in etf_rankings:
        etf_name = etf['name']
        if theme_name in etf_name or etf_name in theme_name:
            return etf
    
    # 方法3: 取评分最高的 ETF 作为参考
    if etf_rankings:
        return etf_rankings[0]
    
    return None


def analyze_theme_stocks(theme_name, theme_stocks, matching_etf):
    """分析主题中的股票"""
    candidates = []
    
    print(f"\n\n{'='*80}")
    print(f"🏷️  主题: {theme_name}")
    if matching_etf:
        print(f"📈  关联ETF: {matching_etf['name']} ({matching_etf['code']})")
        print(f"🎯  ETF评分: {matching_etf['total_score']:.1f} (动量 {matching_etf['momentum']:+.1f}%)")
    print(f"{'='*80}")
    
    print(f"\n🔍 筛选该主题中符合条件的股票 (共 {len(theme_stocks)} 只)...")
    
    for _, stock in theme_stocks.iterrows():
        ts_code = stock['ts_code']
        name = stock['name']
        market_type = stock.get('market_type', '')
        total_score = stock.get('total_score', 0)
        
        # 获取日线数据
        daily_df = get_daily_data(ts_code, days=120)
        if daily_df is None or len(daily_df) < 60:
            continue
        
        # 计算指标
        indicator_df = calculate_strength_indicators(daily_df)
        if indicator_df is None:
            continue
        
        # 判断是否符合条件
        is_pass, reason = is_mild_upward(indicator_df)
        if not is_pass:
            continue
        
        latest = indicator_df.iloc[-1]
        
        candidates.append({
            'ts_code': ts_code,
            'name': name,
            'market_type': market_type,
            'theme_score': total_score,
            'market_cap': stock.get('market_cap', 0),
            'return_20': latest.get('return_20', 0),
            'vol_ratio': latest.get('vol_ratio', 1),
            'ma5': latest['ma5'],
            'ma20': latest['ma20'],
            'ma60': latest['ma60'],
            'themes': theme_name
        })
    
    if candidates:
        candidates.sort(key=lambda x: x['theme_score'], reverse=True)
        
        print(f"\n✅ 找到 {len(candidates)} 只符合条件的股票:")
        print(f"\n  {'排名':<4} {'股票名称':<10} {'板块':<8} {'市值(亿)':<10} {'20日涨幅(%)':<12} {'量比':<8} {'主题得分':<10}")
        print(f"  {'-'*68}")
        
        for i, c in enumerate(candidates[:10], 1):
            market_tag = ""
            if c['market_type'] == '创业板':
                market_tag = "【创】"
            elif c['market_type'] == '科创板':
                market_tag = "【科】"
            
            print(f"  {i:<4} {market_tag}{c['name']:<8} {c['market_type']:<6} {c['market_cap']:<10.1f}"
                  f"{c['return_20']:<12.1f} {c['vol_ratio']:<8.2f} {c['theme_score']:<10.0f}")
    else:
        print(f"\n❌ 该主题暂无非符合条件的股票")
    
    return candidates


def main():
    """主函数"""
    print("="*80)
    print("🚀 20天强弱ETF龙头股筛选系统（整合版）")
    print("="*80)
    print(f"分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 步骤1: 加载主题投资组合数据
    portfolio_df = load_theme_portfolio_csv()
    if portfolio_df is None:
        return
    
    # 步骤2: ETF多因子评分和排序
    etf_rankings = rank_all_etfs()
    if not etf_rankings:
        print("\n❌ ETF评分失败")
        return
    
    # 步骤3: 加载 theme.json 用于主题配置匹配
    with open(THEME_PATH, 'r', encoding='utf-8') as f:
        theme_configs = json.load(f).get('HOT_THEMES', {})
    
    # 步骤4: 逐个分析主题和股票
    all_candidates = []
    
    for theme_name in portfolio_df['themes'].unique():
        theme_stocks = portfolio_df[portfolio_df['themes'] == theme_name]
        
        # 匹配主题到 ETF
        theme_config = theme_configs.get(theme_name, {})
        matching_etf = match_theme_to_etf(theme_name, etf_rankings, theme_config)
        
        # 分析该主题的股票
        candidates = analyze_theme_stocks(theme_name, theme_stocks, matching_etf)
        all_candidates.extend(candidates)
    
    # 步骤5: 汇总所有符合条件的股票
    if all_candidates:
        all_candidates.sort(key=lambda x: x['theme_score'], reverse=True)
        
        print("\n\n" + "="*80)
        print("🏆 全市场符合条件个股汇总")
        print("="*80)
        print(f"\n  {'排名':<4} {'股票名称':<10} {'板块':<8} {'所属主题':<15} {'市值(亿)':<10} {'20日涨幅(%)':<12} {'量比':<8} {'主题得分':<10}")
        print(f"  {'-'*85}")
        
        for i, c in enumerate(all_candidates[:20], 1):
            market_tag = ""
            if c['market_type'] == '创业板':
                market_tag = "【创】"
            elif c['market_type'] == '科创板':
                market_tag = "【科】"
            
            print(f"  {i:<4} {market_tag}{c['name']:<8} {c['market_type']:<6} {c['themes']:<15}"
                  f"{c['market_cap']:<10.1f} {c['return_20']:<12.1f} {c['vol_ratio']:<8.2f} {c['theme_score']:<10.0f}")
        
        print(f"\n✅ 共找到 {len(all_candidates)} 只符合条件的股票")
    else:
        print(f"\n❌ 暂无非符合条件的股票")
    
    print("\n" + "="*80)
    print("✅ 分析完成")
    print("="*80)


if __name__ == "__main__":
    main()
