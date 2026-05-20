import requests
import pandas as pd
import sqlite3
import time
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import os

# =========================
# 工具层（防炸）
# =========================
def normalize_columns(df):
    df.columns = df.columns.str.strip().str.lower()
    return df

def normalize_code(x):
    return str(x)[-6:]

def safe_merge(df, industry_map):
    df = normalize_columns(df)
    industry_map = normalize_columns(industry_map)
    print("df columns:", df.columns.tolist())
    print("industry_map columns:", industry_map.columns.tolist())
    df["code"] = df["code"].astype(str).apply(normalize_code)
    industry_map["code"] = industry_map["code"].astype(str).apply(normalize_code)

    return df.merge(industry_map, on="code", how="left")


# =========================
# 数据获取（东方财富）
# =========================
class EastMoneyClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://quote.eastmoney.com"
        })

        retry = Retry(total=5, backoff_factor=0.5)
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def get_stock_spot(self):
        url = "https://push2.eastmoney.com/api/qt/clist/get"

        params = {
            "pn": "1",
            "pz": "5000",
            "po": "1",
            "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": "m:0+t:6,m:0+t:13,m:1+t:2,m:1+t:23",
            "fields": "f12,f14,f2,f3,f5,f6,f62,f184"
        }

        r = self.session.get(url, params=params, timeout=5)
        data = r.json()["data"]["diff"]

        df = pd.DataFrame(data)

        df.rename(columns={
            "f12": "code",
            "f14": "name",
            "f2": "price",
            "f3": "pct",
            "f5": "volume",
            "f6": "amount",
            "f62": "main_inflow",
            "f184": "turnover"
        }, inplace=True)

        df["date"] = datetime.now().strftime("%Y-%m-%d")

        return df


# =========================
# 行业映射（示例）
# ⚠️ 实战建议你替换成真实接口
# =========================
def get_stock_industry_mapping1(session):
    # ⚠️ 这里做一个简化示例（你可以替换成真实行业接口）
    # 实盘建议：用akshare stock_industry 或 eastmoney行业成分接口

    df = pd.read_csv("industry_map.csv")  # 你提前准备
    return df


def get_stock_industry_mapping(session):
    #url = "https://push2.eastmoney.com/api/qt/clist/get"
    url = "https://17.push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1",
        "pz": "500",
        "fs": "m:90+t:2",  # 行业板块
        "fields": "f12,f14"
    }

    r = session.get(url, params=params)
    data = r.json()["data"]["diff"]

    df = pd.DataFrame(data)
    df.rename(columns={"f12": "industry_code", "f14": "industry_name"}, inplace=True)
    print("industry_map columns:", df.columns.tolist())
    return df

# =========================
# 缓存层
# =========================
class DataCache:
    if os.path.exists("market.db"):
        os.remove("market.db")

    def __init__(self, db_name="market.db"):
        self.conn = sqlite3.connect(db_name)

    print("数据文件存在:", os.path.exists("market.db"))

    def save_stock(self, df):
        df.to_sql("stock_data", self.conn, if_exists="append", index=False)

    def save_industry_map(self, df):
        df.to_sql("industry_map", self.conn, if_exists="replace", index=False)

    def load_industry_map(self):
        return pd.read_sql("SELECT * FROM industry_map", self.conn)


# =========================
# 初始化（只更新一次行业映射）
# =========================
def init_system():
    client = EastMoneyClient()
    cache = DataCache()

    today = datetime.now().strftime("%Y-%m-%d")

    try:
        df = pd.read_sql("SELECT * FROM industry_map LIMIT 1", cache.conn)
        last_date = df.get("date", [""])[0]

        if today not in str(last_date):
            raise Exception("需要更新")
    except:
        print("更新行业映射...")
        industry_map = get_stock_industry_mapping(client.session)
        industry_map["date"] = today
        cache.save_industry_map(industry_map)

    return client, cache


# =========================
# 板块评分模型
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

        score = avg_pct*0.4 + limit_up*0.3 + strong_ratio*0.2 + amount/1e10*0.1

        result.append({
            "industry": name,
            "score": score,
            "avg_pct": avg_pct,
            "limit_up": limit_up
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
        fund = group["amount"].sum()/1e10

        leaders = group.sort_values("pct", ascending=False).head(3)
        structure = leaders["pct"].mean() - leaders["turnover"].mean()*0.2

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
    cond = (df["pct"] > 7) & (df["amount"] > 2e8)
    return df[cond].sort_values("pct", ascending=False).head(2)

def find_core(df):
    cond = (df["pct"] > 2) & (df["amount"] > 5e8)
    return df[cond].sort_values("amount", ascending=False).head(5)


# =========================
# 主程序
# =========================
def run():
    client, cache = init_system()

    print("获取行情...")
    df = client.get_stock_spot()
    cache.save_stock(df)

    industry_map = cache.load_industry_map()

    df = safe_merge(df, industry_map)

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
        print(leaders[["code","name","pct"]])

        print("中军:")
        print(core[["code","name","pct"]])


if __name__ == "__main__":
    run()