# -*- coding: utf-8 -*-
import subprocess, sys, os, time

os.chdir(r'd:\mystock\solo')

env = os.environ.copy()
env['PYTHONIOENCODING'] = 'utf-8'
env['TUSHARE_TOKEN'] = ''

# 直接运行脚本，捕获所有输出并实时显示
proc = subprocess.Popen(
    [sys.executable, '-u', 'realtime_theme_monitor.py'],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    stdin=subprocess.DEVNULL,
    env=env, cwd=r'd:\mystock\solo',
    bufsize=0, universal_newlines=True
)

start_time = time.time()
last_activity = start_time

while time.time() - start_time < 180:  # 最多等3分钟
    line = proc.stdout.readline()
    if line:
        print(line, end='', flush=True)
        last_activity = time.time()
        # 如果看到开始监控，开始计时
        if '开始监控' in line:
            monitor_start = time.time()
    else:
        if proc.poll() is not None:
            print(f"\nProcess exited with code: {proc.poll()}")
            break
        # 检查是否长时间无输出
        if time.time() - last_activity > 30:
            print(f"\n⚠️ 超过30秒无输出，可能卡住了")
            # 尝试发送信号终止
            proc.terminate()
            proc.wait()
            break
        time.sleep(0.5)

if proc.poll() is None:
    proc.terminate()
    proc.wait()
    print("\n强制终止")
