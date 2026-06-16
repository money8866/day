# -*- coding: utf-8 -*-
"""检查ths_member返回的字段结构"""
import sys
sys.path.insert(0, '.')
import tushare as ts
from main import load_config, get_token
import pandas as pd

config = load_config()
token = get_token(config)
pro = ts.pro_api(token=token)

# 不指定fields，看看所有字段
print("ths_member 默认字段:")
try:
    df = pro.ths_member(ts_code='885959.TI')  # PCB概念
    print(f"  共{len(df)}行")
    print(f"  字段: {list(df.columns)}")
    print(f"  样例: {df.head(3).to_dict('records')}")
except Exception as e:
    print(f"  失败: {e}")

print("\nths_index 默认字段:")
try:
    df = pro.ths_index(exchange='A', type='N')
    print(f"  共{len(df)}行")
    print(f"  字段: {list(df.columns)}")
    # 找PCB概念
    pcb = df[df['name'].str.contains('PCB', na=False)]
    print(f"  PCB相关:")
    print(pcb.to_string())
except Exception as e:
    print(f"  失败: {e}")
