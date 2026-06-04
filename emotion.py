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
import block as blk
# ============================================
# 缓存目录
# ============================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_daily")
REPORT_DIR = os.path.join(BASE_DIR, "report_daily")

# =========================
# 环境变量
# =========================
load_dotenv("config/.env")

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
    if now.hour < 15:

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

    # fallback缓存
    fallback = os.path.join(os.path.expanduser("~"), ".qclaw", "workspace", "mystock-reports", f"daily_{TRADE_DATE}.csv")
    if os.path.exists(fallback):
        print(f"读取缓存(fallback): {fallback}")
        return pd.read_csv(fallback, dtype={'ts_code': str})

    print("缓存不存在，开始从Tushare下载...")

    # ========= 下载数据 =========
    df = pro.daily(
        trade_date=TRADE_DATE
    )

    if df.empty:
        # 当天数据未更新，尝试前一交易日
        prev_date = (datetime.strptime(TRADE_DATE, '%Y%m%d') - timedelta(days=1)).strftime('%Y%m%d')
        prev_cache = os.path.join(CACHE_DIR, f"daily_{prev_date}.csv")
        if os.path.exists(prev_cache):
            print(f"当日数据为空，读取前一日缓存: {prev_cache}")
            return pd.read_csv(prev_cache, dtype={'ts_code': str})
        prev_fallback = os.path.join(os.path.expanduser("~"), ".qclaw", "workspace", "mystock-reports", f"daily_{prev_date}.csv")
        if os.path.exists(prev_fallback):
            print(f"当日数据为空，读取前一日缓存(fallback): {prev_fallback}")
            return pd.read_csv(prev_fallback, dtype={'ts_code': str})
        return pd.DataFrame()

    # ========= 成交额转亿 =========
    # tushare amount单位为千元
    # 亿元 = 千元 / 100000
    df['amount'] = (
        df['amount'] / 100000
    )

    # ========= 保存缓存 =========
    try:
        df.to_csv(
            cache_file,
            index=False,
            encoding='utf-8-sig'
        )
        print(f"缓存已保存: {cache_file}")
    except Exception:
        fallback = os.path.join(os.path.expanduser("~"), ".qclaw", "workspace", "mystock-reports", f"daily_{TRADE_DATE}.csv")
        df.to_csv(fallback, index=False, encoding='utf-8-sig')
        print(f"缓存已保存(fallback): {fallback}")

    return df

# =========================
# 获取涨跌停数据（Tushare版）
# 涨停: limit_list_ths(limit_type='涨停池')
# 跌停: limit_list_ths(limit_type='跌停池')
# 覆盖主板10%/科创板创业板20%/ST5%/北交所30%
# =========================
def get_limit_stats():

    try:

        print("开始获取涨跌停数据...")

        zt_codes = []
        dt_codes = []
        broken_rate = 0

        # =========================
        # 涨停: limit_list_ths 涨停池
        # =========================
        try:
            ths_zt = pro.limit_list_ths(trade_date=TRADE_DATE, limit_type='涨停池')
            if ths_zt is not None and not ths_zt.empty:
                zt_codes = ths_zt['ts_code'].astype(str).tolist()
                print(f"涨停(ths涨停池): {len(zt_codes)}只")
        except Exception as e:
            print(f"limit_list_ths涨停失败: {e}")

        # =========================
        # 跌停: limit_list_ths 跌停池
        # =========================
        try:
            ths_dt = pro.limit_list_ths(trade_date=TRADE_DATE, limit_type='跌停池')
            if ths_dt is not None and not ths_dt.empty:
                dt_codes = ths_dt['ts_code'].astype(str).tolist()
                print(f"跌停(ths跌停池): {len(dt_codes)}只")
        except Exception as e:
            print(f"limit_list_ths跌停失败: {e}")

        # =========================
        # 炸板率: 用 limit_list_d U类的 open_times>0 比例
        # =========================
        try:
            limit_df = pro.limit_list_d(trade_date=TRADE_DATE)
            if limit_df is not None and not limit_df.empty:
                zt_u = limit_df[limit_df['limit'] == 'U']
                if not zt_u.empty and 'open_times' in zt_u.columns:
                    total_u = len(zt_u)
                    broken_u = (zt_u['open_times'].fillna(0) > 0).sum()
                    broken_rate = (broken_u / total_u) * 100 if total_u > 0 else 0
                    print(f"炸板率: {broken_rate}%")
                # 兜底: 如果ths涨停没拿到，用list_d的U类
                if not zt_codes:
                    zt_codes = zt_u['ts_code'].astype(str).tolist()
                    print(f"涨停(list_d兜底): {len(zt_codes)}只")
        except Exception as e:
            print(f"limit_list_d失败: {e}")

        # fallback: 两个接口都失败时用daily近似计算
        if not zt_codes and not dt_codes:
            try:
                daily = get_daily_df()
                if not daily.empty:
                    zt_codes = daily[daily['pct_chg'] >= 9.9]['ts_code'].tolist()
                    zt_codes += daily[(daily['ts_code'].str.startswith(('688','300','301'))) & (daily['pct_chg'] >= 19.9)]['ts_code'].tolist()
                    dt_codes = daily[daily['pct_chg'] <= -9.9]['ts_code'].tolist()
                    dt_codes += daily[(daily['ts_code'].str.startswith(('688','300','301'))) & (daily['pct_chg'] <= -19.9)]['ts_code'].tolist()
                    print(f"涨跌停(全fallback): 涨停{len(zt_codes)}, 跌停{len(dt_codes)}")
            except Exception as e:
                print(f"fallback也失败: {e}")

        result = {
            "zt_count": len(zt_codes),
            "dt_count": len(dt_codes),
            "zt_codes": zt_codes,
            "dt_codes": dt_codes,
            "broken_rate": round(broken_rate, 1)
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
# 连板高度（Tushare版）
# =========================
def calc_max_limit_height():

    try:
        zt_df = pro.limit_step(
            trade_date=TRADE_DATE
        )

        if zt_df is None or zt_df.empty:
            return 0

        if 'nums' in zt_df.columns:
            max_lb = (
                zt_df['nums']
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

def analyze_index_environment(index_df):

    if index_df.empty or len(index_df) < 40:

        return {

            "trend_score": 0,

            "risk": "未知",

            "trend": "未知",

            "position_limit": 0.3
        }

    close = index_df["close"]

    current = close.iloc[-1]

    # =====================================================
    # MA
    # =====================================================
    ma5 = close.rolling(5).mean()

    ma10 = close.rolling(10).mean()

    ma20 = close.rolling(20).mean()

    ma30 = close.rolling(30).mean()

    ma5_now = ma5.iloc[-1]

    ma10_now = ma10.iloc[-1]

    ma20_now = ma20.iloc[-1]

    ma30_now = ma30.iloc[-1]

    # =====================================================
    # 均线方向
    # =====================================================
    ma5_slope = ma5.iloc[-1] - ma5.iloc[-3]

    ma10_slope = ma10.iloc[-1] - ma10.iloc[-3]

    ma20_slope = ma20.iloc[-1] - ma20.iloc[-5]

    ma30_slope = ma30.iloc[-1] - ma30.iloc[-5]

    # =====================================================
    # 偏离率
    # =====================================================
    bias20 = (
        (current / ma20_now) - 1
    ) * 100

    # =====================================================
    # 初始化
    # =====================================================
    score = 50

    trend = "震荡"

    position_limit = 0.5

    # =====================================================
    # 多头排列
    # =====================================================
    if (
        current > ma5_now >
        ma10_now > ma20_now
    ):

        score += 25

        trend = "多头主升"

        position_limit = 1.0

    # =====================================================
    # 强势震荡
    # =====================================================
    elif (
        current > ma10_now and
        ma10_now > ma20_now
    ):

        score += 10

        trend = "强势震荡"

        position_limit = 0.8

    # =====================================================
    # 跌破10日
    # =====================================================
    elif current < ma10_now:

        score -= 10

        trend = "短线退潮"

        position_limit = 0.6

    # =====================================================
    # 跌破20日
    # =====================================================
    if current < ma20_now:

        score -= 20

        trend = "中线转弱"

        position_limit = 0.4

    # =====================================================
    # 空头排列（核心）
    # =====================================================
    if (
        current < ma5_now <
        ma10_now < ma20_now
    ):

        score -= 35

        trend = "空头排列"

        position_limit = 0.2

    # =====================================================
    # MA20方向（核心）
    # =====================================================
    if ma20_slope > 0:

        score += 15

    else:

        score -= 20

    # =====================================================
    # MA30方向
    # =====================================================
    if ma30_slope > 0:

        score += 8

    else:

        score -= 10

    # =====================================================
    # MA10方向
    # =====================================================
    if ma10_slope > 0:

        score += 5

    else:

        score -= 5

    # =====================================================
    # MA5方向
    # =====================================================
    if ma5_slope > 0:

        score += 3

    else:

        score -= 3

    # =====================================================
    # 偏离率
    # =====================================================
    if bias20 >= 12:

        score -= 10

    elif bias20 >= 8:

        score -= 5

    elif bias20 <= -10:

        score += 8

    # =====================================================
    # 风险等级
    # =====================================================
    if score >= 70:

        risk = "低风险"

    elif score >= 50:

        risk = "中性"

    elif score >= 35:

        risk = "高风险"

    else:

        risk = "系统风险"

    return {

        "trend_score": round(score, 2),

        "risk": risk,

        "trend": trend,

        "position_limit": position_limit,

        "bias20": round(bias20, 2),

        "ma5": round(ma5_now, 2),

        "ma10": round(ma10_now, 2),

        "ma20": round(ma20_now, 2),

        "ma30": round(ma30_now, 2),

        "ma20_slope": round(ma20_slope, 2)
    }
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

def calc_emotion_position(
    emotion_score
):

    # =====================================================
    # 情绪主导仓位
    # =====================================================
    if emotion_score >= 85:

        return 0.8

    elif emotion_score >= 70:

        return 0.7

    elif emotion_score >= 55:

        return 0.5

    elif emotion_score >= 40:

        return 0.35

    elif emotion_score >= 25:

        return 0.2

    return 0.1

def calc_final_position(

    emotion_score,

    index_env
):

    # =========================================
    # 情绪仓位
    # =========================================
    emotion_pos = calc_emotion_position(
        emotion_score
    )

    # =========================================
    # 趋势限制
    # =========================================
    limit = index_env[
        "position_limit"
    ]

    # =========================================
    # 最终仓位
    # =========================================
    final_pos = min(

        emotion_pos,

        limit
    )

    return {

        "emotion_pos": emotion_pos,

        "limit": limit,

        "final_pos": final_pos
    }

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
    # 指数趋势状态机
# =========================================
# 指数环境
# =========================================
    index_df = get_index_data()

    index_env = analyze_index_environment(
        index_df
    )

    trend_score = index_env["trend_score"]
    

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
# 情绪分（只反映热度）
# =========================================
    emotion_score = (

        20 +

        zt_score +

        lb_score +

        earning_score +

        sector_score * 1.1

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
    # =========================================
    # 趋势否决机制
    # =========================================
    if index_env["trend"] == "空头排列":

        emotion_score *= 0.45

    elif index_env["trend"] == "中线转弱":

        emotion_score *= 0.65

    elif index_env["trend"] == "短线退潮":

        emotion_score *= 0.8

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
    # =========================================
    # 仓位系统
    # =========================================
    position_data = calc_final_position(

        emotion_score,

        index_env
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
        "情绪分": round(emotion_score, 1),

        "趋势分": round(trend_score, 1),

        "指数环境": index_env["trend"],

        "风险等级": index_env["risk"],

        "情绪仓位": f"{int(position_data['emotion_pos']*100)}%",

        "趋势仓位上限": f"{int(position_data['limit']*100)}%",

        "最终建议仓位": f"{int(position_data['final_pos']*100)}%",
        
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



        "MA20方向": index_env["ma20_slope"],

        "20日偏离率": index_env["bias20"],
    }

    # =========================================
    # 输出
    # =========================================
    print("\n========== 市场情绪 ==========\n")

    for k, v in result.items():

        print(f"{k}: {v}")

    return result



# =========================================================
# 运行
# =========================================================
if __name__ == "__main__":
    _, sector_df = blk.analyze_hot_sectors()
    if sector_df is not None and not sector_df.empty:
        sector_df = sector_df.rename(columns={"主题": "name", "综合分": "评分"})
        df = analyze_market_emotion(sector_df)

