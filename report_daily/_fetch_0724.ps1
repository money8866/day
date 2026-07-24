# 同花顺MCP 复盘数据批量拉取 - 2026-07-24 收盘
$ErrorActionPreference = "Continue"
$skill = "C:\Users\kongx\.qclaw\skills\hithink-mcp"
$out = "D:\mystock\report_daily"
$token = & "$skill\get-token.ps1"
$secret = "1"

# 刷新MCP配置（token每小时过期）
mcporter config remove hithink-finance-a-share 2>$null
mcporter config remove hithink-finance-a-share-index 2>$null
mcporter config remove hithink-finance-meta 2>$null
mcporter config remove hithink-finance-fund 2>$null
mcporter config add hithink-finance-a-share --type http --url "https://fuyao.aicubes.cn/mcp/a-share" --header "X-Authorization=$token" --header "X-Consumer-Id=qclaw" --header "X-Client-Secret=$secret" --description "Hithink A-Share" --enabled true --timeout 30 2>$null
mcporter config add hithink-finance-a-share-index --type http --url "https://fuyao.aicubes.cn/mcp/a-share-index" --header "X-Authorization=$token" --header "X-Consumer-Id=qclaw" --header "X-Client-Secret=$secret" --description "Hithink Index" --enabled true --timeout 30 2>$null
Write-Host "Token refreshed."

# 1. 六大指数收盘
Write-Host "Fetching indices..."
mcporter call hithink-finance-a-share.get_a_share_prices_snapshot --args '{"thscodes":"000001.SZ,399001.SZ,399006.SZ,000300.SH,000905.SH,000852.SH"}' 2>$null | Out-File -Encoding UTF8 "$out\q_indices_0724.json"

# 2. 涨停池全3页
Write-Host "Fetching limit-up pool (3 pages)..."
for ($p=1; $p -le 3; $p++) {
  mcporter call hithink-finance-a-share.get_a_share_special_data_limit_up_pool --args "{`"page`":$p,`"size`":50}" 2>$null | Out-File -Encoding UTF8 "$out\q_limitup_0724_$p.json"
}

# 3. 跌停池
Write-Host "Fetching limit-down pool..."
mcporter call hithink-finance-a-share.get_a_share_special_data_limit_down_pool --args '{}' 2>$null | Out-File -Encoding UTF8 "$out\q_limitdown_0724.json"

# 4. 热股榜
Write-Host "Fetching hot stocks..."
mcporter call hithink-finance-a-share.get_a_share_special_data_hot_stock_list --args '{"period":"day"}' 2>$null | Out-File -Encoding UTF8 "$out\q_hot_0724.json"

# 5. 龙虎榜
Write-Host "Fetching dragon-tiger..."
mcporter call hithink-finance-a-share.get_a_share_special_data_dragon_tiger_list --args '{}' 2>$null | Out-File -Encoding UTF8 "$out\q_dragon_0724.json"

# 6. 持仓ETF
Write-Host "Fetching positions..."
mcporter call hithink-finance-a-share.get_a_share_prices_snapshot --args '{"thscodes":"159516.SZ,159611.SZ,512480.SH,512760.SH,159865.SZ,515050.SH"}' 2>$null | Out-File -Encoding UTF8 "$out\q_pos_0724.json"

Write-Host "ALL DONE"
