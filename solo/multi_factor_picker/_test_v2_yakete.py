"""测试趋势模型v2对雅克科技6月10日的评分"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

import pandas as pd
from data_fetcher import DataFetcher
from trend_picker import get_daily_data, get_moneyflow_data, get_daily_basic, get_holder_data
from trend_picker_v2_draft import detect_wave2_pattern, score_fundamental_v2, score_technical_v2, STRATEGIC_INDUSTRIES, TREND_THEMES

# 配置
token = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
config = {'cache': {'dir': 'cache'}, 'tushare': {'token': token}}
fetcher = DataFetcher(token, config)

ts_code = '002409.SZ'
name = '雅克科技'
industry = '半导体'
end_date = '20260610'
start_date = '20260101'

print(f'=== {name}({ts_code}) 趋势模型v2测试 ===')
print(f'截止日期: {end_date}\n')

# 获取数据
daily = get_daily_data(fetcher, ts_code, start_date, end_date)
moneyflow = get_moneyflow_data(fetcher, ts_code, start_date, end_date)
daily_basic = get_daily_basic(fetcher, ts_code, end_date)
income = fetcher.get_income(ts_code)

print(f'日线数据: {len(daily)}条')

if len(daily) > 30:
    latest = daily.iloc[-1]
    print(f'最新日期: {latest["trade_date"]}')
    print(f'收盘价: {latest["close"]:.2f}')
    print(f'涨跌幅: {latest["pct_chg"]:.2f}%')
    
    # 二波检测
    is_wave2, wave2_detail = detect_wave2_pattern(daily)
    print(f'\n--- 二波检测 ---')
    print(f'二波确认: {is_wave2}')
    for k, v in wave2_detail.items():
        print(f'  {k}: {v}')
    
    # 基本面v2
    fund_score, fund_detail = score_fundamental_v2(fetcher, ts_code, industry, income, daily_basic, daily)
    print(f'\n--- 基本面v2: {fund_score:.2f}分 ---')
    for f in ['F1', 'F2', 'F3']:
        if f in fund_detail:
            print(f'{f}: {fund_detail[f].get("score", 0):.1f}分')
            for k, v in fund_detail[f].items():
                if k != 'score' and v and v != 0 and v != {}:
                    print(f'  {k}: {v}')
    
    # 技术面v2（含二波加分）
    tech_score, tech_detail = score_technical_v2(daily, is_wave2)
    print(f'\n--- 技术面v2: {tech_score:.2f}分 ---')
    for f in ['F7', 'F8', 'F9', 'WAVE2']:
        if f in tech_detail:
            print(f'{f}: {tech_detail[f].get("score", 0):.1f}分')
    
    # 总分对比
    print(f'\n=== 评分对比 ===')
    print(f'v1技术面: 0.75分（涨停无加分）')
    print(f'v2技术面: {tech_score:.2f}分（涨停豁免+二波加分）')
    print(f'v1 F3市值: 1.0分（390亿）')
    print(f'v2 F3市值: {fund_detail["F3"].get("score", 0):.1f}分')
    
    # 预估总分提升
    v1_total = 4.6
    v2_total = fund_score + 2.25 + tech_score  # 基本面+资金面(v1相同)+技术面v2
    print(f'\nv1总分: {v1_total:.1f}/18')
    print(f'v2总分: {v2_total:.1f}/22 (标准化: {v2_total/22*100:.1f}/100)')
