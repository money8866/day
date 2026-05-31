import sys
import os
import io
from datetime import datetime

# 设置输出编码
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# 创建日志目录
log_dir = r"C:\Users\kongx\mystock\solo\logs"
os.makedirs(log_dir, exist_ok=True)

# 生成日志文件名
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
log_file = os.path.join(log_dir, f"stock_monitor_{timestamp}.log")

print("=" * 70)
print("Stock Monitor - Starting...")
print(f"Start Time: {datetime.now()}")
print(f"Log File: {log_file}")
print("=" * 70)
print()

# 打开日志文件
with open(log_file, 'w', encoding='utf-8') as f:
    f.write(f"Stock Monitor Starting at {datetime.now()}\n")
    f.write("=" * 70 + "\n")
    f.flush()
    
    # 运行主程序
    try:
        sys.path.insert(0, r"C:\Users\kongx\mystock\dayreal")
        os.chdir(r"C:\Users\kongx\mystock\dayreal")
        
        import main
        main.main()
        
    except KeyboardInterrupt:
        print("\n\nProgram interrupted by user")
        f.write("\nProgram interrupted by user\n")
    except Exception as e:
        print(f"\n\nError: {e}")
        import traceback
        traceback.print_exc(file=f)
        f.write(f"\nError: {e}\n")
        traceback.print_exc()
    finally:
        f.write(f"\nProgram ended at {datetime.now()}\n")
        f.flush()
        
print(f"\nLog saved to: {log_file}")
