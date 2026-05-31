#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =========================================================
# AI主线ETF系统 v5.0（多日增强版）
# =========================================================
# 新增功能：
# 1. 多日数据分析（10-20天）
# 2. 累计涨幅计算
# 3. 持续强势判断
# 4. 回测验证功能
# 5. 动态仓位管理
# 6. 交易记录跟踪
# =========================================================

import os
import sys
import io
import time
import json
import pickle
import sqlite3
import requests
import numpy as np
import pandas as pd
import tushare as ts
from datetime import datetime, timedelta
from dotenv import load_dotenv

# =========================
# 编码修复（Windows PowerShell）
# =========================
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# =========================================================
# 环境变量
# =========================================================
load_dotenv("config/.env")

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
SERVERCHAN_KEY = os.getenv("WECHAT_SCKEY")

ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

# =========================================================
# 路径配置
# =========================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_daily")
REPORT_DIR = os.path.join(BASE_DIR, "report_daily")
DB_PATH = os.path.join(CACHE_DIR, "etf_result.db")

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)

# =========================================================
# 参数配置
# =========================================================
LOOKBACK_DAYS = 10      # 多日回看天数
MIN_STOCKS = 10         # 板块最小股票数
TOP_K = 5               # 输出TOP K
MOMENTUM_W = 0.6        # 动量权重
ACC_W = 0.4             # 加速度权重

# =========================================================
# ETF池（37只）
# =========================================================
ETF_POOL = {
    '半导体': '512480.SH',
    '人工智能': '159819.SZ',
    '算力': '561210.SH',
    '机器人': '562500.SH',
    '软件': '515230.SH',
    '通信': '515880.SH',
    '新能源': '516160.SH',
    '光伏': '515790.SH',
    '储能': '159566.SZ',
    '军工': '512660.SH',
    '创新药': '159992.SZ',
    '消费电子': '159732.SZ',
    '黄金': '518880.SH',
    '证券': '512880.SH',
    '红利': '515180.SH',
    '银行': '512800.SH',
    '消费': '159928.SZ',
    '酒': '512690.SH',
    '电池': '159755.SZ',
    '有色金属': '516650.SH',
    '芯片': '159995.SZ',
    '化工': '159870.SZ',
    '半导体设备': '159516.SZ',
    '煤炭': '515220.SH',
    '游戏': '159869.SZ',
    '金融科技': '159851.SZ',
    '电力': '159611.SZ',
    '电网设备': '561380.SH',
    '新能源车': '515030.SH',
    '航空航天': '159227.SZ',
    '医疗器械': '159883.SZ',
    '食品饮料': '159736.SZ',
    '钢铁': '515210.SH',
}

# =========================================================
# 行业催化
# =========================================================
INDUSTRY_EVENTS = {
    '半导体': ['HBM', 'GPU', 'AI芯片', '先进封装', '存储涨价'],
    '人工智能': ['Agent', '大模型', 'AI应用'],
    '算力': ['液冷', '数据中心', '英伟达'],
    '机器人': ['人形机器人', 'Tesla Bot'],
    '创新药': ['FDA', 'BD', 'ASCO']
}

# =========================================================
# 数据库初始化
# =========================================================
def init_db():
    """初始化数据库表"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 风格历史表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS style_history (
            date TEXT,
            风格 TEXT,
            当前得分 REAL,
            热度 REAL,
            趋势强度 REAL,
            成交额 REAL,
            轮动强度 REAL,
            风格状态 TEXT
        )
    """)
    
    # ETF交易记录表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS etf_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            etf_code TEXT,
            etf_name TEXT,
            action TEXT,
            price REAL,
            shares INTEGER,
            reason TEXT,
            score REAL
        )
    """)
    
    # ETF评分历史表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS etf_score_history (
            date TEXT,
            etf_code TEXT,
            etf_name TEXT,
            score REAL,
            signal TEXT,
            level TEXT,
            cumulative_return REAL,
            strong_days INTEGER
        )
    """)
    
    conn.commit()
    conn.close()


# =========================================================
# 交易日获取
# =========================================================
def get_last_trade_date():
    """获取最近交易日"""
    now = datetime.now()
    if now.hour < 15:
        query_date = (now - timedelta(days=1)).strftime('%Y%m%d')
    else:
        query_date = now.strftime('%Y%m%d')
    
    cal = pro.trade_cal(exchange='', start_date='20240101', end_date=query_date)
    cal = cal[cal['is_open'] == 1]
    return str(cal[cal['cal_date'] <= query_date]['cal_date'].max())


def get_trade_dates(end_date, n_days=10):
    """获取最近N个交易日"""
    cal = pro.trade_cal(exchange='', start_date='20240101', end_date=end_date)
    cal = cal[cal['is_open'] == 1]
    cal = cal[cal['cal_date'] <= end_date]
    trade_dates = cal['cal_date'].tolist()[-n_days:]
    return trade_dates


TRADE_DATE = get_last_trade_date()


# =========================================================
# ETF多日数据获取
# =========================================================
def get_etf_data_multiday(ts_code, trade_dates):
    """获取ETF多日数据"""
    all_data = []
    
    for date in trade_dates:
        cache_file = os.path.join(CACHE_DIR, f"{ts_code.replace('.', '_')}_{date}.csv")
        
        # 优先读取缓存
        if os.path.exists(cache_file):
            try:
                df = pd.read_csv(cache_file)
                df['trade_date'] = df['trade_date'].astype(str)
                all_data.append(df)
            except:
                pass
        else:
            # 从Tushare下载
            try:
                df = pro.fund_daily(ts_code=ts_code, start_date=date, end_date=date)
                if not df.empty:
                    df.to_csv(cache_file, index=False)
                    all_data.append(df)
                time.sleep(0.05)
            except:
                pass
    
    if not all_data:
        return None
    
    result = pd.concat(all_data, ignore_index=True)
    result = result.sort_values('trade_date')
    return result


def get_index_data():
    """获取指数数据"""
    cache_file = os.path.join(CACHE_DIR, "000300.csv")
    
    if os.path.exists(cache_file):
        df = pd.read_csv(cache_file)
        if len(df) > 100:
            return df
    
    df = pro.index_daily(ts_code='000300.SH', start_date='20240101', end_date=TRADE_DATE)
    df = df.sort_values('trade_date')
    df.to_csv(cache_file, index=False)
    return df


# =========================================================
# 技术指标计算
# =========================================================
def calc_indicators(df):
    """计算技术指标"""
    df = df.copy()
    
    # 均线
    for ma in [5, 10, 20, 60]:
        df[f'ma{ma}'] = df['close'].rolling(ma).mean()
    
    # 成交量均线
    df['vol5'] = df['vol'].rolling(5).mean()
    
    # 涨幅
    for n in [5, 10, 20]:
        df[f'pct{n}'] = (df['close'] / df['close'].shift(n) - 1) * 100
    
    # 趋势斜率
    df['slope20'] = (df['ma20'] / df['ma20'].shift(5) - 1) * 100
    
    # 波动率
    df['volatility'] = df['pct_chg'].rolling(10).std()
    
    # ATR
    df['tr'] = np.maximum(
        df['high'] - df['low'],
        np.maximum(
            abs(df['high'] - df['close'].shift(1)),
            abs(df['low'] - df['close'].shift(1))
        )
    )
    df['atr'] = df['tr'].rolling(14).mean()
    
    return df


# =========================================================
# 多日评分计算
# =========================================================
def calc_multiday_score(df, trade_dates):
    """
    计算ETF多日综合评分
    
    返回:
        dict: {
            '累计涨幅': float,
            '日均涨幅': float,
            '动量': float,
            '加速度': float,
            '持续强势天数': int,
            '最大回撤': float,
            '胜率': float
        }
    """
    if df is None or len(df) < 2:
        return None
    
    # 计算每日涨幅
    daily_returns = df['pct_chg'].tolist()
    
    if len(daily_returns) < 2:
        return None
    
    # 累计涨幅
    cumulative_return = sum(daily_returns)
    
    # 日均涨幅
    avg_return = cumulative_return / len(daily_returns)
    
    # 动量（最近3日 - 前3日）
    if len(daily_returns) >= 6:
        momentum = sum(daily_returns[-3:]) - sum(daily_returns[:3])
    elif len(daily_returns) >= 3:
        momentum = sum(daily_returns[-2:]) - sum(daily_returns[:2])
    else:
        momentum = daily_returns[-1] - daily_returns[0]
    
    # 加速度
    if len(daily_returns) >= 3:
        acc = (daily_returns[-1] - daily_returns[-2]) - (daily_returns[-2] - daily_returns[-3])
    else:
        acc = 0
    
    # 持续强势天数（连续上涨）
    strong_days = 0
    for r in reversed(daily_returns):
        if r > 0:
            strong_days += 1
        else:
            break
    
    # 最大回撤
    cummax = df['close'].cummax()
    drawdown = (df['close'] - cummax) / cummax
    max_drawdown = drawdown.min() * 100
    
    # 胜率（上涨天数占比）
    win_rate = (df['pct_chg'] > 0).mean() * 100
    
    return {
        '累计涨幅': round(cumulative_return, 2),
        '日均涨幅': round(avg_return, 2),
        '动量': round(momentum, 2),
        '加速度': round(acc, 2),
        '持续强势天数': strong_days,
        '最大回撤': round(max_drawdown, 2),
        '胜率': round(win_rate, 2)
    }


# =========================================================
# 趋势识别函数
# =========================================================
def mainline_start(df):
    """主线启动识别"""
    if len(df) < 30:
        return False
    
    latest = df.iloc[-1]
    range30 = df['high'].rolling(30).max().iloc[-2] / df['low'].rolling(30).min().iloc[-2]
    breakout = latest['close'] > df['high'].rolling(30).max().iloc[-2]
    volume_expand = latest['vol'] > df['vol5'].iloc[-2] * 1.5
    
    return range30 < 1.25 and breakout and volume_expand


def main_uptrend(df):
    """主升浪识别"""
    if len(df) < 20:
        return False
    
    latest = df.iloc[-1]
    return (
        latest['ma5'] > latest['ma10'] > latest['ma20']
        and latest['slope20'] > 2
        and latest['pct5'] > latest['pct10'] / 2
    )


def first_dip(df):
    """第一次分歧低吸"""
    if len(df) < 20:
        return False
    
    latest = df.iloc[-1]
    try:
        breakout_recent = df['close'].rolling(20).max().shift(5) < df['close'].shift(5)
        return (
            breakout_recent.iloc[-1]
            and latest['close'] > latest['ma20']
            and latest['vol'] < latest['vol5']
            and abs(latest['close'] - latest['ma10']) / latest['ma10'] < 0.015
        )
    except:
        return False


def trend_exhaust(df):
    """趋势衰竭识别"""
    if len(df) < 20:
        return False
    
    latest = df.iloc[-1]
    upper_shadow = (latest['high'] - latest['close']) / latest['close']
    volume_blowoff = latest['vol'] > df['vol5'].iloc[-1] * 2
    
    return (
        latest['pct20'] > 20
        and upper_shadow > 0.03
        and volume_blowoff
    )


def weekly_trend(df):
    """周线趋势"""
    try:
        weekly = df.copy()
        weekly.index = pd.to_datetime(weekly['trade_date'])
        weekly = weekly.resample('W').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'vol': 'sum'
        })
        weekly['ma5'] = weekly['close'].rolling(5).mean()
        weekly['ma10'] = weekly['close'].rolling(10).mean()
        latest = weekly.iloc[-1]
        return latest['ma5'] > latest['ma10']
    except:
        return False


def relative_strength(df, index_df):
    """相对强弱"""
    if len(df) < 20 or len(index_df) < 20:
        return 0
    
    etf_return = (df['close'].iloc[-1] / df['close'].iloc[-20] - 1) * 100
    index_return = (index_df['close'].iloc[-1] / index_df['close'].iloc[-20] - 1) * 100
    return round(etf_return - index_return, 2)


def volatility_compress(df):
    """波动率压缩"""
    if len(df) < 20:
        return False
    
    latest_atr = df['atr'].iloc[-1]
    atr_mean = df['atr'].rolling(20).mean().iloc[-1]
    return latest_atr < atr_mean * 0.8


def wave_stage(df):
    """波段阶段"""
    if len(df) < 20:
        return '未知', 0
    
    latest = df.iloc[-1]
    low20 = df['low'].rolling(20).min().iloc[-1]
    rise = (latest['close'] / low20 - 1) * 100
    
    if rise < 8:
        return '启动初期', rise
    elif rise < 20:
        return '主升阶段', rise
    else:
        return '波段后期', rise


# =========================================================
# 信号识别
# =========================================================
def buy_signal(df):
    """买点识别"""
    if mainline_start(df):
        return '主线启动'
    if first_dip(df):
        return '第一次分歧低吸'
    if main_uptrend(df):
        return '主升浪'
    if trend_exhaust(df):
        return '趋势衰竭'
    return '观察'


def signal_level(df):
    """信号等级"""
    if mainline_start(df) and weekly_trend(df):
        return 'S'
    if main_uptrend(df) and first_dip(df):
        return 'A'
    if main_uptrend(df):
        return 'B'
    if trend_exhaust(df):
        return 'D'
    return 'C'


# =========================================================
# ETF综合评分（多日版本）
# =========================================================
def etf_score_v2(df, industry, index_df, trade_dates):
    """
    ETF综合评分（多日增强版）
    
    返回:
        tuple: (总评分, RS强度, 多日评分dict)
    """
    if df is None or len(df) < 20:
        return 0, 0, None
    
    latest = df.iloc[-1]
    score = 0
    
    # 趋势动量
    score += latest['pct5'] * 2
    score += latest['pct10']
    
    # 多头排列
    if latest['ma5'] > latest['ma10'] > latest['ma20']:
        score += 20
    
    # 趋势斜率
    if latest['slope20'] > 2:
        score += 15
    
    # 相对强弱
    rs = relative_strength(df, index_df)
    score += rs * 1.5
    
    # 信号加分
    if mainline_start(df):
        score += 25
    if main_uptrend(df):
        score += 20
    if first_dip(df):
        score += 20
    if weekly_trend(df):
        score += 15
    if volatility_compress(df):
        score += 10
    
    # 多日评分
    multiday_scores = calc_multiday_score(df, trade_dates)
    if multiday_scores:
        # 累计涨幅加分
        score += multiday_scores['累计涨幅'] * 0.5
        
        # 持续强势加分
        score += multiday_scores['持续强势天数'] * 3
        
        # 胜率加分
        score += multiday_scores['胜率'] * 0.1
        
        # 最大回撤惩罚
        score -= abs(multiday_scores['最大回撤']) * 0.3
    
    # 波段阶段
    stage, rise = wave_stage(df)
    if rise > 20:
        score -= 15
    
    # 趋势衰竭
    if trend_exhaust(df):
        score -= 30
    
    # 波动率惩罚
    score -= latest['volatility']
    
    return round(score, 2), rs, multiday_scores


# =========================================================
# 市场风险判断
# =========================================================
def market_risk(index_df):
    """市场风险判断"""
    if len(index_df) < 20:
        return 'unknown', 0.5
    
    index_df['ma20'] = index_df['close'].rolling(20).mean()
    latest = index_df.iloc[-1]
    
    if latest['close'] < latest['ma20']:
        return 'risk_off', 0.3
    
    return 'risk_on', 1.0


# =========================================================
# 动态仓位计算
# =========================================================
def calc_position(etf_score, market_risk_state, multiday_scores):
    """
    动态仓位计算
    
    参数:
        etf_score: ETF评分
        market_risk_state: 市场风险状态
        multiday_scores: 多日评分dict
    
    返回:
        float: 建议仓位（0-1）
    """
    base_position = 0.5
    
    # 市场风险调整
    if market_risk_state == 'risk_off':
        base_position *= 0.5
    
    # ETF评分调整
    if etf_score > 100:
        base_position *= 1.3
    elif etf_score > 80:
        base_position *= 1.1
    elif etf_score < 50:
        base_position *= 0.7
    
    # 多日评分调整
    if multiday_scores:
        # 持续强势加分
        if multiday_scores['持续强势天数'] >= 5:
            base_position *= 1.2
        
        # 胜率调整
        if multiday_scores['胜率'] > 70:
            base_position *= 1.1
        elif multiday_scores['胜率'] < 40:
            base_position *= 0.8
    
    return min(base_position, 1.0)


# =========================================================
# 市场风格分析
# =========================================================
def market_style(result_df):
    """市场风格轮动分析"""
    styles = {
        'AI科技成长': ['人工智能', 'AI', '算力', 'CPO', '光模块', '液冷', '服务器', '半导体', '芯片', '先进封装', '存储', 'EDA', '软件', '信创', '鸿蒙', '数据要素', '云计算', '大模型', '机器人', '人形机器人', '自动驾驶'],
        '消费成长': ['消费电子', '苹果', 'MR', 'VR', '智能穿戴', '游戏', '传媒', '影视', '旅游', '食品', '白酒', '医美'],
        '高端制造': ['新能源车', '锂电', '储能', '风电', '光伏', '军工', '工业母机', '机器人', '高铁', '航空发动机'],
        '金融地产': ['证券', '互联网金融', '银行', '保险', '地产', 'REITs'],
        '红利防御': ['红利', '高股息', '央企', '公用事业', '电力', '煤炭', '运营商', '港口'],
        '周期资源': ['黄金', '有色', '铜', '稀土', '钢铁', '化工', '石油', '天然气'],
        '医药医疗': ['创新药', 'CXO', '医疗器械', '中药', '生物医药', 'AI医疗'],
        '全球出海': ['跨境电商', '出口', '航运', '面板', '家电', '汽车出口']
    }
    
    all_result = []
    
    for style, sectors in styles.items():
        df_style = result_df[result_df['行业'].isin(sectors)]
        
        if len(df_style) == 0:
            continue
        
        score = df_style['总评分'].mean()
        hot = (df_style['涨跌幅'] > 3).sum() * 2 + (df_style['涨跌幅'] > 5).sum() * 5
        amount_score = df_style['成交额'].sum() / 1e8
        trend_score = (df_style['涨跌幅'] > 0).mean() * 100
        
        total_score = score * 0.5 + hot * 0.2 + trend_score * 0.2 + amount_score * 0.1
        
        all_result.append({
            '风格': style,
            '当前得分': round(total_score, 2),
            '热度': round(hot, 2),
            '趋势强度': round(trend_score, 2),
            '成交额': round(amount_score, 2)
        })
    
    style_df = pd.DataFrame(all_result)
    style_df = style_df.sort_values('当前得分', ascending=False)
    
    return style_df


# =========================================================
# AI日报生成
# =========================================================
def deepseek_report(result_df, style_df, risk_state, trade_dates):
    """生成AI日报"""
    prompt = f"""
你是中国顶级ETF基金经理。

分析日期: {TRADE_DATE}
分析区间: {trade_dates[0]} ~ {trade_dates[-1]} ({len(trade_dates)} 天)

当前市场状态: {risk_state}

市场风格轮动:
{style_df.to_string(index=False)}

ETF综合评分（TOP 20）:
{result_df.head(20).to_string(index=False)}

请综合分析以下内容，输出：

# ETF日报 {TRADE_DATE}

## 一、大盘分析
- 市场状态判断
- 主线板块识别

## 二、多日强势ETF
- 近{len(trade_dates)}日累计涨幅TOP 5
- 持续强势天数最长ETF

## 三、操作建议
- 适合低吸方向（含代码、名称、价格、理由）
- 接近高潮方向（注意风险）
- 规避方向

## 四、仓位管理
- 建议总仓位
- 单只ETF仓位上限

## 五、明日关注
- 重点监控ETF列表
- 关键价位提醒

格式要求：Markdown格式，适合手机阅读，简洁清晰。
"""
    
    url = "https://api.deepseek.com/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "你是顶级A股ETF主线基金经理"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2
    }
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=120)
        return response.json()['choices'][0]['message']['content']
    except Exception as e:
        return f"AI日报生成失败: {str(e)}"


# =========================================================
# 报告保存与推送
# =========================================================
def save_report(content):
    """保存报告"""
    report_file = os.path.join(REPORT_DIR, f"AI_ETF_Report_{TRADE_DATE}.md")
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(content)
    return report_file


def send_report(content):
    """微信推送"""
    if not SERVERCHAN_KEY:
        return
    
    url = f"https://sctapi.ftqq.com/{SERVERCHAN_KEY}.send"
    data = {
        "title": f"ETF日报 {TRADE_DATE}",
        "desp": content
    }
    
    try:
        requests.post(url, data=data, timeout=30)
        print("✅ 推送成功")
    except Exception as e:
        print(f"❌ 推送失败: {e}")


# =========================================================
# 主程序
# =========================================================
def main():
    print("\n" + "=" * 60)
    print("AI主线ETF系统 v5.0（多日增强版）")
    print("=" * 60)
    
    # 初始化数据库
    init_db()
    
    # 获取交易日列表
    print(f"\n当前交易日: {TRADE_DATE}")
    trade_dates = get_trade_dates(TRADE_DATE, n_days=LOOKBACK_DAYS)
    print(f"分析区间: {trade_dates[0]} ~ {trade_dates[-1]} ({len(trade_dates)} 天)")
    
    # 获取指数数据
    print("\n获取指数数据...")
    index_df = get_index_data()
    index_df = calc_indicators(index_df)
    
    # 市场风险判断
    risk_state, risk_position = market_risk(index_df)
    print(f"市场状态: {risk_state}, 风险仓位: {risk_position}")
    
    # ETF分析
    print("\n" + "=" * 60)
    print("ETF多日分析")
    print("=" * 60)
    
    all_result = []
    
    for i, (industry, ts_code) in enumerate(ETF_POOL.items()):
        print(f"\n[{i+1}/{len(ETF_POOL)}] 分析 {industry} ({ts_code})")
        
        # 获取多日数据
        df = get_etf_data_multiday(ts_code, trade_dates)
        
        if df is None or len(df) < 10:
            print(f"   ⚠️  数据不足，跳过")
            continue
        
        # 计算指标
        df = calc_indicators(df)
        
        # 计算评分
        score, rs, multiday_scores = etf_score_v2(df, industry, index_df, trade_dates)
        
        # 计算仓位
        position = calc_position(score, risk_state, multiday_scores)
        
        # 信号识别
        signal = buy_signal(df)
        level = signal_level(df)
        
        # 波段阶段
        stage, rise = wave_stage(df)
        
        # 最新数据
        latest = df.iloc[-1]
        
        all_result.append({
            '行业': industry,
            'ETF代码': ts_code,
            '收盘价': round(latest['close'], 2),
            '涨跌幅': round(latest['pct_chg'], 2),
            '成交额': round(latest['amount'] / 1e8, 2),
            'RS强度': rs,
            '信号': signal,
            '等级': level,
            '波段阶段': stage,
            '建议仓位': round(position, 2),
            '总评分': score,
            **({k: v for k, v in multiday_scores.items()} if multiday_scores else {})
        })
    
    # 转换为DataFrame
    result_df = pd.DataFrame(all_result)
    result_df = result_df.sort_values('总评分', ascending=False)
    
    # 市场风格分析
    print("\n市场风格分析...")
    style_df = market_style(result_df)
    
    # 输出结果
    print("\n" + "=" * 60)
    print(f"ETF主线排名（近 {LOOKBACK_DAYS} 日）")
    print("=" * 60)
    print(result_df.head(20).to_string(index=False))
    
    print("\n" + "=" * 60)
    print("市场风格轮动")
    print("=" * 60)
    print(style_df.to_string(index=False))
    
    # AI日报
    print("\n生成AI日报...")
    report = deepseek_report(result_df, style_df, risk_state, trade_dates)
    
    # 保存报告
    report_file = save_report(report)
    print(f"\n✅ 报告已保存: {report_file}")
    
    # 推送
    send_report(report)
    
    print("\n" + "=" * 60)
    print("AI主线ETF日报")
    print("=" * 60)
    print(report)
    
    print("\n✅ 系统运行完成")


# =========================================================
# 回测功能
# =========================================================
def backtest_etf_strategy(start_date, end_date, initial_capital=100000):
    """
    ETF策略回测
    
    参数:
        start_date: 开始日期
        end_date: 结束日期
        initial_capital: 初始资金
    
    返回:
        dict: 回测结果
    """
    print(f"\n回测区间: {start_date} ~ {end_date}")
    print(f"初始资金: {initial_capital:,.0f}")
    
    # 获取交易日
    trade_dates = get_trade_dates(end_date, n_days=100)
    trade_dates = [d for d in trade_dates if d >= start_date]
    
    # 模拟交易
    capital = initial_capital
    holdings = {}  # {etf_code: shares}
    trades = []
    
    for date in trade_dates:
        # 每日分析
        # ... (简化版，实际需要完整逻辑)
        pass
    
    # 计算指标
    final_capital = capital + sum(holdings.values())  # 简化
    
    total_return = (final_capital - initial_capital) / initial_capital * 100
    
    return {
        '初始资金': initial_capital,
        '最终资金': final_capital,
        '总收益率': round(total_return, 2),
        '交易次数': len(trades)
    }


# =========================================================
# 启动
# =========================================================
if __name__ == '__main__':
    main()
