#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试东方财富API：获取全市场涨跌统计（修正版）
"""
import requests
import json

def test_market_overview():
    """测试方案：使用 quote.eastmoney.com 市场总貌接口"""
    
    print("=" * 70)
    print("测试：东方财富市场总貌API")
    print("=" * 70)
    
    # 方案A：市场总貌页面API
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
        "fields": "f12,f14,f2,f3,f62,f128,f136,f115,f152",
        "_": "1622000000000"
    }
    
    try:
        print(f"\n[请求] {url}")
        print(f"[说明] 获取沪深A股第1页，查看total字段")
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        
        if data.get('data'):
            total = data['data'].get('total', 0)
            print(f"\n[成功] 沪深A股总数: {total}")
            print(f"[说明] 需要分页查询所有股票才能统计涨跌")
            print(f"[结论] ❌ 此方案不可行（需要查询所有页）")
        else:
            print("\n[错误] 未获取到数据")
            
    except Exception as e:
        print(f"\n[异常] {e}")

def test_market_stat_api():
    """测试方案：使用 marketStat.php 接口"""
    
    print("\n" + "=" * 70)
    print("测试：marketStat.php 接口")
    print("=" * 70)
    
    url = "http://quote.eastmoney.com/center/api/marketStat.php"
    
    try:
        print(f"\n[请求] {url}")
        
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        print(f"\n[响应] {json.dumps(data, ensure_ascii=False, indent=2)}")
        
        if data.get('rc') == 0 and data.get('rt') == 0:
            print("\n[成功] 获取到市场统计数据")
            # 解析数据
            diff = data.get('data', {}).get('diff', [])
            if diff:
                print(f"[说明] 返回了 {len(diff)} 个市场的数据")
        else:
            print("\n[错误] 响应状态码异常")
            
    except Exception as e:
        print(f"\n[异常] {e}")
        import traceback
        traceback.print_exc()

def test_index_market_stat():
    """测试方案：使用指数行情获取市场统计"""
    
    print("\n" + "=" * 70)
    print("测试：上证指数 + 深证成指 综合统计")
    print("=" * 70)
    
    # 同时查询上证和深证的市场统计
    url = "http://push2.eastmoney.com/api/qt/stock/get"
    
    # 上证指数
    params_sh = {
        "secid": "1.000001",
        "fields": "f43,f44,f45,f46,f47,f48,f168,f169,f170",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b"
    }
    
    # 深证成指
    params_sz = {
        "secid": "0.399001",
        "fields": "f43,f44,f45,f46,f47,f48,f168,f169,f170",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b"
    }
    
    try:
        print("\n[请求1] 上证指数市场统计")
        r1 = requests.get(url, params=params_sh, timeout=10)
        d1 = r1.json().get('data', {})
        
        print("\n[请求2] 深证成指市场统计")
        r2 = requests.get(url, params=params_sz, timeout=10)
        d2 = r2.json().get('data', {})
        
        print("\n" + "=" * 70)
        print("解析结果：")
        print("=" * 70)
        
        # 上证
        print(f"\n【上证】")
        print(f"  f169 (未知): {d1.get('f169', 'N/A')}")
        print(f"  f170 (未知): {d1.get('f170', 'N/A')}")
        print(f"  f44 (未知): {d1.get('f44', 'N/A')}")
        print(f"  f45 (未知): {d1.get('f45', 'N/A')}")
        print(f"  f46 (未知): {d1.get('f46', 'N/A')}")
        
        # 深证
        print(f"\n【深证】")
        print(f"  f169 (未知): {d2.get('f169', 'N/A')}")
        print(f"  f170 (未知): {d2.get('f170', 'N/A')}")
        print(f"  f44 (未知): {d2.get('f44', 'N/A')}")
        print(f"  f45 (未知): {d2.get('f45', 'N/A')}")
        print(f"  f46 (未知): {d2.get('f46', 'N/A')}")
        
        print("\n[说明] 需要查找东方财富API文档，确定正确的字段含义")
        
    except Exception as e:
        print(f"\n[异常] {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    # 测试方案1：marketStat.php
    test_market_stat_api()
    
    # 测试方案2：指数市场统计（尝试不同的字段）
    # test_index_market_stat()
    
    # 测试方案3：分页查询全市场（太慢，不推荐）
    # test_market_overview()
