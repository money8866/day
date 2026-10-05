# -*- coding: utf-8 -*-
"""
从 a-stock-data SKILL.md 提取内嵌 Python 代码，生成一个可直接 import 的本地模块。

用法:
  python _extract_astdata.py                 # 默认输出到 D:/mystock/lib/astock_data.py
  python _extract_astdata.py --out <路径> --report

策略:
  逐代码块用 ast 解析，只保留「导入 / 函数 / 类 / 常量赋值 / 类型注解」等声明型语句，
  丢弃示例里的可执行语句（调用会触发网络请求或依赖缺失变量）。
  同名定义保留最后一次（SKILL.md 里后文通常是修正版）。
"""
import argparse
import ast
import os
import re
import sys
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

SKILL_MD = os.path.join(os.path.expanduser("~"), ".workbuddy", "skills",
                        "a-stock-data", "SKILL.md")
DEFAULT_OUT = os.path.join("D:/mystock", "lib", "astock_data.py")

BLOCK_RE = re.compile(r"```(?:python|py)\s*\n(.*?)```", re.S)

HEADER = '''# -*- coding: utf-8 -*-
"""
a-stock-data 本地集成包（自动生成，勿手工编辑）

来源: https://github.com/simonlin1212/a-stock-data  (SKILL.md v{ver})
生成脚本: _extract_astdata.py
生成时间: {ts}

把 SKILL.md 内嵌的 93 个代码块合并成可 import 的单文件模块，
保留函数 / 类 / 常量声明，剔除示例中的可执行语句。

用法:
    import sys; sys.path.insert(0, r"D:/mystock/lib")
    import astock_data as A
    print(A.tencent_quote("sh600519"))
    print(A.limit_up_pool("2026-10-01"))

依赖: pandas requests beautifulsoup4 lxml (可选: mootdx baostock)
"""
'''


def parse_blocks(text):
    """解析所有 python 代码块，返回 [(块序号, ast 节点列表)]"""
    parsed = []
    for i, code in enumerate(BLOCK_RE.findall(text)):
        if not code.strip():
            continue
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            skipped.append((i, f"SyntaxError: {e.msg} line {e.lineno}"))
            continue
        parsed.append((i, tree.body))
    return parsed


def collect_decls(parsed):
    """收集所有 import / def / class 名称（含 try/if 内的）与赋值目标名。"""
    imports, defs, targets = set(), set(), set()
    for _, body in parsed:
        for node in ast.walk(ast.Module(body=body, type_ignores=[])):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for al in node.names:
                    imports.add((al.asname or al.name).split(".")[0])
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defs.add(node.name)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        targets.add(t.id)
    return imports, defs, targets


def keep_with_whitelist(body, allowed):
    """只保留 def/class/import，以及右侧常量或只引用已允许名字的赋值。"""
    out = []
    for node in body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(node)
            continue
        if isinstance(node, ast.Assign) and all(
                isinstance(t, ast.Name) and (t.id.isupper() or t.id.startswith("_"))
                for t in node.targets):
            refs = {n.id for n in ast.walk(node.value)
                    if isinstance(n, ast.Name) and n.id not in ("True", "False", "None")}
            if refs <= allowed:
                out.append(node)
    return out


skipped = []


def extract(path, out_path, report=False):
    global skipped
    skipped = []
    text = open(path, encoding="utf-8").read()
    ver = re.search(r"^version:\s*(\S+)", text, re.M)
    ver = ver.group(1) if ver else "?"
    blocks = BLOCK_RE.findall(text)
    parsed = parse_blocks(text)
    imports, defs, targets = collect_decls(parsed)
    import builtins
    allowed = imports | defs | targets | set(dir(builtins)) | {"True", "False", "None"}

    kept, symbols = [], {}
    for i, body in parsed:
        # 代码块块级为非 'if TYPE_CHECKING' 等包装时，顺手把 if-body 的声明提到顶层
        flat = []
        for n in body:
            if isinstance(n, ast.If) and n.body and all(
                    isinstance(x, (ast.Import, ast.ImportFrom, ast.FunctionDef,
                                   ast.AsyncFunctionDef, ast.ClassDef)) for x in n.body):
                flat.extend(n.body)
            else:
                flat.append(n)
        lines = keep_with_whitelist(flat, allowed)
        if not lines:
            skipped.append((i, "无可保留声明（纯示例语句）"))
            continue
        for n in lines:
            name = getattr(n, "name", None)
            if name is None and isinstance(n, ast.Assign):
                name = getattr(n.targets[0], "id", None)
            if name:
                symbols[name] = i
        kept.append("\n".join(ast.unparse(n) for n in lines))

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(HEADER.format(ver=ver, ts=datetime.now().isoformat(timespec="seconds")))
        f.write("\n\n".join(kept) + "\n")

    if report:
        print(f"[抽取] 代码块 {len(blocks)} → 保留 {len(kept)}，跳过 {len(skipped)}")
        for i, why in skipped[:20]:
            print(f"   跳过 #{i}: {why}")
        print(f"[输出] {out_path}  ({len(symbols)} 个顶层符号, "
              f"{os.path.getsize(out_path)/1024:.0f} KB)")
        names = sorted(symbols)
        print(f"[符号] 共 {len(names)} 个，前 40: {', '.join(names[:40])}")
    return out_path, symbols


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--skill", default=SKILL_MD)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    extract(a.skill, a.out, a.report)
