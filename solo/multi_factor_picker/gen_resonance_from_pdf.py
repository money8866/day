# -*- coding: utf-8 -*-
"""从已生成的PDF wave2_pattern_bull_stocks_20260624.pdf 读取数据，
重新生成按共振评分排序+带名称的PDF"""
import os, sys, datetime
import subprocess

# 用pymupdf读取PDF中的表格
try:
    import fitz  # pymupdf
except ImportError:
    # 尝试安装
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pymupdf', '-q'])
    import fitz

PDF_PATH = r'D:\mystock\solo\multi_factor_picker\output\wave2_pattern_bull_stocks_20260624.pdf'
OUT_DIR = r'D:\mystock\solo\multi_factor_picker\output'

doc = fitz.open(PDF_PATH)
print(f'PDF页数: {len(doc)}')

all_rows = []
headers = None

for page_num in range(len(doc)):
    page = doc[page_num]
    # 提取表格
    tables = page.find_tables()
    print(f'页{page_num+1}: 找到{len(tables.tables)}个表格')
    
    for table in tables.tables:
        data = table.extract()
        if not data:
            continue
        if headers is None:
            headers = data[0]
            print(f'表头: {headers}')
            for row in data[1:]:
                all_rows.append(row)
        else:
            for row in data:
                all_rows.append(row)

doc.close()
print(f'\n提取行数: {len(all_rows)}')
if all_rows:
    print(f'第一行: {all_rows[0]}')
    print(f'最后一行: {all_rows[-1]}')

# 解析数据
import pandas as pd
if headers and all_rows:
    df = pd.DataFrame(all_rows, columns=headers)
    print(f'\n列名: {df.columns.tolist()}')
    print(f'行数: {len(df)}')

    # 补充股票名称
    import tushare as ts
    if 'TUSHARE_TOKEN' not in os.environ:
        for _l in open(r'D:\mystock\config\.env'):
            if _l.strip().startswith('TUSHARE_TOKEN='):
                os.environ['TUSHARE_TOKEN'] = _l.strip().split('=', 1)[1].strip().strip('"')
                break
    ts.set_token(os.environ['TUSHARE_TOKEN'])
    pro = ts.pro_api()
    name_col = [c for c in df.columns if '名称' in str(c)]
    if name_col:
        name_col = name_col[0]
        # 检查哪些行名称为空
        mask = df[name_col].isna() | (df[name_col] == '') | (df[name_col].str.strip() == '')
        if mask.any():
            print(f'补充{mask.sum()}只股票名称...')
            try:
                all_basic = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name')
                name_map = dict(zip(all_basic['ts_code'], all_basic['name']))
                code_col = [c for c in df.columns if '代码' in str(c)][0]
                df.loc[mask, name_col] = df.loc[mask, code_col].map(name_map)
                filled = df[name_col].notna() & (df[name_col] != '') & (df[name_col].str.strip() != '')
                print(f'填充成功: {filled.sum()}/{len(df)}')
            except Exception as e:
                print(f'名称获取失败: {e}')
    
    # 找共振评分列
    score_col = None
    for col in df.columns:
        if '评分' in str(col) or 'score' in str(col).lower() or '共振' in str(col):
            score_col = col
            break
    if score_col is None:
        # 看每列内容猜
        for col in df.columns:
            try:
                vals = pd.to_numeric(df[col], errors='coerce')
                if vals.notna().sum() > len(df) * 0.5:
                    vrange = vals.max() - vals.min()
                    if 5 < vrange < 50:
                        score_col = col
                        break
            except:
                pass
    
    print(f'评分列: {score_col}')
    if score_col:
        df['_score_num'] = pd.to_numeric(df[score_col], errors='coerce')
        df = df.sort_values('_score_num', ascending=False).reset_index(drop=True)
        print(f'\n排序后TOP10:')
        for i in range(min(10, len(df))):
            r = df.iloc[i]
            print(f"  {i+1}. {r.iloc[0]} {r.iloc[1]} 评分{r['_score_num']}")

    # 生成新PDF
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
    pdf_path = os.path.join(OUT_DIR, f'wave2_resonance_sorted_{ts_str}.pdf')

    doc2 = SimpleDocTemplate(pdf_path, pagesize=landscape(A4),
        topMargin=12*mm, bottomMargin=12*mm, leftMargin=8*mm, rightMargin=8*mm)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('TCN', parent=styles['Title'],
        fontName=font_name, fontSize=16, alignment=1, spaceAfter=4*mm)
    sub_style = ParagraphStyle('SCN', parent=styles['Normal'],
        fontName=font_name, fontSize=9, alignment=1, spaceAfter=3*mm)
    hdr_style = ParagraphStyle('HCN', parent=styles['Normal'],
        fontName=font_name, fontSize=7.5, alignment=1)
    cel_style = ParagraphStyle('CCN', parent=styles['Normal'],
        fontName=font_name, fontSize=7, alignment=1)
    note_style = ParagraphStyle('NCN', parent=styles['Normal'],
        fontName=font_name, fontSize=8, textColor=colors.HexColor('#888888'))

    elements = []
    elements.append(Paragraph('二波形态精选扫描报告（按共振评分排序）', title_style))
    
    # 统计
    pattern_col = None
    for col in df.columns:
        if '形态' in str(col) or 'pattern' in str(col).lower():
            pattern_col = col
            break
    
    if pattern_col:
        pc = df[pattern_col].value_counts().to_dict()
        summary = f"信号: {len(df)}个 | " + " | ".join(f"{p}: {c}只" for p, c in sorted(pc.items(), key=lambda x: -x[1]))
    else:
        summary = f"信号: {len(df)}个"
    elements.append(Paragraph(summary, sub_style))
    elements.append(Spacer(1, 2*mm))

    # 表格 - 使用原表头+加排名
    new_headers = ['排名'] + [str(h) for h in headers]
    col_count = len(new_headers)
    
    # 列宽精细调整
    col_widths = [8*mm]  # 排名
    # 根据原表头分配宽度
    for h in headers:
        h_str = str(h)
        if '代码' in h_str:
            col_widths.append(20*mm)
        elif '名称' in h_str:
            col_widths.append(18*mm)
        elif '形态' in h_str:
            col_widths.append(14*mm)
        elif '评分' in h_str or '共振' in h_str:
            col_widths.append(12*mm)
        elif '涨幅' in h_str or '回调' in h_str:
            col_widths.append(12*mm)
        elif '天数' in h_str:
            col_widths.append(10*mm)
        elif '日期' in h_str:
            col_widths.append(16*mm)
        elif '盈亏' in h_str:
            col_widths.append(10*mm)
        else:
            col_widths.append(12*mm)

    data_rows = [[Paragraph(h, hdr_style) for h in new_headers]]
    for idx, (_, row) in enumerate(df.iterrows()):
        row_data = [Paragraph(str(idx+1), cel_style)]
        for col in headers:
            val = str(row[col]) if pd.notna(row[col]) else ''
            row_data.append(Paragraph(val, cel_style))
        data_rows.append(row_data)

    t = Table(data_rows, colWidths=col_widths, repeatRows=1)
    style_cmds = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a5276')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#cccccc')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f5f5f5')]),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]
    # TOP3绿色
    for i in range(min(3, len(df))):
        style_cmds.append(('BACKGROUND', (0, i+1), (-1, i+1), colors.HexColor('#d4efdf')))
    # 评分>=15高亮
    if score_col:
        for i in range(len(df)):
            s = df.iloc[i]['_score_num']
            if pd.notna(s):
                if s >= 20:
                    style_cmds.append(('BACKGROUND', (0, i+1), (-1, i+1), colors.HexColor('#a9dfbf')))
                elif s >= 15:
                    style_cmds.append(('BACKGROUND', (0, i+1), (-1, i+1), colors.HexColor('#d5f5e3')))

    t.setStyle(TableStyle(style_cmds))
    elements.append(t)
    elements.append(Spacer(1, 4*mm))
    elements.append(Paragraph("* 绿色 = 评分≥15分 | 深绿 = 评分≥20分 | 按共振评分降序", note_style))
    elements.append(Paragraph(f"* 生成: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}", note_style))

    doc2.build(elements)
    sz = os.path.getsize(pdf_path)
    print(f'\n新PDF已生成: {pdf_path}')
    print(f'文件大小: {sz} bytes')
else:
    print('未能提取表格数据')
