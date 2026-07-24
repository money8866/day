import os, pandas as pd

uhv_stocks = [
    ("000400.SZ", "许继电气"), ("600406.SH", "国电南瑞"), ("600312.SH", "平高电气"),
    ("300265.SZ", "通光线缆"), ("300617.SZ", "安靠智电"), ("601179.SH", "中国西电"),
    ("600089.SH", "特变电工"), ("002270.SZ", "华明装备"), ("600550.SH", "保变电气"),
    ("600379.SH", "宝光股份"), ("603618.SH", "杭电股份"), ("605196.SH", "华通线缆"),
    ("001208.SZ", "华菱线缆"), ("603606.SH", "东方电缆"), ("002121.SZ", "科陆电子"),
    ("000551.SZ", "创元科技"), ("300933.SZ", "中辰股份"), ("601700.SH", "风范股份"),
    ("002300.SZ", "太阳电缆"), ("605222.SH", "起帆电缆"), ("600973.SH", "宝胜股份"),
    ("603666.SH", "亿嘉和"), ("300557.SZ", "理工光科"), ("600869.SH", "远东股份"),
    ("002606.SZ", "大连电瓷"), ("301386.SZ", "未来电器"), ("002692.SZ", "远程股份"),
    ("603016.SH", "新宏泰"), ("301012.SZ", "扬电科技"), ("000720.SZ", "新能泰山"),
    ("002471.SZ", "中超控股"), ("920018.BJ", "宏远股份"), ("000586.SZ", "汇源通信"),
    ("920639.BJ", "晨光电缆")
]

cache_dir = r'd:\mystock\cache_daily'

# 只查今天(20260724)的数据
today = '20260724'
nines = 0
tens = 0
fours = 0
for code, name in uhv_stocks:
    csv_path = os.path.join(cache_dir, f"{code}.csv")
    if not os.path.exists(csv_path):
        continue
    df = pd.read_csv(csv_path)
    row = df[df['trade_date'] == today]
    if row.empty:
        continue
    pct = float(row['pct_chg'].iloc[0])
    close = float(row['close'].iloc[0])
    if pct >= 9.5:
        nines += 1
        print(f"  ✅ {code} {name}: 涨停+{pct:.2f}% close={close}")
    if pct >= 10:
        tens += 1
    if pct >= 4:
        fours += 1
        
print(f"\n>=9.5%(涨停): {nines} 只")
print(f">=10%: {tens} 只")
print(f">=4%(强势): {fours} 只")
