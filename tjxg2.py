import tushare as ts
import pandas as pd
import numpy as np
import requests
import os
import time
from datetime import datetime
from dotenv import load_dotenv

# =========================
# 环境变量
# =========================
load_dotenv()

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_daily")
REPORT_DIR = os.path.join(BASE_DIR, "report_daily")

# =========================
# 初始化 tushare
# =========================
ts.set_token(TUSHARE_TOKEN)

pro = ts.pro_api()

# =========================
# 缓存目录
# =========================
#CACHE_DIR = "cache_daily"

if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

# =========================
# 获取最近交易日
# =========================
def get_last_trade_date():

    today = datetime.today().strftime('%Y%m%d')

    # 获取交易日历
    cal = pro.trade_cal(
        exchange='',
        start_date='20200101',
        end_date=today
    )

    # 只保留开市日
    cal = cal[cal['is_open'] == 1]

    # 找到小于今天的最近交易日
    last_trade_date = cal[cal['cal_date'] <= today]['cal_date'].max()

    return last_trade_date


TRADE_DATE = get_last_trade_date()

print("当前交易日:", TRADE_DATE)

# =========================
# BARSLAST
# =========================
def barslast(series):

    result = []

    last_true = -1

    for i, val in enumerate(series):

        if val:
            last_true = i
            result.append(0)

        else:

            if last_true == -1:
                result.append(np.nan)

            else:
                result.append(i - last_true)

    return pd.Series(result, index=series.index)

# =========================
# 缓存历史数据
# =========================
def get_hist_data(ts_code):

    cache_file = os.path.join(
        CACHE_DIR,
        f"{ts_code}.csv"
    )

    # =========================
    # 优先读取缓存
    # =========================
    if os.path.exists(cache_file):

        try:

            df = pd.read_csv(cache_file)

            # 已是最新交易日
            if str(df.iloc[-1]['trade_date']) == TRADE_DATE:

                df = df.sort_values('trade_date')

                return df

        except:
            pass

    # =========================
    # 下载最新数据
    # =========================
    try:

        df = pro.daily(
            ts_code=ts_code,
            start_date='20250101',
            end_date=TRADE_DATE
        )

        if df.empty:
            return None

        df = df.sort_values('trade_date')

        # 保存缓存
        df.to_csv(
            cache_file,
            index=False
        )

        # 防止频率限制
        time.sleep(0.01)

        return df

    except Exception as e:

        print(f"{ts_code} 下载失败:", e)

        return None

# =========================
# 主策略
# =========================
def strategy(df, total_mv, name, code):

    C = df['close']
    H = df['high']
    VOL = df['vol']

    # =========================
    # ST 条件
    # =========================
    ST = (
        total_mv > 60e8 and
        ('ST' not in name) and
        ('*ST' not in name) and
        (
            code.startswith('688') or
            code.startswith('300') or
            code.startswith('301')
        )
    )

    if not ST:
        return False

    # =========================
    # 涨停定义
    # =========================
    ZT = (
        (C.shift(1) / C.shift(2) < 1.08) &
        (C / C.shift(1) > 1.102) &
        (VOL / VOL.rolling(60).mean() > 1.5)
    )

    ZTTS = barslast(ZT)

    ztts = ZTTS.iloc[-1]

    if np.isnan(ztts):
        return False

    ztts = int(ztts)

    # =========================
    # TJ 条件
    # =========================
    cond1 = ztts > 2 and ztts <= 30

    ref_close = C.shift(ztts + 1).iloc[-1]

    recent_close = C.iloc[-ztts:]

    cond2 = (recent_close < ref_close).sum() == 0

    cond3 = (
        recent_close.max() /
        recent_close.min()
    ) < 1.3

    cond4 = (
        C.iloc[-1] /
        H.shift(ztts).iloc[-1]
    ) < 1.1

    cond5 = (
        H.iloc[-ztts:].max() >=
        H.iloc[-60:].max() * 0.9
    )

    ma22 = C.rolling(22).mean()

    cond6 = (
        ma22.iloc[-1] >=
        ma22.iloc[-2]
    )

    TJ = (
        cond1 and
        cond2 and
        cond3 and
        cond4 and
        cond5 and
        cond6
    )

    if not TJ:
        return False

    # =========================
    # XH
    # =========================
    highest_close = (
        C.iloc[-ztts-1:-1].max()
    )

    cond_xh1 = C.iloc[-1] > highest_close

    cond_xh2 = (
        C.iloc[-1] /
        C.iloc[-2]
    ) > 1.03

    XH = cond_xh1 and cond_xh2

    return XH

# =========================
# DeepSeek
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
                "content": "你是A股顶级机构趋势投资专家"
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "temperature": 0.5
    }

    r = requests.post(
        url,
        headers=headers,
        json=data
    )

    if r.status_code != 200:

        print(r.text)

        return ""

    return r.json()['choices'][0]['message']['content']

# =========================
# 市场数据
# =========================
def get_market():

    daily = pro.daily(
        trade_date=TRADE_DATE
    )

    basic = pro.stock_basic(
        exchange='',
        list_status='L',
        fields='ts_code,name'
    )

    mv = pro.daily_basic(
        trade_date=TRADE_DATE,
        fields='ts_code,total_mv'
    )

    df = daily.merge(
        basic,
        on='ts_code',
        how='left'
    )

    df = df.merge(
        mv,
        on='ts_code',
        how='left'
    )

    return df
# =========================
# 微信 + DeepSeek（不变）
# =========================
def send_wechat(msg, key):
    url = f"https://sctapi.ftqq.com/{key}.send"
    data = {"title": "每日精选个股", "desp": msg}
    requests.post(url, data=data)

# =========================
# 主程序
# =========================
def run():

    market = get_market()

    result = []

    total = len(market)

    for idx, row in market.iterrows():

        ts_code = row['ts_code']

        print(f"[{idx+1}/{total}] {ts_code}")

        try:

            hist = get_hist_data(ts_code)

            if hist is None or len(hist) < 80:
                continue

            ok = strategy(
                hist,
                row['total_mv'] * 10000,
                row['name'],
                ts_code
            )

            if ok:

                result.append({
                    '代码': ts_code,
                    '名称': row['name'],
                    '涨跌幅': row['pct_chg'],
                    '成交额': row['amount'],
                    '总市值': row['total_mv']
                })

                print("✅ 命中:", ts_code, row['name'])

        except Exception as e:

            print(ts_code, e)

            continue

    # =========================
    # 输出
    # =========================
    result_df = pd.DataFrame(result)

    if result_df.empty:

        print("无结果")

        return

    result_df = result_df.sort_values(
        by='成交额',
        ascending=False
    )

    print(result_df)

    # =========================
    # DeepSeek分析
    # =========================
    stock_text = result_df.to_string(index=False)

    prompt = f"""
以下股票是量化模型筛选出的趋势突破候选：

{stock_text}

请进一步分析并筛选：

1、业务增长明确
2、估值合理
3、属于当前热点
4、无明显财务雷
5、近期无减持
6、无增发预案
7、更可能成为趋势中军

输出：

1、最值得关注股票
2、每只逻辑
3、风险
4、主升浪潜力
"""

    report = deepseek(prompt)

    print("\n========== DeepSeek ==========\n")

    print(report)

    with open(
        os.path.join(REPORT_DIR, f"Deepseek_Self_{TRADE_DATE}.txt"),
        "w",
        encoding="utf-8"
    ) as f:

        f.write(report)
    send_wechat(report, os.getenv("WECHAT_SCKEY"))
    
# =========================
# 启动
# =========================
if __name__ == "__main__":

    run()