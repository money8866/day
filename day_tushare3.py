import tushare as ts
import pandas as pd
import numpy as np
import requests
import os
import datetime
from dotenv import load_dotenv

load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
now = datetime.datetime.now()

# 9点前回退一天
if now.hour < 9:
    now = now - datetime.timedelta(days=1)

# 如果是周末继续回退
while now.weekday() >= 5:
    now = now - datetime.timedelta(days=1)

today = now.strftime("%Y%m%d")
pro = ts.pro_api(TUSHARE_TOKEN)

def get_full_daily(start_date, end_date):

    cal = pro.trade_cal(
        start_date=start_date,
        end_date=end_date,
        is_open=1
    )

    dates = cal["cal_date"].tolist()

    all_df = []

    for d in dates:

        df = pro.daily(trade_date=d)

        if df is None or len(df) == 0:
            continue

        df["trade_date"] = d
        all_df.append(df)

    return pd.concat(all_df, ignore_index=True)

def build_sector_data(start_date, end_date):

    df = get_full_daily(start_date, end_date)

    basic = pro.stock_basic(
        fields="ts_code,name,industry"
    )

    df = df.merge(basic, on="ts_code", how="left")

    # ⭐ 统一字段
    df.rename(columns={
        "ts_code": "code",
        "name": "名称",
        "industry": "板块",
        "pct_chg": "涨跌幅",
        "amount": "成交额"
    }, inplace=True)

    return df

def get_hist_market(start_date, end_date):

    df = pro.daily(
        start_date=start_date,
        end_date=end_date
    )

    basic = pro.stock_basic(
        fields="ts_code,name,industry"
    )

    df = df.merge(basic, on="ts_code", how="left")

    df["code"] = df["ts_code"].str.split(".").str[0]

    df.rename(columns={
        "name": "名称",
        "industry": "板块",
        "pct_chg": "涨跌幅",
        "amount": "成交额"
    }, inplace=True)

    # 成交额：千元 → 元
    df["成交额"] = df["成交额"] * 1000

    return df

def calc_daily_sector_strength(df):

    result = []

    grouped = df.groupby(["trade_date", "板块"])

    for (date, board), group in grouped:

        avg_pct = group["涨跌幅"].mean()

        limit_num = len(group[group["涨跌幅"] > 9.5])

        strong_num = len(group[group["涨跌幅"] > 5])

        total_amount = group["成交额"].sum()

        score = (
            avg_pct * 0.3 +
            limit_num * 15 +
            strong_num * 5 +
            np.log1p(total_amount / 1e8)
        )

        result.append({
            "trade_date": date,
            "板块": board,
            "平均涨幅": avg_pct,
            "涨停数": limit_num,
            "强势股数": strong_num,
            "成交额": total_amount,
            "score": score
        })

    return pd.DataFrame(result)

def detect_mainline_phase(sector_daily):

    results = []

    for board, group in sector_daily.groupby("板块"):

        group = group.sort_values("trade_date")

        print(board, len(group))
        # 至少8天数据
        if len(group) < 8:
            continue

        recent3 = group.tail(3)
        prev5 = group.iloc[-8:-3]

        recent_score = recent3["score"].mean()
        prev_score = prev5["score"].mean()

        accel = recent_score - prev_score

        phase = "震荡"

        # 🚀 启动
        if prev_score < 8 and recent_score > 15:
            phase = "启动"

        # 🔥 加速
        elif recent_score > prev_score * 1.15:
            phase = "加速"

        # ❄ 回调
        elif recent_score < prev_score * 0.85:
            phase = "回调"

        # 🌟 二波前夜
        elif (
            prev_score > 15 and
            recent_score > 8 and
            recent_score < prev_score
        ):
            phase = "二波前夜"

        results.append({
            "板块": board,
            "阶段": phase,
            "近期热度": round(recent_score, 2),
            "前期热度": round(prev_score, 2),
            "变化": round(accel, 2)
        })

    # ⭐⭐⭐⭐⭐ 关键修复（必须有）
    if len(results) == 0:

        return pd.DataFrame(columns=[
            "板块",
            "阶段",
            "近期热度",
            "前期热度",
            "变化"
        ])

    return pd.DataFrame(results)

def find_top_mainline(phase_df):

    # ⭐ 防空
    if phase_df.empty:
        return phase_df

    score_map = {
        "启动": 90,
        "加速": 100,
        "回调": 50,
        "二波前夜": 95,
        "震荡": 30
    }

    phase_df["score"] = phase_df["阶段"].map(score_map)

    return phase_df.sort_values(
        "score",
        ascending=False
    )

def get_trade_dates(days=60):

    today = datetime.datetime.now().strftime("%Y%m%d")

    cal = pro.trade_cal(
        start_date="20240101",
        end_date=today,
        is_open=1
    )

    # ⭐ 必须排序
    cal = cal.sort_values("cal_date")

    trade_dates = cal["cal_date"].tolist()

    # ⭐ 防止长度不够
    if len(trade_dates) < days:
        raise Exception(f"交易日不足：{len(trade_dates)}")

    end_date = trade_dates[-1]
    start_date = trade_dates[-days]

    print("start_date:", start_date)
    print("end_date:", end_date)

    return start_date, end_date

def calc_second_wave_score(group):

    recent = group.tail(3)
    prev = group.iloc[-8:-3]

    recent_score = recent["score"].mean()
    prev_score = prev["score"].mean()

    # 动量衰减
    decay = recent_score - prev_score

    # 是否有回流
    rebound = recent["score"].mean()

    # 波动（代表分歧）
    volatility = recent["score"].std()

    score = (
        prev_score * 0.4 +
        max(0, rebound) * 0.3 +
        (-decay) * 0.2 +
        (volatility) * 0.1
    )

    return score

def detect_second_wave(sector_daily):

    results = []

    for board, group in sector_daily.groupby("板块"):

        group = group.sort_values("trade_date")

        if len(group) < 10:
            continue

        score = calc_second_wave_score(group)

        results.append({
            "板块": board,
            "二波评分": score
        })

    df = pd.DataFrame(results)

    return df.sort_values("二波评分", ascending=False).head(10)

def run_mainline_system():


    print("📊 主线系统启动")

    start_date, end_date = get_trade_dates(60)

    # ⭐ 核心数据（替代所有 merge）
    df = build_sector_data(start_date, end_date)

    # ===== 板块时间序列 =====
    sector_daily = calc_daily_sector_strength(df)

    # ===== 主线阶段 =====
    phase_df = detect_mainline_phase(sector_daily)

    # ===== 排序 =====
    top = find_top_mainline(phase_df)

    print("\n🔥 当前主线阶段")
    print(top)

    second_wave = detect_second_wave(sector_daily)

    print("\n🌟 二波机会")
    print(second_wave)

    prompt = f"""
当前主线生命周期的板块有这些：

{top.head(10).to_string(index=False)}

二波机会可能性最大的板块是：

{second_wave.to_string(index=False)}

请分析：

1. 当前市场属于什么周期，及后三个交易日的预测
2. 哪些板块最可能继续主升，哪些龙头最值得关注龙头（请给出1-2个龙头，并且给出一句话点评）
3. 哪些板块明后天可能二波，结合近几个工作日个股行情分析给出这些板块中二波机会最大的个股（请给出1-2个，并且给出一句话点评）

"""

    report = deepseek(prompt)

    print(report)

    send_wechat(
        report,
        os.getenv("WECHAT_SCKEY")
    )

# ========= 获取A股行情（Tushare版）=========
def get_market():

    # ===== 1. 行情 =====
    df = pro.daily(trade_date=today)

    # ===== 2. 基本面（市值）=====
    basic = pro.daily_basic(
        trade_date=today,
        fields="ts_code,total_mv,turnover_rate"
    )

    # ===== 3. 名称 =====
    name = pro.stock_basic(fields="ts_code,name")

    # ===== 4. 合并 =====
    df = df.merge(basic, on="ts_code", how="left")
    df = df.merge(name, on="ts_code", how="left")

    # ===== 5. 字段统一 =====
    df["code"] = df["ts_code"].str.split(".").str[0]
    df["name"] = df["name"]

    df["pct_chg"] = df["pct_chg"].astype(float)
    df["vol"] = df["vol"].astype(float)
    df["amount"] = df["amount"].astype(float)

    # ⚠️ 成交额单位：千元 → 元
    df["amount"] = df["amount"] * 1000

    # ⭐ 关键修复：total_mv来自daily_basic
    df["total_mv"] = df["total_mv"].fillna(0)

    return df

# ========= 涨停 =========
def detect_limit(df):
    return df[df["pct_chg"] > 9.5]


# ========= 强势股 =========
def strong_stock(df):
    return df[(df["pct_chg"] > 5) & (df["vol"] > df["vol"].mean())]


# ========= 情绪 =========
def emotion_score(df):
    up = len(df[df["pct_chg"] > 0])
    down = len(df[df["pct_chg"] < 0])
    limit = len(df[df["pct_chg"] > 9.5])
    return (up - down) + limit * 5


# ========= 伪板块（保持你原逻辑）=========
def pseudo_sector(df):
    return df.sort_values(by="pct_chg", ascending=False).head(20)


# ========= 龙头评分 =========
def leader_score(df):
    df = df.copy()

    df["score"] = (
        df["pct_chg"] * 0.4 +
        (df["vol"] / df["vol"].max()) * 0.3 +
        (df["amount"] / df["amount"].max()) * 0.3
    )

    return df.sort_values(by="score", ascending=False).head(10)



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

    return r.json()["choices"][0]["message"]["content"]


# ========= 微信 =========
def send_wechat(msg, key):
    url = f"https://sctapi.ftqq.com/{key}.send"
    data = {"title": "每日复盘", "desp": msg}
    requests.post(url, data=data)


# =========================
# 1. 获取行情数据（替换AkShare🔥）
# =========================
def get_market_data():
    

    # ===== 1. 行情 =====
    df = pro.daily(trade_date=today)

    # ===== 2. 基本面（市值等）=====
    basic = pro.daily_basic(
        trade_date=today,
        fields="ts_code,total_mv"
    )

    # ===== 3. 股票名称 =====
    name_df = pro.stock_basic(fields="ts_code,name")

    # ===== 4. 合并 =====
    df = df.merge(basic, on="ts_code", how="left")
    df = df.merge(name_df, on="ts_code", how="left")

    # ===== 5. 字段处理 =====
    df['代码'] = df['ts_code'].str.split('.').str[0]
    df['名称'] = df['name']
    df['最新价'] = df['close']
    df['涨跌幅'] = df['pct_chg']
    df['成交额'] = df['amount'] * 1000

    # ⭐ 关键修复点（这里不会再报错）
    df['总市值'] = df['total_mv'] * 10000

    df = df[['代码', '名称', '最新价', '涨跌幅', '成交额', '总市值']]

    df.dropna(inplace=True)

    return df

# =========================
# 2. 获取行业映射（替换AkShare🔥）
# =========================
def get_industry_map():
    df = pro.stock_basic(exchange='', list_status='L',
                         fields='ts_code,industry')

    df['代码'] = df['ts_code'].apply(lambda x: x.split('.')[0])
    df['板块'] = df['industry']
    

    return df[['代码', '板块']].dropna()

# ========= 主流程 =========
def run():
    print("📊 Tushare复盘启动...")

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

热门个股：
{hot[['code','name','pct_chg']].to_string(index=False)}

龙头候选：
{leaders[['code','name','score']].to_string(index=False)}

请输出：
1. 市场情绪周期
2. 最强主线（1-2个）
3. 龙头股（1-2个）
4. 明日策略
5. 前面五天我让你做过的复盘中最强主线的明日二波机会（请给出3-5个个股，并且给出一句话点评）
"""

    report = deepseek(prompt)

    with open(f"report_{today}.txt", "w", encoding="utf-8") as f:
        f.write(report)

    print("✅ 完成")
    print(report)

    send_wechat(f"{report}\n", os.getenv("WECHAT_SCKEY"))

    # =========================
    # 🔥 主线回调末端识别
    # =========================
    print("\n====== 回调末端主线 ======")


    run_mainline_system()


if __name__ == "__main__":
    run()