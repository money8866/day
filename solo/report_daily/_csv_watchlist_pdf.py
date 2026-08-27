# -*- coding: utf-8 -*-
"""h1_2026_follow_watchlist CSV → PDF"""
import os, sys, platform, csv, io
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                Table, TableStyle, PageBreak)
from reportlab.lib import colors

def setup_cn():
    if platform.system() == 'Windows':
        candidates = []
        dirs = [os.path.join(os.environ.get('WINDIR','C:\\Windows'),'Fonts')]
        local = os.environ.get('LOCALAPPDATA','')
        if local:
            dirs.append(os.path.join(local,'Microsoft','Windows','Fonts'))
        for d in dirs:
            for fname, name, idx in [
                ('msyh.ttc','MicrosoftYaHei',0),
                ('simhei.ttf','SimHei',0),
            ]:
                p = os.path.join(d, fname)
                if os.path.exists(p):
                    try:
                        pdfmetrics.registerFont(TTFont(name, p, subfontIndex=idx))
                        candidates.append(name)
                    except: pass
        cn = candidates[0] if candidates else 'Helvetica'
    else:
        cn = 'Helvetica'
    styles = getSampleStyleSheet()
    for s in styles.byName.values():
        if hasattr(s, 'fontName'):
            s.fontName = cn
    return cn, styles

cn, styles = setup_cn()
F = lambda txt, fs=8: Paragraph(str(txt), styles['Normal'])

# ── 读取CSV ──
csv_path = r'D:\mystock\solo\report_daily\h1_2026_follow_watchlist_20260826.csv'
rows = []
with open(csv_path, encoding='utf-8-sig') as f:
    for r in csv.DictReader(f):
        rows.append(r)

# ── 配色 ──
HDR_BG   = colors.HexColor('#1a3a8a')
HDR_TXT  = colors.whitesmoke
ROW_ALT  = colors.HexColor('#f0f4ff')
ROW_NORM = colors.white
RED_TXT  = colors.HexColor('#c0392b')
GRN_TXT  = colors.HexColor('#27ae60')
TTL_BG   = colors.HexColor('#2c5f9e')

# ── 标题 ──
title_style = ParagraphStyle('T', parent=styles['Title'],
                              fontSize=14, textColor=colors.HexColor('#1a3a8a'),
                              alignment=TA_CENTER, spaceAfter=4)
sub_style = ParagraphStyle('S', parent=styles['Normal'],
                            fontSize=9, textColor=colors.grey, alignment=TA_CENTER)

# ── 列定义 ──
COLS = [
    ('名称',    60),
    ('代码',    72),
    ('评分',   34),
    ('基金',   34),
    ('建仓',   34),
    ('操作',   78),
    ('触发',   70),
    ('状态',   72),
    ('发布后', 42),
    ('发布前', 42),
    ('收盘价', 48),
    ('MA20',   46),
    ('MA60',   46),
    ('扣非YoY',46),
    ('净利YoY',46),
    ('营收YoY',46),
]

HDR_ROW = [F(c, 8) for c, _ in COLS]
COL_W   = [w for _, w in COLS]

def row_bg(i):
    return ROW_ALT if i % 2 == 0 else ROW_NORM

def fmt_cell(val, col_name):
    if not val or val == '':
        return F('—', 8)
    try:
        if col_name in ('名称', '代码', '操作', '触发', '状态'):
            return F(str(val), 8)
        v = float(val)
        if col_name in ('发布后', '发布前'):
            color = GRN_TXT if v >= 0 else RED_TXT
            s = ParagraphStyle('x', parent=styles['Normal'], fontSize=8, textColor=color)
            return Paragraph(f'{v:+.1f}%', s)
        if col_name in ('评分', '基金', '建仓', '触发评分'):
            s = ParagraphStyle('x', parent=styles['Normal'], fontSize=8,
                                textColor=colors.HexColor('#1a3a8a'))
            return Paragraph(f'{v:.1f}', s)
        return F(f'{v:.2f}', 8)
    except:
        return F(str(val), 8)

def make_table(page_rows, page_num, total):
    data = [HDR_ROW]
    for r in page_rows:
        data.append([
            fmt_cell(r.get('name',''), '名称'),
            fmt_cell(r.get('ts_code',''), '代码'),
            fmt_cell(r.get('score',''), '评分'),
            fmt_cell(r.get('fund_score',''), '基金'),
            fmt_cell(r.get('setup_score',''), '建仓'),
            fmt_cell(r.get('action',''), '操作'),
            fmt_cell(r.get('trigger',''), '触发'),
            fmt_cell(r.get('price_state',''), '状态'),
            fmt_cell(r.get('post_ret',''), '发布后'),
            fmt_cell(r.get('pre_ret',''), '发布前'),
            fmt_cell(r.get('close',''), '收盘价'),
            fmt_cell(r.get('ma20',''), 'MA20'),
            fmt_cell(r.get('ma60',''), 'MA60'),
            fmt_cell(r.get('dt_yoy',''), '扣非YoY'),
            fmt_cell(r.get('ni_yoy',''), '净利YoY'),
            fmt_cell(r.get('rev_yoy',''), '营收YoY'),
        ])
    t = Table(data, colWidths=COL_W, repeatRows=1)
    row_colors = []
    for i in range(1, len(data)):
        bg = row_bg(i-1)
        row_colors.append(('BACKGROUND', (0,i), (-1,i), bg))
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), HDR_BG),
        ('TEXTCOLOR',  (0,0), (-1,0), HDR_TXT),
        ('FONTNAME',   (0,0), (-1,-1), cn),
        ('FONTSIZE',   (0,0), (-1,-1), 8),
        ('ALIGN',      (0,0), (-1,-1), 'CENTER'),
        ('VALIGN',     (0,0), (-1,-1), 'MIDDLE'),
        ('GRID',       (0,0), (-1,-1), 0.4, colors.HexColor('#cccccc')),
        ('TOPPADDING', (0,0), (-1,-1), 2),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
        ('LEFTPADDING', (0,0), (-1,-1), 2),
        ('RIGHTPADDING',(0,0), (-1,-1), 2),
    ] + row_colors))
    return t

# ── 分页（每页~35行） ──
PAGE_SIZE = 35
pages = [rows[i:i+PAGE_SIZE] for i in range(0, len(rows), PAGE_SIZE)]

out_path = os.path.join(os.path.dirname(csv_path),
                         'h1_2026_follow_watchlist_20260826.pdf')
doc = SimpleDocTemplate(out_path, pagesize=landscape(A4),
                         leftMargin=20, rightMargin=20,
                         topMargin=20, bottomMargin=20)

story = [
    Paragraph('H1-2026 关注股票池 · 重点跟踪', title_style),
    Paragraph(f'生成日期：2026-08-26  |  共 {len(rows)} 只  |  第 {len(pages)} 页', sub_style),
    Spacer(1, 6),
]
for i, pg in enumerate(pages):
    story.append(make_table(pg, i+1, len(pages)))
    if i < len(pages) - 1:
        story.append(PageBreak())

def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont(cn, 8)
    canvas.setFillColor(colors.grey)
    canvas.drawString(20, 12, f'H1-2026 Follow Watchlist 20260826  |  QClaw  |  仅供参考，不构成投资建议')
    canvas.drawRightString(A4[0]-20, 12, f'第 {doc.page} 页')
    canvas.restoreState()

doc.build(story, onFirstPage=footer, onLaterPages=footer)
print(f'PDF: {out_path}  ({os.path.getsize(out_path)//1024}KB)')
