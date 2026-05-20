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

import tushare as ts
from concurrent.futures import ThreadPoolExecutor, as_completed

# =========================
# 环境变量
# =========================
load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
MINI_MAX_API_KEY = os.getenv("MINI_MAX_API_KEY")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_daily")
REPORT_DIR = os.path.join(BASE_DIR, "report_daily")

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
def calc_trend_slope(C):

    ma20 = C.rolling(20).mean()

    if len(ma20.dropna()) < 20:
        return 0

    y = ma20.iloc[-20:].values
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


def calc_structure_score(trend, vola, volume):

    # 趋势标准化
    trend_norm = min(max(trend / 0.05, 0), 1)

    # 波动率（越低越好 → 转成“压缩强度”）
    vola_norm = min(max(vola, 0), 1)

    # 成交量结构
    volume_norm = min(max(volume, 0), 1)

    structure_score = (
        trend_norm * 0.5 +
        volume_norm * 0.3 +
        vola_norm * 0.2
    ) * 100

    return structure_score

def calc_catalyst_score(news_score):

    # news_score: 0~100
    return news_score / 100

def calc_dual_layer_score(df, news_score):

    C = df['close']
    VOL = df['vol']

    # =========================
    # 原始因子
    # =========================
    trend = calc_trend_slope(C)
    vola = calc_volatility_factor(C)
    volume = calc_volume_structure(VOL)

    # =========================
    # Layer 1：结构分
    # =========================
    structure_score = calc_structure_score(
        trend,
        vola,
        volume
    )

    # =========================
    # Layer 2：催化分
    # =========================
    catalyst_score = calc_catalyst_score(news_score)

    # =========================
    # 双层融合（关键）
    # =========================
    final_score = structure_score * (1 + catalyst_score * 0.5)

    return {
        "趋势因子": round(trend, 5),
        "波动压缩": round(vola, 5),
        "量能结构": round(volume, 5),

        "结构分": round(structure_score, 2),
        "催化分": round(catalyst_score * 100, 2),

        "综合评分": round(final_score, 2)
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
#not ST or
    if  ST1:
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

    cond_xh1 = (C.iloc[-1] > highest_close or H.iloc[-1] >H.iloc[-2])

    cond_xh2 = (
        C.iloc[-1] /
        C.iloc[-2]
    ) > 1.01 and  C.iloc[-1] / ma5.iloc[-1] <1.08 and C.iloc[-1] / ma5.iloc[-1] > 0.97

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
# 获取概念板块
# =========================
def get_concepts():

    df = pro.concept()

    return df

# =========================
# 获取概念成分股
# =========================
# =========================
# 概念成分股缓存
# =========================
# =========================
# 本地概念缓存
# =========================
def get_concept_detail(concept_id):

    cache_file = os.path.join(
        "concept_cache",
        f"{concept_id}.csv"
    )

    if not os.path.exists(cache_file):

        return pd.DataFrame()

    try:

        df = pd.read_csv(
            cache_file,
            dtype=str
        )

        return df

    except:

        return pd.DataFrame()
    
# =========================
# 获取行业
# =========================
def get_stock_basic():

    # ========= 缓存文件 =========
    cache_file = os.path.join(
        CACHE_DIR,
        "stock_basic.csv"
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

    print("缓存不存在，开始下载 stock_basic...")

    # ========= 下载数据 =========
    df = pro.stock_basic(
        exchange='',
        list_status='L',
        fields='''
            ts_code,
            name,
            industry
        '''
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
# 主线阶段
# =========================
def detect_stage(pct5, pct10):

    if pct5 > 15:
        return "高潮期"

    if pct5 > 8 and pct10 > 15:
        return "主升期"

    if pct5 > 3:
        return "启动期"

    if pct5 < -3:
        return "退潮期"

    if pct10 > 10 and abs(pct5) < 5:
        return "二波期"

    return "震荡"

# =========================================
# 板块历史缓存目录
# =========================================
SECTOR_HISTORY_DIR = os.path.join(BASE_DIR, "cache_sector")


# =========================================
# 读取板块历史数据
# =========================================
def load_sector_history(sector_name):

    os.makedirs(
        SECTOR_HISTORY_DIR,
        exist_ok=True
    )

    file_path = os.path.join(
        SECTOR_HISTORY_DIR,
        f"{sector_name}.csv"
    )

    if os.path.exists(file_path):

        try:

            df = pd.read_csv(file_path)

            df['trade_date'] = (
                df['trade_date']
                .astype(str)
            )

            return df.sort_values(
                'trade_date'
            )

        except:

            return pd.DataFrame()

    return pd.DataFrame()


# =========================================
# 保存板块历史数据
# =========================================
def save_sector_history(
        sector_name,
        result,
        trade_date
):

    os.makedirs(
        SECTOR_HISTORY_DIR,
        exist_ok=True
    )

    file_path = os.path.join(
        SECTOR_HISTORY_DIR,
        f"{sector_name}.csv"
    )

    # =========================================
    # 新数据
    # =========================================
    new_row = pd.DataFrame([{

        "trade_date": str(trade_date),

        "评分": result["评分"],

        "成交额": result["成交额"],

        "涨停数": result["涨停数"],

        "强势股数": result["强势股数"],

        "上涨占比": result["上涨占比"],

        "龙头强度": result["龙头强度"],

        "当前阶段": result["当前阶段"]

    }])

    # =========================================
    # 已存在缓存
    # =========================================
    if os.path.exists(file_path):

        try:

            old_df = pd.read_csv(file_path)

            old_df['trade_date'] = (
                old_df['trade_date']
                .astype(str)
            )

            # 删除同日期旧数据
            old_df = old_df[
                old_df['trade_date']
                != str(trade_date)
            ]

            df = pd.concat(
                [old_df, new_row],
                ignore_index=True
            )

        except:

            df = new_row

    else:

        df = new_row

    # =========================================
    # 排序
    # =========================================
    df = df.sort_values(
        'trade_date'
    )

    # =========================================
    # 仅保留最近120天
    # =========================================
    df = df.tail(120)

    # =========================================
    # 保存
    # =========================================
    df.to_csv(
        file_path,
        index=False,
        encoding='utf-8-sig'
    )


# =========================================
# 热点持续天数
# 连续评分 >= threshold
# =========================================
def calc_hot_days(
        history_df,
        threshold=80
):

    if history_df.empty:

        return 1

    scores = history_df['评分'].tolist()

    hot_days = 0

    for s in reversed(scores):

        if s >= threshold:

            hot_days += 1

        else:

            break

    return max(hot_days, 1)


# =========================================
# 主线阶段识别
# =========================================
def detect_sector_stage(
        score,
        hot_days,
        zt_count,
        amount_ratio
):

    # 启动期
    if (
        score >= 80 and
        hot_days <= 2 and
        zt_count >= 2
    ):

        return "启动期"

    # 发酵期
    if (
        score >= 100 and
        hot_days <= 5 and
        zt_count >= 5
    ):

        return "发酵期"

    # 主升期
    if (
        score >= 120 and
        hot_days >= 5 and
        amount_ratio >= 1.2
    ):

        return "主升期"

    # 高潮期
    if (
        score >= 160 and
        zt_count >= 10
    ):

        return "高潮期"

    # 二波期
    if (
        hot_days >= 8 and
        amount_ratio >= 1.5 and
        zt_count >= 3
    ):

        return "二波期"

    # 分歧期
    if (
        score >= 70 and
        amount_ratio < 1
    ):

        return "分歧期"

    return "轮动"


# =========================================
# 板块评分（机构趋势增强版）
# =========================================
def calc_sector_score(
        df,
        sector_name="未知板块"
):
    sector_type="concept"
    

    if df.empty:

        return None

    total = len(df)

    if total == 0:

        return None

    # =========================================
    # 数据清洗
    # =========================================
    df = df.copy()

    df = df.replace(
        [np.inf, -np.inf],
        np.nan
    )

    df = df.dropna(
        subset=['pct_chg', 'amount']
    )

    if len(df) == 0:

        return None

    # =========================================
    # 涨停数量
    # =========================================
    zt_count = (
        df['pct_chg'] >= 9.8
    ).sum()

    # =========================================
    # 强势股数量
    # =========================================
    strong_count = (
        df['pct_chg'] >= 5
    ).sum()

    # =========================================
    # 上涨占比
    # =========================================
    up_ratio = (
        (df['pct_chg'] > 0).sum()
        / total
    )

    # =========================================
    # 平均涨幅
    # =========================================
    avg_pct = df['pct_chg'].mean()

    # =========================================
    # 板块成交额
    # =========================================
    amount = df['amount'].sum()

    # =========================================
    # 龙头强度
    # =========================================
    leader_strength = (
        df['pct_chg']
        .nlargest(5)
        .mean()
    )

    # =========================================
    # 创新高占比
    # =========================================
    if (
        'close' in df.columns and
        'high_60' in df.columns
    ):

        new_high_ratio = (
            (
                df['close']
                >= df['high_60']
            ).sum()
            / total
        )

    else:

        new_high_ratio = (
            (df['pct_chg'] >= 7).sum()
            / total
        )

    # =========================================
    # 历史数据
    # =========================================
    history_df = load_sector_history(
        sector_name
    )

    # =========================================
    # 5日平均成交额
    # =========================================
    if (
        not history_df.empty and
        '成交额' in history_df.columns
    ):

        amount_ma5 = (
            history_df['成交额']
            .tail(5)
            .mean()
        )

    else:

        amount_ma5 = amount

    # =========================================
    # 成交额放大率
    # =========================================
    if amount_ma5 > 0:

        amount_ratio = (
            amount / amount_ma5
        )

    else:

        amount_ratio = 1

    amount_ratio = min(
        amount_ratio,
        3
    )

    # =========================================
    # 趋势强度
    # =========================================
    trend_score = (
        avg_pct * 0.15 +
        new_high_ratio * 20
    )

    # =========================================
    # 情绪扩散
    # =========================================

    if 'limit_up_days' in df.columns:

        lb_count = (
            df['limit_up_days'] >= 2
        ).sum()

    else:

        lb_count = 0
    
    emotion_score = (

        np.sqrt(zt_count) * 4.5 +

        np.sqrt(lb_count) * 5 +

        strong_count * 0.18 +

        up_ratio * 8 +

        np.sqrt(max(leader_strength, 0)) * 2
    )

    # =========================================
    # 资金聚焦
    # =========================================
    capital_score = (
        np.log1p(amount) * 0.05 +
        amount_ratio * 0.30
    )

    # =========================================
    # 龙头趋势
    # =========================================
    leader_score = (
        leader_strength * 0.20
    )


    # =========================================
    # 最终评分（乘法模型）
    # =========================================
    score = (
        (1 + trend_score)
        *
        (1 + emotion_score)
        *
        (1 + capital_score)
        *
        (1 + leader_score)
    )

# =========================================
# 板块容量过滤
# 防止小概念霸榜
# =========================================
    stock_count = len(df)

    # 极小概念直接过滤
    if stock_count < 5:

        return None

    # 板块容量系数
    capacity_factor = min(
        stock_count / 20,
        1
    )

    # 小板块衰减
    score *= capacity_factor

    if sector_type == "industry":

        # 机构趋势加权
        score *= 1.2

        # 持续性加权
        score *= (
            1 + hot_days * 0.03
        )

        # 成交额加权
        score *= (
            1 + min(amount_ratio, 2) * 0.05
        )
    # =========================================
    # 热点持续天数
    # =========================================
    hot_days = calc_hot_days(
        history_df
    )

    # =========================================
    # 当前阶段
    # =========================================
    stage = detect_sector_stage(
        score,
        hot_days,
        zt_count,
        amount_ratio
    )

    # =========================================
    # 返回结果
    # =========================================
    result = {

        "评分": round(score, 2),

        "当前阶段": stage,

        "热点持续天数": int(hot_days),

        "平均涨幅": round(avg_pct, 2),

        "涨停数": int(zt_count),

        "强势股数": int(strong_count),

        "上涨占比": round(
            up_ratio * 100,
            1
        ),

        "成交额": round(
            amount,
            1
        ),

        "成交额放大": round(
            amount_ratio,
            2
        ),

        "龙头强度": round(
            leader_strength,
            2
        ),

        "创新高占比": round(
            new_high_ratio * 100,
            1
        )
    }

    # =========================================
    # 保存板块历史数据
    # =========================================
    save_sector_history(
        sector_name,
        result,
        TRADE_DATE
    )

    return result
# =========================
# 板块评分
# =========================
def calc_sector_score1(df):

    if df.empty:

        return None

    total = len(df)

    if total == 0:

        return None

    # =========================
    # 涨停
    # =========================
    zt_count = (
        df['pct_chg'] >= 9.5
    ).sum()

    # =========================
    # 强势股
    # =========================
    strong_count = (
        df['pct_chg'] >= 5
    ).sum()

    # =========================
    # 上涨占比
    # =========================
    up_ratio = (
        (df['pct_chg'] > 0).sum()
        / total
    )

    # =========================
    # 平均涨幅
    # =========================
    avg_pct = df['pct_chg'].mean()

    # =========================
    # 成交额
    # =========================
    amount = df['amount'].sum()

    # =========================
    # 龙头强度
    # =========================
    leader_strength = (
        df['pct_chg']
        .nlargest(5)
        .mean()
    )

    # =========================
    # 综合评分
    # =========================
    # =========================
    # 综合评分
    # =========================
    score = (
        avg_pct * 12 +              # 板块涨幅
        zt_count * 45 +             # 涨停强度
        strong_count * 8 +          # 强势股数量
        up_ratio * 12 +             # 板块赚钱效应
        np.log1p(amount) * 8 +      # 成交额（压缩）
        leader_strength * 15        # 龙头强度
    )

    return {
        "评分": round(score, 2),
        "平均涨幅": round(avg_pct, 2),
        "涨停数": int(zt_count),
        "强势股数": int(strong_count),
        "上涨占比": round(up_ratio * 100, 1),
        "成交额": round(amount, 1),
        "龙头强度": round(leader_strength, 2)
    }

# =========================
# 分析行业板块
# =========================
def analyze_industry(
    daily_df,
    basic_df
):

    print("分析行业板块...")

    result = []

    grouped = basic_df.groupby(
        'industry'
    )

    for industry, stocks in grouped:

        try:

            ts_codes = stocks[
                'ts_code'
            ].tolist()

            sector_df = daily_df[
                daily_df['ts_code'].isin(ts_codes)
            ]

            if sector_df.empty:
                continue

            score_data = calc_sector_score(
                sector_df,
                industry
            )

            if score_data is None:
                continue

            score_data['板块'] = industry
            score_data['类型'] = "行业"

            result.append(score_data)

            #print("行业:", industry)

        except Exception as e:

            print(industry, e)

    return result

# =========================
# 分析概念板块
# =========================
def process_concept(
    concept_row,
    daily_df
):

    try:

        concept_id = concept_row['code']

        concept_name = concept_row['name']
        # =========================
        # 剔除伪概念
        # =========================
        if is_bad_concept(concept_name):

            return None
        
        detail_df = get_concept_detail(
            concept_id
        )

        if detail_df.empty:

            return None

        ts_codes = detail_df[
            'ts_code'
        ].tolist()

        sector_df = daily_df[
            daily_df['ts_code'].isin(ts_codes)
        ]

        if sector_df.empty:

            return None

        score_data = calc_sector_score(
            sector_df,
            concept_name
        )

        if score_data is None:

            return None

        score_data['板块'] = concept_name
        score_data['类型'] = "概念"

        return score_data

    except Exception as e:

        print(concept_row['name'], e)

        return None


# =========================
# 概念黑名单
# =========================
BAD_CONCEPT_KEYWORDS = [

    # 融资融券
    "融资融券",
    "转融券",
    "融券",
    "融资",
    "转融券标的",
    "融券标的股",

    # 沪深港通
    "深股通",
    "沪股通",
    "北交所",
    "陆股通",

    # 指数类
    "标普",
    "标普道琼斯A股",
    "MSCI",
    "中证",
    "上证",
    "深证",
    "沪深300",
    "央视50",
    "上证50",
    "中证500",
    "中证1000",
    "中证2000",

    # ETF
    "ETF",

    # 成分
    "成份",

    # 宽基
    "A股",

    # 风格类
    "低价股",
    "高股息",
    "低市盈率",
    "破净股",
    "年报预增",
    "华为概念",
    "地方国资改革",


    # 地域
    "江苏",
    "浙江",
    "广东",
    "上海",
    "深圳",
    "北京",

    # ST
    "ST",

    # 交易所
    "注册制",

    # 其它杂项
    "昨日连板",
    "昨日涨停",
    "昨日触板",
]
# =========================
# 是否过滤概念
# =========================
def is_bad_concept(name):

    for k in BAD_CONCEPT_KEYWORDS:

        if k in name:

            return True

    return False

# =========================
# 主线分析
# =========================
def analyze_hot_sectors():

    print("\n========================")
    print("开始主线分析(Tushare)")
    print("========================\n")

    # =========================
    # 全市场行情
    # =========================
    daily_df = get_daily_df()

    if daily_df.empty:

        return pd.DataFrame()

    # =========================
    # 股票基础信息
    # =========================
    basic_df = get_stock_basic()

    # =========================
    # 行业分析
    # =========================
    industry_result = analyze_industry(
        daily_df,
        basic_df
    )

    # =========================
    # 概念分析
    # =========================
    print("分析概念板块...")

    concept_df = get_concepts()

    concept_result = []

    with ThreadPoolExecutor(
        max_workers=8
    ) as executor:

        futures = []

        for _, row in concept_df.iterrows():

            futures.append(
                executor.submit(
                    process_concept,
                    row,
                    daily_df
                )
            )

        for future in as_completed(futures):

            try:

                res = future.result()

                if res is not None:

                    concept_result.append(res)

                    #print("概念:",res['板块'])

            except Exception as e:

                print(e)

    # =========================
    # 合并
    # =========================
    all_result = (
        industry_result +
        concept_result
    )

    if len(all_result) == 0:

        return pd.DataFrame()

    sector_df = pd.DataFrame(
        all_result
    )

    # =========================
    # 排序
    # =========================
    sector_df = sector_df.sort_values(
        by='评分',
        ascending=False
    )

    # =========================
    # TOP5
    # =========================
    print("\n========== 最强主线 ==========\n")

    print(
        sector_df.head(5)[
            [
                '板块',
                '类型',
                '评分',
                '涨停数',
                '平均涨幅',
                '成交额',
                '当前阶段'
            ]
        ]
    )

    return sector_df

# =========================
# 获取涨跌停数据（AKShare版）
# =========================
def get_limit_stats():

    try:

        print("开始获取涨跌停数据...")

        # =========================
        # 涨停池
        # =========================
        zt_df = ak.stock_zt_pool_em(
            date=TRADE_DATE
        )

        # =========================
        # 跌停池（兼容老版本）
        # =========================
        try:

            dt_df = ak.stock_zt_pool_dtgc_em(
                date=TRADE_DATE
            )

        except:

            dt_df = pd.DataFrame()
        # =========================
        
        # 涨停股票
        # =========================
        zt_codes = []

        if not zt_df.empty:

            zt_codes = (
                zt_df['代码']
                .astype(str)
                .tolist()
            )

        # =========================
        # 跌停股票
        # =========================
        dt_codes = []

        if not dt_df.empty:

            dt_codes = (
                dt_df['代码']
                .astype(str)
                .tolist()
            )

        # =========================
        # 炸板率
        # =========================
        broken_rate = 0

        if (
            not zt_df.empty and
            '炸板次数' in zt_df.columns
        ):

            broken_count = (
                zt_df['炸板次数']
                .fillna(0)
                .astype(float)
                > 0
            ).sum()

            total = max(
                len(zt_df),
                1
            )

            broken_rate = (
                broken_count / total
            ) * 100

        result = {
            "zt_count": len(zt_codes),
            "dt_count": len(dt_codes),
            "zt_codes": zt_codes,
            "dt_codes": dt_codes,
            "broken_rate": round(
                broken_rate,
                1
            )
        }

        print(
            f"涨停: {result['zt_count']}  "
            f"跌停: {result['dt_count']}  "
            f"炸板率: {result['broken_rate']}%"
        )

        return result

    except Exception as e:

        print("获取涨跌停失败:", e)

        return {
            "zt_count": 0,
            "dt_count": 0,
            "zt_codes": [],
            "dt_codes": [],
            "broken_rate": 0
        }

# =========================
# 连板高度（AKShare版）
# =========================
def calc_max_limit_height():

    try:

        zt_df = ak.stock_zt_pool_em(
            date=TRADE_DATE
        )

        if zt_df.empty:

            return 0

        # =========================
        # 连板数
        # =========================
        if '连板数' in zt_df.columns:

            max_lb = (
                zt_df['连板数']
                .fillna(1)
                .astype(int)
                .max()
            )

            return int(max_lb)

        return 1

    except Exception as e:

        print(e)

        return 0
# =========================
# 大盘情绪分析
# =========================

# =========================
# 获取涨跌停
# =========================
def get_limit_data():

    try:

        limit_df = pro.limit_list_d(
            trade_date=TRADE_DATE
        )

        return limit_df

    except Exception as e:

        print(e)

        return pd.DataFrame()

# =========================
# 获取指数
# =========================
def get_index_data():

    try:

        df = pro.index_daily(
            ts_code='000001.SH',
            start_date='20240101',
            end_date=TRADE_DATE
        )

        return df.sort_values(
            by='trade_date'
        )

    except Exception as e:

        print(e)

        return pd.DataFrame()

# =========================
# 情绪阶段
# =========================
def detect_emotion_stage(score):

    if score >= 85:
        return "高潮"

    if score >= 70:
        return "主升"

    if score >= 55:
        return "修复"

    if score >= 40:
        return "震荡"

    if score >= 25:
        return "退潮"

    return "冰点"

# =========================
# 仓位建议
# =========================
def suggest_position(score):

    if score >= 85:
        return "50%-70%（高潮期谨慎）"

    if score >= 70:
        return "70%-90%"

    if score >= 55:
        return "50%-70%"

    if score >= 40:
        return "30%-50%"

    if score >= 25:
        return "10%-30%"

    return "空仓或试错"

# =========================
# 未来风险预测
# =========================
def predict_market(emotion_score):

    if emotion_score >= 85:

        return (
            "市场已接近高潮，"
            "未来几天可能出现高位分化，"
            "需警惕炸板率上升。"
        )

    if emotion_score >= 70:

        return (
            "主线较强，"
            "市场仍存在持续性，"
            "但需注意局部高低切换。"
        )

    if emotion_score >= 55:

        return (
            "市场处于修复阶段，"
            "部分主线可能继续加强。"
        )

    if emotion_score >= 40:

        return (
            "市场震荡，"
            "题材持续性一般。"
        )

    if emotion_score >= 25:

        return (
            "市场退潮明显，"
            "建议防守。"
        )

    return (
        "市场处于冰点，"
        "等待新主线。"
    )

# =========================
# 大盘情绪分析
# =========================
import numpy as np


# =========================================
# 市场情绪分析（机构实战版）
# =========================================
def analyze_market_emotion(sector_df):

    print("\n========================")
    print("开始分析市场情绪...")
    print("========================\n")

    # =========================================
    # 全市场行情
    # =========================================
    daily_df = get_daily_df()

    if daily_df.empty:

        return {}

    total = len(daily_df)

    # =========================================
    # 涨停跌停
    # =========================================
    limit_data = get_limit_stats()

    zt_count = limit_data['zt_count']

    dt_count = limit_data['dt_count']

    broken_rate = limit_data['broken_rate']

    # =========================================
    # 连板高度
    # =========================================
    max_lb = calc_max_limit_height()

    # =========================================
    # 指数趋势
    # =========================================
    index_df = get_index_data()

    index_score = 0

    if not index_df.empty and len(index_df) >= 20:

        close = index_df['close']

        ma5 = close.rolling(5).mean().iloc[-1]

        ma10 = close.rolling(10).mean().iloc[-1]

        ma20 = close.rolling(20).mean().iloc[-1]

        current = close.iloc[-1]

        # 趋势结构
        trend = 0

        if current > ma5:
            trend += 1

        if ma5 > ma10:
            trend += 1

        if ma10 > ma20:
            trend += 1

        # 最近5日涨幅
        pct5 = (
            current / close.iloc[-5] - 1
        ) * 100

        index_score = (
            trend * 8 +
            pct5 * 1.5
        )

    # =========================================
    # 市场赚钱效应
    # =========================================
    up_ratio = (
        (daily_df['pct_chg'] > 0).sum()
        / total
    )

    strong_ratio = (
        (daily_df['pct_chg'] >= 5).sum()
        / total
    )

    # =========================================
    # 主线强度
    # =========================================
    sector_score = 0

    if not sector_df.empty:

        top5 = sector_df.head(5)

        sector_score = (
            top5['评分'].mean()
        )

        # 压缩量级
        sector_score = np.log1p(
            sector_score
        ) * 8

    # =========================================
    # 涨停情绪
    # =========================================
    # 不直接线性使用
    # 使用压缩函数
    # =========================================
    zt_score = np.log1p(
        zt_count
    ) * 12

    dt_score = np.log1p(
        dt_count
    ) * 10

    # =========================================
    # 连板情绪
    # 龙头高度极其重要
    # =========================================
    if max_lb >= 7:

        lb_score = 25

    elif max_lb >= 5:

        lb_score = 18

    elif max_lb >= 3:

        lb_score = 10

    else:

        lb_score = 3

    # =========================================
    # 炸板率（负反馈核心）
    # =========================================
    # 机构实战中极重要
    # =========================================
    broken_penalty = broken_rate * 0.35

    # =========================================
    # 跌停惩罚（风险释放）
    # =========================================
    if dt_count >= 30:

        risk_penalty = 25

    elif dt_count >= 15:

        risk_penalty = 15

    elif dt_count >= 5:

        risk_penalty = 8

    else:

        risk_penalty = 0

    # =========================================
    # 趋势赚钱效应
    # =========================================
    earning_score = (
        up_ratio * 30 +
        strong_ratio * 120
    )

    # =========================================
    # 最终情绪指数（机构级）
    # =========================================
    emotion_score = (
        20 +                    # 基础分
        zt_score +
        lb_score +
        earning_score +
        sector_score +
        index_score
        - dt_score
        - broken_penalty
        - risk_penalty
    )

    # =========================================
    # 情绪冷却机制
    # 防止长期100分
    # =========================================
    emotion_score = (
        np.tanh(emotion_score / 80)
        * 100
    )

    emotion_score = max(
        0,
        min(100, emotion_score)
    )

    print(
        f"最终情绪分: {emotion_score:.2f}"
    )

    # =========================================
    # 市场阶段
    # =========================================
    stage = detect_emotion_stage(
        emotion_score
    )

    # =========================================
    # 仓位建议
    # =========================================
    position = suggest_position(
        emotion_score
    )

    # =========================================
    # 未来预测
    # =========================================
    prediction = predict_market(
        emotion_score
    )

    market_amount = (
        daily_df['amount']
        .sum()
    )

    market_amount_yi = round(
        market_amount,
        2
    )

    # =========================================
    # 返回结果
    # =========================================
    result = {

        "情绪指数": round(
            emotion_score,
            1
        ),

        "大盘点位": round(
            index_df['close'].iloc[-1],
            2
        ),

        "大盘涨跌幅": round(
            index_df['pct_chg'].iloc[-1],
            2
        ),

        "全市场成交额（亿元）": market_amount_yi,

        "市场阶段": stage,

        "涨停家数": int(zt_count),

        "跌停家数": int(dt_count),

        "连板高度": int(max_lb),

        "炸板率": round(broken_rate, 1),

        "上涨占比": round(
            up_ratio * 100,
            1
        ),

        "强势股占比": round(
            strong_ratio * 100,
            1
        ),

        "主线强度": round(
            sector_score,
            2
        ),

        "指数趋势": round(
            index_score,
            2
        ),

        "仓位建议": position,

        "未来预判": prediction
    }

    # =========================================
    # 输出
    # =========================================
    print("\n========== 市场情绪 ==========\n")

    for k, v in result.items():

        print(f"{k}: {v}")

    return result
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

        "temperature": 0.2
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

    # ========= 生成PDF =========
    with open(
            pdf_file,
            "wb"

    ) as f:

        pisa.CreatePDF(
            html,
            dest=f,
            encoding="utf-8"
        )

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

            if ok and row['total_mv']/10000>=60:

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
    # AI新闻情绪缓存
    # =========================
    result_df = batch_news_sentiment(
        result_df
    )

    # =========================
    # 多因子评分
    # =========================
    factor_list = []

    for idx, row in result_df.iterrows():

        ts_code = row['代码']

        try:

            hist = get_hist_data(ts_code)

            if hist is None:
                continue

            factor = calc_dual_layer_score(

                hist,

                row['新闻情绪']
            )

            factor_list.append(factor)

        except Exception as e:

            print(ts_code, e)

            factor_list.append({

                "趋势因子": 0,

                "波动压缩": 0,

                "量能结构": 0,

                "综合评分": 0
            })

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
            '综合评分',
            '趋势因子',
            '新闻情绪'
        ],

        ascending=False
    )

    print(result_df)

    stock_text = result_df.to_string(index=False)
    

    # =========================板块分析
    sector_df = analyze_hot_sectors()

    # =========================
    # 市场情绪
    # =========================
    emotion_result = analyze_market_emotion(
        sector_df
    )

    emotion_text = ""

    if emotion_result:

        emotion_text = str(emotion_result)

    print(emotion_text)


    if not sector_df.empty:

        print("\n========== 最强主线板块 ==========\n")

        top_sector = sector_df.head(5)

        print(top_sector[
            [
                '板块',
                '类型',
                '评分',
                '涨停数',
                '平均涨幅',
                '成交额'
            ]
        ])

    else:

        top_sector = pd.DataFrame()

    sector_text = ""

    if not top_sector.empty:

        sector_text = top_sector.to_string(index=False)

    
    
    prompt = f"""

当前市场情绪：

{emotion_text}

当前最强主线板块（每个板块给出成交量大趋势好且抗跌的你认为的该板块中军）：

{sector_text}

以下股票是量化模型筛选出的趋势突破候选（输出时带出所有的代码和名称）：

{stock_text}


请对以上每一个股票，搜索截止到{TRADE_DATE}的年报/季报数据、机构研报和资讯公告，进一步分析并筛选：

1、行业景气度高或周期反转向好,个股业绩增长确定性高,属于行业龙头或细分领域领先者，毛利高、技术强、现金流表现良好
2、近三个月内无定增预案、无减持公告、未来半年无解禁压力、机构持股比例高且稳定、无重大诉讼风险、无重大财务风险
3、未来上涨空间大(给出合理的上涨空间预估)
4、剔除涨幅已巨大的个股(如近期已涨幅超过50%或一年内超过200%涨幅的），除非业绩和技术面极其强势且未来空间仍大
5、按综合评分指标排序，给出最终明日机构精选个股列表（2-5只）,给出买点和止损点建议



合并以上分析后输出：
标题：今日复盘及明日机构精选个股（{TRADE_DATE})
内容：
1、大盘情绪(含涨跌停数等几个数据指标)和仓位建议
2、主线板块分析(给出主线龙头和成交量最大趋势最强的中军，并分析主线板块的阶段和持续性，给出数据支撑和逻辑理由）
3、个股分析:以表格方式输出分析后筛选出的股票列表，要求理由清晰且有数据支撑，并给出未来上涨空间预估和技术面分析结论
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

#    send_wechat(
#        report,
#        os.getenv("WECHAT_SCKEY")
#    )
    report_ds = report

##==Minimax===##
    print("\n========== MiniMax ==========\n")
    report = minimax(prompt)
    
    print(report)
  

    with open(
        os.path.join(
            REPORT_DIR,
            f"MiniMax_Self_{TRADE_DATE}.md"
        ),
        "w",
        encoding="utf-8"
    ) as f:

        f.write(report)
    report_mini = report

##==KIMI===##
    print("\n========== KIMI ==========\n")
    report = kimi(prompt)
     

    print(report)
  

    with open(
        os.path.join(
            REPORT_DIR,
            f"KIMI_Self_{TRADE_DATE}.md"
        ),
        "w",
        encoding="utf-8"
    ) as f:

        f.write(report)

    report_kimi = report

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
请仔细阅读以下四份报告，分别来自不同的AI模型，内容都是基于同一份市场数据和个股数据分析得出的。请综合分析这四份报告，找出其中的共识和差异，并以此为基础，输出一个最终的复盘总结和个股推荐。
Deepseek的报告:{report_ds};
Kimi的报告:{report_kimi};
MiniMax的报告:{report_mini};
Doubao的报告:{report_doubao};

输出内容：
标题：每日复盘({TRADE_DATE})
内容(分成三个部分)：
1、大盘情绪(含涨跌停数等几个数据指标)和仓位建议
2、主线板块分析(给出主线龙头和成交量最大趋势最强的中军，并分析主线板块的阶段和持续性，给出数据支撑和逻辑理由）
3、个股分析:
(1)按综合评分输出TOP5个股;
(2)从所有候选股票中筛选输出四个模型一致认可的股票(明日即可买入且上涨空间最大的2-5个股票)，并给出未来上涨空间预估和技术面分析结论（含买点和止损点）;

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