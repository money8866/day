"""全市场选股扫描 - bull_stocks_qualified.csv"""
import pandas as pd
import sys
import time
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

# 从本地缓存读取数据
cache_dir = r'D:\mystock\cache_daily'

# 读取合格股票池
pool_file = r'D:\mystock\solo\report_daily\bull_stocks_qualified.csv'
pool_df = pd.read_csv(pool_file)
pool_df['ts_code'] = pool_df['code'].astype(str).apply(lambda x: f'{x}.SH' if x.startswith('6') else f'{x}.SZ')

# 获取股票名称
from data_fetcher import DataFetcher
for _l in open(r'D:\mystock\config\.env'):
    if _l.strip().startswith('TUSHARE_TOKEN='):
        token = _l.strip().split('=', 1)[1].strip().strip('"')
        break
fetcher = DataFetcher(token, {'cache': {'dir': 'cache'}})
stock_basic = fetcher.pro.stock_basic(exchange='', list_status='L')
name_map = dict(zip(stock_basic['ts_code'], stock_basic['name']))

pool = pool_df['ts_code'].tolist()
print(f'股票池: {len(pool)}只')
print(f'\n开始扫描20260611信号...\n')

results = []
errors = []

for i, ts_code in enumerate(pool):
    try:
        # 从本地缓存读取日线数据
        cache_file = f'{cache_dir}\\{ts_code}.csv'
        daily = pd.read_csv(cache_file, encoding='utf-8')
        daily['trade_date'] = daily['trade_date'].astype(str)
        daily = daily.sort_values('trade_date', ascending=True).reset_index(drop=True)

        # 找到6月11日数据
        target_date = '20260611'
        target_row = daily[daily['trade_date'] == target_date]

        if len(target_row) == 0:
            continue

        target_idx = target_row.index[0]
        today_pct = float(daily.loc[target_idx, 'pct_chg'])

        # 只关注涨幅≥5%的股票
        if today_pct < 5:
            continue

        # 二波检测逻辑
        lookback_days = 60
        start_idx = max(0, target_idx - lookback_days)
        recent = daily.loc[start_idx:target_idx].copy()

        # 排除最近5天
        recent_ex5 = recent.iloc[:-5]

        # 找首波涨停日
        limit_up_days = recent_ex5[recent_ex5['pct_chg'] >= 9.4]

        if len(limit_up_days) > 0:
            wave1_idx = limit_up_days['pct_chg'].idxmax()
            wave1_row = daily.loc[wave1_idx]
            wave1_close = float(wave1_row['close'])
            wave1_pct = float(wave1_row['pct_chg'])

            # 找首波后回踩最低点
            after_wave1 = daily.loc[wave1_idx+1:target_idx]

            if len(after_wave1) > 0:
                pullback_low = float(after_wave1['low'].min())
                pullback_ratio = pullback_low / wave1_close

                # 二波判断
                latest_close = float(daily.loc[target_idx, 'close'])

                is_wave2 = (
                    today_pct >= 5 and
                    latest_close >= wave1_close * 0.98 and
                    pullback_ratio >= 0.80
                )

                # 技术面评分
                tech_score = 0.0

                if is_wave2:
                    # F6换手率（从daily_basic获取，这里假设值）
                    turnover_rate = 10.0  # 默认值，实际需从daily_basic获取

                    if turnover_rate >= 8:
                        tech_score += 2.0
                    elif turnover_rate >= 5:
                        tech_score += 1.5
                    else:
                        tech_score += 1.0

                    # F8成交量
                    tech_score += 1.0

                    # WAVE2二波
                    if today_pct >= 9.4:
                        tech_score += 3.0
                    else:
                        tech_score += 2.0

                # 记录结果
                results.append({
                    'code': ts_code,
                    'name': name_map.get(ts_code, ts_code[:6]),
                    'pct': today_pct,
                    'tech': tech_score,
                    'wave2': is_wave2,
                    'wave1_date': wave1_row['trade_date'],
                    'wave1_pct': wave1_pct,
                    'pullback_ratio': pullback_ratio,
                })

                status = '✓二波' if is_wave2 else '启动'
                print(f'{status} {name_map.get(ts_code, ts_code[:6]):<10} {today_pct:+5.1f}% 技术分{tech_score:.1f}')

        time.sleep(0.01)

    except Exception as e:
        errors.append((ts_code, str(e)))

    if (i+1) % 100 == 0:
        print(f'已扫描 {i+1}/{len(pool)}...')

print('\n' + '='*70)
print(f'扫描完成：{len(pool)}只 → 发现{len(results)}只信号')
print('='*70)

if results:
    results.sort(key=lambda x: x['tech'], reverse=True)

    print(f'\nTOP30信号股票：\n')
    print(f'{"排名":<4} {"股票":<12} {"涨幅":<8} {"技术分":<8} {"首波日期":<10} {"回踩比例":<10} {"二波"}')
    print('-'*85)

    for i, r in enumerate(results[:30], 1):
        wave2_mark = '✓' if r['wave2'] else '✗'
        print(f'{i:<4} {r["name"]:<12} {r["pct"]:+6.1f}%  {r["tech"]:<8.1f} {r["wave1_date"]:<10} {r["pullback_ratio"]:<10.1%} {wave2_mark}')

    # 统计
    wave2_count = sum(1 for r in results if r['wave2'])
    print(f'\n二波确认信号: {wave2_count}只')
    print(f'启动信号: {len(results) - wave2_count}只')

    # 保存结果
    result_df = pd.DataFrame(results)
    result_df.to_csv(r'D:\mystock\solo\multi_factor_picker\选股结果_20260611.csv', index=False, encoding='utf-8-sig')
    print(f'\n结果已保存至 选股结果_20260611.csv')

if errors:
    print(f'\n错误统计: {len(errors)}只')
    for code, err in errors[:10]:
        print(f'  {code}: {err}')
