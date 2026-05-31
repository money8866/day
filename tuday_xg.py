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
# 主策略
# =========================
def strategy(df, code):

    if len(df) < 80:
        return False

    C = df['close']

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


    ST1 = (StockName.upper().startswith('ST') or
        StockName.upper().startswith('*ST')) or (code.startswith('1'))

    if ST1:
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

    cond_xh1 = (C.iloc[-1] > highest_close or H.iloc[-1] >H.iloc[-2])

    cond_xh2 = (
        C.iloc[-1] /
        C.iloc[-2]
    ) > 1.03

    XH = cond_xh1 and cond_xh2

    return XH

# =========================
# 主线板块分析（Tushare版）
# =========================

# =========================
# 获取全部股票日线
# =========================
def get_daily_df():

    print("读取全市场行情...")

    df = pro.daily(
        trade_date=TRADE_DATE
    )

    if df.empty:

        return pd.DataFrame()

    # 成交额转亿
    df['amount'] = (
        df['amount'] / 1000
    )

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

    df = pro.stock_basic(
        exchange='',
        list_status='L',
        fields='ts_code,name,industry'
    )

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

# =========================
# 板块评分
# =========================
def calc_sector_score(df):
    
    if df.empty:

        return None

    total = len(df)

    if total == 0:

        return None

    # =========================
    # 涨停
    # =========================
    zt_count = (
        df['pct_chg'] >= 9.8
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
    score = (
        avg_pct * 12 +              # 板块涨幅
        zt_count * 45 +             # 涨停强度
        strong_count * 8 +          # 强势股数量
        up_ratio * 12 +             # 板块赚钱效应
        np.log1p(amount) * 8 +      # 成交额（压缩）
        leader_strength * 15        # 龙头强度
    )

    #print(f"分析板块: 评分: {score:.2f} 平均涨幅: {avg_pct:.2f}% 涨停数: {zt_count} 强势股数: {strong_count} 上涨占比: {up_ratio*100:.1f}% 成交额: {amount:.1f} 龙头强度: {leader_strength:.2f}")
    
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

    

        ts_codes = stocks[
            'ts_code'
        ].tolist()

        sector_df = daily_df[
            daily_df['ts_code'].isin(ts_codes)
        ]

        if sector_df.empty:
            continue
        
        score_data = calc_sector_score(
            sector_df
        )

        if score_data is None:
            continue

        score_data['板块'] = industry
        score_data['类型'] = "行业"

        result.append(score_data)

        #print("行业:", industry)

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
            sector_df
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
    "新股与次新股",


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
                '成交额'
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

    # =========================================
    # 返回结果
    # =========================================
    result = {

        "情绪指数": round(emotion_score, 1),

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

        print(f"[{idx+1}/{total}] {ts_code}")

        try:

            hist = get_hist_data(ts_code)

            if hist is None or len(hist) < 80:
                continue

            ok = strategy(
                hist,
                ts_code
            )

            if ok:

                result.append({
                    '代码': ts_code,
                    '名称': row['name'],
                    '现价': row['close'],
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

    # =========================
    # DeepSeek
    # =========================
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


请进一步分析并筛选：

1、业务增长明确
2、估值合理、无定增预案、无减持、无财务雷
3、更可能成为机构趋势中军
4、未来上涨空间大（给出合理的上涨空间预估）


输出：

1、大盘情绪（含涨跌停数等几个数据指标）和仓位建议
2、主线板块分析
3、量化选股分析：
（1）先输出所有股票列表
（2）输出初筛选后的股票列表，要求理由清晰明确，且符合上述1-4的要求
（3）精选与现价相比上涨空间大的股票，说明量化逻辑理由及买点建议
"""

    report = deepseek(prompt)
    #report = ""
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