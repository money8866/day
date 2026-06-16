
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
验证个股主题归属准确性
"""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 先读取主题配置文件
cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'theme.json')
theme_cfg = {}
if os.path.exists(cfg_path):
    with open(cfg_path, 'r', encoding='utf-8') as f:
        theme_cfg = json.load(f).get('HOT_THEMES', {})

print("="*80)
print("主题配置分析")
print("="*80)
for theme_name, theme_data in theme_cfg.items():
    print(f"\n【{theme_name}】")
    print(f"  概念: {theme_data.get('concepts', [])}")
    print(f"  行业: {theme_data.get('industries', [])}")

# 从tushare_quant.py中导入函数
from tushare_quant import (
    get_hist_data,
    calc_theme_confidence,
    _find_best_theme,
)

def get_stock_info(ts_code):
    """获取个股基础信息"""
    # 简化的模拟数据，真实环境中应该从东财数据接口获取
    stock_info_map = {
        '300679.SZ': {
            'name': '电连技术',
            'industries': ['电子', '消费电子'],
            'concepts': ['消费电子', '连接器', '智能驾驶', '汽车电子'],
        },
        '000823.SZ': {
            'name': '超声电子',
            'industries': ['电子', '印制电路板'],
            'concepts': ['PCB', '5G', 'HDI', '消费电子'],
        },
        '300576.SZ': {
            'name': '容大感光',
            'industries': ['电子', '电子化学品'],
            'concepts': ['光刻胶', '半导体', 'PCB', '集成电路'],
        },
        '300433.SZ': {
            'name': '蓝思科技',
            'industries': ['电子', '消费电子'],
            'concepts': ['消费电子', '苹果概念', '智能穿戴', '汽车电子', 'VR概念'],
        },
        '300613.SZ': {
            'name': '富瀚微',
            'industries': ['电子', '半导体'],
            'concepts': ['半导体', '芯片', '汽车电子', '安防'],
        },
        '002183.SZ': {
            'name': '怡亚通',
            'industries': ['交通运输', '物流'],
            'concepts': ['供应链', '电子商务', '白酒', '区块链'],
        },
    }
    return stock_info_map.get(ts_code, {})

print("\n" + "="*80)
print("个股主题归属验证")
print("="*80)

check_stocks = [
    ('300679.SZ', '电连技术', '人形机器人'),
    ('000823.SZ', '超声电子', '商业航天'),
    ('300576.SZ', '容大感光', '半导体'),
    ('300433.SZ', '蓝思科技', 'AI算力链'),
    ('300613.SZ', '富瀚微', '半导体'),
    ('002183.SZ', '怡亚通', '半导体'),
]

for ts_code, name, report_theme in check_stocks:
    print(f"\n{name} ({ts_code})")
    print("-"*60)
    
    stock_info = get_stock_info(ts_code)
    print(f"  行业: {stock_info.get('industries', [])}")
    print(f"  概念: {stock_info.get('concepts', [])}")
    
    # 计算与报告主题的匹配度
    confidence = calc_theme_confidence(stock_info, report_theme)
    print(f"  与【{report_theme}】的匹配度: {confidence:.2f}")
    
    # 计算与所有主题的匹配度
    print(f"\n  所有主题匹配度:")
    all_confidences = []
    for theme_name in theme_cfg.keys():
        conf = calc_theme_confidence(stock_info, theme_name)
        all_confidences.append((theme_name, conf))
    all_confidences.sort(key=lambda x: -x[1])
    
    for i, (theme_name, conf) in enumerate(all_confidences[:5]):
        mark = " ★" if theme_name == report_theme else ""
        print(f"    {i+1}. {theme_name}: {conf:.2f}{mark}")
    
    # 系统自动选择的主题
    auto_theme = _find_best_theme(stock_info)
    print(f"\n  系统自动选择的主题: {auto_theme}")
    if auto_theme == report_theme:
        print(f"  ✅ 系统自动选择与报告一致")
    else:
        print(f"  ⚠️ 系统自动选择与报告不一致")

