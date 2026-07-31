$ErrorActionPreference = "SilentlyContinue"
$uri = "https://fuyao.aicubes.cn/mcp"
$h = @{"Content-Type"="application/json"; "Authorization"="Bearer test"}
$b = '{"jsonrpc":"2.0","method":"test","params":{},"id":1}'

Write-Host "=== Testing GET ===" -ForegroundColor Cyan
try {
    $r = Invoke-WebRequest -Uri $uri -Method GET -TimeoutSec 10
    Write-Host "GET Status:" $r.StatusCode
} catch {
    Write-Host "GET Failed:" $_.Exception.Message
}

Write-Host "`n=== Testing POST root ===" -ForegroundColor Cyan
try {
    $r = Invoke-WebRequest -Uri "https://fuyao.aicubes.cn/" -Method POST -Headers $h -Body $b -TimeoutSec 10
    Write-Host "POST / Status:" $r.StatusCode
    $content = $r.Content
    Write-Host $content.Substring(0, [Math]::Min(300, $content.Length))
} catch {
    Write-Host "POST / Failed:" $_.Exception.Message
}

Write-Host "`n=== Testing POST /mcp ===" -ForegroundColor Cyan
try {
    $r = Invoke-WebRequest -Uri "https://fuyao.aicubes.cn/mcp" -Method POST -Headers $h -Body $b -TimeoutSec 10
    Write-Host "POST /mcp Status:" $r.StatusCode
} catch {
    Write-Host "POST /mcp Failed:" $_.Exception.Message
}

Write-Host "`n=== Testing POST /mcp/hithink-finance-a-share ===" -ForegroundColor Cyan
try {
    $r = Invoke-WebRequest -Uri "https://fuyao.aicubes.cn/mcp/hithink-finance-a-share" -Method POST -Headers $h -Body $b -TimeoutSec 10
    Write-Host "POST Status:" $r.StatusCode
} catch {
    Write-Host "POST Failed:" $_.Exception.Message
}
