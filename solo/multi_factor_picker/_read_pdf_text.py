import sys
sys.path.append(r'D:\mystock\solo\multi_factor_picker')

from pdfminer.high_level import extract_text
text = extract_text(r'D:\mystock\solo\multi_factor_picker\output\wave2_pattern_bull_stocks_20260624.pdf')
print(text[:2000])
