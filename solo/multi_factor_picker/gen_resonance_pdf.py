# -*- coding: utf-8 -*-
"""重新生成二波形态精选PDF：按共振评分排序 + 带股票名称"""
import os, sys, datetime, time, json
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

OUT_DIR = r'D:\mystock\solo\multi_factor_picker\output'

# 读取JSON结果（wave2_pattern_scanner的输出）
json_files = [f for f in os.listdir(OUT_DIR) if f.startswith('wave2_pattern') and f.endswith('.json')]
if not json_files:
    json_files = [f for f in os.listdir(OUT_DIR) if f.endswith('.json')]
json_files.sort(key=lambda x: os.path.getmtime(os.path.join(OUT_DIR, x)), reverse=True)
print(f'JSON文件: {json_files[:5]}')

# 读取最新JSON
all_results = []
for jf in json_files[:3]:
    jp = os.path.join(OUT_DIR, jf)
    try:
        with open(jp, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list) and len(data) > 0 and 'score' in data[0]:
            all_results = data
            print(f'使用: {jf} ({len(data)}个信号)')
            break
    except:
        continue

# 如果JSON没有，从CSV读取
if not all_results:
    csv_path = os.path.join(OUT_DIR, 'wave2_daily_20260623.csv')
    df = pd.read_csv(csv_path)
    print(f'从CSV读取: {len(df)}行')

    # 转换为统一格式
    for _, row in df.iterrows():
        all_results.append({
            'ts_code': row['ts_code'],
            'name': row.get('name', ''),
            'pattern': row.get('pattern', ''),
            'score': row.get('base_score', 0),
            'wave1_gain': row.get('wave1_gain', 0),
            'pullback_pct': row.get('pullback', 0),
            'adjust_days': row.get('pullback_days', 0),
            'rsi': row.get('rsi_now', 0),
            'entry_date': '20260623',
            'entry_price': row.get('entry_price', 0),
            'stop_loss': row.get('stop_price', 0),
            'target': row.get('target_price', 0),
            'rr': row.get('rr_ratio', 0),
        })

# 获取股票名称
codes = list(set(r['ts_code'] for r in all_results))
print(f'获取{len(codes)}只股票名称...')
name_map = {}
try:
    all_basic = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name')
    if all_basic is not None:
        for _, row in all_basic.iterrows():
            name_map[row['ts_code']] = row['name']
        print(f'从stock_basic获取{len(name_map)}只')
except:
    pass

for r in all_results:
    if not r.get('name') or r['name'] == r['ts_code']:
        r['name'] = name_map.get(r['ts_code'], r['ts_code'][:6])

# 按共振评分降序排序
all_results.sort(key=lambda x: x.get('score', 0), reverse=True)
print(f'\n排序后TOP5:')
for r in all_results[:5]:
    print(f"  {r['ts_code']} {r['name']} 评分{r['score']} {r['pattern']}")

# 生成PDF
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

font_name = 'Helvetica'
for fp in [r'C:\Windows\Fonts\simhei.ttf', r'C:\Windows\Fonts\msyh.ttc']:
    if os.path.exists(fp):
        try:
            pdfmetrics.registerFont(TTFont('CNFont', fp))
            font_name = 'CNFont'
            break
        except:
            continue

ts_str = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
pdf_path = os.path.join(OUT_DIR, f'wave2_resonance_{ts_str}.pdf')

doc = SimpleDocTemplate(pdf_path, pagesize=landscape(A4),
    topMargin=12*mm, bottomMargin=12*mm, leftMargin=8*mm, rightMargin=8*mm)

styles = getSampleStyleSheet()
title_style = ParagraphStyle('TCN', parent=styles['Title'],
    fontName=font_name, fontSize=16, alignment=1, spaceAfter=4*mm)
sub_style = ParagraphStyle('SCN', parent=styles['Normal'],
    fontName=font_name, fontSize=9, alignment=1, spaceAfter=3*mm)
hdr_style = ParagraphStyle('HCN', parent=styles['Normal'],
    fontName=font_name, fontSize=8, alignment=1)
cel_style = ParagraphStyle('CCN', parent=styles['Normal'],
    fontName=font_name, fontSize=7.5, alignment=1)
note_style = ParagraphStyle('NCN', parent=styles['Normal'],
    fontName=font_name, fontSize=8, textColor=colors.HexColor('#888888'))

elements = []
elements.append(Paragraph('二波形态精选扫描报告 (按共振评分排序)', title_style))

# 统计
pc = {}
for r in all_results:
    p = r['pattern']
    pc[p] = pc.get(p, 0) + 1
summary = f"信号: {len(all_results)}个 | " + " | ".join(f"{p}: {c}只" for p, c in sorted(pc.items(), key=lambda x: -x[1]))
elements.append(Paragraph(summary, sub_style))
elements.append(Spacer(1, 2*mm))

# 表格
headers = ['排名', '股票代码', '股票名称', '形态', '共振评分', '一波涨幅%', '回调%', '调整天数', 'RSI', '入场价', '止损价', '目标价', '盈亏比']
col_widths = [10*mm, 22*mm, 20*mm, 16*mm, 14*mm, 14*mm, 12*mm, 14*mm, 10*mm, 16*mm, 16*mm, 16*mm, 12*mm]

data_rows = [[Paragraph(h, hdr_style) for h in headers]]
for idx, r in enumerate(all_results):
    rank = idx + 1
    data_rows.append([
        Paragraph(str(rank), cel_style),
        Paragraph(r['ts_code'], cel_style),
        Paragraph(str(r.get('name', '')), cel_style),
        Paragraph(r['pattern'], cel_style),
        Paragraph(f"{r['score']}", cel_style),
        Paragraph(f"+{r['wave1_gain']:.1f}", cel_style),
        Paragraph(f"{r['pullback_pct']:.1f}", cel_style),
        Paragraph(f"{r.get('adjust_days', 0)}", cel_style),
        Paragraph(f"{r['rsi']:.1f}", cel_style),
        Paragraph(f"{r['entry_price']:.2f}", cel_style),
        Paragraph(f"{r['stop_loss']:.2f}", cel_style),
        Paragraph(f"{r['target']:.2f}", cel_style),
        Paragraph(f"{r['rr']:.1f}x", cel_style),
    ])

t = Table(data_rows, colWidths=col_widths, repeatRows=1)
style_cmds = [
    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a5276')),
    ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
    ('FONTSIZE', (0, 0), (-1, 0), 9),
    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f5f5f5')]),
    ('TOPPADDING', (0, 0), (-1, -1), 3),
    ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
]
# TOP3绿色
for i in range(min(3, len(all_results))):
    style_cmds.append(('BACKGROUND', (0, i+1), (-1, i+1), colors.HexColor('#d4efdf')))
# 评分>=15深绿高亮
for i in range(len(all_results)):
    score = all_results[i].get('score', 0)
    if score >= 20:
        style_cmds.append(('BACKGROUND', (0, i+1), (-1, i+1), colors.HexColor('#a9dfbf')))
    elif score >= 15:
        style_cmds.append(('BACKGROUND', (0, i+1), (-1, i+1), colors.HexColor('#d5f5e3')))

t.setStyle(TableStyle(style_cmds))
elements.append(t)
elements.append(Spacer(1, 4*mm))
elements.append(Paragraph("* 绿色高亮 = 评分>=15分 | 深绿 = 评分>=20分", note_style))
elements.append(Paragraph(f"* 生成: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}", note_style))

doc.build(elements)
sz = os.path.getsize(pdf_path)
print(f'\nPDF已生成: {pdf_path}')
print(f'文件大小: {sz} bytes')
