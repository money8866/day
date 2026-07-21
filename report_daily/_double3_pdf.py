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
from reportlab.lib.enums import TA_CENTER, TA_LEFT

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

def fix_code(c):
    s = str(int(float(c))) if not pd.isna(c) else ''
    return s.zfill(6) if s.isdigit() and len(s) < 6 else s

# ── 数据 ──
df = pd.read_csv('D:/mystock/solo/report_daily/double_score_20260721_083744.csv', encoding='utf-8-sig')
df['代码'] = df['代码'].apply(fix_code)
df = df.sort_values('DoubleScore', ascending=False).reset_index(drop=True)

today = '20260721'
out = f'D:/mystock/report_daily/double_score_{today}.pdf'
doc = SimpleDocTemplate(
    out, pagesize=landscape(A4),
    leftMargin=12*mm, rightMargin=12*mm,
    topMargin=12*mm, bottomMargin=10*mm
)
story = []

# ═══════════ 标题 ═══════════
story.append(Paragraph('DoubleScore 精选股池（2026-07-21 盘前版）', ps('tit', fontSize=16, textColor=C_BLUE, alignment=TA_CENTER, spaceAfter=2)))
story.append(Paragraph(f'BullScore v3.1 + Alpha评分  |  {len(df)}只  |  最高{df["DoubleScore"].max():.1f}分  |  {today}', ps('sub', fontSize=8, textColor=C_GREY, alignment=TA_CENTER, spaceAfter=4)))
story.append(HRFlowable(width='100%', thickness=2, color=C_BLUE, spaceAfter=6))

# ═══════════ 概览 ═══════════
n_total = len(df)
n_over90 = len(df[df['DoubleScore'] >= 90])
n_over80 = len(df[df['DoubleScore'] >= 80])
n_over75 = len(df[df['DoubleScore'] >= 75])
n_sp100 = len(df[df['估值空间%'] >= 100])
avg_score = df['DoubleScore'].mean()
avg_sp = df['估值空间%'].mean()

stats_data = [
    ['总数', '≥90分', '≥80分', '≥75分', '空间≥100%', '均评分', '均空间%'],
    [f'{n_total}只', f'{n_over90}只', f'{n_over80}只', f'{n_over75}只', f'{n_sp100}只', f'{avg_score:.1f}', f'{avg_sp:.1f}%'],
]
t = Table(stats_data, colWidths=[52]*7)
t.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), C_BLUE),
    ('TEXTCOLOR', (0,0), (-1,0), C_WHITE),
    ('FONTNAME', (0,0), (-1,-1), FONT),
    ('FONTSIZE', (0,0), (-1,-1), 9),
    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('ROWBACKGROUNDS', (0,1), (-1,1), [C_GOLD, colors.HexColor('#fff8e1')]),
    ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor('#cccccc')),
    ('TOPPADDING', (0,0), (-1,-1), 5),
    ('BOTTOMPADDING', (0,0), (-1,-1), 5),
]))
story.append(t)
story.append(Spacer(1, 3*mm))

# ═══════════ TOP 20 ═══════════
story.append(Paragraph('▶ TOP 20 精选', ps('h2', fontSize=11, textColor=C_RED, spaceBefore=4, spaceAfter=4)))

top = df.head(20)
h = ['排名', '代码', '名称', '市值(亿)', '营收YoY%', '利润YoY%', 'ROE%', 'PEG', '估值空间%', '龙头类型', '非经常损益%', 'DoubleScore', '核心逻辑']
cw = [20, 42, 36, 36, 36, 40, 28, 24, 36, 36, 40, 40, 130]

rows = [h]
for i, (_, r) in enumerate(top.iterrows(), 1):
    logic = str(r['核心逻辑']) if pd.notna(r['核心逻辑']) else ''
    profit_yoy = r['利润YoY%']
    profit_display = f"{profit_yoy:.0f}%" if profit_yoy < 9999 else ">9999%"
    rows.append([
        str(i),
        r['代码'],
        r['名称'][:4],
        f"{r['市值(亿)']:.0f}",
        f"{r['营收YoY%']:.1f}",
        profit_display,
        f"{r['ROE%']:.1f}",
        f"{r['PEG']:.2f}",
        f"{r['估值空间%']:.0f}%",
        r['龙头类型'][:4] if pd.notna(r['龙头类型']) else '-',
        f"{r['非经常损益%']:.1f}%" if pd.notna(r['非经常损益%']) else '-',
        f"{r['DoubleScore']:.1f}",
        logic[:30],
    ])

t = Table(rows, colWidths=cw)
ts = hdr_style(C_RED, 7)
for i in range(1, len(rows)):
    v = float(rows[i][11])
    if v >= 90: ts.add('BACKGROUND', (11,i), (11,i), colors.HexColor('#ff6b6b'))
    elif v >= 85: ts.add('BACKGROUND', (11,i), (11,i), colors.HexColor('#fff3e0'))
    elif v >= 80: ts.add('BACKGROUND', (11,i), (11,i), colors.HexColor('#fff8e1'))
    sp_str = rows[i][8].rstrip('%')
    if sp_str != '-':
        spf = float(sp_str)
        if spf >= 100: ts.add('TEXTCOLOR', (8,i), (8,i), C_RED)
        elif spf >= 50: ts.add('TEXTCOLOR', (8,i), (8,i), C_ORANGE)
t.setStyle(ts)
story.append(t)
story.append(Spacer(1, 4*mm))

# ═══════════ 评分分档 ═══════════
story.append(Paragraph('▶ 评分分档分布', ps('h2', fontSize=11, textColor=C_BLUE, spaceBefore=4, spaceAfter=4)))
score_bins = [
    ('≥90分', df[df['DoubleScore'] >= 90]),
    ('85-90分', df[(df['DoubleScore'] >= 85) & (df['DoubleScore'] < 90)]),
    ('80-85分', df[(df['DoubleScore'] >= 80) & (df['DoubleScore'] < 85)]),
    ('75-80分', df[(df['DoubleScore'] >= 75) & (df['DoubleScore'] < 80)]),
    ('<75分', df[df['DoubleScore'] < 75]),
]
score_rows = [['分档', '数量', '占比', '均估值空间%']]
for label, sub in score_bins:
    cnt = len(sub)
    score_rows.append([
        label, str(cnt), f'{cnt/n_total*100:.1f}%',
        f"{sub['估值空间%'].mean():.1f}%" if cnt > 0 else '-',
    ])
t = Table(score_rows, colWidths=[56, 40, 40, 56])
t.setStyle(hdr_style(C_BLUE, 8))
story.append(t)
story.append(Spacer(1, 3*mm))

# ═══════════ 全量列表 ═══════════
story.append(PageBreak())
story.append(Paragraph(f'全量列表（{n_total}只）', ps('h2', fontSize=11, textColor=C_BLUE, spaceBefore=2, spaceAfter=4)))

all_h = ['排名', '代码', '名称', '市值(亿)', '营收YoY%', '利润YoY%', 'ROE%', 'PEG', '估值空间%', '龙头类型', '非经常损益%', 'DoubleScore', '核心逻辑']
all_cw = [20, 42, 36, 36, 36, 40, 28, 24, 36, 36, 40, 40, 130]
all_rows = [all_h]

for i, (_, r) in enumerate(df.iterrows(), 1):
    logic = str(r['核心逻辑'])[:28] if pd.notna(r['核心逻辑']) else ''
    profit_yoy = r['利润YoY%']
    profit_display = f"{profit_yoy:.0f}%" if profit_yoy < 9999 else ">9999%"
    all_rows.append([
        str(i),
        r['代码'],
        r['名称'][:4],
        f"{r['市值(亿)']:.0f}",
        f"{r['营收YoY%']:.1f}",
        profit_display,
        f"{r['ROE%']:.1f}",
        f"{r['PEG']:.2f}",
        f"{r['估值空间%']:.0f}%",
        r['龙头类型'][:4] if pd.notna(r['龙头类型']) else '-',
        f"{r['非经常损益%']:.1f}%" if pd.notna(r['非经常损益%']) else '-',
        f"{r['DoubleScore']:.1f}",
        logic,
    ])

t = Table(all_rows, colWidths=all_cw)
ts = hdr_style(C_BLUE, 7)
for i in range(1, len(all_rows)):
    v = float(all_rows[i][11])
    if v >= 90: ts.add('BACKGROUND', (11,i), (11,i), colors.HexColor('#ff6b6b'))
    elif v >= 85: ts.add('BACKGROUND', (11,i), (11,i), colors.HexColor('#fff3e0'))
    elif v >= 80: ts.add('BACKGROUND', (11,i), (11,i), colors.HexColor('#fff8e1'))
    sp_str = all_rows[i][8].rstrip('%')
    if sp_str not in ['-', '>9999%']:
        spf = float(sp_str)
        if spf >= 100: ts.add('TEXTCOLOR', (8,i), (8,i), C_RED)
        elif spf >= 50: ts.add('TEXTCOLOR', (8,i), (8,i), C_ORANGE)
t.setStyle(ts)
story.append(t)

# Footer
story.append(Spacer(1, 3*mm))
story.append(HRFlowable(width='100%', thickness=0.5, color=colors.HexColor('#cccccc'), spaceBefore=4))
story.append(Paragraph(
    f'DoubleScore = BullScore v3.1 x Alpha评分  |  {n_total}只  |  生成{today} 08:30  |  QClaw量化系统  |  仅供参考',
    ps('foot', fontSize=7, textColor=C_GREY, alignment=TA_CENTER)
))

doc.build(story)
print(f'PDF: {out}  size={os.path.getsize(out):,} bytes')
