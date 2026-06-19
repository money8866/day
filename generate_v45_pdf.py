# -*- coding: utf-8 -*-
"""V4.5 策略信号报告生成"""
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
        pdfmetrics.registerFont(TTFont('Microsoft YaHei', 'C:/Windows/Fonts/msyh.ttc'))
        CH = 'SimHei'
    except:
        CH = 'Helvetica'

    RED    = colors.HexColor('#E53935')
    GREEN  = colors.HexColor('#2E7D32')
    BLUE   = colors.HexColor('#1565C0')
    GOLD   = colors.HexColor('#F9A825')
    PURPLE = colors.HexColor('#6A1B9A')
    GRAY   = colors.HexColor('#757575')

    styles = getSampleStyleSheet()
    title  = ParagraphStyle('T', fontName=CH, fontSize=15, textColor=BLUE, alignment=TA_CENTER, spaceAfter=4)
    sub    = ParagraphStyle('S', fontName=CH, fontSize=9,  textColor=GRAY,  alignment=TA_CENTER, spaceAfter=8)
    sec    = ParagraphStyle('E', fontName=CH, fontSize=11, textColor=BLUE,  spaceBefore=10, spaceAfter=3)
    body   = ParagraphStyle('B', fontName=CH, fontSize=9,  leading=14)
    note   = ParagraphStyle('N', fontName=CH, fontSize=8,  textColor=GRAY,  leading=11)

    with open(r'D:\mystock\solo\report_daily\v45_signals_20260619.json', 'r', encoding='utf-8') as f:
        sig = json.load(f)

    buys   = sig.get('buy_signals', [])
    watchs = sig.get('watch_signals', [])
    avoids = [s for s in sig.get('watch_signals', []) if s.get('total_score', 0) < 55]

    out_path = r'D:\mystock\solo\report_daily\v45_strategy_report_20260619.pdf'
    doc = SimpleDocTemplate(out_path, pagesize=A4,
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm)
    story = []

    # 标题
    story.append(Paragraph('V4.5 上涨空间预测策略报告', title))
    story.append(Paragraph(f'多因子共振 | 大盘{sig["market_env"]}环境 | {sig["date"]}', sub))
    story.append(HRFlowable(width='100%', thickness=2, color=BLUE, spaceAfter=8))

    # 五因子权重
    story.append(Paragraph('五因子体系', sec))
    factor_data = [
        ['因子', '满分', '计算方式', '权重系数'],
        ['趋势强度', '30', '多头排列(MA5>MA10>MA20)+均线方向', '30%'],
        ['回档评分', '25', '回踩MA5+10/MA10+5/突破-5（V4核心）', '25%'],
        ['量能验证', '20', '缩量回踩<MA20均量60%=完美信号', '20%'],
        ['RSI确认', '15', 'RSI-2<5 + RSI14<35=极端超卖', '15%'],
        ['MACD底背离', '10', 'DIF黄金交叉+零轴上方', '10%'],
    ]
    ft = Table(factor_data, colWidths=[3*cm, 1.2*cm, 7*cm, 3.3*cm])
    ft.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), BLUE), ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,-1), CH), ('FONTSIZE', (0,0), (-1,-1), 8),
        ('GRID', (0,0), (-1,-1), 0.3, GRAY),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#F5F5F5')]),
        ('LEFTPADDING', (0,0), (-1,-1), 4), ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ]))
    story.append(ft)
    story.append(Spacer(1, 0.3*cm))

    # BUY信号
    story.append(Paragraph(f'✅ BUY 信号（{len(buys)}只）— 建议重点关注', sec))
    if buys:
        for r in buys:
            bg = colors.HexColor('#E8F5E9')
            story.append(Paragraph(
                f'<b>{r["name"]}（{r["code"]}）</b> '
                f'<font color="#E53935"><b>综合 {r["total_score"]:.1f}分</b></font> | '
                f'{r["sector_rating"]}级板块×{r["sector_bonus"]:.2f} | '
                f'信心：<b>{r["confidence"]}</b> | 预估上涨：<b>{r["upside_pct"]:.0f}%</b>',
                body))
            story.append(Paragraph(
                f'  趋势:{r["trend_score"]:.1f} | 回档:{r["pullback_score"]:.1f} | '
                f'量能:{r["volume_score"]:.1f} | RSI:{r["rsi_score"]:.1f} | MACD:{r["macd_score"]:.1f}',
                note))
            story.append(Spacer(1, 0.1*cm))
    else:
        story.append(Paragraph('  今日无BUY信号，建议观望。', note))

    # WATCH信号
    story.append(Paragraph(f'🔍 WATCH 信号（{len(watchs)}只）— 观察等待回调', sec))
    wrows = [['代码', '名称', '综合', '趋势', '回档', '量能', 'RSI', 'MACD', '板块', '预估上涨']]
    for r in watchs[:20]:
        wrows.append([
            r['code'], r['name'], f"{r['total_score']:.1f}",
            f"{r['trend_score']:.1f}", f"{r['pullback_score']:.1f}",
            f"{r['volume_score']:.1f}", f"{r['rsi_score']:.1f}",
            f"{r['macd_score']:.1f}", r['sector_rating']+'×'+f"{r['sector_bonus']:.2f}",
            f"{r['upside_pct']:.0f}%"
        ])
    wt = Table(wrows, colWidths=[2.5*cm, 2.5*cm, 1.3*cm, 1.3*cm, 1.3*cm, 1.3*cm, 1.3*cm, 1.3*cm, 2*cm, 1.7*cm])
    wt.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#424242')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,-1), CH), ('FONTSIZE', (0,0), (-1,-1), 8),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('GRID', (0,0), (-1,-1), 0.3, GRAY),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#F5F5F5')]),
        ('TOPPADDING', (0,0), (-1,-1), 2), ('BOTTOMPADDING', (0,0), (-1,-1), 2),
    ]))
    story.append(wt)

    # BUY详情
    if buys:
        story.append(Spacer(1, 0.2*cm))
        story.append(Paragraph('BUY信号详细交易计划', sec))
        for r in buys:
            story.append(Paragraph(
                f'<b>{r["name"]}（{r["code"]}）</b> — {r["theme"]}',
                body))
            story.append(Paragraph(
                f'  信号强度：{r["confidence"]} | 综合评分 {r["total_score"]:.1f}分 '
                f'| 板块加成 {r["sector_rating"]}×{r["sector_bonus"]:.2f}',
                note))
            story.append(Spacer(1, 0.1*cm))

    # 风险提示
    story.append(Spacer(1, 0.3*cm))
    story.append(HRFlowable(width='100%', thickness=0.5, color=GRAY))
    story.append(Paragraph(
        '⚠️ 风险提示：策略基于历史技术形态，大盘跌破MA60时策略禁用（AVOID信号）。'
        '实际入场需结合个股成交量验证（建议回踩日均量<60%时确认）。本报告不构成投资建议。',
        note))

    doc.build(story)
    print(f'✅ PDF已生成：{out_path}')
    return out_path

if __name__ == '__main__':
    make_pdf()
