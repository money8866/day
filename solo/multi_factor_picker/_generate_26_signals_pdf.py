# -*- coding: utf-8 -*-
"""生成26号信号结果分析PDF"""
import os
import pandas as pd
from datetime import datetime

# 注册中文字体
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

font_registered = False
for font_path in [r'C:\Windows\Fonts\simhei.ttf', r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\simsun.ttc']:
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
df = pd.read_csv(r'D:\mystock\solo\multi_factor_picker\output\wave2_pattern_20260628_111801.csv')
df_26 = df[df['entry_date'] == 20260626].sort_values('score', ascending=False)

# 创建PDF
pdf_path = r'D:\mystock\solo\multi_factor_picker\output\wave2_20260626_signals.pdf'
doc = SimpleDocTemplate(pdf_path, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)

# 样式
styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name='CustomTitle', fontName=chinese_font, fontSize=18, spaceAfter=30, alignment=TA_CENTER, textColor=colors.HexColor('#1a1a1a')))
styles.add(ParagraphStyle(name='CustomH1', fontName=chinese_font, fontSize=14, spaceAfter=12, spaceBefore=12, textColor=colors.HexColor('#2c3e50')))
styles.add(ParagraphStyle(name='CustomBody', fontName=chinese_font, fontSize=10, spaceAfter=6, leading=14))
styles.add(ParagraphStyle(name='CustomBullet', fontName=chinese_font, fontSize=10, spaceAfter=4, leftIndent=20))

story = []

# 标题
story.append(Paragraph('2026-06-26 二波形态信号结果分析', styles['CustomTitle']))
story.append(Spacer(1, 0.5*cm))

# 概览
story.append(Paragraph('信号概览', styles['CustomH1']))
story.append(Paragraph(f'• 总信号数：{len(df_26)}只', styles['CustomBullet']))
story.append(Paragraph(f'• V型急跌：{len(df_26[df_26["pattern"]=="V型急跌"])}只', styles['CustomBullet']))
story.append(Paragraph(f'• 放量回调：{len(df_26[df_26["pattern"]=="放量回调"])}只', styles['CustomBullet']))
story.append(Paragraph(f'• 深度回调：{len(df_26[df_26["pattern"]=="深度回调"])}只', styles['CustomBullet']))
story.append(Spacer(1, 0.5*cm))

# 评分分布
story.append(Paragraph('评分分布', styles['CustomH1']))
high = len(df_26[df_26['score'] >= 40])
mid = len(df_26[(df_26['score'] >= 30) & (df_26['score'] < 40)])
low = len(df_26[df_26['score'] < 30])
story.append(Paragraph(f'• 高质量（≥40分）：{high}只（{high/len(df_26)*100:.1f}%）', styles['CustomBullet']))
story.append(Paragraph(f'• 中质量（30-40分）：{mid}只（{mid/len(df_26)*100:.1f}%）', styles['CustomBullet']))
story.append(Paragraph(f'• 低质量（<30分）：{low}只（{low/len(df_26)*100:.1f}%）', styles['CustomBullet']))
story.append(Spacer(1, 0.5*cm))

# 完整信号表格
story.append(Paragraph('完整信号列表', styles['CustomH1']))

table_data = [['排名', '名称', '代码', '形态', '评分', '一波%', '回踩%', '量比']]
for i, (idx, row) in enumerate(df_26.iterrows(), 1):
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
    # 高亮≥40分
    ('BACKGROUND', (0, 1), (-1, 2), colors.HexColor('#d5f4e6')),
]))

story.append(table)
story.append(Spacer(1, 0.5*cm))

# TOP3详细分析
story.append(Paragraph('TOP3高质量信号分析', styles['CustomH1']))

for i, (idx, row) in enumerate(df_26.head(3).iterrows(), 1):
    story.append(Paragraph(f'{i}. {row["name"]}（{row["ts_code"]}）', styles['CustomBody']))
    story.append(Paragraph(f'   形态：{row["pattern"]} | 评分：{row["score"]:.0f}分', styles['CustomBullet']))
    story.append(Paragraph(f'   一波涨幅：{row["wave1_gain"]:.1f}% | 回踩深度：{row["pullback_pct"]:.1f}%', styles['CustomBullet']))
    story.append(Paragraph(f'   量比：{row["vol_ratio"]:.2f} | RSI：{row["rsi"]:.1f}', styles['CustomBullet']))

story.append(Spacer(1, 0.5*cm))

# 风险提示
story.append(Paragraph('风险提示', styles['CustomH1']))
low_vol = df_26[df_26['vol_ratio'] < 0.8]
high_pullback = df_26[df_26['pullback_pct'] > 25]
story.append(Paragraph(f'• 缩量信号（量比<0.8）：{len(low_vol)}只，反弹动力可能不足', styles['CustomBullet']))
story.append(Paragraph(f'• 回踩过深（>25%）：{len(high_pullback)}只，趋势可能破坏', styles['CustomBullet']))
story.append(Paragraph(f'• 金博股份：一波涨幅106.4%，主力可能已出货', styles['CustomBullet']))
story.append(Spacer(1, 0.5*cm))

# 结论
story.append(Paragraph('结论', styles['CustomH1']))
story.append(Paragraph(f'• 26号共筛选出{len(df_26)}只二波形态信号', styles['CustomBullet']))
story.append(Paragraph(f'• TOP2高质量信号（≥40分）：矩子科技、银禧科技', styles['CustomBullet']))
story.append(Paragraph(f'• V型急跌形态占主导（11只，68.8%）', styles['CustomBullet']))
story.append(Paragraph(f'• 建议：关注TOP3，谨慎缩量和回踩过深信号', styles['CustomBullet']))

# 页脚
story.append(Spacer(1, 0.5*cm))
story.append(Paragraph(f'报告生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}', styles['CustomBody']))
story.append(Paragraph('版本：wave2_pattern_scanner.py v2.12', styles['CustomBody']))

# 生成PDF
doc.build(story)

print('='*80)
print('26号信号结果分析PDF生成完成！')
print('='*80)
print(f'\n文件路径：{pdf_path}')
print(f'\n信号统计：')
print(f'  总数：{len(df_26)}只')
print(f'  ≥40分：{len(df_26[df_26["score"]>=40])}只')
print(f'  30-40分：{len(df_26[(df_26["score"]>=30) & (df_26["score"]<40)])}只')
print(f'  <30分：{len(df_26[df_26["score"]<30])}只')
