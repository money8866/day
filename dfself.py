import sqlite3
import pandas as pd
import os
from dotenv import load_dotenv
import tushare as ts

load_dotenv()
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

db_path = r"C:\eastmoney\swc8\config\User\9971113309768870\self_stock.db"

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("SELECT group_key,stock_code_arr FROM selfstock where group_key='0_自选股';")
rows = cursor.fetchall()

for row in rows:
    raw = row[1]

items = raw.split(",")
stock_list = []
for x in items:
    if x.strip() == "":
        break
    market, code = x.split(".")

    if market == "1":
        ts_code = code + ".SH"
    else:
        ts_code = code + ".SZ"

    stock_list.append(ts_code)

print(f"获取到 {len(stock_list)} 只自选股")

try:
    stock_df = pro.stock_basic(ts_code=",".join(stock_list[:100]), fields="ts_code,name,list_status")

    if stock_df is None or stock_df.empty:
        print("无法通过批量获取，尝试逐个获取...")
        name_map = {}
        for ts_code in stock_list:
            try:
                single_df = pro.stock_basic(ts_code=ts_code, fields="ts_code,name,list_status")
                if single_df is not None and not single_df.empty:
                    name_map[ts_code] = single_df.iloc[0]["name"]
            except:
                continue

        stock_df = pd.DataFrame([
            {"ts_code": k, "name": v, "list_status": "L"} for k, v in name_map.items()
        ])
except Exception as e:
    print(f"获取股票信息失败: {e}")
    stock_df = pd.DataFrame()

if not stock_df.empty:
    stock_df = stock_df[stock_df["list_status"] == "L"]
    print(f"有效上市股票: {len(stock_df)} 只")

excluded_starts = ("500", "501", "502", "503", "504", "505", "506", "507", "508", "509", "510", "511", "512", "513", "514", "515", "516", "517", "518", "519", "150", "151", "152", "153", "154", "155", "156", "157", "158", "159", "160", "161", "162", "163", "164", "165", "166", "167", "168", "169")

filtered_stocks = []
for ts_code in stock_df["ts_code"]:
    code_only = ts_code.split(".")[0]
    if not code_only.startswith(excluded_starts):
        filtered_stocks.append(ts_code)

filtered_df = stock_df[stock_df["ts_code"].isin(filtered_stocks)].copy()
print(f"过滤后股票: {len(filtered_df)} 只")

dayreal_dir = r"C:\Users\kongx\mystock\dayreal"
os.makedirs(dayreal_dir, exist_ok=True)
stock_csv_path = os.path.join(dayreal_dir, "stocks.csv")

output_df = filtered_df[["ts_code", "name"]].copy()
output_df["ts_code"] = output_df["ts_code"].str.split(".").str[0]
output_df.columns = ["代码", "名称"]

output_df.to_csv(stock_csv_path, index=False, encoding="utf-8-sig")
print(f"已保存到: {stock_csv_path}")

conn.close()

print("\n" + "="*50)
print("股票列表预览:")
print(output_df.head(20))

def to_tdx_blk(raw):
    res = []
    for x in raw.split(","):
        if x.strip() == "":
            break
        market, code = x.split(".")

        if market == "1":
            res.append(f"1#{code}")
        else:
            res.append(f"0#{code}")

    return res

lines = to_tdx_blk(rows[0][1])

print("\n通达信板块格式预览:")
print("\n".join(lines[:10]) + "...")

tdx_path = r"C:\new_tdx\T0002\blocknew\zxg.blk"
with open(tdx_path, "w") as f:
    for line in lines:
        f.write(line + "\n")

print(f"\n已保存通达信板块到: {tdx_path}")
