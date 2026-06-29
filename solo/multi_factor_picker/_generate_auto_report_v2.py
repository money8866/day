# -*- coding: utf-8 -*-
"""生成基本面信息PDF报告 v2（修复内容混乱问题）"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

import pandas as pd
import re
from datetime import datetime
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
import os

print('生成PDF报告 v2（修复版）...')

# 注册中文字体
for font_path in [r'C:\Windows\Fonts\simhei.ttf', r'C:\Windows\Fonts\msyh.ttc']:
    if os.path.exists(font_path):
        try:
            pdfmetrics.registerFont(TTFont('ChineseFont', font_path))
            break
        except:
            continue

chinese_font = 'ChineseFont'

# 读取数据
df = pd.read_csv(r'D:\mystock\solo\multi_factor_picker\output\basic_info_auto_20260629.csv')

def parse_list_string(s):
    """解析类似Python列表的字符串"""
    s = str(s)
    s = s.strip().strip('[]')
    items = []
    current = ''
    in_quote = False
    for ch in s:
        if ch == "'" and not in_quote:
            in_quote = True
        elif ch == "'" and in_quote:
            in_quote = False
            items.append(current)
            current = ''
        elif in_quote:
            current += ch
    return items

def clean_html(text):
    """清理HTML标签"""
    text = re.sub(r'<[^>]+>', '', text)
    return text

def format_stock_code(code):
    """格式化股票代码"""
    code = int(code) if not isinstance(code, str) else int(re.sub(r'\D', '', code))
    return f"{code:06d}"

# 构建干净的数据行
clean_rows = []
for i, (idx, row) in enumerate(df.iterrows(), 1):
    title_list = parse_list_string(row['title'])
    category_list = parse_list_string(row['category'])
    
    # 取第一条记录
    main_title = clean_html(title_list[0]) if title_list else 'N/A'
    main_category = category_list[0] if category_list else 'N/A'
    
    # 汇总所有标题（去重）
    all_titles = list(dict.fromkeys([clean_html(t) for t in title_list]))
    
    clean_rows.append({
        'rank': i,
        'ts_code': format_stock_code(row['ts_code']),
        'weight': row['weight'],
        'category': main_category,
        'count': len(title_list),
        'main_title': main_title,
        'all_titles': all_titles,
        'ann_date': str(row['ann_date'])[:8],
    })

print(f'清洗完成：{len(clean_rows)}条记录')
for r in clean_rows:
    print(f'  {r["rank"]}. {r["ts_code"]} - {r["weight"]}分 - {r["main_title"][:40]}')

# 创建PDF
pdf_path = r'D:\mystock\solo\multi_factor_picker\output\fundamental_info_auto_20260629.pdf'
doc = SimpleDocTemplate(pdf_path, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm,
                       topMargin=2*cm, bottomMargin=2*cm)

# 样式
styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name='CTitle', fontName=chinese_font, fontSize=18,
                          spaceAfter=30, alignment=TA_CENTER, textColor=colors.HexColor('#1a1a1a')))
styles.add(ParagraphStyle(name='CH1', fontName=chinese_font, fontSize=14,
                          spaceAfter=12, spaceBefore=12, textColor=colors.HexColor('#2c3e50')))
styles.add(ParagraphStyle(name='CBody', fontName=chinese_font, fontSize=10,
                          spaceAfter=6, leading=14))
styles.add(ParagraphStyle(name='CSmall', fontName=chinese_font, fontSize=8,
                          spaceAfter=4, leading=11, textColor=colors.HexColor('#555555')))

story = []

# 标题
story.append(Paragraph('基本面重要信息日报', styles['CTitle']))
story.append(Paragraph(f'{datetime.now().strftime("%Y年%m月%d日")}（自动挖掘版）', styles['CTitle']))
story.append(Spacer(1, 0.5*cm))

# 概览
story.append(Paragraph('挖掘概览', styles['CH1']))
story.append(Paragraph(f'• 数据源：巨潮资讯网（自动爬取）', styles['CBody']))
story.append(Paragraph(f'• 时间范围：最近7天', styles['CBody']))
story.append(Paragraph(f'• 扫描股票：50只（TOP50 BullScore）', styles['CBody']))
story.append(Paragraph(f'• 发现重要信息：{len(clean_rows)}条', styles['CBody']))
story.append(Spacer(1, 0.5*cm))

# 重点推荐（评分≥10）
high_quality = [r for r in clean_rows if r['weight'] >= 10]
if high_quality:
    story.append(Paragraph('⭐ 重点推荐', styles['CH1']))
    for r in high_quality:
        story.append(Paragraph(f'<b>{r["ts_code"]}</b> - {r["main_title"][:50]}', styles['CBody']))
        story.append(Paragraph(f'   评分：{r["weight"]}分 | 类别：{r["category"]} | 关联公告：{r["count"]}篇', styles['CSmall']))
    story.append(Spacer(1, 0.3*cm))

# 详细信息表格
story.append(Paragraph('完整信息列表', styles['CH1']))

table_data = [['排名', '代码', '评分', '类别', '公告数', '主要标题']]
for r in clean_rows:
    table_data.append([
        str(r['rank']), r['ts_code'], f"{r['weight']}分",
        r['category'], str(r['count']), r['main_title'][:25]
    ])

table = Table(table_data, colWidths=[1*cm, 2*cm, 1.3*cm, 2*cm, 1.3*cm, 6.5*cm])
table.setStyle(TableStyle([
    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#27ae60')),
    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ('ALIGN', (5, 1), (5, -1), 'LEFT'),
    ('FONTNAME', (0, 0), (-1, -1), chinese_font),
    ('FONTSIZE', (0, 0), (-1, 0), 9),
    ('FONTSIZE', (0, 1), (-1, -1), 8),
    ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
    ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#ecf0f1')),
    ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#bdc3c7')),
]))
story.append(table)

story.append(Spacer(1, 0.5*cm))

# 分类统计
story.append(Paragraph('信息类别分布', styles['CH1']))
category_count = {}
for r in clean_rows:
    cat = r['category']
    category_count[cat] = category_count.get(cat, 0) + 1

cat_table = [['类别', '数量', '占比']]
for cat, count in sorted(category_count.items(), key=lambda x: x[1], reverse=True):
    pct = f"{count/len(clean_rows)*100:.0f}%"
    cat_table.append([cat, str(count), pct])

cat_t = Table(cat_table, colWidths=[4*cm, 4*cm, 4*cm])
cat_t.setStyle(TableStyle([
    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#3498db')),
    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ('FONTNAME', (0, 0), (-1, -1), chinese_font),
    ('FONTSIZE', (0, 0), (-1, -1), 9),
    ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#bdc3c7')),
]))
story.append(cat_t)

story.append(Spacer(1, 1*cm))
story.append(Paragraph(f'报告生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M")}', styles['CSmall']))

# 生成PDF
doc.build(story)

print(f'\nPDF已生成: {pdf_path}')
