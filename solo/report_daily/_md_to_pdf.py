# -*- coding: utf-8 -*-
"""Generic Markdown -> PDF converter (CJK safe, tables + headings + bullets)."""
import os, re, platform
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                Table, TableStyle, HRFlowable)
from reportlab.lib import colors

# ── CJK font ──
def reg_font():
    cn = None; cnb = None
    if platform.system() == 'Windows':
        dirs = [os.path.join(os.environ.get('WINDIR','C:\\Windows'),'Fonts')]
        local = os.environ.get('LOCALAPPDATA','')
        if local: dirs.append(os.path.join(local,'Microsoft','Windows','Fonts'))
        for d in dirs:
            p = os.path.join(d,'msyh.ttc')
            if os.path.exists(p):
                try: pdfmetrics.registerFont(TTFont('CN', p, subfontIndex=0)); cn='CN'
                except: pass
            p2 = os.path.join(d,'msyhbd.ttc')
            if os.path.exists(p2):
                try: pdfmetrics.registerFont(TTFont('CNB', p2, subfontIndex=0)); cnb='CNB'
                except: pass
    if cn is None:
        cn = 'Helvetica'; cnb = 'Helvetica-Bold'
    return cn, (cnb or cn)

CN, CNB = reg_font()

def cjk_len(s):
    n = 0
    for ch in s:
        n += 2 if ord(ch) > 0x2E80 else 1
    return n

def clean(t):
    t = t.strip()
    t = re.sub(r'\*\*(.+?)\*\*', r'\1', t)   # strip ** markers
    t = t.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
    return t

# ── styles ──
ss = getSampleStyleSheet()
BASE = 8.5
title_s = ParagraphStyle('T', fontName=CNB, fontSize=15, leading=19,
                         textColor=colors.HexColor('#1a3a8a'), spaceAfter=6, alignment=TA_LEFT)
sub_s   = ParagraphStyle('SUB', fontName=CN, fontSize=9, leading=13,
                         textColor=colors.HexColor('#444444'), spaceAfter=4)
h1_s    = ParagraphStyle('H1', fontName=CNB, fontSize=12.5, leading=16,
                         textColor=colors.HexColor('#1a3a8a'), spaceBefore=10, spaceAfter=5)
h2_s    = ParagraphStyle('H2', fontName=CNB, fontSize=11, leading=14,
                         textColor=colors.HexColor('#2c5f9e'), spaceBefore=8, spaceAfter=4)
h3_s    = ParagraphStyle('H3', fontName=CNB, fontSize=9.5, leading=13,
                         textColor=colors.HexColor('#c0392b'), spaceBefore=6, spaceAfter=3)
body_s  = ParagraphStyle('B', fontName=CN, fontSize=BASE, leading=BASE+3.5, spaceAfter=3)
bullet_s= ParagraphStyle('BL', parent=body_s, leftIndent=12, bulletIndent=2, spaceAfter=2)
cell_s  = ParagraphStyle('C', fontName=CN, fontSize=7, leading=9)
cellh_s = ParagraphStyle('CH', fontName=CNB, fontSize=7, leading=9,
                         textColor=colors.white)

def parse_row(line):
    parts = line.strip().strip('|').split('|')
    return [p.strip() for p in parts]

def build_table(header, rows, usable):
    data = [[Paragraph(clean(h), cellh_s) for h in header]]
    for r in rows:
        data.append([Paragraph(clean(c), cell_s) for c in r])
    ncol = len(header)
    colchars = [0]*ncol
    for r in data:
        for j,c in enumerate(r):
            txt = c.text if hasattr(c,'text') else str(c)
            colchars[j] = max(colchars[j], cjk_len(txt))
    total = sum(colchars) or 1
    widths = [max(34, usable*cc/total) for cc in colchars]
    t = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ('BACKGROUND',(0,0),(-1,0), colors.HexColor('#1a3a8a')),
        ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
        ('GRID',(0,0),(-1,-1),0.4, colors.HexColor('#cccccc')),
        ('TOPPADDING',(0,0),(-1,-1),2),
        ('BOTTOMPADDING',(0,0),(-1,-1),2),
        ('LEFTPADDING',(0,0),(-1,-1),3),
        ('RIGHTPADDING',(0,0),(-1,-1),3),
    ]
    for i in range(1,len(data)):
        if i % 2 == 0:
            style.append(('BACKGROUND',(0,i),(-1,i), colors.HexColor('#f0f4ff')))
    t.setStyle(TableStyle(style))
    return t

# ── parse ──
md_path = r'D:\mystock\solo\report_daily\hvt_bull_report_20260828.md'
out_path = r'D:\mystock\solo\report_daily\hvt_bull_report_20260828.pdf'

with open(md_path, encoding='utf-8') as f:
    lines = f.read().split('\n')

PAGE_W, PAGE_H = landscape(A4)
MARGIN = 16
usable = PAGE_W - 2*MARGIN

story = []
i = 0
n = len(lines)
while i < n:
    line = lines[i]
    s = line.strip()
    # table
    if s.startswith('|') and i+1 < n and re.match(r'^\s*\|[\s:|-]+\|\s*$', lines[i+1]):
        header = parse_row(line)
        i += 2
        rows = []
        while i < n and lines[i].strip().startswith('|'):
            rows.append(parse_row(lines[i]))
            i += 1
        story.append(build_table(header, rows, usable))
        story.append(Spacer(1,6))
        continue
    # headings
    if s.startswith('### '):
        story.append(Paragraph(clean(s[4:]), h3_s)); i += 1; continue
    if s.startswith('## '):
        story.append(Paragraph(clean(s[3:]), h2_s)); i += 1; continue
    if s.startswith('# '):
        story.append(Paragraph(clean(s[2:]), title_s)); i += 1; continue
    if re.match(r'^-{3,}$', s):
        story.append(HRFlowable(width='100%', color=colors.HexColor('#cccccc'), spaceBefore=4, spaceAfter=4)); i += 1; continue
    if s.startswith('- '):
        story.append(Paragraph('• '+clean(s[2:]), bullet_s)); i += 1; continue
    if s == '':
        story.append(Spacer(1,4)); i += 1; continue
    story.append(Paragraph(clean(s), body_s)); i += 1

def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont(CN, 7.5)
    canvas.setFillColor(colors.grey)
    canvas.drawString(MARGIN, 9, 'HVT-BULL V3.0 Daily Report 20260828  |  QClaw  |  量化模型输出，仅供参考，不构成投资建议')
    canvas.drawRightString(PAGE_W-MARGIN, 9, '第 %d 页' % doc.page)
    canvas.restoreState()

doc = SimpleDocTemplate(out_path, pagesize=landscape(A4),
                        leftMargin=MARGIN, rightMargin=MARGIN,
                        topMargin=MARGIN, bottomMargin=MARGIN+6)
doc.build(story, onFirstPage=footer, onLaterPages=footer)
print('PDF:', out_path, '(%dKB)' % (os.path.getsize(out_path)//1024))
