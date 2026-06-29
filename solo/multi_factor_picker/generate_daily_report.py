# -*- coding: utf-8 -*-
"""生成基本面信息日报PDF v3（完整标题+利空监测）"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

import pandas as pd
import re
from datetime import datetime
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
import os

print('='*80)
print('生成基本面信息日报PDF v3（完整标题+利空监测）')
print('='*80)

# ---------- 中文字体 ----------
chinese_font = 'Helvetica'
for fp in [r'C:\Windows\Fonts\simhei.ttf', r'C:\Windows\Fonts\msyh.ttc']:
    if os.path.exists(fp):
        try:
            pdfmetrics.registerFont(TTFont('ChineseFont', fp))
            chinese_font = 'ChineseFont'
            break
        except:
            pass

# ---------- 数据清洗 ----------
def parse_list_string(s):
    """解析类似 ['a','b','c'] 的字符串"""
    s = str(s).strip().strip('[]')
    items, cur, in_q = [], '', False
    for ch in s:
        if ch == "'" and not in_q:
            in_q = True
        elif ch == "'" and in_q:
            items.append(cur)
            cur = ''
            in_q = False
        elif in_q:
            cur += ch
    return items

def clean_html(text):
    return re.sub(r'<[^>]+>', '', str(text))

def fmt_code(code):
    return f"{int(code):06d}"

def load_and_clean(csv_path):
    df = pd.read_csv(csv_path)
    rows = []
    for _, row in df.iterrows():
        titles = parse_list_string(row['title'])
        cats = parse_list_string(row['category'])
        titles_clean = [clean_html(t) for t in titles]
        unique_titles = list(dict.fromkeys(titles_clean))

        rows.append({
            'ts_code': fmt_code(row['ts_code']),
            'weight': row['weight'],
            'category': clean_html(cats[0]) if cats else 'N/A',
            'count': len(unique_titles),
            'titles': unique_titles,
            'ann_date': str(row['ann_date'])[:8],
        })
    return sorted(rows, key=lambda x: x['weight'], reverse=True)

# ---------- 样式 ----------
styles = getSampleStyleSheet()
def S(name, **kw):
    base = dict(fontName=chinese_font, fontSize=10, leading=14, spaceAfter=6)
    base.update(kw)
    s = ParagraphStyle(name, **base)
    styles.add(s)
    return s

S('CTitle', fontSize=18, spaceAfter=6, alignment=TA_CENTER)
S('CTitle2', fontSize=12, spaceAfter=20, alignment=TA_CENTER, textColor=colors.gray)
S('SectionTitle', fontSize=14, spaceBefore=12, spaceAfter=8, textColor=colors.HexColor('#2c3e50'))
S('SectionTitle2', fontSize=14, spaceBefore=12, spaceAfter=8, textColor=colors.HexColor('#8e44ad'))
S('Body', fontSize=9, leading=15, spaceAfter=2)
S('BodySmall', fontSize=8, leading=12, textColor=colors.gray)
S('BodyNeg', fontSize=9, leading=15, spaceAfter=2, textColor=colors.red)
S('FullTitle', fontSize=9, leading=13, spaceAfter=1)

# ---------- PDF 构建 ----------
def make_stock_block(code, titles, weight, category, count):
    """一只股票的完整信息块（标题换行显示，不截断）"""
    blocks = []
    weight_str = f"+{weight}" if weight > 0 else str(weight)
    cat_str = f'[{category}]' if category else ''

    style = styles['BodyNeg'] if weight < 0 else styles['Body']
    color_tag = 'red' if weight < 0 else 'green'

    # 第一行：代码 + 评分 + 类别
    blocks.append(Paragraph(
        f'<font color="{color_tag}"><b>{code}</b></font>  '
        f'<b>{weight_str}分</b>  {cat_str}  ({count}篇公告)',
        style
    ))

    # 后续每行：一条公告标题（完整显示，自动换行）
    for t in titles:
        blocks.append(Paragraph(f'  &bull; {t}', styles['FullTitle']))

    blocks.append(Spacer(1, 4))
    return blocks

def build_pdf(pos_rows, neg_rows):
    pdf_path = r'D:\mystock\solo\multi_factor_picker\output\fundamental_info_auto_daily.pdf'
    doc = SimpleDocTemplate(pdf_path, pagesize=A4,
                            rightMargin=1.8*cm, leftMargin=1.8*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm)

    story = []
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M')

    # ======== 封面头 ========
    story.append(Paragraph('基本面信息日报', styles['CTitle']))
    story.append(Paragraph(f'{now_str}（自动挖掘版）', styles['CTitle2']))

    # ======== 概览 ========
    story.append(Paragraph('📊 挖掘概览', styles['SectionTitle']))
    story.append(Paragraph(f'• 数据源：巨潮资讯网 | 扫描股票：50只 | 时间窗口：最近7天', styles['Body']))
    story.append(Paragraph(f'• 利好消息：{len(pos_rows)}条  |  利空消息：{len(neg_rows)}条', styles['Body']))
    story.append(Spacer(1, 0.3*cm))

    # ======== 利空预警（红色，放在前面！）========
    if neg_rows:
        story.append(Paragraph('🔴 利空预警', styles['SectionTitle2']))
        story.append(Paragraph(f'以下股票近期有 {len(neg_rows)} 条利空消息，建议重点关注风险', styles['BodySmall']))
        story.append(Spacer(1, 3))
        for r in neg_rows:
            blocks = make_stock_block(
                r['ts_code'], r['titles'], r['weight'],
                r['category'], r['count']
            )
            story.extend(blocks)

        story.append(Spacer(1, 0.5*cm))

    # ======== 利好信息 ========
    if pos_rows:
        story.append(Paragraph('🟢 利好信息', styles['SectionTitle']))
        for r in pos_rows:
            blocks = make_stock_block(
                r['ts_code'], r['titles'], r['weight'],
                r['category'], r['count']
            )
            story.extend(blocks)

        story.append(Spacer(1, 0.5*cm))

    # ======== 分类统计 ========
    story.append(Paragraph('📈 类别分布', styles['SectionTitle']))

    # 利好类别统计
    cat_pos = {}
    for r in pos_rows:
        cat_pos[r['category']] = cat_pos.get(r['category'], 0) + 1

    cat_neg = {}
    for r in neg_rows:
        cat_neg[r['category']] = cat_neg.get(r['category'], 0) + 1

    # 表格
    table_data = [['类别', '利好', '利空']]
    all_cats = list(dict.fromkeys(list(cat_pos.keys()) + list(cat_neg.keys())))
    for cat in all_cats:
        p = str(cat_pos.get(cat, 0))
        n = str(cat_neg.get(cat, 0))
        table_data.append([cat, p, n])

    if len(all_cats) > 0:
        t = Table(table_data, colWidths=[4*cm, 3*cm, 3*cm])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c3e50')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, -1), chinese_font),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#bdc3c7')),
        ]))
        story.append(t)

    story.append(Spacer(1, 1*cm))
    story.append(Paragraph(f'报告生成：{now_str}', styles['BodySmall']))

    # 生成
    doc.build(story)
    print(f'\n✅ PDF已生成: {pdf_path}')
    return pdf_path

# ======== 主流程 ========
def main():
    print('\n[1/5] 读取利好消息...')
    try:
        pos_df = pd.read_csv(r'D:\mystock\solo\multi_factor_picker\output\auto_positive.csv')
        pos_rows = load_and_clean(r'D:\mystock\solo\multi_factor_picker\output\auto_positive.csv')
        print(f'  利好消息：{len(pos_rows)}条')
    except:
        pos_rows = []
        print('  利好消息：0条（文件不存在）')

    print('\n[2/5] 读取利空消息...')
    try:
        neg_df = pd.read_csv(r'D:\mystock\solo\multi_factor_picker\output\auto_negative.csv')
        neg_rows = load_and_clean(r'D:\mystock\solo\multi_factor_picker\output\auto_negative.csv')
        print(f'  利空消息：{len(neg_rows)}条')
    except:
        neg_rows = []
        print('  利空消息：0条（文件不存在）')

    if len(pos_rows) == 0 and len(neg_rows) == 0:
        print('\n❌ 未发现任何信息，跳过PDF生成')
        return

    print('\n[3/5] 清洗数据...')
    print(f'  利好TOP：{[r["ts_code"]+" +"+str(r["weight"])+"分" for r in pos_rows[:3]]}')
    print(f'  利空TOP：{[r["ts_code"]+" "+str(r["weight"])+"分" for r in neg_rows[:3]]}')

    print('\n[4/5] 生成PDF报告...')
    build_pdf(pos_rows, neg_rows)

    print('\n[5/5] 完成！')

if __name__ == '__main__':
    main()
