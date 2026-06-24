# -*- coding: utf-8 -*-
"""重新生成二波形态精选扫描报告：带股票名称 + 按评分排序"""
import os, sys, datetime
sys.path.insert(0, r'D:\mystock')
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'

import pandas as pd
import tushare as ts
ts.set_token(os.environ['TUSHARE_TOKEN'])
pro = ts.pro_api()

# 读取最新的bull_stocks.csv
csv_path = r'D:\mystock\solo\multi_factor_picker\output\bull_stocks.csv'
df = pd.read_csv(csv_path)
print(f'原始信号: {len(df)}个')

# 获取股票名称
codes = df['ts_code'].unique().tolist()
print(f'获取{len(codes)}只股票名称...')
name_map = {}
batch_size = 50
for i in range(0, len(codes), batch_size):
    batch = codes[i:i+batch_size]
    for code in batch:
        try:
            info = pro.stock_basic(ts_code=code, fields='ts_code,name')
            if info is not None and len(info) > 0:
                name_map[code] = info.iloc[0]['name']
        except:
            pass
    import time
    time.sleep(0.3)

df['name'] = df['ts_code'].map(name_map).fillna('')

# 按评分降序排序
df = df.sort_values('score', ascending=False).reset_index(drop=True)

print(f'信号数: {len(df)}')
print(f'有名称: {(df["name"] != "").sum()}')

# 生成PDF
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# 注册中文字体
font_paths = [
    r'C:\Windows\Fonts\simhei.ttf',
    r'C:\Windows\Fonts\msyh.ttc',
]
font_registered = False
for fp in font_paths:
    if os.path.exists(fp):
        try:
            pdfmetrics.registerFont(TTFont('SimHei', fp))
            font_registered = True
            print(f'注册字体: {fp}')
            break
        except:
            continue

if not font_registered:
    print('警告: 未找到中文字体')

FONT = 'SimHei' if font_registered else 'Helvetica'

ts_str = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
pdf_path = f'D:\\mystock\\solo\\multi_factor_picker\\output\\wave2_bull_stocks_scored_{ts_str}.pdf'

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

# 标题
title = Paragraph('二波形态精选扫描报告 (按评分排序)', title_style)
elements.append(title)

# 统计信息
sideways = len(df[df.get('pattern', df.get('形态', '')) == '强势横盘']) if 'pattern' in df.columns or '形态' in df.columns else 0
deep = len(df) - sideways
stats_text = f'扫描日期: {datetime.date.today().strftime("%Y%m%d")} | 信号: {len(df)}个 | 深度回调: {deep}只 | 强势横盘: {sideways}只 | 按共振评分降序排列'
stats = Paragraph(stats_text, subtitle_style)
elements.append(stats)
elements.append(Spacer(1, 8))

# 表格数据
header = ['排名', '股票代码', '股票名称', '形态', '评分', '一波涨幅%', '调整幅度%', '调整天数', 'RSI', '入场价', '止损价', '目标价', '盈亏比']

table_data = [header]
for idx, row in df.iterrows():
    rank = idx + 1

    # 兼容不同列名
    pattern = row.get('pattern', row.get('形态', ''))
    score = row.get('score', row.get('共振评分', 0))
    wave1_gain = row.get('wave1_gain', row.get('一波涨幅', 0))
    pullback = row.get('pullback_pct', row.get('调整幅度', 0))
    adjust_days = row.get('adjust_days', row.get('调整天数', 0))
    rsi = row.get('adjust_rsi', row.get('RSI', 0))
    entry = row.get('entry_price', row.get('入场价', 0))
    stop = row.get('stop_loss', row.get('止损价', 0))
    target = row.get('target_price', row.get('目标价', 0))
    rr = row.get('rr_ratio', row.get('盈亏比', 0))

    name = row.get('name', row.get('股票名称', ''))

    table_data.append([
        str(rank),
        str(row.get('ts_code', row.get('股票代码', ''))),
        str(name),
        str(pattern),
        f'{score:.0f}' if isinstance(score, (int, float)) else str(score),
        f'+{wave1_gain:.1f}' if isinstance(wave1_gain, (int, float)) else str(wave1_gain),
        f'{pullback:.1f}' if isinstance(pullback, (int, float)) else str(pullback),
        f'{adjust_days:.0f}' if isinstance(adjust_days, (int, float)) else str(adjust_days),
        f'{rsi:.1f}' if isinstance(rsi, (int, float)) else str(rsi),
        f'{entry:.2f}' if isinstance(entry, (int, float)) else str(entry),
        f'{stop:.2f}' if isinstance(stop, (int, float)) else str(stop),
        f'{target:.2f}' if isinstance(target, (int, float)) else str(target),
        f'{rr:.1f}x' if isinstance(rr, (int, float)) else str(rr),
    ])

# 表格样式
col_widths = [30, 75, 60, 55, 35, 55, 55, 45, 40, 50, 50, 50, 40]

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

# 评分>=15高亮
for i in range(1, len(table_data)):
    try:
        score_val = float(table_data[i][4])
        if score_val >= 20:
            style_cmds.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#abebc6')))
        elif score_val >= 15:
            style_cmds.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#d5f5e3')))
    except:
        pass

table.setStyle(TableStyle(style_cmds))
elements.append(table)

# 页脚
footer_text = '* 绿色高亮 = 评分>=15分 | 深绿 = 评分>=20分 | 生成: ' + datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
footer = Paragraph(footer_text, subtitle_style)
elements.append(Spacer(1, 6))
elements.append(footer)

doc.build(elements)
print(f'\nPDF已生成: {pdf_path}')
print(f'文件大小: {os.path.getsize(pdf_path)} bytes')
