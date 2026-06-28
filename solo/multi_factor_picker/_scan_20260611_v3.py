"""全市场扫描20260611信号（手动股票池）"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

import time
import pandas as pd
from data_fetcher import DataFetcher
from trend_picker_v2_draft import detect_wave2_pattern, score_technical_v2

token = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
fetcher = DataFetcher(token, {'cache': {'dir': 'cache'}})

# 手动指定股票池：热门龙头股
pool = [
    # 半导体
    '600498.SH', '002409.SZ', '603501.SH', '300661.SZ', '688981.SH',
    '603160.SH', '600584.SH', '002371.SZ', '300671.SZ', '688111.SH',
    # AI算力
    '603019.SH', '000977.SZ', '601138.SH', '688256.SH', '300750.SZ',
    # 存储
    '603986.SH', '300223.SZ', '688041.SH', '300662.SZ', '688008.SH',
    # 商业航天
    '600118.SH', '002465.SZ', '300045.SZ', '688062.SH', '300101.SZ',
    # 电力
    '600011.SH', '601991.SH', '600795.SH', '000591.SZ', '600900.SH',
    # 军工
    '600893.SH', '002179.SZ', '300034.SZ', '688396.SH', '300724.SZ',
    # 新能源
    '300750.SZ', '002594.SZ', '600438.SH', '601012.SH', '002466.SZ',
]

# 获取股票名称
stock_basic = fetcher.pro.stock_basic(exchange='', list_status='L')
name_map = dict(zip(stock_basic['ts_code'], stock_basic['name']))

print(f'股票池: {len(pool)}只')
print(f'\n开始扫描20260611...\n')
print('='*70)

results = []

for i, ts_code in enumerate(pool):
    try:
        # 获取日线数据
        daily = fetcher.pro.daily(ts_code=ts_code, start_date='20260301', end_date='20260611')
        if len(daily) < 60:
            continue

        # 检查今天涨幅
        today_pct = float(daily.iloc[0]['pct_chg'])
        if today_pct < 5:
            continue

        # 二波检测
        is_wave2, wave_detail = detect_wave2_pattern(daily, lookback_days=90)

        # 技术面评分
        tech_score, tech_detail = score_technical_v2(daily, is_wave2)

        if tech_score >= 1.5:
            results.append({
                'code': ts_code,
                'name': name_map.get(ts_code, ts_code[:6]),
                'pct': today_pct,
                'tech': tech_score,
                'wave2': is_wave2,
                'F6': tech_detail.get('F6', {}),
                'F8': tech_detail.get('F8', {}),
            })

            status = '✓二波' if is_wave2 else '启动'
            print(f'{status} {name_map.get(ts_code, ts_code[:6]):<10} {today_pct:+5.1f}% 技术分{tech_score:.1f}')

            # 显示F6和F8详情
            if tech_detail.get('F6'):
                print(f'    F6换手率: {tech_detail["F6"].get("turnover_rate", "N/A")}% {tech_detail["F6"].get("note", "")}')
            if tech_detail.get('F8'):
                print(f'    F8成交量: {tech_detail["F8"].get("note", "")}')

        time.sleep(0.06)

    except Exception as e:
        print(f'错误 {ts_code}: {e}')
        pass

print('\n' + '='*70)
print(f'扫描完成：{len(pool)}只 → 发现{len(results)}只信号')
print('='*70)

if results:
    results.sort(key=lambda x: x['tech'], reverse=True)

    print(f'\nTOP10信号股票：\n')
    print(f'{"排名":<4} {"股票":<12} {"涨幅":<8} {"技术分":<8} {"二波":<6}')
    print('-'*60)

    for i, r in enumerate(results[:10], 1):
        wave2_mark = '✓' if r['wave2'] else '✗'
        print(f'{i:<4} {r["name"]:<12} {r["pct"]:+6.1f}%  {r["tech"]:<8.1f} {wave2_mark}')

    # 二波统计
    wave2_count = sum(1 for r in results if r['wave2'])
    print(f'\n二波确认信号: {wave2_count}只')

else:
    print('未发现有效信号')
