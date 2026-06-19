#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成机构调研与研报验证报告PDF"""

import json
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.lib.colors import HexColor, black, white
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Register Chinese fonts
def setup_chinese_pdf():
    font_dir = r'C:\Windows\Fonts'
    # Register SimHei (bold)
    simhei_path = os.path.join(font_dir, 'simhei.ttf')
    if os.path.exists(simhei_path):
        pdfmetrics.registerFont(TTFont('SimHei', simhei_path))
    # Register SimSun (normal)
    simsun_path = os.path.join(font_dir, 'simsun.ttc')
    if os.path.exists(simsun_path):
        pdfmetrics.registerFont(TTFont('SimSun', simsun_path, subfontIndex=0))
    # Register MS YaHei
    msyh_path = os.path.join(font_dir, 'msyh.ttc')
    if os.path.exists(msyh_path):
        pdfmetrics.registerFont(TTFont('MSYaHei', msyh_path, subfontIndex=0))

setup_chinese_pdf()

# Load JSON data
json_path = r'D:\mystock\solo\report_daily\inst_research_verify_20260619.json'
with open(json_path, 'r', encoding='utf-8') as f:
    data = json.load(f)

# PDF output
pdf_path = r'D:\mystock\solo\report_daily\inst_research_verify_20260619.pdf'
doc = SimpleDocTemplate(pdf_path, pagesize=A4, leftMargin=2*cm, rightMargin=2*cm,
                        topMargin=2*cm, bottomMargin=2*cm)

# Styles
styles = getSampleStyleSheet()
title_style = ParagraphStyle('CNTitle', fontName='SimHei', fontSize=18, leading=24,
                             alignment=TA_CENTER, spaceAfter=6)
h1_style = ParagraphStyle('CNH1', fontName='SimHei', fontSize=14, leading=20,
                          spaceBefore=12, spaceAfter=6, textColor=HexColor('#1a5276'))
h2_style = ParagraphStyle('CNH2', fontName='SimHei', fontSize=11, leading=16,
                          spaceBefore=8, spaceAfter=4, textColor=HexColor('#2c3e50'))
body_style = ParagraphStyle('CNBody', fontName='SimSun', fontSize=9, leading=14,
                            spaceBefore=2, spaceAfter=2)
bullet_style = ParagraphStyle('CNBullet', fontName='SimSun', fontSize=9, leading=13,
                              leftIndent=12, bulletIndent=0, spaceBefore=1, spaceAfter=1)
small_style = ParagraphStyle('CNSmall', fontName='SimSun', fontSize=8, leading=11,
                             textColor=HexColor('#555555'))

elements = []

# Title
elements.append(Paragraph(data['report_title'], title_style))
elements.append(Paragraph(f"报告日期: {data['report_date']}  |  查询区间: {data['query_period']}", small_style))
elements.append(Spacer(1, 4*mm))

# Summary table
summary_data = [
    ['股票', '代码', '行业', '现价', '市值(亿)', '3月调研', '3月研报', '评级分布']
]
for s in data['stocks']:
    ir = s['institution_research']
    rr = s['research_reports']
    rating_dist = rr.get('rating_distribution', {})
    rating_str = ' '.join([f"{k}:{v}" for k,v in rating_dist.items()]) if rating_dist else '无'
    research_count = str(ir['total_count_3m'])
    report_count = str(rr['total_count_3m'])
    summary_data.append([
        s['name'], s['code'], s['sector'],
        str(s['current_price']), str(s['market_cap_yi']),
        research_count, report_count, rating_str
    ])

col_widths = [40, 42, 60, 36, 44, 30, 30, 90]
t = Table(summary_data, colWidths=col_widths)
t.setStyle(TableStyle([
    ('FONTNAME', (0,0), (-1,-1), 'SimSun'),
    ('FONTNAME', (0,0), (-1,0), 'SimHei'),
    ('FONTSIZE', (0,0), (-1,-1), 8),
    ('FONTSIZE', (0,0), (-1,0), 8.5),
    ('BACKGROUND', (0,0), (-1,0), HexColor('#2c3e50')),
    ('TEXTCOLOR', (0,0), (-1,0), white),
    ('ALIGN', (3,0), (5,-1), 'RIGHT'),
    ('GRID', (0,0), (-1,-1), 0.5, HexColor('#bdc3c7')),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [white, HexColor('#f8f9fa')]),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('TOPPADDING', (0,0), (-1,-1), 3),
    ('BOTTOMPADDING', (0,0), (-1,-1), 3),
]))
elements.append(t)
elements.append(Spacer(1, 6*mm))

# Per-stock detail
for s in data['stocks']:
    elements.append(Paragraph(f"━━━ {s['name']}({s['code']}) — {s['sector']} ━━━", h1_style))
    
    # 机构调研
    ir = s['institution_research']
    elements.append(Paragraph(f"▌ 机构调研: 近3月 {ir['total_count_3m']} 次", h2_style))
    for rd in ir.get('research_details', []):
        elements.append(Paragraph(f"日期: {rd['date']} | 类型: {rd['type']}", bullet_style))
        elements.append(Paragraph(f"参与: {rd['participants']}", bullet_style))
        for kp in rd.get('key_points', []):
            elements.append(Paragraph(f"• {kp}", bullet_style))
    
    notable = ir.get('notable_institutions', [])
    if notable:
        elements.append(Paragraph(f"知名机构: {', '.join(notable)}", body_style))
    
    # 研报信息
    rr = s['research_reports']
    elements.append(Paragraph(f"▌ 研报信息: 近3月 {rr['total_count_3m']} 篇", h2_style))
    for rpt in rr.get('reports', []):
        elements.append(Paragraph(
            f"{rpt['date']} {rpt['institution']} | {rpt['rating']} | {rpt['title']}", bullet_style))
        eps_str = f"  EPS: 今年{rpt.get('eps_this_year','N/A')} / 明年{rpt.get('eps_next_year','N/A')}"
        pe_str = f"  PE: 今年{rpt.get('pe_this_year','N/A')}x / 明年{rpt.get('pe_next_year','N/A')}x"
        elements.append(Paragraph(eps_str, small_style))
        elements.append(Paragraph(pe_str, small_style))
    
    # Other reports (for stocks with more data)
    other = rr.get('other_recent_reports', [])
    if other:
        elements.append(Paragraph("同期其他研报:", small_style))
        for rpt in other[:6]:
            elements.append(Paragraph(
                f"  {rpt['date']} {rpt['institution']} [{rpt['rating']}] {rpt['title']}", small_style))
    
    note = rr.get('note', '')
    if note:
        elements.append(Paragraph(f"注: {note}", small_style))
    
    # 核心观点
    elements.append(Paragraph("▌ 机构核心观点", h2_style))
    for vp in s.get('core_viewpoints', []):
        elements.append(Paragraph(f"• {vp}", bullet_style))
    
    # 估值分析
    va = s['valuation_analysis']
    elements.append(Paragraph("▌ 估值空间分析", h2_style))
    elements.append(Paragraph(f"当前价: {va['current_price']}元", body_style))
    elements.append(Paragraph(va['valuation_space'], body_style))
    if va.get('note'):
        elements.append(Paragraph(f"注: {va['note']}", small_style))
    
    # 买入观点汇总
    bs = s['buy_summary']
    elements.append(Paragraph("▌ 买入观点汇总", h2_style))
    elements.append(Paragraph("看好:", h2_style))
    for bp in bs.get('bull_points', []):
        elements.append(Paragraph(f"✓ {bp}", bullet_style))
    elements.append(Paragraph("担忧:", h2_style))
    for bp in bs.get('bear_points', []):
        elements.append(Paragraph(f"✗ {bp}", bullet_style))
    
    elements.append(Spacer(1, 4*mm))

# Overall summary
elements.append(Paragraph("━━━ 总体结论 ━━━", h1_style))
sm = data['summary']
elements.append(Paragraph(sm['overall_findings'], body_style))
elements.append(Paragraph(f"机构覆盖最充分: {sm['most_covered']}", body_style))
elements.append(Paragraph(f"机构覆盖最少: {sm['least_covered']}", body_style))
elements.append(Paragraph(f"估值警示: {sm['valuation_warning']}", body_style))
elements.append(Paragraph(f"机构一致看好: {sm['strong_consensus']}", body_style))

# Build PDF
doc.build(elements)
print(f"PDF generated: {pdf_path}")
