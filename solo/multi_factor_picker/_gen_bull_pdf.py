# -*- coding: utf-8 -*-
"""BullScore合格池PDF报告"""
import os, sys
sys.path.insert(0, r'D:\mystock')
import pandas as pd
import numpy as np
from datetime import datetime

# ── 数据 ──
csv = r'D:\mystock\solo\report_daily\bull_stocks_qualified.csv'
df = pd.read_csv(csv)
scores = df['最终分']

# 统计
now = datetime.now().strftime('%Y-%m-%d %H:%M')
n = len(df)
avg = scores.mean()
med = scores.median()
hi = scores.max()
lo = scores.min()

grade_dist = df['等级'].value_counts().to_dict() if '等级' in df.columns else {}
ind_top = df['industry'].value_counts().head(10).to_dict() if 'industry' in df.columns else {}

# 分层
bins = [0, 60, 65, 70, 75, 80, 85, 90, 100]
labels = ['<60', '60-65', '65-70', '70-75', '75-80', '80-85', '85-90', '90+']
df['分层'] = pd.cut(scores, bins=bins, labels=labels)

# ── 创建PDF ──
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm, cm
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                 TableStyle, PageBreak, HRFlowable)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

font_path = r'C:\Windows\Fonts\simhei.ttf'
font_name = 'SimHei'
pdfmetrics.registerFont(TTFont(font_name, font_path))

out = r'D:\mystock\solo\report_daily\bull_score_report_20260626.pdf'
doc = SimpleDocTemplate(out, pagesize=A4,
    leftMargin=15*mm, rightMargin=15*mm,
    topMargin=15*mm, bottomMargin=15*mm)

st = ParagraphStyle('N', fontName=font_name, fontSize=9, leading=13, spaceAfter=2*mm)
st_title = ParagraphStyle('T', fontName=font_name, fontSize=16, leading=22, spaceAfter=6*mm)
st_h1 = ParagraphStyle('H1', fontName=font_name, fontSize=13, leading=18, spaceAfter=3*mm, textColor=colors.HexColor('#1a5276'))
st_h2 = ParagraphStyle('H2', fontName=font_name, fontSize=11, leading=15, spaceAfter=2*mm, textColor=colors.HexColor('#2c3e50'))
st_stat = ParagraphStyle('S', fontName=font_name, fontSize=10, leading=14, spaceAfter=1*mm)
st_footer = ParagraphStyle('F', fontName=font_name, fontSize=7, leading=10, textColor=colors.gray)

elements = []

# ── 封面 ──
elements.append(Paragraph('BullScore 中长线牛股池', st_title))
elements.append(Paragraph(f'生成时间: {now}', st_stat))
elements.append(Paragraph(f'合格股票: {n} 只 | 评分均值: {avg:.1f} | 中位: {med:.1f} | 最高: {hi:.1f} | 最低: {lo:.1f}', st_stat))
elements.append(Spacer(1, 5*mm))

# ── 概览统计 ──
elements.append(Paragraph('一、评分分层统计', st_h1))
elements.append(Spacer(1, 2*mm))

layers = df['分层'].value_counts().sort_index()
tdata = [['评分区间', '数量', '占比']]
for l, cnt in layers.items():
    tdata.append([str(l), str(cnt), f'{cnt/n*100:.1f}%'])
t = Table(tdata, colWidths=[25*mm, 20*mm, 20*mm])
t.setStyle(TableStyle([
    ('FONTNAME', (0,0), (-1,-1), font_name),
    ('FONTSIZE', (0,0), (-1,-1), 9),
    ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1a5276')),
    ('TEXTCOLOR', (0,0), (-1,0), colors.white),
    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
    ('GRID', (0,0), (-1,-1), 0.5, colors.gray),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f0f4f8')]),
]))
elements.append(t)
elements.append(Spacer(1, 5*mm))

# ── 等级分布 ──
elements.append(Paragraph('二、等级分布', st_h1))
elements.append(Spacer(1, 2*mm))
gdata = [['等级', '数量']]
for g, cnt in sorted(grade_dist.items()):
    gdata.append([str(g), str(cnt)])
gt = Table(gdata, colWidths=[30*mm, 20*mm])
gt.setStyle(TableStyle([
    ('FONTNAME', (0,0), (-1,-1), font_name),
    ('FONTSIZE', (0,0), (-1,-1), 9),
    ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1a5276')),
    ('TEXTCOLOR', (0,0), (-1,0), colors.white),
    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
    ('GRID', (0,0), (-1,-1), 0.5, colors.gray),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f0f4f8')]),
]))
elements.append(gt)
elements.append(Spacer(1, 5*mm))

# ── TOP行业 ──
elements.append(Paragraph('三、TOP10行业分布', st_h1))
elements.append(Spacer(1, 2*mm))
idatas = [['行业', '数量', '占比']]
for ind, cnt in ind_top.items():
    idatas.append([str(ind), str(cnt), f'{cnt/n*100:.1f}%'])
it = Table(idatas, colWidths=[30*mm, 20*mm, 20*mm])
it.setStyle(TableStyle([
    ('FONTNAME', (0,0), (-1,-1), font_name),
    ('FONTSIZE', (0,0), (-1,-1), 9),
    ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1a5276')),
    ('TEXTCOLOR', (0,0), (-1,0), colors.white),
    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
    ('GRID', (0,0), (-1,-1), 0.5, colors.gray),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f0f4f8')]),
]))
elements.append(it)
elements.append(Spacer(1, 5*mm))

# ── A级/B级龙头 ──
elements.append(Paragraph('四、A级/B级龙头', st_h1))
elements.append(Spacer(1, 2*mm))
top_df = df[df['等级'].isin(['A级产业龙头', 'B级成长股'])].sort_values('最终分', ascending=False)
th_data = [['代码', '名称', '等级', '评分', '行业', '主题']]
for _, r in top_df.iterrows():
    th_data.append([
        str(r['code']), str(r['name']), str(r['等级']),
        f"{r['最终分']:.1f}", str(r.get('industry', '') or ''),
        str(r.get('theme', '') or '')
    ])
th = Table(th_data, colWidths=[16*mm, 20*mm, 22*mm, 14*mm, 22*mm, 30*mm])
th.setStyle(TableStyle([
    ('FONTNAME', (0,0), (-1,-1), font_name),
    ('FONTSIZE', (0,0), (-1,-1), 8),
    ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1a5276')),
    ('TEXTCOLOR', (0,0), (-1,0), colors.white),
    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
    ('GRID', (0,0), (-1,-1), 0.5, colors.gray),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f0f4f8')]),
]))
elements.append(th)
elements.append(Spacer(1, 5*mm))

# ── 评分TOP50 ──
elements.append(Paragraph('五、BullScore TOP50', st_h1))
elements.append(Spacer(1, 2*mm))
top50 = df.sort_values('最终分', ascending=False).head(50)
t50_data = [['排名', '代码', '名称', '评分', '行业', '营收同比%', '利润同比%', 'ROE', '市值(亿)']]
for i, (_, r) in enumerate(top50.iterrows(), 1):
    rev = f"{r.get('营收同比', ''):.0f}" if pd.notna(r.get('营收同比', np.nan)) else '-'
    prof = f"{r.get('利润同比', ''):.0f}" if pd.notna(r.get('利润同比', np.nan)) else '-'
    roe = f"{r.get('ROE', ''):.1f}" if pd.notna(r.get('ROE', np.nan)) else '-'
    mcap = f"{r.get('市值(亿)', ''):.0f}" if pd.notna(r.get('市值(亿)', np.nan)) else '-'
    t50_data.append([
        str(i), str(r['code']), str(r['name']),
        f"{r['最终分']:.1f}",
        str(r.get('industry', '') or ''),
        rev, prof, roe, mcap
    ])
t50 = Table(t50_data, colWidths=[10*mm, 16*mm, 18*mm, 12*mm, 24*mm, 18*mm, 18*mm, 12*mm, 16*mm])
t50.setStyle(TableStyle([
    ('FONTNAME', (0,0), (-1,-1), font_name),
    ('FONTSIZE', (0,0), (-1,-1), 7),
    ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1a5276')),
    ('TEXTCOLOR', (0,0), (-1,0), colors.white),
    ('ALIGN', (1,0), (-1,-1), 'CENTER'),
    ('ALIGN', (0,0), (0,-1), 'CENTER'),
    ('GRID', (0,0), (-1,-1), 0.3, colors.gray),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f0f4f8')]),
]))
elements.append(t50)

# ── 按行业TOP半导体/元器件 ──
elements.append(Spacer(1, 5*mm))
elements.append(Paragraph('六、重点行业个股', st_h1))
elements.append(Spacer(1, 2*mm))
for focus_ind, title in [('半导体', '半导体'), ('元器件', '元器件')]:
    subset = df[(df['industry'] == focus_ind) & (df['最终分'] >= 80)]
    if len(subset) == 0:
        continue
    subset = subset.sort_values('最终分', ascending=False)
    elements.append(Paragraph(f'  ⊙ {title}（{len(subset)}只≥80分）', st_h2))
    sd = [['代码', '名称', '评分', '营收同比%', '利润同比%', 'ROE', '市值(亿)']]
    for _, r in subset.iterrows():
        sd.append([
            str(r['code']), str(r['name']),
            f"{r['最终分']:.1f}",
            f"{r.get('营收同比', ''):.0f}" if pd.notna(r.get('营收同比', np.nan)) else '-',
            f"{r.get('利润同比', ''):.0f}" if pd.notna(r.get('利润同比', np.nan)) else '-',
            f"{r.get('ROE', ''):.1f}" if pd.notna(r.get('ROE', np.nan)) else '-',
            f"{r.get('市值(亿)', ''):.0f}" if pd.notna(r.get('市值(亿)', np.nan)) else '-',
        ])
    stbl = Table(sd, colWidths=[16*mm, 18*mm, 12*mm, 18*mm, 18*mm, 12*mm, 16*mm])
    stbl.setStyle(TableStyle([
        ('FONTNAME', (0,0), (-1,-1), font_name),
        ('FONTSIZE', (0,0), (-1,-1), 7),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1a5276')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('ALIGN', (2,0), (-1,-1), 'CENTER'),
        ('ALIGN', (0,0), (0,-1), 'CENTER'),
        ('GRID', (0,0), (-1,-1), 0.3, colors.gray),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f0f4f8')]),
    ]))
    elements.append(stbl)
    elements.append(Spacer(1, 3*mm))

# 卷尾
elements.append(Spacer(1, 10*mm))
elements.append(HRFlowable(width='100%', thickness=0.5, color=colors.gray))
elements.append(Paragraph(f'BullScore v3.0 | 生成时间: {now} | 共{n}只合格股票 | 阈值: ≥55分', st_footer))

doc.build(elements)
print(f"PDF生成成功: {out} ({os.path.getsize(out)/1024:.0f} KB)")
