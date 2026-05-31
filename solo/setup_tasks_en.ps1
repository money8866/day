[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Stock Monitor - Task Scheduler Setup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$scriptDir = "C:\Users\kongx\mystock\solo"
$startScript = "$scriptDir\start_stock_monitor.bat"
$stopScript = "$scriptDir\stop_stock_monitor_silent.bat"
$taskNameStart = "StockMonitor_Start"
$taskNameStop = "StockMonitor_Stop"

if (-not (Test-Path $startScript)) {
    Write-Host "Error: Start script not found: $startScript" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $stopScript)) {
    Write-Host "Error: Stop script not found: $stopScript" -ForegroundColor Red
    exit 1
}

Write-Host "Removing old tasks (if exist)..." -ForegroundColor Yellow
try {
    Unregister-ScheduledTask -TaskName $taskNameStart -ErrorAction SilentlyContinue -Confirm:$false
    Unregister-ScheduledTask -TaskName $taskNameStop -ErrorAction SilentlyContinue -Confirm:$false
    Write-Host "Old tasks cleaned" -ForegroundColor Green
} catch {
    Write-Host "Cleanup completed" -ForegroundColor Gray
}

Write-Host ""
Write-Host "Creating start task: $taskNameStart" -ForegroundColor Yellow

$triggerStart = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 9am
$actionStart = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/k `"$startScript`"" -WorkingDirectory $scriptDir
$settingsStart = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

try {
    Register-ScheduledTask -TaskName $taskNameStart -Trigger $triggerStart -Action $actionStart -Settings $settingsStart -RunLevel Highest -Force | Out-Null
    Write-Host "Start task created successfully!" -ForegroundColor Green
    Write-Host "  Time: Weekdays at 9:00 AM" -ForegroundColor Gray
    Write-Host "  Display: Window visible (real-time output)" -ForegroundColor Gray
} catch {
    Write-Host "Failed to create start task: $_" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Creating stop task: $taskNameStop" -ForegroundColor Yellow

$triggerStop = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 3pm
$actionStop = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$stopScript`"" -WorkingDirectory $scriptDir
$settingsStop = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

try {
    Register-ScheduledTask -TaskName $taskNameStop -Trigger $triggerStop -Action $actionStop -Settings $settingsStop -RunLevel Highest -Force | Out-Null
    Write-Host "Stop task created successfully!" -ForegroundColor Green
    Write-Host "  Time: Weekdays at 3:00 PM" -ForegroundColor Gray
    Write-Host "  Display: Silent" -ForegroundColor Gray
} catch {
    Write-Host "Failed to create stop task: $_" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Setup completed!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Created tasks:" -ForegroundColor White
Write-Host "  1. $taskNameStart - Weekdays at 9:00 AM (window visible)" -ForegroundColor Gray
Write-Host "  2. $taskNameStop - Weekdays at 3:00 PM (silent)" -ForegroundColor Gray
Write-Host ""
Write-Host "Manage tasks with:" -ForegroundColor White
Write-Host "  View: Get-ScheduledTask -TaskName 'StockMonitor*'" -ForegroundColor Gray
Write-Host "  Delete: Unregister-ScheduledTask -TaskName 'StockMonitor*' -Confirm:`$false" -ForegroundColor Gray
Write-Host ""

