import akshare as ak
import pandas as pd
import numpy as np
import requests
import time
import schedule
import os
import sqlite3
from datetime import datetime, time as time1
from dotenv import load_dotenv

load_dotenv()



# =========读取自选股=========
def load_watchlist():
    db_path = r"C:\eastmoney\swc8\config\User\9971113309768870\self_stock.db"

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 查看所有表
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    

    cursor.execute("PRAGMA table_info(selfstock);")
    #print(cursor.fetchall())

    cursor.execute("SELECT group_key,stock_code_arr FROM selfstock where group_key='0_自选股';")
    rows = cursor.fetchall()



    items = rows[0][1].split(",")
    
    result = []
    for x in items:
        if x.strip() == "":
            break
        market, code = x.split(".")
            
        result.append(f"{code}")
    print(result)
    return result

# ========= 自选股 =========
WATCH_LIST = []
WATCH_LIST = load_watchlist()

# ========= 防重复 =========
triggered = set()


# ========= 微信推送 =========
def send_wechat(msg, key):
    url = f"https://sctapi.ftqq.com/{key}.send"
    data = {
        "title": "分时预警",
        "desp": msg
    }
    requests.post(url, data=data)

# ========= 获取分时 =========
def get_minute_data(code):
    try:
        df = ak.stock_zh_a_hist_min_em(
            symbol=code,
            period="1",
            adjust=""
        )
        return df
    except:
        return None

# ========= 分时突破检测 =========
def detect_breakout(df):
    if df is None or len(df) < 20:
        return False

    df = df.copy()

    # 最近20分钟
    recent = df.tail(20)

    high = recent["最高"].max()
    current = recent.iloc[-1]["收盘"]
    print(f"当前价: {current}, 20分钟高点: {high}")
    vol_mean = recent["成交量"].mean()
    vol_now = recent.iloc[-1]["成交量"]

    pct = (current - recent.iloc[0]["收盘"]) / recent.iloc[0]["收盘"] * 100

    # 突破条件
    if (
        current >= high and
        vol_now > vol_mean * 1.5 and
        pct > 2
    ):
        return True

    return False

# ========= 主监控 =========
def monitor():

    print("扫描中...")

    now = datetime.now().time()

    is_trading_time = (
        time1(9, 30) <= now <= time1(11, 30)
        or
        time1(13, 0) <= now <= time1(15, 0)
    )
    if not is_trading_time:
        print("非交易时间，跳过扫描")
        return
    

    for code in WATCH_LIST:
        print(f"检查 {code}...")
        df = get_minute_data(code)
        if df is None:
            continue

        if detect_breakout(df):

            if code in triggered:
                continue

            triggered.add(code)

            price = df.iloc[-1]["收盘"]
            msg = f"""
🚀 分时突破！

代码：{code}
现价：{price}

信号：
- 突破近20分钟高点
- 放量确认

建议：关注是否加速（谨慎追高）
"""

            send_wechat(msg, os.getenv("WECHAT_SCKEY"))
            print(f"{code} 已推送")

# ========= 定时 =========
schedule.every(1).minutes.do(monitor)

print("🚀 分时监控启动...")

while True:
    schedule.run_pending()
    time.sleep(180)