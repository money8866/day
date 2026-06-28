# -*- coding: utf-8 -*-
"""生成合格股池26号信号PDF报告"""
import os
import pandas as pd
from datetime import datetime

# 注册中文字体
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

font_registered = False
for font_path in [r'C:\Windows\Fonts\simhei.ttf', r'C:\Windows\Fonts\msyh.ttc']:
    if os.path.exists(font_path):
        try:
            pdfmetrics.registerFont(TTFont('ChineseFont', font_path))
            font_registered = True
            break
        except:
            continue

chinese_font = 'ChineseFont' if font_registered else 'Helvetica'

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER

# 读取数据
csv_path = r'D:\mystock\solo\multi_factor_picker\output\qualified_pool_20260626_signals.csv'
df = pd.read_csv(csv_path)

# 创建PDF
pdf_path = r'D:\mystock\solo\multi_factor_picker\output\qualified_pool_20260626_report.pdf'
doc = SimpleDocTemplate(pdf_path, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)

# 样式
styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name='CTitle', fontName=chinese_font, fontSize=18, spaceAfter=30, alignment=TA_CENTER, textColor=colors.HexColor('#1a1a1a')))
styles.add(ParagraphStyle(name='CH1', fontName=chinese_font, fontSize=14, spaceAfter=12, spaceBefore=12, textColor=colors.HexColor('#2c3e50')))
styles.add(ParagraphStyle(name='CBody', fontName=chinese_font, fontSize=10, spaceAfter=6, leading=14))
styles.add(ParagraphStyle(name='CBullet', fontName=chinese_font, fontSize=10, spaceAfter=4, leftIndent=20))

story = []

# 标题
story.append(Paragraph('合格股池二波形态信号结果分析', styles['CTitle']))
story.append(Paragraph('2026-06-26', styles['CTitle']))
story.append(Spacer(1, 0.5*cm))

# 概览
story.append(Paragraph('信号概览', styles['CH1']))
story.append(Paragraph(f'• 总信号数：{len(df)}只', styles['CBullet']))
story.append(Paragraph(f'• 强势横盘：{len(df[df["pattern"]=="强势横盘"])}只', styles['CBullet']))
story.append(Paragraph(f'• 深度回调：{len(df[df["pattern"]=="深度回调"])}只', styles['CBullet']))
story.append(Paragraph(f'• 放量回调：{len(df[df["pattern"]=="放量回调"])}只', styles['CBullet']))
story.append(Paragraph(f'• V型急跌：{len(df[df["pattern"]=="V型急跌"])}只', styles['CBullet']))
story.append(Spacer(1, 0.5*cm))

# 评分分布
story.append(Paragraph('评分分布', styles['CH1']))
high = len(df[df['score'] >= 40])
mid = len(df[(df['score'] >= 30) & (df['score'] < 40)])
low = len(df[df['score'] < 30])
story.append(Paragraph(f'• 高质量（≥40分）：{high}只（{high/len(df)*100:.1f}%）', styles['CBullet']))
story.append(Paragraph(f'• 中质量（30-40分）：{mid}只（{mid/len(df)*100:.1f}%）', styles['CBullet']))
story.append(Paragraph(f'• 低质量（<30分）：{low}只（{low/len(df)*100:.1f}%）', styles['CBullet']))
story.append(Spacer(1, 0.5*cm))

# 各形态TOP3
for pattern in ['强势横盘', '深度回调', '放量回调', 'V型急跌']:
    df_pattern = df[df['pattern'] == pattern].head(3)
    if len(df_pattern) > 0:
        story.append(Paragraph(f'{pattern} TOP3', styles['CH1']))

        table_data = [['排名', '名称', '代码', '评分', '一波%', '回踩%', '量比']]
        for i, (idx, row) in enumerate(df_pattern.iterrows(), 1):
            table_data.append([
                str(i), row['name'], row['ts_code'],
                f"{row['score']:.0f}", f"{row['wave1_gain']:.1f}%",
                f"{row['pullback_pct']:.1f}%", f"{row['vol_ratio']:.2f}"
            ])

        table = Table(table_data, colWidths=[1*cm, 2.5*cm, 2.5*cm, 1.5*cm, 2*cm, 2*cm, 2*cm])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#3498db')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, -1), chinese_font),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#ecf0f1')),
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#bdc3c7')),
        ]))
        story.append(table)
        story.append(Spacer(1, 0.3*cm))

# 结论
story.append(Paragraph('结论', styles['CH1']))
story.append(Paragraph(f'• 合格股池共筛选出{len(df)}只二波形态信号', styles['CBullet']))
if high > 0:
    story.append(Paragraph(f'• 高质量信号{high}只，建议优先关注', styles['CBullet']))
story.append(Paragraph(f'• 四种形态均有分布，V型急跌为主', styles['CBullet']))
story.append(Paragraph(f'• 结合BullScore评分和二波形态双重筛选', styles['CBullet']))

# 页脚
story.append(Spacer(1, 0.5*cm))
story.append(Paragraph(f'报告生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}', styles['CBody']))
story.append(Paragraph('来源：合格股池（BullScore ≥55分）', styles['CBody']))

# 生成PDF
doc.build(story)

print('='*80)
print('PDF报告生成完成！')
print('='*80)
print(f'\n文件路径：{pdf_path}')
print(f'\n信号统计：')
print(f'  总数：{len(df)}只')
print(f'  ≥40分：{high}只')
print(f'  30-40分：{mid}只')
print(f'  <30分：{low}只')
