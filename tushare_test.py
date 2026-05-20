###===自选复盘 - tushare接口===###

import json
import os
import struct
import markdown2 # type: ignore
import requests
import pandas as pd
import numpy as np
import akshare as ak
import time
import webbrowser
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv
from xhtml2pdf import pisa # type: ignore
import block # type: ignore
import emotion
import tushare as ts
from concurrent.futures import ThreadPoolExecutor, as_completed
import sqlite3

# =========================
# 环境变量
# =========================
load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
MINI_MAX_API_KEY = os.getenv("MINI_MAX_API_KEY")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_daily")
REPORT_DIR = os.path.join(BASE_DIR, "report_daily")
DB_PATH = os.path.join(
    CACHE_DIR,
    "stock_result.db"
)
NEWS_CACHE_DIR = os.path.join(
    BASE_DIR,
    "news_cache"
)

os.makedirs(
    NEWS_CACHE_DIR,
    exist_ok=True
)
# =========================
# Tushare
# =========================
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")

ts.set_token(TUSHARE_TOKEN)

pro = ts.pro_api()


if not os.path.exists(REPORT_DIR):
    os.makedirs(REPORT_DIR)

# =========================
# 通达信目录（修改成你的）
# =========================
TDX_DIR = r"C:\new_tdx"

import pdfkit # type: ignore

WK_PATH = r"C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe"

config = pdfkit.configuration(wkhtmltopdf=WK_PATH)

# =========================
# 最近交易日
# =========================
# =========================
# 获取最近交易日
# =========================

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
#TRADE_DATE = "20260518" # for test

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


def load_stock_dict():

    df = ak.stock_info_a_code_name()

    stock_dict = {}

    for _, row in df.iterrows():

        stock_dict[str(row['code'])] = row['name']

    return stock_dict

STOCK_DICT = load_stock_dict()

# =========================
# 股票名（简单版）
# =========================
def get_stock_name(code):

    return STOCK_DICT.get(code, code)



# ======================================================
# 获取全部股票
# ======================================================
def get_all_stocks():

    df = pro.stock_basic(
        exchange='',
        list_status='L',
        fields='ts_code,symbol,name,industry'
    )

    return df

# ======================================================
# 全市场daily缓存更新（机构级）
# ======================================================
# ======================================================
# 全市场daily缓存（机构级最终版）
# ======================================================
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
    


# =========================
# 趋势斜率（越陡越强）
# =========================
def calc_trend_slope(C, window=20):

    ma20 = C.rolling(window).mean()

    if len(ma20.dropna()) < window:
        return 0

    y = ma20.iloc[-window:].values
    x = np.arange(len(y))

    slope = np.polyfit(x, y, 1)[0]

    # 标准化
    return slope / np.mean(y)

# =========================
# 波动率压缩
# =========================
def calc_volatility_factor(C):

    ret = C.pct_change()

    vol20 = ret.iloc[-20:].std()
    vol60 = ret.iloc[-60:].std()

    if vol60 == 0:
        return 0

    ratio = vol20 / vol60

    return 1 - ratio

# =========================
# 成交量结构
# =========================
def calc_volume_structure(VOL):

    ma5 = VOL.rolling(5).mean().iloc[-1]
    ma20 = VOL.rolling(20).mean().iloc[-1]
    ma60 = VOL.rolling(60).mean().iloc[-1]

    if ma60 == 0:
        return 0

    score = 0

    # 缩量洗盘
    if ma5 < ma20:
        score += 0.4

    # 中期放量
    if ma20 > ma60:
        score += 0.4

    # 均线结构
    score += min(ma20 / ma60, 2) * 0.1

    return score

# =========================
# AI新闻情绪（缓存版）
# 每日只请求一次
# =========================
def get_news_sentiment(
        code,
        name
):

    cache_file = os.path.join(
        NEWS_CACHE_DIR,
        f"{code}_{TRADE_DATE}.json"
    )

    # =========================
    # 优先读取缓存
    # =========================
    if os.path.exists(cache_file):

        try:

            with open(
                cache_file,
                "r",
                encoding="utf-8"
            ) as f:

                data = json.load(f)

            return data["score"]

        except Exception as e:

            print(
                f"{code} 情绪缓存读取失败:",
                e
            )

    # =========================
    # AI分析
    # =========================
    prompt = f"""
请分析A股股票：

{name}（{code}）

最近30天：

1、公告
2、机构研报
3、新闻热点
4、产业趋势
5、业绩预期
6、AI相关催化

判断市场情绪强弱。

返回一个0-100整数：

90-100:
极强利好
机构持续看多

70-89:
明显利好

50-69:
中性偏好

30-49:
偏空

0-29:
明显利空

要求：
1、只返回数字
2、不要解释
"""

    try:

        r = deepseek(prompt)

        # =========================
        # 提取数字
        # =========================
        score_str = ''.join(
            filter(str.isdigit, r)
        )

        if score_str == "":
            score = 50

        else:

            score = int(score_str)

        score = min(
            max(score, 0),
            100
        )

        # =========================
        # 保存缓存
        # =========================
        cache_data = {

            "code": code,

            "name": name,

            "trade_date": TRADE_DATE,

            "score": score,

            "raw": r

        }

        with open(
            cache_file,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                cache_data,
                f,
                ensure_ascii=False,
                indent=2
            )

        print(
            f"AI情绪缓存已保存: {code} -> {score}"
        )

        # 防止API过快
        time.sleep(0.5)

        return score

    except Exception as e:

        print(
            f"{code} AI情绪失败:",
            e
        )

        return 50



# =========================
# 批量AI情绪缓存
# =========================
def batch_news_sentiment(
        result_df
):

    print("\n开始AI新闻情绪分析...\n")

    for idx, row in result_df.iterrows():

        code = row['代码']

        name = row['名称']

        try:

            score = get_news_sentiment(
                code,
                name
            )

            result_df.loc[
                idx,
                '新闻情绪'
            ] = score

            print(
                f"{code} {name} "
                f"情绪={score}"
            )

        except Exception as e:

            print(code, e)

            result_df.loc[
                idx,
                '新闻情绪'
            ] = 50

    return result_df

def calc_trend_slope(close, window=20):

    if len(close) < window:
        return 0

    y = close.tail(window).values
    x = np.arange(window)

    slope = np.polyfit(x, y, 1)[0]

    # 标准化（按价格尺度）
    mean_price = np.mean(y)
    if mean_price == 0:
        return 0

    return slope / mean_price * 100
def calc_trend_stability(close, window=20):

    if len(close) < window:
        return 0

    ret = close.pct_change().tail(window)

    # 越小越稳定
    vol = ret.std()

    if vol == 0:
        return 10

    trend = calc_trend_slope(close, window)

    # 稳定 = 趋势 / 波动
    return trend / (vol * 10 + 1e-6)
def calc_trend_power(close):

    trend_strength = calc_trend_slope(close, 20)
    trend_stability = calc_trend_stability(close, 20)

    trend_power = (
        trend_strength * 0.75 +
        trend_stability * 0.25
    )

    # 非线性放大（关键）
    return np.tanh(trend_power / 5) * 10
def calc_volume_structure(df):

    if len(df) < 30:
        return 0

    C = df['close']
    V = df['vol']

    vol_ratio = V.iloc[-1] / (V.tail(20).mean() + 1e-6)

    price_trend = C.iloc[-1] / C.iloc[-20] - 1

    obv = (np.sign(C.diff()) * V).fillna(0).cumsum()
    obv_strength = obv.iloc[-1] / (abs(obv.tail(20).mean()) + 1e-6)

    return (
        np.log1p(vol_ratio) * 30 +
        np.log1p(abs(obv_strength)) * 30 +
        max(price_trend, 0) * 40
    )
def calc_accumulation_factor(df):

    if len(df) < 40:
        return 0

    C = df['close']
    V = df['vol']

    # 抗跌结构
    price_hold = C.iloc[-10:].min() / C.iloc[-20:-10].max()

    # 缩量
    vol_shrink = V.tail(5).mean() / (V.tail(20).mean() + 1e-6)

    # 稳定抬升
    slope = calc_trend_slope(C, 20)

    score = 0

    if price_hold > 0.92:
        score += 50

    if vol_shrink < 0.8:
        score += 30

    if slope > 0:
        score += 20

    return score
def calc_big_money_factor(df):

    if len(df) < 30:
        return 0

    C = df['close']
    V = df['vol']

    vol_ratio = V.iloc[-1] / (V.tail(20).mean() + 1e-6)

    price_change = C.iloc[-1] / C.iloc[-2] - 1

    money_flow = (C.pct_change() * V).tail(5).sum()

    # 资金持续性（关键升级）
    flow_consistency = np.sum((C.pct_change().tail(5) > 0)) / 5

    return (
        np.log1p(vol_ratio) * 30 +
        max(price_change, 0) * 200 +
        np.log1p(abs(money_flow)) * 20 +
        flow_consistency * 30
    )    

def calc_dual_layer_score_v4(df):

    C = df['close']

    # =========================
    # 趋势核心（权重提高）
    # =========================
    trend_strength = calc_trend_slope(C, 20)
    trend_stability = calc_trend_stability(C, 20)

    trend_power = (
        trend_strength * 0.7 +
        trend_stability * 0.3
    )

    trend_power = np.tanh(trend_power) * 10   # 放大器

    # =========================
    # 量能结构
    # =========================
    volume_structure = calc_volume_structure(df)
    accumulation = calc_accumulation_factor(df)
    big_money = calc_big_money_factor(df)

    # =========================
    # 结构分（不再过度normalize）
    # =========================
    structure_score = (
        trend_strength * 40 +
        trend_stability * 25 +
        volume_structure * 20 +
        accumulation * 15
    )

    # =========================
    # 不再做风险压制（你已移除高位风险）
    # =========================

    # =========================
    # 最终融合（加法结构）
    # =========================
    final_score = (
        structure_score * 0.7 +
        trend_power * 10
    )

    return {
        "趋势强度": round(trend_strength, 3),
        "趋势稳定": round(trend_stability, 3),
        "结构分": round(structure_score, 2),
        "趋势增强": round(trend_power, 2),
        "最终评分": round(final_score, 2)
    }

def obv_new_high(obv, window=120):

    if len(obv) < window:
        return 0

    recent = obv.tail(window)

    # 当前是否等于区间最高
    if recent.iloc[-1] >= recent.max():
        return 1

    return 0

def obv_second_high(obv, window=120, tolerance=0.03):

    if len(obv) < window:
        return 0

    recent = obv.tail(window).values

    # 排序找前两高
    sorted_vals = np.sort(recent)

    if len(sorted_vals) < 2:
        return 0

    second_high = sorted_vals[-2]
    current = recent[-1]

    # 接近第二高（允许3%误差）
    if abs(current - second_high) / (abs(second_high) + 1e-6) <= tolerance:
        return 1

    return 0

def calc_up_down_volume_ratio(
        df,
        n=15
):

    if len(df) < n + 5:
        return None

    C = df['close']
    O = df['open']
    VOL = df['vol']

    # =====================================
    # 阳线成交量
    # =====================================
    up_vol = 0

    # =====================================
    # 阴线成交量
    # =====================================
    down_vol = 0

    # =====================================
    # 统计最近N日
    # =====================================
    for i in range(-n, 0):

        # 阳线
        if C.iloc[i] >= C.iloc[i-1]:

            up_vol += VOL.iloc[i]

        # 阴线
        else:

            down_vol += VOL.iloc[i]

    # 防止除0
    down_vol = max(down_vol, 1)

    # =====================================
    # 阳量 / 阴量
    # =====================================
    ratio = (
        up_vol
        /
        down_vol
    )

    # =====================================
    # 最近5日缩量程度
    # 越小越好
    # =====================================
    recent_vol_ratio = (
        VOL.tail(5).mean()
        /
        VOL.tail(20).mean()
    )

    # =====================================
    # 缩量调整增强
    # =====================================
    shrink_bonus = (
        max(
            0,
            1 - recent_vol_ratio
        )
        * 50
    )

    # =====================================
    # 最终评分
    # =====================================
    score = (

        np.tanh(ratio / 2)
        * 70

        +

        shrink_bonus
    )

    return {

        "阳量": round(up_vol, 2),

        "阴量": round(down_vol, 2),

        "阳阴量比": round(ratio, 2),

        "近期缩量比例": round(
            recent_vol_ratio,
            2
        ),

        "缩量调整分": round(score, 2)
    }

# =========================
# 主策略
# =========================
def strategy(df, code):

    if len(df) < 80:
        return False

    C = df['close']

    O = df['open']

    H = df['high']

    VOL = df['vol']
    
    StockName = get_stock_name(code)

    # =========================
    # 创业板 科创板
    # =========================
    #ST = (
    #    code.startswith('688') or
    ##    code.startswith('300') or
    #    code.startswith('301') 
    #)

    # =========================
    # 创业板 科创板
    # =========================


    ST = (code.startswith('3') or code.startswith('688'))  

    ST1 = (StockName.upper().startswith('ST') or
        StockName.upper().startswith('*ST')) or (code.startswith('1') or (code.startswith('2')))
#
    if  not ST or ST1:
        return False

    # =========================
    # 涨停
    # =========================
    ZT = (
        (C.shift(1) / C.shift(2) < 1.08) &
        (C / C.shift(1) > 1.098) &
        (VOL / VOL.rolling(60).mean() > 1.5)
    )
    ZTTS = barslast(ZT)

    ztts = ZTTS.iloc[-1]

    if np.isnan(ztts):
        return False

    ztts = int(ztts)

    # =========================
    # TJ
    # =========================
    cond1 = ztts > 3 and ztts <= 20

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
    ma5 = C.rolling(5).mean()
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

    cond_xh1 = (C.iloc[-1] > highest_close or (H.iloc[-1] >H.iloc[-2] and H.iloc[-1] > H.iloc[-3]))
    cond_xh2 = C.iloc[-1] / ma5.iloc[-1] <1.08 and C.iloc[-1] / ma5.iloc[-1] > 0.97


    XH = cond_xh1 and cond_xh2
    return XH

# =========================
# 主线板块分析（Tushare版）
# =========================

# =========================
# 获取全部股票日线
# =========================

os.makedirs(CACHE_DIR, exist_ok=True)

def get_daily_df():

    print("读取全市场行情...")

    # ========= 缓存文件 =========
    cache_file = os.path.join(
        CACHE_DIR,
        f"daily_{TRADE_DATE}.csv"
    )

    # ========= 优先读取缓存 =========
    if os.path.exists(cache_file):

        print(f"读取缓存: {cache_file}")

        df = pd.read_csv(
            cache_file,
            dtype={
                'ts_code': str
            }
        )

        return df

    print("缓存不存在，开始从Tushare下载...")

    # ========= 下载数据 =========
    df = pro.daily(
        trade_date=TRADE_DATE
    )

    if df.empty:

        return pd.DataFrame()

    # ========= 成交额转亿 =========
    # tushare amount单位为千元
    # 亿元 = 千元 / 100000
    df['amount'] = (
        df['amount'] / 100000
    )

    # ========= 保存缓存 =========
    df.to_csv(
        cache_file,
        index=False,
        encoding='utf-8-sig'
    )

    print(f"缓存已保存: {cache_file}")

    return df


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

        "temperature": 0.2,
        "extra_body":[{"enable_search": True}]

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
# MiniMax（备用）
# =========================
def minimax(prompt):
    url = "https://api.minimax.chat/v1/text/chatcompletion_v2"

    headers = {
        "Authorization": f"Bearer {MINI_MAX_API_KEY}",
        "Content-Type": "application/json"
    }

    data = {
        "model": "MiniMax-M2.7",
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
        "temperature": 0.2,
        "top_p": 0.5,
        "max_tokens": 40960
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

##== KIMI ==##
def kimi(prompt):

    KIMI_API_KEY = os.getenv("KIMI_API_KEY")
    URL = "https://api.moonshot.cn/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {KIMI_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "kimi-k2.6",
        "messages": [
            {
                "role": "system",
                "content": "你是专业A股机构分析师"
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
                
        
    }

    try:

        response = requests.post(
            URL,
            headers=headers,
            json=payload,
            timeout=600
        )

        data = response.json()

        return data["choices"][0]["message"]["content"]

    except Exception as e:

        print("Kimi接口错误:", e)

        try:
            print(data)
        except:
            pass

        return ""
    
##== 豆包 ==##
def ask_doubao(prompt):
    DOUBAO_API_KEY = os.getenv("DOUBAO_API_KEY")
    URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DOUBAO_API_KEY}"
    }

    payload = {
        # 模型名称
        "model": "doubao-seed-2-0-pro-260215",

        "messages": [
            {
                "role": "system",
                "content": "你是专业A股机构分析师"
            },
            {
                "role": "user",
                "content": prompt
            }
        ],

        # 稳定输出参数
        "temperature": 0.2,
        "top_p": 0.5,
        "max_tokens": 40960
    }
    try:

        response = requests.post(
            URL,
            headers=headers,
            json=payload,
            timeout=600
        )

        data = response.json()

        return data["choices"][0]["message"]["content"]
    except Exception as e:

        print("Doubao接口错误:", e)

        try:
            print(data)
        except:
            pass

        return ""


def send_wechat_message(message, target=None, chat_id=None):
    # QClaw Gateway 地址（根据实际情况调整）
    GATEWAY_URL = "http://localhost:3000" # 或你的 Gateway 地址
    GATEWAY_TOKEN = "31fd9904c07f8c142760e7a03c11fe9e5820da8cfac24d62" # 从 OpenClaw 配置中获取

    headers = {
    "Authorization": f"Bearer {GATEWAY_TOKEN}",
    "Content-Type": "application/json"
    }
    url = f"{GATEWAY_URL}/api/v1/message/send"
    
    payload = {
    "action": "send",
    "channel": "openclaw-weixin", # 或 "wechat-access"
    "message": message
    }
    
    # 如果指定接收人
    if target:
        payload["target"] = target
    
    # 如果发群消息
    if chat_id:
        payload["chatId"] = chat_id
    
    response = requests.post(url, headers=headers, json=payload)
    return response.json()

    # 使用示例


# =========================
# 微信
# =========================
def send_wechat(msg, key):

    url = f"https://sctapi.ftqq.com/{key}.send"

    data = {
        "title": f"每日复盘 - {TRADE_DATE}",
        "desp": msg
    }

    requests.post(url, data=data)




def markdown_to_html_report(
        markdown_text,
        output_file="stock_report.html",
        pdf_file="stock_report.pdf",
        title="AI股票分析报告"
):

    # ========= Markdown 转 HTML =========
    body = markdown2.markdown(
        markdown_text,
        extras=[
            "tables",
            "fenced-code-blocks",
            "strike",
            "task_list"
        ]
    )

    # ========= CSS美化 =========
    html = f"""
<!DOCTYPE html>
<html lang="zh-CN">

<head>
<meta charset="UTF-8">

<title>{title}</title>

<style>

body {{
    background-color: #f5f7fa;
    color: #222;

    font-family:
        "PingFang SC",
        "Microsoft YaHei",
        Arial;

    max-width: 1000px;

    margin: 40px auto;

    padding: 40px;

    background: white;

    border-radius: 16px;

    box-shadow:
        0 4px 20px rgba(0,0,0,0.08);

    line-height: 1.8;
}}

h1 {{
    border-bottom: 3px solid #1677ff;
    padding-bottom: 12px;
    color: #1677ff;
}}

h2 {{
    margin-top: 35px;
    color: #0f172a;
}}

h3 {{
    color: #334155;
}}

table {{
    width: 100%;
    border-collapse: collapse;
    margin-top: 20px;
    margin-bottom: 20px;
}}

th {{
    background: #1677ff;
    color: white;
    padding: 12px;
}}

td {{
    border: 1px solid #dcdfe6;
    padding: 10px;
}}

tr:nth-child(even) {{
    background: #f8fafc;
}}

code {{
    background: #f1f5f9;
    padding: 2px 6px;
    border-radius: 6px;
}}

pre {{
    background: #0f172a;
    color: #f8fafc;

    padding: 20px;

    border-radius: 12px;

    overflow-x: auto;
}}

blockquote {{
    border-left: 5px solid #1677ff;
    padding-left: 15px;
    color: #555;
    background: #f8fafc;
    margin: 20px 0;
}}

ul {{
    padding-left: 25px;
}}

li {{
    margin-bottom: 8px;
}}

strong {{
    color: #d4380d;
}}

</style>
</head>

<body>

{body}

</body>
</html>
"""

    # ========= 保存HTML =========
    with open(
            output_file,
            "w",
            encoding="utf-8"
    ) as f:

        f.write(html)

    print(f"HTML报告已生成: {output_file}")

    # ========= 自动打开浏览器 =========
    webbrowser.open(
        Path(output_file).absolute().as_uri()
    )

    pdfkit.from_string(
        html,
        pdf_file,
        configuration=config
    )
#    html_to_pdf(html,pdf_file)

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

##==========缓存代码
def init_db():

    os.makedirs("cache", exist_ok=True)

    conn = sqlite3.connect(DB_PATH)

    cursor = conn.cursor()

    cursor.execute("""

        CREATE TABLE IF NOT EXISTS stock_result (
            date TEXT,
            rank INTEGER,
            code TEXT,
            name TEXT,
            close REAL,
            amount REAL,
            score REAL
        )

    """)

    conn.commit()

    conn.close()

def save_result(df):

    conn = sqlite3.connect(DB_PATH)

    today = TRADE_DATE

    # 清理当天旧数据（避免重复）
    conn.execute(
        "DELETE FROM stock_result WHERE date=?",
        (today,)
    )

    for i, row in enumerate(df.itertuples()):

        conn.execute("""

            INSERT INTO stock_result
            (date, rank, code, name,close,amount, score)

            VALUES (?, ?, ?, ?, ?, ?, ?)

        """, (

            today,
            i + 1,
            getattr(row, "代码", ""),
            getattr(row, "名称", ""),
            getattr(row, "现价", 0),
            getattr(row, "成交额", ""),
            getattr(row, "最终评分", "")
        ))

    conn.commit()
    conn.close()

def load_history(days=10):

    conn = sqlite3.connect(DB_PATH)

    query = """

        SELECT *
        FROM stock_result
        ORDER BY date DESC, rank ASC

    """

    df = pd.read_sql(query, conn)

    conn.close()

    return df



# =========================
# 主程序
# =========================
def run():

    market = get_market()

    result = []

    total = len(market)

    for idx, row in market.iterrows():

        ts_code = row['ts_code']

        #print(f"[{idx+1}/{total}] {ts_code}")

        try:

            hist = get_hist_data(ts_code)

            if hist is None or len(hist) < 80:
                continue

            ok = strategy(
                hist,
                ts_code
            )

            if ok and row['total_mv']/10000>=80:

                result.append({
                    '代码': ts_code,
                    '名称': row['name'],
                    '现价': row['close'],
                    '涨跌幅': row['pct_chg'],
                    '成交额': row['amount'],
                    '总市值（亿元）': row['total_mv']/10000,
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


    # =========================
    # 多因子评分
    # =========================
    factor_list = []

    for idx, row in result_df.iterrows():

        ts_code = row['代码']


        hist = get_hist_data(ts_code)

        if hist is None:
            continue

        factor = calc_dual_layer_score_v4(
            hist
        )

        factor_list.append(factor)


    # =========================
    # 合并因子
    # =========================
    factor_df = pd.DataFrame(
        factor_list
    )

    result_df = pd.concat(
        [
            result_df.reset_index(drop=True),
            factor_df
        ],
        axis=1
    )

    # =========================
    # 综合排序
    # =========================
    result_df = result_df.sort_values(

        by=[
            '最终评分',
            '趋势强度'
        ],

        ascending=False
    )

    print(result_df)
    init_db()
    save_result(result_df)
    stock_text = result_df.to_string(index=False)
    

    # =========================板块分析
    sector_df = block.analyze_hot_sectors()

    # =========================
    # 市场情绪
    # =========================
    emotion_result = emotion.analyze_market_emotion(
        sector_df
    )

    emotion_text = ""

    if emotion_result:

        emotion_text = str(emotion_result)

    print(emotion_text)


    if not sector_df.empty:

        print("\n========== 最强主线板块 ==========\n")

        top_sector = sector_df.head(20)

        print(top_sector)

    else:

        top_sector = pd.DataFrame()

    sector_text = ""
    if not top_sector.empty:

        sector_text = top_sector.to_string(index=False)

    sector_df_his = block.load_history()
    sector_text_his = sector_df_his.to_string(index=False)


    prompt = f"""

当前市场情绪：

{emotion_text}

当前最强主线列表：

{sector_text}

近10日最强主线列表:

{sector_text_his}

以下股票是量化模型筛选出的趋势突破候选：

{stock_text}


请对以上每一个股票，实时搜索年报/季报数据、机构研报和资讯公告，进一步分析并筛选：

1综合评分较高和属于近期最强和反复活跃的主线板块的个股，结合AI新闻面和市场情绪分析,优选评分高/超短线情绪强的
2个股业绩增长确定性高,属于行业龙头或细分领域领先者，毛利高、技术强、现金流表现良好
3近三个月内无定增预案、无减持公告、未来半年无解禁压力、机构持股比例高且稳定、无重大诉讼风险、无重大财务风险
4属于机构重点关注的个股，未来上涨空间大(给出合理的上涨空间预估)
5剔除涨幅已巨大的个股(如短期翻倍的）
6按综合评分指标排序，给出最终明日机构精选个股列表（2-5只）,给出买点和止损点建议


输出内容：
标题：每日复盘({TRADE_DATE})
内容(分成以下部分)：
1、大盘情绪(含涨跌停数等几个数据指标)和仓位建议
2、今日主线板块和近几日动态变化分析(给出主线龙头和成交量最大趋势最强的中军，并分析主线板块的阶段和持续性，给出数据支撑和逻辑理由）
3、个股分析:输出分析后筛选出的股票列表，要求理由清晰且有数据支撑，并给出买卖点/未来上涨空间预估
4、附上属于主线板块的个股列表(按个股综合评分从高到低排序,显示序号)，并给出每只股票的综合评分和相关主线，供读者参考


"""
    print("\n========== DeepSeek ==========\n")
    report = deepseek(prompt)
    print(report)

    with open(
        os.path.join(
            REPORT_DIR,
            f"Deepseek_Self_{TRADE_DATE}.md"
        ),
        "w",
        encoding="utf-8"
    ) as f:

        f.write(report)

    report_ds = report

##==Doubao==##
    print("\n========== Doubao ==========\n")

    report = ask_doubao(prompt)
    

    print(report)
  

    with open(
        os.path.join(
            REPORT_DIR,
            f"Doubao_Self_{TRADE_DATE}.md"
        ),
        "w",
        encoding="utf-8"
    ) as f:

        f.write(report)
    report_doubao = report

##==Final==##
    print("\n========== Final ==========\n")
    prompt = f"""
请仔细阅读以下两份报告，分别来自不同的AI模型，
内容都是基于同一份市场数据和个股数据分析得出的。
请综合分析互相验证和辩论,以确定性为标准,输出一个最终的复盘总结和个股推荐。
Deepseek的报告:{report_ds};
Doubao的报告:{report_doubao};

输出内容：
标题：每日复盘({TRADE_DATE})
内容(分成以下部分)：
1、大盘情绪(含涨跌停数等几个数据指标)和仓位建议
2、主线板块分析(给出主线龙头和成交量最大趋势最强的中军，并分析主线板块的阶段和持续性，给出数据支撑和逻辑理由）
3、个股分析:输出分析后筛选出的股票列表，要求理由清晰且有数据支撑，并给出买卖点/未来上涨空间预估
4、附上属于主线板块的个股列表(按个股综合评分从高到低排序,显示序号)，并给出每只股票的综合评分和相关主线，供读者参考

格式要求：
1、不要Markdown表格，适合窄屏手机阅读，避免长段落
2、使用卡片式结构，每只股票单独分段，用【股票名+代码】作为小标题,加黑加粗显示,股票分析另起一行
3、输出风格：类似微信公众号/财经博主简报

最后加上“提醒:股市有风险、买股须谨慎;以上分析仅供参考，不构成投资建议。”
"""

    report = deepseek(prompt)
    
    print("\n========== Final Report ==========\n")

    print(report)

    send_wechat(
        report,
        os.getenv("WECHAT_SCKEY")
    )   

    with open(
        os.path.join(
            REPORT_DIR,
            f"Final_Self_{TRADE_DATE}.md"
        ),
        "w",
        encoding="utf-8"
    ) as f:

        f.write(report)

    markdown_to_html_report(report, 
                            output_file=os.path.join(REPORT_DIR, f"Final_Self_{TRADE_DATE}.html"), 
                            pdf_file=os.path.join(REPORT_DIR, f"Final_Self_{TRADE_DATE}.pdf"), 
                            title=f"复盘及精选个股({TRADE_DATE})"
                            )

    #result = send_wechat_message(report)

# =========================
# 启动
# =========================
if __name__ == "__main__":

    run()


