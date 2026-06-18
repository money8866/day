#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将JSON股票扫描结果转换为PDF（支持中文）
使用方法：python convert_json_to_pdf.py <input_json_file> <output_pdf_file>
"""

import sys
import os
import json
import platform
from datetime import datetime
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors


def setup_chinese_pdf():
    """注册系统中文字体"""
    system = platform.system()
    
    if system == 'Darwin':
        candidates = [
            ('/System/Library/Fonts/STHeiti Light.ttc', 'STHeiti', 0),
            ('/System/Library/Fonts/STHeiti Medium.ttc', 'STHeitiMedium', 0),
            ('/System/Library/Fonts/Supplemental/Songti.ttc', 'Songti', 0),
        ]
    elif system == 'Windows':
        candidates = []
        windir = os.environ.get('WINDIR', 'C:\\Windows')
        dirs = [os.path.join(windir, 'Fonts')]
        local = os.environ.get('LOCALAPPDATA', '')
        if local:
            dirs.append(os.path.join(local, 'Microsoft', 'Windows', 'Fonts'))
        for d in dirs:
            for fname, name, idx in [
                ('msyh.ttc', 'MicrosoftYaHei', 0),
                ('msyhbd.ttc', 'MicrosoftYaHeiBold', 0),
                ('simhei.ttf', 'SimHei', 0),
                ('simsun.ttc', 'SimSun', 0),
            ]:
                candidates.append((os.path.join(d, fname), name, idx))
    else:
        candidates = [
            ('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc', 'NotoSansCJK', 0),
            ('/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc', 'WQYZenHei', 0),
        ]
    
    cn_font = None
    for font_path, font_name, idx in candidates:
        if os.path.exists(font_path):
            try:
                pdfmetrics.registerFont(TTFont(font_name, font_path, subfontIndex=idx))
                cn_font = font_name
                break
            except:
                continue
    
    if cn_font is None:
        print("警告：未找到中文字体")
        cn_font = 'Helvetica'
    
    styles = getSampleStyleSheet()
    for style in styles.byName.values():
        if hasattr(style, 'fontName'):
            style.fontName = cn_font
    
    return cn_font, styles


def convert_json_to_pdf(input_file, output_file):
    """将JSON股票扫描结果转换为PDF"""
    
    # 读取JSON
    print(f"正在读取文件: {input_file}")
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    scan_time = data.get('scan_time', 'N/A')
    total_count = data.get('total_count', 0)
    stocks = data.get('data', [])
    
    print(f"扫描时间: {scan_time}")
    print(f"股票数量: {total_count}")
    
    # 设置中文字体
    print("正在配置中文字体...")
    cn_font, styles = setup_chinese_pdf()
    print(f"使用字体: {cn_font}")
    
    # 创建PDF（横向页面以适应表格）
    print(f"正在创建PDF: {output_file}")
    doc = SimpleDocTemplate(
        output_file,
        pagesize=landscape(A4),
        leftMargin=1.5*cm,
        rightMargin=1.5*cm,
        topMargin=1.5*cm,
        bottomMargin=1.5*cm,
    )
    
    # 定义样式
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Title'],
        fontName=cn_font,
        fontSize=18,
        alignment=TA_CENTER,
        spaceAfter=12,
    )
    
    subtitle_style = ParagraphStyle(
        'CustomSubtitle',
        parent=styles['Normal'],
        fontName=cn_font,
        fontSize=11,
        alignment=TA_CENTER,
        spaceAfter=20,
    )
    
    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontName=cn_font,
        fontSize=14,
        spaceAfter=10,
        spaceBefore=15,
    )
    
    body_style = ParagraphStyle(
        'CustomNormal',
        parent=styles['Normal'],
        fontName=cn_font,
        fontSize=9,
        leading=12,
    )
    
    story = []
    
    # 标题
    story.append(Paragraph("主板扫描报告", title_style))
    story.append(Paragraph(f"扫描时间: {scan_time} | 共 {total_count} 只股票", subtitle_style))
    story.append(Spacer(1, 12))
    
    # 汇总表格
    summary_header = ['序号', '代码', '名称', '主题', '市值(亿)', '评级', '终极评分', '二波评分', '60日涨幅', '120日涨幅', '阶段']
    summary_data = [summary_header]
    
    for i, stock in enumerate(stocks, 1):
        row = [
            str(i),
            stock.get('ts_code', ''),
            stock.get('name', ''),
            stock.get('theme', ''),
            f"{stock.get('market_cap_yi', 0):.1f}",
            stock.get('rating', ''),
            f"{stock.get('ultimate_score', 0):.1f}",
            f"{stock.get('second_wave_score', 0):.1f}",
            f"+{stock.get('ret_60', 0):.1f}%",
            f"+{stock.get('ret_120', 0):.1f}%",
            stock.get('stage', '').replace('阶段', '').replace(':', ':')[:15],
        ]
        summary_data.append(row)
    
    # 创建表格
    col_widths = [0.8*cm, 2.2*cm, 2.2*cm, 3.5*cm, 1.8*cm, 1.2*cm, 1.8*cm, 1.8*cm, 2*cm, 2*cm, 4*cm]
    table = Table(summary_data, colWidths=col_widths)
    
    table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), cn_font),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2E4057')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('ALIGN', (0, 1), (0, -1), 'CENTER'),
        ('ALIGN', (4, 1), (9, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F5F5F5')]),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    
    story.append(table)
    story.append(PageBreak())
    
    # 详细信息页
    story.append(Paragraph("股票详细分析", heading_style))
    story.append(Spacer(1, 10))
    
    for i, stock in enumerate(stocks, 1):
        # 股票标题
        stock_title = f"{i}. {stock.get('name', '')} ({stock.get('ts_code', '')})"
        story.append(Paragraph(stock_title, heading_style))
        story.append(Spacer(1, 6))
        
        # 详细信息表格
        detail_data = [
            ['主题', stock.get('theme', ''), '行业地位', stock.get('industry', '')],
            ['市值(亿)', f"{stock.get('market_cap_yi', 0):.1f}", '20日均成交(亿)', f"{stock.get('avg_amount_20d_yi', 0):.1f}"],
            ['终极评分', f"{stock.get('ultimate_score', 0):.1f}", '二波评分', f"{stock.get('second_wave_score', 0):.1f}"],
            ['主题评分', f"{stock.get('theme_score', 0):.1f}", '牛股评分', f"{stock.get('bull_score', 0):.0f}"],
            ['评级', stock.get('rating', ''), '阶段', stock.get('stage', '')],
            ['5日涨幅', f"+{stock.get('ret_5', 0):.1f}%", '20日涨幅', f"+{stock.get('ret_20', 0):.1f}%"],
            ['60日涨幅', f"+{stock.get('ret_60', 0):.1f}%", '120日涨幅', f"+{stock.get('ret_120', 0):.1f}%"],
            ['MA20乖离', f"+{stock.get('bias_ma20', 0):.1f}%", 'MA60乖离', f"+{stock.get('bias_ma60', 0):.1f}%"],
            ['波动率', f"{stock.get('volatility_60d', 0):.2f}%", '最大回撤', f"{stock.get('max_drawdown_60d', 0):.1f}%"],
            ['涨停次数', str(stock.get('limit_up_count_120', 0)), '热榜天数', str(stock.get('dc_hot_days_120', 0))],
        ]
        
        detail_table = Table(detail_data, colWidths=[3*cm, 5*cm, 3*cm, 5*cm])
        detail_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), cn_font),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#E8E8E8')),
            ('BACKGROUND', (2, 0), (2, -1), colors.HexColor('#E8E8E8')),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        
        story.append(detail_table)
        story.append(Spacer(1, 8))
        
        # 核心理由
        core_reason = stock.get('core_reason', '')
        if core_reason:
            story.append(Paragraph(f"<b>核心理由:</b> {core_reason}", body_style))
            story.append(Spacer(1, 4))
        
        # 风险因素
        risk_factor = stock.get('risk_factor', '')
        if risk_factor:
            story.append(Paragraph(f"<b>风险提示:</b> {risk_factor}", body_style))
        
        story.append(Spacer(1, 15))
        
        # 每5只股票分一页
        if i % 5 == 0 and i < len(stocks):
            story.append(PageBreak())
    
    # 生成PDF
    print("正在生成PDF文件...")
    doc.build(story)
    
    print(f"PDF生成成功: {output_file}")
    print(f"文件大小: {os.path.getsize(output_file)} 字节")


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print("使用方法: python convert_json_to_pdf.py <input_json_file> <output_pdf_file>")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2]
    
    if not os.path.exists(input_file):
        print(f"错误: 输入文件不存在: {input_file}")
        sys.exit(1)
    
    convert_json_to_pdf(input_file, output_file)
