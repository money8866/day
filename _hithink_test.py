# -*- coding: utf-8 -*-
import os, json, sys
from pathlib import Path

# 初始化
skill_dir = r'C:\Users\kongx\.qclaw\skills\hithink-mcp'
ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'hithink_init.ps1')
with open(ps1, 'w', encoding='utf-8') as f:
    f.write(f'bash "{skill_dir}/get-token.sh"\n')

# 用mcporter调用
import subprocess
result = subprocess.run(
    ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ps1],
    capture_output=True, text=True, encoding='utf-8'
)
print(result.stdout)
if result.stderr:
    print('STDERR:', result.stderr)
print('RC:', result.returncode)
