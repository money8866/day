# 创建实时监控计划任务脚本
# 请以管理员身份运行PowerShell

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   实时监控自动任务设置" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$taskName1 = "实时大盘情绪监控"
$taskName2 = "实时均线监控"
$batPath1 = "d:\mystock\solo\start_emotion_monitor.bat"
$batPath2 = "d:\mystock\solo\start_ma_monitor.bat"
$pythonPath = "C:\Users\kongx\AppData\Local\Python\bin\python.exe"

Write-Host "正在创建任务: $taskName1" -ForegroundColor Yellow

# 删除旧任务（如果存在）
try {
    Unregister-ScheduledTask -TaskName $taskName1 -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "  - 已删除旧任务" -ForegroundColor Gray
} catch {
    Write-Host "  - 无旧任务" -ForegroundColor Gray
}

# 创建大盘情绪监控任务
$action1 = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c start /min """" ""$batPath1"""
$trigger1 = New-ScheduledTaskTrigger -Daily -At "9:25" -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday
$settings1 = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -AllowStartIfOnBatteries

Register-ScheduledTask -TaskName $taskName1 -Action $action1 -Trigger $trigger1 -Settings $settings1 -RunLevel Highest -Force | Out-Null

Write-Host "  ✓ 大盘情绪监控任务创建成功" -ForegroundColor Green
Write-Host ""

Write-Host "正在创建任务: $taskName2" -ForegroundColor Yellow

# 删除旧任务（如果存在）
try {
    Unregister-ScheduledTask -TaskName $taskName2 -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "  - 已删除旧任务" -ForegroundColor Gray
} catch {
    Write-Host "  - 无旧任务" -ForegroundColor Gray
}

# 创建均线监控任务
$action2 = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c start /min """" ""$batPath2"""
$trigger2 = New-ScheduledTaskTrigger -Daily -At "9:25" -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday
$settings2 = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -AllowStartIfOnBatteries

Register-ScheduledTask -TaskName $taskName2 -Action $action2 -Trigger $trigger2 -Settings $settings2 -RunLevel Highest -Force | Out-Null

Write-Host "  ✓ 均线监控任务创建成功" -ForegroundColor Green
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "任务设置完成！" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "查看任务：" -ForegroundColor Yellow
Write-Host "  - 打开任务计划程序" -ForegroundColor Gray
Write-Host "  - 查找任务名称: $taskName1, $taskName2" -ForegroundColor Gray
Write-Host ""
Write-Host "删除任务：" -ForegroundColor Yellow
Write-Host "  - 运行: Unregister-ScheduledTask -TaskName '$taskName1' -Confirm:`$false" -ForegroundColor Gray
Write-Host ""
Write-Host "按任意键退出..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
