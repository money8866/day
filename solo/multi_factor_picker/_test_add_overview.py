import sys
sys.path.append(r'D:\mystock\solo\multi_factor_picker')

from wave2_pattern_scanner import _add_market_overview
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm

styles = getSampleStyleSheet()
font_name = 'Helvetica'

# 测试调用
elements = []
try:
    _add_market_overview(elements, font_name, '20260623', styles)
    print(f"elements count: {len(elements)}")
    for i, e in enumerate(elements):
        print(f"  [{i}] {type(e).__name__}: {getattr(e, 'text', '')[:80] if hasattr(e,'text') else ''}")
except Exception as ex:
    print(f"ERROR: {ex}")
    import traceback; traceback.print_exc()
