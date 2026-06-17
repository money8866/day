#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主题趋势 + 情绪 评分（自建"行业最强"算法）
- 复用 theme_portfolio_strategy_cached_dc.py 的成份股匹配逻辑
  （_in_industry_list / _strip_ii / dc_index / dc_member / stock_basic）
- 拉取成份股近 60 个交易日的日线，计算多维度指标
- 输出 trend_score / sentiment_score / composite_score 排名

评分思路（自建）：

【趋势分 TrendScore 0-100】
  1) 多周期收益（5/10/20日 加权）
  2) 均线多头排列占比（站上 MA5/MA10/MA20）
  3) 趋势斜率（10日线性回归斜率）
  4) 趋势加速度（5日 - 10日）
  5) 龙头强度（板块内 Top3 个股平均涨幅）
  6) 抗跌性（10日最大回撤倒数）

【情绪分 SentimentScore 0-100】
  1) 上涨家数占比（breadth）
  2) 涨停占比（>=9.5%）
  3) 强势股占比（>=5%）
  4) 量能放大（5日均量 / 20日均量）
  5) 换手率活跃度
  6) 赚钱效应（中位数涨幅 + 0.5*均值涨幅）
  7) 相对市场强度（板块均值 - 沪深300 均值）
  8) 主线共振（领涨股 + 涨停股同时存在）

【综合分 Composite = 0.55 * Trend + 0.45 * Sentiment】
"""
import os
import sys
import json
import time
import sqlite3
import warnings
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from dotenv import load_dotenv

warnings.filterwarnings("ignore")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")
os.makedirs(CACHE_DIR, exist_ok=True)

# 导入 tushare_quant 模块用于下载K线数据
try:
    import tushare_quant as tq
    TQ_AVAILABLE = True
except ImportError:
    TQ_AVAILABLE = False
    print("[Warning] tushare_quant 模块未找到，将使用内置方法")

# SQLite 缓存数据库配置
DB_PATH = os.path.join(CACHE_DIR, 'cache.db')

def init_db():
    """初始化SQLite数据库"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cache_data (
            key TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            expire_time INTEGER,
            created_at INTEGER
        )
    ''')
    conn.commit()
    conn.close()

# 初始化数据库
init_db()

REPORT_DIR = os.path.join(os.path.dirname(BASE_DIR), "report_daily")
os.makedirs(REPORT_DIR, exist_ok=True)

# 终极方案：patch os.path.expanduser，不让 tushare 访问用户根目录
# 用 sentinel 标记确保 reload 时 original_expanduser 仍指向真实的 expanduser
if not hasattr(os, '_original_expanduser'):
    os._original_expanduser = os.path.expanduser
original_expanduser = os._original_expanduser

def safe_expanduser(path):
    if '~/tk.csv' in path or '\\tk.csv' in path or 'tk.csv' in path:
        return os.path.join(CACHE_DIR, 'tk.csv')
    return original_expanduser(path)

os.path.expanduser = safe_expanduser

import tushare as ts

DOTENV_PATH = "d:/mystock/config/.env"
load_dotenv(DOTENV_PATH)

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
pro = ts.pro_api(TUSHARE_TOKEN) if TUSHARE_TOKEN else None

OUTPUT_CSV = os.path.join(CACHE_DIR, "theme_trend_sentiment.csv")
OUTPUT_DB = os.path.join(CACHE_DIR, "theme_trend_sentiment.db")

N_DAYS = 60
TOP_N_PER_THEME = 30
MIN_STOCKS = 3

# ==================== DC热榜数据获取 ====================

# 热榜缓存目录
DC_HOT_CACHE_DIR = os.path.join(CACHE_DIR, "dc_hot")
os.makedirs(DC_HOT_CACHE_DIR, exist_ok=True)


def get_prev_trade_date(trade_date=None):
    """获取前一个交易日"""
    if trade_date is None:
        trade_date = TRADE_DATE
    
    if pro is None:
        # 简单处理：往前推1-3天
        dt = datetime.strptime(trade_date, "%Y%m%d")
        for i in range(1, 4):
            prev_dt = dt - timedelta(days=i)
            if prev_dt.weekday() < 5:  # 非周末
                return prev_dt.strftime("%Y%m%d")
        return (dt - timedelta(days=1)).strftime("%Y%m%d")
    
    # 从交易日历获取
    cal = pro.trade_cal(exchange='', start_date='20200101', end_date=trade_date)
    cal = cal[cal['is_open'] == 1]
    cal = cal[cal['cal_date'] < trade_date].sort_values('cal_date', ascending=False)
    if not cal.empty:
        return str(cal.iloc[0]['cal_date'])
    return None


def get_dc_hot(trade_date=None, force_refresh=False):
    """获取东方财富热榜数据（A股市场人气榜）
    
    缓存策略：
    1. 每日数据保存为CSV文件，按日期永久保存
    2. 单次最大2000条，循环获取全部数据
    3. 如果CSV文件存在则直接读取，无需重复下载
    
    Args:
        trade_date: 交易日期，默认为当前交易日
        force_refresh: 是否强制刷新
    
    Returns:
        DataFrame 包含 ts_code, ts_name, hot_rank, hot_value, pct_change 等
    """
    if trade_date is None:
        trade_date = TRADE_DATE
    
    # CSV文件路径
    csv_path = os.path.join(DC_HOT_CACHE_DIR, f"dc_hot_{trade_date}.csv")
    
    # 检查CSV缓存
    if os.path.exists(csv_path) and not force_refresh:
        try:
            df = pd.read_csv(csv_path)
            if not df.empty:
                print(f"[DC_HOT] CSV缓存命中: {trade_date}, {len(df)} 条")
                return df
        except Exception as e:
            print(f"[DC_HOT] 读取CSV失败: {e}")
    
    if pro is None:
        print("[DC_HOT] 缺少 Tushare token，无法获取热榜")
        return pd.DataFrame()
    
    print(f"[DC_HOT] 拉取热榜数据: {trade_date}")
    
    all_data = []
    offset = 0
    limit = 2000  # 单次最大2000条
    max_iterations = 10  # 最多循环10次
    
    try:
        for iteration in range(max_iterations):
            # 获取A股市场人气榜
            df = pro.dc_hot(
                trade_date=trade_date,
                market="A股市场",
                hot_type="人气榜",
                is_new="Y",  # 最新数据
                limit=limit,
                offset=offset,
                fields="ts_code,ts_name,rank,hot,pct_change,current_price"
            )
            time.sleep(0.15)
            
            if df is None or df.empty:
                print(f"[DC_HOT] 第{iteration+1}次拉取无数据，停止")
                break
            
            all_data.append(df)
            print(f"[DC_HOT] 第{iteration+1}次拉取: {len(df)} 条, offset={offset}")
            
            # 如果返回数据少于limit，说明已经获取完毕
            if len(df) < limit:
                break
            
            offset += limit
        
        if not all_data:
            return pd.DataFrame()
        
        # 合并所有数据
        df = pd.concat(all_data, ignore_index=True)
        
        # 去重（按ts_code保留排名最高的）
        if 'rank' in df.columns:
            df = df.sort_values('rank').drop_duplicates(subset=['ts_code'], keep='first')
        
        # 重命名列
        df = df.rename(columns={
            "rank": "hot_rank",
            "hot": "hot_value",
            "pct_change": "pct_chg"
        })
        
        # 确保 ts_code 格式一致
        df['ts_code'] = df['ts_code'].astype(str)
        df['trade_date'] = trade_date
        
        # 保存为CSV（永久保存）
        df.to_csv(csv_path, index=False, encoding='utf-8-sig')
        print(f"[DC_HOT] 拉取完成: 共{len(df)} 条，已保存到 {csv_path}")
        
        return df
        
    except Exception as e:
        print(f"[DC_HOT] 拉取失败: {e}")
    
    return pd.DataFrame()


def get_dc_hot_multi_days(days=2, force_refresh=False):
    """获取多个交易日的热榜数据
    
    策略：获取交易日和前交易日的数据，如果前交易日有缓存就无需再下载
    
    Args:
        days: 获取最近几天的数据，默认2天
        force_refresh: 是否强制刷新
    
    Returns:
        dict: {trade_date: DataFrame}
    """
    result = {}
    current_date = TRADE_DATE
    
    for i in range(days):
        if i == 0:
            trade_date = current_date
        else:
            trade_date = get_prev_trade_date(current_date)
            if trade_date is None:
                break
            current_date = trade_date  # 更新为前一日，继续往前找
        
        # 检查CSV是否存在
        csv_path = os.path.join(DC_HOT_CACHE_DIR, f"dc_hot_{trade_date}.csv")
        
        if os.path.exists(csv_path) and not force_refresh:
            print(f"[DC_HOT] {trade_date} 已有缓存，跳过下载")
            df = pd.read_csv(csv_path)
        else:
            df = get_dc_hot(trade_date, force_refresh=force_refresh)
        
        if not df.empty:
            result[trade_date] = df
    
    return result


# 全局热榜数据（按需加载）
_dc_hot_df = None
_dc_hot_date = None

def load_dc_hot():
    """加载热榜数据（延迟加载，自动获取最近7天数据，支持回退到最近有数据的日期）"""
    global _dc_hot_df, _dc_hot_date
    if _dc_hot_df is None or _dc_hot_date != TRADE_DATE:
        # 自动获取最近7天的数据（覆盖周末+节假日）
        multi_data = get_dc_hot_multi_days(days=7)
        
        # 优先用当天数据，如果当天没有则回退到最近有数据的交易日
        _dc_hot_df = multi_data.get(TRADE_DATE, pd.DataFrame())
        if _dc_hot_df.empty:
            # 按日期降序排序，找第一个非空数据
            sorted_dates = sorted(multi_data.keys(), reverse=True)
            for fallback_date in sorted_dates:
                df = multi_data[fallback_date]
                if not df.empty:
                    _dc_hot_df = df
                    print(f"[DC_HOT] ⚠ 当天({TRADE_DATE})热榜未更新，回退到 {fallback_date} 的数据 ({len(df)} 条)")
                    break
            if _dc_hot_df.empty:
                print(f"[DC_HOT] ⚠ 最近7天均无热榜数据，热度评分暂不可用")
        else:
            print(f"[DC_HOT] 使用当天数据: {TRADE_DATE}, {len(_dc_hot_df)} 条")
        
        _dc_hot_date = TRADE_DATE
    return _dc_hot_df


def get_stock_hot_rank(ts_code):
    """获取个股的热榜排名（排名越高分数越高）
    
    注意：热榜只提供前100个股的热度，因此热榜分作为加分项
    
    排名1-10: +10分
    排名11-30: +8分
    排名31-50: +6分
    排名51-70: +4分
    排名71-100: +2分
    未上榜: +0分
    """
    hot_df = load_dc_hot()
    if hot_df is None or hot_df.empty:
        return 0  # 默认0分（加分项）
    
    match = hot_df[hot_df['ts_code'] == ts_code]
    if match.empty:
        return 0  # 未上榜，不加分
    
    rank = match.iloc[0]['hot_rank']
    if pd.isna(rank):
        return 0
    
    rank = int(rank)
    if rank <= 10:
        return 10
    elif rank <= 30:
        return 8
    elif rank <= 50:
        return 6
    elif rank <= 70:
        return 4
    elif rank <= 100:
        return 2
    else:
        return 0


def get_stock_hot_rank_position(ts_code):
    """获取个股的热榜排名位置（返回实际排名数字，未上榜返回极大值）"""
    hot_df = load_dc_hot()
    if hot_df is None or hot_df.empty:
        return 9999  # 未上榜返回极大值
    
    match = hot_df[hot_df['ts_code'] == ts_code]
    if match.empty:
        return 9999  # 未上榜
    
    rank = match.iloc[0]['hot_rank']
    if pd.isna(rank):
        return 9999
    
    return int(rank)


def calc_theme_hot_score(stock_feats):
    """计算主题综合热度得分（基于热榜数据）
    
    公式：S_theme = Σ(wi * 1/ln(1+Ri))
    
    参数：
        stock_feats: 主题成分股特征列表，每个元素包含 'ts_code', 'mcap'（市值）等字段
    
    返回：
        hot_score: 主题综合热度得分
        detail: 详细信息（包含成分股热榜排名、是否有龙头进入Top10等）
    """
    if not stock_feats:
        return 0.0, {}
    
    import math
    
    total_score = 0.0
    total_weight = 0.0
    hot_ranks = []
    top10_count = 0
    top5_count = 0
    
    for feat in stock_feats:
        ts_code = feat.get('ts_code', '')
        if not ts_code:
            continue
        
        # 获取热榜排名
        rank = get_stock_hot_rank_position(ts_code)
        if rank == 9999:
            continue  # 未上榜不参与计算
        
        hot_ranks.append(rank)
        
        # 统计Top10和Top5数量
        if rank <= 5:
            top5_count += 1
            top10_count += 1
        elif rank <= 10:
            top10_count += 1
        
        # 计算权重（使用市值权重，如果没有市值则使用等权重）
        mcap = feat.get('mcap', 1)
        if mcap <= 0:
            mcap = 1
        
        weight = mcap
        
        # 计算得分：wi * 1/ln(1+Ri)
        if rank > 0:
            score_i = weight / math.log(1 + rank)
            total_score += score_i
            total_weight += weight
    
    # 归一化得分
    if total_weight > 0:
        normalized_score = (total_score / total_weight) * 100
    else:
        normalized_score = 0.0
    
    # 计算热度集中度（前10名占比）
    total_stocks = len([f for f in stock_feats if f.get('ts_code')])
    hot_concentration = top10_count / total_stocks if total_stocks > 0 else 0.0
    
    detail = {
        'hot_score': round(normalized_score, 2),
        'top5_count': top5_count,
        'top10_count': top10_count,
        'hot_concentration': round(hot_concentration * 100, 1),
        'n_participate': len(hot_ranks),
        'avg_rank': round(sum(hot_ranks) / len(hot_ranks), 1) if hot_ranks else 0,
        'min_rank': min(hot_ranks) if hot_ranks else 0
    }
    
    return normalized_score, detail


def get_theme_hot_score_percentile(theme_name, current_score, days=60):
    """获取主题热度得分的历史分位数
    
    参数：
        theme_name: 主题名称
        current_score: 当前得分
        days: 统计天数
    
    返回：
        percentile: 历史分位数（0-100）
        historical_scores: 历史得分列表
    """
    historical_scores = []
    
    # 从数据库获取历史数据
    db_path = os.path.join(BASE_DIR, 'cache_backbone_tushare', 'theme_trend_sentiment.db')
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # 获取最近days天的交易日期
            cursor.execute("""
                SELECT DISTINCT trade_date FROM theme_scores 
                ORDER BY trade_date DESC LIMIT ?
            """, (days,))
            dates = [row[0] for row in cursor.fetchall()]
            
            if dates:
                # 先检查表中是否有 hot_score 列
                cursor.execute("PRAGMA table_info(theme_scores)")
                columns = [row[1] for row in cursor.fetchall()]
                
                if 'hot_score' in columns:
                    placeholders = ",".join("?" * len(dates))
                    cursor.execute(f"""
                        SELECT hot_score FROM theme_scores 
                        WHERE theme = ? AND trade_date IN ({placeholders})
                    """, (theme_name,) + tuple(dates))
                    
                    for row in cursor.fetchall():
                        if row[0] is not None:
                            historical_scores.append(float(row[0]))
            
            conn.close()
        except Exception as e:
            print(f"获取历史热度得分失败: {e}")
    
    # 计算分位数
    if not historical_scores:
        return 50.0, []
    
    historical_scores.sort()
    n = len(historical_scores)
    
    # 找到当前得分的位置
    count_below = sum(1 for s in historical_scores if s < current_score)
    count_equal = sum(1 for s in historical_scores if s == current_score)
    
    if n == 0:
        percentile = 50.0
    else:
        percentile = (count_below + count_equal * 0.5) / n * 100
    
    return round(percentile, 1), historical_scores


def judge_hot_phase(hot_score, percentile, top10_count, top5_count, total_stocks):
    """判断主题热度阶段
    
    参数：
        hot_score: 主题热度得分
        percentile: 历史分位数
        top10_count: 进入热榜Top10的成分股数量
        top5_count: 进入热榜Top5的成分股数量
        total_stocks: 成分股总数
    
    返回：
        phase: 阶段标签（潜伏/升温/高潮/拥挤）
        warning: 预警信息
    """
    phase = "正常"
    warning = ""
    
    # 高潮期判断：龙头霸占Top5且热度达到历史95%以上
    if top5_count >= 2 and percentile >= 95:
        phase = "⚠️ 拥挤"
        warning = "拥挤预警：多只龙头霸占热榜Top5，且热度达历史高位，建议减仓"
    elif top10_count >= 3 and percentile >= 90:
        phase = "🔥 高潮"
        warning = "高潮提示：多只成分股进入热榜Top10，情绪高涨"
    elif hot_score > 0 and top10_count == 0 and percentile < 70:
        phase = "🌱 潜伏"
        warning = "潜伏信号：主题热度开始抬升，但龙头尚未进入热榜Top10，或为最佳入场时机"
    elif hot_score > 0 and percentile >= 70 and percentile < 90:
        phase = "📈 升温"
        warning = "升温阶段：主题热度持续上升中"
    
    return phase, warning


def _strip_ii(name):
    if not isinstance(name, str) or not name:
        return ""
    for suf in ("Ⅱ",):
        if name.endswith(suf):
            return name[: -len(suf)]
    return name


def _in_industry_list(name, industry_list):
    if not isinstance(name, str) or not name:
        return False
    stripped = _strip_ii(name)
    for ind in industry_list:
        if isinstance(ind, str) and _strip_ii(ind) == stripped:
            return True
    return False


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

TRADE_DATE = get_last_trade_date()
START_DATE = (datetime.strptime(TRADE_DATE, "%Y%m%d") - timedelta(days=N_DAYS + 30)).strftime("%Y%m%d")
print(f"[Init] 交易日: {TRADE_DATE}  K线区间: {START_DATE} ~ {TRADE_DATE}")


def cache_get(name, **kwargs):
    key = "_".join([name] + [f"{k}_{v}" for k, v in sorted(kwargs.items())])
    safe = key.replace("/", "_").replace(":", "_")
    cache_key = f"tsc_{safe}_{TRADE_DATE}"
    
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT data, expire_time FROM cache_data WHERE key = ?', (cache_key,))
        row = cursor.fetchone()
        if row:
            data_str, expire_time = row
            # 检查是否过期（0 或 None 表示永不过期）
            if expire_time and expire_time > 0 and int(time.time()) > expire_time:
                # 已过期，删除
                cursor.execute('DELETE FROM cache_data WHERE key = ?', (cache_key,))
                conn.commit()
                return None
            # 返回缓存数据
            from io import StringIO
            return pd.read_csv(StringIO(data_str))
    except Exception as e:
        print(f"[Cache] get error: {e}")
    finally:
        conn.close()
    return None


def cache_set(name, data, expire_hours=None, **kwargs):
    key = "_".join([name] + [f"{k}_{v}" for k, v in sorted(kwargs.items())])
    safe = key.replace("/", "_").replace(":", "_")
    cache_key = f"tsc_{safe}_{TRADE_DATE}"
    
    # 默认永不过期（expire_hours 为 None 或 <=0 时）
    if expire_hours and expire_hours > 0:
        expire_time = int(time.time()) + expire_hours * 3600
    else:
        expire_time = 0  # 0 表示永不过期
    created_at = int(time.time())
    
    # 将DataFrame转为字符串
    from io import StringIO
    buffer = StringIO()
    data.to_csv(buffer, index=False)
    data_str = buffer.getvalue()
    
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO cache_data (key, data, expire_time, created_at)
            VALUES (?, ?, ?, ?)
        ''', (cache_key, data_str, expire_time, created_at))
        conn.commit()
    except Exception as e:
        print(f"[Cache] set error: {e}")
    finally:
        conn.close()


def load_theme_json():
    path = os.path.join(BASE_DIR, "theme.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"未找到 {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("HOT_THEMES", {})


def get_dc_members():
    cached = cache_get("dc_all_members")
    if cached is not None:
        # 检查缓存是否有 is_industry 列，没有则说明是旧缓存，需重新拉取
        if "is_industry" in cached.columns:
            #print(f"[DC] 缓存命中: {len(cached)} 条成份股记录")
            return cached
        else:
            print("[DC] 缓存缺 is_industry 列，重新拉取")

    if pro is None:
        print("[DC] 缺少 Tushare token，无法拉取东财板块")
        return pd.DataFrame()

    print("[DC] 调用 Tushare dc_index / dc_member 拉取板块成份股...")
    concept_df = pro.dc_index(trade_date=TRADE_DATE, idx_type="概念板块")
    time.sleep(0.15)
    industry_df = pro.dc_index(trade_date=TRADE_DATE, idx_type="行业板块")
    time.sleep(0.15)
    
    # 建立 board code -> 是否行业板块 的映射（替代靠名字猜测）
    industry_board_codes = set(industry_df["ts_code"].tolist())
    
    boards = pd.concat([concept_df[["ts_code", "name"]], industry_df[["ts_code", "name"]]], ignore_index=True)
    name_map = dict(zip(boards["ts_code"], boards["name"]))
    codes = boards["ts_code"].tolist()

    all_members = []
    fetched_codes = set()
    total = len(codes)
    for i, code in enumerate(codes):
        try:
            m = pro.dc_member(trade_date=TRADE_DATE, ts_code=code)
            if m is not None and not m.empty:
                m["concept_name"] = m["ts_code"].map(name_map)
                m["is_industry"] = code in industry_board_codes
                m = m.dropna(subset=["concept_name"])
                all_members.append(m)
                fetched_codes.add(code)
            if (i + 1) % 100 == 0:
                print(f"[DC] 进度: {i+1}/{total}")
            time.sleep(0.15)
        except Exception as e:
            pass

    # 找出漏掉的板块（异常 + 空返回），重试
    missing_codes = [c for c in codes if c not in fetched_codes]
    if missing_codes:
        print(f"[DC] {len(missing_codes)} 个板块未拉到，重试中...")
        time.sleep(1)
        for code in missing_codes[:]:
            try:
                m = pro.dc_member(trade_date=TRADE_DATE, ts_code=code)
                if m is not None and not m.empty:
                    m["concept_name"] = m["ts_code"].map(name_map)
                    m["is_industry"] = code in industry_board_codes
                    m = m.dropna(subset=["concept_name"])
                    all_members.append(m)
                    fetched_codes.add(code)
                time.sleep(0.15)
            except Exception:
                pass
        still_missing = [c for c in codes if c not in fetched_codes]
        if still_missing:
            print(f"[DC] 仍有 {len(still_missing)} 个板块无法拉取: {[name_map.get(c, c) for c in still_missing[:5]]}...")

    if not all_members:
        return pd.DataFrame()
    df = pd.concat(all_members, ignore_index=True).drop_duplicates(subset=["con_code", "concept_name"])
    cache_set("dc_all_members", df)
    print(f"[DC] 拉取完成: {len(df)} 条")
    return df


def get_stock_basic():
    cached = cache_get("stock_basic")
    if cached is not None:
        return cached
    if pro is None:
        return pd.DataFrame()
    df = pro.stock_basic(exchange="", list_status="L", fields="ts_code,symbol,name,industry,list_date")
    time.sleep(0.15)
    cache_set("stock_basic", df)
    return df


def get_daily_basic(trade_date=None):
    if trade_date is None:
        trade_date = TRADE_DATE
    cached = cache_get("daily_basic", trade_date=trade_date)
    if cached is not None:
        return cached
    if pro is None:
        return pd.DataFrame()
    df = pro.daily_basic(trade_date=trade_date, fields="ts_code,total_mv,circ_mv,turnover_rate,pe,pb")
    time.sleep(0.15)
    cache_set("daily_basic", df, trade_date=trade_date)
    return df


def _add_ma_columns(df):
    """为K线数据添加MA5/MA10/MA20均线列（原地修改并返回）"""
    if df is None or df.empty:
        return df
    df = df.sort_values('trade_date').copy()
    if 'ma5' not in df.columns:
        df['ma5'] = df['close'].rolling(5).mean().bfill()
    if 'ma10' not in df.columns:
        df['ma10'] = df['close'].rolling(10).mean().bfill()
    if 'ma20' not in df.columns:
        df['ma20'] = df['close'].rolling(20).mean().bfill()
    if 'ma60' not in df.columns and len(df) >= 60:
        df['ma60'] = df['close'].rolling(60).mean().bfill()
    return df


def get_daily_kline(ts_codes, start, end):
    if pro is None or not ts_codes:
        return pd.DataFrame()
    
    all_parts = []
    need_fetch_codes = []
    
    # 定义本地缓存目录
    LOCAL_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache_daily")
    
    # 先尝试从本地CSV缓存读取（优先级最高）
    for code in ts_codes:
        csv_path = os.path.join(LOCAL_CACHE_DIR, f"{code}.csv")
        if os.path.exists(csv_path):
            try:
                df = pd.read_csv(csv_path)
                if not df.empty:
                    # 将 trade_date 转换为字符串类型，避免类型比较错误
                    df['trade_date'] = df['trade_date'].astype(str)
                    # 过滤日期范围
                    df = df[(df['trade_date'] >= start) & (df['trade_date'] <= end)].copy()
                    if not df.empty:
                        # 添加均线列
                        df = _add_ma_columns(df)
                        all_parts.append(df)
                        continue
            except Exception as e:
                print(f"[KLine] 读取CSV失败 {csv_path}: {e}")
        
        # 再尝试从SQLite缓存读取
        cache_key = f"daily_kline_{code}_{start}_{end}"
        cached = cache_get(cache_key)
        if cached is not None:
            # 检查缓存数据是否有均线列，没有则补充并更新缓存
            if 'ma5' not in cached.columns:
                cached = _add_ma_columns(cached)
                cache_set(cache_key, cached)  # 更新缓存（写入带均线的版本）
            all_parts.append(cached)
        else:
            # 跳过已确认无法获取的股票（黑名单），避免重复请求
            if _is_failed_stock(code):
                continue
            need_fetch_codes.append(code)
    
    # 需要拉取的股票：先尝试用 tushare_quant 生成CSV缓存
    if need_fetch_codes and TQ_AVAILABLE:
        print(f"[KLine] 使用 tushare_quant 批量预取 {len(need_fetch_codes)} 只股票数据")
        try:
            tq.batch_prefetch_hist_data(need_fetch_codes, start_date=start)
            # 重新从CSV读取刚生成的缓存
            for code in need_fetch_codes[:]:  # 使用副本迭代
                csv_path = os.path.join(LOCAL_CACHE_DIR, f"{code}.csv")
                if os.path.exists(csv_path):
                    try:
                        df = pd.read_csv(csv_path)
                        if not df.empty:
                            # 将 trade_date 转换为字符串类型，避免类型比较错误
                            df['trade_date'] = df['trade_date'].astype(str)
                            df = df[(df['trade_date'] >= start) & (df['trade_date'] <= end)].copy()
                            if not df.empty:
                                df = _add_ma_columns(df)
                                all_parts.append(df)
                                need_fetch_codes.remove(code)
                    except Exception as e:
                        print(f"[KLine] 读取tq生成的CSV失败 {csv_path}: {e}")
        except Exception as e:
            print(f"[KLine] tushare_quant 调用失败: {e}")
    
    # 剩余需要拉取的股票按批次从API拉取
    if need_fetch_codes:
        chunks = [need_fetch_codes[i : i + 80] for i in range(0, len(need_fetch_codes), 80)]
        for ci, chunk in enumerate(chunks):
            try:
                df = pro.daily(ts_code=",".join(chunk), start_date=start, end_date=end)
                if df is not None and not df.empty:
                    # 按股票分开缓存
                    for code in chunk:
                        code_df = df[df['ts_code'] == code].copy()
                        if not code_df.empty:
                            # 先计算均线，再缓存（一次缓存永久可用）
                            code_df = _add_ma_columns(code_df)
                            cache_key = f"daily_kline_{code}_{start}_{end}"
                            cache_set(cache_key, code_df)
                            all_parts.append(code_df)
                            if code in need_fetch_codes:
                                need_fetch_codes.remove(code)
                time.sleep(0.15)
            except Exception as e:
                print(f"[KLine] 批次 {ci + 1}/{len(chunks)} 失败: {e}")
                time.sleep(0.15)

    # 对始终取不到的股票做诊断并加入黑名单避免重复请求
    if need_fetch_codes:
        _mark_failed_stocks(need_fetch_codes)
    
    df = pd.concat(all_parts, ignore_index=True) if all_parts else pd.DataFrame()
    return df


def _mark_failed_stocks(codes):
    """记录无法获取K线数据的股票到黑名单，避免重复请求"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS failed_stocks (
        ts_code TEXT PRIMARY KEY,
        fail_date TEXT,
        fail_count INTEGER DEFAULT 1
    )""")
    for code in codes:
        cur.execute("SELECT fail_count FROM failed_stocks WHERE ts_code=?", (code,))
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE failed_stocks SET fail_count=?, fail_date=? WHERE ts_code=?",
                        (row[0] + 1, TRADE_DATE, code))
        else:
            cur.execute("INSERT INTO failed_stocks (ts_code, fail_date, fail_count) VALUES (?,?,1)",
                        (code, TRADE_DATE))
    conn.commit()
    conn.close()
    print(f"[KLine] ⚠ 以下 {len(codes)} 只股票无法获取K线数据（已加入黑名单，不再重试）:")
    for code in codes:
        print(f"       {code}")
    print(f"       原因推测：退市/停牌/代码格式错误/无交易数据")


FAILED_STOCKS_CACHE = None
def _is_failed_stock(ts_code):
    """检查是否在黑名单中"""
    global FAILED_STOCKS_CACHE
    if FAILED_STOCKS_CACHE is None:
        FAILED_STOCKS_CACHE = set()
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("SELECT ts_code FROM failed_stocks")
            FAILED_STOCKS_CACHE = {row[0] for row in cur.fetchall()}
            conn.close()
        except Exception:
            pass
    return ts_code in FAILED_STOCKS_CACHE


def get_index_kline(ts_code="000300.SH", start=None, end=None):
    if start is None:
        start = START_DATE
    if end is None:
        end = TRADE_DATE
    cached = cache_get("idx_kline", ts_code=ts_code, start=start, end=end)
    
    # 检查缓存数据是否包含最新日期（避免缓存昨天的数据）
    if cached is not None:
        if 'trade_date' in cached.columns:
            max_date = str(cached['trade_date'].max())
            if max_date == str(end):
                print(f"[Index] 缓存命中且包含最新数据: {ts_code}")
                return cached
            else:
                print(f"[Index] 缓存数据过期（最新日期: {max_date}, 需要: {end}），重新拉取")
        else:
            print(f"[Index] 缓存数据格式异常，重新拉取")
    
    if pro is None:
        return pd.DataFrame()
    try:
        print(f"[Index] 拉取 {ts_code} 数据: {start} ~ {end}")
        df = pro.index_daily(ts_code=ts_code, start_date=start, end_date=end)
    except Exception:
        df = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
    time.sleep(0.15)
    if df is not None and not df.empty:
        cache_set("idx_kline", df, ts_code=ts_code, start=start, end=end)
        print(f"[Index] 数据已缓存")
    return df


def _has_concept_overlap(code, stock_concepts, theme_concept_list, theme_keywords, stock_dc_industries=None):
    """检查股票的概念是否与主题的概念或关键词有重叠（子串匹配）
    
    额外检查：如果股票所在的 DC 行业板块名与 theme concept 精确匹配，直接通过。
    这解决了行业板块（如"半导体材料"）的成员在概念标签中不包含该名的问题。
    """
    # 检查 DC 行业板块名是否与 theme concept 精确匹配
    if stock_dc_industries and theme_concept_list:
        inds = stock_dc_industries.get(code, [])
        for ind in inds:
            if ind in theme_concept_list:
                return True
    
    concepts = stock_concepts.get(code, [])
    if not concepts:
        return False  # 无概念数据 → 不通过概念重叠检查（靠行业匹配+关键词才能进入）
    
    # 如果主题没有配置 concept，检查是否有关键词匹配
    if not theme_concept_list:
        # 如果有关键词，需要至少一个关键词匹配才能通过
        if theme_keywords:
            for kw in theme_keywords:
                for c in concepts:
                    if kw in c:
                        return True
                # 也检查股票名称
                #（注意：股票名称匹配在 _compute_chain_score 中单独处理）
            return False  # 没有关键词匹配，不通过概念重叠检查
        return True  # 既无概念也无关键词，纯行业匹配主题
    
    all_theme_terms = list(theme_concept_list) + list(theme_keywords)
    all_theme_terms = [t for t in all_theme_terms if t]
    if not all_theme_terms:
        return True  # 主题无概念/关键词时不阻截
    
    for sc in concepts:
        for tt in all_theme_terms:
            if tt in sc or sc in tt:
                return True
    return False


def _is_force_include(code, stock_name, core_companies, leader_companies):
    """判断股票是否属于强制纳入名单（龙头/核心公司）"""
    if leader_companies and any(c in stock_name for c in leader_companies):
        return True, "leader_company"
    if core_companies and any(c in stock_name for c in core_companies):
        return True, "core_company"
    return False, ""


def _should_exclude(code, stock_name, concepts, exclude_keywords, core_companies, leader_companies):
    """检查股票是否应被排除（跳过强制纳入名单）"""
    if not exclude_keywords:
        return False
    is_force, _ = _is_force_include(code, stock_name, core_companies, leader_companies)
    if is_force:
        return False
    return _match_exclude(code, stock_name, concepts, exclude_keywords)


def _compute_chain_score(code, stock_name, concepts, info, concept_list, keyword_list,
                         core_companies, leader_companies, chain_distance):
    """
    产业链约束匹配评分
    
    score = industry_base + concept_bonus + keyword_bonus + leader_proximity - chain_penalty
    
    规则：
    - industry_base:   DC行业板块匹配+10, stock_basic行业匹配+5
    - concept_bonus:   股票概念与theme concept精确匹配, +5/个
    - keyword_bonus:   关键词在股票名中出现+2/个, 在概念中出现+1/个
    - leader_proximity: leader_companies +15, core_companies +10, 有概念重叠+3
    - chain_penalty:   chain_distance==1 时 -5
    """
    score = 0

    # 1) industry_base
    source = info.get("source", "")
    if source == "dc_industry_board" or source == "dc_industry":
        score += 10
    elif source == "stock_basic_industry":
        score += 5
    elif source == "concept_as_industry":
        score += 8
    # concept_only gets no industry base

    # 2) concept_bonus: 股票东财概念标签与 theme concept 精确匹配
    concept_matched = 0
    for cc in concepts:
        if cc in concept_list:
            concept_matched += 1
    score += concept_matched * 5

    # 3) keyword_bonus: 关键词匹配
    kw_name_count = sum(1 for kw in keyword_list if kw in stock_name)
    score += kw_name_count * 2
    kw_concept_count = 0
    for kw in keyword_list:
        if kw not in stock_name:  # 避免重复计数
            for c in concepts:
                if kw in c:
                    kw_concept_count += 1
                    break
    score += kw_concept_count * 1

    # 4) leader_proximity
    is_force, force_type = _is_force_include(code, stock_name, core_companies, leader_companies)
    if is_force:
        if force_type == "leader_company":
            score += 15
        else:
            score += 10
    elif concept_matched > 0:
        score += 3  # 概念重叠的邻近加分

    # 5) chain_penalty
    if chain_distance == 1:
        score -= 5

    return max(score, 0)


def match_theme_stocks(hot_themes, dc_df, stock_basic_df):
    """
    ===== 产业链约束匹配模型 =====
    
    匹配原则：
    1. Industry Gate：股票必须通过行业板块匹配（东财行业板块 or stock_basic），否则直接排除
    2. Chain Distance 分层（0=核心, 1=上下游, 2+/3=排除）：
       - 0 (核心产业链)：industry match + 概念/关键词重叠 或 龙头/核心公司
       - 1 (上下游)：industry match only，无概念重叠但有部分关键词关联
       - 2+：纯行业关联无验证信息 → 排除
    3. exclude_keywords 硬过滤（跳过强制纳入公司）
    4. leader_companies 锚定：龙头公司强制 chain_distance=0，最高评分
    5. 最终评分：industry_base + concept_bonus + keyword_bonus + leader_proximity - chain_penalty
    
    输出每只股票的：
    - via: 匹配路径
    - industry_match: 是否行业匹配
    - chain_distance: 产业链层级 (0/1)
    - score: 综合评分
    """
    stock_basic_industry = {}
    name_map_basic = {}
    if stock_basic_df is not None and not stock_basic_df.empty:
        for _, row in stock_basic_df.iterrows():
            stock_basic_industry[row["ts_code"]] = row.get("industry", "")
            name_map_basic[row["ts_code"]] = row.get("name", "")

    # 拆分东财数据为行业和概念
    stock_concepts = defaultdict(list)          # code -> [概念板块名, ...]
    stock_dc_industries = defaultdict(list)     # code -> [行业板块名, ...]
    dc_concept_board_members = defaultdict(set)   # 概念板块名 -> {code, ...}
    dc_industry_board_members = defaultdict(set)  # 行业板块名 -> {code, ...}
    if dc_df is not None and not dc_df.empty:
        for _, r in dc_df.iterrows():
            con_code = r["con_code"]
            board_name = r["concept_name"]
            if con_code and board_name:
                is_industry = r.get("is_industry", False)
                if is_industry:
                    stock_dc_industries[con_code].append(board_name)
                    dc_industry_board_members[board_name].add(con_code)
                else:
                    stock_concepts[con_code].append(board_name)
                    dc_concept_board_members[board_name].add(con_code)

    theme_stock_map = {}
    
    for theme_name, cfg in hot_themes.items():
        industry_list = cfg.get("industry", [])
        concept_list = cfg.get("concept", [])
        keyword_list = cfg.get("keywords", [])
        exclude_keywords = cfg.get("exclude_keywords", [])
        core_companies = cfg.get("core_companies", [])
        leader_companies = cfg.get("leader_companies", [])
        dna_concept_required = cfg.get("dna_concept_required", [])

        # ====================================================================
        # Phase 1: Industry Gate — 股票必须通过行业匹配进入候选池
        # ====================================================================
        candidates = {}  # code -> {industry_match, source}

        # 方式A（最强）：industry 列表中的名称直接匹配东财行业板块
        for ind_name in industry_list:
            if ind_name in dc_industry_board_members:
                for code in dc_industry_board_members[ind_name]:
                    if code not in candidates:
                        candidates[code] = {"industry_match": True, "source": "dc_industry_board"}

        # 方式B（强）：股票所属东财行业板块与 theme industry 匹配
        for code, industries in stock_dc_industries.items():
            if code not in candidates:
                for ind in industries:
                    if _in_industry_list(ind, industry_list):
                        candidates[code] = {"industry_match": True, "source": "dc_industry"}
                        break

        # 方式C（中）：stock_basic 行业匹配（单一行业标签）
        for code, ind in stock_basic_industry.items():
            if code not in candidates and ind:
                if _in_industry_list(ind, industry_list):
                    candidates[code] = {"industry_match": True, "source": "stock_basic_industry"}

        # 方式D（兜底）：theme 无 industry 配置 → 用 concept 板块成员作为候选（标记为 industry_match=False）
        if not industry_list:
            for conc_name in concept_list:
                if conc_name in dc_concept_board_members:
                    for code in dc_concept_board_members[conc_name]:
                        if code not in candidates:
                            candidates[code] = {"industry_match": False, "source": "concept_only"}
                # 如果 concept 名恰好是行业板块名
                if conc_name in dc_industry_board_members:
                    for code in dc_industry_board_members[conc_name]:
                        if code not in candidates:
                            candidates[code] = {"industry_match": True, "source": "concept_as_industry"}

        # ====================================================================
        # Phase 1.5: DNA Gate — business_dna_tags 强约束
        #   如果 dna_concept_required 非空，股票必须至少匹配其中1个
        #   东财概念板块名，否则直接过滤（防止行业溢出到无关主题）
        # ====================================================================
        if dna_concept_required:
            filtered_candidates = {}
            for code, info in candidates.items():
                # 检查股票的东财概念是否与 dna_concept_required 有匹配
                concepts_for_stock = stock_concepts.get(code, [])
                industries_for_stock = stock_dc_industries.get(code, [])
                dna_match = False
                for cc in concepts_for_stock:
                    for dc in dna_concept_required:
                        if dc in cc or cc in dc:
                            dna_match = True
                            break
                    if dna_match:
                        break
                # 行业板块也可能包含业务标签（如"半导体"）
                if not dna_match:
                    for ind in industries_for_stock:
                        for dc in dna_concept_required:
                            if dc in ind or ind in dc:
                                dna_match = True
                                break
                        if dna_match:
                            break
                if dna_match:
                    filtered_candidates[code] = info
            candidates = filtered_candidates

        # ====================================================================
        # Phase 2: Chain Distance 计算 + 评分
        # ====================================================================
        matched = {}
        for code, info in candidates.items():
            stock_name = name_map_basic.get(code, "")
            concepts = stock_concepts.get(code, [])

            # --- 2a) exclude_keywords 硬过滤（跳过强制纳入公司）---
            if _should_exclude(code, stock_name, concepts, exclude_keywords, core_companies, leader_companies):
                continue

            # --- 2b) 概念重叠检查 ---
            has_concept_overlap = _has_concept_overlap(
                code, stock_concepts, concept_list, keyword_list, stock_dc_industries
            )

            # --- 2c) 关键词检查（在股票名或概念标签中）---
            kw_matches = []
            for kw in keyword_list:
                if kw in stock_name:
                    kw_matches.append(kw)
                else:
                    for c in concepts:
                        if kw in c:
                            kw_matches.append(kw)
                            break

            # --- 2d) 强制纳入检查 ---
            is_force, force_type = _is_force_include(code, stock_name, core_companies, leader_companies)

            # --- 2e) 判定 chain_distance ---
            if is_force:
                chain_distance = 0
            elif has_concept_overlap:
                chain_distance = 0      # 核心产业链：行业+概念双重确认
            elif kw_matches:
                chain_distance = 1      # 上下游：行业确认 + 关键词提示
            elif info.get("source") == "concept_only":
                chain_distance = 1      # 无行业配置的主题概念匹配 → 弱关联
            else:
                chain_distance = 2      # 纯行业匹配无验证 → 外延收益 → 排除

            if chain_distance >= 2:
                continue

            # --- 2f) 计算综合评分 ---
            score = _compute_chain_score(
                code, stock_name, concepts, info,
                concept_list, keyword_list,
                core_companies, leader_companies,
                chain_distance
            )

            # --- 2g) 构建 meta 信息 ---
            via = info.get("source", "unknown")
            if is_force:
                via = force_type

            matched[code] = {
                "via": via,
                "industry_match": info.get("industry_match", False),
                "chain_distance": chain_distance,
                "score": score
            }

        # ====================================================================
        # Phase 3: 强制纳入龙头/核心公司（即使无行业匹配）
        # ====================================================================
        for code, name in name_map_basic.items():
            is_leader = leader_companies and any(c in name for c in leader_companies)
            is_core = core_companies and any(c in name for c in core_companies)
            if (is_leader or is_core) and code not in matched:
                score = 25 if is_leader else 20
                matched[code] = {
                    "via": "leader_company" if is_leader else "core_company",
                    "industry_match": True,
                    "chain_distance": 0,
                    "score": score
                }

        theme_stock_map[theme_name] = matched

    # ====================================================================
    # Phase 4: 多主题去重（基于新评分体系）
    # ====================================================================
    theme_stock_map = _disambiguate_multi_theme(theme_stock_map, hot_themes, stock_concepts)

    return theme_stock_map, name_map_basic, stock_basic_industry, stock_concepts


def _disambiguate_multi_theme(theme_stock_map, hot_themes, stock_concepts):
    """
    多主题去重：将出现在多个主题的股票只保留在评分最佳的主题中
    
    规则：
    1. chain_distance=0（核心产业链）的股票不参与去重
    2. 龙头/核心公司（via=leader_company/core_company）强制保留
    3. 其余按 score 分配最佳主题（保留最高分）
    4. 分数差 <= 3 且 industry_match=True 的保留
    """
    from collections import defaultdict

    stock_theme_count = defaultdict(int)
    for theme_name, stocks in theme_stock_map.items():
        for code in stocks:
            stock_theme_count[code] += 1

    multi_stocks = {code for code, cnt in stock_theme_count.items() if cnt > 1}
    if not multi_stocks:
        return theme_stock_map

    removed_count = 0
    for code in list(multi_stocks):
        theme_entries = []
        for theme_name, stocks in theme_stock_map.items():
            if code in stocks:
                meta = stocks[code]
                via = meta.get("via", "")
                is_core_chain = meta.get("chain_distance", 1) == 0
                is_force = via in ("leader_company", "core_company")
                score = meta.get("score", 0)
                im = meta.get("industry_match", False)
                theme_entries.append((theme_name, via, is_core_chain, is_force, score, im))

        # 如果股票在所有主题都是核心产业链(chain=0)或强制纳入 → 跳过不去重
        all_exempt = all(is_cc or is_f for _, _, is_cc, is_f, _, _ in theme_entries)
        if all_exempt:
            continue

        # 强制纳入的公司保留
        forced_keep = {t for t, _, _, is_f, _, _ in theme_entries if is_f}

        # 按 score 降序
        theme_scores = sorted(theme_entries, key=lambda x: -x[4])
        best_score = theme_scores[0][4]

        keep_themes = set(forced_keep)
        for t, _, is_cc, is_f, sc, im in theme_scores:
            if t in forced_keep:
                continue
            if sc == best_score:
                keep_themes.add(t)
            elif best_score - sc <= 3 and im and not theme_scores[0][5]:
                # 分数相近且当前最佳无行业匹配 → 保留有行业匹配的
                keep_themes.add(t)

        for theme_name, _, is_cc, is_f, _, _ in theme_entries:
            if theme_name not in keep_themes and not is_cc and not is_f:
                del theme_stock_map[theme_name][code]
                removed_count += 1

    if removed_count:
        print(f"[Match] 多主题去重: {removed_count} 条（跨主题股票配到最佳主题）")

    return theme_stock_map

def _match_exclude(code, stock_name, concepts, exclude_keywords):
    """检查股票是否匹配排除关键词（子串匹配）"""
    for ek in exclude_keywords:
        if ek in stock_name:
            return True
        for c in concepts:
            if ek in c:
                return True
    return False


def per_stock_features(df_one):
    if df_one is None or df_one.empty or len(df_one) < 6:
        return None

    df_one = df_one.sort_values("trade_date").reset_index(drop=True)
    close = df_one["close"].astype(float).values
    high = df_one["high"].astype(float).values
    low = df_one["low"].astype(float).values
    vol = df_one["vol"].astype(float).values
    pct = df_one["pct_chg"].astype(float).values

    n = len(close)
    last = n - 1

    def safe_pct(a, b):
        return (a / b - 1.0) * 100.0 if b and b > 0 else 0.0

    def calc_slope(prices):
        if len(prices) < 3:
            return 0.0
        x = np.arange(len(prices))
        slope = np.polyfit(x, prices, 1)[0]
        slope_norm = (slope / np.mean(prices)) * 100 if np.mean(prices) > 0 else 0
        return slope_norm

    ret_5 = safe_pct(close[last], close[last - 5]) if last - 5 >= 0 else safe_pct(close[last], close[0])
    ret_10 = safe_pct(close[last], close[last - 10]) if last - 10 >= 0 else safe_pct(close[last], close[0])
    ret_20 = safe_pct(close[last], close[last - 20]) if last - 20 >= 0 else safe_pct(close[last], close[0])

    ma5 = close[max(0, last - 4) : last + 1].mean()
    ma10 = close[max(0, last - 9) : last + 1].mean()
    ma20 = close[max(0, last - 19) : last + 1].mean()
    ma60 = close[max(0, last - 59) : last + 1].mean() if n >= 60 else ma20
    ma240 = close[max(0, last - 239) : last + 1].mean() if n >= 240 else ma60
    ma5_b = (close[last] / ma5 - 1) * 100 if ma5 > 0 else 0
    ma10_b = (close[last] / ma10 - 1) * 100 if ma10 > 0 else 0
    ma20_b = (close[last] / ma20 - 1) * 100 if ma20 > 0 else 0
    ma60_b = (close[last] / ma60 - 1) * 100 if ma60 > 0 else 0
    ma240_b = (close[last] / ma240 - 1) * 100 if ma240 > 0 else 0

    win10 = close[max(0, last - 9) : last + 1]
    slope10 = calc_slope(win10)
    win60 = close[max(0, last - 59) : last + 1]
    slope60 = calc_slope(win60)
    win240 = close[max(0, last - 239) : last + 1]
    slope240 = calc_slope(win240)

    acc_5_10 = ret_5 - ret_10

    v5 = vol[max(0, last - 4) : last + 1].mean()
    v20 = vol[max(0, last - 19) : last + 1].mean()
    vol_ratio = v5 / v20 if v20 > 0 else 1.0

    win10 = close[max(0, last - 9) : last + 1]
    if len(win10) > 1:
        running_max = np.maximum.accumulate(win10)
        drawdown = (win10 / running_max - 1.0)
        max_dd_10 = drawdown.min() * 100
    else:
        max_dd_10 = 0.0

    zt_flag = 1 if (pct[last] is not None and pct[last] >= 9.5) else 0
    strong_flag = 1 if (pct[last] is not None and pct[last] >= 5.0) else 0

    amount_latest = float(df_one.iloc[last].get("amount", 0) or 0) / 100000

    lb_height = 0
    for j in range(last, -1, -1):
        p = float(pct[j]) if pct[j] is not None else 0
        if p >= 9.5:
            lb_height += 1
        else:
            break

    return {
        "ret_5": ret_5, "ret_10": ret_10, "ret_20": ret_20,
        "ma5_b": ma5_b, "ma10_b": ma10_b, "ma20_b": ma20_b,
        "ma60_b": ma60_b, "ma240_b": ma240_b,
        "slope_10": slope10, "slope_60": slope60, "slope_240": slope240,
        "acc_5_10": acc_5_10, "vol_ratio": vol_ratio, "max_dd_10": max_dd_10,
        "zt_flag": zt_flag, "strong_flag": strong_flag,
        "pct_chg": float(pct[last]) if pct[last] is not None else 0.0,
        "turnover": float(df_one.iloc[last].get("turnover_rate", 0) or 0),
        "amount_latest": amount_latest, "lb_height": lb_height,
    }


def sigmoid(x, k=0.15, c=0.0):
    try:
        return 1.0 / (1.0 + np.exp(-k * (x - c)))
    except Exception:
        return 0.5


def linear(x, lo, hi, out_lo=0.0, out_hi=1.0):
    if hi == lo:
        return out_lo
    v = (x - lo) / (hi - lo)
    v = max(0.0, min(1.0, v))
    return out_lo + v * (out_hi - out_lo)


def calc_trend_score(stock_feats, market_index_ret):
    if not stock_feats:
        return 0.0, {}

    n = len(stock_feats)
    avg_ret_5 = np.mean([s["ret_5"] for s in stock_feats])
    avg_ret_10 = np.mean([s["ret_10"] for s in stock_feats])
    avg_ret_20 = np.mean([s["ret_20"] for s in stock_feats])

    ret_score = (linear(avg_ret_5, -10, 15) * 0.5 + linear(avg_ret_10, -15, 25) * 0.3 + linear(avg_ret_20, -25, 40) * 0.2)

    pct_above_ma5 = sum(1 for s in stock_feats if s["ma5_b"] > 0) / n
    pct_above_ma10 = sum(1 for s in stock_feats if s["ma10_b"] > 0) / n
    pct_above_ma20 = sum(1 for s in stock_feats if s["ma20_b"] > 0) / n
    pct_above_ma60 = sum(1 for s in stock_feats if s["ma60_b"] > 0) / n
    pct_above_ma240 = sum(1 for s in stock_feats if s["ma240_b"] > 0) / n
    ma_score = pct_above_ma5 * 0.30 + pct_above_ma10 * 0.25 + pct_above_ma20 * 0.20 + pct_above_ma60 * 0.15 + pct_above_ma240 * 0.10

    avg_slope10 = np.mean([s["slope_10"] for s in stock_feats])
    avg_slope60 = np.mean([s["slope_60"] for s in stock_feats])
    avg_slope240 = np.mean([s["slope_240"] for s in stock_feats])
    slope_score = sigmoid(avg_slope10, k=0.3, c=0) * 0.4 + sigmoid(avg_slope60, k=0.25, c=0) * 0.35 + sigmoid(avg_slope240, k=0.2, c=0) * 0.25

    avg_acc = np.mean([s["acc_5_10"] for s in stock_feats])
    acc_score = sigmoid(avg_acc, k=0.3, c=0)

    pcts = sorted([s["pct_chg"] for s in stock_feats], reverse=True)
    top3 = pcts[: min(3, len(pcts))]
    top3_avg = np.mean(top3) if top3 else 0
    leader_score = linear(top3_avg, -5, 15)

    avg_dd = np.mean([s["max_dd_10"] for s in stock_feats])
    dd_score = linear(-avg_dd, -2, 10)

    rel_ret = avg_ret_10 - market_index_ret
    rel_score = sigmoid(rel_ret, k=0.2, c=0)

    # =========================
    # 当日动量（新增）：考虑当日涨跌对趋势的影响（权重较小，避免过度反应）
    # =========================
    pcts_today = [s["pct_chg"] for s in stock_feats]
    avg_pct_today = np.mean(pcts_today)
    up_n = sum(1 for p in pcts_today if p > 0)
    down_n = sum(1 for p in pcts_today if p < 0)
    breadth_today = up_n / n if n > 0 else 0.5
    
    # 当日动量分：综合考虑平均涨幅和上涨比例
    today_momentum_score = linear(avg_pct_today, -3, 3) * 0.6 + linear(breadth_today, 0.2, 0.8) * 0.4
    
    # 当日分歧微调（温和调整，不误判趋势结束）
    # 只有在大幅下跌+普跌时才轻微下调，否则基本不变
    if avg_pct_today < -2.0 and breadth_today < 0.3:
        today_adjust = 0.92  # 大幅分歧，小幅下调8%
    elif avg_pct_today < -1.0 and breadth_today < 0.4:
        today_adjust = 0.96  # 中等分歧，小幅下调4%
    else:
        today_adjust = 1.0  # 正常波动，不影响趋势分

    # 严格趋势判断：
    # 1. 60日和240日斜率必须是正的（向上趋势）
    # 2. 10日趋势斜率也要是正的
    # 3. 中期收益为正
    mid_trend_ok = (avg_slope60 > 0) and (avg_slope240 >= 0) and (avg_slope10 > 0) and (avg_ret_20 >= 0)

    # 最终评分：当日动量权重较小（8%），保持趋势分稳定
    score01 = (
        ret_score * 0.26 +           # 收益分
        ma_score * 0.22 +            # 均线分
        slope_score * 0.18 +         # 斜率分
        acc_score * 0.06 +           # 加速度分
        leader_score * 0.06 +        # 龙头分
        dd_score * 0.05 +            # 回撤分
        rel_score * 0.09 +           # 相对强度分
        today_momentum_score * 0.08  # 当日动量分（权重较小，避免过度反应）
    ) * today_adjust  # 温和调整
    
    score01 = max(0.0, min(1.0, score01))

    detail = {
        "avg_ret_5": round(avg_ret_5, 2), "avg_ret_10": round(avg_ret_10, 2), "avg_ret_20": round(avg_ret_20, 2),
        "pct_above_ma5": round(pct_above_ma5 * 100, 1), "pct_above_ma10": round(pct_above_ma10 * 100, 1),
        "pct_above_ma20": round(pct_above_ma20 * 100, 1), "pct_above_ma60": round(pct_above_ma60 * 100, 1),
        "pct_above_ma240": round(pct_above_ma240 * 100, 1),
        "avg_slope_10": round(avg_slope10, 3), "avg_slope_60": round(avg_slope60, 3), "avg_slope_240": round(avg_slope240, 3),
        "avg_acc_5_10": round(avg_acc, 2), "top3_avg_pct": round(top3_avg, 2),
        "avg_max_dd_10": round(avg_dd, 2), "rel_ret_10": round(rel_ret, 2), "mid_trend_ok": 1 if mid_trend_ok else 0,
        "avg_pct_today": round(avg_pct_today, 2), "breadth_today": round(breadth_today * 100, 1), "today_adjust": today_adjust,
    }
    return round(score01 * 100, 1), detail


def calc_sentiment_score(stock_feats, market_index_ret):
    """计算情绪分（热榜分作为加分项）
    
    热榜分数计算：
    - 热榜只提供前100个股的热度，因此热榜分作为加分项
    - 热榜分数取主题内所有个股的平均热榜分（0-10分）
    - 最终情绪分 = 基础分（0-100） + 热榜加分（0-10）
    """
    if not stock_feats:
        return 0.0, {}

    n = len(stock_feats)
    pcts = [s["pct_chg"] for s in stock_feats]
    up_n = sum(1 for p in pcts if p > 0)
    down_n = sum(1 for p in pcts if p < 0)
    zt_n = sum(1 for s in stock_feats if s["zt_flag"] == 1)
    strong_n = sum(1 for s in stock_feats if s["strong_flag"] == 1)

    breadth = up_n / n
    breadth_score = linear(breadth, 0.2, 0.85)
    zt_ratio = zt_n / n
    zt_score = linear(zt_ratio, 0, 0.15)
    strong_ratio = strong_n / n
    strong_score = linear(strong_ratio, 0, 0.30)
    
    # 处理 NaN 值
    vol_ratios = [s.get("vol_ratio", 0) for s in stock_feats]
    avg_vol_ratio = float(np.nanmean(vol_ratios)) if vol_ratios else 0.0
    vol_score = linear(avg_vol_ratio, 0.6, 1.8)
    
    turnovers = [s.get("turnover", 0) for s in stock_feats]
    avg_turnover = float(np.nanmean(turnovers)) if turnovers else 0.0
    turnover_score = linear(avg_turnover, 1.0, 8.0)
    
    median_pct = float(np.median(pcts))
    mean_pct = float(np.mean(pcts))
    profit_score = sigmoid(median_pct * 0.6 + mean_pct * 0.4, k=0.25, c=0)
    top1 = max(pcts) if pcts else 0
    resonance = 1.0 if (zt_n >= 1 and top1 >= 7) else 0.0
    if zt_n >= 2 and top1 >= 9:
        resonance = 1.2
    resonance_score = min(resonance, 1.0)

    # 基础分数（0-100）
    base_score = breadth_score * 0.25 + zt_score * 0.20 + strong_score * 0.15 + vol_score * 0.10 + turnover_score * 0.10 + profit_score * 0.10 + resonance_score * 0.10

    # 热榜加分（0-10分，作为加分项）
    hot_scores = [s.get("hot_rank_score", 0) for s in stock_feats]
    avg_hot_score = np.mean(hot_scores) if hot_scores else 0
    hot_bonus = avg_hot_score / 10.0  # 归一化到 0-1（加分项）

    # 最终分数 = 基础分 + 热榜加分
    score01 = base_score + hot_bonus * 0.10  # 热榜加分最多提升10%
    score01 = max(0.0, min(1.0, score01))

    detail = {
        "up_ratio": round(breadth * 100, 1), "down_ratio": round(down_n / n * 100, 1),
        "zt_count": zt_n, "zt_ratio": round(zt_ratio * 100, 1), "strong_ratio": round(strong_ratio * 100, 1),
        "avg_vol_ratio": round(avg_vol_ratio, 2), "avg_turnover": round(avg_turnover, 2),
        "median_pct": round(median_pct, 2), "mean_pct": round(mean_pct, 2), "top1_pct": round(top1, 2), "resonance": round(resonance, 2),
        "avg_hot_score": round(avg_hot_score, 1),  # 平均热榜分（0-10）
    }
    return round(score01 * 100, 1), detail


def calc_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    deltas = np.diff(prices)
    gains = deltas[deltas > 0]
    losses = -deltas[deltas < 0]
    avg_gain = np.mean(gains[:period]) if len(gains) >= period else 0.0001
    avg_loss = np.mean(losses[:period]) if len(losses) >= period else 0.0001
    for i in range(period, len(deltas)):
        delta = deltas[i]
        gain = delta if delta > 0 else 0
        loss = -delta if delta < 0 else 0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    rs = avg_gain / max(avg_loss, 0.0001)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calc_theme_state(r, prev_data=None):
    """判断主题状态（优化版：更符合A股抱团逻辑）
    
    状态定义（基于资金抱团、龙头效应、趋势持续性）：
    - 抱团主升：核心龙头持续新高，资金高度集中，趋势陡峭上行
    - 强趋势：趋势分高且持续上升，情绪活跃，赚钱效应明显
    - 分歧转一致：前期分歧后资金回流，快速修复
    - 启动：低位反转，资金开始进场，涨停数增加
    - 分歧：高位震荡，多空博弈，等待方向选择
    - 退潮：资金撤离，情绪低迷，趋势向下
    
    A股抱团逻辑要点：
    1. 龙头稳定性 > 整体涨幅
    2. 资金集中度（成交额占比）是核心
    3. 上涨家数占比反映板块强度
    4. 趋势斜率判断持续性
    5. 连板高度反映情绪热度
    
    Args:
        r: 当前主题数据
        prev_data: 前一日主题数据（用于判断趋势变化）
    
    Returns:
        state: 主题状态字符串
    """
    t_score = r.get("trend_score", 0)
    s_score = r.get("sentiment_score", 0)
    td = r.get("trend_detail", {}) or {}
    sd = r.get("sentiment_detail", {}) or {}
    
    # 趋势指标
    avg_ret_5 = td.get("avg_ret_5", 0)
    avg_ret_10 = td.get("avg_ret_10", 0)
    avg_pct_today = td.get("avg_pct_today", 0)
    pct_above_ma5 = td.get("pct_above_ma5", 0)
    avg_slope_10 = td.get("avg_slope_10", 0)
    mid_trend_ok = td.get("mid_trend_ok", 0)
    leader_stability = td.get("leader_stability", 0)  # 龙头稳定性
    
    # 情绪指标
    zt_count = sd.get("zt_count", 0)
    up_ratio = sd.get("up_ratio", 0)
    turnover_rate = sd.get("turnover_rate", 0)  # 换手率
    volume_ratio = sd.get("volume_ratio", 0)    # 量比
    
    # 获取前一日数据
    prev_t_score = t_score
    prev_s_score = s_score
    prev_up_ratio = up_ratio
    if prev_data:
        prev_t_score = prev_data.get("trend_score", t_score)
        prev_s_score = prev_data.get("sentiment_score", s_score)
        prev_sd = prev_data.get("sentiment_detail", {}) or {}
        prev_up_ratio = prev_sd.get("up_ratio", up_ratio)
    
    # ========== 1. 抱团主升（最核心状态）==========
    # 条件：龙头稳定 + 趋势高位 + 情绪高涨
    if (t_score >= 75 and 
        s_score >= 75 and 
        zt_count >= 4 and              # 涨停数>=4
        up_ratio >= 65):               # 上涨家数占比>=65%
        return "抱团主升"
    
    # ========== 2. 强趋势 ==========
    # 条件：趋势分>=60且情绪分>=60即可
    if t_score >= 60 and s_score >= 60:
        return "强趋势"
    
    # ========== 3. 分歧转一致 ==========
    # 条件：前期分歧后资金回流，快速修复
    if (prev_data and 
        45 <= prev_t_score < 60 and  # 前一日处于分歧区间
        t_score > prev_t_score + 3 and  # 趋势分快速回升
        s_score > prev_s_score + 5 and  # 情绪分快速回升
        up_ratio >= 65 and              # 上涨家数占比高
        zt_count >= 3):                 # 涨停数增加
        return "分歧转一致"
    
    # ========== 4. 启动（收紧条件，更稀缺）==========
    # 条件：低位反转，资金快速进场，必须同时满足多个强化条件
    if (40 <= t_score < 60 and 
        avg_ret_5 > 5 and              # 5日涨幅>5%（提高门槛）
        avg_ret_10 < avg_ret_5 * 0.5 and  # 10日涨幅远低于5日（真正刚启动）
        zt_count >= 3 and              # 涨停数>=3（提高门槛）
        volume_ratio > 1.3):           # 成交量明显放大
        return "启动"
    
    # ========== 5. 分歧 ==========
    # 条件：高位震荡，多空博弈
    if (t_score >= 55 and 
        abs(avg_pct_today) < 1 and     # 当日窄幅震荡
        up_ratio < 55 and              # 上涨家数不足
        zt_count > 0 and               # 仍有涨停（有资金在维护）
        t_score < prev_t_score + 2):   # 趋势分停滞
        return "分歧"
    
    # ========== 6. 退潮 ==========
    # 条件：资金撤离，情绪低迷
    if (t_score < 50 and 
        s_score < 45 and 
        avg_slope_10 < -0.05 and       # 趋势向下
        up_ratio < 40 and              # 下跌家数占优
        zt_count == 0):                # 无涨停
        return "退潮"
    
    # ========== 7. 弱趋势（弱势整理）==========
    if (t_score >= 50 and 
        s_score >= 45 and 
        abs(avg_slope_10) < 0.05):     # 趋势平缓
        return "弱趋势"
    
    # 默认：根据趋势分判断
    if t_score >= 50:
        return "震荡"
    else:
        return "弱势"


def analyze_style_trend(results):
    """
    风格维度中期跟踪分析：按 style 聚合题材，识别市场主线与轮动支线。
    
    输出每个风格的：
      - 平均综合分、平均趋势分、平均情绪分
      - 包含题材数量及题材列表
      - 处于"启动/上升中/短期爆发/中期持续"的可交易题材数量
      - 5日/10日平均涨幅
      - 风格内涨停总数
      - 风格状态标签（主线/支线/冷门）
    
    返回 (style_rankings, style_summary):
      - style_rankings: 按综合分排名的风格列表
      - style_summary: 风格维度的描述文本
    """
    from collections import defaultdict
    
    style_data = defaultdict(lambda: {
        "themes": [], "t_scores": [], "s_scores": [], "c_scores": [],
        "ret_5_list": [], "ret_10_list": [], "zt_total": 0,
        "tradeable_count": 0,
    })
    
    # 可交易状态：抱团主升、强趋势、启动、分歧转一致
    tradeable_states = {"抱团主升", "强趋势", "启动", "分歧转一致"}
    
    for r in results:
        style = r.get("style", "未分类")
        d = style_data[style]
        d["themes"].append(r["theme"])
        d["t_scores"].append(r["trend_score"])
        d["s_scores"].append(r["sentiment_score"])
        d["c_scores"].append(r["composite_score"])
        
        td = r.get("trend_detail", {}) or {}
        d["ret_5_list"].append(td.get("avg_ret_5", 0))
        d["ret_10_list"].append(td.get("avg_ret_10", 0))
        
        sd = r.get("sentiment_detail", {}) or {}
        d["zt_total"] += sd.get("zt_count", 0)
        
        # 使用 theme_state 替代 rotation_cycle
        theme_state = r.get("theme_state", "弱势")
        if theme_state in tradeable_states:
            d["tradeable_count"] += 1
    
    # 计算风格指标并排序
    style_summary = []
    for style, d in style_data.items():
        n = len(d["themes"])
        avg_c = np.mean(d["c_scores"]) if d["c_scores"] else 0
        avg_t = np.mean(d["t_scores"]) if d["t_scores"] else 0
        avg_s = np.mean(d["s_scores"]) if d["s_scores"] else 0
        avg_ret5 = np.mean(d["ret_5_list"]) if d["ret_5_list"] else 0
        avg_ret10 = np.mean(d["ret_10_list"]) if d["ret_10_list"] else 0
        
        # 风格状态标签
        if d["tradeable_count"] >= 2 and avg_t >= 60:
            status = "◆ 主线"  # 核心主线
        elif d["tradeable_count"] >= 1 and avg_t >= 50:
            status = "◇ 支线"  # 轮动支线
        elif avg_t >= 55:
            status = "○ 活跃"  # 活跃但未形成主线
        elif avg_t >= 40:
            status = "△ 冷门"  # 冷门
        else:
            status = "× 弱势"  # 弱势
        
        style_summary.append({
            "style": style, "count": n, "avg_trend": round(avg_t, 1),
            "avg_sentiment": round(avg_s, 1), "composite": round(avg_c, 1),
            "avg_ret_5": round(avg_ret5, 2), "avg_ret_10": round(avg_ret10, 2),
            "zt_total": d["zt_total"], "tradeable": d["tradeable_count"],
            "themes": d["themes"], "status": status,
        })
    
    style_summary.sort(key=lambda x: x["composite"], reverse=True)
    
    # 生成描述文本
    lines = []
    lines.append("=" * 80)
    lines.append("  风格维度中期跟踪（主线/支线识别）")
    lines.append("=" * 80)
    lines.append(f"{'风格':<12}{'题材':<4}{'综合分':<8}{'趋势分':<8}{'情绪分':<8}{'5日%':<7}{'10日%':<7}{'涨停':<5}{'可交易':<6}{'状态':<10}")
    lines.append("-" * 80)
    for s in style_summary:
        lines.append(f"{s['style']:<12}{s['count']:<4}{s['composite']:<8}{s['avg_trend']:<8}{s['avg_sentiment']:<8}"
                     f"{s['avg_ret_5']:<7}{s['avg_ret_10']:<7}{s['zt_total']:<5}{s['tradeable']:<6}{s['status']:<10}")
    lines.append("-" * 80)
    
    # 主线与支线汇总
    main_lines = [s for s in style_summary if "主线" in s["status"]]
    branch_lines = [s for s in style_summary if "支线" in s["status"]]
    active_lines = [s for s in style_summary if "活跃" in s["status"]]
    
    if main_lines:
        lines.append(f"\n【当前市场主线】{', '.join(s['style'] for s in main_lines)}")
        for s in main_lines:
            lines.append(f"  {s['style']}: {', '.join(s['themes'])}")
    if branch_lines:
        lines.append(f"\n【轮动支线】{', '.join(s['style'] for s in branch_lines)}")
        for s in branch_lines:
            lines.append(f"  {s['style']}: {', '.join(s['themes'])}")
    if active_lines:
        lines.append(f"\n【活跃方向（待确认）】{', '.join(s['style'] for s in active_lines)}")
    lines.append("=" * 80)
    
    style_text = "\n".join(lines)
    return style_summary, style_text


def save_to_csv(results):
    flat = []
    for r in results:
        climax_warning = 1 if (r["trend_score"] >= 70 and r["sentiment_score"] >= 85) else 0
        row = {"rank": r["rank"], "theme": r["theme"], "n_stocks": r["n_stocks"], "trend_score": r["trend_score"],
               "sentiment_score": r["sentiment_score"], "composite_score": r["composite_score"], "climax_warning": climax_warning,
               "leader_name": r.get("leader_name", ""), "leader_code": r.get("leader_code", ""), "leader_score": r.get("leader_score", 0),
               "core_name": r.get("core_name", ""), "core_code": r.get("core_code", ""), "core_score": r.get("core_score", 0)}
        row.update({f"t_{k}": v for k, v in (r.get("trend_detail") or {}).items()})
        row.update({f"s_{k}": v for k, v in (r.get("sentiment_detail") or {}).items()})
        flat.append(row)

    # 被占用时自动换文件名重试
    path = OUTPUT_CSV
    for attempt in range(3):
        try:
            pd.DataFrame(flat).to_csv(path, index=False, encoding="utf-8-sig")
            return
        except PermissionError:
            if attempt == 0:
                path = OUTPUT_CSV.replace(".csv", f"_{TRADE_DATE}.csv")
            else:
                from datetime import datetime
                path = OUTPUT_CSV.replace(".csv", f"_{datetime.now().strftime('%H%M%S')}.csv")
            print(f"[Save] CSV 被占用，尝试保存到: {path}")


def save_to_sqlite(results):
    conn = sqlite3.connect(OUTPUT_DB)
    cur = conn.cursor()
    
    # 先删除该日期的旧数据
    cur.execute("DELETE FROM theme_scores WHERE trade_date = ?", (TRADE_DATE,))
    
    # 创建表（如果不存在）或添加新列（如果表已存在但缺少列）
    cur.execute("""CREATE TABLE IF NOT EXISTS theme_scores (
        rank INTEGER, theme TEXT, n_stocks INTEGER, trend_score REAL, sentiment_score REAL, composite_score REAL,
        climax_warning INTEGER DEFAULT 0, leader_name TEXT, leader_code TEXT, leader_score REAL,
        core_name TEXT, core_code TEXT, core_score REAL, ret_5 REAL, ret_10 REAL, ret_20 REAL, up_ratio REAL, zt_count INTEGER, 
        trade_date TEXT, theme_state TEXT, hot_score REAL, hot_percentile REAL, hot_phase TEXT, hot_warning TEXT
    )""")
    
    # 检查表结构，如果缺少某些列则添加
    cur.execute("PRAGMA table_info(theme_scores)")
    columns = [row[1] for row in cur.fetchall()]
    if "theme_state" not in columns:
        cur.execute("ALTER TABLE theme_scores ADD COLUMN theme_state TEXT DEFAULT '弱势'")
    if "hot_score" not in columns:
        cur.execute("ALTER TABLE theme_scores ADD COLUMN hot_score REAL DEFAULT 0")
    if "hot_percentile" not in columns:
        cur.execute("ALTER TABLE theme_scores ADD COLUMN hot_percentile REAL DEFAULT 50")
    if "hot_phase" not in columns:
        cur.execute("ALTER TABLE theme_scores ADD COLUMN hot_phase TEXT DEFAULT '正常'")
    if "hot_warning" not in columns:
        cur.execute("ALTER TABLE theme_scores ADD COLUMN hot_warning TEXT DEFAULT ''")
    if "top10_days_10d" not in columns:
        cur.execute("ALTER TABLE theme_scores ADD COLUMN top10_days_10d INTEGER DEFAULT 0")
    if "top10_days_20d" not in columns:
        cur.execute("ALTER TABLE theme_scores ADD COLUMN top10_days_20d INTEGER DEFAULT 0")
    
    # 固定列名顺序
    fixed_columns = ["rank", "theme", "n_stocks", "trend_score", "sentiment_score", "composite_score",
                     "climax_warning", "leader_name", "leader_code", "leader_score",
                     "core_name", "core_code", "core_score", "ret_5", "ret_10", "ret_20",
                     "up_ratio", "zt_count", "trade_date", "theme_state",
                     "hot_score", "hot_percentile", "hot_phase", "hot_warning",
                     "top10_days_10d", "top10_days_20d"]
    # 确保表中实际存在这些列
    existing_columns = [c for c in fixed_columns if c in columns]
    col_str = ', '.join(existing_columns)
    placeholders = ','.join(['?' for _ in existing_columns])
    
    # 预先计算10日/20日稳定性
    top10_10d = get_top10_stability(TRADE_DATE, days=10)
    top10_20d = get_top10_stability(TRADE_DATE, days=20)
    
    # 插入数据
    for r in results:
        td = r.get("trend_detail", {}) or {}
        sd = r.get("sentiment_detail", {}) or {}
        climax_warning = 1 if (r["trend_score"] >= 70 and r["sentiment_score"] >= 85) else 0
        theme_state = r.get("theme_state", "弱势")
        theme_name = r.get("theme", "")
        
        # 按fixed_columns顺序构建values
        col_to_val = {
            "rank": r["rank"],
            "theme": r["theme"],
            "n_stocks": r["n_stocks"],
            "trend_score": r["trend_score"],
            "sentiment_score": r["sentiment_score"],
            "composite_score": r["composite_score"],
            "climax_warning": climax_warning,
            "leader_name": r.get("leader_name", ""),
            "leader_code": r.get("leader_code", ""),
            "leader_score": r.get("leader_score", 0),
            "core_name": r.get("core_name", ""),
            "core_code": r.get("core_code", ""),
            "core_score": r.get("core_score", 0),
            "ret_5": td.get("avg_ret_5", 0),
            "ret_10": td.get("avg_ret_10", 0),
            "ret_20": td.get("avg_ret_20", 0),
            "up_ratio": sd.get("up_ratio", 0),
            "zt_count": sd.get("zt_count", 0),
            "trade_date": TRADE_DATE,
            "theme_state": theme_state,
            "hot_score": r.get("hot_score", 0),
            "hot_percentile": r.get("hot_percentile", 50),
            "hot_phase": r.get("hot_phase", "正常"),
            "hot_warning": r.get("hot_warning", ""),
            "top10_days_10d": top10_10d.get(theme_name, 0),
            "top10_days_20d": top10_20d.get(theme_name, 0),
        }
        values = [col_to_val[c] for c in existing_columns]
        cur.execute(f"INSERT INTO theme_scores ({col_str}) VALUES ({placeholders})", values)
    conn.commit()
    conn.close()


def save_report_text(results):
    """输出精简版分析报告"""
    report_path = os.path.join(REPORT_DIR, f"theme_analysis_{TRADE_DATE}.txt")

    buf = []
    def w(s=""):
        buf.append(s)

    w("=" * 80)
    w(f"  主题趋势分析报告 - {TRADE_DATE}")
    w("=" * 80)
    w()

    # ========== 重点机会：分歧转一致 ==========
    divergence_to_consensus = [r for r in results if r.get("theme_state") == "分歧转一致"]
    if divergence_to_consensus:
        w("★ 分歧转一致（重点关注）★")
        w("-" * 60)
        for r in divergence_to_consensus:
            sd = r.get("sentiment_detail", {}) or {}
            td = r.get("trend_detail", {}) or {}
            w(f"  {r['theme']:<12} 趋势:{r['trend_score']:5.1f} 情绪:{r['sentiment_score']:5.1f} 涨停:{sd.get('zt_count', 0)}家 上涨:{sd.get('up_ratio', 0):.0f}%")
            w(f"              龙头:{r.get('leader_name', '')}")
        w()

    # ========== 重点机会：启动/主升 ==========
    start_rising = [r for r in results if r.get("theme_state") in ["启动", "主升"]]
    if start_rising:
        w("☆ 启动/主升（趋势向好）☆")
        w("-" * 60)
        for r in start_rising[:5]:
            sd = r.get("sentiment_detail", {}) or {}
            w(f"  {r['theme']:<12} 趋势:{r['trend_score']:5.1f} 情绪:{r['sentiment_score']:5.1f} 涨停:{sd.get('zt_count', 0)}家 龙头:{r.get('leader_name', '')}")
        w()

    # ========== 风险警示：高潮/退潮 ==========
    climax = [r for r in results if r.get("theme_state") == "高潮"]
    retreat = [r for r in results if r.get("theme_state") == "退潮"]
    if climax or retreat:
        w("⚠️ 风险提示")
        w("-" * 60)
        for r in climax:
            sd = r.get("sentiment_detail", {}) or {}
            w(f"  {r['theme']:<12} 高潮⚠️ 趋势:{r['trend_score']:5.1f} 情绪:{r['sentiment_score']:5.1f} 涨停:{sd.get('zt_count', 0)}家 → 注意止盈")
        for r in retreat:
            w(f"  {r['theme']:<12} 退潮 趋势:{r['trend_score']:5.1f} 情绪:{r['sentiment_score']:5.1f} → 规避")
        w()

    # ========== 主题排名表（精简版）==========
    w("-" * 80)
    w(f"{'排名':<3} {'主题':<12} {'趋势':<6} {'情绪':<6} {'综合':<6} {'状态':<10}")
    w("-" * 80)
    for r in results:
        theme_state = r.get("theme_state", "弱势")
        state_icon = ""
        if theme_state == "抱团主升": state_icon = "抱团主升🔥"
        elif theme_state == "高潮": state_icon = "高潮⚠️"
        elif theme_state == "强趋势": state_icon = "强趋势↑"
        elif theme_state == "分歧转一致": state_icon = "转一致⭐"
        elif theme_state == "主升": state_icon = "主升↑"
        elif theme_state == "启动": state_icon = "启动↑"
        elif theme_state == "分歧": state_icon = "分歧~"
        elif theme_state == "退潮": state_icon = "退潮↓"
        elif theme_state == "弱趋势": state_icon = "弱趋势→"
        elif theme_state == "震荡": state_icon = "震荡→"
        else: state_icon = theme_state
        w(f"{r['rank']:<3} {r['theme']:<12} {r['trend_score']:<6.1f} {r['sentiment_score']:<6.1f} {r['composite_score']:<6.1f} {state_icon:<10}")
    w("-" * 80)
    w()

    w("=" * 80)

    # ========== 渤海证券轮动监测 ==========
    rotation_info = calc_rotation_monitoring(ndays=20)
    w(rotation_info["details"])

    w("=" * 80)
    w(f"报告生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    w()

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(buf))
    print(f"[Save] 分析报告: {report_path}")


def main():
    print("=" * 60)
    print("主题趋势 + 情绪 评分系统（自建'行业最强'算法）")
    print("=" * 60)

    hot_themes = load_theme_json()
    print(f"[Theme] 加载 {len(hot_themes)} 个主题")

    dc_df = get_dc_members()
    stock_basic = get_stock_basic()
    daily_basic = get_daily_basic()
    print(f"[Data] stock_basic: {len(stock_basic)}  daily_basic: {len(daily_basic)}")

    theme_stock_map, name_map_basic, stock_industry, stock_concepts = match_theme_stocks(hot_themes, dc_df, stock_basic)

    all_codes = set()
    for tn, m in theme_stock_map.items():
        all_codes.update(m.keys())
    print(f"[Match] 全市场命中成份股去重: {len(all_codes)} 只")

    kline_df = get_daily_kline(list(all_codes), START_DATE, TRADE_DATE)
    print(f"[KLine] 拉取 {len(kline_df)} 条 K 线记录")

    idx_df = get_index_kline("000300.SH")
    market_ret_10 = 0.0
    if idx_df is not None and not idx_df.empty:
        idx_df = idx_df.sort_values("trade_date")
        closes = idx_df["close"].astype(float).values
        if len(closes) >= 11:
            market_ret_10 = (closes[-1] / closes[-11] - 1) * 100
    print(f"[Index] 沪深300 近10日收益: {market_ret_10:+.2f}%")

    # 获取前一日主题数据（用于判断状态变化）
    prev_theme_data = get_prev_day_theme_data()
    if prev_theme_data:
        print(f"[State] 获取前一日 {len(prev_theme_data)} 个主题数据")

    kline_groups = {}
    if not kline_df.empty:
        for code, sub in kline_df.groupby("ts_code"):
            kline_groups[code] = sub

    results = []
    rows_per_theme = {}
    for theme_name, cfg in hot_themes.items():
        matched = theme_stock_map.get(theme_name, {})
        if not matched:
            results.append({"theme": theme_name, "n_stocks": 0, "trend_score": 0.0, "sentiment_score": 0.0, "composite_score": 0.0})
            continue

        mcap_dict = {}
        if not daily_basic.empty:
            mcap_dict = {r["ts_code"]: r for _, r in daily_basic.iterrows()}

        industry_list = cfg.get("industry", [])
        concept_list = cfg.get("concept", [])
        keyword_list = cfg.get("keywords", [])

        rows = []
        for code, meta in matched.items():
            kdf = kline_groups.get(code)
            if kdf is None or len(kdf) < 6:
                continue
            feat = per_stock_features(kdf)
            if feat is None:
                continue

            # 合并换手率（从daily_basic获取）
            if not daily_basic.empty:
                db_one = daily_basic[daily_basic['ts_code'] == code]
                if not db_one.empty:
                    turnover = db_one.iloc[0].get('turnover_rate', 0) or 0
                    feat['turnover'] = float(turnover)

            concepts = stock_concepts.get(code, [])
            concepts_str = "|".join(concepts)
            purity = 0
            for kw in keyword_list:
                if kw in concepts_str:
                    purity += 1
            for c in concept_list:
                if c in concepts:
                    purity += 1
            if _in_industry_list(stock_industry.get(code, ""), industry_list):
                purity += 1

            mv = mcap_dict.get(code, {}).get("total_mv", 0) or 0
            feat["ts_code"] = code
            feat["name"] = name_map_basic.get(code, code)
            feat["purity"] = purity
            feat["total_mv"] = mv
            feat["industry_match"] = meta.get("industry_match", False)
            # 添加热榜排名分数（50%权重）
            feat["hot_rank_score"] = get_stock_hot_rank(code)
            rows.append(feat)

        if len(rows) < MIN_STOCKS:
            results.append({"theme": theme_name, "n_stocks": len(rows), "trend_score": 0.0, "sentiment_score": 0.0, "composite_score": 0.0})
            rows_per_theme[theme_name] = []
            continue

        # =========================
        # 统计全成份股的涨停数（用于情绪分计算）
        # =========================
        all_rows = rows  # 全部成份股
        all_zt_count = sum(1 for r in all_rows if r.get("zt_flag") == 1)
        all_up_count = sum(1 for r in all_rows if r.get("pct_chg", 0) > 0)
        all_down_count = sum(1 for r in all_rows if r.get("pct_chg", 0) < 0)
        all_total = len(all_rows)

        # =========================
        # 按市值权重排序，取前30只用于趋势分计算
        # =========================
        for r in rows:
            r["mcap_w"] = (r["total_mv"] / 10000) ** 0.5 * 0.8 + r["purity"] * 2
            r["mcap_w"] *= 1.0 if r["industry_match"] else 0.5
        rows.sort(key=lambda x: x["mcap_w"], reverse=True)
        top_rows = rows[:TOP_N_PER_THEME]  # 前30只用于趋势分计算

        t_score, t_detail = calc_trend_score(top_rows, market_ret_10)
        s_score, s_detail = calc_sentiment_score(all_rows, market_ret_10)  # 情绪分用全成份股计算
        
        # =========================
        # 计算主题热度得分（基于热榜数据）
        # =========================
        hot_score, hot_detail = calc_theme_hot_score(all_rows)
        hot_percentile, _ = get_theme_hot_score_percentile(theme_name, hot_score, days=60)
        hot_phase, hot_warning = judge_hot_phase(
            hot_score=hot_score,
            percentile=hot_percentile,
            top10_count=hot_detail.get('top10_count', 0),
            top5_count=hot_detail.get('top5_count', 0),
            total_stocks=all_total
        )
        
        composite = round(0.55 * t_score + 0.45 * s_score, 1)

        # 判断主题状态
        theme_result = {
            "theme": theme_name, "n_stocks": all_total, "trend_score": t_score, "sentiment_score": s_score,
            "composite_score": composite, "trend_detail": t_detail, "sentiment_detail": s_detail,
            "hot_score": round(hot_score, 2), "hot_percentile": hot_percentile, "hot_phase": hot_phase, "hot_warning": hot_warning,
            "hot_detail": hot_detail,
        }
        prev_data = prev_theme_data.get(theme_name)
        theme_state = calc_theme_state(theme_result, prev_data)
        theme_result["theme_state"] = theme_state

        # 风格属性（用于风格维度中期跟踪）
        theme_result["style"] = cfg.get("style", "未分类")

        leader_scores = []
        for r in top_rows:
            lb = r.get("lb_height", 0)
            pct = abs(r.get("pct_chg", 0))
            amt = r.get("amount_latest", 0)
            purity = r.get("purity", 0)
            ls = 0.4 * min(lb * 20, 100) + 0.3 * min(pct * 5, 100) + 0.2 * min(amt * 2, 100) + 0.1 * min(purity * 20, 100)
            leader_scores.append((r, ls))
        leader_scores.sort(key=lambda x: x[1], reverse=True)
        leader_stock = leader_scores[0][0] if leader_scores else None
        leader_name = leader_stock["name"] if leader_stock else ""
        leader_code = leader_stock["ts_code"] if leader_stock else ""

        # 中军：排除龙头股，从市值大(>200亿) + 纯度高的股票中选择
        leader_code_exclude = leader_stock["ts_code"] if leader_stock else ""
        core_candidates = [r for r in top_rows if r.get("total_mv", 0) > 2000000 and r.get("purity", 0) >= 1 and r.get("ts_code", "") != leader_code_exclude]
        core_scores = []
        for r in core_candidates:
            amt = r.get("amount_latest", 0)
            mv = r.get("total_mv", 0) / 10000
            pct = abs(r.get("pct_chg", 0))
            cs = 0.5 * min(amt * 2, 100) + 0.3 * min(mv / 10, 100) + 0.2 * min(pct * 5, 100)
            core_scores.append((r, cs))
        core_scores.sort(key=lambda x: x[1], reverse=True)
        core_stock = core_scores[0][0] if core_scores else None
        core_name = core_stock["name"] if core_stock else ""
        core_code = core_stock["ts_code"] if core_stock else ""

        # 添加龙头股和中军股信息
        theme_result["leader_name"] = leader_name
        theme_result["leader_code"] = leader_code
        theme_result["leader_score"] = round(leader_scores[0][1], 1) if leader_scores else 0
        theme_result["core_name"] = core_name
        theme_result["core_code"] = core_code
        theme_result["core_score"] = round(core_scores[0][1], 1) if core_scores else 0

        results.append(theme_result)
        rows_per_theme[theme_name] = top_rows

    # 预先计算10日/20日稳定性
    top10_10d = get_top10_stability(TRADE_DATE, days=10)
    top10_20d = get_top10_stability(TRADE_DATE, days=20)
    for r in results:
        r["top10_days_10d"] = top10_10d.get(r["theme"], 0)
        r["top10_days_20d"] = top10_20d.get(r["theme"], 0)

    results.sort(key=lambda x: x["composite_score"], reverse=True)
    for i, r in enumerate(results, 1):
        r["rank"] = i

    print("\n" + "=" * 120)
    print(f"{'排名':<4}{'主题':<14}{'成份':<6}{'趋势分':<8}{'情绪分':<8}{'综合分':<8}{'热度':<8}{'热度%':<6}{'阶段':<10}{'5日%':<7}{'10日%':<7}{'上涨%':<6}{'涨停':<6}{'状态':<10}{'10日稳':<7}{'20日稳':<7}")
    print("-" * 120)
    for r in results:
        td = r.get("trend_detail", {}) or {}
        sd = r.get("sentiment_detail", {}) or {}
        theme_state = r.get("theme_state", "弱势")
        hot_phase = r.get("hot_phase", "正常")
        print(f"{r['rank']:<4}{r['theme']:<14}{r['n_stocks']:<6}{r['trend_score']:<8}{r['sentiment_score']:<8}{r['composite_score']:<8}"
              f"{r.get('hot_score', 0):<8}{r.get('hot_percentile', 0):<6}{hot_phase:<10}"
              f"{td.get('avg_ret_5', 0):<7}{td.get('avg_ret_10', 0):<7}"
              f"{sd.get('up_ratio', 0):<6}{sd.get('zt_count', 0):<6}{theme_state:<10}"
              f"{r.get('top10_days_10d', 0):<7}{r.get('top10_days_20d', 0):<7}")
    print("=" * 120)

    # 输出热度预警信息
    hot_warnings = [r for r in results if r.get('hot_warning')]
    if hot_warnings:
        print("\n" + "=" * 100)
        print("🔥 热度预警提示")
        print("=" * 100)
        for r in hot_warnings:
            print(f"⚠️ {r['theme']}: {r['hot_warning']}")
        print("=" * 100)

    # 主题状态分布
    state_counts = {}
    for r in results:
        state = r.get("theme_state", "弱势")
        state_counts[state] = state_counts.get(state, 0) + 1
    print("\n" + "-" * 60)
    print("主题状态分布:")
    for state, cnt in sorted(state_counts.items(), key=lambda x: -x[1]):
        themes_in_state = [r["theme"] for r in results if r.get("theme_state") == state]
        print(f"  {state:<8}: {cnt:>2}个 → {', '.join(themes_in_state)}")
    print("-" * 60)

    print("\n" + "=" * 110)
    print("主题龙头/中军一览")
    print("=" * 110)
    print(f"{'排名':<4}{'主题':<14}{'龙头':<18}{'龙头评分':<10}{'中军':<18}{'中军评分':<10}")
    print("-" * 110)
    for r in results[:15]:
        ld = f"{r.get('leader_name', '')}({r.get('leader_code', '')})" if r.get("leader_name") else "-"
        cd = f"{r.get('core_name', '')}({r.get('core_code', '')})" if r.get("core_name") else "-"
        print(f"{r['rank']:<4}{r['theme']:<14}{ld:<18}{r.get('leader_score', 0):<10}{cd:<18}{r.get('core_score', 0):<10}")
    print("=" * 110)

    # ========== 风格维度中期跟踪 ==========
    style_rankings, style_text = analyze_style_trend(results)
    print(style_text)
    print()

    save_to_csv(results)
    save_to_sqlite(results)
    save_report_text(results)

    # ========== 渤海证券轮动监测 ==========
    print("\n" + "=" * 60)
    rotation_info = calc_rotation_monitoring(ndays=20)
    print(rotation_info["details"])
    print("=" * 60)

    print(f"\n[Save] CSV: {OUTPUT_CSV}")
    print(f"[Save] DB : {OUTPUT_DB}")


def run_theme_analysis():
    """供外部调用的主题分析入口，返回 results"""
    hot_themes = load_theme_json()
    dc_df = get_dc_members()
    stock_basic = get_stock_basic()
    daily_basic = get_daily_basic()
    theme_stock_map, name_map_basic, stock_industry, stock_concepts = match_theme_stocks(hot_themes, dc_df, stock_basic)

    all_codes = set()
    for tn, m in theme_stock_map.items():
        all_codes.update(m.keys())

    kline_df = get_daily_kline(list(all_codes), START_DATE, TRADE_DATE)
    idx_df = get_index_kline("000300.SH")
    market_ret_10 = 0.0
    if idx_df is not None and not idx_df.empty:
        idx_df = idx_df.sort_values("trade_date")
        closes = idx_df["close"].astype(float).values
        if len(closes) >= 11:
            market_ret_10 = (closes[-1] / closes[-11] - 1) * 100

    kline_groups = {}
    if not kline_df.empty:
        for code, sub in kline_df.groupby("ts_code"):
            kline_groups[code] = sub

    results = []
    rows_per_theme = {}
    for theme_name, cfg in hot_themes.items():
        matched = theme_stock_map.get(theme_name, {})
        if not matched:
            results.append({"theme": theme_name, "n_stocks": 0, "trend_score": 0.0, "sentiment_score": 0.0, "composite_score": 0.0})
            continue

        mcap_dict = {}
        if not daily_basic.empty:
            mcap_dict = {r["ts_code"]: r for _, r in daily_basic.iterrows()}

        industry_list = cfg.get("industry", [])
        concept_list = cfg.get("concept", [])
        keyword_list = cfg.get("keywords", [])

        rows = []
        for code, meta in matched.items():
            kdf = kline_groups.get(code)
            if kdf is None or len(kdf) < 6:
                continue
            feat = per_stock_features(kdf)
            if feat is None:
                continue

            # 合并换手率（从daily_basic获取）
            if not daily_basic.empty:
                db_one = daily_basic[daily_basic['ts_code'] == code]
                if not db_one.empty:
                    turnover = db_one.iloc[0].get('turnover_rate', 0) or 0
                    feat['turnover'] = float(turnover)

            concepts = stock_concepts.get(code, [])
            concepts_str = "|".join(concepts)
            purity = 0
            for kw in keyword_list:
                if kw in concepts_str:
                    purity += 1
            for c in concept_list:
                if c in concepts:
                    purity += 1
            if _in_industry_list(stock_industry.get(code, ""), industry_list):
                purity += 1

            mv = mcap_dict.get(code, {}).get("total_mv", 0) or 0
            feat["ts_code"] = code
            feat["name"] = name_map_basic.get(code, code)
            feat["purity"] = purity
            feat["total_mv"] = mv
            feat["industry_match"] = meta.get("industry_match", False)
            # 添加热榜排名分数（50%权重）
            feat["hot_rank_score"] = get_stock_hot_rank(code)
            rows.append(feat)

        if len(rows) < MIN_STOCKS:
            results.append({"theme": theme_name, "n_stocks": len(rows), "trend_score": 0.0, "sentiment_score": 0.0, "composite_score": 0.0})
            rows_per_theme[theme_name] = []
            continue

        # =========================
        # 统计全成份股的涨停数（用于情绪分计算）
        # =========================
        all_rows = rows  # 全部成份股
        all_zt_count = sum(1 for r in all_rows if r.get("zt_flag") == 1)
        all_up_count = sum(1 for r in all_rows if r.get("pct_chg", 0) > 0)
        all_down_count = sum(1 for r in all_rows if r.get("pct_chg", 0) < 0)
        all_total = len(all_rows)

        # =========================
        # 按市值权重排序，取前30只用于趋势分计算
        # =========================
        for r in rows:
            r["mcap_w"] = (r["total_mv"] / 10000) ** 0.5 * 0.8 + r["purity"] * 2
            r["mcap_w"] *= 1.0 if r["industry_match"] else 0.5
        rows.sort(key=lambda x: x["mcap_w"], reverse=True)
        top_rows = rows[:TOP_N_PER_THEME]  # 前30只用于趋势分计算

        t_score, t_detail = calc_trend_score(top_rows, market_ret_10)
        s_score, s_detail = calc_sentiment_score(all_rows, market_ret_10)  # 情绪分用全成份股计算
        
        composite = round(0.55 * t_score + 0.45 * s_score, 1)

        results.append({
            "theme": theme_name, "n_stocks": all_total, "trend_score": t_score, "sentiment_score": s_score,
            "composite_score": composite, "trend_detail": t_detail, "sentiment_detail": s_detail
        })
        rows_per_theme[theme_name] = top_rows

    results.sort(key=lambda x: x["composite_score"], reverse=True)
    for i, r in enumerate(results, 1):
        r["rank"] = i

    return results


def get_prev_day_theme_data():
    """获取前一日的主题数据（用于判断状态变化）"""
    if not os.path.exists(OUTPUT_DB):
        return {}
    
    conn = sqlite3.connect(OUTPUT_DB)
    cur = conn.cursor()
    
    # 获取当前 TRADE_DATE 之前的最新一个交易日数据
    cur.execute("SELECT DISTINCT trade_date FROM theme_scores WHERE trade_date < ? ORDER BY trade_date DESC LIMIT 1", (TRADE_DATE,))
    row = cur.fetchone()
    
    if row is None:
        conn.close()
        return {}
    
    prev_date = row[0]
    
    # 检查表结构，获取所有列名
    cur.execute("PRAGMA table_info(theme_scores)")
    columns = [row[1] for row in cur.fetchall()]
    
    # 选择基本列
    select_cols = ["theme", "trend_score", "sentiment_score", "ret_5", "ret_10", "ret_20", "up_ratio", "zt_count"]
    if "theme_state" in columns:
        select_cols.append("theme_state")
    
    # 构建查询
    col_str = ", ".join(select_cols)
    cur.execute(f"SELECT {col_str} FROM theme_scores WHERE trade_date = ?", (prev_date,))
    rows = cur.fetchall()
    
    prev_data = {}
    for row in rows:
        theme = row[0]
        theme_state = row[-1] if "theme_state" in columns else "弱势"
        prev_data[theme] = {
            "trend_score": row[1],
            "sentiment_score": row[2],
            "trend_detail": {
                "avg_ret_5": row[3],
                "avg_ret_10": row[4],
                "avg_ret_20": row[5],
            },
            "sentiment_detail": {
                "up_ratio": row[6],
                "zt_count": row[7],
            },
            "theme_state": theme_state,
        }
    
    conn.close()
    return prev_data


def calc_rotation_monitoring(ndays=20):
    """
    渤海证券轮动监测 — 主线稳定性 + 轮动速率

    核心融合逻辑：
    - 输入：连续N个交易日（默认20）的各主题 composite_score（综合评分）
    - 处理：
      a) 每日按 composite_score 对所有主题排名（未出现主题取最后一名）
      b) 轮动速率 = 相邻5日窗口排名变化绝对值之和 / 固定主题数 / 天数
      c) 主线稳定性 = Top5留存率（20日均值）
    - 主线存在条件：rotation_speed < 7.5 OR stability_index > 0.6

    Args:
        ndays: 分析天数，默认20个交易日

    Returns:
        dict: {
            rotation_speed: float,       # 轮动速率（归一化值，代表每日每主题平均排名位移）
            stability_index: float,      # 主线稳定性 0~1
            market_stage: str,           # 市场阶段文本
            n_themes: int,               # 参与统计的主题数
            n_days_actual: int,          # 实际有效的交易日数
            details: str                 # 详细分析文本
        }
    """
    import sqlite3
    from collections import defaultdict

    if not os.path.exists(OUTPUT_DB):
        return {"rotation_speed": 0, "stability_index": 0,
                "market_stage": "数据不足", "n_themes": 0,
                "n_days_actual": 0, "details": "数据库不存在"}

    conn = sqlite3.connect(OUTPUT_DB)
    cur = conn.cursor()

    # 获取最近 ndays 个交易日
    cur.execute("SELECT DISTINCT trade_date FROM theme_scores ORDER BY trade_date DESC LIMIT ?",
                (ndays * 2,))
    all_dates = [row[0] for row in cur.fetchall()]
    all_dates.reverse()  # 从旧到新
    if len(all_dates) < 5:
        conn.close()
        return {"rotation_speed": 0, "stability_index": 0,
                "market_stage": "数据不足（<5个交易日）", "n_themes": 0,
                "n_days_actual": len(all_dates), "details": ""}

    # 取最近 ndays 个交易日
    dates = all_dates[-ndays:]
    n_days_actual = len(dates)

    # 获取所有主题 composite_score
    placeholders = ','.join(['?' for _ in dates])
    query = f"""
        SELECT trade_date, theme, composite_score, trend_score, sentiment_score
        FROM theme_scores
        WHERE trade_date IN ({placeholders})
          AND composite_score IS NOT NULL
          AND composite_score > 0
        ORDER BY trade_date, composite_score DESC
    """
    cur.execute(query, dates)
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return {"rotation_speed": 0, "stability_index": 0,
                "market_stage": "数据不足", "n_themes": 0,
                "n_days_actual": 0, "details": "无有效主题数据"}

    # ========== 获取全量主题（所有出现过的主题构成固定集合）==========
    all_theme_names = set()
    for trade_date, theme, cs, ts, ss in rows:
        all_theme_names.add(theme)
    total_themes = len(all_theme_names)  # 固定分母（如58）
    theme_list = sorted(all_theme_names)

    # ========== 构建每日排名（含未出现主题=最后一名）==========
    daily_raw = defaultdict(list)
    for trade_date, theme, cs, ts, ss in rows:
        daily_raw[trade_date].append((theme, cs))

    # 构建每日全量排名：未出现的主题取 composite_score=0
    daily_rankings = defaultdict(list)  # date -> [(theme, composite_score, rank), ...]
    for date_key in sorted(daily_raw.keys()):
        scored = {t: cs for t, cs in daily_raw[date_key]}
        all_with_scores = [(t, scored.get(t, 0.0)) for t in theme_list]
        all_with_scores.sort(key=lambda x: -x[1])  # 按综合分降序
        for rank, (theme, cs) in enumerate(all_with_scores, 1):
            daily_rankings[date_key].append((theme, cs, rank))

    date_list = sorted(daily_rankings.keys())

    # ========== 1. 计算轮动速率 ==========
    # rotation_speed = sum(abs(rank_change)) / 58
    # 对每个可用的5日窗口计算，按天数归一化后取平均
    rotation_speeds = []

    for i in range(len(date_list)):
        for j in range(i + 1, min(i + 6, len(date_list))):
            gap = j - i
            if gap < 4:  # 至少间隔5天（0-4）
                continue
            date_a = date_list[i]
            date_b = date_list[j]
            ranking_a = {t: r for t, _, r in daily_rankings[date_a]}
            ranking_b = {t: r for t, _, r in daily_rankings[date_b]}

            # 固定分母：全量58个主题，缺失主题已有最后一名排名
            total_rank_change = sum(abs(ranking_a[t] - ranking_b[t]) for t in theme_list)
            avg_change = total_rank_change / total_themes / gap  # 每日每主题位移
            rotation_speeds.append(avg_change)

    rotation_speed = sum(rotation_speeds) / len(rotation_speeds) if rotation_speeds else 0

    # ========== 2. 计算主线稳定性 ==========
    # Top10留存率20日均值（按照主题数TOP_K = max(5, round(total_theme * 0.17))，渤海原值为5，我们为10
    stability_values = []

    for i in range(len(date_list) - 1):
        date_curr = date_list[i]
        date_next = date_list[i + 1]
        top5_curr = set(t for t, _, r in daily_rankings[date_curr] if r <= 10)
        top5_next = set(t for t, _, r in daily_rankings[date_next] if r <= 10)
        if len(top5_curr) == 0 or len(top5_next) == 0:
            continue
        overlap = len(top5_curr & top5_next)
        jaccard = overlap / max(len(top5_curr | top5_next), 1)
        stability_values.append(jaccard)

    stability_index = sum(stability_values) / len(stability_values) if stability_values else 0

    # ========== 3. 判断市场阶段（四象限）==========
    #
    #              │ stability > 0.6    │ stability < 0.4
    # ─────────────┼────────────────────┼────────────────────
    # speed > 7.5  │ 象限一：主线明确   │ 象限三：混乱轮动
    #              │ 轮动加快           │ 无主线
    # ─────────────┼────────────────────┼────────────────────
    # speed ≤ 7.5  │ 象限二：最强状态   │ 象限四：方向不明
    #              │ 共识强化           │ 混沌期
    #
    speed_up = rotation_speed > 7.5
    stable_high = stability_index > 0.6
    stable_low = stability_index < 0.4

    if speed_up and stable_high:
        # 象限一：主线明确，轮动加快
        market_stage = "主线明确，轮动加快"
        stage_desc = (f"旋转速率{rotation_speed:.2f}>7.5（轮动加快），"
                      f"稳定性{stability_index:.2f}>0.6（主线仍在），"
                      f"坚守主线，只做核心龙头，不碰边缘题材")
    elif not speed_up and stable_high:
        # 象限二：最强状态 - 共识强化
        market_stage = "共识强化🔥"
        stage_desc = (f"旋转速率{rotation_speed:.2f}≤7.5（轮动放缓），"
                      f"稳定性{stability_index:.2f}>0.6（共识稳固），"
                      f"集中仓位，重仓主线，允许追高")
    elif speed_up and stable_low:
        # 象限三：混乱轮动，无主线
        market_stage = "混乱轮动"
        stage_desc = (f"旋转速率{rotation_speed:.2f}>7.5（轮动加快），"
                      f"稳定性{stability_index:.2f}<0.4（无主线），"
                      f"降低仓位，快进快出，不格局")
    elif not speed_up and stable_low:
        # 象限四：方向不明，混沌期
        market_stage = "混沌期"
        stage_desc = (f"旋转速率{rotation_speed:.2f}≤7.5（轮动放缓），"
                      f"稳定性{stability_index:.2f}<0.4（无共识），"
                      f"只做最强主题，不碰边缘题材，快进快出")
    else:
        # 过渡态（0.4 ≤ stability ≤ 0.6）
        if not speed_up:
            market_stage = "过渡期（偏低速）"
            stage_desc = (f"旋转速率{rotation_speed:.2f}≤7.5，"
                          f"稳定性{stability_index:.2f}在0.4~0.6之间，"
                          f"方向待确认，轻仓试探")
        else:
            market_stage = "过渡期（偏轮动）"
            stage_desc = (f"旋转速率{rotation_speed:.2f}>7.5，"
                          f"稳定性{stability_index:.2f}在0.4~0.6之间，"
                          f"轮动加快但主线尚未完全散，谨慎操作")

    # ========== 4. 构建详情文本 ==========
    lines = []
    lines.append("─" * 60)
    lines.append("【渤海证券轮动监测】")
    lines.append(f"分析区间: {date_list[0]} ~ {date_list[-1]} ({n_days_actual}个交易日)")
    lines.append(f"参与主题: {total_themes} 个")
    lines.append("─" * 60)

    # 最新排名
    latest_date = date_list[-1]
    lines.append(f"\n最新({latest_date})主题排名 Top10:")
    lines.append(f"{'排名':<4}{'主题':<18}{'composite_score':<10}")
    for theme, cs, rank in daily_rankings[latest_date][:10]:
        lines.append(f"  {rank:<2}  {theme:<18}{cs:<10.1f}")
    lines.append("")

    # 稳定性时间序列
    lines.append("主线稳定性（Top10留存率）:")
    for i in range(max(0, len(stability_values) - 5), len(stability_values)):
        if i < len(stability_values) and i + 1 < len(date_list):
            lines.append(f"  {date_list[i]}→{date_list[i+1]}: {stability_values[i]:.2f}")
    lines.append(f"  20日均值: {stability_index:.3f}")
    lines.append("")

    # 轮动速率
    lines.append(f"轮动速率（全量{total_themes}主题每日每主题排名位移）:")
    lines.append(f"  rotation_speed = {rotation_speed:.2f}  (阈值 7.5)")
    lines.append(f"  stability_index = {stability_index:.3f}  (阈值 0.6)")
    lines.append("")

    # 市场阶段结论
    lines.append(f"【市场阶段】: {market_stage}")
    lines.append(f"策略建议: {stage_desc}")
    lines.append("─" * 60)

    details = "\n".join(lines)

    return {
        "rotation_speed": round(rotation_speed, 3),
        "stability_index": round(stability_index, 3),
        "market_stage": market_stage,
        "n_themes": total_themes,
        "n_days_actual": n_days_actual,
        "details": details
    }


def get_top10_stability(trade_date, days=10):
    """
    计算每个主题在过去N个交易日内位于前十名的天数
    
    Args:
        trade_date: 当前交易日期
        days: 统计天数（10或20）
    
    Returns:
        dict: {theme_name: top10_days_count}
    """
    import sqlite3
    from collections import defaultdict
    
    if not os.path.exists(OUTPUT_DB):
        return {}
    
    conn = sqlite3.connect(OUTPUT_DB)
    cur = conn.cursor()
    
    # 获取最近的N个交易日（包含当天）
    cur.execute("SELECT DISTINCT trade_date FROM theme_scores ORDER BY trade_date DESC")
    all_dates = [row[0] for row in cur.fetchall()]
    
    # 找到目标日期的位置
    if trade_date not in all_dates:
        conn.close()
        return {}
    
    idx = all_dates.index(trade_date)
    recent_dates = all_dates[idx:idx + days]
    
    if len(recent_dates) < 2:  # 至少需要2天才能计算
        conn.close()
        return {}
    
    # 查询这些日期内每个主题的排名
    placeholders = ','.join(['?' for _ in recent_dates])
    cur.execute(f"""
        SELECT trade_date, theme, rank 
        FROM theme_scores 
        WHERE trade_date IN ({placeholders})
    """, recent_dates)
    
    # 统计每个主题在前十的天数
    theme_top10_days = defaultdict(int)
    theme_total_days = defaultdict(int)
    
    for td, theme, rank in cur.fetchall():
        theme_total_days[theme] += 1
        if rank <= 10:
            theme_top10_days[theme] += 1
    
    conn.close()
    
    # 计算稳定性（出现在前十的天数）
    stability = {}
    for theme in theme_total_days:
        # 只有在该主题出现天数>=3时才计算稳定性（避免数据太少）
        if theme_total_days[theme] >= 3:
            stability[theme] = theme_top10_days[theme]
    
    return stability


def get_60day_avg_trend_score():
    """
    从SQLite数据库读取历史数据，计算每个主题的前60个交易日趋势分平均值
    
    Returns:
        dict: {theme_name: avg_trend_score}
    """
    import sqlite3
    from collections import defaultdict
    
    if not os.path.exists(OUTPUT_DB):
        print(f"[60天平均] 数据库不存在: {OUTPUT_DB}")
        return {}
    
    conn = sqlite3.connect(OUTPUT_DB)
    cur = conn.cursor()
    
    # 获取所有可用的交易日期（按倒序）
    cur.execute("SELECT DISTINCT trade_date FROM theme_scores ORDER BY trade_date DESC")
    dates = [row[0] for row in cur.fetchall()]
    
    if not dates:
        print("[60天平均] 数据库中无数据")
        conn.close()
        return {}
    
    # 取最新的60个交易日
    recent_dates = dates[:60]
    print(f"[60天平均] 使用 {len(recent_dates)} 个交易日的数据")
    
    # 查询这些日期的所有主题趋势分
    placeholders = ','.join(['?' for _ in recent_dates])
    cur.execute(f"SELECT theme, trend_score FROM theme_scores WHERE trade_date IN ({placeholders})", recent_dates)
    rows = cur.fetchall()
    
    # 计算每个主题的平均趋势分
    theme_scores = defaultdict(list)
    for theme, score in rows:
        if score is not None and score > 0:
            theme_scores[theme].append(score)
    
    # 计算平均值
    theme_avg = {}
    for theme, scores in theme_scores.items():
        if len(scores) >= 10:  # 至少要有10天数据才有效
            avg = sum(scores) / len(scores)
            theme_avg[theme] = avg
            print(f"   {theme}: {avg:.2f} ({len(scores)}天)")
    
    conn.close()
    return theme_avg


def main_for_date(target_date, hot_themes, dc_df, stock_basic, daily_basic, theme_stock_map, name_map_basic, stock_industry, stock_concepts):
    """
    为指定日期运行分析（用于批量回溯，复用主题和成分股对应关系）
    """
    global TRADE_DATE, START_DATE
    
    # 保存原始日期
    original_date = TRADE_DATE
    
    try:
        # 设置目标日期
        TRADE_DATE = target_date
        START_DATE = (datetime.strptime(TRADE_DATE, "%Y%m%d") - timedelta(days=N_DAYS + 30)).strftime("%Y%m%d")
        print(f"\n{'='*60}")
        print(f"处理日期: {TRADE_DATE}")
        print(f"{'='*60}")
        
        # 获取K线数据
        all_codes = set()
        for tn, m in theme_stock_map.items():
            all_codes.update(m.keys())
        kline_df = get_daily_kline(list(all_codes), START_DATE, TRADE_DATE)
        
        # 获取指数数据
        idx_df = get_index_kline("000300.SH")
        market_ret_10 = 0.0
        if idx_df is not None and not idx_df.empty:
            idx_df = idx_df.sort_values('trade_date')
            closes = idx_df['close'].astype(float).values
            if len(closes) >= 11:
                market_ret_10 = (closes[-1] / closes[-11] - 1) * 100
        
        kline_groups = {}
        if not kline_df.empty:
            for code, sub in kline_df.groupby('ts_code'):
                kline_groups[code] = sub
        
        results = []
        rows_per_theme = {}
        
        for theme_name, cfg in hot_themes.items():
            matched = theme_stock_map.get(theme_name, {})
            if not matched:
                results.append({'theme': theme_name, 'n_stocks': 0, 'trend_score': 0.0, 'sentiment_score': 0.0, 'composite_score': 0.0})
                continue
            
            mcap_dict = {}
            if not daily_basic.empty:
                mcap_dict = {r['ts_code']: r for _, r in daily_basic.iterrows()}
            
            industry_list = cfg.get('industry', [])
            concept_list = cfg.get('concept', [])
            keyword_list = cfg.get('keywords', [])
            
            rows = []
            for code, meta in matched.items():
                kdf = kline_groups.get(code)
                if kdf is None or len(kdf) < 6:
                    continue
                feat = per_stock_features(kdf)
                if feat is None:
                    continue
                
                # 合并换手率（从daily_basic获取）
                if not daily_basic.empty:
                    db_one = daily_basic[daily_basic['ts_code'] == code]
                    if not db_one.empty:
                        turnover = db_one.iloc[0].get('turnover_rate', 0) or 0
                        feat['turnover'] = float(turnover)
                
                concepts = stock_concepts.get(code, [])
                concepts_str = "|".join(concepts)
                purity = 0
                for kw in keyword_list:
                    if kw in concepts_str:
                        purity += 1
                for c in concept_list:
                    if c in concepts:
                        purity += 1
                if _in_industry_list(stock_industry.get(code, ""), industry_list):
                    purity += 1
                
                mv = mcap_dict.get(code, {}).get('total_mv', 0) or 0
                feat['ts_code'] = code
                feat['name'] = name_map_basic.get(code, code)
                feat['purity'] = purity
                feat['total_mv'] = mv
                feat['industry_match'] = meta.get('industry_match', False)
                # 添加热榜排名分数（50%权重）
                feat['hot_rank_score'] = get_stock_hot_rank(code)
                rows.append(feat)
            
            if len(rows) < MIN_STOCKS:
                results.append({'theme': theme_name, 'n_stocks': len(rows), 'trend_score': 0.0, 'sentiment_score': 0.0, 'composite_score': 0.0})
                rows_per_theme[theme_name] = []
                continue
            
            # =========================
            # 统计全成份股的涨停数（用于情绪分计算）
            # =========================
            all_rows = rows  # 全部成份股
            all_zt_count = sum(1 for r in all_rows if r.get("zt_flag") == 1)
            all_up_count = sum(1 for r in all_rows if r.get("pct_chg", 0) > 0)
            all_down_count = sum(1 for r in all_rows if r.get("pct_chg", 0) < 0)
            all_total = len(all_rows)

            # =========================
            # 按市值权重排序，取前30只用于趋势分计算
            # =========================
            for r in rows:
                r['mcap_w'] = (r['total_mv'] / 10000) ** 0.5 * 0.8 + r['purity'] * 2
                r['mcap_w'] *= 1.0 if r['industry_match'] else 0.5
            rows.sort(key=lambda x: x['mcap_w'], reverse=True)
            top_rows = rows[:TOP_N_PER_THEME]  # 前30只用于趋势分计算
            
            t_score, t_detail = calc_trend_score(top_rows, market_ret_10)
            s_score, s_detail = calc_sentiment_score(all_rows, market_ret_10)  # 情绪分用全成份股计算
            
            # =========================
            # 计算主题热度得分（基于热榜数据）
            # =========================
            hot_score, hot_detail = calc_theme_hot_score(all_rows)
            hot_percentile, _ = get_theme_hot_score_percentile(theme_name, hot_score, days=60)
            hot_phase, hot_warning = judge_hot_phase(
                hot_score=hot_score,
                percentile=hot_percentile,
                top10_count=hot_detail.get('top10_count', 0),
                top5_count=hot_detail.get('top5_count', 0),
                total_stocks=all_total
            )
            
            composite = round(0.55 * t_score + 0.45 * s_score, 1)
            
            leader_scores = []
            for r in top_rows:
                lb = r.get('lb_height', 0)
                pct = abs(r.get('pct_chg', 0))
                amt = r.get('amount_latest', 0)
                purity = r.get('purity', 0)
                ls = 0.4 * min(lb * 20, 100) + 0.3 * min(pct * 5, 100) + 0.2 * min(amt * 2, 100) + 0.1 * min(purity * 20, 100)
                leader_scores.append((r, ls))
            leader_scores.sort(key=lambda x: x[1], reverse=True)
            leader_stock = leader_scores[0][0] if leader_scores else None
            leader_name = leader_stock['name'] if leader_stock else ""
            leader_code = leader_stock['ts_code'] if leader_stock else ""
            
            core_candidates = [r for r in top_rows if r.get('total_mv', 0) > 2000000 and r.get('purity', 0) >= 1]
            core_scores = []
            for r in core_candidates:
                amt = r.get('amount_latest', 0)
                mv = r.get('total_mv', 0) / 10000
                pct = abs(r.get('pct_chg', 0))
                cs = 0.5 * min(amt * 2, 100) + 0.3 * min(mv / 10, 100) + 0.2 * min(pct * 5, 100)
                core_scores.append((r, cs))
            core_scores.sort(key=lambda x: x[1], reverse=True)
            core_stock = core_scores[0][0] if core_scores else None
            core_name = core_stock['name'] if core_stock else ""
            core_code = core_stock['ts_code'] if core_stock else ""
            
            results.append({
                'theme': theme_name, 'n_stocks': all_total, 'trend_score': t_score, 'sentiment_score': s_score,
                'composite_score': composite, 'trend_detail': t_detail, 'sentiment_detail': s_detail,
                'leader_name': leader_name, 'leader_code': leader_code, 'leader_score': round(leader_scores[0][1], 1) if leader_scores else 0,
                'core_name': core_name, 'core_code': core_code, 'core_score': round(core_scores[0][1], 1) if core_scores else 0,
                'hot_score': round(hot_score, 2), 'hot_percentile': hot_percentile, 'hot_phase': hot_phase, 'hot_warning': hot_warning,
                'hot_detail': hot_detail,
            })
            rows_per_theme[theme_name] = top_rows
        
        results.sort(key=lambda x: x["composite_score"], reverse=True)
        for i, r in enumerate(results, 1):
            r['rank'] = i
        
        # 预先计算10日/20日稳定性
        top10_10d = get_top10_stability(TRADE_DATE, days=10)
        top10_20d = get_top10_stability(TRADE_DATE, days=20)
        for r in results:
            r["top10_days_10d"] = top10_10d.get(r["theme"], 0)
            r["top10_days_20d"] = top10_20d.get(r["theme"], 0)
        
        # 补充 theme_state（按日期顺序处理时，前一交易日数据已在 DB 中）
        for r in results:
            theme_state = calc_theme_state(r, get_prev_day_theme_data().get(r['theme']))
            r['theme_state'] = theme_state
        
        # 保存到数据库
        save_to_sqlite(results)
        print(f"[保存完成] {TRADE_DATE} 数据已保存到数据库")
        
    finally:
        # 恢复原始日期
        TRADE_DATE = original_date


def backfill_last_n_days(n_days=60):
    """
    批量回溯最近N个交易日的数据
    
    Args:
        n_days: 回溯天数
    """
    print("=" * 80)
    print(f"批量回溯最近 {n_days} 个交易日")
    print("=" * 80)
    
    # 步骤1: 获取交易日历
    end_date = datetime.strptime(TRADE_DATE, "%Y%m%d")
    start_cal_date = end_date - timedelta(days=n_days * 2)  # 多取一些天数以防节假日
    
    cal = pro.trade_cal(exchange='', start_date=start_cal_date.strftime("%Y%m%d"), end_date=TRADE_DATE)
    cal = cal[cal['is_open'] == 1]
    trade_dates = sorted(cal['cal_date'].tolist(), reverse=True)[:n_days]
    trade_dates.reverse()  # 从旧到新处理
    
    print(f"待处理的 {len(trade_dates)} 个交易日: {trade_dates[0]} 到 {trade_dates[-1]}")
    
    # 步骤2: 只执行一次主题和成分股对应关系计算
    print("\n[初始化] 计算主题和成分股对应关系（只需一次）")
    hot_themes = load_theme_json()
    dc_df = get_dc_members()
    stock_basic = get_stock_basic()
    daily_basic = get_daily_basic()
    theme_stock_map, name_map_basic, stock_industry, stock_concepts = match_theme_stocks(hot_themes, dc_df, stock_basic)
    
    # 步骤3: 逐个日期处理
    print(f"\n[开始处理] 共 {len(trade_dates)} 个交易日")
    for i, target_date in enumerate(trade_dates, 1):
        print(f"\n[{i}/{len(trade_dates)}] 处理 {target_date}")
        try:
            main_for_date(target_date, hot_themes, dc_df, stock_basic, daily_basic, 
                          theme_stock_map, name_map_basic, stock_industry, stock_concepts)
        except Exception as e:
            print(f"处理 {target_date} 时出错: {e}")
            import traceback
            traceback.print_exc()
    
    print(f"\n[全部完成] 共处理 {len(trade_dates)} 个交易日")

    # 回溯完成后输出轮动监测
    print("\n" + "=" * 60)
    print("回溯完成后轮动监测报告")
    print("=" * 60)
    rotation_info = calc_rotation_monitoring(ndays=20)
    print(rotation_info["details"])
    print("=" * 60)


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2:
        if sys.argv[1] == "backfill":
            # 批量回溯模式
            n_days = int(sys.argv[2]) if len(sys.argv) >= 3 else 60
            backfill_last_n_days(n_days)
        else:
            # 单个日期回溯模式
            TRADE_DATE = sys.argv[1]
            START_DATE = (datetime.strptime(TRADE_DATE, "%Y%m%d") - timedelta(days=N_DAYS + 30)).strftime("%Y%m%d")
            print(f"[Backfill] 回溯模式: {TRADE_DATE}")
            main()
    else:
        main()
