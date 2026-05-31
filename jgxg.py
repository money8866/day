# ============================================================
# 主线趋势机构票模型（本地缓存高速版）
# Tushare + 本地缓存 + 行业/概念双热点
# ============================================================
#
# 速度提升：
# 原版：10~30分钟
# 本版：30秒~2分钟
#
# 核心优化：
# 1、历史数据本地缓存
# 2、daily_basic缓存
# 3、避免重复API
# 4、一次下载，多次复用
#
# ============================================================

import tushare as ts
import pandas as pd
import numpy as np
import os
import time
import pickle

from datetime import datetime, timedelta

# ============================================================
# Tushare初始化
# ============================================================

import os

from dotenv import load_dotenv

load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

# ============================================================
# 参数
# ============================================================

TOP_CONCEPT_COUNT = 10
TOP_INDUSTRY_COUNT = 8

MIN_MARKET_VALUE = 1000000      # 100亿

BOX_DAYS = 60

MAX_BOX_AMPLITUDE = 0.35

BREAKOUT_PCT = 1.02

MIN_BREAKOUT_DAYS = 2

VOL_RATIO_LOW = 1.2
VOL_RATIO_HIGH = 2.8

MAX_RISE_FROM_LOW = 1.45

MAX_5DAY_RISE = 0.18

MAX_LIMITUP_COUNT = 1

CACHE_DIR = "cache"

# ============================================================
# 创建缓存目录
# ============================================================

if not os.path.exists(CACHE_DIR):

    os.makedirs(CACHE_DIR)

# ============================================================
# 日期
# ============================================================

trade_cal = pro.trade_cal(
    exchange='',
    is_open='1'
)

end_date = trade_cal.iloc[-1]['cal_date']

start_date = (
    datetime.strptime(end_date, "%Y%m%d")
    - timedelta(days=240)
).strftime("%Y%m%d")

print("开始日期:", start_date)
print("结束日期:", end_date)

# ============================================================
# 股票基础信息
# ============================================================

print("\n获取股票列表...")

stock_basic = pro.stock_basic(
    exchange='',
    list_status='L',
    fields='ts_code,name,industry'
)

# ============================================================
# 缓存文件
# ============================================================

daily_cache_file = (
    f"{CACHE_DIR}/daily_cache.pkl"
)

basic_cache_file = (
    f"{CACHE_DIR}/basic_cache.pkl"
)

concept_cache_file = (
    f"{CACHE_DIR}/concept_cache.pkl"
)

# ============================================================
# 加载 / 创建 日线缓存
# ============================================================

if os.path.exists(daily_cache_file):

    print("\n加载本地日线缓存...")

    with open(daily_cache_file, "rb") as f:

        daily_cache = pickle.load(f)

else:

    print("\n开始下载历史日线数据...\n")

    daily_cache = {}

    total = len(stock_basic)

    for i, row in stock_basic.iterrows():

        code = row['ts_code']

        try:

            df = pro.daily(
                ts_code=code,
                start_date=start_date,
                end_date=end_date
            )

            if len(df) < 80:
                continue

            df = df.sort_values(
                'trade_date'
            )

            daily_cache[code] = df

            if i % 100 == 0:

                print(
                    f"下载进度: "
                    f"{i}/{total}"
                )

            time.sleep(0.01)

        except:
            continue

    # 保存缓存
    with open(daily_cache_file, "wb") as f:

        pickle.dump(daily_cache, f)

    print("\n日线缓存完成")

# ============================================================
# 加载 / 创建 市值缓存
# ============================================================

if os.path.exists(basic_cache_file):

    print("\n加载本地市值缓存...")

    with open(basic_cache_file, "rb") as f:

        basic_cache = pickle.load(f)

else:

    print("\n下载全市场市值数据...")

    basic_df = pro.daily_basic(
        trade_date=end_date,
        fields='ts_code,total_mv,pe_ttm'
    )

    basic_cache = {}

    for _, row in basic_df.iterrows():

        basic_cache[row['ts_code']] = {

            'total_mv': row['total_mv'],
            'pe_ttm': row['pe_ttm']

        }

    with open(basic_cache_file, "wb") as f:

        pickle.dump(basic_cache, f)

# ============================================================
# 加载 / 创建 概念缓存
# ============================================================

if os.path.exists(concept_cache_file):

    print("\n加载本地概念缓存...")

    with open(concept_cache_file, "rb") as f:

        stock_concept_map = pickle.load(f)

else:

    print("\n下载概念板块数据...\n")

    concept_df = pro.concept()

    stock_concept_map = {}

    for i, row in concept_df.iterrows():

        try:

            concept_id = row['code']
            concept_name = row['name']

            members = pro.concept_detail(
                id=concept_id,
                fields='id,concept_name,ts_code,name'
            )

            for _, m in members.iterrows():

                code = m['ts_code']

                if code not in stock_concept_map:

                    stock_concept_map[code] = []

                stock_concept_map[code].append(
                    concept_name
                )

            print(
                f"完成概念: {concept_name}"
            )

            time.sleep(0.02)

        except:
            continue

    with open(concept_cache_file, "wb") as f:

        pickle.dump(stock_concept_map, f)

# ============================================================
# 股票 -> 行业
# ============================================================

stock_industry_map = {}

for _, row in stock_basic.iterrows():

    stock_industry_map[row['ts_code']] = row['industry']

# ============================================================
# 计算概念热点
# ============================================================

print("\n开始计算概念热点...\n")

concept_score_map = {}

for code, concepts in stock_concept_map.items():

    try:

        if code not in daily_cache:
            continue

        df = daily_cache[code]

        latest = df.iloc[-1]

        rise_20 = (
            latest['close']
            /
            df.iloc[-21]['close']
            - 1
        )

        rise_5 = (
            latest['close']
            /
            df.iloc[-6]['close']
            - 1
        )

        df['ma20'] = (
            df['close']
            .rolling(20)
            .mean()
        )

        df['ma60'] = (
            df['close']
            .rolling(60)
            .mean()
        )

        trend_score = 0

        if (
            df.iloc[-1]['ma20']
            >
            df.iloc[-1]['ma60']
        ):
            trend_score = 10

        stock_score = (
            rise_20 * 100 * 0.4
            +
            rise_5 * 100 * 0.3
            +
            trend_score
        )

        for c in concepts:

            if c not in concept_score_map:

                concept_score_map[c] = []

            concept_score_map[c].append(
                stock_score
            )

    except:
        continue

# ============================================================
# 概念评分
# ============================================================

concept_scores = []

for concept, scores in concept_score_map.items():

    if len(scores) < 3:
        continue

    concept_scores.append({

        '概念': concept,
        '评分': round(
            np.mean(scores),
            2
        )

    })

concept_score_df = pd.DataFrame(concept_scores)

if len(concept_score_df) == 0:

    print("概念热点为空")

    TOP_CONCEPTS = []

else:

    concept_score_df = concept_score_df.sort_values(
        by='评分',
        ascending=False
    )

    TOP_CONCEPTS = (
        concept_score_df
        .head(TOP_CONCEPT_COUNT)['概念']
        .tolist()
    )

    print(concept_score_df.head(10))



# ============================================================
# 计算行业热点
# ============================================================

print("\n开始计算行业热点...\n")

industry_score_map = {}

for code, industry in stock_industry_map.items():

    try:

        if code not in daily_cache:
            continue

        df = daily_cache[code]

        latest = df.iloc[-1]

        rise_20 = (
            latest['close']
            /
            df.iloc[-21]['close']
            - 1
        )

        rise_5 = (
            latest['close']
            /
            df.iloc[-6]['close']
            - 1
        )

        df['ma20'] = (
            df['close']
            .rolling(20)
            .mean()
        )

        df['ma60'] = (
            df['close']
            .rolling(60)
            .mean()
        )

        trend_score = 0

        if (
            df.iloc[-1]['ma20']
            >
            df.iloc[-1]['ma60']
        ):
            trend_score = 10

        stock_score = (
            rise_20 * 100 * 0.4
            +
            rise_5 * 100 * 0.3
            +
            trend_score
        )

        if industry not in industry_score_map:

            industry_score_map[industry] = []

        industry_score_map[industry].append(
            stock_score
        )

    except:
        continue

industry_scores = []

for industry, scores in industry_score_map.items():

    if len(scores) < 3:
        continue

    industry_scores.append({

        '行业': industry,
        '评分': round(
            np.mean(scores),
            2
        )

    })

industry_score_df = pd.DataFrame(industry_scores)

if len(industry_score_df) == 0:

    print("行业热点为空")

    TOP_INDUSTRIES = []

else:

    industry_score_df = industry_score_df.sort_values(
        by='评分',
        ascending=False
    )

    TOP_INDUSTRIES = (
        industry_score_df
        .head(TOP_INDUSTRY_COUNT)['行业']
        .tolist()
    )

    print(industry_score_df.head(10))

# ============================================================
# 热点股票池
# ============================================================

hot_stock_set = set()

# 热点概念
for code, concepts in stock_concept_map.items():

    for c in concepts:

        if c in TOP_CONCEPTS:

            hot_stock_set.add(code)

# 热点行业
for code, industry in stock_industry_map.items():

    if industry in TOP_INDUSTRIES:

        hot_stock_set.add(code)

print("\n热点股票数量:", len(hot_stock_set))

# ============================================================
# 主线趋势机构票筛选
# ============================================================

print("\n开始扫描机构趋势票...\n")

results = []

for code in hot_stock_set:

    try:

        if code not in daily_cache:
            continue

        if code not in basic_cache:
            continue

        df = daily_cache[code]

        if len(df) < 120:
            continue

        latest = df.iloc[-1]

        # ====================================================
        # 均线
        # ====================================================

        df['ma5'] = (
            df['close']
            .rolling(5)
            .mean()
        )

        df['ma10'] = (
            df['close']
            .rolling(10)
            .mean()
        )

        df['ma20'] = (
            df['close']
            .rolling(20)
            .mean()
        )

        df['ma60'] = (
            df['close']
            .rolling(60)
            .mean()
        )

        df['vol_ma20'] = (
            df['vol']
            .rolling(20)
            .mean()
        )

        # ====================================================
        # 箱体
        # ====================================================

        box_df = df.iloc[-BOX_DAYS:]

        box_high = (
            box_df['high']
            .max()
        )

        box_low = (
            box_df['low']
            .min()
        )

        amplitude = (
            box_high - box_low
        ) / box_low

        if amplitude > MAX_BOX_AMPLITUDE:
            continue

        # ====================================================
        # 真突破
        # ====================================================

        breakout_line = (
            box_high * BREAKOUT_PCT
        )

        breakout_days = (
            df.iloc[-3:]['close']
            >
            breakout_line
        ).sum()

        if breakout_days < MIN_BREAKOUT_DAYS:
            continue

        # ====================================================
        # 长上影
        # ====================================================

        upper_shadow = (
            latest['high']
            -
            latest['close']
        ) / latest['close']

        if upper_shadow > 0.04:
            continue

        # ====================================================
        # 放量
        # ====================================================

        vol_ratio = (
            latest['vol']
            /
            latest['vol_ma20']
        )

        if (
            vol_ratio < VOL_RATIO_LOW
            or
            vol_ratio > VOL_RATIO_HIGH
        ):
            continue

        # ====================================================
        # 趋势
        # ====================================================

        if latest['ma20'] < latest['ma60']:
            continue

        if latest['close'] < latest['ma20']:
            continue

        # ====================================================
        # 多头排列
        # ====================================================

        if not (
            latest['ma5']
            >
            latest['ma10']
            >
            latest['ma20']
        ):
            continue

        # ====================================================
        # 启动位置
        # ====================================================

        if (
            latest['close']
            /
            box_low
            >
            MAX_RISE_FROM_LOW
        ):
            continue

        # ====================================================
        # 近5日不加速
        # ====================================================

        rise_5 = (
            latest['close']
            /
            df.iloc[-6]['close']
            - 1
        )

        if rise_5 > MAX_5DAY_RISE:
            continue

        # ====================================================
        # 涨停次数
        # ====================================================

        limitup_count = (
            df.iloc[-20:]['pct_chg']
            > 9.5
        ).sum()

        if limitup_count > MAX_LIMITUP_COUNT:
            continue

        # ====================================================
        # 市值
        # ====================================================

        mv = (
            basic_cache[code]['total_mv']
        )

        if mv < MIN_MARKET_VALUE:
            continue

        pe = (
            basic_cache[code]['pe_ttm']
        )

        # ====================================================
        # 趋势评分
        # ====================================================

        breakout_strength = (
            latest['close']
            /
            box_high
            - 1
        ) * 100

        trend_score = (
            breakout_strength * 2
            +
            vol_ratio * 10
            +
            rise_5 * 100
        )

        # ====================================================
        # 名称
        # ====================================================

        info = stock_basic[
            stock_basic['ts_code'] == code
        ].iloc[0]

        # ====================================================
        # 保存
        # ====================================================

        results.append({

            '股票代码': code,

            '股票名称': info['name'],

            '行业': info['industry'],

            '热点概念': ",".join(
                stock_concept_map.get(code, [])[:3]
            ),

            '收盘价': round(
                latest['close'],
                2
            ),

            '总市值(亿)': round(
                mv / 10000,
                1
            ),

            'PE_TTM': round(pe, 1)
            if pd.notna(pe)
            else None,

            '量比': round(
                vol_ratio,
                2
            ),

            '突破强度%': round(
                breakout_strength,
                2
            ),

            '5日涨幅%': round(
                rise_5 * 100,
                2
            ),

            '趋势评分': round(
                trend_score,
                2
            )

        })

        print(
            f"发现机会: "
            f"{code} "
            f"{info['name']}"
        )

    except:
        continue

# ============================================================
# 输出结果
# ============================================================

result_df = pd.DataFrame(results)

if len(result_df) > 0:

    result_df = result_df.sort_values(
        by='趋势评分',
        ascending=False
    )

    print("\n============================")
    print("主线趋势机构票")
    print("============================\n")

    print(result_df.head(30))

    file_name = (
        f"主线趋势机构票_{end_date}.csv"
    )

    result_df.to_csv(
        file_name,
        index=False,
        encoding='utf-8-sig'
    )

    print(f"\n结果保存成功: {file_name}")

else:

    print("\n未发现符合条件股票")