# -*- coding: utf-8 -*-
"""
S级股票深度分析 · DeepSeek AI 分析报告
==========================================

对主板+双创板 S 级股票进行深度分析：
1. 估值分析（PE/PB/PS/PEG）
2. 现价之上的空间（技术面+基本面）
3. 买卖点建议
4. 风险提示（减持、增发、财务等）

运行:
    python s_stock_deep_analysis.py
"""

import os
import json
import time
from datetime import datetime
import requests
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(BASE_DIR, "report_daily")

# 加载环境变量（与 tushare_quant.py 相同）
load_dotenv("d:/mystock/config/.env")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

# ============================================================
# DeepSeek API 调用（与 tushare_quant.py 相同）
# ============================================================
def call_deepseek(prompt, max_tokens=4096):
    """调用 DeepSeek API"""
    if not DEEPSEEK_API_KEY:
        print("[Error] 未配置 DEEPSEEK_API_KEY")
        return None

    url = "https://api.deepseek.com/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }

    data = {
        "model": "deepseek-v4-pro",
        "messages": [
            {"role": "system", "content": "你是一位专业的A股投资分析师，擅长基本面分析、技术分析和风险评估。请基于提供的数据，给出专业、客观、有深度的投资分析报告。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "max_tokens": max_tokens
    }

    try:
        resp = requests.post(url, headers=headers, json=data, timeout=120)
        resp.raise_for_status()
        result = resp.json()
        return result["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"[Error] DeepSeek API 调用失败: {e}")
        return None


# ============================================================
# 构建分析 Prompt
# ============================================================
def build_analysis_prompt(stock):
    """构建深度分析 Prompt"""
    name = stock.get("name", "")
    code = stock.get("ts_code", "")
    theme = stock.get("theme", "")
    mcap = stock.get("market_cap_yi", 0)
    amount = stock.get("avg_amount_20d_yi", 0)

    # 评分
    recognition = stock.get("recognition_score", 0)
    second_wave = stock.get("second_wave_score", 0)
    industry_s = stock.get("industry_strength", 0)
    earnings_s = stock.get("earnings_strength", 0)
    institution_s = stock.get("institution_strength", 0)

    # K线
    ret_5 = stock.get("ret_5", 0)
    ret_20 = stock.get("ret_20", 0)
    ret_60 = stock.get("ret_60", 0)
    ret_120 = stock.get("ret_120", 0)
    bias_ma20 = stock.get("bias_ma20", 0)
    bias_ma60 = stock.get("bias_ma60", 0)
    bull_score = stock.get("bull_score", 0)
    volatility = stock.get("volatility_60d", 0)
    max_dd = stock.get("max_drawdown_60d", 0)

    # 其他
    zt_count = stock.get("limit_up_count_120d", 0)
    hot_days = stock.get("hot_days_30", 0)
    stage = stock.get("stage", "")
    core_reason = stock.get("core_reason", "")
    risk_factor = stock.get("risk_factor", "")

    prompt = f"""
请对以下A股S级中军龙头股票进行深度投资分析：

【基本信息】
- 股票名称: {name}
- 股票代码: {code}
- 所属主题: {theme}
- 当前市值: {mcap:.0f} 亿元
- 20日均成交额: {amount:.1f} 亿元
- 当前阶段: {stage}

【量化评分】
- 辨识度评分: {recognition:.1f}/100（市场记忆度）
- 二波潜力评分: {second_wave:.1f}/100（主升浪潜力）
- 产业强度: {industry_s:.1f}/100（主题持续性强弱）
- 业绩兑现: {earnings_s:.1f}/100（业绩增长确定性）
- 机构强化: {institution_s:.1f}/100（机构资金参与度）

【K线特征】
- 5日涨幅: {ret_5:+.1f}%
- 20日涨幅: {ret_20:+.1f}%
- 60日涨幅: {ret_60:+.1f}%
- 120日涨幅: {ret_120:+.1f}%
- MA20乖离率: {bias_ma20:+.1f}%
- MA60乖离率: {bias_ma60:+.1f}%
- 均线多头排列: {bull_score}/4
- 60日波动率: {volatility:.2f}%
- 60日最大回撤: {max_dd:.1f}%
- 120日涨停次数: {zt_count}次
- 近30日热榜天数: {hot_days}天

【核心逻辑】
{core_reason}

【已知风险】
{risk_factor}

========================================
请从以下维度进行深度分析：

一、估值分析
1. 根据市值、行业属性、成长性，判断当前估值水平（低估/合理/高估）
2. 参考同行业可比公司，给出合理的PE/PB区间
3. 若有业绩预告或快报数据，请纳入考量

二、上涨空间分析
1. 基于技术面（均线结构、趋势斜率、成交量），判断短期/中期上涨空间
2. 基于基本面（行业景气度、业绩增长预期），判断长期上涨空间
3. 给出现价之上的合理目标价位区间（保守/中性/乐观）

三、买卖点建议
1. 当前是否适合买入？若适合，建议的买入价位区间
2. 若已持有，建议的加仓/减仓时机
3. 止盈位和止损位建议
4. 持仓周期建议（短线/中线/长线）

四、风险提示
1. 财务风险：是否存在减持、增发、质押、诉讼等风险
2. 行业风险：行业景气度变化、政策风险
3. 技术风险：短期乖离过大、量价背离、趋势破坏等
4. 其他需要关注的风险点

五、投资建议总结
- 综合评级（强烈推荐/推荐/观望/回避）
- 核心投资逻辑（一句话）
- 关键观察指标

请给出专业、客观、有深度的分析报告。
"""
    return prompt


# ============================================================
# 主流程
# ============================================================
def main():
    print("=" * 70, flush=True)
    print("S级股票深度分析 · DeepSeek AI 分析报告", flush=True)
    print("=" * 70, flush=True)

    # 1. 读取 S 级股票数据
    json_path = os.path.join(REPORT_DIR, "mainboard_second_wave.json")
    if not os.path.exists(json_path):
        print(f"[Error] 找不到数据文件: {json_path}", flush=True)
        return

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    all_stocks = data.get("data", [])
    s_stocks = [s for s in all_stocks if s.get("rating") == "S"]
    print(f"[Info] 共 {len(s_stocks)} 只 S 级股票待分析\n", flush=True)

    if not s_stocks:
        print("[Warn] 没有 S 级股票", flush=True)
        return

    # 2. 逐只分析
    results = []
    for i, stock in enumerate(s_stocks, 1):
        name = stock.get("name", "")
        code = stock.get("ts_code", "")
        print(f"\n[{i}/{len(s_stocks)}] 分析 {name}({code})...", flush=True)

        # 构建 Prompt
        prompt = build_analysis_prompt(stock)

        # 调用 DeepSeek
        analysis = call_deepseek(prompt)

        if analysis:
            result = {
                "ts_code": code,
                "name": name,
                "theme": stock.get("theme", ""),
                "market_cap_yi": stock.get("market_cap_yi", 0),
                "second_wave_score": stock.get("second_wave_score", 0),
                "stage": stock.get("stage", ""),
                "ai_analysis": analysis,
            }
            results.append(result)
            print(f"  ✓ 分析完成", flush=True)
        else:
            print(f"  ✗ 分析失败", flush=True)

        # 避免 API 限流
        if i < len(s_stocks):
            time.sleep(2)

    # 3. 保存结果
    if results:
        output = {
            "analysis_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_count": len(results),
            "data": results
        }

        # JSON
        output_json_path = os.path.join(REPORT_DIR, "s_stock_deep_analysis.json")
        with open(output_json_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"\n[Save] 已保存 JSON: {output_json_path}")

        # Markdown 报告
        md_path = os.path.join(REPORT_DIR, "s_stock_deep_analysis.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"# S级股票深度分析报告\n\n")
            f.write(f"**分析时间**: {output['analysis_time']}\n\n")
            f.write(f"**分析数量**: {len(results)} 只\n\n")
            f.write("---\n\n")

            for i, r in enumerate(results, 1):
                f.write(f"## {i}. {r['name']}({r['ts_code']})\n\n")
                f.write(f"- **主题**: {r['theme']}\n")
                f.write(f"- **市值**: {r['market_cap_yi']:.0f} 亿\n")
                f.write(f"- **二波评分**: {r['second_wave_score']:.0f}\n")
                f.write(f"- **阶段**: {r['stage']}\n\n")
                f.write("### AI 深度分析\n\n")
                f.write(r['ai_analysis'])
                f.write("\n\n---\n\n")

        print(f"[Save] 已保存 Markdown: {md_path}")

        # 控制台输出摘要
        print("\n" + "=" * 70)
        print("【分析完成】")
        print("=" * 70)
        for i, r in enumerate(results[:5], 1):
            print(f"\n{i}. {r['name']}({r['ts_code']}) | {r['theme']} | 二波{r['second_wave_score']:.0f}")
            # 提取投资建议总结部分
            analysis = r['ai_analysis']
            if "投资建议总结" in analysis:
                idx = analysis.find("投资建议总结")
                summary = analysis[idx:idx+500]
                print(f"   {summary[:200]}...")

    print("\n[Done] 分析完成")


if __name__ == "__main__":
    main()
