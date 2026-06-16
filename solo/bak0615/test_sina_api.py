#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""测试新浪市场总貌接口"""
import requests
import re
import json

url = 'http://vip.stock.finance.sina.com.cn/q/view/newMarketsDataAll.php'
headers = {'Referer': 'https://finance.sina.com.cn/', 'User-Agent': 'Mozilla/5.0'}

try:
    r = requests.get(url, headers=headers, timeout=10)
    text = r.text.strip()
    print(f'返回长度: {len(text)}')
    print(f'前300字符: {text[:300]}')
    
    # 尝试解析JSON
    json_match = re.search(r'\((.*)\)', text)
    if json_match:
        print('找到JSON匹配')
        try:
            market_data = json.loads(json_match.group(1))
            print(f'解析成功:')
            print(f'  total={market_data.get("total", 0)}')
            print(f'  up={market_data.get("up", 0)}')
            print(f'  down={market_data.get("down", 0)}')
            print(f'  zt={market_data.get("zt", 0)}')
            print(f'  dt={market_data.get("dt", 0)}')
        except Exception as e:
            print(f'JSON解析失败: {e}')
    else:
        print('未找到JSON匹配')
        # 尝试直接解析
        try:
            data = json.loads(text)
            print(f'直接解析成功: {type(data)}')
            print(f'数据: {data}')
        except Exception as e2:
            print(f'直接解析失败: {e2}')
            
        # 尝试其他正则
        json_match2 = re.search(r'\{.*\}', text)
        if json_match2:
            print(f'找到大括号匹配: {json_match2.group(0)[:100]}')
            
except Exception as e:
    print(f'请求错误: {e}')
