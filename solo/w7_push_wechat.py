# -*- coding: utf-8 -*-
"""
W7 二波引擎 · 每日邮件推送
=========================================
1. 读取最新 w7_second_wave_YYYYMMDD.md 报告
2. 调 DeepSeek 把报告精炼为可直接执行的操作指令（一段文字），并附报告「热点主题新增跟踪」节
3. 通过 Agent Mail CLI 发送邮件（20260929 起替代 PushPlus 微信推送，微信通道已移除）

用法:
  python w7_push_wechat.py                # 自动找最新报告
  python w7_push_wechat.py --date 20260929 # 指定日期
"""
import os, sys, re, glob, subprocess
from datetime import datetime
from dotenv import load_dotenv
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv('d:/mystock/config/.env')

REPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'report_daily')
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
DEEPSEEK_URL = 'https://api.deepseek.com/chat/completions'

# ---- 邮件推送（Agent Mail CLI；与 hvt_bull/push.py、tushare_quant.send_agent_mail 同一通道） ----
MAIL_CLI = r'C:\Users\kongx\AppData\Roaming\npm\agently-cli.cmd'
MAIL_TO = 'stock1975@qq.com'

MAX_REPORT_CHARS = 16000  # 喂给 DeepSeek 的报告截断长度（保留精华段）


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
    """保留统计头部、执行状态三段与各榜单表格，只截掉冗长的行为解释段"""
    keep = []
    skip = False
    for ln in md_text.splitlines():
        if ln.startswith('#'):
            skip = '行为解释' in ln
        if not skip:
            keep.append(ln)
    text = '\n'.join(keep)
    if len(text) <= MAX_REPORT_CHARS:
        return text
    return text[:MAX_REPORT_CHARS] + '\n\n>（报告过长已截断，以上为完整榜单段）'


def _theme_watch_section(md_text: str):
    """抽取报告的「热点主题新增跟踪」节（## 热点主题新增跟踪…）整节，返回行列表。

    该节列出经 V2.4 热点主题扩池纳入的非龙头候选，是报告独立新增的一节；
    邮件 AI 指令按「今日可操作榜/C池」组织，会漏掉它，故原样接进邮件正文。
    """
    out, hit = [], False
    for ln in md_text.splitlines():
        if ln.startswith('## '):
            if '热点主题新增跟踪' in ln:
                hit, out = True, [ln]
                continue
            if hit:
                break
        if hit:
            out.append(ln)
    while out and not out[-1].strip():   # 去尾部空行，保留段内空行（表格前需空行）
        out.pop()
    return out if len(out) > 1 else []


def _right_bottom_section(md_text: str):
    """抽取报告的「④ 右底低吸信号」节（### ④ 右底低吸信号｜W7-RIGHT_BOTTOM…）整节。

    该节独立列出「前波大涨 → 回落 → 双底」的右底低吸买点（20261008 起全市场扫描），
    邮件 AI 指令按「今日可操作榜/C池」组织，会漏掉它，故原样接进邮件正文。
    节标题为 ###（三级），至下一个任意级别标题止；其间父节的「优先=/操作口径/形态分布」
    尾部说明不属本小节，遇之即截断。
    """
    out, hit = [], False
    for ln in md_text.splitlines():
        if ln.startswith('#') and '右底低吸信号' in ln:
            hit, out = True, [ln]
            continue
        if hit and ln.startswith('#'):
            break
        if hit:
            if ln.startswith('优先=') or ln.startswith('形态分布'):
                break
            out.append(ln)
    while out and not out[-1].strip():   # 去尾部空行，保留段内空行（表格前需空行）
        out.pop()
    return out if len(out) > 1 else []


SYSTEM_PROMPT = (
    '你是A股短线交易执行助理。严格基于用户提供的量化报告数据输出，'
    '禁止编造任何数据、价格、新闻或消息面；股票名称与代码必须严格引用报告原文。'
    '\n\n重要规则：'
    '\n- 报告中 T120/ENTRY/HVT/吸收/生命/空间/加速/RS/基本面/DRisk/总分 等均为0-100评分，'
    '**不是股价**，绝对禁止把它们当作价格输出；'
    '\n- 报告中的【现价】【触发价】【MA20】是真实股价（元）：触发价=原策略买点触发位'
    '（BREAKOUT_RETEST 回踩买点=放量突破日收盘=回踩位；MIDLINE_HOLD 不破中位=放量长阳日最高价；'
    'RIGHT_BOTTOM 右底低吸=双底颈线即中间高点），'
    '可直接引用，但禁止自行计算、修改或四舍五入任何价格；'
    '\n- **执行状态优先于 W7 总分**：报告「W7 二波·今日执行状态」已把标的分为三档，'
    '必须严格照抄报告给出的状态，禁止因总分高就把 TRIGGER_WATCH/PULLBACK_WATCH 写成可立即买入：'
    '\n  · EXECUTION＝已站上触发价且量比≥1.2、结构未破坏，可按原仓位规则执行；'
    '\n    （RIGHT_BOTTOM 右底低吸为 EXECUTION 的例外形态：买点＝当日右底本身，现价低于颈线属正常，'
    '缩量不破左底即成立，写"右底低吸买点，可分批建仓"；防线＝收盘跌破左底/前低）'
    '\n  · EXECUTION_WAIT_VOLUME＝已站上触发价但量比未达 1.2，'
    '**禁止写成"可买入/已确认买入"**，只能写"已进入价格执行区，待量能确认"；'
    '\n  · TRIGGER_WATCH＝现价仍低于触发价（距触发 ≤3%），只能写"等放量站上触发价XX.XX（量比≥1.2）"，'
    '**禁止写成已突破或可买入**；'
    '\n  · PULLBACK_WATCH＝距触发位 >3%，仍在回踩/未突破，只能写"观察，等重新站上关键位并满足量能"；'
    '\n- 量能阀门恒为量比≥1.2，任何情况下禁止放宽或省略；报告标的的形态状态'
    '（BREAKOUT_CONFIRM/SECOND_WAVE/RE_EXPANSION/BREAKOUT_RETEST=已突破；MIDLINE_HOLD=未突破缩量回踩；'
    'RIGHT_BOTTOM=前波大涨后双底右底、缩量不破左底的低吸买点；'
    '其余=未突破）只用于补充措辞，不得用来改变上述执行状态；'
    '\n- 巨量日（量比≥3）或单日涨幅>10%不追，只写回踩方案。'
    '\n\n输出要求：'
    '\n1. 只输出一段精炼的操作指令（Markdown，含小标题，总长≤1200字），可直接照着执行；'
    '\n2. 结构固定：【今日结论】一句话（必须与报告「W7状态汇总」完全一致：'
    'EXECUTION 几只、TRIGGER_WATCH 几只、PULLBACK_WATCH 几只；'
    '**报告写"今日 W7 无 EXECUTION 标的/不强行交易"时，必须照写，禁止改写成有买点**）→'
    '【可执行（EXECUTION）】报告中 EXECUTION 与 EXECUTION_WAIT_VOLUME 标的逐只单独成行，'
    '按报告顺序从1开始连续编号，禁止用"统一规则+合并价格列表"的省略写法；每行格式：'
    '"序号. 代码 名称｜状态：EXECUTION（或 EXECUTION_WAIT_VOLUME）｜现价XX.XX、触发价XX.XX，'
    '收盘跌回触发价XX.XX下方离场，失效位XX.XX"'
    '（RIGHT_BOTTOM 右底低吸改为："…｜状态：EXECUTION（右底低吸）｜现价XX.XX、颈线XX.XX，'
    '右底缩量不破左底即成立、可分批建仓，放量站上颈线转突破，失效位＝左底（前低）XX.XX"）；'
    'EXECUTION_WAIT_VOLUME 的操作必须写"待量比≥1.2确认后再执行"；无 EXECUTION 时该节写"无"→'
    '【等待触发（TRIGGER_WATCH）】逐只一句"放量站上触发价XX.XX（量比≥1.2）才触发，不追价"→'
    '【等待回踩（PULLBACK_WATCH）】逐只一句"距触发XX.XX%，观察，等重新站上关键位"→'
    '【风险/纪律】一句话；'
    '\n3. 价格一律引用报告的现价/触发价/MA20 与失效位，保留两位小数，禁止编造；'
    '\n4. 仅当报告无任何 EXECUTION 标的时才写"今日零执行动作"；'
    '\n5. 严禁模糊词（关注/观望/择机），全部换成明确动作或明确触发价。'
)


def _md_to_email_html(md_text: str, title: str) -> str:
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
        '<table>': '<table style="border-collapse:collapse;width:100%;font-size:16px;word-break:break-word;">',
        '<th>': '<th style="background:#1677ff;color:#fff;padding:5px 6px;border:1px solid #b9c6d6;font-size:16px;">',
        '<td>': '<td style="padding:5px 6px;border:1px solid #b9c6d6;font-size:16px;">',
    }
    for k, v in inline.items():
        body = body.replace(k, v)
    # 宽表在手机上会撑破卡片宽度，导致整封邮件上下宽度不一致：套横向滚动容器兜底
    body = body.replace('<table ', '<div style="overflow-x:auto;-webkit-overflow-scrolling:touch;margin:12px 0;">' + '<table ')
    body = body.replace('</table>', '</table></div>')
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
table {{ border-collapse: collapse; width: 100%; font-size: 16px; word-break: break-word; }}
th {{ background: #1677ff; color: #fff; padding: 5px 6px; border: 1px solid #b9c6d6; font-size: 16px; }}
td {{ padding: 5px 6px; border: 1px solid #b9c6d6; font-size: 16px; }}
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


def send_email(msg: str, subject: str, tag: str = 'W7-PUSH') -> bool:
    """Agent Mail CLI 发送操作指令邮件（HTML 正文 22px）到 MAIL_TO。

    CLI 的 --body-file 必须落在当前工作目录内且 ≤1MB，故正文先写 cwd 下的临时
    html 文件，发送成功即删除（失败则留存并打印路径）。任何异常只打日志不抛出。
    """
    if not os.path.exists(MAIL_CLI):
        print(f'[{tag}] 未找到 Agent Mail CLI: {MAIL_CLI}，跳过邮件推送')
        return False

    safe = re.sub(r'[^0-9A-Za-z]+', '_', subject).strip('_') or 'w7'
    body_path = os.path.join(os.getcwd(), f'_w7_mail_{safe}.html')
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
        # DeepSeek 失败则退回榜单摘要（含B榜价格位，截掉行为解释）
        ai_text = summarize_for_ai(md_text)[:1500]

    # 2. 组装推送内容（头部简表 + AI 指令 + 热点主题新增跟踪节）
    header = []
    header.append(f'# W7 二波引擎 · {trade_date} 操作指令')
    header.append('')
    header.append(ai_text)
    header.append('')
    tw = _theme_watch_section(md_text)
    if tw:
        header += tw
        header.append('')
    rb = _right_bottom_section(md_text)
    if rb:
        header += rb
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

    # 4. 邮件推送
    success = send_email(msg, subject=f'W7 二波引擎操作指令 {trade_date}')
    if success:
        print(f'邮件推送完成: {trade_date}')
    else:
        print('邮件推送失败')


if __name__ == '__main__':
    main()
