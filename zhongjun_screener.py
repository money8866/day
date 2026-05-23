# -*- coding: utf-8 -*-
"""
中军选股器 - 主线板块启动时筛选中军标的
核心逻辑：
1. 底部突破：长期横盘后放量突破
2. 均线多头初成：MA20上穿MA60附近
3. 量价配合：突破日量比>1.5
4. 市值适中：100-500亿
5. 行业地位：细分龙头（营收规模靠前）
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import tushare as ts
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta

# ===== 配置 =====
env = Path(r'C:\Users\kongx\mystock\.env').read_text()
for line in env.splitlines():
    if line.startswith('TUSHARE_TOKEN='):
        ts.set_token(line.split('=',1)[1].strip())
        break

pro = ts.pro_api()

def calc_ma(series, n):
    return series.rolling(n).mean()

def calc_vol_ratio(volume_series, period=20):
    """量比 = 当日成交量 / 过去N日平均成交量"""
    avg = volume_series.rolling(period).mean()
    return volume_series / avg

def calc_price_position(close, high, low, period=120):
    """价格在N日高低区间的位置"""
    h = high.rolling(period).max()
    l = low.rolling(period).min()
    return (close - l) / (h - l) * 100

def calc_volatility(close, period=20):
    """N日波动率"""
    ret = close.pct_change()
    return ret.rolling(period).std() * np.sqrt(250) * 100

def calc_platform_duration(close, period=60, threshold=0.15):
    """
    横盘持续时间评估：过去N日内价格波动幅度小于threshold的比例
    返回0-1，越大说明横盘越充分
    """
    rolling_high = close.rolling(period).max()
    rolling_low = close.rolling(period).min()
    amplitude = (rolling_high - rolling_low) / rolling_low
    # 波幅小于阈值的天数占比
    flat_ratio = (amplitude < threshold).sum() / len(amplitude.dropna())
    return flat_ratio

def screen_zhongjun(date_str=None, min_mv=100, max_mv=500):
    """
    中军筛选主函数
    """
    if date_str is None:
        date_str = datetime.now().strftime('%Y%m%d')
    
    print(f"=== 中军选股器 === 日期: {date_str}")
    print(f"筛选条件: 市值{min_mv}-{max_mv}亿")
    print()
    
    # Step 1: 获取当日全部A股基本面
    print("[1/5] 获取市值数据...")
    daily_basic = pro.daily_basic(trade_date=date_str, fields='ts_code,close,pe,pb,total_mv,circ_mv,turnover_rate,volume')
    if len(daily_basic) == 0:
        # 尝试最近的交易日
        df_cal = pro.trade_cal(exchange='SSE', is_open=1, end_date=date_str, limit=5)
        last_date = df_cal.sort_values('cal_date').iloc[-2]['cal_date']
        print(f"  当日无数据，使用最近交易日: {last_date}")
        daily_basic = pro.daily_basic(trade_date=last_date, fields='ts_code,close,pe,pb,total_mv,circ_mv,turnover_rate,volume')
        date_str = last_date
    
    # 市值筛选 (total_mv单位是万元)
    daily_basic['mv_yi'] = daily_basic['total_mv'] / 10000  # 转为亿
    candidates = daily_basic[(daily_basic['mv_yi'] >= min_mv) & (daily_basic['mv_yi'] <= max_mv)].copy()
    # 排除ST、北交所
    candidates = candidates[~candidates['ts_code'].str.startswith(('8','4','9'))]
    candidates = candidates[candidates['pe'] > 0]  # PE为正
    print(f"  市值筛选后剩余: {len(candidates)} 只")
    
    # Step 2: 逐只分析技术形态
    print("[2/5] 分析技术形态...")
    results = []
    
    for idx, row in candidates.iterrows():
        ts_code = row['ts_code']
        try:
            # 获取120日行情
            end_date = date_str
            start_date = (datetime.strptime(date_str, '%Y%m%d') - timedelta(days=200)).strftime('%Y%m%d')
            df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
            if len(df) < 60:
                continue
            
            df = df.sort_values('trade_date').reset_index(drop=True)
            close = df['close']
            high = df['high']
            low = df['low']
            vol = df['vol']
            
            # 均线
            ma5 = calc_ma(close, 5)
            ma10 = calc_ma(close, 10)
            ma20 = calc_ma(close, 20)
            ma60 = calc_ma(close, 60)
            
            cur_close = close.iloc[-1]
            cur_ma5 = ma5.iloc[-1]
            cur_ma10 = ma10.iloc[-1]
            cur_ma20 = ma20.iloc[-1]
            cur_ma60 = ma60.iloc[-1]
            
            # === 核心筛选条件 ===
            
            # 条件1: 均线多头初成 (MA5>MA10>MA20，且MA20刚上穿或接近MA60)
            ma_bullish = cur_ma5 > cur_ma10 > cur_ma20
            ma20_near_ma60 = abs(cur_ma20 / cur_ma60 - 1) < 0.05  # MA20在MA60的5%以内
            ma20_cross_ma60 = cur_ma20 > cur_ma60  # MA20已上穿MA60
            ma_golden = ma20_near_ma60 or ma20_cross_ma60
            
            # 条件2: 放量突破 (近3日量比>1.5)
            vol_ratio = calc_vol_ratio(vol, 20)
            recent_vol_ratio = vol_ratio.iloc[-3:].mean()
            vol_breakout = recent_vol_ratio > 1.3
            
            # 条件3: 价格突破20日平台
            platform_high = high.iloc[-21:-1].max()  # 前20日最高
            price_breakout = cur_close > platform_high * 0.98  # 接近或突破平台
            
            # 条件4: 底部区域启动 (60日价格分位 < 70%，说明不是高位)
            price_pos_60 = calc_price_position(close, high, low, 60).iloc[-1]
            price_pos_120 = calc_price_position(close, high, low, 120).iloc[-1]
            from_bottom = price_pos_120 < 70  # 120日分位<70%，还有空间
            
            # 条件5: 波动率适中 (不追高波动票)
            vol20 = calc_volatility(close, 20).iloc[-1]
            vol_ok = vol20 < 60  # 年化波动率<60%
            
            # 条件6: 近5日涨幅5-20% (启动但不过热)
            pct_5d = (close.iloc[-1] / close.iloc[-6] - 1) * 100
            launch_ok = 3 < pct_5d < 20
            
            # 条件7: 横盘评估 (过去40日振幅<20%)
            recent40_high = high.iloc[-45:-5].max()
            recent40_low = low.iloc[-45:-5].min()
            platform_amp = (recent40_high - recent40_low) / recent40_low * 100
            platform_ok = platform_amp < 25  # 启动前横盘振幅不大
            
            # 综合评分
            score = 0
            if ma_bullish: score += 25
            if ma_golden: score += 20
            if vol_breakout: score += 15
            if price_breakout: score += 15
            if from_bottom: score += 10
            if launch_ok: score += 10
            if platform_ok: score += 5
            
            if score >= 60:  # 60分以上入选
                results.append({
                    'ts_code': ts_code,
                    'close': cur_close,
                    'mv_yi': row['mv_yi'],
                    'pe': row['pe'],
                    'pb': row['pb'],
                    'turnover_rate': row['turnover_rate'],
                    'score': score,
                    'pct_5d': round(pct_5d, 2),
                    'vol_ratio': round(recent_vol_ratio, 2),
                    'price_pos_120': round(price_pos_120, 1),
                    'volatility': round(vol20, 1),
                    'ma_bullish': ma_bullish,
                    'ma_cross': ma20_cross_ma60,
                    'platform_amp': round(platform_amp, 1),
                })
        except Exception as e:
            continue
    
    print(f"  技术筛选后剩余: {len(results)} 只")
    
    # Step 3: 补充行业和概念信息
    print("[3/5] 补充行业信息...")
    result_df = pd.DataFrame(results).sort_values('score', ascending=False)
    
    # 获取股票名称和行业
    stock_basic = pro.stock_basic(fields='ts_code,name,industry')
    name_map = dict(zip(stock_basic['ts_code'], stock_basic['name']))
    industry_map = dict(zip(stock_basic['ts_code'], stock_basic['industry']))
    result_df['name'] = result_df['ts_code'].map(name_map)
    result_df['industry'] = result_df['ts_code'].map(industry_map)
    
    # Step 4: 按行业聚合，找板块效应
    print("[4/5] 识别板块效应...")
    industry_counts = result_df['industry'].value_counts()
    hot_industries = industry_counts[industry_counts >= 2].index.tolist()  # 同行业>=2只入选
    
    result_df['is_sector_play'] = result_df['industry'].isin(hot_industries)
    # 板块效应加分
    result_df.loc[result_df['is_sector_play'], 'score'] += 10
    result_df = result_df.sort_values('score', ascending=False)
    
    # Step 5: 输出结果
    print("[5/5] 输出结果\n")
    
    if len(result_df) == 0:
        print("今日无符合条件的中军标的")
        return result_df
    
    # 打印热门板块
    if hot_industries:
        print("🔥 热门板块(>=2只入选):")
        for ind in hot_industries:
            cnt = industry_counts[ind]
            stocks = result_df[result_df['industry']==ind]['name'].tolist()
            print(f"  {ind}({cnt}只): {', '.join(stocks)}")
        print()
    
    # 打印TOP20
    print("🏆 中军候选 TOP20:")
    print("-" * 120)
    top = result_df.head(20)
    for _, r in top.iterrows():
        sector_tag = "🔥" if r['is_sector_play'] else "  "
        cross_tag = "金叉" if r['ma_cross'] else ""
        print(f"{sector_tag} {r['name']:6s}({r['ts_code']}) 评分:{r['score']:3.0f} "
              f"现价:{r['close']:8.2f} PE:{r['pe']:7.1f} 市值:{r['mv_yi']:6.1f}亿 "
              f"5日涨:{r['pct_5d']:+6.2f}% 量比:{r['vol_ratio']:4.2f} "
              f"120日分位:{r['price_pos_120']:5.1f}% 波动率:{r['volatility']:4.1f}% "
              f"均线多头:{'✓' if r['ma_bullish'] else '✗'} {cross_tag} "
              f"行业:{r['industry']}")
    
    print("\n" + "=" * 120)
    print(f"共筛选出 {len(result_df)} 只中军候选，其中 {len(result_df[result_df['is_sector_play']])} 只有板块效应")
    
    return result_df

if __name__ == '__main__':
    df = screen_zhongjun()
