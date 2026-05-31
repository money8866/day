import os
import time
import pickle
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dotenv import load_dotenv
import tushare as ts

# 加载环境变量
load_dotenv()
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

# ============================================
# 配置参数
# ============================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_kpl")
os.makedirs(CACHE_DIR, exist_ok=True)

MIN_STOCKS = 3      # 板块最小股票数
TOP_K = 10          # 输出主线数量

# ============================================
# 获取最近交易日
# ============================================
def get_last_trade_date():
    now = datetime.now()
    
    # 15点前视为上一交易日
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
    last_trade_date = cal[cal['cal_date'] <= query_date]['cal_date'].max()
    
    return str(last_trade_date)

TRADE_DATE = get_last_trade_date()
print(f"分析日期: {TRADE_DATE}")

# ============================================
# 下载开盘啦概念成分股（带缓存）
# ============================================
def download_kpl_concept_cons(trade_date=None, force_refresh=False):
    """
    使用 kpl_concept_cons 接口下载开盘啦概念题材成分股
    
    参数:
        trade_date: 交易日期 (YYYYMMDD格式)，默认使用最近交易日
        force_refresh: 是否强制刷新缓存
    """
    if trade_date is None:
        trade_date = TRADE_DATE
    
    # 缓存文件路径
    cache_file = os.path.join(CACHE_DIR, f"kpl_concept_{trade_date}.pkl")
    
    # 优先读取缓存
    if not force_refresh and os.path.exists(cache_file):
        print(f"读取缓存: {cache_file}")
        with open(cache_file, "rb") as f:
            return pickle.load(f)
    
    print(f"从Tushare下载开盘啦概念数据...")
    
    # 调用接口
    df = pro.kpl_concept_cons(trade_date=trade_date)
    
    if df is None or df.empty:
        print(f"警告: 未获取到数据")
        return pd.DataFrame()
    
    # 保存缓存
    with open(cache_file, "wb") as f:
        pickle.dump(df, f)
    
    print(f"数据已缓存: {cache_file}")
    print(f"共获取 {len(df)} 条记录")
    
    return df

# ============================================
# 获取股票日线数据（带缓存）
# ============================================
def get_daily_data(trade_date=None):
    if trade_date is None:
        trade_date = TRADE_DATE
    
    cache_file = os.path.join(CACHE_DIR, f"daily_{trade_date}.pkl")
    
    if os.path.exists(cache_file):
        print(f"读取日线缓存: {cache_file}")
        with open(cache_file, "rb") as f:
            return pickle.load(f)
    
    print(f"下载日线数据...")
    df = pro.daily(trade_date=trade_date)
    
    if df is None or df.empty:
        return pd.DataFrame()
    
    # 成交额转亿元（Tushare单位为千元）
    df['amount'] = df['amount'] / 100000
    
    with open(cache_file, "wb") as f:
        pickle.dump(df, f)
    
    print(f"日线数据已缓存: {cache_file}")
    
    return df

# ============================================
# 获取股票基本信息
# ============================================
def get_stock_basic():
    cache_file = os.path.join(CACHE_DIR, "stock_basic.pkl")
    
    if os.path.exists(cache_file):
        with open(cache_file, "rb") as f:
            return pickle.load(f)
    
    df = pro.stock_basic(
        exchange='',
        list_status='L',
        fields='ts_code,name'
    )
    
    with open(cache_file, "wb") as f:
        pickle.dump(df, f)
    
    return df

# ============================================
# 计算板块强度评分
# ============================================
def calc_sector_strength(daily_df, concept_stocks):
    """
    计算板块强度评分
    
    参数:
        daily_df: 日线数据DataFrame
        concept_stocks: 该概念包含的股票代码列表
    
    返回:
        综合评分
    """
    if not concept_stocks or len(concept_stocks) == 0:
        return 0, None, None
    
    # 筛选该概念的股票日线数据
    sector_df = daily_df[daily_df['ts_code'].isin(concept_stocks)]
    
    if sector_df.empty:
        return 0, None, None
    
    pct_chg = sector_df['pct_chg']
    amount = sector_df['amount']
    
    # 1. 平均涨幅
    avg_pct = pct_chg.mean()
    
    # 2. 涨停数量
    limit_up_count = (pct_chg >= 9.5).sum()
    
    # 3. 上涨比例
    up_ratio = (pct_chg > 0).mean()
    
    # 4. 中位数涨幅
    median_pct = pct_chg.median()
    
    # 5. 总成交额（亿元）
    total_amount = amount.sum()
    
    # 6. 资金集中度（前5占比）
    try:
        top5_ratio = (sector_df.sort_values('amount', ascending=False).head(5)['amount'].sum() / total_amount)
    except:
        top5_ratio = 0
    
    # 7. 跌停数量（负向指标）
    limit_down_count = (pct_chg <= -9.5).sum()
    
    # 综合评分
    score = (
        avg_pct * 1.2 +
        limit_up_count * 6 +
        up_ratio * 5 +
        median_pct * 1.5 +
        total_amount * 0.8 +
        top5_ratio * 8 -
        limit_down_count * 10
    )
    
    # 找出龙头（涨幅最高）
    leader_row = sector_df.sort_values('pct_chg', ascending=False).iloc[0]
    leader_code = leader_row['ts_code']
    leader_pct = leader_row['pct_chg']
    
    return score, leader_code, leader_pct

# ============================================
# 分析最强主线
# ============================================
def analyze_top_themes(kpl_df, daily_df, stock_basic):
    """
    分析最强的TOP_K个主线
    
    参数:
        kpl_df: 开盘啦概念数据
        daily_df: 日线数据
        stock_basic: 股票基本信息
    
    返回:
        排序后的主线DataFrame
    """
    if kpl_df.empty or daily_df.empty:
        return pd.DataFrame()
    
    # 构建概念 -> 股票映射
    concept_stock_map = {}
    concept_info = {}
    
    for _, row in kpl_df.iterrows():
        concept_id = row['ts_code']
        concept_name = row['name']
        stock_code = row['con_code']
        hot_num = row.get('hot_num', 0)
        desc = row.get('desc', '')
        
        if concept_name not in concept_stock_map:
            concept_stock_map[concept_name] = []
            concept_info[concept_name] = {
                'concept_id': concept_id,
                'hot_num': hot_num,
                'desc': desc
            }
        
        concept_stock_map[concept_name].append(stock_code)
    
    # 分析每个概念
    results = []
    
    for concept_name, stocks in concept_stock_map.items():
        # 过滤股票数量过少的概念
        if len(stocks) < MIN_STOCKS:
            continue
        
        score, leader_code, leader_pct = calc_sector_strength(daily_df, stocks)
        
        # 获取龙头名称
        leader_name = stock_basic[stock_basic['ts_code'] == leader_code]['name'].values
        leader_name = leader_name[0] if len(leader_name) > 0 else leader_code
        
        info = concept_info[concept_name]
        
        results.append({
            '概念名称': concept_name,
            '概念ID': info['concept_id'],
            '成分股数': len(stocks),
            '人气值': info['hot_num'],
            '强度评分': round(score, 2),
            '龙头代码': leader_code,
            '龙头名称': leader_name,
            '龙头涨幅': round(leader_pct, 2),
            '描述': info['desc']
        })
    
    # 转换为DataFrame并排序
    result_df = pd.DataFrame(results)
    
    if not result_df.empty:
        # 按强度评分排序
        result_df = result_df.sort_values('强度评分', ascending=False)
        result_df = result_df.head(TOP_K)
        result_df.reset_index(drop=True, inplace=True)
        result_df.index = result_df.index + 1  # 从1开始编号
    
    return result_df

# ============================================
# 主函数
# ============================================
def main():
    print("\n=== 开盘啦概念主线分析系统 ===\n")
    
    # 1. 下载开盘啦概念数据
    kpl_df = download_kpl_concept_cons()
    
    if kpl_df.empty:
        print("无法获取开盘啦概念数据，程序退出")
        return
    
    # 2. 获取日线数据
    daily_df = get_daily_data()
    
    if daily_df.empty:
        print("无法获取日线数据，程序退出")
        return
    
    # 3. 获取股票基本信息
    stock_basic = get_stock_basic()
    
    # 4. 分析最强主线
    top_themes = analyze_top_themes(kpl_df, daily_df, stock_basic)
    
    # 5. 输出结果
    print(f"\n【最强{TOP_K}个主线】")
    print("=" * 80)
    
    if top_themes.empty:
        print("暂无符合条件的主线")
        return
    
    # 打印排名前10的主线
    print(top_themes[['概念名称', '成分股数', '人气值', '强度评分', '龙头代码', '龙头名称', '龙头涨幅']].to_string())
    
    # 打印详细描述
    print("\n【主线详细描述】")
    print("=" * 80)
    for idx, row in top_themes.iterrows():
        print(f"{idx}. {row['概念名称']}: {row['描述']}")
        print(f"   龙头: {row['龙头名称']}({row['龙头代码']}) +{row['龙头涨幅']}%")
        print()

if __name__ == "__main__":
    main()
