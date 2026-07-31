$ErrorActionPreference = "SilentlyContinue"
$headers = @{
    "Content-Type" = "application/json"
    "X-Authorization" = "Bearer eyJidHkiOiJvaWRjIiwia2lkIjoiNm0yd2o5eGs3Y3F6eHlrMSIsInR5cCI6IkpXVCIsImFsZyI6IkVTMjU2In0.eyJzY3AiOiJvcGVuaWQgYmFzZV91c2VyaW5mbyBtY3A6ZGF0YS5yZWFkIG9mZmxpbmVfYWNjZXNzIiwiaXNzIjoiaHR0cDovLzEwLjIxNy4xNDAuMTAvb2lkYyIsInN1YiI6IlAtNmZYbXRHNHRfME5VRUs5T3d6YlZnOVdNTHZxb0ZSVkdUVTF6OE5hQUc1VDd1aVd2STVLSnJuUlpSYmd1dm9uWFNNM2F0UUJMemRVUHciLCJhdWQiOiJ1cG9jX2tuOXhlemRjdGtfcWNsYXciLCJqdGkiOiI0OWExYzI5My1iNDExLTQ1NmUtOTNiNC1mYjFkNjlhZGVjNjkiLCJpYXQiOjE3ODU0NTE2MDAsImV4cCI6MTc4NTQ1NTIwMH0.gS3W5f6sHme0pdLGCLDgbShrkkFhufztlH0y3J1NhsICgJyWN1JgfR4JSZE5apyrdaOiMt-_1Tz1lczHaa3RJg"
    "X-Consumer-Id" = "qclaw"
    "X-Client-Secret" = "1"
}
$body = '{"jsonrpc":"2.0","method":"get_a_share_prices_snapshot","params":{"codes":"sh000001,sz399001,sz399006,sh000300,sz399852,sh000688"},"id":1}'
$out = "$env:TEMP\ht_idx.json"
try {
    $r = Invoke-WebRequest -Uri "https://fuyao.aicubes.cn/mcp/a-share" -Method POST -Headers $headers -Body $body -TimeoutSec 30
    $r.Content | Out-File -FilePath $out -Encoding UTF8
    Write-Output "OK"
} catch {
    Write-Output "ERR:$($_.Exception.Message)"
}
