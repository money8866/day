#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全市场基本面筛选 → PDF报告（机构增量视角）
"""

import json
import os
import platform
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors


def setup_chinese_pdf():
    windir = os.environ.get('WINDIR', 'C:\\Windows')
    font_path = os.path.join(windir, 'Fonts', 'msyh.ttc')
    if os.path.exists(font_path):
        pdfmetrics.registerFont(TTFont('MicrosoftYaHei', font_path, subfontIndex=0))
        return 'MicrosoftYaHei'
    return 'Helvetica'


def generate_pdf(input_json, output_pdf):
    with open(input_json, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    cn = setup_chinese_pdf()
    
    ia = data.get('IA_data', [])
    ib = data.get('IB_data', [])
    ic = data.get('IC_data', [])
    theme_ranking = data.get('theme_ranking', [])
    
    doc = SimpleDocTemplate(
        output_pdf, pagesize=landscape(A4),
        leftMargin=1.2*cm, rightMargin=1.2*cm,
        topMargin=1.2*cm, bottomMargin=1.2*cm,
    )
    
    styles = getSampleStyleSheet()
    title_s = ParagraphStyle('T', fontName=cn, fontSize=16, alignment=TA_CENTER, spaceAfter=4)
    sub_s = ParagraphStyle('Sub', fontName=cn, fontSize=9, alignment=TA_CENTER, spaceAfter=8)
    h2_s = ParagraphStyle('H2', fontName=cn, fontSize=12, spaceAfter=6, spaceBefore=10, textColor=colors.HexColor('#1A5276'))
    body_s = ParagraphStyle('Body', fontName=cn, fontSize=9, leading=13, alignment=TA_JUSTIFY)
    small_s = ParagraphStyle('Sm', fontName=cn, fontSize=7.5, leading=10)
    
    story = []
    
    # 标题
    story.append(Paragraph("📊 全市场基本面筛选报告（机构增量视角）", title_s))
    story.append(Paragraph("口径：v4扫描全部569只主板股票 | 季报期：2026Q1 | 筛选日：2026-06-18", sub_s))
    story.append(Paragraph(f"IA机构核心池: {len(ia)}只 | IB机构观察池: {len(ib)}只 | IC机构跟踪池: {len(ic)}只", sub_s))
    story.append(Spacer(1, 8))
    
    # ===== 行业景气度 =====
    story.append(Paragraph("🔥 行业景气度排名（机构配置视角：景气度+板块容量）", h2_s))
    
    th = ['#', '行业', '景气度', '个股数', '净利增速中位数', '营收增速中位数', '板块容量', '均市值(亿)', '均成交(亿)']
    tdata = [th]
    
    for t in theme_ranking[:20]:
        np_m = f"+{t['np_yoy_median']:.0f}%" if t.get('np_yoy_median') is not None else '-'
        rev_m = f"+{t['rev_yoy_median']:.0f}%" if t.get('rev_yoy_median') is not None else '-'
        tdata.append([
            str(t['ranking']), t['theme'], t['prosperity'], str(t['count']),
            np_m, rev_m, t.get('capacity', ''), 
            f"{t.get('avg_cap',0):.0f}", f"{t.get('avg_amount',0):.1f}"
        ])
    
    t = Table(tdata, colWidths=[0.7*cm, 3.8*cm, 2.2*cm, 1.2*cm, 3*cm, 3*cm, 1.8*cm, 1.8*cm, 1.8*cm])
    ts = [
        ('FONTNAME', (0,0), (-1,-1), cn),
        ('FONTSIZE', (0,0), (-1,-1), 7.5),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1A5276')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('GRID', (0,0), (-1,-1), 0.4, colors.grey),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 2),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
    ]
    # 景气度颜色
    p_colors = {
        '🔥高景气': colors.HexColor('#E74C3C'),
        '📈景气上行': colors.HexColor('#F39C12'),
        '➡️平稳': colors.HexColor('#95A5A6'),
        '📉景气下行': colors.HexColor('#3498DB'),
        '❌困境': colors.HexColor('#2C3E50'),
    }
    for i, tr in enumerate(theme_ranking[:20], 1):
        p = tr['prosperity']
        if p in p_colors:
            ts.append(('TEXTCOLOR', (2,i), (2,i), p_colors[p]))
        cap = tr.get('capacity', '')
        if '大' in cap:
            ts.append(('TEXTCOLOR', (6,i), (6,i), colors.HexColor('#27AE60')))
        elif '小' in cap:
            ts.append(('TEXTCOLOR', (6,i), (6,i), colors.HexColor('#E74C3C')))
        if i % 2 == 0:
            ts.append(('BACKGROUND', (0,i), (-1,i), colors.HexColor('#F5F5F5')))
    
    t.setStyle(TableStyle(ts))
    story.append(t)
    story.append(Spacer(1, 12))
    
    # ===== IA核心池 =====
    story.append(Paragraph("🏆 IA 机构核心池（确定性+流动性+景气度+估值合理）", h2_s))
    story.append(Paragraph("筛选标准：高景气行业 + 净利>30% + 营收>15% + ROE>8% + 市值≥100亿 + 日均成交≥3亿 + 排除一次性收益", small_s))
    story.append(Spacer(1, 4))
    
    if ia:
        ia_h = ['机构分', '名称', '代码', '行业', '景气', '容量', '净利增速', '营收增速', 'ROE', 'PE', 'PEG', '市值', '成交', '阶段']
        ia_d = [ia_h]
        for r in ia:
            pe = f"{r['pe']:.0f}" if r.get('pe') else '-'
            peg = f"{r['peg']:.1f}" if r.get('peg') else '-'
            roe = f"{r['roe_waa']:.1f}%" if r.get('roe_waa') else '-'
            ia_d.append([
                f"{r['inst_personal_score']:.0f}",
                r['name'], r['ts_code'][:6],
                r.get('theme','')[:6], r.get('theme_prosperity',''), r.get('theme_capacity',''),
                f"+{r['np_yoy']:.0f}%", f"+{r['rev_yoy']:.0f}%", roe, pe, peg,
                f"{r['market_cap_yi']:.0f}亿", f"{r['avg_amount_20d_yi']:.1f}亿",
                r.get('stage','')[:4],
            ])
        
        ia_t = Table(ia_d, colWidths=[1.1*cm, 1.6*cm, 1.2*cm, 1.6*cm, 1.5*cm, 1.2*cm, 1.6*cm, 1.6*cm, 1.2*cm, 1*cm, 1*cm, 1.4*cm, 1.4*cm, 1.4*cm])
        ia_ts = [
            ('FONTNAME', (0,0), (-1,-1), cn),
            ('FONTSIZE', (0,0), (-1,-1), 7),
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#E74C3C')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('GRID', (0,0), (-1,-1), 0.4, colors.grey),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('TOPPADDING', (0,0), (-1,-1), 2),
            ('BOTTOMPADDING', (0,0), (-1,-1), 2),
        ]
        ia_t.setStyle(TableStyle(ia_ts))
        story.append(ia_t)
    
    story.append(PageBreak())
    
    # ===== IB观察池 =====
    story.append(Paragraph("🔭 IB 机构观察池（景气上行+业绩增长）", h2_s))
    story.append(Paragraph("筛选标准：景气行业 + 净利>15% + 营收>5% + 排除一次性收益", small_s))
    story.append(Spacer(1, 4))
    
    if ib:
        ib_h = ['名称', '代码', '行业', '景气', '净利增速', '营收增速', 'PE', '市值', '成交', '阶段']
        ib_d = [ib_h]
        for r in ib[:40]:
            pe = f"{r['pe']:.0f}" if r.get('pe') else '-'
            ib_d.append([
                r['name'], r['ts_code'][:6], r.get('theme','')[:6],
                r.get('theme_prosperity',''),
                f"+{r['np_yoy']:.0f}%", f"+{r['rev_yoy']:.0f}%", pe,
                f"{r['market_cap_yi']:.0f}亿", f"{r['avg_amount_20d_yi']:.1f}亿",
                r.get('stage','')[:4],
            ])
        
        ib_t = Table(ib_d, colWidths=[1.6*cm, 1.2*cm, 1.8*cm, 1.8*cm, 1.8*cm, 1.8*cm, 1*cm, 1.4*cm, 1.4*cm, 1.4*cm])
        ib_ts = [
            ('FONTNAME', (0,0), (-1,-1), cn),
            ('FONTSIZE', (0,0), (-1,-1), 7),
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#F39C12')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('GRID', (0,0), (-1,-1), 0.4, colors.grey),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('TOPPADDING', (0,0), (-1,-1), 2),
            ('BOTTOMPADDING', (0,0), (-1,-1), 2),
        ]
        for i in range(1, min(len(ib),40)+1):
            if i % 2 == 0:
                ib_ts.append(('BACKGROUND', (0,i), (-1,i), colors.HexColor('#FFF8E1')))
        ib_t.setStyle(TableStyle(ib_ts))
        story.append(ib_t)
    
    story.append(PageBreak())
    
    # ===== IA核心池个股深度 =====
    story.append(Paragraph("🔍 IA核心池个股深度分析", h2_s))
    
    for i, r in enumerate(ia, 1):
        name = r['name']
        code = r['ts_code']
        theme = r.get('theme', '')
        prosperity = r.get('theme_prosperity', '')
        capacity = r.get('theme_capacity', '')
        np_yoy = r.get('np_yoy', 0)
        rev_yoy = r.get('rev_yoy', 0)
        roe = r.get('roe_waa')
        pe = r.get('pe')
        peg = r.get('peg')
        gm = r.get('gross_margin')
        nm = r.get('net_margin')
        dv = r.get('dv_ratio')
        cap = r.get('market_cap_yi', 0)
        amt = r.get('avg_amount_20d_yi', 0)
        stage = r.get('stage', '')
        bias = r.get('bias_ma20', 0)
        ret60 = r.get('ret_60', 0)
        consecutive = r.get('consecutive_growth', False)
        roe_stable = r.get('roe_stable')
        
        # 机构关注点
        inst_points = []
        if peg and peg < 1:
            inst_points.append("PEG<1，估值显著低于增速")
        elif peg and peg < 2:
            inst_points.append("PEG<2，估值增速匹配")
        if roe and roe > 15:
            inst_points.append("ROE>15%，盈利能力强")
        if consecutive:
            inst_points.append("营收连续增长，业绩确定性高")
        if roe_stable:
            inst_points.append("ROE稳定，经营质量好")
        if dv and dv > 2:
            inst_points.append(f"股息率{dv:.1f}%，有分红回报")
        if cap > 500:
            inst_points.append(f"市值{cap:.0f}亿，机构可大额配置")
        if amt > 20:
            inst_points.append(f"日均成交{amt:.0f}亿，流动性充裕")
        
        # 增长类型
        if rev_yoy > 50 and np_yoy > rev_yoy * 2:
            growth_type = "规模扩张+利润率提升型（最理想）"
        elif rev_yoy > 30:
            growth_type = "规模扩张型"
        elif gm and gm > 45:
            growth_type = "高毛利科技型"
        else:
            growth_type = "稳健增长型"
        
        title_text = f"<b>{i}. {name}</b>（{code}）| {theme} | {prosperity} | {capacity}"
        story.append(Paragraph(title_text, ParagraphStyle('D', fontName=cn, fontSize=9, leading=12, spaceBefore=6)))
        
        metrics = f"净利+{np_yoy:.0f}% | 营收+{rev_yoy:.0f}% | ROE={roe:.1f}%" if roe else f"净利+{np_yoy:.0f}% | 营收+{rev_yoy:.0f}%"
        if pe: metrics += f" | PE={pe:.0f}"
        if peg: metrics += f" | PEG={peg:.1f}"
        if gm: metrics += f" | 毛利率{gm:.1f}%"
        if nm: metrics += f" | 净利率{nm:.1f}%"
        if dv: metrics += f" | 股息{dv:.1f}%"
        story.append(Paragraph(metrics, small_s))
        
        story.append(Paragraph(f"增长模型: {growth_type} | 技术面: MA20乖离{bias:+.1f}% · 60日+{ret60:.0f}% · {stage}", small_s))
        
        if inst_points:
            story.append(Paragraph(f"🔑 机构关注: {' | '.join(inst_points)}", small_s))
        
        story.append(Spacer(1, 6))
    
    # ===== 策略 =====
    story.append(PageBreak())
    story.append(Paragraph("💡 机构增量配置策略", h2_s))
    
    # 找出确定性标的
    certain = [r for r in ia if r.get('pe') and r['pe'] < 80 and r.get('np_yoy', 0) < 500]
    low_peg = [r for r in ia if r.get('peg') and r['peg'] < 2]
    
    strategy = f"""
<b>一、筛选体系</b><br/>
本次筛选覆盖v4扫描全部569只股票，独立于技术面评分，纯基本面维度筛选。<br/>
机构增量资金的核心逻辑：业绩确定性 > 弹性，行业景气共振 > 个股独立行情，估值合理性 > 绝对低估。<br/><br/>

<b>二、行业配置优先级</b><br/>
"""
    
    # 按机构景气度排行业
    for t in theme_ranking[:8]:
        strategy += f"· {t['prosperity']} <b>{t['theme']}</b>（{t['count']}只）：净利中位+{t['np_yoy_median']:.0f}%"
        if t.get('rev_yoy_median'):
            strategy += f"，营收中位+{t['rev_yoy_median']:.0f}%"
        strategy += f"，{t.get('capacity','')}，均市值{t.get('avg_cap',0):.0f}亿<br/>"
    
    strategy += f"""
<br/><b>三、IA核心池配置建议（6只）</b><br/>
"""
    if certain:
        strategy += "<b>确定性优先：</b><br/>"
        for r in certain:
            strategy += f"· <b>{r['name']}</b>（{r['ts_code'][:6]}）：净利+{r['np_yoy']:.0f}% · 营收+{r['rev_yoy']:.0f}% · PE={r['pe']:.0f} · PEG={r.get('peg','N/A')} · {r['theme']}<br/>"
    
    if low_peg and len(low_peg) > len(certain):
        strategy += "<b>增速-估值匹配优先：</b><br/>"
        for r in low_peg:
            if r not in certain:
                strategy += f"· <b>{r['name']}</b>（{r['ts_code'][:6]}）：净利+{r['np_yoy']:.0f}% · PEG={r['peg']:.1f} · {r['theme']}<br/>"
    
    strategy += """
<br/><b>四、机构增量路径</b><br/>
1. 先配行业：高景气+大容量（存储芯片、PCB、先进封装、小金属）<br/>
2. 再选个股：PEG<2优先、ROE>10%优选、连续增长加分<br/>
3. 仓位节奏：MA20乖离<-3%时建仓，MA20上方5%以上不加仓<br/>
4. 风控底线：单只≤15%仓位，行业≤30%仓位<br/><br/>

<b>五、与150只筛选对比</b><br/>
"""
    
    strategy += f"""
· 原150只筛选只有3只通过严口径，24只通过宽口径<br/>
· 全市场569只筛选IA核心池6只，IB观察池63只<br/>
· 核心差异：全市场筛选发现了<strong>广合科技</strong>（PCB，净利+63%营收+71%量价齐升），这是150只版本里没有的最高质量机构增量标的<br/>
· 松发股份在150只版本因PE=54被T1排除，全市场加入机构流通性考量后入选IA<br/><br/>

<b>六、风险提示</b><br/>
· 超高增速（>500%）多为基数效应，Q2/Q3同比会大幅回落<br/>
· PE>100的股票，一个季度不及预期就可能跌30%+<br/>
· 季报数据滞后2个月，当前经营情况可能与Q1不同<br/>
· 行业景气判断基于有限样本，可能偏差<br/>
· 本报告不构成投资建议<br/>
"""
    
    story.append(Paragraph(strategy, body_s))
    
    doc.build(story)
    print(f"PDF生成成功: {output_pdf}")
    print(f"文件大小: {os.path.getsize(output_pdf)} 字节")


if __name__ == '__main__':
    input_json = r'D:\mystock\solo\report_daily\fundamental_screen_full_20260618.json'
    output_pdf = r'D:\mystock\solo\report_daily\fundamental_screen_full_20260618.pdf'
    generate_pdf(input_json, output_pdf)
