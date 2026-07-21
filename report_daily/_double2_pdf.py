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
df = pd.read_csv('D:/mystock/solo/report_daily/double_score_20260721_074911.csv', encoding='utf-8-sig')
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
story.append(Paragraph('DoubleScore 精选股池（2026-07-21 早间版）', ps('tit', fontSize=16, textColor=C_BLUE, alignment=TA_CENTER, spaceAfter=2)))
story.append(Paragraph(f'BullScore v3.1 + Alpha评分双维度筛选  |  {len(df)}只  |  早间更新  |  {today}', ps('sub', fontSize=8, textColor=C_GREY, alignment=TA_CENTER, spaceAfter=4)))
story.append(HRFlowable(width='100%', thickness=2, color=C_BLUE, spaceAfter=6))

# ═══════════ 概览 ── ═══════════
n_total = len(df)
n_over90 = len(df[df['DoubleScore'] >= 90])
n_over80 = len(df[df['DoubleScore'] >= 80])
n_over75 = len(df[df['DoubleScore'] >= 75])
n_sp100 = len(df[df['估值空间%'] >= 100])
avg_score = df['DoubleScore'].mean()
avg_sp = df['估值空间%'].mean()
max_score = df['DoubleScore'].max()
min_score = df['DoubleScore'].min()

stats_data = [
    ['总数', '≥90分', '≥80分', '≥75分', '空间≥100%', '均评分', '最高分', '最低分', '均空间%'],
    [f'{n_total}只', f'{n_over90}只', f'{n_over80}只', f'{n_over75}只', f'{n_sp100}只', f'{avg_score:.1f}', f'{max_score:.1f}', f'{min_score:.1f}', f'{avg_sp:.1f}%'],
]
t = Table(stats_data, colWidths=[46]*9)
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

# ═══════════ TOP 20 ── ═══════════
story.append(Paragraph('▶ TOP 20 精选（按DoubleScore降序）', ps('h2', fontSize=11, textColor=C_RED, spaceBefore=4, spaceAfter=4)))

top = df.head(20)
h = ['排名', '代码', '名称', '主题', '市值(亿)', '营收YoY%', '利润YoY%', 'ROE%', '毛利率%', 'PEG', '估值空间%', '龙头类型', 'DoubleScore', '核心逻辑']
cw = [20, 42, 36, 54, 36, 36, 40, 28, 30, 24, 36, 36, 40, 120]

rows = [h]
for i, (_, r) in enumerate(top.iterrows(), 1):
    logic = str(r['核心逻辑']) if pd.notna(r['核心逻辑']) else ''
    profit_yoy = r['利润YoY%']
    # 异常值处理（江波龙68299%→显示为 ">9999%")
    profit_display = f"{profit_yoy:.0f}%" if profit_yoy < 9999 else ">9999%"
    rows.append([
        str(i),
        r['代码'],
        r['名称'][:4],
        str(r['主题'])[:10],
        f"{r['市值(亿)']:.0f}",
        f"{r['营收YoY%']:.1f}",
        profit_display,
        f"{r['ROE%']:.1f}",
        f"{r['毛利率%']:.1f}",
        f"{r['PEG']:.2f}",
        f"{r['估值空间%']:.0f}%",
        r['龙头类型'][:4] if pd.notna(r['龙头类型']) else '-',
        f"{r['DoubleScore']:.1f}",
        logic[:30],
    ])

t = Table(rows, colWidths=cw)
ts = hdr_style(C_RED, 7)
for i in range(1, len(rows)):
    v = float(rows[i][12])
    if v >= 90: ts.add('BACKGROUND', (12,i), (12,i), colors.HexColor('#ff6b6b'))
    elif v >= 85: ts.add('BACKGROUND', (12,i), (12,i), colors.HexColor('#fff3e0'))
    elif v >= 80: ts.add('BACKGROUND', (12,i), (12,i), colors.HexColor('#fff8e1'))
    sp_str = rows[i][10].rstrip('%')
    if sp_str != '-' and sp_str != '>9999%':
        spf = float(sp_str)
        if spf >= 100: ts.add('TEXTCOLOR', (10,i), (10,i), C_RED)
        elif spf >= 50: ts.add('TEXTCOLOR', (10,i), (10,i), C_ORANGE)
        else: ts.add('TEXTCOLOR', (10,i), (10,i), C_GREEN)
t.setStyle(ts)
story.append(t)
story.append(Spacer(1, 4*mm))

# ═══════════ 主题分布 ── ═══════════
story.append(Paragraph('▶ 主题分布', ps('h2', fontSize=11, textColor=C_BLUE, spaceBefore=4, spaceAfter=4)))
thm_dist = df['主题'].value_counts()
thm_rows = [['排名', '主题', '数量', '占比', '均评分', '均利润增速%', '均PEG', '均估值空间%']]
for i, (thm, cnt) in enumerate(thm_dist.items(), 1):
    sub = df[df['主题'] == thm]
    thm_rows.append([
        str(i), thm, str(cnt), f'{cnt/n_total*100:.1f}%',
        f"{sub['DoubleScore'].mean():.1f}",
        f"{sub['利润YoY%'].mean():.0f}%",
        f"{sub['PEG'].mean():.2f}",
        f"{sub['估值空间%'].mean():.1f}%",
    ])
t = Table(thm_rows, colWidths=[20, 66, 24, 28, 36, 52, 36, 48])
t.setStyle(hdr_style(C_BLUE, 7.5))
story.append(t)
story.append(Spacer(1, 3*mm))

# ═══════════ 龙头类型分布 ── ═══════════
story.append(Paragraph('▶ 龙头类型分布', ps('h2', fontSize=11, textColor=C_ORANGE, spaceBefore=4, spaceAfter=4)))
lvl_dist = df['龙头类型'].value_counts()
lvl_rows = [['龙头类型', '数量', '占比', '均评分', '均利润增速%', '均PEG', '均估值空间%']]
for lt, cnt in lvl_dist.items():
    sub = df[df['龙头类型'] == lt]
    lvl_rows.append([
        lt, str(cnt), f'{cnt/n_total*100:.1f}%',
        f"{sub['DoubleScore'].mean():.1f}",
        f"{sub['利润YoY%'].mean():.0f}%",
        f"{sub['PEG'].mean():.2f}",
        f"{sub['估值空间%'].mean():.1f}%",
    ])
t = Table(lvl_rows, colWidths=[56, 32, 28, 36, 52, 36, 48])
t.setStyle(hdr_style(C_ORANGE, 8))
story.append(t)
story.append(Spacer(1, 3*mm))

# ═══════════ 核心逻辑分布 ── ═══════════
story.append(Paragraph('▶ 核心逻辑标签', ps('h2', fontSize=11, textColor=C_PURPLE, spaceBefore=4, spaceAfter=4)))
all_logic_tags = {}
for v in df['核心逻辑'].dropna():
    for tag in str(v).split(' + '):
        tag = tag.strip()
        all_logic_tags[tag] = all_logic_tags.get(tag, 0) + 1

logic_rows = [['逻辑标签', '覆盖数量', '占比']]
for tag, cnt in sorted(all_logic_tags.items(), key=lambda x: -x[1]):
    logic_rows.append([tag, str(cnt), f'{cnt/n_total*100:.1f}%'])
t = Table(logic_rows, colWidths=[90, 36, 36])
t.setStyle(hdr_style(C_PURPLE, 8))
story.append(t)
story.append(Spacer(1, 3*mm))

# ═══════════ 全量列表 ── ═══════════
story.append(PageBreak())
story.append(Paragraph(f'全量列表（{n_total}只）', ps('h2', fontSize=11, textColor=C_BLUE, spaceBefore=2, spaceAfter=4)))

all_h = ['排名', '代码', '名称', '主题', '市值(亿)', '利润YoY%', 'ROE%', '毛利率%', 'PEG', '估值空间%', '龙头类型', 'DoubleScore', '核心逻辑']
all_cw = [20, 42, 36, 54, 36, 40, 28, 30, 24, 36, 36, 40, 120]
all_rows = [all_h]

for i, (_, r) in enumerate(df.iterrows(), 1):
    logic = str(r['核心逻辑'])[:28] if pd.notna(r['核心逻辑']) else ''
    profit_yoy = r['利润YoY%']
    profit_display = f"{profit_yoy:.0f}%" if profit_yoy < 9999 else ">9999%"
    all_rows.append([
        str(i),
        r['代码'],
        r['名称'][:4],
        str(r['主题'])[:10],
        f"{r['市值(亿)']:.0f}",
        profit_display,
        f"{r['ROE%']:.1f}",
        f"{r['毛利率%']:.1f}",
        f"{r['PEG']:.2f}",
        f"{r['估值空间%']:.0f}%",
        r['龙头类型'][:4] if pd.notna(r['龙头类型']) else '-',
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
    sp_str = all_rows[i][9].rstrip('%')
    if sp_str not in ['-', '>9999%']:
        spf = float(sp_str)
        if spf >= 100: ts.add('TEXTCOLOR', (9,i), (9,i), C_RED)
        elif spf >= 50: ts.add('TEXTCOLOR', (9,i), (9,i), C_ORANGE)
        else: ts.add('TEXTCOLOR', (9,i), (9,i), C_GREEN)
t.setStyle(ts)
story.append(t)

# Footer
story.append(Spacer(1, 3*mm))
story.append(HRFlowable(width='100%', thickness=0.5, color=colors.HexColor('#cccccc'), spaceBefore=4))
story.append(Paragraph(
    f'DoubleScore = BullScore v3.1 x Alpha评分  |  {n_total}只  |  生成{today} 07:50  |  QClaw量化系统  |  仅供参考',
    ps('foot', fontSize=7, textColor=C_GREY, alignment=TA_CENTER)
))

doc.build(story)
print(f'PDF: {out}  size={os.path.getsize(out):,} bytes')
