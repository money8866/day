
' 股票监控系统 - 任务计划启动脚本（隐藏窗口）
' 此脚本用于任务计划程序，隐藏窗口但记录日志

Set WshShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")

scriptDir = "C:\Users\kongx\mystock\dayreal"
logDir = "C:\Users\kongx\mystock\solo\logs"
mainPy = scriptDir &amp; "\main.py"

' 创建日志目录
If Not objFSO.FolderExists(logDir) Then
    objFSO.CreateFolder logDir
End If

' 生成日志文件名
dt = Now()
logFile = logDir &amp; "\stock_monitor_" &amp; _
    Year(dt) &amp; Right("0" &amp; Month(dt), 2) &amp; Right("0" &amp; Day(dt), 2) &amp; "_" &amp; _
    Right("0" &amp; Hour(dt), 2) &amp; Right("0" &amp; Minute(dt), 2) &amp; Right("0" &amp; Second(dt), 2) &amp; ".log"

' 构建命令
cmd = "cmd /c cd /d """ &amp; scriptDir &amp; """ &amp;&amp; python """ &amp; mainPy &amp; """ 2&gt;&amp;1 &gt; """ &amp; logFile &amp; """"

' 运行命令（隐藏窗口）
WshShell.Run cmd, 0, False

