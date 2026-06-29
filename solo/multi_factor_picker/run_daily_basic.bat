@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
cd /d D:\mystock\solo\multi_factor_picker
echo ================================================================
echo 基本面信息自动挖掘 - 每日早报
echo ================================================================
echo [%date% %time%] 开始运行...
echo.

REM 步骤1：读取TOP50股票列表
echo [1/4] 读取TOP50股票列表...
python -c "import pandas as pd; df = pd.read_csv(r'output\top50_tracking_list.csv'); print(f'TOP50股票：{len(df)}只')"
if errorlevel 1 (
    echo [ERROR] 读取TOP50列表失败
    exit /b 1
)

REM 步骤2：挖掘公告（利好+利空）
echo.
echo [2/4] 挖掘公告信息（利好+利空）...
python basic_info_juchao_web.py > output\_daily_miner_log.txt 2>&1
if errorlevel 1 (
    echo [ERROR] 挖掘失败，查看日志：output\_daily_miner_log.txt
    exit /b 1
)

REM 步骤3：生成PDF报告
echo.
echo [3/4] 生成PDF报告...
python generate_daily_report.py >> output\_daily_miner_log.txt 2>&1
if errorlevel 1 (
    echo [ERROR] PDF生成失败，查看日志：output\_daily_miner_log.txt
    exit /b 1
)

REM 步骤4：发送微信
echo.
echo [4/4] 发送微信推送...
python -c "
import sys, os
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

pdf_path = r'D:\mystock\solo\multi_factor_picker\output\fundamental_info_auto_daily.pdf'

if not os.path.exists(pdf_path):
    print('[ERROR] PDF文件不存在')
    sys.exit(1)

print(f'PDF路径: {pdf_path}')
print(f'文件大小: {os.path.getsize(pdf_path)} bytes')

# 读取利好/利空统计
try:
    pos = __import__('pandas').read_csv(r'D:\mystock\solo\multi_factor_picker\output\auto_positive.csv')
    pos_count = len(pos)
    pos_top = pos.iloc[0]['weight'] if pos_count > 0 else 0
except:
    pos_count = 0
    pos_top = 0

try:
    neg = __import__('pandas').read_csv(r'D:\mystock\solo\multi_factor_picker\output\auto_negative.csv')
    neg_count = len(neg)
except:
    neg_count = 0

print(f'利好消息: {pos_count}条')
print(f'利空消息: {neg_count}条')
print(f'利好最高分: +{pos_top}分')
print('PDF已就绪，准备发送...')
"
if errorlevel 1 (
    echo [ERROR] PDF检查失败
    exit /b 1
)

echo.
echo ================================================================
echo [%date% %time%] 运行完成！
echo ================================================================
exit /b 0
