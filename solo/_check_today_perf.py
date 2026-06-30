# -*- coding: utf-8 -*-
"""查询6/26和6/29信号股今日（6/30）表现"""
import os
import pandas as pd
from datetime import datetime, timedelta

# 读取信号数据
csv_path = r'D:\mystock\solo\trend_feature_output\entry_precision_20260629_224320_qualified.csv'
df = pd.read_csv(csv_path, dtype={'ts_code': str})

# 筛选6/26和6/29
target_dates = ['20260626', '20260629']
today = '20260630'

# 获取名称映射
name_map = {}
bull_csv = r'D:\mystock\solo\multi_factor_picker\output\bull_stocks_20260629_235153.csv'
if os.path.exists(bull_csv):
    bdf = pd.read_csv(bull_csv, dtype={'code': str})
    for _, row in bdf.iterrows():
        code = str(row['code']).strip().zfill(6)
        name = str(row['name']).strip()
        if name and name != 'nan':
            name_map[code] = name

# Tushare获取今日行情
try:
    import tushare as ts
    from dotenv import load_dotenv
    import os as _os
    load_dotenv(r'D:\mystock\config\.env')
    ts.set_token(_os.getenv('TUSHARE_TOKEN'))
    pro = ts.pro_api()

    # 获取今日日线（6/30）
    print(f'正在获取 {today} 日线数据...')
    today_df = pro.daily(trade_date=today)
    print(f'今日数据行数: {len(today_df)}')
except Exception as e:
    print(f'Tushare获取失败: {e}')
    today_df = pd.DataFrame()

# 合并分析
for dt in target_dates:
    sub = df[df['signal_date'] == int(dt)].sort_values('entry_score', ascending=False)
    print(f'\n{"="*60}')
    print(f'【{dt[:4]}-{dt[4:6]}-{dt[6:8]} 信号股 {len(sub)} 只 → 今日（6/30）表现')
    print(f'{"="*60}')

    if today_df.empty:
        print('（无法获取今日行情，跳过）')
        continue

    results = []
    for _, row in sub.iterrows():
        ts_code = str(row['ts_code']).strip()
        code6 = ts_code.split('.')[0].zfill(6)
        name = name_map.get(code6, '')
        entry_score = int(row['entry_score'])

        # 从今日行情找这只股票
        trow = today_df[today_df['ts_code'] == ts_code]
        if trow.empty:
            print(f'  {code6} {name}  评分{entry_score:3d}  ⚠ 今日行情未找到')
            continue

        t = trow.iloc[0]
        pct_chg = t['pct_chg']  # 今日涨跌幅
        close = t['close']
        open_price = t['open']
        high = t['high']
        low = t['low']

        # 信号日到今天的累计收益（简单用今日收盘价 vs 信号日收盘价）
        # 需要信号日的收盘价
        sig_date_str = str(row['signal_date'])
        sig_df = pro.daily(ts_code=ts_code, start_date=sig_date_str, end_date=sig_date_str)
        if not sig_df.empty:
            sig_close = sig_df.iloc[0]['close']
            cum_ret = (close - sig_close) / sig_close * 100
        else:
            sig_close = None
            cum_ret = None

        icon = '🔴' if pct_chg < 0 else '🟢'
        results.append({
            'code': code6,
            'name': name,
            'score': entry_score,
            'today_pct': pct_chg,
            'close': close,
            'cum_ret': cum_ret,
            'icon': icon,
        })

    # 输出结果
    if results:
        # 按今日涨幅排序
        results.sort(key=lambda x: x['today_pct'], reverse=True)
        up = sum(1 for r in results if r['today_pct'] > 0)
        avg_today = sum(r['today_pct'] for r in results) / len(results)
        print(f'  今日上涨: {up}/{len(results)}  平均涨幅: {avg_today:.2f}%')
        print()
        for r in results:
            cum_str = f'{r["cum_ret"]:.2f}%' if r['cum_ret'] is not None else 'N/A'
            print(f'  {r["icon"]} {r["code"]} {r["name"]:8s}  评分{r["score"]:3d}  今日{r["today_pct"]:+.2f}%  收盘{r["close"]:.2f}  累计{cum_str}')
    else:
        print('  （无今日行情数据）')

print('\n完成。')
