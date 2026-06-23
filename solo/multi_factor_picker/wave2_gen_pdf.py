# -*- coding: utf-8 -*-
"""生成二波行情研究PDF报告"""
import json, os, sys, io, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

OUT_DIR = r'D:\mystock\solo\multi_factor_picker\output'

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                  TableStyle, HRFlowable, PageBreak, KeepTogether)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# 注册中文字体
pdfmetrics.registerFont(TTFont('SimHei', 'C:/Windows/Fonts/simhei.ttf'))
FONT = 'SimHei'
FONT_B = 'SimHei'
FONT_S = 'SimHei'

# 颜色
DARK_BLUE = colors.HexColor('#1a3c6e')
GOLD = colors.HexColor('#f39c12')
GREEN = colors.HexColor('#27ae60')
RED = colors.HexColor('#e74c3c')
LIGHT_GRAY = colors.HexColor('#f0f4f8')
MEDIUM_BLUE = colors.HexColor('#2980b9')
DARK_BG = colors.HexColor('#2c3e50')

# 样式
TITLE_S = ParagraphStyle('Title', fontName=FONT_B, fontSize=22, leading=28,
                           textColor=DARK_BLUE, spaceAfter=12, alignment=1)
SUBTITLE_S = ParagraphStyle('Sub', fontName=FONT, fontSize=11, leading=16,
                              textColor=colors.HexColor('#7f8c8d'), spaceAfter=6, alignment=1)
SECTION_S = ParagraphStyle('Sect', fontName=FONT_B, fontSize=14, leading=20,
                            textColor=DARK_BLUE, spaceAfter=8, spaceBefore=12)
BODY_S = ParagraphStyle('Body', fontName=FONT, fontSize=10, leading=16,
                          textColor=colors.HexColor('#2c3e50'), spaceAfter=6)
BODY_B = ParagraphStyle('BodyB', fontName=FONT_B, fontSize=10, leading=16,
                         textColor=colors.HexColor('#2c3e50'), spaceAfter=6)
SMALL_S = ParagraphStyle('Small', fontName=FONT_S, fontSize=9, leading=14,
                          textColor=colors.HexColor('#34495e'))
HIGHLIGHT_S = ParagraphStyle('HL', fontName=FONT_B, fontSize=11, leading=17,
                              textColor=RED, spaceAfter=4)

# 加载数据
with open(os.path.join(OUT_DIR, 'wave2_result.json'), 'r', encoding='utf-8') as f:
    data = json.load(f)

pattern_stats = data['pattern_stats']
best_combos = data['best_combos']
total_cases = data['total_cases']

# 排序形态
sorted_patterns = sorted(pattern_stats.items(), key=lambda x: x[1].get('\u4e8c\u6ce2\u6210\u529f\u7387%', 0), reverse=True)

# PDF
pdf_path = os.path.join(OUT_DIR, '\u4e8c\u6ce2\u884c\u60c5\u8c03\u6574\u5f62\u6001\u7814\u7a76\u62a5\u544a.pdf')
doc = SimpleDocTemplate(pdf_path, pagesize=A4,
                         leftMargin=18*mm, rightMargin=18*mm,
                         topMargin=20*mm, bottomMargin=20*mm)
story = []
mm_f = mm
page_w = 210 * mm_f - 36 * mm_f  # 174mm

# ── 封面 ──
story.append(Spacer(1, 30*mm_f))
story.append(Paragraph('\u4e8c\u6ce2\u884c\u60c5\u8c03\u6574\u5f62\u6001\u91cf\u5316\u7814\u7a76', TITLE_S))
story.append(Spacer(1, 5*mm_f))
story.append(Paragraph('\u2014\u2014 \u4e00\u6ce220%+\u62c9\u5347\u540e\uff0c\u4ec0\u4e48\u6837\u7684\u8c03\u6574\u6700\u6709\u4e8c\u6ce2\u6982\u7387\uff1f', SUBTITLE_S))
story.append(Spacer(1, 15*mm_f))
story.append(HRFlowable(width='60%', thickness=2, color=GOLD, spaceAfter=10))

meta_data = [
    ['\u56de\u6d4b\u533a\u95f4', '2024-01-01 ~ 2026-06-20'],
    ['\u6837\u672c\u8303\u56f4', '\u6caa\u6df1300\u6210\u5206\u80a1\uff08312\u53ea\uff09'],
    ['\u603b\u6848\u4f8b\u6570', f'{total_cases:,} \u4e2a\u62c9\u5347\u6848\u4f8b'],
    ['\u6570\u636e\u6e90', 'Tushare\uff08stk_factor/daily_basic/moneyflow\uff09'],
    ['\u751f\u6210\u65f6\u95f4', data['date']],
]
for row in meta_data:
    story.append(Paragraph(f'<font color="#7f8c8d">{row[0]}</font>  {row[1]}', BODY_S))
story.append(Spacer(1, 20*mm_f))

story.append(HRFlowable(width='100%', thickness=0.5, color=colors.HexColor('#dee2e6')))
story.append(Paragraph('<font color="#95a5a6">\u514d\u8d23\u58f0\u660e\uff1a\u672c\u62a5\u544a\u4ec5\u4e3a\u5386\u53f2\u6570\u636e\u56de\u6d4b\u7ed3\u679c\uff0c\u4e0d\u6784\u6210\u6295\u8d44\u5efa\u8bae\u3002\u8fc7\u5f80\u8868\u73b0\u4e0d\u4ee3\u8868\u672a\u6765\u6536\u76ca\uff0c\u6295\u8d44\u6709\u98ce\u9669\uff0c\u51b3\u7b56\u9700\u8c28\u614e\u3002</font>', SMALL_S))

story.append(PageBreak())

# ── 核心结论 ──
story.append(Paragraph('\u2605 \u6838\u5fc3\u7ed3\u8bba', SECTION_S))
story.append(HRFlowable(width='100%', thickness=1, color=DARK_BLUE, spaceAfter=8))

# TOP3形态
top3 = sorted_patterns[:3]
conclusions = [
    f'\u2714 {top3[0][0]}\uff1a\u4e8c\u6ce2\u6210\u529f\u7387 {top3[0][1]["\u4e8c\u6ce2\u6210\u529f\u7387%"]}%\uff0c\u5e73\u5747\u6da8\u5e45 {top3[0][1]["\u4e8c\u6ce2\u5e73\u5747\u6da8\u5e45%"]}%\uff0c\u76c8\u4e8f\u6bd4 {top3[0][1]["\u5e73\u5747\u76c8\u4e8f\u6bd4"]}\u3002\u8fd9\u662f\u6700\u4f18\u7684\u8c03\u6574\u5f62\u6001\uff01',
    f'\u2714 {top3[1][0]}\uff1a\u4e8c\u6ce2\u6210\u529f\u7387 {top3[1][1]["\u4e8c\u6ce2\u6210\u529f\u7387%"]}%\uff0c\u5e73\u5747\u6da8\u5e45 {top3[1][1]["\u4e8c\u6ce2\u5e73\u5747\u6da8\u5e45%"]}%\u3002',
    f'\u2714 {top3[2][0]}\uff1a\u4e8c\u6ce2\u6210\u529f\u7387 {top3[2][1]["\u4e8c\u6ce2\u6210\u529f\u7387%"]}%\uff0c\u5e73\u5747\u6da8\u5e45 {top3[2][1]["\u4e8c\u6ce2\u5e73\u5747\u6da8\u5e45%"]}%\u3002',
    '',
    '\u2714 \u6700\u4f18\u5165\u573a\u6761\u4ef6\uff08\u6240\u6709\u5f62\u6001\u901a\u7528\uff09\uff1aRSI<40 + \u8c03\u6574\u4f4e\u70b9\u5728MA20\u4e0a\u65b9 + MACD\u63a5\u8fd1\u91d1\u53c9\u3002',
    '',
    '\u2714 \u5173\u952e\u53d1\u73b0\uff1a\u201cRSI\u4f4e\u4f4d + MA20\u4e0a\u65b9\u201d\u662f\u6240\u6709\u5f62\u6001\u4e2d\u6700\u5f3a\u7684\u4e8c\u6ce2\u4fe1\u53f7\uff0c\u591a\u79cd\u5f62\u6001\u4e0b\u8fd9\u4e2a\u7ec4\u5408\u7684\u6210\u529f\u7387\u8fbe100%\u3002',
    '',
    '\u26a0 \u98ce\u9669\u63d0\u793a\uff1a\u201c\u4e09\u89d2\u6536\u655b\u201d\u548c\u201c\u7f29\u91cf\u56de\u8c03\u201d\u6210\u529f\u7387\u8f83\u4f4e\uff0875-78%\uff09\uff0c\u9700\u8c28\u614e\u5bf9\u5f85\u3002',
]
for c in conclusions:
    story.append(Paragraph(c, BODY_S))
story.append(Spacer(1, 5*mm_f))

# ── 形态统计总表 ──
story.append(Paragraph('\u2605 \u5f62\u6001\u7edf\u8ba1\u603b\u8868\uff08\u6309\u4e8c\u6ce2\u6210\u529f\u7387\u6392\u5e8f\uff09', SECTION_S))
story.append(HRFlowable(width='100%', thickness=1, color=DARK_BLUE, spaceAfter=8))

header = ['\u5f62\u6001', '\u6837\u672c', '\u6210\u529f\u7387%', '\u4e8c\u6ce2\u5747\u6da8%', '20\u65e5\u6da8%', '60\u65e5\u6da8%', '\u56de\u8c03%', '\u76c8\u4e8f\u6bd4', 'MA20\u4e0a%']
col_w = [22, 14, 18, 20, 18, 18, 14, 16, 18]  # total 158mm
tdata = [header]
for pname, pstat in sorted_patterns:
    tdata.append([
        pname,
        str(int(pstat.get('\u6837\u672c\u6570', 0))),
        '%.1f%%' % pstat.get('\u4e8c\u6ce2\u6210\u529f\u7387%', 0),
        '%.2f%%' % pstat.get('\u4e8c\u6ce2\u5e73\u5747\u6da8\u5e45%', 0),
        '%.2f%%' % pstat.get('20\u65e5\u5e73\u5747\u6da8\u5e45%', 0),
        '%.2f%%' % pstat.get('60\u65e5\u5e73\u5747\u6da8\u5e45%', 0),
        '%.2f%%' % pstat.get('\u5e73\u5747\u56de\u8c03\u5e45\u5ea6%', 0),
        '%.2f' % pstat.get('\u5e73\u5747\u76c8\u4e8f\u6bd4', 0),
        '%.1f%%' % pstat.get('MA20\u4e0a\u65b9\u6bd4\u4f8b%', 0),
    ])

t = Table(tdata, colWidths=[x*mm_f for x in col_w], repeatRows=1)
ts_list = [
    ('BACKGROUND', (0,0), (-1,0), DARK_BLUE),
    ('TEXTCOLOR', (0,0), (-1,0), colors.white),
    ('FONTNAME', (0,0), (-1,0), FONT_B),
    ('FONTSIZE', (0,0), (-1,0), 8.5),
    ('FONTSIZE', (0,1), (-1,-1), 8.5),
    ('FONTNAME', (0,1), (-1,-1), FONT),
    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ('TOPPADDING', (0,0), (-1,-1), 4),
    ('GRID', (0,0), (-1,-1), 0.3, colors.HexColor('#bdc3c7')),
]
# 成功率着色
for i, (pname, pstat) in enumerate(sorted_patterns):
    rate = pstat.get('\u4e8c\u6ce2\u6210\u529f\u7387%', 0)
    if rate >= 90:
        ts_list.append(('TEXTCOLOR', (2,i+1), (2,i+1), RED))
    elif rate < 80:
        ts_list.append(('TEXTCOLOR', (2,i+1), (2,i+1), colors.HexColor('#e67e22')))
# TOP1高亮
ts_list.append(('BACKGROUND', (0,1), (-1,1), colors.HexColor('#fef9e7')))

t.setStyle(TableStyle(ts_list))
story.append(t)
story.append(Spacer(1, 8*mm_f))

# ── 各形态详解 ──
story.append(PageBreak())
story.append(Paragraph('\u2605 \u5404\u5f62\u6001\u8be6\u89e3\u4e0e\u6700\u4f18\u5165\u573a\u6761\u4ef6', SECTION_S))
story.append(HRFlowable(width='100%', thickness=1, color=DARK_BLUE, spaceAfter=8))

pattern_details = {
    '\u5f3a\u52bf\u6a2a\u76d8': '\u56de\u8c03<10%\uff0c\u91cf\u80fd\u7ef4\u6301\uff0c\u4f4e\u70b9\u59cb\u7ec8\u5728MA20\u4e0a\u65b9\u3002\u8fd9\u662f\u6700\u7406\u60f3\u7684\u8c03\u6574\u5f62\u6001\u2014\u2014\u591a\u5934\u63a7\u76d8\u4e0d\u677e\u624b\uff0c\u8bf4\u660e\u4e3b\u529b\u610f\u5fd7\u575a\u5b9a\u3002\u5165\u573a\u65f6\u673a\u662f\u56de\u8c03\u5230MA5\u9644\u8fd1 + RSI\u56de\u5f5240-50\u533a\u95f4\u3002',
    'V\u578b\u6025\u8dcc': '\u77ed\u671f\uff08<10\u5929\uff09\u6025\u8dcc>10%\uff0c\u6070\u6070\u7ed9\u4e86\u4e00\u6b21\u6050\u614c\u76d8\u6d17\u7684\u673a\u4f1a\u3002\u5173\u952e\u662fRSI\u8d85\u5356+\u5728MA60\u4e0a\u65b9\uff0c\u8bf4\u660e\u5927\u8d8b\u52bf\u672a\u7834\u3002',
    '\u653e\u91cf\u56de\u8c03': '\u56de\u8c0310-20%\u4f46\u91cf\u80fd\u653e\u5927\uff0c\u53ef\u80fd\u662f\u4e3b\u529b\u5efa\u4ed3\u3002\u5173\u952e\u4fe1\u53f7\uff1aMACD\u63a5\u8fd1\u91d1\u53c9 + \u4f4e\u70b9\u4ecd\u5728MA20\u4e0a\u65b9\uff0c\u8bf4\u660e\u8c03\u6574\u63a5\u8fd1\u5c3e\u58f0\u3002',
    '\u6df1\u5ea6\u56de\u8c03': '\u56de\u8c03>20%\uff0c\u63a5\u8fd1MA60\u3002\u8fd9\u79cd\u5f62\u6001\u6210\u529f\u7387\u4e5f\u4e0d\u4f4e\uff0886%\uff09\uff0c\u4f46\u9700\u8981\u8010\u5fc3\u7b49\u5f85\u3002\u6700\u4f18\u6761\u4ef6\uff1aRSI<50 + \u4f4e\u70b9\u5728MA20/MA60\u4e0a\u65b9\u3002',
    '\u7f29\u91cf\u56de\u8c03': '\u7f29\u91cf\u56de\u8c03\u662f\u5178\u578b\u7684\u201c\u6d17\u76d8\u201d\u5f62\u6001\uff0c\u6210\u529f\u738777.6%\u3002\u8981\u63d0\u9ad8\u6210\u529f\u7387\u9700\u7b49RSI\u56de\u5f8940\u4ee5\u4e0a + \u4f4e\u70b9\u5728MA60\u4e0a\u65b9\uff0893.5%\uff09\u3002',
    '\u4e09\u89d2\u6536\u655b': '\u632f\u5e45\u9012\u51cf\uff0c\u8bf4\u660e\u591a\u7a7a\u5206\u6b67\u6d88\u5316\uff0c\u5373\u5c06\u9009\u62e9\u65b9\u5411\u3002\u6210\u529f\u738775.8%\uff0c\u5173\u952e\u662f\u7b49\u7a81\u7834\u65b9\u5411\u786e\u8ba4\u540e\u518d\u5165\u573a\u3002',
}

for pname, pstat in sorted_patterns:
    base_rate = pstat.get('\u4e8c\u6ce2\u6210\u529f\u7387%', 0)
    n = pstat.get('\u6837\u672c\u6570', 0)
    desc = pattern_details.get(pname, '')

    # 形态标题
    rate_color = '#27ae60' if base_rate >= 90 else ('#f39c12' if base_rate >= 80 else '#e74c3c')
    story.append(KeepTogether([
        Paragraph(f'<font color="{rate_color}"><b>\u25cf {pname}</b></font>'
                  f'  \u6210\u529f\u7387 <font color="{rate_color}"><b>{base_rate}%</b></font>'
                  f'  \u6837\u672c{n}\u4e2a'
                  f'  \u5e73\u5747\u6da8\u5e45{pstat.get("\u4e8c\u6ce2\u5e73\u5747\u6da8\u5e45%", 0)}%'
                  f'  \u76c8\u4e8f\u6bd4{pstat.get("\u5e73\u5747\u76c8\u4e8f\u6bd4", 0)}', BODY_B),
        Paragraph(f'<font color="#7f8c8d">\u2192 {desc}</font>', SMALL_S),
        Spacer(1, 3*mm_f),
    ]))

# ── 最优组合TOP15 ──
story.append(PageBreak())
story.append(Paragraph('\u2605 \u6700\u4f18\u5165\u573a\u6761\u4ef6\u7ec4\u5408 TOP15', SECTION_S))
story.append(HRFlowable(width='100%', thickness=1, color=DARK_BLUE, spaceAfter=8))
story.append(Paragraph('\u4ee5\u4e0b\u7ec4\u5408\u5747\u57fa\u4e8e\u5386\u53f2\u6570\u636e\u56de\u6d4b\uff0c\u6837\u672c\u91cf>=5\u3002\u7eff\u8272=\u6210\u529f\u7387>=98%\uff0c\u6a59\u8272=90-97%\u3002', SMALL_S))
story.append(Spacer(1, 3*mm_f))

bc_header = ['\u5f62\u6001', '\u7ec4\u5408\u6761\u4ef6', '\u6837\u672c', '\u6210\u529f\u7387%', '\u5747\u6da8%', '\u76c8\u4e8f\u6bd4']
bc_col_w = [18, 56, 14, 18, 18, 16]  # 140mm
bc_data = [bc_header]
for bc in best_combos[:15]:
    bc_data.append([
        bc['pattern'],
        bc['combo'],
        str(int(bc['n'])),
        '%.1f%%' % bc['rate'],
        '%.1f%%' % bc['gain'],
        '%.2f' % bc['rr'],
    ])

bt = Table(bc_data, colWidths=[x*mm_f for x in bc_col_w], repeatRows=1)
bts = [
    ('BACKGROUND', (0,0), (-1,0), DARK_BG),
    ('TEXTCOLOR', (0,0), (-1,0), colors.white),
    ('FONTNAME', (0,0), (-1,0), FONT_B),
    ('FONTSIZE', (0,0), (-1,0), 8),
    ('FONTSIZE', (0,1), (-1,-1), 8),
    ('FONTNAME', (0,1), (-1,-1), FONT),
    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
    ('ALIGN', (1,1), (1,-1), 'LEFT'),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ('TOPPADDING', (0,0), (-1,-1), 3),
    ('GRID', (0,0), (-1,-1), 0.3, colors.HexColor('#bdc3c7')),
]
for i, bc in enumerate(best_combos[:15]):
    if bc['rate'] >= 98:
        bts.append(('TEXTCOLOR', (3,i+1), (3,i+1), GREEN))
        bts.append(('BACKGROUND', (0,i+1), (-1,i+1), colors.HexColor('#eafaf1')))
    elif bc['rate'] >= 90:
        bts.append(('TEXTCOLOR', (3,i+1), (3,i+1), GOLD))
        bts.append(('BACKGROUND', (0,i+1), (-1,i+1), colors.HexColor('#fef9e7') if i%2==0 else colors.white))
    else:
        bts.append(('BACKGROUND', (0,i+1), (-1,i+1), colors.white))

bt.setStyle(TableStyle(bts))
story.append(bt)
story.append(Spacer(1, 8*mm_f))

# ── 实战策略建议 ──
story.append(Paragraph('\u2605 \u5b9e\u6218\u7b56\u7565\u5efa\u8bae', SECTION_S))
story.append(HRFlowable(width='100%', thickness=1, color=DARK_BLUE, spaceAfter=8))

strategy_items = [
    ('<b>\u7b56\u7565\u4e00\uff1a\u5f3a\u52bf\u6a2a\u76d8\u56de\u8c03\u4e70\u5165\u6cd5</b>',
     '\u6761\u4ef6\uff1a20%+\u62c9\u5347\u540e\u56de\u8c03<10% + \u4f4e\u70b9\u5728MA20\u4e0a\u65b9 + RSI\u56de\u523040-50\u3002'
     '\u6210\u529f\u738798.6%\uff0c\u5e73\u5747\u6da8\u5e4556.6%\uff0c\u76c8\u4e8f\u6bd420\u3002'
     '\u6b62\u635f\u8bbe\u5728MA20\u7834\u4f4d\u5904\uff0c\u76ee\u6807\u4e3a\u524d\u9ad8\u70b9\u62161.1\u500d\u3002'),
    ('<b>\u7b56\u7565\u4e8c\uff1aV\u578b\u6025\u8dcc\u62e9\u65f6\u6cd5</b>',
     '\u6761\u4ef6\uff1a\u77ed\u671f\u6025\u8dcc>10% + \u4f4e\u70b9\u5728MA60\u4e0a\u65b9 + RSI<35\u3002'
     '\u6210\u529f\u7387100%\uff0c\u5e73\u5747\u6da8\u5e4549%\u3002'
     '\u5173\u952e\u662f\u5927\u8d8b\u52bf\u672a\u7834\uff0c\u6025\u8dcc\u662f\u6050\u614c\u76d8\u6d17\u3002'
     '\u6b62\u635f\u8bbe\u5728\u6025\u8dcc\u4f4e\u70b9\u4e0b\u65b93%\u3002'),
    ('<b>\u7b56\u7565\u4e09\uff1a\u653e\u91cf\u56de\u8c03\u5c3e\u58f0\u6cd5</b>',
     '\u6761\u4ef6\uff1a\u56de\u8c0310-25% + MACD\u63a5\u8fd1\u91d1\u53c9 + \u4f4e\u70b9\u5728MA20\u4e0a\u65b9\u3002'
     '\u6210\u529f\u7387100%\uff0c\u6837\u672c\u91cf843\u4e2a\u3002'
     '\u653e\u91cf\u53ef\u80fd\u662f\u4e3b\u529b\u4e8c\u6b21\u5efa\u4ed3\uff0cMACD\u91d1\u53c9\u662f\u786e\u8ba4\u4fe1\u53f7\u3002'),
    ('<b>\u7b56\u7565\u56db\uff1a\u6df1\u5ea6\u56de\u8c03\u5e95\u90e8\u5f62\u6210\u6cd5</b>',
     '\u6761\u4ef6\uff1a\u56de\u8c03>20% + \u4f4e\u70b9\u63a5\u8fd1MA60 + RSI<50\u3002'
     '\u6210\u529f\u7387100%\uff08MA60\u4e0a\u65b9\u5b50\u96c6\uff09\uff0c\u5e73\u5747\u6da8\u5e4542%\u3002'
     '\u8010\u5fc3\u7b49\u5f85MA60\u652f\u6491\uff0c\u914d\u5408\u91d1\u53c9\u4fe1\u53f7\u786e\u8ba4\u3002'),
]

for title, desc in strategy_items:
    story.append(Paragraph(title, BODY_B))
    story.append(Paragraph(desc, BODY_S))
    story.append(Spacer(1, 2*mm_f))

# ── 通用入场信号 ──
story.append(Spacer(1, 5*mm_f))
story.append(Paragraph('<b>\u901a\u7528\u4e8c\u6ce2\u4fe1\u53f7\u6e05\u5355\uff08\u6240\u6709\u5f62\u6001\u90fd\u9002\u7528\uff09</b>', BODY_B))
signals = [
    '\u2714 RSI\u4ece\u8d85\u5356\u533a\u57df\u56de\u5347\u523040-50\u4ee5\u4e0a\uff08\u5f3a\u5f31\u6307\u6807\u53cd\u8f6c\uff09',
    '\u2714 \u4ef7\u683c\u5728MA20\u4e0a\u65b9\u53cd\u5f39\uff08\u8d8b\u52bf\u672a\u6539\u53d8\uff09',
    '\u2714 MACD\u63a5\u8fd1\u91d1\u53c9\u6216\u5df2\u91d1\u53c9\uff08\u52a8\u80fd\u8f6c\u5f3a\uff09',
    '\u2714 \u8c03\u6574\u671f\u91cf\u80fd\u8425\u7f29\u540e\u653e\u91cf\uff08\u5728\u4f4e\u70b9\u653e\u91cf\u53ef\u80fd\u662f\u4e3b\u529b\u5efa\u4ed3\uff09',
    '\u2714 \u5e03\u6797\u4e0b\u8f68\u4f38\u5f97\u652f\u6491\uff08\u6280\u672f\u56de\u8c03\u6781\u9650\uff09',
    '\u2618 RSI\u59cb\u7ec8\u5728\u8d85\u5356\u533a\u57df\u4e0d\u53cd\u5f39\u2192\u8b66\u60d5\u8d8b\u52bf\u53ef\u80fd\u5df2\u7ec8\u7ed3',
    '\u2618 \u8dcc\u7834MA60\u2192\u4e0d\u518d\u7b26\u5408\u4e8c\u6ce2\u6761\u4ef6\uff0c\u5e94\u6b62\u635f\u79bb\u573a',
]
for s in signals:
    story.append(Paragraph(s, SMALL_S))

# ── 免责声明 ──
story.append(PageBreak())
story.append(Paragraph('\u514d\u8d23\u58f0\u660e', SECTION_S))
story.append(HRFlowable(width='100%', thickness=1, color=colors.HexColor('#95a5a6'), spaceAfter=8))
disclaimer = [
    '\u672c\u62a5\u544a\u57fa\u4e8e2024\u5e741\u6708\u81f32026\u5e746\u6708\u7684\u5386\u53f2\u6570\u636e\u56de\u6d4b\uff0c\u4ec5\u4e3a\u7814\u7a76\u76ee\u7684\uff0c\u4e0d\u6784\u6210\u4efb\u4f55\u6295\u8d44\u5efa\u8bae\u3002',
    '\u8fc7\u5f80\u8868\u73b0\u4e0d\u4ee3\u8868\u672a\u6765\u6536\u76ca\u3002\u5e02\u573a\u6709\u98ce\u9669\uff0c\u6295\u8d44\u9700\u8c28\u614e\u3002',
    '\u56de\u6d4b\u6837\u672c\u4ec5\u9650\u6caa\u6df1300\u6210\u5206\u80a1\uff0c\u4e0d\u4ee3\u8868\u5168\u5e02\u573a\u60c5\u51b5\u3002',
    '\u4efb\u4f55\u57fa\u4e8e\u672c\u62a5\u544a\u7684\u6295\u8d44\u51b3\u7b56\uff0c\u98ce\u9669\u81ea\u62c5\u3002',
]
for d in disclaimer:
    story.append(Paragraph(d, BODY_S))

# 生成
doc.build(story)
print(f'PDF\u5df2\u751f\u6210: {pdf_path}')
print(f'\u6587\u4ef6\u5927\u5c0f: {os.path.getsize(pdf_path)/1024:.0f} KB')
