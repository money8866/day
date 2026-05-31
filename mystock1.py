import akshare as ak
import pandas as pd
import numpy as np
import datetime
import requests
import os
from dotenv import load_dotenv

load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

today = datetime.datetime.now().strftime("%Y-%m-%d")

# ========= 获取A股行情 =========
def get_market():
    df = ak.stock_zh_a_spot_em()
    # 字段统一
    df = df.rename(columns={
        "代码": "code",
        "名称": "name",
        "涨跌幅": "pct_chg",
        "成交量": "vol",
        "成交额": "amount"
    })
    return df

# ========= 涨停 =========
def detect_limit(df):
    return df[df['pct_chg'] > 9.5]

# ========= 强势股 =========
def strong_stock(df):
    return df[(df['pct_chg'] > 5) & (df['vol'] > df['vol'].mean())]

# ========= 情绪 =========
def emotion_score(df):
    up = len(df[df['pct_chg'] > 0])
    down = len(df[df['pct_chg'] < 0])
    limit = len(df[df['pct_chg'] > 9.5])
    return (up - down) + limit * 5

# ========= 板块（简化：用涨幅排序） =========
def pseudo_sector(df):
    return df.sort_values(by='pct_chg', ascending=False).head(20)

# ========= 龙头评分 =========
def leader_score(df):
    df = df.copy()
    df['score'] = (
        df['pct_chg'] * 0.4 +
        (df['vol'] / df['vol'].max()) * 0.3 +
        (df['amount'] / df['amount'].max()) * 0.3
    )
    return df.sort_values(by='score', ascending=False).head(10)

# ========= DeepSeek =========
def deepseek(prompt):
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


def send_wechat(msg, key):
    url = f"https://sctapi.ftqq.com/{key}.send"
    data = {
        "title": "每日复盘",
        "desp": msg
    }
    requests.post(url, data=data)

# ========= 主流程 =========
def run():
    print("📊 AkShare复盘启动...")

    df = get_market()

    limit_df = detect_limit(df)
    strong_df = strong_stock(df)
    emotion = emotion_score(df)
    leaders = leader_score(limit_df)

    hot = pseudo_sector(df)

    prompt = f"""
    今日市场：

    情绪值：{emotion}
    涨停数：{len(limit_df)}
    强势股：{len(strong_df)}

    热门个股（近似板块）：
    {hot[['code','name','pct_chg']].to_string(index=False)}

    龙头候选：
    {leaders[['code','name','score']].to_string(index=False)}

    请输出：
    1. 市场情绪周期
    2. 最强的核心主线（1-2个）
    3. 龙头股（1-2个）
    4. 最强核心主线中已经温和放量最有可能补涨的个股（每个板块1-2个）
    5. 明日策略
    6. 明日最有可能二波启动的前期主线
    """

    report = deepseek(prompt)

    with open(f"report_{today}.txt", "w", encoding="utf-8") as f:
        f.write(report)
    
    print("✅ 完成")
    print(report)
    send_wechat(report, os.getenv("WECHAT_SCKEY"))

if __name__ == "__main__":
    run()