# -*- coding: utf-8 -*-
"""从最新 entry_precision CSV 生成 PDF 报告（自动找最新CSV）"""
import os, glob, pandas as pd
from datetime import datetime

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER

# === 字体注册 ===
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

# === 自动找最新CSV ===
csv_dir = r'D:\mystock\solo\trend_feature_output'
csv_pattern = os.path.join(csv_dir, 'entry_precision_*_qualified.csv')
csv_files = glob.glob(csv_pattern)
if not csv_files:
    print('错误：未找到CSV文件')
    exit(1)
csv_path = max(csv_files, key=os.path.getmtime)
print(f'使用CSV：{csv_path}')

# === 读取数据 ===
df = pd.read_csv(csv_path, dtype={'ts_code': str})
print(f'CSV共 {len(df)} 条信号')

if len(df) == 0:
    print('CSV无数据，跳过PDF生成')
    exit(0)

# === 获取股票名称映射 ===
name_map = {}
# 来源1：bull_stocks CSV
bull_csv = r'D:\mystock\solo\multi_factor_picker\output\bull_stocks_20260629_235153.csv'
if os.path.exists(bull_csv):
    try:
        bdf = pd.read_csv(bull_csv, dtype={'code': str})
        for _, row in bdf.iterrows():
            code = str(row['code']).strip().zfill(6)
            name = str(row['name']).strip()
            if name and name != 'nan':
                name_map[code] = name
    except:
        pass

# 来源2：Tushare补全
try:
    import tushare as ts
    from dotenv import load_dotenv
    import os as _os
    load_dotenv(r'D:\mystock\config\.env')
    token = _os.getenv('TUSHARE_TOKEN')
    if token:
        ts.set_token(token)
        pro = ts.pro_api()
        stock_basic = pro.stock_basic(exchange='', list_status='L', fields='ts_code,symbol,name')
        for _, row in stock_basic.iterrows():
            code = str(row['symbol']).zfill(6)
            name = str(row['name']).strip()
            if code not in name_map and name:
                name_map[code] = name
except Exception as e:
    print(f'Tushare名称补全失败（已跳过）：{e}')

def format_code(ts_code):
    return str(ts_code).strip().split('.')[0].zfill(6)

# 添加名称列
df['name'] = df['ts_code'].apply(lambda x: name_map.get(format_code(x), ''))
print(f'名称覆盖率：{df["name"].ne("").sum()}/{len(df)}')

# === 生成PDF ===
output_dir = r'D:\mystock\solo\multi_factor_picker\output'
os.makedirs(output_dir, exist_ok=True)
timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
pdf_path = os.path.join(output_dir, f'entry_precision_report_{timestamp}.pdf')

doc = SimpleDocTemplate(pdf_path, pagesize=A4,
                        rightMargin=1.5*cm, leftMargin=1.5*cm,
                        topMargin=2*cm, bottomMargin=2*cm)
styles = getSampleStyleSheet()
style_title = ParagraphStyle('Title', fontName=chinese_font, fontSize=16,
                            leading=20, alignment=TA_CENTER, spaceAfter=12)
style_heading = ParagraphStyle('Heading', fontName=chinese_font, fontSize=12,
                              leading=16, spaceBefore=12, spaceAfter=6)
style_normal = ParagraphStyle('Normal', fontName=chinese_font, fontSize=9,
                              leading=12)

story = []
story.append(Paragraph('趋势精准入场信号报告', style_title))
story.append(Paragraph(f'生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M")}', style_normal))
story.append(Spacer(1, 0.3*cm))

# 统计摘要
story.append(Paragraph('一、策略统计', style_heading))
stats_data = [['指标', '数值']]
stats_data.append(['信号数', str(len(df))])
stats_data.append(['+1d平均收益', f"{df['return_1d'].mean():.2f}%"])
stats_data.append(['+1d胜率', f"{(df['return_1d']>0).sum()/len(df)*100:.0f}%"])
stats_data.append(['+10d平均收益', f"{df['return_10d'].mean():.2f}%"])
stats_data.append(['+10d胜率', f"{(df['return_10d']>0).sum()/len(df)*100:.0f}%"])
stats_table = Table(stats_data, colWidths=[4*cm, 4*cm])
stats_table.setStyle(TableStyle([
    ('FONTNAME', (0,0), (-1,-1), chinese_font),
    ('FONTSIZE', (0,0), (-1,-1), 9),
    ('BACKGROUND', (0,0), (-1,0), colors.grey),
    ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
    ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
]))
story.append(stats_table)
story.append(Spacer(1, 0.3*cm))

# TOP5信号
if len(df) >= 5:
    story.append(Paragraph('二、TOP5信号（按+10d收益排序）', style_heading))
    top5 = df.nlargest(5, 'return_10d')
    top5_data = [['排名', '代码', '名称', '信号日', '评分', '+10d', '+20d']]
    for i, (_, row) in enumerate(top5.iterrows()):
        top5_data.append([
            str(i+1),
            format_code(row['ts_code']),
            row['name'] if row['name'] else '-',
            row['signal_date'],
            str(row['entry_score']),
            f"{row['return_10d']:+.2f}%",
            f"{row['return_20d']:+.2f}%" if pd.notna(row['return_20d']) else '-',
        ])
    top5_table = Table(top5_data, colWidths=[1.5*cm, 2*cm, 3*cm, 2.5*cm, 1.5*cm, 2*cm, 2*cm])
    top5_table.setStyle(TableStyle([
        ('FONTNAME', (0,0), (-1,-1), chinese_font),
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('BACKGROUND', (0,0), (-1,0), colors.darkblue),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
    ]))
    story.append(top5_table)
    story.append(Spacer(1, 0.3*cm))

# 完整信号列表
story.append(Paragraph('三、完整信号列表', style_heading))
full_data = [['代码', '名称', '信号日', '评分', 'RSI6', 'return_1d', 'return_10d']]
for _, row in df.iterrows():
    full_data.append([
        format_code(row['ts_code']),
        row['name'] if row['name'] else '-',
        row['signal_date'],
        str(row['entry_score']),
        f"{row['rsi6']:.1f}",
        f"{row['return_1d']:+.2f}%" if pd.notna(row['return_1d']) else '-',
        f"{row['return_10d']:+.2f}%" if pd.notna(row['return_10d']) else '-',
    ])
full_table = Table(full_data, colWidths=[2*cm, 3*cm, 2.5*cm, 1.5*cm, 1.5*cm, 2*cm, 2*cm])
full_table.setStyle(TableStyle([
    ('FONTNAME', (0,0), (-1,-1), chinese_font),
    ('FONTSIZE', (0,0), (-1,-1), 7),
    ('BACKGROUND', (0,0), (-1,0), colors.grey),
    ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
    ('GRID', (0,0), (-1,-1), 0.3, colors.grey),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.lightgrey]),
]))
story.append(full_table)

doc.build(story)
print(f'PDF已生成：{pdf_path}')
print(f'共 {len(df)} 条信号')
