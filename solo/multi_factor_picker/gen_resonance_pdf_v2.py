# -*- coding: utf-8 -*-
"""重新生成二波形态精选PDF：按共振评分降序 + 带股票名称 + 排名"""
import os, sys, datetime, time, json
sys.path.insert(0, r'D:\mystock')
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'

import pandas as pd
import tushare as ts
ts.set_token(os.environ['TUSHARE_TOKEN'])
pro = ts.pro_api()

OUT_DIR = r'D:\mystock\solo\multi_factor_picker\output'

# 获取全部股票名称
print('获取股票名称...')
name_map = {}
try:
    all_basic = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name')
    for _, row in all_basic.iterrows():
        name_map[row['ts_code']] = row['name']
    print(f'获取{len(name_map)}只')
except:
    pass

# 读取wave2_daily CSV
csv_path = os.path.join(OUT_DIR, 'wave2_daily_20260623.csv')
df = pd.read_csv(csv_path)
print(f'信号数: {len(df)}')

# 填充股票名称
df['stock_name'] = df['ts_code'].map(name_map).fillna(df['ts_code'].str[:6])

# 按base_score（共振评分）降序排序
df = df.sort_values('base_score', ascending=False).reset_index(drop=True)

# 生成PDF
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

font_name = 'Helvetica'
for fp in [r'C:\Windows\Fonts\simhei.ttf', r'C:\Windows\Fonts\msyh.ttc']:
    if os.path.exists(fp):
        try:
            pdfmetrics.registerFont(TTFont('CNFont', fp))
            font_name = 'CNFont'
            break
        except:
            continue

ts_str = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
pdf_path = os.path.join(OUT_DIR, f'wave2_resonance_{ts_str}.pdf')

doc = SimpleDocTemplate(pdf_path, pagesize=landscape(A4),
    topMargin=12*mm, bottomMargin=12*mm, leftMargin=8*mm, rightMargin=8*mm)

styles = getSampleStyleSheet()
title_style = ParagraphStyle('TCN', parent=styles['Title'],
    fontName=font_name, fontSize=16, alignment=1, spaceAfter=4*mm)
sub_style = ParagraphStyle('SCN', parent=styles['Normal'],
    fontName=font_name, fontSize=9, alignment=1, spaceAfter=3*mm)
hdr_style = ParagraphStyle('HCN', parent=styles['Normal'],
    fontName=font_name, fontSize=8, alignment=1)
cel_style = ParagraphStyle('CCN', parent=styles['Normal'],
    fontName=font_name, fontSize=7.5, alignment=1)
note_style = ParagraphStyle('NCN', parent=styles['Normal'],
    fontName=font_name, fontSize=8, textColor=colors.HexColor('#888888'))

elements = []
elements.append(Paragraph('二波形态精选扫描报告（按共振评分排序）', title_style))

sideways = len(df[df['pattern'] == '强势横盘'])
deep = len(df[df['pattern'] == '深度回调'])
summary = f"信号: {len(df)}个 | 深度回调: {deep}只 | 强势横盘: {sideways}只 | 按共振评分降序"
elements.append(Paragraph(summary, sub_style))
elements.append(Spacer(1, 2*mm))

headers = ['排名', '股票代码', '股票名称', '形态', '共振评分', '一波涨幅%', '回调%', '调整天数', 'RSI', '入场价', '止损价', '目标价', '盈亏比']
col_widths = [10*mm, 22*mm, 20*mm, 16*mm, 14*mm, 14*mm, 12*mm, 14*mm, 10*mm, 16*mm, 16*mm, 16*mm, 12*mm]

data_rows = [[Paragraph(h, hdr_style) for h in headers]]
for idx, (_, row) in enumerate(df.iterrows()):
    rank = idx + 1
    data_rows.append([
        Paragraph(str(rank), cel_style),
        Paragraph(str(row['ts_code']), cel_style),
        Paragraph(str(row['stock_name']), cel_style),
        Paragraph(str(row['pattern']), cel_style),
        Paragraph(f"{row['base_score']:.0f}", cel_style),
        Paragraph(f"+{row['wave1_gain']:.1f}", cel_style),
        Paragraph(f"{row['pullback']:.1f}", cel_style),
        Paragraph(f"{row['pullback_days']:.0f}", cel_style),
        Paragraph(f"{row['rsi_now']:.1f}", cel_style),
        Paragraph(f"{row['entry_price']:.2f}", cel_style),
        Paragraph(f"{row['stop_price']:.2f}", cel_style),
        Paragraph(f"{row['target_price']:.2f}", cel_style),
        Paragraph(f"{row['rr_ratio']:.1f}x", cel_style),
    ])

t = Table(data_rows, colWidths=col_widths, repeatRows=1)
style_cmds = [
    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a5276')),
    ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
    ('FONTSIZE', (0, 0), (-1, 0), 9),
    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f5f5f5')]),
    ('TOPPADDING', (0, 0), (-1, -1), 3),
    ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
]
# TOP3绿色
for i in range(min(3, len(df))):
    style_cmds.append(('BACKGROUND', (0, i+1), (-1, i+1), colors.HexColor('#d4efdf')))

t.setStyle(TableStyle(style_cmds))
elements.append(t)
elements.append(Spacer(1, 4*mm))
elements.append(Paragraph("* 绿色高亮 = TOP3 | 按共振评分降序排列", note_style))
elements.append(Paragraph(f"* 生成: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}", note_style))

doc.build(elements)
sz = os.path.getsize(pdf_path)
print(f'\nPDF已生成: {pdf_path}')
print(f'文件大小: {sz} bytes')
print(f'\nTOP10:')
for i, (_, r) in enumerate(df.head(10).iterrows()):
    print(f"  {i+1}. {r['ts_code']} {r['stock_name']} 评分{r['base_score']:.0f} {r['pattern']} RSI{r['rsi_now']:.1f}")
