import subprocess
import sys
from datetime import datetime
import os

# 创建日志目录
log_dir = r"C:\Users\kongx\mystock\solo\logs"
os.makedirs(log_dir, exist_ok=True)

# 生成日志文件名
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
log_file = os.path.join(log_dir, f"stock_monitor_{timestamp}.log")

print("=" * 70)
print(f"Stock Monitor - Starting at {datetime.now()}")
print(f"Log File: {log_file}")
print("=" * 70)
print()

# 启动程序
python_exe = r"C:\Users\kongx\AppData\Local\Python\bin\python.exe"
main_py = r"C:\Users\kongx\mystock\dayreal\main.py"

try:
    # 打开日志文件
    with open(log_file, 'w', encoding='utf-8') as f:
        f.write(f"Stock Monitor Starting at {datetime.now()}\n")
        f.write("=" * 70 + "\n\n")
        f.flush()
        
        # 启动进程
        process = subprocess.Popen(
            [python_exe, main_py],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=r"C:\Users\kongx\mystock\dayreal",
            bufsize=1,
            universal_newlines=True,
            encoding='utf-8'
        )
        
        # 实时读取输出
        print(f"Program started with PID: {process.pid}")
        print(f"Waiting for output...\n")
        
        for line in process.stdout:
            print(line, end='')
            f.write(line)
            f.flush()
            
        process.wait()
        
    print(f"\n\nProgram ended. Log saved to: {log_file}")
    
except KeyboardInterrupt:
    print("\n\nProgram interrupted by user")
except Exception as e:
    print(f"\n\nError: {e}")
    import traceback
    traceback.print_exc()
