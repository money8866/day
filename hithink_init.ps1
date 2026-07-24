# 同花顺MCP初始化脚本
$skill_dir = "C:\Users\kongx\.qclaw\skills\hithink-mcp"

# Step 1: Get token
Write-Host "Step 1: Getting access token..."
$accessToken = & "$skill_dir\get-token.ps1"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Token failed, please auth in QClaw panel"
    exit 1
}
Write-Host "Token: $accessToken"

# Step 2: Refresh config
Write-Host "`nStep 2: Refreshing MCP services..."
$ClientSecret = "1"

mcporter config remove hithink-finance-a-share 2>$null
mcporter config remove hithink-finance-a-share-index 2>$null
mcporter config remove hithink-finance-meta 2>$null
mcporter config remove hithink-finance-fund 2>$null

mcporter config add hithink-finance-a-share --type http --url "https://fuyao.aicubes.cn/mcp/a-share" --header "X-Authorization=$accessToken" --header "X-Consumer-Id=qclaw" --header "X-Client-Secret=$ClientSecret" --description "Hithink A-Share" --enabled true --timeout 30
mcporter config add hithink-finance-a-share-index --type http --url "https://fuyao.aicubes.cn/mcp/a-share-index" --header "X-Authorization=$accessToken" --header "X-Consumer-Id=qclaw" --header "X-Client-Secret=$ClientSecret" --description "Hithink Index" --enabled true --timeout 30
mcporter config add hithink-finance-meta --type http --url "https://fuyao.aicubes.cn/mcp/meta" --header "X-Authorization=$accessToken" --header "X-Consumer-Id=qclaw" --header "X-Client-Secret=$ClientSecret" --description "Hithink Meta" --enabled true --timeout 30
mcporter config add hithink-finance-fund --type http --url "https://fuyao.aicubes.cn/mcp/fund" --header "X-Authorization=$accessToken" --header "X-Consumer-Id=qclaw" --header "X-Client-Secret=$ClientSecret" --description "Hithink Fund" --enabled true --timeout 30

Write-Host "`nStep 3: Verify..."
mcporter config list
