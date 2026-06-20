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


def get_hot_list_best_rank_bonus(ts_code, days=60):
    """获取股票在热榜中的最佳排名并返回加分
    
    加分规则：
    1. 排名加分（基于最佳排名）：
       - Top10: +12分
       - Top20: +10分
       - Top30: +8分
       - Top50: +6分
       - Top100: +4分
       - 未进Top100: +0分
    
    2. 出现次数加分（基于60天内上榜次数）：
       - 1次: +0分
       - 2-3次: +2分
       - 4-5次: +4分
       - 6-10次: +6分
       - 11次以上: +8分
    
    总加分 = 排名加分 + 出现次数加分
    
    Args:
        ts_code: 股票代码
        days: 统计天数，默认60天
    
    Returns:
        bonus: 总加分
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
    
    # 1. 排名加分
    if best_rank <= 10:
        rank_bonus = 12
    elif best_rank <= 20:
        rank_bonus = 10
    elif best_rank <= 30:
        rank_bonus = 8
    elif best_rank <= 50:
        rank_bonus = 6
    elif best_rank <= 100:
        rank_bonus = 4
    else:
        rank_bonus = 0
    
    # 2. 出现次数加分
    if appear_count <= 1:
        count_bonus = 0
    elif appear_count <= 3:
        count_bonus = 2
    elif appear_count <= 5:
        count_bonus = 4
    elif appear_count <= 10:
        count_bonus = 6
    else:
        count_bonus = 8
    
    # 总加分
    bonus = rank_bonus + count_bonus
    
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
    pro = ts.pro_api(TUSHARE_TOKEN)
except Exception as e:
    print(f"Token 设置失败: {e}")
    print("请正确配置 TUSHARE_TOKEN 后重新运行。")
    import sys
    sys.exit(1)

if pro is None:
    print("Tushare API 未初始化，请配置 Token 后重新运行。")
    import sys
    sys.exit(1)


if not os.path.exists(REPORT_DIR):
    os.makedirs(REPORT_DIR)

# =========================
# 全局换手率缓存（当日批量加载）
# =========================
TURNOVER_CACHE = {}  # {ts_code: turnover_rate}

def load_turnover_cache():
    """批量加载当日换手率到缓存（从daily_basic表）"""
    global TURNOVER_CACHE
    if pro is None:
        return
    cache_file = os.path.join(CACHE_DIR, f"turnover_rate_{TRADE_DATE}.csv")
    if os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file)
            TURNOVER_CACHE = dict(zip(df['ts_code'], df['turnover_rate']))
            print(f"[缓存] 换手率已加载: {len(TURNOVER_CACHE)} 只")
            return
        except Exception:
            pass
    # 没有缓存则从API批量拉取
    try:
        df = pro.daily_basic(
            trade_date=TRADE_DATE,
            fields='ts_code,turnover_rate'
        )
        if df is not None and not df.empty:
            TURNOVER_CACHE = dict(zip(df['ts_code'], df['turnover_rate']))
            df.to_csv(cache_file, index=False)
            print(f"[缓存] 换手率已保存: {cache_file}")
    except Exception as e:
        print(f"[缓存] 换手率加载失败: {e}")

def get_cached_turnover(ts_code):
    """从缓存获取换手率（单位：%）"""
    return TURNOVER_CACHE.get(ts_code, 0.0)

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

# 启动时加载换手率缓存
load_turnover_cache()

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

    vol_ratio = V.iloc[-1] / (V.iloc[:-1].tail(20).mean() + 1e-6) if len(V) > 20 else V.iloc[-1] / (V.mean() + 1e-6)

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

    vol_ratio = V.iloc[-1] / (V.iloc[:-1].tail(20).mean() + 1e-6) if len(V) > 20 else V.iloc[-1] / (V.mean() + 1e-6)

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
    HHV20 = H.iloc[:-1].tail(20).max() if len(H) > 1 else H.tail(20).max()
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
    HHV60 = H.iloc[:-1].rolling(60).max().iloc[-1] if len(H) > 1 else H.rolling(60).max().iloc[-1]
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
    HHV20 = H.iloc[:-1].tail(20).max() if len(H) > 1 else H.tail(20).max()
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
    high_series = df['high']
    hhv60_s = float(high_series.iloc[:-1].tail(60).max()) if len(high_series) > 1 else float(high_series.tail(60).max())
    llv60_s = float(close_series.tail(60).min())
    hhv20_s = float(high_series.iloc[:-1].tail(20).max()) if len(high_series) > 1 else float(high_series.tail(20).max())
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

def calc_unified_stock_score(df, ts_code='', theme='', theme_trend_score=0, theme_sentiment_score=0):
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
        high_series = df['high']
        MA5 = float(close_series.rolling(5).mean().iloc[-1])
        MA10 = float(close_series.rolling(10).mean().iloc[-1])
        MA20 = float(close_series.rolling(20).mean().iloc[-1])
        MA60 = float(close_series.rolling(60).mean().iloc[-1])
        # 修复: HHV用最高价(非收盘价)，且排除当天数据避免"今天创新高则HHV=今天高点"的循环
        HHV20 = float(high_series.iloc[:-1].tail(20).max()) if len(high_series) > 1 else float(close_series.tail(20).max())
        HHV60 = float(high_series.iloc[:-1].tail(60).max()) if len(high_series) > 1 else float(close_series.tail(60).max())
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
        
        # 均线斜率（趋势反转导向，不依赖均线排列）
        # MA20均线斜率（权重加大）
        if len(C) >= 25:
            ma20_slope = (MA20 - float(close_series.rolling(20).mean().iloc[-6])) / MA20
            if ma20_slope > 0.05:
                trend_score += 35  # 极强上升
            elif ma20_slope > 0.03:
                trend_score += 28  # 强上升
            elif ma20_slope > 0.015:
                trend_score += 20  # 温和上升
            elif ma20_slope > 0:
                trend_score += 10  # 缓慢上升
            elif ma20_slope > -0.015:
                trend_score -= 5   # 轻微下降
            else:
                trend_score -= 15  # 明显下降
        
        # MA10均线斜率（新增，权重加大）
        if len(C) >= 15:
            ma10_current = float(close_series.rolling(10).mean().iloc[-1])
            ma10_prev = float(close_series.rolling(10).mean().iloc[-6])
            ma10_slope = (ma10_current - ma10_prev) / ma10_current if ma10_current > 0 else 0
            if ma10_slope > 0.06:
                trend_score += 30  # 极强上升
            elif ma10_slope > 0.04:
                trend_score += 24  # 强上升
            elif ma10_slope > 0.02:
                trend_score += 18  # 温和上升
            elif ma10_slope > 0:
                trend_score += 8   # 缓慢上升
            elif ma10_slope > -0.02:
                trend_score -= 3   # 轻微下降
            else:
                trend_score -= 12  # 明显下降
        
        # 近期涨幅（5日，趋势反转导向：回调可能是二波机会）
        if len(C) >= 6:
            ret_5 = (C[-1] / C[-6] - 1) * 100
            if 3 <= ret_5 <= 12:
                trend_score += 15  # 最佳区间：稳健上涨
            elif ret_5 > 12:
                trend_score += 5   # 涨幅过大，谨慎
            elif -8 <= ret_5 < 3:
                trend_score += 10  # 小幅回调或横盘，可能是二波蓄力机会
            elif -15 <= ret_5 < -8:
                trend_score += 15  # 深度回调后，反转预期更强
            elif ret_5 < -15:
                trend_score += 5   # 跌幅过大，风险较高
            else:
                trend_score -= 5
        
        # 计算120日高点（用最高价，排除当天）
        HHV120 = float(high_series.iloc[:-1].tail(120).max()) if len(C) >= 120 else HHV20
        
        # 突破前高（不加分，只记录状态用于其他计算）
        breakout_strength = 0.5
        if current_price >= HHV20:
            breakout_strength = 1.0
        elif current_price >= HHV20 * 0.97:
            breakout_strength = 0.85
        elif current_price >= HHV20 * 0.90:
            breakout_strength = 0.6
        
        # 120日新高状态（用于强度减分）
        is_new_high_120 = current_price >= HHV120
        dist_to_120high = (HHV120 - current_price) / HHV120 if HHV120 > 0 else 0
        
        # =========================
        # 新增：假突破识别
        # =========================
        # 判断1：检测'二波'-60日内有过高点后快速回落（失败突破）
        failed_breakout_penalty = 0

        if len(C) >= 30:
            lookback = min(60, len(C) - 5)
            H_series = df['high'].values
            for offset_days_ago in range(lookback, 8, -1):
                idx = len(C) - offset_days_ago
                if idx < 5:
                    continue
                day_high = float(H_series[idx])
                prev_start = max(0, idx - 20)
                hhv_before = float(max(C[prev_start:idx]))
                if hhv_before <= 0:
                    continue
                if day_high >= hhv_before * 0.98:
                    after_3_idx = min(len(C) - 1, idx + 3)
                    if after_3_idx < len(C):
                        after_3_price = float(C[after_3_idx])
                        drop_from_high = (day_high - after_3_price) / day_high * 100
                        if drop_from_high > 8:
                            failed_breakout_penalty = 20
                            break

        # 判断2：长上影K线（冲高回落信号）
        # 注意：科创板/创业板波动大，且大涨日上影5%是正常的
        upper_shadow_penalty = 0
        if 'high' in df.columns and len(df) >= 2:
            today_high = float(df['high'].iloc[-1])
            today_close = C[-1]
            upper_shadow_pct = (today_high - today_close) / today_close * 100

            # 当日涨幅越大，允许的上影越长（科创板涨10%时上影5%很正常）
            if today_pct > 10:
                # 大涨日：上影>8%才算异常
                if upper_shadow_pct > 10:
                    upper_shadow_penalty = 20
                elif upper_shadow_pct > 8:
                    upper_shadow_penalty = 10
            elif today_pct > 5:
                # 中涨日：上影>5%开始扣分
                if upper_shadow_pct > 7:
                    upper_shadow_penalty = 15
                elif upper_shadow_pct > 5:
                    upper_shadow_penalty = 8
            else:
                # 小涨/下跌日：上影>3%开始扣分
                if upper_shadow_pct > 6:
                    upper_shadow_penalty = 20
                elif upper_shadow_pct > 4:
                    upper_shadow_penalty = 15
                elif upper_shadow_pct > 3:
                    upper_shadow_penalty = 8

        # 判断3：删除"接近高点未突破"惩罚
        # 理由：接近高点是强势表现，只有"突破后快速回落"才是假突破
        # 该判断已被判断1（失败突破检测）覆盖，此处不再重复惩罚
        near_high_no_break_penalty = 0

        # 假突破综合惩罚
        total_fake_breakout_penalty = failed_breakout_penalty + upper_shadow_penalty + near_high_no_break_penalty
        trend_score -= total_fake_breakout_penalty

        trend_score = min(100, max(0, trend_score))  # 限制上限

        
        # =========================
        # 2. 资金健康度评分（25%）
        # =========================
        capital_score = 50
        
        # 量能分析（使用当日量比：当日成交量/5日均量，均线排除当天）
        vol_ratio = 1.0
        vol_vs_high_ratio = 1.0  # 当日量能 vs 60日最高量能
        if 'vol' in df.columns and len(df) >= 10:
            vol_today = float(df['vol'].iloc[-1])
            vol_hist = df['vol'].iloc[:-1]  # 排除当天的历史成交量
            vol_ma5 = float(vol_hist.tail(5).mean()) if len(vol_hist) >= 5 else vol_today
            vol_ma20 = float(vol_hist.tail(20).mean()) if len(vol_hist) >= 20 else vol_today
            # 当日量比 = 当日成交量 / 5日均量（均线不含当天，真实放量倍数）
            vol_ratio = vol_today / vol_ma5 if vol_ma5 > 0 else 1.0
            # 5日/20日量比用于平滑判断
            vol_ma_ratio = vol_ma5 / vol_ma20 if vol_ma20 > 0 else 1.0
            
            # 关键改进：当日量 vs 60日最高量（识别量能萎缩，max不含当天）
            vol_max_60d = float(vol_hist.tail(60).max()) if len(vol_hist) >= 5 else vol_today
            vol_vs_high_ratio = vol_today / vol_max_60d if vol_max_60d > 0 else 1.0
            
            # 当日放量越大，加分越多
            if vol_ratio > 3.0:
                capital_score += 35  # 巨量爆发
            elif vol_ratio > 2.0:
                capital_score += 25  # 明显放量
            elif vol_ratio > 1.5:
                capital_score += 15  # 温和放量
            elif vol_ratio > 1.0:
                capital_score += 5   # 轻微放量
            
            # 5日/20日量比辅助判断持续性
            if vol_ma_ratio > 2.0:
                capital_score += 10  # 持续放量
            
            # 新增：量能萎缩惩罚
            # 当日量能 vs 60日最高量能（解决立新能源问题：量比~1但远低于历史高点）
            if vol_vs_high_ratio < 0.2:
                capital_score -= 20  # 量能极度萎缩（不足高峰期的20%）
            elif vol_vs_high_ratio < 0.3:
                capital_score -= 15  # 量能严重萎缩（<30%）
            elif vol_vs_high_ratio < 0.4:
                capital_score -= 10  # 量能明显萎缩（<40%）
            elif vol_vs_high_ratio < 0.5:
                capital_score -= 5   # 量能有所萎缩（<50%）
        
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
        
        # 接近历史高点但未突破：不扣分（强势蓄能形态）
        # 理由：接近高点说明价格强势，不应该惩罚；
        #      是否为假突破由"失败突破检测"和"长上影K线"判断，
        #      不通过距离高点的远近直接扣分
        # 该部分贡献分数：0
        pass
        
        position_score = min(100, max(0, position_score))
        
        # =========================
        # 4. 热度持续性评分（20%）
        # =========================
        hot_score = 50
        
        # 主题生命力（降低加分幅度）
        tli_score, _ = calc_tli_score(theme, top_n=10, days=60)
        hot_score += (tli_score - 50) * 0.3  # 从0.5降到0.3
        
        # 热榜排名加分
        hot_rank_bonus, best_rank, hot_appear_count = get_hot_list_best_rank_bonus(ts_code, days=60)
        hot_score += hot_rank_bonus * 0.5  # 降低热榜加分权重
        
        # 检查主题是否连续3天情绪排名前5（避免追涨高潮主题）
        if theme:
            try:
                db_path = os.path.join(BASE_DIR, 'cache_backbone_tushare', 'theme_trend_sentiment.db')
                if os.path.exists(db_path):
                    conn = sqlite3.connect(db_path)
                    # 获取最近5个交易日的排名数据
                    dates_df = pd.read_sql(
                        f"SELECT DISTINCT trade_date FROM theme_scores WHERE trade_date <= '{TRADE_DATE}' ORDER BY trade_date DESC LIMIT 5",
                        conn
                    )
                    if not dates_df.empty:
                        recent_dates = dates_df['trade_date'].tolist()
                        consecutive_top5 = 0
                        # 检查是否连续在TOP5情绪
                        for td in recent_dates:
                            rank_df = pd.read_sql(
                                f"SELECT theme, sentiment_score FROM theme_scores WHERE trade_date = '{td}' ORDER BY sentiment_score DESC",
                                conn
                            )
                            if not rank_df.empty:
                                top5_themes = rank_df.head(5)['theme'].tolist()
                                if theme in top5_themes:
                                    consecutive_top5 += 1
                                else:
                                    break  # 不连续则停止
                        # 连续3天TOP5情绪 = 追涨惩罚
                        if consecutive_top5 >= 3:
                            hot_score -= 15  # 连续高潮，追涨风险高
                            recommendation = recommendation.replace("热榜Top1", "⚠️连续高潮")
                    conn.close()
            except Exception as e:
                pass  # 查询失败不阻塞
        
        # 过滤条件：非双创板股票且未上过热榜的，直接返回0分
        # 双创板：创业板(300开头)、科创板(688开头)
        is_innovation_board = ts_code.startswith('300') or ts_code.startswith('688')
        if not is_innovation_board and hot_appear_count<=0:
            return 0, "非双创板且未上热榜", {}, 90
        
        # 根据主题趋势分和情绪分调整热度（避免高潮后追高）
        # 情绪分过高（>80）= 高潮期，可能回落，降低热度
        # 趋势分下降（<30）= 主题走弱，降低热度
        if theme_sentiment_score > 80:
            # 情绪高潮，可能即将回落
            hot_score -= 15
        elif theme_sentiment_score > 70:
            # 情绪偏高，谨慎
            hot_score -= 8
        elif theme_sentiment_score < 30:
            # 情绪低迷，热度不足
            hot_score -= 10
        
        if theme_trend_score < 30:
            # 趋势走弱
            hot_score -= 12
        elif theme_trend_score < 40:
            # 趋势偏弱
            hot_score -= 5
        elif theme_trend_score > 70:
            # 趋势强劲，热度有支撑
            hot_score += 5
        
        # 主题TOP3次数
        if hasattr(getattr(globals().get('result_df', None), 'iloc', None), '__call__'):
            # 从主题评分中获取
            pass
        
        hot_score = min(100, max(0, hot_score))
        
        # =========================
        # 5. 基本面评分（使用V2增强模块）
        # =========================
        # 调用新的基本面评分模块
        try:
            fund_result = calc_fundamental_score_v2(
                ts_code=ts_code,
                theme_name=theme,
                theme_trend_score=theme_trend_score,
                theme_sentiment_score=theme_sentiment_score,
                hot_rank=best_rank if best_rank <= 100 else 9999,
                hot_count=hot_appear_count
            )
            fundamental_score = fund_result['base_score']  # 使用base_score作为基本面分
            synergy_coeff = fund_result['synergy_coeff']   # 共振系数用于后续调整
            fund_logic = fund_result['logic']
        except Exception as e:
            print(f"[统一评分] 基本面V2评分失败: {e}")
            fundamental_score = 50
            synergy_coeff = 1.0
            fund_logic = []
        
        fundamental_score = min(100, max(0, fundamental_score))
        
        # =========================
        # 6. 追高惩罚（优化版：考虑缩量调整后风险释放）
        # =========================
        penalty = 0
        if len(C) >= 6:
            ret_5 = (C[-1] / C[-6] - 1) * 100
            if ret_5 > 8:
                penalty += min((ret_5 - 8) * 3, 25)
            if ret_5 > 15:
                penalty += 10  # 额外惩罚
        
        # 新增：缩量调整折扣系数（基于 MA20 乖离率 + 趋势过热保护）
        # 区分两种情形：
        #   1. 严重透支（MA20乖离>20% 或 近10日涨幅>25%）：过热 → 保持高惩罚
        #   2. 温和整理（MA20乖离<15% + 量能萎缩）：风险释放 → 惩罚打折
        if penalty > 0 and len(C) >= 10 and len(df) >= 10:
            position_discount = 1.0
            ma20_discount = 1.0
            volume_discount = 1.0
            consolidation_factor = 1.0
            reasons = []

            # 判断1：MA20 乖离率（核心指标）
            if MA20 > 0:
                bias_to_ma20 = (C[-1] - MA20) / MA20 * 100
                if bias_to_ma20 > 20:
                    ma20_discount = 1.0  # 严重乖离 → 不打折
                    reasons.append(f"严重乖离{bias_to_ma20:.0f}%")
                elif bias_to_ma20 > 15:
                    ma20_discount = 0.9  # 偏高
                    reasons.append(f"乖离偏高{bias_to_ma20:.0f}%")
                elif bias_to_ma20 > 10:
                    ma20_discount = 0.7  # 中等
                    reasons.append(f"乖离中等{bias_to_ma20:.0f}%")
                else:
                    ma20_discount = 0.5  # 接近趋势线
                    reasons.append(f"乖离健康{bias_to_ma20:.0f}%")

            # 判断2：趋势过热保护（新增）
            # 如果近10日涨幅过高，即使MA20乖离合理也不能大折扣
            ret_10 = (C[-1] / C[-11] - 1) * 100
            ret_5_local = (C[-1] / C[-6] - 1) * 100
            trend_overheat_limit = 0.0  # 默认=不限制（取更严格的折扣）
            if ret_10 > 30:
                trend_overheat_limit = 1.0  # 近10日涨超30% → 不打折
                reasons.append(f"10日过热{ret_10:.0f}%")
            elif ret_10 > 25:
                trend_overheat_limit = 0.95  # 近10日25-30% → 几乎不打折
                reasons.append(f"10日偏热{ret_10:.0f}%")
            elif ret_5_local > 20:
                trend_overheat_limit = 0.9  # 近5日>20% → 轻微限制
                reasons.append(f"5日过热{ret_5_local:.0f}%")

            # 判断3：位置修复（距20日高点回撤）
            if dist_to_high > 0.03:
                if dist_to_high > 0.15:
                    position_discount = 0.5
                elif dist_to_high > 0.08:
                    position_discount = 0.7
                else:
                    position_discount = 0.8
                reasons.append(f"高点回撤{dist_to_high*100:.0f}%")

            # 判断4：量能萎缩（不是放量冲顶）
            if vol_ratio < 1.0:
                volume_discount = 0.7
                reasons.append(f"缩量(量比{vol_ratio:.1f})")
            elif vol_ratio < 1.3:
                volume_discount = 0.85
                reasons.append(f"温和量能(量比{vol_ratio:.1f})")

            # 判断5：近3日横盘整理（振幅<10%）
            if len(C) >= 8:
                recent_3_high = max(C[-1], C[-2], C[-3])
                recent_3_low = min(C[-1], C[-2], C[-3])
                consolidation_range = (recent_3_high - recent_3_low) / recent_3_low * 100
                if consolidation_range < 6:
                    consolidation_factor = 0.7
                    reasons.append(f"横盘整理(振幅{consolidation_range:.0f}%)")
                elif consolidation_range < 10:
                    consolidation_factor = 0.85

            # 综合折扣 = 先取各维度最严格的，再被过热保护限制
            raw_discount = min(position_discount, ma20_discount, volume_discount, consolidation_factor)
            discount = max(raw_discount, trend_overheat_limit)  # 过热保护不允许折扣过低

            if discount < 1.0:
                penalty = round(penalty * discount, 1)
        
        # =========================
        # 7. 龙头/核心加分
        # =========================
        leader_bonus = 0
        if breakout_strength >= 0.95 and dist_to_high <= 0.05:
            leader_bonus = 15  # 突破前高的龙头
        elif breakout_strength >= 0.80:
            leader_bonus = 8   # 接近前高的核心
        
        # =========================
        # 8. 历史辨识度加分（YRI-H） + 二波机会
        # =========================
        second_wave_bonus = 0
        recognition_bonus = 0
        yri_h_score = 0
        yri_h_tags = []
        
        # 优先使用 YRI-H 历史辨识度评分（替换旧的 calc_yri_score）
        if ts_code:
            try:
                yri_result = calc_yri_history(ts_code, debug=False)
                if isinstance(yri_result, dict) and "错误" not in yri_result:
                    yri_h_score = float(yri_result.get("YRI历史总分", 0))
                    yri_h_tags = yri_result.get("核心历史标签", [])
                    # YRI-H 总分100分，最高贡献 +10分（从0-5分提升至0-10分）
                    recognition_bonus = (yri_h_score / 100) * 10
                    if recognition_bonus > 0:
                        recommendation = f"YRI{yri_h_score:.0f} | " + recommendation
            except Exception:
                pass
        
        # 降级方案：尝试读取二波扫描结果
        if recognition_bonus == 0:
            second_wave_file = os.path.join(BASE_DIR, 'report_daily', 'mainboard_second_wave.json')
            if os.path.exists(second_wave_file):
                try:
                    with open(second_wave_file, 'r', encoding='utf-8') as f:
                        second_wave_data = json.load(f)
                    for stock in second_wave_data.get('data', []):
                        if stock.get('ts_code') == ts_code:
                            recognition_score = stock.get('recognition_score', 0)
                            second_wave_score = stock.get('second_wave_score', 0)
                            recognition_bonus = (recognition_score / 100) * 5
                            second_wave_bonus = (second_wave_score / 100) * 8
                            if recognition_bonus > 0:
                                recommendation = f"辨识度{recognition_score:.0f} | " + recommendation
                            if second_wave_bonus > 0:
                                recommendation = f"二波{second_wave_score:.0f} | " + recommendation
                            break
                except Exception:
                    pass
        
        # =========================
        # 9. 综合得分 - 趋势强度主导
        # =========================
        # 基础分 = 其他维度加权（降低热度权重）
        base_score = (
            capital_score * 0.30 +
            position_score * 0.25 +
            hot_score * 0.20 +
            fundamental_score * 0.25
        )
        
        # 趋势强度作为乘数因子（趋势越强，总分越高）
        # 缩窄倍数范围，避免顶部聚类
        trend_multiplier = 0.7 + (trend_score / 100) * 0.6  # 0.7 ~ 1.3
        
        # 共振系数改为加法项而非乘法，避免双乘数叠加导致的顶部溢出
        synergy_bonus = (synergy_coeff - 0.8) * 25  # 系数0.5→-7.5分, 1.0→+5分, 1.5→+17.5分
        
        # 综合分 = 基础分 × 趋势乘数 + 共振加分 - 惩罚 + 龙头加分 + 二波加分
        final_score = base_score * trend_multiplier + synergy_bonus - penalty + leader_bonus + second_wave_bonus + recognition_bonus
        
        # 趋势强度额外加成：趋势分>70的股票获得额外加分
        if trend_score >= 80:
            final_score += 8  # 强趋势加成
        elif trend_score >= 70:
            final_score += 4   # 中等趋势加成
        elif trend_score < 40:
            final_score -= 8   # 弱趋势惩罚
        
        final_score = min(100, max(5, final_score))
        
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
            # 关键修正：缩量整理(vol_ratio<1.2)≠缺乏热度，而是健康洗盘
            if vol_ratio >= 1.2:
                failure_prob += 10  # 放量下跌或长期无人关注 = 缺乏热度风险
        
        failure_prob = min(90, max(10, failure_prob))
        
        # 失败概率修正：失败概率越低，加分越多（反向激励）
        # 以30%为基准，每低1%加0.5分，每高1%扣0.5分
        failure_bonus = (30 - failure_prob) * 0.5
        final_score += failure_bonus
        
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
            '共振系数': round(synergy_coeff, 2),
            '追高惩罚': round(penalty, 1),
            '龙头加分': leader_bonus,
            '二波加分': round(second_wave_bonus, 1),
            '辨识度加分': round(recognition_bonus, 1),
            'YRI历史总分': round(yri_h_score, 1),
            'YRI标签': ", ".join(yri_h_tags) if yri_h_tags else "",
            '量能爆发': round(vol_ratio, 2),
            '热榜最佳排名': best_rank if best_rank <= 100 else 0,
            '热榜上榜次数': hot_appear_count,
            '基本面逻辑': fund_logic[:3] if fund_logic else [],
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


def calc_fundamental_score_v2(ts_code, theme_name='', theme_trend_score=0, theme_sentiment_score=0,
                                stock_info=None, hot_rank=9999, hot_count=0):
    """
    行业景气度 + 个股基本面优势评分模块（短线增强型）
    
    目标：判断行业是否是资金主线，个股是否具备成为龙头的基本面支撑
    
    返回：
        dict: {
            "industry_score": 0-100,
            "fundamental_score": 0-100,
            "base_score": 0-100,
            "synergy_coeff": 0.5-1.5,
            "is_mainline": bool,
            "stage": str,
            "logic": [str, ...]
        }
    """
    try:
        logic = []
        
        # =========================
        # 一、行业景气度评分（0~100）
        # =========================
        
        # 【1】产业趋势强度（40%）
        trend_strength = 50  # 基础分
        if theme_trend_score >= 80:
            trend_strength = 95  # 绝对主线
            logic.append(f"产业趋势：绝对主线（趋势分{theme_trend_score:.0f}）")
        elif theme_trend_score >= 65:
            trend_strength = 80  # 强主线分支
            logic.append(f"产业趋势：强主线分支（趋势分{theme_trend_score:.0f}）")
        elif theme_trend_score >= 45:
            trend_strength = 65  # 轮动热点
            logic.append(f"产业趋势：轮动热点（趋势分{theme_trend_score:.0f}）")
        elif theme_trend_score >= 30:
            trend_strength = 45  # 边缘
            logic.append(f"产业趋势：边缘方向（趋势分{theme_trend_score:.0f}）")
        else:
            trend_strength = 25  # 退潮
            logic.append(f"产业趋势：退潮期（趋势分{theme_trend_score:.0f}）")
        
        # 【2】资金集中度（30%）
        # 使用热榜出现次数和排名作为代理
        concentration = 50
        if hot_count >= 5:
            if hot_rank <= 10:
                concentration = 95  # 高集中龙头
                logic.append("资金集中：高集中龙头，持续吸金")
            elif hot_rank <= 30:
                concentration = 80
                logic.append("资金集中：核心标的，资金关注度高")
            else:
                concentration = 65
                logic.append("资金集中：有一定资金关注")
        elif hot_count >= 2:
            concentration = 55
            logic.append("资金集中：偶尔上榜")
        else:
            concentration = 40
            logic.append("资金集中：无显著资金集中")
        
        # 【3】板块阶段（20%）
        stage = "未知"
        stage_score = 50
        if theme_sentiment_score >= 80:
            stage = "高潮期"
            stage_score = 55  # 风险上升
            logic.append("板块阶段：高潮期，谨慎追高")
        elif theme_sentiment_score >= 60:
            stage = "发酵期"
            stage_score = 75
            logic.append("板块阶段：发酵期，可参与")
        elif theme_sentiment_score >= 40:
            stage = "启动期"
            stage_score = 90  # 最优
            logic.append("板块阶段：启动期，最佳介入窗口")
        else:
            stage = "退潮期"
            stage_score = 30
            logic.append("板块阶段：退潮期，建议回避")
        
        # 【4】情绪热度（10%）
        emotion_heat = 50
        if hot_count >= 10:
            emotion_heat = 90
        elif hot_count >= 5:
            emotion_heat = 75
        elif hot_count >= 2:
            emotion_heat = 60
        elif hot_count >= 1:
            emotion_heat = 45
        else:
            emotion_heat = 30
        
        # 科技/创新主题溢价：新兴产业赛道获得额外加分
        # 传统产业（电力链、煤炭、银行等）即使短期趋势强，长期成长性也不如科技主题
        tech_innovation_themes = {
            '人形机器人', 'AI算力链', 'AI服务器与算力基建', 'AI芯片', 'AI模型与AI Agent',
            'AI应用', 'AI终端', 'AI文化娱乐', 'AI能源链',
            '半导体设备', '半导体制造', '半导体材料', '半导体封测', '半导体EDA/IP',
            '存储芯片', '先进封装', '先进封装材料', 'IC设计',
            '光刻机链', '光通信', 'PCB电子电路', '光学光电子', '消费电子', '被动元件',
            '物理AI', '智能驾驶', '低空经济', '商业航天', '军工',
            '脑机接口', '固态电池', '氢能', '核聚变', '新型储能',
            '数据要素', '信创软件', '金融科技', '电网数字化', '工业母机',
        }
        traditional_themes = {
            '电力链', '煤炭链', '银行', '保险', '券商', '贵金属', '工业金属', '小金属',
            '能源金属', '新能源汽车链', '必选消费红利链', '情绪消费成长链',
            '创新医药主线', '硫磺磷化工链', '电力设备出海',
        }
        
        if theme_name in tech_innovation_themes:
            tech_premium = 15  # 新兴产业溢价
            if logic:
                logic.insert(-4, f"产业趋势：科技创新主题溢价+{tech_premium}")
        elif theme_name in traditional_themes:
            tech_premium = -8  # 传统产业折价
            if logic:
                logic.insert(-4, f"产业趋势：传统行业折价{tech_premium}")
        else:
            tech_premium = 0
        
        trend_strength = max(10, min(100, trend_strength + tech_premium))
        
        # 计算行业景气度（主题质量权重从40%大幅降至15%）
        # 理由：主题质量波动大（今天高明天可能回落），不宜过度依赖
        industry_score = (
            trend_strength * 0.15 +  # 从0.40大幅降至0.15
            concentration * 0.30 +
            stage_score * 0.30 +    # 从0.20升至0.30（情绪更实时）
            emotion_heat * 0.25     # 从0.10升至0.25（热度更即时）
        )
        
        # =========================
        # 二、个股基本面优势评分（0~100）
        # =========================
        
        # 【1】产业地位（40%）
        position_score = 50
        
        # 尝试从stock_info获取市值信息，无stock_info时用hot_count代理
        is_large_cap = False
        if stock_info and 'total_market_cap' in stock_info:
            # 总市值大于500亿视为大市值
            if stock_info['total_market_cap'] > 500e8:
                is_large_cap = True
        elif hot_count >= 20:
            # 60天内上热榜20次+ = 大市值指数成份股（如鹏鼎控股、茅台等）
            # 这类股票有持续的市场关注度，但缺乏短线爆发弹性
            is_large_cap = True
        
        # 根据历史热榜排名判断是否为龙头
        if hot_rank <= 10 and hot_count >= 3:
            position_score = 95
            logic.append("个股地位：核心龙头，市场认可度高")
        elif hot_rank <= 30 and hot_count >= 2:
            position_score = 80
            logic.append("个股地位：强势标的，有辨识度")
        elif hot_rank <= 50 or hot_count >= 1:
            position_score = 65
            logic.append("个股地位：有资金关注")
        else:
            position_score = 45
            logic.append("个股地位：跟随标的，辨识度低")
        
        # 大市值扣分（短线资金偏好中小市值）
        if is_large_cap:
            position_score -= 10
            logic.append("个股地位：大市值，弹性受限")
        
        position_score = max(0, min(100, position_score))
        
        # 【2】成长弹性（30%）
        growth_score = 50
        # 使用60日涨幅作为代理
        try:
            csv_path = os.path.join(CACHE_DIR, f"{ts_code}.csv")
            if os.path.exists(csv_path):
                df = pd.read_csv(csv_path)
                if len(df) >= 60:
                    df = df.sort_values('trade_date', ascending=True)
                    close_60d = float(df.iloc[-60]['close'])
                    close_now = float(df.iloc[-1]['close'])
                    ret_60d = (close_now - close_60d) / close_60d if close_60d > 0 else 0
                    
                    if ret_60d >= 0.5:
                        growth_score = 95
                        logic.append(f"成长弹性：强趋势（60日涨幅{ret_60d*100:.0f}%）")
                    elif ret_60d >= 0.3:
                        growth_score = 80
                        logic.append(f"成长弹性：良好（60日涨幅{ret_60d*100:.0f}%）")
                    elif ret_60d >= 0.1:
                        growth_score = 65
                        logic.append(f"成长弹性：一般（60日涨幅{ret_60d*100:.0f}%）")
                    else:
                        growth_score = 45
                        logic.append(f"成长弹性：偏弱（60日涨幅{ret_60d*100:.0f}%）")
        except:
            pass
        
        # 【3】事件催化（20%）
        # 使用主题生命力作为代理
        catalyst_score = 50
        try:
            tli_score, _ = calc_tli_score(theme_name, top_n=10, days=30)
            if tli_score >= 80:
                catalyst_score = 85
                logic.append("事件催化：强催化窗口")
            elif tli_score >= 60:
                catalyst_score = 70
                logic.append("事件催化：有催化预期")
            elif tli_score >= 40:
                catalyst_score = 55
                logic.append("事件催化：催化减弱")
            else:
                catalyst_score = 40
                logic.append("事件催化：无明显催化")
        except:
            pass
        
        # 【4】市场记忆度（10%）
        memory_score = 50
        if hot_count >= 5:
            memory_score = 90  # 反复炒作
            logic.append("市场记忆：历史龙头，反复活跃")
        elif hot_count >= 2:
            memory_score = 70
            logic.append("市场记忆：有一定炒作基础")
        else:
            memory_score = 40
            logic.append("市场记忆：缺乏辨识度")
        
        # 计算个股基本面优势
        fundamental_score = (
            position_score * 0.40 +
            growth_score * 0.30 +
            catalyst_score * 0.20 +
            memory_score * 0.10
        )
        
        # =========================
        # 三、共振系数计算
        # =========================
        base = industry_score * 0.6 + fundamental_score * 0.4
        
        if base >= 85:
            synergy_coeff = 1.30 + min(0.2, (base - 85) / 75)  # 1.30-1.50
        elif base >= 70:
            synergy_coeff = 1.10 + (base - 70) / 75  # 1.10-1.30
        elif base >= 50:
            synergy_coeff = 0.90 + (base - 50) / 100  # 0.90-1.10
        else:
            synergy_coeff = 0.60 + base / 125  # 0.60-0.90
        
        # =========================
        # 四、短线过滤规则
        # =========================
        is_mainline = True
        if industry_score < 40 and fundamental_score < 50:
            synergy_coeff = min(synergy_coeff, 0.8)
            is_mainline = False
            logic.append("过滤规则：行业+个股双弱，强制降权")
        
        # 高潮期+高情绪 = 风险
        if theme_sentiment_score >= 85 and hot_count >= 5:
            synergy_coeff *= 0.9
            logic.append("风险提示：高潮期+高热度，降低预期")
        
        synergy_coeff = round(max(0.5, min(1.5, synergy_coeff)), 2)
        
        # 最终结论
        if synergy_coeff >= 1.2:
            logic.append(f"结论：强共振环境（系数{synergy_coeff}），优先考虑")
        elif synergy_coeff >= 1.0:
            logic.append(f"结论：可交易环境（系数{synergy_coeff}）")
        elif synergy_coeff >= 0.8:
            logic.append(f"结论：普通环境（系数{synergy_coeff}），谨慎参与")
        else:
            logic.append(f"结论：弱势环境（系数{synergy_coeff}），建议回避")
        
        return {
            "industry_score": round(industry_score, 1),
            "fundamental_score": round(fundamental_score, 1),
            "base_score": round(base, 1),
            "synergy_coeff": synergy_coeff,
            "is_mainline": is_mainline,
            "stage": stage,
            "logic": logic
        }
        
    except Exception as e:
        print(f"[基本面评分] 异常: {e}")
        return {
            "industry_score": 50,
            "fundamental_score": 50,
            "base_score": 50,
            "synergy_coeff": 1.0,
            "is_mainline": False,
            "stage": "未知",
            "logic": ["评分异常，使用默认值"]
        }


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
        high_series = df['high']
        MA20 = float(close_series.rolling(20).mean().iloc[-1])
        MA60 = float(close_series.rolling(60).mean().iloc[-1])
        # 修复: HHV用最高价(非收盘价)，且排除当天数据
        HHV20 = float(high_series.iloc[:-1].tail(20).max()) if len(high_series) > 1 else float(close_series.tail(20).max())
        HHV60 = float(high_series.iloc[:-1].tail(60).max()) if len(high_series) > 1 else float(close_series.tail(60).max())
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
        # 换手率打分（相对指标，考虑流通盘大小 + 量比放大程度）
        # =========================
        turnover_rank_score = 50
        try:
            # 从 stock_info/v7_result 获取 ts_code
            ts_code_for_turnover = ""
            if isinstance(stock_info, dict):
                ts_code_for_turnover = stock_info.get('代码', '') or stock_info.get('ts_code', '')
            elif hasattr(stock_info, 'get'):
                ts_code_for_turnover = stock_info.get('代码', '')
            
            # 1. 当日换手率（相对活跃程度，占70%权重）
            today_turnover = 0
            if ts_code_for_turnover:
                today_turnover = get_cached_turnover(ts_code_for_turnover)
            
            # 2. 量比（放大程度，占30%权重，均线排除当天）
            if len(df) >= 22:
                vol_hist = df['vol'].iloc[:-1]  # 排除当天
                recent_5d = vol_hist.tail(5)
                recent_20d = vol_hist.tail(20)
                vol_ratio_for_rank = recent_5d.mean() / recent_20d.mean() if recent_20d.mean() > 0 else 1.0
            else:
                vol_ratio_for_rank = 1.0
            
            # 综合打分：换手率 + 量比放大
            if today_turnover > 0:
                # 换手率评分（70分）
                turnover_sub_score = 0
                if today_turnover >= 10:
                    turnover_sub_score = 70
                elif today_turnover >= 5:
                    turnover_sub_score = 60
                elif today_turnover >= 3:
                    turnover_sub_score = 50
                elif today_turnover >= 1.5:
                    turnover_sub_score = 40
                else:
                    turnover_sub_score = 25
                
                # 量比评分（30分）
                vol_ratio_sub_score = 0
                if vol_ratio_for_rank >= 2.0:
                    vol_ratio_sub_score = 30
                elif vol_ratio_for_rank >= 1.5:
                    vol_ratio_sub_score = 25
                elif vol_ratio_for_rank >= 1.2:
                    vol_ratio_sub_score = 20
                elif vol_ratio_for_rank >= 1.0:
                    vol_ratio_sub_score = 15
                else:
                    vol_ratio_sub_score = 10
                
                turnover_rank_score = turnover_sub_score + vol_ratio_sub_score
            else:
                # 无换手率数据时降级为量比评分
                if vol_ratio_for_rank >= 2.0:
                    turnover_rank_score = 85
                elif vol_ratio_for_rank >= 1.5:
                    turnover_rank_score = 70
                elif vol_ratio_for_rank >= 1.0:
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
        recommendation += f" | 换手率{turnover_rank_score:.0f}分"

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
    
    # ===== 创业板/科创板判断（仅用于今日涨停过滤）=====
    # 主板：10% 涨停；双创板：20% 涨停
    IS_CYB_KCB = (code.startswith('3') or code.startswith('688') or code.startswith('689'))
    ZT_SINGLE_UP = 1.198 if IS_CYB_KCB else 1.098

    # ===== 快速过滤：今天已涨停 → 直接排除 =====
    if len(df) >= 3:
        today_ratio = C[-1] / C[-2]
        if today_ratio >= ZT_SINGLE_UP:
            return False

    # ST名称过滤（延后到这里，只在必要时调用）
    StockName = get_stock_name(code)
    ST1 = (StockName.upper().startswith('ST') or 
            StockName.upper().startswith('*ST'))
    if ST1:
        return False
    
    # ===== 启动过滤：60日振幅 =====
    if len(df) >= 20:
        hh = H[-20:].max()
        ll = L[-20:].min()
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
    if C[-1] >= ma20[-1] * 1.3 or C[-1] / ma60[-1] > 2:
        return False
    
    # 股价必须站上5日、10日、20日均线
    if  C[-1] < ma20[-1] or ma10[-1] < ma20[-1]*0.97 or ma5[-1] < ma10[-1]*0.97:
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
    
    cond_xh1 = ((C[-1] > highest_close) or (C[-1] > C[-2] and C[-1] > C[-3] and C[-1]/C[-2] > 1.05 and C[-1]/C[-2] < 1.15))
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


def calc_tech_indicators(df):
    """计算关键技术指标价格（供AI分析使用）
    返回 MA5/MA10/MA20/MA60价格、20日/60日最高价、最近5日K线高低点
    所有价格均基于真实收盘价/最高价计算，排除当天数据计算历史参考点
    """
    result = {}
    if df is None or len(df) < 5 or not isinstance(df, pd.DataFrame):
        return result
    
    try:
        close = df['close']
        high = df['high']
        low = df['low'] if 'low' in df.columns else close
        current_price = float(close.iloc[-1])
        result['current_price'] = current_price
        
        # 均线价格
        result['ma5'] = round(float(close.rolling(5).mean().iloc[-1]), 2) if len(close) >= 5 else current_price
        result['ma10'] = round(float(close.rolling(10).mean().iloc[-1]), 2) if len(close) >= 10 else current_price
        result['ma20'] = round(float(close.rolling(20).mean().iloc[-1]), 2) if len(close) >= 20 else current_price
        result['ma60'] = round(float(close.rolling(60).mean().iloc[-1]), 2) if len(close) >= 60 else current_price
        
        # 历史高点（排除当天，作为参考压力位）
        hist_high_20 = float(high.iloc[:-1].tail(20).max()) if len(close) > 1 else float(high.tail(20).max())
        hist_high_60 = float(high.iloc[:-1].tail(60).max()) if len(close) > 1 else float(high.tail(60).max())
        result['high_20d'] = round(hist_high_20, 2)
        result['high_60d'] = round(hist_high_60, 2)
        
        # 历史低点
        hist_low_20 = float(close.iloc[:-1].tail(20).min()) if len(close) > 1 else float(close.tail(20).min())
        hist_low_60 = float(close.iloc[:-1].tail(60).min()) if len(close) > 1 else float(close.tail(60).min())
        result['low_20d'] = round(hist_low_20, 2)
        result['low_60d'] = round(hist_low_60, 2)
        
        # 距高点的百分比（正数=距高点空间，负数=已突破）
        if hist_high_20 > 0:
            result['dist_to_high20_pct'] = round(((hist_high_20 - current_price) / current_price) * 100, 1)
        if hist_high_60 > 0:
            result['dist_to_high60_pct'] = round(((hist_high_60 - current_price) / current_price) * 100, 1)
        
        # 距20/60日均线百分比（正数=在均线上方）
        if result['ma20'] > 0:
            result['dist_to_ma20_pct'] = round(((current_price - result['ma20']) / result['ma20']) * 100, 1)
        if result['ma60'] > 0:
            result['dist_to_ma60_pct'] = round(((current_price - result['ma60']) / result['ma60']) * 100, 1)
        
        # 近5日K线概况（最高最低）
        recent_5 = df.tail(5)
        if len(recent_5) >= 5:
            result['recent5_high'] = round(float(recent_5['high'].max()), 2)
            result['recent5_low'] = round(float(recent_5['low'].min()), 2) if 'low' in df.columns else round(float(recent_5['close'].min()), 2)
            result['recent5_range_pct'] = round(((result['recent5_high'] - result['recent5_low']) / result['recent5_low']) * 100, 1)
        
        # 近10日涨跌幅
        if len(df) >= 10:
            price_10d_ago = float(close.iloc[-10])
            if price_10d_ago > 0:
                result['chg_10d_pct'] = round(((current_price - price_10d_ago) / price_10d_ago) * 100, 1)
        
        # 均线方向（向上/向下）
        if len(close) >= 25:
            ma20_prev = float(close.iloc[:-1].tail(20).mean())
            result['ma20_trend'] = '向上' if result['ma20'] > ma20_prev else '向下'
        if len(close) >= 65:
            ma60_prev = float(close.iloc[:-1].tail(60).mean())
            result['ma60_trend'] = '向上' if result['ma60'] > ma60_prev else '向下'
        
    except Exception as e:
        pass
    
    return result


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
        
        # =====批量获取未复权实际价格（修复前复权价格bug）=====
        raw_price_dict = {}
        try:
            if pro is not None and not recent_stocks.empty:
                all_codes = recent_stocks['code'].tolist()
                # 用 daily 接口批量获取最新未复权价格（adj不填=未复权）
                # 分批次，每批100只
                for i in range(0, len(all_codes), 100):
                    batch_codes = all_codes[i:i+100]
                    try:
                        df_raw = pro.daily(
                            ts_code=','.join(batch_codes),
                            start_date='20250101',
                            end_date=TRADE_DATE
                        )
                        if df_raw is not None and not df_raw.empty:
                            df_raw = df_raw.sort_values('trade_date')
                            # 取每只股票最后一条（最新价格）
                            for ts_code, grp in df_raw.groupby('ts_code'):
                                last_row = grp.iloc[-1]
                                raw_price_dict[ts_code] = {
                                    'close': float(last_row['close']),
                                    'pct_chg': float(last_row['pct_chg'])
                                }
                    except Exception as e:
                        print(f'批量获取未复权价失败（批次{i}）: {e}')
                print(f'批量获取未复权价完成，共 {len(raw_price_dict)} 只')
        except Exception as e:
            print(f'批量获取未复权价失败: {e}')
        
        # 生成跟踪分析股票列表
        tracking_stocks = []
        seen_codes = set()  # 去重集合
        
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
                        
                        # 计算关键技术指标（供AI分析使用，防止编造价格）
                        tech = calc_tech_indicators(df_hist)
                    else:
                        # 数据不足，回退到旧评分
                        last_score = float(row['score']) if str(row['score']).strip() not in ['', 'None'] else 0.0
                        if last_score > 100:
                            last_score = min(last_score, 50)
                        open_score = 0
                        structure_type = "未知"
                        open_recommendation = "数据不足"
                        tech = {}
                except Exception as e:
                    # 计算失败，回退到旧评分
                    print(f"整合评分计算失败 {ts_code}: {e}")
                    last_score = float(row['score']) if str(row['score']).strip() not in ['', 'None'] else 0.0
                    if last_score > 100:
                        last_score = min(last_score, 50)
                    open_score = 0
                    structure_type = "未知"
                    open_recommendation = "计算失败"
                    tech = {}
                
                # 优先从缓存文件获取今日价格和涨跌幅（已在MA5计算时读取）
                if cache_close > 0:
                    latest_close = cache_close
                    pct_chg = cache_pct_chg
                else:
                    # 回退到历史数据库数据
                    latest_close = float(row['close']) if str(row['close']).strip() not in ['', 'None'] else 0.0
                    pct_chg = 0.0
                

                # 【BugFix】用批量获取的未复权实际价格覆盖前复权价格
                if ts_code in raw_price_dict:
                    raw = raw_price_dict[ts_code]
                    latest_close = raw["close"]
                    pct_chg = raw["pct_chg"]
                else:
                    # 批量未获取到，单独补一次
                    try:
                        if pro is not None:
                            df_raw = pro.daily(ts_code=ts_code, start_date="20250101", end_date=TRADE_DATE)
                            if df_raw is not None and not df_raw.empty:
                                df_raw = df_raw.sort_values("trade_date")
                                latest_close = float(df_raw.iloc[-1]["close"])
                                pct_chg = float(df_raw.iloc[-1]["pct_chg"])
                    except Exception as e:
                        pass

                # 过滤条件：只保留今天下跌的个股（洗盘形态）
                if pct_chg >= 0:
                    continue
                
                # 去重：用代码做key，避免重复输出同一只股票
                seen_codes.add(ts_code)
                
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
                    'fundamental_score': fundamental_score,
                    # 技术指标（供AI分析，防止编造价格）
                    'ma5': tech.get('ma5', latest_close),
                    'ma10': tech.get('ma10', latest_close),
                    'ma20': tech.get('ma20', latest_close),
                    'ma60': tech.get('ma60', latest_close),
                    'high20': tech.get('high_20d', latest_close),
                    'high60': tech.get('high_60d', latest_close),
                    'dist_high20': tech.get('dist_to_high20_pct', 0),
                    'dist_high60': tech.get('dist_to_high60_pct', 0),
                    'ma20_trend': tech.get('ma20_trend', ''),
                    'ma60_trend': tech.get('ma60_trend', ''),
                    'chg_10d': tech.get('chg_10d_pct', 0),
                })
        
        # 按整合评分排序，取前30只
        tracking_stocks = sorted(tracking_stocks, key=lambda x: -x['open_score'])[:30]
        
        # 生成文本格式 - 与主程序Top10完全相同的格式（【技术价位】【参考位】标签已验证有效）
        lines = []
        if tracking_stocks:
            lines.append("=" * 100)
            lines.append("🔥 跟踪分析股票池 - 【价格约束】")
            lines.append("=" * 100)
            lines.append("【价格约束】本区块所有股票的【技术价位】和【参考位】")
            lines.append("【价格约束】均为基于真实日线数据的精确计算值，")
            lines.append("【价格约束】进行技术面分析时必须严格使用下方标注的")
            lines.append("【价格约束】MA价格、高点价格、现价等数据，")
            lines.append("【价格约束】绝对禁止编造任何价格数字！")
            lines.append("【价格约束】注意：若标注现价=338.90元 MA20=360.52元 20日高点=402.60元，")
            lines.append("【价格约束】则分析中必须写完全相同的数字，不能写成现价=9.74元 MA20=9.58元等编造值！")
            lines.append("=" * 100)

            # 每只股票使用与Top10完全相同的格式：【第N名】股票名(代码) + 【技术价位】+【参考位】
            for i, stock in enumerate(tracking_stocks, 1):
                lines.append(f"【第{i}名】{stock['name']} ({stock['code']})")
                lines.append(f"  整合评分: {stock['open_score']:.1f} | 失败概率: {stock.get('failure_prob', 0):.1f}%")
                lines.append(f"  今日涨幅: {stock.get('pct_chg', 0):.2f}% | 现价: {stock['last_close']:.2f}元 | 量比: {stock.get('vol_ratio', 0):.2f}")
                lines.append(f"  5日涨幅: {stock['range_5d_pct']:+.1f}% | 近10日涨跌: {stock.get('chg_10d', 0):+.1f}%")
                lines.append(f"  趋势强度: {stock.get('trend_score', 0):.1f} | 资金健康度: {stock.get('capital_score', 0):.1f}")
                lines.append(f"  位置安全: {stock.get('position_score', 0):.1f} | 热度持续: {stock.get('heat_score', 0):.1f}")
                lines.append(f"  结构类型: {stock.get('structure_type', '')}")
                # 【技术价位】和【参考位】- 与主程序Top10完全相同的格式（已验证对AI有效）
                lines.append(f"  【技术价位】MA5={stock.get('ma5', stock['last_close']):.2f}元 MA10={stock.get('ma10', stock['last_close']):.2f}元 MA20={stock.get('ma20', stock['last_close']):.2f}元({stock.get('ma20_trend','')}) MA60={stock.get('ma60', stock['last_close']):.2f}元({stock.get('ma60_trend','')})")
                lines.append(f"  【参考位】20日高点={stock.get('high20', stock['last_close']):.2f}元 60日高点={stock.get('high60', stock['last_close']):.2f}元 距20高={stock.get('dist_high20', 0):+.1f}% 距60高={stock.get('dist_high60', 0):+.1f}%")
                lines.append("")
            lines.append("=" * 100)

            # 再次强调价格约束（三重提醒）
            lines.append("")
            lines.append("【价格约束三重提醒】以上每只股票的【技术价位】和【参考位】中的")
            lines.append("所有价格数字均为基于真实日线数据计算的精确值。")
            lines.append("进行分析时，请先确认价格数字，再给出技术分析结论。")
            lines.append("特别提醒：如果股票标注MA20=360.52元，分析中不能写成MA20=9.58元！")
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
    主题质量过滤 + 高潮风险控制
    
    核心逻辑：
    1. 统计60天内各主题进入TOP5的次数（存在感）
    2. 检测"脉冲热点"：近10天才突然进入前列，之前毫无存在感
    3. 检测"高潮风险"：连续多日情绪分>70，回调概率大
    4. 综合评估主题质量，过滤低质量主题的成份股
    
    参数：
        result_df: 待过滤的股票DataFrame
        top_n: 主题综合排名取前N名
    
    返回：
        过滤后的DataFrame，注入主题相关字段
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
        
        # 获取过去60天的所有交易日（从旧到新排序，基于TRADE_DATE回溯）
        trade_dates_df = pd.read_sql(
            f"SELECT DISTINCT trade_date FROM theme_scores "
            f"WHERE trade_date <= '{TRADE_DATE}' "
            f"ORDER BY trade_date ASC LIMIT 60",
            conn
        )
        all_trade_dates = trade_dates_df['trade_date'].tolist()
        
        if not all_trade_dates:
            print("[主题过滤] 无历史数据，跳过过滤")
            conn.close()
            return result_df
        
        total_days = len(all_trade_dates)
        print(f"[主题过滤] 分析最近 {total_days} 个交易日（截至{TRADE_DATE}）")
        
        # 以最近10天为"近期"窗口
        recent_window = min(10, total_days // 3)  # 至少取1/3时间
        recent_dates = set(all_trade_dates[-recent_window:])  # 最近N天
        early_dates = set(all_trade_dates[:-recent_window])    # 之前的天
        
        # 数据结构
        # theme_stats[theme] = {
        #     'total_top5_count': 总上榜次数,
        #     'recent_top5_count': 近N天上榜次数,
        #     'early_top5_count': 之前上榜次数,
        #     'first_seen_idx':  首次出现索引,
        #     'consecutive_high_sentiment': 连续高潮天数,
        #     'sentiment_scores': [近N天情绪分],
        #     'latest_state': 最新状态,
        #     'latest_trend': 最新趋势分,
        #     'latest_sentiment': 最新情绪分,
        #     'latest_composite': 最新综合分,
        #     'last_3_sentiment': 最近3天情绪分,
        # }
        theme_stats = {}
        
        for day_idx, trade_date in enumerate(all_trade_dates):
            day_df = pd.read_sql(
                f"SELECT theme, trend_score, sentiment_score, composite_score, theme_state "
                f"FROM theme_scores WHERE trade_date = '{trade_date}'",
                conn
            )
            if day_df.empty:
                continue
            
            # 按综合分排序取TOP10（扩大窗口，避免半导体类独占导致其他主题被过滤）
            day_df = day_df.sort_values('composite_score', ascending=False).head(10)
            
            for _, row in day_df.iterrows():
                theme = row['theme']
                
                if theme not in theme_stats:
                    theme_stats[theme] = {
                        'total_top5_count': 0,
                        'recent_top5_count': 0,
                        'early_top5_count': 0,
                        'first_seen_idx': day_idx,
                        'consecutive_high_sentiment': 0,
                        'sentiment_scores': [],
                        'latest_state': '',
                        'latest_trend': 0,
                        'latest_sentiment': 0,
                        'latest_composite': 0,
                        'last_3_sentiment': [],
                    }
                
                theme_stats[theme]['total_top5_count'] += 1
                
                # 区分近期和早期
                if trade_date in recent_dates:
                    theme_stats[theme]['recent_top5_count'] += 1
                else:
                    theme_stats[theme]['early_top5_count'] += 1
                
                # 记录情绪分数用于检测高潮（带上日期）
                sentiment = float(row.get('sentiment_score', 0) or 0)
                theme_stats[theme]['sentiment_scores'].append((trade_date, sentiment))
                
                # 保存最新数据（仅来自TOP5记录，可能不是最新日期的）
                theme_stats[theme]['latest_state'] = row.get('theme_state', '')
                theme_stats[theme]['latest_trend'] = float(row.get('trend_score', 0) or 0)
                theme_stats[theme]['latest_sentiment'] = sentiment
                theme_stats[theme]['latest_composite'] = float(row.get('composite_score', 0) or 0)
        
        # 补充：用最新交易日（TRADE_DATE）的数据覆盖所有主题的latest_*字段
        # 因为TOP5记录可能不是最新日期（已退潮主题最后进TOP5可能很久以前）
        latest_date = TRADE_DATE  # 直接用TRADE_DATE，all_trade_dates[-1]因LIMIT截断可能不是最新
        if latest_date:
            try:
                extra_conn = sqlite3.connect(db_path)
                latest_day_df = pd.read_sql(
                    f"SELECT theme, trend_score, sentiment_score, composite_score, theme_state "
                    f"FROM theme_scores WHERE trade_date = '{latest_date}'",
                    extra_conn
                )
                extra_conn.close()
                for _, row in latest_day_df.iterrows():
                    theme = row['theme']
                    if theme in theme_stats:
                        # 用最新交易日的数据覆盖，无论是否在TOP5中
                        theme_stats[theme]['latest_trend'] = float(row.get('trend_score', 0) or 0)
                        theme_stats[theme]['latest_sentiment'] = float(row.get('sentiment_score', 0) or 0)
                        theme_stats[theme]['latest_composite'] = float(row.get('composite_score', 0) or 0)
                        theme_stats[theme]['latest_state'] = row.get('theme_state', '')
                    else:
                        # 即使从未进入TOP5，也保留其最新数据供参考
                        theme_stats[theme] = {
                            'total_top5_count': 0,
                            'recent_top5_count': 0,
                            'early_top5_count': 0,
                            'first_seen_idx': 9999,
                            'consecutive_high_sentiment': 0,
                            'sentiment_scores': [],
                            'latest_state': row.get('theme_state', ''),
                            'latest_trend': float(row.get('trend_score', 0) or 0),
                            'latest_sentiment': float(row.get('sentiment_score', 0) or 0),
                            'latest_composite': float(row.get('composite_score', 0) or 0),
                        }
            except Exception as e:
                print(f"[主题过滤] 补充最新交易日数据失败: {e}")
        
        conn.close()
        
        if not theme_stats:
            print("[主题过滤] 无主题数据，跳过过滤")
            return result_df
        
        # 2. 计算每个主题的质量评分（0-100）
        theme_quality = {}
        
        # 当天最近几天的数据用于检测连续高潮
        today_recent_dates = all_trade_dates[-5:] if len(all_trade_dates) >= 5 else all_trade_dates
        
        for theme, stats in theme_stats.items():
            # 基础存在感（35%）：60天内进入TOP10的次数
            presence_score = min(100, stats['total_top5_count'] * 4.2)
            
            # ----- 持续性（15%）-----
            # 核心判断：是否持续活跃，而非"曾经活跃但现在已退潮"
            if stats['total_top5_count'] >= 3:
                # 近期占比越高越好（近期>0且有持续上榜）
                # 如果近期=0，说明已经完全退潮，0分
                if stats['recent_top5_count'] == 0:
                    consistency_score = 10  # 已退潮主题，基本不合格
                else:
                    # 近期占比越高=持续活跃度越高
                    recent_ratio = stats['recent_top5_count'] / stats['total_top5_count']
                    # 近期占比>30%说明持续性好
                    consistency_score = min(100, recent_ratio * 200 + stats['recent_top5_count'] * 5)
            else:
                consistency_score = 0  # 出现太少，直接0分
            
            # ----- 趋势活力（25%）← 新增维度 -----
            # 核心：当前趋势分越高 = 越活跃，越低 = 越低迷
            # 情绪分用于修正：过高减分（过热），过低减分（冷清）
            trend_vitality = 50  # 基础分
            
            latest_trend = stats['latest_trend']
            latest_sentiment = stats['latest_sentiment']
            
            # 趋势分是核心驱动力
            if latest_trend >= 75:
                trend_vitality = 90  # 强趋势主线
            elif latest_trend >= 65:
                trend_vitality = 80  # 趋势良好
            elif latest_trend >= 55:
                trend_vitality = 65  # 趋势一般
            elif latest_trend >= 40:
                trend_vitality = 45  # 趋势偏弱
            else:
                trend_vitality = 25  # 趋势低迷
            
            # 情绪分修正：极低情绪=冷清，贴切修正
            if latest_sentiment <= 30:
                trend_vitality -= 15  # 情绪冰点，无人问津
                print(f"[主题过滤] ⚠ {theme}: 趋势{latest_trend:.0f}但情绪{latest_sentiment:.0f}低迷，质量折价")
            elif latest_sentiment <= 40:
                trend_vitality -= 8   # 情绪偏低
            
            # 趋势 < 50 且 情绪 < 50 = 双重低迷
            if latest_trend < 50 and latest_sentiment < 50:
                trend_vitality -= 10
                print(f"[主题过滤] ⚠ {theme}: 趋势+情绪双弱({latest_trend:.0f}/{latest_sentiment:.0f})，持续低迷")
            
            trend_vitality = max(5, min(100, trend_vitality))
            
            # ----- 高潮风险检测（15%）-----
            # 只检查最近5个交易日的情绪分
            risk_score = 70  # 基础分
            
            # 从sentiment_scores中筛选最近5天的数据
            recent_cutoff = all_trade_dates[-5] if len(all_trade_dates) >= 5 else all_trade_dates[0]
            recent_sentiments = [
                s for d, s in theme_stats[theme]['sentiment_scores']
                if d >= recent_cutoff
            ]
            
            if len(recent_sentiments) >= 2:
                # 检测连续高潮：情绪分持续>70
                high_sentiment_days = sum(1 for s in recent_sentiments if s >= 70)
                
                if high_sentiment_days >= 4 and len(recent_sentiments) >= 4:
                    risk_score = 20
                    print(f"[主题过滤] ⚠ {theme}: 最近{len(recent_sentiments)}天{high_sentiment_days}天情绪>70，高潮风险极高")
                elif high_sentiment_days >= 3:
                    risk_score = 35
                    print(f"[主题过滤] ⚠ {theme}: 最近{len(recent_sentiments)}天{high_sentiment_days}天情绪>70，高潮风险偏高")
                elif high_sentiment_days >= 2:
                    risk_score = 50
            
            # ----- 脉冲热点检测（10%）-----
            # 如果近10天才首次进入TOP5，之前毫无记录 = 脉冲热点
            pulse_risk = 70  # 基础分
            
            if stats['first_seen_idx'] >= total_days - recent_window:
                # 首次出现在近期窗口
                if stats['early_top5_count'] == 0 and stats['recent_top5_count'] >= 2:
                    # 之前从未上榜，近几天突然出现多次 = 典型脉冲
                    pulse_risk = 15
                    print(f"[主题过滤] ⚠ {theme}: 脉冲热点，仅近{recent_window}天才进入前列")
                elif stats['early_top5_count'] <= 2:
                    pulse_risk = 35
                    print(f"[主题过滤] ⚠ {theme}: 可能为脉冲热点，早期存在感低")
            
            # ----- 综合主题质量评分 -----
            quality_score = (
                presence_score * 0.35 +
                consistency_score * 0.15 +
                trend_vitality * 0.25 +
                risk_score * 0.15 +
                pulse_risk * 0.10
            )
            
            theme_quality[theme] = {
                'quality_score': quality_score,
                'total_top5_count': stats['total_top5_count'],
                'early_top5_count': stats['early_top5_count'],
                'recent_top5_count': stats['recent_top5_count'],
                'first_seen_idx': stats['first_seen_idx'],
                'latest_state': stats['latest_state'],
                'latest_trend': stats['latest_trend'],
                'latest_sentiment': stats['latest_sentiment'],
                'latest_composite': stats['latest_composite'],
            }
        
        # 3. 筛选高质量主题
        # 科技主线白名单：当前市场核心科技方向，即使评分略低也保留
        # 这些主题代表中长期产业趋势，短期评分波动不应导致过滤
        tech_mainline_whitelist = {
            '人形机器人', 'AI算力链', 'AI服务器与算力基建', 'AI芯片',
            'AI终端', '半导体设备', '半导体制造', '数据中心瓶颈硬件链',
            '半导体材料', '半导体封测', '存储芯片', '先进封装', '先进封装材料',
            '光刻机链', '光通信', '物理AI', '低空经济', '商业航天',
            '固态电池', '氢能', '核聚变', 
        }
        
        # 质量阈值：>=55分保留（收紧阈值，过滤退潮主题）
        quality_threshold = 55
        
        # 非白名单主题的强化阈值：需要更高质量才能通过
        non_whitelist_threshold = 58  # 非白名单主题需要更严格的评分
        
        # 宽容规则（仅适用于白名单主题）：总上榜≥15次且质量≥48分
        keep_themes = set()
        for theme, data in theme_quality.items():
            is_whitelist = theme in tech_mainline_whitelist
            
            if data['quality_score'] >= quality_threshold:
                keep_themes.add(theme)
            elif is_whitelist and data['total_top5_count'] >= 15 and data['quality_score'] >= 48:
                # 科技主线白名单保护：频繁出现且质量不太差，给予宽容
                keep_themes.add(theme)
                print(f"[主题过滤] 白名单宽容保留 {theme}: 上榜{data['total_top5_count']}次/质量{data['quality_score']:.0f}分")
            elif is_whitelist and data['quality_score'] >= 35:
                # 科技主线白名单保护：只要不是完全退潮（质量≥35），就保留
                keep_themes.add(theme)
                print(f"[主题过滤] 白名单保留 {theme}: 质量{data['quality_score']:.0f}分（科技主线保护）")
            elif not is_whitelist and data['quality_score'] >= non_whitelist_threshold:
                # 非白名单主题需要更高阈值才能通过，避免追冷门题材
                keep_themes.add(theme)
            elif not is_whitelist and data['total_top5_count'] >= 20 and data['quality_score'] >= 52:
                # 非白名单但极度频繁出现（≥20次），给予有限宽容
                keep_themes.add(theme)
                print(f"[主题过滤] 高频保留 {theme}: 上榜{data['total_top5_count']}次/质量{data['quality_score']:.0f}分（非白名单但持续活跃）")
        
        if not keep_themes:
            print("[主题过滤] 无高质量主题通过过滤")
            # 降级：取质量分最高的3个主题
            sorted_themes = sorted(theme_quality.items(), key=lambda x: -x[1]['quality_score'])
            keep_themes = {t[0] for t in sorted_themes[:3]}
            print(f"[主题过滤] 降级保留TOP3主题: {keep_themes}")
        
        # 打印质量评分
        print(f"\n[主题过滤] 主题质量评分（阈值{quality_threshold}分，保留{len(keep_themes)}个）:")
        for theme in sorted(keep_themes, key=lambda x: -theme_quality[x]['quality_score']):
            d = theme_quality[theme]
            print(f"  {theme}: 质量{d['quality_score']:.0f}分 | 上榜{d['total_top5_count']}次"
                  f" (早期{d['early_top5_count']}+近期{d['recent_top5_count']})"
                  f" | 情绪{d['latest_sentiment']:.0f} | 趋势{d['latest_trend']:.0f}")
        print()
        
        # 构建主题状态映射
        theme_state_map = {}
        for theme in keep_themes:
            if theme in theme_quality:
                d = theme_quality[theme]
                theme_state_map[theme] = {
                    'theme_state': d['latest_state'],
                    'trend_score': d['latest_trend'],
                    'sentiment_score': d['latest_sentiment'],
                    'composite_score': d['latest_composite'],
                }
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
            for theme_name in keep_themes:
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
        return _filter_by_top_themes_fallback(result_df, keep_themes, theme_cfg)

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
            
            # 获取今日成交额（亿）和换手率
            # amount在缓存中单位是千元，vol单位是万手
            today_amount = 0.0
            today_close = float(df['close'].iloc[-1])
            if 'amount' in df.columns:
                today_amount = round(float(df['amount'].iloc[-1]) / 100000, 2)  # 千元转亿：除10000
            # 从全局缓存获取换手率（程序启动时已批量加载）
            today_turnover = get_cached_turnover(ts_code)
            
            # 使用统一评分算法
            theme_trend_score = float(row.get('主题趋势分', 0))
            theme_sentiment_score = float(row.get('主题情绪分', 0))
            integrated_score, recommendation, details, failure_prob = calc_unified_stock_score(
                df, ts_code, theme_name, theme_trend_score, theme_sentiment_score
            )
            
            # 计算关键技术指标价格（供AI分析使用，避免编造价格）
            tech = calc_tech_indicators(df)
            
            stock_data = {
                '代码': ts_code, '名称': name, '现价': today_close,
                '涨跌幅': today_pct, '成交额': today_amount, '换手率': today_turnover,
                '所属主题': theme_name,
                '整合评分': integrated_score, '失败概率': failure_prob,
                '推荐理由': recommendation,
                '趋势强度': details.get('趋势强度', 0), '资金健康度': details.get('资金健康度', 0),
                '位置安全性': details.get('位置安全性', 0), '热度持续性': details.get('热度持续性', 0),
                '基本面': details.get('基本面', 0),
                '热榜最佳排名': details.get('热榜最佳排名', 0), '热榜上榜次数': details.get('热榜上榜次数', 0),
                '所属状态': str(row.get('所属状态', '')),
                '主题趋势分': float(row.get('主题趋势分', 0)), '主题情绪分': float(row.get('主题情绪分', 0)),
                '量能爆发': details.get('量能爆发', 0), '突破强度': float(row.get('突破强度', 0)),
                # 技术指标（供AI分析，防止编造价格）
                'MA5价': tech.get('ma5', today_close),
                'MA10价': tech.get('ma10', today_close),
                'MA20价': tech.get('ma20', today_close),
                'MA60价': tech.get('ma60', today_close),
                '20日高点': tech.get('high_20d', today_close),
                '60日高点': tech.get('high_60d', today_close),
                '距20日高点%': tech.get('dist_to_high20_pct', 0),
                '距60日高点%': tech.get('dist_to_high60_pct', 0),
                '距MA20%': tech.get('dist_to_ma20_pct', 0),
                '距MA60%': tech.get('dist_to_ma60_pct', 0),
                'MA20方向': tech.get('ma20_trend', ''),
                'MA60方向': tech.get('ma60_trend', ''),
                '近5日最高': tech.get('recent5_high', today_close),
                '近5日最低': tech.get('recent5_low', today_close),
                '近10日涨跌%': tech.get('chg_10d_pct', 0),
            }
            ranked_stocks.append(stock_data)
            
        except Exception as e:
            print(f"[整合评分] {ts_code} {name} 失败: {e}")
            continue
    
    # =========================
    # 每个主题只保留失败概率最低的3只个股（防止单主题过度集中）
    # =========================
    theme_groups = {}
    for s in ranked_stocks:
        theme = s['所属主题']
        if theme not in theme_groups:
            theme_groups[theme] = []
        theme_groups[theme].append(s)
    
    filtered_stocks = []
    for theme, stocks in theme_groups.items():
        # 按失败概率升序排序
        sorted_by_failure = sorted(stocks, key=lambda x: x['失败概率'])
        # 每个主题只保留最多3只
        filtered_stocks.extend(sorted_by_failure[:3])
    
    ranked_stocks = filtered_stocks
    
    # 按整合评分排序
    ranked_stocks = sorted(ranked_stocks, key=lambda x: -x['整合评分'])

    lines = []
    lines.append("=" * 60)
    lines.append("🔥 整合评分精选标的 (明日重点关注)")
    lines.append("=" * 60)
    lines.append("【价格约束】本区块所有股票的【技术价位】和【参考位】")
    lines.append("【价格约束】均为基于真实日线数据的精确计算值，")
    lines.append("【价格约束】进行技术面分析时必须严格使用下方标注的")
    lines.append("【价格约束】MA价格、高点价格、现价等数据，")
    lines.append("【价格约束】绝对禁止编造任何价格数字！")
    lines.append("=" * 60)
    
    top_stocks = ranked_stocks[:20]
    for i, s in enumerate(top_stocks, 1):
        lines.append(f"【第{i}名】{s['名称']} ({s['代码']})")
        lines.append(f"  整合评分: {s['整合评分']:.1f} | 失败概率: {s['失败概率']:.1f}%")
        lines.append(f"  今日涨幅: {s['涨跌幅']:.2f}% | 现价: {s['现价']:.2f}元 | 换手率: {s['换手率']:.2f}%")
        lines.append(f"  成交额: {s['成交额']:.2f}亿 | 量能爆发: {s['量能爆发']:.2f}")
        lines.append(f"  所属主题: {s['所属主题']} | 状态: {s['所属状态']}")
        lines.append(f"  推荐理由: {s['推荐理由']}")
        lines.append(f"  ├─趋势强度: {s['趋势强度']:.1f} | 资金健康度: {s['资金健康度']:.1f}")
        lines.append(f"  ├─位置安全: {s['位置安全性']:.1f} | 热度持续: {s['热度持续性']:.1f}")
        lines.append(f"  └─基本面: {s['基本面']:.1f}")
        # 关键技术价格（必须使用这些数据进行技术面分析，禁止编造价格）
        lines.append(f"  【技术价位】MA5={s.get('MA5价', s['现价']):.2f}元 MA10={s.get('MA10价', s['现价']):.2f}元 MA20={s.get('MA20价', s['现价']):.2f}元({s.get('MA20方向','')}) MA60={s.get('MA60价', s['现价']):.2f}元({s.get('MA60方向','')})")
        lines.append(f"  【参考位】20日高点={s.get('20日高点', s['现价']):.2f}元 60日高点={s.get('60日高点', s['现价']):.2f}元 距20高={s.get('距20日高点%', 0):+.1f}% 距60高={s.get('距60日高点%', 0):+.1f}% 近10日涨跌={s.get('近10日涨跌%', 0):+.1f}%")
        if s.get('热榜最佳排名') and 0 < s['热榜最佳排名'] <= 100:
            lines.append(f"  热榜: Top{s['热榜最佳排名']}({s['热榜上榜次数']}次)")
        lines.append("")
    
    #lines.append("完整排名:")
    ##lines.append("-" * 60)
    #for i, s in enumerate(ranked_stocks, 1):
    ##    lines.append(f"{i}. {s['代码']} {s['名称']} | 现价:{s['现价']:.2f} 涨幅:{s['涨跌幅']:.2f}% 成交额:{s['成交额']:.2f}亿 换手:{s['换手率']:.2f}% | 评分:{s['整合评分']:.1f} | 失败概率:{s['失败概率']:.1f}% | {s['所属主题']}")
    #lines.append("=" * 60)
    
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
            
            # 过滤：失败概率高于 50% 的排除
            tracking_stocks = [s for s in tracking_stocks if s.get('failure_prob', 100) <= 50]
            
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
                    lines.append(f"  整合评分: {stock.get('open_score', 0):.1f} | 失败概率: {stock.get('failure_prob', 0):.1f}%")
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


    #return
    prompt = f"""
以下是我自己计算的量化分析结果：
当前市场情绪：

{emotion_text}

今日主题分析情况：

{sector_text_his}
---------------------------------------

主题个股池选股结果（来自 theme_pattern_stock_picker.py）：
（这是根据主题趋势和情绪筛选出的优质个股，包含中期趋势主题和短线主线的龙头和中军）
{theme_stocks_text}
---------------------------------------

整合评分精选量化股票池（综合趋势强度、资金健康度、位置安全性、热度持续性、基本面五个维度评分）：
（这是程序根据整合评分算法筛选的明日重点标的，目标是找到次日介入上涨概率高、失败概率低的股票）
{hot_money_open_text}

---------------------------------------
近20日跟踪分析股票池（从历史自选股中筛选涨幅不大、未大涨过的个股，经主题过滤后按综合评分排序）：
（这些是近期持续关注、尚未启动的股票，值得跟踪分析）
{tracking_stocks_text}

请分析并输出内容：
标题：每日复盘({TRADE_DATE})
内容(分成以下部分)：
1、大盘情绪：简明扼要，重点是仓位建议及理由，操作要点
2、今日主题分析情况:
   【严格按以下固定模板输出，禁止自由发挥格式】

   第一段：用1-2句话概述今日市场风格（基于主题趋势分和情绪分判断核心主线）。

   操作要点：
   - 要点1（锁定核心方向）
   - 要点2（操作风格/市值偏好提示）

   主要关注主题及补涨中军：
   - 主题名1: 股票名1、股票名2（从主题个股池中取，加黑加粗）
   - 主题名2: 股票名3、股票名4、股票名5
   - 主题名3: 股票名6、股票名7
   - 【最多列出8个最强主题，每个主题列出2-3只补涨中军】

   明日主题预测：
   - 明日最看好主题1：简要说明理由（趋势/情绪/稳定性/资金流入状态）
   - 明日次看好主题2：简要说明理由
   - 【最多列出3个明日预测主题】

   【重要约束】：股票名必须从下方"主题个股池选股结果"和"整合评分精选量化股票池"中选取，禁止凭空编造。主题名必须是下方已有的主题，不要自创。
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
           - 所属主题的**状态**（抱团主升/强趋势/震荡/弱势等），不同状态对应不同策略：
                * **抱团主升**：龙头稳定、趋势陡峭、情绪高涨，资金集中，适合持股
                * **强趋势**：趋势分高且持续上升，情绪活跃，适合顺势操作
                * **震荡**：趋势不明显，方向待确认，观望为主
                * **弱势**：趋势下行，回避为主
     - 技术面分析：
       【价格使用强制约束】必须严格使用上方"【技术价位】"和"【参考位】"中标注的EXACT价格数字进行分析！
       【价格验证】在分析前，请先在心中确认：该股票的"现价"、"MA20"、"60日高点"等数字与上方标注的完全一致。
       【禁止项】绝对禁止编造任何价格数字！禁止将MA20=360.52写成MA20=9.58！禁止将20日高点=402.60写成10.23！禁止将现价=338.90写成9.74！
       【允许项】可以在真实价格基础上进行趋势分析、位置判断。目标价可基于真实价位合理外推（如：突破MA20后看20日高点，突破20日高后看60日高），但目标价数字必须与提供的高点价格直接关联。
     - 未来上涨空间预估：基于【技术价位】和【参考位】中的真实价格数据进行测算。如果显示"MA20=360.52元 20日高点=402.60元"，则分析中必须使用这些确切数字，不能写"9.58/10.23"之类的编造值。目标价必须基于实际价位合理外推，且单位必须与现价一致。
     - 给出产业资金定价诊断（ICPM）结果- 生命周期/主线强度/资金状态/决策建议：
     - 买点建议：必须基于"【技术价位】"中提供的真实MA5/MA10/MA20价格给出具体价位，禁止编造价格！如果标注MA20=360.52元，则买点应在360.52元附近或其上下合理区间。
     - 止损点建议：必须基于"【技术价位】"中提供的真实MA价格或支撑位，禁止编造价格！
     - 风险提示：如果主题情绪分持续多天走高，且趋势分也持续走高，说明主题有风险，突出建议勿追高！
     - 如遇个股重大风险，请在分析中标注"【警告】有重大风险"，但仍保留在列表中并说明理由
    其它要求：
    A直接过滤掉有基本面重大风险的个股：
    - 近三个月内有定增预案
    - 有大额减持公告
    - 未来半年有大额解禁压力
    - 有重大诉讼风险
    - 有重大财务风险（如连续亏损、审计异常等）
    - 有其他重大利空消息
    B对于无重大风险的前30名个股，保持原有的综合评分排序，不要重新筛选和排序
    C【最高优先级】所有技术面分析中的价格（MA均线价格、目标价、买点、止损位、支撑位、阻力位、现价、高点等）必须严格使用上方"【技术价位】"和"【参考位】"中提供的EXACT真实数据，禁止凭空编造任何价格数字或百分比！此项约束优先级高于其他所有分析要求。
    D【价格错误检测】分析完成后，请核对：如果某只股票上方标注"现价=XXX元 MA20=YYY元"，而你的分析中写成了不同的价格数字，则你的分析错误，请立即修正。
4、跟踪分析个股：从近20日跟踪分析股票池中，精选5个符合技术形态的个股进行深度分析，重点关注：
    - 显示整合评分和失败概率
    - 分析所属主题和该主题的状态，从网络搜索内容分析个股近期表现的主题驱动因素（尤其是多主题共振）
    - 技术面分析:A洗盘到30日均线或60日均线（必须严格使用上方跟踪股区块标注的"【技术价位】"中的真实MA20/MA60价格！如果标注MA20=360.52元，则分析中必须写MA20=360.52元，不能写成其他数字！），且该均线是向上的趋势，B小阳线温和上涨
    - 未来上涨空间预估（必须严格使用跟踪股区块"【技术价位】"和"【参考位】"中的真实价格数据测算目标位！如果标注20日高点=402.60元，则目标价必须基于402.60元这个数字进行合理外推，不能写成10.23元！）
    - 风险提示（如果有）
    - 临近60日新高或刚刚突破创新高，在前几天震荡调整后放量上涨但没涨停
    【跟踪股最高约束】所有价格分析必须严格使用上方"近20日跟踪分析股票池"区块中标注的"【技术价位】"和"【参考位】"中的EXACT真实数据，禁止编造任何价格数字！若标注现价=338.90元，则分析中必须写338.90元，不能写成9.74元！此项约束优先级最高。
    【跟踪股价格验证】分析完成后，请核对：每只股票的现价、MA20、MA60、高点价格是否与上方跟踪股区块"【技术价位】"和"【参考位】"中标注的完全一致，如有不符则分析错误，请立即修正。
    

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
# YRI-H 历史辨识度评分（过去252个交易日，约1年）
# =========================
def _is_zt_day(pct_chg, ts_code):
    """判断单日是否涨停（主板9.8%+/双创19.8%+）"""
    if pct_chg is None:
        return False
    IS_CYB_KCB = (ts_code.startswith('3') or ts_code.startswith('688') or ts_code.startswith('689'))
    zt_threshold = 19.8 if IS_CYB_KCB else 9.8
    return pct_chg >= zt_threshold


def calc_yri_history(ts_code, debug=False):
    """
    计算历史辨识度指标 YRI-H（满分100，基于过去252个交易日）
    
    返回: dict, 包含各项得分、总分、等级、标签、画像
    """
    ts_code = ts_code.strip()
    
    # 1. 获取历史日线数据（近1年）
    df = get_hist_data(ts_code)
    if df is None or len(df) < 30:
        return {
            "错误": f"历史数据不足（仅{len(df) if df is not None else 0}天）",
            "建议": "请确认股票代码是否正确，或先运行主程序缓存数据"
        }
    
    # 取最近252个交易日
    df = df.sort_values('trade_date').tail(252).reset_index(drop=True)
    n_days = len(df)
    
    # ========== 1. 计算基础指标 ==========
    zt_count = 0  # 涨停次数
    max_consec_zt = 0  # 最大连板
    current_consec = 0
    big_up_days = 0  # 大阳线（+5%以上）天次
    pct_chg_list = []  # 每日涨跌幅
    
    for _, row in df.iterrows():
        pct = row.get('pct_chg', 0)
        pct_chg_list.append(pct)
        
        if _is_zt_day(pct, ts_code):
            zt_count += 1
            current_consec += 1
            max_consec_zt = max(max_consec_zt, current_consec)
        else:
            current_consec = 0
        
        if pct >= 5:  # 大阳线计数（趋势股重要指标）
            big_up_days += 1
    
    # 计算辅助指标
    avg_pct_abs = sum([abs(x) for x in pct_chg_list]) / len(pct_chg_list) if pct_chg_list else 0
    first_close = df['close'].iloc[0]
    last_close = df['close'].iloc[-1]
    stock_return = (last_close / first_close - 1) * 100 if first_close > 0 else 0
    
    # 年化因子（如果实际交易日不足252天，按比例放大阈值分母）
    # 这样短周期数据不会因为观察期短就低估辨识度
    annual_factor = 252 / n_days if n_days > 0 else 1.0
    
    # 年化涨停/大阳次数（用于调整评分阈值）
    zt_count_annual = zt_count * annual_factor
    big_up_days_annual = big_up_days * annual_factor
    
    # 计算近1年最大滚动涨幅（用局部高点法，O(n)复杂度）
    closes = df['close'].values
    max_drawup = 0.0
    min_close_seen = closes[0]
    for c in closes:
        if c < min_close_seen:
            min_close_seen = c
        drawup = (c / min_close_seen - 1) * 100 if min_close_seen > 0 else 0
        max_drawup = max(max_drawup, drawup)
    max_excess = max(max_drawup, stock_return)
    
    # 历史新高检查
    is_new_high = last_close >= df['close'].max() * 0.98
    
    # ========== 2. 涨停基因 G（30分） ==========
    # 趋势股涨停少但大阳线多，组合打分
    # 使用年化涨停次数评估"涨停基因强度"
    G_raw = 0
    if zt_count_annual >= 20:
        G_raw += 30
    elif zt_count_annual >= 15:
        G_raw += 25
    elif zt_count_annual >= 10:
        G_raw += 20
    elif zt_count_annual >= 5:
        G_raw += 15
    elif zt_count_annual >= 2:
        G_raw += 10
    elif zt_count_annual >= 1:
        G_raw += 5
    
    # 大阳线补偿（趋势股没有涨停但有很多5%+阳线时的补偿，最多+25分）
    # 每6天出现一次大阳线给3分
    big_up_bonus = min(int(big_up_days_annual / 6) * 3, 25)
    
    # 趋势股"资金关注"特征：平均日波动>2%也说明有资金在操作
    volatility_bonus = 0
    if avg_pct_abs >= 3.0:
        volatility_bonus = 8
    elif avg_pct_abs >= 2.5:
        volatility_bonus = 6
    elif avg_pct_abs >= 2.0:
        volatility_bonus = 4
    elif avg_pct_abs >= 1.5:
        volatility_bonus = 2
    
    G_score = min(G_raw + big_up_bonus + volatility_bonus, 30)
    
    # ========== 3. 空间记忆 S（25分） ==========
    S_score = 0
    # 连板记忆（15分）
    if max_consec_zt >= 6:
        S_score += 15
    elif max_consec_zt >= 5:
        S_score += 12
    elif max_consec_zt >= 4:
        S_score += 10
    elif max_consec_zt >= 3:
        S_score += 7
    elif max_consec_zt >= 2:
        S_score += 4
    elif max_consec_zt >= 1:
        S_score += 2
    
    # 趋势空间（10分）- 最大涨幅体现"翻倍空间"
    trend_space_score = 0
    if max_excess >= 200:
        trend_space_score = 10
    elif max_excess >= 100:
        trend_space_score = 8
    elif max_excess >= 60:
        trend_space_score = 6
    elif max_excess >= 40:
        trend_space_score = 4
    elif max_excess >= 25:
        trend_space_score = 3
    elif max_excess >= 15:
        trend_space_score = 2
    
    S_score = min(S_score + trend_space_score, 25)
    
    # ========== 4. 历史资金活跃度 A（20分） ==========
    # 4a. 当日真实换手率（从缓存读取）
    today_turnover = get_cached_turnover(ts_code)
    avg_turnover = today_turnover if today_turnover and today_turnover > 0 else 0
    
    # 4b. 若有量数据，vol均值/当日vol的比值估算历史换手
    if 'vol' in df.columns and avg_turnover <= 0:
        vol_1y_avg = df['vol'].mean()
        today_vol = df['vol'].iloc[-1]
        if today_vol > 0:
            if vol_1y_avg / today_vol > 1.5:
                avg_turnover = 5.0
            elif vol_1y_avg / today_vol > 1.0:
                avg_turnover = 3.0
            else:
                avg_turnover = 1.5
    
    # 4c. A评分 - 换手率（12分）
    if avg_turnover >= 15:
        A_turnover = 12
    elif avg_turnover >= 10:
        A_turnover = 11
    elif avg_turnover >= 8:
        A_turnover = 10
    elif avg_turnover >= 6:
        A_turnover = 8
    elif avg_turnover >= 5:
        A_turnover = 7
    elif avg_turnover >= 3:
        A_turnover = 5
    elif avg_turnover >= 2:
        A_turnover = 3
    elif avg_turnover >= 1:
        A_turnover = 2
    else:
        A_turnover = 1
    
    # 4d. 日均成交额百分位（8分）- 用历史数据估算
    avg_amount_1y = 0.0
    if 'amount' in df.columns:
        avg_amount_1y = df['amount'].mean() / 10000
    elif 'vol' in df.columns and 'close' in df.columns:
        avg_amount_1y = (df['vol'] * df['close']).mean() / 10000
    
    # 成交额分级评分
    A_percentile_bonus = 0
    if avg_amount_1y > 50000:  # 50亿+
        A_percentile_bonus = 8
    elif avg_amount_1y > 20000:  # 20亿+
        A_percentile_bonus = 6
    elif avg_amount_1y > 10000:  # 10亿+
        A_percentile_bonus = 5
    elif avg_amount_1y > 5000:  # 5亿+
        A_percentile_bonus = 4
    elif avg_amount_1y > 2000:  # 2亿+
        A_percentile_bonus = 3
    elif avg_amount_1y > 1000:  # 1亿+
        A_percentile_bonus = 2
    else:
        A_percentile_bonus = 1
    
    A_score = min(A_turnover + A_percentile_bonus, 20)
    
    # ========== 5. 股性弹性 E（15分） ==========
    # 用近1年最大涨幅+累计涨幅综合判断
    E_by_max = 0
    if max_excess >= 200:
        E_by_max = 15
    elif max_excess >= 100:
        E_by_max = 12
    elif max_excess >= 60:
        E_by_max = 10
    elif max_excess >= 40:
        E_by_max = 8
    elif max_excess >= 30:
        E_by_max = 6
    elif max_excess >= 20:
        E_by_max = 4
    elif max_excess >= 10:
        E_by_max = 2
    else:
        E_by_max = 1
    
    # 累计涨幅加分（趋势股累计涨幅大）
    E_by_return = 0
    if stock_return >= 100:
        E_by_return = 5
    elif stock_return >= 50:
        E_by_return = 3
    elif stock_return >= 20:
        E_by_return = 2
    elif stock_return >= 0:
        E_by_return = 1
    
    E_score = min(E_by_max + E_by_return, 15)
    
    # ========== 6. 关注度持续性 C（10分） ==========
    # 6a. 热榜天数
    hot_days = 0
    try:
        if os.path.exists(DC_HOT_CACHE_DIR):
            files = sorted([f for f in os.listdir(DC_HOT_CACHE_DIR) if f.startswith('dc_hot_')])
            for f in files:
                try:
                    fpath = os.path.join(DC_HOT_CACHE_DIR, f)
                    hdf = pd.read_csv(fpath)
                    if '代码' in hdf.columns:
                        if ts_code in hdf['代码'].head(50).values:
                            hot_days += 1
                    elif 'ts_code' in hdf.columns:
                        if ts_code in hdf['ts_code'].head(50).values:
                            hot_days += 1
                except:
                    continue
    except:
        pass
    
    # 6b. 市场波动关注度 proxy - 平均日波动大说明市场反复关注
    market_attention_score = 0
    if avg_pct_abs >= 3.5:
        market_attention_score = 10
    elif avg_pct_abs >= 2.8:
        market_attention_score = 8
    elif avg_pct_abs >= 2.2:
        market_attention_score = 6
    elif avg_pct_abs >= 1.8:
        market_attention_score = 4
    elif avg_pct_abs >= 1.4:
        market_attention_score = 3
    else:
        market_attention_score = 2
    
    # 6c. 热榜天数评分
    hot_score = 0
    if hot_days > 50:
        hot_score = 10
    elif hot_days >= 20:
        hot_score = 8
    elif hot_days >= 10:
        hot_score = 6
    elif hot_days >= 5:
        hot_score = 4
    elif hot_days >= 2:
        hot_score = 2
    else:
        hot_score = 1
    
    C_score = max(hot_score, market_attention_score)
    
    # ========== 7. 总分计算 ==========
    total_score = G_score + S_score + A_score + E_score + C_score
    total_score = round(total_score, 1)
    
    # 等级判定
    if total_score >= 80:
        level = "历史大妖/板块核心中军"
    elif total_score >= 65:
        level = "强股性活跃标的/趋势龙头"
    elif total_score >= 50:
        level = "中等辨识度"
    elif total_score >= 30:
        level = "股性一般/非核心"
    else:
        level = "历史冷门/低辨识度"
    
    # 核心历史标签
    tags = []
    if zt_count >= 10:
        tags.append("涨停常客")
    if max_consec_zt >= 3:
        tags.append("连板记忆")
    if big_up_days >= 30:
        tags.append("大阳趋势")
    if avg_turnover >= 5:
        tags.append("高换手")
    elif avg_turnover >= 3:
        tags.append("中高换手")
    if avg_amount_1y > 10000:
        tags.append("资金深度参与")
    if max_excess >= 100:
        tags.append("高弹性")
    elif max_excess >= 50:
        tags.append("中高弹性")
    if hot_days >= 10 or market_attention_score >= 4:
        tags.append("市场关注")
    if stock_return >= 50:
        tags.append("趋势上涨")
    
    if not tags:
        tags = ["低辨识度"]
    
    # 股性画像
    if total_score >= 80:
        portrait = "板块核心中军/历史级大妖，资金深度参与，股性极度活跃，具有强烈市场记忆点"
    elif total_score >= 65:
        portrait = "趋势龙头/板块活跃股，资金关注度高，具备良好波段和短线价值"
    elif total_score >= 50:
        portrait = "股性中等，有一定表现，主题共振时可参与"
    elif total_score >= 30:
        portrait = "股性一般，非核心标的，适合有明确主题催化时谨慎参与"
    else:
        portrait = "冷门股/低辨识度，缺乏资金关注，非主线行情不建议参与"
    
    result = {
        "股票代码": ts_code,
        "分析天数": n_days,
        "G_涨停基因": {
            "涨停次数": zt_count,
            "大阳线天次(+5%)": big_up_days,
            "涨停得分": G_raw,
            "大阳补偿": big_up_bonus,
            "得分": G_score
        },
        "S_空间记忆": {
            "最大连板": max_consec_zt,
            "最大涨幅(%)": round(max_excess, 1),
            "连板得分": S_score - trend_space_score,
            "趋势空间得分": trend_space_score,
            "得分": S_score
        },
        "A_资金活跃度": {
            "日均换手率(%)": round(avg_turnover, 2),
            "日均成交额(万元)": round(avg_amount_1y, 0),
            "换手得分": A_turnover,
            "成交额百分位": A_percentile_bonus,
            "得分": A_score
        },
        "E_股性弹性": {
            "近1年最大涨幅(%)": round(max_excess, 1),
            "近1年累计涨幅(%)": round(stock_return, 1),
            "得分": E_score
        },
        "C_关注度持续性": {
            "热榜天数": hot_days,
            "平均日波动(%)": round(avg_pct_abs, 2),
            "得分": C_score
        },
        "YRI历史总分": total_score,
        "等级": level,
        "核心历史标签": tags,
        "股性画像": portrait
    }
    
    if debug:
        print(f"\n{'='*60}")
        print(f"  YRI-H 历史辨识度评分 - {ts_code}")
        print(f"{'='*60}")
        print(f"  样本: {n_days}天  涨停{zt_count}次  大阳{big_up_days}次  连板{max_consec_zt}天")
        print(f"  G 涨停基因(30): {G_score}  (涨停{G_raw} + 大阳{big_up_bonus})")
        print(f"  S 空间记忆(25):  {S_score}  (连板{S_score - trend_space_score} + 趋势空间{trend_space_score})")
        print(f"  A 资金活跃(20):  {A_score}  (换手{A_turnover} + 成交{A_percentile_bonus})")
        print(f"  E 股性弹性(15):  {E_score}  (最大涨幅{max_excess:.0f}% + 累计{stock_return:.0f}%)")
        print(f"  C 关注度(10):   {C_score}  (热榜{hot_days}天/波动{avg_pct_abs:.1f}%)")
        print(f"  {'-'*60}")
        print(f"  YRI-H 总分: {total_score}  →【{level}】")
        print(f"  标签: {', '.join(tags)}")
        print(f"{'='*60}\n")
    
    return result


def yri_batch_analysis(codes):
    """批量分析多只股票YRI-H并排序"""
    results = []
    for code in codes:
        r = calc_yri_history(code, debug=False)
        if isinstance(r, dict) and "错误" not in r:
            results.append({
                "代码": r["股票代码"],
                "YRI总分": r["YRI历史总分"],
                "等级": r["等级"],
                "涨停": r["G_涨停基因"]["涨停次数"],
                "大阳+5%": r["G_涨停基因"]["大阳线天次(+5%)"],
                "最大连板": r["S_空间记忆"]["最大连板"],
                "最大涨幅%": r["S_空间记忆"]["最大涨幅(%)"],
                "日均换手%": r["A_资金活跃度"]["日均换手率(%)"],
                "日均成交万": r["A_资金活跃度"]["日均成交额(万元)"],
                "标签": ", ".join(r["核心历史标签"])
            })
    
    if results:
        results_df = pd.DataFrame(results).sort_values("YRI总分", ascending=False).reset_index(drop=True)
        results_df.index = results_df.index + 1
        pd.set_option('display.width', 200)
        print(f"\n{'='*120}")
        print(f"  YRI-H 批量历史辨识度评分（共{len(results)}只，按总分排序）")
        print(f"{'='*120}")
        print(results_df.to_string(index=True))
        print(f"{'='*120}\n")
        return results_df
    return None


# =========================
# 启动
# =========================
if __name__ == "__main__":
    import argparse
    import json
    
    parser = argparse.ArgumentParser(description='A股量化选股分析系统')
    parser.add_argument('-d', '--date', type=str, default=None,
                        help='指定目标日期，格式: YYYYMMDD（如: 20260601）')
    parser.add_argument('--no-send', action='store_true',
                        help='不发送微信消息')
    parser.add_argument('--simple', action='store_true',
                        help='简易模式，只输出个股和评分，不进行AI分析、不发送微信')
    parser.add_argument('--yri', type=str, default=None,
                        help='历史辨识度分析，输入股票代码(如 002426.SZ 或 002426)，多只用逗号分隔')
    parser.add_argument('--yri-json', type=str, default=None,
                        help='同--yri，但仅输出JSON格式结果（便于程序调用）')
    
    args = parser.parse_args()
    
    # --- YRI-H 历史辨识度分析（独立模式） ---
    if args.yri_json:
        codes = [c.strip() for c in args.yri_json.split(',') if c.strip()]
        results = {}
        for code in codes:
            # 自动补全 .SZ/.SH
            if '.' not in code:
                if code.startswith(('6', '9')):
                    code_full = code + '.SH'
                else:
                    code_full = code + '.SZ'
            else:
                code_full = code
            r = calc_yri_history(code_full, debug=False)
            results[code] = r
        print(json.dumps(results, ensure_ascii=False, indent=2))
        exit(0)
    
    if args.yri:
        codes = [c.strip() for c in args.yri.split(',') if c.strip()]
        # 自动补全 .SZ/.SH
        full_codes = []
        for code in codes:
            if '.' not in code:
                if code.startswith(('6', '9')):
                    full_codes.append(code + '.SH')
                else:
                    full_codes.append(code + '.SZ')
            else:
                full_codes.append(code)
        
        if len(full_codes) == 1:
            result = calc_yri_history(full_codes[0], debug=True)
            if "错误" in result:
                print(f"\n❌ {result['错误']} - {result['建议']}")
            else:
                print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            yri_batch_analysis(full_codes)
        exit(0)
    
    # 运行
    run(target_date=args.date, simple_mode=args.simple)


