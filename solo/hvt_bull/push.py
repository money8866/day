# -*- coding: utf-8 -*-
"""HVT-BULL AI 自然语言复盘 + 邮件推送

流程：读 report_daily/hvt_bull_report_{date}.md
     → DeepSeek 生成自然语言复盘（移动端友好）
     → Agent Mail CLI 邮件推送（HTML 正文 22px）
     → 落盘 report_daily/hvt_bull_ai_{date}.md
"""
import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

_AI_SYSTEM_PROMPT = """你是资深A股量化操盘手，专注"历史天量+二次突破"牛股模式。请对 HVT-BULL 每日报告做盘后复盘总结。

报告口径速查：
- 第一梯队：PRIMARY_BUY 名单=当日「第一梯队」（全报告最高置信买点层级，宁缺毋滥）；梯队内 FE≥70 为 A级（四要素×Future Expansion 双重确认），<70 为 B级（结构达标、扩张确认稍弱、仓位从低）；复盘输出时用「第一梯队」指代 PRIMARY_BUY
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

规则：只使用报告中出现的数据，绝不编造；个股必须带完整代码（如 陆家嘴 600663.SH）；**所有个股名称必须加粗显示**，格式如 **陆家嘴**（600663.SH），代码不加粗；不要出现'报告原文'或'根据报告'这类话。"""


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


# ---- 邮件推送（Agent Mail CLI；20260925 起替代 Server酱微信推送，微信通道已移除） ----
MAIL_CLI = r'C:\Users\kongx\AppData\Roaming\npm\agently-cli.cmd'
MAIL_TO = 'stock1975@qq.com'

# 邮件 HTML 样式（移动端优先：22px 大字、卡片式、宽表可横向滑动）
_MAIL_CSS = """
* { box-sizing: border-box; }
body { margin:0; padding:0; background:#eef2f7; -webkit-text-size-adjust:100%;
       font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",Arial,sans-serif;
       font-size:22px; line-height:1.8; color:#1f2937; }
.wrap { padding:14px; }
.hd { background:#1677ff; border-radius:14px; padding:18px 16px; margin-bottom:14px; color:#fff; }
.hd .t { font-size:26px; font-weight:700; line-height:1.45; }
.hd .s { font-size:18px; opacity:.92; margin-top:6px; }
.card { background:#fff; border-radius:14px; padding:18px 16px 8px; box-shadow:0 1px 4px rgba(15,23,42,.08); }
.ft { text-align:center; color:#94a3b8; font-size:17px; line-height:1.7; padding:14px 8px 4px; }
h1,h2,h3,h4 { margin:22px 0 12px; line-height:1.45; font-weight:700; color:#0f172a; }
h1 { font-size:26px; padding-bottom:10px; border-bottom:3px solid #1677ff; }
h2 { font-size:24px; padding-left:12px; border-left:7px solid #1677ff; }
h3 { font-size:22px; color:#334155; }
h4 { font-size:22px; color:#475569; }
p { margin:0 0 14px; }
strong { color:#c2410c; }
a { color:#1677ff; text-decoration:none; border-bottom:1px solid #bfdbfe; word-break:break-all; }
ul,ol { margin:0 0 14px; padding-left:1.35em; }
li { margin:7px 0; }
hr { border:0; border-top:1px dashed #cbd5e1; margin:22px 0; }
blockquote { margin:0 0 14px; padding:10px 14px; background:#f8fafc; border-left:6px solid #93c5fd;
             border-radius:0 8px 8px 0; color:#475569; }
code { background:#f1f5f9; color:#be123c; border-radius:6px; padding:2px 7px; font-size:21px; word-break:break-all; }
pre { background:#0f172a; color:#e2e8f0; border-radius:10px; padding:14px; font-size:20px; line-height:1.6; overflow-x:auto; }
pre code { background:none; color:inherit; padding:0; font-size:20px; }
.tbl { overflow-x:auto; -webkit-overflow-scrolling:touch; margin:0 0 16px;
       border:1px solid #e2e8f0; border-radius:10px; }
table { border-collapse:collapse; width:100%; font-size:22px; }
th { background:#1677ff; color:#fff; font-weight:600; text-align:left; padding:11px 10px;
     border-right:1px solid #4d94ff; white-space:nowrap; }
th:last-child { border-right:0; }
td { padding:11px 10px; border-top:1px solid #e8eef5; white-space:nowrap; }
tbody tr:nth-child(even) { background:#f8fafc; }
"""


def _md_to_email_html(md_text: str, title: str, subtitle: str = '') -> str:
    """markdown → 邮件 HTML 正文：卡片式排版、正文 22px、移动端优先。

    - 字号写死在 CSS 里，避免客户端剥离样式后回退成小字（老花阅读）
    - 宽表套滚动容器（.tbl），手机端可左右滑动，不压缩列宽
    - 不依赖外部 CSS/图片，QQ 邮箱等客户端直接渲染
    """
    import re
    import markdown2
    from datetime import datetime
    body = markdown2.markdown(
        md_text,
        extras=['tables', 'fenced-code-blocks', 'strike', 'task_list']
    )
    # 每个表格套一层可横向滚动容器
    body = re.sub(r'<table>', '<div class="tbl"><table>', body).replace('</table>', '</table></div>')
    sub = f'<div class="s">{subtitle}</div>' if subtitle else ''
    gen_time = datetime.now().strftime('%Y-%m-%d %H:%M')
    return (
        '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<title>{title}</title>\n<style>{_MAIL_CSS}</style>\n</head>\n<body>\n'
        '<div class="wrap">\n'
        f'<div class="hd"><div class="t">{title}</div>{sub}</div>\n'
        f'<div class="card">\n{body}\n</div>\n'
        f'<div class="ft">生成时间 {gen_time}<br>本邮件由 HVT-BULL 量化系统自动推送，仅供参考，不构成投资建议</div>\n'
        '</div>\n</body>\n</html>'
    )


def send_email(text: str, trade_date: str) -> bool:
    """Agent Mail CLI 发送复盘邮件（HTML 正文 22px）到 MAIL_TO。

    CLI 的 --body-file 必须落在当前工作目录内且 ≤1MB，故正文先写 cwd 下的临时
    html 文件，发送成功即删除（失败则留存并打印路径）。任何异常只打日志不抛出。
    """
    if not os.path.exists(MAIL_CLI):
        print(f'[HVT-PUSH] 未找到 Agent Mail CLI: {MAIL_CLI}，跳过邮件推送')
        return False

    subject = f'{trade_date} HVT-BULL 天量牛股复盘'
    body_path = os.path.join(os.getcwd(), f'_hvt_mail_{trade_date}.html')
    try:
        html = _md_to_email_html(text, subject, '历史天量 + 二次突破 · AI 复盘')
        raw = html.encode('utf-8')
        if len(raw) > 1000 * 1024:
            html = raw[:1000 * 1024].decode('utf-8', 'ignore') + '<p>（正文超过 1MB，已截断）</p>'
        with open(body_path, 'w', encoding='utf-8') as f:
            f.write(html)
    except Exception as e:
        print(f'[HVT-PUSH] 邮件正文写入失败: {e}')
        return False

    cmd = [MAIL_CLI, 'message', '+send', '--to', MAIL_TO, '--subject', subject,
           '--body-file', os.path.basename(body_path), '--body-format', 'html', '--confirmed']
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                           errors='ignore', timeout=180)
        if r.returncode == 0 and '"ok": true' in (r.stdout or ''):
            print(f'[HVT-PUSH] 邮件推送成功: {MAIL_TO}')
            os.remove(body_path)
            return True
        detail = ((r.stdout or '') + (r.stderr or '')).strip().replace('\n', ' ')[:300]
        print(f'[HVT-PUSH] 邮件推送失败: rc={r.returncode} {detail}（正文留存: {body_path}）')
        return False
    except Exception as e:
        print(f'[HVT-PUSH] 邮件推送异常: {e}')
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
    """读报告 → AI总结 → 邮件推送 → 落盘。返回最终推送文本。"""
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
    send_email(text, trade_date)
    return text


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    args = ap.parse_args()
    print(push_daily_report(args.date))
