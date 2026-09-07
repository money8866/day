# -*- coding: utf-8 -*-
import io
p = r'D:\mystock\solo\stock_cache.py'
with io.open(p, encoding='utf-8') as f:
    lines = f.readlines()

def find(pred, start=0):
    for i in range(start, len(lines)):
        if pred(lines[i]):
            return i
    return -1

# 头注释 '# ====...' 行的下一行是 '# daily_basic_cache 表：pro.daily_basic 精简列'
hdr = find(lambda l: l.startswith('# daily_basic_cache 表：pro.daily_basic 精简列'))
assert hdr > 0 and lines[hdr - 1].startswith('# ===='), (hdr, lines[hdr - 1] if hdr > 0 else None)
end = find(lambda l: l.startswith('def batch_insert_stk_factor_pro'), hdr)
assert end > hdr, end
# 删除 头注释起点(=hdr-1) 起，到 def 前的空行为止（保留 def 前 2 个空行）
del lines[hdr - 1:end - 1]
with io.open(p, 'w', encoding='utf-8', newline='') as f:
    f.writelines(lines)
print('removed block OK: start_line=%d end_line=%d' % (hdr, end))
