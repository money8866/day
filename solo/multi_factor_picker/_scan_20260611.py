"""全市场扫描20260611信号"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

import time
from datetime import datetime
from data_fetcher import DataFetcher
from trend_picker_v2_draft import TrendPickerV2

token = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
fetcher = DataFetcher(token, {'cache': {'dir': 'cache'}})
picker = TrendPickerV2(fetcher)

# 获取股票池（沪深300 + 双创龙头）
print('正在获取股票池...')
hs300 = fetcher.pro.index_weight(index_code='399300.SZ', trade_date='20260610')
pool_300 = hs300['con_code'].tolist()

# 补充双创龙头
gem_leaders = ['300750.SZ', '300059.SZ', '300015.SZ', '300003.SZ', '300033.SZ']
kc_leaders = ['688981.SH', '688111.SH', '688012.SH', '688008.SH', '688005.SH']
pool_all = list(set(pool_300 + gem_leaders + kc_leaders))

print(f'股票池总数: {len(pool_all)}')

# 扫描
print(f'\n开始扫描20260611信号...')
results = []

for i, ts_code in enumerate(pool_all):
    try:
        daily = fetcher.pro.daily(ts_code=ts_code, start_date='20260301', end_date='20260611')
        if len(daily) < 60:
            continue

        # 检查今天是否大涨（>=5%）
        today_pct = float(daily.iloc[0]['pct_chg'])
        if today_pct < 5:
            continue

        # 使用v2.3评分
        result = picker.score_single(ts_code, daily, fetcher)

        if result.total_score >= 10:  # 中等趋势以上
            results.append({
                'code': ts_code,
                'name': result.name,
                'pct': today_pct,
                'score': result.total_score,
                'normalized': result.normalized_score,
                'status': result.trend_status,
                'signal': result.buy_signal
            })
            print(f'✓ {result.name} {today_pct:+.1f}% 得分{result.total_score:.1f} {result.trend_status}')

        time.sleep(0.05)

    except Exception as e:
        pass

    if (i+1) % 50 == 0:
        print(f'已扫描 {i+1}/{len(pool_all)}...')

# 排序输出
print(f'\n{"="*60}')
print(f'20260611全市场信号统计')
print(f'扫描股票: {len(pool_all)}只')
print(f'触发信号: {len(results)}只')
print(f'{"="*60}\n')

if results:
    results.sort(key=lambda x: x['score'], reverse=True)

    print('TOP20强趋势信号：\n')
    print(f'{"排名":<4} {"股票":<8} {"涨幅":<6} {"得分":<6} {"标准化":<6} {"状态":<8} {"买点"}')
    print('-'*70)

    for i, r in enumerate(results[:20], 1):
        print(f'{i:<4} {r["name"]:<8} {r["pct"]:+5.1f}% {r["score"]:<6.1f} {r["normalized"]:<6.1f} {r["status"]:<8} {r["signal"]}')

    # 统计买点分布
    print(f'\n买点分布：')
    signal_counts = {}
    for r in results:
        sig = r['signal']
        signal_counts[sig] = signal_counts.get(sig, 0) + 1

    for sig, count in sorted(signal_counts.items(), key=lambda x: -x[1]):
        print(f'  {sig}: {count}只')

else:
    print('未发现强趋势信号')

# 保存结果
import json
with open('cache/trend_signals_20260611.json', 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

print(f'\n结果已保存至 cache/trend_signals_20260611.json')
