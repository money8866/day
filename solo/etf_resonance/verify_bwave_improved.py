"""验证BWave改进版:逐个过滤器统计被拦截的信号"""
import sys
import os
sys.path.insert(0, r'd:\mystock\solo')
os.chdir(r'd:\mystock\solo')

from bwave_strategy import (
    get_data, detect_awave, detect_bwave, detect_bwave_relaxed,
    check_launch_signal, detect_bwave_divergence,
    calc_bwave_score, calc_divergence_score,
)
import pandas as pd

stocks = pd.read_csv('D:/mystock/solo/report_daily/bull_stocks_qualified.csv', dtype=str)
codes = stocks['code'].head(60).tolist()

stats = {
    'total': 0, 'no_awave': 0, 'no_bwave': 0,
    'launch_raw': 0, 'launch_dist_filter': 0, 'launch_fake_golden': 0,
    'launch_score_fail': 0, 'launch_stopped': 0, 'launch_pass': 0,
    'div_raw': 0, 'div_dscore_fail': 0, 'div_score_fail': 0,
    'div_stopped': 0, 'div_pass': 0,
}

for c in codes:
    df = get_data(c + '.SH' if c.startswith('6') else c + '.SZ')
    if df is None or len(df) < 250:
        stats['no_awave'] += 1
        continue
    stats['total'] += 1
    awave = detect_awave(df)
    if awave is None:
        stats['no_awave'] += 1
        continue

    bwave = detect_bwave(df, awave)
    bwave_r = detect_bwave_relaxed(df, awave)
    if not bwave and not bwave_r:
        stats['no_bwave'] += 1
        continue

    MIN_DIST_TO_A_HIGH = 5.0
    passed = False

    for bw in [bwave, bwave_r]:
        if bw is None:
            continue
        launch = check_launch_signal(df, awave, bw)
        if launch and len(df) - launch['launch_idx'] <= 10:
            stats['launch_raw'] += 1
            dist_ah = launch.get('dist_to_a_high', 99)
            is_low = dist_ah < 15
            dif_val = df.iloc[launch['launch_idx']].get('macd_dif_bfq', 0)
            is_fake = launch.get('macd_golden', 0) and dif_val < 0
            if dist_ah < MIN_DIST_TO_A_HIGH:
                stats['launch_dist_filter'] += 1
            elif is_low and is_fake:
                stats['launch_fake_golden'] += 1
            else:
                s = calc_bwave_score(awave, bw, launch)
                if s['total'] < 65:
                    stats['launch_score_fail'] += 1
                else:
                    stop_line = bw['low_price'] * 0.97
                    stopped = any(df.iloc[j]['close'] < stop_line
                                  for j in range(launch['launch_idx'], len(df)))
                    if stopped:
                        stats['launch_stopped'] += 1
                    else:
                        stats['launch_pass'] += 1
                        passed = True

        if not passed:
            div = detect_bwave_divergence(df, awave, bw)
            if div:
                stats['div_raw'] += 1
                s = calc_divergence_score(awave, bw, div)
                d_score = s.get('l_score', 0)
                if d_score < 40:
                    stats['div_dscore_fail'] += 1
                elif s['total'] < 60:
                    stats['div_score_fail'] += 1
                else:
                    stop_line = bw['low_price'] * 0.97
                    stopped = any(df.iloc[j]['close'] < stop_line
                                  for j in range(div['launch_idx'], len(df)))
                    if stopped:
                        stats['div_stopped'] += 1
                    else:
                        stats['div_pass'] += 1
                        passed = True
        if passed:
            break

print('=' * 60)
print('BWave改进版过滤器效果统计 (60只股票)')
print('=' * 60)
print(f'总股票数:              {stats["total"]}')
print(f'无A浪:                 {stats["no_awave"]}')
print(f'无B浪:                 {stats["no_bwave"]}')
print()
print('--- 启动信号 ---')
print(f'原始触发:              {stats["launch_raw"]}')
print(f'  被距A高<5%过滤:      {stats["launch_dist_filter"]}')
print(f'  被假金叉过滤:         {stats["launch_fake_golden"]}')
print(f'  被评分<65过滤:       {stats["launch_score_fail"]}')
print(f'  被止损过滤:           {stats["launch_stopped"]}')
print(f'  通过:                {stats["launch_pass"]}')
print()
print('--- 底背离信号 ---')
print(f'原始触发:              {stats["div_raw"]}')
print(f'  被d_score<40过滤:    {stats["div_dscore_fail"]}')
print(f'  被评分<60过滤:       {stats["div_score_fail"]}')
print(f'  被止损过滤:           {stats["div_stopped"]}')
print(f'  通过:                {stats["div_pass"]}')
print()
print(f'最终通过信号数:        {stats["launch_pass"] + stats["div_pass"]}')
