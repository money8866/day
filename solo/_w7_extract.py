# -*- coding: utf-8 -*-
"""按字节精确提取指定提交版本的文件，避免 PowerShell 管道破坏中文编码。"""
import subprocess

CWD = r"d:\mystock"
REV = "1acb6b6~1"          # 0915 大改的前一个提交
SRC = "solo/w7_second_wave_engine.py"
DST = r"d:\mystock\solo\_w7_old_engine.py"

r = subprocess.run(["git", "show", f"{REV}:{SRC}"], cwd=CWD, capture_output=True)
if r.returncode != 0:
    raise SystemExit("git show 失败: " + r.stderr.decode("utf-8", "ignore"))

data = r.stdout
with open(DST, "wb") as f:
    f.write(data)

# 同时打印该提交的元信息，便于核对
m = subprocess.run(["git", "log", "-1", "--format=%H %ad %s", "--date=format:%Y-%m-%d %H:%M:%S", REV],
                   cwd=CWD, capture_output=True)
print("提取自:", m.stdout.decode("utf-8", "ignore").strip())
print("字节数:", len(data))
print("行数:", data.count(b"\n"))
print("是否含 BOM:", data[:3] == b"\xef\xbb\xbf")
