# -*- coding: utf-8 -*-
"""从 bull_stocks CSV 生成 BullScore 合格股池 PDF"""
import os
import pandas as pd
from datetime import datetime

# 注册中文字体
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

font_registered = False
for font_path in [r'C:\Windows\Fonts\simhei.ttf', r'C:\Windows\Fonts\msyh.ttc']:
    if os.path.exists(font_path):
        try:
            pdfmetrics.registerFont(TTFont('ChineseFont', font_path))
            font_registered = True
            break
        except:
            continue

chinese_font = 'ChineseFont' if font_registered else 'Helvetica'

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT

def pad_code(code):
    """将代码补足6位（CSV读取时leading zero被pandas去掉）"""
    code_str = str(code).strip()
    if len(code_str) < 6:
        code_str = code_str.zfill(6)
    return code_str

def add_market_suffix(code):
    """添加交易所后缀"""
    code_str = pad_code(code)
    if code_str.startswith('6') and not code_str.startswith('688'):
        return code_str + '.SH'
    elif code_str.startswith('688'):
        return code_str + '.SH'
    else:
        return code_str + '.SZ'

# === 配置 ===
csv_path = r'D:\mystock\solo\multi_factor_picker\output\bull_stocks_20260629_235153.csv'
output_dir = r'D:\mystock\solo\multi_factor_picker\output'
os.makedirs(output_dir, exist_ok=True)
ts = datetime.now().strftime('%Y%m%d_%H%M%S')
pdf_path = os.path.join(output_dir, f'bull_qualified_report_{ts}.pdf')

# === 读取数据 ===
df = pd.read_csv(csv_path, dtype={'code': str})

# 按最终分排序
df = df.sort_values('最终分', ascending=False).reset_index(drop=True)

# 等级分布
grade_counts = df['等级'].value_counts()

# === 创建PDF ===
doc = SimpleDocTemplate(pdf_path, pagesize=A4,
                        rightMargin=1.2*cm, leftMargin=1.2*cm,
                        topMargin=2*cm, bottomMargin=2*cm)

styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name='CTitle', fontName=chinese_font, fontSize=18, spaceAfter=6,
                          alignment=TA_CENTER, textColor=colors.HexColor('#1a1a1a')))
styles.add(ParagraphStyle(name='CSubtitle', fontName=chinese_font, fontSize=10, spaceAfter=16,
                          alignment=TA_CENTER, textColor=colors.HexColor('#888888')))
styles.add(ParagraphStyle(name='CH1', fontName=chinese_font, fontSize=12, spaceAfter=8,
                          spaceBefore=12, textColor=colors.HexColor('#2c3e50')))
styles.add(ParagraphStyle(name='CBullet', fontName=chinese_font, fontSize=9.5, spaceAfter=4, leftIndent=15, leading=14))
styles.add(ParagraphStyle(name='CTableHead', fontName=chinese_font, fontSize=8,
                          alignment=TA_CENTER, textColor=colors.white))
styles.add(ParagraphStyle(name='Note', fontName=chinese_font, fontSize=8.5, spaceAfter=4,
                          textColor=colors.HexColor('#888888'), leading=12))

story = []

# ===== 封面标题 =====
story.append(Paragraph('🐂 BullScore 合格股池报告', styles['CTitle']))
story.append(Paragraph(f'数据时间：2026-06-29  生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M")}', styles['CSubtitle']))
story.append(Spacer(1, 0.2*cm))

# ===== 概览统计 =====
total = len(df)
avg_score = df['最终分'].mean()
max_score = df['最终分'].max()
min_score = df['最终分'].min()
median_score = df['最终分'].median()

story.append(Paragraph('📊 股池概览', styles['CH1']))
story.append(Paragraph(f'• 股票总数：<b>{total}</b> 只', styles['CBullet']))
story.append(Paragraph(f'• 平均评分：{avg_score:.1f} 分  |  最高：{max_score:.1f} 分  |  最低：{min_score:.1f} 分  |  中位：{median_score:.1f} 分', styles['CBullet']))
story.append(Spacer(1, 0.2*cm))

# 等级分布
story.append(Paragraph('等级分布', styles['CH1']))
grade_info = [
    ('A级产业龙头', '⭐⭐⭐⭐⭐', '#c0392b', '超级成长龙头'),
    ('B级成长股',   '⭐⭐⭐⭐',   '#e67e22', '优质成长股'),
    ('观察名单',    '⭐⭐⭐',     '#7f8c8d', '潜力观察池'),
]
for grade_val, stars, color, desc in grade_info:
    cnt = int(grade_counts.get(grade_val, 0))
    if cnt > 0:
        gdf = df[df['等级'] == grade_val]
        avg_g = gdf['最终分'].mean()
        pct = cnt / total * 100
        story.append(Paragraph(
            f'<font color="{color}">{stars} {grade_val}</font>（{desc}）：{cnt}只（{pct:.1f}%）  平均分 {avg_g:.1f}',
            styles['CBullet']
        ))

story.append(Spacer(1, 0.2*cm))

# 行业分布
industry_counts = df['industry'].value_counts().head(10)
story.append(Paragraph('行业分布 TOP10', styles['CH1']))
ind_data = [['排名', '行业', '数量', '占比']]
for i, (ind, cnt) in enumerate(industry_counts.items(), start=1):
    pct = cnt / total * 100
    ind_data.append([str(i), str(ind), str(cnt), f'{pct:.1f}%'])

ind_table = Table(ind_data, colWidths=[1.5*cm, 5*cm, 2.5*cm, 2.5*cm])
ind_table.setStyle(TableStyle([
    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#34495e')),
    ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
    ('FONTNAME', (0, 0), (-1, -1), chinese_font),
    ('FONTSIZE', (0, 0), (-1, -1), 9),
    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#f8f9fa'), colors.white]),
    ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#bdc3c7')),
    ('TOPPADDING', (0, 0), (-1, -1), 5),
    ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
]))
story.append(ind_table)
story.append(Spacer(1, 0.3*cm))

story.append(PageBreak())

# ===== 个股列表 =====
story.append(Paragraph('📋 个股明细（按最终分降序）', styles['CH1']))

def make_stock_table(stocks_df, title, bg_color, title_color='#ffffff'):
    """生成个股表格"""
    rows = []
    header = ['代码', '名称', '行业', '主题', '最终分', '等级', '产业', '壁垒', '预期差', '龙头', '机构']
    rows.append(header)

    for _, row in stocks_df.iterrows():
        code = add_market_suffix(row.get('code', ''))
        name = str(row.get('name', ''))[:6]
        industry = str(row.get('industry', ''))[:6]
        theme = str(row.get('theme', '-'))[:8]
        final = row.get('最终分', 0)
        grade_short = str(row.get('等级', '-'))[:4]
        rows.append([
            code,
            name,
            industry,
            theme,
            f'{final:.1f}',
            grade_short,
            f"{row.get('产业景气', 0):.0f}",
            f"{row.get('技术壁垒', 0):.0f}",
            f"{row.get('预期差', 0):.0f}",
            f"{row.get('龙头地位', 0):.0f}",
            f"{row.get('机构认可', 0):.0f}",
        ])

    col_widths = [2.3*cm, 1.8*cm, 1.6*cm, 2.0*cm, 1.5*cm, 1.5*cm,
                  1.4*cm, 1.4*cm, 1.4*cm, 1.4*cm, 1.4*cm]

    tbl = Table(rows, colWidths=col_widths, repeatRows=1)
    style = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c3e50')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, -1), chinese_font),
        ('FONTSIZE', (0, 0), (-1, -1), 7.5),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#bdc3c7')),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        # 最终分列高亮
        ('BACKGROUND', (4, 1), (4, -1), colors.HexColor('#eaf2ff')),
        # 偶数行
        ('ROWBACKGROUNDS', (0, 2), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
    ]
    tbl.setStyle(TableStyle(style))
    return tbl

# 分页展示各等级
grade_order = [
    ('A级产业龙头', '⭐⭐⭐⭐⭐ A级产业龙头（超级成长龙头）', '#c0392b'),
    ('B级成长股',   '⭐⭐⭐⭐ B级成长股（优质成长股）',       '#e67e22'),
    ('观察名单',    '⭐⭐⭐ 观察名单（潜力池）',               '#7f8c8d'),
]

for grade_val, title, color in grade_order:
    gdf = df[df['等级'] == grade_val].reset_index(drop=True)
    if len(gdf) == 0:
        continue

    # 每页最多50行
    page_size = 50
    for page_idx in range(0, len(gdf), page_size):
        page_df = gdf.iloc[page_idx:page_idx+page_size]
        page_num = page_idx // page_size + 1
        total_pages = (len(gdf) + page_size - 1) // page_size

        story.append(Paragraph(
            f'<font color="{color}">{title}</font> — 第{page_num}/{total_pages}页（共{len(gdf)}只）',
            styles['CH1']
        ))
        story.append(make_stock_table(page_df, grade_val, color))
        story.append(Spacer(1, 0.4*cm))

        if page_idx + page_size < len(gdf):
            story.append(PageBreak())

# ===== 附录：评分分档 =====
story.append(PageBreak())
story.append(Paragraph('📈 评分分档统计', styles['CH1']))

bins = [0, 55, 60, 65, 70, 75, 80, 85, 90, 200]
labels = ['<55', '55-60', '60-65', '65-70', '70-75', '75-80', '80-85', '85-90', '>90']
df['score_bin'] = pd.cut(df['最终分'], bins=bins, labels=labels, right=False)
score_dist = df['score_bin'].value_counts().reindex(labels).fillna(0)

dist_data = [['评分区间', '数量', '占比', '可视化']]
for label in labels:
    cnt = int(score_dist.get(label, 0))
    if cnt == 0:
        continue
    pct = cnt / total * 100
    bar_len = min(int(pct * 1.5), 60)
    bar = '█' * bar_len
    dist_data.append([str(label), str(cnt), f'{pct:.1f}%', bar])

dist_table = Table(dist_data, colWidths=[2.5*cm, 2*cm, 2*cm, 5.5*cm])
dist_table.setStyle(TableStyle([
    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#34495e')),
    ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
    ('FONTNAME', (0, 0), (-1, -1), chinese_font),
    ('FONTSIZE', (0, 0), (-1, -1), 9),
    ('ALIGN', (0, 0), (2, -1), 'CENTER'),
    ('ALIGN', (3, 0), (3, -1), 'LEFT'),
    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#f8f9fa'), colors.white]),
    ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#bdc3c7')),
    ('TOPPADDING', (0, 0), (-1, -1), 5),
    ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
]))
story.append(dist_table)
story.append(Spacer(1, 0.4*cm))

# 核心因子均值
story.append(Paragraph('🔬 核心因子均分（池内所有股票）', styles['CH1']))
factors = ['产业景气', '技术壁垒', '预期差', '业绩质量', '龙头地位', '机构认可', '市值弹性', '估值安全', '筹码面']
factor_means = {f: df[f].mean() for f in factors if f in df.columns}
sorted_factors = sorted(factor_means.items(), key=lambda x: x[1], reverse=True)

factor_data = [['因子名称', '平均分', '排名', '条形']]
for i, (f, v) in enumerate(sorted_factors, start=1):
    bar_len = min(int(v / 2), 30)
    bar = '▓' * bar_len
    factor_data.append([f, f'{v:.1f}', f'#{i}', bar])

factor_table = Table(factor_data, colWidths=[3.5*cm, 2.5*cm, 1.5*cm, 5*cm])
factor_table.setStyle(TableStyle([
    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#34495e')),
    ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
    ('FONTNAME', (0, 0), (-1, -1), chinese_font),
    ('FONTSIZE', (0, 0), (-1, -1), 9),
    ('ALIGN', (0, 0), (2, -1), 'CENTER'),
    ('ALIGN', (3, 0), (3, -1), 'LEFT'),
    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#f8f9fa'), colors.white]),
    ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#bdc3c7')),
    ('TOPPADDING', (0, 0), (-1, -1), 5),
    ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
]))
story.append(factor_table)
story.append(Spacer(1, 0.3*cm))

# 底部注释
story.append(Paragraph(
    '注：A级/B级为正式推荐池，观察名单为潜力观察池。产业景气/技术壁垒/预期差等为百分制因子分。',
    styles['Note']
))

# ===== 生成 =====
doc.build(story)
print(f'PDF已生成：{pdf_path}')
print(f'总股票数：{total}')
print(f'等级分布：{dict(grade_counts)}')
