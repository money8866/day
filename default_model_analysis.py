# -*- coding: utf-8 -*-
import requests
import json
import os
os.environ['PYTHONIOENCODING'] = 'utf-8'

API_KEY = os.environ.get('QCLOUD_BASE_URL', '') + os.environ.get('QCLOUD_API_KEY', '')
# Try OpenAI-compatible endpoint
import subprocess

# Use the default qclaw model route - just compose the analysis directly
data = {
"stock": "威迈斯 (688612.SH)",
"quote_date": "2026-05-22",
"price": 36.14,
"pct_chg": 9.05,
"pe_ttm": 25.74,
"pb": 4.04,
"ps_ttm": 2.38,
"mkt_cap": 151.49,
"circ_mv": 91.27,
"turnover_rate": 6.24,
"technical": {
    "MA5": 32.67,
    "MA10": 32.01,
    "MA20": 32.32,
    "MA60": 31.84,
    "52w_high": 42.26,
    "52w_low": 24.30,
    "52w_position_pct": 65.9,
    "vol_ratio": 1.61
},
"financials_quarterly": [
    {"period": "2026Q1", "eps": 0.31, "roe": 3.55, "gross_margin": 24.03, "net_margin": 9.61, "debt_ratio": 52.04, "np_yoy": 30.01, "or_yoy": 0.75},
    {"period": "2025Q4", "eps": 1.33, "roe": 16.19, "gross_margin": 20.94, "net_margin": 8.82, "debt_ratio": 55.49, "np_yoy": 39.51, "or_yoy": -0.48},
    {"period": "2025Q3", "eps": 1.01, "roe": 12.58, "gross_margin": 21.52, "net_margin": 9.27, "debt_ratio": 55.57, "np_yoy": 43.64, "or_yoy": 5.45}
],
"industry": "电力设备/新能源汽车电源",
"listing_date": "20230726",
"business": "新能源汽车车载充电机OBC、DC-DC转换器等电源产品，客户覆盖比亚迪、吉利、长安、理想、小鹏等主流车企，并已供货大众、Stellantis等国际车企",
"market_context": {
    "shanghai_index": 4112.9,
    "emotion_index": 90.3,
    "market_phase": "高潮区",
    "plate_hot": "电子(137.68)、机械设备(94.33)、基础化工(92.05)",
    "bomb_board_rate": 58.3
}
}

prompt = f"""你是一位专业的A股量化分析师。请严格基于以下数据，对威迈斯（688612.SH）进行深度分析。

数据：
- 现价：{data['price']}元（今日涨幅+{data['pct_chg']}%）
- PE(TTM)：{data['pe_ttm']} | PB：{data['pb']} | PS：{data['ps_ttm']}
- 总市值：{data['mkt_cap']}亿 | 流通市值：{data['circ_mv']}亿 | 换手率：{data['turnover_rate']}%
- 技术面：MA5={data['technical']['MA5']} | MA10={data['technical']['MA10']} | MA20={data['technical']['MA20']} | MA60={data['technical']['MA60']}
- 52W：高={data['technical']['52w_high']} | 低={data['technical']['52w_low']} | 分位={data['technical']['52w_position_pct']}%
- 量比：{data['technical']['vol_ratio']}

财务（季度）：
- 2026Q1：EPS=0.31 ROE=3.55% 毛利率=24.03% 净利率=9.61% 负债率=52.04% 净利+30.01% 营收+0.75%
- 2025Q4：EPS=1.33 ROE=16.19% 毛利率=20.94% 净利率=8.82% 负债率=55.49% 净利+39.51% 营收-0.48%
- 2025Q3：EPS=1.01 ROE=12.58% 毛利率=21.52% 净利率=9.27% 负债率=55.57% 净利+43.64% 营收+5.45%

行业：{data['industry']}
主营：{data['business']}
上市：{data['listing_date']}

大盘：上证{data['market_context']['shanghai_index']}点，情绪指数{data['market_context']['emotion_index']}（{data['market_context']['market_phase']}），主线：{data['market_context']['plate_hot']}，炸板率{data['market_context']['bomb_board_rate']}%

请输出以下格式的报告：

## 一、核心数据总览（表格）

## 二、技术面分析
- 均线系统状态与趋势判断
- 量价关系评估
- 52W位置与压力支撑位

## 三、基本面深度分析
- 盈利能力趋势（ROE、毛利率、净利率的季度变化）
- 成长性评估（净利增速的持续性）
- 财务健康度（资产负债率、有息负债、现金流）
- 重点：Q1营收仅+0.75%的原因分析（季节性？毛利率提升但收入停滞？）

## 四、估值分析
- 当前PE/PB/PS的合理性
- 与行业可比公司对比
- 估值修复空间测算

## 五、投资逻辑与催化剂
- 为什么今天+9%？驱动力是什么？
- 未来3-6个月的催化剂
- 与同类公司对比的优势

## 六、风险点
- 按严重程度排列（高/中/低）

## 七、操作建议
- 当前评级（强烈推荐/推荐/持有/谨慎/回避）
- 买入区间、目标价、止损位
- 仓位建议
- 关键观察指标（需要跟踪什么数据）

## 八、综合评分（100分制 + 5星评级）

要求：用中文输出，数据严格基于上述数字，专业有深度，逻辑清晰，操作建议明确不模糊。"""


print("=== 分析报告请求 ===")
print(f"股票: {data['stock']}")
print(f"现价: {data['price']} (+{data['pct_chg']}%)")
print(f"PE: {data['pe_ttm']} | ROE(Q1): {data['financials_quarterly'][0]['roe']}%")
print(f"净利增速: {data['financials_quarterly'][0]['np_yoy']}%")
print(f"\nPrompt length: {len(prompt)} chars")
print("\n数据已就绪，等待模型输出...")