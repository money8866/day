# -*- coding: utf-8 -*-
import pandas as pd
import os
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER

FONT = 'Chinese'
pdfmetrics.registerFont(TTFont(FONT, r'C:\Windows\Fonts\msyh.ttc'))

C_BLUE   = colors.HexColor('#1a3a8a')
C_RED    = colors.HexColor('#c0392b')
C_GREEN  = colors.HexColor('#27ae60')
C_ORANGE = colors.HexColor('#e67e22')
C_PURPLE = colors.HexColor('#8e44ad')
C_GREY   = colors.HexColor('#6c757d')
C_WHITE  = colors.whitesmoke
C_DARK   = colors.HexColor('#1a1a2e')
C_GOLD   = colors.HexColor('#d4a017')

def ps(name, **kw):
    base = dict(fontName=FONT, fontSize=9, leading=14, textColor=C_DARK)
    base.update(kw)
    return ParagraphStyle(name, **base)

def hdr_style(bg=C_BLUE, fs=8):
    return TableStyle([
        ('BACKGROUND', (0,0), (-1,0), bg),
        ('TEXTCOLOR', (0,0), (-1,0), C_WHITE),
        ('FONTNAME', (0,0), (-1,-1), FONT),
        ('FONTSIZE', (0,0), (-1,-1), fs),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0,1), (-1,-1),
         [colors.HexColor('#f8f9fa'), colors.HexColor('#eef1f5')]),
        ('GRID', (0,0), (-1,-1), 0.3, colors.HexColor('#cccccc')),
        ('TOPPADDING', (0,0), (-1,-1), 2.8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2.8),
    ])

SRC = 'D:/mystock/solo/report_daily/enhanced_timing_bull_all_20260722_084454.csv'
df = pd.read_csv(SRC, encoding='utf-8-sig')
sa = df[df['修正后胜率分级'].isin(['S','A'])].sort_values('量化择时分', ascending=False).reset_index(drop=True)

today = '20260722'
out = f'D:/mystock/report_daily/sa_picks_{today}.pdf'
doc = SimpleDocTemplate(out, pagesize=landscape(A4),
    leftMargin=10*mm, rightMargin=10*mm, topMargin=10*mm, bottomMargin=8*mm)
story = []

# ═══ 标题 ═══
story.append(Paragraph('S级 + A级 标的精选（高胜率池）', ps('tit', fontSize=15, textColor=C_BLUE, alignment=TA_CENTER, spaceAfter=2)))
story.append(Paragraph(f'增强择时·全市场Bull股池 S/A级  |  {len(sa)}只  |  2026-07-22 盘前', ps('sub', fontSize=8, textColor=C_GREY, alignment=TA_CENTER, spaceAfter=4)))
story.append(HRFlowable(width='100%', thickness=2, color=C_BLUE, spaceAfter=5))

# ═══ 概览 ═══
n_s = len(sa[sa['修正后胜率分级']=='S'])
n_a = len(sa[sa['修正后胜率分级']=='A'])
n_pull2 = len(sa[sa['推荐买点类型'].str.contains('买点2', na=False)])
n_pull1 = len(sa[sa['推荐买点类型'].str.contains('买点1', na=False)])
industries = sa['行业'].value_counts()
top_ind = industries.index[0]
top_ind_cnt = industries.iloc[0]

ov = [
    ['S级', 'A级', '已回踩(买点2)', '刚突破(买点1)', '最强行业', '医药板块占比'],
    [f'{n_s}只', f'{n_a}只', f'{n_pull2}只', f'{n_pull1}只', f'{top_ind} ({top_ind_cnt}只)', f"{len(sa[sa['行业'].str.contains('药|医疗|生物', na=False)])}/{len(sa)}"],
]
t = Table(ov, colWidths=[40,40,56,56,80,60])
t.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), C_BLUE),
    ('TEXTCOLOR', (0,0), (-1,0), C_WHITE),
    ('FONTNAME', (0,0), (-1,-1), FONT),
    ('FONTSIZE', (0,0), (-1,-1), 8),
    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('ROWBACKGROUNDS', (0,1), (-1,1), [C_GOLD, colors.HexColor('#fff8e1')]),
    ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor('#cccccc')),
    ('TOPPADDING', (0,0), (-1,-1), 3), ('BOTTOMPADDING', (0,0), (-1,-1), 3),
]))
story.append(t)
story.append(Spacer(1, 3*mm))

# ═══ S级详情卡 ═══
s_row = sa[sa['修正后胜率分级']=='S'].iloc[0]
story.append(Paragraph('🏆 S级（唯一·双确认）', ps('h2', fontSize=11, textColor=C_RED, spaceBefore=2, spaceAfter=3)))
sc = [
    [f"  {s_row['名称']}({str(s_row['代码']).replace('.SH','').replace('.SZ','')})  评级:S  择时分:{s_row['量化择时分']:.1f}  修正分:{s_row['修正后评分']:.1f}", ''],
    [f"  行业:{s_row['行业']}  中报:{s_row['中报业绩亮点']}  真突破:✅  回踩确认:✅", ''],
    [f"  现价:{s_row['现价']}  VWAP:{s_row['VWAP']}  MA20:{s_row['MA20']}  筹码峰:{s_row['筹码峰顶']}  集中度:{s_row['筹码集中度%']}%", ''],
    [f"  买点:{s_row['推荐买点类型']}  ATR止损:{s_row['ATR动态止损价']}  ATR止盈:{s_row['ATR跟踪止盈价']}  决策:{s_row['交易决策']}", ''],
]
t = Table(sc, colWidths=[300, 300])
t.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), C_RED),
    ('BACKGROUND', (0,1), (-1,-1), colors.HexColor('#fff3e0')),
    ('TEXTCOLOR', (0,0), (-1,0), colors.white),
    ('FONTNAME', (0,0), (-1,-1), FONT),
    ('FONTSIZE', (0,0), (-1,-1), 8.5),
    ('SPAN', (0,0), (1,0)), ('SPAN', (0,1), (1,1)), ('SPAN', (0,2), (1,2)), ('SPAN', (0,3), (1,3)),
    ('ALIGN', (0,0), (-1,-1), 'LEFT'), ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('TOPPADDING', (0,0), (-1,-1), 3), ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ('LEFTPADDING', (0,0), (-1,-1), 6),
    ('GRID', (0,0), (-1,-1), 0.3, colors.HexColor('#cccccc')),
]))
story.append(t)
story.append(Spacer(1, 3*mm))

# ═══ A级·买点2（已回踩）═══
a2 = sa[sa['推荐买点类型'].str.contains('买点2', na=False)].sort_values('量化择时分', ascending=False)
story.append(Paragraph('▶ A级·已回踩确认（买点2，缩量回踩VWAP，相对稳健）', ps('h2', fontSize=10, textColor=C_GREEN, spaceBefore=2, spaceAfter=3)))
h = ['代码','名称','行业','中报','择时分','修正分','现价','VWAP','MA20','ATR止损','ATR止盈','决策']
cw = [22,30,34,40,26,26,30,30,30,32,32,80]
rows = [h]
for _, r in a2.iterrows():
    code = str(r['代码']).replace('.SH','').replace('.SZ','').zfill(6)
    rows.append([code, str(r['名称'])[:4], str(r['行业'])[:5], str(r['中报业绩亮点']),
        f"{r['量化择时分']:.1f}", f"{r['修正后评分']:.1f}", f"{r['现价']:.2f}",
        f"{r['VWAP']:.2f}", f"{r['MA20']:.2f}", f"{r['ATR动态止损价']:.2f}",
        f"{r['ATR跟踪止盈价']:.2f}", str(r['交易决策'])[:13]])
t = Table(rows, colWidths=cw, repeatRows=1)
ts = hdr_style(C_GREEN, 7.5)
for i in range(1, len(rows)):
    ts.add('TEXTCOLOR', (3,i), (3,i), C_ORANGE)
t.setStyle(ts)
story.append(t)
story.append(Spacer(1, 3*mm))

# ═══ A级·买点1（刚突破）═══
a1 = sa[sa['推荐买点类型'].str.contains('买点1', na=False)].sort_values('量化择时分', ascending=False)
story.append(Paragraph('▶ A级·刚突破待确认（买点1，放量突破VWAP+筹码峰，等回踩）', ps('h2', fontSize=10, textColor=C_ORANGE, spaceBefore=2, spaceAfter=3)))
rows = [h]
for _, r in a1.iterrows():
    code = str(r['代码']).replace('.SH','').replace('.SZ','').zfill(6)
    rows.append([code, str(r['名称'])[:4], str(r['行业'])[:5], str(r['中报业绩亮点']),
        f"{r['量化择时分']:.1f}", f"{r['修正后评分']:.1f}", f"{r['现价']:.2f}",
        f"{r['VWAP']:.2f}", f"{r['MA20']:.2f}", f"{r['ATR动态止损价']:.2f}",
        f"{r['ATR跟踪止盈价']:.2f}", str(r['交易决策'])[:13]])
t = Table(rows, colWidths=cw, repeatRows=1)
ts = hdr_style(C_ORANGE, 7.5)
for i in range(1, len(rows)):
    ts.add('TEXTCOLOR', (3,i), (3,i), C_ORANGE)
t.setStyle(ts)
story.append(t)

# Footer
story.append(Spacer(1, 2*mm))
story.append(HRFlowable(width='100%', thickness=0.5, color=colors.HexColor('#cccccc'), spaceBefore=3))
story.append(Paragraph(
    f'买点2=已回踩确认(稳健)  买点1=刚突破待回踩确认(不追高)  |  大盘空头(Beta0.6) 仓位宜轻  |  2026-07-22 盘前  |  QClaw量化系统  |  仅供参考',
    ps('foot', fontSize=6.5, textColor=C_GREY, alignment=TA_CENTER)
))

doc.build(story)
print(f'PDF: {out}  size={os.path.getsize(out):,} bytes')
