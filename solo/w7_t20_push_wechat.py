# -*- coding: utf-8 -*-
"""
W7 T20 右尾引擎 · 邮件推送
=========================================
1. 读取最新 w7_t20_right_tail_YYYYMMDD.md 报告
2. 提取【TOP_PICK】与【PRIMARY_BUY】(按报告 SPACE 空间优选分顺序, 不做二次重排)
3. 调 DeepSeek 精炼为可执行操作指令 → 通过 Agent Mail CLI 发送邮件
   （20260929 起替代 PushPlus 微信推送，微信通道已移除）

用法:
  python w7_t20_push_wechat.py                # 自动找最新报告
  python w7_t20_push_wechat.py --date 20260907
"""
import os, sys, re, glob, subprocess
from datetime import datetime
from dotenv import load_dotenv
import requests

sys_path = os.path.dirname(os.path.abspath(__file__))
if sys_path not in sys.path:
    sys.path.insert(0, sys_path)
load_dotenv('d:/mystock/config/.env')

REPORT_DIR = os.path.join(sys_path, 'report_daily')
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
DEEPSEEK_URL = 'https://api.deepseek.com/chat/completions'
MAX_REPORT_CHARS = 16000

# ---- 邮件推送（Agent Mail CLI；与 hvt_bull/push.py、tushare_quant.send_agent_mail 同一通道） ----
MAIL_CLI = r'C:\Users\kongx\AppData\Roaming\npm\agently-cli.cmd'
MAIL_TO = 'stock1975@qq.com'


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


def _md_to_email_html(md_text, title):
    """markdown → 手机友好邮件 HTML。

    22px 老花字号 + 卡片式排版 + 响应式宽度（max-width 680，适配手机）。
    <style> 与关键标签内联样式双保险（部分邮件客户端会剥离 <style>）。
    """
    import markdown2
    import html as _html

    text = md_text.lstrip('\ufeff').lstrip()
    if text.startswith('# '):                      # 首行 H1 与邮件头重复，去掉
        text = text.split('\n', 1)[1] if '\n' in text else ''
    text = re.sub(r'(?m)^(?=\d+\.\s)', '\n', text)  # 「1. xxx」紧跟段落会被吞成普通文本，补空行还原有序列表
    body = markdown2.markdown(
        text,
        extras=['tables', 'fenced-code-blocks', 'strike', 'task_list', 'code-friendly']
    )
    # markdown2 输出的标签不带属性，可安全地补内联样式
    inline = {
        '<h1>': '<h1 style="font-size:25px;line-height:1.45;color:#0b4fd6;margin:0 0 10px;">',
        '<h2>': '<h2 style="font-size:23px;line-height:1.5;color:#0b4fd6;margin:22px 0 10px;padding-left:10px;border-left:6px solid #1677ff;">',
        '<h3>': '<h3 style="font-size:22px;line-height:1.5;color:#334155;margin:16px 0 8px;">',
        '<p>': '<p style="font-size:22px;line-height:1.8;color:#1f2937;margin:10px 0;">',
        '<ol>': '<ol style="font-size:22px;line-height:1.8;color:#1f2937;margin:10px 0;padding-left:30px;">',
        '<ul>': '<ul style="font-size:22px;line-height:1.8;color:#1f2937;margin:10px 0;padding-left:24px;">',
        '<li>': '<li style="font-size:22px;line-height:1.8;margin:0 0 10px;">',
        '<blockquote>': '<blockquote style="margin:12px 0;padding:10px 12px;background:#f1f5f9;border-left:4px solid #94a3b8;color:#475569;font-size:20px;">',
        '<hr>': '<hr style="border:0;border-top:1px solid #e2e8f0;margin:20px 0;">',
        '<strong>': '<strong style="color:#0b4fd6;">',
        '<em>': '<em style="font-style:normal;color:#94a3b8;font-size:19px;">',
        '<table>': '<table style="border-collapse:collapse;width:100%;font-size:20px;">',
        '<th>': '<th style="background:#1677ff;color:#fff;padding:10px;border:1px solid #b9c6d6;">',
        '<td>': '<td style="padding:10px;border:1px solid #b9c6d6;">',
    }
    for k, v in inline.items():
        body = body.replace(k, v)
    body = body.replace('｜', '<br>')   # 全角竖线竖排，避免手机端长行折行错乱
    head = _html.escape(title)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{head}</title>
<style>
body {{ margin: 0; padding: 0; background: #eef2f7; -webkit-text-size-adjust: 100%; }}
.wrap {{ max-width: 680px; margin: 0 auto; padding: 14px 12px 24px; font-family: -apple-system, "PingFang SC", "Microsoft YaHei", Arial; }}
.hero {{ background: #1677ff; background: linear-gradient(135deg, #1677ff 0%, #0b4fd6 100%); border-radius: 14px; padding: 16px 18px; }}
.hero .t {{ font-size: 24px; font-weight: 700; line-height: 1.5; color: #ffffff; }}
.hero .s {{ font-size: 17px; color: #dbeafe; margin-top: 6px; }}
.card {{ background: #ffffff; border-radius: 14px; padding: 14px 16px; margin-top: 14px; box-shadow: 0 1px 3px rgba(16,24,40,.08); }}
h1 {{ font-size: 25px; color: #0b4fd6; margin: 0 0 10px; }}
h2 {{ font-size: 23px; color: #0b4fd6; margin: 22px 0 10px; padding-left: 10px; border-left: 6px solid #1677ff; }}
h3 {{ font-size: 22px; color: #334155; margin: 16px 0 8px; }}
p, li, td, th, div {{ font-size: 22px; line-height: 1.8; color: #1f2937; }}
strong {{ color: #0b4fd6; }}
table {{ border-collapse: collapse; width: 100%; font-size: 20px; }}
th {{ background: #1677ff; color: #fff; padding: 10px; border: 1px solid #b9c6d6; }}
td {{ padding: 10px; border: 1px solid #b9c6d6; }}
</style>
</head>
<body style="margin:0;padding:0;background:#eef2f7;font-size:22px;line-height:1.8;color:#1f2937;">
<div class="wrap" style="max-width:680px;margin:0 auto;padding:14px 12px 24px;">
<div class="hero" style="background:#1677ff;border-radius:14px;padding:16px 18px;">
<div class="t" style="font-size:24px;font-weight:700;line-height:1.5;color:#ffffff;">{head}</div>
<div class="s" style="font-size:17px;color:#dbeafe;margin-top:6px;">盘后自动推送</div>
</div>
<div class="card" style="background:#ffffff;border-radius:14px;padding:14px 16px;margin-top:14px;">
{body}
</div>
</div>
</body>
</html>"""


def send_email(msg, subject, tag='W7-T20-PUSH'):
    """Agent Mail CLI 发送操作指令邮件（HTML 正文 22px）到 MAIL_TO。

    CLI 的 --body-file 必须落在当前工作目录内且 ≤1MB，故正文先写 cwd 下的临时
    html 文件，发送成功即删除（失败则留存并打印路径）。任何异常只打日志不抛出。
    """
    if not os.path.exists(MAIL_CLI):
        print(f'[{tag}] 未找到 Agent Mail CLI: {MAIL_CLI}，跳过邮件推送')
        return False

    safe = re.sub(r'[^0-9A-Za-z]+', '_', subject).strip('_') or 'w7t20'
    body_path = os.path.join(os.getcwd(), f'_w7_t20_mail_{safe}.html')
    try:
        html = _md_to_email_html(msg, subject)
        raw = html.encode('utf-8')
        if len(raw) > 1000 * 1024:
            html = raw[:1000 * 1024].decode('utf-8', 'ignore') + '<p>（正文超过 1MB，已截断）</p>'
        with open(body_path, 'w', encoding='utf-8') as f:
            f.write(html)
    except Exception as e:
        print(f'[{tag}] 邮件正文写入失败: {e}')
        return False

    cmd = [MAIL_CLI, 'message', '+send', '--to', MAIL_TO, '--subject', subject,
           '--body-file', os.path.basename(body_path), '--body-format', 'html', '--confirmed']
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                           errors='ignore', timeout=180)
        if r.returncode == 0 and '"ok": true' in (r.stdout or ''):
            print(f'✅ 邮件推送成功: {MAIL_TO}')
            os.remove(body_path)
            return True
        detail = ((r.stdout or '') + (r.stderr or '')).strip().replace('\n', ' ')[:300]
        print(f'⚠️ 邮件推送失败: rc={r.returncode} {detail}（正文留存: {body_path}）')
        return False
    except Exception as e:
        print(f'⚠️ 邮件推送异常: {e}')
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
    success = send_email(msg, subject=f'W7 T20 右尾操作指令 {trade_date}')
    print('邮件推送完成' if success else '邮件推送失败')


if __name__ == '__main__':
    main()
