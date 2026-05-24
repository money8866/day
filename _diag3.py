# -*- coding: utf-8 -*-
data = open(r'C:\Users\kongx\mystock\zhongjun_backtest_v3_local_halfyear.py','rb').read()
import ast
try:
    code = data.decode('utf-8')
    ast.parse(code)
    print('OK')
except SyntaxError as e:
    print('SyntaxError:', e.msg)
    lines = code.split('\n')
    for i in range(max(0, e.lineno-3), min(len(lines), e.lineno+2)):
        print(f'  {i+1}: {repr(lines[i])}')
    # Find ALL strings in the file
    print()
    print('Scanning all lines for unclosed strings...')
    in_str = False
    str_char = None
    for li, line in enumerate(code.split('\n'), 1):
        stripped = line.lstrip()
        leading = len(line) - len(stripped)
        if not in_str:
            # Check if this line starts a string
            for j, c in enumerate(line):
                if c in ('"', "'") and (j == 0 or line[j-1] != '\\'):
                    in_str = True
                    str_char = c
                    break
        else:
            # Check if this line closes the string
            count = line.count(str_char) - line.count('\\' + str_char)
            if count % 2 == 1:
                print(f'  Line {li} CLOSES string: {repr(line[:60])}')
                in_str = False
            else:
                print(f'  Line {li} still in string: {repr(line[:60])}')
