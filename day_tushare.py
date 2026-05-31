import tushare as ts
import pandas as pd
import sqlite3
from datetime import datetime

# =========================
# 初始化
# =========================
TOKEN = "bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d"
pro = ts.pro_api(TOKEN)


# =========================
# 缓存层
# =========================
class DataCache:
    def __init__(self, db_name="market.db"):
        self.conn = sqlite3.connect(db_name)

    def save(self, df, table):
        df.to_sql(table, self.conn, if_exists="append", index=False)

    def replace(self, df, table):
        df.to_sql(table, self.conn, if_exists="replace", index=False)

    def read(self, table):
        return pd.read_sql(f"SELECT * FROM {table}", self.conn)


# =========================
# 工具层（统一字段🔥）
# =========================
def normalize_code(x):
    return str(x).split(".")[0]

def unify_stock(df):
    df = df.copy()
    df.columns = df.columns.str.lower()

    if "ts_code" in df.columns:
        df["code"] = df["ts_code"].apply(normalize_code)

    return df

# =========================
# 获取行情（用日线替代实时）
# =========================
def get_stock_data():
    today = datetime.now().strftime("%Y%m%d")

    df = pro.daily(trade_date=today)

    df = unify_stock(df)

    df.rename(columns={
        "close": "price",
        "pct_chg": "pct",
        "vol": "volume",
        "amount": "amount"
    }, inplace=True)

    df["turnover"] = df["volume"] / 100000  # 简化
    df["date"] = today

    return df


# =========================
# 行业映射（自动🔥）
# =========================
def get_industry_map():
    df = pro.stock_basic(exchange='', list_status='L',
                         fields='ts_code,industry')

    df = unify_stock(df)

    df.rename(columns={"industry": "industry_name"}, inplace=True)

    return df[["code", "industry_name"]]


# =========================
# 板块评分
# =========================
def calc_industry_score(df):
    result = []

    for name, group in df.groupby("industry_name"):
        if len(group) < 5:
            continue

        avg_pct = group["pct"].mean()
        limit_up = (group["pct"] > 9).sum()
        amount = group["amount"].sum()
        strong_ratio = (group["pct"] > 5).mean()

        score = avg_pct*0.4 + limit_up*0.3 + strong_ratio*0.2 + amount/1e8*0.1

        result.append({
            "industry": name,
            "score": score,
            "avg_pct": avg_pct
        })

    return pd.DataFrame(result).sort_values("score", ascending=False)


# =========================
# 主线持续性模型
# =========================
def calc_continuation(df):
    result = []

    for name, group in df.groupby("industry_name"):
        if len(group) < 5:
            continue

        strength = group["pct"].mean()
        diffusion = (group["pct"] > 5).mean()
        fund = group["amount"].sum()/1e8

        leaders = group.sort_values("pct", ascending=False).head(3)
        structure = leaders["pct"].mean()

        score = strength*0.3 + diffusion*10*0.25 + fund*0.25 + structure*0.2

        result.append({
            "industry": name,
            "score": score
        })

    return pd.DataFrame(result).sort_values("score", ascending=False)


# =========================
# 龙头 & 中军
# =========================
def find_leaders(df):
    cond = (df["pct"] > 7)
    return df[cond].sort_values("pct", ascending=False).head(2)

def find_core(df):
    cond = (df["pct"] > 2) & (df["amount"] > df["amount"].median())
    return df[cond].sort_values("amount", ascending=False).head(5)


# =========================
# 主程序
# =========================
def run():
    cache = DataCache()

    print("获取行业映射...")
    industry_map = get_industry_map()
    cache.replace(industry_map, "industry_map")

    print("获取行情...")
    df = get_stock_data()
    cache.save(df, "stock_data")

    # merge（不会再报错🔥）
    df = df.merge(industry_map, on="code", how="left")

    # ===== 板块评分 =====
    industry_score = calc_industry_score(df)
    top5 = industry_score.head(5)

    print("\n====== 最强板块 ======")
    print(top5)

    # ===== 持续性 =====
    cont = calc_continuation(df)
    print("\n====== 主线持续性 ======")
    print(cont.head(5))

    # ===== 板块结构 =====
    for _, row in top5.iterrows():
        name = row["industry"]

        sub = df[df["industry_name"] == name]

        leaders = find_leaders(sub)
        core = find_core(sub)

        print(f"\n🔥 {name}")
        print("龙头:")
        print(leaders[["code","pct"]])

        print("中军:")
        print(core[["code","pct"]])


if __name__ == "__main__":
    run()