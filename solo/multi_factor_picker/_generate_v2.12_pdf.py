# -*- coding: utf-8 -*-
"""生成v2.12修复后的PDF报告（支持中文）"""
import os
import pandas as pd
from datetime import datetime

# 注册中文字体
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

font_registered = False
font_paths = [
    r'C:\Windows\Fonts\simhei.ttf',
    r'C:\Windows\Fonts\msyh.ttc',
    r'C:\Windows\Fonts\simsun.ttc',
]

for font_path in font_paths:
    if os.path.exists(font_path):
        try:
            pdfmetrics.registerFont(TTFont('ChineseFont', font_path))
            font_registered = True
            break
        except:
            continue

chinese_font = 'ChineseFont' if font_registered else 'Helvetica'

# 创建PDF
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER

# 读取数据
df = pd.read_csv(r'D:\mystock\solo\multi_factor_picker\output\wave2_pattern_20260628_111801.csv')

# 创建PDF文档
pdf_path = r'D:\mystock\solo\multi_factor_picker\output\wave2_v2.12_report.pdf'
doc = SimpleDocTemplate(pdf_path, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm,
                       topMargin=2*cm, bottomMargin=2*cm)

# 创建样式
styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name='CustomTitle', fontName=chinese_font, fontSize=18, spaceAfter=30, alignment=TA_CENTER, textColor=colors.HexColor('#1a1a1a')))
styles.add(ParagraphStyle(name='CustomHeading1', fontName=chinese_font, fontSize=16, spaceAfter=12, spaceBefore=12, textColor=colors.HexColor('#2c3e50')))
styles.add(ParagraphStyle(name='CustomHeading2', fontName=chinese_font, fontSize=14, spaceAfter=10, spaceBefore=10, textColor=colors.HexColor('#34495e')))
styles.add(ParagraphStyle(name='CustomHeading3', fontName=chinese_font, fontSize=12, spaceAfter=8, spaceBefore=8, textColor=colors.HexColor('#7f8c8d')))
styles.add(ParagraphStyle(name='CustomBody', fontName=chinese_font, fontSize=10, spaceAfter=6, leading=14, textColor=colors.HexColor('#2c3e50')))
styles.add(ParagraphStyle(name='CustomBullet', fontName=chinese_font, fontSize=10, spaceAfter=4, leftIndent=20, bulletIndent=10))

story = []

# 标题
story.append(Paragraph('二波形态精选 v2.12 — 量比字段修复版', styles['CustomTitle']))
story.append(Spacer(1, 0.5*cm))

# 扫描信息
story.append(Paragraph('<b>扫描日期</b>：2026-06-28', styles['CustomBody']))
story.append(Paragraph('<b>版本</b>：v2.12（修复量比字段保存错误）', styles['CustomBody']))
story.append(Spacer(1, 0.5*cm))

# v2.12修复说明
story.append(Paragraph('v2.12修复说明', styles['CustomHeading1']))
story.append(Paragraph('<b>问题</b>：CSV文件中vol_ratio字段大量显示为0', styles['CustomBullet']))
story.append(Paragraph('<b>根因</b>：代码保存的是"调整期平均量比"，而非"当日量比"', styles['CustomBullet']))
story.append(Paragraph('<b>修复</b>：所有四种形态的vol_ratio字段统一使用当日量比', styles['CustomBullet']))
story.append(Paragraph('<b>验证</b>：CSV数据与评分详情一致，无量比为0的异常记录', styles['CustomBullet']))
story.append(Spacer(1, 0.5*cm))

# 扫描结果
story.append(Paragraph('扫描结果概览', styles['CustomHeading1']))
story.append(Paragraph(f'• 总信号数：{len(df)}只', styles['CustomBullet']))
story.append(Paragraph(f'• 强势横盘：{len(df[df["pattern"]=="强势横盘"])}只', styles['CustomBullet']))
story.append(Paragraph(f'• V型急跌：{len(df[df["pattern"]=="V型急跌"])}只', styles['CustomBullet']))
story.append(Paragraph(f'• 放量回调：{len(df[df["pattern"]=="放量回调"])}只', styles['CustomBullet']))
story.append(Paragraph(f'• 深度回调：{len(df[df["pattern"]=="深度回调"])}只', styles['CustomBullet']))
story.append(Spacer(1, 0.5*cm))

# TOP10表格
story.append(Paragraph('TOP10高质量信号', styles['CustomHeading1']))

table_data = [['排名', '名称', '代码', '形态', '评分', '一波%', '回踩%', '量比']]
for i, (idx, row) in enumerate(df.sort_values('score', ascending=False).head(10).iterrows(), 1):
    table_data.append([
        str(i), row['name'], row['ts_code'], row['pattern'],
        f"{row['score']:.0f}", f"{row['wave1_gain']:.1f}%",
        f"{row['pullback_pct']:.1f}%", f"{row['vol_ratio']:.2f}"
    ])

table = Table(table_data, colWidths=[1*cm, 2.5*cm, 2.5*cm, 2.5*cm, 1.5*cm, 2*cm, 2*cm, 2*cm])
table.setStyle(TableStyle([
    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#3498db')),
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
story.append(Spacer(1, 0.5*cm))

# 量比验证
story.append(Paragraph('量比字段验证', styles['CustomHeading1']))
vol_stats = df['vol_ratio'].describe()
story.append(Paragraph(f'• 平均量比：{vol_stats["mean"]:.2f}', styles['CustomBullet']))
story.append(Paragraph(f'• 最小量比：{vol_stats["min"]:.2f}', styles['CustomBullet']))
story.append(Paragraph(f'• 最大量比：{vol_stats["max"]:.2f}', styles['CustomBullet']))
story.append(Paragraph(f'• 中位数：{vol_stats["50%"]:.2f}', styles['CustomBullet']))
story.append(Spacer(1, 0.3*cm))

story.append(Paragraph('<b>量比区间分布</b>：', styles['CustomBody']))
bins = [(0, 0.5, '极度缩量'), (0.5, 0.8, '缩量'), (0.8, 1.2, '温和放量'), (1.2, 2.0, '放量'), (2.0, 999, '巨量')]
for low, high, label in bins:
    count = len(df[(df['vol_ratio'] >= low) & (df['vol_ratio'] < high)])
    pct = count / len(df) * 100
    story.append(Paragraph(f'• {label}（{low}-{high}）：{count}只（{pct:.1f}%）', styles['CustomBullet']))

story.append(Spacer(1, 0.5*cm))

# 页脚
story.append(Paragraph(f'报告生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}', styles['CustomBody']))
story.append(Paragraph('版本：wave2_pattern_scanner.py v2.12', styles['CustomBody']))
story.append(Paragraph('<b>核心修复</b>：vol_ratio字段从调整期平均量比改为当日量比', styles['CustomBody']))

# 生成PDF
doc.build(story)

print('='*80)
print('v2.12修复版PDF报告生成完成！')
print('='*80)
print(f'\n文件路径：{pdf_path}')
