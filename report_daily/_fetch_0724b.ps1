# 同花顺MCP 复盘数据批量拉取 - key=value 语法 - 2026-07-24
$ErrorActionPreference = "Continue"
$out = "D:\mystock\report_daily"
$svc = "hithink-finance-a-share"
$env:PYTHONIOENCODING = "utf-8"

Write-Host "1. indices"
& mcporter call $svc.get_a_share_prices_snapshot thscodes="000001.SZ,399001.SZ,399006.SZ,000300.SH,000905.SH,000852.SH" --output json *> "$out\q_indices_0724.json"

Write-Host "2a. limitup p1"
& mcporter call $svc.get_a_share_special_data_limit_up_pool page=1 size=50 --output json *> "$out\q_limitup_0724_1.json"

Write-Host "2b. limitup p2"
& mcporter call $svc.get_a_share_special_data_limit_up_pool page=2 size=50 --output json *> "$out\q_limitup_0724_2.json"

Write-Host "2c. limitup p3"
& mcporter call $svc.get_a_share_special_data_limit_up_pool page=3 size=50 --output json *> "$out\q_limitup_0724_3.json"

Write-Host "3. limitdown"
& mcporter call $svc.get_a_share_special_data_limit_down_pool --output json *> "$out\q_limitdown_0724.json"

Write-Host "4. hot"
& mcporter call $svc.get_a_share_special_data_hot_stock_list period=day --output json *> "$out\q_hot_0724.json"

Write-Host "5. dragon"
& mcporter call $svc.get_a_share_special_data_dragon_tiger_list --output json *> "$out\q_dragon_0724.json"

Write-Host "6. positions"
& mcporter call $svc.get_a_share_prices_snapshot thscodes="159516.SZ,159611.SZ,512480.SH,512760.SH,159865.SZ,515050.SH" --output json *> "$out\q_pos_0724.json"

Write-Host "DONE"
