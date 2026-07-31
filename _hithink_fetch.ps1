param(
    [string]$Tool,
    [string]$Args
)

$headers = @{
    "Content-Type" = "application/json"
    "Authorization" = "Bearer eyJidHkiOiJvaWRjIiwia2lkIjoiNm0yd2o5eGs3Y3F6eHlrMSIsInR5cCI6IkpXVCIsImFsZyI6IkVTMjU2In0.eyJzY3AiOiJvcGVuaWQgYmFzZV91c2VyaW5mbyBtY3A6ZGF0YS5yZWFkIG9mZmxpbmVfYWNjZXNzIiwiaXNzIjoiaHR0cDovLzEwLjIxNy4xNDAuMTAvb2lkYyIsInN1YiI6IlAtNmZYbXRHNHRfME5VRUs5T3d6YlZnOVdNTHZxb0ZSVkdUVTF6OE5hQUc1VDd1aVd2STVLSnJuUlpSYmd1dm9uWFNNM2F0UUJMemRVUHciLCJhdWQiOiJ1cG9jX2tuOXhlemRjdGtfcWNsYXciLCJqdGkiOiI0OWExYzI5My1iNDExLTQ1NmUtOTNiNC1mYjFkNjlhZGVjNjkiLCJpYXQiOjE3ODU0NTE2MDAsImV4cCI6MTc4NTQ1NTIwMH0.gS3W5f6sHme0pdLGCLDgbShrkkFhufztlH0y3J1NhsICgJyWN1JgfR4JSZE5apyrdaOiMt-_1Tz1lczHaa3RJg"
}

$body = @{
    jsonrpc = "2.0"
    method = "hithink-finance-a-share.$Tool"
    params = $Args
    id = [int](Get-Date -UFormat %s * 1000)
} | ConvertTo-Json -Compress

$outFile = "$env:TEMP\hithink_result_$PID.json"
$errFile = "$env:TEMP\hithink_err_$PID.txt"

$proc = Start-Process -FilePath "powershell" -ArgumentList "-NoProfile", "-Command", "
`$headers = @{
    'Content-Type' = 'application/json'
    'Authorization' = 'Bearer eyJidHkiOiJvaWRjIiwia2lkIjoiNm0yd2o5eGs3Y3F6eHlrMSIsInR5cCI6IkpXVCIsImFsZyI6IkVTMjU2In0.eyJzY3AiOiJvcGVuaWQgYmFzZV91c2VyaW5mbyBtY3A6ZGF0YS5yZWFkIG9mZmxpbmVfYWNjZXNzIiwiaXNzIjoiaHR0cDovLzEwLjIxNy4xNDAuMTAvb2lkYyIsInN1YiI6IlAtNmZYbXRHNHRfME5VRUs5T3d6YlZnOVdNTHZxb0ZSVkdUVTF6OE5hQUc1VDd1aVd2STVLSnJuUlpSYmd1dm9uWFNNM2F0UUJMemRVUHciLCJhdWQiOiJ1cG9jX2tuOXhlemRjdGtfcWNsYXciLCJqdGkiOiI0OWExYzI5My1iNDExLTQ1NmUtOTNiNC1mYjFkNjlhZGVjNjkiLCJpYXQiOjE3ODU0NTE2MDAsImV4cCI6MTc4NTQ1NTIwMH0.gS3W5f6sHme0pdLGCLDgbShrkkFhufztlH0y3J1NhsICgJyWN1JgfR4JSZE5apyrdaOiMt-_1Tz1lczHaa3RJg'
}
`$body = '$body'
try {
    `$r = Invoke-WebRequest -Uri 'https://fuyao.aicubes.cn/mcp/hithink-finance-a-share' -Method POST -Headers `$headers -Body `$body -TimeoutSec 30
    `$r.Content | Out-File -FilePath '$outFile' -Encoding UTF8
} catch {
    ('ERROR:' + `$_.Exception.Message) | Out-File -FilePath '$errFile' -Encoding UTF8
}
" -NoNewWindow -PassThru -Wait

if (Test-Path $outFile) {
    Get-Content $outFile -Raw
} elseif (Test-Path $errFile) {
    Get-Content $errFile -Raw
} else {
    Write-Host "No output file"
}
