# -*- coding: utf-8 -*-
"""
每日复盘PDF报告生成器 - 从MD文件解析生成
用法: python _pdf_from_md.py [MD文件路径] [输出PDF路径]
"""
import os, re, datetime, sys

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT

# 字体注册
FONT = 'Chinese'
for font_path in [r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\simhei.ttf']:
    if os.path.exists(font_path):
        try:
            pdfmetrics.registerFont(TTFont(FONT, font_path))
            break
        except: pass

# 样式
styles = getSampleStyleSheet()
S = {}
S['title']  = ParagraphStyle('T', fontName=FONT, fontSize=20, spaceAfter=6, alignment=TA_CENTER, textColor=colors.HexColor('#1a3a8a'))
S['sub']    = ParagraphStyle('Su', fontName=FONT, fontSize=11, spaceAfter=4, alignment=TA_CENTER, textColor=colors.HexColor('#666666'))
S['h1']     = ParagraphStyle('H1', fontName=FONT, fontSize=13, spaceAfter=8, spaceBefore=14, textColor=colors.HexColor('#1a3a8a'))
S['h2']     = ParagraphStyle('H2', fontName=FONT, fontSize=11, spaceAfter=5, spaceBefore=8, textColor=colors.HexColor('#2c5f9e'))
S['body']   = ParagraphStyle('B', fontName=FONT, fontSize=10, spaceAfter=4, leading=14, textColor=colors.HexColor('#222222'))
S['bullet'] = ParagraphStyle('Bu', fontName=FONT, fontSize=10, spaceAfter=3, leftIndent=16, leading=14, textColor=colors.HexColor('#333333'))
S['hl_red'] = ParagraphStyle('HLR', fontName=FONT, fontSize=10, spaceAfter=4, leading=14, textColor=colors.HexColor('#c0392b'), backColor=colors.HexColor('#fff5f5'))
S['warn']   = ParagraphStyle('W', fontName=FONT, fontSize=9.5, spaceAfter=3, leftIndent=16, leading=13, textColor=colors.HexColor('#c0392b'))
S['footer'] = ParagraphStyle('F', fontName=FONT, fontSize=8, spaceAfter=2, textColor=colors.HexColor('#aaaaaa'))

def clean(t):
    """清理HTML标签和特殊字符"""
    t = re.sub(r'<span[^>]*>', '', t)
    t = re.sub(r'</span>', '', t)
    t = re.sub(r'<b>', '**', t)
    t = re.sub(r'</b>', '**', t)
    t = re.sub(r'<[^>]+>', '', t)
    return t.strip()

def sp(h=0.3):
    return Spacer(1, h*cm)

def h1(t):
    return Paragraph(t, S['h1'])

def h2(t):
    return Paragraph(t, S['h2'])

def body(t):
    return Paragraph(clean(t), S['body'])

def bullet(t):
    txt = clean(t)
    if not txt.startswith('•') and not txt.startswith('-'):
        txt = '• ' + txt
    return Paragraph(txt, S['bullet'])

def make_table(data, col_widths, header_color):
    t = Table(data, colWidths=col_widths)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), header_color),
        ('TEXTCOLOR',  (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN',      (0,0), (-1,-1), 'CENTER'),
        ('VALIGN',     (0,0), (-1,-1), 'MIDDLE'),
        ('FONTNAME',   (0,0), (-1,-1), FONT),
        ('FONTSIZE',   (0,0), (-1,-1), 9),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#cccccc')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor('#f8f9fa'), colors.HexColor('#ffffff')]),
    ]))
    return t

def parse_md_to_pdf(md_path, out_path=None):
    """解析MD文件并生成PDF"""
    with open(md_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # 提取日期
    date_str = datetime.date.today().strftime('%Y%m%d')
    for line in lines[:10]:
        m = re.search(r'每日复盘\((\d{8})\)', line)
        if m:
            date_str = m.group(1)
            break
    date_disp = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

    if out_path is None:
        out_path = md_path.replace('.md', '.pdf')

    doc = SimpleDocTemplate(out_path, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
    story = []

    # 封面
    story.append(sp(0.5))
    story.append(Paragraph('每日复盘报告', S['title']))
    story.append(Paragraph(date_disp, S['sub']))
    story.append(sp(0.3))

    # 解析MD内容
    current_section = ''
    for i, line in enumerate(lines):
        line = line.rstrip()
        if not line:
            continue

        # 一级标题 **1、xxx**
        if re.match(r'^\*\*\d+[、.]', line):
            title = clean(line)
            story.append(h1(title))

        # 二级标题 **xxx**：
        elif re.match(r'^\*\*[^*]+\*\*：', line):
            title = clean(line)
            story.append(h2(title))

        # 列表项 - xxx 或 * xxx
        elif line.startswith('- ') or line.startswith('* '):
            txt = clean(line[2:])
            if txt:
                story.append(bullet(txt))

        # 普通段落
        elif not line.startswith('#') and len(line) > 5:
            txt = clean(line)
            if txt and not txt.startswith('==='):
                # 检查是否包含重要提示
                if '仓位' in txt or '建议' in txt:
                    story.append(Paragraph(txt, S['hl_red']))
                else:
                    story.append(body(txt))

    # 页脚
    story.append(sp(0.5))
    story.append(Paragraph(f'生成时间: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M")} | QClaw', S['footer']))
    story.append(Paragraph('免责声明: 本报告仅供参考，不构成投资建议', S['footer']))

    doc.build(story)
    print(f"PDF生成完成: {out_path}")
    return out_path

if __name__ == '__main__':
    md_path = sys.argv[1] if len(sys.argv) > 1 else None
    out_path = sys.argv[2] if len(sys.argv) > 2 else None
    if md_path and os.path.exists(md_path):
        parse_md_to_pdf(md_path, out_path)
    else:
        print("用法: python _pdf_from_md.py <md文件路径> [输出pdf路径]")
