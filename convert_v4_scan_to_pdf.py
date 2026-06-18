#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将v4股票扫描JSON转换为PDF（包含详细分析和上涨空间评级）
算法：v4 - 中军基础(A) + 历史辨识度(B) + 价值健康(C) + 趋势结构(D)
"""

import sys
import os
import json
import platform
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


def calc_space_rating(stock):
    """计算上涨空间评级（基于v4算法）"""
    bias_ma20 = stock.get('bias_ma20', 0)
    bias_ma60 = stock.get('bias_ma60', 0)
    ret_60 = stock.get('ret_60', 0)
    ret_120 = stock.get('ret_120', 0)
    score_c = stock.get('score_c_value_health', 0)
    score_d = stock.get('score_d_trend', 0)
    ultimate = stock.get('ultimate_score', 0)
    drawdown_120 = stock.get('drawdown_from_high_120', 0)
    stage = stock.get('stage', '')
    
    base = 100
    
    # MA20乖离惩罚（核心指标）
    if bias_ma20 > 20:
        base -= 60
    elif bias_ma20 > 15:
        base -= 40
    elif bias_ma20 > 10:
        base -= 25
    elif bias_ma20 > 5:
        base -= 10
    elif bias_ma20 < -5:
        base += 10  # 负乖离是好事
    
    # MA60乖离惩罚
    if bias_ma60 > 50:
        base -= 30
    elif bias_ma60 > 30:
        base -= 15
    elif bias_ma60 > 20:
        base -= 5
    
    # 60日涨幅惩罚
    if ret_60 > 150:
        base -= 40
    elif ret_60 > 100:
        base -= 25
    elif ret_60 > 50:
        base -= 10
    
    # 120日涨幅惩罚
    if ret_120 > 200:
        base -= 30
    elif ret_120 > 100:
        base -= 15
    
    # 价值健康加分
    if score_c >= 90:
        base += 15
    elif score_c >= 80:
        base += 10
    
    # 趋势结构加分
    if score_d >= 90:
        base += 10
    elif score_d >= 80:
        base += 5
    
    # 终极评分加成
    if ultimate >= 90:
        base += 10
    elif ultimate >= 85:
        base += 5
    
    # 从高点回撤幅度（跌多了有反弹空间）
    if drawdown_120 < -30:
        base += 10
    elif drawdown_120 < -20:
        base += 5
    
    # 阶段调整
    if '健康洗盘' in stage:
        base += 5  # 洗盘后更有空间
    elif '高位' in stage or '透支' in stage:
        base -= 20
    
    base = max(0, min(100, base))
    
    # 转换为空间估算
    if base >= 80:
        return "25-40%", "✅ 空间充足", colors.HexColor('#27AE60'), base
    elif base >= 60:
        return "15-25%", "⚠️ 仍有空间", colors.HexColor('#2ECC71'), base
    elif base >= 40:
        return "8-15%", "⚠️ 小幅空间", colors.HexColor('#F39C12'), base
    else:
        return "0-8%", "❌ 空间有限", colors.HexColor('#E74C3C'), base


def convert_v4_to_pdf(input_file, output_file):
    """将v4股票扫描结果转换为PDF"""
    
    print(f"正在读取文件: {input_file}")
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    scan_date = data.get('scan_date', 'N/A')
    algorithm = data.get('algorithm', '')
    weights = data.get('weights', '')
    total_count = data.get('total_count', 0)
    stocks = data.get('data', [])
    
    print(f"扫描日期: {scan_date}")
    print(f"算法: {algorithm}")
    print(f"权重: {weights}")
    print(f"股票数量: {total_count}")
    
    cn_font = setup_chinese_pdf()
    print(f"使用字体: {cn_font}")
    
    print(f"正在创建PDF: {output_file}")
    doc = SimpleDocTemplate(
        output_file,
        pagesize=landscape(A4),
        leftMargin=1.2*cm,
        rightMargin=1.2*cm,
        topMargin=1.2*cm,
        bottomMargin=1.2*cm,
    )
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('Title', fontName=cn_font, fontSize=16, alignment=TA_CENTER, spaceAfter=6)
    subtitle_style = ParagraphStyle('Subtitle', fontName=cn_font, fontSize=10, alignment=TA_CENTER, spaceAfter=10)
    heading_style = ParagraphStyle('Heading', fontName=cn_font, fontSize=12, spaceAfter=6, spaceBefore=8)
    body_style = ParagraphStyle('Body', fontName=cn_font, fontSize=9, leading=11)
    small_style = ParagraphStyle('Small', fontName=cn_font, fontSize=8, leading=10)
    
    story = []
    
    # 标题页
    story.append(Paragraph(f"📊 主板扫描报告 v4", title_style))
    story.append(Paragraph(f"扫描日期: {scan_date} | 股票数量: {total_count} 只", subtitle_style))
    story.append(Paragraph(f"算法: {algorithm}", small_style))
    story.append(Paragraph(f"权重: {weights}", small_style))
    story.append(Spacer(1, 15))
    
    # ============ 汇总表格（TOP50）===========
    story.append(Paragraph("📋 TOP50 股票汇总", heading_style))
    
    top50 = stocks[:50]
    summary_header = ['#', '代码', '名称', '主题', '评级', '综合', 'A基础', 'B辨识', 'C价值', 'D趋势', 'MA20乖离', '60日涨幅', '120日涨幅', '空间', '阶段']
    summary_data = [summary_header]
    
    for i, stock in enumerate(top50, 1):
        space, advice, color, score = calc_space_rating(stock)
        
        row = [
            str(i),
            stock.get('ts_code', ''),
            stock.get('name', ''),
            stock.get('theme', '')[:6],
            stock.get('rating', ''),
            f"{stock.get('ultimate_score', 0):.1f}",
            f"{stock.get('score_a_core', 0):.1f}",
            f"{stock.get('score_b_recognition', 0):.1f}",
            f"{stock.get('score_c_value_health', 0):.1f}",
            f"{stock.get('score_d_trend', 0):.1f}",
            f"{stock.get('bias_ma20', 0):+.1f}%",
            f"+{stock.get('ret_60', 0):.1f}%",
            f"+{stock.get('ret_120', 0):.1f}%",
            space,
            stock.get('stage', '')[:6]
        ]
        summary_data.append(row)
    
    col_widths = [0.6*cm, 2*cm, 1.8*cm, 2*cm, 0.9*cm, 1.1*cm, 1.1*cm, 1.1*cm, 1.1*cm, 1.1*cm, 1.5*cm, 1.5*cm, 1.5*cm, 1.5*cm, 1.8*cm]
    table = Table(summary_data, colWidths=col_widths)
    
    table_style = [
        ('FONTNAME', (0, 0), (-1, -1), cn_font),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('FONTSIZE', (0, 1), (-1, -1), 7),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2E4057')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]
    
    # 评级颜色
    for i, stock in enumerate(top50, 1):
        rating = stock.get('rating', '')
        if rating == 'S+':
            table_style.append(('BACKGROUND', (4, i), (4, i), colors.HexColor('#E74C3C')))
            table_style.append(('TEXTCOLOR', (4, i), (4, i), colors.white))
        elif rating == 'S':
            table_style.append(('BACKGROUND', (4, i), (4, i), colors.HexColor('#F39C12')))
            table_style.append(('TEXTCOLOR', (4, i), (4, i), colors.white))
    
    # 隔行变色
    for i in range(1, len(top50) + 1):
        if i % 2 == 0:
            table_style.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#F8F8F8')))
    
    table.setStyle(TableStyle(table_style))
    story.append(table)
    story.append(PageBreak())
    
    # ============ 空间分析 ============
    story.append(Paragraph("📈 上涨空间分析", heading_style))
    
    analysis_intro = """
    <b>分析方法：</b>综合考虑MA20乖离率、MA60乖离率、60/120日涨幅、价值健康分、趋势结构分、从高点回撤幅度等因素，
    计算上涨空间评级。负乖离或低乖离（<5%）代表尚未启动，上涨空间更大；高乖离（>15%）代表短期已透支。
    """
    story.append(Paragraph(analysis_intro, small_style))
    story.append(Spacer(1, 10))
    
    # 按空间分类
    high_space = []
    medium_space = []
    low_space = []
    
    for stock in stocks[:50]:  # 分析TOP50
        space, advice, color, score = calc_space_rating(stock)
        if "25-40%" in space:
            high_space.append((stock, space, advice, color))
        elif "15-25%" in space:
            medium_space.append((stock, space, advice, color))
        else:
            low_space.append((stock, space, advice, color))
    
    # 第一梯队
    story.append(Paragraph("<b>🏆 第一梯队：空间充足（25-40%，建议重点关注）</b>", heading_style))
    
    if high_space:
        for stock, space, advice, color in high_space[:10]:
            name = stock.get('name', '')
            ts_code = stock.get('ts_code', '')
            bias_ma20 = stock.get('bias_ma20', 0)
            bias_ma60 = stock.get('bias_ma60', 0)
            ret_60 = stock.get('ret_60', 0)
            ret_120 = stock.get('ret_120', 0)
            score_c = stock.get('score_c_value_health', 0)
            score_d = stock.get('score_d_trend', 0)
            ultimate = stock.get('ultimate_score', 0)
            stage = stock.get('stage', '')
            
            text = f"""<b>{name}</b>（{ts_code}）{stage}
            MA20 {bias_ma20:+.1f}% | MA60 {bias_ma60:+.1f}% | 60日 +{ret_60:.1f}% | 120日 +{ret_120:.1f}%
            价值健康 {score_c:.0f}分 | 趋势结构 {score_d:.0f}分 | 终极 {ultimate:.1f}分 | 空间 <b>{space}</b>"""
            
            story.append(Paragraph(text.replace('\n', ' | '), body_style))
            story.append(Spacer(1, 4))
    else:
        story.append(Paragraph("TOP50中暂无空间充足的股票", body_style))
    
    story.append(Spacer(1, 12))
    
    # 第二梯队
    story.append(Paragraph("<b>⚠️ 第二梯队：仍有空间（15-25%）</b>", heading_style))
    
    if medium_space:
        for stock, space, advice, color in medium_space[:15]:
            name = stock.get('name', '')
            ts_code = stock.get('ts_code', '')
            bias_ma20 = stock.get('bias_ma20', 0)
            ret_60 = stock.get('ret_60', 0)
            stage = stock.get('stage', '')
            ultimate = stock.get('ultimate_score', 0)
            
            text = f"""<b>{name}</b>（{ts_code}）{stage} | MA20 {bias_ma20:+.1f}% | 60日 +{ret_60:.1f}% | 终极 {ultimate:.1f}分 | 空间 {space}"""
            
            story.append(Paragraph(text, small_style))
            story.append(Spacer(1, 3))
    else:
        story.append(Paragraph("暂无", body_style))
    
    story.append(Spacer(1, 12))
    
    # 第三梯队
    story.append(Paragraph("<b>🔻 第三梯队：空间有限（0-15%）</b>", heading_style))
    
    if low_space:
        for stock, space, advice, color in low_space[:10]:
            name = stock.get('name', '')
            ts_code = stock.get('ts_code', '')
            bias_ma20 = stock.get('bias_ma20', 0)
            ret_60 = stock.get('ret_60', 0)
            stage = stock.get('stage', '')
            
            text = f"""<b>{name}</b>（{ts_code}）{stage} | MA20 {bias_ma20:+.1f}% | 60日 +{ret_60:.1f}% | 空间 {space}"""
            
            story.append(Paragraph(text, small_style))
            story.append(Spacer(1, 3))
    else:
        story.append(Paragraph("暂无", body_style))
    
    story.append(PageBreak())
    
    # ============ TOP20详细分析 ============
    story.append(Paragraph("📋 TOP20 股票详细分析", heading_style))
    story.append(Spacer(1, 8))
    
    for i, stock in enumerate(stocks[:20], 1):
        name = stock.get('name', '')
        ts_code = stock.get('ts_code', '')
        theme = stock.get('theme', '')
        rating = stock.get('rating', '')
        
        score_a = stock.get('score_a_core', 0)
        score_b = stock.get('score_b_recognition', 0)
        score_c = stock.get('score_c_value_health', 0)
        score_d = stock.get('score_d_trend', 0)
        ultimate = stock.get('ultimate_score', 0)
        
        bias_ma20 = stock.get('bias_ma20', 0)
        bias_ma60 = stock.get('bias_ma60', 0)
        ret_60 = stock.get('ret_60', 0)
        ret_120 = stock.get('ret_120', 0)
        
        stage = stock.get('stage', '')
        market_cap = stock.get('market_cap_yi', 0)
        avg_amount = stock.get('avg_amount_20d_yi', 0)
        drawdown_120 = stock.get('drawdown_from_high_120', 0)
        
        space, advice, color, space_score = calc_space_rating(stock)
        
        # 颜色代码
        color_hex = '#{:02X}{:02X}{:02X}'.format(
            int(color.red * 255),
            int(color.green * 255),
            int(color.blue * 255)
        )
        
        # 股票标题
        title_text = f"<b>{i}. {name}</b>（{ts_code}）<font color='{color_hex}'> {rating}级</font> | {theme}"
        story.append(Paragraph(title_text, body_style))
        
        # 评分详情
        metrics = f"""
        A中军基础 {score_a:.1f} | B历史辨识 {score_b:.1f} | C价值健康 {score_c:.1f} | D趋势结构 {score_d:.1f} | <b>终极 {ultimate:.1f}</b>
        MA20乖离 {bias_ma20:+.1f}% | MA60乖离 {bias_ma60:+.1f}% | 60日 +{ret_60:.1f}% | 120日 +{ret_120:.1f}%
        市值 {market_cap:.0f}亿 | 日均 {avg_amount:.1f}亿 | 从高点回撤 {drawdown_120:.1f}% | {stage}
        """
        story.append(Paragraph(metrics.replace('\n', ' | '), small_style))
        
        # 空间判断
        space_text = f"<font color='{color_hex}'>● 空间预估：{space} | {advice}</font>"
        story.append(Paragraph(space_text, small_style))
        
        story.append(Spacer(1, 8))
    
    # ============ 主题分布 ============
    story.append(PageBreak())
    story.append(Paragraph("💡 投资建议总结", heading_style))
    
    # 统计主题分布
    themes = {}
    for stock in stocks[:50]:
        theme = stock.get('theme', '其他')
        if theme not in themes:
            themes[theme] = []
        themes[theme].append(stock)
    
    # 按数量排序
    sorted_themes = sorted(themes.items(), key=lambda x: len(x[1]), reverse=True)
    
    theme_text = "<br/>".join([f"<b>{t}</b>（{len(stock_list)}只）：{', '.join([s['name'] for s in stock_list[:5]])}" 
                               for t, stock_list in sorted_themes[:10]])
    
    recommendations = f"""
    <b>一、TOP50主题分布</b><br/>
    {theme_text}
    <br/><br/>
    
    <b>二、关键发现</b><br/>
    1. <b>小金属板块</b>表现突出：锡业股份、华锡有色、金钼股份等均为S+或S级<br/>
    2. <b>先进封装/半导体材料</b>持续强势：通富微电、华润微、江丰电子等<br/>
    3. <b>PCB电子电路</b>仍是主线：兴森科技、深南电路、生益电子等<br/>
    4. <b>AI终端/光通信</b>开始发力：环旭电子、太辰光、三安光电等<br/><br/>
    
    <b>三、选股策略</b><br/>
    1. <b>首选低乖离</b>：MA20乖离低于5%的股票，上涨空间更大<br/>
    2. <b>重视价值健康分</b>：C项得分85以上的股票更有安全边际<br/>
    3. <b>关注健康洗盘阶段</b>：经过充分整理的股票爆发力更强<br/>
    4. <b>注意回撤幅度</b>：从高点回撤20-30%的股票反弹空间更大<br/><br/>
    
    <b>四、重点关注</b><br/>
    - 锡业股份（000960）：S+级，MA20-1.08%，价值健康97分，完全未启动<br/>
    - 环旭电子（601231）：S+级，AI终端主线，强势整理中<br/>
    - 华锡有色（600301）：S+级，小金属龙头，120日仅涨61%<br/>
    - 协鑫能科（002015）：S级，MA20仅+0.23%，几乎贴着均线<br/><br/>
    
    <b>五、风险提示</b><br/>
    1. 部分股票60日涨幅已超过50%，注意追高风险<br/>
    2. 波动率普遍在4-5%，需控制仓位<br/>
    3. 本分析仅供参考，不构成投资建议
    """
    
    story.append(Paragraph(recommendations, body_style))
    
    # 生成PDF
    print("正在生成PDF文件...")
    doc.build(story)
    
    print(f"PDF生成成功: {output_file}")
    print(f"文件大小: {os.path.getsize(output_file)} 字节")


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print("使用方法: python convert_v4_scan_to_pdf.py <input_json_file> <output_pdf_file>")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2]
    
    if not os.path.exists(input_file):
        print(f"错误: 输入文件不存在: {input_file}")
        sys.exit(1)
    
    convert_v4_to_pdf(input_file, output_file)
