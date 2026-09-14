$at = & 'C:\Users\kongx\.qclaw\skills\hithink-mcp\get-token.ps1'
Write-Output "Token prefix: $($at.Substring(0, [Math]::Min(20, $at.Length)))"

# Test a call with the current token
$headers = @(
    "X-Authorization=$at"
    "X-Consumer-Id=qclaw"
    "X-Client-Secret=1"
)
$body = @{
    jsonrpc = "2.0"
    method = "tools/call"
    params = @{
        name = "get_a_share_index_prices_snapshot"
        arguments = @{
            thscodes = "000001.SH,399001.SZ,399006.SZ,000688.SH"
        }
    }
    id = 1
} | ConvertTo-Json -Depth 5

try {
    $resp = Invoke-WebRequest -Uri "https://fuyao.aicubes.cn/mcp/a-share" `
        -Method POST `
        -Headers @{"X-Authorization"=$at; "X-Consumer-Id"="qclaw"; "X-Client-Secret"="1"} `
        -ContentType "application/json" `
        -Body $body `
        -TimeoutSec 20
    Write-Output "Status: $($resp.StatusCode)"
    Write-Output $resp.Content.Substring(0, [Math]::Min(500, $resp.Content.Length))
} catch {
    Write-Output "Error: $($_.Exception.Message)"
    Write-Output "StatusCode: $($_.Exception.Response.StatusCode.value__)"
}
