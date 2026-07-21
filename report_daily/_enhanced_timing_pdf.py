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
df = pd.read_csv('D:/mystock/solo/report_daily/enhanced_timing_20260721_092436.csv', encoding='utf-8-sig')
df = df.sort_values('原始Timing分', ascending=False).reset_index(drop=True)

today = '20260721'
out = f'D:/mystock/report_daily/enhanced_timing_{today}.pdf'
doc = SimpleDocTemplate(
    out, pagesize=landscape(A4),
    leftMargin=12*mm, rightMargin=12*mm,
    topMargin=12*mm, bottomMargin=10*mm
)
story = []

# ═══════════ 标题 ═══════════
story.append(Paragraph('增强择时分析报告（Enhanced Timing）', ps('tit', fontSize=16, textColor=C_BLUE, alignment=TA_CENTER, spaceAfter=2)))
story.append(Paragraph(f'Timing分 + 筹码突破 + 回踩确认 + ATR风控  |  {len(df)}只  |  {today} 盘前', ps('sub', fontSize=8, textColor=C_GREY, alignment=TA_CENTER, spaceAfter=4)))
story.append(HRFlowable(width='100%', thickness=2, color=C_BLUE, spaceAfter=6))

# ═══════════ 概览 ═══════════
n_total = len(df)
n_s = len(df[df['修正后胜率分级'] == 'S'])
n_a = len(df[df['修正后胜率分级'] == 'A'])
n_b = len(df[df['修正后胜率分级'] == 'B'])
n_true_break = len(df[df['真突破判定'].str.contains('真突破', na=False)])
n_pullback = len(df[df['回踩确认'].str.contains('是', na=False)])

stats_data = [
    ['总数', 'S级', 'A级', 'B级', '真突破', '回踩确认', '均Timing分'],
    [f'{n_total}只', f'{n_s}只', f'{n_a}只', f'{n_b}只', f'{n_true_break}只', f'{n_pullback}只', f"{df['原始Timing分'].mean():.1f}"],
]
t = Table(stats_data, colWidths=[48]*7)
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

# ═══════════ TOP 15 ═══════════
story.append(Paragraph('▶ TOP 15 精选（含ATR风控）', ps('h2', fontSize=11, textColor=C_RED, spaceBefore=4, spaceAfter=4)))

top = df.head(15)
h = ['代码', '名称', '行业', 'Timing分', '分级', '真突破', '回踩', '买点类型', '现价', 'ATR止损', 'ATR止盈', '交易决策', '核心逻辑']
cw = [44, 36, 40, 36, 26, 30, 26, 120, 36, 40, 40, 100, 90]

rows = [h]
for _, r in top.iterrows():
    rows.append([
        str(r['代码']).replace('.SH','').replace('.SZ','').zfill(6),
        r['名称'][:4],
        str(r['行业'])[:6],
        f"{r['原始Timing分']:.0f}",
        r['修正后胜率分级'],
        '✅' if '真突破' in str(r['真突破判定']) else '❌',
        '✅' if '是' in str(r['回踩确认']) else '❌',
        str(r['推荐买点类型'])[:20],
        f"{r['现价']:.2f}",
        f"{r['ATR动态止损价']:.2f}",
        f"{r['ATR跟踪止盈价']:.2f}",
        str(r['交易决策'])[:18],
        str(r['核心逻辑'])[:16],
    ])

t = Table(rows, colWidths=cw)
ts = hdr_style(C_RED, 7)
for i in range(1, len(rows)):
    # 分级着色
    grade = rows[i][4]
    if grade == 'S': ts.add('BACKGROUND', (4,i), (4,i), colors.HexColor('#ff6b6b'))
    elif grade == 'A': ts.add('TEXTCOLOR', (4,i), (4,i), C_GREEN)
    elif grade == 'B': ts.add('TEXTCOLOR', (4,i), (4,i), C_ORANGE)
    # 真突破
    if rows[i][5] == '✅': ts.add('TEXTCOLOR', (5,i), (5,i), C_GREEN)
    else: ts.add('TEXTCOLOR', (5,i), (5,i), C_RED)
    # 回踩确认
    if rows[i][6] == '✅': ts.add('TEXTCOLOR', (6,i), (6,i), C_GREEN)
    else: ts.add('TEXTCOLOR', (6,i), (6,i), C_GREY)
t.setStyle(ts)
story.append(t)
story.append(Spacer(1, 4*mm))

# ═══════════ 分级分布 ═══════════
story.append(Paragraph('▶ 胜率分级分布', ps('h2', fontSize=11, textColor=C_ORANGE, spaceBefore=4, spaceAfter=4)))
grade_dist = df['修正后胜率分级'].value_counts()
grade_rows = [['分级', '数量', '占比', '均Timing分', '真突破率', '回踩确认率']]
for gr, cnt in grade_dist.items():
    sub = df[df['修正后胜率分级'] == gr]
    true_rate = len(sub[sub['真突破判定'].str.contains('真突破', na=False)]) / cnt * 100 if cnt > 0 else 0
    pullback_rate = len(sub[sub['回踩确认'].str.contains('是', na=False)]) / cnt * 100 if cnt > 0 else 0
    grade_rows.append([
        gr, str(cnt), f'{cnt/n_total*100:.1f}%',
        f"{sub['原始Timing分'].mean():.1f}",
        f"{true_rate:.0f}%",
        f"{pullback_rate:.0f}%",
    ])
t = Table(grade_rows, colWidths=[36, 36, 36, 52, 52, 52])
ts = hdr_style(C_ORANGE, 8)
for i in range(1, len(grade_rows)):
    gr = grade_rows[i][0]
    if gr == 'S': ts.add('TEXTCOLOR', (0,i), (0,i), C_RED)
    elif gr == 'A': ts.add('TEXTCOLOR', (0,i), (0,i), C_GREEN)
    elif gr == 'B': ts.add('TEXTCOLOR', (0,i), (0,i), C_ORANGE)
t.setStyle(ts)
story.append(t)
story.append(Spacer(1, 3*mm))

# ═══════════ 买点类型分布 ═══════════
story.append(Paragraph('▶ 推荐买点类型分布', ps('h2', fontSize=11, textColor=C_PURPLE, spaceBefore=4, spaceAfter=4)))
buy_dist = df['推荐买点类型'].value_counts().head(6)
buy_rows = [['买点类型', '数量', '占比']]
for bt, cnt in buy_dist.items():
    buy_rows.append([str(bt)[:20], str(cnt), f'{cnt/n_total*100:.1f}%'])
t = Table(buy_rows, colWidths=[120, 40, 40])
t.setStyle(hdr_style(C_PURPLE, 8))
story.append(t)
story.append(Spacer(1, 3*mm))

# ═══════════ 全量列表 ═══════════
story.append(PageBreak())
story.append(Paragraph(f'全量列表（{n_total}只）', ps('h2', fontSize=11, textColor=C_BLUE, spaceBefore=2, spaceAfter=4)))

all_h = ['代码', '名称', '行业', 'Timing分', '分级', '真突破', '回踩', 'VWAP', '现价', 'MA20', '筹码峰', 'ATR止损', 'ATR止盈', '交易决策', '核心逻辑']
all_cw = [44, 36, 40, 36, 26, 28, 26, 36, 36, 36, 36, 40, 40, 100, 90]
all_rows = [all_h]

for _, r in df.iterrows():
    all_rows.append([
        str(r['代码']).replace('.SH','').replace('.SZ','').zfill(6),
        r['名称'][:4],
        str(r['行业'])[:6],
        f"{r['原始Timing分']:.0f}",
        r['修正后胜率分级'],
        '✅' if '真突破' in str(r['真突破判定']) else '❌',
        '✅' if '是' in str(r['回踩确认']) else '❌',
        f"{r['VWAP']:.2f}",
        f"{r['现价']:.2f}",
        f"{r['MA20']:.2f}",
        f"{r['筹码峰顶']:.2f}",
        f"{r['ATR动态止损价']:.2f}",
        f"{r['ATR跟踪止盈价']:.2f}",
        str(r['交易决策'])[:18],
        str(r['核心逻辑'])[:16],
    ])

t = Table(all_rows, colWidths=all_cw)
ts = hdr_style(C_BLUE, 7)
for i in range(1, len(all_rows)):
    grade = all_rows[i][4]
    if grade == 'S': ts.add('BACKGROUND', (4,i), (4,i), colors.HexColor('#ff6b6b'))
    elif grade == 'A': ts.add('TEXTCOLOR', (4,i), (4,i), C_GREEN)
    elif grade == 'B': ts.add('TEXTCOLOR', (4,i), (4,i), C_ORANGE)
    if all_rows[i][5] == '✅': ts.add('TEXTCOLOR', (5,i), (5,i), C_GREEN)
    else: ts.add('TEXTCOLOR', (5,i), (5,i), C_RED)
    if all_rows[i][6] == '✅': ts.add('TEXTCOLOR', (6,i), (6,i), C_GREEN)
    else: ts.add('TEXTCOLOR', (6,i), (6,i), C_GREY)
t.setStyle(ts)
story.append(t)

# Footer
story.append(Spacer(1, 3*mm))
story.append(HRFlowable(width='100%', thickness=0.5, color=colors.HexColor('#cccccc'), spaceBefore=4))
story.append(Paragraph(
    f'增强择时 = Timing分 + 筹码突破 + 回踩确认 + ATR风控  |  {n_total}只  |  {today} 盘前  |  QClaw量化系统  |  仅供参考',
    ps('foot', fontSize=7, textColor=C_GREY, alignment=TA_CENTER)
))

doc.build(story)
print(f'PDF: {out}  size={os.path.getsize(out):,} bytes')
