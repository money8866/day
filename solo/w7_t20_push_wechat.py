# -*- coding: utf-8 -*-
"""
W7 T20 右尾引擎 · 微信推送
=========================================
1. 读取最新 w7_t20_right_tail_YYYYMMDD.md 报告
2. 提取【TOP_PICK】与【PRIMARY_BUY】(按报告 SPACE 空间优选分顺序, 不做二次重排)
3. 调 DeepSeek 精炼为可执行操作指令 → PushPlus 推送到微信

用法:
  python w7_t20_push_wechat.py                # 自动找最新报告
  python w7_t20_push_wechat.py --date 20260907
"""
import os, sys, re, glob
from datetime import datetime
from dotenv import load_dotenv
import requests

sys_path = os.path.dirname(os.path.abspath(__file__))
if sys_path not in sys.path:
    sys.path.insert(0, sys_path)
load_dotenv('d:/mystock/config/.env')

REPORT_DIR = os.path.join(sys_path, 'report_daily')
PUSHPLUS_TOKEN = os.getenv('PUSHPLUS')
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
DEEPSEEK_URL = 'https://api.deepseek.com/chat/completions'
MAX_REPORT_CHARS = 16000


def call_deepseek(prompt, system):
    if not DEEPSEEK_API_KEY:
        print('未配置 DEEPSEEK_API_KEY，跳过 AI 精炼')
        return ''
    try:
        resp = requests.post(DEEPSEEK_URL, headers={
            'Authorization': f'Bearer {DEEPSEEK_API_KEY}',
            'Content-Type': 'application/json',
        }, json={
            'model': 'deepseek-v4-flash',
            'messages': [
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': prompt},
            ],
            'temperature': 0.3,
        }, timeout=120)
        resp.raise_for_status()
        return resp.json()['choices'][0]['message']['content'].strip()
    except Exception as e:
        print(f'DeepSeek 调用失败: {e}')
        return ''


def find_report(date_str=None):
    files = glob.glob(os.path.join(REPORT_DIR, 'w7_t20_right_tail_*.md'))
    dated = []
    for f in files:
        m = re.search(r'w7_t20_right_tail_(\d{8})\.md', os.path.basename(f))
        if m:
            dated.append((m.group(1), f))
    if not dated:
        return None, None
    if date_str:
        p = os.path.join(REPORT_DIR, f'w7_t20_right_tail_{date_str}.md')
        return (date_str, p) if os.path.exists(p) else (None, None)
    dated.sort(reverse=True)
    return dated[0]


def parse_sections(md_text):
    """提取【TOP_PICK】全文段 + 【PRIMARY_BUY】每只(###标题 + bullets)。"""
    lines = md_text.splitlines()
    top_rows = []          # TOP_PICK 表格行(原样, 含表头)
    in_top = False
    pb_items = []          # (标题行, [bullet 列表])
    cur = None
    for s in lines:
        st = s.strip()
        if st.startswith('## 【TOP_PICK】'):
            in_top = True
            continue
        if in_top:
            if st.startswith('## '):
                in_top = False
            elif st.startswith('|'):
                top_rows.append(st)
            elif st.startswith('无') and not pb_items:
                top_rows = ['（无达标标的）']
            continue
        if st.startswith('## 【PRIMARY_BUY】'):
            continue
        if st.startswith('### '):
            cur = (st[4:].strip(), [])
            pb_items.append(cur)
            continue
        if cur is not None and st.startswith('- '):
            cur[1].append(st[2:].strip())
    return top_rows, pb_items


SYSTEM_PROMPT = (
    '你是A股短线交易执行助理。严格基于用户提供的量化报告输出，禁止编造数据/价格/消息面；'
    '股票代码与名称必须引用报告原文。'
    '\n\n规则：'
    '\n- 每只标的都包含真实关键位：突破价/回踩区[低,高]/失效位/MA20/ATR，只能引用报告数值，保留两位小数，禁止计算改动；'
    '\n- T20右尾分/结构分/Retest/IGE_ADJ 是0-100评分，不是股价，禁止当价格输出；'
    '\n- 执行规则：这些标的均已满足 PRIMARY_BUY（BUY 动作），统一条件=次日开盘≥回踩区上沿且不破失效位可执行；'
    '收盘跌破失效位=放弃/离场；回踩区缩量企稳是最佳低吸点；'
    '\n- 现价已在回踩区上方且贴近者，禁止写"等突破"，正确写"回踩区上沿上方运行，回踩企稳或放量不破失效位即可持有"；'
    '\n\n输出要求：'
    '\n1. 只输出一段精炼操作指令（Markdown，≤1000字）；'
    '\n2. 结构固定：【今日结论】一句话（家数=PRIMARY_BUY标的数，无达标标的时写"今日零买入动作"）→'
    '【可操作标的】按给定顺序从1连续编号逐只单列一行，格式：'
    '"序号. 代码 名称｜状态：Lifecycle｜突破X.XX 回踩[X.XX,X.XX] 失效X.XX MA20 X.XX｜动作：开盘≥回踩区上沿且不破失效位执行，破失效位放弃"'
    '→【风险/纪律】一句话；'
    '\n3. 严禁模糊词（关注/观望/择机），全部换成明确动作与明确价格位；'
    '\n4. 仅当报告 PRIMARY_BUY 为空时才写"今日零买入动作"。'
)


def push_to_wechat(msg, title):
    if not PUSHPLUS_TOKEN:
        print('错误: 未设置 PUSHPLUS 环境变量')
        return False
    content = msg if len(msg) <= 9000 else msg[:8900] + '\n>（内容超长已截断）'
    try:
        resp = requests.post('https://www.pushplus.plus/send', json={
            'token': PUSHPLUS_TOKEN, 'title': title,
            'content': content, 'template': 'markdown',
        }, timeout=30)
        result = resp.json()
        if result.get('code') == 200:
            print(f'推送成功: {result.get("msg", "")}')
            return True
        print(f'推送失败: code={result.get("code")} msg={result.get("msg")} data={result.get("data", "")}')
        return False
    except Exception as e:
        print(f'推送异常: {e}')
        return False


def main():
    date_str = None
    if len(sys.argv) >= 3 and sys.argv[1] == '--date':
        date_str = sys.argv[2]
    trade_date, report_path = find_report(date_str)
    if not report_path:
        print('未找到 T20 报告，请先运行 w7_t20_right_tail_engine.py')
        return
    with open(report_path, 'r', encoding='utf-8') as f:
        md_text = f.read()
    top_rows, pb_items = parse_sections(md_text)
    print(f'读取报告: {os.path.basename(report_path)} TOP_PICK段={len(top_rows)}行 PRIMARY_BUY={len(pb_items)}只')

    # 组装喂给 DeepSeek 的精炼源（PRIMARY_BUY 逐只要点，按报告原序=SPACE空间优选序）
    src = ['## PRIMARY_BUY（SPACE 空间优选降序）']
    for title, bullets in pb_items:
        src.append(f'### {title}')
        for b in bullets:
            b = b.replace('**', '')
            src.append('- ' + b)
    if top_rows and top_rows[0] != '（无达标标的）':
        src.insert(0, '## TOP_PICK 表格（如有则最先执行）')
        src[1:1] = top_rows
    prompt = '\n'.join(src)[:MAX_REPORT_CHARS]
    if not pb_items and (not top_rows or top_rows == ['（无达标标的）']):
        ai_text = '今日零买入动作：无 PRIMARY_BUY / TOP_PICK 达标标的。'
    else:
        ai_text = call_deepseek(prompt, SYSTEM_PROMPT)
        if not ai_text:
            fallback = ['今日结论：PRIMARY_BUY {n} 只，见下。', '']
            for n, (title, bullets) in enumerate(pb_items, 1):
                kb = '；'.join(b.replace('**', '') for b in bullets if b.startswith('关键位'))
                act = next((b.replace('**', '').replace('明日动作：', '') for b in bullets if '明日动作' in b), 'BUY')
                fallback.append(f'{n}. {title}｜{kb}｜动作：{act}')
            ai_text = '\n'.join(fallback).replace('{n}', str(len(pb_items)))

    msg = [f'# W7 T20 右尾 · {trade_date} PRIMARY_BUY 操作指令', '',
           ai_text, '', '---',
           f'*W7 T20 Right-Tail · SPACE空间优选排序 · {datetime.now().strftime("%Y-%m-%d %H:%M")} 自动推送*']
    msg = '\n'.join(msg)
    save_path = os.path.join(REPORT_DIR, f'w7_t20_指令_{trade_date}.md')
    with open(save_path, 'w', encoding='utf-8') as f:
        f.write(msg)
    print(f'指令已保存: {save_path}')
    success = push_to_wechat(msg, title=f'W7 T20 右尾操作指令 {trade_date}')
    print('微信推送完成' if success else '微信推送失败')


if __name__ == '__main__':
    main()
