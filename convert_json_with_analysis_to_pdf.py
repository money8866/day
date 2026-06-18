#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将JSON股票扫描结果转换为PDF（包含上涨空间分析）
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
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors


def setup_chinese_pdf():
    """注册系统中文字体"""
    system = platform.system()
    
    if system == 'Windows':
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
            ]:
                candidates.append((os.path.join(d, fname), name, idx))
    elif system == 'Darwin':
        candidates = [('/System/Library/Fonts/STHeiti Light.ttc', 'STHeiti', 0)]
    else:
        candidates = [('/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc', 'WQYZenHei', 0)]
    
    cn_font = None
    for font_path, font_name, idx in candidates:
        if os.path.exists(font_path):
            try:
                pdfmetrics.registerFont(TTFont(font_name, font_path, subfontIndex=idx))
                cn_font = font_name
                break
            except:
                continue
    
    return cn_font or 'Helvetica'


def calc_space(bias_ma20, bias_ma60, ret_60, stage, ultimate_score, value_margin):
    """计算上涨空间评级"""
    # 基础分数
    base = 100
    
    # MA20乖离惩罚（超过15%开始惩罚）
    if bias_ma20 > 25:
        base -= 60
    elif bias_ma20 > 20:
        base -= 45
    elif bias_ma20 > 15:
        base -= 30
    elif bias_ma20 > 10:
        base -= 15
    elif bias_ma20 > 5:
        base -= 5
    
    # MA60乖离惩罚
    if bias_ma60 > 80:
        base -= 40
    elif bias_ma60 > 50:
        base -= 25
    elif bias_ma60 > 30:
        base -= 10
    
    # 60日涨幅惩罚
    if ret_60 > 200:
        base -= 50
    elif ret_60 > 150:
        base -= 35
    elif ret_60 > 100:
        base -= 20
    elif ret_60 > 50:
        base -= 10
    
    # 阶段惩罚
    if '阶段5' in stage or '高位' in stage:
        base -= 30
    elif '阶段4' in stage:
        base -= 15
    
    # 评分加成
    if ultimate_score >= 75:
        base += 15
    elif ultimate_score >= 70:
        base += 10
    
    # 价值余量加成
    if value_margin >= 85:
        base += 15
    elif value_margin >= 75:
        base += 10
    elif value_margin >= 65:
        base += 5
    
    base = max(0, min(100, base))
    
    # 转换为空间估算
    if base >= 80:
        return "20-30%", "✅ 仍有空间", colors.HexColor('#27AE60')
    elif base >= 60:
        return "10-20%", "⚠️ 小幅空间", colors.HexColor('#F39C12')
    elif base >= 40:
        return "5-10%", "⚠️ 空间有限", colors.HexColor('#E67E22')
    else:
        return "0-5%", "❌ 短期透支", colors.HexColor('#E74C3C')


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
    cn_font = setup_chinese_pdf()
    print(f"使用字体: {cn_font}")
    
    # 创建PDF
    print(f"正在创建PDF: {output_file}")
    doc = SimpleDocTemplate(
        output_file,
        pagesize=landscape(A4),
        leftMargin=1.2*cm,
        rightMargin=1.2*cm,
        topMargin=1.2*cm,
        bottomMargin=1.2*cm,
    )
    
    # 定义样式
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle('Title', fontName=cn_font, fontSize=18, alignment=TA_CENTER, spaceAfter=8)
    subtitle_style = ParagraphStyle('Subtitle', fontName=cn_font, fontSize=11, alignment=TA_CENTER, spaceAfter=15)
    heading_style = ParagraphStyle('Heading', fontName=cn_font, fontSize=14, spaceAfter=8, spaceBefore=12)
    body_style = ParagraphStyle('Body', fontName=cn_font, fontSize=9, leading=12)
    small_style = ParagraphStyle('Small', fontName=cn_font, fontSize=8, leading=10)
    
    story = []
    
    # 标题页
    story.append(Paragraph("📊 主板扫描报告 v2（含上涨空间分析）", title_style))
    story.append(Paragraph(f"扫描时间: {scan_time} | 股票数量: {total_count} 只", subtitle_style))
    
    # ============ 汇总表格 ============
    summary_header = ['序号', '代码', '名称', '主题', '评级', '评分', '价值余量', 'MA20乖离', '60日涨幅', '120日涨幅', '空间判断', '操作建议']
    summary_data = [summary_header]
    
    for i, stock in enumerate(stocks, 1):
        bias_ma20 = stock.get('bias_ma20', 0)
        bias_ma60 = stock.get('bias_ma60', 0)
        ret_60 = stock.get('ret_60', 0)
        stage = stock.get('stage', '')
        ultimate_score = stock.get('ultimate_score', 0)
        value_margin = stock.get('value_margin_score', 0)
        
        space, advice, color = calc_space(bias_ma20, bias_ma60, ret_60, stage, ultimate_score, value_margin)
        
        row = [
            str(i),
            stock.get('ts_code', ''),
            stock.get('name', ''),
            stock.get('theme', '')[:8],
            stock.get('rating', ''),
            f"{ultimate_score:.1f}",
            f"{value_margin:.0f}",
            f"+{bias_ma20:.1f}%" if bias_ma20 >= 0 else f"{bias_ma20:.1f}%",
            f"+{ret_60:.1f}%",
            f"+{stock.get('ret_120', 0):.1f}%",
            space,
            advice.replace('✅', '').replace('⚠️', '').replace('❌', '').strip()
        ]
        summary_data.append(row)
    
    col_widths = [0.7*cm, 2*cm, 2*cm, 2.5*cm, 1*cm, 1.2*cm, 1.5*cm, 1.5*cm, 1.5*cm, 1.5*cm, 1.8*cm, 3*cm]
    table = Table(summary_data, colWidths=col_widths)
    
    # 表格样式
    table_style = [
        ('FONTNAME', (0, 0), (-1, -1), cn_font),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2E4057')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('ALIGN', (0, 1), (0, -1), 'CENTER'),
        ('ALIGN', (4, 1), (9, -1), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]
    
    # 评级颜色
    for i, stock in enumerate(stocks, 1):
        rating = stock.get('rating', '')
        if rating == 'S+':
            table_style.append(('BACKGROUND', (4, i), (4, i), colors.HexColor('#E74C3C')))
            table_style.append(('TEXTCOLOR', (4, i), (4, i), colors.white))
        elif rating == 'S':
            table_style.append(('BACKGROUND', (4, i), (4, i), colors.HexColor('#F39C12')))
            table_style.append(('TEXTCOLOR', (4, i), (4, i), colors.white))
    
    # 隔行变色
    for i in range(1, len(stocks) + 1):
        if i % 2 == 0:
            table_style.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#F8F8F8')))
    
    table.setStyle(TableStyle(table_style))
    story.append(table)
    story.append(PageBreak())
    
    # ============ 上涨空间分析 ============
    story.append(Paragraph("📈 上涨空间分析", heading_style))
    story.append(Spacer(1, 8))
    
    # 分析说明
    analysis_intro = """
    <b>分析方法：</b>综合考虑MA20乖离率、MA60乖离率、60日涨幅、当前阶段、评分、价值余量等六个维度，
    计算上涨空间评级。MA20乖离率是核心指标（超过25%则严重透支，超过15%开始累积风险）。
    """
    story.append(Paragraph(analysis_intro, small_style))
    story.append(Spacer(1, 15))
    
    # 按空间分类
    high_space = []
    medium_space = []
    low_space = []
    
    for stock in stocks:
        bias_ma20 = stock.get('bias_ma20', 0)
        bias_ma60 = stock.get('bias_ma60', 0)
        ret_60 = stock.get('ret_60', 0)
        stage = stock.get('stage', '')
        ultimate_score = stock.get('ultimate_score', 0)
        value_margin = stock.get('value_margin_score', 0)
        
        space, advice, color = calc_space(bias_ma20, bias_ma60, ret_60, stage, ultimate_score, value_margin)
        
        if "20-30%" in space:
            high_space.append((stock, space, advice))
        elif "10-20%" in space:
            medium_space.append((stock, space, advice))
        else:
            low_space.append((stock, space, advice))
    
    # 第一梯队
    story.append(Paragraph("<b>🏆 第一梯队：仍有空间（可关注回调机会）</b>", heading_style))
    
    if high_space:
        for stock, space, advice in high_space:
            name = stock.get('name', '')
            ts_code = stock.get('ts_code', '')
            bias_ma20 = stock.get('bias_ma20', 0)
            bias_ma60 = stock.get('bias_ma60', 0)
            ret_60 = stock.get('ret_60', 0)
            stage = stock.get('stage', '')
            ultimate_score = stock.get('ultimate_score', 0)
            value_margin = stock.get('value_margin_score', 0)
            theme = stock.get('theme', '')
            theme_score = stock.get('theme_score', 0)
            
            text = f"""<b>{name}</b>（{ts_code}）
            MA20乖离 {bias_ma20:+.1f}% | MA60乖离 {bias_ma60:+.1f}% | 60日涨幅 +{ret_60:.1f}%
            评分 {ultimate_score:.1f} | 价值余量 {value_margin:.0f} | 主题 {theme}（{theme_score:.0f}分）
            {stage} | 空间预估：<b>{space}</b>"""
            
            block = Paragraph(text.replace('\n', '<br/>'), body_style)
            story.append(block)
            story.append(Spacer(1, 6))
    else:
        story.append(Paragraph("暂无", body_style))
    
    story.append(Spacer(1, 15))
    
    # 第二梯队
    story.append(Paragraph("<b>⚠️ 第二梯队：空间有限（等待更好买点）</b>", heading_style))
    
    if medium_space:
        for stock, space, advice in medium_space:
            name = stock.get('name', '')
            ts_code = stock.get('ts_code', '')
            bias_ma20 = stock.get('bias_ma20', 0)
            bias_ma60 = stock.get('bias_ma60', 0)
            ret_60 = stock.get('ret_60', 0)
            stage = stock.get('stage', '')
            ultimate_score = stock.get('ultimate_score', 0)
            value_margin = stock.get('value_margin_score', 0)
            
            text = f"""<b>{name}</b>（{ts_code}）
            MA20乖离 {bias_ma20:+.1f}% | MA60乖离 {bias_ma60:+.1f}% | 60日涨幅 +{ret_60:.1f}%
            评分 {ultimate_score:.1f} | 价值余量 {value_margin:.0f} | {stage}
            空间预估：{space}"""
            
            story.append(Paragraph(text.replace('\n', '<br/>'), body_style))
            story.append(Spacer(1, 6))
    else:
        story.append(Paragraph("暂无", body_style))
    
    story.append(Spacer(1, 15))
    
    # 第三梯队
    story.append(Paragraph("<b>🔻 第三梯队：短期已透支（不建议追高）</b>", heading_style))
    
    if low_space:
        for stock, space, advice in low_space[:8]:  # 限制数量
            name = stock.get('name', '')
            ts_code = stock.get('ts_code', '')
            bias_ma20 = stock.get('bias_ma20', 0)
            bias_ma60 = stock.get('bias_ma60', 0)
            ret_60 = stock.get('ret_60', 0)
            stage = stock.get('stage', '')
            risk = stock.get('risk_factor', '')[:50]
            
            text = f"""<b>{name}</b>（{ts_code}）{stage}
            MA20乖离 {bias_ma20:+.1f}% | 60日涨幅 +{ret_60:.1f}% | 空间 {space}
            风险：{risk}"""
            
            story.append(Paragraph(text.replace('\n', '<br/>'), small_style))
            story.append(Spacer(1, 4))
    else:
        story.append(Paragraph("暂无", body_style))
    
    story.append(PageBreak())
    
    # ============ TOP10详细分析 ============
    story.append(Paragraph("📋 TOP10 股票详细分析", heading_style))
    story.append(Spacer(1, 10))
    
    top10 = stocks[:10]
    for i, stock in enumerate(top10, 1):
        name = stock.get('name', '')
        ts_code = stock.get('ts_code', '')
        theme = stock.get('theme', '')
        rating = stock.get('rating', '')
        ultimate_score = stock.get('ultimate_score', 0)
        second_wave = stock.get('second_wave_score', 0)
        value_margin = stock.get('value_margin_score', 0)
        
        bias_ma20 = stock.get('bias_ma20', 0)
        bias_ma60 = stock.get('bias_ma60', 0)
        ret_60 = stock.get('ret_60', 0)
        ret_120 = stock.get('ret_120', 0)
        
        stage = stock.get('stage', '')
        market_cap = stock.get('market_cap_yi', 0)
        avg_amount = stock.get('avg_amount_20d_yi', 0)
        
        space, advice, color = calc_space(bias_ma20, bias_ma60, ret_60, stage, ultimate_score, value_margin)
        
        core_reason = stock.get('core_reason', '')[:150]
        risk_factor = stock.get('risk_factor', '')
        
        # 股票标题
        title_text = f"<b>{i}. {name}</b>（{ts_code}）<font color='#{color.hexval()[2:]}'> {rating}级</font> | {theme}"
        story.append(Paragraph(title_text, body_style))
        
        # 核心指标
        metrics = f"""
        终极评分 {ultimate_score:.1f} | 二波潜力 {second_wave:.1f} | 价值余量 {value_margin:.0f}
        MA20乖离 {bias_ma20:+.1f}% | MA60乖离 {bias_ma60:+.1f}% | 60日 +{ret_60:.1f}% | 120日 +{ret_120:.1f}%
        市值 {market_cap:.0f}亿 | 日均成交 {avg_amount:.1f}亿 | {stage}
        """
        story.append(Paragraph(metrics.replace('\n', ' | '), small_style))
        
        # 空间判断
        space_text = f"<font color='#{color.hexval()[2:]}'>● 空间预估：{space} | {advice}</font>"
        story.append(Paragraph(space_text, small_style))
        
        # 风险提示
        if risk_factor:
            story.append(Paragraph(f"<i>⚠️ 风险：{risk_factor[:80]}</i>", small_style))
        
        story.append(Spacer(1, 10))
    
    # ============ 投资建议 ============
    story.append(PageBreak())
    story.append(Paragraph("💡 投资建议总结", heading_style))
    story.append(Spacer(1, 10))
    
    recommendations = """
    <b>一、当前市场特征</b><br/>
    本次扫描20只股票全部来自主板核心股，以PCB电子电路（8只）、先进封装材料（3只）为主线。
    S+级1只（沪电股份），S级11只，A级8只。整体评分较高，但部分股票短期已明显透支。
    <br/><br/>
    
    <b>二、选股策略</b><br/>
    1. <b>首选低乖离</b>：MA20乖离低于10%的股票，如宏昌电子（+2.4%）、江丰电子（+2.2%）、生益电子（+1.6%）
    2. <b>兼顾主题强度</b>：PCB电子电路主题评分88分，是当前最强主线之一<br/>
    3. <b>重视价值余量</b>：价值余量超过85分的股票有光华科技（88.9）、生益电子（88.2）、华天科技（87.5）
    <br/><br/>
    
    <b>三、仓位建议</b><br/>
    - 空间20-30%：可考虑15-20%仓位，回调时加仓<br/>
    - 空间10-20%：10-15%仓位，不追高<br/>
    - 空间5%以下：5%以下仓位或观望，等待大幅回调<br/>
    <br/><br/>
    
    <b>四、风险提示</b><br/>
    1. MA20乖离超过20%的股票（金安国纪+20.6%、京东方A+28.4%）短期有回调风险<br/>
    2. 部分股票净利润增速仅10%，业绩支撑不足<br/>
    3. 波动率普遍在4-5%，注意仓位控制<br/>
    4. 本分析仅供参考，不构成投资建议
    """
    
    story.append(Paragraph(recommendations, body_style))
    
    # 生成PDF
    print("正在生成PDF文件...")
    doc.build(story)
    
    print(f"PDF生成成功: {output_file}")
    print(f"文件大小: {os.path.getsize(output_file)} 字节")


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print("使用方法: python convert_json_with_analysis_to_pdf.py <input_json_file> <output_pdf_file>")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2]
    
    if not os.path.exists(input_file):
        print(f"错误: 输入文件不存在: {input_file}")
        sys.exit(1)
    
    convert_json_to_pdf(input_file, output_file)
