# -*- coding: utf-8 -*-
"""report_rc 接口测试：了解字段结构和数据量
"""
import sys
sys.path.insert(0, '.')

import tushare as ts
from datetime import datetime, timedelta
from main import load_config, get_token
import pandas as pd

config = load_config()
token = get_token(config)
pro = ts.pro_api(token=token)

print("=" * 60)
print("1) 查最近一天的 report_rc（最新 3000 条）")
print("=" * 60)

today = datetime.now().strftime('%Y%m%d')
# 倒推 3 天以防当日无数据
for day_offset in range(0, 10):
    check_day = (datetime.now() - timedelta(days=day_offset)).strftime('%Y%m%d')
    try:
        df = pro.report_rc(ann_date=check_day, fields='ts_code,name,report_date,report_type,org_name,researcher,rating_agency,rating,rating_change,predict_next_eps,predict_next_year_eps,predict_this_eps,predict_this_year_eps,predict_this_year_net_profit,predict_next_year_net_profit,max_profit,min_profit,max_eps,min_eps')
        if df is not None and len(df) > 0:
            print(f"ann_date={check_day}: {len(df)} 条")
            print("字段:", list(df.columns))
            print("\n示例前 5 行:")
            print(df.head(5).to_string())
            print("\n去重 ts_code 数量:", df['ts_code'].nunique())
            print("去重机构数量:", df['org_name'].nunique())
            if 'predict_this_year_eps' in df.columns:
                print("this_year_eps 非空占比:", df['predict_this_year_eps'].notna().sum(), "/", len(df))
            if 'predict_next_year_eps' in df.columns:
                print("next_year_eps 非空占比:", df['predict_next_year_eps'].notna().sum(), "/", len(df))
            if 'predict_this_year_net_profit' in df.columns:
                print("this_year_net_profit 非空占比:", df['predict_this_year_net_profit'].notna().sum(), "/", len(df))
            if 'predict_next_year_net_profit' in df.columns:
                print("next_year_net_profit 非空占比:", df['predict_next_year_net_profit'].notna().sum(), "/", len(df))

            # 存一份样本
            df.head(20).to_csv('cache/_report_rc_sample.csv', index=False)
            print("\n样本已存 cache/_report_rc_sample.csv")
            break
        else:
            print(f"ann_date={check_day}: 无数据")
    except Exception as e:
        print(f"ann_date={check_day}: 错误 {e}")

print("\n" + "=" * 60)
print("2) 全量拉最近 90 天，看覆盖多少只股票")
print("=" * 60)

all_frames = []
for day_offset in range(0, 90, 7):
    check_day = (datetime.now() - timedelta(days=day_offset)).strftime('%Y%m%d')
    try:
        df = pro.report_rc(ann_date=check_day)
        if df is not None and len(df) > 0:
            all_frames.append(df)
            print(f"  {check_day}: {len(df)} 条, {df['ts_code'].nunique()} 只股, {df['org_name'].nunique()} 机构")
    except Exception as e:
        pass

if all_frames:
    big = pd.concat(all_frames, ignore_index=True)
    print(f"\n累计: {len(big)} 条, {big['ts_code'].nunique()} 只股, {big['org_name'].nunique()} 机构")
    print("字段完整列表:", list(big.columns))
    print("\n字段缺失率:")
    for col in big.columns:
        miss = big[col].isna().sum()
        if miss > 0:
            print(f"  {col}: {miss}/{len(big)} ({miss/len(big)*100:.1f}%)")

    # 取一只股票做样例
    sample_code = big['ts_code'].value_counts().idxmax()
    print(f"\n覆盖最多的股票: {sample_code}，共 {big['ts_code'].value_counts().max()} 条研报")
    print(big[big['ts_code'] == sample_code].head(10).to_string())
