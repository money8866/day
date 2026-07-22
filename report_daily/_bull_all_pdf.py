# -*- coding: utf-8 -*-
import pandas as pd
import os
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.units import mm
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
        ('TOPPADDING', (0,0), (-1,-1), 2.5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2.5),
    ])

# ── 数据 ──
SRC = 'D:/mystock/solo/report_daily/enhanced_timing_bull_all_20260721.csv'
df = pd.read_csv(SRC, encoding='utf-8-sig')
df = df.sort_values('量化择时分', ascending=False).reset_index(drop=True)

today = '20260721'
out = f'D:/mystock/report_daily/enhanced_timing_bull_all_{today}.pdf'
doc = SimpleDocTemplate(
    out, pagesize=landscape(A4),
    leftMargin=10*mm, rightMargin=10*mm,
    topMargin=10*mm, bottomMargin=8*mm
)
story = []

# ═══════════ 标题 ═══════════
story.append(Paragraph('增强择时 · 全市场Bull股池', ps('tit', fontSize=15, textColor=C_BLUE, alignment=TA_CENTER, spaceAfter=2)))
story.append(Paragraph(f'Timing分 + 筹码突破 + 回踩确认 + ATR风控  |  {len(df)}只  |  2026-07-21 盘前', ps('sub', fontSize=8, textColor=C_GREY, alignment=TA_CENTER, spaceAfter=4)))
story.append(HRFlowable(width='100%', thickness=2, color=C_BLUE, spaceAfter=5))

# ═══════════ 概览 ═══════════
n_total = len(df)
grade_order = ['S', 'A', 'B', 'C', 'D', 'E']
grade_cnt = {g: len(df[df['修正后胜率分级'] == g]) for g in grade_order}
n_true = len(df[df['真突破判定'].str.contains('真突破', na=False)])
n_pull = len(df[df['回踩确认'].str.contains('是', na=False)])
n_attn = len(df[df['交易决策'].str.contains('关注|极高胜率', na=False)])

stats_data = [
    ['总数', 'S级', 'A级', 'B级', 'C/D/E级', '真突破', '回踩确认', '可关注'],
    [f'{n_total}只', f'{grade_cnt["S"]}只', f'{grade_cnt["A"]}只', f'{grade_cnt["B"]}只',
     f'{grade_cnt["C"]+grade_cnt["D"]+grade_cnt["E"]}只', f'{n_true}只', f'{n_pull}只', f'{n_attn}只'],
]
t = Table(stats_data, colWidths=[42]*8)
t.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), C_BLUE),
    ('TEXTCOLOR', (0,0), (-1,0), C_WHITE),
    ('FONTNAME', (0,0), (-1,-1), FONT),
    ('FONTSIZE', (0,0), (-1,-1), 8),
    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('ROWBACKGROUNDS', (0,1), (-1,1), [C_GOLD, colors.HexColor('#fff8e1')]),
    ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor('#cccccc')),
    ('TOPPADDING', (0,0), (-1,-1), 3),
    ('BOTTOMPADDING', (0,0), (-1,-1), 3),
]))
story.append(t)
story.append(Spacer(1, 2.5*mm))

# ═══════════ 分级分布 ═══════════
story.append(Paragraph('▶ 胜率分级分布', ps('h2', fontSize=10, textColor=C_ORANGE, spaceBefore=2, spaceAfter=3)))
grade_rows = [['分级', '数量', '占比', '均Timing分', '均修正评分', '真突破率']]
for g in grade_order:
    cnt = grade_cnt[g]
    if cnt == 0: continue
    sub = df[df['修正后胜率分级'] == g]
    tr = len(sub[sub['真突破判定'].str.contains('真突破', na=False)]) / cnt * 100
    grade_rows.append([
        g, str(cnt), f'{cnt/n_total*100:.1f}%',
        f"{sub['量化择时分'].mean():.1f}",
        f"{sub['修正后评分'].mean():.1f}",
        f"{tr:.0f}%",
    ])
t = Table(grade_rows, colWidths=[32, 36, 36, 52, 52, 48])
ts = hdr_style(C_ORANGE, 8)
for i in range(1, len(grade_rows)):
    g = grade_rows[i][0]
    if g == 'S': ts.add('BACKGROUND', (0,i), (0,i), colors.HexColor('#ff6b6b')); ts.add('TEXTCOLOR', (0,i), (0,i), colors.white)
    elif g == 'A': ts.add('TEXTCOLOR', (0,i), (0,i), C_GREEN)
    elif g == 'B': ts.add('TEXTCOLOR', (0,i), (0,i), C_ORANGE)
    elif g in ('C', 'D', 'E'): ts.add('TEXTCOLOR', (0,i), (0,i), C_GREY)
t.setStyle(ts)
story.append(t)
story.append(Spacer(1, 3*mm))

# ═══════════ TOP 30 精选 ═══════════
story.append(Paragraph('▶ TOP 30 精选（按量化择时分排序）', ps('h2', fontSize=10, textColor=C_RED, spaceBefore=2, spaceAfter=3)))

top = df.head(30)
h = ['代码', '名称', '行业', '择时分', '修正分', '分级', '真突破', '回踩', '买点类型', '现价', 'ATR止损', 'ATR止盈', '交易决策']
cw = [22, 30, 36, 28, 26, 20, 26, 22, 96, 30, 32, 32, 88]

rows = [h]
for _, r in top.iterrows():
    code = str(r['代码']).replace('.SH','').replace('.SZ','').zfill(6)
    rows.append([
        code,
        str(r['名称'])[:4],
        str(r['行业'])[:6],
        f"{r['量化择时分']:.1f}",
        f"{r['修正后评分']:.1f}",
        r['修正后胜率分级'],
        '✅' if '真突破' in str(r['真突破判定']) else '❌',
        '✅' if '是' in str(r['回踩确认']) else '❌',
        str(r['推荐买点类型'])[:16],
        f"{r['现价']:.2f}",
        f"{r['ATR动态止损价']:.2f}",
        f"{r['ATR跟踪止盈价']:.2f}",
        str(r['交易决策'])[:14],
    ])

t = Table(rows, colWidths=cw, repeatRows=1)
ts = hdr_style(C_RED, 7)
for i in range(1, len(rows)):
    grade = rows[i][5]
    if grade == 'S': ts.add('BACKGROUND', (5,i), (5,i), colors.HexColor('#ff6b6b')); ts.add('TEXTCOLOR', (5,i), (5,i), colors.white)
    elif grade == 'A': ts.add('TEXTCOLOR', (5,i), (5,i), C_GREEN)
    elif grade == 'B': ts.add('TEXTCOLOR', (5,i), (5,i), C_ORANGE)
    elif grade in ('C','D','E'): ts.add('TEXTCOLOR', (5,i), (5,i), C_GREY)
    if rows[i][6] == '✅': ts.add('TEXTCOLOR', (6,i), (6,i), C_GREEN)
    else: ts.add('TEXTCOLOR', (6,i), (6,i), C_RED)
    if rows[i][7] == '✅': ts.add('TEXTCOLOR', (7,i), (7,i), C_GREEN)
    else: ts.add('TEXTCOLOR', (7,i), (7,i), C_GREY)
t.setStyle(ts)
story.append(t)
story.append(Spacer(1, 3*mm))

# ═══════════ 全量列表 ═══════════
story.append(Paragraph(f'▶ 全量列表（{n_total}只，按择时分降序）', ps('h2', fontSize=10, textColor=C_BLUE, spaceBefore=2, spaceAfter=3)))

all_h = ['代码', '名称', '行业', '择时分', '修正分', '分级', '真突破', '回踩', '现价', 'MA20', 'VWAP', 'ATR止损', 'ATR止盈', '交易决策']
all_cw = [22, 30, 34, 26, 24, 20, 24, 22, 30, 30, 30, 32, 32, 80]
all_rows = [all_h]

for _, r in df.iterrows():
    code = str(r['代码']).replace('.SH','').replace('.SZ','').zfill(6)
    all_rows.append([
        code,
        str(r['名称'])[:4],
        str(r['行业'])[:5],
        f"{r['量化择时分']:.1f}",
        f"{r['修正后评分']:.1f}",
        r['修正后胜率分级'],
        '✅' if '真突破' in str(r['真突破判定']) else '❌',
        '✅' if '是' in str(r['回踩确认']) else '❌',
        f"{r['现价']:.2f}",
        f"{r['MA20']:.2f}",
        f"{r['VWAP']:.2f}",
        f"{r['ATR动态止损价']:.2f}",
        f"{r['ATR跟踪止盈价']:.2f}",
        str(r['交易决策'])[:13],
    ])

t = Table(all_rows, colWidths=all_cw, repeatRows=1)
ts = hdr_style(C_BLUE, 6.5)
for i in range(1, len(all_rows)):
    grade = all_rows[i][5]
    if grade == 'S': ts.add('BACKGROUND', (5,i), (5,i), colors.HexColor('#ff6b6b')); ts.add('TEXTCOLOR', (5,i), (5,i), colors.white)
    elif grade == 'A': ts.add('TEXTCOLOR', (5,i), (5,i), C_GREEN)
    elif grade == 'B': ts.add('TEXTCOLOR', (5,i), (5,i), C_ORANGE)
    elif grade in ('C','D','E'): ts.add('TEXTCOLOR', (5,i), (5,i), C_GREY)
    if all_rows[i][6] == '✅': ts.add('TEXTCOLOR', (6,i), (6,i), C_GREEN)
    else: ts.add('TEXTCOLOR', (6,i), (6,i), C_RED)
    if all_rows[i][7] == '✅': ts.add('TEXTCOLOR', (7,i), (7,i), C_GREEN)
    else: ts.add('TEXTCOLOR', (7,i), (7,i), C_GREY)
t.setStyle(ts)
story.append(t)

# Footer
story.append(Spacer(1, 2*mm))
story.append(HRFlowable(width='100%', thickness=0.5, color=colors.HexColor('#cccccc'), spaceBefore=3))
story.append(Paragraph(
    f'增强择时 = Timing分 + 筹码突破 + 回踩确认 + ATR风控  |  {n_total}只全市场Bull股池  |  2026-07-21 盘前  |  QClaw量化系统  |  仅供参考',
    ps('foot', fontSize=6.5, textColor=C_GREY, alignment=TA_CENTER)
))

doc.build(story)
print(f'PDF: {out}  size={os.path.getsize(out):,} bytes')
