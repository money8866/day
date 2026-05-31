# -*- coding: utf-8 -*-
"""
快速入门脚本 - 演示涨停跟踪系统的核心功能
不需要完整配置 API 也可以测试部分功能
"""

import os
import sys
from datetime import datetime, timedelta

# 尝试加载配置
try:
    from dotenv import load_dotenv
    DOTENV_PATH = r"d:\mystock\config\.env"
    load_dotenv(DOTENV_PATH)
    
    TUSHARE_TOKEN = os.getenv('TUSHARE_TOKEN')
    DEEPSEEK_KEY = os.getenv('DEEPSEEK_API_KEY')
    SERVERCHAN_KEY = os.getenv('WECHAT_SCKEY')
    
    print("✓ 环境变量加载成功")
except:
    print("⚠ 环境变量加载失败，将使用默认配置")
    TUSHARE_TOKEN = None
    DEEPSEEK_KEY = None
    SERVERCHAN_KEY = None

print("\n" + "="*60)
print("📊 涨停跟踪系统 - 快速入门")
print("="*60)

# 1. 检查配置状态
print("\n[配置状态检查]")
print(f"Tushare Token: {'✓ 已配置' if TUSHARE_TOKEN and TUSHARE_TOKEN != 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx' else '✗ 未配置'}")
print(f"DeepSeek Key:  {'✓ 已配置' if DEEPSEEK_KEY and DEEPSEEK_KEY != 'sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx' else '✗ 未配置'}")
print(f"Server酱 Key:  {'✓ 已配置' if SERVERCHAN_KEY and SERVERCHAN_KEY != 'sctkxxxxxxxxxxxxxxxxxxxxxxxxxxxxx' else '✗ 未配置'}")

# 2. 显示系统功能
print("\n[系统功能]")
print("✓ 涨停数据采集 - 使用 Tushare limit_list_ths() 接口")
print("✓ 第一板筛选 - 自动排除连板股")
print("✓ 温和放量分析 - 量比 1.5-5.0 倍")
print("✓ 历史数据记录 - JSON 格式存储")
print("✓ 二波概率计算 - 6维度量化评分")
print("✓ DeepSeek AI分析 - 基本面和风格匹配")
print("✓ 微信推送 - Server酱 实时推送")

# 3. 展示使用示例
print("\n" + "="*60)
print("📝 使用示例")
print("="*60)

print("\n1. 运行每日涨停跟踪:")
print("   python limit_track_review.py 20260529")

print("\n2. 运行批处理文件:")
print("   run_limit_track.bat 20260529")

print("\n3. 查看历史记录:")
print(f"   文件位置: d:\\mystock\\solo\\cache_limit_track\\limit_history.json")

print("\n4. 查看复盘报告:")
print(f"   文件位置: d:\\mystock\\solo\\cache_limit_track\\reviews\\review_YYYYMMDD.txt")

# 4. 演示二波概率计算逻辑
print("\n" + "="*60)
print("📈 二波概率计算策略")
print("="*60)

strategies = [
    ("回调幅度", "25分", "最佳回调幅度 15%-30%，表明有资金关注但未出货"),
    ("均线多头", "20分", "MA5 > MA10 > MA20，表明趋势向上"),
    ("量能稳定", "15分", "量能变异系数 < 0.5，表明资金稳定"),
    ("突破前期高点", "20分", "突破近30日最高价，表明动能强劲"),
    ("近期涨停次数", "15分", "累计涨停次数越多，二波概率越高"),
    ("市场情绪", "5分", "市场整体氛围好时，二波更容易"),
]

print("\n评分维度：")
for i, (dim, score, desc) in enumerate(strategies, 1):
    print(f"{i}. {dim}（{score}）: {desc}")

print(f"\n综合二波概率 = 各项得分之和（满分100）")

# 5. 投资策略示例
print("\n" + "="*60)
print("🎯 投资策略建议")
print("="*60)

strategies_demo = [
    ("强者恒强", "二波概率 ≥ 60%", "激进型，追涨杀跌"),
    ("低吸潜伏", "二波概率 40%-60%", "稳健型，分批建仓"),
    ("观望等待", "二波概率 < 40%", "保守型，等待确认信号"),
]

for name, condition, style in strategies_demo:
    print(f"\n{name}（{condition}）")
    print(f"  风格: {style}")
    print(f"  建议: 控制仓位，及时止损")

# 6. 下一步操作
print("\n" + "="*60)
print("🚀 下一步操作")
print("="*60)

if not TUSHARE_TOKEN or TUSHARE_TOKEN == 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx':
    print("\n⚠️ 请先配置 Tushare Token:")
    print("   1. 访问 https://tushare.pro/ 注册账号")
    print("   2. 获取 API Token")
    print("   3. 编辑 d:\\mystock\\solo\\.env 文件")
    print("   4. 将 TUSHARE_TOKEN 改为您的真实 Token")
    print("\n配置完成后，运行:")
    print("   python test_limit_track.py")
else:
    print("\n✓ API 配置完成！")
    print("\n运行测试:")
    print("   python test_limit_track.py")
    print("\n运行每日跟踪:")
    print("   python limit_track_review.py")
    print("\n或使用批处理文件:")
    print("   run_limit_track.bat 20260529")

print("\n" + "="*60)
print("📚 查看详细文档:")
print("   LIMIT_TRACK_README.md")
print("="*60)
