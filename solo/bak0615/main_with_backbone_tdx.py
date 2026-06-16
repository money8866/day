#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主线板块 + 中军分析系统（通达信版）
- 板块数据：Tushare 通达信接口 (ths_index/ths_member)
- 行情数据：可选择 Tushare 或通达信本地日线
"""
import os
import sys
import time
import pickle
import tushare as ts
import pandas as pd
import numpy as np
from dotenv import load_dotenv
from datetime import datetime, timedelta
from collections import defaultdict
import argparse

# 添加父目录到路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# =========================
# 参数配置
# =========================
MIN_MARKET_CAP = 100  # 最小市值（亿）
MAX_MARKET_CAP = 1000  # 最大市值（亿）
MIN_AMOUNT = 5.0  # 最小成交金额（亿）
MIN_VOLUME_RATIO = 2.0  # 最小量比
MIN_TURNOVER = 5.0  # 最小换手率
MAX_TURNOVER = 10.0  # 最大换手率
MIN_TREND_SLOPE = 0.5  # 最小趋势斜率（度）
MA_PERIOD_SHORT = 5  # 短期均线
MA_PERIOD_MID = 20  # 中期均线
MA_PERIOD_LONG = 60  # 长期均线
LOOKBACK_DAYS = 120  # 历史数据回看天数
BACKBONE_SCORE_THRESHOLD = 50  # 中军评分阈值
TOP_SECTOR_COUNT = 10  # 分析前N个主线板块
HISTORY_CHECK_DAYS = 30  # 历史中军候选检测天数
MIN_STOCKS = 10  # 板块最小股票数

# 通达信本地数据路径
TDX_PATH = r"C:\new_tdx\vipdoc"

# =========================
# Tushare 配置
# =========================
load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.dirname(__file__)), "TUSHARE.env"))
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

# =========================
# 缓存目录
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "../cache_backbone_tdx")
os.makedirs(CACHE_DIR, exist_ok=True)

# =========================================================
# 文件路径
# =========================================================
CONCEPT_LIST_PATH = os.path.join(
    CACHE_DIR,
    "ths_concept_list.csv"
)

CONCEPT_DETAIL_PATH = os.path.join(
    CACHE_DIR,
    "ths_concept_detail.pkl"
)

STOCK_CONCEPT_PATH = os.path.join(
    CACHE_DIR,
    "stock_concept_map.pkl"
)

CONCEPT_STOCK_PATH = os.path.join(
    CACHE_DIR,
    "concept_stock_map.pkl"
)

HISTORY_PATH = os.path.join(CACHE_DIR, "backbone_history.csv")

# =========================================================
# 通达信本地日线读取（来自 _v3_tdx.py）
# =========================================================
def parse_tdx_day_file(filepath):
    """解析通达信 .day 文件"""
    if not os.path.exists(filepath):
        return None
    data = []
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(32)
            if not chunk or len(chunk) < 32:
                break
            date_int = int.from_bytes(chunk[0:4], "little")
            open_p = int.from_bytes(chunk[4:8], "little") / 100
            high_p = int.from_bytes(chunk[8:12], "little") / 100
            low_p = int.from_bytes(chunk[12:16], "little") / 100
            close_p = int.from_bytes(chunk[16:20], "little") / 100
            volume = int.from_bytes(chunk[20:24], "little")
            amount = int.from_bytes(chunk[24:28], "little") / 100.0
            dt = datetime.strptime(str(date_int), "%Y%m%d")
            data.append({
                "date": dt, 
                "date_int": date_int, 
                "open": open_p, 
                "high": high_p, 
                "low": low_p,
                "close": close_p, 
                "vol": volume, 
                "amount": amount
            })
    return data


def get_tdx_kline(ts_code, end_date_str, n_days=120):
    """读取TDX日线数据，返回最近n_days条（含end_date）"""
    if not os.path.exists(TDX_PATH):
        return None
    
    code = ts_code.split('.')[0]
    market = ts_code.split('.')[1].lower()
    subdir = "lday"
    filename = f"{market}{code}.day"
    filepath = os.path.join(TDX_PATH, market, subdir, filename)
    
    raw = parse_tdx_day_file(filepath)
    if not raw:
        return None

    end_dt = datetime.strptime(end_date_str, '%Y%m%d')
    filtered = [r for r in raw if r['date'] <= end_dt]
    if len(filtered) < MA_PERIOD_LONG:
        return None
    
    recent = filtered[-n_days:]
    df = pd.DataFrame(recent)
    df['pct_chg'] = df['close'].pct_change() * 100
    return df

# =========================================================
# 日期函数
# =========================================================
def get_last_trade_date(custom_date=None):
    """获取最近交易日或使用自定义日期"""
    if custom_date:
        return custom_date
    
    now = datetime.now()
    if now.hour < 15:
        query_date = (now - timedelta(days=1)).strftime('%Y%m%d')
    else:
        query_date = now.strftime('%Y%m%d')

    cal = pro.trade_cal(exchange='', start_date='20200101', end_date=query_date)
    cal = cal[cal['is_open'] == 1]
    last_trade_date = cal[cal['cal_date'] <= query_date]['cal_date'].max()
    return str(last_trade_date)


def get_previous_trade_dates(end_date, count=120):
    """获取指定日期前N个交易日"""
    cal = pro.trade_cal(exchange='', start_date='20200101', end_date=end_date)
    cal = cal[cal['is_open'] == 1]
    return cal['cal_date'].tail(count).tolist()

# =========================================================
# 基础数据获取
# =========================================================
def get_stock_basic():
    """获取股票基础信息"""
    cache_file = os.path.join(CACHE_DIR, "stock_basic.pkl")
    
    if os.path.exists(cache_file):
        with open(cache_file, 'rb') as f:
            return pickle.load(f)
    
    df = pro.stock_basic(
        exchange='',
        list_status='L',
        fields='ts_code,name,industry,market,list_date'
    )
    
    with open(cache_file, 'wb') as f:
        pickle.dump(df, f)
    
    return df


def get_market_cap(ts_code, trade_date):
    """获取股票市值数据"""
    try:
        df = pro.daily_basic(ts_code=ts_code, trade_date=trade_date)
        if df is not None and not df.empty:
            return df.iloc[0]
        return None
    except Exception as e:
        print(f"获取 {ts_code} 市值失败: {e}")
        return None


def get_daily_from_tushare(ts_code, trade_date, n_days=120):
    """从 Tushare 获取日线数据"""
    dates = get_previous_trade_dates(trade_date, n_days)
    start_date = dates[0] if dates else trade_date
    
    try:
        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=trade_date)
        if df is not None and not df.empty:
            df = df.sort_values('trade_date', ascending=True).reset_index(drop=True)
        return df
    except Exception as e:
        print(f"获取 {ts_code} 日线失败: {e}")
        return pd.DataFrame()

# =========================================================
# 板块数据获取（通达信+申万）
# =========================================================
def download_ths_concepts():
    """下载同花顺概念列表（带缓存）"""
    if os.path.exists(CONCEPT_LIST_PATH):
        print(f"读取缓存: {CONCEPT_LIST_PATH}")
        return pd.read_csv(CONCEPT_LIST_PATH, encoding="utf-8-sig")
    
    print("下载同花顺概念列表...")
    df = pro.ths_index(exchange='A', type='N')
    
    if df is None or df.empty:
        return pd.DataFrame()
    
    df.to_csv(CONCEPT_LIST_PATH, index=False, encoding='utf-8-sig')
    print(f"概念列表已保存: {CONCEPT_LIST_PATH}")
    return df


def download_ths_members(concept_df):
    """下载概念成分股（带缓存）"""
    if os.path.exists(CONCEPT_DETAIL_PATH):
        print(f"读取缓存: {CONCEPT_DETAIL_PATH}")
        with open(CONCEPT_DETAIL_PATH, "rb") as f:
            return pickle.load(f)
    
    all_rows = []
    total = len(concept_df)
    
    for i, row in concept_df.iterrows():
        ts_code = row["ts_code"]
        name = row["name"]
        
        print(f"[{i+1}/{total}] 下载: {name}")
        
        try:
            df = pro.ths_member(ts_code=ts_code)
            if df is not None and not df.empty:
                df["concept_name"] = name
                all_rows.append(df)
            time.sleep(0.25)
        except Exception as e:
            print(f"失败: {name} {e}")
    
    if not all_rows:
        return pd.DataFrame()
    
    result = pd.concat(all_rows, ignore_index=True)
    with open(CONCEPT_DETAIL_PATH, "wb") as f:
        pickle.dump(result, f)
    print(f"概念成分股已保存: {CONCEPT_DETAIL_PATH}")
    return result


def build_stock_concept_map(member_df):
    """构建 股票 -> 概念 映射（带缓存）"""
    if os.path.exists(STOCK_CONCEPT_PATH):
        print(f"读取缓存: {STOCK_CONCEPT_PATH}")
        with open(STOCK_CONCEPT_PATH, "rb") as f:
            return pickle.load(f)
    
    stock_map = defaultdict(list)
    for _, row in member_df.iterrows():
        ts_code = row["ts_code"]
        concept = row["concept_name"]
        stock_map[ts_code].append(concept)
    
    stock_map = {
        k: ";".join(sorted(set(v)))
        for k, v in stock_map.items()
    }
    
    with open(STOCK_CONCEPT_PATH, "wb") as f:
        pickle.dump(stock_map, f)
    print(f"股票概念映射已保存: {STOCK_CONCEPT_PATH}")
    return stock_map


def build_concept_stock_map(member_df):
    """构建 概念 -> 股票 映射（带缓存）"""
    if os.path.exists(CONCEPT_STOCK_PATH):
        print(f"读取缓存: {CONCEPT_STOCK_PATH}")
        with open(CONCEPT_STOCK_PATH, "rb") as f:
            return pickle.load(f)
    
    concept_map = defaultdict(list)
    for _, row in member_df.iterrows():
        ts_code = row["ts_code"]
        concept = row["concept_name"]
        concept_map[concept].append(ts_code)
    
    concept_map = {
        k: sorted(set(v))
        for k, v in concept_map.items()
    }
    
    with open(CONCEPT_STOCK_PATH, "wb") as f:
        pickle.dump(concept_map, f)
    print(f"概念股票映射已保存: {CONCEPT_STOCK_PATH}")
    return concept_map


def get_sw_industry_map():
    """获取申万行业映射"""
    cache_file = os.path.join(CACHE_DIR, "sw_map.csv")
    
    if os.path.exists(cache_file):
        df = pd.read_csv(cache_file, dtype=str)
        if not df.empty:
            return df
    
    df = pro.index_member_all(is_new='Y')
    df.to_csv(cache_file, index=False)
    return df


def load_industry_stock_map(sw_df):
    """构建行业股票映射"""
    industry_map = defaultdict(list)
    
    for _, row in sw_df.iterrows():
        ts_code = row['ts_code']
        if 'l1_name' in row and pd.notna(row['l1_name']):
            industry_map[row['l1_name']].append(ts_code)
        if 'l2_name' in row and pd.notna(row['l2_name']):
            industry_map[row['l2_name']].append(ts_code)
        if 'l3_name' in row and pd.notna(row['l3_name']):
            industry_map[row['l3_name']].append(ts_code)
    
    for industry in industry_map:
        industry_map[industry] = list(set(industry_map[industry]))
    
    return industry_map


def init_block_cache():
    """初始化板块缓存"""
    concept_df = download_ths_concepts()
    member_df = download_ths_members(concept_df)
    stock_concept_map = build_stock_concept_map(member_df)
    concept_stock_map = build_concept_stock_map(member_df)
    sw_df = get_sw_industry_map()
    industry_stock_map = load_industry_stock_map(sw_df)
    
    print("板块缓存初始化完成")
    return concept_stock_map, industry_stock_map

# =========================================================
# 技术分析函数
# =========================================================
def calculate_ma(df, period):
    """计算移动平均"""
    if len(df) < period:
        return None
    return df['close'].rolling(window=period).mean().iloc[-1]


def calculate_volume_ratio(df, period=5):
    """计算量比"""
    if len(df) < period + 1:
        return None
    current_vol = df['vol'].iloc[-1]
    avg_vol = df['vol'].iloc[-(period+1):-1].mean()
    if avg_vol == 0:
        return None
    return current_vol / avg_vol


def check_platform_breakout(df, lookback_days=60):
    """检查平台突破形态"""
    if len(df) < lookback_days:
        return False, 0
    
    recent = df.tail(lookback_days)
    max_price = recent['high'].max()
    min_price = recent['low'].min()
    price_range = (max_price - min_price) / min_price
    
    if price_range > 0.3:
        return False, price_range
    
    current_close = df['close'].iloc[-1]
    current_high = df['high'].iloc[-1]
    
    if current_high >= max_price * 0.98:
        return True, price_range
    
    return False, price_range


def check_ma_arrangement(df):
    """检查均线排列"""
    ma5 = calculate_ma(df, MA_PERIOD_SHORT)
    ma20 = calculate_ma(df, MA_PERIOD_MID)
    ma60 = calculate_ma(df, MA_PERIOD_LONG)
    
    if ma5 is None or ma20 is None or ma60 is None:
        return False, None
    
    if len(df) >= MA_PERIOD_LONG + 1:
        prev_ma20 = df['close'].rolling(window=MA_PERIOD_MID).mean().iloc[-2]
        prev_ma60 = df['close'].rolling(window=MA_PERIOD_LONG).mean().iloc[-2]
        golden_cross = (prev_ma20 <= prev_ma60) and (ma20 > ma60)
    else:
        golden_cross = False
    
    ma_up = (ma5 > ma20) and (ma20 > ma60)
    return ma_up or golden_cross, {'ma5': ma5, 'ma20': ma20, 'ma60': ma60, 'golden_cross': golden_cross}

# =========================================================
# 历史记录管理
# =========================================================
def load_history_backbone_records(trade_date):
    """加载历史中军候选记录"""
    if not os.path.exists(HISTORY_PATH):
        return {}
    
    try:
        history_df = pd.read_csv(HISTORY_PATH, dtype={'ts_code': str})
        check_date = datetime.strptime(trade_date, '%Y%m%d')
        start_date = (check_date - timedelta(days=HISTORY_CHECK_DAYS)).strftime('%Y-%m-%d')
        
        history_df = history_df[history_df['date'] >= start_date]
        history_df = history_df[history_df['date'] < check_date.strftime('%Y-%m-%d')]
        
        backbone_count = history_df.groupby('ts_code').size().to_dict()
        return backbone_count
    except Exception as e:
        print(f"加载历史中军记录失败: {e}")
        return {}


def save_backbone_record(ts_code, trade_date, score, sector_name):
    """保存中军候选记录"""
    new_record = pd.DataFrame([{
        'date': datetime.strptime(trade_date, '%Y%m%d').strftime('%Y-%m-%d'),
        'ts_code': ts_code,
        'score': score,
        'sector': sector_name
    }])
    
    if os.path.exists(HISTORY_PATH):
        try:
            existing_df = pd.read_csv(HISTORY_PATH)
            combined_df = pd.concat([existing_df, new_record], ignore_index=True)
        except:
            combined_df = new_record
    else:
        combined_df = new_record
    
    combined_df.to_csv(HISTORY_PATH, index=False, encoding='utf-8-sig')


def check_second_entry(backbone_count, ts_code):
    """检查是否30天内二次进入中军候选"""
    count = backbone_count.get(ts_code, 0)
    return count >= 1

# =========================================================
# 中军评分
# =========================================================
def calculate_trend_strength(df, lookback=20):
    """
    游资风格的趋势强度计算 - 看这一波启动以来的整体趋势
    
    算法逻辑：
    1. 先找到启动点（最近20天内的最低点或平台突破后的起点）
    2. 从启动点到当前计算整体趋势斜率
    3. 结合趋势稳定性、量能配合、涨幅等综合评分
    """
    if len(df) < lookback + 5:
        return 0.0, {}
    
    # 取最近lookback天的数据
    recent_df = df.tail(lookback).copy()
    
    # 找到启动点（成交量放大或价格突破前的最低点）
    # 方法1: 找成交量开始放大的那一天
    volume_avg = recent_df['vol'].rolling(5).mean()
    volume_surge = (recent_df['vol'] > volume_avg * 1.3) & (recent_df['vol'] > recent_df['vol'].shift(1) * 1.2)
    
    # 方法2: 找价格最近突破的起点
    # 计算20日低点
    low_points = []
    for i in range(3, len(recent_df)-2):
        if (recent_df['low'].iloc[i] < recent_df['low'].iloc[i-3:i].min() and
            recent_df['low'].iloc[i] < recent_df['low'].iloc[i+1:i+3].min()):
            low_points.append(i)
    
    # 确定启动点
    start_idx = 0
    if len(low_points) > 0:
        # 取最近的低点作为启动点
        start_idx = low_points[-1]
    elif volume_surge.any():
        # 取放量的那一天
        start_idx = recent_df.index.get_loc(volume_surge.idxmax()) if hasattr(volume_surge.idxmax(), '__len__') else volume_surge.idxmax()
    
    # 从启动点到当前的数据
    trend_df = recent_df.iloc[start_idx:]
    if len(trend_df) < 3:
        return 0.0, {}
    
    # 计算趋势斜率（每日平均涨幅）
    start_price = trend_df['close'].iloc[0]
    end_price = trend_df['close'].iloc[-1]
    trend_days = len(trend_df)
    
    if start_price > 0 and trend_days > 0:
        total_return = (end_price - start_price) / start_price
        daily_slope = total_return / trend_days * 100
    else:
        daily_slope = 0.0
    
    # 计算趋势稳定性（价格沿趋势线的分布）
    x = np.arange(len(trend_df))
    closes = trend_df['close'].values
    z = np.polyfit(x, closes, 1)
    p = np.poly1d(z)
    predicted_closes = p(x)
    # 计算实际价格与趋势线的标准差
    trend_std = np.std(closes - predicted_closes) / start_price * 100 if start_price > 0 else 999
    
    # 计算量能配合（趋势上涨过程中成交量是否放大）
    volume_ratio = trend_df['vol'].tail(5).mean() / trend_df['vol'].head(5).mean() if len(trend_df) >= 10 else 1.0
    
    # 综合趋势强度评分
    # 因素：斜率、稳定性、量能、累计涨幅
    trend_score = 0
    details = {
        '启动天数': trend_days,
        '启动价': round(start_price, 2),
        '现价': round(end_price, 2),
        '累计涨幅': round(total_return * 100, 2) if start_price > 0 else 0,
        '日均涨幅': round(daily_slope, 3),
        '趋势稳定性': round(trend_std, 2),  # 越小越稳定
        '量能放大': round(volume_ratio, 2),
    }
    
    # 判断趋势是否有效
    if daily_slope >= 0.3:  # 日均0.3%以上
        trend_score = daily_slope
        # 稳定性加分
        if trend_std < 2.0:  # 波动率小
            trend_score += 0.5
        # 量能配合加分
        if volume_ratio > 1.2:
            trend_score += 0.3
        # 累计涨幅合理（不是短期暴涨）
        if 5 <= total_return * 100 <= 40:
            trend_score += 0.2
    
    return trend_score, details


def calculate_backbone_score(stock_info, daily_df, market_cap_info, is_second_entry=False):
    """计算中军综合评分"""
    score = 0
    details = {}
    
    # 1. 市值评分（统一20分）
    total_mv = market_cap_info['total_mv'] / 10000 if market_cap_info is not None else 0
    details['市值(亿)'] = round(total_mv, 2)
    
    if MIN_MARKET_CAP <= total_mv <= MAX_MARKET_CAP:
        score += 20
        details['市值评分'] = 20
    else:
        details['市值评分'] = 0
    
    # 2. 成交金额评分（分档）
    # 50亿以上: 25分
    # 20亿以上: 20分
    # 5亿以上: 15分
    amount = market_cap_info['amount'] / 100000 if market_cap_info is not None else 0  # 千元转亿
    details['成交金额(亿)'] = round(amount, 2)
    
    if amount >= 50:
        score += 25
        details['成交金额评分'] = 25
    elif amount >= 20:
        score += 20
        details['成交金额评分'] = 20
    elif amount >= MIN_AMOUNT:
        score += 15
        details['成交金额评分'] = 15
    else:
        details['成交金额评分'] = 0
    
    # 3. 量比评分
    volume_ratio = calculate_volume_ratio(daily_df)
    details['量比'] = round(volume_ratio, 2) if volume_ratio else None
    
    if volume_ratio and volume_ratio >= MIN_VOLUME_RATIO:
        score += 20
        details['量比评分'] = 20
    else:
        details['量比评分'] = 0
    
    # 4. 换手率评分
    turnover_rate = market_cap_info['turnover_rate'] if market_cap_info is not None else 0
    details['换手率'] = round(turnover_rate, 2)
    
    if MIN_TURNOVER <= turnover_rate <= MAX_TURNOVER:
        score += 15
        details['换手率评分'] = 15
    else:
        details['换手率评分'] = 0
    
    # 5. 均线排列评分
    ma_ok, ma_info = check_ma_arrangement(daily_df)
    details['均线信息'] = ma_info
    
    if ma_ok:
        score += 20
        details['均线评分'] = 20
    else:
        details['均线评分'] = 0
    
    # 6. 平台突破评分
    breakout, price_range = check_platform_breakout(daily_df)
    details['平台突破'] = breakout
    details['震荡幅度'] = round(price_range * 100, 2) if price_range else None
    
    if breakout:
        score += 15
        details['突破评分'] = 15
    else:
        details['突破评分'] = 0
    
    # 7. 趋势强度评分（游资风格 - 看这一波启动以来）
    trend_score, trend_details = calculate_trend_strength(daily_df)
    # 把趋势详情也加入details
    details.update(trend_details)
    details['趋势强度'] = round(trend_score, 2)
    
    if trend_score >= MIN_TREND_SLOPE:
        score += 15
        details['趋势强度评分'] = 15
    else:
        details['趋势强度评分'] = 0
    
    # 8. 二次候选加分
    if is_second_entry:
        score += 15
        details['二次候选'] = True
        details['二次候选评分'] = 15
    else:
        details['二次候选'] = False
        details['二次候选评分'] = 0
    
    details['总分'] = score
    return score, details

# =========================================================
# 板块打分（使用 block.py 的算法）
# =========================================================
def calc_sector_score(df):
    """计算板块综合评分"""
    if df is None or len(df) == 0:
        return 0
    
    pct = df["pct_chg"]
    
    momentum = pct.mean()
    limit_up = (pct >= 9.5).sum()
    up_ratio = (pct > 0).mean()
    median_chg = pct.median()
    money = df["amount"].sum() / 1e8
    
    try:
        top5_ratio = (
            df.sort_values("amount", ascending=False)
              .head(5)["amount"].sum()
            / df["amount"].sum()
        )
    except:
        top5_ratio = 0
    
    limit_down = (pct <= -9.5).sum()
    
    score = (
        momentum * 1.2
        + limit_up * 6
        + up_ratio * 5
        + median_chg * 1.5
        + money * 0.8
        + top5_ratio * 8
        - limit_down * 10
    )
    
    return score


def get_sector_stocks(sector_name, concept_map, industry_map):
    """获取板块成分股"""
    if sector_name in concept_map:
        return concept_map[sector_name]
    if sector_name in industry_map:
        return industry_map[sector_name]
    return []


def analyze_all_sectors(trade_date, concept_map, industry_map, use_tdx=False):
    """分析所有板块"""
    print("获取全市场行情...")
    
    # 获取全市场当日行情
    cache_file = os.path.join(CACHE_DIR, f"daily_{trade_date}.csv")
    if os.path.exists(cache_file):
        daily_df = pd.read_csv(cache_file, dtype={'ts_code': str})
    else:
        daily_df = pro.daily(trade_date=trade_date)
        if daily_df.empty:
            return pd.DataFrame()
        daily_df['amount'] = daily_df['amount'] / 100000
        daily_df.to_csv(cache_file, index=False, encoding='utf-8-sig')
    
    results = []
    
    all_sectors = []
    for name in concept_map:
        all_sectors.append((name, "概念"))
    for name in industry_map:
        all_sectors.append((name, "行业"))
    
    print(f"分析 {len(all_sectors)} 个板块...")
    
    for i, (sector_name, sector_type) in enumerate(all_sectors):
        if i % 50 == 0:
            print(f"进度: {i}/{len(all_sectors)}", end='\r')
        
        stocks = get_sector_stocks(sector_name, concept_map, industry_map)
        if len(stocks) < MIN_STOCKS:
            continue
        
        df = daily_df[daily_df['ts_code'].isin(stocks)]
        if df.empty:
            continue
        
        score = calc_sector_score(df)
        
        leader = df.sort_values("pct_chg", ascending=False).iloc[0]
        
        results.append({
            "板块类型": sector_type,
            "主线": sector_name,
            "评分": round(score, 2),
            "动量": round(df['pct_chg'].mean(), 2),
            "涨停数": (df['pct_chg'] >= 9.5).sum(),
            "成分股数": len(stocks),
            "龙头代码": leader['ts_code'],
            "龙头名称": "未知",
            "龙头涨幅": round(leader['pct_chg'], 2)
        })
    
    print()
    
    result_df = pd.DataFrame(results)
    if not result_df.empty:
        result_df = result_df.sort_values("评分", ascending=False)
    
    return result_df

# =========================================================
# 中军分析
# =========================================================
def find_backbones_in_sector(stocks, sector_name, sector_info, trade_date, history_backbone, use_tdx=False):
    """在板块成分股中寻找中军"""
    backbones = []
    
    print(f"\n{'='*80}")
    print(f"【分析板块】{sector_name}")
    print(f"{'='*80}")
    
    stock_basic = get_stock_basic()
    
    for i, ts_code in enumerate(stocks):
        if i % 20 == 0:
            print(f"进度: {i+1}/{len(stocks)}", end='\r')
        
        try:
            if use_tdx:
                daily_df = get_tdx_kline(ts_code, trade_date, LOOKBACK_DAYS)
            else:
                daily_df = get_daily_from_tushare(ts_code, trade_date, LOOKBACK_DAYS)
            
            if daily_df is None or daily_df.empty or len(daily_df) < MA_PERIOD_LONG:
                continue
            
            market_cap_info = get_market_cap(ts_code, trade_date)
            if market_cap_info is None:
                continue
            
            stock_info_row = stock_basic[stock_basic['ts_code'] == ts_code]
            if stock_info_row.empty:
                continue
            
            stock_name = stock_info_row.iloc[0]['name']
            
            is_second_entry = check_second_entry(history_backbone, ts_code)
            
            score, details = calculate_backbone_score(stock_info_row.iloc[0], daily_df, market_cap_info, is_second_entry)
            
            if score >= BACKBONE_SCORE_THRESHOLD:
                backbones.append({
                    "ts_code": ts_code,
                    "name": stock_name,
                    "所属板块": sector_name,
                    "板块类型": sector_info.get("板块类型", "未知"),
                    "板块评分": sector_info.get("评分", 0),
                    "中军评分": score,
                    **details
                })
        
        except Exception as e:
            continue
        
        time.sleep(0.1)
    
    print()
    
    backbones.sort(key=lambda x: x['中军评分'], reverse=True)
    return backbones

# =========================================================
# 主函数
# =========================================================
def main(custom_date=None, use_tdx=False, refresh_cache=False):
    TRADE_DATE = get_last_trade_date(custom_date)
    
    print("="*80)
    print("主线板块 + 中军分析系统（通达信版）")
    print(f"分析日期: {TRADE_DATE}")
    print(f"行情数据: {'通达信本地' if use_tdx else 'Tushare'}")
    print("="*80)
    
    if refresh_cache or not os.path.exists(CONCEPT_STOCK_PATH):
        concept_map, industry_map = init_block_cache()
    else:
        print("加载板块缓存...")
        with open(CONCEPT_STOCK_PATH, 'rb') as f:
            concept_map = pickle.load(f)
        sw_df = get_sw_industry_map()
        industry_map = load_industry_stock_map(sw_df)
    
    print(f"加载了 {len(concept_map)} 个概念，{len(industry_map)} 个行业")
    
    history_backbone = load_history_backbone_records(TRADE_DATE)
    print(f"30天内历史中军候选股票数: {len(history_backbone)}")
    
    sectors_df = analyze_all_sectors(TRADE_DATE, concept_map, industry_map, use_tdx)
    if sectors_df.empty:
        print("未找到有效板块")
        return
    
    top_sectors = sectors_df.head(TOP_SECTOR_COUNT)
    print("\n主线板块（前5名）:")
    print(top_sectors[['板块类型', '主线', '评分', '涨停数', '成分股数']].head(5).to_string(index=False))
    
    all_backbones = []
    for _, sector in top_sectors.iterrows():
        sector_name = sector['主线']
        stocks = get_sector_stocks(sector_name, concept_map, industry_map)
        
        if not stocks or len(stocks) < MIN_STOCKS:
            continue
        
        backbones = find_backbones_in_sector(
            stocks, sector_name, 
            sector.to_dict(), 
            TRADE_DATE, 
            history_backbone, 
            use_tdx
        )
        
        if backbones:
            print(f"找到 {len(backbones)} 只潜在中军")
            for bb in backbones[:3]:
                tag = "✓二次候选" if bb.get('二次候选', False) else ""
                print(f"  - {bb['name']}({bb['ts_code']}) | 评分: {bb['中军评分']} {tag}")
            
            for bb in backbones:
                save_backbone_record(bb['ts_code'], TRADE_DATE, bb['中军评分'], sector_name)
        
        all_backbones.extend(backbones)
    
    if all_backbones:
        result_df = pd.DataFrame(all_backbones)
        result_df = result_df.sort_values('中军评分', ascending=False).reset_index(drop=True)
        
        display_cols = [col for col in [
            'ts_code', 'name', '所属板块', '板块类型', '中军评分', '二次候选',
            '市值(亿)', '成交金额(亿)', '量比', '换手率', '趋势强度', '平台突破', '板块评分'
        ] if col in result_df.columns]
        
        print("\n" + "="*80)
        print("【综合分析结果】")
        print("="*80)
        print("\n中军候选名单（按评分排序）:")
        print(result_df[display_cols].to_string(index=False))
        
        output_file = os.path.join(CACHE_DIR, f"backbone_analysis_{TRADE_DATE}.csv")
        result_df.to_csv(output_file, index=False, encoding='utf-8-sig')
        print(f"\n完整结果已保存至: {output_file}")
        
        print("\n" + "="*80)
        print("【中军特征说明】")
        print("="*80)
        print("1. 市值区间: 100-1000亿（统一20分）")
        print("2. 成交金额: 50亿以上25分，20亿以上20分，5亿以上15分")
        print("3. 量比: >=2（20分）")
        print("4. 换手率: 5%-10%（15分）")
        print("5. 均线排列: MA5>MA20>MA60 或金叉（20分）")
        print("6. 平台突破: 震荡后突破（15分）")
        print("7. 趋势强度: >=0.5（游资风格算法，看这一波启动以来）（15分）")
        print("8. 二次候选: 30天内再次进入（+15分）")
        print("="*80)
        
        return result_df
    else:
        print("未找到符合条件的中军股票")
        return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='主线板块 + 中军分析系统（通达信版）')
    parser.add_argument('--date', type=str, help='指定交易日 (格式: YYYYMMDD)')
    parser.add_argument('--tdx', action='store_true', help='使用通达信本地日线数据')
    parser.add_argument('--refresh', action='store_true', help='刷新板块缓存')
    args = parser.parse_args()
    
    main(args.date, args.tdx, args.refresh)
