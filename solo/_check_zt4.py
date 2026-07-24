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
zt_list = []
all_list = []

for code, name in uhv_stocks:
    csv_path = os.path.join(cache_dir, f"{code}.csv")
    if not os.path.exists(csv_path):
        all_list.append((code, name, "无缓存"))
        continue
    try:
        df = pd.read_csv(csv_path)
        if df.empty or 'trade_date' not in df.columns or 'pct_chg' not in df.columns:
            all_list.append((code, name, "数据不完整"))
            continue
        df = df.sort_values('trade_date')
        last = df.iloc[-1]
        last_date = str(last['trade_date'])
        pct = float(last['pct_chg'])
        close = float(last['close']) if 'close' in df.columns else 0
        vol = float(last['vol']) if 'vol' in df.columns else 0
        entry = (code, name, last_date, round(pct, 2), close)
        all_list.append(entry)
        if pct >= 9.5:
            zt_list.append(entry)
    except Exception as e:
        all_list.append((code, name, f"读取失败: {e}"))

print(f"特高压34只股票中涨停: {len(zt_list)} 只\n")
for code, name, dt, pct, close in zt_list:
    print(f"  ✅ {code:>10s} {name:5s} {dt} 涨停+{pct}% 收盘{close}")

print(f"\n所有股票今日涨跌幅:")
for entry in all_list:
    if len(entry) == 3:
        print(f"  {entry[1]:5s} ({entry[0]}): {entry[2]}")
    else:
        code, name, dt, pct, close = entry
        flag = " ✅涨停" if pct >= 9.5 else ""
        print(f"  {name:5s} ({code:>10s}): +{pct:.2f}% 收盘{close:.2f}{flag}")
