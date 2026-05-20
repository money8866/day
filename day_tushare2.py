### =====龙头及中军复盘 =====###

import os
import tushare as ts
import pandas as pd
import numpy as np
import requests
from datetime import datetime, time
from dotenv import load_dotenv

load_dotenv()

# ========= Tushare 初始化 =========
TOKEN = os.getenv("TUSHARE_TOKEN")
pro = ts.pro_api(TOKEN)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_daily")
REPORT_DIR = os.path.join(BASE_DIR, "report_daily")

def get_last_trade_date():

    today = datetime.today().strftime('%Y%m%d')

    # 获取交易日历
    cal = pro.trade_cal(
        exchange='',
        start_date='20200101',
        end_date=today
    )

    # 只保留开市日
    cal = cal[cal['is_open'] == 1]

    # 找到小于今天的最近交易日
    last_trade_date = cal[cal['cal_date'] <= today]['cal_date'].max()

    return last_trade_date

TRADE_DATE = get_last_trade_date()

print("当前交易日:", TRADE_DATE)

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

                return df.sort_values('trade_date')

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
        time.sleep(0.01)

        return df

    except Exception as e:

        print(f"{ts_code} 下载失败:", e)

        return None
    
# =========================
# 1. 获取行情数据（替换AkShare🔥）
# =========================
import os
import pandas as pd

# 缓存目录
os.makedirs(CACHE_DIR, exist_ok=True)


def get_market_data():

    # ========= 获取最近交易日 =========
    today = get_last_trade_date()

    print(f"当前交易日: {today}")

    # ========= 缓存文件 =========
    cache_file = os.path.join(
        CACHE_DIR,
        f"Market_{today}.csv"
    )

    # ========= 优先读取缓存 =========
    if os.path.exists(cache_file):

        print(f"读取缓存: {cache_file}")

        df = pd.read_csv(
            cache_file,
            dtype={
                '代码': str
            }
        )

        return df

    print("缓存不存在，开始从Tushare下载数据...")

    # ========= 1. 日线行情 =========
    daily_df = pro.daily(
        trade_date=today
    )

    # ========= 2. 基本面 =========
    basic_df = pro.daily_basic(
        trade_date=today,
        fields='''
            ts_code,
            total_mv
        '''
    )

    # ========= 3. 股票名称 =========
    name_df = pro.stock_basic(
        fields='''
            ts_code,
            name
        '''
    )

    # ========= 4. 合并 =========
    df = daily_df.merge(
        basic_df,
        on='ts_code',
        how='left'
    )

    df = df.merge(
        name_df,
        on='ts_code',
        how='left'
    )

    # ========= 5. 字段处理 =========
    df['代码'] = (
        df['ts_code']
        .str
        .split('.')
        .str[0]
    )

    df['名称'] = df['name']

    df['最新价'] = df['close']

    df['涨跌幅'] = df['pct_chg']

    # Tushare amount单位为千元
    df['成交额'] = (
        df['amount'] * 1000
    )

    # total_mv单位为万元
    df['总市值'] = (
        df['total_mv'] * 10000
    )

    # ========= 6. 保留字段 =========
    df = df[
        [
            '代码',
            '名称',
            '最新价',
            '涨跌幅',
            '成交额',
            '总市值'
        ]
    ]

    # ========= 7. 删除空值 =========
    df.dropna(inplace=True)

    # ========= 8. 保存缓存 =========
    df.to_csv(
        cache_file,
        index=False,
        encoding='utf-8-sig'
    )

    print(f"缓存已保存: {cache_file}")

    return df

# =========================
# 2. 获取行业映射（替换AkShare🔥）
# =========================
def get_industry_map():
    df = pro.stock_basic(exchange='', list_status='L',
                         fields='ts_code,industry')

    df['代码'] = df['ts_code'].apply(lambda x: x.split('.')[0])
    df['板块'] = df['industry']

    return df[['代码', '板块']].dropna()


# =========================
# 3. 板块打分模型（不变）
# =========================
def calc_sector_score(df):
    sector = df.groupby('板块').agg({
        '涨跌幅': 'mean',
        '成交额': 'sum',
        '代码': 'count'
    }).rename(columns={'代码': '个股数'})
    
    limit_up = df[df['涨跌幅'] > 9.5].groupby('板块').size()
    sector['涨停数'] = limit_up
    sector['涨停数'] = sector['涨停数'].fillna(0)
    
    sector['score'] = (
        sector['涨跌幅'] * 2 +
        np.log1p(sector['成交额']) * 5 +
        sector['涨停数'] * 30
    )
    
    return sector.sort_values(by='score', ascending=False)


# =========================
# 4. 龙头识别（不变）
# =========================
def find_leaders(df, board):
    sub = df[df['板块'] == board]
    
    leaders = sub.sort_values(
        by=['涨跌幅', '成交额'],
        ascending=[False, False]
    ).head(3)
    
    return leaders[['代码', '名称', '涨跌幅', '成交额']]


# =========================
# 5. 中军识别（用Tushare历史🔥）
# =========================
def get_hist_data(code):
    ts_code = code + ".SH" if code.startswith("6") else code + ".SZ"

    df = pro.daily(ts_code=ts_code, limit=60)

    df.rename(columns={
        "vol": "成交量",
        "amount": "成交额",
        "close": "收盘",
        "pct_chg": "涨跌幅"
    }, inplace=True)

    return df.sort_values("trade_date")


def check_volume_trend(df_hist):
    if len(df_hist) < 10:
        return False

    vol_recent = df_hist['成交额'].tail(5).mean()
    vol_prev = df_hist['成交额'].iloc[-10:-5].mean()

    return vol_recent > vol_prev and vol_recent < vol_prev * 2


def check_no_limit_up(df_hist):
    recent = df_hist.tail(5)
    return not any(recent['涨跌幅'] > 9.5)


def check_breakout(df_hist):
    if len(df_hist) < 25:
        return False

    recent = df_hist.tail(3)
    prev_high = df_hist['收盘'].iloc[-23:-3].max()

    return any(recent['收盘'] > prev_high)


def find_core_old(df, board):
    sub = df[df['板块'] == board]

    candidates = sub[
        (sub['总市值'] > 100e8) &
        (sub['总市值'] < 9999e8) &
        (sub['涨跌幅'] > 1)
    ]

    result = []

    for _, row in candidates.iterrows():
        code = row['代码']

        try:
            hist = get_hist_data(code)

            if hist.isnull().values.any():
                continue

            if (check_volume_trend(hist) and
                check_no_limit_up(hist) and
                check_breakout(hist)):
                
                result.append({
                    '代码': code,
                    '名称': row['名称'],
                    '涨跌幅': row['涨跌幅'],
                    '成交额': row['成交额'],
                    '总市值': row['总市值']
                })

        except:
            continue

    if len(result) == 0:
        return pd.DataFrame()

    return pd.DataFrame(result).sort_values(by='成交额', ascending=False).head(5)

def find_core(df, board):
    """
    中军筛选逻辑（新版）
    条件：
    1、板块内成交额前5
    2、股价站上5日均线
    3、20日均线持续向上（最近5天20MA斜率为正）
    """

    sub = df[df['板块'] == board].copy()

    # 先按成交额排序，取板块前5
    sub = sub.sort_values(by='成交额', ascending=False).head(5)

    result = []

    for _, row in sub.iterrows():

        code = row['代码']

        try:
            hist = get_hist_data(code)

            # 数据为空跳过
            if hist is None or len(hist) < 30:
                continue

            hist = hist.sort_values('trade_date')

            # ========= 计算均线 =========
            hist['MA5'] = hist['收盘'].rolling(5).mean()
            hist['MA20'] = hist['收盘'].rolling(20).mean()

            latest = hist.iloc[-1]

            # ========= 条件1：站上5日线 =========
            above_ma5 = latest['收盘'] > latest['MA5']

            # ========= 条件2：20日均线持续向上 =========
            ma20_up = (
                hist['MA20'].iloc[-1] >
                hist['MA20'].iloc[-2] >
                hist['MA20'].iloc[-3] >
                hist['MA20'].iloc[-4] >
                hist['MA20'].iloc[-5]
            )

            if above_ma5 and ma20_up:

                result.append({
                    '代码': code,
                    '名称': row['名称'],
                    '涨跌幅': row['涨跌幅'],
                    '成交额': row['成交额'],
                    '总市值': row['总市值'],
                    '是否站上5日线': above_ma5,
                    '20日均线向上': ma20_up
                })

        except Exception as e:
            print(f"{code} 出错: {e}")
            continue

    if len(result) == 0:
        return pd.DataFrame()

    return (
        pd.DataFrame(result)
        .sort_values(by='成交额', ascending=False)
        .reset_index(drop=True)
    )
# =========================
# 微信 + DeepSeek（不变）
# =========================
def send_wechat(msg, key):
    url = f"https://sctapi.ftqq.com/{key}.send"
    data = {"title": "每日复盘", "desp": msg}
    requests.post(url, data=data)


def deepseek(prompt):
    key = os.getenv("DEEPSEEK_API_KEY")
    url = "https://api.deepseek.com/chat/completions"

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json"
    }

    data = {
        "model": "deepseek-v4-pro",
        "messages": [
            {"role": "system", "content": "你是A股顶级游资复盘专家"},
            {"role": "user", "content": prompt}
        ]
    }

    r = requests.post(url, headers=headers, json=data)
    return r.json()['choices'][0]['message']['content']


# =========================
# 主流程
# =========================
def run_analysis():
    print(f"\n📊 盘后分析开始：{datetime.now()}")

    market_df = get_market_data()
    industry_map = get_industry_map()

    industry_df = market_df.merge(industry_map, on='代码', how='inner')

    print("\n====== 行业主线 ======")
    sector = calc_sector_score(industry_df)
    top = sector.head(5)

    print(top[['涨跌幅', '涨停数', 'score']])

    resultlines = []

    for board in top.index:
        resultlines.append(f"\n【行业】{board}")

        leaders = find_leaders(industry_df, board)
        core = find_core(industry_df, board)

        resultlines.append("\n龙头：")
        resultlines.append(leaders.to_string(index=False))

        resultlines.append("\n中军：")
        resultlines.append(core.to_string(index=False))

    text = "".join(resultlines)
    print(text)

    prompt = f"""

你是A股中军分析专家,根据今日量化计算的市场热点板块龙头与中军：{text}

    请按行业分段输出：
    1、龙头股(一句话点评)
    2、龙头股带动的高弹性标的
    3、结合近几日走势和估值、业绩增长确定性、资金净流入情况，判断哪些个股最可能是真正的板块中军（按每个板块输出最终推荐的前2名）
    4、明日板块策略

    要求：
    - 基本面无雷、无减持、无定增预案、确定性高、估值有空间、下方有支撑、承接力强的个股优先
"""
    report = deepseek(prompt)


    print(report)
    with open(
        os.path.join(
            REPORT_DIR,
            f"龙头及中军复盘_{TRADE_DATE}.txt"
        ),
        "w",
        encoding="utf-8"
    ) as f:

        f.write(report)

    send_wechat(report, os.getenv("WECHAT_SCKEY"))



# =========================
# 运行
# =========================


if __name__ == "__main__":
    run_analysis()