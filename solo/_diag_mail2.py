# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, '.')

q = chr(34)
# I: 16KB 纯简单内容（无 style、无表格、无特殊字符）——测尺寸阈值
part = '<div class=' + q + 'card' + q + '><p>第%d段 纯文本内容用于测试尺寸阈值 %s</p></div>\n'
I = '<div>' + ''.join(part % (i, 'y' * 60) for i in range(200)) + '</div>'
open('t_i.html', 'w', encoding='utf-8').write(I)

# H: G 截断到 6KB（保留真实报告内容与表格）——测是否为尺寸触发
G = open('t_g.html', encoding='utf-8').read()
# 在 6000 附近找一个干净的标签边界截断
cut = G.rfind('</', 5500, 6200)
H = G[:cut] + '</div>'
open('t_h.html', 'w', encoding='utf-8').write(H)

# J: 在 G 里搜可能破坏解析的原始字符
import re
suspicious = []
for m in re.finditer(r'[^&]<[a-zA-Z/]', G):
    pass  # 都应是合法标签起点
# 找文本里未转义的 < 或 > （不在标签内）
print('I_len', len(I), 'H_len', len(H))
print('H_head', repr(H[:60]))
