#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成V4.9 SQLite版扫描报告PDF"""

import json
import os
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# 注册中文字体
FONT_PATH = r'C:\Windows\Fonts\SimHei.ttf'
if os.path.exists(FONT_PATH):
    pdfmetrics.registerFont(TTFont('SimHei', FONT_PATH))

# 读取结果
with open(r'D:\mystock\solo\report_daily\v49_sqlite_scan_20260619.json', 'r', encoding='utf-8') as f:
    results = json.load(f)

# 创建PDF
pdf_file = r'D:\mystock\solo\report_daily\V49_SQLite扫描报告_20260619.pdf'
doc = SimpleDocTemplate(pdf_file, pagesize=A4, topMargin=1.5*cm, bottomMargin=1.5*cm)

# 样式
styles = getSampleStyleSheet()
title_style = ParagraphStyle('Title', fontName='SimHei', fontSize=18, spaceAfter=20, alignment=1)
body_style = ParagraphStyle('Body', fontName='SimHei', fontSize=11, spaceAfter=12)
subtitle_style = ParagraphStyle('Subtitle', fontName='SimHei', fontSize=14, spaceAfter=10, textColor=colors.darkblue)

# 构建内容
content = []

# 标题
content.append(Paragraph('V4.9 全市场上涨空间扫描报告（SQLite主题匹配版）', title_style))
content.append(Paragraph(f'扫描日期：{datetime.now().strftime("%Y-%m-%d %H:%M")}', body_style))
content.append(Spacer(1, 0.5*cm))

# 摘要
buy_count = sum(1 for r in results if r['signal'] == 'BUY')
watch_count = sum(1 for r in results if r['signal'] == 'WATCH')
breakout_count = sum(1 for r in results if r['mode'] == 'BREAKOUT')

summary = f'''
<b>📊 扫描结果摘要</b><br/>
总扫描股票：{len(results)}只<br/>
BUY信号：{buy_count}只（突破模式）<br/>
WATCH信号：{watch_count}只<br/>
BREAKOUT模式：{breakout_count}只<br/>
<br/>
<b>✅ SQLite主题匹配版改进</b><br/>
1. 读取SQLite缓存进行完整主题匹配（360只股票）<br/>
2. 主题加成生效：A级核心股 +20%<br/>
3. BUY阈值：≥85分，量比>1.3硬约束
'''
content.append(Paragraph(summary, body_style))
content.append(Spacer(1, 0.5*cm))

# BUY信号表格
content.append(Paragraph('🎯 BUY信号列表', subtitle_style))
buy_stocks = [r for r in results if r['signal'] == 'BUY']
if buy_stocks:
    table_data = [['排名', '代码', '名称', '收盘', '模式', '评分', '量比', '主题加成', '预估涨幅']]
    for i, r in enumerate(buy_stocks, 1):
        table_data.append([
            str(i),
            r['ts_code'],
            r['name'],
            f'{r["close"]:.2f}',
            '突破',
            f'{r["score"]:.1f}',
            f'{r["vol_ratio"]:.2f}',
            f'{r["theme_bonus"]:.2f}',
            f'{r["upside"]}%'
        ])

    table = Table(table_data, colWidths=[1*cm, 2.5*cm, 2.5*cm, 1.5*cm, 1.2*cm, 1.5*cm, 1.5*cm, 2*cm, 2*cm])
    table.setStyle(TableStyle([
        ('FONTNAME', (0,0), (-1,-1), 'SimHei'),
        ('FONTSIZE', (0,0), (-1,0), 10),
        ('FONTSIZE', (0,1), (-1,-1), 9),
        ('BACKGROUND', (0,0), (-1,0), colors.lightblue),
        ('TEXTCOLOR', (0,0), (-1,0), colors.black),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.whitesmoke, colors.white])
    ]))
    content.append(table)
else:
    content.append(Paragraph('今日无BUY信号', body_style))

content.append(Spacer(1, 0.5*cm))

# WATCH TOP20
content.append(Paragraph('🔍 WATCH信号TOP20', subtitle_style))
watch_stocks = [r for r in results if r['signal'] == 'WATCH'][:20]
if watch_stocks:
    table_data = [['排名', '代码', '名称', '收盘', '模式', '评分', '量比', '主题加成', '预估涨幅']]
    for i, r in enumerate(watch_stocks, 1):
        mode_str = '突破' if r['mode'] == 'BREAKOUT' else '回踩'
        table_data.append([
            str(i),
            r['ts_code'],
            r['name'],
            f'{r["close"]:.2f}',
            mode_str,
            f'{r["score"]:.1f}',
            f'{r["vol_ratio"]:.2f}',
            f'{r["theme_bonus"]:.2f}',
            f'{r["upside"]}%'
        ])

    table = Table(table_data, colWidths=[1*cm, 2.5*cm, 2.5*cm, 1.5*cm, 1.2*cm, 1.5*cm, 1.5*cm, 2*cm, 2*cm])
    table.setStyle(TableStyle([
        ('FONTNAME', (0,0), (-1,-1), 'SimHei'),
        ('FONTSIZE', (0,0), (-1,0), 10),
        ('FONTSIZE', (0,1), (-1,-1), 9),
        ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
        ('TEXTCOLOR', (0,0), (-1,0), colors.black),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.whitesmoke, colors.white])
    ]))
    content.append(table)

# 生成PDF
doc.build(content)
print(f'✅ PDF报告已生成：{pdf_file}')
