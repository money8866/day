#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将v4股票扫描JSON转换为PDF（包含更准确的上涨空间分析）
修复：空间分析标准收紧，避免85%误判为"空间充足"
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


def calc_space_rating_v2(stock):
    """更准确的上涨空间评级（v2修复版）
    
    核心修改：
    1. 负乖离加成减半（避免85%误判）
    2. MA60乖离惩罚加重
    3. 60日涨幅惩罚阈值降低
    4. 空间分级标准收紧
    """
    bias_ma20 = stock.get('bias_ma20', 0)
    bias_ma60 = stock.get('bias_ma60', 0)
    ret_60 = stock.get('ret_60', 0)
    ret_120 = stock.get('ret_120', 0)
    score_c = stock.get('score_c_value_health', 0)
    score_d = stock.get('score_d_trend', 0)
    ultimate = stock.get('ultimate_score', 0)
    drawdown_120 = stock.get('drawdown_from_high_120', 0)
    stage = stock.get('stage', '')
    limit_up_120 = stock.get('limit_up_count_120', 0)
    
    base = 100
    
    # ========== MA20乖离（核心指标，收紧标准）==========
    if bias_ma20 > 25:
        base -= 70  # 严重透支
    elif bias_ma20 > 20:
        base -= 55  # 明显透支
    elif bias_ma20 > 15:
        base -= 40  # 开始有风险
    elif bias_ma20 > 10:
        base -= 25  # 小幅风险
    elif bias_ma20 > 5:
        base -= 12  # 轻微风险（原-10，微调）
    elif bias_ma20 > 0:
        base -= 5   # 微微上涨（原不惩罚，现在轻微惩罚）
    elif bias_ma20 < -8:
        base += 8   # 严重超跌（原+10，减半）
    elif bias_ma20 < -5:
        base += 5   # 超跌（原+10，减半）
    # -5%~0%：不加分（原+10，现在取消）
    
    # ========== MA60乖离（加重惩罚）==========
    if bias_ma60 > 60:
        base -= 40  # 原-30
    elif bias_ma60 > 50:
        base -= 30  # 原-30
    elif bias_ma60 > 40:
        base -= 20  # 新增
    elif bias_ma60 > 30:
        base -= 12  # 原-15，微调
    elif bias_ma60 > 20:
        base -= 6   # 原-5，加重
    
    # ========== 60日涨幅（降低阈值）==========
    if ret_60 > 120:
        base -= 50  # 原-40
    elif ret_60 > 80:
        base -= 35  # 原-25
    elif ret_60 > 50:
        base -= 18  # 原-10，加重
    elif ret_60 > 30:
        base -= 8   # 新增
    
    # ========== 120日涨幅==========
    if ret_120 > 250:
        base -= 40
    elif ret_120 > 150:
        base -= 25  # 原-15，加重
    elif ret_120 > 100:
        base -= 12  # 原-15，微调
    
    # ========== 价值健康（微调）==========
    if score_c >= 95:
        base += 12  # 原+15
    elif score_c >= 90:
        base += 8   # 原+15，减半
    elif score_c >= 80:
        base += 5   # 原+10，减半
    elif score_c < 70:
        base -= 10  # 新增：价值健康差的要惩罚
    
    # ========== 趋势结构==========
    if score_d >= 95:
        base += 8   # 原+10
    elif score_d >= 90:
        base += 5   # 原+10，减半
    elif score_d >= 80:
        base += 3   # 原+5，减半
    
    # ========== 终极评分==========
    if ultimate >= 92:
        base += 6   # 原+10
    elif ultimate >= 88:
        base += 3   # 原+5，减半
    
    # ========== 从高点回撤（跌多了有反弹空间）==========
    if drawdown_120 < -35:
        base += 12  # 原+10
    elif drawdown_120 < -25:
        base += 6   # 原+5
    elif drawdown_120 < -15:
        base += 3
    
    # ========== 阶段调整==========
    if '健康洗盘' in stage:
        base += 3   # 原+5，减半
    elif '强势整理' in stage:
        base += 1   # 新增：轻微加分
    elif '高位' in stage or '透支' in stage:
        base -= 25  # 原-20，加重
    elif '趋势走弱' in stage:
        base -= 15  # 新增
    
    # ========== 涨停次数（活跃度加分）==========
    if limit_up_120 >= 8:
        base += 5
    elif limit_up_120 >= 5:
        base += 3
    
    base = max(0, min(100, base))
    
    # ========== 转换为空间预估（收紧标准）==========
    if base >= 85:
        return "20-35%", "✅ 空间充足", colors.HexColor('#27AE60'), base
    elif base >= 65:
        return "12-22%", "⚠️ 仍有空间", colors.HexColor('#2ECC71'), base
    elif base >= 45:
        return "5-12%", "⚠️ 小幅空间", colors.HexColor('#F39C12'), base
    elif base >= 25:
        return "0-8%", "❌ 空间有限", colors.HexColor('#E67E22'), base
    else:
        return "风险提示", "❌ 谨慎追高", colors.HexColor('#E74C3C'), base


def convert_v4_to_pdf_v2(input_file, output_file):
    """将v4股票扫描结果转换为PDF（v2修复版）"""
    
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
    story.append(Paragraph(f"📊 主板扫描报告 v4（修复版）", title_style))
    story.append(Paragraph(f"扫描日期: {scan_date} | 股票数量: {total_count} 只", subtitle_style))
    story.append(Paragraph(f"算法: {algorithm}", small_style))
    story.append(Paragraph(f"权重: {weights}", small_style))
    story.append(Paragraph(f"<b>⚠️ 空间分析已修复：标准收紧，避免85%误判</b>", small_style))
    story.append(Spacer(1, 15))
    
    # =========== 空间分布统计 ===========
    space_stats = {"20-35%": 0, "12-22%": 0, "5-12%": 0, "0-8%": 0, "风险提示": 0}
    for stock in stocks[:50]:
        space, _, _, _ = calc_space_rating_v2(stock)
        space_stats[space] = space_stats.get(space, 0) + 1
    
    story.append(Paragraph(f"📈 空间分布（TOP50）：20-35%:{space_stats['20-35%']}只 | 12-22%:{space_stats['12-22%']}只 | 5-12%:{space_stats['5-12%']}只 | 0-8%:{space_stats['0-8%']}只", body_style))
    story.append(Spacer(1, 10))
    
    # =========== 汇总表格（TOP50）===========
    story.append(Paragraph("📋 TOP50 股票汇总", heading_style))
    
    top50 = stocks[:50]
    summary_header = ['#', '代码', '名称', '主题', '评级', '综合', 'A', 'B', 'C', 'D', 'MA20', '60日', '120日', '空间', '阶段']
    summary_data = [summary_header]
    
    for i, stock in enumerate(top50, 1):
        space, advice, color, score = calc_space_rating_v2(stock)
        
        row = [
            str(i),
            stock.get('ts_code', ''),
            stock.get('name', ''),
            stock.get('theme', '')[:6],
            stock.get('rating', ''),
            f"{stock.get('ultimate_score', 0):.1f}",
            f"{stock.get('score_a_core', 0):.0f}",
            f"{stock.get('score_b_recognition', 0):.0f}",
            f"{stock.get('score_c_value_health', 0):.0f}",
            f"{stock.get('score_d_trend', 0):.0f}",
            f"{stock.get('bias_ma20', 0):+.1f}%",
            f"+{stock.get('ret_60', 0):.0f}%",
            f"+{stock.get('ret_120', 0):.0f}%",
            space,
            stock.get('stage', '')[:6]
        ]
        summary_data.append(row)
    
    col_widths = [0.6*cm, 2*cm, 1.8*cm, 2*cm, 0.9*cm, 1.1*cm, 0.8*cm, 0.8*cm, 0.8*cm, 0.8*cm, 1.3*cm, 1.2*cm, 1.2*cm, 1.5*cm, 1.8*cm]
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
    
    # =========== 空间分析 ============
    story.append(Paragraph("📈 上涨空间分析（修复版）", heading_style))
    
    analysis_intro = """
    <b>修复说明：</b>原版空间分析标准过宽，85%股票被误判为"空间充足"。
    修复后：负乖离加成减半、MA60乖离惩罚加重、60日涨幅阈值降低、空间分级标准收紧。<br/>
    <b>分析方法：</b>综合考虑MA20/MA60乖离率、60/120日涨幅、价值健康分、趋势结构分、从高点回撤幅度、阶段等因素。
    """
    story.append(Paragraph(analysis_intro, small_style))
    story.append(Spacer(1, 10))
    
    # 按空间分类
    high_space = []
    medium_space = []
    low_space = []
    minimal_space = []
    
    for stock in stocks[:50]:  # 分析TOP50
        space, advice, color, score = calc_space_rating_v2(stock)
        if "20-35%" in space:
            high_space.append((stock, space, advice, color))
        elif "12-22%" in space:
            medium_space.append((stock, space, advice, color))
        elif "5-12%" in space:
            low_space.append((stock, space, advice, color))
        else:
            minimal_space.append((stock, space, advice, color))
    
    # 第一梯队
    story.append(Paragraph("<b>✅ 第一梯队：空间充足（20-35%，建议重点关注）</b>", heading_style))
    
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
            drawdown = stock.get('drawdown_from_high_120', 0)
            
            text = f"""<b>{name}</b>（{ts_code}）{stage}
            MA20 {bias_ma20:+.1f}% | MA60 {bias_ma60:+.1f}% | 60日 +{ret_60:.1f}% | 120日 +{ret_120:.1f}%
            价值 {score_c:.0f}分 | 趋势 {score_d:.0f}分 | 终极 {ultimate:.1f}分 | 回撤 {drawdown:.1f}% | 空间 <b>{space}</b>"""
            
            story.append(Paragraph(text.replace('\n', ' | '), body_style))
            story.append(Spacer(1, 4))
    else:
        story.append(Paragraph("TOP50中暂无空间充足的股票（标准已收紧）", body_style))
    
    story.append(Spacer(1, 12))
    
    # 第二梯队
    story.append(Paragraph("<b>⚠️ 第二梯队：仍有空间（12-22%）</b>", heading_style))
    
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
    story.append(Paragraph("<b>⚠️ 第三梯队：小幅空间（5-12%）</b>", heading_style))
    
    if low_space:
        for stock, space, advice, color in low_space[:10]:
            name = stock.get('name', '')
            ts_code = stock.get('ts_code', '')
            bias_ma20 = stock.get('bias_ma20', 0)
            ret_60 = stock.get('ret_60', 0)
            
            text = f"""<b>{name}</b>（{ts_code}）| MA20 {bias_ma20:+.1f}% | 60日 +{ret_60:.1f}% | 空间 {space}"""
            
            story.append(Paragraph(text, small_style))
            story.append(Spacer(1, 3))
    
    story.append(PageBreak())
    
    # =========== TOP20详细分析 ============
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
        limit_up = stock.get('limit_up_count_120', 0)
        
        space, advice, color, space_score = calc_space_rating_v2(stock)
        
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
        A基础 {score_a:.1f} | B辨识 {score_b:.1f} | C价值 {score_c:.1f} | D趋势 {score_d:.1f} | <b>终极 {ultimate:.1f}</b>
        MA20乖离 {bias_ma20:+.1f}% | MA60乖离 {bias_ma60:+.1f}% | 60日 +{ret_60:.1f}% | 120日 +{ret_120:.1f}%
        市值 {market_cap:.0f}亿 | 日均 {avg_amount:.1f}亿 | 回撤 {drawdown_120:.1f}% | 涨停{limit_up}次 | {stage}
        """
        story.append(Paragraph(metrics.replace('\n', ' | '), small_style))
        
        # 空间判断
        space_text = f"<font color='{color_hex}'>● 空间预估：{space} | {advice}（评分{space_score:.0f}分）</font>"
        story.append(Paragraph(space_text, small_style))
        
        story.append(Spacer(1, 8))
    
    # =========== 投资建议 ============
    story.append(PageBreak())
    story.append(Paragraph("💡 投资建议总结（修复版）", heading_style))
    
    # 重新统计空间分布
    all_space_stats = {"20-35%": 0, "12-22%": 0, "5-12%": 0, "0-8%": 0, "风险提示": 0}
    for stock in stocks:
        space, _, _, _ = calc_space_rating_v2(stock)
        all_space_stats[space] = all_space_stats.get(space, 0) + 1
    
    total_stocks = len(stocks)
    
    recommendations = f"""
    <b>一、空间分布（全部{total_stocks}只）</b><br/>
    20-35%空间：{all_space_stats['20-35%']}只（{all_space_stats['20-35%']/total_stocks*100:.1f}%）| 
    12-22%空间：{all_space_stats['12-22%']}只（{all_space_stats['12-22%']/total_stocks*100:.1f}%）| <br/>
    5-12%空间：{all_space_stats['5-12%']}只（{all_space_stats['5-12%']/total_stocks*100:.1f}%）| 
    0-8%空间：{all_space_stats['0-8%']}只（{all_space_stats['0-8%']/total_stocks*100:.1f}%）|
    风险提示：{all_space_stats['风险提示']}只<br/><br/>
    
    <b>二、关键发现</b><br/>
    1. <b>空间分布更合理</b>：修复后仅{all_space_stats['20-35%']/total_stocks*100:.1f}%股票被判定为空间充足（原85%）<br/>
    2. <b>小金属板块</b>仍表现突出：锡业股份、华锡有色、金钼股份等均为S+或S级<br/>
    3. <b>PCB电子电路</b>仍是主线：但空间普遍下调，需等待回调<br/>
    4. <b>先进封装</b>开始分化：通富微电、华润微空间有限，需谨慎<br/><br/>
    
    <b>三、重点关注（修复后仍为空间充足）</b><br/>
    """
    
    # 列出空间充足的股票
    high_space_stocks = [s for s in stocks if calc_space_rating_v2(s)[0] == "20-35%"]
    if high_space_stocks:
        stock_list_text = "<br/>".join([
            f"{i+1}. <b>{s['name']}</b>（{s['ts_code']}）| MA20 {s.get('bias_ma20',0):+.1f}% | 60日 +{s.get('ret_60',0):.1f}% | C价值 {s.get('score_c_value_health',0):.0f}分 | {s.get('stage','')}"
            for i, s in enumerate(high_space_stocks[:10])
        ])
        recommendations += stock_list_text + "<br/><br/>"
    
    recommendations += """
    <b>四、风险提示</b><br/>
    1. 修复后多数股票空间下调，说明前期标准过宽，需谨慎追高<br/>
    2. 60日涨幅>50%的股票空间普遍有限，建议等待回调至MA20附近<br/>
    3. 价值健康分（C项）普遍偏高，算法需进一步优化<br/>
    4. 本分析仅供参考，不构成投资建议<br/>
    
    <b>五、修复说明</b><br/>
    - 负乖离加成：-5%~0%不再加分（原+10）<br/>
    - MA60乖离惩罚：>20%即开始惩罚（原>30%）<br/>
    - 60日涨幅惩罚：>30%即开始惩罚（原>50%）<br/>
    - 空间分级标准：20-35%/12-22%/5-12%/0-8% 四档（原25-40%/15-25%/8-15%/0-8%）<br/>
    """
    
    story.append(Paragraph(recommendations, body_style))
    
    # 生成PDF
    print("正在生成PDF文件...")
    doc.build(story)
    
    print(f"PDF生成成功: {output_file}")
    print(f"文件大小: {os.path.getsize(output_file)} 字节")


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print("使用方法: python convert_v4_scan_to_pdf_v2.py <input_json_file> <output_pdf_file>")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2]
    
    if not os.path.exists(input_file):
        print(f"错误: 输入文件不存在: {input_file}")
        sys.exit(1)
    
    convert_v4_to_pdf_v2(input_file, output_file)
