# =========================
# 大盘情绪分析
# =========================

from datetime import datetime, timedelta
from dotenv import load_dotenv
import pandas as pd
import numpy as np
import akshare as ak
import os
import tushare as ts
# ============================================
# 缓存目录
# ============================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_daily")
REPORT_DIR = os.path.join(BASE_DIR, "report_daily")

# =========================
# 环境变量
# =========================
load_dotenv()

# =========================
# Tushare
# =========================
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")

ts.set_token(TUSHARE_TOKEN)

pro = ts.pro_api()

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