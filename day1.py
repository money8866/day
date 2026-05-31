# -*- coding: utf-8 -*-
import os
import akshare as ak
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from matplotlib import lines
import pandas as pd
import numpy as np
import requests
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()


# =========================
# 1. 获取行情数据
# =========================
def get_market_data():
    df = ak.stock_zh_a_spot_em()
    print ("Get market data, total stocks:", len(df))
    df = df[['代码', '名称', '最新价', '涨跌幅', '成交额', '总市值']]
    
    # 数据清洗
    df['涨跌幅'] = pd.to_numeric(df['涨跌幅'], errors='coerce')
    df['成交额'] = pd.to_numeric(df['成交额'], errors='coerce')
    df['总市值'] = pd.to_numeric(df['总市值'], errors='coerce')
    
    df.dropna(inplace=True)
    
    return df


# =========================
# 2. 获取行业映射
# =========================
def get_industry_map():
    industry_names = ak.stock_board_industry_name_em()
    
    result = []
    
    for _, row in industry_names.iterrows():
        try:
            cons = ak.stock_board_industry_cons_em(symbol=row['板块名称'])
            cons['板块'] = row['板块名称']
            result.append(cons[['代码', '板块']])
        except:
            continue
    
    industry_map = pd.concat(result)
    industry_map.drop_duplicates(inplace=True)
    
    return industry_map


# =========================
# 3. 获取概念映射
# =========================
def get_concept_map():
    concept_names = ak.stock_board_concept_name_em()
    
    result = []
    
    for _, row in concept_names.iterrows():
        try:
            cons = ak.stock_board_concept_cons_em(symbol=row['板块名称'])
            cons['板块'] = row['板块名称']
            result.append(cons[['代码', '板块']])
        except:
            continue
    
    concept_map = pd.concat(result)
    concept_map.drop_duplicates(inplace=True)
    
    return concept_map


# =========================
# 4. 板块打分模型
# =========================
def calc_sector_score(df):
    sector = df.groupby('板块').agg({
        '涨跌幅': 'mean',
        '成交额': 'sum',
        '代码': 'count'
    }).rename(columns={'代码': '个股数'})
    
    # 涨停数（近似）
    limit_up = df[df['涨跌幅'] > 9.5].groupby('板块').size()
    sector['涨停数'] = limit_up
    sector['涨停数'] = sector['涨停数'].fillna(0)
    
    # 打分
    sector['score'] = (
        sector['涨跌幅'] * 2 +
        np.log1p(sector['成交额']) * 5 +
        sector['涨停数'] * 10
    )
    
    sector = sector.sort_values(by='score', ascending=False)
    
    return sector


# =========================
# 5. 龙头识别
# =========================
def find_leaders(df, board):
    sub = df[df['板块'] == board]
    
    leaders = sub.sort_values(
        by=['涨跌幅', '成交额'],
        ascending=[False, False]
    ).head(3)
    
    return leaders[['代码', '名称', '涨跌幅', '成交额']]


# =========================
# 6. 中军识别
# =========================

def check_volume_trend(df_hist):
    """
    成交量温和放大：
    最近5日均量 > 前5日均量
    且不是爆量（避免一日游）
    """
    if len(df_hist) < 10:
        return False
    
    vol_recent = df_hist['成交量'].tail(5).mean()
    vol_prev = df_hist['成交量'].iloc[-10:-5].mean()
    
    # 温和放大：1.2倍以内
    return vol_recent > vol_prev and vol_recent < vol_prev * 2


def check_no_limit_up(df_hist):
    """
    未连续涨停：
    最近5天没有 >=9.5%的涨停
    """
    recent = df_hist.tail(5)
    return not any(recent['涨跌幅'] > 9.5)


def check_breakout(df_hist):
    """
    刚突破：
    当前收盘价 > 过去20日最高价
    且突破发生在最近3天
    """
    if len(df_hist) < 25:
        return False
    
    recent = df_hist.tail(3)
    prev_high = df_hist['收盘'].iloc[-23:-3].max()
    
    return any(recent['收盘'] > prev_high)


def find_core(df, board):
    sub = df[df['板块'] == board]
    
    candidates = sub[
        (sub['总市值'] > 50e8) &
        (sub['总市值'] < 500e8) &
        (sub['涨跌幅'] > 1)
    ]
    
    result = []
    
    for _, row in candidates.iterrows():
        code = row['代码']
        
        try:
            hist = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date="20240101",
                adjust="qfq"
            )
            
            hist = hist.tail(60)
            
            hist['成交量'] = pd.to_numeric(hist['成交量'], errors='coerce')
            hist['收盘'] = pd.to_numeric(hist['收盘'], errors='coerce')
            hist['涨跌幅'] = pd.to_numeric(hist['涨跌幅'], errors='coerce')
            
            if hist.isnull().values.any():
                continue
            
            cond1 = check_volume_trend(hist)
            cond2 = check_no_limit_up(hist)
            cond3 = check_breakout(hist)
            
            if cond1 and cond2 and cond3:
                result.append({
                    '代码': code,
                    '名称': row['名称'],
                    '涨跌幅': row['涨跌幅'],
                    '成交额': row['成交额'],
                    '总市值': row['总市值']
                })
        
        except:
            continue
    
    if len(result) == 0:
        return pd.DataFrame()
    
    result_df = pd.DataFrame(result)
    
    return result_df.sort_values(by='成交额', ascending=False).head(5)

def send_wechat(msg, key):
    url = f"https://sctapi.ftqq.com/{key}.send"
    data = {
        "title": "每日复盘",
        "desp": msg
    }
    requests.post(url, data=data)

# ========= DeepSeek =========
def deepseek(prompt):
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
    url = "https://api.deepseek.com/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }

    data = {
        "model": "deepseek-v4-pro",
        "messages": [
            {"role": "system", "content": "你是A股顶级游资复盘专家"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7
    }

    r = requests.post(url, headers=headers, json=data)

    if r.status_code != 200:
        print("API错误:", r.text)
        return ""

    return r.json()['choices'][0]['message']['content']


# =========================
# 7. 主流程
# =========================
def run_analysis():
    print(f"\n📊 盘后分析开始：{datetime.now()}")

    # 数据
    market_df = get_market_data()
    
    # 行业
    industry_map = get_industry_map()
    industry_df = market_df.merge(industry_map, on='代码', how='inner')
    
    # 概念
    #concept_map = get_concept_map()
    #concept_df = market_df.merge(concept_map, on='代码', how='inner')
    
    # =========================
    # 行业主线
    # =========================
    print("\n====== 行业主线 ======")
    industry_sector = calc_sector_score(industry_df)
    top_industry = industry_sector.head(5)
    
    print(top_industry[['涨跌幅', '涨停数', 'score']])
    resultlines = []
    for board in top_industry.index:
        #print(f"\n【行业】{board}")
        resultlines.append(f"\n【行业】{board}")
        leaders = find_leaders(industry_df, board)
        core = find_core(industry_df, board)
        
        
        #print("龙头：")
        resultlines.append(f"\n龙头：")
        #print(leaders)
        resultlines.append(leaders.to_string(index=False)) 
        
        
        #print("中军：")
        resultlines.append(f"\n中军：")
        #print(core)
        resultlines.append(core.to_string(index=False)) 
        
    
    text = "".join(resultlines)
    print(text)

    prompt = f"""
    今日市场热点板块龙头与中军：{text}

    
    请按行业分段输出：
    1、龙头股(一句话点评)
    2、龙头股能带动的适合跟踪介入的高弹性标的（含代码、名称和一句话点评)
    3. 明日策略
    """

    report = deepseek(prompt)

    send_wechat(report, os.getenv("WECHAT_SCKEY"))

    # =========================
    # 概念主线
    # =========================
    #print("\n====== 概念主线 ======")
    #concept_sector = calc_sector_score(concept_df)
    #top_concept = concept_sector.head(5)
    
    #print(top_concept[['涨跌幅', '涨停数', 'score']])
    
    #for board in top_concept.index:
    #    print(f"\n【概念】{board}")
    #    
    #    leaders = find_leaders(concept_df, board)
    #    core = find_core(concept_df, board)
    #    
    #    print("龙头：")
    #    print(leaders)
        
    #    print("中军：")
    #    print(core)


# =========================
# 8. 运行
# =========================
if __name__ == "__main__":
    run_analysis()