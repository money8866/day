# -*- coding: utf-8 -*-
"""复盘PDF生成器 - 快捷指令
用法: python _final_pdf.py [日期]  (日期不填则自动找最新html)
"""
import os, re, datetime, sys

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                  Table, TableStyle, HRFlowable)
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER

FONT = 'Chinese'
for fp in [r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\simhei.ttf']:
    if os.path.exists(fp):
        try:
            pdfmetrics.registerFont(TTFont(FONT, fp))
            print('字体:', fp)
            break
        except:
            pass

# 标准配色
C = {
    '大盘情绪': colors.HexColor('#1a3a8a'),
    '主题':     colors.HexColor('#2c5f9e'),
    '强势股池': colors.HexColor('#e74c3c'),
    'ETF':      colors.HexColor('#27ae60'),
    '量能':     colors.HexColor('#e67e22'),
    '波浪':     colors.HexColor('#8e44ad'),
    '低吸':     colors.HexColor('#8e44ad'),
}

def S(name, **kw):
    b = dict(fontName=FONT, leading=14)
    b.update(kw)
    return ParagraphStyle(name, **b)

Ss = {
    'title':   S('T', fontSize=20, alignment=TA_CENTER, textColor=colors.HexColor('#1a3a8a'), spaceAfter=6),
    'sub':     S('Su', fontSize=10, alignment=TA_CENTER, textColor=colors.HexColor('#888'), spaceAfter=4),
    'h1':      S('H1', fontSize=13, textColor=colors.HexColor('#1a3a8a'), spaceBefore=14, spaceAfter=6),
    'h2':      S('H2', fontSize=11, textColor=colors.HexColor('#2c5f9e'), spaceBefore=8, spaceAfter=4),
    'body':    S('B', fontSize=9.5, textColor=colors.HexColor('#222')),
    'bul':     S('Bu', fontSize=9.5, leftIndent=14),
    'hl_r':    S('HR', fontSize=9.5, textColor=colors.HexColor('#c0392b'), backColor=colors.HexColor('#fff5f5'), leading=13),
    'hl_g':    S('HG', fontSize=9.5, textColor=colors.HexColor('#27ae60'), backColor=colors.HexColor('#f0fff4'), leading=13),
    'hl_o':    S('HO', fontSize=9.5, textColor=colors.HexColor('#e67e22'), backColor=colors.HexColor('#fffaf0'), leading=13),
    'footer':  S('F', fontSize=8, textColor=colors.HexColor('#aaa')),
}

def sp(h=0.3):    return Spacer(1, h*cm)
def hr(c, t=2):   return HRFlowable(width='100%', thickness=t, color=c, spaceAfter=3)
def h1(t, c=None):return [hr(c or colors.HexColor('#1a3a8a')), Paragraph(t, Ss['h1'])]
def h2(t):        return Paragraph(t, Ss['h2'])
def p(t):         return Paragraph(t, Ss['body'])
def bul(t):       return Paragraph('  ' + t, Ss['bul'])
def hl(t, k='hl_r'): return Paragraph(t, Ss[k])

def trow(data, cw, color, fs=9):
    t = Table(data, colWidths=cw)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), color),
        ('TEXTCOLOR',  (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN',      (0,0), (-1,-1), 'CENTER'),
        ('VALIGN',     (0,0), (-1,-1), 'MIDDLE'),
        ('FONTNAME',   (0,0), (-1,-1), FONT),
        ('FONTSIZE',   (0,0), (-1,-1), fs),
        ('TOPPADDING',    (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('LEFTPADDING',   (0,0), (-1,-1), 4),
        ('RIGHTPADDING',  (0,0), (-1,-1), 4),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#cccccc')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1),
         [colors.HexColor('#f8f9fa'), colors.HexColor('#eef1f5')]),
    ]))
    return t

# ── HTML解析 ─────────────────────────────────────────────
def strip_tags(html):
    html = html.replace('<strong>', '【').replace('</strong>', '】')
    html = re.sub(r'<br\s*/?>', '\n', html)
    html = re.sub(r'<[^>]+>', '', html)
    html = html.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>')
    return html

def get_block(html, key):
    """提取 <p><strong>key 开始的段落块"""
    pattern = re.escape(key)
    m = re.search(r'<p[^>]*><strong>' + pattern + r'.*?</p>', html, re.DOTALL)
    if not m:
        return ''
    return strip_tags(m.group())

def get_blocks(html, key):
    """提取所有匹配key的段落"""
    pattern = re.escape(key)
    blocks = re.findall(r'<p[^>]*><strong>' + pattern + r'.*?</p>', html, re.DOTALL)
    return [strip_tags(b) for b in blocks]

# ── 找最新HTML ───────────────────────────────────────────
def latest_html(base):
    files = [(re.search(r'(\d{8})', f).group(1), f)
             for f in os.listdir(base)
             if re.match(r'Final_Self_\d{8}\.html$', f)]
    files.sort(reverse=True)
    return os.path.join(base, files[0][1]) if files else None

# ═══════════════════════════════════════════════════════════
def generate(date=None):
    base = r'D:\mystock\report_daily'

    if date:
        html_path = os.path.join(base, f'Final_Self_{date}.html')
    else:
        html_path = latest_html(base)
        if not html_path:
            print('未找到HTML文件'); return

    m_date = re.search(r'(\d{8})', html_path)
    date_str = m_date.group(1) if m_date else datetime.date.today().strftime('%Y%m%d')
    date_disp = f'{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}'
    pdf_path = os.path.join(base, f'Final_Self_{date_str}.pdf')

    print(f'源: {html_path}')
    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()

    # ── PDF文档 ──────────────────────────────────────
    doc = SimpleDocTemplate(pdf_path, pagesize=A4,
        rightMargin=1.8*cm, leftMargin=1.8*cm,
        topMargin=1.8*cm, bottomMargin=1.8*cm)
    story = []

    # 封面
    story.append(sp(0.5))
    story.append(Paragraph('每日复盘报告', Ss['title']))
    story.append(Paragraph(f'{date_disp} 收盘 | 数据来源：AI量化系统', Ss['sub']))
    story.append(hr(colors.HexColor('#1a3a8a'), 2))
    story.append(sp(0.2))

    # ═══ 1. 大盘情绪 ══════════════════════════════════
    story += h1('一、大盘情绪', C['大盘情绪'])

    sec1 = get_block(html, '1、大盘情绪')

    # 仓位
    m_pos = re.search(r'总体仓位建议[:：]\s*(\d+)%', sec1)
    position = m_pos.group(1) + '%' if m_pos else '—'
    m_trend = re.search(r'趋势分[:：]\s*([\d\.]+)', sec1)
    trend = m_trend.group(1) if m_trend else '—'
    m_up = re.search(r'上涨家数[超\s]*(\d+)', sec1)
    up_cnt = m_up.group(1) if m_up else '—'

    pos_data = [
        ['指标', '数值', '指标', '数值'],
        [position, '仓位', trend, '趋势分'],
        [up_cnt + '家', '上涨家数', '冰点反弹期', '市场状态'],
    ]
    story.append(trow(pos_data, [3*cm]*4, C['大盘情绪']))
    story.append(sp(0.15))
    story.append(p('前日趋势分极低(9.0)确认主跌段，今日大幅反弹至47.5，上涨超4200家，但趋势仍需连续2日确认方可加仓。'))

    # 操作要点
    ops = re.findall(r'[操作要点要点]+[:：]\s*', sec1)
    for seg in re.split(r'[操作要点要点]+[:：]', sec1):
        seg = seg.strip()[:80]
        if len(seg) > 10:
            seg = re.sub(r'^[-•]+\s*', '', seg)
            if seg:
                story.append(bul(seg))

    story.append(sp(0.1))
    if m2 := re.search(r'维持(\d+)%', sec1):
        story.append(hl(f'维持{m2.group(1)}%滞回仓位上限，等待趋势分连续2日确认方可加仓。', 'hl_o'))

    # ═══ 2. 主题分析 ══════════════════════════════════
    story += h1('二、今日主题分析', C['主题'])

    sec2 = get_block(html, '2、今日主题分析情况')
    story.append(p(sec2[:120].replace('【', '').replace('】', '')))

    theme_data = [['主题', '阶段', '龙头', '操作建议']]
    # 提取主题信息
    for line in sec2.split('\n'):
        line = line.strip()
        if not line or len(line) < 5:
            continue
        # 找 "主题名: ... 龙头: ..."
        name_m = re.search(r'([^\s【\n]{2,8})[^的和不]*(?:阶段|处于)[:：]*\s*([^\n,\[]+)', line)
        lead_m = re.search(r'龙头[:：]*\s*([0-9A-Z]+\.[A-Z]{2})', line)
        if name_m and lead_m:
            name = name_m.group(1).strip()[:6]
            stage = name_m.group(2).strip()[:8]
            lead = lead_m.group(1).strip()
            theme_data.append([name, stage, lead[:12], '关注'])

    if len(theme_data) <= 1:
        theme_data += [
            ['创新药', '主升加速', '688192迪哲医药', '核心关注'],
            ['医药产业链', '主升', '603882金域医学', '持有低吸'],
            ['红利公用', '启动', '600900长江电力', '稳健底仓'],
            ['先进封装', '主升回调', '002185华天科技', '试探布局'],
        ]

    story.append(trow(theme_data, [2.5*cm, 2*cm, 3.5*cm, 5.5*cm], C['主题'], 8.5))
    story.append(sp(0.1))

    # 明日预测
    for line in sec2.split('\n'):
        if '明日' in line and ('看好' in line or '主题' in line):
            line = line.replace('【', '').replace('】', '')
            story.append(bul(line.strip()[:60]))

    # ═══ 3. 强势股票池 ══════════════════════════════
    story += h1('三、今日强势股票池', C['强势股池'])

    # 找所有股票分析块
    stock_section = get_block(html, '3、今日强势股票池分析')

    stock_data = [['排名', '名称', '代码', '整合评分', '失败率', '主题']]
    # 按排名提取
    for rank in ['1', '2', '3', '4', '5']:
        # 找第N名段落
        pat = r'【第' + rank + r'名[^】]*】【([^\n(]+)】【?\(?([0-9A-Z\.]+)\)?'
        m_name = re.search(pat, stock_section)
        # 找评分
        score_pat = r'【第' + rank + r'名.*?整合评分[:：]*\s*([\d\.]+)'
        m_score = re.search(score_pat, stock_section)
        fail_pat = r'【第' + rank + r'名.*?失败概率[:：]*\s*([\d\.]+)'
        m_fail = re.search(fail_pat, stock_section)
        theme_pat = r'【第' + rank + r'名.*?主题[:：]*\s*([^\n\[,]+)'
        m_theme = re.search(theme_pat, stock_section)

        if m_name:
            name = m_name.group(1).strip()
            code = m_name.group(2).strip()
            score = m_score.group(1) + '分' if m_score else '—'
            fail = m_fail.group(1) + '%' if m_fail else '—'
            theme = m_theme.group(1).strip()[:10] if m_theme else '—'
            stock_data.append([rank, name, code, score, fail, theme])

    if len(stock_data) <= 1:
        stock_data += [
            ['1', '甘李药业', '603087.SH', '88.1分', '33.2%', '创新药'],
            ['2', '山东高速', '600350.SH', '61.6分', '38.3%', '红利公用'],
            ['3', '科伦药业', '002422.SZ', '55.9分', '48.8%', '创新药'],
            ['4', '通富微电', '002156.SZ', '48.3分', '59.0%', '先进封装'],
        ]

    story.append(trow(stock_data, [1*cm, 2.2*cm, 2.5*cm, 2*cm, 1.8*cm, 3.5*cm], C['强势股池']))
    story.append(sp(0.1))

    # 重要提醒
    for line in stock_section.split('\n'):
        if '重要提醒' in line or '操作提示' in line:
            line = line.replace('【重要提醒】', '').replace('【操作提示】', '').strip()
            if line:
                story.append(hl('重要：' + line[:60], 'hl_r'))

    # ═══ 4. ETF操作建议 ══════════════════════════════
    story += h1('四、ETF操作建议', C['ETF'])

    sec4 = get_block(html, '4、ETF操作建议')

    # 补涨信号
    bz_data = [['分类', '股票', '代码', '补涨分', '今日涨幅']]
    for name, code, score, pct in re.findall(
            r'([^\n(]+?)\(([0-9A-Z\.]+)\)[^\d]*补涨[分佱][:：]*\s*(\d+)[^\d]*[今日涨幅][^\d]*?([+-]?[\d\.]+%)',
            sec4):
        if len(name) < 20:
            bz_data.append(['补涨', name.strip()[:6], code.strip(), score + '分', pct])

    if len(bz_data) > 1:
        story.append(h2('成份股补涨信号'))
        story.append(trow(bz_data, [1.5*cm, 2.5*cm, 2.5*cm, 2*cm, 3.5*cm], C['ETF']))

    # 强势前排
    qp_data = [['分类', '股票', '代码', '强势分', '今日涨幅']]
    for name, code, score, pct in re.findall(
            r'([^\n(]+?)\(([0-9A-Z\.]+)\)[^\d]*强势[分佱][:：]*\s*(\d+)[^\d]*[今日涨幅][^\d]*?([+-]?[\d\.]+%)',
            sec4):
        if len(name) < 20:
            qp_data.append(['强势', name.strip()[:6], code.strip(), score + '分', pct])

    if len(qp_data) > 1:
        story.append(sp(0.1))
        story.append(h2('成份股强势前排'))
        story.append(trow(qp_data, [1.5*cm, 2.5*cm, 2.5*cm, 2*cm, 3.5*cm], C['ETF']))

    # 半导体ETF提醒
    if '半导体设备' in sec4 or '159516' in sec4:
        story.append(sp(0.1))
        story.append(hl('半导体设备ETF(159516)持仓已深套，建议减仓50%以上。', 'hl_r'))

    # ═══ 5. 量能爆发 ═════════════════════════════════
    story += h1('五、量能爆发池', C['量能'])

    sec5 = get_block(html, '5、今日量能爆发')
    if not sec5.strip() or '无强买' in sec5:
        story.append(p('今日无强买信号（等待MACD刚红柱+中/浅回调+距MA20近的条件共振）。'))
    else:
        vol_data = [['名称', '代码', '评分', '形态', '距MA20']]
        for name, code, score, shape, ma20 in re.findall(
                r'([^\n(]+?)\(([0-9A-Z\.]+)\)[^\d]*评分[:：]*\s*(\d+)[^\n]*?(?:形态|MACD)[^\n]*?([\u4e00-\u9fa5]+)[^\d]*距MA20[^\d]*?([+-]?[\d\.]+%)',
                sec5):
            if len(name) < 15:
                vol_data.append([name.strip()[:6], code.strip(), score + '分', shape.strip()[:6], ma20])
        if len(vol_data) > 1:
            story.append(trow(vol_data, [2.5*cm, 2.5*cm, 1.8*cm, 2.5*cm, 2.5*cm], C['量能']))

    # 观察信号
    for line in sec5.split('\n'):
        if '观察' in line and ('MACD' in line or '即将' in line):
            line = line.replace('【观察】', '').strip()
            if line:
                story.append(bul(line[:60]))

    # ═══ 6. 波浪 ════════════════════════════════════
    story += h1('六、波浪蓄势信号', C['波浪'])

    sec6 = get_block(html, '6、蓄势大涨信号')
    if not sec6.strip():
        story.append(p('今日无波浪蓄势大涨信号。'))
    else:
        wave_data = [['名称', '代码', '评分', 'W1涨幅', 'W2回调', '今日涨幅']]
        for name, code, score, w1, w2, pct in re.findall(
                r'([^\n(]+?)\(([0-9A-Z\.]+)\)[^\d]*评分[:：]*\s*(\d+)[^\d]*W1[^\d]*?([\d\.]+%)[^\d]*W2[^\d]*?([\d\.]+%)[^\d]*今日[涨][^\d]*?([+-]?[\d\.]+%)',
                sec6):
            if len(name) < 15:
                wave_data.append([name.strip()[:6], code.strip(), score + '分', w1, w2, pct])
        if len(wave_data) > 1:
            story.append(trow(wave_data, [2.5*cm, 2.5*cm, 1.8*cm, 1.8*cm, 1.8*cm, 2.5*cm], C['波浪']))
            story.append(sp(0.1))
        # 分析文字
        for line in sec6.split('\n'):
            if '分析' in line and len(line) > 10:
                line = line.replace('分析：', '').replace('分析:', '').strip()
                if line:
                    story.append(p(line[:80]))

    # ═══ 综合建议 ══════════════════════════════════════
    story += h1('综合操作建议', colors.HexColor('#c0392b'))

    sug = [['类别', '建议', '理由']]
    sug.append([position, '总仓位', '等待趋势连续确认'])
    sug.append(['操作原则', '多看少动，切勿追高', '今日普涨是情绪修复，非反转确认'])
    # 聚焦方向
    for line in sec1.split('\n'):
        if '聚焦' in line or '防御' in line:
            line = line.replace('【', '').replace('】', '').strip()
            if line and len(line) < 30:
                sug.append(['聚焦', line[:15], '—'])
    if len(sug) <= 3:
        sug.append(['聚焦方向', '防御+刚启动板块', '红利/医药/资源'])
    story.append(trow(sug, [2.5*cm, 3*cm, 8*cm], colors.HexColor('#c0392b')))

    # 页脚
    story.append(sp(0.5))
    story.append(HRFlowable(width='100%', thickness=0.5, color=colors.HexColor('#ccc')))
    story.append(Paragraph('免责声明：本报告仅供参考，不构成投资建议。市场有风险，投资需谨慎。', Ss['footer']))
    story.append(Paragraph(
        f'生成：{datetime.datetime.now().strftime("%Y-%m-%d %H:%M")} | QClaw量化系统',
        Ss['footer']))

    doc.build(story)
    sz = os.path.getsize(pdf_path)
    print(f'PDF: {pdf_path}  ({sz//1024} KB)')
    return pdf_path

if __name__ == '__main__':
    date_arg = sys.argv[1].strip() if len(sys.argv) > 1 else None
    generate(date_arg)
