# Read Windows Credential Manager for lark-cli tokens
$creds = cmdkey /list 2>&1
Write-Output "=== All stored credentials ==="
Write-Output $creds

# Try lark-cli specific targets
Write-Output ""
Write-Output "=== Trying lark-cli targets ==="
$lark_targets = cmdkey /list:lark 2>&1
Write-Output $lark_targets
