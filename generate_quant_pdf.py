# -*- coding: utf-8 -*-
"""生成量化基本面选股模型v1.1 PDF报告"""
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

    # 中文字体
    try:
        pdfmetrics.registerFont(TTFont('SimHei', 'C:/Windows/Fonts/simhei.ttf'))
        pdfmetrics.registerFont(TTFont('SimSun', 'C:/Windows/Fonts/simsun.ttc'))
        pdfmetrics.registerFont(TTFont('Microsoft YaHei', 'C:/Windows/Fonts/msyh.ttc'))
        CHINESE_FONT = 'SimHei'
        CHINESE_FONT_BODY = 'SimSun'
    except:
        CHINESE_FONT = 'Helvetica'
        CHINESE_FONT_BODY = 'Helvetica'

    # 颜色
    RED   = colors.HexColor('#E53935')
    GREEN = colors.HexColor('#43A047')
    BLUE  = colors.HexColor('#1E88E5')
    GOLD  = colors.HexColor('#F9A825')
    GRAY  = colors.HexColor('#757575')
    LIGHT_BLUE = colors.HexColor('#E3F2FD')
    LIGHT_RED  = colors.HexColor('#FFEBEE')

    # 样式
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('Title', fontName=CHINESE_FONT, fontSize=16,
        textColor=colors.HexColor('#1A237E'), alignment=TA_CENTER, spaceAfter=6)
    subtitle_style = ParagraphStyle('Subtitle', fontName=CHINESE_FONT, fontSize=10,
        textColor=GRAY, alignment=TA_CENTER, spaceAfter=12)
    section_style = ParagraphStyle('Section', fontName=CHINESE_FONT, fontSize=12,
        textColor=BLUE, spaceBefore=12, spaceAfter=4)
    body_style = ParagraphStyle('Body', fontName=CHINESE_FONT_BODY, fontSize=9,
        leading=14, spaceAfter=4)
    note_style = ParagraphStyle('Note', fontName=CHINESE_FONT_BODY, fontSize=8,
        textColor=GRAY, leading=12)
    tag_style_s = ParagraphStyle('TagS', fontName=CHINESE_FONT, fontSize=9,
        textColor=colors.white, backColor=RED, alignment=TA_CENTER)
    tag_style_a = ParagraphStyle('TagA', fontName=CHINESE_FONT, fontSize=9,
        textColor=colors.white, backColor=GREEN, alignment=TA_CENTER)

    # 加载数据
    with open(r'D:\mystock\solo\report_daily\stock_quant_model_v1.1_20260619.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    results = data['results']
    ia_top5 = [s for s in results if s['source'] == 'IA'][:5]
    ib_top5 = [s for s in results if s['source'] == 'IB'][:5]

    # 文档
    out_path = r'D:\mystock\solo\report_daily\stock_quant_model_v1.1_20260619.pdf'
    doc = SimpleDocTemplate(out_path, pagesize=A4,
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm)
    story = []

    # ===== 标题 =====
    story.append(Paragraph('📊 个股量化基本面选股模型 v1.1', title_style))
    story.append(Paragraph('五因子体系 | IA池(69只) + IB池 | 2026-06-19', subtitle_style))
    story.append(HRFlowable(width='100%', thickness=2, color=BLUE, spaceAfter=10))

    # ===== 模型说明 =====
    story.append(Paragraph('模型架构', section_style))
    story.append(Paragraph(
        '本模型整合五大因子体系：<b>成长因子(35%)</b>、<b>质量因子(20%)</b>、'
        '<b>价值因子(15%)</b>、<b>动量因子(15%)</b>、<b>预期因子(15%)</b>。'
        '预期因子整合新闻线索（重大合同、业绩指引、产能扩张），对佰维存储、鹏辉能源等出具strong BUY信号。',
        body_style))

    # ===== 因子权重表 =====
    weight_data = [
        ['因子', '权重', '计算方式', '说明'],
        ['成长因子', '35%', 'Q1增速+H1加速+v7评分百分位', '核心因子'],
        ['质量因子', '20%', 'PE合理性+营收规模+净利率', '估值安全边际'],
        ['价值因子', '15%', 'PEG+市值弹性', '成长与价值平衡'],
        ['动量因子', '15%', 'Q1业绩超预期程度', '短期趋势确认'],
        ['预期因子', '15%', '新闻信号加权得分', '公告/研报/订单线索'],
    ]
    wt = Table(weight_data, colWidths=[3*cm, 1.5*cm, 6*cm, 4*cm])
    wt.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), BLUE),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,-1), CHINESE_FONT),
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('GRID', (0,0), (-1,-1), 0.5, GRAY),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#F5F5F5')]),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
        ('RIGHTPADDING', (0,0), (-1,-1), 4),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ]))
    story.append(wt)
    story.append(Spacer(1, 0.3*cm))

    # ===== TOP15 排名表 =====
    story.append(Paragraph('TOP15 综合排名', section_style))
    header = ['排名', '代码', '名称', '综合', '成长', '质量', '价值', '动量', '预期', '信号']
    rows = [header]
    for i, s in enumerate(results[:15]):
        note = '📰强' if s['news'].get('hot') else ('📝中' if s['news'] else '')
        rows.append([
            str(i+1), s['code'], s['name'],
            f"{s['final_score']:.1f}",
            f"{s['growth_score']:.1f}", f"{s['quality_score']:.1f}",
            f"{s['value_score']:.1f}", f"{s['momentum_score']:.1f}",
            f"{s['expect_score']:.1f}", note
        ])

    col_w = [1*cm, 2.5*cm, 2.5*cm, 1.5*cm, 1.5*cm, 1.5*cm, 1.5*cm, 1.5*cm, 1.5*cm, 1.5*cm]
    pt = Table(rows, colWidths=col_w)
    ts = TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1565C0')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,-1), CHINESE_FONT),
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('GRID', (0,0), (-1,-1), 0.3, GRAY),
        ('TOPPADDING', (0,0), (-1,-1), 2),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
    ])
    # 前5名金色，6-15名蓝色
    for i in range(1, 6):
        ts.add('BACKGROUND', (0,i), (-1,i), colors.HexColor('#FFF8E1'))
        ts.add('FONTNAME', (0,i), (-1,i), CHINESE_FONT)
    for i in range(6, 15):
        ts.add('BACKGROUND', (0,i+1), (-1,i+1), colors.HexColor('#E8EAF6'))
    # 综合分最高加粗
    for i in range(1, 16):
        ts.add('FONTNAME', (3,i), (3,i), CHINESE_FONT)
    pt.setStyle(ts)
    story.append(pt)
    story.append(Spacer(1, 0.3*cm))

    # ===== TOP5 详细分析 =====
    story.append(Paragraph('🥇 TOP5 详细分析', section_style))
    for i, s in enumerate(results[:5]):
        bg = colors.HexColor('#FFF8E1') if i == 0 else colors.HexColor('#F5F5F5')
        note = s['news'].get('note', '—') if s['news'] else '—'
        story.append(Paragraph(
            f'<b>{i+1}. {s["name"]}（{s["code"]}）</b>  '
            f'<font color="#1565C0">综合 {s["final_score"]:.1f}分</font>  '
            f'PE:{s["pe"]}x | Q1营收:{s["q1_rev_yi"]}亿({s["q1_rev_yoy"]}%) | '
            f'Q1净利:{s["q1_ni_yi"]}亿 | H1加速:{s["h1_accel"]}pp',
            body_style))
        story.append(Paragraph(
            f'  成长:{s["growth_score"]:.1f} 质量:{s["quality_score"]:.1f} '
            f'价值:{s["value_score"]:.1f} 动量:{s["momentum_score"]:.1f} 预期:{s["expect_score"]:.1f}',
            note_style))
        if note and note != '—':
            story.append(Paragraph(
                f'  <font color="#E53935">📰 {note}</font>',
                note_style))
        story.append(Spacer(1, 0.15*cm))

    # ===== 新闻信号汇总 =====
    hot_stocks = [s for s in results if s['news'].get('hot')]
    if hot_stocks:
        story.append(Paragraph('📰 强信号股票（新闻验证）', section_style))
        for s in hot_stocks:
            note = s['news'].get('note', '')
            story.append(Paragraph(
                f'● {s["name"]}（{s["code"]}）<b>{s["final_score"]:.1f}分</b> | '
                f'预期因子:{s["expect_score"]:.1f} | {note}',
                body_style))
        story.append(Spacer(1, 0.2*cm))

    # ===== 风险提示 =====
    story.append(HRFlowable(width='100%', thickness=0.5, color=GRAY))
    story.append(Paragraph(
        '⚠️ 风险提示：本模型基于历史财报数据和公开新闻线索，'
        '不构成投资建议。实际业绩需以公司公告为准。'
        '模型版本 v1.1，数据截止 2026-06-19。',
        note_style))

    doc.build(story)
    print(f'✅ PDF已生成：{out_path}')
    return out_path

if __name__ == '__main__':
    make_pdf()
