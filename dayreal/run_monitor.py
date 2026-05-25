import sys
import os
from datetime import datetime

# 添加项目目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 创建日志目录
log_dir = r"C:\Users\kongx\mystock\solo\logs"
os.makedirs(log_dir, exist_ok=True)

# 生成日志文件名
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
log_file = os.path.join(log_dir, f"stock_monitor_{timestamp}.log")

print("=" * 70)
print("股票盘中预警系统 - 四因子仓位管理版")
print("=" * 70)
print(f"日志文件: {log_file}")
print(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print()

# 重定向输出到日志文件
class Tee(object):
    def __init__(self, *files):
        self.files = files
    def write(self, obj):
        for f in self.files:
            f.write(obj)
            f.flush()
    def flush(self):
        for f in self.files:
            f.flush()

# 同时输出到控制台和日志文件
log_file_handle = open(log_file, 'w', encoding='utf-8')
original_stdout = sys.stdout
original_stderr = sys.stderr
sys.stdout = Tee(sys.stdout, log_file_handle)
sys.stderr = Tee(sys.stderr, log_file_handle)

try:
    # 导入并运行主程序
    import main
    if __name__ == "__main__":
        main.main()
except KeyboardInterrupt:
    print("\n\n程序被用户中断")
except Exception as e:
    print(f"\n\n程序出错: {e}")
    import traceback
    traceback.print_exc()
finally:
    # 恢复原始输出
    sys.stdout = original_stdout
    sys.stderr = original_stderr
    log_file_handle.close()
    print(f"\n日志已保存到: {log_file}")
