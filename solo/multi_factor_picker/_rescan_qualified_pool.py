"""重新扫描bull_stocks_qualified.csv - 全新进程"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

import pandas as pd
import time
from data_fetcher import DataFetcher
from trend_picker_v2_draft import detect_wave2_pattern, score_technical_v2

token = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
fetcher = DataFetcher(token, {'cache': {'dir': 'cache'}})

# 读取合格股票池
pool_file = r'D:\mystock\solo\report_daily\bull_stocks_qualified.csv'
pool_df = pd.read_csv(pool_file)
pool_df['ts_code'] = pool_df['code'].astype(str).apply(lambda x: f'{x}.SH' if x.startswith('6') else f'{x}.SZ')
pool = pool_df['ts_code'].tolist()

# 获取股票名称
stock_basic = fetcher.pro.stock_basic(exchange='', list_status='L')
name_map = dict(zip(stock_basic['ts_code'], stock_basic['name']))

print(f'股票池: {len(pool)}只')
print(f'\n开始扫描20260611信号...\n')
print('='*70)

results = []

for i, ts_code in enumerate(pool):
    try:
        # 获取日线数据
        daily = fetcher.pro.daily(ts_code=ts_code, start_date='20260301', end_date='20260611')
        if len(daily) < 60:
            continue

        # 获取基本数据
        basic = fetcher.pro.daily_basic(ts_code=ts_code, start_date='20260301', end_date='20260611')
        daily_merged = daily.merge(basic[['trade_date', 'turnover_rate', 'circ_mv']], on='trade_date', how='left')

        # 检查今天涨幅
        today_pct = float(daily_merged.iloc[0]['pct_chg'])
        if today_pct < 5:
            continue

        # 二波检测
        is_wave2, wave_detail = detect_wave2_pattern(daily_merged, lookback_days=60)

        # 技术面评分
        tech_score, tech_detail = score_technical_v2(daily_merged, is_wave2)

        if tech_score >= 2.0 or is_wave2:
            results.append({
                'code': ts_code,
                'name': name_map.get(ts_code, ts_code[:6]),
                'pct': today_pct,
                'tech': tech_score,
                'wave2': is_wave2,
                'turnover': float(daily_merged.iloc[0].get('turnover_rate', 0) or 0),
                'circ_mv': float(daily_merged.iloc[0].get('circ_mv', 0) or 0) / 10000,
                'wave_detail': wave_detail,
                'tech_detail': tech_detail,
            })

            status = '✓二波' if is_wave2 else '启动'
            print(f'{status} {name_map.get(ts_code, ts_code[:6]):<10} {today_pct:+5.1f}% 技术分{tech_score:.1f}')

        time.sleep(0.06)

    except Exception as e:
        pass

    if (i+1) % 100 == 0:
        print(f'已扫描 {i+1}/{len(pool)}...')

print('\n' + '='*70)
print(f'扫描完成：{len(pool)}只 → 发现{len(results)}只信号')
print('='*70)

if results:
    results.sort(key=lambda x: x['tech'], reverse=True)

    print(f'\nTOP20信号股票：\n')
    print(f'{"排名":<4} {"股票":<12} {"涨幅":<8} {"技术分":<8} {"换手率":<8} {"市值亿":<8} {"二波"}')
    print('-'*75)

    for i, r in enumerate(results[:20], 1):
        wave2_mark = '✓' if r['wave2'] else '✗'
        print(f'{i:<4} {r["name"]:<12} {r["pct"]:+6.1f}%  {r["tech"]:<8.1f} {r["turnover"]:<8.1f} {r["circ_mv"]:<8.1f} {wave2_mark}')

    # 统计
    wave2_count = sum(1 for r in results if r['wave2'])
    print(f'\n二波确认信号: {wave2_count}只')
    print(f'启动信号: {len(results) - wave2_count}只')

    # 保存结果
    result_df = pd.DataFrame([{k:v for k,v in r.items() if k not in ['wave_detail', 'tech_detail']} for r in results])
    result_df.to_csv(r'D:\mystock\solo\multi_factor_picker\signals_20260611.csv', index=False, encoding='utf-8-sig')
    print(f'\n结果已保存至 signals_20260611.csv')

else:
    print('未发现有效信号')
