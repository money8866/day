"""读取PDF内容"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

from pdf import PDFReader

pdf_file = r'D:\mystock\solo\report_daily\bull_score_report_20260627.pdf'
reader = PDFReader()
text = reader.read(pdf_file)

# 提取关键信息
lines = text.split('\n')
for i, line in enumerate(lines[:100]):
    if line.strip():
        print(f'{i+1:3d}: {line}')
