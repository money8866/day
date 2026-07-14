# -*- coding: utf-8 -*-
"""HTML 转 PDF - 解析HTML结构并用reportlab渲染"""
import sys, os, re
from datetime import datetime

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("安装: pip install beautifulsoup4")
    sys.exit(1)

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
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
styles.add(ParagraphStyle(name='CDate', fontName=chinese_font, fontSize=11, spaceAfter=12, alignment=TA_CENTER, textColor=colors.HexColor('#666')))
styles.add(ParagraphStyle(name='CH1', fontName=chinese_font, fontSize=13, spaceAfter=8, spaceBefore=14, textColor=colors.HexColor('#1a3a8a')))
styles.add(ParagraphStyle(name='CH2', fontName=chinese_font, fontSize=11, spaceAfter=6, spaceBefore=8, textColor=colors.HexColor('#2c5f9e')))
styles.add(ParagraphStyle(name='CBody', fontName=chinese_font, fontSize=10, spaceAfter=4, leading=15, textColor=colors.HexColor('#2c3e50')))
styles.add(ParagraphStyle(name='CBullet', fontName=chinese_font, fontSize=10, spaceAfter=3, leftIndent=16, leading=15, textColor=colors.HexColor('#34495e')))
styles.add(ParagraphStyle(name='CHighlight', fontName=chinese_font, fontSize=10, spaceAfter=4, leading=15, textColor=colors.HexColor('#c0392b'), backColor=colors.HexColor('#fdf2f2')))
styles.add(ParagraphStyle(name='CStock', fontName=chinese_font, fontSize=9, spaceAfter=3, leftIndent=20, leading=13, textColor=colors.HexColor('#555')))
styles.add(ParagraphStyle(name='CFooter', fontName=chinese_font, fontSize=8, textColor=colors.HexColor('#999')))

def clean_text(text):
    """清理文本"""
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
    return text.strip()

def process_element(elem, story, level=0):
    """递归处理HTML元素"""
    if elem.name == 'p':
        text = elem.get_text(strip=True)
        if not text or text.startswith('这是大盘'):
            return

        # 检查是否包含一级标题
        strong = elem.find('strong')
        if strong and re.match(r'^\d+[、.]', strong.get_text()):
            # 一级标题
            m = re.match(r'^(\d+)[、.]', strong.get_text())
            if m:
                num = int(m.group(1))
                cn = ['一', '二', '三', '四', '五', '六', '七', '八'][num-1]
                title = strong.get_text().strip()
                story.append(Paragraph(f'{cn}、{title[num>9 and 3 or 2:]}', styles['CH1']))
                # 处理剩余内容
                rest = text[len(strong.get_text()):].strip()
                if rest:
                    process_text(rest, story)
        elif '仓位' in text and '%' in text:
            story.append(Paragraph(clean_text(text), styles['CHighlight']))
        else:
            process_text(text, story)

    elif elem.name == 'ul':
        for li in elem.find_all('li', recursive=False):
            text = li.get_text(strip=True)
            if text:
                # 检查是否是股票详情（嵌套ul）
                nested = li.find('ul')
                if nested:
                    # 主标题
                    main_text = ''.join([t for t in li.children if isinstance(t, str)]).strip()
                    if main_text:
                        story.append(Paragraph('• ' + clean_text(main_text), styles['CBullet']))
                    # 嵌套内容
                    for sub_li in nested.find_all('li'):
                        sub_text = sub_li.get_text(strip=True)
                        if sub_text:
                            story.append(Paragraph('  ○ ' + clean_text(sub_text), styles['CStock']))
                else:
                    story.append(Paragraph('• ' + clean_text(text), styles['CBullet']))

    elif elem.name == 'table':
        # 处理表格
        rows = []
        for tr in elem.find_all('tr'):
            cells = [clean_text(td.get_text()) for td in tr.find_all(['th', 'td'])]
            if cells:
                rows.append(cells)
        if rows:
            col_count = len(rows[0])
            col_widths = [15/col_count * cm] * col_count
            t = Table(rows, colWidths=col_widths)
            t.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1677ff')),
                ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
                ('FONTNAME', (0,0), (-1,-1), chinese_font),
                ('FONTSIZE', (0,0), (-1,-1), 8),
                ('ALIGN', (0,0), (-1,-1), 'CENTER'),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#bdc3c7')),
                ('BACKGROUND', (0,1), (-1,-1), colors.HexColor('#f8f9fa')),
            ]))
            story.append(t)

def process_text(text, story):
    """处理文本段落"""
    text = clean_text(text)
    if not text:
        return

    # 检查是否包含重要标记
    if '仓位' in text or '建议' in text or '警告' in text:
        story.append(Paragraph(text, styles['CHighlight']))
    else:
        story.append(Paragraph(text, styles['CBody']))

def html_to_pdf(html_path, pdf_path=None):
    """HTML转PDF"""
    with open(html_path, 'r', encoding='utf-8') as f:
        html_content = f.read()

    soup = BeautifulSoup(html_content, 'html.parser')

    # 提取日期
    date_str = datetime.now().strftime('%Y%m%d')
    for p in soup.find_all('p'):
        text = p.get_text()
        m = re.search(r'每日复盘\((\d{8})\)', text)
        if m:
            date_str = m.group(1)
            break

    date_disp = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

    if pdf_path is None:
        pdf_path = html_path.replace('.html', '.pdf')

    doc = SimpleDocTemplate(pdf_path, pagesize=A4,
        rightMargin=1.8*cm, leftMargin=1.8*cm, topMargin=1.8*cm, bottomMargin=1.8*cm)

    story = []

    # 标题
    story.append(Spacer(1, 0.4*cm))
    story.append(Paragraph('每日复盘报告', styles['CTitle']))
    story.append(Paragraph(date_disp, styles['CDate']))
    story.append(Spacer(1, 0.2*cm))

    # 解析body内容
    body = soup.find('body')
    if body:
        for elem in body.children:
            if hasattr(elem, 'name'):
                process_element(elem, story)

    # 页脚
    story.append(Spacer(1, 0.5*cm))
    story.append(Paragraph(f'报告生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M")}', styles['CFooter']))
    story.append(Paragraph('QClaw | 免责声明：本报告仅供参考，不构成投资建议', styles['CFooter']))

    doc.build(story)
    print(f"PDF生成完成: {pdf_path}")
    return pdf_path

if __name__ == '__main__':
    html_path = sys.argv[1] if len(sys.argv) > 1 else None
    pdf_path = sys.argv[2] if len(sys.argv) > 2 else None
    if html_path and os.path.exists(html_path):
        html_to_pdf(html_path, pdf_path)
