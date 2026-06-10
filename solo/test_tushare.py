# -*- coding: utf-8 -*-
"""测试Tushare接口连接"""
import os
import sys

try:
    import tushare as ts
    
    # 从环境变量获取token
    TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
    print(f"Token状态: {'已设置' if TUSHARE_TOKEN else '未设置'}")
    
    if TUSHARE_TOKEN:
        try:
            ts.set_token(TUSHARE_TOKEN)
            pro = ts.pro_api()
            print("✅ Tushare Pro初始化成功")
            
            # 测试获取股票列表
            print("\n测试获取股票列表...")
            data = pro.stock_basic(exchange='', list_status='L', fields='ts_code,symbol,name,list_date')
            print(f"✅ 成功获取 {len(data)} 只股票")
            print("前5只股票:")
            print(data.head())
            
            # 测试获取日线数据
            print("\n测试获取日线数据...")
            df = pro.daily(ts_code='000001.SZ', start_date='20260601', end_date='20260609')
            print(f"✅ 成功获取 {len(df)} 条日线数据")
            print(df.tail())
            
        except Exception as e:
            print(f"❌ Tushare接口调用失败: {e}")
            sys.exit(1)
    else:
        print("❌ 环境变量 TUSHARE_TOKEN 未设置")
        sys.exit(1)
        
except ImportError:
    print("❌ 未安装tushare库，请先安装: pip install tushare")
    sys.exit(1)
