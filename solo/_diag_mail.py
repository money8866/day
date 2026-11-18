# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, '.')
from hvt_bull import push
css = push._MAIL_CSS

q = chr(34)
# E: 完整大 <style> 块 + 极简正文
e = '<div><style>' + css + '</style><div class=' + q + 'hd' + q + '><div class=' + q + 't' + q + '>E测试_大style块</div></div><p>这是一段正文</p></div>'
open('t_e.html', 'w', encoding='utf-8').write(e)
# F: 无 style 的长正文（约 5KB）
f = '<div>' + ''.join('<div class=' + q + 'card' + q + '><p>第%d段 纯文本内容用于测试长度 %s</p></div>' % (i, 'x' * 50) for i in range(60)) + '</div>'
open('t_f.html', 'w', encoding='utf-8').write(f)
# G: v2 去掉 <style> 块（仅留正文结构），测试正文内容是否触发
import re
v2 = open('_hvt_mail_20261118_v2.html', encoding='utf-8').read()
g = re.sub(r'<style>.*?</style>', '', v2, flags=re.S)
open('t_g.html', 'w', encoding='utf-8').write(g)
print('E_len', len(e), 'F_len', len(f), 'G_len', len(g))
print('E_head', repr(e[:40]))
print('G_head', repr(g[:40]))
