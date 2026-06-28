"""提取PDF中的股票列表"""
from PyPDF2 import PdfReader
import re

pdf_file = r'D:\mystock\solo\report_daily\bull_score_report_20260627.pdf'
reader = PdfReader(pdf_file)

print(f'总页数: {len(reader.pages)}\n')

# 提取所有页面文本
all_text = ''
for page in reader.pages:
    all_text += page.extract_text() + '\n'

# 查找股票代码和名称
lines = all_text.split('\n')

# 查找A级和B级股票
print('=== A级产业龙头 ===')
for line in lines:
    if 'A级' in line or 'B级' in line:
        print(line)
        # 打印后续几行
        idx = lines.index(line)
        for i in range(idx+1, min(idx+15, len(lines))):
            if lines[i].strip():
                print(lines[i])

# 查找烽火通信
print('\n\n=== 搜索烽火通信 ===')
for i, line in enumerate(lines):
    if '烽火' in line or '600498' in line:
        print(f'行{i+1}: {line}')
        if i+1 < len(lines):
            print(f'行{i+2}: {lines[i+1]}')
