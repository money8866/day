#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试东方财富API：获取全市场涨跌统计
"""
import requests
import json

def test_market_stats():
    """测试获取市场涨跌统计"""
    
    print("=" * 70)
    print("测试：东方财富市场涨跌统计API")
    print("=" * 70)
    
    # 方案1：使用 push2.eastmoney.com 的 marketStat 接口
    url = "http://push2.eastmoney.com/api/qt/stock/get"
    
    params = {
        "secid": "1.000001",  # 上证指数
        "fields": "f43,f169,f170,f44,f45,f47,f48,f60,f84,f85,f86,f168,f152",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b"
    }
    
    try:
        print(f"\n[请求] {url}")
        print(f"[参数] {params}")
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        print(f"\n[响应] {json.dumps(data, ensure_ascii=False, indent=2)}")
        
        if data.get('data'):
            d = data['data']
            print("\n" + "=" * 70)
            print("解析结果：")
            print("=" * 70)
            print(f"上涨家数 (f169): {d.get('f169', 'N/A')}")
            print(f"下跌家数 (f170): {d.get('f170', 'N/A')}")
            print(f"涨停家数 (f44): {d.get('f44', 'N/A')}")
            print(f"跌停家数 (f45): {d.get('f45', 'N/A')}")
            print(f"平盘家数 (f46): {d.get('f46', 'N/A')}")
            
            up = d.get('f169', 0) or 0
            down = d.get('f170', 0) or 0
            if up + down > 0:
                ratio = up / (up + down) * 100
                print(f"\n上涨比例: {ratio:.1f}%")
                
        else:
            print("\n[错误] 响应中没有 data 字段")
            
    except Exception as e:
        print(f"\n[异常] {e}")
        import traceback
        traceback.print_exc()

def test_market_stat_v2():
    """测试方案2：使用 quote.eastmoney.com 的 marketStat 接口"""
    
    print("\n" + "=" * 70)
    print("测试方案2：quote.eastmoney.com/marketStat")
    print("=" * 70)
    
    url = "http://quote.eastmoney.com/center/api/marketStat.php"
    
    params = {
        "cb": "",
        "date": "",
        "_": "1622000000000"
    }
    
    try:
        print(f"\n[请求] {url}")
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        print(f"\n[响应] {json.dumps(data, ensure_ascii=False, indent=2)}")
        
    except Exception as e:
        print(f"\n[异常] {e}")
        import traceback
        traceback.print_exc()

def test_market_stat_v3():
    """测试方案3：使用 push2.eastmoney.com 的 marketCenter 接口"""
    
    print("\n" + "=" * 70)
    print("测试方案3：push2.eastmoney.com/api/qt/clist/get")
    print("=" * 70)
    
    url = "http://push2.eastmoney.com/api/qt/clist/get"
    
    params = {
        "pn": "1",
        "pz": "1",
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f3",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",  # 沪深A股
        "fields": "f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f204,f205,f124,f1,f13",
        "_": "1622000000000"
    }
    
    try:
        print(f"\n[请求] {url}")
        print(f"[说明] 尝试获取沪深A股列表，统计涨跌...")
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        
        if data.get('data') and data['data'].get('diff'):
            total = data['data'].get('total', 0)
            print(f"\n[成功] 找到 {total} 只股票")
            print(f"[说明] 可以通过分页查询所有股票，统计涨跌家数")
            print(f"[注意] 这种方法比较慢，需要查询多页")
        else:
            print("\n[错误] 未获取到股票列表")
            
    except Exception as e:
        print(f"\n[异常] {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    # 测试方案1（推荐）
    test_market_stats()
    
    # 如果方案1失败，测试方案2
    # test_market_stat_v2()
    
    # 如果都失败，测试方案3
    # test_market_stat_v3()
