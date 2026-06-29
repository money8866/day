@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8

echo Creating cron job for basic info daily report...

openclaw cron add --name "基本面早报" --cron "30 7 * * 1-5" --tz "Asia/Shanghai" --session isolated --agent main --message "你是我的股票量化助手。请依次完成以下任务：

第一步：运行抓取脚本。执行命令（用cmd /c）：
cmd /c "cd /d D:\mystock\solo\multi_factor_picker && C:\Users\kongx\AppData\Local\Python\pythoncore-3.14-64\python.exe basic_info_juchao_web.py"

第二步：运行PDF生成脚本。执行命令（用cmd /c）：
cmd /c "cd /d D:\mystock\solo\multi_factor_picker && C:\Users\kongx\AppData\Local\Python\pythoncore-3.14-64\python.exe generate_daily_report.py"

第三步：发送微信。PDF文件路径是：D:\mystock\solo\multi_factor_picker\output\fundamental_info_auto_daily.pdf

用message工具（action=send）发送附件，渠道为openclaw-weixin，目标为o9cq80_cRjRtyORVacNy4d1um3Nk@im.wechat，附件路径为该PDF文件，并在message参数中写一段简短的导语，告知今日利好X条、利空X条，以及最重要的发现是什么。

完成后输出DONE。

要求：(1) 不要回复HEARTBEAT_OK (2) 只需要调用message工具发送一次PDF，不要重复发送 (3) 发送完成后直接输出DONE" --announce --channel openclaw-weixin --to "o9cq80_cRjRtyORVacNy4d1um3Nk@im.wechat"

echo Done.
pause
