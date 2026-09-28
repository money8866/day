# -*- coding: utf-8 -*-
"""算力运营 候选采集：全市场主营文本 + 指定候选名单证据"""
import io
import json
import os
import sys

LOG = open(r'd:\mystock\solo\_op_cand.log', 'w', encoding='utf-8')
sys.stdout = LOG

MB = r'd:\mystock\cache_daily\stock_company_mainbiz.json'
CFG = r'd:\mystock\solo\theme_kg_v3\theme_kg_v3\config\theme_config.json'
MAP = r'd:\mystock\solo\report_daily\theme_stock_map_latest_v2.json'
OLD81 = r'd:\mystock\solo\_prev_map_ai.json'
BASIC = r'd:\mystock\cache_daily\basic_20260514.csv'

mb = json.load(open(MB, encoding='utf-8'))
cfg = json.load(open(CFG, encoding='utf-8'))
new = json.load(open(MAP, encoding='utf-8'))
old = json.load(open(OLD81, encoding='utf-8'))

name = {}
for t, ms in new.get('themes', {}).items():
    for m in ms:
        name[m['code']] = m['name']
for m in old['themes']['AI算力']:
    name[m['code']] = m['name']

# 全A名称：尝试 Tushare stock_basic（失败则用 csv 代码集合兜底）
try:
    from tushare_quant import pro
    sb = pro.stock_basic(fields='ts_code,name,industry,market')
    for _, r in sb.iterrows():
        name[r['ts_code']] = r['name']
    print('[names] tushare stock_basic ok, total=%d' % len(name))
except Exception as e:
    print('[names] tushare failed: %s' % e)

print('名称字典 = %d ; mainbiz = %d' % (len(name), len(mb)))

# ---------- 关键词分层 ----------
STRONG = ['算力租赁', 'GPU租赁', '算力服务', '智算中心', '智能算力中心', '智算', '算力调度',
          '算力网络', 'GPU云', 'AI云', '云算力', '算力平台', '算力资源', '算力中心',
          'AI算力', '超算中心', '算力出租']
IDC = ['数据中心', 'IDC', '互联网数据中心', '云计算', '云服务', '云平台', '机房托管',
       '服务器托管', '机柜租用', '算力']
EXCL = ['印制电路', '线路板', '覆铜板', 'PCB', '光模块', '光器件', '光芯片', '液冷',
        '机柜', '空调', '制冷', '电源', '逆变器', '连接器', '电磁屏蔽', '导热',
        '结构件', '元器件', '电缆', '光纤光缆', '服务器整机', '服务器ODM']

print('\n=== A. 全市场主营文本命中【强算力运营词】 ===')
rows = []
for code, txt in mb.items():
    if not txt:
        continue
    hits = [w for w in STRONG if w in txt]
    if not hits:
        continue
    ex = [w for w in EXCL if w in txt]
    rows.append((len(hits), code, name.get(code, '?'), '|'.join(hits), '|'.join(ex), txt))
rows.sort(key=lambda x: (-x[0], x[1]))
print('强命中总数 = %d' % len(rows))
for n, code, nm, hits, ex, txt in rows:
    print('%2d | %s %-8s | S:%s | EX:%s\n     %s' % (n, code, nm, hits, ex or '-', txt[:220]))

print('\n=== B. 全市场主营文本仅命中【IDC/云/数据中心】且无强词 ===')
rows2 = []
for code, txt in mb.items():
    if not txt:
        continue
    if any(w in txt for w in STRONG):
        continue
    hits = [w for w in IDC if w in txt]
    if not hits:
        continue
    ex = [w for w in EXCL if w in txt]
    rows2.append((len(hits), code, name.get(code, '?'), '|'.join(hits), '|'.join(ex), txt))
rows2.sort(key=lambda x: (-x[0], x[1]))
print('次级命中总数 = %d' % len(rows2))
for n, code, nm, hits, ex, txt in rows2[:80]:
    print('%2d | %s %-8s | I:%s | EX:%s\n     %s' % (n, code, nm, hits, ex or '-', txt[:180]))

# ---------- 指定候选名单 ----------
WATCH = ['603003.SH', '002229.SZ', '300846.SZ', '600602.SH', '002335.SZ', '603985.SH',
         '002261.SZ', '300857.SZ', '300571.SZ', '002015.SZ', '600941.SH', '601728.SH',
         '600050.SH', '603990.SH', '002602.SZ', '002929.SZ', '688051.SH', '301085.SZ',
         '000938.SZ', '600100.SH', '002123.SZ', '000839.SZ', '300494.SZ', '002093.SZ',
         '300044.SZ', '300271.SZ', '300212.SZ', '002657.SZ', '300229.SZ', '000977.SZ',
         '300383.SZ', '300442.SZ', '300017.SZ', '300738.SZ', '603881.SH', '600589.SH',
         '000815.SZ', '300895.SZ', '600797.SH', '603220.SH', '600487.SH', '002896.SZ',
         '300310.SZ', '002517.SZ', '603039.SH', '300454.SZ', '688111.SH']
print('\n=== C. 指定候选名单证据 ===')
for code in WATCH:
    print('%s %-8s | %s' % (code, name.get(code, '?'), (mb.get(code) or '<无>')[:230]))

# ---------- 原 AI算力 中的 IDC/算力租赁组 ----------
IDC_GROUP = ['000815.SZ', '300017.SZ', '300383.SZ', '300442.SZ', '300738.SZ',
             '300895.SZ', '600589.SH', '600797.SH', '603220.SH', '603881.SH']
print('\n=== D. 原AI算力 IDC/租赁组（10只）逐只主营 ===')
for code in IDC_GROUP:
    print('%s %-8s | %s' % (code, name.get(code, '?'), (mb.get(code) or '<无>')[:230]))

print('\n=== E. 当前映射中的 IDC 相关伪主题/成员 ===')
for t, ms in new.get('themes', {}).items():
    if t in ('IDC', '智算运营', '算力电源', 'PCB高速互连'):
        print('%s (%d): %s' % (t, len(ms), [(m['code'], m['name'], m.get('via')) for m in ms]))

LOG.close()
