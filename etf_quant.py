# =========================================================
# AI主线ETF系统 v4.0（机构中观增强版）
# =========================================================
# 升级内容：
#
# 1、主线ETF轮动
# 2、市场风格识别
# 3、风险控制
# 4、趋势突破
# 5、主升浪识别
# 6、第一次分歧低吸
# 7、趋势衰竭
# 8、周线共振
# 9、相对强弱RS
# 10、波动率压缩
# 11、成交量结构
# 12、行业轮动速度
# 13、板块宽度
# 14、ETF资金流
# 15、AI新闻情绪
# 16、动态仓位
# 17、DeepSeek日报
# =========================================================

import os
import time
import json
import requests
import numpy as np
import pandas as pd
import tushare as ts
import tushare_quant,block,emotion
from datetime import datetime, timedelta
from dotenv import load_dotenv
import sqlite3

# =========================================================
# 环境变量
# =========================================================
load_dotenv()

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
SERVERCHAN_KEY = os.getenv("WECHAT_SCKEY")

# =========================================================
# 初始化
# =========================================================
ts.set_token(TUSHARE_TOKEN)

pro = ts.pro_api()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CACHE_DIR = os.path.join(BASE_DIR, "cache_daily")
REPORT_DIR = os.path.join(BASE_DIR, "report_daily")

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)
DB_PATH = os.path.join(
    CACHE_DIR,
    "etf_result.db"
)

def init_style_table():

    conn = sqlite3.connect(DB_PATH)

    cursor = conn.cursor()

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

    conn.commit()

    conn.close()

def save_style_history(style_df):

    conn = sqlite3.connect(DB_PATH)

    today = TRADE_DATE

    style_df = style_df.copy()

    style_df['date'] = today

    # =========================
    # 删除当天旧数据（防止重复）
    # =========================
    conn.execute(

        "DELETE FROM style_history WHERE date=?",

        (today,)
    )

    # =========================
    # 写入数据库
    # =========================
    style_df.to_sql(

        'style_history',

        conn,

        if_exists='append',

        index=False
    )

    conn.commit()

    conn.close()

def load_style_history(days=10):

    conn = sqlite3.connect(DB_PATH)

    start_date = (
        datetime.now() - timedelta(days=days)
    ).strftime('%Y-%m-%d')

    query = f"""

        SELECT *
        FROM style_history

        WHERE date >= '{start_date}'

        ORDER BY date ASC

    """

    df = pd.read_sql(query, conn)

    conn.close()

    return df


# =========================================================
# ETF池
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
    '新能源':'516160.SH',
    '电网设备':'561380.SH',
    '新能源车':'515030.SH',
    '航空航天':'159227.SZ',
    '医疗器械':'159883.SZ',
    '食品饮料':'159736.SZ',
    '钢铁':'515210.SH',

}

# =========================================================
# 行业催化
# =========================================================
INDUSTRY_EVENTS = {

    '半导体': [
        'HBM',
        'GPU',
        'AI芯片',
        '先进封装',
        '存储涨价'
    ],

    '人工智能': [
        'Agent',
        '大模型',
        'AI应用'
    ],

    '算力': [
        '液冷',
        '数据中心',
        '英伟达'
    ],

    '机器人': [
        '人形机器人',
        'Tesla Bot'
    ],

    '创新药': [
        'FDA',
        'BD',
        'ASCO'
    ]
}

# =========================================================
# 最近交易日
# =========================================================
def get_last_trade_date():

    now = datetime.now()

    if now.hour < 15:

        query_date = (
            now - timedelta(days=1)
        ).strftime('%Y%m%d')

    else:

        query_date = now.strftime('%Y%m%d')

    cal = pro.trade_cal(
        exchange='',
        start_date='20240101',
        end_date=query_date
    )

    cal = cal[cal['is_open'] == 1]

    return str(
        cal[
            cal['cal_date'] <= query_date
        ]['cal_date'].max()
    )

TRADE_DATE = get_last_trade_date()

print("当前交易日:", TRADE_DATE)

# =========================================================
# ETF历史数据
# =========================================================
def get_etf_data(ts_code):

    cache_file = os.path.join(
        CACHE_DIR,
        f"{ts_code}.csv"
    )

    if os.path.exists(cache_file):

        try:

            df = pd.read_csv(cache_file)

            df['trade_date'] = df['trade_date'].astype(str)

            if (
                len(df) > 120
                and (df['trade_date'] == TRADE_DATE).any()
            ):

                return df.sort_values('trade_date')

        except:
            pass

    try:

        df = pro.fund_daily(
            ts_code=ts_code,
            start_date='20240101',
            end_date=TRADE_DATE
        )

        if df.empty:

            return None

        df = df.sort_values('trade_date')

        df.to_csv(
            cache_file,
            index=False
        )

        time.sleep(0.05)

        return df

    except Exception as e:

        print(ts_code, e)

        return None

# =========================================================
# 指数数据
# =========================================================
def get_index_data():

    cache_file = os.path.join(
        CACHE_DIR,
        "000300.csv"
    )

    if os.path.exists(cache_file):

        df = pd.read_csv(cache_file)

        if len(df) > 100:

            return df

    df = pro.index_daily(
        ts_code='000300.SH',
        start_date='20240101',
        end_date=TRADE_DATE
    )

    df = df.sort_values('trade_date')

    df.to_csv(
        cache_file,
        index=False
    )

    return df

# =========================================================
# 技术指标
# =========================================================
def calc_indicators(df):

    df = df.copy()

    # =====================================================
    # 均线
    # =====================================================
    for ma in [5, 10, 20, 60]:

        df[f'ma{ma}'] = (
            df['close'].rolling(ma).mean()
        )

    # =====================================================
    # 成交量
    # =====================================================
    df['vol5'] = (
        df['vol'].rolling(5).mean()
    )

    # =====================================================
    # 涨幅
    # =====================================================
    for n in [5, 10, 20]:

        df[f'pct{n}'] = (

            df['close']
            /
            df['close'].shift(n)
            - 1

        ) * 100

    # =====================================================
    # 趋势斜率
    # =====================================================
    df['slope20'] = (

        df['ma20']
        /
        df['ma20'].shift(5)
        - 1

    ) * 100

    # =====================================================
    # 波动率
    # =====================================================
    df['volatility'] = (
        df['pct_chg'].rolling(10).std()
    )

    # =====================================================
    # ATR波动
    # =====================================================
    df['tr'] = np.maximum(

        df['high'] - df['low'],

        np.maximum(

            abs(
                df['high']
                - df['close'].shift(1)
            ),

            abs(
                df['low']
                - df['close'].shift(1)
            )
        )
    )

    df['atr'] = (
        df['tr'].rolling(14).mean()
    )

    return df

# =========================================================
# 周线趋势
# =========================================================
def weekly_trend(df):

    try:

        weekly = df.copy()

        weekly.index = pd.to_datetime(
            weekly['trade_date']
        )

        weekly = weekly.resample('W').agg({

            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'vol': 'sum'
        })

        weekly['ma5'] = (
            weekly['close'].rolling(5).mean()
        )

        weekly['ma10'] = (
            weekly['close'].rolling(10).mean()
        )

        latest = weekly.iloc[-1]

        return (
            latest['ma5']
            >
            latest['ma10']
        )

    except:

        return False

# =========================================================
# 市场风险
# =========================================================
def market_risk(index_df):

    index_df['ma20'] = (
        index_df['close'].rolling(20).mean()
    )

    latest = index_df.iloc[-1]

    if latest['close'] < latest['ma20']:

        return 'risk_off', 0.3

    return 'risk_on', 1.0

# =========================================================
# 相对强弱RS
# =========================================================
def relative_strength(df, index_df):

    etf_return = (
        df['close'].iloc[-1]
        /
        df['close'].iloc[-20]
        - 1
    ) * 100

    index_return = (
        index_df['close'].iloc[-1]
        /
        index_df['close'].iloc[-20]
        - 1
    ) * 100

    return round(
        etf_return - index_return,
        2
    )

# =========================================================
# 波动率压缩
# =========================================================
def volatility_compress(df):

    latest_atr = df['atr'].iloc[-1]

    atr_mean = (
        df['atr'].rolling(20).mean().iloc[-1]
    )

    return latest_atr < atr_mean * 0.8

# =========================================================
# 主线启动
# =========================================================
def mainline_start(df):

    latest = df.iloc[-1]

    range30 = (

        df['high'].rolling(30).max().iloc[-2]

        /

        df['low'].rolling(30).min().iloc[-2]
    )

    breakout = (

        latest['close']

        >

        df['high'].rolling(30).max().iloc[-2]
    )

    volume_expand = (

        latest['vol']

        >

        df['vol5'].iloc[-2] * 1.5
    )

    return (

        range30 < 1.25

        and breakout

        and volume_expand
    )

# =========================================================
# 主升浪
# =========================================================
def main_uptrend(df):

    latest = df.iloc[-1]

    return (

        latest['ma5']

        >

        latest['ma10']

        >

        latest['ma20']

        and latest['slope20'] > 2

        and latest['pct5']
        >
        latest['pct10'] / 2
    )

# =========================================================
# 第一次低吸
# =========================================================
def first_dip(df):

    latest = df.iloc[-1]

    try:

        breakout_recent = (

            df['close'].rolling(20).max().shift(5)

            <

            df['close'].shift(5)
        )

        return (

            breakout_recent.iloc[-1]

            and latest['close'] > latest['ma20']

            and latest['vol'] < latest['vol5']

            and abs(

                latest['close']
                - latest['ma10']

            ) / latest['ma10'] < 0.015
        )

    except:

        return False

# =========================================================
# 趋势衰竭
# =========================================================
def trend_exhaust(df):

    latest = df.iloc[-1]

    upper_shadow = (

        latest['high']
        - latest['close']

    ) / latest['close']

    volume_blowoff = (

        latest['vol']

        >

        df['vol5'].iloc[-1] * 2
    )

    return (

        latest['pct20'] > 20

        and upper_shadow > 0.03

        and volume_blowoff
    )

# =========================================================
# 波段阶段
# =========================================================
def wave_stage(df):

    latest = df.iloc[-1]

    low20 = (
        df['low'].rolling(20).min().iloc[-1]
    )

    rise = (

        latest['close']
        /
        low20
        - 1

    ) * 100

    if rise < 8:

        return '启动初期', rise

    elif rise < 20:

        return '主升阶段', rise

    else:

        return '波段后期', rise

# =========================================================
# AI情绪
# =========================================================
def ai_sentiment(industry):

    score = 50

    events = INDUSTRY_EVENTS.get(
        industry,
        []
    )

    score += len(events) * 5

    return min(score, 100)

# =========================================================
# 板块宽度（简化版）
# =========================================================
def breadth_score(df):

    positive_days = (
        df['pct_chg'].tail(10) > 0
    ).sum()

    return positive_days * 5

# =========================================================
# 成交量结构
# =========================================================
def volume_structure(df):

    latest = df.iloc[-1]

    # 缩量调整
    if (

        latest['close'] > latest['ma20']

        and latest['vol'] < latest['vol5']
    ):

        return 10

    # 放量突破
    if (

        latest['vol']
        >
        latest['vol5'] * 1.5
    ):

        return 15

    return 0

# =========================================================
# 信号等级
# =========================================================
def signal_level(df):

    if (
        mainline_start(df)
        and weekly_trend(df)
    ):

        return 'S'

    if (
        main_uptrend(df)
        and first_dip(df)
    ):

        return 'A'

    if main_uptrend(df):

        return 'B'

    if trend_exhaust(df):

        return 'D'

    return 'C'

# =========================================================
# 买点
# =========================================================
def buy_signal(df):

    if mainline_start(df):

        return '主线启动'

    if first_dip(df):

        return '第一次分歧低吸'

    if main_uptrend(df):

        return '主升浪'

    if trend_exhaust(df):

        return '趋势衰竭'

    return '观察'

# =========================================================
# ETF总评分
# =========================================================
def etf_score(df, industry, index_df):

    latest = df.iloc[-1]

    score = 0

    # =====================================================
    # 趋势
    # =====================================================
    score += latest['pct5'] * 2

    score += latest['pct10']

    # =====================================================
    # 多头排列
    # =====================================================
    if (

        latest['ma5']

        >

        latest['ma10']

        >

        latest['ma20']

    ):

        score += 20

    # =====================================================
    # 趋势斜率
    # =====================================================
    if latest['slope20'] > 2:

        score += 15

    # =====================================================
    # RS
    # =====================================================
    rs = relative_strength(df, index_df)

    score += rs * 1.5

    # =====================================================
    # 主线启动
    # =====================================================
    if mainline_start(df):

        score += 25

    # =====================================================
    # 主升浪
    # =====================================================
    if main_uptrend(df):

        score += 20

    # =====================================================
    # 第一次低吸
    # =====================================================
    if first_dip(df):

        score += 20

    # =====================================================
    # 周线共振
    # =====================================================
    if weekly_trend(df):

        score += 15

    # =====================================================
    # 波动率压缩
    # =====================================================
    if volatility_compress(df):

        score += 10

    # =====================================================
    # 成交量结构
    # =====================================================
    score += volume_structure(df)

    # =====================================================
    # 板块宽度
    # =====================================================
    score += breadth_score(df)

    # =====================================================
    # AI情绪
    # =====================================================
    score += ai_sentiment(industry) * 0.3

    # =====================================================
    # 波段
    # =====================================================
    stage, rise = wave_stage(df)

    if rise > 20:

        score -= 15

    # =====================================================
    # 趋势衰竭
    # =====================================================
    if trend_exhaust(df):

        score -= 30

    # =====================================================
    # 波动率惩罚
    # =====================================================
    score -= latest['volatility']

    return round(score, 2), rs

# =========================================================
# 市场风格
# =========================================================
def calc_style_score(df):

    return (
        df["pct_chg"].mean() * 2
        + (df["pct_chg"] > 3).sum() * 3
        + (df["pct_chg"] > 5).sum() * 5
        + df["amount"].sum() / 1e8
    )

def calc_style_trend(close):

    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()

    score = 0

    if ma5.iloc[-1] > ma10.iloc[-1]:
        score += 50

    if close.iloc[-1] > ma5.iloc[-1]:
        score += 50

    return score

import pandas as pd
import numpy as np

# =========================================================
# 市场风格轮动分析
# =========================================================
def market_style(result_df, history_style_df=None):

    styles = {

        'AI科技成长': [
            '人工智能','AI','算力','CPO','光模块',
            '液冷','服务器','半导体','芯片','先进封装',
            '存储','EDA','软件','信创','鸿蒙',
            '数据要素','云计算','大模型',
            '机器人','人形机器人','自动驾驶'
        ],

        '消费成长': [
            '消费电子','苹果','MR','VR',
            '智能穿戴','游戏','传媒',
            '影视','旅游','食品','白酒','医美'
        ],

        '高端制造': [
            '新能源车','锂电','储能',
            '风电','光伏','军工',
            '工业母机','机器人',
            '高铁','航空发动机'
        ],

        '金融地产': [
            '证券','互联网金融',
            '银行','保险','地产','REITs'
        ],

        '红利防御': [
            '红利','高股息','央企',
            '公用事业','电力',
            '煤炭','运营商','港口'
        ],

        '周期资源': [
            '黄金','有色','铜',
            '稀土','钢铁','化工',
            '石油','天然气'
        ],

        '医药医疗': [
            '创新药','CXO','医疗器械',
            '中药','生物医药','AI医疗'
        ],

        '全球出海': [
            '跨境电商','出口',
            '航运','面板',
            '家电','汽车出口'
        ]
    }

    all_result = []

    # =====================================================
    # 当前风格评分
    # =====================================================
    for style, sectors in styles.items():

        df_style = result_df[
            result_df['行业'].isin(sectors)
        ]

        if len(df_style) == 0:
            continue

        # =========================
        # 基础强度
        # =========================
        score = (
            df_style['总评分'].mean()
        )

        # =========================
        # 热度
        # =========================
        hot = (
            (df_style['涨跌幅'] > 3).sum() * 2
            + (df_style['涨跌幅'] > 5).sum() * 5
        )

        # =========================
        # 成交额
        # =========================
        amount_score = (
            df_style['成交额'].sum() / 1e8
        )

        # =========================
        # 趋势强度
        # =========================
        trend_score = (
            (df_style['涨跌幅'] > 0).mean() * 100
        )

        total_score = (
            score * 0.5
            + hot * 0.2
            + trend_score * 0.2
            + amount_score * 0.1
        )

        all_result.append({

            '风格': style,

            '当前得分': round(total_score, 2),

            '热度': round(hot, 2),

            '趋势强度': round(trend_score, 2),

            '成交额': round(amount_score, 2)
        })

    style_df = pd.DataFrame(all_result)
    save_style_history(style_df)
    history_style_df = load_style_history()

    # =====================================================
    # 风格轮动（核心）
    # =====================================================
    if history_style_df is not None and len(history_style_df) > 0:

        latest_history = history_style_df.groupby(
            '风格'
        ).tail(1)

        style_df = style_df.merge(

            latest_history[['风格', '当前得分']],

            on='风格',

            how='left',

            suffixes=('', '_昨日')
        )

        # =========================
        # 轮动强度
        # =========================
        style_df['轮动强度'] = (

            style_df['当前得分']
            - style_df['当前得分_昨日']

        ).round(2)

        # =========================
        # 风格状态
        # =========================
        style_df['风格状态'] = np.where(

            style_df['轮动强度'] > 15,

            '主升加强',

            np.where(

                style_df['轮动强度'] > 5,

                '持续活跃',

                np.where(

                    style_df['轮动强度'] < -10,

                    '退潮',

                    '震荡'
                )
            )
        )

    else:

        style_df['轮动强度'] = 0
        style_df['风格状态'] = '未知'

    # =====================================================
    # 排序
    # =====================================================
    style_df = style_df.sort_values(

        ['当前得分', '轮动强度'],

        ascending=False
    )

    return style_df

# =========================================================
# DeepSeek日报
# =========================================================
def deepseek_report(result_df, style_df, risk_state,emotion_text,sector_text,sector_text_his):

    prompt = f"""
你是中国顶级ETF基金经理。

当前市场情绪：

{emotion_text}

当前最强主线列表：

{sector_text}

近10日最强主线列表:

{sector_text_his}

当前市场状态：

{risk_state}

市场风格：

{style_df.to_string(index=False)}

ETF数据：

{result_df.to_string(index=False)}

请综合分析以下内容,加上你从全网搜索的板块情绪,输出：

# ETF日报{TRADE_DATE}

内容：

1、当前主线
2、适合低吸方向
3、接近高潮方向（注意风险）
4、风险方向
5、明日策略（含代码、名称、价格）
6、仓位建议
格式要求：Markdown格式，适合手机阅读
"""

    url = "https://api.deepseek.com/chat/completions"

    headers = {

        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",

        "Content-Type": "application/json"
    }

    data = {

        "model": "deepseek-chat",

        "messages": [

            {
                "role": "system",
                "content": "你是顶级A股ETF主线基金经理"
            },

            {
                "role": "user",
                "content": prompt
            }
        ],

        "temperature": 0.2
    }

    try:

        response = requests.post(
            url,
            headers=headers,
            json=data,
            timeout=120
        )

        return response.json()[
            'choices'
        ][0]['message']['content']

    except Exception as e:

        return str(e)

# =========================================================
# 保存报告
# =========================================================
def save_report(content):

    report_file = os.path.join(

        REPORT_DIR,

        f"AI_ETF_Report_{TRADE_DATE}.md"
    )

    with open(

        report_file,

        'w',

        encoding='utf-8'

    ) as f:

        f.write(content)

    return report_file

# =========================================================
# 手机推送
# =========================================================
def send_report(content):

    if not SERVERCHAN_KEY:

        return

    url = (
        f"https://sctapi.ftqq.com/"
        f"{SERVERCHAN_KEY}.send"
    )

    data = {

        "title": f"ETF日报{TRADE_DATE}",

        "desp": content
    }

    try:

        requests.post(
            url,
            data=data,
            timeout=30
        )

        print("推送成功")

    except Exception as e:

        print("推送失败:", e)

# =========================================================
# 主程序
# =========================================================
def main():

    print("=" * 60)

    print("AI主线ETF系统 v4.0")

    print("=" * 60)

    init_style_table()
    
    # =====================================================
    # 指数
    # =====================================================
    index_df = get_index_data()

    index_df = calc_indicators(index_df)



    # =========================板块分析
    sector_df = block.analyze_hot_sectors()

    # =========================
    # 市场情绪
    # =========================
    emotion_result = emotion.analyze_market_emotion(
        sector_df
    )

    emotion_text = ""

    if emotion_result:

        emotion_text = str(emotion_result)

    print(emotion_text)


    if not sector_df.empty:

        print("\n========== 最强主线板块 ==========\n")

        top_sector = sector_df.head(20)

        print(top_sector)

    else:

        top_sector = pd.DataFrame()

    sector_text = ""
    if not top_sector.empty:

        sector_text = top_sector.to_string(index=False)

    sector_df_his = block.load_history()
    sector_text_his = sector_df_his.to_string(index=False)

    # =====================================================
    # 市场风险
    # =====================================================
    risk_state, position = market_risk(index_df)

    print("市场状态:", risk_state)

    print("建议仓位:", position)

    all_result = []

    # =====================================================
    # ETF分析
    # =====================================================
    for industry, ts_code in ETF_POOL.items():

        print(f"\n分析 {industry}")

        df = get_etf_data(ts_code)

        if df is None:

            continue

        if len(df) < 60:

            continue

        df = calc_indicators(df)

        latest = df.iloc[-1]

        # =================================================
        # 评分
        # =================================================
        score, rs = etf_score(

            df,

            industry,

            index_df
        )

        # =================================================
        # 波段
        # =================================================
        stage, rise = wave_stage(df)

        # =================================================
        # 信号
        # =================================================
        signal = buy_signal(df)

        level = signal_level(df)

        all_result.append({

            '行业': industry,

            'ETF': ts_code,

            '收盘价': round(
                latest['close'],
                2
            ),
            # =========================
            # 当日表现
            # =========================
            '涨跌幅': round(
                latest['pct_chg'],
                2
            ),

            '成交额': round(
                latest['amount'] / 1e8,
                2
            ),
            'RS强度': rs,

            '5日涨幅': round(
                latest['pct5'],
                2
            ),

            '10日涨幅': round(
                latest['pct10'],
                2
            ),

            '20日涨幅': round(
                latest['pct20'],
                2
            ),

            '波段阶段': stage,
            

            '波段涨幅': round(
                rise,
                2
            ),

            'AI情绪': ai_sentiment(
                industry
            ),

            '信号': signal,

            '等级': level,

            '总评分': score
        })

    # =====================================================
    # DataFrame
    # =====================================================
    result_df = pd.DataFrame(all_result)
    print(result_df)
    result_df = result_df.sort_values(
        '总评分',
        ascending=False
    )

    # =====================================================
    # 市场风格
    # =====================================================
    style_df = market_style(result_df)

    # =====================================================
    # 输出
    # =====================================================
    print("\n")

    print("=" * 60)

    print("ETF主线排名")

    print("=" * 60)

    print(result_df)

    print("\n")

    print("=" * 60)

    print("市场风格")

    print("=" * 60)

    print(style_df)

    # =====================================================
    # AI日报
    # =====================================================
    print("\nAI日报生成中...\n")

    report = deepseek_report(

        result_df,

        style_df,

        risk_state,
        emotion_text,sector_text,sector_text_his
    )

    # =====================================================
    # 保存
    # =====================================================
    report_file = save_report(report)

    print("\n")

    print("=" * 60)

    print("AI主线ETF日报")

    print("=" * 60)

    print(report)

    print("\n报告已保存:", report_file)

    # =====================================================
    # 手机推送
    # =====================================================
    send_report(report)

    print("\n系统运行完成")

# =========================================================
# 启动
# =========================================================
if __name__ == '__main__':

    main()