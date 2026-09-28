# -*- coding: utf-8 -*-
"""AI算力「拆旧建新」最后一步：重构 subtheme_map.json 的母主题键。

原顶层键 "AI算力" 下 9 个子主题重新归属：
  光模块 / CPO光交换 / 光芯片      -> 父主题 CPO
  液冷                            -> 父主题 液冷
  AI服务器                        -> 父主题 AI服务器
  PCB高速互连 / IDC / 算力电源 / 智算运营 -> 父主题 "_UNCERTAIN"（不进入四主题成员池）

写回采用 json 往返 + 格式保真自检（缩进/换行/BOM/尾换行 全部保持原样）。
"""
import json
import shutil

P = r'd:\mystock\solo\theme_kg_v3\theme_kg_v3\config\subtheme_map.json'
BAK = P + '.bak_20260928_aisplit'

raw = open(P, 'rb').read()
has_bom = raw.startswith(b'\xef\xbb\xbf')
body = raw[3:] if has_bom else raw
crlf = b'\r\n' in body
tail_nl = body.endswith(b'\r\n') or body.endswith(b'\n')
txt = body.decode('utf-8')

data = json.loads(txt)

# ---- 格式保真自检：原样 dump 必须与原文逐字节一致 ----
def dumps(d):
    s = json.dumps(d, ensure_ascii=False, indent=2)
    if crlf:
        s = s.replace('\n', '\r\n')
    if tail_nl:
        s += '\r\n' if crlf else '\n'
    return s

probe_ok = dumps(data) == txt
print('格式保真自检(原样往返逐字节一致): %s' % probe_ok)
if not probe_ok:
    raise SystemExit('格式保真自检失败，终止（不做任何写入）')

# ---- 重复子主题名检查（后出现的父主题会覆盖前者）----
from collections import Counter
sub_names = [s for parent, subs in data.items() for s in subs.keys()]
dup = [k for k, v in Counter(sub_names).items() if v > 1]
print('子主题名重复: %s' % (dup or '无'))
for d in dup:
    owners = [p for p, subs in data.items() if d in subs]
    print('   %s -> %s' % (d, owners))

# ---- 重构 ----
assert 'AI算力' in data, 'subtheme_map.json 中未找到 AI算力'
ai = data.pop('AI算力')
print('原 AI算力 子主题: %s' % list(ai.keys()))

GROUPS = [
    ('CPO', ['光模块', 'CPO/光交换', '光芯片']),
    ('液冷', ['液冷']),
    ('AI服务器', ['AI服务器']),
    ('_UNCERTAIN', ['PCB高速互连', 'IDC', '算力电源', '智算运营']),
]
used = []
new_blocks = []
for parent, subs in GROUPS:
    blk = {}
    for s in subs:
        if s not in ai:
            print('  [警告] 子主题缺失: %s' % s)
            continue
        blk[s] = ai[s]
        used.append(s)
    new_blocks.append((parent, blk))
print('已分配: %s' % used)
rest = [k for k in ai.keys() if k not in used]
if rest:
    raise SystemExit('存在未分配子主题，终止: %s' % rest)

# 新键插在 AI算力 原位置（文件最前）
out = {}
for parent, blk in new_blocks:
    out[parent] = blk
for k, v in data.items():
    out[k] = v

# 校验结构合法性
chk = json.loads(json.dumps(out, ensure_ascii=False))
print('顶层母主题数: %d -> %d' % (len(data) + 1, len(chk)))
print('新增键: %s' % [p for p, _ in new_blocks])
assert 'AI算力' not in chk
assert 'CPO' in chk and '液冷' in chk and 'AI服务器' in chk and '_UNCERTAIN' in chk
for p, blk in new_blocks:
    assert list(chk[p].keys()) == list(blk.keys()), p

shutil.copy2(P, BAK)
print('备份 -> %s' % BAK)

new_raw = dumps(out).encode('utf-8')
if has_bom:
    new_raw = b'\xef\xbb\xbf' + new_raw
with open(P, 'wb') as f:
    f.write(new_raw)

r2 = open(P, 'rb').read()
print('写回后 BOM=%s CRLF=%s 行数=%d' % (
    r2.startswith(b'\xef\xbb\xbf'), b'\r\n' in r2, r2.count(b'\n')))
print('写回后可解析: %s' % (json.loads(r2.decode('utf-8-sig')) is not None))
