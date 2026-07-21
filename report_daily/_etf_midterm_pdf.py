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
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ])

# ── 数据 ──
df = pd.read_csv('D:/mystock/solo/report_daily/etf_midterm_rating.csv', encoding='utf-8-sig')
today = '20260721'
out = f'D:/mystock/report_daily/etf_midterm_rating_{today}.pdf'
doc = SimpleDocTemplate(
    out, pagesize=landscape(A4),
    leftMargin=12*mm, rightMargin=12*mm,
    topMargin=12*mm, bottomMargin=10*mm
)
story = []

# ═══════════ 标题 ═══════════
story.append(Paragraph('ETF中期评级报告', ps('tit', fontSize=16, textColor=C_BLUE, alignment=TA_CENTER, spaceAfter=2)))
story.append(Paragraph(f'中期趋势择时  |  12只ETF  |  {today}', ps('sub', fontSize=8, textColor=C_GREY, alignment=TA_CENTER, spaceAfter=4)))
story.append(HRFlowable(width='100%', thickness=2, color=C_BLUE, spaceAfter=6))

# ═══════════ 概览 ═══════════
n_total = len(df)
n_a = len(df[df['rating'] == 'A'])
n_b = len(df[df['rating'] == 'B'])
n_c = len(df[df['rating'] == 'C'])
n_def = len(df[df['is_defensive'] == True])
n_split = len(df[df['has_split'] == True])

stats_data = [
    ['总数', 'A级(防御)', 'B级(中性)', 'C级(回避)', '防御类', '近期除权'],
    [f'{n_total}只', f'{n_a}只', f'{n_b}只', f'{n_c}只', f'{n_def}只', f'{n_split}只'],
]
t = Table(stats_data, colWidths=[48]*6)
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

# ═══════════ 评级说明 ═══════════
desc_data = [
    ['评级', '含义', '数量'],
    ['A  [首选/防御]', '中期多头排列，防御属性强，适合弱市', f'{n_a}只'],
    ['B  [中性]', '中线趋势中性，待观察，不追高', f'{n_b}只'],
    ['C  [回避]', '中期趋势破坏，均线空头，高波动或缩量', f'{n_c}只'],
]
t = Table(desc_data, colWidths=[80, 200, 40])
ts = TableStyle([
    ('BACKGROUND', (0,0), (-1,0), C_GREY),
    ('TEXTCOLOR', (0,0), (-1,0), C_WHITE),
    ('FONTNAME', (0,0), (-1,-1), FONT),
    ('FONTSIZE', (0,0), (-1,-1), 8),
    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor('#e8f5e9'), colors.HexColor('#fff8e1'), colors.HexColor('#ffebee')]),
    ('GRID', (0,0), (-1,-1), 0.3, colors.HexColor('#cccccc')),
    ('TOPPADDING', (0,0), (-1,-1), 3),
    ('BOTTOMPADDING', (0,0), (-1,-1), 3),
])
t.setStyle(ts)
story.append(t)
story.append(Spacer(1, 3*mm))

# ═══════════ 首选ETF（force_rank=1,2）═══════════
top_etfs = df[df['force_rank'] <= 2].copy()
if not top_etfs.empty:
    story.append(Paragraph('▶ ⭐ 首选ETF', ps('h2', fontSize=11, textColor=C_GOLD, spaceBefore=4, spaceAfter=4)))

    for _, r in top_etfs.iterrows():
        name = r['name']
        score = r['rating_score']
        rating = r['rating']
        close = r['close']
        rrs = r['rrs_20']
        ret20 = r['etf_ret_20d']
        bm20 = r['bm_ret_20d']
        div = r['divergence']
        div_trend = r['div_trend']
        bull = r['bull_alignment']
        ma20 = '✅' if r['above_ma20'] else '❌'
        ma60 = '✅' if r['above_ma60'] else '❌'
        ema_bull = '✅' if r['ema_bull'] else '❌'
        dd10 = r['dd_10d']
        dd20 = r['dd_20d']
        weekly = '✅' if r['weekly_broken'] else '❌'
        vol_r = r['vol_ratio']
        vola = r['volatility']
        defense = '✅防御' if r['is_defensive'] else ''
        reason = str(r['reason'])[:60]

        # 计算超额收益
        excess = ret20 - bm20 if pd.notna(ret20) and pd.notna(bm20) else 0

        bg = colors.HexColor('#fff8e1') if rating == 'A' else colors.HexColor('#f8f9fa')
        card_data = [
            [f'  {name}', f'评级: {rating}  评分: {score:.0f}  {'⭐首选' if r['force_tag'] == "⭐ 首选" else "⭐次选"}'],
            [f'  收盘: {close}    RRS20: {rrs:.1f}    20日涨跌: {ret20:+.1f}%    基准20日: {bm20:+.1f}%    超额: {excess:+.1f}%',
             f'  10日回撤: {dd10:.1f}%    20日回撤: {dd20:.1f}%'],
            [f'  MA20上方: {ma20}    MA60上方: {ma60}    EMA多头: {ema_bull}    周线突破: {weekly}    防御类: {defense}',
             f'  量比: {vol_r:.2f}    波动率: {vola:.0f}%    分歧度: {div:.2f}    分歧趋势: {div_trend:+.1f}'],
            [f'  {reason}', ''],
        ]
        t = Table(card_data, colWidths=[260, 340])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), C_GOLD if r['force_rank']==1 else colors.HexColor('#c0a060')),
            ('BACKGROUND', (0,1), (-1,-1), bg),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('FONTNAME', (0,0), (-1,-1), FONT),
            ('FONTSIZE', (0,0), (-1,0), 9),
            ('FONTSIZE', (0,1), (-1,-1), 8),
            ('SPAN', (0,0), (1,0)),
            ('SPAN', (0,3), (1,3)),
            ('ALIGN', (0,0), (-1,-1), 'LEFT'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('TOPPADDING', (0,0), (-1,-1), 3),
            ('BOTTOMPADDING', (0,0), (-1,-1), 3),
            ('LEFTPADDING', (0,0), (-1,-1), 6),
            ('GRID', (0,0), (-1,-1), 0.3, colors.HexColor('#cccccc')),
        ]))
        story.append(t)
        story.append(Spacer(1, 2*mm))

story.append(Spacer(1, 3*mm))

# ═══════════ 全量ETF列表 ═══════════
story.append(Paragraph('▶ 全量ETF评级列表', ps('h2', fontSize=11, textColor=C_BLUE, spaceBefore=4, spaceAfter=4)))

h = ['名称', '评级', '评分', '收盘', 'RRS20', '20日涨跌', '基准', '超额', '10日回撤', 'MA20', 'MA60', '防御', '量比', '波动率', '原因']
cw = [50, 28, 28, 36, 36, 40, 36, 36, 40, 30, 30, 30, 30, 36, 120]

rows = [h]
for _, r in df.iterrows():
    rows.append([
        str(r['name'])[:6],
        r['rating'],
        f"{r['rating_score']:.0f}",
        f"{r['close']:.2f}",
        f"{r['rrs_20']:.1f}",
        f"{r['etf_ret_20d']:+.1f}%",
        f"{r['bm_ret_20d']:+.1f}%",
        f"{(r['etf_ret_20d']-r['bm_ret_20d']):+.1f}%",
        f"{r['dd_10d']:.1f}%",
        '✅' if r['above_ma20'] else '❌',
        '✅' if r['above_ma60'] else '❌',
        '✅' if r['is_defensive'] else '',
        f"{r['vol_ratio']:.2f}",
        f"{r['volatility']:.0f}%",
        str(r['reason'])[:24],
    ])

t = Table(rows, colWidths=cw)
ts = hdr_style(C_BLUE, 7.5)
for i in range(1, len(rows)):
    rating = rows[i][1]
    if rating == 'A': ts.add('BACKGROUND', (1,i), (1,i), colors.HexColor('#c8e6c9'))
    elif rating == 'B': ts.add('BACKGROUND', (1,i), (1,i), colors.HexColor('#fff9c4'))
    elif rating == 'C': ts.add('BACKGROUND', (1,i), (1,i), colors.HexColor('#ffcdd2'))
    # 超额着色
    ex_str = rows[i][7]
    if ex_str and ex_str != '-':
        ex_val = float(ex_str.rstrip('%'))
        if ex_val > 0: ts.add('TEXTCOLOR', (7,i), (7,i), C_GREEN)
        elif ex_val < -5: ts.add('TEXTCOLOR', (7,i), (7,i), C_RED)
    # 评分着色
    score = float(rows[i][2])
    if score >= 75: ts.add('TEXTCOLOR', (2,i), (2,i), C_GREEN)
    elif score < 30: ts.add('TEXTCOLOR', (2,i), (2,i), C_RED)
t.setStyle(ts)
story.append(t)

# ═══════════ Footer ═══════════
story.append(Spacer(1, 3*mm))
story.append(HRFlowable(width='100%', thickness=0.5, color=colors.HexColor('#cccccc'), spaceBefore=4))
story.append(Paragraph(
    f'A级=中期多头排列防御属性强  |  B级=中性待观察  |  C级=趋势破坏回避  |  {n_total}只ETF  |  {today}  |  QClaw量化系统  |  仅供参考',
    ps('foot', fontSize=7, textColor=C_GREY, alignment=TA_CENTER)
))

doc.build(story)
print(f'PDF: {out}  size={os.path.getsize(out):,} bytes')
