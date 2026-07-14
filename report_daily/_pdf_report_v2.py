# -*- coding: utf-8 -*-
"""每日复盘PDF报告生成器 - 解析MD文件（复用6/26模板风格）"""
import os, re, sys
from datetime import datetime

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT

# 注册中文字体
font_registered = False
for font_path in [r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\simhei.ttf']:
    if os.path.exists(font_path):
        try:
            pdfmetrics.registerFont(TTFont('ChineseFont', font_path))
            font_registered = True
            break
        except: continue

chinese_font = 'ChineseFont' if font_registered else 'Helvetica'

# 样式
styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name='CTitle', fontName=chinese_font, fontSize=20, spaceAfter=6, alignment=TA_CENTER, textColor=colors.HexColor('#1a3a8a')))
styles.add(ParagraphStyle(name='CDate', fontName=chinese_font, fontSize=11, spaceAfter=12, alignment=TA_CENTER, textColor=colors.HexColor('#666666')))
styles.add(ParagraphStyle(name='CH1', fontName=chinese_font, fontSize=13, spaceAfter=8, spaceBefore=14, textColor=colors.HexColor('#1a3a8a')))
styles.add(ParagraphStyle(name='CH2', fontName=chinese_font, fontSize=11, spaceAfter=6, spaceBefore=8, textColor=colors.HexColor('#2c5f9e')))
styles.add(ParagraphStyle(name='CBody', fontName=chinese_font, fontSize=10, spaceAfter=4, leading=14, textColor=colors.HexColor('#2c3e50')))
styles.add(ParagraphStyle(name='CBullet', fontName=chinese_font, fontSize=10, spaceAfter=3, leftIndent=16, leading=14, textColor=colors.HexColor('#34495e')))
styles.add(ParagraphStyle(name='CHighlight', fontName=chinese_font, fontSize=10, spaceAfter=4, leading=14, textColor=colors.HexColor('#e74c3c'), backColor=colors.HexColor('#fff5f5')))
styles.add(ParagraphStyle(name='CFooter', fontName=chinese_font, fontSize=8, spaceAfter=2, textColor=colors.HexColor('#aaaaaa')))

def clean_html(t):
    """清理HTML标签"""
    t = re.sub(r'<span[^>]*>', '', t)
    t = re.sub(r'</span>', '', t)
    t = re.sub(r'<b>([^<]+)</b>', r'<b>\1</b>', t)
    t = re.sub(r'<[^>]+>', '', t)
    return t.strip()

def make_table(data, col_widths, header_color):
    """生成彩色表格"""
    t = Table(data, colWidths=col_widths)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), header_color),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTNAME', (0, 0), (-1, -1), chinese_font),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f8f9fa')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#bdc3c7')),
    ]))
    return t

def parse_md_to_pdf(md_path, out_path=None):
    """解析MD文件生成PDF"""
    with open(md_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 提取日期
    m = re.search(r'每日复盘\((\d{8})\)', content)
    date_str = m.group(1) if m else datetime.now().strftime('%Y%m%d')
    date_disp = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

    if out_path is None:
        out_path = md_path.replace('.md', '.pdf')

    doc = SimpleDocTemplate(out_path, pagesize=A4,
        rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)

    story = []
    lines = content.split('\n')

    # 标题
    story.append(Spacer(1, 0.5*cm))
    story.append(Paragraph('每日复盘报告', styles['CTitle']))
    story.append(Paragraph(date_disp, styles['CDate']))
    story.append(Spacer(1, 0.3*cm))

    # 解析内容
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # 跳过空行和分隔线
        if not line or line.startswith('==='):
            i += 1
            continue

        # 一级标题：**1、xxx**
        if re.match(r'^\*\*\d+[、.]', line):
            title = clean_html(line)
            # 转换为中文数字
            title = re.sub(r'\*\*1[、.]', '一、', title)
            title = re.sub(r'\*\*2[、.]', '二、', title)
            title = re.sub(r'\*\*3[、.]', '三、', title)
            title = re.sub(r'\*\*4[、.]', '四、', title)
            title = re.sub(r'\*\*5[、.]', '五、', title)
            title = re.sub(r'\*\*6[、.]', '六、', title)
            title = re.sub(r'\*\*7[、.]', '七、', title)
            title = re.sub(r'\*\*8[、.]', '八、', title)
            title = re.sub(r'\*\*', '', title)
            story.append(Paragraph(title, styles['CH1']))

        # 二级标题：**xxx**：
        elif re.match(r'^\*\*[^*]+\*\*[：:]', line):
            title = clean_html(line).replace('**', '').rstrip('：:')
            story.append(Paragraph(title, styles['CH2']))

        # 列表项：- xxx 或 * xxx
        elif line.startswith('- ') or line.startswith('* '):
            txt = clean_html(line[2:])
            if '仓位' in txt or '建议' in txt.lower():
                story.append(Paragraph('• ' + txt, styles['CHighlight']))
            else:
                story.append(Paragraph('• ' + txt, styles['CBullet']))

        # 股票代码行（尝试解析为表格）
        elif re.search(r'\d{6}\.(SZ|SH)', line):
            # 暂时作为普通文本处理
            txt = clean_html(line)
            story.append(Paragraph(txt, styles['CBody']))

        # 普通段落
        elif len(line) > 5 and not line.startswith('**这是'):
            txt = clean_html(line)
            if '仓位' in txt and ('%' in txt or '建议' in txt):
                story.append(Paragraph(txt, styles['CHighlight']))
            elif txt:
                story.append(Paragraph(txt, styles['CBody']))

        i += 1

    # 页脚
    story.append(Spacer(1, 0.5*cm))
    story.append(Paragraph(f'报告生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M")}', styles['CFooter']))
    story.append(Paragraph('QClaw | 免责声明：本报告仅供参考，不构成投资建议', styles['CFooter']))

    doc.build(story)
    print(f"PDF生成完成: {out_path}")
    return out_path

if __name__ == '__main__':
    md_path = sys.argv[1] if len(sys.argv) > 1 else None
    out_path = sys.argv[2] if len(sys.argv) > 2 else None
    if md_path and os.path.exists(md_path):
        parse_md_to_pdf(md_path, out_path)
    else:
        print("用法: python _pdf_report_v2.py <md文件路径> [输出pdf路径]")
