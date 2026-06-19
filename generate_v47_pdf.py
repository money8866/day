# -*- coding: utf-8 -*-
"""V4.7 高胜率低回撤精选PDF报告"""
import sys, os, json
sys.path.insert(0, r'D:\mystock')

def make_pdf():
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    try:
        pdfmetrics.registerFont(TTFont('SimHei', 'C:/Windows/Fonts/simhei.ttf'))
        CH = 'SimHei'
    except:
        CH = 'Helvetica'

    RED   = colors.HexColor('#D32F2F')
    BLUE  = colors.HexColor('#1565C0')
    GRAY  = colors.HexColor('#757575')

    styles = getSampleStyleSheet()
    title  = ParagraphStyle('T', fontName=CH, fontSize=15, textColor=BLUE, alignment=TA_CENTER, spaceAfter=4)
    sub    = ParagraphStyle('S', fontName=CH, fontSize=9,  textColor=GRAY, alignment=TA_CENTER, spaceAfter=8)
    sec    = ParagraphStyle('E', fontName=CH, fontSize=11, textColor=BLUE, spaceBefore=10, spaceAfter=3)
    body   = ParagraphStyle('B', fontName=CH, fontSize=9,  leading=13)
    note   = ParagraphStyle('N', fontName=CH, fontSize=8,  textColor=GRAY, leading=11)

    with open(r'D:\mystock\solo\report_daily\v47_best_picks_20260619.json', 'r', encoding='utf-8') as f:
        data = json.load(f)

    picks = data.get('top15', [])

    out_path = r'D:\mystock\solo\report_daily\v47_best_picks_20260619.pdf'
    doc = SimpleDocTemplate(out_path, pagesize=A4,
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm)
    story = []

    # 标题
    story.append(Paragraph('V4.7 高胜率低回撤精选报告', title))
    story.append(Paragraph(f'从{data["breakout_total"]}只突破股筛选出{data["filtered"]}只 | {data["date"]}', sub))
    story.append(HRFlowable(width='100%', thickness=2, color=BLUE, spaceAfter=8))

    # 筛选条件
    story.append(Paragraph('筛选条件（纯技术面）', sec))
    story.append(Paragraph(
        '• RSI14<75（未极端超买）\n'
        '• 量比<3（未放巨量）\n'
        '• 近5日涨幅<20%（未暴涨）\n'
        '• 近20日最大回撤<-35%（有安全边际）',
        note))
    story.append(Spacer(1, 0.2*cm))

    # TOP15表格
    if picks:
        story.append(Paragraph('TOP15 质量排名', sec))
        rows = [['排名', '代码', '收盘', 'RSI14', '量比', '5日涨', '回撤', '质量分']]
        for i, p in enumerate(picks[:15]):
            rows.append([
                str(i+1), p['code'], f"{p['close']:.2f}",
                f"{p['rsi14']:.1f}", f"{p['vol_ratio']:.2f}",
                f"{p['pct_5d']:+.1f}%", f"{p['max_dd']:+.1f}%",
                f"{p['quality_score']:.1f}"
            ])
        pt = Table(rows, colWidths=[1.2*cm, 2.8*cm, 1.8*cm, 1.5*cm, 1.5*cm, 1.8*cm, 1.8*cm, 2*cm])
        pt.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1565C0')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('FONTNAME', (0,0), (-1,-1), CH), ('FONTSIZE', (0,0), (-1,-1), 8),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('GRID', (0,0), (-1,-1), 0.3, GRAY),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#F5F5F5')]),
            ('TOPPADDING', (0,0), (-1,-1), 2), ('BOTTOMPADDING', (0,0), (-1,-1), 2),
        ]))
        story.append(pt)
        story.append(Spacer(1, 0.3*cm))

    # TOP5交易计划
    story.append(Paragraph('TOP5 交易计划', sec))
    for i, p in enumerate(picks[:5]):
        entry  = p['close'] * 0.98
        stop   = p['close'] * 0.93
        target = p['close'] * 1.15
        pos    = min(10, max(3, p['quality_score'] - 55))
        
        story.append(Paragraph(
            f'<b>{i+1}. {p["code"]} | 收盘{p["close"]:.2f}元 | 质量分{p["quality_score"]:.1f}</b>',
            body))
        story.append(Paragraph(
            f'   技术：RSI14={p["rsi14"]:.1f} 量比={p["vol_ratio"]:.2f} '
            f'5日{p["pct_5d"]:+.1f}% 回撤{p["max_dd"]:+.1f}%',
            note))
        story.append(Paragraph(
            f'   交易：入场<b>{entry:.2f}</b> | 止损<b>{stop:.2f}</b>(-7%) | 止盈<b>{target:.2f}</b>(+15%)',
            note))
        story.append(Paragraph(
            f'   风险比：盈亏比1:2.1 | 建议仓位：<b>{pos}%</b>',
            note))
        story.append(Spacer(1, 0.1*cm))

    # 风险提示
    story.append(Spacer(1, 0.3*cm))
    story.append(HRFlowable(width='100%', thickness=0.5, color=GRAY))
    story.append(Paragraph(
        '⚠️ 风险提示：本报告基于纯技术面筛选，未考虑基本面因素。突破模式需配合趋势确认，建议在MA5支撑处入场。止损严格执行-7%，止盈分批锁定。本报告不构成投资建议。',
        note))

    doc.build(story)
    print(f'✅ PDF已生成：{out_path}')
    return out_path

if __name__ == '__main__':
    make_pdf()
