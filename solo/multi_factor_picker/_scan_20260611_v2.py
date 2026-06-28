"""全市场扫描20260611信号（简化版）"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

import time
import pandas as pd
from data_fetcher import DataFetcher
from trend_picker_v2_draft import detect_wave2_pattern, score_technical_v2

token = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
fetcher = DataFetcher(token, {'cache': {'dir': 'cache'}})

# 股票池：沪深300成分股
print('获取沪深300成分股...')
hs300 = fetcher.pro.index_weight(index_code='399300.SZ', trade_date='20260610')
pool = hs300['con_code'].tolist()
print(f'股票池: {len(pool)}只')

# 补充股票名称
stock_basic = fetcher.pro.stock_basic(exchange='', list_status='L')
name_map = dict(zip(stock_basic['ts_code'], stock_basic['name']))

print(f'\n开始扫描20260611...')
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

        # 总分估算（简化：仅技术面）
        total_score = tech_score

        if total_score >= 1.5:  # 有一定信号
            results.append({
                'code': ts_code,
                'name': name_map.get(ts_code, ts_code[:6]),
                'pct': today_pct,
                'tech': tech_score,
                'wave2': is_wave2,
                'detail': tech_detail
            })

            status = '✓二波' if is_wave2 else '启动'
            print(f'{status} {name_map.get(ts_code, ts_code[:6]):<8} {today_pct:+5.1f}% 技术分{tech_score:.1f}')

        time.sleep(0.05)

    except Exception as e:
        pass

    if (i+1) % 100 == 0:
        print(f'已扫描 {i+1}/{len(pool)}...')

print('\n' + '='*70)
print(f'扫描完成：{len(pool)}只 → 发现{len(results)}只信号')
print('='*70)

if results:
    results.sort(key=lambda x: x['tech'], reverse=True)

    print(f'\nTOP15信号股票：\n')
    print(f'{"排名":<4} {"股票":<10} {"涨幅":<8} {"技术分":<8} {"二波":<6}')
    print('-'*60)

    for i, r in enumerate(results[:15], 1):
        wave2_mark = '✓' if r['wave2'] else '✗'
        print(f'{i:<4} {r["name"]:<10} {r["pct"]:+6.1f}%  {r["tech"]:<8.1f} {wave2_mark}')

    # 二波统计
    wave2_count = sum(1 for r in results if r['wave2'])
    print(f'\n二波确认信号: {wave2_count}只')

else:
    print('未发现有效信号')
