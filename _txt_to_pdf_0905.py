# -*- coding: utf-8 -*-
"""txt -> pdf 通用转换：reportlab 微软雅黑，等宽保留缩进"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors

FONT = 'Chinese'
pdfmetrics.registerFont(TTFont(FONT, r'C:\Windows\Fonts\msyh.ttc'))
pdfmetrics.registerFont(TTFont(FONT + 'B', r'C:\Windows\Fonts\msyhbd.ttc'))

src = r'D:\mystock\solo\sli\output\sli_v2_subsector_top5_20260901.txt'
out = r'D:\mystock\solo\sli\output\sli_v2_subsector_top5_20260901.pdf'

body = ParagraphStyle('body', fontName=FONT, fontSize=7.2, leading=9.2,
                      textColor=colors.black, spaceAfter=0)
h1 = ParagraphStyle('h1', fontName=FONT+'B', fontSize=13, leading=17,
                    textColor=colors.HexColor('#1a3a8a'), spaceAfter=4)
sec = ParagraphStyle('sec', fontName=FONT+'B', fontSize=10, leading=14,
                     textColor=colors.HexColor('#1a3a8a'), spaceBefore=6, spaceAfter=2)
star = ParagraphStyle('star', fontName=FONT+'B', fontSize=7.2, leading=9.2,
                      textColor=colors.HexColor('#c0392b'))

doc = SimpleDocTemplate(out, pagesize=A4,
                        leftMargin=12*mm, rightMargin=12*mm,
                        topMargin=12*mm, bottomMargin=12*mm,
                        title='SLI_V2 细分赛道龙头 TOP5')

story = []
with open(src, encoding='utf-8-sig') as f:
    lines = [l.rstrip('\n') for l in f]

for i, ln in enumerate(lines):
    s = ln.strip()
    if not s:
        continue
    if ln.startswith('====') or ln.startswith('----'):
        continue
    if i == 0:
        story.append(Paragraph(s, h1))
    elif s.startswith('【'):
        story.append(Paragraph(s, sec))
    elif s.startswith('SLI_V2=') or s.startswith('  #') or s.startswith('#'):
        story.append(Paragraph(s.replace(' ', '&nbsp;'), body))
    elif s.endswith('★'):
        story.append(Paragraph(ln.replace(' ', '&nbsp;'), star))
    else:
        # 转义 & < >
        t = s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        t = t.replace(' ', '&nbsp;')
        story.append(Paragraph(t, body))

doc.build(story)
print('PDF生成:', out, os.path.getsize(out), 'bytes')
