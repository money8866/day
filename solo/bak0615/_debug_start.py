# -*- coding: utf-8 -*-
import subprocess, sys, os, time

os.chdir(r'd:\mystock\solo')

env = os.environ.copy()
env['PYTHONIOENCODING'] = 'utf-8'
env['TUSHARE_TOKEN'] = ''

# 直接运行脚本，捕获所有输出
proc = subprocess.Popen(
    [sys.executable, '-u', 'realtime_theme_monitor.py'],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    stdin=subprocess.DEVNULL,
    env=env, cwd=r'd:\mystock\solo',
    bufsize=0, universal_newlines=True
)

start_time = time.time()
while time.time() - start_time < 120:  # 最多等2分钟
    line = proc.stdout.readline()
    if not line:
        if proc.poll() is not None:
            break
        time.sleep(0.5)
        continue
    print(line, end='', flush=True)
    # 如果看到等待开盘，说明启动成功
    if '等待开盘' in line or '开始监控' in line:
        break
    # 如果看到检测到旧进程，等待它处理
    if '检测到' in line and '旧的监控进程' in line:
        time.sleep(5)

print(f"\nProcess exited with code: {proc.poll()}")
