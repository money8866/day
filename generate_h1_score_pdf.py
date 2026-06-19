#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成半年报超预期评分PDF报告
"""

import json
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm, cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# 注册中文字体
font_paths = [
    r'C:\Windows\Fonts\msyh.ttc',
    r'C:\Windows\Fonts\msyh.ttf',
    r'C:\Windows\Fonts\simsun.ttc',
]
registered = False
for fp in font_paths:
    if os.path.exists(fp):
        try:
            pdfmetrics.registerFont(TTFont('ChineseFont', fp))
            registered = True
            break
        except:
            continue

if not registered:
    print("No Chinese font found!")
    exit(1)

styles = getSampleStyleSheet()
styles.add(ParagraphStyle('CN', fontName='ChineseFont', fontSize=9, leading=12))
styles.add(ParagraphStyle('Title2', fontName='ChineseFont', fontSize=16, leading=20, alignment=1, spaceAfter=10))
styles.add(ParagraphStyle('Sub2', fontName='ChineseFont', fontSize=10, leading=14, spaceAfter=6))

with open(r'D:\mystock\solo\report_daily\h1_超预期评分v7_20260619.json', 'r', encoding='utf-8') as f:
    d = json.load(f)

doc = SimpleDocTemplate(
    r'D:\mystock\solo\report_daily\半年报超预期评分_20260619.pdf',
    pagesize=A4, leftMargin=15*mm, rightMargin=15*mm,
    topMargin=15*mm, bottomMargin=15*mm)

elements = []

elements.append(Paragraph('半年报超预期评分系统 v7', styles['Title2']))
elements.append(Paragraph('2026-06-19 | IA+IB池69只 | 评分>=6分: {}只'.format(d['scored_ge6']), styles['Sub2']))
elements.append(Spacer(1, 3*mm))
elements.append(Paragraph('评分规则: Q1同比>50%(+2) + H1>Q4(+2) + H1同比加速(+3) + Q1动量正(+2) + Q1>100%(+1)', styles['CN']))
elements.append(Paragraph('H1同比加速定义: 2026H1预测同比 > 2025H1实际同比 (中报增速比去年中报更快)', styles['CN']))
elements.append(Paragraph('Q4=年报累计-三季报累计 (真实单季值)', styles['CN']))
elements.append(Spacer(1, 5*mm))

# 按分数和加速幅度排序
scored = d['results']

# 表格
header = ['排名', '池', '名称', '代码', '主题', '评分',
           'Q1同比\n(26vs25)', 'Q1动量', 'H1预测\n同比',
           'H1加速', 'Q4\n(亿)', 'H1预测\n(亿)', 'H1净利\n(亿)', '市值\n(亿)', 'PE']

data = [header]
for i, r in enumerate(scored):
    pt = r['pool']
    code6 = r['code'][:6]
    theme = r.get('theme', '')[:6]
    q1y = '{:.0f}%'.format(r.get('q1_26_yoy',0) or 0)
    q1m = '{:+.0f}%'.format(r.get('q1_mom',0) or 0)
    h1y = '{:.0f}%'.format(r.get('h1_26_yoy',0) or 0)
    ha = '{:+.0f}%'.format(r.get('h1_accel',0) or 0)
    q4 = '{:.1f}'.format(r.get('q4_25r',0) or 0)
    h1 = '{:.1f}'.format(r.get('h1_26r',0) or 0)
    ni = '{:.1f}'.format(r.get('h1_26n',0) or 0) if r.get('h1_26n') else '-'
    cap = '{:.0f}'.format(r.get('market_cap_yi',0) or 0)
    pe = '{:.0f}'.format(r.get('pe',0) or 0) if r.get('pe') else '-'

    data.append([str(i+1), pt, r['name'], code6, theme, str(r['score']),
                 q1y, q1m, h1y, ha, q4, h1, ni, cap, pe])

# 表格样式
col_widths = [18, 20, 42, 38, 45, 18, 42, 35, 42, 35, 30, 35, 35, 30, 28]

style_cmds = [
    ('FONTNAME', (0, 0), (-1, -1), 'ChineseFont'),
    ('FONTSIZE', (0, 0), (-1, -1), 6),
    ('LEADING', (0, 0), (-1, -1), 8),
    ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.2, 0.3, 0.5)),
    ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
    ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
    ('ALIGN', (0, 1), (0, -1), 'CENTER'),
    ('ALIGN', (1, 1), (1, -1), 'CENTER'),
    ('ALIGN', (3, 1), (5, -1), 'CENTER'),
    ('ALIGN', (7, 1), (-1, -1), 'CENTER'),
    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ('GRID', (0, 0), (-1, -1), 0.3, colors.grey),
    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.Color(0.95, 0.95, 0.98)]),
]

# 高亮满分(10分)
for i, r in enumerate(scored):
    if r['score'] >= 10:
        style_cmds.append(('BACKGROUND', (5, i+1), (5, i+1), colors.Color(1, 0.85, 0.3)))

t = Table(data, colWidths=col_widths)
t.setStyle(TableStyle(style_cmds))
elements.append(t)

elements.append(Spacer(1, 8*mm))
elements.append(Paragraph('备注: H1预测 = Q1_2026 × (H1_2025/Q1_2025历史比例). 松发股份因重组数据异常请剔除.', styles['CN']))

doc.build(elements)
print("PDF生成: {} ({} KB)".format(
    doc.filename, os.path.getsize(doc.filename) // 1024))
