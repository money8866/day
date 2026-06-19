#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IB观察池63只 — 半年报加速选股PDF报告
筛选：Q2环比恢复 > Q1环比（gap越大越好）
"""

import json
import os
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors


def setup_chinese():
    windir = os.environ.get('WINDIR', 'C:\\Windows')
    for fname in ['msyh.ttc', 'simhei.ttf', 'simsun.ttc']:
        fp = os.path.join(windir, 'Fonts', fname)
        if os.path.exists(fp):
            pdfmetrics.registerFont(TTFont('CN', fp, subfontIndex=0))
            return 'CN'
    return 'Helvetica'


def star_rating(score, max_score=55):
    pct = score / max_score
    if pct >= 0.85: return '★★★★★'
    elif pct >= 0.70: return '★★★★'
    elif pct >= 0.55: return '★★★'
    elif pct >= 0.40: return '★★'
    else: return '★'


def generate_pdf(input_json, output_pdf):
    with open(input_json, 'r', encoding='utf-8') as f:
        data = json.load(f)

    cn = setup_chinese()
    doc = SimpleDocTemplate(output_pdf, pagesize=landscape(A4),
                            leftMargin=1.0*cm, rightMargin=1.0*cm, topMargin=1.0*cm, bottomMargin=1.0*cm)

    title_s = ParagraphStyle('T', fontName=cn, fontSize=15, alignment=TA_CENTER, spaceAfter=3)
    sub_s = ParagraphStyle('Sub', fontName=cn, fontSize=8.5, alignment=TA_CENTER, spaceAfter=6, textColor=colors.grey)
    h2_s = ParagraphStyle('H2', fontName=cn, fontSize=11, spaceAfter=5, spaceBefore=8, textColor=colors.HexColor('#1A5276'))
    body_s = ParagraphStyle('Body', fontName=cn, fontSize=8.5, leading=12)
    small_s = ParagraphStyle('Sm', fontName=cn, fontSize=7.5, leading=10)
    warn_s = ParagraphStyle('Warn', fontName=cn, fontSize=8, leading=11, textColor=colors.HexColor('#E74C3C'))
    green_s = ParagraphStyle('Grn', fontName=cn, fontSize=8, leading=11, textColor=colors.HexColor('#27AE60'))

    story = []

    # ==== 封面 ====
    story.append(Paragraph("🚀 IB观察池63只 — 半年报加速选股报告", title_s))
    story.append(Paragraph("2026-06-19 | 数据:Tushare | 覆盖:63只IB观察池 | 核心逻辑:Q2环比恢复速度>Q1环比", sub_s))

    scored = data['scored']
    valid = [r for r in scored if r.get('data_ok')]
    accel_stocks = [r for r in scored if r.get('acceleration_rev') and r.get('acceleration_gap', 0) > 0]

    # ==== 概览统计 ====
    story.append(Paragraph("📊 整体统计", h2_s))
    all_gaps = [r.get('acceleration_gap', 0) or 0 for r in scored if r.get('acceleration_gap') is not None]
    import statistics
    median_gap = statistics.median(all_gaps) if all_gaps else 0

    overview_data = [
        ['指标', '数值'],
        ['IB观察池总数', str(data['ib_pool_count']) + ' 只'],
        ['有效财务数据', str(data['valid_data_count']) + ' 只'],
        ['营收加速股 (Q2环比>Q1环比)', str(len([r for r in scored if r.get('acceleration_rev')])) + ' 只'],
        ['净利加速股 (Q2净利环比>Q1净利环比)', str(len(data.get('ni_accelerated', []))) + ' 只'],
        ['Q2加速gap中位数', '{:.1f}%'.format(median_gap)],
        ['Q2加速gap最大值', '{:.1f}%'.format(max(all_gaps)) + ' (' + max(scored, key=lambda x: x.get('acceleration_gap', 0) or 0)['name'] + ')'],
    ]
    t = Table(overview_data, colWidths=[6*cm, 10*cm])
    t.setStyle(TableStyle([
        ('FONTNAME', (0,0), (-1,-1), cn),
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1A5276')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('BACKGROUND', (0,1), (-1,-1), colors.HexColor('#F8F9FA')),
        ('GRID', (0,0), (-1,-1), 0.4, colors.grey),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ]))
    story.append(t)
    story.append(Spacer(1, 8))

    # ==== 核心发现 ====
    story.append(Paragraph("🔍 核心发现", h2_s))

    # 找出加速最强的几只
    top_accel = sorted(scored, key=lambda x: x.get('acceleration_gap', -9999) or -9999, reverse=True)[:10]
    top_ni = data.get('ni_accelerated', [])[:5]

    insight_text = """
<b>规律：A股Q1环比必然大幅为负（春节+年终奖+天数少），真正的alpha在于Q2恢复速度。</b><br/>
<br/>
<b>重要逻辑：</b>比较"Q1环比Q4"与"Q2环比Q1"的差值，差值越大说明Q2恢复斜率越陡，属于景气上行公司。<br/>
<br/>
<b>典型信号解读：</b><br/>
• Q1环比-85% → Q2环比+33%：Q2出现营收爆发，景气快速上行（广立微、特发信息）<br/>
• Q1环比-76% → Q2环比+8%：Q2显著超季节性恢复（深科技、紫光国微）<br/>
• Q1环比-72% → Q2环比-15%：Q2恢复斜率中等，但仍是恢复态势（兴森科技、锐科激光）<br/>
• Q1环比-70% → Q2环比-48%：Q2减速，景气边际恶化（晶丰明源、博杰股份）<br/>
<br/>
<b>净利加速核心标的：</b>博众精工（Q2净利环比+371%，极强恢复）、华峰测控、精智达、紫光国微<br/>
    """
    story.append(Paragraph(insight_text, body_s))
    story.append(Spacer(1, 8))

    # ==== 加速TOP20排名表 ====
    story.append(Paragraph("🏆 营收加速TOP20（按Q2/Q1环比差值排序）", h2_s))

    th = ['排名', '名称', '代码', '行业', '综合分', 'Q1环比', 'Q2环比', '加速差', 'H1营收预测', 'H1同比', '🔥标签']
    tdata = [th]

    for i, r in enumerate(top_accel):
        gap = r.get('acceleration_gap', 0) or 0
        stars = star_rating(r.get('total_score', 0))
        qoq1 = r.get('qoq_rev_q1', 0) or 0
        qoq2 = r.get('qoq_rev_q2', 0) or 0
        h1_rev = r.get('h1_2026_rev_est', 0) or 0
        h1_yoy = r.get('h1_2026_rev_yoy', 0) or 0
        theme = r.get('theme', '')[:8]
        name = r['name']
        code = r['code'][:6]

        tags = []
        if r.get('acceleration_ni'): tags.append('净利加速')
        if r.get('h1_beat_signal'): tags.append('超预期')
        if '🔥' in r.get('theme_prosperity', ''): tags.append('高景气')

        bg_color = colors.white
        if i == 0: bg_color = colors.HexColor('#FEF9E7')
        elif i < 3: bg_color = colors.HexColor('#F8F9FA')

        tdata.append([
            str(i+1), name, code, theme,
            str(r.get('total_score', 0)) + stars,
            '{:.0f}%'.format(qoq1),
            '{:.0f}%'.format(qoq2),
            '+{:.1f}%'.format(gap),
            '{:.0f}亿'.format(h1_rev) if h1_rev else '-',
            '{:+.0f}%'.format(h1_yoy) if h1_yoy is not None else '-',
            ' '.join(tags) if tags else '-',
        ])

    col_widths = [1.0*cm, 1.8*cm, 1.5*cm, 1.8*cm, 2.0*cm, 1.8*cm, 1.8*cm, 1.8*cm, 2.5*cm, 2.0*cm, 5.5*cm]
    t = Table(tdata, colWidths=col_widths)
    ts = [
        ('FONTNAME', (0,0), (-1,-1), cn),
        ('FONTSIZE', (0,0), (-1,-1), 7.5),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1A5276')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('GRID', (0,0), (-1,-1), 0.3, colors.HexColor('#DDDDDD')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 2),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
    ]
    # 颜色加速格子
    for i, r in enumerate(top_accel[:10]):
        gap = r.get('acceleration_gap', 0) or 0
        if gap > 80:
            ts.append(('BACKGROUND', (7, i+1), (7, i+1), colors.HexColor('#E8F8F5')))
            ts.append(('TEXTCOLOR', (7, i+1), (7, i+1), colors.HexColor('#1E8449')))
        elif gap > 50:
            ts.append(('BACKGROUND', (7, i+1), (7, i+1), colors.HexColor('#FDFEFE')))
            ts.append(('TEXTCOLOR', (7, i+1), (7, i+1), colors.HexColor('#117A65')))
        if i < 3:
            ts.append(('BACKGROUND', (0, i+1), (-1, i+1), colors.HexColor('#FEF9E7')))
    t.setStyle(TableStyle(ts))
    story.append(t)
    story.append(Spacer(1, 8))

    # ==== 净利加速TOP10 ====
    story.append(Paragraph("💰 净利加速TOP10（Q2净利环比>Q1净利环比）", h2_s))

    th2 = ['排名', '名称', '代码', '行业', 'Q1净利环比', 'Q2净利环比', '加速差', 'H1净利预测', '加速原因']
    tdata2 = [th2]

    ni_top = data.get('ni_accelerated', [])
    for i, r in enumerate(ni_top[:10]):
        gap = (r.get('qoq_ni_q2') or 0) - (r.get('qoq_ni_q1') or 0)
        h1_ni = r.get('h1_2026_ni_est', 0) or 0
        qoq_ni1 = r.get('qoq_ni_q1', 0) or 0
        qoq_ni2 = r.get('qoq_ni_q2', 0) or 0
        theme = r.get('theme', '')[:8]

        # 加速原因推断
        if qoq_ni2 > 100:
            reason = 'Q2净利爆发，景气快速上行'
        elif qoq_ni2 > 0:
            reason = 'Q2净利恢复正增长'
        elif qoq_ni2 > -20:
            reason = 'Q2净利减速收窄'
        else:
            reason = 'Q2净利继续下滑'

        bg = colors.white
        if i == 0:
            bg = colors.HexColor('#E8F8F5')

        tdata2.append([
            str(i+1), r['name'], r['code'][:6], theme,
            '{:.0f}%'.format(qoq_ni1), '{:.0f}%'.format(qoq_ni2),
            '+{:.0f}%'.format(gap),
            '{:.1f}亿'.format(h1_ni) if h1_ni else '-',
            reason,
        ])

    t2 = Table(tdata2, colWidths=[1.0*cm, 1.8*cm, 1.5*cm, 1.8*cm, 2.2*cm, 2.2*cm, 1.8*cm, 2.5*cm, 9.7*cm])
    t2.setStyle(TableStyle([
        ('FONTNAME', (0,0), (-1,-1), cn),
        ('FONTSIZE', (0,0), (-1,-1), 7.5),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1A5276')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('GRID', (0,0), (-1,-1), 0.3, colors.HexColor('#DDDDDD')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 2),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
        ('BACKGROUND', (0,1), (-1,1), colors.HexColor('#E8F8F5')),
    ]))
    story.append(t2)
    story.append(Spacer(1, 8))

    # ==== 完整63只排名表 ====
    story.append(PageBreak())
    story.append(Paragraph("📋 IB池完整排名（63只，按综合评分+加速差排序）", h2_s))

    th3 = ['#', '名称', '代码', '行业', 'Q1环比', 'Q2环比', '加速差', '综合分', 'Q1净利同比', 'H1净利预测', '标签']
    tdata3 = [th3]

    for i, r in enumerate(scored):
        gap = r.get('acceleration_gap', 0) or 0
        qoq1 = r.get('qoq_rev_q1', 0) or 0
        qoq2 = r.get('qoq_rev_q2', 0) or 0
        theme = r.get('theme', '')[:7]
        tags = []
        if r.get('acceleration_ni'): tags.append('净利加速')
        if r.get('h1_beat_signal'): tags.append('超预期')
        if '🔥' in r.get('theme_prosperity', ''): tags.append('🔥')
        elif '📈' in r.get('theme_prosperity', ''): tags.append('📈')

        bg = colors.white
        if i % 2 == 0:
            bg = colors.HexColor('#F8F9FA')

        tdata3.append([
            str(i+1), r['name'], r['code'][:6], theme,
            '{:.0f}%'.format(qoq1), '{:.0f}%'.format(qoq2),
            '{:+.0f}%'.format(gap),
            str(r.get('total_score', 0)),
            '{:+.0f}%'.format(r.get('q1_np_yoy', 0) or 0),
            '{:.1f}亿'.format(r.get('h1_2026_ni_est', 0) or 0),
            ' '.join(tags) if tags else '-',
        ])

    t3 = Table(tdata3, colWidths=[0.8*cm, 1.8*cm, 1.4*cm, 1.8*cm, 1.6*cm, 1.6*cm, 1.5*cm, 1.3*cm, 1.8*cm, 2.2*cm, 8.7*cm])
    t3.setStyle(TableStyle([
        ('FONTNAME', (0,0), (-1,-1), cn),
        ('FONTSIZE', (0,0), (-1,-1), 7),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#34495E')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('GRID', (0,0), (-1,-1), 0.3, colors.HexColor('#DDDDDD')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 1),
        ('BOTTOMPADDING', (0,0), (-1,-1), 1),
    ]))
    story.append(t3)

    # ==== 方法论 ====
    story.append(PageBreak())
    story.append(Paragraph("📐 分析方法论", h2_s))

    method = """
<b>一、环比加速原理</b><br/>
A股上市公司受春节假期、年终奖金结算、季度税收等因素影响，Q1营收环比Q4通常大幅下降（-60%至-85%）。
Q2环比Q1的恢复速度反映了公司业务的真实景气趋势：<br/>
• Q2环比快速转正或大幅收窄 → 景气上行（需求强劲、订单充足）<br/>
• Q2环比恢复缓慢 → 景气平稳或下行<br/>
• Q2环比进一步恶化 → 景气边际恶化<br/><br/>

<b>二、加速gap计算</b><br/>
加速gap = Q2环比Q1(%) - Q1环比Q4(%)<br/>
例：Q1环比-76%，Q2环比+8%，gap = 8 - (-76) = +84% → Q2恢复斜率极陡<br/><br/>

<b>三、综合评分体系（满分55分）</b><br/>
• 加速因子（0-40分）：gap>20%=40分, gap>10%=30分, gap>0%=20分<br/>
• Q1质量（0-20分）：Q1环比>20%=20分, >0%=15分, >-10%=10分, <-10%=5分<br/>
• H1增速（0-20分）：H1同比>50%=20分, >30%=15分, >10%=10分, >0%=5分<br/>
• 机构热度（0-10分）：inst_score>=80=10分, >=60=7分, >=50=4分<br/>
• 行业景气（0-10分）：🔥=10分, 📈=7分, 📊=4分<br/><br/>

<b>四、净利加速逻辑</b><br/>
净利加速 = Q2净利环比Q1(%) - Q1净利环比Q4(%)<br/>
净利加速比营收加速更重要，因为净利反映了公司的定价权和成本控制能力。<br/>
净利加速+营收加速 = 双重确认景气上行<br/><br/>

<b>五、重要约束</b><br/>
• H1营收预测基于季节性比例（Q1/H1历史均值）估算，Q1/H1比≈0.45<br/>
• H1净利预测基于Q1净利率（净利/营收）推算，假设Q2净利率与Q1持平<br/>
• 真实H1业绩需等中报披露（8月），当前仅为基于已知Q1的估算<br/>
• 部分次新股历史数据不足，季节性比例采用默认值
    """
    story.append(Paragraph(method, body_s))

    doc.build(story)
    size = os.path.getsize(output_pdf)
    print("PDF生成成功: {} ({} KB)".format(output_pdf, size // 1024))


if __name__ == '__main__':
    generate_pdf(
        r'D:\mystock\solo\report_daily\ib_h1_acceleration_20260619.json',
        r'D:\mystock\solo\report_daily\ib_h1_acceleration_20260619.pdf'
    )
