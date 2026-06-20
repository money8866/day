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
# 产业资金定价AI模型（ICPM）延迟导入
# =========================
_MF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'multi_factor_picker')
if _MF_DIR not in sys.path:
    sys.path.insert(0, _MF_DIR)
_ICPM_AVAILABLE = True  # 标记可用，实际 import 在用到时执行

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

# DC热榜缓存目录
DC_HOT_CACHE_DIR = os.path.join(BASE_DIR, 'cache_backbone_tushare', 'dc_hot')


def count_hot_list_appearances(ts_code, days=20):
    """统计个股在热榜前20天出现的次数
    
    Args:
        ts_code: 股票代码
        days: 统计天数，默认20天
    
    Returns:
        count: 出现次数
    """
    count = 0
    
    if not os.path.exists(DC_HOT_CACHE_DIR):
        return count
    
    # 获取最近days天的日期
    from datetime import datetime, timedelta
    
    for i in range(days):
        date = datetime.now() - timedelta(days=i)
        date_str = date.strftime('%Y%m%d')
        csv_path = os.path.join(DC_HOT_CACHE_DIR, f'dc_hot_{date_str}.csv')
        
        if os.path.exists(csv_path):
            try:
                df = pd.read_csv(csv_path)
                if ts_code in df['ts_code'].values:
                    count += 1
            except Exception as e:
                pass
    
    return count


def get_hot_list_bonus(count):
    """根据热榜出现次数计算加分
    
    加分规则：
    - 出现1-3次：+1分
    - 出现4-6次：+3分
    - 出现7-10次：+5分
    - 出现11-15次：+8分
    - 出现16-20次：+12分
    """
    if count >= 16:
        return 12
    elif count >= 11:
        return 8
    elif count >= 7:
        return 5
    elif count >= 4:
        return 3
    elif count >= 1:
        return 1
    else:
        return 0


def get_hot_list_best_rank_bonus(ts_code, days=20):
    """获取股票在热榜中的最佳排名并返回加分
    
    加分规则（按最佳排名分段）：
    - Top10: +15分
    - Top20: +12分
    - Top30: +10分
    - Top50: +8分
    - Top100: +5分
    - 未进Top100: +0分
    
    Args:
        ts_code: 股票代码
        days: 统计天数，默认20天
    
    Returns:
        bonus: 加分
        best_rank: 最佳排名（未进榜返回9999）
        appear_count: 出现次数
    """
    best_rank = 9999
    appear_count = 0
    
    if not os.path.exists(DC_HOT_CACHE_DIR):
        return 0, best_rank, appear_count
    
    from datetime import datetime, timedelta
    
    for i in range(days):
        date = datetime.now() - timedelta(days=i)
        date_str = date.strftime('%Y%m%d')
        csv_path = os.path.join(DC_HOT_CACHE_DIR, f'dc_hot_{date_str}.csv')
        
        if os.path.exists(csv_path):
            try:
                df = pd.read_csv(csv_path)
                match = df[df['ts_code'] == ts_code]
                if not match.empty:
                    appear_count += 1
                    rank = match.iloc[0].get('hot_rank', match.iloc[0].get('rank', 9999))
                    if pd.notna(rank) and int(rank) < best_rank:
                        best_rank = int(rank)
            except Exception:
                pass
    
    # 根据最佳排名计算加分
    if best_rank <= 10:
        bonus = 15
    elif best_rank <= 20:
        bonus = 12
    elif best_rank <= 30:
        bonus = 10
    elif best_rank <= 50:
        bonus = 8
    elif best_rank <= 100:
        bonus = 5
    else:
        bonus = 0
    
    return bonus, best_rank, appear_count


def load_theme_pattern_stocks():
    """读取主题选股结果
    
    基于主题状态区分中期趋势和短线主线：
    - 中期趋势：抱团主升、强趋势（状态稳定且持续）
    - 短线主线：启动、分歧转一致、主升（状态刚启动）
    """
    if not os.path.exists(THEME_STOCKS_CACHE):
        return [], ""
    
    try:
        df = pd.read_csv(THEME_STOCKS_CACHE, encoding='utf-8-sig')
        if df.empty:
            return [], ""
        
        # 基于主题状态区分中期趋势和短线主线
        # 中期趋势：状态稳定且持续（抱团主升、强趋势）
        # 短线主线：状态刚启动（启动、分歧转一致、主升）
        mid_term_states = {'抱团主升', '强趋势'}
        short_term_states = {'启动', '分歧转一致', '主升'}
        
        mid_term = df[df.get('theme_state', '').isin(mid_term_states)]
        short_term = df[df.get('theme_state', '').isin(short_term_states)]
        
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
    # 如果没有 tushare，根据当前时间计算交易日
    # =========================
    if pro is None:
        # 简单处理：跳过周末
        from datetime import date
        d = date.today()
        if d.weekday() == 5:  # 周六
            d = d - timedelta(days=1)
        elif d.weekday() == 6:  # 周日
            d = d - timedelta(days=2)
        return d.strftime('%Y%m%d')

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
    """向量化版本：找到最近True的距离
    
    特殊规则：如果第1天是True，不更新last_true，后续False返回NaN
    """
    arr = series.values.astype(bool)
    n = len(arr)
    
    # 找到所有True的位置
    true_positions = np.where(arr)[0]
    
    if len(true_positions) == 0:
        return pd.Series([np.nan] * n, index=series.index)
    
    # 特殊处理：如果第1天是True，需要跳过它（不作为有效信号）
    start_idx = 0
    if arr[0]:
        # 第1天True，跳过它，从后续找有效True
        valid_positions = true_positions[true_positions > 0]
        if len(valid_positions) == 0:
            # 只有第1天是True，全部返回NaN
            return pd.Series([np.nan] * n, index=series.index)
        true_positions = valid_positions
        start_idx = 1  # 第1天返回NaN
    
    # 使用searchsorted快速定位每个位置最近的True
    indices = np.arange(n)
    idx = np.searchsorted(true_positions, indices, side='right') - 1
    
    # 计算距离
    result = np.where(idx >= 0, indices - true_positions[idx], np.nan)
    
    # 第1天符合条件返回NaN
    if start_idx == 1:
        result[0] = np.nan
    
    # True的位置返回0
    for pos in true_positions:
        result[pos] = 0
    
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
                if not filtered_df.empty:
                    return filtered_df.sort_values('trade_date')
                # 数据为空，走拉取流程

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
        time.sleep(0.15)

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
                        # 合并已有缓存（批次接口返回的数据量有限，不能覆盖已有缓存）
                        if os.path.exists(cache_file):
                            try:
                                old_df = pd.read_csv(cache_file)
                                old_df['trade_date'] = old_df['trade_date'].astype(str)
                                stock_df['trade_date'] = stock_df['trade_date'].astype(str)
                                # 合并新旧数据，按trade_date去重，保留旧数据中的非重复行
                                merged = pd.concat([old_df, stock_df], ignore_index=True)
                                merged = merged.drop_duplicates(subset=['trade_date', 'ts_code'], keep='last')
                                merged = merged.sort_values('trade_date').reset_index(drop=True)
                                merged.to_csv(cache_file, index=False)
                                continue
                            except Exception:
                                pass
                        stock_df.to_csv(cache_file, index=False)
                
                downloaded = len(stock_df['ts_code'].unique()) if 'ts_code' in stock_df.columns else len(batch)
                print(f"  批次 {i//batch_size + 1}: 成功下载 {downloaded}/{len(batch)} 只")
            else:
                print(f"  批次 {i//batch_size + 1}: 下载返回空")
            
            # 防止频率限制
            time.sleep(0.15)
            
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
                    time.sleep(0.15)
                except:
                    pass
    


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
    all_themes = ''
    if not theme and stock_info:
        theme = _find_best_theme(stock_info)
        all_themes = find_all_themes(stock_info)
    
    # 如果没有找到多主题，使用最佳主题
    if not all_themes:
        all_themes = theme or ''

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
            theme_data = das.read_theme_analysis(TRADE_DATE)
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
        "所属主题": all_themes,
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
    # V8.0 综合评分（优化版V3 — 聚焦二波启动模式）
    # ====================

    # 第一部分：基础线性评分（权重降低，让二波加分成为区分关键）
    base_score = (
        trend_strength * 18 +           # 趋势强度 — 核心因子
        trend_probability * 9 +
        money_momentum * 16 +           # 资金动量 — 重要
        breakout_strength * 6 +
        volume_explosion * 9 +
        trend_stability * 4 +
        compression_score * 7 +         # 压缩度（震荡调整的标志）
        momentum_score * 13 +           # 动量 — 体现二波启动
        squeeze_compression_score * 9 +  # 压缩洗盘 — 调整后启动前提
        mainline_resonance * 1 +
        position_factor * 6 +           
        leader_factor * 16              # 龙头因子
    )

    # 第二部分：因子交互共振项
    synergy_bonus = (
        compression_score * leader_factor * 7 +
        squeeze_compression_score * volume_explosion * 9 +
        leader_factor * max(theme_strength_bonus - 1, 0) * 15
    )

    # 第三部分：二波启动模式识别（用户偏好：拉升过→震荡调整→第二波刚启动）
    close_series = df['close']
    hhv60_s = float(close_series.tail(60).max())
    llv60_s = float(close_series.tail(60).min())
    hhv20_s = float(close_series.tail(20).max())
    llv20_s = float(close_series.tail(20).min())
    
    close_price = float(close_series.iloc[-1])
    first_wave_height = (hhv60_s - llv60_s) / max(llv60_s, 0.01)  # 60日第一波高度
    pullback_pct = 1 - close_price / max(hhv20_s, 0.01)           # 距20日高回撤
    pos60 = (close_price - llv60_s) / max(hhv60_s - llv60_s, 0.01)  # 60日位置
    
    # 判断是否为"第二波刚启动"（最多加25分）
    second_wave_bonus = 0
    # 条件1：有过明显的拉升（第一波15%+）
    if first_wave_height >= 0.15:
        second_wave_bonus += 6
        # 条件2：从高点回撤5-15%（震荡调整后蓄力）
        if pullback_pct >= 0.05 and pullback_pct <= 0.15:
            second_wave_bonus += 7
        # 条件3：60日位置在40-85%（非底非顶）
        if pos60 >= 0.40 and pos60 <= 0.85:
            second_wave_bonus += 6
        # 条件4：放量上涨3%+（启动迹象）
        if volume_explosion > 0.3 and today_pct > 3:
            second_wave_bonus += 6
    
    # 第四部分：非线性风险惩罚（适度，不压制高分）
    if fail_prob < 0.3:
        risk_penalty = fail_prob * 2
    elif fail_prob < 0.5:
        risk_penalty = 0.6 + (fail_prob - 0.3) * 6
    else:
        risk_penalty = 1.8 + (fail_prob - 0.5) * 10

    # 第五部分：主题置信度门控
    if theme_confidence < 30:
        confidence_gate = 0.85
    elif theme_confidence >= 70:
        confidence_gate = 1.05
    else:
        confidence_gate = 1.0

    # 汇总：直接加法
    v80_raw = (
        base_score 
        + synergy_bonus 
        + second_wave_bonus
        - risk_penalty
    )

    # 主题强化 + 置信度门控
    v75_total = v80_raw * confidence_gate

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


def calc_yri_score(ts_code, stock_info, theme, v7_result, df):
    """
    YRI (Y Recognition Index) 辨识度评分 V2 - 优化版
    
    核心逻辑：衡量股票在市场中的共识辨识度
    高辨识度 = 市场公认的核心标的，资金聚焦度高
    
    评分维度：
    1. 核心公司身份 (25分)：是否为 theme.json 定义的核心/龙头公司
    2. 市场活跃度 (25分)：换手率排名 + 成交量绝对值
    3. 近期表现 (25分)：近期涨幅 + 涨停次数 + 回撤系数
    4. 市值规模 (15分)：大市值通常辨识度更高
    5. 主题龙头地位 (10分)：在主题中的综合排名
    
    返回：0-100 的辨识度评分
    """
    try:
        yri_score = 0
        details = {}
        
        # 1. 核心公司身份 (25分)
        core_company_score = 0
        if theme and stock_info:
            try:
                cfg_path = os.path.join(BASE_DIR, 'theme.json')
                if os.path.exists(cfg_path):
                    with open(cfg_path, 'r', encoding='utf-8') as f:
                        theme_cfg = json.load(f).get('HOT_THEMES', {})
                    if theme in theme_cfg:
                        theme_data = theme_cfg[theme]
                        stock_name = stock_info.get('name', '')
                        
                        # 龙头公司 +25分
                        leader_companies = theme_data.get('leader_companies', [])
                        if stock_name in leader_companies:
                            core_company_score = 25
                        else:
                            # 核心公司 +18分
                            core_companies = theme_data.get('core_companies', [])
                            if stock_name in core_companies:
                                core_company_score = 18
                            else:
                                # 检查ts_code匹配
                                if ts_code in leader_companies:
                                    core_company_score = 25
                                elif ts_code in core_companies:
                                    core_company_score = 18
            except Exception:
                pass
        yri_score += core_company_score
        details['核心身份'] = core_company_score
        
        # 2. 市场活跃度 (25分) - 优化：增加成交量绝对值
        activity_score = 0
        if df is not None and len(df) >= 20:
            try:
                recent_df = df.tail(20)
                # 计算近5日平均换手率
                if 'vol' in recent_df.columns and len(recent_df) >= 5:
                    recent_5d = recent_df.tail(5)
                    avg_vol_5d = recent_5d['vol'].mean()
                    avg_vol_20d = recent_df['vol'].mean()
                    vol_ratio = avg_vol_5d / avg_vol_20d if avg_vol_20d > 0 else 1.0
                    
                    # 2.1 量比评分（15分）
                    vol_ratio_score = 0
                    if vol_ratio >= 2.0:
                        vol_ratio_score = 15
                    elif vol_ratio >= 1.5:
                        vol_ratio_score = 12
                    elif vol_ratio >= 1.2:
                        vol_ratio_score = 9
                    elif vol_ratio >= 1.0:
                        vol_ratio_score = 6
                    else:
                        vol_ratio_score = 3
                    
                    # 2.2 成交量绝对值评分（10分）- 成交额越大越活跃
                    # 计算近5日平均成交额
                    if 'amount' in recent_5d.columns:
                        avg_amount_5d = recent_5d['amount'].mean() / 1e8  # 转为亿元
                    else:
                        avg_amount_5d = (recent_5d['vol'].mean() * recent_5d['close'].mean()) / 1e8 if 'close' in recent_5d.columns else 0
                    
                    amount_score = 0
                    if avg_amount_5d >= 30:  # 日均成交额30亿+
                        amount_score = 10
                    elif avg_amount_5d >= 15:  # 日均成交额15亿+
                        amount_score = 8
                    elif avg_amount_5d >= 8:   # 日均成交额8亿+
                        amount_score = 6
                    elif avg_amount_5d >= 3:   # 日均成交额3亿+
                        amount_score = 4
                    else:
                        amount_score = 2
                    
                    activity_score = vol_ratio_score + amount_score
            except Exception:
                pass
        yri_score += activity_score
        details['市场活跃度'] = activity_score
        
        # 3. 近期表现 (25分) - 优化：增加涨停次数 + 回撤系数
        performance_score = 0
        if df is not None and len(df) >= 20:
            try:
                C = df['close']
                pct_chg = df['pct_chg'] if 'pct_chg' in df.columns else None
                
                # 3.1 涨幅评分（10分）
                pct5 = (C.iloc[-1] / C.iloc[-5] - 1) * 100 if len(C) >= 5 else 0
                pct20 = (C.iloc[-1] / C.iloc[-20] - 1) * 100 if len(C) >= 20 else 0
                
                pct_score = 0
                if pct5 >= 15 or pct20 >= 30:
                    pct_score = 10
                elif pct5 >= 10 or pct20 >= 20:
                    pct_score = 8
                elif pct5 >= 5 or pct20 >= 10:
                    pct_score = 6
                elif pct5 >= 0 or pct20 >= 0:
                    pct_score = 4
                else:
                    pct_score = 2
                
                # 3.2 涨停次数评分（10分）- 优化新增
                zt_score = 0
                if pct_chg is not None and len(pct_chg) >= 20:
                    # 计算涨停次数（主板10%，科创/创业20%）
                    zt_count = 0
                    for i in range(-1, -min(21, len(pct_chg)), -1):
                        pct = pct_chg.iloc[i]
                        # 判断是否为涨停
                        if pct >= 9.5:  # 接近涨停
                            zt_count += 1
                    
                    if zt_count >= 3:
                        zt_score = 10
                    elif zt_count == 2:
                        zt_score = 7
                    elif zt_count == 1:
                        zt_score = 4
                    else:
                        zt_score = 0
                
                # 3.3 回撤系数评分（5分）- 优化新增：回撤越大评分越低
                dd_score = 0
                if len(C) >= 20:
                    # 计算20日内最大回撤
                    close_20d = C.tail(20).values
                    cummax = np.maximum.accumulate(close_20d)
                    drawdowns = (close_20d - cummax) / cummax
                    max_dd = abs(drawdowns.min()) * 100  # 转为百分比
                    
                    if max_dd <= 5:
                        dd_score = 5   # 最大回撤<=5%
                    elif max_dd <= 10:
                        dd_score = 4   # 最大回撤5-10%
                    elif max_dd <= 15:
                        dd_score = 3   # 最大回撤10-15%
                    elif max_dd <= 20:
                        dd_score = 2   # 最大回撤15-20%
                    else:
                        dd_score = 1   # 最大回撤>20%
                
                performance_score = pct_score + zt_score + dd_score
            except Exception:
                pass
        yri_score += performance_score
        details['近期表现'] = performance_score
        
        # 4. 市值规模 (15分)
        market_cap_score = 0
        if stock_info:
            try:
                market_cap = stock_info.get('total_market_cap') or stock_info.get('market_cap')
                if market_cap and market_cap > 0:
                    # 转换为亿元
                    if market_cap > 1e12:
                        market_cap_yi = market_cap / 1e8
                    else:
                        market_cap_yi = market_cap / 1e8
                    
                    if market_cap_yi >= 1000:
                        market_cap_score = 15
                    elif market_cap_yi >= 500:
                        market_cap_score = 13
                    elif market_cap_yi >= 200:
                        market_cap_score = 11
                    elif market_cap_yi >= 100:
                        market_cap_score = 9
                    elif market_cap_yi >= 50:
                        market_cap_score = 7
                    else:
                        market_cap_score = 5
            except Exception:
                pass
        yri_score += market_cap_score
        details['市值规模'] = market_cap_score
        
        # 5. 主题龙头地位 (10分)
        theme_leader_score = 0
        if theme and v7_result:
            try:
                # 基于V7结果中的主题相关指标
                theme_confidence = float(v7_result.get('主题纯度', 30))
                theme_strength_bonus = float(v7_result.get('主题强化系数', 1.0))
                
                # 综合主题地位
                if theme_confidence >= 70 and theme_strength_bonus >= 1.3:
                    theme_leader_score = 10
                elif theme_confidence >= 50 and theme_strength_bonus >= 1.1:
                    theme_leader_score = 8
                elif theme_confidence >= 30:
                    theme_leader_score = 6
                else:
                    theme_leader_score = 4
            except Exception:
                pass
        yri_score += theme_leader_score
        details['主题龙头地位'] = theme_leader_score
        
        # 确保在0-100范围内
        yri_score = min(100, max(0, yri_score))
        
        return round(yri_score, 1), details
    
    except Exception as e:
        print(f"[YRI辨识度] 计算失败: {e}")
        return 50, {}


def calc_tli_score(theme, top_n=10, days=60):
    """
    TLI (Theme Life Index) 主题生命力评分
    
    核心逻辑：衡量主题在最近N天内的持续活跃程度
    高生命力 = 主题持续出现在市场前排，资金关注度高
    
    计算方法：
    1. 查询最近60天主题排名数据
    2. 统计主题出现在前10名的次数
    3. 根据出现频率和平均排名打分
    
    参数：
        theme: 主题名称
        top_n: 前排定义（默认前10名）
        days: 统计天数（默认60天）
    
    返回：0-100 的主题生命力评分
    """
    try:
        if not theme:
            return 50, {"错误": "主题为空"}
        
        db_path = os.path.join(BASE_DIR, 'cache_backbone_tushare', 'theme_trend_sentiment.db')
        if not os.path.exists(db_path):
            return 50, {"错误": "数据库不存在"}
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 查询最近60天内该主题的排名情况
        cursor.execute("""
            SELECT trade_date, rank, composite_score 
            FROM theme_scores 
            WHERE theme = ? 
            AND trade_date >= date('now', '-{} days')
            ORDER BY trade_date DESC
        """.format(days), (theme,))
        
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return 50, {"说明": "无历史数据"}
        
        # 统计指标
        total_days = len(rows)
        top10_count = sum(1 for row in rows if row[1] <= top_n)
        top5_count = sum(1 for row in rows if row[1] <= 5)
        top3_count = sum(1 for row in rows if row[1] <= 3)
        
        avg_rank = sum(row[1] for row in rows) / total_days if total_days > 0 else 50
        avg_score = sum(row[2] for row in rows) / total_days if total_days > 0 else 0
        
        # 计算生命力评分 (0-100)
        # 基础分：前10出现频率
        base_score = (top10_count / total_days) * 60 if total_days > 0 else 0
        
        # 加分项：前5和前3次数
        bonus_top5 = (top5_count / total_days) * 20 if total_days > 0 else 0
        bonus_top3 = (top3_count / total_days) * 15 if total_days > 0 else 0
        
        # 平均排名修正：平均排名越靠前，加分越多
        rank_bonus = 0
        if avg_rank <= 3:
            rank_bonus = 5
        elif avg_rank <= 5:
            rank_bonus = 3
        elif avg_rank <= 10:
            rank_bonus = 1
        
        tli_score = base_score + bonus_top5 + bonus_top3 + rank_bonus
        tli_score = min(100, max(0, tli_score))
        
        details = {
            "统计天数": total_days,
            "前10次数": top10_count,
            "前5次数": top5_count,
            "前3次数": top3_count,
            "平均排名": round(avg_rank, 1),
            "平均综合分": round(avg_score, 1),
            "前10频率": f"{top10_count/total_days*100:.1f}%" if total_days > 0 else "0%"
        }
        
        return round(tli_score, 1), details
    
    except Exception as e:
        print(f"[TLI生命力] 计算失败: {e}")
        return 50, {"错误": str(e)}




def calc_sector_position_score(ts_code, stock_info, theme, v75_result, df):
    """sector_position 板块位置评分 V3 - 龙头拉开机制 + 板块分层系统（无连板因子）
    
    核心逻辑：
    1. 板块分层系统：S/A/B/C级主线分层基础分
    2. 龙头拉开机制：真龙头非线性加成，后排惩罚
    
    评分公式：
    final_score = 板块分层基础分 + 龙头加成
    
    参数设计：
    - S级（≥80分）：基础分50，真龙头+50=100，准龙头+30=80，后排-20=0
    - A级（60-80分）：基础分30，真龙头+50=80，准龙头+30=60，后排-20=0
    - B级（40-60分）：基础分15，真龙头+50=65，准龙头+30=45，后排-20=0
    - C级（<40分）：基础分0，真龙头+50=50，准龙头+30=30，后排-20=0
    
    效果：
    - S级龙头自然拉到100分
    - S级核心80分
    - 后排自动掉到0-30分
    """
    try:
        if not theme:
            return 50, {}
        
        # =========================
        # 1. 板块分层系统 - 获取主线等级
        # =========================
        theme_tier = 'C'  # 默认C级
        theme_composite_score = 50
        theme_data = None
        
        try:
            theme_data = das.read_theme_analysis(TRADE_DATE)
            if theme_data and theme_data.get('themes'):
                for t in theme_data['themes']:
                    if t.get('theme_name') == theme:
                        theme_composite_score = float(t.get('composite_score', 50))
                        # 分层：S≥80, A 60-80, B 40-60, C <40
                        if theme_composite_score >= 80:
                            theme_tier = 'S'
                        elif theme_composite_score >= 60:
                            theme_tier = 'A'
                        elif theme_composite_score >= 40:
                            theme_tier = 'B'
                        break
        except:
            pass
        
        # 板块分层基础分
        tier_base_score = {'S': 50, 'A': 30, 'B': 15, 'C': 0}.get(theme_tier, 0)
        
        # =========================
        # 2. 龙头识别 - 多维度判断
        # =========================
        is_leader = False
        is_core = False
        sector_rank = 10  # 默认后排

        
        # 2.1 从主题分析数据获取龙头标记和连板高度
        if theme_data and theme_data.get('themes'):
            for t in theme_data['themes']:
                if t.get('theme_name') == theme:
                    lc = t.get('leader_code', '')
                    cc = t.get('core_code', '')
                    
                    # 龙头代码匹配
                    if lc and str(ts_code) in str(lc):
                        is_leader = True
                        sector_rank = 1
                    elif cc and str(ts_code) in str(cc):
                        is_core = True
                        sector_rank = 2
                    
                    break
                    break
        
        # 2.2 从theme.json配置判断
        if sector_rank >= 10 and stock_info:
            try:
                cfg_path = os.path.join(BASE_DIR, 'theme.json')
                if os.path.exists(cfg_path):
                    with open(cfg_path, 'r', encoding='utf-8') as f:
                        tc = json.load(f).get('HOT_THEMES', {})
                    if theme in tc:
                        td = tc[theme]
                        sn = stock_info.get('name', '')
                        if sn in td.get('leader_companies', []):
                            is_leader = True
                            sector_rank = 1
                        elif sn in td.get('core_companies', []):
                            is_core = True
                            sector_rank = 2
            except:
                pass
        
        # 2.3 从V7指标推断（涨幅、成交额占比）
        if sector_rank >= 10 and v75_result and df is not None:
            try:
                # 今日涨幅
                if len(df) >= 2:
                    today_pct = (df['close'].iloc[-1] / df['close'].iloc[-2] - 1) * 100
                    
                    # 涨幅≥7% 且 量能爆发 → 可能是龙头
                    vol_explosion = float(v75_result.get('量能爆发', 0))
                    if today_pct >= 7 and vol_explosion >= 0.7:
                        is_leader = True
                        sector_rank = 1
                    elif today_pct >= 5 and vol_explosion >= 0.5:
                        is_core = True
                        sector_rank = 2
                
                # 主题纯度高 + 强化系数高
                tc = float(v75_result.get('主题纯度', 30))
                tsb = float(v75_result.get('主题强化系数', 1.0))
                if tc >= 70 and tsb >= 1.3:
                    if sector_rank > 2:
                        sector_rank = 2
                        is_core = True
                elif tc >= 50 and tsb >= 1.1:
                    if sector_rank > 3:
                        sector_rank = 3
            except:
                pass
        
        # 2.4 从V7.5综合评分推断（趋势强度+主题纯度+资金动量都高的视为核心）
        if sector_rank >= 5 and v75_result:
            try:
                trend_strength = float(v75_result.get('趋势强度', 0))
                theme_conf = float(v75_result.get('主题纯度', 30))
                money_momentum = float(v75_result.get('资金动量', 0))
                # 趋势强度>0.7 + 主题纯度>60 + 资金动量>0.6 → 准龙头
                if trend_strength >= 0.75 and theme_conf >= 60 and money_momentum >= 0.65:
                    is_core = True
                    sector_rank = min(sector_rank, 2)
                # 三个维度都很高 → 真龙头
                if trend_strength >= 0.85 and theme_conf >= 75 and money_momentum >= 0.75:
                    is_leader = True
                    sector_rank = 1
            except:
                pass
        
        # =========================
        # 3. 龙头拉开机制 - 非线性加成
        # =========================
        leader_bonus = 0
        if is_leader:
            leader_bonus = 50  # 真龙头 +50分
        elif is_core:
            leader_bonus = 30  # 准龙头 +30分
        elif sector_rank >= 10:
            leader_bonus = -20  # 后排 -20分惩罚
        

        # =========================
        # 5. 综合评分计算
        # =========================
        # 基础分：板块分层
        base_score = tier_base_score
        
        # 龙头加成
        final_score = base_score + leader_bonus
        
        # 限制在0-100范围
        final_score = min(100, max(0, final_score))
        
        # =========================
        # 6. 返回结果
        # =========================
        details = {
            '板块等级': theme_tier,
            '板块综合分': round(theme_composite_score, 1),
            '板块排名': sector_rank,
            '是否龙头': is_leader,
            '是否核心': is_core,
            '分层基础分': tier_base_score,
            '龙头加成': leader_bonus
        }
        
        return round(final_score, 1), details
        
    except Exception as e:
        print(f'[sector_position] 失败: {e}')
        import traceback
        traceback.print_exc()
        return 50, {}


def calc_capital_dominance_score(ts_code, stock_info, theme, v75_result, df):
    """capital_dominance 资金主导力评分 (0-100)
    
    量能集中度(40分) + 资金活跃度(35分) + 主力买入迹象(25分)
    """
    try:
        score = 0
        # 1. 量能集中度 (40分)
        vol_conc = 0
        if df is not None and len(df) >= 20:
            try:
                v = df['vol'].values
                c = df['close'].values
                amt_5d = (v[-5:] * c[-5:]).mean()
                amt_20d = (v * c).mean()
                ar = amt_5d / amt_20d if amt_20d > 0 else 1.0
                if ar >= 2.0: vol_conc = 40
                elif ar >= 1.5: vol_conc = 32
                elif ar >= 1.2: vol_conc = 24
                elif ar >= 1.0: vol_conc = 16
                else: vol_conc = 10
            except:
                pass
        score += vol_conc
        # 2. 资金活跃度 (35分)
        cap_act = 0
        if v75_result:
            try:
                ve = float(v75_result.get('量能爆发', 0))
                mm = float(v75_result.get('资金动量', 0))
                cap_act = min(35, round((ve * 0.5 + mm * 0.5) * 35))
            except:
                pass
        score += cap_act
        # 3. 主力买入迹象 (25分)
        buying = 0
        if df is not None and len(df) >= 2:
            try:
                cv = df['close'].values
                vv = df['vol'].values
                tp = (cv[-1] / cv[-2] - 1) * 100
                vr = vv[-1] / max(vv[-20:].mean(), 0.01)
                if tp >= 5 and vr >= 2.0: buying = 25
                elif tp >= 3 and vr >= 1.5: buying = 20
                elif tp >= 2 and vr >= 1.3: buying = 15
                elif tp >= 1 and vr >= 1.2: buying = 10
                elif tp > 0: buying = 5
                else: buying = 2
            except:
                pass
        score += buying
        score = min(100, max(0, score))
        return round(score, 1), {'量能集中度': vol_conc, '资金活跃度': cap_act, '主力买入': buying}
    except Exception as e:
        print(f'[capital_dominance] 失败: {e}')
        return 50, {}

def calc_unified_stock_score(df, ts_code='', theme=''):
    """
    统一股票评分算法 - 整合V9和整合评分
    
    目标：找到次日介入后上涨概率高、失败概率低的股票
    
    核心公式：
    FinalScore = 趋势强度(30%) + 资金健康度(25%) + 位置安全性(15%) + 热度持续性(20%) + 基本面(10%)
    
    输出：
        - 综合评分 (0-100)
        - 失败概率 (10-90%)
        - 推荐理由
        - 各维度评分详情
    """
    try:
        if df is None or not isinstance(df, pd.DataFrame) or len(df) < 20:
            return 0, "数据不足", {}, 50
        
        df = df.reset_index(drop=True)
        C = df['close'].values
        ts_code = ts_code or ''
        
        # =========================
        # 基础数据计算
        # =========================
        close_series = df['close']
        MA5 = float(close_series.rolling(5).mean().iloc[-1])
        MA10 = float(close_series.rolling(10).mean().iloc[-1])
        MA20 = float(close_series.rolling(20).mean().iloc[-1])
        MA60 = float(close_series.rolling(60).mean().iloc[-1])
        HHV20 = float(close_series.tail(20).max())
        LLV20 = float(close_series.tail(20).min())
        current_price = float(C[-1])
        
        if len(C) >= 2:
            today_pct = float((C[-1] / C[-2] - 1) * 100)
        else:
            today_pct = 0
        
        # =========================
        # 1. 趋势强度评分（30%）- 互斥评分
        # =========================
        trend_score = 0
        
        # 均线状态（互斥评分，权重最高）
        if current_price > MA5 > MA10 > MA20 > MA60:
            trend_score += 35  # 完全多头排列
        elif current_price > MA20 > MA60:
            trend_score += 25  # 上升趋势
        elif current_price > MA20 > MA10:
            trend_score += 15  # 震荡偏强
        elif current_price > MA20:
            trend_score += 8   # 初步企稳
        elif current_price > MA10:
            trend_score += 3   # 弱势
        else:
            trend_score -= 10   # 下降趋势
        
        # MA20均线斜率
        if len(C) >= 25:
            ma20_slope = (MA20 - float(close_series.rolling(20).mean().iloc[-6])) / MA20
            if ma20_slope > 0.03:
                trend_score += 20  # 强上升
            elif ma20_slope > 0.01:
                trend_score += 12  # 温和上升
            elif ma20_slope > 0:
                trend_score += 5   # 缓慢上升
            else:
                trend_score -= 5   # 下降
        
        # 近期涨幅（5日，适度加分）
        if len(C) >= 6:
            ret_5 = (C[-1] / C[-6] - 1) * 100
            if 3 <= ret_5 <= 12:
                trend_score += 15  # 最佳区间：稳健上涨
            elif 0 <= ret_5 < 3:
                trend_score += 8   # 小幅上涨
            elif ret_5 > 12:
                trend_score += 5   # 涨幅过大，谨慎
            elif ret_5 >= -3:
                trend_score -= 5   # 小幅回调
            else:
                trend_score -= 15  # 大幅下跌
        
        # 突破前高
        breakout_strength = 0.5
        if current_price >= HHV20:
            trend_score += 15  # 突破前高
            breakout_strength = 1.0
        elif current_price >= HHV20 * 0.97:
            trend_score += 10  # 接近前高
            breakout_strength = 0.85
        elif current_price >= HHV20 * 0.90:
            trend_score += 5   # 离前高较远
            breakout_strength = 0.6
        
        trend_score = min(100, max(0, trend_score))
        
        # =========================
        # 2. 资金健康度评分（25%）
        # =========================
        capital_score = 50
        
        # 量能分析
        if 'vol' in df.columns and len(df) >= 10:
            vol_ma5 = float(df['vol'].tail(5).mean())
            vol_ma20 = float(df['vol'].tail(20).mean())
            vol_ratio = vol_ma5 / vol_ma20 if vol_ma20 > 0 else 1.0
            
            if vol_ratio > 2.0:
                capital_score += 30
            elif vol_ratio > 1.5:
                capital_score += 20
            elif vol_ratio > 1.2:
                capital_score += 10
        
        # 机构资金流
        inst_flow_score = calc_institutional_flow_score(ts_code)
        capital_score += inst_flow_score * 15
        
        capital_score = min(100, max(0, capital_score))
        
        # =========================
        # 3. 位置安全性评分（15%）
        # =========================
        position_score = 50
        
        # 距离前高位置
        dist_to_high = (HHV20 - current_price) / HHV20 if HHV20 > 0 else 0
        if dist_to_high <= 0.05:
            position_score += 25  # 接近前高
        elif dist_to_high <= 0.15:
            position_score += 15
        
        # 从低点涨幅
        run_up = (current_price - LLV20) / LLV20 if LLV20 > 0 else 0
        if run_up <= 0.15:
            position_score += 20
        elif run_up <= 0.25:
            position_score += 10
        
        # 90日振幅（压缩度）
        if len(df) >= 90:
            range90 = (df['high'].values[-90:].max() - df['low'].values[-90:].min()) / df['low'].values[-90:].min()
            if range90 < 0.25:
                position_score += 10
        
        position_score = min(100, max(0, position_score))
        
        # =========================
        # 4. 热度持续性评分（20%）
        # =========================
        hot_score = 50
        
        # 主题生命力
        tli_score, _ = calc_tli_score(theme, top_n=10, days=60)
        hot_score += (tli_score - 50) * 0.5
        
        # 热榜排名加分
        hot_rank_bonus, best_rank, hot_appear_count = get_hot_list_best_rank_bonus(ts_code, days=20)
        hot_score += hot_rank_bonus
        
        # 主题TOP3次数
        if hasattr(getattr(globals().get('result_df', None), 'iloc', None), '__call__'):
            # 从主题评分中获取
            pass
        
        hot_score = min(100, max(0, hot_score))
        
        # =========================
        # 5. 基本面评分（10%）
        # =========================
        fundamental_score = 50
        
        # 技术壁垒
        tech_barrier = calc_tech_barrier_score(ts_code)
        fundamental_score += tech_barrier * 0.5
        
        # 60日涨幅趋势（作为业绩代理）
        if len(C) >= 60:
            ret_60 = (C[-1] / C[-60] - 1) * 100
            if ret_60 >= 20:
                fundamental_score += 15
            elif ret_60 >= 10:
                fundamental_score += 8
            elif ret_60 >= 0:
                fundamental_score += 3
        
        fundamental_score = min(100, max(0, fundamental_score))
        
        # =========================
        # 6. 追高惩罚
        # =========================
        penalty = 0
        if len(C) >= 6:
            ret_5 = (C[-1] / C[-6] - 1) * 100
            if ret_5 > 8:
                penalty += min((ret_5 - 8) * 3, 25)
            if ret_5 > 15:
                penalty += 10  # 额外惩罚
        
        # =========================
        # 7. 龙头/核心加分
        # =========================
        leader_bonus = 0
        if breakout_strength >= 0.95 and dist_to_high <= 0.05:
            leader_bonus = 15  # 突破前高的龙头
        elif breakout_strength >= 0.80:
            leader_bonus = 8   # 接近前高的核心
        
        # =========================
        # 8. 综合得分
        # =========================
        final_score = (
            trend_score * 0.30 +
            capital_score * 0.25 +
            position_score * 0.15 +
            hot_score * 0.20 +
            fundamental_score * 0.10
        ) - penalty + leader_bonus
        
        final_score = min(95, max(0, final_score))
        
        # =========================
        # 9. 失败概率计算
        # =========================
        failure_prob = 50
        
        failure_prob -= (trend_score - 50) * 0.25
        failure_prob -= (capital_score - 50) * 0.25
        failure_prob -= (position_score - 50) * 0.20
        failure_prob -= (hot_score - 50) * 0.15
        failure_prob -= (fundamental_score - 50) * 0.15
        
        failure_prob += penalty * 1.5
        
        if hot_score >= 85:
            failure_prob += 8  # 过热风险
        elif hot_score < 40:
            failure_prob += 10  # 缺乏热度风险
        
        failure_prob = min(90, max(10, failure_prob))
        
        # =========================
        # 10. 推荐理由
        # =========================
        reason_parts = []
        
        if trend_score >= 80:
            reason_parts.append("趋势强劲")
        elif trend_score >= 60:
            reason_parts.append("趋势良好")
        
        if capital_score >= 80:
            reason_parts.append("资金充沛")
        elif capital_score >= 60:
            reason_parts.append("资金健康")
        
        if position_score >= 80:
            reason_parts.append("位置安全")
        elif position_score >= 60:
            reason_parts.append("位置合理")
        
        if hot_score >= 70:
            reason_parts.append("热度持续")
        
        if leader_bonus >= 10:
            reason_parts.append("👑龙头")
        elif leader_bonus >= 5:
            reason_parts.append("⭐核心")
        
        if hot_rank_bonus > 0:
            reason_parts.append(f"热榜Top{best_rank}")
        
        if penalty > 5:
            reason_parts.append(f"⚠️追高-{penalty:.0f}")
        
        recommendation = " | ".join(reason_parts) if reason_parts else "观察中"
        
        # =========================
        # 11. 详细信息
        # =========================
        details = {
            '趋势强度': round(trend_score, 1),
            '资金健康度': round(capital_score, 1),
            '位置安全性': round(position_score, 1),
            '热度持续性': round(hot_score, 1),
            '基本面': round(fundamental_score, 1),
            '追高惩罚': round(penalty, 1),
            '龙头加分': leader_bonus,
            '热榜最佳排名': best_rank if best_rank <= 100 else 0,
            '热榜上榜次数': hot_appear_count,
        }
        
        return round(final_score, 1), recommendation, details, round(failure_prob, 1)
        
    except Exception as e:
        print(f"[统一评分] 异常: {e}")
        import traceback
        traceback.print_exc()
        return 0, "计算异常", {}, 50


def calc_dual_layer_score_v9(df, ts_code='', stock_info=None, theme=''):
    """
    V9综合评分系统 V4 - 龙头拉开机制 + 板块分层系统
    兼容旧接口，内部调用统一评分算法
    """
    # 转换为旧格式输出
    final_score, recommendation, details, failure_prob = calc_unified_stock_score(df, ts_code, theme)
    
    # 兼容旧格式
    return {
        "V9总评分": final_score,
        "失败概率": failure_prob,
        "推荐理由": recommendation,
        "趋势强度": details.get('趋势强度', 0),
        "资金健康度": details.get('资金健康度', 0),
        "位置安全性": details.get('位置安全性', 0),
        "热度持续性": details.get('热度持续性', 0),
        "基本面": details.get('基本面', 0),
        "V9评分说明": f"综合评分={final_score} | 失败概率={failure_prob}%"
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


def find_all_themes(stock_info, min_confidence=0.3):
    """
    查找股票所属的所有主题（支持多主题）
    
    Args:
        stock_info: 股票信息字典
        min_confidence: 最小置信度阈值，默认0.3
    
    Returns:
        所有匹配主题的字符串，用逗号分隔
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

        # 收集所有置信度超过阈值的主题
        matched_themes = []
        for theme_name in theme_cfg.keys():
            confidence = calc_theme_confidence(stock_info, theme_name)
            if confidence >= min_confidence:
                # 带上置信度信息
                matched_themes.append(f"{theme_name}({confidence:.2f})")

        # 按置信度降序排序
        matched_themes.sort(key=lambda x: float(x.split('(')[1].rstrip(')')), reverse=True)
        
        return ','.join(matched_themes) if matched_themes else ''

    except Exception as e:
        print(f"[V7多主题] 选择失败: {e}")
        return ''



def calc_tech_barrier_score(ts_code):
    """
    技术壁垒评分（基本面）
    从 tushare 财务缓存读取 ROE/毛利率/研发费用率

    Returns:
        float: 0~10 分
    """
    try:
        csv_path = os.path.join(CACHE_DIR, f"{ts_code}.csv")
        if not os.path.exists(csv_path):
            return 0
        df = pd.read_csv(csv_path)
        if df.empty or 'close' not in df.columns:
            return 0
        df = df.sort_values('trade_date', ascending=False)

        # 用 pct_chg 的标准差衡量波动（波动小→壁垒高→高分）
        if 'pct_chg' in df.columns and len(df) >= 20:
            pct_vals = df['pct_chg'].head(60).dropna().astype(float)
            if len(pct_vals) >= 20:
                vol = pct_vals.std()
                # 低波动→壁垒高：vol<=2%→5分, vol>=4%→0分
                vol_score = max(0, min(5, 5 * (4 - vol) / 2))
            else:
                vol_score = 0
        else:
            vol_score = 0

        # 用涨幅趋势判断基本面强度
        if len(df) >= 60:
            close_60d = float(df.iloc[min(59, len(df)-1)]['close'])
            close_now = float(df.iloc[0]['close'])
            ret_60d = (close_now - close_60d) / close_60d if close_60d > 0 else 0
            # 60日涨幅>20%→+3分, >50%→+5分
            trend_score = min(5, max(0, ret_60d * 10))
        else:
            trend_score = 0

        return round(min(10, vol_score + trend_score), 1)
    except Exception:
        return 0


def calc_institutional_flow_score(ts_code):
    """
    机构资金流评分
    从 moneyflow 缓存读取大单数据

    Returns:
        float: 0~5 分
    """
    try:
        # 尝试从当日 moneyflow 数据获取
        from datetime import datetime, timedelta
        for offset in range(5):
            check_date = (datetime.now() - timedelta(days=offset)).strftime('%Y%m%d')
            mf_path = os.path.join(CACHE_DIR, f"moneyflow_{check_date}.csv")
            if os.path.exists(mf_path):
                mf_df = pd.read_csv(mf_path)
                if ts_code in mf_df['ts_code'].values:
                    row = mf_df[mf_df['ts_code'] == ts_code].iloc[0]
                    buy_lg = float(row.get('buy_lg_vol', 0)) if pd.notna(row.get('buy_lg_vol')) else 0
                    sell_lg = float(row.get('sell_lg_vol', 0)) if pd.notna(row.get('sell_lg_vol')) else 0
                    net_lg = buy_lg - sell_lg
                    if net_lg > 1e6:
                        return 5  # 大单大幅净买入
                    elif net_lg > 0:
                        return 3  # 大单小幅净买入
                    else:
                        return 0  # 大单净卖出
        return 0
    except Exception:
        return 0


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
            # V10开仓评分
            open_score, structure_type, recommendation = calc_hot_money_open_score_v10(
                 v7_result, df, v7_result, v7_result.get('所属主题', '')
            )
            
        except Exception as e:
            print(f"[开仓评分V10] {v7_result.get('代码', '')} 计算失败: {e}")
            continue
        
        ranked_stocks.append({
            '代码': v7_result.get('代码', ''),
            '名称': v7_result.get('名称', ''),
            '现价': v7_result.get('现价', 0),
            '涨跌幅': v7_result.get('涨跌幅', 0),
            '所属主题': v7_result.get('所属主题', ''),
            'V9总评分': v7_result.get('V9总评分', v7_result.get('V7总评分', 0)),
            '失败概率': v7_result.get('失败概率', 0),
            '量能爆发': v7_result.get('量能爆发', 0),
            '突破强度': v7_result.get('突破强度', 0),
            '结构类型': structure_type,
            '开仓评分': open_score,
            '推荐理由': recommendation,
            '所属状态': v7_result.get('所属状态', ''),
            '主题趋势分': v7_result.get('主题趋势分', 0),
            '主题情绪分': v7_result.get('主题情绪分', 0),
        })
    
    # 按开仓评分降序排序
    ranked_stocks.sort(key=lambda x: x['开仓评分'], reverse=True)
    return ranked_stocks


def print_hot_money_open_report(ranked_stocks, top_n=10):
    """打印游资开仓报告"""
    print("\n" + "=" * 80)
    print("🔥 游资最强开仓标的 (TOP " + str(top_n) + ")")
    print("=" * 80)
    
    for i, stock in enumerate(ranked_stocks[:top_n], 1):
        print(f"\n【第{i}名】{stock['名称']} ({stock['代码']})")
        print(f"  结构类型: {stock['结构类型']}")
        print(f"  开仓评分: {stock['开仓评分']}")
        print(f"  V9基础分: {stock['V9总评分']} | 失败概率: {stock['失败概率']:.1%}")
        print(f"  今日涨幅: {stock['涨跌幅']:.2f}%")
        print(f"  量能爆发: {stock['量能爆发']:.2f} | 突破强度: {stock['突破强度']:.2f}")
        print(f"  所属主题: {stock.get('所属主题', '')}")
        print(f"  推荐理由: {stock['推荐理由']}")
    
    print("\n" + "-" * 80)
    print("📋 完整排名表:")
    print("-" * 80)
    print(f"{'排名':<4} {'代码':<12} {'名称':<8} {'结构类型':<10} {'开仓分':<8} {'V9分':<8} {'主题':<12}")
    print("-" * 80)
    
    for i, stock in enumerate(ranked_stocks, 1):
        print(f"{i:<4} {stock['代码']:<12} {stock['名称']:<8} {stock['结构类型']:<10} "
              f"{stock['开仓评分']:<8.1f} {stock['V9总评分']:<8.1f} {stock['所属主题']:<12}")
    
    print("=" * 80)
    
    return ranked_stocks[:top_n]


def calc_hot_money_open_score_v10(v7_result, df, stock_info, theme=''):
    """
    游资最强开仓评分 V10

    V10 = V9综合评分 + 结构加分 + 突破加分 + 主题热度分 + 热榜排名分 - 追高扣分

    结构识别（参考V9）：
    - 🟢启动型：接近20日高点90~105% + 今日涨幅>1%
    - 🟡加速型：price>MA20>MA60 + 今日涨
    - 🔴高位分歧：距20日低点已涨>20% + price>前高105%
    - 🟡调整型：缩量回调到均线附近 + 跌幅>-3%
    - 兜底：震荡型

    BREAKOUT_BONUS（不变）：
    - 距60日新高<2%:   +10
    - 距120日新高<2%:  +10

    加减项（透明显示）：
    - 结构加分：启动+15, 加速0, 调整+3, 高位-8
    - 追高扣分：加重（5日>5%起扣，线性递增）
    - 压缩加分：90日振幅<30% → +5（避免高分堆积）
    - 主题热度分：60天内进入TOP3次数，每次+1分，最多+10分
    - 热榜排名分：20天内最佳排名分段加分
        - Top10: +15分
        - Top20: +12分
        - Top30: +10分
        - Top50: +8分
        - Top100: +5分
    - 总分使用非线性压缩公式：open = 100 * raw / (raw + 50) 防止天花板效应
    """
    try:
        if not v7_result or df is None or not isinstance(df, pd.DataFrame) or len(df) < 20:
            return 0, "数据不足", ""

        df = df.reset_index(drop=True)
        C = df['close'].values

        # V9综合评分作为基础分
        base_score = float(v7_result.get('V9总评分', v7_result.get('V7总评分', 50)))

        # =========================
        # K线形态基础数据
        # =========================
        close_series = df['close']
        MA20 = float(close_series.rolling(20).mean().iloc[-1])
        MA60 = float(close_series.rolling(60).mean().iloc[-1])
        HHV20 = float(close_series.tail(20).max())
        LLV20 = float(close_series.tail(20).min())
        current_price = float(C[-1])

        if len(C) >= 2:
            today_pct = float((C[-1] / C[-2] - 1) * 100)
        else:
            today_pct = 0

        price_position = current_price / MA20 if MA20 > 0 else 1.0
        run_up_from_20d_low = (current_price - LLV20) / max(LLV20, 0.01)

        volume_explosion = float(v7_result.get('量能爆发', 0))  # 0-1

        # =========================
        # 结构识别（参照V9逻辑）
        # =========================
        structure_type = "未知"
        structure_desc = ""
        structure_bonus = 0

        # 🟢 启动型：接近前高但未大幅透支 + 今日有量
        # 严格条件：低点上来涨幅<20% + 均线乖离<8% + 接近前高
        if (current_price <= HHV20 * 1.05 and current_price >= HHV20 * 0.90 and
            today_pct > 1 and
            run_up_from_20d_low < 0.20 and
            price_position < 1.08):
            structure_type = "🟢启动型"
            structure_bonus = 15
            structure_desc = f"接近前高，均线乖离{((price_position-1)*100):.0f}%，低点上来{run_up_from_20d_low*100:.0f}%，启动形态"
        # 🟡 加速型：均线多头 + 趋势延续（本身已在趋势中，不加分）
        elif price_position > 1.05 and MA20 > MA60 and today_pct > 0:
            structure_type = "🟡加速型"
            structure_bonus = 0
            structure_desc = "趋势加速中"
        # 🔴 高位分歧：大幅透支 + 累计涨幅已大
        elif current_price > HHV20 * 1.05 and run_up_from_20d_low > 0.20:
            structure_type = "🔴高位分歧"
            structure_bonus = -8
            structure_desc = "高位分歧，风险较大"
        # 🟡 调整型：缩量回调到均线附近
        elif price_position < 1.02 and volume_explosion < 0.3 and today_pct > -3:
            structure_type = "🟡调整型"
            structure_bonus = 3
            structure_desc = "缩量调整，关注均线支撑"
        # ⚪ 震荡型：兜底
        else:
            structure_type = "⚪震荡型"
            structure_bonus = 0
            structure_desc = "震荡整理，需观察方向"

        # =========================
        # 成交额打分（直接复用V9的成交额排名逻辑）
        # =========================
        turnover_rank_score = 50
        try:
            if len(df) >= 20:
                recent_df = df.tail(20)
                recent_turnover = recent_df['vol'] * recent_df['close']
                today_t = recent_turnover.iloc[-1]
                ma20_t = recent_turnover.mean()
                tr = today_t / ma20_t if ma20_t > 0 else 1.0
                if tr >= 3:
                    turnover_rank_score = 100
                elif tr >= 2:
                    turnover_rank_score = 85
                elif tr >= 1.5:
                    turnover_rank_score = 70
                elif tr >= 1:
                    turnover_rank_score = 55
                else:
                    turnover_rank_score = 35
        except Exception:
            pass

        # =========================
        # 压缩加分（近90日振幅<30%）
        # =========================
        compression_bonus = 0
        try:
            if len(df) >= 90:
                h90 = df['high'].values[-90:]
                l90 = df['low'].values[-90:]
                range90 = (h90.max() - l90.min()) / l90.min() if l90.min() > 0 else 0
                if range90 < 0.30:
                    compression_bonus = 5
        except Exception:
            pass

        # =========================
        # BREAKOUT_BONUS
        # =========================
        breakout_bonus = 0
        try:
            if len(df) >= 120:
                hhv_60 = df['high'].values[-60:].max()
                hhv_120 = df['high'].values[-120:].max()
                dist_60 = (current_price - hhv_60) / hhv_60 if hhv_60 > 0 else 0
                dist_120 = (current_price - hhv_120) / hhv_120 if hhv_120 > 0 else 0
                if dist_60 > -0.02:
                    breakout_bonus += 10
                if dist_120 > -0.02:
                    breakout_bonus += 10
            elif len(df) >= 60:
                hhv_60 = df['high'].values[-60:].max()
                dist_60 = (current_price - hhv_60) / hhv_60 if hhv_60 > 0 else 0
                if dist_60 > -0.02:
                    breakout_bonus += 10
        except Exception:
            pass

        # =========================
        # 追高扣分（加重版：从5日涨5%开始扣，线性递增）
        # =========================
        recent_penalty = 0
        try:
            n = len(C)
            ret_5 = (C[-1] / C[-6] - 1) * 100 if n >= 6 else 0
            ret_10 = (C[-1] / C[-11] - 1) * 100 if n >= 11 else 0
            ret_20 = (C[-1] / C[-21] - 1) * 100 if n >= 21 else 0
            # 5日涨幅扣分：5%起扣，每多1%多扣2分，最高扣20分
            if ret_5 > 5:
                recent_penalty += min((ret_5 - 5) * 2, 20)
            # 10日涨幅额外扣分：20%起扣
            if ret_10 > 20:
                recent_penalty += min((ret_10 - 20) * 1.5, 15)
            # 20日涨幅额外扣分：40%起扣
            if ret_20 > 40:
                recent_penalty += min((ret_20 - 40) * 1.0, 10)
            recent_penalty = min(recent_penalty, 30)
        except Exception:
            pass

        # =========================
        # 主题热度分（V10新增）
        # 计算60天内该主题出现在TOP3的次数，每次+1分，最多20分
        # =========================
        theme_hot_score = 0
        theme_top3_count = 0
        try:
            if theme:
                db_path = os.path.join(BASE_DIR, 'cache_backbone_tushare', 'theme_trend_sentiment.db')
                if os.path.exists(db_path):
                    conn = sqlite3.connect(db_path)
                    # 获取过去60天的交易日
                    trade_dates_df = pd.read_sql(
                        "SELECT DISTINCT trade_date FROM theme_scores ORDER BY trade_date DESC LIMIT 60",
                        conn
                    )
                    trade_dates = trade_dates_df['trade_date'].tolist()
                    
                    # 统计该主题进入TOP3的次数
                    for td in trade_dates:
                        day_df = pd.read_sql(
                            f"SELECT theme FROM theme_scores WHERE trade_date = '{td}' ORDER BY composite_score DESC LIMIT 3",
                            conn
                        )
                        if not day_df.empty and theme in day_df['theme'].values:
                            theme_top3_count += 1
                    
                    conn.close()
                    
                    # 热度分 = min(次数, 10)
                    theme_hot_score = min(theme_top3_count, 10)
        except Exception as e:
            pass

        # =========================
        # 基本面因子分（技术壁垒+机构资金流）
        # =========================
        ts_code = v7_result.get('代码', '')
        tech_barrier_score = calc_tech_barrier_score(ts_code)
        inst_flow_score = calc_institutional_flow_score(ts_code)

        # =========================
        # 热榜排名加分（V10新增：20天内最佳排名分段加分）
        # =========================
        hot_rank_bonus, best_rank, hot_appear_count = get_hot_list_best_rank_bonus(ts_code, days=20)

        # =========================
        # 总分（非线性压缩公式：天然防止天花板效应）
        # raw = 基础 + 各加分 - 追高扣分
        # open_score = 100 * raw / (raw + 50)
        #    raw=30→38, raw=50→50, raw=70→58, raw=100→67
        #    raw=150→75, raw=200→80（高分区间自然收敛）
        # =========================
        raw_score = base_score + structure_bonus + breakout_bonus + compression_bonus + theme_hot_score + hot_rank_bonus - recent_penalty
        raw_score += tech_barrier_score + inst_flow_score
        open_score = 100 * raw_score / (raw_score + 50) if raw_score > 0 else 0
        open_score = min(95, max(0, open_score))  # 95硬上限防溢出

        # =========================
        # 推荐理由（参照V9风格）
        # =========================
        recommendation = f"{structure_desc}"
        recommendation += f" | V9基础{base_score:.1f}分"
        recommendation += f" | 成交额{turnover_rank_score:.0f}分"

        # 加分项拆解
        bonus_parts = []
        if structure_bonus > 0:
            bonus_parts.append(f"结构+{structure_bonus}分")
        elif structure_bonus < 0:
            bonus_parts.append(f"结构{structure_bonus}分")
        if breakout_bonus > 0:
            bonus_parts.append(f"突破+{breakout_bonus}分")
        if compression_bonus > 0:
            bonus_parts.append(f"压缩+{compression_bonus}分")
        if theme_hot_score > 0:
            bonus_parts.append(f"主题热度+{theme_hot_score}分({theme_top3_count}次TOP3)")
        if recent_penalty > 0:
            bonus_parts.append(f"追高-{recent_penalty}分")
        if tech_barrier_score > 0:
            bonus_parts.append(f"基本面+{tech_barrier_score}分")
        if inst_flow_score > 0:
            bonus_parts.append(f"资金流+{inst_flow_score}分")
        if hot_rank_bonus > 0:
            bonus_parts.append(f"热榜Top{best_rank}+{hot_rank_bonus}分({hot_appear_count}次)")

        if bonus_parts:
            recommendation += f" | 修正:{','.join(bonus_parts)}"

        recommendation += f" | V10开仓={open_score:.1f} (raw={raw_score:.0f})"

        # 特殊标记（参照V9）
        if structure_type == "🟢启动型" and float(v7_result.get('失败概率', 0.5)) < 0.25:
            recommendation = "⭐重点关注: " + recommendation
        elif structure_type == "🔴高位分歧":
            recommendation = "⚠️谨慎: " + recommendation

        return round(open_score, 1), structure_type, recommendation

    except Exception as e:
        print(f"[开仓评分V10] 异常: {e}")
        import traceback
        traceback.print_exc()
        return 0, "计算异常", ""


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


def strategy(df, code, emotion_stage):
    """优化版本：向量化计算 + 提前过滤 + 缓存复用"""
    
    # ===== 快速前置过滤（低成本判断优先）=====
    if len(df) < 80:
        return False
    
    # ST股票过滤（代码前缀判断，无需查询字典）
    if code.startswith('1') or code.startswith('2'):
        return False
    
    # 两个月涨幅过滤
    if len(df) >= 40:
        close_values = df['close'].values
        ret_2m = close_values[-1] / close_values[-40] - 1
        if ret_2m > 1.0:
            return False
    
    # ===== 数据提取（一次提取，多次使用）=====
    C = df['close'].values
    O = df['open'].values
    H = df['high'].values
    L = df['low'].values
    VOL = df['vol'].values
    
    # ===== 创业板/科创板判断 =====
    ST = code.startswith('3') or code.startswith('688')
    
    # ST名称过滤（延后到这里，只在必要时调用）
    StockName = get_stock_name(code)
    ST1 = (StockName.upper().startswith('ST') or 
            StockName.upper().startswith('*ST'))
    if ST1:
        return False
    
    # ===== 启动过滤：60日振幅 =====
    if len(df) >= 60:
        hh = H[-60:].max()
        ll = L[-60:].min()
        if (hh / ll - 1) > 1.8:
            return False
    
    # ===== 均线计算（一次计算，多次使用）=====
    close_arr = df['close'].values if hasattr(df['close'], 'values') else np.asarray(df['close'])
    C_series = pd.Series(close_arr)
    ma5 = C_series.rolling(5).mean().values
    ma10 = C_series.rolling(10).mean().values
    ma20 = C_series.rolling(20).mean().values
    ma22 = C_series.rolling(30).mean().values
    ma60 = C_series.rolling(60).mean().values
    
    # 均线条件
    if C[-1] >= ma20[-1] * 1.3 and C[-1] / ma60[-1] > 2:
        return False
    
    # 股价必须站上5日、10日、20日均线
    if  C[-1] < ma10[-1] or C[-1] < ma20[-1]:
        return False
    
    # ===== 涨停判断（向量化）=====
    ZT_1day = (C_series.shift(1) / C_series.shift(2) < 1.08) & (C_series / C_series.shift(1) > 1.098)
    ZT_2day = (C_series.shift(1) / C_series.shift(2) >= 1.051) & (C_series / C_series.shift(1) >= 1.051) & (C_series / C_series.shift(2) >= 1.11)
    ZT = ZT_1day | ZT_2day
    
    # 使用向量化的barslast
    ZTTS = barslast(ZT)
    
    # 原版逻辑：如果今天涨停(ztts=0)，取前一个信号
    ztts = ZTTS.iloc[-1]
    if ztts == 0:
        ztts = ZTTS.iloc[-2]
    
    if np.isnan(ztts):
        return False
    
    ztts = int(ztts)
    
    # ===== 过滤：近5天累计涨幅超过20% = 乖离过大，跳过 =====
    if len(C) >= 6 and (C[-1] / C[-6] - 1) > 0.3:
        return False
    
    # ===== 条件1：ZTTS范围 =====
    if ztts < 2 or ztts > 20:
        return False
    
    # ===== 缓存ztts区间数据（避免重复切片）=====
    ztts_close = C[-ztts:]
    ztts_df = df.iloc[-ztts:]
    ztts_vol = ztts_df['vol'].values
    vol_ma5 = ztts_df['vol'].rolling(5).mean().values  # 只计算一次
    
    # ===== TJ条件判断 =====
    ref_close = C[-ztts-1]
    cond2 = (ztts_close < ref_close).sum() == 0
    cond3 = (ztts_close.max() / ztts_close.min()) < 1.3
    cond4 = (C[-1] / H[-ztts-1]) < 1.2  # 修复：H.shift(ztts).iloc[-1] = H[-ztts-1]
    cond5 = H[-ztts:].max() >= H[-60:].max() * 0.8
    cond6 = ma22[-1] >= ma22[-2]
    
    # 量能条件（复用vol_ma5）
    cond_low_vol = (ztts_vol < vol_ma5 * 0.9).any()
    
    # 回撤计算（向量化）
    cum_max = np.maximum.accumulate(ztts_close)
    drawdown = (ztts_close - cum_max) / cum_max
    cond_dd = drawdown.min() >= -0.15
    
    # 放量大跌判断（复用vol_ma5）
    down_k = ztts_df['close'].values < ztts_df['open'].values
    big_vol = ztts_vol > vol_ma5 * 1.5
    big_drop = ztts_df['pct_chg'].values < -5
    cond_no_bad_k = ~(down_k & big_vol & big_drop).any()
    
    cond7 = cond_low_vol and cond_no_bad_k
    
    TJ = cond3 and cond4 and cond5 and cond6 
    if not TJ:
        return False
    
    # ===== XH 判断 =====
    highest_close = C[-ztts-1:-1].max()
    
    # 量能接近前高条件：当前成交量达到ztts区间内最高成交量的80%以上
    vol_peak = VOL[-ztts-1:-1].max()
    vol_condition = VOL[-1] >= vol_peak * 0.7 if vol_peak > 0 else True
    
    cond_xh1 = ((C[-1] > highest_close) or (C[-1] > C[-2] and C[-1] > C[-3] and C[-1]/C[-2] > 1.05 and C[-1]/C[-2] < 1.15 and vol_condition))
    cond_xh2 = C[-1] > C[-2] and C[-1] / ma5[-1] < 1.11 and C[-1] / ma5[-1] > 0.97
    
    # 前两日低点必须贴近5日线（1%以内）
    cond_low_near_ma5 = (abs(L[-2] - ma5[-2]) / ma5[-2] < 0.02) and (abs(L[-3] - ma5[-3]) / ma5[-3] < 0.02)
    # 连涨天数少于4（从最近一天往前数）
    consec_up = 0
    for i in range(len(C)-1, 0, -1):
        if C[i] > C[i-1]:
            consec_up += 1
        else:
            break
    cond_consec_up_lt_4 = consec_up < 4
    
    return cond_xh1 and cond_xh2


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
    """筛选近20天内出现的、涨幅不大且符合技术形态条件的个股"""
    try:
        # 加载近20天的历史数据
        history_df = load_history(days=20)
        
        if history_df.empty:
            return [], "暂无历史数据"
        
        # 获取近20天内出现过的股票（去重，不含今天）
        twenty_days_ago = (datetime.now() - timedelta(days=21)).strftime('%Y%m%d')
        today_date = TRADE_DATE
        recent_stocks = history_df[(history_df['date'] >= twenty_days_ago) & (history_df['date'] < today_date)]
        
        if recent_stocks.empty:
            return [], "近20天无历史数据"
        
        # 去重，保留每只股票最近一次出现的信息
        recent_stocks = recent_stocks.drop_duplicates(subset=['code'], keep='first')
        
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
            cache_close = 0.0
            cache_pct_chg = 0.0
            cache_vol_ratio = 0.0
            
            # 直接从缓存读取K线数据计算MA5和形态
            try:
                cache_file = os.path.join(CACHE_DIR, f"{ts_code}.csv")
                if os.path.exists(cache_file):
                    df = pd.read_csv(cache_file)
                    df['trade_date'] = df['trade_date'].astype(str)
                    df = df[df['trade_date'] <= TRADE_DATE]
                    df = df.sort_values('trade_date').tail(10)  # 取最近10天
                    
                    if len(df) >= 5:
                        # 计算5日均线和量比
                        df['ma5'] = df['close'].rolling(window=5).mean()
                        df['vol_ma5'] = df['vol'].rolling(window=5).mean()
                        
                        # 最新的K线
                        latest_kline = df.iloc[-1]
                        close = float(latest_kline['close'])
                        ma5 = float(latest_kline['ma5']) if pd.notna(latest_kline['ma5']) else None
                        
                        # 保存今日数据（供后续回退使用）
                        cache_close = float(latest_kline['close'])
                        cache_pct_chg = float(latest_kline.get('pct_chg', 0))
                        latest_vol = float(latest_kline['vol'])
                        vol_ma5 = float(latest_kline['vol_ma5']) if pd.notna(latest_kline['vol_ma5']) and latest_kline['vol_ma5'] > 0 else None
                        cache_vol_ratio = round(latest_vol / vol_ma5, 2) if vol_ma5 else 0.0
                        
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
                # =====改用开仓评分系统评测=====
                stock_industry = industry_dict.get(ts_code, '')
                stock_info = {
                    "name": row['name'],
                    "industries": [stock_industry] if stock_industry else [],
                    "concepts": [],
                    "business_text": ""
                }
                
                last_score = 0.0
                open_score = 0.0
                structure_type = "未知"
                open_recommendation = ""
                
                failure_prob = 0.0
                trend_score = 0.0
                capital_score = 0.0
                position_score = 0.0
                heat_score = 0.0
                fundamental_score = 0.0
                
                try:
                    df_hist = get_hist_data(ts_code)
                    if df_hist is not None and len(df_hist) >= 60:
                        # 使用统一评分算法
                        integrated_score, recommendation, details, failure_prob = calc_unified_stock_score(
                            df_hist, ts_code
                        )
                        open_score = integrated_score
                        last_score = integrated_score  # 统一评分作为综合评分
                        structure_type = details.get('结构类型', '未知')
                        open_recommendation = recommendation
                        
                        # 获取各维度评分
                        trend_score = details.get('趋势强度', 0)
                        capital_score = details.get('资金健康度', 0)
                        position_score = details.get('位置安全性', 0)
                        heat_score = details.get('热度持续性', 0)
                        fundamental_score = details.get('基本面', 0)
                    else:
                        # 数据不足，回退到旧评分
                        last_score = float(row['score']) if str(row['score']).strip() not in ['', 'None'] else 0.0
                        if last_score > 100:
                            last_score = min(last_score, 50)
                        open_score = 0
                        structure_type = "未知"
                        open_recommendation = "数据不足"
                except Exception as e:
                    # 计算失败，回退到旧评分
                    print(f"整合评分计算失败 {ts_code}: {e}")
                    last_score = float(row['score']) if str(row['score']).strip() not in ['', 'None'] else 0.0
                    if last_score > 100:
                        last_score = min(last_score, 50)
                    open_score = 0
                    structure_type = "未知"
                    open_recommendation = "计算失败"
                
                # 优先从缓存文件获取今日价格和涨跌幅（已在MA5计算时读取）
                if cache_close > 0:
                    latest_close = cache_close
                    pct_chg = cache_pct_chg
                else:
                    # 回退到历史数据库数据
                    latest_close = float(row['close']) if str(row['close']).strip() not in ['', 'None'] else 0.0
                    pct_chg = 0.0
                
                tracking_stocks.append({
                    'code': ts_code,
                    'name': row['name'],
                    'last_date': TRADE_DATE,
                    'last_close': latest_close,
                    'last_score': last_score,
                    'open_score': open_score,
                    'structure_type': structure_type,
                    'open_recommendation': open_recommendation,
                    'range_5d_pct': range_pct,
                    'max_pct': max_pct,
                    'bias_rate': bias_rate,
                    'pct_chg': pct_chg,
                    'vol_ratio': cache_vol_ratio,
                    'failure_prob': failure_prob,
                    'trend_score': trend_score,
                    'capital_score': capital_score,
                    'position_score': position_score,
                    'heat_score': heat_score,
                    'fundamental_score': fundamental_score
                })
        
        # 按整合评分排序，取前30只
        tracking_stocks = sorted(tracking_stocks, key=lambda x: -x['open_score'])[:30]
        
        # 生成文本格式
        lines = []
        if tracking_stocks:
            lines.append("=" * 100)
            lines.append("跟踪分析股票池（最高涨幅≤20%、5日均线上、5日乖离率<5%）")
            lines.append("=" * 100)
            lines.append(f"{'代码':<12} {'名称':<10} {'最新价':<8} {'整合评分':<8} {'失败概率':<8} {'当日涨跌':<8} {'量比':<6} {'5日涨幅':<10}")
            lines.append("-" * 100)
            for stock in tracking_stocks:
                lines.append(f"{stock['code']:<12} {stock['name']:<10} {stock['last_close']:<8.2f} {stock['open_score']:<8.1f} {stock.get('failure_prob', 0):<8.1f}% {stock.get('pct_chg', 0):<+8.2f}% {stock.get('vol_ratio', 0):<6.2f} {stock['range_5d_pct']:<+10.2f}%")
            lines.append("=" * 100)
            
            # 输出详细维度评分
            lines.append("")
            lines.append("【详细维度评分】")
            lines.append("-" * 100)
            lines.append(f"{'代码':<12} {'名称':<10} {'趋势':<6} {'资金':<6} {'位置':<6} {'热度':<6} {'基本面':<6} {'结构类型':<10}")
            lines.append("-" * 100)
            for stock in tracking_stocks:
                lines.append(f"{stock['code']:<12} {stock['name']:<10} {stock.get('trend_score', 0):<6.1f} {stock.get('capital_score', 0):<6.1f} {stock.get('position_score', 0):<6.1f} {stock.get('heat_score', 0):<6.1f} {stock.get('fundamental_score', 0):<6.1f} {stock.get('structure_type', ''):<10}")
            lines.append("=" * 100)
        
       
        return tracking_stocks, "\n".join(lines), ""
    except Exception as e:
        print(f"筛选跟踪分析个股失败: {e}")
        return [], "数据加载失败", ""


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
        # 优先使用 akshare 免费接口
        import akshare as ak
        try:
            # akshare 1.18.x 版本使用 stock_zt_pool_em
            zt_df = ak.stock_zt_pool_em(date=TRADE_DATE)
            if zt_df is not None and not zt_df.empty:
                # 提取连板数并转换为整数
                if '连板数' in zt_df.columns:
                    zt_df['连板数'] = pd.to_numeric(zt_df['连板数'], errors='coerce').fillna(1).astype(int)
                    max_lb = zt_df['连板数'].max()
                    print(f"[连板高度] akshare获取成功: 最高连板 {max_lb} 板")
                    return int(max_lb)
                elif '连扳数' in zt_df.columns:  # 兼容不同版本的字段名
                    zt_df['连扳数'] = pd.to_numeric(zt_df['连扳数'], errors='coerce').fillna(1).astype(int)
                    max_lb = zt_df['连扳数'].max()
                    print(f"[连板高度] akshare获取成功: 最高连板 {max_lb} 板")
                    return int(max_lb)
        except Exception as ak_error:
            print(f"[连板高度] akshare获取失败: {ak_error}")
        
        # akshare失败时，尝试 tushare pro 接口
        if pro is not None:
            zt_df = pro.limit_step(trade_date=TRADE_DATE)
            if zt_df is not None and not zt_df.empty:
                if 'nums' in zt_df.columns:
                    max_lb = zt_df['nums'].fillna(1).astype(int).max()
                    return int(max_lb)
            return 1
        
        # 都失败时返回默认值
        return 3
    except Exception as e:
        print(f"[连板高度] 计算失败: {e}")
        return 3






# =========================
# 主题过滤：以60天综合分出现至少3次以上TOP3的主题为范围进行筛选
# =========================
def filter_by_top_themes(result_df, top_n=10):
    """
    根据60天历史数据筛选主题：只保留综合分排名进入TOP3至少3次的主题的成份股。
    并在个股中注入主题状态字段供AI分析。
    """
    if result_df.empty:
        return result_df

    # 1. 从 theme_trend_sentiment.db 读取过去60天数据
    db_path = os.path.join(BASE_DIR, 'cache_backbone_tushare', 'theme_trend_sentiment.db')
    if not os.path.exists(db_path):
        print("[主题过滤] 数据库不存在，跳过过滤")
        return result_df

    try:
        conn = sqlite3.connect(db_path)
        
        # 获取过去60天的所有交易日
        trade_dates_df = pd.read_sql(
            "SELECT DISTINCT trade_date FROM theme_scores ORDER BY trade_date DESC LIMIT 60",
            conn
        )
        trade_dates = trade_dates_df['trade_date'].tolist()
        
        if not trade_dates:
            print("[主题过滤] 无历史数据，跳过过滤")
            conn.close()
            return result_df
        
        print(f"[主题过滤] 分析最近 {len(trade_dates)} 个交易日数据")
        
        # 统计每个主题进入TOP3的次数
        theme_top3_counts = {}
        all_theme_data = {}
        
        for trade_date in trade_dates:
            day_df = pd.read_sql(
                f"SELECT theme, trend_score, sentiment_score, composite_score, theme_state "
                f"FROM theme_scores WHERE trade_date = '{trade_date}'",
                conn
            )
            if day_df.empty:
                continue
            
            # 按综合分排序取TOP5
            day_df = day_df.sort_values('composite_score', ascending=False).head(5)
            
            for _, row in day_df.iterrows():
                theme = row['theme']
                # 修复KeyError：使用get方法或先初始化
                if theme not in theme_top3_counts:
                    theme_top3_counts[theme] = 0
                theme_top3_counts[theme] += 1
                
                # 保存最新数据用于主题状态
                if theme not in all_theme_data:
                    all_theme_data[theme] = {
                        'theme_state': row.get('theme_state', ''),
                        'trend_score': row.get('trend_score', 0) or 0,
                        'sentiment_score': row.get('sentiment_score', 0) or 0,
                        'composite_score': row.get('composite_score', 0) or 0,
                    }
        
        conn.close()
        
        # 筛选进入TOP3至少5次的主题
        valid_themes = {theme for theme, count in theme_top3_counts.items() if count >= 5}
        
        if not valid_themes:
            print("[主题过滤] 无进入TOP3至少5次的主题，跳过过滤")
            return result_df
        
        # 打印TOP3统计
        print("\n[主题过滤] 60天TOP3统计（进入≥5次）:")
        for theme in sorted(valid_themes, key=lambda x: -theme_top3_counts[x]):
            print(f"  {theme}: {theme_top3_counts[theme]}次")
        print(f"  → 有效主题共 {len(valid_themes)} 个")
        
        # 构建主题状态映射
        theme_state_map = {}
        for theme in valid_themes:
            if theme in all_theme_data:
                theme_state_map[theme] = all_theme_data[theme]
            else:
                theme_state_map[theme] = {
                    'theme_state': '',
                    'trend_score': 0,
                    'sentiment_score': 0,
                    'composite_score': 0,
                }

    except Exception as e:
        print(f"[主题过滤] 读取历史数据失败: {e}")
        import traceback
        traceback.print_exc()
        return result_df

    # 2. 加载主题配置（只保留有效主题）
    theme_cfg = {}
    cfg_path = os.path.join(BASE_DIR, 'theme.json')
    if os.path.exists(cfg_path):
        with open(cfg_path, 'r', encoding='utf-8') as f:
            all_themes = json.load(f).get('HOT_THEMES', {})
            for theme_name in valid_themes:
                if theme_name in all_themes:
                    theme_cfg[theme_name] = all_themes[theme_name]

    # 3. 调用 match_theme_stocks 获取主题成份股映射
    try:
        import theme_trend_sentiment_score as theme_ts
        dc_df = theme_ts.get_dc_members()
        stock_basic_df = None
        if pro is not None:
            try:
                stock_basic_df = pro.stock_basic(fields='ts_code,industry,name')
            except Exception as e:
                print(f"[主题过滤] 获取stock_basic失败: {e}")
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
        return _filter_by_top_themes_fallback(result_df, valid_themes, theme_cfg)

    # 4. 遍历股票，匹配主题并注入主题状态
    keep = []
    matched_themes = []
    match_scores = []
    theme_states_list = []
    theme_trends = []
    theme_sentiments = []

    for _, row in result_df.iterrows():
        ts_code = row['代码']
        stock_name = row.get('名称', '')
        found_theme = ''
        for theme_name, stocks in theme_stock_map.items():
            if ts_code in stocks:
                found_theme = theme_name
                break
        if found_theme:
            keep.append(True)
            matched_themes.append(found_theme)
            match_scores.append(100)
            st = theme_state_map.get(found_theme, {})
            theme_states_list.append(st.get("theme_state", ""))
            theme_trends.append(st.get("trend_score", 0))
            theme_sentiments.append(st.get("sentiment_score", 0))
            print(f"[主题过滤] {stock_name}({ts_code}) -> {found_theme}"
                  f"  状态:{st.get('theme_state','')}")
        else:
            keep.append(False)
            matched_themes.append('')
            match_scores.append(0)
            theme_states_list.append('')
            theme_trends.append(0)
            theme_sentiments.append(0)

    # 5. 应用过滤 + 注入字段
    before = len(result_df)
    result_df = result_df[keep].reset_index(drop=True)
    kept_indices = [i for i in range(len(keep)) if keep[i]]
    result_df['所属主题'] = [matched_themes[i] for i in kept_indices]
    result_df['主题匹配度'] = [match_scores[i] for i in kept_indices]
    result_df['所属状态'] = [theme_states_list[i] for i in kept_indices]
    result_df['主题趋势分'] = [theme_trends[i] for i in kept_indices]
    result_df['主题情绪分'] = [theme_sentiments[i] for i in kept_indices]

    print(f"[主题过滤] 过滤后 {before} -> {len(result_df)} 只")
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
    # 主题状态全景（来自主题分析简报）
    # =========================
    print("\n========== 主题状态全景（来自主题分析简报）==========\n")
    # 从 theme_trend_sentiment_score.py 输出的简报中读取主题状态全景内容
    REPORT_DIR_TS = os.path.join(BASE_DIR, "..", "report_daily")
    report_path = os.path.join(REPORT_DIR_TS, f"theme_analysis_{TRADE_DATE}.txt")
    sector_text_his = ""
    cycle_text = ""
    try:
        if os.path.exists(report_path):
            with open(report_path, "r", encoding="utf-8") as f:
                content = f.read()
            sector_text_his = content
        else:
            print(f"  未找到简报文件: {report_path}")
            print("  请先运行 theme_trend_sentiment_score.py 生成分析报告")
            sector_text_his = ""
    except Exception as e:
        print(f"⚠️ 读取主题状态简报失败: {e}")
        sector_text_his = ""
    
    emotion_stage = "强"
    #else:
    #    emotion_stage = "弱"
    

    
    # 市场情绪 → 使用 market_analysis.py 的大盘分析 
    import importlib
    ma = importlib.import_module("market_analysis")
    ma_results, ma_position, ma_reason, ma_style_allocations, ma_overview = ma.analyze_market(TRADE_DATE)
    
    # 从数据库或重新计算获取趋势评分（用于显示）
    theme_csv = os.path.join(BASE_DIR, "cache_backbone_tushare", "theme_trend_sentiment.csv")
    theme_top3_scores = None
    if os.path.exists(theme_csv):
        try:
            df_theme = pd.read_csv(theme_csv, encoding='utf-8-sig')
            if not df_theme.empty and 'trend_score' in df_theme.columns:
                df_theme = df_theme.sort_values('rank').head(3)
                theme_top3_scores = df_theme['trend_score'].tolist()
        except:
            pass
    
    ts, it, tt = ma.calculate_market_trend_score(ma_results, theme_top3_scores)
    ms, pr, tp = ma.get_market_status_and_position(ts)
    
    # 构建 emotion_text 用于 DeepSeek 日报
    emotion_lines = ["【大盘分析】"]
    
    # 市场趋势总评分
    status_icon = "🚀" if "主升浪" in ms else ("📈" if "上升" in ms or "良好" in ms else ("⚠️" if "退潮" in ms or "主跌" in ms else "📊"))
    emotion_lines.append(f"  {status_icon} 市场状态: 【{ms}】")
    emotion_lines.append(f"  总趋势分: {ts:.1f} | 指数趋势: {it:.1f} | 主题趋势: {tt:.1f}")
    emotion_lines.append(f"  建议仓位: {ma_position}%")
    emotion_lines.append("")
    
    if ma_overview:
        ov = ma_overview
        emotion_lines.append(f"【市场概况】")
        emotion_lines.append(f"  上证{ov['sh_index']:.2f}({ov['sh_pct']:+.2f}%) "
                             f"成交{ov['total_amount']:.0f}亿 涨{ov['up_count']}跌{ov['down_count']} "
                             f"涨停{ov['zt_count']}跌停{ov['dt_count']}炸板率{ov['zb_rate']}%")
    emotion_lines.append("")
    emotion_lines.append(f"【各指数分析】")
    for r in ma_results:
        emotion_lines.append(f"  {r['name']}: 趋势{r['trend_score']:.1f}({r['trend_status']}) 情绪{r['sentiment_score']:.1f}({r['sentiment_status']}) 涨跌{r['pct_chg']:+.2f}%")
    emotion_lines.append(f"\n综合建议仓位: {ma_position}%")
    emotion_lines.append(f"理由: {ma_reason}")
    emotion_text = "\n".join(emotion_lines)
    print(emotion_text)

    result = []

    # 批量预取：解决高频API调用问题
    # 在循环之前一次性下载所有股票数据到本地缓存

    market = get_market()
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
            #print(f"[{idx+1}/{total}] {ts_code}")
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
                {'代码': '000001.SZ', '名称': '平安银行', '现价': 10.5, '涨跌幅': 1.5, '成交额': 500000, '总市值（亿元）': 1500, 'total_market_cap': 1500e8, 'market_cap': 1500e8, '所属主题': '银行', '所属状态': '震荡'},
                {'代码': '600000.SH', '名称': '浦发银行', '现价': 8.2, '涨跌幅': -0.8, '成交额': 300000, '总市值（亿元）': 1200, 'total_market_cap': 1200e8, 'market_cap': 1200e8, '所属主题': '银行', '所属状态': '震荡'},
                {'代码': '000002.SZ', '名称': '万科A', '现价': 25.3, '涨跌幅': 2.3, '成交额': 800000, '总市值（亿元）': 800, 'total_market_cap': 800e8, 'market_cap': 800e8, '所属主题': '房地产', '所属状态': '弱势'},
                {'代码': '600519.SH', '名称': '贵州茅台', '现价': 1800.0, '涨跌幅': 0.5, '成交额': 1200000, '总市值（亿元）': 25000, 'total_market_cap': 25000e8, 'market_cap': 25000e8, '所属主题': '白酒', '所属状态': '强趋势'},
                {'代码': '300750.SZ', '名称': '宁德时代', '现价': 120.0, '涨跌幅': 3.2, '成交额': 2000000, '总市值（亿元）': 18000, 'total_market_cap': 18000e8, 'market_cap': 18000e8, '所属主题': '新能源车', '所属状态': '抱团主升'},
            ])
        else:
            print("无结果")
            return

    # =========================
    # 主题过滤：注入所属主题等字段
    # =========================
    result_df = filter_by_top_themes(result_df)

    # =========================
    # 使用统一评分算法一次计算所有股票
    # =========================
    ranked_stocks = []
    for idx, row in result_df.iterrows():
        ts_code = row['代码']
        name = row['名称']
        theme_name = str(row.get('所属主题', ''))
        
        df = get_hist_data(ts_code)
        if df is None or len(df) < 20 or not isinstance(df, pd.DataFrame) or 'close' not in df.columns:
            continue
        
        try:
            today_pct = ((df['close'].iloc[-1] / df['close'].iloc[-2]) - 1) * 100 if len(df) >= 2 else float(row.get('涨跌幅', 0))
            
            # 使用统一评分算法
            integrated_score, recommendation, details, failure_prob = calc_unified_stock_score(df, ts_code, theme_name)
            
            stock_data = {
                '代码': ts_code, '名称': name, '现价': float(row.get('现价', 0)),
                '涨跌幅': today_pct, '所属主题': theme_name,
                '整合评分': integrated_score, '失败概率': failure_prob,
                '推荐理由': recommendation,
                '趋势强度': details.get('趋势强度', 0), '资金健康度': details.get('资金健康度', 0),
                '位置安全性': details.get('位置安全性', 0), '热度持续性': details.get('热度持续性', 0),
                '基本面': details.get('基本面', 0),
                '热榜最佳排名': details.get('热榜最佳排名', 0), '热榜上榜次数': details.get('热榜上榜次数', 0),
                '所属状态': str(row.get('所属状态', '')),
                '主题趋势分': float(row.get('主题趋势分', 0)), '主题情绪分': float(row.get('主题情绪分', 0)),
                '量能爆发': float(row.get('量能爆发', 0)), '突破强度': float(row.get('突破强度', 0)),
            }
            ranked_stocks.append(stock_data)
            
        except Exception as e:
            print(f"[整合评分] {ts_code} {name} 失败: {e}")
            continue
    
    # 按整合评分排序
    ranked_stocks = sorted(ranked_stocks, key=lambda x: -x['整合评分'])

    lines = []
    lines.append("=" * 60)
    lines.append("🔥 整合评分精选标的 (明日重点关注)")
    lines.append("=" * 60)
    
    top_stocks = ranked_stocks[:10]
    for i, s in enumerate(top_stocks, 1):
        lines.append(f"【第{i}名】{s['名称']} ({s['代码']})")
        lines.append(f"  整合评分: {s['整合评分']:.1f} | 失败概率: {s['失败概率']:.1f}%")
        lines.append(f"  今日涨幅: {s['涨跌幅']:.2f}% | 量能爆发: {s['量能爆发']:.2f}")
        lines.append(f"  所属主题: {s['所属主题']} | 状态: {s['所属状态']}")
        lines.append(f"  推荐理由: {s['推荐理由']}")
        lines.append(f"  ├─趋势强度: {s['趋势强度']:.1f} | 资金健康度: {s['资金健康度']:.1f}")
        lines.append(f"  ├─位置安全: {s['位置安全性']:.1f} | 热度持续: {s['热度持续性']:.1f}")
        lines.append(f"  └─基本面: {s['基本面']:.1f}")
        if s.get('热榜最佳排名') and 0 < s['热榜最佳排名'] <= 100:
            lines.append(f"  热榜: Top{s['热榜最佳排名']}({s['热榜上榜次数']}次)")
        lines.append("")
    
    lines.append("完整排名:")
    lines.append("-" * 60)
    for i, s in enumerate(ranked_stocks, 1):
        lines.append(f"{i}. {s['代码']} {s['名称']} | 评分:{s['整合评分']:.1f} | 失败概率:{s['失败概率']:.1f}% | {s['所属主题']}")
    lines.append("=" * 60)
    
    hot_money_open_text = "\n".join(lines)
    print(hot_money_open_text)
    
    icpm_top10_list = []
    for s in ranked_stocks[:30]:
        icpm_top10_list.append({
            'code': s.get('代码', ''),
            'name': s.get('名称', ''),
            'theme': s.get('所属主题', ''),
            'open_score': s.get('整合评分', 0),
        })
    
    # 生成stock_text供后续使用
    stock_text = "\n".join([f"{s['代码']} {s['名称']} | 评分:{s['整合评分']:.1f}" for s in ranked_stocks[:10]])
    all_stock_text = "\n".join([f"{s['代码']} {s['名称']} | 评分:{s['整合评分']:.1f}" for s in ranked_stocks])

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
            
                       
            # 转换回列表格式，并按 V9评分 排序
            tracking_stocks = tracking_df.to_dict('records')
            
            # 按 V9评分 降序排列，取前50只
            tracking_stocks = sorted(tracking_stocks, key=lambda x: x.get('last_score', 0), reverse=True)[:50]
            
            # 重新生成文本（加入主题状态信息）
            lines = []
            if tracking_stocks:
                lines.append("=" * 80)
                lines.append("跟踪分析股票池（主题过滤后，按整合评分排序）")
                lines.append("=" * 80)
                lines.append(f"{'代码':<12} {'名称':<10} {'最新价':<8} {'整合评分':<8} {'当日涨跌':<8} {'量比':<6} {'5日涨幅':<10} {'最高涨幅':<10} {'主题状态':<10}")
                lines.append("-" * 85)
                for stock in tracking_stocks:
                    lines.append(f"{stock['代码']:<12} {stock['名称']:<10} {stock.get('last_close', 0):<8.2f} {stock.get('open_score', 0):<8.1f} {stock.get('pct_chg', 0):<+8.2f}% {stock.get('vol_ratio', 0):<6.2f} {stock.get('range_5d_pct', 0):<+10.2f}% {stock.get('max_pct', 0):<+10.2f}% {stock.get('所属状态', ''):<10}")
                lines.append("=" * 85)
                # 加入每只股票的详细评价信息（供AI分析）
                lines.append("")
                lines.append("跟踪个股详细评价（按整合评分降序）：")
                lines.append("-" * 80)
                for i, stock in enumerate(tracking_stocks, 1):
                    lines.append(f"【第{i}名】{stock.get('名称', '')} ({stock.get('代码', '')})")
                    lines.append(f"  整合评分: {stock.get('open_score', 0):.1f}")
                    theme_str = stock.get('所属主题', '')
                    state_str = stock.get('所属状态', '')
                    lines.append(f"  最新价: {stock.get('last_close', 0):.2f} | 当日涨跌: {stock.get('pct_chg', 0):+.2f}% | 量比: {stock.get('vol_ratio', 0):.2f} | 5日涨幅: {stock.get('range_5d_pct', 0):+.2f}% | 最高涨幅: {stock.get('max_pct', 0):+.2f}%")
                    if theme_str:
                        lines.append(f"  所属主题: {theme_str} | 主题状态: {state_str}")
                    rec = stock.get('open_recommendation', '')
                    if rec:
                        lines.append(f"  推荐理由: {rec}")
                    lines.append("")
                lines.append("=" * 80)
            tracking_stocks_text = "\n".join(lines)
    except Exception as e:
        print(f"获取跟踪分析个股失败: {e}")
        tracking_stocks, tracking_stocks_text, ai_report = [], "", ""
    
    if tracking_stocks_text:
        print("\n========== 跟踪分析个股 ==========\n")
        print(tracking_stocks_text)

    else:
        print(f"\n========== 暂无跟踪分析个股 ==========\n")


    # =========================
    # 读取主题选股结果
    # =========================
    theme_stocks_records, theme_stocks_text = load_theme_pattern_stocks()
    if theme_stocks_text:
        print("\n========== 主题选股结果 ==========\n")
        #print(theme_stocks_text)
    else:
        print("\n========== 未找到主题选股结果 ==========")

    # =========================
    # ICPM 产业资金定价诊断（Top 10 开仓股）
    # =========================
    icpm_text = ""
    if _ICPM_AVAILABLE and icpm_top10_list:
        try:
            import importlib
            import yaml

            config_path = os.path.join(_MF_DIR, 'config.yaml')
            with open(config_path, 'r', encoding='utf-8') as f:
                icpm_config = yaml.safe_load(f)
            icpm_token_env = icpm_config.get('tushare', {}).get('token_env', 'TUSHARE_TOKEN')
            icpm_token = os.environ.get(icpm_token_env)
            if not icpm_token:
                for ep in [Path(__file__).resolve().parent.parent.parent / "config" / ".env",
                           Path(__file__).resolve().parent.parent / "config" / ".env"]:
                    if ep.exists():
                        with open(ep, 'r', encoding='utf-8') as f:
                            for line in f:
                                line = line.strip()
                                if not line or line.startswith('#'):
                                    continue
                                if '=' in line:
                                    k, v = line.split('=', 1)
                                    if k.strip() == icpm_token_env:
                                        icpm_token = v.strip().strip('"\'')
                                        break
                        break

            if icpm_token:
                # 延迟导入（避免循环引用：tushare_quant ↔ industry_pricing_model）
                df_mod = importlib.import_module('data_fetcher')
                DataFetcher = df_mod.DataFetcher
                ipm = importlib.import_module('industry_pricing_model')
                IndustryPricingModel = ipm.IndustryPricingModel
                extract_pricing_data = ipm.extract_pricing_data
                fetcher = DataFetcher(icpm_token, icpm_config)
                codes = [s['code'] for s in icpm_top10_list if s['code']]
                start_year = str(datetime.now().year - 3)
                financial_batch = fetcher.get_stock_financial_batch(codes, start_year=start_year, max_workers=5)

                daily_basic = fetcher.get_daily_basic(TRADE_DATE)
                moneyflow = fetcher.get_moneyflow(TRADE_DATE)
                daily_basic_idx = {r['ts_code']: r for _, r in daily_basic.iterrows()} if daily_basic is not None and not daily_basic.empty else {}
                moneyflow_idx = {r['ts_code']: r for _, r in moneyflow.iterrows()} if moneyflow is not None and not moneyflow.empty else {}

                model = IndustryPricingModel(icpm_config)
                icpm_lines = []

                # 生命周期/决策/资金映射
                _STAGE_CN = {
                    "ACCUMULATION": "资金建仓", "MAINLINE_ACCELERATION": "主升浪",
                    "DISTRIBUTION": "分歧/顶部震荡", "DECLINE": "衰退", "EARLY_STAGE": "产业萌芽",
                }
                _DECISION_CN = {"BUY": "买入", "HOLD": "观望", "REDUCE": "减仓", "EXIT": "清仓"}
                _CAPITAL_CN = {
                    "STRONG_INFLOW": "强流入", "WEAK_INFLOW": "弱流入",
                    "NEUTRAL": "中性", "OUTFLOW": "流出",
                }

                # 收集需要删除的股票代码（REDUCE 或 EXIT）
                icpm_exclude_codes = set()

                icpm_lines.append("=" * 72)
                icpm_lines.append("产业资金定价诊断（ICPM）- 整合评分Top30")
                icpm_lines.append("=" * 72)
                icpm_lines.append(f"{'股票':<16} {'生命周期':<12} {'主线强度':<8} {'资金状态':<8} {'决策':<6} {'整合评分':<8}")
                icpm_lines.append("-" * 72)

                for s in icpm_top10_list:
                    code = s['code']
                    name = s['name']
                    theme = s['theme']
                    if not code:
                        continue
                    kline = get_hist_data(code) if code else None
                    data = extract_pricing_data(
                        code, name, theme, '',
                        financial_batch,
                        daily_basic_idx.get(code),
                        moneyflow_idx.get(code),
                        kline_df=kline,
                    )
                    if data is None:
                        print(f"[ICPM] {name}({code}) → extract_pricing_data 返回 None（财务数据缺失）")
                        continue
                    result = model.diagnose(data)
                    stage_cn = _STAGE_CN.get(result.lifecycle_stage, result.lifecycle_stage)
                    decision_cn = _DECISION_CN.get(result.final_decision, result.final_decision)
                    capital_cn = _CAPITAL_CN.get(result.capital_flow_state, result.capital_flow_state)
                    # 诊断详情打印（保留英文缩写给日志）
                    print(f"[ICPM] {name}({code}) "
                          f"theme={theme} "
                          f"revenue_yoy={data.revenue_yoy:.2%} "
                          f"profit_yoy={data.profit_yoy:.2%} "
                          f"order_cl_yoy={data.contract_liability_yoy:.2%} "
                          f"order_score={result.order_explosion_score:.0f} "
                          f"exp_score={result.expectation_score:.0f} "
                          f"mainline={result.is_mainline} "
                          f"strength={result.mainline_strength:.2f} "
                          f"capital={result.capital_flow_state} "
                          f"→ stage={result.lifecycle_stage} "
                          f"decision={result.final_decision}")
                    icpm_lines.append(
                        f"{name+'('+code+')':<16} {stage_cn:<12} "
                        f"{result.mainline_strength:<8.2f} {capital_cn:<8} "
                        f"{decision_cn:<6} {s['open_score']:<8.1f}"
                    )

                    # 收集需要删除的股票（REDUCE 或 EXIT）
                    if result.final_decision in ("REDUCE", "EXIT"):
                        icpm_exclude_codes.add(code)

                icpm_lines.append("=" * 72)
                icpm_text = "\n".join(icpm_lines)
                print(icpm_text)

                # 从 ranked_stocks 中删除 ICPM 提示减仓/清仓的股票
                if icpm_exclude_codes:
                    original_count = len(ranked_stocks)
                    ranked_stocks = [s for s in ranked_stocks if s.get('代码', '') not in icpm_exclude_codes]
                    filtered_count = original_count - len(ranked_stocks)
                    print(f"[ICPM] 过滤掉 {filtered_count} 只股票（减仓/清仓）: {icpm_exclude_codes}")
                    # 重新生成 hot_money_text（包含过滤后的排名）
                    lines = []
                    lines.append("=" * 60)
                    lines.append("🔥 整合评分精选标的 (ICPM过滤后)")
                    lines.append("=" * 60)
                    for i, s in enumerate(ranked_stocks[:10], 1):
                        lines.append(f"【第{i}名】{s['名称']} ({s['代码']})")
                        lines.append(f"  整合评分: {s['整合评分']:.1f} | 失败概率: {s['失败概率']:.1f}%")
                        lines.append(f"  所属主题: {s['所属主题']} | 状态: {s.get('所属状态', '')}")
                        lines.append(f"  推荐理由: {s['推荐理由']}")
                        lines.append("")
                    lines.append("=" * 60)
                    hot_money_text = "\n".join(lines)
                    hot_money_open_text = hot_money_text  # 更新全局变量
                    # 更新 stock_text
                    if not ranked_stocks:
                        stock_text = "（ICPM过滤后无股票）"
                    else:
                        stock_text = "\n".join([f"{s['代码']} {s['名称']} | 评分:{s['整合评分']:.1f}" for s in ranked_stocks[:10]])
        except Exception as e:
            print(f"[ICPM] 诊断失败: {e}")
            import traceback
            traceback.print_exc()
            icpm_text = ""

    #return
    prompt = f"""

当前市场情绪：

{emotion_text}

今日主题分析情况：

{sector_text_his}

主题个股池选股结果（来自 theme_pattern_stock_picker.py）：
（这是根据主题趋势和情绪筛选出的优质个股，包含中期趋势主题和短线主线的龙头和中军）

{theme_stocks_text}

近20日跟踪分析股票池（从历史自选股中筛选涨幅不大、未大涨过的个股，经主题过滤后按综合评分排序）：
（这些是近期持续关注、尚未启动的股票，值得跟踪分析）

{tracking_stocks_text}

整合评分精选标的（综合趋势强度、资金健康度、位置安全性、热度持续性、基本面五个维度评分）：
（这是程序根据整合评分算法筛选的明日重点标的，目标是找到次日介入上涨概率高、失败概率低的股票）

{hot_money_open_text}


产业资金定价诊断（ICPM）- 生命周期/主线强度/资金状态/决策建议：

{icpm_text}


请对以上数据进行分析，具体要求：

1. 仅过滤有基本面重大风险的个股：
   - 近三个月内有定增预案
   - 有大额减持公告
   - 未来半年有大额解禁压力
   - 有重大诉讼风险
   - 有重大财务风险（如连续亏损、审计异常等）
   - 有其他重大利空消息

2. 对于无重大风险的前30名个股，保持原有的综合评分排序，不要重新筛选和排序

3. 对完整量化候选股票池中的每只股票进行以下分析：
   - 个股基本情况和所属主题
   - 所属主题的**状态**（抱团主升/强趋势/震荡/弱势等），不同状态对应不同策略：
     * **抱团主升**：龙头稳定、趋势陡峭、情绪高涨，资金集中，适合持股
     * **强趋势**：趋势分高且持续上升，情绪活跃，适合顺势操作
     * **震荡**：趋势不明显，方向待确认，观望为主
     * **弱势**：趋势下行，回避为主
   - 当前位置和走势分析
   - 未来上涨空间预估（给出合理的目标价位）
   - 买点建议（具体价位或技术形态）
   - 止损点建议
   - 简要的风险提示


输出内容：
标题：每日复盘({TRADE_DATE})
内容(分成以下部分)：
1、大盘情绪：简明扼要，重点是仓位建议及理由，操作要点
2、今日主题分析情况:
   - 根据提示中的主题分析情况，显示整体市场主题和风格热点，提示操作要点
   - 对推荐的主题，从主题选股结果中输出该主题对应的补涨中军（加粗股票名称），不要输出中军个股
   - 根据趋势分和情绪分及轮动频率、稳定性，预测明日可能持续或回升的热点主题，简明扼要
3、自选量化股票池分析（仅对完整量化候选股票池中股票，不要加入其它的）：
   **【重要】按整合评分从高到低排序分析前10名个股：**
   - **【必须】用以下格式显示：**
     【第1名 - 明日首选】股票名 (代码)
     【第2名】股票名 (代码)
     【第3名】股票名 (代码)
     依此往后
   - 对每只股票进行详细分析，包括：
     - 整合评分和失败概率
     - 所属主题和该主题的状态，从网络搜索内容分析个股近期表现的主题驱动因素（尤其是多主题共振）
     - 技术面分析
     - 未来上涨空间预估（AI估值分析）
     - 给出产业资金定价诊断（ICPM）结果- 生命周期/主线强度/资金状态/决策建议：
     - 买点建议
     - 止损点建议
     - 风险提示（如果有）
     - 如遇重大风险，请在分析中标注"【警告】有重大风险"，但仍保留在列表中并说明理由
   
4、跟踪分析个股：从近20日跟踪分析股票池中，精选5个符合技术形态的个股进行深度分析，重点关注：
    - 显示整合评分和失败概率
    - 分析所属主题和该主题的状态，从网络搜索内容分析个股近期表现的主题驱动因素（尤其是多主题共振）
    - 技术面分析:A洗盘到30日均线或60日均线，且该均线是向上的趋势，B小阳线温和上涨
    - 未来上涨空间预估（AI估值分析）
    - 风险提示（如果有）
    - 临近60日新高或刚刚突破创新高，在前几天震荡调整后放量上涨但没涨停
    

格式要求：
- Top30个股分析中，每只股票单独分段，用【股票名+代码】作为小标题，加黑加粗显示
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


