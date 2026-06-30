# -*- coding: utf-8 -*-
"""生成二波形态精选报告：带股票名称 + 按评分排序"""
import os, sys, datetime, time
sys.path.insert(0, r'D:\mystock')
if 'TUSHARE_TOKEN' not in os.environ:
    for _l in open(r'D:\mystock\config\.env'):
        if _l.strip().startswith('TUSHARE_TOKEN='):
            os.environ['TUSHARE_TOKEN'] = _l.strip().split('=', 1)[1].strip().strip('"')
            break

import pandas as pd
import tushare as ts
ts.set_token(os.environ['TUSHARE_TOKEN'])
pro = ts.pro_api()

# 读取数据
csv_path = r'D:\mystock\solo\multi_factor_picker\output\wave2_daily_20260623.csv'
df = pd.read_csv(csv_path)
print(f'原始信号: {len(df)}个')

# 获取股票名称（批量）
codes = df['ts_code'].unique().tolist()
print(f'获取{len(codes)}只股票名称...')
name_map = {}
try:
    all_basic = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name')
    if all_basic is not None and len(all_basic) > 0:
        for _, row in all_basic.iterrows():
            name_map[row['ts_code']] = row['name']
        print(f'从stock_basic获取{len(name_map)}只')
except Exception as e:
    print(f'stock_basic失败: {e}')

df['stock_name'] = df['ts_code'].map(name_map).fillna(df['ts_code'])
missing = df[df['stock_name'] == df['ts_code']]['ts_code'].unique()
if len(missing) > 0:
    for code in missing:
        try:
            info = pro.stock_basic(ts_code=code, fields='ts_code,name')
            if info is not None and len(info) > 0:
                name_map[code] = info.iloc[0]['name']
                time.sleep(0.08)
        except:
            pass
    df['stock_name'] = df['ts_code'].map(name_map).fillna(df['ts_code'])

named_count = (df['stock_name'] != df['ts_code']).sum()
print(f'成功获取名称: {named_count}/{len(df)}')

# 按base_score降序排序
df = df.sort_values('base_score', ascending=False).reset_index(drop=True)

# 生成PDF
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

font_registered = False
for fp in [r'C:\Windows\Fonts\simhei.ttf', r'C:\Windows\Fonts\msyh.ttc']:
    if os.path.exists(fp):
        try:
            pdfmetrics.registerFont(TTFont('SimHei', fp))
            font_registered = True
            break
        except:
            continue

FONT = 'SimHei' if font_registered else 'Helvetica'

ts_str = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
pdf_path = f'D:\\mystock\\solo\\multi_factor_picker\\output\\wave2_scored_{ts_str}.pdf'

doc = SimpleDocTemplate(
    pdf_path,
    pagesize=landscape(A4),
    topMargin=15, bottomMargin=15, leftMargin=15, rightMargin=15
)

styles = getSampleStyleSheet()
title_style = styles['Title']
title_style.fontName = FONT
title_style.fontSize = 16
subtitle_style = styles['Normal']
subtitle_style.fontName = FONT
subtitle_style.fontSize = 9

elements = []

title = Paragraph('二波形态精选扫描报告 (按评分排序)', title_style)
elements.append(title)

sideways = len(df[df['pattern'] == '强势横盘'])
deep = len(df[df['pattern'] == '深度回调'])
stats_text = f'扫描日期: 20260623 | 信号: {len(df)}个 | 深度回调: {deep}只 | 强势横盘: {sideways}只 | 按base_score降序'
stats = Paragraph(stats_text, subtitle_style)
elements.append(stats)
elements.append(Spacer(1, 8))

# 表格
header = ['排名', '代码', '名称', '形态', '评分', '一波%', '调整%', '天数', 'RSI', '入场价', '止损', '目标价', '盈亏比']

table_data = [header]
for idx, row in df.iterrows():
    rank = idx + 1
    table_data.append([
        str(rank),
        str(row['ts_code']).replace('.SH','').replace('.SZ',''),
        str(row['stock_name']),
        str(row.get('pattern', '')),
        f"{row.get('base_score', 0):.0f}",
        f"+{row.get('wave1_gain', 0):.1f}",
        f"{row.get('pullback', 0):.1f}",
        f"{row.get('pullback_days', 0):.0f}",
        f"{row.get('rsi_now', 0):.1f}",
        f"{row.get('entry_price', 0):.2f}",
        f"{row.get('stop_price', 0):.2f}",
        f"{row.get('target_price', 0):.2f}",
        f"{row.get('rr_ratio', 0):.1f}x",
    ])

col_widths = [30, 55, 55, 55, 35, 45, 45, 35, 40, 50, 50, 50, 40]

table = Table(table_data, colWidths=col_widths, repeatRows=1)

style_cmds = [
    ('FONTNAME', (0, 0), (-1, -1), FONT),
    ('FONTSIZE', (0, 0), (-1, 0), 8),
    ('FONTSIZE', (0, 1), (-1, -1), 7),
    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ('GRID', (0, 0), (-1, -1), 0.3, colors.grey),
    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a5276')),
    ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#eaf2f8')]),
    # TOP3高亮
    ('BACKGROUND', (0, 1), (-1, 3), colors.HexColor('#d5f5e3')),
]

# 评分>=98高亮
for i in range(1, len(table_data)):
    try:
        score_val = float(table_data[i][4])
        if score_val >= 98:
            style_cmds.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#abebc6')))
    except:
        pass

table.setStyle(TableStyle(style_cmds))
elements.append(table)

footer_text = '* 绿色高亮 = 评分>=98分 | 生成: ' + datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
footer = Paragraph(footer_text, subtitle_style)
elements.append(Spacer(1, 6))
elements.append(footer)

doc.build(elements)
print(f'\nPDF已生成: {pdf_path}')
print(f'文件大小: {os.path.getsize(pdf_path)} bytes')
