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
df = pd.read_csv('D:/mystock/solo/report_daily/bull_stocks_all_20260721_004031.csv', encoding='utf-8-sig')
df['code'] = df['code'].apply(fix_code)
df = df.sort_values('最终分', ascending=False).reset_index(drop=True)

today = '20260721'
out = f'D:/mystock/report_daily/BullScore_All_{today}.pdf'
doc = SimpleDocTemplate(
    out, pagesize=landscape(A4),
    leftMargin=12*mm, rightMargin=12*mm,
    topMargin=12*mm, bottomMargin=10*mm
)
story = []

# ═══════════ 标题 ═══════════
story.append(Paragraph('BullScore 全量合格股池', ps('tit', fontSize=16, textColor=C_BLUE, alignment=TA_CENTER, spaceAfter=2)))
story.append(Paragraph(f'BullScore v3.1  |  {len(df)}只  |  全量输出  |  {today} 凌晨', ps('sub', fontSize=8, textColor=C_GREY, alignment=TA_CENTER, spaceAfter=4)))
story.append(HRFlowable(width='100%', thickness=2, color=C_BLUE, spaceAfter=6))

# ═══════════ 概览 ── ═══════════
n_total = len(df)
n_a = len(df[df['等级'] == 'A级产业龙头'])
n_b = len(df[df['等级'] == 'B级成长股'])
n_obs = len(df[df['等级'] == '观察名单'])
n_over80 = len(df[df['Bull_v2.1分'] >= 80])
n_over75 = len(df[df['Bull_v2.1分'] >= 75])
n_sp100 = len(df[df['估值空间%'] >= 100])
avg_score = df['Bull_v2.1分'].mean()
avg_sp = df['估值空间%'].mean()

stats_data = [
    ['总数', 'A级龙头', 'B级成长', '观察名单', '≥80分', '≥75分', '空间≥100%', '均评分', '均空间%'],
    [f'{n_total}只', f'{n_a}只', f'{n_b}只', f'{n_obs}只', f'{n_over80}只', f'{n_over75}只', f'{n_sp100}只', f'{avg_score:.1f}', f'{avg_sp:.1f}%'],
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

# ═══════════ TOP 30 ── ═══════════
story.append(Paragraph('▶ TOP 30 精选（按最终分降序）', ps('h2', fontSize=11, textColor=C_RED, spaceBefore=4, spaceAfter=4)))

top = df.head(30)
cols = ['code','name','industry','theme','Bull_v2.1分','最终分','估值空间%','等级',
        '产业景气','预期差','筹码面','利润同比','ROE','PEG']
col_names = ['代码','名称','行业','主题','Bull评分','最终分','空间%','等级','产业景气','预期差','筹码面','利润YoY%','ROE%','PEG']
cw = [42, 36, 52, 52, 36, 36, 30, 46, 34, 34, 28, 34, 24, 22]

rows = [col_names]
for _, r in top.iterrows():
    sp = r['估值空间%']
    rows.append([
        r['code'],
        r['name'][:4],
        str(r['industry'])[:6],
        str(r['theme'])[:8],
        f"{r['Bull_v2.1分']:.1f}",
        f"{r['最终分']:.1f}",
        f"{sp:.0f}%",
        r['等级'][:5] if pd.notna(r['等级']) else '-',
        f"{r['产业景气']:.0f}",
        f"{r['预期差']:.0f}",
        f"{r['筹码面']:.0f}",
        f"{r['利润同比']:.1f}" if pd.notna(r['利润同比']) else '-',
        f"{r['ROE']:.1f}" if pd.notna(r['ROE']) else '-',
        f"{r['PEG']:.2f}" if pd.notna(r['PEG']) else '-',
    ])

t = Table(rows, colWidths=cw)
ts = hdr_style(C_RED, 7)
for i in range(1, len(rows)):
    v = float(rows[i][4])
    if v >= 85: ts.add('BACKGROUND', (4,i), (4,i), colors.HexColor('#fff3e0'))
    elif v >= 80: ts.add('BACKGROUND', (4,i), (4,i), colors.HexColor('#fff8e1'))
    if v >= 85: ts.add('BACKGROUND', (5,i), (5,i), colors.HexColor('#fff3e0'))
    sp_str = rows[i][6].rstrip('%')
    if sp_str != '-':
        spf = float(sp_str)
        if spf >= 100: ts.add('TEXTCOLOR', (6,i), (6,i), C_RED)
        elif spf >= 50: ts.add('TEXTCOLOR', (6,i), (6,i), C_ORANGE)
        else: ts.add('TEXTCOLOR', (6,i), (6,i), C_GREEN)
    lvl = rows[i][7]
    if 'A级' in lvl: ts.add('TEXTCOLOR', (7,i), (7,i), C_RED)
    elif 'B级' in lvl: ts.add('TEXTCOLOR', (7,i), (7,i), C_ORANGE)
t.setStyle(ts)
story.append(t)
story.append(Spacer(1, 4*mm))

# ═══════════ 主题分布 ═══════════
story.append(Paragraph('▶ 主题分布 TOP 10', ps('h2', fontSize=11, textColor=C_BLUE, spaceBefore=4, spaceAfter=4)))
thm_dist = df['theme'].value_counts().head(10)
thm_rows = [['排名','主题','数量','占比','均Bull评分','均最终分','均估值空间%','均利润YoY%']]
for i, (thm, cnt) in enumerate(thm_dist.items(), 1):
    sub = df[df['theme'] == thm]
    thm_rows.append([
        str(i), thm, str(cnt), f'{cnt/n_total*100:.1f}%',
        f"{sub['Bull_v2.1分'].mean():.1f}",
        f"{sub['最终分'].mean():.1f}",
        f"{sub['估值空间%'].mean():.1f}%",
        f"{sub['利润同比'].mean():.0f}%" if '利润同比' in sub.columns and sub['利润同比'].notna().any() else '-',
    ])
t = Table(thm_rows, colWidths=[18, 62, 24, 28, 46, 44, 50, 50])
t.setStyle(hdr_style(C_BLUE, 7.5))
story.append(t)
story.append(Spacer(1, 3*mm))

# ═══════════ 行业分布 ── ═══════════
story.append(Paragraph('▶ 行业分布 TOP 10', ps('h2', fontSize=11, textColor=C_ORANGE, spaceBefore=4, spaceAfter=4)))
ind_dist = df['industry'].value_counts().head(10)
ind_rows = [['排名','行业','数量','占比','均Bull评分','均最终分']]
for i, (ind, cnt) in enumerate(ind_dist.items(), 1):
    sub = df[df['industry'] == ind]
    ind_rows.append([
        str(i), ind, str(cnt), f'{cnt/n_total*100:.1f}%',
        f"{sub['Bull_v2.1分'].mean():.1f}",
        f"{sub['最终分'].mean():.1f}",
    ])
t = Table(ind_rows, colWidths=[18, 72, 24, 28, 46, 44])
t.setStyle(hdr_style(C_ORANGE, 7.5))
story.append(t)
story.append(Spacer(1, 3*mm))

# ═══════════ 等级分布 ── ═══════════
story.append(Paragraph('▶ 等级分布', ps('h2', fontSize=11, textColor=C_PURPLE, spaceBefore=4, spaceAfter=4)))
lvl_dist = df['等级'].value_counts()
lvl_rows = [['等级','数量','占比','均Bull评分','均最终分','均估值空间%','均利润YoY%']]
for lvl, cnt in lvl_dist.items():
    sub = df[df['等级'] == lvl]
    lvl_rows.append([
        lvl, str(cnt), f'{cnt/n_total*100:.1f}%',
        f"{sub['Bull_v2.1分'].mean():.1f}",
        f"{sub['最终分'].mean():.1f}",
        f"{sub['估值空间%'].mean():.1f}%",
        f"{sub['利润同比'].mean():.0f}%",
    ])
t = Table(lvl_rows, colWidths=[56, 32, 28, 46, 44, 50, 50])
ts = hdr_style(C_PURPLE, 8)
# A级B级颜色
lvl_colors = {'A级产业龙头': C_RED, 'B级成长股': C_ORANGE, '观察名单': C_GREY}
for i, row in enumerate(lvl_rows[1:], 1):
    c = lvl_colors.get(row[0], C_DARK)
    ts.add('TEXTCOLOR', (0,i), (0,i), c)
t.setStyle(ts)
story.append(t)
story.append(Spacer(1, 3*mm))

# ═══════════ 全量列表（第二页） ── ═══════════
story.append(PageBreak())
story.append(Paragraph(f'全量列表（{n_total}只，按最终分降序）', ps('h2', fontSize=11, textColor=C_BLUE, spaceBefore=2, spaceAfter=4)))

# 分页：每页约55行
PAGE_SIZE = 55
all_cols = ['code','name','industry','theme','Bull_v2.1分','最终分','估值空间%','等级','产业景气','预期差','筹码面']
all_col_names = ['代码','名称','行业','主题','Bull评分','最终分','空间%','等级','产业','预期差','筹码面']
all_cw = [42, 36, 50, 52, 34, 34, 28, 44, 28, 34, 28]

for page_start in range(0, len(df), PAGE_SIZE):
    page_df = df.iloc[page_start:page_start + PAGE_SIZE].reset_index(drop=True)
    rows2 = [all_col_names]
    for _, r in page_df.iterrows():
        sp = r['估值空间%']
        rows2.append([
            r['code'],
            r['name'][:4],
            str(r['industry'])[:6],
            str(r['theme'])[:8],
            f"{r['Bull_v2.1分']:.1f}",
            f"{r['最终分']:.1f}",
            f"{sp:.0f}%",
            r['等级'][:5] if pd.notna(r['等级']) else '-',
            f"{r['产业景气']:.0f}",
            f"{r['预期差']:.0f}",
            f"{r['筹码面']:.0f}",
        ])

    t = Table(rows2, colWidths=all_cw)
    ts = hdr_style(C_BLUE, 7)
    for i in range(1, len(rows2)):
        v = float(rows2[i][4])
        if v >= 85: ts.add('BACKGROUND', (4,i), (4,i), colors.HexColor('#fff3e0'))
        elif v >= 80: ts.add('BACKGROUND', (4,i), (4,i), colors.HexColor('#fff8e1'))
        sp_str = rows2[i][6].rstrip('%')
        if sp_str != '-':
            spf = float(sp_str)
            if spf >= 100: ts.add('TEXTCOLOR', (6,i), (6,i), C_RED)
            elif spf >= 50: ts.add('TEXTCOLOR', (6,i), (6,i), C_ORANGE)
            else: ts.add('TEXTCOLOR', (6,i), (6,i), C_GREEN)
        lvl = rows2[i][7]
        if 'A级' in lvl: ts.add('TEXTCOLOR', (7,i), (7,i), C_RED)
        elif 'B级' in lvl: ts.add('TEXTCOLOR', (7,i), (7,i), C_ORANGE)
    t.setStyle(ts)
    story.append(t)
    if page_start + PAGE_SIZE < len(df):
        story.append(Spacer(1, 3*mm))

# Footer
story.append(Spacer(1, 3*mm))
story.append(HRFlowable(width='100%', thickness=0.5, color=colors.HexColor('#cccccc'), spaceBefore=4))
story.append(Paragraph(
    f'BullScore v3.1  |  共{n_total}只  |  生成{today}  |  QClaw量化系统  |  仅供参考，不构成投资建议',
    ps('foot', fontSize=7, textColor=C_GREY, alignment=TA_CENTER)
))

doc.build(story)
print(f'PDF: {out}  size={os.path.getsize(out):,} bytes')
