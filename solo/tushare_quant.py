###===自选复盘 - tushare接口===###

import io
import json
import os
import struct
import sys

# =========================
# Windows GBK 控制台输出修复：强制 UTF-8 编码
# =========================
#sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
#sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# =========================
# 终极方案：patch os.path.expanduser，不让 tushare 访问用户根目录
# 必须在导入 tushare 之前执行！
# =========================
original_expanduser = os.path.expanduser
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 现在可以安全导入 tushare 了
import markdown2 # type: ignore
import requests
import pandas as pd
import numpy as np
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

# 缓存/报告目录统一到 d:\stock\ 下
STOCK_DATA_DIR = r"d:\mystock"
CACHE_DIR = os.path.join(STOCK_DATA_DIR, "cache_daily")
REPORT_DIR = os.path.join(STOCK_DATA_DIR, "report_daily")   
DB_PATH = os.path.join(REPORT_DIR, "stock_result.db")
NEWS_CACHE_DIR = os.path.join(STOCK_DATA_DIR, "news_cache")

os.makedirs(STOCK_DATA_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)
os.makedirs(NEWS_CACHE_DIR, exist_ok=True)

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
                    close_val = row.get('close', 0) or 0
                    pct_val = row.get('pct_chg', 0) or 0
                    turnover_val = row.get('turnover_rate', 0) or 0
                    theme_val = row.get('theme_name', '') or ''
                    lines.append(f"  {row['code']} {row['name']} | 主题:{theme_val} | "
                               f"现价:{close_val:.2f} | 涨跌:{pct_val:+.2f}% | "
                               f"换手:{turnover_val:.2f}% | 市值:{mcap}")
                    lines.append(f"    推荐理由: {row.get('reason', '')}")
            
            if not mid_term_buzhang.empty:
                lines.append("📈 补涨中军（成交活跃+均线金叉）")
                for _, row in mid_term_buzhang.iterrows():
                    mcap = f"{row.get('mcap', 0):.1f}亿" if pd.notna(row.get('mcap')) else "--"
                    close_val = row.get('close', 0) or 0
                    pct_val = row.get('pct_chg', 0) or 0
                    turnover_val = row.get('turnover_rate', 0) or 0
                    theme_val = row.get('theme_name', '') or ''
                    lines.append(f"  {row['code']} {row['name']} | 主题:{theme_val} | "
                               f"现价:{close_val:.2f} | 涨跌:{pct_val:+.2f}% | "
                               f"换手:{turnover_val:.2f}% | 市值:{mcap}")
                    lines.append(f"    推荐理由: {row.get('reason', '')}")
        
        # 短线主线主题
        if not short_term.empty:
            lines.append("\n⚡ 短线主线（当日最强主线TOP3）")
            lines.append("-" * 80)
            
            if not short_term_zhongjun.empty:
                lines.append("🏆 中军（短线跟随）")
                for _, row in short_term_zhongjun.iterrows():
                    mcap = f"{row.get('mcap', 0):.1f}亿" if pd.notna(row.get('mcap')) else "--"
                    close_val = row.get('close', 0) or 0
                    pct_val = row.get('pct_chg', 0) or 0
                    turnover_val = row.get('turnover_rate', 0) or 0
                    theme_val = row.get('theme_name', '') or ''
                    lines.append(f"  {row['code']} {row['name']} | 主题:{theme_val} | "
                               f"现价:{close_val:.2f} | 涨跌:{pct_val:+.2f}% | "
                               f"换手:{turnover_val:.2f}% | 市值:{mcap}")
                    lines.append(f"    推荐理由: {row.get('reason', '')}")
            
            if not short_term_buzhang.empty:
                lines.append("📈 补涨中军（成交活跃+均线金叉）")
                for _, row in short_term_buzhang.iterrows():
                    mcap = f"{row.get('mcap', 0):.1f}亿" if pd.notna(row.get('mcap')) else "--"
                    close_val = row.get('close', 0) or 0
                    pct_val = row.get('pct_chg', 0) or 0
                    turnover_val = row.get('turnover_rate', 0) or 0
                    theme_val = row.get('theme_name', '') or ''
                    lines.append(f"  {row['code']} {row['name']} | 主题:{theme_val} | "
                               f"现价:{close_val:.2f} | 涨跌:{pct_val:+.2f}% | "
                               f"换手:{turnover_val:.2f}% | 市值:{mcap}")
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
    """获取最近的交易日"""

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


def validate_trade_date(date_str):
    """验证日期是否为有效交易日，如果不是则返回最近的有效交易日"""
    if pro is None:
        return date_str
    
    try:
        # 获取交易日历
        cal = pro.trade_cal(
            exchange='',
            start_date=date_str,
            end_date=date_str
        )
        
        # 如果当天是交易日
        if not cal.empty and cal.iloc[0]['is_open'] == 1:
            return date_str
        
        # 如果不是交易日，找之前最近的交易日
        cal = pro.trade_cal(
            exchange='',
            start_date=(datetime.strptime(date_str, '%Y%m%d') - timedelta(days=30)).strftime('%Y%m%d'),
            end_date=date_str
        )
        cal = cal[cal['is_open'] == 1]
        last_valid = cal[cal['cal_date'] <= date_str]['cal_date'].max()
        if last_valid:
            print(f"[警告] {date_str} 不是交易日，使用最近交易日: {last_valid}")
            return str(last_valid)
        return date_str
    except Exception as e:
        print(f"[警告] 日期验证失败: {e}，使用原日期: {date_str}")
        return date_str


# 全局交易日变量
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
    """使用tushare获取股票代码和名称映射"""
    global STOCK_DICT
    try:
        if pro is not None:
            df = pro.stock_basic(exchange='', list_status='L', fields='ts_code,symbol,name')
            stock_dict = {}
            for _, row in df.iterrows():
                # 同时存储带后缀和不带后缀的代码
                stock_dict[str(row['symbol'])] = row['name']
                stock_dict[str(row['ts_code'])] = row['name']
            STOCK_DICT = stock_dict
            return stock_dict
    except Exception as e:
        print(f"[警告] tushare股票字典获取失败: {e}")
    
    # 兜底：使用内置基础字典
    STOCK_DICT = {
        '000001': '平安银行', '600000': '浦发银行', '000002': '万科A',
        '600519': '贵州茅台', '300750': '宁德时代', '000001.SZ': '平安银行',
        '600000.SH': '浦发银行', '000002.SZ': '万科A', '600519.SH': '贵州茅台',
        '300750.SZ': '宁德时代'
    }
    return STOCK_DICT

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



# ======================================================
# 批量预取历史数据（解决高频API调用问题）
# ======================================================
def batch_prefetch_hist_data(codes, start_date='20250101'):
    """
    在主循环之前批量预取所有股票数据到本地缓存
    使用 tushare 批量接口 pro.daily(ts_code="code1,code2,...")
    之后 get_hist_data() 将全部命中本地缓存，不再调API
    """
    if not codes:
        return
    
    # 检查有多少已经在缓存中有今日数据
    cached = []
    missing = []
    for ts_code in codes:
        cache_file = os.path.join(CACHE_DIR, f"{ts_code}.csv")
        if os.path.exists(cache_file):
            try:
                df = pd.read_csv(cache_file)
                df['trade_date'] = df['trade_date'].astype(str)
                if (df['trade_date'] == TRADE_DATE).any():
                    cached.append(ts_code)
                    continue
            except:
                pass
        missing.append(ts_code)
    
    print(f"  批量预取: {len(cached)} 已缓存, {len(missing)} 需下载")
    
    if not missing:
        return
    
    # 分批下载，每批最多200只（tushare单次上限）
    batch_size = 200
    for i in range(0, len(missing), batch_size):
        batch = missing[i:i + batch_size]
        try:
            # 拼接为逗号分隔的代码列表
            ts_list = ",".join(batch)
            df = pro.daily(
                ts_code=ts_list,
                start_date=start_date,
                end_date=TRADE_DATE
            )
            
            if df is not None and not df.empty:
                # 按股票代码分组，保存到各自的缓存文件
                for ts_code in batch:
                    stock_df = df[df['ts_code'] == ts_code]
                    if not stock_df.empty:
                        stock_df = stock_df.sort_values('trade_date')
                        cache_file = os.path.join(CACHE_DIR, f"{ts_code}.csv")
                        stock_df.to_csv(cache_file, index=False)
                
                downloaded = len(stock_df['ts_code'].unique()) if 'ts_code' in stock_df.columns else len(batch)
                print(f"  批次 {i//batch_size + 1}: 成功下载 {downloaded}/{len(batch)} 只")
            else:
                print(f"  批次 {i//batch_size + 1}: 下载返回空")
            
            # 防止频率限制
            time.sleep(0.1)
            
        except Exception as e:
            print(f"  批次 {i//batch_size + 1} 下载失败: {e}")
            # 单批失败则逐只重试
            for ts_code in batch:
                try:
                    single_df = pro.daily(
                        ts_code=ts_code,
                        start_date=start_date,
                        end_date=TRADE_DATE
                    )
                    if single_df is not None and not single_df.empty:
                        cache_file = os.path.join(CACHE_DIR, f"{ts_code}.csv")
                        single_df.to_csv(cache_file, index=False)
                    time.sleep(0.05)
                except:
                    pass
    


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
def calc_trend_strength_v2(df):
    """
    更细腻的趋势强度评分（避免1.0被滥用）
    """
    C = df['close']

    ma10 = C.rolling(10).mean()
    ma20 = C.rolling(20).mean()
    ma30 = C.rolling(30).mean()
    ma60 = C.rolling(60).mean()

    score = 0

    # 1. 均线排列（更细化，避免全满分）
    if (ma10.iloc[-1] > ma20.iloc[-1] > ma30.iloc[-1] > ma60.iloc[-1]):
        score += 30  # 完美多头排列得30分
    elif (ma10.iloc[-1] > ma20.iloc[-1] and ma20.iloc[-1] > ma60.iloc[-1]):
        score += 20  # 次好排列得20分
    elif (ma10.iloc[-1] > ma60.iloc[-1]):
        score += 10  # 仅短期在长期上得10分

    # 2. 均线斜率（更细腻）
    ma20_slope = (ma20.iloc[-1] - ma20.iloc[-5]) / ma20.iloc[-5]
    if ma20_slope > 0.03:
        score += 20
    elif ma20_slope > 0.01:
        score += 10
    elif ma20_slope > 0:
        score += 5

    ma60_slope = (ma60.iloc[-1] - ma60.iloc[-10]) / ma60.iloc[-10]
    if ma60_slope > 0.02:
        score += 15
    elif ma60_slope > 0.01:
        score += 8
    elif ma60_slope > 0:
        score += 3

    # 3. 股价位置（更细化）
    price_ma60_ratio = C.iloc[-1] / ma60.iloc[-1]
    if price_ma60_ratio > 1.1:
        score += 15
    elif price_ma60_ratio > 1.05:
        score += 10
    elif price_ma60_ratio > 1.0:
        score += 5

    # 最大不超过85分（避免1.0被滥用）
    return min(score, 85) / 100
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

from scipy.stats import linregress
def calc_trend_stability2(close, window=20):

    y = close.tail(window).values

    x = np.arange(window)

    slope, intercept, r, p, stderr = linregress(x, y)

    return r * r


def calc_volume_structure(df):
    if len(df) < 30:
        return 0

    C = df['close']
    V = df['vol']

    vol_ratio = V.iloc[-1] / (V.tail(20).mean() + 1e-6)

    price_trend = C.iloc[-1] / C.iloc[-20] - 1

    obv = (np.sign(C.diff()) * V).fillna(0).cumsum()
    obv_strength = obv.iloc[-1] / (abs(obv.tail(20).mean()) + 1e-6)

    # 归一化到 0-1
    vol_component = np.tanh(np.log1p(vol_ratio) * 0.3)
    obv_component = np.tanh(np.log1p(abs(obv_strength)) * 0.3)
    price_component = np.tanh(max(price_trend, 0) * 2)
    return vol_component * 0.4 + obv_component * 0.4 + price_component * 0.2
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

    # 归一化到 0-1
    return score / 100.0
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

    # 归一化到 0-1
    vol_component = np.tanh(np.log1p(vol_ratio) * 0.3)
    price_component = np.tanh(max(price_change, 0) * 5)
    flow_component = np.tanh(np.log1p(abs(money_flow)) * 0.1)
    consistency_component = flow_consistency
    return vol_component * 0.3 + price_component * 0.3 + flow_component * 0.2 + consistency_component * 0.2    

def calc_dual_layer_score_v5(df, ts_code=''):
    """计算双层评分
    
    Args:
        df: K线数据
        ts_code: 股票代码，用于判断双创加分
    """

    C = df['close']
    H = df['high']
    L = df['low']
    VOL = df['vol']

    # =========================
    # 趋势层
    # =========================
    #trend_strength = calc_trend_slope(C, 20)
    trend_strength = calc_trend_strength_v2(df)
    trend_stability = calc_trend_stability2(C, 20)

    trend_score = (
        trend_strength * 0.6 +
        trend_stability * 0.4
    )

    # =========================
    # 资金层
    # =========================
    volume_structure = calc_volume_structure(df)
    accumulation = calc_accumulation_factor(df)
    big_money = calc_big_money_factor(df)

    money_score = (
        volume_structure * 0.4 +
        accumulation * 0.3 +
        big_money * 0.3
    )

    # =========================
    # 突破位置
    # =========================
    HHV60 = H.rolling(60).max().iloc[-1]

    breakout_position = min(
        C.iloc[-1] / HHV60,
        1.05
    )

    breakout_position = np.clip(
        (breakout_position - 0.90) / 0.15,
        0,
        1
    )

    # =========================
    # 启动阶段
    # =========================
    MA20 = C.rolling(20).mean()

    distance = C.iloc[-1] / MA20.iloc[-1]

    if distance <= 1.05:
        phase_score = 1.0

    elif distance <= 1.15:
        phase_score = 0.8

    elif distance <= 1.25:
        phase_score = 0.5

    else:
        phase_score = 0.2

    # =========================
    # 量能爆发
    # =========================
    vol5 = VOL.tail(5).mean()
    vol20 = VOL.tail(20).mean()

    volume_burst = min(vol5 / vol20, 3)

    burst_score = volume_burst / 3

    # =========================
    # 平台压缩度
    # =========================
    HHV20 = H.tail(20).max()
    LLV20 = L.tail(20).min()

    amp20 = (HHV20 - LLV20) / LLV20

    if amp20 <= 0.15:
        compression_score = 1.0

    elif amp20 <= 0.25:
        compression_score = 0.8

    elif amp20 <= 0.35:
        compression_score = 0.5

    else:
        compression_score = 0.2

    # =========================
    # 爆发力层
    # =========================
    explosion_score = (
        breakout_position * 0.4 +
        phase_score * 0.3 +
        burst_score * 0.3
    )

    # =========================
    # 双创加分（创业板300开头、科创板688开头）
    # =========================
    is_chuangchuang = ts_code.startswith('300') or ts_code.startswith('688')
    chuangchuang_bonus = 1.15 if is_chuangchuang else 1.0  # 双创加成15%

    # =========================
    # 最终评分
    # =========================
    final_score = (
        trend_score * 35 +
        money_score * 25 +
        explosion_score * 25 +
        compression_score * 15
    ) * chuangchuang_bonus

    # =========================
    # 风险等级(只输出)
    # =========================
    MA60 = C.rolling(60).mean()

    risk_ratio = C.iloc[-1] / MA60.iloc[-1]

    if risk_ratio > 1.60:
        risk_level = "极高"

    elif risk_ratio > 1.40:
        risk_level = "高"

    elif risk_ratio > 1.20:
        risk_level = "中"

    else:
        risk_level = "低"

    return {
        "趋势强度": round(trend_strength, 3),
        "趋势稳定": round(trend_stability, 3),

        "趋势分": round(trend_score * 100, 1),
        "资金分": round(money_score * 100, 1),

        "突破位置": round(breakout_position * 100, 1),
        "启动阶段": round(phase_score * 100, 1),
        "量能爆发": round(burst_score * 100, 1),

        "压缩度": round(compression_score * 100, 1),

        "爆发力分": round(explosion_score * 100, 1),

        "风险等级": risk_level,

        "最终评分": round(final_score, 2)
    }

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


def sigmoid(x):
    return 1 / (1 + np.exp(-x))


def calc_dual_layer_score_v6(df, ts_code='', theme=''):
    """
    三路径概率系统（游资行为建模 v6）- 修复版
    1. 避免趋势强度1.0被滥用 ✅
    2. 失败概率分层惩罚机制 ✅ 
    3. 支持主题强度纳入（通过外部传入） ✅

    输出：
        P_up        上涨延续概率
        P_fail      突破失败概率
        P_squeeze   洗盘再启动概率
        edge_score  交易边际优势
    """

    C = df['close']
    H = df['high']
    L = df['low']
    VOL = df['vol']


    is_chuangchuang = ts_code.startswith('300') or ts_code.startswith('688')

    beta_multiplier = 1.0

    if ts_code.startswith('300'):   # 创业板（高弹性）
        beta_multiplier = 1.25

    elif ts_code.startswith('688'): # 科创板（更极端弹性）
        beta_multiplier = 1.30
    
    # =========================
    # 弹性结构（游资核心）
    # =========================
    HHV20 = H.tail(20).max()
    LLV20 = L.tail(20).min()

    amp20 = (HHV20 - LLV20) / (LLV20 + 1e-6)

    if np.isnan(amp20) or np.isinf(amp20):
        compression_score = 0.5
    else:
        compression_score = (
            1.0 if amp20 <= 0.15 else
            0.8 if amp20 <= 0.25 else
            0.5 if amp20 <= 0.35 else
            0.2
        )
    volatility = (H.tail(20).max() - L.tail(20).min()) / C.iloc[-1]

    turnover_proxy = VOL.tail(5).mean() / (VOL.tail(20).mean() + 1e-6)

    elastic_score = sigmoid(
        volatility * 1.0 +
        turnover_proxy * 0.8 +
        (1 - compression_score) * 0.6
    )

    # =========================
    # 1. 基础趋势 & 资金结构
    # =========================
    trend_strength = calc_trend_strength_v2(df)
    trend_stability = calc_trend_stability2(C, 20)

    volume_structure = calc_volume_structure(df)
    accumulation = calc_accumulation_factor(df)
    big_money = calc_big_money_factor(df)

    money_momentum = (
        volume_structure * 0.5 +
        accumulation * 0.3 +
        big_money * 0.2
    )

    # =========================
    # 2. 突破结构（关键升级）
    # =========================
    HHV60 = H.rolling(60).max().iloc[-1]
    breakout_position = np.clip(
        (C.iloc[-1] / HHV60 - 0.90) / 0.15,
        0, 1
    )

    MA20 = C.rolling(20).mean().iloc[-1]
    MA60 = C.rolling(60).mean().iloc[-1]

    # 价格效率（放量是否有效）
    price_efficiency = abs(C.iloc[-1] - C.iloc[-2]) / (VOL.iloc[-1] + 1e-6)
    price_efficiency = np.tanh(price_efficiency * 3)

    # =========================
    # 3. 压缩与爆发结构
    # =========================
    HHV20 = H.tail(20).max()
    LLV20 = L.tail(20).min()
    amp20 = (HHV20 - LLV20) / LLV20

    compression_score = (
        1.0 if amp20 <= 0.15 else
        0.8 if amp20 <= 0.25 else
        0.5 if amp20 <= 0.35 else
        0.2
    )

    vol5 = VOL.tail(5).mean()
    vol20 = VOL.tail(20).mean()
    burst_score = np.clip(vol5 / (vol20 + 1e-6), 0, 3) / 3

    # =========================
    # 4. 趋势概率（核心）
    # =========================
    trend_prob = sigmoid(
        (trend_strength - 0.5) * 1.5 +
        (trend_stability - 0.5) * 1.0
    )

    # =========================
    # 5. 上涨推进概率（核心）
    # =========================
    break_strength = sigmoid(
        (breakout_position - 0.5) * 1.5 +
        (money_momentum - 0.5) * 1.0 +
        (price_efficiency - 0.5) * 0.8
    )

    P_up = (
        0.45 * trend_prob +
        0.35 * break_strength +
        0.20 * money_momentum
    )

    # =========================
    # 6. 失败概率（最关键风控）
    # =========================
    # 1. 高价风险因子 - 连续值而非二值
    price_ma60_ratio = C.iloc[-1] / MA60
    high_risk_zone = np.clip((price_ma60_ratio - 1.1) / 0.4, 0.0, 1.0)  # 1.1以下0，1.5以上1
    
    # 2. 阻力压力因子 - 连续值
    # 用amp20直接计算，而不是离散的compression_score
    resistance_pressure = np.clip((amp20 - 0.15) / 0.25, 0.0, 1.0)  # 0.15以下0，0.4以上1
    
    # 3. 派发风险因子 - 连续值
    vol_ratio = VOL.iloc[-1] / (VOL.tail(10).mean() + 1e-6)
    price_change = (C.iloc[-1] - C.iloc[-2]) / C.iloc[-2]
    # 放量下跌风险：量比越大且跌幅越大，风险越高
    distribution_risk = 0.0
    if price_change < 0:  # 下跌
        distribution_risk = np.clip((vol_ratio - 1.0) * abs(price_change) * 10, 0.0, 1.0)
    
    # 4. 额外维度：趋势稳定性下降风险
    ma20 = C.rolling(20).mean().iloc[-1]
    ma5 = C.rolling(5).mean().iloc[-1]
    trend_decline_risk = 0.0
    if ma5 < ma20:  # 5日均线跌破20日均线
        trend_decline_risk = np.clip((ma20 - ma5) / ma20 * 20, 0.0, 1.0)
    
    fail_prob = sigmoid(
        (resistance_pressure - 0.5) * 1.5 +
        (high_risk_zone - 0.5) * 1.2 +
        (distribution_risk - 0.5) * 1.5 +
        (trend_decline_risk - 0.5) * 0.8
    )

    # =========================
    # 7. 洗盘再启动概率（游资核心）
    # =========================
    squeeze_prob = sigmoid(
        (compression_score - 0.5) * 1.5 +
        (0.5 - burst_score) * 0.8 +
        (trend_stability - 0.5) * 0.6
    )

    # =========================
    # 8. 交易边际优势
    # =========================
    edge_score = P_up - fail_prob

    # =========================
    # 9. 风险等级（辅助）
    # =========================
    risk_ratio = C.iloc[-1] / MA60

    risk_level = (
        "极高" if risk_ratio > 1.6 else
        "高" if risk_ratio > 1.4 else
        "中" if risk_ratio > 1.2 else
        "低"
    )
    # =========================
    # 10. 总排序评分（用于选股优先级）
    # =========================

    trend_component = trend_prob
    momentum_component = break_strength
    money_component = money_momentum

    # =========================
    # 基础层（核心 alpha）
    # =========================
    base_score = (
        trend_component * 0.4 +
        momentum_component * 0.35 +
        money_component * 0.25
    )

    # =========================
    # 弹性层（游资核心）
    # =========================
    elastic_layer = beta_multiplier * (0.7 + 0.3 * elastic_score)

    # =========================
    # 风险层 - 分层惩罚机制 ✅
    # =========================
    if fail_prob < 0.2:
        # 低失败概率：几乎不惩罚
        risk_layer = 1.0
    elif fail_prob < 0.4:
        # 中低失败概率：轻微惩罚
        risk_layer = 0.9
    elif fail_prob < 0.5:
        # 中等失败概率：中等惩罚
        risk_layer = 0.75
    elif fail_prob < 0.6:
        # 中高失败概率：较重惩罚
        risk_layer = 0.55
    else:
        # 高失败概率：严厉惩罚
        risk_layer = 0.35

    # =========================
    # 主题强度加分（可选）✅
    # =========================
    theme_bonus = 1.0
    if theme:
        try:
            theme_data = das.read_theme_analysis(TRADE_DATE)
            if theme_data:
                for t in theme_data.get('themes', []):
                    if t.get('theme_name') == theme:
                        theme_score = t.get('trend_score', 0)
                        if theme_score >= 80:
                            theme_bonus = 1.3  # 热主题大加分
                        elif theme_score >= 60:
                            theme_bonus = 1.15  # 次热主题加分
                        elif theme_score >= 40:
                            theme_bonus = 1.0  # 不加分
                        else:
                            theme_bonus = 0.9  # 冷主题减分
                        break
        except Exception as e:
            pass

    # =========================
    # 最终评分
    # =========================
    final_rank_score = base_score

    final_rank_score *= elastic_layer
    final_rank_score *= risk_layer
    final_rank_score *= theme_bonus

    # 调整放大倍数，让评分更合理
    final_rank_score = np.clip(final_rank_score * 200, 0, None)
    
    # =========================
    # 输出
    # =========================
    return {
        "趋势概率": round(P_up, 4),
        "失败概率": round(fail_prob, 4),
        "洗盘概率": round(squeeze_prob, 4),
        "交易优势": round(edge_score, 4),

        "趋势强度": round(trend_strength, 3),
        "趋势稳定": round(trend_stability, 3),

        "资金动量": round(money_momentum, 3),
        "突破强度": round(break_strength, 3),

        "压缩度": round(compression_score, 3),
        "量能爆发": round(burst_score, 3),

        "风险等级": risk_level,
        "总排序评分": round(final_rank_score, 2)
    }


# =========================================================
# V7 评分系统 v6：主题纯度优化 + 主线共振加分
# =========================================================
def calc_dual_layer_score_v7(df, ts_code='', stock_info=None, theme=''):
    """
    V7评分系统 v6：主题纯度优化 + 主线共振加分
    """

    # =========================
    # 获取V6技术指标
    # =========================
    v6_result = calc_dual_layer_score_v6(df, ts_code, theme)

    # V6各指标
    trend_probability = v6_result.get('趋势概率', 0)  # 0-1
    fail_prob = v6_result.get('失败概率', 0)  # 0-1
    breakout_strength = v6_result.get('突破强度', 0)  # 0-1
    money_momentum = v6_result.get('资金动量', 0)  # 0-1
    trend_stability = v6_result.get('趋势稳定', 0)  # 0-1
    volume_explosion = v6_result.get('量能爆发', 0)  # 0-1
    compression_score = v6_result.get('压缩度', 0)  # 0-1
    squeeze_prob = v6_result.get('洗盘概率', 0)  # 0-1

    # =========================
    # 自动选择纯度最高的主题
    # =========================
    if not theme and stock_info:
        theme = _find_best_theme(stock_info)

    # =========================
    # 1. 主题真实性（防止蹭概念）
    # =========================
    theme_confidence = calc_theme_confidence(stock_info, theme) if theme else 30

    # =========================
    # 2. 主题强度 + 主线共振
    # =========================
    theme_strength_bonus = 1.0
    theme_rank_bonus = 0
    mainline_resonance = 0  # 新增：主线共振加分
    if theme:
        try:
            theme_data = das.read_theme_analysis()
            if theme_data:
                for t in theme_data.get('themes', []):
                    if t.get('theme_name') == theme:
                        theme_score = t.get('trend_score', 0)
                        # 主题基础强度
                        if theme_score >= 80:
                            theme_strength_bonus = 1.2  # 降低从1.3降到1.2
                        elif theme_score >= 60:
                            theme_strength_bonus = 1.1
                        elif theme_score >= 40:
                            theme_strength_bonus = 1.0
                        else:
                            theme_strength_bonus = 0.95
                        
                        # 主线共振：如果趋势/情绪/成交量共振
                        trend = t.get('trend', 0)
                        sentiment = t.get('sentiment', 0)
                        vol_increase = t.get('volume_increase', 0)
                        if trend > 70 and sentiment > 70 and vol_increase > 0:
                            mainline_resonance = 5  # 三个共振加5分
                        elif trend > 60 or sentiment > 60:
                            mainline_resonance = 3  # 两个强共振加3分
                        
                        break
        except Exception as e:
            pass

    # =========================
    # 3. 动量维度捕捉启动股
    # =========================
    momentum_score = _calc_momentum_score(df)

    # =========================
    # 4. 压缩+洗盘统一建模
    # =========================
    squeeze_compression_score = _calc_squeeze_compression(df, compression_score, squeeze_prob)

    # =========================
    # 5. V6基础分（重新设计权重）
    # =========================
    # V6技术指标转为基础分（0-100）
    v6_base_score = (
        trend_probability * 30 +      # 趋势概率占30%
        breakout_strength * 25 +      # 突破强度占25%
        money_momentum * 20 +         # 资金动量占20%
        volume_explosion * 15 +       # 量能爆发占15%
        trend_stability * 10          # 趋势稳定占10%
    )

    # =========================
    # 6. 风险调整
    # =========================
    risk_adjustment = 0
    if fail_prob < 0.2:  # 极低失败率：加分
        risk_adjustment = 15
    elif fail_prob < 0.35:
        risk_adjustment = 10
    elif fail_prob < 0.45:
        risk_adjustment = 5
    elif fail_prob < 0.55:
        risk_adjustment = -3  # 从0改为稍微降3分
    elif fail_prob < 0.65:
        risk_adjustment = -10  # 从-8提高到-10
    else:
        risk_adjustment = -18  # 从-15提高到-18

    # =========================
    # 7. V7综合评分
    # =========================
    base_with_theme = v6_base_score * theme_strength_bonus
    momentum_bonus = momentum_score * 15
    squeeze_bonus = squeeze_compression_score * 10
    theme_purity_bonus = theme_confidence * 0.15  # 从10提高到15，注意这里是0.15（百分比系数）
    v7_total = (
        base_with_theme +
        momentum_bonus +
        squeeze_bonus +
        theme_purity_bonus +
        mainline_resonance +
        risk_adjustment
    )

    # 确保在0-100范围内
    v7_total = np.clip(v7_total, 0, 100)

    # =========================
    # 输出结果
    # =========================
    return {
        # V6技术指标
        "趋势概率": round(trend_probability, 4),
        "失败概率": round(fail_prob, 4),
        "洗盘概率": round(squeeze_prob, 4),
        "交易优势": round(v6_result.get('交易优势', 0), 4),
        "趋势强度": round(v6_result.get('趋势强度', 0), 3),
        "趋势稳定": round(trend_stability, 3),
        "资金动量": round(money_momentum, 3),
        "突破强度": round(breakout_strength, 3),
        "压缩度": round(compression_score, 3),
        "量能爆发": round(volume_explosion, 3),
        "风险等级": v6_result.get('风险等级', '低'),

        # V7新指标
        "所属主题": theme,
        "主题纯度": round(theme_confidence, 2),
        "主题强化系数": round(theme_strength_bonus, 2),
        "主线共振加分": round(mainline_resonance, 2),
        "动量得分": round(momentum_score, 2),
        "压缩洗盘得分": round(squeeze_compression_score, 2),
        "风险调整": round(risk_adjustment, 2),

        # V7总分
        "V7总评分": round(v7_total, 2)
    }


# =========================================================
# V7.5 综合评分系统
# =========================================================
def calc_dual_layer_score_v75(df, ts_code='', stock_info=None, theme=''):
    """
    V7.5综合评分系统 - 从V7函数引入所有指标再计算V7.5总分
    
    V7.5在V7基础上叠加：
    - 位置因子（position_factor）：120日高低位位置
    - 龙头因子（leader_factor）：趋势+资金+概率综合
    - 主题排名加成（theme_rank_bonus）：核心公司额外加分
    
    从V7引入的指标：
    - 基础技术指标：趋势概率、突破强度、资金动量等
    - V7复合指标：动量得分、压缩洗盘得分、主线共振加分、主题强化系数
    """

    # =========================
    # 从V7获取所有指标
    # =========================
    v7_result = calc_dual_layer_score_v7(df, ts_code=ts_code, stock_info=stock_info, theme=theme)
    
    # 计算涨跌幅（用于返回给AI报告）
    C = df['close']
    if len(C) >= 2:
        today_pct = ((C.iloc[-1] / C.iloc[-2]) - 1) * 100
    else:
        today_pct = 0.0

    # V7基础技术指标（0-1范围）
    trend_probability = float(v7_result.get('趋势概率', 0.5))
    fail_prob = float(v7_result.get('失败概率', 0.5))
    breakout_strength = float(v7_result.get('突破强度', 0.5))
    money_momentum = float(v7_result.get('资金动量', 0.5))
    trend_stability = float(v7_result.get('趋势稳定', 0.5))
    volume_explosion = float(v7_result.get('量能爆发', 0.5))
    compression_score = float(v7_result.get('压缩度', 0.5))
    trend_strength = float(v7_result.get('趋势强度', 0.5))

    # V7复合指标
    theme_confidence = float(v7_result.get('主题纯度', 30))
    theme_strength_bonus = float(v7_result.get('主题强化系数', 1.0))
    mainline_resonance = float(v7_result.get('主线共振加分', 0))
    momentum_score = float(v7_result.get('动量得分', 0))
    squeeze_compression_score = float(v7_result.get('压缩洗盘得分', 0))
    theme = v7_result.get('所属主题', theme)

    # =========================
    # V7.5独有：位置因子
    # =========================
    position_factor = calc_position_factor(df)

    # =========================
    # V7.5独有：龙头因子
    # =========================
    leader_factor = (
        trend_strength * 0.40 +
        money_momentum * 0.35 +
        trend_probability * 0.25
    )

    # =========================
    # V7.5独有：主题排名加成
    # =========================
    theme_rank_bonus = 0
    if stock_info and theme:
        try:
            cfg_path = os.path.join(BASE_DIR, 'theme.json')
            if os.path.exists(cfg_path):
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    theme_cfg = json.load(f).get('HOT_THEMES', {})
                if theme in theme_cfg:
                    core_companies = theme_cfg[theme].get('core_companies', [])
                    if ts_code in core_companies:
                        theme_rank_bonus = 15  # 核心公司额外15分
        except:
            pass

    # ====================
    # V8.0 综合评分（优化版）
    # ====================

    # 第一部分：基础线性评分（保留原有框架，微调权重）
    base_score = (
        trend_strength * 16 +           # 微降，给交互项留空间
        trend_probability * 12 +
        money_momentum * 12 +
        breakout_strength * 10 +
        volume_explosion * 8 +
        trend_stability * 6 +
        compression_score * 4 +
        momentum_score * 10 +
        squeeze_compression_score * 6 +
        mainline_resonance * 1 +
        position_factor * 6 +           # 权重提升：4→6
        leader_factor * 18
    )

    # 第二部分：因子交互共振项（新增）
    synergy_bonus = (
        (compression_score / 4) * (leader_factor / 18) * 15 +
        (squeeze_compression_score / 6) * volume_explosion * 12 +
        (leader_factor / 18) * (theme_strength_bonus - 1) * 10
    )

    # 第四部分：非线性风险惩罚（优化）
    if fail_prob < 0.3:
        risk_penalty = fail_prob * 3
    elif fail_prob < 0.5:
        risk_penalty = 0.9 + (fail_prob - 0.3) * 10
    else:
        risk_penalty = 2.9 + (fail_prob - 0.5) * 20

    # 第五部分：主题置信度门控（优化）
    if theme_confidence < 30:
        confidence_gate = 0.7
    elif theme_confidence >= 70:
        confidence_gate = 1.1
    else:
        confidence_gate = 1.0

    # 汇总
    v80_raw = (
        base_score 
        + synergy_bonus 
        - risk_penalty
    )

    # 主题强度放大 + 置信度门控
    v75_total = v80_raw * theme_strength_bonus * confidence_gate

    # 确保范围
    v75_total = np.clip(v75_total, 0, 100)
    
    # =========================
    # 输出结果
    # =========================
    return {
        # V7技术指标
        "趋势概率": round(trend_probability, 4),
        "失败概率": round(fail_prob, 4),
        "洗盘概率": round(v7_result.get('洗盘概率', 0), 4),
        "交易优势": round(v7_result.get('交易优势', 0), 4),
        "趋势强度": round(trend_strength, 3),
        "趋势稳定": round(trend_stability, 3),
        "资金动量": round(money_momentum, 3),
        "突破强度": round(breakout_strength, 3),
        "压缩度": round(compression_score, 3),
        "量能爆发": round(volume_explosion, 3),
        "风险等级": v7_result.get('风险等级', '低'),

        # V7复合指标
        "所属主题": theme,
        "主题纯度": round(theme_confidence, 2),
        "主题强化系数": round(theme_strength_bonus, 2),
        "主线共振加分": round(mainline_resonance, 2),
        "动量得分": round(momentum_score, 2),
        "压缩洗盘得分": round(squeeze_compression_score, 2),
        
        # V7.5独有指标
        "龙头因子": round(leader_factor, 3),
        "主题排名加成": round(theme_rank_bonus, 2),
        "位置因子": round(position_factor, 3),
        "涨跌幅": round(today_pct, 2),  # 添加涨跌幅给AI报告

        # V7.5总分
        "V7总评分": round(v75_total, 2)
    }



def calc_position_factor(df):

    low120 = df["low"].tail(120).min()
    high120 = df["high"].tail(120).max()

    close = df["close"].iloc[-1]

    pos = (close - low120) / (high120 - low120)

    if pos < 0.30:
        score = 0.2

    elif pos < 0.50:
        score = 0.4

    elif pos < 0.70:
        score = 0.7

    elif pos < 0.90:
        score = 1.0

    else:
        score = 0.8

    return score


def _calc_momentum_score(df):
    """
    新增：动量评分，捕捉启动股
    """
    C = df['close']
    VOL = df['vol']

    score = 0

    # 1. 5日价格动量
    if len(C) >= 5:
        pct5 = (C.iloc[-1] / C.iloc[-5]) - 1
        if pct5 > 0.05:
            score += 0.3
        elif pct5 > 0.02:
            score += 0.2
        elif pct5 > 0:
            score += 0.1

    # 2. 20日趋势
    if len(C) >= 20:
        pct20 = (C.iloc[-1] / C.iloc[-20]) - 1
        if pct20 > 0.15:
            score += 0.25
        elif pct20 > 0.08:
            score += 0.15
        elif pct20 > 0:
            score += 0.05

    # 3. 量能配合
    if len(VOL) >= 5:
        vol_ratio = VOL.tail(5).mean() / (VOL.tail(20).mean() + 1e-6)
        if vol_ratio > 1.5:
            score += 0.2
        elif vol_ratio > 1.2:
            score += 0.1
        elif vol_ratio > 1.0:
            score += 0.05

    # 4. 突破近期高点
    if len(C) >= 10:
        recent_high = C.tail(10).max()
        if C.iloc[-1] >= recent_high * 0.98:
            score += 0.25

    # 归一化到0-1
    return np.clip(score, 0, 1)

def _calc_squeeze_compression(df, compression_score, squeeze_prob):
    """
    新增：压缩+洗盘统一建模
    组合压缩度和洗盘概率
    """
    # 压缩度和洗盘概率的乘积，重点是高压缩度 + 高洗盘概率 = 好启动机会
    score = 0

    # 组合得分
    if compression_score >= 0.8 and squeeze_prob >= 0.5:
        score += 0.4
    elif compression_score >= 0.6 and squeeze_prob >= 0.4:
        score += 0.3
    elif compression_score >= 0.5:
        score += 0.2

    # 加上两个指标平均
    avg = (compression_score + squeeze_prob) / 2
    score += avg * 0.5

    return np.clip(score, 0, 1)


def _find_best_theme(stock_info):
    """
    自动选择纯度最高的主题
    遍历所有主题配置，计算个股与每个主题的纯度，返回最高的主题名
    """
    if not stock_info:
        return ''

    try:
        # 加载主题配置
        cfg_path = os.path.join(BASE_DIR, 'theme.json')
        if not os.path.exists(cfg_path):
            return ''

        with open(cfg_path, 'r', encoding='utf-8') as f:
            theme_cfg = json.load(f).get('HOT_THEMES', {})

        best_theme = ''
        best_confidence = 0

        # 遍历所有主题，找纯度最高的
        for theme_name in theme_cfg.keys():
            confidence = calc_theme_confidence(stock_info, theme_name)
            if confidence > best_confidence:
                best_confidence = confidence
                best_theme = theme_name

        return best_theme

    except Exception as e:
        print(f"[V7自动主题] 选择失败: {e}")
        return ''


def _calc_theme_rank_bonus(ts_code, theme):
    """
    主题排名加成（0-100）
    根据股票在其所属主题中的排名进行加成
    """
    if not ts_code or not theme:
        return 0

    try:
        # 读取主题分析数据
        theme_data = das.read_theme_analysis(TRADE_DATE)
        avg_data = das.read_60day_avg_trend_scores(TRADE_DATE)

        if not theme_data or not avg_data:
            return 0

        # 获取该主题的趋势评分
        theme_score = 0
        for t in theme_data.get('themes', []):
            if t.get('theme_name') == theme:
                theme_score = t.get('trend_score', 0)
                break

        if theme_score == 0:
            return 0

        # 根据主题热度给予排名加成
        # 主题越热，排名靠前的股票加成越高
        if theme_score >= 80:
            # 高热主题，前排股票额外加分
            return 20
        elif theme_score >= 60:
            return 10
        else:
            return 0

    except Exception as e:
        print(f"[V7主题排名] 计算失败: {e}")
        return 0


def _calc_risk_control_score(v6_result):
    """
    风险控制评分（5%权重）
    基于V6结果中的风险指标进行评分
    """
    if not v6_result:
        return 50

    try:
        # 风险等级评分
        risk_level = v6_result.get('风险等级', '低')
        if risk_level == '低':
            risk_level_score = 100
        elif risk_level == '中':
            risk_level_score = 70
        elif risk_level == '高':
            risk_level_score = 40
        else:  # 极高
            risk_level_score = 20

        # 失败概率评分（失败概率越高，分数越低）
        fail_prob = v6_result.get('失败概率', 0.5)
        fail_score = (1 - fail_prob) * 100

        # 交易优势评分（交易优势越高，分数越高）
        edge_score = v6_result.get('交易优势', 0)
        edge_score_normalized = (edge_score + 1) * 50  # 转换到0-100

        # 综合风险控制评分
        risk_score = (
            risk_level_score * 0.4 +
            fail_score * 0.4 +
            edge_score_normalized * 0.2
        )

        return max(min(risk_score, 100), 0)

    except Exception as e:
        print(f"[V7风险控制] 计算失败: {e}")
        return 50


def _calc_theme_score(ts_code, stock_info):
    """
    主题匹配评分（20%权重）
    根据股票是否属于当前热门主题进行评分
    """
    if stock_info is None:
        return 30  # 默认低分

    try:
        # 获取当前热门主题
        theme_data = das.read_theme_analysis()
        if not theme_data or not theme_data.get('themes'):
            return 30

        # 获取短线TOP5和中线TOP5主题
        themes = theme_data['themes']
        hot_themes = set()
        for t in themes:
            theme_name = t.get('theme_name', '')
            trend_score = t.get('trend_score', 0)
            avg_trend_score = t.get('avg_trend_score', 0)
            # 同时考虑短线和中线热度
            if trend_score > 60 or avg_trend_score > 60:
                hot_themes.add(theme_name)

        # 获取股票所属主题
        stock_industry = stock_info.get('industry', '')
        stock_concept = stock_info.get('concept', [])

        # 加载主题配置
        cfg_path = os.path.join(BASE_DIR, 'theme.json')
        if not os.path.exists(cfg_path):
            return 30

        with open(cfg_path, 'r', encoding='utf-8') as f:
            theme_cfg = json.load(f).get('HOT_THEMES', {})

        # 检查股票是否匹配热门主题
        matched = False
        match_count = 0
        for theme_name, cfg in theme_cfg.items():
            if theme_name not in hot_themes:
                continue

            # 检查行业匹配
            industries = cfg.get('industry', [])
            if stock_industry in industries:
                matched = True
                match_count += 1

            # 检查概念匹配
            concepts = cfg.get('concept', [])
            for c in concepts:
                if c in stock_concept:
                    matched = True
                    match_count += 1

        if not matched:
            return 25  # 不匹配任何热门主题

        # 匹配度评分
        if match_count >= 3:
            return 85
        elif match_count >= 2:
            return 70
        else:
            return 55

    except Exception as e:
        print(f"[V7主题评分] 计算失败: {e}")
        return 30


def _calc_industry_position_score(df, stock_info):
    """
    产业链地位评分（15%权重）
    根据股票在行业中的地位进行评分
    """
    if df is None or len(df) < 60:
        return 50  # 数据不足给中等分

    try:
        C = df['close']
        VOL = df['vol']

        # 1. 相对行业强弱（与大盘相比）
        # 使用价格动量
        ret_20d = (C.iloc[-1] / C.iloc[-20] - 1) if len(C) >= 20 else 0
        ret_60d = (C.iloc[-1] / C.iloc[-60] - 1) if len(C) >= 60 else 0

        # 2. 成交额在行业中的排名（用换手率代理）
        turnover_rate = VOL.tail(5).mean() / VOL.tail(60).mean() if len(VOL) >= 60 else 1

        # 3. 均线多头排列程度
        ma5 = C.rolling(5).mean()
        ma10 = C.rolling(10).mean()
        ma20 = C.rolling(20).mean()
        ma60 = C.rolling(60).mean()

        # 多头排列：ma5 > ma10 > ma20 > ma60
        多头_count = 0
        if len(C) >= 60:
            if ma5.iloc[-1] > ma10.iloc[-1]:
                多头_count += 1
            if ma10.iloc[-1] > ma20.iloc[-1]:
                多头_count += 1
            if ma20.iloc[-1] > ma60.iloc[-1]:
                多头_count += 1

        # 4. 行业地位综合评分
        score = 50  # 基础分

        # 动量加分
        if ret_20d > 0.05:
            score += 10
        elif ret_20d > 0.10:
            score += 15

        if ret_60d > 0.10:
            score += 10

        # 成交活跃加分
        if turnover_rate > 1.5:
            score += 10
        elif turnover_rate > 1.2:
            score += 5

        # 多头排列加分
        score += 多头_count * 8

        return min(score, 100)

    except Exception as e:
        print(f"[V7产业链评分] 计算失败: {e}")
        return 50


def _calc_mainline_heat_score(stock_info):
    """
    主线热度评分（10%权重）
    根据股票所属主线板块的热度进行评分
    """
    if stock_info is None:
        return 30

    try:
        # 获取主线状态
        theme_data = das.read_theme_analysis(TRADE_DATE)
        if not theme_data or not theme_data.get('themes'):
            return 30

        # 获取60日平均趋势评分（代表主线持续热度）
        avg_data = das.read_60day_avg_trend_scores(TRADE_DATE)
        if not avg_data or not avg_data.get('themes'):
            return 30

        # 建立主题到热度的映射
        theme_heat_map = {}
        for t in avg_data.get('themes', []):
            theme_heat_map[t['theme_name']] = t.get('avg_trend_score', 50)

        # 获取股票行业
        stock_industry = stock_info.get('industry', '')

        # 加载主题配置
        cfg_path = os.path.join(BASE_DIR, 'theme.json')
        if not os.path.exists(cfg_path):
            return 30

        with open(cfg_path, 'r', encoding='utf-8') as f:
            theme_cfg = json.load(f).get('HOT_THEMES', {})

        # 查找股票所属主题的热度
        max_heat = 0
        for theme_name, cfg in theme_cfg.items():
            industries = cfg.get('industry', [])
            if stock_industry in industries:
                heat = theme_heat_map.get(theme_name, 50)
                max_heat = max(max_heat, heat)

        # 热度评分转换
        if max_heat == 0:
            return 30

        # 热度80以上为高热，给80-100分
        if max_heat >= 80:
            return min(80 + (max_heat - 80) * 0.5, 100)
        elif max_heat >= 60:
            return 60 + (max_heat - 60) * 0.5
        else:
            return max_heat

    except Exception as e:
        print(f"[V7主线热度] 计算失败: {e}")
        return 30


def _calc_capital_behavior_score(df):
    """
    资金行为评分（5%权重）
    根据资金流向和机构行为进行评分
    """
    if df is None or len(df) < 20:
        return 50

    try:
        VOL = df['vol']
        C = df['close']

        # 1. 成交量趋势（缩量还是放量）
        vol_5d = VOL.tail(5).mean()
        vol_20d = VOL.tail(20).mean()

        if vol_20d == 0:
            vol_ratio = 1
        else:
            vol_ratio = vol_5d / vol_20d

        # 2. 资金净流入代理（用价格涨跌和成交量配合判断）
        price_chg = (C.iloc[-1] / C.iloc[-2] - 1) if len(C) >= 2 else 0

        # 3. 大单买入代理（成交量突增）
        vol_burst = 1 if vol_ratio > 1.5 else 0

        # 4. 综合评分
        score = 50  # 基础分

        # 温和放量上涨
        if 1.2 <= vol_ratio <= 2.0 and price_chg > 0:
            score += 15

        # 缩量调整
        if vol_ratio < 0.8 and price_chg < 0:
            score += 10  # 主力控盘迹象

        # 放量突破
        if vol_ratio > 2.0 and price_chg > 0.02:
            score += 20

        # 放量滞涨（不好）
        if vol_ratio > 1.5 and price_chg < 0:
            score -= 10

        return max(min(score, 100), 10)  # 限制在10-100

    except Exception as e:
        print(f"[V7资金行为] 计算失败: {e}")
        return 50


# =========================================================
# 游资最强开仓算法 V8
# =========================================================
def calc_hot_money_open_score_v9(v7_result, df, stock_info, theme=''):
    """
    游资最强开仓评分算法 V9.7
    
    核心逻辑（V9.7升级）：
    1. 主线强度（22%）：主题热度+主题排名
    2. 龙头地位（18%）：趋势+资金
    3. 成交额排名（18%）：近20日成交额比率（放量程度）
    4. 资金体量（15%）：市值+承接力（体现"有承接的小票"）
    5. 主题纯度（13%）：从V7结果获取
    6. 结构位置（9%）：启动型/加速型/分歧型
    7. 突破强度（3%）：从V7结果获取
    8. 量能爆发（2%）：从V7结果获取
    
    资金体量因子逻辑：
    - 小票（<50亿）：需要高承接力（换手率>5%）才能得高分
    - 中小票（50-100亿）：需要中等承接力（换手率>3%）
    - 中票（100-200亿）：需要基本承接力（换手率>2%）
    - 大票（>200亿）：承接力要求较低（换手率>1.5%即可）
    
    加分项：
    - 市值加成：中军（>150亿）+10~15分，小票加分
    - 板块第一强：当日涨幅板块第一+8分
    - 低失败概率：+3~5分
    - 主线排名：TOP1:+10分，TOP2-3:+8分
    
    返回：
        - open_score: 开仓评分 (0-100)
        - structure_type: 结构类型（启动型/加速型/高位分歧）
        - recommendation: 推荐理由
    """
    try:
        if not v7_result or df is None:
            return 0, "数据不足", ""
        
        # 确保df是DataFrame类型
        if not isinstance(df, pd.DataFrame):
            return 0, "数据不足", ""
        
        if len(df) < 20:
            return 0, "数据不足", ""
        
        # 重置索引避免问题
        df = df.reset_index(drop=True)
        
        C = df['close'].values  # 转为numpy数组
        VOL = df['vol'].values
        
        # =========================
        # 1. 获取V7基础指标
        # =========================
        volume_explosion = float(v7_result.get('量能爆发', 0))  # 0-1
        breakout_strength = float(v7_result.get('突破强度', 0))  # 0-1
        fail_prob = float(v7_result.get('失败概率', 0.5))  # 0-1
        theme_confidence = float(v7_result.get('主题纯度', 30))  # 0-100
        
        # 今日涨幅
        if len(C) >= 2:
            today_pct = float((C[-1] / C[-2] - 1) * 100)
        else:
            today_pct = 0
        
        # =========================
        # 2. 主题热度评分（theme_score）
        # =========================
        theme_rank_score = 50  # 默认值
        theme_name = v7_result.get('所属主题', theme)
        theme_sentiment_score = 0  # 主题情绪分
        
        if theme_name:
            theme_name = str(theme_name).strip()
            try:
                theme_data = das.read_theme_analysis(TRADE_DATE)
                if theme_data and theme_data.get('themes'):
                    for t in theme_data['themes']:
                        if t.get('theme_name') == theme_name:
                            # 使用综合分作为主题热度评分（0-100）
                            composite_score = float(t.get('composite_score', 50))
                            theme_rank_score = min(100, max(0, composite_score))
                            # 获取主题情绪分（如果有）
                            theme_sentiment_score = float(t.get('sentiment_score', 0))
                            break
                    else:
                        theme_rank_score = 50
            except Exception as e:
                print(f"[开仓评分V9] 获取主题热度失败: {e}")
                theme_rank_score = 50
        
        # =========================
        # 3. 主题纯度评分（purity_score）
        # =========================
        purity_score = theme_confidence  # 0-100
        
        # =========================
        # 4. 龙头地位评分（leader_score）
        # =========================
        money_momentum = float(v7_result.get('资金动量', 0.5))
        trend_stability = float(v7_result.get('趋势稳定', 0.5))
        trend_probability = float(v7_result.get('趋势概率', 0.5))
        trend_strength = float(v7_result.get('趋势强度', 0.5))
        
        leader_score = (
            trend_strength * 0.40 +      # 趋势强度 40%
            money_momentum * 0.35 +      # 资金动量 35%
            trend_stability * 0.20 +     # 趋势稳定 20%
            trend_probability * 0.05     # 趋势概率 5%
        ) * 100  # 归一化到0-100
        
        # =========================
        # 5. 成交额排名评分（turnover_rank_score）- 最重要独立因子
        # =========================
        turnover_rank_score = 50  # 默认中等
        try:
            if len(df) >= 20 and 'vol' in df.columns and 'close' in df.columns:
                # 计算近20日成交额
                recent_df = df.tail(20).copy()
                recent_df['turnover'] = recent_df['vol'] * recent_df['close']
                
                # 今日成交额
                today_turnover = recent_df['turnover'].iloc[-1]
                
                # 20日平均成交额
                ma20_turnover = recent_df['turnover'].mean()
                
                # 换手率比率：今日成交额 / 20日均成交额
                turnover_ratio = today_turnover / ma20_turnover if ma20_turnover > 0 else 1.0
                
                # 成交额比率评分：比率越高说明当日放量越明显
                if turnover_ratio >= 3:
                    turnover_rank_score = 100
                elif turnover_ratio >= 2:
                    turnover_rank_score = 85
                elif turnover_ratio >= 1.5:
                    turnover_rank_score = 70
                elif turnover_ratio >= 1:
                    turnover_rank_score = 55
                else:
                    turnover_rank_score = 35
        except Exception:
            turnover_rank_score = 50
        
        # =========================
        # 5. 结构位置评分（structure_score）
        # =========================
        close_series = df['close']
        MA20 = float(close_series.rolling(20).mean().iloc[-1])
        MA60 = float(close_series.rolling(60).mean().iloc[-1])
        HHV20 = float(close_series.tail(20).max())
        LLV20 = float(close_series.tail(20).min())
        
        current_price = float(C[-1])
        price_position = current_price / MA20 if MA20 > 0 else 1.0
        
        structure_score = 0
        structure_type = "未知"
        structure_desc = ""
        
        # 判断结构类型
        if current_price >= HHV20 * 0.95 and (1 < today_pct and today_pct <= 10):
            structure_type = "🟢启动型"
            structure_score = 100
            structure_desc = "首板/突破形态，次日惯性较强"
        elif price_position > 1.05 and MA20 > MA60 and (0 < today_pct and today_pct <= 7):
            structure_type = "🟡加速型"
            structure_score = 75
            structure_desc = "趋势加速中，稳健跟进"
        elif current_price > HHV20 * 1.08 or today_pct > 10:
            structure_type = "🔴高位分歧"
            structure_score = 50
            structure_desc = "高位分歧，风险较大"
        elif price_position < 1.02 and volume_explosion < 0.3 and today_pct > -3:
            structure_type = "🟡调整型"
            structure_score = 20
            structure_desc = "缩量调整，关注均线支撑"
        else:
            structure_type = "⚪震荡型"
            structure_score = 40
            structure_desc = "震荡整理，需观察方向"
        
        # =========================
        # 6. 突破强度评分（breakout_score）
        # =========================
        breakout_score = breakout_strength * 100  # 0-1 -> 0-100
        
        # =========================
        # 7. 量能爆发评分（volume_score）
        # =========================
        volume_score = volume_explosion * 100  # 0-1 -> 0-100
        
        # =========================
        # 8. 资金体量因子（capital_volume_score）- 体现"有承接的小票"
        # =========================
        capital_volume_score = 50  # 默认中等
        if stock_info and len(df) >= 20:
            market_cap = stock_info.get('total_market_cap') or stock_info.get('market_cap')
            if market_cap and market_cap > 0:
                # 统一转换为亿元（复用之前的转换逻辑）
                if market_cap > 1e12:
                    market_cap_yi = market_cap / 1e8
                elif market_cap > 1e8:
                    market_cap_yi = market_cap / 1e8
                else:
                    market_cap_yi = market_cap / 1e8
                
                # 直接使用 df 中的数据（get_hist_data 已获取）
                recent_df = df.tail(20).copy()
                
                # 优先使用 amount 字段（单位：千元），异常时用 vol*close 计算
                if 'amount' in recent_df.columns and recent_df['amount'].iloc[-1] > 0:
                    amount = recent_df['amount'].iloc[-1]
                    # 验证 amount 是否合理（避免异常值）
                    vol = recent_df['vol'].iloc[-1]
                    close = recent_df['close'].iloc[-1]
                    expected_amount = vol * close * 100 / 1000  # 转为千元
                    
                    if abs(amount - expected_amount) / expected_amount < 2.0:  # 允许2倍误差
                        today_turnover_yi = amount / 1e3  # 千元转亿元
                    else:
                        # amount异常，用计算值
                        today_turnover_yi = vol * close * 100 / 1e8
                else:
                    # 没有amount字段，用vol*close计算
                    today_turnover_yi = recent_df['vol'].iloc[-1] * recent_df['close'].iloc[-1] * 100 / 1e8
                
                # 计算承接力：当日成交额 / 总市值（换手率）
                turnover_ratio = today_turnover_yi / market_cap_yi if market_cap_yi > 0 else 0
                
                # 市值分档 + 承接力评分（使用亿元）
                if market_cap_yi < 50:
                    if turnover_ratio >= 0.05:
                        capital_volume_score = 100
                    elif turnover_ratio >= 0.03:
                        capital_volume_score = 85
                    elif turnover_ratio >= 0.02:
                        capital_volume_score = 70
                    else:
                        capital_volume_score = 40
                elif market_cap_yi < 100:
                    if turnover_ratio >= 0.04:
                        capital_volume_score = 90
                    elif turnover_ratio >= 0.025:
                        capital_volume_score = 75
                    elif turnover_ratio >= 0.015:
                        capital_volume_score = 60
                    else:
                        capital_volume_score = 45
                elif market_cap_yi < 200:
                    if turnover_ratio >= 0.03:
                        capital_volume_score = 85
                    elif turnover_ratio >= 0.02:
                        capital_volume_score = 70
                    else:
                        capital_volume_score = 50
                else:
                    if turnover_ratio >= 0.025:
                        capital_volume_score = 80
                    elif turnover_ratio >= 0.015:
                        capital_volume_score = 65
                    else:
                        capital_volume_score = 55
                

        
        # =========================
        # 9. V9.7基础评分公式
        # =========================
        open_score = (
            theme_rank_score * 0.22 +      # 主线强度 22%（降低）
            leader_score * 0.18 +          # 龙头地位 18%（降低）
            turnover_rank_score * 0.18 +  # 成交额排名 18%（降低）
            capital_volume_score * 0.15 + # 资金体量 15%（新增）
            purity_score * 0.13 +          # 主题纯度 13%（降低）
            structure_score * 0.09 +       # 结构位置 9%（降低）
            breakout_score * 0.03 +        # 突破强度 3%（降低）
            volume_score * 0.02           # 量能爆发 2%（降低）
        )
        
        # =========================
        # 9. 市值加成（中军/小票区分）
        # =========================
        market_cap_bonus = 0
        if stock_info:
            # 从stock_info获取市值
            market_cap = stock_info.get('total_market_cap') or stock_info.get('market_cap')
            if market_cap and market_cap > 0:
                # 转换为亿元
                if market_cap > 1e12:  # 万亿转亿
                    market_cap_yi = market_cap / 1e8
                elif market_cap > 1e8:  # 已经是亿
                    market_cap_yi = market_cap / 1e8
                else:  # 元转亿
                    market_cap_yi = market_cap / 1e8
                
                # 中军加成：150亿~500亿 +10分，500亿~1000亿 +12分，>1000亿 +15分
                if market_cap_yi >= 1000:
                    market_cap_bonus = 15
                elif market_cap_yi >= 500:
                    market_cap_bonus = 12
                elif market_cap_yi >= 150:
                    market_cap_bonus = 10
                # 小票加成：<50亿 +5分（弹性更好）
                elif market_cap_yi < 50:
                    market_cap_bonus = 5
        
        open_score += market_cap_bonus
        
        # =========================
        # 10. 板块第一强加成
        # =========================
        theme_first_bonus = 0
        if theme_name:
            try:
                # 获取板块内涨幅最强的股票
                theme_data = das.read_theme_analysis(TRADE_DATE)
                if theme_data and theme_data.get('themes'):
                    for t in theme_data['themes']:
                        if t.get('theme_name') == theme_name:
                            # 检查是否为板块第一强
                            top_stocks = t.get('top_stocks', [])
                            stock_name = stock_info.get('name', '') if stock_info else ''
                            if top_stocks and len(top_stocks) > 0:
                                # top_stocks 格式可能是 [(name, pct), ...]
                                if isinstance(top_stocks[0], tuple):
                                    if stock_name and stock_name in str(top_stocks[0][0]):
                                        theme_first_bonus = 8
                                elif top_stocks[0].get('name') == stock_name:
                                    theme_first_bonus = 8
                            break
            except Exception:
                pass
        
        open_score += theme_first_bonus
        
        # =========================
        # 11. 失败概率微调（降级）
        # =========================
        fail_bonus = 0
        if fail_prob < 0.15:
            fail_bonus = 5
        elif fail_prob < 0.25:
            fail_bonus = 3
        
        open_score += fail_bonus
        
        # =========================
        # 12. 主线排名加分（按综合分排名）
        # =========================
        rank_bonus = 0
        if theme_name:
            try:
                theme_data = das.read_theme_analysis(TRADE_DATE)
                if theme_data and theme_data.get('themes'):
                    # 按综合分降序排序
                    sorted_themes = sorted(theme_data['themes'], 
                                         key=lambda x: x.get('composite_score', 0), 
                                         reverse=True)
                    # 查找当前主题的排名
                    for idx, t in enumerate(sorted_themes, 1):
                        if t.get('theme_name') == theme_name:
                            # TOP1:10分, TOP2-3:8分, TOP4-5:6分, TOP6-10:4分
                            if idx == 1:
                                rank_bonus = 10
                            elif idx <= 3:
                                rank_bonus = 8
                            elif idx <= 5:
                                rank_bonus = 6
                            elif idx <= 10:
                                rank_bonus = 4
                            break
            except Exception as e:
                print(f"[开仓评分V9] 获取主线排名失败: {e}")
        
        open_score += rank_bonus
        
        # 确保在0-100范围内
        open_score = min(100, max(0, open_score))
        
        # =========================
        # 生成推荐理由
        # =========================
        recommendation = f"{structure_desc}"
        recommendation += f" | 主题{theme_rank_score:.0f}分"
        recommendation += f" | 纯度{purity_score:.0f}分"
        recommendation += f" | 龙头{leader_score:.0f}分"
        recommendation += f" | 成交额{turnover_rank_score:.0f}分"
        recommendation += f" | 资金体量{capital_volume_score:.0f}分"
        recommendation += f" | 结构{structure_score:.0f}分"
        
        # 显示加分项和扣分项
        bonus_parts = []
        if market_cap_bonus > 0:
            bonus_parts.append(f"市值+{market_cap_bonus}分")
        if theme_first_bonus > 0:
            bonus_parts.append(f"板块第一+{theme_first_bonus}分")
        if rank_bonus > 0:
            bonus_parts.append(f"主线排名+{rank_bonus}分")
        if fail_bonus > 0:
            bonus_parts.append(f"低风险+{fail_bonus}分")
        
        if bonus_parts:
            recommendation += f" | 修正:{','.join(bonus_parts)}"
        
        # 显示完整公式
        recommendation += f" | V9.7开仓={open_score:.1f} (主线{theme_rank_score:.0f}×0.22 + 龙头{leader_score:.0f}×0.18 + 成交额{turnover_rank_score:.0f}×0.18 + 资金{capital_volume_score:.0f}×0.15 + 纯度{purity_score:.0f}×0.13 + 结构{structure_score:.0f}×0.09 + 突破{breakout_score:.0f}×0.03 + 量能{volume_score:.0f}×0.02)"
        
        # 特殊标记
        if structure_type == "🟢启动型" and fail_prob < 0.25:
            recommendation = "⭐重点关注: " + recommendation
        elif structure_type == "🔴高位分歧":
            recommendation = "⚠️谨慎: " + recommendation
        
        return round(open_score, 1), structure_type, recommendation
    
    except Exception as e:
        print(f"[开仓评分V9] 异常: {e}")
        import traceback
        traceback.print_exc()
        return 0, "计算异常", ""


def calc_hot_money_open_score(v7_result, df, stock_info, theme=''):
    """
    游资最强开仓评分算法
    
    核心逻辑：
    1. 资金有效性（今天有没有真流入）
    2. 结构位置（决定次日惯性）
    3. 主题强度（V8新增核心）
    
    返回：
        - open_score: 开仓评分 (0-100)
        - structure_type: 结构类型（启动型/加速型/高位分歧）
        - recommendation: 推荐理由
    """
    try:
        if not v7_result or df is None:
            return 0, "数据不足", ""
        
        # 确保df是DataFrame类型
        if not isinstance(df, pd.DataFrame):
            return 0, "数据不足", ""
        
        if len(df) < 20:
            return 0, "数据不足", ""
        
        # 重置索引避免问题
        df = df.reset_index(drop=True)
        
        C = df['close'].values  # 转为numpy数组
        VOL = df['vol'].values
        
        # =========================
        # 1. 资金有效性评分
        # =========================
        volume_explosion = float(v7_result.get('量能爆发', 0))  # 0-1
        breakout_strength = float(v7_result.get('突破强度', 0))  # 0-1
        fail_prob = float(v7_result.get('失败概率', 0.5))  # 0-1
        
        # 今日涨幅
        if len(C) >= 2:
            today_pct = float((C[-1] / C[-2] - 1) * 100)
        else:
            today_pct = 0
        
        # 资金有效性评分
        capital_score = 0
        capital_detail = []
        
        if volume_explosion > 0.30:
            capital_score += 30
            capital_detail.append(f"量能爆发:{volume_explosion:.2f}")
        elif volume_explosion > 0.20:
            capital_score += 20
            capital_detail.append(f"量能良好:{volume_explosion:.2f}")
        
        if breakout_strength > 0.25:
            capital_score += 25
            capital_detail.append(f"突破强度:{breakout_strength:.2f}")
        elif breakout_strength > 0.20:
            capital_score += 15
            capital_detail.append(f"突破良好:{breakout_strength:.2f}")
        
        if today_pct > 3:
            capital_score += 20
            capital_detail.append(f"涨幅:{today_pct:.1f}%")
        elif today_pct > 1:
            capital_score += 10
            capital_detail.append(f"涨幅温和:{today_pct:.1f}%")
        elif today_pct < -3:
            capital_score -= 10
            capital_detail.append(f"跌幅过大:{today_pct:.1f}%")
        
        # 失败概率惩罚（超过40%要谨慎）
        if fail_prob > 0.55:
            capital_score -= 25  # 从-15提高到-25
            capital_detail.append(f"失败概率高:{fail_prob:.1%}")
        elif fail_prob > 0.45:
            capital_score -= 15  # 从-5提高到-15
            capital_detail.append(f"失败概率中等:{fail_prob:.1%}")
        elif fail_prob > 0.35:
            capital_score -= 5  # 新增这个档
            capital_detail.append(f"失败概率一般:{fail_prob:.1%}")
        
        # =========================
        # 2. 结构位置评分
        # =========================
        close_series = df['close']
        MA20 = float(close_series.rolling(20).mean().iloc[-1])
        MA60 = float(close_series.rolling(60).mean().iloc[-1])
        HHV20 = float(close_series.tail(20).max())
        LLV20 = float(close_series.tail(20).min())
        
        current_price = float(C[-1])
        price_position = current_price / MA20 if MA20 > 0 else 1.0
        amp20 = (HHV20 - LLV20) / LLV20 if LLV20 > 0 else 0
        
        structure_score = 0
        structure_type = "未知"
        structure_desc = ""
        
        # 判断结构类型
        # 首板/突破型：价格接近20日高点 + 涨幅适中
        if current_price >= HHV20 * 0.95 and (1 < today_pct and today_pct <= 10):
            structure_type = "🟢启动型"
            structure_score = 35
            structure_desc = "首板/突破形态，次日惯性较强"
        # 加速型：均线多头 + 温和放量
        elif price_position > 1.05 and MA20 > MA60 and (0 < today_pct and today_pct <= 7):
            structure_type = "🟡加速型"
            structure_score = 28
            structure_desc = "趋势加速中，稳健跟进"
        # 高位分歧型：超过20日高点太多 + 大幅波动
        elif current_price > HHV20 * 1.08 or today_pct > 10:
            structure_type = "🔴高位分歧"
            structure_score = 5
            structure_desc = "高位分歧，风险较大"
        # 调整型：缩量回调到均线附近
        elif price_position < 1.02 and volume_explosion < 0.3 and today_pct > -3:
            structure_type = "🟡调整型"
            structure_score = 20
            structure_desc = "缩量调整，关注均线支撑"
        else:
            structure_type = "⚪震荡型"
            structure_score = 15
            structure_desc = "震荡整理，需观察方向"
        
        # =========================
        # 3. 主题强度评分（从主题分析数据动态获取）
        # =========================
        theme_rank_score = 50  # 默认值
        theme_name = v7_result.get('所属主题', theme)
        # 清理主题名称
        if theme_name:
            theme_name = str(theme_name).strip()
        
        # 从主题分析数据获取实际综合分
        if theme_name:
            try:
                theme_data = das.read_theme_analysis(TRADE_DATE)
                if theme_data and theme_data.get('themes'):
                    for t in theme_data['themes']:
                        if t.get('theme_name') == theme_name:
                            # 使用综合分作为主题热度评分（0-100）
                            composite_score = float(t.get('composite_score', 50))
                            theme_rank_score = min(100, max(0, composite_score))
                            break
                    else:
                        # 未找到匹配主题，使用默认值
                        theme_rank_score = 50
            except Exception as e:
                print(f"[开仓评分] 获取主题热度失败: {e}")
                theme_rank_score = 50
        
        # =========================
        # 4. 获取主题纯度
        # =========================
        theme_confidence = float(v7_result.get('主题纯度', 30))  # 0-100
        
        # =========================
        # 5. 计算龙头得分 (leader_score)
        # =========================
        leader_score = 0
        money_momentum = float(v7_result.get('资金动量', 0.5))
        trend_stability = float(v7_result.get('趋势稳定', 0.5))
        trend_probability = float(v7_result.get('趋势概率', 0.5))
        trend_strength = float(v7_result.get('趋势强度', 0.5))
        
        # 龙头得分：强化趋势强度和资金动量
        leader_score = (
            money_momentum * 0.30 +
            trend_strength * 0.35 +  # 新增趋势强度
            trend_stability * 0.20 +
            trend_probability * 0.15
        ) * 100  # 归一化到0-100
        
        # =========================
        # 6. 开仓优先级公式（提高主题纯度和龙头权重）
        # =========================
        # 新公式：theme_confidence和leader_score各占30%
        # theme_rank: 20%
        # theme_confidence: 30% (提高)
        # leader_score: 30% (提高)
        # structure_score: 10%
        # breakout_strength: 5%
        # volume_burst: 5%
        
        # 归一化指标
        normalized_theme_rank = theme_rank_score  # 主题排名已经是0-100
        normalized_theme_confidence = theme_confidence  # 主题纯度已经是0-100
        normalized_structure_score = structure_score  # 结构得分已经是0-100
        normalized_breakout = breakout_strength * 100  # 0-1 -> 0-100
        normalized_volume = volume_explosion * 100  # 0-1 -> 0-100
        

        # 计算开仓优先级得分（基础分）
        raw_score = (
            normalized_theme_rank * 0.20 +
            normalized_theme_confidence * 0.30 +  # 提高到30%
            leader_score * 0.30 +  # 提高到30%
            normalized_structure_score * 0.10 +
            normalized_breakout * 0.05 +
            normalized_volume * 0.05
        )
        # 放大评分，让最高分可以超过70-80分
        open_score = raw_score * 1.15
        
        # 生成推荐理由
        recommendation = f"{structure_desc}"
        if capital_detail:
            recommendation += f" | {'/'.join(capital_detail[:2])}"
        # 显示开仓优先级公式各分项
        recommendation += f" | 开仓评分={open_score:.1f} (基础分{raw_score:.1f}×1.15 = 主题{normalized_theme_rank:.0f}×0.20 + 纯度{normalized_theme_confidence:.0f}×0.30 + 龙头{leader_score:.0f}×0.30 + 结构{normalized_structure_score:.0f}×0.10 + 突破{normalized_breakout:.0f}×0.05 + 量能{normalized_volume:.0f}×0.05)"
        
        # 特殊标记
        if structure_type == "🟢启动型" and fail_prob < 0.4:
            recommendation = "⭐重点关注: " + recommendation
        elif structure_type == "🔴高位分歧":
            recommendation = "⚠️谨慎: " + recommendation
        
        return round(open_score, 1), structure_type, recommendation
    
    except Exception as e:
        print(f"[开仓评分] 异常: {e}")
        import traceback
        traceback.print_exc()
        return 0, "计算异常", ""


def rank_top_stocks_for_open(df_list, results_list):
    """
    对TOP10股票进行游资开仓排名
    
    参数：
        df_list: K线数据DataFrame列表
        results_list: V7评分结果列表
    
    返回：
        排序后的股票列表，包含开仓评分和推荐
    """
    ranked_stocks = []
    
    for i, (df, v7_result) in enumerate(zip(df_list, results_list)):
        if v7_result is None or df is None:
            continue
        
        # 确保df是DataFrame且有足够数据
        if not isinstance(df, pd.DataFrame) or len(df) < 20:
            continue
            
        # 确保必要字段存在
        if 'close' not in df.columns or 'vol' not in df.columns:
            continue
            
        try:
            open_score, structure_type, recommendation = calc_hot_money_open_score_v9(
                v7_result, df, v7_result, v7_result.get('所属主题', '')
            )
        except Exception as e:
            print(f"[开仓评分V9] {v7_result.get('代码', '')} 计算失败: {e}")
            continue
        
        ranked_stocks.append({
            '代码': v7_result.get('代码', ''),
            '名称': v7_result.get('名称', ''),
            '现价': v7_result.get('现价', 0),
            '涨跌幅': v7_result.get('涨跌幅', 0),
            '所属主题': v7_result.get('所属主题', ''),
            'V7总评分': v7_result.get('V7总评分', 0),
            '失败概率': v7_result.get('失败概率', 0),
            '量能爆发': v7_result.get('量能爆发', 0),
            '突破强度': v7_result.get('突破强度', 0),
            '结构类型': structure_type,
            '开仓评分': open_score,
            '推荐理由': recommendation,
        })
    
    # 按开仓评分降序排序
    ranked_stocks.sort(key=lambda x: x['开仓评分'], reverse=True)
    
    return ranked_stocks


def print_hot_money_open_report(ranked_stocks, top_n=3):
    """
    打印游资开仓报告
    """
    print("\n" + "=" * 80)
    print("🔥 游资最强开仓标的 (TOP " + str(top_n) + ")")
    print("=" * 80)
    
    for i, stock in enumerate(ranked_stocks[:top_n], 1):
        print(f"\n【第{i}名】{stock['名称']} ({stock['代码']})")
        print(f"  结构类型: {stock['结构类型']}")
        print(f"  开仓评分: {stock['开仓评分']}")
        print(f"  V7基础分: {stock['V7总评分']} | 失败概率: {stock['失败概率']:.1%}")
        print(f"  今日涨幅: {stock['涨跌幅']:.2f}%")
        print(f"  量能爆发: {stock['量能爆发']:.2f} | 突破强度: {stock['突破强度']:.2f}")
        print(f"  推荐理由: {stock['推荐理由']}")
    
    print("\n" + "-" * 80)
    print("📋 完整排名表:")
    print("-" * 80)
    print(f"{'排名':<4} {'代码':<12} {'名称':<8} {'结构类型':<10} {'开仓分':<8} {'V7分':<8} {'主题':<12}")
    print("-" * 80)
    
    for i, stock in enumerate(ranked_stocks, 1):
        print(f"{i:<4} {stock['代码']:<12} {stock['名称']:<8} {stock['结构类型']:<10} "
              f"{stock['开仓评分']:<8.1f} {stock['V7总评分']:<8.1f} {stock['所属主题']:<12}")
    
    print("=" * 80)
    
    return ranked_stocks[:top_n]


# =========================================================
# 主题纯度评分系统
# =========================================================
def calc_theme_confidence(stock_info, theme):
    """
    计算个股与主题的纯度/置信度评分（0-100）

    参数：
        stock_info: 股票信息字典，包含：
            - industries: 行业列表
            - concepts: 概念列表
            - business_text: 业务描述文本
            - name: 股票名称
        theme: 主题名称（如"AI算力链"、"半导体"等）

    返回：
        主题纯度评分（0-100）
    """
    if not stock_info or not theme:
        return 0

    score = 0

    # 行业匹配得分
    score += calc_industry_score(
        stock_info.get("industries", []),
        theme
    )

    # 概念匹配得分
    score += calc_concept_score(
        stock_info.get("concepts", []),
        theme
    )

    # 关键词命中得分
    score += calc_keyword_score(
        stock_info.get("business_text", ""),
        theme
    )

    # 业务相关性得分
    score += calc_business_score(
        stock_info.get("business_text", ""),
        theme
    )

    # 核心公司加分
    score += calc_core_company_score(
        stock_info.get("name", ""),
        theme
    )

    # 核心龙头股额外加分（对行业内具有核心地位的龙头公司给予加成）
    score += calc_leader_bonus(
        stock_info.get("name", ""),
        theme
    )

    # 排除惩罚
    score -= calc_penalty(
        stock_info.get("business_text", ""),
        theme
    )

    return max(min(score, 100), 0)


def calc_industry_score(industries, theme):
    """行业匹配得分（满分25）"""
    if not industries or not theme:
        return 0

    score = 0
    cfg = _get_theme_config(theme)
    if not cfg:
        return 0

    target_industries = set(cfg.get("industry", []))

    for ind in industries:
        if ind in target_industries:
            score += 25
            break
        # 部分匹配
        for target in target_industries:
            if target in ind or ind in target:
                score += 15
                break

    return min(score, 25)


def calc_concept_score(concepts, theme):
    """概念匹配得分（满分25）"""
    if not concepts or not theme:
        return 0

    score = 0
    cfg = _get_theme_config(theme)
    if not cfg:
        return 0

    target_concepts = set(cfg.get("concept", []))

    matched_count = 0
    for c in concepts:
        if c in target_concepts:
            matched_count += 1

    # 匹配数量越多，得分越高
    if matched_count >= 5:
        score = 25
    elif matched_count >= 3:
        score = 20
    elif matched_count >= 2:
        score = 15
    elif matched_count >= 1:
        score = 10

    return min(score, 25)


def calc_keyword_score(business_text, theme):
    """关键词命中得分（满分20）"""
    if not business_text or not theme:
        return 0

    score = 0
    cfg = _get_theme_config(theme)
    if not cfg:
        return 0

    keywords = cfg.get("keywords", [])
    exclude_keywords = set(cfg.get("exclude_keywords", []))

    text_lower = business_text.lower()

    hit_count = 0
    for kw in keywords:
        if kw.lower() in text_lower:
            hit_count += 1

    # 根据命中数量计算得分
    if len(keywords) > 0:
        hit_rate = hit_count / len(keywords)
        score = hit_rate * 20

    return min(score, 20)


def calc_business_score(business_text, theme):
    """业务相关性得分（满分15）"""
    if not business_text or not theme:
        return 0

    score = 0
    cfg = _get_theme_config(theme)
    if not cfg:
        return 0

    # 核心关键词
    core_keywords = cfg.get("keywords", [])[:5]  # 取前5个核心词

    text_lower = business_text.lower()
    core_hits = 0
    for kw in core_keywords:
        if kw.lower() in text_lower:
            core_hits += 1

    # 核心关键词命中给高分
    if core_hits >= 3:
        score = 15
    elif core_hits >= 2:
        score = 10
    elif core_hits >= 1:
        score = 5

    return min(score, 15)


def calc_core_company_score(stock_name, theme):
    """核心公司加分（满分10）- 从theme.json读取"""
    if not stock_name or not theme:
        return 0

    cfg = _get_theme_config(theme)
    if not cfg:
        return 0

    # 从theme.json获取核心公司列表
    companies = cfg.get("core_companies", [])
    for c in companies:
        if c in stock_name:
            return 10

    return 0


def calc_leader_bonus(stock_name, theme):
    """核心龙头股额外加分（满分15）- 从theme.json读取"""
    if not stock_name or not theme:
        return 0

    cfg = _get_theme_config(theme)
    if not cfg:
        return 0

    # 从theme.json获取核心龙头股列表（优先使用leader_companies，没有则用core_companies前3个）
    leader_companies = cfg.get("leader_companies", [])
    if not leader_companies:
        # 如果没有定义leader_companies，使用core_companies的前3个作为龙头
        core_list = cfg.get("core_companies", [])
        leader_companies = core_list[:3]

    for c in leader_companies:
        if c in stock_name:
            return 15

    return 0


def calc_penalty(business_text, theme):
    """排除惩罚（扣分项）"""
    if not business_text or not theme:
        return 0

    penalty = 0
    cfg = _get_theme_config(theme)
    if not cfg:
        return 0

    exclude_keywords = cfg.get("exclude_keywords", [])
    text_lower = business_text.lower()

    for kw in exclude_keywords:
        if kw.lower() in text_lower:
            penalty += 15

    return min(penalty, 30)  # 最多扣30分


def _get_theme_config(theme):
    """获取主题配置"""
    try:
        cfg_path = os.path.join(BASE_DIR, 'theme.json')
        if not os.path.exists(cfg_path):
            return None

        with open(cfg_path, 'r', encoding='utf-8') as f:
            theme_cfg = json.load(f).get('HOT_THEMES', {})

        return theme_cfg.get(theme, {})
    except Exception as e:
        print(f"[主题纯度] 配置读取失败: {e}")
        return None


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
    # 涨停或连续两日大涨
    # =========================
    # 条件1：单日涨停（涨幅>9.8%）
    ZT_1day = (
        (C.shift(1) / C.shift(2) < 1.08) &
        (C / C.shift(1) > 1.098) 
    )
    # 条件2：连续两日每日上涨5%以上
    ZT_2day = (
        (C.shift(1) / C.shift(2) >= 1.05) &
        (C / C.shift(1) >= 1.05) &
        (C / C.shift(2) >= 1.10)  # 两日累计涨幅>=10%
    )
    # 合并条件：满足任意一个即可
    ZT = ZT_1day | ZT_2day
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

    cond_xh1 = (C.iloc[-1] > highest_close or (H.iloc[-1] >H.iloc[-2] and H.iloc[-1] > H.iloc[-3]))
    #cond_xh1 = (C.iloc[-1] > highest_close)
    cond_xh2 = C.iloc[-1]>C.iloc[-2] and C.iloc[-1] / ma5.iloc[-1] <1.15 and C.iloc[-1] / ma5.iloc[-1] > 0.95
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
                "content": "你是A股顶级机构和游资短线投资分析师"
            },
            {
                "role": "user",
                "content": prompt
            }
        ],

        "temperature": 0.1,
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
        os.makedirs(REPORT_DIR, exist_ok=True)
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
                score REAL,
                theme TEXT
            )
        """)
        # 兼容旧表：如果表存在但没有theme列，则添加
        try:
            cursor.execute("ALTER TABLE stock_result ADD COLUMN theme TEXT")
        except:
            pass  # 列已存在，忽略
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
                (date, rank, code, name, close, amount, score, theme)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                today,
                i + 1,
                getattr(row, "代码", ""),
                getattr(row, "名称", ""),
                float(getattr(row, "现价", 0)) if getattr(row, "现价", 0) not in ['', None] else 0.0,
                float(getattr(row, "成交额", 0)) if getattr(row, "成交额", 0) not in ['', None] else 0.0,
                float(getattr(row, "总排序评分", 0)) if getattr(row, "总排序评分", 0) not in ['', None] else 0.0,
                getattr(row, "所属主题", "")
            ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"保存结果到数据库失败，跳过: {e}")

def load_history(days=10):
    try:
        # 尝试从旧目录和新目录都读取数据
        dataframes = []
        print(DB_PATH)
        if os.path.exists(DB_PATH):
            try:
                conn = sqlite3.connect(DB_PATH)
                today = TRADE_DATE
                start_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
                query = f"""
                    SELECT *
                    FROM stock_result
                    WHERE date >= '{start_date}'
                    AND date < '{today}'
                    ORDER BY date DESC, rank ASC
                """
                df_new = pd.read_sql(query, conn)
                conn.close()
                if not df_new.empty:
                    dataframes.append(df_new)
                    print(f"从新目录加载历史数据: {len(df_new)} 条")
            except Exception as e:
                print(f"从新目录加载历史数据失败: {e}")
        
        # 合并数据
        if len(dataframes) > 0:
            df_new = pd.concat(dataframes, ignore_index=True)
            # 去重
            df_new = df_new.drop_duplicates(subset=['date', 'code'], keep='last')
            df_new = df_new.sort_values(['date', 'rank'], ascending=[False, True])
            return df_new
        
        else:
            print("未找到任何历史数据")
            return pd.DataFrame(columns=['date', 'rank', 'code', 'name', 'close', 'amount', 'score'])
    except Exception as e:
        print(f"加载历史数据失败，返回空数据: {e}")
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
        
        # =====批量获取行业信息（用于V75主题纯度计算）=====
        industry_dict = {}
        try:
            if pro is not None:
                all_codes = recent_stocks['code'].tolist()
                for i in range(0, len(all_codes), 100):
                    batch_codes = all_codes[i:i+100]
                    df_basic = pro.stock_basic(
                        ts_code=",".join(batch_codes),
                        list_status='L',
                        fields='ts_code,industry'
                    )
                    if df_basic is not None and not df_basic.empty:
                        for _, r in df_basic.iterrows():
                            industry_dict[r['ts_code']] = r['industry']
        except Exception as e:
            print(f"获取行业信息失败: {e}")
        
        # 生成跟踪分析股票列表
        tracking_stocks = []
        for _, row in recent_stocks.iterrows():
            ts_code = row['code']
            
            range_pct = 0
            max_pct = 0
            
            # 从缓存文件读取完整数据计算5日涨幅和最高涨幅（支持旧目录和新目录）
            try:
                cache_file = os.path.join(CACHE_DIR, f"{ts_code}.csv")
                # 尝试旧目录
                old_cache_file = os.path.join(os.path.dirname(BASE_DIR), "cache_daily", f"{ts_code}.csv")
                
                if os.path.exists(cache_file):
                    df = pd.read_csv(cache_file)
                elif os.path.exists(old_cache_file):
                    df = pd.read_csv(old_cache_file)
                else:
                    continue
                
                df['trade_date'] = df['trade_date'].astype(str)
                df = df[df['trade_date'] <= TRADE_DATE]
                df = df.sort_values('trade_date').tail(10)  # 取最近10天
                
                if len(df) >= 5:
                    closes = df['close'].values
                    first_close = closes[-5]
                    last_close = closes[-1]
                    if first_close > 0:
                        range_pct = ((last_close - first_close) / first_close) * 100
                    
                    # 计算最高涨幅
                    highs = df['high'].values
                    max_high = max(highs[-5:])
                    if first_close > 0:
                        max_pct = ((max_high - first_close) / first_close) * 100
            except Exception as e:
                pass
            
            # 条件1：近5天最高涨幅不超过20%
            if max_pct > 20:
                continue
            
            # 条件2：收盘价在5日线以上且5日乖离率小于5%
            is_valid = False
            bias_rate = 0
            
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
                        
                        # 条件1：收盘价必须在MA5以上
                        if ma5 is not None and pd.notna(ma5) and close > ma5 and ma5 > 0:
                            # 计算5日乖离率
                            bias_rate = ((close - ma5) / ma5) * 100
                            
                            # 条件2：5日乖离率小于5%
                            if bias_rate < 5:
                                is_valid = True
            except Exception as e:
                pass
            
            if is_valid:
                # =====改用V75评分系统=====
                stock_industry = industry_dict.get(ts_code, '')
                stock_info = {
                    "name": row['name'],
                    "industries": [stock_industry] if stock_industry else [],
                    "concepts": [],
                    "business_text": ""
                }
                
                last_score = 0.0
                try:
                    df_hist = get_hist_data(ts_code)
                    if df_hist is not None and len(df_hist) >= 60:
                        v75_result = calc_dual_layer_score_v75(df_hist, ts_code=ts_code, stock_info=stock_info)
                        last_score = v75_result.get('V7总评分', 0)
                    else:
                        # 数据不足，回退到旧评分
                        last_score = float(row['score']) if str(row['score']).strip() not in ['', 'None'] else 0.0
                        if last_score > 100:
                            last_score = min(last_score, 50)
                except Exception as e:
                    # 计算失败，回退到旧评分
                    last_score = float(row['score']) if str(row['score']).strip() not in ['', 'None'] else 0.0
                    if last_score > 100:
                        last_score = min(last_score, 50)
                
                # 优先使用kline_data中的最新交易日价格
                latest_kline = kline_data.get(ts_code)
                if latest_kline is not None:
                    latest_close = float(latest_kline['close'])
                else:
                    latest_close = float(row['close']) if str(row['close']).strip() not in ['', 'None'] else 0.0
                
                tracking_stocks.append({
                    'code': ts_code,
                    'name': row['name'],
                    'last_date': TRADE_DATE,
                    'last_close': latest_close,
                    'last_score': last_score,
                    'range_5d_pct': range_pct,
                    'max_pct': max_pct,
                    'bias_rate': bias_rate
                })
        
        # 按V75评分排序，取前10只
        tracking_stocks = sorted(tracking_stocks, key=lambda x: -x['last_score'])[:10]
        
        # 生成文本格式
        lines = []
        if tracking_stocks:
            lines.append("=" * 80)
            lines.append("跟踪分析股票池（最高涨幅≤20%、5日均线上、5日乖离率<5%）")
            lines.append("=" * 80)
            lines.append(f"{'代码':<12} {'名称':<10} {'最新价':<8} {'评分':<10} {'5日涨幅':<10} {'最高涨幅':<10} {'乖离率':<10}")
            lines.append("-" * 80)
            for stock in tracking_stocks:
                lines.append(f"{stock['code']:<12} {stock['name']:<10} {stock['last_close']:<8.2f} {stock['last_score']:<10.2f} {stock['range_5d_pct']:<+10.2f}% {stock['max_pct']:<+10.2f}% {stock['bias_rate']:<10.2f}%")
            lines.append("=" * 80)
        
        # 生成AI报告
        ai_report = generate_ai_report(tracking_stocks)
        
        return tracking_stocks, "\n".join(lines), ai_report
    except Exception as e:
        print(f"筛选跟踪分析个股失败: {e}")
        return [], "数据加载失败", ""


def generate_ai_report(tracking_stocks):
    """
    为符合条件的个股生成AI分析报告
    """
    if not tracking_stocks:
        return "暂无符合条件的个股"
    
    report = []
    report.append("=" * 80)
    report.append("AI智能选股分析报告")
    report.append("=" * 80)
    report.append(f"分析日期: {TRADE_DATE}")
    report.append(f"筛选条件: 最高涨幅≤20% 且 在5日均线上 且 5日乖离率<5%")
    report.append(f"符合条件个股: {len(tracking_stocks)}只")
    report.append("")
    
    # TOP 5重点分析
    if len(tracking_stocks) > 0:
        report.append("【TOP 5重点分析】")
        report.append("-" * 80)
        
        for i, stock in enumerate(tracking_stocks[:5]):
            report.append(f"\n{i+1}. {stock['name']} ({stock['code']})")
            report.append(f"   最新价: {stock['last_close']:.2f}  |  综合评分: {stock['last_score']:.2f}")
            report.append(f"   5日涨幅: {stock['range_5d_pct']:+.2f}%  |  最高涨幅: {stock['max_pct']:+.2f}%")
            report.append(f"   5日乖离率: {stock['bias_rate']:.2f}%")
            
            # 简单判断
            bias_desc = ""
            if stock['bias_rate'] < 2:
                bias_desc = "极度接近均线，安全边际高"
            elif stock['bias_rate'] < 3:
                bias_desc = "适中偏离，走势健康"
            elif stock['bias_rate'] < 4:
                bias_desc = "小幅偏离，仍可关注"
            else:
                bias_desc = "接近上限，注意风险"
            
            max_desc = ""
            if stock['max_pct'] < 10:
                max_desc = "涨幅温和，潜力大"
            elif stock['max_pct'] < 15:
                max_desc = "适中涨幅，空间尚存"
            else:
                max_desc = "接近20%上限，谨慎关注"
            
            report.append(f"   评价: {bias_desc} | {max_desc}")
    
    # 整体分析
    report.append("\n" + "=" * 80)
    report.append("【整体分析】")
    report.append("-" * 80)
    
    if len(tracking_stocks) > 0:
        avg_score = sum(s['last_score'] for s in tracking_stocks) / len(tracking_stocks)
        avg_pct = sum(s['range_5d_pct'] for s in tracking_stocks) / len(tracking_stocks)
        avg_bias = sum(s['bias_rate'] for s in tracking_stocks) / len(tracking_stocks)
        
        report.append(f"平均评分: {avg_score:.2f}")
        report.append(f"平均5日涨幅: {avg_pct:+.2f}%")
        report.append(f"平均5日乖离率: {avg_bias:.2f}%")
        report.append("")
        report.append("【策略建议】")
        report.append("1. 优先关注TOP 3中乖离率<3%的个股")
        report.append("2. 重点观察量价配合情况，放量突破可跟进")
        report.append("3. 设置止损位，建议-5%为止损")
        report.append("4. 分批建仓，控制单只仓位不超过10%")
    
    report.append("=" * 80)
    
    return "\n".join(report)


# =========================
# 涨跌停数据（替代 emotion.get_limit_stats）
# =========================
def get_limit_stats():
    """获取涨跌停数据，替代原 emotion.get_limit_stats
    优化：优先使用收盘后的实际涨跌停数据，避免盘中触板数据干扰
    """
    try:
        print("开始获取涨跌停数据...")
        zt_codes = []
        dt_codes = []
        broken_rate = 0.0

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

        # 方法1：使用每日行情数据计算真实的涨跌停（收盘价）
        try:
            # 获取当日所有股票的收盘价和涨跌幅
            daily = pro.daily(trade_date=TRADE_DATE)
            if daily is not None and not daily.empty:
                # 计算涨跌停阈值（简化版：主板10%，科创板/创业板20%）
                daily['is_kcb'] = daily['ts_code'].str.startswith(('688', '301'))
                daily['is_cn'] = daily['ts_code'].str.startswith('300')
                daily['limit_up'] = daily.apply(
                    lambda x: 20.0 if x['is_kcb'] or x['is_cn'] else 10.0, axis=1
                )
                daily['limit_down'] = -daily['limit_up']
                
                # 真实涨停：收盘价涨幅接近涨停价（>=99%的涨停幅度）
                zt_mask = (daily['pct_chg'] >= daily['limit_up'] * 0.99) & (daily['pct_chg'] < daily['limit_up'] + 0.1)
                # 真实跌停：收盘价跌幅接近跌停价
                dt_mask = (daily['pct_chg'] <= daily['limit_down'] * 0.99) & (daily['pct_chg'] > daily['limit_down'] - 0.1)
                
                zt_codes = daily[zt_mask]['ts_code'].tolist()
                dt_codes = daily[dt_mask]['ts_code'].tolist()
                
                print(f"涨停(真实收盘): {len(zt_codes)}只")
                print(f"跌停(真实收盘): {len(dt_codes)}只")
                
                # 获取炸板数据（盘中触及涨停但未封住）
                try:
                    limit_df = pro.limit_list_d(trade_date=TRADE_DATE)
                    if limit_df is not None and not limit_df.empty:
                        # limit='D'表示最终封住, limit='Z'表示炸板
                        zhaban_codes = limit_df[limit_df['limit'] == 'Z']['ts_code'].astype(str).tolist()
                        zhaban_count = len(zhaban_codes)
                        
                        # 炸板率 = 炸板数 ÷ (封住数 + 炸板数)
                        total_touch = len(zt_codes) + zhaban_count
                        if total_touch > 0:
                            broken_rate = (zhaban_count / total_touch) * 100
                            print(f"炸板率: {broken_rate:.1f}% (炸板{zhaban_count}只/触及涨停{total_touch}只)")
                except Exception as e:
                    print(f"获取炸板数据失败: {e}")
                    
        except Exception as e:
            print(f"方法1失败: {e}")

        # 如果以上方法都失败，使用ths接口作为备选（但不作为主要数据源）
        if not zt_codes and not dt_codes:
            print("[备选] 使用ths接口...")
            try:
                ths_zt = pro.limit_list_ths(trade_date=TRADE_DATE, limit_type='涨停池')
                if ths_zt is not None and not ths_zt.empty:
                    zt_codes = ths_zt['ts_code'].astype(str).tolist()
                    print(f"涨停(ths备选): {len(zt_codes)}只")
            except Exception as e:
                print(f"ths涨停失败: {e}")

            try:
                ths_dt = pro.limit_list_ths(trade_date=TRADE_DATE, limit_type='跌停池')
                if ths_dt is not None and not ths_dt.empty:
                    dt_codes = ths_dt['ts_code'].astype(str).tolist()
                    print(f"跌停(ths备选): {len(dt_codes)}只")
            except Exception as e:
                print(f"ths跌停失败: {e}")

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
# 主题过滤：只保留短线TOP5+中线TOP5主题覆盖的个股
# =========================
def filter_by_top_themes(result_df, top_n=5):
    """优化版主题匹配算法：使用 match_theme_stocks 获取成份股"""
    if result_df.empty:
        return result_df
    
    # 1. 获取短线TOP5（当日综合分）和中线TOP5（60日均分）
    theme_data = das.read_theme_analysis(TRADE_DATE)
    short_top = []
    if theme_data and theme_data.get('themes'):
        short_top = [t['theme_name'] for t in 
                     sorted(theme_data['themes'], key=lambda x: x.get('composite_score', 0), reverse=True)[:top_n]]
    
    avg_data = das.read_60day_avg_trend_scores(TRADE_DATE)
    mid_top = []
    if avg_data and avg_data.get('themes'):
        mid_top = [t['theme_name'] for t in 
                   sorted(avg_data['themes'], key=lambda x: x.get('avg_trend_score', 0), reverse=True)[:top_n]]
    
    valid_themes = set(short_top + mid_top)
    print(f"\n[主题过滤] 短线TOP{top_n}: {short_top}")
    print(f"[主题过滤] 中线TOP{top_n}: {mid_top}")
    
    if not valid_themes:
        print("[主题过滤] 无主题数据，不过滤")
        return result_df
    
    # 2. 加载主题配置（只保留有效主题）
    theme_cfg = {}
    cfg_path = os.path.join(BASE_DIR, 'theme.json')
    if os.path.exists(cfg_path):
        with open(cfg_path, 'r', encoding='utf-8') as f:
            all_themes = json.load(f).get('HOT_THEMES', {})
            # 只保留有效主题的配置
            for theme_name in valid_themes:
                if theme_name in all_themes:
                    theme_cfg[theme_name] = all_themes[theme_name]
    
    # 3. 调用 match_theme_stocks 获取主题成份股映射
    try:
        import theme_trend_sentiment_score as theme_ts
        dc_df = theme_ts.get_dc_members()
        
        # 获取股票基本信息
        stock_basic_df = None
        if pro is not None:
            try:
                stock_basic_df = pro.stock_basic(fields='ts_code,industry,name')
            except Exception as e:
                print(f"[主题过滤] 获取stock_basic失败: {e}")
        
        # 调用 match_theme_stocks 获取成份股映射
        theme_stock_map, name_map_basic, stock_basic_industry, stock_concepts = theme_ts.match_theme_stocks(
            theme_cfg, dc_df, stock_basic_df
        )
        
        print(f"[主题过滤] 成份股映射加载完成: {len(theme_stock_map)} 个主题")
        for theme_name, stocks in theme_stock_map.items():
            print(f"  {theme_name}: {len(stocks)} 只成份股")
        
    except Exception as e:
        print(f"[主题过滤] match_theme_stocks调用失败: {e}")
        import traceback
        traceback.print_exc()
        # 如果调用失败，使用原有的简单匹配逻辑
        return _filter_by_top_themes_fallback(result_df, valid_themes, theme_cfg)
    
    # 4. 遍历股票，只要是成份股就保留
    keep = []
    matched_themes = []
    match_scores = []
    
    for _, row in result_df.iterrows():
        ts_code = row['代码']
        stock_name = row.get('名称', '')
        
        # 查找该股票属于哪个主题
        found_theme = ''
        for theme_name, stocks in theme_stock_map.items():
            if ts_code in stocks:
                found_theme = theme_name
                break
        
        if found_theme:
            # 是成份股，保留
            keep.append(True)
            matched_themes.append(found_theme)
            match_scores.append(100)  # 成份股匹配度为100分
            print(f"[主题过滤] 成份股匹配: {stock_name}({ts_code}) -> {found_theme}")
        else:
            # 不是成份股，过滤掉
            keep.append(False)
            matched_themes.append('')
            match_scores.append(0)
    
    # 5. 应用过滤
    before = len(result_df)
    result_df = result_df[keep].reset_index(drop=True)
    result_df['所属主题'] = [matched_themes[i] for i in range(len(matched_themes)) if keep[i]]
    result_df['主题匹配度'] = [match_scores[i] for i in range(len(match_scores)) if keep[i]]
    
    print(f"[主题过滤] 过滤后 {before} -> {len(result_df)} 只")
    
    # 打印匹配详情
    if len(result_df) > 0:
        print(f"\n[主题匹配详情] TOP10:")
        for i, r in result_df.head(10).iterrows():
            print(f"  {i+1}. {r['名称']}({r['代码']}) -> {r['所属主题']} (成份股)")
    
    return result_df


def _filter_by_top_themes_fallback(result_df, valid_themes, theme_cfg):
    """降级版主题匹配算法：当match_theme_stocks不可用时使用"""
    print("[主题过滤] 使用降级匹配逻辑")
    
    keep = []
    matched_themes = []
    match_scores = []
    
    for _, row in result_df.iterrows():
        ts_code = row['代码']
        stock_name = row.get('名称', '')
        
        best_theme = ''
        best_score = 0
        
        # 检查核心公司
        for theme_name in valid_themes:
            cfg = theme_cfg.get(theme_name, {})
            core_companies = cfg.get('core_companies', [])
            if stock_name in core_companies:
                best_theme = theme_name
                best_score = 100
                break
        
        if best_theme:
            keep.append(True)
            matched_themes.append(best_theme)
            match_scores.append(best_score)
        else:
            keep.append(False)
            matched_themes.append('')
            match_scores.append(0)
    
    before = len(result_df)
    result_df = result_df[keep].reset_index(drop=True)
    result_df['所属主题'] = [matched_themes[i] for i in range(len(matched_themes)) if keep[i]]
    result_df['主题匹配度'] = [match_scores[i] for i in range(len(match_scores)) if keep[i]]
    
    print(f"[主题过滤] 降级过滤后 {before} -> {len(result_df)} 只")
    return result_df


# =========================
# 主程序
# =========================
def run(target_date=None, simple_mode=False):
    """运行量化选股分析
    
    Args:
        target_date: 目标日期，格式为 'YYYYMMDD'，默认为当前交易日
        simple_mode: 简易模式，只输出个股和评分，不进行AI分析、不发送微信
    """
    global TRADE_DATE
    
    # 如果指定了目标日期，验证并设置
    if target_date:
        target_date = str(target_date)
        TRADE_DATE = validate_trade_date(target_date)
        print(f"\n{'='*60}")
        print(f"[回溯模式] 目标日期: {TRADE_DATE}")
        print(f"{'='*60}\n")
    
    # =========================
    # 新版大盘分析 + 主题分析（替代 emotion + block）
    # =========================
    print("\n========== 市场趋势总评分 ==========\n")
    market_data = das.read_market_analysis(TRADE_DATE)
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
    theme_data = das.read_theme_analysis(TRADE_DATE)
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
    avg_trend_data = das.read_60day_avg_trend_scores(TRADE_DATE)
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
        # 从 market_analysis.py 获取指数数据（确保数据一致性）
        import importlib
        ma = importlib.import_module("market_analysis")
        ma_results, ma_position, ma_reason, ma_style_allocations, ma_overview = ma.analyze_market(TRADE_DATE)
        
        # 提取指数数据
        index_lines = []
        for r in ma_results:
            index_lines.append(f"  {r['name']}: {r['close']:.2f} ({r['pct_chg']:+.2f}%)")
        
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

    # 批量预取：解决高频API调用问题
    # 在循环之前一次性下载所有股票数据到本地缓存
    if market is not None and not market.empty:
        all_codes = market['ts_code'].tolist()
        print(f"\n[批量预取] 共 {len(all_codes)} 只股票，开始下载历史数据...")
        batch_prefetch_hist_data(all_codes)
        print(f"[批量预取] 完成，后续循环将命中本地缓存\n")

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
                    'total_market_cap': row['total_mv'] * 10000,  # 转换为元
                    'market_cap': row['total_mv'] * 10000,       # 兼容字段
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
                {'代码': '000001.SZ', '名称': '平安银行', '现价': 10.5, '涨跌幅': 1.5, '成交额': 500000, '总市值（亿元）': 1500, 'total_market_cap': 1500e8, 'market_cap': 1500e8},
                {'代码': '600000.SH', '名称': '浦发银行', '现价': 8.2, '涨跌幅': -0.8, '成交额': 300000, '总市值（亿元）': 1200, 'total_market_cap': 1200e8, 'market_cap': 1200e8},
                {'代码': '000002.SZ', '名称': '万科A', '现价': 25.3, '涨跌幅': 2.3, '成交额': 800000, '总市值（亿元）': 800, 'total_market_cap': 800e8, 'market_cap': 800e8},
                {'代码': '600519.SH', '名称': '贵州茅台', '现价': 1800.0, '涨跌幅': 0.5, '成交额': 1200000, '总市值（亿元）': 25000, 'total_market_cap': 25000e8, 'market_cap': 25000e8},
                {'代码': '300750.SZ', '名称': '宁德时代', '现价': 120.0, '涨跌幅': 3.2, '成交额': 2000000, '总市值（亿元）': 18000, 'total_market_cap': 18000e8, 'market_cap': 18000e8},
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
        theme = row.get('所属主题', '')

        hist = get_hist_data(ts_code)

        if hist is None:
            continue

        # 构建stock_info
        stock_info = {}

        # 先尝试从主题数据获取信息（为了计算主题纯度
        try:
            import theme_trend_sentiment_score as theme_ts
            dc_df = theme_ts.get_dc_members()
            if dc_df is not None and not dc_df.empty:
                stock_rows = dc_df[dc_df['con_code'] == ts_code]
                if not stock_rows.empty:
                    # 收集概念和行业
                    concepts = set()
                    industries = set()
                    for _, r in stock_rows.iterrows():
                        name = r['concept_name']
                        if '行业' in name or 'Ⅱ' in name or 'Ⅲ' in name:
                            industries.add(name)
                        else:
                            concepts.add(name)
                    stock_info['concepts'] = list(concepts)
                    stock_info['industries'] = list(industries)
                    stock_info['name'] = row.get('名称', '')
        except:
            pass

        factor = calc_dual_layer_score_v75(
            hist, ts_code, stock_info, theme
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
            'V7总评分'
            
        ],

        ascending=False
    )

    print(result_df)
    init_db()
    # 保存时要把V7总评分作为score字段
    # 创建一个临时副本，修改score字段后保存
    result_df_save = result_df.copy()
    if 'V7总评分' in result_df_save.columns:
        # 用V7总评分覆盖score列（如果有）或作为新列
        result_df_save['总排序评分'] = result_df_save['V7总评分']
    save_result(result_df_save)

    # ===== 主题过滤：只保留短线TOP5+中线TOP5覆盖的个股 =====
    result_df = filter_by_top_themes(result_df)
    
    # =========================
    # 取前10名用于分析
    # =========================
    top10_df = result_df.head(30)
    print("\n========== Top10 个股 ==========\n")
    # 显示所属主题字段（如果存在）
    display_cols = ['代码', '名称', '现价', '涨跌幅', 'V7总评分', '风险等级','所属主题','趋势概率','突破强度','压缩度','量能爆发']
    if '所属主题' in top10_df.columns:
        display_cols = ['代码', '名称', '现价', '涨跌幅', '所属主题', 'V7总评分', '风险等级','趋势概率','突破强度','压缩度','量能爆发']
    print(top10_df[display_cols])
    
    # =========================
    # 游资最强开仓算法 V8
    # =========================
    global hot_money_open_text
    hot_money_open_text = ""  # 初始化全局变量
    try:
        # 获取TOP10的K线数据
        df_list = []
        results_list = []
        
        for _, row in top10_df.iterrows():
            code = str(row.get('代码', ''))
            name = str(row.get('名称', ''))
            df = get_hist_data(code)
            
            if df is None or len(df) < 20:
                continue
            
            # 确保df是DataFrame且有close列
            if not isinstance(df, pd.DataFrame) or 'close' not in df.columns:
                continue
            
            # 从row中安全获取值，转为Python原生类型
            try:

                theme_name = str(row.get('所属主题', ''))
                
                # 直接从K线数据计算真实涨跌幅，避免数据错误
                if len(df) >= 2:
                    today_pct = ((df['close'].iloc[-1] / df['close'].iloc[-2]) - 1) * 100
                else:
                    today_pct = float(row.get('涨跌幅', 0))
                
                # 过滤当日涨停股票（区分主板和双创）
                # 主板（600xxx, 601xxx, 603xxx, 605xxx, 000xxx, 001xxx, 002xxx, 003xxx）：9.98%
                # 双创（300xxx, 688xxx, 301xxx）：19.88%
                code_prefix = code[:3] if len(code) >= 3 else ''
                is_double_innovation = code_prefix in ['300', '688', '301']
                limit_up_threshold = 19.88 if is_double_innovation else 9.98
                
                #if today_pct >= limit_up_threshold:
                #    market_type = "双创" if is_double_innovation else "主板"
                #    print(f"[开仓过滤] {code} {name} 今日涨停({today_pct:.2f}%，{market_type}阈值{limit_up_threshold}%)，跳过")
                #    continue
                
                v7_result = {
                    '代码': code,
                    '名称': name,
                    '现价': float(row.get('现价', 0)),
                    '涨跌幅': today_pct,
                    '所属主题': theme_name,
                    'V7总评分': float(row.get('V7总评分', 50)),
                    '风险等级': str(row.get('风险等级', '低')),
                    '趋势概率': float(row.get('趋势概率', 0.5)),
                    '失败概率': float(row.get('失败概率', 0.5)),
                    '洗盘概率': float(row.get('洗盘概率', 0.5)),
                    '趋势强度': float(row.get('趋势强度', 0.5)),
                    '趋势稳定': float(row.get('趋势稳定', 0.5)),
                    '资金动量': float(row.get('资金动量', 0.5)),
                    '突破强度': float(row.get('突破强度', 0.5)),
                    '压缩度': float(row.get('压缩度', 0.5)),
                    '量能爆发': float(row.get('量能爆发', 0.5)),
                    # 主题纯度从V7打分结果中获取，不再硬编码
                    '主题纯度': float(row.get('主题纯度', 0)),
                    # 市值信息（用于资金体量因子）
                    'total_market_cap': float(row.get('total_market_cap', 0)),
                    'market_cap': float(row.get('market_cap', 0)),
                }
                df_list.append(df)
                results_list.append(v7_result)
            except Exception as e:
                print(f"[开仓数据构建] {code} 失败: {e}")
                continue
        
        if df_list and results_list:
            # 执行游资开仓排名
            ranked_stocks = rank_top_stocks_for_open(df_list, results_list)
            # 打印报告
            top3_stocks = print_hot_money_open_report(ranked_stocks, top_n=3)
            
            # 生成游资开仓信号文本
            lines = []
            lines.append("=" * 60)
            lines.append("🔥 游资最强开仓标的 (明日重点关注)")
            lines.append("=" * 60)
            for i, s in enumerate(top3_stocks, 1):
                lines.append(f"【第{i}名】{s['名称']} ({s['代码']})")
                lines.append(f"  结构类型: {s['结构类型']}")
                lines.append(f"  开仓评分: {s['开仓评分']:.1f} | V7基础分: {s['V7总评分']:.1f}")
                lines.append(f"  今日涨幅: {s['涨跌幅']:.2f}% | 失败概率: {s['失败概率']:.1%}")
                lines.append(f"  量能爆发: {s['量能爆发']:.2f} | 突破强度: {s['突破强度']:.2f}")
                lines.append(f"  推荐理由: {s['推荐理由']}")
                lines.append("")
            lines.append("完整排名:")
            lines.append("-" * 60)
            for i, s in enumerate(ranked_stocks, 1):
                lines.append(f"{i}. {s['代码']} {s['名称']} | {s['结构类型']} | 开仓分:{s['开仓评分']:.1f} | {s['所属主题']}")
            lines.append("=" * 60)
            hot_money_text = "\n".join(lines)
            
            # 保存到全局变量供prompt使用
            hot_money_open_text = hot_money_text
            
            # 在TOP10数据中标记明日开仓标的
            top3_codes = [s['代码'] for s in top3_stocks]
            result_df['明日开仓标的'] = result_df['代码'].apply(
                lambda x: '🔥' if str(x) in top3_codes else ''
            )
    except Exception as e:
        print(f"[游资开仓算法] 执行失败: {e}")
        import traceback
        traceback.print_exc()
        hot_money_open_text = ""
    
    stock_text = top10_df.to_string(index=False)
    all_stock_text = result_df.to_string(index=False)
    stock_his_df=load_history()
    stock_his_text = str(stock_his_df)

    # =========================
    # 获取跟踪分析个股
    # =========================
    try:
        result = get_tracking_stocks()

        if isinstance(result, tuple) and len(result) == 3:
            tracking_stocks, tracking_stocks_text, ai_report = result
        elif isinstance(result, tuple) and len(result) == 2:
            tracking_stocks, tracking_stocks_text = result
            ai_report = ""
        else:
            tracking_stocks, tracking_stocks_text, ai_report = [], "", ""
        
        # 对跟踪股票池进行主题过滤
        if tracking_stocks:
            tracking_df = pd.DataFrame(tracking_stocks)
            # 重命名字段以匹配filter_by_top_themes的期望
            tracking_df = tracking_df.rename(columns={'code': '代码', 'name': '名称'})
            tracking_df = filter_by_top_themes(tracking_df)
            
                       
            # 转换回列表格式
            tracking_stocks = tracking_df.to_dict('records')
            
            # 重新生成文本
            lines = []
            if tracking_stocks:
                lines.append("=" * 80)
                lines.append("跟踪分析股票池（最高涨幅≤20%、5日均线上、5日乖离率<5%）")
                lines.append("=" * 80)
                lines.append(f"{'代码':<12} {'名称':<10} {'最新价':<8} {'评分':<10} {'5日涨幅':<10} {'最高涨幅':<10} {'乖离率':<10}")
                lines.append("-" * 80)
                for stock in tracking_stocks:
                    lines.append(f"{stock['代码']:<12} {stock['名称']:<10} {stock.get('last_close', 0):<8.2f} {stock.get('last_score', 0):<10.2f} {stock.get('range_5d_pct', 0):<+10.2f}% {stock.get('max_pct', 0):<+10.2f}% {stock.get('bias_rate', 0):<10.2f}%")
                lines.append("=" * 80)
            tracking_stocks_text = "\n".join(lines)
    except Exception as e:
        print(f"获取跟踪分析个股失败: {e}")
        tracking_stocks, tracking_stocks_text, ai_report = [], "", ""
    
    if tracking_stocks_text:
        print("\n========== 跟踪分析个股 ==========\n")
        print(tracking_stocks_text)
        
        if ai_report:
            print("\n========== AI智能分析报告 ==========\n")
            print(ai_report)
    else:
        print(f"\n========== 暂无跟踪分析个股 ==========\n")

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

游资最强开仓信号（基于V8算法筛选的明日重点标的）：
（这是程序根据资金有效性、结构位置、主题强度等维度综合评分的结果，代表游资今日最强开仓偏好）

{hot_money_open_text}


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
1、大盘情绪(详细三大指数的数值和变化趋势,重点上证,含涨跌停数等几个数据指标)和仓位建议
2、通过以上数据及全网板块热点分析,给出今日主线板块和近几日动态变化分析：
   - 在主线板块分析中，**必须明确区分并加粗标注"中军"和"补涨中军"**
   - 中军：满足8个严格条件的趋势个股，RS20>=5，属于稳健型标的
   - 补涨中军：成交活跃+均线金叉的个股，不比RS，特点是大成交额+温和放量，属于补涨型标的
   - 在描述中，使用【**中军**】和【**补涨中军**】加粗标注股票类型
   - 分析主线板块的阶段和持续性，给出数据支撑和逻辑理由
3、自选量化股票池分析：
   **【重要】必须按以下两个维度分别排序和分析，先输出开仓建议，再输出综合评分排序：**
   
   a) **按游资开仓分排序（明日重点关注）**：
      - **【必须】根据游资最强开仓信号中的开仓评分，重新按开仓分从高到低排序**
      - **【必须】用以下格式突出显示前三名：**
        【第1名 - 明日首选】股票名 (代码)
        【第2名】股票名 (代码)
        【第3名】股票名 (代码)
      - 对这三只股票进行详细分析（包括开仓评分、结构类型、失败概率、量能爆发等）
      - 简要说明为什么这三只是游资明日最优开仓标的
      - **【重要】如果游资最强开仓信号中的前三名与按综合评分排序的前三名不同，必须明确指出并分析原因**
   
   b) **按综合评分排序**：严格按综合评分从高到低排序输出前10名个股，对每只股票单独分析，包括：
      - 股票名和代码（作为小标题，加粗显示）
      - 当前价格
      - 综合评分
      - 所属板块和主线关系
      - 技术面分析
      - 未来上涨空间预估
      - 买点建议
      - 止损点建议
      - 风险提示（如果有）
      - 如遇重大风险，请在分析中标注"【警告】有重大风险"，但仍保留在列表中并说明理由
   
4、跟踪分析个股：从近5日跟踪分析股票池中，精选符合技术形态的个股进行深度分析，重点关注：
   - 这些是近期持续出现在量化池中但尚未大涨的个股
   - 分析其当前技术形态（小十字星/揉搓线/下影线洗盘等）和可能的启动时机
   - 给出合适的跟踪关注点和潜在买点

格式要求：
- Top10个股分析中，每只股票单独分段，用【股票名+代码】作为小标题，加黑加粗显示
- 股票分析另起一行，分点说明
- 风格简洁明了，适合阅读

"""
    if not simple_mode:
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
            report_file = os.path.join(REPORT_DIR, f"Final_Self_{TRADE_DATE}.md")
            with open(report_file, "w", encoding="utf-8") as f:
                f.write(final_report)
            print(f"✅ 报告已保存: {report_file}")
        except Exception as e:
            print(f"⚠️ 报告保存失败: {e}")

        try:
            html_file = os.path.join(REPORT_DIR, f"Final_Self_{TRADE_DATE}.html")
            markdown_to_html_report(final_report, 
                                    output_file=html_file, 
                                    pdf_file=os.path.join(REPORT_DIR, f"Final_Self_{TRADE_DATE}.pdf"), 
                                    title=f"复盘及精选个股({TRADE_DATE})"
                                    )
        except Exception as e:
            print(f"⚠️ HTML报告生成失败: {e}")
    else:
        print(f"\n{'='*60}")
        print(f"[简易模式] 跳过AI分析和微信发送")
        print(f"{'='*60}")

    #result = send_wechat_message(report)

# =========================
# 启动
# =========================
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='A股量化选股分析系统')
    parser.add_argument('-d', '--date', type=str, default=None,
                        help='指定目标日期，格式: YYYYMMDD（如: 20260601）')
    parser.add_argument('--no-send', action='store_true',
                        help='不发送微信消息')
    parser.add_argument('--simple', action='store_true',
                        help='简易模式，只输出个股和评分，不进行AI分析、不发送微信')
    
    args = parser.parse_args()
    
    # 运行
    run(target_date=args.date, simple_mode=args.simple)


