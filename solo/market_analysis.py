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
    趋势分 = MA_SCORE(40) + INDEX_SCORE(30) + BREADTH_SCORE(30)
    
    MA_SCORE（40分）- 均线趋势：
    - MA5 > MA10 > MA20     40
    - MA5 > MA10            30
    - MA5 > MA20            20
    - 全空头排列           10
    
    INDEX_SCORE（30分）- 指数站位：
    - 站上20日线 30
    - 站上10日线 20
    - 站上5日线 10
    - 全部跌破 0
    
    BREADTH_SCORE（30分）- 市场广度：
    - >70%    30
    - 60%     25
    - 50%     20
    - 40%     10
    - 30%      5
    - <30%     0
    """
    if df is None or len(df) < 20:
        return 50.0, "无数据"
    
    latest = df.iloc[-1]
    
    # 计算均线
    ma5 = df['close'].rolling(5).mean().iloc[-1]
    ma10 = df['close'].rolling(10).mean().iloc[-1]
    ma20 = df['close'].rolling(20).mean().iloc[-1]
    
    # -------------------
    # MA_SCORE（40分）- 均线趋势
    # -------------------
    ma_score = 0
    if ma5 > ma10 > ma20:
        ma_score = 40
    elif ma5 > ma10:
        ma_score = 30
    elif ma5 > ma20:
        ma_score = 20
    elif ma5 < ma10 < ma20:
        ma_score = 10
    else:
        ma_score = 15  # 其他情况
    
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
            breadth_score = 30
        elif up_ratio >= 60:
            breadth_score = 25
        elif up_ratio >= 50:
            breadth_score = 20
        elif up_ratio >= 40:
            breadth_score = 10
        elif up_ratio >= 30:
            breadth_score = 5
        else:
            breadth_score = 0
    else:
        # 如果没有上涨家数数据，用指数涨幅代替
        pct_chg = float(latest.get('pct_chg', 0))
        if pct_chg > 2:
            breadth_score = 25
        elif pct_chg > 1:
            breadth_score = 20
        elif pct_chg > 0:
            breadth_score = 15
        elif pct_chg > -1:
            breadth_score = 10
        else:
            breadth_score = 5
    
    # 计算总分
    trend_score = ma_score + index_score + breadth_score
    trend_score = max(0, min(100, trend_score))
    
    # 判断趋势状态（综合趋势分和均线排列）
    # 趋势分是主要依据，均线排列作为辅助判断
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
    
    return trend_score, trend_status

# ================
# 计算情绪指标
# ================
def calc_sentiment_score(df):
    if df is None or len(df) < 20:
        return 50.0, "无数据"
    
    latest = df.iloc[-1]
    pct_chg = latest['pct_chg'] if 'pct_chg' in df.columns else 0
    
    # ==============================
    # 1. 涨跌方向与强度 (30分) - 核心指标
    # ==============================
    # 上涨得正分，下跌得负分
    direction_score = 0
    if pct_chg >= 2:
        direction_score = 30  # 大涨
    elif pct_chg >= 1:
        direction_score = 20  # 上涨
    elif pct_chg >= 0:
        direction_score = 10  # 平盘微涨
    elif pct_chg >= -1:
        direction_score = 5   # 小幅下跌
    elif pct_chg >= -2:
        direction_score = 0   # 下跌
    else:
        direction_score = -20 # 大跌（额外扣分）
    
    # ==============================
    # 2. 成交量变化 (25分) - 区分涨跌
    # ==============================
    vol5 = df['vol'].tail(5).mean()
    vol20 = df['vol'].tail(20).mean()
    vol_ratio = vol5 / vol20 if vol20 > 0 else 1
    
    vol_score = 12.5 + (vol_ratio - 1) * 20
    
    # 关键修正：放量下跌是恐慌信号，应扣分
    if pct_chg < -1 and vol_ratio > 1.2:
        vol_score -= 10  # 放量大跌额外扣10分
    elif pct_chg > 1 and vol_ratio > 1.2:
        vol_score += 5   # 放量大涨额外加5分
    
    vol_score = min(25, max(0, vol_score))
    
    # ==============================
    # 3. 振幅与涨跌结合 (20分)
    # ==============================
    amplitude = (latest['high'] - latest['low']) / latest['low'] * 100
    
    # 振幅大但下跌是负面信号
    if pct_chg < 0:
        # 下跌时振幅大 = 恐慌，扣分
        amp_score = max(0, 10 - amplitude)
    else:
        # 上涨时振幅大 = 强势，加分
        amp_score = min(20, 10 + amplitude * 0.5)
    
    # ==============================
    # 4. 连涨连跌趋势 (25分)
    # ==============================
    up_streak = 0
    down_streak = 0
    
    # 计算连涨天数
    for i in range(1, 6):
        if len(df) > i:
            if df['pct_chg'].iloc[-i] > 0:
                up_streak += 1
            else:
                break
    
    # 计算连跌天数
    for i in range(1, 6):
        if len(df) > i:
            if df['pct_chg'].iloc[-i] < 0:
                down_streak += 1
            else:
                break
    
    # 连涨加分，连跌扣分
    streak_score = 12.5 + up_streak * 3 - down_streak * 4
    streak_score = min(25, max(0, streak_score))
    
    # ==============================
    # 5. 近期波动率惩罚 (额外)
    # ==============================
    vol20 = df['pct_chg'].tail(20).std()
    volatility = vol20 if not pd.isna(vol20) else 1.5
    
    # 高波动惩罚（市场不稳定）
    vol_penalty = 0
    if volatility > 2.5:
        vol_penalty = (volatility - 2.5) * 3  # 高波动扣分
    
    # ==============================
    # 综合评分
    # ==============================
    sentiment_score = direction_score + vol_score + amp_score + streak_score - vol_penalty
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
        trend_score, trend_status = calc_trend_score(df, up_count, total_count)
        sentiment_score, sentiment_status = calc_sentiment_score(df)
        
        print(f"   趋势分: {trend_score:.1f} → {trend_status}")
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
            "pct_chg": latest['pct_chg']
        })
    
    # 综合建议（游资策略：综合考虑所有指数，寻找结构性机会）
    if results:
        # 获取 TOP3 主题趋势分
        theme_top3_scores = get_top3_theme_scores(trade_date)
        
        # 计算市场趋势总评分
        trend_score, index_trend, theme_trend = calculate_market_trend_score(results, theme_top3_scores)
        market_status, position_range, position = get_market_status_and_position(trend_score)
        reason = f"当前市场处于【{market_status}】阶段"
        
        print("\n" + "=" * 80)
        print("🎯 综合分析结论")
        print("=" * 80)
        
        print(f"\n【指数趋势分】")
        for r in results:
            print(f"  {r['name']}: {r['trend_status']} ({r['trend_score']:.1f})")
        
        print(f"\n【市场趋势总评分】")
        print(f"  指数趋势 (IndexTrend): {index_trend:.1f}")
        print(f"  主题趋势 (ThemeTrend): {theme_trend:.1f}")
        print(f"  总趋势分 (TrendScore): {trend_score:.1f}")
        print(f"  市场状态: {market_status}")
        
        print(f"\n【市场情绪】")
        for r in results:
            print(f"  {r['name']}: {r['sentiment_status']} ({r['sentiment_score']:.1f})")
        
        print(f"\n💡 总体仓位建议: {position}% ({position_range})")
        print(f"   理由: 当前市场处于【{market_status}】阶段")
        
        # 根据不同指数趋势给出风格个股仓位分配
        print(f"\n🎨 风格个股仓位分配建议")
        print(f"  {'指数':<12} {'风格':<12} {'状态':<12} {'建议仓位':<10} {'理由':<30}")
        print(f"  {'-'*12} {'-'*12} {'-'*12} {'-'*10} {'-'*30}")
        
        # 计算权重
        weights = []
        for r in results:
            if r['trend_status'] == '上升趋势':
                weight = 5
            elif r['trend_status'] == '震荡偏强':
                weight = 3
            elif r['trend_status'] == '震荡偏弱':
                weight = 1.5
            else:  # 下降趋势
                weight = 0.5
            weights.append(weight)
        
        total_weight = sum(weights)
        style_allocations = []
        
        for i, r in enumerate(results):
            # 根据指数对应风格
            if r['name'] == '上证指数':
                style = '综合指数'
            elif r['name'] == '沪深300':
                style = '大盘股'
            elif r['name'] == '中证2000':
                style = '小盘股'
            else:
                style = '其他'
            
            # 根据权重分配仓位（5%为最小单位）
            style_pos = round(position * weights[i] / total_weight / 5) * 5
            
            if r['trend_status'] == '上升趋势':
                style_reason = '趋势向好，重点配置'
            elif r['trend_status'] == '震荡偏强':
                style_reason = '趋势偏强，适度配置'
            elif r['trend_status'] == '震荡偏弱':
                style_reason = '趋势偏弱，谨慎配置'
            else:  # 下降趋势
                style_reason = '趋势下降，少量参与'
            
            style_allocations.append((r, style, style_pos, style_reason))
        
        # 调整最后一个仓位使总和等于总仓位
        sum_so_far = sum(a[2] for a in style_allocations[:-1])
        if style_allocations:
            last_r, last_style, last_pos, last_reason = style_allocations[-1]
            last_pos = position - sum_so_far
            style_allocations[-1] = (last_r, last_style, last_pos, last_reason)
        
        # 输出分配结果
        for r, style, style_pos, style_reason in style_allocations:
            print(f"  {r['name']:<12} {style:<12} {r['trend_status']:<12} {style_pos}%{' ':>6} {style_reason:<30}")
        
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
        save_result(results, position, reason, style_allocations, overview,
                    trend_score, index_trend, theme_trend, market_status, trade_date,
                    limit_stats, max_lb)
        
        # 保存到数据库
        save_to_database(trade_date, results, position, reason, 
                        trend_score, index_trend, theme_trend, market_status)
        
        return results, position, reason, style_allocations, overview
    
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
def calculate_market_trend_score(index_results, theme_top3_scores=None):
    """
    计算市场趋势总评分：
    IndexTrend = sh_score * 0.5 + hs300_score * 0.3 + cyb_score * 0.2
    ThemeTrend = TOP3主题平均分
    TrendScore = IndexTrend * 0.4 + ThemeTrend * 0.6
    
    如果 ThemeTrend > 90: TrendScore += 10
    如果 ThemeTrend > 85: TrendScore += 5
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
        # 如果没有主题数据，用指数趋势代替
        theme_trend = index_trend * 0.8
    
    # 计算总趋势分
    trend_score = index_trend * 0.4 + theme_trend * 0.6
    
    # 主题趋势加分
    if theme_trend > 90:
        trend_score += 10
    elif theme_trend > 85:
        trend_score += 5
    
    # 限制在 0-100 范围内
    trend_score = min(100, max(0, trend_score))
    
    return trend_score, index_trend, theme_trend


def get_market_status_and_position(trend_score):
    """
    根据趋势分返回市场状态和仓位建议：
    85~100  → 主升浪  → 80~100%
    75~85   → 强趋势  → 60~80%
    65~75   → 趋势良好 → 50~70%
    55~65   → 震荡    → 30~50%
    45~55   → 弱势    → 20~30%
    35~45   → 退潮    → 10~20%
    <35     → 主跌段  → 0~10%
    """
    if trend_score >= 85:
        status = "主升浪"
        position_range = "80~100%"
        position = 90
    elif trend_score >= 75:
        status = "强趋势"
        position_range = "60~80%"
        position = 70
    elif trend_score >= 65:
        status = "趋势良好"
        position_range = "50~70%"
        position = 60
    elif trend_score >= 55:
        status = "震荡"
        position_range = "30~50%"
        position = 40
    elif trend_score >= 45:
        status = "弱势"
        position_range = "20~30%"
        position = 25
    elif trend_score >= 35:
        status = "退潮"
        position_range = "10~20%"
        position = 15
    else:
        status = "主跌段"
        position_range = "0~10%"
        position = 5
    
    return status, position_range, position


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
    
    # 运行分析
    result = analyze_market()
    
    if result and len(result) >= 4:
        results, total_position, position_reason, style_allocations, overview = result
        
        # 获取趋势评分数据（从 analyze_market 中获取）
        # 需要重新计算或从全局变量获取
        theme_csv = os.path.join(safe_cache_dir, "theme_trend_sentiment.csv")
        theme_top3_scores = None
        if os.path.exists(theme_csv):
            try:
                df = pd.read_csv(theme_csv, encoding='utf-8-sig')
                if not df.empty and 'trend_score' in df.columns:
                    df = df.sort_values('rank').head(3)
                    theme_top3_scores = df['trend_score'].tolist()
            except:
                pass
        
        ts, it, tt = calculate_market_trend_score(results, theme_top3_scores)
        ms, pr, tp = get_market_status_and_position(ts)
        
        # 保存到数据库
        save_to_database(TRADE_DATE, results, total_position, position_reason, 
                        ts, it, tt, ms)
        
        # 获取并保存涨跌停 & 连板数据
        limit_stats = get_limit_up_down_stats(TRADE_DATE)
        max_lb = calc_max_limit_height(TRADE_DATE)
        limit_stats['max_limit_height'] = max_lb
        save_limit_stats_to_cache(limit_stats)
        save_limit_stats_to_database(limit_stats)
        
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

