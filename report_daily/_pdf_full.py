# -*- coding: utf-8 -*-
"""每日复盘PDF报告 - 完整解析MD内容"""
import os, re, sys
from datetime import datetime

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT

# 注册中文字体
for font_path in [r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\simhei.ttf']:
    if os.path.exists(font_path):
        try:
            pdfmetrics.registerFont(TTFont('ChineseFont', font_path))
            break
        except: pass

chinese_font = 'ChineseFont'

# 样式
styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name='CTitle', fontName=chinese_font, fontSize=20, spaceAfter=6, alignment=TA_CENTER, textColor=colors.HexColor('#1a3a8a')))
styles.add(ParagraphStyle(name='CDate', fontName=chinese_font, fontSize=11, spaceAfter=12, alignment=TA_CENTER, textColor=colors.HexColor('#666666')))
styles.add(ParagraphStyle(name='CH1', fontName=chinese_font, fontSize=13, spaceAfter=8, spaceBefore=14, textColor=colors.HexColor('#1a3a8a')))
styles.add(ParagraphStyle(name='CH2', fontName=chinese_font, fontSize=11, spaceAfter=6, spaceBefore=8, textColor=colors.HexColor('#2c5f9e')))
styles.add(ParagraphStyle(name='CBody', fontName=chinese_font, fontSize=10, spaceAfter=4, leading=15, textColor=colors.HexColor('#2c3e50')))
styles.add(ParagraphStyle(name='CBullet', fontName=chinese_font, fontSize=10, spaceAfter=3, leftIndent=16, leading=15, textColor=colors.HexColor('#34495e')))
styles.add(ParagraphStyle(name='CHighlight', fontName=chinese_font, fontSize=10, spaceAfter=4, leading=15, textColor=colors.HexColor('#c0392b'), backColor=colors.HexColor('#fdf2f2')))
styles.add(ParagraphStyle(name='CFooter', fontName=chinese_font, fontSize=8, spaceAfter=2, textColor=colors.HexColor('#999999')))
styles.add(ParagraphStyle(name='CSmall', fontName=chinese_font, fontSize=9, spaceAfter=3, leading=13, textColor=colors.HexColor('#555555')))

def clean(t):
    """清理HTML标签"""
    t = re.sub(r'<span[^>]*>', '', t)
    t = re.sub(r'</span>', '', t)
    t = re.sub(r'<b>([^<]*)</b>', r'<b>\1</b>', t)
    t = re.sub(r'<[^>]+>', '', t)
    t = re.sub(r'\*\*([^*]*)\*\*', r'<b>\1</b>', t)
    return t.strip()

def make_table(data, col_widths, header_color):
    t = Table(data, colWidths=col_widths)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), header_color),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTNAME', (0, 0), (-1, -1), chinese_font),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f8f9fa')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#bdc3c7')),
    ]))
    return t

def generate_pdf(md_path, out_path=None):
    """解析MD并生成完整PDF"""
    with open(md_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # 提取日期
    date_str = datetime.now().strftime('%Y%m%d')
    for line in lines[:20]:
        m = re.search(r'每日复盘\((\d{8})\)', line)
        if m:
            date_str = m.group(1)
            break
    date_disp = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

    if out_path is None:
        out_path = md_path.replace('.md', '.pdf')

    doc = SimpleDocTemplate(out_path, pagesize=A4,
        rightMargin=1.8*cm, leftMargin=1.8*cm, topMargin=1.8*cm, bottomMargin=1.8*cm)

    story = []

    # 标题
    story.append(Spacer(1, 0.4*cm))
    story.append(Paragraph('每日复盘报告', styles['CTitle']))
    story.append(Paragraph(date_disp, styles['CDate']))
    story.append(Spacer(1, 0.2*cm))

    # 逐行解析
    section_num = 0
    in_stock_detail = False
    stock_data = []

    for line in lines:
        line = line.rstrip()
        if not line or line.startswith('==='):
            continue

        # 一级标题：**1、xxx** 或 1、**xxx**
        m = re.match(r'^\*\*(\d+)[、.](.+?)\*\*', line)
        if not m:
            m = re.match(r'^(\d+)[、.]\*\*(.+?)\*\*', line)
        if m:
            section_num = int(m.group(1))
            title = m.group(2).strip()
            cn_num = ['一', '二', '三', '四', '五', '六', '七', '八'][section_num - 1]
            story.append(Paragraph(f'{cn_num}、{title}', styles['CH1']))
            in_stock_detail = False
            continue

        # 二级标题：**xxx**：
        m = re.match(r'^\*\*([^*]+)\*\*[：:]', line)
        if m:
            title = m.group(1).strip()
            story.append(Paragraph(title, styles['CH2']))
            continue

        # 列表项
        if line.startswith('- ') or line.startswith('* '):
            txt = clean(line[2:])
            if not txt:
                continue
            # 高亮显示仓位/重要信息
            if '仓位' in txt or '建议' in txt or '警告' in txt or '风险' in txt:
                story.append(Paragraph('• ' + txt, styles['CHighlight']))
            else:
                story.append(Paragraph('• ' + txt, styles['CBullet']))
            continue

        # 股票详细分析行（包含代码）
        if re.search(r'\d{6}\.(SZ|SH)', line):
            txt = clean(line)
            # 检查是否是【股票名】格式
            if '【' in txt and '】' in txt:
                story.append(Paragraph(txt, styles['CBody']))
            else:
                story.append(Paragraph(txt, styles['CSmall']))
            continue

        # 普通段落
        if len(line) > 3 and not line.startswith('#'):
            txt = clean(line)
            if txt and not txt.startswith('这是大盘'):
                # 高亮仓位相关
                if '仓位' in txt and '%' in txt:
                    story.append(Paragraph(txt, styles['CHighlight']))
                else:
                    story.append(Paragraph(txt, styles['CBody']))

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
        generate_pdf(md_path, out_path)
