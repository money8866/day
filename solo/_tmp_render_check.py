# -*- coding: utf-8 -*-
"""临时：AST 抽取 tushare_quant 的邮件渲染函数，渲染 0928 报告并逐项断言。

不 import tushare_quant（导入期会触发 Tushare API），只取所需顶层定义。
"""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

NEED_VARS = {"_MAIL_CSS", "_EMAIL_INLINE", "_TBL_WRAP_STYLE", "_ST_DIV_STYLE",
             "_PSEUDO_HEADING_RE", "_INNER_UNDERSCORE_RE",
             "_prep_email_markdown", "_add_inline_style", "_inline_email_styles",
             "_wrap_email_tables", "markdown_to_email_html"}

src_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tushare_quant.py")
tree = ast.parse(open(src_path, encoding="utf-8").read())

ns = {"__name__": "_extracted"}
import re as _re            # noqa: F401  (供抽取出的代码使用)
import markdown2 as _md2    # noqa: F401
from datetime import datetime as _dt  # noqa: F401
ns["re"], ns["markdown2"], ns["datetime"] = _re, _md2, _dt


def _public_names(node):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [node.name]
    if isinstance(node, ast.Assign):
        return [t.id for t in node.targets if isinstance(t, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


kept = []
for node in tree.body:
    if set(_public_names(node)) & NEED_VARS:
        kept.append(node)

mod = ast.Module(body=kept, type_ignores=[])
exec(compile(mod, "<extract>", "exec"), ns)

missing = NEED_VARS - set(ns)
assert not missing, f"未抽取到: {missing}"

md_src = r"D:\mystock\report_daily\Final_Self_20260928.md"
raw_md = open(md_src, encoding="utf-8").read()
html = ns["markdown_to_email_html"](raw_md, title="每日复盘(20260928)",
                                    subtitle="A股每日复盘 · 自动推送")

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp_render_out.html")
open(out, "w", encoding="utf-8").write(html)
print(f"HTML 长度: {len(html)} 字符 -> {out}\n")

checks = [
    ("① 无字面伪标题 '** '", "** " not in html and "**\n" not in html),
    ("① <div class=\"st\" 小节块存在", 'class="st"' in html),
    ("② 无 <em> 斜体碎片", "<em>" not in html),
    ("② 下划线已转义 &#95;", "&#95;" in html),
    ("③ <p> 带内联 font-size:22px", bool(_re.search(r'<p style="[^"]*font-size:22px', html))),
    ("③ <li> 带内联 font-size:22px", bool(_re.search(r'<li style="[^"]*font-size:22px', html))),
    ("④ <h2> 蓝底白字 pill",
     bool(_re.search(r'<h2 style="[^"]*background:#1677ff', html))
     and bool(_re.search(r'<h2 style="[^"]*color:#ffffff', html))
     and bool(_re.search(r'<h2 style="[^"]*border-radius:10px', html))),
    ("④ h2 内 strong 未染橙", bool(_re.search(r"<h2[^>]*>.*?color:inherit", html, _re.S))),
    ("⑤ 报告无表格（本文件确无 markdown 表）", "<table" not in html),
    ("视口 meta 存在", 'width=device-width' in html),
]

# 合成一段含表格的 markdown，单独验证「宽表套滚动容器」
_synth = ("## 4、**测试表**\n\n| 代码 | 名称 | 现价 |\n| --- | --- | --- |\n"
          "| 688536.SH | 思瑞浦 | 342.42 |\n| 688052.SH | 纳芯微 | 246.15 |\n")
_shtml = ns["markdown_to_email_html"](_synth, title="表格用例")
checks += [
    ("⑤ 合成表已套滚动容器", '<div style="overflow-x:auto' in _shtml),
    ("⑤ 合成表无裸 <table>", "<table>" not in _shtml),
    ("⑤ 合成表 th 蓝底白字", bool(_re.search(r'<th style="[^"]*background:#1677ff', _shtml))),
    ("⑤ 合成表 td 20px", bool(_re.search(r'<td style="[^"]*font-size:20px', _shtml))),
]

ok = True
for name, passed in checks:
    print(("  [OK]   " if passed else "  [FAIL] ") + name)
    ok = ok and passed

# 关键片段抽样
print("\n--- 抽样：伪标题所在处 ---")
i = html.find('class="st"')
print(html[i - 60:i + 260] if i >= 0 else "(未找到)")
print("\n--- 抽样：下划线所在处 ---")
j = html.find("&#95;")
print(html[max(0, j - 90):j + 90] if j >= 0 else "(未找到)")
print("\n--- 抽样：章节 h2 ---")
k = html.find("<h2")
print(html[k:k + 200] if k >= 0 else "(未找到)")
print("\n--- 抽样：表格容器 ---")
t = html.find("<div style=\"overflow-x:auto")
print(html[t:t + 260] if t >= 0 else "(未找到)")

print("\n结果:", "全部通过" if ok else "存在失败项")
