# -*- coding: utf-8 -*-
"""AI算力拆旧建新 · 配置层收口（第 2 组，文本切片版）

theme_config.json 非 json.dumps 标准格式，故改用「按主题块定位 + 块内正则替换」，
块外内容保持逐字节不变。

改动：
  CPO         sw/cx_industry_match -> []            （禁止行业标签自动归类）
  液冷        sw/cx_industry_match -> []；eastmoney_concepts -> ["液冷","温控"]
  AI服务器    sw/cx_industry_match -> []；eastmoney_concepts/ths_concepts -> ["服务器"]
"""
import json
import re
import shutil

P = r'd:\mystock\solo\theme_kg_v3\theme_kg_v3\config\theme_config.json'
BAK = P + '.bak_20260928_aisplit_ch2'

raw = open(P, 'rb').read()
BOM = b'\xef\xbb\xbf'
has_bom = raw.startswith(BOM)
body = raw[3:] if has_bom else raw
txt = body.decode('utf-8')
nl = '\r\n' if '\r\n' in txt else '\n'
print('BOM=%s CRLF=%s' % (has_bom, nl == '\r\n'))
json.loads(txt)


def block_span(key):
    """返回顶层块 [起, 止) 的字符区间（止于下一个顶层 key 行首）"""
    s = txt.index('  "%s": {' % key)
    m = re.search(r'\r\n  "', txt[s + 10:])
    e = s + 10 + m.start() + len(nl)
    return s, e


def set_list(seg, field, items):
    body_txt = ''.join(
        '      "%s"%s%s' % (x, ',' if i < len(items) - 1 else '', nl)
        for i, x in enumerate(items))
    repl = '    "%s": [%s%s    ]' % (field, nl, body_txt)
    pat = re.compile(r'    "%s": \[(?:[^\[\]]*?)\]' % field)
    new, n = pat.subn(lambda m: repl, seg, count=1)
    assert n == 1, 'field not found: %s' % field
    return new


targets = {}
for key, edits in (
    ('CPO', {'sw_industry_match': [], 'cx_industry_match': []}),
    ('LIQUID_COOLING', {'sw_industry_match': [], 'cx_industry_match': [],
                        'eastmoney_concepts': ['液冷', '温控']}),
    ('AI_SERVER', {'sw_industry_match': [], 'cx_industry_match': [],
                   'eastmoney_concepts': ['服务器'], 'ths_concepts': ['服务器']}),
):
    s, e = block_span(key)
    seg = txt[s:e]
    before = {}
    for f, items in edits.items():
        m = re.search(r'    "%s": \[(?:[^\[\]]*?)\]' % f, seg)
        before[f] = ' '.join(m.group(0).split())
        seg = set_list(seg, f, items)
    targets[key] = (s, e, seg)
    print('[%s] %s' % (key, before))

# 从后往前拼接，保证偏移有效
new_txt = txt
for key in sorted(targets, key=lambda k: -targets[k][0]):
    s, e, seg = targets[key]
    new_txt = new_txt[:s] + seg + new_txt[e:]

chk = json.loads(new_txt)          # 写回前校验
for key in ('CPO', 'LIQUID_COOLING', 'AI_SERVER', 'AI_APPLICATION'):
    b = chk[key]
    print('  %-6s sw=%s cx=%s em=%s ths=%s leaders=%d core=%d' % (
        b['name_cn'], b['sw_industry_match'], b['cx_industry_match'],
        b['eastmoney_concepts'], b['ths_concepts'],
        len(b['leaders']), len(b['core_stocks'])))
assert chk['CPO']['sw_industry_match'] == [] and chk['CPO']['cx_industry_match'] == []
assert chk['LIQUID_COOLING']['sw_industry_match'] == [] and chk['LIQUID_COOLING']['cx_industry_match'] == []
assert chk['LIQUID_COOLING']['eastmoney_concepts'] == ['液冷', '温控']
assert chk['AI_SERVER']['eastmoney_concepts'] == ['服务器']
assert chk['AI_SERVER']['ths_concepts'] == ['服务器']
assert chk['AI_SERVER']['sw_industry_match'] == [] and chk['AI_SERVER']['cx_industry_match'] == []
assert chk['AI_APPLICATION']['sw_industry_match'] == [] and chk['AI_APPLICATION']['cx_industry_match'] == []
assert 'AI_COMPUTE' not in chk
print('顶层 key 数: %d' % len(chk))

# 块外未改动校验：把各块按新长度跳过，块外文本必须逐字节一致
spans = sorted((targets[k][0], targets[k][1]) for k in targets)
pos_t, pos_n, ok = 0, 0, True
for a, b in spans:
    unchanged = txt[pos_t:a]
    if new_txt[pos_n:pos_n + len(unchanged)] != unchanged:
        ok = False
        break
    pos_n += len(unchanged)
    key = [k for k in targets if targets[k][0] == a][0]
    pos_n += len(targets[key][2])
    pos_t = b
ok = ok and new_txt[pos_n:] == txt[pos_t:]
print('块外逐字节一致: %s' % ok)
if not ok:
    raise SystemExit('块外内容被改动，终止（不写入）')

shutil.copy2(P, BAK)
print('备份 -> %s' % BAK)
out = new_txt.encode('utf-8')
if has_bom:
    out = BOM + out
with open(P, 'wb') as f:
    f.write(out)

r2 = open(P, 'rb').read()
print('写回后 CRLF=%s 行数=%d 可解析=%s' % (
    b'\r\n' in r2, r2.count(b'\n'), json.loads(r2.decode('utf-8-sig')) is not None))
