# -*- coding: utf-8 -*-
"""ETF Alpha 报告生成与推送：DeepSeek AI 生成自然语言报告 → PushPlus 推送到微信"""
import json
import os
import sys
import time
from datetime import datetime

# 复用 tushare_quant 的 deepseek 和 send_pushplus
sys.path.insert(0, r'd:\mystock\solo')
from tushare_quant import deepseek, send_pushplus

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__)) + r'\output'
REPORT_DIR = os.path.dirname(os.path.abspath(__file__)) + r'\report'


def load_results(json_path):
    """加载 ETF Alpha 结果"""
    with open(json_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def build_data_summary(results):
    """构建结构化数据摘要，供 DeepSeek 分析"""
    # 按 ETF Alpha 排序
    sorted_results = sorted(results, key=lambda x: x['etf_alpha'], reverse=True)

    lines = []
    lines.append("## 市场环境")
    market = results[0]
    lines.append(f"- 市场状态评分: {market['market_score']:.1f}/100 ({market['market_state']})")
    lines.append(f"- 交易日期: {market['trade_date']}")
    lines.append("")

    # Top 10
    lines.append("## TOP 10 ETF Alpha 排名")
    lines.append("| 排名 | ETF代码 | 主题 | ETF Alpha | 主题分 | 阶段 | 趋势天数 | 龙头 | 龙头分 | 预期收益 | 风险分 | 建议仓位 | 操作 |")
    lines.append("|------|---------|------|-----------|--------|------|----------|------|--------|----------|--------|----------|------|")
    for i, r in enumerate(sorted_results[:10], 1):
        action = "买入" if r['buy'] else ("持有" if r['hold'] else ("卖出" if r['sell'] else "观望"))
        lines.append(f"| {i} | {r['etf_code']} | {r['theme']} | {r['etf_alpha']:.1f} | {r['theme_score']:.1f} | {r['lifecycle']} | {r['trend_duration']}天 | {r['leader']} | {r['leader_score']:.1f} | {r['expected_return']*100:.1f}% | {r['risk_score']:.1f} | {r['suggested_position']*100:.0f}% | {action} |")
    lines.append("")

    # 操作信号汇总
    buy_list = [r for r in sorted_results if r['buy']]
    hold_list = [r for r in sorted_results if r['hold']]
    sell_list = [r for r in sorted_results if r['sell']]
    lines.append("## 操作信号汇总")
    lines.append(f"- 买入信号: {len(buy_list)} 个")
    if buy_list:
        for r in buy_list:
            lines.append(f"  - {r['etf_code']} {r['theme']} (ETF Alpha={r['etf_alpha']:.1f})")
    lines.append(f"- 持有信号: {len(hold_list)} 个")
    if hold_list:
        for r in hold_list:
            lines.append(f"  - {r['etf_code']} {r['theme']} (ETF Alpha={r['etf_alpha']:.1f})")
    lines.append(f"- 卖出信号: {len(sell_list)} 个")
    if sell_list:
        for r in sell_list:
            lines.append(f"  - {r['etf_code']} {r['theme']} (ETF Alpha={r['etf_alpha']:.1f})")
    lines.append("")

    # 生命周期分布
    from collections import Counter
    lifecycle_dist = Counter(r['lifecycle'] for r in sorted_results)
    lines.append("## 生命周期分布")
    for stage, count in lifecycle_dist.most_common():
        lines.append(f"- {stage}: {count} 个主题")
    lines.append("")

    # 完整排名 (简化版)
    lines.append("## 完整排名 (32只ETF)")
    lines.append("| 排名 | ETF | 主题 | Alpha | 阶段 | 操作 | 关键理由 |")
    lines.append("|------|-----|------|-------|------|------|----------|")
    for i, r in enumerate(sorted_results, 1):
        action = "买" if r['buy'] else ("持" if r['hold'] else ("卖" if r['sell'] else "-"))
        # 取前3个理由
        key_reasons = [x for x in r['reasons'][:3] if not x.startswith('建议仓位')]
        reasons_str = '; '.join(key_reasons) if key_reasons else '-'
        lines.append(f"| {i} | {r['etf_code']} | {r['theme']} | {r['etf_alpha']:.1f} | {r['lifecycle']} | {action} | {reasons_str} |")
    lines.append("")

    return '\n'.join(lines)


def build_prompt(data_summary, results):
    """构建 DeepSeek prompt"""
    today = results[0]['trade_date'] if results else datetime.now().strftime('%Y%m%d')
    # 格式化日期
    date_str = f"{today[:4]}-{today[4:6]}-{today[6:8]}"

    prompt = f"""你是A股顶级机构级ETF量化分析师。请根据以下ETF Alpha Engine的量化结果，生成一份专业的ETF投资分析报告。

**报告日期**: {date_str}
**分析体系**: 6模块量化评分（市场环境25% + 主题Alpha 20% + 生命周期20% + ETF趋势20% + 龙头确认20% + 风险引擎扣分），总权重100%

{data_summary}

## 输出要求

请生成一份结构清晰的Markdown报告，包含以下内容：

### 一、市场环境总览
- 市场状态判断（当前市场评分和状态）
- 对ETF投资的整体影响

### 二、核心推荐（TOP 5深度分析）
对TOP 5 ETF逐一分析：
- 主题Alpha强度及驱动因素
- 生命周期阶段及趋势可持续性
- 龙头股表现
- 风险收益比评估
- 具体操作建议

### 三、风险提示
- 需要警惕的ETF（卖出信号）
- 主要风险因素

### 四、策略建议
- 当前市场环境下最优ETF配置策略
- 仓位管理建议
- 关注要点

**重要：**
- 使用Markdown格式，适合手机阅读
- 语言简洁专业，避免冗长
- 数据引用准确
- 报告总长度控制在1500字以内
- 不要使用表格（手机端阅读体验差），改用列表形式
"""
    return prompt


def main():
    # 查找最新结果文件
    json_files = sorted([f for f in os.listdir(OUTPUT_DIR) if f.endswith('.json')], reverse=True)
    if not json_files:
        print("❌ 未找到ETF Alpha结果文件")
        return

    json_path = os.path.join(OUTPUT_DIR, json_files[0])
    print(f"📊 加载结果: {json_path}")

    results = load_results(json_path)
    print(f"   共 {len(results)} 只ETF")

    # 构建数据摘要
    data_summary = build_data_summary(results)

    # 构建 prompt
    prompt = build_prompt(data_summary, results)
    print(f"📝 Prompt长度: {len(prompt)} 字符")

    # 调用 DeepSeek
    print("🤖 正在调用 DeepSeek AI 生成报告...")
    start = time.time()
    report = deepseek(prompt, use_flash=False)
    elapsed = time.time() - start
    print(f"   DeepSeek 响应耗时: {elapsed:.1f}s")

    if not report:
        print("❌ DeepSeek 返回为空")
        return

    print(f"   报告长度: {len(report)} 字符")

    # 保存报告
    os.makedirs(REPORT_DIR, exist_ok=True)
    today = results[0]['trade_date']
    report_file = os.path.join(REPORT_DIR, f"ETF_Alpha_Report_{today}.md")
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"✅ 报告已保存: {report_file}")

    # 打印报告
    print("\n" + "=" * 60)
    print(report)
    print("=" * 60)

    # 发送到微信 (PushPlus)
    print("\n📤 正在推送到微信...")
    pushplus_token = os.getenv("PUSHPLUS")
    if pushplus_token:
        # 临时修改 send_pushplus 中的 title（使用闭包/内联实现）
        import requests
        import re
        msg = report.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
        url = "https://www.pushplus.plus/send"
        payload = {
            "token": pushplus_token,
            "title": f"ETF Alpha 引擎日报 - {today}",
            "content": msg,
            "template": "markdown"
        }
        try:
            resp = requests.post(url, json=payload, timeout=15)
            result = resp.json()
            if result.get("code") == 200:
                print("✅ PushPlus 已发送到微信")
            else:
                print(f"⚠️ PushPlus 发送失败: {result.get('msg', '未知错误')}")
        except Exception as e:
            print(f"⚠️ PushPlus 请求异常: {e}")
    else:
        print("⚠️ PUSHPLUS 环境变量未设置，跳过微信推送")

    print("\n🎉 完成!")


if __name__ == '__main__':
    main()