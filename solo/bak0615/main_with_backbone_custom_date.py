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

# 添加父目录到路径，以便导入 block.py
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# =========================
# 参数配置
# =========================
MIN_MARKET_CAP = 100  # 最小市值（亿）
MAX_MARKET_CAP = 1000  # 最大市值（亿）
MIN_VOLUME_RATIO = 2.0  # 最小量比
MIN_TURNOVER = 5.0  # 最小换手率
MAX_TURNOVER = 10.0  # 最大换手率
MA_PERIOD_SHORT = 5  # 短期均线
MA_PERIOD_MID = 20  # 中期均线
MA_PERIOD_LONG = 60  # 长期均线
LOOKBACK_DAYS = 120  # 历史数据回看天数
BACKBONE_SCORE_THRESHOLD = 50  # 中军评分阈值
TOP_SECTOR_COUNT = 5  # 分析前N个主线板块
HISTORY_CHECK_DAYS = 30  # 历史中军候选检测天数

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


def get_stock_basic():
    """获取股票基础信息"""
    cache_file = os.path.join(CACHE_DIR, f"stock_basic.pkl")
    
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
    """检查均线排列：MA5 > MA20 > MA60"""
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
    
    return ma_up or golden_cross, {
        'ma5': ma5,
        'ma20': ma20,
        'ma60': ma60,
        'golden_cross': golden_cross
    }


def calculate_backbone_score(stock_info, daily_df, market_cap_info, is_second_entry=False):
    """计算中军综合评分"""
    score = 0
    details = {}
    
    # 1. 市值评分（分档）
    total_mv = market_cap_info['total_mv'] / 10000 if market_cap_info is not None else 0
    details['市值(亿)'] = round(total_mv, 2)
    
    if MIN_MARKET_CAP <= total_mv <= MAX_MARKET_CAP:
        if total_mv <= 500:  # 100-500亿满分
            score += 30
            details['市值评分'] = 30
        else:  # 500-1000亿部分分数
            score += 20
            details['市值评分'] = 20
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
    
    # 6. 二次候选加分
    if is_second_entry:
        score += 15
        details['二次候选'] = True
        details['二次候选评分'] = 15
    else:
        details['二次候选'] = False
        details['二次候选评分'] = 0
    
    details['总分'] = score
    return score, details


def find_backbones_in_sector(stock_list, sector_name, sector_info, trade_date, history_backbone):
    """在板块成分股中寻找中军"""
    backbones = []
    
    print(f"\n{'='*80}")
    print(f"【分析板块】{sector_name} ({sector_info.get('类型', '未知')}级)")
    print(f"板块评分: {sector_info.get('评分', 'N/A')} | 主线强度: {sector_info.get('主线强度', 'N/A')}")
    print(f"成分股数量: {len(stock_list)}")
    print(f"{'='*80}")
    
    end_date = trade_date
    start_dt = datetime.strptime(end_date, '%Y%m%d') - timedelta(days=LOOKBACK_DAYS)
    start_date = start_dt.strftime('%Y%m%d')
    
    stock_basic = get_stock_basic()
    
    for i, ts_code in enumerate(stock_list):
        if i % 10 == 0:
            print(f"进度: {i+1}/{len(stock_list)}", end='\r')
        
        try:
            daily_df = get_daily_data(ts_code, start_date, end_date)
            if daily_df.empty or len(daily_df) < MA_PERIOD_LONG:
                continue
            
            market_cap_info = get_market_cap(ts_code, trade_date)
            if market_cap_info is None:
                continue
            
            stock_info_row = stock_basic[stock_basic['ts_code'] == ts_code]
            if stock_info_row.empty:
                continue
            
            stock_name = stock_info_row.iloc[0]['name']
            
            # 检查是否30天内二次进入中军候选
            is_second_entry = check_second_entry(history_backbone, ts_code)
            
            score, details = calculate_backbone_score(stock_info_row.iloc[0], daily_df, market_cap_info, is_second_entry)
            
            if score >= BACKBONE_SCORE_THRESHOLD:
                backbones.append({
                    'ts_code': ts_code,
                    'name': stock_name,
                    '所属板块': sector_name,
                    '板块类型': sector_info.get('类型', '未知'),
                    '板块评分': sector_info.get('评分', 0),
                    '板块主线强度': sector_info.get('主线强度', 0),
                    '中军评分': score,
                    **details
                })
        
        except Exception as e:
            continue
        
        time.sleep(0.1)
    
    print()
    
    backbones.sort(key=lambda x: x['中军评分'], reverse=True)
    return backbones


def load_concept_stock_map():
    """加载概念股票映射"""
    concept_stock_path = os.path.join(BASE_DIR, "../cache_daily/concept_stock_map.pkl")
    if os.path.exists(concept_stock_path):
        with open(concept_stock_path, 'rb') as f:
            return pickle.load(f)
    return {}


def load_history_backbone_records(trade_date):
    """加载历史中军候选记录"""
    history_file = os.path.join(CACHE_DIR, "backbone_history.csv")
    
    if not os.path.exists(history_file):
        return {}
    
    try:
        history_df = pd.read_csv(history_file, dtype={'ts_code': str})
        
        # 过滤30天内的记录
        check_date = datetime.strptime(trade_date, '%Y%m%d')
        start_date = (check_date - timedelta(days=HISTORY_CHECK_DAYS)).strftime('%Y-%m-%d')
        
        history_df = history_df[history_df['date'] >= start_date]
        history_df = history_df[history_df['date'] < check_date.strftime('%Y-%m-%d')]
        
        # 返回股票出现次数
        backbone_count = history_df.groupby('ts_code').size().to_dict()
        return backbone_count
    
    except Exception as e:
        print(f"加载历史中军记录失败: {e}")
        return {}


def save_backbone_record(ts_code, trade_date, score, sector_name):
    """保存中军候选记录"""
    history_file = os.path.join(CACHE_DIR, "backbone_history.csv")
    
    new_record = pd.DataFrame([{
        'date': datetime.strptime(trade_date, '%Y%m%d').strftime('%Y-%m-%d'),
        'ts_code': ts_code,
        'score': score,
        'sector': sector_name
    }])
    
    if os.path.exists(history_file):
        try:
            existing_df = pd.read_csv(history_file)
            combined_df = pd.concat([existing_df, new_record], ignore_index=True)
        except:
            combined_df = new_record
    else:
        combined_df = new_record
    
    combined_df.to_csv(history_file, index=False, encoding='utf-8-sig')


def check_second_entry(backbone_count, ts_code):
    """检查是否30天内二次进入中军候选"""
    count = backbone_count.get(ts_code, 0)
    return count >= 1


def load_industry_stock_map():
    """加载申万行业股票映射"""
    sw_map_path = os.path.join(BASE_DIR, "../cache_daily/sw_map.csv")
    if os.path.exists(sw_map_path):
        sw_df = pd.read_csv(sw_map_path, dtype=str)
        
        # 构建行业->股票映射
        industry_stock_map = defaultdict(list)
        
        for _, row in sw_df.iterrows():
            ts_code = row['ts_code']
            
            # 添加到一级行业
            if 'l1_name' in row and pd.notna(row['l1_name']):
                industry_stock_map[row['l1_name']].append(ts_code)
            
            # 添加到二级行业
            if 'l2_name' in row and pd.notna(row['l2_name']):
                industry_stock_map[row['l2_name']].append(ts_code)
            
            # 添加到三级行业
            if 'l3_name' in row and pd.notna(row['l3_name']):
                industry_stock_map[row['l3_name']].append(ts_code)
        
        # 去重
        for industry in industry_stock_map:
            industry_stock_map[industry] = list(set(industry_stock_map[industry]))
        
        return dict(industry_stock_map)
    return {}


def get_sector_stocks(sector_name, sector_type, concept_stock_map, industry_stock_map):
    """获取板块成分股（支持行业、概念、主题三种类型）"""
    
    # 1. 如果是行业类型
    if sector_type in ['l1_name', 'l2_name', 'l3_name', '行业']:
        # 先尝试从行业映射中获取
        if sector_name in industry_stock_map:
            return industry_stock_map[sector_name]
        # 如果找不到，尝试从概念映射中获取（某些概念也是行业）
        if sector_name in concept_stock_map:
            return concept_stock_map[sector_name]
        return []
    
    # 2. 如果是概念类型
    if sector_type == '概念':
        if sector_name in concept_stock_map:
            return concept_stock_map[sector_name]
        return []
    
    # 3. 如果是主题类型
    if sector_type == '主题':
        # 主题通常和概念类似，从概念映射中获取
        if sector_name in concept_stock_map:
            return concept_stock_map[sector_name]
        return []
    
    # 4. 默认：先查概念，再查行业
    if sector_name in concept_stock_map:
        return concept_stock_map[sector_name]
    if sector_name in industry_stock_map:
        return industry_stock_map[sector_name]
    
    return []


def main(custom_date=None):
    """主函数：整合主线板块和中军分析"""
    TRADE_DATE = get_last_trade_date(custom_date)
    
    print("="*80)
    print("主线板块 + 中军分析系统")
    print(f"分析日期: {TRADE_DATE}")
    print("="*80)
    
    # 第一步：加载板块数据
    print("\n【第一步】加载板块数据...")
    
    try:
        from block import analyze_hot_sectors
        hot_sectors_df = analyze_hot_sectors()
        
        if hot_sectors_df.empty:
            print("未获取到主线板块数据")
            return
        
        print("\n主线板块（前10名）:")
        print(hot_sectors_df[['类型', '主线', '评分', '主线强度', '龙头名称', '成分股数']].head(10).to_string(index=False))
        
    except Exception as e:
        print(f"获取主线板块失败: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # 第二步：加载概念和行业映射
    print("\n【第二步】加载板块成分股映射...")
    
    concept_stock_map = load_concept_stock_map()
    industry_stock_map = load_industry_stock_map()
    
    print(f"加载了 {len(concept_stock_map)} 个概念")
    print(f"加载了 {len(industry_stock_map)} 个行业")
    
    if not concept_stock_map and not industry_stock_map:
        print("概念和行业映射都不存在，请先运行 block.py 生成缓存")
        return
    
    # 第三步：加载历史中军候选记录
    print("\n【第三步】加载历史中军候选记录...")
    
    history_backbone = load_history_backbone_records(TRADE_DATE)
    print(f"30天内历史中军候选股票数: {len(history_backbone)}")
    
    # 第四步：对前N个主线板块进行中军分析
    top_sectors = hot_sectors_df.head(TOP_SECTOR_COUNT)
    
    all_backbones = []
    
    print(f"\n【第四步】对前{TOP_SECTOR_COUNT}个主线板块进行中军分析...")
    
    for _, sector in top_sectors.iterrows():
        sector_name = sector['主线']
        sector_type = sector['类型']
        
        stocks = get_sector_stocks(sector_name, sector_type, concept_stock_map, industry_stock_map)
        
        if not stocks or len(stocks) < 10:
            print(f"\n跳过 {sector_name}（{sector_type}）：成分股不足")
            continue
        
        sector_info = {
            '类型': sector_type,
            '评分': sector['评分'],
            '主线强度': sector['主线强度'],
            '动量': sector['动量'],
            '加速度': sector['加速度']
        }
        
        backbones = find_backbones_in_sector(stocks, sector_name, sector_info, TRADE_DATE, history_backbone)
        
        if backbones:
            print(f"\n找到 {len(backbones)} 只潜在中军:")
            for bb in backbones[:3]:
                second_entry_str = " ✓二次候选" if bb.get('二次候选', False) else ""
                print(f"  - {bb['name']}({bb['ts_code']}) | 评分: {bb['中军评分']}{second_entry_str} | 市值: {bb['市值(亿)']}亿")
            
            # 保存中军候选记录
            for bb in backbones:
                save_backbone_record(bb['ts_code'], TRADE_DATE, bb['中军评分'], sector_name)
        
        all_backbones.extend(backbones)
    
    # 第五步：输出综合结果
    print("\n" + "="*80)
    print("【综合分析结果】")
    print("="*80)
    
    if all_backbones:
        result_df = pd.DataFrame(all_backbones)
        result_df = result_df.sort_values('中军评分', ascending=False).reset_index(drop=True)
        
        display_cols = [
            'ts_code', 'name', '所属板块', '板块类型', '中军评分', '二次候选',
            '市值(亿)', '量比', '换手率', '当日涨幅',
            '平台突破', '板块评分', '板块主线强度'
        ]
        
        display_cols = [col for col in display_cols if col in result_df.columns]
        
        print("\n中军候选名单（按评分排序）:")
        print(result_df[display_cols].to_string(index=False))
        
        output_file = os.path.join(CACHE_DIR, f"main_backbone_analysis_{TRADE_DATE}.csv")
        result_df.to_csv(output_file, index=False, encoding='utf-8-sig')
        print(f"\n完整结果已保存至: {output_file}")
        
        print("\n" + "="*80)
        print("【中军特征说明】")
        print("="*80)
        print("1. 市值区间: 100-1000亿（评分权重30/20）")
        print("   - 100-500亿: 30分")
        print("   - 500-1000亿: 20分")
        print("2. 量比: >=2（评分权重20）")
        print("3. 换手率: 5%-10%（评分权重15）")
        print("4. 均线排列: MA5>MA20>MA60 或 MA20刚上穿MA60（评分权重20）")
        print("5. 平台突破: 长期震荡后放量突破（评分权重15）")
        print("6. 当日涨幅: 5%-8%为佳（评分权重10）")
        print("7. 二次候选: 30天内再次进入中军候选（评分权重15）")
        print("="*80)
        
        print("\n【板块类型说明】")
        print("- 行业: 申万一级、二级、三级行业分类")
        print("- 概念: 同花顺概念板块（如人工智能、新能源汽车等）")
        print("- 主题: 自定义主题配置（可跨行业、跨概念）")
        
        return result_df
    else:
        print("未找到符合条件的中军股票")
        return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='主线板块 + 中军分析系统')
    parser.add_argument('--date', type=str, help='指定交易日 (格式: YYYYMMDD)')
    args = parser.parse_args()
    
    main(args.date)
