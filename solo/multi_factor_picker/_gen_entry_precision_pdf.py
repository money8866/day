# -*- coding: utf-8 -*-
"""从 entry_precision CSV 生成 PDF 报告（含股票名称）"""
import os
import pandas as pd
from datetime import datetime

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
from reportlab.lib.enums import TA_CENTER

# === 获取股票名称映射 ===
def get_name_map():
    """从多个来源获取 code→name 映射"""
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

    # 来源2：Tushare 补全（从 .env 加载 token）
    try:
        import tushare as ts
        from dotenv import load_dotenv
        import os as _os
        load_dotenv(r'D:\mystock\config\.env')
        token = _os.getenv('TUSHARE_TOKEN')
        if token:
            ts.set_token(token)
            pro = ts.pro_api()
            # 获取全部A股名称
            stock_basic = pro.stock_basic(exchange='', list_status='L', fields='ts_code,symbol,name')
            for _, row in stock_basic.iterrows():
                code = str(row['symbol']).zfill(6)
                name = str(row['name']).strip()
                if code not in name_map and name:
                    name_map[code] = name
    except Exception as e:
        print(f'Tushare名称补全失败（已跳过）：{e}')

    return name_map

def format_code(ts_code):
    """从 ts_code（如 600027.SH）提取纯代码"""
    s = str(ts_code).strip()
    return s.split('.')[0]

# === 配置 ===
csv_path = r'D:\mystock\solo\trend_feature_output\entry_precision_20260629_224320_qualified.csv'
output_dir = r'D:\mystock\solo\multi_factor_picker\output'
os.makedirs(output_dir, exist_ok=True)
ts = datetime.now().strftime('%Y%m%d_%H%M%S')
pdf_path = os.path.join(output_dir, f'entry_precision_report_{ts}.pdf')

# === 读取数据 ===
df = pd.read_csv(csv_path, dtype={'ts_code': str})
df['signal_date'] = df['signal_date'].astype(str)
# 按日期倒序（最新在前），同日期内按评分倒序
df = df.sort_values(['signal_date', 'entry_score'], ascending=[False, False]).reset_index(drop=True)

# 加入股票名称
name_map = get_name_map()
df['name'] = df['ts_code'].apply(lambda x: name_map.get(format_code(x), ''))

# === 创建PDF ===
doc = SimpleDocTemplate(pdf_path, pagesize=A4,
                        rightMargin=0.8*cm, leftMargin=0.8*cm,
                        topMargin=1.8*cm, bottomMargin=1.8*cm)

styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name='CTitle', fontName=chinese_font, fontSize=15, spaceAfter=4,
                          alignment=TA_CENTER, textColor=colors.HexColor('#1a1a1a')))
styles.add(ParagraphStyle(name='CSubtitle', fontName=chinese_font, fontSize=9, spaceAfter=10,
                          alignment=TA_CENTER, textColor=colors.HexColor('#888888')))
styles.add(ParagraphStyle(name='CH1', fontName=chinese_font, fontSize=11, spaceAfter=6,
                          spaceBefore=10, textColor=colors.HexColor('#2c3e50')))
styles.add(ParagraphStyle(name='CBullet', fontName=chinese_font, fontSize=9, spaceAfter=3, leftIndent=15, leading=13))

story = []

# ===== 标题 =====
story.append(Paragraph('📈 趋势入场精度信号报告', styles['CTitle']))
data_date = str(df['signal_date'].iloc[0]) if len(df) > 0 else '20260629'
display_date = data_date[:4] + '-' + data_date[4:6] + '-' + data_date[6:8]
story.append(Paragraph(f'数据日期：{display_date}  生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M")}', styles['CSubtitle']))
story.append(Spacer(1, 0.2*cm))

# ===== 概览统计 =====
total = len(df)
avg_score = df['entry_score'].mean()
avg_ret1  = df['return_1d'].mean()
avg_ret5  = df['return_5d'].mean()
avg_ret10 = df['return_10d'].mean()
avg_ret20 = df['return_20d'].mean()
pos_1d  = (df['return_1d']  > 0).sum()
pos_5d  = (df['return_5d']  > 0).sum()
pos_10d = (df['return_10d'] > 0).sum()
pos_20d = (df['return_20d'] > 0).sum()

story.append(Paragraph('📊 信号概览', styles['CH1']))
story.append(Paragraph(f'• 信号总数：<b>{total}</b> 只', styles['CBullet']))
story.append(Paragraph(f'• 平均入场评分：{avg_score:.1f} 分', styles['CBullet']))
story.append(Paragraph(f'• 1日收益：{avg_ret1:.2f}%（正收益 {pos_1d}/{total}，{pos_1d/total*100:.1f}%）', styles['CBullet']))
story.append(Paragraph(f'• 5日收益：{avg_ret5:.2f}%（正收益 {pos_5d}/{total}，{pos_5d/total*100:.1f}%）', styles['CBullet']))
story.append(Paragraph(f'• 10日收益：{avg_ret10:.2f}%（正收益 {pos_10d}/{total}，{pos_10d/total*100:.1f}%）', styles['CBullet']))
story.append(Paragraph(f'• 20日收益：{avg_ret20:.2f}%（正收益 {pos_20d}/{total}，{pos_20d/total*100:.1f}%）', styles['CBullet']))
story.append(Spacer(1, 0.3*cm))

# 信号日期分布
date_counts = df['signal_date'].value_counts().sort_index()
story.append(Paragraph('信号日期分布', styles['CH1']))
for dt, cnt in date_counts.items():
    d = str(dt)[:4] + '-' + str(dt)[4:6] + '-' + str(dt)[6:8]
    story.append(Paragraph(f'• {d}：{cnt} 只', styles['CBullet']))
story.append(Spacer(1, 0.3*cm))

story.append(PageBreak())

# ===== 个股明细表格 =====
story.append(Paragraph('📋 个股信号明细（按入场评分降序）', styles['CH1']))
story.append(Spacer(1, 0.2*cm))

def make_detail_table(stocks_df):
    rows = []
    # 表头增加"名称"列
    header = ['代码', '名称', '信号日', '评分', '连涨', '涨幅%', '量比', 'RSI6', '1日%', '5日%', '10日%', '20日%']
    rows.append(header)

    for _, row in stocks_df.iterrows():
        code = format_code(row['ts_code'])
        name = str(row['name'])[:6] if row['name'] else '-'
        sig_date = str(row['signal_date'])
        sig_date_fmt = sig_date[:4] + '-' + sig_date[4:6] + '-' + sig_date[6:8]
        score    = str(int(row['entry_score']))
        cons_up = str(int(row['consecutive_up']))
        pct      = f"{row['pct_chg']:.2f}"
        vr       = f"{row['vol_ratio']:.2f}"
        rsi      = f"{row['rsi6']:.1f}"
        r1       = f"{row['return_1d']:.2f}"
        r5       = f"{row['return_5d']:.2f}"
        r10      = f"{row['return_10d']:.2f}"
        r20      = f"{row['return_20d']:.2f}"
        rows.append([code, name, sig_date_fmt, score, cons_up, pct, vr, rsi, r1, r5, r10, r20])

    col_widths = [1.8*cm, 2.0*cm, 1.8*cm, 1.3*cm, 1.2*cm,
                   1.4*cm, 1.4*cm, 1.4*cm, 1.5*cm, 1.5*cm, 1.5*cm, 1.5*cm]

    tbl = Table(rows, colWidths=col_widths, repeatRows=1)
    style = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c3e50')),
        ('TEXTCOLOR',  (0, 0), (-1, 0), colors.white),
        ('FONTNAME',   (0, 0), (-1, -1), chinese_font),
        ('FONTSIZE',  (0, 0), (-1, -1), 6.8),
        ('ALIGN',      (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN',     (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID',       (0, 0), (-1, -1), 0.3, colors.HexColor('#bdc3c7')),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
    ]

    # 评分列高亮
    for i, row_data in enumerate(rows[1:], start=1):
        score = int(row_data[3])
        if score >= 80:
            style.append(('BACKGROUND', (3, i), (3, i), colors.HexColor('#e8f5e9')))
        elif score >= 70:
            style.append(('BACKGROUND', (3, i), (3, i), colors.HexColor('#fff9c4')))

    tbl.setStyle(TableStyle(style))
    return tbl

# 分页展示（每页约35行，列多了要少点）
page_size = 35
for page_idx in range(0, len(df), page_size):
    page_df = df.iloc[page_idx:page_idx+page_size]
    page_num  = page_idx // page_size + 1
    total_pages = (len(df) + page_size - 1) // page_size
    story.append(Paragraph(f'第 {page_num}/{total_pages} 页', styles['CSubtitle']))
    story.append(make_detail_table(page_df))
    story.append(Spacer(1, 0.3*cm))
    if page_idx + page_size < len(df):
        story.append(PageBreak())

# ===== 附录：评分分档收益 =====
story.append(PageBreak())
story.append(Paragraph('📈 入场评分与收益分析', styles['CH1']))

bins  = [0, 50, 60, 70, 80, 90, 101]
labels = ['<50', '50-60', '60-70', '70-80', '80-90', '>90']
df['score_bin'] = pd.cut(df['entry_score'], bins=bins, labels=labels, right=False)
score_group = df.groupby('score_bin')

score_data = [['评分区间', '数量', '1日%', '5日%', '10日%', '20日%']]
for label in labels:
    if label in score_group.groups:
        g = score_group.get_group(label)
        score_data.append([
            label,
            str(len(g)),
            f"{g['return_1d'].mean():.2f}",
            f"{g['return_5d'].mean():.2f}",
            f"{g['return_10d'].mean():.2f}",
            f"{g['return_20d'].mean():.2f}",
        ])

score_table = Table(score_data, colWidths=[2.0*cm, 1.5*cm, 2.0*cm, 2.0*cm, 2.0*cm, 2.0*cm])
score_table.setStyle(TableStyle([
    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#34495e')),
    ('TEXTCOLOR',  (0, 0), (-1, 0), colors.white),
    ('FONTNAME',   (0, 0), (-1, -1), chinese_font),
    ('FONTSIZE',  (0, 0), (-1, -1), 9),
    ('ALIGN',      (0, 0), (-1, -1), 'CENTER'),
    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#f8f9fa'), colors.white]),
    ('GRID',       (0, 0), (-1, -1), 0.4, colors.HexColor('#bdc3c7')),
    ('TOPPADDING', (0, 0), (-1, -1), 5),
    ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
]))
story.append(score_table)
story.append(Spacer(1, 0.3*cm))
story.append(Paragraph(
    '注：入场评分越高，后续收益整体越好。return_Xd 为信号日后 X 日收益率。',
    ParagraphStyle(name='Note', fontName=chinese_font, fontSize=8, textColor=colors.HexColor('#888888'), leading=12)
))

# ===== 生成 =====
doc.build(story)
print(f'PDF已生成：{pdf_path}')
print(f'信号总数：{total}')
print(f'名称覆盖率：{sum(df["name"]!="")}/{total}')
