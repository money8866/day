# -*- coding: utf-8 -*-
"""生成基本面信息PDF报告"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

import pandas as pd
from datetime import datetime
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
import os

print('生成PDF报告...')

# 注册中文字体
for font_path in [r'C:\Windows\Fonts\simhei.ttf', r'C:\Windows\Fonts\msyh.ttc']:
    if os.path.exists(font_path):
        try:
            pdfmetrics.registerFont(TTFont('ChineseFont', font_path))
            break
        except:
            continue

chinese_font = 'ChineseFont'

# 读取数据
df = pd.read_csv(r'D:\mystock\solo\multi_factor_picker\output\basic_info_auto_20260629.csv')

# 创建PDF
pdf_path = r'D:\mystock\solo\multi_factor_picker\output\fundamental_info_auto_20260629.pdf'
doc = SimpleDocTemplate(pdf_path, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm,
                       topMargin=2*cm, bottomMargin=2*cm)

# 样式
styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name='CTitle', fontName=chinese_font, fontSize=18,
                          spaceAfter=30, alignment=TA_CENTER, textColor=colors.HexColor('#1a1a1a')))
styles.add(ParagraphStyle(name='CH1', fontName=chinese_font, fontSize=14,
                          spaceAfter=12, spaceBefore=12, textColor=colors.HexColor('#2c3e50')))
styles.add(ParagraphStyle(name='CBody', fontName=chinese_font, fontSize=10,
                          spaceAfter=6, leading=14))

story = []

# 标题
story.append(Paragraph('基本面重要信息日报（自动挖掘版）', styles['CTitle']))
story.append(Paragraph(datetime.now().strftime('%Y-%m-%d'), styles['CTitle']))
story.append(Spacer(1, 0.5*cm))

# 概览
story.append(Paragraph('挖掘概览', styles['CH1']))
story.append(Paragraph(f'数据源：巨潮资讯网（自动爬取）', styles['CBody']))
story.append(Paragraph(f'时间范围：最近7天', styles['CBody']))
story.append(Paragraph(f'扫描股票：50只（TOP50 BullScore）', styles['CBody']))
story.append(Paragraph(f'发现重要信息：{len(df)}条', styles['CBody']))
story.append(Spacer(1, 0.5*cm))

# TOP信息表格
story.append(Paragraph('重要信息详情（按评分排序）', styles['CH1']))

# 清理HTML标签
def clean_html(text):
    import re
    text = str(text)
    text = re.sub(r'<[^>]+>', '', text)  # 移除HTML标签
    return text

# 准备表格数据
table_data = [['排名', '代码', '评分', '类别', '公告日期', '标题']]
for i, (idx, row) in enumerate(df.iterrows(), 1):
    title = clean_html(row['title'])[:30]
    categories = ', '.join(set(row['category']))
    table_data.append([
        str(i), clean_html(row['ts_code']), f"{row['weight']}分",
        categories, str(row['ann_date']), title + '...'
    ])

table = Table(table_data, colWidths=[1*cm, 2*cm, 1.5*cm, 2.5*cm, 2*cm, 4*cm])
table.setStyle(TableStyle([
    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#27ae60')),
    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ('FONTNAME', (0, 0), (-1, -1), chinese_font),
    ('FONTSIZE', (0, 0), (-1, 0), 9),
    ('FONTSIZE', (0, 1), (-1, -1), 8),
    ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
    ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#ecf0f1')),
    ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#bdc3c7')),
]))
story.append(table)

# 生成PDF
doc.build(story)

print(f'PDF已生成: {pdf_path}')
