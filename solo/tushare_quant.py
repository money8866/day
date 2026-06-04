###===自选复盘 - tushare接口===###

import json
import os
import struct
import sys

# =========================
# 终极方案：patch os.path.expanduser，不让 tushare 访问用户根目录
# 必须在导入 tushare 之前执行！
# =========================
original_expanduser = os.path.expanduser
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
safe_cache_dir = os.path.join(BASE_DIR, 'cache_daily')
os.makedirs(safe_cache_dir, exist_ok=True)

def safe_expanduser(path):
    if 'tk.csv' in str(path):
        return os.path.join(safe_cache_dir, 'tk.csv')
    return original_expanduser(path)

os.path.expanduser = safe_expanduser
print(f"[修复] os.path.expanduser 已打补丁，避免 tk.csv 权限问题")

# 现在可以安全导入 tushare 了
import markdown2 # type: ignore
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
import sqlite3

# 新增：引用新版大盘/主题分析
import daily_analysis_summarizer as das

# =========================
# 环境变量
# =========================
load_dotenv("d:/mystock/config/.env")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
MINI_MAX_API_KEY = os.getenv("MINI_MAX_API_KEY")
QWEN_API_KEY = os.getenv("QWEN_API_KEY")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

PARENT_DIR = os.path.dirname(BASE_DIR)
CACHE_DIR = os.path.join(PARENT_DIR, "cache_daily")
REPORT_DIR = os.path.join(PARENT_DIR, 'report_daily')
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
# 主题个股池路径
# =========================
THEME_STOCKS_CACHE = os.path.join(BASE_DIR, 'cache_backbone_tushare', 'theme_pattern_stocks.csv')

def load_theme_pattern_stocks():
    """读取主题选股结果"""
    if not os.path.exists(THEME_STOCKS_CACHE):
        return [], ""
    
    try:
        df = pd.read_csv(THEME_STOCKS_CACHE, encoding='utf-8-sig')
        if df.empty:
            return [], ""
        
        # 按主题类型和买入类型分组
        mid_term = df[df.get('theme_type', '') == '中期趋势']
        short_term = df[df.get('theme_type', '') == '短线主线']
        
        # 中期趋势：中军和补涨中军
        mid_term_zhongjun = mid_term[mid_term.get('buy_type', '') == '中军']
        mid_term_buzhang = mid_term[mid_term.get('buy_type', '') == '补涨中军']
        
        # 短线主线：中军和补涨中军
        short_term_zhongjun = short_term[short_term.get('buy_type', '') == '中军']
        short_term_buzhang = short_term[short_term.get('buy_type', '') == '补涨中军']
        
        # 生成文本格式
        lines = []
        lines.append("=" * 80)
        lines.append("主题个股池选股结果")
        lines.append("=" * 80)
        
        # 中期趋势主题
        if not mid_term.empty:
            lines.append("\n📈 中期趋势主题（60日趋势平均分TOP2）")
            lines.append("-" * 80)
            
            if not mid_term_zhongjun.empty:
                lines.append("🏆 中军（中线布局）")
                for _, row in mid_term_zhongjun.iterrows():
                    mcap = f"{row.get('mcap', 0):.1f}亿" if pd.notna(row.get('mcap')) else "--"
                    lines.append(f"  {row['code']} {row['name']} | 主题:{row.get('theme', '')} | "
                               f"现价:{row.get('price', 0):.2f} | 涨跌:{row.get('pct_chg', 0):+.2f}% | "
                               f"换手:{row.get('turnover', 0):.2f}% | 市值:{mcap}")
                    lines.append(f"    推荐理由: {row.get('reason', '')}")
            
            if not mid_term_buzhang.empty:
                lines.append("📈 补涨中军（成交活跃+均线金叉）")
                for _, row in mid_term_buzhang.iterrows():
                    mcap = f"{row.get('mcap', 0):.1f}亿" if pd.notna(row.get('mcap')) else "--"
                    lines.append(f"  {row['code']} {row['name']} | 主题:{row.get('theme', '')} | "
                               f"现价:{row.get('price', 0):.2f} | 涨跌:{row.get('pct_chg', 0):+.2f}% | "
                               f"换手:{row.get('turnover', 0):.2f}% | 市值:{mcap}")
                    lines.append(f"    推荐理由: {row.get('reason', '')}")
        
        # 短线主线主题
        if not short_term.empty:
            lines.append("\n⚡ 短线主线（当日最强主线TOP3）")
            lines.append("-" * 80)
            
            if not short_term_zhongjun.empty:
                lines.append("🏆 中军（短线跟随）")
                for _, row in short_term_zhongjun.iterrows():
                    mcap = f"{row.get('mcap', 0):.1f}亿" if pd.notna(row.get('mcap')) else "--"
                    lines.append(f"  {row['code']} {row['name']} | 主题:{row.get('theme', '')} | "
                               f"现价:{row.get('price', 0):.2f} | 涨跌:{row.get('pct_chg', 0):+.2f}% | "
                               f"换手:{row.get('turnover', 0):.2f}% | 市值:{mcap}")
                    lines.append(f"    推荐理由: {row.get('reason', '')}")
            
            if not short_term_buzhang.empty:
                lines.append("📈 补涨中军（成交活跃+均线金叉）")
                for _, row in short_term_buzhang.iterrows():
                    mcap = f"{row.get('mcap', 0):.1f}亿" if pd.notna(row.get('mcap')) else "--"
                    lines.append(f"  {row['code']} {row['name']} | 主题:{row.get('theme', '')} | "
                               f"现价:{row.get('price', 0):.2f} | 涨跌:{row.get('pct_chg', 0):+.2f}% | "
                               f"换手:{row.get('turnover', 0):.2f}% | 市值:{mcap}")
                    lines.append(f"    推荐理由: {row.get('reason', '')}")
        
        lines.append("=" * 80)
        
        return df.to_dict('records'), "\n".join(lines)
    except Exception as e:
        print(f"读取主题个股池失败: {e}")
        return [], ""

# =========================
# Tushare
# =========================
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")

# 尝试安全设置 tushare token
pro = None
try:
    ts.set_token(TUSHARE_TOKEN)
    pro = ts.pro_api()
except Exception as e:
    print(f"Token 设置失败，使用模拟模式: {e}")
    pro = None


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
    if now.hour < 15:

        query_date = (now - timedelta(days=1)).strftime('%Y%m%d')

    else:

        query_date = now.strftime('%Y%m%d')

    # =========================
    # 如果没有 tushare，直接返回模拟日期
    # =========================
    if pro is None:
        # 返回一个固定的模拟日期
        return "20260603"

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
#TRADE_DATE = "20260529" # for test

print("当前交易日1:", TRADE_DATE)
# =========================
# BARSLAST
# =========================
def barslast(series):

    result = []

    last_true = -1

    for i, val in enumerate(series):

        if val:

            if i == 0:

                # 第1天符合条件的不纳入结果，返回 NaN，不更新 last_true

                result.append(np.nan)

            else:

                # 更新 last_true 并返回 0

                last_true = i

                result.append(0)

        else:

            if last_true == -1:
                result.append(np.nan)

            else:
                result.append(i - last_true)

    return pd.Series(result, index=series.index)


def load_stock_dict():
    try:
        df = ak.stock_info_a_code_name()
        stock_dict = {}
        for _, row in df.iterrows():
            stock_dict[str(row['code'])] = row['name']
        return stock_dict
    except Exception as e:
        print(f"[模拟] akshare 不可用，使用模拟股票字典: {e}")
        return {
            '000001': '平安银行',
            '600000': '浦发银行',
            '000002': '万科A',
            '600519': '贵州茅台',
            '300750': '宁德时代',
            '000001.SZ': '平安银行',
            '600000.SH': '浦发银行',
            '000002.SZ': '万科A',
            '600519.SH': '贵州茅台',
            '300750.SZ': '宁德时代'
        }

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

    # 如果没有 tushare API，返回模拟数据
    if pro is None:
        print("[模拟] 使用模拟股票列表...")
        mock_data = {
            'ts_code': ['000001.SZ', '600000.SH', '000002.SZ', '600519.SH', '300750.SZ'],
            'symbol': ['000001', '600000', '000002', '600519', '300750'],
            'name': ['平安银行', '浦发银行', '万科A', '贵州茅台', '宁德时代'],
            'industry': ['银行', '银行', '房地产', '食品饮料', '新能源']
        }
        return pd.DataFrame(mock_data)

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
                # 只返回 TRADE_DATE 及之前的数据
                filtered_df = df[df['trade_date'] <= TRADE_DATE]
                return filtered_df.sort_values('trade_date')

        except Exception as e:

            print(f"{ts_code} 缓存读取失败: {e}")

    # =========================
    # 如果没有API，生成模拟历史数据
    # =========================
    if pro is None:
        print(f"[模拟] 为 {ts_code} 生成模拟历史数据...")
        # 生成过去100个交易日的模拟数据
        dates = []
        base_date = datetime.strptime(TRADE_DATE, '%Y%m%d')
        for i in range(100):
            dt = base_date - timedelta(days=i)
            dates.append(dt.strftime('%Y%m%d'))
        
        dates.reverse()
        # 生成模拟价格
        import random
        close = 10.0
        data = []
        for date in dates:
            change = random.uniform(-0.03, 0.03)
            close = close * (1 + change)
            high = close * random.uniform(1.01, 1.04)
            low = close * random.uniform(0.96, 0.99)
            open_p = close * random.uniform(0.98, 1.02)
            vol = random.randint(1000000, 10000000)
            amount = vol * close
            data.append({
                'ts_code': ts_code,
                'trade_date': date,
                'open': open_p,
                'high': high,
                'low': low,
                'close': close,
                'vol': vol,
                'amount': amount,
                'pct_chg': change * 100
            })
        df = pd.DataFrame(data)
        df.to_csv(cache_file, index=False)
        return df

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
        time.sleep(0.02)

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
    # 加大成交量相关权重
    structure_score = (
        trend_strength * 30 +
        trend_stability * 20 +
        volume_structure * 30 +
        accumulation * 20
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
def strategy(df, code, emotion_stage):

    if len(df) < 80:
        return False

    C = df['close']

    O = df['open']

    H = df['high']
    L = df['low']
    
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
    if  ST1:
        return False

    # =========================
    # 过热过滤：两个月涨幅 > 100% 剔除
    # =========================
    if len(df) >= 40:
        ret_2m = df['close'].iloc[-1] / df['close'].iloc[-40] - 1
        if ret_2m > 1.0:
            return False

    # =========================
    # 启动过滤：必须是“相对新启动结构”
    # =========================
    if len(df) >= 60:
        hh = df['high'].iloc[-60:].max()
        ll = df['low'].iloc[-60:].min()
        range_ratio = (hh / ll - 1)

        # 如果60日振幅过大（已走过主升），过滤
        if range_ratio > 1.8:
            return False
        
    # =========================
    # 启动确认（趋势初期）
    # =========================
    ma20 = C.rolling(20).mean()
    ma60 = C.rolling(60).mean()

    # 必须处于均线修复或突破初期
    if C.iloc[-1] < ma20.iloc[-1] * 1.2:
        pass
    else:
        # 允许突破，但不能过度偏离
        if C.iloc[-1] / ma60.iloc[-1] > 1.5:
            return False
    # =========================
    # 涨停
    # =========================
    ZT = (
        (C.shift(1) / C.shift(2) < 1.08) &
        (C / C.shift(1) > 1.098) 
    )
    ZTTS = barslast(ZT)

    ztts = ZTTS.iloc[-1]
    if ztts ==0:
        ztts = ZTTS.iloc[-2]

    if np.isnan(ztts):
        return False

    ztts = int(ztts)

    # =========================
    # TJ
    # =========================
    cond1 = ztts >= 2 and ztts <= 30

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
    ) < 1.2

    cond5 = (
        H.iloc[-ztts:].max() >=
        H.iloc[-120:].max() * 0.9
    )

    ma22 = C.rolling(22).mean()
    ma5 = C.rolling(5).mean()
    cond6 = (
        ma22.iloc[-1] >=
        ma22.iloc[-2]
    )

    # =========================
    # ZTTS 量能结构增强条件
    # =========================

    ztts_window = recent_close.index  # 或直接用 df tail

    ztts_df = df.iloc[-ztts:]  # ZTTS区间数据

    VOL_ma = ztts_df['vol'].rolling(5).mean()

    # 1. 缩量：低于均量 70%
    cond_low_vol = (ztts_df['vol'] < VOL_ma * 0.9).any()

    # 2. 温和放量：1.1~1.8倍均量
    cond_mid_vol = (
        (ztts_df['vol'] > VOL_ma * 1.1) &
        (ztts_df['vol'] < VOL_ma * 1.8)
    ).any()

    # 3. 回撤不超过10%
    # =========================
    # 回撤
    # =========================
    cum_max = ztts_df['close'].cummax()

    drawdown = (
        (ztts_df['close'] - cum_max)
        / cum_max
    )

    max_dd = drawdown.min()

    # 最大回撤不超过10%
    cond_dd = max_dd >= -0.15


    # =========================
    # 无放量下跌K线
    # =========================

    vol_ma5 = ztts_df['vol'].rolling(5).mean()

    # 阴线
    down_k = (
        ztts_df['close'] < ztts_df['open']
    )

    # 放量
    big_vol = (
        ztts_df['vol'] > vol_ma5 * 1.5
    )

    # 大跌
    big_drop = (
        ztts_df['pct_chg'] < -5
    )

    # 放量大跌阴线
    bad_k = (
        down_k &
        big_vol &
        big_drop
    )

    # 不允许出现
    cond_no_bad_k = ~bad_k.any()


    # 必须同时满足
    cond7 = cond_low_vol and cond_no_bad_k
    
    TJ = (
        cond1 and
        cond3 and
        cond4 and
        cond5 and
        cond6 and
        cond7
    )

    if not TJ:
        return False
    

    # =========================
    # XH
    # =========================
    highest_close = (
        C.iloc[-ztts-1:-1].max()
    )

    highest_vol = (
        VOL.iloc[-ztts-1:-1].max()
    )

    #cond_xh1 = (C.iloc[-1] > highest_close or (H.iloc[-1] >H.iloc[-2] and H.iloc[-1] > H.iloc[-3]))
    cond_xh1 = (C.iloc[-1] > highest_close)
    cond_xh2 = C.iloc[-1] / ma5.iloc[-1] <1.15 and C.iloc[-1] / ma5.iloc[-1] > 0.95
    cond_xh3 = C.iloc[-2] > highest_close or C.iloc[-3] > highest_close or C.iloc[-1] > C.iloc[-ztts-1]
    cond_xh4 = C.iloc[-1] / C.iloc[-2]<0.99 and C.iloc[-1] > ma5.iloc[-1] * 0.95 and VOL.iloc[-1] < VOL.iloc[-2]
    
    XH = (cond_xh1 and cond_xh2) 
    
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

from openai import OpenAI
##== 千问 ==##
def ask_qwen(prompt):
    try:
        client = OpenAI(
            api_key=QWEN_API_KEY,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
        )

        completion = client.chat.completions.create(
            model="qwen3-max",
            messages=[
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
            temperature=0.2,
            top_p=0.5,
            max_tokens=40960
        )

        if completion and completion.choices and len(completion.choices) > 0:
            message = completion.choices[0].message
            if message and hasattr(message, 'content'):
                return message.content
        print("千问接口返回数据格式异常")
        return ""
    except Exception as e:
        print(f"千问接口错误: {str(e)}")
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
    import re
    # 清理HTML标签（Server酱不支持HTML）
    msg = re.sub(r'<[^>]+>', '', msg)
    msg = msg.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')

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



# =========================
# 市场数据（带缓存）
# =========================
def get_market():

    cache_file = os.path.join(
        CACHE_DIR,
        f"market_{TRADE_DATE}.csv"
    )

    if os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file)
            print(f"[缓存] 市场数据已加载: {cache_file}")
            return df
        except Exception as e:
            print(f"[缓存] 市场数据读取失败: {e}")

    # 如果没有 tushare API 可用或缓存缺失，生成模拟数据用于测试
    if pro is None:
        print("[模拟] 生成模拟市场数据用于测试...")
        mock_data = {
            'ts_code': ['000001.SZ', '600000.SH', '000002.SZ', '600519.SH', '300750.SZ'],
            'name': ['平安银行', '浦发银行', '万科A', '贵州茅台', '宁德时代'],
            'close': [10.5, 8.2, 25.3, 1800.0, 120.0],
            'pct_chg': [1.5, -0.8, 2.3, 0.5, 3.2],
            'amount': [500000, 300000, 800000, 1200000, 2000000],
            'total_mv': [15000000, 12000000, 8000000, 25000000, 18000000]
        }
        df = pd.DataFrame(mock_data)
        df.to_csv(cache_file, index=False)
        return df

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

    df.to_csv(cache_file, index=False)
    print(f"[缓存] 市场数据已保存: {cache_file}")

    return df

##==========缓存代码
def init_db():
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
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
    except Exception as e:
        print(f"数据库初始化失败，跳过: {e}")

def save_result(df):
    try:
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
                (date, rank, code, name, close, amount, score)
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
    except Exception as e:
        print(f"保存结果到数据库失败，跳过: {e}")

def load_history(days=10):
    try:
        conn = sqlite3.connect(DB_PATH)
        today = TRADE_DATE
        start_date = (
            datetime.now() - timedelta(days=days)
        ).strftime('%Y%m%d')
        query = f"""
            SELECT *
            FROM stock_result
            WHERE date >= '{start_date}'
            AND date < '{today}'
            ORDER BY date DESC, rank ASC
        """
        df = pd.read_sql(query, conn)
        conn.close()
        return df
    except Exception as e:
        print(f"加载历史数据失败，返回空数据: {e}")
        # 返回空 DataFrame
        return pd.DataFrame(columns=['date', 'rank', 'code', 'name', 'close', 'amount', 'score'])

def get_tracking_stocks():
    """筛选近5天内出现的、涨幅不大且符合技术形态条件的个股"""
    try:
        # 加载近10天的历史数据（确保包含近5天）
        history_df = load_history(days=10)
        
        if history_df.empty:
            return [], "暂无历史数据"
        
        # 获取近5天内出现过的股票（去重，不含今天）
        five_days_ago = (datetime.now() - timedelta(days=6)).strftime('%Y%m%d')  # 6天前，确保不含今天
        today_date = TRADE_DATE
        recent_stocks = history_df[(history_df['date'] >= five_days_ago) & (history_df['date'] < today_date)]
        
        if recent_stocks.empty:
            return [], "近5天无历史数据"
        
        # 去重，保留每只股票最近一次出现的信息
        recent_stocks = recent_stocks.drop_duplicates(subset=['code'], keep='first')
        
        # 获取当日K线数据用于技术分析
        kline_data = {}
        try:
            if pro is not None:
                # 批量获取当日K线数据（最多100只）
                all_codes = recent_stocks['code'].tolist()
                if len(all_codes) > 0:
                    # 分批获取，每批100只
                    for i in range(0, len(all_codes), 100):
                        batch_codes = all_codes[i:i+100]
                        df = pro.daily(
                            ts_code=",".join(batch_codes),
                            start_date=TRADE_DATE,
                            end_date=TRADE_DATE
                        )
                        if df is not None and not df.empty:
                            for _, row in df.iterrows():
                                kline_data[row['ts_code']] = row
        except Exception as e:
            print(f"获取K线数据失败: {e}")
        
        # 生成跟踪分析股票列表
        tracking_stocks = []
        for _, row in recent_stocks.iterrows():
            ts_code = row['code']
            
            # 获取该股票近5天的数据，计算区间涨幅
            stock_history = history_df[
                (history_df['code'] == ts_code) & 
                (history_df['date'] >= five_days_ago)
            ].sort_values('date')
            
            if len(stock_history) >= 2:
                # 计算近5天涨幅
                first_close = stock_history.iloc[0]['close']
                last_close = stock_history.iloc[-1]['close']
                if first_close > 0:
                    range_pct = ((last_close - first_close) / first_close) * 100
                else:
                    range_pct = 0
            else:
                range_pct = 0
            
            # 条件1：近5天涨幅不超过15%（未大涨过）
            if range_pct > 15:
                continue
            
            # 条件2：收盘价在5日线以上，且出现特殊形态（AND关系）
            is_valid_pattern = False
            pattern_type = ""
            
            # 直接从缓存读取K线数据计算MA5和形态
            try:
                cache_file = os.path.join(CACHE_DIR, f"{ts_code}.csv")
                if os.path.exists(cache_file):
                    df = pd.read_csv(cache_file)
                    df['trade_date'] = df['trade_date'].astype(str)
                    df = df[df['trade_date'] <= TRADE_DATE]
                    df = df.sort_values('trade_date').tail(10)  # 取最近10天
                    
                    if len(df) >= 5:
                        # 计算5日均线
                        df['ma5'] = df['close'].rolling(window=5).mean()
                        
                        # 最新的K线
                        latest_kline = df.iloc[-1]
                        close = float(latest_kline['close'])
                        ma5 = float(latest_kline['ma5']) if pd.notna(latest_kline['ma5']) else None
                        open_price = float(latest_kline['open'])
                        high = float(latest_kline['high'])
                        low = float(latest_kline['low'])
                        
                        # 条件1：收盘价必须在MA5以上
                        if ma5 is not None and pd.notna(ma5) and close > ma5:
                            body_size = abs(close - open_price)
                            upper_shadow = high - max(open_price, close)
                            lower_shadow = min(open_price, close) - low
                            total_range = high - low
                            
                            # 条件2：必须出现特殊形态（小十字星、揉搓线、下影线洗盘）
                            if total_range > 0 and body_size > 0:
                                # 1. 小十字星（实体很小，上下影线存在）
                                if body_size <= total_range * 0.2 and upper_shadow > 0 and lower_shadow > 0:
                                    is_valid_pattern = True
                                    pattern_type = "小十字星"
                                
                                # 2. 揉搓线（长上影+长下影，实体较小）
                                elif (upper_shadow > body_size * 1.5 and lower_shadow > body_size * 1.5) and body_size <= total_range * 0.3:
                                    is_valid_pattern = True
                                    pattern_type = "揉搓线"
                                
                                # 3. 下影线洗盘（长下影，实体可大可小，但下影线很长）
                                elif lower_shadow > body_size * 2 or lower_shadow > total_range * 0.5:
                                    is_valid_pattern = True
                                    pattern_type = "下影线洗盘"
            except Exception as e:
                pass
            
            # 不放宽条件：必须同时满足MA5以上 AND 特殊形态
            
            if is_valid_pattern:
                tracking_stocks.append({
                    'code': ts_code,
                    'name': row['name'],
                    'last_date': row['date'],
                    'last_close': row['close'],
                    'last_score': row['score'],
                    'range_5d_pct': range_pct,
                    'pattern_type': pattern_type
                })
        
        # 按评分排序，取前10只
        tracking_stocks = sorted(tracking_stocks, key=lambda x: -x['last_score'])[:10]
        
        # 生成文本格式
        lines = []
        if tracking_stocks:
            lines.append("=" * 80)
            lines.append("跟踪分析股票池（近5天内出现、涨幅不大、符合技术形态）")
            lines.append("=" * 80)
            for stock in tracking_stocks:
                lines.append(f"{stock['code']} {stock['name']} | "
                           f"最近出现:{stock['last_date']} | "
                           f"最新价:{stock['last_close']:.2f} | "
                           f"5日涨幅:{stock['range_5d_pct']:+.2f}% | "
                           f"形态:{stock['pattern_type']} | "
                           f"评分:{stock['last_score']:.2f}")
            lines.append("=" * 80)
        
        return tracking_stocks, "\n".join(lines)
    except Exception as e:
        print(f"筛选跟踪分析个股失败: {e}")
        return [], "数据加载失败"


# =========================
# 涨跌停数据（替代 emotion.get_limit_stats）
# =========================
def get_limit_stats():
    """获取涨跌停数据，替代原 emotion.get_limit_stats"""
    try:
        print("开始获取涨跌停数据...")
        zt_codes = []
        dt_codes = []
        broken_rate = 15.5

        # 如果没有 tushare API，返回模拟数据
        if pro is None:
            print("[模拟] 使用模拟涨跌停数据...")
            zt_codes = ['000001.SZ', '600000.SH']
            dt_codes = ['000002.SZ']
            return {
                "zt_count": len(zt_codes),
                "dt_count": len(dt_codes),
                "zt_codes": zt_codes,
                "dt_codes": dt_codes,
                "broken_rate": round(broken_rate, 1)
            }

        # 涨停: limit_list_ths 涨停池
        try:
            ths_zt = pro.limit_list_ths(trade_date=TRADE_DATE, limit_type='涨停池')
            if ths_zt is not None and not ths_zt.empty:
                zt_codes = ths_zt['ts_code'].astype(str).tolist()
                print(f"涨停(ths涨停池): {len(zt_codes)}只")
        except Exception as e:
            print(f"limit_list_ths涨停失败: {e}")

        # 跌停: limit_list_ths 跌停池
        try:
            ths_dt = pro.limit_list_ths(trade_date=TRADE_DATE, limit_type='跌停池')
            if ths_dt is not None and not ths_dt.empty:
                dt_codes = ths_dt['ts_code'].astype(str).tolist()
                print(f"跌停(ths跌停池): {len(dt_codes)}只")
        except Exception as e:
            print(f"limit_list_ths跌停失败: {e}")

        # 炸板率
        try:
            limit_df = pro.limit_list_d(trade_date=TRADE_DATE)
            if limit_df is not None and not limit_df.empty:
                zt_u = limit_df[limit_df['limit'] == 'U']
                if not zt_u.empty and 'open_times' in zt_u.columns:
                    total_u = len(zt_u)
                    broken_u = (zt_u['open_times'].fillna(0) > 0).sum()
                    broken_rate = (broken_u / total_u) * 100 if total_u > 0 else 0
                    print(f"炸板率: {broken_rate}%")
                if not zt_codes:
                    zt_codes = zt_u['ts_code'].astype(str).tolist()
        except Exception as e:
            print(f"limit_list_d失败: {e}")

        # fallback
        if not zt_codes and not dt_codes:
            try:
                from solo import theme_trend_sentiment_score as tss
                daily = tss.get_daily_basic(TRADE_DATE)
                if not daily.empty:
                    zt_codes = daily[daily['pct_chg'] >= 9.9]['ts_code'].tolist()
                    zt_codes += daily[(daily['ts_code'].str.startswith(('688','300','301'))) & (daily['pct_chg'] >= 19.9)]['ts_code'].tolist()
                    dt_codes = daily[daily['pct_chg'] <= -9.9]['ts_code'].tolist()
                    dt_codes += daily[(daily['ts_code'].str.startswith(('688','300','301'))) & (daily['pct_chg'] <= -19.9)]['ts_code'].tolist()
            except Exception as e:
                print(f"fallback失败: {e}")

        return {
            "zt_count": len(zt_codes),
            "dt_count": len(dt_codes),
            "zt_codes": zt_codes,
            "dt_codes": dt_codes,
            "broken_rate": round(broken_rate, 1)
        }
    except Exception as e:
        print("获取涨跌停失败:", e)
        return {"zt_count": 0, "dt_count": 0, "zt_codes": [], "dt_codes": [], "broken_rate": 0}

def calc_max_limit_height():
    """计算最高连板高度，替代原 emotion.calc_max_limit_height"""
    try:
        # 没有 tushare API 时返回模拟值
        if pro is None:
            return 3
        
        zt_df = pro.limit_step(trade_date=TRADE_DATE)
        if zt_df is None or zt_df.empty:
            return 0
        if 'nums' in zt_df.columns:
            max_lb = zt_df['nums'].fillna(1).astype(int).max()
            return int(max_lb)
        return 1
    except Exception as e:
        print(e)
        return 0


# =========================
# 主程序
# =========================
def run():


    # =========================
    # 新版大盘分析 + 主题分析（替代 emotion + block）
    # =========================
    print("\n========== 市场趋势总评分 ==========\n")
    market_data = das.read_market_analysis()
    if market_data and market_data.get('overall'):
        ov = market_data['overall']
        print(f"市场状态: {ov.get('market_status', 'N/A')}")
        print(f"总趋势分: {ov.get('trend_score', 0):.1f} | 指数趋势: {ov.get('index_trend', 0):.1f} | 主题趋势: {ov.get('theme_trend', 0):.1f}")
        print(f"建议仓位: {ov.get('position', 0)}%")
    
    emotion_text = ""
    if market_data:
        indices = market_data.get('indices', [])
        for r in indices:
            emotion_text += f"{r['index_name']}: 趋势{r['trend_score']:.1f}({r['trend_status']}) 涨跌{r['pct_change']:+.2f}%\n"
        ov = market_data.get('overall', {})
        if ov:
            emotion_text = f"市场状态: {ov.get('market_status', 'N/A')} | 总趋势分: {ov.get('trend_score', 0):.1f}\n" + emotion_text
            emotion_text += f"建议仓位: {ov.get('position', 0)}%\n"
    
    print("\n========== 主题趋势排名 ==========\n")
    theme_data = das.read_theme_analysis()
    sector_text = ""
    top_sector = pd.DataFrame()
    if theme_data and theme_data.get('themes'):
        themes = theme_data['themes']
        # 按趋势分排序
        themes_sorted = sorted(themes, key=lambda x: x.get('trend_score', 0), reverse=True)
        sector_text_lines = []
        for i, t in enumerate(themes_sorted[:20]):
            sector_text_lines.append(f"{i+1}. {t['theme_name']:10s} 趋势{t['trend_score']:.1f}({t['trend_status']}) 情绪{t['sentiment_score']:.1f}({t['sentiment_status']}) 5日{t.get('change', 0):+.1f}%")
        sector_text = "\n".join(sector_text_lines)
        print(f"  共 {len(themes_sorted)} 个主题，TOP20 如下：")
        for i, t in enumerate(themes_sorted[:10]):
            print(f"    #{i+1} {t['theme_name']:<10} 趋势{t['trend_score']:.1f}")
    
    print("\n========== 60日趋势平均分TOP10 ==========\n")
    avg_trend_data = das.read_60day_avg_trend_scores()
    sector_text_his = ""
    if avg_trend_data and avg_trend_data.get('themes'):
        his_themes = avg_trend_data['themes']
        his_lines = []
        for i, t in enumerate(his_themes[:10], 1):
            his_lines.append(f"{i}. {t['theme_name']:10s} 60日平均{t['avg_trend_score']:.1f} ({t['day_count']}天)")
        sector_text_his = "\n".join(his_lines)
        print(f"  共 {len(his_themes)} 个主题")
        for t in his_themes[:5]:
            print(f"    {t['theme_name']:<10} 平均{t['avg_trend_score']:.1f}")
    
    emotion_stage = "强"
    #else:
    #    emotion_stage = "弱"
    
    market = get_market()
    
    # ===== 大盘行情概览 =====
    market_overview = ""
    try:
        # 指数数据
        index_codes = {'上证指数': '000001.SH', '沪深300': '000300.SH', '创业板指': '399006.SZ'}
        index_lines = []
        if pro is None:
            print("[模拟] 使用模拟指数数据...")
            index_lines = [
                "  上证指数: 3150.25 (+1.25%)",
                "  沪深300: 3680.15 (+0.89%)",
                "  创业板指: 2150.75 (+1.45%)"
            ]
        else:
            for name, code in index_codes.items():
                try:
                    idx = pro.index_daily(ts_code=code, start_date=TRADE_DATE, end_date=TRADE_DATE)
                    if not idx.empty:
                        r = idx.iloc[-1]
                        index_lines.append(f"  {name}: {r['close']:.2f} ({r['pct_chg']:+.2f}%)")
                except:
                    pass
        if index_lines:
            market_overview = "【大盘行情】\n" + "\n".join(index_lines) + "\n"
        
        # 涨跌统计
        if market is not None and not market.empty:
            up = (market['pct_chg'] > 0).sum()
            down = (market['pct_chg'] < 0).sum()
            flat = (market['pct_chg'] == 0).sum()
            total_amount = market['amount'].sum() / 100000  # 千元→亿
            market_overview += f"  上涨{up}只 下跌{down}只 平盘{flat}只 | 总成交额{total_amount:.0f}亿\n"
    except Exception as e:
        market_overview = f"[大盘行情获取失败: {e}]\n"
    
    # 拼入 emotion_text
    emotion_text = market_overview + emotion_text
    
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
                ts_code,
                emotion_stage
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
        # 如果没有结果，在测试模式下使用模拟数据
        if pro is None:
            print("[模拟] 无真实策略结果，使用模拟数据...")
            result_df = pd.DataFrame([
                {'代码': '000001.SZ', '名称': '平安银行', '现价': 10.5, '涨跌幅': 1.5, '成交额': 500000, '总市值（亿元）': 1500},
                {'代码': '600000.SH', '名称': '浦发银行', '现价': 8.2, '涨跌幅': -0.8, '成交额': 300000, '总市值（亿元）': 1200},
                {'代码': '000002.SZ', '名称': '万科A', '现价': 25.3, '涨跌幅': 2.3, '成交额': 800000, '总市值（亿元）': 800},
                {'代码': '600519.SH', '名称': '贵州茅台', '现价': 1800.0, '涨跌幅': 0.5, '成交额': 1200000, '总市值（亿元）': 25000},
                {'代码': '300750.SZ', '名称': '宁德时代', '现价': 120.0, '涨跌幅': 3.2, '成交额': 2000000, '总市值（亿元）': 18000},
            ])
        else:
            print("无结果")
            return

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
    
    # =========================
    # 取前10名用于分析
    # =========================
    top10_df = result_df.head(10)
    print("\n========== Top10 个股 ==========\n")
    print(top10_df[['代码', '名称', '现价', '涨跌幅', '最终评分']])
    
    stock_text = top10_df.to_string(index=False)
    all_stock_text = result_df.to_string(index=False)
    stock_his_df=load_history()
    stock_his_text = str(stock_his_df)

    # =========================
    # 获取跟踪分析个股
    # =========================
    tracking_stocks, tracking_stocks_text = get_tracking_stocks()
    if tracking_stocks_text and tracking_stocks_text != "暂无历史数据" and tracking_stocks_text != "近5天无历史数据":
        print("\n========== 跟踪分析个股 ==========\n")
        print(tracking_stocks_text)
    else:
        print(f"\n========== 暂无跟踪分析个股 ==========\n{tracking_stocks_text}")

    limit_stats = get_limit_stats()
    max_lb_height = calc_max_limit_height()

    limit_info = f"""
涨跌停数据：
- 涨停数量: {limit_stats['zt_count']}只
- 跌停数量: {limit_stats['dt_count']}只
- 炸板率: {limit_stats['broken_rate']}%
- 最高连板高度: {max_lb_height}连板
"""

    try:
        if pro is None:
            print("[模拟] 使用模拟连板天梯数据...")
            lb_text = """ts_code    name    limit_num  \n000001.SZ  平安银行  3\n600000.SH  浦发银行  2\n000002.SZ  万科A  1"""
            limit_info += f"\n连板天梯详情：\n{lb_text}"
        else:
            lb_df = pro.limit_step(trade_date=TRADE_DATE)
            if lb_df is not None and not lb_df.empty:
                lb_text = lb_df.to_string(index=False)
                limit_info += f"\n连板天梯详情：\n{lb_text}"
    except Exception as e:
        print(f"连板天梯获取失败: {e}")

    # =========================
    # 读取主题选股结果
    # =========================
    theme_stocks_records, theme_stocks_text = load_theme_pattern_stocks()
    if theme_stocks_text:
        print("\n========== 主题选股结果 ==========\n")
        print(theme_stocks_text)
    else:
        print("\n========== 未找到主题选股结果 ==========")

    #return
    prompt = f"""

当前市场情绪：

{emotion_text}

{limit_info}

当前最强主线列表：

{sector_text}

近10日最强主线列表:

{sector_text_his}

今日量化候选股票池（按综合评分排序前10名）：

{stock_text}

完整量化候选股票池（按综合评分排序）：

{all_stock_text}

过去十日量化候选股票池:
{stock_his_text}

主题个股池选股结果（来自 theme_pattern_stock_picker.py）：
（这是根据主题趋势和情绪筛选出的优质个股，包含中期趋势主题和短线主线的龙头和中军）

{theme_stocks_text}

近5日跟踪分析股票池（从历史自选股中筛选涨幅不大、未大涨过的个股）：
（这些是近期持续关注、尚未启动的股票，值得跟踪分析）

{tracking_stocks_text}


请对以上数据进行分析，具体要求：

1. 仅过滤有基本面重大风险的个股：
   - 近三个月内有定增预案
   - 有大额减持公告
   - 未来半年有大额解禁压力
   - 有重大诉讼风险
   - 有重大财务风险（如连续亏损、审计异常等）
   - 有其他重大利空消息

2. 对于无重大风险的前10名个股，保持原有的综合评分排序，不要重新筛选和排序

3. 对每只股票进行以下分析：
   - 个股基本情况和所属板块
   - 当前位置和走势分析
   - 未来上涨空间预估（给出合理的目标价位）
   - 买点建议（具体价位或技术形态）
   - 止损点建议
   - 简要的风险提示


输出内容：
标题：每日复盘({TRADE_DATE})
内容(分成以下部分)：
1、大盘情绪(含涨跌停数等几个数据指标)和仓位建议
2、通过以上数据及全网板块热点分析,给出今日主线板块和近几日动态变化分析：
   - 在主线板块分析中，**必须明确区分并加粗标注"中军"和"补涨中军"**
   - 中军：满足8个严格条件的趋势个股，RS20>=5，属于稳健型标的
   - 补涨中军：成交活跃+均线金叉的个股，不比RS，特点是大成交额+温和放量，属于补涨型标的
   - 在描述中，使用【**中军**】和【**补涨中军**】加粗标注股票类型
   - 分析主线板块的阶段和持续性，给出数据支撑和逻辑理由
3、自选量化股票池分析：严格按综合评分从高到低排序输出前10名个股，对每只股票单独分析，包括：
   - 股票名和代码（作为小标题，加粗显示）
   - 当前价格
   - 综合评分
   - 所属板块和主线关系
   - 技术面分析
   - 未来上涨空间预估
   - 买点建议
   - 止损点建议
   - 风险提示（如果有）
   - 如遇重大风险，请在分析中标注"⚠️ 有重大风险"，但仍保留在列表中并说明理由
4、跟踪分析个股：从近5日跟踪分析股票池中，精选符合技术形态的个股进行深度分析，重点关注：
   - 这些是近期持续出现在量化池中但尚未大涨的个股
   - 分析其当前技术形态（小十字星/揉搓线/下影线洗盘等）和可能的启动时机
   - 给出合适的跟踪关注点和潜在买点

格式要求：
- Top10个股分析中，每只股票单独分段，用【股票名+代码】作为小标题，加黑加粗显示
- 股票分析另起一行，分点说明
- 风格简洁明了，适合阅读

禁止猜测。
禁止根据经验判断。
所有结论必须引用输入数据。
特别注意：
- 涨停数量、跌停数量、炸板率、连板高度等数据必须使用上面提供的实际数据
- 不要添加任何未在输入数据中出现的具体股票事实（如"某股5连板"等）
- 不要重新筛选和排序Top10个股，严格按输入的综合评分顺序输出
- 在个股分析中，可结合主题选股结果进行交叉验证

"""
    print("\n========== Deepseek AI分析 ==========\n")
    report = deepseek(prompt)
    print(report)

    try:
        ds_file = os.path.join(CACHE_DIR, f"Deepseek_Self_{TRADE_DATE}.md")
        with open(ds_file, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"✅ Deepseek报告已保存: {ds_file}")
    except Exception as e:
        print(f"⚠️ Deepseek报告保存失败: {e}")
    
    # 保存最终报告
    final_report = report
    
    # 先发送微信（即使报告保存失败也要发送）
    send_wechat(
        final_report,
        os.getenv("WECHAT_SCKEY")
    )   
    print("✅ 微信已发送")

    # 保存报告（带异常处理）
    try:
        report_file = os.path.join(CACHE_DIR, f"Final_Self_{TRADE_DATE}.md")
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(final_report)
        print(f"✅ 报告已保存: {report_file}")
    except Exception as e:
        print(f"⚠️ 报告保存失败: {e}")

    try:
        html_file = os.path.join(CACHE_DIR, f"Final_Self_{TRADE_DATE}.html")
        markdown_to_html_report(final_report, 
                                output_file=html_file, 
                                pdf_file=os.path.join(CACHE_DIR, f"Final_Self_{TRADE_DATE}.pdf"), 
                                title=f"复盘及精选个股({TRADE_DATE})"
                                )
    except Exception as e:
        print(f"⚠️ HTML报告生成失败: {e}")

    #result = send_wechat_message(report)

# =========================
# 启动
# =========================
if __name__ == "__main__":

    run()


