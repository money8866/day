#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
半年报预测系统 — PDF报告生成
"""

import json
import os
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors


def setup_chinese():
    windir = os.environ.get('WINDIR', 'C:\\Windows')
    for fname in ['msyh.ttc', 'simhei.ttf', 'simsun.ttc']:
        fp = os.path.join(windir, 'Fonts', fname)
        if os.path.exists(fp):
            pdfmetrics.registerFont(TTFont('CN', fp, subfontIndex=0))
            return 'CN'
    return 'Helvetica'


def generate_pdf(input_json, output_pdf):
    with open(input_json, 'r', encoding='utf-8') as f:
        data = json.load(f)
    predictions = data['predictions']
    cn = setup_chinese()
    
    doc = SimpleDocTemplate(output_pdf, pagesize=landscape(A4),
                            leftMargin=1.2*cm, rightMargin=1.2*cm, topMargin=1.2*cm, bottomMargin=1.2*cm)
    styles = getSampleStyleSheet()
    title_s = ParagraphStyle('T', fontName=cn, fontSize=16, alignment=TA_CENTER, spaceAfter=4)
    sub_s = ParagraphStyle('Sub', fontName=cn, fontSize=9, alignment=TA_CENTER, spaceAfter=8)
    h2_s = ParagraphStyle('H2', fontName=cn, fontSize=12, spaceAfter=6, spaceBefore=10, textColor=colors.HexColor('#1A5276'))
    h3_s = ParagraphStyle('H3', fontName=cn, fontSize=10, spaceAfter=4, spaceBefore=8, textColor=colors.HexColor('#2E86C1'))
    body_s = ParagraphStyle('Body', fontName=cn, fontSize=9, leading=13, alignment=TA_JUSTIFY)
    small_s = ParagraphStyle('Sm', fontName=cn, fontSize=8, leading=11)
    warn_s = ParagraphStyle('Warn', fontName=cn, fontSize=8, leading=11, textColor=colors.HexColor('#E74C3C'))
    
    story = []
    
    story.append(Paragraph("📊 半年报预测报告（因子引擎v1）", title_s))
    story.append(Paragraph("预测目标: 2026H1中报 | 数据截止: 2026-06-19 | 覆盖: IA核心池6只", sub_s))
    story.append(Spacer(1, 6))
    
    # ===== 预测汇总表 =====
    story.append(Paragraph("📋 预测结果汇总", h2_s))
    
    th = ['名称', '代码', '行业', 'Q1营收(亿)', 'Q1净利(亿)', 'H1营收预测', 'H1营收同比', 'H1净利预测', 'H1净利同比', '置信度', '现金流质量']
    tdata = [th]
    
    for code, p in predictions.items():
        hp = p.get('h1_predict', {})
        f = p['factors']
        h1r = hp.get('h1_rev', {})
        h1n = hp.get('h1_ni', {})
        h1ry = hp.get('h1_rev_yoy', {})
        h1ny = hp.get('h1_ni_yoy', {})
        
        h1_rev_str = f"{h1r.get('low',0):.1f}~{h1r.get('high',0):.1f}" if h1r else '-'
        h1_rev_yoy_str = f"+{h1ry.get('low',0):.0f}%~+{h1ry.get('high',0):.0f}%" if h1ry and h1ry.get('mid') else '-'
        h1_ni_str = f"{h1n.get('low',0):.1f}~{h1n.get('high',0):.1f}" if h1n else '-'
        
        # 净利同比太大时用更简洁的显示
        ni_yoy_mid = h1ny.get('mid')
        if ni_yoy_mid and ni_yoy_mid > 10000:
            h1_ni_yoy_str = f"扭亏/暴增"
        elif ni_yoy_mid:
            h1_ni_yoy_str = f"+{h1ny.get('low',0):.0f}%~+{h1ny.get('high',0):.0f}%"
        else:
            h1_ni_yoy_str = '-'
        
        ocf_quality = f.get('A7_quality', '-')
        
        tdata.append([
            p['name'], p['code'][:6], p['theme'][:6],
            f"{p['q1_rev_yi']:.1f}", f"{p['q1_ni_yi']:.2f}",
            h1_rev_str, h1_rev_yoy_str, h1_ni_str, h1_ni_yoy_str,
            p['confidence'], ocf_quality[:6] if ocf_quality else '-',
        ])
    
    t = Table(tdata, colWidths=[1.6*cm, 1.2*cm, 1.5*cm, 1.4*cm, 1.4*cm, 2.5*cm, 2.8*cm, 2.2*cm, 2.5*cm, 1.2*cm, 2.2*cm])
    ts = [
        ('FONTNAME', (0,0), (-1,-1), cn),
        ('FONTSIZE', (0,0), (-1,-1), 7),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1A5276')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('GRID', (0,0), (-1,-1), 0.4, colors.grey),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 2),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
    ]
    for i in range(1, len(tdata)):
        ocf_q = predictions[list(predictions.keys())[i-1]]['factors'].get('A7_quality', '')
        if '负' in str(ocf_q):
            ts.append(('TEXTCOLOR', (10,i), (10,i), colors.HexColor('#E74C3C')))
        elif '高' in str(ocf_q):
            ts.append(('TEXTCOLOR', (10,i), (10,i), colors.HexColor('#27AE60')))
        if i % 2 == 0:
            ts.append(('BACKGROUND', (0,i), (-1,i), colors.HexColor('#F5F5F5')))
    t.setStyle(TableStyle(ts))
    story.append(t)
    story.append(Spacer(1, 8))
    
    story.append(PageBreak())
    
    # ===== 逐只详细分析 =====
    for code, p in predictions.items():
        name = p['name']
        f = p['factors']
        hp = p.get('h1_predict', {})
        
        story.append(Paragraph(f"{'='*80}", small_s))
        story.append(Paragraph(f"🔍 {name}（{code}）| {p['theme']}", h2_s))
        
        # 基本信息行
        info_text = f"Q1已知: 营收{p['q1_rev_yi']}亿（同比+{p['q1_rev_yoy']}%）| 净利{p['q1_ni_yi']}亿（同比+{p['q1_ni_yoy']}%）"
        story.append(Paragraph(info_text, body_s))
        
        h1_2025_rev = hp.get('h1_2025_rev', 0)
        h1_2025_ni = hp.get('h1_2025_ni', 0)
        story.append(Paragraph(f"2025H1基准: 营收{h1_2025_rev:.2f}亿 | 净利{h1_2025_ni:.2f}亿", body_s))
        
        # 因子分析
        story.append(Paragraph("因子分析:", h3_s))
        
        # A1 季节性
        q1_ratio = f.get('A1_q1_h1_ratio_rev', 0)
        story.append(Paragraph(f"  A1 季节性外推: Q1/H1营收比={q1_ratio}（历史均值） → H1营收≈{p['q1_rev_yi']}/{q1_ratio}={f.get('A1_seasonal_h1_rev',0):.1f}亿", small_s))
        
        # A2 环比趋势
        momentum = f.get('A2_momentum', '未知')
        qoq_rev = f.get('A2_qoq_rev', 0)
        qoq_ni = f.get('A2_qoq_ni', 0)
        story.append(Paragraph(f"  A2 环比趋势: Q1环比Q4 营收{qoq_rev}% / 净利{qoq_ni}% → 趋势判断: {momentum}", small_s))
        
        # A3 行业景气
        peer = f.get('A3_vs_peers', '')
        median = f.get('A3_peer_np_median', 0)
        story.append(Paragraph(f"  A3 行业景气: 板块净利增速中位+{median:.0f}% → 本公司: {peer}", small_s))
        
        # A5 基数效应
        base = f.get('A5_base_effect', '')
        if base:
            h1_2025_rev_yoy = f.get('A5_2025h1_rev_yoy', 0)
            h1_2025_ni_yoy = f.get('A5_2025h1_ni_yoy', 0)
            story.append(Paragraph(f"  A5 基数效应: 2025H1营收同比{h1_2025_rev_yoy}% / 净利同比{h1_2025_ni_yoy}% → {base}", small_s))
        else:
            story.append(Paragraph(f"  A5 基数效应: 无2024H1可比数据（次新股或数据缺失）", small_s))
        
        # A7 现金流
        ocf = f.get('A7_q1_ocf', 0)
        ocf_ratio = f.get('A7_ocf_ni_ratio', 0)
        quality = f.get('A7_quality', '')
        ocf_color = ''
        if '负' in quality: ocf_color = ' ⚠️'
        elif '低' in quality: ocf_color = ' ⚠️'
        story.append(Paragraph(f"  A7 现金流: Q1经营现金流{ocf}亿, OCF/净利={ocf_ratio} → {quality}{ocf_color}", small_s))
        
        # 预测结论
        story.append(Paragraph("预测结论:", h3_s))
        h1r = hp.get('h1_rev', {})
        h1n = hp.get('h1_ni', {})
        h1ry = hp.get('h1_rev_yoy', {})
        h1ny = hp.get('h1_ni_yoy', {})
        
        story.append(Paragraph(f"  H1营收: {h1r.get('low',0):.1f}~{h1r.get('high',0):.1f}亿（中值{h1r.get('mid',0):.1f}亿）", body_s))
        if h1ry.get('mid'):
            story.append(Paragraph(f"  H1营收同比: +{h1ry.get('low',0):.0f}%~+{h1ry.get('high',0):.0f}%（中值+{h1ry.get('mid',0):.0f}%）", body_s))
        
        story.append(Paragraph(f"  H1净利: {h1n.get('low',0):.1f}~{h1n.get('high',0):.1f}亿（中值{h1n.get('mid',0):.1f}亿）", body_s))
        ni_mid = h1ny.get('mid')
        if ni_mid and abs(ni_mid) < 10000:
            story.append(Paragraph(f"  H1净利同比: +{h1ny.get('low',0):.0f}%~+{h1ny.get('high',0):.0f}%（中值+{ni_mid:.0f}%）", body_s))
        elif ni_mid:
            story.append(Paragraph(f"  H1净利同比: 扭亏为盈或暴增（基数效应所致）", body_s))
        
        story.append(Paragraph(f"  置信度: {p['confidence']}（{p.get('confidence_pct',0)*100:.0f}%因子填充率）", body_s))
        
        # 风险提示
        warnings = []
        if '负' in quality:
            warnings.append(f"⚠️ Q1经营现金流为负({ocf}亿)，利润含金量存疑")
        if momentum == '减速':
            warnings.append(f"⚠️ Q1环比Q4营收下滑{abs(qoq_rev)}%，Q2可能继续减速")
        if ni_mid and ni_mid > 500:
            warnings.append(f"⚠️ 净利增速+{ni_mid:.0f}%主要由低基数效应驱动，Q2同比大概率大幅回落")
        if base and '高基数' in base:
            warnings.append(f"⚠️ 2025H1已高增，2026H1继续高增难度大")
        peer_np = f.get('A3_peer_np_median', 0)
        if peer_np and p['q1_ni_yoy'] > peer_np * 2:
            warnings.append(f"⚠️ 净利增速远超板块中位(+{peer_np:.0f}%)，可能有不可持续因素")
        
        if warnings:
            story.append(Paragraph("风险提示:", warn_s))
            for w in warnings:
                story.append(Paragraph(f"  {w}", warn_s))
        
        story.append(Spacer(1, 8))
    
    # ===== 方法论说明 =====
    story.append(PageBreak())
    story.append(Paragraph("📐 预测方法论", h2_s))
    
    method_text = """
<b>因子引擎v1 — 基于已知Q1财报 + 历史季节性 + 多因子修正</b><br/><br/>

<b>A1 季节性外推因子（权重40%）</b><br/>
原理：多数公司Q1占H1营收的比例相对稳定（通常40-50%），用Q1实际营收除以历史均值推算H1。<br/>
数据：近5年Q1/H1比例均值<br/>
局限：不适用于季节性波动大的行业（如军工、新能源Q4集中交付）<br/><br/>

<b>A2 环比趋势因子（权重25%）</b><br/>
原理：Q1环比Q4的变动方向和幅度，反映当前经营动量。<br/>
加速(+30%+) → Q2调整系数+10%<br/>
稳定(+10~30%) → Q2调整系数+5%<br/>
平稳(-10~+10%) → 不调整<br/>
减速(-30%+) → Q2调整系数-10%<br/><br/>

<b>A3 行业景气度因子（权重15%）</b><br/>
原理：同一板块公司业绩高度相关，板块中位数增速可作为个股增速的锚。<br/>
远超板块(2x+) → alpha型，估值有溢价但增速可能回落<br/>
略超板块(1.2x+) → 跟随+alpha<br/>
跟随板块(<1.2x) → beta型<br/><br/>

<b>A5 基数效应因子（定性修正）</b><br/>
原理：如果2025H1基数异常（大亏/大增），2026H1同比会被严重扭曲。<br/>
强低基数效应(2025H1营收同比<-20%) → 2026H1同比高增但实际改善有限<br/>
高基数效应(2025H1营收同比>+20%) → 2026H1继续高增难度大<br/><br/>

<b>A7 现金流先行因子（定性验证）</b><br/>
原理：经营性现金流/净利润 > 0.8 说明利润含金量高（真实现金流入）。<br/>
高(>0.8) → 利润扎实<br/>
中(0.5-0.8) → 有一定应收/库存压力<br/>
低(<0.5) → 应收账款暴增或库存积压<br/>
负 → 警惕（可能是渠道压货而非真实需求）<br/><br/>

<b>预测区间</b><br/>
中值预测 ±15% 形成上下界。置信度取决于因子填充率。<br/><br/>

<b>下一步优化方向</b><br/>
1. 加入价格传导因子（A4）：产品价格指数 - 原材料价格指数 → 毛利率预测<br/>
2. 加入管理层指引因子（A6）：从机构调研纪要中提取Q2经营指引<br/>
3. 加入订单管线因子：在手订单/上年营收 → 收入先行指标<br/>
4. 月度跟踪更新：每月用最新月度经营数据修正预测<br/>
5. AI定性融合：让DeepSeek/Kimi读取调研纪要给出方向性修正
    """
    story.append(Paragraph(method_text, body_s))
    
    doc.build(story)
    print(f"PDF生成成功: {output_pdf}")
    print(f"大小: {os.path.getsize(output_pdf)} 字节")


if __name__ == '__main__':
    generate_pdf(
        r'D:\mystock\solo\report_daily\h1_predict_v1_20260619.json',
        r'D:\mystock\solo\report_daily\h1_predict_v1_20260619.pdf'
    )
