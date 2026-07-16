#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ETF Winner Prediction - AI 智能分析报告生成器
===============================================
使用 DeepSeek AI 对 ETF Winner Prediction Engine 的输出结果进行深度分析。
参考 etf_alpha_engine/report_sender.py 的模式实现。
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta
from collections import Counter

sys.path.insert(0, r'd:\mystock\solo')
from tushare_quant import deepseek, send_pushplus

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, 'output')
REPORT_DIR = os.path.join(BASE_DIR, 'report')


def load_results(json_path):
    with open(json_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def find_latest_results():
    json_files = sorted([f for f in os.listdir(OUTPUT_DIR) if f.endswith('.json')], reverse=True)
    if not json_files:
        return None
    return os.path.join(OUTPUT_DIR, json_files[0])


def build_data_summary(results):
    """构建结构化数据摘要供 DeepSeek 分析"""
    lines = []

    # 市场环境
    first = results[0] if results else {}
    trade_date_str = "20260714"
    for fn in os.listdir(OUTPUT_DIR):
        if fn.startswith('etf_winner_') and fn.endswith('.json'):
            trade_date_str = fn.replace('etf_winner_', '').replace('.json', '')
            break
    date_str = f"{trade_date_str[:4]}-{trade_date_str[4:6]}-{trade_date_str[6:8]}"

    lines.append(f"分析日期: {date_str}")
    lines.append("")
    lines.append("=== 市场环境 ===")
    lines.append(f"市场状态: {first.get('Market Regime', 'Neutral')}")
    lines.append(f"市场评分: {first.get('Market Score', 'N/A')}/100")
    lines.append("")

    # 统计
    accepted = [r for r in results if r.get('Decision') == 'ACCEPT']
    rejected = [r for r in results if r.get('Decision') == 'REJECT']
    lines.append(f"总计评估ETF: {len(results)}")
    lines.append(f"通过硬过滤器(ACCEPT): {len(accepted)}")
    lines.append(f"拒绝(REJECT): {len(rejected)}")
    lines.append("")

    # 生命周期分布
    stages = Counter(r.get('Lifecycle', 'Unknown') for r in results)
    lines.append("=== 生命周期分布 ===")
    for stage, cnt in stages.most_common():
        lines.append(f"- {stage}: {cnt}只")
    lines.append("")

    # 主题预测排名TOP
    lines.append("=== 未来主题预测 TOP 3 ===")
    theme_rank_sorted = sorted(results, key=lambda x: x.get('Future Theme Rank', 99))
    top_themes = []
    seen = set()
    for r in theme_rank_sorted:
        th = r.get('Theme', '')
        if th not in seen and r.get('Future Theme Rank', 99) <= 3:
            seen.add(th)
            top_themes.append(r)
    for r in top_themes:
        lines.append(f"- #{r.get('Future Theme Rank')} {r.get('Theme')} "
                     f"(预测分={r.get('Theme Forecast Score')}, "
                     f"剩余趋势={r.get('Remaining Trend Days')}天, "
                     f"轮动概率={r.get('Rotation Probability')})")
    lines.append("")

    # 完整排名表
    lines.append("=== ETF 完整排名 ===")
    lines.append("|排名|ETF代码|主题|生命周期|市场分|主题预测排名|主题分|龙头|龙头分|ETF趋势分|20D预期|40D预期|60D预期|综合预期|Top1概率|Top3概率|预期持仓天|预期回撤|风险分|建议仓位|决策|置信度|拒绝原因|")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        reason_str = r.get('Reasons', '').replace('|', '/')
        lines.append(
            f"|{r.get('Rank')}"
            f"|{r.get('ETF Code')}"
            f"|{r.get('Theme')}"
            f"|{r.get('Lifecycle')}"
            f"|{r.get('Market Score')}"
            f"|{r.get('Future Theme Rank')}"
            f"|{r.get('Theme Forecast Score')}"
            f"|{r.get('Leader', '无')}"
            f"|{r.get('Leader Score')}"
            f"|{r.get('ETF Trend Score')}"
            f"|{r.get('Expected20D')}"
            f"|{r.get('Expected40D')}"
            f"|{r.get('Expected60D')}"
            f"|{r.get('Expected Return')}"
            f"|{r.get('Probability Top1')}"
            f"|{r.get('Probability Top3')}"
            f"|{r.get('Expected Holding Days')}天"
            f"|{r.get('Expected Drawdown')}"
            f"|{r.get('Risk Score')}"
            f"|{r.get('Suggested Position')}"
            f"|{r.get('Decision')}"
            f"|{r.get('Confidence')}"
            f"|{reason_str}|"
        )
    lines.append("")

    # 硬过滤器失败原因统计
    lines.append("=== 硬过滤器失败原因汇总 ===")
    fail_counter = Counter()
    for r in rejected:
        reasons = r.get('Reasons', '')
        if '市场环境不支撑' in reasons:
            fail_counter['市场环境不足(<60)'] += 1
        if '主题预测排名不足' in reasons:
            fail_counter['主题排名不足(>3)'] += 1
        if '剩余趋势天数不足' in reasons:
            fail_counter['剩余趋势天数不足(<20)'] += 1
        if '龙头强度不足' in reasons:
            fail_counter['龙头强度不足(<75)'] += 1
        if '风险过高' in reasons:
            fail_counter['风险过高(>40)'] += 1
        if '预期收益不足' in reasons:
            fail_counter['预期收益不足(<10%)'] += 1
        if 'Top3概率不足' in reasons:
            fail_counter['Top3概率不足(<60%)'] += 1
        if '生命周期拒绝' in reasons:
            fail_counter['生命周期在Peak/Distribution/Decline/Dead'] += 1
    for reason, cnt in fail_counter.most_common():
        lines.append(f"- {reason}: {cnt}只")
    lines.append("")

    return "\n".join(lines), trade_date_str


def build_prompt(data_summary, trade_date_str):
    date_str = f"{trade_date_str[:4]}-{trade_date_str[4:6]}-{trade_date_str[6:8]}"

    prompt = f"""你是A股顶级机构级ETF量化投资组合经理（Chief Quantitative Portfolio Manager）。

你的任务不是推荐今天最强的ETF，而是预测未来20~60个交易日哪只行业ETF最可能产生最高总收益。

请根据以下ETF Winner Prediction Engine（8步流水线：市场环境过滤→主题预测引擎→生命周期预测→龙头引擎→ETF趋势引擎→预期收益模型→预期排名模型→风险引擎，经过7项硬过滤器决策）的量化结果，生成一份专业的投资分析报告。

**报告日期**: {date_str}
**持仓周期**: 20~60个交易日
**持仓上限**: 最多1只ETF，允许持现金
**优化目标**: 预期收益、胜率、夏普比率、最大回撤、趋势持续性

{data_summary}

## 分析要求

请生成一份结构清晰、数据驱动的专业分析报告，使用Markdown格式，包含以下内容：

### 一、市场环境深度判断
- 当前市场状态评分和定性判断（Bull/Recovery/Neutral/Weak/Bear）
- 对ETF投资的影响：仓位建议、风格偏好
- 流动性、情绪、北向资金等关键信号解读
- 给出明确的市场操作等级（满仓/重仓/轻仓/空仓观望）

### 二、主题轮动预测
- 未来20/40/60天最有潜力的主题排序及理由
- 当前处于Expansion/Birth阶段的主题分析（这是可买阶段）
- 处于Decline/Dead的主题为什么应该规避
- 主题轮动节奏判断：哪些主题即将启动？哪些主题已经见顶？
- PCB产业链、创新药、半导体设备等高分主题的持续性判断

### 三、候选ETF深度分析（TOP 5）
对排名前5的ETF逐一分析：
- 为什么排在这个位置？量化数据支撑
- 核心优势和主要短板（距离通过硬过滤器差在哪？）
- 龙头股健康度（有没有真正的龙头？龙头是否持续跑赢行业？）
- 预期收益和风险收益比
- 建议操作（观望/小仓试探/等待信号确认后买入）
- 关注买点触发条件（什么情况下会通过硬过滤器？）

### 四、当前不买的核心理由
- 如果所有ETF都被拒绝，系统性分析原因
- 市场环境不支撑的话，需要什么信号才能入场？
- 龙头强度普遍不足意味着什么？
- 给出等待信号清单

### 五、投资策略建议
- 当前最优策略（潜伏低吸/等待突破/空仓观望等）
- 仓位管理建议
- 关注清单：最值得跟踪的3只ETF及理由
- 下一个关键观察信号

**重要要求：**
1. 语言简洁专业，适合手机阅读，不使用段落缩进
2. 数据引用准确，基于上面的量化数据
3. 观点明确，不要模棱两可
4. 如果没有ACCEPT的ETF，不要强行推荐买入，而是给出等待策略
5. 报告总长度控制在2000字以内
6. 不使用表格（手机阅读体验差），改用列表和分节
7. 注意：Semiconductor/半导体板块的生命周期判断是否准确（目前很多被标为Decline但趋势分很高，这是否矛盾？请分析）
"""
    return prompt


def main():
    json_path = find_latest_results()
    if not json_path:
        print("No result files found. Run main.py first.")
        return

    print(f"Loading results: {json_path}")
    results = load_results(json_path)
    print(f"Total ETFs: {len(results)}")

    data_summary, trade_date_str = build_data_summary(results)

    prompt = build_prompt(data_summary, trade_date_str)
    print(f"Prompt length: {len(prompt)} chars")

    print("Calling DeepSeek AI (Pro model)...")
    start = time.time()
    report = deepseek(prompt, use_flash=False)
    elapsed = time.time() - start
    print(f"DeepSeek response time: {elapsed:.1f}s")

    if not report:
        print("DeepSeek returned empty response")
        return

    print(f"Report length: {len(report)} chars")

    os.makedirs(REPORT_DIR, exist_ok=True)
    report_file = os.path.join(REPORT_DIR, f"AI_Analysis_{trade_date_str}.md")
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"Report saved: {report_file}")

    print("\n" + "=" * 70)
    print(report)
    print("=" * 70)

    pushplus_token = os.getenv("PUSHPLUS")
    if pushplus_token:
        print("\nSending to WeChat via PushPlus...")
        msg = report.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
        url = "https://www.pushplus.plus/send"
        payload = {
            "token": pushplus_token,
            "title": f"ETF Winner Prediction AI分析 - {trade_date_str}",
            "content": msg,
            "template": "markdown"
        }
        try:
            resp = requests.post(url, json=payload, timeout=15)
            result = resp.json()
            if result.get("code") == 200:
                print("PushPlus sent successfully.")
            else:
                print(f"PushPlus failed: {result.get('msg')}")
        except Exception as e:
            print(f"PushPlus error: {e}")
    else:
        print("PUSHPLUS env not set, skipping WeChat push.")

    print("\nDone!")


if __name__ == '__main__':
    main()