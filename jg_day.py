# =========================================================
# 机构级主线轮动系统 V2
# 行业 + 主题 + 龙头 + 双创弹性 + ETF联动
# 全缓存版本（适合长期运行）
# =========================================================

import os
import time
import pickle
import numpy as np
import pandas as pd
import tushare as ts

from datetime import datetime
from collections import defaultdict

import os
import time
import random
import pickle
import tushare as ts
import pandas as pd
import json
from dotenv import load_dotenv
import sqlite3


from datetime import datetime, timedelta
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed
)

import numpy as np
from collections import defaultdict

# =========================
# 参数
# =========================
LOOKBACK = 5          # 动量窗口
TOP_K = 10            # 输出主线数量

MIN_STOCKS = 10       # 板块最小股票数

MOMENTUM_W = 0.6
ACC_W = 0.4

##=========== TUshare

load_dotenv()

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")

ts.set_token(TUSHARE_TOKEN)

pro = ts.pro_api()

# ============================================
# Tushare
# ============================================


# ============================================
# 缓存目录
# ============================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_daily")

DB_PATH = os.path.join(CACHE_DIR, "hot_sector.db")

CACHE_DAILY = CACHE_DIR
CACHE_CONCEPT = CACHE_DIR
CACHE_BASIC = CACHE_DIR
CACHE_ETF = CACHE_DIR
REPORT_DIR = os.path.join(BASE_DIR, "report_daily")

for d in [
    CACHE_DAILY,
    CACHE_CONCEPT,
    CACHE_BASIC,
    CACHE_ETF,
    REPORT_DIR
]:
    os.makedirs(d, exist_ok=True)



# =========================================================
# 文件路径
# =========================================================
CONCEPT_LIST_PATH = os.path.join(
    CACHE_DIR,
    "ths_concept_list.csv"
)

CONCEPT_DETAIL_PATH = os.path.join(
    CACHE_DIR,
    "ths_concept_detail.pkl"
)

STOCK_CONCEPT_PATH = os.path.join(
    CACHE_DIR,
    "stock_concept_map.pkl"
)

CONCEPT_STOCK_PATH = os.path.join(
    CACHE_DIR,
    "concept_stock_map.pkl"
)

# =========================================================
# 主题映射（替代概念）
# =========================================================

def load_theme_map():

    file_path = os.path.join(
        BASE_DIR,
        "theme_map.json"
    )

    if not os.path.exists(file_path):

        raise FileNotFoundError(
            f"配置不存在: {file_path}"
        )

    with open(file_path, "r", encoding="utf-8") as f:

        theme_map = json.load(f)

    print("主题配置加载完成")

    return theme_map


THEME_MAP = load_theme_map()



def get_last_trade_date():

    now = datetime.now()

    # =========================
    # 9点前：视为上一自然日
    # =========================
    if now.hour < 9:

        query_date = (now - timedelta(days=1)).strftime('%Y%m%d')

    else:

        query_date = now.strftime('%Y%m%d')

    # =========================
    # 获取交易日历
    # =========================
    cal = pro.trade_cal(
        exchange='',
        start_date='20200101',
        end_date=query_date
    )

    # 只保留开市日
    cal = cal[cal['is_open'] == 1]

    # 最近交易日
    last_trade_date = cal[
        cal['cal_date'] <= query_date
    ]['cal_date'].max()

    return str(last_trade_date)

TRADE_DATE = get_last_trade_date()

#TRADE_DATE = "20260520" # for test




# =========================================================
# pickle工具
# =========================================================
def save_pickle(obj, path):

    with open(path, "wb") as f:
        pickle.dump(obj, f)


def load_pickle(path):

    with open(path, "rb") as f:
        return pickle.load(f)


# =========================================================
# 股票列表
# =========================================================
def get_stock_basic():

    cache_file = os.path.join(
        CACHE_BASIC,
        "stock_basic.pkl"
    )

    if os.path.exists(cache_file):

        return load_pickle(cache_file)

    print("下载股票列表...")

    df = pro.stock_basic(
        exchange='',
        list_status='L',
        fields='ts_code,symbol,name,industry'
    )

    save_pickle(df, cache_file)

    return df


# =========================================================
# 股票日线缓存
# =========================================================
def get_daily_data(ts_code):

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

            # 避免类型不一致
            df['trade_date'] = df['trade_date'].astype(str)

            # 缓存中已存在目标日期
            if (df['trade_date'] == TRADE_DATE).any():

                return df.sort_values('trade_date')

        except Exception as e:

            print(f"{ts_code} 缓存读取失败: {e}")

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


# =========================================================
# ETF缓存
# =========================================================
def get_etf_daily(ts_code):

    cache_file = os.path.join(
        CACHE_ETF,
        f"{ts_code}.pkl"
    )

    if os.path.exists(cache_file):

        try:
            return load_pickle(cache_file)
        except:
            pass

    try:

        df = pro.fund_daily(
            ts_code=ts_code
        )

        if df is None or df.empty:
            return pd.DataFrame()

        df = df.sort_values("trade_date")

        save_pickle(df, cache_file)

        time.sleep(0.03)

        return df

    except Exception as e:

        print(ts_code, e)

        return pd.DataFrame()


# =========================================================
# 获取市场最新数据
# =========================================================
def get_market_daily(stock_df):

    rows = []

    total = len(stock_df)

    for i, row in stock_df.iterrows():

        ts_code = row["ts_code"]

        #print(f"[{i+1}/{total}] {ts_code}")

        df = get_daily_data(ts_code)

        if df.empty:
            continue

        rows.append(df.iloc[-1])

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows)


# =========================================================
# 同花顺概念
# =========================================================
def get_concept_members():

    cache_file = os.path.join(
        CACHE_CONCEPT,
        "stock_concept_map.pkl"
    )

    if os.path.exists(cache_file):

        return load_pickle(cache_file)

    print("下载概念板块...")

    concept_df = pro.ths_index(
        exchange='A',
        type='N'
    )

    stock_map = defaultdict(list)

    total = len(concept_df)

    for i, row in concept_df.iterrows():

        code = row["ts_code"]

        concept_name = row["name"]

        print(f"[{i+1}/{total}] {concept_name}")

        try:

            member_df = pro.ths_member(
                ts_code=code
            )

            if member_df is None or member_df.empty:
                continue

            for _, r in member_df.iterrows():

                stock_map[
                    r["code"]
                ].append(concept_name)

            time.sleep(0.2)

        except Exception as e:

            print(concept_name, e)

    stock_map = {

        k: ";".join(sorted(list(set(v))))

        for k, v in stock_map.items()
    }

    save_pickle(stock_map, cache_file)

    return stock_map


# =========================================================
# 趋势斜率
# =========================================================
def calc_trend_slope(close, window=20):

    if len(close) < window:
        return 0

    y = close.tail(window).values

    x = np.arange(window)

    slope = np.polyfit(x, y, 1)[0]

    mean_price = np.mean(y)

    if mean_price == 0:
        return 0

    return slope / mean_price * 100


# =========================================================
# 最大回撤
# =========================================================
def calc_max_drawdown(close, window=20):

    if len(close) < window:
        return 0

    price = close.tail(window)

    rolling_max = price.cummax()

    drawdown = (
        price - rolling_max
    ) / rolling_max

    return abs(drawdown.min()) * 100


# =========================================================
# 趋势质量
# =========================================================
def calc_trend_quality(close, window=20):

    if len(close) < window:
        return 0

    trend = calc_trend_slope(close, window)

    mdd = calc_max_drawdown(close, window)

    vol = (
        close
        .pct_change()
        .tail(window)
        .std()
        * 100
    )

    return trend / (
        mdd * 0.7
        + vol * 0.3
        + 1e-6
    )


# =========================================================
# 是否双创
# =========================================================
def is_growth_stock(ts_code):

    return (
        ts_code.startswith("300")
        or
        ts_code.startswith("688")
    )


# =========================================================
# 最近大涨日
# =========================================================
def find_big_up_day(df, pct=9.8):

    sub = df[
        df["pct_chg"] >= pct
    ]

    if sub.empty:
        return None

    return sub.iloc[-1]


# =========================================================
# 缩量调整
# =========================================================
def detect_volume_contraction(df):

    if len(df) < 10:
        return False

    recent = df.tail(5)

    recent_vol = recent["vol"].mean()

    prev_vol = (
        df.tail(15)
        .head(10)["vol"]
        .mean()
    )

    vol_ok = recent_vol < prev_vol * 0.8

    high = recent["high"].max()

    low = recent["low"].min()

    retrace = (high - low) / high

    retrace_ok = retrace < 0.12

    return vol_ok and retrace_ok


# =========================================================
# 缩量突破
# =========================================================
def detect_breakout(df):

    if len(df) < 30:
        return False

    big_day = find_big_up_day(df)

    if big_day is None:
        return False

    breakout_price = big_day["close"]

    latest = df.iloc[-1]

    if not detect_volume_contraction(df.iloc[:-1]):
        return False

    price_ok = latest["close"] > breakout_price

    vol_ok = (

        latest["vol"]

        >

        df.tail(5)["vol"].mean()
    )

    return price_ok and vol_ok


# =========================================================
# 龙头评分
# =========================================================
def calc_leader_score(df):

    if len(df) < 30:
        return 0

    close = df["close"]

    trend = calc_trend_quality(close)

    latest = df.iloc[-1]

    score = (

        trend * 0.4

        +

        latest["pct_chg"] * 0.3

        +

        np.log(
            latest["vol"] + 1
        ) * 0.3
    )

    if is_growth_stock(
        latest["ts_code"]
    ):
        score *= 1.2

    return score


# =========================================================
# ETF评分
# =========================================================
def calc_etf_score(df):

    if len(df) < 20:
        return 0

    close = df["close"]

    trend = calc_trend_quality(close)

    latest = df.iloc[-1]

    pct = latest["pct_chg"]

    vol_ratio = (

        latest["vol"]

        /

        (
            df.tail(5)["vol"].mean()
            + 1e-6
        )
    )

    score = (

        trend * 0.4

        +

        pct * 0.3

        +

        vol_ratio * 10 * 0.3
    )

    return score


# =========================================================
# ETF共振
# =========================================================
def calc_theme_etf_score(theme):

    theme_info = THEME_MAP.get(theme, {})

    etfs = theme_info.get("etf", [])

    if not etfs:
        return 0

    scores = []

    for etf in etfs:

        df = get_etf_daily(etf)

        if df.empty:
            continue

        scores.append(
            calc_etf_score(df)
        )

    if not scores:
        return 0

    return np.mean(scores)


# =========================================================
# 市场情绪
# =========================================================
def calc_market_emotion(df):

    up_limit = (
        df["pct_chg"] >= 9.5
    ).sum()

    down_limit = (
        df["pct_chg"] <= -9.5
    ).sum()

    strong = (
        df["pct_chg"] >= 5
    ).sum()

    weak = (
        df["pct_chg"] <= -5
    ).sum()

    score = (

        up_limit * 5

        +

        strong

        -

        weak

        -

        down_limit * 5
    )

    return score


def calc_sector_score(df):

    if df is None or df.empty:
        return 0

    # =========================
    # 基础数据
    # =========================
    ret = df["pct_chg"].fillna(0)
    vol = df.get("vol", pd.Series(np.ones(len(df))))  # 防止没有vol
    amount = df.get("amount", pd.Series(np.ones(len(df))))

    # =========================
    # 1️⃣ 趋势强度（均值 + 中位数）
    # =========================
    mean_ret = ret.mean()
    median_ret = ret.median()

    trend_score = 0.6 * mean_ret + 0.4 * median_ret

    # =========================
    # 2️⃣ 上涨结构（机构更看“结构”）
    # =========================
    up_ratio = (ret > 0).mean()
    strong_up_ratio = (ret >= 5).mean()
    limit_up_ratio = (ret >= 9.5).mean()

    structure_score = (
        up_ratio * 2
        + strong_up_ratio * 4
        + limit_up_ratio * 8
    )

    # =========================
    # 3️⃣ 资金强度（放量才算数）
    # =========================
    if vol.sum() > 0:
        vol_ratio = vol / vol.mean()
        volume_score = (vol_ratio * (ret > 0)).mean()
    else:
        volume_score = 0

    if amount.sum() > 0:
        amount_score = np.log1p(amount).mean()
    else:
        amount_score = 0

    money_score = volume_score * 0.6 + amount_score * 0.4

    # =========================
    # 4️⃣ 波动质量（机构避免“乱冲”）
    # =========================
    volat = ret.std()

    stability_score = 1 / (1 + volat)

    # =========================
    # 5️⃣ 龙头集中度（关键机构因子）
    # =========================
    max_ret = ret.max()
    leader_gap = max_ret - mean_ret

    concentration_score = max_ret * 0.5 + leader_gap * 0.5

    # =========================
    # 6️⃣ 最终合成（机构权重）
    # =========================
    score = (
        trend_score * 25 +
        structure_score * 25 +
        money_score * 20 +
        stability_score * 15 +
        concentration_score * 15
    )

    return float(score)

# =========================================================
# 主线分析
# =========================================================
def analyze_themes(
    daily_df,
    stock_df
):

    result = []

    for theme, cfg in THEME_MAP.items():

        industry_mask = stock_df.apply(

            lambda x:

                str(
                    x.get("industry", "")
                ) in cfg["industry"],

            axis=1
        )

        keyword_mask = stock_df.apply(

            lambda x:

                any(

                    kw in str(
                        x.get("concept", "")
                    )

                    for kw in cfg["keywords"]
                ),

            axis=1
        )

        mask = (
            industry_mask
            |
            keyword_mask
        )

        sub = stock_df[mask]

        stocks = sub[
            "ts_code"
        ].dropna().unique().tolist()

        if len(stocks) < 5:
            continue

        theme_df = daily_df[
            daily_df["ts_code"].isin(stocks)
        ]

        if theme_df.empty:
            continue

        sector_score = calc_sector_score(
            theme_df
        )

        momentum = (
            theme_df["pct_chg"].mean()
        )

        limit_num = (
            theme_df["pct_chg"] >= 9.5
        ).sum()

        etf_score = calc_theme_etf_score(
            theme
        )

        leaders = []

        elastic = []

        for ts_code in stocks:

            stock_k = get_daily_data(
                ts_code
            )

            if stock_k.empty:
                continue

            leader_score = calc_leader_score(
                stock_k
            )

            leaders.append({

                "ts_code": ts_code,

                "score": leader_score
            })

            if is_growth_stock(ts_code):

                if detect_breakout(stock_k):

                    elastic.append({

                        "ts_code": ts_code,

                        "score": leader_score
                    })

        leaders = sorted(
            leaders,
            key=lambda x: x["score"],
            reverse=True
        )

        elastic = sorted(
            elastic,
            key=lambda x: x["score"],
            reverse=True
        )

        leader_code = ""

        if leaders:
            leader_code = leaders[0]["ts_code"]

        elastic_codes = [
            x["ts_code"]
            for x in elastic[:5]
        ]

        final_score = (

            sector_score * 0.35

            +

            momentum * 0.25

            +

            limit_num * 0.20

            +

            etf_score * 0.20
        )

        result.append({

            "主线": theme,

            "主线评分": round(final_score, 2),

            "ETF强度": round(etf_score, 2),

            "涨停数": int(limit_num),

            "成分股数": len(stocks),

            "龙头": leader_code,

            "弹性双创": ";".join(
                elastic_codes
            )
        })

    result = pd.DataFrame(result)

    if not result.empty:

        result = result.sort_values(
            "主线评分",
            ascending=False
        )

    return result


# =========================================================
# 主程序
# =========================================================
def main():

    print("=" * 60)
    print("机构级主线轮动系统 V2")
    print("=" * 60)

    # 股票列表
    stock_df = get_stock_basic()

    # 概念缓存
    stock_concept_map = get_concept_members()

    stock_df["concept"] = stock_df[
        "ts_code"
    ].map(stock_concept_map)

    # 市场数据
    daily_df = get_market_daily(
        stock_df
    )

    # 情绪
    emotion = calc_market_emotion(
        daily_df
    )

    print(f"\n市场情绪分: {emotion}")

    # 主线分析
    theme_df = analyze_themes(
        daily_df,
        stock_df
    )

    print("\n主线轮动:")
    print(theme_df)

    # 保存
    report_file = os.path.join(

        REPORT_DIR,

        f"mainline_{TRADE_DATE}.csv"
    )

    theme_df.to_csv(
        report_file,
        index=False,
        encoding="utf-8-sig"
    )

    print(f"\n已保存: {report_file}")


# =========================================================
# 启动
# =========================================================
if __name__ == "__main__":

    main()