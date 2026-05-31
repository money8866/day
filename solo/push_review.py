# -*- coding: utf-8 -*-
"""
每日复盘总结和微信推送模块
使用 DeepSeek 总结复盘内容，通过 Server酱 发送到微信
"""
import os
import sys
import requests
from datetime import datetime
from dotenv import load_dotenv

# 加载环境变量
DOTENV_PATH = "d:/mystock/config/.env"
load_dotenv(DOTENV_PATH)

DEEPSEEK_KEY = os.getenv('DEEPSEEK_API_KEY')
SERVERCHAN_KEY = os.getenv('WECHAT_SCKEY')

def summarize_with_deepseek(report_text, trade_date):
    """使用 DeepSeek 总结复盘报告"""
    
    prompt = f"""你是一位专业的A股量化分析师。请将以下每日复盘报告进行精简总结，突出重点，便于手机端阅读。

交易日期: {trade_date}

{report_text}

请严格按照以下格式输出总结（必须包含全部7个章节，控制在2500字以内）：

## 🌡️ 大盘情绪分析
直接列出报告中的：
- 情绪指数和市场阶段（必须精确数值，如"情绪指数: 22.9，市场阶段: 冰点"）
- 上涨占比、强势股占比、炸板率（必须精确数字）
- 仓位建议

## 📊 今日主题排名 TOP 5
从【今日主题完整排名】中提取评分最高的前5个主题，格式：
1. 【主题名】：评分XX，排名变化±X位，趋势（上升/下降）
2. ...
5. ...

## 🏆 近10日平均分 TOP 5主题
从【近20日平均分TOP 5主题】中提取，格式：
1. 【主题名】：近20日平均分XX，近10日平均分XX，今日评分XX
2. ...
5. ...

## 🚀 策略一：强者恒强（追涨）
从报告中找到策略一的所有推荐股票，格式：
- **股票名称**：评分XX，二波概率XX%，关键指标

## 📉 策略二：低吸潜伏（抄底）
从报告中找到策略二的所有推荐股票，格式：
- **股票名称**：评分XX，回升概率XX%，关键指标

## 🎯 策略三：长线价值策略（中长线布局）
从报告中找到策略三的所有推荐股票，格式：
- **股票名称**【所属主题】：评分XX，二波概率XX%，均线多头信息

## 💎 主线中长线潜力股分析
从【主线中长线潜力股分析】中提取候选股票，格式：
- **股票名称**（所属主题）：综合评分XX，5日涨幅XX%，风险等级，二波信号
重点关注趋势稳定且评分高的标的。

## ⚡ 短线潜力跟踪
从【短线潜力跟踪】中提取候选股票，格式：
- **股票名称**（所属主题）：综合评分XX，5日涨幅XX%，形态（W底或揉搓线）

## 🏆 龙头股点评
从【今日龙头评分与轮动分析】中提取龙头股票信息：
- **全市场TOP 5龙头**：列出评分最高的前5只龙头股，格式：
  - **股票名称**（所属主题）：综合评分XX，5日涨幅XX%，20日涨幅XX%，二波信号

重要要求：
1. 必须包含上述全部8个章节，缺一不可
2. 所有数字必须从报告中准确提取，不要编造
3. 简洁精炼，每项不超过2行
4. 用中文输出
5. 不要编造任何数据，如果报告中没有相关信息，写"暂无数据"或"无法提取"
"""

    headers = {
        'Authorization': f'Bearer {DEEPSEEK_KEY}',
        'Content-Type': 'application/json'
    }
    
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "你是一位专业的A股量化分析师，擅长用简洁精准的语言总结复盘报告。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "max_tokens": 2000
    }
    
    try:
        resp = requests.post('https://api.deepseek.com/v1/chat/completions', 
                            headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        result = resp.json()
        summary = result['choices'][0]['message']['content']
        return summary
    except Exception as e:
        print(f"DeepSeek 总结失败: {e}")
        return None

def send_to_wechat(title, content, key=None):
    """通过 Server酱 发送到微信"""
    
    if key is None:
        key = SERVERCHAN_KEY
    
    if not key:
        print("未配置 Server酱 KEY，跳过微信推送")
        return False
    
    url = f"https://sctapi.ftqq.com/{key}.send"
    
    data = {
        "title": title,
        "desp": content
    }
    
    try:
        resp = requests.post(url, data=data, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        if result.get('code') == 0 or result.get('errno') == 0:
            print(f"✓ 微信推送成功: {title}")
            return True
        else:
            print(f"✗ 微信推送失败: {result}")
            return False
    except Exception as e:
        print(f"✗ 微信推送异常: {e}")
        return False

def push_daily_review(report_file, trade_date, recession_risk_report=None):
    """完整的推送流程：读取报告 → DeepSeek总结 → 微信推送"""
    
    print("="*60)
    print("📤 开始推送每日复盘")
    print("="*60)
    
    # 1. 读取复盘报告
    if not os.path.exists(report_file):
        print(f"✗ 复盘报告不存在: {report_file}")
        return False
    
    with open(report_file, 'r', encoding='utf-8') as f:
        report_text = f.read()
    
    print(f"✓ 已读取复盘报告: {len(report_text)} 字符")
    
    # 2. DeepSeek 总结
    print("\n🤖 正在使用 DeepSeek 总结...")
    summary = summarize_with_deepseek(report_text, trade_date)
    
    if summary:
        print(f"✓ DeepSeek 总结完成: {len(summary)} 字符")
    else:
        summary = report_text[:1500]
        print("⚠ DeepSeek 总结失败，使用原始报告前1500字符")
    
    # 3. 添加退潮风险分析
    final_content = summary
    if recession_risk_report:
        risk_section = "\n\n## 🌊 主线退潮风险检测\n\n"
        risk_section += f"**风险等级**: {recession_risk_report['risk_level']}\n"
        risk_section += f"**风险得分**: {recession_risk_report['risk_score']}/100\n\n"
        
        if recession_risk_report.get('signals') and len(recession_risk_report['signals']) > 0:
            risk_section += "**风险信号**:\n"
            for signal in recession_risk_report['signals']:
                risk_section += f"- {signal}\n"
            risk_section += "\n"
        
        risk_section += f"{recession_risk_report.get('analysis_summary', '')}\n"
        final_content = summary + risk_section
    
    print("\n" + "="*60)
    print("📋 总结内容预览:")
    print("="*60)
    print(final_content)
    
    # 4. 微信推送
    print("\n📱 正在推送至微信...")
    title = f"📊 每日复盘 {trade_date}"
    
    # 如果有风险警报，在标题中突出显示
    if recession_risk_report:
        risk_level = recession_risk_report['risk_level']
        if risk_level in ['高风险', '严重风险']:
            title = f"🚨 每日复盘 {trade_date} - {risk_level}!"
        elif risk_level in ['中等风险']:
            title = f"⚠️ 每日复盘 {trade_date} - {risk_level}"
    
    success = send_to_wechat(title, final_content)
    
    if success:
        print("\n✅ 推送完成！请查收微信通知")
    else:
        print("\n⚠ 推送失败，请检查 Server酱 配置")
    
    return success

if __name__ == "__main__":
    # 测试推送
    report_file = r"d:\mystock\solo\cache_backbone_tushare\daily_review_20260527.txt"
    trade_date = "20260527"
    
    if len(sys.argv) >= 2:
        report_file = sys.argv[1]
    if len(sys.argv) >= 3:
        trade_date = sys.argv[2]
    
    push_daily_review(report_file, trade_date)
