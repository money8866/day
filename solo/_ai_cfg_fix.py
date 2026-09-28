# -*- coding: utf-8 -*-
"""从 theme_config.json 的 CPO.core_stocks 中移除 600498.SH（烽火通信）。

依据 20260928「AI算力拆旧建新」用户裁定：光纤光缆 / 光传输设备 一律判 UNCERTAIN，
烽火通信主营为光通信传输设备 + 光纤光缆，无光模块/CPO 主营证据 → 不应作为
CPO 人工核验成员强制纳入。字节级删除单行，保持 CRLF 与其余内容逐字节不变。
"""
import json
import shutil

P = r'd:\mystock\solo\theme_kg_v3\theme_kg_v3\config\theme_config.json'
BAK = P + '.bak_20260928_cpo_fh'
TARGET = '      "600498.SH",'

raw = open(P, 'rb').read()
crlf = b'\r\n' in raw
txt = raw.decode('utf-8-sig')
json.loads(txt)  # 先确认当前文件合法

nl = '\r\n' if crlf else '\n'
needle = TARGET + nl
cnt = txt.count(needle)
print('待删行出现次数: %d' % cnt)
if cnt != 1:
    raise SystemExit('出现次数不为 1，终止（不写入）')

new_txt = txt.replace(needle, '', 1)
chk = json.loads(new_txt)                      # 写回前校验
cpo = chk['CPO']['core_stocks']
print('CPO.core_stocks 条数: %d -> %d' % (len(json.loads(txt)['CPO']['core_stocks']), len(cpo)))
assert '600498.SH' not in cpo
assert '600498.SH' in txt
print('CPO 其余成员保留: %s' % [c for c in cpo if c in ('300308.SZ', '002281.SZ', '300570.SZ')])
print('顶层 key 数: %d' % len(chk))

shutil.copy2(P, BAK)
print('备份 -> %s' % BAK)
with open(P, 'w', encoding='utf-8', newline='') as f:
    f.write(new_txt)

r2 = open(P, 'rb').read()
print('写回后 CRLF=%s 行数=%d 可解析=%s' % (
    b'\r\n' in r2, r2.count(b'\n'), json.loads(r2.decode('utf-8-sig')) is not None))
