import sys, os
sys.argv = ['x', '20260807']

src = open(r'D:\mystock\report_daily\_final_pdf_fixed.py', encoding='utf-8').read()
src = src.replace(
    "pdf_path = os.path.join(base, f'Final_Self_{date_str}.pdf')",
    "pdf_path = os.path.join(base, f'Final_Self_{date_str}_v2.pdf')"
)

exec(src + '\ngenerate()')
