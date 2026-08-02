#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主线板块 + 中军综合分析（Tushare东财版）
"""
import os
import sys
import pickle
import warnings
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv

import numpy as np
import pandas as pd
import tushare as ts

warnings.filterwarnings('ignore')

# =========================
# 环境变量
# =========================


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# .env 文件在 mystock 目录下
DOTENV_PATH = os.path.join(os.path.dirname(BASE_DIR), "/config/.env")
load_dotenv(DOTENV_PATH)

# 使用 solo 目录下的 cache_backbone_tushare 作为缓存目录
CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")
os.makedirs(CACHE_DIR, exist_ok=True)

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
pro = ts.pro_api(TUSHARE_TOKEN)

# =========================
# 获取最近交易日
# =========================
def get_last_trade_date():
    now = datetime.now()
    
    # 9点前：视为上一自然日
    if now.hour < 15:
        query_date = (now - timedelta(days=1)).strftime('%Y%m%d')
    else:
        query_date = now.strftime('%Y%m%d')
    
    # 获取交易日历
    cal = pro.trade_cal(
        exchange='',
        start_date='20200101',
        end_date=query_date
    )
    
    # 只保留开市日
    cal = cal[cal['is_open'] == 1]
    
    # 最近交易日
    last_trade_date = cal[
        cal['cal_date'] <= query_date
    ]['cal_date'].max()
    
    return str(last_trade_date)

TRADE_DATE = get_last_trade_date()
#TRADE_DATE = "20260522"  # 可手动指定测试
print("当前交易日:", TRADE_DATE)

# =========================
# 获取历史数据（带缓存）
# =========================
def get_hist_data(ts_code, n_days=120):
    cache_file = os.path.join(
        CACHE_DIR,
        f"{ts_code}.csv"
    )
    
    # 优先读取缓存
    if os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file)
            df['trade_date'] = df['trade_date'].astype(str)
            
            # 缓存中已存在目标日期
            if (df['trade_date'] == TRADE_DATE).any():
                # 只返回 TRADE_DATE 及之前的数据
                filtered_df = df[df['trade_date'] <= TRADE_DATE].copy()
                filtered_df = filtered_df.sort_values('trade_date').tail(n_days)
                return filtered_df
        except Exception as e:
            print(f"{ts_code} 缓存读取失败: {e}")
    
    # 下载最新数据
    try:
        # V2: 优先 daily_cache 表
        df = None
        try:
            from stock_cache import get_daily_cache, get_daily_cache_range, batch_insert_daily_cache
            _, _max_date = get_daily_cache_range(ts_code)
            if _max_date is not None and str(_max_date) >= str(TRADE_DATE):
                df = get_daily_cache(ts_code, '20250101', TRADE_DATE)
                if df is not None and not df.empty:
                    df['trade_date'] = df['trade_date'].astype(str)
        except Exception:
            pass
        if df is None or df.empty:
            df = pro.daily(
                ts_code=ts_code,
                start_date='20250101',
                end_date=TRADE_DATE
            )
            if df is not None and not df.empty:
                try:
                    from stock_cache import batch_insert_daily_cache
                    batch_insert_daily_cache(df)
                except Exception:
                    pass

        if df is None or df.empty:
            return None
        
        df = df.sort_values('trade_date')
        
        # 保存缓存
        df.to_csv(
            cache_file,
            index=False
        )
        
        # 防止频率限制
        time.sleep(0.01)
        
        return df.tail(n_days)
    except Exception as e:
        print(f"{ts_code} 下载失败:", e)
        return None

# =========================
# 获取概念板块映射（同花顺）
# =========================
def get_concept_map():
    print("\n[1/6] 获取同花顺概念板块映射...")

    cache_file = os.path.join(CACHE_DIR, "ths_concept_members.pkl")

    if os.path.exists(cache_file):
        try:
            df = pd.read_pickle(cache_file)
            if df is not None and len(df) > 0:
                print(f"   从缓存加载成功，共 {len(df)} 条记录")
                return build_maps_from_df(df)
        except Exception as e:
            print(f"   缓存读取失败: {e}")

    try:
        print("   正在获取同花顺概念板块列表...")
        index_df = pro.ths_index(
            ts_code="",
            exchange="A",
            type="N",
            name="",
            limit="",
            offset=""
        )

        if index_df is None or index_df.empty:
            print("   获取板块列表失败")
            return {}, {}

        print(f"   获取到 {len(index_df)} 个同花顺概念板块")

        all_members = []
        for idx, row in index_df.iterrows():
            ts_code = row['ts_code']
            name = row['name']

            try:
                member_df = pro.ths_member(
                    ts_code=ts_code,
                    con_code="",
                    offset="",
                    limit=""
                )
                if member_df is not None and not member_df.empty:
                    member_df['concept_name'] = name
                    all_members.append(member_df)

                time.sleep(0.05)

            except Exception as e:
                continue
            
            if idx % 50 == 0:
                print(f"   已处理 {idx+1}/{len(index_df)} 个板块")
        
        if all_members:
            df = pd.concat(all_members, ignore_index=True)
            df.to_pickle(cache_file)
            print(f"   保存缓存: {cache_file}")
            print(f"   共获取 {len(df)} 条成分股记录")
            return build_maps_from_df(df)
        else:
            print("   未获取到任何成分股数据")
            return {}, {}
            
    except Exception as e:
        print(f"   获取概念板块失败: {e}")
        return {}, {}

# =========================
# 过滤业绩类板块
# =========================
def is_performance_sector(sector_name):
    performance_keywords = [
        '年报', '一季报', '半年报', '三季报', '季报',
        '扭亏', '预增', '预减', '预盈', '预亏',
        '业绩', '中报'
    ]
    for keyword in performance_keywords:
        if keyword in sector_name:
            return True
    return False

def build_maps_from_df(df):
    concept_map = {}
    name_map = {}

    for concept_name, group in df.groupby('concept_name'):
        if is_performance_sector(concept_name):
            continue

        stocks = group['con_code'].unique().tolist() if 'con_code' in group.columns else group['CODE'].unique().tolist()
        if len(stocks) >= 30:
            concept_map[concept_name] = stocks

        for _, row in group.drop_duplicates('con_code' if 'con_code' in group.columns else 'CODE').iterrows():
            stock_code = row.get('con_code', row.get('CODE', ''))
            stock_name = row.get('con_name', stock_code)
            if stock_code not in name_map:
                name_map[stock_code] = stock_name

    print(f"   找到 {len(concept_map)} 个有效概念板块（成分股>=30，剔除业绩类板块）")

    return concept_map, name_map

# =========================
# 获取股票名称映射
# =========================
def get_stock_name_map():
    try:
        cache_file = os.path.join(CACHE_DIR, "ths_concept_members.pkl")
        if os.path.exists(cache_file):
            df = pd.read_pickle(cache_file)
            name_map = {}
            for _, row in df.iterrows():
                stock_code = row.get('con_code', row.get('CODE', ''))
                stock_name = row.get('con_name', stock_code)
                if stock_code not in name_map:
                    name_map[stock_code] = stock_name
            return name_map
    except Exception as e:
        print(f"   获取股票名称映射失败: {e}")
    return {}

# =========================
# 双创股票识别
# =========================
def is_chuangye_stock(ts_code):
    return ts_code.startswith('300')

def is_kechuang_stock(ts_code):
    return ts_code.startswith('688')

def is_shuangchuang_stock(ts_code):
    return is_chuangye_stock(ts_code) or is_kechuang_stock(ts_code)

def get_stock_elasticity_type(ts_code):
    if is_kechuang_stock(ts_code):
        return '科创'
    elif is_chuangye_stock(ts_code):
        return '创业'
    else:
        return '主板'

# =========================
# 行业细分龙头识别
# =========================
def get_industry_leader_info(ts_code):
    try:
        cache_file = os.path.join(CACHE_DIR, f"stock_industry_{ts_code}.pkl")
        if os.path.exists(cache_file):
            return pd.read_pickle(cache_file)
        
        df = pro.stock_basic(ts_code=ts_code, fields='ts_code,name,industry,market')
        if df is not None and not df.empty:
            df.to_pickle(cache_file)
            return df
    except:
        pass
    return pd.DataFrame()

def is_industry_leader(ts_code, name):
    leader_keywords = ['龙头', '细分龙头', '行业龙头', '龙头老大', '行业第一', '全球领先', '国内领先']
    for keyword in leader_keywords:
        if keyword in name:
            return True
    return False

# =========================
# 负面筛选：雷、增发、减持
# =========================
def check_negative_events(ts_code):
    try:
        cache_file = os.path.join(CACHE_DIR, f"negative_events_{ts_code}.pkl")
        if os.path.exists(cache_file):
            return pd.read_pickle(cache_file)
        
        results = {
            'has_st': False,
            'has_suspended': False,
            'recent_increase': False,
            'recent_reduce': False
        }
        
        try:
            df = pro.namechange(ts_code=ts_code, fields='ts_code,name,change_reason,ann_date')
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    reason = str(row.get('change_reason', ''))
                    if 'ST' in reason or '*ST' in reason:
                        results['has_st'] = True
                    if '暂停上市' in reason:
                        results['has_suspended'] = True
        except:
            pass
        
        try:
            limit_df = pro.limit_list_d(trade_date=TRADE_DATE)
            if limit_df is not None and not limit_df.empty:
                if ts_code in limit_df['ts_code'].values:
                    results['has_limit_down'] = True
        except:
            pass
        
        results_df = pd.DataFrame([results])
        results_df.to_pickle(cache_file)
        return results_df
    except:
        return pd.DataFrame([{'has_st': False, 'has_suspended': False, 'recent_increase': False, 'recent_reduce': False}])

def is_negative_stock(ts_code):
    events = check_negative_events(ts_code)
    if events is None or events.empty:
        return False
    return (events['has_st'].iloc[0] or 
            events['has_suspended'].iloc[0] or 
            events['recent_increase'].iloc[0] or 
            events['recent_reduce'].iloc[0])

# =========================
# 分析板块
# =========================
def analyze_sectors(daily_basic, concept_map):
    print("\n[2/6] 分析主线板块...")
    
    sector_results = []
    
    # 创建市值查询字典
    market_cap_dict = {}
    if not daily_basic.empty:
        for _, row in daily_basic.iterrows():
            market_cap_dict[row['ts_code']] = row
    
    analyzed_count = 0
    for sector_name, stocks in concept_map.items():
        if len(stocks) < 10 or analyzed_count >= 50:  # 分析前50个板块
            continue
        
        analyzed_count += 1
        
        # 获取板块成分股的日线数据
        sector_stocks_data = []
        stock_count = 0
        for stock_code in stocks:
            if stock_count >= 30:  # 每板块最多取30只
                break
            
            df = get_hist_data(stock_code, 30)
            if df is not None and len(df) > 5:
                sector_stocks_data.append(df)
                stock_count += 1
        
        if len(sector_stocks_data) < 5:
            continue
        
        # 计算板块动量和涨停数
        total_momentum = 0
        zt_count = 0
        total_amount = 0
        
        for df in sector_stocks_data:
            if len(df) >= 2:
                pct = df['pct_chg'].iloc[-1]
                total_momentum += pct
                if pct >= 9.5:
                    zt_count += 1
                # 获取成交金额（tushare amount单位为千元）
                if not df.empty:
                    last_row = df.iloc[-1]
                    if 'amount' in last_row:
                        total_amount += last_row['amount'] / 1000  # 转万元
        
        avg_momentum = total_momentum / len(sector_stocks_data) if sector_stocks_data else 0
        avg_amount = total_amount / len(sector_stocks_data) if sector_stocks_data else 0
        
        sector_results.append({
            '板块名称': sector_name,
            '板块类型': '概念',
            '成分股数': len(stocks),
            '分析股数': len(sector_stocks_data),
            '平均涨幅': round(avg_momentum, 2),
            '涨停数': zt_count,
            '动量': round(avg_momentum * 10, 2),
            '平均成交额(万)': round(avg_amount, 2) if avg_amount else 0
        })
    
    # 排序
    sector_results = sorted(sector_results, key=lambda x: x['动量'], reverse=True)
    
    return sector_results[:15]

# =========================
# 计算趋势强度
# =========================
def calculate_trend_strength(df):
    if len(df) < 20:
        return 0, None
    
    # 寻找启动点：最近20天内的相对低点 + 成交量放大
    recent_20 = df.iloc[-20:].copy()
    
    # 找成交量放大的点
    avg_vol_20 = recent_20['vol'].mean()
    volume_spike_idx = None
    for i in range(len(recent_20)):
        if recent_20['vol'].iloc[i] > avg_vol_20 * 1.2:
            volume_spike_idx = i
            break
    
    if volume_spike_idx is None:
        volume_spike_idx = 0
    
    start_idx = max(0, volume_spike_idx - 2)
    start_price = recent_20['close'].iloc[start_idx]
    end_price = recent_20['close'].iloc[-1]
    
    if start_price <= 0:
        return 0, None
    
    # 计算涨幅
    total_return = (end_price - start_price) / start_price * 100
    days = len(recent_20) - start_idx
    avg_daily_return = total_return / days if days > 0 else 0
    
    # 计算波动率
    returns = recent_20['pct_chg'].iloc[start_idx:].dropna()
    volatility = returns.std() if len(returns) > 0 else 100
    
    # 稳定性加分
    stability_bonus = 5 if volatility < 3 else 0
    stability_bonus += 3 if volatility < 2 else 0
    
    # 合理涨幅加分（10%-80%比较健康）
    healthy_bonus = 0
    if 10 <= total_return <= 80:
        healthy_bonus = 3
    
    # 基础评分：日均涨幅
    base_score = min(avg_daily_return * 2, 20)  # 日均涨幅*2，上限20分
    
    total_score = base_score + stability_bonus + healthy_bonus
    
    trend_info = {
        '启动天数': days,
        '累计涨幅': round(total_return, 2),
        '日均涨幅': round(avg_daily_return, 3),
        '波动率': round(volatility, 2)
    }
    
    return min(total_score, 20), trend_info

# =========================
# 检查平台突破
# =========================
def check_platform_breakout(df):
    if len(df) < 30:
        return False, 0
    
    # 前20天找平台
    platform_data = df.iloc[-30:-10]
    if len(platform_data) < 15:
        return False, 0
    
    # 计算平台价格区间
    platform_high = platform_data['high'].max()
    platform_low = platform_data['low'].min()
    
    # 平台波动小于25%视为平台
    platform_range = (platform_high - platform_low) / platform_low if platform_low > 0 else 1
    if platform_range > 0.25:
        return False, 0
    
    # 最近10天是否突破
    recent_data = df.iloc[-10:]
    breakouts = recent_data[recent_data['close'] > platform_high]
    
    if len(breakouts) >= 2:
        # 突破确认
        return True, 15
    elif len(breakouts) >= 1:
        return True, 10
    else:
        return False, 0

# =========================
# 检查均线排列
# =========================
def check_ma_pattern(df):
    if len(df) < 30:
        return 0
    
    try:
        df = df.copy()
        df['ma5'] = df['close'].rolling(window=5).mean()
        df['ma20'] = df['close'].rolling(window=20).mean()
        df['ma60'] = df['close'].rolling(window=60).mean()
        
        if df['ma5'].isnull().any() or df['ma20'].isnull().any() or df['ma60'].isnull().any():
            return 0
        
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else last
        
        score = 0
        
        # MA5 > MA20
        if last['ma5'] > last['ma20']:
            score += 8
        
        # MA20 > MA60
        if last['ma20'] > last['ma60']:
            score += 8
        
        # 多头排列
        if last['ma5'] > last['ma20'] and last['ma20'] > last['ma60']:
            score += 4
        
        # MA20金叉MA60
        if prev['ma20'] < prev['ma60'] and last['ma20'] > last['ma60']:
            score += 10
        
        return min(score, 20)
    except:
        return 0

# =========================
# 分析单只股票的中军特征
# =========================
def analyze_backbone_stock(stock_code, df, daily_basic_row, name_map):
    if df is None or len(df) < 30:
        return None
    
    score = 0
    details = {}
    
    # 负面筛选：ST、退市、减持等
    if is_negative_stock(stock_code):
        return None
    
    # 股票名称
    stock_name = name_map.get(stock_code, stock_code)
    
    # 0. 双创股票加分（弹性优先）
    elasticity_bonus = 0
    if is_kechuang_stock(stock_code):
        elasticity_bonus = 15
        details['板块类型'] = '科创板'
    elif is_chuangye_stock(stock_code):
        elasticity_bonus = 12
        details['板块类型'] = '创业板'
    else:
        details['板块类型'] = '主板'
    score += elasticity_bonus
    details['弹性加分'] = elasticity_bonus
    
    # 1. 行业细分龙头加分
    leader_bonus = 0
    if is_industry_leader(stock_code, stock_name):
        leader_bonus = 10
        score += leader_bonus
        details['行业龙头'] = True
        details['龙头加分'] = leader_bonus
    else:
        details['行业龙头'] = False
        details['龙头加分'] = 0
    
    # 2. 市值评分（优化：双创允许更小市值）
    total_mv = float(daily_basic_row['total_mv']) if pd.notna(daily_basic_row['total_mv']) else 0
    market_cap_billion = total_mv / 10000  # 转换为亿
    
    if is_shuangchuang_stock(stock_code):
        if 50 <= market_cap_billion <= 800:
            score += 20
            details['市值_亿'] = round(market_cap_billion, 2)
            details['市值评分'] = 20
        elif 30 <= market_cap_billion < 50:
            score += 18
            details['市值_亿'] = round(market_cap_billion, 2)
            details['市值评分'] = 18
        elif 20 <= market_cap_billion < 30:
            score += 15
            details['市值_亿'] = round(market_cap_billion, 2)
            details['市值评分'] = 15
        else:
            return None
    else:
        if 100 <= market_cap_billion <= 1000:
            score += 20
            details['市值_亿'] = round(market_cap_billion, 2)
            details['市值评分'] = 20
        elif 50 <= market_cap_billion < 100:
            score += 15
            details['市值_亿'] = round(market_cap_billion, 2)
            details['市值评分'] = 15
        else:
            return None
    
    # 3. 成交金额评分（优化：双创允许更低成交额）
    amount = 0
    if len(df) > 0:
        last_row = df.iloc[-1]
        if 'amount' in last_row:
            amount = last_row['amount']  # 千元
    
    amount_wan = amount / 10  # 转万元
    amount_billion = amount / 100000  # 转亿元
    
    if is_shuangchuang_stock(stock_code):
        if amount_billion >= 5:
            score += 25
            details['成交额_万'] = round(amount_wan, 2)
            details['成交额评分'] = 25
            details['成交额档位'] = '5亿+'
        elif amount_billion >= 2:
            score += 20
            details['成交额_万'] = round(amount_wan, 2)
            details['成交额评分'] = 20
            details['成交额档位'] = '2亿+'
        elif amount_billion >= 0.5:
            score += 15
            details['成交额_万'] = round(amount_wan, 2)
            details['成交额评分'] = 15
            details['成交额档位'] = '5000万+'
        else:
            return None
    else:
        if amount_billion >= 30:
            score += 25
            details['成交额_万'] = round(amount_wan, 2)
            details['成交额评分'] = 25
            details['成交额档位'] = '30亿+'
        elif amount_billion >= 10:
            score += 20
            details['成交额_万'] = round(amount_wan, 2)
            details['成交额评分'] = 20
            details['成交额档位'] = '10亿+'
        elif amount_billion >= 2:
            score += 15
            details['成交额_万'] = round(amount_wan, 2)
            details['成交额评分'] = 15
            details['成交额档位'] = '2亿+'
        else:
            return None
    
    # 4. 换手率评分（优化：双创允许更高换手率）
    turnover_rate = float(daily_basic_row['turnover_rate']) if pd.notna(daily_basic_row['turnover_rate']) else 0
    
    if is_shuangchuang_stock(stock_code):
        if 3 <= turnover_rate <= 25:
            score += 15
            details['换手率'] = round(turnover_rate, 2)
            details['换手率评分'] = 15
        elif turnover_rate > 25:
            details['换手率'] = round(turnover_rate, 2)
            details['换手率评分'] = 12
        elif turnover_rate >= 1:
            details['换手率'] = round(turnover_rate, 2)
            details['换手率评分'] = 10
        else:
            return None
    else:
        if 3 <= turnover_rate <= 15:
            score += 15
            details['换手率'] = round(turnover_rate, 2)
            details['换手率评分'] = 15
        elif turnover_rate > 15:
            details['换手率'] = round(turnover_rate, 2)
            details['换手率评分'] = 12
        elif turnover_rate >= 1:
            details['换手率'] = round(turnover_rate, 2)
            details['换手率评分'] = 10
        else:
            return None
    
    # 4. 均线排列评分
    ma_score = check_ma_pattern(df)
    score += ma_score
    details['均线评分'] = ma_score
    
    # 5. 平台突破评分
    breakout, breakout_score = check_platform_breakout(df)
    score += breakout_score
    details['平台突破'] = breakout
    details['平台突破评分'] = breakout_score
    
    # 6. 趋势强度评分
    trend_score, trend_info = calculate_trend_strength(df)
    score += trend_score
    details['趋势强度评分'] = trend_score
    if trend_info:
        details.update(trend_info)
    
    # 当日涨幅
    if len(df) >= 2:
        details['当日涨幅'] = round(df['pct_chg'].iloc[-1], 2)
    
    details['总评分'] = score
    details['股票代码'] = stock_code
    details['股票名称'] = stock_name
    
    return details

# =========================
# 对前N名板块进行中军分析
# =========================
def analyze_backbone_for_sectors(sectors, concept_map, daily_basic, name_map):
    print("\n[3/6] 分析中军候选...")
    
    # 创建市值查询字典
    market_cap_dict = {}
    if not daily_basic.empty:
        for _, row in daily_basic.iterrows():
            market_cap_dict[row['ts_code']] = row
    
    all_backbone_candidates = []
    
    # 对前5名板块进行分析
    for idx, sector in enumerate(sectors[:5]):
        sector_name = sector['板块名称']
        stocks = concept_map.get(sector_name, [])
        
        print(f"\n[{idx+1}/5] 正在分析板块: {sector_name}")
        
        sector_candidates = []
        analyzed_count = 0
        
        for stock_code in stocks:
            if analyzed_count >= 100:  # 每板块最多分析100只
                break
            
            # 获取日线数据
            df = get_hist_data(stock_code, 120)
            if df is None or len(df) < 60:
                continue
            
            # 获取市值数据
            daily_row = market_cap_dict.get(stock_code)
            if daily_row is None:
                continue
            
            # 分析中军特征
            result = analyze_backbone_stock(stock_code, df, daily_row, name_map)
            if result and result['总评分'] >= 70:  # 筛选总分>=70的
                result['所属板块'] = sector_name
                result['板块排名'] = idx + 1
                sector_candidates.append(result)
            
            analyzed_count += 1
        
        # 板块内按评分排序
        sector_candidates = sorted(sector_candidates, key=lambda x: x['总评分'], reverse=True)
        print(f"   找到 {len(sector_candidates)} 只中军候选")
        
        all_backbone_candidates.extend(sector_candidates[:10])  # 每板块取前10名
    
    # 去重：一只股票只保留一次（在最早出现的板块里）
    seen_stocks = set()
    unique_candidates = []
    for candidate in all_backbone_candidates:
        stock_code = candidate['股票代码']
        if stock_code not in seen_stocks:
            seen_stocks.add(stock_code)
            unique_candidates.append(candidate)
    
    # 全部按评分排序
    all_backbone_candidates = sorted(unique_candidates, key=lambda x: x['总评分'], reverse=True)
    
    return all_backbone_candidates

# =========================
# 主程序
# =========================
def main():
    print("\n" + "=" * 80)
    print(f"  {TRADE_DATE} 主线板块 + 中军综合分析（Tushare东财版）")
    print("=" * 80)
    
    # 1. 获取市值和成交数据
    print("\n[4/6] 获取市值和成交数据...")
    try:
        daily_basic = pro.daily_basic(
            trade_date=TRADE_DATE, 
            fields='ts_code,total_mv,circ_mv,turnover_rate'
        )
        print(f"   获取 {len(daily_basic)} 只股票的市值数据")
    except Exception as e:
        print(f"   获取失败: {e}")
        daily_basic = pd.DataFrame()
    
    # 2. 获取概念板块映射和股票名称映射
    concept_map, name_map = get_concept_map()
    
    # 3. 如果需要额外获取股票名称映射（备用）
    if not name_map:
        name_map = get_stock_name_map()
    
    # 4. 分析板块
    sectors = analyze_sectors(daily_basic, concept_map)
    
    print("\n[5/6] 主线板块（前15名）:")
    print("-" * 110)
    print(f"{'排名':^4} | {'板块名称':^20} | {'类型':^4} | {'成分':^5} | {'分析':^5} | {'均涨':^8} | {'涨停':^5} | {'动量':^8}")
    print("-" * 110)
    for i, s in enumerate(sectors[:15], 1):
        print(f"{i:^4} | {s['板块名称']:^20s} | {s['板块类型']:^4} | {s['成分股数']:^5} | {s['分析股数']:^5} | {s['平均涨幅']:^8.2f}% | {s['涨停数']:^5} | {s['动量']:^8.2f}")
    
    # 5. 中军分析
    backbone_candidates = analyze_backbone_for_sectors(sectors, concept_map, daily_basic, name_map)
    
    print("\n" + "=" * 80)
    print("【中军候选排名（总分>=70）】")
    print("=" * 80)
    
    if backbone_candidates:
        print("\n" + "-" * 120)
        print(f"{'排名':^4} | {'股票代码':^10} | {'股票名称':^10} | {'所属板块':^15} | {'总分':^5} | {'市值_亿':^10} | {'成交额_万':^12} | {'换手率':^8} | {'当日涨幅':^10}")
        print("-" * 120)
        for i, candidate in enumerate(backbone_candidates[:20], 1):
            # 处理可能为None的值
            zhangfu = candidate.get('当日涨幅', 0)
            print(f"{i:^4} | {candidate['股票代码']:^10} | {candidate['股票名称']:^10} | {candidate['所属板块']:^15} | {candidate['总评分']:^5.0f} | {candidate['市值_亿']:^10.2f} | {candidate['成交额_万']:^12.2f} | {candidate['换手率']:^8.2f} | {zhangfu:^10.2f}%")
        print("-" * 120)
        
        # 保存详细结果
        result_df = pd.DataFrame(backbone_candidates)
        result_file = os.path.join(CACHE_DIR, f"backbone_candidates_{TRADE_DATE}.csv")
        result_df.to_csv(result_file, index=False, encoding='utf-8-sig')
        print(f"\n[6/6] 完整结果已保存至: {result_file}")
        
        # 保存板块结果
        sector_df = pd.DataFrame(sectors[:15])
        sector_file = os.path.join(CACHE_DIR, f"sectors_{TRADE_DATE}.csv")
        sector_df.to_csv(sector_file, index=False, encoding='utf-8-sig')
        
        # 打印中军详细评分
        print("\n" + "=" * 80)
        print("【中军候选详细评分（前10名）】")
        print("=" * 80)
        for i, candidate in enumerate(backbone_candidates[:10], 1):
            print(f"\n{i}. {candidate['股票名称']}({candidate['股票代码']}) - 所属板块: {candidate['所属板块']}")
            print(f"   总评分: {candidate['总评分']}")
            print(f"   市值评分: {candidate['市值评分']} (市值: {candidate['市值_亿']}亿)")
            print(f"   成交额评分: {candidate['成交额评分']} ({candidate['成交额档位']}, {candidate['成交额_万']}万)")
            print(f"   换手率评分: {candidate['换手率评分']} (换手率: {candidate['换手率']}%)")
            print(f"   均线评分: {candidate['均线评分']}")
            print(f"   平台突破评分: {candidate['平台突破评分']} (突破: {candidate['平台突破']})")
            print(f"   趋势强度评分: {candidate['趋势强度评分']}")
            if '累计涨幅' in candidate:
                print(f"   趋势特征: 累计涨幅 {candidate.get('累计涨幅', 0)}%, 启动 {candidate.get('启动天数', 0)}天, 日均 {candidate.get('日均涨幅', 0)}%, 波动率 {candidate.get('波动率', 0)}")
    else:
        print("\n无符合条件的中军候选股票")
        print(f"\n[6/6] 结果已保存")

if __name__ == "__main__":
    main()