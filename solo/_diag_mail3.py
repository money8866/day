# -*- coding: utf-8 -*-
import re
G = open('t_g.html', encoding='utf-8').read()

# 1) 找所有不在合法标签里的 < 或 > （文本中未转义的尖括号）
# 简化：用 html.parser 提取文本，然后在原始 G 中找文本里的 < >
from html.parser import HTMLParser

class TextPos(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.text_spans = []
        self._last = 0
    def handle_data(self, data):
        # data 的原始位置需要自己算；用搜索
        pass

# 更直接：在 G 中找 <x 其中 x 不是合法标签开头的模式
# 合法标签开头：< / ? ! 后跟字母
# 找 '<' 后跟空白或数字等非字母且非 / ! ?
suspicious_less = []
for m in re.finditer(r'<(?![a-zA-Z/!?!])', G):
    suspicious_less.append((m.start(), G[m.start():m.start()+30].replace('\n','\\n')))
print('=== 文本中未转义的 < (前20个) ===')
for p, c in suspicious_less[:20]:
    print(p, repr(c))

suspicious_gt = []
for m in re.finditer(r'(?<=[a-zA-Z0-9\u4e00-\u9fff])>', G):
    # 标签结束是 </tag> 或 <tag>；文本里出现 字> 的可疑
    suspicious_gt.append((m.start(), G[max(0,m.start()-15):m.start()+15].replace('\n','\\n')))
print('=== 疑似文本中未转义的 > (前20个) ===')
for p, c in suspicious_gt[:20]:
    print(p, repr(c))

# 2) 找 & 后面不是合法实体的
bad_amp = []
for m in re.finditer(r'&(?!#?[a-zA-Z0-9]+;)', G):
    bad_amp.append((m.start(), G[m.start():m.start()+20].replace('\n','\\n')))
print('=== 未转义 & (前20个) ===')
for p, c in bad_amp[:20]:
    print(p, repr(c))
