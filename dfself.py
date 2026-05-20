import sqlite3
import pandas as pd

db_path = r"C:\eastmoney\swc8\config\User\9971113309768870\self_stock.db"

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 查看所有表
cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
tables = cursor.fetchall()

print("表列表:", tables)

cursor.execute("PRAGMA table_info(selfstock);")
#print(cursor.fetchall())

cursor.execute("SELECT group_key,stock_code_arr FROM selfstock where group_key='0_自选股';")
rows = cursor.fetchall()


for row in rows:
    print(row[1])
    raw=row[1]

print(rows[0][1])

items = rows[0][1].split(",")
print(items)
result = []
for x in items:
    if x.strip() == "":
        break
    market, code = x.split(".")
    


    if market == "1":
        sec = "SH" + code
    else:
        sec = "SZ" + code
        
    result.append(sec)

print(result)

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

print("\n".join(lines))
path = r"C:\new_tdx\T0002\blocknew\zxg.blk"

with open(path, "w") as f:
    for line in lines:
        f.write(line + "\n")