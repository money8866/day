# -*- coding: utf-8 -*-
"""读取PDF内容"""
import sys
try:
    import pdfplumber
except ImportError:
    print('pdfplumber未安装，使用pdfminer')
    from pdfminer.high_level import extract_text

pdf_path = r'D:\mystock\solo\multi_factor_picker\output\wave2_pattern_bull_stocks_20260624.pdf'

try:
    if 'pdfplumber' in sys.modules:
        with pdfplumber.open(pdf_path) as pdf:
            print(f'PDF页数: {len(pdf.pages)}')
            for i, page in enumerate(pdf.pages):
                text = page.extract_text()
                if text:
                    print(f'\n=== 第{i+1}页 ===')
                    print(text)
    else:
        text = extract_text(pdf_path)
        print(text)
except Exception as e:
    print(f'读取失败: {e}')
    print('尝试读取CSV数据...')
    import pandas as pd
    import glob
    csv_files = glob.glob(r'D:\mystock\solo\multi_factor_picker\output\*.csv')
    if csv_files:
        latest = max(csv_files, key=lambda x: pd.Timestamp(x.split('_')[-1].replace('.csv', '')))
        df = pd.read_csv(latest)
        print(f'\n最新CSV: {latest}')
        print(df.head(20))
