# -*- coding: utf-8 -*-
"""
BullScore 中长线牛股报告生成器
生成专业PDF投资报告 — 适配主线归因 + 产业β框架
"""
import sys
import os
from datetime import datetime
from pathlib import Path

# Add vendor path for reportlab
vendor_path = Path(__file__).parent / 'vendor'
if vendor_path.exists():
    sys.path.insert(0, str(vendor_path))

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

import pandas as pd

# ── 字体注册 ──
FONT_REGISTERED = False
try:
    font_paths = [
        r'C:\Windows\Fonts\msyh.ttc',
        r'C:\Windows\Fonts\SimHei.ttf',
        r'C:\Windows\Fonts\simsun.ttc',
    ]
    for fp in font_paths:
        if os.path.exists(fp):
            if fp.endswith('.ttf'):
                pdfmetrics.registerFont(TTFont('Chinese', fp))
            else:
                pdfmetrics.registerFont(TTFont('Chinese', fp, subfontIndex=0))
            FONT_REGISTERED = True
            print(f"Registered font: {fp}")
            break
except Exception as e:
    print(f"Font registration failed: {e}")

if not FONT_REGISTERED:
    print("Warning: Chinese font not registered, will use Helvetica")


# ============================================================
# 数据加载与预处理
# ============================================================
def load_data(csv_path):
    """加载 BullScore 结果 CSV"""
    df = pd.read_csv(csv_path, dtype={'ts_code': str})  # 确保代码列保持字符串格式
    df.columns = df.columns.str.strip()

    # 数值列转 float
    score_cols = ['industry_demand_score', 'tech_barrier_score', 'order_explosion_score',
                  'earnings_quality_score', 'leader_score', 'expectation_score',
                  'institution_score', 'marketcap_score', 'bull_score', 'theme_score', 'final_score']
    for c in score_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)

    # 百分比字符串转数值（roe, gross_margin, rd_ratio, revenue_yoy, profit_yoy 等）
    pct_cols = ['roe', 'gross_margin', 'rd_ratio', 'revenue_yoy', 'profit_yoy', 'contract_liability_yoy']
    for c in pct_cols:
        if c in df.columns:
            df[c] = df[c].astype(str).str.rstrip('%')
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)

    return df


def tier_classify(df):
    """按 bull_level 分层"""
    a = df[df['bull_level'] == 'A级产业龙头'].copy()
    b = df[df['bull_level'] == 'B级成长股'].copy()
    return a, b


def industry_analysis(df):
    """行业分布分析"""
    if len(df) == 0:
        return pd.DataFrame()
    industry_count = df.groupby('industry').agg({
        'ts_code': 'count',
        'final_score': 'mean',
        'roe': 'mean',
        'rd_ratio': 'mean',
    }).round(2)
    industry_count.columns = ['股票数', '平均BullScore', '平均ROE%', '平均研发%']
    industry_count = industry_count.sort_values('平均BullScore', ascending=False)
    return industry_count


def theme_analysis(df):
    """主题分布分析"""
    if len(df) == 0:
        return pd.DataFrame()
    theme_count = df.groupby('theme').agg({
        'ts_code': 'count',
        'final_score': 'mean',
        'expectation_score': 'mean',
        'order_explosion_score': 'mean',
    }).round(2)
    theme_count.columns = ['股票数', '平均分', '预期差分', '订单爆发分']
    theme_count = theme_count.sort_values('平均分', ascending=False)
    return theme_count


# ============================================================
# 样式定义
# ============================================================
def get_styles():
    """获取样式定义"""
    font_name = 'Chinese' if FONT_REGISTERED else 'Helvetica'

    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle(name='CoverTitle', fontName=font_name, fontSize=32, leading=40,
                               alignment=TA_CENTER, textColor=colors.HexColor('#1a1a2e'), spaceAfter=20))
    styles.add(ParagraphStyle(name='CoverSubtitle', fontName=font_name, fontSize=18, leading=24,
                               alignment=TA_CENTER, textColor=colors.HexColor('#4a4a6a'), spaceAfter=10))
    styles.add(ParagraphStyle(name='CoverDate', fontName=font_name, fontSize=14,
                               alignment=TA_CENTER, textColor=colors.HexColor('#888888'), spaceAfter=6))
    styles.add(ParagraphStyle(name='ChapterTitle', fontName=font_name, fontSize=20, leading=26,
                               alignment=TA_LEFT, textColor=colors.HexColor('#1a1a2e'), spaceBefore=20, spaceAfter=12))
    styles.add(ParagraphStyle(name='SectionTitle', fontName=font_name, fontSize=14, leading=20,
                               alignment=TA_LEFT, textColor=colors.HexColor('#2d5a87'), spaceBefore=16, spaceAfter=8))
    styles.add(ParagraphStyle(name='SubTitle', fontName=font_name, fontSize=12, leading=16,
                               alignment=TA_LEFT, textColor=colors.HexColor('#4a4a6a'), spaceBefore=10, spaceAfter=6))
    styles.add(ParagraphStyle(name='ReportBody', fontName=font_name, fontSize=10, leading=16,
                               alignment=TA_JUSTIFY, textColor=colors.HexColor('#333333'), spaceAfter=8))
    styles.add(ParagraphStyle(name='TableHeader', fontName=font_name, fontSize=9, leading=12,
                               alignment=TA_CENTER, textColor=colors.white))
    styles.add(ParagraphStyle(name='TableCell', fontName=font_name, fontSize=8, leading=11,
                               alignment=TA_CENTER, textColor=colors.HexColor('#333333')))
    styles.add(ParagraphStyle(name='TableCellLeft', fontName=font_name, fontSize=8, leading=11,
                               alignment=TA_LEFT, textColor=colors.HexColor('#333333')))
    styles.add(ParagraphStyle(name='KeyMetric', fontName=font_name, fontSize=28, leading=34,
                               alignment=TA_CENTER, textColor=colors.HexColor('#2d5a87')))
    styles.add(ParagraphStyle(name='KeyMetricLabel', fontName=font_name, fontSize=10, leading=14,
                               alignment=TA_CENTER, textColor=colors.HexColor('#888888')))
    styles.add(ParagraphStyle(name='Footer', fontName=font_name, fontSize=8,
                               alignment=TA_CENTER, textColor=colors.HexColor('#888888')))
    return styles


# ============================================================
# 封面
# ============================================================
def build_cover(df, styles):
    """构建封面"""
    elements = []
    font_name = 'Chinese' if FONT_REGISTERED else 'Helvetica'

    elements.append(Spacer(1, 80))
    elements.append(Paragraph('BullScore 中长线牛股投资报告', styles['CoverTitle']))
    elements.append(Spacer(1, 20))
    elements.append(Paragraph('基于产业景气 + 订单验证 + 龙头地位 + 业绩质量 + 预期差 框架', styles['CoverSubtitle']))
    elements.append(Spacer(1, 40))
    elements.append(HRFlowable(width='80%', thickness=2, color=colors.HexColor('#2d5a87')))
    elements.append(Spacer(1, 40))

    a_df, b_df = tier_classify(df)
    report_date = datetime.now().strftime('%Y-%m-%d')

    key_metrics = [
        (f'{len(df)}', '入选标的'),
        (f'全部含S/A/B级', '等级范围'),
        (f'{df["final_score"].mean():.1f}', '平均FinalScore'),
        (report_date, '报告日期'),
    ]
    metrics_data = []
    for _, (val, label) in enumerate(key_metrics):
        cell_html = f'<b>{val}</b><br/><font size="9" color="#888888">{label}</font>'
        metrics_data.append(Paragraph(cell_html, ParagraphStyle(
            'MetricCell', fontName=font_name, fontSize=24, leading=34,
            alignment=TA_CENTER, textColor=colors.HexColor('#2d5a87'))))

    metrics_table = Table([metrics_data], colWidths=[4.5*cm]*4)
    metrics_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 12),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
    ]))
    elements.append(metrics_table)
    elements.append(Spacer(1, 60))

    elements.append(Paragraph('<b>选股模型</b>', styles['SectionTitle']))
    elements.append(Spacer(1, 10))

    model_desc = """
    本报告基于 BullScore 中长线牛股评分系统，对A股全市场股票进行多维度综合评估。
    评分框架围绕产业景气(25%)、技术壁垒(15%)、订单爆发(15%)、业绩质量(15%)、
    龙头地位(10%)、预期差(10%)、机构认可(5%)、市值弹性(5%) 八个核心因子，
    叠加 ThemeScore 主题加成(20%)，筛选具备1~3年 200% 上涨空间的中长线牛股。
    """
    elements.append(Paragraph(model_desc.strip(), styles['ReportBody']))
    elements.append(Spacer(1, 30))
    elements.append(HRFlowable(width='80%', thickness=1, color=colors.HexColor('#cccccc')))
    elements.append(Spacer(1, 10))
    elements.append(Paragraph(
        '<i>免责声明：本报告仅供参考，不构成投资建议。</i>',
        ParagraphStyle('Disclaimer', fontName=font_name, fontSize=8, alignment=TA_CENTER,
                       textColor=colors.HexColor('#888888'))))
    elements.append(PageBreak())
    return elements


# ============================================================
# 执行摘要
# ============================================================
def build_executive_summary(df, a_df, b_df, thm_dist, styles):
    """构建执行摘要"""
    elements = []
    font_name = 'Chinese' if FONT_REGISTERED else 'Helvetica'

    elements.append(Paragraph('一、执行摘要', styles['ChapterTitle']))
    elements.append(HRFlowable(width='100%', thickness=1, color=colors.HexColor('#2d5a87')))
    elements.append(Spacer(1, 15))

    elements.append(Paragraph('1.1 核心结论', styles['SectionTitle']))

    total = len(df)
    a_cnt = len(a_df)
    b_cnt = len(b_df)
    conclusion_text = f"""
    本次 BullScore 全市场扫描共筛选出<b>{total}只</b>符合入选标准的标的，
    其中<b>A级产业龙头{a_cnt}只</b>、<b>B级成长股{b_cnt}只</b>。
    模型遵循八因子行业内分位数评分原则，不使用固定阈值或简单二元判断，
    确保不同行业之间的评分可比性。
    """
    elements.append(Paragraph(conclusion_text.strip(), styles['ReportBody']))

    # 入选情况表
    a_mean = a_df['final_score'].mean() if len(a_df) > 0 else 0
    b_mean = b_df['final_score'].mean() if len(b_df) > 0 else 0
    summary_data = [
        ['等级', '数量', '平均FinalScore', '平均ROE%', '平均研发%', '特征描述'],
        ['A级产业龙头', str(a_cnt), f'{a_mean:.1f}',
         f'{a_df["roe"].mean():.1f}%' if len(a_df) > 0 else '-',
         f'{a_df["rd_ratio"].mean():.1f}%' if len(a_df) > 0 else '-',
         '高产业景气+强订单验证+技术壁垒突出'],
        ['B级成长股', str(b_cnt), f'{b_mean:.1f}',
         f'{b_df["roe"].mean():.1f}%' if len(b_df) > 0 else '-',
         f'{b_df["rd_ratio"].mean():.1f}%' if len(b_df) > 0 else '-',
         '高成长性+预期差较大+资金关注度提升'],
    ]
    summary_table = Table(summary_data, colWidths=[3*cm, 1.5*cm, 2.5*cm, 2*cm, 2*cm, 6.5*cm])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2d5a87')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, -1), font_name),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f5f7fa')]),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 15))

    # 主题分布
    elements.append(Paragraph('1.2 主题/产业链分布', styles['SectionTitle']))
    if not thm_dist.empty:
        ind_text = f"""
        主题分布方面，<b>{thm_dist.index[0]}</b>板块入选数量最多且平均分最高，
        体现了当前AI/科技产业趋势驱动的市场主线特征。
        """
        elements.append(Paragraph(ind_text.strip(), styles['ReportBody']))

        thm_data = [['主题', '股票数', '平均分', '预期差分', '订单爆发分']]
        for theme, row in thm_dist.head(6).iterrows():
            thm_data.append([theme, str(int(row['股票数'])),
                             f'{row["平均分"]:.1f}', f'{row["预期差分"]:.1f}', f'{row["订单爆发分"]:.1f}'])
        col_widths = [3*cm, 1.5*cm, 1.8*cm, 2*cm, 2*cm]
        thm_table = Table(thm_data, colWidths=col_widths)
        thm_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4a90a4')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, -1), font_name),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f5f7fa')]),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        elements.append(thm_table)
    elements.append(PageBreak())
    return elements


# ============================================================
# 方法论
# ============================================================
def build_methodology(styles):
    """构建方法论 — BullScore 八因子 + 主题加成"""
    elements = []
    font_name = 'Chinese' if FONT_REGISTERED else 'Helvetica'

    elements.append(Paragraph('二、BullScore 评分方法论', styles['ChapterTitle']))
    elements.append(HRFlowable(width='100%', thickness=1, color=colors.HexColor('#2d5a87')))
    elements.append(Spacer(1, 15))

    factors = [
        {'name': '产业景气度 (IndustryDemandScore)', 'weight': '25分',
         'desc': '采用需求链模型，通过终端需求(AI服务器/GPU/新能源车/机器人等)、订单强度、产品价格趋势、产能利用率、行业扩产强度五个子维度，评估产业链整体景气度。所有指标使用行业内分位数标准化，禁止使用申万行业景气度。'},
        {'name': '技术壁垒 (TechBarrierScore)', 'weight': '15分',
         'desc': '通过ROIC(30%)、ROE(20%)、毛利率(20%)、研发强度(20%)、专利评分(10%)五个维度，评估企业的技术护城河深度。全部采用行业内分位数排名，不使用固定阈值。'},
        {'name': '订单爆发 (OrderExplosionScore)', 'weight': '15分',
         'desc': '通过合同负债增速(40%)、营收加速度(25%)、预收款增速(20%)、存货结构优化(15%)识别牛股启动前兆。全部连续评分，禁止二元判断。'},
        {'name': '业绩质量 (EarningsQualityScore)', 'weight': '15分',
         'desc': '通过利润增速排名(35%)、利润加速度(25%)、营收增速排名(20%)、现金流增速(20%)四个维度，评估业绩增长的可持续性与真实质量。全部连续化评分。'},
        {'name': '龙头地位 (LeaderScore)', 'weight': '10分',
         'desc': '通过市场份额(40%)、行业排名(20%)、机构覆盖度(20%)、客户质量(20%)四个维度，按行业第一/第二/前三/前五/普通分档映射分值。'},
        {'name': '预期差 (ExpectationScore)', 'weight': '10分',
         'desc': '通过未来利润CAGR(40%)、盈利上调次数(30%)、PEG倒数(20%)、新业务贡献(10%)，寻找未来利润增速高于市场预期的公司。'},
        {'name': '机构认可 (InstitutionScore)', 'weight': '5分',
         'desc': '通过基金持仓变化(40%)、调研次数(30%)、覆盖机构数(20%)、预测上调次数(10%)评估机构认可度。不使用北向持仓变化百分比，改用持仓占流通股变化避免异常值。'},
        {'name': '市值弹性 (MarketCapElasticity)', 'weight': '5分',
         'desc': '50~300亿=100分，300~800亿=80分，800~2000亿=60分，2000亿以上=30分，再映射至5分制。小市值在同等条件下弹性更大。'},
    ]

    for i, f in enumerate(factors, 1):
        elements.append(Paragraph(f"2.{i} {f['name']}（权重{f['weight']}）", styles['SectionTitle']))
        elements.append(Paragraph(f['desc'], styles['ReportBody']))
        elements.append(Spacer(1, 8))

    # 主题加成
    elements.append(Paragraph('2.9 主题强度加成 (ThemeScore)', styles['SectionTitle']))
    ts_desc = """
    在BullScore基础上叠加20%主题强度加成。主题包括AI算力、PCB、光模块、液冷、机器人、
    商业航天、低空经济、半导体设备/材料、创新药、数据要素等。
    ThemeScore = 主题热度(趋势分×0.35 + 情绪分×0.30 + 资金活跃度×0.20 + 综合分×0.15)。

    FinalScore = 0.80 × BullScore + 0.20 × ThemeScore
    """
    elements.append(Paragraph(ts_desc, styles['ReportBody']))
    elements.append(Spacer(1, 10))

    # 牛股等级
    elements.append(Paragraph('2.10 牛股等级定义', styles['SectionTitle']))
    level_desc = """
    S+级(≥95)：核心牛股 | S级(90~95)：牛股 | A级(85~90)：产业龙头
    B级(80~85)：成长股 | 观察名单(70~80) | 淘汰(<70)
    """
    elements.append(Paragraph(level_desc, styles['ReportBody']))

    elements.append(PageBreak())
    return elements


# ============================================================
# 分层分析
# ============================================================
def _build_tier_table(tier_df, level_name, header_color, row_colors, styles):
    """为某个等级构建表格"""
    font_name = 'Chinese' if FONT_REGISTERED else 'Helvetica'
    data = [['序号', '代码', '名称', '主题/链',
             '产业景气', '订单爆发', '预期差', 'FinalScore',
             'ROE%', '研发%', '利润YoY%']]
    for idx, (_, row) in enumerate(tier_df.iterrows(), 1):
        code = str(row['ts_code']).replace('.SH','').replace('.SZ','').replace('.BJ','')
        data.append([
            str(idx), code, str(row.get('name', ''))[:4],
            str(row.get('theme', row.get('industry', '')))[:8],
            f'{row["industry_demand_score"]:.0f}',
            f'{row["order_explosion_score"]:.0f}',
            f'{row["expectation_score"]:.0f}',
            f'{row["final_score"]:.1f}',
            f'{row["roe"]:.1f}',
            f'{row["rd_ratio"]:.1f}',
            f'{row["profit_yoy"]:.1f}',
        ])
    col_widths = [0.8*cm, 2*cm, 1.5*cm, 2.2*cm, 1.5*cm, 1.5*cm, 1.5*cm, 2*cm, 1.3*cm, 1.3*cm, 1.5*cm]
    tbl = Table(data, colWidths=col_widths)
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), header_color),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, -1), font_name),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('FONTSIZE', (0, 1), (-1, -1), 7.5),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#cccccc')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), row_colors),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    return tbl


def build_tier_analysis(a_df, b_df, styles):
    """构建分层分析"""
    elements = []
    font_name = 'Chinese' if FONT_REGISTERED else 'Helvetica'

    elements.append(Paragraph('三、分层标的深度分析', styles['ChapterTitle']))
    elements.append(HRFlowable(width='100%', thickness=1, color=colors.HexColor('#2d5a87')))
    elements.append(Spacer(1, 15))

    # A级
    elements.append(Paragraph('3.1 A级产业龙头', styles['SectionTitle']))
    a_text = f"""
    共<b>{len(a_df)}只</b>A级标的，平均FinalScore <b>{a_df["final_score"].mean():.1f}</b>。
    这批标的具备高产业景气度、强订单验证、技术壁垒突出等特征，属于产业趋势驱动的核心资产。
    """
    elements.append(Paragraph(a_text.strip(), styles['ReportBody']))
    elements.append(Spacer(1, 6))
    if len(a_df) > 0:
        elements.append(_build_tier_table(a_df, 'A级产业龙头', colors.HexColor('#c0392b'),
                                          [colors.white, colors.HexColor('#fff5f5')], styles))
    elements.append(PageBreak())

    # B级
    elements.append(Paragraph('3.2 B级成长股', styles['SectionTitle']))
    b_text = f"""
    共<b>{len(b_df)}只</b>B级标的，平均FinalScore <b>{b_df["final_score"].mean():.1f}</b>。
    这批标的在特定维度（如预期差、订单爆发）表现突出，具备较强的成长弹性。
    """
    elements.append(Paragraph(b_text.strip(), styles['ReportBody']))
    elements.append(Spacer(1, 6))
    if len(b_df) > 0:
        elements.append(_build_tier_table(b_df, 'B级成长股', colors.HexColor('#e67e22'),
                                          [colors.white, colors.HexColor('#fffaf0')], styles))
    elements.append(PageBreak())
    return elements


# ============================================================
# 风险提示
# ============================================================
def build_risk_analysis(df, styles):
    """构建风险提示"""
    elements = []
    font_name = 'Chinese' if FONT_REGISTERED else 'Helvetica'

    elements.append(Paragraph('四、风险提示与投资建议', styles['ChapterTitle']))
    elements.append(HRFlowable(width='100%', thickness=1, color=colors.HexColor('#2d5a87')))
    elements.append(Spacer(1, 15))

    elements.append(Paragraph('4.1 主要风险因素', styles['SectionTitle']))
    risks = [
        ('市场风险', '量化模型基于历史数据与当前产业趋势判断，市场风格切换或宏观环境变化可能导致模型失效。'),
        ('产业趋势风险', 'AI/科技产业趋势若低于预期（如AI资本开支放缓），将直接影响主线标的的订单持续性。'),
        ('竞争格局恶化', '高景气行业容易吸引大量资本进入，导致供给过剩、毛利率下滑。'),
        ('业绩不达预期', '部分高预期差标的若未能兑现业绩增速，可能导致股价大幅回调。'),
        ('估值泡沫风险', '主题热度驱动的溢价可能在情绪降温后快速回归。'),
    ]
    for name, desc in risks:
        elements.append(Paragraph(f'<b>• {name}：</b>{desc}', styles['ReportBody']))

    elements.append(Spacer(1, 15))
    elements.append(Paragraph('4.2 投资配置建议', styles['SectionTitle']))
    config_text = """
    <b>仓位建议：</b><br/>
    • A级产业龙头为核心持仓，建议配置15%~25%仓位<br/>
    • B级成长股为卫星配置，建议配置10%~15%仓位<br/>
    • 单一个股持仓不超过总仓位的10%<br/>
    <b>行业分散：</b>单一产业链持仓不超过总仓位的30%<br/>
    <b>跟踪调整：</b>建议每季度根据最新财报数据和主题评分变化调整持仓
    """
    elements.append(Paragraph(config_text, styles['ReportBody']))

    elements.append(Spacer(1, 15))
    elements.append(Paragraph('4.3 重点关注个股', styles['SectionTitle']))
    top10 = df.head(10)
    highlight_text = "基于 BullScore 综合评分，建议重点关注以下标的："
    elements.append(Paragraph(highlight_text, styles['ReportBody']))
    elements.append(Spacer(1, 5))
    for i, (_, row) in enumerate(top10.iterrows(), 1):
        hl = (f"{i}. <b>{row.get('name', '')}</b> ({row.get('ts_code', '')}) "
              f"| 主题:{row.get('theme', row.get('industry', ''))} "
              f"| FinalScore={row['final_score']:.1f} "
              f"| 订单爆发={row['order_explosion_score']:.0f} "
              f"| 预期差={row['expectation_score']:.0f}")
        elements.append(Paragraph(hl, styles['ReportBody']))
    elements.append(PageBreak())
    return elements


# ============================================================
# 附录
# ============================================================
def _format_stock_code(ts_code):
    """格式化股票代码，保留深圳股票前面的0"""
    code = str(ts_code).strip()
    # 提取纯数字部分（保留原始位数）
    numeric = ''.join(c for c in code if c.isdigit())
    # 判断市场：SH=6开头, SZ/BJ=0/3/4/8开头
    if '.SZ' in code or numeric.startswith(('0', '3', '4', '8')):
        # 深圳市场，保留完整6位代码
        return numeric
    else:
        # 上海市场
        return numeric


def build_appendix(df, styles):
    """构建附录：前100只标的"""
    elements = []
    font_name = 'Chinese' if FONT_REGISTERED else 'Helvetica'

    elements.append(Paragraph('五、附录：TOP100标的清单', styles['ChapterTitle']))
    elements.append(HRFlowable(width='100%', thickness=1, color=colors.HexColor('#2d5a87')))
    elements.append(Spacer(1, 15))

    elements.append(Paragraph('5.1 TOP100入选标的', styles['SectionTitle']))

    cols = ['序号', '代码', '名称', '产业链', '等级',
            '产景', '技术', '订单', '业绩', '龙头', '预期差', '机构', '市值弹性',
            'Bull', '主题', 'Final']
    col_keys = ['industry_demand_score', 'tech_barrier_score', 'order_explosion_score',
                'earnings_quality_score', 'leader_score', 'expectation_score',
                'institution_score', 'marketcap_score', 'bull_score', 'theme_score', 'final_score']

    # 只取前100个
    df_top100 = df.head(100)

    all_data = [cols]
    for idx, (_, row) in enumerate(df_top100.iterrows(), 1):
        code = _format_stock_code(row['ts_code'])
        r = [str(idx), code, str(row.get('name', ''))[:4],
             str(row.get('theme', ''))[:6], str(row.get('bull_level', ''))[:6].replace('产业', '').replace('成长', '')[:4]]
        for k in col_keys:
            r.append(f'{row[k]:.0f}' if k in col_keys[:8] else f'{row[k]:.1f}')
        all_data.append(r)

    cw = [0.6*cm, 1.5*cm, 1.2*cm, 1.5*cm, 1.2*cm] + [1*cm]*8 + [1.2*cm, 1*cm, 1.2*cm]
    all_table = Table(all_data, colWidths=cw)
    all_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#34495e')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, -1), font_name),
        ('FONTSIZE', (0, 0), (-1, -1), 5.5),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#cccccc')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    elements.append(all_table)
    elements.append(Spacer(1, 20))

    elements.append(Paragraph('5.2 数据说明', styles['SectionTitle']))
    data_notes = """
    <b>数据来源：</b>Tushare Pro 金融数据平台<br/>
    <b>财务数据：</b>利润表(income)、资产负债表(balancesheet)、现金流量表(cashflow)、业绩预告(forecast)<br/>
    <b>行情数据：</b>日线行情(daily)、大单资金流(moneyflow)、每日基本面(daily_basic)<br/>
    <b>行业分类：</b>东方财富行业分类（非申万）<br/>
    <b>产业链映射：</b>需求链驱动模型（chain_mapping.py），基于同花顺概念+白名单+关键词<br/>
    <b>报告日期：</b>{report_date}
    """
    elements.append(Paragraph(data_notes.format(report_date=datetime.now().strftime('%Y年%m月%d日')), styles['ReportBody']))
    return elements


# ============================================================
# 页码
# ============================================================
def add_page_number(canvas, doc):
    """添加页码"""
    canvas.saveState()
    canvas.setFont('Helvetica', 8)
    canvas.setFillColor(colors.HexColor('#888888'))
    page_num = canvas.getPageNumber()
    text = f'第 {page_num} 页'
    canvas.drawCentredString(A4[0]/2, 1.5*cm, text)
    canvas.drawRightString(A4[0] - 2*cm, 1.5*cm, 'BullScore 中长线牛股投资报告')
    canvas.restoreState()


# ============================================================
# 主函数
# ============================================================
def generate_report(csv_path: str, output_path: str) -> str:
    """生成完整PDF报告"""
    print(f"加载数据: {csv_path}")
    df = load_data(csv_path)
    print(f"共 {len(df)} 只标的")

    # 按 bull_level 分层
    a_df, b_df = tier_classify(df)
    print(f"A级产业龙头: {len(a_df)}, B级成长股: {len(b_df)}")

    thm_dist = theme_analysis(df)

    styles = get_styles()
    elements = []

    print("生成封面...")
    elements += build_cover(df, styles)
    print("生成执行摘要...")
    elements += build_executive_summary(df, a_df, b_df, thm_dist, styles)
    print("生成方法论...")
    elements += build_methodology(styles)
    print("生成分层分析...")
    elements += build_tier_analysis(a_df, b_df, styles)
    print("生成风险提示...")
    elements += build_risk_analysis(df, styles)
    print("生成附录...")
    elements += build_appendix(df, styles)

    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        rightMargin=2*cm, leftMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm,
        title='BullScore 中长线牛股投资报告',
        author='BullScore Quant System',
        subject='中长线量化选股分析报告',
    )

    print(f"生成PDF: {output_path}")
    doc.build(elements, onFirstPage=add_page_number, onLaterPages=add_page_number)
    print("报告生成完成！")
    return output_path


if __name__ == '__main__':
    output_dir = Path(__file__).parent / 'output'

    # 找最新的 bullscore/bull_stocks/elite_stocks_*.csv
    csv_files = (
        sorted(output_dir.glob('bullscore_*.csv'), key=os.path.getmtime, reverse=True) or
        sorted(output_dir.glob('bull_stocks_*.csv'), key=os.path.getmtime, reverse=True) or
        sorted(output_dir.glob('elite_stocks_*.csv'), key=os.path.getmtime, reverse=True)
    )
    if not csv_files:
        print(f"未找到 csv 数据文件在 {output_dir}")
        sys.exit(1)

    csv_file = csv_files[0]
    today_str = datetime.now().strftime('%Y%m%d')
    output_file = output_dir / f'BullScore中长线牛股投资报告_{today_str}.pdf'

    print(f"使用数据: {csv_file}")
    generate_report(str(csv_file), str(output_file))
    print(f"\n报告已保存至: {output_file}")
