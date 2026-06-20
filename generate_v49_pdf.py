# -*- coding: utf-8 -*-
"""V4.9 全市场扫描PDF报告生成"""

import json, os
from datetime import datetime
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# 注册中文字体
def setup_chinese_pdf():
    """注册中文字体（SimHei黑体）"""
    font_paths = [
        'C:/Windows/Fonts/simhei.ttf',
        'C:/Windows/Fonts/msyh.ttc',
        '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc'
    ]
    for path in font_paths:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont('SimHei', path))
                return 'SimHei'
            except:
                continue
    return 'Helvetica'

FONT_NAME = setup_chinese_pdf()

# ========== 读取JSON ==========
INPUT_FILE = r'D:\mystock\solo\report_daily\v49_full_scan_20260619.json'
OUTPUT_FILE = r'D:\mystock\solo\report_daily\v49_full_scan_20260619.pdf'

with open(INPUT_FILE, 'r', encoding='utf-8') as f:
    data = json.load(f)

date_str = data.get('date', datetime.now().strftime('%Y-%m-%d'))
total_stocks = data.get('total_stocks', 0)
buy_signals = data.get('buy_signals', [])
watch_signals = data.get('watch_signals', [])
breakout_signals = data.get('breakout_signals', [])

# ========== 创建PDF ==========
doc = SimpleDocTemplate(OUTPUT_FILE, pagesize=A4,
                        rightMargin=1.5*cm, leftMargin=1.5*cm,
                        topMargin=1.5*cm, bottomMargin=1.5*cm)

styles = getSampleStyleSheet()
title_style = ParagraphStyle(
    'TitleCN', parent=styles['Title'],
    fontName=FONT_NAME, fontSize=18, spaceAfter=12,
    textColor=colors.HexColor('#1a1a1a')
)
heading_style = ParagraphStyle(
    'HeadingCN', parent=styles['Heading2'],
    fontName=FONT_NAME, fontSize=14, spaceAfter=8, spaceBefore=12,
    textColor=colors.HexColor('#333333')
)
normal_style = ParagraphStyle(
    'NormalCN', parent=styles['Normal'],
    fontName=FONT_NAME, fontSize=10, leading=14,
    textColor=colors.HexColor('#555555')
)

elements = []

# ========== 标题 ==========
elements.append(Paragraph(f"V4.9 全市场上涨空间扫描报告", title_style))
elements.append(Paragraph(f"日期：{date_str} | 扫描范围：{total_stocks}只A股", normal_style))
elements.append(Spacer(1, 0.5*cm))

# ========== 摘要 ==========
elements.append(Paragraph("📊 信号摘要", heading_style))
summary_data = [
    ['BUY信号', f"{len(buy_signals)}只", "突破模式，强势股"],
    ['WATCH信号', f"{len(watch_signals)}只", "观察池，等待回调"],
    ['BREAKOUT', f"{len(breakout_signals)}只", "放量突破，跟踪"],
]
summary_table = Table(summary_data, colWidths=[3*cm, 2.5*cm, 8*cm])
summary_table.setStyle(TableStyle([
    ('FONTNAME', (0, 0), (-1, -1), FONT_NAME),
    ('FONTSIZE', (0, 0), (-1, -1), 11),
    ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#1a5276')),
    ('ALIGN', (0, 0), (0, -1), 'LEFT'),
    ('ALIGN', (1, 0), (1, -1), 'CENTER'),
    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#dddddd')),
    ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8f9fa')),
]))
elements.append(summary_table)
elements.append(Spacer(1, 0.8*cm))

# ========== BUY信号TOP20 ==========
if buy_signals:
    elements.append(Paragraph(f"✅ BUY信号（共{len(buy_signals)}只）", heading_style))
    
    buy_header = ['排名', '代码', '名称', '收盘', '量比', '突破分', '总分', '主题加成']
    buy_rows = [buy_header]
    
    for i, stock in enumerate(buy_signals[:20], 1):
        buy_rows.append([
            str(i),
            stock['code'],
            stock.get('name', '')[:6],
            f"{stock['close']:.2f}",
            f"{stock['vol_ratio']:.2f}",
            f"{stock['breakout']:.0f}",
            f"{stock['total']:.1f}",
            f"{stock['bonus']:.2f}",
        ])
    
    buy_table = Table(buy_rows, colWidths=[1*cm, 2.5*cm, 2.5*cm, 2*cm, 1.8*cm, 1.8*cm, 1.8*cm, 2*cm])
    buy_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), FONT_NAME),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#27ae60')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#bdc3c7')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#ecf0f1')]),
    ]))
    elements.append(buy_table)
    elements.append(Spacer(1, 0.8*cm))

# ========== WATCH信号TOP30 ==========
if watch_signals:
    elements.append(Paragraph(f"🔍 WATCH信号（共{len(watch_signals)}只，显示前30）", heading_style))
    
    watch_header = ['排名', '代码', '名称', '收盘', '量比', '模式', '总分']
    watch_rows = [watch_header]
    
    for i, stock in enumerate(watch_signals[:30], 1):
        mode_icon = '🚀' if stock['mode'] == 'BREAKOUT' else '📉'
        watch_rows.append([
            str(i),
            stock['code'],
            stock.get('name', '')[:6],
            f"{stock['close']:.2f}",
            f"{stock['vol_ratio']:.2f}",
            mode_icon,
            f"{stock['total']:.1f}",
        ])
    
    watch_table = Table(watch_rows, colWidths=[1*cm, 2.5*cm, 2.5*cm, 2*cm, 1.8*cm, 1.5*cm, 1.8*cm])
    watch_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), FONT_NAME),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#3498db')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#bdc3c7')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#ecf0f1')]),
    ]))
    elements.append(watch_table)
    elements.append(Spacer(1, 0.8*cm))

# ========== 策略说明 ==========
elements.append(Paragraph("📋 V4.9策略说明", heading_style))
strategy_text = """
<b>改进点：</b><br/>
• 模式B评分收紧（满分115→85），避免全体100分封顶<br/>
• BUY阈值提高（75→85），过滤弱信号<br/>
• 突破确认加强（量比&gt;1.3硬约束+突破误差≤0.5%）<br/>
• 动态主题强度正确路径（report_daily/）<br/>
• Tushare配置修复（dotenv加载token）<br/><br/>
<b>交易建议：</b><br/>
• BUY：等待回调2-3%入场，止损-7%，止盈+15%<br/>
• WATCH：观察回调至MA10/MA20支撑位再行动<br/>
• 当前市场：突破信号多（125只），回踩机会少
"""
elements.append(Paragraph(strategy_text, normal_style))

# ========== 生成PDF ==========
doc.build(elements)
print(f"✅ PDF已生成：{OUTPUT_FILE}")
