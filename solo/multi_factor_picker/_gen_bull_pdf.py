# -*- coding: utf-8 -*-
"""BullScore合格池PDF报告"""
import os, sys
sys.path.insert(0, r'D:\mystock')
import pandas as pd
import numpy as np
from datetime import datetime

# ── 数据 ──
csv = r'D:\mystock\solo\report_daily\bull_stocks_qualified.csv'
df = pd.read_csv(csv)
scores = df['最终分']

# 统计
now = datetime.now().strftime('%Y-%m-%d %H:%M')
today_str = datetime.now().strftime('%Y%m%d')
n = len(df)
avg = scores.mean()
med = scores.median()
hi = scores.max()
lo = scores.min()

grade_dist = df['等级'].value_counts().to_dict() if '等级' in df.columns else {}
ind_top = df['industry'].value_counts().head(10).to_dict() if 'industry' in df.columns else {}

# 分层
bins = [0, 60, 65, 70, 75, 80, 85, 90, 100]
labels = ['<60', '60-65', '65-70', '70-75', '75-80', '80-85', '85-90', '90+']
df['分层'] = pd.cut(scores, bins=bins, labels=labels)

# ── 创建PDF ──
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm, cm
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                 TableStyle, PageBreak, HRFlowable)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

font_path = r'C:\Windows\Fonts\simhei.ttf'
font_name = 'SimHei'
pdfmetrics.registerFont(TTFont(font_name, font_path))

out = rf'D:\mystock\solo\report_daily\bull_score_report_{today_str}.pdf'
doc = SimpleDocTemplate(out, pagesize=A4,
    leftMargin=15*mm, rightMargin=15*mm,
    topMargin=15*mm, bottomMargin=15*mm)

st = ParagraphStyle('N', fontName=font_name, fontSize=9, leading=13, spaceAfter=2*mm)
st_title = ParagraphStyle('T', fontName=font_name, fontSize=16, leading=22, spaceAfter=6*mm)
st_h1 = ParagraphStyle('H1', fontName=font_name, fontSize=13, leading=18, spaceAfter=3*mm, textColor=colors.HexColor('#1a5276'))
st_h2 = ParagraphStyle('H2', fontName=font_name, fontSize=11, leading=15, spaceAfter=2*mm, textColor=colors.HexColor('#2c3e50'))
st_h3 = ParagraphStyle('H3', fontName=font_name, fontSize=9, leading=13, spaceAfter=1*mm, textColor=colors.HexColor('#34495e'))
st_stat = ParagraphStyle('S', fontName=font_name, fontSize=10, leading=14, spaceAfter=1*mm)
st_footer = ParagraphStyle('F', fontName=font_name, fontSize=7, leading=10, textColor=colors.gray)

elements = []

# ── 封面 ──
elements.append(Paragraph('BullScore 中长线牛股池', st_title))
elements.append(Paragraph(f'生成时间: {now}', st_stat))
elements.append(Paragraph(f'合格股票: {n} 只 | 评分均值: {avg:.1f} | 中位: {med:.1f} | 最高: {hi:.1f} | 最低: {lo:.1f}', st_stat))
elements.append(Spacer(1, 5*mm))

# ── 概览统计 ──
elements.append(Paragraph('一、评分分层统计', st_h1))
elements.append(Spacer(1, 2*mm))

layers = df['分层'].value_counts().sort_index()
tdata = [['评分区间', '数量', '占比']]
for l, cnt in layers.items():
    tdata.append([str(l), str(cnt), f'{cnt/n*100:.1f}%'])
t = Table(tdata, colWidths=[25*mm, 20*mm, 20*mm])
t.setStyle(TableStyle([
    ('FONTNAME', (0,0), (-1,-1), font_name),
    ('FONTSIZE', (0,0), (-1,-1), 9),
    ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1a5276')),
    ('TEXTCOLOR', (0,0), (-1,0), colors.white),
    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
    ('GRID', (0,0), (-1,-1), 0.5, colors.gray),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f0f4f8')]),
]))
elements.append(t)
elements.append(Spacer(1, 5*mm))

# ── 等级分布 ──
elements.append(Paragraph('二、等级分布', st_h1))
elements.append(Spacer(1, 2*mm))
gdata = [['等级', '数量']]
for g, cnt in sorted(grade_dist.items()):
    gdata.append([str(g), str(cnt)])
gt = Table(gdata, colWidths=[30*mm, 20*mm])
gt.setStyle(TableStyle([
    ('FONTNAME', (0,0), (-1,-1), font_name),
    ('FONTSIZE', (0,0), (-1,-1), 9),
    ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1a5276')),
    ('TEXTCOLOR', (0,0), (-1,0), colors.white),
    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
    ('GRID', (0,0), (-1,-1), 0.5, colors.gray),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f0f4f8')]),
]))
elements.append(gt)
elements.append(Spacer(1, 5*mm))

# ── 评分方法论 ──
elements.append(Paragraph('三、评分方法论（BullScore v3.1）', st_h1))
elements.append(Spacer(1, 2*mm))

methodology_text = [
    (
        "BullScore v3.1 是中长线牛股评分系统，基于 10 因子加权评分框架，"
        "输出 0~100 分的综合评分。合格线 60 分，分数越高代表中长线投资价值越大。"
    ),
    (
        "核心因子及权重如下："
    ),
]

factors = [
    ("产业景气度（14%）",
     "通过终端需求、订单强度、产品价格趋势、产能利用率、资本开支五个子维度，"
     "评估产业链整体景气度。全部使用行业内分位数标准化。"),
    ("订单爆发（14%）",
     "通过合同负债增速、营收加速度、预收款增速、存货结构优化识别牛股启动前兆。"
     "全部连续评分，禁止二元判断。"),
    ("预期差（14%）",
     "通过未来利润CAGR、盈利上调次数、PEG倒数、新业务贡献，寻找利润增速高于市场预期的公司。"
     "v3.1新增：优先使用Q1财报数据，当Q1营收增速大幅低于年报时触发增长衰减降权。"),
    ("业绩质量（12%）",
     "通过利润增速排名、利润加速度、营收增速排名、现金流增速四个维度，"
     "评估业绩增长的可持续性与真实质量。全部连续化评分。"),
    ("技术壁垒（10%）",
     "通过ROIC(30%)、ROE(20%)、毛利率(15%)、研发强度(25%)、专利评分(10%)五个维度，"
     "评估企业的技术护城河深度。引入绝对值下限约束：毛利率<15%上限60分、"
     "ROE<5%上限60分，防止矮子里拔将军。毛利率连续下降>5%扣10分。"),
    ("龙头地位（8%）",
     "通过市场份额(40%)、行业排名(20%)、机构覆盖度(20%)、客户质量(20%)四个维度，"
     "按行业第一/第二/前三/前五/普通分档映射分值。"),
    ("机构认可（8%）",
     "通过分析师覆盖数、基金持仓变化、评级情绪、近30天上调幅度评估机构认可度。"),
    ("历史辨识度（8%）",
     "v3.1新增。通过资金活跃度(25%)、涨停基因(25%)、价格动量(20%)、"
     "舆情热度(15%)、辨识度持续性(15%)五个维度，评估股票在历史行情中的辨识度和股性。"),
    ("估值安全（7%）",
     "v3.1新增。通过PEG估值(30%)、质押风险(25%)、解禁压力(20%)、"
     "营收质量(15%)、审计意见(10%)五个维度，评估估值安全边际。"
     "缺失质押/解禁/审计数据时统一用50分中性值。"),
    ("市值弹性（5%）",
     "50~300亿=100分，300~800亿=80分，800~2000亿=60分，2000亿以上=30分。"
     "小市值在同等条件下弹性更大。"),
]

for title, desc in factors:
    elements.append(Paragraph(f'  ▸ {title}', st_h2))
    elements.append(Paragraph(f'    {desc}', st))
    elements.append(Spacer(1, 1*mm))

elements.append(Spacer(1, 2*mm))
elements.append(Paragraph(
    "最终分 = 0.88 × BullScore_v3.1 + 0.12 × ThemeScore_v2（主题加成）。"
    "主题分基于主营业务匹配和概念板块热度。"
    "数据完整度<62.5%（缺失3个及以上维度）时，最终分打8%折扣。", st))
elements.append(Spacer(1, 2*mm))
elements.append(Paragraph(
    "v3.1核心改进：① Q1财报优先，增长衰减标记；② 技术壁垒绝对值下限约束+毛利率趋势惩罚；"
    "③ 预期差低基数校验，利润高增长需营收支撑；④ 质押/解禁/审计缺失用50分中性替代默认满分；"
    "⑤ 数据完整度惩罚。", st))
elements.append(Spacer(1, 5*mm))

# ── A级/B级龙头 ──
elements.append(Paragraph('四、A级/B级龙头', st_h1))
elements.append(Spacer(1, 2*mm))
top_df = df[df['等级'].isin(['A级产业龙头', 'B级成长股'])].sort_values('最终分', ascending=False)
th_data = [['代码', '名称', '等级', '评分', '行业', '主题']]
for _, r in top_df.iterrows():
    th_data.append([
        f"{int(r['code']):06d}", str(r['name']), str(r['等级']),
        f"{r['最终分']:.1f}", str(r.get('industry', '') or ''),
        str(r.get('theme', '') or '')
    ])
th = Table(th_data, colWidths=[16*mm, 20*mm, 22*mm, 14*mm, 22*mm, 30*mm])
th.setStyle(TableStyle([
    ('FONTNAME', (0,0), (-1,-1), font_name),
    ('FONTSIZE', (0,0), (-1,-1), 8),
    ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1a5276')),
    ('TEXTCOLOR', (0,0), (-1,0), colors.white),
    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
    ('GRID', (0,0), (-1,-1), 0.5, colors.gray),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f0f4f8')]),
]))
elements.append(th)
elements.append(Spacer(1, 5*mm))

# ── TOP10行业 ──
elements.append(Paragraph('五、TOP10行业分布', st_h1))
elements.append(Spacer(1, 2*mm))
idatas = [['行业', '数量', '占比']]
for ind, cnt in ind_top.items():
    idatas.append([str(ind), str(cnt), f'{cnt/n*100:.1f}%'])
it = Table(idatas, colWidths=[30*mm, 20*mm, 20*mm])
it.setStyle(TableStyle([
    ('FONTNAME', (0,0), (-1,-1), font_name),
    ('FONTSIZE', (0,0), (-1,-1), 9),
    ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1a5276')),
    ('TEXTCOLOR', (0,0), (-1,0), colors.white),
    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
    ('GRID', (0,0), (-1,-1), 0.5, colors.gray),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f0f4f8')]),
]))
elements.append(it)
elements.append(Spacer(1, 5*mm))

# ── 评分TOP50 ──
elements.append(Paragraph('六、BullScore TOP50', st_h1))
elements.append(Spacer(1, 2*mm))
top50 = df.sort_values('最终分', ascending=False).head(50)
t50_data = [['排名', '代码', '名称', '评分', '行业', '营收同比%', '利润同比%', 'ROE', '市值(亿)']]
for i, (_, r) in enumerate(top50.iterrows(), 1):
    rev = f"{r.get('营收同比', ''):.0f}" if pd.notna(r.get('营收同比', np.nan)) else '-'
    prof = f"{r.get('利润同比', ''):.0f}" if pd.notna(r.get('利润同比', np.nan)) else '-'
    roe = f"{r.get('ROE', ''):.1f}" if pd.notna(r.get('ROE', np.nan)) else '-'
    mcap = f"{r.get('市值(亿)', ''):.0f}" if pd.notna(r.get('市值(亿)', np.nan)) else '-'
    t50_data.append([
        str(i), f"{int(r['code']):06d}", str(r['name']),
        f"{r['最终分']:.1f}",
        str(r.get('industry', '') or ''),
        rev, prof, roe, mcap
    ])
t50 = Table(t50_data, colWidths=[10*mm, 16*mm, 18*mm, 12*mm, 24*mm, 18*mm, 18*mm, 12*mm, 16*mm])
t50.setStyle(TableStyle([
    ('FONTNAME', (0,0), (-1,-1), font_name),
    ('FONTSIZE', (0,0), (-1,-1), 7),
    ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1a5276')),
    ('TEXTCOLOR', (0,0), (-1,0), colors.white),
    ('ALIGN', (1,0), (-1,-1), 'CENTER'),
    ('ALIGN', (0,0), (0,-1), 'CENTER'),
    ('GRID', (0,0), (-1,-1), 0.3, colors.gray),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f0f4f8')]),
]))
elements.append(t50)

# ── 按行业TOP半导体/元器件 ──
elements.append(Spacer(1, 5*mm))
elements.append(Paragraph('七、重点行业个股', st_h1))
elements.append(Spacer(1, 2*mm))
for focus_ind, title in [('半导体', '半导体'), ('元器件', '元器件')]:
    subset = df[(df['industry'] == focus_ind) & (df['最终分'] >= 80)]
    if len(subset) == 0:
        continue
    subset = subset.sort_values('最终分', ascending=False)
    elements.append(Paragraph(f'  ⊙ {title}（{len(subset)}只≥80分）', st_h2))
    sd = [['代码', '名称', '评分', '营收同比%', '利润同比%', 'ROE', '市值(亿)']]
    for _, r in subset.iterrows():
        sd.append([
            f"{int(r['code']):06d}", str(r['name']),
            f"{r['最终分']:.1f}",
            f"{r.get('营收同比', ''):.0f}" if pd.notna(r.get('营收同比', np.nan)) else '-',
            f"{r.get('利润同比', ''):.0f}" if pd.notna(r.get('利润同比', np.nan)) else '-',
            f"{r.get('ROE', ''):.1f}" if pd.notna(r.get('ROE', np.nan)) else '-',
            f"{r.get('市值(亿)', ''):.0f}" if pd.notna(r.get('市值(亿)', np.nan)) else '-',
        ])
    stbl = Table(sd, colWidths=[16*mm, 18*mm, 12*mm, 18*mm, 18*mm, 12*mm, 16*mm])
    stbl.setStyle(TableStyle([
        ('FONTNAME', (0,0), (-1,-1), font_name),
        ('FONTSIZE', (0,0), (-1,-1), 7),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1a5276')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('ALIGN', (2,0), (-1,-1), 'CENTER'),
        ('ALIGN', (0,0), (0,-1), 'CENTER'),
        ('GRID', (0,0), (-1,-1), 0.3, colors.gray),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f0f4f8')]),
    ]))
    elements.append(stbl)
    elements.append(Spacer(1, 3*mm))

# 卷尾
elements.append(Spacer(1, 10*mm))
elements.append(HRFlowable(width='100%', thickness=0.5, color=colors.gray))
elements.append(Paragraph(f'BullScore v3.1 | 生成时间: {now} | 共{n}只合格股票 | 阈值: ≥60分', st_footer))

doc.build(elements)
print(f"PDF生成成功: {out} ({os.path.getsize(out)/1024:.0f} KB)")
