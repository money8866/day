# Stop Stock Monitor Script
$ErrorActionPreference = "SilentlyContinue"

# Create logs directory if it doesn't exist
$logDir = Join-Path $PSScriptRoot "logs"
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}

# Generate log filename with timestamp
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logFile = Join-Path $logDir "stock_monitor_stop_$timestamp.log"

# Write log header
"=" * 50 | Out-File -FilePath $logFile -Append
"Stopping Stock Monitor" | Out-File -FilePath $logFile -Append
"Stop Time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Out-File -FilePath $logFile -Append
"=" * 50 | Out-File -FilePath $logFile -Append
"" | Out-File -FilePath $logFile -Append

$found = $false

# Get all python processes
$pythonProcesses = Get-Process python -ErrorAction SilentlyContinue

if ($pythonProcesses) {
    foreach ($proc in $pythonProcesses) {
        try {
            # Get command line using WMI
            $wmiProc = Get-WmiObject Win32_Process -Filter "ProcessId=$($proc.Id)" -ErrorAction Stop
            if ($wmiProc.CommandLine -like "*main.py*") {
                "Found running main.py process (PID: $($proc.Id))" | Out-File -FilePath $logFile -Append
                Write-Host "Found running main.py process (PID: $($proc.Id))"
                
                # Stop the process
                Stop-Process -Id $proc.Id -Force -ErrorAction Stop
                "Successfully terminated process $($proc.Id)" | Out-File -FilePath $logFile -Append
                Write-Host "Successfully terminated process $($proc.Id)"
                $found = $true
            }
        } catch {
            "Failed to process PID $($proc.Id): $_" | Out-File -FilePath $logFile -Append
        }
    }
}

if (-not $found) {
    "No running main.py process found" | Out-File -FilePath $logFile -Append
    Write-Host "No running main.py process found"
}

"" | Out-File -FilePath $logFile -Append
"Operation completed" | Out-File -FilePath $logFile -Append
"=" * 50 | Out-File -FilePath $logFile -Append

Write-Host "Log saved to: $logFile"
