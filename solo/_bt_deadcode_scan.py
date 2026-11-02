#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
死代码扫描：找出未被调用的函数、未使用的 import、未使用的局部变量
"""
import ast
import os
import sys
from collections import defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)


class Visitor(ast.NodeVisitor):
    def __init__(self):
        self.defs = {}          # name -> (lineno, kind)
        self.calls = defaultdict(int)
        self.names_used = defaultdict(int)
        self.imports = {}       # alias -> (lineno, orig)
        self.attr_uses = defaultdict(int)

    def visit_FunctionDef(self, node):
        self.defs[node.name] = (node.lineno, 'def')
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node):
        self.defs[node.name] = (node.lineno, 'async def')
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        self.defs[node.name] = (node.lineno, 'class')
        self.generic_visit(node)

    def visit_Call(self, node):
        f = node.func
        if isinstance(f, ast.Name):
            self.calls[f.id] += 1
        elif isinstance(f, ast.Attribute):
            self.attr_uses[f.attr] += 1
            self.names_used[f.attr] += 1
        self.generic_visit(node)

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load):
            self.names_used[node.id] += 1
        self.generic_visit(node)

    def visit_Import(self, node):
        for a in node.names:
            key = a.asname or a.name.split('.')[0]
            self.imports[key] = (node.lineno, a.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        for a in node.names:
            key = a.asname or a.name
            self.imports[key] = (node.lineno, f"{node.module}.{a.name}")
        self.generic_visit(node)


def scan(path):
    src = open(path, encoding='utf-8').read()
    tree = ast.parse(src)
    v = Visitor()
    v.visit(tree)
    return v, src, tree


def report(path):
    print('=' * 84)
    print(f'扫描: {os.path.relpath(path, ROOT)}')
    print('=' * 84)
    v, src, tree = scan(path)
    lines = src.splitlines()

    # 1) 疑似死函数（在本文件内无调用）
    dead = []
    for name, (ln, kind) in sorted(v.defs.items(), key=lambda x: x[1][0]):
        if name.startswith('__'):
            continue
        if v.calls.get(name, 0) == 0 and v.names_used.get(name, 0) <= 1:
            # names_used<=1 表示只有定义处那一次（def 本身不算 Name Load，
            # 所以 <=1 说明只在别处被当名字引用过 0~1 次）
            dead.append((ln, name, kind))
    if dead:
        print('\n【A】本文件内无调用的函数/类：')
        for ln, name, kind in dead:
            print(f'  L{ln:<6} {kind} {name}')
    else:
        print('\n【A】无死函数')

    # 2) 未使用的 import
    unused_imp = []
    for alias, (ln, orig) in v.imports.items():
        used = v.names_used.get(alias, 0)
        if used == 0 and v.attr_uses.get(alias, 0) == 0:
            unused_imp.append((ln, alias, orig))
    if unused_imp:
        print('\n【B】未使用的 import：')
        for ln, alias, orig in sorted(unused_imp):
            print(f'  L{ln:<6} {alias}   (from {orig})')
    else:
        print('\n【B】无未使用 import')

    # 3) 函数内未使用的局部变量（简单启发式：赋值后从未 Load）
    print('\n【C】函数内疑似未使用的局部变量：')
    found = 0
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        stores, loads = {}, set()
        for n in ast.walk(fn):
            if isinstance(n, ast.Name):
                if isinstance(n.ctx, ast.Store):
                    stores.setdefault(n.id, n.lineno)
                else:
                    loads.add(n.id)
            elif isinstance(n, ast.AugAssign):
                if isinstance(n.target, ast.Name):
                    loads.add(n.target.id)
        for var, ln in sorted(stores.items(), key=lambda x: x[1]):
            if var in loads:
                continue
            if var.startswith('_'):
                continue
            # 排除参数/全局误报：仅当赋值行在函数体内且该名字不是参数
            args = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
            if var in args:
                continue
            # 局部嵌套 def 会产生 Store
            nested_defs = {n.name for n in ast.walk(fn)
                           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n is not fn}
            if var in nested_defs:
                continue
            found += 1
            print(f'  L{ln:<6} {fn.name}() 内变量 {var}')
    if not found:
        print('  （无）')
    return v


if __name__ == '__main__':
    targets = sys.argv[1:] or [
        os.path.join(BASE, 'market_analysis.py'),
        os.path.join(BASE, 'position_engine_v95.py'),
    ]
    for t in targets:
        report(t)
        print()
