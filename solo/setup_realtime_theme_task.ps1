[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "实时主题盯盘 - 计划任务配置" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$scriptDir = "d:\mystock\solo"
$startScript = "$scriptDir\start_realtime_theme_monitor.bat"
$stopScript = "$scriptDir\stop_realtime_theme_monitor.bat"
$taskNameStart = "RealtimeThemeMonitor_Start"
$taskNameStop = "RealtimeThemeMonitor_Stop"

if (-not (Test-Path $startScript)) {
    Write-Host "错误: 启动脚本不存在: $startScript" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $stopScript)) {
    Write-Host "错误: 停止脚本不存在: $stopScript" -ForegroundColor Red
    exit 1
}

Write-Host "正在删除旧任务（如果存在）..." -ForegroundColor Yellow
try {
    Unregister-ScheduledTask -TaskName $taskNameStart -ErrorAction SilentlyContinue -Confirm:$false
    Unregister-ScheduledTask -TaskName $taskNameStop -ErrorAction SilentlyContinue -Confirm:$false
    Write-Host "旧任务已清理" -ForegroundColor Green
} catch {
    Write-Host "清理旧任务时出错（可能不存在）" -ForegroundColor Gray
}

Write-Host ""
Write-Host "正在创建启动任务: $taskNameStart" -ForegroundColor Yellow

$triggerStart = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "09:25"
$actionStart = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/k `"$startScript`"" -WorkingDirectory $scriptDir
$settingsStart = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

try {
    Register-ScheduledTask -TaskName $taskNameStart -Trigger $triggerStart -Action $actionStart -Settings $settingsStart -RunLevel Highest -Force | Out-Null
    Write-Host "启动任务创建成功！" -ForegroundColor Green
    Write-Host "  时间: 每个工作日 9:25" -ForegroundColor Gray
    Write-Host "  显示: 窗口可见（可查看实时输出）" -ForegroundColor Gray
} catch {
    Write-Host "创建启动任务失败: $_" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "正在创建停止任务: $taskNameStop" -ForegroundColor Yellow

$triggerStop = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "15:01"
$actionStop = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$stopScript`"" -WorkingDirectory $scriptDir
$settingsStop = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

try {
    Register-ScheduledTask -TaskName $taskNameStop -Trigger $triggerStop -Action $actionStop -Settings $settingsStop -RunLevel Highest -Force | Out-Null
    Write-Host "停止任务创建成功！" -ForegroundColor Green
    Write-Host "  时间: 每个工作日 15:01" -ForegroundColor Gray
    Write-Host "  显示: 静默运行" -ForegroundColor Gray
} catch {
    Write-Host "创建停止任务失败: $_" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "任务计划配置完成！" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "已创建的任务:" -ForegroundColor White
Write-Host "  1. $taskNameStart - 每个工作日 9:25 启动（显示窗口）" -ForegroundColor Gray
Write-Host "  2. $taskNameStop - 每个工作日 15:01 停止（静默）" -ForegroundColor Gray
Write-Host ""
Write-Host "管理命令:" -ForegroundColor White
Write-Host "  查看: Get-ScheduledTask -TaskName 'RealtimeThemeMonitor*'" -ForegroundColor Gray
Write-Host "  删除: Unregister-ScheduledTask -TaskName 'RealtimeThemeMonitor*' -Confirm:`$false" -ForegroundColor Gray
Write-Host ""
