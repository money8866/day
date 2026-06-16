# -*- coding: utf-8 -*-
"""获取一线标的的PE等估值数据"""
import sys
sys.path.insert(0, '.')
import pandas as pd
import tushare as ts
from datetime import datetime
from main import load_config, get_token

config = load_config()
token = get_token(config)
pro = ts.pro_api(token=token)

tier1_stocks = [
    ('603256.SH', '宏和科技', '玻璃'),
    ('688127.SH', '蓝特光学', '元器件'),
    ('600176.SH', '中国巨石', '玻璃'),
    ('688008.SH', '澜起科技', '半导体'),
    ('688300.SH', '联瑞新材', '矿物制品'),
    ('603893.SH', '瑞芯微', '半导体'),
    ('300476.SZ', '胜宏科技', '元器件'),
    ('301377.SZ', '鼎泰高科', '机械基件'),
    ('001389.SZ', '广合科技', '元器件'),
    ('301338.SZ', '凯格精机', '专用机械'),
    ('300395.SZ', '菲利华', '玻璃'),
]

today = datetime.now().strftime('%Y%m%d')

print("获取今日估值数据...")
for ts_code, name, industry in tier1_stocks:
    try:
        # 使用 daily_basic 获取当日估值
        df = pro.daily_basic(ts_code=ts_code, trade_date=today)
        if df is not None and len(df) > 0:
            row = df.iloc[0]
            pe = row.get('pe_ttm')
            pb = row.get('pb')
            mkt_cap = row.get('total_mv')  # 总市值（万元）
            print(f"{name}: PE(TTM)={pe:.1f if pd.notna(pe) else 'N/A'}, PB={pb:.2f if pd.notna(pb) else 'N/A'}, 总市值={mkt_cap/10000:.1f}亿")
        else:
            print(f"{name}: 无今日数据，尝试最近交易日")
            # 尝试获取最近交易日数据
            df = pro.daily_basic(ts_code=ts_code, trade_date='20260613')
            if df is not None and len(df) > 0:
                row = df.iloc[0]
                pe = row.get('pe_ttm')
                pb = row.get('pb')
                mkt_cap = row.get('total_mv')
                print(f"{name}: PE={pe:.1f if pd.notna(pe) else 'N/A'}, PB={pb:.2f if pd.notna(pb) else 'N/A'}, 总市值={mkt_cap/10000:.1f}亿")
            else:
                print(f"{name}: 无数据")
    except Exception as e:
        print(f"{name}: 错误 {e}")
