#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
大盘分析与仓位建议
- 分析大盘情绪和趋势
- 游资标准仓位建议
"""
import sys

# Windows GBK 控制台输出修复:安全方式（Python 3.7+）
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import os
import time
import sqlite3
import numpy as np
import pandas as pd
import tushare as ts
from datetime import datetime, timedelta
from dotenv import load_dotenv

# ================
# 环境配置
# ================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 阻止 tushare 写入根目录
original_expanduser = os.path.expanduser
safe_cache_dir = os.path.join(BASE_DIR, 'cache_backbone_tushare')
os.makedirs(safe_cache_dir, exist_ok=True)

def safe_expanduser(path):
    if '~/tk.csv' in path or '\\tk.csv' in path or 'tk.csv' in path:
        return os.path.join(safe_cache_dir, 'tk.csv')
    return original_expanduser(path)

os.path.expanduser = safe_expanduser

# 加载环境变量
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', '.env'))
TUSHARE_TOKEN = os.getenv('TUSHARE_TOKEN')
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

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
START_DATE = (datetime.strptime(TRADE_DATE, '%Y%m%d') - timedelta(days=90)).strftime('%Y%m%d')
print(f"[Init] 交易日: {TRADE_DATE}  分析区间: {START_DATE} ~ {TRADE_DATE}")

# ================
# 缓存函数（SQLite版本）
# ================
DB_PATH = os.path.join(safe_cache_dir, 'cache.db')

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

def cache_get(name, trade_date=None, **kwargs):
    if trade_date is None:
        trade_date = TRADE_DATE
    key = "_".join([name] + [f"{k}_{v}" for k, v in sorted(kwargs.items())])
    safe = key.replace("/", "_").replace(":", "_")
    cache_key = f"tsc_{safe}_{trade_date}"
    
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

def cache_set(name, data, trade_date=None, expire_hours=None, **kwargs):
    if trade_date is None:
        trade_date = TRADE_DATE
    key = "_".join([name] + [f"{k}_{v}" for k, v in sorted(kwargs.items())])
    safe = key.replace("/", "_").replace(":", "_")
    cache_key = f"tsc_{safe}_{trade_date}"
    
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

# 初始化数据库
init_db()

# ================
# 获取指数数据
# ================
def get_index_kline(ts_code="000300.SH", trade_date=None):
    if trade_date is None:
        trade_date = TRADE_DATE
        start_date = START_DATE
    else:
        start_date = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=90)).strftime("%Y%m%d")
    
    cache_key = f"index_kline_{ts_code}_{start_date}_{trade_date}"
    cached = cache_get(name="index_kline", trade_date=trade_date, ts_code=ts_code)
    
    # 检查缓存数据是否包含目标日期（避免缓存其他日期的数据）
    if cached is not None:
        if 'trade_date' in cached.columns and not cached.empty:
            max_date = str(cached['trade_date'].max())
            if max_date == str(trade_date):
                print(f"[Index] 缓存命中且包含目标日期: {ts_code} ({trade_date})")
                return cached
            else:
                print(f"[Index] 缓存数据不匹配（最新日期: {max_date}, 需要: {trade_date}），重新拉取")
        else:
            print(f"[Index] 缓存数据格式异常，重新拉取")
    
    print(f"[Index] 拉取 {ts_code} 数据: {start_date} ~ {trade_date}")
    df = pro.index_daily(ts_code=ts_code, start_date=start_date, end_date=trade_date)
    if df is None or df.empty:
        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=trade_date)
    
    if df is not None and not df.empty:
        df = df.sort_values('trade_date').reset_index(drop=True)
        cache_set(name="index_kline", data=df, trade_date=trade_date, ts_code=ts_code)
        print(f"[Index] 数据已缓存: {trade_date}")
    else:
        print(f"[Index] 未获取到数据")
    
    return df

# ================
# 计算趋势指标（新方案：游资视角）
# ================
def calc_trend_score(df, up_count=0, total_count=3000):
    """
    趋势分 = MA_SCORE(50) + INDEX_SCORE(30) + BREADTH_SCORE(20)
    
    MA_SCORE（50分）- 均线趋势 + 动量加速（权重提升，降低单日广度波动影响）：
    - 多头排列 + 加速上涨    50
    - 多头排列但减速        40
    - 多头平稳              45
    - MA5 > MA10            38
    - MA5 > MA20            25
    - 空头但企稳             22
    - 全空头排列             12
    - 其他                  18
    
    INDEX_SCORE（30分）- 指数站位：
    - 站上20日线 30
    - 站上10日线 20
    - 站上5日线 10
    - 全部跌破 0
    
    BREADTH_SCORE（20分）- 市场广度（降低权重，防止单日广度波动驱动仓位跳变）：
    - >70%    20
    - 60%     17
    - 50%     14
    - 40%      8
    - 30%      4
    - <30%     0
    """
    if df is None or len(df) < 20:
        return 50.0, "无数据", {}
    
    latest = df.iloc[-1]
    
    # 计算均线
    ma5 = df['close'].rolling(5).mean().iloc[-1]
    ma10 = df['close'].rolling(10).mean().iloc[-1]
    ma20 = df['close'].rolling(20).mean().iloc[-1]
    
    # 计算MA斜率（动量加速因子）
    ma5_series = df['close'].rolling(5).mean()
    ma20_series = df['close'].rolling(20).mean()
    ma5_slope = 0.0
    ma20_slope = 0.0
    if len(ma5_series) >= 6 and not pd.isna(ma5_series.iloc[-6]) and ma5_series.iloc[-6] > 0:
        ma5_slope = (ma5 - ma5_series.iloc[-6]) / ma5_series.iloc[-6] * 100
    if len(ma20_series) >= 21 and not pd.isna(ma20_series.iloc[-21]) and ma20_series.iloc[-21] > 0:
        ma20_slope = (ma20 - ma20_series.iloc[-21]) / ma20_series.iloc[-21] * 100
    
    # -------------------
    # MA_SCORE（40分）- 均线趋势 + 动量加速修正
    # -------------------
    ma_score = 0
    momentum_note = ""
    if ma5 > ma10 > ma20:
        # 多头排列：判断加速还是减速
        if ma5_slope > 2 and ma20_slope > 0.5:
            ma_score = 50  # 加速上涨
            momentum_note = "加速上涨"
        elif ma5_slope < 0.5:
            ma_score = 40  # 多头但减速
            momentum_note = "多头减速"
        else:
            ma_score = 45  # 多头平稳
            momentum_note = "多头平稳"
    elif ma5 > ma10:
        ma_score = 38
        momentum_note = "短多头"
    elif ma5 > ma20:
        ma_score = 25
        momentum_note = "弱反弹"
    elif ma5 < ma10 < ma20:
        # 空头排列：判断是否企稳
        if ma5_slope > 0 and len(ma5_series) >= 4 and ma5 > ma5_series.iloc[-4]:
            ma_score = 22  # 空头但企稳
            momentum_note = "空头企稳"
        else:
            ma_score = 12
            momentum_note = "空头加速"
    else:
        ma_score = 18  # 其他情况
        momentum_note = "趋势不明"
    
    # -------------------
    # INDEX_SCORE（30分）- 指数站位
    # -------------------
    index_score = 0
    if latest['close'] > ma20:
        index_score = 30
    elif latest['close'] > ma10:
        index_score = 20
    elif latest['close'] > ma5:
        index_score = 10
    else:
        index_score = 0
    
    # -------------------
    # BREADTH_SCORE（30分）- 市场广度
    # -------------------
    breadth_score = 0
    if total_count > 0:
        up_ratio = up_count / total_count * 100
        if up_ratio > 70:
            breadth_score = 20
        elif up_ratio >= 60:
            breadth_score = 17
        elif up_ratio >= 50:
            breadth_score = 14
        elif up_ratio >= 40:
            breadth_score = 8
        elif up_ratio >= 30:
            breadth_score = 4
        else:
            breadth_score = 0
    else:
        # 如果没有上涨家数数据，用指数涨幅代替
        pct_chg = float(latest.get('pct_chg', 0))
        if pct_chg > 2:
            breadth_score = 17
        elif pct_chg > 1:
            breadth_score = 14
        elif pct_chg > 0:
            breadth_score = 10
        elif pct_chg > -1:
            breadth_score = 6
        else:
            breadth_score = 3
    
    # 计算总分
    trend_score = ma_score + index_score + breadth_score
    trend_score = max(0, min(100, trend_score))
    
    # 判断趋势状态（综合趋势分和均线排列）
    if trend_score >= 75 and ma5 > ma10 > ma20 and latest['close'] > ma5:
        trend_status = "上升趋势"
    elif trend_score >= 65 and ma5 > ma10:
        trend_status = "上升趋势"
    elif trend_score >= 60:
        trend_status = "震荡偏强"
    elif trend_score >= 45:
        trend_status = "震荡整理"
    elif trend_score >= 30:
        trend_status = "震荡偏弱"
    else:
        trend_status = "下降趋势"
    
    detail = {
        'ma_score': ma_score, 'index_score': index_score, 'breadth_score': breadth_score,
        'ma5_slope': round(ma5_slope, 2), 'ma20_slope': round(ma20_slope, 2),
        'momentum_note': momentum_note,
    }
    return trend_score, trend_status, detail

# ================
# 计算情绪指标
# ================
def calc_sentiment_score(df, zt_count=0, zhaban_rate=0.0, total_amount=0):
    """
    情绪评分 = 方向(25) + 量能(20) + 振幅(15) + 连涨跌(20) + 涨停质量(10) + 成交额趋势(10)
    
    新增维度：
    - 涨停封板质量（10分）：炸板率越低，情绪越强
    - 成交额趋势（10分）：5日/20日成交额比值
    """
    if df is None or len(df) < 20:
        return 50.0, "无数据"
    
    latest = df.iloc[-1]
    pct_chg = latest['pct_chg'] if 'pct_chg' in df.columns else 0
    
    # ==============================
    # 1. 涨跌方向与强度 (25分)
    # ==============================
    direction_score = 0
    if pct_chg >= 2:
        direction_score = 25
    elif pct_chg >= 1:
        direction_score = 18
    elif pct_chg >= 0:
        direction_score = 10
    elif pct_chg >= -1:
        direction_score = 5
    elif pct_chg >= -2:
        direction_score = 0
    else:
        direction_score = -15
    
    # ==============================
    # 2. 成交量变化 (20分) - 区分涨跌
    # ==============================
    vol5 = df['vol'].tail(5).mean()
    vol20 = df['vol'].tail(20).mean()
    vol_ratio = vol5 / vol20 if vol20 > 0 else 1
    
    vol_score = 10 + (vol_ratio - 1) * 15
    
    if pct_chg < -1 and vol_ratio > 1.2:
        vol_score -= 8   # 放量大跌额外扣
    elif pct_chg > 1 and vol_ratio > 1.2:
        vol_score += 4   # 放量大涨额外加
    
    vol_score = min(20, max(0, vol_score))
    
    # ==============================
    # 3. 振幅与涨跌结合 (15分)
    # ==============================
    amplitude = (latest['high'] - latest['low']) / latest['low'] * 100
    
    if pct_chg < 0:
        amp_score = max(0, 7.5 - amplitude * 0.5)
    else:
        amp_score = min(15, 7.5 + amplitude * 0.4)
    
    # ==============================
    # 4. 连涨连跌趋势 (20分)
    # ==============================
    up_streak = 0
    down_streak = 0
    
    for i in range(1, 6):
        if len(df) > i:
            if df['pct_chg'].iloc[-i] > 0:
                up_streak += 1
            else:
                break
    
    for i in range(1, 6):
        if len(df) > i:
            if df['pct_chg'].iloc[-i] < 0:
                down_streak += 1
            else:
                break
    
    streak_score = 10 + up_streak * 2.5 - down_streak * 3
    streak_score = min(20, max(0, streak_score))
    
    # ==============================
    # 5. 涨停封板质量 (10分) - 新增
    # ==============================
    zt_quality_score = 5  # 默认中性
    if zt_count > 0:
        # 炸板率越低，封板质量越高
        if zhaban_rate < 10:
            zt_quality_score = 10  # 情绪极强
        elif zhaban_rate < 20:
            zt_quality_score = 8
        elif zhaban_rate < 35:
            zt_quality_score = 5
        else:
            zt_quality_score = 2  # 炸板率高=情绪分歧
    elif zhaban_rate > 50:
        zt_quality_score = 0  # 极端炸板，情绪崩溃
    
    # ==============================
    # 6. 成交额趋势 (10分) - 新增
    # ==============================
    amount_trend_score = 5  # 默认中性
    if 'amount' in df.columns and len(df) >= 20:
        amt_5d = df['amount'].tail(5).mean()
        amt_20d = df['amount'].tail(20).mean()
        if amt_20d > 0:
            amt_ratio = amt_5d / amt_20d
            if amt_ratio > 1.5 and pct_chg > 0:
                amount_trend_score = 10  # 放量上涨
            elif amt_ratio > 1.2 and pct_chg > 0:
                amount_trend_score = 7
            elif amt_ratio > 1.2 and pct_chg < 0:
                amount_trend_score = 3  # 放量下跌=恐慌
            elif amt_ratio < 0.7:
                amount_trend_score = 2  # 缩量严重
            elif amt_ratio < 0.9:
                amount_trend_score = 4  # 缩量
    
    # ==============================
    # 7. 近期波动率惩罚 (额外)
    # ==============================
    vol20_std = df['pct_chg'].tail(20).std()
    volatility = vol20_std if not pd.isna(vol20_std) else 1.5
    
    vol_penalty = 0
    if volatility > 2.5:
        vol_penalty = (volatility - 2.5) * 3
    
    # ==============================
    # 综合评分
    # ==============================
    sentiment_score = (direction_score + vol_score + amp_score + streak_score 
                       + zt_quality_score + amount_trend_score - vol_penalty)
    sentiment_score = max(0, min(100, sentiment_score))
    
    if sentiment_score >= 70:
        sentiment_status = "情绪高涨"
    elif sentiment_score >= 50:
        sentiment_status = "情绪温和"
    elif sentiment_score >= 30:
        sentiment_status = "情绪低迷"
    else:
        sentiment_status = "情绪退潮"
    
    return sentiment_score, sentiment_status

# ================
# 游资标准仓位建议
# ================
def suggest_position(results):
    """
    游资仓位策略：
    - 强主线行情（有指数上升趋势）：总仓位最高7成
    - 市场震荡：3-5成轻仓
    - 大盘全面退潮（都下降）：1成以内
    仓位以5%为最小单位
    """
    # 分析各指数状态
    up_trend = [r for r in results if r['trend_status'] == '上升趋势']
    strong_trend = [r for r in results if r['trend_status'] in ['上升趋势', '震荡偏强']]
    down_trend = [r for r in results if r['trend_status'] == '下降趋势']
    
    # 统计情绪
    high_sentiment = [r for r in results if r['sentiment_status'] == '情绪高涨']
    
    if len(up_trend) >= 1 and len(high_sentiment) >= 1:
        # 有上升趋势且情绪高涨：强主线行情
        position = 70
        reason = f"有{len(up_trend)}个指数上升趋势+情绪高涨 → 强主线行情，可积极参与"
    elif len(strong_trend) >= 2:
        # 2个以上指数偏强：震荡偏强
        position = 50
        reason = f"{len(strong_trend)}个指数趋势偏强 → 市场震荡偏强，适度参与"
    elif len(down_trend) >= 2:
        # 2个以上指数下降：全面退潮
        position = 10
        reason = f"{len(down_trend)}个指数下降趋势 → 市场退潮，严控仓位"
    elif len(down_trend) >= 1:
        # 有1个指数下降：但还有其他机会
        position = 30
        reason = f"有{len(down_trend)}个指数下降，但仍有结构性机会 → 轻仓参与主线"
    else:
        # 其他情况：中性
        position = 40
        reason = "市场中性震荡 → 中等仓位"
    
    # 确保仓位是5的倍数
    position = round(position / 5) * 5
    
    return position, reason


# ================
# 大盘整体概况（成交额/涨跌家数/涨停跌停/炸板率）
# ================
def get_market_overview(trade_date=None):
    """获取大盘整体概况数据"""
    if trade_date is None:
        trade_date = TRADE_DATE
    
    overview = {
        "sh_index": 0, "sh_pct": 0,
        "total_amount": 0,
        "up_count": 0, "down_count": 0,
        "zt_count": 0, "dt_count": 0,
        "zb_count": 0, "zb_rate": 0,
    }

    try:
        # 上证指数（用 index_daily 接口）
        sh_df = pro.index_daily(ts_code="000001.SH", start_date=trade_date, end_date=trade_date)
        if sh_df is not None and not sh_df.empty:
            overview["sh_index"] = sh_df.iloc[0]["close"]
            overview["sh_pct"] = sh_df.iloc[0]["pct_chg"]

        # 全市场成交额（日线所有股票 amount 之和，千元→亿元）
        daily_df = pro.daily(trade_date=trade_date)
        if daily_df is not None and not daily_df.empty:
            total_amt = daily_df["amount"].sum() / 100000  # 千元→亿元
            overview["total_amount"] = round(total_amt, 0)
            up_count = (daily_df["pct_chg"] > 0).sum()
            down_count = (daily_df["pct_chg"] < 0).sum()
            overview["up_count"] = up_count
            overview["down_count"] = down_count

        time.sleep(0.3)

        # 涨停池
        zt_df = pro.limit_list_ths(trade_date=trade_date, limit_type="涨停池")
        if zt_df is not None and not zt_df.empty:
            overview["zt_count"] = len(zt_df)
        time.sleep(0.15)

        # 跌停池
        dt_df = pro.limit_list_ths(trade_date=trade_date, limit_type="跌停池")
        if dt_df is not None and not dt_df.empty:
            overview["dt_count"] = len(dt_df)
        time.sleep(0.15)

        # 炸板池
        zb_df = pro.limit_list_ths(trade_date=trade_date, limit_type="炸板池")
        if zb_df is not None and not zb_df.empty:
            overview["zb_count"] = len(zb_df)
        time.sleep(0.15)

        # 炸板率 = 炸板数 / (涨停数 + 炸板数)
        total_zt_zb = overview["zt_count"] + overview["zb_count"]
        if total_zt_zb > 0:
            overview["zb_rate"] = round(overview["zb_count"] / total_zt_zb * 100, 1)

    except Exception as e:
        print(f"   [市场概况] 获取失败: {e}")

    return overview


def print_market_overview(overview, trade_date=None):
    """打印大盘整体概况"""
    if trade_date is None:
        trade_date = TRADE_DATE
    print(f"\n📊 大盘整体概况（{trade_date}）")
    print(f"  {'='*50}")
    print(f"  上证指数: {overview['sh_index']:.2f}  ({overview['sh_pct']:+.2f}%)")
    print(f"  全市场成交额: {overview['total_amount']:.0f}亿")
    print(f"  上涨家数: {overview['up_count']}  下跌家数: {overview['down_count']}")
    print(f"  涨停: {overview['zt_count']}  跌停: {overview['dt_count']}  炸板: {overview['zb_count']}")
    print(f"  炸板率: {overview['zb_rate']}%")
    print(f"  {'='*50}")


# ================
# 主分析流程
# ================
def _get_prev_trend_score(trade_date=None):
    """从数据库读取前一交易日的趋势分（用于趋势确认机制）"""
    if trade_date is None:
        trade_date = TRADE_DATE
    try:
        db_path = os.path.join(safe_cache_dir, "market_analysis.db")
        if not os.path.exists(db_path):
            return None
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT trend_score FROM overall_analysis
            WHERE trade_date < ?
            ORDER BY trade_date DESC LIMIT 1
        ''', (trade_date,))
        row = cursor.fetchone()
        conn.close()
        if row and row[0] is not None:
            return float(row[0])
    except Exception:
        pass
    return None


def _get_prev_position(trade_date=None):
    """从数据库读取前一交易日的仓位（用于滞回机制）"""
    if trade_date is None:
        trade_date = TRADE_DATE
    try:
        db_path = os.path.join(safe_cache_dir, "market_analysis.db")
        if not os.path.exists(db_path):
            return None
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT total_position FROM overall_analysis
            WHERE trade_date < ?
            ORDER BY trade_date DESC LIMIT 1
        ''', (trade_date,))
        row = cursor.fetchone()
        conn.close()
        if row and row[0]:
            return int(row[0])
    except Exception:
        pass
    return None


def analyze_market(trade_date=None):
    if trade_date is None:
        trade_date = TRADE_DATE
    print("=" * 80)
    print(f"📊 大盘分析与仓位建议 ({trade_date})")
    print("=" * 80)

    # 大盘整体概况
    overview = get_market_overview(trade_date)
    print_market_overview(overview, trade_date)
    
    # 获取主要指数数据
    indices = {
        "上证指数": "000001.SH",
        "沪深300": "000300.SH",
        "中证2000": "932000.CSI"
    }
    
    results = []
    
    for name, code in indices.items():
        print(f"\n📈 正在分析: {name} ({code})")
        
        df = get_index_kline(code, trade_date)
        
        if df is None or df.empty:
            print(f"   ❌ 无数据")
            continue
        
        latest = df.iloc[-1]
        up_count = overview.get('up_count', 0)
        total_count = overview.get('up_count', 0) + overview.get('down_count', 0)
        trend_score, trend_status, trend_detail = calc_trend_score(df, up_count, total_count)
        zt_count = overview.get('zt_count', 0)
        zhaban_rate = overview.get('zb_rate', 0)
        sentiment_score, sentiment_status = calc_sentiment_score(df, zt_count, zhaban_rate, overview.get('total_amount', 0))
        
        print(f"   趋势分: {trend_score:.1f} -> {trend_status} [{trend_detail.get('momentum_note', '')}]")
        print(f"   情绪分: {sentiment_score:.1f} → {sentiment_status}")
        print(f"   最新收: {latest['close']:.2f}  涨跌: {latest['pct_chg']:.2f}%")
        
        results.append({
            "name": name,
            "code": code,
            "trend_score": trend_score,
            "trend_status": trend_status,
            "sentiment_score": sentiment_score,
            "sentiment_status": sentiment_status,
            "close": latest['close'],
            "pct_chg": latest['pct_chg'],
            "trend_detail": trend_detail,
        })
    
    # 综合建议
    if results:
        # 获取 TOP3 主题趋势分
        theme_top3_scores = get_top3_theme_scores(trade_date)
        
        # 计算市场趋势总评分
        trend_score, index_trend, theme_trend = calculate_market_trend_score(results, theme_top3_scores, trade_date)
        
        # 获取前一日仓位（从数据库读取）
        prev_position = _get_prev_position(trade_date)
        
        # 获取前一日趋势分（用于趋势确认机制）
        prev_trend_score = _get_prev_trend_score(trade_date)
        
        # 计算综合情绪分（各指数均值）
        avg_sentiment = sum(r['sentiment_score'] for r in results) / len(results)
        
        # 使用带滞回的仓位调整
        zhaban_rate = overview.get('zb_rate', 0)
        market_status, position_range, position = get_market_status_and_position(
            trend_score, prev_position=prev_position, sentiment_score=avg_sentiment
        )
        
        # 市场状态分类（传入前日趋势分用于趋势确认）
        market_regime, regime_reason = classify_market_regime(
            trend_score, avg_sentiment, zhaban_rate=zhaban_rate,
            prev_trend_score=prev_trend_score
        )
        
        # 持仓结构建议
        portfolio_structure = suggest_portfolio_structure(market_regime)
        
        # 下跌中继风险控制：如果前日趋势分极低（<25），今日加仓上限不超过10%
        # 机构原则：主跌段后的反弹，首日最多试探性建仓，确认趋势后再加
        if prev_trend_score is not None and prev_trend_score < 25:
            crash_limit = 10
            if position > crash_limit:
                reason_addon = f"前日趋势分{prev_trend_score:.0f}极低（主跌段），加仓上限{crash_limit}%，需连续2日趋势确认后方可加仓"
                position = min(position, crash_limit)
                market_regime = "冰点反弹期"
                portfolio_structure = suggest_portfolio_structure(market_regime)
            else:
                reason_addon = ""
        else:
            reason_addon = ""
        
        reason = f"当前市场处于【{market_regime}】阶段 - {regime_reason}"
        if reason_addon:
            reason += f" | {reason_addon}"
        if prev_position is not None and prev_position != position:
            reason += f"（前日仓位{prev_position}% -> 今日{position}%，滞回机制生效）"
        elif prev_position is not None and prev_position == position:
            reason += f"（维持前日仓位{prev_position}%，滞回机制维持不变）"
        
        print("\n" + "=" * 80)
        print("🎯 综合分析结论")
        print("=" * 80)
        
        print(f"\n【指数趋势分】")
        for r in results:
            momentum = r.get('trend_detail', {}).get('momentum_note', '')
            print(f"  {r['name']}: {r['trend_status']} ({r['trend_score']:.1f}) [{momentum}]")
        
        print(f"\n【市场趋势总评分】")
        print(f"  指数趋势 (IndexTrend): {index_trend:.1f}")
        print(f"  主题趋势 (ThemeTrend): {theme_trend:.1f}")
        print(f"  总趋势分 (TrendScore): {trend_score:.1f}")
        print(f"  市场状态: {market_status}")
        
        print(f"\n【市场情绪】")
        for r in results:
            print(f"  {r['name']}: {r['sentiment_status']} ({r['sentiment_score']:.1f})")
        print(f"  综合情绪分: {avg_sentiment:.1f}")
        
        print(f"\n【市场状态分类】")
        print(f"  状态: {market_regime}")
        print(f"  理由: {regime_reason}")
        
        print(f"\n【持仓结构建议】")
        for k, v in portfolio_structure.items():
            print(f"  {k}: {v}")
        
        print(f"\n💡 总体仓位建议: {position}% ({position_range})")
        print(f"   理由: {reason}")
        
        # ===== 涨跌停 & 连板统计 =====
        print(f"\n{'='*80}")
        print("📊 涨跌停 & 连板统计")
        print('='*80)
        limit_stats = get_limit_up_down_stats(trade_date)
        max_lb = calc_max_limit_height(trade_date)
        limit_stats['max_limit_height'] = max_lb
        
        print(f"  涨停: {limit_stats['zt_count']}只 | 跌停: {limit_stats['dt_count']}只")
        print(f"  炸板: {limit_stats['zhaban_count']}只 | 炸板率: {limit_stats['broken_rate']:.1f}%")
        print(f"  最高连板: {max_lb}板")
        print(f"  上涨: {limit_stats['up_count']}只({limit_stats['up_ratio']}%) | 下跌: {limit_stats['down_count']}只({limit_stats['down_ratio']}%)")
        
        # 保存涨跌停数据到数据库（供实时监控读取）
        save_limit_stats_to_cache(limit_stats)
        save_limit_stats_to_database(limit_stats)
        
        # 保存结果
        save_result(results, position, reason, None, overview,
                    trend_score, index_trend, theme_trend, market_status, trade_date,
                    limit_stats, max_lb)
        
        # 保存到数据库（market_status 存 market_regime，更准确反映市场状态）
        save_to_database(trade_date, results, position, reason, 
                        trend_score, index_trend, theme_trend, market_regime)
        
        return results, position, reason, [], overview
    
    return None, 0, "", [], None

# ================
# 保存结果
# ================
def save_result(results, position, reason, style_allocations=None, overview=None, 
                trend_score=None, index_trend=None, theme_trend=None, market_status=None, trade_date=None,
                limit_stats=None, max_lb=0):
    if trade_date is None:
        trade_date = TRADE_DATE
    out_file = os.path.join(safe_cache_dir, f"market_analysis_{trade_date}.csv")
    df = pd.DataFrame(results)
    df.to_csv(out_file, index=False, encoding='utf-8-sig')
    print(f"\n✅ 结果已保存: {out_file}")
    
    # 简单保存文本
    txt_file = os.path.join(safe_cache_dir, f"market_analysis_{trade_date}.txt")
    with open(txt_file, 'w', encoding='utf-8') as f:
        f.write("=" * 80 + "\n")
        f.write("大盘分析与仓位建议\n")
        f.write("=" * 80 + "\n")
        # 大盘整体概况
        if overview:
            f.write(f"\n【大盘整体概况】\n")
            f.write(f"  上证指数: {overview['sh_index']:.2f}  ({overview['sh_pct']:+.2f}%)\n")
            f.write(f"  全市场成交额: {overview['total_amount']:.0f}亿\n")
            f.write(f"  上涨: {overview['up_count']}  下跌: {overview['down_count']}\n")
            f.write(f"  涨停: {overview['zt_count']}  跌停: {overview['dt_count']}  炸板: {overview['zb_count']}  炸板率: {overview['zb_rate']}%\n")
        
        # 指数趋势分
        f.write(f"\n【指数趋势分】\n")
        for r in results:
            f.write(f"  {r['name']}: {r['trend_status']} ({r['trend_score']:.1f})\n")
        
        # 市场趋势总评分
        if trend_score is not None:
            f.write(f"\n【市场趋势总评分】\n")
            f.write(f"  指数趋势 (IndexTrend): {index_trend:.1f}\n")
            f.write(f"  主题趋势 (ThemeTrend): {theme_trend:.1f}\n")
            f.write(f"  总趋势分 (TrendScore): {trend_score:.1f}\n")
            f.write(f"  市场状态: {market_status}\n")
        
        # 市场情绪
        f.write(f"\n【市场情绪】\n")
        for r in results:
            f.write(f"  {r['name']}: {r['sentiment_status']} ({r['sentiment_score']:.1f})\n")
        
        f.write(f"\n总体仓位建议: {position}%\n")
        f.write(f"理由: {reason}\n")
        
        if style_allocations:
            f.write(f"\n风格个股仓位分配:\n")
            f.write(f"  {'指数':<12} {'风格':<12} {'状态':<12} {'建议仓位':<10} {'理由':<30}\n")
            f.write(f"  {'-'*12} {'-'*12} {'-'*12} {'-'*10} {'-'*30}\n")
            for r, style, style_pos, style_reason in style_allocations:
                f.write(f"  {r['name']:<12} {style:<12} {r['trend_status']:<12} {style_pos:<10}% {style_reason:<30}\n")
        
        # 涨跌停 & 连板统计
        if limit_stats:
            f.write(f"\n【涨跌停 & 连板统计】\n")
            f.write(f"  涨停: {limit_stats['zt_count']}只 | 跌停: {limit_stats['dt_count']}只\n")
            f.write(f"  炸板: {limit_stats['zhaban_count']}只 | 炸板率: {limit_stats['broken_rate']:.1f}%\n")
            f.write(f"  最高连板: {max_lb}板\n")
            f.write(f"  上涨: {limit_stats['up_count']}只({limit_stats['up_ratio']}%)")
            f.write(f" | 下跌: {limit_stats['down_count']}只({limit_stats['down_ratio']}%)\n")

# ================
# 读取TOP3主题趋势分
# ================
def get_top3_theme_scores(trade_date=None):
    """
    从 theme_trend_sentiment_score.py 生成的结果中读取 TOP3 主题趋势分
    返回: [score1, score2, score3] 或 None
    """
    if trade_date is None:
        trade_date = TRADE_DATE
    
    # 先尝试从数据库读取指定日期的主题数据
    theme_db = os.path.join(safe_cache_dir, "theme_trend_sentiment.db")
    if os.path.exists(theme_db):
        try:
            conn = sqlite3.connect(theme_db)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT trend_score FROM theme_scores 
                WHERE trade_date = ? 
                ORDER BY rank ASC 
                LIMIT 3
            ''', (trade_date,))
            rows = cursor.fetchall()
            conn.close()
            if rows:
                scores = [row[0] for row in rows]
                print(f"[Theme] 从数据库读取 {trade_date} TOP3主题趋势分: {scores}")
                return scores
        except Exception as e:
            print(f"[Theme] 从数据库读取主题评分失败: {e}")
    
    # 如果没有，尝试从 CSV 读取最新数据
    theme_csv = os.path.join(safe_cache_dir, "theme_trend_sentiment.csv")
    if os.path.exists(theme_csv):
        try:
            df = pd.read_csv(theme_csv, encoding='utf-8-sig')
            if not df.empty and 'trend_score' in df.columns:
                # 按 rank 排序，取前3个
                df = df.sort_values('rank').head(3)
                scores = df['trend_score'].tolist()
                if scores:
                    print(f"[Theme] 从 CSV 读取 TOP3主题趋势分: {scores}")
                    return scores
        except Exception as e:
            print(f"[Theme] 读取主题评分失败: {e}")
    
    print("[Theme] 未找到主题评分数据")
    return None


# ================
# 计算市场趋势总评分（新评分体系）
# ================
def calculate_market_trend_score(index_results, theme_top3_scores=None, trade_date=None):
    """
    计算市场趋势总评分：
    IndexTrend = sh_score * 0.5 + hs300_score * 0.3 + cyb_score * 0.2
    ThemeTrend = TOP3主题平均分
    TrendScore = IndexTrend * 0.5 + ThemeTrend * 0.5
    
    涨跌幅惩罚：主要指数大跌时适当扣分
    情绪惩罚：情绪退潮扣分
    """
    # 提取指数趋势分
    sh_score = 0
    hs300_score = 0
    zz2000_score = 0
    
    for r in index_results:
        if r['name'] == '上证指数':
            sh_score = r['trend_score']
        elif r['name'] == '沪深300':
            hs300_score = r['trend_score']
        elif r['name'] == '中证2000':
            zz2000_score = r['trend_score']
    
    # 计算指数趋势分
    index_trend = sh_score * 0.5 + hs300_score * 0.3 + zz2000_score * 0.2
    
    # 计算主题趋势分（TOP3主题平均分）
    if theme_top3_scores and len(theme_top3_scores) >= 3:
        theme_trend = sum(theme_top3_scores[:3]) / 3
    elif theme_top3_scores:
        theme_trend = sum(theme_top3_scores) / len(theme_top3_scores)
    else:
        theme_trend = index_trend * 0.8
    
    # 计算总趋势分（指数和主题各占一半）
    trend_score = index_trend * 0.5 + theme_trend * 0.5
    
    # 涨跌幅惩罚：主要指数大跌时扣分（适度）
    for r in index_results:
        pct_chg = r.get('pct_chg', 0)
        if pct_chg < -1.0 and r['name'] == '上证指数':
            penalty = abs(pct_chg) * 3  # 跌1%扣3分
            trend_score -= penalty
            print(f"  [惩罚] {r['name']}跌{pct_chg:.1f}%，扣{penalty:.0f}分")
        if pct_chg < -3.0:
            extra_penalty = abs(pct_chg) * 2
            trend_score -= extra_penalty
            print(f"  [惩罚] {r['name']}大跌{pct_chg:.1f}%，额外扣{extra_penalty:.0f}分")
    
    # 情绪惩罚：情绪退潮扣分
    sentiment_statuses = [r.get('sentiment_status', '') for r in index_results]
    if any(s == '情绪退潮' for s in sentiment_statuses):
        trend_score -= 5
        print(f"  [惩罚] 市场情绪退潮，扣5分")
    
    # 限制在 0-100 范围内
    trend_score = min(100, max(0, trend_score))
    
    return trend_score, index_trend, theme_trend


def get_market_status_and_position(trend_score, prev_position=None, sentiment_score=None, breadth_up_ratio=None):
    """
    带滞回的仓位调整：
    - 升仓需超过区间中值+3分才升仓
    - 降仓需低于区间中值-3分才降仓
    - 避免在边界附近频繁切换
    """
    tiers = [
        (80, "强趋势",   "70~80%", 75),
        (70, "趋势良好", "55~70%", 60),
        (60, "震荡偏强", "40~60%", 50),
        (50, "震荡",     "30~50%", 40),
        (40, "弱势",     "20~30%", 25),
        (30, "退潮",     "10~20%", 15),
        (0,  "主跌段",   "0~10%",  5),
    ]
    
    new_tier_idx = 0
    for i, (threshold, status, pos_range, pos) in enumerate(tiers):
        if trend_score >= threshold:
            new_tier_idx = i
            break
    
    # 滞回机制：如果有前一日仓位，在非极端区域避免频繁切换
    if prev_position is not None and 30 < trend_score < 80:
        prev_tier_idx = _position_to_tier_idx(prev_position, tiers)
        if abs(new_tier_idx - prev_tier_idx) == 0:
            new_tier_idx = prev_tier_idx
        elif abs(new_tier_idx - prev_tier_idx) == 1:
            threshold = tiers[min(new_tier_idx, prev_tier_idx)][0]
            if abs(trend_score - threshold) <= 3:
                new_tier_idx = prev_tier_idx
    
    # 单日仓位变化上限：±15%（防止暴跌后单日反弹导致仓位跳变）
    if prev_position is not None:
        raw_position = tiers[new_tier_idx][3]
        max_change = 15
        if raw_position > prev_position + max_change:
            # 加仓受限：找不超过 prev+15 的最高档位
            target = prev_position + max_change
            for i, (th, st, pr, pos) in enumerate(tiers):
                if pos <= target:
                    new_tier_idx = i
                    break
        # 降仓不受限（该跑就跑）
    
    status = tiers[new_tier_idx][1]
    position_range = tiers[new_tier_idx][2]
    position = tiers[new_tier_idx][3]
    return status, position_range, position


def _position_to_tier_idx(position, tiers):
    """将仓位百分比映射到档位索引"""
    for i, (threshold, status, pos_range, pos) in enumerate(tiers):
        if position >= pos - 5:
            return i
    return len(tiers) - 1


def classify_market_regime(trend_score, sentiment_score, breadth_up_ratio=None, zhaban_rate=None,
                           prev_trend_score=None):
    """
    市场状态分类（指导持仓结构而非仅仓位）：
    1. 主升加速期：趋势>=75 + 情绪>=70
    2. 震荡轮动期：趋势50-75 + 情绪40-60
    3. 顶部分歧期：趋势>=60 但 情绪<40 或 炸板率>35%
    4. 主跌退潮期：趋势<40 + 情绪<30
    5. 冰点反弹期：趋势30-45 + 情绪<20
    
    趋势确认机制：从前一日主跌/冰点状态反弹时，需连续2日趋势分回升才确认状态升级，
    防止单日反弹被误判为趋势反转。
    """
    # 趋势确认：如果前日趋势分极低（<30），今日即使反弹也不能直接跳到"震荡轮动"
    if prev_trend_score is not None and prev_trend_score < 30:
        if trend_score >= 50:
            # 前日主跌，今日反弹到50+，但仍需确认，暂定为"冰点反弹期"
            return "冰点反弹期", f"前日趋势分{prev_trend_score:.0f}极低，今日反弹至{trend_score:.0f}但仍需确认，试探性建仓严格止损"
        elif trend_score >= 40:
            return "冰点反弹期", f"前日趋势分{prev_trend_score:.0f}，今日反弹至{trend_score:.0f}，情绪修复中但仍需确认"
    
    if trend_score >= 60 and sentiment_score is not None and sentiment_score < 40:
        return "顶部分歧期", "趋势在但情绪骤降，减仓兑现，切换防御品种"
    if trend_score >= 60 and zhaban_rate is not None and zhaban_rate > 35:
        return "顶部分歧期", "炸板率过高，情绪分歧严重，减仓兑现"
    if trend_score >= 75 and sentiment_score is not None and sentiment_score >= 70:
        return "主升加速期", "趋势与情绪共振，重仓主线龙头，可追高连板"
    if 50 <= trend_score < 75:
        return "震荡轮动期", "结构性行情为主，高抛低吸，快进快出"
    if 30 <= trend_score < 45 and sentiment_score is not None and sentiment_score < 25:
        return "冰点反弹期", "情绪冰点，试探性建仓超跌反弹，严格止损"
    if trend_score < 40 and sentiment_score is not None and sentiment_score < 30:
        return "主跌退潮期", "趋势与情绪双弱，空仓或极轻仓等待"
    if trend_score >= 45:
        return "震荡轮动期", "结构性行情为主，高抛低吸"
    return "主跌退潮期", "市场偏弱，严控仓位"


def suggest_portfolio_structure(market_regime):
    """根据市场状态给出持仓结构建议"""
    structure_map = {
        "主升加速期": {
            "集中度": "集中持仓3-5只",
            "持有周期": "短线为主，3-5日",
            "止损幅度": "5-8%（趋势加速期容错高）",
            "选股偏好": "追强主线龙头，打板/半路",
            "操作要点": "敢于追高，顺势加仓，不轻易止盈",
        },
        "震荡轮动期": {
            "集中度": "分散持仓5-8只",
            "持有周期": "T+1或T+2为主",
            "止损幅度": "3%（严格执行）",
            "选股偏好": "低吸回流，避免追高",
            "操作要点": "高抛低吸，控制换手率，不恋战",
        },
        "顶部分歧期": {
            "集中度": "减至2-3只",
            "持有周期": "T+1，快进快出",
            "止损幅度": "2-3%（收紧止损）",
            "选股偏好": "切换到防御性品种/低位补涨",
            "操作要点": "兑现利润，不追高，防范突然杀跌",
        },
        "主跌退潮期": {
            "集中度": "空仓或仅1-2只",
            "持有周期": "不参与或日内了结",
            "止损幅度": "不适用（不参与）",
            "选股偏好": "仅做超跌反弹，严格止损",
            "操作要点": "管住手，等待情绪冰点和趋势企稳",
        },
        "冰点反弹期": {
            "集中度": "试探2-3只",
            "持有周期": "1-3日反弹",
            "止损幅度": "3-5%",
            "选股偏好": "超跌+缩量到位+首板",
            "操作要点": "小仓位试探，确认反弹再加仓",
        },
    }
    return structure_map.get(market_regime, {
        "集中度": "3-5只",
        "持有周期": "短线",
        "止损幅度": "3%",
        "选股偏好": "中性",
        "操作要点": "观望为主",
    })


# ================
# SQLite数据库操作
# ================
def init_database():
    """初始化数据库表"""
    db_path = os.path.join(safe_cache_dir, "market_analysis.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 创建指数分析表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS index_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            index_name TEXT NOT NULL,
            index_code TEXT NOT NULL,
            trend_score REAL,
            trend_status TEXT,
            sentiment_score REAL,
            sentiment_status TEXT,
            close_price REAL,
            pct_change REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(trade_date, index_code)
        )
    ''')
    
    # 创建总体分析表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS overall_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL UNIQUE,
            total_position INTEGER,
            position_reason TEXT,
            trend_score REAL,
            index_trend REAL,
            theme_trend REAL,
            market_status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 创建涨跌停统计表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS limit_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL UNIQUE,
            zt_count INTEGER DEFAULT 0,
            dt_count INTEGER DEFAULT 0,
            broken_rate REAL DEFAULT 0.0,
            zhaban_count INTEGER DEFAULT 0,
            max_limit_height INTEGER DEFAULT 0,
            up_count INTEGER DEFAULT 0,
            down_count INTEGER DEFAULT 0,
            total INTEGER DEFAULT 0,
            up_ratio REAL DEFAULT 0.0,
            down_ratio REAL DEFAULT 0.0,
            updated TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 添加新列（如果已存在表但缺少新列）
    try:
        cursor.execute("ALTER TABLE overall_analysis ADD COLUMN trend_score REAL")
    except:
        pass
    try:
        cursor.execute("ALTER TABLE overall_analysis ADD COLUMN index_trend REAL")
    except:
        pass
    try:
        cursor.execute("ALTER TABLE overall_analysis ADD COLUMN theme_trend REAL")
    except:
        pass
    try:
        cursor.execute("ALTER TABLE overall_analysis ADD COLUMN market_status TEXT")
    except:
        pass
    
    conn.commit()
    conn.close()
    return db_path

def save_to_database(trade_date, results, total_position, position_reason,
                     trend_score=None, index_trend=None, theme_trend=None, market_status=None):
    """保存分析结果到数据库"""
    db_path = os.path.join(safe_cache_dir, "market_analysis.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # 保存各指数分析
        for r in results:
            cursor.execute('''
                INSERT OR REPLACE INTO index_analysis 
                (trade_date, index_name, index_code, trend_score, trend_status, 
                 sentiment_score, sentiment_status, close_price, pct_change)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                trade_date,
                r['name'],
                r['code'],
                r['trend_score'],
                r['trend_status'],
                r['sentiment_score'],
                r['sentiment_status'],
                r['close'],
                r['pct_chg']
            ))
        
        # 保存总体分析
        cursor.execute('''
            INSERT OR REPLACE INTO overall_analysis 
            (trade_date, total_position, position_reason, trend_score, index_trend, theme_trend, market_status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (trade_date, total_position, position_reason, trend_score, index_trend, theme_trend, market_status))
        
        conn.commit()
        print(f"\n✅ 数据已保存到数据库: {db_path}")
    except Exception as e:
        print(f"\n❌ 数据库保存失败: {e}")
        conn.rollback()
    finally:
        conn.close()


def save_limit_stats_to_database(data):
    """保存涨跌停统计数据到数据库（供 realtime_theme_monitor.py 调用）"""
    db_path = os.path.join(safe_cache_dir, "market_analysis.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            INSERT OR REPLACE INTO limit_stats 
            (trade_date, zt_count, dt_count, broken_rate, zhaban_count, max_limit_height,
             up_count, down_count, total, up_ratio, down_ratio, updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            data.get('trade_date', TRADE_DATE),
            data.get('zt_count', 0),
            data.get('dt_count', 0),
            data.get('broken_rate', 0.0),
            data.get('zhaban_count', 0),
            data.get('max_limit_height', 0),
            data.get('up_count', 0),
            data.get('down_count', 0),
            data.get('total', 0),
            data.get('up_ratio', 0.0),
            data.get('down_ratio', 0.0),
            data.get('updated', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        ))
        conn.commit()
        print(f"[涨跌停DB] 已保存至 limit_stats: {data.get('trade_date')}")
    except Exception as e:
        print(f"[涨跌停DB] 保存失败: {e}")
        conn.rollback()
    finally:
        conn.close()

def get_historical_data(days=30):
    """获取历史数据分析"""
    db_path = os.path.join(safe_cache_dir, "market_analysis.db")
    conn = sqlite3.connect(db_path)
    
    # 获取各指数历史数据
    query = '''
        SELECT trade_date, index_name, trend_score, trend_status, 
               sentiment_score, sentiment_status, close_price, pct_change
        FROM index_analysis
        ORDER BY trade_date DESC
        LIMIT ?
    '''
    df = pd.read_sql_query(query, conn, params=(days * 3,))
    
    conn.close()
    return df

def check_consecutive_sentiment(index_name, sentiment_type, consecutive_days=3):
    """
    检查连续情绪状态
    sentiment_type: 'high' (高情绪) or 'low' (低情绪)
    """
    db_path = os.path.join(safe_cache_dir, "market_analysis.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 获取最近N天的数据
    cursor.execute('''
        SELECT sentiment_status, sentiment_score, trade_date
        FROM index_analysis
        WHERE index_name = ?
        ORDER BY trade_date DESC
        LIMIT ?
    ''', (index_name, consecutive_days))
    
    rows = cursor.fetchall()
    conn.close()
    
    if len(rows) < consecutive_days:
        return None
    
    # 检查条件
    if sentiment_type == 'high':
        # 高情绪：情绪高涨或分数>=70
        is_high = all(
            (row[0] == '情绪高涨' or row[1] >= 70) 
            for row in rows
        )
        if is_high:
            return {
                'type': 'high',
                'index': index_name,
                'days': consecutive_days,
                'start_date': rows[-1][2],
                'end_date': rows[0][2],
                'scores': [row[1] for row in reversed(rows)]
            }
    elif sentiment_type == 'low':
        # 低情绪：情绪退潮或分数<=30
        is_low = all(
            (row[0] == '情绪退潮' or row[1] <= 30) 
            for row in rows
        )
        if is_low:
            return {
                'type': 'low',
                'index': index_name,
                'days': consecutive_days,
                'start_date': rows[-1][2],
                'end_date': rows[0][2],
                'scores': [row[1] for row in reversed(rows)]
            }
    
    return None

def check_consecutive_trend(index_name, trend_type, consecutive_days=3):
    """
    检查连续趋势状态
    trend_type: 'up' (上升) or 'down' (下降)
    """
    db_path = os.path.join(safe_cache_dir, "market_analysis.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT trend_status, trend_score, trade_date
        FROM index_analysis
        WHERE index_name = ?
        ORDER BY trade_date DESC
        LIMIT ?
    ''', (index_name, consecutive_days))
    
    rows = cursor.fetchall()
    conn.close()
    
    if len(rows) < consecutive_days:
        return None
    
    if trend_type == 'up':
        is_up = all(row[0] in ['上升趋势', '震荡偏强'] for row in rows)
        if is_up:
            return {
                'type': 'up',
                'index': index_name,
                'days': consecutive_days,
                'start_date': rows[-1][2],
                'end_date': rows[0][2],
                'scores': [row[1] for row in reversed(rows)]
            }
    elif trend_type == 'down':
        is_down = all(row[0] in ['下降趋势', '震荡偏弱'] for row in rows)
        if is_down:
            return {
                'type': 'down',
                'index': index_name,
                'days': consecutive_days,
                'start_date': rows[-1][2],
                'end_date': rows[0][2],
                'scores': [row[1] for row in reversed(rows)]
            }
    
    return None

def analyze_sentiment_trend_alerts():
    """分析并返回情绪和趋势的提醒"""
    alerts = []
    indices = ['沪深300', '上证指数', '中证2000']
    
    for idx in indices:
        # 检查连续高情绪
        high_sent = check_consecutive_sentiment(idx, 'high', 3)
        if high_sent:
            alerts.append({
                'category': '情绪',
                'level': '⚠️',
                'message': f"{idx}已连续{high_sent['days']}天高情绪（最近分数：{high_sent['scores']}），注意情绪过热"
            })
        
        # 检查连续低情绪
        low_sent = check_consecutive_sentiment(idx, 'low', 3)
        if low_sent:
            alerts.append({
                'category': '情绪',
                'level': '💡',
                'message': f"{idx}已连续{low_sent['days']}天低情绪（最近分数：{low_sent['scores']}），可能是情绪冰点"
            })
        
        # 检查连续上升趋势
        up_trend = check_consecutive_trend(idx, 'up', 3)
        if up_trend:
            alerts.append({
                'category': '趋势',
                'level': '🚀',
                'message': f"{idx}已连续{up_trend['days']}天强势（最近分数：{up_trend['scores']}）"
            })
        
        # 检查连续下降趋势
        down_trend = check_consecutive_trend(idx, 'down', 3)
        if down_trend:
            alerts.append({
                'category': '趋势',
                'level': '📉',
                'message': f"{idx}已连续{down_trend['days']}天弱势（最近分数：{down_trend['scores']}）"
            })
    
    return alerts


def get_limit_up_down_stats(trade_date=None):
    """
    获取涨跌停数据和炸板率（移植自 tushare_quant.py）
    返回: {zt_count, dt_count, zt_codes, dt_codes, broken_rate, zhaban_count}
    """
    if trade_date is None:
        trade_date = TRADE_DATE
    
    zt_codes = []
    dt_codes = []
    broken_rate = 0.0
    zhaban_count = 0
    
    try:
        # 方法1：使用每日行情数据计算真实的涨跌停（收盘价）
        daily = pro.daily(trade_date=trade_date)
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
            
            print(f"[涨跌停] 涨停(真实收盘): {len(zt_codes)}只")
            print(f"[涨跌停] 跌停(真实收盘): {len(dt_codes)}只")
            
            # 获取炸板数据（盘中触及涨停但未封住）
            try:
                limit_df = pro.limit_list_d(trade_date=trade_date)
                if limit_df is not None and not limit_df.empty:
                    # limit='D'表示最终封住, limit='Z'表示炸板
                    zhaban_codes = limit_df[limit_df['limit'] == 'Z']['ts_code'].astype(str).tolist()
                    zhaban_count = len(zhaban_codes)
                    
                    # 炸板率 = 炸板数 ÷ (封住数 + 炸板数)
                    total_touch = len(zt_codes) + zhaban_count
                    if total_touch > 0:
                        broken_rate = (zhaban_count / total_touch) * 100
                    print(f"[涨跌停] 炸板率: {broken_rate:.1f}% (炸板{zhaban_count}只/触及涨停{total_touch}只)")
            except Exception as e:
                print(f"[涨跌停] 获取炸板数据失败: {e}")
        
        # 如果以上方法都失败，使用ths接口作为备选
        if not zt_codes and not dt_codes:
            print("[涨跌停] 使用ths接口备选...")
            try:
                ths_zt = pro.limit_list_ths(trade_date=trade_date, limit_type='涨停池')
                if ths_zt is not None and not ths_zt.empty:
                    zt_codes = ths_zt['ts_code'].astype(str).tolist()
                    print(f"[涨跌停] 涨停(ths备选): {len(zt_codes)}只")
            except Exception as e:
                print(f"[涨跌停] ths涨停失败: {e}")
            
            try:
                ths_dt = pro.limit_list_ths(trade_date=trade_date, limit_type='跌停池')
                if ths_dt is not None and not ths_dt.empty:
                    dt_codes = ths_dt['ts_code'].astype(str).tolist()
                    print(f"[涨跌停] 跌停(ths备选): {len(dt_codes)}只")
            except Exception as e:
                print(f"[涨跌停] ths跌停失败: {e}")
    
    except Exception as e:
        print(f"[涨跌停] 获取失败: {e}")
    
    # 获取涨跌比例数据
    up_count = 0
    down_count = 0
    total_count = 0
    up_ratio = 0.0
    down_ratio = 0.0
    
    try:
        daily = pro.daily(trade_date=trade_date)
        if daily is not None and not daily.empty:
            up_count = int((daily['pct_chg'] > 0).sum())
            down_count = int((daily['pct_chg'] < 0).sum())
            total_count = len(daily)
            if total_count > 0:
                up_ratio = round(up_count / total_count * 100, 1)
                down_ratio = round(down_count / total_count * 100, 1)
    except Exception as e:
        print(f"[涨跌比例] 获取失败: {e}")
    
    result = {
        "zt_count": len(zt_codes),
        "dt_count": len(dt_codes),
        "zt_codes": zt_codes,
        "dt_codes": dt_codes,
        "broken_rate": round(broken_rate, 1),
        "zhaban_count": zhaban_count,
        "trade_date": trade_date,
        "updated": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        # 新增涨跌比例数据
        "up_count": up_count,
        "down_count": down_count,
        "total": total_count,
        "up_ratio": up_ratio,
        "down_ratio": down_ratio
    }
    
    # 保存到缓存文件（供实时监控读取）
    save_limit_stats_to_cache(result)
    
    return result


def calc_max_limit_height(trade_date=None):
    """计算最高连板高度（移植自 tushare_quant.py）"""
    if trade_date is None:
        trade_date = TRADE_DATE
    
    try:
        # 优先使用 akshare 免费接口
        import akshare as ak
        try:
            zt_df = ak.stock_zt_pool_em(date=trade_date)
            if zt_df is not None and not zt_df.empty:
                if '连板数' in zt_df.columns:
                    zt_df['连板数'] = pd.to_numeric(zt_df['连板数'], errors='coerce').fillna(1).astype(int)
                    max_lb = int(zt_df['连板数'].max())
                    print(f"[连板高度] akshare获取成功: 最高连板 {max_lb} 板")
                    return max_lb
                elif '连扳数' in zt_df.columns:
                    zt_df['连扳数'] = pd.to_numeric(zt_df['连扳数'], errors='coerce').fillna(1).astype(int)
                    max_lb = int(zt_df['连扳数'].max())
                    print(f"[连板高度] akshare获取成功: 最高连板 {max_lb} 板")
                    return max_lb
        except Exception as ak_error:
            print(f"[连板高度] akshare获取失败: {ak_error}")
        
        # akshare失败时，尝试 tushare pro 接口
        if pro is not None:
            zt_df = pro.limit_step(trade_date=trade_date)
            if zt_df is not None and not zt_df.empty:
                if 'nums' in zt_df.columns:
                    max_lb = int(zt_df['nums'].max())
                    print(f"[连板高度] tushare limit_step: 最高连板 {max_lb} 板")
                    return max_lb
    except Exception as e:
        print(f"[连板高度] 计算失败: {e}")
    
    return 0


def save_limit_stats_to_cache(data):
    """将涨跌停统计数据保存到缓存文件"""
    import json
    
    # 保存每日统计文件
    daily_cache_file = os.path.join(BASE_DIR, 'cache_daily', f'full_market_stats_{data["trade_date"]}.json')
    os.makedirs(os.path.dirname(daily_cache_file), exist_ok=True)
    
    try:
        with open(daily_cache_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[涨跌停] 缓存已保存: {daily_cache_file}")
    except Exception as e:
        print(f"[涨跌停] 保存缓存失败: {e}")


if __name__ == '__main__':
    # 初始化数据库
    init_database()
    
    # 运行分析（analyze_market 内部已完成所有计算和保存）
    result = analyze_market()
    
    if result and len(result) >= 4:
        results, total_position, position_reason, style_allocations, overview = result
        
        # 检查提醒
        print("\n" + "=" * 80)
        print("🔔 动态跟踪提醒")
        print("=" * 80)
        
        alerts = analyze_sentiment_trend_alerts()
        if alerts:
            for alert in alerts:
                print(f"\n{alert['level']} 【{alert['category']}】{alert['message']}")
        else:
            print("\n✅ 暂无特殊提醒")
    else:
        print("\n❌ 分析未完成")

