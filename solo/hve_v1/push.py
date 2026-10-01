# -*- coding: utf-8 -*-
"""HVE V1 日报邮件推送（§47 日报 / §49 输出；Agent Mail CLI，HTML 正文 22px）

流程：读 report_daily/hve_daily_{date}.md
     → DeepSeek 生成自然语言复盘（移动端友好）
     → Agent Mail CLI 邮件推送（HTML 正文 22px，卡片式）
     → 落盘 report_daily/hve_daily_ai_{date}.md

硬约束（与 hvt_bull/push.py 一致）：
  - 正文 22px（用户老花）、宽表可横向滑动
  - AI 不可用时降级为确定性摘要，绝不编造
  - 任何异常只打日志，不抛出（扫描与落库不受影响）
"""
import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

_AI_SYSTEM_PROMPT = """你是资深A股量化研究者，负责 HVE V1（High Volume Event 高量事件）策略的每日复盘。

口径速查（必须严格遵守，不得改写语义）：
- HVE 是「事件」，不是「买点」：HVE 当日永不产生 BUY，只进入 HVE_EVENT 观察态
- HVE-BULL：HVE + MA20 向上 + 结构完好 + 再扩张（Close 破前3日高、量能放大、阳线）→ BUY
- HVE-2ND：HVE + 消化（整理≥3日）+ Volume Contraction + 结构完好 + 突破整理区高点 → BUY
- HVE-WATCH：结构完好但尚未满足再扩张/突破，等确认
- HVE-FAIL：结构破坏（回撤超限 / 跌破 MA20−ATR / 跌破整理区低点），风险回避
- 缩量一律称「Volume Contraction」，禁止写成「卖压减少/抛压减轻」
- 本策略没有综合评分，禁止出现分数、Alpha、评级、星级；排序只是展示顺序
- 可交易性：LIQUID / LIMIT_UP_RISK / ONE_PRICE_BOARD；涨停或一字板即使出 BUY 也不默认可执行
- 市场环境为 MARKET_RISK 时，BUY 会被降级为 signal=CONDITIONAL（§32，保留研究信号但不执行）：
  CONDITIONAL 必须明确标注「不可执行」，禁止写成可买/可执行

请输出（200~450字，Markdown 列表，禁止首行缩进，适合手机阅读）：
1. 一句话结论：当日 HVE 事件整体偏多还是偏空，机会与风险哪个占优
2. 今日 BUY 信号：分两组列出 —— ① signal=BUY（可执行）：逐只说清路径（HVE-BULL 再扩张 / HVE-2ND 消化后突破）、HVE 日期、入场价与失效价；② signal=CONDITIONAL（市场 RISK 降级，不可执行）：只列名称+代码+路径，并注明「因市场风险不执行；涨停/一字板的另行标注」
3. 观察名单：HVE-WATCH 里最值得盯的 2~3 只，说明还差什么条件才确认
4. 风险名单：HVE-FAIL 报总数并点出最需警惕的 2~3 只及失效规则
5. 明日操作要点：观察什么、避免什么

规则：只使用报告中出现的数据，绝不编造；个股必须带完整代码（如 某股票 600xxx.SH），所有个股名称加粗、代码不加粗；
不得出现「必涨」「高概率」「保证」等结论性用语；不要出现「报告原文」这类话。"""


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
    return os.path.join(BASE_DIR, 'report_daily', f'hve_daily_{trade_date}.md')


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
        print('[HVE-PUSH] 未配置 DEEPSEEK_API_KEY，跳过AI总结')
        return ''
    try:
        url = 'https://api.deepseek.com/v1/chat/completions'
        headers = {'Content-Type': 'application/json',
                   'Authorization': f'Bearer {api_key}'}
        messages = [
            {'role': 'system', 'content': _AI_SYSTEM_PROMPT},
            {'role': 'user', 'content': f'请复盘 {trade_date} 的 HVE V1 日报：\n\n{md_text}'},
        ]
        data = {'model': 'deepseek-chat', 'messages': messages,
                'temperature': 0.3, 'max_tokens': 1024}
        resp = requests.post(url, headers=headers, json=data, timeout=60)
        resp.raise_for_status()
        text = resp.json()['choices'][0]['message']['content'].strip()
        return text[:3000]
    except Exception as e:
        print(f'[HVE-PUSH] AI总结失败: {e}')
        return ''


# ---- 邮件推送（Agent Mail CLI） ----
MAIL_CLI = r'C:\Users\kongx\AppData\Roaming\npm\agently-cli.cmd'
MAIL_TO = 'stock1975@qq.com'

# 邮件 HTML 样式（移动端优先：22px 大字、卡片式、宽表可横向滑动）
_MAIL_CSS = """
* { box-sizing: border-box; }
body { margin:0; padding:0; background:#eef2f7; -webkit-text-size-adjust:100%;
       font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",Arial,sans-serif;
       font-size:22px; line-height:1.8; color:#1f2937; }
.wrap { padding:14px; }
.hd { background:#0f766e; border-radius:14px; padding:18px 16px; margin-bottom:14px; color:#fff; }
.hd .t { font-size:26px; font-weight:700; line-height:1.45; }
.hd .s { font-size:18px; opacity:.92; margin-top:6px; }
.card { background:#fff; border-radius:14px; padding:18px 16px 8px; box-shadow:0 1px 4px rgba(15,23,42,.08); }
.ft { text-align:center; color:#94a3b8; font-size:17px; line-height:1.7; padding:14px 8px 4px; }
h1,h2,h3,h4 { margin:22px 0 12px; line-height:1.45; font-weight:700; color:#0f172a; }
h1 { font-size:26px; padding-bottom:10px; border-bottom:3px solid #0f766e; }
h2 { font-size:24px; padding-left:12px; border-left:7px solid #0f766e; }
h3 { font-size:22px; color:#334155; }
h4 { font-size:22px; color:#475569; }
p { margin:0 0 14px; }
strong { color:#b45309; }
a { color:#0f766e; text-decoration:none; border-bottom:1px solid #99f6e4; word-break:break-all; }
ul,ol { margin:0 0 14px; padding-left:1.35em; }
li { margin:7px 0; }
hr { border:0; border-top:1px dashed #cbd5e1; margin:22px 0; }
blockquote { margin:0 0 14px; padding:10px 14px; background:#f8fafc; border-left:6px solid #5eead4;
             border-radius:0 8px 8px 0; color:#475569; }
code { background:#f1f5f9; color:#be123c; border-radius:6px; padding:2px 7px; font-size:21px; word-break:break-all; }
pre { background:#0f172a; color:#e2e8f0; border-radius:10px; padding:14px; font-size:20px; line-height:1.6; overflow-x:auto; }
pre code { background:none; color:inherit; padding:0; font-size:20px; }
.tbl { overflow-x:auto; -webkit-overflow-scrolling:touch; margin:0 0 16px;
       border:1px solid #e2e8f0; border-radius:10px; }
table { border-collapse:collapse; width:100%; font-size:22px; }
th { background:#0f766e; color:#fff; font-weight:600; text-align:left; padding:11px 10px;
     border-right:1px solid #2ba396; white-space:nowrap; }
th:last-child { border-right:0; }
td { padding:11px 10px; border-top:1px solid #e8eef5; white-space:nowrap; }
tbody tr:nth-child(even) { background:#f8fafc; }
"""


def _md_to_email_html(md_text: str, title: str, subtitle: str = '') -> str:
    """markdown → 邮件 HTML：卡片式、正文 22px、宽表可横滑、不依赖外部资源"""
    import re
    import markdown2
    from datetime import datetime
    body = markdown2.markdown(
        md_text,
        extras=['tables', 'fenced-code-blocks', 'strike', 'task_list']
    )
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
        f'<div class="ft">生成时间 {gen_time}<br>'
        '本邮件由 HVE V1 量化系统自动推送，仅输出信号，不构成投资建议</div>\n'
        '</div>\n</body>\n</html>'
    )


def send_email(text: str, trade_date: str) -> bool:
    """Agent Mail CLI 发送日报邮件（HTML 正文 22px）到 MAIL_TO。

    CLI 的 --body-file 必须落在当前工作目录内且 ≤1MB，故正文先写 cwd 下的临时
    html 文件，发送成功即删除（失败则留存并打印路径）。异常只打日志不抛出。
    """
    if not os.path.exists(MAIL_CLI):
        print(f'[HVE-PUSH] 未找到 Agent Mail CLI: {MAIL_CLI}，跳过邮件推送')
        return False

    subject = f'{trade_date} HVE V1 高量事件日报'
    body_path = os.path.join(os.getcwd(), f'_hve_mail_{trade_date}.html')
    try:
        html = _md_to_email_html(text, subject, '高量事件 + 再扩张 / 二次突破')
        raw = html.encode('utf-8')
        if len(raw) > 1000 * 1024:
            html = raw[:1000 * 1024].decode('utf-8', 'ignore') + '<p>（正文超过 1MB，已截断）</p>'
        with open(body_path, 'w', encoding='utf-8') as f:
            f.write(html)
    except Exception as e:
        print(f'[HVE-PUSH] 邮件正文写入失败: {e}')
        return False

    cmd = [MAIL_CLI, 'message', '+send', '--to', MAIL_TO, '--subject', subject,
           '--body-file', os.path.basename(body_path), '--body-format', 'html', '--confirmed']
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                           errors='ignore', timeout=180)
        if r.returncode == 0 and '"ok": true' in (r.stdout or ''):
            print(f'[HVE-PUSH] 邮件推送成功: {MAIL_TO}')
            os.remove(body_path)
            return True
        detail = ((r.stdout or '') + (r.stderr or '')).strip().replace('\n', ' ')[:300]
        print(f'[HVE-PUSH] 邮件推送失败: rc={r.returncode} {detail}（正文留存: {body_path}）')
        return False
    except Exception as e:
        print(f'[HVE-PUSH] 邮件推送异常: {e}')
        return False


def _fallback_summary(md_text: str, trade_date: str) -> str:
    """AI 不可用时的确定性降级摘要：头部统计 + 两态明细（截断）+ 观察/失效计数"""
    lines = md_text.splitlines()
    head, bull, sec2 = [], [], []
    cur = None
    watch_n = fail_n = None
    for ln in lines:
        if ln.startswith('## HVE-BULL'):
            cur = 'B'
            continue
        if ln.startswith('## HVE-2ND'):
            cur = 'C'
            continue
        if ln.startswith('## HVE-WATCH'):
            cur = 'W'
            watch_n = ln
            continue
        if ln.startswith('## HVE-FAIL'):
            cur = 'F'
            fail_n = ln
            continue
        if ln.startswith('## '):
            cur = None
            continue
        if cur is None:
            if ln.startswith(('# ', '- ', '> ')):
                head.append(ln)
            continue
        if cur == 'B' and ln.strip():
            bull.append(ln)
        elif cur == 'C' and ln.strip():
            sec2.append(ln)
    out = [f'# {trade_date} HVE V1 日报（AI降级版）', '']
    out += head
    for title, block in (('HVE-BULL', bull), ('HVE-2ND', sec2)):
        out += ['', f'## {title}', '']
        if not block:
            out.append('无。')
            continue
        out += [ln for ln in block if ln.startswith('### ')]
        out += [ln for ln in block if not ln.startswith('### ')][:60]
    out += ['', f"## 观察与风险"]
    out.append(f"- {watch_n or 'HVE-WATCH：0'}")
    out.append(f"- {fail_n or 'HVE-FAIL：0'}")
    out.append('- 详情见完整日报（本邮件为降级摘要）')
    return '\n'.join(out)


def push_daily_report(trade_date: str = None) -> str:
    """读日报 → AI总结（失败降级）→ 邮件推送 → 落盘。返回最终推送文本。"""
    _load_env()
    from datetime import datetime
    if trade_date is None:
        trade_date = datetime.now().strftime('%Y%m%d')
    trade_date = str(trade_date)
    md_text = _read_report(trade_date)
    if not md_text:
        print(f'[HVE-PUSH] 未找到日报 {_report_path(trade_date)}，跳过')
        return ''
    text = summarize_with_deepseek(md_text, trade_date)
    if not text:
        text = _fallback_summary(md_text, trade_date)
    out_dir = os.path.join(BASE_DIR, 'report_daily')
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f'hve_daily_ai_{trade_date}.md'), 'w', encoding='utf-8') as f:
        f.write(text)
    send_email(text, trade_date)
    return text


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    args = ap.parse_args()
    print(push_daily_report(args.date))
