$at = & 'C:\Users\kongx\.qclaw\skills\hithink-mcp\get-token.ps1'

# Update all 4 services with fresh token first
$services = @(
    @{name="hithink-finance-a-share"; url="https://fuyao.aicubes.cn/mcp/a-share"; desc="同花顺A股数据"},
    @{name="hithink-finance-a-share-index"; url="https://fuyao.aicubes.cn/mcp/a-share-index"; desc="同花顺指数数据"},
    @{name="hithink-finance-meta"; url="https://fuyao.aicubes.cn/mcp/meta"; desc="同花顺元信息"},
    @{name="hithink-finance-fund"; url="https://fuyao.aicubes.cn/mcp/fund"; desc="同花顺基金数据"}
)

foreach ($svc in $services) {
    $r = mcporter config remove $svc.name 2>$null
    $r = mcporter config add $svc.name --type http --url $svc.url --header "X-Authorization=$at" --header "X-Consumer-Id=qclaw" --header "X-Client-Secret=1" --description $svc.desc --enabled true --timeout 30 2>&1
    Write-Output "Config $($svc.name): $r"
}

# Now test index snapshot
Write-Output "`n=== Testing index snapshot ==="
$body = @{
    jsonrpc = "2.0"
    method = "tools/call"
    params = @{
        name = "get_a_share_index_prices_snapshot"
        arguments = @{
            thscodes = "000001.SH,399001.SZ,399006.SZ,000688.SH,399300.SH"
        }
    }
    id = 1
} | ConvertTo-Json -Compress

try {
    $resp = Invoke-WebRequest -Uri "https://fuyao.aicubes.cn/mcp/a-share-index" `
        -Method POST `
        -Headers @{"X-Authorization"=$at; "X-Consumer-Id"="qclaw"; "X-Client-Secret"="1"} `
        -ContentType "application/json" `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) `
        -TimeoutSec 20
    Write-Output "Status: $($resp.StatusCode)"
    $content = $resp.Content
    if ($content.Length -gt 2000) {
        Write-Output $content.Substring(0, 2000)
    } else {
        Write-Output $content
    }
} catch {
    Write-Output "Error: $($_.Exception.Message)"
}
