# -*- coding: utf-8 -*-
"""
W7 二波引擎 · 每日微信推送
=========================================
1. 读取最新 w7_second_wave_YYYYMMDD.md 报告
2. 调 DeepSeek 把报告精炼为可直接执行的操作指令（一段文字）
3. 通过 PushPlus 推送到微信

用法:
  python w7_push_wechat.py                # 自动找最新报告
  python w7_push_wechat.py --date 20260828 # 指定日期
"""
import os, sys, re, glob
from datetime import datetime
from dotenv import load_dotenv
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv('d:/mystock/config/.env')

REPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'report_daily')
PUSHPLUS_TOKEN = os.getenv('PUSHPLUS')
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
DEEPSEEK_URL = 'https://api.deepseek.com/chat/completions'

MAX_REPORT_CHARS = 6000  # 喂给 DeepSeek 的报告截断长度（保留精华段）


def call_deepseek(prompt: str, system: str) -> str:
    """调用 DeepSeek 精炼报告（严格基于数据，禁止编造）"""
    if not DEEPSEEK_API_KEY:
        print('⚠️ 未配置 DEEPSEEK_API_KEY，跳过 AI 精炼')
        return ''
    try:
        data = {
            'model': 'deepseek-v4-flash',
            'messages': [
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': prompt},
            ],
            'temperature': 0.3,
        }
        resp = requests.post(DEEPSEEK_URL, headers={
            'Authorization': f'Bearer {DEEPSEEK_API_KEY}',
            'Content-Type': 'application/json',
        }, json=data, timeout=120)
        resp.raise_for_status()
        return resp.json()['choices'][0]['message']['content'].strip()
    except Exception as e:
        print(f'⚠️ DeepSeek 调用失败: {e}')
        return ''


def find_latest_report(date_str=None) -> str:
    """找最新的 w7_second_wave_YYYYMMDD.md 报告（仅匹配 8 位日期格式）"""
    files = glob.glob(os.path.join(REPORT_DIR, 'w7_second_wave_*.md'))
    dated = []
    for f in files:
        m = re.search(r'w7_second_wave_(\d{8})\.md', os.path.basename(f))
        if m:
            dated.append((m.group(1), f))
    if not dated:
        return None
    dated.sort(reverse=True)  # 按日期字符串降序
    if date_str:
        target = os.path.join(REPORT_DIR, f'w7_second_wave_{date_str}.md')
        return target if os.path.exists(target) else None
    return dated[0][1]


def summarize_for_ai(md_text: str) -> str:
    """截取报告精华段喂给 DeepSeek（跳过 WATCH 长尾）"""
    if len(md_text) <= MAX_REPORT_CHARS:
        return md_text
    # 保留开头（PRIMARY_BUY/T120_ROCKET/CONFIRMED 通常在前面），并裁剪 WATCH 段
    head = md_text[:MAX_REPORT_CHARS]
    # 若截断点在 WATCH 段中间，补充一句说明
    return head + '\n\n>（报告过长，WATCH 长尾已截断，以上为精华段）'


SYSTEM_PROMPT = (
    '你是A股短线交易执行助理。严格基于用户提供的量化报告数据输出，'
    '禁止编造任何数据、价格、新闻或消息面；股票名称与代码必须严格引用报告原文。'
    '\n\n重要规则：'
    '\n- 报告中所有数字（T120/ENTRY/HVT/Acceptance/HVT_SIM/分数等）均为0-100的评分，'
    '**不是股价**，绝对禁止把它们当作触发价/止损价/买入价输出；'
    '\n- 报告未提供具体股价时，触发条件只能用形态描述（如"放量突破平台""PP10成立""缩量回踩XX不破"），'
    '禁止写出任何具体价格数字；'
    '\n- 若某标的无可执行买点，明确写"今日不买"，不得编造价位。'
    '\n\n输出要求：'
    '\n1. 只输出一段精炼的操作指令文字（Markdown，含小标题但总长≤600字），可直接照着执行；'
    '\n2. 结构固定：【今日结论】一句话 → 【可操作标的】逐只给出"状态+触发条件"'
    '（触发条件用形态描述，不用价格）→【等待标的】简列 →【风险/纪律】一句话；'
    '\n3. 未满足买点条件（如PP10未成立、未突破）的标的必须写成"等XX触发再买"，'
    '不得写成可立即买入；涨停/巨量当日不追；'
    '\n4. 无信号时明确写"今日零买入动作"；'
    '\n5. 严禁模糊词（关注/观望/择机），全部换成明确动作或触发形态。'
)


def push_to_wechat(msg: str, title: str) -> bool:
    """PushPlus 推送（markdown），超长自动降级"""
    if not PUSHPLUS_TOKEN:
        print('错误: 未设置 PUSHPLUS 环境变量')
        return False
    url = 'https://www.pushplus.plus/send'
    try:
        resp = requests.post(url, json={
            'token': PUSHPLUS_TOKEN,
            'title': title,
            'content': msg,
            'template': 'markdown',
        }, timeout=30)
        result = resp.json()
        if result.get('code') == 200:
            print(f'✅ 推送成功: {result.get("msg", "")}')
            return True
        print(f'⚠️ 推送失败: code={result.get("code")} msg={result.get("msg")} data={result.get("data","")}')
        return False
    except Exception as e:
        print(f'⚠️ 推送异常: {e}')
        return False


def main():
    date_str = None
    if len(sys.argv) >= 3 and sys.argv[1] == '--date':
        date_str = sys.argv[2]
    report_path = find_latest_report(date_str)
    if not report_path:
        print('未找到 w7 报告，请先运行 w7_second_wave_engine.py')
        return

    match = re.search(r'w7_second_wave_(\d{8})\.md', os.path.basename(report_path))
    trade_date = match.group(1) if match else datetime.now().strftime('%Y%m%d')
    print(f'读取报告: {os.path.basename(report_path)}')

    with open(report_path, 'r', encoding='utf-8') as f:
        md_text = f.read()

    # 1. DeepSeek 精炼
    ai_text = call_deepseek(summarize_for_ai(md_text), SYSTEM_PROMPT)
    if not ai_text:
        # DeepSeek 失败则退回简单摘要（前1200字符）
        ai_text = md_text[:1200]

    # 2. 组装推送内容（头部简表 + AI 指令）
    header = []
    header.append(f'# W7 二波引擎 · {trade_date} 操作指令')
    header.append('')
    header.append(ai_text)
    header.append('')
    header.append('---')
    header.append(f'*W7 Second Wave V4.2 · {datetime.now().strftime("%Y-%m-%d %H:%M")} 自动推送*')
    msg = '\n'.join(header)

    # 3. 存档
    os.makedirs(REPORT_DIR, exist_ok=True)
    save_path = os.path.join(REPORT_DIR, f'w7_指令_{trade_date}.md')
    with open(save_path, 'w', encoding='utf-8') as f:
        f.write(msg)
    print(f'✅ 指令已保存: {save_path}')

    # 4. 推送微信
    success = push_to_wechat(msg, title=f'W7 二波引擎操作指令 {trade_date}')
    if success:
        print(f'微信推送完成: {trade_date}')
    else:
        print('微信推送失败')


if __name__ == '__main__':
    main()
