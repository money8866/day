# -*- coding: utf-8 -*-
"""分析一线标的今年涨幅与估值空间"""
import sys
sys.path.insert(0, '.')
import pandas as pd
import tushare as ts
from datetime import datetime
from main import load_config, get_token

config = load_config()
token = get_token(config)
pro = ts.pro_api(token=token)

# 一线标的
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

results = []
year_start = '20260101'
today = datetime.now().strftime('%Y%m%d')

for ts_code, name, industry in tier1_stocks:
    try:
        # 获取今年初和今日的日线数据
        df = pro.daily(ts_code=ts_code, start_date=year_start, end_date=today)
        if df is not None and len(df) > 0:
            df = df.sort_values('trade_date')
            
            # 年初价格（第一笔）
            year_open = df.iloc[0]['close']
            year_open_date = df.iloc[0]['trade_date']
            
            # 今日价格（最后一笔）
            year_close = df.iloc[-1]['close']
            year_close_date = df.iloc[-1]['trade_date']
            
            # YTD涨幅
            ytd_return = (year_close - year_open) / year_open * 100
            
            # 今年最高/最低
            year_high = df['high'].max()
            year_low = df['low'].min()
            high_date = df[df['high'] == year_high].iloc[0]['trade_date']
            low_date = df[df['low'] == year_low].iloc[0]['trade_date']
            
            # 当前股价位置（距离高点/低点）
            pct_from_high = (year_high - year_close) / year_high * 100
            pct_from_low = (year_close - year_low) / year_low * 100
            
            # 52周高低
            one_year_ago = (datetime.now() - pd.Timedelta(days=365)).strftime('%Y%m%d')
            df_52w = pro.daily(ts_code=ts_code, start_date=one_year_ago, end_date=today)
            if df_52w is not None and len(df_52w) > 0:
                high_52w = df_52w['high'].max()
                low_52w = df_52w['low'].min()
            else:
                high_52w, low_52w = year_high, year_low
            
            # 市盈率PE（获取最新一期财报计算）
            try:
                income = pro.fina_indicator(ts_code=ts_code, start_date='20250101', fields='ts_code,ann_date,pe')
                if income is not None and len(income) > 0:
                    pe = income.iloc[0]['pe']
                    if pd.isna(pe) or pe <= 0:
                        pe = None
                else:
                    pe = None
            except:
                pe = None
            
            results.append({
                '代码': ts_code,
                '名称': name,
                '行业': industry,
                '年初价': round(year_open, 2),
                '当前价': round(year_close, 2),
                'YTD涨幅%': round(ytd_return, 1),
                '今年高': round(year_high, 2),
                '今年低': round(year_low, 2),
                '距高点%': round(pct_from_high, 1),
                '距低点%': round(pct_from_low, 1),
                '52W高': round(high_52w, 2),
                '52W低': round(low_52w, 2),
                'PE': round(pe, 1) if pe else '-',
            })
            print(f"{name}: YTD={ytd_return:.1f}%, 当前={year_close}, PE={pe}")
        else:
            print(f"{name}: 无数据")
    except Exception as e:
        print(f"{name}: 错误 {e}")

print()
print("=" * 80)
df_result = pd.DataFrame(results)
print(df_result.to_string(index=False))
