# -*- coding: utf-8 -*-
import pandas as pd
import os
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, PageBreak
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER

FONT = 'Chinese'
pdfmetrics.registerFont(TTFont(FONT, r'C:\Windows\Fonts\msyh.ttc'))

C_BLUE  = colors.HexColor('#1a3a8a')
C_RED   = colors.HexColor('#c0392b')
C_GREEN = colors.HexColor('#27ae60')
C_ORANGE= colors.HexColor('#e67e22')
C_PURPLE= colors.HexColor('#8e44ad')
C_GREY  = colors.HexColor('#6c757d')
C_WHITE = colors.whitesmoke
C_DARK  = colors.HexColor('#1a1a2e')
C_GOLD  = colors.HexColor('#d4a017')

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
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ])

# ── 数据 ──
df = pd.read_csv('D:/mystock/solo/report_daily/timing_analysis_20260721_090211.csv', encoding='utf-8-sig')
df = df.sort_values('择时评分', ascending=False).reset_index(drop=True)

today = '20260721'
out = f'D:/mystock/report_daily/timing_analysis_{today}.pdf'
doc = SimpleDocTemplate(
    out, pagesize=landscape(A4),
    leftMargin=12*mm, rightMargin=12*mm,
    topMargin=12*mm, bottomMargin=10*mm
)
story = []

# ═══════════ 标题 ═══════════
story.append(Paragraph('择时分析报告（Timing Analysis）', ps('tit', fontSize=16, textColor=C_BLUE, alignment=TA_CENTER, spaceAfter=2)))
story.append(Paragraph(f'技术面择时评分  |  {len(df)}只  |  最高{df["择时评分"].max():.0f}分  |  {today} 盘前', ps('sub', fontSize=8, textColor=C_GREY, alignment=TA_CENTER, spaceAfter=4)))
story.append(HRFlowable(width='100%', thickness=2, color=C_BLUE, spaceAfter=6))

# ═══════════ 概览 ═══════════
n_total = len(df)
n_a = len(df[df['评级'] == 'A'])
n_b = len(df[df['评级'] == 'B'])
n_c = len(df[df['评级'] == 'C'])
n_multi = len(df[df['趋势'] == '多头趋势'])
n_osc = len(df[df['趋势'] == '震荡偏多'])
avg_score = df['择时评分'].mean()
avg_rsi = df['RSI'].mean()

stats_data = [
    ['总数', 'A级', 'B级', 'C级', '多头趋势', '震荡偏多', '均择时分', '均RSI'],
    [f'{n_total}只', f'{n_a}只', f'{n_b}只', f'{n_c}只', f'{n_multi}只', f'{n_osc}只', f'{avg_score:.1f}', f'{avg_rsi:.1f}'],
]
t = Table(stats_data, colWidths=[48]*8)
t.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), C_BLUE),
    ('TEXTCOLOR', (0,0), (-1,0), C_WHITE),
    ('FONTNAME', (0,0), (-1,-1), FONT),
    ('FONTSIZE', (0,0), (-1,-1), 8.5),
    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('ROWBACKGROUNDS', (0,1), (-1,1), [C_GOLD, colors.HexColor('#fff8e1')]),
    ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor('#cccccc')),
    ('TOPPADDING', (0,0), (-1,-1), 4),
    ('BOTTOMPADDING', (0,0), (-1,-1), 4),
]))
story.append(t)
story.append(Spacer(1, 3*mm))

# ═══════════ TOP 20 ═══════════
story.append(Paragraph('▶ TOP 20 精选', ps('h2', fontSize=11, textColor=C_RED, spaceBefore=4, spaceAfter=4)))

top = df.head(20)
h = ['排名', '代码', '名称', 'DoubleScore', '择时评分', '评级', '趋势', '信号', '核心逻辑', '现价', 'MA20', 'RSI', '近5日%']
cw = [20, 44, 36, 44, 40, 26, 44, 130, 80, 32, 36, 26, 32]

rows = [h]
for _, r in top.iterrows():
    rows.append([
        str(int(r['排名'])),
        str(r['股票代码']),
        r['名称'][:4],
        f"{r['DoubleScore']:.1f}",
        f"{r['择时评分']:.0f}",
        r['评级'],
        str(r['趋势'])[:6],
        str(r['信号'])[:22],
        str(r['核心逻辑'])[:14],
        f"{r['现价']:.2f}",
        f"{r['MA20']:.2f}",
        f"{r['RSI']:.1f}",
        f"{r['近5日涨幅%']:.1f}%",
    ])

t = Table(rows, colWidths=cw)
ts = hdr_style(C_RED, 7)
for i in range(1, len(rows)):
    # 评级着色
    rating = rows[i][5]
    if rating == 'A': ts.add('TEXTCOLOR', (5,i), (5,i), C_GREEN)
    elif rating == 'B': ts.add('TEXTCOLOR', (5,i), (5,i), C_ORANGE)
    elif rating == 'C': ts.add('TEXTCOLOR', (5,i), (5,i), C_RED)
    # 趋势着色
    trend = rows[i][6]
    if '多头' in trend: ts.add('TEXTCOLOR', (6,i), (6,i), C_GREEN)
    elif '震荡' in trend: ts.add('TEXTCOLOR', (6,i), (6,i), C_ORANGE)
    # RSI着色
    rsi = float(rows[i][11])
    if rsi > 70: ts.add('TEXTCOLOR', (11,i), (11,i), C_RED)
    elif rsi < 30: ts.add('TEXTCOLOR', (11,i), (11,i), C_GREEN)
    # 近5日涨幅
    chg5 = float(rows[i][12].rstrip('%'))
    if chg5 > 10: ts.add('TEXTCOLOR', (12,i), (12,i), C_RED)
    elif chg5 < 0: ts.add('TEXTCOLOR', (12,i), (12,i), C_GREY)
t.setStyle(ts)
story.append(t)
story.append(Spacer(1, 4*mm))

# ═══════════ 趋势分布 ═══════════
story.append(Paragraph('▶ 趋势分布', ps('h2', fontSize=11, textColor=C_ORANGE, spaceBefore=4, spaceAfter=4)))
trend_dist = df['趋势'].value_counts()
trend_rows = [['趋势', '数量', '占比', '均择时评分', '均RSI']]
for tr, cnt in trend_dist.items():
    sub = df[df['趋势'] == tr]
    trend_rows.append([
        tr, str(cnt), f'{cnt/n_total*100:.1f}%',
        f"{sub['择时评分'].mean():.1f}",
        f"{sub['RSI'].mean():.1f}",
    ])
t = Table(trend_rows, colWidths=[56, 36, 36, 52, 40])
t.setStyle(hdr_style(C_ORANGE, 8))
story.append(t)
story.append(Spacer(1, 3*mm))

# ═══════════ 评级分布 ═══════════
story.append(Paragraph('▶ 评级分布', ps('h2', fontSize=11, textColor=C_PURPLE, spaceBefore=4, spaceAfter=4)))
rating_dist = df['评级'].value_counts()
rating_rows = [['评级', '数量', '占比', '均择时评分', '均RSI', '均近5日涨幅%']]
for rt, cnt in rating_dist.items():
    sub = df[df['评级'] == rt]
    rating_rows.append([
        rt, str(cnt), f'{cnt/n_total*100:.1f}%',
        f"{sub['择时评分'].mean():.1f}",
        f"{sub['RSI'].mean():.1f}",
        f"{sub['近5日涨幅%'].mean():.1f}%",
    ])
t = Table(rating_rows, colWidths=[36, 36, 36, 52, 40, 52])
ts = hdr_style(C_PURPLE, 8)
for i in range(1, len(rating_rows)):
    rt = rating_rows[i][0]
    if rt == 'A': ts.add('TEXTCOLOR', (0,i), (0,i), C_GREEN)
    elif rt == 'B': ts.add('TEXTCOLOR', (0,i), (0,i), C_ORANGE)
    elif rt == 'C': ts.add('TEXTCOLOR', (0,i), (0,i), C_RED)
t.setStyle(ts)
story.append(t)
story.append(Spacer(1, 3*mm))

# ═══════════ 全量列表 ═══════════
story.append(PageBreak())
story.append(Paragraph(f'全量列表（{n_total}只）', ps('h2', fontSize=11, textColor=C_BLUE, spaceBefore=2, spaceAfter=4)))

all_h = ['排名', '代码', '名称', 'DoubleScore', '择时评分', '评级', '趋势', '信号', '核心逻辑', '现价', 'MA5', 'MA20', 'RSI', '近5日%', '量比']
all_cw = [20, 44, 36, 44, 40, 26, 44, 120, 80, 32, 32, 36, 26, 32, 26]
all_rows = [all_h]

for _, r in df.iterrows():
    all_rows.append([
        str(int(r['排名'])),
        str(r['股票代码']),
        r['名称'][:4],
        f"{r['DoubleScore']:.1f}",
        f"{r['择时评分']:.0f}",
        r['评级'],
        str(r['趋势'])[:6],
        str(r['信号'])[:18],
        str(r['核心逻辑'])[:14],
        f"{r['现价']:.2f}",
        f"{r['MA5']:.2f}",
        f"{r['MA20']:.2f}",
        f"{r['RSI']:.1f}",
        f"{r['近5日涨幅%']:.1f}%",
        f"{r['量比']:.2f}",
    ])

t = Table(all_rows, colWidths=all_cw)
ts = hdr_style(C_BLUE, 7)
for i in range(1, len(all_rows)):
    rating = all_rows[i][5]
    if rating == 'A': ts.add('TEXTCOLOR', (5,i), (5,i), C_GREEN)
    elif rating == 'B': ts.add('TEXTCOLOR', (5,i), (5,i), C_ORANGE)
    elif rating == 'C': ts.add('TEXTCOLOR', (5,i), (5,i), C_RED)
    trend = all_rows[i][6]
    if '多头' in trend: ts.add('TEXTCOLOR', (6,i), (6,i), C_GREEN)
    elif '震荡' in trend: ts.add('TEXTCOLOR', (6,i), (6,i), C_ORANGE)
    rsi = float(all_rows[i][12])
    if rsi > 70: ts.add('TEXTCOLOR', (12,i), (12,i), C_RED)
    elif rsi < 30: ts.add('TEXTCOLOR', (12,i), (12,i), C_GREEN)
t.setStyle(ts)
story.append(t)

# Footer
story.append(Spacer(1, 3*mm))
story.append(HRFlowable(width='100%', thickness=0.5, color=colors.HexColor('#cccccc'), spaceBefore=4))
story.append(Paragraph(
    f'择时评分 = 技术面综合评分（趋势+信号+RSI+量比）  |  {n_total}只  |  {today} 盘前  |  QClaw量化系统  |  仅供参考',
    ps('foot', fontSize=7, textColor=C_GREY, alignment=TA_CENTER)
))

doc.build(story)
print(f'PDF: {out}  size={os.path.getsize(out):,} bytes')
