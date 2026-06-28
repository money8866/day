"""读取PDF内容"""
from PyPDF2 import PdfReader

pdf_file = r'D:\mystock\solo\report_daily\bull_score_report_20260627.pdf'
reader = PdfReader(pdf_file)

print(f'总页数: {len(reader.pages)}')
print()

# 提取第一页内容
page1 = reader.pages[0]
text = page1.extract_text()

print('=== 第1页内容 ===')
print(text[:2000])
