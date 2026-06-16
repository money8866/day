# -*- coding: utf-8 -*-
"""测试Tushare宏观数据接口——终端需求指标"""
import sys, os
sys.path.insert(0, '.')
import tushare as ts
from main import load_config, get_token
from datetime import datetime

config = load_config()
token = get_token(config)
pro = ts.pro_api(token=token)

# 检查可用接口
apis_to_test = [
    # 宏观数据
    ('宏观-新能源汽车', lambda: pro.new_share(start_date='20250101', end_date='20250630', fields='ts_code,name,issue_date')),
    ('宏观-工业增加值', lambda: pro.cn_gdp(q='2024', start_date='20240101', end_date='20251231')),
    # 消费数据
    ('消费者信心指数', lambda: pro.cx_consumer_confidence(start_date='20240101', end_date='20250630')),
    # 新能源车
    ('新能源车产销', lambda: pro.ev_stats(start_date='20250101', end_date='20250630')),
    # 科技硬件
    ('光伏装机', lambda: pro.solar_today_q(start_date='20250101', end_date='20250630')),
    # GPU/AI相关
    ('AI服务器', lambda: pro.ths_index(start_date='20250101', end_date='20250630')),
]

print("检查可用接口:")
for name, fn in apis_to_test:
    try:
        result = fn()
        if result is not None and len(result) > 0:
            print(f"  ✓ {name}: {len(result)}行, 列={list(result.columns)}")
            print(f"    样例: {result.iloc[0].to_dict()}")
        else:
            print(f"  ✗ {name}: 无数据")
    except Exception as e:
        print(f"  ✗ {name}: {str(e)[:60]}")

print("\n尝试其他可能的数据源:")
# 尝试用Tushare的日频数据做代理
test2 = [
    ('GDP季度', lambda: pro.cn_gdp(q='L', start_date='20240101', end_date='20250630')),
    ('工业利润', lambda: pro.indust_profit(start_date='20250101', end_date='20250630')),
    ('社零消费', lambda: pro.cn_cpi(start_date='20250101', end_date='20250630')),
    ('CPI', lambda: pro.cn_cpi(start_date='20250101', end_date='20250630')),
]

for name, fn in test2:
    try:
        result = fn()
        if result is not None and len(result) > 0:
            print(f"  ✓ {name}: {len(result)}行, 列={list(result.columns)}")
            if len(result) <= 5:
                print(f"    样例: {result.to_dict('records')}")
        else:
            print(f"  ✗ {name}: 无数据")
    except Exception as e:
        print(f"  ✗ {name}: {str(e)[:80]}")
