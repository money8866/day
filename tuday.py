import tushare as ts
import pandas as pd
import numpy as np
import datetime
import requests
import os
from dotenv import load_dotenv

# =========================
# 加载环境变量
# =========================
load_dotenv()

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
WECHAT_SCKEY = os.getenv("WECHAT_SCKEY")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_daily")
REPORT_DIR = os.path.join(BASE_DIR, "report_daily")

# =========================
# 初始化 Tushare
# =========================
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

today = datetime.datetime.now().strftime("%Y%m%d")

# =========================
# 获取最近交易日
# =========================
def get_last_trade_date():

    cal = pro.trade_cal(
        exchange='',
        start_date='20240101',
        end_date=today
    )

    cal = cal[cal['is_open'] == 1]

    last_trade_date = (
        cal[cal['cal_date'] <= today]
        ['cal_date']
        .max()
    )

    return last_trade_date


TRADE_DATE = get_last_trade_date()

print(f"当前交易日: {TRADE_DATE}")

# =========================
# 获取A股行情
# =========================
def get_market():

    df = pro.daily(
        trade_date=TRADE_DATE
    )

    # 获取基本信息
    basic = pro.stock_basic(
        exchange='',
        list_status='L',
        fields='ts_code,name'
    )

    # 合并名称
    df = df.merge(
        basic,
        on='ts_code',
        how='left'
    )

    # 成交额单位统一（万元 -> 元）
    df['amount'] = df['amount'] * 10000

    # 字段统一
    df = df.rename(columns={
        'ts_code': 'code',
        'name': 'name',
        'pct_chg': 'pct_chg',
        'vol': 'vol',
        'amount': 'amount'
    })

    return df


# =========================
# 涨停
# =========================
def detect_limit(df):

    return df[df['pct_chg'] > 9.5]


# =========================
# 强势股
# =========================
def strong_stock(df):

    return df[
        (df['pct_chg'] > 5) &
        (df['vol'] > df['vol'].mean())
    ]


# =========================
# 市场情绪
# =========================
def emotion_score(df):

    up = len(df[df['pct_chg'] > 0])

    down = len(df[df['pct_chg'] < 0])

    limit_num = len(df[df['pct_chg'] > 9.5])

    score = (up - down) + limit_num * 5

    return score


# =========================
# 热门板块（简化）
# =========================
def pseudo_sector(df):

    return (
        df.sort_values(
            by='pct_chg',
            ascending=False
        )
        .head(20)
    )


# =========================
# 龙头评分
# =========================
def leader_score(df):

    df = df.copy()

    df['score'] = (
        df['pct_chg'] * 0.4 +
        (df['vol'] / df['vol'].max()) * 0.3 +
        (df['amount'] / df['amount'].max()) * 0.3
    )

    return (
        df.sort_values(
            by='score',
            ascending=False
        )
        .head(10)
    )


# =========================
# DeepSeek 分析
# =========================
def deepseek(prompt):

    url = "https://api.deepseek.com/chat/completions"

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }

    data = {
        "model": "deepseek-v4-pro",
        "messages": [
            {
                "role": "system",
                "content": "你是A股顶级游资复盘专家"
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "temperature": 0.7
    }

    r = requests.post(
        url,
        headers=headers,
        json=data
    )

    if r.status_code != 200:
        print("DeepSeek API错误:", r.text)
        return ""

    return r.json()['choices'][0]['message']['content']


# =========================
# 微信推送
# =========================
def send_wechat(msg, key):

    if not key:
        print("未配置微信Key")
        return

    url = f"https://sctapi.ftqq.com/{key}.send"

    data = {
        "title": f"A股每日复盘 {TRADE_DATE}",
        "desp": msg
    }

    requests.post(url, data=data)


# =========================
# 主流程
# =========================
def run():

    print("📊 Tushare复盘启动...")

    # 获取市场数据
    df = get_market()

    print(f"股票数量: {len(df)}")

    # 涨停
    limit_df = detect_limit(df)

    # 强势股
    strong_df = strong_stock(df)

    # 情绪
    emotion = emotion_score(df)

    # 龙头
    leaders = leader_score(limit_df)

    # 热门
    hot = pseudo_sector(df)

    # AI分析 Prompt
    prompt = f"""
今日A股市场复盘：

交易日：{TRADE_DATE}

市场情绪值：{emotion}

涨停数：{len(limit_df)}

强势股数量：{len(strong_df)}

热门个股：
{hot[['code','name','pct_chg']].to_string(index=False)}

龙头候选：
{leaders[['code','name','score']].to_string(index=False)}

请输出：

1、市场情绪周期位置

2、当前最强主线（1-2个）

3、核心龙头股（1-2个）

4、主线中最可能补涨的趋势中军

5、明日短线策略

6、最可能二波启动的旧主线

7、风险提示
"""

    # AI生成复盘
    report = deepseek(prompt)

    # 保存文件
    filename = os.path.join(REPORT_DIR, f"report_{TRADE_DATE}.txt")

    with open(
        filename,
        "w",
        encoding="utf-8"
    ) as f:
        f.write(report)

    print("✅ 复盘完成")

    print(report)

    # 微信推送
    send_wechat(
        report,
        WECHAT_SCKEY
    )


# =========================
# 启动
# =========================
if __name__ == "__main__":

    run()