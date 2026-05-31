import os
import struct
import requests
import pandas as pd
import numpy as np
import akshare as ak
import time
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

import tushare as ts
from concurrent.futures import ThreadPoolExecutor, as_completed

# =========================
# 环境变量
# =========================
load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_daily")
REPORT_DIR = os.path.join(BASE_DIR, "report_daily")


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

# =========================
# 读取通达信day文件
# =========================
def read_tdx_day(filepath):

    data = []

    with open(filepath, 'rb') as f:

        buf = f.read()

    row_size = 32

    count = len(buf) // row_size

    for i in range(count):

        row = struct.unpack(
            'IIIIIfII',
            buf[i * row_size:(i + 1) * row_size]
        )

        data.append([
            str(row[0]),
            row[1] / 100,
            row[2] / 100,
            row[3] / 100,
            row[4] / 100,
            row[5],
            row[6]
        ])

    df = pd.DataFrame(data, columns=[
        'trade_date',
        'open',
        'high',
        'low',
        'close',
        'amount',
        'vol'
    ])

    return df

# =========================
# 获取全部股票
# =========================
def get_all_stock_files():

    result = []

    for market in ['sh', 'sz']:

        path = Path(TDX_DIR) / "vipdoc" / market / "lday"

        files = list(path.glob("*.day"))

        result.extend(files)

    return result


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

# =========================
# 主策略
# =========================
def strategy(df, code):

    if len(df) < 80:
        return False

    C = df['close']

    H = df['high']

    O = df['open']

    VOL = df['vol']

    StockName = get_stock_name(code)

    # =========================
    # 创业板 科创板
    # =========================


    ST = (code.startswith('3') or code.startswith('688'))  

    ST1 = (StockName.upper().startswith('ST') or
        StockName.upper().startswith('*ST')) or (code.startswith('1') or (code.startswith('2')))

    if not ST or ST1:
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

    cond_xh1 = (C.iloc[-1] > highest_close or (H.iloc[-1] >H.iloc[-2] and H.iloc[-1] >H.iloc[-3]))

    cond_xh2 = (
        C.iloc[-1] /
        C.iloc[-2]
    ) > 1.01 and  (
        C.iloc[-1] / ma5.iloc[-1] <1.08 and C.iloc[-1] / ma5.iloc[-1] > 0.97 and VOL.iloc[-1] / VOL.rolling(5).mean().iloc[-1] > 1
    )

    XH = cond_xh1 and cond_xh2

    return XH

# =========================
# 主线板块分析（Tushare版）
# =========================

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
        df['amount'] / 1000
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
        market_amount/100,
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
        "reasoning_effort": "high",
        "extra_body": {"thinking": {"type": "enabled"}},
        "temperature": 0.1
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
# 微信
# =========================
def send_wechat(msg, key):

    url = f"https://sctapi.ftqq.com/{key}.send"

    data = {
        "title": "每日精选个股",
        "desp": msg
    }

    requests.post(url, data=data)

# =========================
# 主程序
# =========================
def run():

    
    stock_files = get_all_stock_files()

    result = []

    total = len(stock_files)

    for idx, file in enumerate(stock_files):

        try:

            code = file.stem[2:]

            #print(f"[{idx+1}/{total}] {code}")

            df = read_tdx_day(file)

            if len(df) < 80:
                continue

            ok = strategy(df, code)

            if ok:

                last = df.iloc[-1]

                pct = (
                    last['close'] /
                    df.iloc[-2]['close'] - 1
                ) * 100

                result.append({
                    '代码': code,
                    '名称': get_stock_name(code),
                    '现价': round(last['close'], 2),
                    '涨跌幅': round(pct, 2),
                    '成交额': round(last['amount'] / 1e8, 2)
                })

                print("✅ 命中:", code)

        except Exception as e:

            print(file, e)

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
                '成交额',
                '当前阶段'
            ]
        ])

    else:

        top_sector = pd.DataFrame()

    # =========================
    # DeepSeek
    # =========================
    sector_text = ""

    if not top_sector.empty:

        sector_text = top_sector.to_string(index=False)

    stock_text = result_df.to_string(index=False)
    
    prompt = f"""

当前市场情绪（输出给出的几个指标数值）：

{emotion_text}

当前最强主线板块（每个板块给出成交量大趋势好且抗跌的你认为的该板块中军）：

{sector_text}

以下股票是量化模型筛选出的候选池（输出时带出所有的代码和名称）：

{stock_text}


请对以上股票，结合截止到{TRADE_DATE}的数据和资讯，进一步分析并筛选：

1、业务增长确定性高,未来有业绩预告支撑的股票(按2025年报和2026年季报数据及全网信息分析,给出数据支撑和逻辑理由)
2、无定增预案、无减持、无财务雷
3、未来上涨空间大(给出合理的上涨空间预估)


合并以上分析后输出：
1、大盘情绪(含涨跌停数等几个数据指标)和仓位建议
2、主线板块分析(给出主线龙头和成交量最大趋势最强的中军，并分析主线板块的阶段和持续性，给出数据支撑和逻辑理由）
3、个股分析:
a.先输出以上所有股票列表
b.输出分析和初步筛选后的股票列表，要求理由清晰且有数据支撑
c.结合近期走势技术面，精选基本面成长潜力大、技术面上涨空间大的股票，说明逻辑理由及买点建议
"""

    report = deepseek(prompt)

    print("\n========== DeepSeek ==========\n")

    print(report)

    with open(
        os.path.join(
            REPORT_DIR,
            f"Deepseek_Self_{TRADE_DATE}.txt"
        ),
        "w",
        encoding="utf-8"
    ) as f:

        f.write(report)

    send_wechat(
        report,
        os.getenv("WECHAT_SCKEY")
    )

# =========================
# 启动
# =========================
if __name__ == "__main__":

    run()