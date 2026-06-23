#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
每日主题-个股对应关系映射生成器
使用 theme_pattern_stock_picker.py 中的 match_theme_stocks 算法，
生成所有主题与个股的对应关系 JSON 文件。

输出文件：d:\mystock\cache_daily\theme_stock_map_{TRADE_DATE}.json

JSON 结构：
{
    "trade_date": "20260618",
    "update_time": "2026-06-18T15:30:00",
    "themes": {
        "光通信": [
            {"code": "300308.SZ", "name": "中际旭创", "via": "leader_company", "chain_distance": 0, "score": 35},
            ...
        ]
    },
    "stocks": {
        "300308.SZ": {
            "name": "中际旭创",
            "themes": ["光通信", "AI算力链"]
        }
    }
}
"""

import sys
import os
import json
from datetime import datetime

# Windows GBK 控制台输出修复
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# 添加项目路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(BASE_DIR)
sys.path.append(parent_dir)
sys.path.append(BASE_DIR)

# 导入所需模块
from tushare_quant import pro, TRADE_DATE
import theme_trend_sentiment_score as theme_ts

CACHE_DIR = r"d:\mystock\cache_daily"
os.makedirs(CACHE_DIR, exist_ok=True)


def build_theme_stock_map():
    """
    构建主题-个股对应关系映射
    """
    print(f"[开始] 构建主题-个股映射: {TRADE_DATE}")
    
    # 1. 加载主题配置
    theme_path = os.path.join(BASE_DIR, 'theme.json')
    with open(theme_path, 'r', encoding='utf-8') as f:
        hot_themes = json.load(f)['HOT_THEMES']
    print(f"[加载] 共 {len(hot_themes)} 个主题配置")
    
    # 2. 获取东财成分股数据和股票基本信息
    dc_df = theme_ts.get_dc_members()
    try:
        stock_basic_df = pro.stock_basic(fields='ts_code,industry,name')
    except Exception as e:
        print(f"[错误] 获取 stock_basic 失败: {e}")
        return None
    
    # 3. 调用 match_theme_stocks 进行匹配
    # 返回: theme_stock_map, name_map_basic, stock_basic_industry, stock_concepts
    theme_stock_map, name_map_basic, stock_basic_industry, stock_concepts = theme_ts.match_theme_stocks(
        hot_themes, dc_df, stock_basic_df
    )
    
    print(f"[匹配] 共 {len(theme_stock_map)} 个主题匹配到成份股")
    
    # 4. 构建正向映射: theme -> [stock, ...]
    themes_output = {}
    total_stock_refs = 0
    for theme_name, stocks in theme_stock_map.items():
        stock_list = []
        for code, meta in stocks.items():
            stock_name = name_map_basic.get(code, code)
            stock_list.append({
                "code": code,
                "name": stock_name,
                "via": meta.get("via", ""),
                "chain_distance": meta.get("chain_distance", 2),
                "industry_match": meta.get("industry_match", False),
                "score": meta.get("score", 0),
                "industry": stock_basic_industry.get(code, ""),
                "concepts": stock_concepts.get(code, []),
            })
            total_stock_refs += 1
        # 按 score 降序排列
        stock_list.sort(key=lambda x: -x['score'])
        themes_output[theme_name] = stock_list
    
    # 5. 构建反向映射: stock -> [theme, ...]
    stocks_output = {}
    for theme_name, stocks in theme_stock_map.items():
        for code in stocks:
            if code not in stocks_output:
                stock_name = name_map_basic.get(code, code)
                stocks_output[code] = {
                    "name": stock_name,
                    "industry": stock_basic_industry.get(code, ""),
                    "concepts": stock_concepts.get(code, []),
                    "themes": [],
                }
            stocks_output[code]["themes"].append(theme_name)
    
    # 为每只股票的主题按 score 排序
    for code in stocks_output:
        theme_list = stocks_output[code]["themes"]
        theme_with_score = []
        for t in theme_list:
            if t in theme_stock_map and code in theme_stock_map[t]:
                theme_with_score.append((t, theme_stock_map[t][code].get("score", 0)))
            else:
                theme_with_score.append((t, 0))
        theme_with_score.sort(key=lambda x: -x[1])
        stocks_output[code]["themes"] = [t[0] for t in theme_with_score]
    
    # 6. 组装最终 JSON
    output = {
        "trade_date": TRADE_DATE,
        "update_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "n_themes": len(themes_output),
        "n_stocks": len(stocks_output),
        "n_stock_refs": total_stock_refs,
        "themes": themes_output,
        "stocks": stocks_output,
    }
    
    # 7. 保存到缓存目录
    output_file = os.path.join(CACHE_DIR, f"theme_stock_map_{TRADE_DATE}.json")
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    # 同时更新最新版本（无日期后缀，方便引用）
    latest_file = os.path.join(CACHE_DIR, "theme_stock_map_latest.json")
    with open(latest_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"[保存] {output_file}")
    print(f"[保存] {latest_file}")
    print(f"[统计] {len(themes_output)} 个主题, {len(stocks_output)} 只个股, {total_stock_refs} 条映射关系")
    
    return output


def load_theme_stock_map(trade_date=None):
    """加载指定日期的主题-个股映射"""
    if trade_date is None:
        latest_file = os.path.join(CACHE_DIR, "theme_stock_map_latest.json")
        if os.path.exists(latest_file):
            with open(latest_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None
    
    cache_file = os.path.join(CACHE_DIR, f"theme_stock_map_{trade_date}.json")
    if os.path.exists(cache_file):
        with open(cache_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None


def get_stock_themes(ts_code, trade_date=None):
    """查询某只股票所属的所有主题"""
    data = load_theme_stock_map(trade_date)
    if data and ts_code in data.get("stocks", {}):
        return data["stocks"][ts_code]
    return None


def get_theme_stocks(theme_name, trade_date=None):
    """查询某个主题的所有成份股"""
    data = load_theme_stock_map(trade_date)
    if data and theme_name in data.get("themes", {}):
        return data["themes"][theme_name]
    return None


if __name__ == '__main__':
    build_theme_stock_map()
    
    # 测试查询
    print("\n=== 测试查询 ===")
    data = load_theme_stock_map()
    if data:
        # 测试个股查询
        test_codes = ['600487.SH', '300308.SZ']
        for code in test_codes:
            info = get_stock_themes(code)
            if info:
                print(f"{info['name']}({code}): 主题={info['themes']}")
        
        # 测试主题查询
        test_themes = ['光通信', '人形机器人']
        for theme in test_themes:
            stocks = get_theme_stocks(theme)
            if stocks:
                print(f"{theme}: {len(stocks)} 只成份股")
                for s in stocks[:5]:
                    print(f"  {s['code']} {s['name']} via={s['via']} score={s['score']}")
