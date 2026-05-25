import os
import time
import pickle
import tushare as ts
import pandas as pd
import numpy as np
from dotenv import load_dotenv
from datetime import datetime, timedelta
from collections import defaultdict

# =========================
# 参数配置
# =========================
MIN_MARKET_CAP = 100  # 最小市值（亿）
MAX_MARKET_CAP = 500  # 最大市值（亿）
MIN_VOLUME_RATIO = 2.0  # 最小量比
MIN_TURNOVER = 5.0  # 最小换手率
MAX_TURNOVER = 10.0  # 最大换手率
MA_PERIOD_SHORT = 5  # 短期均线
MA_PERIOD_MID = 20  # 中期均线
MA_PERIOD_LONG = 60  # 长期均线
LOOKBACK_DAYS = 120  # 历史数据回看天数

# =========================
# Tushare 配置
# =========================
load_dotenv(dotenv_path='../TUSHARE.env')
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

# =========================
# 缓存目录
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "../cache_backbone")
os.makedirs(CACHE_DIR, exist_ok=True)


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


TRADE_DATE = get_last_trade_date()


def get_stock_basic():
    """获取股票基础信息"""
    cache_file = os.path.join(CACHE_DIR, f"stock_basic_{TRADE_DATE}.pkl")
    
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


def get_daily_data(ts_code, start_date, end_date):
    """获取单只股票的历史日线数据"""
    try:
        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df is not None and not df.empty:
            df = df.sort_values('trade_date', ascending=True).reset_index(drop=True)
        return df
    except Exception as e:
        print(f"获取 {ts_code} 数据失败: {e}")
        return pd.DataFrame()


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


def calculate_ma(df, period):
    """计算移动平均线"""
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
    
    # 计算过去lookback_days的价格区间
    recent = df.tail(lookback_days)
    max_price = recent['high'].max()
    min_price = recent['low'].min()
    
    # 计算震荡幅度
    price_range = (max_price - min_price) / min_price
    
    # 检查是否在相对窄幅区间震荡（幅度小于30%）
    if price_range > 0.3:
        return False, price_range
    
    # 检查最近是否突破
    current_close = df['close'].iloc[-1]
    current_high = df['high'].iloc[-1]
    
    if current_high >= max_price * 0.98:
        return True, price_range
    
    return False, price_range


def check_ma_arrangement(df):
    """检查均线排列：MA5 > MA20 > MA60"""
    ma5 = calculate_ma(df, MA_PERIOD_SHORT)
    ma20 = calculate_ma(df, MA_PERIOD_MID)
    ma60 = calculate_ma(df, MA_PERIOD_LONG)
    
    if ma5 is None or ma20 is None or ma60 is None:
        return False, None
    
    # 检查MA20是否刚上穿MA60（金叉）
    if len(df) >= MA_PERIOD_LONG + 1:
        prev_ma20 = df['close'].rolling(window=MA_PERIOD_MID).mean().iloc[-2]
        prev_ma60 = df['close'].rolling(window=MA_PERIOD_LONG).mean().iloc[-2]
        
        golden_cross = (prev_ma20 <= prev_ma60) and (ma20 > ma60)
    else:
        golden_cross = False
    
    # 检查均线是否向上排列
    ma_up = (ma5 > ma20) and (ma20 > ma60)
    
    return ma_up or golden_cross, {
        'ma5': ma5,
        'ma20': ma20,
        'ma60': ma60,
        'golden_cross': golden_cross
    }


def calculate_backbone_score(stock_info, daily_df, market_cap_info):
    """计算中军综合评分"""
    score = 0
    details = {}
    
    # 1. 市值筛选（100-500亿）
    total_mv = market_cap_info['total_mv'] / 10000 if market_cap_info is not None else 0
    details['市值(亿)'] = round(total_mv, 2)
    
    if MIN_MARKET_CAP <= total_mv <= MAX_MARKET_CAP:
        score += 30
        details['市值评分'] = 30
    else:
        details['市值评分'] = 0
    
    # 2. 量比检查
    volume_ratio = calculate_volume_ratio(daily_df)
    details['量比'] = round(volume_ratio, 2) if volume_ratio else None
    
    if volume_ratio and volume_ratio >= MIN_VOLUME_RATIO:
        score += 20
        details['量比评分'] = 20
    else:
        details['量比评分'] = 0
    
    # 3. 换手率检查（5%-10%）
    turnover_rate = market_cap_info['turnover_rate'] if market_cap_info is not None else 0
    details['换手率'] = round(turnover_rate, 2)
    
    if MIN_TURNOVER <= turnover_rate <= MAX_TURNOVER:
        score += 15
        details['换手率评分'] = 15
    else:
        details['换手率评分'] = 0
    
    # 4. 均线排列检查
    ma_ok, ma_info = check_ma_arrangement(daily_df)
    details['均线信息'] = ma_info
    
    if ma_ok:
        score += 20
        details['均线评分'] = 20
    else:
        details['均线评分'] = 0
    
    # 5. 平台突破检查
    breakout, price_range = check_platform_breakout(daily_df)
    details['平台突破'] = breakout
    details['震荡幅度'] = round(price_range * 100, 2) if price_range else None
    
    if breakout:
        score += 15
        details['突破评分'] = 15
    else:
        details['突破评分'] = 0
    
    # 6. 当日涨幅（5%-8%为佳）
    pct_chg = daily_df['pct_chg'].iloc[-1] if not daily_df.empty else 0
    details['当日涨幅'] = round(pct_chg, 2)
    
    if 5 <= pct_chg <= 8:
        score += 10
        details['涨幅评分'] = 10
    elif 3 <= pct_chg < 5 or 8 < pct_chg <= 10:
        score += 5
        details['涨幅评分'] = 5
    else:
        details['涨幅评分'] = 0
    
    details['总分'] = score
    return score, details


def find_backbones_in_sector(stock_list, concept_name=""):
    """在板块成分股中寻找中军"""
    backbones = []
    
    print(f"\n{'='*60}")
    print(f"分析板块: {concept_name}")
    print(f"成分股数量: {len(stock_list)}")
    print(f"{'='*60}")
    
    # 计算开始日期
    end_date = TRADE_DATE
    start_date = (datetime.strptime(end_date, '%Y%m%d') - timedelta(days=LOOKBACK_DAYS)).strftime('%Y%m%d')
    
    for i, ts_code in enumerate(stock_list):
        if i % 10 == 0:
            print(f"进度: {i+1}/{len(stock_list)}")
        
        try:
            # 获取日线数据
            daily_df = get_daily_data(ts_code, start_date, end_date)
            if daily_df.empty or len(daily_df) < MA_PERIOD_LONG:
                continue
            
            # 获取市值数据
            market_cap_info = get_market_cap(ts_code, TRADE_DATE)
            if market_cap_info is None:
                continue
            
            # 获取股票基础信息
            stock_basic = get_stock_basic()
            stock_info = stock_basic[stock_basic['ts_code'] == ts_code]
            if stock_info.empty:
                continue
            
            stock_name = stock_info.iloc[0]['name']
            
            # 计算中军评分
            score, details = calculate_backbone_score(stock_info.iloc[0], daily_df, market_cap_info)
            
            if score >= 50:  # 阈值，可调整
                backbones.append({
                    'ts_code': ts_code,
                    'name': stock_name,
                    '板块': concept_name,
                    '评分': score,
                    **details
                })
        
        except Exception as e:
            print(f"处理 {ts_code} 出错: {e}")
            continue
        
        time.sleep(0.1)  # 避免请求过快
    
    # 按评分排序
    backbones.sort(key=lambda x: x['评分'], reverse=True)
    return backbones


def load_concept_stock_map():
    """加载概念股票映射"""
    concept_stock_path = os.path.join(BASE_DIR, "../cache_daily/concept_stock_map.pkl")
    if os.path.exists(concept_stock_path):
        with open(concept_stock_path, 'rb') as f:
            return pickle.load(f)
    return {}


def analyze_main_backbones():
    """主函数：分析主线板块和中军"""
    print("="*60)
    print("主线板块中军分析系统")
    print(f"分析日期: {TRADE_DATE}")
    print("="*60)
    
    # 加载概念股票映射
    concept_stock_map = load_concept_stock_map()
    if not concept_stock_map:
        print("概念股票映射不存在，请先运行 block.py 生成缓存")
        return
    
    # 这里可以先运行 block.py 获取主线板块
    print("\n提示：请先运行 block.py 获取主线板块")
    print("然后根据主线板块进行中军分析")
    
    # 示例：分析几个热门概念
    sample_concepts = list(concept_stock_map.keys())[:5]  # 前5个概念作为示例
    
    all_backbones = []
    
    for concept in sample_concepts:
        stock_list = concept_stock_map.get(concept, [])
        if len(stock_list) < 10:
            continue
        
        backbones = find_backbones_in_sector(stock_list, concept)
        all_backbones.extend(backbones)
    
    # 输出结果
    print("\n" + "="*80)
    print("中军分析结果（按评分排序）")
    print("="*80)
    
    if all_backbones:
        result_df = pd.DataFrame(all_backbones)
        
        # 选择主要列显示
        display_cols = [
            'ts_code', 'name', '板块', '评分', 
            '市值(亿)', '量比', '换手率', '当日涨幅',
            '平台突破', '均线信息'
        ]
        
        # 确保列存在
        display_cols = [col for col in display_cols if col in result_df.columns]
        
        print(result_df[display_cols].to_string(index=False))
        
        # 保存结果
        output_file = os.path.join(CACHE_DIR, f"backbones_{TRADE_DATE}.csv")
        result_df.to_csv(output_file, index=False, encoding='utf-8-sig')
        print(f"\n结果已保存至: {output_file}")
    else:
        print("未找到符合条件的中军")


if __name__ == "__main__":
    analyze_main_backbones()
