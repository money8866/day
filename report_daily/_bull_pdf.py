# -*- coding: utf-8 -*-
import pandas as pd
import os
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.pagesizes import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, PageBreak
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

FONT = 'Chinese'
try:
    pdfmetrics.registerFont(TTFont(FONT, r'C:\Windows\Fonts\msyh.ttc'))
except:
    FONT = 'Helvetica'

pdfmetrics.registerFont(TTFont('Chinese', r'C:\Windows\Fonts\msyh.ttc'))

# ── 配色 ──
C_BLUE   = colors.HexColor('#1a3a8a')
C_RED    = colors.HexColor('#c0392b')
C_GREEN  = colors.HexColor('#27ae60')
C_ORANGE = colors.HexColor('#e67e22')
C_PURPLE = colors.HexColor('#8e44ad')
C_GREY   = colors.HexColor('#6c757d')
C_LIGHT  = colors.HexColor('#f0f4f8')
C_WHITE  = colors.whitesmoke
C_DARK   = colors.HexColor('#1a1a2e')
C_TEXT   = colors.HexColor('#2c3e50')

def ps(name, **kw):
    base = dict(fontName=FONT, fontSize=9, leading=13, textColor=C_TEXT)
    base.update(kw)
    return ParagraphStyle(name, **base)

def tstyle(header_color=C_BLUE, fontsize=8):
    return TableStyle([
        ('BACKGROUND', (0,0), (-1,0), header_color),
        ('TEXTCOLOR', (0,0), (-1,0), C_WHITE),
        ('FONTNAME', (0,0), (-1,-1), FONT),
        ('FONTSIZE', (0,0), (-1,-1), fontsize),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor('#f8f9fa'), colors.HexColor('#eef1f5')]),
        ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor('#cccccc')),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ])

def h1(text, color=C_BLUE):
    return Paragraph(f'<b>{text}</b>', ps('h1', fontSize=14, textColor=color, spaceAfter=4))

def h2(text, color=C_BLUE):
    return Paragraph(f'<b>{text}</b>', ps('h2', fontSize=11, textColor=color, spaceAfter=2, spaceBefore=10))

def note(text):
    return Paragraph(text, ps('note', fontSize=8, textColor=C_GREY, spaceBefore=2))

# ── 加载数据 ──
df = pd.read_csv('D:/mystock/solo/multi_factor_picker/output/bull_stocks_20260720_084735.csv', encoding='utf-8-sig')
today = '2026-07-20'

out = f'D:/mystock/report_daily/BullScore_Qualified_{today}.pdf'
doc = SimpleDocTemplate(
    out, pagesize=landscape(A4),
    leftMargin=15*mm, rightMargin=15*mm,
    topMargin=15*mm, bottomMargin=15*mm
)
story = []

# ══════════════════════════════════════
# 1. 标题
# ══════════════════════════════════════
story.append(Paragraph('BullScore 精选股票池  |  2026-07-20', 
    ps('title', fontSize=18, textColor=C_BLUE, alignment=TA_CENTER, spaceAfter=4)))
story.append(Paragraph('基于产业景气 / 预期差 / 业绩质量 / 机构认可 / 筹码面 / 估值安全 六大维度综合评分',
    ps('subtitle', fontSize=9, textColor=C_GREY, alignment=TA_CENTER, spaceAfter=6)))
story.append(HRFlowable(width='100%', thickness=2, color=C_BLUE, spaceAfter=8))

# ══════════════════════════════════════
# 2. 概览统计
# ══════════════════════════════════════
total = len(df)
score_mean = df['Bull_v2.1分'].mean()
score_max = df['Bull_v2.1分'].max()
score_min = df['Bull_v2.1分'].min()
top80 = len(df[df['Bull_v2.1分'] >= 80])
top75 = len(df[df['Bull_v2.1分'] >= 75])

industry_dist = df['industry'].value_counts().head(8)
theme_dist = df['theme'].value_counts().head(8)

stats_data = [
    ['合格股池', '平均评分', '最高评分', '≥80分', '≥75分', '≥70分'],
    [f'{total}只', f'{score_mean:.1f}', f'{score_max:.1f}', f'{top80}只', f'{top75}只', f'{len(df[df["Bull_v2.1分"]>=70])}只'],
]
ts = tstyle(header_color=C_BLUE)
ts.add('FONTSIZE', (0,0), (-1,-1), 9)
ts.add('FONTSIZE', (0,0), (-1,0), 8)
t = Table(stats_data, colWidths=[60]*6)
t.setStyle(ts)
story.append(t)
story.append(Spacer(1, 6*mm))

# ══════════════════════════════════════
# 3. TOP 20 精选
# ══════════════════════════════════════
story.append(h2('▶ TOP 20 精选股池', C_RED))

top20 = df.nlargest(20, '最终分')[
    ['code','name','industry','theme','Bull_v2.1分','最终分',
     '估值空间%','龙头类型','产业景气','预期差','筹码面']
].copy()
top20['code'] = top20['code'].astype(str)

def color_score(v):
    if v >= 80: return C_RED
    elif v >= 75: return C_ORANGE
    elif v >= 70: return C_PURPLE
    return C_TEXT

def color_up(v):
    if pd.isna(v) or v == 0: return C_TEXT
    return C_RED if v < 0 else C_GREEN

headers = ['代码','名称','行业','主题','Bull评分','最终分','估值空间%','类型','产业景气','预期差','筹码面']
col_w = [55, 60, 65, 65, 52, 52, 52, 38, 52, 52, 52]

rows = [headers]
for _, r in top20.iterrows():
    bull = r['Bull_v2.1分']
    final = r['最终分']
    val_space = r['估值空间%']
    rows.append([
        str(int(r['code'])),
        r['name'][:4],
        r['industry'][:5] if pd.notna(r['industry']) else '-',
        r['theme'][:6] if pd.notna(r['theme']) else '-',
        f'{bull:.1f}',
        f'{final:.1f}',
        f'{val_space:.0f}%' if pd.notna(val_space) else '-',
        r['龙头类型'][:2] if pd.notna(r['龙头类型']) else '-',
        f"{r['产业景气']:.0f}",
        f"{r['预期差']:.0f}",
        f"{r['筹码面']:.0f}",
    ])

t = Table(rows, colWidths=col_w)
ts = tstyle(header_color=C_RED, fontsize=7.5)
# 对评分列着色
for i, row in enumerate(rows[1:], 1):
    bull_v = float(row[4])
    if bull_v >= 80:
        ts.add('BACKGROUND', (4,i), (4,i), colors.HexColor('#fff3e0'))
        ts.add('BACKGROUND', (5,i), (5,i), colors.HexColor('#fff3e0'))
    val = row[6]
    if val != '-':
        v = float(val.rstrip('%'))
        ts.add('TEXTCOLOR', (6,i), (6,i), C_RED if v < 0 else C_GREEN)
t.setStyle(ts)
story.append(t)
story.append(Spacer(1, 4*mm))

# ══════════════════════════════════════
# 4. 行业分布
# ══════════════════════════════════════
story.append(h2('▶ 行业分布 TOP 8', C_BLUE))
ind_data = [['排名','行业','数量','占比']]
for i, (ind, cnt) in enumerate(industry_dist.items(), 1):
    ind_data.append([str(i), ind, str(cnt), f'{cnt/total*100:.1f}%'])
t = Table(ind_data, colWidths=[30, 80, 40, 40])
ts = tstyle(header_color=C_BLUE)
ts.add('ALIGN', (2,0), (-1,-1), 'CENTER')
t.setStyle(ts)
story.append(t)
story.append(Spacer(1, 4*mm))

# ══════════════════════════════════════
# 5. 主题分布
# ══════════════════════════════════════
story.append(h2('▶ 主题分布 TOP 8', C_ORANGE))
thm_data = [['排名','主题','数量','占比']]
for i, (thm, cnt) in enumerate(theme_dist.items(), 1):
    thm_data.append([str(i), thm, str(cnt), f'{cnt/total*100:.1f}%'])
t = Table(thm_data, colWidths=[30, 100, 40, 40])
ts = tstyle(header_color=C_ORANGE)
ts.add('ALIGN', (2,0), (-1,-1), 'CENTER')
t.setStyle(ts)
story.append(t)
story.append(Spacer(1, 4*mm))

# ══════════════════════════════════════
# 6. 70-80分段潜力股
# ══════════════════════════════════════
story.append(h2('▶ 70-80分潜力股（关注名单）', C_PURPLE))
mid = df[(df['Bull_v2.1分'] >= 70) & (df['Bull_v2.1分'] < 80)].nlargest(15, '最终分')[
    ['code','name','industry','theme','Bull_v2.1分','最终分','估值空间%','龙头类型']
].copy()
mid['code'] = mid['code'].astype(str)
headers2 = ['代码','名称','行业','主题','Bull评分','最终分','估值空间%','类型']
col_w2 = [55, 60, 65, 75, 52, 52, 52, 38]
rows2 = [headers2]
for _, r in mid.iterrows():
    val = r['估值空间%']
    rows2.append([
        str(int(r['code'])), r['name'][:4],
        r['industry'][:5] if pd.notna(r['industry']) else '-',
        r['theme'][:8] if pd.notna(r['theme']) else '-',
        f"{r['Bull_v2.1分']:.1f}", f"{r['最终分']:.1f}",
        f'{val:.0f}%' if pd.notna(val) else '-',
        r['龙头类型'][:2] if pd.notna(r['龙头类型']) else '-',
    ])
t = Table(rows2, colWidths=col_w2)
ts = tstyle(header_color=C_PURPLE, fontsize=7.5)
t.setStyle(ts)
story.append(t)
story.append(Spacer(1, 4*mm))

# ══════════════════════════════════════
# 7. Footer
# ══════════════════════════════════════
story.append(HRFlowable(width='100%', thickness=0.5, color=colors.HexColor('#cccccc'), spaceBefore=4))
story.append(Paragraph(
    f'数据来源：Tushare  |  BullScore v3.1  |  合格阈值≥55分  |  共{total}只  |  生成时间 {today} 08:47  |  QClaw量化系统  |  仅供参考，不构成投资建议',
    ps('footer', fontSize=7, textColor=C_GREY, alignment=TA_CENTER)
))

doc.build(story)
print(f'PDF: {out}  size={os.path.getsize(out):,} bytes')
