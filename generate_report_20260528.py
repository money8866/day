#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成 2026-05-28 的完整 HTML 报告
修复原脚本的 emoji 编码问题
"""

import sys
import io
import os

# 修复 Windows PowerShell 编码问题
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 设置环境变量
os.environ['PYTHONIOENCODING'] = 'utf-8'

# 导入主脚本模块
print("导入 tushare_quant 模块...")
import tushare_quant as tq

# 设置交易日期
tq.TRADE_DATE = '20260528'

print(f"交易日期: {tq.TRADE_DATE}")
print("开始生成报告...")

# 直接调用报告生成函数（跳过数据采集，使用已缓存的数据）
try:
    # 重新运行完整流程（数据已缓存，不会重复调用 API）
    tq.run()
    print("\n✅ 报告生成成功！")
    
    # 检查生成的文件
    html_file = os.path.join(tq.REPORT_DIR, f"Final_Self_20260528.html")
    if os.path.exists(html_file):
        file_size = os.path.getsize(html_file)
        print(f"HTML 报告: {html_file}")
        print(f"文件大小: {file_size:,} 字节")
    else:
        print("⚠️ HTML 报告未生成")
        
except Exception as e:
    print(f"❌ 错误: {e}")
    import traceback
    traceback.print_exc()
    
finally:
    print("\n完成！")
