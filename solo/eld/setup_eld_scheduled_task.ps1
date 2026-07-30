# ============================================================
#   ELD V2 每日 17:00 自动任务 - Windows 任务计划程序安装脚本
#   请以 管理员身份 运行 PowerShell 执行
# ============================================================

$ErrorActionPreference = "Continue"

$TASK_NAME = "ELD_V2_Daily_1700"
$BAT_PATH = "D:\mystock\solo\eld\run_eld_daily.bat"
$PYTHON_PATHS = @(
    "C:\Users\kongx\AppData\Local\Python\bin\python.exe",
    "C:\Users\kongx\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\vm\tools\python\python.exe"
)

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   ELD V2 定时任务安装" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# --- 检查 BAT 文件 ---
if (-not (Test-Path $BAT_PATH)) {
    Write-Host "[错误] BAT 脚本不存在: $BAT_PATH" -ForegroundColor Red
    Write-Host "请先在 eld 目录下创建 run_eld_daily.bat" -ForegroundColor Yellow
    exit 1
}
Write-Host "[OK] 启动脚本: $BAT_PATH" -ForegroundColor Green

# --- 找到 python 路径 ---
$pythonExe = ""
foreach ($p in $PYTHON_PATHS) {
    if (Test-Path $p) {
        $pythonExe = $p
        break
    }
}
if (-not $pythonExe) {
    $pythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $pythonExe) {
    Write-Host "[错误] 找不到 python.exe" -ForegroundColor Red
    exit 1
}
Write-Host "[OK] Python 路径: $pythonExe" -ForegroundColor Green

# --- 检查工作目录 ---
$workDir = "D:\mystock\solo"
if (-not (Test-Path $workDir)) {
    Write-Host "[错误] 工作目录不存在: $workDir" -ForegroundColor Red
    exit 1
}
Write-Host "[OK] 工作目录: $workDir" -ForegroundColor Green
Write-Host ""

# --- 删除旧任务 ---
$existing = Get-ScheduledTask -TaskName $TASK_NAME -ErrorAction SilentlyContinue
if ($existing) {
    try {
        Unregister-ScheduledTask -TaskName $TASK_NAME -Confirm:$false -ErrorAction Stop
        Write-Host "[清理] 已删除旧任务 $TASK_NAME" -ForegroundColor Gray
    } catch {
        Write-Host "[警告] 删除旧任务失败: $_" -ForegroundColor Yellow
    }
}

# --- 创建任务 ---
Write-Host "[注册] 创建任务 $TASK_NAME ..." -ForegroundColor Yellow

$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"`"$BAT_PATH`"`"" `
    -WorkingDirectory $workDir

# 交易日 = 周一 ~ 周五 17:00；非交易日时 Python 脚本内部会自动 skip
$trigger = New-ScheduledTaskTrigger `
    -Weekly `
    -At "17:00" `
    -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 8)

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName $TASK_NAME `
    -Description "ELD V2 中报预增股池每日 17:00 自动运行（周一~周五），非交易日自动跳过。含评分、报告生成、微信推送。" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

# --- 验证 ---
$task = Get-ScheduledTask -TaskName $TASK_NAME -ErrorAction SilentlyContinue
if ($task) {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "   任务创建成功！" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "任务名称  : $TASK_NAME" -ForegroundColor Cyan
    Write-Host "运行时间  : 周一 ~ 周五 17:00" -ForegroundColor Cyan
    Write-Host "启动脚本  : $BAT_PATH" -ForegroundColor Cyan
    Write-Host "Python    : $pythonExe" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "下次运行:"
    Get-ScheduledTaskInfo -TaskName $TASK_NAME | Select-Object NextRunTime | Format-List
    Write-Host ""
    Write-Host "常用命令：" -ForegroundColor Yellow
    Write-Host "  立即运行一次  : Start-ScheduledTask -TaskName '$TASK_NAME'" -ForegroundColor Gray
    Write-Host "  查看任务状态  : Get-ScheduledTask -TaskName '$TASK_NAME'" -ForegroundColor Gray
    Write-Host "  查看任务结果  : Get-ScheduledTaskInfo -TaskName '$TASK_NAME'" -ForegroundColor Gray
    Write-Host "  删除任务      : Unregister-ScheduledTask -TaskName '$TASK_NAME' -Confirm:`$false" -ForegroundColor Gray
    Write-Host ""
} else {
    Write-Host "[错误] 任务创建失败" -ForegroundColor Red
    exit 1
}
