with open(r'D:\mystock\report_daily\_final_pdf_fixed.py', encoding='utf-8') as f:
    content = f.read()

old = "    idx = html.find(f'<strong>{marker}</strong>')\n    if idx == -1:\n        return ''"
new = "    idx = -1\n    for m in re.finditer(r'<strong>([^<]*)</strong>', html):\n        if marker in m.group(1):\n            idx = m.start(); break\n    if idx == -1:\n        return ''"

assert old in content, 'marker block not found'
content = content.replace(old, new)
open(r'D:\mystock\report_daily\_final_pdf_fixed.py', 'w', encoding='utf-8').write(content)
print('OK')
