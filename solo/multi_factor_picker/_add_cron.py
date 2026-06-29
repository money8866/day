# -*- coding: utf-8 -*-
"""添加每日基本面早报定时任务"""
import subprocess
import sys

message = """你是我的股票量化助手。请依次完成以下任务：

第一步：运行抓取脚本。执行命令（用subprocess.run，不要用shell）：
import subprocess
result = subprocess.run(
    [r'C:\Users\kongx\AppData\Local\Python\pythoncore-3.14-64\python.exe',
     r'D:\mystock\solo\multi_factor_picker\basic_info_juchao_web.py'],
    capture_output=True, text=True
)
print(result.stdout)
print(result.stderr)

第二步：运行PDF生成脚本。执行命令（用subprocess.run，不要用shell）：
result = subprocess.run(
    [r'C:\Users\kongx\AppData\Local\Python\pythoncore-3.14-64\python.exe',
     r'D:\mystock\solo\multi_factor_picker\generate_daily_report.py'],
    capture_output=True, text=True
)
print(result.stdout)

第三步：发送微信。PDF文件路径是：D:\mystock\solo\multi_factor_picker\output\fundamental_info_auto_daily.pdf
用message工具（action=send）发送附件，渠道为openclaw-weixin，目标为o9cq80_cRjRtyORVacNy4d1um3Nk@im.wechat，附件路径为该PDF文件，并在message参数中写一段简短的导语，告知今日利好X条、利空X条，以及最重要的发现是什么。

完成后输出DONE。

要求：(1) 不要回复HEARTBEAT_OK (2) 只需要调用message工具发送一次PDF，不要重复发送 (3) 发送完成后直接输出DONE"""

cmd = [
    'openclaw', 'cron', 'add',
    '--name', '\u57fa\u672c\u9762\u65e9\u62a5',
    '--cron', '30 7 * * 1-5',
    '--tz', 'Asia/Shanghai',
    '--session', 'isolated',
    '--agent', 'main',
    '--message', message,
    '--announce',
    '--channel', 'openclaw-weixin',
    '--to', 'o9cq80_cRjRtyORVacNy4d1um3Nk@im.wechat',
]

print('Running command:')
print(' '.join(cmd))
print()
print('Message length:', len(message))

result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
print('STDOUT:', result.stdout)
print('STDERR:', result.stderr)
print('Return code:', result.returncode)
