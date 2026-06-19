# -*- coding: utf-8 -*-
"""V4.6 全市场扫描PDF报告"""
import sys, os, json
sys.path.insert(0, r'D:\mystock')

def make_pdf():
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable, PageBreak
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    try:
        pdfmetrics.registerFont(TTFont('SimHei', 'C:/Windows/Fonts/simhei.ttf'))
        CH = 'SimHei'
    except:
        CH = 'Helvetica'

    RED   = colors.HexColor('#E53935')
    BLUE  = colors.HexColor('#1565C0')
    GREEN = colors.HexColor('#2E7D32')
    GRAY  = colors.HexColor('#757575')

    styles = getSampleStyleSheet()
    title  = ParagraphStyle('T', fontName=CH, fontSize=15, textColor=BLUE, alignment=TA_CENTER, spaceAfter=4)
    sub    = ParagraphStyle('S', fontName=CH, fontSize=9,  textColor=GRAY, alignment=TA_CENTER, spaceAfter=8)
    sec    = ParagraphStyle('E', fontName=CH, fontSize=11, textColor=BLUE, spaceBefore=10, spaceAfter=3)
    body   = ParagraphStyle('B', fontName=CH, fontSize=9,  leading=13)
    note   = ParagraphStyle('N', fontName=CH, fontSize=8,  textColor=GRAY, leading=11)

    with open(r'D:\mystock\solo\report_daily\v46_full_scan_20260619.json', 'r', encoding='utf-8') as f:
        sig = json.load(f)

    breakout = sig.get('breakout_signals', [])
    watch    = sig.get('watch_signals', [])

    out_path = r'D:\mystock\solo\report_daily\v46_full_scan_20260619.pdf'
    doc = SimpleDocTemplate(out_path, pagesize=A4,
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm)
    story = []

    # 标题
    story.append(Paragraph('V4.6 全市场上涨空间扫描报告', title))
    story.append(Paragraph(f'双模式：回踩买点 + 突破跟踪 | 5567只A股 | {sig["date"]}', sub))
    story.append(HRFlowable(width='100%', thickness=2, color=BLUE, spaceAfter=8))

    # 统计
    story.append(Paragraph(f'📊 信号统计：BREAKOUT={len(breakout)}只 | WATCH={len(watch)}只', sec))
    story.append(Paragraph(
        f'模式A（回踩买点）：缩量回踩MA5/MA10，RSI-2超卖确认，捕捉洗盘后的二次买点', note))
    story.append(Paragraph(
        f'模式B（突破跟踪）：放量突破20日新高，趋势确认，跟踪主升浪', note))
    story.append(Spacer(1, 0.2*cm))

    # BREAKOUT表格
    if breakout:
        story.append(Paragraph(f'🚀 BREAKOUT 模式（放量突破）— TOP30', sec))
        br_rows = [['代码', '收盘', 'MA5偏离', '量比', 'RSI2', '综合', '模式']]
        for r in breakout[:30]:
            br_rows.append([
                r['code'], f"{r['close']:.2f}",
                f"{r['ma5_dev']:+.1f}%", f"{r['vol_ratio']:.2f}",
                f"{r['rsi2']:.1f}", f"{r['total']:.1f}",
                '突破'
            ])
        br_t = Table(br_rows, colWidths=[2.5*cm, 1.8*cm, 2*cm, 1.5*cm, 1.5*cm, 1.5*cm, 1.5*cm])
        br_t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#D32F2F')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('FONTNAME', (0,0), (-1,-1), CH), ('FONTSIZE', (0,0), (-1,-1), 8),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('GRID', (0,0), (-1,-1), 0.3, GRAY),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#FFEBEE')]),
            ('TOPPADDING', (0,0), (-1,-1), 2), ('BOTTOMPADDING', (0,0), (-1,-1), 2),
        ]))
        story.append(br_t)
        story.append(Spacer(1, 0.3*cm))

    # WATCH表格
    story.append(Paragraph(f'🔍 WATCH 信号（观察池）— TOP20', sec))
    wt_rows = [['代码', '收盘', 'MA5偏离', '量比', 'RSI2', '综合', '模式']]
    for r in sig.get('all_results', [])[:20]:
        mode_icon = '突破' if r['mode'] == 'BREAKOUT' else '回踩'
        wt_rows.append([
            r['code'], f"{r['close']:.2f}",
            f"{r['ma5_dev']:+.1f}%", f"{r['vol_ratio']:.2f}",
            f"{r['rsi2']:.1f}", f"{r['total']:.1f}",
            mode_icon
        ])
    wt_t = Table(wt_rows, colWidths=[2.5*cm, 1.8*cm, 2*cm, 1.5*cm, 1.5*cm, 1.5*cm, 1.5*cm])
    wt_t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#424242')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,-1), CH), ('FONTSIZE', (0,0), (-1,-1), 8),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('GRID', (0,0), (-1,-1), 0.3, GRAY),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#F5F5F5')]),
        ('TOPPADDING', (0,0), (-1,-1), 2), ('BOTTOMPADDING', (0,0), (-1,-1), 2),
    ]))
    story.append(wt_t)

    # 说明
    story.append(Spacer(1, 0.3*cm))
    story.append(HRFlowable(width='100%', thickness=0.5, color=GRAY))
    story.append(Paragraph(
        '⚠️ 说明：V4.6双模式 = 原V4回踩策略 + 新增突破跟踪。'
        '突破模式需放量>1.5倍均量，MA5偏离>3%。'
        '当前市场突破股较多（238只），回踩机会较少，建议观望或跟踪突破股回调。', note))

    doc.build(story)
    print(f'✅ PDF已生成：{out_path}')
    return out_path

if __name__ == '__main__':
    make_pdf()
