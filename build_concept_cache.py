import os
import time
import tushare as ts
import pandas as pd
from dotenv import load_dotenv

# =========================
# 环境
# =========================
load_dotenv()

TOKEN = os.getenv("TUSHARE_TOKEN")

ts.set_token(TOKEN)

pro = ts.pro_api()

# =========================
# 缓存目录
# =========================
CACHE_DIR = "concept_cache"

if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

# =========================
# 获取概念列表
# =========================
concept_df = pro.concept()

print(concept_df.head())

# =========================
# 下载全部概念成分股
# =========================
for idx, row in concept_df.iterrows():

    concept_id = row['code']

    concept_name = row['name']

    cache_file = os.path.join(
        CACHE_DIR,
        f"{concept_id}.csv"
    )

    # =========================
    # 已存在
    # =========================
    if os.path.exists(cache_file):

        print(f"跳过: {concept_name}")

        continue

    try:

        print(
            f"[{idx+1}/{len(concept_df)}] "
            f"{concept_name}"
        )

        df = pro.concept_detail(
            id=concept_id
        )

        if not df.empty:

            df.to_csv(
                cache_file,
                index=False,
                encoding='utf-8-sig'
            )

        # =========================
        # 关键限速
        # =========================
        time.sleep(0.7)

    except Exception as e:

        print(concept_name, e)

        time.sleep(5)

print("完成")