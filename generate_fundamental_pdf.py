#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基本面深度筛选 → PDF报告
侧重行业景气 + 业绩实质性增长，而非技术指标
"""

import json
import os
import platform
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors


def setup_chinese_pdf():
    system = platform.system()
    if system == 'Windows':
        windir = os.environ.get('WINDIR', 'C:\\Windows')
        font_path = os.path.join(windir, 'Fonts', 'msyh.ttc')
        if os.path.exists(font_path):
            pdfmetrics.registerFont(TTFont('MicrosoftYaHei', font_path, subfontIndex=0))
            return 'MicrosoftYaHei'
    return 'Helvetica'


def generate_fundamental_pdf(input_json, output_pdf):
    print(f"读取数据: {input_json}")
    with open(input_json, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    cn_font = setup_chinese_pdf()
    print(f"字体: {cn_font}")
    
    t1 = data.get('T1_data', [])
    t2 = data.get('T2_data', [])
    t3 = data.get('T3_data', [])
    theme_ranking = data.get('theme_ranking', [])
    
    doc = SimpleDocTemplate(
        output_pdf,
        pagesize=landscape(A4),
        leftMargin=1.2*cm, rightMargin=1.2*cm,
        topMargin=1.2*cm, bottomMargin=1.2*cm,
    )
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('Title', fontName=cn_font, fontSize=16, alignment=TA_CENTER, spaceAfter=6)
    subtitle_style = ParagraphStyle('Subtitle', fontName=cn_font, fontSize=10, alignment=TA_CENTER, spaceAfter=8)
    h2_style = ParagraphStyle('H2', fontName=cn_font, fontSize=12, spaceAfter=6, spaceBefore=10, textColor=colors.HexColor('#2C3E50'))
    body_style = ParagraphStyle('Body', fontName=cn_font, fontSize=9, leading=12)
    small_style = ParagraphStyle('Small', fontName=cn_font, fontSize=8, leading=10)
    tag_style = ParagraphStyle('Tag', fontName=cn_font, fontSize=8, leading=10, leftIndent=15)
    
    story = []
    
    # ===== 标题 =====
    story.append(Paragraph("📊 主板基本面深度筛选报告", title_style))
    story.append(Paragraph("筛选标准：行业景气 + 业绩实质性增长（非技术指标） | 扫描日期：2026-06-18", subtitle_style))
    story.append(Paragraph(f"T1优质成长: {len(t1)}只 | T2稳健成长: {len(t2)}只 | T3业绩回暖: {len(t3)}只", subtitle_style))
    story.append(Spacer(1, 10))
    
    # ===== 行业景气度 =====
    story.append(Paragraph("🔥 行业景气度排名", h2_style))
    
    theme_header = ['排名', '行业', '景气度', '个股数', '净利润增速中位数', '营收增速中位数']
    theme_data = [theme_header]
    
    prosperity_colors = {
        '🔥高景气': colors.HexColor('#E74C3C'),
        '📈景气上行': colors.HexColor('#F39C12'),
        '➡️平稳': colors.HexColor('#95A5A6'),
        '📉景气下行': colors.HexColor('#3498DB'),
        '❌困境': colors.HexColor('#2C3E50'),
    }
    
    for i, t in enumerate(theme_ranking[:15], 1):
        np_m = f"+{t['np_yoy_median']:.0f}%" if t.get('np_yoy_median') else 'N/A'
        rev_m = f"+{t['rev_yoy_median']:.0f}%" if t.get('rev_yoy_median') else 'N/A'
        theme_data.append([str(i), t['theme'], t['prosperity'], str(t['count']), np_m, rev_m])
    
    theme_table = Table(theme_data, colWidths=[1*cm, 4*cm, 2.5*cm, 1.5*cm, 4*cm, 4*cm])
    theme_ts = [
        ('FONTNAME', (0,0), (-1,-1), cn_font),
        ('FONTSIZE', (0,0), (-1,0), 9),
        ('FONTSIZE', (0,1), (-1,-1), 8),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#2E4057')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ]
    # 景气度颜色
    for i, t in enumerate(theme_ranking[:15], 1):
        p = t['prosperity']
        if p in prosperity_colors:
            theme_ts.append(('TEXTCOLOR', (2, i), (2, i), prosperity_colors[p]))
    
    for i in range(1, len(theme_ranking[:15])+1):
        if i % 2 == 0:
            theme_ts.append(('BACKGROUND', (0,i), (-1,i), colors.HexColor('#F8F8F8')))
    
    theme_table.setStyle(TableStyle(theme_ts))
    story.append(theme_table)
    story.append(Spacer(1, 15))
    
    # ===== T2 稳健成长表格 =====
    story.append(Paragraph("📈 T2 稳健成长股（净利润增速>15%, 营收增速>5%, ROE>5%, 排除一次性收益）", h2_style))
    
    if t2:
        t2_header = ['#', '名称', '代码', '主题', '景气度', '净利增速', '营收增速', 'ROE', 'PE', '毛利率', 'v4评分', '评级', '阶段']
        t2_table_data = [t2_header]
        
        for i, r in enumerate(t2[:30], 1):
            pe_str = f"{r['pe']:.0f}" if r.get('pe') else '-'
            roe_str = f"{r['roe_waa']:.1f}%" if r.get('roe_waa') else '-'
            gm_str = f"{r['gross_margin']:.1f}%" if r.get('gross_margin') else '-'
            
            t2_table_data.append([
                str(i),
                r['name'],
                r['ts_code'][:6],
                r.get('theme', '')[:6],
                r.get('theme_prosperity', ''),
                f"+{r['np_yoy']:.0f}%",
                f"+{r['rev_yoy']:.0f}%",
                roe_str,
                pe_str,
                gm_str,
                f"{r['ultimate_score']:.1f}",
                r['rating'],
                r.get('stage', '')[:4],
            ])
        
        t2_table = Table(t2_table_data, colWidths=[0.6*cm, 1.8*cm, 1.5*cm, 1.8*cm, 1.6*cm, 1.8*cm, 1.8*cm, 1.5*cm, 1.2*cm, 1.5*cm, 1.2*cm, 0.8*cm, 1.5*cm])
        t2_ts = [
            ('FONTNAME', (0,0), (-1,-1), cn_font),
            ('FONTSIZE', (0,0), (-1,0), 7),
            ('FONTSIZE', (0,1), (-1,-1), 7),
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#27AE60')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('GRID', (0,0), (-1,-1), 0.4, colors.grey),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('TOPPADDING', (0,0), (-1,-1), 2),
            ('BOTTOMPADDING', (0,0), (-1,-1), 2),
        ]
        for i in range(1, min(len(t2), 30)+1):
            if i % 2 == 0:
                t2_ts.append(('BACKGROUND', (0,i), (-1,i), colors.HexColor('#F0FFF0')))
        t2_table.setStyle(TableStyle(t2_ts))
        story.append(t2_table)
    
    story.append(PageBreak())
    
    # ===== 重点个股详细分析 =====
    story.append(Paragraph("🔍 重点个股深度分析", h2_style))
    story.append(Spacer(1, 6))
    
    # 选最有价值的15只详细展开
    top_stocks = t2[:15]
    
    for i, r in enumerate(top_stocks, 1):
        name = r['name']
        code = r['ts_code']
        theme = r.get('theme', '')
        prosperity = r.get('theme_prosperity', '')
        np_yoy = r.get('np_yoy', 0)
        rev_yoy = r.get('rev_yoy', 0)
        roe = r.get('roe_waa') or r.get('roe')
        pe = r.get('pe')
        pb = r.get('pb')
        gross_margin = r.get('gross_margin')
        net_margin = r.get('net_margin')
        market_cap = r.get('market_cap_yi', 0)
        stage = r.get('stage', '')
        bias_ma20 = r.get('bias_ma20', 0)
        ret_60 = r.get('ret_60', 0)
        
        # 业绩质量评级
        quality = ''
        quality_color = colors.black
        if np_yoy > 100 and rev_yoy > 50 and roe and roe > 15:
            quality = '⭐⭐⭐ 顶级成长'
            quality_color = colors.HexColor('#E74C3C')
        elif np_yoy > 50 and rev_yoy > 20:
            quality = '⭐⭐ 高成长'
            quality_color = colors.HexColor('#F39C12')
        elif np_yoy > 20 and rev_yoy > 10:
            quality = '⭐ 稳健成长'
            quality_color = colors.HexColor('#27AE60')
        else:
            quality = '成长一般'
            quality_color = colors.HexColor('#95A5A6')
        
        # 判断业绩驱动力
        driver = ''
        if rev_yoy and np_yoy and rev_yoy > 0:
            if np_yoy / max(rev_yoy, 1) > 3:
                driver = '利润率提升型（营收增长+费用率下降）'
            elif gross_margin and gross_margin > 40:
                driver = '高毛利科技型'
            elif rev_yoy > 50:
                driver = '规模扩张型（营收高速增长）'
            else:
                driver = '稳健增长型'
        
        q_hex = '#{:02X}{:02X}{:02X}'.format(int(quality_color.red*255), int(quality_color.green*255), int(quality_color.blue*255))
        
        title = f"<b>{i}. {name}</b>（{code}）| {theme} | {prosperity}"
        story.append(Paragraph(title, body_style))
        
        metrics_parts = [
            f"净利润增速: <b>+{np_yoy:.0f}%</b>",
            f"营收增速: +{rev_yoy:.0f}%",
        ]
        if roe: metrics_parts.append(f"ROE: {roe:.1f}%")
        if pe: metrics_parts.append(f"PE: {pe:.0f}")
        if pb: metrics_parts.append(f"PB: {pb:.1f}")
        if gross_margin: metrics_parts.append(f"毛利率: {gross_margin:.1f}%")
        if net_margin: metrics_parts.append(f"净利率: {net_margin:.1f}%")
        metrics_parts.append(f"市值: {market_cap:.0f}亿")
        
        story.append(Paragraph(' | '.join(metrics_parts), small_style))
        
        # 业绩质量 + 驱动力
        story.append(Paragraph(f"<font color='{q_hex}'>业绩质量: {quality}</font> |驱动力: {driver} | 技术面: MA20乖离{bias_ma20:+.1f}% · 60日+{ret_60:.0f}% · {stage}", small_style))
        
        story.append(Spacer(1, 8))
    
    # ===== 投资策略总结 =====
    story.append(PageBreak())
    story.append(Paragraph("💡 投资策略总结", h2_style))
    
    # 统计
    hi_growth = [r for r in t2 if r.get('np_yoy', 0) > 100]
    mid_growth = [r for r in t2 if 30 < r.get('np_yoy', 0) <= 100]
    low_growth = [r for r in t2 if r.get('np_yoy', 0) <= 30]
    
    strategy = f"""
    <b>一、筛选体系变革</b><br/>
    本次筛选完全替代了原v4的"价值健康"指标（基于乖离率的技术指标），改用Tushare财报数据驱动的真正基本面分析。<br/><br/>
    
    <b>筛选标准</b>：<br/>
    - 净利润同比增速 >15%（要求业绩实质性增长）<br/>
    - 营收同比增速 >5%（排除一次性收益/资产处置）<br/>
    - ROE >5%（排除低效增长）<br/>
    - 排除一次性收益嫌疑（环比暴跌>80%或净利/营收增速严重背离）<br/>
    - 行业景气度标注（同行业公司业绩集体改善=景气上行）<br/><br/>
    
    <b>二、业绩增长分布</b><br/>
    - 超高增长（净利>100%）：{len(hi_growth)}只<br/>
    - 中高速增长（净利30-100%）：{len(mid_growth)}只<br/>
    - 稳健增长（净利15-30%）：{len(low_growth)}只<br/><br/>
    
    <b>三、核心发现</b><br/>
    1. <b>IC设计/存储芯片</b>：行业净利润增速中位数+300%+，景气度最高，但PE普遍>100，估值昂贵<br/>
    2. <b>固态电池</b>：净利润增速中位数+300%，营收增速中位数+77%，量价齐升<br/>
    3. <b>AI算力基建</b>：净利润增速中位数+234%，但个股分化大<br/>
    4. <b>PCB电子电路</b>：净利润增速中位数+63%，营收+38%，确定性最强的景气主线<br/>
    5. <b>小金属</b>：净利润增速中位数+74%，锡/钨/钼涨价驱动<br/><br/>
    
    <b>四、选股策略</b><br/>
    <b>A. 确定性优先（推荐）</b>：选业绩增速30-100%、PE<60、行业高景气的股票<br/>
"""
    
    # 找出确定性最高的
    certain = [r for r in t2 if 30 < r.get('np_yoy', 0) < 200 and r.get('pe') and r['pe'] < 60 and '🔥' in r.get('theme_prosperity', '')]
    if certain:
        for r in certain[:5]:
            strategy += f"    - <b>{r['name']}</b>（{r['ts_code']}）净利+{r['np_yoy']:.0f}% · 营收+{r['rev_yoy']:.0f}% · PE={r['pe']:.0f} · ROE={r.get('roe_waa',0):.1f}% · {r['theme']}<br/>"
    
    strategy += f"""
    <br/><b>B. 弹性优先（高风险高回报）</b>：选业绩爆发>200%但PE也很高的股票，需等待回调<br/>
"""
    explosive = [r for r in t2 if r.get('np_yoy', 0) > 200]
    if explosive:
        for r in explosive[:5]:
            pe_str = f"PE={r['pe']:.0f}" if r.get('pe') else 'PE=N/A'
            strategy += f"    - <b>{r['name']}</b>（{r['ts_code']}）净利+{r['np_yoy']:.0f}% · 营收+{r['rev_yoy']:.0f}% · {pe_str} · {r['theme']}<br/>"
    
    strategy += """
    <br/><b>五、风险提示</b><br/>
    1. 超高增长（>500%）多为基数效应，不可持续<br/>
    2. PE>100的股票，业绩一旦不及预期跌幅巨大<br/>
    3. 季报数据有滞后性，最新经营情况可能与财报不同<br/>
    4. 行业景气度判断基于有限样本，可能偏差<br/>
    5. 本报告不构成投资建议<br/>
    """
    
    story.append(Paragraph(strategy, body_style))
    
    # 生成
    print("正在生成PDF...")
    doc.build(story)
    print(f"PDF生成成功: {output_pdf}")
    print(f"文件大小: {os.path.getsize(output_pdf)} 字节")


if __name__ == '__main__':
    input_json = r'D:\mystock\solo\report_daily\fundamental_screen_20260618.json'
    output_pdf = r'D:\mystock\solo\report_daily\fundamental_screen_20260618.pdf'
    generate_fundamental_pdf(input_json, output_pdf)
