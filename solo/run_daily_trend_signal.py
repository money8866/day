# -*- coding: utf-8 -*-
"""
每日趋势精准入场信号自动化脚本
功能：运行检测 → 生成PDF → 推送微信
使用：python run_daily_trend_signal.py
"""
import os, sys, subprocess, glob, time
from datetime import datetime

# === 配置 ===
DETECTION_SCRIPT = r'D:\mystock\solo\trend_entry_precision.py'
PDF_SCRIPT = r'D:\mystock\solo\multi_factor_picker\_gen_entry_precision_pdf_v2.py'
OUTPUT_CSV_DIR = r'D:\mystock\solo\trend_feature_output'
OUTPUT_PDF_DIR = r'D:\mystock\solo\multi_factor_picker\output'
PYTHON_EXE = r'C:\Users\kongx\AppData\Local\Python\bin\python.exe'
WECHAT_TARGET = 'o9cq80_cRjRtyORVacNy4d1um3Nk@im.wechat'

def run_detection():
    """运行趋势精准入场检测"""
    print(f'[{datetime.now():%H:%M:%S}] 开始运行趋势检测...')
    cmd = [
        PYTHON_EXE,
        DETECTION_SCRIPT,
        '--pool', 'qualified',
        '--today',               # 只检测今日信号
        '--filter-return1d',     # 过滤：信号日收涨
        '--filter-rsi', '50,70', # 过滤：RSI6在[50,70]
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
    if result.returncode != 0:
        print(f'[{datetime.now():%H:%M:%S}] 错误：检测失败')
        print(result.stderr)
        return False
    print(f'[{datetime.now():%H:%M:%S}] 检测完成')
    return True

def find_latest_csv():
    """找到最新的entry_precision CSV"""
    pattern = os.path.join(OUTPUT_CSV_DIR, 'entry_precision_*_qualified.csv')
    files = glob.glob(pattern)
    if not files:
        return None
    return max(files, key=os.path.getmtime)

def update_pdf_script_csv_path(csv_path):
    """修改PDF生成脚本的CSV路径（临时）"""
    with open(PDF_SCRIPT, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 替换csv_path行
    import re
    new_content = re.sub(
        r"csv_path = r'.*?'",
        f"csv_path = r'{csv_path}'",
        content
    )
    
    # 写入临时脚本
    temp_script = PDF_SCRIPT.replace('.py', '_temp.py')
    with open(temp_script, 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    return temp_script

def generate_pdf(csv_path):
    """生成PDF报告"""
    print(f'[{datetime.now():%H:%M:%S}] 开始生成PDF...')
    
    # 修改PDF脚本的CSV路径
    temp_script = update_pdf_script_csv_path(csv_path)
    
    cmd = [PYTHON_EXE, temp_script]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
    
    # 删除临时脚本
    if os.path.exists(temp_script):
        os.remove(temp_script)
    
    if result.returncode != 0:
        print(f'[{datetime.now():%H:%M:%S}] 错误：PDF生成失败')
        print(result.stderr)
        return None
    
    # 找到最新生成的PDF
    pattern = os.path.join(OUTPUT_PDF_DIR, 'entry_precision_report_*.pdf')
    pdf_files = glob.glob(pattern)
    if not pdf_files:
        return None
    latest_pdf = max(pdf_files, key=os.path.getmtime)
    print(f'[{datetime.now():%H:%M:%S}] PDF已生成：{latest_pdf}')
    return latest_pdf

def send_pdf_via_openclaw_api(pdf_path):
    """
    通过OpenClaw Gateway API推送PDF到微信
    注意：这个函数需要在有OpenClaw Gateway运行的环境中执行
    """
    print(f'[{datetime.now():%H:%M:%S}] 推送PDF到微信...')
    
    # 方案：调用openclaw CLI（如果可用）
    # 或者使用requests调用REST API
    # 这里先打印路径，让cron任务的agent用message工具发送
    print(f'PDF路径：{pdf_path}')
    print('请使用OpenClaw message工具发送此PDF')
    return True

def main():
    print('=' * 60)
    print(f'每日趋势精准入场信号自动化 - {datetime.now():%Y-%m-%d %H:%M}')
    print('=' * 60)
    
    # 步骤1：运行检测
    if not run_detection():
        print('脚本终止')
        return 1
    
    # 步骤2：找到最新CSV
    csv_path = find_latest_csv()
    if not csv_path:
        print('错误：未找到CSV文件')
        return 1
    print(f'最新CSV：{csv_path}')
    
    # 步骤3：生成PDF
    pdf_path = generate_pdf(csv_path)
    if not pdf_path:
        print('错误：PDF生成失败')
        return 1
    
    # 步骤4：推送微信（输出路径供agent读取）
    print('=' * 60)
    print('SUCCESS')
    print(f'PDF_PATH={pdf_path}')
    print('=' * 60)
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
