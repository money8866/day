# -*- coding: utf-8 -*-
"""HVT-BULL AI 自然语言复盘 + 微信推送

流程：读 report_daily/hvt_bull_report_{date}.md
     → DeepSeek 生成自然语言复盘（移动端友好）
     → Server酱 推送微信
     → 落盘 report_daily/hvt_bull_ai_{date}.md
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

_AI_SYSTEM_PROMPT = """你是资深A股量化操盘手，专注"历史天量+二次突破"牛股模式。请对 HVT-BULL 每日报告做盘后复盘总结。

报告口径速查：
- PRIMARY_BUY：天量日+缩量锁筹+二次突破+RS20≥70，最强买点，仓位建议5~15%
- T20_ROCKET_WATCH / BREAKOUT_READY：右侧扩张观察池，等入场确认
- 突破回踩 GOOD/NEAR：突破后回踩缩量(≤0.8×突破日量)守住T0高点并收复突破收盘，二次买点；GOOD=完全满足
- RIGHT_TAIL：右侧主升持有跟踪（HOLD持有/TRIMMING分批兑现/EXIT止盈离场）
- FAILED/DISTRIBUTION/EXIT：跌破T0_High或回撤过大，风险回避名单
- HVT_STRONG：天量当日强势，等待缩量锁筹再观察
- DIP_REBOUND_WATCH：40~50分超跌反弹观察池（多为FAILED/DISTRIBUTION超跌结构），弱市均值回归观察信号，非买入信号、不进决策链

请输出（200~500字，Markdown 列表，禁止首行缩进，适合手机阅读）：
1. 一句话核心结论：今天 HVT 信号整体偏多还是偏空，机会与风险哪个占优
2. 今日重点信号：列出 PRIMARY_BUY 与 突破回踩 GOOD 标的（必须带完整代码+名称），每只一句话说清逻辑
3. 持有与风险：RIGHT_TAIL 持仓建议动作；FAILED/DISTRIBUTION/EXIT 报总数并点出最需警惕的2~3只
4. 明日操作要点：观察什么、避免什么
5. 超跌反弹观察池：DIP_REBOUND_WATCH 若存在则列出观察标的（代码+名称+落选原因），必须明确标注为"观察信号、非买入建议"

规则：只使用报告中出现的数据，绝不编造；个股必须带完整代码（如 陆家嘴 600663.SH）；不要出现'报告原文'或'根据报告'这类话。"""


def _load_env():
    """加载 d:\\mystock\\config\\.env（缺省回退 solo/.env）"""
    try:
        from dotenv import load_dotenv
        env_path = r'd:\mystock\config\.env'
        if not os.path.exists(env_path):
            env_path = os.path.join(BASE_DIR, '.env')
        if os.path.exists(env_path):
            load_dotenv(env_path)
    except Exception:
        pass


def _report_path(trade_date: str) -> str:
    out_dir = os.path.join(BASE_DIR, 'report_daily')
    return os.path.join(out_dir, f'hvt_bull_report_{trade_date}.md')


def _read_report(trade_date: str) -> str:
    path = _report_path(trade_date)
    if not os.path.exists(path):
        return ''
    with open(path, encoding='utf-8') as f:
        return f.read()


def summarize_with_deepseek(md_text: str, trade_date: str) -> str:
    """DeepSeek 生成自然语言复盘；失败返回空串（由调用方走降级）"""
    import requests
    api_key = os.getenv('DEEPSEEK_API_KEY')
    if not api_key:
        print('[HVT-PUSH] 未配置 DEEPSEEK_API_KEY，跳过AI总结')
        return ''
    try:
        url = 'https://api.deepseek.com/v1/chat/completions'
        headers = {'Content-Type': 'application/json',
                   'Authorization': f'Bearer {api_key}'}
        messages = [
            {'role': 'system', 'content': _AI_SYSTEM_PROMPT},
            {'role': 'user', 'content': f'请复盘 {trade_date} 的 HVT-BULL 报告：\n\n{md_text}'},
        ]
        data = {'model': 'deepseek-chat', 'messages': messages,
                'temperature': 0.3, 'max_tokens': 1024}
        resp = requests.post(url, headers=headers, json=data, timeout=60)
        resp.raise_for_status()
        result = resp.json()
        text = result['choices'][0]['message']['content'].strip()
        return text[:3000]
    except Exception as e:
        print(f'[HVT-PUSH] AI总结失败: {e}')
        return ''


def send_to_wechat(text: str, trade_date: str) -> bool:
    """Server酱 推送微信（SendKey 或旧 SCKEY 兼容）"""
    import requests
    send_key = os.getenv('SERVERCHAN_SENDKEY', os.getenv('WECHAT_SCKEY'))
    if not send_key:
        print('[HVT-PUSH] 未配置 Server酱 SendKey，跳过微信推送')
        return False
    try:
        url = f'https://sctapi.ftqq.com/{send_key}.send'
        title = f'{trade_date} HVT-BULL 天量牛股复盘'
        resp = requests.post(url, data={'title': title, 'desp': text}, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        if result.get('code') == 0:
            print('[HVT-PUSH] 微信推送成功')
            return True
        print(f"[HVT-PUSH] 微信推送失败: {result.get('message', '未知错误')}")
        return False
    except Exception as e:
        print(f'[HVT-PUSH] 微信推送失败: {e}')
        return False


def _fallback_summary(md_text: str, trade_date: str) -> str:
    """AI 不可用时的降级摘要：头部统计 + A/E/F 关键区段 + 风险计数 + 超跌反弹观察池"""
    import re
    lines = md_text.splitlines()
    head = []
    sec = {'A': [], 'E': [], 'F': [], 'D': [], 'W': []}
    cur = None
    for ln in lines:
        if ln.startswith('# HVT-BULL') or ln.startswith('日期') or ln.startswith('股票池') \
           or ln.startswith('引擎') or ln.startswith('状态分布'):
            head.append(ln)
            continue
        if ln.startswith('## A.') or ln.startswith('## D.') \
           or ln.startswith('## E.') or ln.startswith('## F.'):
            cur = ln[3]
            sec[cur] = [ln]
            continue
        if ln.startswith('DIP_REBOUND_WATCH'):
            cur = 'W'
            sec[cur] = [ln]
            continue
        if ln.startswith('## '):
            cur = None
            continue
        if cur in sec and (ln.startswith('|') or ln.strip() == ''
                           or (cur == 'W' and ln.startswith('定位：'))):
            if ln.strip():
                sec[cur].append(ln)
    # 风险计数
    d_rows = [r for r in sec.get('D', []) if r.startswith('|') and not r.startswith('|---')]
    out = [f'# {trade_date} HVT-BULL 复盘（AI降级版）', '']
    out.extend(head)
    for k in ('A', 'E', 'F'):
        rows = [r for r in sec.get(k, []) if r.startswith('|') and not r.startswith('|---')]
        if rows:
            out += ['', sec[k][0], '', rows[0]] + rows[1:]
    w_sec = sec.get('W', [])
    w_rows = [r for r in w_sec if r.startswith('|')
              and not r.startswith('|---') and not r.startswith('| 代码')]
    if w_sec:
        out += ['', '## 超跌反弹观察池（观察信号，非买入建议）', '']
        for r in w_sec:
            if r.startswith('定位：'):
                out.append(f"- {r}")
                break
        for r in w_rows[:10]:
            cells = [c.strip() for c in r.strip('|').split('|')]
            if len(cells) >= 6:
                out.append(f"- {cells[0]} {cells[1]} [{cells[2]}] SCORE={cells[3]} 现价{cells[4]}（{cells[5]}）")
    out += ['', f'## 风险名单（{len(d_rows)} 只）']
    for r in d_rows[:10]:
        cells = [c.strip() for c in r.strip('|').split('|')]
        if len(cells) >= 3:
            out.append(f"- {cells[1]} {cells[2]}: {cells[3]}")
    return '\n'.join(out)


def push_daily_report(trade_date: str = None) -> str:
    """读报告 → AI总结 → 微信推送 → 落盘。返回最终推送文本。"""
    _load_env()
    from datetime import datetime
    if trade_date is None:
        trade_date = datetime.now().strftime('%Y%m%d')
    md_text = _read_report(trade_date)
    if not md_text:
        print(f'[HVT-PUSH] 未找到报告 {_report_path(trade_date)}，跳过')
        return ''
    text = summarize_with_deepseek(md_text, trade_date)
    if not text:
        text = _fallback_summary(md_text, trade_date)
    out_dir = os.path.join(BASE_DIR, 'report_daily')
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f'hvt_bull_ai_{trade_date}.md'), 'w', encoding='utf-8') as f:
        f.write(text)
    send_to_wechat(text, trade_date)
    return text


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    args = ap.parse_args()
    print(push_daily_report(args.date))
