import tushare as ts
import akshare as ak
import pandas as pd
import numpy as np
import datetime
import requests
import os
from dotenv import load_dotenv

load_dotenv()

# ========= 配置 =========
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

today = datetime.datetime.now().strftime("%Y%m%d")

# ========= 数据获取 =========
def get_daily():
    return pro.daily(trade_date=today)

def get_basic():
    return pro.stock_basic(exchange='', list_status='L',
                           fields='ts_code,name,industry')

def get_moneyflow():
    try:
        return ak.stock_fund_flow_individual(symbol="即时")
    except:
        return pd.DataFrame()

# ========= 情绪指标 =========
def emotion_score(df):
    up = len(df[df['pct_chg'] > 0])
    down = len(df[df['pct_chg'] < 0])
    limit_up = len(df[df['pct_chg'] > 9.5])

    score = (up - down) + limit_up * 5
    return score

# ========= 涨停 / 强势 =========
def detect_limit(df):
    return df[df['pct_chg'] > 9.5]

def strong_stock(df):
    return df[(df['pct_chg'] > 5) & (df['vol'] > df['vol'].mean())]

# ========= 板块热度 =========
def sector_heat(df, basic):
    df = df.merge(basic, on='ts_code')
    heat = df.groupby('industry')['pct_chg'].mean()
    return heat.sort_values(ascending=False).head(10)

# ========= 连板（简化版） =========
def fake_lianban(df):
    # 用涨幅近似模拟（真实需历史数据）
    return df[df['pct_chg'] > 9.5].sort_values(by='vol', ascending=False).head(20)

# ========= 龙头评分 =========
def leader_score(df):
    df['score'] = (
        df['pct_chg'] * 0.4 +
        (df['vol'] / df['vol'].max()) * 0.3 +
        (df['amount'] / df['amount'].max()) * 0.3
    )
    return df.sort_values(by='score', ascending=False).head(10)

# ========= DeepSeek =========
def deepseek(prompt):
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }

    data = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "你是A股顶级游资复盘专家"},
            {"role": "user", "content": prompt}
        ]
    }

    r = requests.post(url, headers=headers, json=data)
    return r.json()['choices'][0]['message']['content']

def deepseek_v4(prompt):
    url = "https://api.deepseek.com/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }

    data = {
        "model": "deepseek-v4",   # ← 关键修改
        "messages": [
            {
                "role": "system",
                "content": "你是顶级A股游资复盘专家，擅长识别龙头、情绪周期和二波机会"
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "temperature": 0.7,     # 控制发散
        "max_tokens": 2000
    }

    response = requests.post(url, headers=headers, json=data)

    if response.status_code != 200:
        print("❌ API错误:", response.text)
        return ""

    return response.json()['choices'][0]['message']['content']

# ========= 主流程 =========
def run():
    print("📊 开始复盘...")

    df = get_daily()
    basic = get_basic()

    limit_df = detect_limit(df)
    strong_df = strong_stock(df)
    heat = sector_heat(df, basic)
    lianban = fake_lianban(df)
    leaders = leader_score(limit_df.copy())

    emotion = emotion_score(df)

    prompt = f"""
    今日市场数据：

    情绪值：{emotion}
    涨停数：{len(limit_df)}
    强势股：{len(strong_df)}

    热门行业：
    {heat.to_string()}

    连板候选：
    {lianban[['ts_code','pct_chg','vol']].to_string()}

    龙头候选：
    {leaders[['ts_code','score']].to_string()}

    请输出：
    1. 市场情绪周期（冰点/回暖/主升/分歧/退潮）
    2. 今日主线
    3. 龙头股判断（是否唯一）
    4. 是否具备二波结构
    5. 明日策略（进攻/防守/空仓）
    """

    report = deepseek_v4(prompt)

    file = f"report_{today}.txt"
    with open(file, "w", encoding="utf-8") as f:
        f.write(report)

    print("✅ 完成复盘")
    print(report)

# ========= 执行 =========
if __name__ == "__main__":
    run()