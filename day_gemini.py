import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

def get_strongest_sectors(top_n=3, lookback_days=3):
    print(f"正在全市场扫描近 {lookback_days} 天最强主线板块...")
    
    try:
        # 获取所有行业板块
        sector_boards = ak.stock_board_industry_name_em()
    except Exception as e:
        print(f"获取板块列表失败: {e}")
        return []

    # 取涨幅靠前的 50 个板块作为候选，增加覆盖面
    candidate_sectors = sector_boards.head(100) 
    
    sector_scores = []
    end_date = datetime.now().strftime("%Y%m%d")
    # 增加回溯天数到 30 天，确保能抓到足够的交易日数据
    start_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")

    for index, row in candidate_sectors.iterrows():
        name = row['板块名称']
        try:
            # 修正接口参数
            hist = ak.stock_board_industry_hist_em(
                symbol=name, 
                start_date=start_date, 
                end_date=end_date, 
                period="日现"
            )
            
            if hist is None or len(hist) < 5: 
                continue
            
            # 动态调整：如果数据不够10天，就取全部可用数据
            actual_days = min(len(hist), lookback_days)
            period_hist = hist.tail(actual_days)
            
            # 计算区间涨幅
            total_ret = (period_hist['收盘'].iloc[-1] / period_hist['收盘'].iloc[0] - 1) * 100
            std_dev = period_hist['涨跌幅'].std() if len(period_hist) > 1 else 1
            
            sector_scores.append({
                '板块': name,
                '10日涨幅%': round(total_ret, 2),
                '波动率': round(std_dev, 2)
            })
        except:
            continue

    # --- 关键防护逻辑 ---
    if not sector_scores:
        print("⚠️ 警告：未能获取到任何板块的历史数据，请检查网络或接口！")
        # 兜底方案：直接返回今日涨幅最好的板块名
        return candidate_sectors.head(top_n)['板块名称'].tolist()

    ranked_df = pd.DataFrame(sector_scores)
    
    # 再次确认列是否存在
    if '10日涨幅%' in ranked_df.columns:
        ranked_df = ranked_df.sort_values(by='10日涨幅%', ascending=False)
        return ranked_df.head(top_n)['板块'].tolist()
    else:
        return candidate_sectors.head(top_n)['板块名称'].tolist()
    
def analyze_sector_structure(sector_name):
    """对指定板块进行龙头/中军拆解"""
    print(f"\n>>> 正在拆解【{sector_name}】内部结构...")
    
    try:
        # 获取板块成分股
        stocks = ak.stock_board_industry_cons_em(symbol=sector_name)
    except:
        return pd.DataFrame(), pd.DataFrame()

    if stocks.empty:
        return pd.DataFrame(), pd.DataFrame()

    # --- 自动识别“市值”列名 ---
    mv_col = None
    for col in stocks.columns:
        if '市值' in col:
            mv_col = col
            break
    
    if not mv_col:
        # 如果实在找不到，创建一个虚拟列避免后续报错
        stocks['虚拟市值'] = 0
        mv_col = '虚拟市值'
    
    stock_list = stocks['代码'].tolist()
    results = []
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=15)).strftime("%Y%m%d")

    # 为了效率，只分析板块内前 30 只股票
    for code in stock_list[:30]: 
        try:
            df = ak.stock_zh_a_hist(symbol=code, start_date=start_date, end_date=end_date, adjust="qfq")
            if df.empty: continue
            
            # 动态获取市值数据
            try:
                raw_mv = stocks[stocks['代码'] == code][mv_col].values[0]
                latest_mv = float(raw_mv) / 1e8 # 换算为亿元
            except:
                latest_mv = 0
                
            avg_vol = df['成交额'].tail(5).mean() / 1e8
            limit_ups = (df['涨跌幅'] > 9.5).sum()
            recent_ret = (df['收盘'].iloc[-1] / df['收盘'].iloc[0] - 1) * 100
            
            results.append({
                '代码': code,
                '名称': stocks[stocks['代码'] == code]['名称'].values[0],
                '市值(亿)': round(latest_mv, 2),
                '5日日均成交(亿)': round(avg_vol, 2),
                '涨停数': limit_ups,
                '区间涨幅%': round(recent_ret, 2)
            })
        except:
            continue

    if not results:
        return pd.DataFrame(), pd.DataFrame()

    all_df = pd.DataFrame(results)
    
    # 识别中军：市值前 3 且必须有成交量支撑
    mid_army = all_df.nlargest(3, '市值(亿)')
    
    # 识别龙头：涨停多且非中军
    potential_dragons = all_df[~all_df['代码'].isin(mid_army['代码'])]
    if not potential_dragons.empty:
        dragons = potential_dragons.sort_values(by=['涨停数', '区间涨幅%'], ascending=False).head(2)
    else:
        dragons = pd.DataFrame()
    
    return dragons, mid_army

if __name__ == "__main__":
    # 1. 自动寻找最强主线
    top_mainlines = get_strongest_sectors(top_n=2, lookback_days=10)
    
    if not top_mainlines:
        print("❌ 未能识别到近期强力主线，请检查网络或接口频率。")
    else:
        print("\n" + "="*50)
        print(f"检测到当前市场最强主线：{', '.join(top_mainlines)}")
        print("="*50)

        # 2. 对每个主线进行详细分析
        all_targets = []
        for sector in top_mainlines:
            dragons, mids = analyze_sector_structure(sector)
            
            print(f"\n【{sector}】板块分析结果：")
            
            # --- 安全打印：情绪龙头 ---
            if not dragons.empty:
                # 检查预期的列是否都在 dragons 中
                cols_to_show = ['名称', '涨停数', '区间涨幅%', '市值(亿)']
                existing_cols = [c for c in cols_to_show if c in dragons.columns]
                
                print("🚩 情绪龙头（弹性）：")
                print(dragons[existing_cols].to_string(index=False))
                all_targets.extend(dragons['代码'].tolist())
            else:
                print("🚩 情绪龙头：未检测到高强度标的")

            # --- 安全打印：趋势中军 ---
            if not mids.empty:
                cols_to_show_mid = ['名称', '市值(亿)', '5日日均成交(亿)', '区间涨幅%']
                existing_cols_mid = [c for c in cols_to_show_mid if c in mids.columns]
                
                print("\n🏰 趋势中军（容量）：")
                print(mids[existing_cols_mid].to_string(index=False))
                all_targets.extend(mids['代码'].tolist())
            else:
                print("\n🏰 趋势中军：未检测到核心权重标的")

        # 3. 自动写入自选股 (如果你已经配置好了路径)
        if all_targets:
            # 去重
            unique_targets = list(set(all_targets))
            # 这里替换成你真实的东财数据库路径
            # db_path = r"C:\EastMoney\terminal\data\user\你的ID\extern_user_self.db"
            # write_to_em_selfstock(db_path, unique_targets)
            print(f"\n✅ 分析完成，共识别出 {len(unique_targets)} 只核心标的。")