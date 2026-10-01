# -*- coding: utf-8 -*-
"""明日行程提醒 —— 邮件推送封装（Agent Mail CLI，HTML 正文 22px 卡片式）。

复用 _tomorrow_agenda.py 的查询逻辑，把其标准输出包成移动端友好 HTML 邮件
发到 MAIL_TO。邮件样式与 solo/hve_v1/push.py、solo/hvt_bull/push.py 保持一致。

用法：
    python _push_agenda_mail.py          # 查询明天并推送
    python _push_agenda_mail.py --dry    # 只打印 HTML 到 cwd，不发邮件
"""
import os
import re
import sys
import html as _html
import subprocess
import importlib.util
from datetime import datetime

CWD = r"D:\mystock"
MAIL_CLI = r'C:\Users\kongx\AppData\Roaming\npm\agently-cli.cmd'
MAIL_TO = 'stock1975@qq.com'

# 复用 hve_v1 的邮件样式（青绿主题；行程提醒为中性信息，色值保持一致以免多套风格）
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
.ev { padding:14px 0; border-bottom:1px dashed #cbd5e1; }
.ev:last-child { border-bottom:0; }
.ev .tm { display:inline-block; background:#0f766e; color:#fff; border-radius:8px;
          padding:2px 12px; font-size:22px; font-weight:700; letter-spacing:.5px; }
.ev .ti { font-size:24px; font-weight:700; color:#0f172a; margin-top:8px; }
.ev .mt { font-size:19px; color:#64748b; margin-top:6px; }
.empty { text-align:center; font-size:24px; color:#0f766e; font-weight:700; padding:26px 0 30px; }
"""


def _load_agenda_module():
    """_tomorrow_agenda.py 以数字/下划线开头且在工作区，用 spec 方式加载最稳。"""
    path = os.path.join(CWD, "_tomorrow_agenda.py")
    spec = importlib.util.spec_from_file_location("_tomorrow_agenda", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build_agenda_data():
    """返回 (tomorrow_date, weekday_cn, events)；事件为 dict 列表，按时间升序。"""
    from datetime import timezone, timedelta
    TZ8 = timezone(timedelta(hours=8))
    mod = _load_agenda_module()
    tomorrow = (datetime.now(TZ8) + timedelta(days=1)).date()
    weekday_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][tomorrow.weekday()]
    events = mod.fetch_qclaw_events()
    todays = [e for e in events if e["start"].date() == tomorrow]
    todays.sort(key=lambda x: x["start"])
    return tomorrow, weekday_cn, todays


def render_html(tomorrow, weekday_cn, todays) -> str:
    title = f"📅 明日行程提醒（{tomorrow.strftime('%Y-%m-%d')} {weekday_cn}）"
    if not todays:
        body = '<div class="empty">✨ 明日暂无安排，好好休息！</div>'
    else:
        rows = []
        for e in todays:
            t = e["start"].strftime("%H:%M")
            ti = _html.escape(e["summary"])
            meta = []
            if e.get("location"):
                loc = e["location"].splitlines()[0].strip()
                if loc:
                    meta.append(f"📍 {_html.escape(loc)}")
            if e.get("calendar") and e["calendar"] != "日历":
                meta.append(f"（{_html.escape(e['calendar'])}日历）")
            mt = f'<div class="mt">{"　".join(meta)}</div>' if meta else ''
            rows.append(
                f'<div class="ev"><span class="tm">{t}</span>'
                f'<div class="ti">{ti}</div>{mt}</div>'
            )
        body = "".join(rows)

    n = len(todays)
    subtitle = f"共 {n} 项安排" if n else "明日无安排"
    gen_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    return (
        '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<title>{title}</title>\n<style>{_MAIL_CSS}</style>\n</head>\n<body>\n'
        '<div class="wrap">\n'
        f'<div class="hd"><div class="t">{title}</div><div class="s">{subtitle}</div></div>\n'
        f'<div class="card">\n{body}\n</div>\n'
        f'<div class="ft">生成时间 {gen_time}<br>'
        '本邮件由 明日行程提醒 自动化任务推送</div>\n'
        '</div>\n</body>\n</html>'
    )


def send_email(html_body: str, subject: str) -> bool:
    """Agent Mail CLI 发送 HTML 邮件。--body-file 必须在 cwd 内且 ≤1MB。"""
    if not os.path.exists(MAIL_CLI):
        print(f'[AGENDA-PUSH] 未找到 Agent Mail CLI: {MAIL_CLI}，跳过邮件推送')
        return False

    body_path = os.path.join(CWD, '_agenda_mail_body.html')
    try:
        raw = html_body.encode('utf-8')
        if len(raw) > 1000 * 1024:
            html_body = raw[:1000 * 1024].decode('utf-8', 'ignore') + '<p>（正文超过 1MB，已截断）</p>'
        with open(body_path, 'w', encoding='utf-8') as f:
            f.write(html_body)
    except Exception as e:
        print(f'[AGENDA-PUSH] 邮件正文写入失败: {e}')
        return False

    cmd = [MAIL_CLI, 'message', '+send', '--to', MAIL_TO, '--subject', subject,
           '--body-file', os.path.basename(body_path), '--body-format', 'html', '--confirmed']
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                           errors='ignore', timeout=180, cwd=CWD)
        if r.returncode == 0 and '"ok": true' in (r.stdout or ''):
            print(f'[AGENDA-PUSH] 邮件推送成功: {MAIL_TO}')
            try:
                os.remove(body_path)
            except OSError:
                pass
            return True
        detail = ((r.stdout or '') + (r.stderr or '')).strip().replace('\n', ' ')[:300]
        print(f'[AGENDA-PUSH] 邮件推送失败: rc={r.returncode} {detail}（正文留存: {body_path}）')
        return False
    except Exception as e:
        print(f'[AGENDA-PUSH] 邮件推送异常: {e}')
        return False


def main():
    dry = '--dry' in sys.argv
    try:
        tomorrow, weekday_cn, todays = build_agenda_data()
    except Exception as e:
        print(f'[AGENDA-PUSH] 日程查询失败: {type(e).__name__}: {e}')
        return 1

    # 控制台仍打印纯文本版（与 _tomorrow_agenda.py 输出格式一致，便于人工核对）
    print(f"📅 明日行程提醒（{tomorrow.strftime('%Y-%m-%d')} {weekday_cn}）")
    print("=" * 40)
    if not todays:
        print("✨ 明日暂无安排，好好休息！")
    else:
        for e in todays:
            line = f"• {e['start'].strftime('%H:%M')} {e['summary']}"
            if e.get("location"):
                line += f"  📍{e['location'].splitlines()[0]}"
            print(line)
            if e["calendar"] != "日历":
                print(f"    （{e['calendar']}日历）")
    print("=" * 40)

    subject = f"📅 明日行程提醒 {tomorrow.strftime('%m-%d')} {weekday_cn}"
    html_body = render_html(tomorrow, weekday_cn, todays)

    if dry:
        out = os.path.join(CWD, '_agenda_preview.html')
        with open(out, 'w', encoding='utf-8') as f:
            f.write(html_body)
        print(f'[AGENDA-PUSH] dry-run，HTML 已写入 {out}')
        return 0

    ok = send_email(html_body, subject)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
