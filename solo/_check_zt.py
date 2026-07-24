# 查询特高压成份股今日(20260724)涨停的股票
import sqlite3, os
import pandas as pd

# 特高压34只成份股
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

# 尝试从缓存中读取今日K线数据
base = r'd:\mystock\solo\cache_backbone_tushare'
daily_dir = os.path.join(base, 'cache_daily')

# 检查 theme_trend_sentiment_score.py 的缓存模式
# 它用的是 get_daily_kline 函数，查看是用的单个文件还是统一文件
# 尝试找今天(20260724)的日常K线缓存
found = []
for code, name in uhv_stocks:
    safe_code = code.replace('.', '_')
    # 尝试多个可能的缓存路径
    paths_to_try = [
        os.path.join(base, f'daily_{safe_code}.pkl'),
        os.path.join(base, f'kline_{safe_code}.pkl'),
        os.path.join(daily_dir, f'daily_{safe_code}_{code}.pkl'),
        os.path.join(daily_dir, f'{safe_code}.pkl'),
    ]
    for p in paths_to_try:
        if os.path.exists(p):
            try:
                df = pd.read_pickle(p)
                if 'close' in df.columns and len(df) > 0:
                    last = df.iloc[-1]
                    today_idx = len(df) - 1
                    if 'pct_chg' in df.columns:
                        pct = df['pct_chg'].iloc[-1]
                    else:
                        pct = (df['close'].iloc[-1] / df['close'].iloc[-2] - 1) * 100 if len(df) > 1 else 0
                    if pct >= 9.5:
                        found.append((code, name, round(pct, 2), p))
                    break
            except:
                pass

print(f"从缓存找到涨停股票 {len(found)} 只:")
for code, name, pct, path in found:
    print(f"  {name} ({code}): +{pct}%")

if not found:
    print("缓存中未找到匹配数据，尝试 SQLite 缓存...")
    conn = sqlite3.connect(os.path.join(base, 'cache.db'))
    cur = conn.execute("SELECT key FROM cache_data WHERE key LIKE '%daily_kline%' AND key LIKE '%20260724%' LIMIT 10")
    keys = cur.fetchall()
    print(f"找到 {len(keys)} 个相关缓存条目")
    for k in keys:
        print(f"  {k[0]}")
    
    # 如果没有今天的数据，尝试检查 theme_trend_sentiment_score.py 的 kline 缓存模式
    # 看看是否有 daily_quotes_20260724 这样的文件
    files = [f for f in os.listdir(base) if '20260724' in f and ('daily' in f or 'kline' in f)]
    print(f"\n20260724相关缓存文件: {files}")
    conn.close()
