openclaw cron add --name "基本面早报" --cron "30 7 * * 1-5" --tz "Asia/Shanghai" --session isolated --agent main --message "你是我的股票量化助手。请执行以下任务：

1. 运行：cd D:\mystock\solo\multi_factor_picker && C:\Users\kongx\AppData\Local\Python\pythoncore-3.14-64\python.exe basic_info_juchao_web.py
2. 运行：cd D:\mystock\solo\multi_factor_picker && C:\Users\kongx\AppData\Local\Python\pythoncore-3.14-64\python.exe generate_daily_report.py
3. PDF文件路径：D:\mystock\solo\multi_factor_picker\output\fundamental_info_auto_daily.pdf
4. 用message工具发送给当前用户，渠道为openclaw-weixin，附件为该PDF文件
5. 消息内容：写一段简短的导语，告知今日利好X条、利空X条，以及最重要的发现。

要求：(1) 不要回复 HEARTBEAT_OK (2) 不要调用 message 工具以外的工具 (3) 只需发送一次 (4) 发送完成后输出 DONE" --announce --channel openclaw-weixin --to "o9cq80_cRjRtyORVacNy4d1um3Nk@im.wechat"
